"""Odds and ends around the Base on-chain reader: address ordering, RPC failover, HTTP retry.

The legacy-CLI tests (``resolve_input_symbols``, ``basis_cmd``, the interactive collect
prompt) and the x402 payment-gate tests that used to share this file went with the surface
stacks they exercised. The hand-written crypto CLI is gone outright; ``market-data`` is not —
it resolves to the ``onchain-price`` capability through the rename ledger, and is served
without a payment gate.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from crocodile.core.connector import http_get_helper
from crocodile.crypto.exchanges.base_onchain.price import execute_with_retry_and_failover


# --- 6. Uniswap V3 Address Sorting Numerical Check ---
def test_uniswap_v3_address_sorting_numerical() -> None:
    """Verify that Uniswap V3 addresses are sorted numerically, not lexicographically."""
    # aero is lexicographically smaller but numerically larger (starts with 'B' = 11)
    aero = "0x" + "B" * 40
    # usdc is lexicographically larger but numerically smaller (starts with 'a' = 10)
    usdc = "0x" + "a" * 40

    # Lexicographically, 'B' (66) < 'a' (97)
    lex_sorted = sorted([aero, usdc])
    assert lex_sorted == [aero, usdc]

    # Numerically: 0xa... (10) < 0xB... (11)
    num_sorted = sorted([aero, usdc], key=lambda x: int(x, 16))
    assert num_sorted == [usdc, aero]
    assert num_sorted != lex_sorted


# --- 7. RPC Failover / Retry Test ---
@pytest.mark.asyncio
async def test_mcp_execute_with_retry_and_failover() -> None:
    """Verify that execute_with_retry_and_failover retries on 429 and failovers successfully."""
    call_count = 0
    async def mock_callback(w3_instance):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise Exception("HTTP status 429 Too Many Requests")
        return {"success": True, "provider": w3_instance.provider.endpoint_uri}

    # Patch RPC URLs to provide three mock URLs
    with patch(
        "crocodile.crypto.exchanges.base_onchain.price._get_rpc_urls",
        return_value=["http://rpc1", "http://rpc2", "http://rpc3"],
    ), patch("asyncio.sleep", AsyncMock()):  # no delay in tests

        # Execute
        res = await execute_with_retry_and_failover("http://rpc1", mock_callback)
        assert res["success"] is True
        assert call_count == 3


# --- 8. REST Client session/timeout/429 retry ---
@pytest.mark.asyncio
async def test_http_get_helper_429_retry() -> None:
    """Verify http_get_helper handles 429 rate limits and retries using headers."""
    import aiohttp

    class MockResponse:
        def __init__(self, status, json_data, headers=None):
            self.status = status
            self._json = json_data
            self.headers = headers or {}
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass
        def raise_for_status(self):
            if self.status >= 400:
                raise aiohttp.ClientResponseError(None, None, status=self.status)
        async def json(self):
            return self._json

    mock_session = MagicMock()

    call_count = 0
    def mock_get(url, params=None, timeout=None):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return MockResponse(429, None, {"Retry-After": "0.05"})
        return MockResponse(200, {"data": "ok"})

    mock_session.get = mock_get

    with patch("asyncio.sleep", AsyncMock()) as mock_sleep:
        res = await http_get_helper("http://test-url", session=mock_session)
        assert res == {"data": "ok"}
        assert call_count == 2
        mock_sleep.assert_called_with(0.05)
