"""What all three projections share: resolution, invocation, and the provenance envelope.

Everything here is surface-agnostic on purpose. A projector's own module should hold only
what is genuinely specific to its transport — how a parameter is spelled on a command
line, in a query string, in a JSON schema — because anything else in there is a fourth
copy of the capability list waiting to drift from the other three.

The one thing the surfaces are *allowed* to disagree about is trust, and
:func:`build_context` is where they say so.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any, Final

import msgspec

from crocodile.capabilities import load_all
from crocodile.core.capability import (
    REGISTRY,
    AssetClass,
    Capability,
    CapabilityContext,
    Impl,
    ReturnKind,
)
from crocodile.core.config import Settings
from crocodile.core.errors import CapabilityUnavailable
from crocodile.core.schema.provenance import Provenance, describe

if TYPE_CHECKING:  # pragma: no cover - annotations only
    from pathlib import Path

    import polars as pl

    from crocodile.core.store.catalog import Catalog

__all__ = [
    "NETWORK_ROW_LIMIT",
    "asset_class_option_values",
    "build_context",
    "build_params",
    "invoke",
    "params_schema",
    "payload",
    "provenance_block",
    "resolve",
    "resolve_asset_class",
    "warning_for",
    "wire_names",
]

NETWORK_ROW_LIMIT: Final = 10_000
"""Rows a network surface will materialise from a raw-SQL read.

The number the legacy REST server already used, on every one of its fifteen
``_*_MAX_LIMIT`` constants. It is one constant here because fifteen copies of one policy
is the shape this package exists to remove, and because a per-capability limit is a
per-capability branch in a projector.
"""


# ---------------------------------------------------------------------------
# Name resolution
# ---------------------------------------------------------------------------


def wire_names() -> dict[str, str]:
    """Every string the surfaces must answer to, mapped to the capability it names.

    Canonical names map to themselves; :attr:`Capability.aliases` map to their capability.
    Built fresh on each call rather than cached, because the registry is filled at import
    time by four batch modules and a cache would freeze whichever subset had loaded first.

    Raises:
        ValueError: two capabilities answer to one string. That is the original bug — two
            things behind one command — and it must not be resolvable by ordering, so it
            is refused rather than won by whichever declaration imported first.
    """
    load_all()
    names: dict[str, str] = {}
    for cap in REGISTRY.values():
        for spelling in (cap.name, *cap.aliases):
            existing = names.get(spelling)
            if existing is not None and existing != cap.name:
                raise ValueError(
                    f"{spelling!r} names both {existing!r} and {cap.name!r}; "
                    f"a capability has one name and an alias is a redirect to it"
                )
            names[spelling] = cap.name
    return names


def resolve(name: str) -> Capability:
    """Return the capability ``name`` refers to, following aliases.

    Raises:
        KeyError: no capability answers to that string.
    """
    resolved = wire_names().get(name)
    if resolved is None:
        raise KeyError(f"no capability named {name!r}")
    return REGISTRY[resolved]


# ---------------------------------------------------------------------------
# Asset class
# ---------------------------------------------------------------------------


def _sources_by_asset_class() -> dict[AssetClass, frozenset[str]]:
    """Which source names each market serves, from the two source registries.

    Imported here rather than at module scope: the equity provider factory pulls in five
    provider modules, and a surface that is only being asked to list its commands should
    not pay for that.
    """
    from crocodile.crypto.exchanges.factory import list_all_exchanges
    from crocodile.equity.providers.factory import list_providers

    return {
        AssetClass.CRYPTO: frozenset(list_all_exchanges()),
        AssetClass.EQUITY: frozenset(list_providers()),
    }


def resolve_asset_class(
    cap: Capability,
    *,
    explicit: AssetClass | None = None,
    symbol: str | None = None,
) -> AssetClass:
    """Decide which of ``cap.impls`` serves this request.

    In order, and the order is the point — each step is a *stronger* claim than the next:

    1. What the caller said. An explicit choice is never overridden.
    2. What the symbol says. A canonical symbol is ``source:RAW``, and the source
       registries know which market each source serves, so ``deribit:BTC-PERPETUAL`` is
       evidence rather than a guess. A source both registries claim resolves to neither:
       an overlap is a real ambiguity and picking one silently is how a request lands in
       the wrong market's implementation and comes back with plausible numbers.
    3. Whether there is a choice at all. A capability with one implementation — which is
       what every entry on ``PENDING_SYMMETRY`` looks like until Phase 3 — has nothing to
       decide.

    Anything else refuses, naming the option that settles it. Defaulting to crypto here
    would make every unrecognised equity symbol quietly return an empty crypto answer.

    Raises:
        ValueError: the asset class cannot be established, or was named explicitly and
            this capability does not implement it.
    """
    if explicit is not None:
        if explicit not in cap.impls:
            raise CapabilityUnavailable(
                cap.name,
                explicit.value,
                reason=f"implemented for {sorted(a.value for a in cap.impls)}",
            )
        return explicit

    if symbol and ":" in symbol:
        source = symbol.rsplit(":", 1)[0].strip().lower()
        claimed = [
            asset_class
            for asset_class, sources in _sources_by_asset_class().items()
            if source in sources and asset_class in cap.impls
        ]
        if len(claimed) == 1:
            return claimed[0]

    if len(cap.impls) == 1:
        return next(iter(cap.impls))

    raise ValueError(
        f"cannot tell which market {cap.name!r} should serve"
        + (f" for symbol {symbol!r}" if symbol else "")
        + f"; name it explicitly as one of {sorted(a.value for a in cap.impls)}"
    )


def asset_class_option_values() -> list[str]:
    """The accepted spellings of an explicit asset class, for help text and schemas."""
    return [a.value for a in AssetClass]


# ---------------------------------------------------------------------------
# Parameters
# ---------------------------------------------------------------------------


def params_schema(cap: Capability) -> dict[str, Any]:
    """Return ``cap.params`` as one inlined JSON Schema object.

    ``msgspec.json.schema`` returns a ``$ref`` into ``$defs``, which is correct JSON Schema
    and is what MCP publishes verbatim. A CLI building options and a REST server building
    query parameters both need the properties themselves, so the reference is followed here
    once instead of in two projectors.
    """
    schema = msgspec.json.schema(cap.params)
    ref = schema.get("$ref")
    if isinstance(ref, str):
        defs = schema.get("$defs", {})
        assert isinstance(defs, dict)
        return dict(defs[ref.rsplit("/", 1)[-1]])
    return dict(schema)


def build_params(cap: Capability, values: dict[str, Any]) -> Any:
    """Build ``cap.params`` from whatever the transport collected.

    ``strict=False`` because every transport here delivers strings: a query string has no
    integers, and a command line has no floats. Letting msgspec coerce against the declared
    types means the params struct is the single statement of what a parameter *is*, rather
    than each surface parsing to its own idea of it — which is how one capability came to
    accept ``period=14`` in three subtly different ways.

    ``None`` values are dropped rather than passed through, so a transport that represents
    "not supplied" as ``None`` gets the struct's own default instead of overwriting it with
    a null.

    Raises:
        ValueError: a value does not fit the declared schema, or a required one is missing.
            msgspec's own message names the field and the type, and is not reworded.
    """
    supplied = {key: value for key, value in values.items() if value is not None}
    try:
        return msgspec.convert(supplied, type=cap.params, strict=False)
    except msgspec.ValidationError as exc:
        raise ValueError(f"{cap.name}: {exc}") from exc


# ---------------------------------------------------------------------------
# Invocation
# ---------------------------------------------------------------------------


def build_context(
    catalog: Catalog,
    asset_class: AssetClass,
    *,
    settings: Settings | None = None,
    readonly: bool,
    row_limit: int | None,
) -> CapabilityContext:
    """Assemble the context for one invocation.

    ``readonly`` and ``row_limit`` are keyword-only and have no defaults on purpose. They
    are the whole of a surface's trust policy, and a default would let a new surface
    inherit one silently — which is the accident that gave ``query`` three behaviours
    across six stacks. Every caller states its own.
    """
    return CapabilityContext(
        catalog=catalog,
        settings=settings if settings is not None else Settings.from_env(),
        asset_class=asset_class,
        readonly=readonly,
        row_limit=row_limit,
    )


def implementation(cap: Capability, ctx: CapabilityContext) -> Impl:
    """Return the implementation serving ``ctx.asset_class``.

    Raises:
        CapabilityUnavailable: this asset class has no implementation. Raised rather than
            returning an empty result, because "no answer" and "the answer is nothing" are
            different and the merge already lost seven capabilities to that confusion.
    """
    impl = cap.impls.get(ctx.asset_class)
    if impl is None:
        raise CapabilityUnavailable(
            cap.name,
            ctx.asset_class.value,
            reason=f"implemented for {sorted(a.value for a in cap.impls)}",
        )
    return impl


def invoke(cap: Capability, ctx: CapabilityContext, params: Any) -> Any:
    """Call the implementation. The entire dispatch, and there is nothing else to it.

    This function is deliberately three lines. Everything a surface might be tempted to do
    around a call — coercing arguments, choosing the market, attaching provenance — is a
    separate function here so that no projector grows a reason to special-case one
    capability on its way through.
    """
    return implementation(cap, ctx).fn(ctx, params)


# ---------------------------------------------------------------------------
# The response envelope
# ---------------------------------------------------------------------------


def provenance_block(cap: Capability, ctx: CapabilityContext) -> dict[str, Any]:
    """The block every network response carries, describing how the answer was obtained.

    Derived from the declaration rather than measured per call: :attr:`Impl.prov` is a
    ceiling, and the per-record truth is on each record's own provenance tail where it can
    be measured. Publishing the ceiling is still worth doing — it is what lets a caller see
    that an equity depth answer is modelled *before* reading a single row.
    """
    impl = implementation(cap, ctx)
    block: dict[str, Any] = {
        "capability": cap.name,
        "asset_class": ctx.asset_class.value,
        "prov": impl.prov.value,
        "prov_basis": impl.basis,
        "method": describe(impl.basis),
    }
    if ctx.row_limit is not None:
        # Published so a truncated answer reads as a stated ceiling rather than as the
        # whole lake. The legacy REST server wrapped a LIMIT and said nothing.
        block["row_limit"] = ctx.row_limit
    return block


def warning_for(cap: Capability, ctx: CapabilityContext) -> str | None:
    """A human-readable warning when the answer is not a native observation.

    This generalises the banner the equity depth route already ships — "SYNTHETIC —
    relative volume-at-price from Yahoo 1m bars, not real resting liquidity" — which was
    written by hand for one endpoint and therefore existed for exactly one of the fifty-odd
    capabilities that can return modelled data. The text comes from the basis registration,
    so a new synthetic method is announced the moment it is registered rather than the
    moment someone remembers to write a banner for it.
    """
    impl = implementation(cap, ctx)
    if impl.prov is Provenance.NATIVE:
        return None
    return (
        f"{impl.prov.value.upper()} — {cap.name} for {ctx.asset_class.value} is not a "
        f"venue-reported observation. Its inputs rest on {impl.basis!r}: {_headline(impl.basis)}"
    )


def _headline(basis: str) -> str:
    """The first line of a basis's description, which is what fits in a banner.

    ``describe`` returns the registration's full docstring, several paragraphs for some
    bases. The wording of the sentence around it is careful for a reason: ``prov`` describes
    what the implementation *hands back* and ``basis`` names what its *inputs* were, and
    those genuinely differ — ``indicators`` is DERIVED from inputs whose basis is ``native``.
    Running the two together would have the banner announce a computed answer as a
    venue-reported one.
    """
    described = describe(basis).strip()
    return described.splitlines()[0] if described else "(no description registered)"


def _jsonable(value: Any) -> Any:
    """Make one cell safe to serialise.

    JSON has no ``NaN`` and no ``Infinity``. Polars produces both from ordinary analytics —
    a Bollinger band over a constant window, a ratio with a zero denominator — and
    ``json.dumps`` emits them as bare ``NaN`` tokens that most clients reject as malformed.
    ``None`` is the honest encoding: the number does not exist.
    """
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def payload(cap: Capability, result: Any) -> dict[str, Any]:
    """Shape a return value according to :attr:`Capability.returns`.

    A ``TABLE`` becomes ``rows``, a ``SCALAR`` becomes ``result``. The distinction is read
    off the declaration rather than off the value's Python type, which is what stops a
    one-row frame from being served as a table by one surface and as an object by another —
    ``slippage`` returns exactly that and is declared ``SCALAR``.
    """
    rows = _rows(result)
    if cap.returns is ReturnKind.TABLE:
        return {"rows": rows if rows is not None else result}
    if rows is not None:
        return {"result": rows[0] if rows else None}
    return {"result": result}


def _rows(result: Any) -> list[dict[str, Any]] | None:
    """Return ``result`` as JSON-safe row dicts, or ``None`` if it is not a frame."""
    to_dicts = getattr(result, "to_dicts", None)
    if to_dicts is None:
        return None
    frame: pl.DataFrame = result
    if frame.width == 0:
        # The Catalog empty-result contract: zero rows *and* zero columns.
        return []
    return [{key: _jsonable(value) for key, value in row.items()} for row in to_dicts()]


def data_dir_for(settings: Settings, override: Path | None) -> Path:
    """Where the lake is: what the caller said, else what the environment said."""
    return override if override is not None else settings.data_dir
