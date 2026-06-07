"""Shared interval-parsing utilities for the resample package.

All resample functions (ohlcv, metrics, …) accept the same short-hand interval
notation (``"1s"``, ``"5m"``, ``"1h"``, ``"4h"``, ``"1d"``, ``"1w"``).  This
module provides a single implementation so a future regex or unit-map change
only needs to happen in one place.
"""

from __future__ import annotations

import re

# Map from shorthand suffix to DuckDB INTERVAL unit word.
_UNIT_MAP: dict[str, str] = {
    "s": "second",
    "m": "minute",
    "h": "hour",
    "d": "day",
    "w": "week",
}

_INTERVAL_RE = re.compile(r"^(\d+)([smhdw])$")


def parse_interval(interval: str) -> tuple[str, str]:
    """Translate a shorthand interval string to safe SQL components.

    The input is validated against a strict regex (digits followed by one of
    ``s/m/h/d/w``).  Only the validated numeric quantity and unit word are used
    in SQL construction — no raw user input is ever interpolated into SQL.

    Args:
        interval: Short-hand interval string (e.g. ``"1s"``, ``"5m"``).

    Returns:
        A 2-tuple ``(interval_sql, unit_word)`` where ``interval_sql`` is a
        safe DuckDB ``INTERVAL '...'`` literal (e.g. ``"INTERVAL '1 minute'"``).

    Raises:
        ValueError: If the interval string cannot be parsed.
    """
    m = _INTERVAL_RE.match(interval.strip().lower())
    if m is None:
        raise ValueError(
            f"Cannot parse interval {interval!r}. "
            f"Expected a number followed by s/m/h/d/w (e.g. '1s', '5m', '1h')."
        )
    qty: str = m.group(1)            # validated: only digits
    unit: str = _UNIT_MAP[m.group(2)]  # validated: one of fixed unit words
    # Both components come from our own validation, not raw user input.
    interval_sql = f"INTERVAL '{qty} {unit}'"
    return interval_sql, unit
