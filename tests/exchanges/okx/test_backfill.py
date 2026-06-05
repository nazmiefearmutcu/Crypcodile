"""Tests for OKX REST backfill parsers (Task 4.5).

All tests use injected fixtures — no live network calls.
"""

from __future__ import annotations

from typing import Any

from crocodile.exchanges.okx.backfill import (
    OKXBackfill,
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
    "code": "0",
    "data": [
        {
            "instId": "BTC-USDT",
            "tradeId": "abc-001",
            "px": "50000.10",
            "sz": "0.5",
            "side": "buy",
            "ts": "1700000000100",
        },
        {
            "instId": "BTC-USDT",
            "tradeId": "abc-002",
            "px": "49999.50",
            "sz": "1.2",
            "side": "sell",
            "ts": "1700000000200",
        },
    ],
    "msg": "",
}


def test_parse_trades_page_basic() -> None:
    records = parse_trades_page(_TRADES_RAW, venue="okx", symbol="BTC-USDT", local_ts=0)
    assert len(records) == 2
    first = records[0]
    assert isinstance(first, Trade)
    assert first.price == 50000.10
    assert first.amount == 0.5
    assert first.side == Side.BUY
    assert first.exchange_ts == 1700000000100 * 1_000_000  # ms → ns
    assert first.exchange == "okx"
    assert first.symbol == "okx:BTC-USDT"
    assert first.id == "abc-001"

    second = records[1]
    assert second.side == Side.SELL


# ---------------------------------------------------------------------------
# parse_funding_page
# ---------------------------------------------------------------------------

_FUNDING_RAW: dict[str, Any] = {
    "code": "0",
    "data": [
        {
            "instId": "BTC-USDT-SWAP",
            "instType": "SWAP",
            "fundingRate": "0.0001",
            "realizedRate": "0.0001",
            "fundingTime": "1700003600000",
        },
        {
            "instId": "BTC-USDT-SWAP",
            "instType": "SWAP",
            "fundingRate": "-0.0002",
            "realizedRate": "-0.0002",
            "fundingTime": "1700000000000",
        },
    ],
    "msg": "",
}


def test_parse_funding_page() -> None:
    records = parse_funding_page(_FUNDING_RAW, venue="okx", symbol="BTC-USDT-SWAP", local_ts=0)
    assert len(records) == 2
    first = records[0]
    assert isinstance(first, Funding)
    assert first.funding_rate == 0.0001
    assert first.funding_timestamp == 1700003600000 * 1_000_000
    assert first.exchange == "okx"
    assert first.symbol == "okx:BTC-USDT-SWAP"
    assert first.interval_hours == 8  # OKX default 8h cadence


# ---------------------------------------------------------------------------
# parse_open_interest_page
# ---------------------------------------------------------------------------

_OI_RAW: dict[str, Any] = {
    "code": "0",
    "data": [
        {
            "instId": "BTC-USDT-SWAP",
            "instType": "SWAP",
            "oi": "12345",
            "oiCcy": "1234.5",
            "ts": "1700000000000",
        }
    ],
    "msg": "",
}


def test_parse_open_interest_page() -> None:
    records = parse_open_interest_page(_OI_RAW, venue="okx", symbol="BTC-USDT-SWAP", local_ts=0)
    assert len(records) == 1
    oi = records[0]
    assert isinstance(oi, OpenInterest)
    assert oi.open_interest == 12345.0
    assert oi.open_interest_value == 1234.5
    assert oi.exchange_ts == 1700000000000 * 1_000_000
    assert oi.exchange == "okx"


# ---------------------------------------------------------------------------
# OKXBackfill — pagination with injected fetch callbacks
# ---------------------------------------------------------------------------


async def test_backfill_trades_yields_records() -> None:
    call_count = 0

    async def _fake_fetch(
        symbol: str,
        inst_type: str,
        after: str | None,
        before: str | None,
        limit: int,
    ) -> dict[str, Any]:
        nonlocal call_count
        call_count += 1
        # First call: return data; second call: return empty to stop pagination
        if call_count == 1:
            return _TRADES_RAW
        return {"code": "0", "data": [], "msg": ""}

    bf = OKXBackfill(
        fetch_trades=_fake_fetch,
        fetch_funding=None,
        fetch_open_interest=None,
    )
    records = []
    async for r in bf.backfill_trades(
        venue="okx",
        symbol="BTC-USDT",
        inst_type="SPOT",
        start_ns=0,
        end_ns=9_999_999_999_999_999_999,
    ):
        records.append(r)
    assert len(records) == 2
    assert all(isinstance(r, Trade) for r in records)


async def test_backfill_funding_yields_records() -> None:
    call_count = 0

    async def _fake_fetch(
        symbol: str,
        inst_type: str,
        after: str | None,
        before: str | None,
        limit: int,
    ) -> dict[str, Any]:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _FUNDING_RAW
        return {"code": "0", "data": [], "msg": ""}

    bf = OKXBackfill(
        fetch_trades=None,
        fetch_funding=_fake_fetch,
        fetch_open_interest=None,
    )
    records = []
    async for r in bf.backfill_funding(
        venue="okx",
        symbol="BTC-USDT-SWAP",
        inst_type="SWAP",
        start_ns=0,
        end_ns=9_999_999_999_999_999_999,
    ):
        records.append(r)
    assert len(records) == 2
    assert all(isinstance(r, Funding) for r in records)


async def test_backfill_open_interest_yields_records() -> None:
    call_count = 0

    async def _fake_fetch(
        symbol: str,
        inst_type: str,
        period: str,
        after: str | None,
        before: str | None,
        limit: int,
    ) -> dict[str, Any]:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _OI_RAW
        return {"code": "0", "data": [], "msg": ""}

    bf = OKXBackfill(
        fetch_trades=None,
        fetch_funding=None,
        fetch_open_interest=_fake_fetch,
    )
    records = []
    async for r in bf.backfill_open_interest(
        venue="okx",
        symbol="BTC-USDT-SWAP",
        inst_type="SWAP",
        start_ns=0,
        end_ns=9_999_999_999_999_999_999,
    ):
        records.append(r)
    assert len(records) == 1
    assert isinstance(records[0], OpenInterest)


# ---------------------------------------------------------------------------
# Pagination — trades multi-page (after/before cursor loop)
# ---------------------------------------------------------------------------

_TRADES_PAGE1: dict[str, Any] = {
    "code": "0",
    "data": [
        {
            "instId": "BTC-USDT",
            "tradeId": "page1-trade",
            "px": "50000.0",
            "sz": "1.0",
            "side": "buy",
            "ts": "1700000000100",  # within range
        },
    ],
    "msg": "",
}
_TRADES_PAGE2: dict[str, Any] = {
    "code": "0",
    "data": [],  # empty → stop pagination
    "msg": "",
}


async def test_backfill_trades_stops_on_empty_page() -> None:
    """Pagination must stop when the data array is empty (OKX pagination pattern)."""
    pages = [_TRADES_PAGE1, _TRADES_PAGE2]
    calls: list[int] = []

    async def _paged_fetch(
        symbol: str,
        inst_type: str,
        after: str | None,
        before: str | None,
        limit: int,
    ) -> dict[str, Any]:
        calls.append(len(calls))
        return pages[min(len(calls) - 1, len(pages) - 1)]

    bf = OKXBackfill(
        fetch_trades=_paged_fetch,
        fetch_funding=None,
        fetch_open_interest=None,
    )
    records = []
    async for r in bf.backfill_trades(
        venue="okx",
        symbol="BTC-USDT",
        inst_type="SPOT",
        start_ns=0,
        end_ns=9_999_999_999_999_999_999,
    ):
        records.append(r)
    # Should yield the 1 record from page 1 then stop on empty page 2
    assert len(records) == 1
    assert records[0].id == "page1-trade"
