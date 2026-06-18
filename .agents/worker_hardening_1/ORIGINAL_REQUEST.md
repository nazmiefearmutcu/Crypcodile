## 2026-06-15T00:37:57Z
You are a teamwork_preview_worker.
Your working directory is /Users/nazmi/Crypcodile/.agents/worker_hardening_1.
Your task is to implement the production hardening changes specified in the plan:
/Users/nazmi/Crypcodile/.agents/orchestrator_prod_hardening_1/plan.md.

Mandatory Instructions:
1. Run `uv run pytest` first to check the current state of tests.
2. Refactor `src/crypcodile/exchanges/base_onchain/connector.py` to:
   - Make file IPC (`_write_ipc` and `_load_ipc`) non-blocking using `asyncio.to_thread` or standard threadpool execution.
   - Run pool polling concurrently using `asyncio.gather` or concurrent tasks to prevent Head-of-Line blocking.
   - Prevent retrying deterministic RPC exceptions (e.g., `ContractLogicError`, `BadFunctionCallOutput`, `ValidationError` from `web3.exceptions`) in `_call_with_retry`.
   - Handle block re-orgs by querying logs with a small overlap buffer (e.g. 5 blocks) and deduplicating in-memory using a set of recently seen `(tx_hash, log_index)`.
   - Update `_last_blocks[sym]` incrementally after each successful pagination chunk in the log polling loop.
3. Refactor `src/crypcodile/api_server.py` to:
   - Implement robust retries with exponential backoff on `w3.eth.get_transaction_receipt(tx_hash)`.
   - Persist `PAYMENTS_DB` to a lock-protected local file (`/Users/nazmi/Crypcodile/.payments_db.json`) to survive restarts and prevent replay attacks.
   - Fetch the block of the payment transaction and validate that the block timestamp is recent (e.g., within the last 1 hour).
4. Update or add unit/integration tests under `tests/exchanges/base_onchain/` to verify these hardening mechanisms.
5. Create an Adversarial Review at `/Users/nazmi/Crypcodile/CHALLENGE_REPORT.md` covering:
   - Identified vulnerabilities (event-loop blocking, head-of-line blocking, replay attacks, missing retries).
   - Validation logic analysis.
   - Proof of hardening (how each issue is fixed).
6. Verify that `uv run pytest` passes 100% and `uv build` builds successfully.
7. Write a detailed handoff report to `/Users/nazmi/Crypcodile/.agents/worker_hardening_1/handoff.md`.

MANDATORY INTEGRITY WARNING:
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A Forensic Auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

Send a message to the orchestrator (conversation ID: ab5dcee8-f485-41a2-b6c6-1b4c68cc07ba) once done.
