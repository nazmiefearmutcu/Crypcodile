"""Regression: real Deribit option tokens are D{1,2}MMMYY with an explicit year.

The legacy slice (date_str[:2] / [2:5]) mis-read single-digit days ("8JUN26" ->
day "8J") and ignored the 2-digit year, so live option subscriptions were dropped
with "cannot parse option symbol". These pin the corrected behavior.
"""

import calendar
import time

from crypcodile.exchanges.deribit.normalize import _parse_option_symbol
from crypcodile.schema.enums import OptType


def _expected_ns(day: int, month: int, year: int) -> int:
    struct = time.strptime(f"{day:02d} {month:02d} {year}", "%d %m %Y")
    return int(calendar.timegm(struct)) * 1_000_000_000


def test_single_digit_day_with_year():
    und, strike, expiry, opt = _parse_option_symbol("BTC-8JUN26-62000-C")
    assert und == "BTC"
    assert strike == 62000.0
    assert opt is OptType.CALL
    assert expiry == _expected_ns(8, 6, 2026)


def test_two_digit_day_with_year_put():
    _und, strike, expiry, opt = _parse_option_symbol("BTC-28JUN26-55000-P")
    assert strike == 55000.0
    assert opt is OptType.PUT
    assert expiry == _expected_ns(28, 6, 2026)


def test_explicit_year_is_used_not_guessed():
    # 30JUN25 must resolve to 2025, not a current/next-year guess.
    _, _, expiry, _ = _parse_option_symbol("BTC-30JUN25-50000-C")
    assert expiry == _expected_ns(30, 6, 2025)


def test_legacy_ddmmm_without_year_still_parses_future():
    # Backward-compat: "30JUN" (no year) still yields a future expiry.
    _, _, expiry, _ = _parse_option_symbol("BTC-30JUN-50000-C")
    assert expiry > int(time.time()) * 1_000_000_000
