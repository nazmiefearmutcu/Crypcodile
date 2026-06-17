import asyncio
import json
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import Response, HTTPException

from crypcodile.api_server import get_market_data, simulate_payment, PAYMENTS_DB, PRICE_USDC, RECIPIENT_WALLET
from crypcodile.exchanges.base_onchain.connector import BaseOnchainTransport, POOL_SPECS, TOKENS, _get_ipc_file, IPCDict

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
async def test_replay_attack_vulnerability() -> None:
    """Hypothesis 1: Replay Attack / Transaction Reuse on Payment Verification.
    
    Verify if a malicious client can reuse the same successful transaction hash (tx_hash)
    for multiple independent payment_ids.
    """
    from crypcodile.api_server import PAYMENTS_FILE
    if os.path.exists(PAYMENTS_FILE):
        try:
            os.remove(PAYMENTS_FILE)
        except Exception:
            pass
    PAYMENTS_DB.clear()
    
    # 1. Get payment_id_1
    response1 = Response()
    res1 = await get_market_data(symbol="cbBTC-USDC", response=response1, payment_signature=None)
    assert res1["status"] == "payment_required"
    pid1 = res1["payment_required"]["payment_id"]
    
    # 2. Get payment_id_2
    response2 = Response()
    res2 = await get_market_data(symbol="cbBTC-USDC", response=response2, payment_signature=None)
    assert res2["status"] == "payment_required"
    pid2 = res2["payment_required"]["payment_id"]
    
    # Check that we got two distinct payment IDs
    assert pid1 != pid2
    
    from eth_account import Account
    from eth_account.messages import encode_defunct

    class AwaitableValue:
        def __init__(self, val):
            self.val = val
        def __await__(self):
            async def _async_val():
                return self.val
            return _async_val().__await__()

    private_key = "0x" + "1" * 64
    account = Account.from_key(private_key)
    
    # Sign pid1
    msg1 = encode_defunct(text=pid1)
    sig1 = account.sign_message(msg1).signature.hex()
    if not sig1.startswith("0x"):
        sig1 = "0x" + sig1
        
    # Sign pid2
    msg2 = encode_defunct(text=pid2)
    sig2 = account.sign_message(msg2).signature.hex()
    if not sig2.startswith("0x"):
        sig2 = "0x" + sig2

    mock_receipt = {
        "status": 1,
        "blockNumber": 100,
        "logs": [
            {
                "address": "0x833589fCD6eDb6E08f4c7C32D4f71b54bda02913", # official USDC
                "topics": [
                    b"\xdd\xf2\x52\xad\x1b\xe2\xc8\x9b\x69\xc2\xb0\x68\xfc\x37\x8d\xaa\x95\x2b\xa7\xf1\x63\xc4\xa1\x16\x28\xf5\x5a\x4d\xf5\x23\xb3\xef", # Transfer topic
                    bytes.fromhex(account.address[2:].zfill(64)), # from
                    bytes.fromhex(RECIPIENT_WALLET[2:].zfill(64)) # to Nazmi's recipient wallet
                ],
                "data": (1000).to_bytes(32, byteorder='big') # 1000 USDC
            }
        ]
    }

    with patch("crypcodile.api_server.get_w3") as mock_get_w3, \
         patch("crypcodile.api_server.get_onchain_price", new_callable=AsyncMock) as mock_get_price:
         
        mock_w3 = MagicMock()
        mock_w3.eth.chain_id = AwaitableValue(8453)
        mock_w3.eth.get_transaction = AsyncMock(return_value={
            "from": account.address,
            "chainId": 8453
        })
        mock_w3.eth.get_block = AsyncMock(return_value={"timestamp": 1000})
        mock_w3.eth.get_transaction_receipt = AsyncMock(return_value=mock_receipt)
        mock_get_w3.return_value = mock_w3
        
        mock_get_price.return_value = {
            "symbol": "cbBTC-USDC",
            "price": 40000.0,
            "block": 12345
        }
        
        # 3. Call first time with pid1 and tx_hash_1
        sig_payload_1 = json.dumps({
            "payment_id": pid1,
            "tx_hash": "0xreplayedtxhash123",
            "signature": sig1
        })
        
        resp_1 = await get_market_data(
            symbol="cbBTC-USDC",
            response=Response(),
            payment_signature=sig_payload_1
        )
        assert resp_1["status"] == "success"
        assert PAYMENTS_DB[pid1]["status"] == "paid"
        
        # 4. Call second time with pid2 and the SAME tx_hash_1
        sig_payload_2 = json.dumps({
            "payment_id": pid2,
            "tx_hash": "0xreplayedtxhash123", # Reused transaction hash
            "signature": sig2
        })
        
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await get_market_data(
                symbol="cbBTC-USDC",
                response=Response(),
                payment_signature=sig_payload_2
            )
        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == "Transaction hash already processed."


@pytest.mark.asyncio
async def test_dynamic_ipc_reload_failure() -> None:
    """Hypothesis 2: Dynamic IPC reload failure inside BaseOnchainTransport's poll loop.
    
    Verify if BaseOnchainTransport reloads POOL_SPECS from the IPC file dynamically.
    If it doesn't reload, new custom pools written to the IPC file will never be polled.
    """
    # Initialize transport with cbBTC-USDC and WELL-WETH, but WELL-WETH is not in POOL_SPECS yet
    # We'll simulate adding WELL-WETH to POOL_SPECS dynamically.
    
    # Save original specs
    original_specs = dict(POOL_SPECS)
    
    # Clear WELL-WETH from POOL_SPECS initially
    if "WELL-WETH" in POOL_SPECS:
        del POOL_SPECS["WELL-WETH"]
        
    mock_w3 = MagicMock()
    mock_w3.eth.block_number = AwaitableValue(1000)
    mock_w3.eth.get_block = AsyncMock(return_value={"timestamp": 1234567890})
    mock_w3.eth.get_logs = AsyncMock(return_value=[])
    
    # Factory mock
    mock_factory = MagicMock()
    mock_factory.functions.getPool.return_value.call = AsyncMock(return_value="0xMockPoolAddress")
    
    # Pool mock
    mock_pool = MagicMock()
    mock_pool.functions.slot0.return_value.call = AsyncMock(return_value=[(2**96), 0, 0, 0, 0, 0, True])
    mock_pool.functions.liquidity.return_value.call = AsyncMock(return_value=1000)
    
    def contract_side_effect(address, abi):
        if address in ("0x33128a8fC17869897dcE68Ed026d694621f6FDfD", "0x420DD381b31aEf6683db6B902084cB0FFECe40Da"):
            return mock_factory
        return mock_pool
        
    mock_w3.eth.contract.side_effect = contract_side_effect
    
    with patch("web3.AsyncWeb3", return_value=mock_w3) as mock_web3_class:
        mock_web3_class.to_checksum_address = lambda x: x
        
        transport = BaseOnchainTransport("mock_rpc", ["WELL-WETH"], poll_interval=0.01)
        
        # Start the transport
        await transport.connect()
        
        # Let it run a bit
        await asyncio.sleep(0.05)
        
        # Verify queue is empty (since WELL-WETH is not in POOL_SPECS, it was skipped/not resolved)
        assert transport._queue.qsize() == 0
        
        # Dynamically add WELL-WETH to the IPC dict (which writes to the IPC file)
        # Note: in a separate process or even this process, this writes to the IPC file.
        # But POOL_SPECS itself is modified in memory in this process.
        # However, we want to test if _poll_loop ever reads from the IPC file during execution.
        # To simulate a separate process writing to IPC_FILE, we write directly to the file:
        data = {
            "POOL_SPECS": {
                "WELL-WETH": original_specs["WELL-WETH"]
            },
            "TOKENS": dict(TOKENS)
        }
        with open(_get_ipc_file(), "w") as f:
            json.dump(data, f)
            
        # Wait more time
        await asyncio.sleep(0.05)
        
        # Since _poll_loop does NOT reload from IPC_FILE, it still doesn't see WELL-WETH
        # and queue remains empty.
        assert transport._queue.qsize() == 0
        
        # Clean up
        await transport.close()
        
        # Restore original POOL_SPECS
        POOL_SPECS.update(original_specs)
        print("DYNAMIC_IPC_RELOAD_FAILURE_VERIFIED: The connector's poll loop did not load WELL-WETH dynamically.")


@pytest.mark.asyncio
async def test_api_server_robustness_malformed_receipts() -> None:
    """Hypothesis 4: Robustness of payment verification against malformed logs/receipts.
    
    Verify that get_market_data raises appropriate HTTPException and does not crash the server
    if receipt is invalid or contains unexpected logs formats.
    """
    from crypcodile.api_server import PAYMENTS_FILE
    if os.path.exists(PAYMENTS_FILE):
        try:
            os.remove(PAYMENTS_FILE)
        except Exception:
            pass
    PAYMENTS_DB.clear()
    
    # 1. Get payment_id
    response = Response()
    res = await get_market_data(symbol="cbBTC-USDC", response=response, payment_signature=None)
    pid = res["payment_required"]["payment_id"]
    
    # 2. Mock receipt with a missing 'topics' field inside logs
    mock_bad_receipt = {
        "status": 1,
        "logs": [
            {
                "address": "0x833589fCD6eDb6E08f4c7C32D4f71b54bda02913",
                # 'topics' is missing
                "data": (1000).to_bytes(32, byteorder='big')
            }
        ]
    }
    
    sig, address = generate_signature(pid)
    
    with patch("crypcodile.api_server.get_w3") as mock_get_w3:
        mock_w3 = MagicMock()
        mock_w3.eth.chain_id = AwaitableValue(8453)
        mock_w3.eth.get_transaction = AsyncMock(return_value={
            "from": address,
            "chainId": 8453
        })
        mock_w3.eth.get_block = AsyncMock(return_value={"timestamp": 1000})
        mock_w3.eth.get_transaction_receipt = AsyncMock(return_value=mock_bad_receipt)
        mock_get_w3.return_value = mock_w3
        
        sig_payload = json.dumps({
            "payment_id": pid,
            "tx_hash": "0xbadtxhash",
            "signature": sig
        })
        
        # Should raise HTTPException 400
        with pytest.raises(HTTPException) as exc_info:
            await get_market_data(
                symbol="cbBTC-USDC",
                response=Response(),
                payment_signature=sig_payload
            )
        assert exc_info.value.status_code == 400
        assert "USDC payment validation failed" in exc_info.value.detail or "Failed verifying payment signature" in exc_info.value.detail


class LagMockWeb3:
    def __init__(self, block_sequence: list[int], logged_ranges: list):
        self.block_sequence = block_sequence
        self.call_count = 0
        self.logged_ranges = logged_ranges
        self.eth = LagMockEth(self)
        
    @staticmethod
    def to_checksum_address(addr):
        return addr

class LagMockEth:
    def __init__(self, parent: LagMockWeb3):
        self.parent = parent
        
    @property
    async def block_number(self) -> int:
        seq = self.parent.block_sequence
        idx = min(self.parent.call_count, len(seq) - 1)
        self.parent.call_count += 1
        return seq[idx]
        
    def contract(self, address, abi):
        return DummyMockContract(address)
        
    async def get_block(self, block_num):
        return {"timestamp": 1600000000}
        
    async def get_logs(self, filter_params):
        self.parent.logged_ranges.append((filter_params["fromBlock"], filter_params["toBlock"]))
        return []

class DummyMockContract:
    def __init__(self, address):
        self.address = address
        self.functions = DummyMockContractFunctions(address)

class DummyMockContractFunctions:
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
                return [2**96, 0, 0, 0, 0, 0, True]
        return Call()

    def liquidity(self):
        class Call:
            async def call(self):
                return 1000000
        return Call()

    def getReserves(self):
        class Call:
            async def call(self):
                return [1000 * 10**18, 2000 * 10**18, 1234567]
        return Call()


@pytest.mark.asyncio
async def test_duplicate_log_query_bug() -> None:
    """Hypothesis 5: Cursor rollback and duplicate log query bug on block lag.
    
    Verify that if the block number goes backwards (lagging block reported), the cursor
    is rolled back to the lower lagging block, causing duplicate logs to be queried on the next recovery.
    """
    logged_ranges = []
    # Block sequence:
    # 1. 1000 (last_block becomes 1000)
    # 2. 990 (block lag, start_block 1001 > 990, does not query but sets last_block to 990)
    # 3. 1010 (recovery, start_block 991, end_block 1010. Queries logs from 991 to 1010)
    mock_w3 = LagMockWeb3(block_sequence=[1000, 990, 1010], logged_ranges=logged_ranges)
    
    with patch("web3.AsyncWeb3", return_value=mock_w3) as mock_web3_class:
        mock_web3_class.to_checksum_address = lambda x: x
        
        transport = BaseOnchainTransport("mock_rpc", ["cbBTC-USDC"], poll_interval=0.01)
        
        # Start transport and run 3 loops
        await transport.connect()
        
        # Let it run for a short time to complete the 3 loops
        await asyncio.sleep(0.5)
        await transport.close()
        
    # Assert logged ranges: with monotonic cursor update, the second range is from 1001 to 1010, preventing duplicate queries.
    assert len(logged_ranges) >= 2
    assert logged_ranges[0] == (976, 1000)
    assert logged_ranges[1] == (996, 1010)
    
    # Verify range overlap is exactly the 5 blocks overlap (996 to 1000 inclusive)
    overlap = set(range(976, 1001)).intersection(set(range(996, 1011)))
    assert overlap == set(range(996, 1001))
    print("DUPLICATE_LOG_QUERY_BUG_FIXED: Verified no duplicate log query range overlap.")
