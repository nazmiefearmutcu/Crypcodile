"""Tests for Coinbase connector wiring + edge-case normalization coverage (Task 4.6)."""

from __future__ import annotations

import pathlib

from crypcodile.exchanges.coinbase.connector import (
    CoinbaseConnector,
    build_channels,
    parse_products,
)
from crypcodile.exchanges.coinbase.normalize import _parse_iso_ns, normalize_message
from crypcodile.instruments.registry import InstrumentRegistry, Kind
from crypcodile.schema.enums import Side
from crypcodile.schema.records import BookTicker, Trade
from crypcodile.sink.memory import MemorySink

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# build_channels
# ---------------------------------------------------------------------------


def test_build_channels_deduplicates_level2() -> None:
    """book_delta + book_snapshot both map to level2 — only one entry."""
    chans = build_channels(["BTC-USD"], ["book_delta", "book_snapshot"])
    assert chans.count("level2") == 1


def test_build_channels_all_canonical_channels() -> None:
    chans = build_channels(
        ["BTC-USD"],
        ["trade", "book_delta", "book_snapshot", "book_ticker", "derivative_ticker"],
    )
    assert "matches" in chans
    assert "level2" in chans
    assert "ticker" in chans


def test_build_channels_empty_returns_empty() -> None:
    chans = build_channels(["BTC-USD"], [])
    assert chans == []


def test_build_channels_unknown_channel_ignored() -> None:
    chans = build_channels(["BTC-USD"], ["options_chain", "liquidation"])
    # options_chain and liquidation are not mapped for Coinbase (spot only)
    assert chans == []


# ---------------------------------------------------------------------------
# parse_products
# ---------------------------------------------------------------------------


def test_parse_products_spot_kind() -> None:
    raw = {
        "products": [
            {
                "product_id": "BTC-USD",
                "base_currency": "BTC",
                "quote_currency": "USD",
                "quote_increment": "0.01",
                "status": "online",
            }
        ]
    }
    insts = parse_products(raw)
    assert len(insts) == 1
    inst = insts[0]
    assert inst.canonical == "coinbase:BTC-USD"
    assert inst.kind == Kind.SPOT
    assert inst.base == "BTC"
    assert inst.quote == "USD"
    assert inst.tick_size == 0.01


def test_parse_products_invalid_quote_increment() -> None:
    """Non-numeric quote_increment should not crash — tick_size stays None."""
    raw = {
        "products": [
            {
                "product_id": "TEST-USD",
                "base_currency": "TEST",
                "quote_currency": "USD",
                "quote_increment": "N/A",
            }
        ]
    }
    insts = parse_products(raw)
    assert len(insts) == 1
    assert insts[0].tick_size is None


def test_parse_products_empty_list() -> None:
    insts = parse_products({"products": []})
    assert insts == []


# ---------------------------------------------------------------------------
# CoinbaseConnector — pure / non-network methods
# ---------------------------------------------------------------------------


def test_connector_subscribe_channels() -> None:
    sink = MemorySink()
    conn = CoinbaseConnector(
        symbols=["BTC-USD"],
        channels=["trade", "book_delta"],
        out=sink,
        registry=InstrumentRegistry(),
    )
    chans = conn.subscribe_channels()
    assert "matches" in chans
    assert "level2" in chans
    assert "ticker" in chans


def test_connector_normalize_delegates_to_normalize_message() -> None:
    sink = MemorySink()
    conn = CoinbaseConnector(
        symbols=["BTC-USD"],
        channels=["trade"],
        out=sink,
        registry=InstrumentRegistry(),
    )
    msg = {
        "type": "match",
        "trade_id": 1,
        "product_id": "BTC-USD",
        "size": "0.1",
        "price": "50000.0",
        "side": "buy",
        "time": "2023-11-14T22:13:20.000000Z",
    }
    out = list(conn.normalize(msg, local_ts=99))
    assert len(out) == 1
    assert isinstance(out[0], Trade)


def test_connector_normalize_non_dict_ignored() -> None:
    sink = MemorySink()
    conn = CoinbaseConnector(
        symbols=["BTC-USD"],
        channels=["trade"],
        out=sink,
        registry=InstrumentRegistry(),
    )
    out = list(conn.normalize("not a dict", local_ts=1))
    assert out == []


# ---------------------------------------------------------------------------
# normalize.py edge-case coverage
# ---------------------------------------------------------------------------


def test_parse_iso_ns_valid() -> None:
    ns = _parse_iso_ns("2023-11-14T22:13:20.000000Z")
    assert ns is not None
    assert ns >= 1_700_000_000_000_000_000


def test_parse_iso_ns_invalid_returns_none() -> None:
    """Invalid timestamp string → returns None without raising."""
    ns = _parse_iso_ns("not-a-timestamp")
    assert ns is None


def test_side_unknown_for_unrecognized_value() -> None:
    """Unrecognized side string → Side.UNKNOWN."""
    msg = {
        "type": "match",
        "trade_id": 1,
        "product_id": "BTC-USD",
        "size": "0.1",
        "price": "50000.0",
        "side": "neutral",  # not buy/sell
        "time": "2023-11-14T22:13:20.000000Z",
    }
    out = list(normalize_message(msg, local_ts=1))
    trades = [r for r in out if isinstance(r, Trade)]
    assert len(trades) == 1
    assert trades[0].side == Side.UNKNOWN


def test_last_match_is_treated_as_match() -> None:
    """``last_match`` type triggers Trade normalization (same as match)."""
    msg = {
        "type": "last_match",
        "trade_id": 99,
        "product_id": "ETH-USD",
        "size": "2.0",
        "price": "2000.0",
        "side": "sell",
        "time": "2023-11-14T22:13:20.000000Z",
    }
    out = list(normalize_message(msg, local_ts=5))
    trades = [r for r in out if isinstance(r, Trade)]
    assert len(trades) == 1
    assert trades[0].price == 2000.0


def test_ticker_missing_best_bid_emits_nothing() -> None:
    """Ticker without best_bid/best_ask → no BookTicker emitted."""
    msg = {
        "type": "ticker",
        "product_id": "BTC-USD",
        "price": "50000.0",
        # no best_bid / best_ask
    }
    out = list(normalize_message(msg, local_ts=1))
    bts = [r for r in out if isinstance(r, BookTicker)]
    assert bts == []


def test_unhandled_message_type_emits_nothing() -> None:
    """Unknown message type → no records emitted (logged at debug)."""
    msg = {"type": "heartbeat", "product_id": "BTC-USD"}
    out = list(normalize_message(msg, local_ts=1))
    assert out == []


def test_snapshot_no_time_exchange_ts_is_none() -> None:
    """Coinbase snapshots have no timestamp → exchange_ts=None."""
    from crypcodile.schema.records import BookSnapshot

    msg = {
        "type": "snapshot",
        "product_id": "BTC-USD",
        "bids": [["50000.0", "1.0"]],
        "asks": [["50001.0", "0.5"]],
    }
    out = list(normalize_message(msg, local_ts=1))
    snaps = [r for r in out if isinstance(r, BookSnapshot)]
    assert len(snaps) == 1
    assert snaps[0].exchange_ts is None


def test_normalize_with_registry() -> None:
    """Registry-based canonical symbol resolution."""
    from crypcodile.instruments.registry import Instrument

    reg = InstrumentRegistry()
    reg.add(
        Instrument(
            canonical="coinbase:BTC-USD",
            exchange="coinbase",
            symbol_raw="BTC-USD",
            kind=Kind.SPOT,
            base="BTC",
            quote="USD",
        )
    )
    msg = {
        "type": "match",
        "trade_id": 7,
        "product_id": "BTC-USD",
        "size": "0.5",
        "price": "50000.0",
        "side": "buy",
        "time": "2023-11-14T22:13:20.000000Z",
    }
    out = list(normalize_message(msg, local_ts=1, registry=reg))
    trades = [r for r in out if isinstance(r, Trade)]
    assert len(trades) == 1
    assert trades[0].symbol == "coinbase:BTC-USD"
