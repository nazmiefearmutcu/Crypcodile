"""The arithmetic a carry is, in the one place both asset classes can reach it.

Four capabilities — ``basis``, ``perp-basis``, ``spot-future-basis`` and ``funding-apr`` —
are one trade priced four ways: the spread between a derivative leg and its cash leg,
carried to an annual rate, and (where a horizon exists) read net of what it costs to hold
the cash leg over that horizon. Crypto had all four and expressed the arithmetic twice,
in ``crocodile.crypto.analytics.funding`` and ``crocodile.crypto.analytics.basis``, in two
idioms that never met: ``rate * 8760 / interval_hours`` in one file and
``basis_pct * 365 / days_to_expiry`` in the other. They are the same operation over
different units of the same year.

What lives here is what is true of *any* market: a year has 8760 hours and (by the
convention both forks already used) 365 days; a per-period rate becomes an annual one by
multiplying by the number of periods; an annualised spread becomes a carry by subtracting
the financing rate over the same horizon. What does not live here is anything that names a
channel, a venue or a record — the equity and crypto halves each read their own lake and
then call these.

**Simple, not compounded**, throughout. That is the convention
``crocodile.crypto.analytics.funding`` shipped and its golden numbers encode
(``0.0001 * 1095 == 0.1095``, ``tests/analytics/test_funding.py:113``), and it is also the
convention the Treasury par yield curve is quoted in, so the two legs of an equity carry
are on one footing without a conversion nobody asked for. Compounding would change every
stored answer and buy accuracy only over horizons where the difference is smaller than the
staleness of the yield being subtracted.
"""

from __future__ import annotations

from typing import Final

__all__ = [
    "DAYS_PER_YEAR",
    "HOURS_PER_YEAR",
    "NS_PER_DAY",
    "NS_PER_HOUR",
    "annualise_over_days",
    "apr_from_rate",
    "carry_over_risk_free",
    "days_between",
    "hours_between",
    "periods_per_year",
    "spread",
]

HOURS_PER_YEAR: Final = 8760.0
"""Hours in a non-leap year. The denominator ``funding-apr`` has always annualised by."""

DAYS_PER_YEAR: Final = 365.0
"""Days in a non-leap year, which is what ``spot_future_basis`` annualises by.

365 and not 360: the 360-day money-market convention would put the futures leg and the
Treasury leg on different clocks, and the Treasury publishes the par yield curve on a
bond-equivalent (365-day) basis. One clock for both legs is the whole point of subtracting
one from the other.
"""

NS_PER_DAY: Final = 86_400_000_000_000
NS_PER_HOUR: Final = 3_600_000_000_000


def periods_per_year(interval_hours: float) -> float:
    """Return how many ``interval_hours``-long periods fit in a year.

    Args:
        interval_hours: Length of one period in hours. Must be positive.

    Returns:
        ``8760 / interval_hours`` — 1095.0 for the 8-hour interval most perpetuals settle
        on, 8760.0 for an hourly one.

    Raises:
        ValueError: if ``interval_hours`` is zero or negative. A stored ``0`` would be a
            ``ZeroDivisionError`` deep in a Polars loop and a stored negative would be a
            silently negated APR, which is worse.

    The message says *integer* because that is the wording
    ``crocodile.crypto.analytics.funding`` shipped and three tests match on it
    (``tests/analytics/test_funding.py:305``, ``:311``, ``:317``). The parameter is
    nonetheless a ``float`` here, because the equity half's period is the time an equity
    holder is financed for — hours to expiry, hours between dividend events — and neither
    is whole. Widening the type while keeping the message is the trade that lets one
    function serve both without changing a single stored crypto number; narrowing the
    message to match the type would break the regression gate that proves that.
    """
    if interval_hours <= 0:
        raise ValueError(f"interval_hours must be a positive integer, got {interval_hours}")
    return HOURS_PER_YEAR / interval_hours


def apr_from_rate(rate: float, interval_hours: float) -> float:
    """Annualise a single per-period rate: ``rate * periods_per_year(interval_hours)``.

    Args:
        rate: What one period costs or pays, as a decimal fraction (0.0001 is one basis
            point). Signed: a crypto funding rate is positive when longs pay shorts, and
            the equity half keeps that sign so the two series can be read on one axis.
        interval_hours: Length of that period in hours.

    Returns:
        The annualised rate as a decimal fraction (0.1095 is 10.95 %).

    Routing through :func:`periods_per_year` rather than inlining the division is what
    makes the invariant on ``interval_hours`` unforgettable; it is the reason the crypto
    per-row loop calls this instead of multiplying.
    """
    return rate * periods_per_year(interval_hours)


def annualise_over_days(pct: float, days: float) -> float | None:
    """Carry a spread measured over ``days`` up to an annual rate.

    Args:
        pct: The spread as a decimal fraction of the cash leg.
        days: How many days the spread is earned over — for a dated future, the days
            remaining to expiry.

    Returns:
        ``pct * 365 / days``, or ``None`` when ``days`` is zero or negative.

    ``None`` and not zero, and not an exception. A future that has expired, or a print
    stamped at the expiry instant, has no remaining horizon to spread the basis over, so
    the annualised number is undefined rather than nil — and a nil would be read as "no
    carry", which is a claim about the market rather than about the calendar. This is the
    behaviour ``spot_future_basis`` already had (``crypto/analytics/basis.py:276``) and a
    crypto regression test pins it (``tests/analytics/test_basis.py:457``).
    """
    if days <= 0.0:
        return None
    return pct * DAYS_PER_YEAR / days


def carry_over_risk_free(
    annualised_pct: float | None, risk_free_rate: float | None
) -> float | None:
    """Return what the trade earns above financing: ``annualised_pct - risk_free_rate``.

    Args:
        annualised_pct: The annualised spread between the derivative leg and the cash leg.
        risk_free_rate: The annual financing rate over the same horizon, as a decimal
            fraction. For the equity halves this is a published Treasury par yield.

    Returns:
        The excess, or ``None`` if either input is absent.

    This one subtraction is the whole of what turns a basis into a carry, and it is the
    reason ``spot-future-basis`` could not have an equity half before M5. A crypto
    perpetual quotes its financing directly, as funding, so the crypto halves never needed
    the term; an equity future quotes only a price, and the cost of holding the shares it
    substitutes for is published somewhere else entirely.

    Propagating ``None`` rather than defaulting the rate to 0.0 is deliberate: a missing
    yield would make the carry equal the basis, which is exactly the answer you would get
    in a zero-rate world, and nothing on the row would distinguish the two. The absent leg
    is instead reported as absent, and it is one of the observables
    ``treasury_carry``'s confidence formula is a function of.
    """
    if annualised_pct is None or risk_free_rate is None:
        return None
    return annualised_pct - risk_free_rate


def spread(rich: float, cheap: float) -> tuple[float, float | None]:
    """Return ``(rich - cheap, (rich - cheap) / cheap)`` — a basis and its percentage.

    Args:
        rich: The leg quoted at a premium in the normal case — the derivative.
        cheap: The leg it is measured against — spot, index, or the cash price.

    Returns:
        The absolute spread, and the spread as a fraction of ``cheap``, which is ``None``
        when ``cheap`` is zero.

    The percentage denominator is the *cash* leg in every one of the four capabilities:
    ``basis_pct = (perp - spot) / spot``, ``(mark - index) / index``,
    ``(future - spot) / spot``. Writing it once is worth more than the two lines it saves,
    because the argument order is the thing that was flipped between the two legacy REST
    routes (``api_server.py:2010`` passes ``(spot, perp)`` and ``:2095`` passes
    ``(future, spot)``) — a caller that reversed them got a sign-flipped answer with no
    column to notice it by.
    """
    difference = rich - cheap
    if cheap == 0.0:
        return difference, None
    return difference, difference / cheap


def days_between(start_ns: int, end_ns: int) -> float:
    """Return the days from ``start_ns`` to ``end_ns``, fractional and signed."""
    return (end_ns - start_ns) / float(NS_PER_DAY)


def hours_between(start_ns: int, end_ns: int) -> float:
    """Return the hours from ``start_ns`` to ``end_ns``, fractional and signed."""
    return (end_ns - start_ns) / float(NS_PER_HOUR)
