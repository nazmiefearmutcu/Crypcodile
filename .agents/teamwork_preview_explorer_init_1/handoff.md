# Handoff Report

## 1. Observation
- **File Paths and Lines**:
  - `src/crypcodile/exchanges/base_onchain/connector.py`, lines 243-249:
    ```python
    sorted_tokens = sorted([TOKENS[spec["token0"]], TOKENS[spec["token1"]]])
    if sorted_tokens[0] == TOKENS[spec["token0"]]:
        dec_diff = spec["decimals0"] - spec["decimals1"]
        price = price_ratio * (10 ** dec_diff)
    else:
        dec_diff = spec["decimals1"] - spec["decimals0"]
        price = (1.0 / price_ratio) * (10 ** dec_diff) if price_ratio > 0 else 0.0
    ```
  - `src/crypcodile/exchanges/base_onchain/connector.py`, lines 292-311:
    ```python
    if spec["type"] == "uniswap_v3":
        # Decode Swap(address,address,int256,int256,uint160,uint128,int24)
        amount0 = int.from_bytes(data[0:32], byteorder='big', signed=True)
        amount1 = int.from_bytes(data[32:64], byteorder='big', signed=True)
        
        abs0 = abs(amount0) / (10 ** spec["decimals0"])
        abs1 = abs(amount1) / (10 ** spec["decimals1"])
        
        sw_price = abs1 / abs0 if abs0 > 0 else 0.0
        # If amount0 is negative, token0 was bought (bought using token1)
        is_buy = amount0 < 0
    ```
  - `src/crypcodile/exchanges/base_onchain/connector.py`, lines 264-268:
    ```python
    else: # aerodrome_v2
        res = contract.functions.getReserves().call()
        reserve0 = res[0] / (10 ** spec["decimals0"])
        reserve1 = res[1] / (10 ** spec["decimals1"])
        price = reserve1 / reserve0 if reserve0 > 0 else 0.0
    ```
  - `src/crypcodile/exchanges/base_onchain/connector.py`, lines 312-333:
    ```python
    else: # aerodrome_v2
        # Decode Swap(address,address,uint256,uint256,uint256,uint256)
        amt0_in = int.from_bytes(data[0:32], byteorder='big', signed=False)
        amt1_in = int.from_bytes(data[32:64], byteorder='big', signed=False)
        amt0_out = int.from_bytes(data[64:96], byteorder='big', signed=False)
        amt1_out = int.from_bytes(data[96:128], byteorder='big', signed=False)
        
        # Token0 amount traded
        amt0 = (amt0_in if amt0_in > 0 else amt0_out) / (10 ** spec["decimals0"])
        amt1 = (amt1_in if amt1_in > 0 else amt1_out) / (10 ** spec["decimals1"])
        
        sw_price = amt1 / amt0 if amt0 > 0 else 0.0
        is_buy = amt1_in > 0 # token1 deposited to buy token0
    ```
  - `src/crypcodile/exchanges/base_onchain/connector.py`, lines 95-104:
    ```python
    async def _iter(self) -> AsyncIterator[bytes]:
        while self._connected or not self._queue.empty():
            try:
                val = await self._queue.get()
                yield val
            except asyncio.CancelledError:
                break
    ```
  - `src/crypcodile/mcp_server.py`, lines 110-122.
  - **Pytest Output**:
    ```
    602 passed in 5.01s
    ```
  - **Directories**: No existing base_onchain unit or integration tests exist under the `tests/` folder.

---

## 2. Logic Chain
1. Uniswap V3 and Aerodrome V2 sort pool token addresses numerically (or case-insensitively alphabetically).
2. If the quote token address is smaller than the base token address, `token0` of the contract will be the quote token, and `token1` of the contract will be the base token. This is the case for `cbBTC-USDC`, `DEGEN-WETH`, and `WELL-WETH` in our codebase.
3. In `connector.py` and `mcp_server.py`, the price calculation for a flipped Uniswap V3 pool uses the exponent factor `decimals1 - decimals0`. Since it is flipped, the raw ratio is `base_raw / quote_raw`. The correct real price is `quote_real / base_real = (1.0 / price_ratio) * 10**(decimals0 - decimals1)`. Using `decimals1 - decimals0` flips the scaling factor.
4. For Uniswap V3 swaps, the code maps `amount0` directly to `decimals0` (base) and `amount1` directly to `decimals1` (quote). For a flipped pool, `amount0` is quote and `amount1` is base, so their decimal scaling, swap price, swap amount, and buy/sell side logic are completely inverted.
5. For Aerodrome V2 pools, the code has no check for flipped status, assuming `res[0]` is always base and `res[1]` is quote, which is false for `WELL-WETH`.
6. For queue iteration, `close()` stops the polling loop but leaves the async generator blocked on `await self._queue.get()`. If the queue is empty, this hangs indefinitely, blocking cleanup and shutdown.

---

## 3. Caveats
- No actual on-chain executions were made (we did not query live Base nodes during this investigation).
- Assumed standard Uniswap V3 and Aerodrome V2 Smart Contract layouts (which is true on-chain).
- The proposed fixes should be verified by the implementer through tests or mock runs.

---

## 4. Conclusion
The `base_onchain` connector has critical correctness and liveness bugs:
1. Decimal correction factor is flipped for `cbBTC-USDC` and `DEGEN-WETH` pricing.
2. Swap logs are incorrectly decoded (flipped amounts, flipped prices, and flipped trade sides) for all pools where quote address is smaller than base address.
3. Aerodrome V2 pools lack flipped-detection, breaking `WELL-WETH` state and swaps.
4. Transport queue close is blocked indefinitely when empty.

These must be addressed before running the connector on live data. We propose updating the sorting/price/swap logic to use an `is_flipped` flag, caching block timestamps to minimize duplicate RPC requests, and writing a mock-based test suite.

---

## 5. Verification Method
- Execute the updated codebase tests via `uv run pytest`.
- Add a new test file `tests/exchanges/base_onchain/test_connector.py` that mocks the Web3 calls (e.g. contract `slot0()`, `getReserves()`, `get_logs()`, `get_block()`) and validates that price, reserves, and swap fields match expected values.
