"""DuckDB-backed catalog for querying hive-partitioned Parquet data (Task 2.3).

Design (Appendix §4):
    - ``Catalog(data_dir)`` builds per-channel DuckDB views over
      ``read_parquet(glob, hive_partitioning=true, union_by_name=true)``.
    - ``query(sql)`` executes arbitrary SQL against registered views,
      returns a Polars DataFrame.
    - ``scan(channel, symbol, start_ns, end_ns)`` narrows the glob path by
      exchange/channel/date **before** the WHERE clause for partition pruning
      (avoids full directory discovery on large lakes), then filters by
      ``symbol`` and ``local_ts`` range, returns a Polars DataFrame ordered
      by ``local_ts``.

Partition layout (from ParquetSink):
    data/exchange={E}/channel={C}/date=YYYY-MM-DD/bucket={0..127}/part-*.parquet

Views registered:
    One DuckDB VIEW per channel found on disk, named by the channel string
    (e.g. ``trade``, ``book_snapshot``).  Views are created lazily on first
    access and re-created whenever ``refresh_views()`` is called explicitly.

Empty-result contract:
    ``scan()`` returns ``pl.DataFrame()`` (zero columns, zero rows) whenever
    no rows match — whether because no files exist for the channel/date or
    because all files are filtered out by the WHERE clause.  Callers must
    check ``len(df) == 0`` before accessing named columns.
"""

from __future__ import annotations

import datetime
import glob as _glob
from pathlib import Path

import duckdb
import polars as pl


class Catalog:
    """Query interface over a hive-partitioned Parquet data lake.

    Args:
        data_dir: Root of the data lake (same ``data_dir`` passed to
            ``ParquetSink``).
    """

    def __init__(self, data_dir: Path | str) -> None:
        self._data_dir = Path(data_dir)
        # In-memory DuckDB connection — lightweight, no persistence needed here.
        self._conn = duckdb.connect()
        self._registered_channels: set[str] = set()
        # Register views for all channels present on disk.
        self._refresh_views()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def query(self, sql: str) -> pl.DataFrame:
        """Execute arbitrary SQL against registered channel views.

        Views available mirror the channel names (e.g. ``trade``,
        ``book_snapshot``, ``book_delta``, …).

        Args:
            sql: Any DuckDB-compatible SQL query.

        Returns:
            A Polars DataFrame with the query result.
        """
        # Refresh views so newly written files are picked up.
        self._refresh_views()
        result = self._conn.execute(sql)
        return result.pl()

    def scan(
        self,
        channel: str,
        symbol: str,
        start_ns: int,
        end_ns: int,
    ) -> pl.DataFrame:
        """Return rows for a single symbol within a nanosecond time range.

        Partition pruning is applied by narrowing the glob **before** the
        ``WHERE`` clause — only date partitions that overlap ``[start_ns,
        end_ns]`` are discovered, avoiding a full directory scan.

        Args:
            channel: Channel name, e.g. ``"trade"``, ``"book_snapshot"``.
            symbol: Canonical symbol string, e.g. ``"deribit:BTC-PERPETUAL"``.
            start_ns: Inclusive lower bound on ``local_ts`` (nanoseconds UTC).
            end_ns: Inclusive upper bound on ``local_ts`` (nanoseconds UTC).

        Returns:
            A Polars DataFrame ordered by ``local_ts``, potentially empty if
            no rows match.
        """
        # Build narrow glob paths by date — avoids discovering unneeded dirs.
        glob_paths = self._build_date_globs(channel, start_ns, end_ns)

        if not glob_paths:
            # No matching date partitions exist on disk → empty result.
            # Return schemaless DataFrame — consistent with the WHERE-filtered
            # empty path below (callers must check len == 0 before column access).
            return pl.DataFrame()

        # Deduplicate (different (exchange, date) combos may share same date).
        unique_globs = list(dict.fromkeys(glob_paths))

        # Build a multi-path read_parquet expression.
        # DuckDB accepts a list literal:  ['path1', 'path2', ...]
        # Single quotes in paths must be escaped as '' (SQL string literal rule).
        # DuckDB does not support ? parameters for read_parquet() path arguments.
        paths_literal = ", ".join(f"'{p.replace(chr(39), chr(39) * 2)}'" for p in unique_globs)

        # Use parameterized query to avoid SQL injection on the symbol value.
        # start_ns and end_ns are ints (no injection risk) but kept as parameters
        # for consistency and to let DuckDB optimise them as typed literals.
        sql = f"""
            SELECT *
            FROM read_parquet(
                [{paths_literal}],
                hive_partitioning => true,
                union_by_name => true
            )
            WHERE symbol = ?
              AND local_ts >= ?
              AND local_ts <= ?
            ORDER BY local_ts
        """
        result = self._conn.execute(sql, [symbol, start_ns, end_ns])  # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query  # noqa: E501
        df = result.pl()
        # Normalise: return a bare schemaless DataFrame when no rows match so
        # both empty-result paths have the same shape (consistent contract).
        if len(df) == 0:
            return pl.DataFrame()
        return df

    def refresh_views(self) -> None:
        """Re-scan the data directory and re-register channel views.

        Call after writing new data if you need ``query()`` to pick up files
        without constructing a new ``Catalog`` instance.
        """
        self._refresh_views()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _refresh_views(self) -> None:
        """Scan data_dir for channel directories and create/replace views."""
        channel_dir = self._data_dir
        if not channel_dir.exists():
            return

        # Discover channels from directory names ``channel=<name>``.
        for exchange_dir in channel_dir.iterdir():
            if not exchange_dir.is_dir() or not exchange_dir.name.startswith("exchange="):
                continue
            for chan_dir in exchange_dir.iterdir():
                if not chan_dir.is_dir() or not chan_dir.name.startswith("channel="):
                    continue
                channel = chan_dir.name[len("channel="):]
                if channel not in self._registered_channels:
                    self._create_view(channel)

    def _create_view(self, channel: str) -> None:
        """Register a DuckDB VIEW named after the channel.

        The glob covers all exchanges and all dates for that channel so that
        ``query("SELECT … FROM trade")`` works without extra parameters.
        """
        glob_pattern = str(
            self._data_dir
            / "exchange=*"
            / f"channel={channel}"
            / "date=*"
            / "bucket=*"
            / "part-*.parquet"
        )
        # Escape embedded single quotes in the path so the SQL string literal
        # is valid even when data_dir or channel contains a single quote.
        # DuckDB does not support ? parameters for structural/path arguments
        # like read_parquet() paths, so quote-escaping is the correct fix.
        escaped_glob = glob_pattern.replace("'", "''")
        escaped_channel = channel.replace("'", "''")
        sql = f"""
            CREATE OR REPLACE VIEW "{escaped_channel}" AS
            SELECT * FROM read_parquet(
                '{escaped_glob}',
                hive_partitioning => true,
                union_by_name => true
            )
        """
        self._conn.execute(sql)  # nosemgrep: python.lang.security.audit.formatted-sql-query.formatted-sql-query, python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query  # noqa: E501
        self._registered_channels.add(channel)

    def _build_date_globs(
        self, channel: str, start_ns: int, end_ns: int
    ) -> list[str]:
        """Return concrete glob patterns narrowed to dates in [start_ns, end_ns].

        We enumerate all ``exchange=*`` directories, then derive which UTC
        dates are spanned by the query window.  Only those date partitions are
        included in the returned glob list.

        If no relevant files exist on disk, returns an empty list.
        """
        channel_dirs = list(self._data_dir.glob(f"exchange=*/channel={channel}"))
        if not channel_dirs:
            return []

        # Compute the set of dates covered by [start_ns, end_ns].
        dates = _ns_range_to_dates(start_ns, end_ns)

        globs: list[str] = []
        for chan_dir in channel_dirs:
            for date_str in dates:
                date_dir = chan_dir / f"date={date_str}"
                if date_dir.exists():
                    pattern = str(date_dir / "bucket=*" / "part-*.parquet")
                    # Only include if there are actual files.
                    if _glob.glob(pattern):
                        globs.append(pattern)

        return globs


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------


def _ns_to_date(ns: int) -> str:
    """Convert nanosecond UTC epoch to a ``YYYY-MM-DD`` string."""
    dt = datetime.datetime.fromtimestamp(ns / 1_000_000_000, tz=datetime.UTC)
    return dt.strftime("%Y-%m-%d")


def _ns_range_to_dates(start_ns: int, end_ns: int) -> list[str]:
    """Return all UTC date strings that overlap the nanosecond range.

    Includes the start date, end date, and all dates in between.
    """
    start_dt = datetime.datetime.fromtimestamp(start_ns / 1_000_000_000, tz=datetime.UTC).date()
    end_dt = datetime.datetime.fromtimestamp(end_ns / 1_000_000_000, tz=datetime.UTC).date()

    dates: list[str] = []
    current = start_dt
    while current <= end_dt:
        dates.append(current.strftime("%Y-%m-%d"))
        current += datetime.timedelta(days=1)
    return dates
