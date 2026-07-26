"""Interval parsing for the *equity* resample package.

Two ``parse_interval`` implementations coexist in this repository
==================================================================

This is deliberate, it is not an oversight, and it must not be "cleaned up"
by deleting one of them without a separate decision. The crocodile merge
folded two forks (Crypcodile, crypto) and (stockodile, US equities) into one
package, and both forks independently grew a function named
``parse_interval`` with the *same name and the same signature* but a
*different arity*:

``crocodile.core.resample._interval.parse_interval``
    returns ``tuple[str, str]`` = ``(interval_sql, unit_word)``
``crocodile.equity.resample._interval.parse_interval``
    returns ``tuple[int, str, str]`` = ``(interval_ns, interval_sql, polars_str)``

Neither is a superset of the other in shape: the core (crypto) version's
second element is the bare DuckDB unit word (``"minute"``), while the equity
version's second element is the SQL literal and its third is a Polars
``every=`` string (``"5m"``). Unpacking one where the other is expected is a
silent-arity ``ValueError`` at best and a wrong SQL string at worst.

Who depends on which
--------------------

Callers of the **2-tuple core** version — these unpack exactly two values and
must keep working unchanged, so the core version is frozen:

* ``crocodile.core.resample.ohlcv.resample_ohlcv`` — ``interval_sql, _unit = ...``
* ``crocodile.core.resample.metrics.resample_metrics`` — same shape
* transitively, ``crocodile.crypto.client.client`` and
  ``crocodile.equity.client.client``, both of which call
  ``crocodile.core.resample.ohlcv.resample_ohlcv``

Callers of the **3-tuple equity** version — these need the nanosecond width
(for integer bucket arithmetic on ``local_ts``) and/or the Polars interval
string (for ``group_by_dynamic(every=...)``), neither of which the core
version produces:

* ``crocodile.equity.resample.ohlcv.resample_trades_to_bars``
* ``crocodile.equity.resample.ohlcv.resample_quotes_to_bars``
* ``crocodile.equity.resample.ohlcv.resample_bars_to_bars``
* ``crocodile.equity.resample.ohlcv.resample_trades_df``
* ``crocodile.equity.resample.ohlcv.resample_quotes_df``
* ``crocodile.equity.resample.ohlcv.resample_bars_df``
* ``tests/equity/test_resample.py::test_parse_interval`` asserts the 3-tuple
  contract directly.

Why this file exists instead of a shared one
---------------------------------------------

Widening the core version to 3 elements would break every crypto caller's
two-value unpack, and the merge's standing rule is that it must be
behaviour-preserving for both forks. Narrowing the equity version would
throw away the nanosecond width the stream resamplers are built on. So both
live, in their own layers, and this note is the marker.

Reconciling them is a later decision, not this task's. The likely end state
is one implementation returning a small named structure (dataclass or
``NamedTuple``) with ``ns`` / ``sql`` / ``polars`` / ``unit`` fields, so that
callers read attributes instead of unpacking positionally and the arity
question disappears. That change touches crypto call sites and therefore
needs its own review.

This is the fourth divergence of this kind the merge has surfaced, after the
RSI warm-up window, the Bollinger ``ddof``, and the ``ms_to_ns`` truncation
order. Same name, different arithmetic, in both cases silently.
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

_NS_MAP: dict[str, int] = {
    "s": 1_000_000_000,
    "m": 60_000_000_000,
    "h": 3_600_000_000_000,
    "d": 86_400_000_000_000,
    "w": 604_800_000_000_000,
}

_INTERVAL_RE = re.compile(r"^(\d+)([smhdw])$")


def parse_interval(interval: str) -> tuple[int, str, str]:
    """Translate a shorthand interval string to safe SQL components and nanoseconds.

    Note:
        This is the *equity* 3-tuple implementation. ``crocodile.core.resample``
        exports a different function of the same name returning a 2-tuple; see
        this module's docstring before using either.

    Args:
        interval: Short-hand interval string (e.g. ``"1s"``, ``"5m"``).

    Returns:
        A 3-tuple ``(interval_ns, interval_sql, polars_str)`` where
        ``interval_ns`` is the interval duration in nanoseconds,
        ``interval_sql`` is a safe DuckDB ``INTERVAL '...'`` literal, and
        ``polars_str`` is a Polars-compatible interval string.

    Raises:
        ValueError: If the interval string cannot be parsed.
    """
    m = _INTERVAL_RE.match(interval.strip().lower())
    if m is None:
        raise ValueError(
            f"Cannot parse interval {interval!r}. "
            f"Expected a number followed by s/m/h/d/w (e.g. '1s', '5m', '1h')."
        )
    qty_str: str = m.group(1)
    qty: int = int(qty_str)
    unit_char: str = m.group(2)

    ns = qty * _NS_MAP[unit_char]
    duckdb_unit = _UNIT_MAP[unit_char]
    interval_sql = f"INTERVAL '{qty_str} {duckdb_unit}'"
    polars_str = f"{qty_str}{unit_char}"

    return ns, interval_sql, polars_str
