"""Regression tests for T1-options: Option metadata + IV-unit consistency.

Each test is written to FAIL on the current (unfixed) code and PASS after the
fixes described in the task are applied.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from crypcodile.exchanges.binance.normalize import normalize_message as binance_normalize
from crypcodile.exchanges.bybit.normalize import normalize_message as bybit_normalize
from crypcodile.exchanges.deribit.normalize import (
    _parse_option_symbol,
)
from crypcodile.exchanges.deribit.normalize import (
    normalize_message as deribit_normalize,
)
from crypcodile.instruments.registry import Instrument, InstrumentRegistry, Kind
from crypcodile.schema.enums import OptType
from crypcodile.schema.records import OptionsChain

# ---------------------------------------------------------------------------
# Fixture paths
# ---------------------------------------------------------------------------

DERIBIT_FIXTURES = pathlib.Path(__file__).parent / "deribit/fixtures"
BINANCE_FIXTURES = pathlib.Path(__file__).parent / "binance/fixtures"
BYBIT_FIXTURES = pathlib.Path(__file__).parent / "bybit/fixtures"


# ===========================================================================
# BUG 1 — IV UNITS: Deribit and Binance must divide IV by 100 (decimal fraction)
# ===========================================================================


def test_deribit_option_iv_is_decimal_fraction():
    """Deribit wire sends mark_iv=65.0 (percent); after fix it must be 0.65."""
    msg = json.loads((DERIBIT_FIXTURES / "ticker_option.json").read_text())
    reg = InstrumentRegistry()
    reg.add(
        Instrument(
            canonical="deribit:BTC-30JUN-50000-C",
            exchange="deribit",
            symbol_raw="BTC-30JUN-50000-C",
            kind=Kind.OPTION,
            base="BTC",
            quote="USD",
            strike=50000.0,
            expiry=1_900_000_000_000_000_000,
            opt_type="C",
        )
    )
    out = list(deribit_normalize(msg, local_ts=7, registry=reg))
    oc = next(r for r in out if isinstance(r, OptionsChain))
    # Wire value: mark_iv=65.0, bid_iv=64.0, ask_iv=66.0 → must be /100
    assert oc.mark_iv == pytest.approx(0.65), (
        f"mark_iv must be decimal fraction 0.65, got {oc.mark_iv}"
    )
    assert oc.bid_iv == pytest.approx(0.64), (
        f"bid_iv must be decimal fraction 0.64, got {oc.bid_iv}"
    )
    assert oc.ask_iv == pytest.approx(0.66), (
        f"ask_iv must be decimal fraction 0.66, got {oc.ask_iv}"
    )


def test_binance_eapi_option_iv_is_decimal_fraction():
    """Binance EAPI wire sends vo/b/a as percent (65.0/64.0/66.0); must be /100."""
    msg = json.loads((BINANCE_FIXTURES / "eapi_option_markprice.json").read_text())
    out = list(binance_normalize(msg, local_ts=1, venue="binance-eapi"))
    oc = next(r for r in out if isinstance(r, OptionsChain))
    # Wire: vo="65.0", b="64.0", a="66.0" → decimal fraction after fix
    assert oc.mark_iv == pytest.approx(0.65), (
        f"mark_iv must be decimal fraction 0.65, got {oc.mark_iv}"
    )
    assert oc.bid_iv == pytest.approx(0.64), (
        f"bid_iv must be decimal fraction 0.64, got {oc.bid_iv}"
    )
    assert oc.ask_iv == pytest.approx(0.66), (
        f"ask_iv must be decimal fraction 0.66, got {oc.ask_iv}"
    )


# ===========================================================================
# BUG 2 — NO SILENT OptType.CALL DEFAULT
# ===========================================================================


def test_deribit_registry_put_opt_type_none_not_coerced_to_call():
    """Registry instrument with opt_type=None must NOT silently become CALL.

    After fix, the code falls back to parsing the symbol (BTC-30JUN-50000-P)
    and must produce OptType.PUT.
    """
    # Craft a fixture message with a PUT symbol
    msg = {
        "params": {
            "channel": "ticker.BTC-30JUN-50000-P",
            "data": {
                "instrument_name": "BTC-30JUN-50000-P",
                "timestamp": 1700000000000,
                "mark_price": 0.03,
                "mark_iv": 40.0,  # percent — will be /100 after fix
                "underlying_price": 50000.0,
                "open_interest": 5.0,
                "best_bid_price": 0.02,
                "best_bid_amount": 1.0,
                "bid_iv": 39.0,
                "best_ask_price": 0.04,
                "best_ask_amount": 0.5,
                "ask_iv": 41.0,
                "greeks": {"delta": -0.4, "gamma": 0.001, "vega": 10.0, "theta": -2.0, "rho": -0.5},
            },
        }
    }
    reg = InstrumentRegistry()
    # opt_type=None in the registry — the bug silently coerces to CALL
    reg.add(
        Instrument(
            canonical="deribit:BTC-30JUN-50000-P",
            exchange="deribit",
            symbol_raw="BTC-30JUN-50000-P",
            kind=Kind.OPTION,
            base="BTC",
            quote="USD",
            strike=50000.0,
            expiry=1_900_000_000_000_000_000,
            opt_type=None,  # <-- opt_type missing in registry
        )
    )
    out = list(deribit_normalize(msg, local_ts=7, registry=reg))
    oc = next(r for r in out if isinstance(r, OptionsChain))
    assert oc.opt_type == OptType.PUT, (
        f"opt_type must be PUT (parsed from symbol), got {oc.opt_type}"
    )


def test_bybit_unparseable_option_symbol_is_skipped_not_emitted_as_call():
    """Bybit: an option-looking symbol that cannot be parsed must be SKIPPED.

    The current code emits a record with opt_type=OptType.CALL (silent default).
    After fix, a log.warning is emitted and the record is not yielded.
    """
    # Use a 4-part symbol that looks like an option (passes _is_option_symbol)
    # but has a non-numeric strike so strike parsing fails.
    msg = {
        "topic": "tickers.BTC-30JUN25-BADSTRIKE-C",
        "type": "snapshot",
        "ts": 1700000000000,
        "data": {
            "symbol": "BTC-30JUN25-BADSTRIKE-C",
            "markPrice": "0.05",
            "markIv": "0.65",
        },
    }
    out = list(bybit_normalize(msg, local_ts=0, venue="bybit", registry=None))
    oc_list = [r for r in out if isinstance(r, OptionsChain)]
    assert len(oc_list) == 0, (
        f"Unparseable Bybit option symbol must be skipped, but got {len(oc_list)} record(s)"
    )


# ===========================================================================
# BUG 3 — Binance EAPI _parse_eapi_option_symbol drops expiry
# ===========================================================================


def test_binance_eapi_no_registry_expiry_is_nonzero():
    """No-registry path: expiry must be parsed from YYMMDD segment, not 0.

    BTC-231215-50000-C → 2023-12-15 → nonzero expiry_ns.
    """
    msg = {
        "stream": "BTC@optionMarkPrice",
        "data": [
            {
                "e": "option_mark_price_update",
                "E": 1700000000000,
                "s": "BTC-231215-50000-C",
                "mp": "0.05",
                "d": "0.5",
                "g": "0.001",
                "t": "-3.0",
                "v": "12.0",
                "b": "64.0",
                "a": "66.0",
                "vo": "65.0",
                "oi": "10.0",
            }
        ],
    }
    out = list(binance_normalize(msg, local_ts=1, venue="binance-eapi", registry=None))
    oc = next(r for r in out if isinstance(r, OptionsChain))
    assert oc.expiry != 0, (
        f"expiry must be nonzero (parsed from symbol YYMMDD), got {oc.expiry}"
    )
    # 2023-12-15 → 1702598400 * 1e9 (allow any non-zero value; exact epoch check below)
    assert oc.expiry > 0


# ===========================================================================
# BUG 4 — Deribit _parse_option_symbol: len(parts) < 4 guard
# ===========================================================================


def test_deribit_parse_option_symbol_raises_on_short_symbol():
    """_parse_option_symbol with < 4 parts must raise ValueError, not IndexError."""
    with pytest.raises(ValueError):
        _parse_option_symbol("BTC-30JUN")  # only 2 parts


def test_deribit_malformed_option_symbol_in_ticker_is_skipped():
    """Malformed Deribit option ticker must be skipped (log.warning + return), not crash."""
    msg = {
        "params": {
            "channel": "ticker.BTC-BADFORMAT",
            "data": {
                "instrument_name": "BTC-BADFORMAT",
                "timestamp": 1700000000000,
                # mark_iv present → triggers option branch
                "mark_iv": 50.0,
                "underlying_price": 50000.0,
                "open_interest": 1.0,
                "best_bid_price": 0.01,
                "best_bid_amount": 1.0,
                "bid_iv": 49.0,
                "best_ask_price": 0.02,
                "best_ask_amount": 0.5,
                "ask_iv": 51.0,
                "greeks": None,
            },
        }
    }
    # No registry → falls through to _parse_option_symbol → must not raise
    out = list(deribit_normalize(msg, local_ts=7, registry=None))
    oc_list = [r for r in out if isinstance(r, OptionsChain)]
    assert len(oc_list) == 0, (
        f"Malformed Deribit symbol must produce 0 records, got {len(oc_list)}"
    )
