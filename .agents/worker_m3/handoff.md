# Handoff Report — Milestone 3

## 1. Observation
- **Original Source File**: `src/crypcodile/exchanges/base_onchain/normalize.py`
  - Heuristic size calculations previously used division by `5.0 * i` (line 111-112).
  - Uniswap V3 fallback path returned only a single level of bids and asks (line 120-136).
  - Prices were checked as `if price <= 0:` without checking for NaN or Inf (line 56-57).
  - Missing type checks on `price` and `reserves` resulted in unhandled exceptions or invalid calculations.
- **Project Test Execution**:
  - Test command `.venv/bin/pytest` was executed in the workspace and completed successfully:
    ```
    735 passed, 36 warnings in 37.40s
    ```
  - Specific unit test files:
    - `tests/exchanges/base_onchain/test_normalize_depth.py` (added).
    - `tests/exchanges/base_onchain/test_stress_challenger.py` (modified).
    - `tests/exchanges/base_onchain/test_connector.py` (modified).

## 2. Logic Chain
- **Step 1**: To resolve the depth-1 facade and incorrect sizes for Aerodrome V2 and Uniswap V3 fallback, we implemented the constant-product AMM formulas:
  - Ask size at level $i$:
    $$\text{ask\_sz}_i = x_{\text{base}} \cdot \left( \frac{1}{\sqrt{1 + s_{i-1}}} - \frac{1}{\sqrt{1 + s_i}} \right)$$
  - Bid size at level $i$:
    $$\text{bid\_sz}_i = x_{\text{base}} \cdot \left( \frac{1}{\sqrt{1 - s_i}} - \frac{1}{\sqrt{1 - s_{i-1}}} \right)$$
  where $x_{\text{base}}$ is determined based on the flipping state (token 0 if not flipped, token 1 if flipped).
- **Step 2**: To fix the active Uniswap V3 path math, we scaled sizes using active tick and tick spacing boundaries:
  - Unflipped (`not is_flipped`):
    - $\text{ask\_sz}_i = \frac{L \cdot \left(\frac{1}{\sqrt{P_1}} - \frac{1}{\sqrt{P_2}}\right)}{10^{\text{decimals0}}}$
    - $\text{bid\_sz}_i = \frac{L \cdot (\sqrt{P_2} - \sqrt{P_1})}{10^{\text{decimals1}} \cdot \text{bid\_px}_i}$
  - Flipped (`is_flipped`):
    - $\text{ask\_sz}_i = \frac{L \cdot (\sqrt{P_2} - \sqrt{P_1})}{10^{\text{decimals1}}}$
    - $\text{bid\_sz}_i = \frac{L \cdot \left(\frac{1}{\sqrt{P_1}} - \frac{1}{\sqrt{P_2}}\right)}{10^{\text{decimals0}} \cdot \text{bid\_px}_i}$
- **Step 3**: Validated incoming prices and reserves:
  - Prices that are `<= 0`, `NaN`, or `Inf` are correctly identified and discarded (returning early).
  - Explicitly typed values of price/reserves that are `None` or of invalid type raise `TypeError`, as verified by stress tests.
  - Parameter coercion safely handles cases when `decimals0`, `decimals1`, or `tickSpacing` are missing or `None`.

## 3. Caveats
- No caveats. The implementation covers all constraints and conforms precisely to the equations specified in the analysis.

## 4. Conclusion
- The synthetic orderbook depth calculations in `src/crypcodile/exchanges/base_onchain/normalize.py` are now mathematically sound, produce exactly 5 levels for both active and fallback paths, safely discard invalid price updates, and pass all 735 tests including unit and E2E suites.

## 5. Verification Method
- **Run the full test suite**:
  ```bash
  .venv/bin/pytest
  ```
- **Inspect unit tests**:
  - Open and read `tests/exchanges/base_onchain/test_normalize_depth.py` to check active, fallback, flipped, NaN/Inf, and parameter coercion assertions.
