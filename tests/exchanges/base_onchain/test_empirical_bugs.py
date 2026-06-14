import asyncio
import logging
import json
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from fastapi import Response

from crypcodile.exchanges.base_onchain.connector import BaseOnchainTransport
from crypcodile.api_server import get_market_data, simulate_payment, PAYMENTS_DB, PRICE_USDC, RECIPIENT_WALLET

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


class FaultyV3MockWeb3:
    def __init__(self):
        self.eth = FaultyV3MockEth(self)
        
    @staticmethod
    def to_checksum_address(addr):
        return addr

class FaultyV3MockEth:
    def __init__(self, parent):
        self.parent = parent
        self._block_number = 1000
        
    @property
    async def block_number(self):
        return self._block_number
        
    def contract(self, address, abi):
        return FaultyV3MockContract(address)
        
    async def get_block(self, block_num):
        return {"timestamp": 1600000000}
        
    async def get_logs(self, filter_params):
        return []

class FaultyV3MockContract:
    def __init__(self, address):
        self.address = address
        self.functions = FaultyV3MockContractFunctions(address)

class FaultyV3MockContractFunctions:
    def __init__(self, address):
        self.address = address

    def getPool(self, *args, **kwargs):
        class Call:
            async def call(self):
                return "0xMockPoolAddress"
        return Call()

    def slot0(self):
        class Call:
            async def call(self):
                raise Exception("Slot0 query failure")
        return Call()

    def liquidity(self):
        class Call:
            async def call(self):
                return 1000000
        return Call()


@pytest.mark.asyncio
async def test_slot0_unbound_local_error() -> None:
    """Demonstrate that slot0 failure raises UnboundLocalError outside the inner try-except."""
    mock_w3 = FaultyV3MockWeb3()
    
    with patch("web3.AsyncWeb3", return_value=mock_w3) as mock_web3_class:
        mock_web3_class.to_checksum_address = lambda x: x
        
        # cbBTC-USDC is uniswap_v3, WELL-WETH is aerodrome_v2
        # We query both symbols to see if the exception on cbBTC-USDC prevents WELL-WETH processing
        transport = BaseOnchainTransport("mock_rpc", ["cbBTC-USDC", "WELL-WETH"], poll_interval=0.01)
        
        original_sleep = asyncio.sleep
        async def mock_sleep(delay):
            transport._connected = False
            await original_sleep(0)
            
        with patch("asyncio.sleep", mock_sleep):
            # Run the poll loop. It should catch the exception at the outer loop block level
            # but it will crash the current iteration.
            await transport.connect()
            await transport._poll_task
            
            # Since slot0 failed, the update for cbBTC-USDC was not sent, and WELL-WETH was never processed
            # because the UnboundLocalError aborted the iteration.
            # Let's verify if WELL-WETH was initialized/polled.
            # If the loop did not crash mid-way, WELL-WETH would be in resolved_pools.
            # Let's verify that we have an empty queue (or no WELL-WETH update).
            results = []
            while not transport._queue.empty():
                val = transport._queue.get_nowait()
                if val is not None:
                    results.append(val.decode())
            
            # WELL-WETH update should not be in the queue because the loop crashed when processing cbBTC-USDC
            assert not any("WELL-WETH" in r for r in results), "WELL-WETH was processed despite slot0 failure!"


@pytest.mark.asyncio
async def test_api_server_double_spend_replay() -> None:
    """Verify that api_server.py is vulnerable to double-spend transaction hash replay."""
    # 1. Initialize two payment sessions
    response1 = Response()
    response2 = Response()
    
    res1 = await get_market_data("cbBTC-USDC", response1, payment_signature=None)
    assert res1["status"] == "payment_required"
    pid1 = res1["payment_required"]["payment_id"]
    
    res2 = await get_market_data("cbBTC-USDC", response2, payment_signature=None)
    assert res2["status"] == "payment_required"
    pid2 = res2["payment_required"]["payment_id"]
    
    sig1, address = generate_signature(pid1)
    sig2, _ = generate_signature(pid2)

    # 2. We mock on-chain transaction receipt query
    mock_receipt = {
        "status": 1,
        "blockNumber": 100,
        "logs": [
            {
                "address": "0x833589fCD6eDb6E08f4c7C32D4f71b54bda02913", # USDC
                "topics": [
                    b"\xdd\xf2\x52\xad\x1b\xe2\xc8\x9b\x69\xc2\xb0\x68\xfc\x37\x8d\xaa\x95\x2b\xa7\xf1\x63\xc4\xa1\x16\x28\xf5\x5a\x4d\xf5\x23\xb3\xef", # Transfer
                    bytes.fromhex(address[2:].zfill(64)),
                    bytes.fromhex(RECIPIENT_WALLET[2:].zfill(64))
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
            self.eth.get_block = AsyncMock(return_value={"timestamp": 1000})
            self.eth.get_transaction_receipt = AsyncMock(return_value=mock_receipt)
            
        @staticmethod
        def to_checksum_address(addr):
            return addr
            
    mock_w3 = MockWeb3API()
    
    with patch("crypcodile.api_server.get_w3", return_value=mock_w3), \
         patch("crypcodile.api_server.get_onchain_price", AsyncMock(return_value={"price": 40000.0})):
         
        # Use the same tx_hash for both payment IDs
        tx_hash = "0x" + "a" * 64
        
        # Verify first payment
        sig_payload1 = json.dumps({
            "payment_id": pid1,
            "tx_hash": tx_hash,
            "signature": sig1
        })
        res_pay1 = await get_market_data("cbBTC-USDC", response1, payment_signature=sig_payload1)
        assert res_pay1["status"] == "success"
        assert PAYMENTS_DB[pid1]["status"] == "paid"
        
        # Verify second payment using the exact same tx_hash
        sig_payload2 = json.dumps({
            "payment_id": pid2,
            "tx_hash": tx_hash,
            "signature": sig2
        })
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await get_market_data("cbBTC-USDC", response2, payment_signature=sig_payload2)
        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == "Transaction hash already processed."
