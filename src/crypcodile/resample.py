"""Deprecated: moved to crocodile.core.resample."""

import sys as _sys
import warnings as _warnings

from crocodile.core.resample import _interval, book, metrics, ohlcv  # noqa: F401
from crocodile.core.resample._interval import parse_interval  # noqa: F401
from crocodile.core.resample.book import resample_book_snapshots  # noqa: F401
from crocodile.core.resample.metrics import resample_metrics  # noqa: F401
from crocodile.core.resample.ohlcv import resample_ohlcv  # noqa: F401

# See the note in crypcodile/sink.py for why the sys.modules aliases are needed.
for _alias, _module in (
    ("_interval", _interval),
    ("book", book),
    ("metrics", metrics),
    ("ohlcv", ohlcv),
):
    _sys.modules[f"{__name__}.{_alias}"] = _module

__all__ = [
    "_interval",
    "book",
    "metrics",
    "ohlcv",
    "parse_interval",
    "resample_book_snapshots",
    "resample_metrics",
    "resample_ohlcv",
]

_warnings.warn(
    "crypcodile.resample moved to crocodile.core.resample",
    DeprecationWarning,
    stacklevel=2,
)
