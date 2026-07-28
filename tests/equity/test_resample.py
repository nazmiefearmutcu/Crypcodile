"""Tests for Stockodile resampling algorithms."""

from __future__ import annotations

import datetime
import tempfile
from pathlib import Path

import polars as pl
import pytest

from crocodile.core.replay.orderbook import BookGap
from crocodile.core.schema.enums import AssetClass, Side
from crocodile.core.schema.provenance import Provenance, provenance_fields
from crocodile.core.schema.records import OHLCV, BookDelta, BookSnapshot, Quote, Trade
from crocodile.core.store.catalog import Catalog
from crocodile.equity.resample import (
    parse_interval,
    resample_bars_df,
    resample_bars_to_bars,
    resample_book_snapshots,
    resample_ohlcv,
    resample_quotes_df,
    resample_quotes_to_bars,
    resample_trades_df,
    resample_trades_to_bars,
)


def test_parse_interval_names_its_components_instead_of_numbering_them() -> None:
    """Migrated: this used to pin a bare 3-tuple, against an equity-only implementation.

    ``core`` had a *different* ``parse_interval`` of the same name and signature returning
    a 2-tuple whose second element was the bare unit word where this one's was the SQL
    literal, so importing the wrong one either raised on the unpack or built SQL out of the
    word ``"minute"``. One function now, and it returns a named structure precisely so that
    positional unpacking — which is what made the arity difference silent — is not how
    callers read it.
    """
    assert parse_interval("5m").ns == 300_000_000_000
    assert parse_interval("5m").sql == "INTERVAL '5 minute'"
    assert parse_interval("5m").polars == "5m"
    assert parse_interval("5m").unit == "minute"

    assert parse_interval("1s").ns == 1_000_000_000
    assert parse_interval("1h").ns == 3_600_000_000_000
    assert parse_interval("1d").ns == 86_400_000_000_000
    assert parse_interval("1w").ns == 604_800_000_000_000

    with pytest.raises(ValueError):
        parse_interval("1x")


def test_resample_trades_to_bars() -> None:
    """Test stream resampling of Trades to Bars."""
    trades = [
        Trade(
            source="alpaca",
            symbol="AAPL",
            symbol_raw="AAPL",
            source_ts=None,
            local_ts=100_000_000,
            id="1",
            price=150.0,
            amount=10.0,
            asset_class=AssetClass.EQUITY,
            side=Side.UNKNOWN,
        ),
        Trade(
            source="alpaca",
            symbol="AAPL",
            symbol_raw="AAPL",
            source_ts=None,
            local_ts=500_000_000,
            id="2",
            price=152.0,
            amount=20.0,
            asset_class=AssetClass.EQUITY,
            side=Side.UNKNOWN,
        ),
        Trade(
            source="alpaca",
            symbol="AAPL",
            symbol_raw="AAPL",
            source_ts=None,
            local_ts=1_200_000_000,
            id="3",
            price=151.0,
            amount=15.0,
            asset_class=AssetClass.EQUITY,
            side=Side.UNKNOWN,
        ),
    ]

    bars = list(resample_trades_to_bars(trades, "1s"))
    assert len(bars) == 2

    # First bar (0s to 1s bucket)
    assert bars[0].local_ts == 0
    assert bars[0].open == 150.0
    assert bars[0].high == 152.0
    assert bars[0].low == 150.0
    assert bars[0].close == 152.0
    assert bars[0].volume == 30.0
    assert bars[0].vwap == pytest.approx((150.0 * 10.0 + 152.0 * 20.0) / 30.0)
    assert bars[0].num_trades == 2

    # Second bar (1s to 2s bucket)
    assert bars[1].local_ts == 1_000_000_000
    assert bars[1].open == 151.0
    assert bars[1].high == 151.0
    assert bars[1].low == 151.0
    assert bars[1].close == 151.0
    assert bars[1].volume == 15.0
    assert bars[1].vwap == 151.0
    assert bars[1].num_trades == 1


def test_resample_quotes_to_bars() -> None:
    """Test stream resampling of Quotes to Bars."""
    quotes = [
        Quote(
            source="alpaca",
            symbol="AAPL",
            symbol_raw="AAPL",
            source_ts=None,
            local_ts=100_000_000,
            bid_px=149.0,
            bid_sz=100.0,
            ask_px=151.0,
            ask_sz=200.0,
            asset_class=AssetClass.EQUITY,
        ),
        Quote(
            source="alpaca",
            symbol="AAPL",
            symbol_raw="AAPL",
            source_ts=None,
            local_ts=500_000_000,
            bid_px=150.0,
            bid_sz=150.0,
            ask_px=152.0,
            ask_sz=250.0,
            asset_class=AssetClass.EQUITY,
        ),
    ]

    # Resample quotes mid-price (mid of Q1: 150.0, mid of Q2: 151.0)
    bars = list(resample_quotes_to_bars(quotes, "1s", price_type="mid"))
    assert len(bars) == 1
    assert bars[0].local_ts == 0
    assert bars[0].open == 150.0
    assert bars[0].close == 151.0
    assert bars[0].high == 151.0
    assert bars[0].low == 150.0
    assert bars[0].volume == 0.0
    assert bars[0].vwap == 150.5
    assert bars[0].num_trades == 2


def test_resample_bars_to_bars() -> None:
    """Test resampling of lower resolution bars to higher resolution bars."""
    bars_1s = [
        OHLCV(
            source="alpaca",
            symbol="AAPL",
            symbol_raw="AAPL",
            source_ts=None,
            local_ts=0,
            interval="1s",
            open=100.0,
            high=105.0,
            low=99.0,
            close=102.0,
            volume=1000.0,
            vwap=102.0,
            num_trades=10,
            asset_class=AssetClass.EQUITY,
        ),
        OHLCV(
            source="alpaca",
            symbol="AAPL",
            symbol_raw="AAPL",
            source_ts=None,
            local_ts=1_000_000_000,
            interval="1s",
            open=102.0,
            high=103.0,
            low=101.0,
            close=102.5,
            volume=2000.0,
            vwap=102.2,
            num_trades=20,
            asset_class=AssetClass.EQUITY,
        ),
        OHLCV(
            source="alpaca",
            symbol="AAPL",
            symbol_raw="AAPL",
            source_ts=None,
            local_ts=60_000_000_000,
            interval="1s",
            open=103.0,
            high=104.0,
            low=102.0,
            close=103.5,
            volume=1500.0,
            vwap=103.2,
            num_trades=15,
            asset_class=AssetClass.EQUITY,
        ),
    ]

    # Resample 1s bars to 1m (60s) bars
    bars_1m = list(resample_bars_to_bars(bars_1s, "1m"))
    assert len(bars_1m) == 2

    # First 1m bar (includes 0s and 1s bars)
    assert bars_1m[0].local_ts == 0
    assert bars_1m[0].open == 100.0
    assert bars_1m[0].high == 105.0
    assert bars_1m[0].low == 99.0
    assert bars_1m[0].close == 102.5
    assert bars_1m[0].volume == 3000.0
    assert bars_1m[0].vwap == pytest.approx((102.0 * 1000.0 + 102.2 * 2000.0) / 3000.0)
    assert bars_1m[0].num_trades == 30

    # Second 1m bar (includes 60s bar)
    assert bars_1m[1].local_ts == 60_000_000_000
    assert bars_1m[1].open == 103.0
    assert bars_1m[1].high == 104.0
    assert bars_1m[1].low == 102.0
    assert bars_1m[1].close == 103.5
    assert bars_1m[1].volume == 1500.0
    assert bars_1m[1].vwap == 103.2
    assert bars_1m[1].num_trades == 15


def test_resample_trades_df() -> None:
    """Test Polars-based trade resampling."""
    df = pl.DataFrame(
        [
            {"local_ts": 100_000_000, "price": 150.0, "amount": 10.0, "symbol": "AAPL"},
            {"local_ts": 500_000_000, "price": 152.0, "amount": 20.0, "symbol": "AAPL"},
            {"local_ts": 1_200_000_000, "price": 151.0, "amount": 15.0, "symbol": "AAPL"},
        ]
    )
    res = resample_trades_df(df, "1s")
    assert len(res) == 2
    assert res.row(0, named=True)["bar"] == 0
    assert res.row(0, named=True)["open"] == 150.0
    assert res.row(0, named=True)["close"] == 152.0
    assert res.row(0, named=True)["volume"] == 30.0
    assert res.row(0, named=True)["vwap"] == pytest.approx((150.0 * 10.0 + 152.0 * 20.0) / 30.0)
    assert res.row(0, named=True)["trade_count"] == 2

    assert res.row(1, named=True)["bar"] == 1_000_000_000
    assert res.row(1, named=True)["close"] == 151.0
    assert res.row(1, named=True)["volume"] == 15.0
    assert res.row(1, named=True)["trade_count"] == 1


def test_resample_quotes_df() -> None:
    """Test Polars-based quote resampling."""
    df = pl.DataFrame(
        [
            {"local_ts": 100_000_000, "bid_px": 149.0, "ask_px": 151.0, "symbol": "AAPL"},
            {"local_ts": 500_000_000, "bid_px": 150.0, "ask_px": 152.0, "symbol": "AAPL"},
        ]
    )
    res = resample_quotes_df(df, "1s", price_type="mid")
    assert len(res) == 1
    assert res.row(0, named=True)["bar"] == 0
    assert res.row(0, named=True)["open"] == 150.0
    assert res.row(0, named=True)["close"] == 151.0
    assert res.row(0, named=True)["volume"] == 0.0
    assert res.row(0, named=True)["vwap"] == 150.5
    assert res.row(0, named=True)["trade_count"] == 2


def test_resample_bars_df() -> None:
    """Test Polars-based bar resampling."""
    df = pl.DataFrame(
        [
            {
                "local_ts": 0,
                "open": 100.0,
                "high": 105.0,
                "low": 99.0,
                "close": 102.0,
                "volume": 1000.0,
                "vwap": 102.0,
                "trade_count": 10,
                "symbol": "AAPL",
            },
            {
                "local_ts": 1_000_000_000,
                "open": 102.0,
                "high": 103.0,
                "low": 101.0,
                "close": 102.5,
                "volume": 2000.0,
                "vwap": 102.2,
                "trade_count": 20,
                "symbol": "AAPL",
            },
        ]
    )
    res = resample_bars_df(df, "1m")
    assert len(res) == 1
    assert res.row(0, named=True)["bar"] == 0
    assert res.row(0, named=True)["open"] == 100.0
    assert res.row(0, named=True)["high"] == 105.0
    assert res.row(0, named=True)["low"] == 99.0
    assert res.row(0, named=True)["close"] == 102.5
    assert res.row(0, named=True)["volume"] == 3000.0
    assert res.row(0, named=True)["vwap"] == pytest.approx(
        (102.0 * 1000.0 + 102.2 * 2000.0) / 3000.0
    )
    assert res.row(0, named=True)["trade_count"] == 30


def _bars_frame(count_column: str | None) -> pl.DataFrame:
    """Two 1-second bars carrying their print count under ``count_column``, or under none."""
    rows: list[dict[str, object]] = [
        {
            "local_ts": 0,
            "open": 100.0,
            "high": 105.0,
            "low": 99.0,
            "close": 102.0,
            "volume": 1000.0,
            "symbol": "AAPL",
        },
        {
            "local_ts": 1_000_000_000,
            "open": 102.0,
            "high": 103.0,
            "low": 101.0,
            "close": 102.5,
            "volume": 2000.0,
            "symbol": "AAPL",
        },
    ]
    if count_column is not None:
        for row, count in zip(rows, (1000, 1500), strict=True):
            row[count_column] = count
    return pl.DataFrame(rows)


def test_resample_bars_df_reads_the_count_a_lake_derived_frame_actually_carries() -> None:
    """The canonical field and the Parquet column are both ``num_trades``.

    This test used to hand-build a frame spelled ``trade_count``, which is the one
    spelling that made the lookup succeed — so the suite could not see that a frame read
    off the lake missed it entirely and fell through to a fabricated 1 per bar. Measured
    then: 2 as the summed count of 2 500 prints.
    """
    res = resample_bars_df(_bars_frame("num_trades"), "1m")

    assert len(res) == 1
    assert res.row(0, named=True)["trade_count"] == 2500


def test_resample_bars_df_reports_no_count_rather_than_one_per_bar() -> None:
    """A frame that never said how many prints made each bar must not be answered with 2."""
    res = resample_bars_df(_bars_frame(None), "1m")

    assert len(res) == 1
    assert res.row(0, named=True)["trade_count"] is None


def test_resample_book_snapshots() -> None:
    """Test generating order book snapshots from BookSnapshot and BookDelta stream.

    This fixture is the one that separated the two forked resamplers, and it survives
    unchanged because its answer is the one that won. Against the crypto rule — apply the
    boundary-crossing record, then emit every boundary at or below it — the 1s boundary
    reported bids ``[(150, 12)]`` and asks ``[(152, 25), (153, 30)]``, having already folded
    in the 1.2s delta. There is one ``resample_book_snapshots`` now, in
    ``crocodile.core.resample.book``, and this import reaches it.
    """
    records: list[BookSnapshot | BookDelta] = [
        BookSnapshot(
            source="iex",
            symbol="AAPL",
            symbol_raw="AAPL",
            source_ts=None,
            local_ts=100_000_000,
            bids=[(150.0, 10.0), (149.0, 20.0)],
            asks=[(151.0, 15.0), (152.0, 25.0)],
            depth=4,
            asset_class=AssetClass.EQUITY,
        ),
        BookDelta(
            source="iex",
            symbol="AAPL",
            symbol_raw="AAPL",
            source_ts=None,
            local_ts=500_000_000,
            bids=[(150.0, 12.0)],  # update bid size
            asks=[(151.0, 0.0)],  # remove ask price 151
            seq_id=1,
            asset_class=AssetClass.EQUITY,
        ),
        BookDelta(
            source="iex",
            symbol="AAPL",
            symbol_raw="AAPL",
            source_ts=None,
            local_ts=1_200_000_000,
            bids=[(149.0, 0.0)],  # remove bid price 149
            asks=[(153.0, 30.0)],  # add ask price 153
            seq_id=2,
            asset_class=AssetClass.EQUITY,
        ),
        BookDelta(
            source="iex",
            symbol="AAPL",
            symbol_raw="AAPL",
            source_ts=None,
            local_ts=2_200_000_000,
            bids=[],
            asks=[],
            seq_id=3,
            asset_class=AssetClass.EQUITY,
        ),
    ]

    snapshots = list(resample_book_snapshots(records, 1_000_000_000))
    assert len(snapshots) == 2

    # First snapshot boundary at 1_000_000_000 ns
    # Captures state before local_ts=1_200_000_000 delta has been applied
    snap1 = snapshots[0]
    assert snap1.local_ts == 1_000_000_000
    assert snap1.bids == [(150.0, 12.0), (149.0, 20.0)]
    assert snap1.asks == [(152.0, 25.0)]

    # Second snapshot boundary at 2_000_000_000 ns
    # Captures state before local_ts=2_200_000_000 delta has been applied
    snap2 = snapshots[1]
    assert snap2.local_ts == 2_000_000_000
    assert snap2.bids == [(150.0, 12.0)]
    assert snap2.asks == [(152.0, 25.0), (153.0, 30.0)]


def test_resample_unsorted_streams() -> None:
    # Test that ValueError is raised for unsorted streams
    trades = [
        Trade(
            source="alpaca",
            symbol="AAPL",
            symbol_raw="AAPL",
            local_ts=500_000_000,
            id="1",
            price=150.0,
            amount=10.0,
            asset_class=AssetClass.EQUITY,
            source_ts=None,
            side=Side.UNKNOWN,
        ),
        Trade(
            source="alpaca",
            symbol="AAPL",
            symbol_raw="AAPL",
            local_ts=100_000_000,
            id="2",
            price=152.0,
            amount=20.0,
            asset_class=AssetClass.EQUITY,
            source_ts=None,
            side=Side.UNKNOWN,
        ),  # unsorted!
    ]
    with pytest.raises(ValueError):
        list(resample_trades_to_bars(trades, "1s"))

    quotes = [
        Quote(
            source="alpaca",
            symbol="AAPL",
            symbol_raw="AAPL",
            local_ts=500_000_000,
            bid_px=150.0,
            bid_sz=10.0,
            ask_px=151.0,
            ask_sz=10.0,
            asset_class=AssetClass.EQUITY,
            source_ts=None,
        ),
        Quote(
            source="alpaca",
            symbol="AAPL",
            symbol_raw="AAPL",
            local_ts=100_000_000,
            bid_px=152.0,
            bid_sz=20.0,
            ask_px=153.0,
            ask_sz=10.0,
            asset_class=AssetClass.EQUITY,
            source_ts=None,
        ),  # unsorted!
    ]
    with pytest.raises(ValueError):
        list(resample_quotes_to_bars(quotes, "1s"))


def test_resample_timestamp_units() -> None:
    # Test that millisecond timestamps are scaled correctly
    # 1782060000000 ms is 2026-06-21.
    base_ms = 1782060000000
    trades = [
        Trade(
            source="alpaca",
            symbol="AAPL",
            symbol_raw="AAPL",
            local_ts=base_ms + 100_000,
            id="1",
            price=150.0,
            amount=10.0,
            asset_class=AssetClass.EQUITY,
            source_ts=None,
            side=Side.UNKNOWN,
        ),  # base + 100s
        Trade(
            source="alpaca",
            symbol="AAPL",
            symbol_raw="AAPL",
            local_ts=base_ms + 250_000,
            id="2",
            price=152.0,
            amount=20.0,
            asset_class=AssetClass.EQUITY,
            source_ts=None,
            side=Side.UNKNOWN,
        ),  # base + 250s
        Trade(
            source="alpaca",
            symbol="AAPL",
            symbol_raw="AAPL",
            local_ts=base_ms + 350_000,
            id="3",
            price=151.0,
            amount=15.0,
            asset_class=AssetClass.EQUITY,
            source_ts=None,
            side=Side.UNKNOWN,
        ),  # base + 350s
    ]
    # interval "5m" = 300s. 300s in ms is 300,000 ms.
    # first bucket is [base_ms, base_ms + 300_000) -> contains base + 100k, base + 250k.
    # second bucket is [base_ms + 300_000, base_ms + 600_000) -> contains base + 350k.
    bars = list(resample_trades_to_bars(trades, "5m"))
    assert len(bars) == 2
    assert bars[0].local_ts == base_ms
    assert bars[1].local_ts == base_ms + 300_000


def test_resample_book_snapshots_gaps() -> None:
    """Test that BookGap is raised when L2 stream seq_id is discontinuous."""
    records: list[BookSnapshot | BookDelta] = [
        BookSnapshot(
            source="iex",
            symbol="AAPL",
            symbol_raw="AAPL",
            source_ts=None,
            local_ts=100_000_000,
            bids=[(150.0, 10.0)],
            asks=[(151.0, 15.0)],
            depth=2,
            sequence_id=0,
            asset_class=AssetClass.EQUITY,
        ),
        BookDelta(
            source="iex",
            symbol="AAPL",
            symbol_raw="AAPL",
            source_ts=None,
            local_ts=500_000_000,
            bids=[(150.0, 12.0)],
            asks=[],
            seq_id=5,
            asset_class=AssetClass.EQUITY,  # gap! expected 1
        ),
    ]

    with pytest.raises(BookGap):
        list(resample_book_snapshots(records, 1_000_000_000))


def test_resample_ohlcv_catalog() -> None:
    """Test resampling from Catalog / DuckDB."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        # Create mock parquet files mimicking the hive partition layout:
        # source=alpaca/channel=trade/date=2026-06-21/bucket=42/part-0.parquet
        data_path = Path(tmp_dir) / "source=alpaca/channel=trade/date=2026-06-21/part-0.parquet"
        data_path.parent.mkdir(parents=True, exist_ok=True)

        # Write trade records to parquet using Polars
        trade_df = pl.DataFrame(
            [
                {
                    "source": "alpaca",
                    "asset_class": "equity",
                    "channel": "trade",
                    "symbol": "AAPL",
                    "symbol_raw": "AAPL",
                    "source_ts": None,
                    "local_ts": 1782060000000000000 + 100_000_000,  # 2026-06-21 + 0.1s
                    "id": "1",
                    "price": 150.0,
                    "amount": 10.0,
                },
                {
                    "source": "alpaca",
                    "asset_class": "equity",
                    "channel": "trade",
                    "symbol": "AAPL",
                    "symbol_raw": "AAPL",
                    "source_ts": None,
                    "local_ts": 1782060000000000000 + 500_000_000,  # 2026-06-21 + 0.5s
                    "id": "2",
                    "price": 152.0,
                    "amount": 20.0,
                },
            ]
        )
        trade_df.write_parquet(data_path)

        # Load catalog pointing to the temporary directory
        catalog = Catalog(tmp_dir)

        # Resample OHLCV over the catalog
        res = resample_ohlcv(
            catalog,
            "AAPL",
            1782060000000000000,
            1782060000000000000 + 1_000_000_000,
            "1s",
        )
        assert len(res) == 1
        assert res.row(0, named=True)["bar"] == 1782060000000000000
        assert res.row(0, named=True)["open"] == 150.0
        assert res.row(0, named=True)["close"] == 152.0
        assert res.row(0, named=True)["volume"] == 30.0
        assert res.row(0, named=True)["vwap"] == pytest.approx((150.0 * 10.0 + 152.0 * 20.0) / 30.0)
        # Migrated from ``trade_count``: the merged catalog resampler emits the
        # canonical spelling, which is what the ``OHLCV`` record and the lake column
        # are called. The Polars frame paths above still say ``trade_count``.
        assert res.row(0, named=True)["num_trades"] == 2


# ---------------------------------------------------------------------------
# What a re-bucketed bar claims about itself
# ---------------------------------------------------------------------------


_MINUTE_NS = 60_000_000_000


def _minute_bar(index: int, **overrides: object) -> OHLCV:
    """One 1-minute bar, so each test below states only the field it is about."""
    kwargs: dict[str, object] = {
        "source": "alpaca",
        "symbol": "AAPL",
        "symbol_raw": "AAPL",
        "local_ts": index * _MINUTE_NS,
        "asset_class": AssetClass.EQUITY,
        "source_ts": None,
        "interval": "1m",
        "open": 100.0,
        "high": 101.0,
        "low": 99.0,
        "close": 100.5,
        "volume": 10.0,
    }
    kwargs.update(overrides)
    return OHLCV(**kwargs)  # type: ignore[arg-type]


def test_a_re_bucketed_bar_measures_its_coverage_instead_of_claiming_a_full_one() -> None:
    """C1: the resampler holds the denominator its confidence was refusing to use.

    Three 1-minute bars do not make a day. ``yahoo_1m_vap`` scores the same three inputs
    0.0077 and this scored 1.0, on the argument that a resampler cannot observe a stream
    it was not given — true of trades, false here, where every input declares its width.

    The denominator is a regular session rather than a calendar day (see I1), and the
    score is extent times adequacy, so three fully-sampled minutes of a 390-minute
    session score ``(3/390)²``.
    """
    bars = list(resample_bars_to_bars([_minute_bar(i) for i in range(3)], "1d"))

    assert len(bars) == 1
    assert bars[0].prov_basis == "ohlcv_from_ohlcv"
    assert bars[0].prov_confidence == pytest.approx((3 / 390) ** 2)


def test_a_complete_session_re_bucketed_to_a_day_no_longer_scores_a_quarter() -> None:
    """I1: 390 fully-sampled minutes are a complete US trading day, and now say so.

    Against a 1440-minute calendar denominator this scored 0.2708 — every complete
    equity daily bar there is, dropped by any consumer thresholding at 0.5.
    """
    bars = list(resample_bars_to_bars([_minute_bar(i) for i in range(390)], "1d"))

    assert len(bars) == 1
    assert bars[0].prov_confidence == 1.0


def test_the_same_bar_arriving_twice_does_not_raise_the_confidence() -> None:
    """C3: coverage is a union of intervals, not a sum of widths.

    A lake spanning the migration holds one day under ``channel=bar/`` and again under
    ``channel=ohlcv/`` after a backfill. Summed widths counted it twice, so a *duplicate*
    made the derived bar look better sampled — the one direction a confidence number
    must never move.
    """
    once = [_minute_bar(i) for i in range(30)]
    twice = [bar for i in range(30) for bar in (_minute_bar(i), _minute_bar(i))]

    single = list(resample_bars_to_bars(once, "1h"))
    doubled = list(resample_bars_to_bars(twice, "1h"))

    assert single[0].prov_confidence == pytest.approx(0.25)
    assert doubled[0].prov_confidence == pytest.approx(single[0].prov_confidence)


def test_a_fully_covered_bucket_still_scores_one() -> None:
    """The formula has to leave the honest case alone."""
    bars = list(resample_bars_to_bars([_minute_bar(i) for i in range(60)], "1h"))

    assert len(bars) == 1
    assert bars[0].prov_confidence == 1.0


def test_a_re_bucketed_bar_cannot_be_better_sampled_than_the_bars_it_came_from() -> None:
    """Summing declared widths alone would report a full day made of half-empty hours."""
    inputs = [_minute_bar(i, prov_confidence=0.5) for i in range(60)]

    bars = list(resample_bars_to_bars(inputs, "1h"))

    assert bars[0].prov_confidence == pytest.approx(0.5)


def test_re_bucketing_synthetic_bars_does_not_launder_them_into_derived() -> None:
    """C2: a caller filtering ``WHERE prov != 'synthetic'`` got quote bars back as derived.

    ``resample_quotes_to_bars`` produces SYNTHETIC bars whose ``volume`` is a structural
    zero — nothing in them was transacted. Re-bucketing them reported DERIVED, over prices
    that were never traded, while every input record carried its own ``prov`` and said so.
    """
    quotes = [
        Quote(
            source="alpaca",
            symbol="AAPL",
            symbol_raw="AAPL",
            local_ts=i * _MINUTE_NS,
            asset_class=AssetClass.EQUITY,
            source_ts=None,
            bid_px=99.0,
            bid_sz=1.0,
            ask_px=101.0,
            ask_sz=1.0,
        )
        for i in range(60)
    ]
    quote_bars = list(resample_quotes_to_bars(quotes, "1m"))
    assert {b.prov for b in quote_bars} == {Provenance.SYNTHETIC}

    wider = list(resample_bars_to_bars(quote_bars, "1h"))

    assert len(wider) == 1
    assert wider[0].prov is Provenance.SYNTHETIC
    assert wider[0].prov_basis == "ohlcv_from_ohlcv"
    assert wider[0].volume == 0.0


def test_re_bucketing_venue_bars_stays_derived_rather_than_inheriting_native() -> None:
    """The propagation is a floor on distrust, not a copy of the input's level."""
    bars = list(resample_bars_to_bars([_minute_bar(i) for i in range(60)], "1h"))

    assert all(b.prov is Provenance.NATIVE for b in [_minute_bar(0)])
    assert bars[0].prov is Provenance.DERIVED


# ---------------------------------------------------------------------------
# The three record paths, and the three frame paths, all state the same claim
# ---------------------------------------------------------------------------


def _scraped_trade(index: int) -> Trade:
    """One ``google_finance`` print: a last price lifted off a rendered page.

    ``prov=SYNTHETIC, prov_basis='scraped_last_price', prov_confidence=0.0`` is exactly
    what that connector emits today, so this is the live input, not a contrived one.
    """
    tail = provenance_fields("scraped_last_price")
    return Trade(
        source="google_finance",
        symbol="AAPL",
        symbol_raw="AAPL",
        local_ts=index * _MINUTE_NS,
        asset_class=AssetClass.EQUITY,
        source_ts=None,
        id="",
        price=190.0 + index,
        amount=0.0,
        side=Side.UNKNOWN,
        prov=tail.prov,
        prov_basis=tail.prov_basis,
        prov_confidence=tail.prov_confidence,
        prov_inputs=tail.prov_inputs,
    )


def test_bars_aggregated_from_scraped_prices_are_not_labelled_derived() -> None:
    """C2: ``worst_provenance`` was applied on one of the three record paths.

    The claim was "at every emitted bar". ``resample_bars_to_bars`` had it and the trade
    and quote paths did not, so a consumer filtering ``WHERE prov != 'synthetic'`` got
    back bars built entirely from prices scraped off a web page.
    """
    bars = list(resample_trades_to_bars([_scraped_trade(i) for i in range(3)], "1h"))

    assert len(bars) == 1
    assert bars[0].prov is Provenance.SYNTHETIC
    assert bars[0].prov_basis == "ohlcv_from_trades"


def test_a_venue_reported_trade_stream_still_aggregates_to_derived() -> None:
    """The propagation is a floor on distrust, not a copy of the input's level."""
    trades = [
        Trade(
            source="alpaca",
            symbol="AAPL",
            symbol_raw="AAPL",
            local_ts=i * _MINUTE_NS,
            asset_class=AssetClass.EQUITY,
            source_ts=None,
            id=str(i),
            price=100.0 + i,
            amount=1.0,
            side=Side.UNKNOWN,
        )
        for i in range(3)
    ]

    bars = list(resample_trades_to_bars(trades, "1h"))

    assert [t.prov for t in trades] == [Provenance.NATIVE] * 3
    assert bars[0].prov is Provenance.DERIVED


def test_a_quote_bar_cannot_be_more_trustworthy_than_the_quotes_under_it() -> None:
    """SYNTHETIC is already the floor for most inputs — but not for all of them."""
    tail = provenance_fields("unavailable")
    quotes = [
        Quote(
            source="x",
            symbol="AAPL",
            symbol_raw="AAPL",
            local_ts=i * _MINUTE_NS,
            asset_class=AssetClass.EQUITY,
            source_ts=None,
            bid_px=99.0,
            bid_sz=1.0,
            ask_px=101.0,
            ask_sz=1.0,
            prov=tail.prov,
            prov_basis=tail.prov_basis,
            prov_confidence=tail.prov_confidence,
            prov_inputs=tail.prov_inputs,
        )
        for i in range(3)
    ]

    bars = list(resample_quotes_to_bars(quotes, "1h"))

    assert bars[0].prov is Provenance.UNAVAILABLE
    assert bars[0].prov_basis == "ohlcv_from_quotes"


def test_the_frame_paths_state_a_tail_instead_of_leaving_the_header_default() -> None:
    """A bar frame with no ``prov`` column has NATIVE at 1.0 applied for it downstream."""
    trades = pl.DataFrame(
        {
            "local_ts": [i * _MINUTE_NS for i in range(3)],
            "price": [100.0, 101.0, 102.0],
            "amount": [1.0, 1.0, 1.0],
            "symbol": ["AAPL"] * 3,
        }
    )

    bars = resample_trades_df(trades, "1h")

    assert bars.row(0, named=True)["prov"] == Provenance.DERIVED.value
    assert bars.row(0, named=True)["prov_basis"] == "ohlcv_from_trades"
    assert bars.row(0, named=True)["prov_confidence"] == 1.0


def test_a_frame_of_scraped_prints_resamples_to_a_synthetic_frame() -> None:
    """C2 on the frame path: same laundering, one type further out."""
    scraped = pl.DataFrame(
        {
            "local_ts": [i * _MINUTE_NS for i in range(3)],
            "price": [190.0, 191.0, 192.0],
            "amount": [0.0, 0.0, 0.0],
            "symbol": ["AAPL"] * 3,
            "prov": [Provenance.SYNTHETIC.value] * 3,
        }
    )

    bars = resample_trades_df(scraped, "1h")

    assert bars.row(0, named=True)["prov"] == Provenance.SYNTHETIC.value


def test_a_quote_frame_resamples_to_a_synthetic_frame() -> None:
    quotes = pl.DataFrame(
        {
            "local_ts": [i * _MINUTE_NS for i in range(3)],
            "bid_px": [99.0, 99.5, 100.0],
            "ask_px": [101.0, 101.5, 102.0],
            "symbol": ["AAPL"] * 3,
        }
    )

    bars = resample_quotes_df(quotes, "1h")

    assert bars.row(0, named=True)["prov"] == Provenance.SYNTHETIC.value
    assert bars.row(0, named=True)["prov_basis"] == "ohlcv_from_quotes"


def test_a_bar_frame_measures_its_coverage_and_a_duplicate_does_not_raise_it() -> None:
    """The frame path does the same union arithmetic the record path does.

    Thirty of a session's 390 minutes is a fraction of a day either way; sending each
    of them twice is the shape a lake holding one date under both channel tags produces,
    and a summed width would have reported the duplicate as better coverage.
    """
    once = pl.DataFrame(
        {
            "local_ts": [i * _MINUTE_NS for i in range(30)],
            "open": [1.0] * 30,
            "high": [2.0] * 30,
            "low": [0.5] * 30,
            "close": [1.5] * 30,
            "volume": [10.0] * 30,
            "interval": ["1m"] * 30,
            "prov": [Provenance.DERIVED.value] * 30,
            "prov_confidence": [1.0] * 30,
        }
    )
    twice = pl.concat([once, once]).sort("local_ts")

    single = resample_bars_df(once, "1h")
    doubled = resample_bars_df(twice, "1h")

    assert single.row(0, named=True)["prov_basis"] == "ohlcv_from_ohlcv"
    assert single.row(0, named=True)["prov_confidence"] == pytest.approx(0.25)
    assert doubled.row(0, named=True)["prov_confidence"] == pytest.approx(0.25)


def test_a_bar_frame_that_declares_no_width_reports_no_confidence() -> None:
    """The numerator is the inputs' declared widths; nothing on the frame supplies one.

    A null says so. 1.0 would claim a full bucket, which is the constant this whole
    round is about.
    """
    frame = pl.DataFrame(
        {
            "local_ts": [i * _MINUTE_NS for i in range(3)],
            "open": [1.0] * 3,
            "high": [2.0] * 3,
            "low": [0.5] * 3,
            "close": [1.5] * 3,
            "volume": [10.0] * 3,
        }
    )

    bars = resample_bars_df(frame, "1h")

    assert bars.row(0, named=True)["prov_confidence"] is None
    assert bars.row(0, named=True)["prov"] == Provenance.DERIVED.value


# ---------------------------------------------------------------------------
# One coverage rule, not two implementations of it
# ---------------------------------------------------------------------------
# The record path and the frame path each computed the ``ohlcv_from_ohlcv``
# numerator, and they drifted on two independent axes before anything compared
# them. The tests below feed identical inputs to both and require the same answer,
# which is the property the collapse buys; the two that follow pin the numbers each
# axis was wrong by, so a re-split cannot pass by agreeing on a new wrong answer.

_DAY_NS = 24 * 60 * _MINUTE_NS
_ALIGNED_DAY = (1_700_000_000_000_000_000 // _DAY_NS) * _DAY_NS


def _iso_monday_ns(ts_ns: int) -> int:
    """Return the UTC midnight of the ISO week containing ``ts_ns``, in nanoseconds.

    Computed from ``datetime`` rather than from the resampler's own arithmetic, so the
    expectation below is the calendar's answer and not a restatement of the code under
    test. ``(ts // week) * week`` — the thing the record paths used to do — is *not* an
    ISO week start: it counts from the Unix epoch, and the epoch fell on a Thursday.
    """
    moment = datetime.datetime.fromtimestamp(ts_ns / 1e9, tz=datetime.UTC)
    monday = (moment - datetime.timedelta(days=moment.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return int(monday.timestamp()) * 1_000_000_000


_ALIGNED_MONDAY = _iso_monday_ns(_ALIGNED_DAY)


def _bar_at(local_ts: int, interval: str, confidence: float) -> OHLCV:
    return OHLCV(
        source="alpaca",
        symbol="AAPL",
        symbol_raw="AAPL",
        local_ts=local_ts,
        asset_class=AssetClass.EQUITY,
        source_ts=None,
        interval=interval,
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.5,
        volume=10.0,
        vwap=100.2,
        num_trades=7,
        prov_confidence=confidence,
    )


def _as_frame(bars: list[OHLCV]) -> pl.DataFrame:
    """The same bars a lake read would hand ``resample_bars_df``."""
    return pl.DataFrame(
        [
            {
                "local_ts": b.local_ts,
                "symbol": b.symbol,
                "interval": b.interval,
                "open": b.open,
                "high": b.high,
                "low": b.low,
                "close": b.close,
                "volume": b.volume,
                "vwap": b.vwap,
                "num_trades": b.num_trades,
                "prov": b.prov.value,
                "prov_confidence": b.prov_confidence,
            }
            for b in bars
        ]
    )


_SAME_INPUTS: dict[str, tuple[list[OHLCV], str]] = {
    "a daily bar landing late in its own bucket": (
        [_bar_at(1_700_000_000_000_000_000, "1d", 0.6)],
        "1d",
    ),
    "a narrow well-sampled bar nested inside a wide poorly-sampled one": (
        [_bar_at(_ALIGNED_DAY, "1h", 0.2), _bar_at(_ALIGNED_DAY + 30 * _MINUTE_NS, "1m", 1.0)],
        "1d",
    ),
    "a complete session of minutes": (
        [_bar_at(_ALIGNED_DAY + i * _MINUTE_NS, "1m", 1.0) for i in range(390)],
        "1d",
    ),
    "half a session of minutes": (
        [_bar_at(_ALIGNED_DAY + i * _MINUTE_NS, "1m", 1.0) for i in range(195)],
        "1d",
    ),
    "the same minute twice": (
        [_bar_at(_ALIGNED_DAY, "1m", 1.0), _bar_at(_ALIGNED_DAY, "1m", 1.0)],
        "1h",
    ),
    "a week of sessions": (
        [_bar_at(_ALIGNED_MONDAY + i * _DAY_NS, "1d", 1.0) for i in range(5)],
        "1w",
    ),
}
"""Inputs both entry points must score the same, one case per axis they drifted on.

The ``1w`` case is the newest and it used to be excluded, because the two paths anchored
weekly buckets differently: the record path floored ``local_ts`` against the epoch, which
fell on a Thursday, while ``group_by_dynamic`` anchors on Monday, so five daily bars
landed in one bucket on one side and two on the other. That was a disagreement about
which rows share a bucket rather than about how a bucket's coverage is scored, and
excluding it kept this table honest about what it was and was not measuring. The record
paths now take their origin from ``Interval.anchor_ns`` and land where both engines land,
so the case belongs here — and this is the entry that would go red if the anchor were
ever reverted to zero.
"""


@pytest.mark.parametrize("case", sorted(_SAME_INPUTS), ids=lambda k: k.replace(" ", "_"))
def test_the_record_path_and_the_frame_path_score_identical_inputs_identically(
    case: str,
) -> None:
    """Two entry points, one coverage rule — asserted, not assumed.

    They were two implementations and they disagreed by 22x on clipping and by 7% on
    how an overlap is charged. Both now compute their bucket through ``_coverage_tail``,
    so this is a regression test on the collapse itself rather than on any one number.
    """
    bars, interval = _SAME_INPUTS[case]

    from_records = list(resample_bars_to_bars(bars, interval))
    from_frame = resample_bars_df(_as_frame(bars), interval)

    assert len(from_records) == len(from_frame)
    assert [b.prov_confidence for b in from_records] == pytest.approx(
        from_frame["prov_confidence"].to_list()
    )


def test_an_input_span_is_clipped_to_the_bucket_it_is_scored_against() -> None:
    """C2: the frame path let a span run past the bucket's end and scored a full one.

    One 1d bar at confidence 0.6 stamped 1_700_000_000_000_000_000 lands 6 400 s before
    the end of its 1d bucket, so 6 400 s of a 23 400 s tradeable window is all it covers.
    The frame path reported 1.0 against the record path's 0.0449 — a fully-sampled
    bucket claimed for a bar that overlaps a fifth of it.
    """
    bars = [_bar_at(1_700_000_000_000_000_000, "1d", 0.6)]

    from_records = list(resample_bars_to_bars(bars, "1d"))
    from_frame = resample_bars_df(_as_frame(bars), "1d")

    assert from_records[0].prov_confidence == pytest.approx(0.0448827, abs=1e-6)
    assert from_frame.row(0, named=True)["prov_confidence"] == pytest.approx(
        from_records[0].prov_confidence
    )


def test_an_overlapped_instant_is_scored_at_the_best_confidence_that_observed_it() -> None:
    """I1: the frame path charged each instant to whoever owned the leftover span.

    A 1h bar at 0.2 with a 1m bar at 1.0 nested half an hour in: the overlapping minute
    really was observed at 1.0 by one input, so it is worth 1.0. Charging it to the 1h
    bar because that bar owned the non-overlapping remainder gave 0.004734 against the
    record path's 0.005049 — two answers to one question, neither arguable alone.
    """
    bars = [_bar_at(_ALIGNED_DAY, "1h", 0.2), _bar_at(_ALIGNED_DAY + 30 * _MINUTE_NS, "1m", 1.0)]

    from_records = list(resample_bars_to_bars(bars, "1d"))
    from_frame = resample_bars_df(_as_frame(bars), "1d")

    assert from_records[0].prov_confidence == pytest.approx(0.00504931, abs=1e-8)
    assert from_frame.row(0, named=True)["prov_confidence"] == pytest.approx(
        from_records[0].prov_confidence
    )


# ---------------------------------------------------------------------------
# The numerator is measured in the denominator's units
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("confidence", [0.1, 0.3, 0.5, 1.0])
def test_a_daily_bar_re_read_at_its_own_interval_keeps_its_confidence(
    confidence: float,
) -> None:
    """A confidence must never move up, and re-reading a bar at its own width moved it.

    ``_tradeable_ns`` measures the emitted 1d bucket as one 390-minute session, while
    ``_parse_interval`` gave the input bar a declared width of 1440 minutes — a 3.69x
    inflation of the numerator alone. 0.3, 0.5 and 1.0 all came back 1.0, and 0.1 came
    back 0.3692: a half-sampled daily bar re-read as a daily bar claimed a fully sampled
    bucket. Passing the input width through the same map puts both in session units.
    """
    bars = [_bar_at(_ALIGNED_DAY, "1d", confidence)]

    from_records = list(resample_bars_to_bars(bars, "1d"))
    from_frame = resample_bars_df(_as_frame(bars), "1d")

    assert from_records[0].prov_confidence == pytest.approx(confidence)
    assert from_frame.row(0, named=True)["prov_confidence"] == pytest.approx(confidence)


def test_five_daily_bars_fill_a_week_and_two_do_not() -> None:
    """The units reconcile in the other direction too: a week is five sessions.

    Against wall-clock widths two daily bars covered 172 800 s of a 117 000 s tradeable
    week and saturated at 1.0 — two of five sessions reported as a full week.

    The five days are counted from the ISO Monday, not from ``(ts // week) * week``.
    Those were the same number of nanoseconds apart and a different set of buckets: the
    epoch-floored start is a Thursday, so the fifth bar landed in the following week and
    a full week scored 0.64. The denominator this test is about is unchanged either way;
    what changed is that the numerator is now five sessions of one week rather than four
    of one and one of the next.
    """
    start = _ALIGNED_MONDAY
    full = [_bar_at(start + i * _DAY_NS, "1d", 1.0) for i in range(5)]
    partial = [_bar_at(start + i * _DAY_NS, "1d", 1.0) for i in range(2)]

    scored_full = list(resample_bars_to_bars(full, "1w"))
    scored_partial = list(resample_bars_to_bars(partial, "1w"))

    assert scored_full[0].prov_confidence == pytest.approx(1.0)
    assert scored_partial[0].prov_confidence == pytest.approx(0.16)


def test_a_bar_frame_that_declares_a_width_for_only_some_of_its_rows_raises() -> None:
    """M2: a null ``interval`` became a zero-width input and quietly lowered the score.

    Three 1-minute bars beside one null-interval bar scored 5.9e-05 through the frame
    path while the record path raised on the same input — a silent answer where the
    other entry point refuses to guess.
    """
    bars = [_bar_at(_ALIGNED_DAY + i * _MINUTE_NS, "1m", 1.0) for i in range(3)]
    frame = _as_frame(bars).with_columns(
        pl.when(pl.col("local_ts") == _ALIGNED_DAY)
        .then(None)
        .otherwise(pl.col("interval"))
        .alias("interval")
    )

    with pytest.raises(ValueError, match="declared width"):
        resample_bars_df(frame, "1d")


# ---------------------------------------------------------------------------
# The fourth frame builder — the one the client actually exposes
# ---------------------------------------------------------------------------


def _scraped_trade_lake(root: Path) -> None:
    """A lake of ``google_finance`` prints: a last price off a rendered page, no size."""
    tail = provenance_fields("scraped_last_price")
    path = root / "source=google_finance/channel=trade/date=2026-06-21/part-0.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        [
            {
                "source": "google_finance",
                "asset_class": "equity",
                "channel": "trade",
                "symbol": "AAPL",
                "symbol_raw": "AAPL",
                "source_ts": None,
                "local_ts": 1782060000000000000 + i * 100_000_000,
                "id": str(i),
                "price": 150.0 + i,
                "amount": 0.0,
                "prov": tail.prov.value,
                "prov_basis": tail.prov_basis,
                "prov_confidence": tail.prov_confidence,
            }
            for i in range(3)
        ]
    ).write_parquet(path)


def test_the_catalog_resampler_states_a_tail_rather_than_taking_the_header_default() -> None:
    """The highest-traffic path stated no provenance at all while the other three were fixed.

    ``StockodileClient.resample()`` exposes this one and only this one, and its frame came
    back with no ``prov``, ``prov_basis`` or ``prov_confidence`` column. Written back to the
    lake or compared against records, those bars took the header default — NATIVE at 1.0 —
    over prices scraped off a rendered page at ``prov_confidence=0.0``. The emitted level is
    floored by the worst print in the bucket, so this bucket is SYNTHETIC.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        _scraped_trade_lake(Path(tmp_dir))
        with Catalog(tmp_dir) as catalog:
            res = resample_ohlcv(
                catalog,
                "AAPL",
                1782060000000000000,
                1782060000000000000 + 1_000_000_000,
                "1s",
            )

    row = res.row(0, named=True)
    assert {"prov", "prov_basis", "prov_confidence"} <= set(res.columns)
    assert row["prov"] == Provenance.SYNTHETIC.value
    assert row["prov_basis"] == "ohlcv_from_trades"
    assert row["prov_confidence"] == 1.0


def test_the_catalog_resampler_stays_derived_over_venue_prints() -> None:
    """The floor is on distrust, not a copy: NATIVE inputs still make a DERIVED bar.

    A bar is not something a venue published, whatever the prints under it were.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = Path(tmp_dir) / "source=alpaca/channel=trade/date=2026-06-21/part-0.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        pl.DataFrame(
            [
                {
                    "source": "alpaca",
                    "asset_class": "equity",
                    "channel": "trade",
                    "symbol": "AAPL",
                    "symbol_raw": "AAPL",
                    "source_ts": None,
                    "local_ts": 1782060000000000000 + i * 100_000_000,
                    "id": str(i),
                    "price": 150.0 + i,
                    "amount": 10.0,
                    "prov": Provenance.NATIVE.value,
                }
                for i in range(2)
            ]
        ).write_parquet(path)
        with Catalog(tmp_dir) as catalog:
            res = resample_ohlcv(
                catalog,
                "AAPL",
                1782060000000000000,
                1782060000000000000 + 1_000_000_000,
                "1s",
            )

    assert res.row(0, named=True)["prov"] == Provenance.DERIVED.value


def test_a_filled_empty_bucket_carries_the_tail_too() -> None:
    """An empty grid bar joins no prints, so it makes no claim of its own and takes the basis.

    It must still carry the three columns: a frame where only some rows state a tail is a
    frame where the rest silently take the header default.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        _scraped_trade_lake(Path(tmp_dir))
        with Catalog(tmp_dir) as catalog:
            res = resample_ohlcv(
                catalog,
                "AAPL",
                1782060000000000000 - 3_000_000_000,
                1782060000000000000 + 1_000_000_000,
                "1s",
                fill_empty=True,
            )

    assert len(res) > 1
    assert res["prov_basis"].to_list() == ["ohlcv_from_trades"] * len(res)
    assert res["prov_confidence"].null_count() == 0
    # Migrated from ``trade_count``: see test_resample_ohlcv_catalog.
    by_count = dict(zip(res["num_trades"].to_list(), res["prov"].to_list(), strict=True))
    assert by_count[0] == Provenance.DERIVED.value, "an empty bucket rests on the basis"
    assert by_count[3] == Provenance.SYNTHETIC.value, "a scraped bucket is floored by its prints"


# ---------------------------------------------------------------------------
# Where a weekly bucket begins
# ---------------------------------------------------------------------------
# The three record paths above floored ``local_ts`` against the raw Unix epoch, and the
# epoch fell on a *Thursday*. The two frame paths hand the same question to Polars and
# the DuckDB path hands it to ``time_bucket``, and both of those anchor a week on its
# Monday — measured, not assumed: ``time_bucket(INTERVAL '1 week', make_timestamp(0))``
# and ``from_epoch(0).dt.truncate('1w')`` each answer 1969-12-29, which is the Monday
# whose week contains the epoch.
#
# So a weekly bar's boundary depended on which entry point produced it, and nothing said
# so on the record: a bar stamped Thursday 00:00 UTC is a perfectly plausible weekly bar
# to anyone who does not already know the convention. It is invisible to every daily
# test because a day divides the epoch grid exactly and every other supported width does
# too, which is why this survived a merge that compared the paths on ``1d``.


_WEEK_PROBE_NS = 1_700_000_000_000_000_000
"""2023-11-14T22:13:20Z, a Tuesday — the base timestamp the rest of this file uses."""


def _week_trade(ts: int) -> Trade:
    return Trade(
        source="alpaca",
        symbol="AAPL",
        symbol_raw="AAPL",
        source_ts=None,
        local_ts=ts,
        asset_class=AssetClass.EQUITY,
        id=str(ts),
        price=100.0,
        amount=1.0,
        side=Side.BUY,
    )


def _week_quote(ts: int) -> Quote:
    return Quote(
        source="alpaca",
        symbol="AAPL",
        symbol_raw="AAPL",
        source_ts=None,
        local_ts=ts,
        asset_class=AssetClass.EQUITY,
        bid_px=99.0,
        bid_sz=1.0,
        ask_px=101.0,
        ask_sz=1.0,
    )


def test_a_weekly_bar_from_trades_begins_on_the_iso_monday() -> None:
    """The epoch's Thursday is not a week boundary anyone reports against."""
    bars = list(resample_trades_to_bars([_week_trade(_WEEK_PROBE_NS)], "1w"))

    assert len(bars) == 1
    assert bars[0].local_ts == _iso_monday_ns(_WEEK_PROBE_NS)


def test_a_weekly_bar_from_quotes_begins_on_the_iso_monday() -> None:
    bars = list(resample_quotes_to_bars([_week_quote(_WEEK_PROBE_NS)], "1w"))

    assert len(bars) == 1
    assert bars[0].local_ts == _iso_monday_ns(_WEEK_PROBE_NS)


def test_a_weekly_bar_from_bars_begins_on_the_iso_monday() -> None:
    bars = list(resample_bars_to_bars([_bar_at(_WEEK_PROBE_NS, "1d", 1.0)], "1w"))

    assert len(bars) == 1
    assert bars[0].local_ts == _iso_monday_ns(_WEEK_PROBE_NS)


def test_a_millisecond_stream_anchors_its_week_where_a_nanosecond_one_does() -> None:
    """The anchor is a duration and has to be converted into the stream's unit too.

    ``_detect_scale_and_adjust_interval`` converts the *width*; a grid is a width and an
    origin, and converting only the width would leave a millisecond stream flooring
    against nanosecond-scaled Monday — an offset of three million days.
    """
    ms = _WEEK_PROBE_NS // 1_000_000
    bars = list(resample_trades_to_bars([_week_trade(ms)], "1w"))

    assert len(bars) == 1
    assert bars[0].local_ts == _iso_monday_ns(_WEEK_PROBE_NS) // 1_000_000


def test_the_record_and_frame_paths_put_a_trade_in_the_same_week() -> None:
    """The disagreement was about *which rows share a bucket*, not about a label.

    Two prints four days apart straddle a Monday. Anchored on Thursday they are one
    weekly bar; anchored on Monday they are two. A consumer reading the lake could get
    either answer depending on which entry point wrote the bars.
    """
    early = _iso_monday_ns(_WEEK_PROBE_NS) - 2 * _DAY_NS  # the previous Saturday
    late = early + 4 * _DAY_NS  # the following Wednesday

    from_records = list(resample_trades_to_bars([_week_trade(early), _week_trade(late)], "1w"))
    frame = pl.DataFrame(
        {
            "local_ts": [early, late],
            "price": [100.0, 100.0],
            "amount": [1.0, 1.0],
            "symbol": ["AAPL", "AAPL"],
        }
    )
    from_frame = resample_trades_df(frame, "1w")

    assert [b.local_ts for b in from_records] == from_frame["bar"].to_list()
    assert len(from_records) == 2, "a Saturday and the next Wednesday are not one week"


def test_the_record_path_and_duckdb_put_a_trade_in_the_same_week() -> None:
    """``time_bucket`` is the third opinion, and it was one of the two that agreed."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = Path(tmp_dir) / "source=alpaca/channel=trade/date=2023-11-14/part-0.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        pl.DataFrame(
            [
                {
                    "source": "alpaca",
                    "asset_class": "equity",
                    "channel": "trade",
                    "symbol": "AAPL",
                    "symbol_raw": "AAPL",
                    "source_ts": None,
                    "local_ts": _WEEK_PROBE_NS,
                    "id": "1",
                    "price": 100.0,
                    "amount": 1.0,
                }
            ]
        ).write_parquet(path)

        with Catalog(tmp_dir) as catalog:
            res = resample_ohlcv(
                catalog, "AAPL", _WEEK_PROBE_NS - _DAY_NS, _WEEK_PROBE_NS + _DAY_NS, "1w"
            )

    from_records = list(resample_trades_to_bars([_week_trade(_WEEK_PROBE_NS)], "1w"))

    assert res["bar"].to_list() == [b.local_ts for b in from_records]
    assert res.row(0, named=True)["bar"] == _iso_monday_ns(_WEEK_PROBE_NS)
