"""Tests for Binance REST backfill: parse saved fixtures, pagination logic, field mapping.

Appendix §3.2:
- aggTrades: paginate by fromId; m=true -> SELL (buyer is maker, taker sold).
- klines -> OHLCV (openTime, o, h, l, c, v, ..., takerBuyBaseVol, takerBuyQuoteVol).
- openInterest -> OpenInterest (snapshot and historical).
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

import pytest

from crypcodile.exchanges.binance.backfill import (
    BinanceBackfill,
    parse_aggtrades_page,
    parse_klines_page,
    parse_open_interest,
    parse_open_interest_hist,
)
from crypcodile.schema.enums import Side
from crypcodile.schema.records import OHLCV, OpenInterest, Trade

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# parse_aggtrades_page
# ---------------------------------------------------------------------------


def test_parse_aggtrades_page_maps_fields() -> None:
    raw: list[dict[str, Any]] = json.loads((FIXTURES / "rest_aggtrades.json").read_text())
    records = parse_aggtrades_page(raw, venue="binance-spot", symbol="BTCUSDT", local_ts=42)
    trades = [r for r in records if isinstance(r, Trade)]

    assert len(trades) == 2

    t0 = trades[0]
    assert t0.exchange == "binance-spot"
    assert t0.symbol == "binance-spot:BTCUSDT"
    assert t0.symbol_raw == "BTCUSDT"
    assert t0.price == 50000.10
    assert t0.amount == 0.5
    # m=true -> buyer is maker -> taker sold -> SELL
    assert t0.side == Side.SELL
    assert t0.exchange_ts == 1700000000100 * 1_000_000  # T ms -> ns
    assert t0.local_ts == 42
    assert t0.id == "1001"

    t1 = trades[1]
    # m=false -> buyer is taker -> BUY
    assert t1.side == Side.BUY
    assert t1.id == "1002"


def test_parse_aggtrades_returns_last_id() -> None:
    """parse_aggtrades_page must expose the last agg trade id for fromId pagination."""
    raw: list[dict[str, Any]] = json.loads((FIXTURES / "rest_aggtrades.json").read_text())
    records = parse_aggtrades_page(raw, venue="binance-spot", symbol="BTCUSDT", local_ts=0)
    trades = [r for r in records if isinstance(r, Trade)]
    # last trade in this page has agg id 1002
    assert int(trades[-1].id) == 1002


# ---------------------------------------------------------------------------
# parse_klines_page
# ---------------------------------------------------------------------------


def test_parse_klines_page_maps_fields() -> None:
    raw: list[list[Any]] = json.loads((FIXTURES / "rest_klines.json").read_text())
    records = parse_klines_page(
        raw, venue="binance-spot", symbol="BTCUSDT", interval="1m", local_ts=99
    )
    bars = [r for r in records if isinstance(r, OHLCV)]

    assert len(bars) == 2

    b0 = bars[0]
    assert b0.exchange == "binance-spot"
    assert b0.symbol == "binance-spot:BTCUSDT"
    assert b0.symbol_raw == "BTCUSDT"
    assert b0.interval == "1m"
    assert b0.open == 50000.0
    assert b0.high == 50100.0
    assert b0.low == 49900.0
    assert b0.close == 50050.0
    assert b0.volume == pytest.approx(10.5)
    # takerBuyBaseVol = buy_volume
    assert b0.buy_volume == pytest.approx(6.3)
    # sell_volume = total - buy
    assert b0.sell_volume == pytest.approx(10.5 - 6.3)
    # num_trades from count field (index 8)
    assert b0.num_trades == 150
    # exchange_ts from openTime (ms -> ns)
    assert b0.exchange_ts == 1700000000000 * 1_000_000
    assert b0.local_ts == 99

    b1 = bars[1]
    assert b1.open == 50050.0
    assert b1.num_trades == 120


# ---------------------------------------------------------------------------
# parse_open_interest (snapshot endpoint)
# ---------------------------------------------------------------------------


def test_parse_open_interest_snapshot() -> None:
    raw: dict[str, Any] = json.loads((FIXTURES / "rest_open_interest.json").read_text())
    oi = parse_open_interest(raw, venue="binance-usdm", local_ts=7)

    assert isinstance(oi, OpenInterest)
    assert oi.exchange == "binance-usdm"
    assert oi.symbol == "binance-usdm:BTCUSDT"
    assert oi.symbol_raw == "BTCUSDT"
    assert oi.open_interest == pytest.approx(12345.678)
    # time field (ms) -> exchange_ts (ns)
    assert oi.exchange_ts == 1700000000000 * 1_000_000
    assert oi.local_ts == 7


# ---------------------------------------------------------------------------
# parse_open_interest_hist (historical endpoint)
# ---------------------------------------------------------------------------


def test_parse_open_interest_hist() -> None:
    raw: list[dict[str, Any]] = json.loads(
        (FIXTURES / "rest_open_interest_hist.json").read_text()
    )
    records = parse_open_interest_hist(raw, venue="binance-usdm", symbol="BTCUSDT", local_ts=5)
    ois = [r for r in records if isinstance(r, OpenInterest)]

    assert len(ois) == 2

    oi0 = ois[0]
    assert oi0.open_interest == pytest.approx(12000.0)
    assert oi0.open_interest_value == pytest.approx(600000000.0)
    assert oi0.exchange_ts == 1700000000000 * 1_000_000
    assert oi0.local_ts == 5

    oi1 = ois[1]
    assert oi1.open_interest == pytest.approx(12500.0)
    assert oi1.open_interest_value == pytest.approx(625000000.0)
    assert oi1.exchange_ts == 1700003600000 * 1_000_000


# ---------------------------------------------------------------------------
# BinanceBackfill — pagination (fromId walk for aggTrades)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backfill_aggtrades_paginates_by_from_id() -> None:
    """BinanceBackfill.backfill_aggtrades iterates pages via fromId until limit < page_size."""
    page1: list[dict[str, Any]] = json.loads(
        (FIXTURES / "rest_aggtrades_page1.json").read_text()
    )
    page2: list[dict[str, Any]] = json.loads(
        (FIXTURES / "rest_aggtrades.json").read_text()
    )

    calls: list[dict[str, Any]] = []

    async def fake_fetch_aggtrades(
        symbol: str,
        from_id: int | None,
        start_time_ms: int | None,
        end_time_ms: int | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        calls.append(
            {
                "from_id": from_id,
                "start_time_ms": start_time_ms,
                "end_time_ms": end_time_ms,
                "limit": limit,
            }
        )
        if from_id is None:
            return page1
        # Second call uses fromId = page1 last id (1000) + 1 = 1001
        if len(calls) == 2:
            return page2
        # Third call onward: no more data -> paginator stops
        return []

    bf = BinanceBackfill(
        fetch_aggtrades=fake_fetch_aggtrades,
        fetch_klines=None,
        fetch_open_interest=None,
        fetch_open_interest_hist=None,
    )

    trades: list[Trade] = []
    async for rec in bf.backfill_aggtrades(
        venue="binance-spot",
        symbol="BTCUSDT",
        start_ns=0,
        end_ns=9_999_999_999_999_999_999,
        page_size=2,
        local_ts=0,
    ):
        if isinstance(rec, Trade):
            trades.append(rec)

    # Two pages, 2 trades each = 4 total
    assert len(trades) == 4
    # Second call used fromId = last id of page1 + 1 = 1001
    assert calls[1]["from_id"] == 1001


@pytest.mark.asyncio
async def test_backfill_aggtrades_stops_on_partial_page() -> None:
    """BinanceBackfill stops when a page has fewer items than page_size (no more data)."""
    single: list[dict[str, Any]] = json.loads(
        (FIXTURES / "rest_aggtrades.json").read_text()
    )  # 2 items

    async def fake_fetch_aggtrades(
        symbol: str,
        from_id: int | None,
        start_time_ms: int | None,
        end_time_ms: int | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        return single  # always 2 items

    bf = BinanceBackfill(
        fetch_aggtrades=fake_fetch_aggtrades,
        fetch_klines=None,
        fetch_open_interest=None,
        fetch_open_interest_hist=None,
    )

    trades: list[Trade] = []
    async for rec in bf.backfill_aggtrades(
        venue="binance-spot",
        symbol="BTCUSDT",
        start_ns=0,
        end_ns=9_999_999_999_999_999_999,
        page_size=5,  # page_size > result size -> stop
        local_ts=0,
    ):
        if isinstance(rec, Trade):
            trades.append(rec)

    # Only one page returned (< page_size items -> done)
    assert len(trades) == 2


@pytest.mark.asyncio
async def test_backfill_klines_yields_ohlcv() -> None:
    """BinanceBackfill.backfill_klines returns OHLCV bars."""
    raw: list[list[Any]] = json.loads((FIXTURES / "rest_klines.json").read_text())

    async def fake_fetch_klines(
        symbol: str,
        interval: str,
        start_time_ms: int | None,
        end_time_ms: int | None,
        limit: int,
    ) -> list[list[Any]]:
        return raw

    bf = BinanceBackfill(
        fetch_aggtrades=None,
        fetch_klines=fake_fetch_klines,
        fetch_open_interest=None,
        fetch_open_interest_hist=None,
    )

    bars: list[OHLCV] = []
    async for rec in bf.backfill_klines(
        venue="binance-spot",
        symbol="BTCUSDT",
        interval="1m",
        start_ns=0,
        end_ns=9_999_999_999_999_999_999,
        local_ts=0,
    ):
        if isinstance(rec, OHLCV):
            bars.append(rec)

    assert len(bars) == 2
    assert bars[0].open == 50000.0


@pytest.mark.asyncio
async def test_backfill_aggtrades_respects_end_ns() -> None:
    """backfill_aggtrades must not yield trades with exchange_ts > end_ns.

    Regression for the fromId pagination end-bound gap: once pagination switches
    to fromId mode, Binance ignores startTime/endTime on the wire. Without a
    client-side guard, trades past end_ns leak into the result.

    Setup: page1 has a=999 (T=1699999999900ms) and a=1000 (T=1700000000000ms);
    page2 has a=1001 (T=1700000000100ms) and a=1002 (T=1700000000200ms).
    end_ns is set to T=1700000000050ms (in ns), which is between page1's last
    trade and page2's first trade.  Only the two page1 trades must be emitted;
    a=1001 and a=1002 must be dropped and pagination must stop.
    """
    page1: list[dict[str, Any]] = json.loads(
        (FIXTURES / "rest_aggtrades_page1.json").read_text()
    )  # a=999 T=1699999999900ms, a=1000 T=1700000000000ms
    page2: list[dict[str, Any]] = json.loads(
        (FIXTURES / "rest_aggtrades.json").read_text()
    )  # a=1001 T=1700000000100ms, a=1002 T=1700000000200ms

    call_count = 0

    async def fake_fetch_aggtrades(
        symbol: str,
        from_id: int | None,
        start_time_ms: int | None,
        end_time_ms: int | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        nonlocal call_count
        call_count += 1
        if from_id is None:
            return page1
        return page2

    bf = BinanceBackfill(
        fetch_aggtrades=fake_fetch_aggtrades,
        fetch_klines=None,
        fetch_open_interest=None,
        fetch_open_interest_hist=None,
    )

    # end_ns sits between page1's last trade (T=1700000000000ms) and page2's
    # first trade (T=1700000000100ms)
    end_ns = 1700000000050 * 1_000_000  # 1700000000050ms -> ns

    trades: list[Trade] = []
    async for rec in bf.backfill_aggtrades(
        venue="binance-spot",
        symbol="BTCUSDT",
        start_ns=0,
        end_ns=end_ns,
        page_size=2,  # page_size == len(page1) -> normally triggers second page fetch
        local_ts=0,
    ):
        if isinstance(rec, Trade):
            trades.append(rec)

    # Only trades from page1 (both within end_ns) must be emitted
    assert len(trades) == 2
    assert trades[0].id == "999"
    assert trades[1].id == "1000"
    # Verify that page2 was fetched but its out-of-range trades were dropped;
    # the paginator may or may not fetch page2 depending on when the stop fires —
    # what matters is no trade past end_ns is yielded.
    for t in trades:
        assert t.exchange_ts <= end_ns, f"Trade {t.id} at {t.exchange_ts} exceeds end_ns {end_ns}"


@pytest.mark.asyncio
async def test_backfill_aggtrades_none_callback_yields_nothing() -> None:
    """BinanceBackfill.backfill_aggtrades with fetch_aggtrades=None yields nothing."""
    bf = BinanceBackfill(
        fetch_aggtrades=None,
        fetch_klines=None,
        fetch_open_interest=None,
        fetch_open_interest_hist=None,
    )
    records: list[Any] = []
    async for r in bf.backfill_aggtrades(
        venue="binance-spot", symbol="BTCUSDT", start_ns=0, end_ns=1_000_000_000
    ):
        records.append(r)
    assert records == []


@pytest.mark.asyncio
async def test_backfill_klines_none_callback_yields_nothing() -> None:
    """BinanceBackfill.backfill_klines with fetch_klines=None yields nothing."""
    bf = BinanceBackfill(
        fetch_aggtrades=None,
        fetch_klines=None,
        fetch_open_interest=None,
        fetch_open_interest_hist=None,
    )
    bars: list[Any] = []
    async for b in bf.backfill_klines(
        venue="binance-spot", symbol="BTCUSDT", interval="1m", start_ns=0, end_ns=1_000_000_000
    ):
        bars.append(b)
    assert bars == []


@pytest.mark.asyncio
async def test_backfill_open_interest_snapshot_none_callback() -> None:
    """BinanceBackfill.backfill_open_interest with fetch=None returns None."""
    bf = BinanceBackfill(
        fetch_aggtrades=None,
        fetch_klines=None,
        fetch_open_interest=None,
        fetch_open_interest_hist=None,
    )
    result = await bf.backfill_open_interest(venue="binance-usdm", symbol="BTCUSDT")
    assert result is None


@pytest.mark.asyncio
async def test_backfill_open_interest_snapshot_with_callback() -> None:
    """BinanceBackfill.backfill_open_interest returns an OpenInterest record."""
    from crypcodile.exchanges.binance.backfill import BinanceBackfill
    from crypcodile.schema.records import OpenInterest

    async def fake_fetch(symbol: str) -> dict[str, Any]:
        return {"symbol": symbol, "openInterest": "999.0", "time": "1700000000000"}

    bf = BinanceBackfill(
        fetch_aggtrades=None,
        fetch_klines=None,
        fetch_open_interest=fake_fetch,
        fetch_open_interest_hist=None,
    )
    oi = await bf.backfill_open_interest(venue="binance-usdm", symbol="BTCUSDT")
    assert isinstance(oi, OpenInterest)
    assert oi.open_interest == 999.0


@pytest.mark.asyncio
async def test_backfill_open_interest_hist_none_callback_yields_nothing() -> None:
    """BinanceBackfill.backfill_open_interest_hist with fetch=None yields nothing."""
    bf = BinanceBackfill(
        fetch_aggtrades=None,
        fetch_klines=None,
        fetch_open_interest=None,
        fetch_open_interest_hist=None,
    )
    records: list[Any] = []
    async for r in bf.backfill_open_interest_hist(
        venue="binance-usdm", symbol="BTCUSDT", period="1h", start_ns=0, end_ns=1_000_000_000
    ):
        records.append(r)
    assert records == []


@pytest.mark.asyncio
async def test_backfill_aggtrades_empty_first_page_stops() -> None:
    """backfill_aggtrades stops immediately when first page is empty."""
    async def fake_fetch(
        symbol: str,
        from_id: int | None,
        start_time_ms: int | None,
        end_time_ms: int | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        return []

    bf = BinanceBackfill(
        fetch_aggtrades=fake_fetch,
        fetch_klines=None,
        fetch_open_interest=None,
        fetch_open_interest_hist=None,
    )
    records: list[Any] = []
    async for r in bf.backfill_aggtrades(
        venue="binance-spot", symbol="BTCUSDT", start_ns=0, end_ns=9_999_999_999_999_999_999
    ):
        records.append(r)
    assert records == []


@pytest.mark.asyncio
async def test_backfill_open_interest_hist_yields_records() -> None:
    """BinanceBackfill.backfill_open_interest_hist yields OpenInterest records."""
    raw: list[dict[str, Any]] = json.loads(
        (FIXTURES / "rest_open_interest_hist.json").read_text()
    )

    async def fake_fetch_oi_hist(
        symbol: str,
        period: str,
        start_time_ms: int | None,
        end_time_ms: int | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        return raw

    bf = BinanceBackfill(
        fetch_aggtrades=None,
        fetch_klines=None,
        fetch_open_interest=None,
        fetch_open_interest_hist=fake_fetch_oi_hist,
    )

    ois: list[OpenInterest] = []
    async for rec in bf.backfill_open_interest_hist(
        venue="binance-usdm",
        symbol="BTCUSDT",
        period="1h",
        start_ns=0,
        end_ns=9_999_999_999_999_999_999,
        local_ts=0,
    ):
        if isinstance(rec, OpenInterest):
            ois.append(rec)

    assert len(ois) == 2
    assert ois[0].open_interest == pytest.approx(12000.0)
