"""The shared carry arithmetic, and the proof that lifting it changed no crypto number.

``crocodile.core.analytics.carry`` was assembled out of two crypto modules that never met:
``funding.py`` annualised a rate by 8760 hours and ``basis.py`` annualised a spread by 365
days, and the equity halves of the same four capabilities need both. The risk of a lift
like that is silent drift, so the first section below asserts the two crypto modules still
re-export and still compute exactly what their own golden-number tests
(``tests/analytics/test_funding.py``, ``tests/analytics/test_basis.py``) pin — including
the ``ValueError`` message, which three of those tests match on by text.
"""

from __future__ import annotations

import pytest

from crocodile.core.analytics.carry import (
    DAYS_PER_YEAR,
    HOURS_PER_YEAR,
    NS_PER_DAY,
    NS_PER_HOUR,
    annualise_over_days,
    apr_from_rate,
    carry_over_risk_free,
    days_between,
    hours_between,
    periods_per_year,
    spread,
)

# ---------------------------------------------------------------------------
# The lift is identity: the crypto names still resolve here
# ---------------------------------------------------------------------------


def test_the_crypto_module_re_exports_the_lifted_functions_and_not_copies() -> None:
    """Two definitions of one multiplication is how the forks drifted in the first place."""
    from crocodile.crypto.analytics import funding

    assert funding.periods_per_year is periods_per_year
    assert funding.apr_from_rate is apr_from_rate


def test_the_golden_funding_numbers_are_unchanged() -> None:
    """The three values ``tests/analytics/test_funding.py`` pins, asserted from this side."""
    assert periods_per_year(8) == pytest.approx(1095.0)
    assert periods_per_year(1) == pytest.approx(HOURS_PER_YEAR)
    assert apr_from_rate(0.0001, 8) == pytest.approx(0.1095)
    assert apr_from_rate(-0.0002, 8) == pytest.approx(-0.219)


@pytest.mark.parametrize("interval", [0, -1, -8, 0.0])
def test_a_non_positive_interval_keeps_the_message_three_tests_match_on(interval: float) -> None:
    """The wording says *integer* while the parameter is a float, on purpose.

    Widening the type is what lets the equity half pass hours-to-expiry through the same
    function; narrowing the message to match the type would break the regression gate
    that proves no stored crypto number moved.
    """
    with pytest.raises(ValueError, match="interval_hours must be a positive integer"):
        periods_per_year(interval)
    with pytest.raises(ValueError, match="interval_hours must be a positive integer"):
        apr_from_rate(0.0001, interval)


def test_a_fractional_interval_is_accepted_because_an_equity_period_is_not_whole() -> None:
    """Hours to a dividend, or to an expiry, is not a round number of hours."""
    assert periods_per_year(0.5) == pytest.approx(2 * HOURS_PER_YEAR)
    assert apr_from_rate(0.01, 4380.0) == pytest.approx(0.02)


# ---------------------------------------------------------------------------
# Annualising over a horizon
# ---------------------------------------------------------------------------


def test_a_spread_over_a_full_year_annualises_to_itself() -> None:
    assert annualise_over_days(0.01, DAYS_PER_YEAR) == pytest.approx(0.01)


def test_a_spread_over_a_quarter_annualises_to_four_times_itself() -> None:
    assert annualise_over_days(0.01, DAYS_PER_YEAR / 4.0) == pytest.approx(0.04)


@pytest.mark.parametrize("days", [0.0, -1.0, -365.0])
def test_a_non_positive_horizon_is_undefined_rather_than_nil(days: float) -> None:
    """A nil would read as "no carry", which is a claim about the market rather than
    about the calendar. This is the behaviour ``spot_future_basis`` already had."""
    assert annualise_over_days(0.01, days) is None


def test_the_carry_is_the_annualised_spread_less_what_money_costs() -> None:
    assert carry_over_risk_free(0.06, 0.045) == pytest.approx(0.015)
    assert carry_over_risk_free(0.02, 0.045) == pytest.approx(-0.025)


@pytest.mark.parametrize(
    ("annualised", "rate"), [(None, 0.045), (0.06, None), (None, None)]
)
def test_an_absent_leg_propagates_rather_than_defaulting_to_zero(
    annualised: float | None, rate: float | None
) -> None:
    """Defaulting the rate to 0.0 would make the carry equal the basis, which is exactly
    the answer a real zero-rate world gives — and nothing would distinguish the two."""
    assert carry_over_risk_free(annualised, rate) is None


# ---------------------------------------------------------------------------
# The spread itself
# ---------------------------------------------------------------------------


def test_the_percentage_denominator_is_always_the_cash_leg() -> None:
    """The argument order is the thing the two legacy REST routes flipped."""
    assert spread(101.0, 100.0) == (pytest.approx(1.0), pytest.approx(0.01))
    assert spread(100.0, 101.0)[1] == pytest.approx(-1.0 / 101.0)


def test_a_zero_cash_leg_has_no_percentage_rather_than_an_infinite_one() -> None:
    difference, percent = spread(101.0, 0.0)
    assert difference == pytest.approx(101.0)
    assert percent is None


# ---------------------------------------------------------------------------
# Unit conversions
# ---------------------------------------------------------------------------


def test_the_nanosecond_constants_are_the_units_they_claim() -> None:
    assert NS_PER_DAY == 24 * NS_PER_HOUR
    assert NS_PER_HOUR == 3600 * 1_000_000_000


def test_the_two_gaps_are_fractional_and_signed() -> None:
    assert days_between(0, NS_PER_DAY // 2) == pytest.approx(0.5)
    assert days_between(NS_PER_DAY, 0) == pytest.approx(-1.0)
    assert hours_between(0, 90 * 60 * 1_000_000_000) == pytest.approx(1.5)


def test_a_year_of_hours_and_a_year_of_days_describe_the_same_year() -> None:
    """8760 and 365 are the same non-leap year in two units, which is what makes an
    equity carry — annualised by days — subtractable from a funding APR annualised by
    hours."""
    assert HOURS_PER_YEAR == DAYS_PER_YEAR * 24.0
