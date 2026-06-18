# Verification Plan: Crypcodile Production-Ready Base Integration

This plan outlines the final verification checks required to ensure the Crypcodile repository's transition to a production-ready Base integration is robust and complete.

## Verification Steps

### Step 1: Run Full Test Suite
- **Task**: Execute the complete test suite (unit + E2E tests) to ensure all tests pass.
- **Verification**: Run `uv run pytest`. Expected output is 100% passing tests (approx 630 unit tests + 74 E2E tests).
- **Agent**: `teamwork_preview_worker`

### Step 2: Static Analysis & Lint Checks
- **Task**: Run linter and type checker checks across the codebase.
- **Verification**: Run `uv run ruff check` and `uv run mypy`. Expected output is clean results.
- **Agent**: `teamwork_preview_worker`

### Step 3: Build Package Verification
- **Task**: Run package build to confirm it compiles and generates distribution packages cleanly.
- **Verification**: Run `uv build`. Expected output is successful generation of source distribution (.tar.gz) and wheel (.whl) in the `dist/` directory.
- **Agent**: `teamwork_preview_worker`

### Step 4: Run Showcase Script Verification
- **Task**: Execute the showcase script with dry-run configuration.
- **Verification**: Run `uv run python examples/collect_base_onchain.py --dry-run`. Expected output is a clean exit with mock outputs printed.
- **Agent**: `teamwork_preview_worker`

### Step 5: Perform Forensic Integrity Audit
- **Task**: Perform a forensic integrity check to ensure no test results are hardcoded, and the implementation is authentic.
- **Verification**: Run the audit checks. Expected output is a clean verdict.
- **Agent**: `teamwork_preview_auditor`

### Step 6: Final Handoff & Reporting
- **Task**: Write the final handoff report to `handoff.md` and send the victory claimed message to Sentinel.
- **Verification**: Verify that `handoff.md` is present and send the message.
- **Agent**: `teamwork_preview_orchestrator` (Self)
