"""OHLCV resampling from equity trades, quotes, or lower-resolution bars.

These resamplers build canonical records (``crocodile.core.schema.records``)
stamped ``AssetClass.EQUITY``. They stay under ``crocodile.equity`` because the
aggregation is an equity one, not because the record types are: see below.

Interval parsing here uses ``crocodile.equity.resample._interval.parse_interval``,
which returns a 3-tuple and is a *different function* from the identically named
2-tuple ``crocodile.core.resample._interval.parse_interval``. See the docstring
of ``crocodile.equity.resample._interval`` for why both exist and who depends on
which.

Two functions named ``resample_ohlcv``
--------------------------------------
:func:`resample_ohlcv` here and ``crocodile.core.resample.ohlcv.resample_ohlcv``
have the same name and the same signature, aggregate the same ``trade`` view,
and return different schemas. They are not interchangeable and neither is a
superset of the other:

==================  ===============================  =========================
                    ``core`` (crypto)                 ``equity`` (this module)
==================  ===============================  =========================
taker side          ``side`` ∈ {buy, sell}            ``side`` = ``unknown``
extra outputs       ``buy_volume``, ``sell_volume``   ``vwap``
trade count         ``num_trades``                    ``trade_count``
==================  ===============================  =========================

A crypto trade carries an aggressor ``side``, so splitting volume into
``buy_volume``/``sell_volume`` is meaningful and ``vwap`` is not carried on the
record. A consolidated-tape equity print carries no aggressor side, so an equity
``Trade`` states ``Side.UNKNOWN``; what equities do want, and what
:class:`~crocodile.core.schema.records.OHLCV` carries as a field, is ``vwap``.

**The way to get this wrong changed with the union merge.** Both now read the
same quantity column — the one that used to separate them was equity's ``size``,
and the canonical spelling is ``amount``. So calling the crypto one on an equity
lake no longer returns zero rows: it returns bars whose ``buy_volume`` is 0.0 and
whose ``sell_volume`` is the whole session, because ``CASE WHEN side = 'buy'``
puts every ``unknown`` print on the sell side. An empty result announces itself;
a plausible one does not.

The rename has a second hazard pointing the other way, and this module reads both
spellings because of it: a lake collected before *and* after the merge holds both
columns, and filtering on ``amount`` alone silently discarded the pre-migration
prints — moving the bar's open, not merely shrinking its volume. See
:data:`_TRADE_QUANTITY_COLUMNS`.

Choose by asset class, not by which one is easier to import.
"""

from __future__ import annotations

import heapq
from collections.abc import Iterable, Iterator
from itertools import pairwise
from typing import Final

import duckdb
import polars as pl

from crocodile.core.schema.enums import AssetClass
from crocodile.core.schema.provenance import (
    Provenance,
    ProvenanceFields,
    level_for,
    provenance_fields,
    trust_rank,
    worst_provenance,
)
from crocodile.core.schema.records import OHLCV, Quote, Trade
from crocodile.core.store.catalog import Catalog
from crocodile.equity.resample._interval import parse_interval as _parse_interval

__all__ = [
    "resample_bars_df",
    "resample_bars_to_bars",
    "resample_ohlcv",
    "resample_quotes_df",
    "resample_quotes_to_bars",
    "resample_trades_df",
    "resample_trades_to_bars",
]


# ---------------------------------------------------------------------------
# DuckDB Catalog Resampling
# ---------------------------------------------------------------------------
#
# ``interval_sql`` and ``interval_label`` below are both produced by
# ``_parse_interval``'s strict regex (digits + one of s/m/h/d/w), never taken
# raw from a caller, so they are safe to interpolate into the SQL templates.
# ``symbol``, ``start_ns`` and ``end_ns`` are always ``?`` parameters.


_TRADE_QUANTITY_COLUMNS = ("amount", "size")
"""How the two lakes spell a trade's quantity, canonical first.

``migrate_lake`` renames partition directories and rewrites no Parquet file, so a
lake collected before and after the union merge holds both columns under one
``channel=trade/`` directory and ``union_by_name`` makes each row carry the other
as a null. Reading ``amount`` alone did not merely undercount volume: the WHERE
clause dropped every pre-migration print, and ``first(price ORDER BY local_ts)``
runs *after* the filter, so the bar's open became the first surviving print's
price. A wrong open is not a smaller answer, it is a different one.
"""


def _quantity_expr(available: Iterable[str]) -> str:
    """Return the SQL expression for a trade's quantity, given the view's columns.

    Built from the columns the ``trade`` view actually exposes rather than
    hard-coded as ``coalesce(amount, size)``: a lake written entirely after the
    merge has no ``size`` column at all, and naming a column DuckDB cannot resolve
    fails the whole query.

    Raises:
        ValueError: if the view spells the quantity under neither name. Falling
            back to ``count(*)`` or to zero would report a bar with no volume as
            a bar with none traded.
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


def _build_no_fill_sql(interval_sql: str, interval_label: str, qty: str) -> str:
    """Return the aggregation SQL for non-empty bars only."""
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
        f"    sum(price * {qty}) / nullif(sum({qty}), 0.0) AS vwap,\n"
        "    count(*)::BIGINT                        AS trade_count\n"
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
    interval_sql: str, interval_label: str, start_ns: int, end_ns: int, qty: str
) -> str:
    """Return the fill-enabled SQL.

    ``start_ns``/``end_ns`` are plain ints, safe as numeric literals in the grid
    CTE; DuckDB does not accept ``?`` parameters inside ``generate_series``
    bounds that also feed ``time_bucket``.
    """
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
        f"        sum(price * {qty}) / nullif(sum({qty}), 0.0) AS vwap,\n"
        "        count(*)::BIGINT                        AS trade_count\n"
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
        "        a.vwap,\n"
        "        coalesce(a.trade_count, 0)   AS trade_count\n"
        "    FROM grid g\n"
        "    LEFT JOIN agg a USING (bar_ts)\n"
        ")\n"
        "SELECT * FROM filled ORDER BY bar"
    )


def resample_ohlcv(
    catalog: Catalog,
    symbol: str,
    start_ns: int,
    end_ns: int,
    interval: str,
    *,
    fill_empty: bool = False,
) -> pl.DataFrame:
    """Resample equity trade records from the DuckDB Catalog into OHLCV bars.

    Queries the ``trade`` view for *symbol* in ``[start_ns, end_ns]``, groups by
    ``time_bucket``, and returns the bars as a Polars DataFrame.

    Args:
        catalog:    A ``Catalog`` over the lake root.
        symbol:     Canonical symbol, e.g. ``"alpaca:AAPL"``.
        start_ns:   Inclusive lower bound on ``local_ts`` (nanoseconds UTC).
        end_ns:     Inclusive upper bound on ``local_ts`` (nanoseconds UTC).
        interval:   Bar width shorthand: ``"1s"``, ``"5m"``, ``"1h"``, ``"1d"``.
        fill_empty: Emit a row for every bucket in range, with ``volume=0`` and
                    ``open/high/low/close/vwap`` NULL where no prints landed.

    Returns:
        A Polars DataFrame ordered by ``bar``, with columns::

            bar          Int64    nanosecond epoch of the bucket start
            symbol       Utf8
            interval     Utf8
            open         Float64  (NULL for empty fill bars)
            high         Float64
            low          Float64
            close        Float64
            volume       Float64  (0.0 for empty fill bars)
            vwap         Float64  sum(price*amount)/sum(amount), NULL at zero volume
            trade_count  Int64    (0 for empty fill bars)

        An empty DataFrame (0 rows, 0 columns) if the ``trade`` view does not
        exist yet, matching the ``Catalog`` empty-result contract: callers must
        check ``len(df) == 0`` before touching a column.

        This is the *equity* schema. See the module docstring for how it differs
        from ``crocodile.core.resample.ohlcv.resample_ohlcv``'s.

    Raises:
        ValueError: If *interval* cannot be parsed.
    """
    _ns, interval_sql, _polars_str = _parse_interval(interval)
    interval_label = interval.strip().lower()

    # Refresh views so files written since construction are visible.
    catalog.refresh_views()
    conn = catalog.connection

    try:
        columns = conn.execute("SELECT * FROM trade LIMIT 0").pl().columns
    except (duckdb.CatalogException, duckdb.IOException):
        # No trade data has ever been written → the view does not exist.
        return pl.DataFrame()
    qty = _quantity_expr(columns)

    if fill_empty:
        sql = _build_fill_sql(interval_sql, interval_label, start_ns, end_ns, qty)
        # symbol (agg WHERE), start_ns, end_ns, symbol (filled SELECT)
        params: list[object] = [symbol, start_ns, end_ns, symbol]
    else:
        sql = _build_no_fill_sql(interval_sql, interval_label, qty)
        params = [symbol, start_ns, end_ns]

    try:
        result = conn.execute(sql, params)
        df: pl.DataFrame = result.pl()
    except (duckdb.CatalogException, duckdb.IOException):
        # No trade data has ever been written → the view does not exist.
        return pl.DataFrame()

    return df


# ---------------------------------------------------------------------------
# Stream/Record-Level Resampling
# ---------------------------------------------------------------------------


def _detect_scale_and_adjust_interval(ts: int, interval_ns: int) -> int:
    """Detect timestamp unit and return adjusted_interval in the same unit.

    If ts is a small offset or mock timestamp (ts < 1e11), we default to nanoseconds.
    Otherwise, we use the epoch ranges:
    - ts > 1e17: nanoseconds
    - 1e14 < ts <= 1e17: microseconds
    - 1e11 < ts <= 1e14: milliseconds
    """
    if ts < 1e11:
        return interval_ns
    elif ts > 1e17:  # nanoseconds
        return interval_ns
    elif ts > 1e14:  # microseconds
        return max(1, interval_ns // 1000)
    else:  # milliseconds
        return max(1, interval_ns // 1_000_000)


def _ts_unit_ns(ts: int) -> int:
    """Return how many nanoseconds one unit of ``ts``'s own clock is worth.

    The mirror of :func:`_detect_scale_and_adjust_interval`, which converts a duration
    *into* the stream's unit. Coverage arithmetic needs the conversion back out: bucket
    boundaries and record timestamps are in the stream's unit, and a confidence formula's
    inputs are in nanoseconds. Mixing the two silently scaled a millisecond stream's
    coverage by a million.
    """
    if ts < 1e11 or ts > 1e17:
        return 1
    if ts > 1e14:
        return 1_000
    return 1_000_000


_MINUTE_NS: Final = 60 * 1_000_000_000
_DAY_NS: Final = 24 * 60 * _MINUTE_NS
_WEEK_NS: Final = 7 * _DAY_NS
_REGULAR_SESSION_NS: Final = 390 * _MINUTE_NS
"""One regular US trading session, the same 390 one-minute bars ``yahoo_1m_vap`` divides by.

Stated here rather than imported from the registry because it is the same *fact* about the
same market reached for a different reason, and the registry scopes its copy to that one
basis deliberately: a crypto volume-at-price day has 1440 minutes and no session at all.
"""

_SESSIONS_PER_WEEK: Final = 5


def _tradeable_ns(interval_ns: int) -> int:
    """Return how much of a bucket ``interval_ns`` wide a US regular session can fill.

    The denominator ``ohlcv_from_ohlcv`` measures against. A bucket no wider than one
    session is entirely inside trading hours and is its own denominator; wider ones are
    counted in whole weeks, whole days and a remainder, so a complete session re-bucketed
    to ``1d`` scores 1.0 instead of the 390/1440 = 0.2708 that made every complete equity
    daily bar fail a ``>= 0.5`` filter.

    Holidays and half-days are not modelled. That biases the denominator *up*, which
    lowers the score and never raises it — the safe direction for a number consumers
    filter on, and the reason a calendar is not a prerequisite for measuring at all.
    """
    if interval_ns <= _REGULAR_SESSION_NS:
        return interval_ns
    weeks, rest = divmod(interval_ns, _WEEK_NS)
    days, remainder = divmod(rest, _DAY_NS)
    sessions = weeks * _SESSIONS_PER_WEEK + min(days, _SESSIONS_PER_WEEK)
    return sessions * _REGULAR_SESSION_NS + min(remainder, _REGULAR_SESSION_NS)


def _union_coverage(
    segments: list[tuple[int, int, float]], lo: int, hi: int
) -> tuple[int, float]:
    """Return ``(covered, sampled)`` over the union of ``segments`` clipped to ``[lo, hi)``.

    ``segments`` are ``(start, end, confidence)`` in the stream's own timestamp unit.
    ``covered`` is the measure of the union — every instant counted once however many
    inputs cover it — and ``sampled`` weights each instant by the *best* confidence
    covering it.

    A union rather than a sum of widths because a lake spanning the migration holds the
    same day under ``channel=bar/`` and ``channel=ohlcv/`` at once, and summing counted it
    twice: the duplicated day doubled ``volume``, doubled ``num_trades``, and *raised* the
    emitted bar's ``prov_confidence``. A duplicate making a derivation look better sampled
    is the one direction a confidence number must never move.

    Overlapping instants take the best rather than the worst confidence because the
    observation really was made that well by at least one input; the worst level among the
    inputs is a separate claim and ``worst_provenance`` carries it.
    """
    clipped = [
        (max(start, lo), min(end, hi), confidence)
        for start, end, confidence in segments
        if min(end, hi) > max(start, lo)
    ]
    if not clipped:
        return 0, 0.0
    clipped.sort()
    boundaries = sorted({point for start, end, _ in clipped for point in (start, end)})

    # (-confidence, end): a max-heap on confidence with lazy expiry. An entry whose end
    # has passed is discarded when it reaches the top, which is enough — it can only hide
    # entries with a *lower* confidence, and those are still live below it.
    active: list[tuple[float, int]] = []
    index = 0
    covered = 0
    sampled = 0.0
    for left, right in pairwise(boundaries):
        while index < len(clipped) and clipped[index][0] <= left:
            heapq.heappush(active, (-clipped[index][2], clipped[index][1]))
            index += 1
        while active and active[0][1] <= left:
            heapq.heappop(active)
        if not active:
            continue
        width = right - left
        covered += width
        sampled += width * -active[0][0]
    return covered, sampled


def resample_trades_to_bars(trades: Iterable[Trade], interval: str) -> Iterator[OHLCV]:
    """Resample an iterable of Trade records into OHLCV records.

    Assumes Trade records are ordered by local_ts.
    """
    interval_ns, _, interval_label = _parse_interval(interval)
    adjusted_interval: int | None = None

    # A bar is not something a venue published. Stated once here and spread onto every
    # record below: a resampled record with no `prov=` inherits the header default, which
    # says NATIVE — the claim that the venue reported this bar directly.
    basis = provenance_fields("ohlcv_from_trades")
    input_levels: list[Provenance] = []

    def _tail_for_bucket() -> ProvenanceFields:
        """The bucket's tail: the basis level floored by the worst input's level.

        The level a basis registers is a ceiling, not a measurement, and the fix that
        introduced :func:`worst_provenance` applied it to one of this module's three
        record paths. ``google_finance`` emits ``Trade(prov=SYNTHETIC,
        prov_basis='scraped_last_price')`` — a last price lifted off a rendered page —
        and bars aggregated from those prints came back ``prov=derived``, so a consumer
        filtering ``WHERE prov != 'synthetic'`` got them with nothing to notice.

        ``prov_confidence`` stays the registered constant, which is the separation this
        registry states outright: confidence measures sampling adequacy *within* a level,
        and the bar really is the whole of the prints it was handed. Which level it is at
        is ``prov``'s answer, and that is what moves here.
        """
        return basis._replace(prov=worst_provenance([basis.prov, *input_levels]))

    current_bucket: int | None = None
    open_px = 0.0
    high_px = 0.0
    low_px = 0.0
    close_px = 0.0
    volume = 0.0
    vwap_sum = 0.0
    trade_count = 0

    source = ""
    symbol = ""
    symbol_raw = ""

    previous_ts: int | None = None

    for trade in trades:
        if previous_ts is not None and trade.local_ts < previous_ts:
            raise ValueError(
                f"Unsorted stream: trade local_ts {trade.local_ts} "
                f"is less than previous_ts {previous_ts}"
            )
        previous_ts = trade.local_ts

        if not source:
            source = trade.source
            symbol = trade.symbol
            symbol_raw = trade.symbol_raw

        if adjusted_interval is None:
            adjusted_interval = _detect_scale_and_adjust_interval(trade.local_ts, interval_ns)

        bucket = (trade.local_ts // adjusted_interval) * adjusted_interval

        if current_bucket is None:
            current_bucket = bucket
            open_px = trade.price
            high_px = trade.price
            low_px = trade.price
            close_px = trade.price
            volume = trade.amount
            vwap_sum = trade.price * trade.amount
            trade_count = 1
            input_levels = [trade.prov]
        elif bucket == current_bucket:
            high_px = max(high_px, trade.price)
            low_px = min(low_px, trade.price)
            close_px = trade.price
            volume += trade.amount
            vwap_sum += trade.price * trade.amount
            trade_count += 1
            input_levels.append(trade.prov)
        else:
            vwap = (vwap_sum / volume) if volume > 0 else None
            tail = _tail_for_bucket()
            yield OHLCV(
                source=source,
                symbol=symbol,
                symbol_raw=symbol_raw,
                source_ts=None,
                local_ts=current_bucket,
                interval=interval_label,
                open=open_px,
                high=high_px,
                low=low_px,
                close=close_px,
                volume=volume,
                vwap=vwap,
                num_trades=trade_count,
                asset_class=AssetClass.EQUITY,
                prov=tail.prov,
                prov_basis=tail.prov_basis,
                prov_confidence=tail.prov_confidence,
                prov_inputs=tail.prov_inputs,
            )
            current_bucket = bucket
            open_px = trade.price
            high_px = trade.price
            low_px = trade.price
            close_px = trade.price
            volume = trade.amount
            vwap_sum = trade.price * trade.amount
            trade_count = 1
            input_levels = [trade.prov]

    if current_bucket is not None:
        vwap = (vwap_sum / volume) if volume > 0 else None
        tail = _tail_for_bucket()
        yield OHLCV(
            source=source,
            symbol=symbol,
            symbol_raw=symbol_raw,
            source_ts=None,
            local_ts=current_bucket,
            interval=interval_label,
            open=open_px,
            high=high_px,
            low=low_px,
            close=close_px,
            volume=volume,
            vwap=vwap,
            num_trades=trade_count,
            asset_class=AssetClass.EQUITY,
            prov=tail.prov,
            prov_basis=tail.prov_basis,
            prov_confidence=tail.prov_confidence,
            prov_inputs=tail.prov_inputs,
        )


def resample_quotes_to_bars(
    quotes: Iterable[Quote], interval: str, price_type: str = "mid"
) -> Iterator[OHLCV]:
    """Resample Quote records into OHLCV records based on bid, ask, or mid-price.

    Assumes Quote records are ordered by local_ts.
    """
    interval_ns, _, interval_label = _parse_interval(interval)
    adjusted_interval: int | None = None

    # SYNTHETIC, not DERIVED: these prices are quotes, nothing here was transacted, and
    # the `volume` below is a structural zero rather than a measured one.
    basis = provenance_fields("ohlcv_from_quotes")
    input_levels: list[Provenance] = []

    def _tail_for_bucket() -> ProvenanceFields:
        """The bucket's tail, floored by the worst input quote's level.

        SYNTHETIC is already the floor for most inputs, but not for all of them:
        UNAVAILABLE is worse, and a quote reconstructed from something worse than a
        venue reading must not be re-labelled by the aggregation. Same rule, same
        reason, as the trade path — the level a basis registers is a ceiling.
        """
        return basis._replace(prov=worst_provenance([basis.prov, *input_levels]))

    current_bucket: int | None = None
    open_px = 0.0
    high_px = 0.0
    low_px = 0.0
    close_px = 0.0
    volume = 0.0
    quote_count = 0
    price_sum = 0.0

    source = ""
    symbol = ""
    symbol_raw = ""

    previous_ts: int | None = None

    for quote in quotes:
        if previous_ts is not None and quote.local_ts < previous_ts:
            raise ValueError(
                f"Unsorted stream: quote local_ts {quote.local_ts} "
                f"is less than previous_ts {previous_ts}"
            )
        previous_ts = quote.local_ts

        if not source:
            source = quote.source
            symbol = quote.symbol
            symbol_raw = quote.symbol_raw

        if adjusted_interval is None:
            adjusted_interval = _detect_scale_and_adjust_interval(quote.local_ts, interval_ns)

        bucket = (quote.local_ts // adjusted_interval) * adjusted_interval

        if price_type == "mid":
            price = (quote.bid_px + quote.ask_px) / 2.0
        elif price_type == "bid":
            price = quote.bid_px
        elif price_type == "ask":
            price = quote.ask_px
        else:
            raise ValueError(f"Unknown price_type: {price_type!r}")

        if current_bucket is None:
            current_bucket = bucket
            open_px = price
            high_px = price
            low_px = price
            close_px = price
            volume = 0.0
            price_sum = price
            quote_count = 1
            input_levels = [quote.prov]
        elif bucket == current_bucket:
            high_px = max(high_px, price)
            low_px = min(low_px, price)
            close_px = price
            price_sum += price
            quote_count += 1
            input_levels.append(quote.prov)
        else:
            vwap = (price_sum / quote_count) if quote_count > 0 else None
            tail = _tail_for_bucket()
            yield OHLCV(
                source=source,
                symbol=symbol,
                symbol_raw=symbol_raw,
                source_ts=None,
                local_ts=current_bucket,
                interval=interval_label,
                open=open_px,
                high=high_px,
                low=low_px,
                close=close_px,
                volume=volume,
                vwap=vwap,
                num_trades=quote_count,
                asset_class=AssetClass.EQUITY,
                prov=tail.prov,
                prov_basis=tail.prov_basis,
                prov_confidence=tail.prov_confidence,
                prov_inputs=tail.prov_inputs,
            )
            current_bucket = bucket
            open_px = price
            high_px = price
            low_px = price
            close_px = price
            volume = 0.0
            price_sum = price
            quote_count = 1
            input_levels = [quote.prov]

    if current_bucket is not None:
        vwap = (price_sum / quote_count) if quote_count > 0 else None
        tail = _tail_for_bucket()
        yield OHLCV(
            source=source,
            symbol=symbol,
            symbol_raw=symbol_raw,
            source_ts=None,
            local_ts=current_bucket,
            interval=interval_label,
            open=open_px,
            high=high_px,
            low=low_px,
            close=close_px,
            volume=volume,
            vwap=vwap,
            num_trades=quote_count,
            asset_class=AssetClass.EQUITY,
            prov=tail.prov,
            prov_basis=tail.prov_basis,
            prov_confidence=tail.prov_confidence,
            prov_inputs=tail.prov_inputs,
        )


def _bar_width_ns(interval_label: str, cache: dict[str, int]) -> int:
    """Return one input bar's declared width in nanoseconds.

    Raises:
        ValueError: from ``_parse_interval`` if the bar's own ``interval`` string is not
            one this codebase can parse. That is loud on purpose: the width is the
            numerator of the emitted bar's confidence, and a bar silently counted as
            zero-width would drag the score down with nothing to point at.
    """
    width = cache.get(interval_label)
    if width is None:
        width = _parse_interval(interval_label)[0]
        cache[interval_label] = width
    return width


def resample_bars_to_bars(bars: Iterable[OHLCV], interval: str) -> Iterator[OHLCV]:
    """Resample lower-resolution OHLCV records into higher-resolution OHLCV records.

    Assumes OHLCV records are ordered by local_ts.

    Each emitted bar measures its own coverage and inherits its worst input's level; see
    the ``ohlcv_from_ohlcv`` registration and :func:`worst_provenance`. Both used to be
    constants, and both were wrong in the same direction: a 1d bar built from three 1m
    bars reported ``prov_confidence=1.0``, and re-bucketing quote-derived bars turned
    ``synthetic`` into ``derived`` over prices that were never transacted.
    """
    interval_ns, _, interval_label = _parse_interval(interval)
    adjusted_interval: int | None = None
    unit_ns = 1
    width_cache: dict[str, int] = {}
    tradeable_ns = _tradeable_ns(interval_ns)

    # The spans the inputs of the bucket being accumulated declare, in the stream's own
    # timestamp unit, and the worst level seen among them. Spans rather than a running
    # total because coverage is the *union* of them: the same day arrives twice from a
    # lake that holds it under both channel tags, and a sum counted it twice.
    segments: list[tuple[int, int, float]] = []
    input_levels: list[Provenance] = []

    def _tail_for_bucket() -> ProvenanceFields:
        assert current_bucket is not None and adjusted_interval is not None
        covered, sampled = _union_coverage(
            segments, current_bucket, current_bucket + adjusted_interval
        )
        covered_ns = covered * unit_ns
        fields = provenance_fields(
            "ohlcv_from_ohlcv",
            {
                "covered_ns": covered_ns,
                # Clamped, not merely rounded: a float weighting can round to one
                # nanosecond above the span it weights, and the formula refuses an
                # instant sampled better than it is covered.
                "sampled_ns": min(round(sampled * unit_ns), covered_ns),
                "tradeable_ns": tradeable_ns,
            },
        )
        return fields._replace(prov=worst_provenance([fields.prov, *input_levels]))

    current_bucket: int | None = None
    open_px = 0.0
    high_px = 0.0
    low_px = 0.0
    close_px = 0.0
    volume = 0.0
    vwap_vol_sum = 0.0
    trade_count_sum = 0
    has_trade_count = False

    source = ""
    symbol = ""
    symbol_raw = ""

    previous_ts: int | None = None

    for bar in bars:
        if previous_ts is not None and bar.local_ts < previous_ts:
            raise ValueError(
                f"Unsorted stream: bar local_ts {bar.local_ts} "
                f"is less than previous_ts {previous_ts}"
            )
        previous_ts = bar.local_ts

        if not source:
            source = bar.source
            symbol = bar.symbol
            symbol_raw = bar.symbol_raw

        if adjusted_interval is None:
            adjusted_interval = _detect_scale_and_adjust_interval(bar.local_ts, interval_ns)
            unit_ns = _ts_unit_ns(bar.local_ts)

        bucket = (bar.local_ts // adjusted_interval) * adjusted_interval
        width = max(1, _bar_width_ns(bar.interval, width_cache) // unit_ns)
        segment = (bar.local_ts, bar.local_ts + width, bar.prov_confidence)

        if current_bucket is None:
            current_bucket = bucket
            open_px = bar.open
            high_px = bar.high
            low_px = bar.low
            close_px = bar.close
            volume = bar.volume
            vwap_val = bar.vwap if bar.vwap is not None else bar.close
            vwap_vol_sum = vwap_val * bar.volume
            segments = [segment]
            input_levels = [bar.prov]
            if bar.num_trades is not None:
                trade_count_sum = bar.num_trades
                has_trade_count = True
            else:
                trade_count_sum = 1
                has_trade_count = False
        elif bucket == current_bucket:
            high_px = max(high_px, bar.high)
            low_px = min(low_px, bar.low)
            close_px = bar.close
            volume += bar.volume
            vwap_val = bar.vwap if bar.vwap is not None else bar.close
            vwap_vol_sum += vwap_val * bar.volume
            segments.append(segment)
            input_levels.append(bar.prov)
            if bar.num_trades is not None:
                trade_count_sum += bar.num_trades
                has_trade_count = True
            else:
                trade_count_sum += 1
        else:
            vwap = (
                (vwap_vol_sum / volume)
                if volume > 0
                else (vwap_vol_sum if vwap_vol_sum > 0 else None)
            )
            tail = _tail_for_bucket()
            yield OHLCV(
                source=source,
                symbol=symbol,
                symbol_raw=symbol_raw,
                source_ts=None,
                local_ts=current_bucket,
                interval=interval_label,
                open=open_px,
                high=high_px,
                low=low_px,
                close=close_px,
                volume=volume,
                vwap=vwap,
                num_trades=trade_count_sum if has_trade_count else None,
                asset_class=AssetClass.EQUITY,
                prov=tail.prov,
                prov_basis=tail.prov_basis,
                prov_confidence=tail.prov_confidence,
                prov_inputs=tail.prov_inputs,
            )
            current_bucket = bucket
            open_px = bar.open
            high_px = bar.high
            low_px = bar.low
            close_px = bar.close
            volume = bar.volume
            vwap_val = bar.vwap if bar.vwap is not None else bar.close
            vwap_vol_sum = vwap_val * bar.volume
            segments = [segment]
            input_levels = [bar.prov]
            if bar.num_trades is not None:
                trade_count_sum = bar.num_trades
                has_trade_count = True
            else:
                trade_count_sum = 1
                has_trade_count = False

    if current_bucket is not None:
        vwap = (
            (vwap_vol_sum / volume) if volume > 0 else (vwap_vol_sum if vwap_vol_sum > 0 else None)
        )
        tail = _tail_for_bucket()
        yield OHLCV(
            source=source,
            symbol=symbol,
            symbol_raw=symbol_raw,
            source_ts=None,
            local_ts=current_bucket,
            interval=interval_label,
            open=open_px,
            high=high_px,
            low=low_px,
            close=close_px,
            volume=volume,
            vwap=vwap,
            num_trades=trade_count_sum if has_trade_count else None,
            asset_class=AssetClass.EQUITY,
            prov=tail.prov,
            prov_basis=tail.prov_basis,
            prov_confidence=tail.prov_confidence,
            prov_inputs=tail.prov_inputs,
        )


# ---------------------------------------------------------------------------
# Polars-Based Resampling
# ---------------------------------------------------------------------------


_EMPTY_BAR_SCHEMA: dict[str, pl.DataType] = {
    "bar": pl.Int64(),
    "symbol": pl.String(),
    "interval": pl.String(),
    "open": pl.Float64(),
    "high": pl.Float64(),
    "low": pl.Float64(),
    "close": pl.Float64(),
    "volume": pl.Float64(),
    "vwap": pl.Float64(),
    "trade_count": pl.Int64(),
    "prov": pl.String(),
    "prov_basis": pl.String(),
    "prov_confidence": pl.Float64(),
}

_DESIRED_COLS = [
    "bar",
    "symbol",
    "interval",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "vwap",
    "trade_count",
    "prov",
    "prov_basis",
    "prov_confidence",
]


# ---------------------------------------------------------------------------
# The provenance tail, in frame form
# ---------------------------------------------------------------------------
# The three functions above build records and state their tail on each one. The
# three below build frames and used to state nothing at all — a bar frame came
# back with no ``prov`` column, so a caller writing those rows to the lake, or
# comparing them against records, had the header default applied for them: NATIVE
# at confidence 1.0, the claim that a venue reported this bar. Same laundering,
# one type further out.

_TRUST_RANKS: dict[str, int] = {level.value: trust_rank(level) for level in Provenance}
_LEVEL_BY_RANK: dict[int, str] = {rank: value for value, rank in _TRUST_RANKS.items()}
_PROV_RANK = "_prov_rank"


def _rank_inputs(df: pl.DataFrame, floor: Provenance) -> tuple[pl.DataFrame, bool]:
    """Attach the numeric trust rank of each input row's ``prov``, if it declares one.

    Returns the frame and whether the rank column was added. A frame with no ``prov``
    column makes no provenance claim at all, and the emitted level then rests on the
    basis alone — which is the same reading the record paths give a default-constructed
    input.
    """
    if "prov" not in df.columns:
        return df, False
    return (
        df.with_columns(
            pl.col("prov")
            .replace_strict(_TRUST_RANKS, default=trust_rank(floor), return_dtype=pl.Int32)
            .fill_null(trust_rank(floor))
            .alias(_PROV_RANK)
        ),
        True,
    )


def _emitted_prov(has_rank: bool, floor: Provenance) -> pl.Expr:
    """Return the emitted ``prov`` for a bucket: the basis level, floored by its inputs."""
    if not has_rank:
        return pl.lit(floor.value).alias("prov")
    return (
        pl.max_horizontal(pl.col(_PROV_RANK), pl.lit(trust_rank(floor), dtype=pl.Int32))
        .replace_strict(_LEVEL_BY_RANK, return_dtype=pl.String)
        .alias("prov")
    )


def _order_columns(resampled: pl.DataFrame) -> pl.DataFrame:
    """Put the canonical bar columns first, then any remaining group keys."""
    out_cols = [c for c in _DESIRED_COLS if c in resampled.columns] + [
        c for c in resampled.columns if c not in _DESIRED_COLS
    ]
    return resampled.select(out_cols)


def resample_trades_df(df: pl.DataFrame, interval: str) -> pl.DataFrame:
    """Resample trades DataFrame using Polars.

    Expects columns: local_ts, price, amount, and optionally symbol, source, symbol_raw,
    and the provenance tail a lake-read frame carries.

    The output carries ``prov``, ``prov_basis`` and ``prov_confidence``, the same tail
    :func:`resample_trades_to_bars` puts on its records and for the same reason: a bar is
    not something a venue published, and a frame that says nothing gets the header default
    said for it. ``prov`` is floored by the worst input's level, so bars built from
    ``google_finance``'s scraped last prices come back SYNTHETIC rather than DERIVED.
    """
    if len(df) == 0:
        return pl.DataFrame(schema=_EMPTY_BAR_SCHEMA)

    _ns, _sql, polars_str = _parse_interval(interval)
    interval_label = interval.strip().lower()

    tail = provenance_fields("ohlcv_from_trades")
    df, has_rank = _rank_inputs(df, tail.prov)

    df_dt = df.with_columns(pl.from_epoch("local_ts", time_unit="ns").alias("datetime")).sort(
        "datetime"
    )

    group_keys = [k for k in ["symbol", "source", "symbol_raw"] if k in df.columns]

    aggregates = [
        pl.col("price").first().alias("open"),
        pl.col("price").max().alias("high"),
        pl.col("price").min().alias("low"),
        pl.col("price").last().alias("close"),
        pl.col("amount").sum().alias("volume"),
        pl.when(pl.col("amount").sum() > 0.0)
        .then((pl.col("price") * pl.col("amount")).sum() / pl.col("amount").sum())
        .otherwise(None)
        .alias("vwap"),
        pl.len().cast(pl.Int64).alias("trade_count"),
    ]
    if has_rank:
        aggregates.append(pl.col(_PROV_RANK).max().alias(_PROV_RANK))

    resampled = df_dt.group_by_dynamic(
        "datetime",
        every=polars_str,
        group_by=group_keys,
        closed="left",
    ).agg(aggregates)

    resampled = resampled.with_columns(
        pl.col("datetime").dt.epoch("ns").alias("bar"),
        pl.lit(interval_label).alias("interval"),
        _emitted_prov(has_rank, tail.prov),
        pl.lit(tail.prov_basis).alias("prov_basis"),
        pl.lit(tail.prov_confidence).alias("prov_confidence"),
    ).drop("datetime")
    if has_rank:
        resampled = resampled.drop(_PROV_RANK)

    return _order_columns(resampled)


def resample_quotes_df(df: pl.DataFrame, interval: str, price_type: str = "mid") -> pl.DataFrame:
    """Resample quotes DataFrame using Polars.

    Expects columns: local_ts, bid_px, ask_px, and optionally symbol, source, symbol_raw,
    and the provenance tail a lake-read frame carries.

    The output carries the ``ohlcv_from_quotes`` tail, floored by the worst input's level;
    see :func:`resample_trades_df` for why a frame that states nothing is worse than a
    record that states nothing.
    """
    if len(df) == 0:
        return pl.DataFrame(schema=_EMPTY_BAR_SCHEMA)

    _ns, _sql, polars_str = _parse_interval(interval)
    interval_label = interval.strip().lower()

    tail = provenance_fields("ohlcv_from_quotes")
    df, has_rank = _rank_inputs(df, tail.prov)

    if price_type == "mid":
        df = df.with_columns(((pl.col("bid_px") + pl.col("ask_px")) / 2.0).alias("price"))
    elif price_type == "bid":
        df = df.with_columns(pl.col("bid_px").alias("price"))
    elif price_type == "ask":
        df = df.with_columns(pl.col("ask_px").alias("price"))
    else:
        raise ValueError(f"Unknown price_type: {price_type!r}")

    df_dt = df.with_columns(pl.from_epoch("local_ts", time_unit="ns").alias("datetime")).sort(
        "datetime"
    )

    group_keys = [k for k in ["symbol", "source", "symbol_raw"] if k in df.columns]

    aggregates = [
        pl.col("price").first().alias("open"),
        pl.col("price").max().alias("high"),
        pl.col("price").min().alias("low"),
        pl.col("price").last().alias("close"),
        pl.lit(0.0).alias("volume"),
        pl.col("price").mean().alias("vwap"),
        pl.len().cast(pl.Int64).alias("trade_count"),
    ]
    if has_rank:
        aggregates.append(pl.col(_PROV_RANK).max().alias(_PROV_RANK))

    resampled = df_dt.group_by_dynamic(
        "datetime",
        every=polars_str,
        group_by=group_keys,
        closed="left",
    ).agg(aggregates)

    resampled = resampled.with_columns(
        pl.col("datetime").dt.epoch("ns").alias("bar"),
        pl.lit(interval_label).alias("interval"),
        _emitted_prov(has_rank, tail.prov),
        pl.lit(tail.prov_basis).alias("prov_basis"),
        pl.lit(tail.prov_confidence).alias("prov_confidence"),
    ).drop("datetime")
    if has_rank:
        resampled = resampled.drop(_PROV_RANK)

    return _order_columns(resampled)


_BAR_COUNT_COLUMNS = ("trade_count", "num_trades")
"""How a bar's print count is spelled, this module's own output first.

Both spellings reach :func:`resample_bars_df`. Its own bar frames say ``trade_count``,
which is the equity schema documented in the module docstring; a frame read off the lake
says ``num_trades``, which is what the canonical ``OHLCV`` record and its Parquet column
are called. Looking for ``trade_count`` alone meant a lake-derived frame never matched,
and the fallback then wrote a literal 1 per bar — turning 2 500 prints into 5, in a
column whose whole job is to report how many there were.
"""


_COVERED = "_covered_ns"
_SAMPLED = "_sampled_ns"


def _measure_coverage(
    df_dt: pl.DataFrame, group_keys: list[str], polars_str: str
) -> tuple[pl.DataFrame, bool]:
    """Attach each input bar's *non-overlapping* contribution to its bucket's coverage.

    The frame form of what :func:`_union_coverage` does for records, expressed as the
    classic merge-intervals sweep: sort by start within the bucket, and let each bar
    contribute only the part of its declared span that no earlier bar already covered.
    A day that arrives twice — which is what a lake holding it under both channel tags
    hands back — contributes its width once, so the duplicate cannot raise the emitted
    bar's confidence. Ties are broken by confidence descending, so the instant is scored
    at the best sampling that observed it.

    Returns ``(frame, measurable)``. A frame that declares no ``interval`` states no
    width for its inputs, and there is nothing to divide; the caller emits a null
    confidence rather than the 1.0 that would claim a full bucket.
    """
    if "interval" not in df_dt.columns:
        return df_dt, False
    labels = [str(v) for v in df_dt["interval"].unique().drop_nulls().to_list()]
    if not labels:
        return df_dt, False
    widths = {label: _parse_interval(label)[0] for label in labels}

    # A frame with no prov_confidence makes no sampling claim, and the level fallback in
    # ``_emitted_prov`` reads such a frame as venue-reported; this reads it the same way
    # rather than inventing a second, quieter answer to the same question.
    confidence = (
        pl.col("prov_confidence").fill_null(1.0)
        if "prov_confidence" in df_dt.columns
        else pl.lit(1.0, dtype=pl.Float64)
    )
    df_dt = df_dt.with_columns(
        pl.col("interval").replace_strict(widths, return_dtype=pl.Int64).alias("_width_ns"),
        confidence.cast(pl.Float64).alias("_conf"),
        pl.col("datetime").dt.truncate(polars_str).alias("_bucket"),
    ).with_columns((pl.col("local_ts") + pl.col("_width_ns")).alias("_end_ns"))

    partition = [*group_keys, "_bucket"]
    df_dt = df_dt.sort(
        [*partition, "local_ts", "_conf"],
        descending=[*([False] * len(partition)), False, True],
    ).with_columns(pl.col("_end_ns").cum_max().shift(1).over(partition).alias("_prev_end"))
    return (
        df_dt.with_columns(
            pl.max_horizontal(
                pl.col("_end_ns")
                - pl.max_horizontal(
                    pl.col("local_ts"), pl.col("_prev_end").fill_null(pl.col("local_ts"))
                ),
                pl.lit(0, dtype=pl.Int64),
            ).alias(_COVERED)
        ).with_columns((pl.col(_COVERED) * pl.col("_conf")).alias(_SAMPLED)),
        True,
    )


def resample_bars_df(df: pl.DataFrame, interval: str) -> pl.DataFrame:
    """Resample lower-resolution bars DataFrame into higher-resolution bars using Polars.

    Expects columns: local_ts (or bar), open, high, low, close, volume, and optionally
    vwap, symbol, a print count under either spelling in :data:`_BAR_COUNT_COLUMNS`, and
    the ``interval`` / ``prov`` / ``prov_confidence`` a lake-read bar frame carries.

    A frame carrying neither count column produces a null ``trade_count`` rather than a
    fabricated one: the input did not say how many prints made each bar, and summing
    invented ones answers the question with the bar count instead.

    The output carries the ``ohlcv_from_ohlcv`` tail: ``prov`` floored by the worst
    input's level, and ``prov_confidence`` measured the same way
    :func:`resample_bars_to_bars` measures it — extent times adequacy against the
    tradeable window, over the *union* of the input spans. A frame that declares no
    ``interval`` gets a null confidence, because the width of its inputs is the numerator
    and nothing on the frame supplies it.
    """
    if len(df) == 0:
        return pl.DataFrame(schema=_EMPTY_BAR_SCHEMA)

    interval_ns, _sql, polars_str = _parse_interval(interval)
    interval_label = interval.strip().lower()

    if "local_ts" not in df.columns and "bar" in df.columns:
        df = df.with_columns(pl.col("bar").alias("local_ts"))

    floor = level_for("ohlcv_from_ohlcv")
    df, has_rank = _rank_inputs(df, floor)

    df_dt = df.with_columns(pl.from_epoch("local_ts", time_unit="ns").alias("datetime")).sort(
        "datetime"
    )

    group_keys = [k for k in ["symbol", "source", "symbol_raw"] if k in df.columns]

    if "vwap" not in df.columns:
        df_dt = df_dt.with_columns(pl.col("close").alias("vwap"))

    count_column = next((c for c in _BAR_COUNT_COLUMNS if c in df.columns), None)
    if count_column is None:
        df_dt = df_dt.with_columns(pl.lit(None, dtype=pl.Int64).alias("trade_count"))
    elif count_column != "trade_count":
        df_dt = df_dt.with_columns(pl.col(count_column).alias("trade_count"))

    df_dt, measurable = _measure_coverage(df_dt, group_keys, polars_str)

    aggregates = [
        pl.col("open").first().alias("open"),
        pl.col("high").max().alias("high"),
        pl.col("low").min().alias("low"),
        pl.col("close").last().alias("close"),
        pl.col("volume").sum().alias("volume"),
        pl.when(pl.col("volume").sum() > 0.0)
        .then((pl.col("vwap") * pl.col("volume")).sum() / pl.col("volume").sum())
        .otherwise(None)
        .alias("vwap"),
        # A bucket in which no input bar published a count reports none. Polars sums
        # all-null to 0, and "zero prints made this bar" is a different false claim
        # from the fabricated 1-per-bar this replaced. Matches what
        # ``resample_bars_to_bars`` does with the same input.
        pl.when(pl.col("trade_count").null_count() == pl.len())
        .then(pl.lit(None, dtype=pl.Int64))
        .otherwise(pl.col("trade_count").sum().cast(pl.Int64))
        .alias("trade_count"),
    ]
    if has_rank:
        aggregates.append(pl.col(_PROV_RANK).max().alias(_PROV_RANK))
    if measurable:
        aggregates.append(pl.col(_COVERED).sum().alias(_COVERED))
        aggregates.append(pl.col(_SAMPLED).sum().alias(_SAMPLED))

    resampled = df_dt.group_by_dynamic(
        "datetime",
        every=polars_str,
        group_by=group_keys,
        closed="left",
    ).agg(aggregates)

    tradeable_ns = _tradeable_ns(interval_ns)
    if measurable:
        confidence = pl.min_horizontal(
            pl.col(_COVERED) / tradeable_ns, pl.lit(1.0)
        ) * pl.min_horizontal(pl.col(_SAMPLED) / tradeable_ns, pl.lit(1.0))
    else:
        confidence = pl.lit(None, dtype=pl.Float64)

    resampled = resampled.with_columns(
        pl.col("datetime").dt.epoch("ns").alias("bar"),
        pl.lit(interval_label).alias("interval"),
        _emitted_prov(has_rank, floor),
        pl.lit("ohlcv_from_ohlcv").alias("prov_basis"),
        confidence.cast(pl.Float64).alias("prov_confidence"),
    ).drop("datetime")
    resampled = resampled.drop(
        [c for c in (_PROV_RANK, _COVERED, _SAMPLED) if c in resampled.columns]
    )

    return _order_columns(resampled)
