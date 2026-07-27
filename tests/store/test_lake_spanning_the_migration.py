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
* ``replay(["ohlcv"])`` returned the post-migration bars only, because the glob
  matched ``channel=ohlcv`` literally and the older bars are under ``channel=bar``.
* the bars that did come back reported ``num_trades=None`` — the encoding reserved
  for "the source did not publish one" — because the column is spelled
  ``trade_count`` on the older files.

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
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from crocodile.core.schema.enums import AssetClass, OptType, Side
from crocodile.core.schema.provenance import Provenance
from crocodile.core.schema.records import OHLCV, DepthProfile, Instrument, OptionsChain, Trade
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


def test_replaying_ohlcv_returns_the_bars_written_under_the_retired_tag_too(
    spanning_lake: Path,
) -> None:
    """The C6 reproduction: three bars live under ``channel=bar/`` and two under ``ohlcv/``."""
    bars = _replay(spanning_lake, "ohlcv")

    assert len(bars) == 5
    assert [b.close for b in bars] == [179.0, 180.0, 181.0, 181.0, 182.0]
    assert all(isinstance(b, OHLCV) for b in bars)


def test_a_bar_written_under_the_retired_tag_keeps_its_print_count(spanning_lake: Path) -> None:
    """The C8 reproduction: ``trade_count`` on disk must not read back as "not published"."""
    bars = _replay(spanning_lake, "ohlcv")

    assert [b.num_trades for b in bars] == [77, 78, 79, 900, 901]


def test_asking_for_the_retired_tag_reads_the_old_partition_and_decodes_it(
    spanning_lake: Path,
) -> None:
    """``replay(["bar"])`` used to raise ``Unknown channel tag: 'bar'``.

    The widening is one-directional on purpose: naming a retired tag asks about the
    old files, so this returns three bars rather than all five.
    """
    bars = _replay(spanning_lake, "bar")

    assert len(bars) == 3
    assert [b.num_trades for b in bars] == [77, 78, 79]
    assert all(type(b) is OHLCV for b in bars)


def test_the_ohlcv_view_spans_both_tags_so_sql_sees_one_channel(spanning_lake: Path) -> None:
    """``SELECT * FROM ohlcv`` on a lake with legacy bars returned two rows of five.

    On the *real* legacy layout it did worse than that: the two tags straddle the
    ``bucket=`` change, both ``_PART_TAILS`` entries start ``date=*``, and grouping on
    that first element alone put both layouts in one ``read_parquet`` call — which
    DuckDB refuses with ``Hive partition mismatch``, swallowed, leaving no ``ohlcv``
    view at all.
    """
    client = StockodileClient(spanning_lake)
    df = client.query("SELECT count(*) AS n FROM ohlcv")

    assert df.row(0, named=True)["n"] == 5


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


def test_the_inventory_counts_a_five_row_channel_once(spanning_lake: Path) -> None:
    """``bar`` and ``ohlcv`` are one channel; counting both reported eight rows of five."""
    with Catalog(spanning_lake) as catalog:
        rows = catalog.inventory(channel="ohlcv")
        every = catalog.inventory()

    assert len(rows) == 1
    row = rows.row(0, named=True)
    assert row["channel"] == "ohlcv", "asking for ohlcv must not answer with the retired tag"
    assert row["row_count"] == 5
    assert row["exchange"] == _SOURCE

    counted = {(r["channel"], r["symbol"]): r["row_count"] for r in every.iter_rows(named=True)}
    assert counted[("ohlcv", _SYMBOL)] == 5
    assert ("bar", _SYMBOL) not in counted, "the retired tag is not a second channel"
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
    """``option_quote`` → ``options_chain``, ``type`` → ``opt_type``, ``YYYY-MM-DD`` → ns.

    Converting a date to nanoseconds is total; converting back is not, which is the
    direction that must not lose anything. ``underlying_price`` is required with no
    default and the legacy row has no column for it, so it has to be passed explicitly
    as ``None`` — "the source published none" — rather than left to a missing argument.
    """
    contracts = _replay(spanning_lake, "options_chain")

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
