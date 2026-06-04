"""Tests for Deribit REST backfill: parse saved fixtures, pagination logic, field mapping."""

import json
import pathlib
from typing import Any

import pytest

from crocodile.exchanges.deribit.backfill import (
    parse_funding_page,
    parse_trades_page,
)
from crocodile.schema.enums import Side
from crocodile.schema.records import Funding, Liquidation, Trade

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# parse_trades_page
# ---------------------------------------------------------------------------


def test_parse_trades_page_maps_fields():
    raw = json.loads((FIXTURES / "rest_trades.json").read_text())
    records = list(parse_trades_page(raw, local_ts=42))
    trades = [r for r in records if isinstance(r, Trade)]
    liqs = [r for r in records if isinstance(r, Liquidation)]

    assert len(trades) == 2
    t0 = trades[0]
    assert t0.exchange == "deribit"
    assert t0.symbol == "deribit:BTC-PERPETUAL"
    assert t0.symbol_raw == "BTC-PERPETUAL"
    assert t0.price == 50000.5
    assert t0.amount == 2.0
    assert t0.side == Side.BUY
    assert t0.exchange_ts == 1700000000000 * 1_000_000  # ms → ns
    assert t0.local_ts == 42
    assert t0.id == "REST-1"

    t1 = trades[1]
    assert t1.side == Side.SELL
    assert t1.liquidation == "T"

    # liquidation record emitted for trade with liquidation flag
    assert len(liqs) == 1
    assert liqs[0].side == Side.SELL
    assert liqs[0].price == 49999.0


def test_parse_trades_page_has_more_flag():
    raw = json.loads((FIXTURES / "rest_trades.json").read_text())
    # has_more is False in this fixture
    assert raw["result"]["has_more"] is False

    raw2 = json.loads((FIXTURES / "rest_trades_page1.json").read_text())
    assert raw2["result"]["has_more"] is True


# ---------------------------------------------------------------------------
# parse_funding_page
# ---------------------------------------------------------------------------


def test_parse_funding_page_maps_interest_8h_as_funding_rate():
    raw = json.loads((FIXTURES / "rest_funding.json").read_text())
    records = list(parse_funding_page(raw, symbol="BTC-PERPETUAL", local_ts=99))

    assert len(records) == 2
    f0 = records[0]
    assert isinstance(f0, Funding)
    assert f0.exchange == "deribit"
    assert f0.symbol == "deribit:BTC-PERPETUAL"
    assert f0.symbol_raw == "BTC-PERPETUAL"
    # canonical: interest_8h -> funding_rate
    assert f0.funding_rate == pytest.approx(0.0001)
    # interest_1h -> predicted_funding_rate
    assert f0.predicted_funding_rate == pytest.approx(0.000012)
    assert f0.exchange_ts == 1700000000000 * 1_000_000  # ms → ns
    assert f0.local_ts == 99

    f1 = records[1]
    assert f1.funding_rate == pytest.approx(0.00009)
    assert f1.predicted_funding_rate == pytest.approx(0.000011)


def test_parse_funding_page_populates_funding_timestamp() -> None:
    """funding_timestamp must equal exchange_ts (settlement time per Deribit docs)."""
    raw = json.loads((FIXTURES / "rest_funding.json").read_text())
    records = list(parse_funding_page(raw, symbol="BTC-PERPETUAL", local_ts=99))

    f0 = records[0]
    # entry['timestamp'] = 1700000000000 ms → funding_timestamp = 1700000000000 * 1_000_000 ns
    assert f0.funding_timestamp == 1700000000000 * 1_000_000
    # must equal exchange_ts (both derived from the same entry['timestamp'])
    assert f0.funding_timestamp == f0.exchange_ts

    f1 = records[1]
    assert f1.funding_timestamp == 1700003600000 * 1_000_000


# ---------------------------------------------------------------------------
# backfill_trades — pagination via end_timestamp walking
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backfill_trades_paginates_until_has_more_false():
    """DeribitBackfill.backfill_trades paginates: calls page fetcher walking end_timestamp
    until has_more is False, yielding records from each page."""
    from crocodile.exchanges.deribit.backfill import DeribitBackfill

    page1_raw = json.loads((FIXTURES / "rest_trades_page1.json").read_text())
    page2_raw = json.loads((FIXTURES / "rest_trades.json").read_text())

    # Inject fake fetch: first call returns page1 (has_more=True), second returns page2
    call_args: list[tuple[str, int, int, int]] = []

    async def fake_fetch_trades(
        instrument: str, end_ts_ms: int, count: int, start_ts_ms: int
    ) -> dict:
        call_args.append((instrument, end_ts_ms, count, start_ts_ms))
        if len(call_args) == 1:
            return page1_raw
        return page2_raw

    bf = DeribitBackfill(fetch_trades=fake_fetch_trades, fetch_funding=None)

    # start=1700000000000ms, end=1700000002000ms (ms ints, but backfill accepts ns)
    start_ns = 1700000000000 * 1_000_000
    end_ns = 1700000002000 * 1_000_000

    records = []
    async for r in bf.backfill_trades("BTC-PERPETUAL", start_ns=start_ns, end_ns=end_ns):
        records.append(r)

    trades = [r for r in records if isinstance(r, Trade)]
    # 1 from page1, 2 from page2 (plus 1 liquidation from page2's second trade)
    assert len(trades) == 3
    assert len(call_args) == 2
    # second call's end_ts should walk back to the earliest timestamp on page1
    # page1 has a single trade at timestamp=1700000001500ms (strictly below end_ms=1700000002000ms)
    # so pagination actually moved backward, proving the walk is working
    assert call_args[1][1] == 1700000001500  # end_ts_ms walked back


@pytest.mark.asyncio
async def test_backfill_funding_yields_funding_records():
    """DeribitBackfill.backfill_funding returns Funding records from REST fixture."""
    from crocodile.exchanges.deribit.backfill import DeribitBackfill

    funding_raw = json.loads((FIXTURES / "rest_funding.json").read_text())

    async def fake_fetch_funding(instrument: str, start_ts_ms: int, end_ts_ms: int) -> dict:
        return funding_raw

    bf = DeribitBackfill(fetch_trades=None, fetch_funding=fake_fetch_funding)

    start_ns = 1700000000000 * 1_000_000
    end_ns = 1700007200000 * 1_000_000

    records = []
    async for r in bf.backfill_funding("BTC-PERPETUAL", start_ns=start_ns, end_ns=end_ns):
        records.append(r)

    assert len(records) == 2
    assert all(isinstance(r, Funding) for r in records)
    assert records[0].funding_rate == pytest.approx(0.0001)


# ---------------------------------------------------------------------------
# backfill_trades — infinite-loop guard (same-millisecond page)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backfill_trades_stops_on_no_progress() -> None:
    """When all trades on a page share the same ms as current_end_ms (e.g. a
    liquidation cascade with 1000+ trades in one millisecond), the loop must
    break rather than re-fetching the same page indefinitely."""
    from crocodile.exchanges.deribit.backfill import DeribitBackfill

    # All trades share timestamp=1700000002000ms == end_ms → no progress possible
    same_ms_page = {
        "result": {
            "trades": [
                {
                    "trade_id": f"LIQ-{i}",
                    "trade_seq": i,
                    "instrument_name": "BTC-PERPETUAL",
                    "price": 48000.0,
                    "amount": 1.0,
                    "direction": "sell",
                    "timestamp": 1700000002000,  # identical to end_ms
                    "index_price": 48000.0,
                    "mark_price": 48000.0,
                    "iv": None,
                }
                for i in range(5)
            ],
            "has_more": True,  # server claims more pages exist
        }
    }

    call_count = 0

    async def fake_fetch_no_progress(
        instrument: str, end_ts_ms: int, count: int, start_ts_ms: int
    ) -> dict[str, Any]:
        nonlocal call_count
        call_count += 1
        if call_count > 3:
            raise RuntimeError("Infinite loop: fetch called too many times")
        return same_ms_page

    bf = DeribitBackfill(fetch_trades=fake_fetch_no_progress, fetch_funding=None)

    start_ns = 1700000000000 * 1_000_000
    end_ns = 1700000002000 * 1_000_000  # end_ms = 1700000002000

    records = []
    async for r in bf.backfill_trades("BTC-PERPETUAL", start_ns=start_ns, end_ns=end_ns):
        records.append(r)

    # Must have fetched exactly once and then stopped (no progress → break)
    assert call_count == 1
    trades = [r for r in records if isinstance(r, Trade)]
    assert len(trades) == 5
