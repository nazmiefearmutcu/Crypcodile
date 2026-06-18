# Review Report — Milestone 3

## Review Summary

**Verdict**: APPROVE

The Milestone 3 implementation in `src/crypcodile/exchanges/base_onchain/normalize.py` has been thoroughly reviewed and tested. The changes correctly and robustly resolve all orderbook depth issues (depth-1 facade removal, Uniswap V3 active price scaling, Aerodrome V2 cpmm math, NaN/Inf checks, coercion) without introducing regressions. All 754 tests in the test suite pass cleanly when the cache is cleared, verifying both unit logic and full E2E/adversarial setups.

---

## Findings

### [Minor] Robustness Edge Case: Unwrapped Exponentiation overflow for extreme ticks

- **What**: Exponentiation calculations (`1.0001 ** (ask_t1 / 2.0)`) do not have direct try-except wrapper blocks.
- **Where**: `src/crypcodile/exchanges/base_onchain/normalize.py`, lines 172-175.
- **Why**: If a malicious or corrupted input contains an extremely large tick value in `state["tick"]` (e.g. `1e9`), `1.0001 ** (tick / 2.0)` will raise an `OverflowError` and crash the normalizer.
- **Suggestion**: Wrap the tick exponentiation logic in a try-except block to default to `0.0` or log a warning and return early if an `OverflowError` occurs.

---

## Verified Claims

- **Depth-1 Facade Removal** → verified via inspecting output structure in `tests/exchanges/base_onchain/test_normalize_depth.py` and checking that exactly 5 levels are produced. → **PASS**
- **Uniswap V3 Active Price Scaling** → verified via checking mathematical formulas in active Uniswap V3 code paths (flipped and unflipped config) and validating the decimal scaling. → **PASS**
- **Aerodrome V2 CPMM Math** → verified by matching the Constant Product Market Maker formulas with bids/asks sizes and prices at all 5 levels. → **PASS**
- **NaN/Inf Price Checks** → verified via stress testing inputs containing `float('nan')`, `float('inf')`, and negative numbers, ensuring they are discarded early. → **PASS**
- **Decimals/Tick-spacing Coercion** → verified by testing updates with missing or `None` values, confirming safe defaults (8/18 decimals, 10 tick spacing) are used. → **PASS**
- **No Regressions** → verified by running the entire project test suite via `uv run pytest --cache-clear` and confirming 754/754 tests pass. → **PASS**

---

## Coverage Gaps

- No significant coverage gaps. The added tests in `test_normalize_depth.py` cover active Uniswap V3, fallback CPMM, flipped configs, parameters coercion, and extreme price inputs.

---

## Unverified Items

- None. All key claims and behaviors were verified.
