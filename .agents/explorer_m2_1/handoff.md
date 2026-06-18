# Handoff Report: Milestone 2 Audit

This handoff report summarizes the findings of the Milestone 2 investigation of the Base on-chain connector's log pagination and exponential backoff retry logic.

---

## 1. Observation

Direct observations made in the workspace:

### A. Uniswap V3 `UnboundLocalError`
In `src/crypcodile/exchanges/base_onchain/connector.py` (lines 679-693), Step C executes outside the `try-except` block (which ends at line 677). When a query inside the try block fails, the local variable `slot0` (assigned at line 499) or `liquidity` (assigned at line 502) is not bound:
```python
688:                         if spec["type"] == "uniswap_v3":
689:                             state_payload["tick"] = int(slot0[1])
690:                             state_payload["liquidity"] = int(liquidity)
```
When running tests, pytest outputs the following error log (visible using `--log-cli-level=INFO`):
```
ERROR    crypcodile.exchanges.base_onchain.connector:connector.py:706 base_onchain: Error polling pool data: cannot access local variable 'slot0' where it is not associated with a value
```

### B. Aerodrome V2 Corrupted Payload Emission
In `connector.py` (lines 488–490), variables are initialized to `0.0`:
```python
488:                         price = 0.0
489:                         reserve0 = 0.0
490:                         reserve1 = 0.0
```
If Aerodrome V2 query `contract.functions.getReserves().call` fails inside the `try` block, it is caught at line 676. Since it is Aerodrome V2, it bypasses the Uniswap V3 `UnboundLocalError` check and proceeds to queue the payload at lines 700-703:
```python
700:                             "state": state_payload,
701:                             "swaps": swaps
702:                         }
703:                         await self._queue.put(json.dumps(update_msg).encode())
```
This is verified by running `test_unbound_local_error_regression_aerodrome` in `tests/exchanges/base_onchain/test_challenger_stress_4.py`, which successfully completes the loop iteration but logs a query failure.

### C. Startup Block Cursor Initialization
In `connector.py` (line 494):
```python
493:                         if sym not in self._last_blocks:
494:                             self._last_blocks[sym] = current_block - 20
```
If `current_block < 20`, `self._last_blocks[sym]` becomes negative.

### D. Exponential Backoff Retry Delay
In `connector.py` (line 283):
```python
283:                 delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
```
No random jitter calculation is applied to `delay` before sleeping.

---

## 2. Logic Chain

1. **Bug A (Uniswap V3 Loop Abortion)**:
   * *Observation*: Line 499 reads `slot0 = await self._call_with_retry(...)` inside the `try` block. Step C at line 689 reads `int(slot0[1])` outside the `try` block.
   * *Reasoning*: If `_call_with_retry` raises an exception for `slot0()`, line 499 is skipped. The exception is caught at line 677. Execution then proceeds to line 689. Because `slot0` was never defined, an `UnboundLocalError` is raised. Since line 689 is outside the inner `try-except` block, the exception propagates to the outer `try-except` at line 705, which aborts the polling loop iteration.
   * *Conclusion*: Uniswap V3 query failure aborts the entire iteration, skipping any subsequent symbols.

2. **Bug B (Aerodrome V2 Corrupted Payload)**:
   * *Observation*: Line 488-490 initializes `price`, `reserve0`, and `reserve1` to `0.0`. Line 550 gets Aerodrome reserves inside the `try` block. Step C constructs `state_payload` at lines 680-687 and puts it in the queue at line 703.
   * *Reasoning*: If `getReserves` fails, the exception is caught at line 677. Execution proceeds to Step C. Because it is not Uniswap V3, it skips lines 688–692, avoiding the `UnboundLocalError`. It proceeds to line 703 and queues a message containing `price=0.0`, `reserve0=0.0`, and `reserve1=0.0`.
   * *Conclusion*: Aerodrome V2 query failure results in queueing corrupted zero-price, zero-reserves updates.

3. **Bug C (Negative Cursor Crash)**:
   * *Observation*: Line 494 sets `_last_blocks[sym] = current_block - 20`.
   * *Reasoning*: On local test environments where `current_block` is less than 20, the expression yields a negative value. A negative block number passed as `fromBlock` in `w3.eth.get_logs` throws an RPC validation error.
   * *Conclusion*: The connector loop crashes on startup on low-height/fresh blockchains.

4. **Gap D (Missing Jitter)**:
   * *Observation*: `_call_with_retry` calculations use simple exponential delay $2^{attempt-1}$ with no random scaling factor.
   * *Reasoning*: Concurrent requests hitting a rate limit on the same public RPC will retry in exact sync, repeating rate limits indefinitely (thundering herd).
   * *Conclusion*: Lack of jitter compromises the robustness of the backoff retry logic.

---

## 3. Caveats

* We assumed that public node RPCs are the primary backend. If a dedicated private RPC is used, the lack of jitter is less severe, but it is still highly recommended.
* We have not investigated the memory footprint of the memory sink during long runs, though test files indicate the presence of cache-clearing mechanisms for blocks.

---

## 4. Conclusion

The Milestone 2 implementation:
1. **Block-range pagination**: Correct math (500 blocks chunking), but vulnerable to negative cursors on testnets (`current_block < 20`).
2. **Exponential backoff**: Implemented correctly but lacks random jitter, and the API is vulnerable to coroutine reuse crashes.
3. **Robustness**: Severely flawed due to `UnboundLocalError` (Uniswap V3) and corrupted payload emission (Aerodrome V2) on RPC query failures.

Actionable fixes have been provided in `/Users/nazmi/Crypcodile/.agents/explorer_m2_1/analysis.md` and handed off to the Implementer agent.

---

## 5. Verification Method

To verify these observations and the logic chain:
1. Run pytest with logs visible:
   `uv run pytest tests/exchanges/base_onchain/test_challenger_stress_4.py -vv -s --log-cli-level=INFO`
   Observe the logged `cannot access local variable 'slot0'` error message.
2. Run empirical bugs tests:
   `uv run pytest tests/exchanges/base_onchain/test_empirical_bugs.py -vv -s --log-cli-level=INFO`
   Observe that `WELL-WETH` updates were not processed due to `cbBTC-USDC` slot0 query failure.
