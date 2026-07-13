"""Shared order-book sync primitives for multi-venue gap recovery.

``SyncResult`` and the buffer-filter helper are used by
:class:`~crypcodile.ingest.gap_bridge.BookResyncBridge`.  Venue-specific
state machines (e.g. Binance ``OrderBookSync``) implement
:class:`BookSyncMachine` and live under ``exchanges/<venue>/book.py``.

Bybit is intentionally not wired yet — see the deferral note on
``BybitConnector`` (REST ``u`` only aligns with ``orderbook.1000``, while
the connector uses ``orderbook.50``; recovery is re-snapshot, not
Binance-style REST-anchored delta replay).
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum
from typing import Protocol, runtime_checkable

from crypcodile.schema.records import BookDelta


class SyncResult(StrEnum):
    """Action returned by a venue order-book continuity state machine."""

    DROP = "drop"
    APPLY = "apply"
    RESYNC = "resync"


@runtime_checkable
class BookSyncMachine(Protocol):
    """Minimal interface required by :class:`~crypcodile.ingest.gap_bridge.BookResyncBridge`.

    Implementations must expose ``_venue`` for post-snapshot buffer filtering
    (``"spot"`` / ``"futures"`` today; future venues map to one of those
    boundary rules or extend :func:`filter_buffered_book_deltas`).
    """

    _venue: str

    def set_snapshot(self, last_update_id: int) -> None:
        """Anchor continuity to a REST (or WS) snapshot update id."""
        ...

    def note_applied(self, u: int) -> None:
        """Record that a delta ending at update id *u* was applied after resync."""
        ...


def keep_delta_after_snapshot(
    delta: BookDelta,
    snap_seq: int | None,
    *,
    venue: str = "spot",
) -> bool:
    """Return True if *delta* should be applied after a snapshot at *snap_seq*.

    Venue rules (Binance today):
      - ``futures``: keep ``seq_id >= snap_seq`` (boundary inclusive)
      - all others (``spot``, and any future venue reusing spot rules):
        keep ``seq_id > snap_seq`` (boundary exclusive)

    When *snap_seq* or the delta's ``seq_id`` is ``None``, keep (best-effort
    path when the REST snapshot has no usable anchor).
    """
    if snap_seq is None or delta.seq_id is None:
        return True
    if venue == "futures":
        return delta.seq_id >= snap_seq
    return delta.seq_id > snap_seq


def filter_buffered_book_deltas(
    buffer: Sequence[BookDelta],
    snap_seq: int | None,
    *,
    venue: str = "spot",
) -> list[BookDelta]:
    """Keep buffered deltas that apply after a snapshot sequence anchor.

    See :func:`keep_delta_after_snapshot` for venue boundary rules.
    """
    return [
        delta
        for delta in buffer
        if keep_delta_after_snapshot(delta, snap_seq, venue=venue)
    ]
