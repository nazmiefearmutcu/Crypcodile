## 2026-06-14T15:50:09Z

You are a Worker (teamwork_preview_worker).
Your working directory is /Users/nazmi/Crypcodile/.agents/worker_m1.
Your task is to implement Milestone 1: Native AsyncWeb3 refactoring (connector and mcp_server.py).

Here is the step-by-step fix strategy derived from the Explorers' reports:

### Step 1: Refactor `src/crypcodile/exchanges/base_onchain/connector.py`
1. Replace `from web3 import Web3` (inside `_poll_loop`) with `from web3 import AsyncWeb3, AsyncHTTPProvider`.
2. Replace Web3 instantiation with:
   ```python
   w3 = AsyncWeb3(AsyncHTTPProvider(self.rpc_url))
   ```
3. Use `AsyncWeb3` instead of `Web3` for checksum address conversions:
   `AsyncWeb3.to_checksum_address(...)` (keep `Web3.to_checksum_address(...)` if easier, but ensure Web3 is imported).
4. Remove all `asyncio.to_thread(...)` wrappers in `_get_block_timestamp` and `_poll_loop` and replace them with direct await expressions:
   - `blk = await w3.eth.get_block(block_number)`
   - `pool_addr = await factory.functions.getPool(sorted_t0, sorted_t1, fee).call()`
   - `pool_addr = await factory.functions.getPool(t0_addr, t1_addr, stable).call()`
   - `current_block = await w3.eth.block_number`
   - `slot0 = await contract.functions.slot0().call()`
   - `liquidity = await contract.functions.liquidity().call()`
   - `res = await contract.functions.getReserves().call()`
   - `logs = await w3.eth.get_logs(...)`

### Step 2: Refactor `src/crypcodile/mcp_server.py`
1. Replace the import on line 10 with `from web3 import AsyncWeb3, AsyncHTTPProvider`.
2. Redefine `get_onchain_price` as an async function:
   ```python
   async def get_onchain_price(symbol: str, rpc_url: str = DEFAULT_RPC_URL) -> dict[str, Any]:
   ```
3. Replace Web3 instantiation:
   ```python
   w3 = AsyncWeb3(AsyncHTTPProvider(rpc_url))
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
1. Update `test_connector.py` and `test_adversarial.py`:
   - Change patch targets from `"web3.Web3"` to `"web3.AsyncWeb3"`.
   - Mock properties and functions as async / awaitable using `unittest.mock.AsyncMock`.
   - Specifically, `mock_w3.eth.block_number` must be mocked in a way that it can be awaited multiple times (e.g., using `AsyncMock(return_value=1000)` or `PropertyMock` or a custom awaitable helper class).
   - Mock `get_block` and `get_logs` as `AsyncMock`.
   - Mock contract methods `.call` as `AsyncMock` returning the appropriate mock response.
2. Update `test_challenger_stress_2.py` and `test_challenger_stress_3.py`:
   - Update patch targets to `"web3.AsyncWeb3"`.
   - Refactor mock helper classes (`SleepyMockWeb3`, `LaggingMockWeb3`, etc.) to use async functions (`async def`) and return awaitables. Convert properties like `block_number` to return awaitables or be async properties.
   - Convert contract call methods to be async (`async def call(self)`).

Run the test suite `uv run pytest tests/exchanges/base_onchain/` and verify that all tests pass. Run `uv build` and check that it builds. Write your changes and handoff report containing build and test outputs to `/Users/nazmi/Crypcodile/.agents/worker_m1/handoff.md`.
