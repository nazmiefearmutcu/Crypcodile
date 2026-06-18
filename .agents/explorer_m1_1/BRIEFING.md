# BRIEFING — 2026-06-14T15:50:00Z

## Mission
Analyze codebase to refactor connector.py and mcp_server.py from synchronous Web3 to native AsyncWeb3/AsyncHTTPProvider, and plan corresponding test modifications.

## 🔒 My Identity
- Archetype: Codebase Explorer
- Roles: Read-only investigator, analyzer
- Working directory: /Users/nazmi/Crypcodile/.agents/explorer_m1_1
- Original parent: cc7e5b69-9d39-48f9-a41b-d6135c7918c4
- Milestone: Milestone 1: Native AsyncWeb3 refactoring (connector and mcp_server.py)

## 🔒 Key Constraints
- Read-only investigation — do NOT implement
- Code-only network mode: no external HTTP/HTTPS connections. No curl/wget/etc to external URLs.

## Current Parent
- Conversation ID: cc7e5b69-9d39-48f9-a41b-d6135c7918c4
- Updated: 2026-06-14T15:50:00Z

## Investigation State
- **Explored paths**:
  - `src/crypcodile/exchanges/base_onchain/connector.py`
  - `src/crypcodile/mcp_server.py`
  - `src/crypcodile/api_server.py`
  - `tests/exchanges/base_onchain/test_connector.py`
  - `tests/exchanges/base_onchain/test_adversarial.py`
  - `tests/exchanges/base_onchain/test_challenger_stress_2.py`
  - `tests/exchanges/base_onchain/test_challenger_stress_3.py`
- **Key findings**:
  - Identified all 6 categories of synchronous Web3 calls wrapped in `asyncio.to_thread` inside `connector.py`.
  - Identified synchronous `get_onchain_price` in `mcp_server.py` and its downstream usages (including `api_server.py`).
  - Identified the exact mocking strategies across the 4 test files under `tests/exchanges/base_onchain/` and how they should be updated for `AsyncWeb3`.
- **Unexplored areas**: None.

## Key Decisions Made
- Include `src/crypcodile/api_server.py` in the step-by-step fix strategy since converting `get_onchain_price` to `async` requires updating its FastAPI route to await it.
- Include all 4 test files under `tests/exchanges/base_onchain/` since they all mock Web3 and will break if the mock is not updated.

## Artifact Index
- /Users/nazmi/Crypcodile/.agents/explorer_m1_1/analysis.md — Detailed analysis findings
- /Users/nazmi/Crypcodile/.agents/explorer_m1_1/handoff.md — Handoff report with fix strategy
