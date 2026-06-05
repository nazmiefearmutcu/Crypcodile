"""Golden-fixture tests for Bybit V5 message normalization (Task 4.4)."""

from __future__ import annotations

import json
import pathlib

from crocodile.exchanges.bybit.normalize import normalize_message
from crocodile.instruments.registry import Instrument, InstrumentRegistry, Kind
from crocodile.schema.enums import OptType, Side
from crocodile.schema.records import (
    BookDelta,
    BookSnapshot,
    BookTicker,
    DerivativeTicker,
    Funding,
    OptionsChain,
    Trade,
)

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Trades
# ---------------------------------------------------------------------------


def test_trade_buy_normalization() -> None:
    msg = json.loads((FIXTURES / "trade.json").read_text())
    out = list(normalize_message(msg, local_ts=42, venue="bybit"))
    trades = [r for r in out if isinstance(r, Trade)]
    assert len(trades) == 2
    first = trades[0]
    assert first.price == 50000.10
    assert first.amount == 0.5
    assert first.side == Side.BUY          # "Buy" → lowercase buy
    assert first.exchange_ts == 1700000000100 * 1_000_000  # T field ms→ns
    assert first.local_ts == 42
    assert first.symbol_raw == "BTCUSDT"
    assert first.exchange == "bybit"


def test_trade_sell_normalization() -> None:
    msg = json.loads((FIXTURES / "trade.json").read_text())
    out = list(normalize_message(msg, local_ts=42, venue="bybit"))
    trades = [r for r in out if isinstance(r, Trade)]
    second = trades[1]
    assert second.side == Side.SELL        # "Sell" → lowercase sell
    assert second.price == 49999.50


# ---------------------------------------------------------------------------
# Order book — snapshot
# ---------------------------------------------------------------------------


def test_orderbook_snapshot() -> None:
    msg = json.loads((FIXTURES / "orderbook_snapshot.json").read_text())
    out = list(normalize_message(msg, local_ts=7, venue="bybit"))
    snaps = [r for r in out if isinstance(r, BookSnapshot)]
    assert len(snaps) == 1
    snap = snaps[0]
    assert snap.is_snapshot is True
    assert snap.sequence_id == 100          # u field
    assert (49999.0, 1.5) in snap.bids
    assert (49998.0, 2.0) in snap.bids
    assert (50001.0, 0.8) in snap.asks
    assert snap.exchange_ts == 1700000000000 * 1_000_000  # ts field ms→ns
    assert snap.symbol_raw == "BTCUSDT"


# ---------------------------------------------------------------------------
# Order book — delta (qty "0" → amount 0.0 = canonical remove)
# ---------------------------------------------------------------------------


def test_orderbook_delta_with_removal() -> None:
    msg = json.loads((FIXTURES / "orderbook_delta.json").read_text())
    out = list(normalize_message(msg, local_ts=8, venue="bybit"))
    deltas = [r for r in out if isinstance(r, BookDelta)]
    assert len(deltas) == 1
    delta = deltas[0]
    assert delta.is_snapshot is False
    assert delta.seq_id == 101              # u field
    # qty="0" must map to amount=0.0 (canonical remove signal)
    assert (49998.0, 0.0) in delta.bids
    assert (49997.0, 3.0) in delta.bids
    assert (50001.0, 1.2) in delta.asks


# ---------------------------------------------------------------------------
# Ticker — linear perpetual (DerivativeTicker + Funding + BookTicker)
# ---------------------------------------------------------------------------


def test_ticker_linear_emits_derivative_and_funding() -> None:
    msg = json.loads((FIXTURES / "ticker_linear.json").read_text())
    out = list(normalize_message(msg, local_ts=9, venue="bybit"))
    dt_list = [r for r in out if isinstance(r, DerivativeTicker)]
    fn_list = [r for r in out if isinstance(r, Funding)]
    bt_list = [r for r in out if isinstance(r, BookTicker)]
    assert len(dt_list) == 1
    dt = dt_list[0]
    assert dt.last_price == 50000.0
    assert dt.mark_price == 50001.0
    assert dt.index_price == 49999.5
    assert dt.open_interest == 12345.6
    assert dt.funding_rate == 0.0001
    assert dt.funding_timestamp == 1700003600000 * 1_000_000  # nextFundingTime ms→ns

    assert len(fn_list) == 1
    fn = fn_list[0]
    assert fn.funding_rate == 0.0001
    assert fn.funding_timestamp == 1700003600000 * 1_000_000

    # BookTicker from bid1/ask1 fields
    assert len(bt_list) == 1
    bt = bt_list[0]
    assert bt.bid_px == 49999.0
    assert bt.ask_px == 50001.0


# ---------------------------------------------------------------------------
# Ticker — option (OptionsChain via registry)
# ---------------------------------------------------------------------------


def test_ticker_option_emits_options_chain() -> None:
    reg = InstrumentRegistry()
    reg.add(
        Instrument(
            canonical="bybit:BTC-30JUN25-50000-C",
            exchange="bybit",
            symbol_raw="BTC-30JUN25-50000-C",
            kind=Kind.OPTION,
            base="BTC",
            quote="USD",
            strike=50000.0,
            expiry=1_900_000_000_000_000_000,
            opt_type="C",
        )
    )
    msg = json.loads((FIXTURES / "ticker_option.json").read_text())
    out = list(normalize_message(msg, local_ts=10, venue="bybit", registry=reg))
    oc_list = [r for r in out if isinstance(r, OptionsChain)]
    assert len(oc_list) == 1
    oc = oc_list[0]
    assert oc.strike == 50000.0
    assert oc.opt_type == OptType.CALL
    assert oc.mark_price == 0.05
    assert oc.mark_iv == 0.65
    assert oc.delta == 0.5
    assert oc.gamma == 0.001
    assert oc.vega == 12.0
    assert oc.theta == -3.0
    assert oc.bid_px == 0.04
    assert oc.ask_px == 0.06
    assert oc.open_interest == 10.0
