"""Technical Analysis Indicators Engine using Polars.

Shared by both asset classes without a fork. The five primitives take a price
series and know nothing about markets; :func:`apply_indicators` lifts them onto
an OHLCV frame, which is the record type crypto and equity both produce natively.
That is what makes ``indicators`` the capability registry's walking skeleton.

The fork this replaced disagreed with itself on two numbers. ``equity/analytics/
indicators.py`` seeded RSI the way Wilder defined it and took the Bollinger deviation
over the population; this module started RSI from index 0 and took the sample
deviation. Each fork's own tests asserted its own answer, so neither was wrong by the
suite and both were on the wire. The canonical arithmetic won on the merits — see
:func:`_wilder_average` and :func:`calculate_bollinger_bands` for the two arguments —
which moved the crypto surfaces' numbers and left the equity ones where they were.
The one thing that did not come across from the equity copy is its treatment of a
missing price as zero movement: that turned a gap in the data into a confident RSI of
50, and a fabricated number is worse than an absent one.
"""

from collections.abc import Sequence
from typing import Any, overload

import numpy as np
import polars as pl

__all__ = [
    "apply_indicators",
    "calculate_bollinger_bands",
    "calculate_ema",
    "calculate_macd",
    "calculate_rsi",
    "calculate_sma",
]


def _to_series(prices: pl.Series | np.ndarray | Sequence[float | None]) -> pl.Series:
    """Helper to convert input type to a Polars Float64 Series."""
    if isinstance(prices, pl.Series):
        return prices.cast(pl.Float64)
    elif isinstance(prices, np.ndarray):
        return pl.Series(values=prices, dtype=pl.Float64)
    else:
        return pl.Series(values=list(prices), dtype=pl.Float64)


def _from_series(
    result: pl.Series,
    original: pl.Series | np.ndarray | Sequence[float | None],
) -> pl.Series | np.ndarray | list[float | None]:
    """Helper to convert a Polars Series back to the original input type."""
    if isinstance(original, pl.Series):
        return result
    elif isinstance(original, np.ndarray):
        return result.to_numpy()
    else:
        return result.to_list()


@overload
def calculate_sma(prices: pl.Series, period: int) -> pl.Series: ...


@overload
def calculate_sma(prices: np.ndarray, period: int) -> np.ndarray: ...


@overload
def calculate_sma(prices: Sequence[float | None], period: int) -> list[float | None]: ...


def calculate_sma(
    prices: pl.Series | np.ndarray | Sequence[float | None],
    period: int,
) -> pl.Series | np.ndarray | list[float | None]:
    """Calculate Simple Moving Average (SMA) over a given period."""
    if period <= 0:
        raise ValueError("Period must be a positive integer.")
    series = _to_series(prices)
    if len(series) == 0:
        return _from_series(series, prices)

    res = series.rolling_mean(window_size=period)
    return _from_series(res, prices)


@overload
def calculate_ema(prices: pl.Series, period: int) -> pl.Series: ...


@overload
def calculate_ema(prices: np.ndarray, period: int) -> np.ndarray: ...


@overload
def calculate_ema(prices: Sequence[float | None], period: int) -> list[float | None]: ...


def calculate_ema(
    prices: pl.Series | np.ndarray | Sequence[float | None],
    period: int,
) -> pl.Series | np.ndarray | list[float | None]:
    """Calculate Exponential Moving Average (EMA) over a given period."""
    if period <= 0:
        raise ValueError("Period must be a positive integer.")
    series = _to_series(prices)
    if len(series) == 0:
        return _from_series(series, prices)

    res = series.ewm_mean(span=period, adjust=False)
    return _from_series(res, prices)


def _wilder_average(changes: pl.Series, period: int) -> pl.Series:
    """Wilder's smoothed average of ``changes``, seeded by the mean of the first ``period``.

    Wilder's recursion ``avg[i] = avg[i-1] * (period-1)/period + changes[i]/period`` is an
    exponentially weighted mean with ``alpha = 1/period``, so the only thing that
    distinguishes it from a bare ``ewm_mean`` is where it starts: at index ``period``, from
    the simple mean of ``changes[1..period]``. Starting at index 0 from the first change
    instead — which is what a bare ``ewm_mean`` does — makes the first value an average of
    one observation, and because the recursion never forgets its seed the two series stay
    apart forever rather than converging. Seeding is therefore what makes this RSI the one
    TA-Lib and every charting package report.

    The seed is ``None`` when any change in the window is, rather than the mean of whatever
    survived. A gap in the prices means the average gain over that window is unknown, and
    an average taken over the observations that happened to be present is a number with no
    stated basis — the same reason the rest of this module propagates nulls instead of
    treating a missing price as no movement.
    """
    window = changes.slice(1, period)
    seed = None if window.null_count() else window.mean()
    seeded = pl.concat([pl.Series([seed], dtype=pl.Float64), changes.slice(period + 1)])
    warmup = pl.Series([None] * period, dtype=pl.Float64)
    return pl.concat([warmup, seeded.ewm_mean(alpha=1.0 / period, adjust=False)])


@overload
def calculate_rsi(prices: pl.Series, period: int) -> pl.Series: ...


@overload
def calculate_rsi(prices: np.ndarray, period: int) -> np.ndarray: ...


@overload
def calculate_rsi(prices: Sequence[float | None], period: int) -> list[float | None]: ...


def calculate_rsi(
    prices: pl.Series | np.ndarray | Sequence[float | None],
    period: int,
) -> pl.Series | np.ndarray | list[float | None]:
    """Calculate Relative Strength Index (RSI) over a given period using Wilder's smoothing.

    Warm-up: the first ``period`` changes seed the averages, so the first value that is not
    null sits at index ``period`` and ``period + 1`` prices are needed to produce one. A
    shorter series returns all nulls rather than a number computed from too little.
    """
    if period <= 0:
        raise ValueError("Period must be a positive integer.")
    series = _to_series(prices)
    if len(series) == 0:
        return _from_series(series, prices)
    if len(series) <= period:
        return _from_series(pl.Series([None] * len(series), dtype=pl.Float64), prices)

    change = series.diff()
    gain = change.clip(lower_bound=0.0)
    loss = (-change).clip(lower_bound=0.0)

    avg_gain = _wilder_average(gain, period)
    avg_loss = _wilder_average(loss, period)

    rs = avg_gain / avg_loss

    rsi_expr = (
        pl.when(avg_loss.is_null() | avg_gain.is_null())
        .then(None)
        .when((avg_loss == 0.0) & (avg_gain == 0.0))
        .then(50.0)
        .when(avg_loss == 0.0)
        .then(100.0)
        .otherwise(100.0 - (100.0 / (1.0 + rs)))
    )
    rsi = pl.select(rsi_expr).to_series()

    return _from_series(rsi, prices)


@overload
def calculate_macd(
    prices: pl.Series,
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9,
) -> tuple[pl.Series, pl.Series, pl.Series]: ...


@overload
def calculate_macd(
    prices: np.ndarray,
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]: ...


@overload
def calculate_macd(
    prices: Sequence[float | None],
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9,
) -> tuple[list[float | None], list[float | None], list[float | None]]: ...


def calculate_macd(
    prices: pl.Series | np.ndarray | Sequence[float | None],
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9,
) -> tuple[Any, Any, Any]:
    """Calculate Moving Average Convergence Divergence (MACD)."""
    if fast_period <= 0 or slow_period <= 0 or signal_period <= 0:
        raise ValueError("Periods must be positive integers.")
    series = _to_series(prices)
    if len(series) == 0:
        empty = _from_series(series, prices)
        return empty, empty, empty

    fast_ema = series.ewm_mean(span=fast_period, adjust=False)
    slow_ema = series.ewm_mean(span=slow_period, adjust=False)
    macd_line = fast_ema - slow_ema
    signal_line = macd_line.ewm_mean(span=signal_period, adjust=False)
    macd_hist = macd_line - signal_line

    return (
        _from_series(macd_line, prices),
        _from_series(signal_line, prices),
        _from_series(macd_hist, prices),
    )


@overload
def calculate_bollinger_bands(
    prices: pl.Series,
    period: int = 20,
    k: float = 2.0,
) -> tuple[pl.Series, pl.Series, pl.Series]: ...


@overload
def calculate_bollinger_bands(
    prices: np.ndarray,
    period: int = 20,
    k: float = 2.0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]: ...


@overload
def calculate_bollinger_bands(
    prices: Sequence[float | None],
    period: int = 20,
    k: float = 2.0,
) -> tuple[list[float | None], list[float | None], list[float | None]]: ...


def calculate_bollinger_bands(
    prices: pl.Series | np.ndarray | Sequence[float | None],
    period: int = 20,
    k: float = 2.0,
) -> tuple[Any, Any, Any]:
    """Calculate Bollinger Bands (Upper, Middle, Lower)."""
    if period <= 0:
        raise ValueError("Period must be a positive integer.")
    series = _to_series(prices)
    if len(series) == 0:
        empty = _from_series(series, prices)
        return empty, empty, empty

    middle = series.rolling_mean(window_size=period)
    # Population deviation, not sample. Polars defaults to ddof=1, which treats the window
    # as a sample drawn from a wider population and widens every band by
    # sqrt(period/(period-1)) — 2.6% at the default period of 20. Bollinger's bands are
    # defined over the window itself, so the window is the population and there is nothing
    # to correct for; a band 2.6% wide of the published one is a different indicator.
    std = series.rolling_std(window_size=period, ddof=0)
    upper = middle + k * std
    lower = middle - k * std

    return (
        _from_series(upper, prices),
        _from_series(middle, prices),
        _from_series(lower, prices),
    )


INDICATOR_NAMES: tuple[str, ...] = ("sma", "ema", "rsi", "macd", "bb", "all")
"""Every value :func:`apply_indicators` accepts, in the order the surfaces list them."""


def apply_indicators(
    bars: pl.DataFrame,
    indicator: str | None = None,
    period: int = 14,
) -> pl.DataFrame:
    """Append indicator columns to an OHLCV frame.

    This is the ``indicators`` capability's implementation, and one function serves both
    asset classes: it consumes ``close`` from a frame of OHLCV bars, which crypto and
    equity both produce natively, and knows nothing else about either market. Fetching or
    resampling those bars is the caller's job and is where the asset classes genuinely
    differ, so it deliberately stays outside.

    Args:
        bars: OHLCV rows carrying a ``close`` column, already in ascending time order.
            An empty frame is returned unchanged — a frame with no rows has no
            indicators, which is not an error.
        indicator: One of :data:`INDICATOR_NAMES`. ``None`` means ``"all"``.
        period: Lookback window for SMA, EMA, RSI and the Bollinger middle band. MACD
            uses its own conventional 12/26/9 and ignores this.

    Returns:
        ``bars`` with the requested columns appended. Column names are the wire names
        the existing surfaces already emit: ``sma``, ``ema``, ``rsi``, ``macd``,
        ``signal``, ``hist``, ``bb_upper``, ``bb_middle``, ``bb_lower``.

    Raises:
        ValueError: if ``indicator`` is not a recognised name, or ``period`` is not
            positive. An unknown name is a caller bug and silently returning the input
            unchanged would hide it.
    """
    name = (indicator or "all").lower()
    if name not in INDICATOR_NAMES:
        raise ValueError(f"Unknown indicator {indicator!r}; expected one of {INDICATOR_NAMES}")
    if period <= 0:
        raise ValueError("Period must be a positive integer.")
    if bars.is_empty():
        return bars

    close: pl.Series = bars["close"]
    columns: list[pl.Series] = []
    if name in ("sma", "all"):
        columns.append(calculate_sma(close, period).alias("sma"))
    if name in ("ema", "all"):
        columns.append(calculate_ema(close, period).alias("ema"))
    if name in ("rsi", "all"):
        columns.append(calculate_rsi(close, period).alias("rsi"))
    if name in ("macd", "all"):
        macd, signal, hist = calculate_macd(close)
        columns += [macd.alias("macd"), signal.alias("signal"), hist.alias("hist")]
    if name in ("bb", "all"):
        upper, middle, lower = calculate_bollinger_bands(close, period=period)
        columns += [
            upper.alias("bb_upper"),
            middle.alias("bb_middle"),
            lower.alias("bb_lower"),
        ]
    return bars.with_columns(columns)
