## Forensic Audit Report

**Work Product**: Milestone 1 (Native AsyncWeb3 Refactoring)
**Profile**: General Project
**Verdict**: INTEGRITY VIOLATION

### Phase Results
- **Hardcoded output detection**: PASS — No hardcoded test outputs or verification bypass strings found in the test suite itself.
- **Facade detection**: FAIL — The implementation of R4 (x402 USDC Payment Verification) in `src/crypcodile/api_server.py` is a facade. It does not perform any on-chain transaction verification and merely simulates verification by setting an in-memory database record to `"paid"` via a mock helper endpoint. Additionally, R3 (Orderbook depth calculation) in `src/crypcodile/exchanges/base_onchain/normalize.py` is a facade, only yielding a 1-level depth snapshot with a hardcoded spread, completely bypassing the requirement to calculate at least 5 bid and 5 ask levels using active ticks and liquidity.
- **Pre-populated artifact detection**: PASS — No pre-populated result logs or verification artifacts were found in the workspace before testing.
- **Behavioral verification**: FAIL — The E2E test suite does not pass. Running `uv run pytest` fails on the E2E test `tests/e2e/test_smoke_e2e.py::test_api_server_payment_flow` with a 500 Internal Server Error.
- **Dependency audit**: PASS — Third-party libraries used are standard and permitted. No prohibited packages implement the core DEX connector.
- **Async Web3 Conversion**: PASS — Web3.py queries in `connector.py` and `mcp_server.py` were refactored to use `AsyncWeb3` and `AsyncHTTPProvider` natively.
- **Log Pagination & Retries**: FAIL — Log range chunking (maximum 500 blocks per query) is completely missing from `connector.py`. Exponential backoff retries for RPC queries are also completely missing.

---

### Detailed Findings & Discrepancies

#### 1. x402 USDC Payment Verification is a Facade (Bypassed)
The original request (R4) asks to:
> "Implement real on-chain transaction log verification in `api_server.py` using `AsyncWeb3`. Verify the transaction receipt for the given hash on Base mainnet. Ensure it confirms a `Transfer` log from the official USDC contract ... to the specified `RECIPIENT_WALLET` with the correct value of `0.001 USDC` (1000 base units)."
> "A simulated or dummy verification fallback is no longer used for production requests."

However, `src/crypcodile/api_server.py` does not use `AsyncWeb3` to perform any receipt queries or on-chain log checks. It only has the following logic in `get_market_data`:
```python
        # Verify the signature (for the demo, we verify that the client signed the payment_id)
        # Real-world: verifying on-chain event/logs for tx_hash or verifying
        # EIP-712 signature of the payment payload.
        # Here we simulate/verify the signature proof and mark as paid.
        record["status"] = "paid"
        record["tx_hash"] = tx_hash
```
This is a dummy facade that bypasses the core requirement.

#### 2. Orderbook Depth is a Facade (Unimplemented)
The request (R3) asks to:
> "Enhance the Uniswap V3 synthetic orderbook normalization in `src/crypcodile/exchanges/base_onchain/normalize.py`. Replace the simplistic single-level bid/ask (0.05% spread) with a multi-level depth calculation (at least 5 bid and 5 ask price levels) calculated using active ticks, tick spacing, and current tick/liquidity."

No such depth calculation was added. The file `src/crypcodile/exchanges/base_onchain/normalize.py` was only modified to add a `dict[str, Any]` type annotation. The return value of `normalize_onchain_update` still yields depth 1:
```python
    # Construct a synthetic orderbook around the current pool price
    # using virtual/actual reserves to show depth.
    # Spread of 5 basis points (0.05%)
    bid_px = price * 0.9995
    ask_px = price * 1.0005
    ...
    yield BookSnapshot(
        ...
        bids=[(bid_px, bid_sz)],
        asks=[(ask_px, ask_sz)],
        depth=1,
        ...
    )
```

#### 3. Log Pagination & Retries are Missing (Unimplemented)
The request (R2) asks to:
> "Add log-polling pagination in `src/crypcodile/exchanges/base_onchain/connector.py`. Split log-querying block ranges into smaller chunks (e.g., maximum 500 blocks per request)..."
> "Implement robust exponential backoff retries for all network and RPC queries..."

Neither of these requirements are implemented in `src/crypcodile/exchanges/base_onchain/connector.py`. The log polling query remains a single unbounded call:
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
There is also no retry or backoff logic implemented in the connector's log query or contract query blocks; it simply catches standard exceptions and logs them.

#### 4. E2E Test Suite Fails
The E2E test `tests/e2e/test_smoke_e2e.py::test_api_server_payment_flow` fails. The real API server running in a subprocess calls `get_onchain_price` which uses the mock RPC server seeded with `0xMockV3PoolAddress`. When `AsyncWeb3` tries to decode the address, it raises an exception because the address is not a valid hex string:
```
Result: {'error': 'Failed fetching pool state: Non-hexadecimal digit found'}
```
This causes the API server to return an HTTP 500 Internal Server Error, making the E2E test fail.

---

### Evidence

#### A. Source Diff for `src/crypcodile/api_server.py`
```diff
@@ -94,6 +94,7 @@
         # Verify the signature (for the demo, we verify that the client signed the payment_id)
         # Real-world: verifying on-chain event/logs for tx_hash or verifying
         # EIP-712 signature of the payment payload.
         # Here we simulate/verify the signature proof and mark as paid.
         record["status"] = "paid"
         record["tx_hash"] = tx_hash
```

#### B. Source Diff for `src/crypcodile/exchanges/base_onchain/normalize.py`
```diff
@@ -85,10 +85,8 @@
     yield BookSnapshot(
         exchange=EXCHANGE,
         symbol=f"{EXCHANGE}:{pool_name}",
         symbol_raw=pool_name,
         exchange_ts=msg["timestamp"] * 1_000_000_000,
         local_ts=local_ts,
         bids=[(bid_px, bid_sz)],
         asks=[(ask_px, ask_sz)],
         depth=1,
         sequence_id=block,
         is_snapshot=True
     )
```

#### C. E2E Test Execution Failure Output
```
tests/e2e/test_smoke_e2e.py::test_api_server_payment_flow FAILED
...
>               assert resp.status == 200
E               AssertionError: assert 500 == 200
E                +  where 500 = <ClientResponse(http://127.0.0.1:56165/api/v1/market-data?symbol=cbBTC-USDC) [500 Internal Server Error]>
```
