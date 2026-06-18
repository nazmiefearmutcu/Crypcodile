# Milestone 3 Analysis: Multi-Level Orderbook Depth Calculations

## Executive Summary
This report analyzes the implementation of Milestone 3 (Multi-level orderbook depth calculations) in `src/crypcodile/exchanges/base_onchain/normalize.py` and the corresponding test coverage in `tests/exchanges/base_onchain/`. 

Our findings indicate that the normalizer implementation has significant correctness, completeness, and robustness issues:
1. **Depth-1 Facade in Fallback Paths**: While the main paths for Uniswap V3 and Aerodrome V2 attempt to calculate 5 levels of bids and asks, the Uniswap V3 fallback path (when `liquidity` info is missing from the state) only returns a **single bid and ask level**, creating a `depth=1` facade. This violates the interface contract of `PROJECT.md`.
2. **Mathematically Incorrect Uniswap V3 Sizes**: The active path for Uniswap V3 computes `base_sz` directly from `liquidity` as `base_sz = liquidity / (10 ** decimals0)`. This completely omits the $\sqrt{p_{\text{raw}}}$ scaling factor. Consequently, the calculated orderbook sizes are off by up to **100,000x** (underestimated for WETH) and **3,600x** (overestimated for USDC). It also fails to account for `is_flipped` when scaling by decimal places.
3. **Unrealistic Aerodrome V2 Sizes**: Aerodrome V2 utilizes a heuristic size calculation `reserve / (5.0 * i)`, resulting in cumulative depth representing **45.7%** of the entire pool reserves within a tiny 0.25% spread. In a realistic constant product pool ($x \cdot y = k$), moving the price by 0.25% only utilizes **0.125%** of reserves. This represents an overestimate of orderbook sizes by a factor of **~360x to 800x**.
4. **Critical Test Gaps**: There are **no tests** verifying the correctness or math accuracy of the 5-level orderbook snapshots. The current test suite only verifies basic error handling and the fallback depth=1 behavior.

---

## Detailed Evaluation

### 1. 5-Level Bid and Ask Calculation (Completeness)
- **Aerodrome V2**: Yes, it loops 5 times (`for i in range(1, 6)`) and correctly appends 5 levels of bids/asks to the lists.
- **Uniswap V3 (Active Path)**: Yes, when `liquidity` is present in `state`, it loops 5 times using tick-based steps and appends 5 levels.
- **Uniswap V3 (Fallback Path)**: **No**. If `"liquidity" in state` is false, it falls through to:
  ```python
  elif pool_type == "uniswap_v3":
      # Fallback for Uniswap V3 without liquidity info (1 level)
      ...
      bid_px = price * 0.9995
      ask_px = price * 1.0005
      bids.append((bid_px, base_bid_sz))
      asks.append((ask_px, base_ask_sz))
  ```
  This returns only `1` bid and `1` ask level. This is a clear violation of the `PROJECT.md` interface contract:
  > *"For Uniswap V3 and Aerodrome V2, BookSnapshot must provide at least 5 bid and 5 ask levels calculated using tick/reserves math."*

---

### 2. Depth-1 Facade Check
There is a depth-1 facade in the **Uniswap V3 fallback path** (detailed above). 
If the normalizer encounters a Uniswap V3 state update without active `liquidity` (e.g. during initialization, RPC polling issues, or in test setups like `test_normalize_standard_case` in `test_stress_challenger.py`), it creates a `BookSnapshot` containing only 1 level. 

To maintain compliance and robustness, the normalizer must always output 5 levels, using a constant-product reserve-based formula when tick/liquidity information is not available.

---

### 3. Math Correctness and Realism Analysis

#### A. Uniswap V3 Math (Active Path)
The active path uses the following formula to compute sizes (lines 109-112):
```python
base_sz = liquidity / (10 ** decimals0) if decimals0 else liquidity / 1e18
ask_sz = base_sz / (5.0 * i)
bid_sz = base_sz / (5.0 * i)
```
This formula is mathematically incorrect and unrealistic:
1. **Missing Price Scale**: Raw Uniswap V3 liquidity $L$ represents the virtual reserves density $\sqrt{X_{\text{raw}} \cdot Y_{\text{raw}}}$. The virtual reserves are given by:
   $$X_{\text{virtual, raw}} = \frac{L}{\sqrt{P_{\text{raw}}}}$$
   $$Y_{\text{virtual, raw}} = L \cdot \sqrt{P_{\text{raw}}}$$
   Using $L$ directly without scaling by $\sqrt{P_{\text{raw}}}$ makes the sizes completely incorrect.
   - For a `cbBTC-USDC` pool, $P_{\text{raw}} \approx 10^{-7}$, so $\sqrt{P_{\text{raw}}} \approx 3 \cdot 10^{-4}$. The base size is underestimated by $\sim 3,000\text{x}$.
   - For a `WETH-USDC` pool, $P_{\text{raw}} \approx 3 \cdot 10^{-9}$, so $\sqrt{P_{\text{raw}}} \approx 5.47 \cdot 10^{-5}$. The base size is underestimated by $\sim 18,000\text{x}$.
2. **Ignored Flip State**: The division by `10 ** decimals0` is hardcoded. If the pool is flipped (`is_flipped` is True), the base asset corresponds to token 1 of the contract, so the decimal division should use `decimals1` or adjust according to the base asset denomination.
3. **Arbitrary Size Distribution**: Dividing the base size by `5.0 * i` is a heuristic that does not reflect actual available tick liquidity.

##### Recommended Uniswap V3 Size Formulas:
For each level $i \in [1, 5]$ with tick boundaries $[t_1, t_2]$:
- Let $\sqrt{P_1} = 1.0001^{t_1/2}$ and $\sqrt{P_2} = 1.0001^{t_2/2}$.
- **Unflipped (`not is_flipped`)**:
  - Asks ($t_1 = t + (i-1)s, t_2 = t + i \cdot s$):
    $$\text{ask\_sz}_i = \frac{L \cdot \left(\frac{1}{\sqrt{P_1}} - \frac{1}{\sqrt{P_2}}\right)}{10^{\text{decimals0}}}$$
  - Bids ($t_1 = t - i \cdot s, t_2 = t - (i-1)s$):
    $$\text{bid\_sz}_i = \frac{L \cdot (\sqrt{P_2} - \sqrt{P_1})}{10^{\text{decimals1}} \cdot \text{bid\_px}_i}$$
- **Flipped (`is_flipped`)**:
  - Asks ($t_1 = t - i \cdot s, t_2 = t - (i-1)s$):
    $$\text{ask\_sz}_i = \frac{L \cdot (\sqrt{P_2} - \sqrt{P_1})}{10^{\text{decimals0}}}$$
  - Bids ($t_1 = t + (i-1)s, t_2 = t + i \cdot s$):
    $$\text{bid\_sz}_i = \frac{L \cdot \left(\frac{1}{\sqrt{P_1}} - \frac{1}{\sqrt{P_2}}\right)}{10^{\text{decimals1}} \cdot \text{bid\_px}_i}$$

#### B. Aerodrome V2 Math
The Aerodrome V2 math uses:
```python
ask_sz = base_ask_sz / (5.0 * i)
bid_sz = base_bid_sz / (5.0 * i)
```
Summing these sizes over 5 levels gives a cumulative depth of:
$$\text{Cumulative Depth} = \sum_{i=1}^5 \frac{\text{reserve}}{5i} \approx 0.457 \cdot \text{reserve}$$
This means 45.7% of the pool's reserves are available within a tiny 0.25% spread.
In a constant product AMM ($x \cdot y = k$), moving the price by $\epsilon$ (spread) changes the reserves as follows:
$$x' = \frac{x}{\sqrt{1 + \epsilon}} \approx x \left(1 - \frac{\epsilon}{2}\right)$$
For $\epsilon = 0.25\%$, the actual token amount available is $x \cdot 0.00125$ (or 0.125% of reserves).
Thus, the current implementation overestimates liquidity depth by **~360x to 800x**, which is extremely unrealistic and dangerous for executing trades/estimating slippage.

##### Recommended Constant-Product Size Formulas:
For any constant product pool (including the Uniswap V3 fallback path), the size at level $i$ (with spread boundary $s_i = 0.0005 \cdot i$ and $s_{i-1} = 0.0005 \cdot (i-1)$) should be:
- **Asks**:
  $$\text{ask\_sz}_i = \text{reserve0} \cdot \left( \frac{1}{\sqrt{1 + s_{i-1}}} - \frac{1}{\sqrt{1 + s_i}} \right)$$
- **Bids**:
  $$\text{bid\_sz}_i = \text{reserve0} \cdot \left( \frac{1}{\sqrt{1 - s_i}} - \frac{1}{\sqrt{1 - s_{i-1}}} \right)$$

---

## Identified Gaps and Vulnerabilities
1. **Unhandled NaN/Inf Price**:
   If the state contains `"price": float("nan")`, the normalizer passes the `price <= 0` check (because `NaN <= 0` evaluates to `False` in Python). This results in `BookSnapshot` yielding NaN values for prices, contaminating downstream sinks.
2. **Missing Input Coercion**:
   If `decimals0`, `decimals1`, or `tick_spacing` are `None` (rather than missing), `state.get` returns `None` and subsequent `int()` casts or arithmetic operations will raise a `TypeError` and crash the normalizer process.
3. **No 5-Level Validation Tests**:
   The test suite lacks any validation for the actual output counts and pricing/sizing logic of a 5-level Uniswap V3 active or Aerodrome V2 orderbook update.

---

## Recommended Fix Strategy
Below is the proposed patch for `src/crypcodile/exchanges/base_onchain/normalize.py`:

```python
# Proposed changes in normalize.py
import math

def normalize_onchain_update(msg: dict[str, Any], local_ts: int) -> Iterable[Record]:
    ...
    price = state["price"]
    # 1. Robust NaN/Inf validation
    if price <= 0 or math.isnan(price) or math.isinf(price):
        return
        
    reserve_token0 = state.get("reserve0", 0.0)
    reserve_token1 = state.get("reserve1", 0.0)
    
    bids = []
    asks = []
    
    # Coerce parameters safely to handle None or missing values
    decimals0 = int(state.get("decimals0") or (8 if "btc" in pool_name.lower() else 18))
    decimals1 = int(state.get("decimals1") or 18)
    is_flipped = bool(state.get("is_flipped", False))
    
    if pool_type == "uniswap_v3" and "liquidity" in state:
        liquidity = state.get("liquidity") or 0
        tick_spacing = int(state.get("tickSpacing") or state.get("tick_spacing") or 10)
        
        # Calculate active tick
        dec_diff = decimals0 - decimals1
        try:
            if not is_flipped:
                price_ratio = price / (10 ** dec_diff)
            else:
                price_ratio = (10 ** dec_diff) / price
            if price_ratio > 0:
                tick = math.log(price_ratio) / math.log(1.0001)
            else:
                tick = float(state.get("tick", 0))
        except Exception:
            tick = float(state.get("tick", 0))
            
        def get_price_at_tick(t: float, flipped: bool, dec0: int, dec1: int) -> float:
            dec_diff = dec0 - dec1
            try:
                if not flipped:
                    return float((1.0001 ** t) * (10 ** dec_diff))
                else:
                    return float((1.0001 ** (-t)) * (10 ** dec_diff))
            except Exception:
                return 0.0

        # 2. Accurate Uniswap V3 math implementation
        for i in range(1, 6):
            if not is_flipped:
                ask_t1 = tick + (i - 1) * tick_spacing
                ask_t2 = tick + i * tick_spacing
                bid_t1 = tick - i * tick_spacing
                bid_t2 = tick - (i - 1) * tick_spacing
            else:
                ask_t1 = tick - i * tick_spacing
                ask_t2 = tick - (i - 1) * tick_spacing
                bid_t1 = tick + (i - 1) * tick_spacing
                bid_t2 = tick + i * tick_spacing

            ask_px = get_price_at_tick(ask_t2 if not is_flipped else ask_t1, is_flipped, decimals0, decimals1)
            bid_px = get_price_at_tick(bid_t1 if not is_flipped else bid_t2, is_flipped, decimals0, decimals1)

            sqrt_ask1 = 1.0001 ** (ask_t1 / 2.0)
            sqrt_ask2 = 1.0001 ** (ask_t2 / 2.0)
            sqrt_bid1 = 1.0001 ** (bid_t1 / 2.0)
            sqrt_bid2 = 1.0001 ** (bid_t2 / 2.0)

            if not is_flipped:
                ask_sz = (liquidity * (1.0 / sqrt_ask1 - 1.0 / sqrt_ask2)) / (10 ** decimals0)
                bid_sz = ((liquidity * (sqrt_bid2 - sqrt_bid1)) / (10 ** decimals1)) / bid_px if bid_px > 0 else 0.0
            else:
                ask_sz = (liquidity * (sqrt_ask2 - sqrt_ask1)) / (10 ** decimals0)
                bid_sz = ((liquidity * (1.0 / sqrt_bid1 - 1.0 / sqrt_bid2)) / (10 ** decimals1)) / bid_px if bid_px > 0 else 0.0

            bids.append((bid_px, max(bid_sz, 0.0001)))
            asks.append((ask_px, max(ask_sz, 0.0001)))

    else:
        # 3. Robust 5-level Reserves Math for Aerodrome V2 AND Uniswap V3 fallback
        def safe_cap(val: float) -> float:
            try:
                if math.isnan(val) or math.isinf(val):
                    return 0.0001
            except Exception:
                pass
            return max(val, 0.0001)

        base_reserve0 = safe_cap(reserve_token0)
        
        for i in range(1, 6):
            spread_prev = 0.0005 * (i - 1)
            spread_curr = 0.0005 * i
            
            bid_px = price * (1.0 - spread_curr)
            ask_px = price * (1.0 + spread_curr)
            
            ask_sz = base_reserve0 * (1.0 / math.sqrt(1.0 + spread_prev) - 1.0 / math.sqrt(1.0 + spread_curr))
            bid_sz = base_reserve0 * (1.0 / math.sqrt(1.0 - spread_curr) - 1.0 / math.sqrt(1.0 - spread_prev))
            
            bids.append((bid_px, safe_cap(bid_sz)))
            asks.append((ask_px, safe_cap(ask_sz)))
```

This strategy removes the depth-1 facade entirely, fixes the scale and flipped logic bugs, makes sizes realistic based on constant-product and Uniswap V3 formulas, and handles NaN/Type errors robustly.
