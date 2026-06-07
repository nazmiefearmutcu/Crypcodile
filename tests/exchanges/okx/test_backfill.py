"""Tests for OKX REST backfill parsers (Task 4.5).

All tests use injected fixtures — no live network calls.
"""

from __future__ import annotations

from typing import Any

from crypcodile.exchanges.okx.backfill import (
    OKXBackfill,
    parse_funding_page,
    parse_open_interest_page,
    parse_trades_page,
)
from crypcodile.schema.enums import Side
from crypcodile.schema.records import Funding, OpenInterest, Trade

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


# ---------------------------------------------------------------------------
# Bug fix: backfill_open_interest must filter by start_ns / end_ns
# ---------------------------------------------------------------------------

_OI_THREE_RECORDS: dict[str, Any] = {
    "code": "0",
    "data": [
        # OKX returns descending order: newest first.
        # ts=1700000004000 ms → AFTER end_ns → must be skipped (continue branch)
        {"instId": "BTC-USDT-SWAP", "oi": "300", "oiCcy": "30", "ts": "1700000004000"},
        # ts=1700000002000 ms → inside range [1700000001_000_000_000, 1700000003_000_000_000]
        {"instId": "BTC-USDT-SWAP", "oi": "100", "oiCcy": "10", "ts": "1700000002000"},
        # ts=1700000000000 ms → BEFORE start_ns → triggers break
        {"instId": "BTC-USDT-SWAP", "oi": "200", "oiCcy": "20", "ts": "1700000000000"},
    ],
    "msg": "",
}

_START_NS = 1_700_000_001_000_000_000  # 1700000001 s
_END_NS   = 1_700_000_003_000_000_000  # 1700000003 s


async def test_backfill_open_interest_filters_by_time_range() -> None:
    """backfill_open_interest must skip records outside [start_ns, end_ns]."""
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
            return _OI_THREE_RECORDS
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
        start_ns=_START_NS,
        end_ns=_END_NS,
    ):
        records.append(r)
    # Only the record with ts=1700000002000 ms is inside the window
    assert len(records) == 1
    assert records[0].open_interest == 100.0


# ---------------------------------------------------------------------------
# Bug fix: backfill_funding must break (not just skip) when records < start_ns
# ---------------------------------------------------------------------------

# Two pages: page 1 has one record below start_ns; page 2 would be an HTTP hit
# that must NOT occur once we detect we've passed below start_ns.
_FUNDING_BELOW_START: dict[str, Any] = {
    "code": "0",
    "data": [
        {
            "instId": "BTC-USDT-SWAP",
            "instType": "SWAP",
            "fundingRate": "0.0003",
            "fundingTime": "1699990000000",  # far before _START_NS → must break
        }
    ],
    "msg": "",
}


async def test_backfill_trades_none_callback_yields_nothing() -> None:
    """OKXBackfill.backfill_trades with fetch_trades=None yields nothing."""
    bf = OKXBackfill(fetch_trades=None, fetch_funding=None, fetch_open_interest=None)
    records = []
    async for r in bf.backfill_trades(
        venue="okx", symbol="BTC-USDT", inst_type="SPOT", start_ns=0, end_ns=1_000_000_000
    ):
        records.append(r)
    assert records == []


async def test_backfill_funding_none_callback_yields_nothing() -> None:
    """OKXBackfill.backfill_funding with fetch_funding=None yields nothing."""
    bf = OKXBackfill(fetch_trades=None, fetch_funding=None, fetch_open_interest=None)
    records = []
    async for r in bf.backfill_funding(
        venue="okx", symbol="BTC-USDT-SWAP", inst_type="SWAP", start_ns=0, end_ns=1_000_000_000
    ):
        records.append(r)
    assert records == []


async def test_backfill_open_interest_none_callback_yields_nothing() -> None:
    """OKXBackfill.backfill_open_interest with fetch_open_interest=None yields nothing."""
    bf = OKXBackfill(fetch_trades=None, fetch_funding=None, fetch_open_interest=None)
    records = []
    async for r in bf.backfill_open_interest(
        venue="okx", symbol="BTC-USDT-SWAP", inst_type="SWAP", start_ns=0, end_ns=1_000_000_000
    ):
        records.append(r)
    assert records == []


async def test_backfill_trades_advances_after_cursor() -> None:
    """backfill_trades uses the last tradeId as 'after' cursor on the next page."""
    page1: dict[str, Any] = {
        "code": "0",
        "data": [
            {
                "instId": "BTC-USDT",
                "tradeId": "first-trade",
                "px": "50000.0",
                "sz": "1.0",
                "side": "buy",
                "ts": "1700000000100",
            }
        ],
        "msg": "",
    }
    calls: list[Any] = []

    async def _paged_fetch(
        symbol: str,
        inst_type: str,
        after: str | None,
        before: str | None,
        limit: int,
    ) -> dict[str, Any]:
        calls.append(after)
        if len(calls) == 1:
            return page1
        return {"code": "0", "data": [], "msg": ""}  # stop on second call

    bf = OKXBackfill(fetch_trades=_paged_fetch, fetch_funding=None, fetch_open_interest=None)
    records = []
    async for r in bf.backfill_trades(
        venue="okx",
        symbol="BTC-USDT",
        inst_type="SPOT",
        start_ns=0,
        end_ns=9_999_999_999_999_999_999,
    ):
        records.append(r)

    # First call has no after cursor; second call has the tradeId from page 1
    assert len(calls) == 2
    assert calls[0] is None
    assert calls[1] == "first-trade"
    assert len(records) == 1


async def test_backfill_funding_advances_after_cursor_from_fundingtime() -> None:
    """backfill_funding uses the last fundingTime as 'after' cursor on the next page."""
    page1: dict[str, Any] = {
        "code": "0",
        "data": [
            {
                "instId": "BTC-USDT-SWAP",
                "instType": "SWAP",
                "fundingRate": "0.0001",
                "fundingTime": "1700003600000",
            }
        ],
        "msg": "",
    }
    calls: list[Any] = []

    async def _paged_fetch(
        symbol: str,
        inst_type: str,
        after: str | None,
        before: str | None,
        limit: int,
    ) -> dict[str, Any]:
        calls.append(after)
        if len(calls) == 1:
            return page1
        return {"code": "0", "data": [], "msg": ""}

    bf = OKXBackfill(fetch_trades=None, fetch_funding=_paged_fetch, fetch_open_interest=None)
    records = []
    async for r in bf.backfill_funding(
        venue="okx", symbol="BTC-USDT-SWAP", inst_type="SWAP",
        start_ns=0, end_ns=9_999_999_999_999_999_999
    ):
        records.append(r)

    assert len(calls) == 2
    assert calls[0] is None
    assert calls[1] == "1700003600000"
    assert len(records) == 1


async def test_backfill_open_interest_advances_after_cursor_from_ts() -> None:
    """backfill_open_interest uses the last ts as 'after' cursor on the next page."""
    page1: dict[str, Any] = {
        "code": "0",
        "data": [
            {
                "instId": "BTC-USDT-SWAP",
                "oi": "100",
                "oiCcy": "10",
                "ts": "1700000000000",
            }
        ],
        "msg": "",
    }
    calls: list[Any] = []

    async def _paged_fetch(
        symbol: str,
        inst_type: str,
        period: str,
        after: str | None,
        before: str | None,
        limit: int,
    ) -> dict[str, Any]:
        calls.append(after)
        if len(calls) == 1:
            return page1
        return {"code": "0", "data": [], "msg": ""}

    bf = OKXBackfill(fetch_trades=None, fetch_funding=None, fetch_open_interest=_paged_fetch)
    records = []
    async for r in bf.backfill_open_interest(
        venue="okx", symbol="BTC-USDT-SWAP", inst_type="SWAP",
        start_ns=0, end_ns=9_999_999_999_999_999_999
    ):
        records.append(r)

    assert len(calls) == 2
    assert calls[0] is None
    assert calls[1] == "1700000000000"
    assert len(records) == 1


async def test_backfill_funding_breaks_on_first_record_below_start_ns() -> None:
    """Once a funding record falls below start_ns, pagination must stop immediately
    (no further HTTP pages), matching the behaviour of backfill_trades.

    The fake fetch returns an empty page after 2 calls so the test terminates
    even on the unfixed code path.  We then assert only 1 call occurred
    (i.e. the break fired before the second page request).
    """
    fetch_calls: list[int] = []

    async def _fake_fetch(
        symbol: str,
        inst_type: str,
        after: str | None,
        before: str | None,
        limit: int,
    ) -> dict[str, Any]:
        fetch_calls.append(1)
        if len(fetch_calls) == 1:
            return _FUNDING_BELOW_START
        # Second call: return empty to terminate the unfixed loop
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
        start_ns=_START_NS,
        end_ns=_END_NS,
    ):
        records.append(r)
    # No in-range records expected (the only record is below start_ns)
    assert len(records) == 0
    # Crucially: only ONE fetch call must have occurred — early break, not skip
    assert len(fetch_calls) == 1, (
        f"Expected 1 HTTP call (early break) but got {len(fetch_calls)}"
    )
