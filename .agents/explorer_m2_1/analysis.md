# Milestone 2 Analysis: Log Pagination & Backoff Retries

This report provides an in-depth investigation and audit of Milestone 2 (Log pagination & backoff retries) in `src/crypcodile/exchanges/base_onchain/connector.py` and the associated unit and stress tests.

---

## Executive Summary

The implementation of Milestone 2 is **mostly correct and performs correct chunk calculations** under normal conditions. However, the polling loop in `connector.py` contains **critical architectural flaws and regression bugs** that compromise its robustness:
1. **Uniswap V3 Query Failures** trigger an `UnboundLocalError` (due to Step C accessing `slot0` or `liquidity` outside the `try-except` block), which aborts the entire iteration, skipping other configured symbols.
2. **Aerodrome V2 Query Failures** silently proceed to push corrupted zero-price/zero-reserve updates to the queue instead of skipping the update.
3. **Negative Block Cursors** on local/test networks (when `current_block < 20`) result in passing negative integers to `get_logs`, crashing the poll loop.
4. **Retry Logic lacks random jitter**, exposing the system to rate-limit synchronization (thundering herd problem).
5. **Coroutine Reuses** inside the generic `_call_with_retry` API design will crash with `RuntimeError: cannot reuse already awaited coroutine` if a raw coroutine object fails and is retried.

The test coverage is exceptionally high and adversarial test suites successfully reproduce and expose these exact bugs.

---

## 1. Block-Range Pagination Logic (500 Block Chunks)

### Analysis of the Chunking Mechanism
The chunking logic is implemented in `connector.py` (lines 567–585) inside the `_poll_loop` method of `BaseOnchainTransport`:
```python
start_block = self._last_blocks[sym] + 1
end_block = current_block

logs = []
if start_block <= end_block:
    chunk_size = 500
    for from_b in range(start_block, end_block + 1, chunk_size):
        to_b = min(from_b + chunk_size - 1, end_block)
        chunk_logs = await self._call_with_retry(
            w3.eth.get_logs,
            {
                "address": addr,
                "fromBlock": from_b,
                "toBlock": to_b,
                "topics": [swap_topic]
            }
        )
        logs.extend(chunk_logs)
```

### Assessment
* **Correctness**: The math for calculating pagination bounds (`from_b` and `to_b`) is correct. For a range like `1001` to `2100` with `chunk_size = 500`, it generates three distinct non-overlapping bounds: `1001-1500`, `1501-2000`, and `2001-2100`.
* **RPC Limits Compatibility**: Public RPC providers (such as publicnode.com) restrict the block range of log queries (often to 1000–2000 blocks). Capping chunks at `500` blocks is a robust design that prevents query rejection due to large block ranges.
* **Cursor Isolation**: The block cursors are stored per-symbol in `self._last_blocks[sym]`. If a specific pool fails during querying, its block cursor does not advance, whereas other successful pools advance. This prevents redundant historical fetching of successful pools.
* **Latent Bug (Negative Block Numbers)**: 
  Upon initialization, `self._last_blocks[sym]` is configured as `current_block - 20`. On local testnets (Anvil, Hardhat, Ganache) where `current_block < 20`, this results in a negative cursor. The resulting negative `fromBlock` throws a validator exception in `get_logs`, crashing the poll loop.

---

## 2. Exponential Backoff Retry Logic

### Analysis of the Retry Mechanism
`BaseOnchainTransport` utilizes `_call_with_retry` (lines 261–288) for all RPC calls:
```python
async def _call_with_retry(self, func: Any, *args: Any, **kwargs: Any) -> Any:
    attempt = 0
    max_attempts = 5
    base_delay = kwargs.pop("base_delay", 0.0001 if self.poll_interval < 0.2 else 1.0)
    max_delay = 10.0
    
    while True:
        try:
            if callable(func):
                res = func(*args, **kwargs)
            else:
                res = func
            
            while inspect.isawaitable(res):
                res = await res
            return res
        except Exception as e:
            attempt += 1
            if attempt >= max_attempts:
                raise
            delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
            await asyncio.sleep(delay)
```

### Assessment
* **Backoff Scaling**: Delay successfully doubles each attempt: $1\text{s} \to 2\text{s} \to 4\text{s} \to 8\text{s}$ (bounded by `max_delay = 10.0`), which is correct.
* **Dead Code**: There is a global function `retry_rpc` defined at lines 211-239 which is never used in the codebase. It has been replaced by `_call_with_retry`.
* **Gap 1: Missing Jitter**: Unlike the dead `retry_rpc` function, `_call_with_retry` has no random jitter. If multiple instances hit the public RPC and get rate-limited, they will retry at synchronized intervals, exacerbating rate limiting (thundering herd).
* **Gap 2: Awaiting Coroutines**: If a raw coroutine object is passed as `func` (e.g. `self._call_with_retry(w3.eth.get_block(num))`), `callable(func)` evaluates to `False`, so `res` becomes the coroutine object. On the first failure, the coroutine is partially or fully awaited. On the second attempt, the retry loop tries to await the same coroutine object again, raising `RuntimeError: cannot reuse already awaited coroutine`. Fortunately, current calls pass the bound function/callable itself rather than the coroutine object, but the API design remains a risk.

---

## 3. Test Coverage

### Assessment
* **Quantity**: There are 9 distinct test files under `tests/exchanges/base_onchain/` containing a total of 47 tests. All tests execute and pass successfully (`47 passed in 1.06s` using `uv run pytest`).
* **Quality**:
  * `test_connector.py` verifies standard pool queries, flipped token orders (USDC address comparison sorting), and mock log extraction.
  * `test_rpc_retries_and_call_with_retry` validates retry counts and exponential delays.
  * `test_log_pagination_chunking` mock-asserts that exactly 3 calls are made to `get_logs` for a 1100 block range and that bounds are exactly `1001-1500`, `1501-2000`, `2001-2100`.
  * `test_slot0_unbound_local_error` and `test_unbound_local_error_regression_*` reproduce the exact bugs in the connector's exception handling, demonstrating code coverage and verification.
  * `test_connector_dlq_on_corrupted_message` ensures malformed JSON is isolated in the Dead-Letter Queue.

---

## 4. Detailed Bug Reports

### Bug A: Uniswap V3 `UnboundLocalError` Loop Termination
* **Location**: `connector.py`, lines 676-694.
* **Trace**:
  1. `contract.functions.slot0().call` throws an exception (e.g. RPC timeout).
  2. The inner `except Exception as e:` at line 676 catches the exception and logs it.
  3. The execution jumps out of the `try-except` block to Step C.
  4. At line 689, `state_payload["tick"] = int(slot0[1])` is executed.
  5. Since `slot0` was never assigned inside the failed `try` block, Python raises `UnboundLocalError`.
  6. The `UnboundLocalError` propagates to the outer `try-except` (line 705), logging `Error polling pool data: ...` and aborting the current polling iteration. Other symbols in the same loop iteration are skipped.

### Bug B: Aerodrome V2 Corrupted Payload Emission
* **Location**: `connector.py`, lines 676-687.
* **Trace**:
  1. `contract.functions.getReserves().call` fails.
  2. The inner `except Exception as e:` catches and logs it.
  3. Execution moves to Step C. Since it is Aerodrome, it skips the Uniswap V3 slot0 check.
  4. It constructs `state_payload` using `price = 0.0`, `reserve0 = 0.0`, and `reserve1 = 0.0` (initialized at lines 488–490).
  5. It pushes the update `{"price": 0.0, "reserve0": 0.0, "reserve1": 0.0}` to the queue. Downstream memory sinks receive a corrupt zero-state representation of the pool.

### Bug C: Negative Block Cursor on Local/Testnets
* **Location**: `connector.py`, line 494.
* **Trace**:
  1. On startup, the pool cursor initializes: `self._last_blocks[sym] = current_block - 20`.
  2. In local testnets, if `current_block` is `< 20`, the cursor becomes negative.
  3. `get_logs` throws an RPC error because `fromBlock` is negative, preventing the connector from starting.

---

## 5. Recommended Fix Strategy

To resolve the above bugs and gaps without breaking any interface contract, apply the following changes to `src/crypcodile/exchanges/base_onchain/connector.py`:

### Fix 1: Restructure the Polling Loop (Fixes Bug A & B)
Move the entire Step C logic inside the inner `try` block (or introduce `continue` in the `except` block). Pushing the payload and updating the cursor must only occur if all data fetches succeeded.

* **Proposed Structure**:
```python
                    for sym, pool in resolved_pools.items():
                        spec = pool["spec"]
                        addr = pool["address"]
                        contract = pool["contract"]
                        is_flipped = pool["is_flipped"]
                        
                        if sym not in self._last_blocks:
                            self._last_blocks[sym] = max(0, current_block - 20)  # Fix C
                        
                        try:
                            # A. Query current price and reserves/liquidity
                            price = 0.0
                            reserve0 = 0.0
                            reserve1 = 0.0
                            swaps = []
                            
                            if spec["type"] == "uniswap_v3":
                                slot0 = await self._call_with_retry(
                                    contract.functions.slot0().call
                                )
                                liquidity = await self._call_with_retry(
                                    contract.functions.liquidity().call
                                )
                                # ... calculations for price, reserve0, reserve1 ...
                            else: # aerodrome_v2
                                res = await self._call_with_retry(
                                    contract.functions.getReserves().call
                                )
                                # ... calculations for price, reserve0, reserve1 ...
                            
                            # B. Fetch Swap logs
                            # ... pagination chunking logic ...
                            
                            # C. Push state update to queue (moved INSIDE try block)
                            state_payload = {
                                "price": price,
                                "reserve0": reserve0,
                                "reserve1": reserve1,
                                "is_flipped": is_flipped,
                                "decimals0": spec["decimals0"],
                                "decimals1": spec["decimals1"],
                            }
                            if spec["type"] == "uniswap_v3":
                                state_payload["tick"] = int(slot0[1])
                                state_payload["liquidity"] = int(liquidity)
                                state_payload["tickSpacing"] = int(tick_spacing)
                                state_payload["tick_spacing"] = int(tick_spacing)
                            
                            update_msg = {
                                "type": "onchain_update",
                                "block": current_block,
                                "pool": sym,
                                "pool_type": spec["type"],
                                "timestamp": await self._get_block_timestamp(w3, current_block),
                                "state": state_payload,
                                "swaps": swaps
                            }
                            await self._queue.put(json.dumps(update_msg).encode())
                            
                            # Only advance the cursor if the entire block execution succeeded
                            self._last_blocks[sym] = max(self._last_blocks[sym], current_block)
                            
                        except Exception as e:
                            log.error(f"base_onchain: Error polling pool data for {sym}: {e}")
```

### Fix 2: Jitter Addition to Retry Logic (Fixes Gap 4)
In `_call_with_retry` (lines 283–288), apply a standard 50%-100% random scaling factor to break retry lockstep synchronization:

* **Proposed Replacement**:
```python
                delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
                delay = delay * (0.5 + random.random() * 0.5)
                log.warning(
                    f"RPC call failed: {e}. Retrying in {delay:.4f}s... "
                    f"(Attempt {attempt}/{max_attempts})"
                )
                await asyncio.sleep(delay)
```

### Fix 3: Remove Dead Function
Remove the unused `retry_rpc` function (lines 211–239) to clean up code complexity.
