"""One declaration per capability, and the registry the symmetry gate reads.

Crypcodile expressed a single capability list three times: 5 499 lines of Typer CLI,
3 273 of FastAPI routes and 2 600 of MCP tool handlers — roughly 11 400 lines saying
one thing. Adding a second asset class to that shape means six monoliths and a promise
of full API symmetry that nobody can keep by hand. So a capability is declared once,
here, and Phase 2 makes the three surfaces projections of this registry rather than
three hand-maintained copies of it.

The promise being mechanised is: every capability available for one asset class is
available for the other under the same name and parameter schema, and always returns
data — where no free native equity source exists, a derived or synthetic method supplies
it while saying so. :data:`REGISTRY` is what makes the first half checkable and
:attr:`Impl.basis` is what makes the second half machine-readable.

Phase 1 ships this with one real capability rather than empty on purpose: a symmetry gate
over an empty registry is vacuously green, which is the same as not having one.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum
from typing import Any, Final

import msgspec

from crocodile.core.analytics.indicators import apply_indicators
from crocodile.core.schema.enums import AssetClass
from crocodile.core.schema.provenance import Provenance

__all__ = [
    "IRREDUCIBLE",
    "REGISTRY",
    "AssetClass",
    "Capability",
    "Impl",
    "IndicatorParams",
    "ReturnKind",
    "register",
]


class ReturnKind(StrEnum):
    """The shape a capability returns, which is what a surface needs to render it.

    A CLI prints a ``TABLE`` as rows and a ``SCALAR`` as one line; REST pages the first
    and not the second; MCP has to decide whether the result is a content block or a
    subscription. Encoding it here means each surface derives that once instead of
    carrying its own per-endpoint knowledge of the answer.
    """

    TABLE = "table"
    """Zero or more rows. The common case; an empty result is still a table."""

    SCALAR = "scalar"
    """A single value or object, not a row set of length one."""

    STREAM = "stream"
    """An unbounded sequence a caller subscribes to rather than requests."""


class Impl(msgspec.Struct, frozen=True):
    """How one asset class satisfies a capability."""

    fn: Callable[..., Any]
    """The callable that does the work."""

    prov: Provenance
    """The best level this implementation can produce.

    A ceiling, not a per-call measurement: an implementation that models depth from bars
    is :attr:`Provenance.SYNTHETIC` on its best day. The per-record value comes from
    ``provenance_fields()``, never from a number written at a call site.
    """

    basis: str
    """The registered method this implementation's inputs rest on.

    A key into the provenance registry in :mod:`crocodile.core.schema.provenance`, which
    is what Gate 3 checks: a basis with no registered confidence formula is a confidence
    number chosen by feel waiting to happen. Note this names the *inputs* — ``indicators``
    declares ``native`` because both asset classes report OHLCV natively — while
    :attr:`prov` describes what the implementation hands back.
    """


class Capability(msgspec.Struct, frozen=True):
    """One capability, declared once for both asset classes."""

    name: str
    """The wire name. The same string names the CLI command, the REST path segment and
    the MCP tool, so the three surfaces cannot drift apart on spelling."""

    summary: str
    """One line, reused as CLI help, OpenAPI summary and MCP tool description."""

    params: type[msgspec.Struct]
    """The parameter schema, shared by every asset class implementing this capability.

    One struct, not one per asset class: identical parameter schemas are half of what
    "full API symmetry" means, and two structs would let them diverge silently. Phase 2
    projects this to an MCP ``inputSchema`` via ``msgspec.json.schema``, to Typer options
    and to FastAPI query parameters.
    """

    returns: ReturnKind
    """See :class:`ReturnKind`."""

    impls: dict[AssetClass, Impl]
    """One entry per asset class this capability serves.

    Missing an asset class is a build failure unless the name appears in
    :data:`IRREDUCIBLE`.
    """


REGISTRY: Final[dict[str, Capability]] = {}
"""Every declared capability, keyed by name. Populated at import time; see :func:`register`."""


IRREDUCIBLE: Final[dict[str, str]] = {
    "gas-tracker": "L1/L2 gas markets have no equity analogue.",
    "gas-vol": "Correlates price volatility with gas prices; gas is chain-native.",
    "mev-sandwich": "Requires a public mempool and atomic transaction ordering.",
    "sequencer-latency": "Measures an L2 sequencer; equities have no sequencer.",
    "peg-deviation": "Stablecoin peg mechanics; no equity instrument behaves this way.",
    "lending-stress": "On-chain lending-pool utilisation and liquidation thresholds.",
}
"""The only way a capability escapes the symmetry gate, and the bar for getting on it.

The value is the *argument* for why no equity analogue can exist — a property of the
market, not of the schedule. "Not built yet" is not a valid reason and neither is "no
free data source": the product promise is that a derived or synthetic method supplies
the data while saying so, so an absent source is a reason to declare a
:attr:`Provenance.SYNTHETIC` implementation, not to claim irreducibility. Adding a name
here silences a build failure, which is exactly why the justification is mandatory and
exactly why an empty one is itself a build failure.
"""


def register(cap: Capability) -> Capability:
    """Add ``cap`` to :data:`REGISTRY`.

    Returns the capability so a declaration can be bound to a name at the call site.

    Raises:
        ValueError: if the name is already taken. Two modules claiming one name is how
            the three surfaces would end up projecting different things under the same
            command, which is the failure this registry exists to prevent.
    """
    if cap.name in REGISTRY:
        raise ValueError(f"capability {cap.name!r} is already registered")
    REGISTRY[cap.name] = cap
    return cap


_BUILTIN_NAMES: Final[set[str]] = set()


def _install(cap: Capability) -> Capability:
    """Register a built-in declared by this module, tolerating a second call.

    :func:`register` is strict, and it has to be. But the seeding below runs at import
    time, and ``load_all_bases()`` walks the whole package catching ``Exception`` into a
    ``RuntimeWarning`` — so a ``ValueError`` raised here would not fail loudly, it would
    degrade into a registry that is quietly missing whatever came after it. Re-installing
    a name this module already owns therefore replaces it instead of raising, while a
    *different* module claiming the same name still goes through :func:`register` and
    still fails hard.
    """
    if cap.name in _BUILTIN_NAMES:
        REGISTRY[cap.name] = cap
        return cap
    _BUILTIN_NAMES.add(cap.name)
    return register(cap)


class IndicatorParams(msgspec.Struct, frozen=True):
    """Parameters for ``indicators``, identical for both asset classes."""

    symbol: str
    start_ns: int
    end_ns: int
    interval: str = "1d"
    indicator: str | None = None
    period: int = 14


_install(
    Capability(
        name="indicators",
        summary="Moving averages, RSI, MACD and Bollinger bands over stored OHLCV.",
        params=IndicatorParams,
        returns=ReturnKind.TABLE,
        impls={
            # One function serves both: its input is OHLCV, which both asset classes
            # produce natively. This is the walking skeleton that keeps the symmetry gate
            # honest before the real work of Phase 3 — a gate whose only subject is a
            # capability contrived to satisfy it proves nothing.
            AssetClass.CRYPTO: Impl(fn=apply_indicators, prov=Provenance.DERIVED, basis="native"),
            AssetClass.EQUITY: Impl(fn=apply_indicators, prov=Provenance.DERIVED, basis="native"),
        },
    )
)
