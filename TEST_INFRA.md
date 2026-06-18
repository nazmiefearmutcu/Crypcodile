# Crypcodile E2E Test Infrastructure Documentation

This document describes the offline-first End-to-End (E2E) testing infrastructure implemented for the Crypcodile codebase transition.

## Architecture Overview

The E2E testing framework is designed to run completely offline, utilizing a programmatically controllable mock Ethereum JSON-RPC node to intercept blockchain traffic from all Crypcodile components:

```
                  +-----------------------------------+
                  |           Pytest Runner           |
                  +-----+-------------+-------------+-+
                        |             |             |
                        | (Starts)    | (Starts)    | (Starts)
                        v             v             v
  +-----------------------+     +-----------+     +-------------+
  |    Mock RPC Server    |<----+api_server |     | mcp_server  |
  | (aiohttp on dynamic   |     | (FastAPI) |     |  (Stdio)    |
  |  port via control API)|     +-----+-----+     +------+------+
  +-----------------------+           |                  |
              ^                       |                  |
              |                       v                  v
              +-----------------------+------------------+
                     (Routes JSON-RPC via BASE_RPC_URL)
```

## Core Components

### 1. Mock RPC Server (`tests/e2e/mock_rpc_server.py`)
A lightweight, asynchronous `aiohttp` web server running on a dynamic free port during tests. It implements:
* **JSON-RPC Route (`POST /`)**: Simulates the Base Mainnet RPC node.
  * `eth_blockNumber`: Returns head block heights.
  * `eth_getBlockByNumber`: Resolves block timestamps.
  * `eth_getLogs`: Filters simulated Uniswap V3 Swap logs and Aerodrome V2 Swap logs.
  * `eth_getTransactionReceipt`: Simulates payment receipts containing USDC Transfer events.
  * `eth_call`: Dispatches contract calls to seed factories and pool slot0/reserves functions.
* **Control REST API (`POST /control/...`)**: Allows pytest fixtures to dynamically configure the node's state:
  * `/control/block`: Set the mock block number and timestamp.
  * `/control/pool`: Seed details of a pool (address, factory, token contracts, fee, stable, reserves, liquidity, etc.).
  * `/control/logs`: Seed raw log data for block queries.
  * `/control/receipt`: Seed a mock transaction receipt for USDC verification.
  * `/control/behavior`: Mock rate limiting (HTTP 429), server errors (HTTP 500/503), and response delays.
  * `/control/reset`: Clean all configurations between tests.

### 2. Pytest Fixtures (`tests/e2e/conftest.py`)
Manages the lifecycles of all test targets:
* `mock_rpc`: Instantiates the mock node and yields its URL.
* `api_server`: Spawns the FastAPI application in a supervised subprocess, overriding `BASE_RPC_URL` to point to the local mock node.
* `mcp_server_client`: Spawns the MCP JSON-RPC CLI in a supervised subprocess.
* `clear_mock_rpc_state`: Reset the mock node state automatically before each test function execution.

## Advantages of This Infrastructure
1. **100% Offline**: Zero external network requests are made.
2. **Deterministic & Fast**: Tests run locally on memory-backed mock state, executing the complete suite of 74 tests in under 30 seconds.
3. **Robust Connection Testing**: The control API enables setting simulated network errors and rate-limiting limits, testing components' native AsyncWeb3 retry middleware behaviors under stress.
