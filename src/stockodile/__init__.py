"""Deprecated. Stockodile is now `crocodile.equity`; install `crocodile`.

This shim exists for one minor version so pinned imports keep working.
"""

import warnings as _warnings

from crocodile import *  # noqa: F403
from crocodile import __version__ as __version__

_warnings.warn(
    "stockodile is deprecated and will be removed in 0.4; "
    "import from crocodile (equity providers live under crocodile.equity)",
    DeprecationWarning,
    stacklevel=2,
)
