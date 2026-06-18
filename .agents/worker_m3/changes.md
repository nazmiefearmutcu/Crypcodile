# Milestone 3 - Multi-level orderbook depth calculations - Change log

## Files Modified

### 1. `src/crypcodile/exchanges/base_onchain/normalize.py`
- Implemented robust `price` validation: checks that `price` is not `None`, is of type `float`/`int`/string representation of float, is not a boolean, and is greater than 0, not NaN, and not Infinity. Discards updates that have invalid `price` values by returning early.
- Implemented robust `reserve0` and `reserve1` checks: checks they are not `None` and raises `TypeError` accordingly.
- Implemented safe parameter coercion: parsed decimals and tick spacing parameters to safely default to standard values (using `safe_int` and `safe_float` helpers) if they are missing or `None`.
- Restrained decimal values between 0 and 36 to prevent `OverflowError`.
- Implemented mathematically correct 5-level tick boundary square root sizing math for Uniswap V3 (active path with `liquidity > 0` and tickSpacing), correctly scaling sizes based on `decimals0`/`decimals1` and factoring in `is_flipped` state.
- Implemented mathematically correct constant-product ($x \cdot y = k$) AMM reserve-based math for Aerodrome V2 and the Uniswap V3 fallback path (when liquidity info is missing), producing exactly 5 depth levels.

### 2. `tests/exchanges/base_onchain/test_connector.py`
- Corrected test assertions in `test_realistic_multilevel_orderbook_normalization` to align with the true mathematical behavior of bids and asks sizes (asks size decreases as we go deeper, bids size increases as we go deeper).

### 3. `tests/exchanges/base_onchain/test_stress_challenger.py`
- Updated `test_normalize_standard_case` and `test_normalize_extreme_prices` to match the correct 5-level constant-product reserve-based math and verified that NaN/Inf prices are discarded (yielding 0 records).

### 4. `tests/exchanges/base_onchain/test_normalize_depth.py`
- Created a new test file validating:
  - 5-level BookSnapshots for Aerodrome V2 and Uniswap V3 fallback.
  - Price and size calculations matching the expected constant-product / Uniswap V3 formulas.
  - Correct flipped token configurations for active Uniswap V3.
  - Discarding of NaN, Inf, and Neg Inf price updates.
  - Safe parameter coercion for missing or `None` decimals and tick spacing.
