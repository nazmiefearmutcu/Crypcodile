## Challenge Summary

**Overall risk assessment**: LOW

The `base_onchain` connector and its normalizer logic are robustly designed. Polling loops, RPC requests, and payload normalization operations are guarded by appropriate exception handlers. Corrupted payloads or missing data fields are automatically intercepted at the supervisor level and redirected to the Dead Letter Queue (DLQ), ensuring the process remains active.

## Challenges

### [Low] Challenge 1: Schema Invariance / Corrupted Payload Keys
- **Assumption challenged**: The payload structure received from the transport layer is invariant and will always contain all expected keys (e.g. `"pool"`, `"state"`, `"price"`, `"block"`, `"timestamp"`).
- **Attack scenario**: A modification in the transport layer or a malformed data frame from a custom endpoint results in missing keys.
- **Blast radius**: The generator `normalize_onchain_update` throws a `KeyError` upon iteration.
- **Mitigation**: Verified that the supervisor loop (`Connector.run`) wraps all calls to `normalize` in a `try-except` block. Any normalization error is captured, logged to debug, and routed to the `DeadLetterQueue` (DLQ) without halting the connector process.

### [Low] Challenge 2: Float Division Under Extreme Price Inputs
- **Assumption challenged**: Prices will always be finite positive floats within standard bounds.
- **Attack scenario**: A flash loan or pool depletion results in an extremely low price (e.g., `1e-323`) or zero.
- **Blast radius**: Potential division by zero errors or floating-point overflow (`inf`/`nan`) causing schema validation crashes.
- **Mitigation**: 
  - If the price is exactly `0.0` or negative, the normalizer checks `if price <= 0:` and returns early.
  - If the price is extremely small (e.g., `1e-300`), the division `reserve_token1 / price` evaluates to a very large float or `inf` (which msgspec encodes to `null`). The minimum orderbook size check `max(sz, 0.0001)` guarantees bounds sanity.

### [Low] Challenge 3: RPC Call Interruptions and Rate Limiting
- **Assumption challenged**: Web3 RPC endpoints are permanently healthy and responsive.
- **Attack scenario**: Network interruptions, rate-limiting HTTP 429s, or timeout events on `get_logs` or contract calls.
- **Blast radius**: Unhandled Web3 connection exceptions crashing the transport loop.
- **Mitigation**: 
  - `get_logs` failures are isolated using a dedicated try-except block in `BaseOnchainTransport._poll_loop`. If `get_logs` fails, the connector still outputs state updates (with empty swaps).
  - General contract/block failures are caught at the outer polling layer, logging the error and sleeping before retrying.

## Stress Test Results

- **Extreme Price (1e300)** → BookTicker and BookSnapshot generated successfully → **PASS**
- **Extreme Price (1e-300)** → Division succeeds without exceptions → **PASS**
- **Extreme Price near float underflow (1e-323)** → Division results in `inf` and encodes gracefully → **PASS**
- **Zero Price (0.0)** → Normalizer returns early, yielding 0 records → **PASS**
- **Negative Price (-12.34)** → Normalizer returns early, yielding 0 records → **PASS**
- **Large Swap Count (5000)** → Correctly processes all 5000 trades in a single update → **PASS**
- **Very Small Amount (1e-18)** → Precision preserved in Trade, capped at 0.0001 in orderbook → **PASS**
- **Missing "price" in state** → Raises `KeyError` gracefully (DLQ routed) → **PASS**
- **Missing "block" in root** → Raises `KeyError` gracefully (DLQ routed) → **PASS**
- **Missing swap fields** → Raises `KeyError` gracefully (DLQ routed) → **PASS**
- **Simulated RPC Get Block Failure** → Transport logs error and sleeps, no crash → **PASS**
- **Simulated RPC Get Logs Failure** → Transport logs error and yields state with empty swaps, no crash → **PASS**

## Unchallenged Areas

- **Operating System OOM / Hardware Failures** — out of scope for software connector unit verification.
- **Network Bandwidth Saturation** — out of scope.
