# Handoff Report - explorer_m4_2

## 1. Observation
We observed the following inside the codebase of `Crypcodile`:
* **AsyncWeb3 Lifecycle**: In `src/crypcodile/api_server.py:180-182`, `w3` is instantiated on every single call to `get_market_data`:
  ```python
  rpc_url = os.getenv("BASE_RPC_URL", "https://base-rpc.publicnode.com")
  w3 = AsyncWeb3(AsyncHTTPProvider(rpc_url))
  ```
  It is manually closed inside the `finally:` block (lines 354-363) instead of using the context manager or preserving it across requests.
* **Silent Cryptographic Verification Bypass**: In `src/crypcodile/api_server.py:198-208`:
  ```python
  if not is_valid_format:
      signer_address = None
  else:
      try:
          message = encode_defunct(text=pid)
          signer_address = Account.recover_message(message, signature=signature)
      except Exception as e:
          raise HTTPException(...)
  ```
  And then the sender validation is guarded by:
  ```python
  if signer_address:
      try:
          tx_details = await w3.eth.get_transaction(tx_hash)
          ...
  ```
  This skips verification when `is_valid_format` is False (i.e. invalid signature format).
* **Missing Retries on Initial RPC Call**: In `src/crypcodile/api_server.py:213`, `w3.eth.get_transaction(tx_hash)` is called before the transaction receipt retry loop and is not inside any retry logic. If the transaction has just been broadcasted and the node hasn't seen it yet, this fails immediately.
* **Database Truncation Race Condition**: In `src/crypcodile/api_server.py:67-73`, the save database function opens the file with `"w"` (truncating it to 0 bytes) before calling `flock`:
  ```python
  with open(payments_file, "w") as f:
      try:
          fcntl.flock(f.fileno(), fcntl.LOCK_EX)
  ```
* **Test Coverage Gap**: The test cases in `tests/exchanges/base_onchain/test_servers.py` invoke `simulate_payment` first, which marks the payment status as `"paid"`. In `api_server.py:175-178`, when the status is `"paid"`, the server skips the entire on-chain verification path.

## 2. Logic Chain
1. A new connection session (`AsyncHTTPProvider`) per request without connection pooling will cause TCP connection churn and socket leaks under load.
2. If `signer_address` is set to `None` on invalid formats and the sender check is guarded by `if signer_address:`, the server silently bypasses the cryptographic check. A malicious actor could supply any valid transaction hash with an invalid signature string (like `"0x00"`) and claim the transaction as theirs.
3. If a transaction is fresh, `w3.eth.get_transaction` will fail if the node hasn't mempooled the transaction. Because this first call lacks retry logic, the request fails immediately.
4. Opening the payments file in `"w"` mode truncates the file immediately. In multi-process environments, this results in data corruption/loss.
5. Because all tests use `/simulate-payment`, the actual on-chain verification branch is never executed, which explains why the signature bypass and truncation bugs were not caught by pytest.

## 3. Caveats
* The investigation was purely read-only and static; we did not run the API server under concurrent traffic.
* We assume the system is expected to support multiple worker processes, requiring atomic writes rather than process-local locks.

## 4. Conclusion
The codebase is currently not production-ready. The payment validation logic contains a critical signature bypass vulnerability, data safety risks on file writes, and fragile RPC integrations (no fallback URLs, missing retries on `get_transaction` and `get_block`). Implementing a singleton `AsyncWeb3` instance, enforcing cryptographic checks, adding an RPC retry/failover wrapper, writing files atomically using `os.replace`, and adding unit tests for the on-chain path are necessary to achieve production readiness.

## 5. Verification Method
1. **Source Inspection**: Read `src/crypcodile/api_server.py` at line 198 (signature parsing) and line 67 (file writing) to confirm the observed behaviors.
2. **Test Inspection**: Verify that `tests/exchanges/base_onchain/test_servers.py` does not mock `AsyncWeb3` to test the actual on-chain validation logic.
3. **Project Test Command**: Run the tests using:
   `uv run pytest tests/exchanges/base_onchain/test_servers.py`
