## 2026-06-14T14:15:38Z
You are a teamwork_preview_worker.
Your role: Connector Developer (Iteration 2)
Your working directory: /Users/nazmi/Crypcodile/.agents/teamwork_preview_worker_m1_m4_2

Please perform the following tasks:
1. Initialize your progress.md under your working directory.

2. Resolve all issues identified by Reviewer 1 and Reviewer 2:
   - **Silent Startup Failure**: In `connector.py`'s `_poll_loop`, if a pool contract fails to resolve at startup, retry the resolution dynamically inside the polling loop on subsequent iterations.
   - **Data Loss on Log Fetch Failure**: In `connector.py`'s `_poll_loop`, do not unconditionally advance `self._last_block = current_block`. Only update `self._last_block` if log fetching and all queries for the current polling block range succeed without raising errors.
   - **Mypy strict mode failures**: Run `uv run mypy` to see the 67 type errors. Resolve all typing errors in the new files: `src/crypcodile/exchanges/base_onchain/connector.py`, `src/crypcodile/mcp_server.py`, `src/crypcodile/api_server.py`, `tests/exchanges/base_onchain/test_connector.py`, and `tests/exchanges/base_onchain/test_stress_challenger.py`. E.g., ensure `spec` dictionary values are type cast or asserted (e.g. `int(spec["decimals0"])`), annotate FastAPI route handlers properly, and resolve any union type checks.
   - **Recipient Wallet Address**: In `src/crypcodile/api_server.py`, the hardcoded `RECIPIENT_WALLET` is currently set to the USDC token contract itself (`0x833589fCD6eDb6E08f4c7C32D4f71b54bda02913`). Real payments would be permanently lost. Update this to a configurable environment variable `RECIPIENT_WALLET` (using a standard developer/user wallet address as a fallback, e.g. a valid mock wallet address like `0x70997970C51812dc3A010C7d01b50e0d17dc79C8`).
   - **Ruff Linting**: Format and fix formatting/lint errors in `tests/exchanges/base_onchain/test_stress_challenger.py` using `ruff check --fix` and line wrapping for lines exceeding 100 characters.
   - **Event Loop Thread Blocking**: Wrap synchronous blocking Web3/RPC queries (such as contract calls, log fetching, block height queries) inside `await asyncio.to_thread(...)` to prevent blocking the asyncio event loop thread.

3. Verify that:
   - `uv run pytest` passes successfully.
   - `uv run mypy` succeeds with zero errors.
   - `uv run ruff check .` succeeds with no warnings/errors.
   - `uv build` succeeds and builds the package distribution files.

4. Write `handoff.md` and report back when all tasks are complete and verified. Include the command outputs in your handoff.

MANDATORY INTEGRITY WARNING — include this verbatim in the Worker's dispatch prompt:
> DO NOT CHEAT. All implementations must be genuine. DO NOT
> hardcode test results, create dummy/facade implementations, or
> circumvent the intended task. A Forensic Auditor will independently
> verify your work. Integrity violations WILL be detected and your
> work WILL be rejected.
