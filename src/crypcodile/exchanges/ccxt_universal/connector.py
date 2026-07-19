"""Universal CCXT connector — poll-first, optional ccxt.pro WebSocket.

One :class:`CCXTConnector` class stands in for **every** exchange ccxt supports
(104 REST venues / 78 WebSocket venues as of ccxt 4.5).  It is parametrised by
``ccxt_id`` (e.g. ``"kraken"``, ``"kucoin"``, ``"mexc"``) and normalises ccxt's
unified market-data structures into the same :mod:`crypcodile.schema.records`
types the native connectors emit, so the Parquet lake, replay and analytics
layers treat a ccxt venue exactly like a hand-written one.

Design
------
* **Poll-first.**  ccxt's REST *unified* API (``fetch_trades`` /
  ``fetch_order_book`` / ``fetch_ticker`` / ``fetch_ohlcv`` /
  ``fetch_funding_rate``) works on essentially every venue, so the default
  ``run()`` is a supervised poll loop — the same shape as
  :class:`~crypcodile.exchanges.derive.connector.DerivePollConnector`.  It owns
  its loop and does not use ``self.transport``.
* **Optional WebSocket.**  When ``use_ws=True`` and the venue advertises the
  matching ``watch*`` capability, :meth:`run` drives ccxt.pro instead, one
  task per (symbol, channel).  WS coverage is uneven across venues, so poll
  stays the safe default.
* **Resilient cycles.**  A single bad symbol or a transient venue error never
  aborts a poll cycle; it is logged and skipped, and the supervised loop
  applies exponential backoff only on whole-cycle failure.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Iterable
from typing import Any

from crypcodile.exchanges.base import Connector, backoff_delays
from crypcodile.ingest.transport import Transport
from crypcodile.instruments.registry import Instrument, InstrumentRegistry
from crypcodile.schema.records import Record
from crypcodile.sink.base import Sink
from crypcodile.util.time import now_ns

from . import normalize as norm

log = logging.getLogger(__name__)

# Canonical channel -> the ccxt capability flag that must be truthy in
# ``exchange.has`` for that channel to be pollable.
_CHANNEL_REST_CAP: dict[str, str] = {
    "trade": "fetchTrades",
    "book_ticker": "fetchTicker",
    "book_snapshot": "fetchOrderBook",
    "book_delta": "fetchOrderBook",  # REST has no diff; emit snapshots
    "derivative_ticker": "fetchTicker",
    "funding": "fetchFundingRate",
    "ohlcv": "fetchOHLCV",
}

# Canonical channel -> the ccxt.pro ``watch*`` capability flag.
_CHANNEL_WS_CAP: dict[str, str] = {
    "trade": "watchTrades",
    "book_ticker": "watchTicker",
    "book_snapshot": "watchOrderBook",
    "book_delta": "watchOrderBook",
    "derivative_ticker": "watchTicker",
    "ohlcv": "watchOHLCV",
}


class CCXTConnector(Connector):
    """Poll-first ccxt connector for an arbitrary ``ccxt_id``.

    Parameters
    ----------
    ccxt_id:
        A ccxt exchange id (member of ``ccxt.exchanges``), e.g. ``"kraken"``.
    poll_interval:
        Seconds to sleep between poll cycles (default ``2.0``).  ccxt's own
        rate-limiter (``enableRateLimit=True``) additionally paces individual
        requests, so this is the *extra* gap between full cycles.
    book_depth:
        Client-side cap on order-book levels kept per snapshot (default ``50``).
        ``fetch_order_book`` is deliberately called **without** an exchange-side
        ``limit`` argument — several venues (e.g. KuCoin) reject arbitrary
        limits — and truncated here instead.
    ohlcv_interval:
        Timeframe string for the ``ohlcv`` channel (default ``"1m"``).
    use_ws:
        When ``True``, prefer ccxt.pro ``watch*`` streams where the venue
        supports them, falling back to polling per unsupported channel.
    exchange_config:
        Extra kwargs merged into the ccxt exchange constructor (e.g. API keys
        for private endpoints — not required for public market data).
    """

    # Poll/WS-managed: base-class ``run()`` (transport loop) is overridden, so
    # these exist only to satisfy the ABC and the ``collect`` wiring, which
    # constructs an unused ``AiohttpWsTransport(ws_url)`` for poll connectors.
    ws_url = ""
    rest_url = ""

    def __init__(
        self,
        symbols: list[str],
        channels: list[str],
        out: Sink,
        registry: InstrumentRegistry,
        *,
        ccxt_id: str,
        poll_interval: float = 2.0,
        book_depth: int = 50,
        ohlcv_interval: str = "1m",
        use_ws: bool = False,
        exchange_config: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(symbols=symbols, channels=channels, out=out, registry=registry)
        self.ccxt_id = ccxt_id
        self.name = ccxt_id
        self.poll_interval = poll_interval
        self.book_depth = book_depth
        self.ohlcv_interval = ohlcv_interval
        self.use_ws = use_ws
        self._exchange_config = exchange_config or {}
        # Poll/WS own their loop; no WS transport (mirrors DerivePollConnector).
        self.transport = None
        # Cache of resolved unified symbols after load_markets.
        self._resolved: dict[str, str] = {}
        self._contracts: set[str] = set()

    # ------------------------------------------------------------------
    # ABC requirements (records are produced in the poll/WS loop, not here)
    # ------------------------------------------------------------------

    def normalize(self, msg: object, local_ts: int) -> Iterable[Record]:
        """No-op: this connector emits records from :meth:`run`, not a WS frame."""
        return ()

    async def _subscribe(self, transport: Transport) -> None:
        """No-op — ccxt is driven by fetch/watch calls, not a subscribe frame."""

    def subscribe_channels(self) -> list[str]:
        return list(self.channels)

    # ------------------------------------------------------------------
    # ccxt exchange construction (lazy import so ccxt is only needed at use)
    # ------------------------------------------------------------------

    def _make_exchange(self, *, ws: bool) -> Any:
        cfg: dict[str, Any] = {"enableRateLimit": True}
        cfg.update(self._exchange_config)
        if ws:
            import ccxt.pro as ccxtpro

            return getattr(ccxtpro, self.ccxt_id)(cfg)
        import ccxt.async_support as ccxt_async

        return getattr(ccxt_async, self.ccxt_id)(cfg)

    # ------------------------------------------------------------------
    # instruments / universe
    # ------------------------------------------------------------------

    async def list_instruments(self) -> list[Instrument]:
        """Enumerate the venue's entire tradable universe via ``load_markets``."""
        ex = self._make_exchange(ws=False)
        try:
            markets = await ex.load_markets()
        finally:
            await ex.close()
        out: list[Instrument] = []
        for market in markets.values():
            inst = norm.market_to_instrument(market, self.ccxt_id)
            if inst is not None:
                out.append(inst)
        return out

    def _register_markets(self, ex: Any) -> None:
        """Populate the shared registry and resolve requested symbols to unified form.

        Also records which resolved symbols are contracts (swap/future/option)
        so the ticker path can additionally emit :class:`DerivativeTicker`.
        """
        markets = ex.markets or {}
        markets_by_id = getattr(ex, "markets_by_id", {}) or {}
        for market in markets.values():
            inst = norm.market_to_instrument(market, self.ccxt_id)
            if inst is not None:
                self.registry.add(inst)

        for requested in self.symbols:
            unified = self._resolve_symbol(requested, markets, markets_by_id)
            if unified is None:
                log.warning(
                    "%s: symbol %r not found on venue — skipping", self.ccxt_id, requested
                )
                continue
            self._resolved[requested] = unified
            m = markets.get(unified) or {}
            if m.get("contract") or m.get("swap") or m.get("future"):
                self._contracts.add(unified)

    @staticmethod
    def _resolve_symbol(
        requested: str,
        markets: dict[str, Any],
        markets_by_id: dict[str, Any],
    ) -> str | None:
        """Best-effort map a user symbol to a ccxt **unified** symbol.

        Accepts a unified symbol as-is (``"BTC/USDT"``), a raw exchange id
        (``"XBTUSD"`` → its unified symbol), or a delimiter-free spot pair
        (``"BTCUSDT"`` → ``"BTC/USDT"`` when such a market exists).  Returns
        ``None`` when nothing plausibly matches, so the caller can skip it.
        """
        if requested in markets:
            return requested
        up = requested.upper()
        if up in markets:
            return up
        # Raw exchange id → unified.
        entry = markets_by_id.get(requested) or markets_by_id.get(up)
        if entry:
            m = entry[0] if isinstance(entry, list) else entry
            sym = m.get("symbol") if isinstance(m, dict) else None
            if sym:
                return str(sym)
        # Delimiter-free pair → try to split against known quotes.
        if "/" not in up:
            for quote in ("USDT", "USDC", "USD", "BTC", "ETH", "EUR", "FDUSD", "TRY"):
                if up.endswith(quote):
                    candidate = f"{up[: -len(quote)]}/{quote}"
                    if candidate in markets:
                        return candidate
        return None

    # ------------------------------------------------------------------
    # run: poll loop (default) or ccxt.pro WS
    # ------------------------------------------------------------------

    async def run(self, max_reconnects: int = -1) -> None:
        """Supervised market-data loop.

        Uses ccxt.pro streaming when ``use_ws`` is set and the venue supports
        the WS capability for at least one requested channel; otherwise runs the
        REST poll loop.  Both honour *max_reconnects* (``-1`` = unlimited).
        """
        if self.use_ws:
            await self._run_ws(max_reconnects)
        else:
            await self._run_poll(max_reconnects)

    async def _run_poll(self, max_reconnects: int) -> None:
        ex = self._make_exchange(ws=False)
        attempt = 0
        try:
            await ex.load_markets()
            self._register_markets(ex)
            while True:
                try:
                    n = await self._poll_cycle(ex)
                    log.debug("%s: poll cycle wrote %d record(s)", self.ccxt_id, n)
                    attempt = 0
                    await asyncio.sleep(self.poll_interval)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # whole-cycle failure → backoff
                    log.warning(
                        "%s: poll cycle error (attempt %d): %s",
                        self.ccxt_id,
                        attempt,
                        exc,
                    )
                    if max_reconnects == 0 or (
                        max_reconnects > 0 and attempt >= max_reconnects
                    ):
                        raise
                    delay = backoff_delays(attempt, jitter=0.25, rand=random.random())
                    await asyncio.sleep(delay)
                    attempt += 1
        finally:
            await ex.close()
            if self._session is not None and not self._session.closed:
                await self._session.close()

    async def _poll_cycle(self, ex: Any) -> int:
        """One full pass over (symbol, channel).  Per-item errors are isolated."""
        written = 0
        for requested in self.symbols:
            unified = self._resolved.get(requested)
            if unified is None:
                continue
            for channel in self.channels:
                try:
                    written += await self._poll_one(ex, channel, unified)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    # A single symbol/channel hiccup must not sink the cycle.
                    log.debug(
                        "%s: %s %s poll skipped: %s",
                        self.ccxt_id,
                        unified,
                        channel,
                        exc,
                    )
        return written

    async def _poll_one(self, ex: Any, channel: str, unified: str) -> int:
        """Fetch + normalize + sink one (channel, symbol).  Returns record count."""
        cap = _CHANNEL_REST_CAP.get(channel)
        if cap is not None and not ex.has.get(cap):
            return 0
        local_ts = now_ns()
        records: list[Record] = []

        if channel == "trade":
            trades = await ex.fetch_trades(unified)
            records = list(
                norm.normalize_trades(
                    trades,
                    exchange=self.ccxt_id,
                    symbol_raw=unified,
                    local_ts=local_ts,
                    registry=self.registry,
                )
            )
        elif channel in ("book_snapshot", "book_delta"):
            ob = await ex.fetch_order_book(unified)
            records = [
                norm.normalize_order_book(
                    ob,
                    exchange=self.ccxt_id,
                    symbol_raw=unified,
                    local_ts=local_ts,
                    depth=self.book_depth,
                    registry=self.registry,
                )
            ]
        elif channel in ("book_ticker", "derivative_ticker"):
            ticker = await ex.fetch_ticker(unified)
            records = list(
                norm.normalize_ticker(
                    ticker,
                    exchange=self.ccxt_id,
                    symbol_raw=unified,
                    local_ts=local_ts,
                    registry=self.registry,
                    is_contract=(unified in self._contracts),
                )
            )
        elif channel == "funding":
            funding = await ex.fetch_funding_rate(unified)
            funding_rec = norm.normalize_funding(
                funding,
                exchange=self.ccxt_id,
                symbol_raw=unified,
                local_ts=local_ts,
                registry=self.registry,
            )
            records = [funding_rec] if funding_rec is not None else []
        elif channel == "ohlcv":
            candles = await ex.fetch_ohlcv(unified, timeframe=self.ohlcv_interval, limit=1)
            for candle in candles:
                ohlcv_rec = norm.normalize_ohlcv(
                    candle,
                    interval=self.ohlcv_interval,
                    exchange=self.ccxt_id,
                    symbol_raw=unified,
                    local_ts=local_ts,
                    registry=self.registry,
                )
                if ohlcv_rec is not None:
                    records.append(ohlcv_rec)
        else:
            return 0

        for rec in records:
            await self.out.put(rec)
        return len(records)

    # ------------------------------------------------------------------
    # WebSocket path (ccxt.pro) — opt-in
    # ------------------------------------------------------------------

    async def _run_ws(self, max_reconnects: int) -> None:
        """Stream over ccxt.pro, one socket per channel when the venue allows it.

        The scalability win: when a venue supports the ``*ForSymbols`` /
        ``watchTickers`` multi-symbol subscriptions, the ENTIRE requested symbol
        list rides a single WebSocket per channel instead of one task (and often
        one socket) per symbol — the difference between streaming three symbols
        and streaming a whole exchange's book.  Venues without the multi-symbol
        variant fall back to per-symbol ``watch*`` streams, and channels with no
        WS support at all fall back to polling.
        """
        ex = self._make_exchange(ws=True)
        try:
            await ex.load_markets()
            self._register_markets(ex)
            symbols = [
                self._resolved[s] for s in self.symbols if s in self._resolved
            ]
            if not symbols:
                return
            tasks: list[asyncio.Task[None]] = []
            for channel in self.channels:
                multi = self._multi_stream_for(ex, channel, symbols, max_reconnects)
                if multi is not None:
                    tasks.append(asyncio.create_task(multi))
                    continue
                # No multi-symbol stream for this channel → per-symbol streams.
                cap = _CHANNEL_WS_CAP.get(channel)
                for unified in symbols:
                    if cap is not None and ex.has.get(cap):
                        tasks.append(
                            asyncio.create_task(
                                self._ws_stream(ex, channel, unified, max_reconnects)
                            )
                        )
                    else:
                        tasks.append(
                            asyncio.create_task(
                                self._ws_poll_fallback(channel, unified)
                            )
                        )
            if not tasks:
                return
            await asyncio.gather(*tasks)
        finally:
            await ex.close()
            if self._session is not None and not self._session.closed:
                await self._session.close()

    def _multi_stream_for(
        self, ex: Any, channel: str, symbols: list[str], max_reconnects: int
    ) -> Any:
        """Return a single multi-symbol stream coroutine for *channel*, or ``None``.

        ``None`` means the venue has no whole-list subscription for this channel
        and the caller should fall back to per-symbol streams.
        """
        if channel == "trade" and ex.has.get("watchTradesForSymbols"):
            return self._ws_multi_trades(ex, symbols, max_reconnects)
        if channel in ("book_snapshot", "book_delta") and ex.has.get(
            "watchOrderBookForSymbols"
        ):
            return self._ws_multi_book(ex, symbols, max_reconnects)
        if channel in ("book_ticker", "derivative_ticker") and ex.has.get(
            "watchTickers"
        ):
            return self._ws_multi_tickers(ex, symbols, max_reconnects)
        return None

    async def _ws_retry_guard(
        self, attempt: int, max_reconnects: int, label: str, exc: Exception
    ) -> int:
        """Shared WS error handling: log, honour max_reconnects, backoff, bump attempt."""
        log.warning(
            "%s: ws %s error (attempt %d): %s", self.ccxt_id, label, attempt, exc
        )
        if max_reconnects == 0 or (max_reconnects > 0 and attempt >= max_reconnects):
            raise exc
        await asyncio.sleep(backoff_delays(attempt, jitter=0.25, rand=random.random()))
        return attempt + 1

    async def _ws_multi_trades(
        self, ex: Any, symbols: list[str], max_reconnects: int
    ) -> None:
        """One socket → trades for EVERY symbol (``watchTradesForSymbols``)."""
        attempt = 0
        while True:
            try:
                trades = await ex.watch_trades_for_symbols(symbols)
                local_ts = now_ns()
                for t in trades:
                    sym = t.get("symbol")
                    if not sym:
                        continue
                    rec = norm.normalize_trade(
                        t,
                        exchange=self.ccxt_id,
                        symbol_raw=sym,
                        local_ts=local_ts,
                        registry=self.registry,
                    )
                    if rec is not None:
                        await self.out.put(rec)
                attempt = 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                attempt = await self._ws_retry_guard(
                    attempt, max_reconnects, "trades-multi", exc
                )

    async def _ws_multi_book(
        self, ex: Any, symbols: list[str], max_reconnects: int
    ) -> None:
        """One socket → order books for EVERY symbol (``watchOrderBookForSymbols``).

        ccxt returns the single book that just updated (tagged with its symbol);
        we snapshot that one and loop.
        """
        attempt = 0
        while True:
            try:
                ob = await ex.watch_order_book_for_symbols(symbols)
                sym = ob.get("symbol")
                if sym:
                    snap = norm.normalize_order_book(
                        ob,
                        exchange=self.ccxt_id,
                        symbol_raw=sym,
                        local_ts=now_ns(),
                        depth=self.book_depth,
                        registry=self.registry,
                    )
                    await self.out.put(snap)
                attempt = 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                attempt = await self._ws_retry_guard(
                    attempt, max_reconnects, "book-multi", exc
                )

    async def _ws_multi_tickers(
        self, ex: Any, symbols: list[str], max_reconnects: int
    ) -> None:
        """One socket → tickers for EVERY symbol (``watchTickers``)."""
        attempt = 0
        while True:
            try:
                tickers = await ex.watch_tickers(symbols)
                local_ts = now_ns()
                for sym, ticker in tickers.items():
                    for rec in norm.normalize_ticker(
                        ticker,
                        exchange=self.ccxt_id,
                        symbol_raw=sym,
                        local_ts=local_ts,
                        registry=self.registry,
                        is_contract=(sym in self._contracts),
                    ):
                        await self.out.put(rec)
                attempt = 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                attempt = await self._ws_retry_guard(
                    attempt, max_reconnects, "tickers-multi", exc
                )

    async def _ws_stream(
        self, ex: Any, channel: str, unified: str, max_reconnects: int
    ) -> None:
        """Loop a single ccxt.pro ``watch*`` stream for one (symbol, channel)."""
        attempt = 0
        while True:
            try:
                local_ts = now_ns()
                records: list[Record] = []
                if channel == "trade":
                    trades = await ex.watch_trades(unified)
                    records = list(
                        norm.normalize_trades(
                            trades,
                            exchange=self.ccxt_id,
                            symbol_raw=unified,
                            local_ts=local_ts,
                            registry=self.registry,
                        )
                    )
                elif channel in ("book_snapshot", "book_delta"):
                    ob = await ex.watch_order_book(unified)
                    records = [
                        norm.normalize_order_book(
                            ob,
                            exchange=self.ccxt_id,
                            symbol_raw=unified,
                            local_ts=local_ts,
                            depth=self.book_depth,
                            registry=self.registry,
                        )
                    ]
                elif channel in ("book_ticker", "derivative_ticker"):
                    ticker = await ex.watch_ticker(unified)
                    records = list(
                        norm.normalize_ticker(
                            ticker,
                            exchange=self.ccxt_id,
                            symbol_raw=unified,
                            local_ts=local_ts,
                            registry=self.registry,
                            is_contract=(unified in self._contracts),
                        )
                    )
                elif channel == "ohlcv":
                    candles = await ex.watch_ohlcv(unified, timeframe=self.ohlcv_interval)
                    for candle in candles:
                        ohlcv_rec = norm.normalize_ohlcv(
                            candle,
                            interval=self.ohlcv_interval,
                            exchange=self.ccxt_id,
                            symbol_raw=unified,
                            local_ts=local_ts,
                            registry=self.registry,
                        )
                        if ohlcv_rec is not None:
                            records.append(ohlcv_rec)
                for rec in records:
                    await self.out.put(rec)
                attempt = 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning(
                    "%s: ws %s %s error (attempt %d): %s",
                    self.ccxt_id,
                    unified,
                    channel,
                    attempt,
                    exc,
                )
                if max_reconnects == 0 or (
                    max_reconnects > 0 and attempt >= max_reconnects
                ):
                    raise
                await asyncio.sleep(backoff_delays(attempt, jitter=0.25, rand=random.random()))
                attempt += 1

    async def _ws_poll_fallback(self, channel: str, unified: str) -> None:
        """Poll one (symbol, channel) the venue can't stream, in its own ex object."""
        ex = self._make_exchange(ws=False)
        try:
            await ex.load_markets()
            while True:
                try:
                    await self._poll_one(ex, channel, unified)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    log.debug("%s: ws-fallback poll %s %s: %s", self.ccxt_id, unified, channel, exc)
                await asyncio.sleep(self.poll_interval)
        finally:
            await ex.close()
