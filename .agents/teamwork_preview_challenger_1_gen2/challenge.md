## Challenge Summary

**Overall risk assessment**: MEDIUM

While the event loop blocking issues from Iteration 1 have been successfully resolved by wrapping all synchronous Web3 network calls in `asyncio.to_thread`, a critical cursor vulnerability remains. Specifically, if a single pool query fails consistently, the block cursor `self._last_block` is never updated. This causes the block query range for ALL pools to grow indefinitely, which will eventually hit RPC provider constraints (e.g., query limits or timeout errors) and produce massive duplicate payloads for the healthy pools.

---

## Challenges

### [High] Challenge 1: Stuck Block Cursor & Indefinite Query Range Expansion
- **Assumption challenged**: The transport uses a single transport-wide `self._last_block` cursor and assumes that if any pool queries fail, retrying the whole block range from the last successful block is safe.
- **Attack scenario**: If one pool (e.g., WELL-WETH) consistently raises RPC exceptions (due to misconfiguration, RPC node errors, or contract halts), `success` is marked `False` in every iteration. As a result, `self._last_block` is never updated. On subsequent poll intervals, the block query range `fromBlock: self._last_block + 1` to `current_block` grows larger and larger.
- **Blast radius**: High. The log queries for all other (healthy) pools will be repeatedly executed over a progressively larger block range, generating massive duplicate swap updates. Ultimately, the RPC provider will reject the query due to block limit restrictions (e.g. maximum block range limit exceeded) or timeout/OOM issues, disabling the entire transport.
- **Mitigation**: Maintain a per-pool block cursor (`self._last_block_per_pool: dict[str, int]`) instead of a single global `self._last_block`. If a pool successfully polls, its individual cursor progresses. If a pool fails, only its cursor remains stuck, isolating the failure.

### [Low] Challenge 2: Propagation of NaN Prices in Normalizer
- **Assumption challenged**: Inputs from the RPC node are always valid, non-NaN real numbers.
- **Attack scenario**: If a pool state contains a `price` of `float('nan')`, the check `price <= 0` evaluates to `False`. The normalizer will proceed and generate `BookTicker` and `BookSnapshot` records with `NaN` bid and ask prices.
- **Blast radius**: Low. While the system does not crash directly, downstream consumers or data pipelines (like DuckDB / parquet store) may crash or throw errors when attempting to store or calculate statistics on `NaN` price values.
- **Mitigation**: Check for `math.isnan(price)` or `math.isinf(price)` and return early to prevent downstream propagation.

---

## Stress Test Results

- **Non-blocking Event Loop Test** → Expected: Event loop ticks continue running concurrently during Web3 RPC operations → Actual: Event loop ticked 30+ times, proving RPC thread-pool isolation is functional → **PASS**
- **Pool Resolution Retry Test** → Expected: Initially failing pool address resolution is retried and succeeds on subsequent poll interval → Actual: getPool retried and successfully queued the pool state → **PASS**
- **Cursor Stuck and Growing Range Test** → Expected: If one pool fails, the block cursor does not advance, causing subsequent iterations to query from the old block range → Actual: Verified that `fromBlock` remains stuck at `981` while `toBlock` increases, confirming the range expansion risk → **PASS**
- **Normalizer Robustness against Invalid Types** → Expected: Providing invalid price type (e.g. string) raises `TypeError` which the connector run loop catches and routes to DLQ → Actual: `TypeError` was raised as expected → **PASS**

---

## Unchallenged Areas

- **DuckDB storage constraints** — We verified the connector and normalizer flow, but did not test how the DuckDB backend reacts to incoming `NaN`/`Infinity` float values under long-running scenarios.
