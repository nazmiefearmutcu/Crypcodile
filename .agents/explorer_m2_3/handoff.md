# Handoff Report: Milestone 2 — Log Pagination & Backoff Retries

This handoff report summarizes the investigation of Milestone 2 (Log pagination & backoff retries) in `src/crypcodile/exchanges/base_onchain/connector.py` and related tests.

---

## 1. Observation
- **File Paths and Lines**:
  - `src/crypcodile/exchanges/base_onchain/connector.py` containing polling logic and retry logic.
  - `tests/exchanges/base_onchain/test_challenger_stress_4.py` containing regression tests for unbound variables.
- **Uncaught `UnboundLocalError`**:
  - In `connector.py` line 677, the inner `try-except` catch block ends, logging errors at the individual pool level:
    ```python
    except Exception as e:
        log.error(f"base_onchain: Error polling pool data for {sym}: {e}")
    ```
  - In line 689, the code references `slot0` (inside block C) outside of the try-except scope:
    ```python
    if spec["type"] == "uniswap_v3":
        state_payload["tick"] = int(slot0[1])
    ```
  - Running `.venv/bin/pytest --log-cli-level=INFO tests/exchanges/base_onchain/test_challenger_stress_4.py` outputs the following logs:
    ```
    ERROR    crypcodile.exchanges.base_onchain.connector:connector.py:281 RPC call failed after 5 attempts: Slot0 query failed
    ERROR    crypcodile.exchanges.base_onchain.connector:connector.py:677 base_onchain: Error polling pool data for cbBTC-USDC: Slot0 query failed
    ERROR    crypcodile.exchanges.base_onchain.connector:connector.py:706 base_onchain: Error polling pool data: cannot access local variable 'slot0' where it is not associated with a value
    ```
- **Zeroed-Out Update Propagation**:
  - The `price`, `reserve0`, and `reserve1` variables are default-initialized to `0.0` at the beginning of the pool processing loop (lines 488–490).
  - For Aerodrome V2, if `getReserves()` query fails (raising an exception caught on line 677), the loop proceeds to construct `state_payload` using `price` (0.0), `reserve0` (0.0), and `reserve1` (0.0), and puts it onto `self._queue` (line 703):
    ```python
    await self._queue.put(json.dumps(update_msg).encode())
    ```
- **Lack of Retry Jitter**:
  - The `_call_with_retry` method (lines 261–289) implements exponential delay, but does not multiply it by a random factor:
    ```python
    delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
    ```
- **Defeated Delay in Tests/Low Intervals**:
  - In `_call_with_retry` line 265, `base_delay` scales down to `0.0001` if `poll_interval < 0.2`:
    ```python
    base_delay = kwargs.pop("base_delay", 0.0001 if self.poll_interval < 0.2 else 1.0)
    ```

---

## 2. Logic Chain
1. **Observation 1 (Uncaught `UnboundLocalError`)** shows that when `slot0` or `liquidity` query fails for a Uniswap V3 pool, execution is diverted to the inner `except Exception as e:` block on line 677.
2. Once that inner exception handler is complete, execution proceeds to Block C (line 680 onwards) because it resides outside the inner `try-except`.
3. Because `slot0` or `liquidity` was never defined, accessing them on line 689 / 690 raises an `UnboundLocalError`.
4. This exception is caught by the outer loop handler at line 705, which logs `cannot access local variable 'slot0' where it is not associated with a value` and aborts the remaining pools in `resolved_pools` for that cycle.
5. Therefore, a query failure on one Uniswap V3 pool crashes the entire polling cycle, causing a gap in processing other pools.
6. **Observation 2 (Zeroed-Out Update Propagation)** shows that for Aerodrome V2 pools, a query failure is caught by the inner handler, but because it does not reference any undefined variables in Block C, it executes without error and pushes a state payload to `self._queue` with `price = 0.0` and reserves `0.0`.
7. This propagates corrupted data downstream, which will skew synthetic orderbook depth calculations and trading triggers.
8. **Observation 3 (Lack of Retry Jitter)** and **Observation 4 (Defeated Delay)** show that retries lack jitter to resolve the thundering herd problem, and are performed sub-millisecond if `poll_interval < 0.2`, exhausting the retries instantly during minor outages.

---

## 3. Caveats
- No caveats. The codebase runs deterministically in the local virtual environment and the code structure and behavior are verified.

---

## 4. Conclusion
The current implementation of Milestone 2 (Log pagination & backoff retries) has critical robustness defects:
1. It crashes polling cycles with `UnboundLocalError` when Uniswap V3 queries fail, preventing subsequent pools from being processed.
2. It pushes incorrect zeroed-out state data (`price: 0.0`) onto the transport queue when Aerodrome V2 queries fail.
3. The retry mechanism is vulnerable to thundering herd (no jitter) and collapses backoff time on fast-poll settings.

**Fix Recommendation**: Restructure the pool polling loop to encompass Block C inside the inner `try` block. This guarantees updates are only queued on success, avoiding unbound variables and zeroed-out propagation. Clean up `_call_with_retry` by default-initializing `base_delay` to a static 1.0s and introducing jitter.

---

## 5. Verification Method
- **Command**:
  Run pytest on `test_challenger_stress_4.py` with logging output enabled:
  ```bash
  .venv/bin/pytest --log-cli-level=INFO tests/exchanges/base_onchain/test_challenger_stress_4.py
  ```
- **Inspect**:
  Look at the terminal output to confirm that `cannot access local variable 'slot0' where it is not associated with a value` is logged at the end of the second test.
- **Invalidation Condition**:
  If a modified loop structure is implemented and the same test command runs without any `UnboundLocalError` or zero-price payload logs, the issue is resolved.
