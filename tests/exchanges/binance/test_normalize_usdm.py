import json
import pathlib

from crocodile.exchanges.binance.normalize import normalize_message
from crocodile.schema.enums import Side
from crocodile.schema.records import DerivativeTicker, Funding, Liquidation

P = pathlib.Path(__file__).parent / "fixtures"


def test_markprice_emits_derivative_and_funding():
    msg = json.loads((P / "usdm_markprice.json").read_text())
    out = list(normalize_message(msg, local_ts=1, venue="binance-usdm"))
    dt = next(r for r in out if isinstance(r, DerivativeTicker))
    fn = next(r for r in out if isinstance(r, Funding))
    assert dt.mark_price == 50000.0 and dt.index_price == 50001.0
    assert fn.funding_rate == 0.0001 and fn.funding_timestamp == 1700003600000 * 1_000_000


def test_forceorder_emits_liquidation():
    msg = json.loads((P / "usdm_forceorder.json").read_text())
    liq = next(normalize_message(msg, local_ts=1, venue="binance-usdm"))
    assert isinstance(liq, Liquidation)
    assert liq.side == Side.SELL and liq.price == 48950.0 and liq.amount == 1.5
