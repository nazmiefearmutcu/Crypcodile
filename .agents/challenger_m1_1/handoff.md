# Handoff Report: Milestone 1 Native AsyncWeb3 Refactoring Verification

**Verdict**: **FAIL**

---

## 1. Observation

Direct observations made during verification:

1. **UnboundLocalError Bug**:
   - Location: `src/crypcodile/exchanges/base_onchain/connector.py`
   - Code snippet (lines 420-433):
     ```python
     # C. Push state update to queue
     update_msg = {
         "type": "onchain_update",
         "block": current_block,
         "pool": sym,
         "pool_type": spec["type"],
         "timestamp": await self._get_block_timestamp(w3, current_block),
         "state": {
             "price": price,
             "reserve0": reserve0,
             "reserve1": reserve1,
         },
         "swaps": swaps
     }
     ```
   - Observed behavior in logs:
     ```
     ERROR    crypcodile.exchanges.base_onchain.connector:connector.py:439 base_onchain: Error polling pool data: cannot access local variable 'swaps' where it is not associated with a value
     ```
   - We verified this with a dedicated test `test_challenger_stress_4.py`, which consistently reproduces the `UnboundLocalError` when any pool query fails.

2. **Broken Unit Tests**:
   - Command: `uv run pytest tests/exchanges/base_onchain/`
   - Result: `3 failed, 34 passed, 1 warning`
   - Failing tests:
     - `test_get_onchain_price_uniswap_v3_success` (KeyError on `'symbol'` due to mock leakage raising `TypeError` inside `isinstance` in web3 library)
     - `test_get_onchain_price_aerodrome_success` (KeyError on `'symbol'`)
     - `test_mcp_server_serve_stdio` (TypeError: `argument should be a str or an os.PathLike object where __fspath__ returns a str, not 'type'`)

3. **Global Cursor Issue**:
   - In `connector.py` line 436:
     ```python
     if success:
         self._last_block = current_block
     ```
   - If a pool fails, `success` becomes `False`. Thus `self._last_block` does not advance. In the next iteration, the successful pool queries logs again from `self._last_block + 1` (the old block range), causing duplicate swap logs. This was verified in `test_challenger_stress_2.py::test_cursor_behavior_on_exceptions`.

4. **API Server Silent Failures**:
   - `src/crypcodile/api_server.py` line 102 and 111:
     ```python
     data = await get_onchain_price(symbol)
     ...
     return {
         "status": "success",
         ...
         "data": data
     }
     ```
   - When `get_onchain_price` fails, it returns `{"error": ...}`. The API server blindly returns this dictionary with HTTP 200 OK and status `"success"`.

---

## 2. Logic Chain

1. From **Observation 1**, when a pool query (like `slot0()` or `getReserves()`) fails, the execution jumps to the `except Exception` block before `swaps` is initialized on line 312.
2. The code then execution proceeds to step C where it references `swaps`.
3. Because `swaps` is not initialized, python raises `UnboundLocalError`.
4. This `UnboundLocalError` is uncaught inside the pool loop and propagates to the outer loop, aborting all remaining pool updates in that polling tick. Therefore, any transient RPC failure triggers an internal exception rather than a graceful failure.
5. From **Observation 2**, the existing tests in `test_servers.py` fail because:
   - They patch `web3.AsyncWeb3` directly instead of targeting the local reference `crypcodile.mcp_server.AsyncWeb3`, causing web3 internal code to receive `MagicMock` where it expects classes/types.
   - They pass `pytest.TempPathFactory` instead of `tmp_path` fixture value, leading to pathlib errors.
6. From **Observation 3**, the global block cursor cursor logic will duplicate updates for healthy pools in subsequent ticks if any other pool in the registry fails to query.
7. From **Observation 4**, the API server does not distinguish between success and failure payloads returned by the MCP helper, resulting in misleading HTTP success states.
8. Therefore, the refactored connector and server code are incorrect and fragile under connection drops.

---

## 3. Caveats

- We assumed that the RPC URL used in production may behave similarly to our simulated mocks regarding connection drops and timeouts.
- We did not verify the performance under infinite loops since unit tests were kept fast.

---

## 4. Conclusion

The native AsyncWeb3 refactoring for Milestone 1 contains multiple regressions:
1. `UnboundLocalError` crashes the polling loop's step C when queries fail.
2. A single pool query failure causes duplicate logs for all other successful pools.
3. The server tests are broken.
4. The API server returns success states on RPC errors.

---

## 5. Verification Method

To verify the failures independently, run:

```bash
# Execute the base onchain test suite (will show 3 test failures in test_servers.py)
uv run pytest tests/exchanges/base_onchain/

# Run the new stress/adversarial test suite to verify the UnboundLocalError explicitly
uv run pytest tests/exchanges/base_onchain/test_challenger_stress_4.py
```
