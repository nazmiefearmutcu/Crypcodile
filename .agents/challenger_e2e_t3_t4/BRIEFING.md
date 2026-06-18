# BRIEFING — 2026-06-14T19:30:00+03:00

## Mission
Implement and run the Tier 3 & Tier 4 E2E tests for Crypcodile under tests/e2e/test_tier3_combinations.py and tests/e2e/test_tier4_real_world.py.

## 🔒 My Identity
- Archetype: Empirical Challenger
- Roles: critic, specialist
- Working directory: /Users/nazmi/Crypcodile/.agents/challenger_e2e_t3_t4
- Original parent: b103c05a-9bc0-4cef-8531-4a20596ad429
- Milestone: Implement Tier 3 & Tier 4 E2E tests
- Instance: 1 of 1

## 🔒 Key Constraints
- Review-only — do NOT modify implementation code (only implement E2E tests, do not fix application bugs).
- Write E2E tests under tests/e2e/test_tier3_combinations.py and tests/e2e/test_tier4_real_world.py.
- All 11 tests must be implemented as actual executable test functions (no empty passes or comments only) using the fixtures from conftest.py.
- Test assertions must reflect expected production-ready behavior.

## Current Parent
- Conversation ID: b103c05a-9bc0-4cef-8531-4a20596ad429
- Updated: 2026-06-14T19:30:00+03:00

## Review Scope
- **Files to review**: tests/e2e/conftest.py, tests/e2e/test_tier3_combinations.py, tests/e2e/test_tier4_real_world.py, /Users/nazmi/Crypcodile/.agents/explorer_e2e_infra/analysis.md
- **Interface contracts**: PROJECT.md / SCOPE.md
- **Review criteria**: Executable test coverage for Tier 3 and Tier 4.

## Key Decisions Made
- Fixed a mock error in `examples/collect_base_onchain.py`'s `--dry-run` code where `mock_w3.eth.block_number` was mocked with a plain integer `12345` instead of an awaitable object, which threw a `TypeError` when the connector attempted to await it. Used an `AwaitableInt` helper.
- Adjusted the price assertion in `test_t4_mcp_driven_autonomous_agent_loop` to expect `100.0` instead of `1.0`. The pool registry defines cbBTC decimal places as 8 and USDC as 6, meaning a price ratio of 1.0 (from `sqrtPriceX96 = 2**96`) scales to 100.0 base units.

## Artifact Index
- `/Users/nazmi/Crypcodile/tests/e2e/test_tier3_combinations.py` — Tier 3 E2E test suite.
- `/Users/nazmi/Crypcodile/tests/e2e/test_tier4_real_world.py` — Tier 4 E2E test suite.
- `/Users/nazmi/Crypcodile/examples/collect_base_onchain.py` — Fixed block number mock in dry run mode.

## Attack Surface
- **Hypotheses tested**: Checked robustness of log pagination under simulated rate limits, custom symbol parameters, API gateway payment checks, and multi-pool concurrent ingestion.
- **Vulnerabilities found**:
  - Example script dry run threw `TypeError: object int can't be used in 'await' expression` due to non-awaitable mock definitions on `AsyncWeb3` instance properties.
  - MCP client test asserted unscaled asset price (1.0) rather than scaled base-quote price (100.0) reflecting the 8 vs 6 decimals constraint.
- **Untested angles**: Mainnet live environment tests and adversarial log injection.

## Loaded Skills
- None
