# Milestone 5 Analysis: Extensible Custom Pool Configuration Gaps & Recommendations

This report examines the extensibility of custom pool configuration in the `base_onchain` exchange connector and outlines the technical gaps between the current implementation and production-grade requirements.

---

## 1. Core Findings Summary

1. **Broken IPC Dictionary Sync**: `IPCDict` only loads the IPC JSON file once. It compares path strings rather than file modification times, so updates from other processes are completely ignored.
2. **Missing Input Validation**: Incoming custom pool parameters (`type`, `decimals`, `address`, `fee`, `stable`) are registered without verification. Invalid inputs cause late-stage crashes in the polling loop or when executing RPC calls.
3. **Flawed `list_instruments` Tick Size**: The tick size calculation ignores whether a pool is flipped. For flipped pools (where `token0` is the quote token), it erroneously uses `decimals1` (the base token's decimals) instead of `decimals0`.
4. **Lack of Concurrency and Inter-Process Locking**: Writing to `.custom_pools_ipc.json` is not process-safe (lacks file locks) and not thread-safe within a running event loop (runs in the default multi-worker executor), leading to race conditions.

---

## 2. Detailed Gap Analysis

### Gap 2.1: Registering both Uniswap V3 & Aerodrome V2 Custom Pools
The registry supports both factory types via `_register_custom_pools` but does so in an extremely fragile manner:
- **Type Checking**: There is no type validation. If a user inputs an unrecognized type (e.g. `"curve"`), it falls through the `if` check to the `else` block and is treated as `"aerodrome_v2"`, causing contract calls to revert at runtime.
- **Factory Resolution Gaps**: If the pool contract `address` is not specified, it relies on factory calls:
  - Uniswap V3 calls `getPool(token0, token1, fee)`. If the configuration lacks `"fee"`, it raises `KeyError` on `spec["fee"]` at runtime.
  - Aerodrome V2 calls `getPool(token0, token1, stable)`. If the configuration lacks `"stable"`, it raises `KeyError` on `spec["stable"]` at runtime.

### Gap 2.2: IPC Persistence Gaps (`.custom_pools_ipc.json`)
The persistence mechanism is intended to support dynamic custom pools across processes but has several architectural issues:
- **No Sync Updates**: The sync check in `IPCDict._sync` is defined as:
  ```python
  current_file = _get_ipc_file()
  if current_file != self._last_ipc_file:
      # Sync logic...
      self._last_ipc_file = current_file
  ```
  Since `_get_ipc_file()` always returns the same string path, `current_file != self._last_ipc_file` evaluates to `False` on all subsequent accesses. The dictionary is never reloaded, meaning updates written by other processes are completely invisible.
- **Single-Process Concurrency Race**: `_write_ipc` submits tasks using `asyncio.to_thread` when an event loop is running. This runs in the default thread pool containing multiple workers. Sequential updates will run concurrently in different threads, leading to race conditions.
- **Inter-Process Race Conditions**: There is no lock (e.g. `fcntl.flock` or a lockfile) protecting `.custom_pools_ipc.json`. If two processes write simultaneously, they will read the same file content, perform non-atomic updates, and overwrite each other's changes.

### Gap 2.3: Validation Gaps on Custom Pool Parameters
No validation is performed when custom pools are registered. The following parameters lack safety checks:
- **EVM Addresses**: `token0_address`, `token1_address`, and `address` are converted to string but not checked for EVM format or checksummed. Invalid inputs trigger exceptions in `to_checksum_address` inside the background loop instead of being rejected on registration.
- **Decimals**: `decimals0` and `decimals1` default to 18 but are not validated. Non-integer or negative inputs cause arithmetic errors or `ValueError` during polling.
- **Fee and Stable Flags**: Not validated as integers or booleans, respectively, before registration.

### Gap 2.4: Instrument Listing (`list_instruments`)
- **Flipped Pools**: A pool is flipped if `address(token1) < address(token0)`. In this case, `token1` is the base asset and `token0` is the quote asset. The price represents quote per base (units of `token0` per `token1`). The tick size must be derived from the quote asset's decimals (`decimals0`).
  `list_instruments` always uses `decimals1`:
  ```python
  tick_size = custom_ticks.get(sym, 10 ** (-int(spec.get("decimals1", 6))))
  ```
  This returns incorrect tick sizes for all flipped custom pools.
- **Dynamic Listing Isolation**: `list_instruments` only checks `self.symbols`. If a pool is dynamically added to `POOL_SPECS` via IPC, the running connector will not list it because it does not update `self.symbols`.

---

## 3. Recommended Implementation Strategy

### Step 1: Make `IPCDict` Safe and Robust
1. **Modification Tracking**: Track the file modification time (`os.path.getmtime(current_file)`) inside `IPCDict._sync`. Only reload when the modification time changes:
   ```python
   mtime = os.path.getmtime(current_file) if os.path.exists(current_file) else 0
   if current_file != self._last_ipc_file or mtime != self._last_mtime:
       # ... reload logic ...
       self._last_mtime = mtime
   ```
2. **File Locking**: Use a file lock (such as `fcntl.flock` on POSIX systems or a file lock library) inside `_write_ipc_to_file` and `_sync` to ensure serialized access across processes.
3. **Thread Safety**: Ensure all writes within the same process are serialized through a dedicated single-threaded executor or a task queue.

### Step 2: Introduce Parameter Validation and Pre-computation
Add validation logic inside `_register_custom_pools`:
1. **Type Check**: Validate that `type` is in `{"uniswap_v3", "aerodrome_v2"}`.
2. **EVM Address Verification**: Checksum and validate `token0_address`, `token1_address`, and `address` (if provided) using `AsyncWeb3.to_checksum_address`. Raise a clear `ValueError` early if they are invalid.
3. **Pre-calculate Flipped Flag**: Since token addresses are resolved at registration, pre-calculate `is_flipped = int(t1_addr, 16) < int(t0_addr, 16)` and store it directly in the pool's specification dictionary inside `POOL_SPECS`. This avoids duplicate address comparison and makes the flag globally accessible to `list_instruments`.
4. **Required Parameters**:
   - For Uniswap V3: Ensure `fee` is present and is a positive integer (e.g. 100, 500, 3000, 10000) if no `address` is specified.
   - For Aerodrome V2: Ensure `stable` is present and is a boolean if no `address` is specified.

### Step 3: Correct `list_instruments`
Update `list_instruments` to look up the pre-calculated `is_flipped` flag from `spec` or calculate it on the fly from the token addresses in `TOKENS`.
Calculate the tick size based on the active quote asset's decimals:
```python
is_flipped = spec.get("is_flipped", False)
decimals = spec["decimals0"] if is_flipped else spec["decimals1"]
tick_size = custom_ticks.get(sym, 10 ** (-int(decimals)))
```
