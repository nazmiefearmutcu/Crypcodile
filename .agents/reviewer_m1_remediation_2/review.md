# Review Report - Milestone 1: Native AsyncWeb3 Refactoring

## Review Summary

**Verdict**: REQUEST_CHANGES

The implementation of Milestone 1 contains several critical correctness, logic, and exception-handling bugs that cause the test suite to fail (5 out of 30 tests in `test_tier1_features.py` failed). In particular, the RPC log-polling logic is inverted, USDC payment topic verification fails due to missing prefix handling, and transaction lookup failures return HTTP 500 instead of HTTP 400.

---

## Findings

### [Critical] Finding 1: Inverted Range Check in Swap Logs Polling
- **What**: The condition checking if new blocks have been mined to fetch logs is inverted.
- **Where**: `src/crypcodile/exchanges/base_onchain/connector.py`, line 395
- **Why**: The code uses:
  ```python
  if start_block > end_block:
      # Calls get_logs with start_block > end_block (invalid range)
  else:
      # Does chunking when start_block <= end_block
  ```
  This causes the transport to query logs using an invalid range on every single polling loop iteration when no new blocks are mined. This floods the RPC node with useless/invalid requests and causes `test_f3_pagination_boundaries` to fail (expecting 1 call, but getting 3).
- **Suggestion**: Change the check to only fetch logs when `start_block <= end_block`. If `start_block > end_block`, skip fetching logs (or just pass).

### [Critical] Finding 2: Missing "0x" Prefix in USDC Transfer Topic Comparison
- **What**: The log topic validation compares a hex string without `0x` to a string with `0x`.
- **Where**: `src/crypcodile/api_server.py`, line 139
- **Why**: `topics[0]` is returned as a `HexBytes` object by Web3.py. Calling `.hex()` on it returns a hex string without the `0x` prefix (e.g., `"ddf252..."`), which is then compared to `transfer_topic` (which contains `"0xddf252..."`). This comparison always fails, causing USDC payment verification to fail with HTTP 400.
- **Suggestion**: Ensure hex comparisons are prefix-aware or strip/normalize prefixes (e.g., compare `t0.removeprefix("0x") == transfer_topic.removeprefix("0x")`).

### [Major] Finding 3: Incorrect HTTP Status Code on Transaction Lookup Failure
- **What**: Lookup failures for nonexistent transaction hashes return HTTP 500 instead of HTTP 400.
- **Where**: `src/crypcodile/api_server.py`, line 102
- **Why**: Web3.py raises `web3.exceptions.TransactionNotFound` when `get_transaction_receipt` is queried for a nonexistent tx. The server catches this under `except Exception as e:` and raises HTTP 500, whereas it should return HTTP 400 (Client Error) as tested in `test_f5_x402_receipt_lookup_fail`.
- **Suggestion**: Catch `web3.exceptions.TransactionNotFound` specifically and raise HTTP 400, or check the exception type and map it to HTTP 400.

### [Major] Finding 4: In-Memory Dynamic Registry is Inaccessible to Subprocess
- **What**: MCP server custom symbol lookup fails because the MCP server runs in a separate subprocess.
- **Where**: `tests/e2e/test_tier1_features.py`, line 972 (`test_f2_mcp_custom_symbol_lookup`)
- **Why**: The test dynamically updates `connector.POOL_SPECS["CUSTOM_MCP-USDC"]` in the pytest process, but the MCP server subprocess is spawned beforehand and cannot see these changes, leading to a "Symbol CUSTOM_MCP-USDC not supported" error.
- **Suggestion**: Introduce a configuration file or environment variable mechanism so that the MCP server subprocess can load custom pool configurations at startup, or run the MCP server in-process if supported.

---

## Verified Claims

- **Context Manager Bug in MCP Server Fixed** → verified via manual review of `mcp_server.py` → **PASS** (wrapped in `try...finally` with `await w3.provider.disconnect()`).
- **Context Manager Bug in API Server Fixed** → verified via manual review of `api_server.py` → **PASS** (wrapped in `try...finally` with `await w3.provider.disconnect()`).
- **Connection leaks prevented in connector loop** → verified via manual review of `connector.py` → **PASS** (disconnect in `finally` block of `_poll_loop`).

---

## Coverage Gaps

- **Robustness under Network Partition** — risk level: **medium** — The `retry_rpc` function has basic retry logic, but does not gracefully disconnect/reconnect the underlying session if the network goes down permanently. Recommendation: Investigate adding session recreation on failure.

---

## Unverified Items

- None. All items in the diff and patch have been reviewed.
