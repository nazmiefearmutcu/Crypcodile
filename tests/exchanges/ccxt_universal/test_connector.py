"""Connector-level tests for CCXTConnector using a fake ccxt exchange.

No network: a :class:`FakeExchange` stands in for a ccxt exchange object so the
poll cycle, symbol resolution, contract detection and per-item error isolation
are all exercised deterministically.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from crypcodile.exchanges.ccxt_universal.connector import CCXTConnector
from crypcodile.instruments.registry import InstrumentRegistry
from crypcodile.schema.records import BookSnapshot, BookTicker, DerivativeTicker, Trade


class CaptureSink:
    def __init__(self) -> None:
        self.records: list[Any] = []

    async def put(self, record: Any) -> None:
        self.records.append(record)

    async def flush(self) -> None: ...

    async def close(self) -> None: ...


class FakeExchange:
    """Minimal async stand-in for a ccxt exchange object."""

    def __init__(self, *, contract: bool = False, fail_trades: bool = False) -> None:
        self._contract = contract
        self._fail_trades = fail_trades
        spot = {
            "symbol": "BTC/USDT",
            "base": "BTC",
            "quote": "USDT",
            "type": "swap" if contract else "spot",
            "spot": not contract,
            "swap": contract,
            "future": False,
            "option": False,
            "contract": contract,
            "precision": {"price": 0.01},
        }
        self.markets = {"BTC/USDT": spot}
        self.markets_by_id = {"BTCUSDT": [spot]}
        self.has = {
            "fetchTrades": True,
            "fetchTicker": True,
            "fetchOrderBook": True,
            "fetchOHLCV": True,
            "fetchFundingRate": True,
            "fetchTickers": True,
        }
        self.closed = False

    async def load_markets(self) -> dict[str, Any]:
        return self.markets

    async def fetch_trades(self, symbol: str) -> list[dict[str, Any]]:
        if self._fail_trades:
            raise RuntimeError("boom")
        return [
            {"id": "1", "timestamp": 1, "side": "buy", "price": 100.0, "amount": 1.0},
            {"id": "2", "timestamp": 2, "side": "sell", "price": 101.0, "amount": 2.0},
        ]

    async def fetch_ticker(self, symbol: str) -> dict[str, Any]:
        t: dict[str, Any] = {
            "symbol": symbol,
            "last": 100.5,
            "bid": 100.0,
            "ask": 101.0,
            "bidVolume": 1.0,
            "askVolume": 2.0,
            "timestamp": 3,
        }
        if self._contract:
            t["info"] = {"markPrice": "100.4", "indexPrice": "100.3", "fundingRate": "0.0001"}
        return t

    async def fetch_order_book(self, symbol: str) -> dict[str, Any]:
        return {"bids": [[100.0, 1.0]], "asks": [[101.0, 2.0]], "nonce": 7, "timestamp": 4}

    async def close(self) -> None:
        self.closed = True


def _make(symbols, channels, *, contract=False, fail_trades=False):
    sink = CaptureSink()
    reg = InstrumentRegistry()
    conn = CCXTConnector(
        symbols=symbols, channels=channels, out=sink, registry=reg, ccxt_id="fake"
    )
    ex = FakeExchange(contract=contract, fail_trades=fail_trades)
    return conn, ex, sink, reg


# --------------------------------------------------------------------------- #
# symbol resolution
# --------------------------------------------------------------------------- #

def test_resolve_unified_symbol():
    markets = {"BTC/USDT": {"symbol": "BTC/USDT"}}
    assert CCXTConnector._resolve_symbol("BTC/USDT", markets, {}) == "BTC/USDT"


def test_resolve_raw_id_symbol():
    m = {"symbol": "BTC/USDT"}
    assert CCXTConnector._resolve_symbol("BTCUSDT", {"BTC/USDT": m}, {"BTCUSDT": [m]}) == "BTC/USDT"


def test_resolve_delimiterless_pair():
    markets = {"BTC/USDT": {"symbol": "BTC/USDT"}}
    assert CCXTConnector._resolve_symbol("btcusdt", markets, {}) == "BTC/USDT"


def test_resolve_unknown_returns_none():
    assert CCXTConnector._resolve_symbol("NOPE", {}, {}) is None


# --------------------------------------------------------------------------- #
# ABC no-ops
# --------------------------------------------------------------------------- #

def test_normalize_is_noop():
    conn, _ex, _sink, _reg = _make(["BTC/USDT"], ["trade"])
    assert list(conn.normalize({"anything": 1}, 0)) == []


async def test_subscribe_is_noop():
    conn, _ex, _sink, _reg = _make(["BTC/USDT"], ["trade"])
    await conn._subscribe(None)  # must not raise


# --------------------------------------------------------------------------- #
# poll cycle
# --------------------------------------------------------------------------- #

async def test_register_markets_populates_registry_and_resolves():
    conn, ex, _sink, reg = _make(["BTCUSDT"], ["trade"])
    await ex.load_markets()
    conn._register_markets(ex)
    assert conn._resolved == {"BTCUSDT": "BTC/USDT"}
    assert reg.get_raw("fake", "BTC/USDT") is not None


async def test_poll_cycle_emits_all_channels():
    conn, ex, sink, _reg = _make(
        ["BTC/USDT"], ["trade", "book_ticker", "book_snapshot"]
    )
    await ex.load_markets()
    conn._register_markets(ex)
    n = await conn._poll_cycle(ex)
    assert n >= 4  # 2 trades + 1 ticker + 1 book
    kinds = {type(r) for r in sink.records}
    assert Trade in kinds and BookTicker in kinds and BookSnapshot in kinds
    # every record is tagged with the ccxt id and canonical symbol
    assert all(r.exchange == "fake" for r in sink.records)
    assert all(r.symbol == "fake:BTC/USDT" for r in sink.records)


async def test_contract_ticker_emits_derivative_ticker():
    conn, ex, sink, _reg = _make(["BTC/USDT"], ["book_ticker"], contract=True)
    await ex.load_markets()
    conn._register_markets(ex)
    await conn._poll_cycle(ex)
    assert any(isinstance(r, DerivativeTicker) for r in sink.records)


async def test_poll_cycle_isolates_failing_channel():
    # fetch_trades raises, but book_ticker must still produce a record.
    conn, ex, sink, _reg = _make(["BTC/USDT"], ["trade", "book_ticker"], fail_trades=True)
    await ex.load_markets()
    conn._register_markets(ex)
    n = await conn._poll_cycle(ex)  # must not raise
    assert n >= 1
    assert any(isinstance(r, BookTicker) for r in sink.records)
    assert not any(isinstance(r, Trade) for r in sink.records)


async def test_unsupported_capability_skips_channel():
    conn, ex, sink, _reg = _make(["BTC/USDT"], ["funding"])
    ex.has["fetchFundingRate"] = False
    await ex.load_markets()
    conn._register_markets(ex)
    n = await conn._poll_cycle(ex)
    assert n == 0
    assert sink.records == []


async def test_list_instruments_maps_markets(monkeypatch):
    conn, ex, _sink, _reg = _make([], [])
    monkeypatch.setattr(conn, "_make_exchange", lambda *, ws: ex)
    insts = await conn.list_instruments()
    assert len(insts) == 1
    assert insts[0].canonical == "fake:BTC/USDT"
    assert ex.closed is True  # exchange is always closed


def test_transport_is_none_and_urls_empty():
    # Mirrors DerivePollConnector: poll owns the loop; collect's unused
    # AiohttpWsTransport(ws_url) construction must be harmless.
    conn, _ex, _sink, _reg = _make(["BTC/USDT"], ["trade"])
    assert conn.transport is None
    assert conn.ws_url == ""
    assert conn.name == "fake"


# --------------------------------------------------------------------------- #
# multi-symbol WebSocket (the scalability path)
# --------------------------------------------------------------------------- #

class FakeWSExchange:
    """ccxt.pro stand-in whose ``*ForSymbols`` streams yield one batch then stop."""

    def __init__(self, **caps: bool) -> None:
        self.has = {
            "watchTradesForSymbols": True,
            "watchOrderBookForSymbols": True,
            "watchTickers": True,
            **caps,
        }
        self._trade_calls = 0

    async def watch_trades_for_symbols(self, symbols: list[str]) -> list[dict[str, Any]]:
        self._trade_calls += 1
        if self._trade_calls == 1:
            return [
                {"id": "1", "symbol": "BTC/USDT", "timestamp": 1, "side": "buy",
                 "price": 100.0, "amount": 1.0},
                {"id": "2", "symbol": "ETH/USDT", "timestamp": 2, "side": "sell",
                 "price": 50.0, "amount": 2.0},
            ]
        raise asyncio.CancelledError  # break the infinite loop


def test_multi_stream_routing_follows_capabilities():
    conn, _ex, _sink, _reg = _make(["BTC/USDT"], ["trade"])
    ex = FakeWSExchange()
    coro = conn._multi_stream_for(ex, "trade", ["BTC/USDT"], -1)
    assert coro is not None
    coro.close()  # never awaited — close to avoid a warning

    ex.has["watchTradesForSymbols"] = False
    assert conn._multi_stream_for(ex, "trade", ["BTC/USDT"], -1) is None


async def test_ws_multi_trades_streams_every_symbol_on_one_socket():
    conn, _ex, sink, _reg = _make(["BTC/USDT", "ETH/USDT"], ["trade"])
    ws = FakeWSExchange()
    with pytest.raises(asyncio.CancelledError):
        await conn._ws_multi_trades(ws, ["BTC/USDT", "ETH/USDT"], max_reconnects=-1)
    # both symbols were normalized from the single multi-symbol batch
    assert {r.symbol_raw for r in sink.records} == {"BTC/USDT", "ETH/USDT"}
    assert all(isinstance(r, Trade) for r in sink.records)
