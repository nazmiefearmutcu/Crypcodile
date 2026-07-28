"""The crypto half of the vol surface: Black-76, and the venue mark it inverts.

The surface itself moved. Everything this module used to hold that was arithmetic over
the ``options_chain`` columns — the snapshot rule, the SABR and spline fits, the moneyness,
the ATM search, the risk reversal and butterfly, the four output frames — now lives in
:mod:`crocodile.core.analytics.volsurface`, because none of it knew which market it was
reading and the equity half needed the same code rather than a second copy of it. The
module docstring there carries the argument.

What stays here is the pair of functions that genuinely are crypto's: Deribit and its
peers report a ``mark_price`` on a *forward*, so a vol solved from that price is a Black-76
inversion and the delta priced back from it is a Black-76 delta. The equity half quotes a
bid and an ask on a *spot* and has no mark at all, which is a different model and not a
different parameterisation of this one.

IV source priority, unchanged
-----------------------------
1. ``mark_iv`` (exchange-provided) — used directly, ``source="mark_iv"``.
2. ``mark_price`` plus ``underlying_price`` — inverted through Black-76,
   ``source="computed"``.
3. Neither — ``iv=NULL``, ``source="unavailable"``.

Moneyness is ``strike / underlying_price``; ATM is 1.0. All catalog-backed functions
return ``pl.DataFrame()`` when no data exists.
"""

from __future__ import annotations

import math

import polars as pl

from crocodile.core.analytics.volsurface import (
    _NS_PER_YEAR,
    ChainPrices,
    OptionsModel,
    _atm_iv,
    _snapshot,
    risk_reversal_butterfly,
)
from crocodile.core.analytics.volsurface import iv_surface as _core_iv_surface
from crocodile.core.analytics.volsurface import term_structure as _core_term_structure
from crocodile.core.analytics.volsurface import vol_skew as _core_vol_skew
from crocodile.core.schema.enums import OptType
from crocodile.core.store.catalog import Catalog
from crocodile.crypto.analytics.blackscholes import bs_greeks, implied_vol

__all__ = [
    "BLACK76",
    "_NS_PER_YEAR",
    "_atm_iv",
    "_resolve_iv",
    "_snapshot",
    "iv_surface",
    "risk_reversal_butterfly",
    "term_structure",
    "vol_skew",
]
"""The four public functions, plus four names this module published before the lift.

``_atm_iv``, ``_snapshot``, ``_resolve_iv`` and ``_NS_PER_YEAR`` are underscored and still
listed, which is the point: the Task 6.4 tests import all four from here by name, so they
are this module's interface to its own test suite whatever the leading underscore says.
Three of them are now the core object bound under its old spelling rather than a second
implementation, which is what keeps the two halves from drifting; listing them is also how
``F401`` is told they are re-exports rather than imports nothing uses.
"""


def _black76_iv(
    *,
    quote: ChainPrices,
    underlying_price: float | None,
    strike: float,
    t_years: float,
    opt_type: OptType,
    rate: float,
) -> tuple[float | None, str]:
    """Resolve one crypto row's implied vol: the venue's own, else Black-76 on its mark.

    The venue's ``mark_iv`` wins outright when it is present and positive, because the
    exchange solved it against the same forward this would and got there first. The
    fallback inverts the venue's ``mark_price``, which is why the surface is
    :attr:`~crocodile.core.schema.provenance.Provenance.DERIVED` rather than modelled: the
    price being inverted was published, not estimated from some other data class.

    An expired contract yields ``unavailable`` rather than an intrinsic-value vol. There
    is no volatility left to imply once ``t_years <= 0``, and the solver says so by
    returning ``None``.
    """
    mark_iv = quote.mark_iv
    if mark_iv is not None and math.isfinite(mark_iv) and mark_iv > 0.0:
        return float(mark_iv), "mark_iv"

    mark_price = quote.mark_price
    if (
        mark_price is not None
        and math.isfinite(mark_price)
        and mark_price > 0.0
        and underlying_price is not None
        and math.isfinite(underlying_price)
        and underlying_price > 0.0
        and t_years > 0.0
    ):
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


def _black76_delta(
    *,
    underlying_price: float,
    strike: float,
    t_years: float,
    vol: float,
    opt_type: OptType,
    rate: float,
) -> float | None:
    """Black-76 delta with respect to the forward, which is what a crypto venue quotes."""
    return bs_greeks(
        forward=underlying_price,
        strike=strike,
        t_years=t_years,
        vol=vol,
        opt_type=opt_type,
        rate=rate,
    ).delta


BLACK76 = OptionsModel(solve_iv=_black76_iv, delta=_black76_delta)
"""The model the four crypto capabilities read the shared surface through."""


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
    """Resolve one row's IV from an ``(expiry, at_ns)`` pair rather than a ``t_years``.

    The shape this module published before the surface moved to core, kept because it is
    what the Task 6.4 tests call directly and because an expiry and an instant is the pair
    a caller holding a chain row actually has. The arithmetic is :func:`_black76_iv`'s, so
    the two cannot answer differently.
    """
    return _black76_iv(
        quote=ChainPrices(
            mark_iv=mark_iv,
            mark_price=mark_price,
            bid_px=None,
            ask_px=None,
            last_price=None,
        ),
        underlying_price=underlying_price,
        strike=strike,
        t_years=(expiry - at_ns) / _NS_PER_YEAR,
        opt_type=OptType.CALL if opt_type_str == OptType.CALL else OptType.PUT,
        rate=rate,
    )


def iv_surface(
    catalog: Catalog,
    underlying: str,
    at_ns: int,
    rate: float = 0.0,
    fit_method: str = "sabr",
) -> pl.DataFrame:
    """The crypto implied-vol surface snapshot at ``at_ns``.

    Columns, dtypes and the empty-frame contract are
    :func:`crocodile.core.analytics.volsurface.iv_surface`'s; ``rate`` is the continuous
    risk-free rate the Black-76 inversion discounts at and ``fit_method`` selects the
    ``sabr`` or ``spline`` skew fit.
    """
    return _core_iv_surface(
        catalog, underlying, at_ns, model=BLACK76, rate=rate, fit_method=fit_method
    )


def vol_skew(
    catalog: Catalog,
    underlying: str,
    expiry_ns: int,
    at_ns: int,
    rate: float = 0.0,
    fit_method: str = "sabr",
) -> pl.DataFrame:
    """Per-strike IV, fitted IV and Black-76 delta for one crypto expiry."""
    return _core_vol_skew(
        catalog,
        underlying,
        expiry_ns,
        at_ns,
        model=BLACK76,
        rate=rate,
        fit_method=fit_method,
    )


def term_structure(
    catalog: Catalog,
    underlying: str,
    at_ns: int,
    rate: float = 0.0,
) -> pl.DataFrame:
    """The ATM IV per expiry for a crypto underlying at ``at_ns``."""
    return _core_term_structure(catalog, underlying, at_ns, model=BLACK76, rate=rate)
