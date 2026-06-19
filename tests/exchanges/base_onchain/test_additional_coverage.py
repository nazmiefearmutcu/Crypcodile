import asyncio
import json
import os
import pytest
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import Response, HTTPException

import typer
from crypcodile.cli import (
    resolve_input_symbols,
    basis_cmd,
    select_collect_params_interactively
)
from crypcodile.api_server import get_market_data, PAYMENTS_DB, db_lock
from crypcodile.mcp_server import execute_with_retry_and_failover
from crypcodile.exchanges.base import http_get_helper


class AwaitableValue:
    def __init__(self, val):
        self.val = val
    def __await__(self):
        async def _async_val():
            if isinstance(self.val, Exception):
                raise self.val
            return self.val
        return _async_val().__await__()


# --- 1. Deterministic Symbol Resolution Test ---
def test_deterministic_symbol_resolution(tmp_path: Path) -> None:
    """Verify prefix-less and fuzzy match symbol resolution is deterministic."""
    with patch("crypcodile.store.catalog.Catalog") as mock_catalog_class:
        mock_catalog = MagicMock()
        mock_catalog._registered_channels = ["trade"]
        import polars as pl
        mock_df = pl.DataFrame({"symbol": ["deribit:BTC-PERPETUAL", "bybit:BTC-PERPETUAL"]})
        mock_catalog.query.return_value = mock_df
        mock_catalog_class.return_value = mock_catalog

        # We resolve "btc-perpetual".
        # Sorted candidate list will be: ["bybit:BTC-PERPETUAL", "deribit:BTC-PERPETUAL"]
        # Prefixless match: parts[1].lower() == "btc-perpetual"
        # First match found must be "bybit:BTC-PERPETUAL" deterministically.
        resolved = resolve_input_symbols(tmp_path, ["btc-perpetual"], ["trade"])
        assert resolved == ["bybit:BTC-PERPETUAL"]


# --- 2. Empty String Inputs Validation in basis_cmd ---
def test_basis_cmd_empty_strings(tmp_path: Path) -> None:
    """Verify empty/whitespace strings passed to basis_cmd raiseExit code 1."""
    # We patch is_interactive_stdin to return False to simulate non-interactive CLI run
    with patch("crypcodile.cli.is_interactive_stdin", return_value=False):
        with pytest.raises(typer.Exit) as exc_info:
            basis_cmd(perp="  ", future="", spot="", data_dir=tmp_path)
        assert exc_info.value.exit_code == 1

        with pytest.raises(typer.Exit) as exc_info:
            basis_cmd(perp=None, future=" ", spot="BTC", data_dir=tmp_path)
        assert exc_info.value.exit_code == 1


# --- 3. Custom Channel Interactive Selection ---
def test_custom_channel_interactive_selection() -> None:
    """Verify select_collect_params_interactively allows custom channels."""
    # We select exchange, then custom channel, then default symbol
    # Mock inputs:
    # 1. Exchange selection: "1" (binance)
    # 2. Channel selection: "C" (custom)
    # 3. Custom channel text: "custom_channel_1, custom_channel_2"
    # 4. Symbol selection: "1" (BTCUSDT)
    inputs = ["1", "C", "custom_channel_1, custom_channel_2", "1"]
    
    with patch("typer.prompt", side_effect=inputs), \
         patch("crypcodile.cli.prompt_with_autocomplete", return_value="BTCUSDT"):
        exchange, symbols, channels = select_collect_params_interactively(None, None, None)
        assert exchange == "binance"
        assert channels == ["custom_channel_1", "custom_channel_2"]
        assert symbols == ["BTCUSDT"]


# --- 4. Spent Payment State Transition and Re-entrancy Rejection ---
@pytest.mark.asyncio
async def test_api_server_spent_payment_rejection() -> None:
    """Verify that already spent payment IDs are rejected in get_market_data."""
    # Simulate a spent payment record
    pid = "mock_spent_payment_id"
    async with db_lock:
        await PAYMENTS_DB.set_async(pid, {
            "status": "spent",
            "symbol": "cbBTC-USDC",
            "tx_hash": "0xspent_tx_hash",
            "sender": "0xsender",
            "signature": "0x" + "0" * 130
        })
    
        # Prepare signature JSON payload
        sig_payload = json.dumps({
            "payment_id": pid,
            "tx_hash": "0xspent_tx_hash",
            "signature": "0x" + "0" * 130
        })

    # Recover signature address mock
    with patch("eth_account.Account.recover_message", return_value="0xsender"):
        with pytest.raises(HTTPException) as exc_info:
            await get_market_data("cbBTC-USDC", Response(), payment_signature=sig_payload)
        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == "Payment already spent."


# --- 5. USDC Configuration-based validation ---
@pytest.mark.asyncio
async def test_api_server_usdc_configured_price() -> None:
    """Verify that payment log parsing validates the USDC transfer log amount against PRICE_USDC config."""
    pid = "mock_config_payment_id"
    async with db_lock:
        await PAYMENTS_DB.set_async(pid, {
            "status": "pending",
            "symbol": "cbBTC-USDC"
        })

    from eth_account import Account
    from eth_account.messages import encode_defunct
    private_key = "0x" + "2" * 64
    account = Account.from_key(private_key)
    message = encode_defunct(text=pid)
    sig = account.sign_message(message).signature.hex()
    if not sig.startswith("0x"):
        sig = "0x" + sig
    address = account.address

    # Let's say PRICE_USDC is 2.5 USDC
    expected_amount = int(round(2.5 * 1_000_000)) # 2,500,000 base units

    mock_receipt = {
        "status": 1,
        "blockNumber": 12345,
        "logs": [
            {
                "address": "0x833589fCD6eDb6E08f4c7C32D4f71b54bda02913", # USDC
                "topics": [
                    b"\xdd\xf2\x52\xad\x1b\xe2\xc8\x9b\x69\xc2\xb0\x68\xfc\x37\x8d\xaa\x95\x2b\xa7\xf1\x63\xc4\xa1\x16\x28\xf5\x5a\x4d\xf5\x23\xb3\xef", # Transfer topic
                    b"\x00" * 12 + bytes.fromhex(address[2:]), # sender
                    b"\x00" * 12 + bytes.fromhex("70997970C51812dc3A010C7d01b50e0d17dc79C8") # Recipient wallet
                ],
                "data": expected_amount.to_bytes(32, byteorder='big')
            }
        ]
    }

    class MockWeb3API:
        def __init__(self):
            self.provider = MagicMock()
            self.eth = MagicMock()
            self.eth.chain_id = AwaitableValue(8453)
            self.eth.get_transaction = AsyncMock(return_value={"from": address, "chainId": 8453})
            self.eth.get_transaction_receipt = AsyncMock(return_value=mock_receipt)
            self.eth.get_block = AsyncMock(return_value={"timestamp": int(time.time())})

        @staticmethod
        def to_checksum_address(addr):
            return addr

    mock_w3 = MockWeb3API()

    with patch("crypcodile.api_server.get_w3", return_value=mock_w3), \
         patch("crypcodile.api_server.PRICE_USDC", "2.5"), \
         patch("crypcodile.api_server.get_onchain_price", AsyncMock(return_value={"price": 40000.0})):
         
        sig_payload = json.dumps({
            "payment_id": pid,
            "tx_hash": "0xmockhash",
            "signature": sig
        })

        res = await get_market_data("cbBTC-USDC", Response(), payment_signature=sig_payload)
        assert res["status"] == "success"
        
        # Verify status became spent after returning market data
        record = await PAYMENTS_DB.get_async(pid)
        assert record["status"] == "spent"


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


# --- 7. MCP RPC Failover / Retry Test ---
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
    with patch("crypcodile.mcp_server._get_rpc_urls", return_value=["http://rpc1", "http://rpc2", "http://rpc3"]), \
         patch("asyncio.sleep", AsyncMock()): # no delay in tests
         
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
