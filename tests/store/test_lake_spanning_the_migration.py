"""One lake, three dialects, two on-disk layouts, in the same partition directories.

Most of the review's findings share this scenario and none of them is visible without
it. ``migrate_lake`` renames partition directories and never rewrites a Parquet byte,
so a source that was collected before the union merge and again after it ends up with
two column sets under one ``channel=`` directory — and ``Catalog`` reads them with
``union_by_name => true``, which makes every column present on every row and only the
*values* tell the two apart.

What that produced before the family dispatch:

* ``replay(["trade"])`` returned ``Trade(amount=None, side=None)`` for the
  pre-migration half — no exception, and the caller's ``sum(amount)`` raised
  ``TypeError`` several frames away.
* ``replay(["bar"])`` raised ``ValueError: Unknown channel tag: 'bar'``, so the rows
  under the retired tag were unreachable through the record API at all. *That* was
  the loss. ``replay(["ohlcv"])`` returning only ``channel=ohlcv/`` was never the
  bug — a retired tag is its own channel and is read by asking for it.
* the bars that did come back reported ``num_trades=None`` — the encoding reserved
  for "the source did not publish one" — because the column is spelled
  ``trade_count`` on the older files.

**Reachability, not transparency.** Three review rounds tried to make ``ohlcv``
silently cover ``channel=bar/`` and each one broke something new: duplicated rows,
then a dedup key on ``(symbol, local_ts, interval)`` that discarded 249 of a 250-bar
Yahoo history, because a provider stamps a whole fetched history with one
``local_ts`` and the key carries no ``source``. So the tests below assert the
property that actually matters — every row on disk is reachable through the record
API under some channel name, and the counts add up — and never that one name answers
for two directories. Rewriting the tag is ``migrate-lake``'s job.

**The layout is the point, and the first version of this file got it wrong.** It
wrote its legacy half *with* a ``bucket=`` level, which is not the layout the equity
fork used — that fork wrote its parts directly under ``date=``, which is why
``Catalog`` carries two entries in ``_PART_TAILS`` at all. A fixture in the layout
that cannot fail is a fixture written so that it passes rather than so that it
probes: the grouping bug in ``_create_view`` fires only on the real shape, and cost a
real legacy equity lake its ``ohlcv`` view entirely while every test here stayed
green.

So the equity half is flat, the crypto half is bucketed (crypto always wrote one),
and the canonical half is bucketed because that is what ``ParquetSink`` writes today.
Both halves go through a real schema — the canonical one through the sink itself, the
legacy ones through the schema ``ParquetSink`` declares for each family — so no side
is a hand-shaped dict pretending to be a file.

**The five on-chain tags are here because their structs were deleted.** The equity fork
carried its own ``limit_order_fill``, ``por_update``, ``balance_correction``,
``reserve_data_updated`` and ``liquidation_call``, and the union merge kept only the
crypto copies. The rows those structs wrote are still on disk, so each tag gets a
legacy-dialect partition below and the reachability test counts it. Their only previous
coverage constructed the retired structs directly, which is coverage that leaves with
them.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from crocodile.core.schema.enums import AssetClass, OptType, Side
from crocodile.core.schema.provenance import Provenance
from crocodile.core.schema.records import (
    OHLCV,
    BalanceCorrection,
    DepthProfile,
    Instrument,
    LimitOrderFill,
    LiquidationCall,
    OptionsChain,
    PoRUpdate,
    ReserveDataUpdated,
    Trade,
)
from crocodile.core.store.catalog import Catalog
from crocodile.core.store.parquet_sink import ParquetSink, _channel_schema
from crocodile.core.store.rows import (
    FAMILY_CRYPTO,
    FAMILY_EQUITY,
    _date_from_ns,
    _symbol_bucket,
)
from crocodile.equity.client.client import StockodileClient

_DAY = 86_400_000_000_000
_TS = 1_700_000_000_000_000_000  # 2023-11-14T22:13:20Z
_SYMBOL = "AAPL"
_SOURCE = "alpaca"

_CRYPTO_SYMBOL = "binance:BTC-USDT"
_CRYPTO_SOURCE = "binance"

_ONCHAIN_SYMBOL = "ETH-USDC"
_ONCHAIN_SOURCE = "base_onchain"

_LEGACY_ONCHAIN_BODIES: dict[str, dict[str, Any]] = {
    "limit_order_fill": {
        "tx_hash": "0xabc",
        "log_index": 1,
        "protocol": "1inch",
        "maker": "0x1",
        "taker": "0x2",
        "maker_token": "0xa",
        "taker_token": "0xb",
        "maker_amount": 1.5,
        "taker_amount": 2.5,
        "order_hash": "0xord",
    },
    "por_update": {
        "feed_address": "0xfeed",
        "token_address": "0xtok",
        "reserves": 100.0,
        "total_supply": 100.0,
        "backing_ratio": 1.0,
        "is_backed": True,
    },
    "balance_correction": {
        "holder_address": "0xhold",
        "token_address": "0xtok",
        "local_balance": 9.0,
        "onchain_balance": 10.0,
        "correction_amount": 1.0,
    },
    "reserve_data_updated": {
        "reserve": "0xres",
        "liquidity_rate": 0.02,
        "stable_borrow_rate": 0.05,
        "variable_borrow_rate": 0.04,
        "liquidity_index": 11,
        "variable_borrow_index": 12,
    },
    "liquidation_call": {
        "collateral_asset": "0xcol",
        "debt_asset": "0xdebt",
        "user": "0xuser",
        "debt_to_cover": 3.0,
        "liquidated_collateral_amount": 4.0,
        "liquidator": "0xliq",
        "receive_a_token": False,
    },
}
"""The five on-chain records the equity fork declared and the merge deleted.

Field-for-field what the fork's structs held, so each row is the shape its own sink
schema still declares. Every one of these tags names a *crypto* record in the canonical
union; the rows below prove the surviving struct reads the retired one's files.
"""


def _legacy_equity_row(channel: str, local_ts: int, **body: Any) -> dict[str, Any]:
    """A row exactly as the pre-merge equity fork flattened one."""
    return {
        "provider": _SOURCE,
        "symbol": _SYMBOL,
        "symbol_raw": _SYMBOL,
        "source_ts": local_ts,
        "local_ts": local_ts,
        "channel": channel,
        "date": _date_from_ns(local_ts),
        "bucket": _symbol_bucket(_SYMBOL),
        # Vestigial and always null on an equity file. Its presence is why
        # ``_row_family`` discriminates on values rather than on key presence.
        "exchange": None,
        **body,
    }


def _legacy_crypto_row(channel: str, local_ts: int, **body: Any) -> dict[str, Any]:
    """A row exactly as the pre-merge crypto fork flattened one.

    The origin is ``exchange`` and the venue clock is ``exchange_ts``; there is no
    ``asset_class`` and no provenance tail, because the retired crypto union had
    neither. Third dialect, same partition tree.
    """
    return {
        "exchange": _CRYPTO_SOURCE,
        "symbol": _CRYPTO_SYMBOL,
        "symbol_raw": "BTCUSDT",
        "exchange_ts": local_ts,
        "local_ts": local_ts,
        "channel": channel,
        "date": _date_from_ns(local_ts),
        "bucket": _symbol_bucket(_CRYPTO_SYMBOL),
        **body,
    }


def _write_part(
    data_dir: Path,
    source: str,
    channel: str,
    family: str,
    rows: list[dict[str, Any]],
    *,
    bucketed: bool,
    name: str = "part-premigration.parquet",
) -> Path:
    """Write one pre-migration part file into the partition the canonical rows share.

    ``bucketed`` selects the on-disk layout, and the two forks really did differ:
    crypto wrote ``date=…/bucket=…/part-*.parquet`` and equity wrote
    ``date=…/part-*.parquet``. Both are in ``Catalog._PART_TAILS`` for that reason,
    and reading them in one ``read_parquet`` call is what DuckDB refuses.
    """
    schema = _channel_schema(channel, family)
    part_dir = data_dir / f"source={source}" / f"channel={channel}" / f"date={rows[0]['date']}"
    if bucketed:
        part_dir = part_dir / f"bucket={rows[0]['bucket']}"
    part_dir.mkdir(parents=True, exist_ok=True)
    path = part_dir / name
    pl.DataFrame([{k: row.get(k) for k in schema} for row in rows], schema=schema).write_parquet(
        path
    )
    return path


def _canonical_trade(local_ts: int, price: float, amount: float, side: Side) -> Trade:
    return Trade(
        source=_SOURCE,
        symbol=_SYMBOL,
        symbol_raw=_SYMBOL,
        local_ts=local_ts,
        asset_class=AssetClass.EQUITY,
        source_ts=local_ts,
        id=f"c{local_ts}",
        price=price,
        amount=amount,
        side=side,
    )


def _canonical_bar(local_ts: int, close: float, volume: float, num_trades: int) -> OHLCV:
    return OHLCV(
        source=_SOURCE,
        symbol=_SYMBOL,
        symbol_raw=_SYMBOL,
        local_ts=local_ts,
        asset_class=AssetClass.EQUITY,
        source_ts=local_ts,
        interval="1d",
        open=close - 1.0,
        high=close + 1.0,
        low=close - 2.0,
        close=close,
        volume=volume,
        num_trades=num_trades,
    )


@pytest.fixture
def spanning_lake(tmp_path: Path) -> Path:
    """A lake holding pre- and post-migration part files for the same source."""

    async def build() -> None:
        sink = ParquetSink(data_dir=tmp_path, max_buffer_rows=10_000, flush_interval_seconds=9999)
        for i, price in enumerate((153.0, 154.0)):
            await sink.put(_canonical_trade(_TS + 200 + i, price, 100.0, Side.BUY))
        for i, close in enumerate((181.0, 182.0)):
            await sink.put(_canonical_bar(_TS + (i + 3) * _DAY, close, 5_000.0, 900 + i))
        await sink.flush()

    asyncio.run(build())

    # --- the pre-migration equity half: flat, the layout the fork really wrote ---
    _write_part(
        tmp_path,
        _SOURCE,
        "trade",
        FAMILY_EQUITY,
        [
            _legacy_equity_row("trade", _TS + i, id=f"L{i}", price=150.0 + i, size=10.0 + i)
            for i in range(3)
        ],
        bucketed=False,
    )
    _write_part(
        tmp_path,
        _SOURCE,
        "bar",
        FAMILY_EQUITY,
        [
            _legacy_equity_row(
                "bar",
                _TS + i * _DAY,
                interval="1d",
                open=178.0 + i,
                high=180.0 + i,
                low=177.0 + i,
                close=179.0 + i,
                volume=1_000.0 + i,
                vwap=178.5 + i,
                trade_count=77 + i,
            )
            for i in range(3)
        ],
        bucketed=False,
    )
    _write_part(
        tmp_path,
        _SOURCE,
        "depth",
        FAMILY_EQUITY,
        [
            _legacy_equity_row(
                "depth",
                _TS,
                bids=[{"price": 149.0, "size": 300.0}],
                asks=[{"price": 151.0, "size": 400.0}],
                reference_price=150.0,
                basis="yahoo_1m_vap",
                is_synthetic=True,
                depth=1,
            ),
            _legacy_equity_row(
                "depth",
                _TS + 1,
                bids=[{"price": 149.5, "size": 100.0}],
                asks=[{"price": 150.5, "size": 100.0}],
                reference_price=150.0,
                basis="alpaca_l1",
                is_synthetic=False,
                depth=1,
            ),
        ],
        bucketed=False,
    )
    _write_part(
        tmp_path,
        _SOURCE,
        "option_quote",
        FAMILY_EQUITY,
        [
            _legacy_equity_row(
                "option_quote",
                _TS,
                underlying=_SYMBOL,
                expiry="2023-12-15",
                strike=190.0,
                type=OptType.CALL.value,
                bid=1.5,
                ask=1.7,
                last=1.6,
                volume=12.0,
                open_interest=340.0,
                implied_volatility=0.31,
            )
        ],
        bucketed=False,
    )
    _write_part(
        tmp_path,
        _SOURCE,
        "instrument",
        FAMILY_EQUITY,
        [
            _legacy_equity_row(
                "instrument",
                _TS,
                name="Apple Inc.",
                exchange_name="NASDAQ",
                security_type="CS",
                cusip="037833100",
            )
        ],
        bucketed=False,
    )

    # --- the pre-migration crypto half: bucketed, and a third column dialect ---
    _write_part(
        tmp_path,
        _CRYPTO_SOURCE,
        "trade",
        FAMILY_CRYPTO,
        [
            _legacy_crypto_row(
                "trade",
                _TS + i,
                id=f"X{i}",
                price=37_000.0 + i,
                amount=0.25,
                side=Side.SELL.value,
            )
            for i in range(2)
        ],
        bucketed=True,
    )

    # --- the five on-chain tags the equity fork wrote and no longer has structs for ---
    for channel, body in _LEGACY_ONCHAIN_BODIES.items():
        _write_part(
            tmp_path,
            _ONCHAIN_SOURCE,
            channel,
            FAMILY_EQUITY,
            [
                _legacy_equity_row(
                    channel,
                    _TS,
                    provider=_ONCHAIN_SOURCE,
                    symbol=_ONCHAIN_SYMBOL,
                    symbol_raw=_ONCHAIN_SYMBOL,
                    # The fork's on-chain adapters stamped the block clock in
                    # ``exchange_ts`` and left ``source_ts`` null, so this also
                    # exercises ``_header``'s fallback between the two.
                    source_ts=None,
                    exchange_ts=_TS,
                    **body,
                )
            ],
            bucketed=False,
        )
    return tmp_path


def _replay(lake: Path, channel: str, symbol: str = _SYMBOL) -> list[Any]:
    client = StockodileClient(lake)
    return list(client.replay([channel], [symbol], _TS - _DAY, _TS + 10 * _DAY))


def test_replay_returns_every_trade_in_the_partition_with_a_usable_amount(
    spanning_lake: Path,
) -> None:
    """The C3 reproduction: five trades, and ``sum`` over them is arithmetic, not a TypeError."""
    trades = _replay(spanning_lake, "trade")

    assert len(trades) == 5
    assert [t.amount for t in trades] == [10.0, 11.0, 12.0, 100.0, 100.0]
    assert sum(t.amount for t in trades) == pytest.approx(233.0)
    assert all(isinstance(t.side, Side) for t in trades)
    assert [t.side for t in trades] == [Side.UNKNOWN] * 3 + [Side.BUY] * 2
    assert all(t.side.value for t in trades)
    assert all(t.asset_class is AssetClass.EQUITY for t in trades)


def test_the_third_dialect_in_the_same_lake_is_read_as_the_market_it_came_from(
    spanning_lake: Path,
) -> None:
    """``_row_family`` discriminates three unions, and only two had file-backed coverage.

    A pre-migration crypto row names its origin ``exchange`` and its venue clock
    ``exchange_ts``, carries no ``asset_class`` and no provenance tail, and sits under
    the *other* on-disk layout — so it exercises the marker order, the timestamp
    fallback, the legacy-provenance default and the layout grouping at once.
    """
    trades = _replay(spanning_lake, "trade", _CRYPTO_SYMBOL)

    assert len(trades) == 2
    assert all(t.asset_class is AssetClass.CRYPTO for t in trades)
    assert {t.source for t in trades} == {_CRYPTO_SOURCE}
    assert [t.amount for t in trades] == [0.25, 0.25]
    assert [t.side for t in trades] == [Side.SELL, Side.SELL]
    assert [t.source_ts for t in trades] == [_TS, _TS + 1]
    assert {t.prov for t in trades} == {Provenance.NATIVE}


def test_every_bar_on_disk_is_reachable_and_each_tag_answers_for_its_own_partition(
    spanning_lake: Path,
) -> None:
    """The C6 reproduction, stated as reachability: three under ``bar/``, two under ``ohlcv/``.

    ``replay(["bar"])`` used to raise ``Unknown channel tag: 'bar'``, which made those
    three rows unreachable through the record API however they were asked for. It now
    answers, so all five bars are reachable — three under the name the directory
    carries and two under the other, and five in total with nothing counted twice.
    """
    retired = _replay(spanning_lake, "bar")
    surviving = _replay(spanning_lake, "ohlcv")

    assert [b.close for b in retired] == [179.0, 180.0, 181.0]
    assert [b.close for b in surviving] == [181.0, 182.0]
    assert len(retired) + len(surviving) == 5
    assert all(type(b) is OHLCV for b in (*retired, *surviving))


def test_a_bar_written_under_the_retired_tag_keeps_its_print_count(spanning_lake: Path) -> None:
    """The C8 reproduction: ``trade_count`` on disk must not read back as "not published"."""
    assert [b.num_trades for b in _replay(spanning_lake, "bar")] == [77, 78, 79]
    assert [b.num_trades for b in _replay(spanning_lake, "ohlcv")] == [900, 901]


def test_each_tag_gets_its_own_view_and_the_two_counts_add_up(spanning_lake: Path) -> None:
    """A retired tag is a channel of its own in SQL too, and the lake reports its real shape.

    On the *real* legacy layout the ``ohlcv`` view once vanished entirely: the two tags
    straddle the ``bucket=`` change, both ``_PART_TAILS`` entries start ``date=*``, and
    grouping on that first element alone put both layouts in one ``read_parquet`` call —
    which DuckDB refuses with ``Hive partition mismatch``, swallowed, leaving no view at
    all. That grouping fix is what this still guards; the union it was written for is
    gone.
    """
    client = StockodileClient(spanning_lake)

    assert client.query("SELECT count(*) AS n FROM bar").row(0, named=True)["n"] == 3
    assert client.query("SELECT count(*) AS n FROM ohlcv").row(0, named=True)["n"] == 2


def test_the_lake_lists_the_tags_that_are_on_disk_rather_than_one_that_is_not(
    spanning_lake: Path,
) -> None:
    """A lake holding both directories holds both channels, and says so.

    Reporting ``bar`` as ``ohlcv`` was how a caller was told to ask for a name whose
    read then had to reconcile two directories. Discovery answers what is there; the
    rename is ``migrate-lake``'s job.
    """
    with Catalog(spanning_lake) as catalog:
        channels = catalog.list_channels()
        bar_dates = catalog.list_dates("bar")
        ohlcv_dates = catalog.list_dates("ohlcv")

    assert {"bar", "ohlcv"} <= set(channels)
    assert bar_dates and ohlcv_dates
    assert not set(bar_dates) & set(ohlcv_dates), "the two tags hold different days here"


def test_both_on_disk_layouts_sit_under_one_channel_directory_and_are_both_read(
    spanning_lake: Path,
) -> None:
    """The shape ``_PART_TAILS`` exists for, and the one the first fixture did not build.

    ``source=alpaca/channel=trade/`` holds the equity fork's flat parts *and* the
    sink's bucketed ones, because ``migrate_lake`` renames the directory and the
    collector keeps writing into it. Two hive key sets, one directory: DuckDB refuses
    to read them in a single ``read_parquet`` call, so they have to be separate groups
    — which is what grouping on ``tail[0]`` alone silently undid.
    """
    equity = spanning_lake / f"source={_SOURCE}" / "channel=trade"
    crypto = spanning_lake / f"source={_CRYPTO_SOURCE}" / "channel=trade"

    assert list(equity.glob("date=*/part-*.parquet")), "the equity fork wrote no bucket= level"
    assert list(equity.glob("date=*/bucket=*/part-*.parquet")), "and the sink writes one"
    assert list(crypto.glob("date=*/bucket=*/part-*.parquet")), "crypto always wrote one"

    client = StockodileClient(spanning_lake)
    counts = client.query(
        "SELECT symbol, count(*) AS n FROM trade GROUP BY symbol ORDER BY symbol"
    )

    assert dict(counts.iter_rows()) == {_SYMBOL: 5, _CRYPTO_SYMBOL: 2}


def test_the_inventory_reports_each_directory_with_its_own_true_row_count(
    spanning_lake: Path,
) -> None:
    """Two directories, two rows, three bars and two bars — the lake's actual shape.

    Folding them into one entry meant the count had to reconcile two partitions, and
    every reconciliation tried so far either double-counted the overlap or deleted it.
    Reporting them separately needs no reconciliation and loses nothing: the caller can
    add.
    """
    with Catalog(spanning_lake) as catalog:
        asked = catalog.inventory(channel="ohlcv")
        every = catalog.inventory()

    assert len(asked) == 1
    row = asked.row(0, named=True)
    assert row["channel"] == "ohlcv"
    assert row["row_count"] == 2
    assert row["exchange"] == _SOURCE

    counted = {(r["channel"], r["symbol"]): r["row_count"] for r in every.iter_rows(named=True)}
    assert counted[("ohlcv", _SYMBOL)] == 2
    assert counted[("bar", _SYMBOL)] == 3
    assert counted[("trade", _CRYPTO_SYMBOL)] == 2


def test_resampling_the_span_uses_every_print_and_opens_at_the_earliest_one(
    spanning_lake: Path,
) -> None:
    """The C4 reproduction: ``sum(amount)`` alone filtered the pre-migration prints out.

    Because ``first(price ORDER BY local_ts)`` runs after the WHERE clause, dropping
    them did not merely lose volume — it moved the bar's open to the wrong price.
    """
    client = StockodileClient(spanning_lake)
    df = client.resample(_SYMBOL, _TS - _DAY, _TS + 10 * _DAY, "1d")

    assert len(df) == 1
    bar = df.row(0, named=True)
    assert bar["open"] == 150.0
    assert bar["close"] == 154.0
    assert bar["volume"] == pytest.approx(233.0)
    assert bar["trade_count"] == 5


# ---------------------------------------------------------------------------
# The three channels that had only hand-built dicts behind them
# ---------------------------------------------------------------------------


def test_a_legacy_depth_profile_says_it_was_modelled_rather_than_reading_back_native(
    spanning_lake: Path,
) -> None:
    """``basis`` and ``is_synthetic`` are the prototype the four-field tail generalised.

    Defaulting a pre-migration equity row to NATIVE reported a modelled
    volume-at-price ladder as something the venue published. The confidence is 0.0
    because the fork stored the method and the level but never a sampling number, and
    1.0 would rank a legacy profile above every measured one.
    """
    profiles = _replay(spanning_lake, "depth")

    assert [type(p) for p in profiles] == [DepthProfile, DepthProfile]
    synthetic, derived = profiles
    assert synthetic.prov is Provenance.SYNTHETIC
    assert synthetic.prov_basis == "yahoo_1m_vap"
    assert synthetic.is_synthetic is True
    assert derived.prov is Provenance.DERIVED
    assert derived.prov_basis == "alpaca_l1"
    assert derived.is_synthetic is False
    assert [p.prov_confidence for p in profiles] == [0.0, 0.0]
    # ``{price, size}`` is the equity spelling of a book level; ``{price, amount}`` the
    # canonical one. Reading only the second raised KeyError on every legacy level.
    assert synthetic.bids == [(149.0, 300.0)]
    assert synthetic.asks == [(151.0, 400.0)]


def test_a_legacy_option_quote_becomes_a_contract_with_a_nanosecond_expiry(
    spanning_lake: Path,
) -> None:
    """``option_quote`` → ``OptionsChain``, ``type`` → ``opt_type``, ``YYYY-MM-DD`` → ns.

    The second half of the struct collapse, and the other retired tag: the partition is
    named for the tag the fork wrote, so it is read by asking for ``option_quote``, and
    what comes back is the record that absorbed it. Reachable as itself, decoded as its
    successor — the same contract ``bar`` has.

    Converting a date to nanoseconds is total; converting back is not, which is the
    direction that must not lose anything. ``underlying_price`` is required with no
    default and the legacy row has no column for it, so it has to be passed explicitly
    as ``None`` — "the source published none" — rather than left to a missing argument.
    """
    contracts = _replay(spanning_lake, "option_quote")

    assert [type(c) for c in contracts] == [OptionsChain]
    (contract,) = contracts
    assert contract.opt_type is OptType.CALL
    assert contract.expiry == 1_702_598_400_000_000_000  # 2023-12-15T00:00:00Z
    assert contract.underlying_price is None
    assert (contract.bid_px, contract.ask_px, contract.last_price) == (1.5, 1.7, 1.6)
    assert contract.mark_iv == pytest.approx(0.31)
    assert contract.strike == 190.0


def test_a_legacy_instrument_keeps_its_listing_venue_and_does_not_become_its_source(
    spanning_lake: Path,
) -> None:
    """``exchange_name`` → ``exchange``, and ``exchange`` is not where the data came from.

    The canonical reader looked for neither name and dropped the column silently; the
    header then had three origin names to choose from by value and picked the listing
    venue. Alpaca served this row; NASDAQ lists the security.
    """
    instruments = _replay(spanning_lake, "instrument")

    assert [type(i) for i in instruments] == [Instrument]
    (instrument,) = instruments
    assert instrument.exchange == "NASDAQ"
    assert instrument.source == _SOURCE
    assert instrument.name == "Apple Inc."
    assert instrument.cusip == "037833100"


# ---------------------------------------------------------------------------
# The five tags whose equity structs were deleted
# ---------------------------------------------------------------------------


def test_the_deleted_on_chain_records_read_back_as_the_structs_that_survived_them(
    spanning_lake: Path,
) -> None:
    """Two unions declared these five tags; one union is gone and the files are not.

    The equity fork carried its own ``PoRUpdate``, ``LiquidationCall``,
    ``ReserveDataUpdated``, ``BalanceCorrection`` and ``LimitOrderFill`` as fork residue —
    on-chain records inside an equities library — and the merge kept the crypto copies.
    Deleting a struct that wrote rows is only safe if something still reads them, and the
    coverage those five had constructed the retired structs directly, so it left with them.

    ``asset_class`` reads back ``EQUITY`` for all five, and that is the honest answer
    rather than a good one: the row's only surviving evidence of a market is which fork's
    column dialect it is written in, and the fork that wrote these was the equity one.
    Nothing on disk says otherwise, and inventing ``CRYPTO`` from the channel name would
    be the reader deciding a fact the file does not record.
    """
    expected = {
        "limit_order_fill": LimitOrderFill,
        "por_update": PoRUpdate,
        "balance_correction": BalanceCorrection,
        "reserve_data_updated": ReserveDataUpdated,
        "liquidation_call": LiquidationCall,
    }
    for channel, struct in expected.items():
        (record,) = _replay(spanning_lake, channel, _ONCHAIN_SYMBOL)
        assert type(record) is struct, channel
        assert record.source == _ONCHAIN_SOURCE
        assert record.asset_class is AssetClass.EQUITY
        assert record.source_ts == _TS, "the fork stamped the block clock in exchange_ts"
        for field, value in _LEGACY_ONCHAIN_BODIES[channel].items():
            assert getattr(record, field) == value, f"{channel}.{field}"


def test_every_row_in_every_partition_of_this_lake_is_reachable_through_the_record_api(
    spanning_lake: Path,
) -> None:
    """The whole property, counted against the files rather than against a channel list.

    Each test above names one channel it knows about, so a partition nothing thought to
    name would be unreachable and every one of them would still pass — which is the shape
    of this project's defining failure, a capability that stops existing with no assertion
    positioned to see it. This one enumerates the ``channel=`` directories off disk and
    demands the record API hand back exactly as many records as there are rows in each.

    A retired tag is read by asking for it: ``bar`` answers for ``channel=bar/`` and
    ``ohlcv`` for ``channel=ohlcv/``, which is why summing per directory works and a
    single widened name would double-count.
    """
    symbols = [_SYMBOL, _CRYPTO_SYMBOL, _ONCHAIN_SYMBOL]

    on_disk: dict[str, int] = {}
    for part in spanning_lake.rglob("*.parquet"):
        (directory,) = (p for p in part.parents if p.name.startswith("channel="))
        channel = directory.name.removeprefix("channel=")
        on_disk[channel] = on_disk.get(channel, 0) + len(pl.read_parquet(part))

    assert len(on_disk) == 11, f"the fixture built {sorted(on_disk)}"

    client = StockodileClient(spanning_lake)
    replayed = {
        channel: len(list(client.replay([channel], symbols, _TS - 400 * _DAY, _TS + 400 * _DAY)))
        for channel in on_disk
    }

    assert replayed == on_disk


# ---------------------------------------------------------------------------
# When both tags hold a row for the same instant
# ---------------------------------------------------------------------------


@pytest.fixture
def backfilled_lake(tmp_path: Path) -> Path:
    """A lake where a date collected before the merge was backfilled after it.

    ``alpaca.connector`` maps ``ch in ("bar", "ohlcv")`` onto ``ohlcv``, so any
    post-merge backfill of an already-collected date writes ``channel=ohlcv/`` beside
    the ``channel=bar/`` the first collection left. Day 0 is in both halves.
    """

    async def build() -> None:
        sink = ParquetSink(data_dir=tmp_path, max_buffer_rows=10_000, flush_interval_seconds=9999)
        for day in (0, 3, 4):
            await sink.put(_canonical_bar(_TS + day * _DAY, 200.0 + day, 5_000.0, 900))
        await sink.flush()

    asyncio.run(build())

    _write_part(
        tmp_path,
        _SOURCE,
        "bar",
        FAMILY_EQUITY,
        [
            _legacy_equity_row(
                "bar",
                _TS + i * _DAY,
                interval="1d",
                open=178.0,
                high=180.0,
                low=177.0,
                close=179.0 + i,
                volume=1_000.0,
                vwap=178.5,
                trade_count=77,
            )
            for i in range(3)
        ],
        bucketed=False,
    )
    return tmp_path


def test_the_overlapping_day_is_reachable_from_both_tags_and_duplicated_under_neither(
    backfilled_lake: Path,
) -> None:
    """The round-2 finding: a widened ``ohlcv`` unioned the two tags and returned day 0 twice.

    Six distinct recordings sit on disk — three under ``bar/`` and three under
    ``ohlcv/`` — and day 0 has one on each side. Under the union that day came back
    twice from a single call, doubling its ``volume`` and ``num_trades`` and, through
    ``ohlcv_from_ohlcv``'s coverage sum, *raising* the confidence of anything derived
    from it. Each tag now answers for its own directory: every row is reachable, no call
    repeats an instant, and 3 + 3 is the lake.
    """
    retired = _replay(backfilled_lake, "bar")
    surviving = _replay(backfilled_lake, "ohlcv")

    assert len(retired) == 3
    assert len(surviving) == 3
    assert len({b.local_ts for b in retired}) == 3, "the retired tag repeated an instant"
    assert len({b.local_ts for b in surviving}) == 3, "the surviving tag repeated an instant"
    assert [b.close for b in retired] == [179.0, 180.0, 181.0]
    assert [b.close for b in surviving] == [200.0, 203.0, 204.0]
    # Day 0 is the overlap: one recording under each tag, each reachable by name.
    assert [b.close for b in retired if b.local_ts == _TS] == [179.0]
    assert [b.close for b in surviving if b.local_ts == _TS] == [200.0]


def test_sql_and_the_record_api_agree_about_which_rows_each_tag_holds(
    backfilled_lake: Path,
) -> None:
    """``replay`` reads through ``scan``; SQL reads through the view. Both, or neither."""
    client = StockodileClient(backfilled_lake)

    assert client.query("SELECT count(*) AS n FROM ohlcv").row(0, named=True)["n"] == 3
    assert client.query("SELECT count(*) AS n FROM bar").row(0, named=True)["n"] == 3


@pytest.fixture
def fetched_history_lake(tmp_path: Path) -> Path:
    """A Yahoo-shaped EOD history: one fetch clock on 250 daily bars, beside a ``bar/`` dir.

    ``yahoo.client`` takes ``local_ts = time.time_ns()`` once per fetch and stamps every
    bar of the returned history with it; ``stooq.connector`` does the same. The bar's own
    instant is ``source_ts``. Any lake with a ``channel=bar/`` directory beside this is
    the whole target population of the widening that used to reconcile the two.
    """

    async def build() -> None:
        sink = ParquetSink(data_dir=tmp_path, max_buffer_rows=100_000, flush_interval_seconds=9999)
        for i in range(250):
            await sink.put(
                OHLCV(
                    source="yahoo",
                    symbol=_SYMBOL,
                    symbol_raw=_SYMBOL,
                    local_ts=_TS,
                    asset_class=AssetClass.EQUITY,
                    source_ts=_TS - (250 - i) * _DAY,
                    interval="1d",
                    open=100.0 + i,
                    high=101.0 + i,
                    low=99.0 + i,
                    close=100.5 + i,
                    volume=1_000_000.0,
                    num_trades=10,
                )
            )
        await sink.flush()

    asyncio.run(build())

    _write_part(
        tmp_path,
        _SOURCE,
        "bar",
        FAMILY_EQUITY,
        [
            _legacy_equity_row(
                "bar",
                _TS + _DAY,
                interval="1d",
                open=178.0,
                high=180.0,
                low=177.0,
                close=179.0,
                volume=1.0,
                vwap=178.5,
                trade_count=77,
            )
        ],
        bucketed=False,
    )
    return tmp_path


def test_a_fetched_daily_history_keeps_all_of_its_bars_next_to_a_retired_tag(
    fetched_history_lake: Path,
) -> None:
    """C1: ``(symbol, local_ts, interval)`` is not an identity for an equity bar.

    The key carried no ``source`` — equity providers write bare tickers, so two
    providers' ``AAPL`` collide — and ``local_ts`` is not the bar's instant but the clock
    of the fetch that returned the whole history. Every one of these 250 bars shares one
    ``local_ts``, so the moment a ``channel=bar/`` directory made the read "span a retired
    tag", the ``QUALIFY row_number() = 1`` kept one of them: 250 bars became 1, 250 000 000
    shares of volume became 1 000 000, and the same one row came back through ``scan``,
    ``replay`` and ``inventory`` alike.
    """
    client = StockodileClient(fetched_history_lake)
    replayed = list(client.replay(["ohlcv"], [_SYMBOL], _TS - 400 * _DAY, _TS + 400 * _DAY))

    assert len(replayed) == 250
    assert client.query("SELECT count(*) AS n FROM ohlcv").row(0, named=True)["n"] == 250
    assert client.query("SELECT sum(volume) AS v FROM ohlcv").row(0, named=True)["v"] == (
        pytest.approx(250_000_000.0)
    )
    assert {b.source_ts for b in replayed} == {_TS - (250 - i) * _DAY for i in range(250)}

    with Catalog(fetched_history_lake) as catalog:
        inventory = catalog.inventory(channel="ohlcv")
    assert inventory.row(0, named=True)["row_count"] == 250
