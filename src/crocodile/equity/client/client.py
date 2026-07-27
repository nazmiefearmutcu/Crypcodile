"""StockodileClient - high-level API wrapping the DuckDB Catalog.

StockodileClient(data_dir) is the primary entry-point for users who want to
query and scan the Parquet data lake without interacting with the lower-level
Catalog directly.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import polars as pl

from crocodile.core.replay.merge import replay as _kway_merge
from crocodile.core.schema.records import Record
from crocodile.core.store.catalog import Catalog
from crocodile.core.store.rows import from_row
from crocodile.equity.client.export import ExportFmt
from crocodile.equity.client.export import export as _export


def _df_to_record_iter(df: pl.DataFrame) -> Iterator[Record]:
    """Yield Records from a Polars DataFrame, one row at a time.

    The DataFrame must contain a ``channel`` column so that ``from_row``
    can reconstruct the correct Record type.

    The reader is ``core``'s. The equity fork shipped a second one, and it is deleted
    rather than wired up here: the equity providers write canonical records now, so an
    equity lake's new files carry ``source`` and ``asset_class`` and no ``provider``
    column at all, and the fork's reader would have raised ``KeyError('provider')`` on
    every one of them.

    **One reader, three dialects.** ``core``'s reader speaks the canonical column set,
    the pre-migration crypto one and the pre-migration equity one, choosing between them
    by the value of the row's origin marker; see
    :func:`crocodile.core.store.rows.from_row`. A legacy ``ohlcv`` row's ``trade_count``
    comes back as ``num_trades`` and a legacy ``instrument``'s ``exchange_name`` as
    ``exchange``, rather than being dropped, and the tags ``bar`` and ``option_quote``
    resolve to the records that absorbed them. ``tests/store/test_premerge_equity_rows.py``
    and ``tests/store/test_lake_spanning_the_migration.py`` walk it channel by channel,
    the second against real Parquet files in both on-disk layouts.

    This paragraph used to say the capability did not exist and was "the merge task that
    follows this one". It landed; the sentence did not, which is this project's defining
    failure in documentation form — a reader of the primary documented entry point being
    told a working capability is missing.
    """
    for row_dict in df.to_dicts():
        yield from_row(row_dict)


class StockodileClient:
    """High-level data client wrapping the hive-partitioned Parquet catalog.

    Args:
        data_dir: Root directory of the data lake.
    """

    def __init__(self, data_dir: Path | str) -> None:
        self._catalog = Catalog(data_dir)

    def query(self, sql: str, *, readonly: bool = False) -> pl.DataFrame:
        """Execute DuckDB SQL against registered channel views.

        Args:
            sql: SQL string.
            readonly: When True, reject multi-statement / mutating SQL (MCP).
        """
        return self._catalog.query(sql, readonly=readonly)

    def scan(
        self,
        channel: str,
        symbols: list[str],
        start_ns: int,
        end_ns: int,
    ) -> pl.DataFrame:
        """Return rows for one or more symbols within a nanosecond time range."""
        if not symbols:
            return pl.DataFrame()

        frames: list[pl.DataFrame] = []
        for symbol in symbols:
            df = self._catalog.scan(channel, symbol, start_ns, end_ns)
            if len(df) > 0:
                frames.append(df)

        if not frames:
            return pl.DataFrame()

        if len(frames) == 1:
            return frames[0]

        # Concatenate across symbols and re-sort globally by local_ts.
        combined = pl.concat(frames, how="diagonal")
        return combined.sort("local_ts")

    def replay(
        self,
        channels: list[str],
        symbols: list[str],
        frm: int,
        to: int,
    ) -> Iterator[Record]:
        """Iterate over canonical Records sorted by ``local_ts`` (k-way merge)."""
        if not symbols or not channels:
            return iter([])

        streams: list[Iterator[Record]] = []
        for channel in channels:
            for symbol in symbols:
                df = self._catalog.scan(channel, symbol, frm, to)
                if len(df) > 0:
                    streams.append(_df_to_record_iter(df))

        if not streams:
            return iter([])

        return _kway_merge(streams)

    def export(
        self,
        channel: str,
        symbols: list[str],
        frm: int,
        to: int,
        fmt: ExportFmt,
        dest: Path | str,
        limit: int | None = None,
    ) -> None:
        """Write rows for a channel x symbols x time range to a file."""
        _export(self._catalog, channel, symbols, frm, to, fmt, Path(dest), limit=limit)

    def resample(
        self,
        symbol: str,
        start_ns: int,
        end_ns: int,
        interval: str,
        *,
        fill_empty: bool = False,
    ) -> pl.DataFrame:
        """Resample trade data in the DuckDB Catalog into OHLCV bars.

        Uses the *equity* resampler: the extra columns are ``vwap``/``trade_count``.
        The identically named ``crocodile.core.resample.ohlcv.resample_ohlcv`` splits
        the same ``amount`` column by ``side`` instead, which on an equity lake credits
        every print to ``sell_volume`` rather than returning nothing — see
        ``crocodile.equity.resample.ohlcv``'s module docstring.
        """
        from crocodile.equity.resample.ohlcv import resample_ohlcv

        return resample_ohlcv(
            self._catalog,
            symbol,
            start_ns,
            end_ns,
            interval,
            fill_empty=fill_empty,
        )
