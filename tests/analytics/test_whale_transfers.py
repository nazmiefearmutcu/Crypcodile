from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from typer.testing import CliRunner
from web3 import AsyncWeb3

from crypcodile.analytics.whale_transfers import (
    WhaleTransferTracker,
    filter_transfers_by_usd,
    label_known_addresses,
    label_transfer_addresses,
)
from crypcodile.cli import app

_RUNNER = CliRunner()


def test_filter_transfers_by_usd() -> None:
    rows = [
        {"from": "0x1", "to": "0x2", "usd_value": 150_000},
        {"from": "0x3", "to": "0x4", "usd_value": 50_000},
        {"from": "0x5", "to": "0x6", "amount": 200_000},
        {"from": "0x7", "to": "0x8", "usd_value": "not-a-number"},
    ]
    out = filter_transfers_by_usd(rows, 100_000)
    assert len(out) == 2
    assert out[0]["usd_value"] == 150_000
    assert out[1]["usd_value"] == 200_000


def test_filter_transfers_by_usd_rejects_negative() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        filter_transfers_by_usd([], -1)


def test_label_transfer_addresses() -> None:
    smart = "0xAb5801a7D398351b8bE11C439e05C5B3259aeC9B"
    other = "0x1111111111111111111111111111111111111111"
    labeled = label_transfer_addresses(
        [
            {"from": smart, "to": other, "usd_value": 1},
            {"from": other, "to": other, "usd_value": 2},
        ],
        {smart.lower(): "vitalik"},
    )
    assert labeled[0]["from_label"] == "vitalik"
    assert labeled[0]["to_label"] == ""
    assert labeled[0]["is_known"] is True
    assert labeled[1]["is_known"] is False


def test_label_transfer_addresses_ignores_blank_addr_and_keys() -> None:
    """Missing/blank sides and blank watchlist keys never yield is_known."""
    other = "0x1111111111111111111111111111111111111111"
    labeled = label_transfer_addresses(
        [
            {"from": "", "to": other, "usd_value": 1},
            {"from": None, "to": "  ", "usd_value": 2},
            {"from": f"  {other}  ", "to": other, "usd_value": 3},
        ],
        {"": "ghost", "  ": "space", other.lower(): "known"},
    )
    assert labeled[0]["from_label"] == ""
    assert labeled[0]["to_label"] == "known"
    assert labeled[0]["is_known"] is True
    assert labeled[1]["from_label"] == ""
    assert labeled[1]["to_label"] == ""
    assert labeled[1]["is_known"] is False
    # Surrounding whitespace on transfer addresses is stripped for lookup.
    assert labeled[2]["from_label"] == "known"
    assert labeled[2]["is_known"] is True


def test_label_known_addresses() -> None:
    rows = label_known_addresses(
        ["0xAa", "0xBb"],
        {"0xaa": "alpha"},
    )
    assert rows[0] == {"address": "0xAa", "label": "alpha", "is_known": "true"}
    assert rows[1]["is_known"] == "false"
    assert rows[1]["label"] == ""


def test_cli_label_transfers_exits_0(tmp_path: Path) -> None:
    smart = "0xAb5801a7D398351b8bE11C439e05C5B3259aeC9B"
    other = "0x1111111111111111111111111111111111111111"
    xfer = tmp_path / "transfers.csv"
    xfer.write_text(
        "from,to,usd_value\n"
        f"{smart},{other},150000\n"
        f"{other},{other},50000\n",
        encoding="utf-8",
    )
    wl = tmp_path / "watchlist.json"
    wl.write_text(json.dumps({smart: "vitalik"}), encoding="utf-8")

    result = _RUNNER.invoke(
        app,
        [
            "label-transfers",
            "--transfers",
            str(xfer),
            "--watchlist",
            str(wl),
            "--min-usd",
            "100000",
            "--known-only",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "vitalik" in result.output
    assert "150000" in result.output or "150000.0" in result.output


def test_cli_label_transfers_missing_args() -> None:
    result = _RUNNER.invoke(app, ["label-transfers"])
    assert result.exit_code == 1
    assert "required" in result.output.lower()


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
