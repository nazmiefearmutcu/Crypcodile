# Handoff Report — Milestone 5 Audit

## 1. Observation
- File `src/crypcodile/exchanges/base_onchain/connector.py` line 48: `fcntl.flock(lf.fileno(), fcntl.LOCK_EX)` locks the lock file exclusively for writing.
- File `src/crypcodile/exchanges/base_onchain/connector.py` line 119: `fcntl.flock(lf.fileno(), fcntl.LOCK_SH)` locks the lock file shared for reading.
- File `src/crypcodile/exchanges/base_onchain/connector.py` line 107-113: stat modification time and size are obtained (`st_mtime` and `st_size`) and compared with previous values to trigger reload.
- File `src/crypcodile/exchanges/base_onchain/connector.py` line 278-365: `_register_custom_pools` performs detailed parameter validations on EVM address format, unsupported types, decimals type and bounds, and required parameters (fee/stable), raising `ValueError` on validation failures.
- File `src/crypcodile/exchanges/base_onchain/connector.py` line 343: `is_flipped` status is derived at pool registration: `int(str(t1_addr), 16) < int(str(t0_addr), 16)`.
- File `src/crypcodile/exchanges/base_onchain/connector.py` line 1014: Instrument tick size for flipped pool derived from `decimals0` instead of `decimals1`.
- File `src/crypcodile/exchanges/base_onchain/connector.py` lines 584: Polling loop triggers `_load_ipc_sync` to dynamically discover newly registered pools at runtime.
- File `tests/exchanges/base_onchain/test_connector.py` executes successfully. The entire project test suite has been run using `.venv/bin/pytest` and successfully passed 769 tests.

## 2. Logic Chain
- Locking verification: Opening a separate lock file `.custom_pools_ipc.json.lock` using `a+` and locking it using `flock` (exclusive for writing, shared for reading) prevents concurrent file reads and writes from corrupting the IPC state. It also avoids file descriptor truncation issues.
- Reload logic: Stat checks on both modification time and size guarantee reload triggers even on filesystems where timestamp precision is low, and avoid unnecessary reloads when modification values are identical.
- Custom pool verification: Validating decimals (ensuring they are not booleans and are in $[0, 36]$) and verifying mandatory fields for Uniswap V3 (`fee`) and Aerodrome V2 (`stable`) prevents invalid parameters from causing runtime failures or RPC decoding bugs.
- Flipped pools: Storing the flipped status at registration ensures that standard on-chain pools can be queried correctly even if token addresses are reversed. Tick size calculation uses the quote asset's decimals (`decimals0` for flipped pools), ensuring the derived tick size matches the instrument structure.
- Polling: Because the loop calls `_load_ipc_sync` on each iteration, new pools registered at runtime are parsed dynamically, their contracts are instantiated, and they are queried.

## 3. Caveats
- The Web3 RPC queries in tests use mock contracts (`MagicMock` and `AsyncMock`), which is standard for offline testing but relies on mocks accurately reproducing node behaviors.
- The `is_flipped` detection assumes standard EVM address sorting rules, which is standard for Uniswap V3/Aerodrome factories but might differ for non-standard custom DEX factory models.

## 4. Conclusion
The implementation of the extensible custom pool configuration in Milestone 5 is highly robust, genuine, and implements all requested checks correctly. The final verdict is **CLEAN**.

## 5. Verification Method
- Execute the test suite to verify all unit/integration tests pass:
  ```bash
  .venv/bin/pytest tests/exchanges/base_onchain/test_connector.py
  ```
- Inspect file locking and IPC reloading in `/Users/nazmi/Crypcodile/src/crypcodile/exchanges/base_onchain/connector.py`.
