"""``run_historical_backfill`` — the orchestrator, exercised without a command line.

The CLI half of this file is gone. ``backfill`` is a capability now
(:mod:`crocodile.capabilities.ops`), so the unsupported-exchange, missing-argument,
inverted-range and writes-Parquet cases are no longer properties of a hand-written Typer
command: ``tests/capabilities/test_ops.py`` pins the range guard and the unstarted-run
contract, and ``tests/conformance/test_surfaces.py`` pins that the name is reachable on all
three surfaces.

Two affordances did not survive that move and are named here rather than quietly dropped:
``--start``/``--end`` as aliases for ``--from``/``--to`` (the parameter is ``start_ns`` /
``end_ns`` on every surface now), and the per-venue symbol normalisation that turned a bare
``BTC`` into ``BTCUSDT`` for binance — ``CollectParams``' docstring records that the
normaliser stayed behind in the legacy CLI module and that closing the gap means lifting it
into ``crypto.instruments``.

What is below reaches the orchestrator directly, which is where the behaviour actually
lives, with fixture-backed fetch callbacks so no test touches the network.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

import pytest

from crocodile.crypto.exchanges.binance.backfill import BinanceBackfill

_FIXTURES = pathlib.Path(__file__).parent / "exchanges" / "binance" / "fixtures"

# Time bounds covering the fixture trade timestamps (T = 1700000000100 / 200 ms).
_START_NS = 1_700_000_000_000 * 1_000_000  # 1700000000000 ms → ns
_END_NS = 1_700_000_001_000 * 1_000_000


def _load_aggtrades() -> list[dict[str, Any]]:
    return json.loads((_FIXTURES / "rest_aggtrades.json").read_text())


def _make_fixture_binance_backfill() -> BinanceBackfill:
    """BinanceBackfill whose fetch callbacks return saved fixture pages (no HTTP)."""
    page = _load_aggtrades()

    async def fetch_aggtrades(
        symbol: str,
        from_id: int | None,
        start_time_ms: int | None,
        end_time_ms: int | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        # First call (time-bounded) returns fixture; subsequent fromId pages empty.
        if from_id is not None:
            return []
        return page

    return BinanceBackfill(
        fetch_aggtrades=fetch_aggtrades,
        fetch_klines=None,
        fetch_open_interest=None,
        fetch_open_interest_hist=None,
    )


async def test_run_historical_backfill_binance_direct(tmp_path: pathlib.Path) -> None:
    """``run_historical_backfill`` with injected BinanceBackfill writes rows."""
    from crocodile.core.store.parquet_sink import ParquetSink
    from crocodile.crypto.client.backfill import run_historical_backfill

    sink = ParquetSink(data_dir=tmp_path, max_buffer_rows=10_000, flush_interval_seconds=9999)
    count = await run_historical_backfill(
        exchange="binance",
        channel="trade",
        symbols=["BTCUSDT"],
        start_ns=_START_NS,
        end_ns=_END_NS,
        sink=sink,
        backfill_factory=_make_fixture_binance_backfill,
    )
    assert count == 2
    assert list(tmp_path.rglob("*.parquet"))


async def test_run_historical_backfill_unsupported_exchange() -> None:
    from crocodile.core.sink.memory import MemorySink
    from crocodile.crypto.client.backfill import run_historical_backfill

    sink = MemorySink()
    try:
        await run_historical_backfill(
            exchange="coinbase",
            channel="trade",
            symbols=["BTC-USD"],
            start_ns=0,
            end_ns=1,
            sink=sink,
        )
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "Unsupported exchange" in str(exc)
        assert "coinbase" in str(exc)


async def test_run_historical_backfill_unsupported_channel() -> None:
    """Binance has no funding REST backfill, and the orchestrator says so by name.

    Migrated from a CLI invocation: the guard is ``SUPPORTED_CHANNELS`` in
    ``crypto.client.backfill`` and it is the only thing standing between a caller and a
    silent empty result, so it is asserted where it lives rather than through a command
    that no longer exists.
    """
    from crocodile.core.sink.memory import MemorySink
    from crocodile.crypto.client.backfill import run_historical_backfill

    with pytest.raises(ValueError, match="not supported"):
        await run_historical_backfill(
            exchange="binance",
            channel="funding",
            symbols=["BTCUSDT"],
            start_ns=_START_NS,
            end_ns=_END_NS,
            sink=MemorySink(),
        )
