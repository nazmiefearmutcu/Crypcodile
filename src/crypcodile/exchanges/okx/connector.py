"""OKX V5 connector — wiring (REST instruments + WS subscribe build).

Appendix §7:
- WS public endpoint: ``wss://ws.okx.com:8443/ws/v5/public``
  Region overrides: ``wss://ws.us.okx.com:8443/ws/v5/public`` (US/AU),
  ``wss://ws.eea.okx.com:8443/ws/v5/public`` (EU).
- Subscribe format: ``{"op":"subscribe","args":[{"channel":"trades","instId":"BTC-USDT"}]}``
  (each arg is a dict, not a plain string — differs from Bybit/Binance).
- REST: ``https://openapi.okx.com/api/v5`` (region: ``us.okx.com``,
  ``eea.okx.com``).
- Instrument endpoint: ``/public/instruments?instType={SPOT|MARGIN|SWAP|FUTURES|OPTION}``
  Response: ``{"code":"0","data":[...]}``; fields: ``instType``, ``instId``,
  ``baseCcy``, ``quoteCcy``, ``settleCcy``, ``stk`` (strike, option only),
  ``expTime`` (expiry ms, option only), ``optType`` (``C``/``P``), ``tickSz``.

Book path (sequence-gap resync)
-------------------------------
When ``book_delta`` / ``book_snapshot`` channels are subscribed, the ``books``
stream is gated through :class:`~crypcodile.exchanges.okx.book.OkxOrderBookSync`
and :class:`~crypcodile.ingest.gap_bridge.BookResyncBridge`:

1. First ``action=snapshot`` → seed the sync machine from the WS snapshot
   (no REST).  If the first books push is an ``update`` (rare), REST
   ``GET /market/books`` bootstraps instead.
2. Continuous ``action=update`` events → APPLY / DROP via ``OkxOrderBookSync``
   using ``prevSeqId`` / ``seqId`` continuity.
3. Sequence gap (``SyncResult.RESYNC``) → buffer live deltas, re-fetch REST
   books snapshot, emit snapshot + post-snapshot buffered deltas.

Bridges are registered only after a successful bootstrap with a usable
``sequence_id`` / ``seqId`` so a failed fetch does not permanently DROP the
symbol (same fix as Binance).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from typing import Any

from crypcodile.exchanges.base import Connector
from crypcodile.exchanges.okx.book import OkxOrderBookSync, parse_rest_books_snapshot
from crypcodile.ingest.gap_bridge import BookResyncBridge
from crypcodile.ingest.transport import Transport
from crypcodile.instruments.registry import Instrument, InstrumentRegistry, Kind
from crypcodile.schema.records import BookDelta, BookSnapshot, Record
from crypcodile.sink.base import Sink
from crypcodile.util.time import ms_to_ns, now_ns

from .normalize import normalize_message

log = logging.getLogger(__name__)

EXCHANGE = "okx"
REST_BASE = "https://openapi.okx.com/api/v5"

# Region-specific base URLs (appendix §7 critical quirk)
_REGION_WS_URL: dict[str, str] = {
    "global": "wss://ws.okx.com:8443/ws/v5/public",
    "us": "wss://ws.us.okx.com:8443/ws/v5/public",
    "eu": "wss://ws.eea.okx.com:8443/ws/v5/public",
}
_REGION_REST_URL: dict[str, str] = {
    "global": "https://openapi.okx.com/api/v5",
    "us": "https://us.okx.com/api/v5",
    "eu": "https://eea.okx.com/api/v5",
}

# Mapping from canonical channel names to OKX channel strings.
# OKX uses a dict-per-arg subscribe format: {"channel": "...", "instId": "..."}.
_CHANNEL_MAP: dict[str, str] = {
    "trade": "trades",
    "book_delta": "books",
    "book_snapshot": "books",
    "derivative_ticker": "tickers",
    "funding": "funding-rate",
    "open_interest": "open-interest",
    "liquidation": "liq-orders",
    "options_chain": "option-summary",
    "book_ticker": "bbo-tbt",
}


def build_channels(symbols: list[str], channels: list[str]) -> list[dict[str, str]]:
    """Return OKX subscribe arg dicts for the given symbols and channel kinds.

    Each element is ``{"channel": "<okx_channel>", "instId": "<sym>"}`` as
    required by the OKX V5 subscribe frame.  Deduplicates entries where
    multiple canonical channels map to the same OKX channel for a given symbol.

    Returns a list of arg dicts (not topic strings like Bybit/Binance) because
    OKX subscribe format differs: ``{"op":"subscribe","args":[{"channel":...,"instId":...}]}``.
    """
    seen: set[tuple[str, str]] = set()
    result: list[dict[str, str]] = []
    for sym in symbols:
        for ch in channels:
            okx_ch = _CHANNEL_MAP.get(ch)
            if okx_ch is None:
                continue
            key = (okx_ch, sym)
            if key not in seen:
                seen.add(key)
                result.append({"channel": okx_ch, "instId": sym})
    return result


def parse_instruments(raw: dict[str, Any]) -> list[Instrument]:
    """Parse the JSON response from OKX ``/public/instruments``.

    Response shape: ``{"code":"0","data":[...instrument dicts...]}``

    Supports SWAP (perpetuals), FUTURES, SPOT, and OPTION instrument types.
    """
    out: list[Instrument] = []
    items: list[dict[str, Any]] = raw.get("data") or []

    for item in items:
        inst_type: str = item.get("instType", "")
        sym: str = item["instId"]
        base: str = item.get("baseCcy", "")
        quote: str = item.get("quoteCcy", "")
        settle: str | None = item.get("settleCcy") or None

        # Determine kind from instType
        inst_type_upper = inst_type.upper()
        if inst_type_upper == "SWAP":
            kind = Kind.PERPETUAL
        elif inst_type_upper == "FUTURES":
            kind = Kind.FUTURE
        elif inst_type_upper == "OPTION":
            kind = Kind.OPTION
        else:
            kind = Kind.SPOT

        # Option-specific fields
        strike: float | None = None
        expiry: int | None = None
        opt_type: str | None = None

        if kind == Kind.OPTION:
            stk_raw = item.get("stk")
            if stk_raw:
                try:
                    strike = float(stk_raw)
                except (ValueError, TypeError):
                    pass

            exp_raw = item.get("expTime")
            if exp_raw:
                try:
                    expiry = ms_to_ns(int(exp_raw))
                except (ValueError, TypeError):
                    pass

            raw_opt_type = item.get("optType", "")
            if raw_opt_type.upper() in ("C", "CALL"):
                opt_type = "C"
            elif raw_opt_type.upper() in ("P", "PUT"):
                opt_type = "P"

        # tick_size from tickSz
        tick_size: float | None = None
        ts_raw = item.get("tickSz")
        if ts_raw:
            try:
                tick_size = float(ts_raw)
            except (ValueError, TypeError):
                pass

        out.append(
            Instrument(
                canonical=f"{EXCHANGE}:{sym}",
                exchange=EXCHANGE,
                symbol_raw=sym,
                kind=kind,
                base=base,
                quote=quote,
                strike=strike,
                expiry=expiry,
                opt_type=opt_type,
                tick_size=tick_size,
                settlement_currency=settle,
            )
        )
    return out


class OKXConnector(Connector):
    """OKX V5 WebSocket connector.

    ``region`` selects the public WS and REST endpoints:
    ``global`` (default) | ``us`` | ``eu``.

    OKX subscribe format uses dict args:
    ``{"op":"subscribe","args":[{"channel":"trades","instId":"BTC-USDT"}]}``.
    """

    name = EXCHANGE
    rest_url = REST_BASE

    def __init__(
        self,
        symbols: list[str],
        channels: list[str],
        out: Sink,
        registry: InstrumentRegistry,
        region: str = "global",
    ) -> None:
        super().__init__(symbols=symbols, channels=channels, out=out, registry=registry)
        self.region = region
        self.ws_url = _REGION_WS_URL.get(region, _REGION_WS_URL["global"])
        self._rest_base = _REGION_REST_URL.get(region, _REGION_REST_URL["global"])
        self._sub_args = build_channels(symbols, channels)
        # Per-symbol book sync + resync bridge (lazy, only for book channels).
        self._book_syncs: dict[str, OkxOrderBookSync] = {}
        self._book_bridges: dict[str, BookResyncBridge] = {}
        # REST books depth per side (OKX max 400).
        self.book_depth_sz: int = 400

    def normalize(self, msg: object, local_ts: int) -> Iterable[Record]:
        if isinstance(msg, dict):
            yield from normalize_message(msg, local_ts=local_ts, venue=self.name,
                                         registry=self.registry)

    async def list_instruments(self) -> list[Instrument]:  # pragma: no cover
        """Fetch instruments from OKX REST API.

        Fetches SWAP, FUTURES, SPOT, and OPTION instrument types and merges
        them into a single list.  Each type requires a separate REST call.
        """
        instruments: list[Instrument] = []
        inst_types = ["SWAP", "FUTURES", "SPOT", "OPTION"]
        for inst_type in inst_types:
            url = f"{self._rest_base}/public/instruments"
            params: dict[str, str] = {"instType": inst_type}
            try:
                data = await self.http_get(url, params=params)
                instruments.extend(parse_instruments(data))
            except Exception as exc:
                log.warning("OKX: failed to fetch %s instruments: %s", inst_type, exc)
        return instruments

    def subscribe_channels(self) -> list[dict[str, str]]:
        """Return the list of OKX subscribe arg dicts."""
        return self._sub_args

    # ------------------------------------------------------------------
    # Book resync integration (BookResyncBridge)
    # ------------------------------------------------------------------

    def _wants_book_sync(self) -> bool:
        """True when book channels are requested (enable gap → REST resync)."""
        return any(c in ("book_delta", "book_snapshot") for c in self.channels)

    @staticmethod
    def _is_books_message(msg: dict[str, Any]) -> bool:
        """True for incremental ``books`` pushes (not bbo-tbt / books5)."""
        arg = msg.get("arg")
        if not isinstance(arg, dict):
            return False
        if arg.get("channel") != "books":
            return False
        return isinstance(msg.get("data"), list)

    async def fetch_book_snapshot(self, symbol: str) -> BookSnapshot:
        """Fetch a REST order-book snapshot for *symbol* (raw ``instId``).

        Used as the ``fetch_snapshot`` callback for
        :class:`~crypcodile.ingest.gap_bridge.BookResyncBridge`.  Tests may
        monkeypatch this method to avoid network I/O.
        """
        url = f"{self._rest_base}/market/books"
        data = await self.http_get(
            url,
            params={"instId": symbol, "sz": str(self.book_depth_sz)},
        )
        return parse_rest_books_snapshot(
            data,
            symbol_raw=symbol,
            venue=self.name,
            local_ts=now_ns(),
            registry=self.registry,
        )

    async def _ensure_book_bridge(
        self,
        symbol_raw: str,
        *,
        ws_snapshot: BookSnapshot | None = None,
    ) -> tuple[OkxOrderBookSync, BookResyncBridge]:
        """Return (create if needed) OkxOrderBookSync + BookResyncBridge.

        Bootstrap preference:
        1. Seed from the concurrent WS ``action=snapshot`` when provided.
        2. Otherwise REST-fetch ``/market/books``.

        Bridges are registered only after a successful bootstrap with a usable
        ``sequence_id`` so a failed fetch does not permanently DROP the symbol.
        """
        key = symbol_raw
        if key not in self._book_bridges:
            sync = OkxOrderBookSync()
            bridge = BookResyncBridge(
                sync=sync,
                fetch_snapshot=self.fetch_book_snapshot,
                symbol=key,
            )

            if ws_snapshot is not None:
                snap = ws_snapshot
            else:
                snap = await self.fetch_book_snapshot(key)

            if snap.sequence_id is None:
                raise ValueError(
                    f"OKX book bootstrap [{key}]: snapshot missing sequence_id"
                )
            sync.set_snapshot(last_update_id=snap.sequence_id)
            await self.out.put(snap)
            self._book_syncs[key] = sync
            self._book_bridges[key] = bridge
            log.info(
                "OKX book bootstrap [%s]: snapshot seq=%s (source=%s)",
                key,
                snap.sequence_id,
                "ws" if ws_snapshot is not None else "rest",
            )
        return self._book_syncs[key], self._book_bridges[key]

    async def _handle_books_message(self, msg: dict[str, Any], local_ts: int) -> None:
        """Normalize one books push, gate updates via OkxOrderBookSync + bridge."""
        arg: dict[str, Any] = msg.get("arg") or {}
        symbol_raw: str = arg.get("instId", "")
        action: str = msg.get("action", "")

        # Pre-normalize so we can seed from a WS snapshot without REST.
        records = list(
            normalize_message(
                msg,
                local_ts=local_ts,
                venue=self.name,
                registry=self.registry,
            )
        )

        ws_snap: BookSnapshot | None = None
        if action == "snapshot":
            for rec in records:
                if isinstance(rec, BookSnapshot) and rec.sequence_id is not None:
                    ws_snap = rec
                    break

        # Mid-stream WS re-snapshot: re-anchor an existing bridge.
        if symbol_raw in self._book_bridges and ws_snap is not None:
            assert ws_snap.sequence_id is not None
            self._book_syncs[symbol_raw].set_snapshot(
                last_update_id=ws_snap.sequence_id
            )
            await self.out.put(ws_snap)
            log.info(
                "OKX book re-snapshot [%s]: seq=%s",
                symbol_raw,
                ws_snap.sequence_id,
            )
            return

        sync, bridge = await self._ensure_book_bridge(
            symbol_raw, ws_snapshot=ws_snap
        )

        # Bootstrap already emitted the WS snapshot; skip re-emitting it.
        for rec in records:
            if isinstance(rec, BookSnapshot):
                # Already emitted during bootstrap (or mid-stream handled above).
                continue
            if not isinstance(rec, BookDelta):
                await self.out.put(rec)
                continue

            result = sync.feed(seq_id=rec.seq_id, prev_seq_id=rec.prev_seq_id)
            emit = bridge.feed_sync_result(result, rec)
            if emit is not None:
                await self.out.put(emit)

            if bridge.is_resyncing:
                applied = await bridge.complete_resync()
                for r in applied:
                    await self.out.put(r)

    async def _handle_message(self, msg: object, local_ts: int) -> None:
        """Route books updates through BookResyncBridge; all else default path.

        Integration point documented on
        :meth:`crypcodile.exchanges.base.Connector._handle_message`.
        """
        if (
            isinstance(msg, dict)
            and self._wants_book_sync()
            and self._is_books_message(msg)
        ):
            await self._handle_books_message(msg, local_ts)
            return
        await super()._handle_message(msg, local_ts)

    async def _subscribe(self, transport: Transport) -> None:  # pragma: no cover
        """Send an OKX V5 subscribe frame."""
        # Drop book state on (re)subscribe so a reconnect re-bootstraps.
        self._book_syncs.clear()
        self._book_bridges.clear()
        args = self.subscribe_channels()
        if args:
            frame = json.dumps({"op": "subscribe", "args": args}).encode()
            await transport.send(frame)
