# Handoff Report — explorer_m5_2

## 1. Observation
Direct observations from the `base_onchain` exchange connector module and its unit tests:

1. **Connector Implementation File**: `src/crypcodile/exchanges/base_onchain/connector.py`
   - **Stale Cache Check** (lines 73–87):
     ```python
     def _sync(self) -> None:
         current_file = _get_ipc_file()
         if current_file != self._last_ipc_file:
             dict.clear(self)
             ...
             self._last_ipc_file = current_file
     ```
     `current_file` is constant, meaning `_sync()` only reloads the JSON file once on startup.
   - **Concurrent Writes on Same Temp File** (lines 51–56):
     ```python
     tmp_file = ipc_file + ".tmp"
     with open(tmp_file, "w") as f:
         json.dump(data, f)
         f.flush()
         os.fsync(f.fileno())
     os.replace(tmp_file, ipc_file)
     ```
     No locks are used, and all concurrent calls to `_write_ipc_to_file` read and overwrite the same temporary file and target file.
   - **No-op Reload** (lines 60–61):
     ```python
     def _load_ipc_sync() -> None:
         pass
     ```
     `_load_ipc_sync` does not do any dictionary synchronization.
   - **Missing Parameter Validation in `_register_custom_pools`** (lines 206–240):
     There is no schema check, address verification, or parameter checks (such as required `fee` for Uniswap V3 or `stable` for Aerodrome V2) before saving configuration.
   - **Crashes during Resolution**:
     - Uniswap V3 fee lookup: `fee = int(spec["fee"])` (line 487)
     - Aerodrome V2 stable flag lookup: `stable = bool(spec["stable"])` (line 496)
     These trigger a `KeyError` at runtime if missing.

2. **Tests File**: `tests/exchanges/base_onchain/test_connector.py`
   - **Test `test_custom_pool_configuration_and_dynamic_listing`** (lines 635–677):
     Checks local registration and instrument listing for Uniswap V3 custom pool `"TESTCUSTOM-WETH"`. It does not test Aerodrome V2 registration, cross-process persistence, file locking, or validation checks.

3. **Test Command Output**:
   Running `.venv/bin/pytest tests/exchanges/base_onchain/test_connector.py` completes successfully:
   ```
   10 passed, 1 warning in 0.21s
   ```

---

## 2. Logic Chain
1. **Broken IPC**: Because `_sync()` checks if the path to the IPC file (`current_file`) has changed before reloading, and that path remains static (e.g. `.custom_pools_ipc.json`), the condition `current_file != self._last_ipc_file` is false for all calls after the first one. Hence, dynamic custom pool updates made by other processes are never loaded into the connector.
2. **Race Conditions**: Since multiple processes and threads write to the same `.custom_pools_ipc.json` file and `.custom_pools_ipc.json.tmp` file concurrently without a file lock (like `fcntl.flock`), the JSON file is highly prone to corruption and lost updates.
3. **Runtime Crashes**: Because `_register_custom_pools` does not validate that `fee` is present for Uniswap V3 pools, or `stable` is present for Aerodrome V2 pools, or that decimals and addresses are valid, any malformed registration will cause a runtime exception (such as `KeyError` or `ValueError`) when parsing the specs in `resolve_single_pool` or `poll_single_pool`.
4. **Stale list_instruments**: Since `POOL_SPECS` fails to sync dynamically added pools from the file, `list_instruments()` will skip any custom pools registered by other processes, causing those instruments to be absent from the listing.

---

## 3. Caveats
- Only `base_onchain` connector was analyzed for Milestone 5 custom pool registration; other connectors are assumed to be out of scope.
- We assume that the `.custom_pools_ipc.json` file path behaves correctly if set via the `CUSTOM_POOLS_IPC_FILE` environment variable.

---

## 4. Conclusion
The custom pool dynamic configuration is not production-ready due to lack of cross-process synchronization, lack of file locking, lack of input parameter validation, and missing test coverage for Aerodrome V2 custom pools and invalid configurations. 

---

## 5. Verification Method
- **Command**:
  ```bash
  .venv/bin/pytest tests/exchanges/base_onchain/test_connector.py
  ```
- **Files to Inspect**:
  - `src/crypcodile/exchanges/base_onchain/connector.py`
  - `tests/exchanges/base_onchain/test_connector.py`
- **Invalidation Condition**:
  If the tests run successfully but fail to catch race conditions (simulated by multi-threaded writes) or malformed config registration crashes, the verification method is insufficient.
