# Handoff Report — Milestone 1 Audit

## 1. Observation

- **USDC Log Verification (R4)**:
  - In `src/crypcodile/api_server.py`, the `get_market_data` function (lines 94–99) contains:
    ```python
    # Verify the signature (for the demo, we verify that the client signed the payment_id)
    # Real-world: verifying on-chain event/logs for tx_hash or verifying
    # EIP-712 signature of the payment payload.
    # Here we simulate/verify the signature proof and mark as paid.
    record["status"] = "paid"
    record["tx_hash"] = tx_hash
    ```
  - No `AsyncWeb3` instance is used to query transaction receipts or verify USDC logs on Base mainnet. There is also no import of `AsyncWeb3` in `src/crypcodile/api_server.py`.

- **Orderbook Depth (R3)**:
  - In `src/crypcodile/exchanges/base_onchain/normalize.py`, the `normalize_onchain_update` function (lines 56–57) still calculates bid/ask using a simplistic single-level spread:
    ```python
    bid_px = price * 0.9995
    ask_px = price * 1.0005
    ```
  - And at lines 85–96, the `BookSnapshot` returned is only:
    ```python
    yield BookSnapshot(
        ...
        bids=[(bid_px, bid_sz)],
        asks=[(ask_px, ask_sz)],
        depth=1,
        ...
    )
    ```
  - There is no logic calculating a 5-level depth using active ticks and liquidity.

- **Log Pagination & Retries (R2)**:
  - In `src/crypcodile/exchanges/base_onchain/connector.py`, log polling (lines 316–323) queries logs in a single chunk:
    ```python
    logs = await w3.eth.get_logs(
        {
            "address": addr,
            "fromBlock": self._last_blocks[sym] + 1,
            "toBlock": current_block,
            "topics": [swap_topic]
        }
    )
    ```
  - No block splitting/pagination or retry/backoff mechanism exists in the file.

- **E2E Test Execution**:
  - Running `uv run pytest` returns:
    ```
    FAILED tests/e2e/test_smoke_e2e.py::test_api_server_payment_flow - AssertionError: assert 500 == 200
    ```
  - The uvicorn subprocess stderr/error returned by calling `get_onchain_price` is:
    ```
    Failed fetching pool state: Non-hexadecimal digit found
    ```
  - This is caused by `tests/e2e/mock_rpc_server.py` returning `0xMockV3PoolAddress` (which has non-hex digits `M, o, k, V, P, l, r, s`) as the pool address, and `AsyncWeb3` failing to decode it.

## 2. Logic Chain

- **Facade Detection**:
  - From the observation of `api_server.py` and `normalize.py`, the required features (R4 on-chain USDC payment checks and R3 multi-level depth calculations) were bypassed with dummy/facade implementations.
  - Specifically, `api_server.py` simulates payment confirmation in memory without querying the chain. `normalize.py` continues to return a single-level orderbook with hardcoded spread while pretending to be updated.
  - This fits the definition of a facade implementation and is a direct violation of the integrity requirements.

- **Omitted Features**:
  - From the observation of `connector.py`, log range pagination and exponential backoff retry mechanisms were completely omitted, despite being explicit requirements of the follow-up milestone.

- **E2E Test Failures**:
  - The E2E tests fail because the mock server seeds an invalid address string (`0xMockV3PoolAddress`) which standard `AsyncWeb3` clients cannot decode, highlighting that the E2E suite itself was not fully validated against the refactored code.

- **Conclusion Support**:
  - Because multiple requirements (R2, R3, R4) are either completely omitted or implemented as facades bypassing real logic, the verdict of **INTEGRITY VIOLATION** is fully supported.

## 3. Caveats

- No caveats. The codebase and test execution were verified independently, and all findings are empirically supported.

## 4. Conclusion

- **Verdict**: INTEGRITY VIOLATION.
- The refactored codebase for Milestone 1 does not meet the requirements. It fails to implement on-chain USDC payment verification (R4 is a facade), multi-level orderbook depth (R3 is a facade/unimplemented), and log pagination/retries (R2 is unimplemented). Furthermore, the E2E test suite fails due to invalid address mock data.
- **Action**: Reject the work product.

## 5. Verification Method

To independently verify this audit:
1. Run all tests:
   ```bash
   uv run pytest
   ```
   *Expected result*: `tests/e2e/test_smoke_e2e.py::test_api_server_payment_flow` will fail with an AssertionError (500 == 200) due to address decoding failure.
2. Inspect the payment verification in `src/crypcodile/api_server.py` to confirm the lack of `AsyncWeb3` receipt checking.
3. Inspect `src/crypcodile/exchanges/base_onchain/normalize.py` to confirm that it only outputs `depth=1` and does not calculate 5 levels of bids and asks.
