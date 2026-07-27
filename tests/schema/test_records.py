import msgspec

from crocodile.core.schema.enums import AssetClass, Side
from crocodile.core.schema.records import BookDelta, Trade


def test_trade_encodes_with_channel_tag():
    t = Trade(source="deribit", symbol="BTC-PERPETUAL", symbol_raw="BTC-PERPETUAL",
              source_ts=1, local_ts=2, asset_class=AssetClass.CRYPTO, id="x", price=100.0, amount=1.0, side=Side.BUY)
    raw = msgspec.json.encode(t)
    d = msgspec.json.decode(raw)
    assert d["channel"] == "trade"
    assert d["side"] == "buy"

def test_book_delta_remove_level_is_zero_amount():
    d = BookDelta(source="binance-spot", symbol="BTC-USDT", symbol_raw="BTCUSDT",
                  source_ts=None, local_ts=2, asset_class=AssetClass.CRYPTO, bids=[(100.0, 0.0)], asks=[], seq_id=5)
    assert d.bids[0][1] == 0.0  # canonical removal signal
