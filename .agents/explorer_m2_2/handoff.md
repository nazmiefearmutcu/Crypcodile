# Handoff Report: Milestone 2 (Log pagination & backoff retries)

## 1. Observation
We observed the following in `/Users/nazmi/Crypcodile/src/crypcodile/exchanges/base_onchain/connector.py`:
- In `BaseOnchainTransport._poll_loop`, log chunking uses a step size of 500 blocks:
  ```python
  572:                                 chunk_size = 500
  573:                                 for from_b in range(start_block, end_block + 1, chunk_size):
  574:                                     to_b = min(from_b + chunk_size - 1, end_block)
  ```
- The inner loop iterates over resolved pools sequentially and contains a `try-except` block covering lines 496 to 675. However, there is no `continue` statement inside the `except` block:
  ```python
  676:                         except Exception as e:
  677:                             log.error(f"base_onchain: Error polling pool data for {sym}: {e}")
  678:      
  679:                         # C. Push state update to queue
  680:                         state_payload = {
  ...
  688:                         if spec["type"] == "uniswap_v3":
  689:                             state_payload["tick"] = int(slot0[1])
  ```
- In `BaseOnchainTransport._call_with_retry`, the backoff delay lacks a randomized jitter component:
  ```python
  283:                 delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
  284:                 log.warning(...)
  285:                 ...
  288:                 await asyncio.sleep(delay)
  ```
- Module-level `retry_rpc` function (lines 211-238) implements jitter (`delay = delay * (0.5 + random.random() * 0.5)`) but is never called within `connector.py` (only in `tests/e2e/test_tier2_boundaries.py`).

We also ran pytest and verified that all tests passed:
- `pytest tests/exchanges/base_onchain/` passed 47 tests.
- `pytest tests/e2e/` passed 74 tests.
- However, `tests/exchanges/base_onchain/test_empirical_bugs.py` contains:
  ```python
  99:             assert not any("WELL-WETH" in r for r in results), "WELL-WETH was processed despite slot0 failure!"
  ```
  which asserts that other pools are NOT processed when one fails (a consequence of `UnboundLocalError`).

## 2. Logic Chain
1. **UnboundLocalError**: Since lines 680-703 are outside the inner `try-except` block (lines 496-675) and there is no `continue` in the `except` block, execution continues to lines 680-703 after a query failure. When variables like `slot0` are not defined, they raise `UnboundLocalError`.
2. **Stale Data Pollution**: If `slot0` was defined in a previous loop iteration, it is reused for the failed pool, polluting the failed pool's update message with incorrect tick and liquidity data.
3. **Thundering Herd Problem**: Since `_call_with_retry` lacks jitter, multiple clients retrying at the same time will hit rate limits simultaneously.
4. **No Chain Reorg Protection**: Log polling queries up to `current_block` without safety margin, risking processing unfinalized logs that might be reorged.

## 3. Caveats
- We assumed the default block margin safety logic should be ~5 blocks, but this may depend on the finality time of the RPC node and the Base network.
- The default chunk size (500 blocks) is assumed to be accepted by the RPC. We did not investigate dynamic range resizing on RPC range errors since it's not currently implemented.

## 4. Conclusion
The Milestone 2 implementation contains a critical logic bug (missing `continue` in inner loop except block leading to `UnboundLocalError` or stale data pollution) and minor robustness gaps (redundant retry function, lack of jitter, lack of reorg safety block margin). Fixing this is necessary to ensure robust, independent pool polling and accurate data delivery.

## 5. Verification Method
- Execute the test suite using `.venv/bin/pytest tests/exchanges/base_onchain/test_empirical_bugs.py`.
- To verify the fix, once `continue` is added to line 678, the test assertion `assert not any("WELL-WETH" in r for r in results)` should be rewritten to assert that `WELL-WETH` IS successfully processed and present in the results.
