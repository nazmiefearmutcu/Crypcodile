"""Deprecated: moved to crocodile.core.replay."""

import sys as _sys
import warnings as _warnings

from crocodile.core.replay import merge, orderbook  # noqa: F401
from crocodile.core.replay.merge import replay  # noqa: F401
from crocodile.core.replay.orderbook import BookGap, OrderBook  # noqa: F401

# See the note in crypcodile/sink.py for why the sys.modules aliases are needed.
for _alias, _module in (("merge", merge), ("orderbook", orderbook)):
    _sys.modules[f"{__name__}.{_alias}"] = _module

__all__ = ["BookGap", "OrderBook", "merge", "orderbook", "replay"]

_warnings.warn(
    "crypcodile.replay moved to crocodile.core.replay",
    DeprecationWarning,
    stacklevel=2,
)
