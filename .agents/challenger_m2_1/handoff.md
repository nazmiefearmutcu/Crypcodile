# Adversarial Challenge & Handoff Report — Milestone 2

## 1. Observation

- **Implementation Location**: `src/crypcodile/exchanges/base_onchain/connector.py`
  - Retry & Backoff Jitter implementation in `BaseOnchainTransport._call_with_retry` (lines 234-263):
    ```python
    234:     async def _call_with_retry(self, func: Any, *args: Any, **kwargs: Any) -> Any:
    ...
    251:             except Exception as e:
    252:                 attempt += 1
    253:                 if attempt >= max_attempts:
    254:                     log.error(f"RPC call failed after {attempt} attempts: {e}")
    255:                     raise
    256:                 delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
    257:                 delay = delay * random.uniform(0.5, 1.0)
    ...
    262:                 await asyncio.sleep(delay)
    ```
  - Pagination Chunking implementation in `BaseOnchainTransport._poll_loop` (lines 541-563):
    ```python
    541:                             start_block = self._last_blocks[sym] + 1
    542:                             end_block = current_block
    ...
    546:                             if start_block <= end_block:
    547:                                 chunk_size = 500
    548:                                 try:
    549:                                     for from_b in range(start_block, end_block + 1, chunk_size):
    550:                                         to_b = min(from_b + chunk_size - 1, end_block)
    551:                                         chunk_logs = await self._call_with_retry(
    ...
    ```
- **Test Locations**: 
  - Existing: `tests/exchanges/base_onchain/test_connector.py` and E2E tests `tests/e2e/test_tier2_boundaries.py`.
  - Added Adversarial Stress Tests: `tests/exchanges/base_onchain/test_adversarial.py`
- **Execution Results**:
  - Run command: `uv run pytest`
  - Output: `729 passed, 36 warnings in 37.49s`
  - Verified 5 custom adversarial and stress cases in `test_adversarial.py` passed successfully.

---

## 2. Challenge & Logic Chain

We stress-tested the implementation's resilience and mathematical guarantees across key dimensions.

### Challenge 1: Pagination Under Extremely Large Block Ranges
- **Assumption Challenged**: The chunking pagination logic works under massive block ranges (e.g. >100,000 blocks) without timing out or missing chunks.
- **Attack Scenario / Test**: Mocked a range of 100,000 blocks (`start_block=1000` to `end_block=101000`). With `chunk_size=500`, this requires exactly 200 chunked RPC query calls.
- **Observation / Result**: `test_pagination_extremely_large_range` ran successfully. Exactly 200 chunk calls were made, verifying the loop bounds and pagination termination.
  - First chunk: `fromBlock: 1001`, `toBlock: 1500`.
  - Last chunk: `fromBlock: 100501`, `toBlock: 101000`.
- **Mitigation / Defense**: Robust chunk bounds logic (`min(from_b + chunk_size - 1, end_block)`) guarantees correctness.

### Challenge 2: Pagination Under Empty Ranges
- **Assumption Challenged**: When `start_block > end_block` (e.g. current block reverts or stays constant), no unnecessary RPC queries are dispatched, and the connector doesn't crash.
- **Attack Scenario / Test**: Seeded `_last_blocks` to `1000` and current block to `1000`. This maps to an empty range (`start_block=1001` > `end_block=1000`).
- **Observation / Result**: `test_pagination_empty_range` ran successfully, registering 0 calls to `get_logs`.
- **Mitigation / Defense**: Covered by explicit conditional gate `if start_block <= end_block:`.

### Challenge 3: Retry Backoff Jitter Range Limits & Capping
- **Assumption Challenged**: Exponential backoff scales accurately according to base delay, stays strictly within jitter bounds, and caps at `max_delay = 10.0`.
- **Attack Scenario / Test**: Mocked RPC calls that consistently fail. Measured the sleep delays over 5 attempts.
- **Observation / Result**: `test_backoff_retry_jitter_limits` verified:
  - Exponential scaling: attempt 1: `base * 1`, attempt 2: `base * 2`, attempt 3: `base * 4`, attempt 4: `base * 8`.
  - Jitter: delay is scaled by `[0.5, 1.0]`. All delay measurements fell strictly within their jittered ranges.
  - Capping: Tested with `base_delay = 20.0`. Measured delays fell strictly in the `[5.0, 10.0]` range, validating the 10.0s maximum limit.

### Challenge 4: Thundering Herd Desynchronization
- **Assumption Challenged**: Under thundering herd situations (multiple concurrent connectors/workers retrying due to shared RPC limits), their retry timings spread out due to the random jitter factor.
- **Attack Scenario / Test**: Spatially concurrent execution of 20 tasks calling a failing RPC method. Captured the sleep timelines for all tasks.
- **Observation / Result**: `test_retry_thundering_herd_jitter_distribution` confirmed that no two tasks slept for identical durations. The random scaling factor `random.uniform(0.5, 1.0)` successfully desynchronized the retries.

---

## 3. Caveats

- **Network Constraints**: All tests were run using mocked/simulated transports. Absolute live-network anomalies (e.g., TCP socket resets, OS-level file descriptor exhaustion) were not investigated.
- **Jitter distribution**: We confirmed that delays desynchronize, but did not perform a rigorous statistical analysis (like Kolmogorov-Smirnov test) on the distribution of sleep delays.

---

## 4. Conclusion

The Milestone 2 implementation of block-range pagination and backoff retry logic is mathematically correct, handles boundary conditions correctly, and mitigates thundering herd scenarios. The code is highly robust and meets all requirements.

---

## 5. Verification Method

To independently verify the test executions and run the entire suite:

```bash
# Verify the entire test suite including the new adversarial suite
uv run pytest
```

Specific files to inspect:
- Custom adversarial/stress tests: `tests/exchanges/base_onchain/test_adversarial.py`
- Target implementation: `src/crypcodile/exchanges/base_onchain/connector.py`
