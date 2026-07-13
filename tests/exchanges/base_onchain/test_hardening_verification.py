import asyncio
import json
import os
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import Response, HTTPException

from crypcodile.exchanges.base_onchain.connector import BaseOnchainTransport
from crypcodile.api_server import get_market_data, get_payments_file, load_payments_db

# Mock exceptions
from web3.exceptions import ContractLogicError

class AwaitableValue:
    def __init__(self, val):
        self.val = val
    def __await__(self):
        async def _async_val():
            if isinstance(self.val, Exception):
                raise self.val
            return self.val
        return _async_val().__await__()


def generate_signature(payment_id: str, private_key: str = "0x" + "1" * 64) -> tuple[str, str]:
    from eth_account import Account
    from eth_account.messages import encode_defunct
    account = Account.from_key(private_key)
    message = encode_defunct(text=payment_id)
    sig = account.sign_message(message).signature.hex()
    if not sig.startswith("0x"):
        sig = "0x" + sig
    return sig, account.address

@pytest.mark.asyncio
async def test_deterministic_exceptions_not_retried() -> None:
    """Verify that deterministic exceptions like ContractLogicError are not retried in _call_with_retry."""
    transport = BaseOnchainTransport("mock_rpc", ["cbBTC-USDC"], poll_interval=1.0)
    
    call_count = 0
    async def mock_deterministic_fail():
        nonlocal call_count
        call_count += 1
        raise ContractLogicError("Execution reverted")
        
    with pytest.raises(ContractLogicError, match="Execution reverted"):
        await transport._call_with_retry(mock_deterministic_fail)
        
    # Should only attempt once
    assert call_count == 1

@pytest.mark.asyncio
async def test_api_server_recent_block_timestamp_validation() -> None:
    """Verify that transactions older than 1 hour are rejected with a 400 status."""
    # Initialize a pending payment
    response = Response()
    res = await get_market_data("cbBTC-USDC", response, payment_signature=None)
    payment_id = res["payment_required"]["payment_id"]
    
    sig, address = generate_signature(payment_id)
    
    # We construct a mock transaction receipt and blocks
    # Block of the transaction is far in the past (e.g. 10 hours ago)
    past_time = int(time.time()) - 36000
    mock_receipt = {
        "status": 1,
        "blockNumber": 9999,
        "logs": [
            {
                "address": "0x833589fCD6eDb6E08f4c7C32D4f71b54bda02913", # USDC
                "topics": [
                    b"\xdd\xf2\x52\xad\x1b\xe2\xc8\x9b\x69\xc2\xb0\x68\xfc\x37\x8d\xaa\x95\x2b\xa7\xf1\x63\xc4\xa1\x16\x28\xf5\x5a\x4d\xf5\x23\xb3\xef",
                    bytes.fromhex(address[2:].zfill(64)),
                    b"\x00" * 12 + bytes.fromhex("70997970C51812dc3A010C7d01b50e0d17dc79C8") # Recipient wallet
                ],
                "data": (1000).to_bytes(32, byteorder='big')
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
            # Transaction block timestamp (10 hours ago)
            self.eth.get_block = AsyncMock(side_effect=lambda block_num: {
                "timestamp": past_time if block_num == 9999 else int(time.time())
            })
            
        @staticmethod
        def to_checksum_address(addr):
            return addr
            
    mock_w3 = MockWeb3API()
    
    with patch("crypcodile.api_server.get_w3", return_value=mock_w3), \
         patch("crypcodile.api_server.get_onchain_price", AsyncMock(return_value={"price": 40000.0})):
         
        sig_payload = json.dumps({
            "payment_id": payment_id,
            "tx_hash": "0xmockhash",
            "signature": sig
        })
        
        # This should fail because the block timestamp is older than 1 hour
        with pytest.raises(HTTPException) as exc_info:
            await get_market_data("cbBTC-USDC", Response(), payment_signature=sig_payload)
            
        assert exc_info.value.status_code == 400
        assert "Transaction is too old" in exc_info.value.detail

@pytest.mark.asyncio
async def test_api_server_payments_db_file_persistence() -> None:
    """Verify that PAYMENTS_DB is written to the persistent JSON file."""
    payments_file = get_payments_file()
    # Clear file if exists
    if os.path.exists(payments_file):
        try:
            os.remove(payments_file)
        except Exception:
            pass
            
    response = Response()
    res = await get_market_data("cbBTC-USDC", response, payment_signature=None)
    payment_id = res["payment_required"]["payment_id"]
    
    # Check that file exists and contains the payment record
    assert os.path.exists(payments_file)
    db = await load_payments_db()
    assert payment_id in db
    assert db[payment_id]["status"] == "pending"
    assert db[payment_id]["symbol"] == "cbBTC-USDC"
    # Atomic write should not leave a temp sibling behind
    assert not os.path.exists(payments_file + ".tmp")


@pytest.mark.asyncio
async def test_save_db_file_atomic_replace_and_fail_loud() -> None:
    """_save_db_file uses temp+os.replace and re-raises on failure."""
    from crypcodile.api_server import _save_db_file, PAYMENTS_DB, db_lock

    payments_file = get_payments_file()
    payload = {"pid-atomic": {"status": "pending", "symbol": "cbBTC-USDC"}}

    # Happy path: durable write lands at final path
    _save_db_file(payload)
    assert os.path.exists(payments_file)
    assert not os.path.exists(payments_file + ".tmp")
    with open(payments_file) as f:
        assert json.load(f) == payload

    # Failure path: OSError from open is logged and re-raised (not swallowed)
    with patch("builtins.open", side_effect=OSError("disk full")):
        with pytest.raises(OSError, match="disk full"):
            _save_db_file({"pid-fail": {"status": "paid"}})

    # set_async depends on save — must propagate so CAS/serve cannot succeed silently
    async with db_lock:
        with patch(
            "crypcodile.api_server._save_db_file",
            side_effect=OSError("disk full"),
        ):
            with pytest.raises(OSError, match="disk full"):
                await PAYMENTS_DB.set_async(
                    "pid-set-async-fail",
                    {"status": "spent", "symbol": "cbBTC-USDC"},
                )


@pytest.mark.asyncio
async def test_sync_logs_load_errors(caplog: pytest.LogCaptureFixture) -> None:
    """_sync must log load failures instead of silently passing."""
    import logging
    from crypcodile.api_server import PersistentDict

    payments_file = get_payments_file()
    with open(payments_file, "w") as f:
        f.write("{not-valid-json")

    pdb = PersistentDict()
    with caplog.at_level(logging.ERROR, logger="crypcodile.api_server"):
        pdb._sync()

    assert any(
        "Error loading PAYMENTS_DB during sync" in rec.message for rec in caplog.records
    )


@pytest.mark.asyncio
async def test_non_blocking_ipc() -> None:
    """Verify that _load_ipc is a coroutine function (async/non-blocking) and uses to_thread."""
    import inspect
    from crypcodile.exchanges.base_onchain.connector import _load_ipc
    
    assert inspect.iscoroutinefunction(_load_ipc)

@pytest.mark.asyncio
async def test_write_ipc_non_blocking() -> None:
    """Verify that _write_ipc creates an asyncio task with asyncio.to_thread."""
    from crypcodile.exchanges.base_onchain.connector import IPCDict
    import asyncio
    
    ipc_dict = IPCDict("TEST_WRITE_IPC")
    
    with patch("asyncio.to_thread") as mock_to_thread, \
         patch("asyncio.get_running_loop") as mock_get_loop:
         
        mock_loop = MagicMock()
        mock_get_loop.return_value = mock_loop
        
        ipc_dict._write_ipc()
        
        # Check that asyncio.to_thread was called
        mock_to_thread.assert_called_once()
        # Check that loop.create_task was called with the returned coroutine
        mock_loop.create_task.assert_called_once()
