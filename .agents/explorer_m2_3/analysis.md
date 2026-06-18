# Analysis Report: Milestone 2 — Log Pagination & Backoff Retries

## Executive Summary
Milestone 2 implementation in `src/crypcodile/exchanges/base_onchain/connector.py` is mostly complete and passes the current unit tests, but is not robust. Specifically, querying errors on Uniswap V3 pools raise uncaught `UnboundLocalError` that abort the entire polling loop cycle, querying errors on Aerodrome V2 pools emit zeroed-out state updates (`price = 0.0`) to the queue, and the exponential backoff retry logic lacks jitter and collapses to sub-millisecond retry delays on short poll intervals.

---

## Detailed Findings

### 1. Block-Range Pagination Logic
The pagination logic is located in `BaseOnchainTransport._poll_loop` (lines 567–584):
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
- **Correctness**: The block pagination divides the block range into exactly 500-block chunks without overlapping or missing any block.
- **Block Lag Resilience**: It uses `self._last_blocks[sym] = max(self._last_blocks[sym], current_block)` (line 675). This ensures that if the RPC node momentarily returns a lagging block number (`current_block < self._last_blocks[sym]`), `start_block > end_block` is evaluated, skipping the log query entirely and preventing duplicate log fetches. The cursor remains at the highest processed block.
- **Robustness**: The pagination itself is correct and robust against node-level block count fluctuations.

---

### 2. Exponential Backoff Retry Logic
RPC calls are wrapped in `_call_with_retry` (lines 261–289):
```python
async def _call_with_retry(self, func: Any, *args: Any, **kwargs: Any) -> Any:
    ...
    base_delay = kwargs.pop("base_delay", 0.0001 if self.poll_interval < 0.2 else 1.0)
    max_delay = 10.0
    ...
    while True:
        try:
            ...
        except Exception as e:
            attempt += 1
            if attempt >= max_attempts:
                log.error(f"RPC call failed after {attempt} attempts: {e}")
                raise
            delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
            log.warning(
                f"RPC call failed: {e}. Retrying in {delay:.4f}s... (Attempt {attempt}/{max_attempts})"
            )
            await asyncio.sleep(delay)
```
- **Jitter Gap**: There is no randomized jitter applied to the delay. This leads to the **thundering herd** problem where multiple parallel tasks or pools hammer the RPC node at identical intervals after a rate limit or connection drop occurs.
- **Poll Interval Collapsing Delay**: In `_call_with_retry`, the default `base_delay` scales down to `0.0001` seconds if `self.poll_interval < 0.2`. When executing with low poll intervals (common in testing or fast-polling settings), a retry delay of `0.0001` means all 5 retry attempts are exhausted almost instantaneously (within ~0.003 seconds), completely defeating the purpose of exponential backoff.
- **Dead Code**: The global helper `retry_rpc` (lines 211–239) is fully implemented with randomized jitter but is not called anywhere in the codebase.

---

### 3. Identified Gaps & Bugs

#### Bug 1: `UnboundLocalError` on Uniswap V3 Failures
If `slot0` or `liquidity` contract calls fail for a Uniswap V3 pool (e.g., due to temporary RPC connection failure), the inner `try-except` block (lines 496–676) catches the error and logs it. However, the loop continues to **Block C** (lines 680–703) to build the queue payload:
```python
if spec["type"] == "uniswap_v3":
    state_payload["tick"] = int(slot0[1])       # slot0 was never defined!
    state_payload["liquidity"] = int(liquidity) # liquidity was never defined!
```
Because the queries failed, `slot0` and `liquidity` were never defined, causing Python to raise an `UnboundLocalError`. This exception is uncaught by the inner try-except, and is caught by the outer loop try-except (lines 705–706):
```python
except Exception as e:
    log.error(f"base_onchain: Error polling pool data: {e}")
```
This aborts the entire iteration cycle. Any pool configured *after* the failing pool in `resolved_pools` is not updated during that poll cycle.

#### Bug 2: Zeroed-Out State Updates
If an Aerodrome V2 pool query fails (e.g. `getReserves` raises an exception), the inner try-except catches and logs it. Because Aerodrome does not use `slot0` or `liquidity` in Block C, it does not trigger `UnboundLocalError`. Instead, it uses the default initialized values:
```python
price = 0.0
reserve0 = 0.0
reserve1 = 0.0
swaps = []
```
Block C proceeds to queue the state payload with `price: 0.0`, `reserve0: 0.0`, and `reserve1: 0.0`. This corrupts downstream consumers with false state values representing zero price and zero reserves.

---

### 4. Test Coverage Analysis
The codebase contains tests in `tests/exchanges/base_onchain/`. Specifically:
- `test_connector.py` tests standard/flipped pools and chunked log pagination.
- `test_adversarial.py` validates handling of extreme prices, large numbers of swaps, and duplicate query prevention on block lags.
- `test_challenger_stress_2.py` tests non-blocking event loops, pool resolution retry, and cursor behavior on exceptions.
- `test_challenger_stress_3.py` verifies block cache memory constraints and cursor behavior on block lag.
- `test_challenger_stress_4.py` checks for regressions related to unbound variables.

**Coverage Gaps**:
- `test_unbound_local_error_regression_uniswap` in `test_challenger_stress_4.py` checks that `"swaps"` is not unbound, but it passes while the logs output:
  `ERROR base_onchain: Error polling pool data: cannot access local variable 'slot0' where it is not associated with a value`.
  The test incorrectly asserts success because the crash happens on `slot0` rather than `swaps`.
- No test checks if pushing to `_queue` is skipped when a poll fails. Thus, zeroed-out state payloads for Aerodrome V2 (or Uniswap V3 if `UnboundLocalError` is bypassed) are currently accepted and queued without any validation failing.

---

## Recommended Fix Strategy

### 1. Restructure Polling Loop (Fix for Bug 1 & 2)
Scope Block C (building payloads and pushing to the queue) inside the inner `try` block of the `for sym, pool in resolved_pools.items()` loop. This guarantees that:
- Payloads are only constructed and pushed if all RPC queries succeed.
- Zeroed-out state updates are never queued.
- Unbound variables (`slot0`, `liquidity`, etc.) are never accessed.
- A failure in one pool is cleanly logged, the cursor for that pool is not advanced, and the loop continues to process other pools in the current cycle.

#### Code Sketch (Restructuring `connector.py` line 482–704):
```python
for sym, pool in resolved_pools.items():
    spec = pool["spec"]
    addr = pool["address"]
    contract = pool["contract"]
    is_flipped = pool["is_flipped"]
    
    if sym not in self._last_blocks:
        self._last_blocks[sym] = current_block - 20
    
    try:
        price = 0.0
        reserve0 = 0.0
        reserve1 = 0.0
        swaps = []
        
        # A. Query current price and reserves/liquidity
        if spec["type"] == "uniswap_v3":
            slot0 = await self._call_with_retry(
                contract.functions.slot0().call
            )
            liquidity = await self._call_with_retry(
                contract.functions.liquidity().call
            )
            try:
                tick_spacing = await self._call_with_retry(
                    contract.functions.tickSpacing().call
                )
            except Exception as e:
                log.warning(
                    f"base_onchain: Failed to fetch tickSpacing dynamically: {e}. Deriving from fee tier."
                )
                fee = int(spec.get("fee", 3000))
                tick_spacing = (
                    1 if fee == 100 else
                    10 if fee == 500 else
                    60 if fee == 3000 else
                    200 if fee == 10000 else
                    max(1, fee // 50)
                )
            
            sqrtPriceX96 = slot0[0]
            price_ratio = (sqrtPriceX96 / (2**96)) ** 2
            dec_diff = int(spec["decimals0"]) - int(spec["decimals1"])
            if not is_flipped:
                price = price_ratio * (10 ** dec_diff)
            else:
                price = (
                    (1.0 / price_ratio) * (10 ** dec_diff)
                    if price_ratio > 0 else 0.0
                )
            
            sqrtP = sqrtPriceX96 / (2**96)
            x_virtual = liquidity / sqrtP if sqrtP > 0 else 0
            y_virtual = liquidity * sqrtP
            
            if not is_flipped:
                reserve0 = x_virtual / (10 ** int(spec["decimals0"]))
                reserve1 = y_virtual / (10 ** int(spec["decimals1"]))
            else:
                reserve0 = y_virtual / (10 ** int(spec["decimals0"]))
                reserve1 = x_virtual / (10 ** int(spec["decimals1"]))
        
        else: # aerodrome_v2
            res = await self._call_with_retry(
                contract.functions.getReserves().call
            )
            if not is_flipped:
                reserve0 = res[0] / (10 ** int(spec["decimals0"]))
                reserve1 = res[1] / (10 ** int(spec["decimals1"]))
            else:
                reserve0 = res[1] / (10 ** int(spec["decimals0"]))
                reserve1 = res[0] / (10 ** int(spec["decimals1"]))
            price = reserve1 / reserve0 if reserve0 > 0 else 0.0

        # B. Fetch Swap logs
        swap_topic = (
            SWAP_TOPIC_V3 if spec["type"] == "uniswap_v3"
            else SWAP_TOPIC_V2
        )
        
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
        
        for lg in logs:
            data = lg["data"]
            tx_hash = lg["transactionHash"].hex()
            log_index = lg["logIndex"]
            ts = await self._get_block_timestamp(w3, lg["blockNumber"])
            
            if spec["type"] == "uniswap_v3":
                amount0 = int.from_bytes(data[0:32], byteorder='big', signed=True)
                amount1 = int.from_bytes(data[32:64], byteorder='big', signed=True)
                if not is_flipped:
                    abs_base = abs(amount0) / (10 ** int(spec["decimals0"]))
                    abs_quote = abs(amount1) / (10 ** int(spec["decimals1"]))
                    is_buy = amount0 < 0
                else:
                    abs_base = abs(amount1) / (10 ** int(spec["decimals0"]))
                    abs_quote = abs(amount0) / (10 ** int(spec["decimals1"]))
                    is_buy = amount1 < 0
                sw_price = abs_quote / abs_base if abs_base > 0 else 0.0
                swaps.append({
                    "tx_hash": tx_hash,
                    "log_index": log_index,
                    "timestamp": ts,
                    "price": sw_price,
                    "amount": abs_base,
                    "is_buy": is_buy
                })
            else: # aerodrome_v2
                amt0_in = int.from_bytes(data[0:32], byteorder='big', signed=False)
                amt1_in = int.from_bytes(data[32:64], byteorder='big', signed=False)
                amt0_out = int.from_bytes(data[64:96], byteorder='big', signed=False)
                amt1_out = int.from_bytes(data[96:128], byteorder='big', signed=False)
                if not is_flipped:
                    amt_base = (amt0_in if amt0_in > 0 else amt0_out) / (10 ** int(spec["decimals0"]))
                    amt_quote = (amt1_in if amt1_in > 0 else amt1_out) / (10 ** int(spec["decimals1"]))
                    is_buy = amt0_out > 0
                else:
                    amt_base = (amt1_in if amt1_in > 0 else amt1_out) / (10 ** int(spec["decimals0"]))
                    amt_quote = (amt0_in if amt0_in > 0 else amt0_out) / (10 ** int(spec["decimals1"]))
                    is_buy = amt1_out > 0
                sw_price = amt_quote / amt_base if amt_base > 0 else 0.0
                swaps.append({
                    "tx_hash": tx_hash,
                    "log_index": log_index,
                    "timestamp": ts,
                    "price": sw_price,
                    "amount": amt_base,
                    "is_buy": is_buy
                })
        
        self._last_blocks[sym] = max(self._last_blocks[sym], current_block)
        
        # C. Push state update to queue (Now securely inside try block)
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
        
    except Exception as e:
        log.error(f"base_onchain: Error polling pool data for {sym}: {e}")
```

### 2. Fix Exponential Backoff Jitter and Base Delay Collapse
1. Add jitter to `_call_with_retry` (identical to the unused `retry_rpc` implementation):
   ```python
   delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
   delay = delay * (0.5 + random.random() * 0.5)
   ```
2. Remove the poll-interval dependency for `base_delay` default. Keep a standard static default in the signature:
   ```python
   async def _call_with_retry(
       self,
       func: Any,
       *args: Any,
       max_attempts: int = 5,
       base_delay: float = 1.0,
       max_delay: float = 10.0,
       **kwargs: Any
   ) -> Any:
   ```
   If tests need a faster run, they can pass `base_delay=0.0001` explicitly (which `test_rpc_retries_and_call_with_retry` already does). This guarantees production runs have a robust delay pattern.
