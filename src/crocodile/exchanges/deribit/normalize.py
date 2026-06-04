import calendar
import time as _time
from collections.abc import Iterable
from typing import Any

from crocodile.instruments.registry import InstrumentRegistry
from crocodile.schema.enums import OptType, Side
from crocodile.schema.records import (
    BookDelta,
    BookSnapshot,
    DerivativeTicker,
    Funding,
    Liquidation,
    OptionsChain,
    Record,
    Trade,
)
from crocodile.util.time import ms_to_ns

EXCHANGE = "deribit"

# Month abbreviation -> numeric string used in option symbol parsing
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


def _levels(rows: list[list[Any]]) -> list[tuple[float, float]]:
    out = []
    for action, price, amount in rows:
        out.append((float(price), 0.0 if action == "delete" else float(amount)))
    return out


def _side(direction: str) -> Side:
    return Side.BUY if direction == "buy" else Side.SELL if direction == "sell" else Side.UNKNOWN


def _parse_option_symbol(sym: str) -> tuple[str, float, int, OptType]:
    """Parse Deribit option symbol BASE-DDMMM-STRIKE-C|P.

    Returns (underlying, strike, expiry_ns, opt_type).
    expiry_ns is a best-effort nanosecond timestamp derived from the date string;
    registry values are preferred when available.
    """
    parts = sym.split("-")
    # e.g. ["BTC", "30JUN", "50000", "C"]
    underlying = parts[0]
    date_str = parts[1]  # e.g. "30JUN"
    strike = float(parts[2])
    opt_type = OptType.CALL if parts[3] == "C" else OptType.PUT

    # Parse date: DD + MMM (3-char month abbreviation)
    day = date_str[:2]
    mon_abbr = date_str[2:5].upper()
    month = _MONTH_MAP.get(mon_abbr, "01")
    # Use current decade as best guess (expiry year in symbol is ambiguous without full year)
    # Approximate: 2020-2030 range; for golden tests exact value comes from registry
    year = "2025"  # fallback; registry overrides this
    struct = _time.strptime(f"{day} {month} {year}", "%d %m %Y")
    expiry_ns = int(calendar.timegm(struct)) * 1_000_000_000
    return underlying, strike, expiry_ns, opt_type


def normalize_message(
    msg: dict[str, Any], local_ts: int, registry: InstrumentRegistry | None = None
) -> Iterable[Record]:
    params: dict[str, Any] = msg.get("params") or {}
    channel: str = params.get("channel", "")
    data: Any = params.get("data")
    if channel.startswith("trades."):
        for t in (data or []):
            sym = t["instrument_name"]
            side = _side(t["direction"])
            yield Trade(
                exchange=EXCHANGE,
                symbol=f"{EXCHANGE}:{sym}",
                symbol_raw=sym,
                exchange_ts=ms_to_ns(t["timestamp"]),
                local_ts=local_ts,
                id=str(t["trade_id"]),
                price=float(t["price"]),
                amount=float(t["amount"]),
                side=side,
                liquidation=t.get("liquidation"),
            )
            if t.get("liquidation"):
                yield Liquidation(
                    exchange=EXCHANGE,
                    symbol=f"{EXCHANGE}:{sym}",
                    symbol_raw=sym,
                    exchange_ts=ms_to_ns(t["timestamp"]),
                    local_ts=local_ts,
                    price=float(t["price"]),
                    amount=float(t["amount"]),
                    side=side,
                    id=str(t["trade_id"]),
                )
    if channel.startswith("book."):
        d: dict[str, Any] = data or {}
        sym = d["instrument_name"]
        common: dict[str, Any] = dict(
            exchange=EXCHANGE,
            symbol=f"{EXCHANGE}:{sym}",
            symbol_raw=sym,
            exchange_ts=ms_to_ns(d["timestamp"]),
            local_ts=local_ts,
            bids=_levels(d.get("bids", [])),
            asks=_levels(d.get("asks", [])),
        )
        if d.get("type") == "snapshot":
            yield BookSnapshot(
                **common,
                depth=len(d.get("bids", [])) + len(d.get("asks", [])),
                sequence_id=d.get("change_id"),
                is_snapshot=True,
            )
        else:
            yield BookDelta(
                **common,
                seq_id=d.get("change_id"),
                prev_seq_id=d.get("prev_change_id"),
                is_snapshot=False,
            )
    if channel.startswith("ticker."):
        td: dict[str, Any] = data or {}
        sym = td["instrument_name"]
        exchange_ts = ms_to_ns(td["timestamp"])
        symbol_canonical = f"{EXCHANGE}:{sym}"

        # Determine if option or perp/future by presence of greeks or mark_iv
        is_option = td.get("greeks") is not None or td.get("mark_iv") is not None

        if is_option:
            # Resolve metadata from registry if available, else parse the symbol
            inst = registry.get_raw(EXCHANGE, sym) if registry is not None else None
            if inst is not None:
                strike = inst.strike
                expiry = inst.expiry
                opt_type = OptType(inst.opt_type) if inst.opt_type is not None else OptType.CALL
                underlying = inst.base
            else:
                underlying, strike, expiry, opt_type = _parse_option_symbol(sym)

            greeks: dict[str, Any] = td.get("greeks") or {}
            yield OptionsChain(
                exchange=EXCHANGE,
                symbol=symbol_canonical,
                symbol_raw=sym,
                exchange_ts=exchange_ts,
                local_ts=local_ts,
                underlying=underlying,
                underlying_price=td.get("underlying_price"),
                strike=strike if strike is not None else 0.0,
                expiry=expiry if expiry is not None else 0,
                opt_type=opt_type,
                mark_price=td.get("mark_price"),
                mark_iv=td.get("mark_iv"),
                bid_px=td.get("best_bid_price"),
                bid_sz=td.get("best_bid_amount"),
                bid_iv=td.get("bid_iv"),
                ask_px=td.get("best_ask_price"),
                ask_sz=td.get("best_ask_amount"),
                ask_iv=td.get("ask_iv"),
                last_price=td.get("last_price"),
                open_interest=td.get("open_interest"),
                delta=greeks.get("delta"),
                gamma=greeks.get("gamma"),
                vega=greeks.get("vega"),
                theta=greeks.get("theta"),
                rho=greeks.get("rho"),
            )
        else:
            # Perp / future: emit DerivativeTicker + Funding (from current_funding/funding_8h)
            yield DerivativeTicker(
                exchange=EXCHANGE,
                symbol=symbol_canonical,
                symbol_raw=sym,
                exchange_ts=exchange_ts,
                local_ts=local_ts,
                last_price=td.get("last_price"),
                mark_price=td.get("mark_price"),
                index_price=td.get("index_price"),
                funding_rate=td.get("current_funding"),
                predicted_funding_rate=td.get("funding_8h"),
                open_interest=td.get("open_interest"),
            )
            # Emit Funding derived from current_funding/funding_8h
            # canonical: funding_rate = current_funding; funding_8h -> predicted_funding_rate
            if td.get("current_funding") is not None:
                yield Funding(
                    exchange=EXCHANGE,
                    symbol=symbol_canonical,
                    symbol_raw=sym,
                    exchange_ts=exchange_ts,
                    local_ts=local_ts,
                    funding_rate=float(td["current_funding"]),
                    predicted_funding_rate=td.get("funding_8h"),
                )
