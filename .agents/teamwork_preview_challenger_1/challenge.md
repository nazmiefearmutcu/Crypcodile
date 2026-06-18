## Challenge Summary

**Overall risk assessment**: MEDIUM

While the normalizer and connector loops are functionally robust and incorporate defensive capping and error isolation (via the Dead Letter Queue), there are key architectural issues related to blocking calls in the event loop and pool-query isolation that could cause performance degradation under stress.

---

## Challenges

### [Medium] Challenge 1: Blocking calls in asynchronous poll loop
- **Assumption challenged**: The polling transport runs asynchronously in the event loop (`async def _poll_loop`) and assumes it will not block other asynchronous tasks.
- **Attack scenario**: The transport makes multiple synchronous, blocking RPC queries (e.g. `w3.eth.block_number`, `contract.functions.slot0().call()`, `w3.eth.get_logs()`) inside the polling loop. If the target RPC endpoint experiences high latency or hangs, the entire asyncio event loop will be blocked for seconds.
- **Blast radius**: High. Any other concurrent connectors or services sharing the same Python event loop will freeze or timeout.
- **Mitigation**: Wrap all synchronous Web3 network calls in `asyncio.to_thread(...)` or execute them via a thread pool executor to offload them from the main event loop.

### [Medium] Challenge 2: Lack of isolation between individual pool queries in the polling loop
- **Assumption challenged**: If querying one resolved pool fails, it will not disrupt the updates of other pools.
- **Attack scenario**: Inside the loop `for sym, pool in resolved_pools.items():`, if a query (such as `slot0().call()`) raises an exception for a single pool, that exception propagates out of the `for` loop to the outer `try-except` block.
- **Blast radius**: Medium. The update for all remaining pools in that block iteration is skipped, and `self._last_block = current_block` is not set. In the next iteration, the transport will re-query the same block range, leading to duplicate swap retrieval attempts.
- **Mitigation**: Wrap the state query block for each individual pool in its own inner `try-except` block so that one failing RPC query does not skip updates for other pools.

### [Low] Challenge 3: Propagation of NaN and Infinity values
- **Assumption challenged**: Inputs from the RPC endpoint do not contain `NaN` or `Infinity` prices.
- **Attack scenario**: If a pool state contains `price: float('nan')`, the check `price <= 0` evaluates to `False`. This produces `BookTicker` and `BookSnapshot` records with `NaN` bid and ask prices.
- **Blast radius**: Medium. Downstream consumers/analytics engines might crash or produce incorrect calculations if they assume prices are always valid real numbers.
- **Mitigation**: Add validation in the normalizer to return early if the state price is `NaN` or `Infinity`.

---

## Stress Test Results

- **Standard baseline update** → Expected: Yields 1 Trade, 1 BookTicker, and 1 BookSnapshot correctly → Actual: 3 records generated, all fields valid → **PASS**
- **Zero or negative price** → Expected: Normalizer returns early, yielding no BookTicker or BookSnapshot → Actual: Returned early, no records generated → **PASS**
- **Extremely small price (1e-30)** → Expected: Correctly calculates bid size without overflow → Actual: Correctly handled, bid_sz scales up → **PASS**
- **Extremely large price (1e30)** → Expected: Bid size is very small, capped at the minimum size limit (0.0001) → Actual: Correctly capped at 0.0001 → **PASS**
- **Infinity price** → Expected: Handled without crashing; yields inf bid/ask px and capped 0.0001 bid_sz → Actual: Handled without crashing, inf values generated → **PASS**
- **NaN price** → Expected: Handled without crashing; price > 0 check evaluates to False, bid size defaults to 0.0 (capped at 0.0001) → Actual: Handled without crashing → **PASS**
- **Zero, negative, or missing reserves** → Expected: Capped at minimum size (0.0001) → Actual: Correctly capped at 0.0001 → **PASS**
- **Large number of swaps (10,000)** → Expected: Parses all swaps quickly without performance/memory bottleneck → Actual: Parses 10,002 records in <0.05s → **PASS**
- **Corrupted/Missing dictionary keys** → Expected: KeyError/TypeError is raised during parsing, caught by connector's supervised run loop, and the bad payload is routed to the Dead Letter Queue (DLQ) while the connector continues running → Actual: Successfully routed to DLQ; connector continued running without crash → **PASS**

---

## Unchallenged Areas

- **Web3 Provider internal details** — We mock the Web3 instance and its network responses; we do not challenge the internal socket connections or the provider's connection pool limits.
