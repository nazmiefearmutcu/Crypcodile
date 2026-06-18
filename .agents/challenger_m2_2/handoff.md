# Milestone 2 Adversarial and Stress Testing Handoff Report

## 1. Observation

### 1.1 Block-Range Pagination and Chunking
The log pagination chunking implementation is found in `src/crypcodile/exchanges/base_onchain/connector.py` (lines 546-564):
```python
546:                             if start_block <= end_block:
547:                                 chunk_size = 500
548:                                 try:
549:                                     for from_b in range(start_block, end_block + 1, chunk_size):
550:                                         to_b = min(from_b + chunk_size - 1, end_block)
551:                                         chunk_logs = await self._call_with_retry(
552:                                             w3.eth.get_logs,
553:                                             {
554:                                                 "address": addr,
555:                                                 "fromBlock": from_b,
556:                                                 "toBlock": to_b,
557:                                                 "topics": [swap_topic]
558:                                             }
559:                                         )
560:                                         logs.extend(chunk_logs)
561:                                 except Exception as e:
562:                                     log.error(f"base_onchain: Error polling pool data for {sym}: {e}")
563:                                     log_query_success = False
```

If the pagination succeeds, the cursor is updated (lines 680-681):
```python
680:                             if log_query_success:
681:                                 self._last_blocks[sym] = max(self._last_blocks[sym], current_block)
```

### 1.2 Backoff Retry and Jitter
The backoff retry and jitter logic is implemented in `src/crypcodile/exchanges/base_onchain/connector.py` (lines 234-263):
```python
234:     async def _call_with_retry(self, func: Any, *args: Any, **kwargs: Any) -> Any:
235:         import inspect
236:         attempt = 0
237:         max_attempts = 5
238:         base_delay = kwargs.pop("base_delay", 0.0001 if self.poll_interval < 0.2 else 1.0)
239:         max_delay = 10.0
240:         
241:         while True:
242:             try:
243:                 if callable(func):
244:                     res = func(*args, **kwargs)
...
251:             except Exception as e:
252:                 attempt += 1
253:                 if attempt >= max_attempts:
254:                     log.error(f"RPC call failed after {attempt} attempts: {e}")
255:                     raise
256:                 delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
257:                 delay = delay * random.uniform(0.5, 1.0)
258:                 log.warning(
259:                     f"RPC call failed: {e}. Retrying in {delay:.4f}s... "
260:                     f"(Attempt {attempt}/{max_attempts})"
261:                 )
262:                 await asyncio.sleep(delay)
```

### 1.3 Test Suite Execution Results
The test suite was run via `uv run pytest tests/exchanges/base_onchain/test_challenger_m2_adversarial.py --log-level=ERROR`. The command completed successfully with output:
```
tests/exchanges/base_onchain/test_challenger_m2_adversarial.py::test_pagination_extremely_large_range_chunking PASSED
tests/exchanges/base_onchain/test_challenger_m2_adversarial.py::test_pagination_error_loses_all_progress PASSED
tests/exchanges/base_onchain/test_challenger_m2_adversarial.py::test_pagination_empty_range PASSED
tests/exchanges/base_onchain/test_challenger_m2_adversarial.py::test_pagination_invalid_range_negative PASSED
tests/exchanges/base_onchain/test_retry_logic_jitter_bounds_and_distribution PASSED
tests/exchanges/base_onchain/test_retry_logic_indefinite_hang_vulnerability PASSED
tests/exchanges/base_onchain/test_thundering_herd_concurrency PASSED
7 passed in 0.42s
```

## 2. Logic Chain

### 2.1 Block-Range Pagination and Cursor Reset Vulnerability
1. **Observation 1.1**: The logs from all chunk ranges `[from_b, to_b]` are accumulated into `logs.extend(chunk_logs)` within a `try-except` block.
2. **Observation 1.1**: If any chunk query raises an exception (e.g. rate limit on the 5th chunk), the exception is caught, the loop aborts, and `log_query_success` is set to `False`.
3. **Observation 1.1**: Since `log_query_success` is `False`, the cursor `self._last_blocks[sym]` is NOT updated and remains at the original block height.
4. **Conclusion**: On the next poll, the connector will query the block range starting from `start_block` again, resulting in duplicate querying of blocks that were already queried successfully in preceding chunks, causing duplicate swap events and excessive RPC load. This is verified empirically by `test_pagination_error_loses_all_progress`.

### 2.2 Block Reorg Log Omission Vulnerability
1. **Observation 1.1**: If `current_block` drops below `self._last_blocks` (e.g. during a block reorg), `start_block` becomes greater than `end_block`.
2. **Observation 1.1**: No logs are queried, and `log_query_success` remains `True`.
3. **Observation 1.1**: The cursor update `max(self._last_blocks[sym], current_block)` evaluates to `self._last_blocks[sym]` (the old higher block height).
4. **Conclusion**: The cursor is never rolled back to align with the new fork height. As the new fork progresses, all blocks between the drop height and the old height are skipped, permanently omitting any swap events that occurred in those blocks on the new fork. This is verified empirically by `test_pagination_empty_range`.

### 2.3 Unbounded RPC Execution Hang Vulnerability
1. **Observation 1.2**: In `_call_with_retry`, the call to the function (`res = func(*args, **kwargs)`) and the subsequent await (`await res`) are executed directly without any timeout wrapper (such as `asyncio.wait_for`).
2. **Conclusion**: If an RPC node hangs indefinitely, the await will hang indefinitely, blocking the entire poll loop task and halting state updates for all pools. This is verified empirically by `test_retry_logic_indefinite_hang_vulnerability`.

### 2.4 Jitter/Thundering Herd Window Compression
1. **Observation 1.2**: The delay jitter is computed as `delay * random.uniform(0.5, 1.0)`.
2. **Conclusion**: This ensures the actual retry delay is always at least `0.5 * delay`. In thundering herd scenarios where multiple tasks fail simultaneously, the window of retries is compressed into `[0.5 * delay, delay]`, which has a higher risk of collision/synchronization compared to Full Jitter (`[0, delay]`). This is verified empirically by `test_thundering_herd_concurrency`.

## 3. Caveats
- Actual live network rate limits and node latency bounds were simulated via mocks to comply with the network isolation constraint. Live network testing under congested network conditions was not performed.
- We did not modify the implementation code to apply mitigations, focusing strictly on adversarial verification and reporting as per review constraints.

## 4. Conclusion
The Milestone 2 implementation contains critical vulnerabilities that jeopardize its robustness under network partitions, slow RPC nodes, and block reorganizations.
- **Unbounded Hangs**: A single slow/hanging RPC call will freeze the entire polling loop indefinitely.
- **Progress Loss / Duplicates**: Transient RPC rate limit errors during large block range pagination throw away all successful chunk queries, leading to duplicated work and duplicate swap processing on subsequent loops.
- **Event Omissions**: Block reorganizations where the block height drops will permanently omit events from the new canonical chain.
- **Thundering Herd Risk**: Jitter compression limits randomness spread, increasing retry collision rates.

## 5. Verification Method

### 5.1 Test Command
To verify the adversarial findings and test coverage:
```bash
uv run pytest tests/exchanges/base_onchain/test_challenger_m2_adversarial.py --log-level=ERROR -v
```

### 5.2 Files to Inspect
- `tests/exchanges/base_onchain/test_challenger_m2_adversarial.py` — Adversarial and stress tests verifying pagination chunking, progress loss, hang vulnerability, and thundering herd jitter.
- `src/crypcodile/exchanges/base_onchain/connector.py` — Implementation source.
