# Milestone 2 Implementation Review Report

## Review Summary

**Verdict**: APPROVE

The Milestone 2 implementation correctly resolves all targeted issues:
- **UnboundLocalError**: Step C is now enclosed inside the inner `try` block of the pool polling loop. If queries fail, the loop catches the exception at the pool level and continues to the next pool without raising `UnboundLocalError`.
- **Zeroed-out updates**: If queries fail, Step C is not executed, preventing zeroed-out state payloads from being queued.
- **Negative block cursor**: Cursor initialization uses `max(0, current_block - 20)` to ensure it never evaluates to a negative index on local testnets.
- **Backoff jitter**: Exponential backoff delays in `_call_with_retry` are randomized using a `random.uniform(0.5, 1.0)` jitter scaling factor (50% to 100% delay), successfully preventing synchronization under rate limits.
- **Dead code removal**: The unused global `retry_rpc` function has been completely removed.
- **Log query failure resilience**: Logs query is wrapped in its own `try-except` block, allowing state updates to be queued even when log queries fail, while preserving the block cursor boundary for subsequent retries.

All 729 unit, integration, and E2E tests pass cleanly. No integrity violations or regressions were detected.

---

## Quality Review Report

### Findings

#### [Minor] Finding 1: Lack of Timeout in `_call_with_retry`
- **What**: The `_call_with_retry` wrapper does not impose a timeout on the awaited RPC call.
- **Where**: `src/crypcodile/exchanges/base_onchain/connector.py`, lines 234–263
- **Why**: If the RPC node hangs indefinitely on a request, the entire polling loop task will hang, preventing updates for all pools.
- **Suggestion**: Wrap the awaited call/coroutine with `asyncio.wait_for` (e.g., with a 10s timeout) to let it fail and trigger the retry mechanism.

---

### Verified Claims

- **UnboundLocalError resolved** → Verified via unit tests (`test_transport_resilience_to_rpc_errors`) and log query failure tests → **PASS**
- **Zeroed-out updates prevented** → Verified by ensuring Step C is bypassed on query failure → **PASS**
- **Negative block cursor initialization fixed** → Verified by code audit and `max(0, ...)` boundary checks → **PASS**
- **Backoff jitter implemented** → Verified via `test_backoff_retry_jitter_limits` and `test_retry_thundering_herd_jitter_distribution` → **PASS**
- **Unused `retry_rpc` removed** → Verified that no occurrences exist in the codebase → **PASS**
- **100% Test Pass Rate** → Verified by running `uv run pytest` globally (729/729 passed) and locally (53/53 passed) → **PASS**

---

### Coverage Gaps

- **RPC Call Timeouts** — risk level: **Low** — recommendation: **Accept risk** (the public node RPC endpoints are generally stable, but wrapping with a timeout is recommended for production environments).

---

### Unverified Items

- None. All major claims and implementations have been verified.

---

## Adversarial Review Report

**Overall risk assessment**: LOW

### Challenges

#### [Low] Challenge 1: Restricted Jitter vs. Full Jitter
- **Assumption challenged**: The jitter factor of `[0.5, 1.0]` is sufficient to break retry synchronization.
- **Attack scenario**: In high-concurrency thundering herd scenarios (e.g., 50+ instances starting simultaneously and hit with rate limits), the delay range is restricted to `[0.5 * delay, 1.0 * delay]`. This creates a tighter cluster of retries than a full jitter range of `[0.0, 1.0]`.
- **Blast radius**: Increased peak load on the RPC node when rate limits are cleared, potentially causing secondary rate limit triggers.
- **Mitigation**: Use full jitter `random.uniform(0, 1.0) * delay` or randomized exponential backoff (Decorrelated Jitter).

---

### Stress Test Results

- **Reorg / Block Lag scenario** → Cursor maintains its position and does not roll back, preventing duplicate log queries when block height recovers → **PASS**
- **Extremely large block range pagination** → Transport successfully chunks log fetching in units of 500 blocks → **PASS**
- **Underflow/Overflow price values** → Normalizer caps small sizes at `0.0001` and handles extreme prices gracefully without crashing → **PASS**

---

### Unchallenged Areas

- **FastAPI / REST API gateway performance under high load** — reason not challenged: Out of scope for this base onchain connector review.
