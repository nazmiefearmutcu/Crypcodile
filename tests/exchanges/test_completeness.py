"""Task 5.4: Options/derivative completeness golden tests.

Cross-checks each connector against appendix §1 field table to ensure:
- open_interest is emitted (REST poll or WS where available)
- funding is emitted at the correct cadence with interval_hours set
- full options greeks are present where the exchange publishes them

All tests use injected fixtures — no live network calls.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from crocodile.schema.enums import OptType
from crocodile.schema.records import (
    DerivativeTicker,
    Funding,
    Liquidation,
    OpenInterest,
    OptionsChain,
)

BINANCE_FIX = pathlib.Path(__file__).parent / "binance/fixtures"
DERIBIT_FIX = pathlib.Path(__file__).parent / "deribit/fixtures"
BYBIT_FIX = pathlib.Path(__file__).parent / "bybit/fixtures"
OKX_FIX = pathlib.Path(__file__).parent / "okx/fixtures"


# ---------------------------------------------------------------------------
# Deribit completeness
# ---------------------------------------------------------------------------


def test_deribit_ticker_perp_emits_open_interest() -> None:
    """Deribit perpetual ticker → DerivativeTicker must carry open_interest."""
    from crocodile.exchanges.deribit.normalize import normalize_message

    msg = json.loads((DERIBIT_FIX / "ticker_perp.json").read_text())
    out = list(normalize_message(msg, local_ts=7))
    dt_list = [r for r in out if isinstance(r, DerivativeTicker)]
    assert len(dt_list) == 1
    dt = dt_list[0]
    # open_interest field from Deribit ticker (appendix §1 + §3.1)
    assert dt.open_interest == 12345.0, (
        f"Expected open_interest=12345.0 from Deribit ticker, got {dt.open_interest}"
    )


def test_deribit_ticker_perp_funding_has_interval_hours() -> None:
    """Funding records derived from Deribit ticker must set interval_hours=8.

    Deribit perp funding settles every 8 hours (appendix §8: 'Funding: USDⓂ now 4h
    cadence' is Binance-specific; Deribit is 8h).  The live Funding record derived
    from current_funding/funding_8h must document this cadence.
    """
    from crocodile.exchanges.deribit.normalize import normalize_message

    msg = json.loads((DERIBIT_FIX / "ticker_perp.json").read_text())
    out = list(normalize_message(msg, local_ts=7))
    fn_list = [r for r in out if isinstance(r, Funding)]
    assert len(fn_list) == 1
    fn = fn_list[0]
    # Deribit perpetual funding is 8-hourly
    assert fn.interval_hours == 8, (
        f"Expected interval_hours=8 for Deribit Funding, got {fn.interval_hours}"
    )


def test_deribit_options_chain_all_greeks() -> None:
    """Deribit option ticker → OptionsChain must include all BS greeks incl. rho."""
    from crocodile.exchanges.deribit.normalize import normalize_message

    msg = json.loads((DERIBIT_FIX / "ticker_option.json").read_text())
    out = list(normalize_message(msg, local_ts=7))
    oc_list = [r for r in out if isinstance(r, OptionsChain)]
    assert len(oc_list) == 1
    oc = oc_list[0]
    # Deribit exposes all 5 BS greeks (delta/gamma/vega/theta/rho)
    assert oc.delta == 0.5
    assert oc.gamma == 0.001
    assert oc.vega == 12.0
    assert oc.theta == -3.0
    assert oc.rho == 1.0, (
        f"Expected rho=1.0 from Deribit option ticker, got {oc.rho}"
    )


def test_deribit_rest_funding_has_interval_hours() -> None:
    """Deribit REST funding backfill (interest_8h path) must set interval_hours=8."""
    from crocodile.exchanges.deribit.backfill import parse_funding_page

    raw = json.loads((DERIBIT_FIX / "rest_funding.json").read_text())
    records = list(parse_funding_page(raw, symbol="BTC-PERPETUAL", local_ts=0))
    assert len(records) >= 1
    for fn in records:
        assert isinstance(fn, Funding)
        assert fn.interval_hours == 8, (
            f"Deribit REST Funding must have interval_hours=8, got {fn.interval_hours}"
        )


# ---------------------------------------------------------------------------
# Binance completeness
# ---------------------------------------------------------------------------


def test_binance_markprice_does_not_carry_open_interest() -> None:
    """Binance @markPrice does NOT include open_interest (no field in the event).

    open_interest for Binance comes from the REST backfill
    (/fapi/v1/openInterest, /futures/data/openInterestHist).  The DerivativeTicker
    emitted from @markPrice must have open_interest=None; callers must use the
    backfill path.
    """
    from crocodile.exchanges.binance.normalize import normalize_message

    msg = json.loads((BINANCE_FIX / "usdm_markprice.json").read_text())
    out = list(normalize_message(msg, local_ts=1, venue="binance-usdm"))
    dt_list = [r for r in out if isinstance(r, DerivativeTicker)]
    assert len(dt_list) == 1
    dt = dt_list[0]
    # markPrice event has no openInterest → must be None (not fabricated)
    assert dt.open_interest is None, (
        f"DerivativeTicker from @markPrice must have open_interest=None, got {dt.open_interest}"
    )


def test_binance_markprice_funding_interval_hours() -> None:
    """Binance @markPrice Funding must set interval_hours=4.

    Appendix §3.2: 'USDⓂ funding settles every 4h (00/04/08/12/16/20 UTC) as of 2025.'
    The Funding record emitted from the @markPrice branch must document this cadence.
    """
    from crocodile.exchanges.binance.normalize import normalize_message

    msg = json.loads((BINANCE_FIX / "usdm_markprice.json").read_text())
    out = list(normalize_message(msg, local_ts=1, venue="binance-usdm"))
    fn_list = [r for r in out if isinstance(r, Funding)]
    assert len(fn_list) == 1, (
        f"Expected exactly 1 Funding record from @markPrice, got {len(fn_list)}"
    )
    fn = fn_list[0]
    assert fn.interval_hours == 4, (
        f"Binance USDM @markPrice Funding must have interval_hours=4 (appendix §3.2),"
        f" got {fn.interval_hours}"
    )


def test_binance_rest_open_interest_emits_record() -> None:
    """Binance REST open-interest snapshot must parse to OpenInterest correctly."""
    from crocodile.exchanges.binance.backfill import parse_open_interest

    raw = json.loads((BINANCE_FIX / "rest_open_interest.json").read_text())
    oi = parse_open_interest(raw, venue="binance-usdm", local_ts=0)
    assert isinstance(oi, OpenInterest)
    assert oi.open_interest > 0
    assert oi.exchange_ts is not None


def test_binance_eapi_optionmarkprice_emits_options_chain() -> None:
    """Binance EAPI @optionMarkPrice → OptionsChain with greeks.

    Appendix §3.2: 'Options: {underlying}@optionMarkPrice — mp(mark), greeks d/g/t/v,
    b/a(buy/sell IV), vo(vol), best bo/ao.'

    Fields:
    - s  → symbol_raw
    - mp → mark_price
    - d  → delta
    - g  → gamma
    - t  → theta
    - v  → vega
    - b  → bid_iv  (buy IV)
    - a  → ask_iv  (sell IV)
    - vo → mark_iv (vol)
    - oi → open_interest
    - E  → exchange_ts (event time, ms)
    """
    from crocodile.exchanges.binance.normalize import normalize_message

    msg = json.loads((BINANCE_FIX / "eapi_option_markprice.json").read_text())
    out = list(normalize_message(msg, local_ts=42, venue="binance-eapi"))
    oc_list = [r for r in out if isinstance(r, OptionsChain)]
    assert len(oc_list) >= 1, (
        f"Expected ≥1 OptionsChain from @optionMarkPrice, got {len(oc_list)}: {out}"
    )
    oc = oc_list[0]
    assert oc.symbol_raw == "BTC-231215-50000-C"
    assert oc.exchange == "binance-eapi"
    assert oc.mark_price == pytest.approx(0.05)
    assert oc.mark_iv == pytest.approx(65.0)   # vo field
    assert oc.bid_iv == pytest.approx(64.0)    # b field
    assert oc.ask_iv == pytest.approx(66.0)    # a field
    assert oc.delta == pytest.approx(0.5)      # d field
    assert oc.gamma == pytest.approx(0.001)    # g field
    assert oc.theta == pytest.approx(-3.0)     # t field
    assert oc.vega == pytest.approx(12.0)      # v field
    assert oc.open_interest == pytest.approx(10.0)
    # opt_type and strike must be parsed from symbol "BTC-231215-50000-C"
    assert oc.opt_type == OptType.CALL
    assert oc.strike == pytest.approx(50000.0)
    # exchange_ts from E (event time ms → ns)
    assert oc.exchange_ts == 1700000000000 * 1_000_000
    assert oc.local_ts == 42


def test_binance_eapi_optionmarkprice_put() -> None:
    """Binance EAPI @optionMarkPrice for a PUT option parses opt_type=PUT."""
    from crocodile.exchanges.binance.normalize import normalize_message

    msg = {
        "stream": "BTC@optionMarkPrice",
        "data": [
            {
                "e": "option_mark_price_update",
                "E": 1700000000000,
                "s": "BTC-231215-45000-P",
                "mp": "0.03",
                "r": "0.0",
                "d": "-0.4",
                "g": "0.0008",
                "t": "-2.5",
                "v": "10.0",
                "b": "62.0",
                "a": "64.0",
                "vo": "63.0",
                "oi": "5.0",
                "T": 1700000000000,
            }
        ],
    }
    out = list(normalize_message(msg, local_ts=1, venue="binance-eapi"))
    oc_list = [r for r in out if isinstance(r, OptionsChain)]
    assert len(oc_list) == 1
    oc = oc_list[0]
    assert oc.opt_type == OptType.PUT
    assert oc.strike == pytest.approx(45000.0)


# ---------------------------------------------------------------------------
# Bybit completeness
# ---------------------------------------------------------------------------


def test_bybit_ticker_linear_open_interest() -> None:
    """Bybit linear perpetual ticker → DerivativeTicker must carry open_interest."""
    from crocodile.exchanges.bybit.normalize import normalize_message

    msg = json.loads((BYBIT_FIX / "ticker_linear.json").read_text())
    out = list(normalize_message(msg, local_ts=9, venue="bybit"))
    dt_list = [r for r in out if isinstance(r, DerivativeTicker)]
    assert len(dt_list) == 1
    dt = dt_list[0]
    assert dt.open_interest is not None and dt.open_interest > 0, (
        f"Expected open_interest > 0 from Bybit ticker, got {dt.open_interest}"
    )


def test_bybit_funding_interval_hours() -> None:
    """Bybit Funding records from ticker must have interval_hours=8 (8h default cadence)."""
    from crocodile.exchanges.bybit.normalize import normalize_message

    msg = json.loads((BYBIT_FIX / "ticker_linear.json").read_text())
    out = list(normalize_message(msg, local_ts=9, venue="bybit"))
    fn_list = [r for r in out if isinstance(r, Funding)]
    assert len(fn_list) == 1
    fn = fn_list[0]
    assert fn.interval_hours == 8, (
        f"Expected interval_hours=8 for Bybit Funding, got {fn.interval_hours}"
    )


def test_bybit_options_all_greeks() -> None:
    """Bybit option ticker → OptionsChain must include all available greeks."""
    from crocodile.exchanges.bybit.normalize import normalize_message
    from crocodile.instruments.registry import Instrument, InstrumentRegistry, Kind

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
    msg = json.loads((BYBIT_FIX / "ticker_option.json").read_text())
    out = list(normalize_message(msg, local_ts=10, venue="bybit", registry=reg))
    oc_list = [r for r in out if isinstance(r, OptionsChain)]
    assert len(oc_list) == 1
    oc = oc_list[0]
    # Bybit option ticker exposes delta/gamma/vega/theta (no rho per spec)
    assert oc.delta is not None and oc.delta != 0.0
    assert oc.gamma is not None
    assert oc.vega is not None
    assert oc.theta is not None


# ---------------------------------------------------------------------------
# OKX completeness
# ---------------------------------------------------------------------------


def test_okx_open_interest_channel_emits_oi_value() -> None:
    """OKX open-interest channel → OpenInterest must include open_interest_value (oiCcy)."""
    from crocodile.exchanges.okx.normalize import normalize_message

    msg = json.loads((OKX_FIX / "open_interest.json").read_text())
    out = list(normalize_message(msg, local_ts=11, venue="okx"))
    oi_list = [r for r in out if isinstance(r, OpenInterest)]
    assert len(oi_list) == 1
    oi = oi_list[0]
    # oiCcy field maps to open_interest_value
    assert oi.open_interest_value is not None, (
        "OKX open-interest must include open_interest_value (oiCcy field)"
    )
    assert oi.open_interest_value > 0


def test_okx_funding_rate_channel_interval_hours() -> None:
    """OKX funding-rate channel → Funding must have interval_hours=8."""
    from crocodile.exchanges.okx.normalize import normalize_message

    msg = json.loads((OKX_FIX / "funding_rate.json").read_text())
    out = list(normalize_message(msg, local_ts=10, venue="okx"))
    fn_list = [r for r in out if isinstance(r, Funding)]
    assert len(fn_list) == 1
    fn = fn_list[0]
    assert fn.interval_hours == 8, (
        f"Expected interval_hours=8 for OKX Funding, got {fn.interval_hours}"
    )


def test_okx_options_chain_all_greeks() -> None:
    """OKX option-summary → OptionsChain must include all available greeks (no rho in OKX)."""
    from crocodile.exchanges.okx.normalize import normalize_message

    msg = json.loads((OKX_FIX / "option_summary.json").read_text())
    out = list(normalize_message(msg, local_ts=13, venue="okx"))
    oc_list = [r for r in out if isinstance(r, OptionsChain)]
    assert len(oc_list) == 1
    oc = oc_list[0]
    assert oc.delta is not None
    assert oc.gamma is not None
    assert oc.vega is not None
    assert oc.theta is not None
    # mark_iv, bid_iv, ask_iv from markVol/bidVol/askVol
    assert oc.mark_iv is not None
    assert oc.bid_iv is not None
    assert oc.ask_iv is not None


# ---------------------------------------------------------------------------
# Coinbase completeness — spot only, no derivatives
# ---------------------------------------------------------------------------


def test_coinbase_no_funding_emitted() -> None:
    """Coinbase is spot-only; no Funding records must be emitted from any message.

    This validates the documented 'no funding/OI/liquidation' constraint
    (appendix §7: 'n/a (spot only)' for funding/OI/liquidation).
    """
    from crocodile.exchanges.coinbase.normalize import normalize_message

    # A ticker message is the richest message type Coinbase WS provides
    ticker_msg = {
        "type": "ticker",
        "product_id": "BTC-USD",
        "best_bid": "50000.0",
        "best_bid_size": "0.5",
        "best_ask": "50001.0",
        "best_ask_size": "0.3",
        "time": "2023-11-14T22:13:20.000000Z",
    }
    out = list(normalize_message(ticker_msg, local_ts=1))
    funding = [r for r in out if isinstance(r, Funding)]
    oi = [r for r in out if isinstance(r, OpenInterest)]
    liq = [r for r in out if isinstance(r, Liquidation)]
    assert funding == [], "Coinbase must not emit Funding records (spot-only)"
    assert oi == [], "Coinbase must not emit OpenInterest records (spot-only)"
    assert liq == [], "Coinbase must not emit Liquidation records (spot-only)"
