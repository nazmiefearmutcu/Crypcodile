"""Shared interval parsing for every resampler, crypto and equity alike.

There used to be two ``parse_interval`` functions with the same name, the same
signature and different arities — core returned ``(sql, unit)`` and equity returned
``(ns, sql, polars)`` — so a caller that imported the wrong one either raised on the
unpack or, when the arities happened to line up, bound ``interval_sql`` to the bare
word ``"minute"`` and built SQL from it. Two capabilities cannot be projected from one
registry entry while the thing they parse their interval with depends on which package
they were imported from, so the two are one function now.

It returns a named structure rather than a tuple, which is what the equity module's own
note proposed as the end state. Positional unpacking is what made the arity difference
silent: ``a, b = parse_interval(...)`` is a statement about how many fields there are,
never about which. ``parse_interval(...).sql`` cannot be read as anything else, so a
field added later breaks nobody and a field misread breaks immediately.
"""

from __future__ import annotations

import re
from typing import NamedTuple

# Map from shorthand suffix to DuckDB INTERVAL unit word.
_UNIT_MAP: dict[str, str] = {
    "s": "second",
    "m": "minute",
    "h": "hour",
    "d": "day",
    "w": "week",
}

_NS_MAP: dict[str, int] = {
    "s": 1_000_000_000,
    "m": 60_000_000_000,
    "h": 3_600_000_000_000,
    "d": 86_400_000_000_000,
    "w": 604_800_000_000_000,
}

_INTERVAL_RE = re.compile(r"^(\d+)([smhdw])$")

_DAY_NS = 86_400_000_000_000

_MONDAY_BEFORE_EPOCH_NS = -3 * _DAY_NS
"""1969-12-29T00:00:00Z, the Monday whose week contains the Unix epoch.

The epoch fell on a *Thursday*, so a weekly bucket floored with ``ts // week * week``
starts on a Thursday. Neither engine this codebase resamples with does that: DuckDB's
``time_bucket`` counts non-month intervals from an origin of 2000-01-03 and Polars'
``truncate``/``group_by_dynamic`` anchor a week on its Monday, and both were measured
here — ``time_bucket(INTERVAL '1 week', make_timestamp(0))`` and
``from_epoch(0).dt.truncate('1w')`` each answer 1969-12-29. Counting from this instant
instead of from zero is what makes the third path agree with the two.
"""

_ANCHOR_NS: dict[str, int] = {"w": _MONDAY_BEFORE_EPOCH_NS}
"""Per-unit bucket origin, as a nanosecond epoch offset; absent means the epoch itself.

Only the week needs an entry. A second, a minute, an hour and a day all divide the
epoch-midnight grid exactly, so flooring against zero already lands where both engines
land; a week does not divide it, and 604800 seconds counted from a Thursday is a week
that begins on a Thursday.
"""


class Interval(NamedTuple):
    """One bar width, in each of the five spellings the resamplers need."""

    ns: int
    """Width in nanoseconds, for integer bucket arithmetic over ``local_ts``."""

    sql: str
    """A DuckDB ``INTERVAL '...'`` literal, e.g. ``"INTERVAL '5 minute'"``."""

    polars: str
    """A Polars ``every=`` string, e.g. ``"5m"``, for ``group_by_dynamic``."""

    unit: str
    """The bare DuckDB unit word, e.g. ``"minute"``."""

    anchor_ns: int
    """The instant bucket zero begins at, as a nanosecond epoch offset.

    Zero for every width that divides the epoch-midnight grid;
    :data:`_MONDAY_BEFORE_EPOCH_NS` for a week. Carried on the interval rather than
    computed at each call site because the three record resamplers each floored their
    own bucket and the two frame resamplers delegated to an engine, which is exactly how
    the three came to disagree with the two about which rows share a weekly bucket.
    """


def bucket_start(ts: int, width: int, anchor: int) -> int:
    """Return the first instant of the bucket ``width`` wide that contains ``ts``.

    All three arguments are in one unit, and it need not be nanoseconds: the record
    resamplers work in whatever unit their stream stamps its timestamps in, so a
    millisecond stream passes a millisecond width and a millisecond anchor.

    ``anchor`` shifts the grid rather than the value. Python's floor division rounds
    toward negative infinity, so this is correct for timestamps on either side of the
    anchor — which matters, because the week anchor is three days *before* the epoch and
    a test stream stamped from zero is therefore before it.
    """
    return (ts - anchor) // width * width + anchor


def parse_interval(interval: str) -> Interval:
    """Translate a shorthand interval string into every spelling a resampler needs.

    The input is validated against a strict regex (digits followed by one of
    ``s/m/h/d/w``) before any component reaches SQL, so no caller-controlled string is
    ever interpolated into a query.

    Args:
        interval: Short-hand interval string (e.g. ``"1s"``, ``"5m"``).

    Returns:
        An :class:`Interval`.

    Raises:
        ValueError: If the interval string cannot be parsed. Note that the match is
            made after ``.lower()``, so ``"1M"`` is one *minute* rather than a month —
            months and years are not supported by either resampler and never were.
    """
    m = _INTERVAL_RE.match(interval.strip().lower())
    if m is None:
        raise ValueError(
            f"Cannot parse interval {interval!r}. "
            f"Expected a number followed by s/m/h/d/w (e.g. '1s', '5m', '1h')."
        )
    qty_str: str = m.group(1)  # validated: only digits
    unit_char: str = m.group(2)  # validated: one of s/m/h/d/w
    return Interval(
        ns=int(qty_str) * _NS_MAP[unit_char],
        sql=f"INTERVAL '{qty_str} {_UNIT_MAP[unit_char]}'",
        polars=f"{qty_str}{unit_char}",
        unit=_UNIT_MAP[unit_char],
        anchor_ns=_ANCHOR_NS.get(unit_char, 0),
    )
