# Changes Document — Milestone 2

This document records the modifications made to `src/crypcodile/exchanges/base_onchain/connector.py` to address the Milestone 2 requirements.

## Modifications in `src/crypcodile/exchanges/base_onchain/connector.py`

### 1. Polling Loop Restructuring (UnboundLocalError & Zeroed-out Updates Prevention)
- **Problem**: Query failures on a Uniswap V3 pool triggered `UnboundLocalError` when accessing `slot0` or `liquidity` inside Step C because it sat outside the `try` block. This crashed the entire polling iteration for all subsequent pools. Furthermore, failures on Aerodrome V2 pools silently proceeded to queue zeroed-out price/reserve updates (`0.0`).
- **Fix**: 
  - Restructured the polling loop by moving Step C (queue payload construction and pushing) inside the inner `try` block of the pool processing loop.
  - If a price/reserve query fails, the loop catches the exception at the pool level, logs it, and continues to the next pool. No payload is queued, preventing zeroed-out updates.
  - Wrapped the `get_logs` query in its own `try-except` block, setting a `log_query_success` flag to `False` if it fails. If `get_logs` fails, we still queue the valid state update (containing correct non-zero price and reserves) but do *not* advance the block cursor (`self._last_blocks[sym]`). This preserves the block range for future query retries and maintains compatibility with E2E and resilience tests.

### 2. Negative Block Index Cursor Initialization Fix
- **Problem**: On startup, `self._last_blocks[sym] = current_block - 20` evaluated to a negative number on local testnets (where `current_block < 20`), causing RPC query validation failures.
- **Fix**: Initialized the cursor using `max(0, current_block - 20)`.

### 3. Jitter in `_call_with_retry`
- **Problem**: Retries were synchronized across instances, exposing the system to the thundering herd problem under rate limits.
- **Fix**: Multiplied the calculated exponential delay by `random.uniform(0.5, 1.0)` to introduce a 50% to 100% scaling factor jitter.

### 4. Dead Code Cleanup
- **Problem**: The global `retry_rpc` function was unused and redundant.
- **Fix**: Removed `retry_rpc` completely from the codebase.
