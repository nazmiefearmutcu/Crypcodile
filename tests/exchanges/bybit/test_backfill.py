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
        cursor: str | None = None,
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
        cursor: str | None = None,
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


# ---------------------------------------------------------------------------
# Pagination — funding multi-page (cursor loop)
# ---------------------------------------------------------------------------

# Two funding pages: page 1 has one record + cursor "pg2", page 2 has one record + empty cursor.
_FUNDING_PAGE1: dict[str, Any] = {
    "result": {
        "nextPageCursor": "pg2",
        "list": [
            {
                "symbol": "BTCUSDT",
                "fundingRate": "0.0001",
                "fundingRateTimestamp": "1700003600000",
            },
        ],
    }
}
_FUNDING_PAGE2: dict[str, Any] = {
    "result": {
        "nextPageCursor": "",
        "list": [
            {
                "symbol": "BTCUSDT",
                "fundingRate": "0.0002",
                "fundingRateTimestamp": "1700000000000",
            },
        ],
    }
}


async def test_backfill_funding_follows_cursor_pagination() -> None:
    """backfill_funding must follow nextPageCursor across pages (not stop after one fetch)."""
    pages = [_FUNDING_PAGE1, _FUNDING_PAGE2]
    calls: list[str | None] = []

    async def _paged_fetch(
        symbol: str,
        category: str,
        start_ms: int,
        end_ms: int,
        limit: int,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        calls.append(cursor)
        return pages[len(calls) - 1]

    bf = BybitBackfill(
        fetch_trades=None,
        fetch_funding=_paged_fetch,
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

    # Must have fetched 2 pages
    assert len(calls) == 2, f"Expected 2 fetch calls, got {len(calls)}"
    # First call has no cursor; second carries the cursor from page 1
    assert calls[0] is None
    assert calls[1] == "pg2"
    # All records from both pages are yielded
    assert len(records) == 2
    assert all(isinstance(r, Funding) for r in records)


# ---------------------------------------------------------------------------
# Pagination — open interest multi-page (cursor loop)
# ---------------------------------------------------------------------------

_OI_PAGE1: dict[str, Any] = {
    "result": {
        "nextPageCursor": "oi_pg2",
        "list": [
            {"symbol": "BTCUSDT", "openInterest": "10000.0", "timestamp": "1700000000000"},
        ],
    }
}
_OI_PAGE2: dict[str, Any] = {
    "result": {
        "nextPageCursor": "",
        "list": [
            {"symbol": "BTCUSDT", "openInterest": "11000.0", "timestamp": "1700003600000"},
        ],
    }
}


async def test_backfill_open_interest_follows_cursor_pagination() -> None:
    """backfill_open_interest must follow nextPageCursor across pages."""
    oi_pages = [_OI_PAGE1, _OI_PAGE2]
    oi_calls: list[str | None] = []

    async def _paged_oi_fetch(
        symbol: str,
        category: str,
        interval_min: int,
        start_ms: int,
        end_ms: int,
        limit: int,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        oi_calls.append(cursor)
        return oi_pages[len(oi_calls) - 1]

    bf = BybitBackfill(
        fetch_trades=None,
        fetch_funding=None,
        fetch_open_interest=_paged_oi_fetch,
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

    assert len(oi_calls) == 2, f"Expected 2 fetch calls, got {len(oi_calls)}"
    assert oi_calls[0] is None
    assert oi_calls[1] == "oi_pg2"
    assert len(records) == 2
    assert all(isinstance(r, OpenInterest) for r in records)


# ---------------------------------------------------------------------------
# backfill_trades — mixed-timestamp page (ascending-order assumption)
# ---------------------------------------------------------------------------
# Bybit /recent-trade returns pages in ASCENDING timestamp order (oldest first).
# When a page contains a mix of in-range and above-end_ns records, the stop
# signal (exchange_ts > end_ns) fires only after yielding in-range records.
# This test validates that assumption: in-range records before the boundary
# record MUST be yielded even though the same page has an above-bound record.

_TRADES_MIXED_PAGE: dict[str, Any] = {
    "result": {
        "nextPageCursor": "",  # last page
        "list": [
            {
                "execId": "in-range-1",
                "symbol": "BTCUSDT",
                "price": "50000.0",
                "size": "1.0",
                "side": "Buy",
                # 1700000000100 ms → inside range (end_ns = 1700000000200_000_000)
                "time": "1700000000100",
            },
            {
                "execId": "above-bound",
                "symbol": "BTCUSDT",
                "price": "50001.0",
                "size": "0.5",
                "side": "Sell",
                # 1700000000300 ms → above end_ns
                "time": "1700000000300",
            },
        ],
    }
}

_END_NS_MIXED = 1700000000200 * 1_000_000  # 1700000000200 ms in ns


async def test_backfill_trades_mixed_page_yields_in_range_records() -> None:
    """Ascending-order assumption: in-range records before stop boundary must be yielded.

    The stop logic (exchange_ts > end_ns → break) is safe ONLY because Bybit
    returns trades in ascending timestamp order.  A mixed page (in-range record
    followed by an above-bound record) must yield the in-range record and then
    stop — it must NOT discard it.
    """

    async def _fake_fetch(
        symbol: str,
        category: str,
        limit: int,
        cursor: str | None,
    ) -> dict[str, Any]:
        return _TRADES_MIXED_PAGE

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
        end_ns=_END_NS_MIXED,
    ):
        records.append(r)

    # Must yield the in-range record and stop before the above-bound one
    assert len(records) == 1, f"Expected 1 record, got {len(records)}: {records}"
    trade = records[0]
    assert isinstance(trade, Trade)
    assert trade.id == "in-range-1"
