# Handoff Report - challenger_m1_remediation_6

## 1. Observation

- **Replay Attack / Transaction Reuse**: In `src/crypcodile/api_server.py`, the gating endpoint `/api/v1/market-data` checks if the `payment_id` exists in `PAYMENTS_DB`. When status is not `"paid"`, it validates the transaction hash `tx_hash` on-chain. However, there is no check to verify if the `tx_hash` has already been used by *another* `payment_id`.
- **Coroutine TypeError**: In `src/crypcodile/exchanges/base_onchain/connector.py` (lines 239-243), the block number helper:
  ```python
  async def _get_block_number(self, w3: Any) -> int:
      async def get_bn():
          val = w3.eth.block_number
          return val
      return await self._call_with_retry(get_bn)
  ```
  returns the unawaited coroutine of `w3.eth.block_number` (since `get_bn` is not awaiting it). Consequently, `current_block` is assigned a coroutine object, which raises `TypeError: unsupported operand type(s) for -: 'coroutine' and 'int'` on line 403:
  ```python
  self._last_blocks[sym] = current_block - 20
  ```
  This was confirmed via `tests/exchanges/base_onchain/test_connector.py` failing with this exact error.
- **Cursor Rollback Bug**: In `src/crypcodile/exchanges/base_onchain/connector.py` (line 571), the cursor `self._last_blocks[sym]` is updated to `current_block` regardless of whether `current_block` is greater than the previous cursor. If block number lag occurs (e.g. block reports [1000, 990, 1010]), the cursor is rolled back to 990, leading to duplicate log range queries [991, 1010] on the next iteration.
- **Dynamic Reload Failure**: The IPC file `POOL_SPECS` and `TOKENS` are only loaded once when the connector module is imported. The connector's main `_poll_loop` never re-reads the IPC file from disk, so dynamic updates are ignored.
- **Race Condition in IPC Dict**: `IPCDict` writes to the shared IPC file without file locks, creating risk of lost updates or file corruption.

## 2. Logic Chain

- **Replay Attack**: By submitting `payment_signature` with a new `payment_id` but the `tx_hash` of a previously confirmed transaction, the endpoint accepts it because it calls `w3.eth.get_transaction_receipt(tx_hash)` which returns a valid transfer receipt. Since there is no mapping of `tx_hash -> payment_id` or unique tx registry, the transaction succeeds a second time, marking the new `payment_id` as `paid`.
- **Block Number Coroutine**: When `w3.eth.block_number` returns an awaitable/coroutine, `get_bn` returns the coroutine itself without awaiting it. `_call_with_retry` awaits `get_bn()`, which resolves to the coroutine. `_get_block_number` returns `await self._call_with_retry(get_bn)`, which resolves to the coroutine. When the loop does `current_block - 20`, it attempts subtraction on a coroutine, raising `TypeError`.
- **Block Lag Rollback**: If `current_block` is less than `self._last_blocks[sym]`, the condition `start_block <= end_block` is false, so log retrieval is skipped. However, `self._last_blocks[sym] = current_block` is executed unconditionally, which lowers the cursor. On recovery, `start_block` is derived from the lowered cursor, resulting in overlapping logs being retrieved twice.

## 3. Caveats

- We assumed that `w3.eth.block_number` returns a coroutine. In `web3.py` v6+, `block_number` is indeed an awaitable property.
- We did not evaluate concurrent locking under multi-threaded high-rate API gateway environments.

## 4. Conclusion

The refactored AsyncWeb3 integration contains multiple critical and high-severity issues:
1. **Critical**: Transaction Replay/Double-Spend allows bypassing API gate micropayments.
2. **High**: Coroutine retrieval bugs in `_get_block_number` break connector loop logic on real async providers.
3. **High**: Block lag cursor rollback causes duplicate log processing.
4. **Medium**: Lack of dynamic reload in `_poll_loop` defeats dynamic pool configuration.
5. **Medium**: Lack of locking in `IPCDict` writes risks configuration file corruption.

Remediation must address these logic flaws before Milestone 1 can be considered complete.

## 5. Verification Method

To verify these findings, run the dedicated stress tests:
```bash
uv run pytest tests/exchanges/base_onchain/test_challenger_remediation_6.py -vv -s
uv run pytest tests/exchanges/base_onchain/test_empirical_bugs.py -vv -s
```
Both of these test suites verify the replay attack, coroutine type mismatches, block lag rollback, and dynamic reload failures.
