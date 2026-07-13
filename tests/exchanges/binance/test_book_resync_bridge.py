"""BinanceConnector ↔ BookResyncBridge integration (sequence-gap resync).

Covers:
- REST depth snapshot parsing
- Bootstrap snapshot on first depth event
- Happy-path APPLY through the bridge
- RESYNC on sequence gap: REST re-fetch + buffered deltas applied
- Continuity after resync (note_applied)
- Non-book channels still use the default normalize path (no REST)
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from crypcodile.exchanges.binance.book import (
    OrderBookSync,
    SyncResult,
    parse_rest_depth_snapshot,
)
from crypcodile.exchanges.binance.connector import BinanceConnector
from crypcodile.ingest.gap_bridge import BookResyncBridge
from crypcodile.ingest.transport import FakeTransport
from crypcodile.instruments.registry import InstrumentRegistry
from crypcodile.schema.records import BookDelta, BookSnapshot, Trade
from crypcodile.sink.memory import MemorySink


def _rest_depth(last_update_id: int) -> dict[str, Any]:
    return {
        "lastUpdateId": last_update_id,
        "bids": [["50000.00", "1.0"], ["49999.00", "2.0"]],
        "asks": [["50001.00", "1.5"]],
    }


def _depth_ws(
    *,
    U: int,
    u: int,
    pu: int | None = None,
    symbol: str = "BTCUSDT",
) -> bytes:
    data: dict[str, Any] = {
        "e": "depthUpdate",
        "E": 1700000000200,
        "s": symbol,
        "U": U,
        "u": u,
        "b": [["50000.00", "1.5"]],
        "a": [["50001.00", "2.0"]],
    }
    if pu is not None:
        data["pu"] = pu
    msg = {"stream": f"{symbol.lower()}@depth", "data": data}
    return json.dumps(msg).encode()


def _trade_ws(symbol: str = "BTCUSDT") -> bytes:
    msg = {
        "stream": f"{symbol.lower()}@aggTrade",
        "data": {
            "e": "aggTrade",
            "E": 1700000000100,
            "s": symbol,
            "a": 12345,
            "p": "50000.00",
            "q": "0.01",
            "f": 100,
            "l": 105,
            "T": 1700000000100,
            "m": True,
        },
    }
    return json.dumps(msg).encode()


def _make_connector(
    *,
    channels: list[str] | None = None,
    market: str = "spot",
    rest_seq: int = 100,
    rest_seqs: list[int] | None = None,
) -> tuple[BinanceConnector, MemorySink, list[int]]:
    """Build a connector with mocked fetch_book_snapshot (no network)."""
    sink = MemorySink()
    conn = BinanceConnector(
        symbols=["BTCUSDT"],
        channels=channels or ["book_delta"],
        out=sink,
        registry=InstrumentRegistry(),
        market=market,
    )
    calls: list[int] = []
    seqs = list(rest_seqs) if rest_seqs is not None else [rest_seq]
    # Always have a final fallback seq so extra resyncs don't IndexError.
    seq_iter = iter(seqs)

    async def _fake_fetch(symbol: str) -> BookSnapshot:
        try:
            sid = next(seq_iter)
        except StopIteration:
            sid = seqs[-1] + 1000 * (len(calls) + 1)
        calls.append(sid)
        return parse_rest_depth_snapshot(
            _rest_depth(sid),
            symbol_raw=symbol,
            venue=conn._venue,
            local_ts=0,
            registry=conn.registry,
        )

    conn.fetch_book_snapshot = _fake_fetch  # type: ignore[method-assign]
    return conn, sink, calls


# ---------------------------------------------------------------------------
# parse_rest_depth_snapshot
# ---------------------------------------------------------------------------


def test_parse_rest_depth_snapshot() -> None:
    snap = parse_rest_depth_snapshot(
        _rest_depth(1027024),
        symbol_raw="BTCUSDT",
        venue="binance-spot",
        local_ts=42,
    )
    assert isinstance(snap, BookSnapshot)
    assert snap.sequence_id == 1027024
    assert snap.symbol_raw == "BTCUSDT"
    assert snap.is_snapshot is True
    assert (50000.0, 1.0) in snap.bids
    assert snap.depth == 3
    assert snap.local_ts == 42


# ---------------------------------------------------------------------------
# OrderBookSync.note_applied
# ---------------------------------------------------------------------------


def test_note_applied_enables_continuity_after_resync() -> None:
    s = OrderBookSync(venue="spot")
    s.set_snapshot(last_update_id=200)
    # Simulate buffered deltas applied after REST resync (last u=250)
    s.note_applied(250)
    assert s.feed(U=251, u=260, pu=None) == SyncResult.APPLY
    assert s.feed(U=300, u=310, pu=None) == SyncResult.RESYNC


# ---------------------------------------------------------------------------
# Connector integration via FakeTransport
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bootstrap_emits_snapshot_then_applies_delta() -> None:
    """First depth: REST bootstrap snapshot + first valid delta APPLY."""
    conn, sink, calls = _make_connector(rest_seq=100)
    # First valid spot event against lastUpdateId=100: U<=101 and u>=101
    frames = [_depth_ws(U=101, u=105)]
    conn.transport = FakeTransport(frames=frames)
    await conn.run(max_reconnects=0)

    assert len(calls) == 1  # bootstrap only
    assert isinstance(sink.records[0], BookSnapshot)
    assert sink.records[0].sequence_id == 100
    assert isinstance(sink.records[1], BookDelta)
    assert sink.records[1].seq_id == 105


@pytest.mark.asyncio
async def test_sequence_gap_triggers_resync_bridge() -> None:
    """Gap in U continuity → BookResyncBridge complete_resync path."""
    # bootstrap seq=100, resync seq=200
    conn, sink, calls = _make_connector(rest_seqs=[100, 200])
    frames = [
        _depth_ws(U=101, u=105),  # APPLY after bootstrap
        _depth_ws(U=106, u=110),  # APPLY continuity
        _depth_ws(U=200, u=210),  # GAP → RESYNC (expected U=111)
        _depth_ws(U=201, u=205),  # arrives during/after resync window
    ]
    conn.transport = FakeTransport(frames=frames)
    await conn.run(max_reconnects=0)

    assert len(calls) >= 2  # bootstrap + resync
    snapshots = [r for r in sink.records if isinstance(r, BookSnapshot)]
    deltas = [r for r in sink.records if isinstance(r, BookDelta)]

    assert len(snapshots) >= 2
    # Second snapshot is the resync REST fetch at seq=200
    assert any(s.sequence_id == 200 for s in snapshots)
    # Pre-gap deltas applied
    assert any(d.seq_id == 105 for d in deltas)
    assert any(d.seq_id == 110 for d in deltas)
    # Post-resync: delta with u=210 was the RESYNC trigger (buffered);
    # spot keeps seq_id > snap_seq (200), so 210 is kept; 205 also if > 200.
    assert any(d.seq_id == 210 for d in deltas)


@pytest.mark.asyncio
async def test_trade_channel_skips_book_bridge() -> None:
    """Without book channels, depth is not special-cased (no REST fetch)."""
    conn, sink, calls = _make_connector(channels=["trade"], rest_seq=100)
    frames = [_trade_ws()]
    conn.transport = FakeTransport(frames=frames)
    await conn.run(max_reconnects=0)

    assert calls == []  # fetch never invoked
    assert any(isinstance(r, Trade) for r in sink.records)


@pytest.mark.asyncio
async def test_stale_delta_dropped_not_emitted() -> None:
    """Deltas with u <= lastUpdateId are DROP'd and never reach the sink."""
    conn, sink, _ = _make_connector(rest_seq=100)
    frames = [
        _depth_ws(U=90, u=100),  # stale for spot (u <= 100)
        _depth_ws(U=101, u=105),  # first valid
    ]
    conn.transport = FakeTransport(frames=frames)
    await conn.run(max_reconnects=0)

    deltas = [r for r in sink.records if isinstance(r, BookDelta)]
    assert len(deltas) == 1
    assert deltas[0].seq_id == 105


@pytest.mark.asyncio
async def test_futures_resync_uses_pu_continuity() -> None:
    """USD-M market: OrderBookSync venue=futures; gap on bad pu triggers resync."""
    conn, sink, calls = _make_connector(
        market="usdm",
        rest_seqs=[100, 200],
    )
    frames = [
        _depth_ws(U=95, u=100, pu=90),  # first valid futures
        _depth_ws(U=101, u=110, pu=100),  # continuity
        _depth_ws(U=111, u=120, pu=999),  # bad pu → RESYNC
    ]
    conn.transport = FakeTransport(frames=frames)
    await conn.run(max_reconnects=0)

    assert len(calls) >= 2
    assert any(
        isinstance(r, BookSnapshot) and r.sequence_id == 200 for r in sink.records
    )


# ---------------------------------------------------------------------------
# Bridge unit: complete_resync advances continuity via note_applied
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_resync_note_applied_continuity() -> None:
    """After complete_resync, live deltas continue from last kept buffered seq."""
    sync = OrderBookSync(venue="spot")
    sync.set_snapshot(last_update_id=100)

    async def fetch(symbol: str) -> BookSnapshot:
        return parse_rest_depth_snapshot(
            _rest_depth(200),
            symbol_raw=symbol,
            venue="binance-spot",
            local_ts=0,
        )

    bridge = BookResyncBridge(sync=sync, fetch_snapshot=fetch, symbol="BTCUSDT")

    # Trigger RESYNC and buffer a post-snapshot delta
    gap = BookDelta(
        exchange="binance-spot",
        symbol="binance-spot:BTCUSDT",
        symbol_raw="BTCUSDT",
        exchange_ts=None,
        local_ts=0,
        bids=[(50000.0, 1.0)],
        asks=[],
        seq_id=250,
        prev_seq_id=None,
        is_snapshot=False,
    )
    assert bridge.feed_sync_result(SyncResult.RESYNC, gap) is None
    applied = await bridge.complete_resync()
    assert isinstance(applied[0], BookSnapshot)
    assert any(isinstance(r, BookDelta) and r.seq_id == 250 for r in applied)

    # Next live event must use continuity from 250, not first-event vs 200
    assert sync.feed(U=251, u=260, pu=None) == SyncResult.APPLY

