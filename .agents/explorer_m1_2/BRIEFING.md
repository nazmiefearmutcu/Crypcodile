# BRIEFING — 2026-06-14T15:49:46Z

## Mission
Analyze the codebase for Milestone 1: Native AsyncWeb3 refactoring (connector and mcp_server.py) and provide a step-by-step fix strategy.

## 🔒 My Identity
- Archetype: teamwork_preview_explorer
- Roles: Codebase Explorer
- Working directory: /Users/nazmi/Crypcodile/.agents/explorer_m1_2
- Original parent: cc7e5b69-9d39-48f9-a41b-d6135c7918c4
- Milestone: Milestone 1: Native AsyncWeb3 refactoring (connector and mcp_server.py)

## 🔒 Key Constraints
- Read-only investigation — do NOT implement
- Run in CODE_ONLY mode

## Current Parent
- Conversation ID: cc7e5b69-9d39-48f9-a41b-d6135c7918c4
- Updated: 2026-06-14T15:48:31Z

## Investigation State
- **Explored paths**:
  - `src/crypcodile/exchanges/base_onchain/connector.py`
  - `src/crypcodile/mcp_server.py`
  - `tests/exchanges/base_onchain/test_connector.py`
  - `tests/exchanges/base_onchain/test_adversarial.py`
  - `tests/exchanges/base_onchain/test_challenger_stress_2.py`
  - `tests/exchanges/base_onchain/test_challenger_stress_3.py`
- **Key findings**:
  - `connector.py` wraps sync Web3 operations in `asyncio.to_thread`.
  - `mcp_server.py`'s `get_onchain_price` is fully blocking on main loop.
  - Tests patch `Web3` synchronously and must be updated to `AsyncWeb3` with `AsyncMock`, `PropertyMock` for properties, and `async def` custom classes.
- **Unexplored areas**: None

## Key Decisions Made
- Avoid unnecessary `asyncio.to_thread` wrappers.
- Update custom mock classes in stress tests to support native async/await.

## Artifact Index
- ORIGINAL_REQUEST.md — Original task description
- BRIEFING.md — Current briefing state
- progress.md — Progress tracking
- analysis.md — In-depth analysis of codebase and proposed changes
