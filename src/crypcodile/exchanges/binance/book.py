"""Binance order-book depth diff normalization + sync state machine.

Spot vs futures rules (appendix §3.2 + §8):
- Both venues: diff event has {U, u, b, a}; futures also has {pu}.
  qty=0 means remove the level.
- Map to BookDelta: seq_id=u; spot prev_seq_id=None; futures prev_seq_id=pu.

OrderBookSync state machine:
  Spot:
    - Drop buffered events where u <= lastUpdateId.
    - First applied event: U <= lastUpdateId+1 AND u >= lastUpdateId+1.
    - Thereafter: U == prev_u + 1.
    - Continuity break -> RESYNC.
  Futures:
    - Drop buffered events where u < lastUpdateId.
    - First applied event: U <= lastUpdateId AND u >= lastUpdateId.
    - Thereafter: pu == prev_u.
    - Continuity break -> RESYNC.
"""

from collections.abc import Iterable
from enum import StrEnum
from typing import Any, Literal

from crypcodile.instruments.registry import InstrumentRegistry
from crypcodile.schema.records import BookDelta, Record
from crypcodile.util.time import ms_to_ns


class SyncResult(StrEnum):
    DROP = "drop"
    APPLY = "apply"
    RESYNC = "resync"


class OrderBookSync:
    """State machine for synchronising a Binance depth stream with a REST snapshot."""

    def __init__(self, venue: Literal["spot", "futures"]) -> None:
        """Initialise for 'spot' or 'futures' venue."""
        if venue not in ("spot", "futures"):
            raise ValueError(f"venue must be 'spot' or 'futures', got {venue!r}")
        self._venue = venue  # "spot" or "futures"
        self._snapshot_id: int | None = None
        self._prev_u: int | None = None
        self._have_first: bool = False

    def set_snapshot(self, last_update_id: int) -> None:
        """Called once the REST snapshot has been fetched."""
        self._snapshot_id = last_update_id
        self._prev_u = None
        self._have_first = False

    def feed(self, U: int, u: int, pu: int | None) -> SyncResult:
        """Process one depth diff event and return the action to take.

        Parameters
        ----------
        U:  First update id in this event.
        u:  Final update id in this event.
        pu: Previous final update id (futures only; None for spot).
        """
        if self._snapshot_id is None:
            # No snapshot yet — buffer (treat as DROP until snapshot arrives)
            return SyncResult.DROP

        sid = self._snapshot_id

        if not self._have_first:
            if self._venue == "spot":
                # Drop stale events
                if u <= sid:
                    return SyncResult.DROP
                # First valid: U <= sid+1 AND u >= sid+1
                if U <= sid + 1 and u >= sid + 1:
                    self._have_first = True
                    self._prev_u = u
                    return SyncResult.APPLY
                # Otherwise gap before first event -> resync
                return SyncResult.RESYNC
            else:
                # futures
                # Drop stale events: u < lastUpdateId
                if u < sid:
                    return SyncResult.DROP
                # First valid: U <= lastUpdateId AND u >= lastUpdateId
                if U <= sid and u >= sid:
                    self._have_first = True
                    self._prev_u = u
                    return SyncResult.APPLY
                return SyncResult.RESYNC
        else:
            # Subsequent events — check continuity.
            # Invariant: both branches that set _have_first=True also set _prev_u=u,
            # so _prev_u can never be None here.  If somehow it were, we would crash
            # with a confusing TypeError on `self._prev_u + 1`; raise explicitly instead.
            if self._prev_u is None:
                raise RuntimeError(
                    "invariant violated: _prev_u is None with _have_first=True"
                )
            if self._venue == "spot":
                if U == self._prev_u + 1:
                    self._prev_u = u
                    return SyncResult.APPLY
                return SyncResult.RESYNC
            else:
                # futures: pu must equal prev_u
                if pu == self._prev_u:
                    self._prev_u = u
                    return SyncResult.APPLY
                return SyncResult.RESYNC


def _levels(raw: list[list[Any]]) -> list[tuple[float, float]]:
    """Convert Binance [price_str, qty_str] pairs to canonical (price, amount) tuples.

    qty=0 means remove the level (canonical removal signal).
    """
    return [(float(px), float(qty)) for px, qty in raw]


def normalize_depth(
    msg: dict[str, Any],
    local_ts: int,
    venue: str,
    registry: InstrumentRegistry | None = None,
) -> Iterable[Record]:
    """Normalize a Binance depth diff (depthUpdate) message to a BookDelta.

    Works for both spot (@depth) and futures (@depth / @depthUpdate streams).
    """
    data: dict[str, Any] = msg.get("data", msg)
    raw_symbol: str = data["s"]

    inst = registry.get_raw(venue, raw_symbol) if registry is not None else None
    canonical = inst.canonical if inst is not None else f"{venue}:{raw_symbol}"

    u: int = data["u"]
    pu: int | None = data.get("pu")  # futures only

    # exchange_ts: use event time E if present
    e_ts = data.get("E")
    exchange_ts = ms_to_ns(e_ts) if e_ts is not None else None

    bids = _levels(data.get("b", []))
    asks = _levels(data.get("a", []))

    yield BookDelta(
        exchange=venue,
        symbol=canonical,
        symbol_raw=raw_symbol,
        exchange_ts=exchange_ts,
        local_ts=local_ts,
        bids=bids,
        asks=asks,
        seq_id=u,
        prev_seq_id=pu,  # None for spot, int for futures
        is_snapshot=False,
    )
