## 2026-06-14T14:23:52Z

You are a teamwork_preview_worker.
Your role: Formatting Developer
Your working directory: /Users/nazmi/Crypcodile/.agents/teamwork_preview_worker_m1_m4_3

Please perform the following tasks:
1. Initialize your progress.md under your working directory.
2. Fix all Ruff lint errors in:
   - `tests/exchanges/base_onchain/test_challenger_stress_2.py`
   - `tests/exchanges/base_onchain/test_challenger_stress_3.py`
   Run `uv run ruff check --fix .` to auto-fix and manually wrap any long lines exceeding 100 characters.
3. Verify that:
   - `uv run ruff check .` passes with zero issues.
   - `uv run pytest` passes successfully.
   - `uv run mypy src/crypcodile/exchanges/base_onchain/connector.py src/crypcodile/mcp_server.py src/crypcodile/api_server.py tests/exchanges/base_onchain/test_connector.py tests/exchanges/base_onchain/test_stress_challenger.py` passes with no issues.
4. Write `handoff.md` and report back when complete.

MANDATORY INTEGRITY WARNING — include this verbatim in the Worker's dispatch prompt:
> DO NOT CHEAT. All implementations must be genuine. DO NOT
> hardcode test results, create dummy/facade implementations, or
> circumvent the intended task. A Forensic Auditor will independently
> verify your work. Integrity violations WILL be detected and your
> work WILL be rejected.
