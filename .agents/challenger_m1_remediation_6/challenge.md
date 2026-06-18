# Milestone 1: Native AsyncWeb3 Refactoring - Challenger Report

**Overall risk assessment**: CRITICAL

This report outlines key vulnerabilities, logic gaps, and bugs discovered during the stress and adversarial review of Milestone 1 changes.

---

## Challenges

### [Critical] Challenge 1: Transaction Hash Replay / Double Spend on Payment Verification
- **Assumption challenged**: That the API gate checks if a payment signature's `tx_hash` is unique and represents a new transfer.
- **Attack scenario**: The payment validation endpoint `/api/v1/market-data` validates the transfer on-chain by checking `tx_hash` and matches it against `RECIPIENT_WALLET` and `1000` micro-USDC. However, it does not keep a ledger of already used `tx_hash`es. A user can get a new `payment_id` and submit the same `tx_hash` from a past transaction. The server will verify the tx on-chain again, see it is successful, and return the gated data.
- **Blast Radius**: Critical. Completely bypasses the pay-per-request monetization system. A single on-chain payment allows infinite free API requests.
- **Mitigation**: Maintain a registry of already processed `tx_hash`es in a persistent database (or mapping the `tx_hash` to the validated payment record) and reject any transaction hash that has already been validated.

### [High] Challenge 2: Unawaited `w3.eth.block_number` Coroutine causing TypeError
- **Assumption challenged**: That `w3.eth.block_number` is resolved to an integer before arithmetic operations.
- **Attack scenario**: In `connector.py`, `_get_block_number` is implemented as:
  ```python
  async def _get_block_number(self, w3: Any) -> int:
      async def get_bn():
          val = w3.eth.block_number
          return val
      return await self._call_with_retry(get_bn)
  ```
  Since `w3.eth.block_number` returns a coroutine in AsyncWeb3, and `get_bn` does not await `val`, it returns the coroutine object itself. The `_call_with_retry` method awaits `get_bn()` which resolves to the `block_number` coroutine object without awaiting it. As a consequence, `current_block` is assigned a coroutine, leading to:
  `TypeError: unsupported operand type(s) for -: 'coroutine' and 'int'` on `current_block - 20`.
- **Blast Radius**: High. Under real async providers or custom mocks returning awaitables/coroutines, the connector gets stuck or crashes with exceptions, preventing ingestion.
- **Mitigation**: Change `get_bn` to correctly await the block number:
  ```python
  async def get_bn():
      import inspect
      val = w3.eth.block_number
      if inspect.isawaitable(val):
          return await val
      return val
  ```

### [High] Challenge 3: Cursor Rollback and Duplicate Log Queries on Block Lag
- **Assumption challenged**: That block number reports from RPC nodes are strictly monotonic and always advance.
- **Attack scenario**: If a lagging RPC node returns a block number lower than the last processed block (`current_block < self._last_blocks[sym]`), `start_block > end_block` occurs, so log query is skipped. However, the connector updates the cursor to `current_block`:
  ```python
  self._last_blocks[sym] = current_block
  ```
  This rolls back the cursor to the lagging block. On the next iteration, once the block number recovers, it queries the logs starting from the rolled-back block, resulting in querying and pushing duplicate swap events.
- **Blast Radius**: High. Results in duplicate trade records being pushed to the queue and stored in the DuckDB Parquet lake, corrupting historical data analytics.
- **Mitigation**: Only update the cursor to `current_block` if `current_block >= self._last_blocks[sym]`.

### [Medium] Challenge 4: IPC Pool Configuration is Not Dynamically Reloaded
- **Assumption challenged**: That the connector dynamically reconfigures its polled pools via the IPC file during execution.
- **Attack scenario**: The connector only loads custom pools from the IPC file at module import time (`_load_ipc()`). The main loop (`_poll_loop`) never reloads `POOL_SPECS` or `TOKENS` from the IPC file. If an external client writes a new custom pool to the IPC file, the running connector will never discover it.
- **Blast Radius**: Medium. Dynamic pool updates are not truly dynamic without restarting the connector daemon.
- **Mitigation**: Periodic reload of `POOL_SPECS` from the IPC file inside `_poll_loop` (e.g. every loop or every N loops).

### [Medium] Challenge 5: Lack of File Locking in `IPCDict` writes
- **Assumption challenged**: That concurrent writes to the IPC file are safe.
- **Attack scenario**: In `IPCDict._write_ipc`, the process reads, modifies, and replaces the `IPC_FILE` without any operating system file locking (`flock`). If two processes (e.g. API Server and a management CLI) write custom pools/tokens concurrently, they will overwrite each other's changes, leading to lost updates or JSON corruption.
- **Blast Radius**: Medium. Data corruption in dynamic configurations.
- **Mitigation**: Use a file lock (e.g. `fcntl.flock` on Unix) before reading and writing to `IPC_FILE`.

---

## Stress Test Results

The following tests were executed in `tests/exchanges/base_onchain/test_challenger_remediation_6.py` and `tests/exchanges/base_onchain/test_empirical_bugs.py` to confirm the hypotheses empirically:

| Test Case | Scenario | Expected Behavior | Actual Behavior | Result |
|---|---|---|---|---|
| `test_replay_attack_vulnerability` | Submit same `tx_hash` for two different `payment_id`s | Second request rejected | Second request accepted (status paid) | **FAIL (Vulnerable)** |
| `test_dynamic_ipc_reload_failure` | Write custom pool to IPC file during connector execution | Connector polls the new pool | Connector ignores the new pool | **FAIL (Missing Feature)** |
| `test_duplicate_log_query_bug` | Block lag sequence [1000, 990, 1010] | Only new blocks queried | Blocks 991-1000 queried twice | **FAIL (Bug)** |
| `test_api_server_robustness_malformed_receipts` | Validate receipt with missing logs fields | Graceful HTTP 400 error | Returns HTTP 400 without crash | **PASS** |

---

## Recommendations

1. **Implement Replay Protection**:
   Add a check in `api_server.py` to verify that `tx_hash` is not already used:
   ```python
   # Verify if tx_hash has already been used by another payment
   for paid_record in PAYMENTS_DB.values():
       if paid_record.get("tx_hash") == tx_hash and paid_record.get("status") == "paid":
           raise HTTPException(status_code=400, detail="Transaction hash already processed.")
   ```
2. **Fix Coroutine in `_get_block_number`**:
   Correctly inspect and await `w3.eth.block_number` if it is awaitable inside the `_get_block_number` inner function.
3. **Monotonic Cursor Update**:
   Update `self._last_blocks[sym]` only if the new block is greater than the previous cursor:
   ```python
   self._last_blocks[sym] = max(self._last_blocks[sym], current_block)
   ```
4. **Periodic IPC File Reload**:
   Invoke `_load_ipc()` inside the polling loop to reload dynamic pool specifications dynamically.
5. **IPC File Locking**:
   Add file locking to `IPCDict._write_ipc` and `_load_ipc` to prevent race conditions.
