## 2026-06-15T01:29:59Z
Objective:
Implement Milestone 4: Production-ready x402 USDC payment verification in `src/crypcodile/api_server.py`, fix identified security/performance/robustness gaps, and verify with unit tests.

Here are the detailed gaps and requirements to implement:
1. Cryptographic Signature Bypass Fix:
   - Ensure signature validation is strictly enforced. If `signature` is malformed, missing, or cryptographic recovery fails (such as returning an invalid signer or throwing an error), raise an `HTTPException(status_code=400, detail="...")` immediately. NEVER allow requests to skip verification.
2. Concurrency Bottleneck Fix:
   - Reduce the scope of the global `db_lock`. Release it before starting any on-chain network queries (Web3 RPC calls, retries, sleep loops).
   - Re-acquire the lock only when writing to the database (marking status as `"paid"` or saving logs).
   - To prevent double spending/race conditions, use an in-memory set of transaction hashes currently in validation (or update a status `"verifying"` in the DB) so concurrent requests cannot evaluate the same transaction hash simultaneously.
3. Database Write Safety:
   - Standardize `_save_db_file` to use atomic write and rename (write to `.tmp` file, flush, fsync, and `os.replace` to original path). This prevents file truncation/corruption by concurrent worker processes.
4. Lifespan and Connection Pooling:
   - Maintain a single, persistent `AsyncWeb3` instance in the FastAPI application state (e.g., `app.state.w3`) initialized during a lifespan event and closed during shutdown.
5. RPC Fallback Failover:
   - Support a comma-separated list of Base RPC URLs (e.g. from `BASE_RPC_URLS` or fallback to `BASE_RPC_URL`).
   - Implement a failover mechanism where the application automatically switches to the next RPC URL in the list if the current one throws a connection or rate limit (HTTP 429) error.
6. Robust Transaction / Receipt Querying:
   - Perform the receipt polling first inside the retry loop. Once the receipt is obtained, fetch transaction details to verify sender, also wrapped in retry/backoff.
7. Safe Log Parsing:
   - Wrap hex data/topic parsing in try-except blocks, or use Web3.py's native event ABI processing.
   - Verify Chain ID is exactly `8453` (Base mainnet) to prevent cross-chain transaction replay.
8. Test Suite Verification:
   - Ensure the existing tests in `tests/exchanges/base_onchain/test_servers.py` pass.
   - Add new tests in `tests/exchanges/base_onchain/test_servers.py` (or a separate test file if appropriate) that specifically mock `AsyncWeb3` calls to cover:
     - Successful payment verification.
     - Signature verification failures (cryptographic validation, sender mismatch, invalid formats).
     - Transaction not found or rate limit retries.
     - RPC fallback failover.
     - Concurrency/lock validation.
   - Run tests and make sure 100% of tests pass.
