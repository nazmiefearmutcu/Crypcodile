## 2026-06-15T00:10:31Z

You are a worker tasked with implementing the remaining features for the Crypcodile production-ready Base integration:

1. **R2. Robust RPC Rate-Limiting, Retries, and Log Pagination**:
   - In `src/crypcodile/exchanges/base_onchain/connector.py`, implement log pagination. Split the queried block range for logs into chunks of maximum 500 blocks (e.g. using `range(from_block, to_block + 1, 500)`).
   - Implement an exponential backoff retry helper (e.g. `_call_with_retry`) to execute all AsyncWeb3 RPC calls (block number, get_block, slot0, liquidity, getReserves, get_logs) with a retry limit (e.g. 5) and doubling backoff delays.

2. **R3. Realistic Multi-Level Orderbook Depth Calculation**:
   - In `src/crypcodile/exchanges/base_onchain/connector.py`, fetch the `tickSpacing()` for Uniswap V3 pools dynamically from the contract, or derive it from the fee tier (e.g., fee 500 -> 10 spacing, fee 3000 -> 60 spacing, fee 10000 -> 200 spacing), and pass `tickSpacing`, `tick`, and `liquidity` to the update message state payload.
   - In `src/crypcodile/exchanges/base_onchain/normalize.py`, implement a 5-level bids and 5-level asks depth calculation for `BookSnapshot`.
   - For Uniswap V3 pools: generate price levels using active tick and tick spacing (bids at `tick - i * spacing`, asks at `tick + i * spacing` for i in [1..5]) using standard `1.0001**tick` calculation (adjusting for flipped pools and decimals). Distribute sizes realistically using liquidity (e.g., `liquidity / 10**decimals` divided across levels, potentially decreasing size for outer levels).
   - For Aerodrome V2 pools: generate 5 levels using spread multipliers (e.g., steps of 0.05% price adjustment) and distribute reserves realistically.

3. **R5. Extensible Configuration for Custom Symbols**:
   - In `src/crypcodile/exchanges/base_onchain/connector.py`, modify `__init__` of `BaseOnchainConnector` to accept an optional `custom_pools: dict[str, dict[str, Any]] | None = None` parameter.
   - Dynamically register any custom pools provided in `custom_pools` into the global `POOL_SPECS` and `TOKENS` dictionaries inside the initialization logic.
   - Update `list_instruments` to dynamically return `Instrument` records based on the symbols that are actually configured (from both default and custom pools) rather than returning a hardcoded list.

4. **Verify Tests and Build**:
   - Add unit tests verifying log pagination, retries, multi-level orderbook depth, and custom pool configuration.
   - Run the full test suite (`uv run pytest`) and ensure 100% of tests pass.
   - Run `uv build` to verify the build completes successfully.

Your working directory is `/Users/nazmi/Crypcodile/.agents/worker_implementation_1`.
Please create your own BRIEFING.md and progress.md.
Report all changes, test execution outputs, and build verification outputs in `/Users/nazmi/Crypcodile/.agents/worker_implementation_1/handoff.md`.
Then send a message back to your parent.
