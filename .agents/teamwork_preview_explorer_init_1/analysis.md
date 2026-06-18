# Exploration Report: base_onchain Connector Analysis

## Executive Summary
An in-depth investigation of the `base_onchain` exchange connector and the Model Context Protocol (MCP) server has revealed major correctness bugs in price, reserves, and swap decoding for "flipped" DEX pools (pools where the quote token's contract address is smaller than the base token's address). These bugs affect the `cbBTC-USDC`, `DEGEN-WETH`, and `WELL-WETH` pools, leading to trade prices being off by a factor of 10,000, inverted trade sides (BUY/SELL), and swapped asset amounts.

---

## 1. Test Suite Verification
Running the test suite via `uv run pytest` completes successfully:
- **Result**: `602 passed in 5.01s`
- **Observation**: There are currently zero unit or integration tests for `base_onchain` under the `tests/` directory.

---

## 2. Identified Bugs & Vulnerabilities

### Bug A: Flipped Uniswap V3 Pool Price & Decimals Calculation
* **Location**: `src/crypcodile/exchanges/base_onchain/connector.py` (lines 243-249) and `src/crypcodile/mcp_server.py` (lines 110-116)
* **Description**: When resolving Uniswap V3 pools, the base token (token0 in `POOL_SPECS`) and quote token (token1 in `POOL_SPECS`) are sorted to match the EVM address ordering. If the quote token address is smaller than the base token address, the pool is "flipped".
  In the flipped case, the code calculates:
  ```python
  dec_diff = spec["decimals1"] - spec["decimals0"]
  price = (1.0 / price_ratio) * (10 ** dec_diff)
  ```
  However, the exponent factor must scale by `decimals0 - decimals1` (base decimals minus quote decimals) regardless of the EVM address order. Using `decimals1 - decimals0` applies the decimal correction in the wrong direction.
* **Impact**: For `cbBTC-USDC` (cbBTC = 8 decimals, USDC = 6 decimals), `dec_diff` is calculated as `6 - 8 = -2` instead of `8 - 6 = 2`. The price is off by a factor of $10^4$ ($10,000$ times too small).

### Bug B: Uniswap V3 Swap Log Parsing for Flipped Pools
* **Location**: `src/crypcodile/exchanges/base_onchain/connector.py` (lines 292-311)
* **Description**: The swap decoder assumes that `amount0` always corresponds to base (`spec["decimals0"]`) and `amount1` always corresponds to quote (`spec["decimals1"]`). In a flipped pool:
  - `amount0` (contract token0) is actually quote.
  - `amount1` (contract token1) is actually base.
* **Impact**:
  - The trade amount is set to quote instead of base, and scaled using base decimals.
  - The trade price is calculated as `abs1 / abs0` (base/quote) instead of quote/base.
  - `is_buy = amount0 < 0` checks if quote was withdrawn, which swaps BUY and SELL.

### Bug C: Flipped Aerodrome V2 Reserves & Swap Log Parsing
* **Location**: `src/crypcodile/exchanges/base_onchain/connector.py` (lines 264-268 and lines 312-333) and `src/crypcodile/mcp_server.py` (lines 118-122)
* **Description**: For Aerodrome pools, the code does not check if the pool is flipped.
  - In `WELL-WETH`, `WETH` (quote) has a smaller address than `WELL` (base).
  - The contract's `res[0]` corresponds to `WETH` (quote) and `res[1]` corresponds to `WELL` (base).
* **Impact**:
  - `reserve0` is assigned quote reserves scaled by base decimals.
  - `reserve1` is assigned base reserves scaled by quote decimals.
  - Swap logs append quote amount as trade amount, invert `is_buy`, and compute base/quote as price.

### Bug D: Polling Loop Liveness Hang on Close
* **Location**: `src/crypcodile/exchanges/base_onchain/connector.py` (lines 98-104)
* **Description**: `BaseOnchainTransport._iter` performs `await self._queue.get()`. When `close()` cancels the polling task and sets `self._connected = False`, the generator is left waiting on the queue. If the queue is empty, it hangs indefinitely.
* **Impact**: `Connector.run()` hangs during shutdown or connection tear-down.

### Bug E: Duplicate RPC Calls
* **Location**: `src/crypcodile/exchanges/base_onchain/connector.py` (lines 218-358)
* **Description**: The polling loop calls `w3.eth.get_block(current_block)` for each pool individually on every poll iteration. Additionally, it calls `w3.eth.get_block(lg["blockNumber"])` for every swap log, which results in redundant blocking RPC requests.

---

## 3. Mocking & Testing Strategy (Reference to Other Connectors)
Other connectors (Binance, Bybit, Coinbase, Deribit, OKX) use local JSON files stored under `tests/exchanges/<exchange_name>/fixtures/` to mock exchange WebSocket messages and REST responses. They test:
1. Topic subscription matching (`subscribe_channels` logic).
2. De-duplication.
3. Msg normalization dispatching (by reading fixtures and passing them to `normalize`).

Since `base_onchain` is a Web3 connector executing JSON-RPC calls, we need to mock the Web3 instance and Web3 contracts.

### Proposed Test File Structure
We propose introducing `tests/exchanges/base_onchain/test_connector.py` using `unittest.mock` to mock `web3.Web3`, the contract calls, and log queries.

---

## 4. Proposed Fixes (Code Modifications)

### Determining Flipping Safely (EVM Address Sorting)
```python
addr0 = TOKENS[spec["token0"]].lower()
addr1 = TOKENS[spec["token1"]].lower()
is_flipped = addr0 > addr1
```

### Reserves & Price Corrected Parsing
```python
if spec["type"] == "uniswap_v3":
    slot0 = contract.functions.slot0().call()
    liquidity = contract.functions.liquidity().call()
    sqrtPriceX96 = slot0[0]
    price_ratio = (sqrtPriceX96 / (2**96)) ** 2
    
    sqrtP = sqrtPriceX96 / (2**96)
    x_virtual = liquidity / sqrtP if sqrtP > 0 else 0
    y_virtual = liquidity * sqrtP
    
    if not is_flipped:
        reserve0 = x_virtual / (10 ** spec["decimals0"])
        reserve1 = y_virtual / (10 ** spec["decimals1"])
        price = price_ratio * (10 ** (spec["decimals0"] - spec["decimals1"]))
    else:
        reserve0 = y_virtual / (10 ** spec["decimals0"])
        reserve1 = x_virtual / (10 ** spec["decimals1"])
        price = (1.0 / price_ratio) * (10 ** (spec["decimals0"] - spec["decimals1"])) if price_ratio > 0 else 0.0
else:  # aerodrome_v2
    res = contract.functions.getReserves().call()
    if not is_flipped:
        reserve0 = res[0] / (10 ** spec["decimals0"])
        reserve1 = res[1] / (10 ** spec["decimals1"])
    else:
        reserve0 = res[1] / (10 ** spec["decimals0"])
        reserve1 = res[0] / (10 ** spec["decimals1"])
    price = reserve1 / reserve0 if reserve0 > 0 else 0.0
```

### Swaps Corrected Parsing
```python
if spec["type"] == "uniswap_v3":
    amount0 = int.from_bytes(data[0:32], byteorder='big', signed=True)
    amount1 = int.from_bytes(data[32:64], byteorder='big', signed=True)
    
    if not is_flipped:
        abs_base = abs(amount0) / (10 ** spec["decimals0"])
        abs_quote = abs(amount1) / (10 ** spec["decimals1"])
        is_buy = amount0 < 0
    else:
        abs_base = abs(amount1) / (10 ** spec["decimals0"])
        abs_quote = abs(amount0) / (10 ** spec["decimals1"])
        is_buy = amount1 < 0
        
    sw_price = abs_quote / abs_base if abs_base > 0 else 0.0
else:  # aerodrome_v2
    amt0_in = int.from_bytes(data[0:32], byteorder='big', signed=False)
    amt1_in = int.from_bytes(data[32:64], byteorder='big', signed=False)
    amt0_out = int.from_bytes(data[64:96], byteorder='big', signed=False)
    amt1_out = int.from_bytes(data[96:128], byteorder='big', signed=False)
    
    if not is_flipped:
        amt_base = (amt0_in if amt0_in > 0 else amt0_out) / (10 ** spec["decimals0"])
        amt_quote = (amt1_in if amt1_in > 0 else amt1_out) / (10 ** spec["decimals1"])
        is_buy = amt1_in > 0
    else:
        amt_base = (amt1_in if amt1_in > 0 else amt1_out) / (10 ** spec["decimals0"])
        amt_quote = (amt0_in if amt0_in > 0 else amt0_out) / (10 ** spec["decimals1"])
        is_buy = amt0_in > 0
        
    sw_price = amt_quote / amt_base if amt_base > 0 else 0.0
```

### Queue Close Fix
In `connector.py`'s `close()`:
```python
async def close(self) -> None:
    self._connected = False
    if self._poll_task:
        self._poll_task.cancel()
        try:
            await self._poll_task
        except asyncio.CancelledError:
            pass
        self._poll_task = None
    # Put sentinel to unblock queue.get
    await self._queue.put(b"")
```
And inside `_iter`:
```python
async def _iter(self) -> AsyncIterator[bytes]:
    while self._connected or not self._queue.empty():
        try:
            val = await self._queue.get()
            if not val and not self._connected:
                break
            yield val
        except asyncio.CancelledError:
            break
```
