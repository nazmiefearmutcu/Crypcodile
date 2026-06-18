# Milestone 3 Review Report

## Review Summary

**Verdict**: REQUEST_CHANGES (due to test suite failures/regressions in pagination logic, although the core normalization implementation for Milestone 3 is correct)

## Findings

### Major Finding 1: Broken Test Suite / Pagination Regressions
- **What**: Seven tests in the test suite are failing (including E2E boundary tests, adversarial pagination tests, and cursor rollback tests).
- **Where**:
  - `tests/e2e/test_tier2_boundaries.py::test_t2_huge_pagination_split`
  - `tests/exchanges/base_onchain/test_adversarial.py::test_pagination_extremely_large_range`
  - `tests/exchanges/base_onchain/test_adversarial.py::test_pagination_empty_range`
  - `tests/exchanges/base_onchain/test_challenger_m2_adversarial.py::test_pagination_extremely_large_range_chunking`
  - `tests/exchanges/base_onchain/test_challenger_m2_adversarial.py::test_pagination_error_loses_all_progress`
  - `tests/exchanges/base_onchain/test_challenger_m2_adversarial.py::test_pagination_invalid_range_negative`
  - `tests/exchanges/base_onchain/test_challenger_remediation_6.py::test_duplicate_log_query_bug`
- **Why**: The introduction of `overlap = 5` block pagination logic in `connector.py` (added to prevent duplicate log query bugs or handle re-orgs) shifts the starting query blocks backward by 5 blocks. This breaks the hardcoded assertions of the pagination and adversarial tests which expect exact chunk bounds and mock query counts without block overlap. Furthermore, the fix for incremental progress cursor updates breaks tests (like `test_pagination_error_loses_all_progress`) that specifically verify the losing of progress under error conditions.
- **Suggestion**: The tests in the test suite must be updated to align with the pagination overlap and incremental progress logic, or the connector's overlap logic must be parameterized or isolated so it doesn't break baseline pagination behavior.

### Minor Finding 2: Reserve Type Coercion is Boolean-permissive
- **What**: Reserves are coerced using `safe_float`, but boolean checks are not enforced (unlike price checks).
- **Where**: `src/crypcodile/exchanges/base_onchain/normalize.py`, lines 92-93.
- **Why**: `state.get("reserve0")` values of type `bool` (e.g. `True`) will be coerced to `1.0` by `safe_float` because `float(True) == 1.0` in Python. While this doesn't crash, it leads to incorrect mathematical computations.
- **Suggestion**: Add explicit `isinstance(reserve, bool)` checks to raise `TypeError` similar to the check implemented for price.

---

## Verified Claims

- **Depth-1 Facade Removal** → verified via manual review and unit tests (`test_normalize_depth.py`) → PASS. The normalizer now correctly generates 5 levels of bids and asks using loops for all paths.
- **Uniswap V3 Active Price Sizing & Scaling** → verified via mathematical trace and unit tests → PASS. The sizing math correctly uses active tick boundaries, scales token decimals ($10^{\text{decimals0}}$ / $10^{\text{decimals1}}$), and respects the flipping status of pools.
- **Aerodrome V2 Constant Product Math** → verified via mathematical trace and unit tests → PASS. Bids and asks are sized using mathematically sound constant-product formulas $x \cdot y = k$ and correctly map to the active base token reserve.
- **NaN / Inf Checks** → verified via unit tests → PASS. The price validation checks for NaN, Inf, and Neg Inf, discarding updates by returning early.
- **Parameter Coercion** → verified via unit tests → PASS. decimals and tick spacing default safely to standard values when `None` or missing.

---

## Coverage Gaps
- **Boolean Reserves Verification** — risk level: Low — recommendation: Accept risk or implement type check.

---

## Unverified Items
- None.

---

# Adversarial Challenge Report

## Challenge Summary

**Overall risk assessment**: MEDIUM

## Challenges

### Medium Challenge 1: Extreme Parameter Overflow in Decimals
- **Assumption challenged**: User-provided decimals are within standard bounds.
- **Attack scenario**: If a pool update contains an extremely large decimal count (e.g., `decimals0 = 100`), calculating $10^{\text{decimals0}}$ could cause `OverflowError` or massive resource consumption.
- **Blast radius**: Crashes the normalizer connector poll loop, leading to data loss or service denial.
- **Mitigation**: The worker correctly mitigated this by constraining decimals: `decimals0 = max(0, min(decimals0, 36))` in `normalize.py` lines 100-101. This is verified to be robust.

### Medium Challenge 2: Floating Point Underflow in Bid Price
- **Assumption challenged**: Bid price is always non-zero when calculating sizes.
- **Attack scenario**: If `bid_px` is extremely small (e.g., $10^{-300}$), dividing by it could cause division by zero or float overflow.
- **Blast radius**: Normalization crash if `bid_px` is zero.
- **Mitigation**: The implementation uses `if bid_px > 0 else 0.0` check on size calculation lines 181 and 187, and handles division safely.

## Stress Test Results

- **Zero / Negative / NaN / Inf Prices** → Discarded correctly → PASS.
- **Zero / Negative Reserves** → Capped safely to `0.0001` via `safe_cap` → PASS.
- **Extreme Decimals / Tick Spacings** → Constrained and coerced safely → PASS.

## Unchallenged Areas
- None.
