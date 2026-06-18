# BRIEFING — 2026-06-14T19:33:00+03:00

## Mission
Verify the correctness, completeness, robustness, and conformance of Milestones 1 to 5 for the Crypcodile base mainnet integration, run builds/tests, and document the results.

## 🔒 My Identity
- Archetype: reviewer_final_2
- Roles: reviewer, critic
- Working directory: /Users/nazmi/Crypcodile/.agents/teamwork_preview_reviewer_final_2
- Original parent: 7e00f01b-95d5-4b76-b3a6-109e3b140b79
- Milestone: Final Review (Milestones 1-5)
- Instance: 1 of 1

## 🔒 Key Constraints
- Review-only — do NOT modify implementation code.
- Strictly check for integrity violations (hardcoded test results, dummy/facade implementations, shortcuts, fabricated verification outputs).
- Run `uv build` and `uv run pytest`.
- Produce detailed handoff report `handoff.md` with the 5 required sections.

## Current Parent
- Conversation ID: 7e00f01b-95d5-4b76-b3a6-109e3b140b79
- Updated: not yet

## Review Scope
- **Files to review**: Base mainnet integration files: `src/crypcodile/exchanges/base_onchain/connector.py`, `src/crypcodile/exchanges/base_onchain/normalize.py`, `src/crypcodile/mcp_server.py`, `src/crypcodile/api_server.py`, `src/crypcodile/cli.py`, and the full E2E test suite under `tests/e2e/`.
- **Interface contracts**: `PROJECT.md`
- **Review criteria**: Correctness, AsyncWeb3 refactoring, retry_rpc backoff retries, Uniswap V3 / Aerodrome V2 orderbook calculations, USDC payment log validation, custom pool configs.

## Review Checklist
- **Items reviewed**: none yet
- **Verdict**: pending
- **Unverified claims**: none yet

## Attack Surface
- **Hypotheses tested**: none yet
- **Vulnerabilities found**: none yet
- **Untested angles**: none yet

## Key Decisions Made
- [TBD]

## Artifact Index
- `/Users/nazmi/Crypcodile/.agents/teamwork_preview_reviewer_final_2/ORIGINAL_REQUEST.md` — Original request content
- `/Users/nazmi/Crypcodile/.agents/teamwork_preview_reviewer_final_2/BRIEFING.md` — This briefing document
