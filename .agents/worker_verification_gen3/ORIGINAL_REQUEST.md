## 2026-06-14T21:11:33Z
You are a verification worker. Your working directory is `/Users/nazmi/Crypcodile/.agents/worker_verification_gen3`.
Your identity is `teamwork_preview_worker`.

**Objective**: Run all repository verifications to confirm that the existing implementation and E2E/unit tests are fully functional, typed, clean of lint errors, package builds successfully, and the showcase script works.

**Scope boundaries**:
- Do NOT modify any source code files.
- ONLY run test, build, lint, type checks, and showcase script dry-run.

**Input Information**:
- Workspace root: `/Users/nazmi/Crypcodile`
- Config files: `pyproject.toml`
- Core code layout described in `/Users/nazmi/Crypcodile/PROJECT.md`.

**MANDATORY INTEGRITY WARNING**:
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A Forensic Auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

**Output Requirements**:
- Run `uv run pytest` and record command output and results.
- Run `uv run ruff check` and record output.
- Run `uv run mypy .` and record output.
- Run `uv build` and record output.
- Run `uv run python examples/collect_base_onchain.py --dry-run` and record output.
- Save the results as a report at `/Users/nazmi/Crypcodile/.agents/worker_verification_gen3/verification_report.md`.

**Completion Criteria**:
- All tests pass (pytest exits with code 0).
- Ruff check passes (exits with code 0).
- MyPy check passes (exits with code 0).
- Build succeeds (exits with code 0).
- Showcase script executes successfully in dry-run mode.
- Handoff report is written and you send a completion message.
