# BRIEFING — 2026-06-14T21:42:00Z

## Mission
Investigate Milestone 3 (Multi-level orderbook depth calculations) in src/crypcodile/exchanges/base_onchain/normalize.py and related tests for Uniswap V3 and Aerodrome V2 to ensure correctness, completeness, and robustness.

## 🔒 My Identity
- Archetype: Explorer
- Roles: Read-only investigator, Analyzer
- Working directory: /Users/nazmi/Crypcodile/.agents/explorer_m3_1
- Original parent: 5c0b98bd-4196-4f15-b3fa-8228abff7342
- Milestone: Milestone 3

## 🔒 Key Constraints
- Read-only investigation — do NOT implement.
- Write analysis to `/Users/nazmi/Crypcodile/.agents/explorer_m3_1/analysis.md`.
- CODE_ONLY network mode: no external access, no curl/wget/etc. to external URLs.

## Current Parent
- Conversation ID: 5c0b98bd-4196-4f15-b3fa-8228abff7342
- Updated: 2026-06-14T21:42:00Z

## Investigation State
- **Explored paths**:
  - `src/crypcodile/exchanges/base_onchain/normalize.py`
  - `src/crypcodile/exchanges/base_onchain/connector.py`
  - `tests/exchanges/base_onchain/test_stress_challenger.py`
  - `tests/exchanges/base_onchain/test_challenger_stress_2.py`
  - `tests/exchanges/base_onchain/test_challenger_stress_3.py`
- **Key findings**:
  - **Depth-1 Facade**: Found in the Uniswap V3 fallback path (`"liquidity" not in state`), which yields only 1 level.
  - **Wrong Uniswap V3 Sizes**: Active Uniswap V3 path ignores `sqrt_p_raw` and `is_flipped` when computing `base_sz` from `liquidity`, resulting in sizes off by up to 100,000x.
  - **Unrealistic Aerodrome V2 Sizes**: Aerodrome V2 sizes represent a massive overestimation (~45.7% of reserves instead of ~0.125%).
  - **Testing Gap**: No tests currently verify the correct 5-level depth values.
- **Unexplored areas**: None.

## Key Decisions Made
- Completed analysis of normalizer mathematical formulas and testing coverage.

## Artifact Index
- /Users/nazmi/Crypcodile/.agents/explorer_m3_1/analysis.md — structured report of findings on Milestone 3 correctness, completeness, and robustness.
