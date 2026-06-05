"""OKX V5 WebSocket message normalization.

Appendix §7 / §8 critical notes:
- ``books*`` channels are **snapshots, not deltas** on the wire.  OKX sends
  ``action="snapshot"`` for the full book and ``action="update"`` for
  incremental updates; maintain L2 locally between updates.
- Book levels are 4-element string arrays ``[price, qty, liquidated_orders,
  order_count]``; ``qty=="0"`` ⇒ canonical ``amount=0.0`` (removal signal).
- ``action="snapshot"`` → BookSnapshot; ``action="update"`` → BookDelta.
  ``seqId`` → ``seq_id``; ``prevSeqId`` → ``prev_seq_id``.
- OKX option ``instId`` format: ``BTC-USD-25DEC22-40000-C``
  (``underlying-expiry-strike-opttype``); option-summary carries ``uly`` as
  the underlying.  Parse strike/opt_type from the last two dash-segments.
- Side on trades is lowercase on the wire (``buy``/``sell``) — no conversion
  needed.
- Region endpoints: US/AU→``us.okx.com``, EU→``eea.okx.com``; default
  ``ws.okx.com:8443``.  The connector selects the base URL via ``region``.
- ``funding-rate`` channel: ``fundingRate`` + ``fundingTime`` (settlement ts,
  ms) + ``nextFundingRate`` (predicted).
- ``open-interest`` channel: ``oi`` (contracts) + ``oiCcy`` (base-ccy equiv).
- ``liq-orders`` channel: ``details[*]{side, sz, bkPx, ts}``.
- ``option-summary``: ``markVol``→mark_iv, ``bidVol``→bid_iv, ``askVol``→ask_iv,
  ``delta/gamma/vega/theta``; ``uly`` is the underlying; strike/opt_type
  parsed from ``instId`` when not in registry.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from crocodile.instruments.registry import InstrumentRegistry, Kind
from crocodile.schema.enums import OptType, Side
from crocodile.schema.records import (
    BookDelta,
    BookSnapshot,
    DerivativeTicker,
    Funding,
    Liquidation,
    OpenInterest,
    OptionsChain,
    Record,
    Trade,
)
from crocodile.util.time import ms_to_ns

log = logging.getLogger(__name__)

EXCHANGE = "okx"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _side(raw: str) -> Side:
    """OKX side is lowercase on the wire (``buy``/``sell``)."""
    if raw == "buy":
        return Side.BUY
    if raw == "sell":
        return Side.SELL
    return Side.UNKNOWN


def _levels(rows: list[list[str]]) -> list[tuple[float, float]]:
    """Parse OKX order-book level arrays ``[price, qty, liquidated_orders, count]``.

    OKX book levels are 4-element arrays on the wire.  ``qty == "0"`` is the
    canonical wire removal signal → emit ``amount=0.0``.
    """
    out: list[tuple[float, float]] = []
    for row in rows:
        price = float(row[0])
        qty = float(row[1])  # 0.0 when qty_s == "0"
        out.append((price, qty))
    return out


def _canonical(venue: str, raw_symbol: str, registry: InstrumentRegistry | None) -> str:
    if registry is not None:
        inst = registry.get_raw(venue, raw_symbol)
        if inst is not None:
            return inst.canonical
    return f"{venue}:{raw_symbol}"


def _parse_option_instid(inst_id: str) -> tuple[float | None, OptType | None]:
    """Parse OKX option instId to extract strike and opt_type.

    OKX option format: ``BTC-USD-25DEC22-40000-C``
    Parts: [underlying_base, underlying_quote, expiry_str, strike_str, opt_type]
    """
    parts = inst_id.split("-")
    if len(parts) < 5:
        return None, None
    try:
        strike = float(parts[-2])
    except (ValueError, IndexError):
        return None, None
    raw_type = parts[-1].upper()
    opt_type = OptType.CALL if raw_type == "C" else OptType.PUT if raw_type == "P" else None
    return strike, opt_type


# ---------------------------------------------------------------------------
# Per-channel normalizers
# ---------------------------------------------------------------------------


def _normalize_trades(
    arg: dict[str, Any],
    data: list[dict[str, Any]],
    local_ts: int,
    venue: str,
    registry: InstrumentRegistry | None,
) -> Iterable[Record]:
    """``trades`` channel → Trade records.

    OKX trade fields:
    - ``instId``  → symbol_raw
    - ``tradeId`` → id
    - ``px``      → price
    - ``sz``      → amount
    - ``side``    → side (already lowercase)
    - ``ts``      → exchange_ts (ms)
    """
    for entry in data:
        sym: str = entry.get("instId", arg.get("instId", ""))
        canonical = _canonical(venue, sym, registry)
        yield Trade(
            exchange=venue,
            symbol=canonical,
            symbol_raw=sym,
            exchange_ts=ms_to_ns(int(entry["ts"])),
            local_ts=local_ts,
            id=str(entry["tradeId"]),
            price=float(entry["px"]),
            amount=float(entry["sz"]),
            side=_side(entry["side"]),
        )


def _normalize_books(
    arg: dict[str, Any],
    action: str,
    data: list[dict[str, Any]],
    local_ts: int,
    venue: str,
    registry: InstrumentRegistry | None,
) -> Iterable[Record]:
    """``books``/``bbo-tbt``/``books50-l2-tbt`` channels → BookSnapshot | BookDelta.

    OKX sends ``action="snapshot"`` for the initial full book, then
    ``action="update"`` for incremental updates.  Both carry ``seqId`` and
    ``prevSeqId`` for gap detection.

    Level format: ``[price_str, qty_str, liquidated_orders_count, order_count]``.
    ``qty_str == "0"`` → canonical ``amount=0.0`` (level removal).
    """
    sym: str = arg.get("instId", "")
    canonical = _canonical(venue, sym, registry)
    is_snapshot = action == "snapshot"

    for entry in data:
        exchange_ts = ms_to_ns(int(entry["ts"]))
        bids = _levels(entry.get("bids", []))
        asks = _levels(entry.get("asks", []))
        seq_id: int | None = entry.get("seqId")
        prev_seq_id_raw = entry.get("prevSeqId")
        # prevSeqId == -1 is used by OKX on the first snapshot; treat as None
        prev_seq_id: int | None = (
            None if prev_seq_id_raw is None or int(prev_seq_id_raw) < 0
            else int(prev_seq_id_raw)
        )

        if is_snapshot:
            yield BookSnapshot(
                exchange=venue,
                symbol=canonical,
                symbol_raw=sym,
                exchange_ts=exchange_ts,
                local_ts=local_ts,
                bids=bids,
                asks=asks,
                depth=len(bids) + len(asks),
                sequence_id=seq_id,
                is_snapshot=True,
            )
        else:
            yield BookDelta(
                exchange=venue,
                symbol=canonical,
                symbol_raw=sym,
                exchange_ts=exchange_ts,
                local_ts=local_ts,
                bids=bids,
                asks=asks,
                seq_id=seq_id,
                prev_seq_id=prev_seq_id,
                is_snapshot=False,
            )


def _normalize_tickers(
    arg: dict[str, Any],
    data: list[dict[str, Any]],
    local_ts: int,
    venue: str,
    registry: InstrumentRegistry | None,
) -> Iterable[Record]:
    """``tickers`` channel → DerivativeTicker only.

    Fields:
    - ``last``           → last_price
    - ``markPx``         → mark_price
    - ``indexPx``        → index_price
    - ``openInterest``   → open_interest
    - ``fundingRate``    → funding_rate (carried on the ticker; no separate Funding record)
    - ``nextFundingTime``→ funding_timestamp (ms → ns; carried on the ticker)

    Note: ``Funding`` records are emitted **exclusively** from the ``funding-rate``
    channel (``_normalize_funding_rate``).  Emitting Funding here would create
    duplicate records when both channels are subscribed simultaneously.
    """
    for entry in data:
        sym: str = entry.get("instId", arg.get("instId", ""))
        canonical = _canonical(venue, sym, registry)
        exchange_ts = ms_to_ns(int(entry["ts"]))

        last_raw = entry.get("last")
        mark_raw = entry.get("markPx")
        index_raw = entry.get("indexPx")
        oi_raw = entry.get("openInterest")
        fr_raw = entry.get("fundingRate")
        nft_raw = entry.get("nextFundingTime")

        last_price = float(last_raw) if last_raw else None
        mark_price = float(mark_raw) if mark_raw else None
        index_price = float(index_raw) if index_raw else None
        open_interest = float(oi_raw) if oi_raw else None
        funding_rate = float(fr_raw) if fr_raw is not None else None
        funding_ts = ms_to_ns(int(nft_raw)) if nft_raw else None

        yield DerivativeTicker(
            exchange=venue,
            symbol=canonical,
            symbol_raw=sym,
            exchange_ts=exchange_ts,
            local_ts=local_ts,
            last_price=last_price,
            mark_price=mark_price,
            index_price=index_price,
            funding_rate=funding_rate,
            funding_timestamp=funding_ts,
            open_interest=open_interest,
        )


def _normalize_funding_rate(
    arg: dict[str, Any],
    data: list[dict[str, Any]],
    local_ts: int,
    venue: str,
    registry: InstrumentRegistry | None,
) -> Iterable[Record]:
    """``funding-rate`` channel → Funding records.

    Fields:
    - ``ts``              → exchange_ts (message/event time, ms → ns; fallback: local_ts)
    - ``fundingRate``     → funding_rate (canonical)
    - ``fundingTime``     → funding_timestamp (settlement time, ms → ns)
    - ``nextFundingRate`` → predicted_funding_rate
    - ``nextFundingTime`` → (not used in canonical Funding record)

    ``exchange_ts`` is the message/event time (``ts``); ``funding_timestamp`` is the
    *future* settlement time (``fundingTime``).  Every other connector separates
    event-time from settlement-time — OKX must do the same.
    """
    for entry in data:
        sym: str = entry.get("instId", arg.get("instId", ""))
        canonical = _canonical(venue, sym, registry)
        # exchange_ts = message/event time ("ts"); fallback to local_ts if absent
        raw_ts = entry.get("ts")
        exchange_ts = ms_to_ns(int(raw_ts)) if raw_ts is not None else local_ts
        # funding_timestamp = settlement time ("fundingTime")
        funding_ts_ns = ms_to_ns(int(entry["fundingTime"]))
        yield Funding(
            exchange=venue,
            symbol=canonical,
            symbol_raw=sym,
            exchange_ts=exchange_ts,
            local_ts=local_ts,
            funding_rate=float(entry["fundingRate"]),
            funding_timestamp=funding_ts_ns,
            predicted_funding_rate=(
                float(entry["nextFundingRate"]) if entry.get("nextFundingRate") else None
            ),
            interval_hours=8,  # OKX default 8h cadence
        )


def _normalize_open_interest(
    arg: dict[str, Any],
    data: list[dict[str, Any]],
    local_ts: int,
    venue: str,
    registry: InstrumentRegistry | None,
) -> Iterable[Record]:
    """``open-interest`` channel → OpenInterest records.

    Fields:
    - ``oi``    → open_interest (contracts)
    - ``oiCcy`` → open_interest_value (base-currency equivalent)
    - ``ts``    → exchange_ts (ms → ns)
    """
    for entry in data:
        sym: str = entry.get("instId", arg.get("instId", ""))
        canonical = _canonical(venue, sym, registry)
        yield OpenInterest(
            exchange=venue,
            symbol=canonical,
            symbol_raw=sym,
            exchange_ts=ms_to_ns(int(entry["ts"])),
            local_ts=local_ts,
            open_interest=float(entry["oi"]),
            open_interest_value=float(entry["oiCcy"]) if entry.get("oiCcy") else None,
        )


def _normalize_liq_orders(
    arg: dict[str, Any],
    data: list[dict[str, Any]],
    local_ts: int,
    venue: str,
    registry: InstrumentRegistry | None,
) -> Iterable[Record]:
    """``liq-orders`` channel → Liquidation records.

    OKX liquidation structure:
    ``{instId, instType, details: [{side, sz, bkPx, bkLoss, ts}]}``.

    Fields:
    - ``side``  → side (lowercase)
    - ``sz``    → amount
    - ``bkPx``  → price (bankruptcy price)
    - ``ts``    → exchange_ts (ms → ns)
    """
    for entry in data:
        sym: str = entry.get("instId", arg.get("instId", ""))
        canonical = _canonical(venue, sym, registry)
        for detail in entry.get("details", []):
            yield Liquidation(
                exchange=venue,
                symbol=canonical,
                symbol_raw=sym,
                exchange_ts=ms_to_ns(int(detail["ts"])),
                local_ts=local_ts,
                price=float(detail["bkPx"]),
                amount=float(detail["sz"]),
                side=_side(detail["side"]),
            )


def _normalize_option_summary(
    arg: dict[str, Any],
    data: list[dict[str, Any]],
    local_ts: int,
    venue: str,
    registry: InstrumentRegistry | None,
) -> Iterable[Record]:
    """``option-summary`` channel → OptionsChain records.

    OKX option-summary fields:
    - ``uly``     → underlying
    - ``markVol`` → mark_iv
    - ``bidVol``  → bid_iv
    - ``askVol``  → ask_iv
    - ``delta``, ``gamma``, ``vega``, ``theta`` → greeks
    - ``fwdPx``   → underlying_price (forward price)
    - Strike / opt_type resolved from registry or parsed from ``instId``.
    """
    for entry in data:
        sym: str = entry.get("instId", "")
        canonical = _canonical(venue, sym, registry)
        exchange_ts = ms_to_ns(int(entry["ts"]))

        # Resolve metadata from registry or symbol string
        strike: float | None = None
        expiry: int | None = None
        opt_type_enum: OptType | None = None

        if registry is not None:
            inst = registry.get_raw(venue, sym)
            if inst is not None and inst.kind == Kind.OPTION:
                strike = inst.strike
                expiry = inst.expiry
                if inst.opt_type == "C":
                    opt_type_enum = OptType.CALL
                elif inst.opt_type == "P":
                    opt_type_enum = OptType.PUT

        if strike is None or opt_type_enum is None:
            strike, opt_type_enum = _parse_option_instid(sym)

        if strike is None or opt_type_enum is None:
            log.warning(
                "okx: option-summary: cannot parse strike/opt_type from instId %r — skipping",
                sym,
            )
            continue

        underlying: str = entry.get("uly", sym.rsplit("-", 2)[0] if sym.count("-") >= 2 else sym)
        fwd_px_raw = entry.get("fwdPx")
        underlying_price = float(fwd_px_raw) if fwd_px_raw else None

        mark_vol_raw = entry.get("markVol")
        bid_vol_raw = entry.get("bidVol")
        ask_vol_raw = entry.get("askVol")
        delta_raw = entry.get("delta")
        gamma_raw = entry.get("gamma")
        vega_raw = entry.get("vega")
        theta_raw = entry.get("theta")

        yield OptionsChain(
            exchange=venue,
            symbol=canonical,
            symbol_raw=sym,
            exchange_ts=exchange_ts,
            local_ts=local_ts,
            underlying=underlying,
            underlying_price=underlying_price,
            strike=strike,
            expiry=expiry or 0,
            opt_type=opt_type_enum,
            mark_iv=float(mark_vol_raw) if mark_vol_raw else None,
            bid_iv=float(bid_vol_raw) if bid_vol_raw else None,
            ask_iv=float(ask_vol_raw) if ask_vol_raw else None,
            delta=float(delta_raw) if delta_raw else None,
            gamma=float(gamma_raw) if gamma_raw else None,
            vega=float(vega_raw) if vega_raw else None,
            theta=float(theta_raw) if theta_raw else None,
        )


# ---------------------------------------------------------------------------
# Top-level dispatcher
# ---------------------------------------------------------------------------


def normalize_message(
    msg: dict[str, Any],
    local_ts: int,
    venue: str = EXCHANGE,
    registry: InstrumentRegistry | None = None,
) -> Iterable[Record]:
    """Normalize an OKX V5 WebSocket message to canonical records.

    OKX WS messages have the shape::

        {
            "arg":    {"channel": "...", "instId": "..."},
            "action": "snapshot" | "update",   # books channels only
            "data":   [...]
        }

    Dispatches on ``arg.channel``:

    ==================  ================================================
    ``trades``          → Trade
    ``books``           → BookSnapshot (action=snapshot) | BookDelta (action=update)
    ``bbo-tbt``         → BookSnapshot | BookDelta
    ``books50-l2-tbt``  → BookSnapshot | BookDelta
    ``tickers``         → DerivativeTicker  (funding fields carried inline; no Funding record)
    ``funding-rate``    → Funding
    ``open-interest``   → OpenInterest
    ``liq-orders``      → Liquidation
    ``option-summary``  → OptionsChain
    ==================  ================================================
    """
    arg: dict[str, Any] = msg.get("arg") or {}
    channel: str = arg.get("channel", "")
    action: str = msg.get("action", "")
    data: Any = msg.get("data")

    if not isinstance(data, list):
        log.debug("okx: unexpected data type for channel %r", channel)
        return

    if channel == "trades":
        yield from _normalize_trades(arg, data, local_ts, venue, registry)

    elif channel in ("books", "bbo-tbt", "books50-l2-tbt", "books5"):
        yield from _normalize_books(arg, action, data, local_ts, venue, registry)

    elif channel == "tickers":
        yield from _normalize_tickers(arg, data, local_ts, venue, registry)

    elif channel == "funding-rate":
        yield from _normalize_funding_rate(arg, data, local_ts, venue, registry)

    elif channel == "open-interest":
        yield from _normalize_open_interest(arg, data, local_ts, venue, registry)

    elif channel == "liq-orders":
        yield from _normalize_liq_orders(arg, data, local_ts, venue, registry)

    elif channel == "option-summary":
        yield from _normalize_option_summary(arg, data, local_ts, venue, registry)

    else:
        log.debug("okx: unhandled channel %r", channel)
