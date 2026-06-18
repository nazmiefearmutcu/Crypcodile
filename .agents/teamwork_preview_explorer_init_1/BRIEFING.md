# BRIEFING — 2026-06-14T14:06:06Z

## Mission
Investigate base_onchain connector implementation, test suite execution, mocking approaches in other connectors, and report codebase structure.

## 🔒 My Identity
- Archetype: Codebase Researcher
- Roles: Codebase Researcher, Explorer
- Working directory: /Users/nazmi/Crypcodile/.agents/teamwork_preview_explorer_init_1
- Original parent: 7a442407-8d07-42d2-bfba-7ac29c0666e1
- Milestone: Initial exploration

## 🔒 Key Constraints
- Read-only investigation — do NOT implement
- Rely only on local tools for search and files
- Keep briefing files updated with findings and decisions

## Current Parent
- Conversation ID: 7a442407-8d07-42d2-bfba-7ac29c0666e1
- Updated: 2026-06-14T14:07:53Z

## Investigation State
- **Explored paths**: `src/crypcodile/exchanges/base_onchain/connector.py`, `src/crypcodile/exchanges/base_onchain/normalize.py`, `src/crypcodile/mcp_server.py`, `src/crypcodile/api_server.py`, `tests/` (exchanges connectors and factory tests).
- **Key findings**: Identified 5 critical bugs in `base_onchain` connector price/reserve/swap calculations due to EVM pool address flipping, and a liveness generator hang.
- **Unexplored areas**: None.

## Key Decisions Made
- Performed detailed read-only codebase investigation of the `base_onchain` connector.
- Produced `analysis.md` and `handoff.md` summarizing the findings.
- Drafted a proposed mock unit test file `proposed_test_connector.py`.

## Artifact Index
- /Users/nazmi/Crypcodile/.agents/teamwork_preview_explorer_init_1/ORIGINAL_REQUEST.md — Original task details
- /Users/nazmi/Crypcodile/.agents/teamwork_preview_explorer_init_1/BRIEFING.md — Exploration state tracker
- /Users/nazmi/Crypcodile/.agents/teamwork_preview_explorer_init_1/analysis.md — Deep exploration report on base_onchain connector
- /Users/nazmi/Crypcodile/.agents/teamwork_preview_explorer_init_1/handoff.md — Completed handoff report
- /Users/nazmi/Crypcodile/.agents/teamwork_preview_explorer_init_1/proposed_test_connector.py — Proposed unit test implementation for base_onchain
