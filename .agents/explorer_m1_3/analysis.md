# Analysis: Native AsyncWeb3 Refactoring (Milestone 1)

This analysis outlines the required changes to migrate `src/crypcodile/exchanges/base_onchain/connector.py`, `src/crypcodile/mcp_server.py`, and corresponding tests to run natively on `AsyncWeb3` and `AsyncHTTPProvider` using Python's `web3` library (v7.16.0).

---

## 1. Synchronous Web3 Calls in `connector.py`

In `src/crypcodile/exchanges/base_onchain/connector.py`, the `BaseOnchainTransport` class uses a polling loop (`_poll_loop`) to fetch pool data. This loop makes several synchronous Web3 calls wrapped inside `asyncio.to_thread(...)`.

### Call Locations and Details

1. **Get Block Timestamp** (Line 96):
   ```python
   blk = await asyncio.to_thread(w3.eth.get_block, block_number)
   ```
   * *Required Refactor*: Directly await the async method:
     ```python
     blk = await w3.eth.get_block(block_number)
     ```

2. **Uniswap V3 Factory Pool Resolution** (Lines 221-223):
   ```python
   pool_addr = await asyncio.to_thread(
       factory.functions.getPool(sorted_t0, sorted_t1, fee).call
   )
   ```
   * *Required Refactor*: Directly await the function call:
     ```python
     pool_addr = await factory.functions.getPool(sorted_t0, sorted_t1, fee).call()
     ```

3. **Aerodrome Factory Pool Resolution** (Lines 230-232):
   ```python
   pool_addr = await asyncio.to_thread(
       factory.functions.getPool(t0_addr, t1_addr, stable).call
   )
   ```
   * *Required Refactor*: Directly await the function call:
     ```python
     pool_addr = await factory.functions.getPool(t0_addr, t1_addr, stable).call()
     ```

4. **Get Block Number** (Line 252):
   ```python
   current_block = await asyncio.to_thread(lambda: w3.eth.block_number)
   ```
   * *Required Refactor*: Directly await the block number coroutine property:
     ```python
     current_block = await w3.eth.block_number
     ```

5. **Query Uniswap V3 Pool `slot0`** (Line 272):
   ```python
   slot0 = await asyncio.to_thread(contract.functions.slot0().call)
   ```
   * *Required Refactor*: Directly await the call:
     ```python
     slot0 = await contract.functions.slot0().call()
     ```

6. **Query Uniswap V3 Pool `liquidity`** (Line 273):
   ```python
   liquidity = await asyncio.to_thread(contract.functions.liquidity().call)
   ```
   * *Required Refactor*: Directly await the call:
     ```python
     liquidity = await contract.functions.liquidity().call()
     ```

7. **Query Aerodrome V2 Pool Reserves** (Line 302):
   ```python
   res = await asyncio.to_thread(contract.functions.getReserves().call)
   ```
   * *Required Refactor*: Directly await the call:
     ```python
     res = await contract.functions.getReserves().call()
     ```

8. **Fetch Swap Logs** (Lines 318-326):
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
   * *Required Refactor*: Directly await the log retrieval:
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

---

## 2. Synchronous Web3 Calls in `mcp_server.py`

In `src/crypcodile/mcp_server.py`, the `get_onchain_price` function (starting on Line 77) is fully synchronous. It initiates a synchronous Web3 provider instance and queries block metadata and contract calls synchronously:

- **Web3 Instance Initialization** (Line 84):
  ```python
  w3 = Web3(Web3.HTTPProvider(rpc_url))
  ```
- **Factory Contract Resolution** (Lines 95, 101):
  ```python
  pool_addr = factory.functions.getPool(sorted_t0, sorted_t1, int(spec["fee"])).call()
  # or
  pool_addr = factory.functions.getPool(t0_addr, t1_addr, bool(spec["stable"])).call()
  ```
- **Pool State Query** (Lines 114, 115, 138):
  ```python
  slot0 = pool_contract.functions.slot0().call()
  liquidity = pool_contract.functions.liquidity().call()
  # or
  res = pool_contract.functions.getReserves().call()
  ```
- **Block Number Query** (Line 154):
  ```python
  "block": w3.eth.block_number
  ```

### Required Refactoring:
1. Make `get_onchain_price` an asynchronous function: `async def get_onchain_price(...)`.
2. Instantiate `AsyncWeb3` with `AsyncHTTPProvider`:
   ```python
   from web3 import AsyncWeb3
   w3 = AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(rpc_url))
   ```
3. Await all factory and pool calls: `await contract.functions.method().call()`.
4. Await `w3.eth.block_number`.
5. Call `get_onchain_price` with `await` inside the `serve_stdio` loop in `mcp_server.py` (Line 282):
   ```python
   tool_result = await get_onchain_price(sym)
   ```
6. Crucially, update `src/crypcodile/api_server.py` (Line 102) to also await the function:
   ```python
   data = await get_onchain_price(symbol)
   ```

---

## 3. Test Refactoring (`tests/exchanges/base_onchain/`)

All unit tests in `tests/exchanges/base_onchain/` need to be refactored to support `AsyncWeb3` natively.

### 3.1 `tests/exchanges/base_onchain/test_connector.py`

#### A. Patch Target
Change all instances patching `"web3.Web3"` to patch `"web3.AsyncWeb3"`:
```python
# Before
with patch("web3.Web3") as mock_web3_class:

# After
with patch("web3.AsyncWeb3") as mock_web3_class:
```

#### B. Awaitable Properties and Methods (using `AsyncMock`)
For async methods and properties, replace regular mocks with `AsyncMock`:
```python
mock_w3.eth.block_number = AsyncMock(return_value=1000)
mock_w3.eth.get_logs = AsyncMock(return_value=[mock_log])
mock_w3.eth.get_block = AsyncMock(return_value={"timestamp": 1234567890})

# Factory mock
mock_factory.functions.getPool.return_value.call = AsyncMock(return_value="0xMockV3StandardPoolAddress")

# Pool contract mocks
mock_pool.functions.slot0.return_value.call = AsyncMock(return_value=[(2**96 * 2), 0, 0, 0, 0, 0, True])
mock_pool.functions.liquidity.return_value.call = AsyncMock(return_value=100 * 10**8)
mock_pool.functions.getReserves.return_value.call = AsyncMock(return_value=[(1000 * 10**18), (2000 * 10**6), 1234567])
```

---

### 3.2 `tests/exchanges/base_onchain/test_adversarial.py`

Update patches from `web3.Web3` to `web3.AsyncWeb3`, and use `AsyncMock` to raise errors or simulate failures:
- In `test_transport_resilience_to_rpc_errors`:
  ```python
  mock_w3.eth.block_number = AsyncMock(side_effect=Exception("RPC connection refused"))
  ```
- In `test_transport_resilience_to_get_logs_error`:
  ```python
  mock_w3.eth.get_logs = AsyncMock(side_effect=Exception("Log server offline"))
  ```

---

### 3.3 `tests/exchanges/base_onchain/test_challenger_stress_2.py` and `test_challenger_stress_3.py`

These tests implement custom mock classes (`SleepyMockWeb3`, `LaggingMockWeb3`). These mocks must have their synchronous methods rewritten as async methods and properties.

#### A. Awaitable Property Setup
Use python's `async def` in combination with `@property` to return coroutines for properties:
```python
# In SleepyMockEth / LaggingMockEth
@property
async def block_number(self):
    return self._block_number
```

#### B. Async Methods in Mock Eth
```python
# In SleepyMockEth
async def get_block(self, block_num):
    if self.parent.get_block_delay > 0:
        await asyncio.sleep(self.parent.get_block_delay)
    return {"timestamp": 1600000000 + block_num}

async def get_logs(self, filter_params):
    if self.parent.get_logs_delay > 0:
        await asyncio.sleep(self.parent.get_logs_delay)
    return []
```

#### C. Async Inner Class `.call()` Methods
```python
# In Contract Mock Call functions
class Call:
    async def call(self):
        return ...
```
For example:
```python
def slot0(self):
    class Call:
        def __init__(self, parent):
            self.parent = parent
        async def call(self):
            return [2**96, 0, 0, 0, 0, 0, True]
    return Call(self.parent)
```

---

This refactoring removes all blocking logic and threads in on-chain interactions, ensuring full event loop concurrency.
