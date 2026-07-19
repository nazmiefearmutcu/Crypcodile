"""Whole-market universe: enumerate and rank the tradable set across venues.

This is the layer that turns "pull the entire crypto market" into a concrete
symbol list.  It answers two questions for **any** venue (native or ccxt):

* *What can I trade here?* — :func:`exchange_instruments` enumerates every
  market on a venue as :class:`~crypcodile.instruments.registry.Instrument`.
* *What matters most?* — :func:`top_symbols_by_volume` ranks a venue's markets
  by 24 h quote volume so a bounded ``--top N`` collect covers the liquid core
  of the market instead of ten thousand dead pairs.

For ccxt venues (the 100+ exchanges) this uses ccxt's unified ``load_markets`` /
``fetch_tickers``.  For the native-only connectors (on-chain venues, Derive)
it falls back to the connector's own ``list_instruments`` — without live volume,
so ranking there is best-effort (declared, not silent).
"""

from __future__ import annotations

import logging
from typing import Any

from crypcodile.exchanges.factory import list_ccxt_exchanges
from crypcodile.instruments.registry import Instrument, InstrumentRegistry, Kind

log = logging.getLogger(__name__)


def _ccxt_supported(exchange: str) -> bool:
    """True when ccxt can serve *exchange* (installed and knows the id)."""
    return exchange in set(list_ccxt_exchanges())


def filter_instruments(
    instruments: list[Instrument],
    *,
    kinds: set[Kind] | None = None,
    quote: str | None = None,
    base: str | None = None,
    active_only: bool = True,
) -> list[Instrument]:
    """Filter an instrument list by kind / quote / base (case-insensitive)."""
    q = quote.upper() if quote else None
    b = base.upper() if base else None
    out: list[Instrument] = []
    for inst in instruments:
        if kinds is not None and inst.kind not in kinds:
            continue
        if q is not None and inst.quote.upper() != q:
            continue
        if b is not None and inst.base.upper() != b:
            continue
        out.append(inst)
    return out


async def exchange_instruments(
    exchange: str,
    *,
    registry: InstrumentRegistry | None = None,
    exchange_config: dict[str, Any] | None = None,
) -> list[Instrument]:
    """Enumerate every tradable instrument on *exchange*.

    Works for both ccxt venues (via ``load_markets``) and native-only
    connectors (via their ``list_instruments``).  When *registry* is given,
    every instrument is also added to it for downstream canonical resolution.
    """
    if _ccxt_supported(exchange):
        instruments = await _ccxt_instruments(exchange, exchange_config)
    else:
        instruments = await _native_instruments(exchange)

    if registry is not None:
        for inst in instruments:
            registry.add(inst)
    return instruments


async def _ccxt_instruments(
    exchange: str, exchange_config: dict[str, Any] | None
) -> list[Instrument]:
    from crypcodile.exchanges.ccxt_universal import normalize as norm

    ex = _make_ccxt(exchange, exchange_config)
    try:
        markets = await ex.load_markets()
    finally:
        await ex.close()
    out: list[Instrument] = []
    for market in markets.values():
        inst = norm.market_to_instrument(market, exchange)
        if inst is not None:
            out.append(inst)
    return out


async def _native_instruments(exchange: str) -> list[Instrument]:
    """Enumerate a native-only connector's instruments (no ccxt)."""
    from crypcodile.exchanges.factory import make_connector
    from crypcodile.sink.base import Sink

    class _NullSink(Sink):
        async def put(self, record: Any) -> None: ...
        async def flush(self) -> None: ...
        async def close(self) -> None: ...

    conn = make_connector(
        exchange,
        symbols=[],
        channels=[],
        out=_NullSink(),
        registry=InstrumentRegistry(),
    )
    return await conn.list_instruments()


def _make_ccxt(exchange: str, exchange_config: dict[str, Any] | None) -> Any:
    import ccxt.async_support as ccxt

    cfg: dict[str, Any] = {"enableRateLimit": True}
    if exchange_config:
        cfg.update(exchange_config)
    return getattr(ccxt, exchange)(cfg)


async def top_symbols_by_volume(
    exchange: str,
    n: int,
    *,
    quote: str | None = "USDT",
    kinds: set[Kind] | None = None,
    exchange_config: dict[str, Any] | None = None,
) -> list[str]:
    """Return the *n* highest 24 h quote-volume symbols on *exchange*.

    Symbols are ccxt **unified** strings (``"BTC/USDT"``) ready to hand to
    :class:`~crypcodile.exchanges.ccxt_universal.connector.CCXTConnector`.

    Ranking uses ccxt ``fetch_tickers`` (one request returns the whole board).
    When a venue lacks ``fetchTickers`` — or is native-only — this falls back to
    the first *n* filtered instruments in enumeration order and logs that the
    result is **not** volume-ranked, so a caller never mistakes arbitrary order
    for liquidity order.
    """
    if not _ccxt_supported(exchange):
        insts = filter_instruments(
            await _native_instruments(exchange), kinds=kinds, quote=quote
        )
        log.warning(
            "%s: native venue has no live volume feed — returning first %d "
            "instruments in enumeration order (NOT volume-ranked)",
            exchange,
            n,
        )
        return [i.symbol_raw for i in insts[:n]]

    ex = _make_ccxt(exchange, exchange_config)
    try:
        markets = await ex.load_markets()
        if not ex.has.get("fetchTickers"):
            log.warning(
                "%s: no fetchTickers — returning first %d matching markets "
                "(NOT volume-ranked)",
                exchange,
                n,
            )
            syms = _filter_market_symbols(markets, quote=quote, kinds=kinds)
            return syms[:n]
        tickers = await ex.fetch_tickers()
    finally:
        await ex.close()

    q = quote.upper() if quote else None
    ranked: list[tuple[float, str]] = []
    for symbol, ticker in tickers.items():
        market = markets.get(symbol)
        if market is None:
            continue
        if q is not None and str(market.get("quote", "")).upper() != q:
            continue
        if kinds is not None and _market_kind(market) not in kinds:
            continue
        vol = ticker.get("quoteVolume")
        try:
            vol_f = float(vol) if vol is not None else 0.0
        except (TypeError, ValueError):
            vol_f = 0.0
        ranked.append((vol_f, symbol))

    ranked.sort(key=lambda t: t[0], reverse=True)
    return [symbol for _vol, symbol in ranked[:n]]


def _market_kind(market: dict[str, Any]) -> Kind:
    from crypcodile.exchanges.ccxt_universal import normalize as norm

    return norm.kind_from_market(market)


def _filter_market_symbols(
    markets: dict[str, Any],
    *,
    quote: str | None,
    kinds: set[Kind] | None,
) -> list[str]:
    q = quote.upper() if quote else None
    out: list[str] = []
    for symbol, market in markets.items():
        if q is not None and str(market.get("quote", "")).upper() != q:
            continue
        if kinds is not None and _market_kind(market) not in kinds:
            continue
        out.append(symbol)
    return out
