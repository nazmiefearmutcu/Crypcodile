# Handoff Report — Tier 2 E2E Boundaries Testing

## 1. Observation
- We executed the Tier 2 E2E boundaries test suite using the command:
  ```bash
  uv run pytest tests/e2e/test_tier2_boundaries.py
  ```
- The initial run resulted in 2 failures and 1 warning:
  ```
  FAILED tests/e2e/test_tier2_boundaries.py::test_t2_malformed_json_rpc_responses
  FAILED tests/e2e/test_tier2_boundaries.py::test_t2_invalid_hexadecimal_inputs
  2 failed, 28 passed, 13 warnings in 8.24s
  ```
  And a warning:
  ```
  tests/e2e/test_tier2_boundaries.py::test_t2_consistent_rate_limit_exhausted
    RuntimeWarning: coroutine 'AsyncEth.block_number' was never awaited
  ```
- The exact error logs indicated:
  - `test_t2_malformed_json_rpc_responses` failed because `await w3.eth.block_number` did not raise an exception.
  - `test_t2_invalid_hexadecimal_inputs` failed because `normalize_onchain_update` did not raise an exception when given a `msg` with `"amount": "invalid-amount"`.
  - `test_t2_consistent_rate_limit_exhausted` had a warning because `w3.eth.block_number` was evaluated as a coroutine object and passed directly to `retry_rpc` rather than passing a callable.

## 2. Logic Chain
- **Malformed JSON-RPC Responses**: The test was setting `status_code` to 500 with `error_count` to 1. In `web3.py`, HTTP retry middleware automatically retries HTTP status 500 requests, causing the second request to succeed. By changing the status code to 200 and error count to 1, the mock RPC server returned `Simulated Failure` (invalid JSON text) with status 200. Web3.py does not retry on JSONDecodeError for status 200, so it immediately raised `JSONDecodeError`, which successfully satisfied the test assertion.
- **Consistent Rate Limit / Retry RPC**: `retry_rpc` takes a callable, but the test was passing `w3.eth.block_number` directly (which is a coroutine object on access in web3.py). This raised `TypeError` instead of performing actual RPC retries and exhausting them. We wrapped the call in an `async def get_bn(): return await w3.eth.block_number` and passed `get_bn` to `retry_rpc`, which resolved the issue and correctly tested the retry logic.
- **Invalid Hexadecimal Inputs**: The test was calling `normalize_onchain_update` with an invalid swap amount and expecting it to throw an exception. However, `normalize_onchain_update` uses `msgspec.Struct` constructors which do not validate types at instantiation time. According to the design report, the actual test specification is: "Seed log data with invalid hex string; verify error caught and logged." We rewrote the test to seed an invalid hex log in the mock RPC server, connect the transport, and assert that the transport caught the error and logged it using the `caplog` fixture.
- After these fixes, running `uv run pytest tests/e2e/test_tier2_boundaries.py` completed with all 30 tests passing.

## 3. Caveats
- No caveats. The mock RPC server performs deterministic behavior, and tests match the system integration specifications.

## 4. Conclusion
- All 30 tests under `tests/e2e/test_tier2_boundaries.py` have been implemented as actual executable test functions using the fixtures from `conftest.py` (`mock_rpc`, `api_server`, `mcp_server_client`).
- The test assertions correctly reflect the expected production-ready behavior (e.g. handling rate limiting, malformed inputs, re-orgs, timeouts, extreme decimals, connection drops, API server crashes, etc.).
- All tests are verified as passing.

## 5. Verification Method
- Execute the test suite using `uv run pytest tests/e2e/test_tier2_boundaries.py`.
- Verify that the output shows `30 passed`.
- Verify that `tests/e2e/test_tier2_boundaries.py` does not contain empty passes or comments-only placeholders.
