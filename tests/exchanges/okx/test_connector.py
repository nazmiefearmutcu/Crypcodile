"""Tests for OKX connector wiring — pure functions only (Task 4.5)."""

from __future__ import annotations

import json
import pathlib

from crocodile.exchanges.okx.connector import build_channels, parse_instruments
from crocodile.instruments.registry import Kind

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
