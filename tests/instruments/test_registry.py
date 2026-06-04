from crocodile.instruments.registry import Instrument, InstrumentRegistry, Kind


def test_register_and_resolve():
    reg = InstrumentRegistry()
    inst = Instrument(canonical="deribit:BTC-PERPETUAL", exchange="deribit",
                      symbol_raw="BTC-PERPETUAL", kind=Kind.PERPETUAL, base="BTC", quote="USD")
    reg.add(inst)
    assert reg.by_raw("deribit", "BTC-PERPETUAL").canonical == "deribit:BTC-PERPETUAL"
    assert reg.by_canonical("deribit:BTC-PERPETUAL").symbol_raw == "BTC-PERPETUAL"

def test_option_metadata_round_trip():
    reg = InstrumentRegistry()
    inst = Instrument(canonical="deribit:BTC-30JUN-50000-C", exchange="deribit",
                      symbol_raw="BTC-30JUN-50000-C", kind=Kind.OPTION, base="BTC", quote="USD",
                      strike=50000.0, expiry=1_900_000_000_000_000_000, opt_type="C")
    reg.add(inst)
    got = reg.by_raw("deribit", "BTC-30JUN-50000-C")
    assert got.strike == 50000.0 and got.opt_type == "C"
