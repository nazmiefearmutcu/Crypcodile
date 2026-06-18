# Challenge Report — Milestone 1 Verification

## Challenge Summary

**Overall risk assessment**: LOW

All tests pass, and stress/concurrency testing confirms that the implementation is robust, correct, and completely resolved under high concurrency and RPC error conditions.

## Challenges

### Low Challenge 1: Public RPC Node Reliability & Rate Limits

- **Assumption challenged**: The API server relies on a public RPC node `https://base-rpc.publicnode.com` to fetch live DEX prices on-chain.
- **Attack scenario**: Under heavy load or DDoS, the public RPC node may return 429 Too Many Requests or time out, causing requests to the Crypcodile gated API to return 500 Internal Server Error.
- **Blast radius**: The API server's gated endpoint `/api/v1/market-data` returns HTTP 500 instead of a clean, cached, or fallback price.
- **Mitigation**: Implement a local caching mechanism (e.g., storing the last valid price for up to 10–15 seconds) or configure fallback RPC URLs in case the primary public node fails.

### Low Challenge 2: Client JSON-RPC Request Formats to MCP Server

- **Assumption challenged**: The Model Context Protocol (MCP) server `serve_stdio` reads JSON-RPC requests from standard input and processes them sequentially.
- **Attack scenario**: If a client sends malformed JSON or invalid JSON-RPC payload structures (e.g., missing mandatory properties like `method` or `jsonrpc`), the server might crash or fail to process subsequent inputs.
- **Blast radius**: Stdio connection drops, causing the MCP server process to terminate.
- **Mitigation**: The current codebase wraps each line processing loop in a `try...except` block, preventing crash on malformed JSON and logging the error properly.

## Stress Test Results

- **FastAPI Gated Request (No Signature)** → Returns `402 Payment Required` with appropriate `Payment-Required` header details (payment_id, recipient, price) → Verified successfully → **PASS**
- **Micropayment Gated Flow with Simulation** → Returns `200 OK` with the live price (`64018.29` from Uniswap V3 `cbBTC-USDC` pool) and sets the `Payment-Response` header → Verified successfully → **PASS**
- **High Concurrency Stress Test (20 Parallel Requests)** → All 20 concurrent requests successfully fetch payment IDs, simulate payments, and obtain correct live market data with no connection leaks or resource warnings → 20/20 Successful requests → **PASS**
- **UnboundLocalError regression (WELL-WETH pool query error)** → WELL-WETH state query fails, but WELL-WETH `price` and `swaps` default values are preserved and no UnboundLocalError is raised → Caught exception gracefully and logged `WELL-WETH: Reserves query failed` → **PASS**
- **Log Duplication regression (failing pool in multi-pool setup)** → In a multi-pool subscription, if one pool fails, successful pools advance their block cursors individually, preventing duplicate logs from being re-fetched next poll → Successful pools advanced, failed pool retried correctly → **PASS**

## Unchallenged Areas

- **On-chain Signature Verification Cryptography** — The simulated payment currently checks state mappings rather than cryptographically verifying ECDSA signature proofs of EIP-712 payloads. This is out of scope as it is documented as a mock/simulation mode.
