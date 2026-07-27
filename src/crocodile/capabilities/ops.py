"""Capabilities that move data, plus the names no equity analogue can have.

Owns ``collect``, ``collect-market``, ``backfill``, ``replay``, ``export`` and
``resample``, and five of the six entries of :data:`IRREDUCIBLE
<crocodile.core.capability.IRREDUCIBLE>` — ``gas-vol``, ``mev-sandwich``,
``sequencer-latency``, ``peg-deviation`` and ``lending-stress``. Those five are declared
here so the exemption list and the declarations it exempts can be read side by side; the
justification stays on ``IRREDUCIBLE``, where the gate looks for it.

Two names this batch was asked to port are deliberately **not** declared, and each refusal
is argued where the declaration would have gone: ``gas-tracker`` (a Qt window, not a
capability — see :func:`_why_gas_tracker_is_not_a_capability`) and ``migrate-lake`` (a
directory rename, not a capability — see :func:`_why_migrate_lake_is_infrastructure`).
:data:`UNDECLARED` is the ledger of both, so a name declined on purpose cannot be mistaken
for one that was forgotten — which is the shape of the seven capabilities Phase 1 lost.

What ``ReturnKind.STREAM`` means, per surface
---------------------------------------------
``collect`` is the first capability in the product that never returns, so this is where
:attr:`ReturnKind.STREAM <crocodile.core.capability.ReturnKind.STREAM>` acquires a
meaning. The line that matters is not laziness — ``replay`` is lazy and is a ``TABLE`` —
it is **who owns the end of the sequence**:

``TABLE`` / ``SCALAR``
    finite, and the caller asked for all of it. Whether the implementation materialises it
    is an efficiency question the surface may answer either way.
``STREAM``
    unbounded, and the caller *subscribes*. There is no last element, so somebody other
    than the data has to decide when it stops.

A ``STREAM`` implementation therefore returns a :class:`Subscription` — a run that has not
started — and never awaits anything itself. That is not a stylistic choice. A capability
adapter cannot know whether it is being called from a synchronous CLI or from inside an
already-running event loop, and ``asyncio.run()`` raises ``RuntimeError`` in the second
case: an adapter that drove its own loop would work on the CLI and fail on REST and MCP,
which is precisely the shape this registry exists to end. The same rule makes ``backfill``
return its coroutine unstarted even though it is bounded.

The three surfaces then differ only in how long they hold the subscription:

CLI
    ``asyncio.run(sub.run())``. Unbounded is the native idiom here — the operator ends it
    with SIGINT — and ``duration_seconds`` bounds it when a run has to be unattended.
REST
    A request/response route cannot hold an unbounded subscription; the client would keep
    a socket open forever and every proxy between them would time it out first. The
    projection requires ``duration_seconds`` and answers when the bound expires. Because
    the capability hands back a run that has not started, the route can reject an
    unbounded request *before* a single socket is opened, rather than discovering it by
    hanging.
MCP
    Identical, and for a sharper reason: a tool call is one JSON-RPC request and one
    response, under a client-side timeout the server never sees. MCP's subscription
    primitive is a *resource*, not a tool, and this registry projects tools — so
    ``duration_seconds`` is required and must be smaller than the client's timeout.

Writers and surface trust
-------------------------
``collect``, ``collect-market`` and ``backfill`` write to the lake.
:attr:`CapabilityContext.readonly <crocodile.core.capability.CapabilityContext.readonly>`
is documented as how far a surface is trusted, and a surface that does not trust its
callers with mutating SQL cannot coherently trust them to start an unbounded write into
the operator's lake. :func:`_refuse_readonly` is that check, in one place, so the policy
cannot be forgotten per capability the way the SQL guard was forgotten per surface.

What does *not* belong here, or anywhere in this package: infrastructure. ``health``,
``ready``, ``version``, ``metrics``, ``docs``, ``/``, ``/api/events``, the x402 payment
routes, and the launchers ``mcp``, ``api``, ``update``, ``shell``, ``flowmap`` and
``gas-tracker`` are hand-written on the surfaces that have them. A capability is something
with an asset class, a parameter schema and a provenance; a readiness probe has none of
the three, and registering one would put it in the symmetry gate's subject list where it
can only ever be answered with an exemption.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
from collections.abc import Callable, Coroutine, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
from crocodile.core.connector import Connector
from crocodile.core.replay.merge import replay as merge_replay
from crocodile.core.resample.ohlcv import resample_ohlcv
from crocodile.core.schema.provenance import Provenance
from crocodile.core.schema.records import Record
from crocodile.core.sink.base import Sink
from crocodile.core.store.parquet_sink import ParquetSink
from crocodile.core.store.rows import from_row
from crocodile.crypto.analytics.gas_vol_correlation import gas_to_volatility_correlation
from crocodile.crypto.analytics.lending_stress import lending_stress_test
from crocodile.crypto.analytics.mev_sandwich import detect_sandwiches
from crocodile.crypto.analytics.peg_deviation import (
    calculate_peg_deviation,
    peg_deviation_from_price,
)
from crocodile.crypto.analytics.sequencer_latency import calculate_sequencer_latency
from crocodile.crypto.client.backfill import run_historical_backfill
from crocodile.crypto.client.collect import collect as collect_crypto
from crocodile.crypto.client.export import export as export_rows
from crocodile.equity.client.collect import collect as collect_equity

__all__ = [
    "BACKFILL",
    "COLLECT",
    "COLLECT_MARKET",
    "EXPORT",
    "GAS_VOL",
    "LENDING_STRESS",
    "MEV_SANDWICH",
    "PEG_DEVIATION",
    "REPLAY",
    "RESAMPLE",
    "SEQUENCER_LATENCY",
    "BackfillParams",
    "CollectMarketParams",
    "CollectParams",
    "ExportParams",
    "GasVolParams",
    "LendingStressParams",
    "MevSandwichParams",
    "PegDeviationParams",
    "ReplayParams",
    "ResampleParams",
    "SequencerLatencyParams",
    "Subscription",
]


# ---------------------------------------------------------------------------
# Shared machinery
# ---------------------------------------------------------------------------


def _refuse_readonly(ctx: CapabilityContext, capability: str) -> None:
    """Refuse a lake-mutating capability on a surface that declared itself read-only.

    ``PermissionError`` rather than ``ValueError`` — which is what
    :func:`~crocodile.core.store.catalog.assert_readonly_sql` raises — because the two are
    different answers. ``ValueError`` on this path already means *your parameters are
    wrong*, and a caller that retries with better parameters is doing the right thing with
    it. This one says the parameters were fine and the surface is not trusted to run the
    capability at all, which a REST projection maps to 403 and a caller must not retry.
    """
    if ctx.readonly:
        raise PermissionError(
            f"capability {capability!r} writes to the lake and this surface is read-only; "
            f"a surface that does not trust its callers with mutating SQL cannot trust "
            f"them to start a write into the operator's lake"
        )


@dataclass(frozen=True, slots=True)
class Subscription:
    """An unbounded collection run that has not started: what ``ReturnKind.STREAM`` returns.

    Everything the request can be judged on has already happened — the parameters are
    validated and the connectors are built — and nothing has connected. That ordering is
    the point: a surface that cannot host an unbounded subscription rejects the request
    after it is known to be well formed and before any socket exists, instead of
    discovering the problem by hanging.

    :attr:`sources` and :attr:`channels` are here so a surface can say what it is about to
    subscribe to before it commits to holding the connection. The symbol list deliberately
    is not: ``collect-market`` resolves its symbols from a live venue universe inside
    :attr:`begin`, so a count promised here would be a guess for one of the two capabilities
    that returns this type.
    """

    sources: tuple[str, ...]
    """The venues or providers this run will collect from."""

    channels: tuple[str, ...]
    """The channels it will subscribe to on each of them."""

    duration_seconds: float | None
    """Wall-clock bound, or ``None`` for "until cancelled".

    ``None`` is only servable by a surface that owns a process — see the module docstring.
    """

    begin: Callable[[], Coroutine[Any, Any, None]]
    """The unstarted run. Call :meth:`run` instead unless the surface owns the timer."""

    async def run(self) -> None:
        """Collect until the declared bound expires, or until cancelled if there is none.

        ``asyncio.timeout`` rather than a hand-rolled canceller task: it converts its own
        expiry into ``TimeoutError`` while letting an *outer* cancellation through
        unchanged. The CLI's hand-rolled version could not tell the two apart — it caught
        ``CancelledError`` from the collect task either way — so a ``--duration-seconds``
        run and a Ctrl-C looked identical to the caller, and a SIGINT arriving during a
        bounded run was swallowed rather than propagated.
        """
        if self.duration_seconds is None:
            await self.begin()
            return
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(self.duration_seconds):
                await self.begin()


def _lake_sink(ctx: CapabilityContext) -> ParquetSink:
    """Open the sink every writing capability writes through.

    ``data_dir`` is read from :attr:`CapabilityContext.settings
    <crocodile.core.capability.CapabilityContext.settings>` and is not a parameter, for the
    reason the context exists at all: which lake this deployment owns is the surface's to
    know, and a caller-supplied lake root on a network surface is a caller choosing where
    the server writes.
    """
    return ParquetSink(
        data_dir=ctx.settings.data_dir,
        max_buffer_rows=10_000,
        flush_interval_seconds=5.0,
    )


def _rows_to_frame(rows: list[dict[str, Any]]) -> pl.DataFrame:
    """Build a frame from caller-supplied JSON rows, tolerating an empty series.

    ``pl.DataFrame([])`` is a zero-column frame rather than an error, which is exactly what
    the correlator's ``is_empty()`` branch expects, so no schema has to be invented for the
    empty case — and inventing one is how a column name the caller never sent ends up
    deciding which column the correlator picks.
    """
    return pl.DataFrame(rows)


# ---------------------------------------------------------------------------
# Part 1 — the IRREDUCIBLE five, and the sixth that is not a capability
# ---------------------------------------------------------------------------
#
# Every implementation below rests on inputs that are read rather than reconstructed, so
# each declares ``basis="native"``. For the three that compute over *caller-supplied*
# numbers — gas-vol, mev-sandwich, lending-stress, and peg-deviation's pure mode — that is
# the closest registered basis and it is the wrong shade: ``native`` says a *venue*
# reported the value, and a series pasted in from a CSV has no venue behind it that this
# process can see. What these want is a `caller_supplied` basis registered at
# ``Provenance.NATIVE`` with a constant 1.0 and the argument that a pure function is exact
# over whatever it was handed. Registering one is a change to
# ``core/schema/provenance.py``, which this batch does not own, so the gap is reported
# rather than filled — and filled by feel is the one thing the provenance registry exists
# to refuse.


class GasVolParams(msgspec.Struct, frozen=True):
    """Parameters for ``gas-vol``: two caller-supplied series, aligned on ``local_ts``.

    Rows rather than file paths. The crypto CLI takes ``--gas-file`` / ``--vol-file`` and
    REST takes a JSON body, and only one of those can be the schema the three surfaces
    share — a server-side path in a published parameter schema is a network caller naming a
    file on the server's disk. So the rows are the capability and reading a CSV into rows
    is a CLI affordance, the same category as its interactive symbol picker.

    ``dict[str, Any]`` and not ``dict[str, float]``: ``local_ts`` is a nanosecond epoch,
    which passes 2**53 and stops being exactly representable as a float. A float-typed
    schema would silently round the join key of every row.
    """

    gas: list[dict[str, Any]]
    vol: list[dict[str, Any]]


def gas_vol(ctx: CapabilityContext, params: GasVolParams) -> dict[str, float]:
    """Correlate a gas series against a volatility series. A pure argument shuffle."""
    return gas_to_volatility_correlation(_rows_to_frame(params.gas), _rows_to_frame(params.vol))


class MevSandwichParams(msgspec.Struct, frozen=True):
    """Parameters for ``mev-sandwich``: an offline trade sequence, and what to return of it.

    ``sandwiches_only`` is the crypto CLI's ``--sandwiches-only`` flag, which neither REST
    nor MCP had. It defaults to ``False``, which is what those two do, so keeping it costs
    a caller that omits it nothing while dropping it would delete the only way to ask the
    question the flag exists for — a sandwich hunt over a large sequence wants the legs,
    not the sequence back with a mostly-false column.
    """

    trades: list[dict[str, Any]]
    sandwiches_only: bool = False


def mev_sandwich(ctx: CapabilityContext, params: MevSandwichParams) -> pl.DataFrame:
    """Flag the frontrun / victim / backrun legs of same-block same-pool sandwiches.

    An empty ``trades`` returns an empty table rather than reaching the detector, which is
    what REST and MCP both do. The opposite reflex is the right one for ``indicators``,
    where passing an empty frame through is what keeps a misspelled indicator name being
    rejected on a lake with no data — but there is no parameter here whose validity the
    detector's column check decides, so the early return withholds no error. An empty
    sequence has no columns to be missing.
    """
    if not params.trades:
        return pl.DataFrame(schema={"is_sandwich": pl.Boolean})
    flagged = detect_sandwiches(_rows_to_frame(params.trades))
    if params.sandwiches_only:
        return flagged.filter(pl.col("is_sandwich"))
    return flagged


class SequencerLatencyParams(msgspec.Struct, frozen=True):
    """Parameters for ``sequencer-latency``.

    REST also took a ``limit``; it is not here. A row cap on a network surface is
    :attr:`CapabilityContext.row_limit
    <crocodile.core.capability.CapabilityContext.row_limit>`, a property of the surface
    rather than of the question — the same rule that keeps the SQL policy out of ``query``'s
    parameters. It never bound anything in any case: the answer is two summary rows and the
    route's floor was one.
    """

    exchange: str = "base_onchain"


def sequencer_latency(ctx: CapabilityContext, params: SequencerLatencyParams) -> pl.DataFrame:
    """Block production interval and local ingestion delay, from stored venue timestamps.

    The blank-to-default normalisation is the CLI's and REST's, kept because both of them
    did it: an explicitly empty ``--exchange`` means "the default chain", not "no chain",
    and without it the query filters on a source no record carries and reports an empty
    lake.
    """
    exchange = params.exchange.strip() or "base_onchain"
    return calculate_sequencer_latency(ctx.catalog, exchange)


class PegDeviationParams(msgspec.Struct, frozen=True):
    """Parameters for ``peg-deviation``, covering both of the modes that shipped.

    The crypto CLI had two modes and REST and MCP had one. ``--symbol`` reads a stored
    quote series out of the lake; ``--price`` (or ``--bid``/``--ask``) evaluates one mid
    with no lake at all. REST and MCP exposed only the second, and added a ``target`` the
    CLI did not have. One struct now has to hold all of it, and the arithmetic of "which
    mode" is: a derivable mid means the pure mode, a symbol and no mid means the lake mode,
    neither is an error.

    Nothing is dropped in either direction. The lake mode is reachable from all three
    surfaces for the first time, and ``target`` applies to both — see
    :func:`peg_deviation` for why that costs a recomputation.
    """

    symbol: str | None = None
    price: float | None = None
    bid: float | None = None
    ask: float | None = None
    threshold: float = 0.01
    target: float = 1.0


_PEG_COLUMNS = ("timestamp", "symbol", "price", "deviation_pct", "is_alert_triggered", "threshold")
"""One column vocabulary for both modes, which is what lets one ``ReturnKind`` describe them.

The two modes answer in different shapes — one evaluation against a series of them — and a
capability has exactly one :class:`~crocodile.core.capability.ReturnKind`. Emitting the
union and letting the pure mode be a one-row table is the move
``crocodile.core.resample.ohlcv`` already made when two divergent resamplers became one:
neither column set was a superset, both were correct, so the survivor emits both. Here
``timestamp`` and ``symbol`` are null in the pure mode because a mid the caller typed in
happened at no time and to no instrument, and ``threshold`` is echoed in the lake mode
because a row that carries the bar it was judged against can be read without the request
beside it.
"""


def peg_deviation(ctx: CapabilityContext, params: PegDeviationParams) -> pl.DataFrame:
    """Measure how far a stablecoin sits from its peg, from one price or from the lake.

    ``target`` is applied in the lake mode by re-deriving the deviation from the price
    series the lake query returns, because ``calculate_peg_deviation`` hard-codes a 1.0 peg
    and takes no target. Recomputing unconditionally rather than only when
    ``target != 1.0`` keeps the branch that runs the same as the branch under test; at the
    default the two agree to the bit. The alternative was to accept ``target`` and ignore it
    on one of the two modes, and a parameter an implementation silently drops is worse than
    one it never offered — the caller gets an answer to a question they did not ask, with
    nothing in the result to say so.

    Raises:
        ValueError: if neither a mid price nor a symbol was given. The surfaces each
            phrased this refusal themselves; there is one phrasing now.
    """
    mid = params.price
    if mid is None and params.bid is not None and params.ask is not None:
        mid = (float(params.bid) + float(params.ask)) / 2.0

    if mid is not None:
        one = peg_deviation_from_price(mid, threshold=params.threshold, target=params.target)
        return pl.DataFrame(
            {
                "timestamp": pl.Series([None], dtype=pl.Int64),
                "symbol": pl.Series([params.symbol], dtype=pl.Utf8),
                "price": pl.Series([float(one["price"])], dtype=pl.Float64),
                "deviation_pct": pl.Series([float(one["deviation_pct"])], dtype=pl.Float64),
                "is_alert_triggered": pl.Series(
                    [bool(one["is_alert_triggered"])], dtype=pl.Boolean
                ),
                "threshold": pl.Series([float(one["threshold"])], dtype=pl.Float64),
            }
        ).select(list(_PEG_COLUMNS))

    if not params.symbol:
        raise ValueError(
            "peg-deviation needs either a mid price (price, or bid and ask) for the pure "
            "mode or a symbol for the lake mode"
        )

    series = calculate_peg_deviation(ctx.catalog, params.symbol, params.threshold)
    return (
        series.with_columns((pl.col("price") - params.target).abs().alias("deviation_pct"))
        .with_columns(
            (pl.col("deviation_pct") >= params.threshold).alias("is_alert_triggered"),
            pl.lit(params.threshold, dtype=pl.Float64).alias("threshold"),
        )
        .select(list(_PEG_COLUMNS))
    )


class LendingStressParams(msgspec.Struct, frozen=True):
    """Parameters for ``lending-stress``: a position, and the collateral drop to test it at.

    All four are required, which is the CLI's arity and the MCP tool's ``required`` list.
    REST defaulted every one of them to ``0.0`` and so answered a bare ``GET`` with an
    infinite health factor on a position of nothing — a number with the shape of an answer
    and no question behind it. Required is the honest schema; the REST default was a
    FastAPI convenience, not a contract.
    """

    collateral_usd: float
    debt_usd: float
    liquidation_threshold: float
    haircut_pct: float


def lending_stress(ctx: CapabilityContext, params: LendingStressParams) -> dict[str, Any]:
    """Health factor now and after a collateral haircut, plus the inputs it was judged on.

    The inputs are echoed because all three surfaces echoed them, and for a reason that
    survives the port: ``haircut_pct`` is normalised inside the calculation — ``20`` and
    ``0.20`` both mean twenty percent — so a result without its inputs cannot be checked
    against the request that produced it.
    """
    result = lending_stress_test(
        collateral_usd=params.collateral_usd,
        debt_usd=params.debt_usd,
        liquidation_threshold=params.liquidation_threshold,
        haircut_pct=params.haircut_pct,
    )
    return {
        "collateral_usd": params.collateral_usd,
        "debt_usd": params.debt_usd,
        "liquidation_threshold": params.liquidation_threshold,
        "haircut_pct": params.haircut_pct,
        **result,
    }


def _why_gas_tracker_is_not_a_capability() -> str:
    """Return the argument for leaving ``gas-tracker`` undeclared, so a test can assert it.

    ``gas-tracker`` is on :data:`~crocodile.core.capability.IRREDUCIBLE` and has no
    declaration here, which is a contradiction on the face of it — the exemption list names
    something the registry will never contain. The resolution is that it was never a
    capability:

    * It takes **no parameters**. The CLI command has zero options; there is no schema for
      the three surfaces to share.
    * It **returns nothing**. It opens a ``QMainWindow`` and blocks on a Qt event loop until
      the operator closes it, then calls ``sys.exit``.
    * It has **no provenance**. It renders whatever the gas oracle streams; it observes
      nothing itself, so there is no level ``prov`` could name and no method ``basis``
      could name.
    * It has **no asset class**, and could not acquire one. Its ``IRREDUCIBLE``
      justification — "L1/L2 gas markets have no equity analogue" — is an argument about
      *gas data*, which ``gas-vol`` already carries. What is undeclarable here is not the
      gas, it is the window.
    * It exists on exactly one surface and cannot reach the other two. There is no
      projection of "open a window on the operator's desktop" onto an HTTP route or a
      JSON-RPC tool.

    ``flowmap`` is the same thing — a Qt launcher over lake data — and it is *not* on
    ``IRREDUCIBLE``. That inconsistency is the evidence: the two were classified together
    as launchers by the survey, and only one of them picked up an exemption it never needed.

    The entry is left in place because ``core/capability.py`` is not this batch's to edit
    and because removing a name from ``IRREDUCIBLE`` is a claim of its own. The
    recommendation to the coordinator is to drop it, at which point the list is five names
    and every one of them is declared below.
    """
    return "gas-tracker launches a Qt window: no params, no return, no provenance, no class"


GAS_VOL = declare(
    Capability(
        name="gas-vol",
        summary="Pearson and Spearman correlation between gas costs and volatility.",
        params=GasVolParams,
        returns=ReturnKind.SCALAR,
        impls={
            AssetClass.CRYPTO: Impl(fn=gas_vol, prov=Provenance.DERIVED, basis="native"),
        },
    )
)


MEV_SANDWICH = declare(
    Capability(
        name="mev-sandwich",
        summary="Flag sandwich-attack legs in a same-block, same-pool trade sequence.",
        params=MevSandwichParams,
        returns=ReturnKind.TABLE,
        impls={
            AssetClass.CRYPTO: Impl(fn=mev_sandwich, prov=Provenance.DERIVED, basis="native"),
        },
    )
)


SEQUENCER_LATENCY = declare(
    Capability(
        name="sequencer-latency",
        summary="Block production interval and ingestion delay for an L2 sequencer.",
        params=SequencerLatencyParams,
        returns=ReturnKind.TABLE,
        impls={
            # The venue stamped every ``source_ts`` and the capture stamped every
            # ``local_ts``; the summary over their differences is what is computed.
            AssetClass.CRYPTO: Impl(
                fn=sequencer_latency, prov=Provenance.DERIVED, basis="native"
            ),
        },
    )
)


PEG_DEVIATION = declare(
    Capability(
        name="peg-deviation",
        summary="Stablecoin deviation from its peg, from one mid price or from the lake.",
        params=PegDeviationParams,
        returns=ReturnKind.TABLE,
        impls={
            AssetClass.CRYPTO: Impl(fn=peg_deviation, prov=Provenance.DERIVED, basis="native"),
        },
    )
)


LENDING_STRESS = declare(
    Capability(
        name="lending-stress",
        summary="Health factor of a lending position, now and under a collateral haircut.",
        params=LendingStressParams,
        returns=ReturnKind.SCALAR,
        impls={
            AssetClass.CRYPTO: Impl(fn=lending_stress, prov=Provenance.DERIVED, basis="native"),
        },
    )
)


# ---------------------------------------------------------------------------
# Part 2 — lake operations
# ---------------------------------------------------------------------------


class CollectParams(msgspec.Struct, frozen=True):
    """Parameters for ``collect``, identical for both asset classes.

    ``sources`` rather than ``exchanges`` or ``providers``. The two forks named the same
    thing twice and ``core.store.migrate`` already settled the merged spelling — the lake's
    top-level partition key is ``source=``, and the parameter that decides it should not
    disagree with the directory it produces.

    Two fields come from the crypto signature and have no equity counterpart, and they are
    resolved in opposite directions:

    ``data_dir`` is **gone**. It is the lake root, which
    :attr:`CapabilityContext.settings <crocodile.core.capability.CapabilityContext.settings>`
    already knows; a caller-supplied lake root in a published schema is a network caller
    choosing where the server writes.

    ``dlq_report_path`` **stays**, equity-ignored, on the argument ``SlippageParams`` makes
    for ``size_unit``: an optional parameter costs a caller that omits it nothing, while
    dropping it deletes the only way to say where a crypto run's dead-letter report lands.
    Equity providers have no dead-letter queue at all, so there is nothing there for it to
    mean and nothing lost by its being ignored.
    """

    sources: tuple[str, ...]
    symbols: tuple[str, ...]
    channels: tuple[str, ...]
    max_reconnects: int = -1
    duration_seconds: float | None = None
    dlq_report_path: str | None = None


def _require_collect_inputs(params: CollectParams) -> None:
    """Refuse an empty source, symbol or channel list before anything is constructed.

    ``collect([])`` closes the sink and returns immediately, which reads to a caller as a
    run that started and ended — the CLI guarded against this itself, on both forks, and a
    guard each surface writes for itself is a guard one of them will not write.
    """
    missing = [
        name
        for name, value in (
            ("sources", params.sources),
            ("symbols", params.symbols),
            ("channels", params.channels),
        )
        if not value
    ]
    if missing:
        raise ValueError(f"collect needs a non-empty {', '.join(missing)}")


def collect(ctx: CapabilityContext, params: CollectParams) -> Subscription:
    """Subscribe to live crypto venues and write every record into the lake.

    Symbols are passed to the connector exactly as given. The crypto CLI normalises them
    first — ``BTC`` becomes ``BTCUSDT`` on binance and ``BTC-PERPETUAL`` on deribit — but
    that normaliser lives in the legacy CLI module beside its Typer app, and a capability
    that imported it would drag a surface into the registry. So this takes exchange-native
    symbols and normalisation stays a surface affordance — which is a real behavioural
    difference from the crypto CLI command, and the follow-up that closes it is to lift
    ``normalize_user_symbol`` into ``crypto.instruments`` where both can reach it.
    """
    _refuse_readonly(ctx, "collect")
    _require_collect_inputs(params)

    from crocodile.core.ingest.transport import AiohttpWsTransport
    from crocodile.crypto.exchanges.factory import make_connector
    from crocodile.crypto.instruments.registry import InstrumentRegistry

    sink = _lake_sink(ctx)
    registry = InstrumentRegistry()
    connectors = []
    for source in params.sources:
        connector = make_connector(
            exchange=source,
            symbols=list(params.symbols),
            channels=list(params.channels),
            out=sink,
            registry=registry,
        )
        if connector.transport is None:
            connector.transport = AiohttpWsTransport(connector.ws_url)
        connectors.append(connector)

    async def begin() -> None:
        await collect_crypto(
            connectors,
            sink,
            max_reconnects=params.max_reconnects,
            dlq_report_path=params.dlq_report_path,
            data_dir=ctx.settings.data_dir,
        )

    return Subscription(
        sources=params.sources,
        channels=params.channels,
        duration_seconds=params.duration_seconds,
        begin=begin,
    )


def collect_equities(ctx: CapabilityContext, params: CollectParams) -> Subscription:
    """Subscribe to live equity providers and write every record into the lake.

    A separate adapter rather than a branch on :attr:`CapabilityContext.asset_class`
    because the two halves genuinely differ below the surface: the factories build
    different base classes from different registries, and the crypto orchestrator drains a
    dead-letter queue the equity one does not have. One function with a mode flag would put
    that difference inside an ``if`` where the calling-convention gate cannot see it.
    """
    _refuse_readonly(ctx, "collect")
    _require_collect_inputs(params)

    from crocodile.core.ingest.transport import AiohttpWsTransport
    from crocodile.equity.providers.factory import make_provider
    from crocodile.equity.reference.registry import InstrumentRegistry

    sink = _lake_sink(ctx)
    registry = InstrumentRegistry()
    providers = []
    for source in params.sources:
        provider = make_provider(
            provider=source,
            symbols=list(params.symbols),
            channels=list(params.channels),
            out=sink,
            registry=registry,
        )
        if provider.transport is None:
            provider.transport = AiohttpWsTransport(provider.ws_url)
        providers.append(provider)

    async def begin() -> None:
        await collect_equity(providers, sink, max_reconnects=params.max_reconnects)

    return Subscription(
        sources=params.sources,
        channels=params.channels,
        duration_seconds=params.duration_seconds,
        begin=begin,
    )


class CollectMarketParams(msgspec.Struct, frozen=True):
    """Parameters for ``collect-market``: a slice of a venue's market, not a symbol list.

    This is ``collect`` with the symbols resolved from the venue's live universe instead of
    named by the caller, which is the whole of what makes it a separate capability — every
    other field here exists to describe the slice.

    ``top`` and ``all_symbols`` are mutually exclusive and one of them is required; the two
    say "the N most liquid" and "everything that matches, up to ``limit``", and a request
    that asks for both has asked for two different market slices.
    """

    sources: tuple[str, ...]
    channels: tuple[str, ...]
    top: int | None = None
    all_symbols: bool = False
    quote: str | None = "USDT"
    kinds: tuple[str, ...] = ()
    limit: int = 500
    poll_interval: float = 2.0
    use_ws: bool = False
    book_depth: int = 50
    max_reconnects: int = -1
    duration_seconds: float | None = None
    dlq_report_path: str | None = None


def collect_market(ctx: CapabilityContext, params: CollectMarketParams) -> Subscription:
    """Subscribe to a whole slice of one or more crypto venues' markets.

    The slice is resolved inside the subscription rather than here, because resolving it is
    itself asynchronous — it queries the venue — and an adapter that awaited would have to
    own an event loop, which is the failure the module docstring rejects. Everything that
    can be judged without the network *is* judged here, so a malformed request fails before
    a subscription exists rather than on first await.
    """
    _refuse_readonly(ctx, "collect-market")

    from crocodile.crypto.instruments.registry import Kind

    if not params.sources or not params.channels:
        raise ValueError("collect-market needs a non-empty sources and channels")
    if params.top is None and not params.all_symbols:
        raise ValueError("collect-market needs either top=N or all_symbols=True")
    if params.top is not None and params.all_symbols:
        raise ValueError("collect-market takes either top=N or all_symbols=True, not both")

    # An empty ``quote`` means "any quote currency", which is how the CLI spelled it; the
    # universe filter spells the same thing ``None``.
    quote = params.quote or None
    kinds = {Kind(k.lower()) for k in params.kinds} or None

    async def begin() -> None:
        from crocodile.core.ingest.transport import AiohttpWsTransport
        from crocodile.crypto.exchanges.ccxt_universal.connector import CCXTConnector
        from crocodile.crypto.exchanges.factory import list_ccxt_exchanges, make_connector
        from crocodile.crypto.instruments.registry import InstrumentRegistry
        from crocodile.crypto.instruments.universe import (
            exchange_instruments,
            filter_instruments,
            top_symbols_by_volume,
        )

        resolved: dict[str, list[str]] = {}
        for source in params.sources:
            if params.top is not None:
                symbols = await top_symbols_by_volume(
                    source, params.top, quote=quote, kinds=kinds
                )
            else:
                instruments = await exchange_instruments(source)
                matched = filter_instruments(instruments, kinds=kinds, quote=quote)
                symbols = [i.symbol_raw for i in matched][: params.limit]
            if symbols:
                resolved[source] = symbols
        if not resolved:
            raise ValueError(
                f"no symbols matched the requested market slice on any of {params.sources}"
            )

        sink = _lake_sink(ctx)
        registry = InstrumentRegistry()
        ccxt_ids = set(list_ccxt_exchanges())
        connectors: list[Connector] = []
        for source, symbols in resolved.items():
            connector: Connector
            # Every ccxt-supported venue must use the universal connector here, including
            # the five that also have a native one: the universe resolves ccxt's unified
            # spelling ("BTC/USDT") and the native connectors expect the venue's own
            # ("BTCUSDT"), so a native connector fed from this path subscribes to nothing.
            if source in ccxt_ids:
                connector = CCXTConnector(
                    symbols=symbols,
                    channels=list(params.channels),
                    out=sink,
                    registry=registry,
                    ccxt_id=source,
                    poll_interval=params.poll_interval,
                    use_ws=params.use_ws,
                    book_depth=params.book_depth,
                )
            else:
                connector = make_connector(
                    exchange=source,
                    symbols=symbols,
                    channels=list(params.channels),
                    out=sink,
                    registry=registry,
                )
            if connector.transport is None:
                connector.transport = AiohttpWsTransport(connector.ws_url)
            connectors.append(connector)

        await collect_crypto(
            connectors,
            sink,
            max_reconnects=params.max_reconnects,
            dlq_report_path=params.dlq_report_path,
            data_dir=ctx.settings.data_dir,
        )

    return Subscription(
        sources=params.sources,
        channels=params.channels,
        duration_seconds=params.duration_seconds,
        begin=begin,
    )


class BackfillParams(msgspec.Struct, frozen=True):
    """Parameters for ``backfill``: a bounded historical fetch, written straight to the lake.

    The five venue-shaped fields at the bottom are the crypto REST APIs' own vocabulary and
    are equity-ignored, on the argument ``SlippageParams`` makes for ``size_unit``: dropping
    them deletes the only way to reach a Binance USD-M market or an OKX swap, and an equity
    caller that omits them pays nothing.
    """

    source: str
    channel: str
    symbols: tuple[str, ...]
    start_ns: int
    end_ns: int
    market: str = "spot"
    category: str = "linear"
    inst_type: str = "SWAP"
    interval: str = "1m"
    period: str = "5m"


def _check_backfill_range(params: BackfillParams) -> None:
    """Refuse an inverted time range before any venue is contacted.

    An inverted range is not an empty result; the per-venue REST helpers page forward from
    ``start_ns`` and a range that ends before it begins either fetches nothing or fetches
    everything, depending on which venue answered. The CLI checked this and REST and MCP
    never exposed the capability at all, so the check moves here rather than staying there.
    """
    if params.start_ns > params.end_ns:
        raise ValueError(f"backfill start_ns ({params.start_ns}) is after end_ns ({params.end_ns})")
    if not params.symbols:
        raise ValueError("backfill needs a non-empty symbols")


def backfill(ctx: CapabilityContext, params: BackfillParams) -> Coroutine[Any, Any, int]:
    """Fetch a historical range from a crypto venue's REST API into the lake.

    Returns the run **unstarted**, for the reason the module docstring gives: a capability
    cannot know whether its caller already owns an event loop, and ``asyncio.run`` from
    inside a FastAPI route raises. Awaiting it yields the number of records written.
    """
    _refuse_readonly(ctx, "backfill")
    _check_backfill_range(params)

    return run_historical_backfill(
        exchange=params.source,
        channel=params.channel,
        symbols=list(params.symbols),
        start_ns=params.start_ns,
        end_ns=params.end_ns,
        sink=_lake_sink(ctx),
        market=params.market,
        category=params.category,
        inst_type=params.inst_type,
        interval=params.interval,
        period=params.period,
    )


async def _drain_provider_backfill(
    source: str,
    params: BackfillParams,
    sink: Sink,
) -> int:
    """Page one equity provider's historical API into ``sink``, returning the record count.

    The crypto half of this is ``run_historical_backfill``; there was no equity half,
    because no equity surface ever exposed a backfill command. The provider API it needs
    has been there the whole time — :meth:`Provider.backfill
    <crocodile.equity.providers.base.Provider.backfill>` is on the base class and ``stooq``
    and ``msn_money`` implement it — so what was missing was the orchestration, not the
    capability. That is the difference between a name that belongs on
    ``PENDING_SYMMETRY`` and one that just needed assembling.

    The sink is closed in a ``finally`` on the same rule ``run_historical_backfill``
    follows: a partial fetch that raised has still written rows, and rows buffered in a
    sink nobody closed are rows that were fetched and lost.
    """
    from crocodile.equity.providers.factory import make_provider
    from crocodile.equity.reference.registry import InstrumentRegistry

    provider = make_provider(
        provider=source,
        symbols=list(params.symbols),
        channels=[params.channel],
        out=sink,
        registry=InstrumentRegistry(),
    )
    written = 0
    try:
        for symbol in params.symbols:
            async for record in provider.backfill(
                params.channel, symbol, params.start_ns, params.end_ns
            ):
                await sink.put(record)
                written += 1
    finally:
        await sink.close()
        # ``Provider`` does not declare ``close`` on its base, but the pull-only providers
        # that implement ``backfill`` open an aiohttp session in theirs. Leaving it open
        # leaks a connector and prints an unraisable warning from the GC thread, which is
        # a failure that surfaces nowhere near its cause.
        closer = getattr(provider, "close", None)
        if callable(closer):
            await closer()
    return written


def backfill_equities(ctx: CapabilityContext, params: BackfillParams) -> Coroutine[Any, Any, int]:
    """Fetch a historical range from an equity provider into the lake, unstarted."""
    _refuse_readonly(ctx, "backfill")
    _check_backfill_range(params)

    return _drain_provider_backfill(params.source, params, _lake_sink(ctx))


class ReplayParams(msgspec.Struct, frozen=True):
    """Parameters for ``replay``, identical for both asset classes."""

    channels: tuple[str, ...]
    symbols: tuple[str, ...]
    start_ns: int
    end_ns: int
    limit: int | None = None


def replay(ctx: CapabilityContext, params: ReplayParams) -> Iterator[Record]:
    """Merge the stored channels into one globally time-ordered record stream.

    ``TABLE`` and not ``STREAM``: the result is finite — the time range and the lake bound
    it — and the caller asked for all of it. That it arrives lazily is an efficiency
    property, and the whole point of the k-way merge is that it stays O(number of channels)
    in memory, so materialising it here would undo the engine it is built on.

    One implementation serves both asset classes, and that is a measured claim rather than
    an assumed one. The equity client scanned per (channel, symbol) where the crypto one
    scans per channel with the symbol list pushed into the query, which changes how many
    streams reach ``heapq.merge`` and not what comes out of it; and the crypto one also
    honours ``limit``, which the equity one had no way to express. The crypto path is
    therefore a superset in behaviour, not merely in signature.
    """
    if not params.symbols or not params.channels:
        return iter([])

    streams: list[Iterator[Record]] = []
    for channel in params.channels:
        # The per-channel limit is a safe upper bound rather than the answer: the first N
        # records of the merged stream are always inside the first N rows of each
        # time-ordered input, so pushing it down never drops a record the merge would
        # have chosen, and it stops a large lake being materialised for a small question.
        frame = ctx.catalog.scan(
            channel, list(params.symbols), params.start_ns, params.end_ns, limit=params.limit
        )
        if len(frame) > 0:
            streams.append(from_row(row) for row in frame.to_dicts())

    if not streams:
        return iter([])

    merged = merge_replay(streams)
    if params.limit is None:
        return merged
    # The per-channel bound above can still yield ``limit * len(channels)`` after the
    # merge, so the global bound is applied once more on the merged stream.
    return itertools.islice(merged, params.limit)


class ExportParams(msgspec.Struct, frozen=True):
    """Parameters for ``export``, identical for both asset classes.

    ``start_ns`` / ``end_ns`` rather than the CLI's ``--from`` / ``--to``, matching
    ``IndicatorParams``: the registry has one vocabulary for a nanosecond bound and the
    surfaces can keep their own flag spellings.

    ``dest`` is the one parameter in this batch that names a path on the machine running
    the capability, and it is the whole of what ``export`` does, so it cannot move to the
    context the way ``collect``'s ``data_dir`` did. On a local CLI that is exactly right.
    On a network surface it is a caller choosing where the server writes, which is a
    surface policy decision of the same family as
    :attr:`CapabilityContext.readonly <crocodile.core.capability.CapabilityContext.readonly>`
    — a REST or MCP projection has to confine it to a directory it owns rather than pass it
    through. Stated here because a hazard that is obvious on one surface and invisible on
    another is the kind that ships.
    """

    channel: str
    symbols: tuple[str, ...]
    start_ns: int
    end_ns: int
    dest: str
    fmt: str = "parquet"
    limit: int | None = None


def export(ctx: CapabilityContext, params: ExportParams) -> str:
    """Write a channel x symbols x time range to a file, and return where it went.

    One implementation for both asset classes, and the choice was measured rather than
    assumed — "identical twin" was the working description of
    ``crocodile.equity.client.export`` and it is not true. The two diverge in three places
    and the crypto one wins all three:

    * **Nested CSV columns.** Crypto JSON-encodes them; equity casts lists to a joined
      string. Every canonical record now carries ``prov_inputs``, a ``list[str]``, and the
      book channels carry ``bids``/``asks`` as ``list[struct]`` — which the equity cast
      cannot express at all.
    * **The empty result.** Crypto writes a schema-shaped empty frame, so an export of no
      rows is still a readable file that says what it would have held; equity writes a
      bare, column-less one.
    * **``limit`` push-down.** Crypto passes it into the scan; equity fetches everything
      and slices afterwards.

    Only one thing goes the other way — equity raised on a negative ``limit`` before
    scanning — and it is not lost: ``Catalog.scan`` raises ``ValueError("limit must be >=
    0")`` on the same input, one call deeper.
    """
    export_rows(
        ctx.catalog,
        params.channel,
        list(params.symbols),
        params.start_ns,
        params.end_ns,
        params.fmt,
        Path(params.dest),
        limit=params.limit,
    )
    return str(Path(params.dest))


class ResampleParams(msgspec.Struct, frozen=True):
    """Parameters for ``resample``, identical for both asset classes."""

    symbol: str
    start_ns: int
    end_ns: int
    interval: str = "1m"
    fill_empty: bool = False


def resample(ctx: CapabilityContext, params: ResampleParams) -> pl.DataFrame:
    """Aggregate stored trade prints into OHLCV bars. A pure argument shuffle.

    One function serves both because there is only one left:
    ``crocodile.core.resample.ohlcv`` is the survivor of two identically named resamplers
    that returned different columns over the same view, and it emits the union of what both
    produced. This capability existed on the equity CLI and nowhere else, which is what a
    fork looks like from the outside — the crypto client had the method and never exposed
    it.
    """
    return resample_ohlcv(
        ctx.catalog,
        params.symbol,
        params.start_ns,
        params.end_ns,
        params.interval,
        fill_empty=params.fill_empty,
    )


def _why_migrate_lake_is_infrastructure() -> str:
    """Return the argument for leaving ``migrate-lake`` undeclared, so a test can assert it.

    ``migrate_lake`` renames ``exchange=`` and ``provider=`` partition directories to
    ``source=``. It is a real operation and the CLI should keep offering it; it is not a
    capability, and the test is the registry's own vocabulary — asset class, parameter
    schema, provenance — none of which it can answer:

    * **No asset class.** It renames *both* legacy prefixes in a single pass over one lake.
      Declared with an impl per asset class, the two would be the same function doing the
      same whole-lake work and ignoring ``ctx.asset_class`` — and an impl keyed by a
      discriminator it cannot use is a capability that has no discriminator. That is not
      the ``indicators`` case, where one function serves both because OHLCV means the same
      thing in both markets; here there is one lake and one answer.
    * **No parameter schema.** Its only input is the lake root, which is
      ``ctx.settings.data_dir``. The struct would be empty.
    * **No provenance, and no honest way to fake one.** ``prov`` is "the best level this
      implementation can produce" and this one produces a count of renamed directories.
      ``NATIVE`` would claim a venue reported it; ``DERIVED`` would claim it was computed
      from records, and nothing was read — the whole point of the migration is that no
      Parquet file is opened; ``SYNTHETIC`` would claim modelling; ``UNAVAILABLE`` is a
      typed hole. All four are false statements about a directory rename, and a provenance
      chosen because the field is required is the first decorative provenance in the tree.

    The line this draws against ``collect`` and ``backfill``, which also mutate the lake:
    those two **acquire records**, which have a venue, an asset class and a level. This one
    moves directories. A capability moves or computes records; ``migrate-lake`` moves paths.

    One real asymmetry falls out of the decision and is worth the coordinator's attention:
    the command exists on the crypto CLI only, so an equity-only operator has no way to
    migrate a ``provider=`` lake without installing the crypto surface. The fix is the
    hand-written command on the equity CLI, which is a surfaces change and not this
    batch's.
    """
    return "migrate-lake renames directories: no asset class, no params, no provenance"


COLLECT = declare(
    Capability(
        name="collect",
        summary="Subscribe to live venues and write every record into the lake.",
        params=CollectParams,
        returns=ReturnKind.STREAM,
        impls={
            # NATIVE on both sides is the *ceiling*, and collect reaches it: what it writes
            # is what the venue streamed, unaltered. A synthetic provider behind one of
            # these names does not lower the ceiling; it lowers the tail on its own records,
            # which is where a per-record claim belongs.
            AssetClass.CRYPTO: Impl(fn=collect, prov=Provenance.NATIVE, basis="native"),
            AssetClass.EQUITY: Impl(fn=collect_equities, prov=Provenance.NATIVE, basis="native"),
        },
    )
)


COLLECT_MARKET = declare(
    Capability(
        name="collect-market",
        summary="Subscribe to a whole slice of a venue's market, resolved from its universe.",
        params=CollectMarketParams,
        returns=ReturnKind.STREAM,
        impls={
            AssetClass.CRYPTO: Impl(fn=collect_market, prov=Provenance.NATIVE, basis="native"),
        },
    )
)

# ``collect-market`` is ``collect`` with the symbol list resolved from a live venue
# universe, and that resolution is the entire equity gap: no equity source in the tree
# enumerates a universe. ``Provider.list_instruments`` describes the symbols it was handed
# rather than discovering any, and nothing ranks equities by traded volume, so neither the
# ``top`` slice nor the ``all`` slice has anything to resolve against. That is exactly
# SPEC_METHOD M3 — "Equity universe from SEC EDGAR x OpenFIGI x Tiingo, merged by
# CoverageResolver" — which makes this a schedule and not a market property, so it goes to
# PENDING_SYMMETRY and never to IRREDUCIBLE.
#
# Written here rather than in the dict's own module because ``core/capability.py`` is
# shared by four batches porting in parallel and an edit per batch is four conflicts in one
# file — the same reason the declarations left that module. ``setdefault`` so a coordinator
# who does write the entry there wins over this one instead of colliding with it.
PENDING_SYMMETRY.setdefault("collect-market", "M3")


BACKFILL = declare(
    Capability(
        name="backfill",
        summary="Fetch a historical range from a venue's REST API into the lake.",
        params=BackfillParams,
        returns=ReturnKind.SCALAR,
        impls={
            AssetClass.CRYPTO: Impl(fn=backfill, prov=Provenance.NATIVE, basis="native"),
            AssetClass.EQUITY: Impl(
                fn=backfill_equities, prov=Provenance.NATIVE, basis="native"
            ),
        },
    )
)


REPLAY = declare(
    Capability(
        name="replay",
        summary="Merge stored channels into one globally time-ordered record stream.",
        params=ReplayParams,
        returns=ReturnKind.TABLE,
        impls={
            # A relocation, not a derivation: every record comes back exactly as it was
            # stored, carrying the tail it was written with, so the ceiling is whatever the
            # lake holds.
            AssetClass.CRYPTO: Impl(fn=replay, prov=Provenance.NATIVE, basis="native"),
            AssetClass.EQUITY: Impl(fn=replay, prov=Provenance.NATIVE, basis="native"),
        },
    )
)


EXPORT = declare(
    Capability(
        name="export",
        summary="Write a channel, symbol set and time range out to a file.",
        params=ExportParams,
        returns=ReturnKind.SCALAR,
        impls={
            AssetClass.CRYPTO: Impl(fn=export, prov=Provenance.NATIVE, basis="native"),
            AssetClass.EQUITY: Impl(fn=export, prov=Provenance.NATIVE, basis="native"),
        },
    )
)


RESAMPLE = declare(
    Capability(
        name="resample",
        summary="Aggregate stored trade prints into OHLCV bars at a chosen interval.",
        params=ResampleParams,
        returns=ReturnKind.TABLE,
        impls={
            # ``basis`` names where the *inputs* came from and a trade print is reported by
            # the venue, which is the same reading that makes ``indicators`` native. The
            # method applied on top is on the emitted rows, as ``ohlcv_from_trades``, where
            # it is measured per bar rather than promised once here.
            AssetClass.CRYPTO: Impl(fn=resample, prov=Provenance.DERIVED, basis="native"),
            AssetClass.EQUITY: Impl(fn=resample, prov=Provenance.DERIVED, basis="native"),
        },
    )
)


UNDECLARED: dict[str, Callable[[], str]] = {
    "gas-tracker": _why_gas_tracker_is_not_a_capability,
    "migrate-lake": _why_migrate_lake_is_infrastructure,
}
"""Names this batch was asked to port and deliberately did not, with the argument for each.

A port that silently skips a name is indistinguishable from a port that forgot it, which
is the shape of the seven capabilities Phase 1 lost. Declining is allowed; declining
quietly is not, so each entry points at the function holding the argument and a test
asserts both that the name is absent from the registry and that the argument is there.

``gas-tracker`` is additionally on :data:`~crocodile.core.capability.IRREDUCIBLE`, which
this batch does not own. Leaving the entry there while declaring nothing is the reported
state, not the intended end state — see
:func:`_why_gas_tracker_is_not_a_capability`.
"""
