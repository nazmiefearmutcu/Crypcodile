from __future__ import annotations

import pytest
from crypcodile.exchanges.gmx_synthetix.position_tracker import (
    GMXPositionTracker,
    SynthetixPositionTracker,
)


def test_perp_tracker_increase_decrease() -> None:
    tracker = GMXPositionTracker()

    # Initial state should be empty
    assert tracker.get_position("ETH-USD") is None

    # 1. PositionIncrease event
    tracker.process_event({
        "event": "PositionIncrease",
        "symbol": "ETH-USD",
        "size_delta_usd": 1000.0,
        "collateral_delta_usd": 100.0,
        "price": 3000.0,
        "funding_fee_delta": 0.5,
    })

    pos = tracker.get_position("ETH-USD")
    assert pos is not None
    assert pos["size_usd"] == 1000.0
    assert pos["collateral_usd"] == 100.0
    assert pos["margin"] == 100.0
    assert pos["leverage"] == 10.0
    assert pos["entry_price"] == 3000.0
    assert pos["funding_fee"] == 0.5
    assert pos["realized_pnl"] == 0.0
    assert pos["liquidations"] == 0

    # 2. Another PositionIncrease (scaling up)
    tracker.process_event({
        "event": "IncreasePosition",
        "symbol": "ETH-USD",
        "size_delta_usd": 500.0,
        "collateral_delta_usd": 50.0,
        "price": 3300.0,
    })

    pos = tracker.get_position("ETH-USD")
    assert pos["size_usd"] == 1500.0
    assert pos["collateral_usd"] == 150.0
    # Average entry price calculation: (1000 * 3000 + 500 * 3300) / 1500 = (3000000 + 1650000) / 1500 = 3100.0
    assert abs(pos["entry_price"] - 3100.0) < 1e-9
    assert pos["leverage"] == 10.0

    # 3. PositionDecrease event
    tracker.process_event({
        "event": "PositionDecrease",
        "symbol": "ETH-USD",
        "size_delta_usd": 750.0,
        "collateral_delta_usd": 50.0,
        "realized_pnl_delta": 25.0,
        "funding_fee_delta": 1.0,
    })

    pos = tracker.get_position("ETH-USD")
    assert pos["size_usd"] == 750.0
    assert pos["collateral_usd"] == 100.0
    assert pos["realized_pnl"] == 25.0
    assert pos["funding_fee"] == 1.5
    assert pos["leverage"] == 7.5
    # entry price remains the same on decrease
    assert abs(pos["entry_price"] - 3100.0) < 1e-9


def test_perp_tracker_liquidate_close() -> None:
    tracker = SynthetixPositionTracker()

    # Open position
    tracker.process_event({
        "event": "PositionIncrease",
        "symbol": "BTC-USD",
        "size_delta_usd": 5000.0,
        "collateral_delta_usd": 500.0,
        "price": 60000.0,
    })

    # Liquidate position
    tracker.process_event({
        "event": "LiquidatePosition",
        "symbol": "BTC-USD",
        "realized_pnl_delta": -500.0,
    })

    pos = tracker.get_position("BTC-USD")
    assert pos["size_usd"] == 0.0
    assert pos["collateral_usd"] == 0.0
    assert pos["entry_price"] == 0.0
    assert pos["leverage"] == 0.0
    assert pos["liquidations"] == 1
    assert pos["realized_pnl"] == -500.0

    # Open again
    tracker.process_event({
        "event": "PositionIncrease",
        "symbol": "BTC-USD",
        "size_delta_usd": 2000.0,
        "collateral_delta_usd": 200.0,
        "price": 62000.0,
    })

    # Close position
    tracker.process_event({
        "event": "ClosePosition",
        "symbol": "BTC-USD",
        "realized_pnl_delta": 100.0,
    })

    pos = tracker.get_position("BTC-USD")
    assert pos["size_usd"] == 0.0
    assert pos["collateral_usd"] == 0.0
    assert pos["entry_price"] == 0.0
    assert pos["realized_pnl"] == -400.0  # -500 from liquidation + 100 from close
    assert pos["liquidations"] == 1
