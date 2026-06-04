from crocodile.exchanges.deribit.connector import build_channels, parse_instruments


def test_build_channels():
    chans = build_channels(["BTC-PERPETUAL"], ["trade", "book_delta", "derivative_ticker"])
    assert "trades.BTC-PERPETUAL.raw" in chans
    assert "book.BTC-PERPETUAL.raw" in chans
    assert "ticker.BTC-PERPETUAL" in chans


def test_parse_instruments():
    raw = {
        "result": [
            {
                "instrument_name": "BTC-30JUN-50000-C",
                "kind": "option",
                "base_currency": "BTC",
                "quote_currency": "USD",
                "strike": 50000.0,
                "expiration_timestamp": 1700000000000,
                "option_type": "call",
                "tick_size": 0.0005,
                "contract_size": 1.0,
            }
        ]
    }
    insts = parse_instruments(raw)
    assert insts[0].canonical == "deribit:BTC-30JUN-50000-C"
    assert insts[0].opt_type == "C" and insts[0].expiry == 1700000000000 * 1_000_000
