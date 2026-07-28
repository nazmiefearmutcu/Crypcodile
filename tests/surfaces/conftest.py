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
from crocodile.core.schema.records import BookSnapshot, OpenInterest, Record, Trade
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


OVERSIZED_ROWS = 12_000
"""How many rows the oversized lake holds: more than ``NETWORK_ROW_LIMIT``, deliberately.

The exact number matters less than the inequality, and the inequality is the whole point.
``test_rest_publishes_the_row_ceiling_it_applied`` asserted that the published ceiling
equalled the constant — against the forty-row lake above, where no ceiling can bind — so a
capability that never applied one passed a test named for applying it. A gate about a cap
has to drive a lake bigger than the cap or it is measuring the constant against itself.
"""

_OI_SOURCES = ("deribit", "binance-futures")
"""Two venues, so an unfiltered ``open-interest`` board has more than one column to fill."""


def _oversized_trade(index: int) -> Trade:
    """One of :data:`OVERSIZED_ROWS` trades, a minute apart, so 1m bars are one per trade."""
    return Trade(
        source="deribit",
        symbol=SYMBOL,
        symbol_raw="BTC-PERPETUAL",
        local_ts=START_NS + index * _TRADE_SPACING_NS,
        asset_class=AssetClass.CRYPTO,
        source_ts=None,
        id=str(index),
        price=42_000.0 + (index % 7) * 25.0,
        amount=0.25,
        side=Side.BUY if index % 2 else Side.SELL,
    )


def _open_interest(source: str, index: int) -> OpenInterest:
    return OpenInterest(
        source=source,
        symbol=f"{source}:BTC-PERPETUAL",
        symbol_raw="BTC-PERPETUAL",
        local_ts=START_NS + index * _TRADE_SPACING_NS,
        asset_class=AssetClass.CRYPTO,
        source_ts=None,
        open_interest=1_000.0 + index,
    )


@pytest_asyncio.fixture
async def oversized_lake(tmp_path: pathlib.Path) -> AsyncIterator[pathlib.Path]:
    """A lake with more rows than a network surface will return, plus an open-interest board.

    Two properties nothing else in this package has, and both of them are needed to see a
    defect rather than to assume one:

    * more than :data:`~crocodile.surfaces.dispatch.NETWORK_ROW_LIMIT` rows for a single
      symbol, so a published row ceiling either binds or is a false claim; and
    * ``open_interest`` records on two sources, so ``open-interest`` with no ``--symbols``
      has a non-empty answer to fail to return.

    Written once per test rather than shared at session scope because every surface here
    opens its own :class:`~crocodile.core.store.catalog.Catalog` over the directory and a
    ``tmp_path`` per test is what keeps them from seeing each other's writes.
    """
    sink = ParquetSink(data_dir=tmp_path, max_buffer_rows=20_000, flush_interval_seconds=9999)
    for index in range(OVERSIZED_ROWS):
        await sink.put(_oversized_trade(index))
    for source in _OI_SOURCES:
        for index in range(20):
            await sink.put(_open_interest(source, index))
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


@pytest.fixture(autouse=True)
def _equity_depth_ladder(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stand in for the live equity depth source, so no surface test reaches the internet.

    ``slippage``/equity used to be the same function as ``slippage``/crypto and read
    ``book_snapshot`` out of the lake, so the three tests below answered an *equity* request
    from the crypto book this conftest writes — and labelled the result ``yahoo_1m_vap``, a
    basis for a code path that did not exist. They passed on the strength of the defect.

    The equity half now walks the ladder ``depth`` already serves, which is a network fetch
    (Alpaca L1 when keyed, Yahoo 1m VAP when not). A surfaces test asserting that a
    projection carries a provenance banner should not depend on Yahoo being up or on how
    hard it is rate-limiting today, so the ladder is fixed here. Autouse because *no* test
    in this package should be making that call.
    """
    from crocodile.core.schema.records import DepthProfile

    profile = DepthProfile(
        source="alpaca",
        symbol=SYMBOL,
        symbol_raw="BTC-PERPETUAL",
        local_ts=START_NS,
        source_ts=None,
        asset_class=AssetClass.EQUITY,
        bids=[(41_990.0, 5.0), (41_980.0, 10.0)],
        asks=[(42_010.0, 5.0), (42_020.0, 10.0)],
        reference_price=42_000.0,
        depth=2,
    )

    class _FixedLadder:
        async def snapshot(self, symbol: str) -> DepthProfile:
            return profile

    monkeypatch.setattr(
        "crocodile.capabilities.analytics.select_depth_source", lambda **_: _FixedLadder()
    )
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
