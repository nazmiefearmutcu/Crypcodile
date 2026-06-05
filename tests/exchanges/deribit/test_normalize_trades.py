import json
import logging
import pathlib

import pytest

from crocodile.exchanges.deribit.normalize import normalize_message
from crocodile.schema.enums import Side
from crocodile.schema.records import Liquidation, Trade

FIX = pathlib.Path(__file__).parent / "fixtures" / "trades.json"


# ---------------------------------------------------------------------------
# T3-connmisc: Deribit unrecognized channel → log.debug
# ---------------------------------------------------------------------------


def test_unrecognized_channel_emits_debug_log(caplog: pytest.LogCaptureFixture) -> None:
    """normalize_message must emit a log.debug for unrecognized channels.

    Other connectors (Bybit, Coinbase, OKX) all have an else-branch that logs
    unhandled topics; Deribit was missing this.
    """
    msg = {
        "params": {
            "channel": "unknown_channel.BTC-PERPETUAL",
            "data": {},
        }
    }
    with caplog.at_level(logging.DEBUG, logger="crocodile.exchanges.deribit.normalize"):
        out = list(normalize_message(msg, local_ts=0))
    assert out == [], "unrecognized channel must yield no records"
    assert any(
        "unrecognized" in r.message.lower() or "unhandled" in r.message.lower()
        for r in caplog.records
    ), f"expected a debug log for unrecognized channel, got: {[r.message for r in caplog.records]}"


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
