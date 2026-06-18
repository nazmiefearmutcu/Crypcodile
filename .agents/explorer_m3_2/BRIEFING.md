# BRIEFING — 2026-06-14T21:35:30Z

## Mission
Investigate Milestone 3 (Multi-level orderbook depth calculations) in `normalize.py` and related tests to verify correctness, completeness, and robustness for Uniswap V3 and Aerodrome V2.

## 🔒 My Identity
- Archetype: Teamwork Explorer
- Roles: read-only investigator, analyzer
- Working directory: /Users/nazmi/Crypcodile/.agents/explorer_m3_2
- Original parent: 5c0b98bd-4196-4f15-b3fa-8228abff7342
- Milestone: Milestone 3

## 🔒 Key Constraints
- Read-only investigation — do NOT implement
- CODE_ONLY network mode: no external requests, no curl/wget targeting external URLs.
- Write analysis to `/Users/nazmi/Crypcodile/.agents/explorer_m3_2/analysis.md`
- Write handoff to `/Users/nazmi/Crypcodile/.agents/explorer_m3_2/handoff.md`

## Current Parent
- Conversation ID: 5c0b98bd-4196-4f15-b3fa-8228abff7342
- Updated: 2026-06-14T21:36:40Z

## Investigation State
- **Explored paths**: `PROJECT.md`, `.agents/sub_orch_implementation_gen3/SCOPE.md`, `src/crypcodile/exchanges/base_onchain/normalize.py`, `src/crypcodile/exchanges/base_onchain/connector.py`, `tests/exchanges/base_onchain/test_connector.py`, `tests/exchanges/base_onchain/test_stress_challenger.py`, `tests/e2e/test_tier2_boundaries.py`
- **Key findings**: 
  - Uniswap V3 fallback is a depth=1 facade when liquidity is missing, violating the 5-level snapshot contract.
  - Uniswap V3 size calculation ignores the price/sqrt(price) factor, leading to ~22x size errors for assets like cbBTC.
  - Level size scaling via `base_sz / (5.0 * i)` (for Uniswap V3) and `reserve / (5.0 * i)` (for Aerodrome V2) is highly unrealistic, inflating the actual pool reserves/liquidity in tick ranges by 800x to 10,000x.
- **Unexplored areas**: None, the core normalization logic and math verification are fully explored.

## Key Decisions Made
- Performed detailed manual trace and implemented temporary scratch mathematical verifications to calculate exact Uniswap V3 and Aerodrome V2 CPMM reserves/sizes in respective price ranges.
- Formulated the exact delta-reserve mathematical models for Uniswap V3 (standard and flipped) and Aerodrome V2 to serve as the remediation blueprint.

## Artifact Index
- /Users/nazmi/Crypcodile/.agents/explorer_m3_2/ORIGINAL_REQUEST.md — Original request and parent status messages
- /Users/nazmi/Crypcodile/.agents/explorer_m3_2/BRIEFING.md — My working memory
- /Users/nazmi/Crypcodile/.agents/explorer_m3_2/analysis.md — The detailed orderbook calculations analysis report
- /Users/nazmi/Crypcodile/.agents/explorer_m3_2/handoff.md — The 5-component handoff report for the next agent

