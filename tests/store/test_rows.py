import pathlib

import msgspec
import polars as pl

from crocodile.core.schema.enums import AssetClass, OptType, Side
from crocodile.core.schema.provenance import Provenance
from crocodile.core.schema.records import (
    OHLCV,
    BookDelta,
    BookSnapshot,
    BookTicker,
    DerivativeTicker,
    Funding,
    Liquidation,
    OpenInterest,
    OptionsChain,
    Trade,
)
from crocodile.core.store.parquet_sink import ParquetSink
from crocodile.core.store.rows import from_row, to_row

_BASE_TS = 1_700_000_000_000_000_000  # 2023-11-14


def test_to_row_adds_partition_cols():
    t = Trade(
        source="deribit",
        symbol="deribit:BTC-PERPETUAL",
        symbol_raw="BTC-PERPETUAL",
        source_ts=1700000000000000000,
        local_ts=1700000000000000000,
        asset_class=AssetClass.CRYPTO,
        id="1",
        price=1.0,
        amount=2.0,
        side=Side.BUY,
    )
    row = to_row(t)
    assert row["channel"] == "trade"
    assert row["date"] == "2023-11-14"
    assert 0 <= row["bucket"] < 128
    assert row["side"] == "buy"


def test_to_row_source_ts_none():
    t = Trade(
        source="binance-spot",
        symbol="binance-spot:BTC-USDT",
        symbol_raw="BTCUSDT",
        source_ts=None,
        local_ts=1700000000000000000,
        asset_class=AssetClass.CRYPTO,
        id="2",
        price=50000.0,
        amount=0.5,
        side=Side.SELL,
    )
    row = to_row(t)
    assert row["source_ts"] is None
    assert row["channel"] == "trade"
    assert row["side"] == "sell"


def test_to_row_book_snapshot_levels():
    snap = BookSnapshot(
        source="deribit",
        symbol="deribit:BTC-PERPETUAL",
        symbol_raw="BTC-PERPETUAL",
        source_ts=1700000000000000000,
        local_ts=1700000000000000000,
        asset_class=AssetClass.CRYPTO,
        bids=[(100.0, 5.0), (99.0, 2.0)],
        asks=[(101.0, 4.0)],
        depth=3,
        sequence_id=100,
        is_snapshot=True,
    )
    row = to_row(snap)
    assert row["channel"] == "book_snapshot"
    assert row["date"] == "2023-11-14"
    assert 0 <= row["bucket"] < 128
    assert row["bids"] == [(100.0, 5.0), (99.0, 2.0)]
    assert row["asks"] == [(101.0, 4.0)]


def test_to_row_book_delta_zero_amount_round_trips():
    delta = BookDelta(
        source="deribit",
        symbol="deribit:BTC-PERPETUAL",
        symbol_raw="BTC-PERPETUAL",
        source_ts=None,
        local_ts=1700000000100000000,
        asset_class=AssetClass.CRYPTO,
        bids=[(99.0, 0.0), (100.0, 7.0)],
        asks=[(102.0, 1.0)],
        seq_id=101,
        prev_seq_id=100,
        is_snapshot=False,
    )
    row = to_row(delta)
    assert row["channel"] == "book_delta"
    # amount=0.0 (canonical removal) must survive round-trip
    assert (99.0, 0.0) in row["bids"]
    assert (100.0, 7.0) in row["bids"]


def test_bucket_is_deterministic():
    t = Trade(
        source="deribit",
        symbol="deribit:BTC-PERPETUAL",
        symbol_raw="BTC-PERPETUAL",
        source_ts=None,
        local_ts=1700000000000000000,
        asset_class=AssetClass.CRYPTO,
        id="3",
        price=1.0,
        amount=1.0,
        side=Side.BUY,
    )
    row1 = to_row(t)
    row2 = to_row(t)
    assert row1["bucket"] == row2["bucket"]


# ---------------------------------------------------------------------------
# from_row round-trip tests — one per channel type
# ---------------------------------------------------------------------------


def _base(channel: str) -> dict:
    """Shared fields present in every channel row, as ``to_row`` writes them today."""
    return {
        "channel": channel,
        "source": "deribit",
        "symbol": "deribit:BTC-PERPETUAL",
        "symbol_raw": "BTC-PERPETUAL",
        "source_ts": _BASE_TS,
        "local_ts": _BASE_TS,
        "asset_class": "crypto",
        "date": "2023-11-14",
        "bucket": 42,
    }


def _premerge_base(channel: str) -> dict:
    """The same row as a crypto lake written before the union merge holds it.

    ``exchange`` / ``exchange_ts``, no ``asset_class``, no provenance tail. The
    lake migration renames partition directories without rewriting a Parquet
    byte, so this shape does not age out — every file already on disk keeps it.
    """
    return {
        "channel": channel,
        "exchange": "deribit",
        "symbol": "deribit:BTC-PERPETUAL",
        "symbol_raw": "BTC-PERPETUAL",
        "exchange_ts": _BASE_TS,
        "local_ts": _BASE_TS,
        "date": "2023-11-14",
        "bucket": 42,
    }


def test_from_row_book_snapshot():
    row = {
        **_base("book_snapshot"),
        "bids": [{"price": 100.0, "amount": 5.0}, {"price": 99.0, "amount": 2.0}],
        "asks": [{"price": 101.0, "amount": 4.0}],
        "depth": 3,
        "sequence_id": 100,
        "is_snapshot": True,
    }
    rec = from_row(row)
    assert isinstance(rec, BookSnapshot)
    assert rec.bids == [(100.0, 5.0), (99.0, 2.0)]
    assert rec.asks == [(101.0, 4.0)]
    assert rec.depth == 3
    assert rec.sequence_id == 100
    assert rec.is_snapshot is True


def test_from_row_book_snapshot_default_is_snapshot():
    """is_snapshot defaults to True when the key is missing."""
    row = {
        **_base("book_snapshot"),
        "bids": [],
        "asks": [],
        "depth": 0,
    }
    rec = from_row(row)
    assert isinstance(rec, BookSnapshot)
    assert rec.is_snapshot is True


def test_from_row_book_delta_zero_amount_preserved():
    row = {
        **_base("book_delta"),
        "bids": [{"price": 99.0, "amount": 0.0}, {"price": 100.0, "amount": 7.0}],
        "asks": [{"price": 102.0, "amount": 1.0}],
        "seq_id": 101,
        "prev_seq_id": 100,
        "is_snapshot": False,
    }
    rec = from_row(row)
    assert isinstance(rec, BookDelta)
    # amount=0.0 (canonical removal) must survive round-trip
    assert (99.0, 0.0) in rec.bids
    assert (100.0, 7.0) in rec.bids
    assert rec.seq_id == 101
    assert rec.prev_seq_id == 100
    assert rec.is_snapshot is False


def test_from_row_book_delta_default_is_snapshot():
    """is_snapshot defaults to False when the key is missing."""
    row = {
        **_base("book_delta"),
        "bids": [],
        "asks": [],
    }
    rec = from_row(row)
    assert isinstance(rec, BookDelta)
    assert rec.is_snapshot is False


def test_from_row_book_ticker():
    row = {
        **_base("book_ticker"),
        "bid_px": 49999.0,
        "bid_sz": 1.2,
        "ask_px": 50001.0,
        "ask_sz": 0.8,
        "update_id": 99,
    }
    rec = from_row(row)
    assert isinstance(rec, BookTicker)
    assert rec.bid_px == 49999.0
    assert rec.bid_sz == 1.2
    assert rec.ask_px == 50001.0
    assert rec.ask_sz == 0.8
    assert rec.update_id == 99


def test_from_row_book_ticker_no_update_id():
    """update_id is optional and defaults to None."""
    row = {
        **_base("book_ticker"),
        "bid_px": 1.0,
        "bid_sz": 1.0,
        "ask_px": 2.0,
        "ask_sz": 1.0,
    }
    rec = from_row(row)
    assert isinstance(rec, BookTicker)
    assert rec.update_id is None


def test_from_row_derivative_ticker():
    row = {
        **_base("derivative_ticker"),
        "last_price": 50000.0,
        "mark_price": 50000.4,
        "index_price": 50001.0,
        "funding_rate": 0.0001,
        "predicted_funding_rate": 0.0003,
        "funding_timestamp": _BASE_TS + 28800_000_000_000,
        "open_interest": 12345.0,
    }
    rec = from_row(row)
    assert isinstance(rec, DerivativeTicker)
    assert rec.mark_price == 50000.4
    assert rec.index_price == 50001.0
    assert rec.funding_rate == 0.0001
    assert rec.open_interest == 12345.0


def test_from_row_derivative_ticker_all_nullable_none():
    """All nullable fields absent → all default to None."""
    row = _base("derivative_ticker")
    rec = from_row(row)
    assert isinstance(rec, DerivativeTicker)
    assert rec.last_price is None
    assert rec.mark_price is None
    assert rec.funding_rate is None


def test_from_row_options_chain():
    row = {
        **_base("options_chain"),
        "symbol": "deribit:BTC-30JUN-50000-C",
        "symbol_raw": "BTC-30JUN-50000-C",
        "underlying": "BTC",
        "underlying_price": 50000.0,
        "strike": 50000.0,
        "expiry": 1_900_000_000_000_000_000,
        "opt_type": "C",
        "mark_price": 0.05,
        "mark_iv": 65.0,
        "bid_px": 0.04,
        "bid_sz": 2.0,
        "bid_iv": 64.0,
        "ask_px": 0.06,
        "ask_sz": 1.0,
        "ask_iv": 66.0,
        "last_price": 0.045,
        "open_interest": 10.0,
        "delta": 0.5,
        "gamma": 0.001,
        "vega": 12.0,
        "theta": -3.0,
        "rho": 1.0,
    }
    rec = from_row(row)
    assert isinstance(rec, OptionsChain)
    assert rec.opt_type == OptType.CALL
    assert rec.strike == 50000.0
    assert rec.mark_iv == 65.0
    assert rec.delta == 0.5
    assert rec.bid_iv == 64.0


def test_from_row_options_chain_put():
    """opt_type 'P' must deserialise to OptType.PUT."""
    row = {
        **_base("options_chain"),
        "symbol": "deribit:BTC-30JUN-50000-P",
        "symbol_raw": "BTC-30JUN-50000-P",
        "underlying": "BTC",
        "underlying_price": None,
        "strike": 50000.0,
        "expiry": 1_900_000_000_000_000_000,
        "opt_type": "P",
    }
    rec = from_row(row)
    assert isinstance(rec, OptionsChain)
    assert rec.opt_type == OptType.PUT


def test_from_row_funding():
    row = {
        **_base("funding"),
        "funding_rate": 0.0001,
        "funding_timestamp": _BASE_TS + 28800_000_000_000,
        "predicted_funding_rate": 0.0003,
        "interval_hours": 8,
    }
    rec = from_row(row)
    assert isinstance(rec, Funding)
    assert rec.funding_rate == 0.0001
    assert rec.predicted_funding_rate == 0.0003
    assert rec.interval_hours == 8


def test_from_row_funding_nullable_defaults():
    """predicted_funding_rate, funding_timestamp, interval_hours default to None."""
    row = {**_base("funding"), "funding_rate": 0.0002}
    rec = from_row(row)
    assert isinstance(rec, Funding)
    assert rec.funding_rate == 0.0002
    assert rec.predicted_funding_rate is None
    assert rec.funding_timestamp is None
    assert rec.interval_hours is None


def test_from_row_open_interest():
    row = {
        **_base("open_interest"),
        "open_interest": 99999.0,
        "open_interest_value": 5_000_000.0,
    }
    rec = from_row(row)
    assert isinstance(rec, OpenInterest)
    assert rec.open_interest == 99999.0
    assert rec.open_interest_value == 5_000_000.0


def test_from_row_open_interest_no_value():
    """open_interest_value is optional."""
    row = {**_base("open_interest"), "open_interest": 1.0}
    rec = from_row(row)
    assert isinstance(rec, OpenInterest)
    assert rec.open_interest_value is None


def test_from_row_liquidation():
    row = {
        **_base("liquidation"),
        "price": 48950.0,
        "amount": 1.5,
        "side": "sell",
        "id": "liq-001",
    }
    rec = from_row(row)
    assert isinstance(rec, Liquidation)
    assert rec.price == 48950.0
    assert rec.amount == 1.5
    assert rec.side == Side.SELL
    assert rec.id == "liq-001"


def test_from_row_liquidation_no_id():
    """id field is optional for Liquidation."""
    row = {**_base("liquidation"), "price": 1.0, "amount": 1.0, "side": "buy"}
    rec = from_row(row)
    assert isinstance(rec, Liquidation)
    assert rec.id is None


def test_from_row_ohlcv():
    row = {
        **_base("ohlcv"),
        "interval": "1m",
        "open": 50000.0,
        "high": 50500.0,
        "low": 49800.0,
        "close": 50200.0,
        "volume": 123.45,
        "buy_volume": 80.0,
        "sell_volume": 43.45,
        "num_trades": 1000,
    }
    rec = from_row(row)
    assert isinstance(rec, OHLCV)
    assert rec.interval == "1m"
    assert rec.open == 50000.0
    assert rec.close == 50200.0
    assert rec.volume == 123.45
    assert rec.buy_volume == 80.0
    assert rec.sell_volume == 43.45
    assert rec.num_trades == 1000


def test_from_row_ohlcv_buy_sell_volume_defaults():
    """buy_volume and sell_volume default to 0.0 when absent."""
    row = {
        **_base("ohlcv"),
        "interval": "1h",
        "open": 1.0,
        "high": 2.0,
        "low": 0.5,
        "close": 1.5,
        "volume": 10.0,
    }
    rec = from_row(row)
    assert isinstance(rec, OHLCV)
    assert rec.buy_volume == 0.0
    assert rec.sell_volume == 0.0
    assert rec.num_trades is None


def test_from_row_unknown_channel_raises():
    """from_row raises ValueError for unrecognised channel tags."""
    import pytest

    with pytest.raises(ValueError, match="Unknown channel tag"):
        from_row({**_base("not_a_channel")})


# ---------------------------------------------------------------------------
# Rows written before the union merge
# ---------------------------------------------------------------------------


def test_a_row_from_a_premerge_crypto_lake_still_reconstructs():
    """`exchange` reads as `source` and `exchange_ts` as `source_ts`.

    Those files are not going to be rewritten — `migrate_lake` renames
    directories and never touches a Parquet byte — so `from_row` is the only
    place the two spellings can meet. Reading such a row as anything other than
    the record it describes would empty the query rather than fail it.
    """
    rec = from_row(
        {
            **_premerge_base("trade"),
            "id": "7",
            "price": 42.0,
            "amount": 1.5,
            "side": "buy",
        }
    )
    assert isinstance(rec, Trade)
    assert rec.source == "deribit"
    assert rec.source_ts == _BASE_TS
    assert rec.price == 42.0


def test_a_premerge_row_reads_back_as_crypto():
    """The `exchange` column is itself the evidence, not a guess.

    `exchange` was the crypto union's word for the origin and the equity fork
    said `provider`, so a row carrying one and no `asset_class` can only have
    been written by the crypto side.
    """
    rec = from_row(
        {
            **_premerge_base("open_interest"),
            "open_interest": 1234.0,
        }
    )
    assert rec.asset_class is AssetClass.CRYPTO


async def test_an_equity_row_does_not_read_back_as_crypto(tmp_path: pathlib.Path) -> None:
    """The equity file schema *has* an ``exchange`` column, and it is always null.

    ``_EQUITY_COMMON_FIELDS`` declares it for compatibility with equity lakes
    written before the merge, nothing populates it, and the sink materialises
    every schema key as a column — so ``polars.to_dicts()`` on any equity file
    yields ``"exchange": None``. Testing for the *key* therefore matched equity
    rows too and stamped them ``CRYPTO``: no exception, an ``ohlcv`` row whose
    field names overlap enough that nothing else raised, and a lake query that
    answers with the wrong market. The row is written through the real sink
    rather than hand-built because the null column is the whole point.

    The record class is stated here rather than imported: the equity connectors emit
    canonical records now, so the fork's ``OHLCV`` has no producer and the shape below is
    frozen history — which is also what the pre-migration files it stands in for are.
    ``ParquetSink`` reads the tag and the fields off whatever it is handed, so this goes
    through the real writer and picks the real equity file schema.
    """

    class _PreMergeEquityOHLCV(msgspec.Struct, frozen=True, tag="ohlcv", tag_field="channel"):
        provider: str
        symbol: str
        symbol_raw: str
        local_ts: int
        interval: str
        open: float
        high: float
        low: float
        close: float
        volume: float
        source_ts: int | None = None
        vwap: float | None = None
        trade_count: int | None = None

    sink = ParquetSink(data_dir=tmp_path, max_buffer_rows=1000, flush_interval_seconds=9999)
    await sink.put(
        _PreMergeEquityOHLCV(  # type: ignore[arg-type]
            provider="alpaca",
            symbol="AAPL",
            symbol_raw="AAPL",
            local_ts=_BASE_TS,
            source_ts=_BASE_TS,
            interval="1m",
            open=10.0,
            high=20.0,
            low=5.0,
            close=15.0,
            volume=100.0,
            vwap=15.0,
        )
    )
    await sink.flush()

    (row,) = pl.read_parquet(sorted(tmp_path.rglob("part-*.parquet"))).to_dicts()
    assert "exchange" in row and row["exchange"] is None, (
        "the premise of this test: the column is present and null on every equity file"
    )

    rec = from_row(row)
    assert rec.asset_class is AssetClass.EQUITY
    assert rec.source == "alpaca"


def test_a_premerge_row_reads_back_as_natively_reported():
    """A file with no provenance tail is not a file with unknown provenance.

    The fork only ever wrote venue-reported records, so the struct defaults are
    the truth about those rows rather than a placeholder.
    """
    rec = from_row({**_premerge_base("funding"), "funding_rate": 0.0001})
    assert rec.prov is Provenance.NATIVE
    assert rec.prov_basis == "native"
    assert rec.prov_confidence == 1.0
    assert rec.prov_inputs == []


def test_a_row_that_names_no_market_is_refused():
    """Inventing an asset class is indistinguishable from having recorded one.

    A row with neither `asset_class` nor the pre-migration `exchange` column has
    not said which market it came from, and a default here would be a claim the
    lake never made.
    """
    import pytest

    row = _base("trade")
    del row["asset_class"]
    with pytest.raises(KeyError, match="asset_class"):
        from_row({**row, "id": "1", "price": 1.0, "amount": 1.0, "side": "buy"})


def test_the_provenance_tail_survives_a_round_trip():
    """A derived record must not read back as a native one.

    `to_row` flattens the four `prov_*` fields and nothing downstream restores
    them unless `from_row` does; a record that loses them on the way back is a
    modelled value presented as a venue-reported one.
    """
    original = OHLCV(
        source="deribit",
        symbol="deribit:BTC-PERPETUAL",
        symbol_raw="BTC-PERPETUAL",
        source_ts=None,
        local_ts=_BASE_TS,
        asset_class=AssetClass.CRYPTO,
        prov=Provenance.SYNTHETIC,
        prov_basis="yahoo_1m_vap",
        prov_confidence=0.5,
        prov_inputs=["bar"],
        interval="1m",
        open=1.0,
        high=2.0,
        low=0.5,
        close=1.5,
        volume=10.0,
    )
    assert from_row(to_row(original)) == original


# ---------------------------------------------------------------------------
# What the reader says when the row cannot supply a field
# ---------------------------------------------------------------------------


def test_an_all_legacy_row_names_the_column_that_is_there_not_one_that_is_not():
    """I2: the good error did not fire on a lake with no canonical files in it.

    On a lake spanning the migration ``union_by_name`` supplies ``amount`` as a null,
    and the reader raises the ``ValueError`` that names it. On an all-legacy lake there
    is no ``amount`` column at all: the alias found ``size`` null, created no canonical
    key, ``_record_body`` skipped the field on ``if column not in d``, and msgspec raised
    ``TypeError: Missing required argument 'amount'`` — naming a column that appears
    nowhere in the file, several frames from the row that caused it.
    """
    import pytest

    row = {
        "provider": "alpaca",
        "symbol": "AAPL",
        "symbol_raw": "AAPL",
        "source_ts": _BASE_TS,
        "local_ts": _BASE_TS,
        "channel": "trade",
        "id": "L0",
        "price": 150.0,
        "size": None,
    }

    with pytest.raises(ValueError, match=r"Trade\.amount is required"):
        from_row(row)


def test_a_canonical_row_reads_its_origin_from_its_own_family_not_the_listing_venue():
    """I3: ``_header`` walked all three origin names by value and took the first hit.

    A canonical ``Instrument`` read off a bare Parquet file has no ``source`` column —
    it is a path component — and does have ``exchange``, which on this record is where
    the security is *listed*. The walk reported ``source='NASDAQ'``: the exact confusion
    ``_FAMILY_MARKERS`` and ``_inventory_for_channel`` each exist to prevent, reproduced
    on the read side.
    """
    import pytest

    from crocodile.core.schema.records import Instrument

    row = {
        "symbol": "AAPL",
        "symbol_raw": "AAPL",
        "local_ts": _BASE_TS,
        "source_ts": _BASE_TS,
        "asset_class": "equity",
        "channel": "instrument",
        "exchange": "NASDAQ",
        "name": "Apple Inc.",
    }

    with pytest.raises(KeyError, match="canonical"):
        from_row(row)

    record = from_row({**row, "source": "alpaca"})
    assert isinstance(record, Instrument)
    assert record.source == "alpaca"
    assert record.exchange == "NASDAQ"
