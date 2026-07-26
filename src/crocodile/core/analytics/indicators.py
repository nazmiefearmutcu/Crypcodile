"""Technical Analysis Indicators Engine using Polars.

Shared by both asset classes without a fork. The five primitives take a price
series and know nothing about markets; :func:`apply_indicators` lifts them onto
an OHLCV frame, which is the record type crypto and equity both produce natively.
That is what makes ``indicators`` the capability registry's walking skeleton.
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
    """Calculate Relative Strength Index (RSI) over a given period using Wilder's smoothing."""
    if period <= 0:
        raise ValueError("Period must be a positive integer.")
    series = _to_series(prices)
    if len(series) == 0:
        return _from_series(series, prices)

    change = series.diff()
    gain = change.clip(lower_bound=0.0)
    loss = (-change).clip(lower_bound=0.0)

    # Wilder's smoothing uses alpha = 1 / period
    avg_gain = gain.ewm_mean(alpha=1.0 / period, adjust=False)
    avg_loss = loss.ewm_mean(alpha=1.0 / period, adjust=False)

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
    std = series.rolling_std(window_size=period)
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
