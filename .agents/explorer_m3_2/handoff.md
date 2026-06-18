# Handoff Report: Milestone 3 (Multi-Level Orderbook Depth Calculations)

This report summarizes the white-box investigation of the orderbook normalization logic in `src/crypcodile/exchanges/base_onchain/normalize.py`.

---

## 1. Observation

We investigated the normalizer code and the test suite:
- **File**: `src/crypcodile/exchanges/base_onchain/normalize.py`
  - Uniswap V3 fallback path (lines 120-136):
    ```python
    elif pool_type == "uniswap_v3":
        # Fallback for Uniswap V3 without liquidity info (1 level)
        ...
        base_ask_sz = safe_cap(reserve_token0)
        base_bid_sz = safe_cap(reserve_token1 / price if price > 0 else 0.0)
        
        bid_px = price * 0.9995
        ask_px = price * 1.0005
        bids.append((bid_px, base_bid_sz))
        asks.append((ask_px, base_ask_sz))
    ```
  - Uniswap V3 sizing logic (lines 109-112):
    ```python
    base_sz = liquidity / (10 ** decimals0) if decimals0 else liquidity / 1e18
    # Distribute realistically, decreasing for outer levels
    ask_sz = base_sz / (5.0 * i)
    bid_sz = base_sz / (5.0 * i)
    ```
  - Aerodrome V2 sizing logic (lines 155-156):
    ```python
    ask_sz = base_ask_sz / (5.0 * i)
    bid_sz = base_bid_sz / (5.0 * i)
    ```

- **File**: `tests/exchanges/base_onchain/test_connector.py`
  - Verifies multi-level snapshot properties (lines 566-570):
    ```python
    for i in range(4):
        assert snapshot.bids[i][0] > snapshot.bids[i+1][0]
        assert snapshot.asks[i][0] < snapshot.asks[i+1][0]
        assert snapshot.bids[i][1] > snapshot.bids[i+1][1]
        assert snapshot.asks[i][1] > snapshot.asks[i+1][1]
    ```

- **File**: `tests/exchanges/base_onchain/test_stress_challenger.py`
  - Verifies fallback depth=1 behavior (lines 57-58):
    ```python
    assert snapshot.bids == [(ticker.bid_px, ticker.bid_sz)]
    assert snapshot.asks == [(ticker.ask_px, ticker.ask_sz)]
    ```

---

## 2. Logic Chain

1. **Depth=1 Facade in Fallback**: In `normalize.py:120-136`, if an update lacks `liquidity` information, the Normalizer generates only 1 bid and 1 ask. This directly creates a depth=1 facade, violating the interface contract specified in `PROJECT.md:64` which states that `BookSnapshot` must provide at least 5 bid and 5 ask levels.
2. **Ignored Price Term in Uniswap V3 Size**: Virtual reserves in Uniswap V3 depend on the price ratio: $X_{virtual} = L / \sqrt{P_{raw}}$ (or $Y_{virtual} = L \cdot \sqrt{P_{raw}}$ if flipped). In `normalize.py:109`, the code computes size as `liquidity / 10**decimals0` without adjusting for $\sqrt{P_{raw}}$. For a high priced asset like cbBTC ($P \approx 50000$), this introduces a $\approx 22.36\text{x}$ scaling error.
3. **Inflated Liquidity**: 
   - For Uniswap V3: The code distributes virtual liquidity using `base_sz / 5i`. Since tick spacing (e.g., 10 ticks) represents a tiny fraction of total virtual reserves ($\approx 0.05\%$), setting each level size to $20\%$ of virtual reserves inflates the available size in that range by **~10,000x**.
   - For Aerodrome V2: The code uses `reserve / 5i`. In a constant product pool, a 5 bps price change corresponds to $\approx 0.025\%$ of reserves. Setting the size to $20\%$ of reserves inflates liquidity by **~800x**.
4. **Weak Assertions**: The test suite (`test_connector.py:566-570`) only checks the relative ordering of prices and sizes, rather than verifying their mathematical validity. This allowed the incorrect sizing logic to pass undetected.

---

## 3. Caveats

- Stable pools on Aerodrome V2 use the same CPMM (volatile) logic fallback. While not exact for stable swaps ($x^3y + y^3x = k$), treating them with constant product math is a much safer and more realistic approximation than the current reserve division formula.
- The `is_flipped` address-sorting logic is correctly parsed, but the normalizer's size calculations fail to adjust for flipped vs standard pool equations.

---

## 4. Conclusion

The normalizer implementation is **incomplete** (violates the 5-level snapshot contract in the fallback path) and **mathematically inaccurate/unrealistic** (sizing calculations overestimate liquidity by up to 10,000x and ignore price factors).

### Recommended Implementation Strategy

1. **Re-implement Fallback Path**: Ensure the fallback path generates 5 levels of bids and asks using CPMM math.
2. **Correct Uniswap V3 Sizing**:
   Use tick intervals to calculate the exact reserve change:
   - For Standard Pools:
     $$\Delta X_i = \frac{L_{raw}}{10^{decimals0}} \cdot \left(\frac{1}{\sqrt{P_{raw, i-1}}} - \frac{1}{\sqrt{P_{raw, i}}}\right)$$
   - For Flipped Pools:
     $$\Delta Y_i = \frac{L_{raw}}{10^{decimals0}} \cdot \left(\sqrt{P_{raw, i-1}} - \sqrt{P_{raw, i}}\right)$$
3. **Correct Aerodrome V2 (and Fallback) Sizing**:
   Calculate size using the CPMM formula ($k = R_0 \cdot R_1$):
   $$\text{ask\_sz}_i = \sqrt{\frac{k}{P_{ask, i-1}}} - \sqrt{\frac{k}{P_{ask, i}}}$$
   $$\text{bid\_sz}_i = \sqrt{\frac{k}{P_{bid, i}}} - \sqrt{\frac{k}{P_{bid, i-1}}}$$

---

## 5. Verification Method

- **Verification Command**:
  Run the test suite with:
  ```bash
  .venv/bin/pytest tests/exchanges/base_onchain/
  ```
- **Files to Inspect**:
  - `src/crypcodile/exchanges/base_onchain/normalize.py`: Ensure mathematical correctness and 5-level coverage.
  - `tests/exchanges/base_onchain/test_connector.py`: Add exact numerical assertions checking that sizes match expected values (e.g. $\sim 0.22$ cbBTC instead of `2000.0` cbBTC for cbBTC-USDC at 50,000 price).
