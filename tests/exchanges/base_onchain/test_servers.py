import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import Response, HTTPException

from crypcodile.mcp_server import get_onchain_price, serve_stdio, get_base_market_data
from crypcodile.api_server import get_market_data, simulate_payment, PaymentSignature, PAYMENTS_DB


class AwaitableValue:
    def __init__(self, val):
        self.val = val
    def __await__(self):
        async def _async_val():
            if isinstance(self.val, Exception):
                raise self.val
            return self.val
        return _async_val().__await__()


@pytest.mark.asyncio
async def test_get_onchain_price_uniswap_v3_success() -> None:
    """Verify get_onchain_price successfully queries Uniswap V3 pools."""
    with patch("crypcodile.mcp_server.AsyncWeb3") as mock_web3_class:
        mock_w3 = MagicMock()
        mock_w3.__aenter__ = AsyncMock(return_value=mock_w3)
        mock_w3.__aexit__ = AsyncMock(return_value=None)
        mock_web3_class.return_value = mock_w3
        mock_web3_class.to_checksum_address = lambda x: x

        mock_w3.eth.block_number = AwaitableValue(12345)
        
        # Factory mock
        mock_factory = MagicMock()
        mock_factory.functions.getPool.return_value.call = AsyncMock(
            return_value="0xMockV3PoolAddress"
        )
        
        # Pool mock
        mock_pool = MagicMock()
        mock_pool.functions.slot0.return_value.call = AsyncMock(
            return_value=[(2**96 * 2), 0, 0, 0, 0, 0, True]
        )
        mock_pool.functions.liquidity.return_value.call = AsyncMock(
            return_value=100 * 10**8
        )
        
        def contract_side_effect(address, abi):
            if address == "0x33128a8fC17869897dcE68Ed026d694621f6FDfD":
                return mock_factory
            return mock_pool

        mock_w3.eth.contract.side_effect = contract_side_effect

        res = await get_onchain_price("cbBTC-USDC")
        assert res["symbol"] == "cbBTC-USDC"
        assert res["pool_address"] == "0xMockV3PoolAddress"
        assert res["price"] == 25.0
        assert res["block"] == 12345


@pytest.mark.asyncio
async def test_get_onchain_price_aerodrome_success() -> None:
    """Verify get_onchain_price successfully queries Aerodrome pools."""
    with patch("crypcodile.mcp_server.AsyncWeb3") as mock_web3_class:
        mock_w3 = MagicMock()
        mock_w3.__aenter__ = AsyncMock(return_value=mock_w3)
        mock_w3.__aexit__ = AsyncMock(return_value=None)
        mock_web3_class.return_value = mock_w3
        mock_web3_class.to_checksum_address = lambda x: x

        mock_w3.eth.block_number = AwaitableValue(12345)
        
        # Factory mock
        mock_factory = MagicMock()
        mock_factory.functions.getPool.return_value.call = AsyncMock(
            return_value="0xMockAeroPoolAddress"
        )
        
        # Pool mock
        mock_pool = MagicMock()
        mock_pool.functions.getReserves.return_value.call = AsyncMock(
            return_value=[(1000 * 10**18), (2000 * 10**6), 1234567]
        )
        
        def contract_side_effect(address, abi):
            if address == "0x420DD381b31aEf6683db6B902084cB0FFECe40Da":
                return mock_factory
            return mock_pool

        mock_w3.eth.contract.side_effect = contract_side_effect

        res = await get_onchain_price("AERO-USDC")
        assert res["symbol"] == "AERO-USDC"
        assert res["pool_address"] == "0xMockAeroPoolAddress"
        assert res["price"] == 2.0
        assert res["block"] == 12345


@pytest.mark.asyncio
async def test_get_onchain_price_unsupported_symbol() -> None:
    """Verify get_onchain_price returns error for unsupported symbols."""
    res = await get_onchain_price("UNKNOWN-SYMBOL")
    assert "error" in res
    assert "not supported" in res["error"]


@pytest.mark.asyncio
async def test_get_onchain_price_rpc_error_handling() -> None:
    """Verify get_onchain_price handles RPC errors gracefully."""
    with patch("crypcodile.mcp_server.AsyncWeb3") as mock_web3_class:
        mock_w3 = MagicMock()
        mock_w3.__aenter__ = AsyncMock(return_value=mock_w3)
        mock_w3.__aexit__ = AsyncMock(return_value=None)
        mock_web3_class.return_value = mock_w3
        mock_web3_class.to_checksum_address = lambda x: x

        # Simulate block_number lookup raising exception
        mock_w3.eth.block_number = AwaitableValue(Exception("Node connection refused"))

        res = await get_onchain_price("cbBTC-USDC")
        assert "error" in res
        assert "Failed fetching pool state" in res["error"]


@pytest.mark.asyncio
async def test_get_base_market_data_success() -> None:
    """Verify get_base_market_data successfully queries Uniswap V3 WETH/USDC pool and calculates 1h volume."""
    with patch("crypcodile.mcp_server.AsyncWeb3") as mock_web3_class:
        mock_w3 = MagicMock()
        mock_w3.__aenter__ = AsyncMock(return_value=mock_w3)
        mock_w3.__aexit__ = AsyncMock(return_value=None)
        mock_web3_class.return_value = mock_w3
        mock_web3_class.to_checksum_address = lambda x: x

        mock_w3.eth.block_number = AwaitableValue(12345)
        
        # Factory mock
        mock_factory = MagicMock()
        mock_factory.functions.getPool.return_value.call = AsyncMock(
            return_value="0xMockWethUsdcPoolAddress"
        )
        
        # Pool mock
        mock_pool = MagicMock()
        mock_pool.functions.slot0.return_value.call = AsyncMock(
            return_value=[int(2**96 * 40.0), 0, 0, 0, 0, 0, True]
        )
        mock_pool.functions.liquidity.return_value.call = AsyncMock(
            return_value=100 * 10**18
        )
        
        def contract_side_effect(address, abi):
            if address == "0x33128a8fC17869897dcE68Ed026d694621f6FDfD":
                return mock_factory
            return mock_pool

        mock_w3.eth.contract.side_effect = contract_side_effect

        # Mock swap logs return 1 mock swap log (is_flipped = False, WETH/USDC)
        mock_log = {
            "data": ((-2 * 10**18).to_bytes(32, byteorder='big', signed=True) +
                     (3200 * 10**6).to_bytes(32, byteorder='big', signed=True)),
            "transactionHash": MagicMock(hex=lambda: "0xhash"),
            "logIndex": 1,
            "blockNumber": 12345
        }
        mock_w3.eth.get_logs = AsyncMock(return_value=[mock_log])

        res = await get_base_market_data("WETH/USDC")
        assert res["symbol"] == "WETH-USDC"
        assert res["pool_address"] == "0xMockWethUsdcPoolAddress"
        assert res["volume_1h_base"] == 2.0
        assert res["volume_1h_quote"] == 3200.0
        assert res["num_swaps_1h"] == 1


@pytest.mark.asyncio
async def test_api_server_flow_direct() -> None:
    """Test the complete API server payment gateway flow by calling handlers directly."""
    PAYMENTS_DB.clear()
    
    from eth_account import Account
    from eth_account.messages import encode_defunct

    private_key = "0x" + "1" * 64
    account = Account.from_key(private_key)

    # 1. Access market data without payment signature -> should return payment required
    response = Response()
    res = await get_market_data(symbol="cbBTC-USDC", response=response, payment_signature=None)
    
    assert res["status"] == "payment_required"
    assert response.status_code == 402
    assert "Payment-Required" in response.headers
    
    payment_required_payload = json.loads(response.headers["Payment-Required"])
    payment_id = payment_required_payload["payment_id"]
    assert payment_id in PAYMENTS_DB
    
    # 2. Try to simulate payment for a non-existent payment ID -> should raise HTTPException 404
    msg_fail = encode_defunct(text="non-existent-id")
    sig_fail = account.sign_message(msg_fail).signature.hex()
    if not sig_fail.startswith("0x"):
        sig_fail = "0x" + sig_fail

    payload_fail = PaymentSignature(payment_id="non-existent-id", tx_hash="0x123", signature=sig_fail)
    with pytest.raises(HTTPException) as exc_info:
        await simulate_payment(payload_fail)
    assert exc_info.value.status_code == 404

    # 3. Simulate payment for the generated payment_id -> should return success
    msg_success = encode_defunct(text=payment_id)
    sig_success = account.sign_message(msg_success).signature.hex()
    if not sig_success.startswith("0x"):
        sig_success = "0x" + sig_success

    payload_success = PaymentSignature(payment_id=payment_id, tx_hash="0xmocktxhash", signature=sig_success)
    sim_res = await simulate_payment(payload_success)
    assert sim_res["status"] == "success"
    assert PAYMENTS_DB[payment_id]["status"] == "paid"

    # 4. Request market data with the signature header -> should return 200 with data
    with patch("crypcodile.api_server.get_onchain_price", new_callable=AsyncMock) as mock_get_price:
        mock_get_price.return_value = {
            "symbol": "cbBTC-USDC",
            "price": 40000.0,
            "block": 100
        }

        payment_sig_header = json.dumps({
            "payment_id": payment_id,
            "tx_hash": "0xmocktxhash",
            "signature": sig_success
        })

        response_success = Response()
        resp_res = await get_market_data(
            symbol="cbBTC-USDC",
            response=response_success,
            payment_signature=payment_sig_header
        )
        
        assert resp_res["status"] == "success"
        assert resp_res["data"]["price"] == 40000.0
        
        # Verify success header
        assert "Payment-Response" in response_success.headers
        resp_header_payload = json.loads(response_success.headers["Payment-Response"])
        assert resp_header_payload["payment_id"] == payment_id
        assert resp_header_payload["tx_hash"] == "0xmocktxhash"


@pytest.mark.asyncio
async def test_api_server_invalid_signature_direct() -> None:
    """Test API server handling of invalid signature format or values directly."""
    response = Response()
    with pytest.raises(HTTPException) as exc_info:
        await get_market_data(
            symbol="cbBTC-USDC",
            response=response,
            payment_signature="invalid_json"
        )
    assert exc_info.value.status_code == 400
    assert "Failed verifying payment signature" in exc_info.value.detail


@pytest.mark.asyncio
async def test_mcp_server_serve_stdio(tmp_path) -> None:
    """Verify serve_stdio reads stdin and writes correct JSON-RPC output to stdout."""
    # Build a sequence of JSON-RPC requests
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "get_onchain_price", "arguments": {"symbol": "cbBTC-USDC"}}
        }
    ]
    
    input_bytes = b"".join(json.dumps(r).encode() + b"\n" for r in requests)
    
    # We mock stream reader and sys.stdout
    mock_reader = asyncio.StreamReader()
    mock_reader.feed_data(input_bytes)
    mock_reader.feed_eof()
    
    # Mock sys.stdout.write and flush
    stdout_writes = []
    def mock_write(s):
        stdout_writes.append(s)
    
    loop = asyncio.get_running_loop()
    
    with patch("asyncio.StreamReader", return_value=mock_reader), \
         patch("sys.stdin", MagicMock()), \
         patch("sys.stdout.write", mock_write), \
         patch("sys.stdout.flush", MagicMock()), \
         patch.object(loop, "connect_read_pipe", new_callable=AsyncMock) as mock_connect, \
         patch("crypcodile.mcp_server.get_onchain_price", new_callable=AsyncMock) as mock_get_price:
         
         mock_get_price.return_value = {
             "symbol": "cbBTC-USDC",
             "price": 50000.0
         }
         mock_connect.return_value = (MagicMock(), MagicMock())
         
         # Run serve_stdio with a temporary folder
         await serve_stdio(data_dir=tmp_path)
         
         # Check the responses written to stdout
         assert len(stdout_writes) == 3
         
         resp_1 = json.loads(stdout_writes[0])
         assert resp_1["id"] == 1
         assert "protocolVersion" in resp_1["result"]
         
         resp_2 = json.loads(stdout_writes[1])
         assert resp_2["id"] == 2
         assert "tools" in resp_2["result"]
         
         resp_3 = json.loads(stdout_writes[2])
         assert resp_3["id"] == 3
         tool_content = json.loads(resp_3["result"]["content"][0]["text"])
         assert tool_content["symbol"] == "cbBTC-USDC"
         assert tool_content["price"] == 50000.0


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
async def test_successful_payment_verification() -> None:
    from crypcodile.api_server import get_market_data, PAYMENTS_DB
    from fastapi import Response
    
    PAYMENTS_DB.clear()
    
    response = Response()
    res = await get_market_data(symbol="cbBTC-USDC", response=response, payment_signature=None)
    payment_required_payload = json.loads(response.headers["Payment-Required"])
    payment_id = payment_required_payload["payment_id"]
    
    sig, address = generate_signature(payment_id)
    tx_hash = "0x" + "a" * 64
    
    with patch("crypcodile.api_server.get_w3") as mock_get_w3, \
         patch("crypcodile.api_server.get_onchain_price", new_callable=AsyncMock) as mock_get_price:
         
        mock_w3 = MagicMock()
        mock_w3.eth.chain_id = AwaitableValue(8453)
        
        mock_w3.eth.get_transaction = AsyncMock(return_value={
            "from": address,
            "chainId": 8453
        })
        
        mock_w3.eth.get_block = AsyncMock(return_value={
            "timestamp": 1000
        })
        
        mock_w3.eth.get_transaction_receipt = AsyncMock(return_value={
            "status": 1,
            "blockNumber": 100,
            "logs": [
                {
                    "address": "0x833589fCD6eDb6E08f4c7C32D4f71b54bda02913",
                    "topics": [
                        bytes.fromhex("ddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"),
                        bytes.fromhex("000000000000000000000000" + address[2:]),
                        bytes.fromhex("00000000000000000000000070997970C51812dc3A010C7d01b50e0d17dc79C8"[2:])
                    ],
                    "data": bytes.fromhex("00000000000000000000000000000000000000000000000000000000000003e8")
                }
            ]
        })
        
        mock_get_w3.return_value = mock_w3
        mock_get_price.return_value = {"symbol": "cbBTC-USDC", "price": 50000.0}
        
        payment_sig_header = json.dumps({
            "payment_id": payment_id,
            "tx_hash": tx_hash,
            "signature": sig
        })
        
        response_success = Response()
        resp_res = await get_market_data(
            symbol="cbBTC-USDC",
            response=response_success,
            payment_signature=payment_sig_header
        )
        
        assert resp_res["status"] == "success"
        assert resp_res["data"]["price"] == 50000.0
        assert PAYMENTS_DB[payment_id]["status"] == "spent"
        assert PAYMENTS_DB[payment_id]["sender"] == address


@pytest.mark.asyncio
async def test_signature_verification_failures() -> None:
    from crypcodile.api_server import get_market_data, PAYMENTS_DB
    from fastapi import Response, HTTPException
    
    PAYMENTS_DB.clear()
    
    response = Response()
    res = await get_market_data(symbol="cbBTC-USDC", response=response, payment_signature=None)
    payment_required_payload = json.loads(response.headers["Payment-Required"])
    payment_id = payment_required_payload["payment_id"]
    
    # Case A: Missing signature field in header
    payment_sig_header_missing = json.dumps({
        "payment_id": payment_id,
        "tx_hash": "0x123"
    })
    with pytest.raises(HTTPException) as exc_info:
        await get_market_data(symbol="cbBTC-USDC", response=Response(), payment_signature=payment_sig_header_missing)
    assert exc_info.value.status_code == 400
    
    # Case B: Malformed signature length
    payment_sig_header_malformed = json.dumps({
        "payment_id": payment_id,
        "tx_hash": "0x123",
        "signature": "0x123"
    })
    with pytest.raises(HTTPException) as exc_info:
        await get_market_data(symbol="cbBTC-USDC", response=Response(), payment_signature=payment_sig_header_malformed)
    assert exc_info.value.status_code == 400
    assert "Malformed signature" in exc_info.value.detail
    
    # Case C: Sender mismatch
    sig, address = generate_signature(payment_id)
    tx_hash = "0x" + "b" * 64
    
    with patch("crypcodile.api_server.get_w3") as mock_get_w3:
        mock_w3 = MagicMock()
        mock_w3.eth.chain_id = AwaitableValue(8453)
        mock_w3.eth.get_transaction_receipt = AsyncMock(return_value={"status": 1})
        mock_w3.eth.get_transaction = AsyncMock(return_value={
            "from": "0x" + "9" * 40,
            "chainId": 8453
        })
        mock_get_w3.return_value = mock_w3
        
        payment_sig_header_mismatch = json.dumps({
            "payment_id": payment_id,
            "tx_hash": tx_hash,
            "signature": sig
        })
        with pytest.raises(HTTPException) as exc_info:
            await get_market_data(symbol="cbBTC-USDC", response=Response(), payment_signature=payment_sig_header_mismatch)
        assert exc_info.value.status_code == 400
        assert "Payment signature does not match transaction sender" in exc_info.value.detail


@pytest.mark.asyncio
async def test_transaction_not_found_and_retries() -> None:
    from crypcodile.api_server import get_market_data, PAYMENTS_DB
    from fastapi import Response
    from web3.exceptions import TransactionNotFound
    
    PAYMENTS_DB.clear()
    
    response = Response()
    res = await get_market_data(symbol="cbBTC-USDC", response=response, payment_signature=None)
    payment_id = json.loads(response.headers["Payment-Required"])["payment_id"]
    
    sig, address = generate_signature(payment_id)
    tx_hash = "0x" + "c" * 64
    
    with patch("crypcodile.api_server.get_w3") as mock_get_w3, \
         patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep, \
         patch("crypcodile.api_server.get_onchain_price", new_callable=AsyncMock) as mock_get_price:
         
        mock_w3 = MagicMock()
        mock_w3.eth.chain_id = AwaitableValue(8453)
        mock_w3.eth.get_transaction = AsyncMock(return_value={"from": address, "chainId": 8453})
        mock_w3.eth.get_block = AsyncMock(return_value={"timestamp": 1000})
        
        mock_receipt = {
            "status": 1,
            "blockNumber": 100,
            "logs": [
                {
                    "address": "0x833589fCD6eDb6E08f4c7C32D4f71b54bda02913",
                    "topics": [
                        bytes.fromhex("ddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"),
                        bytes.fromhex("000000000000000000000000" + address[2:]),
                        bytes.fromhex("00000000000000000000000070997970C51812dc3A010C7d01b50e0d17dc79C8"[2:])
                    ],
                    "data": bytes.fromhex("00000000000000000000000000000000000000000000000000000000000003e8")
                }
            ]
        }
        
        mock_w3.eth.get_transaction_receipt = AsyncMock(side_effect=[
            TransactionNotFound("Not found"),
            mock_receipt
        ])
        
        mock_get_w3.return_value = mock_w3
        mock_get_price.return_value = {"symbol": "cbBTC-USDC", "price": 50000.0}
        
        payment_sig_header = json.dumps({
            "payment_id": payment_id,
            "tx_hash": tx_hash,
            "signature": sig
        })
        
        resp_res = await get_market_data(
            symbol="cbBTC-USDC",
            response=Response(),
            payment_signature=payment_sig_header
        )
        
        assert resp_res["status"] == "success"
        assert mock_sleep.call_count == 1


@pytest.mark.asyncio
async def test_rpc_fallback_failover() -> None:
    from crypcodile.api_server import app, get_market_data, PAYMENTS_DB, get_w3, AsyncHTTPProvider
    from fastapi import Response
    import os
    
    PAYMENTS_DB.clear()
    
    response = Response()
    res = await get_market_data(symbol="cbBTC-USDC", response=response, payment_signature=None)
    payment_id = json.loads(response.headers["Payment-Required"])["payment_id"]
    
    sig, address = generate_signature(payment_id)
    tx_hash = "0x" + "d" * 64
    
    os.environ["BASE_RPC_URLS"] = "http://dummy-rpc-1,http://dummy-rpc-2"
    
    if hasattr(app.state, "rpc_urls"):
        delattr(app.state, "rpc_urls")
    if hasattr(app.state, "w3"):
        delattr(app.state, "w3")
        
    call_count = 0
    async def mock_make_request(provider_self, method, params):
        nonlocal call_count
        if method == "eth_chainId":
            return {"jsonrpc": "2.0", "id": 1, "result": "0x2105"}
        elif method == "eth_getTransactionReceipt":
            call_count += 1
            if call_count == 1:
                raise Exception("HTTP Status Code 429: Too Many Requests")
            return {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "transactionHash": tx_hash,
                    "transactionIndex": "0x0",
                    "blockHash": "0x" + "0" * 64,
                    "blockNumber": "0x64",
                    "cumulativeGasUsed": "0x0",
                    "gasUsed": "0x0",
                    "contractAddress": None,
                    "logsBloom": "0x" + "0" * 512,
                    "status": "0x1",
                    "logs": [
                        {
                            "logIndex": "0x0",
                            "transactionIndex": "0x0",
                            "transactionHash": tx_hash,
                            "blockHash": "0x" + "0" * 64,
                            "blockNumber": "0x64",
                            "address": "0x833589fCD6eDb6E08f4c7C32D4f71b54bda02913",
                            "topics": [
                                "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef",
                                "0x" + "0" * 24 + (address[2:] if address.startswith("0x") else address).lower(),
                                "0x" + "0" * 24 + "70997970C51812dc3A010C7d01b50e0d17dc79C8".lower()
                            ],
                            "data": "0x" + "0" * 60 + "3e8"
                        }
                    ]
                }
            }
        elif method == "eth_getTransactionByHash":
            return {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "hash": tx_hash,
                    "nonce": "0x0",
                    "blockHash": "0x" + "0" * 64,
                    "blockNumber": "0x64",
                    "transactionIndex": "0x0",
                    "from": address,
                    "to": "0x" + "0" * 40,
                    "value": "0x0",
                    "gas": "0x0",
                    "gasPrice": "0x0",
                    "input": "0x",
                    "chainId": "0x2105"
                }
            }
        elif method == "eth_getBlockByNumber":
            return {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "number": "0x64",
                    "hash": "0x" + "0" * 64,
                    "parentHash": "0x" + "0" * 64,
                    "nonce": "0x0",
                    "sha3Uncles": "0x" + "0" * 64,
                    "logsBloom": "0x" + "0" * 512,
                    "transactionsRoot": "0x" + "0" * 64,
                    "stateRoot": "0x" + "0" * 64,
                    "receiptsRoot": "0x" + "0" * 64,
                    "miner": "0x" + "0" * 40,
                    "difficulty": "0x0",
                    "totalDifficulty": "0x0",
                    "extraData": "0x",
                    "size": "0x0",
                    "gasLimit": "0x0",
                    "gasUsed": "0x0",
                    "timestamp": "0x3e8",
                    "uncles": []
                }
            }
        return {"jsonrpc": "2.0", "id": 1, "result": None}

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep, \
         patch("crypcodile.api_server.get_onchain_price", new_callable=AsyncMock) as mock_get_price, \
         patch.object(AsyncHTTPProvider, "make_request", mock_make_request):
         
        w3 = get_w3()
        mock_get_price.return_value = {"symbol": "cbBTC-USDC", "price": 50000.0}
        
        payment_sig_header = json.dumps({
            "payment_id": payment_id,
            "tx_hash": tx_hash,
            "signature": sig
        })
        
        assert app.state.rpc_urls == ["http://dummy-rpc-1", "http://dummy-rpc-2"]
        assert app.state.current_rpc_index == 0
        
        resp_res = await get_market_data(
            symbol="cbBTC-USDC",
            response=Response(),
            payment_signature=payment_sig_header
        )
        
        assert resp_res["status"] == "success"
        assert app.state.current_rpc_index == 1
        assert w3.provider.endpoint_uri == "http://dummy-rpc-2"
        
    del os.environ["BASE_RPC_URLS"]
    if hasattr(app.state, "rpc_urls"):
        delattr(app.state, "rpc_urls")
    if hasattr(app.state, "w3"):
        delattr(app.state, "w3")


@pytest.mark.asyncio
async def test_concurrency_lock_validation() -> None:
    from crypcodile.api_server import get_market_data, PAYMENTS_DB, VERIFYING_TXS
    from fastapi import Response, HTTPException
    
    PAYMENTS_DB.clear()
    VERIFYING_TXS.clear()
    
    response1 = Response()
    res1 = await get_market_data(symbol="cbBTC-USDC", response=response1, payment_signature=None)
    payment_id1 = json.loads(response1.headers["Payment-Required"])["payment_id"]
    
    response2 = Response()
    res2 = await get_market_data(symbol="cbBTC-USDC", response=response2, payment_signature=None)
    payment_id2 = json.loads(response2.headers["Payment-Required"])["payment_id"]
    
    sig1, address = generate_signature(payment_id1)
    sig2, _ = generate_signature(payment_id2)
    tx_hash = "0x" + "e" * 64
    
    with patch("crypcodile.api_server.get_w3") as mock_get_w3:
        mock_w3 = MagicMock()
        mock_w3.eth.chain_id = AwaitableValue(8453)
        mock_w3.eth.get_transaction = AsyncMock(return_value={"from": address, "chainId": 8453})
        mock_w3.eth.get_block = AsyncMock(return_value={"timestamp": 1000})
        
        async def delayed_receipt(*args, **kwargs):
            await asyncio.sleep(0.1)
            return {
                "status": 1,
                "blockNumber": 100,
                "logs": [
                    {
                        "address": "0x833589fCD6eDb6E08f4c7C32D4f71b54bda02913",
                        "topics": [
                            bytes.fromhex("ddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"),
                            bytes.fromhex("000000000000000000000000" + address[2:]),
                            bytes.fromhex("00000000000000000000000070997970C51812dc3A010C7d01b50e0d17dc79C8"[2:])
                        ],
                        "data": bytes.fromhex("00000000000000000000000000000000000000000000000000000000000003e8")
                    }
                ]
            }
            
        mock_w3.eth.get_transaction_receipt = delayed_receipt
        mock_get_w3.return_value = mock_w3
        
        payment_sig_header1 = json.dumps({
            "payment_id": payment_id1,
            "tx_hash": tx_hash,
            "signature": sig1
        })
        payment_sig_header2 = json.dumps({
            "payment_id": payment_id2,
            "tx_hash": tx_hash,
            "signature": sig2
        })
        
        t1 = asyncio.create_task(get_market_data(
            symbol="cbBTC-USDC",
            response=Response(),
            payment_signature=payment_sig_header1
        ))
        
        await asyncio.sleep(0.02)
        
        with pytest.raises(HTTPException) as exc_info:
            await get_market_data(
                symbol="cbBTC-USDC",
                response=Response(),
                payment_signature=payment_sig_header2
            )
            
        assert exc_info.value.status_code == 400
        assert "Transaction hash is currently being verified" in exc_info.value.detail
        
        with patch("crypcodile.api_server.get_onchain_price", new_callable=AsyncMock) as mock_get_price:
            mock_get_price.return_value = {"symbol": "cbBTC-USDC", "price": 50000.0}
            resp_res = await t1
            assert resp_res["status"] == "success"


async def test_api_server_metrics_endpoint() -> None:
    """Verify that the /metrics endpoint returns standard Prometheus exposition metrics and tracks usage."""
    from crypcodile.api_server import metrics, METRICS_METRICS_REQUESTS
    
    # Track initial metrics requests count
    init_count = METRICS_METRICS_REQUESTS
    
    resp = await metrics()
    assert resp.status_code == 200
    content = resp.body.decode()
    
    assert "process_cpu_seconds_total" in content
    assert "process_resident_memory_bytes" in content
    assert "crypcodile_uptime_seconds" in content
    assert "crypcodile_api_requests_total" in content
    assert "crypcodile_payments_total" in content
    
    from crypcodile.api_server import METRICS_METRICS_REQUESTS as new_count
    assert new_count == init_count + 1


@pytest.mark.asyncio
async def test_cas_concurrent_double_serve_prevention() -> None:
    """CAS paid→spent under lock: only one concurrent serve may succeed per payment_id."""
    from eth_account import Account
    from eth_account.messages import encode_defunct
    from crypcodile.api_server import db_lock

    PAYMENTS_DB.clear()

    private_key = "0x" + "3" * 64
    account = Account.from_key(private_key)
    pid = "cas-double-serve-pid"
    tx_hash = "0xcas_double_serve_tx"

    msg = encode_defunct(text=pid)
    sig = account.sign_message(msg).signature.hex()
    if not sig.startswith("0x"):
        sig = "0x" + sig

    async with db_lock:
        await PAYMENTS_DB.set_async(pid, {
            "status": "paid",
            "symbol": "cbBTC-USDC",
            "tx_hash": tx_hash,
            "sender": account.address,
            "signature": sig,
            "price": "0.001",
            "currency": "USDC",
            "recipient": "0x70997970C51812dc3A010C7d01b50e0d17dc79C8",
        })

    payment_sig_header = json.dumps({
        "payment_id": pid,
        "tx_hash": tx_hash,
        "signature": sig,
    })

    serve_started = asyncio.Event()
    release_serve = asyncio.Event()

    async def slow_price(symbol, rpc_url=None):
        serve_started.set()
        await release_serve.wait()
        return {"symbol": symbol, "price": 42000.0, "block": 1}

    with patch("crypcodile.api_server.get_onchain_price", side_effect=slow_price):
        t1 = asyncio.create_task(
            get_market_data(
                symbol="cbBTC-USDC",
                response=Response(),
                payment_signature=payment_sig_header,
            )
        )
        # Wait until first request has passed CAS and is serving
        await asyncio.wait_for(serve_started.wait(), timeout=2.0)

        # Second concurrent serve must fail — payment already spent by CAS
        with pytest.raises(HTTPException) as exc_info:
            await get_market_data(
                symbol="cbBTC-USDC",
                response=Response(),
                payment_signature=payment_sig_header,
            )
        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == "Payment already spent."

        release_serve.set()
        resp1 = await t1
        assert resp1["status"] == "success"
        assert resp1["data"]["price"] == 42000.0

    record = await PAYMENTS_DB.get_async(pid)
    assert record["status"] == "spent"


@pytest.mark.asyncio
async def test_admin_payments_protection() -> None:
    """Admin payments endpoint: 404 if unset, 401 if wrong key, 200 if correct."""
    import os
    from crypcodile.api_server import get_all_payments, db_lock

    PAYMENTS_DB.clear()
    async with db_lock:
        await PAYMENTS_DB.set_async("admin-test-pid", {"status": "paid"})

    prev_key = os.environ.get("ADMIN_API_KEY")
    try:
        # Unset ADMIN_API_KEY → 404
        os.environ.pop("ADMIN_API_KEY", None)
        with pytest.raises(HTTPException) as exc_404:
            await get_all_payments()
        assert exc_404.value.status_code == 404

        # Set key, missing/wrong credentials → 401
        os.environ["ADMIN_API_KEY"] = "super-secret-admin-key"

        with pytest.raises(HTTPException) as exc_missing:
            await get_all_payments()
        assert exc_missing.value.status_code == 401

        with pytest.raises(HTTPException) as exc_wrong:
            await get_all_payments(x_admin_key="wrong-key")
        assert exc_wrong.value.status_code == 401

        with pytest.raises(HTTPException) as exc_wrong_bearer:
            await get_all_payments(authorization="Bearer wrong-key")
        assert exc_wrong_bearer.value.status_code == 401

        # Correct X-Admin-Key → 200 / full DB
        result = await get_all_payments(x_admin_key="super-secret-admin-key")
        assert "admin-test-pid" in result
        assert result["admin-test-pid"]["status"] == "paid"

        # Correct Bearer token → 200
        result_bearer = await get_all_payments(authorization="Bearer super-secret-admin-key")
        assert "admin-test-pid" in result_bearer
    finally:
        if prev_key is None:
            os.environ.pop("ADMIN_API_KEY", None)
        else:
            os.environ["ADMIN_API_KEY"] = prev_key


@pytest.mark.asyncio
async def test_simulate_payment_disabled_without_env() -> None:
    """simulate_payment must reject when ALLOW_SIMULATION is not true."""
    import os
    from eth_account import Account
    from eth_account.messages import encode_defunct
    from crypcodile.api_server import PaymentSignature

    prev_sim = os.environ.get("ALLOW_SIMULATION")
    try:
        os.environ["ALLOW_SIMULATION"] = "false"

        private_key = "0x" + "4" * 64
        account = Account.from_key(private_key)
        pid = "sim-disabled-pid"
        msg = encode_defunct(text=pid)
        sig = account.sign_message(msg).signature.hex()
        if not sig.startswith("0x"):
            sig = "0x" + sig

        payload = PaymentSignature(payment_id=pid, tx_hash="0xsim", signature=sig)
        with pytest.raises(HTTPException) as exc_info:
            await simulate_payment(payload)
        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == "Simulation mode is disabled."

        # Also when unset (default false)
        os.environ.pop("ALLOW_SIMULATION", None)
        with pytest.raises(HTTPException) as exc_info2:
            await simulate_payment(payload)
        assert exc_info2.value.status_code == 400
        assert exc_info2.value.detail == "Simulation mode is disabled."
    finally:
        if prev_sim is None:
            os.environ.pop("ALLOW_SIMULATION", None)
        else:
            os.environ["ALLOW_SIMULATION"] = prev_sim


@pytest.mark.asyncio
async def test_simulate_payment_rejects_paid_and_spent() -> None:
    """simulate_payment only allows pending → paid; paid/spent return 400."""
    from eth_account import Account
    from eth_account.messages import encode_defunct
    from crypcodile.api_server import PaymentSignature, db_lock

    PAYMENTS_DB.clear()

    private_key = "0x" + "5" * 64
    account = Account.from_key(private_key)

    # --- paid record: second simulate must fail ---
    response = Response()
    await get_market_data(symbol="cbBTC-USDC", response=response, payment_signature=None)
    payment_id = json.loads(response.headers["Payment-Required"])["payment_id"]

    msg = encode_defunct(text=payment_id)
    sig = account.sign_message(msg).signature.hex()
    if not sig.startswith("0x"):
        sig = "0x" + sig

    payload = PaymentSignature(
        payment_id=payment_id, tx_hash="0xsim_paid_once", signature=sig
    )
    sim_res = await simulate_payment(payload)
    assert sim_res["status"] == "success"
    assert PAYMENTS_DB[payment_id]["status"] == "paid"

    with pytest.raises(HTTPException) as exc_paid:
        await simulate_payment(payload)
    assert exc_paid.value.status_code == 400
    assert exc_paid.value.detail == "Payment already processed."
    assert PAYMENTS_DB[payment_id]["status"] == "paid"

    # --- spent record: simulate must fail ---
    spent_pid = "sim-spent-pid"
    spent_msg = encode_defunct(text=spent_pid)
    spent_sig = account.sign_message(spent_msg).signature.hex()
    if not spent_sig.startswith("0x"):
        spent_sig = "0x" + spent_sig

    async with db_lock:
        await PAYMENTS_DB.set_async(spent_pid, {
            "status": "spent",
            "symbol": "cbBTC-USDC",
            "tx_hash": "0xsim_spent_tx",
            "sender": account.address,
            "signature": spent_sig,
            "price": "0.001",
            "currency": "USDC",
            "recipient": "0x70997970C51812dc3A010C7d01b50e0d17dc79C8",
        })

    spent_payload = PaymentSignature(
        payment_id=spent_pid, tx_hash="0xsim_spent_new", signature=spent_sig
    )
    with pytest.raises(HTTPException) as exc_spent:
        await simulate_payment(spent_payload)
    assert exc_spent.value.status_code == 400
    assert exc_spent.value.detail == "Payment already processed."
    assert PAYMENTS_DB[spent_pid]["status"] == "spent"


@pytest.mark.asyncio
async def test_spent_cannot_be_repaid_via_verify_path() -> None:
    """Spent records cannot be re-paid: early gate + pending-only write after verify."""
    from crypcodile.api_server import db_lock, VERIFYING_TXS

    PAYMENTS_DB.clear()
    VERIFYING_TXS.clear()

    # --- A: spent at entry → Payment already spent (never re-verify) ---
    response = Response()
    await get_market_data(symbol="cbBTC-USDC", response=response, payment_signature=None)
    payment_id = json.loads(response.headers["Payment-Required"])["payment_id"]
    sig, address = generate_signature(payment_id)
    tx_hash = "0x" + "e" * 64

    async with db_lock:
        rec = await PAYMENTS_DB.get_async(payment_id)
        rec["status"] = "spent"
        rec["tx_hash"] = tx_hash
        rec["sender"] = address
        rec["signature"] = sig
        await PAYMENTS_DB.set_async(payment_id, rec)

    payment_sig_header = json.dumps({
        "payment_id": payment_id,
        "tx_hash": tx_hash,
        "signature": sig,
    })
    with pytest.raises(HTTPException) as exc_spent:
        await get_market_data(
            symbol="cbBTC-USDC",
            response=Response(),
            payment_signature=payment_sig_header,
        )
    assert exc_spent.value.status_code == 400
    assert exc_spent.value.detail == "Payment already spent."
    assert PAYMENTS_DB[payment_id]["status"] == "spent"

    # --- B: status flipped to spent during on-chain verify → pending-only write rejects ---
    PAYMENTS_DB.clear()
    VERIFYING_TXS.clear()

    response2 = Response()
    await get_market_data(symbol="cbBTC-USDC", response=response2, payment_signature=None)
    payment_id2 = json.loads(response2.headers["Payment-Required"])["payment_id"]
    sig2, address2 = generate_signature(payment_id2)
    tx_hash2 = "0x" + "f" * 64

    mock_receipt = {
        "status": 1,
        "blockNumber": 100,
        "logs": [
            {
                "address": "0x833589fCD6eDb6E08f4c7C32D4f71b54bda02913",
                "topics": [
                    bytes.fromhex("ddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"),
                    bytes.fromhex("000000000000000000000000" + address2[2:]),
                    bytes.fromhex(
                        "00000000000000000000000070997970C51812dc3A010C7d01b50e0d17dc79C8"[2:]
                    ),
                ],
                "data": bytes.fromhex(
                    "00000000000000000000000000000000000000000000000000000000000003e8"
                ),
            }
        ],
    }

    async def flip_to_spent_then_receipt(*args, **kwargs):
        async with db_lock:
            rec = await PAYMENTS_DB.get_async(payment_id2)
            rec["status"] = "spent"
            await PAYMENTS_DB.set_async(payment_id2, rec)
        return mock_receipt

    with patch("crypcodile.api_server.get_w3") as mock_get_w3:
        mock_w3 = MagicMock()
        mock_w3.eth.chain_id = AwaitableValue(8453)
        mock_w3.eth.get_transaction = AsyncMock(
            return_value={"from": address2, "chainId": 8453}
        )
        mock_w3.eth.get_block = AsyncMock(return_value={"timestamp": 1000})
        mock_w3.eth.get_transaction_receipt = AsyncMock(
            side_effect=flip_to_spent_then_receipt
        )
        mock_get_w3.return_value = mock_w3

        payment_sig_header2 = json.dumps({
            "payment_id": payment_id2,
            "tx_hash": tx_hash2,
            "signature": sig2,
        })
        with pytest.raises(HTTPException) as exc_repay:
            await get_market_data(
                symbol="cbBTC-USDC",
                response=Response(),
                payment_signature=payment_sig_header2,
            )
        assert exc_repay.value.status_code == 400
        assert exc_repay.value.detail == "Payment already processed."
        assert PAYMENTS_DB[payment_id2]["status"] == "spent"
