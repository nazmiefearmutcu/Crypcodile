from typing import Any
import eth_abi
from eth_utils import to_checksum_address

def decode_uniswap_v3_swap(topics: list[str], data: str) -> dict[str, Any]:
    """
    Decodes Uniswap V3 Swap log.
    Topics: [SwapTopic, sender_address, recipient_address]
    Data: amount0 (int256), amount1 (int256), sqrtPriceX96 (uint160), liquidity (uint128), tick (int24)
    """
    if len(topics) < 3:
        raise ValueError("Uniswap V3 Swap log must have at least 3 topics (SwapTopic, sender, recipient)")
    
    # Extract addresses from topics
    sender = to_checksum_address("0x" + topics[1][-40:])
    recipient = to_checksum_address("0x" + topics[2][-40:])
    
    # Strip 0x if present
    data_hex = data[2:] if data.startswith("0x") else data
    try:
        data_bytes = bytes.fromhex(data_hex)
    except ValueError as e:
        raise ValueError(f"Invalid hex data: {e}")
        
    try:
        amount0, amount1, sqrtPriceX96, liquidity, tick = eth_abi.decode(
            ['int256', 'int256', 'uint160', 'uint128', 'int24'],
            data_bytes
        )
    except Exception as e:
        raise ValueError(f"Failed to decode Uniswap V3 Swap log data: {e}")
        
    return {
        "sender": sender,
        "recipient": recipient,
        "amount0": amount0,
        "amount1": amount1,
        "sqrtPriceX96": sqrtPriceX96,
        "liquidity": liquidity,
        "tick": tick
    }

def decode_aerodrome_v2_swap(topics: list[str], data: str) -> dict[str, Any]:
    """
    Decodes Aerodrome V2/Uniswap V2 Swap log.
    Topics: [SwapTopic, sender_address, recipient_address]
    Data: amount0In (uint256), amount1In (uint256), amount0Out (uint256), amount1Out (uint256)
    """
    if len(topics) < 3:
        raise ValueError("Aerodrome V2 Swap log must have at least 3 topics (SwapTopic, sender, recipient)")
        
    sender = to_checksum_address("0x" + topics[1][-40:])
    recipient = to_checksum_address("0x" + topics[2][-40:])
    
    data_hex = data[2:] if data.startswith("0x") else data
    try:
        data_bytes = bytes.fromhex(data_hex)
    except ValueError as e:
        raise ValueError(f"Invalid hex data: {e}")
        
    try:
        amount0In, amount1In, amount0Out, amount1Out = eth_abi.decode(
            ['uint256', 'uint256', 'uint256', 'uint256'],
            data_bytes
        )
    except Exception as e:
        raise ValueError(f"Failed to decode Aerodrome V2 Swap log data: {e}")
        
    return {
        "sender": sender,
        "recipient": recipient,
        "amount0In": amount0In,
        "amount1In": amount1In,
        "amount0Out": amount0Out,
        "amount1Out": amount1Out
    }
