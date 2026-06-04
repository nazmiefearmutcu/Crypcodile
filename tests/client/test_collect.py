"""Acceptance tests for the live collect orchestrator (Task 3.4).

Two fake connectors drain into one ParquetSink concurrently.
Files must appear after collect() returns.
When one connector raises mid-run the other is unaffected (isolated errors).
SIGINT path is covered by a direct cancellation test.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from crocodile.exchanges.deribit.connector import DeribitConnector
from crocodile.ingest.transport import FakeTransport
from crocodile.instruments.registry import InstrumentRegistry
from crocodile.schema.records import Trade
from crocodile.store.parquet_sink import ParquetSink

# ─── fixture helpers ────────────────────────────────────────────────────────

_TRADES_FRAME = json.dumps(
    {
        "params": {
            "channel": "trades.BTC-PERPETUAL.raw",
            "data": [
                {
                    "trade_id": "t1",
                    "price": 30000.0,
                    "amount": 1.0,
                    "direction": "buy",
                    "timestamp": 1700000000000,
                    "instrument_name": "BTC-PERPETUAL",
                }
            ],
        }
    }
).encode()

_ETH_TRADES_FRAME = json.dumps(
    {
        "params": {
            "channel": "trades.ETH-PERPETUAL.raw",
            "data": [
                {
                    "trade_id": "e1",
                    "price": 2000.0,
                    "amount": 5.0,
                    "direction": "sell",
                    "timestamp": 1700000001000,
                    "instrument_name": "ETH-PERPETUAL",
                }
            ],
        }
    }
).encode()


def _make_connector(
    sink: ParquetSink,
    frames: list[bytes],
    symbol: str = "BTC-PERPETUAL",
) -> DeribitConnector:
    conn = DeribitConnector(
        symbols=[symbol],
        channels=["trade"],
        out=sink,
        registry=InstrumentRegistry(),
    )
    conn.transport = FakeTransport(frames=frames)
    return conn


# ─── tests ──────────────────────────────────────────────────────────────────


def _find_parquets(base: pathlib.Path, pattern: str = "*.parquet") -> list[pathlib.Path]:
    """Collect parquet files synchronously (avoids ASYNC240)."""
    return list(base.rglob(pattern))


async def test_collect_two_connectors_write_parquet_files(tmp_path: pathlib.Path) -> None:
    """Two fake connectors draining into one ParquetSink → files appear on disk."""
    from crocodile.client.collect import collect

    sink = ParquetSink(data_dir=tmp_path, max_buffer_rows=1, flush_interval_seconds=9999)
    conn1 = _make_connector(sink, frames=[_TRADES_FRAME], symbol="BTC-PERPETUAL")
    conn2 = _make_connector(sink, frames=[_ETH_TRADES_FRAME], symbol="ETH-PERPETUAL")

    await collect([conn1, conn2], sink)

    parquet_files = _find_parquets(tmp_path)
    assert len(parquet_files) > 0, "No Parquet files written after collect()"

    # Every file belongs to the 'trade' channel
    for p in parquet_files:
        assert "channel=trade" in str(p)


async def test_collect_records_reach_sink(tmp_path: pathlib.Path) -> None:
    """Records emitted by connectors actually land in the sink."""
    from crocodile.client.collect import collect
    from crocodile.sink.memory import MemorySink

    sink = MemorySink()
    # Two connectors, different symbols, each emitting one Trade
    conn1 = _make_connector(sink, frames=[_TRADES_FRAME], symbol="BTC-PERPETUAL")  # type: ignore[arg-type]
    conn2 = _make_connector(sink, frames=[_ETH_TRADES_FRAME], symbol="ETH-PERPETUAL")  # type: ignore[arg-type]

    await collect([conn1, conn2], sink)  # type: ignore[arg-type]

    trades = [r for r in sink.records if isinstance(r, Trade)]
    assert len(trades) == 2
    prices = {t.price for t in trades}
    assert prices == {30000.0, 2000.0}


async def test_collect_one_connector_raises_does_not_crash(tmp_path: pathlib.Path) -> None:
    """When one connector raises an exception the others complete normally.

    collect() must not propagate the exception — each connector is supervised
    in isolation, matching the plan's "isolated" requirement.
    """
    from crocodile.client.collect import collect
    from crocodile.sink.memory import MemorySink

    class _BrokenTransport(FakeTransport):
        async def connect(self) -> None:
            raise RuntimeError("simulated connect failure")

    sink = MemorySink()

    # Connector A — will raise on connect
    conn_bad = _make_connector(sink, frames=[], symbol="BTC-PERPETUAL")  # type: ignore[arg-type]
    conn_bad.transport = _BrokenTransport(frames=[])

    # Connector B — healthy; emits one trade
    conn_good = _make_connector(sink, frames=[_ETH_TRADES_FRAME], symbol="ETH-PERPETUAL")  # type: ignore[arg-type]

    # Must not raise — bad connector is isolated
    await collect([conn_bad, conn_good], sink)  # type: ignore[arg-type]

    trades = [r for r in sink.records if isinstance(r, Trade)]
    # The good connector delivered its record
    assert len(trades) == 1
    assert trades[0].price == 2000.0


async def test_collect_empty_connectors_is_noop(tmp_path: pathlib.Path) -> None:
    """collect([]) with an empty list returns immediately without error."""
    from crocodile.client.collect import collect
    from crocodile.sink.memory import MemorySink

    sink = MemorySink()
    await collect([], sink)  # type: ignore[arg-type]
    assert sink.records == []


async def test_collect_sigint_closes_sink(tmp_path: pathlib.Path) -> None:
    """Simulated cancellation (analogous to SIGINT) triggers sink.close().

    We can't send a real SIGINT in a unit test, so we cancel the task that
    runs collect() and verify the sink is flushed / closed.
    """
    import asyncio

    from crocodile.client.collect import collect

    closed: list[bool] = []

    class _TrackCloseSink(ParquetSink):
        async def close(self) -> None:
            closed.append(True)
            await super().close()

    # Infinite transport — never stops on its own
    class _InfiniteTransport(FakeTransport):
        async def _iter(self) -> None:  # type: ignore[override]
            while True:
                await asyncio.sleep(0)
                yield _TRADES_FRAME

    sink = _TrackCloseSink(data_dir=tmp_path, max_buffer_rows=100, flush_interval_seconds=9999)
    conn = _make_connector(sink, frames=[], symbol="BTC-PERPETUAL")  # type: ignore[arg-type]
    conn.transport = _InfiniteTransport(frames=[])

    task = asyncio.create_task(collect([conn], sink))  # type: ignore[arg-type]
    # Give it a moment to start
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert closed, "sink.close() was not called on cancellation"
