## 2026-06-14T16:05:22Z
You are a Worker (teamwork_preview_worker).
Your working directory is /Users/nazmi/Crypcodile/.agents/worker_m1_complete.
Your task is to implement the full suite of implementation requirements (Milestones 1 to 5) to resolve all regressions, socket leaks, and integrity violations reported by the Reviewers and the Forensic Auditor.

Specifically, you must:

1. **Fix connection/socket leak in `src/crypcodile/mcp_server.py`**:
   - Ensure the `AsyncWeb3` instance or its provider is properly closed after usage. The cleanest way is to use `async with AsyncWeb3(AsyncHTTPProvider(rpc_url)) as w3:` context manager inside `get_onchain_price` so that the underlying client session is closed automatically when exiting the function.

2. **Implement Log Range Pagination and Retries (Milestone 2) in `src/crypcodile/exchanges/base_onchain/connector.py`**:
   - Split log-querying block ranges into smaller chunks (maximum 500 blocks per chunk) to prevent RPC timeouts or range-exceeded errors.
   - Implement a robust exponential backoff retry mechanism (handling HTTP 429 rate-limiting, network timeouts, or node errors) for all async network and RPC queries (such as block_number, contract calls, and log querying).

3. **Implement Multi-Level Orderbook Depth Calculation (Milestone 3) in `src/crypcodile/exchanges/base_onchain/normalize.py`**:
   - For Uniswap V3, replace the simplistic single-level bid/ask with a multi-level depth calculation (at least 5 bid and 5 ask price levels) calculated using active ticks, tick spacing (derived from the pool fee tier), and current tick/liquidity.
   - Ensure that `connector.py` passes the necessary V3 parameters (`tick`, `liquidity`, `tick_spacing`) inside the `update_msg['state']` payload.
   - For Aerodrome V2, also provide at least 5 bid and 5 ask levels of depth calculated using reserves and spread math.

4. **Implement Production-Ready USDC Payment Verification (Milestone 4) in `src/crypcodile/api_server.py`**:
   - Query transaction receipts on Base mainnet via `AsyncWeb3`.
   - Validate on-chain transaction receipt:
     1. Transaction status must be successful (`status == 1`).
     2. Transaction logs must contain an ERC-20 `Transfer` event from the official USDC contract (`0x833589fCD6eDb6E08f4c7C32D4f71b54bda02913`).
     3. The log recipient must be the designated `RECIPIENT_WALLET`.
     4. The transfer amount must be exactly 1000 base units (`0.001 USDC` with 6 decimals).
   - If the payment is simulated (status is already marked as `"paid"` in `PAYMENTS_DB` via `simulate_payment`), you can skip the on-chain query to keep the simulated payment test working. If not already marked as paid, perform the actual on-chain validation and raise a proper `HTTPException(status_code=400, detail=...)` or `HTTPException(status_code=500, detail=...)` on failures instead of returning a 200 OK success with the error payload.

5. **Ensure Custom pool parameters are supported (Milestone 5)**:
   - Allow adding custom pools to the connector configuration dynamically via optional parameters (such as address, decimals, fee tier, factory type) passed during initialization.

6. **Fix the E2E Test Failure (`test_smoke_e2e.py`)**:
   - Investigate why `test_api_server_payment_flow` fails with "Non-hexadecimal digit found" (such as checking if mock address `0xMockV3PoolAddress` is being decoded by the real AsyncWeb3 client, and replace it with a valid hex address or mock it appropriately in the test file/mock server).

Run `uv run pytest` to ensure all 642 tests in the project (including E2E and unit tests) pass successfully. Verify that `uv build` compiles cleanly.
Write your handoff report containing the list of changes and passing build/test outputs to `/Users/nazmi/Crypcodile/.agents/worker_m1_complete/handoff.md`.

MANDATORY INTEGRITY WARNING:
> DO NOT CHEAT. All implementations must be genuine. DO NOT
> hardcode test results, create dummy/facade implementations, or
> circumvent the intended task. A Forensic Auditor will independently
> verify your work. Integrity violations WILL be detected and your
> work WILL be rejected.
