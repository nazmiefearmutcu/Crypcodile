## Challenge Summary

**Overall risk assessment**: HIGH

Through systematic code review and empirical testing, we identified two significant vulnerabilities:
1. **Unclosed Client Sessions / Socket Leak**: Creating a new `AsyncWeb3` instance on every request in `get_onchain_price` without closing/disconnecting the provider leaks client sessions (`aiohttp.ClientSession`). Under heavy load, this results in resource exhaustion (Too many open files / socket leaks).
2. **Cursor/Duplicate Log Fetching on Partial Failures**: In the Base onchain transport, a single pool query failure sets `success = False` and prevents `_last_block` from advancing. This forces the transport to query previously successful blocks again on the next tick, generating duplicate swap logs.

---

## Challenges

### [High] Challenge 1: Unclosed Client Sessions / Connection Leak in `get_onchain_price`

- **Assumption challenged**: Instantiating a new `AsyncWeb3` client per request is safe and resource-neutral.
- **Attack scenario**: High-concurrency polling of `/api/v1/market-data` or the MCP server tools instantiates many new `AsyncHTTPProvider` objects. Each provider allocates an underlying `aiohttp.ClientSession` which is never closed. This leads to Python `ResourceWarning: Unclosed client session` and ultimately socket/file descriptor exhaustion.
- **Blast radius**: The server crashes or hangs, rejecting further requests with connection or socket exceptions.
- **Mitigation**: 
  - Manage a shared/singleton `AsyncWeb3` instance in the MCP/API servers rather than instantiating a new one per function call.
  - Or, explicitly dispose of the connection at the end of `get_onchain_price` using `await w3.provider.disconnect()`.

### [Medium] Challenge 2: Duplicate Log Queries under Partial RPC Failures

- **Assumption challenged**: A single global `self._last_block` cursor is sufficient to track processed logs across all queried pools.
- **Attack scenario**: When the transport polls multiple pools:
  1. Iteration 1 starts at block 1000. Pool A succeeds; Pool B fails. `success` becomes `False`.
  2. `self._last_block` is NOT updated (stays at 980).
  3. Iteration 2 starts at block 1005. Both Pool A and B succeed. Log queries for both start from 981 to 1005.
  4. Pool A's swaps from blocks 981 to 1000 are queried and processed a second time, emitting duplicate trade records to downstream sinks.
- **Blast radius**: Emitting duplicate trade records during network hiccups or RPC errors, corrupting data lake integrity.
- **Mitigation**: Track the last queried block independently per pool/symbol (e.g. `self._last_block = {}` mapping `symbol -> block_number`).

---

## Stress Test Results

- **Server Flow Direct** (FastAPI route integration) → Verifies payment gate state transitions → Direct calls to FastAPI async routes → **PASS**
- **Non-blocking Event Loop** (slow RPC nodes) → Tickers in event loop are not blocked by slow network tasks → Ran concurrent ticks with simulated RPC latency → **PASS**
- **Cursor Behavior on Exception** → Verify cursor does not advance if any query fails → Verified `_last_block` stayed at initial value → **PASS**
- **Resource Leak Test** (100 sequential calls) → `get_onchain_price` called 100 times in loop → Yielded 100 `ResourceWarning: Unclosed client session` → **FAIL** (exposes leak)

---

## Unchallenged Areas

- **DuckDB Parquet queries via CrypcodileClient** — Out of scope for native AsyncWeb3 refactoring review.
- **Signature verification cryptographics** — The API server uses a placeholder/mock signature verifier for demo purposes.
