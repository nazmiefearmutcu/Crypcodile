from __future__ import annotations

import os
from typing import TYPE_CHECKING, Final

from crocodile.equity.depth.base import DepthSource

if TYPE_CHECKING:  # pragma: no cover - annotations only
    from crocodile.core.config import Settings

__all__ = ["alpaca_is_keyed", "select_depth_source"]

_ALPACA_FIELDS: Final = ("alpaca_api_key", "alpaca_api_secret")
"""The two :class:`~crocodile.core.config.Settings` fields the L1 branch needs."""

_ALPACA_BARE_NAMES: Final = ("ALPACA_API_KEY", "ALPACA_API_SECRET")
"""The unprefixed spelling this switch has always read, kept so nobody's keys go dark.

``Settings`` reads ``CROCODILE_ALPACA_API_KEY`` and the two legacy prefixes; this module read
the bare name. Those are not the same variable, so a deployment keyed the old way would
silently drop to the synthetic branch the moment this started asking ``Settings`` — the
opposite of the failure being fixed. Both spellings are honoured, in one function, so the
surface and the switch cannot disagree about which branch is going to run.
"""


def alpaca_is_keyed(settings: Settings | None = None) -> bool:
    """Whether this deployment can take the Alpaca L1 branch.

    Lifted out of :func:`select_depth_source` so there is exactly one reader of the fact.
    :attr:`~crocodile.core.capability.Impl.prov` is a *ceiling*, so ``depth``, ``slippage``
    and ``liquidity-depth`` all declare the keyed branch — and the surfaces then announced a
    method that never ran, because in a keyless deployment this returns the synthetic Yahoo
    source and stamps its records ``SYNTHETIC``/``yahoo_1m_vap``. ``banner_for`` suppresses
    ``DERIVED``, so a CLI operator's stderr was empty while the answer was modelled: exactly
    what ``warning_for``'s docstring says the generalised banner exists to prevent.

    The surfaces resolve the effective provenance by calling this, through
    :attr:`~crocodile.core.capability.Impl.fallback`. A predicate written at the surface
    instead would be a second reader of the environment, and two readers of one fact is how
    the announcement came to be wrong.
    """
    if settings is None:
        from crocodile.core.config import Settings as _Settings

        settings = _Settings.from_env()
    if all(getattr(settings, field, None) for field in _ALPACA_FIELDS):
        return True
    return all(os.environ.get(name) for name in _ALPACA_BARE_NAMES)


def select_depth_source(
    *,
    bins: int = 40,
    top_n: int = 10,
    method: str = "uniform",
    settings: Settings | None = None,
) -> DepthSource:
    """Return Alpaca L1 iff this deployment is keyed, else the keyless synthetic source.

    This is the 'upgrade without code change' switch: the user sets env vars only.

    ``settings`` is optional and defaults to the process environment, so every existing
    caller keeps working unchanged. Passing ``ctx.settings`` is what lets a capability and
    the surface announcing it read one configuration rather than two.
    """
    if alpaca_is_keyed(settings):
        from crocodile.equity.depth.alpaca_l1 import AlpacaL1DepthSource

        return AlpacaL1DepthSource()
    from crocodile.equity.depth.synthetic import SyntheticYahooDepthSource

    return SyntheticYahooDepthSource(bins=bins, top_n=top_n, method=method)
