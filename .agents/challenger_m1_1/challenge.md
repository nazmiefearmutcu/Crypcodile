## Challenge Summary

**Overall risk assessment**: HIGH

Milestone 1 Native AsyncWeb3 refactoring introduces several correctness regressions and robustness issues under connection drops or RPC failures. Uncaught `UnboundLocalError` exceptions and duplicate swap logs occur under partial query failures.

---

## Challenges

### [High] Challenge 1: UnboundLocalError in `BaseOnchainTransport`

- **Assumption challenged**: The code assumes that if a pool query fails, it can be caught, marked as unsuccessful (`success = False`), and cleanly continue to the next pool or sleep.
- **Attack scenario**: If a pool state query (like `slot0()` or `getReserves()`) fails due to a network timeout or RPC drop, an exception is raised before `swaps = []` is initialized (line 312). The code catches the exception, but then proceeds to step C to push a state update to the queue using the local variable `swaps`. Since `swaps` was never assigned, an `UnboundLocalError` is raised.
- **Blast radius**: The `UnboundLocalError` escapes the loop body and is caught by the outer loop's `except Exception as e:` handler. This prevents any subsequent steps in the iteration from executing and pollutes log outputs with confusing tracebacks.
- **Mitigation**: Define `swaps = []` at the top of the pool iteration alongside `price`, `reserve0`, and `reserve1`, or skip step C entirely for failed pools.

### [Medium] Challenge 2: Duplicate Logs and Queue Pollution on Partial Pool Failure

- **Assumption challenged**: The code assumes that a global `self._last_block` cursor can be updated only when all pools succeed without affecting other pools.
- **Attack scenario**: If one pool query succeeds but another pool query in the same iteration fails, `success` is marked as `False`, and `self._last_block` is not advanced. In the next iteration, the successful pool queries `get_logs` again from the old `_last_block + 1`, retrieving and pushing duplicate swap logs to the queue.
- **Blast radius**: Downstream data sinks are polluted with duplicate `Trade` records, corrupting historical data and triggering erroneous trade triggers or alerts.
- **Mitigation**: Track the last block cursor independently per pool (e.g. in `resolved_pools[sym]["last_block"]`) instead of using a global `self._last_block`.

### [Medium] Challenge 3: Inconsistent HTTP responses under RPC failure in `api_server.py`

- **Assumption challenged**: The API server assumes that `get_onchain_price` returns valid market data or raises an exception.
- **Attack scenario**: If an RPC connection drop occurs, `get_onchain_price` returns a dictionary with an `"error"` key. The API server blindly returns this dictionary in the response with `"status": "success"` and HTTP 200 OK.
- **Blast radius**: API clients receive an HTTP 200 OK success response that actually contains a failed query error message, leading to silent failures or incorrect downstream logic.
- **Mitigation**: Check if `"error"` is present in the `data` returned by `get_onchain_price`, and if so, raise an `HTTPException` with status code 502 or 503.

### [Low] Challenge 4: Buggy Test Harness Mocks and Fixtures

- **Assumption challenged**: The test suite in `tests/exchanges/base_onchain/test_servers.py` is correctly configured.
- **Attack scenario**: 
  1. The tests patch `web3.AsyncWeb3` instead of `crypcodile.mcp_server.AsyncWeb3`, causing type validation/`isinstance` checks to fail inside web3.py internal code with `TypeError`.
  2. `test_mcp_server_serve_stdio` passes the class `pytest.TempPathFactory` to `serve_stdio(data_dir=...)` instead of a `Path` instance, causing path validation to fail with a `TypeError`.
- **Blast radius**: The unit tests for the MCP server and API server are broken and fail consistently.
- **Mitigation**: Correct the patch targets and use pytest's `tmp_path` fixture.

---

## Stress Test Results

- **Aerodrome reserves query failure** → Transport catches query error and handles `UnboundLocalError` → Logs `UnboundLocalError` and continues after sleep → **FAIL** (spurious traceback and aborted execution of subsequent queue updates)
- **Uniswap slot0 query failure** → Transport catches query error and handles `UnboundLocalError` → Logs `UnboundLocalError` and continues after sleep → **FAIL**
- **Test Server Suite execution** → run `uv run pytest tests/exchanges/base_onchain/` → 3 tests fail in `test_servers.py` → **FAIL**
- **Memory cache limit stress** → Query 1000+ blocks → block cache cleared to size 1 → **PASS**

---

## Unchallenged Areas

- **DuckDB DuckDB SQL querying** — Out of scope for base_onchain verification.
- **Perpetual funding rate APR calculations** — Out of scope.
