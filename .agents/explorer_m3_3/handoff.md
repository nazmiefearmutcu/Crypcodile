# Handoff Report: Milestone 3 Investigation (Multi-Level Orderbook Depth)

## 1. Observation
The following was directly observed in the codebase:
- In `src/crypcodile/exchanges/base_onchain/normalize.py` (lines 120-136), the Uniswap V3 fallback path only appends a single level to the `bids` and `asks` lists:
  ```python
  bid_px = price * 0.9995
  ask_px = price * 1.0005
  bids.append((bid_px, base_bid_sz))
  asks.append((ask_px, base_ask_sz))
  ```
- In the same file (lines 109-112), the Uniswap V3 base size calculation is defined as:
  ```python
  base_sz = liquidity / (10 ** decimals0) if decimals0 else liquidity / 1e18
  ask_sz = base_sz / (5.0 * i)
  bid_sz = base_sz / (5.0 * i)
  ```
- Running the normalizer with a flipped USDC-WETH config (`decimals0 = 6`, `decimals1 = 18`, `is_flipped = True`, and `liquidity = 10**18`) yielded:
  ```
  BookTicker(..., bid_sz=200000000000.0, ask_sz=200000000000.0)
  ```
  While the pool reserves are:
  ```python
  "reserve0": 30000.0, "reserve1": 10.0
  ```
- In `tests/exchanges/base_onchain/test_stress_challenger.py` (lines 56-58), the unit tests assert that fallback snapshots only have 1 level:
  ```python
  assert isinstance(snapshot, BookSnapshot)
  assert snapshot.bids == [(ticker.bid_px, ticker.bid_sz)]
  assert snapshot.asks == [(ticker.ask_px, ticker.ask_sz)]
  ```

---

## 2. Logic Chain
- **Observation 1**: The Uniswap V3 fallback path only yields 1 level of bids/asks.
- **Deduction 1**: This creates a "depth=1 facade" when `"liquidity"` is missing from the state update payload, which violates the interface contract requiring at least 5 levels of bids/asks for both Uniswap V3 and Aerodrome V2 snapshots.
- **Observation 2**: The Uniswap V3 size calculation is based on `base_sz = liquidity / 10**decimals0` and does not factor in the mid-price $\sqrt{P}$ or decimal differences.
- **Observation 3**: Running the USDC-WETH flipped pool update results in an orderbook size of 200 Billion USDC for a pool with only 30,000 USDC of physical reserves.
- **Deduction 2**: The sizing math is mathematically incorrect and yields highly unrealistic numbers that can be off by 7+ orders of magnitude when `is_flipped = True` and token decimals differ significantly.

---

## 3. Caveats
- We assumed the raw `liquidity` and `tick` supplied from the blockchain connector matches the Uniswap V3 slot0 and pool state variables, which we verified by inspecting `connector.py`.
- No actual on-chain transaction execution was analyzed, only the mock update messages processed by the local normalizer.

---

## 4. Conclusion
The current normalizer implementation for Milestone 3 (Multi-level orderbook depth calculations) has critical bugs:
1. It contains a depth=1 fallback facade for Uniswap V3 that violates the interface specifications.
2. The size calculation for Uniswap V3 is mathematically incorrect and produces highly unrealistic depth figures for flipped pools.
Actionable fix strategies have been documented in `/Users/nazmi/Crypcodile/.agents/explorer_m3_3/analysis.md`.

---

## 5. Verification Method
1. To verify the tests passing with the current implementation:
   ```bash
   .venv/bin/pytest tests/exchanges/base_onchain/
   ```
2. To inspect the abnormal size outputs for a flipped USDC-WETH pool, run:
   ```bash
   .venv/bin/python -c '
   from crypcodile.exchanges.base_onchain.normalize import normalize_onchain_update
   msg = {
       "type": "onchain_update",
       "block": 1000,
       "pool": "USDC-WETH",
       "pool_type": "uniswap_v3",
       "timestamp": 1234567890,
       "state": {
           "price": 0.000333,
           "reserve0": 30000.0,
           "reserve1": 10.0,
           "tick": -200000,
           "liquidity": 10**18,
           "tickSpacing": 60,
           "decimals0": 6,
           "decimals1": 18,
           "is_flipped": True
       },
       "swaps": []
   }
   records = list(normalize_onchain_update(msg, 9999))
   print(records[1])
   '
   ```
   This will output extremely large sizes (`200000000000.0`) which confirm the mathematical error.
