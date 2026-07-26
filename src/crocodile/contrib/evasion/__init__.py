"""Opt-in machinery for working around source-side access controls.

Not imported by ``crocodile.core`` and off by default. It exists because
providers that predate this package depend on it; nothing here is extended, and
``crocodile.core`` respects quotas rather than routing around them.

Install with ``pip install 'crocodile[evasion]'``.
"""

from crocodile.contrib.evasion.api_key import ApiKeyPool
from crocodile.contrib.evasion.proxy import ProxyRotator

__all__ = ["ApiKeyPool", "ProxyRotator"]
