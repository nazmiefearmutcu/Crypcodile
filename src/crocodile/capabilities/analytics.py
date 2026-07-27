"""Capabilities computed from stored records.

Owns ``basis``, ``funding-apr``, ``iv-surface``, ``vol-skew``, ``term-structure``,
``risk-reversal``, ``slippage``, ``ofi``, ``indicators``, ``liquidity-depth`` and the rest
of the analytics family — anything whose inputs are rows already in the lake.

Every declaration here shows the same adapter pattern: the analytics function keeps the
signature its own domain wants, and a module-level function named after the capability
turns ``(ctx, params)`` into that signature's arguments. Do not reshape an analytics
function to fit the registry — ``apply_indicators`` takes a frame because a frame is what
it computes over, and a version of it that took a
:class:`~crocodile.core.capability.CapabilityContext` would be a worse function that is
also harder to test.

The adapters are module-level and named, not lambdas or partials, so that a stack trace and
the calling-convention gate both point at something with a file and a line number. Six
analytics functions already carry the name their capability needs — ``perp_basis``,
``spot_future_basis``, ``funding_apr``, ``iv_surface``, ``term_structure``, ``vol_skew`` —
so those are imported under a ``_``-prefixed alias and the bare name belongs to the adapter.
The rule is one-directional: the adapter is what the registry sees, so the adapter gets the
capability's spelling.

One params struct per capability, never one shared by two. Identical schemas are what
:attr:`Capability.params` asks of the two *asset classes*; sharing a struct across two
capabilities would additionally couple their schedules, so that a field Phase 3 needs on
``term-structure`` alone could only be added by splitting a type two declarations point at.
``IvSurfaceParams`` and ``TermStructureParams`` are field-for-field identical today and
still separate for that reason.

Where the three legacy surfaces disagreed about a parameter, the struct's docstring records
which surface was followed and why. Those disagreements are the substance of the port: a
default is a decision six files made six times, and collapsing them into one declaration
means making it once, in the open.
"""

from __future__ import annotations

from typing import Any

import msgspec
import polars as pl

from crocodile.core.analytics.indicators import apply_indicators
from crocodile.core.analytics.slippage import estimate_slippage
from crocodile.core.capability import (
    PENDING_SYMMETRY,
    AssetClass,
    Capability,
    CapabilityContext,
    Impl,
    ReturnKind,
    declare,
)
from crocodile.core.resample.ohlcv import resample_ohlcv
from crocodile.core.schema.provenance import Provenance
from crocodile.crypto.analytics.basis import (
    perp_basis as _perp_basis,
)
from crocodile.crypto.analytics.basis import (
    spot_future_basis as _spot_future_basis,
)
from crocodile.crypto.analytics.basis import (
    spot_perp_basis,
)
from crocodile.crypto.analytics.funding import funding_apr as _funding_apr
from crocodile.crypto.analytics.funding_prediction import predict_next_funding
from crocodile.crypto.analytics.liquidity_depth import calculate_block_liquidity_depth
from crocodile.crypto.analytics.ofi import calculate_ofi
from crocodile.crypto.analytics.risk import calculate_chaos_score
from crocodile.crypto.analytics.smart_money import normalize_watchlist, summarize_smart_money
from crocodile.crypto.analytics.volsurface import (
    iv_surface as _iv_surface,
)
from crocodile.crypto.analytics.volsurface import (
    risk_reversal_butterfly,
)
from crocodile.crypto.analytics.volsurface import (
    term_structure as _term_structure,
)
from crocodile.crypto.analytics.volsurface import (
    vol_skew as _vol_skew,
)
from crocodile.crypto.analytics.whale import track_whale_alerts

__all__ = [
    "BASIS",
    "CHAOS_SCORE",
    "FUNDING_APR",
    "FUNDING_PREDICT",
    "INDICATORS",
    "IV_SURFACE",
    "LABEL_TRANSFERS",
    "LIQUIDITY_DEPTH",
    "OFI",
    "PERP_BASIS",
    "RISK_REVERSAL",
    "SLIPPAGE",
    "SMART_MONEY",
    "SPOT_FUTURE_BASIS",
    "TERM_STRUCTURE",
    "VOL_SKEW",
    "WHALE_ALERTS",
    "BasisParams",
    "ChaosScoreParams",
    "FundingAprParams",
    "FundingPredictParams",
    "IndicatorParams",
    "IvSurfaceParams",
    "LabelTransfersParams",
    "LiquidityDepthParams",
    "OfiParams",
    "PerpBasisParams",
    "RiskReversalParams",
    "SlippageParams",
    "SmartMoneyParams",
    "SpotFutureBasisParams",
    "TermStructureParams",
    "VolSkewParams",
    "WhaleAlertsParams",
]


# ---------------------------------------------------------------------------
# Parameter schemas
# ---------------------------------------------------------------------------


class IndicatorParams(msgspec.Struct, frozen=True):
    """Parameters for ``indicators``, identical for both asset classes."""

    symbol: str
    start_ns: int
    end_ns: int
    interval: str = "1d"
    indicator: str | None = None
    period: int = 14


class SlippageParams(msgspec.Struct, frozen=True):
    """Parameters for ``slippage``, identical for both asset classes.

    ``size_unit`` is the crypto half of a collision: the crypto implementation took
    ``size: float | str`` plus a unit and could walk the book denominated in either asset,
    the equity one took a bare ``float``. One struct has to cover both, so the unit is
    either equity-ignored or crypto-lost, and it is equity-ignored — an optional parameter
    costs a caller that omits it nothing, while dropping it deletes a measured, tested book
    walk. Left unset, the walk is by quantity, which is what sizing in shares means.
    """

    symbol: str
    side: str
    size: float | str
    size_unit: str | None = None


class BasisParams(msgspec.Struct, frozen=True):
    """Parameters for ``basis`` — the spot leg and the perpetual leg, plus a window.

    Both symbols are required and named rather than positional, which is what settles the
    argument-order flip between the legacy routes. ``GET /basis`` passes ``(spot, perp)``
    (api_server.py:2010) and ``GET /spot-future-basis`` passes ``(future, spot)``
    (:2095), so "the spot leg" is argument one in one route and argument two in the other.
    A struct has no argument two; the adapter binds each field to the parameter that
    means it, and a caller that swapped the two legs on the wire could only do so by
    swapping two labelled fields.

    ``start_ns``/``end_ns`` are required, following ``IndicatorParams`` rather than either
    surface. The CLI resolves an omitted range to the whole lake (cli.py:3197) and REST
    defaults both to ``0`` (:1985), which returns nothing; neither is a default worth
    freezing into the one schema all three surfaces will publish.
    """

    spot_symbol: str
    perp_symbol: str
    start_ns: int
    end_ns: int


class PerpBasisParams(msgspec.Struct, frozen=True):
    """Parameters for ``perp-basis`` — one perpetual contract, mark against index.

    The single symbol is ``symbol``, not ``perp_symbol``. Two of three surfaces spell it
    for the leg (``--perp`` at cli.py:3035, ``perp_symbol`` at mcp_server.py:1528) and REST
    spells it ``symbol`` (:2027); the tie is broken by the registry rather than by the
    legacy majority, because every other single-symbol capability here — ``indicators``,
    ``slippage``, ``funding-apr``, ``ofi``, ``whale-alerts``, ``liquidity-depth`` — calls
    its one symbol ``symbol``, and a schema that renames the same field per capability is
    the drift the single registry exists to stop. The leg is already named by the
    capability.
    """

    symbol: str
    start_ns: int
    end_ns: int


class SpotFutureBasisParams(msgspec.Struct, frozen=True):
    """Parameters for ``spot-future-basis`` — a dated future against spot.

    ``expiry_ns`` is exposed even though ``GET /spot-future-basis`` never accepted it
    (api_server.py:2067). It is on the CLI as ``--expiry`` (cli.py:3041) and on the MCP
    tool as an optional ``expiry_ns`` (mcp_server.py:1611), and it is not cosmetic: with it
    the result gains an ``annualized_pct`` column, without it that column does not exist.
    Two surfaces pinned a parameter that changes the answer's shape and one forgot it, so
    the REST projection gains it. Absent, the behaviour is the REST route's exactly.
    """

    future_symbol: str
    spot_symbol: str
    start_ns: int
    end_ns: int
    expiry_ns: int | None = None


class FundingAprParams(msgspec.Struct, frozen=True):
    """Parameters for ``funding-apr`` — one perpetual symbol over a window."""

    symbol: str
    start_ns: int
    end_ns: int


class FundingPredictParams(msgspec.Struct, frozen=True):
    """Parameters for ``funding-predict`` — a rate history and a fallback window.

    ``rates`` is a sequence of floats, which is the MCP tool's shape
    (mcp_server.py:1749) and neither of the other two. The CLI takes ``--rates`` as a
    comma-separated string or ``--file`` as a path to a CSV/JSON (cli.py:4823) and REST
    takes ``rates`` as a comma-separated query string (api_server.py:2699); both then parse
    to exactly this list. Comma-splitting is how you put an array in a query string and
    reading a CSV is how you put one in a shell — encodings of the parameter, not the
    parameter — so they stay in the projections and the registry takes the numbers.

    ``window_size`` is 5 on all three (cli.py:4844, api_server.py:2700, mcp_server.py:1757),
    so there is nothing to decide.

    ``target_col`` exists on ``predict_next_funding`` and is exposed by no surface, so it is
    not here; see ``IvSurfaceParams`` for the same rule applied to ``fit_method``.
    """

    rates: tuple[float, ...]
    window_size: int = 5


class IvSurfaceParams(msgspec.Struct, frozen=True):
    """Parameters for ``iv-surface`` — one underlying at one instant.

    ``rate`` defaults to 0.0 on all three surfaces (cli.py:3377, api_server.py:2308,
    mcp_server.py:1421), so it carries that default here.

    ``fit_method`` is **not** here, and this is the rule for the whole options family.
    ``iv_surface`` and ``vol_skew`` both accept ``fit_method="sabr"`` and neither the CLI,
    REST nor MCP ever offered it — a port that added it would be inventing public API under
    cover of a migration, and there is no legacy behaviour to check the new option against.
    It would also be asymmetric within its own family: ``term_structure`` does not take the
    argument at all, so a shared surface option would be honoured by two capabilities and
    silently ignored by the third. The implementations keep their own default.
    """

    underlying: str
    at_ns: int
    rate: float = 0.0


class TermStructureParams(msgspec.Struct, frozen=True):
    """Parameters for ``term-structure`` — the ATM slice of the same snapshot.

    Field-for-field identical to :class:`IvSurfaceParams`, and deliberately a separate type;
    the module docstring has the argument.
    """

    underlying: str
    at_ns: int
    rate: float = 0.0


class VolSkewParams(msgspec.Struct, frozen=True):
    """Parameters for ``vol-skew`` — one underlying, one expiry, one instant."""

    underlying: str
    expiry_ns: int
    at_ns: int
    rate: float = 0.0


class RiskReversalParams(msgspec.Struct, frozen=True):
    """Parameters for ``risk-reversal`` — a vol skew plus the delta to read it at.

    ``target_delta`` is 0.25 on all three surfaces (cli.py:3757, api_server.py:2435,
    mcp_server.py:1510), which is the 25-delta convention the capability is named after.
    """

    underlying: str
    expiry_ns: int
    at_ns: int
    rate: float = 0.0
    target_delta: float = 0.25


class OfiParams(msgspec.Struct, frozen=True):
    """Parameters for ``ofi`` — one symbol, a window, and a bin width.

    ``interval`` defaults to ``1m``. The three surfaces look like three answers and are
    not: the CLI's ``None`` (cli.py:3921) is a sentinel, and both branches behind it resolve
    to ``1m`` — the non-interactive fallback at :3939 and the prompt's default at :3960.
    REST publishes ``1m`` twice, as the signature default and as the empty-string coalesce
    (api_server.py:2177, :2198). Only MCP declines to choose, marking it required
    (mcp_server.py:1369). So one value is written five times across two surfaces and the
    third asks the caller for a number nobody has to think about; ``1m`` wins.

    That ``indicators`` defaults to ``1d`` is not a contradiction. OFI bins book-snapshot
    deltas, where a day-wide bin sums a day of top-of-book churn into one number, and
    ``indicators`` bins trades into candles, where a minute-wide bin is a different chart.
    The default belongs to the measurement, not to the word ``interval``.
    """

    symbol: str
    start_ns: int
    end_ns: int
    interval: str = "1m"


class WhaleAlertsParams(msgspec.Struct, frozen=True):
    """Parameters for ``whale-alerts`` — trades and liquidations above a USD notional.

    ``min_usd`` defaults to 100 000. This is the one parameter in the batch where the three
    surfaces genuinely disagree: the CLI resolves it to ``100000.0`` in both of its branches
    (cli.py:4144 non-interactive, :4165 as the prompt's default), REST defaults it to ``0.0``
    (api_server.py:2225) and MCP requires it (mcp_server.py:1400).

    The CLI's value wins because it is the only one any surface *chose*. REST's ``0.0`` is
    what a FastAPI query parameter looks like when nobody decided — and it is not a harmless
    zero: ``min_usd=0`` admits every print in the range, so ``GET /whale-alerts`` with no
    threshold returns the entire tape under a name that promises whales. MCP's "required"
    is the honest version of the same abstention, but a capability whose whole subject is a
    threshold should carry the threshold the product means.

    This does change ``GET /whale-alerts?symbol=X`` with no ``min_usd``: it stops returning
    every trade and starts returning whales. A caller who wanted the tape asks for ``trade``
    rows; a caller who wanted whales was being lied to.
    """

    symbol: str
    start_ns: int
    end_ns: int
    min_usd: float = 100_000.0


class LiquidityDepthParams(msgspec.Struct, frozen=True):
    """Parameters for ``liquidity-depth`` — one symbol, and nothing else.

    No time range, on any of the three surfaces (cli.py:4012, api_server.py:2489,
    mcp_server.py:1668), because ``calculate_block_liquidity_depth`` scans from zero to now
    and buckets by ``sequence_id`` rather than by time. Adding a window here would be a
    parameter the implementation cannot honour.
    """

    symbol: str


class SmartMoneyParams(msgspec.Struct, frozen=True):
    """Parameters for ``smart-money`` — transfer rows and the addresses to watch.

    Both are data, not paths. The CLI takes ``--transfers`` and ``--watchlist`` as files
    (cli.py:4601, :4608) and REST and MCP take the rows and the map inline
    (api_server.py:2930, mcp_server.py:1948); loading a CSV is how a shell passes a table,
    so it stays in the CLI projection, the same call the ``rates`` field of
    :class:`FundingPredictParams` makes.

    ``watchlist`` keeps the union the REST body already declared (api_server.py:2927):
    ``normalize_watchlist`` accepts an ``addr -> label`` map, a bare address list, and the
    nested ``{"watchlist": ...}`` / ``{"labels": ...}`` / ``{"addresses": [...]}`` shapes,
    and narrowing it here would reject documents the legacy endpoint accepted.
    """

    transfers: tuple[dict[str, Any], ...]
    watchlist: dict[str, Any] | list[Any]


class LabelTransfersParams(msgspec.Struct, frozen=True):
    """Parameters for ``label-transfers`` — rows, a watchlist, and two optional filters.

    ``known_only`` is ``False`` and ``min_usd`` is ``None`` on all three surfaces
    (cli.py:4686/:4679, api_server.py:3003/:3004, mcp_server.py:910/:911), so neither needs
    deciding. ``min_usd=None`` is not ``min_usd=0.0``: ``None`` skips the filter entirely
    while ``0.0`` runs it and drops every row whose ``usd_value`` is missing or unparseable,
    which is a different result on the same input.
    """

    transfers: tuple[dict[str, Any], ...]
    watchlist: dict[str, Any] | list[Any]
    known_only: bool = False
    min_usd: float | None = None


class ChaosScoreParams(msgspec.Struct, frozen=True):
    """Parameters for ``chaos-score`` — the four readings the index is made of.

    All four are required, which is MCP's schema (mcp_server.py:1805) and not the CLI's or
    REST's; both of those default every one of them to ``0.0`` (cli.py:4406, :4413, :4420,
    :4427; api_server.py:2568). Those defaults are what Typer and FastAPI need in order to
    make an option optional, and the value they chose is the worst possible one here: the
    four inputs *are* the computation, and all-zeros scores exactly 0.0 — "perfectly calm",
    reported for a market nobody looked at. That is the zero-standing-in-for-a-hole this
    package's provenance rules exist to refuse, one layer up.

    So ``crocodile chaos-score`` with no options stops printing 0.0 and starts saying which
    reading it is missing.
    """

    volatility: float
    stablecoin_deviation: float
    orderbook_imbalance: float
    sequencer_delay: float


# ---------------------------------------------------------------------------
# Adapters — (ctx, params) in, the analytics function's own signature out
# ---------------------------------------------------------------------------


def indicators(ctx: CapabilityContext, params: IndicatorParams) -> pl.DataFrame:
    """Resample the symbol's trades into bars, then append the requested indicators.

    The query is the crypto CLI's ``indicators`` command end to end, via
    ``CrypcodileClient.get_indicators``: resample with ``fill_empty=True``, sort by
    ``bar``, then compute. It is copied rather than invented because the two differ in ways
    that change the numbers — without ``fill_empty`` a quiet hour is simply absent from the
    series, so a 14-period SMA silently spans a different amount of wall-clock time on a
    thin symbol than on a busy one.

    An empty frame is passed through to :func:`apply_indicators` rather than returned
    early, so an unknown ``indicator`` name is still rejected on a lake with no data. The
    early return looked equivalent and made a typo depend on whether the symbol had trades.
    """
    bars = resample_ohlcv(
        ctx.catalog,
        params.symbol,
        params.start_ns,
        params.end_ns,
        params.interval,
        fill_empty=True,
    )
    if not bars.is_empty():
        bars = bars.sort("bar")
    return apply_indicators(bars, params.indicator, params.period)


def slippage(ctx: CapabilityContext, params: SlippageParams) -> pl.DataFrame:
    """Walk the stored book for the requested size. A pure argument shuffle."""
    return estimate_slippage(
        ctx.catalog,
        params.symbol,
        params.side,
        params.size,
        params.size_unit,
    )


def basis(ctx: CapabilityContext, params: BasisParams) -> pl.DataFrame:
    """ASOF-join the perpetual's mark price onto the nearest prior spot print."""
    return spot_perp_basis(
        ctx.catalog,
        params.spot_symbol,
        params.perp_symbol,
        params.start_ns,
        params.end_ns,
    )


def perp_basis(ctx: CapabilityContext, params: PerpBasisParams) -> pl.DataFrame:
    """Mark price against index price, off the perpetual's own ticker channel."""
    return _perp_basis(ctx.catalog, params.symbol, params.start_ns, params.end_ns)


def spot_future_basis(ctx: CapabilityContext, params: SpotFutureBasisParams) -> pl.DataFrame:
    """ASOF-join spot onto the future's prints, annualising when an expiry is given."""
    return _spot_future_basis(
        ctx.catalog,
        params.future_symbol,
        params.spot_symbol,
        params.start_ns,
        params.end_ns,
        params.expiry_ns,
    )


def funding_apr(ctx: CapabilityContext, params: FundingAprParams) -> pl.DataFrame:
    """Per-settlement funding APR and the running sum. A pure argument shuffle."""
    return _funding_apr(ctx.catalog, params.symbol, params.start_ns, params.end_ns)


def funding_predict(ctx: CapabilityContext, params: FundingPredictParams) -> dict[str, Any]:
    """Forecast the next settlement's funding rate from a supplied rate history.

    ``ctx`` is unused because this capability reads nothing: the history arrives in
    ``params`` and the model is offline. It still takes the context, because a projector
    that had to know which implementations want one would be introspecting signatures
    again — the failure :data:`~crocodile.core.capability.CapabilityFn` rules out.

    ``predict_next_funding`` wants a list or tuple; ``rates`` is already a tuple, so this is
    the argument shuffle it looks like and not a conversion.
    """
    return predict_next_funding(params.rates, window_size=params.window_size)


def iv_surface(ctx: CapabilityContext, params: IvSurfaceParams) -> pl.DataFrame:
    """The implied-vol cross-section at one instant, latest row per strike/expiry/type."""
    return _iv_surface(ctx.catalog, params.underlying, params.at_ns, params.rate)


def term_structure(ctx: CapabilityContext, params: TermStructureParams) -> pl.DataFrame:
    """ATM implied vol per expiry — the surface read down its ATM column."""
    return _term_structure(ctx.catalog, params.underlying, params.at_ns, params.rate)


def vol_skew(ctx: CapabilityContext, params: VolSkewParams) -> pl.DataFrame:
    """Per-strike implied vol and delta for a single expiry."""
    return _vol_skew(
        ctx.catalog,
        params.underlying,
        params.expiry_ns,
        params.at_ns,
        params.rate,
    )


def risk_reversal(ctx: CapabilityContext, params: RiskReversalParams) -> dict[str, float | None]:
    """Solve the skew for one expiry, then read the risk reversal and butterfly off it.

    Two calls, because ``risk_reversal_butterfly`` takes the skew frame rather than a
    catalog — all three legacy surfaces make the same pair (cli.py:3789,
    api_server.py:2463, mcp_server.py:645), and none of them caches the intermediate.

    The empty skew is short-circuited rather than passed through. ``risk_reversal_butterfly``
    returns ``(None, None)`` for an empty frame anyway, so this is the legacy guard kept for
    its cost rather than its result: without it every no-data call still walks the frame's
    column set.

    Returns the two measurements and not the request that produced them. The REST route
    echoes ``underlying``, ``expiry_ns``, ``at`` and both parameters back into its body
    (api_server.py:2478) and the MCP tool does not (mcp_server.py:649); echoing is a
    surface's answer to "what did I ask for", and a surface already knows.
    """
    skew = _vol_skew(
        ctx.catalog,
        params.underlying,
        params.expiry_ns,
        params.at_ns,
        params.rate,
    )
    if skew.is_empty():
        return {"risk_reversal": None, "butterfly": None}
    rr, bf = risk_reversal_butterfly(skew, target_delta=params.target_delta)
    return {"risk_reversal": rr, "butterfly": bf}


def ofi(ctx: CapabilityContext, params: OfiParams) -> pl.DataFrame:
    """Order-flow imbalance per time bin, from consecutive top-of-book snapshots."""
    return calculate_ofi(
        ctx.catalog,
        params.symbol,
        params.start_ns,
        params.end_ns,
        params.interval,
    )


def liquidity_depth(ctx: CapabilityContext, params: LiquidityDepthParams) -> pl.DataFrame:
    """Cumulative bid and ask size within 1, 2 and 5 percent of mid, per book sequence."""
    return calculate_block_liquidity_depth(ctx.catalog, params.symbol)


def whale_alerts(ctx: CapabilityContext, params: WhaleAlertsParams) -> pl.DataFrame:
    """Trades and liquidations whose notional clears the threshold. An argument shuffle."""
    return track_whale_alerts(
        ctx.catalog,
        params.symbol,
        params.start_ns,
        params.end_ns,
        params.min_usd,
    )


def smart_money(ctx: CapabilityContext, params: SmartMoneyParams) -> list[dict[str, Any]]:
    """Net flow, volume and last activity per watched address, over supplied transfers.

    Normalising the watchlist first is what lets the three document shapes REST accepted
    keep working; ``summarize_smart_money`` alone treats a mapping as ``addr -> label`` and
    would read ``{"addresses": [...]}`` as a single address named ``addresses``.

    The legacy handlers return early on an empty watchlist (api_server.py:2977,
    mcp_server.py:866). That branch is dropped rather than copied: an empty address set
    matches no transfer, so the tracker yields no rows by construction, and a guard whose
    only effect is to reach the same empty list sooner is a second place for the two answers
    to differ.
    """
    return summarize_smart_money(params.transfers, normalize_watchlist(params.watchlist))


def label_transfers(ctx: CapabilityContext, params: LabelTransfersParams) -> list[dict[str, Any]]:
    """Annotate supplied transfer rows with watchlist labels, optionally filtering first.

    ``filter_transfers_by_usd`` runs before labelling, which is the order all three surfaces
    use (cli.py:4726, api_server.py:3059, mcp_server.py:946). It is kept because it is what
    they did and not because it changes anything: both steps are row-wise maps over disjoint
    keys — the filter reads ``usd_value`` and its aliases, the labeller reads ``from`` and
    ``to`` — so swapping them is unobservable. Stated because it looks like it should
    matter, and a reader who assumed it did would go looking for the case where it bites.

    ``known_only`` is genuinely ordered: it reads ``is_known``, which does not exist until
    the labeller has written it.

    ``crocodile.crypto.analytics.whale_transfers`` is imported here rather than at module
    scope because it imports ``web3`` and ``eth_abi`` at *its* module scope, and those are
    the ``onchain`` extra. A top-level import would make ``crocodile.capabilities.analytics``
    unimportable on a base install, which ``load_all()`` deliberately refuses to swallow —
    so an optional dependency would take the whole registry, and all three surfaces with it.
    The two functions used here touch neither package.
    """
    from crocodile.crypto.analytics.whale_transfers import (
        filter_transfers_by_usd,
        label_transfer_addresses,
    )

    rows: list[dict[str, Any]] = list(params.transfers)
    if params.min_usd is not None:
        rows = filter_transfers_by_usd(rows, params.min_usd)
    labelled = label_transfer_addresses(rows, normalize_watchlist(params.watchlist))
    if params.known_only:
        return [row for row in labelled if row.get("is_known")]
    return labelled


def chaos_score(ctx: CapabilityContext, params: ChaosScoreParams) -> float:
    """Blend four stress readings into one soft-thresholded index in ``[0, 100]``.

    Returns the score alone. Both REST and MCP echo the four inputs back beside it
    (api_server.py:2600, mcp_server.py:801); a surface that was handed the inputs does not
    need the capability to hand them back, and a scalar capability that returns a
    five-field object is not a scalar.
    """
    return calculate_chaos_score(
        volatility=params.volatility,
        stablecoin_deviation=params.stablecoin_deviation,
        orderbook_imbalance=params.orderbook_imbalance,
        sequencer_delay=params.sequencer_delay,
    )


# ---------------------------------------------------------------------------
# Declarations
# ---------------------------------------------------------------------------


SLIPPAGE = declare(
    Capability(
        name="slippage",
        summary="Expected execution price and slippage for a size, against the stored book.",
        params=SlippageParams,
        returns=ReturnKind.SCALAR,
        # One capability, two wire names. `slippage` is the crypto CLI command, the crypto
        # REST GET route and (as `estimate_slippage`) the MCP tool; `simulate-price-impact`
        # is a REST POST on both sides and the only spelling equity ever exposed. The name
        # is `slippage` because it names the measurement rather than an action performed on
        # a UI, and because one name here becomes a command, a path segment and a tool name
        # at once — an imperative reads wrong as two of those three.
        #
        # That equity exposes only the other spelling is not evidence for it: equity has no
        # CLI and no MCP at all, so the "shared" name is an artefact of equity having almost
        # nothing rather than of the name being the better one.
        aliases=("simulate-price-impact",),
        impls={
            AssetClass.CRYPTO: Impl(fn=slippage, prov=Provenance.DERIVED, basis="native"),
            # An equity book is modelled from volume bars unless an Alpaca key upgrades it
            # to L1, so an estimate walked over it is SYNTHETIC on its best day. Declaring
            # the keyed ceiling here would let a keyless deployment report a level it never
            # reaches; which of the two a given snapshot actually was is on the snapshot's
            # own tail, where it can be measured rather than promised.
            AssetClass.EQUITY: Impl(
                fn=slippage, prov=Provenance.SYNTHETIC, basis="yahoo_1m_vap"
            ),
        },
    )
)


INDICATORS = declare(
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
            AssetClass.CRYPTO: Impl(fn=indicators, prov=Provenance.DERIVED, basis="native"),
            AssetClass.EQUITY: Impl(fn=indicators, prov=Provenance.DERIVED, basis="native"),
        },
    )
)


# The three basis capabilities stay three. The CLI is one `basis` command that infers its
# mode from which of --perp/--future/--spot were given (cli.py:3012), and REST and MCP are
# three endpoints each; collapsing to one capability would mean adopting the CLI's shape,
# which costs more than it reads like. One capability has one params struct, and the three
# modes do not share one: the legs differ in arity and in name, `expiry_ns` is meaningful in
# exactly one of them, and the returned columns differ (`spot_price`/`perp_price` versus
# `mark_price`/`index_price` versus `future_price`/`spot_price` plus `annualized_pct`). A
# merged struct would publish four optional symbol fields with no combination marked
# required, and the rule for which combinations are legal would live in the adapter as a
# re-implementation of `_basis_mode` — an inference the MCP inputSchema could not express
# and the OpenAPI schema could not either. Mode inference is a convenience a CLI projection
# can keep offering over three capabilities; it is not part of the contract.


BASIS = declare(
    Capability(
        name="basis",
        summary="Spot against perpetual mark, ASOF-joined onto the nearest prior print.",
        params=BasisParams,
        returns=ReturnKind.TABLE,
        impls={
            # Both legs are venue-reported: perpetual marks off `derivative_ticker`, spot
            # off `trade` with `book_snapshot` mid as the documented fallback. The join and
            # the two ratios are this implementation's own work, which is what makes the
            # result DERIVED rather than the NATIVE its inputs are.
            AssetClass.CRYPTO: Impl(fn=basis, prov=Provenance.DERIVED, basis="native"),
        },
    )
)


PERP_BASIS = declare(
    Capability(
        name="perp-basis",
        summary="Perpetual mark price against its index price, per ticker update.",
        params=PerpBasisParams,
        returns=ReturnKind.TABLE,
        impls={
            AssetClass.CRYPTO: Impl(fn=perp_basis, prov=Provenance.DERIVED, basis="native"),
        },
    )
)


SPOT_FUTURE_BASIS = declare(
    Capability(
        name="spot-future-basis",
        summary="Dated future against spot, annualised when a contract expiry is given.",
        params=SpotFutureBasisParams,
        returns=ReturnKind.TABLE,
        impls={
            AssetClass.CRYPTO: Impl(
                fn=spot_future_basis, prov=Provenance.DERIVED, basis="native"
            ),
        },
    )
)


FUNDING_APR = declare(
    Capability(
        name="funding-apr",
        summary="Per-settlement funding APR and cumulative funding for a perpetual.",
        params=FundingAprParams,
        returns=ReturnKind.TABLE,
        impls={
            AssetClass.CRYPTO: Impl(fn=funding_apr, prov=Provenance.DERIVED, basis="native"),
        },
    )
)


FUNDING_PREDICT = declare(
    Capability(
        name="funding-predict",
        summary="Next-period funding rate forecast from a supplied rate history.",
        params=FundingPredictParams,
        returns=ReturnKind.SCALAR,
        impls={
            # SYNTHETIC, and it is the only reasonable ceiling: the value reported is a
            # settlement that has not happened, so nothing observed it at any level. The
            # method — XGBoost when the `ml` extra is installed, a rolling mean otherwise —
            # is named in the returned object's `method` field, where it varies per call.
            #
            # `native` names where the *inputs* came from, and this is the batch's weakest
            # basis: the rates arrive in `params` rather than out of the lake, so what is
            # really being claimed is "funding rates as a venue settles them", which is what
            # the parameter documents and not something this capability can check. A
            # registered basis for caller-supplied inputs would say it properly; there is
            # none, and inventing a confidence formula to get one is the thing the registry
            # exists to forbid. Reported rather than papered over.
            AssetClass.CRYPTO: Impl(
                fn=funding_predict, prov=Provenance.SYNTHETIC, basis="native"
            ),
        },
    )
)


IV_SURFACE = declare(
    Capability(
        name="iv-surface",
        summary="Implied-volatility cross-section for an underlying at one instant.",
        params=IvSurfaceParams,
        returns=ReturnKind.TABLE,
        impls={
            # DERIVED is the ceiling. On its best day every row's `iv` is the venue's own
            # `mark_iv` and the surface is those points arranged into a cross-section — the
            # venue reported the points, not the surface, which is the same argument
            # `ohlcv_from_trades` makes for a bar. It is not NATIVE for that reason and not
            # SYNTHETIC either, because the fallback path inverts Black-76 on a venue mark
            # price rather than modelling a price from some other data class. Which of the
            # three each row actually took is on the row, in its `source` column.
            AssetClass.CRYPTO: Impl(fn=iv_surface, prov=Provenance.DERIVED, basis="native"),
        },
    )
)


TERM_STRUCTURE = declare(
    Capability(
        name="term-structure",
        summary="ATM implied volatility per expiry for an underlying at one instant.",
        params=TermStructureParams,
        returns=ReturnKind.TABLE,
        impls={
            AssetClass.CRYPTO: Impl(
                fn=term_structure, prov=Provenance.DERIVED, basis="native"
            ),
        },
    )
)


VOL_SKEW = declare(
    Capability(
        name="vol-skew",
        summary="Per-strike implied volatility and delta for a single option expiry.",
        params=VolSkewParams,
        returns=ReturnKind.TABLE,
        impls={
            AssetClass.CRYPTO: Impl(fn=vol_skew, prov=Provenance.DERIVED, basis="native"),
        },
    )
)


RISK_REVERSAL = declare(
    Capability(
        name="risk-reversal",
        summary="Risk reversal and butterfly at a target delta, from one expiry's skew.",
        params=RiskReversalParams,
        returns=ReturnKind.SCALAR,
        impls={
            AssetClass.CRYPTO: Impl(
                fn=risk_reversal, prov=Provenance.DERIVED, basis="native"
            ),
        },
    )
)


OFI = declare(
    Capability(
        name="ofi",
        summary="Order-flow imbalance per time bin, from consecutive book snapshots.",
        params=OfiParams,
        returns=ReturnKind.TABLE,
        impls={
            AssetClass.CRYPTO: Impl(fn=ofi, prov=Provenance.DERIVED, basis="native"),
        },
    )
)


LIQUIDITY_DEPTH = declare(
    Capability(
        name="liquidity-depth",
        summary="Bid and ask size within 1, 2 and 5 percent of mid, per book sequence.",
        params=LiquidityDepthParams,
        returns=ReturnKind.TABLE,
        impls={
            AssetClass.CRYPTO: Impl(
                fn=liquidity_depth, prov=Provenance.DERIVED, basis="native"
            ),
        },
    )
)


WHALE_ALERTS = declare(
    Capability(
        name="whale-alerts",
        summary="Trades and liquidations whose notional clears a USD threshold.",
        params=WhaleAlertsParams,
        returns=ReturnKind.TABLE,
        impls={
            AssetClass.CRYPTO: Impl(
                fn=whale_alerts, prov=Provenance.DERIVED, basis="native"
            ),
        },
    )
)


SMART_MONEY = declare(
    Capability(
        name="smart-money",
        summary="Net flow, volume and last activity per watched address, from transfers.",
        params=SmartMoneyParams,
        returns=ReturnKind.TABLE,
        impls={
            # DERIVED: every output field is a sum or a max over the transfers supplied, and
            # nothing here is modelled. `native` carries the same caveat as `funding-predict`
            # — the rows arrive in `params`, so the claim is about what a transfer row is
            # rather than about a channel this capability read.
            AssetClass.CRYPTO: Impl(
                fn=smart_money, prov=Provenance.DERIVED, basis="native"
            ),
        },
    )
)


LABEL_TRANSFERS = declare(
    Capability(
        name="label-transfers",
        summary="Annotate transfer rows with watchlist labels, optionally filtered by USD.",
        params=LabelTransfersParams,
        returns=ReturnKind.TABLE,
        impls={
            AssetClass.CRYPTO: Impl(
                fn=label_transfers, prov=Provenance.DERIVED, basis="native"
            ),
        },
    )
)


CHAOS_SCORE = declare(
    Capability(
        name="chaos-score",
        summary="Composite market-stress index in [0, 100] from four supplied readings.",
        params=ChaosScoreParams,
        returns=ReturnKind.SCALAR,
        impls={
            # SYNTHETIC: the reported quantity is an index, and no market publishes one.
            # Its four terms are soft-thresholded and averaged, so the number is modelled
            # from a different data class than any of them — which is the definition, and
            # also why a chaos score is not comparable with anything but another chaos
            # score. `native` carries the caller-supplied caveat `funding-predict` states.
            AssetClass.CRYPTO: Impl(
                fn=chaos_score, prov=Provenance.SYNTHETIC, basis="native"
            ),
        },
    )
)


# ---------------------------------------------------------------------------
# The schedule for this batch's equity halves
# ---------------------------------------------------------------------------

PENDING_SYMMETRY.update(
    {
        # M5 — carry. All four are the same trade priced four ways: the spread between a
        # derivative and its spot or index leg, annualised. Equity has the legs already and
        # is missing the risk-free rate that turns a spread into a carry, which is exactly
        # the keyless `treasury` provider M5 adds. `funding-predict` is on the same method
        # because it forecasts the series `funding-apr` produces; there is nothing for its
        # equity half to predict until `carry` exists.
        "basis": "M5",
        "perp-basis": "M5",
        "spot-future-basis": "M5",
        "funding-apr": "M5",
        "funding-predict": "M5",
        # M1 — the options family, all four of it. `term-structure`, `vol-skew` and
        # `risk-reversal` are `iv-surface` read three ways and call it directly, so they
        # land the moment the equity chain does.
        "iv-surface": "M1",
        "term-structure": "M1",
        "vol-skew": "M1",
        "risk-reversal": "M1",
        # M7 — order-flow imbalance from L1 quote changes, which is this measurement's
        # equity form: the crypto implementation differences consecutive top-of-book
        # snapshots, and an equity quote stream is the same two prices and two sizes.
        "ofi": "M7",
        # M6 — depth. The crypto implementation reads a real ladder out of `book_snapshot`;
        # equity has no ladder until the synthetic VAP profile and its Alpaca L1 upgrade
        # exist, and cumulative size within a percent band is meaningless without one.
        "liquidity-depth": "M6",
        # M4 — the equity form of "who is moving size" is a filing, not a transfer:
        # Form 4 for insiders and 13F-HR for institutions. `whale-alerts` becomes large
        # reported transactions, `smart-money` becomes per-filer flow, and
        # `label-transfers` becomes the same watchlist join against named filers, which is
        # the one place equity is *better* served — a CIK is already a label.
        "whale-alerts": "M4",
        "smart-money": "M4",
        "label-transfers": "M4",
        # M6, and this is the batch's one uncomfortable mapping — no single spec method
        # covers a composite. Of the four terms, volatility is computable from equity bars
        # today, and `stablecoin_deviation` and `sequencer_delay` name phenomena this same
        # registry calls IRREDUCIBLE, so their equity readings have to be re-specified
        # rather than ported. What blocks an equity chaos score *mechanically* is the one
        # term neither available nor re-definable without new data: order-book imbalance
        # needs both sides' resting size, which is precisely what M6 delivers. M6 is named
        # because it is the binding constraint, not because it is the whole of the work.
        "chaos-score": "M6",
    }
)
