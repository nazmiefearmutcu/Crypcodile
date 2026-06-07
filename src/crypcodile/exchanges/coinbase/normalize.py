"""Coinbase Advanced Trade WebSocket message normalization.

Appendix §7 critical notes:
- WS endpoint: ``wss://ws-feed.exchange.coinbase.com`` (JWT-authenticated for
  private channels; public channels use the same endpoint without auth).
- Subscribe format: ``{"type":"subscribe","product_ids":["BTC-USD"],"channels":["matches"]}``
- ``product_id`` is canonical (e.g. ``BTC-USD``) — it is the exchange-native symbol; no
  derivation from base/quote needed.  Cache ``/products`` to populate the registry.
- Spot only: no funding, open-interest, or liquidation channels.

Channel routing:
- ``matches`` (or ``last_match``)  → Trade
- ``snapshot``                     → BookSnapshot   (``type == "snapshot"``)
- ``l2update``                     → BookDelta      (``type == "l2update"``)
- ``ticker``                       → BookTicker

Timestamps:
- ``matches`` carries an ISO 8601 ``time`` field (``2023-11-14T22:13:20.000000Z``)
  → parse to ns UTC.
- ``snapshot`` has no ``time`` field → ``exchange_ts = None``.
- ``l2update`` carries ``time`` → parse to ns UTC.
- ``ticker`` carries ``time`` → parse to ns UTC.

Order-book levels:
- ``snapshot``: bids/asks are ``[price_str, size_str]`` pairs.
- ``l2update``:  ``changes`` is a list of ``[side_str, price_str, size_str]``
  triples. ``size_str == "0"`` or ``"0.0"`` → canonical ``amount=0.0``
  (level removal signal).

Side mapping (``matches``):
- ``"buy"``  → Side.BUY  (taker side)
- ``"sell"`` → Side.SELL
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from crypcodile.instruments.registry import InstrumentRegistry
from crypcodile.schema.enums import Side
from crypcodile.schema.records import (
    BookDelta,
    BookSnapshot,
    BookTicker,
    Record,
    Trade,
)

log = logging.getLogger(__name__)

EXCHANGE = "coinbase"

# Epoch anchor for integer-only ns conversion (avoids float64 rounding)
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _side(raw: str) -> Side:
    """Map Coinbase side string to canonical Side."""
    lower = raw.lower()
    if lower == "buy":
        return Side.BUY
    if lower == "sell":
        return Side.SELL
    return Side.UNKNOWN


def _parse_iso_ns(time_str: str) -> int | None:
    """Parse an ISO 8601 UTC timestamp string to nanoseconds.

    Coinbase uses the format ``2023-11-14T22:13:20.000000Z``.
    Returns None if parsing fails.

    Uses integer arithmetic (days/seconds/microseconds) to avoid the up-to-32ns
    rounding error introduced by ``int(dt.timestamp() * 1e9)`` on float64.
    """
    try:
        # Replace trailing 'Z' with '+00:00' for fromisoformat compatibility
        dt = datetime.fromisoformat(time_str.replace("Z", "+00:00"))
        # Integer-only arithmetic: avoids float64 rounding (up to ~32 ns error)
        td = dt - _EPOCH
        epoch_ns = (
            td.days * 86_400_000_000_000
            + td.seconds * 1_000_000_000
            + td.microseconds * 1_000
        )
        return epoch_ns
    except (ValueError, AttributeError):
        log.debug("coinbase: failed to parse timestamp %r", time_str)
        return None


def _canonical(raw_symbol: str, registry: InstrumentRegistry | None) -> str:
    """Resolve canonical symbol via registry or fall back to ``coinbase:{raw}``."""
    if registry is not None:
        inst = registry.get_raw(EXCHANGE, raw_symbol)
        if inst is not None:
            return inst.canonical
    return f"{EXCHANGE}:{raw_symbol}"


def _parse_snapshot_levels(rows: list[list[str]]) -> list[tuple[float, float]]:
    """Parse ``snapshot`` bid/ask rows ``[price_str, size_str]``."""
    return [(float(row[0]), float(row[1])) for row in rows]


def _parse_l2update_changes(
    changes: list[list[str]],
) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    """Parse ``l2update`` changes ``[[side_str, price_str, size_str], ...]``.

    Returns (bids, asks) level lists.
    ``size_str == "0"`` or ``"0.0"`` → canonical amount=0.0 (removal signal).
    """
    bids: list[tuple[float, float]] = []
    asks: list[tuple[float, float]] = []
    for change in changes:
        side_str, price_str, size_str = change[0], change[1], change[2]
        price = float(price_str)
        amount = float(size_str)  # 0.0 when "0" or "0.0" — canonical removal
        if side_str.lower() == "buy":
            bids.append((price, amount))
        else:
            asks.append((price, amount))
    return bids, asks


# ---------------------------------------------------------------------------
# Per-message-type normalizers
# ---------------------------------------------------------------------------


def _normalize_match(
    msg: dict[str, Any],
    local_ts: int,
    registry: InstrumentRegistry | None,
) -> Iterable[Record]:
    """``matches`` / ``last_match`` → Trade."""
    raw_symbol: str = msg["product_id"]
    canonical = _canonical(raw_symbol, registry)
    time_str = msg.get("time")
    exchange_ts = _parse_iso_ns(time_str) if time_str else None
    trade_id = msg.get("trade_id") or msg.get("sequence")
    yield Trade(
        exchange=EXCHANGE,
        symbol=canonical,
        symbol_raw=raw_symbol,
        exchange_ts=exchange_ts,
        local_ts=local_ts,
        id=str(trade_id),
        price=float(msg["price"]),
        amount=float(msg["size"]),
        side=_side(msg.get("side", "unknown")),
    )


def _normalize_snapshot(
    msg: dict[str, Any],
    local_ts: int,
    registry: InstrumentRegistry | None,
) -> Iterable[Record]:
    """``snapshot`` → BookSnapshot.

    Coinbase snapshots have no ``time`` field → ``exchange_ts = None``.
    """
    raw_symbol: str = msg["product_id"]
    canonical = _canonical(raw_symbol, registry)
    bids = _parse_snapshot_levels(msg.get("bids", []))
    asks = _parse_snapshot_levels(msg.get("asks", []))
    yield BookSnapshot(
        exchange=EXCHANGE,
        symbol=canonical,
        symbol_raw=raw_symbol,
        exchange_ts=None,  # snapshots have no timestamp on Coinbase
        local_ts=local_ts,
        bids=bids,
        asks=asks,
        depth=len(bids) + len(asks),
        sequence_id=None,
        is_snapshot=True,
    )


def _normalize_l2update(
    msg: dict[str, Any],
    local_ts: int,
    registry: InstrumentRegistry | None,
) -> Iterable[Record]:
    """``l2update`` → BookDelta.

    Level removal is signaled by ``size == "0"`` → canonical ``amount=0.0``.
    ``prev_seq_id`` is None (Coinbase level2 has no sequence field on deltas).
    """
    raw_symbol: str = msg["product_id"]
    canonical = _canonical(raw_symbol, registry)
    time_str = msg.get("time")
    exchange_ts = _parse_iso_ns(time_str) if time_str else None
    bids, asks = _parse_l2update_changes(msg.get("changes", []))
    yield BookDelta(
        exchange=EXCHANGE,
        symbol=canonical,
        symbol_raw=raw_symbol,
        exchange_ts=exchange_ts,
        local_ts=local_ts,
        bids=bids,
        asks=asks,
        seq_id=None,
        prev_seq_id=None,
        is_snapshot=False,
    )


def _normalize_ticker(
    msg: dict[str, Any],
    local_ts: int,
    registry: InstrumentRegistry | None,
) -> Iterable[Record]:
    """``ticker`` → BookTicker.

    Maps ``best_bid``/``best_bid_size``/``best_ask``/``best_ask_size`` to
    canonical BookTicker.  Coinbase has no derivative ticker (spot only).
    """
    raw_symbol: str = msg["product_id"]
    canonical = _canonical(raw_symbol, registry)
    time_str = msg.get("time")
    exchange_ts = _parse_iso_ns(time_str) if time_str else None

    bid_px_raw = msg.get("best_bid")
    bid_sz_raw = msg.get("best_bid_size")
    ask_px_raw = msg.get("best_ask")
    ask_sz_raw = msg.get("best_ask_size")

    if bid_px_raw is None or ask_px_raw is None:
        log.debug("coinbase: ticker missing best_bid/best_ask for %s", raw_symbol)
        return

    yield BookTicker(
        exchange=EXCHANGE,
        symbol=canonical,
        symbol_raw=raw_symbol,
        exchange_ts=exchange_ts,
        local_ts=local_ts,
        bid_px=float(bid_px_raw),
        bid_sz=float(bid_sz_raw) if bid_sz_raw is not None else 0.0,
        ask_px=float(ask_px_raw),
        ask_sz=float(ask_sz_raw) if ask_sz_raw is not None else 0.0,
        update_id=msg.get("sequence"),
    )


# ---------------------------------------------------------------------------
# Top-level dispatcher
# ---------------------------------------------------------------------------


def normalize_message(
    msg: dict[str, Any],
    local_ts: int,
    registry: InstrumentRegistry | None = None,
) -> Iterable[Record]:
    """Normalize a Coinbase Advanced Trade WebSocket message to canonical records.

    Dispatches on ``type``:

    ==================  ====================================
    ``match``           → Trade  (taker fill)
    ``last_match``      → Trade  (last fill on subscribe)
    ``snapshot``        → BookSnapshot  (level2 full book)
    ``l2update``        → BookDelta    (level2 incremental)
    ``ticker``          → BookTicker   (best bid/ask)
    ==================  ====================================

    Spot only: no funding/OI/liquidation records are emitted.
    """
    msg_type: str = msg.get("type", "")

    if msg_type in ("match", "last_match"):
        yield from _normalize_match(msg, local_ts, registry)

    elif msg_type == "snapshot":
        yield from _normalize_snapshot(msg, local_ts, registry)

    elif msg_type == "l2update":
        yield from _normalize_l2update(msg, local_ts, registry)

    elif msg_type == "ticker":
        yield from _normalize_ticker(msg, local_ts, registry)

    else:
        log.debug("coinbase: unhandled message type %r", msg_type)
