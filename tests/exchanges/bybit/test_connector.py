"""Tests for Bybit connector wiring — pure functions only (Task 4.4)."""

from __future__ import annotations

import pathlib

from crypcodile.exchanges.bybit.connector import build_channels, parse_instruments
from crypcodile.instruments.registry import Kind

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


def test_parse_instruments_future_kind() -> None:
    """LinearFuture contractType → Kind.FUTURE."""
    raw = {
        "result": {
            "list": [
                {
                    "symbol": "BTCUSDT-31DEC25",
                    "contractType": "LinearFuture",
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
    from crypcodile.instruments.registry import Kind
    assert insts[0].kind == Kind.FUTURE


def test_parse_instruments_spot_kind() -> None:
    """category='spot' with no contractType → Kind.SPOT."""
    raw = {
        "result": {
            "list": [
                {
                    "symbol": "BTCUSDT",
                    "contractType": "",
                    "baseCoin": "BTC",
                    "quoteCoin": "USDT",
                    "settleCoin": None,
                    "tickSize": "0.01",
                    "lotSizeFilter": {"qtyStep": "0.001"},
                }
            ]
        }
    }
    insts = parse_instruments(raw, category="spot")
    assert len(insts) == 1
    from crypcodile.instruments.registry import Kind
    assert insts[0].kind == Kind.SPOT


def test_parse_instruments_pricefilter_tick_size() -> None:
    """priceFilter.tickSize takes priority over top-level tickSize."""
    raw = {
        "result": {
            "list": [
                {
                    "symbol": "BTCUSDT",
                    "contractType": "LinearPerpetual",
                    "baseCoin": "BTC",
                    "quoteCoin": "USDT",
                    "settleCoin": "USDT",
                    "priceFilter": {"tickSize": "0.5"},
                    "lotSizeFilter": {"qtyStep": "0.001"},
                }
            ]
        }
    }
    insts = parse_instruments(raw, category="linear")
    assert insts[0].tick_size == 0.5


def test_parse_instruments_put_option() -> None:
    """optionsType='Put' → opt_type='P'."""
    raw = {
        "result": {
            "list": [
                {
                    "symbol": "BTC-30JUN25-50000-P",
                    "contractType": "Option",
                    "baseCoin": "BTC",
                    "quoteCoin": "USD",
                    "settleCoin": "BTC",
                    "optionsType": "Put",
                    "strikePrice": "50000",
                    "deliveryTime": "1751241600000",
                    "tickSize": "0.0005",
                    "lotSizeFilter": {"qtyStep": "0.01"},
                }
            ]
        }
    }
    insts = parse_instruments(raw, category="option")
    assert insts[0].opt_type == "P"


def test_parse_instruments_option_unknown_type() -> None:
    """Unknown optionsType → opt_type=None (no crash)."""
    raw = {
        "result": {
            "list": [
                {
                    "symbol": "BTC-30JUN25-50000-X",
                    "contractType": "Option",
                    "baseCoin": "BTC",
                    "quoteCoin": "USD",
                    "settleCoin": "BTC",
                    "optionsType": "Exotic",
                    "strikePrice": "50000",
                    "deliveryTime": "1751241600000",
                    "tickSize": "0.0005",
                    "lotSizeFilter": {"qtyStep": "0.01"},
                }
            ]
        }
    }
    insts = parse_instruments(raw, category="option")
    assert insts[0].opt_type is None


def test_parse_instruments_no_delivery_time() -> None:
    """Missing deliveryTime → expiry=None (no crash)."""
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
                    "tickSize": "0.0005",
                    "lotSizeFilter": {"qtyStep": "0.01"},
                }
            ]
        }
    }
    insts = parse_instruments(raw, category="option")
    assert insts[0].expiry is None


def test_build_channels_spot_category() -> None:
    """Spot category builds the same topic patterns (no separate WS for spot)."""
    chans = build_channels(["BTCUSDT"], ["trade"], "spot")
    assert "publicTrade.BTCUSDT" in chans


def test_bybit_connector_normalize_delegates() -> None:
    """BybitConnector.normalize dispatches dict messages to normalize_message."""
    from crypcodile.exchanges.bybit.connector import BybitConnector
    from crypcodile.instruments.registry import InstrumentRegistry
    from crypcodile.sink.memory import MemorySink

    sink = MemorySink()
    conn = BybitConnector(
        symbols=["BTCUSDT"],
        channels=["trade"],
        out=sink,
        registry=InstrumentRegistry(),
        category="linear",
    )
    # Non-dict messages must produce no records (no crash)
    result = list(conn.normalize("not a dict", local_ts=1))
    assert result == []

    # A dict that doesn't match any known topic → empty (no crash)
    result2 = list(conn.normalize({"topic": "unknown", "data": {}}, local_ts=1))
    assert result2 == []
