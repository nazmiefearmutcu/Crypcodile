# BRIEFING — 2026-06-20T18:40:17+03:00

## Mission
Extend the Crypcodile CLI and its interactive shell with three new analytics commands: a Slippage Estimator, an Order Flow Imbalance (OFI) calculator, and a Whale/Liquidation Alert tracker, fully integrated with autocomplete and covered by unit tests.

## 🔒 My Identity
- Archetype: teamwork_preview_orchestrator
- Roles: orchestrator, user_liaison, human_reporter, successor
- Working directory: /Users/nazmi/Crypcodile/.agents/orchestrator
- Original parent: parent
- Original parent conversation ID: 78954009-a941-4722-9a8c-7ae4ce50e247

## 🔒 My Workflow
- **Pattern**: Project Pattern (Simplified)
- **Scope document**: /Users/nazmi/Crypcodile/PROJECT.md
1. **Decompose**: Decompose the task into milestones (Milestone 1: Slippage command logic and tests, Milestone 2: OFI command logic and tests, Milestone 3: Whale-alerts command logic and tests, Milestone 4: Shell integration and complete verification).
2. **Dispatch & Execute**:
   - **Delegate (sub-orchestrator)**: Spawn a worker or explorer to research/implement each milestone, followed by reviewer, challenger, and auditor check.
3. **On failure** (in this order):
   - Retry: nudge stuck agent or re-send task
   - Replace: spawn fresh agent with partial progress
   - Skip: proceed without (only if non-critical)
   - Redistribute: split stuck agent's remaining work
   - Redesign: re-partition decomposition
   - Escalate: report to parent (sub-orchestrators only, last resort)
4. **Succession**: Self-succeed at 16 spawns, write handoff.md, spawn successor.
- **Work items**:
  1. Decompose requirements and design [done]
  2. Implement Slippage Estimator command (`slippage`) [pending]
  3. Implement Order Flow Imbalance command (`ofi`) [pending]
  4. Implement Whale/Liquidation Alerts command (`whale-alerts`) [pending]
  5. CLI/Shell integration, Autocomplete, and Unified testing [pending]
- **Current phase**: 1 (Decompose)
- **Current focus**: Planning and Initial Explorer Dispatch

## 🔒 Key Constraints
- CODE_ONLY network mode: No external internet access.
- NEVER write, modify, or create source code files directly.
- NEVER run build/test commands yourself — require workers to do so.
- Audit is a binary veto.

## Current Parent
- Conversation ID: 78954009-a941-4722-9a8c-7ae4ce50e247
- Updated: not yet

## Key Decisions Made
- Use Project Pattern to implement the new commands sequentially or in parallel depending on dependency. Since they all reside in `src/crypcodile/cli.py` and might touch normalizer/logic, we'll decompose and implement them systematically.

## Team Roster
| Agent | Type | Work Item | Status | Conv ID |
|-------|------|-----------|--------|---------|
| explorer_1 | teamwork_preview_explorer | Codebase Research (M1) | completed | c6394247-9e33-406e-a8bc-083cadaf6beb |
| worker_1 | teamwork_preview_worker | Implementation (M2-M5) | completed | b0279722-eb1b-4552-b65b-45fb9b2b6bbd |
| worker_2 | teamwork_preview_worker | Verification & Build | completed | 823544e5-0111-40a2-9528-dcfd47d87dc3 |
| auditor | teamwork_preview_auditor | Forensic Integrity Audit | completed | 09f0e31c-634c-4fb3-822b-1c33c0aaf521 |

## Succession Status
- Succession required: no
- Spawn count: 4 / 16
- Pending subagents: none
- Predecessor: none
- Successor: not yet spawned

## Active Timers
- Heartbeat cron: not started
- Safety timer: none
- On succession: kill all timers before spawning successor
- On context truncation: run `manage_task(Action="list")` — re-create if missing

## Artifact Index
- /Users/nazmi/Crypcodile/.agents/orchestrator/plan.md — Project Plan
- /Users/nazmi/Crypcodile/.agents/orchestrator/progress.md — Progress Checklist
- /Users/nazmi/Crypcodile/PROJECT.md — Global project layout and milestones
