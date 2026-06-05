import logging
from collections.abc import Iterable
from typing import Any

from crocodile.exchanges.binance.book import normalize_depth
from crocodile.instruments.registry import InstrumentRegistry
from crocodile.schema.enums import OptType, Side
from crocodile.schema.records import (
    BookTicker,
    DerivativeTicker,
    Funding,
    Liquidation,
    OptionsChain,
    Record,
    Trade,
)
from crocodile.util.time import ms_to_ns

log = logging.getLogger(__name__)


def _parse_eapi_option_symbol(
    sym: str,
) -> tuple[str, float | None, OptType | None]:
    """Parse a Binance EAPI option symbol to (underlying, strike, opt_type).

    Binance EAPI format: ``BTC-231215-50000-C``
    Parts: [underlying, expiry_str, strike_str, C|P]

    Returns (underlying, strike, opt_type).  Returns (sym, None, None) if
    the symbol cannot be parsed (caller must handle gracefully).
    """
    parts = sym.split("-")
    if len(parts) != 4:
        return sym, None, None
    underlying = parts[0]
    try:
        strike = float(parts[2])
    except ValueError:
        return underlying, None, None
    raw_type = parts[3].upper()
    if raw_type == "C":
        opt_type = OptType.CALL
    elif raw_type == "P":
        opt_type = OptType.PUT
    else:
        return underlying, None, None
    return underlying, strike, opt_type


def _normalize_eapi_option_markprice(
    stream: str,
    data: list[dict[str, Any]],
    local_ts: int,
    venue: str,
    registry: InstrumentRegistry | None,
) -> Iterable[OptionsChain]:
    """Normalize Binance EAPI ``@optionMarkPrice`` stream entries to OptionsChain.

    Appendix §3.2: ``{underlying}@optionMarkPrice`` stream carries a list of
    option entries.  Each entry has:

    - ``s``   → symbol_raw  (e.g. ``BTC-231215-50000-C``)
    - ``mp``  → mark_price
    - ``d``   → delta
    - ``g``   → gamma
    - ``t``   → theta
    - ``v``   → vega
    - ``b``   → bid_iv  (buy IV, percent)
    - ``a``   → ask_iv  (ask/sell IV, percent)
    - ``vo``  → mark_iv (volume/mark vol)
    - ``oi``  → open_interest
    - ``E``   → exchange_ts  (event time, ms)

    Strike and opt_type are parsed from the ``s`` symbol field.
    """
    for entry in data:
        sym: str = entry.get("s", "")
        inst = registry.get_raw(venue, sym) if registry is not None else None

        if inst is not None:
            underlying = inst.base
            strike: float | None = inst.strike
            opt_type: OptType | None = (
                OptType(inst.opt_type) if inst.opt_type is not None else None
            )
            expiry: int | None = inst.expiry
        else:
            underlying, strike, opt_type = _parse_eapi_option_symbol(sym)
            expiry = None

        if strike is None or opt_type is None:
            log.warning(
                "binance: @optionMarkPrice: cannot parse strike/opt_type from symbol %r — skipping",
                sym,
            )
            continue

        canonical = inst.canonical if inst is not None else f"{venue}:{sym}"
        exchange_ts = ms_to_ns(int(entry["E"]))

        mp_raw = entry.get("mp")
        d_raw = entry.get("d")
        g_raw = entry.get("g")
        t_raw = entry.get("t")
        v_raw = entry.get("v")
        b_raw = entry.get("b")
        a_raw = entry.get("a")
        vo_raw = entry.get("vo")
        oi_raw = entry.get("oi")

        yield OptionsChain(
            exchange=venue,
            symbol=canonical,
            symbol_raw=sym,
            exchange_ts=exchange_ts,
            local_ts=local_ts,
            underlying=underlying,
            underlying_price=None,  # not in @optionMarkPrice event
            strike=strike,
            expiry=expiry if expiry is not None else 0,
            opt_type=opt_type,
            mark_price=float(mp_raw) if mp_raw is not None else None,
            mark_iv=float(vo_raw) if vo_raw is not None else None,
            bid_iv=float(b_raw) if b_raw is not None else None,
            ask_iv=float(a_raw) if a_raw is not None else None,
            open_interest=float(oi_raw) if oi_raw is not None else None,
            delta=float(d_raw) if d_raw is not None else None,
            gamma=float(g_raw) if g_raw is not None else None,
            theta=float(t_raw) if t_raw is not None else None,
            vega=float(v_raw) if v_raw is not None else None,
        )


def normalize_message(
    msg: dict[str, Any],
    local_ts: int,
    venue: str,
    registry: InstrumentRegistry | None = None,
) -> Iterable[Record]:
    """Normalize a Binance combined-stream message to canonical records.

    Handles:
    - @aggTrade -> Trade
    - @bookTicker -> BookTicker
    - @markPrice -> DerivativeTicker + Funding
    - @forceOrder -> Liquidation
    - @depth -> BookDelta (via normalize_depth)
    - @optionMarkPrice -> OptionsChain (EAPI options, with greeks d/g/t/v and IVs b/a/vo)
    """
    stream: str = msg.get("stream", "")
    data: dict[str, Any] = msg.get("data", {})

    if "@aggTrade" in stream:
        raw_symbol: str = data["s"]
        # m=true means buyer is maker, so the taker sold -> SELL
        side = Side.SELL if data["m"] else Side.BUY
        # Use T (trade time) not E (event time) for exchange_ts
        exchange_ts = ms_to_ns(data.get("T") or data["E"])
        # Canonical symbol via registry or fallback
        inst = registry.get_raw(venue, raw_symbol) if registry is not None else None
        canonical = inst.canonical if inst is not None else f"{venue}:{raw_symbol}"
        yield Trade(
            exchange=venue,
            symbol=canonical,
            symbol_raw=raw_symbol,
            exchange_ts=exchange_ts,
            local_ts=local_ts,
            id=str(data["a"]),
            price=float(data["p"]),
            amount=float(data["q"]),
            side=side,
        )

    elif "@bookTicker" in stream:
        raw_symbol = data["s"]
        inst = registry.get_raw(venue, raw_symbol) if registry is not None else None
        canonical = inst.canonical if inst is not None else f"{venue}:{raw_symbol}"
        yield BookTicker(
            exchange=venue,
            symbol=canonical,
            symbol_raw=raw_symbol,
            exchange_ts=None,
            local_ts=local_ts,
            bid_px=float(data["b"]),
            bid_sz=float(data["B"]),
            ask_px=float(data["a"]),
            ask_sz=float(data["A"]),
            update_id=data.get("u"),
        )

    elif "@markPrice" in stream:
        raw_symbol = data["s"]
        inst = registry.get_raw(venue, raw_symbol) if registry is not None else None
        canonical = inst.canonical if inst is not None else f"{venue}:{raw_symbol}"
        exchange_ts = ms_to_ns(data["E"])
        funding_ts_raw = data.get("T")
        yield DerivativeTicker(
            exchange=venue,
            symbol=canonical,
            symbol_raw=raw_symbol,
            exchange_ts=exchange_ts,
            local_ts=local_ts,
            mark_price=float(data["p"]),
            index_price=float(data["i"]),
            funding_rate=float(data["r"]),
            funding_timestamp=ms_to_ns(funding_ts_raw) if funding_ts_raw is not None else None,
        )
        yield Funding(
            exchange=venue,
            symbol=canonical,
            symbol_raw=raw_symbol,
            exchange_ts=exchange_ts,
            local_ts=local_ts,
            funding_rate=float(data["r"]),
            funding_timestamp=ms_to_ns(funding_ts_raw) if funding_ts_raw is not None else None,
        )

    elif "@forceOrder" in stream:
        order: dict[str, Any] = data["o"]
        raw_symbol = order["s"]
        inst = registry.get_raw(venue, raw_symbol) if registry is not None else None
        canonical = inst.canonical if inst is not None else f"{venue}:{raw_symbol}"
        raw_side = order["S"]
        side = Side.BUY if raw_side == "BUY" else Side.SELL if raw_side == "SELL" else Side.UNKNOWN
        yield Liquidation(
            exchange=venue,
            symbol=canonical,
            symbol_raw=raw_symbol,
            exchange_ts=ms_to_ns(order["T"]),
            local_ts=local_ts,
            price=float(order["ap"]),  # average execution price
            amount=float(order["q"]),
            side=side,
        )

    elif "@depth" in stream:
        yield from normalize_depth(msg, local_ts=local_ts, venue=venue, registry=registry)

    elif "@optionMarkPrice" in stream:
        # EAPI options: stream is "{underlying}@optionMarkPrice"; data is a list of entries
        raw_data: Any = msg.get("data")
        if isinstance(raw_data, list):
            yield from _normalize_eapi_option_markprice(
                stream, raw_data, local_ts, venue, registry
            )
        elif isinstance(raw_data, dict):
            # Some Binance streams wrap in a dict with an inner list; handle both shapes
            inner = raw_data.get("data", [raw_data])
            if isinstance(inner, list):
                yield from _normalize_eapi_option_markprice(
                    stream, inner, local_ts, venue, registry
                )
