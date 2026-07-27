"""Every equity channel, flattened and rebuilt through the canonical row codec.

The equity providers build ``crocodile.core.schema.records`` structs now, so the flattener
under test is ``crocodile.core.store.rows`` — the one the sink actually calls. The equity
fork's own ``crocodile.equity.store.rows`` still exists to read pre-migration files off
disk and is exercised in ``tests/equity/store/test_rows.py``.

``tests/conformance/test_lake_symmetry.py`` round-trips all 30 canonical channels, but
every record it builds is stamped ``crypto``. The half that would go wrong for equity is
not the channel list: it is the header. So the records here are the ones equity actually
writes, they all carry ``asset_class=EQUITY``, and the last test takes one through the
real sink and back to prove the market survives the file.
"""

from __future__ import annotations

import pathlib

import polars as pl

from crocodile.core.schema.enums import (
    AssetClass,
    CorpActionType,
    FundPeriod,
    OptType,
    SecurityType,
    Side,
    Tape,
)
from crocodile.core.schema.records import (
    OHLCV,
    Auction,
    BookDelta,
    BookSnapshot,
    CorporateAction,
    Filing,
    Fundamental,
    Holding13F,
    IndexValue,
    InsiderTransaction,
    Instrument,
    MacroSeries,
    OptionsChain,
    Quote,
    ShortInterest,
    ShortVolume,
    Trade,
    TradingStatus,
)
from crocodile.core.store.parquet_sink import ParquetSink
from crocodile.core.store.rows import from_row, to_row

_EQUITY = {"asset_class": AssetClass.EQUITY}


def test_trade_to_from_row() -> None:
    t = Trade(
        source="alpaca",
        symbol="AAPL",
        symbol_raw="AAPL",
        source_ts=1700000000000000000,
        local_ts=1700000005000000000,
        **_EQUITY,
        id="t-12345",
        price=180.5,
        amount=100.0,
        # A consolidated-tape print names no aggressor. `Side.UNKNOWN` is the claim the
        # tape makes; omitting `side` is not available, and would have been an adapter
        # that did not say rather than a venue that does not classify.
        side=Side.UNKNOWN,
        conditions=["@", "F"],
        tape=Tape.A,
        venue="IEX",
    )
    row = to_row(t)
    assert row["channel"] == "trade"
    assert row["symbol"] == "AAPL"
    assert row["tape"] == "A"
    assert row["asset_class"] == "equity"
    assert "date" in row
    assert "bucket" in row
    assert isinstance(row["bucket"], int)

    assert from_row(row) == t


def test_quote_to_from_row() -> None:
    q = Quote(
        source="alpaca",
        symbol="MSFT",
        symbol_raw="MSFT",
        source_ts=1700000000000000000,
        local_ts=1700000005000000000,
        **_EQUITY,
        bid_px=350.0,
        bid_sz=10.0,
        ask_px=350.1,
        ask_sz=20.0,
        is_nbbo=True,
        is_consolidated=True,
        conditions=["R"],
        tape=Tape.B,
    )
    row = to_row(q)
    assert row["channel"] == "quote"
    assert row["bid_px"] == 350.0
    assert row["tape"] == "B"

    assert from_row(row) == q


def test_book_snapshot_to_from_row() -> None:
    b = BookSnapshot(
        source="iex",
        symbol="AAPL",
        symbol_raw="AAPL",
        source_ts=1700000000000000000,
        local_ts=1700000005000000000,
        **_EQUITY,
        bids=[(180.0, 10.0), (179.9, 20.0)],
        asks=[(180.1, 5.0), (180.2, 15.0)],
        depth=2,
        sequence_id=98765,
        is_snapshot=True,
    )
    row = to_row(b)
    assert row["channel"] == "book_snapshot"
    # bids/asks preserved as lists of tuples/lists
    assert row["bids"] == [(180.0, 10.0), (179.9, 20.0)]

    assert from_row(row) == b


def test_book_delta_to_from_row() -> None:
    d = BookDelta(
        source="iex",
        symbol="AAPL",
        symbol_raw="AAPL",
        source_ts=1700000000000000000,
        local_ts=1700000005000000000,
        **_EQUITY,
        bids=[(180.0, 0.0)],  # remove level
        asks=[],
        seq_id=98766,
        prev_seq_id=98765,
        is_snapshot=False,
    )
    row = to_row(d)
    assert row["channel"] == "book_delta"

    assert from_row(row) == d


def test_corporate_action_to_from_row() -> None:
    """``CorporateAction.type`` keeps its name.

    It is the one field on this record that a keyword-level rename sweep would have
    caught by accident: the *option* record's ``type`` became ``opt_type`` because it
    shadowed the builtin inside a chain, and nothing about that argument applies here.
    """
    c = CorporateAction(
        source="sec_edgar",
        symbol="AAPL",
        symbol_raw="AAPL",
        source_ts=1700000000000000000,
        local_ts=1700000005000000000,
        **_EQUITY,
        ex_date="2026-06-15",
        type=CorpActionType.DIVIDEND_CASH,
        value=0.5,
    )
    row = to_row(c)
    assert row["channel"] == "corp_action"
    assert row["type"] == "dividend_cash"

    assert from_row(row) == c


def test_ohlcv_to_from_row() -> None:
    """One bar record, not two.

    The equity fork declared ``Bar`` (tag ``bar``) and ``OHLCV`` (tag ``ohlcv``) with
    identical fields, and this file used to round-trip both. Nothing constructs ``bar``
    now; a lake written under the old tag still decodes because
    ``crocodile.core.schema.enums.CHANNEL_SUCCESSORS`` maps it onto ``ohlcv`` and every
    glob widens to cover it — not because ``Channel.BAR`` is still declared, which on its
    own decoded nothing. ``tests/store/test_premerge_equity_rows.py`` exercises that path.
    """
    o = OHLCV(
        source="stooq",
        symbol="AAPL",
        symbol_raw="AAPL",
        source_ts=None,
        local_ts=1700000005000000000,
        **_EQUITY,
        interval="1d",
        open=180.0,
        high=182.0,
        low=179.5,
        close=181.2,
        volume=5000000.0,
        vwap=180.8,
        num_trades=1234,
    )
    row = to_row(o)
    assert row["channel"] == "ohlcv"

    assert from_row(row) == o


def test_fundamental_to_from_row() -> None:
    f = Fundamental(
        source="sec_edgar",
        symbol="AAPL",
        symbol_raw="AAPL",
        source_ts=1700000000000000000,
        local_ts=1700000005000000000,
        **_EQUITY,
        taxonomy="us-gaap",
        tag="Revenues",
        unit="USD",
        val=90000000000.0,
        end="2025-12-31",
        start="2025-10-01",
        fy=2025,
        fp=FundPeriod.Q4,
        form="10-K",
        filed="2026-01-30",
        accn="0000320193-26-000010",
        frame="CY2025Q4",
    )
    row = to_row(f)
    assert row["channel"] == "fundamental"

    assert from_row(row) == f


def test_filing_to_from_row() -> None:
    f = Filing(
        source="sec_edgar",
        symbol="AAPL",
        symbol_raw="AAPL",
        source_ts=1700000000000000000,
        local_ts=1700000005000000000,
        **_EQUITY,
        accession_number="0000320193-26-000010",
        form="10-K",
        filing_date="2026-01-30",
        primary_document="aapl-20251231.htm",
        document_url=(
            "https://www.sec.gov/Archives/edgar/data/320193/"
            "000032019326000010/aapl-20251231.htm"
        ),
        report_date="2025-12-31",
        is_xbrl=True,
    )
    row = to_row(f)
    assert row["channel"] == "filing"

    assert from_row(row) == f


def test_index_value_to_from_row() -> None:
    i = IndexValue(
        source="fred",
        symbol="SP500",
        symbol_raw="SP500",
        source_ts=1700000000000000000,
        local_ts=1700000005000000000,
        **_EQUITY,
        value=5000.5,
    )
    row = to_row(i)
    assert row["channel"] == "index_value"

    assert from_row(row) == i


def test_new_records_to_from_row() -> None:
    # 1. Auction
    a = Auction(
        source="nasdaq",
        symbol="AAPL",
        symbol_raw="AAPL",
        source_ts=1700000000000000000,
        local_ts=1700000005000000000,
        **_EQUITY,
        paired_shares=100000.0,
        imbalance_shares=5000.0,
        imbalance_side=Side.BUY,
        reference_price=180.5,
        indicative_price=180.6,
        auction_type="open",
    )
    assert from_row(to_row(a)) == a

    # 2. TradingStatus
    ts = TradingStatus(
        source="nasdaq",
        symbol="AAPL",
        symbol_raw="AAPL",
        source_ts=1700000000000000000,
        local_ts=1700000005000000000,
        **_EQUITY,
        status="H",
        reason="LULD",
        limit_up_price=190.0,
        limit_down_price=170.0,
        indicator="Y",
    )
    assert from_row(to_row(ts)) == ts

    # 3. Instrument. ``exchange`` here is the *listing venue* and stays ``exchange``;
    # ``source`` is who served the record. Collapsing the two filed an Alpaca-sourced
    # instrument under ``source=NASDAQ``, which is why they are both asserted below.
    inst = Instrument(
        source="alpaca",
        symbol="AAPL",
        symbol_raw="AAPL",
        source_ts=1700000000000000000,
        local_ts=1700000005000000000,
        **_EQUITY,
        name="Apple Inc.",
        cik="0000320193",
        figi="BBG000B9Y5X2",
        composite_figi="BBG000B9Y5X2",
        share_class_figi="BBG001S5N8V8",
        cusip="037833100",
        exchange="NASDAQ",
        security_type=SecurityType.CS,
        sic="3571",
        shares_outstanding=15000000000,
        listing_date="1980-12-12",
        status="active",
    )
    inst_row = to_row(inst)
    assert inst_row["source"] == "alpaca"
    assert inst_row["exchange"] == "NASDAQ"
    assert from_row(inst_row) == inst

    # 4. InsiderTransaction
    ins = InsiderTransaction(
        source="yahoo",
        symbol="AAPL",
        symbol_raw="AAPL",
        source_ts=1700000000000000000,
        local_ts=1700000005000000000,
        **_EQUITY,
        insider_name="Cook Timothy D",
        position="CEO",
        transaction_type="Sale",
        transaction_date="2026-06-01",
        shares=10000.0,
        price=180.5,
        value=1805000.0,
        ownership="D",
    )
    assert from_row(to_row(ins)) == ins

    # 5. Holding13F
    h13 = Holding13F(
        source="sec_edgar",
        symbol="AAPL",
        symbol_raw="AAPL",
        source_ts=1700000000000000000,
        local_ts=1700000005000000000,
        **_EQUITY,
        manager_name="BERKSHIRE HATHAWAY INC",
        issuer_name="APPLE INC",
        cusip="037833100",
        value=150000000.0,
        shares=1000000.0,
        shares_type="SH",
        discretion="SOLE",
        voting_sole=1000000.0,
        voting_shared=0.0,
        voting_none=0.0,
        report_date="2025-12-31",
        accession_number="0000320193-26-000010",
    )
    assert from_row(to_row(h13)) == h13

    # 6. ShortInterest
    si = ShortInterest(
        source="finra",
        symbol="AAPL",
        symbol_raw="AAPL",
        source_ts=1700000000000000000,
        local_ts=1700000005000000000,
        **_EQUITY,
        settlement_date="2026-05-15",
        short_interest=50000000.0,
        prev_short_interest=48000000.0,
        days_to_cover=1.5,
        change_pct=4.17,
    )
    assert from_row(to_row(si)) == si

    # 7. ShortVolume. Its ``date`` is the settlement day and the partition ``date`` is
    # the capture day; the flattener moves the first to ``date_val`` so the second does
    # not overwrite it.
    sv = ShortVolume(
        source="finra",
        symbol="AAPL",
        symbol_raw="AAPL",
        source_ts=1700000000000000000,
        local_ts=1700000005000000000,
        **_EQUITY,
        date="2026-06-18",
        short_volume=1200000.0,
        short_exempt_volume=15000.0,
        total_volume=3000000.0,
    )
    sv_row = to_row(sv)
    assert sv_row["date_val"] == "2026-06-18"
    assert sv_row["date"] == "2023-11-14"
    assert from_row(sv_row) == sv

    # 8. OptionsChain. Expiry is UTC epoch nanoseconds, not "YYYY-MM-DD" — see
    # ``crocodile.equity.providers.yahoo.client._expiry_to_ns`` for the instant and why.
    oc = OptionsChain(
        source="yahoo",
        symbol="AAPL260618C00180000",
        symbol_raw="AAPL260618C00180000",
        source_ts=1700000000000000000,
        local_ts=1700000005000000000,
        **_EQUITY,
        underlying="AAPL",
        underlying_price=None,
        expiry=1781740800000000000,  # 2026-06-18T00:00:00Z
        strike=180.0,
        opt_type=OptType.CALL,
        bid_px=5.5,
        ask_px=5.7,
        last_price=5.6,
        volume=1200.0,
        open_interest=5000.0,
        mark_iv=0.25,
        delta=0.55,
        gamma=0.03,
        vega=0.15,
        theta=-0.05,
        rho=0.08,
    )
    oc_row = to_row(oc)
    assert oc_row["opt_type"] == "C", "the wire value is unchanged; only the member renamed"
    assert from_row(oc_row) == oc

    # 9. MacroSeries — the other ``date`` collision.
    ms = MacroSeries(
        source="fred",
        symbol="UNRATE",
        symbol_raw="UNRATE",
        source_ts=1700000000000000000,
        local_ts=1700000005000000000,
        **_EQUITY,
        date="2026-05-01",
        value=3.9,
        realtime_start="2026-06-05",
        realtime_end="9999-12-31",
    )
    assert from_row(to_row(ms)) == ms


async def test_an_equity_row_written_canonically_reads_back_as_equity(
    tmp_path: pathlib.Path,
) -> None:
    """The market survives the file, not just the flattener.

    The crypto migration shipped the inverse of this bug and a reviewer caught it: an
    equity row read back stamped ``CRYPTO`` because ``_header`` tested a marker column for
    presence rather than for value, and an ``ohlcv`` row's field names overlap enough
    between the forks that nothing raised. Nothing downstream filters by asset class, so
    nothing was positioned to notice.

    Written and read the way the product does it — through ``ParquetSink`` and back
    through ``from_row`` — because the round trip is where the header can be lost.
    ``hive_partitioning`` is required rather than incidental: ``source`` is a path
    component and is deliberately absent from every canonical file schema.
    """
    original = OHLCV(
        source="alpaca",
        symbol="AAPL",
        symbol_raw="AAPL",
        local_ts=1700000005000000000,
        source_ts=1700000000000000000,
        **_EQUITY,
        interval="1m",
        open=10.0,
        high=20.0,
        low=5.0,
        close=15.0,
        volume=100.0,
        vwap=15.0,
        num_trades=7,
    )

    sink = ParquetSink(data_dir=tmp_path, max_buffer_rows=1000, flush_interval_seconds=9999)
    await sink.put(original)
    await sink.flush()

    df = pl.read_parquet(str(tmp_path / "**" / "*.parquet"), hive_partitioning=True)
    (row,) = df.to_dicts()

    rec = from_row(row)
    assert rec.asset_class is AssetClass.EQUITY
    assert rec.source == "alpaca"
    assert rec == original


async def test_an_equity_instrument_reads_back_as_equity_despite_its_exchange(
    tmp_path: pathlib.Path,
) -> None:
    """The record that carries the marker the old reader mistook for a market.

    ``Instrument`` is the only equity record with a populated ``exchange`` column, and
    ``exchange`` was the retired crypto union's word for the origin. A reader that decides
    the market from that column's *presence* — which is what shipped, and what stamped
    every equity row ``CRYPTO`` — reads this one as crypto and every other equity record
    correctly, so a bar-shaped fixture proves nothing. This is the case with teeth:
    mutating ``_header`` back to ``AssetClass.CRYPTO if "exchange" in d`` fails here and
    nowhere else in this file.
    """
    original = Instrument(
        source="alpaca",
        symbol="AAPL",
        symbol_raw="AAPL",
        local_ts=1700000005000000000,
        source_ts=1700000000000000000,
        **_EQUITY,
        name="Apple Inc.",
        exchange="NASDAQ",
        security_type=SecurityType.CS,
    )

    sink = ParquetSink(data_dir=tmp_path, max_buffer_rows=1000, flush_interval_seconds=9999)
    await sink.put(original)
    await sink.flush()

    df = pl.read_parquet(str(tmp_path / "**" / "*.parquet"), hive_partitioning=True)
    (row,) = df.to_dicts()
    assert row["exchange"] == "NASDAQ", "the premise: the column is present and populated"

    rec = from_row(row)
    assert rec.asset_class is AssetClass.EQUITY
    assert rec.source == "alpaca", "who served the record, not where the security lists"
    assert rec == original
