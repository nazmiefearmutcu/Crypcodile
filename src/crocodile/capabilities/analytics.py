"""Capabilities computed from stored records.

Owns ``basis``, ``funding-apr``, ``iv-surface``, ``vol-skew``, ``term-structure``,
``risk-reversal``, ``slippage``, ``ofi``, ``indicators``, ``liquidity-depth`` and the rest
of the analytics family — anything whose inputs are rows already in the lake.

Two are declared here today. Both show the adapter pattern the port repeats: the analytics
function keeps the signature its own domain wants, and a module-level function named after
the capability turns ``(ctx, params)`` into that signature's arguments. Do not reshape an
analytics function to fit the registry — ``apply_indicators`` takes a frame because a frame
is what it computes over, and a version of it that took a
:class:`~crocodile.core.capability.CapabilityContext` would be a worse function that is
also harder to test.

The adapters are module-level and named, not lambdas or partials, so that a stack trace and
the calling-convention gate both point at something with a file and a line number.
"""

from __future__ import annotations

import msgspec
import polars as pl

from crocodile.core.analytics.indicators import apply_indicators
from crocodile.core.analytics.slippage import estimate_slippage
from crocodile.core.capability import (
    AssetClass,
    Capability,
    CapabilityContext,
    Impl,
    ReturnKind,
    declare,
)
from crocodile.core.resample.ohlcv import resample_ohlcv
from crocodile.core.schema.provenance import Provenance

__all__ = ["INDICATORS", "SLIPPAGE", "IndicatorParams", "SlippageParams"]


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
