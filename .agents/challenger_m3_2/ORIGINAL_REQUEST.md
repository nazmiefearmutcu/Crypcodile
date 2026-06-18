## 2026-06-15T00:40:42+03:00
Conduct adversarial testing and stress testing on the Milestone 3 orderbook math implementation in `src/crypcodile/exchanges/base_onchain/normalize.py` using the worker's changes.
Verify the edge cases of:
1. Extreme prices/reserves (underflows, overflows, NaN, positive/negative infinity, float inputs for integers).
2. Flipped pool decimals, negative tick spacing, and zero tick spacing configurations.
3. Assert that the depth is exactly 5 levels.
Report findings and test execution results in `/Users/nazmi/Crypcodile/.agents/challenger_m3_2/handoff.md`.
