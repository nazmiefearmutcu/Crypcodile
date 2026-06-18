# BRIEFING — 2026-06-14T21:24:40Z

## Mission
Analyze Milestone 2 (Log pagination & backoff retries) in src/crypcodile/exchanges/base_onchain/connector.py and related tests for correctness, completeness, and robustness.

## 🔒 My Identity
- Archetype: explorer
- Roles: Read-only investigator
- Working directory: /Users/nazmi/Crypcodile/.agents/explorer_m2_3
- Original parent: 5c0b98bd-4196-4f15-b3fa-8228abff7342
- Milestone: Milestone 2 (Log pagination & backoff retries)

## 🔒 Key Constraints
- Read-only investigation — do NOT implement or modify any files (except progress.md, briefing.md, analysis.md, handoff.md, etc. in explorer_m2_3 directory).
- CODE_ONLY network mode: No external network requests.

## Current Parent
- Conversation ID: 5c0b98bd-4196-4f15-b3fa-8228abff7342
- Updated: 2026-06-14T21:24:40Z

## Investigation State
- **Explored paths**:
  - `src/crypcodile/exchanges/base_onchain/connector.py`
  - `src/crypcodile/exchanges/base_onchain/normalize.py`
  - `tests/exchanges/base_onchain/test_connector.py`
  - `tests/exchanges/base_onchain/test_adversarial.py`
  - `tests/exchanges/base_onchain/test_challenger_stress_2.py`
  - `tests/exchanges/base_onchain/test_challenger_stress_3.py`
  - `tests/exchanges/base_onchain/test_challenger_stress_4.py`
  - `tests/exchanges/base_onchain/test_empirical_bugs.py`
- **Key findings**:
  - `UnboundLocalError`: In `connector.py`, if slot0/liquidity query fails, it raises `UnboundLocalError` in block C (referencing unbound `slot0` or `liquidity`). This escapes the inner try-catch, aborts the entire iteration cycle, and prevents other pools from being updated in that cycle.
  - `Zeroed-Out Update Propagation`: If a query fails for Aerodrome (like `getReserves`), the inner catch logs the error, but the code still proceeds to push an update message to the queue with default zeroed-out reserves and price (`price=0.0`), corrupting downstream consumers.
  - `Thundering Herd in Retries`: `_call_with_retry` implements exponential backoff retries but lacks randomized jitter (unlike the unused `retry_rpc` helper), which can hammer the RPC node.
  - `Defeated Exponential Backoff on low poll interval`: Default `base_delay` scales down to `0.0001s` when `poll_interval < 0.2`, causing instant exhaustion of 5 attempts.
- **Unexplored areas**: None. The investigation is complete.

## Key Decisions Made
- Confirmed the bugs by running pytest with logging output (`pytest --log-cli-level=INFO`).
- Formulated a robust fix strategy: Restructure the loop to scope block C inside the `try` block, add jitter to `_call_with_retry`, and keep default `base_delay` at a sane constant.

## Artifact Index
- /Users/nazmi/Crypcodile/.agents/explorer_m2_3/analysis.md — Main Analysis Report
- /Users/nazmi/Crypcodile/.agents/explorer_m2_3/handoff.md — Handoff Report
