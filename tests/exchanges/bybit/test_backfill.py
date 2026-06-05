"""Tests for Bybit REST backfill parsers (Task 4.4).

All tests use injected fixtures — no live network calls.
"""

from __future__ import annotations

from typing import Any

from crocodile.exchanges.bybit.backfill import (
    BybitBackfill,
    parse_funding_page,
    parse_open_interest_page,
    parse_trades_page,
)
from crocodile.schema.enums import Side
from crocodile.schema.records import Funding, OpenInterest, Trade

# ---------------------------------------------------------------------------
# parse_trades_page
# ---------------------------------------------------------------------------

_TRADES_RAW: dict[str, Any] = {
    "result": {
        "list": [
            {
                "execId": "abc-001",
                "symbol": "BTCUSDT",
                "price": "50000.10",
                "size": "0.5",
                "side": "Buy",
                "time": "1700000000100",
                "isBlockTrade": False,
            },
            {
                "execId": "abc-002",
                "symbol": "BTCUSDT",
                "price": "49999.50",
                "size": "1.2",
                "side": "Sell",
                "time": "1700000000200",
                "isBlockTrade": False,
            },
        ]
    }
}


def test_parse_trades_page_basic() -> None:
    records = parse_trades_page(_TRADES_RAW, venue="bybit", symbol="BTCUSDT", local_ts=0)
    assert len(records) == 2
    first = records[0]
    assert isinstance(first, Trade)
    assert first.price == 50000.10
    assert first.amount == 0.5
    assert first.side == Side.BUY
    assert first.exchange_ts == 1700000000100 * 1_000_000  # ms → ns
    assert first.exchange == "bybit"
    assert first.symbol == "bybit:BTCUSDT"

    second = records[1]
    assert second.side == Side.SELL


# ---------------------------------------------------------------------------
# parse_funding_page
# ---------------------------------------------------------------------------

_FUNDING_RAW: dict[str, Any] = {
    "result": {
        "list": [
            {
                "symbol": "BTCUSDT",
                "fundingRate": "0.0001",
                "fundingRateTimestamp": "1700003600000",
            },
            {
                "symbol": "BTCUSDT",
                "fundingRate": "-0.0002",
                "fundingRateTimestamp": "1700000000000",
            },
        ]
    }
}


def test_parse_funding_page() -> None:
    records = parse_funding_page(_FUNDING_RAW, venue="bybit", symbol="BTCUSDT", local_ts=0)
    assert len(records) == 2
    first = records[0]
    assert isinstance(first, Funding)
    assert first.funding_rate == 0.0001
    assert first.funding_timestamp == 1700003600000 * 1_000_000
    assert first.exchange == "bybit"
    assert first.symbol == "bybit:BTCUSDT"
    assert first.interval_hours == 8  # Bybit default 8h cadence


# ---------------------------------------------------------------------------
# parse_open_interest_page
# ---------------------------------------------------------------------------

_OI_RAW: dict[str, Any] = {
    "result": {
        "list": [
            {
                "symbol": "BTCUSDT",
                "openInterest": "12345.6",
                "timestamp": "1700000000000",
            }
        ]
    }
}


def test_parse_open_interest_page() -> None:
    records = parse_open_interest_page(_OI_RAW, venue="bybit", symbol="BTCUSDT", local_ts=0)
    assert len(records) == 1
    oi = records[0]
    assert isinstance(oi, OpenInterest)
    assert oi.open_interest == 12345.6
    assert oi.exchange_ts == 1700000000000 * 1_000_000
    assert oi.exchange == "bybit"


# ---------------------------------------------------------------------------
# BybitBackfill — pagination with injected fetch callbacks
# ---------------------------------------------------------------------------


async def test_backfill_trades_yields_records() -> None:
    async def _fake_fetch(
        symbol: str,
        category: str,
        limit: int,
        cursor: str | None,
    ) -> dict[str, Any]:
        # Return one page then signal no more data (nextPageCursor absent / empty)
        return _TRADES_RAW | {"nextPageCursor": ""}

    bf = BybitBackfill(
        fetch_trades=_fake_fetch,
        fetch_funding=None,
        fetch_open_interest=None,
    )
    records = []
    async for r in bf.backfill_trades(
        venue="bybit",
        symbol="BTCUSDT",
        category="linear",
        start_ns=0,
        end_ns=9_999_999_999_999_999_999,
    ):
        records.append(r)
    assert len(records) == 2
    assert all(isinstance(r, Trade) for r in records)


async def test_backfill_funding_yields_records() -> None:
    async def _fake_fetch(
        symbol: str,
        category: str,
        start_ms: int,
        end_ms: int,
        limit: int,
    ) -> dict[str, Any]:
        return _FUNDING_RAW | {"nextPageCursor": ""}

    bf = BybitBackfill(
        fetch_trades=None,
        fetch_funding=_fake_fetch,
        fetch_open_interest=None,
    )
    records = []
    async for r in bf.backfill_funding(
        venue="bybit",
        symbol="BTCUSDT",
        category="linear",
        start_ns=0,
        end_ns=9_999_999_999_999_999_999,
    ):
        records.append(r)
    assert len(records) == 2
    assert all(isinstance(r, Funding) for r in records)


async def test_backfill_open_interest_yields_records() -> None:
    async def _fake_fetch(
        symbol: str,
        category: str,
        interval_min: int,
        start_ms: int,
        end_ms: int,
        limit: int,
    ) -> dict[str, Any]:
        return _OI_RAW | {"nextPageCursor": ""}

    bf = BybitBackfill(
        fetch_trades=None,
        fetch_funding=None,
        fetch_open_interest=_fake_fetch,
    )
    records = []
    async for r in bf.backfill_open_interest(
        venue="bybit",
        symbol="BTCUSDT",
        category="linear",
        start_ns=0,
        end_ns=9_999_999_999_999_999_999,
    ):
        records.append(r)
    assert len(records) == 1
    assert isinstance(records[0], OpenInterest)
