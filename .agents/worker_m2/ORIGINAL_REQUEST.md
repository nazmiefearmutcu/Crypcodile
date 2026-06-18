## 2026-06-15T00:25:09Z
You are a worker assigned to implement fixes for Milestone 2 (Log pagination & backoff retries) in `src/crypcodile/exchanges/base_onchain/connector.py`.

Refer to the Explorer reports for details on the bugs and fix recommendations:
1. /Users/nazmi/Crypcodile/.agents/explorer_m2_1/analysis.md
2. /Users/nazmi/Crypcodile/.agents/explorer_m2_3/handoff.md

Your tasks:
1. Fix the UnboundLocalError loop termination in the poll loop of `BaseOnchainTransport`. Restructure the loop so that Step C (putting the state update on the queue and updating the block cursor) is only executed on success. Ensure that if a query fails for a symbol, that symbol is skipped, and other symbols in the same loop are still processed.
2. Prevent zeroed-out price/reserve updates from being pushed onto the queue for Aerodrome V2 (or any other symbol) on failure.
3. Fix the negative block number initialization bug on local testnets by using `max(0, current_block - 20)`.
4. Introduce random jitter (scaling factor of 50% to 100% of calculated delay) in `_call_with_retry` retry logic to avoid lockstep retries.
5. Remove the dead/unused `retry_rpc` function to reduce code complexity.
6. Verify your implementation by running the builds and tests. Verify that all tests (including stress/regression tests) pass successfully.

MANDATORY INTEGRITY WARNING:
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A Forensic Auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

Please document the changes you made in /Users/nazmi/Crypcodile/.agents/worker_m2/changes.md, run the test suite, and provide a detailed handoff report in /Users/nazmi/Crypcodile/.agents/worker_m2/handoff.md with passing build/test results.
