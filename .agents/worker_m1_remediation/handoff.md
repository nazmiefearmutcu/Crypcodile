# Handoff Report — 2026-06-15T00:22:30+03:00

## 1. Observation

- **Transaction Replay / Double Spend**: `src/crypcodile/api_server.py` did not check if a transaction hash (`tx_hash`) had already been associated with a paid payment in `PAYMENTS_DB`. As shown in the challenger test `tests/exchanges/base_onchain/test_challenger_remediation_6.py` at lines 105–107:
  ```python
  # This succeeds because the server doesn't check if the tx_hash has already been used!
  assert resp_2["status"] == "success"
  assert PAYMENTS_DB[pid2]["status"] == "paid"
  ```
- **Coroutine in _get_block_number**: `src/crypcodile/exchanges/base_onchain/connector.py` had an inner function `get_bn` inside `_get_block_number` (line 239) that directly returned `w3.eth.block_number` without checking if it was a coroutine or awaitable.
- **Monotonic Cursor Update**: The block polling cursor `self._last_blocks[sym]` was updated at line 615 in `connector.py` via `self._last_blocks[sym] = current_block`. If `current_block` rolled back (e.g., due to RPC node replication delays), it would cause duplicate log queries.
- **Dynamic IPC Config Reload**: In `connector.py`, `_load_ipc()` was not being called inside the polling loop (`_poll_loop`), preventing dynamically added pools from being loaded at runtime after the initial setup.
- **IPC File Locking**: `IPCDict._write_ipc` and `_load_ipc` accessed the shared configuration file `IPC_FILE` without any concurrent write/read synchronization, leading to potential data corruption or lost updates.
- **Test Results**: All 723 tests (including the challenger and E2E suites) passed cleanly after implementing these changes:
  ```
  723 passed, 37 warnings in 44.89s
  ```
  `uv build` was verified to run successfully and built distributions:
  ```
  Successfully built dist/crypcodile-0.1.0.tar.gz
  Successfully built dist/crypcodile-0.1.0-py3-none-any.whl
  ```

## 2. Logic Chain

- **Transaction Replay**: By iterating through `PAYMENTS_DB` and matching the incoming `tx_hash` against other `paid` records (excluding the current `pid` to preserve idempotency), the server can detect and reject replayed transactions with status code 400.
- **Awaitable Block Number**: By checking `inspect.isawaitable(val)` inside `_get_block_number` and awaiting the block number if it is awaitable, the connector correctly handles both async and sync Web3 provider behaviors.
- **Cursor Monotonicity**: Changing the update from `self._last_blocks[sym] = current_block` to `self._last_blocks[sym] = max(self._last_blocks[sym], current_block)` ensures the block cursor never moves backward, preventing duplicate log fetches.
- **Dynamic Pool Configuration**: Placing `_load_ipc()` at the start of each iteration in `_poll_loop` ensures any external pool additions to the shared IPC file are processed immediately.
- **IPC File Synchronization**: Integrating `fcntl.flock(f.fileno(), fcntl.LOCK_EX)` on writes and `fcntl.flock(f.fileno(), fcntl.LOCK_SH)` on reads guarantees process-safe access to `IPC_FILE`. Modifying `_write_ipc` to use file truncation (`f.truncate()`) and write-in-place ensures the file inode remains stable, making the flock locks visible to other processes opening the file.

## 3. Caveats

- Operating system file locking via `fcntl` is only available on Unix-based operating systems (macOS, Linux). Windows is not natively supported by `fcntl`, so the code includes a graceful fallback (`ImportError`) to prevent crashes on non-Unix platforms.

## 4. Conclusion

All five challenger issues have been remediated cleanly:
1. Replay attack verification endpoints in both real and simulated endpoints reject reuse of the same transaction hash.
2. Async/await behavior for block number extraction is robustly verified.
3. Polling cursor updates are strictly monotonic.
4. Custom pool registration loads dynamically within the main loop.
5. Process concurrency is safely managed via OS-level file locking.

## 5. Verification Method

To independently verify the changes, execute the following commands in the project root:

1. **Verify Test Suite**:
   ```bash
   uv run pytest
   ```
   All 723 tests must pass.
2. **Verify Build**:
   ```bash
   uv build
   ```
   Both the tarball and wheel distributions should build without errors.
3. **Inspect Code Files**:
   - Check `src/crypcodile/api_server.py` for `Transaction hash already processed.` checks in `get_market_data` and `simulate_payment`.
   - Check `src/crypcodile/exchanges/base_onchain/connector.py` for flock logic, `inspect.isawaitable` usage, `max(...)` updates, and `_load_ipc()` at the start of `_poll_loop`.
