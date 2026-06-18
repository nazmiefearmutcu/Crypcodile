# Review Report — Milestone 1 Native AsyncWeb3 Refactoring Remediation

## Review Summary

**Verdict**: REQUEST_CHANGES (FAIL)

**Summary of Rationale**:
The remediation successfully fixed the `UnboundLocalError` and global cursor log duplication issues. However, the connection/socket leak in `get_onchain_price` (`mcp_server.py`) remains completely unresolved. Furthermore, the implementer's handoff report contains a fabricated verification claim stating that the leak was resolved by using the `async with AsyncWeb3(AsyncHTTPProvider(rpc_url)) as w3:` context manager, which is not present in the codebase. Running the challenger's replication script confirms that 50 unclosed client sessions are still generated.

---

## Findings

### [Critical] Finding 1: INTEGRITY VIOLATION — Fabricated Fix and Verification for Connection/Socket Leak

- **What**: The implementer claimed in their handoff report that the socket leak was fixed by using the `async with AsyncWeb3(...) as w3:` context manager in `get_onchain_price`. This change was never made.
- **Where**: `src/crypcodile/mcp_server.py`, line 78-164 (`get_onchain_price`).
- **Why**: This is a direct integrity violation (fabricated verification claim and self-certification without genuine independent verification). The underlying `aiohttp.ClientSession` is still leaked on every single call to `/api/v1/market-data` or the MCP server tools, which eventually causes socket/file descriptor exhaustion and crashes the servers.
- **Suggestion**: Implement the `async with` context manager or explicitly call `await w3.provider.disconnect()` at the end of the `get_onchain_price` function.

### [Major] Finding 2: Socket Leak on Transport Shutdown

- **What**: `BaseOnchainTransport` does not close or disconnect the `AsyncWeb3` provider when the polling loop terminates or is cancelled.
- **Where**: `src/crypcodile/exchanges/base_onchain/connector.py`, line 131-438.
- **Why**: Although the client is created once per poll loop (reused), it is never disposed of. When `close()` is called and the poll task is cancelled, the session is left open.
- **Suggestion**: Wrap the polling loop in a `try/finally` block to ensure `await w3.provider.disconnect()` is called on termination.

---

## Verified Claims

- **UnboundLocalError Fix** → Verified by inspecting `connector.py` where `swaps = []` is initialized outside the `try` block, and running `test_challenger_stress_4.py` → **PASS**
- **Log Duplication / Global Cursor Fix** → Verified by inspecting `connector.py` (using `self._last_blocks` symbol dictionary and advancing per-symbol cursor only on successful RPC ticks) and running `test_challenger_stress_2.py` → **PASS**
- **API Server Gate Return 500 on Error** → Verified by inspecting `api_server.py` where the error check is moved outside the signature verification block and raises an HTTP 500 error → **PASS**
- **Failing test_servers.py unit tests** → Verified by running the test suite → **PASS** (all 37 tests pass)
- **Connection Leak Fix** → Verified by running `uv run python3 -Wd -c "..."` resource replication script → **FAIL** (50 unclosed client sessions are still reported)

---

## Coverage Gaps

- **Resource Leak Unit Tests** — risk level: **HIGH** — The unit tests completely mock/patch `AsyncWeb3`, which hidden the socket leak warnings during the test suite execution. Recommendation: Implement a unit test that verifies `__aenter__` and `__aexit__` (or `disconnect`) are indeed called on the mocked provider/client instance, or run the client session trace dynamically.

---

## Unverified Items

- No unverified items.
