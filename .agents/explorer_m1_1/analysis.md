# Analysis of Web3 Usage and Refactoring Plan

## 1. Synchronous Web3 Calls in `src/crypcodile/exchanges/base_onchain/connector.py`

In `connector.py`, the `BaseOnchainTransport._poll_loop` runs asynchronously, but it uses a synchronous `web3.Web3` instance and offloads blocking operations to thread pools using `asyncio.to_thread`. 

Below are all the occurrences of synchronous calls and `asyncio.to_thread` wraps:

*   **Provider Initialization**:
    *   **Line 134**: `w3 = Web3(Web3.HTTPProvider(self.rpc_url))`
    *   *Description*: Synchronous `Web3` client instantiation with `HTTPProvider`.

*   **Block Timestamp Queries**:
    *   **Line 96** (in `_get_block_timestamp`):
        ```python
        blk = await asyncio.to_thread(w3.eth.get_block, block_number)
        ```
    *   *Description*: `get_block` is a synchronous call wrapped in `asyncio.to_thread`.

*   **Dynamic Pool Address Resolution (Factory calls)**:
    *   **Lines 221-223**:
        ```python
        pool_addr = await asyncio.to_thread(
            factory.functions.getPool(sorted_t0, sorted_t1, fee).call
        )
        ```
    *   **Lines 230-232**:
        ```python
        pool_addr = await asyncio.to_thread(
            factory.functions.getPool(t0_addr, t1_addr, stable).call
        )
        ```
    *   *Description*: Uniswap V3 and Aerodrome factory `getPool().call` operations wrapped in `asyncio.to_thread`.

*   **Block Number Check**:
    *   **Line 252**:
        ```python
        current_block = await asyncio.to_thread(lambda: w3.eth.block_number)
        ```
    *   *Description*: Querying the current block number properties, wrapped in `asyncio.to_thread`.

*   **Pool State and Reserves (Contract view calls)**:
    *   **Lines 272-273**:
        ```python
        slot0 = await asyncio.to_thread(contract.functions.slot0().call)
        liquidity = await asyncio.to_thread(contract.functions.liquidity().call)
        ```
    *   **Line 302**:
        ```python
        res = await asyncio.to_thread(contract.functions.getReserves().call)
        ```
    *   *Description*: Querying pool data (`slot0`, `liquidity`, `getReserves`) on respective pool contracts via `asyncio.to_thread`.

*   **Log Fetching**:
    *   **Lines 318-326**:
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
    *   *Description*: Pulling contract swap logs synchronously using `asyncio.to_thread`.

---

## 2. Synchronous Web3 Calls in `src/crypcodile/mcp_server.py`

In `mcp_server.py`, the helper function `get_onchain_price` is defined synchronously (`def`) and directly calls the synchronous `Web3` API, blocking the main event loop when invoked from the async stream reader.

Below are the occurrences of synchronous calls:

*   **Provider Initialization**:
    *   **Line 84**: `w3 = Web3(Web3.HTTPProvider(rpc_url))`
    *   *Description*: Instantiating a synchronous `Web3` client.

*   **Factory Contract Calls**:
    *   **Line 95**: `pool_addr = factory.functions.getPool(sorted_t0, sorted_t1, int(spec["fee"])).call()`
    *   **Line 101**: `pool_addr = factory.functions.getPool(t0_addr, t1_addr, bool(spec["stable"])).call()`
    *   *Description*: Blocking `getPool().call()` invocation.

*   **Contract View Calls**:
    *   **Line 114**: `slot0 = pool_contract.functions.slot0().call()`
    *   **Line 115**: `liquidity = pool_contract.functions.liquidity().call()`
    *   **Line 138**: `res = pool_contract.functions.getReserves().call()`
    *   *Description*: Blocking contract slot, liquidity, and reserve calls.

*   **Block Number Check**:
    *   **Line 154**: `"block": w3.eth.block_number`
    *   *Description*: Blocking property lookup.

*   **Downstream Blocking Usages**:
    *   **`src/crypcodile/mcp_server.py:282`**: Calling `tool_result = get_onchain_price(sym)` inside async `serve_stdio`.
    *   **`src/crypcodile/api_server.py:102`**: Calling `data = get_onchain_price(symbol)` inside the FastAPI async endpoint `get_market_data`.

---

## 3. Web3 Mocking in `tests/exchanges/base_onchain/`

The test files in `tests/exchanges/base_onchain/` mock Web3 functionality synchronously. Refactoring to `AsyncWeb3` will break these tests because they expect synchronous mocks (raising `TypeError` when `await`ed or returning `MagicMock` instead of a coroutine/awaitable).

Here is how each test file currently mocks Web3:

### A. `tests/exchanges/base_onchain/test_connector.py`
*   Patches `web3.Web3` to return `mock_w3`.
*   Mocks properties: `mock_w3.eth.block_number = 1000` (synchronous integer).
*   Mocks contract function return values: `.call.return_value = ...` or `.call.return_value.call.return_value = ...`.
*   Mocks methods like `mock_w3.eth.get_logs` and `mock_w3.eth.get_block` with standard synchronous return values.

### B. `tests/exchanges/base_onchain/test_adversarial.py`
*   Patches `web3.Web3`.
*   Simulates RPC exceptions using synchronous side effects: `mock_w3.eth.block_number = MagicMock(side_effect=Exception("RPC connection refused"))`.
*   Simulates log failures using: `mock_w3.eth.get_logs.side_effect = Exception("Log server offline")`.

### C. `tests/exchanges/base_onchain/test_challenger_stress_2.py`
*   Defines `SleepyMockWeb3`, `SleepyMockEth`, `SleepyMockContract`, and `SleepyMockContractFunctions`.
*   Simulates network delays via `time.sleep()` in `get_block` and `get_logs`.
*   Returns contract call mocks wrapped in synchronous inner classes with `def call(self)` methods returning static data.

### D. `tests/exchanges/base_onchain/test_challenger_stress_3.py`
*   Defines `LaggingMockWeb3` and `LaggingMockEth`.
*   Returns sequential block numbers synchronously via a custom `@property def block_number` getter.
