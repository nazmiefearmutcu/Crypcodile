import eth_abi
from eth_utils import to_checksum_address
from crypcodile.exchanges.base_onchain.limit_orders import (
    decode_1inch_order_filled,
    decode_0x_limit_order_filled,
)

def test_decode_1inch_order_filled():
    maker = to_checksum_address("0x1111111111111111111111111111111111111111")
    taker = to_checksum_address("0x2222222222222222222222222222222222222222")
    maker_token = to_checksum_address("0x833589fCD6eDb6E08f4c7C32D4f71b54bda02913")
    
    order_hash = b"\xaa" * 32
    remaining = 5000
    maker_amount = 1000000000
    taker_amount = 2000000000
    
    data_bytes = eth_abi.encode(
        ["bytes32", "address", "address", "uint256", "uint256", "uint256"],
        [order_hash, maker, taker, remaining, maker_amount, taker_amount]
    )
    topics = ["0x09da1b238d7fe6167120df0559f635c02b2176435f0de367098e945c73d9d300"]
    
    receipt = {
        "logs": [
            {
                "topics": [
                    b"\xdd\xf2\x52\xad\x1b\xe2\xc8\x9b\x69\xc2\xb0\x68\xfc\x37\x8d\xaa\x95\x2b\xa7\xf1\x63\xc4\xa1\x16\x28\xf5\x5a\x4d\xf5\x23\xb3\xef",
                    b"\x00" * 12 + bytes.fromhex(maker[2:]),
                    b"\x00" * 32,
                ],
                "address": maker_token
            }
        ]
    }
    
    res = decode_1inch_order_filled(topics, "0x" + data_bytes.hex(), receipt)
    assert res["protocol"] == "1inch"
    assert res["maker"] == maker
    assert res["taker"] == taker
    assert res["maker_token"] == maker_token
    assert res["maker_amount"] == float(maker_amount)
    assert res["taker_amount"] == float(taker_amount)
    assert res["order_hash"] == "0x" + order_hash.hex()

def test_decode_0x_limit_order_filled():
    maker = to_checksum_address("0x3333333333333333333333333333333333333333")
    taker = to_checksum_address("0x4444444444444444444444444444444444444444")
    maker_token = to_checksum_address("0x5555555555555555555555555555555555555555")
    taker_token = to_checksum_address("0x6666666666666666666666666666666666666666")
    maker_amount = 1500000000000000000
    taker_amount = 3000000000000000000
    order_hash = b"\xbb" * 32
    
    data_bytes = eth_abi.encode(
        ["address", "address", "address", "address", "uint128", "uint128", "bytes32"],
        [maker, taker, maker_token, taker_token, maker_amount, taker_amount, order_hash]
    )
    topics = ["0xab61feda74a9d45f448651a5c6819eb6085a6b0c265a7df498d5c328db9403d1"]
    
    res = decode_0x_limit_order_filled(topics, "0x" + data_bytes.hex())
    assert res["protocol"] == "0x"
    assert res["maker"] == maker
    assert res["taker"] == taker
    assert res["maker_token"] == maker_token
    assert res["taker_token"] == taker_token
    assert res["maker_amount"] == float(maker_amount)
    assert res["taker_amount"] == float(taker_amount)
    assert res["order_hash"] == "0x" + order_hash.hex()
