"""Tests for Bybit connector wiring — pure functions only (Task 4.4)."""

from __future__ import annotations

import pathlib

from crocodile.exchanges.bybit.connector import build_channels, parse_instruments
from crocodile.instruments.registry import Kind

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def test_build_channels_linear() -> None:
    chans = build_channels(["BTCUSDT"], ["trade", "book_delta", "derivative_ticker"], "linear")
    assert "publicTrade.BTCUSDT" in chans
    assert "orderbook.50.BTCUSDT" in chans
    assert "tickers.BTCUSDT" in chans


def test_build_channels_option() -> None:
    chans = build_channels(["BTC-30JUN25-50000-C"], ["trade", "options_chain"], "option")
    assert "publicTrade.BTC-30JUN25-50000-C" in chans
    assert "tickers.BTC-30JUN25-50000-C" in chans


def test_build_channels_deduplicates_ticker() -> None:
    # derivative_ticker + funding both map to tickers.{sym} — only one entry
    chans = build_channels(["BTCUSDT"], ["derivative_ticker", "funding"], "linear")
    assert chans.count("tickers.BTCUSDT") == 1


def test_parse_instruments_linear_perp() -> None:
    raw = {
        "result": {
            "list": [
                {
                    "symbol": "BTCUSDT",
                    "contractType": "LinearPerpetual",
                    "baseCoin": "BTC",
                    "quoteCoin": "USDT",
                    "settleCoin": "USDT",
                    "tickSize": "0.1",
                    "lotSizeFilter": {"qtyStep": "0.001"},
                }
            ]
        }
    }
    insts = parse_instruments(raw, category="linear")
    assert len(insts) == 1
    inst = insts[0]
    assert inst.canonical == "bybit:BTCUSDT"
    assert inst.kind == Kind.PERPETUAL
    assert inst.base == "BTC"
    assert inst.quote == "USDT"
    assert inst.tick_size == 0.1
    assert inst.settlement_currency == "USDT"


def test_parse_instruments_option() -> None:
    raw = {
        "result": {
            "list": [
                {
                    "symbol": "BTC-30JUN25-50000-C",
                    "contractType": "Option",
                    "baseCoin": "BTC",
                    "quoteCoin": "USD",
                    "settleCoin": "BTC",
                    "optionsType": "Call",
                    "strikePrice": "50000",
                    "deliveryTime": "1751241600000",
                    "tickSize": "0.0005",
                    "lotSizeFilter": {"qtyStep": "0.01"},
                }
            ]
        }
    }
    insts = parse_instruments(raw, category="option")
    assert len(insts) == 1
    inst = insts[0]
    assert inst.canonical == "bybit:BTC-30JUN25-50000-C"
    assert inst.kind == Kind.OPTION
    assert inst.opt_type == "C"
    assert inst.strike == 50000.0
    # deliveryTime 1751241600000 ms → ns
    assert inst.expiry == 1751241600000 * 1_000_000
