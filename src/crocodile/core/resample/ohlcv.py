"""OHLCV resampling from stored trade records, for both asset classes.

``resample_ohlcv(catalog, symbol, start_ns, end_ns, interval)`` queries the ``trade``
channel via the DuckDB Catalog and groups trades into OHLCV bars of the requested
interval using DuckDB's ``time_bucket``.

One function, where there were two
----------------------------------
``crocodile.equity.resample.ohlcv`` used to define a function with this exact name and
this exact signature over the same ``trade`` view, returning a different schema: it
emitted ``vwap``/``trade_count`` and a provenance tail, this one emitted
``buy_volume``/``sell_volume``/``num_trades`` and no tail. Nothing at a call site
distinguished them, so which numbers a caller got depended on which package it had
imported from — and a single ``resample`` capability cannot be declared over two
functions that answer differently. This is the survivor and it emits the union, because
neither set was a superset and both are correct: an aggressor-side split is meaningful
wherever the tape classifies the aggressor, and VWAP is wanted everywhere.

The rejected alternative was to keep both and have the surfaces pick by asset class.
That is what was already happening, and it is what let a crypto route on an equity lake
report a whole session as ``sell_volume`` — see :func:`_side_volume_sql`.

Timestamps and intervals
------------------------
``local_ts`` is stored as nanosecond integers. ``make_timestamp(local_ts // 1000)``
converts ns → µs → DuckDB TIMESTAMP (microsecond precision, no TZ dependency) so
``time_bucket`` works without the optional ``pytz`` extension. The ``bar`` column is the
inclusive lower bound of each bucket as a nanosecond epoch integer
(``epoch_ns(time_bucket(...))``), matching the pipeline's ``local_ts`` convention.

Interval shorthand (``"1s"``, ``"5m"``, ``"1h"``, ``"1d"``, ``"1w"``) is translated by
:func:`crocodile.core.resample._interval.parse_interval`, which validates against a
strict regex before any component reaches SQL — no caller-controlled string is ever
interpolated into a query.

Note on ``1w``: ``time_bucket`` anchors weekly buckets on Monday, which is also where
Polars' ``group_by_dynamic`` anchors them, so this path and the Polars frame paths in
``crocodile.equity.resample.ohlcv`` agree. The record paths in that module used not to —
they floored ``local_ts`` against the epoch, which fell on a Thursday, so a weekly bar's
boundary depended on which entry point produced it and nothing on the record said which.
That is closed: :data:`crocodile.core.resample._interval.Interval.anchor_ns` carries the
origin a width is counted from, the record paths floor against it, and
``test_the_record_path_and_duckdb_put_a_trade_in_the_same_week`` pins the three answers
together. It was invisible to every daily test because a second, a minute, an hour and a
day all divide the epoch-midnight grid exactly and only a week does not.
"""

from __future__ import annotations

from collections.abc import Iterable

import duckdb
import polars as pl

from crocodile.core.resample._frame_prov import (
    PROV_RANK,
    TRUST_RANKS,
    emitted_prov,
    order_columns,
)
from crocodile.core.resample._interval import parse_interval as _parse_interval
from crocodile.core.schema.provenance import Provenance, provenance_fields, trust_rank
from crocodile.core.store.catalog import Catalog

_TRADE_QUANTITY_COLUMNS = ("amount", "size")
"""How the two lakes spell a trade's quantity, canonical first.

``migrate_lake`` renames partition directories and rewrites no Parquet file, so a lake
collected before and after the union merge holds both columns under one ``channel=trade/``
directory and ``union_by_name`` makes each row carry the other as a null. Reading
``amount`` alone did not merely undercount volume: the WHERE clause dropped every
pre-migration print, and ``first(price ORDER BY local_ts)`` runs *after* the filter, so
the bar's open became the first surviving print's price. A wrong open is not a smaller
answer, it is a different one.
"""


def _quantity_expr(available: Iterable[str]) -> str:
    """Return the SQL expression for a trade's quantity, given the view's columns.

    Built from the columns the ``trade`` view actually exposes rather than hard-coded as
    ``coalesce(amount, size)``: a lake written entirely after the merge has no ``size``
    column at all, and naming a column DuckDB cannot resolve fails the whole query.

    Raises:
        ValueError: if the view spells the quantity under neither name. Falling back to
            ``count(*)`` or to zero would report a bar with no volume as a bar with none
            traded.
    """
    columns = set(available)
    present = [c for c in _TRADE_QUANTITY_COLUMNS if c in columns]
    if not present:
        raise ValueError(
            f"the 'trade' view names a quantity under none of "
            f"{list(_TRADE_QUANTITY_COLUMNS)}; its columns are {sorted(columns)}"
        )
    if len(present) == 1:
        return present[0]
    return f"coalesce({', '.join(present)})"


def _side_volume_sql(qty: str, indent: str, *, has_side: bool) -> str:
    """Return the aggressor-side volume split, indented for its SELECT.

    A print whose aggressor is unknown is credited to **neither** side. The obvious
    spelling — ``CASE WHEN side = 'buy' THEN amount ELSE 0 END`` for buys and its
    complement for sells — makes ``buy_volume + sell_volume == volume`` hold by
    construction, and pays for it by asserting that every print nobody classified was a
    sell. On a consolidated equity tape, where ``Side.UNKNOWN`` is the normal case rather
    than the exception, that reported an entire session as seller-initiated. It was
    equally wrong on the crypto side wherever a venue declined to classify, just rarer and
    so never noticed.

    The identity is therefore ``buy_volume + sell_volume <= volume``, and the gap is the
    volume no source attributed. It stays derivable rather than becoming a fourth column:
    ``volume - buy_volume - sell_volume`` recovers it exactly, and a wire column that every
    surface must carry is a real cost for a number nobody has asked for yet.

    ``has_side`` is false when the ``trade`` view has no ``side`` column at all, which a
    lake written by a provider that never classified the aggressor genuinely does. Naming a
    column DuckDB cannot resolve fails the whole query, so the split degrades to zeroes —
    the same answer as a view whose every row says ``unknown``, which is what such a lake
    means. The old equity SQL never referenced ``side``, so this case only became reachable
    when the two implementations merged.
    """
    if not has_side:
        return f"{indent}0.0 AS buy_volume,\n{indent}0.0 AS sell_volume,\n"
    return (
        f"{indent}sum(CASE WHEN side = 'buy'  THEN {qty} ELSE 0.0 END) AS buy_volume,\n"
        f"{indent}sum(CASE WHEN side = 'sell' THEN {qty} ELSE 0.0 END) AS sell_volume,\n"
    )


def _build_no_fill_sql(
    interval_sql: str,
    interval_label: str,
    qty: str,
    rank_case: str | None,
    *,
    has_side: bool,
) -> str:
    """Return the aggregation SQL for non-empty bars only.

    ``interval_sql`` must be a pre-validated DuckDB INTERVAL literal and ``interval_label``
    the original shorthand (e.g. ``"1m"``), used as a string constant in the SELECT. Both
    come from ``parse_interval``, never from raw caller input, as does ``qty`` (one of
    :data:`_TRADE_QUANTITY_COLUMNS`) and ``rank_case`` (generated from
    :class:`~crocodile.core.schema.provenance.Provenance`). ``symbol``, ``start_ns`` and
    ``end_ns`` are always ``?`` parameters.
    """
    rank = f"    max({rank_case})::INTEGER               AS {PROV_RANK},\n" if rank_case else ""
    return (
        "SELECT\n"
        f"    epoch_ns(time_bucket({interval_sql}, make_timestamp(local_ts // 1000))) AS bar,\n"
        "    symbol,\n"
        f"    '{interval_label}' AS interval,\n"
        "    first(price ORDER BY local_ts)          AS open,\n"
        "    max(price)                              AS high,\n"
        "    min(price)                              AS low,\n"
        "    last(price ORDER BY local_ts)           AS close,\n"
        f"    sum({qty})                             AS volume,\n"
        f"{_side_volume_sql(qty, '    ', has_side=has_side)}"
        f"    sum(price * {qty}) / nullif(sum({qty}), 0.0) AS vwap,\n"
        f"{rank}"
        "    count(*)::BIGINT                        AS num_trades\n"
        "FROM trade\n"
        "WHERE symbol = ?\n"
        "  AND local_ts >= ?\n"
        "  AND local_ts <= ?\n"
        "  AND price IS NOT NULL AND NOT isnan(price)\n"
        f"  AND {qty} IS NOT NULL AND NOT isnan({qty})\n"
        "GROUP BY 1, 2, 3\n"
        "ORDER BY 1"
    )


def _build_fill_sql(
    interval_sql: str,
    interval_label: str,
    start_ns: int,
    end_ns: int,
    qty: str,
    rank_case: str | None,
    *,
    has_side: bool,
) -> str:
    """Return the fill-enabled SQL.

    ``start_ns``/``end_ns`` are plain ints, safe as numeric literals in the grid CTE;
    DuckDB does not accept ``?`` parameters inside ``generate_series`` bounds that also
    feed ``time_bucket``.
    """
    agg_rank = f"        max({rank_case})::INTEGER           AS {PROV_RANK},\n" if rank_case else ""
    filled_rank = f"        a.{PROV_RANK},\n" if rank_case else ""
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
        f"        sum({qty})                             AS volume,\n"
        f"{_side_volume_sql(qty, '        ', has_side=has_side)}"
        f"        sum(price * {qty}) / nullif(sum({qty}), 0.0) AS vwap,\n"
        f"{agg_rank}"
        "        count(*)::BIGINT                        AS num_trades\n"
        "    FROM trade\n"
        "    WHERE symbol = ?\n"
        "      AND local_ts >= ?\n"
        "      AND local_ts <= ?\n"
        "      AND price IS NOT NULL AND NOT isnan(price)\n"
        f"      AND {qty} IS NOT NULL AND NOT isnan({qty})\n"
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
        "        ? AS symbol,\n"
        f"        '{interval_label}'          AS interval,\n"
        "        a.open,\n"
        "        a.high,\n"
        "        a.low,\n"
        "        a.close,\n"
        "        coalesce(a.volume, 0.0)      AS volume,\n"
        "        coalesce(a.buy_volume, 0.0)  AS buy_volume,\n"
        "        coalesce(a.sell_volume, 0.0) AS sell_volume,\n"
        "        a.vwap,\n"
        f"{filled_rank}"
        "        coalesce(a.num_trades, 0)    AS num_trades\n"
        "    FROM grid g\n"
        "    LEFT JOIN agg a USING (bar_ts)\n"
        ")\n"
        "SELECT * FROM filled ORDER BY bar"
    )


def _trust_rank_case(floor: Provenance) -> str:
    """Return SQL folding the ``trade`` view's ``prov`` column onto its numeric trust rank.

    The aggregation has to emit the *worst* level among the prints in a bucket, and
    ``max()`` over an enum's spelling is alphabetical, not an order.
    :func:`~crocodile.core.schema.provenance.trust_rank` is the one place that order is
    written down, so the CASE is generated from it rather than restated; a second
    hand-written ordering here is a second place for it to disagree. A row whose ``prov``
    is null or unrecognised reads as the basis's own level, which is the same reading
    ``rank_inputs`` gives a frame that states nothing.

    Both the values and the ranks come from :class:`Provenance`, never from a caller, so
    interpolating them is safe.
    """
    whens = " ".join(f"WHEN '{value}' THEN {rank}" for value, rank in TRUST_RANKS.items())
    return f"CASE prov {whens} ELSE {trust_rank(floor)} END"


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

    Serves both asset classes. Queries the ``trade`` channel in the DuckDB Catalog for the
    given symbol and time range, groups by ``time_bucket``, and returns the bars as a
    Polars DataFrame.

    Args:
        catalog:    A ``Catalog`` instance pointing at the data lake root.
        symbol:     Canonical symbol, e.g. ``"deribit:BTC-PERPETUAL"`` or ``"alpaca:AAPL"``.
        start_ns:   Inclusive lower bound on ``local_ts`` (nanoseconds UTC).
        end_ns:     Inclusive upper bound on ``local_ts`` (nanoseconds UTC).
        interval:   Bar width as a shorthand string: ``"1s"``, ``"5m"``, ``"1h"``, ``"1d"``.
        fill_empty: If ``True``, insert a row for every bucket in ``[start_ns, end_ns]``
                    that contains no trades, with ``volume=0`` and OHLC null. If ``False``
                    (default), only non-empty buckets appear.

    Returns:
        A Polars DataFrame ordered by ``bar`` ascending, with columns::

            bar             Int64   nanosecond epoch of the bucket start
            symbol          Utf8
            interval        Utf8
            open            Float64 (NULL for empty fill bars)
            high            Float64
            low             Float64
            close           Float64
            volume          Float64 (0.0 for empty fill bars)
            buy_volume      Float64 volume whose aggressor was a buyer
            sell_volume     Float64 volume whose aggressor was a seller
            vwap            Float64 sum(price*qty)/sum(qty), NULL at zero volume
            num_trades      Int64   (0 for empty fill bars)
            prov            Utf8    ohlcv_from_trades' level, floored by the worst print's
            prov_basis      Utf8    always ``"ohlcv_from_trades"``
            prov_confidence Float64

        ``buy_volume + sell_volume <= volume``; the remainder is volume whose aggressor no
        source classified. See :func:`_side_volume_sql` for why it is not forced to
        balance.

        An empty DataFrame (0 rows, 0 columns) if the ``trade`` view does not exist yet,
        matching the ``Catalog`` empty-result contract: callers must check ``len(df) == 0``
        before touching a column.

    Raises:
        ValueError: If ``interval`` cannot be parsed, or the ``trade`` view names a
            quantity under neither spelling.
    """
    interval_sql = _parse_interval(interval).sql
    # The validated, normalised label stored in the output column — already stripped and
    # lowercased by the same rule parse_interval matched on.
    interval_label = interval.strip().lower()

    # Refresh views so newly written files are visible.
    catalog.refresh_views()
    conn = catalog.connection

    try:
        columns = conn.execute("SELECT * FROM trade LIMIT 0").pl().columns
    except (duckdb.CatalogException, duckdb.IOException):
        # No trade data has ever been written → the view does not exist.
        return pl.DataFrame()
    qty = _quantity_expr(columns)

    tail = provenance_fields("ohlcv_from_trades")
    # A view with no ``prov`` column states no level, and the emitted one then rests on
    # the basis alone — the same reading ``rank_inputs`` gives such a frame.
    rank_case = _trust_rank_case(tail.prov) if "prov" in columns else None
    has_side = "side" in columns

    if fill_empty:
        sql = _build_fill_sql(
            interval_sql, interval_label, start_ns, end_ns, qty, rank_case, has_side=has_side
        )
        # Parameters: symbol (agg WHERE), start_ns, end_ns, symbol (filled SELECT)
        params: list[object] = [symbol, start_ns, end_ns, symbol]
    else:
        sql = _build_no_fill_sql(interval_sql, interval_label, qty, rank_case, has_side=has_side)
        params = [symbol, start_ns, end_ns]

    try:
        result = conn.execute(sql, params)
        df: pl.DataFrame = result.pl()
    except (duckdb.CatalogException, duckdb.IOException):
        # View may not exist yet (no trade data written) → return empty.
        return pl.DataFrame()

    has_rank = rank_case is not None and PROV_RANK in df.columns
    if has_rank:
        # A filled bucket with no prints joins nothing, so its rank is null; it makes no
        # claim of its own and takes the basis's level, as an empty frame would.
        df = df.with_columns(pl.col(PROV_RANK).fill_null(trust_rank(tail.prov)).cast(pl.Int32))
    df = df.with_columns(
        emitted_prov(has_rank, tail.prov),
        pl.lit(tail.prov_basis).alias("prov_basis"),
        pl.lit(tail.prov_confidence).alias("prov_confidence"),
    )
    if has_rank:
        df = df.drop(PROV_RANK)

    return order_columns(df)
