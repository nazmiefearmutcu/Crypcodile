"""Tests for Stockodile resampling algorithms."""

from __future__ import annotations

import tempfile
from pathlib import Path

import polars as pl
import pytest

from crocodile.core.replay.orderbook import BookGap
from crocodile.core.schema.enums import AssetClass, Side
from crocodile.core.schema.provenance import Provenance
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


def test_parse_interval() -> None:
    """Test parse_interval translates shorthand correctly."""
    assert parse_interval("1s") == (1_000_000_000, "INTERVAL '1 second'", "1s")
    assert parse_interval("5m") == (300_000_000_000, "INTERVAL '5 minute'", "5m")
    assert parse_interval("1h") == (3_600_000_000_000, "INTERVAL '1 hour'", "1h")
    assert parse_interval("1d") == (86_400_000_000_000, "INTERVAL '1 day'", "1d")

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
    """Test generating order book snapshots from BookSnapshot and BookDelta stream."""
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
        assert res.row(0, named=True)["trade_count"] == 2


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
    """
    bars = list(resample_bars_to_bars([_minute_bar(i) for i in range(3)], "1d"))

    assert len(bars) == 1
    assert bars[0].prov_basis == "ohlcv_from_ohlcv"
    assert bars[0].prov_confidence == pytest.approx(3 / 1440)


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
