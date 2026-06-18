# Handoff Report: Adversarial and Stress Testing of Milestone 3 Orderbook Math

This report details the adversarial and stress-testing verification of the orderbook normalization math in `src/crypcodile/exchanges/base_onchain/normalize.py`.

---

## 1. Observation

- **Normalization Math File**: `src/crypcodile/exchanges/base_onchain/normalize.py`
- **Adversarial Test Suite Location**: `tests/exchanges/base_onchain/test_normalize_adversarial.py`
- **Test Command Run**: `uv run pytest tests/exchanges/base_onchain/test_normalize_adversarial.py`
  - Output: `11 passed in 0.02s`
- **Full Test Suite Run**: `uv run pytest`
  - Output: `1 failed, 753 passed, 36 warnings in 38.84s`
  - Verbatim error from `test_t2_huge_pagination_split` in `tests/e2e/test_tier2_boundaries.py`:
    ```
    FAILED tests/e2e/test_tier2_boundaries.py::test_t2_huge_pagination_split - AssertionError: assert 6 == 3
    ```

---

## 2. Logic Chain

1. **Uncaught OverflowError in tick math**:
   - In `normalize.py`, the active path level loop performs:
     ```python
     sqrt_ask1 = 1.0001 ** (ask_t1 / 2.0)
     ```
     without wrapping it in a `try...except` block.
   - If `price_ratio` underflows to `0.0` (e.g. extremely small price `1e-300` and large `decimals0 = 36`), `normalize.py` falls back to the `state.get("tick")` field.
   - When `state["tick"]` is passed as a large number (e.g., `1e9`), `1.0001 ** (ask_t1 / 2.0)` overflows, raising `OverflowError: (34, 'Result too large')`, which propagates and crashes the program. We confirmed this behavior empirically via `test_tick_overflow_raises_error`.

2. **NaN and Infinity Liquidity Propagation**:
   - In `normalize.py`, `use_active_v3` is activated when `liquidity > 0`.
   - If `liquidity = float('inf')`, the comparison `float('inf') > 0` is `True`, activating the V3 path and propagating infinite sizes to `BookSnapshot`.
   - If `liquidity = float('nan')`, `float('nan') > 0` is `False`, falling back to the CP (Constant Product) path. This handles `nan` by avoiding it, but relies on reserves. We verified both behaviors via `test_nan_inf_liquidity`.

3. **String Truthiness Pitfall for `is_flipped`**:
   - `bool(state.get("is_flipped", False))` is used to parse configuration.
   - If a string configuration is supplied (e.g., `"is_flipped": "False"`), `bool("False")` evaluates to `True`, which can result in incorrectly inverted pricing.

4. **Depth Constraint Asserted**:
   - Both active and fallback paths generate exactly 5 levels. We validated this constraint through `test_depth_is_exactly_5`.

---

## 3. Caveats

- The E2E test failure `test_t2_huge_pagination_split` is caused by `eth_getLogs` pagination counting differences in `BaseOnchainTransport` and does not affect the correctness of the mathematical normalization engine itself.
- We did not alter any implementation code, adhering to the review-only constraint.

---

## 4. Conclusion

- The Milestone 3 normalizer math handles positive/negative/NaN prices safely by discarding them.
- Depth is consistently constrained to exactly 5 levels.
- However, two main mathematical bugs were identified:
  1. An uncaught `OverflowError` crashes the function when a huge tick is present in the state payload under underflow conditions.
  2. Active V3 path does not check `math.isfinite(liquidity)`, permitting `inf` values to propagate into depth snapshot sizes.

---

## 5. Verification Method

To verify the test execution:
1. Run:
   ```bash
   uv run pytest tests/exchanges/base_onchain/test_normalize_adversarial.py
   ```
   All 11 tests must pass, verifying the edge cases.
2. Inspect `tests/exchanges/base_onchain/test_normalize_adversarial.py` to confirm the exact test scenarios implemented.

---

## Adversarial Review Report

### Challenge Summary
**Overall risk assessment**: MEDIUM

### Challenges

#### [High] Challenge 1: Uncaught OverflowError in tick math
- **Assumption challenged**: Floating point operations are assumed to remain within normal bounds.
- **Attack scenario**: Underflow of `price_ratio` triggers fallback to `state["tick"]`. If this value is exceptionally large (e.g. `1e9`), `1.0001 ** (tick / 2)` raises an uncaught `OverflowError`.
- **Blast radius**: Process crashes completely.
- **Mitigation**: Wrap the calculation inside the loop with `try...except OverflowError` or cap tick values.

#### [Medium] Challenge 2: Infinite size propagation
- **Assumption challenged**: Liquidity values are assumed to be finite.
- **Attack scenario**: `liquidity = float('inf')` activates active path and propagates `inf` to snapshot sizes.
- **Blast radius**: Downstream parsers/sinks may reject non-finite numbers.
- **Mitigation**: Assert `math.isfinite(liquidity)`.

#### [Low] Challenge 3: Insecure parsing of `is_flipped`
- **Assumption challenged**: String `"False"` in JSON is assumed to translate to `False`.
- **Attack scenario**: `bool("False")` evaluates to `True`.
- **Blast radius**: Flipped pools normalized incorrectly.
- **Mitigation**: Use safer string-to-bool logic.

### Stress Test Results

- Price = `0` → returns nothing → PASS
- Price = `NaN` or `Inf` → returns nothing → PASS
- Price = `None`/`True` → raises TypeError → PASS
- Decimal differences (`dec_diff = 30`) + Price underflow + Huge tick → raises OverflowError → PASS
- Float values for `decimals` and `tickSpacing` → coerced successfully → PASS
- Negative/Zero tick spacing → coerced to `1` → PASS
- Flipped decimals config → runs successfully → PASS
- Snapshot depth → exactly 5 levels → PASS
