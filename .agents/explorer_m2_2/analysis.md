# Milestone 2 Investigation: Log Pagination & Backoff Retries

This report analyzes the implementation correctness, completeness, and robustness of the block-range pagination and exponential backoff retry logic in `src/crypcodile/exchanges/base_onchain/connector.py`, along with its test coverage in `tests/exchanges/base_onchain/`.

---

## Executive Summary

1. **Block-Range Pagination (500 block chunks)**: Correctly partitions block ranges without gaps or overlaps. However, it lacks dynamic chunk resizing (fails if the RPC rejects a 500-block range) and does not implement a safety block margin to prevent ingestion of unfinalized blocks subject to chain reorganizations (reorgs).
2. **Exponential Backoff Retries**: Implemented comprehensively for all RPC calls. However, there are two redundant implementations: `retry_rpc` (module-level, has randomized jitter) and `_call_with_retry` (instance method, lacks jitter). Lacking jitter makes the system prone to synchronization/thundering herd issues during network recovery. Parameters like `max_attempts` and `max_delay` are also hardcoded.
3. **Critical Bug (UnboundLocalError & Stale Data Pollution)**: The inner loop in `_poll_loop` catches pool-specific exceptions but lacks a `continue` statement. As a result, when a query fails for a pool, the connector still attempts to push an update message. This results in either:
   - **`UnboundLocalError`**: If variables like `slot0` are not defined, causing the entire polling iteration for all pools to crash and skip remaining symbols.
   - **Stale Data Pollution**: If those variables were defined in a previous loop iteration, their stale data is reused, contaminating the failed pool's update message with incorrect tick and liquidity data.
4. **Test Coverage**: High unit and E2E coverage (74/74 E2E tests and 47/47 base unit tests pass). However, the tests verify the buggy behavior (i.e. verifying that subsequent pools are not processed when one fails) rather than enforcing robust error isolation and continuation.

---

## 1. Block-Range Pagination Logic (500 block chunks)

### Mechanism
The log retrieval is performed in chunks of 500 blocks using a `for` loop with a step size:
```python
572:                                 chunk_size = 500
573:                                 for from_b in range(start_block, end_block + 1, chunk_size):
574:                                     to_b = min(from_b + chunk_size - 1, end_block)
575:                                     chunk_logs = await self._call_with_retry(
576:                                         w3.eth.get_logs,
577:                                         {
578:                                             "address": addr,
579:                                             "fromBlock": from_b,
580:                                             "toBlock": to_b,
581:                                             "topics": [swap_topic]
582:                                         }
583:                                     )
584:                                     logs.extend(chunk_logs)
```

### Assessment
* **Correctness**: The partition math `range(start_block, end_block + 1, chunk_size)` and `min(from_b + chunk_size - 1, end_block)` is correct. It covers the full range `[start_block, end_block]` without gaps or overlapping queries.
* **Initialization**: The last block is initialized as `self._last_blocks[sym] = current_block - 20` to prevent querying from block `0` on startup.
* **Robustness Issues / Gaps**:
  * **No Chain Reorg Protection**: The loop polls up to `current_block` (the latest block number returned by the RPC). On Base mainnet, unfinalized blocks can undergo reorganizations (reorgs). Querying logs up to `current_block` directly makes the connector vulnerable to processing logs that might be reverted, leading to database discrepancies. A safety margin (e.g., polling up to `current_block - 5`) is recommended.
  * **Static Chunk Sizing**: If an RPC provider returns an error (e.g., query size/range too large or timeout), the transport does not dynamically split the chunk size (e.g., from 500 to 250). It will retry the 500-block query 5 times and fail, stalling block polling.
  * **Sequential execution**: Log pagination is executed sequentially per pool. In case of a large backlog, this blocks processing of subsequent pools in the iteration.

---

## 2. Exponential Backoff Retry Logic

### Mechanism
The transport implements retries through `_call_with_retry`:
```python
261:     async def _call_with_retry(self, func: Any, *args: Any, **kwargs: Any) -> Any:
...
280:                 if attempt >= max_attempts:
281:                     log.error(f"RPC call failed after {attempt} attempts: {e}")
282:                     raise
283:                 delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
...
288:                 await asyncio.sleep(delay)
```

### Assessment
* **Completeness**: All outbound RPC queries (`block_number`, `get_block`, `getPool`, `slot0`, `liquidity`, `tickSpacing`, `getReserves`, and `get_logs`) are correctly wrapped in `_call_with_retry`.
* **Redundancy**: There are two parallel implementations of retry logic in the same file:
  1. `retry_rpc` (lines 211-239): A standalone module-level function. It implements exponential backoff **with randomized jitter** (`delay * (0.5 + random.random() * 0.5)`).
  2. `_call_with_retry` (lines 261-289): An instance method on `BaseOnchainTransport`. It implements exponential backoff **without jitter**.
* **Robustness Issues / Gaps**:
  * **No Randomized Jitter**: The lack of jitter in `_call_with_retry` means that multiple concurrent clients (or multiple pool polling tasks) experiencing RPC errors (e.g., rate limit 429s) will retry at the exact same intervals, causing a thundering herd problem and exacerbating the rate limits.
  * **Hardcoded Limits**: `max_attempts` (5) and `max_delay` (10.0s) are hardcoded inside `_call_with_retry` and cannot be customized via parameters.

---

## 3. The Critical Bug: UnboundLocalError & Stale Data Pollution

### Observation
In `connector.py`, the inner loop processes each pool sequentially inside a `try-except` block:
```python
482:                     for sym, pool in resolved_pools.items():
...
496:                         try:
497:                             # A. Query current price and reserves/liquidity
...
675:                             self._last_blocks[sym] = max(self._last_blocks[sym], current_block)
676:                         except Exception as e:
677:                             log.error(f"base_onchain: Error polling pool data for {sym}: {e}")
678:      
679:                         # C. Push state update to queue
680:                         state_payload = {
681:                             "price": price,
682:                             "reserve0": reserve0,
683:                             "reserve1": reserve1,
684:                             "is_flipped": is_flipped,
685:                             "decimals0": spec["decimals0"],
686:                             "decimals1": spec["decimals1"],
687:                         }
688:                         if spec["type"] == "uniswap_v3":
689:                             state_payload["tick"] = int(slot0[1])
690:                             state_payload["liquidity"] = int(liquidity)
691:                             state_payload["tickSpacing"] = int(tick_spacing)
692:                             state_payload["tick_spacing"] = int(tick_spacing)
```

The `except` block catches the exception and logs it, but it does **not** stop the execution of the rest of the pool's updates (there is no `continue` statement at line 678).

### Consequences
1. **UnboundLocalError**: If it is the first pool in the iteration, or if no Uniswap V3 pool has succeeded yet, variables like `slot0`, `liquidity`, and `tick_spacing` are not assigned. The code at line 689 will attempt to reference `slot0`, raising an `UnboundLocalError`. This error escapes to the outer loop's `except` block (line 705), crashing the entire polling iteration and skipping all subsequent pools.
2. **Stale Data Pollution**: If a Uniswap V3 pool (e.g., `cbBTC-USDC`) succeeds, it defines `slot0`, `liquidity`, and `tick_spacing` in the local scope. If the next Uniswap V3 pool (e.g., `DEGEN-WETH`) fails during its query, the exception is caught, and execution continues to line 680. Because `slot0`, `liquidity`, and `tick_spacing` are still in scope from the previous loop iteration, their stale values (from `cbBTC-USDC`) are reused. The update message for `DEGEN-WETH` is then built and sent containing `cbBTC-USDC`'s tick, liquidity, and tick spacing, resulting in data corruption.

---

## 4. Test Coverage Analysis

### Unit Tests (`tests/exchanges/base_onchain/test_connector.py`)
* `test_log_pagination_chunking`: Correctly verifies block range chunking (from block 1000 to 2100 in 3 queries: `1001-1500`, `1501-2000`, and `2001-2100`).
* `test_rpc_retries_and_call_with_retry`: Verifies that `_call_with_retry` retries on failure and eventually raises an exception after 5 attempts.

### Gaps in Tests
* **Incorrect Assertions in `test_empirical_bugs.py`**:
  `test_slot0_unbound_local_error` asserts:
  ```python
  assert not any("WELL-WETH" in r for r in results), "WELL-WETH was processed despite slot0 failure!"
  ```
  This test expects `WELL-WETH` to be skipped because the loop crashes. While this asserts the code behaves as written, this behavior is a **bug**. A robust design would ensure that a failure in one pool (e.g., `cbBTC-USDC`) is isolated, allows `WELL-WETH` to be successfully processed, and pushes the `WELL-WETH` update message to the queue.

---

## 5. Recommended Fix Strategy

### Fix 1: Add Inner Loop `continue` and Variable Reset
To prevent the propagation of failures and stale data pollution, reset Uniswap-specific variables and immediately `continue` to the next symbol in the inner loop `except` block:

```python
# Before the try block:
slot0 = None
liquidity = None
tick_spacing = None

try:
    ...
except Exception as e:
    log.error(f"base_onchain: Error polling pool data for {sym}: {e}")
    continue  # Skip pushing update payload for failed queries
```

### Fix 2: Unify Retry Implementations and Add Jitter
To fix the lack of jitter in `_call_with_retry`, refactor `BaseOnchainTransport` to use `retry_rpc` directly, or add randomized jitter to `_call_with_retry`:

```python
# In BaseOnchainTransport._call_with_retry:
delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
delay = delay * (0.5 + random.random() * 0.5)  # Add jitter
```

### Fix 3: Add Safety Block Margin
To protect against block reorganizations, introduce a safety margin of e.g. 5 blocks:

```python
# Set a block margin config (can be parameter-driven)
block_margin = 5
end_block = max(start_block, current_block - block_margin)
```
