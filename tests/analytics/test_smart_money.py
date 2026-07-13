from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from crypcodile.analytics.smart_money import (
    SmartMoneyTracker,
    load_watchlist,
    normalize_watchlist,
    summarize_smart_money,
    transfers_from_rows,
)
from crypcodile.cli import app

_RUNNER = CliRunner()


def test_smart_money_tracker() -> None:
    smart_addr1 = "0xAb5801a7D398351b8bE11C439e05C5B3259aeC9B"  # Vitalik's address
    smart_addr2 = "0xMEV1111111111111111111111111111111111111"
    random_addr = "0xNormalUser123456789012345678901234567"

    tracker = SmartMoneyTracker([smart_addr1, smart_addr2])

    # Initial states should be None
    assert tracker.get_address_state(smart_addr1) is None

    # 1. Outgoing transfer from smart_addr1 to random_addr
    tracker.process_transfer({
        "from": smart_addr1,
        "to": random_addr,
        "usd_value": 50000.0,
        "timestamp": 1000,
    })

    state1 = tracker.get_address_state(smart_addr1)
    assert state1 is not None
    assert state1["net_flow_usd"] == -50000.0
    assert state1["total_volume_usd"] == 50000.0
    assert state1["tx_count"] == 1
    assert state1["last_active_ts"] == 1000

    # Random address should not be tracked
    assert tracker.get_address_state(random_addr) is None

    # 2. Incoming transfer to smart_addr1 from smart_addr2 (internal transfer)
    tracker.process_transfer({
        "from": smart_addr2,
        "to": smart_addr1,
        "usd_value": 20000.0,
        "timestamp": 2000,
    })

    # smart_addr1 updates: net_flow gets +20000 -> -30000, total volume gets +20000 -> 70000, tx count -> 2
    state1 = tracker.get_address_state(smart_addr1)
    assert state1["net_flow_usd"] == -30000.0
    assert state1["total_volume_usd"] == 70000.0
    assert state1["tx_count"] == 2
    assert state1["last_active_ts"] == 2000

    # smart_addr2 updates: net_flow gets -20000, volume 20000, tx count 1
    state2 = tracker.get_address_state(smart_addr2)
    assert state2 is not None
    assert state2["net_flow_usd"] == -20000.0
    assert state2["total_volume_usd"] == 20000.0
    assert state2["tx_count"] == 1


def test_normalize_watchlist_shapes() -> None:
    flat = normalize_watchlist({"0xAa": "vitalik", "0xBb": "mev"})
    assert flat["0xaa"] == "vitalik"
    assert flat["0xbb"] == "mev"

    listed = normalize_watchlist(["0xCc", "0xDd"])
    assert listed["0xcc"] == "0xCc"
    assert listed["0xdd"] == "0xDd"

    nested = normalize_watchlist({"addresses": ["0xEe"]})
    assert nested["0xee"] == "0xEe"

    labeled = normalize_watchlist({"watchlist": {"0xFf": "bot"}})
    assert labeled["0xff"] == "bot"


def test_load_watchlist_file(tmp_path: Path) -> None:
    path = tmp_path / "wl.json"
    path.write_text(json.dumps({"0xABC": "smart"}), encoding="utf-8")
    assert load_watchlist(path) == {"0xabc": "smart"}


def test_transfers_from_rows_aliases() -> None:
    rows = transfers_from_rows(
        [
            {
                "from_address": "0x1",
                "to_address": "0x2",
                "amount": "10",
                "local_ts": "5",
            },
            {"sender": None, "recipient": None, "value": 1},
        ]
    )
    assert len(rows) == 1
    assert rows[0]["from"] == "0x1"
    assert rows[0]["to"] == "0x2"
    assert rows[0]["usd_value"] == 10.0
    assert rows[0]["timestamp"] == 5


def test_summarize_smart_money_with_labels() -> None:
    smart = "0xAb5801a7D398351b8bE11C439e05C5B3259aeC9B"
    other = "0x1111111111111111111111111111111111111111"
    rows = summarize_smart_money(
        [
            {"from": smart, "to": other, "usd_value": 100.0, "timestamp": 1},
            {"from": other, "to": smart, "usd_value": 40.0, "timestamp": 2},
        ],
        {smart: "vitalik"},
    )
    assert len(rows) == 1
    assert rows[0]["net_flow_usd"] == -60.0
    assert rows[0]["total_volume_usd"] == 140.0
    assert rows[0]["tx_count"] == 2
    assert rows[0]["label"] == "vitalik"


def test_cli_smart_money_exits_0(tmp_path: Path) -> None:
    smart = "0xAb5801a7D398351b8bE11C439e05C5B3259aeC9B"
    other = "0x1111111111111111111111111111111111111111"
    xfer = tmp_path / "transfers.csv"
    xfer.write_text(
        "from,to,usd_value,timestamp\n"
        f"{smart},{other},50000,1000\n"
        f"{other},{smart},20000,2000\n",
        encoding="utf-8",
    )
    wl = tmp_path / "watchlist.json"
    wl.write_text(json.dumps({smart: "vitalik"}), encoding="utf-8")

    result = _RUNNER.invoke(
        app,
        ["smart-money", "--transfers", str(xfer), "--watchlist", str(wl)],
    )
    assert result.exit_code == 0, result.output
    assert "vitalik" in result.output or "net_flow_usd" in result.output
    assert "30000" in result.output or "-30000" in result.output


def test_cli_smart_money_missing_args() -> None:
    result = _RUNNER.invoke(app, ["smart-money"])
    assert result.exit_code == 1
    assert "required" in result.output.lower()
