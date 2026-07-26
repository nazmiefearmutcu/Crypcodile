"""Deprecated: moved to crocodile.core.sink."""

import sys as _sys
import warnings as _warnings

from crocodile.core.sink import base, memory  # noqa: F401
from crocodile.core.sink.base import Sink  # noqa: F401
from crocodile.core.sink.memory import MemorySink  # noqa: F401

# ``from crypcodile.sink.base import Sink`` asks the import machinery for a module
# named ``crypcodile.sink.base``.  Re-exporting the *name* is not enough: this shim
# is a plain module, so there is no package directory left for the finder to search.
# Registering the canonical modules under the old dotted names makes those deep
# imports resolve, and resolve to the *same* module object — so a test that patches
# ``crypcodile.<pkg>.<mod>.<attr>`` patches the one object everybody else uses.
for _alias, _module in (("base", base), ("memory", memory)):
    _sys.modules[f"{__name__}.{_alias}"] = _module

__all__ = ["MemorySink", "Sink", "base", "memory"]

_warnings.warn(
    "crypcodile.sink moved to crocodile.core.sink",
    DeprecationWarning,
    stacklevel=2,
)
