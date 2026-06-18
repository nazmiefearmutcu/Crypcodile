# Milestone 3 Analysis: Multi-Level Orderbook Depth Calculations

This report provides a detailed white-box investigation of the orderbook normalization logic in `src/crypcodile/exchanges/base_onchain/normalize.py` and its related tests, evaluating its correctness, completeness, and mathematical realism for Uniswap V3 and Aerodrome V2 pools.

---

## Executive Summary

1. **Depth Coverage**:
   - The normalizer calculates 5 bids and 5 asks for both Uniswap V3 (when active `liquidity` is provided) and Aerodrome V2.
   - However, the Uniswap V3 **fallback path** (when `liquidity` is not present in the state message) returns only **1 bid and 1 ask level**, failing the requirement of providing at least 5 bid and 5 ask levels.
2. **Depth=1 Facade**:
   - Yes, there is a **depth=1 facade** in the Uniswap V3 fallback path.
   - While the main paths do provide 5 levels, the sizes are scaled using arbitrary linear division (`base_sz / (5.0 * i)`), creating a synthetic facade of depth where liquidity is inflated by **800x to 10,000x** compared to actual pool reserves and tick ranges.
3. **Mathematical Correctness & Realism**:
   - **Prices**: Correct. Price calculations at different ticks in Uniswap V3 correctly use exponents and handle decimals and flipped pools (`is_flipped`).
   - **Sizes**: **Inaccurate and highly unrealistic**.
     - Uniswap V3 size calculation ignores the price factor ($\sqrt{P}$ or $1/\sqrt{P}$) when converting virtual liquidity to base asset reserves, leading to incorrect base sizes (e.g. off by ~22x for cbBTC-USDC due to price level).
     - In both DEXs, dividing the base size or total reserves by $5.0 \cdot i$ grossly exaggerates the pool liquidity in that narrow price step, resulting in sizes up to 10,000x larger than the actual reserves in that range.

---

## Detailed Investigation

### 1. Depth Levels for Uniswap V3 and Aerodrome V2

In `normalize.py`, the normalizer handles pool updates by type:
- **Uniswap V3 with Liquidity** (lines 66-119): Iterates `i` from 1 to 5 to produce exactly 5 bid and ask levels based on tick spacing:
  ```python
  for i in range(1, 6):
      ...
      bids.append((bid_px, bid_sz))
      asks.append((ask_px, ask_sz))
  ```
- **Uniswap V3 Fallback (Without Liquidity)** (lines 120-136): Only appends a **single level** of bids and asks (depth = 1):
  ```python
  bid_px = price * 0.9995
  ask_px = price * 1.0005
  bids.append((bid_px, base_bid_sz))
  asks.append((ask_px, base_ask_sz))
  ```
- **Aerodrome V2** (lines 137-162): Iterates `i` from 1 to 5 to generate exactly 5 bid and ask levels:
  ```python
  for i in range(1, 6):
      spread = 0.0005 * i
      ...
      bids.append((bid_px, bid_sz))
      asks.append((ask_px, ask_sz))
  ```

Thus, the 5-level requirement is met in the main paths, but **broken (depth=1 facade)** in the Uniswap V3 fallback path.

---

### 2. Math for Prices and Sizes

#### A. Price Calculations (Accurate)
For Uniswap V3, the price $P_{human}$ at tick $t$ is computed via:
$$P_{human} = 1.0001^{\pm t} \times 10^{\text{decimals}_0 - \text{decimals}_1}$$
The code correctly implements this relationship:
- If `not is_flipped`: uses $1.0001^t \times 10^{dec\_diff}$.
- If `is_flipped`: uses $1.0001^{-t} \times 10^{dec\_diff}$.
This matches the Uniswap V3 specification.

#### B. Size Calculations (Inaccurate and Unrealistic)

##### Uniswap V3 Size Calculations
The code computes the base size as:
```python
base_sz = liquidity / (10 ** decimals0) if decimals0 else liquidity / 1e18
ask_sz = base_sz / (5.0 * i)
bid_sz = base_sz / (5.0 * i)
```
This contains two major mathematical errors:
1. **Price scaling is ignored**: In Uniswap V3, virtual reserves of contract token0 and token1 are $X_{virtual} = L / \sqrt{P_{raw}}$ and $Y_{virtual} = L \cdot \sqrt{P_{raw}}$. The base size (in units of token0 or token1) depends directly on the price ratio.
   - For a standard pool (`not is_flipped`), the base asset is contract token0, so the base reserve should scale as $L / (\sqrt{P_{raw}} \cdot 10^{decimals0})$.
   - For a flipped pool (`is_flipped`), the base asset is contract token1, so the base reserve should scale as $(L \cdot \sqrt{P_{raw}}) / 10^{decimals0}$.
   - By omitting $\sqrt{P_{raw}}$, the code calculates an incorrect base size. For example, for cbBTC-USDC at $P = 50000$ and `is_flipped = True`, $\sqrt{P_{raw}} \approx 0.0447$. The calculated base size is off by a factor of $22.36\text{x}$.
2. **Gross exaggeration of orderbook depth**:
   The actual amount of base asset available in a tick range $[t_1, t_2]$ is:
   $$\Delta X = L \cdot \left(\frac{1}{\sqrt{P_{raw, 1}}} - \frac{1}{\sqrt{P_{raw, 2}}}\right)$$
   For a tick spacing of 10 ticks (a 0.1% price move), this corresponds to roughly $0.0005 \cdot X_{virtual}$.
   However, the code sets the level 1 size to $0.2 \cdot \text{base\_sz}$ (20% of virtual reserves). This results in level sizes that are **~400x to 10,000x larger** than the actual reserves in that tick interval (e.g., calculating a size of `2000.0` cbBTC instead of the actual `0.223` cbBTC).

##### Aerodrome V2 Size Calculations
The code computes sizes for Aerodrome V2 as:
```python
base_ask_sz = reserve_token0
base_bid_sz = reserve_token1 / price
ask_sz = base_ask_sz / (5.0 * i)
bid_sz = base_bid_sz / (5.0 * i)
```
This distributes 20% of the entire pool reserve into the first 5 bps spread level.
For a volatile pool (constant product $x \cdot y = k$), the actual amount of token0 available when price moves from $P$ to $P_{target}$ is:
$$\Delta R_0 = \left|\sqrt{\frac{k}{P}} - \sqrt{\frac{k}{P_{target}}}\right|$$
For a 5 bps price change (spread = 0.0005), the actual available base asset is only **0.025%** of the pool reserves:
$$\Delta R_0 \approx R_0 \cdot \left(1 - \frac{1}{\sqrt{1.0005}}\right) \approx R_0 \cdot 0.00025$$
The code's synthetic size of $R_0 / 5 = 20\%$ of the reserves is **800 times larger** than the actual pool liquidity in that price range.

---

## Gaps and Bugs Summary

| DEX Type / Path | Issue | Impact |
| --- | --- | --- |
| **Uniswap V3 Fallback** | Only 1 bid/ask level is generated when `liquidity` is missing in `state`. | **High**: Breaks the 5-level interface contract of `BookSnapshot`. |
| **Uniswap V3 Size Math** | Ignores the $1/\sqrt{P}$ or $\sqrt{P}$ factor for virtual reserves. | **High**: Sizing is incorrect by orders of magnitude (e.g. 22x error for cbBTC). |
| **Uniswap V3 Depth Math** | Uses arbitrary division ($L / 5i$) instead of range liquidity change $\Delta(L/\sqrt{P})$. | **Medium**: Liquidity is inflated by up to 10,000x. |
| **Aerodrome V2 Size Math** | Uses arbitrary division of reserves ($R / 5i$) instead of constant product math. | **Medium**: Liquidity is inflated by up to 800x. |

---

## Proposed Fix and Implementation Strategy

To address these gaps, we propose a complete, mathematically robust rewrite of the orderbook normalization logic in `normalize.py`.

### 1. Robust 5-Level Fallback path
If `liquidity` is not present for Uniswap V3, instead of returning a 1-level book, the normalizer should fall back to reserve-based 5-level calculations (similar to Aerodrome V2 but using the CPMM math outlined below).

### 2. Correct Uniswap V3 Depth Math
Compute the size of level $i$ as the change in reserves between tick levels $i-1$ and $i$:
- Let $P_{raw} = price\_ratio$.
- Let $P_{raw, ask, i} = 1.0001^{ask\_tick_i}$ and $P_{raw, bid, i} = 1.0001^{bid\_tick_i}$.
- **Standard Pool (`not is_flipped`)**:
  $$\text{ask\_sz\_raw}_i = L_{raw} \cdot \left(\frac{1}{\sqrt{P_{raw, ask, i-1}}} - \frac{1}{\sqrt{P_{raw, ask, i}}}\right)$$
  $$\text{bid\_sz\_raw}_i = L_{raw} \cdot \left(\frac{1}{\sqrt{P_{raw, bid, i}}} - \frac{1}{\sqrt{P_{raw, bid, i-1}}}\right)$$
- **Flipped Pool (`is_flipped`)**:
  $$\text{ask\_sz\_raw}_i = L_{raw} \cdot \left(\sqrt{P_{raw, ask, i-1}} - \sqrt{P_{raw, ask, i}}\right)$$
  $$\text{bid\_sz\_raw}_i = L_{raw} \cdot \left(\sqrt{P_{raw, bid, i}} - \sqrt{P_{raw, bid, i-1}}\right)$$
Divide each raw size by $10^{decimals0}$ to get the correct human base sizes.

### 3. Correct Aerodrome V2 / Fallback CPMM Math
Use the constant product model to determine the exact change in reserves:
- Let $k = \text{reserve}_0 \cdot \text{reserve}_1$.
- Let $P_{ask, 0} = P_{bid, 0} = price$.
- For level $i$ (with price spread $P_{ask, i}$ and $P_{bid, i}$):
  $$\text{ask\_sz}_i = \sqrt{\frac{k}{P_{ask, i-1}}} - \sqrt{\frac{k}{P_{ask, i}}}$$
  $$\text{bid\_sz}_i = \sqrt{\frac{k}{P_{bid, i}}} - \sqrt{\frac{k}{P_{bid, i-1}}}$$
- For stable pools (Aerodrome V2 stable), since $x^3y + y^3x = k$ holds, we can use the volatile volatile formula as a conservative lower bound, or apply an adjusted multiplier.
