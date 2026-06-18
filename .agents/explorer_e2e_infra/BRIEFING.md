# BRIEFING — 2026-06-14T15:48:32Z

## Mission
Explore Crypcodile codebase to discover all Web3/RPC calls and design a Mock RPC Server and E2E Test Harness architecture.

## 🔒 My Identity
- Archetype: explorer
- Roles: E2E Testing Explorer
- Working directory: /Users/nazmi/Crypcodile/.agents/explorer_e2e_infra
- Original parent: b103c05a-9bc0-4cef-8531-4a20596ad429
- Milestone: E2E Test Infrastructure Design

## 🔒 Key Constraints
- Read-only investigation — do NOT implement code changes.
- CODE_ONLY network mode: no external HTTP/HTTPS requests.

## Current Parent
- Conversation ID: b103c05a-9bc0-4cef-8531-4a20596ad429
- Updated: 2026-06-14T15:50:00Z

## Investigation State
- **Explored paths**: `src/crypcodile/exchanges/base_onchain/connector.py`, `src/crypcodile/exchanges/base_onchain/normalize.py`, `src/crypcodile/mcp_server.py`, `src/crypcodile/api_server.py`, `tests/exchanges/base_onchain/test_connector.py`, `tests/exchanges/base_onchain/test_adversarial.py`
- **Key findings**: Identified all 5 primary Ethereum JSON-RPC calls (`eth_blockNumber`, `eth_getBlockByNumber`, `eth_getLogs`, `eth_getTransactionReceipt`, `eth_call`) with their specific parameter patterns and ABI-encoded output layouts.
- **Unexplored areas**: None.

## Key Decisions Made
- Selected `aiohttp` web application framework for the Mock RPC Server implementation due to native asyncio support.
- Recommended a control REST API `/control/...` for the Mock RPC Server to dynamically program states (reserves, block height, logs, rate limits) per test.
- Divided the E2E test plan into Tiers 1-4 with specific pytest fixtures and test runner configurations in `tests/e2e/`.

## Artifact Index
- /Users/nazmi/Crypcodile/.agents/explorer_e2e_infra/analysis.md — Final design and analysis report
- /Users/nazmi/Crypcodile/.agents/explorer_e2e_infra/handoff.md — Handoff report following the 5-component protocol
