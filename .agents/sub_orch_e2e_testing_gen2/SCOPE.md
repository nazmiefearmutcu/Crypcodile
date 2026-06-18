# Scope: E2E Testing Track

## Architecture
- A lightweight HTTP mock JSON-RPC server (based on `aiohttp` or `fastapi`) running on a dynamic local port.
- Configurable RPC responses for block numbers, receipts, contract calls (`eth_call`), and log polling (`eth_getLogs`).
- Decoupled test runner starting/stopping the mock RPC server, the FastAPI api_server, the MCP server, and running pytest against them.
- Features to test: F1, F2, F3, F4, F5, F6.

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| 1 | Exploration & Test Arch | Analyze current codebase, structure tests/e2e/ folder and build the mock RPC server harness | None | DONE |
| 2 | Tier 1: Feature Coverage | Implement >=30 tests verifying F1-F6 features in isolation using mock RPC server | M1 | DONE |
| 3 | Tier 2: Boundary & Corner | Implement >=30 tests covering edge cases (empty data, large pagination, connection timeout, rate-limiting, malformed signature) | M2 | DONE |
| 4 | Tier 3: Cross-Feature Combinations | Implement >=6 tests verifying interactions (e.g. pagination + custom symbol, retries + x402 verification) | M3 | DONE |
| 5 | Tier 4: Real-world Workloads | Implement >=5 tests verifying end-to-end user workflows (e.g. running CLI + API server + payment flow) | M4 | DONE |
| 6 | Verification & Reports | Run the full test suite, verify 100% success, publish TEST_READY.md and TEST_INFRA.md | M5 | DONE |

## Interface Contracts
- Mock RPC Server: listens on `http://localhost:<PORT>`, simulates standard JSON-RPC over HTTP.
- Test Cases: call CLI, API Server, and MCP Server via stdout/HTTP/stdin, asserting correctness.
