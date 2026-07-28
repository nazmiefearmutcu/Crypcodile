"""The implied-volatility surface, once, for whichever market supplied the chain.

Both halves of this product read the same channel. ``options_chain`` is one record type
with one set of columns, written by a Deribit connector and by the Yahoo provider alike,
and the four capabilities built on it — ``iv-surface``, ``term-structure``, ``vol-skew``
and ``risk-reversal`` — ask it the same four questions. What differs between the two
markets is two functions and nothing else:

* **which price gets inverted when the venue publishes no vol.** Deribit publishes a
  ``mark_price``, so the crypto half inverts Black-76 on it. Yahoo publishes a bid and an
  ask and no mark at all, so the equity half inverts Black-Scholes-Merton on their mid.
* **which model prices the delta** that ``vol-skew`` reports and ``risk-reversal`` searches
  over — the same split, for the same reason.

Everything else — the snapshot rule, the SABR/spline fit, the moneyness, the ATM search,
the risk reversal and butterfly, the empty-frame contract, the column names and their
dtypes — is arithmetic over columns, and arithmetic does not know which market it came
from. So it lives here and the two halves pass in an :class:`OptionsModel`.

The alternative was a second copy under ``equity/analytics``, and the merge this module
belongs to exists because of what that costs: ``crypto/analytics/slippage.py`` and
``equity/analytics/slippage.py`` were a fork of one function that drifted apart at both
ends, and neither was a superset of the other by the time anyone looked. A surface built
by one copy and a skew built by the other would disagree about which strike is ATM long
before anyone noticed they were meant to be the same table.

Every catalog-backed function returns ``pl.DataFrame()`` — empty, zero columns — when no
data exists, which is the contract ``resample_ohlcv`` and ``funding_apr`` already keep.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping
from typing import TYPE_CHECKING, Any, Final, NamedTuple, Protocol

import duckdb
import numpy as np
import polars as pl

from crocodile.core.schema.enums import OptType

if TYPE_CHECKING:  # pragma: no cover - annotations only
    import numpy.typing as npt

    from crocodile.core.store.catalog import Catalog

__all__ = [
    "ChainPrices",
    "CubicSpline",
    "DeltaModel",
    "ImpliedVolSolver",
    "OptionsModel",
    "calibrate_sabr",
    "fit_volatility_skew",
    "iv_surface",
    "line_search",
    "risk_reversal_butterfly",
    "sabr_vol",
    "term_structure",
    "vol_skew",
]

_NS_PER_YEAR: float = 365.0 * 24.0 * 3600.0 * 1e9
"""Nanoseconds per year, for turning ``expiry - at_ns`` into the solver's ``t_years``."""

_NS_PER_DAY: float = 86_400.0 * 1e9
"""Nanoseconds per day, for ``term_structure``'s ``days_to_expiry`` column."""


# ---------------------------------------------------------------------------
# What one asset class has to supply
# ---------------------------------------------------------------------------


class ChainPrices(NamedTuple):
    """Every price one chain row carries that an IV solver could invert.

    All five fields, not the two either half happens to use, because the *point* of the
    split is that the two halves disagree about which price is the mark: crypto reads
    ``mark_price``, equity reads the mid of ``bid_px`` and ``ask_px``. A per-asset-class
    projection of the row would put that disagreement in two places — the projection and
    the solver — and a solver that wanted a price its projection did not carry would fail
    by receiving ``None``, which is indistinguishable here from a venue that quoted
    nothing.

    Not named ``OptionQuote``, which is the obvious spelling and is a retired one:
    ``OptionQuote`` was equity's name for the record ``OptionsChain`` is now, and
    ``tests/conformance/test_phase1_exit.py`` pins it as deliberately dropped so that a
    name the merge removed cannot quietly come back meaning something else. This is a
    handful of prices lifted off a row, not a record — reusing the record's old name for
    it is exactly the confusion that pin exists to catch.
    """

    mark_iv: float | None
    mark_price: float | None
    bid_px: float | None
    ask_px: float | None
    last_price: float | None


class ImpliedVolSolver(Protocol):
    """Resolve one row's implied vol, and name the path that produced it.

    The returned string lands in the surface's ``source`` column, so a caller can tell a
    venue-published vol from one this engine solved for without reading the declaration.
    Both halves speak the same three words — ``mark_iv``, ``computed``, ``unavailable`` —
    because a column whose vocabulary changes with the asset class is a column no shared
    consumer can filter on.
    """

    def __call__(
        self,
        *,
        quote: ChainPrices,
        underlying_price: float | None,
        strike: float,
        t_years: float,
        opt_type: OptType,
        rate: float,
    ) -> tuple[float | None, str]: ...


class DeltaModel(Protocol):
    """Price the delta of one contract at a solved vol.

    ``None`` for a contract this model cannot price — an expired one, a zero vol — rather
    than a zero, because ``vol-skew`` publishes the column and ``risk-reversal`` searches
    it for the nearest 25-delta strike. A fabricated 0.0 would be the nearest thing to
    every negative target and would win the search.
    """

    def __call__(
        self,
        *,
        underlying_price: float,
        strike: float,
        t_years: float,
        vol: float,
        opt_type: OptType,
        rate: float,
    ) -> float | None: ...


class OptionsModel(NamedTuple):
    """One market's pair: how it solves a vol, and how it prices a delta."""

    solve_iv: ImpliedVolSolver
    delta: DeltaModel


# ---------------------------------------------------------------------------
# Calibration math
# ---------------------------------------------------------------------------


class CubicSpline:
    """Natural cubic spline interpolation in pure Python/NumPy."""

    def __init__(self, x: list[float], y: list[float]) -> None:
        self.x: npt.NDArray[np.float64] = np.array(x, dtype=float)
        self.y: npt.NDArray[np.float64] = np.array(y, dtype=float)
        n = len(x) - 1
        h = np.diff(self.x)

        # Tridiagonal system for the second derivatives, natural boundary conditions.
        a: npt.NDArray[np.float64] = np.zeros((n + 1, n + 1))
        b: npt.NDArray[np.float64] = np.zeros(n + 1)

        a[0, 0] = 1.0
        a[n, n] = 1.0

        for i in range(1, n):
            a[i, i - 1] = h[i - 1] / 6.0
            a[i, i] = (h[i - 1] + h[i]) / 3.0
            a[i, i + 1] = h[i] / 6.0
            b[i] = (self.y[i + 1] - self.y[i]) / h[i] - (self.y[i] - self.y[i - 1]) / h[i - 1]

        self.m: npt.NDArray[np.floating[Any]] = np.linalg.solve(a, b)

    def __call__(self, val: float) -> float:
        if val <= self.x[0]:
            return float(self.y[0])
        if val >= self.x[-1]:
            return float(self.y[-1])

        idx = int(np.searchsorted(self.x, val)) - 1
        h = self.x[idx + 1] - self.x[idx]

        tmp1 = self.m[idx] * (self.x[idx + 1] - val) ** 3 / (6.0 * h)
        tmp2 = self.m[idx + 1] * (val - self.x[idx]) ** 3 / (6.0 * h)
        tmp3 = (self.y[idx] - self.m[idx] * h**2 / 6.0) * (self.x[idx + 1] - val) / h
        tmp4 = (self.y[idx + 1] - self.m[idx + 1] * h**2 / 6.0) * (val - self.x[idx]) / h

        return float(tmp1 + tmp2 + tmp3 + tmp4)


def sabr_vol(
    k: float,
    f: float,
    t: float,
    alpha: float,
    beta: float,
    rho: float,
    nu: float,
) -> float:
    """Standard SABR implied volatility approximation formula."""
    if f <= 0.0 or k <= 0.0:
        return 0.0
    if abs(f - k) < 1e-6:
        # ATM formula
        one_minus_beta = 1.0 - beta
        num = alpha
        den = f**one_minus_beta
        factor = 1.0 + (
            (one_minus_beta**2 / 24.0) * (alpha**2 / (f ** (2.0 * one_minus_beta)))
            + (0.25 * rho * beta * nu * alpha / (f**one_minus_beta))
            + ((2.0 - 3.0 * rho**2) / 24.0) * (nu**2)
        ) * t
        return float((num / den) * factor)

    one_minus_beta = 1.0 - beta
    log_fk = math.log(f / k)
    fk_power = (f * k) ** (one_minus_beta / 2.0)

    z = (nu / alpha) * fk_power * log_fk

    # x(z) definition
    temp = 1.0 - 2.0 * rho * z + z * z
    val = 0.0 if temp <= 0.0 else math.sqrt(temp)

    num_xz = val + z - rho
    if num_xz <= 0.0 or abs(1.0 - rho) < 1e-9:
        xz = z
    else:
        xz = math.log(num_xz / (1.0 - rho))

    den_base = fk_power * (
        1.0
        + (one_minus_beta**2 / 24.0) * (log_fk**2)
        + (one_minus_beta**4 / 1920.0) * (log_fk**4)
    )
    z_over_xz = 1.0 if abs(xz) < 1e-6 else (z / xz)

    term3 = 1.0 + (
        (one_minus_beta**2 / 24.0) * (alpha**2 / ((f * k) ** one_minus_beta))
        + (0.25 * rho * beta * nu * alpha / fk_power)
        + ((2.0 - 3.0 * rho**2) / 24.0) * (nu**2)
    ) * t

    return float((alpha / den_base) * z_over_xz * term3)


class _Objective(Protocol):
    """A one-parameter loss, which is all :func:`line_search` needs to know about it."""

    def __call__(self, value: float, /) -> float: ...


def line_search(func: _Objective, low: float, high: float, steps: int = 30) -> float:
    """1D grid optimizer helper for parameter calibration."""
    best_val = low
    best_loss = float("inf")
    for i in range(steps + 1):
        val = low + (high - low) * (i / steps)
        loss = func(val)
        if loss < best_loss:
            best_loss = loss
            best_val = val
    return best_val


def calibrate_sabr(
    strikes: list[float],
    ivs: list[float | None],
    forward: float,
    t_years: float,
    beta: float = 0.5,
) -> tuple[float, float, float]:
    """Calibrate SABR model parameters (alpha, rho, nu) to the given IV skew."""
    valid_data = [
        (k, iv)
        for k, iv in zip(strikes, ivs, strict=False)
        if iv is not None and math.isfinite(iv) and iv > 0.0
    ]
    if len(valid_data) < 2:
        return 0.4 * (forward ** (1.0 - beta)), 0.0, 0.1

    closest_idx = min(range(len(valid_data)), key=lambda i: abs(valid_data[i][0] - forward))
    atm_iv = valid_data[closest_idx][1]

    # Initial guesses
    alpha = atm_iv * (forward ** (1.0 - beta))
    rho = 0.0
    nu = 0.5

    def loss_func(a: float, r: float, n: float) -> float:
        loss = 0.0
        for k, iv in valid_data:
            model_iv = sabr_vol(k, forward, t_years, a, beta, r, n)
            loss += (model_iv - iv) ** 2
        return loss

    # Coordinate descent over the three free parameters. Each lambda binds the other two
    # coordinates as defaults rather than closing over them: `line_search` calls it before
    # the next assignment lands, so the two spellings compute the same number — but only
    # the bound one says at the call site which iterate is being held fixed.
    for _ in range(5):
        alpha = line_search(lambda a, r=rho, n=nu: loss_func(a, r, n), 1e-3, 5.0, steps=20)
        rho = line_search(lambda r, a=alpha, n=nu: loss_func(a, r, n), -0.95, 0.95, steps=20)
        nu = line_search(lambda n, a=alpha, r=rho: loss_func(a, r, n), 1e-3, 5.0, steps=20)

    return alpha, rho, nu


def fit_volatility_skew(
    strikes: list[float],
    ivs: list[float | None],
    forward: float,
    t_years: float,
    target_strikes: list[float],
    method: str = "sabr",
) -> list[float | None]:
    """Fit a volatility skew model to market IVs and interpolate at target strikes."""
    valid_data = [
        (k, iv)
        for k, iv in zip(strikes, ivs, strict=False)
        if iv is not None and math.isfinite(iv) and iv > 0.0
    ]
    if not valid_data or t_years <= 0.0:
        return [None] * len(target_strikes)

    valid_strikes = [d[0] for d in valid_data]
    valid_ivs = [d[1] for d in valid_data]

    if method.lower() == "sabr":
        if len(valid_data) < 2:
            return [valid_data[0][1]] * len(target_strikes)

        alpha, rho, nu = calibrate_sabr(valid_strikes, list(valid_ivs), forward, t_years)

        fitted: list[float | None] = []
        for k in target_strikes:
            try:
                fitted.append(sabr_vol(k, forward, t_years, alpha, 0.5, rho, nu))
            except Exception:
                fitted.append(None)
        return fitted

    if method.lower() == "spline":
        if len(valid_data) < 3:
            return [valid_data[0][1]] * len(target_strikes)

        sorted_valid = sorted(zip(valid_strikes, valid_ivs, strict=False), key=lambda x: x[0])
        unique_strikes: list[float] = []
        unique_ivs: list[float] = []
        for s, iv in sorted_valid:
            if not unique_strikes or unique_strikes[-1] != s:
                unique_strikes.append(s)
                unique_ivs.append(iv)

        if len(unique_strikes) < 3:
            return [unique_ivs[0]] * len(target_strikes)

        try:
            spline = CubicSpline(unique_strikes, unique_ivs)
            return [spline(k) for k in target_strikes]
        except Exception:
            return [None] * len(target_strikes)

    return [valid_data[0][1]] * len(target_strikes)


# ---------------------------------------------------------------------------
# Reading the chain
# ---------------------------------------------------------------------------


def _snapshot(raw: pl.DataFrame, at_ns: int) -> pl.DataFrame:
    """Filter to rows with ``local_ts <= at_ns`` and keep the latest per key.

    Key = ``(strike, expiry, opt_type)``. That is what turns a log of quote updates into a
    cross-section: rows arrive out of order and two venues may quote one instrument, and a
    surface built from every row would plot the same strike several times at several
    instants.
    """
    visible = raw.filter(pl.col("local_ts") <= at_ns)
    if len(visible) == 0:
        return visible

    visible = visible.sort("local_ts", descending=True)
    return visible.unique(
        subset=["strike", "expiry", "opt_type"], keep="first", maintain_order=False
    )


def _scan_chain(catalog: Catalog, underlying: str) -> pl.DataFrame:
    """Every stored ``options_chain`` row for ``underlying``, or an empty frame.

    ``catalog.query`` is not used: this needs a *parameterised* statement, because
    ``underlying`` is a data column rather than a partition key and interpolating a
    caller's string into SQL is the injection the parameter list exists to refuse. The
    two DuckDB errors caught are the shapes an absent view and an unreadable lake take;
    anything else propagates, which is a T6 regression the crypto tests pin.
    """
    catalog.refresh_views()
    try:
        result = catalog.connection.execute(
            "SELECT * FROM options_chain WHERE UPPER(underlying) = UPPER(?) ORDER BY local_ts",
            [underlying],
        )
        raw: pl.DataFrame = result.pl()
    except (duckdb.CatalogException, duckdb.IOException):
        return pl.DataFrame()
    return raw


def _quote(row: dict[str, Any]) -> ChainPrices:
    """Read the five prices off one chain row, coercing a stored null to ``None``."""
    return ChainPrices(
        mark_iv=_as_float(row.get("mark_iv")),
        mark_price=_as_float(row.get("mark_price")),
        bid_px=_as_float(row.get("bid_px")),
        ask_px=_as_float(row.get("ask_px")),
        last_price=_as_float(row.get("last_price")),
    )


def _as_float(value: Any) -> float | None:
    return None if value is None else float(value)


def _opt_type(spelling: str) -> OptType:
    """Map the stored ``opt_type`` cell back to the enum, defaulting to a put.

    ``OptType`` is a ``StrEnum`` whose call value *is* ``"C"``/``"P"``, so the comparison
    is against one string rather than two spellings; anything that is not a call is a put,
    which is the two-valued fact the record's own enum encodes.
    """
    return OptType.CALL if spelling == OptType.CALL else OptType.PUT


_LATEST_SPOT: Final = (
    "SELECT underlying_price FROM options_chain "
    "WHERE UPPER(underlying) = UPPER(?) AND local_ts <= ? "
    "ORDER BY local_ts DESC LIMIT 1"
)
"""The whole statement as one literal, and its expiry-scoped twin below.

Written out twice rather than assembled from a base plus a predicate. The two differ by
one clause and one bound parameter, and building them by concatenation is the shape a
scanner cannot distinguish from an injection however careful the pieces are — the
parameters are what make this safe, and a reader should be able to see both statements
whole to check that.
"""

_LATEST_SPOT_FOR_EXPIRY: Final = (
    "SELECT underlying_price FROM options_chain "
    "WHERE UPPER(underlying) = UPPER(?) AND expiry = ? AND local_ts <= ? "
    "ORDER BY local_ts DESC LIMIT 1"
)


def _underlying_price_at(
    catalog: Catalog, underlying: str, at_ns: int, expiry_ns: int | None = None
) -> float | None:
    """The latest ``underlying_price`` this chain carries at ``at_ns``, or ``None``.

    Re-read from the lake rather than taken off the surface frame, because the surface
    reports ``moneyness`` and not the price it divided by — and back-solving the price out
    of ``strike / moneyness`` would reintroduce the rounding the division just lost.
    """
    catalog.refresh_views()
    try:
        if expiry_ns is None:
            row = catalog.connection.execute(_LATEST_SPOT, [underlying, at_ns]).fetchone()
        else:
            row = catalog.connection.execute(
                _LATEST_SPOT_FOR_EXPIRY, [underlying, expiry_ns, at_ns]
            ).fetchone()
    except (duckdb.CatalogException, duckdb.IOException):
        return None
    if row and row[0] is not None:
        return float(row[0])
    return None


# ---------------------------------------------------------------------------
# iv_surface
# ---------------------------------------------------------------------------


def iv_surface(
    catalog: Catalog,
    underlying: str,
    at_ns: int,
    *,
    model: OptionsModel,
    rate: float = 0.0,
    fit_method: str = "sabr",
) -> pl.DataFrame:
    """Return the implied-vol surface snapshot at ``at_ns``.

    Takes the latest row per ``(strike, expiry, opt_type)`` with ``local_ts <= at_ns`` and
    resolves an IV for each through ``model.solve_iv``, then fits a smooth skew per expiry.

    Columns: ``expiry`` (Int64), ``strike``, ``moneyness``, ``opt_type`` (Utf8), ``iv``,
    ``fitted_iv`` and ``source`` (Utf8). ``moneyness`` is ``strike / underlying_price``,
    and ``NaN`` where the chain carries no underlying price — a hole, not a strike that
    happens to sit at zero.

    The per-expiry fit is skipped, rather than run against an invented forward, when no row
    of that expiry carries an underlying price. A SABR calibration seeded on a made-up
    forward returns numbers, and they would land in ``fitted_iv`` beside the honest ones
    with nothing to tell them apart.
    """
    raw = _scan_chain(catalog, underlying)
    if len(raw) == 0:
        return pl.DataFrame()

    snap = _snapshot(raw, at_ns)
    if len(snap) == 0:
        return pl.DataFrame()

    fitted_iv_map: dict[tuple[int, float, str], float | None] = {}
    for expiry in set(snap["expiry"].to_list()):
        exp_df = snap.filter(pl.col("expiry") == expiry)
        if len(exp_df) == 0:
            continue

        underlying_prices = [
            p
            for p in exp_df["underlying_price"].to_list()
            if p is not None and math.isfinite(p) and p > 0.0
        ]
        if not underlying_prices:
            continue
        forward = float(underlying_prices[0])
        t_years = (expiry - at_ns) / _NS_PER_YEAR

        strikes: list[float] = []
        ivs: list[float | None] = []
        keys: list[tuple[float, str]] = []
        for row in exp_df.iter_rows(named=True):
            strike = float(row["strike"])
            opt_type_str = str(row["opt_type"])
            iv, _ = model.solve_iv(
                quote=_quote(row),
                underlying_price=forward,
                strike=strike,
                t_years=t_years,
                opt_type=_opt_type(opt_type_str),
                rate=rate,
            )
            strikes.append(strike)
            ivs.append(iv)
            keys.append((strike, opt_type_str))

        fitted_vols = fit_volatility_skew(
            strikes, ivs, forward, t_years, strikes, method=fit_method
        )
        for (strike, opt_type_str), fitted in zip(keys, fitted_vols, strict=False):
            fitted_iv_map[(int(expiry), strike, opt_type_str)] = fitted

    out_expiry: list[int] = []
    out_strike: list[float] = []
    out_moneyness: list[float] = []
    out_opt_type: list[str] = []
    out_iv: list[float | None] = []
    out_fitted_iv: list[float | None] = []
    out_source: list[str] = []

    for row in snap.iter_rows(named=True):
        strike = float(row["strike"])
        expiry_ns = int(row["expiry"])
        opt_type_str = str(row["opt_type"])
        underlying_price = _as_float(row.get("underlying_price"))

        iv, source = model.solve_iv(
            quote=_quote(row),
            underlying_price=underlying_price,
            strike=strike,
            t_years=(expiry_ns - at_ns) / _NS_PER_YEAR,
            opt_type=_opt_type(opt_type_str),
            rate=rate,
        )
        moneyness = (
            strike / underlying_price
            if underlying_price is not None and underlying_price > 0.0
            else float("nan")
        )

        out_expiry.append(expiry_ns)
        out_strike.append(strike)
        out_moneyness.append(moneyness)
        out_opt_type.append(opt_type_str)
        out_iv.append(iv)
        out_fitted_iv.append(fitted_iv_map.get((expiry_ns, strike, opt_type_str)))
        out_source.append(source)

    return pl.DataFrame(
        {
            "expiry": pl.Series(out_expiry, dtype=pl.Int64),
            "strike": pl.Series(out_strike, dtype=pl.Float64),
            "moneyness": pl.Series(out_moneyness, dtype=pl.Float64),
            "opt_type": pl.Series(out_opt_type, dtype=pl.Utf8),
            "iv": pl.Series(out_iv, dtype=pl.Float64),
            "fitted_iv": pl.Series(out_fitted_iv, dtype=pl.Float64),
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
    *,
    model: OptionsModel,
    rate: float = 0.0,
    fit_method: str = "sabr",
) -> pl.DataFrame:
    """Per-strike IV, fitted IV and delta for one expiry, ordered by strike.

    The surface read down one expiry, plus the delta column that surface does not carry —
    ``risk_reversal_butterfly`` searches on delta, so this is where the greek is priced.
    A delta that cannot be priced is ``None``, never zero; :class:`DeltaModel` has why.

    Columns: ``strike``, ``moneyness``, ``opt_type``, ``iv``, ``fitted_iv``, ``delta``.
    Returns ``pl.DataFrame()`` when the expiry has no chain.
    """
    surface = iv_surface(
        catalog, underlying, at_ns, model=model, rate=rate, fit_method=fit_method
    )
    if len(surface) == 0:
        return pl.DataFrame()

    skew = surface.filter(pl.col("expiry") == expiry_ns)
    if len(skew) == 0:
        return pl.DataFrame()

    underlying_price = _underlying_price_at(catalog, underlying, at_ns, expiry_ns=expiry_ns)
    t_years = (expiry_ns - at_ns) / _NS_PER_YEAR

    out_strike: list[float] = []
    out_moneyness: list[float] = []
    out_opt_type: list[str] = []
    out_iv: list[float | None] = []
    out_fitted_iv: list[float | None] = []
    out_delta: list[float | None] = []

    for row in skew.sort("strike").iter_rows(named=True):
        iv = row["iv"]
        delta: float | None = None
        if iv is not None and underlying_price is not None and t_years > 0.0:
            try:
                delta = model.delta(
                    underlying_price=underlying_price,
                    strike=float(row["strike"]),
                    t_years=t_years,
                    vol=float(iv),
                    opt_type=_opt_type(str(row["opt_type"])),
                    rate=rate,
                )
            except Exception:
                delta = None

        out_strike.append(float(row["strike"]))
        out_moneyness.append(float(row["moneyness"]))
        out_opt_type.append(str(row["opt_type"]))
        out_iv.append(iv)
        out_fitted_iv.append(row.get("fitted_iv"))
        out_delta.append(delta)

    if not out_strike:
        return pl.DataFrame()

    return pl.DataFrame(
        {
            "strike": pl.Series(out_strike, dtype=pl.Float64),
            "moneyness": pl.Series(out_moneyness, dtype=pl.Float64),
            "opt_type": pl.Series(out_opt_type, dtype=pl.Utf8),
            "iv": pl.Series(out_iv, dtype=pl.Float64),
            "fitted_iv": pl.Series(out_fitted_iv, dtype=pl.Float64),
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
    """The 25-delta risk reversal and butterfly, off a skew frame.

    Takes the frame rather than a catalog, and so has no asset class at all: by this point
    the market has been reduced to strikes, vols and deltas, and the arithmetic below —
    ``rr = iv(call at +δ) - iv(put at -δ)``, ``bf = mean of the two - atm_iv`` — is the
    same sentence in both.

    Returns ``(None, None)`` rather than a zero whenever a required option cannot be found.
    A zero risk reversal is a real and meaningful market state; an expiry nobody quoted is
    not that state.
    """
    if len(skew_df) == 0:
        return None, None

    if not {"iv", "delta", "opt_type"}.issubset(set(skew_df.columns)):
        return None, None

    call_rows = [
        row
        for row in skew_df.iter_rows(named=True)
        if str(row["opt_type"]) == OptType.CALL
        and row["iv"] is not None
        and row["delta"] is not None
    ]
    put_rows = [
        row
        for row in skew_df.iter_rows(named=True)
        if str(row["opt_type"]) == OptType.PUT
        and row["iv"] is not None
        and row["delta"] is not None
    ]

    best_call = _nearest_delta_row(call_rows, target_delta)
    # Put deltas are negative, so the 25-delta put is the one nearest -0.25.
    best_put = _nearest_delta_row(put_rows, -target_delta)
    if best_call is None or best_put is None:
        return None, None

    iv_call = float(best_call["iv"])
    iv_put = float(best_put["iv"])

    all_rows = [
        row
        for row in skew_df.iter_rows(named=True)
        if row["iv"] is not None and row["delta"] is not None
    ]
    atm_iv = _atm_iv(skew_df, all_rows)
    if atm_iv is None:
        return None, None

    return iv_call - iv_put, 0.5 * (iv_call + iv_put) - atm_iv


def _nearest(
    rows: Iterable[Mapping[str, Any]],
    distance: Callable[[Mapping[str, Any]], float],
) -> Mapping[str, Any] | None:
    """The row minimising ``distance``, skipping every row whose distance is not finite.

    Every "which strike is nearest X" search in this module goes through here, because
    ``min(rows, key=…)`` does not answer that question over a column that can hold a hole.
    ``moneyness`` is ``NaN`` where the chain carried no underlying price — ``iv_surface``
    calls that "a hole, not a strike that happens to sit at zero" — and
    ``abs(nan - 1.0)`` is ``nan``, which compares false against everything. ``min`` keeps
    an incumbent unless a later candidate is strictly smaller, so a ``NaN`` that arrives
    first is never displaced and every honest strike behind it loses.

    That made the winner a property of frame order, and ``_snapshot`` de-duplicates with
    ``maintain_order=False``, so frame order is not stable between runs over one lake.
    Measured on twenty-five runs against an identical store, ten published
    ``atm_strike=5000.0, atm_iv=2.5`` — a 250% vol on a strike fifty times spot, reported
    as that expiry's ATM — and fifteen published the strike next to spot. A capability
    whose two asset-class halves "cannot disagree about which strike is ATM" had a half
    that could not agree with itself.

    Skipping is the right treatment rather than scoring a hole as far away: a row with no
    underlying price makes no statement about where the money is, and ranking it last
    would still let it win an expiry where it is the only row. When nothing is finite the
    answer is ``None``, and each caller says what it does with that.

    Ties keep the earliest row, which is ``min``'s rule; callers that need the result to
    be stable across runs order their rows before calling.
    """
    best: Mapping[str, Any] | None = None
    best_distance = math.inf
    for row in rows:
        candidate = distance(row)
        if not math.isfinite(candidate):
            continue
        if candidate < best_distance:
            best_distance = candidate
            best = row
    return best


def _nearest_delta_row(
    rows: list[dict[str, Any]],
    target: float,
) -> Mapping[str, Any] | None:
    """Return the row whose ``delta`` is nearest to ``target``, or ``None``."""
    return _nearest(rows, lambda r: abs(float(r["delta"]) - target))


def _atm_iv(
    skew_df: pl.DataFrame,
    all_rows: list[dict[str, Any]],
) -> float | None:
    """The ATM IV: the option whose ``|delta|`` is nearest 0.5, else moneyness nearest 1.

    Delta first because it is the definition the risk reversal is quoted against, and
    moneyness second because a chain whose deltas could not be priced still knows which
    strike sits at the money. Both searches run through :func:`_nearest`, so a hole in
    either column loses instead of winning; if neither column leaves a finite candidate
    the answer is ``None``, which ``risk_reversal_butterfly`` already turns into
    ``(None, None)`` rather than a zero.
    """
    if not all_rows:
        return None

    if all(r["delta"] is not None for r in all_rows):
        atm_row = _nearest(all_rows, lambda r: abs(abs(float(r["delta"])) - 0.5))
        if atm_row is not None:
            return float(atm_row["iv"])

    if "moneyness" in skew_df.columns:
        moneyness_rows = [r for r in all_rows if r.get("moneyness") is not None]
        atm_row = _nearest(moneyness_rows, lambda r: abs(float(r["moneyness"]) - 1.0))
        if atm_row is not None:
            return float(atm_row["iv"])

    return None


# ---------------------------------------------------------------------------
# term_structure
# ---------------------------------------------------------------------------


def term_structure(
    catalog: Catalog,
    underlying: str,
    at_ns: int,
    *,
    model: OptionsModel,
    rate: float = 0.0,
) -> pl.DataFrame:
    """The ATM IV per expiry at ``at_ns``, ordered by expiry.

    Columns: ``expiry`` (Int64), ``days_to_expiry``, ``atm_strike``, ``atm_iv``.

    ``fit_method`` is absent here and present on the other two, which is why the family
    shares no parameter for it: this function reports the vol the chain published at the
    ATM strike and never a fitted one, so a fit selector would be a parameter it accepted
    and ignored.
    """
    surface = iv_surface(catalog, underlying, at_ns, model=model, rate=rate)
    if len(surface) == 0:
        return pl.DataFrame()

    underlying_price = _underlying_price_at(catalog, underlying, at_ns)

    out_expiry: list[int] = []
    out_days: list[float] = []
    out_atm_strike: list[float] = []
    out_atm_iv: list[float | None] = []

    for expiry in sorted(set(surface["expiry"].to_list())):
        # Sorted, because `_snapshot` de-duplicates with `maintain_order=False` and the
        # search below can tie: two strikes equidistant from spot are a real chain, and
        # which of them is named must not be a property of how DuckDB happened to hand
        # the rows back. `expiry` is constant inside the loop, so strike and type are the
        # whole key.
        expiry_rows = surface.filter(pl.col("expiry") == expiry).sort(["strike", "opt_type"])
        if len(expiry_rows) == 0:
            continue

        if underlying_price is not None:
            reference = underlying_price
            best_row = _nearest(
                expiry_rows.iter_rows(named=True),
                lambda r, up=reference: abs(float(r["strike"]) - up),  # type: ignore[misc]
            )
        else:
            # No stored spot: moneyness is the only remaining statement about where the
            # money is, and it is one the surface already made. Rows that made no such
            # statement carry `NaN` there and are skipped by `_nearest` rather than
            # winning it — see that function for what they used to cost.
            best_row = _nearest(
                expiry_rows.iter_rows(named=True),
                lambda r: abs(float(r["moneyness"]) - 1.0),
            )

        if best_row is None:
            # Every row of this expiry is a hole: no stored spot, and no row carrying an
            # underlying price of its own. There is no strike this expiry can call ATM,
            # and `atm_strike` is not nullable, so the expiry is absent from the term
            # structure rather than present with an invented one.
            continue

        atm_iv = best_row["iv"]
        out_expiry.append(int(expiry))
        out_days.append((expiry - at_ns) / _NS_PER_DAY)
        out_atm_strike.append(float(best_row["strike"]))
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
