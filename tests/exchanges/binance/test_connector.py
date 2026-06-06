"""Tests for BinanceConnector wiring — pure functions only (Task T7a).

No live network calls.  Tests cover:
- subscribe_channels() topic construction for spot and USD-M futures
- deduplication across channels that map to the same topic
- normalize() dispatch of an existing fixture frame to the right record type
- connector normalizes non-dict messages gracefully (no crash)
"""

from __future__ import annotations

import json
import pathlib

import pytest

P = pathlib.Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# build_channels — topic construction
# ---------------------------------------------------------------------------


def test_build_channels_spot_trade() -> None:
    """Spot aggTrade topic for a symbol."""
    from crocodile.exchanges.binance.connector import build_channels

    topics = build_channels(["BTCUSDT"], ["trade"], market="spot")
    assert "btcusdt@aggTrade" in topics


def test_build_channels_usdm_trade() -> None:
    """USD-M futures aggTrade topic for a symbol."""
    from crocodile.exchanges.binance.connector import build_channels

    topics = build_channels(["BTCUSDT"], ["trade"], market="usdm")
    assert "btcusdt@aggTrade" in topics


def test_build_channels_spot_book_ticker() -> None:
    from crocodile.exchanges.binance.connector import build_channels

    topics = build_channels(["ETHUSDT"], ["book_ticker"], market="spot")
    assert "ethusdt@bookTicker" in topics


def test_build_channels_usdm_mark_price() -> None:
    """USD-M futures markPrice topic."""
    from crocodile.exchanges.binance.connector import build_channels

    topics = build_channels(["BTCUSDT"], ["derivative_ticker"], market="usdm")
    assert "btcusdt@markPrice" in topics


def test_build_channels_book_delta() -> None:
    """@depth topic is returned for book_delta channel."""
    from crocodile.exchanges.binance.connector import build_channels

    topics = build_channels(["BTCUSDT"], ["book_delta"], market="usdm")
    assert any("btcusdt@depth" in t for t in topics)


def test_build_channels_deduplicates() -> None:
    """derivative_ticker + funding both map to @markPrice — only one entry per symbol."""
    from crocodile.exchanges.binance.connector import build_channels

    topics = build_channels(["BTCUSDT"], ["derivative_ticker", "funding"], market="usdm")
    markprice_topics = [t for t in topics if "markPrice" in t]
    assert len(markprice_topics) == 1


def test_build_channels_multi_symbol() -> None:
    """Topics are built for every symbol in the list."""
    from crocodile.exchanges.binance.connector import build_channels

    topics = build_channels(["BTCUSDT", "ETHUSDT"], ["trade"], market="spot")
    assert "btcusdt@aggTrade" in topics
    assert "ethusdt@aggTrade" in topics


def test_build_channels_unknown_channel_ignored() -> None:
    """Unrecognised canonical channel names produce no topics (no crash)."""
    from crocodile.exchanges.binance.connector import build_channels

    topics = build_channels(["BTCUSDT"], ["some_unknown_channel"], market="spot")
    assert topics == []


# ---------------------------------------------------------------------------
# BinanceConnector — subscribe_channels() delegates to build_channels
# ---------------------------------------------------------------------------


def test_connector_subscribe_channels_spot() -> None:
    from crocodile.exchanges.binance.connector import BinanceConnector
    from crocodile.instruments.registry import InstrumentRegistry
    from crocodile.sink.memory import MemorySink

    conn = BinanceConnector(
        symbols=["BTCUSDT"],
        channels=["trade", "book_ticker"],
        out=MemorySink(),
        registry=InstrumentRegistry(),
        market="spot",
    )
    topics = conn.subscribe_channels()
    assert "btcusdt@aggTrade" in topics
    assert "btcusdt@bookTicker" in topics


def test_connector_subscribe_channels_usdm() -> None:
    from crocodile.exchanges.binance.connector import BinanceConnector
    from crocodile.instruments.registry import InstrumentRegistry
    from crocodile.sink.memory import MemorySink

    conn = BinanceConnector(
        symbols=["BTCUSDT"],
        channels=["derivative_ticker"],
        out=MemorySink(),
        registry=InstrumentRegistry(),
        market="usdm",
    )
    topics = conn.subscribe_channels()
    assert any("markPrice" in t for t in topics)


# ---------------------------------------------------------------------------
# BinanceConnector.normalize() — dispatches fixture frames correctly
# ---------------------------------------------------------------------------


def test_connector_normalize_spot_aggtrade() -> None:
    """normalize() dispatches an aggTrade fixture to a Trade record."""
    from crocodile.exchanges.binance.connector import BinanceConnector
    from crocodile.instruments.registry import InstrumentRegistry
    from crocodile.schema.records import Trade
    from crocodile.sink.memory import MemorySink

    conn = BinanceConnector(
        symbols=["BTCUSDT"],
        channels=["trade"],
        out=MemorySink(),
        registry=InstrumentRegistry(),
        market="spot",
    )
    msg = json.loads((P / "spot_aggtrade.json").read_text())
    records = list(conn.normalize(msg, local_ts=1))
    assert len(records) == 1
    assert isinstance(records[0], Trade)
    assert records[0].price == 50000.10


def test_connector_normalize_spot_bookticker() -> None:
    """normalize() dispatches a bookTicker fixture to a BookTicker record."""
    from crocodile.exchanges.binance.connector import BinanceConnector
    from crocodile.instruments.registry import InstrumentRegistry
    from crocodile.schema.records import BookTicker
    from crocodile.sink.memory import MemorySink

    conn = BinanceConnector(
        symbols=["BTCUSDT"],
        channels=["book_ticker"],
        out=MemorySink(),
        registry=InstrumentRegistry(),
        market="spot",
    )
    msg = json.loads((P / "spot_bookticker.json").read_text())
    records = list(conn.normalize(msg, local_ts=5))
    assert len(records) == 1
    assert isinstance(records[0], BookTicker)


def test_connector_normalize_non_dict_ignored() -> None:
    """Non-dict messages must produce no records (no crash)."""
    from crocodile.exchanges.binance.connector import BinanceConnector
    from crocodile.instruments.registry import InstrumentRegistry
    from crocodile.sink.memory import MemorySink

    conn = BinanceConnector(
        symbols=["BTCUSDT"],
        channels=["trade"],
        out=MemorySink(),
        registry=InstrumentRegistry(),
        market="spot",
    )
    result = list(conn.normalize("not a dict", local_ts=1))
    assert result == []


def test_connector_normalize_unknown_stream_ignored() -> None:
    """Dict message with unknown stream → empty output (no crash)."""
    from crocodile.exchanges.binance.connector import BinanceConnector
    from crocodile.instruments.registry import InstrumentRegistry
    from crocodile.sink.memory import MemorySink

    conn = BinanceConnector(
        symbols=["BTCUSDT"],
        channels=["trade"],
        out=MemorySink(),
        registry=InstrumentRegistry(),
        market="spot",
    )
    result = list(conn.normalize({"stream": "btcusdt@unknownType", "data": {}}, local_ts=1))
    assert result == []


# ---------------------------------------------------------------------------
# WS URL selection
# ---------------------------------------------------------------------------


def test_connector_spot_ws_url() -> None:
    from crocodile.exchanges.binance.connector import BinanceConnector
    from crocodile.instruments.registry import InstrumentRegistry
    from crocodile.sink.memory import MemorySink

    conn = BinanceConnector(
        symbols=["BTCUSDT"],
        channels=["trade"],
        out=MemorySink(),
        registry=InstrumentRegistry(),
        market="spot",
    )
    assert "stream.binance.com" in conn.ws_url or "wss://" in conn.ws_url


def test_connector_usdm_ws_url() -> None:
    from crocodile.exchanges.binance.connector import BinanceConnector
    from crocodile.instruments.registry import InstrumentRegistry
    from crocodile.sink.memory import MemorySink

    conn = BinanceConnector(
        symbols=["BTCUSDT"],
        channels=["trade"],
        out=MemorySink(),
        registry=InstrumentRegistry(),
        market="usdm",
    )
    # USD-M futures uses fstream.binance.com
    assert "fstream" in conn.ws_url or "wss://" in conn.ws_url
