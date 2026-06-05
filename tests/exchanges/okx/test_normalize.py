"""Golden-fixture tests for OKX V5 message normalization (Task 4.5)."""

from __future__ import annotations

import json
import pathlib

from crocodile.exchanges.okx.normalize import normalize_message
from crocodile.instruments.registry import Instrument, InstrumentRegistry, Kind
from crocodile.schema.enums import OptType, Side
from crocodile.schema.records import (
    BookDelta,
    BookSnapshot,
    DerivativeTicker,
    Funding,
    Liquidation,
    OpenInterest,
    OptionsChain,
    Trade,
)

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Trades
# ---------------------------------------------------------------------------


def test_trade_buy_normalization() -> None:
    msg = json.loads((FIXTURES / "trade.json").read_text())
    out = list(normalize_message(msg, local_ts=42, venue="okx"))
    trades = [r for r in out if isinstance(r, Trade)]
    assert len(trades) == 2
    first = trades[0]
    assert first.price == 50000.10
    assert first.amount == 0.5
    assert first.side == Side.BUY          # "buy" → canonical buy
    assert first.exchange_ts == 1700000000100 * 1_000_000  # ts ms → ns
    assert first.local_ts == 42
    assert first.symbol_raw == "BTC-USDT"
    assert first.exchange == "okx"
    assert first.id == "555"


def test_trade_sell_normalization() -> None:
    msg = json.loads((FIXTURES / "trade.json").read_text())
    out = list(normalize_message(msg, local_ts=42, venue="okx"))
    trades = [r for r in out if isinstance(r, Trade)]
    second = trades[1]
    assert second.side == Side.SELL        # "sell" → canonical sell
    assert second.price == 49999.50
    assert second.id == "556"


# ---------------------------------------------------------------------------
# Order book — snapshot (action="snapshot")
# ---------------------------------------------------------------------------


def test_books_snapshot() -> None:
    msg = json.loads((FIXTURES / "books_snapshot.json").read_text())
    out = list(normalize_message(msg, local_ts=7, venue="okx"))
    snaps = [r for r in out if isinstance(r, BookSnapshot)]
    assert len(snaps) == 1
    snap = snaps[0]
    assert snap.is_snapshot is True
    assert snap.sequence_id == 100         # seqId field
    assert (50000.0, 5.0) in snap.bids
    assert (49999.0, 2.0) in snap.bids
    assert (50001.0, 4.0) in snap.asks
    assert snap.exchange_ts == 1700000000000 * 1_000_000  # ts ms → ns
    assert snap.symbol_raw == "BTC-USDT"
    assert snap.exchange == "okx"


# ---------------------------------------------------------------------------
# Order book — update (action="update", qty="0" → amount 0.0 = remove)
# ---------------------------------------------------------------------------


def test_books_update_with_removal() -> None:
    msg = json.loads((FIXTURES / "books_update.json").read_text())
    out = list(normalize_message(msg, local_ts=8, venue="okx"))
    deltas = [r for r in out if isinstance(r, BookDelta)]
    assert len(deltas) == 1
    delta = deltas[0]
    assert delta.is_snapshot is False
    assert delta.seq_id == 101             # seqId field
    assert delta.prev_seq_id == 100        # prevSeqId field
    # qty="0" must map to amount=0.0 (canonical remove signal)
    assert (49999.0, 0.0) in delta.bids
    assert (49998.0, 3.0) in delta.bids
    assert (50002.0, 1.0) in delta.asks


# ---------------------------------------------------------------------------
# Ticker — SWAP perpetual (DerivativeTicker + Funding)
# ---------------------------------------------------------------------------


def test_ticker_swap_emits_derivative_and_funding() -> None:
    msg = json.loads((FIXTURES / "ticker_swap.json").read_text())
    out = list(normalize_message(msg, local_ts=9, venue="okx"))
    dt_list = [r for r in out if isinstance(r, DerivativeTicker)]
    fn_list = [r for r in out if isinstance(r, Funding)]
    assert len(dt_list) == 1
    dt = dt_list[0]
    assert dt.last_price == 50000.0
    assert dt.mark_price == 50001.0
    assert dt.index_price == 50000.5
    assert dt.open_interest == 12345.6
    assert dt.funding_rate == 0.0001
    # nextFundingTime ms → ns
    assert dt.funding_timestamp == 1700003600000 * 1_000_000

    assert len(fn_list) == 1
    fn = fn_list[0]
    assert fn.funding_rate == 0.0001
    assert fn.funding_timestamp == 1700003600000 * 1_000_000


# ---------------------------------------------------------------------------
# Funding-rate channel
# ---------------------------------------------------------------------------


def test_funding_rate_channel() -> None:
    msg = json.loads((FIXTURES / "funding_rate.json").read_text())
    out = list(normalize_message(msg, local_ts=10, venue="okx"))
    fn_list = [r for r in out if isinstance(r, Funding)]
    assert len(fn_list) == 1
    fn = fn_list[0]
    assert fn.funding_rate == 0.0001
    assert fn.funding_timestamp == 1700003600000 * 1_000_000   # fundingTime ms → ns
    assert fn.predicted_funding_rate == 0.00012
    assert fn.symbol_raw == "BTC-USDT-SWAP"


# ---------------------------------------------------------------------------
# Open interest channel
# ---------------------------------------------------------------------------


def test_open_interest_channel() -> None:
    msg = json.loads((FIXTURES / "open_interest.json").read_text())
    out = list(normalize_message(msg, local_ts=11, venue="okx"))
    oi_list = [r for r in out if isinstance(r, OpenInterest)]
    assert len(oi_list) == 1
    oi = oi_list[0]
    assert oi.open_interest == 12345.0
    assert oi.open_interest_value == 1234.5   # oiCcy field
    assert oi.exchange_ts == 1700000000000 * 1_000_000
    assert oi.symbol_raw == "BTC-USDT-SWAP"


# ---------------------------------------------------------------------------
# Liquidation orders channel
# ---------------------------------------------------------------------------


def test_liq_orders_emits_liquidation() -> None:
    msg = json.loads((FIXTURES / "liq_orders.json").read_text())
    out = list(normalize_message(msg, local_ts=12, venue="okx"))
    liqs = [r for r in out if isinstance(r, Liquidation)]
    assert len(liqs) == 1
    liq = liqs[0]
    assert liq.side == Side.SELL
    assert liq.amount == 1.5
    assert liq.price == 49000.0            # bkPx (bankruptcy price)
    assert liq.exchange_ts == 1700000000000 * 1_000_000


# ---------------------------------------------------------------------------
# Option-summary channel → OptionsChain (with registry)
# ---------------------------------------------------------------------------


def test_option_summary_with_registry() -> None:
    reg = InstrumentRegistry()
    reg.add(
        Instrument(
            canonical="okx:BTC-USD-25DEC22-40000-C",
            exchange="okx",
            symbol_raw="BTC-USD-25DEC22-40000-C",
            kind=Kind.OPTION,
            base="BTC",
            quote="USD",
            strike=40000.0,
            expiry=1_700_000_000_000_000_000,
            opt_type="C",
        )
    )
    msg = json.loads((FIXTURES / "option_summary.json").read_text())
    out = list(normalize_message(msg, local_ts=13, venue="okx", registry=reg))
    oc_list = [r for r in out if isinstance(r, OptionsChain)]
    assert len(oc_list) == 1
    oc = oc_list[0]
    assert oc.strike == 40000.0
    assert oc.opt_type == OptType.CALL
    assert oc.mark_iv == 0.65              # markVol field
    assert oc.bid_iv == 0.64              # bidVol field
    assert oc.ask_iv == 0.66              # askVol field
    assert oc.delta == 0.5
    assert oc.gamma == 0.001
    assert oc.vega == 12.0
    assert oc.theta == -3.0
    assert oc.underlying == "BTC-USD"      # uly field


# ---------------------------------------------------------------------------
# Option-summary channel → OptionsChain (fallback — no registry)
# ---------------------------------------------------------------------------


def test_option_summary_no_registry_fallback() -> None:
    """Fallback: no registry → strike/opt_type parsed from instId string."""
    msg = json.loads((FIXTURES / "option_summary.json").read_text())
    out = list(normalize_message(msg, local_ts=13, venue="okx", registry=None))
    oc_list = [r for r in out if isinstance(r, OptionsChain)]
    assert len(oc_list) == 1
    oc = oc_list[0]
    # OKX option instId: BTC-USD-25DEC22-40000-C → strike=40000.0, opt_type=CALL
    assert oc.strike == 40000.0
    assert oc.opt_type == OptType.CALL
