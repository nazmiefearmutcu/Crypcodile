import json
import pathlib
import time

import pytest

from crypcodile.exchanges.deribit.normalize import normalize_message
from crypcodile.instruments.registry import Instrument, InstrumentRegistry, Kind
from crypcodile.schema.enums import OptType
from crypcodile.schema.records import DerivativeTicker, Funding, OptionsChain

P = pathlib.Path(__file__).parent / "fixtures"


def test_perp_ticker_emits_derivative_and_funding():
    msg = json.loads((P / "ticker_perp.json").read_text())
    out = list(normalize_message(msg, local_ts=7))
    dt = next(r for r in out if isinstance(r, DerivativeTicker))
    fn = next(r for r in out if isinstance(r, Funding))
    assert dt.mark_price == 2000.4 and dt.open_interest == 12345.0
    assert fn.funding_rate == 0.0001 and fn.predicted_funding_rate == 0.0003


def test_option_ticker_emits_options_chain():
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
    msg = json.loads((P / "ticker_option.json").read_text())
    out = list(normalize_message(msg, local_ts=7, registry=reg))
    oc = next(r for r in out if isinstance(r, OptionsChain))
    assert oc.strike == 50000.0 and oc.opt_type == OptType.CALL
    # IV fields are decimal fractions (wire percent / 100): 65.0% → 0.65
    assert oc.mark_iv == pytest.approx(0.65)
    assert oc.delta == 0.5
    assert oc.bid_iv == pytest.approx(0.64)


def test_option_ticker_fallback_no_registry():
    """Registry-fallback path: symbol parsed directly when no registry is supplied.

    The fixture symbol is BTC-30JUN-50000-C.  Without a registry the normalizer
    must parse it and produce structurally correct output.  The expiry must be a
    future timestamp (not a date in 2025 or earlier).
    """
    msg = json.loads((P / "ticker_option.json").read_text())
    # Pass no registry — exercises the symbol-parsing branch in _parse_option_symbol
    out = list(normalize_message(msg, local_ts=7))
    oc = next(r for r in out if isinstance(r, OptionsChain))
    assert oc.underlying == "BTC"
    assert oc.strike == 50000.0
    assert oc.opt_type == OptType.CALL
    # expiry must be a future nanosecond timestamp (not stuck in 2025)
    assert oc.expiry > int(time.time()) * 1_000_000_000
