"""Golden-fixture tests for OKX V5 message normalization (Task 4.5)."""

from __future__ import annotations

import json
import pathlib

from crypcodile.exchanges.okx.normalize import normalize_message
from crypcodile.instruments.registry import Instrument, InstrumentRegistry, Kind
from crypcodile.schema.enums import OptType, Side
from crypcodile.schema.records import (
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


def test_ticker_swap_emits_derivative_ticker_with_funding_fields() -> None:
    """tickers channel emits DerivativeTicker only (no Funding — T2-okx fix).

    The DerivativeTicker carries funding_rate and funding_timestamp so callers
    can read them, but the separate Funding record comes exclusively from the
    funding-rate channel to avoid duplication when both channels are subscribed.
    (Updated from the pre-fix assertion that incorrectly expected fn_list length 1.)
    """
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

    # T2-okx fix: tickers must NOT emit a separate Funding record
    assert len(fn_list) == 0


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


# ---------------------------------------------------------------------------
# Bug fix: malformed instId → record must be skipped (not emitted with sentinel 0)
# ---------------------------------------------------------------------------


def test_option_summary_malformed_instid_is_skipped() -> None:
    """_parse_option_instid returns (None, None) for a short/malformed instId.

    Previously the normalizer would emit a nonsensical OptionsChain with
    strike=0.0, expiry=0, opt_type=CALL (sentinel defaults).  After the fix
    such records are silently dropped (no yield).
    """
    # Deliberately malformed instId: too few dash-segments → _parse_option_instid → (None, None)
    msg: dict = {
        "arg": {"channel": "option-summary", "instId": "MALFORMED"},
        "data": [
            {
                "instId": "MALFORMED",  # <5 parts → cannot parse strike/opt_type
                "uly": "BTC-USD",
                "ts": "1700000000000",
                "markVol": "0.5",
                "bidVol": "0.49",
                "askVol": "0.51",
                "delta": "0.5",
                "gamma": "0.001",
                "vega": "10.0",
                "theta": "-2.0",
            }
        ],
    }
    out = list(normalize_message(msg, local_ts=99, venue="okx", registry=None))
    oc_list = [r for r in out if isinstance(r, OptionsChain)]
    # Malformed record must NOT be emitted
    assert len(oc_list) == 0, (
        f"Expected 0 OptionsChain records for malformed instId, got {len(oc_list)}: "
        f"strike={oc_list[0].strike if oc_list else 'N/A'}"
    )


# ---------------------------------------------------------------------------
# Verify _side() is NOT duplicated between normalize.py and backfill.py
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# T2-okx regression: funding-rate exchange_ts != funding_timestamp
# ---------------------------------------------------------------------------


def test_funding_rate_exchange_ts_is_event_time_not_settlement() -> None:
    """exchange_ts must be the message/event time (ts), NOT the settlement time (fundingTime).

    OKX funding-rate fixture has:
      - ts            = 1700000100000  (event time, ms)
      - fundingTime   = 1700003600000  (settlement time, ms)

    Before the fix both exchange_ts and funding_timestamp were set to fundingTime,
    making them equal.  After the fix exchange_ts == ts_ns != fundingTime_ns.
    """
    msg = json.loads((FIXTURES / "funding_rate.json").read_text())
    out = list(normalize_message(msg, local_ts=99, venue="okx"))
    fn_list = [r for r in out if isinstance(r, Funding)]
    assert len(fn_list) == 1
    fn = fn_list[0]
    event_ts_ns = 1700000100000 * 1_000_000    # ts field
    settlement_ts_ns = 1700003600000 * 1_000_000  # fundingTime field
    assert fn.exchange_ts == event_ts_ns, (
        f"exchange_ts should be event time {event_ts_ns}, got {fn.exchange_ts}"
    )
    assert fn.funding_timestamp == settlement_ts_ns, (
        f"funding_timestamp should be settlement time {settlement_ts_ns}, "
        f"got {fn.funding_timestamp}"
    )
    assert fn.exchange_ts != fn.funding_timestamp, (
        "exchange_ts must differ from funding_timestamp (event time vs settlement time)"
    )


# ---------------------------------------------------------------------------
# T2-okx regression: tickers channel must yield ONLY DerivativeTicker, no Funding
# ---------------------------------------------------------------------------


def test_ticker_swap_emits_only_derivative_ticker_not_funding() -> None:
    """_normalize_tickers must NOT emit Funding — that comes exclusively from funding-rate channel.

    Before the fix, tickers emitted both DerivativeTicker and Funding, causing
    duplicate Funding records when both channels are subscribed simultaneously.
    After the fix, only DerivativeTicker is emitted; funding_rate/funding_timestamp
    fields are still present on the DerivativeTicker itself.
    """
    msg = json.loads((FIXTURES / "ticker_swap.json").read_text())
    out = list(normalize_message(msg, local_ts=9, venue="okx"))
    dt_list = [r for r in out if isinstance(r, DerivativeTicker)]
    fn_list = [r for r in out if isinstance(r, Funding)]
    assert len(dt_list) == 1, f"Expected 1 DerivativeTicker, got {len(dt_list)}"
    assert len(fn_list) == 0, (
        f"Expected 0 Funding records from tickers channel (got {len(fn_list)}); "
        "Funding must come exclusively from the funding-rate channel"
    )
    # DerivativeTicker still carries the funding fields
    dt = dt_list[0]
    assert dt.funding_rate == 0.0001
    assert dt.funding_timestamp == 1700003600000 * 1_000_000


# ---------------------------------------------------------------------------
# Verify _side() is NOT duplicated between normalize.py and backfill.py
# ---------------------------------------------------------------------------


def test_side_helper_not_duplicated() -> None:
    """_side() in normalize.py and backfill.py must share one implementation.

    After deduplication, backfill.py must import _side from normalize.py
    (or a shared location) rather than defining its own copy.
    """
    from crypcodile.exchanges.okx import backfill as bf_mod
    from crypcodile.exchanges.okx import normalize as norm_mod

    # After deduplication, backfill._side must be the same object as normalize._side
    # OR backfill must not define _side at all (it imports from normalize or shared util).

    # The canonical test: they must point to the same function
    assert bf_mod._side is norm_mod._side, (
        "backfill._side and normalize._side are different functions — "
        "_side() is duplicated. Deduplicate by importing from one module."
    )
