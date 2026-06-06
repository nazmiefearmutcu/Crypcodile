"""OHLCV resampling from stored trade records (Task 5.1).

``resample_ohlcv(catalog, symbol, start_ns, end_ns, interval)`` queries the
``trade`` channel via the DuckDB Catalog and groups trades into OHLCV bars of
the requested interval using DuckDB's ``time_bucket`` function.

Design (Appendix §4 / §5):

    - Resampling builds on the existing DuckDB Catalog and ParquetSink storage
      layer — it never queries an exchange directly.
    - SQL pattern from Appendix §4::

          SELECT symbol,
                 time_bucket(INTERVAL '1 minute', make_timestamp(local_ts // 1000)) AS bar,
                 first(price ORDER BY local_ts) AS open,
                 max(price) AS high, min(price) AS low,
                 last(price ORDER BY local_ts) AS close,
                 sum(amount) AS volume,
                 sum(CASE WHEN side='buy'  THEN amount ELSE 0 END) AS buy_volume,
                 sum(CASE WHEN side='sell' THEN amount ELSE 0 END) AS sell_volume,
                 count(*) AS num_trades
          FROM trade
          WHERE symbol=? AND local_ts BETWEEN ? AND ?
          GROUP BY 1, 2
          ORDER BY 2

    - Timestamps: ``local_ts`` is stored as nanosecond integers.
      ``make_timestamp(local_ts // 1000)`` converts ns → µs → DuckDB TIMESTAMP
      (microsecond-precision, no TZ dependency) so ``time_bucket`` works without
      the optional ``pytz`` extension.

    - Interval notation: the public API uses short-hand strings (``"1s"``,
      ``"1m"``, ``"5m"``, ``"1h"``, ``"4h"``, ``"1d"``) which are translated
      to ``INTERVAL '... unit'`` SQL literals.  The translation is performed by
      ``_parse_interval`` which validates input via a strict regex (digits +
      one of s/m/h/d/w only) before producing the SQL literal — no user-
      controlled string ever reaches DuckDB unvalidated.

    - The ``bar`` column is the inclusive lower bound of each bucket expressed
      as a nanosecond epoch integer (``epoch_ns(time_bucket(...))``) so it
      aligns with the rest of the pipeline's ``local_ts`` convention.

    - Optional forward-fill: when ``fill_empty=True``, a complete grid of
      buckets from ``start_ns`` to ``end_ns`` is generated via
      ``generate_series`` and LEFT-JOINed with the aggregated bars so that
      empty buckets appear as rows with ``volume=0`` and ``OHLCV=NULL``.

Returns a Polars DataFrame (via DuckDB → ``result.pl()``).  The schema is::

    bar:         Int64      nanosecond epoch of the bucket start
    symbol:      Utf8       canonical symbol (same as the input arg)
    interval:    Utf8       interval string (e.g. "1m")
    open:        Float64    price of first trade in bucket (NULL if empty)
    high:        Float64
    low:         Float64
    close:       Float64    price of last trade in bucket
    volume:      Float64    total traded amount (0 for empty bars)
    buy_volume:  Float64    subset where side="buy"
    sell_volume: Float64    non-buy volume (side='sell' + side='unknown')
    num_trades:  Int64      count of source trades
"""

from __future__ import annotations

import duckdb
import polars as pl

from crocodile.resample._interval import parse_interval as _parse_interval
from crocodile.store.catalog import Catalog

# ---------------------------------------------------------------------------
# SQL templates (no user input interpolated — only validated interval tokens)
# ---------------------------------------------------------------------------


def _build_no_fill_sql(interval_sql: str, interval_label: str) -> str:
    """Return the aggregation SQL for non-empty bars only.

    ``interval_sql``  must be a pre-validated DuckDB INTERVAL literal.
    ``interval_label`` must be the original short-hand string (e.g. "1m");
    it is used as a string constant in the SELECT (not as a SQL structural
    token), and the value is controlled by our own regex-validation step.

    Both arguments come from ``_parse_interval``, never from raw user input.
    No column names, table names, or SQL keywords are sourced from user input.
    """

    #              produced by _parse_interval's regex-validation, not from
    #              untrusted user input; symbol/start_ns/end_ns are ? params)
    return (
        "SELECT\n"
        f"    epoch_ns(time_bucket({interval_sql}, make_timestamp(local_ts // 1000))) AS bar,\n"
        "    symbol,\n"
        f"    '{interval_label}' AS interval,\n"
        "    first(price ORDER BY local_ts)          AS open,\n"
        "    max(price)                              AS high,\n"
        "    min(price)                              AS low,\n"
        "    last(price ORDER BY local_ts)           AS close,\n"
        "    sum(amount)                             AS volume,\n"
        "    sum(CASE WHEN side = 'buy'  THEN amount ELSE 0.0 END) AS buy_volume,\n"
        "    sum(CASE WHEN side = 'buy'  THEN 0.0   ELSE amount END) AS sell_volume,\n"
        "    count(*)::BIGINT                        AS num_trades\n"
        "FROM trade\n"
        "WHERE symbol = ?\n"
        "  AND local_ts >= ?\n"
        "  AND local_ts <= ?\n"
        "GROUP BY 1, 2, 3\n"
        "ORDER BY 1"
    )


def _build_fill_sql(
    interval_sql: str, interval_label: str, start_ns: int, end_ns: int
) -> str:
    """Return the fill-enabled SQL.

    ``start_ns`` and ``end_ns`` are plain Python ints (not user-supplied
    strings), safe to embed as numeric literals in the grid CTE.
    ``interval_sql`` and ``interval_label`` come from ``_parse_interval``.
    ``symbol`` is passed as a ``?`` parameter at execute time.
    """

    #              never user-controlled strings)
    return (
        "WITH\n"
        "agg AS (\n"
        "    SELECT\n"
        f"        time_bucket({interval_sql}, make_timestamp(local_ts // 1000)) AS bar_ts,\n"
        "        symbol,\n"
        "        first(price ORDER BY local_ts)          AS open,\n"
        "        max(price)                              AS high,\n"
        "        min(price)                              AS low,\n"
        "        last(price ORDER BY local_ts)           AS close,\n"
        "        sum(amount)                             AS volume,\n"
        "        sum(CASE WHEN side = 'buy'  THEN amount ELSE 0.0 END) AS buy_volume,\n"
        "        sum(CASE WHEN side = 'buy'  THEN 0.0   ELSE amount END) AS sell_volume,\n"
        "        count(*)::BIGINT                        AS num_trades\n"
        "    FROM trade\n"
        "    WHERE symbol = ?\n"
        "      AND local_ts >= ?\n"
        "      AND local_ts <= ?\n"
        "    GROUP BY 1, 2\n"
        "),\n"
        "grid AS (\n"
        "    SELECT generate_series AS bar_ts\n"
        "    FROM generate_series(\n"
        f"        time_bucket({interval_sql}, make_timestamp({start_ns}::BIGINT // 1000)),\n"
        f"        time_bucket({interval_sql}, make_timestamp({end_ns}::BIGINT // 1000)),\n"
        f"        {interval_sql}\n"
        "    )\n"
        "),\n"
        "filled AS (\n"
        "    SELECT\n"
        "        epoch_ns(g.bar_ts)          AS bar,\n"
        f"        ? AS symbol,\n"
        f"        '{interval_label}'          AS interval,\n"
        "        a.open,\n"
        "        a.high,\n"
        "        a.low,\n"
        "        a.close,\n"
        "        coalesce(a.volume, 0.0)      AS volume,\n"
        "        coalesce(a.buy_volume, 0.0)  AS buy_volume,\n"
        "        coalesce(a.sell_volume, 0.0) AS sell_volume,\n"
        "        coalesce(a.num_trades, 0)    AS num_trades\n"
        "    FROM grid g\n"
        "    LEFT JOIN agg a USING (bar_ts)\n"
        ")\n"
        "SELECT * FROM filled ORDER BY bar"
    )


# ---------------------------------------------------------------------------
# Main resample function
# ---------------------------------------------------------------------------


def resample_ohlcv(
    catalog: Catalog,
    symbol: str,
    start_ns: int,
    end_ns: int,
    interval: str,
    *,
    fill_empty: bool = False,
) -> pl.DataFrame:
    """Resample trade records into OHLCV bars at the requested interval.

    Queries the ``trade`` channel in the DuckDB Catalog for the given symbol
    and time range, groups by ``time_bucket``, and returns OHLCV bars as a
    Polars DataFrame.

    Args:
        catalog:    A ``Catalog`` instance pointing at the data lake root.
        symbol:     Canonical symbol, e.g. ``"deribit:BTC-PERPETUAL"``.
        start_ns:   Inclusive lower bound on ``local_ts`` (nanoseconds UTC).
        end_ns:     Inclusive upper bound on ``local_ts`` (nanoseconds UTC).
        interval:   Bar width as a short-hand string: ``"1s"``, ``"5m"``,
                    ``"1h"``, ``"4h"``, ``"1d"``, etc.
        fill_empty: If ``True``, insert rows with ``volume=0`` and
                    ``open/high/low/close=NULL`` for every interval bucket
                    in ``[start_ns, end_ns]`` that contains no trades.
                    If ``False`` (default), only non-empty buckets appear.

    Returns:
        A Polars DataFrame with columns::

            bar         Int64   nanosecond epoch of the bucket start
            symbol      Utf8
            interval    Utf8
            open        Float64 (NULL for empty fill bars)
            high        Float64
            low         Float64
            close       Float64
            volume      Float64 (0.0 for empty fill bars)
            buy_volume  Float64 (side='buy' trades only)
            sell_volume Float64 (non-buy volume: side='sell' + side='unknown')
            num_trades  Int64   (0 for empty fill bars)

        Ordered by ``bar`` ascending.  Returns an empty DataFrame (0 rows,
        0 columns) if no trade data exists for the symbol in the given range.

    Raises:
        ValueError: If ``interval`` cannot be parsed.
    """
    # Validate interval — both output values come from our own regex validation,
    # never from raw user input.  They are safe to embed in SQL templates.
    interval_sql, _unit = _parse_interval(interval)
    # interval is the validated, normalized label stored in the output column.
    # Re-derive from the match so we store the normalised form (already stripped
    # and lowercased by _parse_interval).
    interval_label = interval.strip().lower()

    # Refresh views so newly written files are visible.
    catalog.refresh_views()
    conn = catalog.connection

    if fill_empty:
        sql = _build_fill_sql(interval_sql, interval_label, start_ns, end_ns)
        # Parameters: symbol (agg WHERE), start_ns, end_ns, symbol (filled SELECT)
        params: list[object] = [symbol, start_ns, end_ns, symbol]
    else:
        sql = _build_no_fill_sql(interval_sql, interval_label)
        params = [symbol, start_ns, end_ns]

    try:
        result = conn.execute(sql, params)
        df: pl.DataFrame = result.pl()
    except (duckdb.CatalogException, duckdb.IOException):
        # View may not exist yet (no trade data written) → return empty.
        return pl.DataFrame()

    return df
