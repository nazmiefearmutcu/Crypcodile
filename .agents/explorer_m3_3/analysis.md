# Milestone 3 Investigation Report: Multi-Level Orderbook Depth Calculations

This report analyzes the correctness, completeness, and robustness of the multi-level orderbook depth calculations in `src/crypcodile/exchanges/base_onchain/normalize.py` and its related tests.

---

## Executive Summary

1. **Calculates 5 Bids/Asks**:
   - **Aerodrome V2**: Yes, it always calculates 5 levels of bids and asks.
   - **Uniswap V3**: Yes, in the main path (when `"liquidity"` is present in the `state` payload). However, in the fallback path (when `"liquidity"` is absent), it only calculates 1 bid and 1 ask.
2. **Depth=1 Facade**:
   - Yes, there is a **depth=1 facade** in the Uniswap V3 fallback path (lines 120-136). This violates the interface contract defined in `PROJECT.md` which requires at least 5 bids and asks for all snapshots.
3. **Math Accuracy and Realism**:
   - **Aerodrome V2 (Reserve-Based)**: The math for calculating spreads (`price * (1.0 +/- 0.0005 * i)`) and sizes (`reserve / (5.0 * i)`) is mathematically robust, correctly scaled, and represents a realistic AMM fractional reserve depth heuristic.
   - **Uniswap V3 (Tick/Liquidity-Based)**:
     - **Price Math**: The math to calculate ticks and prices is correct for standard and flipped configurations, but is highly fragile. It relies on a "double-inversion" canceling out. If the normalizer falls back to the contract tick (e.g. if `price` or `price_ratio` is malformed or throws an exception), the code calculates incorrect prices for flipped pools, off by a scaling factor of $10^{2 \cdot (decimals0 - decimals1)}$ (e.g., $10^{4}$ for cbBTC/USDC, or $10^{24}$ for WETH/USDC).
     - **Size Math**: The size calculation `base_sz = liquidity / 10**decimals0` is **incorrect and highly unrealistic**. It ignores the relationship between raw liquidity $L$ and the current price ($\sqrt{P}$), and fails to compute the actual asset reserves in the tick intervals. This results in massive scaling errors—especially in flipped pools (e.g., calculating $200$ Billion USDC of depth at level 1 for a pool with only $30,000$ USDC of total reserves).

---

## Detailed Findings

### 1. 5-Level Depth and the Fallback Facade
The正常izer (`normalize_onchain_update`) splits processing by pool type:
* **Aerodrome V2** (lines 150-163):
  ```python
  for i in range(1, 6):
      spread = 0.0005 * i
      bid_px = price * (1.0 - spread)
      ...
      bids.append((bid_px, bid_sz))
      asks.append((ask_px, ask_sz))
  ```
  This correctly generates 5 bids and 5 asks.
* **Uniswap V3 Main Path** (lines 98-119):
  ```python
  for i in range(1, 6):
      ...
      bids.append((bid_px, bid_sz))
      asks.append((ask_px, ask_sz))
  ```
  This correctly generates 5 bids and 5 asks when `"liquidity"` is in the state payload.
* **Uniswap V3 Fallback Path** (lines 120-136):
  If the state update lacks `"liquidity"`, it falls back to a single level:
  ```python
  bid_px = price * 0.9995
  ask_px = price * 1.0005
  bids.append((bid_px, base_bid_sz)) # Single append -> depth = 1
  asks.append((ask_px, base_ask_sz)) # Single append -> depth = 1
  ```
  This is a **depth=1 facade**. It directly violates the project's interface contract (`PROJECT.md` line 64):
  > "For Uniswap V3 and Aerodrome V2, BookSnapshot must provide at least 5 bid and 5 ask levels calculated using tick/reserves math."

Furthermore, unit tests like `test_normalize_standard_case` in `tests/exchanges/base_onchain/test_stress_challenger.py` assert and enforce this incorrect depth=1 behavior when liquidity is missing:
```python
assert isinstance(snapshot, BookSnapshot)
assert snapshot.bids == [(ticker.bid_px, ticker.bid_sz)]
assert snapshot.asks == [(ticker.ask_px, ticker.ask_sz)]
```

---

### 2. Math Accuracy & Realism Analysis

#### Uniswap V3 Tick/Price Fallback Fragility
When `is_flipped = True` (meaning symbol's base/quote are opposite to pool's token0/token1), the normalizer calculates:
```python
price_ratio = (10 ** dec_diff) / price
tick = math.log(price_ratio) / math.log(1.0001)
```
And `get_price_at_tick` returns:
```python
return float((1.0001 ** (-t)) * (10 ** dec_diff))
```
While this cancels out if we calculate `tick` from `price`, if an exception occurs in `price_ratio` calculation or log scaling, the normalizer falls back to the contract tick:
```python
except Exception:
    tick = float(state.get("tick", 0))
```
If we use the contract tick $t$ directly:
- The contract tick $t$ is defined as $1.0001^t = \frac{\text{pool\_token1\_base}}{\text{pool\_token0\_base}} = \frac{\text{token0\_base}}{\text{token1\_base}}$.
- The desired display price is $\text{token1\_human} / \text{token0\_human} = \frac{\text{token1\_base}}{\text{token0\_base}} \cdot 10^{d_0 - d_1} = 1.0001^{-t} \cdot 10^{dec\_diff}$.
- This matches `1.0001 ** (-t) * 10 ** dec_diff`.
- However, if the code falls back to the contract tick when `is_flipped = True`, the price calculation is correct, but the calculated `tick` inside `normalize_onchain_update` is inconsistent with the price if `price_ratio` works. The normalizer calculates `price_ratio = (10 ** dec_diff) / price`, whereas the contract tick corresponds to $\text{price\_ratio\_contract} = 1.0001^t = \frac{1}{\text{price} \cdot 10^{dec\_diff}}$.
- For example, if WETH-USDC is flipped: WETH (18 decimals) is token0, USDC (6 decimals) is token1.
  If $P = 3000$, `price_ratio = 10**12 / 3000 = 3.33 * 10**8`.
  But the contract's real `price_ratio_contract` is $\frac{1}{3000 \cdot 10^{12}} = 3.33 \cdot 10^{-16}$.
  If the `price_ratio` calculation succeeds, it computes `tick = log(3.33 * 10**8) / log(1.0001) = 196249`.
  If it fails and falls back to contract tick, it gets `tick = log(3.33 * 10**-16) / log(1.0001) = -356396`.
  This discrepancy changes the tick value used by a massive margin (offset of $\approx 550,000$ ticks). Because it also flips the sign in `get_price_at_tick` ($1.0001^{-t}$), the final price levels cancel out, but this is a fragile design that makes debugging and tick-range analysis impossible.

#### Uniswap V3 Size Calculations
The Uniswap V3 size calculation is fundamentally incorrect:
```python
base_sz = liquidity / (10 ** decimals0) if decimals0 else liquidity / 1e18
```
This is unrealistic because:
1. **Wrong Scale Factor**: Uniswap V3 liquidity $L$ is a geometric mean of base units: $L = \sqrt{x_{\text{virtual}} \cdot y_{\text{virtual}}}$. Dividing by $10^{decimals0}$ makes the size scale completely incorrect if $decimals0 \neq decimals1$.
2. **Missing Price Scale**: The actual token0 size $x$ and token1 size $y$ are related to $L$ and the price $\sqrt{P}$:
   $$x_{\text{virtual}} = L / \sqrt{P}, \quad y_{\text{virtual}} = L \cdot \sqrt{P}$$
   By ignoring $\sqrt{P}$, the size of token0 is scaled incorrectly.
3. **Massive Flipped Errors**:
   If `is_flipped = True` and $decimals0 = 6$ (USDC) and $decimals1 = 18$ (WETH).
   For WETH-USDC pool, $L \approx 10^{18}$.
   The normalizer calculates `base_sz = 10**18 / 10**6 = 10**12` USDC.
   At level 1, `ask_sz = 2 * 10**11` USDC ($200$ Billion USDC).
   In reality, the pool reserves are only $30,000$ USDC. The calculated orderbook depth is $7,000,000$ times larger than the entire pool reserves!

---

## Recommendations & Fix Strategy

To resolve these gaps and ensure Milestone 3 is complete, correct, and robust, the following fixes are recommended:

### 1. Remove the Depth=1 Fallback Facade
Modify the Uniswap V3 fallback path to calculate 5 levels of bids and asks using the reserve-based heuristic, matching the Aerodrome V2 behavior.

```python
# Proposed Fix: Generate 5 levels instead of 1
base_ask_sz = safe_cap(reserve_token0)
base_bid_sz = safe_cap(reserve_token1 / price if price > 0 else 0.0)

for i in range(1, 6):
    spread = 0.0005 * i
    bid_px = price * (1.0 - spread)
    ask_px = price * (1.0 + spread)
    
    ask_sz = base_ask_sz / (5.0 * i)
    bid_sz = base_bid_sz / (5.0 * i)
    
    ask_sz = max(ask_sz, 0.0001)
    bid_sz = max(bid_sz, 0.0001)
    
    bids.append((bid_px, bid_sz))
    asks.append((ask_px, ask_sz))
```
*Note: Any unit tests asserting `len(snapshot.bids) == 1` in fallback mode must be updated to assert `len(snapshot.bids) == 5`.*

### 2. Correct the Uniswap V3 Size Math
To make the sizes mathematically realistic and prevent multi-billion dollar scaling errors in flipped pools, calculate sizes using the actual Uniswap V3 tick-interval math:

For a given level $i$, the price moves from tick $t_1$ to $t_2$:
* For asks (selling token0, moving price up):
  $$\Delta x = L \cdot \left( \frac{1}{\sqrt{P(t_1)}} - \frac{1}{\sqrt{P(t_2)}} \right)$$
* For bids (buying token0, moving price down):
  $$\Delta y = L \cdot (\sqrt{P(t_1)} - \sqrt{P(t_2)}), \quad \Delta x = \Delta y / P_{bid}$$

Where:
- $\sqrt{P(t)} = 1.0001^{t/2}$ (in raw contract scale).
- Liquidity $L$ is used directly in its raw form.
- The resulting $\Delta x$ (base units of token0) is scaled to human-readable units by dividing by $10^{d_0}$.

Alternatively, if a simplified heuristic is preferred, use a reserve-bounded heuristic similar to the fallback path, but scaled by the active liquidity fraction, ensuring that the total size at any level never exceeds the physical reserves of the pool.
