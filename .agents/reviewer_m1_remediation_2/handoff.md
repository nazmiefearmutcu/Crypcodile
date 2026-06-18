# Handoff Report - Milestone 1 Review

## 1. Observation
- Modified files list from `git diff --stat`:
  ```
  src/crypcodile/api_server.py                       | 149 ++++-
  src/crypcodile/exchanges/base_onchain/connector.py | 702 +++++++++++++--------
  src/crypcodile/mcp_server.py                       | 138 +++-
  ```
- Pytest command execution: `uv run pytest tests/e2e/test_tier1_features.py` failed with 5 failures:
  1. `test_f3_pagination_boundaries`
     ```
     FAILED tests/e2e/test_tier1_features.py::test_f3_pagination_boundaries - AssertionError: assert 3 == 1
     ```
  2. `test_f5_x402_verify_valid_payment`
     ```
     FAILED tests/e2e/test_tier1_features.py::test_f5_x402_verify_valid_payment - AssertionError: assert 400 == 200
     ```
  3. `test_f5_x402_receipt_lookup_fail`
     ```
     FAILED tests/e2e/test_tier1_features.py::test_f5_x402_receipt_lookup_fail - AssertionError: assert 500 == 400
     ```
  4. `test_f6_custom_aerodrome_stable`
     ```
     FAILED tests/e2e/test_tier1_features.py::test_f6_custom_aerodrome_stable - AssertionError: assert 0 > 0
     ```
  5. `test_f2_mcp_custom_symbol_lookup`
     ```
     FAILED tests/e2e/test_tier1_features.py::test_f2_mcp_custom_symbol_lookup - AssertionError: assert 'error' not in ...
     ```

- Log range checking logic in `src/crypcodile/exchanges/base_onchain/connector.py` (lines 391-410):
  ```python
  start_block = self._last_blocks[sym] + 1
  end_block = current_block
  logs = []
  if start_block > end_block:
      # Calls get_logs with start_block > end_block (invalid range)
  else:
      # Does chunking when start_block <= end_block
  ```

- USDC topic comparison logic in `src/crypcodile/api_server.py` (lines 138-140):
  ```python
  t0 = topics[0].hex().lower() if isinstance(topics[0], bytes) else str(topics[0]).lower()
  if t0 != transfer_topic:
  ```
  Where `transfer_topic` is:
  ```python
  transfer_topic = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
  ```

- Exception handling in `src/crypcodile/api_server.py` (lines 102-108):
  ```python
  try:
      receipt = await w3.eth.get_transaction_receipt(tx_hash)
  except Exception as e:
      raise HTTPException(
          status_code=500,
          detail=f"Failed querying transaction receipt: {e}"
      ) from e
  ```

## 2. Logic Chain
- **Swap Logs Range Check**: Because `if start_block > end_block:` is used as the condition to query logs directly (instead of skipping or handling it), the connector executes an invalid `eth_getLogs` call every time it polls when no new blocks are mined. This causes 3 calls instead of 1 in `test_f3_pagination_boundaries`, causing the assertion `assert len(logs_t1) == 1` to fail.
- **USDC Log Topic Comparison**: Since `topics[0]` is a `HexBytes` object (bytes), `.hex()` returns a hex representation without a `0x` prefix. The comparison `t0 != transfer_topic` will always evaluate to `True` because `transfer_topic` has a `"0x"` prefix (e.g. `"ddf252..." != "0xddf252..."`). This causes the log entry to be skipped, making validation fail with HTTP 400.
- **Transaction Receipt Lookup Failure Status**: Nonexistent transactions query throws `TransactionNotFound` from Web3.py. The generic `except Exception` block intercepts this and translates it to HTTP 500 instead of allowing a specific HTTP 400 client error response, which causes `test_f5_x402_receipt_lookup_fail` to fail.
- **MCP Subprocess Pool Configuration**: `test_f2_mcp_custom_symbol_lookup` modifies `connector.POOL_SPECS` in the parent process, but the MCP server runs as a separate subprocess spawned via `subprocess.Popen` and does not inherit the modified `POOL_SPECS` memory dict, causing tool calls for custom symbols to fail.

## 3. Caveats
- Checked and confirmed that the provider `disconnect` calls are correctly invoked in all `finally` blocks, successfully preventing socket leaks. No caveats identified.

## 4. Conclusion
The changes made for Milestone 1 fail to meet correctness and verification standards due to logic errors, topic prefix mismatch bugs, and incorrect HTTP exception mappings. A verdict of `REQUEST_CHANGES` is issued.

## 5. Verification Method
- Execute the test suite isolates:
  `uv run pytest tests/e2e/test_tier1_features.py`
- Invalidation condition: The test suite should pass cleanly without any failures or warnings once the identified logic, prefix, and status code bugs are fixed.
