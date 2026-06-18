# Handoff Report — Challenger M3 2

## 1. Observation
Adversarial and stress testing was conducted on the Milestone 3 orderbook math implementation located in:
* **File Path**: `/Users/nazmi/Crypcodile/src/crypcodile/exchanges/base_onchain/normalize.py`
* **Test Suite Added**: `/Users/nazmi/Crypcodile/tests/exchanges/base_onchain/test_challenger_stress_m3.py`

During testing, the following codebase structures and execution outcomes were observed:

### Observation A: Fallback Tick Parsing Crash
At lines 121–133 in `normalize.py`:
```python
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
```
When `price_ratio` underflows to `0.0` (e.g. `price = 1e-320`), it executes the `else` block: `tick = float(state.get("tick", 0))`. If `state["tick"]` is `None`, this triggers a `TypeError: float() argument must be a string or a real number, not 'NoneType'` and crashes the process.

### Observation B: Exponentiation Overflow Crash
At lines 172–175 in `normalize.py`:
```python
            sqrt_ask1 = 1.0001 ** (ask_t1 / 2.0)
            sqrt_ask2 = 1.0001 ** (ask_t2 / 2.0)
            sqrt_bid1 = 1.0001 ** (bid_t1 / 2.0)
            sqrt_bid2 = 1.0001 ** (bid_t2 / 2.0)
```
If `tickSpacing` is extremely large (e.g., `10**15`), `ask_t2` overflows the float limit when computing `1.0001 ** (ask_t2 / 2.0)`, raising an unhandled `OverflowError: (34, 'Result too large')` and crashing the process.

### Observation C: Silent Invalid Snapshots
When `is_flipped = True` and the `price_ratio` overflows (producing `inf`), `math.log(float('inf'))` does not raise an exception in Python but returns `inf`. This propagates to the sizing math resulting in `inf - inf` which yields `nan`. The resulting `BookSnapshot` emits:
* Ask Price: `0.0`
* Ask Size: `nan`
* Bid Price: `0.0`
* Bid Size: `0.0001`

### Observation D: Successful Parameter Coercion and Level Depth
* **Negative/Zero Tick Spacing**: Successfully coerced to `1` (via `max(tick_spacing, 1)`), avoiding zero/negative division or array indexing issues.
* **Flipped Decimals**: Decimals are successfully clamped between 0 and 36 (via `max(0, min(decimals, 36))`), preventing extreme exponent power overflows.
* **Snapshot Depth**: Confirmed that the output always contains exactly 5 levels (`len(snapshot.bids) == 5` and `len(snapshot.asks) == 5`) in both active v3 path and fallback path.

---

## 2. Logic Chain
1. Under normal operation, the inputs to `normalize_onchain_update` are assumed to reside within standard limits.
2. In an adversarial scenario or extreme market volatility, `price` may drop near zero (`1e-320`) or decimals diff could be large, triggering price ratio underflow.
3. The fallback `state.get("tick", 0)` returns `None` if `tick` is explicitly set to `None` in the update payload.
4. Passing `None` to `float()` in Python throws a `TypeError`.
5. For overflow of price ratios, Python's floating-point division `10**30 / 1e-300` returns `inf` instead of throwing `OverflowError`.
6. Passing `inf` to `math.log` returns `inf`, which when used in subtracting infinities (`inf - inf`) results in `nan`.
7. This means incorrect prices and sizes (`0.0` and `nan`) are quietly formatted and emitted, leading to corrupt order book snapshots in downstream systems.
8. Similarly, when tick spacing is excessively large, no `try/except` wraps `1.0001 ** (ask_t2 / 2.0)`, letting the `OverflowError` bubble up and crash the normalizer thread/runner.

---

## 3. Caveats
* Testing was performed strictly in isolation on the math functions inside `normalize.py`.
* Network level parsing constraints that might reject inputs before they reach the math level (e.g. schema validations) were not verified.
* All findings are verified locally on a mac system running Python 3.12.

---

## 4. Conclusion

**Overall risk assessment**: HIGH (due to potential for process crashes on invalid ticks/tick spacing and silent corrupt data emission).

### Challenges

#### 1. [High] Fallback Tick Parsing Crash
* **Assumption challenged**: Fallback tick conversion is safe.
* **Attack scenario**: Incoming message with price ratio underflow and `state["tick"] = None` triggers `float(None)`.
* **Blast radius**: Normalization thread crashes, halting ingestion for the affected exchange.
* **Mitigation**: Update fallback parsing to: `safe_float(state.get("tick"), 0.0)`.

#### 2. [High] Exponentiation Overflow Crash
* **Assumption challenged**: Tick spacing remains within reasonable bounds.
* **Attack scenario**: Malicious or invalid pool state updates with extremely large tick spacing (e.g. `10**15`).
* **Blast radius**: Process crashes with `OverflowError`.
* **Mitigation**: Constrain `tickSpacing` using a max limit, e.g., `tick_spacing = max(1, min(tick_spacing, 10000))`.

#### 3. [Medium] Silent Invalid Snapshots
* **Assumption challenged**: Math calculations always throw exceptions on invalid values.
* **Attack scenario**: Overflowing flipped price ratio computes `price_ratio = inf` -> `tick = inf` -> `ask_px = 0.0`, `ask_sz = nan`.
* **Blast radius**: Downstream systems receive invalid data (size is NaN, price is 0.0), potentially causing logic bugs in strategy execution.
* **Mitigation**: Check for `math.isnan(tick)` or `math.isinf(tick)` immediately after log calculation, and discard the update.

#### 4. [Low] Negative Reserves/Liquidity
* **Assumption challenged**: Reserves are positive.
* **Attack scenario**: Negative reserves are passed; fallback calculation results in negative sizes which are capped at `0.0001` by `safe_cap`.
* **Blast radius**: Sub-optimal fallback data (size 0.0001) instead of dropping the update.
* **Mitigation**: Add checks to ensure reserves and liquidity are `>= 0`.

---

## 5. Verification Method

### Execution Command
Execute the newly written test suite utilizing `uv`:
```bash
uv run pytest tests/exchanges/base_onchain/test_challenger_stress_m3.py
```

### Stress Test Results
* **`test_extreme_prices`** -> Discards NaN/Inf/Negative prices; float inputs successfully parsed. -> **PASS**
* **`test_extreme_reserves`** -> Correctly caps reserve sizes or propagates nan/inf reserves as expected. -> **PASS**
* **`test_float_inputs_for_integers`** -> Gracefully parses floats for integers (`decimals`, `tickSpacing`). -> **PASS**
* **`test_flipped_decimals_and_tick_spacing`** -> negative and zero tick spacing coerced to `1`; is_flipped decimals work. -> **PASS**
* **`test_exact_5_levels_depth`** -> Verifies snapshot bids/asks array lengths are exactly 5. -> **PASS**
* **`test_unhandled_type_error_in_tick_fallback`** -> Proves `TypeError` crash when tick is `None` on price ratio underflow. -> **PASS**
* **`test_unhandled_type_error_in_tick_overflow_fallback`** -> Proves silent 0.0 price and NaN size emission on flipped overflow. -> **PASS**
* **`test_overflow_in_tick_power_calculation`** -> Proves `OverflowError` crash when tick spacing is extremely large. -> **PASS**
