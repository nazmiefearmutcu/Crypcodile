# Handoff Report — Milestone 1 Verification

**Verdict**: **FAIL**

While the core functionality of the Native AsyncWeb3 refactoring works as expected and all 37 tests (including the 28 pre-existing ones and the 9 new unit/integration tests we implemented) pass successfully, the implementation fails stress and adversarial conditions due to two major vulnerabilities:
1. **High Risk Connection/Socket Leak**: A new `AsyncHTTPProvider` session is leaked on every single API/MCP request.
2. **Medium Risk Log Duplication**: RPC errors or partial node failures result in the re-fetching of already processed log events.

---

## 1. Observation

### Observation A: Unclosed Client Session Warnings under repeated calls
Running 100 sequential calls to `get_onchain_price` results in 100 `ResourceWarning: Unclosed client session` errors:
```
Unclosed client session
client_session: <aiohttp.client.ClientSession object at 0x10df55580>
/Users/nazmi/Crypcodile/.venv/lib/python3.12/site-packages/aiohttp/client.py:506: ResourceWarning: Unclosed client session <aiohttp.client.ClientSession object at 0x10df550a0>
  _warnings.warn(
```
File path: `src/crypcodile/mcp_server.py` line 84:
```python
        w3 = AsyncWeb3(AsyncHTTPProvider(rpc_url))
```
This is called inside `get_onchain_price` on every single request. There is no call to disconnect or close the underlying aiohttp session.

### Observation B: Single Global Cursor causing duplicates on partial node failure
File path: `src/crypcodile/exchanges/base_onchain/connector.py` line 435-436:
```python
                if success:
                    self._last_block = current_block
```
And lines 415-417:
```python
                    except Exception as e:
                        log.error(f"base_onchain: Error polling pool data for {sym}: {e}")
                        success = False
```
If query for any resolved pool raises an exception, `success` becomes `False`. Consequently, `self._last_block` is not advanced to `current_block`. On the next loop tick, log fetching for all pools (including previously successful ones) is repeated starting from the old `_last_block + 1`, emitting duplicate swaps. This was empirically verified in `tests/exchanges/base_onchain/test_challenger_stress_2.py` in `test_cursor_behavior_on_exceptions`.

---

## 2. Logic Chain

1. **Observation A** shows that `get_onchain_price` instantiates `AsyncHTTPProvider` (which initializes a new `aiohttp.ClientSession` internally) every time it is called.
2. The code never closes or disconnects this provider, leaving the underlying session open.
3. This is verified by our warning check task (Observation A), which shows that 100 calls yield 100 unclosed session warnings.
4. Under heavy polling (stress conditions), this will exhaust available file descriptors, causing socket errors and crashing the API/MCP server.
5. **Observation B** shows that `success` is shared across all pools in the polling loop.
6. If any one pool fails, `success` becomes `False`, which prevents `self._last_block` from updating.
7. Consequently, the log query range `[self._last_block + 1, current_block]` is reused for the next iteration.
8. Any successful pool in the previous iteration will have its logs queried and processed again, leading to duplicates.
9. Therefore, the implementation is incorrect under connection drops/RPC failures.

---

## 3. Caveats

- We did not stress-test real-world rate-limiting of public mainnet RPC nodes, as this would get the client IP banned. The latency/errors were simulated via mocking.
- The payment gate's signature verification is a mock implementation and is not production-grade, but this is a documented constraint of the demo.

---

## 4. Conclusion

The Milestone 1 implementation contains critical design issues:
- **Resource leak** in `get_onchain_price` under load.
- **Log query duplication** in `BaseOnchainTransport` under connection drops.
To pass verification, these must be resolved by:
1. Reusing `AsyncWeb3` connection instances or cleanly closing the session.
2. Tracking the cursor block number per pool/symbol rather than globally.

---

## 5. Verification Method

To run the full test suite and verify results:
```bash
uv run pytest tests/exchanges/base_onchain/
```
To run the resource leak replication script:
```bash
uv run python -Wd -c "
import asyncio
from crypcodile.mcp_server import get_onchain_price
asyncio.run(asyncio.gather(*[get_onchain_price('cbBTC-USDC') for _ in range(50)]))
"
```
Check if it outputs `ResourceWarning: Unclosed client session`.
