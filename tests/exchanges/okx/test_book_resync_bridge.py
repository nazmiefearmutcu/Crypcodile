"""OKXConnector ↔ BookResyncBridge integration (seqId gap resync).

Covers:
- REST books snapshot parsing
- Bootstrap from WS action=snapshot (no REST)
- Bootstrap from REST when first push is an update
- Happy-path APPLY through the bridge
- RESYNC on prevSeqId discontinuity: REST re-fetch + buffered deltas
- Continuity after resync (note_applied)
- Heartbeat and sequence-reset APPLY paths
- Non-book channels still use the default normalize path (no REST)
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from crypcodile.exchanges.okx.book import (
    OkxOrderBookSync,
    SyncResult,
    parse_rest_books_snapshot,
)
from crypcodile.exchanges.okx.connector import OKXConnector
from crypcodile.ingest.gap_bridge import BookResyncBridge
from crypcodile.ingest.transport import FakeTransport
from crypcodile.instruments.registry import InstrumentRegistry
from crypcodile.schema.records import BookDelta, BookSnapshot, Trade
from crypcodile.sink.memory import MemorySink


def _rest_books(seq_id: int) -> dict[str, Any]:
    return {
        "code": "0",
        "msg": "",
        "data": [
            {
                "asks": [["50001.0", "1.5", "0", "1"]],
                "bids": [["50000.0", "1.0", "0", "1"], ["49999.0", "2.0", "0", "1"]],
                "ts": "1700000000000",
                "seqId": seq_id,
            }
        ],
    }


def _books_ws(
    *,
    action: str,
    seq_id: int,
    prev_seq_id: int,
    symbol: str = "BTC-USDT",
    bids: list[list[str]] | None = None,
    asks: list[list[str]] | None = None,
) -> bytes:
    entry: dict[str, Any] = {
        "bids": bids if bids is not None else [["50000.0", "1.0", "0", "1"]],
        "asks": asks if asks is not None else [["50001.0", "1.0", "0", "1"]],
        "ts": "1700000000100",
        "checksum": 0,
        "prevSeqId": prev_seq_id,
        "seqId": seq_id,
    }
    msg = {
        "arg": {"channel": "books", "instId": symbol},
        "action": action,
        "data": [entry],
    }
    return json.dumps(msg).encode()


def _trade_ws(symbol: str = "BTC-USDT") -> bytes:
    msg = {
        "arg": {"channel": "trades", "instId": symbol},
        "data": [
            {
                "instId": symbol,
                "tradeId": "1",
                "px": "50000.0",
                "sz": "0.01",
                "side": "buy",
                "ts": "1700000000100",
            }
        ],
    }
    return json.dumps(msg).encode()


def _make_connector(
    *,
    channels: list[str] | None = None,
    rest_seq: int = 100,
    rest_seqs: list[int] | None = None,
) -> tuple[OKXConnector, MemorySink, list[int]]:
    """Build a connector with mocked fetch_book_snapshot (no network)."""
    sink = MemorySink()
    conn = OKXConnector(
        symbols=["BTC-USDT"],
        channels=channels or ["book_delta"],
        out=sink,
        registry=InstrumentRegistry(),
    )
    calls: list[int] = []
    seqs = list(rest_seqs) if rest_seqs is not None else [rest_seq]
    seq_iter = iter(seqs)

    async def _fake_fetch(symbol: str) -> BookSnapshot:
        try:
            sid = next(seq_iter)
        except StopIteration:
            sid = seqs[-1] + 1000 * (len(calls) + 1)
        calls.append(sid)
        return parse_rest_books_snapshot(
            _rest_books(sid),
            symbol_raw=symbol,
            venue=conn.name,
            local_ts=0,
            registry=conn.registry,
        )

    conn.fetch_book_snapshot = _fake_fetch  # type: ignore[method-assign]
    return conn, sink, calls


# ---------------------------------------------------------------------------
# parse_rest_books_snapshot
# ---------------------------------------------------------------------------


def test_parse_rest_books_snapshot() -> None:
    snap = parse_rest_books_snapshot(
        _rest_books(1027024),
        symbol_raw="BTC-USDT",
        venue="okx",
        local_ts=42,
    )
    assert isinstance(snap, BookSnapshot)
    assert snap.sequence_id == 1027024
    assert snap.symbol_raw == "BTC-USDT"
    assert snap.is_snapshot is True
    assert (50000.0, 1.0) in snap.bids
    assert snap.depth == 3
    assert snap.local_ts == 42
    assert snap.exchange_ts == 1700000000000 * 1_000_000


def test_parse_rest_books_snapshot_entry_only() -> None:
    """Accept a bare entry dict (no envelope)."""
    entry = _rest_books(50)["data"][0]
    snap = parse_rest_books_snapshot(entry, symbol_raw="ETH-USDT", local_ts=1)
    assert snap.sequence_id == 50
    assert snap.symbol_raw == "ETH-USDT"


# ---------------------------------------------------------------------------
# OkxOrderBookSync unit
# ---------------------------------------------------------------------------


def test_okx_sync_first_event_and_continuity() -> None:
    s = OkxOrderBookSync()
    s.set_snapshot(last_update_id=100)
    assert s.feed(seq_id=90, prev_seq_id=80) == SyncResult.DROP  # stale
    assert s.feed(seq_id=101, prev_seq_id=100) == SyncResult.APPLY
    assert s.feed(seq_id=105, prev_seq_id=101) == SyncResult.APPLY
    assert s.feed(seq_id=200, prev_seq_id=150) == SyncResult.RESYNC  # gap


def test_okx_sync_heartbeat_and_reset() -> None:
    s = OkxOrderBookSync()
    s.set_snapshot(last_update_id=10)
    assert s.feed(seq_id=15, prev_seq_id=10) == SyncResult.APPLY
    # Heartbeat: same seq repeated
    assert s.feed(seq_id=15, prev_seq_id=15) == SyncResult.APPLY
    # Maintenance reset: seqId < prevSeqId but prev chains
    assert s.feed(seq_id=3, prev_seq_id=15) == SyncResult.APPLY
    assert s.feed(seq_id=5, prev_seq_id=3) == SyncResult.APPLY


def test_note_applied_enables_continuity_after_resync() -> None:
    s = OkxOrderBookSync()
    s.set_snapshot(last_update_id=200)
    s.note_applied(250)
    assert s.feed(seq_id=260, prev_seq_id=250) == SyncResult.APPLY
    assert s.feed(seq_id=300, prev_seq_id=999) == SyncResult.RESYNC


# ---------------------------------------------------------------------------
# Connector integration via FakeTransport
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bootstrap_from_ws_snapshot_then_applies_delta() -> None:
    """WS action=snapshot seeds sync (no REST); following update APPLYs."""
    conn, sink, calls = _make_connector(rest_seq=999)
    frames = [
        _books_ws(action="snapshot", seq_id=100, prev_seq_id=-1),
        _books_ws(action="update", seq_id=101, prev_seq_id=100),
    ]
    conn.transport = FakeTransport(frames=frames)
    await conn.run(max_reconnects=0)

    assert calls == []  # WS bootstrap — REST not used
    assert "BTC-USDT" in conn._book_bridges
    assert isinstance(sink.records[0], BookSnapshot)
    assert sink.records[0].sequence_id == 100
    assert isinstance(sink.records[1], BookDelta)
    assert sink.records[1].seq_id == 101


@pytest.mark.asyncio
async def test_bootstrap_from_rest_when_first_is_update() -> None:
    """First books push is update → REST bootstrap then APPLY."""
    conn, sink, calls = _make_connector(rest_seq=100)
    frames = [
        _books_ws(action="update", seq_id=101, prev_seq_id=100),
    ]
    conn.transport = FakeTransport(frames=frames)
    await conn.run(max_reconnects=0)

    assert len(calls) == 1
    assert isinstance(sink.records[0], BookSnapshot)
    assert sink.records[0].sequence_id == 100
    assert isinstance(sink.records[1], BookDelta)
    assert sink.records[1].seq_id == 101


@pytest.mark.asyncio
async def test_bootstrap_retries_after_failed_fetch() -> None:
    """First REST bootstrap fails; later update still bootstraps and APPLYs."""
    conn, sink, _ = _make_connector(rest_seq=100)
    attempts = 0

    async def _flaky_fetch(symbol: str) -> BookSnapshot:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("simulated REST books failure")
        return parse_rest_books_snapshot(
            _rest_books(100),
            symbol_raw=symbol,
            venue=conn.name,
            local_ts=0,
            registry=conn.registry,
        )

    conn.fetch_book_snapshot = _flaky_fetch  # type: ignore[method-assign]
    frames = [
        _books_ws(action="update", seq_id=101, prev_seq_id=100),
        _books_ws(action="update", seq_id=101, prev_seq_id=100),
    ]
    conn.transport = FakeTransport(frames=frames)
    await conn.run(max_reconnects=0)

    assert attempts == 2
    assert "BTC-USDT" in conn._book_bridges
    assert isinstance(sink.records[0], BookSnapshot)
    assert sink.records[0].sequence_id == 100
    assert isinstance(sink.records[1], BookDelta)
    assert sink.records[1].seq_id == 101


@pytest.mark.asyncio
async def test_sequence_gap_triggers_resync_bridge() -> None:
    """Gap in prevSeqId continuity → BookResyncBridge complete_resync path."""
    # Bootstrap is WS (seq 100); first REST call is the resync → 200.
    conn, sink, calls = _make_connector(rest_seqs=[200])
    frames = [
        _books_ws(action="snapshot", seq_id=100, prev_seq_id=-1),
        _books_ws(action="update", seq_id=105, prev_seq_id=100),
        _books_ws(action="update", seq_id=110, prev_seq_id=105),
        # GAP: expected prev=110, got 150 → RESYNC; REST returns 200
        _books_ws(action="update", seq_id=210, prev_seq_id=150),
        # Continues from last kept buffered seq (210) after note_applied
        _books_ws(action="update", seq_id=215, prev_seq_id=210),
    ]
    conn.transport = FakeTransport(frames=frames)
    await conn.run(max_reconnects=0)

    assert len(calls) >= 1  # REST resync (bootstrap was WS)
    snapshots = [r for r in sink.records if isinstance(r, BookSnapshot)]
    deltas = [r for r in sink.records if isinstance(r, BookDelta)]

    assert len(snapshots) >= 2
    assert any(s.sequence_id == 200 for s in snapshots)
    assert any(d.seq_id == 105 for d in deltas)
    assert any(d.seq_id == 110 for d in deltas)
    # Post-resync: spot keeps seq_id > snap_seq (200), so 210 is kept.
    assert any(d.seq_id == 210 for d in deltas)
    assert any(d.seq_id == 215 for d in deltas)


@pytest.mark.asyncio
async def test_trade_channel_skips_book_bridge() -> None:
    """Without book channels, trades use default path (no REST fetch)."""
    conn, sink, calls = _make_connector(channels=["trade"], rest_seq=100)
    frames = [_trade_ws()]
    conn.transport = FakeTransport(frames=frames)
    await conn.run(max_reconnects=0)

    assert calls == []
    assert any(isinstance(r, Trade) for r in sink.records)


@pytest.mark.asyncio
async def test_stale_delta_dropped_not_emitted() -> None:
    """Deltas with seqId <= snapshot seq are DROP'd."""
    conn, sink, _ = _make_connector(rest_seq=100)
    frames = [
        _books_ws(action="snapshot", seq_id=100, prev_seq_id=-1),
        _books_ws(action="update", seq_id=100, prev_seq_id=99),  # stale
        _books_ws(action="update", seq_id=105, prev_seq_id=100),  # first valid
    ]
    conn.transport = FakeTransport(frames=frames)
    await conn.run(max_reconnects=0)

    deltas = [r for r in sink.records if isinstance(r, BookDelta)]
    assert len(deltas) == 1
    assert deltas[0].seq_id == 105


@pytest.mark.asyncio
async def test_midstream_ws_snapshot_reanchors() -> None:
    """A second WS snapshot re-seeds the sync without REST."""
    conn, sink, calls = _make_connector(rest_seq=999)
    frames = [
        _books_ws(action="snapshot", seq_id=100, prev_seq_id=-1),
        _books_ws(action="update", seq_id=105, prev_seq_id=100),
        _books_ws(action="snapshot", seq_id=200, prev_seq_id=-1),
        _books_ws(action="update", seq_id=201, prev_seq_id=200),
    ]
    conn.transport = FakeTransport(frames=frames)
    await conn.run(max_reconnects=0)

    assert calls == []
    snaps = [r for r in sink.records if isinstance(r, BookSnapshot)]
    assert any(s.sequence_id == 100 for s in snaps)
    assert any(s.sequence_id == 200 for s in snaps)
    deltas = [r for r in sink.records if isinstance(r, BookDelta)]
    assert any(d.seq_id == 105 for d in deltas)
    assert any(d.seq_id == 201 for d in deltas)


# ---------------------------------------------------------------------------
# Bridge unit: complete_resync advances continuity via note_applied
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_resync_note_applied_continuity() -> None:
    """After complete_resync, live deltas continue from last kept buffered seq."""
    sync = OkxOrderBookSync()
    sync.set_snapshot(last_update_id=100)

    async def fetch(symbol: str) -> BookSnapshot:
        return parse_rest_books_snapshot(
            _rest_books(200),
            symbol_raw=symbol,
            venue="okx",
            local_ts=0,
        )

    bridge = BookResyncBridge(sync=sync, fetch_snapshot=fetch, symbol="BTC-USDT")

    gap = BookDelta(
        exchange="okx",
        symbol="okx:BTC-USDT",
        symbol_raw="BTC-USDT",
        exchange_ts=None,
        local_ts=0,
        bids=[(50000.0, 1.0)],
        asks=[],
        seq_id=250,
        prev_seq_id=150,
        is_snapshot=False,
    )
    assert bridge.feed_sync_result(SyncResult.RESYNC, gap) is None
    applied = await bridge.complete_resync()
    assert isinstance(applied[0], BookSnapshot)
    assert any(isinstance(r, BookDelta) and r.seq_id == 250 for r in applied)

    assert sync.feed(seq_id=260, prev_seq_id=250) == SyncResult.APPLY
