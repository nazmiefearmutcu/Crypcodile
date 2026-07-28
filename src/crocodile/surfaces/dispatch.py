"""What all three projections share: resolution, invocation, and the provenance envelope.

Everything here is surface-agnostic on purpose. A projector's own module should hold only
what is genuinely specific to its transport — how a parameter is spelled on a command
line, in a query string, in a JSON schema — because anything else in there is a fourth
copy of the capability list waiting to drift from the other three.

The one thing the surfaces are *allowed* to disagree about is trust, and
:func:`build_context` is where they say so.
"""

from __future__ import annotations

import asyncio
import inspect
import itertools
from collections.abc import Iterator, Sequence
from types import NoneType, UnionType
from typing import TYPE_CHECKING, Any, Final, Literal, Union, get_args, get_origin

import duckdb
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
from crocodile.core.util.json_safe import json_safe_float

if TYPE_CHECKING:  # pragma: no cover - annotations only
    from pathlib import Path

    import polars as pl

    from crocodile.core.store.catalog import Catalog

__all__ = [
    "BAD_REQUEST",
    "NETWORK_ROW_LIMIT",
    "REFUSED",
    "UNAVAILABLE",
    "asset_class_option_values",
    "build_context",
    "build_params",
    "drive",
    "invoke",
    "params_schema",
    "payload",
    "provenance_block",
    "resolve",
    "resolve_asset_class",
    "stream_summary",
    "structured_fields",
    "symbol_hints",
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
# Whose fault a failure is
# ---------------------------------------------------------------------------
#
# Three categories, declared once and read by all three projectors, because the question
# they are answering is the same one in three vocabularies: a status code, an exit code, a
# JSON-RPC result. Anything not named here is *ours* — a 500, a traceback, a protocol error
# — and that default is the point. The legacy REST server caught ``Exception`` and answered
# 400, which is the opposite failure: a lake that cannot be read reported as a bad request
# tells the caller to fix a query that was fine, and pages nobody.

UNAVAILABLE: Final[tuple[type[BaseException], ...]] = (CapabilityUnavailable,)
"""This capability has no implementation for the asset class that was resolved.

Not the caller's mistake and not a fault: the request is well formed and the answer does
not exist yet, which is 501 on REST.
"""

REFUSED: Final[tuple[type[BaseException], ...]] = (PermissionError,)
"""The request is fine and this surface is not trusted to run it.

``capabilities.ops._refuse_readonly`` chose ``PermissionError`` over ``ValueError``
precisely so this category could exist — its docstring says "a REST projection maps [it] to
403 and a caller must not retry" — and then no surface caught it, so a deliberate policy
refusal was served as a 500 and became indistinguishable from a crash. A crash invites a
retry; this must not be retried, because nothing about it will be different next time.
"""

BAD_REQUEST: Final[tuple[type[BaseException], ...]] = (
    ValueError,
    duckdb.ProgrammingError,
    duckdb.DataError,
)
"""The caller asked for something that cannot be answered as asked.

``ValueError`` is what an implementation raises for an unknown indicator or a symbol with
no stored book, and what ``build_params`` raises for a value that does not fit the schema.

The two DuckDB families are the ``query`` capability's, and they are named rather than
caught wholesale: DB-API says ``ProgrammingError`` is a statement problem (a missing table,
a syntax error, a bad column) and ``DataError`` a value problem, while ``OperationalError``
— ``IOException``, ``OutOfMemoryException`` — is the environment's. Only the first two are
the caller's. Before this, ``duckdb.CatalogException`` was neither ``CrocodileError`` nor
``ValueError``, so every user SQL typo answered 500: alerting fires, and every client that
backs off on 5xx retries a statement that will never compile.
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


_SYMBOL_FIELDS: Final = frozenset({"symbol", "symbols"})
"""The params fields that carry a canonical symbol, and therefore evidence about a market.

Named rather than sniffed. Reading *any* string containing a colon would let
``SELECT * FROM t WHERE note = 'binance:x'`` choose which implementation runs ``query``,
which is a request landing in a market because of a string literal.

Both spellings, because the registry uses both and they mean the same thing: ``symbol`` is
one and ``symbols`` is a set of them. Consulting only the singular is what made six
two-implementation capabilities — ``catalog-scan``, ``resolve-symbols``, ``replay``,
``export``, ``backfill``, ``collect`` — unreachable without an ``--asset-class`` the symbol
had already determined.
"""


def symbol_hints(params: Any) -> tuple[str, ...]:
    """Every canonical symbol a built request carries, for :func:`resolve_asset_class`.

    Read off the *built params struct* rather than off the raw request, which is what lets
    one rule cover three transports: by this point a sequence is a sequence, whether it
    arrived as a JSON list, a repeated flag or a comma-separated query parameter, and the
    surfaces no longer each need their own idea of how ``symbols`` is spelled.
    """
    found: list[str] = []
    for field in msgspec.structs.fields(params):
        if field.name not in _SYMBOL_FIELDS:
            continue
        value = getattr(params, field.name, None)
        if isinstance(value, str):
            found.append(value)
        elif isinstance(value, (list, tuple, set, frozenset)):
            found.extend(item for item in value if isinstance(item, str))
    return tuple(found)


def resolve_asset_class(
    cap: Capability,
    *,
    explicit: AssetClass | None = None,
    symbols: Sequence[str] = (),
) -> AssetClass:
    """Decide which of ``cap.impls`` serves this request.

    In order, and the order is the point — each step is a *stronger* claim than the next:

    1. What the caller said. An explicit choice is never overridden.
    2. What the symbols say. A canonical symbol is ``source:RAW``, and the source
       registries know which market each source serves, so ``deribit:BTC-PERPETUAL`` is
       evidence rather than a guess. A source both registries claim resolves to neither —
       ``alpaca`` is a crypto exchange *and* an equity provider — because an overlap is a
       real ambiguity and picking one silently is how a request lands in the wrong market's
       implementation and comes back with plausible numbers. Symbols that name *different*
       markets refuse for the same reason and more sharply: one request has one
       implementation, so resolving by position would send the equity symbols into the
       crypto one, which answers plausibly and empty.
    3. Whether there is a choice at all. A capability with one implementation — which is
       what every entry on ``PENDING_SYMMETRY`` looks like until Phase 3 — has nothing to
       decide.

    Anything else refuses, naming the option that settles it. Defaulting to crypto here
    would make every unrecognised equity symbol quietly return an empty crypto answer.

    Raises:
        ValueError: the asset class cannot be established, or the symbols disagree about it.
        CapabilityUnavailable: an asset class was named explicitly and this capability does
            not implement it.
    """
    if explicit is not None:
        if explicit not in cap.impls:
            raise CapabilityUnavailable(
                cap.name,
                explicit.value,
                reason=f"implemented for {sorted(a.value for a in cap.impls)}",
            )
        return explicit

    by_asset_class = _sources_by_asset_class()
    claimed: set[AssetClass] = set()
    for symbol in symbols:
        if not symbol or ":" not in symbol:
            continue
        source = symbol.rsplit(":", 1)[0].strip().lower()
        matched = [
            asset_class
            for asset_class, sources in by_asset_class.items()
            if source in sources and asset_class in cap.impls
        ]
        if len(matched) == 1:
            claimed.add(matched[0])
    if len(claimed) == 1:
        return next(iter(claimed))
    if len(claimed) > 1:
        raise ValueError(
            f"{cap.name!r} was given symbols from two markets "
            f"({sorted(a.value for a in claimed)}): {list(symbols)}. One request is served "
            f"by one implementation, so split it rather than have one market answer for both"
        )

    if len(cap.impls) == 1:
        return next(iter(cap.impls))

    raise ValueError(
        f"cannot tell which market {cap.name!r} should serve"
        + (f" for symbols {list(symbols)}" if symbols else "")
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

    A string arriving for a *sequence of scalars* is split on commas, because a command line
    and a query string have no lists either. Without this, fifteen of the registry's
    capabilities were unreachable from two of their three surfaces: ``--symbols BTCUSDT`` and
    ``?symbols=BTC`` both failed with ``Expected 'array', got 'str'``, which is a capability
    that is declared, projected, listed by Gate 4 and impossible to call. Comma separation is
    not invented here — it is what both forks' REST servers and both CLIs took, and what
    ``_PARAM_RENAMES`` in the surface-parity gate already documents for ``open-interest``.

    A string arriving for a *structured* field is JSON. Splitting ``[{"a": 1}, {"b": 2}]`` on
    commas produces ``['[{"a"', '1}', '{"b"', '2}]']``, which is the same unreachability one
    step further along: ``gas-vol``, ``mev-sandwich``, ``smart-money`` and ``label-transfers``
    take arrays of objects, and a transport that can only hand over text — a command line —
    has no other way to spell one. What those four take is a JSON document either way; this
    only says so where the transport lost the type.

    Raises:
        ValueError: a value does not fit the declared schema, a required one is missing, or a
            structured field was handed text that is not JSON. msgspec's own message names
            the field and the type, and is not reworded.
    """
    sequences = _sequence_fields(cap.params)
    structured = structured_fields(cap)
    supplied = {
        key: _from_text(cap, key, value, sequence=key in sequences, structured=key in structured)
        for key, value in values.items()
        if value is not None
    }
    try:
        return msgspec.convert(supplied, type=cap.params, strict=False)
    except msgspec.ValidationError as exc:
        raise ValueError(f"{cap.name}: {exc}") from exc


def _from_text(
    cap: Capability, key: str, value: Any, *, sequence: bool, structured: bool
) -> Any:
    """Recover a value a text-only transport had to flatten. Anything else passes through."""
    if not isinstance(value, str):
        return value
    if structured:
        try:
            return msgspec.json.decode(value)
        except msgspec.DecodeError as exc:
            raise ValueError(
                f"{cap.name}: {key} takes a JSON document and {value!r} is not one: {exc}"
            ) from exc
    if sequence:
        return [part.strip() for part in value.split(",") if part.strip()]
    return value


def _sequence_fields(params: type[msgspec.Struct]) -> frozenset[str]:
    """Field names whose declared type is a sequence rather than a scalar.

    Read off the annotation rather than guessed from the name: ``symbols`` is a list and
    ``symbol`` is not, but so is ``rates``, and ``sql`` is a string that would be ruined by
    a split on commas.
    """
    found: set[str] = set()
    for field in msgspec.structs.fields(params):
        for candidate in (field.type, *get_args(field.type)):
            origin = get_origin(candidate) or candidate
            if isinstance(origin, type) and issubclass(origin, (list, tuple, set, frozenset)):
                found.add(field.name)
    return frozenset(found)


def structured_fields(cap: Capability) -> frozenset[str]:
    """Field names whose declared type has no faithful spelling in a URL.

    A scalar is spellable, and so is a sequence of scalars: ``?symbols=BTC,ETH`` is a
    convention both forks already served. An array of *objects* is not — ``?trades=`` would
    have to carry an escaped JSON document through a length limit, an access log and a
    browser history — and neither is a mapping.

    This is the whole of what tells a projection that a capability wants a body. It is read
    off the parameter declaration because that is the only place the answer exists: a list of
    capability names in a projector would be the fourth copy of the registry this package
    exists to remove, and it would be wrong the first time a parameter changed type.
    """
    return frozenset(
        field.name
        for field in msgspec.structs.fields(cap.params)
        if not _url_expressible(field.type)
    )


def _url_expressible(annotation: Any) -> bool:
    """Whether a value of this type can be written in a query string or on a command line."""
    origin = get_origin(annotation)
    if origin is not None and origin in (list, tuple, set, frozenset):
        return all(_scalar(arg) for arg in get_args(annotation) if arg is not Ellipsis)
    if _is_union(annotation):
        return all(_url_expressible(arg) for arg in get_args(annotation) if arg is not NoneType)
    return _scalar(annotation)


def _scalar(annotation: Any) -> bool:
    """Whether this is a single value: a string, a number, a flag, or a literal among them.

    ``Literal`` counts because a ``Literal["error", "first"]`` is spelled as one of its
    strings, and a ``StrEnum`` counts because it *is* a ``str`` — which is what makes the
    check ``issubclass`` rather than an identity test against a tuple of types.
    """
    if get_origin(annotation) is Literal:
        return True
    if _is_union(annotation):
        return all(_scalar(arg) for arg in get_args(annotation) if arg is not NoneType)
    return isinstance(annotation, type) and issubclass(annotation, (str, int, float, bool))


def _is_union(annotation: Any) -> bool:
    """``X | None`` and ``Union[X, None]`` are the same thing and are spelled two ways."""
    return get_origin(annotation) in (Union, UnionType)


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


def drive(result: Any, *, row_limit: int | None) -> Any:
    """Finish anything a capability handed back unstarted or unconsumed.

    Three return shapes in the registry are *work* rather than an answer, and all three are
    deliberate: an implementation cannot know whether its caller already owns an event loop,
    and ``asyncio.run`` from inside a running one raises — so ``backfill`` returns an
    unstarted coroutine, a ``STREAM`` returns an unstarted
    :class:`~crocodile.capabilities.ops.Subscription`, and ``replay`` returns a lazy iterator
    because the whole point of its k-way merge is to stay O(channels) in memory.

    This lives here rather than in one projector because the hazard is not one surface's.
    The first two were fixed inside the CLI, and that is exactly why the third shipped
    unnoticed on all three at once: ``replay`` printed ``<itertools.islice object at 0x…>``
    and exited **0** on the CLI, answered 500 on REST, and failed ``json.dumps`` on MCP.
    A zero exit code over work that never ran is the quietest possible failure, and a
    projection that answers it in one place answers it once.

    ``row_limit`` bounds the materialisation, because a lazy result is only safe on a network
    surface while somebody bounds it: ``replay`` reads through :meth:`Catalog.scan` rather
    than :meth:`CapabilityContext.query`, so the ``LIMIT`` wrapper that caps raw SQL never
    sees it and draining the iterator is how one request materialises a lake. ``None`` — the
    CLI's posture, on the machine that owns the lake — drains it all.
    """
    begin = getattr(result, "run", None)
    if callable(begin) and not isinstance(result, type):
        return asyncio.run(begin())
    if inspect.iscoroutine(result):
        return asyncio.run(result)
    if isinstance(result, Iterator):
        # ``Iterator`` and not ``Iterable``: a str, a dict, a list and a polars frame are all
        # iterable and none of them is unconsumed work. What is being caught here is the
        # generator/islice family, which has a ``__next__`` and one shot at being read.
        return list(result) if row_limit is None else list(itertools.islice(result, row_limit))
    return result


def stream_summary(pending: Any) -> dict[str, Any] | None:
    """Describe an unstarted subscription, or ``None`` if this is not one.

    A ``STREAM`` run returns ``None`` when it finishes — there is no last element to report —
    so a surface that renders only the return value prints ``None`` after an hour of
    collection and says nothing about what it collected. Everything worth reporting is known
    *before* the run starts, which is the whole reason a ``Subscription`` exists, so it is
    read off here while it still exists.

    Duck-typed rather than an ``isinstance`` against
    :class:`~crocodile.capabilities.ops.Subscription`: importing that module here would drag
    every connector and every analytics dependency into a surface that is often only being
    asked to list its commands, which is the cost ``dispatch`` defers everywhere else.
    """
    sources = getattr(pending, "sources", None)
    channels = getattr(pending, "channels", None)
    if not callable(getattr(pending, "run", None)) or sources is None or channels is None:
        return None
    return {
        "sources": list(sources),
        "channels": list(channels),
        "duration_seconds": getattr(pending, "duration_seconds", None),
    }


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
    """Make one cell safe to serialise, on every surface's encoder rather than on one.

    JSON has no ``NaN`` and no ``Infinity``. Polars produces both from ordinary analytics —
    a Bollinger band over a constant window, a ratio with a zero denominator — and
    ``json.dumps`` emits them as bare ``NaN`` tokens that most clients reject as malformed.
    ``None`` is the honest encoding: the number does not exist. The rule itself is
    :func:`crocodile.core.util.json_safe.json_safe_float` rather than an ``isfinite`` written
    here. Both deleted REST servers and both deleted MCP servers re-exported that function
    precisely so there would be one answer, and a fourth copy in the projection would undo
    what the re-export was for.

    Narrowing only ``float`` was not enough, and the gap divided the surfaces exactly the way
    ``DepthProfile`` did. Every lake read carries a ``date`` cell — the partition column — and
    FastAPI's encoder knows what to do with one while ``json.dumps`` does not, so
    ``catalog-scan`` answered 200 on REST and raised ``Object of type date is not JSON
    serializable`` on MCP. A ``date``, a ``Decimal``, a ``UUID`` and a nested Struct are all
    values a capability may legitimately return; which of them a given transport happens to
    understand is not something a caller should have to know.

    ``msgspec.to_builtins`` supplies the conversion for anything that is not already a JSON
    primitive, and the walk continues into the result so a non-finite float *inside* a
    converted structure is still caught. Sequence types are preserved rather than flattened
    to lists: both encoders write a tuple as an array, so changing it would only churn the
    shape a caller sees.
    """
    if isinstance(value, float):
        return json_safe_float(value)
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        converted = [_jsonable(item) for item in value]
        return tuple(converted) if isinstance(value, tuple) else converted
    builtin = msgspec.to_builtins(value)
    # ``to_builtins`` returns a str for a date and a dict for a Struct, so one more pass
    # finishes the walk. The type check is what makes that pass terminate rather than
    # recur forever on a value msgspec handed straight back.
    return _jsonable(builtin) if type(builtin) is not type(value) else builtin


def payload(cap: Capability, result: Any) -> dict[str, Any]:
    """Shape a return value according to :attr:`Capability.returns`.

    A ``TABLE`` becomes ``rows``, a ``SCALAR`` becomes ``result``. The distinction is read
    off the declaration rather than off the value's Python type, which is what stops a
    one-row frame from being served as a table by one surface and as an object by another —
    ``slippage`` returns exactly that and is declared ``SCALAR``.

    Whatever comes out is plain JSON data all the way down, because the three surfaces do not
    share an encoder and the one thing they must share is what they are asked to encode.
    """
    rows = _rows(result)
    if cap.returns is ReturnKind.TABLE:
        return {"rows": rows if rows is not None else _encodable(cap, result)}
    if rows is not None:
        return {"result": rows[0] if rows else None}
    return {"result": _encodable(cap, result)}


def _encodable(cap: Capability, result: Any) -> Any:
    """Render a result as plain data, or say loudly that it cannot be.

    ``depth`` returns a ``DepthProfile``, which is a Struct and is this codebase's wire type
    — but only for *msgspec's* encoder. FastAPI serialises with pydantic, which refuses an
    unknown type, so the route answered 500 with ``Unable to serialize unknown type:
    DepthProfile`` while the CLI printed it happily. A capability that works on one surface
    and 500s on another is the divergence this projection exists to end, so the conversion
    is here — once, for every surface — rather than in the REST handler.

    ``to_builtins`` and not ``json.encode``: the result has to stay a Python object for the
    CLI to render and for MCP to embed, and it is msgspec's own recursive walk, so a Struct
    nested inside a dict or a list is converted too.

    The failure is **raised**. Swallowing it and handing the object back is what let a lazy
    ``replay`` reach three different callers as three different symptoms — an ``islice`` repr
    on stdout under exit 0, a 500 with no detail, and a JSON-RPC internal error — none of
    which names the capability or the type. Anything unstarted is :func:`drive`'s to finish
    before it gets here, so reaching this branch means the projection has a bug, and a bug
    says so where it happens.

    Raises:
        TypeError: the result is not encodable by any surface.
    """
    try:
        return _jsonable(msgspec.to_builtins(result))
    except (TypeError, NotImplementedError) as exc:
        raise TypeError(
            f"{cap.name} returned a {type(result).__name__}, which no surface can encode; "
            f"a capability returns data, and work handed back unstarted must be driven to "
            f"completion before it reaches the envelope"
        ) from exc


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
