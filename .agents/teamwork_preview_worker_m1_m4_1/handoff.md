# Handoff Report

## 1. Observation
- Modified files:
  - `src/crypcodile/exchanges/base_onchain/connector.py`
  - `src/crypcodile/mcp_server.py`
  - `src/crypcodile/api_server.py`
  - `tests/exchanges/base_onchain/test_connector.py`
  - `examples/collect_base_onchain.py`
  - `pyproject.toml`
  - `README.md`
- Running `uv run pytest` yielded the following output:
```
608 passed, 1 warning in 5.21s
```
- Running `uv run python examples/collect_base_onchain.py --dry-run` yielded:
```
2026-06-14 17:11:35,133 INFO collect_base_onchain  Initializing BaseOnchainConnector. RPC URL: https://base-rpc.publicnode.com
2026-06-14 17:11:35,386 INFO crypcodile.exchanges.base_onchain.connector  base_onchain: Resolved pool cbBTC-USDC to 0xMockPoolAddress (flipped: True)
2026-06-14 17:11:35,386 INFO collect_base_onchain  Dry run complete. Printed 3 records.
[Trade] Trade(exchange='base_onchain', symbol='base_onchain:cbBTC-USDC', symbol_raw='cbBTC-USDC', exchange_ts=1234567890000000000, local_ts=1781446295386752000, id='0xhash-1', price=0.16666666666666666, amount=600.0, side=<Side.SELL: 'sell'>, liquidation=None)
[BookTicker] BookTicker(exchange='base_onchain', symbol='base_onchain:cbBTC-USDC', symbol_raw='cbBTC-USDC', exchange_ts=1234567890000000000, local_ts=1781446295386752000, bid_px=44.42222222222224, bid_sz=749.9999999999999, ask_px=44.46666666666666, ask_sz=750.0, update_id=12345)
[BookSnapshot] BookSnapshot(exchange='base_onchain', symbol='base_onchain:cbBTC-USDC', symbol_raw='cbBTC-USDC', exchange_ts=1234567890000000000, local_ts=1781446295386752000, bids=[(44.42222222222224, 749.9999999999999)], asks=[(44.46666666666666, 750.0)], depth=1, sequence_id=12345, is_snapshot=True)
```
- Running `uv build` succeeded with:
```
Building source distribution...
Building wheel from source distribution...
Successfully built dist/crypcodile-0.1.0.tar.gz
Successfully built dist/crypcodile-0.1.0-py3-none-any.whl
```
- Running `uv run ruff check .` outputted:
```
All checks passed!
```

## 2. Logic Chain
1. We observed that standard Uniswap V3 and Aerodrome V2 pools sort token contract addresses on-chain by address value (token0 has a smaller address than token1).
2. For flipped pools (where quote token address < base token address, e.g. cbBTC-USDC or WELL-WETH), on-chain token0 is the quote and token1 is the base.
3. Therefore:
   - Pricing: standard V3 uses `price = price_ratio * 10**(decimals0 - decimals1)`, flipped V3 uses `price = (1.0 / price_ratio) * 10**(decimals0 - decimals1)`.
   - Reserves: standard V2 uses `reserve0 = res[0]/10**decimals0`, `reserve1 = res[1]/10**decimals1`; flipped V2 uses `reserve0 = res[1]/10**decimals0`, `reserve1 = res[0]/10**decimals1`.
   - Swap decoding: Trades sides are determined by checks on the base token balance change (`amount0 < 0` for standard, `amount1 < 0` for flipped in V3; and `amt0_out > 0` for standard, `amt1_out > 0` for flipped in Aerodrome V2).
4. Polling loop hang occurred because `_iter` was awaiting `self._queue.get()` indefinitely when `close()` was called. Putting `None` (sentinel) in the queue forces `_iter` to wake up and exit.
5. Caching block timestamps was implemented via `_block_cache` in the transport, evicting after 1000 entries, preventing redundant RPC calls.

## 3. Caveats
- No live network calls were tested during the unit test suite; all testing is simulated offline via Web3 mock contracts. Live testing is supported by running `collect_base_onchain.py` without the `--dry-run` flag.

## 4. Conclusion
- All issues specified in the user request have been successfully fixed and verified. The codebase is clean of any formatting or lint warnings/errors.

## 5. Verification Method
- Execute the test suite:
  ```bash
  uv run pytest tests/exchanges/base_onchain/test_connector.py
  ```
- Run the showcase script dry-run:
  ```bash
  uv run python examples/collect_base_onchain.py --dry-run
  ```
- Build the package:
  ```bash
  uv build
  ```
