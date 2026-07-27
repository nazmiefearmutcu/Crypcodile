"""Capabilities describing market structure and reference data.

Owns ``list-exchanges``, ``markets``, ``universe``, ``census``, ``open-interest`` and
``depth`` — what exists to trade, where, and in what size, as opposed to what the lake
happens to hold about it.

Four of the six reach a venue rather than the lake, which makes this the batch where the
two questions below had to be answered once, out loud, instead of per declaration.

**Why every adapter here is synchronous, including the four whose work is not.**
``exchange_instruments``, ``top_symbols_by_volume``, ``market_census`` and
``DepthSource.snapshot`` are all ``async def``, while
:data:`~crocodile.core.capability.CapabilityFn` is one calling convention for three
surfaces — and the CLI projection is synchronous where the REST and MCP ones run on an
event loop. An ``async`` ``Impl.fn``
would therefore force every surface to branch on ``inspect.iscoroutinefunction`` — the
signature introspection that convention exists to refuse — and a surface that forgot would
not crash: it would hand the caller a coroutine object that never runs, which is a wrong
answer wearing a passing call. A capability that works on MCP and hangs on the CLI is worse
than one that is not declared, so the coroutine is driven to completion inside the adapter,
by :func:`_run_to_completion`, and what every surface gets back is a value.

**Why none of them needs a live client on the context.**
:class:`~crocodile.core.capability.CapabilityContext` carries a ``Catalog`` and a
``Settings`` and no client, and nothing here wants one: ``universe`` and ``census``
construct their own ``ccxt``/``aiohttp`` sessions inside the coroutine and close them
again, and ``depth`` asks
:func:`~crocodile.equity.depth.select.select_depth_source` for a source rather than being
handed one. ``open-interest`` reads the lake through ``ctx.catalog``, exactly as
``slippage`` does. The one thing that *is* read from outside the context is the pair of
Alpaca keys, and that read happens inside ``select_depth_source``; see :func:`depth`.

The equity halves of this family are where :data:`SPEC_METHODS
<crocodile.core.capability.SPEC_METHODS>` M2 and M3 land, and the block at the foot of this
module is where each of them is scheduled. ``list-exchanges`` needs no entry — both markets
can already answer it — and ``depth`` needs one the ledger cannot quite express, which is
argued there rather than left to be found.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine, Mapping
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Final

import msgspec
import polars as pl

from crocodile.core.capability import (
    PENDING_SYMMETRY,
    AssetClass,
    Capability,
    CapabilityContext,
    Impl,
    ReturnKind,
    declare,
)
from crocodile.core.schema.provenance import Provenance
from crocodile.core.schema.records import DepthProfile
from crocodile.core.util.time import now_ns
from crocodile.crypto import census as census_mod
from crocodile.crypto.analytics.oi_aggregator import aggregate_open_interest
from crocodile.crypto.exchanges import factory
from crocodile.crypto.instruments.registry import Kind
from crocodile.crypto.instruments.universe import (
    exchange_instruments,
    filter_instruments,
    top_symbols_by_volume,
)
from crocodile.equity.depth import select_depth_source
from crocodile.equity.providers import factory as provider_factory

__all__ = [
    "CENSUS",
    "DEPTH",
    "LIST_EXCHANGES",
    "MARKETS",
    "OPEN_INTEREST",
    "UNIVERSE",
    "CensusParams",
    "DepthParams",
    "ListExchangesParams",
    "MarketsParams",
    "OpenInterestParams",
    "UniverseParams",
]

_END_OF_TIME: Final[int] = 2**63 - 1
"""The open right-hand end of a nanosecond range, as the largest ``local_ts`` there is.

The crypto CLI spelled this ``9999999999999999999``, which is larger than a signed 64-bit
integer and only ever worked because DuckDB widened the literal. ``local_ts`` is an
``int64`` on every record, so the largest representable timestamp is the honest spelling of
"no upper bound" and cannot be a value the comparison silently promotes.
"""


def _run_to_completion[T](make: Callable[[], Coroutine[Any, Any, T]]) -> T:
    """Drive ``make()``'s coroutine to a value, from a caller that may or may not have a loop.

    The two callers this has to serve are the reason it is not a bare ``asyncio.run``. A CLI
    projection has no running loop, so ``asyncio.run`` is exactly right. A REST or MCP
    projection *is* a running loop, and ``asyncio.run`` raises ``RuntimeError`` there —
    which would make the same declaration work on one surface and fail on another, the
    asymmetry this registry exists to end.

    So a running loop is detected and the coroutine gets its own loop on a worker thread.
    That blocks the calling loop for the duration, which is a real cost and is stated rather
    than hidden: an async surface that does not want to pay it calls the adapter through
    ``asyncio.to_thread``, which puts it on a thread with no loop and takes the first
    branch. It cannot deadlock — every coroutine here builds its own ``aiohttp``/``ccxt``
    session inside the new loop and awaits nothing belonging to the outer one.

    ``make`` is a factory rather than a coroutine so the object is created on the thread
    that will await it, and so a failure before submission cannot leave a
    "coroutine was never awaited" warning behind instead of an error.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(make())

    def _in_its_own_loop() -> T:
        return asyncio.run(make())

    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(_in_its_own_loop).result()


class ListExchangesParams(msgspec.Struct, frozen=True):
    """No parameters: the answer is the whole registry or nothing.

    All three legacy surfaces took none — ``crypcodile list-exchanges``,
    ``GET /api/v1/exchanges`` and MCP ``list_registered_exchanges`` are each a bare read of
    :func:`crocodile.crypto.exchanges.factory.list_exchanges`. Filtering lives on
    ``markets``, which is the capability that has a venue universe worth filtering.
    """


class MarketsParams(msgspec.Struct, frozen=True):
    """Parameters for ``markets``. One struct, which the equity half inherits when M3 lands."""

    search: str | None = None
    """Case-insensitive substring filter on the venue id. ``None`` means every venue."""

    native_only: bool = False
    """Keep only venues served by a hand-written connector."""

    ccxt_only: bool = False
    """Keep only venues served by the universal ccxt connector.

    Set together with ``native_only`` this yields the *overlap* — venues that are both,
    where a request routes to the native connector. The CLI's two independent ``if`` blocks
    printed nothing at all for that combination, which is an artefact of rendering two
    sections rather than an answer; two filters over one row set compose.
    """


class UniverseParams(msgspec.Struct, frozen=True):
    """Parameters for ``universe``. One struct, which the equity half inherits when M3 lands.

    ``--symbols-only`` is absent: printing bare symbols instead of a table is how a CLI
    renders this answer, not a different answer.
    """

    exchange: str
    """Venue id, native or ccxt."""

    top: int | None = None
    """Rank by 24 h quote volume and return the top N, instead of enumerating."""

    quote: str | None = None
    """Quote-currency filter, e.g. ``USDT``. ``None`` means every quote.

    ``None`` rather than the ``"USDT"`` that
    :func:`~crocodile.crypto.instruments.universe.top_symbols_by_volume` defaults to, because
    that default is invisible to a caller who never named a quote: the CLI passed its own
    ``None`` straight through, and an unasked-for filter that silently empties a USD-quoted
    venue is the worse failure.
    """

    kinds: tuple[str, ...] = ()
    """Instrument kinds to keep: ``spot``/``perpetual``/``future``/``option``. Empty means
    every kind. An unrecognised name raises, rather than quietly matching nothing."""

    limit: int = 50
    """Cap on enumerated rows. Ignored when ``top`` is set, which caps itself."""


class CensusParams(msgspec.Struct, frozen=True):
    """Parameters for ``census``. One struct, which the equity half inherits when M3 lands.

    The CLI's ``--output`` and ``--json`` are absent on purpose: they name files to write,
    which is a surface's business, and ``render_terminal`` / ``render_html`` are pure
    functions of the returned snapshot that a projection calls on the way to a screen.
    Neither reads the market, so neither belongs in the schema three surfaces publish.
    """

    venues: tuple[str, ...] = ()
    """Venues to deep-enumerate. Empty means the module's curated majors."""

    coin_pages: int = 1
    """Pages of 250 coins to sample for the movers board."""


class OpenInterestParams(msgspec.Struct, frozen=True):
    """Parameters for ``open-interest``. One struct, which the equity half inherits at M2.

    ``symbols`` is where three surfaces disagreed on paper and one implementation decided
    the matter. The CLI took ``--symbol``, singular, documented as a substring filter; REST
    took ``symbols``, plural; the MCP handler annotated ``str | list[str]`` while its
    published ``inputSchema`` said ``{"type": "string"}``. Read
    :func:`~crocodile.crypto.analytics.oi_aggregator.aggregate_open_interest` and the
    disagreement collapses: it has only ever had **one** semantic, and it is neither of the
    two the names suggest. Every element is a case-insensitive *literal substring* pattern
    and the elements are OR-ed, so a list of symbols was never a list of symbol identities —
    it was a list of patterns, and a lone string was the one-element case.

    Both plural spellings were therefore broken in opposite directions. REST handed its
    whole query string over as a single pattern, so ``?symbols=BTC,ETH`` matched no symbol
    at all and returned an empty board that reads exactly like an empty lake. MCP's schema
    made the list arm unreachable from the wire, so the only callers who could use it were
    in-process Python ones. A tuple of patterns is what the implementation always was: the
    CLI's ``--symbol`` becomes the one-element case, REST's comma string becomes a real
    split, and MCP's array becomes something a client can actually send.
    """

    symbols: tuple[str, ...] = ()
    """Case-insensitive literal substring patterns, OR-ed. Empty means every symbol."""

    start_ns: int = 0
    """Inclusive lower bound on ``local_ts``."""

    end_ns: int = _END_OF_TIME
    """Inclusive upper bound on ``local_ts``.

    Together these default to the whole range, which is the CLI's behaviour, and not to
    REST's ``start=0, end=0`` — that pair returns nothing for every lake, so a caller who
    omitted the range was told the market has no open interest rather than being asked for
    one.
    """


class DepthParams(msgspec.Struct, frozen=True):
    """Parameters for ``depth``. One struct, which the crypto half inherits when it lands."""

    symbol: str
    """The ticker to snapshot."""

    method: str = "uniform"
    """Volume-at-price binning method: ``uniform``/``typical``/``close``. Consumed only by
    the synthetic source; the L1 source has one level per side and nothing to bin."""

    bins: int = 40
    """Price buckets the synthetic ladder is built from."""

    top_n: int = 10
    """Levels returned per side."""


def list_exchanges(ctx: CapabilityContext, params: ListExchangesParams) -> list[str]:
    """Return the crypto connectors this build registers. Never touches the lake.

    Distinct from ``catalog-exchanges``, which walks the ``source=`` partitions actually on
    disk. This one answers "what could I collect from", and the answer is a property of the
    build rather than of any market.
    """
    return factory.list_exchanges()


def list_providers(ctx: CapabilityContext, params: ListExchangesParams) -> list[str]:
    """Return the equity providers this build registers — the same question, other market.

    This is what makes ``list-exchanges`` symmetric today rather than scheduled. The two
    registries answer one question, "which sources can this build pull from", and the merge
    already ruled that a crypto venue and an equity vendor are the same kind of thing by
    collapsing ``exchange=`` and ``provider=`` into one ``source=`` partition key. The
    objection that a data vendor is not an exchange does not survive the crypto list either:
    it has carried ``coingecko``, an aggregator, since before the merge.
    """
    return provider_factory.list_providers()


_MARKETS_SCHEMA: Final[Mapping[str, pl.DataType]] = {
    "venue": pl.String(),
    "native": pl.Boolean(),
    "ccxt": pl.Boolean(),
}
"""Stated so an empty result is still a table with the columns a caller selects on."""


def markets(ctx: CapabilityContext, params: MarketsParams) -> pl.DataFrame:
    """Every venue this build can reach, tagged by which connector serves it.

    One row per venue with two independent flags rather than the CLI's two printed
    sections. A venue in both is not two rows: a request for that name routes to the
    hand-written connector, which is what ``native`` being true means, and what the CLI
    rendered as ``(native override)``.

    ``list_all_exchanges()`` is the row source rather than the union of the other two calls,
    so the "total reachable" number the CLI printed and the rows it printed can no longer
    come from two different unions.
    """
    native = set(factory.list_exchanges())
    ccxt = set(factory.list_ccxt_exchanges())
    needle = params.search.lower() if params.search is not None else None

    rows: list[dict[str, Any]] = []
    for venue in factory.list_all_exchanges():
        is_native, is_ccxt = venue in native, venue in ccxt
        if params.native_only and not is_native:
            continue
        if params.ccxt_only and not is_ccxt:
            continue
        if needle is not None and needle not in venue.lower():
            continue
        rows.append({"venue": venue, "native": is_native, "ccxt": is_ccxt})
    return pl.DataFrame(rows, schema=_MARKETS_SCHEMA)


_UNIVERSE_SCHEMA: Final[Mapping[str, pl.DataType]] = {
    "symbol": pl.String(),
    "kind": pl.String(),
    "base": pl.String(),
    "quote": pl.String(),
    "rank": pl.Int64(),
}
"""One schema across both of ``universe``'s branches, with nulls where a branch cannot see.

The ranked branch reads ``fetch_tickers``, which returns symbols and volumes and no
instrument metadata; the enumerating branch reads ``load_markets``, which returns metadata
and no ranking. Emitting two different column sets under one capability would make the
result unrenderable by a projection that does not know which flag was passed, so both
branches fill the same five columns and a null says "this path does not observe it" rather
than "this venue does not have it".
"""


def _kinds(names: tuple[str, ...]) -> set[Kind] | None:
    """Turn the wire spelling of the kind filter into the enum, or ``None`` for no filter.

    Raises:
        ValueError: naming the bad kind. The CLI mapped this to exit 1; silently matching
            nothing would make a typo look like a venue with no perpetuals.
    """
    if not names:
        return None
    try:
        return {Kind(name.lower()) for name in names}
    except ValueError as exc:
        raise ValueError(
            f"unknown instrument kind in {list(names)}: {exc}; "
            f"valid kinds are {sorted(k.value for k in Kind)}"
        ) from exc


def universe(ctx: CapabilityContext, params: UniverseParams) -> pl.DataFrame:
    """Enumerate — or volume-rank — one venue's tradable set. Live, off the venue's own API.

    Reaches the network and not the lake: ``load_markets`` and ``fetch_tickers`` are what a
    venue publishes about itself, which is why this capability can answer for a symbol that
    has never been collected. The coroutine is driven here rather than returned; see
    :func:`_run_to_completion`.
    """
    kinds = _kinds(params.kinds)
    top = params.top
    if top is not None:
        symbols = _run_to_completion(
            lambda: top_symbols_by_volume(params.exchange, top, quote=params.quote, kinds=kinds)
        )
        return pl.DataFrame(
            [
                {"symbol": symbol, "kind": None, "base": None, "quote": None, "rank": rank}
                for rank, symbol in enumerate(symbols, start=1)
            ],
            schema=_UNIVERSE_SCHEMA,
        )

    instruments = filter_instruments(
        _run_to_completion(lambda: exchange_instruments(params.exchange)),
        kinds=kinds,
        quote=params.quote,
    )
    return pl.DataFrame(
        [
            {
                "symbol": inst.symbol_raw,
                "kind": inst.kind.value,
                "base": inst.base,
                "quote": inst.quote,
                "rank": None,
            }
            for inst in instruments[: params.limit]
        ],
        schema=_UNIVERSE_SCHEMA,
    )


def census(ctx: CapabilityContext, params: CensusParams) -> dict[str, Any]:
    """Snapshot the whole crypto market — venues, coins, DeFi — from keyless public APIs.

    The clock is read here rather than taken as a parameter. ``market_census`` takes
    ``generated_ns`` so that it never reads the clock itself and stays deterministic under
    test; that is an argument for the *function's* signature, not for a field in a schema
    three surfaces publish, since when a snapshot was taken is not something a user chooses.
    """
    return _run_to_completion(
        lambda: census_mod.market_census(
            generated_ns=now_ns(),
            venues=list(params.venues) or None,
            coin_pages=params.coin_pages,
        )
    )


def open_interest(ctx: CapabilityContext, params: OpenInterestParams) -> pl.DataFrame:
    """Align stored open interest across venues with a forward fill. A pure argument shuffle.

    ``ctx.catalog`` rather than ``ctx.query``: the SQL here is fixed and internal to the
    aggregator, not a string a caller supplied, so there is nothing for the readonly guard
    to read. This is the same handoff ``slippage`` makes.
    """
    return aggregate_open_interest(
        ctx.catalog,
        list(params.symbols),
        params.start_ns,
        params.end_ns,
    )


def depth(ctx: CapabilityContext, params: DepthParams) -> DepthProfile:
    """A US-equity depth ladder, from real L1 when keyed and a modelled one when not.

    ``select_depth_source`` reads ``ALPACA_API_KEY``/``ALPACA_API_SECRET`` from the process
    environment rather than from ``ctx.settings``, which carries both. That is a deviation
    from the rule :class:`~crocodile.core.capability.CapabilityContext` states, and it is
    left as it is rather than papered over here: the switch is shared with the two legacy
    surfaces that still call it, and a copy of the decision in this adapter would be a
    second place for the two to disagree about which source is live. Rewiring it through
    ``Settings`` is one change in one function, and it belongs with the surface migration.

    The declaration's ``prov`` is the *ceiling* — what this implementation produces on its
    best day, which is the keyed Alpaca L1 branch — and never a reading of today's
    environment. Which of the two branches actually ran is on the returned record's own
    tail, where ``provenance_fields`` measured it: ``alpaca_l1`` scores how much of the top
    of book was quoted, ``yahoo_1m_vap`` how much of a session the profile was binned out
    of. Declaring the keyless floor instead would understate a keyed deployment exactly as
    badly as declaring the ceiling overstates a keyless one, and only one of the two is
    fixed by a field that is documented as a maximum.
    """
    source = select_depth_source(bins=params.bins, top_n=params.top_n, method=params.method)
    return _run_to_completion(lambda: source.snapshot(params.symbol))


LIST_EXCHANGES = declare(
    Capability(
        name="list-exchanges",
        summary="Registered source connectors this build can collect from. No lake, no network.",
        params=ListExchangesParams,
        returns=ReturnKind.TABLE,
        impls={
            # NATIVE for a list of connector names reads oddly until you read the formula:
            # `native` scores 1.0 because a value was "read rather than reconstructed", and
            # nothing about a registry is modelled. DERIVED would claim it was computed
            # from records in the lake, which is the one thing this capability promises not
            # to touch.
            AssetClass.CRYPTO: Impl(fn=list_exchanges, prov=Provenance.NATIVE, basis="native"),
            AssetClass.EQUITY: Impl(fn=list_providers, prov=Provenance.NATIVE, basis="native"),
        },
    )
)


MARKETS = declare(
    Capability(
        name="markets",
        summary="Every venue reachable from this build, native connector or ccxt.",
        params=MarketsParams,
        returns=ReturnKind.TABLE,
        impls={
            AssetClass.CRYPTO: Impl(fn=markets, prov=Provenance.NATIVE, basis="native"),
        },
    )
)


UNIVERSE = declare(
    Capability(
        name="universe",
        summary="Enumerate or volume-rank one venue's whole tradable instrument set.",
        params=UniverseParams,
        returns=ReturnKind.TABLE,
        impls={
            # The rows are the venue's own market list and its own 24 h volumes, fetched
            # live. Ranking sorts them; a sort adds no modelling, so the ceiling stays where
            # the venue put it.
            AssetClass.CRYPTO: Impl(fn=universe, prov=Provenance.NATIVE, basis="native"),
        },
    )
)


CENSUS = declare(
    Capability(
        name="census",
        summary="Live whole-market snapshot: venue market counts, coin universe, DeFi TVL.",
        params=CensusParams,
        returns=ReturnKind.SCALAR,
        impls={
            # DERIVED where `universe` is NATIVE, and the difference is real: this reports
            # counts, sums and dominance ratios computed across venues and endpoints, not
            # the rows any one of them published. Its inputs are all natively reported,
            # which is what `basis` names — the same split `indicators` makes.
            AssetClass.CRYPTO: Impl(fn=census, prov=Provenance.DERIVED, basis="native"),
        },
    )
)


OPEN_INTEREST = declare(
    Capability(
        name="open-interest",
        summary="Open interest aligned across venues with forward fill, from stored records.",
        params=OpenInterestParams,
        returns=ReturnKind.TABLE,
        impls={
            AssetClass.CRYPTO: Impl(fn=open_interest, prov=Provenance.DERIVED, basis="native"),
        },
    )
)


DEPTH = declare(
    Capability(
        name="depth",
        summary="Market-depth ladder around a reference price, real L1 when keyed.",
        params=DepthParams,
        returns=ReturnKind.SCALAR,
        impls={
            # The ceiling, argued in `depth`: `alpaca_l1` registers as DERIVED — every
            # number in the profile was quoted by the venue, but the venue quoted a top of
            # book rather than a ladder — and it is the best of the two branches
            # `select_depth_source` can take.
            AssetClass.EQUITY: Impl(fn=depth, prov=Provenance.DERIVED, basis="alpaca_l1"),
        },
    )
)


# ---------------------------------------------------------------------------
# The schedule for the halves that are missing
# ---------------------------------------------------------------------------
# Filled here rather than in `core/capability.py` for the reason the batch package exists:
# four agents write these in parallel, and one file would serialise them behind one set of
# merge conflicts. `declare()` and this assignment are both idempotent, so a module body
# that runs twice leaves the same ledger.
PENDING_SYMMETRY.update(
    {
        # An equity venue list and an equity instrument universe both fall out of the same
        # reference data, so both wait on the same method.
        "markets": "M3",
        "universe": "M3",
        # The census counts a venue universe and a coin universe. Its equity form counts
        # what M3 resolves.
        "census": "M3",
        "open-interest": "M2",
        # `depth` is the one entry whose direction the ledger cannot express, and it is
        # recorded here rather than left to be discovered. Every method in SPEC_METHODS
        # closes an *equity* gap; `depth`'s missing half is the **crypto** one, because the
        # equity half is what already ships. M6 — "equity depth from the synthetic VAP
        # ladder, upgraded by Alpaca L1 when keyed" — is a description of
        # `equity/depth/select.py`, which this module declares, so M6 is already spent and
        # cannot be what closes this.
        #
        # It is scheduled anyway, against the only method that names depth at all, because
        # the alternatives are worse in kind rather than in degree: IRREDUCIBLE would claim
        # a crypto order book cannot exist, and not declaring the capability at all is the
        # silent-absence failure the registry was built to end. The entry is honest about
        # being asymmetric and wrong about which direction; Phase 3's exit gate forces
        # somebody to look at it, which is the property that makes recording it better than
        # leaving it out.
        #
        # What closing it actually needs: a registered confidence formula for a ladder
        # sliced out of a stored venue book snapshot. `native` would claim the venue
        # published the ladder, `book_resample` measures a boundary lookahead this has none
        # of, and inventing a formula is exactly what the provenance registry forbids.
        "depth": "M6",
    }
)
