"""VWAP and derived trade metrics resampling (Task 5.3).

``resample_metrics(catalog, symbol, start_ns, end_ns, interval)`` queries the
``trade`` channel via the DuckDB Catalog and computes VWAP, dollar volume, and
trade count per interval bucket using DuckDB's ``time_bucket`` function.

Design (Appendix §4 / §5):

    - Resampling builds on the existing DuckDB Catalog and ParquetSink storage
      layer — it never queries an exchange directly.
    - Follows the same pattern as ``resample_ohlcv`` (Task 5.1).

Metrics computed per interval bucket:

    vwap          = sum(price * amount) / sum(amount)
                    Volume-Weighted Average Price for the interval.  NULL when
                    ``sum(amount) == 0`` (i.e. no trades in the bucket — this
                    only arises if a consumer builds a full grid; without
                    ``fill_empty`` there are no zero-volume bars).

    dollar_volume = sum(price * amount)
                    The total notional value traded in the interval (in quote
                    currency units).

    trade_count   = count(*)
                    Number of individual trade records in the interval.

Timestamps
----------
``local_ts`` is stored as nanosecond integers.
``make_timestamp(local_ts // 1000)`` converts ns → µs → DuckDB TIMESTAMP
(microsecond-precision) so ``time_bucket`` works without a timezone extension.
The ``bar`` column is expressed as a nanosecond epoch integer using
``epoch_ns(time_bucket(...))`` to align with the rest of the pipeline.

Interval notation
-----------------
Public API uses short-hand strings (``"1s"``, ``"1m"``, ``"1h"``, etc.).  The
``_parse_interval`` helper (shared conceptually with ``ohlcv``) validates input
via a strict regex before producing a safe DuckDB ``INTERVAL '...'`` literal.
No user-controlled string is ever interpolated directly into SQL.

Output schema
-------------
::

    bar:          Int64      nanosecond epoch of the bucket start
    symbol:       Utf8       canonical symbol (same as the input arg)
    interval:     Utf8       interval string (e.g. "1m")
    vwap:         Float64    Volume-Weighted Average Price (NULL if no trades)
    dollar_volume: Float64   sum(price * amount) for the bucket
    trade_count:  Int64      count of source trades in the bucket

Ordered by ``bar`` ascending.  Returns an empty DataFrame (0 rows) if no trade
data exists for the symbol in the given range.
"""

from __future__ import annotations

import duckdb
import polars as pl

from crocodile.resample._interval import parse_interval as _parse_interval
from crocodile.store.catalog import Catalog

# ---------------------------------------------------------------------------
# SQL template
# ---------------------------------------------------------------------------


def _build_metrics_sql(interval_sql: str, interval_label: str) -> str:
    """Return the aggregation SQL for VWAP + derived metrics.

    ``interval_sql`` must be a pre-validated DuckDB INTERVAL literal.
    ``interval_label`` is the original short-hand string (e.g. "1m");
    used as a string constant in the SELECT, never as a SQL structural token.

    Both arguments come from ``_parse_interval``, never from raw user input.
    ``symbol``, ``start_ns``, ``end_ns`` are ``?`` parameters.
    """
    # epoch_ns() converts the DuckDB TIMESTAMP bucket back to a nanosecond
    # integer so the ``bar`` column aligns with the pipeline's local_ts units.
    return (
        "SELECT\n"
        f"    epoch_ns(time_bucket({interval_sql}, make_timestamp(local_ts // 1000))) AS bar,\n"
        "    symbol,\n"
        f"    '{interval_label}' AS interval,\n"
        "    sum(price * amount) / sum(amount)        AS vwap,\n"
        "    sum(price * amount)                      AS dollar_volume,\n"
        "    count(*)::BIGINT                         AS trade_count\n"
        "FROM trade\n"
        "WHERE symbol = ?\n"
        "  AND local_ts >= ?\n"
        "  AND local_ts <= ?\n"
        "GROUP BY 1, 2, 3\n"
        "ORDER BY 1"
    )


# ---------------------------------------------------------------------------
# Public function
# ---------------------------------------------------------------------------


def resample_metrics(
    catalog: Catalog,
    symbol: str,
    start_ns: int,
    end_ns: int,
    interval: str,
) -> pl.DataFrame:
    """Compute VWAP, dollar volume, and trade count per interval bucket.

    Queries the ``trade`` channel in the DuckDB Catalog for the given symbol
    and time range, groups by ``time_bucket``, and returns derived metrics as
    a Polars DataFrame.

    Args:
        catalog:   A ``Catalog`` instance pointing at the data lake root.
        symbol:    Canonical symbol, e.g. ``"deribit:BTC-PERPETUAL"``.
        start_ns:  Inclusive lower bound on ``local_ts`` (nanoseconds UTC).
        end_ns:    Inclusive upper bound on ``local_ts`` (nanoseconds UTC).
        interval:  Bucket width as a short-hand string: ``"1s"``, ``"5m"``,
                   ``"1h"``, ``"4h"``, ``"1d"``, etc.

    Returns:
        A Polars DataFrame with columns::

            bar           Int64    nanosecond epoch of the bucket start
            symbol        Utf8
            interval      Utf8
            vwap          Float64  Volume-Weighted Average Price
                                   (NULL for empty buckets, won't appear
                                    unless fill_empty variant is used)
            dollar_volume Float64  sum(price * amount)
            trade_count   Int64    count of source trades

        Ordered by ``bar`` ascending.  Returns an empty DataFrame (0 rows)
        if no trade data exists for the symbol in the given range.

    Raises:
        ValueError: If ``interval`` cannot be parsed.
    """
    # Validate and translate the interval — both output values come from our
    # own regex validation, never from raw user input; safe to embed in SQL.
    interval_sql, _unit = _parse_interval(interval)
    interval_label = interval.strip().lower()

    # Refresh catalog views so newly written files are visible.
    catalog.refresh_views()
    conn = catalog._conn

    sql = _build_metrics_sql(interval_sql, interval_label)
    params: list[object] = [symbol, start_ns, end_ns]

    try:
        result = conn.execute(sql, params)
        df: pl.DataFrame = result.pl()
    except (duckdb.CatalogException, duckdb.IOException):
        # View may not exist yet (no trade data written) → return empty.
        return pl.DataFrame()

    return df
