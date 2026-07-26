"""Deprecated: moved to crocodile.core.analytics.indicators."""

import warnings as _warnings

from crocodile.core.analytics.indicators import (  # noqa: F401
    INDICATOR_NAMES,
    apply_indicators,
    calculate_bollinger_bands,
    calculate_ema,
    calculate_macd,
    calculate_rsi,
    calculate_sma,
)

# No sys.modules aliasing here, unlike the package shims in crypcodile/sink.py and
# crypcodile/replay.py: those replaced *packages*, so ``from crypcodile.sink.base import X``
# needed the old dotted submodule names to keep resolving. ``indicators`` was a leaf
# module all along, so re-exporting the names is the whole job.
__all__ = [
    "INDICATOR_NAMES",
    "apply_indicators",
    "calculate_bollinger_bands",
    "calculate_ema",
    "calculate_macd",
    "calculate_rsi",
    "calculate_sma",
]

_warnings.warn(
    "crypcodile.analytics.indicators moved to crocodile.core.analytics.indicators",
    DeprecationWarning,
    stacklevel=2,
)
