"""A small real lake, so the surface tests exercise a capability rather than a mock.

Gate 4 proves the three projections carry the same names. It cannot prove that invoking
one of them reaches an implementation and comes back with data — and "the name is there,
the answer is empty" is precisely the failure this merge already shipped once, when seven
capabilities were promoted away and nothing raised.
"""

from __future__ import annotations

import dataclasses
import pathlib
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
import pytest_asyncio

from crocodile.core import capability as capability_module
from crocodile.core.capability import REGISTRY, Capability, Impl
from crocodile.core.schema.enums import AssetClass, Side
from crocodile.core.schema.records import BookSnapshot, Record, Trade
from crocodile.core.store.parquet_sink import ParquetSink

SYMBOL = "deribit:BTC-PERPETUAL"
START_NS = 1_700_000_000_000_000_000  # 2023-11-14
END_NS = START_NS + 3_600 * 1_000_000_000  # +1 h, one 1d bucket either way

_TRADE_COUNT = 40
_TRADE_SPACING_NS = 60 * 1_000_000_000  # one minute apart, so 1m bars are one per trade


def _trade(index: int) -> Trade:
    return Trade(
        source="deribit",
        symbol=SYMBOL,
        symbol_raw="BTC-PERPETUAL",
        local_ts=START_NS + index * _TRADE_SPACING_NS,
        asset_class=AssetClass.CRYPTO,
        source_ts=None,
        id=str(index),
        # A sawtooth rather than a straight line: a constant series gives RSI a zero
        # denominator, and a test whose indicator column is all-null cannot tell a working
        # projection from a broken one.
        price=42_000.0 + (index % 7) * 25.0,
        amount=0.25,
        side=Side.BUY if index % 2 else Side.SELL,
    )


def _book() -> BookSnapshot:
    return BookSnapshot(
        source="deribit",
        symbol=SYMBOL,
        symbol_raw="BTC-PERPETUAL",
        local_ts=START_NS + _TRADE_COUNT * _TRADE_SPACING_NS,
        asset_class=AssetClass.CRYPTO,
        source_ts=None,
        bids=[(41_990.0, 5.0), (41_980.0, 10.0)],
        asks=[(42_010.0, 5.0), (42_020.0, 10.0)],
        depth=2,
    )


@pytest_asyncio.fixture
async def lake(tmp_path: pathlib.Path) -> AsyncIterator[pathlib.Path]:
    """Write forty trades and one book snapshot, and yield the lake root."""
    sink = ParquetSink(data_dir=tmp_path, max_buffer_rows=10_000, flush_interval_seconds=9999)
    records: list[Record] = [_trade(i) for i in range(_TRADE_COUNT)]
    records.append(_book())
    for record in records:
        await sink.put(record)
    await sink.flush()
    yield tmp_path


@pytest.fixture
def indicator_query() -> dict[str, str]:
    """The arguments every surface below sends, so the three are asking the same thing."""
    return {
        "symbol": SYMBOL,
        "start_ns": str(START_NS),
        "end_ns": str(END_NS),
        "interval": "1m",
        "indicator": "rsi",
        "period": "5",
    }


@dataclasses.dataclass(frozen=True)
class FakeSubscription:
    """Shaped like :class:`~crocodile.capabilities.ops.Subscription`, connecting to nothing.

    A real ``collect`` opens a websocket to a live venue, which is not a thing a test may do.
    What is under test is what a *surface* does with the object, and that is decided entirely
    by the three fields a surface is allowed to read off it plus the run it has to start.
    """

    sources: tuple[str, ...]
    channels: tuple[str, ...]
    duration_seconds: float | None

    async def run(self) -> None:
        return None


@pytest.fixture
def collecting_nothing() -> Iterator[None]:
    """Replace ``collect``'s implementations with one that opens no sockets.

    ``STREAM`` is the one return shape whose real implementation cannot be exercised in a
    test, which is exactly why it kept shipping broken — so it is substituted at the registry
    rather than skipped, and everything above the implementation is the real thing.
    """
    original = REGISTRY["collect"]

    def _fake(ctx: Any, params: Any) -> FakeSubscription:
        from crocodile.capabilities.ops import _refuse_readonly

        _refuse_readonly(ctx, "collect")
        return FakeSubscription(
            sources=tuple(params.sources),
            channels=tuple(params.channels),
            duration_seconds=params.duration_seconds,
        )

    REGISTRY["collect"] = Capability(
        name=original.name,
        summary=original.summary,
        params=original.params,
        returns=original.returns,
        aliases=original.aliases,
        impls={
            asset_class: Impl(fn=_fake, prov=impl.prov, basis=impl.basis)
            for asset_class, impl in original.impls.items()
        },
    )
    capability_module._DECLARED_NAMES.add(original.name)
    try:
        yield
    finally:
        REGISTRY["collect"] = original
