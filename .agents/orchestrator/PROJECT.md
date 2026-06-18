# Project: Crypcodile Base Builder Grants Preparation

## Architecture
- `BaseOnchainConnector` (`src/crypcodile/exchanges/base_onchain/connector.py`) is a Python class inheriting from `Connector`.
- It initializes a `BaseOnchainTransport` which runs a polling loop querying Ethereum block number, contracts (`slot0` for Uniswap V3, `getReserves` for Aerodrome V2), and filters logs via Web3 provider RPC.
- Msg normalization uses `normalize_onchain_update` (`src/crypcodile/exchanges/base_onchain/normalize.py`) to map incoming raw pool state updates to `BookTicker` and `BookSnapshot` records, and raw swaps to `Trade` records.
- `mcp_server.py` implements a tool `get_onchain_price` that queries the contracts directly on-chain.

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|---|---|---|---|
| 1 | Complete Base On-Chain Connector | Fix pricing decimals, swap log parsing, Aerodrome flipped state, queue liveness hang, block caching in `connector.py`, `normalize.py`, and `mcp_server.py`. | None | DONE |
| 2 | Unit Test Suite & Mock Fixtures | Create `tests/exchanges/base_onchain/test_connector.py` with mock contracts and mock web3 to run offline and verify calculations. | M1 | DONE |
| 3 | Showcase Example Script | Create `examples/collect_base_onchain.py` demonstrating connection and subscription with public URL/env overrides and `--dry-run` exit. | M1 | DONE |
| 4 | PyPI Publishing & Build Verification | Version bump pyproject.toml to "0.1.0", update README.md, verify package builds successfully. | M2, M3 | DONE |
| 5 | White-Box & Adversarial Hardening | Run Challenger coverage audits and generate stress tests to verify robustness of the solution. | M4 | DONE |

## Interface Contracts
### BaseOnchainTransport ↔ BaseOnchainConnector
- `BaseOnchainTransport` queues serialized JSON payloads.
- `BaseOnchainConnector` subscribes to the transport and yields `Record` objects by passing JSON payloads to `normalize_onchain_update`.

## Code Layout
- `src/crypcodile/exchanges/base_onchain/connector.py` — Connector and Transport implementation.
- `src/crypcodile/exchanges/base_onchain/normalize.py` — Event and state normalization.
- `src/crypcodile/mcp_server.py` — MCP server price endpoint.
- `tests/exchanges/base_onchain/test_connector.py` — Connector unit tests.
- `examples/collect_base_onchain.py` — Script to showcase execution.
- `pyproject.toml` — Project configuration and versioning.
- `README.md` — Project documentation.
