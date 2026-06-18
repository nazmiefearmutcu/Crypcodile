## 2026-06-14T18:59:02+03:00
You are a Worker (teamwork_preview_worker).
Your working directory is /Users/nazmi/Crypcodile/.agents/worker_m1_remediation.
Your task is to remediate the bugs and regressions identified in Milestone 1: Native AsyncWeb3 refactoring.

Specifically, you must:
1. Run `uv run pytest tests/exchanges/base_onchain/` to see the failing tests and tracebacks.
2. Fix the UnboundLocalError in `src/crypcodile/exchanges/base_onchain/connector.py`:
   - Initialize `swaps = []` at the top of the polling loop or ensure it is always defined before it is referenced in block C, even when exceptions are raised in the pool state/log query blocks.
3. Fix the Log Duplication / Global Cursor issue in `connector.py`:
   - Instead of a single global `self._last_block`, track the last polled block number *per pool symbol* (e.g. using a dictionary `self._last_blocks: dict[str, int]`). If a pool's query succeeds, advance its cursor. If it fails, keep its cursor the same. This prevents healthy pools from duplicating logs when another pool fails.
4. Fix the Connection/Socket Leak in `src/crypcodile/mcp_server.py`:
   - Ensure the `AsyncWeb3` instance or its provider is properly closed after usage. The cleanest way is to use `async with AsyncWeb3(AsyncHTTPProvider(rpc_url)) as w3:` context manager inside `get_onchain_price` so that the underlying `aiohttp.ClientSession` is closed automatically.
5. Fix the API Server Silent Failures in `src/crypcodile/api_server.py`:
   - If `get_onchain_price` returns an error payload (e.g. `{"error": ...}`), the API server should raise a proper `HTTPException(status_code=500, detail=data["error"])` instead of returning a 200 OK success response with the error payload.
6. Fix the failing tests in `tests/exchanges/base_onchain/test_servers.py`:
   - Inspect the exact tracebacks and fix any mock mismatches. Since `get_onchain_price` is now async, ensure that test mocks patch it to return an awaitable/coroutine (e.g. using `AsyncMock` or `AwaitableValue`) instead of a synchronous dict.
   - Resolve any TypeError where a class/type is passed instead of a Path/string.

Verify that all tests pass by running `uv run pytest tests/exchanges/base_onchain/`. Verify that `uv build` succeeds.
Write your changes and handoff report with build/test results to `/Users/nazmi/Crypcodile/.agents/worker_m1_remediation/handoff.md`.

## 2026-06-15T00:14:15+03:00
You are a worker agent. Your task is to remediate the vulnerabilities and bugs identified by the challenger agents in Milestone 1:
1. **Transaction Replay / Double Spend**: In `src/crypcodile/api_server.py`, add check to verify that `tx_hash` is not already used in any paid payment record in `PAYMENTS_DB`. If it is already used, raise an `HTTPException` with status code `400` and detail `"Transaction hash already processed."`.
2. **Coroutine in _get_block_number**: In `src/crypcodile/exchanges/base_onchain/connector.py`'s `_get_block_number`, verify if `w3.eth.block_number` is awaitable / coroutine and await it correctly inside the inner function `get_bn` before returning (e.g. check `inspect.isawaitable(val)`).
3. **Monotonic Cursor Update**: In `src/crypcodile/exchanges/base_onchain/connector.py`, update the cursor `self._last_blocks[sym]` only if `current_block >= self._last_blocks[sym]`. For example: `self._last_blocks[sym] = max(self._last_blocks[sym], current_block)`.
4. **Dynamic IPC Config Reload**: In `src/crypcodile/exchanges/base_onchain/connector.py`, call `_load_ipc()` at the start of each iteration of the polling loop (`_poll_loop`) to ensure that dynamically added custom pools are discovered.
5. **IPC File Locking**: In `src/crypcodile/exchanges/base_onchain/connector.py`, add file locking (using `fcntl.flock` on Unix systems) when reading and writing the `IPC_FILE` in `_load_ipc` and `IPCDict._write_ipc` to prevent concurrent write corruption.

Run all tests including the new challenger tests (e.g., `uv run pytest`) and ensure everything passes without warnings/errors.
Create a handoff report documenting the changes made.
