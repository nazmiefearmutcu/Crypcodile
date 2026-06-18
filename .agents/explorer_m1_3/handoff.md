# Handoff Report: Native AsyncWeb3 Refactoring (Milestone 1)

This report outlines the observations, reasoning, and a complete step-by-step fix strategy to migrate the base on-chain connector, MCP server tool, and unit tests from synchronous Web3 to native `AsyncWeb3`.

---

## 1. Observation

Direct inspection of the codebase reveals that:
- **`src/crypcodile/exchanges/base_onchain/connector.py`** uses synchronous `Web3` with `Web3.HTTPProvider` inside `_poll_loop`, wrapping calls in `asyncio.to_thread` (Lines 96, 221, 230, 252, 272, 273, 302, 318):
  - Line 96: `blk = await asyncio.to_thread(w3.eth.get_block, block_number)`
  - Line 252: `current_block = await asyncio.to_thread(lambda: w3.eth.block_number)`
  - Lines 318-319: `logs = await asyncio.to_thread(w3.eth.get_logs, ...)`
- **`src/crypcodile/mcp_server.py`** defines `get_onchain_price` as a synchronous function (Line 77) utilizing `Web3(Web3.HTTPProvider(...))` (Line 84), making synchronous blocking calls:
  - Line 84: `w3 = Web3(Web3.HTTPProvider(rpc_url))`
  - Line 95: `pool_addr = factory.functions.getPool(...).call()`
  - Line 114: `slot0 = pool_contract.functions.slot0().call()`
- **`src/crypcodile/api_server.py`** imports and invokes `get_onchain_price` synchronously (Line 102):
  - Line 102: `data = get_onchain_price(symbol)`
- **Tests** in `tests/exchanges/base_onchain/` mock `web3.Web3` (using `patch("web3.Web3")`) and return synchronous mock values:
  - `test_connector.py` Line 69: `patch("web3.Web3") as mock_web3_class`
  - `test_connector.py` Line 88: `mock_pool.functions.slot0.return_value.call.return_value = [...]`
  - `test_challenger_stress_2.py` and `test_challenger_stress_3.py` define custom mock classes `SleepyMockWeb3` and `LaggingMockWeb3` which use synchronous methods and properties.

---

## 2. Logic Chain

1. **Observations 1 & 2** show that network requests to Base nodes are performed synchronously and either block the main event loop or run inside a separate thread pool (`asyncio.to_thread`).
2. **Web3.py (v7.16.0)** provides native async support using `AsyncWeb3` and `AsyncHTTPProvider`. Using native async prevents main-thread blocking and eliminates thread context-switching overhead.
3. Because `AsyncWeb3` calls are awaitable coroutines, any function performing them must be defined using `async def` and invoke them with the `await` keyword.
4. Hence, `BaseOnchainTransport._poll_loop` in `connector.py` and `get_onchain_price` in `mcp_server.py` must use `await` when calling Web3 methods, which forces `get_onchain_price` to become an `async def`.
5. Since `get_onchain_price` becomes asynchronous, any client function calling it (like the endpoint in `api_server.py` and the tool execution in `mcp_server.py`) must also await the call (**Observation 3**).
6. In tests (**Observation 4**), patching `web3.Web3` will no longer intercept the imports of `AsyncWeb3`. The tests must target `web3.AsyncWeb3` and mock returning coroutines (using `AsyncMock` or `async def` methods) to prevent `TypeError: object can't be used in 'await' expression` when the async methods are awaited.

---

## 3. Caveats

- This investigation is read-only; no code files were modified.
- No testing of WebSockets (`AsyncWeb3.WebSocketProvider`) was conducted because both `connector.py` and `mcp_server.py` rely exclusively on HTTP JSON-RPC polling.
- Assumes python dependency `web3>=6.0.0` is present, which is satisfied by the environment's `web3==7.16.0`.

---

## 4. Conclusion & Step-by-Step Fix Strategy

To complete Milestone 1, the following changes should be implemented:

### Step 1: Refactor `src/crypcodile/exchanges/base_onchain/connector.py`
1. Replace `from web3 import Web3` (inside `_poll_loop`) with `from web3 import AsyncWeb3`.
2. Replace Web3 instantiation with:
   ```python
   w3 = AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(self.rpc_url))
   ```
3. Use `AsyncWeb3` instead of `Web3` for checksum address conversions:
   `AsyncWeb3.to_checksum_address(...)`
4. Remove all `asyncio.to_thread(...)` wrappers in `_get_block_timestamp` and `_poll_loop` and replace them with direct await expressions:
   - `blk = await w3.eth.get_block(block_number)`
   - `pool_addr = await factory.functions.getPool(...).call()`
   - `current_block = await w3.eth.block_number`
   - `slot0 = await contract.functions.slot0().call()`
   - `liquidity = await contract.functions.liquidity().call()`
   - `res = await contract.functions.getReserves().call()`
   - `logs = await w3.eth.get_logs(...)`

### Step 2: Refactor `src/crypcodile/mcp_server.py`
1. Replace the import on line 10 with `from web3 import AsyncWeb3`.
2. Redefine `get_onchain_price` as an async function:
   ```python
   async def get_onchain_price(symbol: str, rpc_url: str = DEFAULT_RPC_URL) -> dict[str, Any]:
   ```
3. Replace Web3 instantiation:
   ```python
   w3 = AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(rpc_url))
   ```
4. Await all contract calls and block number queries:
   - `pool_addr = await factory.functions.getPool(...).call()`
   - `slot0 = await pool_contract.functions.slot0().call()`
   - `liquidity = await pool_contract.functions.liquidity().call()`
   - `res = await pool_contract.functions.getReserves().call()`
   - `"block": await w3.eth.block_number`
5. Await the function call inside `serve_stdio`:
   ```python
   tool_result = await get_onchain_price(sym)
   ```

### Step 3: Refactor `src/crypcodile/api_server.py`
1. Update `get_market_data` endpoint (line 102) to await `get_onchain_price`:
   ```python
   data = await get_onchain_price(symbol)
   ```

### Step 4: Refactor tests in `tests/exchanges/base_onchain/`

#### `test_connector.py` and `test_adversarial.py`
1. Change patch targets from `"web3.Web3"` to `"web3.AsyncWeb3"`.
2. Convert mock variables to `AsyncMock`:
   - `mock_w3.eth.block_number = AsyncMock(return_value=1000)`
   - `mock_w3.eth.get_block = AsyncMock(return_value={"timestamp": 1234567890})`
   - `mock_w3.eth.get_logs = AsyncMock(return_value=[mock_log])` (or `AsyncMock(side_effect=Exception(...))` in error tests)
   - `mock_factory.functions.getPool.return_value.call = AsyncMock(return_value="0xMockAddress")`
   - `mock_pool.functions.slot0.return_value.call = AsyncMock(return_value=[...])`
   - `mock_pool.functions.liquidity.return_value.call = AsyncMock(return_value=...)`
   - `mock_pool.functions.getReserves.return_value.call = AsyncMock(return_value=[...])`

#### `test_challenger_stress_2.py` and `test_challenger_stress_3.py`
1. Update patch targets from `web3.Web3` to `web3.AsyncWeb3`.
2. Update mock helper classes (`SleepyMockWeb3`, `LaggingMockWeb3`, etc.):
   - Convert property `block_number` to an `async def`:
     ```python
     @property
     async def block_number(self):
         return self._block_number
     ```
   - Convert `get_block` and `get_logs` to `async def` and replace synchronous `time.sleep` with `await asyncio.sleep`.
   - Update inner `Call` classes inside `getPool`, `slot0`, `liquidity`, `getReserves` to use `async def call(self)` instead of `def call(self)`.

---

## 5. Verification Method

To independently verify the changes after implementation:

1. **Command to run**:
   ```bash
   uv run pytest tests/exchanges/base_onchain/
   ```
2. **Files to inspect**:
   - `src/crypcodile/exchanges/base_onchain/connector.py` (ensure no `asyncio.to_thread` wraps Web3 calls)
   - `src/crypcodile/mcp_server.py` (ensure `get_onchain_price` is async and tool call is awaited)
   - `src/crypcodile/api_server.py` (ensure API route awaits `get_onchain_price`)
3. **Invalidation conditions**:
   - If any test fails with a `TypeError` indicating a coroutine wasn't awaited, or that a non-coroutine object was used in an `await` expression.
   - If `test_non_blocking_event_loop` fails, indicating that a blocking synchronous network request is still occurring on the main thread.
