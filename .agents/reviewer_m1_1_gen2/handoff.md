# Handoff Report — Milestone 1 Review

## 1. Observation
- **Test execution**: Running `uv run pytest tests/exchanges/base_onchain/` succeeded with the output:
  ```
  37 passed, 1 warning in 0.85s
  ```
- **UnboundLocalError Fix**: In `src/crypcodile/exchanges/base_onchain/connector.py`, variables `price`, `reserve0`, `reserve1`, and `swaps` are initialized before the query block (lines 260–263):
  ```python
  260:                     price = 0.0
  261:                     reserve0 = 0.0
  262:                     reserve1 = 0.0
  263:                     swaps = []
  ```
- **Log Duplication Cursor Fix**: In `src/crypcodile/exchanges/base_onchain/connector.py`, cursors are tracked per pool symbol in `self._last_blocks` (dictionary initialized on line 89 and used in line 319). Cursors are only updated upon success at line 414:
  ```python
  414:                         self._last_blocks[sym] = current_block
  ```
- **Connection Leak Fix**: In `src/crypcodile/mcp_server.py`, the `get_onchain_price` helper uses an async context manager (line 83):
  ```python
  83:         async with AsyncWeb3(AsyncHTTPProvider(rpc_url)) as w3:
  ```
- **API Server Error Code Fix**: In `src/crypcodile/api_server.py`, error responses are checked and raised as `HTTPException(500)` (lines 109–111):
  ```python
  109:     data = await get_onchain_price(symbol)
  110:     if "error" in data:
  111:         raise HTTPException(status_code=500, detail=data["error"])
  ```
- **Failing Unit Tests Fix**: In `tests/exchanges/base_onchain/test_servers.py`, the mocked Web3 class includes standard async magic methods (lines 27–28, 68–69, 114–115):
  ```python
  mock_w3.__aenter__ = AsyncMock(return_value=mock_w3)
  mock_w3.__aexit__ = AsyncMock(return_value=None)
  ```

## 2. Logic Chain
- Initializing `swaps = []` before the `try:` block resolves the `UnboundLocalError` since the name is bound even if queries fail before log-parsing.
- Replacing the global block tracker with `self._last_blocks: dict[str, int]` ensures cursor tracking is isolated per pool. If one pool query throws an exception, only that pool's cursor remains unchanged, preventing duplicate queries for the other successfully polled pools.
- The `async with AsyncWeb3(...) as w3:` context manager ensures proper closing of the client instance, cleaning up all connection and socket descriptors.
- Raising a FastAPI `HTTPException(status_code=500, detail=data["error"])` ensures the HTTP response code is correctly set to 500 instead of returning a 200 OK with the error message.
- Providing `__aenter__` and `__aexit__` async mocks allows the mocked Web3 class to run in tests using `async with AsyncWeb3(...) as w3`, resolving mock-related failures in the unit test suite.

## 3. Caveats
- No caveats. The issues were verified by examining code modifications and validating that the test suite passes.

## 4. Conclusion
- The remediated implementation successfully resolves all 5 bugs and regressions, passes 100% of unit/stress tests, and contains no integrity violations. The verdict is **PASS**.

## 5. Verification Method
- Execute the test suite:
  ```bash
  uv run pytest tests/exchanges/base_onchain/
  ```
- Inspect target files (`connector.py`, `mcp_server.py`, `api_server.py`, and `test_servers.py`).
