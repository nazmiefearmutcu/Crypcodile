"""OKX order-book continuity + REST snapshot parsing for gap resync.

WS ``books`` / ``books50-l2-tbt`` / ``books-l2-tbt`` push ``seqId`` +
``prevSeqId`` (docs: use them for continuity; checksum is deprecated).
REST ``GET /api/v5/market/books`` returns the same ``seqId`` field on the
instrument, so :class:`~crypcodile.ingest.gap_bridge.BookResyncBridge` can
re-anchor after a gap the same way Binance does with ``/depth``.

Continuity rules (OKX Sequence ID docs)
---------------------------------------
- After a snapshot at ``snap_seq``: drop ``seqId <= snap_seq``; first
  applied update satisfies ``prevSeqId <= snap_seq < seqId`` (or
  ``prevSeqId is None`` from wire ``prevSeqId=-1``).
- Thereafter: ``prevSeqId`` must equal the last applied ``seqId``.
- Heartbeat (no depth change): ``prevSeqId == seqId == last`` → APPLY.
- Maintenance reset: ``prevSeqId == last`` and ``seqId < prevSeqId`` →
  APPLY and re-anchor (documented OKX exception, not a gap).
- Any other break → RESYNC (REST re-fetch + buffer replay).

Post-snapshot buffer filter reuses the shared ``spot`` boundary
(``seq_id > snap_seq``), matching OKX's "discard stream seqId <=
snapshot seqId" rule.
"""

from __future__ import annotations

from typing import Any

from crypcodile.ingest.book_sync import SyncResult
from crypcodile.instruments.registry import InstrumentRegistry
from crypcodile.schema.records import BookSnapshot
from crypcodile.util.time import ms_to_ns, now_ns

from .normalize import _levels

__all__ = [
    "OkxOrderBookSync",
    "SyncResult",
    "parse_rest_books_snapshot",
]


class OkxOrderBookSync:
    """State machine for OKX ``seqId`` / ``prevSeqId`` book continuity.

    Satisfies :class:`~crypcodile.ingest.book_sync.BookSyncMachine` so it can
    drive :class:`~crypcodile.ingest.gap_bridge.BookResyncBridge`.  ``_venue``
    is always ``"spot"`` for the shared exclusive boundary filter (OKX has
    one continuity rule for all products).
    """

    def __init__(self) -> None:
        self._venue = "spot"
        self._snapshot_id: int | None = None
        self._prev_seq: int | None = None
        self._have_first: bool = False

    def set_snapshot(self, last_update_id: int) -> None:
        """Anchor continuity to a REST or WS snapshot ``seqId``."""
        self._snapshot_id = last_update_id
        self._prev_seq = None
        self._have_first = False

    def note_applied(self, u: int) -> None:
        """Record that a delta ending at *u* was applied after resync replay."""
        self._have_first = True
        self._prev_seq = u

    def feed(self, seq_id: int | None, prev_seq_id: int | None) -> SyncResult:
        """Process one books update and return APPLY / DROP / RESYNC.

        Parameters
        ----------
        seq_id:
            Wire ``seqId`` of this message.
        prev_seq_id:
            Wire ``prevSeqId`` (``None`` when the wire value is ``-1`` or
            missing — typical of the first snapshot message).
        """
        if self._snapshot_id is None:
            return SyncResult.DROP
        if seq_id is None:
            return SyncResult.DROP

        sid = self._snapshot_id

        if not self._have_first:
            # Stale relative to snapshot.
            if seq_id <= sid:
                return SyncResult.DROP
            # First valid: covers the snapshot boundary.
            # prevSeqId <= snap_seq < seqId  (or prev unknown from -1).
            if prev_seq_id is None or prev_seq_id <= sid:
                self._have_first = True
                self._prev_seq = seq_id
                return SyncResult.APPLY
            # prev is already past the snapshot without linking to it → gap.
            return SyncResult.RESYNC

        if self._prev_seq is None:
            raise RuntimeError(
                "invariant violated: _prev_seq is None with _have_first=True"
            )

        # Heartbeat: no depth change, same seq repeated.
        if prev_seq_id == self._prev_seq and seq_id == self._prev_seq:
            return SyncResult.APPLY

        # Maintenance sequence reset (documented OKX exception).
        if (
            prev_seq_id is not None
            and prev_seq_id == self._prev_seq
            and seq_id < prev_seq_id
        ):
            self._prev_seq = seq_id
            return SyncResult.APPLY

        # Normal continuity: prevSeqId chains from last applied seqId.
        if prev_seq_id == self._prev_seq:
            self._prev_seq = seq_id
            return SyncResult.APPLY

        return SyncResult.RESYNC


def parse_rest_books_snapshot(
    data: dict[str, Any],
    *,
    symbol_raw: str,
    venue: str = "okx",
    local_ts: int | None = None,
    registry: InstrumentRegistry | None = None,
) -> BookSnapshot:
    """Parse OKX REST ``GET /api/v5/market/books`` into a :class:`BookSnapshot`.

    Accepts either the full envelope ``{"code":"0","data":[{...}]}`` or a
    single book entry dict with ``bids`` / ``asks`` / ``seqId`` / ``ts``.
    """
    entry: dict[str, Any]
    if isinstance(data.get("data"), list) and data["data"]:
        entry = data["data"][0]
    else:
        entry = data

    inst = registry.get_raw(venue, symbol_raw) if registry is not None else None
    canonical = inst.canonical if inst is not None else f"{venue}:{symbol_raw}"

    bids = _levels(entry.get("bids", []))
    asks = _levels(entry.get("asks", []))

    ts_raw = entry.get("ts")
    exchange_ts = ms_to_ns(int(ts_raw)) if ts_raw is not None else None
    ts = local_ts if local_ts is not None else now_ns()

    seq_raw = entry.get("seqId")
    sequence_id: int | None
    if seq_raw is None:
        sequence_id = None
    else:
        sequence_id = int(seq_raw)

    return BookSnapshot(
        exchange=venue,
        symbol=canonical,
        symbol_raw=symbol_raw,
        exchange_ts=exchange_ts,
        local_ts=ts,
        bids=bids,
        asks=asks,
        depth=len(bids) + len(asks),
        sequence_id=sequence_id,
        is_snapshot=True,
    )
