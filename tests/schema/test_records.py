import msgspec

from crypcodile.schema.enums import Side
from crypcodile.schema.records import BookDelta, Trade


def test_trade_encodes_with_channel_tag():
    t = Trade(exchange="deribit", symbol="BTC-PERPETUAL", symbol_raw="BTC-PERPETUAL",
              exchange_ts=1, local_ts=2, id="x", price=100.0, amount=1.0, side=Side.BUY)
    raw = msgspec.json.encode(t)
    d = msgspec.json.decode(raw)
    assert d["channel"] == "trade"
    assert d["side"] == "buy"

def test_book_delta_remove_level_is_zero_amount():
    d = BookDelta(exchange="binance-spot", symbol="BTC-USDT", symbol_raw="BTCUSDT",
                  exchange_ts=None, local_ts=2, bids=[(100.0, 0.0)], asks=[], seq_id=5)
    assert d.bids[0][1] == 0.0  # canonical removal signal
