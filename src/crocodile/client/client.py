"""CrocodileClient — high-level API wrapping the DuckDB Catalog (Task 3.1 + 3.2).

``CrocodileClient(data_dir)`` is the primary entry-point for users who want to
query and scan the Parquet data lake without interacting with the lower-level
``Catalog`` directly.

Methods
-------
query(sql)
    Execute arbitrary DuckDB SQL against the registered channel views.
    Returns a Polars DataFrame.

scan(channel, symbols, start_ns, end_ns)
    Return rows for one or more canonical symbols within a nanosecond time
    range, ordered by ``local_ts`` ascending.  When multiple symbols are
    provided the per-symbol DataFrames are concatenated (union_by_name) and
    sorted globally.  Returns an empty DataFrame (0 rows, 0 columns) when no
    matching rows exist.

replay(channels, symbols, frm, to)
    Iterate over canonical Records across one or more channels and symbols
    within a nanosecond time range, globally sorted by ``local_ts``.  Uses
    the M2 k-way merge engine to combine per-(channel, symbol) streams.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import polars as pl

from crocodile.replay.merge import replay as _kway_merge
from crocodile.schema.records import Record
from crocodile.store.catalog import Catalog
from crocodile.store.rows import from_row


def _df_to_record_iter(df: pl.DataFrame) -> Iterator[Record]:
    """Yield Records from a Polars DataFrame, one row at a time.

    The DataFrame must contain a ``channel`` column (added by ``to_row()`` and
    preserved in the Parquet hive layout) so that ``from_row`` can reconstruct
    the correct Record type.

    The DataFrame is assumed to be pre-sorted by ``local_ts`` (the Catalog's
    ``scan`` method already returns rows ``ORDER BY local_ts``), so the
    resulting iterator is already sorted — a prerequisite for ``heapq.merge``.
    """
    for row_dict in df.to_dicts():
        yield from_row(row_dict)


class CrocodileClient:
    """High-level data client wrapping the hive-partitioned Parquet catalog.

    Args:
        data_dir: Root directory of the data lake — the same path passed to
            ``ParquetSink``.

    Example::

        client = CrocodileClient(data_dir="/data/crocodile")
        df = client.query("SELECT count(*) FROM trade")
        df2 = client.scan("trade", ["deribit:BTC-PERPETUAL"], start_ns, end_ns)
    """

    def __init__(self, data_dir: Path | str) -> None:
        self._catalog = Catalog(data_dir)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def query(self, sql: str) -> pl.DataFrame:
        """Execute arbitrary DuckDB SQL against registered channel views.

        Channel views are named after the channel string (e.g. ``trade``,
        ``book_snapshot``).  Views are refreshed before each query so
        newly written Parquet files are visible without reconstructing the
        client.

        Args:
            sql: Any DuckDB-compatible SQL string.

        Returns:
            A Polars DataFrame containing the query result.
        """
        return self._catalog.query(sql)

    def scan(
        self,
        channel: str,
        symbols: list[str],
        start_ns: int,
        end_ns: int,
    ) -> pl.DataFrame:
        """Return rows for one or more symbols within a nanosecond time range.

        Partition pruning is applied per symbol by narrowing the glob path to
        relevant date partitions before executing the WHERE clause.  When
        multiple symbols are requested, each symbol is scanned independently
        and the results are concatenated then re-sorted by ``local_ts``
        (globally ordered).

        Args:
            channel:   Channel name, e.g. ``"trade"``, ``"book_snapshot"``.
            symbols:   List of canonical symbol strings, e.g.
                       ``["deribit:BTC-PERPETUAL", "binance-spot:BTC-USDT"]``.
                       An empty list returns an empty DataFrame immediately.
            start_ns:  Inclusive lower bound on ``local_ts`` (nanoseconds UTC).
            end_ns:    Inclusive upper bound on ``local_ts`` (nanoseconds UTC).

        Returns:
            A Polars DataFrame ordered by ``local_ts`` ascending.  Returns an
            empty DataFrame (0 columns, 0 rows) when no rows match.
        """
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
        """Iterate over canonical Records sorted by ``local_ts`` (k-way merge).

        Reads matching Parquet partitions for each ``(channel, symbol)`` pair,
        reconstructs Record objects from the flat Parquet rows, and merges all
        per-(channel, symbol) streams using the M2 k-way merge engine
        (``heapq.merge`` with sort key ``(local_ts, exchange_ts_or_neg_inf,
        seq_or_0)``).

        Each per-(channel, symbol) stream is already sorted on disk (written
        in ``local_ts`` order by the ParquetSink); the merge combines them
        globally without materialising the full result set.

        Args:
            channels: Channel names to include, e.g. ``["trade", "book_delta"]``.
            symbols:  Canonical symbols, e.g.
                      ``["deribit:BTC-PERPETUAL", "binance-spot:BTC-USDT"]``.
                      An empty list yields nothing immediately.
            frm:      Inclusive start of the time range (nanoseconds UTC).
            to:       Inclusive end of the time range (nanoseconds UTC).

        Yields:
            Record objects in non-decreasing ``local_ts`` order.

        Note:
            The returned iterator is lazy — Parquet scans happen as the caller
            consumes records, one stream at a time.  If you need all records
            in memory, wrap with ``list(client.replay(...))``.
        """
        if not symbols or not channels:
            return iter([])

        # Build one sorted iterator per (channel, symbol) pair.
        streams: list[Iterator[Record]] = []
        for channel in channels:
            for symbol in symbols:
                df = self._catalog.scan(channel, symbol, frm, to)
                if len(df) > 0:
                    streams.append(_df_to_record_iter(df))

        if not streams:
            return iter([])

        return _kway_merge(streams)
