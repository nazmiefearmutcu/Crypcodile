"""Deprecated. Crypcodile is now `crocodile.crypto`; install `crocodile`.

This shim exists for one minor version so pinned imports keep working.
"""

import warnings as _warnings

from crocodile import *  # noqa: F403
from crocodile import __version__ as __version__

_warnings.warn(
    "crypcodile is deprecated and will be removed in 0.4; "
    "import from crocodile (crypto connectors live under crocodile.crypto)",
    DeprecationWarning,
    stacklevel=2,
)
