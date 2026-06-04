import json
import pathlib

from crocodile.exchanges.deribit.normalize import normalize_message
from crocodile.schema.enums import Side
from crocodile.schema.records import Liquidation, Trade

FIX = pathlib.Path(__file__).parent / "fixtures" / "trades.json"


def test_trade_and_liquidation_emitted():
    msg = json.loads(FIX.read_text())
    out = list(normalize_message(msg, local_ts=42))
    trades = [r for r in out if isinstance(r, Trade)]
    liqs = [r for r in out if isinstance(r, Liquidation)]
    assert len(trades) == 2
    assert trades[0].price == 2000.5 and trades[0].side == Side.BUY
    assert trades[0].liquidation is None
    assert trades[0].exchange_ts == 1700000000000 * 1_000_000  # ms→ns
    assert trades[0].local_ts == 42
    assert trades[1].liquidation == "T"
    assert len(liqs) == 1 and liqs[0].side == Side.SELL  # from direction "sell"
