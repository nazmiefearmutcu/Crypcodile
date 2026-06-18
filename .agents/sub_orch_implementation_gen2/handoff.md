# Soft Handoff — Implementation Track (Gen 2 to Gen 3)

## Milestone State
- **Milestone 1**: Native AsyncWeb3 refactoring — **DONE**. All 723 unit/E2E/adversarial tests pass, builds compile successfully, and all connection leak warnings are resolved.
- **Milestone 2**: Log pagination & backoff retries — **IN_PROGRESS**. (Note: While some pagination and backoff retry logic was written during Milestone 1 remediation, a systematic validation loop must be run for M2).
- **Milestone 3**: Multi-level orderbook depth calculations — **PENDING** (currently dummy depth=1 facade in normalize.py).
- **Milestone 4**: Production-ready x402 USDC payment verification — **PENDING** (currently basic payment receipt checks, but needs systematic validation).
- **Milestone 5**: Extensible custom pool configuration — **PENDING** (dynamic registration logic implemented in connector.py, but needs systematic validation).

## Active Subagents
- None. All spawned subagents are complete or failed.

## Key Decisions and Context
1. **Milestone 1 Bugs Resolved**:
   - Fixed the `AsyncWeb3(AsyncHTTPProvider)` context manager TypeError by manually instantiating AsyncWeb3 and using a `try...finally` block with `await w3.provider.disconnect()`.
   - Fixed `MagicMock` await failures in unit tests by ensuring `w3.provider.disconnect` is checked for awaitability before call.
   - Fixed USDC topic validation formatting (missing `"0x"` prefix).
   - Fixed missing transaction hash error status (returning 400 instead of 500 on `TransactionNotFound`).
   - Fixed double-spend/replay attacks by maintaining checks for duplicate transaction hashes in `PAYMENTS_DB`.
   - Fixed coroutine await bug inside `connector.py`'s `_get_block_number`.
   - Fixed block cursor rollbacks on lags by updating cursors monotonically (`max(last_blocks, current_block)`).
   - Fixed IPC pool config race conditions using `fcntl` Unix file locking.

2. **Test Baseline**: All 723 tests pass cleanly in ~45 seconds. `uv build` compiles with Hatch.

## Remaining Work for Successor
1. **Resume Implementation**:
   - Begin with Milestone 2 (Log pagination & backoff retries). Formulate plans and run the iteration loop (Explorer -> Worker -> Reviewers -> Challenger -> Auditor) with Forensic Auditor validation.
   - Proceed sequentially to Milestones 3, 4, and 5.
   - For each milestone, do NOT combine them. Run the full iteration loop.
   - Keep `PROJECT.md` and `SCOPE.md` updated as status changes.

## Key Artifacts
- Global project layout: `/Users/nazmi/Crypcodile/PROJECT.md`
- Original user request: `/Users/nazmi/Crypcodile/ORIGINAL_REQUEST.md`
- Scope document: `/Users/nazmi/Crypcodile/.agents/sub_orch_implementation_gen2/SCOPE.md`
- Progress heartbeat: `/Users/nazmi/Crypcodile/.agents/sub_orch_implementation_gen2/progress.md`
- Briefing: `/Users/nazmi/Crypcodile/.agents/sub_orch_implementation_gen2/BRIEFING.md`
- Last worker handoff: `/Users/nazmi/Crypcodile/.agents/worker_m1_remediation/handoff.md`
