"""Capabilities that answer questions about the lake itself.

Owns ``query``, ``catalog``, ``search``, the ``catalog-*`` family (``catalog-channels``,
``catalog-dates``, ``catalog-summary``, ``catalog-stats``, ``catalog-inventory``,
``catalog-scan``, ``catalog-symbols``, ``catalog-exchanges``), ``data-coverage`` and
``resolve-symbols``.

Every capability here is lake-shaped, so every one of them is symmetric: the answer comes
from :class:`~crocodile.core.store.catalog.Catalog`, which reads whatever is on disk
without caring which market wrote it. That is why each declaration below names the same
adapter for both asset classes and why nothing here is on ``PENDING_SYMMETRY``.

Three things this module deliberately does not do.

**It does not hold an SQL policy.** ``query`` shipped three of them — the crypto CLI called
``Catalog.query`` with no guard, REST scanned for 19 mutating keywords and wrapped a
``LIMIT``, the equity MCP passed ``readonly=True`` — because each surface decided at its own
call site. Deciding per call site is also how the *crypto* MCP, a network-facing surface,
came to pass no guard at all where its equity twin passed one: not a fourth policy, the
first one again, in the place it costs most. The implementation reads
:meth:`CapabilityContext.query
<crocodile.core.capability.CapabilityContext.query>` and nothing else; the surface sets
``readonly`` and ``row_limit`` when it builds the context. A ``readonly`` field in a params
struct would put the choice back in the caller's hands, which is how a network endpoint
ends up trusting whoever is talking to it.

**It does not reach for a client.** The context hands over a live ``Catalog``, and
constructing a :class:`~crocodile.crypto.client.client.CrypcodileClient` from
``ctx.settings.data_dir`` to get at its wrappers would open a second DuckDB connection over
a lake the surface did not necessarily point at. The three client methods that are more
than a delegation — ``catalog_summary``, ``list_symbols``, ``resolve_symbols`` — are
reproduced here over the catalog they always actually used, and
``tests/capabilities/test_catalog.py`` asserts row-for-row that the two agree. Diffing the
arithmetic rather than the signature is the only thing that catches a fork.

**It does not fold a flag into a capability.** ``crypto catalog --symbols`` switches that
command into printing ``client.inventory()`` — the same call, the same columns and the same
rendering as ``catalog-inventory``. A boolean that turns one command into another is two
capabilities sharing a name, so the flag is not carried and ``catalog-inventory`` is what
answers it.

The adapters are module-level and named, matching :mod:`crocodile.capabilities.analytics`:
a stack trace and the calling-convention gate both need something with a file and a line.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

import msgspec
import polars as pl

from crocodile.core.capability import (
    AssetClass,
    Capability,
    CapabilityContext,
    CapabilityFn,
    Impl,
    ReturnKind,
    declare,
)
from crocodile.core.schema.provenance import Provenance

if TYPE_CHECKING:  # pragma: no cover - annotations only
    from crocodile.core.store.catalog import Catalog

__all__ = [
    "CATALOG",
    "CATALOG_CHANNELS",
    "CATALOG_DATES",
    "CATALOG_EXCHANGES",
    "CATALOG_INVENTORY",
    "CATALOG_SCAN",
    "CATALOG_STATS",
    "CATALOG_SUMMARY",
    "CATALOG_SYMBOLS",
    "DATA_COVERAGE",
    "QUERY",
    "RESOLVE_SYMBOLS",
    "SEARCH",
    "AmbiguousMode",
    "CatalogFilterParams",
    "ChannelParams",
    "DataCoverageParams",
    "NoParams",
    "QueryParams",
    "ResolveSymbolsParams",
    "ScanParams",
    "SearchParams",
    "catalog",
    "catalog_channels",
    "catalog_dates",
    "catalog_exchanges",
    "catalog_inventory",
    "catalog_scan",
    "catalog_stats",
    "catalog_summary",
    "catalog_symbols",
    "data_coverage",
    "query",
    "resolve_symbols",
    "search",
]

AmbiguousMode = Literal["error", "first", "all"]
"""What ``resolve-symbols`` does when one input matches several catalog symbols.

A ``Literal`` rather than a bare ``str`` so the value the three surfaces publish is an
enumeration instead of free text. All three already agreed on the three spellings and on
``error`` as the default; typing it is what stops a fourth from being invented at a call
site and failing only for whoever typed it.
"""

_RESOLVE_SCORE_THRESHOLD = 40
"""Minimum :func:`~crocodile.core.store.catalog._score_symbol` for a resolution match.

40 is a substring hit. Copied from the value the client resolved against rather than
re-chosen, because the threshold decides whether an ambiguous input raises or resolves and
a different number here would make the capability disagree with the client on the same
lake.
"""

_RESOLVE_SEARCH_LIMIT = 20
"""How many candidates a resolution considers per input, as the client did.

Not the same knob as ``SearchParams.limit``: this one is internal to resolution and the
surfaces never exposed it, so it stays a constant rather than becoming a parameter nobody
asked for.
"""


class NoParams(msgspec.Struct, frozen=True):
    """The parameter schema of a capability that takes no parameters.

    Shared by the five capabilities whose whole input is "which lake", which is
    :class:`~crocodile.core.capability.CapabilityContext`'s to supply and not a user's:
    ``catalog``, ``catalog-summary``, ``catalog-stats``, ``catalog-channels`` and
    ``catalog-exchanges``. Five identically empty structs would be five names for one
    published schema and five places to accidentally grow a field.

    ``--data-dir`` is the option every one of these carried on the legacy CLI and it is not
    here for the same reason ``readonly`` is not: it selects the lake, which the surface
    resolves once when it builds the context.
    """


class QueryParams(msgspec.Struct, frozen=True):
    """Parameters for ``query``.

    One field, and the absence of the other two is the point. The legacy REST payload also
    carried ``limit``, which is now :attr:`CapabilityContext.row_limit
    <crocodile.core.capability.CapabilityContext.row_limit>`: it was never a request for
    fewer rows, it defaulted to the hard maximum and was clamped to it, which makes it a
    ceiling the *surface* imposes. Leaving it in the struct would let a caller raise its own
    ceiling.
    """

    sql: str


class ChannelParams(msgspec.Struct, frozen=True):
    """Parameters for ``catalog-dates``: which channel's date partitions to list.

    Required, not defaulted to ``""``. The CLI made ``--channel`` a required option and the
    MCP tool listed it in ``required``; only REST defaulted it, and that default answers a
    caller who forgot the argument with ``[]`` — indistinguishable from a channel that is
    genuinely empty.
    """

    channel: str


class CatalogFilterParams(msgspec.Struct, frozen=True):
    """The two optional lake filters, shared by ``catalog-inventory`` and ``catalog-symbols``.

    Both capabilities take exactly ``channel`` and ``exchange`` and both pass them to
    :meth:`Catalog.inventory <crocodile.core.store.catalog.Catalog.inventory>`, so they
    share one struct and therefore one published schema.

    ``str | None`` covers all three surfaces: the CLI spelled an absent filter ``None`` and
    REST spelled it ``""``, and every one of the six legacy call sites normalised through
    ``(x or "").strip() or None`` before use. One nullable string says that once. Empty and
    whitespace-only values still normalise to "no filter" in the adapters, because a
    surface that hands through a blank query parameter must not silently empty the answer.
    """

    channel: str | None = None
    exchange: str | None = None


class SearchParams(msgspec.Struct, frozen=True):
    """Parameters for ``search``.

    ``q`` rather than ``query``: REST, MCP and :meth:`Catalog.search_symbols
    <crocodile.core.store.catalog.Catalog.search_symbols>` all call it ``q`` and only the
    CLI positional was called ``query`` — which is also the name of a different capability
    in this module, so keeping it would put two unrelated things one word apart.

    ``limit`` is a real parameter here and not a surface ceiling: all three surfaces agreed
    on ``20``, which is a ranking cutoff a caller chooses, not a transport cap. The
    surface's cap still applies on top of it through ``row_limit``.
    """

    q: str
    channel: str | None = None
    exchange: str | None = None
    limit: int = 20


class ResolveSymbolsParams(msgspec.Struct, frozen=True):
    """Parameters for ``resolve-symbols``.

    ``symbols`` is a sequence, not the comma-separated string the CLI positional and the
    REST query parameter carried. Both of those are text encodings of a list — the MCP tool
    accepted a real list *or* the string, and the client's own signature is ``list[str]`` —
    so the splitting belongs in the projection that has only text to work with, and the
    schema the surfaces publish says what the value actually is.

    ``ambiguous`` defaults to ``error`` on all three surfaces, which is the safe default:
    an input matching four symbols is a question, and picking one silently answers it wrong
    three times out of four.
    """

    symbols: tuple[str, ...]
    channel: str | None = None
    ambiguous: AmbiguousMode = "error"


class DataCoverageParams(msgspec.Struct, frozen=True):
    """Parameters for ``data-coverage``: coverage rows for one exact symbol.

    ``symbol`` is required for the reason ``ChannelParams.channel`` is — the CLI exited
    non-zero without it and the MCP tool required it, while REST's ``""`` default returns
    an empty result that reads as "this symbol has no data".
    """

    symbol: str
    channel: str | None = None
    exchange: str | None = None


class ScanParams(msgspec.Struct, frozen=True):
    """Parameters for ``catalog-scan``.

    Three decisions.

    ``start_ns`` / ``end_ns``, not REST's ``start`` / ``end``. The registry already spells
    a nanosecond bound this way in :class:`~crocodile.capabilities.analytics.IndicatorParams`
    and :meth:`Catalog.scan <crocodile.core.store.catalog.Catalog.scan>` names its own
    arguments the same, so the alternative is one struct in the registry saying ``start``
    while its neighbour says ``start_ns`` for the identical quantity. The suffix also names
    the unit, which a bare ``start`` invites a caller to fill with seconds. Both are
    required: REST defaulted them to ``0``, and ``[0, 0]`` is an empty range that answers
    a caller who omitted the bounds with an empty frame.

    ``symbols``, not REST's single ``symbol``. The implementation underneath —
    ``CrypcodileClient.scan`` — already scanned a list and merged the results by
    ``local_ts``; the route narrowed that to ``[symbol]`` at the call site. Publishing the
    narrow shape would delete a working multi-symbol read the way this merge has already
    deleted seven other things.

    ``limit`` is a user parameter here, unlike ``query``'s. It is a real argument to
    ``Catalog.scan`` that prunes rows inside DuckDB, and "the first 200 prints" is a
    request rather than a policy. The surface's ``row_limit`` still bounds it — see
    :func:`catalog_scan` — so a caller cannot use it to raise a ceiling, only to lower one.
    """

    channel: str
    symbols: tuple[str, ...]
    start_ns: int
    end_ns: int
    limit: int | None = None


def _filter(value: str | None) -> str | None:
    """Normalise an optional lake filter to ``None`` or a non-blank value.

    The one place the ``(x or "").strip() or None`` idiom lives, instead of the six legacy
    call sites that each wrote it out. It matters rather than being tidiness:
    :meth:`Catalog.inventory <crocodile.core.store.catalog.Catalog.inventory>` treats a
    non-``None`` channel that is not registered as an empty result, so a surface passing a
    blank query parameter straight through would report an empty lake.
    """
    return (value or "").strip() or None


def _row_cap(requested: int | None, ceiling: int | None) -> int | None:
    """The smaller of a caller's requested row count and the surface's ceiling.

    Neither alone is right. Dropping the ceiling lets a caller ask a public endpoint for the
    whole lake; dropping the request ignores a parameter three surfaces published. ``None``
    on either side means "no bound from that side", so the result is ``None`` only when
    neither imposes one.
    """
    if requested is None:
        return ceiling
    if ceiling is None:
        return requested
    return min(requested, ceiling)


def query(ctx: CapabilityContext, params: QueryParams) -> pl.DataFrame:
    """Run the caller's SQL under *this surface's* policy.

    :meth:`CapabilityContext.query <crocodile.core.capability.CapabilityContext.query>` and
    never ``ctx.catalog.query``: the second one compiles and runs while ignoring both
    ``readonly`` and ``row_limit``, which is precisely how one capability came to ship three
    different SQL policies across six surfaces — including a network-facing one with no
    guard at all.
    """
    return ctx.query(params.sql)


def catalog(ctx: CapabilityContext, params: NoParams) -> pl.DataFrame:
    """Every channel in the lake with its row count.

    :meth:`Catalog.channel_row_counts
    <crocodile.core.store.catalog.Catalog.channel_row_counts>` is the one implementation
    the three disagreeing loops collapsed into, and the distinction it draws survives into
    the frame: ``0`` means a partition directory exists with no parquet parts in it, ``-1``
    means a view exists whose ``COUNT(*)`` failed and the number is unknown. Rendering the
    second as the first would make a stalled collector look like an idle one.

    Returned as a frame rather than the mapping the method hands back because two columns
    of one row per channel is what all three surfaces render, and a mapping is the one shape
    a table projector cannot page.
    """
    counts = ctx.catalog.channel_row_counts()
    return pl.DataFrame(
        {"channel": list(counts), "row_count": list(counts.values())},
        schema={"channel": pl.Utf8, "row_count": pl.Int64},
    )


def catalog_summary(ctx: CapabilityContext, params: NoParams) -> dict[str, Any]:
    """Channels and on-disk exchanges in one call, with their counts.

    ``exchanges_on_disk`` is hive partitions that exist, which is a different question from
    which connectors are registered — the key is named the long way because the two were
    confused often enough that both legacy surfaces documented the difference.
    """
    channels = ctx.catalog.list_channels()
    exchanges_on_disk = ctx.catalog.list_exchanges_on_disk()
    return {
        "channels": channels,
        "exchanges_on_disk": exchanges_on_disk,
        "exchange_count": len(exchanges_on_disk),
        "channel_count": len(channels),
    }


def catalog_stats(ctx: CapabilityContext, params: NoParams) -> dict[str, Any]:
    """Per-channel row counts, without the per-symbol inventory aggregate.

    The same numbers :func:`catalog` returns, in the envelope the REST route and the MCP
    tool published. Both project ``channel_row_counts`` so that the count a caller gets
    cannot depend on which surface asked — they disagreed until it did.
    """
    row_counts = ctx.catalog.channel_row_counts()
    return {"row_counts": row_counts, "channel_count": len(row_counts)}


def catalog_channels(ctx: CapabilityContext, params: NoParams) -> list[str]:
    """Channel names present in the lake, from the filesystem walk.

    A directory with no parquet parts is still a channel the lake has, which is why this is
    a walk rather than a listing of registered DuckDB views: a view cannot be registered
    over a glob matching no file, so the view-backed answer can only ever be a subset and
    cannot report a collector that started and wrote nothing.
    """
    return ctx.catalog.list_channels()


def catalog_dates(ctx: CapabilityContext, params: ChannelParams) -> list[str]:
    """Distinct ``date=`` partitions present for one channel."""
    return ctx.catalog.list_dates(params.channel)


def catalog_symbols(ctx: CapabilityContext, params: CatalogFilterParams) -> list[str]:
    """Distinct symbols present in the lake inventory.

    The light half of ``catalog-inventory``: symbol strings only, no coverage rows. Kept as
    its own capability rather than a flag on that one because the two return different
    shapes, and a surface has to know which before it can render either.
    """
    inv = ctx.catalog.inventory(channel=_filter(params.channel), exchange=_filter(params.exchange))
    if len(inv) == 0:
        return []
    symbols: list[str] = sorted(inv["symbol"].unique().to_list())
    return symbols


def catalog_inventory(ctx: CapabilityContext, params: CatalogFilterParams) -> pl.DataFrame:
    """Per-symbol coverage: exchange, channel, symbol, first and last timestamp, row count."""
    return ctx.catalog.inventory(
        channel=_filter(params.channel), exchange=_filter(params.exchange)
    )


def catalog_exchanges(ctx: CapabilityContext, params: NoParams) -> list[str]:
    """Exchange partitions present on disk.

    Distinct from the connector registry, which lists what the code can talk to rather than
    what has been written. ``market``'s ``list-exchanges`` is that other question.
    """
    return ctx.catalog.list_exchanges_on_disk()


def catalog_scan(ctx: CapabilityContext, params: ScanParams) -> pl.DataFrame:
    """Stored rows for one channel and one or more symbols within a nanosecond range.

    Per-symbol scans merged and re-sorted by ``local_ts``, which is what the client did,
    with the row cap pushed down into each scan as well as applied to the merge. Both are
    needed and neither is redundant: pushing it down is what stops DuckDB materialising
    rows the caller will not see, and applying it after the merge is what makes the answer
    honour the cap once rather than once per symbol. The two agree — the globally first *n*
    rows can contain at most *n* from any single symbol — so the push-down never removes a
    row the merge would have kept.
    """
    if not params.symbols:
        return pl.DataFrame()

    limit = _row_cap(params.limit, ctx.row_limit)
    frames: list[pl.DataFrame] = []
    for symbol in params.symbols:
        df = ctx.catalog.scan(params.channel, symbol, params.start_ns, params.end_ns, limit)
        if len(df) > 0:
            frames.append(df)

    if not frames:
        return pl.DataFrame()
    merged = frames[0] if len(frames) == 1 else pl.concat(frames, how="diagonal").sort("local_ts")
    return merged.head(limit) if limit is not None and len(merged) > limit else merged


def search(ctx: CapabilityContext, params: SearchParams) -> pl.DataFrame:
    """Ranked symbol search over the lake inventory."""
    return ctx.catalog.search_symbols(
        params.q,
        channel=_filter(params.channel),
        exchange=_filter(params.exchange),
        limit=params.limit,
    )


def resolve_symbols(ctx: CapabilityContext, params: ResolveSymbolsParams) -> list[str]:
    """Resolve free-form symbol inputs to canonical catalog symbols.

    ``CrypcodileClient.resolve_symbols``' algorithm over the catalog the context supplies.
    It is reproduced rather than delegated to because the client method reaches the lake
    through ``self.inventory`` and ``self.search_symbols``, both of which are pure
    :class:`~crocodile.core.store.catalog.Catalog` delegations — the client adds an owned
    ``data_dir`` and nothing else, and honouring that would mean opening a second DuckDB
    connection over a lake the surface did not choose.

    Reproducing an implementation is how a fork starts, so it is pinned rather than
    trusted: ``tests/capabilities/test_catalog.py`` asserts this returns what the client
    returns, for pass-through, ranked, ambiguous and unmatched inputs on the same lake.
    """
    return _resolve(ctx.catalog, params.symbols, params.channel, params.ambiguous)


def data_coverage(ctx: CapabilityContext, params: DataCoverageParams) -> pl.DataFrame:
    """Inventory coverage rows for one exact symbol.

    A blank symbol matches nothing and yields the empty frame carrying the inventory
    schema, which is the contract all three surfaces documented. It falls out of the filter
    instead of being hand-built from a second copy of the column list: the fork's copy is
    what a schema change would have to remember to update twice.
    """
    inv = ctx.catalog.inventory(channel=_filter(params.channel), exchange=_filter(params.exchange))
    if len(inv) == 0:
        return inv
    return inv.filter(pl.col("symbol") == params.symbol.strip())


def _resolve(
    catalog_: Catalog,
    symbols: tuple[str, ...],
    channel: str | None,
    ambiguous: AmbiguousMode,
) -> list[str]:
    """The resolution algorithm, over a catalog rather than a client.

    Raises:
        ValueError: on an unknown *ambiguous* mode, an input nothing matched, or a
            multi-match while *ambiguous* is ``error``. All three are the caller's
            question to answer, which is why they are raised rather than resolved: REST
            turned them into 400s and MCP into a structured ``{"error": ...}``, and both
            need the sentence naming the candidates.
    """
    if ambiguous not in ("error", "first", "all"):
        raise ValueError(f"ambiguous must be 'error', 'first', or 'all'; got {ambiguous!r}")
    if not symbols:
        return []

    channel = _filter(channel)
    inv = catalog_.inventory(channel=channel)
    known: set[str] = set(inv["symbol"].to_list()) if len(inv) > 0 else set()

    resolved: list[str] = []
    seen: set[str] = set()

    def _append(sym: str) -> None:
        if sym not in seen:
            seen.add(sym)
            resolved.append(sym)

    for raw in symbols:
        candidate = raw.strip()
        if not candidate:
            continue

        # An already-canonical symbol that is in the lake needs no ranking, and skipping it
        # matters: a symbol whose raw half is a substring of another's would otherwise
        # resolve as ambiguous against itself.
        if ":" in candidate and candidate in known:
            _append(candidate)
            continue

        hits = catalog_.search_symbols(candidate, channel=channel, limit=_RESOLVE_SEARCH_LIMIT)
        if len(hits) > 0:
            hits = hits.filter(pl.col("score") >= _RESOLVE_SCORE_THRESHOLD)
        if len(hits) == 0:
            raise ValueError(f"No symbols matched {candidate!r}")

        matches: list[str] = hits["symbol"].to_list()
        if len(matches) == 1:
            _append(matches[0])
            continue
        if ambiguous == "error":
            listed = ", ".join(
                f"{row['symbol']} (score={row['score']})" for row in hits.iter_rows(named=True)
            )
            raise ValueError(f"Ambiguous symbol {candidate!r}: {len(matches)} matches: {listed}")
        if ambiguous == "first":
            # search_symbols already ranks by score descending.
            _append(matches[0])
        else:
            for match in matches:
                _append(match)

    return resolved


# ---------------------------------------------------------------------------
# Declarations
#
# `prov` on every implementation below is one of two levels, drawn on one line: NATIVE
# where every value in the answer was read — a partition name, a stored row, a stored
# field — and DERIVED where a value was computed over stored records, which is the same
# line `indicators` sits on the far side of. A row count, a min/max timestamp and a search
# score are all numbers no record carries.
#
# `basis` is `native` throughout, because it names where the *inputs* came from and the
# inputs are the lake's own records and directories. A stored row's own provenance travels
# with it in its `prov_*` tail, so reading it back neither raises nor lowers what it
# already claims — which is exactly why a catalog read does not have to guess what it is
# handing over, and why the equity half can declare what the crypto half does without
# asserting anything about how the equity rows were produced.
# ---------------------------------------------------------------------------


def _both(fn: CapabilityFn, prov: Provenance) -> dict[AssetClass, Impl]:
    """One adapter, both asset classes, one basis.

    Every capability in this module is served by the same code reading the same
    ``Catalog``, so writing the pair out thirteen times would be thirteen chances to make
    the two halves differ by accident — the asymmetry the gate exists to catch, introduced
    by the port meant to end it.
    """
    impl = Impl(fn=fn, prov=prov, basis="native")
    return {AssetClass.CRYPTO: impl, AssetClass.EQUITY: impl}


QUERY = declare(
    Capability(
        name="query",
        summary="Execute DuckDB SQL against the lake's channel views.",
        params=QueryParams,
        returns=ReturnKind.TABLE,
        # `query_market_data` is the MCP tool name on both forks. It does not fall out of
        # the capability name under any transform, so an agent wired to it would simply
        # stop finding the tool.
        aliases=("query_market_data",),
        # NATIVE is the ceiling: `SELECT *` hands back stored rows unaltered. That a caller
        # can write an aggregate is the caller's computation, not this implementation's.
        impls=_both(query, Provenance.NATIVE),
    )
)


CATALOG = declare(
    Capability(
        name="catalog",
        summary="Every channel in the lake with its row count.",
        params=NoParams,
        returns=ReturnKind.TABLE,
        impls=_both(catalog, Provenance.DERIVED),
    )
)


CATALOG_SUMMARY = declare(
    Capability(
        name="catalog-summary",
        summary="Channels and on-disk exchanges present in the lake, with their counts.",
        params=NoParams,
        returns=ReturnKind.SCALAR,
        # NATIVE, unlike `catalog-stats` next to it: the two counts here are the lengths of
        # two directory listings, not an aggregate over records. Nothing in the answer was
        # computed from a stored row.
        impls=_both(catalog_summary, Provenance.NATIVE),
    )
)


CATALOG_STATS = declare(
    Capability(
        name="catalog-stats",
        summary="Per-channel row counts, without the per-symbol inventory aggregate.",
        params=NoParams,
        returns=ReturnKind.SCALAR,
        impls=_both(catalog_stats, Provenance.DERIVED),
    )
)


CATALOG_CHANNELS = declare(
    Capability(
        name="catalog-channels",
        summary="Channel names present in the lake.",
        params=NoParams,
        returns=ReturnKind.TABLE,
        aliases=("list_data_channels",),
        impls=_both(catalog_channels, Provenance.NATIVE),
    )
)


CATALOG_DATES = declare(
    Capability(
        name="catalog-dates",
        summary="Distinct date partitions present for one channel.",
        params=ChannelParams,
        returns=ReturnKind.TABLE,
        aliases=("list_dates",),
        impls=_both(catalog_dates, Provenance.NATIVE),
    )
)


CATALOG_SYMBOLS = declare(
    Capability(
        name="catalog-symbols",
        summary="Distinct symbols present in the lake inventory.",
        params=CatalogFilterParams,
        returns=ReturnKind.TABLE,
        aliases=("list_symbols",),
        impls=_both(catalog_symbols, Provenance.DERIVED),
    )
)


CATALOG_INVENTORY = declare(
    Capability(
        name="catalog-inventory",
        summary="Per-symbol coverage rows: exchange, channel, symbol, timestamps, row count.",
        params=CatalogFilterParams,
        returns=ReturnKind.TABLE,
        aliases=("inventory_snapshot",),
        impls=_both(catalog_inventory, Provenance.DERIVED),
    )
)


CATALOG_EXCHANGES = declare(
    Capability(
        name="catalog-exchanges",
        summary="Exchange partitions present on disk, as opposed to registered connectors.",
        params=NoParams,
        returns=ReturnKind.TABLE,
        aliases=("list_exchanges_on_disk",),
        impls=_both(catalog_exchanges, Provenance.NATIVE),
    )
)


CATALOG_SCAN = declare(
    Capability(
        name="catalog-scan",
        summary="Stored rows for one channel and symbols within a nanosecond time range.",
        params=ScanParams,
        returns=ReturnKind.TABLE,
        impls=_both(catalog_scan, Provenance.NATIVE),
    )
)


SEARCH = declare(
    Capability(
        name="search",
        summary="Ranked symbol search over the lake inventory.",
        params=SearchParams,
        returns=ReturnKind.TABLE,
        aliases=("search_symbols",),
        impls=_both(search, Provenance.DERIVED),
    )
)


RESOLVE_SYMBOLS = declare(
    Capability(
        name="resolve-symbols",
        summary="Resolve free-form symbol inputs to canonical catalog symbols.",
        params=ResolveSymbolsParams,
        returns=ReturnKind.TABLE,
        impls=_both(resolve_symbols, Provenance.DERIVED),
    )
)


DATA_COVERAGE = declare(
    Capability(
        name="data-coverage",
        summary="Inventory coverage rows for one exact symbol.",
        params=DataCoverageParams,
        returns=ReturnKind.TABLE,
        impls=_both(data_coverage, Provenance.DERIVED),
    )
)
