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

# Stable schemas for inventory / search (empty-result contract).
_INVENTORY_SCHEMA: dict[str, pl.DataType] = {
    "exchange": pl.Utf8,
    "channel": pl.Utf8,
    "symbol": pl.Utf8,
    "min_ts": pl.Int64,
    "max_ts": pl.Int64,
    "row_count": pl.Int64,
}

_SEARCH_SCHEMA: dict[str, pl.DataType] = {
    "symbol": pl.Utf8,
    "exchange": pl.Utf8,
    "channels": pl.Utf8,
    "score": pl.Int64,
    "min_ts": pl.Int64,
    "max_ts": pl.Int64,
    "row_count": pl.Int64,
}


def _symbol_raw(symbol: str) -> str:
    """Return the raw portion of a canonical symbol (after the last ``:``)."""
    return symbol.rsplit(":", 1)[-1]


def _score_symbol(q: str, symbol: str) -> int:
    """Rank how well *symbol* matches query *q*.

    Scores (highest first):
      100 exact full symbol match
       90 exact raw (after last ':') match
       80 case-insensitive equality (full or raw)
       60 prefix match on raw or full
       40 substring match
        0 no match
    """
    if symbol == q:
        return 100
    raw = _symbol_raw(symbol)
    if raw == q:
        return 90
    q_lower = q.lower()
    symbol_lower = symbol.lower()
    raw_lower = raw.lower()
    if symbol_lower == q_lower or raw_lower == q_lower:
        return 80
    if symbol_lower.startswith(q_lower) or raw_lower.startswith(q_lower):
        return 60
    if q_lower in symbol_lower or q_lower in raw_lower:
        return 40
    return 0


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
        symbol: str | list[str],
        start_ns: int,
        end_ns: int,
        limit: int | None = None,
    ) -> pl.DataFrame:
        """Return rows for a single or multiple symbols within a nanosecond time range.

        Partition pruning is applied by narrowing the glob **before** the
        ``WHERE`` clause — only date partitions that overlap ``[start_ns,
        end_ns]`` are discovered, avoiding a full directory scan.

        Args:
            channel: Channel name, e.g. ``"trade"``, ``"book_snapshot"``.
            symbol: Canonical symbol string or list of symbol strings.
            start_ns: Inclusive lower bound on ``local_ts`` (nanoseconds UTC).
            end_ns: Inclusive upper bound on ``local_ts`` (nanoseconds UTC).
            limit: Optional maximum number of rows to retrieve.

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

        if isinstance(symbol, str):
            symbol_filter = "symbol = ?"
            params = [symbol, start_ns, end_ns]
        else:
            symbols_list = list(symbol)
            if not symbols_list:
                return pl.DataFrame()
            placeholders = ", ".join("?" for _ in symbols_list)
            symbol_filter = f"symbol IN ({placeholders})"
            params = symbols_list + [start_ns, end_ns]

        # Cast/validate before interpolating into SQL — never trust a raw limit.
        if limit is not None:
            limit = int(limit)
            if limit < 0:
                raise ValueError("limit must be >= 0")
            limit_clause = f" LIMIT {limit}"  # safe after int cast
        else:
            limit_clause = ""

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
            WHERE {symbol_filter}
              AND local_ts >= ?
              AND local_ts <= ?
            ORDER BY local_ts
            {limit_clause}
        """
        result = self._conn.execute(sql, params)  # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query  # noqa: E501
        df = result.pl()
        # Normalise: return a bare schemaless DataFrame when no rows match so
        # both empty-result paths have the same shape (consistent contract).
        if len(df) == 0:
            return pl.DataFrame()
        return df

    @property
    def connection(self) -> duckdb.DuckDBPyConnection:
        """The underlying DuckDB connection (read-only accessor).

        Provides direct access to the in-memory DuckDB connection for callers
        that need to execute custom SQL or register temporary relations against
        the same connection that holds the channel views.

        Returns:
            The :class:`duckdb.DuckDBPyConnection` instance backing this catalog.
        """
        return self._conn

    def refresh_views(self) -> None:
        """Re-scan the data directory and re-register channel views.

        Call after writing new data if you need ``query()`` to pick up files
        without constructing a new ``Catalog`` instance.
        """
        self._refresh_views()

    def list_channels(self) -> list[str]:
        """Return sorted channel names present in the lake.

        Walks the hive layout ``exchange=*/channel=*`` on the filesystem
        (no DuckDB scan).  Useful discovery even when channel directories
        exist but views cannot be registered yet (empty partitions / no
        parquet parts).

        Empty lake or missing data directory yields ``[]``. Channel names
        are the raw partition suffixes (e.g. ``trade``, ``book_snapshot``),
        deduplicated across exchanges and sorted ascending. Non-directory
        entries and names that are not ``channel=...`` are ignored.
        """
        if not self._data_dir.exists() or not self._data_dir.is_dir():
            return []

        try:
            data_root = self._data_dir.resolve()
        except OSError:
            return []

        channels: set[str] = set()
        try:
            exchange_dirs = list(self._data_dir.iterdir())
        except OSError:
            return []

        for exchange_dir in exchange_dirs:
            if not exchange_dir.is_dir() or not exchange_dir.name.startswith(
                "exchange="
            ):
                continue
            # Ensure resolved path stays under data_dir (defence in depth).
            try:
                exchange_dir.resolve().relative_to(data_root)
            except (ValueError, OSError):
                continue
            try:
                children = list(exchange_dir.iterdir())
            except OSError:
                continue
            for chan_dir in children:
                if not chan_dir.is_dir() or not chan_dir.name.startswith(
                    "channel="
                ):
                    continue
                channel_str = chan_dir.name[len("channel=") :]
                if channel_str and channel_str not in (".", ".."):
                    channels.add(channel_str)

        return sorted(channels)

    def list_dates(self, channel: str) -> list[str]:
        """Return sorted distinct ``date=`` partition values for *channel*.

        Walks the hive layout ``exchange=*/channel={channel}/date=*`` on the
        filesystem (no DuckDB scan).  Useful discovery before bounded
        ``scan()`` / analytics calls.

        Empty / whitespace *channel*, unknown channel, empty lake, or a
        channel value that is unsafe as a path segment (separators, null
        bytes, ``.`` / ``..``, glob metacharacters) yields ``[]``.

        Dates are the raw partition suffixes (typically ``YYYY-MM-DD``),
        deduplicated across exchanges and sorted ascending.
        """
        channel = (channel or "").strip()
        if not channel:
            return []
        # Reject path traversal and glob injection — never interpolate
        # untrusted channel into Path.glob patterns.
        if any(c in channel for c in ("/", "\\", "\x00", "*", "?", "[", "]")):
            return []
        if channel in (".", ".."):
            return []

        if not self._data_dir.exists() or not self._data_dir.is_dir():
            return []

        try:
            data_root = self._data_dir.resolve()
        except OSError:
            return []

        dates: set[str] = set()
        try:
            exchange_dirs = list(self._data_dir.iterdir())
        except OSError:
            return []

        for exchange_dir in exchange_dirs:
            if not exchange_dir.is_dir() or not exchange_dir.name.startswith("exchange="):
                continue
            chan_dir = exchange_dir / f"channel={channel}"
            if not chan_dir.is_dir():
                continue
            # Ensure resolved path stays under data_dir (defence in depth).
            try:
                chan_dir.resolve().relative_to(data_root)
            except (ValueError, OSError):
                continue
            try:
                children = list(chan_dir.iterdir())
            except OSError:
                continue
            for date_dir in children:
                if not date_dir.is_dir() or not date_dir.name.startswith("date="):
                    continue
                date_str = date_dir.name[len("date=") :]
                if date_str and date_str not in (".", ".."):
                    dates.add(date_str)

        return sorted(dates)

    def list_exchanges_on_disk(self) -> list[str]:
        """Return sorted distinct ``exchange=`` partition values on disk.

        Walks the hive layout ``exchange=*/`` at the data lake root on the
        filesystem (no DuckDB scan).  Useful discovery before channel/date
        scoping or ``inventory(exchange=...)`` filters.

        Distinct from :func:`crypcodile.exchanges.factory.list_exchanges`,
        which returns **registered connector** names (code registry), not
        partitions present in the lake.

        Empty lake or missing data directory yields ``[]``. Exchange names
        are the raw partition suffixes (e.g. ``deribit``, ``binance``),
        deduplicated and sorted ascending. Non-directory entries and names
        that are not ``exchange=...`` are ignored.
        """
        if not self._data_dir.exists() or not self._data_dir.is_dir():
            return []

        try:
            data_root = self._data_dir.resolve()
        except OSError:
            return []

        exchanges: set[str] = set()
        try:
            children = list(self._data_dir.iterdir())
        except OSError:
            return []

        for exchange_dir in children:
            if not exchange_dir.is_dir() or not exchange_dir.name.startswith("exchange="):
                continue
            # Ensure resolved path stays under data_dir (defence in depth).
            try:
                exchange_dir.resolve().relative_to(data_root)
            except (ValueError, OSError):
                continue
            exchange_str = exchange_dir.name[len("exchange=") :]
            if exchange_str and exchange_str not in (".", ".."):
                exchanges.add(exchange_str)

        return sorted(exchanges)

    def inventory(
        self,
        channel: str | None = None,
        exchange: str | None = None,
    ) -> pl.DataFrame:
        """Summarise symbols present in the lake.

        Columns (stable schema even when empty)::

            exchange: str
            channel: str
            symbol: str
            min_ts: int
            max_ts: int
            row_count: int

        Optionally filter by *channel* and/or *exchange*. Empty or
        whitespace-only filter strings are treated as no filter (same contract
        as client ``resolve_symbols``), so ``channel=""`` does not falsely
        empty the inventory.
        """
        self._refresh_views()
        empty = pl.DataFrame(schema=_INVENTORY_SCHEMA)

        # Treat empty / whitespace filters as "no filter". A non-None channel
        # that is not registered returns empty, so "" would otherwise yield [].
        if isinstance(channel, str):
            channel = channel.strip() or None
        if isinstance(exchange, str):
            exchange = exchange.strip() or None

        channels = sorted(self._registered_channels)
        if channel is not None:
            if channel not in self._registered_channels:
                return empty
            channels = [channel]
        if not channels:
            return empty

        frames: list[pl.DataFrame] = []
        for ch in channels:
            frame = self._inventory_for_channel(ch, exchange=exchange)
            if frame is not None and len(frame) > 0:
                frames.append(frame)

        if not frames:
            return empty

        out = pl.concat(frames, how="diagonal_relaxed")
        # Enforce stable column order and dtypes.
        return out.select(
            pl.col("exchange").cast(pl.Utf8),
            pl.col("channel").cast(pl.Utf8),
            pl.col("symbol").cast(pl.Utf8),
            pl.col("min_ts").cast(pl.Int64),
            pl.col("max_ts").cast(pl.Int64),
            pl.col("row_count").cast(pl.Int64),
        ).sort(["exchange", "channel", "symbol"])

    def search_symbols(
        self,
        q: str,
        *,
        channel: str | None = None,
        exchange: str | None = None,
        limit: int = 20,
    ) -> pl.DataFrame:
        """Ranked symbol search over the catalog inventory.

        Columns::

            symbol, exchange, channels, score, min_ts, max_ts, row_count

        Ranking (see :func:`_score_symbol`).  Empty or whitespace-only *q*
        returns an empty DataFrame with the documented schema.  Multi-channel
        rows for the same ``(symbol, exchange)`` are aggregated: channels
        joined with commas, ``row_count`` summed, timestamps min/max'd,
        score max'd.  ``limit < 1`` yields the empty schema (Polars
        ``DataFrame.head(-n)`` would otherwise drop the last *n* rows).
        """
        empty = pl.DataFrame(schema=_SEARCH_SCHEMA)
        q = q.strip()
        if not q:
            return empty
        # Guard before .head(limit): Polars treats negative n as "all but last n".
        if limit < 1:
            return empty

        inv = self.inventory(channel=channel, exchange=exchange)
        if len(inv) == 0:
            return empty

        rows: list[dict[str, object]] = []
        for rec in inv.iter_rows(named=True):
            score = _score_symbol(q, rec["symbol"])
            if score <= 0:
                continue
            rows.append(
                {
                    "symbol": rec["symbol"],
                    "exchange": rec["exchange"],
                    "channel": rec["channel"],
                    "score": score,
                    "min_ts": rec["min_ts"],
                    "max_ts": rec["max_ts"],
                    "row_count": rec["row_count"],
                }
            )

        if not rows:
            return empty

        scored = pl.DataFrame(rows)
        agg = (
            scored.group_by(["symbol", "exchange"])
            .agg(
                pl.col("channel").unique().sort().str.join(",").alias("channels"),
                pl.col("score").max().alias("score"),
                pl.col("min_ts").min().alias("min_ts"),
                pl.col("max_ts").max().alias("max_ts"),
                pl.col("row_count").sum().alias("row_count"),
            )
            .with_columns(
                pl.col("score").cast(pl.Int64),
                pl.col("min_ts").cast(pl.Int64),
                pl.col("max_ts").cast(pl.Int64),
                pl.col("row_count").cast(pl.Int64),
            )
            .sort(["score", "symbol"], descending=[True, False])
            .head(limit)
            .select(
                "symbol",
                "exchange",
                "channels",
                "score",
                "min_ts",
                "max_ts",
                "row_count",
            )
        )
        return agg

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _inventory_for_channel(
        self,
        channel: str,
        *,
        exchange: str | None = None,
    ) -> pl.DataFrame | None:
        """Run the inventory aggregate SQL for a single registered channel.

        Returns ``None`` if the view is unusable (missing required columns)
        or the query fails; returns an empty DataFrame if the channel has
        no rows after filtering.
        """
        escaped = channel.replace('"', '""')
        try:
            cols_df = self._conn.execute(f'DESCRIBE "{escaped}"').pl()
            col_names = set(cols_df["column_name"].to_list())
        except Exception:
            return None

        required = {"symbol", "local_ts"}
        if not required.issubset(col_names):
            return None

        # Prefer hive-partition columns when present; otherwise synthesise
        # from the registered channel name / optional exchange filter.
        has_exchange = "exchange" in col_names
        has_channel = "channel" in col_names

        if has_exchange:
            exchange_expr = "exchange"
        elif exchange is not None:
            exchange_expr = f"'{exchange.replace(chr(39), chr(39) * 2)}'"
        else:
            exchange_expr = "''"

        channel_expr = (
            "channel" if has_channel else f"'{channel.replace(chr(39), chr(39) * 2)}'"
        )

        where_parts: list[str] = []
        params: list[object] = []
        if exchange is not None and has_exchange:
            where_parts.append("exchange = ?")
            params.append(exchange)

        where_sql = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""

        sql = f"""
            SELECT
                {exchange_expr} AS exchange,
                {channel_expr} AS channel,
                symbol,
                CAST(min(local_ts) AS BIGINT) AS min_ts,
                CAST(max(local_ts) AS BIGINT) AS max_ts,
                CAST(count(*) AS BIGINT) AS row_count
            FROM "{escaped}"
            {where_sql}
            GROUP BY 1, 2, 3
        """
        try:
            if params:
                result = self._conn.execute(sql, params)
            else:
                result = self._conn.execute(sql)
            return result.pl()
        except Exception:
            return None


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
                # Skip empty / relative partition suffixes (invalid view names).
                if not channel or channel in (".", ".."):
                    continue
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
        # View name uses double-quoted identifiers: escape " as "".
        escaped_glob = glob_pattern.replace("'", "''")
        escaped_channel = channel.replace('"', '""')
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
    # Integer division: float `ns / 1e9` loses precision near 2e18 (ULP ≈ 256 ns)
    # and rounds sub-second values up across a day boundary, yielding the wrong date.
    dt = datetime.datetime.fromtimestamp(ns // 1_000_000_000, tz=datetime.UTC)
    return dt.strftime("%Y-%m-%d")


def _ns_range_to_dates(start_ns: int, end_ns: int) -> list[str]:
    """Return all UTC date strings that overlap the nanosecond range.

    Includes the start date, end date, and all dates in between.
    """
    # Integer division (see _ns_to_date): float division can round a timestamp in
    # the last sub-second of a day up to the next day, over-including a partition.
    start_dt = datetime.datetime.fromtimestamp(start_ns // 1_000_000_000, tz=datetime.UTC).date()
    end_dt = datetime.datetime.fromtimestamp(end_ns // 1_000_000_000, tz=datetime.UTC).date()

    dates: list[str] = []
    current = start_dt
    while current <= end_dt:
        dates.append(current.strftime("%Y-%m-%d"))
        current += datetime.timedelta(days=1)
    return dates
