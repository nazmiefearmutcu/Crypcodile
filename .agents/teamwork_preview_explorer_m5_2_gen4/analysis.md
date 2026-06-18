# Analysis of Milestone 5: Extensible Custom Pool Configuration Gaps & Recommendations

This report evaluates the requirements and current implementation gaps of **Milestone 5 (Extensible custom pool configuration)** in `src/crypcodile/exchanges/base_onchain/connector.py` and `tests/exchanges/base_onchain/test_connector.py`.

---

## 1. Executive Summary
The `base_onchain` connector implements dynamic custom pool registration via an IPC-synchronized dictionary (`IPCDict`) backed by a JSON file (`.custom_pools_ipc.json`). However, the implementation is not yet production-ready. The primary critical gaps identified are:
- **Flawed IPC Synchronization**: The synchronization cache is never updated after initial startup because of a stale file path check.
- **Race Conditions**: There is no cross-process file locking (e.g. via `fcntl.flock`), and multi-threaded file writes can run concurrently, causing file corruption or lost updates.
- **Lack of Parameter Validation**: Malformed custom pool configurations (invalid addresses, missing Uniswap fee, missing Aerodrome stable flag, invalid decimals, unsupported pool types) are registered without check, leading to runtime crashes in polling tasks.
- **Incomplete Test Coverage**: Existing test coverage only checks local Uniswap V3 custom pool registration and list instruments, completely skipping Aerodrome V2 custom pools, validation checks, and concurrency/IPC validation.

---

## 2. Deep Dive: Gaps and Findings

### A. Support for Registering Uniswap V3 vs. Aerodrome V2 Custom Pools
The connector supports registering both types of custom pools, but has some structural gaps:
1. **Fallback Default to Aerodrome**: During address resolution, if the `spec["type"]` is not `"uniswap_v3"`, the code defaults to the `else` block (Aerodrome V2):
   ```python
   # connector.py lines 491-499
   else: # aerodrome_v2
       factory = w3.eth.contract(
           address=AsyncWeb3.to_checksum_address(FACTORIES["aerodrome"]),
           abi=factory_aero_abi
       )
       stable = bool(spec["stable"])
       pool_addr = await self._call_with_retry(
           factory.functions.getPool(t0_addr, t1_addr, stable).call
       )
   ```
   If a user specifies a type other than `uniswap_v3` (e.g. an invalid string or unsupported DEX), the connector treats it as `aerodrome_v2` and attempts to initialize it using the Aerodrome factory. This causes a crash at contract query time or logs errors continuously.
2. **Factory Keying**: The factory map currently holds `"uniswap_v3"` and `"aerodrome"`. If a custom pool defines type `"aerodrome_v2"`, it is resolved correctly in the `else` block using the `"aerodrome"` factory, but there is no explicit check verifying that the type is one of the supported types.

### B. Persistence Safety & Cross-Process IPC Locking
The persistence mechanism (`IPCDict` class and `_write_ipc_to_file` function) is vulnerable to race conditions and synchronization failures:
1. **Lack of File Locking**: 
   When writing to the IPC file, `_write_ipc_to_file` reads, updates, and overwrites the shared `.custom_pools_ipc.json` file. Because no cross-process lock (such as `fcntl.flock` or a lockfile) is acquired, concurrent writes from different processes will read stale states, overwrite each other's changes, or corrupt the file.
2. **Concurrent Temp File Overwriting**:
   The temporary file is defined statically:
   ```python
   tmp_file = ipc_file + ".tmp"
   ```
   If two processes or threads write at the same time, they both write to the *same* `.tmp` file concurrently, leading to intermingled or truncated JSON contents before replacing the main file.
3. **Multi-Threaded Concurrency within the Event Loop**:
   If an event loop is running, `IPCDict._write_ipc()` schedules the write using `loop.create_task(asyncio.to_thread(_write_ipc_to_file, ...))`. This runs the write in the default asyncio thread pool, which has multiple workers. If a script updates the dictionary multiple times in quick succession (e.g. registering multiple pools), multiple threads will write to the same file concurrently, triggering in-process race conditions.
4. **Stale Cache / Sync Defeat**:
   `IPCDict._sync()` only clears and updates the local dictionary if the path of the file changes:
   ```python
   # connector.py lines 72-87
   def _sync(self) -> None:
       current_file = _get_ipc_file()
       if current_file != self._last_ipc_file:
           # ... clears and loads ...
           self._last_ipc_file = current_file
   ```
   Since the filename is constant (e.g. `/Users/nazmi/Crypcodile/.custom_pools_ipc.json`), `current_file != self._last_ipc_file` evaluates to `False` on every access after the first. Therefore, the dictionary NEVER reloads updates written by other processes at runtime.
5. **No-op `_load_ipc_sync`**:
   The polling loop invokes `await asyncio.get_running_loop().run_in_executor(_ipc_executor, _load_ipc_sync)`, but `_load_ipc_sync` is empty:
   ```python
   def _load_ipc_sync() -> None:
       pass
   ```
   Hence, no reloading actually happens in the background.

### C. Validation Checks on Incoming Custom Pool Parameters
There are virtually no validation checks when registering custom pools in `_register_custom_pools()`.
1. **Address Validity**: 
   ```python
   t0_addr = str(cfg.get("token0_address") or cfg.get("token0"))
   ```
   If `token0_address` is missing or invalid, it defaults to the token name (e.g., `"TESTCUSTOM"`). In the polling loop, `AsyncWeb3.to_checksum_address("TESTCUSTOM")` is called, which immediately crashes the pool resolution task.
2. **Uniswap V3 Fee**: 
   ```python
   fee = int(spec["fee"])
   ```
   If `type` is `"uniswap_v3"` and `fee` is missing in the configuration, `spec["fee"]` is not set. The address resolution task crashes with a `KeyError: 'fee'`.
3. **Aerodrome Stable Flag**: 
   ```python
   stable = bool(spec["stable"])
   ```
   If `type` is `"aerodrome_v2"` and `stable` is missing, the address resolution task crashes with a `KeyError: 'stable'`.
4. **Decimals Validation**:
   Decimals are parsed and stored directly as integers or strings:
   ```python
   "decimals0": cfg.get("decimals0", 18)
   ```
   If the config contains non-integer values (e.g., `"decimals0": "abc"`), they are registered as strings and later crash the polling calculation during division `10 ** int(spec["decimals0"])`.
5. **No Early Reject**:
   Malformed pools are saved to the persistent `.custom_pools_ipc.json` file. Once a bad configuration is persisted, it will cause the polling loop to fail on every startup until the file is manually deleted.

### D. Instruments Listing (`list_instruments`)
1. **Excluding Unsynchronized Custom Pools**:
   Because `POOL_SPECS` never synchronizes with `.custom_pools_ipc.json` after the initial call, any custom pools added by another process will be missing from `POOL_SPECS` in the current process. When `list_instruments` is called, it loops over `self.symbols` and does `spec = POOL_SPECS.get(sym)`. For unsynchronized custom pools, it returns `None` and skips listing them.
2. **Decimals Cast Crash**:
   If a custom pool was registered with invalid decimals, `list_instruments` crashes at:
   ```python
   tick_size = custom_ticks.get(sym, 10 ** (-int(spec.get("decimals1", 6))))
   ```
   because `int(spec.get("decimals1", 6))` raises `ValueError` or `TypeError`.

---

## 3. Recommendation & Implementation Strategy

We recommend implementing the following fixes in the next phase (to be completed by the implementer worker):

### 1. Robust File Locking and Serialization in IPC Dictionary
- **Acquire Exclusive Advisory Lock**:
  Use `fcntl.flock` to serialize file operations across processes. Implement a robust context manager or wrapper for reading and writing to the `.custom_pools_ipc.json` file.
- **Thread-safe Writes**:
  Maintain a local threading Lock to serialize concurrent writes from different tasks within the same process.
- **Unique Temp Files**:
  Use `tempfile.NamedTemporaryFile(dir=...)` or append process ID / random suffix to the `.tmp` filename (e.g. `tmp_file = f"{ipc_file}.{os.getpid()}.tmp"`) to prevent concurrent processes from writing to the same temporary file.
- **Timestamp or Size-based Reloading**:
  Instead of caching the file path in `self._last_ipc_file`, keep track of the file's modification time (`os.path.getmtime(current_file)`) and size. If the file has been updated, clear the cache and reload.
  Example structure for `_sync()`:
  ```python
  def _sync(self) -> None:
      current_file = _get_ipc_file()
      try:
          if os.path.exists(current_file):
              mtime = os.path.getmtime(current_file)
              if mtime != self._last_mtime:
                  with open(current_file, "r") as f:
                      # Acquire shared lock for reading
                      # Load JSON
                      # Update dict
                  self._last_mtime = mtime
      except Exception:
          pass
  ```

### 2. Strict Input Validation at Registration Time
Modify `_register_custom_pools` to validate all parameters BEFORE saving them to the dictionary and persisting them:
- **Type Check**: Validate that the pool type is one of `{"uniswap_v3", "aerodrome_v2", "aerodrome"}`.
- **Address Validation**: Check that the token0, token1, and (optional) pool addresses are valid Ethereum hex addresses using `AsyncWeb3.is_address()` or a regex pattern `^0x[a-fA-F0-9]{40}$`.
- **Parameter Check per Type**:
  - If `uniswap_v3`: ensure `fee` is present and is a valid integer (e.g., `100`, `500`, `3000`, `10000`).
  - If `aerodrome_v2` / `aerodrome`: ensure `stable` is present and is a boolean value.
- **Decimals Check**: Ensure `decimals0` and `decimals1` are positive integers (typically between 0 and 18, cap at 36).
- **Early Rejection**: If validation fails, raise a descriptive `ValueError` and do NOT write the configuration to the IPC file.

### 3. Implement Full Test Coverage in `test_connector.py`
Add tests in `tests/exchanges/base_onchain/test_connector.py` to cover:
- Registering an Aerodrome V2 custom pool.
- Validating the behavior when missing/invalid arguments are passed to registration (expecting `ValueError`).
- Multi-process persistence testing by simulating concurrent writes and verifying file locks are held.
- Verification of `list_instruments()` behavior for both Uniswap V3 and Aerodrome V2 custom pools.
