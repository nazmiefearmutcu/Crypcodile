import json
import pathlib

from crypcodile.exchanges.binance.normalize import normalize_message
from crypcodile.schema.enums import Side
from crypcodile.schema.records import BookTicker, Trade

P = pathlib.Path(__file__).parent / "fixtures"


def test_spot_aggtrade():
    msg = json.loads((P / "spot_aggtrade.json").read_text())
    t = next(iter(normalize_message(msg, local_ts=9, venue="binance-spot")))
    assert isinstance(t, Trade)
    assert t.price == 50000.10 and t.amount == 0.5
    assert t.side == Side.SELL  # m=true => buyer is maker => taker sold
    assert t.exchange_ts == 1700000000100 * 1_000_000  # uses T, not E
    assert t.symbol_raw == "BTCUSDT"


def test_spot_bookticker():
    msg = json.loads((P / "spot_bookticker.json").read_text())
    bt = next(iter(normalize_message(msg, local_ts=9, venue="binance-spot")))
    assert isinstance(bt, BookTicker)
    assert bt.bid_px == 49999.0 and bt.ask_sz == 0.8 and bt.update_id == 99
