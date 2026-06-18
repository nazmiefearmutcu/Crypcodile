# BRIEFING — 2026-06-15T01:45:00+03:00

## Mission
Run the test suite of Crypcodile, inspect any test failures, and fix any bugs in src/crypcodile/api_server.py or src/crypcodile/exchanges/base_onchain/connector.py to ensure all tests pass.

## 🔒 My Identity
- Archetype: Hardener / Bug Fixer
- Roles: implementer, qa, specialist
- Working directory: /Users/nazmi/Crypcodile/.agents/worker_prod_hardening_gen3
- Original parent: 3409b06d-ce94-4e6e-a23b-79424b5bca6c
- Milestone: Test Suite Stabilization

## 🔒 Key Constraints
- Only modify src/crypcodile/api_server.py or src/crypcodile/exchanges/base_onchain/connector.py.
- Do not modify tests themselves to bypass checks, except if tests have bugs or mocks are outdated.
- Output report to /Users/nazmi/Crypcodile/.agents/worker_prod_hardening_gen3/handoff.md.
- Send a completion message back to the parent orchestrator.

## Current Parent
- Conversation ID: 3409b06d-ce94-4e6e-a23b-79424b5bca6c
- Updated: 2026-06-15T01:45:00+03:00

## Task Summary
- **What to build**: Fix bugs in api_server.py and/or base_onchain/connector.py.
- **Success criteria**: All tests in the test suite pass cleanly and no state leakage occurs.
- **Interface contracts**: Clean APIs and proper state management in Crypcodile api_server and connector.
- **Code layout**: src/crypcodile/api_server.py and src/crypcodile/exchanges/base_onchain/connector.py.

## Change Tracker
- **Files modified**:
  - src/crypcodile/api_server.py: Modified `PersistentDict` to implement the `_sync` pattern.
  - src/crypcodile/exchanges/base_onchain/connector.py: Modified `IPCDict` to implement the `_sync` pattern and restore `_write_ipc`.
- **Build status**: All tests pass (765/765).
- **Pending issues**: None

## Quality Status
- **Build/test result**: Pass (765 tests passed)
- **Lint status**: Clean for modified files
- **Tests added/modified**: None (fixed underlying bugs in api_server.py and connector.py to align with tests)

## Key Decisions Made
- Made `PersistentDict` and `IPCDict` dynamically synchronise with the filesystem based on the active environment file path (`PAYMENTS_FILE` and `CUSTOM_POOLS_IPC_FILE`), completely resolving cross-test state leakages.

## Artifact Index
- /Users/nazmi/Crypcodile/.agents/worker_prod_hardening_gen3/ORIGINAL_REQUEST.md — Original request instructions
