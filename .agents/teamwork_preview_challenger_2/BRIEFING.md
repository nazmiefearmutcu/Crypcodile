# BRIEFING — 2026-06-14T14:15:30Z

## Mission
Empirically verify the correctness of the base_onchain connector and its normalizer logic by writing edge-case scenarios or executing stress tests.

## 🔒 My Identity
- Archetype: critic
- Roles: critic, specialist
- Working directory: /Users/nazmi/Crypcodile/.agents/teamwork_preview_challenger_2
- Original parent: 7a442407-8d07-42d2-bfba-7ac29c0666e1
- Milestone: Verification
- Instance: 1 of 1

## 🔒 Key Constraints
- Stress-test assumptions, find failure modes, propose counter-examples.
- Do NOT modify implementation code (Review-only/Verifier role, do not fix issues, report them).
- Network: CODE_ONLY.

## Current Parent
- Conversation ID: 7a442407-8d07-42d2-bfba-7ac29c0666e1
- Updated: not yet

## Review Scope
- **Files to review**: `src/crypcodile/exchanges/base_onchain/connector.py`, `src/crypcodile/exchanges/base_onchain/normalize.py`
- **Interface contracts**: Correctness of normalized output, robustness against extreme prices, large numbers of swaps, very small amounts, corrupted or missing dictionary updates.
- **Review criteria**: Correctness, reliability, graceful error handling.

## Loaded Skills
- None

## Attack Surface
- **Hypotheses tested**:
  - *Hypothesis 1*: Division by zero or overflow on extreme prices. Tested with price = 1e300, 1e-300, 1e-323, 0.0, -12.34. Results: Handled gracefully (early return or valid values).
  - *Hypothesis 2*: Memory leak or failure with large numbers of swaps. Tested with 5000 swaps. Results: Handled gracefully (yields correct Trade records).
  - *Hypothesis 3*: Loss of precision or crash on extremely small trade/reserve sizes. Tested with 1e-20 reserves and 1e-18 trade sizes. Results: Enforces 0.0001 minimum orderbook sizing gracefully.
  - *Hypothesis 4*: Ingestion/connector loop crashes due to corrupted inputs or missing fields. Tested via dictionary key omission. Results: Exceptions are propagated by `normalize`, but safely intercepted and logged by `Connector.run` without crashing the core thread.
  - *Hypothesis 5*: RPC connection failure or logs query failure crashing the connector. Tested via Web3.eth mocked exceptions. Results: Caught and logged by transport `_poll_loop`, retry behavior preserves liveness.
- **Vulnerabilities found**: No critical bugs. Redundant `msg["pool_type"]` lookup line in `normalize.py:28` without variable assignment (doesn't cause issues, just key verification). Flipped pool price math assertions initially corrected.
- **Untested angles**: Hardware failure, OS memory exhaustion (out-of-scope).

## Key Decisions Made
- Wrote a new test file `tests/exchanges/base_onchain/test_adversarial.py` to keep stress tests isolated from standard unit tests.
- Re-ran tests using `uv run pytest` to ensure environment consistency.

## Artifact Index
- `tests/exchanges/base_onchain/test_adversarial.py` — Adversarial stress tests for edge-cases.
- `/Users/nazmi/Crypcodile/.agents/teamwork_preview_challenger_2/challenge.md` — Detailed challenge report.
- `/Users/nazmi/Crypcodile/.agents/teamwork_preview_challenger_2/handoff.md` — 5-Component handoff report.
