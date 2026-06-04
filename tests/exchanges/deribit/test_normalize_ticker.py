import json
import pathlib

from crocodile.exchanges.deribit.normalize import normalize_message
from crocodile.instruments.registry import Instrument, InstrumentRegistry, Kind
from crocodile.schema.enums import OptType
from crocodile.schema.records import DerivativeTicker, Funding, OptionsChain

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
    assert oc.mark_iv == 65.0 and oc.delta == 0.5 and oc.bid_iv == 64.0
