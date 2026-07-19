"""Pure ``ccxt unified dict -> crypcodile Record`` transforms.

Every function here is a pure, synchronous mapping from a ccxt unified data
structure (as returned by ``fetch_ticker``, ``fetch_order_book``,
``fetch_trades``, ``fetch_ohlcv``, ``fetch_funding_rate`` and ``load_markets``)
onto Crypcodile's canonical :mod:`crypcodile.schema.records` types.  No network,
no exchange objects — just dicts in, structs out — so the whole normalization
surface is unit-testable against captured fixtures.

ccxt reference: <https://docs.ccxt.com/#/README?id=unified-api>

Shape gotchas handled here (found by live-probing Kraken / KuCoin / MEXC):

* Order-book levels may carry a **third element** (Kraken emits
  ``[price, amount, timestamp]``); we take ``level[0]`` / ``level[1]`` only.
* ``timestamp`` is frequently ``None`` (Kraken tickers / books); the schema
  allows ``exchange_ts=None`` so we pass it through rather than fabricating one.
* ``side`` / ``takerOrMaker`` can be absent; side falls back to
  :attr:`~crypcodile.schema.enums.Side.UNKNOWN`.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from crypcodile.instruments.registry import Instrument, InstrumentRegistry, Kind
from crypcodile.schema.enums import Side
from crypcodile.schema.records import (
    OHLCV,
    BookSnapshot,
    BookTicker,
    DerivativeTicker,
    Funding,
    Record,
    Trade,
)

# ccxt gives millisecond epoch timestamps; the crypcodile schema is nanoseconds.
_MS_TO_NS = 1_000_000


def _canonical(
    registry: InstrumentRegistry | None, exchange: str, symbol_raw: str
) -> str:
    """Canonical symbol for *symbol_raw* on *exchange*.

    Mirrors the native-connector convention: resolve through the registry when
    the instrument is known, otherwise fall back to ``"<exchange>:<raw>"`` so a
    record is still emitted for symbols the registry never loaded.
    """
    if registry is not None:
        inst = registry.get_raw(exchange, symbol_raw)
        if inst is not None:
            return inst.canonical
    return f"{exchange}:{symbol_raw}"


def _ms_to_ns(ts_ms: Any) -> int | None:
    """Convert a ccxt millisecond timestamp to nanoseconds (``None`` passes through)."""
    if ts_ms is None:
        return None
    try:
        return int(ts_ms) * _MS_TO_NS
    except (TypeError, ValueError):
        return None


def _f(value: Any) -> float | None:
    """Coerce to ``float`` or ``None`` (ccxt uses ``None`` for absent numerics)."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# markets -> instruments
# ---------------------------------------------------------------------------

def kind_from_market(market: dict[str, Any]) -> Kind:
    """Map a ccxt market's type flags to a Crypcodile :class:`Kind`.

    ccxt exposes mutually-exclusive booleans (``spot`` / ``swap`` / ``future`` /
    ``option``) plus a ``type`` string.  ``swap`` is ccxt's word for a perpetual
    contract, so it maps to :attr:`Kind.PERPETUAL`.
    """
    if market.get("option") or market.get("type") == "option":
        return Kind.OPTION
    if market.get("swap") or market.get("type") == "swap":
        return Kind.PERPETUAL
    if market.get("future") or market.get("type") == "future":
        return Kind.FUTURE
    return Kind.SPOT


def market_to_instrument(
    market: dict[str, Any], exchange: str
) -> Instrument | None:
    """Convert one ccxt unified market to an :class:`Instrument`.

    Returns ``None`` for markets with no unified ``symbol`` (malformed entries).
    ``symbol_raw`` is the ccxt **unified** symbol (e.g. ``"BTC/USDT"``) because
    that is exactly what the connector passes back to ccxt fetch methods, so it
    is the natural "raw" key for this venue.  The exchange-native id
    (``market['id']``) is not schema-representable and is intentionally dropped.
    """
    unified = market.get("symbol")
    if not unified:
        return None
    kind = kind_from_market(market)
    precision = market.get("precision") or {}
    contract_size = _f(market.get("contractSize"))
    return Instrument(
        canonical=f"{exchange}:{unified}",
        exchange=exchange,
        symbol_raw=unified,
        kind=kind,
        base=str(market.get("base") or ""),
        quote=str(market.get("quote") or ""),
        strike=_f(market.get("strike")),
        expiry=_ms_to_ns(market.get("expiry")),
        opt_type=_opt_type(market),
        tick_size=_f(precision.get("price")),
        contract_size=contract_size,
        settlement_currency=(market.get("settle") or None),
    )


def _opt_type(market: dict[str, Any]) -> str | None:
    """ccxt option markets carry ``optionType`` = ``"call"``/``"put"``."""
    ot = market.get("optionType")
    if ot == "call":
        return "C"
    if ot == "put":
        return "P"
    return None


# ---------------------------------------------------------------------------
# ticker -> BookTicker (+ DerivativeTicker for contracts)
# ---------------------------------------------------------------------------

def normalize_ticker(
    ticker: dict[str, Any],
    *,
    exchange: str,
    symbol_raw: str,
    local_ts: int,
    registry: InstrumentRegistry | None = None,
    is_contract: bool = False,
) -> Iterable[Record]:
    """Yield a :class:`BookTicker` (top-of-book) from a ccxt ticker.

    When *is_contract* is set and the ticker carries derivative fields
    (``markPrice`` / ``indexPrice`` / ``info.fundingRate``) a
    :class:`DerivativeTicker` is emitted alongside it.

    A ticker missing **both** ``bid`` and ``ask`` yields nothing — a book
    ticker with no quote is meaningless and would poison downstream mid-price
    math (``sqrt(bid*ask)``).
    """
    canonical = _canonical(registry, exchange, symbol_raw)
    exchange_ts = _ms_to_ns(ticker.get("timestamp"))
    bid = _f(ticker.get("bid"))
    ask = _f(ticker.get("ask"))

    if bid is not None and ask is not None:
        yield BookTicker(
            exchange=exchange,
            symbol=canonical,
            symbol_raw=symbol_raw,
            exchange_ts=exchange_ts,
            local_ts=local_ts,
            bid_px=bid,
            bid_sz=_f(ticker.get("bidVolume")) or 0.0,
            ask_px=ask,
            ask_sz=_f(ticker.get("askVolume")) or 0.0,
        )

    if is_contract:
        info = ticker.get("info") or {}
        mark = _f(ticker.get("markPrice")) or _f(info.get("markPrice"))
        index = _f(ticker.get("indexPrice")) or _f(info.get("indexPrice"))
        funding = _f(info.get("fundingRate"))
        if mark is not None or index is not None or funding is not None:
            yield DerivativeTicker(
                exchange=exchange,
                symbol=canonical,
                symbol_raw=symbol_raw,
                exchange_ts=exchange_ts,
                local_ts=local_ts,
                last_price=_f(ticker.get("last")),
                mark_price=mark,
                index_price=index,
                funding_rate=funding,
            )


# ---------------------------------------------------------------------------
# order book -> BookSnapshot
# ---------------------------------------------------------------------------

def _levels(raw_levels: Any, depth: int) -> list[tuple[float, float]]:
    """Take ``[price, amount, *rest]`` rows, keep the first two fields, cap to *depth*.

    Kraken (and a few others) append a per-level timestamp as a third element;
    slicing to ``[:2]`` keeps the transform exchange-agnostic.
    """
    out: list[tuple[float, float]] = []
    if not raw_levels:
        return out
    for lvl in raw_levels[:depth]:
        if not lvl or len(lvl) < 2:
            continue
        price = _f(lvl[0])
        amount = _f(lvl[1])
        if price is None or amount is None:
            continue
        out.append((price, amount))
    return out


def normalize_order_book(
    ob: dict[str, Any],
    *,
    exchange: str,
    symbol_raw: str,
    local_ts: int,
    depth: int = 50,
    registry: InstrumentRegistry | None = None,
) -> BookSnapshot:
    """Convert a ccxt order book to a :class:`BookSnapshot` (top *depth* levels).

    ccxt's REST ``fetch_order_book`` always returns a full snapshot (there is no
    unified incremental-diff REST call), so we always emit ``is_snapshot=True``.
    ``nonce`` becomes ``sequence_id`` when present.
    """
    canonical = _canonical(registry, exchange, symbol_raw)
    bids = _levels(ob.get("bids"), depth)
    asks = _levels(ob.get("asks"), depth)
    nonce = ob.get("nonce")
    return BookSnapshot(
        exchange=exchange,
        symbol=canonical,
        symbol_raw=symbol_raw,
        exchange_ts=_ms_to_ns(ob.get("timestamp")),
        local_ts=local_ts,
        bids=bids,
        asks=asks,
        depth=max(len(bids), len(asks)),
        sequence_id=int(nonce) if nonce is not None else None,
        is_snapshot=True,
    )


# ---------------------------------------------------------------------------
# trades -> Trade
# ---------------------------------------------------------------------------

def _side(raw: Any) -> Side:
    if raw == "buy":
        return Side.BUY
    if raw == "sell":
        return Side.SELL
    return Side.UNKNOWN


def normalize_trade(
    trade: dict[str, Any],
    *,
    exchange: str,
    symbol_raw: str,
    local_ts: int,
    registry: InstrumentRegistry | None = None,
) -> Trade | None:
    """Convert one ccxt trade to a :class:`Trade` (``None`` if price/amount absent)."""
    price = _f(trade.get("price"))
    amount = _f(trade.get("amount"))
    if price is None or amount is None:
        return None
    trade_id = trade.get("id")
    return Trade(
        exchange=exchange,
        symbol=_canonical(registry, exchange, symbol_raw),
        symbol_raw=symbol_raw,
        exchange_ts=_ms_to_ns(trade.get("timestamp")),
        local_ts=local_ts,
        id=str(trade_id) if trade_id is not None else "",
        price=price,
        amount=amount,
        side=_side(trade.get("side")),
    )


def normalize_trades(
    trades: Iterable[dict[str, Any]],
    *,
    exchange: str,
    symbol_raw: str,
    local_ts: int,
    registry: InstrumentRegistry | None = None,
) -> list[Trade]:
    """Normalize a batch of ccxt trades, dropping any that fail validation."""
    out: list[Trade] = []
    for t in trades:
        rec = normalize_trade(
            t,
            exchange=exchange,
            symbol_raw=symbol_raw,
            local_ts=local_ts,
            registry=registry,
        )
        if rec is not None:
            out.append(rec)
    return out


# ---------------------------------------------------------------------------
# OHLCV -> OHLCV
# ---------------------------------------------------------------------------

def normalize_ohlcv(
    candle: list[Any],
    *,
    interval: str,
    exchange: str,
    symbol_raw: str,
    local_ts: int,
    registry: InstrumentRegistry | None = None,
) -> OHLCV | None:
    """Convert one ccxt OHLCV row ``[ts, open, high, low, close, volume]``.

    Returns ``None`` for malformed rows (fewer than 6 fields).
    """
    if not candle or len(candle) < 6:
        return None
    o, h, low, c, v = (_f(candle[1]), _f(candle[2]), _f(candle[3]), _f(candle[4]), _f(candle[5]))
    if None in (o, h, low, c, v):
        return None
    return OHLCV(
        exchange=exchange,
        symbol=_canonical(registry, exchange, symbol_raw),
        symbol_raw=symbol_raw,
        exchange_ts=_ms_to_ns(candle[0]),
        local_ts=local_ts,
        interval=interval,
        open=o,  # type: ignore[arg-type]
        high=h,  # type: ignore[arg-type]
        low=low,  # type: ignore[arg-type]
        close=c,  # type: ignore[arg-type]
        volume=v,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# funding rate -> Funding
# ---------------------------------------------------------------------------

def normalize_funding(
    funding: dict[str, Any],
    *,
    exchange: str,
    symbol_raw: str,
    local_ts: int,
    registry: InstrumentRegistry | None = None,
) -> Funding | None:
    """Convert a ccxt funding-rate structure to a :class:`Funding` record.

    Returns ``None`` when no ``fundingRate`` is present (the schema requires a
    concrete rate; a funding record without one carries no signal).
    """
    rate = _f(funding.get("fundingRate"))
    if rate is None:
        return None
    return Funding(
        exchange=exchange,
        symbol=_canonical(registry, exchange, symbol_raw),
        symbol_raw=symbol_raw,
        exchange_ts=_ms_to_ns(funding.get("timestamp")),
        local_ts=local_ts,
        funding_rate=rate,
        funding_timestamp=_ms_to_ns(funding.get("fundingTimestamp")),
        predicted_funding_rate=_f(funding.get("nextFundingRate")),
    )
