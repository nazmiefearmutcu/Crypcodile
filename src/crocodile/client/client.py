"""CrocodileClient — high-level API wrapping the DuckDB Catalog (Task 3.1).

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
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from crocodile.store.catalog import Catalog


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
