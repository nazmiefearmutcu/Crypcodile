# E2E Test Infrastructure Design Report

## Executive Summary
This report presents the E2E testing infrastructure design for Crypcodile. By analyzing all JSON-RPC/Web3 integration points across the codebase (`connector`, `normalizer`, `api_server`, and `mcp_server`), we map out the exact contract interfaces and event signatures required. We design a lightweight, programmatically configurable **Mock RPC Server** using `aiohttp` to intercept all Web3 traffic during tests. Finally, we propose a structured E2E test harness (`tests/e2e/`) spanning four coverage tiers (Tiers 1-4) to guarantee system reliability and compliance.

---

## 1. Web3 & JSON-RPC Call Catalog

Our investigation of the codebase reveals that Web3 queries are concentrated in `connector.py` and `mcp_server.py`, with on-chain transaction log checking to be added in `api_server.py`. 

The following methods are called, with their parameters and expected JSON-RPC mapping:

### 1.1 `w3.eth.block_number`
* **JSON-RPC Method**: `eth_blockNumber`
* **Parameters**: `[]`
* **Usage**: Polled by `BaseOnchainTransport` and `get_onchain_price` to fetch the current head block.
* **Mock Output**: Returns block height as hex string (e.g., `"0x3e8"` for block 1000).

### 1.2 `w3.eth.get_block`
* **JSON-RPC Method**: `eth_getBlockByNumber`
* **Parameters**: `[block_number_hex, False]` (e.g. `["0x3e8", false]`)
* **Usage**: Called by `_get_block_timestamp` to resolve block number to its unix timestamp for trade normalization.
* **Mock Output**: A block object containing the `"timestamp"` key (e.g. `{"timestamp": "0x499602d2"}`).

### 1.3 `w3.eth.get_logs`
* **JSON-RPC Method**: `eth_getLogs`
* **Parameters**: An object containing:
  * `"address"`: Checksummed pool address hex.
  * `"fromBlock"`: Hex string block number.
  * `"toBlock"`: Hex string block number.
  * `"topics"`: `[swap_topic]` (where `swap_topic` is `SWAP_TOPIC_V3` or `SWAP_TOPIC_V2`).
* **Usage**: Polled by `BaseOnchainTransport` to capture raw Swap events.
* **Mock Output**: List of log dictionaries containing `"data"`, `"transactionHash"`, `"logIndex"`, and `"blockNumber"`.

### 1.4 `w3.eth.get_transaction_receipt`
* **JSON-RPC Method**: `eth_getTransactionReceipt`
* **Parameters**: `[tx_hash]` (e.g. `["0x..."]`)
* **Usage**: Required by `api_server.py` to verify USDC micropayments (x402 protocol).
* **Mock Output**: A receipt containing `"status"` (`"0x1"` for success) and `"logs"` representing the Transfer event.

### 1.5 Contract Calls (`eth_call`)
All read-only contract calls are routed through `eth_call`. The mock server must match the `"to"` address and the `"data"` function selector (first 4 bytes of call data) to determine what to return.

| Contract & Function | Selector | Target Address | Parameter Encoding | Return Value Encoding |
| :--- | :--- | :--- | :--- | :--- |
| **Uniswap V3 Factory**<br>`getPool(address,address,uint24)` | `0x1698ee43` | `0x33128a8fC17869897dcE68Ed026d694621f6FDfD` | `token0` (32B), `token1` (32B), `fee` (32B) | `pool_address` (32B hex-encoded address) |
| **Aerodrome Factory**<br>`getPool(address,address,bool)` | `0x990f1d5d` | `0x420DD381b31aEf6683db6B902084cB0FFECe40Da` | `tokenA` (32B), `tokenB` (32B), `stable` (32B) | `pool_address` (32B hex-encoded address) |
| **Uniswap V3 Pool**<br>`slot0()` | `0x3850c7bd` | Dynamic Pool Address | None | `(sqrtPriceX96, tick, Index, Card, CardNext, feeProtocol, unlocked)` (7 * 32B words) |
| **Uniswap V3 Pool**<br>`liquidity()` | `0x1a6828d9` | Dynamic Pool Address | None | `liquidity` (uint128, 32B word) |
| **Aerodrome V2 Pool**<br>`getReserves()` | `0x0902f1ac` | Dynamic Pool Address | None | `(reserve0, reserve1, blockTimestampLast)` (3 * 32B words) |

---

## 2. Mock RPC Server Architecture & Design

The Mock RPC Server will run as an asynchronous `aiohttp` web server within the test runner's thread or a separate thread, depending on the test configuration. To make it programmatically controllable by pytest, it will expose:
1. **JSON-RPC endpoint (`POST /`)** to handle Web3 library requests.
2. **REST Control API (`POST /mock/...`)** to configure prices, blocks, logs, receipts, rate limits, and network errors.

### 2.1 Web3 JSON-RPC Request Matchers
The server processes `POST /` requests by matching the `"method"` field:

* **`eth_blockNumber`**: Returns the current mocked block number from memory.
* **`eth_getBlockByNumber`**: Looks up or generates block headers, converting timestamps to hex.
* **`eth_getLogs`**: Filters in-memory mock logs matching target address and block range.
* **`eth_getTransactionReceipt`**: Returns pre-seeded transaction receipts containing ERC-20 transfer logs.
* **`eth_call`**: Parses `"to"` and `"data"` to route to specific contract mock behaviors:
  * If `"to"` matches the Uniswap V3 Factory: decode parameters and return the pre-registered V3 pool address.
  * If `"to"` matches the Aerodrome Factory: return the V3/V2 pool address.
  * If `"to"` matches a registered Pool: return packed hex matching `slot0`, `liquidity`, or `getReserves`.

### 2.2 Control API Endpoints
To avoid hardcoding mock states, the server supports REST endpoints for dynamic configuration:
* `POST /mock/block`: Update the current block number and timestamp.
* `POST /mock/pool`: Seed details of a pool (type, tokens, reserves, price, etc.).
* `POST /mock/logs`: Append swap or transfer event logs.
* `POST /mock/receipts`: Seed a transaction receipt (with custom logs, success/fail status).
* `POST /mock/behavior`: Configure simulated errors like network timeouts, connection drops, or HTTP 429 status codes.

### 2.3 Reference Implementation Script

Below is the proposed implementation code for `tests/e2e/mock_rpc_server.py`:

```python
import asyncio
import logging
from aiohttp import web

log = logging.getLogger("mock_rpc_server")

class MockRPCServer:
    def __init__(self):
        self.block_number = 1000
        self.block_timestamp = 1700000000
        self.pools = {}       # pool_address -> pool_state dict
        self.factories = {}   # factory_address -> {(token0, token1, fee/stable): pool_address}
        self.logs = []        # list of raw log objects
        self.receipts = {}    # tx_hash -> receipt dict
        self.behavior = {
            "status_code": 200,
            "error_count": 0,
            "delay": 0.0
        }
        
    def reset(self):
        self.block_number = 1000
        self.block_timestamp = 1700000000
        self.pools.clear()
        self.factories.clear()
        self.logs.clear()
        self.receipts.clear()
        self.behavior = {"status_code": 200, "error_count": 0, "delay": 0.0}

    async def handle_rpc(self, request: web.Request) -> web.Response:
        # Simulate configured delay/timeout
        if self.behavior["delay"] > 0:
            await asyncio.sleep(self.behavior["delay"])
            
        # Simulate rate-limiting / server errors
        if self.behavior["error_count"] > 0:
            self.behavior["error_count"] -= 1
            return web.Response(status=self.behavior["status_code"], text="Simulated Failure")

        try:
            body = await request.json()
        except Exception:
            return web.json_response({"jsonrpc": "2.0", "error": {"code": -32700, "message": "Parse error"}}, status=400)

        # Handle batches vs single requests
        if isinstance(body, list):
            results = [await self._process_single_rpc(req) for req in body]
            return web.json_response(results)
        else:
            result = await self._process_single_rpc(body)
            return web.json_response(result)

    async def _process_single_rpc(self, req: dict) -> dict:
        req_id = req.get("id")
        method = req.get("method")
        params = req.get("params", [])
        
        result = None
        error = None
        
        try:
            if method == "eth_blockNumber":
                result = hex(self.block_number)
                
            elif method == "eth_getBlockByNumber":
                # params[0] is hex block number or tag like "latest"
                blk_num = self.block_number if params[0] in ("latest", "pending") else int(params[0], 16)
                result = {
                    "number": hex(blk_num),
                    "timestamp": hex(self.block_timestamp),
                    "hash": f"0xblockhash{blk_num}",
                    "transactions": []
                }
                
            elif method == "eth_getLogs":
                filter_obj = params[0]
                from_blk = int(filter_obj.get("fromBlock", "0x0"), 16)
                to_blk = self.block_number if filter_obj.get("toBlock") in ("latest", "pending") else int(filter_obj.get("toBlock", "0x0"), 16)
                addr = filter_obj.get("address")
                topics = filter_obj.get("topics", [])
                
                matched = []
                for lg in self.logs:
                    lg_blk = int(lg["blockNumber"], 16)
                    if from_blk <= lg_blk <= to_blk:
                        if not addr or lg["address"].lower() == addr.lower():
                            # Check topics matching
                            if not topics or lg["topics"][0] == topics[0]:
                                matched.append(lg)
                result = matched
                
            elif method == "eth_getTransactionReceipt":
                tx_hash = params[0]
                result = self.receipts.get(tx_hash)
                
            elif method == "eth_call":
                tx_obj = params[0]
                to_addr = tx_obj.get("to", "").lower()
                data = tx_obj.get("data", "")
                
                result = await self._handle_eth_call(to_addr, data)
                
            else:
                error = {"code": -32601, "message": f"Method {method} not supported"}
        except Exception as e:
            error = {"code": -32603, "message": f"Execution error: {str(e)}"}

        response = {"jsonrpc": "2.0", "id": req_id}
        if error:
            response["error"] = error
        else:
            response["result"] = result
        return response

    async def _handle_eth_call(self, to_addr: str, data: str) -> str:
        selector = data[:10]
        
        # 1. Uniswap V3 Factory getPool: selector = 0x1698ee43
        # 2. Aerodrome Factory getPool: selector = 0x990f1d5d
        if selector in ("0x1698ee43", "0x990f1d5d"):
            # Return address representation padded to 32 bytes
            # For simplicity, extract matching pool from seeded factories
            for (f_addr, keys), pool in self.factories.items():
                if f_addr.lower() == to_addr:
                    # Return pool address padded
                    return "0x" + pool[2:].zfill(64)
            return "0x" + "0".zfill(64)
            
        # 3. Uniswap V3 Pool slot0: selector = 0x3850c7bd
        elif selector == "0x3850c7bd":
            state = self.pools.get(to_addr, {})
            sqrtPriceX96 = state.get("sqrtPriceX96", 0)
            tick = state.get("tick", 0)
            # Encode return tuple (sqrtPriceX96, tick, obsIndex, obsCard, obsCardNext, feeProtocol, unlocked)
            res = (
                sqrtPriceX96.to_bytes(32, "big").hex() +
                tick.to_bytes(32, "big", signed=True).hex() +
                (0).to_bytes(32, "big").hex() * 4 +
                (1).to_bytes(32, "big").hex() # unlocked = True
            )
            return "0x" + res
            
        # 4. Uniswap V3 Pool liquidity: selector = 0x1a6828d9
        elif selector == "0x1a6828d9":
            state = self.pools.get(to_addr, {})
            liquidity = state.get("liquidity", 0)
            return "0x" + liquidity.to_bytes(32, "big").hex()
            
        # 5. Aerodrome V2 Pool getReserves: selector = 0x0902f1ac
        elif selector == "0x0902f1ac":
            state = self.pools.get(to_addr, {})
            r0 = state.get("reserve0", 0)
            r1 = state.get("reserve1", 0)
            ts = state.get("timestamp", self.block_timestamp)
            res = (
                r0.to_bytes(32, "big").hex() +
                r1.to_bytes(32, "big").hex() +
                ts.to_bytes(32, "big").hex()
            )
            return "0x" + res
            
        raise ValueError(f"Unknown selector {selector} on contract {to_addr}")

    # REST Control Endpoints
    async def set_block(self, request: web.Request) -> web.Response:
        body = await request.json()
        self.block_number = body.get("block_number", self.block_number)
        self.block_timestamp = body.get("timestamp", self.block_timestamp)
        return web.Response(text="Block state updated")

    async def seed_pool(self, request: web.Request) -> web.Response:
        body = await request.json()
        addr = body["address"].lower()
        self.pools[addr] = {
            "sqrtPriceX96": body.get("sqrtPriceX96", 0),
            "tick": body.get("tick", 0),
            "liquidity": body.get("liquidity", 0),
            "reserve0": body.get("reserve0", 0),
            "reserve1": body.get("reserve1", 0),
            "timestamp": body.get("timestamp", self.block_timestamp)
        }
        # Seed factory resolution as well
        factory_addr = body["factory"].lower()
        tokens_key = body["tokens_key"]  # e.g., "AERO-USDC"
        if factory_addr not in self.factories:
            self.factories[factory_addr] = {}
        self.factories[factory_addr][tokens_key] = addr
        return web.Response(text="Pool seeded")

    async def seed_receipt(self, request: web.Request) -> web.Response:
        body = await request.json()
        tx_hash = body["transactionHash"]
        self.receipts[tx_hash] = {
            "transactionHash": tx_hash,
            "status": hex(body.get("status", 1)),
            "blockNumber": hex(body.get("blockNumber", self.block_number)),
            "logs": body.get("logs", [])
        }
        return web.Response(text="Receipt seeded")

    async def seed_logs(self, request: web.Request) -> web.Response:
        body = await request.json()
        self.logs.extend(body.get("logs", []))
        return web.Response(text="Logs seeded")

    async def set_behavior(self, request: web.Request) -> web.Response:
        body = await request.json()
        self.behavior.update(body)
        return web.Response(text="Behavior updated")


async def start_mock_server(host="127.0.0.1", port=0) -> tuple[web.AppRunner, int]:
    server = MockRPCServer()
    app = web.Application()
    app.router.add_post("/", server.handle_rpc)
    app.router.add_post("/control/block", server.set_block)
    app.router.add_post("/control/pool", server.seed_pool)
    app.router.add_post("/control/receipt", server.seed_receipt)
    app.router.add_post("/control/logs", server.seed_logs)
    app.router.add_post("/control/behavior", server.set_behavior)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    
    actual_port = runner.addresses[0][1]
    return runner, actual_port
```

---

## 3. Recommended E2E Directory Layout

We propose the following directory structure inside the `tests/` directory:

```
tests/e2e/
├── __init__.py
├── conftest.py                    # Pytest fixtures for server/app lifecycle & configurations
├── mock_rpc_server.py             # Mock RPC server class & runner (defined in Section 2)
├── test_tier1_features.py         # Isolation tests for F1-F6 (>=30 tests)
├── test_tier2_boundaries.py       # Corner-case, rate-limiting, and error tests (>=30 tests)
├── test_tier3_combinations.py     # Cross-feature flow tests (>=6 tests)
└── test_tier4_real_world.py       # E2E system/user flow simulations (>=5 tests)
```

---

## 4. Test Specifications (Tiers 1-4)

### Tier 1: Feature Coverage (>=30 tests)
Tests the individual features F1-F6 in isolation with pre-seeded mock states.

1. **F1-Uniswap V3 Pool Resolution**: Assert factory resolves Uniswap V3 pool address and invokes V3 functions on dynamic target.
2. **F1-Aerodrome Pool Resolution**: Assert factory resolves Aerodrome pool address and invokes V2 functions on dynamic target.
3. **F1-Uniswap V3 Slot0 Calculation**: Seed `slot0` and ensure correctly converted to price and reserves.
4. **F1-Aerodrome Reserves Retrieval**: Seed `getReserves` and check reserves and price calculation.
5. **F1-Swap Log Processing (Uniswap V3)**: Seed Uniswap V3 swap log, verify trade normalized and output.
6. **F1-Swap Log Processing (Aerodrome V2)**: Seed Aerodrome V2 swap log, verify trade normalized.
7. **F1-Liveness Sentinel Check**: Verify that closing the transport gracefully stops the async generator queue.
8. **F2-MCP tool list**: Verify MCP `tools/list` returns the correct schema and descriptions.
9. **F2-MCP query get_onchain_price**: Call MCP server tool to fetch Uniswap V3 price and assert exact JSON payload return.
10. **F2-MCP query get_onchain_price (Aerodrome)**: Call MCP server tool to fetch Aerodrome price.
11. **F2-MCP non-existing symbol**: Call MCP price tool with unsupported symbol, verify error returned gracefully.
12. **F3-Pagination Check**: Query block range with >500 blocks span and assert transport slices it into separate queries.
13. **F3-Pagination boundaries**: Query exactly 500 blocks, verify single call. Query 501 blocks, verify split (500 + 1).
14. **F3-Backoff Success**: Mock RPC returns HTTP 429 once, then returns 200. Ensure connector retries and completes.
15. **F4-Uniswap V3 Synthetic depth calculation**: Verify snapshot contains 5 bid and 5 ask levels computed around slot0 price.
16. **F4-Orderbook size enforcement**: Verify synthetic bids/asks do not drop below the minimum required sizing (0.0001).
17. **F5-x402 Micropayment 402 code**: Access `api_server` market data, verify status code 402 and header payload return.
18. **F5-x402 Verify valid payment**: Seed successful transaction receipt in Mock RPC; submit to `api_server` and verify 200 OK.
19. **F5-x402 Receipt lookup fail**: Submit tx hash not present in RPC; verify 400 Bad Request.
20. **F5-x402 Wrong recipient**: Seed receipt with transfer to a random wallet; verify payment rejected.
21. **F5-x402 Wrong transfer amount**: Seed receipt with value 999 instead of 1000 base units; verify payment rejected.
22. **F5-x402 Wrong ERC-20 contract**: Seed receipt from different token contract; verify payment rejected.
23. **F5-x402 Failed transaction status**: Seed receipt with transaction status `0` (failed); verify payment rejected.
24. **F6-Custom Symbol Registration**: Initialize connector with user-defined specs; verify it queries the custom pool.
25. **F6-Custom Symbol Decimals**: Register custom symbol with different base/quote decimals (e.g. 18/18); verify normalization scale.
26. **F6-Custom Uniswap fee tier**: Register custom pool with 10000 fee; verify factory call uses correct fee.
27. **F6-Custom Aerodrome stable**: Register custom Aerodrome pool with `stable=True`; verify factory call stable parameter.
28. **F2-MCP custom symbol lookup**: Ensure MCP price tool successfully resolves dynamically registered custom symbol configs.
29. **F1-Block Cache Hit**: Query block timestamp twice for same block number; assert RPC `eth_getBlockByNumber` called only once.
30. **F1-Block Cache eviction**: Verify block cache evicts old blocks when size threshold (1000) is exceeded.

### Tier 2: Boundary & Corner Cases (>=30 tests)
Verifies resilience against corrupted payloads, edge values, rate limits, and network issues.

1. **Extreme decimals pricing**: Run normalizer with pricing of extremely high base unit difference (e.g., 18 vs 2 decimals).
2. **Zero/Negative Price Handling**: Feed zero/negative state prices to normalizer, verify early return with 0 records.
3. **Empty Swap Logs**: Mock returns empty log list for query block range; assert state updates still queued with 0 swaps.
4. **Huge Pagination Split**: Block difference is 100,000 blocks; verify pagination safely performs 200 chunked RPC calls.
5. **Connection drop during initialization**: Mock RPC drops connection on first factory resolution. Verify connector retries.
6. **Connection drop mid-polling**: Mock RPC drops connection in poll loop. Verify error is logged, and next polling iteration recovers.
7. **Consistent rate limit (HTTP 429 exhausted)**: Mock RPC continuously returns 429. Assert connector throws MaxRetriesExceeded after exhaustion.
8. **Malformed JSON-RPC Responses**: Mock RPC returns invalid JSON string; verify Web3 library and connector handle gracefully.
9. **Malformed x402 Header Signature**: Pass corrupted string as `Payment-Signature` header; assert 400 Bad Request.
10. **JSON-RPC batch failures**: Return partial failures in multi-request JSON-RPC batch; verify connector handles success slices.
11. **Huge log payload**: Return 10,000 logs in one query; verify processing does not cause memory spikes or block processing.
12. **Double swap logs**: Seed logs with identical tx hash and index; verify database deduplication/normalizer handling.
13. **Timestamp drift**: Block timestamp is in the future relative to local timestamp; verify normalizer converts cleanly.
14. **USDC transfer log missing parameters**: Seed receipt logs lacking `to` parameter in topics; assert payment fails.
15. **USDC transfer log multi-transfer**: Transaction performs multiple transfers, with only one valid USDC transfer; verify pass.
16. **Aerodrome flipped address edge**: Address parsing where `int(addr1) == int(addr2) - 1`.
17. **Fast block production**: Current block progresses by 100 blocks between polls; verify pagination handles chunking.
18. **Slow block production**: Block number stays identical for multiple polls; verify get_logs is skipped or queried with 0 range.
19. **RPC Timeout on eth_call**: Call to pool contract times out; assert retried exponentially.
20. **Re-org detection**: Head block number drops (reorg); verify connector handles block tracking resetting.
21. **Invalid hexadecimal inputs**: Seed log data with invalid hex string; verify error caught and logged.
22. **Int256 overflow in Swap log**: Decode extreme overflow/underflow swap amounts; verify limits are handled.
23. **FastAPI server crash resilience**: Abruptly close API server; verify client code receives connection exception cleanly.
24. **MCP stdin EOF**: Close MCP server stdin; verify clean exit of MCP server process without hanging.
25. **Extremely large decimals**: Decimals parameter is set to 36; verify math handles without floating point crash.
26. **Empty factory return**: Factory returns `0x0000...0000` (pool not created); verify transport logs error and retries later.
27. **HTTP 500 Internal Error from RPC**: Verify HTTP 500 triggers the backoff retry mechanism.
28. **HTTP 503 Service Unavailable from RPC**: Verify HTTP 503 triggers backoff retry.
29. **x402 signature EIP-712 parsing**: Pass syntactically invalid signature JSON, assert rejection.
30. **Concurrent client calls**: Run 50 concurrent client calls to API server with mock payments; check stability.

### Tier 3: Cross-Feature Combinations (>=6 tests)
Tests the integration and intersection of multiple features.

1. **Pagination + Rate Limiting**: Query log range requiring multiple 500-block pagination slices, while simulating intermittent HTTP 429 responses. Verify all chunks are eventually queried and merged.
2. **Custom Symbol + Retries**: Configure a custom symbol dynamically. During factory lookup and slot0 queries, simulate RPC timeouts. Verify custom parameters are preserved across retries.
3. **x402 Payment Gating + Fast Block Production**: Make consecutive market data calls. Verify API server correctly parses the payment hashes, queries receipts across progressing block heights, and returns correct data.
4. **MCP Price fetching + RPC Rate Limiting**: Call MCP tool `get_onchain_price` under RPC rate-limiting. Assert MCP tool blocks/waits for retry backoff and returns valid price rather than failing immediately.
5. **Synthetic Depth + Custom Decimal Pool**: Register custom pool with 6 decimal base and 18 decimal quote. Polled reserves are processed into synthetic orderbook depth. Assert prices and sizing levels are accurately computed.
6. **Re-org + Pagination**: Trigger block reorg during active paginated log polling. Assert connector resets block ranges without duplicate log processing.

### Tier 4: Real-world Workloads (>=5 tests)
Simulates end-to-end production operations.

1. **Full Market Data Collection Pipeline**: Run `BaseOnchainConnector` with active polling, write outputs to `MemorySink` / Parquet, verify normalized parquet files can be queried using `CrypcodileClient` DuckDB SQL interface.
2. **Complete x402 Micropayment Flow**:
   - Client requests `/api/v1/market-data` without payment, receives 402 + Payment-Required header.
   - Client extracts payment details, submits transaction on mock node (simulated by seeding tx hash/receipt).
   - Client sends second request with payment details in headers.
   - API server queries mock node, verifies receipt, and yields market data.
3. **Showcase Script Offline Dry Run**: Execute `examples/collect_base_onchain.py --dry-run` pointing to Mock RPC. Verify connector initializes, retrieves a few mock ticks/swaps, logs records to console, and exits with code 0.
4. **MCP-driven Autonomous Agent Loop**: An external client connects to MCP server over stdin/stdout, queries available tools, calls `get_onchain_price` for active pool, and then calls `query_market_data` using DuckDB SQL to fetch historical averages. Verify complete cycle.
5. **Multi-pool Concurrent Ingestion under Stress**: Start connector with 4 concurrent pools (AERO-USDC, cbBTC-USDC, DEGEN-WETH, WELL-WETH). Simulate rapid block mining and sporadic swap logs on the mock node. Verify memory footprint is stable and all records are ingested without loss.

---

## 5. Pytest Fixture & Runner Blueprint

To run these tests offline, the test runner needs fixtures to manage the life cycle of the servers.

Below is the design for `tests/e2e/conftest.py`:

```python
import asyncio
import os
import subprocess
import time
import socket
import pytest
from typing import AsyncGenerator, Generator
import aiohttp

from .mock_rpc_server import start_mock_server

def get_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]

@pytest.fixture(scope="module")
async def mock_rpc() -> AsyncGenerator[tuple[str, int], None]:
    # Start Mock RPC server on dynamic port
    port = get_free_port()
    runner, actual_port = await start_mock_server(host="127.0.0.1", port=port)
    rpc_url = f"http://127.0.0.1:{actual_port}"
    
    yield rpc_url, actual_port
    
    await runner.cleanup()

@pytest.fixture(scope="module")
def api_server(mock_rpc) -> Generator[str, None, None]:
    rpc_url, _ = mock_rpc
    port = get_free_port()
    
    # Run API server subprocess overriding BASE_RPC_URL
    env = os.environ.copy()
    env["BASE_RPC_URL"] = rpc_url
    
    proc = subprocess.Popen(
        ["uv", "run", "uvicorn", "crypcodile.api_server:app", "--host", "127.0.0.1", "--port", str(port)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    
    # Wait for FastAPI to start
    time.sleep(1.5)
    api_url = f"http://127.0.0.1:{port}"
    
    yield api_url
    
    proc.terminate()
    proc.wait()

@pytest.fixture(scope="module")
def mcp_server_client(mock_rpc) -> Generator[subprocess.Popen, None, None]:
    rpc_url, _ = mock_rpc
    env = os.environ.copy()
    env["BASE_RPC_URL"] = rpc_url
    
    # Run MCP server subprocess (over stdin/stdout)
    proc = subprocess.Popen(
        ["uv", "run", "python", "-m", "crypcodile.mcp_server"],
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    
    yield proc
    
    proc.terminate()
    proc.wait()

@pytest.fixture(autouse=True)
async def clear_mock_rpc_state(mock_rpc):
    rpc_url, _ = mock_rpc
    # Clear and reset state of mock RPC server between tests
    async with aiohttp.ClientSession() as session:
        async with session.post(f"{rpc_url}/control/behavior", json={"status_code": 200, "error_count": 0, "delay": 0.0}):
            pass
        # Clear logs/receipts/pools control call can be executed here or via a dedicated /control/reset endpoint
```
