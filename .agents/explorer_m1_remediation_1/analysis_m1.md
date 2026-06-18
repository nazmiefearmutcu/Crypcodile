# Analysis of Milestone 1 Status and Remediation

## Executive Summary
This analysis investigates the current status of Milestone 1: Native AsyncWeb3 refactoring for the Crypcodile codebase. While the codebase uses `AsyncWeb3` and `AsyncHTTPProvider` asynchronously (without blocking calls or `asyncio.to_thread`), a critical context manager usage error in `mcp_server.py` causes all endpoints querying on-chain prices (such as `api_server.py` and related tests) to crash with a `500 Internal Server Error`. Additionally, connection leaks due to unclosed sessions have been identified in both `connector.py` and the test suite. Finally, several downstream features (synthetic orderbook depth calculation and actual on-chain USDC payment verification) are currently implemented as dummy facades.

---

## 1. AsyncWeb3 and AsyncHTTPProvider Native Usage
- **`src/crypcodile/exchanges/base_onchain/connector.py`**:
  - Uses `AsyncWeb3` and `AsyncHTTPProvider` inside `_poll_loop`.
  - All calls (e.g. `w3.eth.get_block`, `factory.functions.getPool().call`, `w3.eth.block_number`, `contract.functions.slot0().call`, `contract.functions.liquidity().call`, `contract.functions.getReserves().call`, and `w3.eth.get_logs`) are correctly `await`ed natively.
  - There are no synchronous blocking calls or instances of `asyncio.to_thread`.
- **`src/crypcodile/mcp_server.py`**:
  - The function `get_onchain_price` attempts to manage the lifecycle of `AsyncWeb3` with an `async with` statement:
    ```python
    async with AsyncWeb3(AsyncHTTPProvider(rpc_url)) as w3:
    ```
  - **Syntax/Runtime Issue**: In Web3.py, `AsyncHTTPProvider` is not a persistent connection provider. Instantiating `AsyncWeb3` via `async with` using a non-persistent provider triggers a runtime error:
    `TypeError/ValueError: Provider must inherit from PersistentConnectionProvider class when instantiating via async with.`
  - This prevents `get_onchain_price` from functioning, returning an error response, which in turn causes the API server to fail with a `500` error status code.

---

## 2. Connection, Socket, and Client Session Leaks
Three specific locations have been identified where `AsyncHTTPProvider` (backed by `httpx.AsyncClient`) is instantiated but the underlying session is never closed:

1. **`src/crypcodile/exchanges/base_onchain/connector.py` (`BaseOnchainTransport._poll_loop`)**:
   - `w3 = AsyncWeb3(AsyncHTTPProvider(self.rpc_url))` is instantiated before the polling loop.
   - When the transport closes (the polling task is cancelled or `self._connected` becomes `False`), the function exits without calling `await w3.provider.disconnect()`.
   - This causes a client session leak.

2. **`src/crypcodile/mcp_server.py` (`get_onchain_price`)**:
   - Because the `async with` block crashes instantly, the provider's HTTP client is not properly cleaned up, leaving an unclosed session.
   - Even if the block did not crash, using `async with` on `AsyncHTTPProvider` is invalid.

3. **`tests/e2e/test_tier1_features.py`**:
   - In `test_f1_block_cache_hit` (line 1028) and `test_f1_block_cache_eviction` (line 1051), `w3 = AsyncWeb3(AsyncHTTPProvider(rpc_url))` is instantiated, but `await w3.provider.disconnect()` is never called.

---

## 3. Test Suite Analysis and Root Cause Tracing
- **Pytest Command**: `uv run pytest`
- **Results**: 641 passed, 1 failed (overall suite); 6 failed when running payment-specific tests.
- **Failures**:
  - `tests/e2e/test_smoke_e2e.py::test_api_server_payment_flow`
  - `tests/e2e/test_tier1_features.py::test_f5_x402_verify_valid_payment`
  - `tests/e2e/test_tier1_features.py::test_f5_x402_receipt_lookup_fail`
  - `tests/e2e/test_tier1_features.py::test_f5_x402_wrong_recipient`
  - `tests/e2e/test_tier1_features.py::test_f5_x402_wrong_transfer_amount`
  - `tests/e2e/test_tier1_features.py::test_f5_x402_wrong_erc20_contract`
  - `tests/e2e/test_tier1_features.py::test_f5_x402_failed_transaction_status`

### Root Cause
1. In `api_server.py` (line 109), the `/api/v1/market-data` endpoint retrieves real-time pricing:
   ```python
   data = await get_onchain_price(symbol)
   if "error" in data:
       raise HTTPException(status_code=500, detail=data["error"])
   ```
2. Because of the `async with AsyncWeb3(AsyncHTTPProvider(...))` bug in `get_onchain_price`, the call raises an exception:
   `Provider must inherit from PersistentConnectionProvider class when instantiating via async with.`
3. `get_onchain_price` catches the exception and returns `{"error": "Failed fetching pool state: Provider must inherit from ..."}`.
4. The API server returns `500 Internal Server Error`.
5. The tests assert `resp.status == 200` or `resp.status in (400, 402)`, which fails because they receive `500`.

---

## 4. Dummy and Facade Implementations (Milestones 2–5)
The following features are currently implemented as dummy facades:

1. **Synthetic Orderbook Depth (Milestone 2/5)**:
   - In `src/crypcodile/exchanges/base_onchain/normalize.py`, the normalizer is specified to produce at least 5 bid and 5 ask levels (depth=5) calculated from tick/reserves math.
   - The current implementation only populates a single bid and ask level (depth=1) with a fixed spread and size proxy:
     ```python
     bids=[(bid_px, bid_sz)],
     asks=[(ask_px, ask_sz)],
     depth=1,
     ```
   
2. **On-chain USDC Payment Verification (Milestone 2/5)**:
   - In `src/crypcodile/api_server.py`, the `Payment-Signature` header containing a transaction hash is supposed to be validated on-chain (using `AsyncWeb3` to look up the transaction receipt, verify status == 1, check ERC-20 `Transfer` events from the USDC contract to `RECIPIENT_WALLET` for exactly `0.001 USDC`).
   - The current implementation contains a mock verification that ignores the transaction receipt details and simply marks the payment record as `"paid"`:
     ```python
     record["status"] = "paid"
     record["tx_hash"] = tx_hash
     ```
     No actual Web3 on-chain lookup or log parsing is executed.

---

## 5. Remediation Proposals

### Correcting the Context Manager & Session Leaks
Modify `get_onchain_price` in `src/crypcodile/mcp_server.py` to instantiate `AsyncWeb3` normally, and wrap the code in a `try...finally` block to await `w3.provider.disconnect()`.

**Proposed Change for `src/crypcodile/mcp_server.py`:**
```python
# Before
async def get_onchain_price(symbol: str, rpc_url: str = DEFAULT_RPC_URL) -> dict[str, Any]:
    ...
    try:
        async with AsyncWeb3(AsyncHTTPProvider(rpc_url)) as w3:
            t0_addr = AsyncWeb3.to_checksum_address(TOKENS[str(spec["token0"])])
            ...
            return { ... }
    except Exception as e:
        return {"error": f"Failed fetching pool state: {e}"}

# After
async def get_onchain_price(symbol: str, rpc_url: str = DEFAULT_RPC_URL) -> dict[str, Any]:
    ...
    w3 = AsyncWeb3(AsyncHTTPProvider(rpc_url))
    try:
        t0_addr = AsyncWeb3.to_checksum_address(TOKENS[str(spec["token0"])])
        ...
        return {
            "symbol": symbol,
            "pool_address": pool_addr,
            "price": price,
            "reserve0": reserve0,
            "reserve1": reserve1,
            "pool_type": spec["type"],
            "block": block_num
        }
    except Exception as e:
        return {"error": f"Failed fetching pool state: {e}"}
    finally:
        await w3.provider.disconnect()
```

Similarly, update `BaseOnchainTransport._poll_loop` in `src/crypcodile/exchanges/base_onchain/connector.py` to ensure provider cleanup:
```python
# Proposed Change for _poll_loop in connector.py:
async def _poll_loop(self) -> None:
    from web3 import AsyncHTTPProvider, AsyncWeb3
    w3 = AsyncWeb3(AsyncHTTPProvider(self.rpc_url))
    try:
        # factory and pool ABIs...
        while self._connected:
            try:
                # polling logic...
            except Exception as e:
                log.error(f"base_onchain: Error polling pool data: {e}")
            await asyncio.sleep(self.poll_interval)
    finally:
        await w3.provider.disconnect()
```

Also, update the two tests in `tests/e2e/test_tier1_features.py` to call `await w3.provider.disconnect()` in a `try...finally` block.
