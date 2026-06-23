from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
import pytest
from web3 import AsyncWeb3

from crypcodile.analytics.whale_transfers import WhaleTransferTracker


@pytest.mark.asyncio
async def test_whale_transfer_tracker() -> None:
    # Setup mock w3
    mock_w3 = MagicMock(spec=AsyncWeb3)
    # Mock to_checksum_address to act normally
    mock_w3.to_checksum_address = lambda x: AsyncWeb3.to_checksum_address(x)

    # 1. Prepare dummy logs:
    # Log 1: $150k transfer (above $100k threshold)
    # Log 2: $50k transfer (below $100k threshold)
    # Log 3: Invalid log (short topics list)
    tx_hash1 = b"\x01" * 32
    tx_hash2 = b"\x02" * 32

    # Amount for log 1: 150,000 WETH (if price=1, decimals=18)
    # Wait, if price = 3000, 50 WETH = $150k.
    # 50 WETH in raw: 50 * 10^18
    raw_amount1 = 50 * 10**18
    # 10 WETH in raw = $30k (below threshold)
    raw_amount2 = 10 * 10**18

    # Pad addresses to 32 bytes for topics
    from_addr = bytes.fromhex("0000000000000000000000001111111111111111111111111111111111111111")
    to_addr = bytes.fromhex("0000000000000000000000002222222222222222222222222222222222222222")

    mock_logs = [
        # Whale transfer
        {
            "topics": [
                bytes.fromhex("ddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"),
                from_addr,
                to_addr,
            ],
            "data": raw_amount1.to_bytes(32, "big"),
            "transactionHash": tx_hash1,
            "blockNumber": 12345,
        },
        # Small transfer
        {
            "topics": [
                bytes.fromhex("ddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"),
                from_addr,
                to_addr,
            ],
            "data": raw_amount2.to_bytes(32, "big"),
            "transactionHash": tx_hash2,
            "blockNumber": 12346,
        },
        # Invalid transfer (no recipient)
        {
            "topics": [
                bytes.fromhex("ddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"),
                from_addr,
            ],
            "data": raw_amount1.to_bytes(32, "big"),
            "transactionHash": tx_hash1,
            "blockNumber": 12347,
        },
    ]

    mock_w3.eth = MagicMock()
    mock_w3.eth.get_logs = AsyncMock(return_value=mock_logs)

    token_addr = "0xC02aaA39b223FE8D0A0e5C4F27ead9083C756Cc2"
    tracker = WhaleTransferTracker(
        w3=mock_w3,
        token_address=token_addr,
        token_price_usd=3000.0,
        usd_threshold=100000.0,
        decimals=18,
    )

    results = await tracker.get_whale_transfers(12000, 13000)

    # Verify calls
    mock_w3.eth.get_logs.assert_called_once_with({
        "address": AsyncWeb3.to_checksum_address(token_addr),
        "topics": [tracker.transfer_topic],
        "fromBlock": 12000,
        "toBlock": 13000,
    })

    # Verify results
    assert len(results) == 1
    whale = results[0]
    assert whale["transaction_hash"] == tx_hash1.hex()
    assert whale["block_number"] == 12345
    assert whale["from"] == AsyncWeb3.to_checksum_address("0x1111111111111111111111111111111111111111")
    assert whale["to"] == AsyncWeb3.to_checksum_address("0x2222222222222222222222222222222222222222")
    assert whale["amount"] == 50.0
    assert whale["usd_value"] == 150000.0
