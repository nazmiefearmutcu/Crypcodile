# BRIEFING — 2026-06-15T00:40:42+03:00

## Mission
Conduct adversarial testing and stress testing on the Milestone 3 orderbook math implementation in `src/crypcodile/exchanges/base_onchain/normalize.py`.

## 🔒 My Identity
- Archetype: Challenger
- Roles: critic, specialist
- Working directory: /Users/nazmi/Crypcodile/.agents/challenger_m3_1
- Original parent: 5c0b98bd-4196-4f15-b3fa-8228abff7342
- Milestone: Milestone 3
- Instance: 1 of 1

## 🔒 Key Constraints
- Review-only — do NOT modify implementation code
- Report findings and test execution results in `/Users/nazmi/Crypcodile/.agents/challenger_m3_1/handoff.md`

## Current Parent
- Conversation ID: 5c0b98bd-4196-4f15-b3fa-8228abff7342
- Updated: 2026-06-15T00:42:50+03:00

## Review Scope
- **Files to review**: `src/crypcodile/exchanges/base_onchain/normalize.py`
- **Interface contracts**: `PROJECT.md`
- **Review criteria**: extreme prices/reserves, flipped pool decimals, negative/zero tick spacing, exactly 5 depth levels.

## Key Decisions Made
- Added a robust unit test suite (`test_normalize_adversarial.py`) addressing extreme float/integer conversions, underflow/overflow bounds, zero/negative configurations, flipped pool setup, and depth constraints.
- Confirmed uncaught math `OverflowError` under specific underflow-tick fallback conditions.
- Confirmed propagation of `inf` values when `liquidity = inf`.

## Artifact Index
- `/Users/nazmi/Crypcodile/tests/exchanges/base_onchain/test_normalize_adversarial.py` — Adversarial testing suite.
- `/Users/nazmi/Crypcodile/.agents/challenger_m3_1/handoff.md` — Findings and Handoff Report.

## Attack Surface
- **Hypotheses tested**: Checked behavior of floats in place of integers, price/reserve underflows and overflows, zero/negative tick spacing, flipped decimals, and depth constraints.
- **Vulnerabilities found**: (1) Uncaught `OverflowError` during level calculation if a very large tick is used on active V3 path under price-ratio underflow. (2) Non-finite `inf` value propagation in size math. (3) `is_flipped` string parsing pitfall.
- **Untested angles**: None.

## Loaded Skills
- None
