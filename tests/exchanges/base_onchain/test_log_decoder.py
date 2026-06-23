import eth_abi
from eth_utils import to_checksum_address
import pytest
from crypcodile.log_decoder.fallback import (
    decode_uniswap_v3_swap as fallback_v3,
    decode_aerodrome_v2_swap as fallback_v2,
)

try:
    from crypcodile.log_decoder._rust_decoder import (
        decode_uniswap_v3_swap as rust_v3,
        decode_aerodrome_v2_swap as rust_v2,
    )
    HAS_RUST = True
except ImportError:
    HAS_RUST = False

def test_uniswap_v3_swap_decoders():
    sender = to_checksum_address("0x1111111111111111111111111111111111111111")
    recipient = to_checksum_address("0x2222222222222222222222222222222222222222")
    
    topics = [
        "0xc42079f94a6350d7e6235f29174924f9287a20ac8e91c97b870daEE5297F6e85",
        "0x000000000000000000000000" + sender[2:],
        "0x000000000000000000000000" + recipient[2:]
    ]
    
    amount0 = -1000000000000000000
    amount1 = 2500000000000000000000
    sqrtPriceX96 = 79228162514264337593543950336
    liquidity = 15000000000000000000
    tick = 195000
    
    data_bytes = eth_abi.encode(
        ['int256', 'int256', 'uint160', 'uint128', 'int24'],
        [amount0, amount1, sqrtPriceX96, liquidity, tick]
    )
    data_hex = "0x" + data_bytes.hex()
    
    res_fallback = fallback_v3(topics, data_hex)
    assert res_fallback["sender"] == sender
    assert res_fallback["recipient"] == recipient
    assert res_fallback["amount0"] == amount0
    assert res_fallback["amount1"] == amount1
    assert res_fallback["sqrtPriceX96"] == sqrtPriceX96
    assert res_fallback["liquidity"] == liquidity
    assert res_fallback["tick"] == tick
    
    if HAS_RUST:
        res_rust = rust_v3(topics, data_hex)
        assert res_rust == res_fallback

def test_aerodrome_v2_swap_decoders():
    sender = to_checksum_address("0x3333333333333333333333333333333333333333")
    recipient = to_checksum_address("0x4444444444444444444444444444444444444444")
    
    topics = [
        "0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822",
        "0x000000000000000000000000" + sender[2:],
        "0x000000000000000000000000" + recipient[2:]
    ]
    
    amount0In = 1000000000
    amount1In = 0
    amount0Out = 0
    amount1Out = 2000000000
    
    data_bytes = eth_abi.encode(
        ['uint256', 'uint256', 'uint256', 'uint256'],
        [amount0In, amount1In, amount0Out, amount1Out]
    )
    data_hex = "0x" + data_bytes.hex()
    
    res_fallback = fallback_v2(topics, data_hex)
    assert res_fallback["sender"] == sender
    assert res_fallback["recipient"] == recipient
    assert res_fallback["amount0In"] == amount0In
    assert res_fallback["amount1In"] == amount1In
    assert res_fallback["amount0Out"] == amount0Out
    assert res_fallback["amount1Out"] == amount1Out
    
    if HAS_RUST:
        res_rust = rust_v2(topics, data_hex)
        assert res_rust == res_fallback

def test_invalid_log_handling():
    with pytest.raises(ValueError):
        fallback_v3(["0x123"], "0x123")
