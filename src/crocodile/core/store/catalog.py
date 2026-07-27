"""DuckDB-backed catalog for querying hive-partitioned Parquet data (Task 2.3).

Design (Appendix §4):
    - ``Catalog(data_dir)`` builds per-channel DuckDB views over
      ``read_parquet(glob, hive_partitioning=true, union_by_name=true)``.
    - ``query(sql)`` executes arbitrary SQL against registered views,
      returns a Polars DataFrame. ``query(sql, readonly=True)`` vets it with
      ``assert_readonly_sql`` first, for network-facing callers.
    - ``scan(channel, symbol, start_ns, end_ns)`` narrows the glob path by
      exchange/channel/date **before** the WHERE clause for partition pruning
      (avoids full directory discovery on large lakes), then filters by
      ``symbol`` and ``local_ts`` range, returns a Polars DataFrame ordered
      by ``local_ts``.

Partition layout (from ParquetSink):
    data/source={S}/channel={C}/date=YYYY-MM-DD/bucket={0..127}/part-*.parquet

Legacy layouts are still read: crypto lakes written before the merge use
``exchange={S}`` and equity lakes ``provider={S}``. Every discovery walk and
every glob covers all three prefixes, and a lake still on a legacy prefix logs
one warning per prefix pointing at ``crocodile migrate-lake``.

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
import logging
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Final

import duckdb
import polars as pl

from crocodile.core.store.migrate import SOURCE_PREFIX, SOURCE_PREFIXES

log = logging.getLogger(__name__)

# Stable schemas for inventory / search (empty-result contract).
_INVENTORY_SCHEMA: dict[str, type[pl.DataType]] = {
    "exchange": pl.Utf8,
    "channel": pl.Utf8,
    "symbol": pl.Utf8,
    "min_ts": pl.Int64,
    "max_ts": pl.Int64,
    "row_count": pl.Int64,
}

_SEARCH_SCHEMA: dict[str, type[pl.DataType]] = {
    "symbol": pl.Utf8,
    "exchange": pl.Utf8,
    "channels": pl.Utf8,
    "score": pl.Int64,
    "min_ts": pl.Int64,
    "max_ts": pl.Int64,
    "row_count": pl.Int64,
}


# Statements and table functions that are never acceptable on a network-facing
# query surface: everything that mutates the database, loads an extension, or
# reads a file the caller names rather than one the lake owns.
_UNSAFE_SQL_RE = re.compile(
    r"\b(COPY|ATTACH|INSTALL|LOAD|PRAGMA|CALL|EXPORT|IMPORT|CREATE\s+OR\s+REPLACE\s+TABLE|"
    r"DROP|ALTER|INSERT|UPDATE|DELETE|TRUNCATE|GRANT|REVOKE)\b"
    r"|read_csv|read_csv_auto|read_json|read_json_auto|read_parquet\s*\(\s*['\"]/"
    r"|read_blob|read_text",
    re.IGNORECASE,
)


def assert_readonly_sql(sql: str) -> None:
    """Reject multi-statement and mutating / external-access SQL.

    Used by MCP and other network-facing query entry points. Local CLI may
    still call :meth:`Catalog.query` without this guard for power users.

    Enforcement is deliberately keyword-level rather than a real parse, which
    makes it conservative in both directions and is why it is opt-in:

    - It over-rejects. The pattern has no lexer, so a banned word inside a
      string literal or a double-quoted identifier still trips it —
      ``SELECT 'delete me'`` and ``SELECT "delete" FROM t`` are refused. Word
      boundaries do spare ordinary columns like ``deleted_at``.
    - It under-inspects. ``read_parquet`` is only blocked when its first
      argument is an absolute path, because the catalog's own views are built
      from ``read_parquet`` over the lake.

    Wiring this into :meth:`Catalog.query` unconditionally is a follow-up
    decision, not a default: the crypto fork reaches ``query()`` with SQL this
    guard rejects. ``crocodile.crypto.legacy.cli.get_available_option_underlyings``
    builds ``SELECT DISTINCT underlying FROM read_parquet('<absolute glob>', …)``
    to read one date partition directly, which trips the absolute-path branch.
    Callers that want the guard pass ``readonly=True``, matching how the equity
    fork shipped it.

    Args:
        sql: The statement to vet before it reaches DuckDB.

    Raises:
        ValueError: The SQL is empty, holds more than one statement, does not
            start with a read-only verb, or names a disallowed keyword or
            external reader.
    """
    stripped = sql.strip().rstrip(";").strip()
    if not stripped:
        raise ValueError("Empty SQL")
    # A single trailing ';' was stripped above, so anything left is a second
    # statement smuggled in behind an innocent-looking SELECT.
    if ";" in stripped:
        raise ValueError("Multi-statement SQL is not allowed")
    upper = stripped.lstrip().upper()
    if not (
        upper.startswith("SELECT")
        or upper.startswith("WITH")
        or upper.startswith("DESCRIBE")
        or upper.startswith("SHOW")
        or upper.startswith("EXPLAIN")
    ):
        raise ValueError("Only SELECT/WITH/DESCRIBE/SHOW/EXPLAIN queries are allowed")
    if _UNSAFE_SQL_RE.search(stripped):
        raise ValueError("SQL contains disallowed keywords or external readers")


# Path / glob characters that must never appear in hive partition suffixes
# used for discovery walks or interpolated into Path.glob / DuckDB patterns.
_UNSAFE_HIVE_SUFFIX_CHARS = frozenset({"/", "\\", "\x00", "*", "?", "[", "]"})


# The two on-disk layouts, deepest first. Crypto always wrote a ``bucket=``
# level under the date; equity wrote parts directly under it and its Catalog
# read both. That second reader was lost when the crypto Catalog was promoted,
# so an equity lake resolved to no views at all and every query raised. Each
# layout is globbed separately because they differ in hive keys, and DuckDB
# refuses to read two key sets in one call.
_PART_TAILS: Final = (
    ("date=*", "bucket=*", "part-*.parquet"),
    ("date=*", "part-*.parquet"),
)


def _split_source_prefix(name: str) -> tuple[str, str] | None:
    """Split a top-level partition directory name into (prefix, source value).

    Returns ``None`` for any name that is not a source partition at all, so
    unrelated directories in the lake root are skipped rather than guessed at.
    """
    for prefix in SOURCE_PREFIXES:
        if name.startswith(prefix):
            return prefix, name[len(prefix) :]
    return None


def _is_safe_hive_suffix(value: str) -> bool:
    """Return True if *value* is a safe hive ``source=`` / ``channel=`` suffix.

    Rejects empty / relative names (``.``, ``..``), leading or trailing
    whitespace (callers such as :meth:`Catalog.list_dates` strip user input,
    so padded on-disk names would be orphaned), path separators, null bytes,
    glob metacharacters, and other ASCII control characters (e.g. newlines).
    """
    if not value or value in (".", ".."):
        return False
    if value != value.strip():
        return False
    if any(c in _UNSAFE_HIVE_SUFFIX_CHARS for c in value):
        return False
    if any(ord(c) < 32 for c in value):
        return False
    return True


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
        # Legacy partition prefixes already warned about, so a repeated walk
        # (every query() refreshes views) does not repeat the warning.
        self._warned_prefixes: set[str] = set()
        self._closed = False
        # Register views for all channels present on disk.
        self._refresh_views()

    def close(self) -> None:
        """Close the DuckDB connection. Idempotent.

        The equity fork's Catalog owned its connection's lifetime and this one
        did not, so a long-lived process opened one in-memory database per
        Catalog and never gave any of them back. Restored as the plain form:
        equity also carried a lock and thread-local cursors, which belong with
        the concurrency model Phase 2 decides, not with the file handle.
        """
        if self._closed:
            return
        self._closed = True
        try:
            self._conn.close()
        except Exception:  # pragma: no cover - close() on a dead handle
            pass

    def __enter__(self) -> Catalog:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _iter_source_dirs(self) -> Iterator[tuple[Path, str]]:
        """Yield ``(directory, source value)`` for each top-level partition.

        Covers the current ``source=`` prefix and both legacy ones, and warns
        once per legacy prefix seen. Directories that resolve outside
        ``data_dir`` are skipped (symlink-escape defence), as are unreadable
        ones — discovery degrades to "nothing there", never to a traceback.
        """
        if not self._data_dir.is_dir():
            return
        try:
            data_root = self._data_dir.resolve()
            children = list(self._data_dir.iterdir())
        except OSError:
            return

        for child in children:
            if not child.is_dir():
                continue
            split = _split_source_prefix(child.name)
            if split is None:
                continue
            prefix, value = split
            try:
                child.resolve().relative_to(data_root)
            except (ValueError, OSError):
                continue
            if prefix != SOURCE_PREFIX and prefix not in self._warned_prefixes:
                self._warned_prefixes.add(prefix)
                log.warning(
                    "lake at %s still uses the legacy %r partition prefix; "
                    "run `crocodile migrate-lake %s` (a rename, not a rewrite)",
                    self._data_dir,
                    prefix,
                    self._data_dir,
                )
            yield child, value

    def _source_globs(self, *tail: str) -> dict[str, str]:
        """Return ``{prefix: glob}`` covering every source prefix.

        A lake can hold a mix of prefixes — mid-migration, or because the two
        forks wrote crypto under ``exchange=`` and equities under ``provider=``
        into the same root — so every read covers all three rather than picking
        whichever one it happens to see first.
        """
        return {
            prefix: str(self._data_dir.joinpath(prefix + "*", *tail)) for prefix in SOURCE_PREFIXES
        }

    @staticmethod
    def _read_expr(groups: list[tuple[str, list[str]]]) -> str:
        """Return SQL reading every group as one relation.

        DuckDB refuses to read files with differing hive keys in a single
        ``read_parquet`` call — "Hive partition mismatch … key 'exchange' not
        found" — so anything that changes the key set has to be its own group
        and the groups are unioned. Two things change it: the source prefix
        (``source=`` / ``exchange=`` / ``provider=``) and whether the lake has a
        ``bucket=`` level at all. Legacy groups project their key as ``source``
        so the union is over one vocabulary.

        The legacy key also survives under its own name, and that is now
        load-bearing rather than redundant. A pre-migration crypto lake carried
        ``exchange`` as a real column; the canonical file schema does not, so on
        such a lake the hive key is the only thing left saying the row predates
        the merged header — which is exactly what
        :func:`crocodile.core.store.rows.from_row` reads to decide its asset
        class. Excluding it from the projection would make those rows
        unreconstructable.

        Prefixes come from :data:`SOURCE_PREFIXES`, a module constant, so the
        interpolated key is never caller-controlled; only the paths are, and
        those are quote-escaped.
        """
        selects: list[str] = []
        for prefix, patterns in groups:
            paths_literal = ", ".join(f"'{p.replace(chr(39), chr(39) * 2)}'" for p in patterns)
            key = prefix.removesuffix("=")
            projection = "*" if prefix == SOURCE_PREFIX else f"*, {key} AS source"
            selects.append(
                f"SELECT {projection} FROM read_parquet(\n"
                f"    [{paths_literal}],\n"
                "    hive_partitioning => true,\n"
                "    union_by_name => true\n"
                ")"
            )
        return "\nUNION ALL BY NAME\n".join(selects)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def query(self, sql: str, *, readonly: bool = False) -> pl.DataFrame:
        """Execute arbitrary SQL against registered channel views.

        Views available mirror the channel names (e.g. ``trade``,
        ``book_snapshot``, ``book_delta``, …).

        Args:
            sql: Any DuckDB-compatible SQL query.
            readonly: When True, vet *sql* with :func:`assert_readonly_sql`
                first. Off by default so local callers — the CLI, the GUI,
                analytics — keep the unrestricted access both forks gave them;
                network-facing surfaces such as the MCP server opt in.

        Returns:
            A Polars DataFrame with the query result.

        Raises:
            ValueError: *readonly* is True and *sql* is not read-only.
        """
        if readonly:
            assert_readonly_sql(sql)
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

        # Build a multi-path read_parquet expression, one group per prefix.
        # DuckDB accepts a list literal:  ['path1', 'path2', ...]
        # Single quotes in paths must be escaped as '' (SQL string literal rule).
        # DuckDB does not support ? parameters for read_parquet() path arguments.
        read_expr = self._read_expr(glob_paths)

        if isinstance(symbol, str):
            symbol_filter = "symbol = ?"
            params = [symbol, start_ns, end_ns]
        else:
            symbols_list = list(symbol)
            if not symbols_list:
                return pl.DataFrame()
            placeholders = ", ".join("?" for _ in symbols_list)
            symbol_filter = f"symbol IN ({placeholders})"
            params = [*symbols_list, start_ns, end_ns]

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
            FROM ({read_expr})
            WHERE {symbol_filter}
              AND local_ts >= ?
              AND local_ts <= ?
            ORDER BY local_ts
            {limit_clause}
        """
        result = self._conn.execute(
            sql, params
        )  # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query  # noqa: E501
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

        Walks the hive layout ``source=*/channel=*`` on the filesystem — plus
        the legacy ``exchange=*`` / ``provider=*`` roots — with no DuckDB scan.
        Useful discovery even when channel directories
        exist but views cannot be registered yet (empty partitions / no
        parquet parts).

        Empty lake or missing data directory yields ``[]``. Channel names
        are the raw partition suffixes (e.g. ``trade``, ``book_snapshot``),
        deduplicated across exchanges and sorted ascending. Non-directory
        entries, names that are not ``channel=...``, and suffixes that are
        unsafe as path segments (separators, null/control bytes, ``.`` /
        ``..``, glob metacharacters, leading/trailing whitespace) are ignored.
        """
        data_root = self._data_dir.resolve() if self._data_dir.is_dir() else None
        if data_root is None:
            return []

        channels: set[str] = set()
        for source_dir, _value in self._iter_source_dirs():
            try:
                children = list(source_dir.iterdir())
            except OSError:
                continue
            for chan_dir in children:
                if not chan_dir.is_dir() or not chan_dir.name.startswith("channel="):
                    continue
                # Resolve-check channel dirs too (symlink escape defence).
                try:
                    chan_dir.resolve().relative_to(data_root)
                except (ValueError, OSError):
                    continue
                channel_str = chan_dir.name[len("channel=") :]
                if _is_safe_hive_suffix(channel_str):
                    channels.add(channel_str)

        return sorted(channels)

    def list_dates(self, channel: str) -> list[str]:
        """Return sorted distinct ``date=`` partition values for *channel*.

        Walks the hive layout ``source=*/channel={channel}/date=*`` on the
        filesystem — plus the legacy ``exchange=*`` / ``provider=*`` roots —
        with no DuckDB scan. Useful discovery before bounded ``scan()`` /
        analytics calls.

        Empty / whitespace *channel*, unknown channel, empty lake, or a
        channel value that is unsafe as a path segment (separators, null
        bytes, ``.`` / ``..``, glob metacharacters) yields ``[]``.

        Dates are the raw partition suffixes (typically ``YYYY-MM-DD``),
        deduplicated across exchanges and sorted ascending.
        """
        channel = (channel or "").strip()
        # Reject path traversal and glob injection — never interpolate
        # untrusted channel into Path.glob patterns.
        if not _is_safe_hive_suffix(channel):
            return []

        data_root = self._data_dir.resolve() if self._data_dir.is_dir() else None
        if data_root is None:
            return []

        dates: set[str] = set()
        for source_dir, _value in self._iter_source_dirs():
            chan_dir = source_dir / f"channel={channel}"
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

        Distinct from :func:`crocodile.crypto.exchanges.factory.list_exchanges`,
        which returns **registered connector** names (code registry), not
        partitions present in the lake.

        Empty lake or missing data directory yields ``[]``. Exchange names
        are the raw partition suffixes (e.g. ``deribit``, ``binance``),
        deduplicated and sorted ascending. Non-directory entries, names
        that are not ``exchange=...``, and suffixes that are unsafe as path
        segments (separators, null/control bytes, ``.`` / ``..``, glob
        metacharacters, leading/trailing whitespace) are ignored.
        """
        exchanges: set[str] = set()
        for _source_dir, value in self._iter_source_dirs():
            if _is_safe_hive_suffix(value):
                exchanges.add(value)

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
        #
        # ``source`` is read before ``exchange`` and the order is load-bearing.
        # The view always projects ``source`` — legacy prefixes are aliased onto
        # it — while ``exchange`` is a record field that only some channels have,
        # and on the canonical ``instrument`` channel it is the *listing* venue.
        # Reading it first reported NASDAQ as the source of an Alpaca-served row,
        # the same confusion :mod:`crocodile.core.store.rows` guards the
        # partition path against. The names come from this literal tuple, never
        # from a caller, so interpolating one into the SQL is safe.
        origin_col = next((c for c in ("source", "exchange") if c in col_names), None)
        has_channel = "channel" in col_names

        if origin_col is not None:
            exchange_expr = origin_col
        elif exchange is not None:
            exchange_expr = f"'{exchange.replace(chr(39), chr(39) * 2)}'"
        else:
            exchange_expr = "''"

        channel_expr = "channel" if has_channel else f"'{channel.replace(chr(39), chr(39) * 2)}'"

        where_parts: list[str] = []
        params: list[object] = []
        if exchange is not None and origin_col is not None:
            where_parts.append(f"{origin_col} = ?")
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
        # Discover channels from directory names ``channel=<name>``.
        for source_dir, _value in self._iter_source_dirs():
            for chan_dir in source_dir.iterdir():
                if not chan_dir.is_dir() or not chan_dir.name.startswith("channel="):
                    continue
                channel = chan_dir.name[len("channel=") :]
                # Skip empty / relative / glob-unsafe suffixes (invalid views).
                if not _is_safe_hive_suffix(channel):
                    continue
                if channel not in self._registered_channels:
                    self._create_view(channel)

    def _create_view(self, channel: str) -> None:
        """Register a DuckDB VIEW named after the channel.

        The globs cover every source prefix and all dates for that channel so
        that ``query("SELECT … FROM trade")`` works without extra parameters,
        including on a lake that is half-migrated.

        Empty partition directories (no ``part-*.parquet`` yet) are skipped
        without raising: DuckDB ``read_parquet`` fails hard when the glob
        matches nothing, which would otherwise break ``Catalog`` construction
        / ``refresh_views`` when filesystem ``list_channels`` still discovers
        those channels. The channel is not added to ``_registered_channels``.
        """
        # Avoid DuckDB "No files found" when only empty hive dirs exist: keep
        # only the prefixes that actually match files.
        groups: list[tuple[str, list[str]]] = []
        for tail in _PART_TAILS:
            for prefix, pattern in self._source_globs(f"channel={channel}", *tail).items():
                if _glob.glob(pattern):
                    groups.append((prefix, [pattern]))
        if not groups:
            return

        # Escape embedded single quotes in the path so the SQL string literal
        # is valid even when data_dir or channel contains a single quote.
        # DuckDB does not support ? parameters for structural/path arguments
        # like read_parquet() paths, so quote-escaping is the correct fix.
        # View name uses double-quoted identifiers: escape " as "".
        escaped_channel = channel.replace('"', '""')
        sql = f"""
            CREATE OR REPLACE VIEW "{escaped_channel}" AS
            {self._read_expr(groups)}
        """
        try:
            self._conn.execute(
                sql
            )  # nosemgrep: python.lang.security.audit.formatted-sql-query.formatted-sql-query, python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query  # noqa: E501
        except Exception:
            # Race: files vanished between glob check and execute, or corrupt.
            return
        self._registered_channels.add(channel)

    def _build_date_globs(
        self, channel: str, start_ns: int, end_ns: int
    ) -> list[tuple[str, list[str]]]:
        """Return glob groups narrowed to dates in [start_ns, end_ns].

        We enumerate all source directories — ``source=*`` and both legacy
        prefixes — then derive which UTC dates are spanned by the query window.
        Only those date partitions are included.

        Patterns are grouped by (source prefix, on-disk layout) because a single
        ``read_parquet`` call cannot span two hive key sets; see
        :meth:`_read_expr`.

        If no relevant files exist on disk, returns an empty list.
        """
        channel_dirs: list[tuple[str, Path]] = []
        for source_dir, _value in self._iter_source_dirs():
            chan_dir = source_dir / f"channel={channel}"
            if not chan_dir.is_dir():
                continue
            split = _split_source_prefix(source_dir.name)
            assert split is not None  # _iter_source_dirs only yields matches
            channel_dirs.append((split[0], chan_dir))
        if not channel_dirs:
            return []

        # Compute the set of dates covered by [start_ns, end_ns].
        dates = _ns_range_to_dates(start_ns, end_ns)

        grouped: dict[tuple[str, tuple[str, ...]], list[str]] = {}
        for prefix, chan_dir in channel_dirs:
            for date_str in dates:
                date_dir = chan_dir / f"date={date_str}"
                if not date_dir.is_dir():
                    continue
                for tail in _PART_TAILS:
                    pattern = str(date_dir.joinpath(*tail[1:]))
                    # Only include if there are actual files.
                    if not _glob.glob(pattern):
                        continue
                    patterns = grouped.setdefault((prefix, tail), [])
                    if pattern not in patterns:
                        patterns.append(pattern)

        return [(prefix, patterns) for (prefix, _tail), patterns in grouped.items() if patterns]


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
