# Codebase Analysis: Native AsyncWeb3 Refactoring (connector.py and mcp_server.py)

## 1. Current Web3 Architecture & Blocking Operations

We analyzed the current implementation of on-chain operations within the `crypcodile` codebase. We found that the project relies on the synchronous `web3.Web3` client and `web3.HTTPProvider`, executing network queries inside an event loop by wrapping them in `asyncio.to_thread`. While this avoids blocking the main event loop, it is inefficient because it spawns background threads for operations that are fundamentally I/O bound.

---

## 2. Examination of `src/crypcodile/exchanges/base_onchain/connector.py`

### Observations
1. **Instantiation**: Inside `BaseOnchainTransport._poll_loop` (lines 132-134), `w3` is initialized as a synchronous Web3 instance using a synchronous `HTTPProvider`:
   ```python
   from web3 import Web3
   w3 = Web3(Web3.HTTPProvider(self.rpc_url))
   ```
2. **Synchronous Calls Wrapped in `asyncio.to_thread`**:
   - **Dynamic Pool Address Resolution** (lines 221-223 & 230-232):
     ```python
     pool_addr = await asyncio.to_thread(
         factory.functions.getPool(sorted_t0, sorted_t1, fee).call
     )
     ...
     pool_addr = await asyncio.to_thread(
         factory.functions.getPool(t0_addr, t1_addr, stable).call
     )
     ```
   - **Current Block Number** (line 252):
     ```python
     current_block = await asyncio.to_thread(lambda: w3.eth.block_number)
     ```
   - **Pool State Query (Uniswap V3)** (lines 272-273):
     ```python
     slot0 = await asyncio.to_thread(contract.functions.slot0().call)
     liquidity = await asyncio.to_thread(contract.functions.liquidity().call)
     ```
   - **Pool State Query (Aerodrome V2)** (line 302):
     ```python
     res = await asyncio.to_thread(contract.functions.getReserves().call)
     ```
   - **Log Retrieval** (lines 318-326):
     ```python
     logs = await asyncio.to_thread(
         w3.eth.get_logs,
         {
             "address": addr,
             "fromBlock": self._last_block + 1,
             "toBlock": current_block,
             "topics": [swap_topic]
         }
     )
     ```
   - **Block Timestamp Cache Resolution** (line 96):
     ```python
     blk = await asyncio.to_thread(w3.eth.get_block, block_number)
     ```

### Proposed Refactoring for `connector.py`
1. Replace `from web3 import Web3` with `from web3 import AsyncHTTPProvider, AsyncWeb3, Web3`. (Keeping `Web3` is useful for utility functions such as `Web3.to_checksum_address`).
2. Instantiate `w3` natively as:
   ```python
   w3 = AsyncWeb3(AsyncHTTPProvider(self.rpc_url))
   ```
3. Convert all wrapped sync Web3 calls to native awaitable calls:
   - `await factory.functions.getPool(...).call()`
   - `await w3.eth.block_number`
   - `await contract.functions.slot0().call()`
   - `await contract.functions.liquidity().call()`
   - `await contract.functions.getReserves().call()`
   - `await w3.eth.get_logs(...)`
   - `await w3.eth.get_block(block_number)`

---

## 3. Examination of `src/crypcodile/mcp_server.py`

### Observations
1. **Import**: At line 10, it imports `from web3 import Web3`.
2. **Synchronous Function**: The helper function `get_onchain_price` is defined synchronously (lines 77-158):
   ```python
   def get_onchain_price(symbol: str, rpc_url: str = DEFAULT_RPC_URL) -> dict[str, Any]:
   ```
   It performs blocking synchronous Web3 calls directly in the main event loop thread without `asyncio.to_thread`:
   - Line 84: `w3 = Web3(Web3.HTTPProvider(rpc_url))`
   - Line 95: `pool_addr = factory.functions.getPool(...).call()`
   - Line 101: `pool_addr = factory.functions.getPool(...).call()`
   - Line 114: `slot0 = pool_contract.functions.slot0().call()`
   - Line 115: `liquidity = pool_contract.functions.liquidity().call()`
   - Line 138: `res = pool_contract.functions.getReserves().call()`
   - Line 154: `"block": w3.eth.block_number`
3. **Execution**: The server executes `get_onchain_price` in the stdio loop within `serve_stdio`:
   ```python
   if tool_name == "get_onchain_price":
       sym = arguments.get("symbol", "")
       tool_result = get_onchain_price(sym)
   ```
   Because `get_onchain_price` is synchronous and does not yield control, any active RPC network request will block the entire MCP server's JSON-RPC process, leading to potential lag or timeouts.

### Proposed Refactoring for `mcp_server.py`
1. Change `get_onchain_price` signature to async:
   ```python
   async def get_onchain_price(symbol: str, rpc_url: str = DEFAULT_RPC_URL) -> dict[str, Any]:
   ```
2. Import `AsyncHTTPProvider` and `AsyncWeb3` alongside `Web3`.
3. Instantiate `w3 = AsyncWeb3(AsyncHTTPProvider(rpc_url))`.
4. Await all contract calls and Eth calls:
   - `await factory.functions.getPool(...).call()`
   - `await pool_contract.functions.slot0().call()`
   - `await pool_contract.functions.liquidity().call()`
   - `await pool_contract.functions.getReserves().call()`
   - `await w3.eth.block_number`
5. Await `get_onchain_price` inside `serve_stdio`:
   ```python
   tool_result = await get_onchain_price(sym)
   ```

---

## 4. Analysis of Existing Tests & Mocking Web3

All 4 test files under `tests/exchanges/base_onchain/` mock Web3 functionality synchronously. We need to refactor them to use `AsyncMock` and return proper coroutines for awaited properties and calls.

### `tests/exchanges/base_onchain/test_connector.py`
1. **Mock Patching**:
   - Change `patch("web3.Web3")` to `patch("web3.AsyncWeb3")`.
2. **Mock setup**:
   - `mock_w3.eth.block_number` is accessed as an awaitable property. We must set it using `PropertyMock` with an async `side_effect` or returning a coroutine:
     ```python
     async def mock_block_number():
         return 1000
     type(mock_w3.eth).block_number = PropertyMock(side_effect=mock_block_number)
     ```
   - `mock_w3.eth.get_block` and `mock_w3.eth.get_logs` must be mapped to `AsyncMock`:
     ```python
     mock_w3.eth.get_logs = AsyncMock(return_value=[mock_log])
     mock_w3.eth.get_block = AsyncMock(return_value={"timestamp": 1234567890})
     ```
   - Contract function calls like `.call()` must be mocked as async:
     ```python
     mock_factory.functions.getPool.return_value.call = AsyncMock(return_value="...")
     mock_pool.functions.slot0.return_value.call = AsyncMock(return_value=[...])
     mock_pool.functions.liquidity.return_value.call = AsyncMock(return_value=...)
     mock_pool.functions.getReserves.return_value.call = AsyncMock(return_value=[...])
     ```

### `tests/exchanges/base_onchain/test_adversarial.py`
- Follows the same pattern as `test_connector.py` for mocking Web3.
- `mock_w3.eth.block_number` needs to raise a connection exception to test resilience. We can set `side_effect=Exception("RPC connection refused")` on an `AsyncMock` property.
- `mock_w3.eth.get_logs` must be configured with `side_effect=Exception("Log server offline")` as an `AsyncMock`.

### `tests/exchanges/base_onchain/test_challenger_stress_2.py` & `test_challenger_stress_3.py`
- These files define custom mock classes: `SleepyMockWeb3`, `LaggingMockWeb3` and their respective helper contract and eth classes.
- Since `connector.py` will use `await` on their return values, all these mock methods (e.g. `call()`, `get_block()`, `get_logs()`) and properties (`block_number`) must be refactored to be asynchronous (prefix with `async def`).
- Replace `time.sleep(delay)` with `await asyncio.sleep(delay)` in `SleepyMockWeb3` classes to avoid blocking tests and allow proper event loop task interleaving.
