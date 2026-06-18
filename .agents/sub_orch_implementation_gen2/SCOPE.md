# Scope: Implementation Track

## Architecture
- Connector: `src/crypcodile/exchanges/base_onchain/connector.py` (polls block/logs, supports pagination and backoff, AsyncWeb3)
- Normalizer: `src/crypcodile/exchanges/base_onchain/normalize.py` (calculates 5-level orderbook depth)
- MCP server: `src/crypcodile/mcp_server.py` (async price fetching)
- API server: `src/crypcodile/api_server.py` (FastAPI gated endpoint with USDC on-chain validation)

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| 1 | Native AsyncWeb3 refactoring | Refactor connector and mcp_server to use AsyncWeb3 and AsyncHTTPProvider natively. | None | DONE |
| 2 | Log pagination & backoff retries | 500 block chunks log polling + exponential backoff retries. | M1 | IN_PROGRESS |
| 3 | Multi-level orderbook depth | 5-level bids/asks depth calculation from active ticks/liquidity. | M2 | PLANNED |
| 4 | Production-ready x402 USDC payment | AsyncWeb3-based transaction log verification. | M3 | PLANNED |
| 5 | Extensible custom pool config | Custom pool params via init. | M4 | PLANNED |

## Interface Contracts
### base_onchain ↔ normalize
- Input: Raw `onchain_update` payload containing `pool_type`, price, reserves, and V3 fields.
- Output: Normalized Trade, BookTicker, and BookSnapshot with 5 bids and 5 asks.

### api_server ↔ Base Mainnet
- Input: `tx_hash` from header.
- Output: Verification result verifying status, contract address, recipient, and amount (0.001 USDC).
