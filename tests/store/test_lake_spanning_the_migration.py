"""One lake, both dialects, in the same partition directory.

Four of the review's eight findings share this scenario and none of them is visible
without it. ``migrate_lake`` renames partition directories and never rewrites a
Parquet byte, so a source that was collected before the union merge and again after
it ends up with two column sets under one ``channel=`` directory — and ``Catalog``
reads them with ``union_by_name => true``, which makes every column present on every
row and only the *values* tell the two apart.

What that produced before the family dispatch:

* ``replay(["trade"])`` returned ``Trade(amount=None, side=None)`` for the
  pre-migration half — no exception, and the caller's ``sum(amount)`` raised
  ``TypeError`` several frames away.
* ``replay(["ohlcv"])`` returned the post-migration bars only, because the glob
  matched ``channel=ohlcv`` literally and the older bars are under ``channel=bar``.
* the bars that did come back reported ``num_trades=None`` — the encoding reserved
  for "the source did not publish one" — because the column is spelled
  ``trade_count`` on the older files.

The fixture writes the canonical half through the real ``ParquetSink`` and the
legacy half through the schema ``ParquetSink`` itself declares for the equity
family, so neither side is a hand-shaped dict pretending to be a file.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from crocodile.core.schema.enums import AssetClass, Side
from crocodile.core.schema.records import OHLCV, Trade
from crocodile.core.store.parquet_sink import ParquetSink, _channel_schema
from crocodile.core.store.rows import FAMILY_EQUITY, _date_from_ns, _symbol_bucket
from crocodile.equity.client.client import StockodileClient

_DAY = 86_400_000_000_000
_TS = 1_700_000_000_000_000_000  # 2023-11-14T22:13:20Z
_SYMBOL = "AAPL"
_SOURCE = "alpaca"


def _legacy_row(channel: str, local_ts: int, **body: Any) -> dict[str, Any]:
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
        "exchange": None,
        **body,
    }


def _write_legacy_part(data_dir: Path, channel: str, rows: list[dict[str, Any]]) -> Path:
    """Write one pre-migration part file into the partition the canonical rows share."""
    schema = _channel_schema(channel, FAMILY_EQUITY)
    part_dir = (
        data_dir
        / f"source={_SOURCE}"
        / f"channel={channel}"
        / f"date={rows[0]['date']}"
        / f"bucket={rows[0]['bucket']}"
    )
    part_dir.mkdir(parents=True, exist_ok=True)
    path = part_dir / "part-premigration.parquet"
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

    _write_legacy_part(
        tmp_path,
        "trade",
        [
            _legacy_row("trade", _TS + i, id=f"L{i}", price=150.0 + i, size=10.0 + i)
            for i in range(3)
        ],
    )
    _write_legacy_part(
        tmp_path,
        "bar",
        [
            _legacy_row(
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
    )
    return tmp_path


def _replay(lake: Path, channel: str) -> list[Any]:
    client = StockodileClient(lake)
    return list(client.replay([channel], [_SYMBOL], _TS - _DAY, _TS + 10 * _DAY))


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
    """``SELECT * FROM ohlcv`` on a lake with legacy bars returned two rows of five."""
    client = StockodileClient(spanning_lake)
    df = client.query("SELECT count(*) AS n FROM ohlcv")

    assert df.row(0, named=True)["n"] == 5


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
