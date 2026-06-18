# Handoff Report: Milestone 1 - Native AsyncWeb3 Refactoring

## 1. Observation

During our codebase exploration, we identified multiple synchronous Web3 calls and synchronous mock structures. 

### Target 1: `src/crypcodile/exchanges/base_onchain/connector.py`
Synchronous calls are wrapped in `asyncio.to_thread` throughout the `BaseOnchainTransport` class:
- **Block Timestamp Retrieval** (Line 96):
  ```python
  blk = await asyncio.to_thread(w3.eth.get_block, block_number)
  ```
- **Web3 Instantiation** (Line 134):
  ```python
  w3 = Web3(Web3.HTTPProvider(self.rpc_url))
  ```
- **Factory Address Resolution** (Lines 221-223, 230-232):
  ```python
  pool_addr = await asyncio.to_thread(
      factory.functions.getPool(sorted_t0, sorted_t1, fee).call
  )
  ```
- **Block Number Retrieval** (Line 252):
  ```python
  current_block = await asyncio.to_thread(lambda: w3.eth.block_number)
  ```
- **Pool state details** (Lines 272-273, 302):
  ```python
  slot0 = await asyncio.to_thread(contract.functions.slot0().call)
  liquidity = await asyncio.to_thread(contract.functions.liquidity().call)
  ...
  res = await asyncio.to_thread(contract.functions.getReserves().call)
  ```
- **Logs query** (Lines 318-326):
  ```python
  logs = await asyncio.to_thread(
      w3.eth.get_logs,
      { ... }
  )
  ```

### Target 2: `src/crypcodile/mcp_server.py`
The helper function `get_onchain_price` is synchronous and blocks the MCP server event loop thread:
- **Web3 Instantiation** (Line 84):
  ```python
  w3 = Web3(Web3.HTTPProvider(rpc_url))
  ```
- **Synchronous Calls** (Lines 95, 101, 114, 115, 138, 154):
  - `pool_addr = factory.functions.getPool(...).call()`
  - `slot0 = pool_contract.functions.slot0().call()`
  - `liquidity = pool_contract.functions.liquidity().call()`
  - `res = pool_contract.functions.getReserves().call()`
  - `"block": w3.eth.block_number`
- **Call invocation in STDIO loop** (Line 282):
  ```python
  tool_result = get_onchain_price(sym)
  ```

### Target 3: Tests (`tests/exchanges/base_onchain/`)
Existing tests mock `web3.Web3` synchronously. For example, in `test_connector.py`:
- **Web3 Patching** (Line 69):
  ```python
  patch("web3.Web3") as mock_web3_class
  ```
- **Sync Property/Method Mocking** (Lines 75, 79, 88, 91, 110, 113):
  - `mock_w3.eth.block_number = 1000`
  - `mock_factory.functions.getPool.return_value.call.return_value = ...`
  - `mock_pool.functions.slot0.return_value.call.return_value = ...`
- **Custom Mock Classes** (`test_challenger_stress_2.py` / `test_challenger_stress_3.py`):
  - `SleepyMockWeb3`, `LaggingMockWeb3`, and contract wrappers are fully synchronous and use `time.sleep()`.

---

## 2. Logic Chain

1. **Elimination of Thread Pools**: The use of `asyncio.to_thread` spawns OS threads to execute blocking sync network queries. By migrating to `AsyncWeb3` and `AsyncHTTPProvider`, these network queries can run directly on python's `asyncio` event loop using non-blocking sockets, which reduces context switching overhead and improves resource efficiency.
2. **MCP Server Responsiveness**: The MCP server listens for stdin/stdout JSON-RPC commands. Any invocation of the synchronous `get_onchain_price` completely blocks the single-threaded server process. Making this function async (`async def`) and awaiting calls (`await`) ensures the server remains responsive to other commands.
3. **Test Correction Requirement**: If source code is refactored to use `await`, any test that returns a sync mock value will crash with `TypeError: object ... can't be used in 'await' expression`. Mocks must therefore be updated to return coroutines (using `AsyncMock`) and properties (like `w3.eth.block_number`) must be patched with a `PropertyMock` returning a coroutine dynamically to avoid the "already awaited coroutine" exception during multiple loop iterations.

---

## 3. Caveats

- **Python Version**: Tested on Python 3.12, where `AsyncMock` is natively supported. The environment must use Python 3.8+ to support `AsyncMock`.
- **DuckDB Sync Queries**: The DuckDB query tool in `mcp_server.py` (`query_market_data`) remains synchronous, but this is outside the scope of Milestone 1.

---

## 4. Conclusion

We conclude that the refactoring of `connector.py` and `mcp_server.py` to natively support `AsyncWeb3` is highly recommended, feasible, and requires refactoring both source and test modules. 

Below is the step-by-step fix strategy for the implementer:

### Step-by-Step Fix Strategy

#### Step 1: Refactor `src/crypcodile/exchanges/base_onchain/connector.py`
1. Update imports inside `BaseOnchainTransport._poll_loop` (Line 132):
   ```python
   from web3 import AsyncHTTPProvider, AsyncWeb3, Web3
   ```
2. Modify instantiation of the web3 provider (Line 134):
   ```python
   w3 = AsyncWeb3(AsyncHTTPProvider(self.rpc_url))
   ```
3. Update `_get_block_timestamp` (Line 96):
   ```python
   blk = await w3.eth.get_block(block_number)
   ```
4. Update dynamic pool resolution (Lines 221-223, 230-232):
   ```python
   pool_addr = await factory.functions.getPool(sorted_t0, sorted_t1, fee).call()
   # ... and
   pool_addr = await factory.functions.getPool(t0_addr, t1_addr, stable).call()
   ```
5. Update current block retrieval (Line 252):
   ```python
   current_block = await w3.eth.block_number
   ```
6. Update Uniswap V3 pool details querying (Lines 272-273):
   ```python
   slot0 = await contract.functions.slot0().call()
   liquidity = await contract.functions.liquidity().call()
   ```
7. Update Aerodrome V2 pool reserves querying (Line 302):
   ```python
   res = await contract.functions.getReserves().call()
   ```
8. Update get logs query (Lines 318-326):
   ```python
   logs = await w3.eth.get_logs(
       {
           "address": addr,
           "fromBlock": self._last_block + 1,
           "toBlock": current_block,
           "topics": [swap_topic]
       }
   )
   ```

#### Step 2: Refactor `src/crypcodile/mcp_server.py`
1. Update imports at line 10:
   ```python
   from web3 import AsyncHTTPProvider, AsyncWeb3, Web3
   ```
2. Refactor `get_onchain_price` to `async def` and update queries (Lines 77-158):
   ```python
   async def get_onchain_price(symbol: str, rpc_url: str = DEFAULT_RPC_URL) -> dict[str, Any]:
       # ...
       w3 = AsyncWeb3(AsyncHTTPProvider(rpc_url))
       # ...
       pool_addr = await factory.functions.getPool(...).call()
       # ...
       slot0 = await pool_contract.functions.slot0().call()
       liquidity = await pool_contract.functions.liquidity().call()
       # ...
       res = await pool_contract.functions.getReserves().call()
       # ...
       block_num = await w3.eth.block_number
   ```
3. Await `get_onchain_price` in `serve_stdio` (Line 282):
   ```python
   tool_result = await get_onchain_price(sym)
   ```

#### Step 3: Refactor Tests in `tests/exchanges/base_onchain/`

##### A. In `test_connector.py` and `test_adversarial.py`
1. Change mock patching targets:
   ```python
   patch("web3.Web3")  ->  patch("web3.AsyncWeb3")
   ```
2. Mock `w3.eth.block_number` as an awaitable property:
   ```python
   async def mock_block_number():
       return 1000
   type(mock_w3.eth).block_number = PropertyMock(side_effect=mock_block_number)
   ```
3. Mock `get_block` and `get_logs` as `AsyncMock`:
   ```python
   mock_w3.eth.get_block = AsyncMock(return_value={"timestamp": 1234567890})
   mock_w3.eth.get_logs = AsyncMock(return_value=[mock_log])
   ```
   *(For adversarial tests, set exceptions on `AsyncMock`'s `side_effect` instead).*
4. Update contract function `.call()` mocking:
   ```python
   mock_factory.functions.getPool.return_value.call = AsyncMock(return_value="0xMockAddress")
   mock_pool.functions.slot0.return_value.call = AsyncMock(return_value=[...])
   mock_pool.functions.liquidity.return_value.call = AsyncMock(return_value=...)
   mock_pool.functions.getReserves.return_value.call = AsyncMock(return_value=[...])
   ```

##### B. In `test_challenger_stress_2.py` and `test_challenger_stress_3.py`
1. Refactor mock helper classes (`SleepyMockWeb3`, `SleepyMockEth`, `LaggingMockWeb3`, etc.) to use async functions:
   ```python
   class SleepyMockEth:
       @property
       async def block_number(self):
           return self._block_number

       async def get_block(self, block_num):
           if self.parent.get_block_delay > 0:
               await asyncio.sleep(self.parent.get_block_delay)
           return {"timestamp": 1600000000 + block_num}

       async def get_logs(self, filter_params):
           if self.parent.get_logs_delay > 0:
               await asyncio.sleep(self.parent.get_logs_delay)
           return []
   ```
2. Make `Call.call` methods async inside custom Contract Mock functions:
   ```python
   async def call(self):
       return ...
   ```
3. Update `test_block_cache_memory_efficiency` in `test_challenger_stress_3.py` to use an `AsyncMock` for `get_block`:
   ```python
   mock_w3 = MagicMock()
   async def mock_get_block(block_num):
       return {"timestamp": 1600000000 + block_num}
   mock_w3.eth.get_block = AsyncMock(side_effect=mock_get_block)
   ```

---

## 5. Verification Method

To verify the correct execution and compliance of the refactoring steps:
1. Run the test command:
   ```bash
   .venv/bin/pytest tests/exchanges/base_onchain/
   ```
2. Inspect test coverage to ensure no tests were deleted or disabled.
3. Verify that the return types of all mock methods are awaitables.
