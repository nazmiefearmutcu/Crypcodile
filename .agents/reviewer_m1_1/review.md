# Review Report - Milestone 1: Native AsyncWeb3 Refactoring

## Review Summary

**Verdict**: APPROVE (PASS)

## Findings

### Minor Finding 1: Cursor Lag / Duplicate Log Processing on Single Pool Failures

- **What**: If any pool's state query fails during a polling iteration, the cursor (`self._last_block`) is not advanced for any pool.
- **Where**: `src/crypcodile/exchanges/base_onchain/connector.py`, lines 415-417 and line 435:
  ```python
  415:                     except Exception as e:
  416:                         log.error(f"base_onchain: Error polling pool data for {sym}: {e}")
  417:                         success = False
  ...
  435:                 if success:
  436:                     self._last_block = current_block
  ```
- **Why**: Since `success` is set to `False` when one pool query fails, the `_last_block` cursor is not updated. In the next iteration, the transport queries logs from the old `_last_block` block number, resulting in duplicate swap log processing for all other pools that succeeded.
- **Suggestion**: Track a separate block cursor per pool or resolve each pool independently, rather than sharing a single `_last_block` across the entire transport.

### Minor Finding 2: Lack of RPC Backoff on Outer Polling Loop Failure

- **What**: Sustained outer loop exceptions (such as network interface down or block number query failure) will cause the transport to sleep only for `self.poll_interval` before retrying.
- **Where**: `src/crypcodile/exchanges/base_onchain/connector.py`, lines 438-441:
  ```python
  438:             except Exception as e:
  439:                 log.error(f"base_onchain: Error polling pool data: {e}")
  440:             
  441:             await asyncio.sleep(self.poll_interval)
  ```
- **Why**: If the RPC node is down or rate-limiting the client, rapid polling every 5 seconds (or configured `poll_interval`) without exponential backoff could worsen rate limiting or IP bans.
- **Suggestion**: Implement a simple exponential backoff wrapper (e.g. doubling sleep time up to a maximum cap like 60 seconds) when consecutive outer loop exceptions occur.

---

## Verified Claims

- **Web3 queries use native `AsyncWeb3` and `AsyncHTTPProvider`** (no `asyncio.to_thread` wrapping, no synchronous Web3 client instantiations)  
  → **Verified via**: Code inspection of `src/crypcodile/exchanges/base_onchain/connector.py`, `src/crypcodile/mcp_server.py`, and `src/crypcodile/api_server.py`  
  → **Status**: PASS

- **Tests mock these properly and all tests pass**  
  → **Verified via**: Running `uv run pytest tests/exchanges/base_onchain/` (28 passed) and inspecting mocking patterns in tests (e.g. `test_connector.py` and `test_adversarial.py`)  
  → **Status**: PASS

- **Static Type Checking & Linting Compliance**  
  → **Verified via**: Running `uv run ruff check` and `uv run mypy` on the modified files  
  → **Status**: PASS (0 issues found)

---

## Coverage Gaps

- **Direct payment verification logic on-chain**: The `api_server.py` simulates payment verification using database status checks rather than querying logs on the blockchain directly.  
  - *Risk level*: Low for Milestone 1 (since x402 payment verification is explicitly scoped under Milestone 2).  
  - *Recommendation*: Accept risk for now and implement full on-chain USDC transfer receipt verification under Milestone 2.

---

## Unverified Items

- **Real blockchain connection latency/failures**: Actual integration testing against Base mainnet public/private endpoints was not conducted due to running in an isolated environment.  
  - *Reason not verified*: Integration/unit tests correctly use mocks to simulate various RPC node behaviors and latencies safely and deterministically.

---

## Challenge Summary

**Overall risk assessment**: LOW

## Challenges

### Low Challenge 1: Event Loop Blockage

- **Assumption challenged**: Web3 queries might block the asyncio event loop.
- **Attack scenario**: High RPC latency causes synchronous operations to hang, stalling other concurrent tasks in the application.
- **Blast radius**: Low. Since all Web3 client interactions are refactored to native `AsyncWeb3` and `AsyncHTTPProvider` using `await`, the event loop is released during network I/O.
- **Mitigation**: Confirmed by `test_non_blocking_event_loop`, where a background task successfully ran concurrent loop ticks during simulated RPC node delays.

### Medium Challenge 2: Cursor behavior on block lag/reorg

- **Assumption challenged**: RPC nodes always return strictly increasing block numbers.
- **Attack scenario**: An RPC node reports a block number lower than the previously processed `_last_block` block.
- **Blast radius**: Medium. If `toBlock` < `fromBlock`, the `get_logs` API throws a `ValueError`.
- **Mitigation**: The transport catches the `ValueError`, preventing the cursor from advancing. The next iteration uses the correct parameters once block heights align. Confirmed by `test_cursor_behavior_on_block_lag`.

---

## Stress Test Results

- **Extreme prices (e.g. zero, negative, extremely small, extremely large)** → Verified standard normalization, division-by-zero handling, size capping -> PASS
- **Extreme reserves (e.g. infinity, NaN, zero)** → Verified normalization robustness, size enforcement, and float validations -> PASS
- **Memory efficiency of cache** → Verified `_block_cache` limits size to 1000 entries to prevent memory leaks -> PASS
