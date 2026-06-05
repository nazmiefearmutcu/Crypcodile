"""IV surface, vol skew, and term structure analytics (Task 6.4).

Snapshot semantics
------------------
All functions operate at a single instant ``at_ns``.  For each unique
``(strike, expiry, opt_type)`` combination, only the **latest** row with
``local_ts <= at_ns`` is kept.  This produces a clean cross-section of the
options market at that moment, even if rows arrive out-of-order or multiple
exchanges quote the same instrument.

IV source priority
------------------
1. ``mark_iv`` (exchange-provided): used directly when not NULL, ``source="mark_iv"``.
2. ``mark_price`` + ``underlying_price``: if ``mark_iv`` is NULL but both prices
   are present, ``implied_vol`` from the Black-76 solver is called.  On success,
   ``source="computed"``.
3. If neither source can produce a valid vol, ``iv=NULL`` and ``source="unavailable"``.

Moneyness
---------
``moneyness = strike / underlying_price``.  ATM is moneyness == 1.0.

Functions
---------
- ``iv_surface(catalog, underlying, at_ns, rate=0.0) -> pl.DataFrame``
- ``vol_skew(catalog, underlying, expiry_ns, at_ns, rate=0.0) -> pl.DataFrame``
- ``risk_reversal_butterfly(skew_df, target_delta=0.25) -> tuple[float|None, float|None]``
- ``term_structure(catalog, underlying, at_ns, rate=0.0) -> pl.DataFrame``

All catalog-backed functions return ``pl.DataFrame()`` (empty, zero columns) when
no data exists — consistent with the ``resample_ohlcv`` / ``funding_apr`` contract.
"""

from __future__ import annotations

import math

import polars as pl

from crocodile.analytics.blackscholes import bs_greeks, implied_vol
from crocodile.schema.enums import OptType
from crocodile.store.catalog import Catalog

__all__ = [
    "iv_surface",
    "risk_reversal_butterfly",
    "term_structure",
    "vol_skew",
]

# Nanoseconds per year (used to convert expiry_ns - at_ns to t_years)
_NS_PER_YEAR: float = 365.0 * 24.0 * 3600.0 * 1e9

# Nanoseconds per day
_NS_PER_DAY: float = 86_400.0 * 1e9


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _snapshot(raw: pl.DataFrame, at_ns: int) -> pl.DataFrame:
    """Filter to rows with local_ts <= at_ns and keep the latest per key.

    Key = (strike, expiry, opt_type).

    Args:
        raw:    Full scan result from the ``options_chain`` channel.
        at_ns:  Snapshot instant (nanoseconds UTC).

    Returns:
        A DataFrame filtered and deduplicated to the latest snapshot row per
        (strike, expiry, opt_type).  Returns the raw input schema (same columns).
    """
    # Step 1: keep only rows visible at at_ns
    visible = raw.filter(pl.col("local_ts") <= at_ns)
    if len(visible) == 0:
        return visible

    # Step 2: keep the latest local_ts per (strike, expiry, opt_type) group.
    # sort descending by local_ts, then keep first per group.
    visible = visible.sort("local_ts", descending=True)
    visible = visible.unique(
        subset=["strike", "expiry", "opt_type"], keep="first", maintain_order=False
    )
    return visible


def _resolve_iv(
    underlying_price: float | None,
    strike: float,
    expiry: int,
    at_ns: int,
    opt_type_str: str,
    mark_iv: float | None,
    mark_price: float | None,
    rate: float,
) -> tuple[float | None, str]:
    """Resolve an IV value (and its source) for a single options row.

    Priority:
      1. mark_iv (exchange-provided)  → source="mark_iv"
      2. Black-76 solver from mark_price + underlying_price → source="computed"
      3. None                         → source="unavailable"

    Returns:
        (iv, source) where iv is a float or None.
    """
    # --- source 1: exchange mark_iv ---
    if mark_iv is not None and math.isfinite(mark_iv) and mark_iv > 0.0:
        return float(mark_iv), "mark_iv"

    # --- source 2: compute from mark_price ---
    if (
        mark_price is not None
        and math.isfinite(mark_price)
        and mark_price > 0.0
        and underlying_price is not None
        and math.isfinite(underlying_price)
        and underlying_price > 0.0
    ):
        t_years = (expiry - at_ns) / _NS_PER_YEAR
        if t_years > 0.0:
            # Map opt_type_str back to OptType enum
            opt_type: OptType
            if opt_type_str in ("C", OptType.CALL):
                opt_type = OptType.CALL
            else:
                opt_type = OptType.PUT
            iv = implied_vol(
                price=mark_price,
                forward=underlying_price,
                strike=strike,
                t_years=t_years,
                opt_type=opt_type,
                rate=rate,
            )
            if iv is not None:
                return float(iv), "computed"

    return None, "unavailable"


# ---------------------------------------------------------------------------
# iv_surface
# ---------------------------------------------------------------------------


def iv_surface(
    catalog: Catalog,
    underlying: str,
    at_ns: int,
    rate: float = 0.0,
) -> pl.DataFrame:
    """Return the implied-vol surface snapshot at ``at_ns``.

    Queries the ``options_chain`` channel, takes the latest row per
    ``(strike, expiry, opt_type)`` with ``local_ts <= at_ns``, and resolves
    an IV for each row (mark_iv preferred; computed from mark_price as fallback).

    Args:
        catalog:    A :class:`~crocodile.store.catalog.Catalog` instance.
        underlying: Underlying asset identifier, e.g. ``"BTC"``.
        at_ns:      Snapshot instant (nanoseconds UTC).
        rate:       Continuous risk-free rate used in the IV solver (default 0.0).

    Returns:
        A Polars DataFrame with columns:

        =========  =========  =======================================================
        expiry     Int64      Expiry timestamp (nanoseconds UTC).
        strike     Float64    Option strike.
        moneyness  Float64    ``strike / underlying_price``.
        opt_type   Utf8       ``"C"`` or ``"P"`` (matching ``OptType`` enum values).
        iv         Float64    Implied volatility (NULL when unavailable).
        source     Utf8       One of ``"mark_iv"``, ``"computed"``, ``"unavailable"``.
        =========  =========  =======================================================

        Returns ``pl.DataFrame()`` when no data exists.
    """
    # Scan the options_chain channel; we need all rows for this underlying.
    # The catalog.scan requires a specific symbol; we use catalog.query instead
    # to filter by underlying (which is a data column, not a partition key).
    catalog.refresh_views()
    conn = catalog._conn

    try:
        # underlying is a column value — use a parameterized query to avoid injection.
        result = conn.execute(
            "SELECT * FROM options_chain WHERE underlying = ? ORDER BY local_ts",
            [underlying],
        )
        raw: pl.DataFrame = result.pl()
    except Exception:
        return pl.DataFrame()

    if len(raw) == 0:
        return pl.DataFrame()

    # Apply snapshot filter: latest row per (strike, expiry, opt_type) at at_ns.
    snap = _snapshot(raw, at_ns)
    if len(snap) == 0:
        return pl.DataFrame()

    # Build output rows.
    out_expiry: list[int] = []
    out_strike: list[float] = []
    out_moneyness: list[float] = []
    out_opt_type: list[str] = []
    out_iv: list[float | None] = []
    out_source: list[str] = []

    for row in snap.iter_rows(named=True):
        strike: float = float(row["strike"])
        expiry: int = int(row["expiry"])
        opt_type_str: str = str(row["opt_type"])
        underlying_price: float | None = (
            float(row["underlying_price"])
            if row.get("underlying_price") is not None
            else None
        )
        mark_iv_val: float | None = (
            float(row["mark_iv"])
            if row.get("mark_iv") is not None
            else None
        )
        mark_price_val: float | None = (
            float(row["mark_price"])
            if row.get("mark_price") is not None
            else None
        )

        iv, source = _resolve_iv(
            underlying_price=underlying_price,
            strike=strike,
            expiry=expiry,
            at_ns=at_ns,
            opt_type_str=opt_type_str,
            mark_iv=mark_iv_val,
            mark_price=mark_price_val,
            rate=rate,
        )

        moneyness: float = (
            strike / underlying_price
            if underlying_price is not None and underlying_price > 0.0
            else float("nan")
        )

        out_expiry.append(expiry)
        out_strike.append(strike)
        out_moneyness.append(moneyness)
        out_opt_type.append(opt_type_str)
        out_iv.append(iv)
        out_source.append(source)

    return pl.DataFrame(
        {
            "expiry": pl.Series(out_expiry, dtype=pl.Int64),
            "strike": pl.Series(out_strike, dtype=pl.Float64),
            "moneyness": pl.Series(out_moneyness, dtype=pl.Float64),
            "opt_type": pl.Series(out_opt_type, dtype=pl.Utf8),
            "iv": pl.Series(out_iv, dtype=pl.Float64),
            "source": pl.Series(out_source, dtype=pl.Utf8),
        }
    )


# ---------------------------------------------------------------------------
# vol_skew
# ---------------------------------------------------------------------------


def vol_skew(
    catalog: Catalog,
    underlying: str,
    expiry_ns: int,
    at_ns: int,
    rate: float = 0.0,
) -> pl.DataFrame:
    """Return per-strike IV and delta for a single expiry, ordered by strike.

    Args:
        catalog:    A :class:`~crocodile.store.catalog.Catalog` instance.
        underlying: Underlying asset identifier.
        expiry_ns:  Expiry filter (nanoseconds UTC).
        at_ns:      Snapshot instant (nanoseconds UTC).
        rate:       Continuous risk-free rate (default 0.0).

    Returns:
        A Polars DataFrame with columns:

        =========  =========  =======================================================
        strike     Float64    Option strike.
        moneyness  Float64    ``strike / underlying_price``.
        opt_type   Utf8       ``"C"`` or ``"P"``.
        iv         Float64    Implied volatility (NULL when unavailable).
        delta      Float64    Option delta (NULL when iv is unavailable).
        =========  =========  =======================================================

        Ordered by ``strike`` ascending.  Returns ``pl.DataFrame()`` when empty.
    """
    # Get the full surface snapshot first.
    surface = iv_surface(catalog, underlying, at_ns, rate=rate)
    if len(surface) == 0:
        return pl.DataFrame()

    # Filter to the requested expiry.
    skew = surface.filter(pl.col("expiry") == expiry_ns)
    if len(skew) == 0:
        return pl.DataFrame()

    # We also need underlying_price to compute delta.  Re-fetch it from the
    # catalog snapshot so we don't re-query the surface again.
    catalog.refresh_views()
    conn = catalog._conn

    underlying_price: float | None = None
    try:
        res = conn.execute(
            "SELECT underlying_price FROM options_chain "
            "WHERE underlying = ? AND expiry = ? AND local_ts <= ? "
            "ORDER BY local_ts DESC LIMIT 1",
            [underlying, expiry_ns, at_ns],
        )
        rows = res.fetchall()
        if rows and rows[0][0] is not None:
            underlying_price = float(rows[0][0])
    except Exception:
        pass

    t_years = (expiry_ns - at_ns) / _NS_PER_YEAR

    # Build output with delta column.
    out_strike: list[float] = []
    out_moneyness: list[float] = []
    out_opt_type: list[str] = []
    out_iv: list[float | None] = []
    out_delta: list[float | None] = []

    for row in skew.sort("strike").iter_rows(named=True):
        strike = float(row["strike"])
        moneyness = float(row["moneyness"])
        opt_type_str = str(row["opt_type"])
        iv = row["iv"]  # float or None

        delta: float | None = None
        if iv is not None and underlying_price is not None and t_years > 0.0:
            _is_call = opt_type_str in ("C", str(OptType.CALL))
            opt_type_enum = OptType.CALL if _is_call else OptType.PUT
            try:
                greeks = bs_greeks(
                    forward=underlying_price,
                    strike=strike,
                    t_years=t_years,
                    vol=iv,
                    opt_type=opt_type_enum,
                    rate=rate,
                )
                delta = greeks.delta
            except Exception:
                pass

        out_strike.append(strike)
        out_moneyness.append(moneyness)
        out_opt_type.append(opt_type_str)
        out_iv.append(iv)
        out_delta.append(delta)

    if not out_strike:
        return pl.DataFrame()

    return pl.DataFrame(
        {
            "strike": pl.Series(out_strike, dtype=pl.Float64),
            "moneyness": pl.Series(out_moneyness, dtype=pl.Float64),
            "opt_type": pl.Series(out_opt_type, dtype=pl.Utf8),
            "iv": pl.Series(out_iv, dtype=pl.Float64),
            "delta": pl.Series(out_delta, dtype=pl.Float64),
        }
    )


# ---------------------------------------------------------------------------
# risk_reversal_butterfly
# ---------------------------------------------------------------------------


def risk_reversal_butterfly(
    skew_df: pl.DataFrame,
    target_delta: float = 0.25,
) -> tuple[float | None, float | None]:
    """Compute the 25-delta risk reversal and butterfly from a skew DataFrame.

    Uses the call with delta nearest to ``+target_delta`` and the put with delta
    nearest to ``-target_delta``.  ATM is taken as the option whose
    ``|delta| - 0.5`` is smallest (or, as a fallback, ``moneyness`` nearest 1.0).

    Formulas:
    - ``rr = iv(call @ +target_delta) - iv(put @ -target_delta)``
    - ``bf = mean(iv_call_target, iv_put_target) - atm_iv``

    Args:
        skew_df:      Output of :func:`vol_skew` for a single expiry.
        target_delta: Target absolute delta for RR/BF (default 0.25).

    Returns:
        ``(rr, bf)`` where each element is a float or ``None`` if the
        required options cannot be found.
    """
    if len(skew_df) == 0:
        return None, None

    required_cols = {"iv", "delta", "opt_type"}
    if not required_cols.issubset(set(skew_df.columns)):
        return None, None

    # Split into calls and puts.
    call_rows = [
        row
        for row in skew_df.iter_rows(named=True)
        if str(row["opt_type"]) in ("C", str(OptType.CALL))
        and row["iv"] is not None
        and row["delta"] is not None
    ]
    put_rows = [
        row
        for row in skew_df.iter_rows(named=True)
        if str(row["opt_type"]) in ("P", str(OptType.PUT))
        and row["iv"] is not None
        and row["delta"] is not None
    ]

    # Find call nearest to +target_delta.
    best_call = _nearest_delta_row(call_rows, target_delta)
    # Find put nearest to -target_delta (put deltas are negative).
    best_put = _nearest_delta_row(put_rows, -target_delta)

    if best_call is None or best_put is None:
        return None, None

    iv_call = float(best_call["iv"])  # type: ignore[arg-type]
    iv_put = float(best_put["iv"])  # type: ignore[arg-type]

    # ATM: option with |delta| closest to 0.5 across all rows, or moneyness→1.
    all_rows = [
        row
        for row in skew_df.iter_rows(named=True)
        if row["iv"] is not None and row["delta"] is not None
    ]
    atm_iv = _atm_iv(skew_df, all_rows)
    if atm_iv is None:
        return None, None

    rr = iv_call - iv_put
    bf = 0.5 * (iv_call + iv_put) - atm_iv
    return rr, bf


def _nearest_delta_row(
    rows: list[dict[str, object]],
    target: float,
) -> dict[str, object] | None:
    """Return the row whose ``delta`` is nearest to ``target``, or None."""
    if not rows:
        return None
    return min(rows, key=lambda r: abs(float(r["delta"]) - target))  # type: ignore[arg-type]


def _atm_iv(
    skew_df: pl.DataFrame,
    all_rows: list[dict[str, object]],
) -> float | None:
    """Find ATM IV: option whose |delta| is closest to 0.5 (call) / -0.5 equivalently.

    Falls back to moneyness nearest 1.0 when delta is not available.
    """
    if not all_rows:
        return None

    # Primary: nearest |delta| to 0.5
    if all(r["delta"] is not None for r in all_rows):
        atm_row = min(all_rows, key=lambda r: abs(abs(float(r["delta"])) - 0.5))  # type: ignore[arg-type]
        return float(atm_row["iv"])  # type: ignore[arg-type]

    # Fallback: moneyness nearest 1.0
    if "moneyness" in skew_df.columns:
        moneyness_rows = [
            r for r in all_rows if r.get("moneyness") is not None
        ]
        if moneyness_rows:
            atm_row = min(moneyness_rows, key=lambda r: abs(float(r["moneyness"]) - 1.0))  # type: ignore[arg-type]
            return float(atm_row["iv"])  # type: ignore[arg-type]

    return None


# ---------------------------------------------------------------------------
# term_structure
# ---------------------------------------------------------------------------


def term_structure(
    catalog: Catalog,
    underlying: str,
    at_ns: int,
    rate: float = 0.0,
) -> pl.DataFrame:
    """Return the ATM IV term structure at ``at_ns``.

    For each expiry in the IV surface, picks the strike nearest to
    ``underlying_price`` (ATM strike) and returns its IV.

    Args:
        catalog:    A :class:`~crocodile.store.catalog.Catalog` instance.
        underlying: Underlying asset identifier.
        at_ns:      Snapshot instant (nanoseconds UTC).
        rate:       Continuous risk-free rate (default 0.0).

    Returns:
        A Polars DataFrame ordered by ``expiry`` ascending with columns:

        ===============  =========  ==============================================
        expiry           Int64      Expiry timestamp (nanoseconds UTC).
        days_to_expiry   Float64    Days from ``at_ns`` to ``expiry``.
        atm_strike       Float64    Strike nearest to ``underlying_price``.
        atm_iv           Float64    IV at the ATM strike (NULL if unavailable).
        ===============  =========  ==============================================

        Returns ``pl.DataFrame()`` when no data exists.
    """
    surface = iv_surface(catalog, underlying, at_ns, rate=rate)
    if len(surface) == 0:
        return pl.DataFrame()

    # We need underlying_price to identify ATM. Re-read it from the catalog.
    catalog.refresh_views()
    conn = catalog._conn

    underlying_price: float | None = None
    try:
        res = conn.execute(
            "SELECT underlying_price FROM options_chain "
            "WHERE underlying = ? AND local_ts <= ? "
            "ORDER BY local_ts DESC LIMIT 1",
            [underlying, at_ns],
        )
        row = res.fetchone()
        if row and row[0] is not None:
            underlying_price = float(row[0])
    except Exception:
        pass

    if underlying_price is None:
        # Try to derive from moneyness (moneyness = strike / underlying_price)
        # If we have moneyness and strike we could back out underlying_price,
        # but that's fragile. Fall back to moneyness nearest 1.0 as proxy for ATM.
        underlying_price = None  # will use moneyness proxy below

    # Group surface rows by expiry.
    expiries: list[int] = sorted(set(surface["expiry"].to_list()))

    out_expiry: list[int] = []
    out_days: list[float] = []
    out_atm_strike: list[float] = []
    out_atm_iv: list[float | None] = []

    for expiry in expiries:
        expiry_rows = surface.filter(pl.col("expiry") == expiry)
        if len(expiry_rows) == 0:
            continue

        days_to_expiry = (expiry - at_ns) / _NS_PER_DAY

        # Identify ATM strike: nearest to underlying_price (or moneyness → 1.0).
        if underlying_price is not None:
            _up = underlying_price  # capture for lambda
            best_row = min(
                expiry_rows.iter_rows(named=True),
                key=lambda r, up=_up: abs(float(r["strike"]) - up),  # type: ignore[misc]
            )
        else:
            # Fallback: strike nearest to moneyness=1.0
            best_row = min(
                expiry_rows.iter_rows(named=True),
                key=lambda r: abs(float(r["moneyness"]) - 1.0),
            )

        atm_strike = float(best_row["strike"])
        atm_iv = best_row["iv"]  # float or None

        out_expiry.append(expiry)
        out_days.append(days_to_expiry)
        out_atm_strike.append(atm_strike)
        out_atm_iv.append(float(atm_iv) if atm_iv is not None else None)

    if not out_expiry:
        return pl.DataFrame()

    return pl.DataFrame(
        {
            "expiry": pl.Series(out_expiry, dtype=pl.Int64),
            "days_to_expiry": pl.Series(out_days, dtype=pl.Float64),
            "atm_strike": pl.Series(out_atm_strike, dtype=pl.Float64),
            "atm_iv": pl.Series(out_atm_iv, dtype=pl.Float64),
        }
    ).sort("expiry")
