## 2026-06-15T00:36:26Z
You are a worker assigned to implement Milestone 3 (Multi-level orderbook depth calculations) in `src/crypcodile/exchanges/base_onchain/normalize.py`.

Refer to the Explorer analysis report for mathematical details:
- /Users/nazmi/Crypcodile/.agents/explorer_m3_1/analysis.md

Your tasks:
1. Implement mathematically correct 5-level orderbook depth calculations in `normalize_onchain_update` inside `src/crypcodile/exchanges/base_onchain/normalize.py`:
   - For Uniswap V3 (with active liquidity): Scale sizes using the square root of prices at the tick boundaries, correctly factoring in `is_flipped` state and decimals.
   - For Aerodrome V2 AND the Uniswap V3 fallback path (no liquidity information): Calculate 5 bids and asks using constant product AMM ($x \cdot y = k$) formulas rather than arbitrary divisions. Ensure the fallback path has 5 levels, removing the depth-1 facade.
2. Ensure robust NaN and Inf checks on incoming prices (prices must be > 0 and not NaN/Inf).
3. Support parameter coercion (handling `None` values or missing decimals/tick spacing parameters robustly).
4. Implement comprehensive unit tests in `tests/exchanges/base_onchain/test_normalize_depth.py` verifying that:
   - BookSnapshots have exactly 5 levels.
   - Prices and sizes are computed correctly and matches the expected constant-product / Uniswap V3 formulas.
   - Flipped token setups calculate sizes and prices correctly.
   - NaN/Inf prices are discarded.
5. Run the unit and E2E test suite to ensure all tests pass.

MANDATORY INTEGRITY WARNING:
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A Forensic Auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

Please document your changes in `/Users/nazmi/Crypcodile/.agents/worker_m3/changes.md` and provide your handoff report in `/Users/nazmi/Crypcodile/.agents/worker_m3/handoff.md` with passing test results.
