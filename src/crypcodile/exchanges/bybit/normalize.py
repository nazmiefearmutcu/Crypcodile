"""Bybit V5 WebSocket message normalization.

Appendix §7 / §8 critical notes:
- Side is capitalized on the wire (``Buy``/``Sell``) → must lowercase to canonical.
- Order book: ``type="snapshot"`` → BookSnapshot; ``type="delta"`` → BookDelta.
  Levels are 2-element string arrays ``[price, qty]``; ``qty=="0"`` ⇒ canonical
  ``amount=0.0`` (removal signal).
- ``ts`` field on the outer envelope is the message publish time in ms.
- Tickers: perp/linear/inverse → DerivativeTicker + Funding + BookTicker;
  option tickers → OptionsChain (greeks if available).
- Funding data is REST-only for Bybit; the ticker ``fundingRate``/``nextFundingTime``
  fields are used to derive live Funding records from the ticker stream.
"""

from __future__ import annotations

import calendar
import logging
import re
import time as _time
from collections.abc import Iterable
from typing import Any

from crypcodile.instruments.registry import InstrumentRegistry, Kind
from crypcodile.schema.enums import OptType, Side
from crypcodile.schema.records import (
    BookDelta,
    BookSnapshot,
    BookTicker,
    DerivativeTicker,
    Funding,
    Liquidation,
    OptionsChain,
    Record,
    Trade,
)
from crypcodile.util.time import ms_to_ns

log = logging.getLogger(__name__)

EXCHANGE = "bybit"

# Month abbreviation → numeric month string (Bybit option expiry: DDMMMYY)
_MONTH_MAP = {
    "JAN": "01",
    "FEB": "02",
    "MAR": "03",
    "APR": "04",
    "MAY": "05",
    "JUN": "06",
    "JUL": "07",
    "AUG": "08",
    "SEP": "09",
    "OCT": "10",
    "NOV": "11",
    "DEC": "12",
}

# Bybit option date token: D{1,2}MMMYY (e.g. "30JUN25", "8JUN26")
_OPT_DATE_RE = re.compile(r"^(\d{1,2})([A-Z]{3})(\d{2})$")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _side(raw: str) -> Side:
    """Bybit side is capitalized (``Buy``/``Sell``) → canonical lowercase."""
    low = raw.lower()
    if low == "buy":
        return Side.BUY
    if low == "sell":
        return Side.SELL
    return Side.UNKNOWN


def _levels(rows: list[list[str]]) -> list[tuple[float, float]]:
    """Parse Bybit order-book level arrays ``[price_str, qty_str]``.

    ``qty == "0"`` is the canonical wire removal signal → emit ``amount=0.0``.
    """
    out: list[tuple[float, float]] = []
    for price_s, qty_s in rows:
        out.append((float(price_s), float(qty_s)))  # 0.0 when qty_s == "0"
    return out


def _canonical(venue: str, raw_symbol: str, registry: InstrumentRegistry | None) -> str:
    if registry is not None:
        inst = registry.get_raw(venue, raw_symbol)
        if inst is not None:
            return inst.canonical
    return f"{venue}:{raw_symbol}"


def _is_option_symbol(symbol: str) -> bool:
    """Heuristic: Bybit option symbols look like ``BTC-30JUN25-50000-C``."""
    parts = symbol.split("-")
    return len(parts) == 4 and parts[-1] in ("C", "P")


def _parse_option_expiry_ns(date_str: str) -> int | None:
    """Parse Bybit option date token ``DDMMMYY`` (e.g. ``30JUN25``) to midnight UTC ns.

    Returns ``None`` if the token cannot be parsed.
    """
    m = _OPT_DATE_RE.match(date_str.upper())
    if not m:
        return None
    day_i = int(m.group(1))
    month = _MONTH_MAP.get(m.group(2))
    if month is None:
        return None
    year = 2000 + int(m.group(3))
    try:
        struct = _time.strptime(f"{day_i:02d} {month} {year}", "%d %m %Y")
        return int(calendar.timegm(struct)) * 1_000_000_000
    except (ValueError, OverflowError):
        return None


def _parse_option_symbol(
    sym: str,
) -> tuple[float | None, OptType | None, int | None]:
    """Parse Bybit option symbol to (strike, opt_type, expiry_ns).

    Bybit option format: ``BTC-30JUN25-50000-C``
    Parts: [underlying, expiry_str (DDMMMYY), strike_str, C|P]
    """
    parts = sym.split("-")
    if len(parts) != 4:
        return None, None, None
    try:
        strike = float(parts[2])
    except ValueError:
        return None, None, None
    raw_type = parts[3].upper()
    if raw_type == "C":
        opt_type = OptType.CALL
    elif raw_type == "P":
        opt_type = OptType.PUT
    else:
        return None, None, None
    expiry_ns = _parse_option_expiry_ns(parts[1])
    return strike, opt_type, expiry_ns


# ---------------------------------------------------------------------------
# Per-topic normalizers
# ---------------------------------------------------------------------------


def _normalize_trade(
    topic: str,
    data: list[dict[str, Any]],
    ts_ms: int,
    local_ts: int,
    venue: str,
    registry: InstrumentRegistry | None,
) -> Iterable[Record]:
    # topic = "publicTrade.{symbol}"
    sym = topic.split(".", 1)[1] if "." in topic else ""
    canonical = _canonical(venue, sym, registry)
    for entry in data:
        trade_ts_ms = int(entry["T"])
        yield Trade(
            exchange=venue,
            symbol=canonical,
            symbol_raw=sym,
            exchange_ts=ms_to_ns(trade_ts_ms),
            local_ts=local_ts,
            id=str(entry["i"]),
            price=float(entry["p"]),
            amount=float(entry["v"]),
            side=_side(entry["S"]),
        )


def _normalize_liquidation(
    topic: str,
    data: dict[str, Any],
    local_ts: int,
    venue: str,
    registry: InstrumentRegistry | None,
) -> Iterable[Record]:
    """``liquidation.{symbol}`` → Liquidation.

    Bybit V5 liquidation data schema:
    ``{symbol, side, size, price, updatedTime}``

    - ``side``        : ``"Buy"``/``"Sell"`` (capitalized) → lowercase canonical
    - ``price``       : bankruptcy price string
    - ``size``        : quantity string
    - ``updatedTime`` : ms → ns for exchange_ts
    """
    sym: str = data.get("symbol", topic.split(".", 1)[1] if "." in topic else "")
    canonical = _canonical(venue, sym, registry)
    updated_time_ms: int = int(data.get("updatedTime", 0))
    yield Liquidation(
        exchange=venue,
        symbol=canonical,
        symbol_raw=sym,
        exchange_ts=ms_to_ns(updated_time_ms),
        local_ts=local_ts,
        price=float(data["price"]),
        amount=float(data["size"]),
        side=_side(data.get("side", "")),
    )


def _normalize_orderbook(
    topic: str,
    msg_type: str,
    data: dict[str, Any],
    ts_ms: int,
    local_ts: int,
    venue: str,
    registry: InstrumentRegistry | None,
) -> Iterable[Record]:
    # topic = "orderbook.{depth}.{symbol}"
    sym: str = data["s"]
    canonical = _canonical(venue, sym, registry)
    exchange_ts = ms_to_ns(ts_ms)
    bids = _levels(data.get("b", []))
    asks = _levels(data.get("a", []))
    seq_id: int | None = data.get("u")  # update id

    if msg_type == "snapshot":
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
        # delta — prev_seq_id not present in Bybit V5 book delta
        yield BookDelta(
            exchange=venue,
            symbol=canonical,
            symbol_raw=sym,
            exchange_ts=exchange_ts,
            local_ts=local_ts,
            bids=bids,
            asks=asks,
            seq_id=seq_id,
            prev_seq_id=None,
            is_snapshot=False,
        )


def _normalize_ticker(
    topic: str,
    data: dict[str, Any],
    ts_ms: int,
    local_ts: int,
    venue: str,
    registry: InstrumentRegistry | None,
) -> Iterable[Record]:
    # topic = "tickers.{symbol}"
    sym: str = data["symbol"]
    canonical = _canonical(venue, sym, registry)
    exchange_ts = ms_to_ns(ts_ms)

    # Determine if this is an option ticker
    is_option = False
    if registry is not None:
        inst = registry.get_raw(venue, sym)
        if inst is not None and inst.kind == Kind.OPTION:
            is_option = True
    if not is_option:
        is_option = _is_option_symbol(sym)

    if is_option:
        yield from _normalize_option_ticker(
            sym, canonical, data, exchange_ts, local_ts, venue, registry
        )
    else:
        yield from _normalize_linear_ticker(
            sym, canonical, data, exchange_ts, local_ts, venue
        )


def _normalize_linear_ticker(
    sym: str,
    canonical: str,
    data: dict[str, Any],
    exchange_ts: int,
    local_ts: int,
    venue: str,
) -> Iterable[Record]:
    """Emit DerivativeTicker + Funding + BookTicker for perp/linear/inverse tickers."""
    last_price = float(data["lastPrice"]) if data.get("lastPrice") else None
    mark_price = float(data["markPrice"]) if data.get("markPrice") else None
    index_price = float(data["indexPrice"]) if data.get("indexPrice") else None
    oi_raw = data.get("openInterest")
    open_interest = float(oi_raw) if oi_raw else None
    funding_rate_raw = data.get("fundingRate")
    funding_rate = float(funding_rate_raw) if funding_rate_raw is not None else None
    next_funding_raw = data.get("nextFundingTime")
    funding_ts = ms_to_ns(int(next_funding_raw)) if next_funding_raw else None

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

    if funding_rate is not None:
        yield Funding(
            exchange=venue,
            symbol=canonical,
            symbol_raw=sym,
            exchange_ts=exchange_ts,
            local_ts=local_ts,
            funding_rate=funding_rate,
            funding_timestamp=funding_ts,
            interval_hours=8,  # Bybit default 8h cadence
        )

    # BookTicker from bid1/ask1 fields (present in snapshot tickers)
    bid_px_raw = data.get("bid1Price")
    ask_px_raw = data.get("ask1Price")
    if bid_px_raw and ask_px_raw:
        yield BookTicker(
            exchange=venue,
            symbol=canonical,
            symbol_raw=sym,
            exchange_ts=exchange_ts,
            local_ts=local_ts,
            bid_px=float(bid_px_raw),
            bid_sz=float(data.get("bid1Size") or 0),
            ask_px=float(ask_px_raw),
            ask_sz=float(data.get("ask1Size") or 0),
        )


def _normalize_option_ticker(
    sym: str,
    canonical: str,
    data: dict[str, Any],
    exchange_ts: int,
    local_ts: int,
    venue: str,
    registry: InstrumentRegistry | None,
) -> Iterable[Record]:
    """Emit OptionsChain for option tickers (+greeks where available)."""
    # Resolve strike/expiry/opt_type from registry or symbol
    strike: float | None = None
    expiry: int | None = None
    opt_type_enum: OptType | None = None

    if registry is not None:
        inst = registry.get_raw(venue, sym)
        if inst is not None:
            strike = inst.strike
            expiry = inst.expiry
            if inst.opt_type == "C":
                opt_type_enum = OptType.CALL
            elif inst.opt_type == "P":
                opt_type_enum = OptType.PUT

    # Fall back to symbol parse for any missing field (including expiry when
    # unregistered): BTC-30JUN25-50000-C
    if strike is None or opt_type_enum is None or expiry is None:
        parsed_strike, parsed_opt, parsed_expiry = _parse_option_symbol(sym)
        if strike is None:
            strike = parsed_strike
        if opt_type_enum is None:
            opt_type_enum = parsed_opt
        if expiry is None:
            expiry = parsed_expiry

    # After registry + symbol-parse attempts, if either strike or opt_type is still
    # unresolved, skip the record (matches OKX/Binance skip-with-warning pattern).
    if strike is None or opt_type_enum is None:
        log.warning(
            "bybit: cannot resolve strike/opt_type for option symbol %r — skipping",
            sym,
        )
        return

    underlying_price_raw = data.get("underlyingPrice")
    underlying_price = float(underlying_price_raw) if underlying_price_raw else None
    mark_price_raw = data.get("markPrice")
    mark_price = float(mark_price_raw) if mark_price_raw else None
    mark_iv_raw = data.get("markIv")
    mark_iv = float(mark_iv_raw) if mark_iv_raw else None

    yield OptionsChain(
        exchange=venue,
        symbol=canonical,
        symbol_raw=sym,
        exchange_ts=exchange_ts,
        local_ts=local_ts,
        underlying=sym.split("-")[0] if "-" in sym else sym,
        underlying_price=underlying_price,
        strike=strike,
        expiry=expiry if expiry is not None else 0,
        opt_type=opt_type_enum,
        mark_price=mark_price,
        mark_iv=mark_iv,
        bid_px=float(data["bid1Price"]) if data.get("bid1Price") else None,
        bid_sz=float(data["bid1Size"]) if data.get("bid1Size") else None,
        ask_px=float(data["ask1Price"]) if data.get("ask1Price") else None,
        ask_sz=float(data["ask1Size"]) if data.get("ask1Size") else None,
        open_interest=float(data["openInterest"]) if data.get("openInterest") else None,
        delta=float(data["delta"]) if data.get("delta") else None,
        gamma=float(data["gamma"]) if data.get("gamma") else None,
        vega=float(data["vega"]) if data.get("vega") else None,
        theta=float(data["theta"]) if data.get("theta") else None,
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
    """Normalize a Bybit V5 WebSocket message to canonical records.

    Dispatches on ``topic`` prefix:
    - ``publicTrade.*``    → Trade
    - ``orderbook.*``      → BookSnapshot | BookDelta
    - ``tickers.*``        → DerivativeTicker + Funding + BookTicker  (perp/linear)
                             OptionsChain                              (option)
    """
    topic: str = msg.get("topic", "")
    msg_type: str = msg.get("type", "")
    ts_ms: int = int(msg.get("ts", 0))
    data: Any = msg.get("data")

    if topic.startswith("publicTrade."):
        if isinstance(data, list):
            yield from _normalize_trade(topic, data, ts_ms, local_ts, venue, registry)

    elif topic.startswith("orderbook."):
        if isinstance(data, dict):
            yield from _normalize_orderbook(topic, msg_type, data, ts_ms, local_ts, venue, registry)

    elif topic.startswith("tickers."):
        if isinstance(data, dict):
            yield from _normalize_ticker(topic, data, ts_ms, local_ts, venue, registry)

    elif topic.startswith("liquidation."):
        if isinstance(data, dict):
            yield from _normalize_liquidation(topic, data, local_ts, venue, registry)

    else:
        log.debug("bybit: unhandled topic %r", topic)
