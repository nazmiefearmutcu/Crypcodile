# BRIEFING — 2026-06-15T01:45:27+03:00

## Mission
Implement Milestone 5: Extensible custom pool configuration in `src/crypcodile/exchanges/base_onchain/connector.py`, resolve codebase gaps, and verify via unit and integration tests.

## 🔒 My Identity
- Archetype: teamwork_preview_worker
- Roles: implementer, qa, specialist
- Working directory: /Users/nazmi/Crypcodile/.agents/teamwork_preview_worker_m5_gen4/
- Original parent: e72b6678-f50d-4a4f-9b0a-1b2f957b2a1e
- Milestone: Milestone 5

## 🔒 Key Constraints
- Safe and Robust IPC Persistence (IPCDict & _write_ipc_to_file)
- Input Validation for Custom Pools
- Dynamic Listing and Polling
- Test Suite Verification (100% pass)
- DO NOT CHEAT

## Current Parent
- Conversation ID: e72b6678-f50d-4a4f-9b0a-1b2f957b2a1e
- Updated: not yet

## Task Summary
- **What to build**: Extensible custom pool configuration support in the Base Onchain connector.
- **Success criteria**: All features implemented matching the specifications exactly, 100% tests pass including existing and new tests.
- **Interface contracts**: `src/crypcodile/exchanges/base_onchain/connector.py`
- **Code layout**: python package layout in `src/crypcodile/` and `tests/`

## Key Decisions Made
- Used a dedicated `.lock` file to coordinate POSIX flock read/write synchronization across processes to avoid race conditions with file replacement.
- Gracefully handled Mock/MagicMock address responses in tests to prevent TypeError.
- Symmetrically filtered symbols in list_instruments and _poll_loop to only target initial symbols plus dynamically registered custom pools.

## Artifact Index
- `/Users/nazmi/Crypcodile/.agents/teamwork_preview_worker_m5_gen4/changes.md` — Changes report listing implementations.
- `/Users/nazmi/Crypcodile/.agents/teamwork_preview_worker_m5_gen4/handoff.md` — Handoff report following the Handoff Protocol.
