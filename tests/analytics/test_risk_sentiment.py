from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from web3 import AsyncWeb3

import pytest

from crypcodile.analytics.risk import calculate_chaos_score, calculate_dynamic_chaos_score
from crypcodile.analytics.smart_money import SmartMoneyTracker
from crypcodile.analytics.whale_transfers import WhaleTransferTracker


def test_calculate_chaos_score_sentiment() -> None:
    score = calculate_chaos_score(0.02, 0.001, -0.2, 2.0)
    assert 0.0 <= score <= 100.0


def test_calculate_dynamic_chaos_score_sentiment() -> None:
    score = calculate_dynamic_chaos_score(
        volatility=0.05,
        stablecoin_deviation=0.002,
        orderbook_imbalance=0.1,
        sequencer_delay=1.0,
    )
    assert 0.0 <= score <= 100.0


@pytest.mark.asyncio
async def test_whale_transfer_sentiment() -> None:
    mock_w3 = MagicMock(spec=AsyncWeb3)
    mock_w3.to_checksum_address = lambda x: AsyncWeb3.to_checksum_address(x)

    tx_hash = b"\x01" * 32
    from_addr = bytes.fromhex("0000000000000000000000001111111111111111111111111111111111111111")
    to_addr = bytes.fromhex("0000000000000000000000002222222222222222222222222222222222222222")
    raw_amount = 50 * 10**18  # 50 tokens

    mock_logs = [
        {
            "topics": [
                bytes.fromhex("ddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"),
                from_addr,
                to_addr,
            ],
            "data": raw_amount.to_bytes(32, "big"),
            "transactionHash": tx_hash,
            "blockNumber": 12345,
        }
    ]

    mock_w3.eth = MagicMock()
    mock_w3.eth.get_logs = AsyncMock(return_value=mock_logs)

    tracker = WhaleTransferTracker(
        w3=mock_w3,
        token_address="0xC02aaA39b223FE8D0A0e5C4F27ead9083C756Cc2",
        token_price_usd=3000.0,
        usd_threshold=100000.0,
        decimals=18,
    )

    results = await tracker.get_whale_transfers(12000, 13000)
    assert len(results) == 1
    assert results[0]["usd_value"] == 150000.0


def test_smart_money_sentiment() -> None:
    smart_addr = "0xAb5801a7D398351b8bE11C439e05C5B3259aeC9B"
    tracker = SmartMoneyTracker([smart_addr])

    tracker.process_transfer({
        "from": smart_addr,
        "to": "0xNormalUser",
        "usd_value": 10000.0,
        "timestamp": 100,
    })

    state = tracker.get_address_state(smart_addr)
    assert state is not None
    assert state["net_flow_usd"] == -10000.0
    assert state["tx_count"] == 1
