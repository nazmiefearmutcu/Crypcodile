# Progress - worker_m3

Last visited: 2026-06-15T00:41:00Z

## Status
- [x] Implement robust NaN/Inf/invalid checks on price in `src/crypcodile/exchanges/base_onchain/normalize.py`.
- [x] Coerce parameter types safely (handling `None` or missing decimals/tick spacing parameters).
- [x] Implement correct 5-level Uniswap V3 depth calculations (using active tick, square root of price ratio tick boundaries, `is_flipped` factoring, and decimal scaling).
- [x] Implement correct 5-level constant-product reserve-based depth calculations (using constant product $x \cdot y = k$ formula) for Aerodrome V2 and the Uniswap V3 fallback path.
- [x] Update existing tests in `test_stress_challenger.py` to assert that NaN/Inf price records are discarded.
- [x] Create `tests/exchanges/base_onchain/test_normalize_depth.py` to thoroughly test all cases (5 levels, mathematical formulas, flipped setup, NaN/Inf handling, parameter coercion).
- [x] Run test suite to verify everything passes.
- [x] Document changes in `changes.md` and handoff report in `handoff.md`.
