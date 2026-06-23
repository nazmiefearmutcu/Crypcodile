import pytest
from unittest.mock import AsyncMock, MagicMock
from web3.exceptions import ContractLogicError
from crypcodile.exchanges.base_onchain.revert_parser import RevertReasonParser
import eth_abi
import eth_utils

@pytest.mark.asyncio
async def test_revert_reason_parser_standard():
    w3 = AsyncMock()
    abi_reg = AsyncMock()
    
    mock_tx = {
        "from": "0x1111111111111111111111111111111111111111",
        "to": "0x2222222222222222222222222222222222222222",
        "input": "0x1234",
        "value": 0,
        "gas": 100000,
    }
    w3.eth.get_transaction.return_value = mock_tx
    
    revert_msg = "SlippageExceeded"
    payload = eth_abi.encode(["string"], [revert_msg])
    raw_data = "0x08c379a0" + payload.hex()
    
    w3.eth.call.side_effect = ContractLogicError(data=raw_data)
    
    parser = RevertReasonParser(w3, abi_reg)
    res = await parser.parse_revert_reason("0xtxhash", 100)
    assert revert_msg in res

@pytest.mark.asyncio
async def test_revert_reason_parser_panic():
    w3 = AsyncMock()
    abi_reg = AsyncMock()
    
    mock_tx = {
        "from": "0x1111111111111111111111111111111111111111",
        "to": "0x2222222222222222222222222222222222222222",
    }
    w3.eth.get_transaction.return_value = mock_tx
    
    payload = eth_abi.encode(["uint256"], [0x11])
    raw_data = "0x4e487b71" + payload.hex()
    
    w3.eth.call.side_effect = ContractLogicError(data=raw_data)
    
    parser = RevertReasonParser(w3, abi_reg)
    res = await parser.parse_revert_reason("0xtxhash", 100)
    assert "Arithmetic overflow" in res

@pytest.mark.asyncio
async def test_revert_reason_parser_custom_error():
    w3 = AsyncMock()
    abi_reg = AsyncMock()
    
    contract_addr = "0x2222222222222222222222222222222222222222"
    mock_tx = {
        "from": "0x1111111111111111111111111111111111111111",
        "to": contract_addr,
    }
    w3.eth.get_transaction.return_value = mock_tx
    
    sig = "CustomError(uint256,string)"
    selector = eth_utils.keccak(text=sig)[:4]
    
    payload = eth_abi.encode(["uint256", "string"], [404, "Not Found"])
    raw_data = "0x" + selector.hex() + payload.hex()
    
    w3.eth.call.side_effect = ContractLogicError(data=raw_data)
    
    mock_abi = [{
        "type": "error",
        "name": "CustomError",
        "inputs": [
            {"name": "code", "type": "uint256"},
            {"name": "reason", "type": "string"}
        ]
    }]
    abi_reg.get_abi.return_value = mock_abi
    
    parser = RevertReasonParser(w3, abi_reg)
    res = await parser.parse_revert_reason("0xtxhash", 100)
    assert "CustomError" in res
    assert "code=404" in res
    assert "reason=Not Found" in res
