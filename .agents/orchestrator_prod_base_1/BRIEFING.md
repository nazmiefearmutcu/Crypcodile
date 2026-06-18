# BRIEFING — 2026-06-14T18:47:00+03:00

## Mission
Orchestrate the transition of the Crypcodile repository from a prototype to a production-ready, highly robust implementation of the Base on-chain integration.

## 🔒 My Identity
- Archetype: teamwork_preview_orchestrator
- Roles: orchestrator, user_liaison, human_reporter, successor
- Working directory: /Users/nazmi/Crypcodile/.agents/orchestrator_prod_base_1
- Original parent: Sentinel
- Original parent conversation ID: cbc2f186-0a86-4af6-b549-d53eb03e0bfa

## 🔒 My Workflow
- **Pattern**: Project Pattern
- **Scope document**: /Users/nazmi/Crypcodile/PROJECT.md
1. **Decompose**: Decompose requirements into milestones (architecture, async refactoring, pagination/retries, orderbook depth, payment verification, custom pools, E2E/unit tests).
2. **Dispatch & Execute** (pick ONE):
   - **Delegate (sub-orchestrator)**: When an item is too large, spawn a sub-orchestrator for it.
   - **Direct (iteration loop)**: For milestones, run Explorer -> Worker -> Reviewer -> Challenger -> Auditor loop.
3. **On failure** (in this order):
   - Retry: nudge stuck agent or re-send task
   - Replace: spawn fresh agent with partial progress
   - Skip: proceed without (only if non-critical)
   - Redistribute: split stuck agent's remaining work
   - Redesign: re-partition decomposition
   - Escalate: report to parent (sub-orchestrators only, last resort)
4. **Succession**: Self-succeed at 16 spawns, write handoff.md, spawn successor.
- **Work items**:
  1. Decompose requirements and create PROJECT.md [done]
  2. Implement E2E Test Suite [in-progress]
  3. Native AsyncWeb3 Refactoring (connector & mcp) [in-progress]
  4. Log Pagination & Exponential Backoff Retries [in-progress]
  5. Synthetic Multi-Level Orderbook Depth [in-progress]
  6. On-Chain x402 USDC Payment Verification [in-progress]
  7. Extensible Pool Configuration [in-progress]
  8. Integration and Verification [pending]
- **Current phase**: 2 (Dispatch & Execute)
- **Current focus**: Monitoring the E2E Testing and Implementation tracks.

## 🔒 Key Constraints
- NEVER write, modify, or create source code files directly.
- NEVER run build/test commands yourself — require workers to do so.
- You MAY use file-editing tools ONLY for metadata/state files (.md) in your .agents/ folder.
- Never reuse a subagent after it has delivered its handoff — always spawn fresh.
- Binary veto on Forensic Auditor integrity violations.

## Current Parent
- Conversation ID: cbc2f186-0a86-4af6-b549-d53eb03e0bfa
- Updated: not yet

## Key Decisions Made
- Chose Project Pattern with dual-track (Implementation + E2E Testing).

## Team Roster
| Agent | Type | Work Item | Status | Conv ID |
|-------|------|-----------|--------|---------|
| sub_orch_e2e_testing | self | E2E Testing Track (Tiers 1-4) | completed | 51cccefd-dfa4-4a63-8e2d-d39995b2f901 |
| sub_orch_implementation | self | Implementation Track (F1-F6) | completed | f7ccc9ac-6e76-4c80-b271-091bc7b6b43d |

## Succession Status
- Succession required: no
- Spawn count: 4 / 16
- Pending subagents: none
- Predecessor: none
- Successor: not yet spawned

## Active Timers
- Heartbeat cron: none (killed)
- Safety timer: none
- On succession: kill all timers before spawning successor
- On context truncation: run `manage_task(Action="list")` — re-create if missing

## Artifact Index
- /Users/nazmi/Crypcodile/.agents/orchestrator_prod_base_1/ORIGINAL_REQUEST.md — Original request copy
- /Users/nazmi/Crypcodile/.agents/orchestrator_prod_base_1/BRIEFING.md — Persistent state / briefing
