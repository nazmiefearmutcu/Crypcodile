# BRIEFING — 2026-06-14T21:36:30Z

## Mission
Investigate Milestone 3 (Multi-level orderbook depth calculations) in Base onchain exchanges module and verify if correct, complete, and robust.

## 🔒 My Identity
- Archetype: explorer
- Roles: Teamwork explorer
- Working directory: /Users/nazmi/Crypcodile/.agents/explorer_m3_3
- Original parent: 5c0b98bd-4196-4f15-b3fa-8228abff7342
- Milestone: Milestone 3 (Multi-level orderbook depth calculations)

## 🔒 Key Constraints
- Read-only investigation — do NOT implement
- Analyze correctness, completeness, and robustness of Milestone 3
- Check if it calculates 5 bids and 5 asks for Uniswap V3 and Aerodrome V2
- Check for depth=1 facade in fallback or main paths
- Verify price/size math accuracy and realism at tick/liquidity levels
- Identify gaps/bugs and recommend fix strategy

## Current Parent
- Conversation ID: 5c0b98bd-4196-4f15-b3fa-8228abff7342
- Updated: 2026-06-14T21:36:30Z

## Investigation State
- **Explored paths**:
  - `src/crypcodile/exchanges/base_onchain/normalize.py`
  - `src/crypcodile/exchanges/base_onchain/connector.py`
  - `tests/exchanges/base_onchain/test_connector.py`
  - `tests/exchanges/base_onchain/test_stress_challenger.py`
  - `tests/exchanges/base_onchain/test_challenger_stress_3.py`
  - `tests/exchanges/base_onchain/test_challenger_stress_2.py`
- **Key findings**:
  - **Uniswap V3 Fallback Facade**: The fallback path in `normalize.py` only calculates a single level of bids and asks (depth=1 facade), violating the interface contract of at least 5 levels.
  - **Inaccurate V3 Size Math**: The V3 size calculation (`base_sz = liquidity / 10**decimals0`) is mathematically incorrect. It ignores price scaling and tick ranges, resulting in massive scaling errors (up to 7 orders of magnitude, e.g., 200 Billion USDC for a 30,000 USDC pool) when `is_flipped = True` and decimals differ.
  - **Latent Flipped Tick Fallback Bug**: Flipped V3 tick-price math works by double-inversion canceling out, but if the code falls back to the contract tick (when `price_ratio` fails), calculated prices are wrong by a factor of $10^{2(decimals0 - decimals1)}$.
  - **Robust Aerodrome V2 Math**: Aerodrome V2 orderbook depth is robust, calculating 5 levels using linear spreads and scaling sizes correctly as fractions of the pool's reserves.
- **Unexplored areas**: None.

## Key Decisions Made
- Confirmed mathematical and design errors in normalizer depth math.
- Formulated recommended fix strategies:
  1. Remove Uniswap V3 fallback's depth=1 facade by generating 5 levels of bids/asks using the reserve-based heuristic when liquidity info is missing.
  2. Implement proper Uniswap V3 liquidity-to-size tick range depth math or adapt a reserve-bounded heuristic.
  3. Ensure tick fallback is correct by decoupling it from double inversion.

## Artifact Index
- /Users/nazmi/Crypcodile/.agents/explorer_m3_3/ORIGINAL_REQUEST.md — Original user request
- /Users/nazmi/Crypcodile/.agents/explorer_m3_3/BRIEFING.md — Briefing file
- /Users/nazmi/Crypcodile/.agents/explorer_m3_3/progress.md — Progress tracker
- /Users/nazmi/Crypcodile/.agents/explorer_m3_3/analysis.md — Main analysis report
