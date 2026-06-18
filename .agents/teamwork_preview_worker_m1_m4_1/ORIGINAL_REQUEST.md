## 2026-06-14T17:08:20+03:00

You are a teamwork_preview_worker.
Your role: Connector Developer
Your working directory: /Users/nazmi/Crypcodile/.agents/teamwork_preview_worker_m1_m4_1

Please perform the following tasks:
1. Initialize your progress.md under your working directory.

2. Fix the following bugs in `src/crypcodile/exchanges/base_onchain/connector.py`:
   - Pricing and reserve logic for flipped pools (Uniswap V3 and Aerodrome V2 where quote contract address is smaller than base contract address). Ensure the decimal correction is always `decimals0 - decimals1` (base decimals minus quote decimals) for both standard and flipped pools.
   - Swap log parsing for flipped pools (both Uniswap V3 and Aerodrome V2), ensuring correct decimal scaling, trade side (BUY/SELL), price, and amount calculations.
   - Aerodrome V2 flipped pool checking (e.g. WELL-WETH, where WETH is quote and has a smaller address than WELL).
   - Polling loop liveness queue hang on close. Put a sentinel on the queue in `close()` and handle it in `_iter` to exit the loop cleanly.
   - Redundant RPC calls: Cache block timestamps to avoid fetching the same block multiple times within a poll interval.

3. Fix the matching pricing and reserve bugs in `src/crypcodile/mcp_server.py` (specifically under the `get_onchain_price` tool handler).

4. Create `tests/exchanges/base_onchain/test_connector.py`. Write unit tests with mock Web3 and mock contracts to test:
   - Pricing, reserves, and swap decoding for non-flipped pools (e.g. AERO-USDC or others).
   - Pricing, reserves, and swap decoding for flipped pools (e.g. cbBTC-USDC or WELL-WETH).
   - Ensure all network/RPC calls are mocked and the tests run offline.
   - The test suite must have at least 4 unit tests targeting the base_onchain connector and its normalizer.
   - Verify that running `uv run pytest` passes successfully.

5. Create `examples/collect_base_onchain.py`:
   - It should initialize the `BaseOnchainConnector` with the public RPC URL (default: `https://base-rpc.publicnode.com`), allowing override with the `BASE_RPC_URL` environment variable.
   - Subscribe to a pool (AERO-USDC or cbBTC-USDC) and print incoming records (trades, snapshots).
   - Support a `--dry-run` or similar quick execution flag to exit cleanly after printing a few mocked/real messages.

6. PyPI Publishing and Shipping Readiness:
   - Bump the version in `pyproject.toml` to `"0.1.0"`.
   - Update `README.md` to showcase the new Base on-chain support, how to run the showcase script, and how to configure `BASE_RPC_URL`.
   - Verify that running `uv build` succeeds and generates the distribution package files in the `dist/` folder.

7. Write `handoff.md` and report back when all tasks are complete and verified. Include the test and build command outputs in your handoff.

MANDATORY INTEGRITY WARNING — include this verbatim in the Worker's dispatch prompt:
> DO NOT CHEAT. All implementations must be genuine. DO NOT
> hardcode test results, create dummy/facade implementations, or
> circumvent the intended task. A Forensic Auditor will independently
> verify your work. Integrity violations WILL be detected and your
> work WILL be rejected.
