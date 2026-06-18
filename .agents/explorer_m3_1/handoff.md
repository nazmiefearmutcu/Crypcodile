# Handoff Report: Milestone 3 Investigation

## 1. Observation
- File analyzed: `src/crypcodile/exchanges/base_onchain/normalize.py`
  - Fallback path for `uniswap_v3` (lines 120-137):
    ```python
    elif pool_type == "uniswap_v3":
        # Fallback for Uniswap V3 without liquidity info (1 level)
        ...
        bids.append((bid_px, base_bid_sz))
        asks.append((ask_px, base_ask_sz))
    ```
    This appends only 1 level to the `bids` and `asks` lists.
  - Active path for `uniswap_v3` (lines 109-112):
    ```python
    base_sz = liquidity / (10 ** decimals0) if decimals0 else liquidity / 1e18
    ask_sz = base_sz / (5.0 * i)
    bid_sz = base_sz / (5.0 * i)
    ```
    Here, the code does not scale `liquidity` by the price square root $\sqrt{p_{\text{raw}}}$ to calculate the virtual reserves, and it always divides by `10 ** decimals0` regardless of `is_flipped`.
  - Aerodrome V2 size math (lines 155-156):
    ```python
    ask_sz = base_ask_sz / (5.0 * i)
    bid_sz = base_bid_sz / (5.0 * i)
    ```
    Where `base_ask_sz = safe_cap(reserve_token0)` and `base_bid_sz = safe_cap(reserve_token1 / price)`.
- Test file analyzed: `tests/exchanges/base_onchain/test_stress_challenger.py`
  - In `test_normalize_standard_case` (lines 13-59), a simulated `uniswap_v3` message is passed without `liquidity` info and asserts a 1-level list is returned:
    ```python
    assert snapshot.bids == [(ticker.bid_px, ticker.bid_sz)]
    assert snapshot.asks == [(ticker.ask_px, ticker.ask_sz)]
    ```
  - Running command: `uv run pytest tests/exchanges/base_onchain/`
    - Result: `53 passed, 1 warning in 1.69s` (Exit code: 0)

---

## 2. Logic Chain
- **Depth-1 Facade**: 
  - Observation: `normalize.py` fallback path for Uniswap V3 appends exactly one bid and ask to `bids` and `asks` lists (lines 135-136).
  - Observation: `PROJECT.md` interface contract states: *"For Uniswap V3 and Aerodrome V2, BookSnapshot must provide at least 5 bid and 5 ask levels calculated using tick/reserves math."*
  - Inference: When active liquidity is missing, Uniswap V3 updates produce a `depth=1` snapshot, violating the interface contract constraint of providing at least 5 levels.
- **Inaccurate Uniswap V3 Sizes**:
  - Observation: Active Uniswap V3 size calculation is `liquidity / 10**decimals0` (line 109).
  - Fact: In Uniswap V3, raw liquidity $L$ is a density parameter related to virtual reserves by $X_{\text{virtual}} = L / \sqrt{P_{\text{raw}}}$ and $Y_{\text{virtual}} = L \cdot \sqrt{P_{\text{raw}}}$.
  - Inference: Omitting the $\sqrt{P_{\text{raw}}}$ scaling factor causes orderbook sizes to be mathematically wrong, underestimating WETH sizes by up to 100,000x and overestimating USDC sizes by 3,600x.
- **Unrealistic Aerodrome V2 Sizes**:
  - Observation: Aerodrome V2 size is `reserve0 / (5.0 * i)` at level $i$. Summed over 5 levels, this represents 45.7% of the pool's entire reserves.
  - Fact: In a real constant product AMM ($x \cdot y = k$), a price change of 0.25% (cumulative spread of the 5 levels) corresponds to $\approx 0.125\%$ of reserves swapped.
  - Inference: The current heuristic overestimates orderbook depth by a factor of ~360x to 800x, making the synthetic depth highly unrealistic for pricing and execution simulations.

---

## 3. Caveats
- The custom pools defined via JSON IPC were not analyzed dynamically under live RPC conditions since we are operating in `CODE_ONLY` network mode. We assume the mock schemas in tests represent normal production logs accurately.
- No other caveats.

---

## 4. Conclusion
The normalizer implementation for Milestone 3 is **incomplete and mathematically incorrect**:
1. It contains a depth-1 facade in the Uniswap V3 fallback path.
2. The Uniswap V3 active sizing math is off by orders of magnitude because it ignores the price square root.
3. The Aerodrome V2 sizing math overestimates available liquidity by up to 800x.
4. There is a complete lack of unit/integration tests verifying the 5-level orderbook snapshots.

We recommend replacing the heuristic sizing math with exact constant product and Uniswap V3 tick-interval math, integrating the fallback path to generate 5 levels, and adding tests for 5-level depth updates.

---

## 5. Verification Method
- **Command to run**: `uv run pytest tests/exchanges/base_onchain/` to verify existing tests continue to pass.
- **Code review**: Inspect `/Users/nazmi/Crypcodile/.agents/explorer_m3_1/analysis.md` for the detailed mathematics and patch proposals.
