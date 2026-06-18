# Handoff Report: Milestone 5 (Extensible custom pool configuration) Explorer Investigation

## 1. Observation

We directly examined the following files and executed verification commands:

1. **Broken IPC Dictionary Sync**:
   In `src/crypcodile/exchanges/base_onchain/connector.py:72-87`:
   ```python
   def _sync(self) -> None:
       current_file = _get_ipc_file()
       if current_file != self._last_ipc_file:
           dict.clear(self)
           dict.update(self, self._default)
           try:
               if os.path.exists(current_file):
                   with open(current_file, "r") as f:
                       content = f.read().strip()
                       if content:
                           file_data = json.loads(content)
                           if self._name in file_data:
                               dict.update(self, file_data[self._name])
           except Exception:
               pass
           self._last_ipc_file = current_file
   ```
   This was verified by running a Python one-liner simulating an external process writing to the IPC file. The results showed that `POOL_SPECS` did not pick up the external updates:
   - Initial keys: `['AERO-USDC', 'cbBTC-USDC', 'DEGEN-WETH', 'WELL-WETH']`
   - Keys after external write: `['AERO-USDC', 'cbBTC-USDC', 'DEGEN-WETH', 'WELL-WETH']`

2. **Single-Process and Inter-Process Concurrency Races**:
   In `src/crypcodile/exchanges/base_onchain/connector.py:107-113`:
   ```python
   def _write_ipc(self) -> None:
       data_copy = dict(self)
       try:
           loop = asyncio.get_running_loop()
           loop.create_task(asyncio.to_thread(_write_ipc_to_file, self._name, data_copy))
       except RuntimeError:
           _ipc_executor.submit(_write_ipc_to_file, self._name, data_copy)
   ```
   If a running event loop exists, `asyncio.to_thread` delegates to the default thread pool executor (multi-worker), meaning multiple concurrent writes can run in parallel. Additionally, `_write_ipc_to_file` has no file-level lock.

3. **Missing Parameter Validation in Registration**:
   In `src/crypcodile/exchanges/base_onchain/connector.py:206-239`, parameters like `type`, `decimals0`, `decimals1`, `fee`, `stable`, and address string formats are saved in `POOL_SPECS` without any checks.

4. **Flawed `list_instruments` and Tick Size**:
   In `src/crypcodile/exchanges/base_onchain/connector.py:873`:
   ```python
   tick_size = custom_ticks.get(sym, 10 ** (-int(spec.get("decimals1", 6))))
   ```
   Also, in `src/crypcodile/exchanges/base_onchain/connector.py:476`, `is_flipped` is evaluated inside `_poll_loop`'s local `resolve_single_pool` helper, but it is not persisted in the pool spec in `POOL_SPECS` or stored on the transport/connector instance.

5. **Existing Tests**:
   We verified that all 10 tests in `tests/exchanges/base_onchain/test_connector.py` pass cleanly using the command:
   ```bash
   .venv/bin/pytest tests/exchanges/base_onchain/test_connector.py
   ```

---

## 2. Logic Chain

1. **IPC Dictionary Sync Failure**:
   - `_sync()` is called on every dictionary read operation.
   - The method checks if the static file path string has changed: `current_file != self._last_ipc_file`.
   - On the first call, `_last_ipc_file` is empty, so it reads the file.
   - On all subsequent calls, the path string matches, so the check returns `False` and skips reading.
   - Therefore, any dynamic updates written to disk by other processes are ignored.

2. **Write Concurrency and Race Conditions**:
   - `asyncio.to_thread` executes tasks in parallel threads.
   - Without file-level locking, concurrent reads and writes across threads or processes lead to race conditions, lost updates, or corrupted JSON files.

3. **Late-Stage Polling Crashes**:
   - Registering parameters without early validation means invalid/missing types, address formats, fees, and decimals are registered in `POOL_SPECS`.
   - These parameters fail during polling (e.g., throwing `KeyError`, `ValueError`, or `ContractLogicError`), crashing pool resolution and logging errors dynamically.

4. **Incorrect Tick Size**:
   - Flipped pools use `token1` as the base asset and `token0` as the quote asset.
   - The price is quoted in terms of `token0` per unit of `token1`.
   - The tick size represents the price increment, so it must use `decimals0` (the quote token's decimals).
   - Unconditionally using `decimals1` results in wrong tick size values (e.g., setting tick size to `1e-18` instead of `1e-6` when quote is USDC and base is WETH).
   - Because `is_flipped` is local to `_poll_loop`, `list_instruments` lacks access to this information and cannot compute the correct tick size dynamically.

---

## 3. Caveats

- We did not implement or test a file-locking implementation.
- We assumed the address comparison `int(token1, 16) < int(token0, 16)` is the universal standard for determining a flipped pool on Uniswap V3 and Aerodrome V2 on Base.

---

## 4. Conclusion

The extensible custom pool configuration implementation suffers from:
- A broken IPC dictionary reload mechanism.
- Race conditions during file write operations.
- Lack of upfront parameter validation during registration.
- An incorrect tick size calculation for flipped pools in `list_instruments`.

**Actionable Recommendation for Implementer/Worker**:
1. Fix `IPCDict._sync` to track file modification time (`os.path.getmtime(current_file)`) and reload accordingly.
2. Add file locking using `fcntl.flock` (or a cross-platform lockfile) during read and write operations.
3. Serialize writes in the event loop by routing them through a single executor.
4. Implement input validation in `_register_custom_pools` for pool types, EVM address checksum formats, decimals, and required flags (`fee` for Uniswap V3, `stable` for Aerodrome V2).
5. Pre-calculate the `is_flipped` flag during registration and store it in `spec`, allowing `list_instruments` to correctly choose between `decimals0` and `decimals1` when computing the tick size.

---

## 5. Verification Method

To verify the fixes independently:
1. Inspect the implementation of `IPCDict._sync` and `_write_ipc_to_file`.
2. Inspect `_register_custom_pools` for parameter validation and pre-computation of `is_flipped`.
3. Inspect `list_instruments` tick size logic for flipped pools.
4. Run the test command:
   ```bash
   .venv/bin/pytest tests/exchanges/base_onchain/test_connector.py
   ```
5. Implement new tests verifying IPC sync (external updates), invalid registration inputs (should fail with `ValueError`), and correct tick sizes for flipped/non-flipped custom pools.
