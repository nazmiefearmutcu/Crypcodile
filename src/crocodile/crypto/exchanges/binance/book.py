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

Those rules are not Binance-specific — the equity providers speak the same
``U``/``u``/``pu`` dialect — so ``OrderBookSync`` itself now lives in
:mod:`crocodile.core.ingest.book_sync` and is re-exported here for the
connector and its tests.  Only the wire-format parsing below is Binance's.
"""

from collections.abc import Iterable
from typing import Any

from crocodile.core.ingest.book_sync import OrderBookSync, SyncResult
from crocodile.core.schema.legacy.records import BookDelta, BookSnapshot, Record
from crocodile.core.util.time import ms_to_ns, now_ns
from crocodile.crypto.instruments.registry import InstrumentRegistry

# Re-export for callers that import OrderBookSync / SyncResult from this module.
__all__ = [
    "OrderBookSync",
    "SyncResult",
    "normalize_depth",
    "parse_rest_depth_snapshot",
]


def _levels(raw: list[list[Any]]) -> list[tuple[float, float]]:
    """Convert Binance [price_str, qty_str] pairs to canonical (price, amount) tuples.

    qty=0 means remove the level (canonical removal signal).
    """
    return [(float(px), float(qty)) for px, qty in raw]


def parse_rest_depth_snapshot(
    data: dict[str, Any],
    *,
    symbol_raw: str,
    venue: str,
    local_ts: int | None = None,
    registry: InstrumentRegistry | None = None,
) -> BookSnapshot:
    """Parse a Binance REST depth response into a :class:`BookSnapshot`.

    Spot: ``GET /api/v3/depth`` → ``lastUpdateId``, ``bids``, ``asks``.
    Futures: ``GET /fapi/v1/depth`` (or dapi) — same shape (optional ``E``/``T``).
    """
    inst = registry.get_raw(venue, symbol_raw) if registry is not None else None
    canonical = inst.canonical if inst is not None else f"{venue}:{symbol_raw}"

    # Spot/futures REST depth use lastUpdateId; tolerate rare `u` aliases.
    last_update_id = data.get("lastUpdateId", data.get("u"))

    bids = _levels(data.get("bids", []))
    asks = _levels(data.get("asks", []))

    e_ts = data.get("E")
    exchange_ts = ms_to_ns(e_ts) if e_ts is not None else None
    ts = local_ts if local_ts is not None else now_ns()

    return BookSnapshot(
        exchange=venue,
        symbol=canonical,
        symbol_raw=symbol_raw,
        exchange_ts=exchange_ts,
        local_ts=ts,
        bids=bids,
        asks=asks,
        depth=len(bids) + len(asks),
        sequence_id=int(last_update_id) if last_update_id is not None else None,
        is_snapshot=True,
    )


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
