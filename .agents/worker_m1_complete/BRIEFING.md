# BRIEFING — 2026-06-14T19:05:22+03:00

## Mission
Implement Milestones 1 to 5 to resolve all regressions, socket leaks, and integrity violations, and fix E2E tests, ensuring all tests pass cleanly.

## 🔒 My Identity
- Archetype: worker
- Roles: implementer, qa, specialist
- Working directory: /Users/nazmi/Crypcodile/.agents/worker_m1_complete
- Original parent: cc7e5b69-9d39-48f9-a41b-d6135c7918c4
- Milestone: Complete Implementation (Milestones 1 to 5)

## 🔒 Key Constraints
- CODE_ONLY network mode: no external HTTP/HTTPS requests (no curl/wget/lynx to external URLs).
- Do not cheat, do not hardcode test results, do not create dummy/facade implementations.
- Write only to our agent folder (/Users/nazmi/Crypcodile/.agents/worker_m1_complete) for agent metadata.
- All code files must be edited in place with minimal changes.

## Current Parent
- Conversation ID: cc7e5b69-9d39-48f9-a41b-d6135c7918c4
- Updated: not yet

## Task Summary
- **What to build**: 
  1. Fix socket leak in `src/crypcodile/mcp_server.py` using `async with AsyncWeb3(...) as w3:` context manager inside `get_onchain_price`.
  2. Implement block range pagination (max 500 blocks per chunk) and exponential backoff retry logic for async network/RPC queries in `src/crypcodile/exchanges/base_onchain/connector.py`.
  3. Implement multi-level orderbook depth (at least 5 bid and 5 ask levels) for Uniswap V3 (using ticks, tick spacing, active tick/liquidity) and Aerodrome V2 (using reserves and spread math) in `normalize.py` and pass state parameters (`tick`, `liquidity`, `tick_spacing`) in `connector.py`.
  4. Production-ready USDC payment verification in `src/crypcodile/api_server.py`: query Tx receipt via `AsyncWeb3` on Base mainnet, validate status == 1, ERC-20 transfer event from official USDC contract `0x833589fCD6eDb6E08f4c7C32D4f71b54bda02913`, designated recipient `RECIPIENT_WALLET`, and transfer amount exactly 1000 base units (0.001 USDC). Proper HTTPException on failure.
  5. Support custom pool parameters passed dynamically during initialization in connector.
  6. Fix E2E test `test_smoke_e2e.py` failure (Non-hexadecimal digit found).
- **Success criteria**: All pytest tests (642 tests) pass successfully. `uv build` compiles cleanly. `handoff.md` written with passing build/test outputs.
- **Interface contracts**: src/crypcodile/
- **Code layout**: src/crypcodile/

## Change Tracker
- **Files modified**: None
- **Build status**: TBD
- **Pending issues**: None

## Quality Status
- **Build/test result**: TBD
- **Lint status**: TBD
- **Tests added/modified**: TBD

## Loaded Skills
- None

## Key Decisions Made
- [TBD]

## Artifact Index
- `/Users/nazmi/Crypcodile/.agents/worker_m1_complete/handoff.md` — Handoff report
- `/Users/nazmi/Crypcodile/.agents/worker_m1_complete/progress.md` — Liveness heartbeat progress file
