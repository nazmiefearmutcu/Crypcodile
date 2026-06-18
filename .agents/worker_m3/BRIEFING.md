# BRIEFING — 2026-06-15T00:37:00Z

## Mission
Implement Milestone 3 (Multi-level orderbook depth calculations) in `src/crypcodile/exchanges/base_onchain/normalize.py` with mathematically correct Uniswap V3 and constant-product formulas, and add comprehensive unit tests.

## 🔒 My Identity
- Archetype: worker
- Roles: implementer, qa, specialist
- Working directory: /Users/nazmi/Crypcodile/.agents/worker_m3
- Original parent: 5c0b98bd-4196-4f15-b3fa-8228abff7342
- Milestone: Milestone 3 - Multi-level orderbook depth calculations

## 🔒 Key Constraints
- Mathematically correct 5-level orderbook depth calculations for Uniswap V3 (with active liquidity) scaling sizes using square root of prices at tick boundaries, factoring in `is_flipped` state and decimals.
- Constant product AMM ($x \cdot y = k$) formulas for Aerodrome V2 and Uniswap V3 fallback path (no liquidity information). Ensure the fallback path has 5 levels.
- Robust NaN and Inf checks on incoming prices (must be > 0 and not NaN/Inf).
- Parameter coercion support (handling None/missing decimals/tick spacing).
- Comprehensive unit tests in `tests/exchanges/base_onchain/test_normalize_depth.py`.
- No cheating: no hardcoded test results or dummy/facade implementations.
- Write updates to `progress.md` after completing each step.
- Document changes in `changes.md` and handoff report in `handoff.md`.

## Current Parent
- Conversation ID: 5c0b98bd-4196-4f15-b3fa-8228abff7342
- Updated: not yet

## Task Summary
- **What to build**: Multi-level orderbook depth calculations (5 levels) for Uniswap V3 and Aerodrome V2/fallback using exact mathematical formulas.
- **Success criteria**: BookSnapshots have exactly 5 levels. Prices and sizes are correct and match the expected constant-product / Uniswap V3 formulas. Flipped token setups work correctly. NaN/Inf prices are discarded. All tests pass.
- **Interface contracts**: `src/crypcodile/exchanges/base_onchain/normalize.py`
- **Code layout**: Source in `src/crypcodile/exchanges/base_onchain/`, tests in `tests/exchanges/base_onchain/`.

## Key Decisions Made
- Used the true constant product AMM formula for reserve-based pools (Aerodrome V2 and Uniswap V3 fallback).
- Swapped decimals appropriately for flipped setups in active Uniswap V3 calculations.
- Discarded updates with non-positive, NaN, or Inf prices by early returning.
- Safely coerced missing and None decimals and tick spacing parameters to defaults.

## Change Tracker
- **Files modified**:
  - `src/crypcodile/exchanges/base_onchain/normalize.py`
  - `tests/exchanges/base_onchain/test_connector.py`
  - `tests/exchanges/base_onchain/test_stress_challenger.py`
  - `tests/exchanges/base_onchain/test_normalize_depth.py`
- **Build status**: Pass
- **Pending issues**: None

## Quality Status
- **Build/test result**: Pass (735/735 passed)
- **Lint status**: Ruff check and Mypy strict type checking pass with success
- **Tests added/modified**: `tests/exchanges/base_onchain/test_normalize_depth.py` added

## Loaded Skills
- None

## Artifact Index
- `/Users/nazmi/Crypcodile/.agents/worker_m3/ORIGINAL_REQUEST.md` — Original request
- `/Users/nazmi/Crypcodile/.agents/worker_m3/BRIEFING.md` — Current briefing index
- `/Users/nazmi/Crypcodile/.agents/worker_m3/progress.md` — Progress tracking
- `/Users/nazmi/Crypcodile/.agents/worker_m3/changes.md` — Documented changes
- `/Users/nazmi/Crypcodile/.agents/worker_m3/handoff.md` — 5-component handoff report
