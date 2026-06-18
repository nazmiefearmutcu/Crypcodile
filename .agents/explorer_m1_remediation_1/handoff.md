# Handoff Report — explorer_m1_remediation_1

## 1. Observation

- **Verbatim Error**:
  ```
  E2E API SERVER ERROR: {"detail":"Failed fetching pool state: Provider must inherit from ``PersistentConnectionProvider`` class when instantiating via ``async with``."}
  ```
- **Context Manager Bug**:
  - File: `src/crypcodile/mcp_server.py`, line 85:
    ```python
    async with AsyncWeb3(AsyncHTTPProvider(rpc_url)) as w3:
    ```
- **Session Leak in Connector**:
  - File: `src/crypcodile/exchanges/base_onchain/connector.py`, lines 134-437:
    - Instantiates `w3 = AsyncWeb3(AsyncHTTPProvider(self.rpc_url))` at line 134.
    - Exits the `_poll_loop` without calling `await w3.provider.disconnect()`.
- **Session Leaks in Tests**:
  - File: `tests/e2e/test_tier1_features.py`, lines 1028 and 1051:
    - Instantiates `w3 = AsyncWeb3(AsyncHTTPProvider(rpc_url))` without calling `await w3.provider.disconnect()`.
- **Test Failures**:
  - Command: `uv run pytest`
  - Output: `1 failed, 641 passed, 1 warning`
  - Failed test: `tests/e2e/test_smoke_e2e.py::test_api_server_payment_flow` due to the 500 error returned by `api_server.py`.
- **Dummy Facades**:
  - File: `src/crypcodile/exchanges/base_onchain/normalize.py`, lines 91-96:
    - Returns orderbook with `depth=1` instead of `depth=5`:
      ```python
      bids=[(bid_px, bid_sz)],
      asks=[(ask_px, ask_sz)],
      depth=1,
      ```
  - File: `src/crypcodile/api_server.py`, lines 98-99:
    - Bypasses real on-chain transaction receipt check:
      ```python
      record["status"] = "paid"
      record["tx_hash"] = tx_hash
      ```

## 2. Logic Chain

1. In `test_smoke_e2e.py`, `test_api_server_payment_flow` simulates a payment and requests market data from `api_server`.
2. The `api_server` handles `/api/v1/market-data` by calling `get_onchain_price(symbol)`.
3. `get_onchain_price` uses an `async with` context manager with `AsyncHTTPProvider`.
4. In Web3.py, `AsyncHTTPProvider` is a non-persistent provider, which triggers a `TypeError` when instantiated via `async with`.
5. This runtime error gets caught by the `get_onchain_price` exception block and returned as `{"error": "Failed fetching pool state: Provider must inherit from PersistentConnectionProvider..."}`.
6. The `api_server` converts this error into a `500 Internal Server Error`.
7. This causes `test_api_server_payment_flow` and multiple payment verification tests in `test_tier1_features.py` to fail.
8. Further, `BaseOnchainTransport` and test fixtures instantiate `AsyncHTTPProvider` but never call `await w3.provider.disconnect()`, leading to socket and HTTP client leaks.
9. To pass the test suite and remediate Milestone 1, we must fix the context manager syntax, implement `await w3.provider.disconnect()` cleanup blocks, and eventually replace the dummy facades (orderbook depth and payment verification) with real implementations.

## 3. Caveats
- No caveats. The root cause of the test failures and connection leaks was successfully identified and isolated.

## 4. Conclusion
Milestone 1 has critical defects (context manager crash and session leaks) that prevent the E2E tests from passing and cause connection leaks. Remediation is needed to replace `async with AsyncWeb3` with explicit instantiation and `w3.provider.disconnect()` in a `try...finally` block. Downstream milestones (synthetic orderbook depth and onchain USDC payment log verification) are currently dummy facades and must be fully implemented.

## 5. Verification Method
1. Fix the context manager syntax in `src/crypcodile/mcp_server.py` and run:
   ```bash
   uv run pytest tests/e2e/test_smoke_e2e.py -s
   ```
   This command should pass once the 500 error is resolved and the API server begins returning the gated payload.
2. Confirm session cleanup by checking that no unclosed client session warnings are raised by pytest.
