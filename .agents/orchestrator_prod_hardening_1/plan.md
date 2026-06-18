# Production Hardening Plan

## Objectives
1. **R1: Resolve existing test failures & edge cases**: Run `uv run pytest` to ensure all tests pass, check for any edge-case or flaky tests in `tests/exchanges/base_onchain/`.
2. **R2: Concurrency and Race Condition Hardening**:
   - Refactor `connector.py` to use non-blocking asynchronous I/O (`asyncio.to_thread`) for `_write_ipc` and `_load_ipc` disk operations.
   - Refactor the poll loop in `connector.py` to query pool states and logs concurrently (using `asyncio.gather`) instead of sequentially, preventing Head-of-Line blocking.
3. **R3: Edge Case Review and Code Hardening**:
   - RPC rate limiting & network timeouts: Skip retrying deterministic exceptions (e.g. `ContractLogicError`, `BadFunctionCallOutput`, `ValidationError`) in `connector.py`.
   - Implement exponential backoff retries when fetching transaction receipts in `api_server.py`.
   - Resilience against block re-orgs: Poll logs with a small overlap buffer (e.g. 5 blocks) and filter duplicates in Python using a rolling set of seen log IDs `(tx_hash, log_index)`.
   - Log pagination gaps: Update `_last_blocks[sym]` incrementally after each successful pagination chunk instead of only at the end of the entire loop.
   - USDC payment validation & replay protection:
     - Persist `PAYMENTS_DB` to a local JSON file (`.payments_db.json`) using file locks or thread-safe writes to survive server restarts.
     - Validate transaction block timestamp to ensure it was mined recently (e.g. within the last 1 hour).
4. **R4: Adversarial Review (Challenge Report)**:
   - Create `CHALLENGE_REPORT.md` at `/Users/nazmi/Crypcodile/CHALLENGE_REPORT.md` summarizing the issues, the hardening fixes, and verification.
5. **E2E/Unit Test verification**:
   - Run the full test suite and write/update unit tests to verify the new behaviors (e.g., retry logic, rate limit handling, re-org overlap filtering, and DB persistence).
