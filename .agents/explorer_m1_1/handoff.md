# Handoff Report - Milestone 1: Native AsyncWeb3 Refactoring

## 1. Observation
We examined the codebase and observed the following:

### A. Synchronous Calls in `src/crypcodile/exchanges/base_onchain/connector.py`
Inside `BaseOnchainTransport._poll_loop` (lines 131–443):
- Instantiates synchronous Web3:
  ```python
  134:         w3 = Web3(Web3.HTTPProvider(self.rpc_url))
  ```
- Uses `asyncio.to_thread` to wrap synchronous Web3 properties, contract function calls, and log queries:
  ```python
  96:             blk = await asyncio.to_thread(w3.eth.get_block, block_number)
  ...
  221:                             pool_addr = await asyncio.to_thread(
  222:                                 factory.functions.getPool(sorted_t0, sorted_t1, fee).call
  223:                             )
  ...
  252:                 current_block = await asyncio.to_thread(lambda: w3.eth.block_number)
  ...
  272:                             slot0 = await asyncio.to_thread(contract.functions.slot0().call)
  273:                             liquidity = await asyncio.to_thread(contract.functions.liquidity().call)
  ...
  302:                             res = await asyncio.to_thread(contract.functions.getReserves().call)
  ...
  318:                         logs = await asyncio.to_thread(
  319:                             w3.eth.get_logs,
  ...
  ```

### B. Synchronous Calls in `src/crypcodile/mcp_server.py`
The helper function `get_onchain_price` is declared synchronously (`def`) and makes blocking calls (lines 77–158):
- Instantiates synchronous Web3:
  ```python
  84:         w3 = Web3(Web3.HTTPProvider(rpc_url))
  ```
- Makes synchronous blocking calls:
  ```python
  95:             pool_addr = factory.functions.getPool(sorted_t0, sorted_t1, int(spec["fee"])).call()
  ...
  114:             slot0 = pool_contract.functions.slot0().call()
  115:             liquidity = pool_contract.functions.liquidity().call()
  ...
  138:             res = pool_contract.functions.getReserves().call()
  ...
  154:             "block": w3.eth.block_number
  ```
- Is called synchronously in async function `serve_stdio`:
  ```python
  282:                     tool_result = get_onchain_price(sym)
  ```

### C. Downstream API usage in `src/crypcodile/api_server.py`
The FastAPI async endpoint `get_market_data` imports and calls `get_onchain_price` synchronously:
- Verbatim:
  ```python
  13: from crypcodile.mcp_server import get_onchain_price
  ...
  102:         data = get_onchain_price(symbol)
  ```

### D. Synchronous Mocking in `tests/exchanges/base_onchain/`
The tests in `tests/exchanges/base_onchain/test_connector.py` (and associated files `test_adversarial.py`, `test_challenger_stress_2.py`, `test_challenger_stress_3.py`) mock Web3 methods synchronously:
- Mocks block number as static integer: `mock_w3.eth.block_number = 1000`
- Mocks `.call` as a synchronous method returning static values: `.call.return_value = ...`
- Uses custom mock objects (like `SleepyMockWeb3`, `LaggingMockWeb3`) containing synchronous properties and helper classes with `def call(self)` methods.

---

## 2. Logic Chain
1. To refactor the connector to run Web3 natively asynchronously, we must instantiate `AsyncWeb3` and `AsyncHTTPProvider` inside `connector.py` and replace all `asyncio.to_thread` calls with direct awaits on the corresponding `AsyncWeb3` attributes and method calls.
2. Similarly, to prevent blocking the main event loop in `mcp_server.py`, `get_onchain_price` must be declared as `async def get_onchain_price`, use `AsyncWeb3` and `AsyncHTTPProvider` internally, and await all Web3 calls.
3. If `get_onchain_price` is converted to an asynchronous function, all call sites invoking it must be updated to await it. This includes the call site in `mcp_server.py:282` (`serve_stdio`) and the call site in `api_server.py:102` (`get_market_data`).
4. Since `AsyncWeb3` changes properties (like `w3.eth.block_number`) and method calls (like `.call()`, `.get_block()`, `.get_logs()`) to return coroutines/awaitables, tests that mock these attributes synchronously will fail.
5. Therefore, we must:
   - Patch `web3.AsyncWeb3` instead of `web3.Web3` in the tests.
   - Use `AsyncMock` to mock asynchronous method calls (such as `.call()`, `.get_block()`, `.get_logs()`).
   - Mock properties like `w3.eth.block_number` using a custom awaitable helper class (`AsyncVal`) so it can be awaited multiple times without reuse errors or throwing type issues.
   - Update the custom mock classes (`SleepyMockWeb3`, `LaggingMockWeb3`, `DummyMockContract`) in the stress test files so that their methods are async (`async def`) and their properties return awaitables.

---

## 3. Caveats
No caveats.

---

## 4. Conclusion & Step-by-Step Fix Strategy

### Step 1: Refactor `src/crypcodile/exchanges/base_onchain/connector.py`
- Change imports inside `_poll_loop` (lines 131–134):
  ```python
  from web3 import AsyncWeb3, AsyncHTTPProvider
  w3 = AsyncWeb3(AsyncHTTPProvider(self.rpc_url))
  ```
- Change `_get_block_timestamp` (line 96) to:
  ```python
  blk = await w3.eth.get_block(block_number)
  ```
- Change checksum address lookups to use `AsyncWeb3.to_checksum_address(...)` (or keep `Web3.to_checksum_address` by importing `Web3` as well).
- Change factory contract calls:
  ```python
  pool_addr = await factory.functions.getPool(sorted_t0, sorted_t1, fee).call()
  ```
  and:
  ```python
  pool_addr = await factory.functions.getPool(t0_addr, t1_addr, stable).call()
  ```
- Change block number check (line 252) to:
  ```python
  current_block = await w3.eth.block_number
  ```
- Change contract view function calls (lines 272–273, 302):
  ```python
  slot0 = await contract.functions.slot0().call()
  liquidity = await contract.functions.liquidity().call()
  # ...
  res = await contract.functions.getReserves().call()
  ```
- Change logs query (lines 318–326):
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

### Step 2: Refactor `src/crypcodile/mcp_server.py`
- Update imports at the top of the file:
  ```python
  from web3 import AsyncWeb3, AsyncHTTPProvider
  ```
- Declare `get_onchain_price` as `async def`:
  ```python
  async def get_onchain_price(symbol: str, rpc_url: str = DEFAULT_RPC_URL) -> dict[str, Any]:
  ```
- Instantiation and address mapping inside `get_onchain_price`:
  ```python
  w3 = AsyncWeb3(AsyncHTTPProvider(rpc_url))
  t0_addr = AsyncWeb3.to_checksum_address(TOKENS[str(spec["token0"])])
  t1_addr = AsyncWeb3.to_checksum_address(TOKENS[str(spec["token1"])])
  ```
- Await contract functions and block number:
  ```python
  pool_addr = await factory.functions.getPool(sorted_t0, sorted_t1, int(spec["fee"])).call()
  # ...
  slot0 = await pool_contract.functions.slot0().call()
  liquidity = await pool_contract.functions.liquidity().call()
  # ...
  res = await pool_contract.functions.getReserves().call()
  # ...
  "block": await w3.eth.block_number
  ```
- Await `get_onchain_price` in `serve_stdio`:
  ```python
  tool_result = await get_onchain_price(sym)
  ```

### Step 3: Refactor `src/crypcodile/api_server.py`
- Await the refactored function inside FastAPI route `get_market_data` (line 102):
  ```python
  data = await get_onchain_price(symbol)
  ```

### Step 4: Refactor Tests under `tests/exchanges/base_onchain/`

#### A. Modify `test_connector.py` and `test_adversarial.py`
1. Define a helper class `AsyncVal` to mock properties like `block_number` that can be awaited multiple times:
   ```python
   class AsyncVal:
       def __init__(self, val_or_exc):
           self.val_or_exc = val_or_exc
       def __await__(self):
           async def _get():
               if isinstance(self.val_or_exc, Exception):
                   raise self.val_or_exc
               return self.val_or_exc
           return _get().__await__()
   ```
2. Replace `patch("web3.Web3")` with `patch("web3.AsyncWeb3")`.
3. Set block number using `AsyncVal`:
   ```python
   mock_w3.eth.block_number = AsyncVal(1000)
   ```
4. Mock contract methods and helper calls with `AsyncMock`:
   ```python
   mock_factory.functions.getPool.return_value.call = AsyncMock(return_value="0xMockPoolAddress")
   mock_pool.functions.slot0.return_value.call = AsyncMock(return_value=[...])
   mock_pool.functions.liquidity.return_value.call = AsyncMock(return_value=...)
   mock_pool.functions.getReserves.return_value.call = AsyncMock(return_value=...)
   mock_w3.eth.get_block = AsyncMock(return_value={"timestamp": 1234567890})
   mock_w3.eth.get_logs = AsyncMock(return_value=[mock_log])
   ```

#### B. Modify Custom Mocks in `test_challenger_stress_2.py` & `test_challenger_stress_3.py`
1. Convert `SleepyMockEth.block_number` and `LaggingMockEth.block_number` properties to be async:
   ```python
   @property
   async def block_number(self):
       # ...
       return value
   ```
2. Convert contract call methods to be async. Instead of returning a class with synchronous `call(self)`, return a class with an asynchronous `async def call(self)`:
   ```python
   class Call:
       async def call(self):
           return value
   ```
3. Convert methods like `get_block`, `get_logs`, and custom overrides to `async def` and replace blocking `time.sleep` with `await asyncio.sleep`.

---

## 5. Verification Method
1. Run target build/test command using the virtual environment pytest:
   ```bash
   .venv/bin/pytest tests/exchanges/base_onchain/
   ```
2. Confirm all 28 tests pass successfully.
3. Verification is invalidated if any synchronous Web3 call remains in `connector.py` or `mcp_server.py`, or if the event loop blocks during tests.
