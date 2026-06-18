# Handoff Report

## 1. Observation

- **Observation 1 (Connector Loop Structure)**: In `src/crypcodile/exchanges/base_onchain/connector.py`, line 572-573 ends the `try` block that wraps the pool queries. Lines 584-586 are outside of the `try-except` block and reference `slot0` and `liquidity`:
  ```python
  572:                         except Exception as e:
  573:                             log.error(f"base_onchain: Error polling pool data for {sym}: {e}")
  574:      
  575:                         # C. Push state update to queue
  ...
  584:                         if spec["type"] == "uniswap_v3":
  585:                             state_payload["tick"] = int(slot0[1])
  586:                             state_payload["liquidity"] = int(liquidity)
  ```
- **Observation 2 (API Payment Verification)**: In `src/crypcodile/api_server.py`, lines 94-196 verify the transaction signature. The transaction receipt status and logs are checked, but there is no mechanism to record or reject transaction hashes that have been previously processed.
  ```python
  194:             record["status"] = "paid"
  195:             record["tx_hash"] = tx_hash
  ```
- **Observation 3 (IPC Write)**: In `src/crypcodile/exchanges/base_onchain/connector.py`, lines 48-65 perform the dynamic pool IPC file write:
  ```python
  48:     def _write_ipc(self):
  49:         try:
  50:             data = {}
  51:             if os.path.exists(IPC_FILE):
  52:                 try:
  53:                     with open(IPC_FILE, "r") as f:
  54:                         content = f.read().strip()
  ...
  60:             temp_file = IPC_FILE + ".tmp"
  61:             with open(temp_file, "w") as f:
  62:                 json.dump(data, f)
  63:             os.replace(temp_file, IPC_FILE)
  ```
- **Observation 4 (Empirical Test Runs)**: Running `uv run pytest tests/exchanges/base_onchain/test_empirical_bugs.py` succeeded with:
  ```
  2 passed, 1 warning in 0.35s
  ```
  This proves the vulnerabilities described in `test_empirical_bugs.py` exist in the code under test.

---

## 2. Logic Chain

1. **Connector Loop Crash**:
   - Step A: If `slot0` or `liquidity` calls fail, they throw an exception.
   - Step B: The exception is caught on line 572, and the `slot0` and `liquidity` local variables are never initialized (they are unbound).
   - Step C: Since Step C (lines 584-586) is outside the inner `try-except` block, the interpreter attempts to execute `int(slot0[1])` and raises `UnboundLocalError`.
   - Step D: This unbound error escapes the inner loop, propagating to the outer loop's `try-except` block on lines 606-607, thereby aborting the iteration for any remaining pools.
2. **Double-Spend Replay**:
   - Step A: When a user calls `/api/v1/market-data` with a unique `payment_id` and a previously used `tx_hash`, the database `PAYMENTS_DB` registers a new pending record because the `payment_id` is unique.
   - Step B: The verification block queries the `tx_hash` on-chain. Since the transaction is a valid USDC transaction to the recipient wallet, it returns a successful receipt.
   - Step C: The server does not check if that `tx_hash` has already been marked as `paid` under another payment ID, so it marks the current `payment_id` as `paid` and allows the user to access the gated data.
   - Conclusion: This enables complete double-spend/replay attacks on the micropayment gateway.
3. **IPC Race Condition**:
   - Step A: The write process reads the shared `IPC_FILE` file, updates the dictionary locally, and writes it back.
   - Step B: Because there is no concurrency control (such as file locking) during the read-modify-write phase, two processes writing to the IPC file concurrently can overwrite each other's edits, resulting in lost configuration.

---

## 3. Caveats

- We did not write a concurrent client script to trigger the IPC race condition because the two critical vulnerabilities (UnboundLocalError loop crash and transaction replay double-spend) are of significantly higher priority and were verified empirically.
- We did not modify any implementation code, strictly adhering to the review-only constraint.

---

## 4. Conclusion

Milestone 1 satisfies basic AsyncWeb3 requirements and provider teardown. However, it is vulnerable to two high-severity flaws:
1. **Transaction Replay (Double-Spend)** in the payment gateway (`api_server.py`), allowing a user to reuse the same transaction hash indefinitely to unlock data.
2. **UnboundLocalError** in the base_onchain connector (`connector.py`), causing the entire polling iteration to crash and skip polling remaining pools if any Uniswap V3 query fails.

---

## 5. Verification Method

To verify these vulnerabilities independently:
1. Run the empirical bug tests using:
   ```bash
   uv run pytest tests/exchanges/base_onchain/test_empirical_bugs.py
   ```
2. Verify that both tests pass, which demonstrates the successful replication of the loop crash and double-spend replay bugs.
3. Read the code in `src/crypcodile/api_server.py` and `src/crypcodile/exchanges/base_onchain/connector.py` to inspect the logic flaws.
