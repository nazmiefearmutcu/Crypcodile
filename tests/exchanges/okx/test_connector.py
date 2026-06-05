"""Tests for OKX connector wiring — pure functions only (Task 4.5)."""

from __future__ import annotations

import json
import pathlib

from crocodile.exchanges.base import Connector
from crocodile.exchanges.okx.connector import OKXConnector, build_channels, parse_instruments
from crocodile.instruments.registry import InstrumentRegistry, Kind
from crocodile.sink.memory import MemorySink

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def test_build_channels_swap() -> None:
    chans = build_channels(["BTC-USDT-SWAP"], ["trade", "book_delta", "derivative_ticker"])
    # trade → "trades" channel
    assert any(c["channel"] == "trades" and c["instId"] == "BTC-USDT-SWAP" for c in chans)
    # book_delta → "books" channel
    assert any(c["channel"] == "books" and c["instId"] == "BTC-USDT-SWAP" for c in chans)
    # derivative_ticker → "tickers" channel
    assert any(c["channel"] == "tickers" and c["instId"] == "BTC-USDT-SWAP" for c in chans)


def test_build_channels_funding() -> None:
    chans = build_channels(["BTC-USDT-SWAP"], ["funding"])
    assert any(c["channel"] == "funding-rate" and c["instId"] == "BTC-USDT-SWAP" for c in chans)


def test_build_channels_deduplicates() -> None:
    # derivative_ticker + funding both map to different channels — both present
    chans = build_channels(["BTC-USDT-SWAP"], ["derivative_ticker", "funding"])
    channels_set = {c["channel"] for c in chans if c["instId"] == "BTC-USDT-SWAP"}
    assert "tickers" in channels_set
    assert "funding-rate" in channels_set


def test_build_channels_open_interest() -> None:
    chans = build_channels(["BTC-USDT-SWAP"], ["open_interest"])
    assert any(c["channel"] == "open-interest" and c["instId"] == "BTC-USDT-SWAP" for c in chans)


def test_parse_instruments_swap() -> None:
    raw = json.loads((FIXTURES / "rest_instruments.json").read_text())
    insts = parse_instruments(raw)
    swaps = [i for i in insts if i.symbol_raw == "BTC-USDT-SWAP"]
    assert len(swaps) == 1
    inst = swaps[0]
    assert inst.canonical == "okx:BTC-USDT-SWAP"
    assert inst.kind == Kind.PERPETUAL
    assert inst.base == "BTC"
    assert inst.quote == "USDT"
    assert inst.tick_size == 0.1


def test_parse_instruments_option() -> None:
    raw = json.loads((FIXTURES / "rest_instruments.json").read_text())
    insts = parse_instruments(raw)
    opts = [i for i in insts if i.symbol_raw == "BTC-USD-25DEC22-40000-C"]
    assert len(opts) == 1
    inst = opts[0]
    assert inst.canonical == "okx:BTC-USD-25DEC22-40000-C"
    assert inst.kind == Kind.OPTION
    assert inst.opt_type == "C"
    assert inst.strike == 40000.0
    # expTime 1700000000000 ms → ns
    assert inst.expiry == 1700000000000 * 1_000_000
    assert inst.tick_size == 0.0005


# ---------------------------------------------------------------------------
# Bug fix: subscribe_channels() return type must satisfy the ABC without suppression
# ---------------------------------------------------------------------------


def test_subscribe_channels_return_type_is_list_of_dicts() -> None:
    """OKXConnector.subscribe_channels() returns list[dict[str,str]].

    After removing the # type: ignore[override] suppression, the ABC must
    be widened to accept dict args. This test verifies the return value
    is actually a list of dicts at runtime.
    """
    sink = MemorySink()
    registry = InstrumentRegistry()
    conn = OKXConnector(
        symbols=["BTC-USDT-SWAP"],
        channels=["trade", "funding"],
        out=sink,
        registry=registry,
    )
    result = conn.subscribe_channels()
    assert isinstance(result, list)
    assert len(result) >= 1
    for item in result:
        assert isinstance(item, dict), f"Expected dict, got {type(item)}"
        assert "channel" in item
        assert "instId" in item


def test_subscribe_channels_is_callable_as_connector() -> None:
    """subscribe_channels() is callable on the base class reference (no override error)."""
    sink = MemorySink()
    registry = InstrumentRegistry()
    conn: Connector = OKXConnector(
        symbols=["BTC-USDT-SWAP"],
        channels=["trade"],
        out=sink,
        registry=registry,
    )
    # Calling via base class reference must work without type errors
    result = conn.subscribe_channels()
    assert result is not None


# ---------------------------------------------------------------------------
# parse_instruments — additional instType paths
# ---------------------------------------------------------------------------


def test_parse_instruments_futures_kind() -> None:
    """instType=FUTURES → Kind.FUTURE."""
    from crocodile.instruments.registry import Kind as K

    raw = {
        "data": [
            {
                "instType": "FUTURES",
                "instId": "BTC-USDT-20251231",
                "baseCcy": "BTC",
                "quoteCcy": "USDT",
                "settleCcy": "USDT",
                "tickSz": "0.1",
            }
        ]
    }
    insts = parse_instruments(raw)
    assert len(insts) == 1
    assert insts[0].kind == K.FUTURE


def test_parse_instruments_spot_kind() -> None:
    """instType=SPOT → Kind.SPOT."""
    raw = {
        "data": [
            {
                "instType": "SPOT",
                "instId": "BTC-USDT",
                "baseCcy": "BTC",
                "quoteCcy": "USDT",
                "settleCcy": "",
                "tickSz": "0.01",
            }
        ]
    }
    insts = parse_instruments(raw)
    from crocodile.instruments.registry import Kind as K
    assert len(insts) == 1
    assert insts[0].kind == K.SPOT


def test_parse_instruments_put_option() -> None:
    """optType='P' → opt_type='P'."""
    raw = {
        "data": [
            {
                "instType": "OPTION",
                "instId": "BTC-USD-25DEC22-40000-P",
                "baseCcy": "BTC",
                "quoteCcy": "USD",
                "settleCcy": "BTC",
                "stk": "40000",
                "expTime": "1700000000000",
                "optType": "P",
                "tickSz": "0.0005",
            }
        ]
    }
    insts = parse_instruments(raw)
    assert insts[0].opt_type == "P"


def test_parse_instruments_invalid_tick_size() -> None:
    """Non-numeric tickSz does not crash — tick_size stays None."""
    raw = {
        "data": [
            {
                "instType": "SWAP",
                "instId": "BTC-USDT-SWAP",
                "baseCcy": "BTC",
                "quoteCcy": "USDT",
                "settleCcy": "USDT",
                "tickSz": "N/A",
            }
        ]
    }
    insts = parse_instruments(raw)
    assert insts[0].tick_size is None


def test_parse_instruments_unknown_opt_type() -> None:
    """Unknown optType that is neither C/CALL nor P/PUT → opt_type=None."""
    raw = {
        "data": [
            {
                "instType": "OPTION",
                "instId": "BTC-USD-25DEC22-40000-X",
                "baseCcy": "BTC",
                "quoteCcy": "USD",
                "settleCcy": "BTC",
                "stk": "40000",
                "expTime": "1700000000000",
                "optType": "exotic",
                "tickSz": "0.0005",
            }
        ]
    }
    insts = parse_instruments(raw)
    assert insts[0].opt_type is None


# ---------------------------------------------------------------------------
# OKXConnector — region selection
# ---------------------------------------------------------------------------


def test_okx_connector_us_region_ws_url() -> None:
    """OKXConnector with region='us' uses the US WS endpoint."""
    sink = MemorySink()
    conn = OKXConnector(
        symbols=["BTC-USDT-SWAP"],
        channels=["trade"],
        out=sink,
        registry=InstrumentRegistry(),
        region="us",
    )
    assert "us.okx.com" in conn.ws_url


def test_okx_connector_eu_region_ws_url() -> None:
    """OKXConnector with region='eu' uses the EU WS endpoint."""
    sink = MemorySink()
    conn = OKXConnector(
        symbols=["BTC-USDT-SWAP"],
        channels=["trade"],
        out=sink,
        registry=InstrumentRegistry(),
        region="eu",
    )
    assert "eea.okx.com" in conn.ws_url


def test_okx_connector_normalize_non_dict_ignored() -> None:
    """Non-dict messages in normalize() produce no records."""
    sink = MemorySink()
    conn = OKXConnector(
        symbols=["BTC-USDT-SWAP"],
        channels=["trade"],
        out=sink,
        registry=InstrumentRegistry(),
    )
    result = list(conn.normalize("not a dict", local_ts=1))
    assert result == []


def test_build_channels_unknown_channel_skipped() -> None:
    """Unknown canonical channel names are silently skipped (no crash, not included)."""
    chans = build_channels(["BTC-USDT-SWAP"], ["nonexistent_channel"])
    assert chans == []
