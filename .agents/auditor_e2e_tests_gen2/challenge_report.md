# Adversarial Challenge Report

## Challenge Summary

**Overall risk assessment**: LOW

All implementations are authentic, cleanly separated, and cover standard error conditions (HTTP 429, HTTP 500/503, block re-orgs, USDC value checking). The mock server is fully modular and controllable via REST.

## Challenges

### [Low] Challenge 1: Lack of RPC Error Graceful Recovery in api_server.py
- **Assumption challenged**: The Base mainnet RPC node is always online and responsive during payment verification.
- **Attack scenario**: If the RPC node is experiencing downtime or severe latency when an agent attempts to submit a payment receipt, the `api_server` raises a `500 Internal Server Error` due to a connection error on `get_transaction_receipt`.
- **Blast radius**: The customer gets a 500 error instead of a retry instruction or custom payment pending status, requiring them to resubmit their query.
- **Mitigation**: Implement a retry policy or double-check with secondary public endpoints (e.g. Ankr or LlamaNode) when the main RPC fails.

### [Low] Challenge 2: Decimals Overrides in custom pools
- **Assumption challenged**: Custom pools added via initialization configuration always provide correct decimals.
- **Attack scenario**: If a custom pool configurations lists decimals0 or decimals1 as 0 or missing, the synthetic book depth calculations may overflow or division by zero checks could fail or fallback to a default that produces unrealistic sizes.
- **Blast radius**: `normalize_onchain_update` could yield snapshots with incorrect prices or unrealistic bid/ask sizes.
- **Mitigation**: Add validation check at connector startup to ensure `decimals0` and `decimals1` are present and valid (> 0).

## Stress Test Results

- **Re-org Scenario** → Transport detects block height re-org (i.e. `last_block > current_block`), resets last block pointer to `current_block - 20` and repaginates → Passes cleanly (implemented and covered in `test_t3_reorg_plus_pagination`).
- **HTTP 429 Rate Limiting** → Mock RPC responds with HTTP 429, client transport middleware retries using exponential backoff → Passes cleanly (covered in `test_t3_pagination_plus_rate_limiting` and `test_t3_custom_symbol_plus_retries`).
- **Incorrect USDC Transfer Value** → Transaction receipt contains transfer event but with incorrect transfer amount (e.g. 500 units instead of 1000) → `api_server` detects incorrect amount and rejects with 400 error → Passes cleanly (covered in `test_tier2_boundaries`).

## Unchallenged Areas

- **EIP-712 Signature Validation** — The exact verification of agent-level signatures (the `signature` field) is bypassed or mock-validated, as it requires a full private/public key registry not currently fully specified in the project scope. Bypassing this is acceptable under Development mode requirements.
