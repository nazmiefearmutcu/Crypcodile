"""The equity half of the vol surface: Black-Scholes-Merton, and the mid it inverts.

The surface, the fits and the four output frames are
:mod:`crocodile.core.analytics.volsurface`'s; this module is the pair of functions that
make them read a US equity chain rather than a crypto one, and the argument for each.

**Why a different model at all.** A crypto venue quotes an option on a *forward* and
publishes a ``mark_price`` against it, which is what makes Black-76 the right inversion
there. Yahoo quotes a US listed option on the *spot*, publishes a bid and an ask, and
publishes no mark of any kind. Black-Scholes-Merton is Black-76 with the forward written
out as ``S·e^{(r-q)T}``, so the two are the same model wearing the arguments each feed
actually supplies — and passing a spot into the Black-76 solver as though it were a
forward would silently misprice every contract by the carry, most visibly at long tenors
where a term structure is exactly what someone is looking at.

**Why the mid, and only the mid.** ``mark_price`` on a crypto row is the venue's own
statement of where the option is; the equity analogue of that statement is the midpoint of
a two-sided quote, and nothing else on the row is one:

* ``lastPrice`` is a *traded* price at an unstated instant. On an illiquid contract it can
  be days old, so inverting it yields a vol for a moment that is not ``at_ns`` — filed in
  a column whose whole premise is that every row is the same instant. It is refused.
* A one-sided quote has no mid. Substituting the quoted side, or half of it, would be a
  number this engine invented and then reported as a measurement.

So a contract with no published IV and no two-sided quote resolves to ``unavailable``,
which is a hole the ``source`` column names rather than a vol nobody quoted.

**Two model choices that are stated rather than measured, because the chain does not
carry them.** The dividend yield is 0.0: an option chain publishes no dividend stream, and
this product's dividend history lives on a different channel that this snapshot does not
join. The consequence is real and one-directional — a dividend-paying underlying's calls
imply slightly high and its puts slightly low. And the solver is European while US single
stock options are American, so an in-the-money put's early-exercise premium implies a
little high. Both are the convention every retail chain publishes under, including the
``impliedVolatility`` Yahoo itself puts on the row, so a solved vol and a published one
stay comparable — which is the property the ``source`` column would otherwise be lying
about.
"""

from __future__ import annotations

import math

import polars as pl

from crocodile.core.analytics.volsurface import ChainPrices, OptionsModel
from crocodile.core.analytics.volsurface import iv_surface as _core_iv_surface
from crocodile.core.analytics.volsurface import term_structure as _core_term_structure
from crocodile.core.analytics.volsurface import vol_skew as _core_vol_skew
from crocodile.core.schema.enums import OptType
from crocodile.core.store.catalog import Catalog
from crocodile.equity.analytics.options import bsm_greeks, bsm_implied_volatility

__all__ = [
    "BSM",
    "iv_surface",
    "mid_price",
    "term_structure",
    "vol_skew",
]

_NO_DIVIDEND_YIELD: float = 0.0
"""The dividend yield an option chain does not publish. The module docstring has the cost."""


def mid_price(quote: ChainPrices) -> float | None:
    """The midpoint of a two-sided quote, or ``None`` when there is not one.

    Both sides must be present and strictly positive. A zero bid is what Yahoo shows for a
    contract nobody is bidding on, and ``(0 + ask) / 2`` is half an offer rather than a
    market — it implies a vol roughly half the one the offer alone would, which is a wrong
    number rather than a missing one.
    """
    bid, ask = quote.bid_px, quote.ask_px
    if bid is None or ask is None:
        return None
    if not (math.isfinite(bid) and math.isfinite(ask)):
        return None
    if bid <= 0.0 or ask <= 0.0:
        return None
    return 0.5 * (bid + ask)


def _bsm_iv(
    *,
    quote: ChainPrices,
    underlying_price: float | None,
    strike: float,
    t_years: float,
    opt_type: OptType,
    rate: float,
) -> tuple[float | None, str]:
    """Resolve one equity row's implied vol: Yahoo's own, else BSM on the quote's mid.

    ``mark_iv`` carries Yahoo's ``impliedVolatility`` — the feed solved it and got there
    first, exactly as a crypto venue's ``mark_iv`` does — so it wins outright when it is
    present and positive.

    ``bsm_implied_volatility`` reports failure as ``NaN`` rather than ``None``: a price
    outside the no-arbitrage bounds, or a solve that does not converge inside a hundred
    iterations. That is checked here rather than trusted, because a ``NaN`` in the ``iv``
    column beside ``source="computed"`` would claim a solve that did not happen and would
    then propagate through the SABR fit into every fitted vol on the expiry.
    """
    mark_iv = quote.mark_iv
    if mark_iv is not None and math.isfinite(mark_iv) and mark_iv > 0.0:
        return float(mark_iv), "mark_iv"

    mid = mid_price(quote)
    if (
        mid is not None
        and underlying_price is not None
        and math.isfinite(underlying_price)
        and underlying_price > 0.0
        and t_years > 0.0
    ):
        iv = bsm_implied_volatility(
            price=mid,
            s=underlying_price,
            k=strike,
            t=t_years,
            r=rate,
            q=_NO_DIVIDEND_YIELD,
            option_type=opt_type,
        )
        if math.isfinite(iv) and iv > 0.0:
            return float(iv), "computed"

    return None, "unavailable"


def _bsm_delta(
    *,
    underlying_price: float,
    strike: float,
    t_years: float,
    vol: float,
    opt_type: OptType,
    rate: float,
) -> float | None:
    """Black-Scholes-Merton delta with respect to the spot, which is what an equity quotes."""
    return bsm_greeks(
        underlying_price,
        strike,
        t_years,
        rate,
        vol,
        _NO_DIVIDEND_YIELD,
        opt_type,
    )["delta"]


BSM = OptionsModel(solve_iv=_bsm_iv, delta=_bsm_delta)
"""The model the four equity capabilities read the shared surface through."""


def iv_surface(
    catalog: Catalog,
    underlying: str,
    at_ns: int,
    rate: float = 0.0,
    fit_method: str = "sabr",
) -> pl.DataFrame:
    """The equity implied-vol surface snapshot at ``at_ns``.

    Signature-for-signature the crypto half's, because the capability declares one
    ``params`` struct for both and an adapter that had to reorder arguments per asset
    class would be the divergence the registry exists to end.
    """
    return _core_iv_surface(
        catalog, underlying, at_ns, model=BSM, rate=rate, fit_method=fit_method
    )


def vol_skew(
    catalog: Catalog,
    underlying: str,
    expiry_ns: int,
    at_ns: int,
    rate: float = 0.0,
    fit_method: str = "sabr",
) -> pl.DataFrame:
    """Per-strike IV, fitted IV and Black-Scholes-Merton delta for one equity expiry."""
    return _core_vol_skew(
        catalog,
        underlying,
        expiry_ns,
        at_ns,
        model=BSM,
        rate=rate,
        fit_method=fit_method,
    )


def term_structure(
    catalog: Catalog,
    underlying: str,
    at_ns: int,
    rate: float = 0.0,
) -> pl.DataFrame:
    """The ATM IV per expiry for an equity underlying at ``at_ns``."""
    return _core_term_structure(catalog, underlying, at_ns, model=BSM, rate=rate)
