from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from crocodile.core.schema.enums import Side
from crocodile.core.schema.records import Funding, Liquidation, Trade
from crocodile.core.sink.memory import MemorySink
from crocodile.crypto.exchanges.gmx_synthetix.connector import GMXSynthetixConnector
from crocodile.crypto.instruments.registry import InstrumentRegistry, Kind


def test_list_instruments() -> None:
    registry = InstrumentRegistry()
    sink = MemorySink()
    connector = GMXSynthetixConnector(
        symbols=["GMX:BTC-USD", "SYNTHETIX:ETH-USD"],
        channels=["trade"],
        out=sink,
        registry=registry,
    )
    
    async def run_test():
        insts = await connector.list_instruments()
        assert len(insts) == 2
        assert insts[0].canonical == "GMX:BTC-USD"
        assert insts[0].exchange == "gmx_synthetix"
        assert insts[0].symbol_raw == "GMX:BTC-USD"
        assert insts[0].kind == Kind.PERPETUAL
        assert insts[0].base == "BTC"
        assert insts[0].quote == "USD"
        
        assert insts[1].canonical == "SYNTHETIX:ETH-USD"
        assert insts[1].base == "ETH"
        assert insts[1].quote == "USD"
        
    asyncio.run(run_test())

def test_gmx_increase_position() -> None:
    registry = InstrumentRegistry()
    sink = MemorySink()
    connector = GMXSynthetixConnector(
        symbols=["GMX:BTC-USD"],
        channels=["trade"],
        out=sink,
        registry=registry,
    )
    
    mock_process = MagicMock(return_value=MagicMock(args={
        "key": b"key1",
        "account": "0xAccount",
        "collateralToken": "0xCollateral",
        "indexToken": "0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f",
        "sizeDelta": int(1000 * 1e30),
        "collateralDelta": int(100 * 1e30),
        "isLong": True,
        "price": int(50000 * 1e30),
        "fee": int(1 * 1e30),
    }))
    
    connector.gmx_events["IncreasePosition"].process_log = mock_process
    
    msg = {"protocol": "gmx", "topics": ["0xIncreasePositionTopic"], "data": "0x"}
    records = list(connector.normalize(msg, 123456789))
    
    assert len(records) == 1
    rec = records[0]
    assert isinstance(rec, Trade)
    assert rec.source == "gmx"
    assert rec.price == 50000.0
    assert abs(rec.amount - 0.02) < 1e-9
    assert rec.side == Side.BUY

def test_gmx_decrease_position() -> None:
    registry = InstrumentRegistry()
    sink = MemorySink()
    connector = GMXSynthetixConnector(
        symbols=["GMX:BTC-USD"],
        channels=["trade"],
        out=sink,
        registry=registry,
    )
    
    mock_process = MagicMock(return_value=MagicMock(args={
        "key": b"key1",
        "account": "0xAccount",
        "collateralToken": "0xCollateral",
        "indexToken": "0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f",
        "sizeDelta": int(500 * 1e30),
        "collateralDelta": int(50 * 1e30),
        "isLong": True,
        "price": int(50000 * 1e30),
        "fee": int(1 * 1e30),
    }))
    connector.gmx_events["DecreasePosition"].process_log = mock_process
    
    msg = {"protocol": "gmx", "topics": ["0xDecreasePositionTopic"], "data": "0x"}
    records = list(connector.normalize(msg, 123456789))
    
    assert len(records) == 1
    rec = records[0]
    assert isinstance(rec, Trade)
    assert rec.price == 50000.0
    assert abs(rec.amount - 0.01) < 1e-9
    assert rec.side == Side.SELL

def test_gmx_liquidate_position() -> None:
    registry = InstrumentRegistry()
    sink = MemorySink()
    connector = GMXSynthetixConnector(
        symbols=["GMX:BTC-USD"],
        channels=["trade"],
        out=sink,
        registry=registry,
    )
    
    mock_process = MagicMock(return_value=MagicMock(args={
        "key": b"key1",
        "account": "0xAccount",
        "collateralToken": "0xCollateral",
        "indexToken": "0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f",
        "isLong": True,
        "size": int(2000 * 1e30),
        "collateral": int(200 * 1e30),
        "reserveAmount": int(1 * 1e18),
        "realisedPnl": int(-200 * 1e30),
        "markPrice": int(50000 * 1e30),
    }))
    connector.gmx_events["LiquidatePosition"].process_log = mock_process
    
    msg = {"protocol": "gmx", "topics": ["0xLiquidatePositionTopic"], "data": "0x"}
    records = list(connector.normalize(msg, 123456789))
    
    assert len(records) == 1
    rec = records[0]
    assert isinstance(rec, Liquidation)
    assert rec.price == 50000.0
    assert abs(rec.amount - 0.04) < 1e-9
    assert rec.side == Side.SELL

def test_gmx_update_position_publishes_no_funding_rate() -> None:
    """Migrated: this used to assert ``rec.funding_rate == 123456 / 1e9``.

    It pinned the number the connector happened to compute, which is the one thing a test
    over a fabricated scale cannot be allowed to do — it makes the invention load-bearing.
    The divisor was annotated ``# illustrative scale`` and 1e9 is not a GMX constant:
    GMX v1's Vault declares ``FUNDING_RATE_PRECISION = 1000000``, so it was out by a
    thousand.

    The scale is not the reason nothing is emitted now. ``entryFundingRate`` is a snapshot
    of a cumulative index taken when a position opened, and ``getFundingFee`` computes
    funding as the *difference* between two such snapshots over
    ``FUNDING_RATE_PRECISION``. One log carries one endpoint, and the other endpoint is
    keyed by collateral token — which this event does not carry. Neither does it carry
    ``indexToken``, so the record it used to emit was attributed to ``gmx:unknown``: a
    since-listing accrual, at a divisor off by 1000, against no instrument, in a field
    ``apr_from_rate`` multiplies by 1095.
    """
    registry = InstrumentRegistry()
    sink = MemorySink()
    connector = GMXSynthetixConnector(
        symbols=["GMX:BTC-USD"],
        channels=["trade"],
        out=sink,
        registry=registry,
    )
    
    mock_process = MagicMock(return_value=MagicMock(args={
        "key": b"key1",
        "size": int(1000 * 1e30),
        "collateral": int(100 * 1e30),
        "averagePrice": int(50000 * 1e30),
        "entryFundingRate": int(123456),
        "reserveAmount": int(1 * 1e18),
        "realisedPnl": 0,
        "markPrice": int(50000 * 1e30),
    }))
    connector.gmx_events["UpdatePosition"].process_log = mock_process
    
    msg = {"protocol": "gmx", "topics": ["0xUpdatePositionTopic"], "data": "0x"}
    records = list(connector.normalize(msg, 123456789))

    assert records == [], (
        "an UpdatePosition log states a position's size, collateral and marks, and this "
        "connector has no canonical record for a position; a Funding it cannot compute is "
        "worse than none, because a zero-argument number annualizes just as smoothly as a "
        "real one"
    )

def test_synthetix_position_modified() -> None:
    registry = InstrumentRegistry()
    sink = MemorySink()
    connector = GMXSynthetixConnector(
        symbols=["SYNTHETIX:ETH-USD"],
        channels=["trade"],
        out=sink,
        registry=registry,
    )
    
    mock_process = MagicMock(return_value=MagicMock(args={
        "id": 1,
        "account": "0xAccount",
        "margin": int(100 * 1e18),
        "size": int(2 * 1e18),
        "tradeSize": int(-1.5 * 1e18),
        "lastPrice": int(3000 * 1e18),
        "fundingIndex": 5,
        "fee": int(0.1 * 1e18),
    }))
    connector.syn_events["PositionModified"].process_log = mock_process
    
    msg = {"protocol": "synthetix", "topics": ["0xPositionModifiedTopic"], "data": "0x"}
    records = list(connector.normalize(msg, 123456789))
    
    assert len(records) == 1
    rec = records[0]
    assert isinstance(rec, Trade)
    assert rec.price == 3000.0
    assert rec.amount == 1.5
    assert rec.side == Side.SELL

def test_synthetix_position_liquidated() -> None:
    registry = InstrumentRegistry()
    sink = MemorySink()
    connector = GMXSynthetixConnector(
        symbols=["SYNTHETIX:ETH-USD"],
        channels=["trade"],
        out=sink,
        registry=registry,
    )
    
    mock_process = MagicMock(return_value=MagicMock(args={
        "id": 1,
        "account": "0xAccount",
        "liquidator": "0xLiquidator",
        "size": int(2 * 1e18),
        "price": int(3000 * 1e18),
        "fee": int(10 * 1e18),
    }))
    connector.syn_events["PositionLiquidated"].process_log = mock_process
    
    msg = {"protocol": "synthetix", "topics": ["0xPositionLiquidatedTopic"], "data": "0x"}
    records = list(connector.normalize(msg, 123456789))
    
    assert len(records) == 1
    rec = records[0]
    assert isinstance(rec, Liquidation)
    assert rec.price == 3000.0
    assert rec.amount == 2.0
    assert rec.side == Side.SELL

def test_synthetix_funding_recomputed() -> None:
    registry = InstrumentRegistry()
    sink = MemorySink()
    connector = GMXSynthetixConnector(
        symbols=["SYNTHETIX:ETH-USD"],
        channels=["trade"],
        out=sink,
        registry=registry,
    )
    
    mock_process = MagicMock(return_value=MagicMock(args={
        "funding": int(0.01 * 1e18),
        "fundingRate": int(0.0005 * 1e18),
        "index": 12,
        "timestamp": 123456789,
    }))
    connector.syn_events["FundingRecomputed"].process_log = mock_process
    
    msg = {"protocol": "synthetix", "topics": ["0xFundingRecomputedTopic"], "data": "0x"}
    records = list(connector.normalize(msg, 123456789))
    
    assert len(records) == 1
    rec = records[0]
    assert isinstance(rec, Funding)
    assert rec.funding_rate == 0.0005
