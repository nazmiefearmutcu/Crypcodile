"""Every analytics declaration, called the way a surface will call it.

A declaration is a claim that some ``(ctx, params)`` produces an answer, and the only thing
that makes the claim checkable is running it. So each test here reaches the implementation
through :data:`~crocodile.core.capability.REGISTRY` rather than importing the adapter
directly: a capability wired to the wrong function, or declared under a name nothing looks
up, fails here rather than on whichever surface Phase 2 projects first.

The lake is built once per test from real records through ``ParquetSink``, because the four
capabilities that read the options chain do so through a DuckDB view over parquet, and a
stubbed catalog would exercise the adapter's argument order and nothing else.
"""

from __future__ import annotations

import asyncio
import datetime
import math
from pathlib import Path
from typing import Any

import msgspec.structs
import polars as pl
import pytest

from crocodile.capabilities import load_all
from crocodile.capabilities.analytics import (
    BasisParams,
    ChaosScoreParams,
    FundingAprParams,
    FundingPredictParams,
    IndicatorParams,
    IvSurfaceParams,
    LabelTransfersParams,
    LiquidityDepthParams,
    OfiParams,
    PerpBasisParams,
    RiskReversalParams,
    SlippageParams,
    SmartMoneyParams,
    SpotFutureBasisParams,
    TermStructureParams,
    VolSkewParams,
    WhaleAlertsParams,
)
from crocodile.core.capability import REGISTRY, AssetClass, CapabilityContext
from crocodile.core.config import Settings
from crocodile.core.schema.enums import OptType, Side
from crocodile.core.schema.provenance import Provenance, provenance_fields
from crocodile.core.schema.records import (
    BookSnapshot,
    DepthProfile,
    DerivativeTicker,
    Funding,
    Holding13F,
    InsiderTransaction,
    Liquidation,
    OptionsChain,
    Quote,
    Trade,
)
from crocodile.core.store.catalog import Catalog
from crocodile.core.store.parquet_sink import ParquetSink
from crocodile.equity.analytics.options import bsm_price

_BASE_NS = 1_704_067_200_000_000_000  # 2024-01-01 00:00:00 UTC
_SEC_NS = 1_000_000_000
_DAY_NS = 86_400 * _SEC_NS
_YEAR_NS = 365 * _DAY_NS

_T1 = _BASE_NS + 1 * _SEC_NS
_T2 = _BASE_NS + 2 * _SEC_NS
_T3 = _BASE_NS + 3 * _SEC_NS
_T4 = _BASE_NS + 4 * _SEC_NS

_SPOT = "deribit:BTC-SPOT"
_PERP = "deribit:BTC-PERPETUAL"
_FUTURE = "deribit:BTC-27JUN25"
_POOL = "base_onchain:DEGEN-WETH"
_UNDERLYING = "BTC"

_E1 = _BASE_NS + _YEAR_NS
_E2 = _BASE_NS + 2 * _YEAR_NS
_FUTURE_EXPIRY = _T3 + _YEAR_NS

# Calls quoted uniformly below puts, so the risk reversal is the same number whichever
# strike the 25-delta rule lands on. Golden numbers that survive the delta search are the
# only kind worth asserting here — the search is volsurface's subject, not this file's.
_CALL_IV = 0.50
_PUT_IV = 0.70
_E2_CALL_IV = 0.45


def _trade(symbol: str, ts: int, price: float, amount: float, tid: str) -> Trade:
    return Trade(
        source="deribit",
        symbol=symbol,
        symbol_raw=symbol.split(":", 1)[-1],
        source_ts=ts,
        local_ts=ts,
        asset_class=AssetClass.CRYPTO,
        id=tid,
        price=price,
        amount=amount,
        side=Side.BUY,
    )


def _ticker(ts: int, mark: float, index: float) -> DerivativeTicker:
    return DerivativeTicker(
        source="deribit",
        symbol=_PERP,
        symbol_raw="BTC-PERPETUAL",
        source_ts=ts,
        local_ts=ts,
        asset_class=AssetClass.CRYPTO,
        mark_price=mark,
        index_price=index,
    )


def _funding(ts: int, rate: float) -> Funding:
    return Funding(
        source="deribit",
        symbol=_PERP,
        symbol_raw="BTC-PERPETUAL",
        source_ts=ts,
        local_ts=ts,
        asset_class=AssetClass.CRYPTO,
        funding_rate=rate,
        funding_timestamp=ts,
        interval_hours=8,
    )


def _liquidation(ts: int, price: float, amount: float) -> Liquidation:
    return Liquidation(
        source="deribit",
        symbol=_PERP,
        symbol_raw="BTC-PERPETUAL",
        source_ts=ts,
        local_ts=ts,
        asset_class=AssetClass.CRYPTO,
        price=price,
        amount=amount,
        side=Side.SELL,
        id="liq-1",
    )


def _snapshot(ts: int, sequence_id: int, scale: float) -> BookSnapshot:
    """A five-level book centred on 100.0, with every size multiplied by ``scale``."""
    return BookSnapshot(
        source="base_onchain",
        symbol=_POOL,
        symbol_raw="DEGEN-WETH",
        source_ts=ts,
        local_ts=ts,
        asset_class=AssetClass.CRYPTO,
        bids=[
            (100.0, 10.0 * scale),
            (99.0, 5.0 * scale),
            (98.0, 3.0 * scale),
            (97.0, 2.0 * scale),
            (94.0, 1.0 * scale),
        ],
        asks=[
            (100.0, 8.0 * scale),
            (101.0, 4.0 * scale),
            (102.0, 3.0 * scale),
            (103.0, 2.0 * scale),
            (106.0, 1.0 * scale),
        ],
        depth=5,
        sequence_id=sequence_id,
    )


def _option(strike: float, expiry: int, opt_type: OptType, mark_iv: float) -> OptionsChain:
    symbol = f"deribit:BTC-{expiry}-{int(strike)}-{opt_type.value}"
    return OptionsChain(
        source="deribit",
        symbol=symbol,
        symbol_raw=symbol.split(":", 1)[-1],
        source_ts=_BASE_NS,
        local_ts=_BASE_NS,
        asset_class=AssetClass.CRYPTO,
        underlying=_UNDERLYING,
        underlying_price=100.0,
        strike=strike,
        expiry=expiry,
        opt_type=opt_type,
        mark_price=10.0,
        mark_iv=mark_iv,
    )


def _records() -> list[Any]:
    """One lake covering every channel this batch reads."""
    options: list[Any] = [
        _option(strike, _E1, opt_type, iv)
        for strike in (90.0, 100.0, 110.0)
        for opt_type, iv in ((OptType.CALL, _CALL_IV), (OptType.PUT, _PUT_IV))
    ]
    return [
        # Spot leg for `basis` and `spot-future-basis`.
        _trade(_SPOT, _T1, 100.0, 1.0, "spot-1"),
        # Perpetual mark/index for `basis` and `perp-basis`.
        _ticker(_T2, 101.5, 101.0),
        # Dated future for `spot-future-basis`.
        _trade(_FUTURE, _T3, 105.0, 1.0, "fut-1"),
        # Whale-sized and small prints, plus a liquidation, for `whale-alerts`.
        _trade(_PERP, _T1, 50_000.0, 3.0, "whale-1"),
        _trade(_PERP, _T2, 50_000.0, 1.0, "minnow-1"),
        _liquidation(_T3, 50_000.0, 4.0),
        # Two book sequences for `ofi` and `liquidity-depth`.
        _snapshot(_BASE_NS, 100, 1.0),
        _snapshot(_BASE_NS + _SEC_NS, 101, 2.0),
        # Funding settlements for `funding-apr`.
        _funding(_T1, 0.0001),
        _funding(_T2, -0.0002),
        _funding(_T3, 0.0003),
        # The options chain the four volsurface capabilities read.
        *options,
        _option(100.0, _E2, OptType.CALL, _E2_CALL_IV),
    ]


async def _write(data_dir: Path, records: list[Any]) -> None:
    sink = ParquetSink(data_dir, max_buffer_rows=10_000, flush_interval_seconds=9999)
    for record in records:
        await sink.put(record)
    await sink.flush()


@pytest.fixture
def ctx(tmp_path: Path) -> CapabilityContext:
    """A context over a real lake, as a surface would build one."""
    load_all()
    asyncio.run(_write(tmp_path, _records()))
    return CapabilityContext(
        catalog=Catalog(tmp_path),
        settings=Settings(data_dir=tmp_path),
        asset_class=AssetClass.CRYPTO,
    )


@pytest.fixture
def empty_ctx(tmp_path: Path) -> CapabilityContext:
    """A context over a lake with nothing in it, for the pure capabilities.

    They read no channel, and handing them a populated lake would let a test pass because
    of data the implementation never touches.
    """
    load_all()
    return CapabilityContext(
        catalog=Catalog(tmp_path),
        settings=Settings(data_dir=tmp_path),
        asset_class=AssetClass.CRYPTO,
    )


def _call(name: str, ctx: CapabilityContext, params: Any) -> Any:
    """Invoke a capability through the registry, which is the path a surface takes."""
    return REGISTRY[name].impls[AssetClass.CRYPTO].fn(ctx, params)


# ---------------------------------------------------------------------------
# The basis family
# ---------------------------------------------------------------------------


def test_basis_pairs_the_perp_mark_with_the_nearest_prior_spot_print(
    ctx: CapabilityContext,
) -> None:
    rows = _call("basis", ctx, BasisParams(_SPOT, _PERP, _T1, _T4))
    assert len(rows) == 1
    row = rows.row(0, named=True)
    assert row["local_ts"] == _T2
    assert row["spot_price"] == pytest.approx(100.0)
    assert row["perp_price"] == pytest.approx(101.5)
    assert row["basis"] == pytest.approx(1.5)
    assert row["basis_pct"] == pytest.approx(0.015)


def test_perp_basis_reports_mark_against_index_from_the_ticker_channel(
    ctx: CapabilityContext,
) -> None:
    rows = _call("perp-basis", ctx, PerpBasisParams(_PERP, _T1, _T4))
    assert len(rows) == 1
    row = rows.row(0, named=True)
    assert row["mark_price"] == pytest.approx(101.5)
    assert row["index_price"] == pytest.approx(101.0)
    assert row["basis"] == pytest.approx(0.5)
    assert row["basis_pct"] == pytest.approx(0.5 / 101.0)


def test_spot_future_basis_omits_the_annualised_column_when_no_expiry_is_given(
    ctx: CapabilityContext,
) -> None:
    rows = _call("spot-future-basis", ctx, SpotFutureBasisParams(_FUTURE, _SPOT, _T1, _T4))
    assert len(rows) == 1
    assert "annualized_pct" not in rows.columns
    row = rows.row(0, named=True)
    assert row["future_price"] == pytest.approx(105.0)
    assert row["spot_price"] == pytest.approx(100.0)
    assert row["basis_pct"] == pytest.approx(0.05)


def test_spot_future_basis_annualises_when_the_expiry_rest_never_accepted_is_supplied(
    ctx: CapabilityContext,
) -> None:
    """The parameter ``GET /spot-future-basis`` dropped, and what it changes.

    One year to expiry means the annualised basis is the raw basis, which is what makes
    this assertion about the column existing at all rather than about the arithmetic.
    """
    params = SpotFutureBasisParams(_FUTURE, _SPOT, _T1, _T4, expiry_ns=_FUTURE_EXPIRY)
    rows = _call("spot-future-basis", ctx, params)
    assert "annualized_pct" in rows.columns
    assert rows.row(0, named=True)["annualized_pct"] == pytest.approx(0.05)


# ---------------------------------------------------------------------------
# Funding
# ---------------------------------------------------------------------------


def test_funding_apr_annualises_each_settlement_and_accumulates_the_rate(
    ctx: CapabilityContext,
) -> None:
    rows = _call("funding-apr", ctx, FundingAprParams(_PERP, _T1, _T4))
    assert len(rows) == 3
    assert rows["apr"][0] == pytest.approx(0.0001 * 1095.0)
    assert rows["cumulative_funding"][-1] == pytest.approx(0.0002)


def test_funding_predict_forecasts_from_the_supplied_history_and_reads_no_lake(
    empty_ctx: CapabilityContext,
) -> None:
    """The rates arrive in params, so an empty lake must not change the answer.

    Three rates is deliberate. The predictor builds lag-1, lag-2 and lag-3 features and
    drops incomplete rows, so a three-row history can never leave a trainable row however
    the environment answers ``import xgboost`` — which is what makes the rolling mean the
    answer here rather than a number that depends on whether the ``ml`` extra is installed.
    """
    result = _call("funding-predict", empty_ctx, FundingPredictParams((0.1, 0.2, 0.3)))
    assert result["method"] == "rolling_mean"
    assert result["predicted_funding_rate"] == pytest.approx(0.2)
    assert result["n_history"] == 3
    assert result["window_size"] == 5


def test_funding_predict_honours_a_window_narrower_than_the_history(
    empty_ctx: CapabilityContext,
) -> None:
    """``window_size`` is the parameter all three surfaces agreed on; it has to bite."""
    params = FundingPredictParams((0.1, 0.2, 0.3), window_size=2)
    result = _call("funding-predict", empty_ctx, params)
    assert result["window_size"] == 2
    assert result["predicted_funding_rate"] == pytest.approx(0.25)


# ---------------------------------------------------------------------------
# The options surface family
# ---------------------------------------------------------------------------


def test_iv_surface_returns_one_row_per_strike_expiry_and_option_type(
    ctx: CapabilityContext,
) -> None:
    rows = _call("iv-surface", ctx, IvSurfaceParams(_UNDERLYING, _BASE_NS))
    assert len(rows) == 7
    assert set(rows["source"].to_list()) == {"mark_iv"}
    otm_call = rows.filter((pl.col("strike") == 110.0) & (pl.col("opt_type") == "C"))
    assert otm_call["moneyness"][0] == pytest.approx(1.1)
    assert otm_call["iv"][0] == pytest.approx(_CALL_IV)


def test_term_structure_reduces_the_surface_to_one_atm_row_per_expiry(
    ctx: CapabilityContext,
) -> None:
    rows = _call("term-structure", ctx, TermStructureParams(_UNDERLYING, _BASE_NS))
    assert rows["expiry"].to_list() == [_E1, _E2]
    assert rows["atm_strike"].to_list() == [100.0, 100.0]
    assert rows["days_to_expiry"][0] == pytest.approx(365.0)


def test_vol_skew_returns_only_the_requested_expiry(ctx: CapabilityContext) -> None:
    rows = _call("vol-skew", ctx, VolSkewParams(_UNDERLYING, _E1, _BASE_NS))
    assert len(rows) == 6
    assert rows["strike"].to_list() == sorted(rows["strike"].to_list())
    assert all(delta is not None for delta in rows["delta"].to_list())


def test_risk_reversal_prices_the_call_skew_against_the_put_skew(
    ctx: CapabilityContext,
) -> None:
    """Every call is quoted 20 vols under every put, so the risk reversal is -0.20.

    Which strikes the 25-delta search lands on is volsurface's business and deliberately
    not pinned here; a flat pair of skews makes the answer the same for every choice it
    could make, which is what leaves this test measuring the capability rather than the
    search.
    """
    result = _call("risk-reversal", ctx, RiskReversalParams(_UNDERLYING, _E1, _BASE_NS))
    assert result["risk_reversal"] == pytest.approx(_CALL_IV - _PUT_IV)
    assert result["butterfly"] is not None


def test_risk_reversal_reports_two_holes_when_the_expiry_has_no_chain(
    ctx: CapabilityContext,
) -> None:
    """An expiry nobody quoted is not a zero risk reversal."""
    params = RiskReversalParams(_UNDERLYING, _E1 + _DAY_NS, _BASE_NS)
    assert _call("risk-reversal", ctx, params) == {"risk_reversal": None, "butterfly": None}


# ---------------------------------------------------------------------------
# Microstructure
# ---------------------------------------------------------------------------


def test_ofi_nets_the_bid_and_ask_size_changes_within_a_bin(ctx: CapabilityContext) -> None:
    """Both sides thicken between the two snapshots: bids by 10, asks by 8, so OFI is 2."""
    rows = _call("ofi", ctx, OfiParams(_POOL, _BASE_NS, _T4))
    assert len(rows) == 1
    row = rows.row(0, named=True)
    assert row["timestamp"] == _BASE_NS
    assert row["ofi"] == pytest.approx(2.0)
    assert row["best_bid"] == pytest.approx(100.0)


def test_ofi_bins_at_one_minute_unless_told_otherwise(ctx: CapabilityContext) -> None:
    """The default this port had to choose between three surfaces' answers.

    A one-second interval separates the two snapshots into their own bins, which is how
    the default's effect is visible rather than asserted.
    """
    default_bins = _call("ofi", ctx, OfiParams(_POOL, _BASE_NS, _T4))
    per_second = _call("ofi", ctx, OfiParams(_POOL, _BASE_NS, _T4, interval="1s"))
    assert OfiParams(_POOL, 0, 1).interval == "1m"
    assert len(default_bins) == 1
    assert len(per_second) == 1  # one step exists, and it lands in the second bucket
    assert per_second.row(0, named=True)["timestamp"] == _BASE_NS + _SEC_NS


def test_liquidity_depth_sums_resting_size_inside_each_percent_band(
    ctx: CapabilityContext,
) -> None:
    rows = _call("liquidity-depth", ctx, LiquidityDepthParams(_POOL))
    assert rows["block"].to_list() == [100, 101]
    first = rows.row(0, named=True)
    assert first["bid_depth_1pct"] == pytest.approx(15.0)
    assert first["ask_depth_1pct"] == pytest.approx(12.0)
    assert first["bid_depth_5pct"] == pytest.approx(20.0)
    # The second sequence carries twice the size at every level.
    assert rows.row(1, named=True)["bid_depth_1pct"] == pytest.approx(30.0)


# ---------------------------------------------------------------------------
# Flow tracking
# ---------------------------------------------------------------------------


def test_whale_alerts_keeps_both_the_trade_and_the_liquidation_above_the_threshold(
    ctx: CapabilityContext,
) -> None:
    rows = _call("whale-alerts", ctx, WhaleAlertsParams(_PERP, _T1, _T4))
    assert rows["event_type"].to_list() == ["Trade", "Liquidation"]
    assert rows["usd_value"].to_list() == [pytest.approx(150_000.0), pytest.approx(200_000.0)]


def test_whale_alerts_defaults_to_the_threshold_the_cli_chose_not_the_rest_zero(
    ctx: CapabilityContext,
) -> None:
    """``min_usd=0`` returns the whole tape, which is the reading the default rejects."""
    assert WhaleAlertsParams(_PERP, _T1, _T4).min_usd == 100_000.0
    unfiltered = _call("whale-alerts", ctx, WhaleAlertsParams(_PERP, _T1, _T4, min_usd=0.0))
    assert len(unfiltered) == 3
    assert len(_call("whale-alerts", ctx, WhaleAlertsParams(_PERP, _T1, _T4))) == 2


def test_smart_money_nets_flow_per_watched_address_and_ignores_the_rest(
    empty_ctx: CapabilityContext,
) -> None:
    transfers = (
        {"from": "0xSMART", "to": "0xother", "usd_value": 100.0, "timestamp": 1},
        {"from": "0xother", "to": "0xSMART", "usd_value": 250.0, "timestamp": 2},
        {"from": "0xnobody", "to": "0xelse", "usd_value": 900.0, "timestamp": 3},
    )
    rows = _call(
        "smart-money", empty_ctx, SmartMoneyParams(transfers, {"0xsmart": "vitalik"})
    )
    assert len(rows) == 1
    assert rows[0]["net_flow_usd"] == pytest.approx(150.0)
    assert rows[0]["total_volume_usd"] == pytest.approx(350.0)
    assert rows[0]["tx_count"] == 2
    assert rows[0]["label"] == "vitalik"


def test_smart_money_accepts_the_nested_watchlist_shapes_rest_accepted(
    empty_ctx: CapabilityContext,
) -> None:
    """``normalize_watchlist`` is in the adapter, not the surface, so the shapes survive."""
    transfers = ({"from": "0xsmart", "to": "0xother", "usd_value": 10.0, "timestamp": 1},)
    rows = _call(
        "smart-money", empty_ctx, SmartMoneyParams(transfers, {"addresses": ["0xsmart"]})
    )
    assert [row["address"] for row in rows] == ["0xsmart"]


def test_label_transfers_annotates_both_sides_and_flags_the_known_ones(
    empty_ctx: CapabilityContext,
) -> None:
    transfers = (
        {"from": "0xsmart", "to": "0xother", "usd_value": 100.0},
        {"from": "0xnobody", "to": "0xelse", "usd_value": 100.0},
    )
    rows = _call(
        "label-transfers", empty_ctx, LabelTransfersParams(transfers, {"0xsmart": "vitalik"})
    )
    assert [row["is_known"] for row in rows] == [True, False]
    assert rows[0]["from_label"] == "vitalik"
    assert rows[0]["to_label"] == ""


def test_label_transfers_treats_an_absent_notional_as_below_any_threshold(
    empty_ctx: CapabilityContext,
) -> None:
    """``min_usd=None`` and ``min_usd=0.0`` are different requests, not the same one.

    A row carrying no parseable ``usd_value`` is dropped by the filter at *any* threshold,
    including zero, and kept when the filter does not run — which is why the field is
    ``float | None`` on all three surfaces rather than a defaulted zero.
    """
    transfers = (
        {"from": "0xsmart", "to": "0xother", "usd_value": 100.0},
        {"from": "0xsmart", "to": "0xother"},
    )
    watchlist = {"0xsmart": "v"}
    assert len(_call("label-transfers", empty_ctx, LabelTransfersParams(transfers, watchlist))) == 2
    for threshold in (0.0, 50.0):
        params = LabelTransfersParams(transfers, watchlist, min_usd=threshold)
        assert len(_call("label-transfers", empty_ctx, params)) == 1


def test_label_transfers_drops_the_unknown_rows_only_when_asked(
    empty_ctx: CapabilityContext,
) -> None:
    transfers = (
        {"from": "0xsmart", "to": "0xother"},
        {"from": "0xnobody", "to": "0xelse"},
    )
    params = LabelTransfersParams(transfers, {"0xsmart": "v"}, known_only=True)
    rows = _call("label-transfers", empty_ctx, params)
    assert [row["from"] for row in rows] == ["0xsmart"]


# ---------------------------------------------------------------------------
# Risk
# ---------------------------------------------------------------------------


def test_chaos_score_returns_the_index_alone_and_reads_no_lake(
    empty_ctx: CapabilityContext,
) -> None:
    """All four terms saturated: each normalises to 1.0, so the index is its maximum."""
    params = ChaosScoreParams(
        volatility=1e9,
        stablecoin_deviation=1e9,
        orderbook_imbalance=1.0,
        sequencer_delay=1e9,
    )
    score = _call("chaos-score", empty_ctx, params)
    assert isinstance(score, float)
    assert score == pytest.approx(100.0)


def test_chaos_score_scales_with_each_term_it_is_given(empty_ctx: CapabilityContext) -> None:
    calm = ChaosScoreParams(0.0, 0.0, 0.0, 0.0)
    stressed = ChaosScoreParams(0.1, 0.01, 0.5, 5.0)
    assert _call("chaos-score", empty_ctx, calm) == pytest.approx(0.0)
    assert 0.0 < _call("chaos-score", empty_ctx, stressed) < 100.0


def test_chaos_score_requires_every_reading_rather_than_defaulting_them_to_calm() -> None:
    """The decision this port made against two of the three surfaces.

    A struct that defaulted its four fields would report 0.0 — "perfectly calm" — for a
    caller who supplied nothing, which is a fabricated reading rather than a missing one.
    """
    with pytest.raises(TypeError):
        ChaosScoreParams()  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# The declarations themselves
# ---------------------------------------------------------------------------


_DECLARED = (
    "basis",
    "chaos-score",
    "funding-apr",
    "funding-predict",
    "indicators",
    "iv-surface",
    "label-transfers",
    "liquidity-depth",
    "ofi",
    "perp-basis",
    "risk-reversal",
    "slippage",
    "smart-money",
    "spot-future-basis",
    "term-structure",
    "vol-skew",
    "whale-alerts",
)


def test_the_batch_declares_every_capability_it_owns() -> None:
    """A name that quietly failed to register would make every test above skip its subject."""
    load_all()
    assert set(_DECLARED) <= set(REGISTRY)


@pytest.mark.parametrize("name", _DECLARED)
def test_every_declaration_states_a_summary_and_a_registered_basis(name: str) -> None:
    from crocodile.core.schema.provenance import load_all_bases, registered_bases

    load_all()
    load_all_bases()
    known = registered_bases()
    cap = REGISTRY[name]
    assert cap.summary.strip() and cap.summary.endswith(".")
    for impl in cap.impls.values():
        assert impl.basis in known


_SYMMETRIC = frozenset(
    {
        # OHLCV and a stored book are things both markets report, so these two were
        # symmetric before Phase 3 began.
        "indicators",
        "slippage",
        # The options family, symmetric since M1: one surface in
        # `core.analytics.volsurface`, read through a Black-76 model on the crypto side
        # and a Black-Scholes-Merton one on the equity side.
        "iv-surface",
        "term-structure",
        "vol-skew",
        "risk-reversal",
        # M5's five. `basis`, `perp-basis`, `spot-future-basis` and `funding-apr` read
        # equity legs through `crocodile.equity.analytics.carry`; `funding-predict` binds
        # the one offline forecaster to both classes, on the `indicators` argument rather
        # than the `fn=slippage` one — it reads no lake at all, so there is no channel for
        # the two halves to differ over.
        "basis",
        "funding-apr",
        "funding-predict",
        "perp-basis",
        "spot-future-basis",
        # M4's three, which left PENDING_SYMMETRY together because one method closes all
        # of them: Form 4 for the dated insider transactions and 13F-HR for the
        # institutional positions.
        "whale-alerts",
        "smart-money",
        "label-transfers",
        # Phase 3, this batch: M7 gave `ofi` an equity quote stream to difference, and M6
        # gave `liquidity-depth` a ladder to sum bands over — which is also what unblocked
        # `chaos-score`'s order-book term and with it the whole composite.
        "ofi",
        "liquidity-depth",
        "chaos-score",
    }
)
"""Names this batch owns that no longer need a schedule, and why each stopped needing one.

A bare exclusion list would let a capability leave the gate below by being deleted from
the ledger without gaining an equity half, which is the failure
:func:`test_the_ledger_is_not_hoarding_capabilities_that_became_symmetric` catches in the
other direction. So membership here is asserted rather than assumed: the test below runs
over *every* declared name and checks the two facts against each other.
"""


def test_every_crypto_only_capability_is_scheduled_against_a_spec_method() -> None:
    """The other half of declaring an asymmetric capability — the half with no subject left.

    This was 17 parametrised cases whose first branch, ``if name in _SYMMETRIC: … return``,
    every one of them took: ``_DECLARED - _SYMMETRIC`` is empty, so the two assertions after
    it never ran and what did run duplicated
    :func:`test_every_symmetric_capability_left_the_ledger` exactly — 34 cases asserting the
    same two facts twice.

    The rule still matters and Phase 4 will declare capabilities, so it is not deleted; it
    moved to :func:`~tests.conformance.test_pending_symmetry.assert_asymmetry_is_scheduled`,
    where ``_isolate`` drives it with a fixture capability and proves it catches an
    unscheduled one, an invented method, a name that grew an equity half, and a name nothing
    registered. Here it is applied to this batch's actual crypto-only set, which is empty
    today and says so rather than looking like coverage.
    """
    from tests.conformance.test_pending_symmetry import assert_asymmetry_is_scheduled

    load_all()
    assert_asymmetry_is_scheduled(set(_DECLARED) - _SYMMETRIC)


def test_the_symmetric_list_names_exactly_the_capabilities_that_are_symmetric() -> None:
    """``_SYMMETRIC`` as an assertion rather than as a list the tests below trust.

    :func:`test_every_symmetric_capability_left_the_ledger` checks that everything on the
    list really has both halves. This is the other direction, and it is the one that keeps
    the list from being an exemption: a capability that becomes symmetric without being
    added here would silently move into the *unscheduled asymmetric* set above, where the
    rule would then demand a schedule for a capability that no longer needs one.
    """
    load_all()
    symmetric_now = {
        name
        for name in _DECLARED
        if set(REGISTRY[name].impls) == {AssetClass.CRYPTO, AssetClass.EQUITY}
    }
    assert symmetric_now == set(_SYMMETRIC), (
        f"symmetric and unlisted: {sorted(symmetric_now - _SYMMETRIC)}; "
        f"listed and not symmetric: {sorted(_SYMMETRIC - symmetric_now)}"
    )


@pytest.mark.parametrize("name", _SYMMETRIC)
def test_every_symmetric_capability_left_the_ledger(name: str) -> None:
    """The mirror of the test above, and the half that catches a *reverted* equity half.

    The exclusion list above is what a name leaves by, so a name that leaves it and then
    loses its equity implementation would be asserted about by neither test — which is
    exactly the invisibility ``test_the_irreducible_list_is_not_hoarding_capabilities_
    that_became_symmetric`` was written for one file over. Both directions are pinned
    here: the implementations exist, and no schedule survives them.
    """
    from crocodile.core.capability import PENDING_SYMMETRY

    load_all()
    assert set(REGISTRY[name].impls) == {AssetClass.CRYPTO, AssetClass.EQUITY}
    assert name not in PENDING_SYMMETRY


def test_the_pure_capabilities_never_touch_the_catalog() -> None:
    """Four capabilities take a context and must not read through it.

    Asserted by handing them a context whose catalog raises on any attribute access: a
    lake read would surface as that error rather than as a silently different answer.
    """

    class _Exploding:
        def __getattr__(self, name: str) -> Any:
            raise AssertionError(f"a pure capability reached the catalog for {name!r}")

    load_all()
    hostile = CapabilityContext(
        catalog=_Exploding(),  # type: ignore[arg-type]
        settings=Settings(),
        asset_class=AssetClass.CRYPTO,
    )
    assert _call("funding-predict", hostile, FundingPredictParams((0.1, 0.2)))
    assert _call("chaos-score", hostile, ChaosScoreParams(0.1, 0.0, 0.0, 0.0)) > 0.0
    assert _call("smart-money", hostile, SmartMoneyParams((), {"0xa": "a"})) == []
    assert _call("label-transfers", hostile, LabelTransfersParams((), {"0xa": "a"})) == []


def test_the_options_family_shares_one_snapshot_instant_not_one_struct() -> None:
    """Two identical schemas, deliberately two types; the module docstring has the why."""
    assert IvSurfaceParams is not TermStructureParams
    fields = {"underlying", "at_ns", "rate"}
    assert set(IvSurfaceParams.__struct_fields__) == fields
    assert set(TermStructureParams.__struct_fields__) == fields


def test_no_declaration_exposes_the_fit_method_no_surface_ever_published() -> None:
    """``fit_method='sabr'`` is on two implementations and on none of the six surfaces.

    Porting it would invent public API with no legacy behaviour to check it against, and
    ``term_structure`` does not accept it at all — so it would be honoured by two members
    of the family and silently ignored by the third.
    """
    load_all()
    for name in ("iv-surface", "term-structure", "vol-skew", "risk-reversal"):
        assert "fit_method" not in REGISTRY[name].params.__struct_fields__


def test_the_declared_return_kinds_match_what_the_adapters_return(
    empty_ctx: CapabilityContext,
) -> None:
    """A ``SCALAR`` that hands back a frame would make every surface render it wrong."""
    from crocodile.core.capability import ReturnKind

    load_all()
    assert REGISTRY["chaos-score"].returns is ReturnKind.SCALAR
    assert REGISTRY["risk-reversal"].returns is ReturnKind.SCALAR
    assert REGISTRY["funding-predict"].returns is ReturnKind.SCALAR
    assert REGISTRY["basis"].returns is ReturnKind.TABLE
    score = _call("chaos-score", empty_ctx, ChaosScoreParams(0.0, 0.0, 0.0, 0.0))
    assert isinstance(score, float) and not math.isnan(score)


# ---------------------------------------------------------------------------
# indicators: the default that flipped
# ---------------------------------------------------------------------------


def test_indicators_can_be_asked_not_to_fill_the_gaps_and_does_not_by_default() -> None:
    """The legacy default was ``False`` and this port hardcoded ``True`` with no way back.

    ``equity/legacy/cli.py:1641-1647@4a0f84c`` declared ``--fill-empty`` defaulting to
    ``False``, with help warning it "can explode wide date ranges". The adapter passes
    ``fill_empty=True`` unconditionally and ``IndicatorParams`` has no field to ask for the
    other one, so every caller on all three surfaces gets a series the legacy command would
    only have produced on request.

    It changes the numbers, which is the part that matters: measured on a 40-bar equity
    series with a 60-minute session gap, 40 rows became 121 and the last bar's RSI moved
    49.573 → 55.732, max ΔRSI 36.56. A period is a count of bars, so inserting bars
    redefines the window every indicator is measured over.
    """
    assert IndicatorParams(symbol="x", start_ns=0, end_ns=1).fill_empty is False
    assert "fill_empty" in IndicatorParams.__struct_fields__


def test_indicators_over_a_symbol_with_no_trades_returns_nothing_to_indicate(
    ctx: CapabilityContext,
) -> None:
    """The second-order effect, which is a fabrication rather than a difference of opinion.

    With ``fill_empty=True``, a symbol the lake has never seen stops answering "no data" and
    starts answering with bars: ``num_trades: 0``, ``volume: 0.0``, and — because the tail is
    the header's default — ``prov_confidence: 1.0``. Eleven invented rows, exit 0, at the
    confidence a venue print carries. Gate 3b bans exactly this shape inside a record
    constructor; it arrived here through a resampler argument instead.
    """
    bars = _call(
        "indicators",
        ctx,
        IndicatorParams(
            symbol="deribit:NOTHING-EVER-TRADED",
            start_ns=_BASE_NS,
            end_ns=_BASE_NS + 10 * 60 * _SEC_NS,
            interval="1m",
        ),
    )
    assert bars.is_empty(), (
        f"a symbol with no stored trades produced {bars.height} bars; a bar the market never "
        f"printed is a fabrication whatever the resampler calls it"
    )


def test_the_indicators_fill_empty_parameter_is_served_rather_than_redirected() -> None:
    """``_PARAM_BECAME_A_CAPABILITY`` said the flag moved to ``resample``, and it had.

    That entry was true and was not the whole story: the flag moved, and the *default* moved
    with it in the opposite direction, so a caller who did nothing got the behaviour the
    legacy command reserved for a caller who asked. A redirect ledger has no vocabulary for
    a default, which is why the parameter is back on the capability that no longer only
    redirects it.
    """
    from tests.conformance.test_phase2_surface_parity import _PARAM_BECAME_A_CAPABILITY

    assert ("indicators", "fill_empty") not in _PARAM_BECAME_A_CAPABILITY
    load_all()
    assert "fill_empty" in REGISTRY["indicators"].params.__struct_fields__


# ---------------------------------------------------------------------------
# slippage: the equity half that declared a book it never opened
# ---------------------------------------------------------------------------


def _equity_ctx(tmp_path: Path) -> CapabilityContext:
    load_all()
    return CapabilityContext(
        catalog=Catalog(tmp_path),
        settings=Settings(data_dir=tmp_path),
        asset_class=AssetClass.EQUITY,
    )


class _StubDepthSource:
    """A depth source that answers with a fixed ladder, the way the real ones answer."""

    def __init__(self, profile: DepthProfile) -> None:
        self._profile = profile

    async def snapshot(self, symbol: str) -> DepthProfile:
        return self._profile


def _equity_profile() -> DepthProfile:
    return DepthProfile(
        source="alpaca",
        symbol="alpaca:AAPL",
        symbol_raw="AAPL",
        local_ts=_BASE_NS,
        source_ts=_BASE_NS,
        asset_class=AssetClass.EQUITY,
        bids=[(99.0, 100.0), (98.0, 200.0)],
        asks=[(101.0, 50.0), (102.0, 200.0)],
        reference_price=100.0,
        depth=2,
    )


def test_slippage_for_equities_walks_a_ladder_instead_of_a_book_no_equity_source_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``fn=slippage`` was one object for both asset classes and read ``book_snapshot``.

    No equity provider emits a ``BookSnapshot``, so every equity call raised
    ``ValueError: No book snapshots found`` — while the declaration published
    ``prov_basis: "yahoo_1m_vap"``, naming a synthetic VAP ladder the code path never
    touched. A declared basis for a branch that cannot run is the decorative provenance the
    registry exists to refuse, and it is worse than a missing one because it reads as
    measured.

    The ladder it names does exist: ``equity/depth/select.py`` is what ``depth`` already
    serves, and walking it is the same arithmetic the crypto book gets.
    """
    monkeypatch.setattr(
        "crocodile.capabilities.analytics.select_depth_source",
        lambda **_: _StubDepthSource(_equity_profile()),
    )
    frame = REGISTRY["slippage"].impls[AssetClass.EQUITY].fn(
        _equity_ctx(tmp_path),
        SlippageParams(symbol="alpaca:AAPL", side="buy", size=60.0),
    )
    row = frame.to_dicts()[0]
    assert row["best_price"] == 101.0
    # 50 @ 101 + 10 @ 102 = 6070 over 60 shares.
    assert math.isclose(row["expected_price"], 6070.0 / 60.0)
    assert row["slippage_usd"] > 0.0


def test_slippage_and_depth_declare_the_same_ceiling_over_the_same_equity_book(
    tmp_path: Path,
) -> None:
    """Two batches read one book and picked opposite ends of its range.

    ``market.py`` declared ``depth``/equity ``DERIVED``/``alpaca_l1`` and argued it as the
    deliberate *ceiling*; ``analytics.py`` declared ``slippage``/equity ``SYNTHETIC``/
    ``yahoo_1m_vap`` and argued it as the deliberate *floor*. Same
    ``select_depth_source()``, same two branches, opposite declarations —
    and ``Impl.prov`` is documented as a ceiling, so only one of the two arguments can be
    the one the field takes.
    """
    load_all()
    slippage_equity = REGISTRY["slippage"].impls[AssetClass.EQUITY]
    depth_equity = REGISTRY["depth"].impls[AssetClass.EQUITY]
    assert (slippage_equity.prov, slippage_equity.basis) == (
        depth_equity.prov,
        depth_equity.basis,
    )


def test_the_two_asset_classes_of_slippage_are_not_the_same_function() -> None:
    """The tell that the equity declaration was never exercised.

    ``analytics.py:674,680`` bound ``fn=slippage`` twice. One function reading one store
    cannot be two implementations resting on two different bases, and the parameter that
    would have said so — an asset class on the context — was ignored by the body.
    """
    load_all()
    impls = REGISTRY["slippage"].impls
    assert impls[AssetClass.CRYPTO].fn is not impls[AssetClass.EQUITY].fn


# ---------------------------------------------------------------------------
# The options family's equity halves — M1, closed
# ---------------------------------------------------------------------------
# Their lake is separate from the crypto one above rather than merged into `_records()`,
# because `iv-surface` selects on `underlying` and not on asset class: one lake holding
# both markets' chains would let a crypto row satisfy an equity assertion under a shared
# ticker, which is precisely the "answers plausibly and empty" failure the registry exists
# to make impossible.

_EQUITY_UNDERLYING = "AAPL"
_EQUITY_SPOT = 100.0
_EQUITY_CALL_IV = 0.50
_EQUITY_PUT_IV = 0.70

_SOLVED_STRIKE = 120.0
_SOLVED_MID = bsm_price(
    _EQUITY_SPOT, _SOLVED_STRIKE, 1.0, 0.0, _EQUITY_CALL_IV, 0.0, OptType.CALL
)
"""The mid of the one contract Yahoo priced but did not grade, in vols the chain agrees with.

It is the Black-Scholes price at exactly ``_EQUITY_CALL_IV``, so solving it back returns
the vol every other call on this expiry carries. That is what keeps the skew flat while
still forcing the fallback branch to run: a mid quoted at some other vol would make the
risk reversal depend on which strike the 25-delta search happened to land on, and the test
below would then be measuring the search rather than the capability.
"""


def _equity_option(
    strike: float,
    expiry: int,
    opt_type: OptType,
    mark_iv: float | None,
    *,
    bid_px: float | None = None,
    ask_px: float | None = None,
    open_interest: float | None = None,
) -> OptionsChain:
    """A Yahoo-shaped contract: an IV or a two-sided quote, and never a ``mark_price``."""
    symbol = f"yahoo:{_EQUITY_UNDERLYING}-{expiry}-{int(strike)}-{opt_type.value}"
    return OptionsChain(
        source="yahoo",
        symbol=symbol,
        symbol_raw=symbol.split(":", 1)[-1],
        source_ts=_BASE_NS,
        local_ts=_BASE_NS,
        asset_class=AssetClass.EQUITY,
        underlying=_EQUITY_UNDERLYING,
        underlying_price=_EQUITY_SPOT,
        strike=strike,
        expiry=expiry,
        opt_type=opt_type,
        mark_iv=mark_iv,
        bid_px=bid_px,
        ask_px=ask_px,
        open_interest=open_interest,
    )


def _equity_chain() -> list[Any]:
    """Three strikes at E1 both ways, one at E2, and one contract Yahoo priced but did not
    grade — the row that makes the solve-from-mid branch reachable."""
    rows: list[Any] = [
        _equity_option(strike, _E1, opt_type, iv, open_interest=oi)
        for strike, oi in ((90.0, 11.0), (100.0, 22.0), (110.0, 33.0))
        for opt_type, iv in ((OptType.CALL, _EQUITY_CALL_IV), (OptType.PUT, _EQUITY_PUT_IV))
    ]
    rows.append(_equity_option(100.0, _E2, OptType.CALL, 0.45, open_interest=5.0))
    rows.append(
        _equity_option(
            _SOLVED_STRIKE,
            _E1,
            OptType.CALL,
            None,
            bid_px=_SOLVED_MID - 0.01,
            ask_px=_SOLVED_MID + 0.01,
            open_interest=1.0,
        )
    )
    return rows


@pytest.fixture
def equity_ctx(tmp_path: Path) -> CapabilityContext:
    load_all()
    asyncio.run(_write(tmp_path, _equity_chain()))
    return CapabilityContext(
        catalog=Catalog(tmp_path),
        settings=Settings(data_dir=tmp_path),
        asset_class=AssetClass.EQUITY,
    )


# ---------------------------------------------------------------------------
# M4: the equity halves of whale-alerts, smart-money and label-transfers
# ---------------------------------------------------------------------------
# Three capabilities whose crypto halves read an on-chain transfer. Equity has none, and
# the substitute is a filing — Form 4 for a dated insider transaction, 13F-HR for a
# quarter-end institutional position. Every test below reaches the implementation through
# `REGISTRY[...].impls[AssetClass.EQUITY]`, so a declaration wired to the wrong function
# fails here rather than on whichever surface projects it first.

_ISSUER = "EXCO"
_INSIDER_CIK = "0001051401"
_MANAGER_CIK = "0000933136"


def _date_ns(day: str) -> int:
    return int(
        datetime.datetime.fromisoformat(day)
        .replace(tzinfo=datetime.UTC)
        .timestamp()
        * 1_000_000_000
    )


def _insider(
    day: str,
    code: str,
    direction: str | None,
    shares: float | None,
    price: float | None,
    *,
    name: str = "DOE JANE Q",
    cik: str | None = _INSIDER_CIK,
    ingested_at: int = _BASE_NS,
) -> InsiderTransaction:
    """One Form 4 line as the parser would have written it.

    ``ingested_at`` defaults to one instant for every row on purpose: a filing history
    arrives in a single fetch, so a decade of transactions really does share one
    ``local_ts``. That is what makes the window question below a real one.
    """
    n_reported = int(shares is not None) + int(price is not None)
    tail = provenance_fields("sec_form4", {"n_reported_amounts": n_reported})
    return InsiderTransaction(
        source="sec_edgar",
        symbol=_ISSUER,
        symbol_raw=_ISSUER,
        local_ts=ingested_at,
        source_ts=None,
        asset_class=AssetClass.EQUITY,
        prov=tail.prov,
        prov_basis=tail.prov_basis,
        prov_confidence=tail.prov_confidence,
        prov_inputs=tail.prov_inputs,
        insider_name=name,
        insider_cik=cik,
        position="Chief Executive Officer",
        transaction_type=code,
        transaction_date=day,
        shares=shares,
        price=price,
        value=shares * price if shares is not None and price is not None else None,
        ownership="D",
        acquired_disposed=direction,
    )


def _holding(report_date: str, cusip: str, value: float, *, manager: str = "Cascade Partners LP"):
    tail = provenance_fields("sec_13f_hr", {"disclosure_lag_days": 30})
    return Holding13F(
        source="sec_edgar",
        symbol=cusip.upper(),
        symbol_raw=cusip,
        local_ts=_BASE_NS,
        source_ts=None,
        asset_class=AssetClass.EQUITY,
        prov=tail.prov,
        prov_basis=tail.prov_basis,
        prov_confidence=tail.prov_confidence,
        prov_inputs=tail.prov_inputs,
        manager_name=manager,
        manager_cik=_MANAGER_CIK,
        issuer_name="EXAMPLE INDUSTRIES INC",
        cusip=cusip,
        value=value,
        shares=1000.0,
        shares_type="SH",
        report_date=report_date,
    )


def _filings_lake(tmp_path: Path) -> CapabilityContext:
    load_all()
    asyncio.run(
        _write(
            tmp_path,
            [
                # Below the default threshold: 100 shares at $10.
                _insider("2024-05-01", "P", "A", 100.0, 10.0),
                # A whale sale, and the row every assertion below is really about.
                _insider("2024-05-03", "S", "D", 196410.0, 183.2143),
                # A gift: shares, no price, so no notional at all.
                _insider("2024-05-06", "G", "D", 5000.0, None),
                # Inside the same bulk fetch and outside the window every test asks for.
                _insider("2023-01-04", "S", "D", 400000.0, 200.0),
            ],
        )
    )
    return CapabilityContext(
        catalog=Catalog(tmp_path),
        settings=Settings(data_dir=tmp_path),
        asset_class=AssetClass.EQUITY,
    )
# M5: the four spread capabilities that gained an equity half, plus the forecaster
# ---------------------------------------------------------------------------
#
# The equity carry arithmetic is tested against a real lake in
# `tests/equity/test_equity_carry.py`. What is asserted here is the thing only this file
# can assert: that the declarations are wired to those functions and reachable the way a
# surface reaches them — through `REGISTRY`, by asset class, with the shared params
# struct. A capability wired to the wrong function, or to the right one under the wrong
# asset class, fails here rather than on whichever surface Phase 2 projects first.

_M5_SPOT = "SPX"
_M5_FUTURE = "ESH24"
_M5_STOCK = "AAPL"
_M5_EXPIRY = _T4 + 91 * _DAY_NS
_M5_CURVE_CSV = "Date,1 Mo,3 Mo,6 Mo,1 Yr\n01/01/2024,5.53,5.46,5.24,4.84\n"


def _m5_lake(tmp_path: Path) -> CapabilityContext:
    """An equity lake with all four legs: two price series, a chain, a dividend, a curve."""
    from crocodile.core.schema.enums import CorpActionType
    from crocodile.core.schema.records import OHLCV, CorporateAction, OptionsChain
    from crocodile.equity.providers.treasury import parse_par_yield_csv

    def _px(symbol: str, ts: int, close: float) -> OHLCV:
        return OHLCV(
            source="stooq", symbol=symbol, symbol_raw=symbol, local_ts=ts,
            asset_class=AssetClass.EQUITY, source_ts=ts, interval="1d",
            open=close, high=close, low=close, close=close, volume=1.0,
        )

    def _opt(strike: float, opt: OptType, bid: float, ask: float) -> OptionsChain:
        return OptionsChain(
            source="yahoo", symbol=f"{_M5_STOCK}{strike:g}{opt.value}",
            symbol_raw=f"{_M5_STOCK}{strike:g}{opt.value}", local_ts=_T3,
            asset_class=AssetClass.EQUITY, source_ts=_T3, underlying=_M5_STOCK,
            underlying_price=None, strike=strike, expiry=_M5_EXPIRY, opt_type=opt,
            bid_px=bid, ask_px=ask,
        )

    records = [
        _px(_M5_SPOT, _T1, 100.0),
        _px(_M5_FUTURE, _T2, 101.0),
        _px(_M5_STOCK, _T2, 100.0),
        _opt(100.0, OptType.CALL, 5.4, 5.6),
        _opt(100.0, OptType.PUT, 2.4, 2.6),
        CorporateAction(
            source="tiingo", symbol=_M5_STOCK, symbol_raw=_M5_STOCK, local_ts=_T4,
            asset_class=AssetClass.EQUITY, source_ts=_T4, ex_date="2024-01-01",
            type=CorpActionType.DIVIDEND_CASH, value=1.0,
        ),
        *parse_par_yield_csv(_M5_CURVE_CSV, local_ts=_T1),
    ]

    async def _write() -> None:
        sink = ParquetSink(tmp_path)
        for record in records:
            await sink.put(record)
        await sink.flush()
        await sink.close()

    asyncio.run(_write())
    return _equity_ctx(tmp_path)


def _call_equity(name: str, ctx: CapabilityContext, params: Any) -> Any:
    return REGISTRY[name].impls[AssetClass.EQUITY].fn(ctx, params)


def test_iv_surface_serves_equities_and_says_which_price_each_row_came_from(
    equity_ctx: CapabilityContext,
) -> None:
    """M1's two halves in one assertion: the chain reads, and the fallback is reachable.

    Six of the seven rows carry Yahoo's own ``impliedVolatility`` and report
    ``source="mark_iv"``; the seventh has none and is solved by inverting Black-Scholes-
    Merton on the mid of its bid and ask, reporting ``source="computed"``. A surface where
    every row said ``mark_iv`` would pass a row-count test while the solver had never run.
    """
    rows = _call_equity("iv-surface", equity_ctx, IvSurfaceParams(_EQUITY_UNDERLYING, _BASE_NS))
    assert len(rows) == 8
    assert set(rows["source"].to_list()) == {"mark_iv", "computed"}

    solved = rows.filter(pl.col("source") == "computed")
    assert solved.height == 1
    assert solved["strike"][0] == pytest.approx(_SOLVED_STRIKE)
    assert solved["iv"][0] == pytest.approx(_EQUITY_CALL_IV, abs=1e-3)

    otm_call = rows.filter((pl.col("strike") == 110.0) & (pl.col("opt_type") == "C"))
    assert otm_call["moneyness"][0] == pytest.approx(1.1)
    assert otm_call["iv"][0] == pytest.approx(_EQUITY_CALL_IV)


def test_the_two_asset_classes_of_the_options_family_are_not_the_same_function() -> None:
    """The tell ``slippage`` did not have until it broke, asserted before this one can.

    One function cannot invert a ``mark_price`` no equity feed publishes *and* a mid no
    crypto venue publishes. Binding one object for both would have produced an equity
    surface of ``source="unavailable"`` under a declaration claiming otherwise — a hole
    that reads exactly like an empty lake.
    """
    load_all()
    for name in ("iv-surface", "term-structure", "vol-skew", "risk-reversal"):
        impls = REGISTRY[name].impls
        assert set(impls) == {AssetClass.CRYPTO, AssetClass.EQUITY}
        assert impls[AssetClass.CRYPTO].fn is not impls[AssetClass.EQUITY].fn


def test_term_structure_serves_equities_with_one_atm_row_per_expiry(
    equity_ctx: CapabilityContext,
) -> None:
    rows = _call_equity(
        "term-structure", equity_ctx, TermStructureParams(_EQUITY_UNDERLYING, _BASE_NS)
    )
    assert rows["expiry"].to_list() == [_E1, _E2]
    assert rows["atm_strike"].to_list() == [100.0, 100.0]
    assert rows["days_to_expiry"][0] == pytest.approx(365.0)


def test_vol_skew_serves_equities_with_a_delta_priced_off_the_spot(
    equity_ctx: CapabilityContext,
) -> None:
    """Every row gets a delta, which is what ``risk-reversal`` then searches.

    The ``None`` this refuses is not cosmetic: a missing delta silently drops a strike from
    the 25-delta search, and a fabricated zero would win it against every negative target.
    """
    rows = _call_equity("vol-skew", equity_ctx, VolSkewParams(_EQUITY_UNDERLYING, _E1, _BASE_NS))
    assert len(rows) == 7
    assert rows["strike"].to_list() == sorted(rows["strike"].to_list())
    assert all(delta is not None for delta in rows["delta"].to_list())


def test_risk_reversal_serves_equities_and_prices_calls_against_puts(
    equity_ctx: CapabilityContext,
) -> None:
    """Calls are quoted 20 vols under puts throughout, so the risk reversal is -0.20.

    Flat on both sides on purpose, for the reason the crypto twin gives: which strikes the
    25-delta search lands on is the surface's business, and a flat pair makes the answer
    the same whichever it picks — which leaves this measuring the capability.
    """
    result = _call_equity(
        "risk-reversal", equity_ctx, RiskReversalParams(_EQUITY_UNDERLYING, _E1, _BASE_NS)
    )
    assert result["risk_reversal"] == pytest.approx(_EQUITY_CALL_IV - _EQUITY_PUT_IV)
    assert result["butterfly"] is not None


def test_risk_reversal_reports_two_holes_for_an_equity_expiry_with_no_chain(
    equity_ctx: CapabilityContext,
) -> None:
    params = RiskReversalParams(_EQUITY_UNDERLYING, _E1 + _DAY_NS, _BASE_NS)
    assert _call_equity("risk-reversal", equity_ctx, params) == {
        "risk_reversal": None,
        "butterfly": None,
    }


def test_the_options_family_declares_the_same_ceiling_for_both_markets() -> None:
    """DERIVED on ``native``, by the same argument rather than by copying the crypto row.

    On its best day every ``iv`` is a vol the feed published — Deribit's ``mark_iv``, or
    Yahoo's ``impliedVolatility`` carried in the same field — and the cross-section is this
    engine's arrangement of those points, which is DERIVED and not NATIVE. The fallback
    inverts a *published* price on both sides, a venue mark there and a quoted mid here, so
    neither reaches SYNTHETIC. ``native`` names the inputs, which is what a basis is for.
    """
    load_all()
    for name in ("iv-surface", "term-structure", "vol-skew", "risk-reversal"):
        for asset_class, impl in REGISTRY[name].impls.items():
            assert impl.prov is Provenance.DERIVED, (name, asset_class)
            assert impl.basis == "native", (name, asset_class)


def test_the_equity_options_adapters_are_named_module_level_functions() -> None:
    """A stack trace and the calling-convention gate both need a file and a line number."""
    from crocodile.capabilities import analytics

    load_all()
    for name in ("iv-surface", "term-structure", "vol-skew", "risk-reversal"):
        fn = REGISTRY[name].impls[AssetClass.EQUITY].fn
        assert fn is getattr(analytics, fn.__name__)
        assert fn.__module__ == analytics.__name__


def test_the_options_family_reads_the_lake_through_the_context_and_not_the_network(
    equity_ctx: CapabilityContext,
) -> None:
    """No provider call, no ``run_to_completion``: the chain is already in the lake.

    Worth pinning because the equity halves of ``depth`` and ``slippage`` in this same
    registry *do* reach a provider, and a reader could reasonably assume an equity
    implementation always does.
    """
    surface = _call_equity(
        "iv-surface", equity_ctx, IvSurfaceParams(_EQUITY_UNDERLYING, _BASE_NS)
    )
    assert isinstance(surface, pl.DataFrame)
    empty = CapabilityContext(
        catalog=Catalog(Path(equity_ctx.settings.data_dir) / "nothing-here"),
        settings=equity_ctx.settings,
        asset_class=AssetClass.EQUITY,
    )
    assert _call_equity(
        "iv-surface", empty, IvSurfaceParams(_EQUITY_UNDERLYING, _BASE_NS)
    ).is_empty()
def test_basis_for_equities_spreads_the_derivative_leg_over_the_cash_leg(
    tmp_path: Path,
) -> None:
    """``perp_symbol`` names the derivative leg for equities; one capability, one schema."""
    ctx = _m5_lake(tmp_path)
    rows = _call_equity("basis", ctx, BasisParams(_M5_SPOT, _M5_FUTURE, _T1, _T4))
    row = rows.row(0, named=True)
    assert row["spot_price"] == pytest.approx(100.0)
    assert row["perp_price"] == pytest.approx(101.0)
    assert row["basis_pct"] == pytest.approx(0.01)


def test_spot_future_basis_for_equities_reports_the_carry_the_crypto_half_cannot(
    tmp_path: Path,
) -> None:
    """The column M5 exists for: the annualised basis net of what the money costs.

    A perpetual quotes its financing directly as funding, so the crypto half never needed
    the term and does not carry the column; an equity future quotes only a price.
    """
    ctx = _m5_lake(tmp_path)
    params = SpotFutureBasisParams(_M5_FUTURE, _M5_SPOT, _T1, _T4, expiry_ns=_M5_EXPIRY)
    rows = _call_equity("spot-future-basis", ctx, params)
    row = rows.row(0, named=True)
    assert row["risk_free_rate"] == pytest.approx(0.0546)
    assert row["risk_free_date"] == "2024-01-01"
    assert row["carry_pct"] == pytest.approx(row["annualized_pct"] - row["risk_free_rate"])
    # The five columns the crypto half returns are all still here, in its order, so a
    # caller does not have to know which market answered before it can read the answer.
    assert rows.columns[:6] == [
        "local_ts",
        "future_price",
        "spot_price",
        "basis",
        "basis_pct",
        "annualized_pct",
    ]


def test_perp_basis_for_equities_reads_the_forward_off_put_call_parity(
    tmp_path: Path,
) -> None:
    """One symbol, and the two columns the crypto half returns.

    Nothing under ``equity/providers`` writes a ``DerivativeTicker``, so the one-symbol
    schema is served by the option market's own statement of the same spread rather than
    by a record no equity lake holds.
    """
    ctx = _m5_lake(tmp_path)
    rows = _call_equity("perp-basis", ctx, PerpBasisParams(_M5_STOCK, _T1, _T4))
    row = rows.row(0, named=True)
    assert row["index_price"] == pytest.approx(100.0)
    assert row["mark_price"] > row["index_price"]
    assert row["basis"] == pytest.approx(row["mark_price"] - row["index_price"])
    assert row["basis_pct"] == pytest.approx(row["basis"] / row["index_price"])


def test_funding_apr_for_equities_reports_the_cost_of_carry_per_dividend(
    tmp_path: Path,
) -> None:
    """Crypto's sign convention unchanged: positive means the position holder pays, so a
    received dividend is negative funding and the two series share one axis."""
    ctx = _m5_lake(tmp_path)
    rows = _call_equity("funding-apr", ctx, FundingAprParams(_M5_STOCK, _T1, _T4))
    row = rows.row(0, named=True)
    assert row["funding_rate"] == pytest.approx(-0.01)
    assert row["cumulative_funding"] == pytest.approx(-0.01)
    assert row["risk_free_apr"] == pytest.approx(0.0553)
    assert row["carry_apr"] == pytest.approx(row["risk_free_apr"] + row["apr"])


def test_funding_predict_is_one_function_for_both_asset_classes(tmp_path: Path) -> None:
    """The ``indicators`` pattern, not the ``fn=slippage`` one.

    The distinction is whether the shared body can reach data the asset class has:
    ``slippage`` read ``book_snapshot``, which no equity provider writes, while this reads
    no lake at all — the history arrives in ``params`` and the model is offline. A second
    adapter would be two spellings of one call.
    """
    load_all()
    impls = REGISTRY["funding-predict"].impls
    assert impls[AssetClass.CRYPTO].fn is impls[AssetClass.EQUITY].fn
    ctx = _equity_ctx(tmp_path)
    answer = _call_equity(
        "funding-predict", ctx, FundingPredictParams((0.01, 0.02, 0.03), window_size=2)
    )
    # `method in {"xgboost", "rolling_mean"}` was what stood here, and those are the only
    # two literals the function assigns — an assertion the implementation could not fail.
    # Three rates cannot leave a trainable row after lag-1/2/3 shifting, so the branch is
    # the rolling mean however the machine answers `import xgboost`, and the number is the
    # mean of the last `window_size` rates: mean([0.02, 0.03]).
    assert answer["method"] == "rolling_mean"
    assert answer["predicted_funding_rate"] == pytest.approx(0.025)
    assert answer["window_size"] == 2


def test_every_m5_equity_impl_is_a_named_module_level_function() -> None:
    """The calling-convention gate covers the shape; this covers the *identity*.

    An equity half bound to the crypto function is the defect ``slippage`` shipped, and it
    passes every gate that only looks at signatures. The one exception is
    ``funding-predict``, which is asserted to be shared in the test above with the
    argument for why.
    """
    load_all()
    for name in ("basis", "perp-basis", "spot-future-basis", "funding-apr"):
        impls = REGISTRY[name].impls
        assert impls[AssetClass.CRYPTO].fn is not impls[AssetClass.EQUITY].fn
        equity_fn = impls[AssetClass.EQUITY].fn
        assert equity_fn.__name__.endswith("_equities")
        assert equity_fn.__module__ == "crocodile.capabilities.analytics"


def test_the_three_carry_impls_declare_the_basis_that_names_the_treasury_leg() -> None:
    """``native`` would say a venue reported the carry. None did — the Treasury reported a
    yield, and the combination is this engine's. ``basis`` keeps ``native`` because it has
    no horizon and therefore no financing leg to claim."""
    from crocodile.core.schema.provenance import Provenance

    load_all()
    for name in ("perp-basis", "spot-future-basis", "funding-apr"):
        impl = REGISTRY[name].impls[AssetClass.EQUITY]
        assert impl.basis == "treasury_carry"
        assert impl.prov is Provenance.DERIVED
    assert REGISTRY["basis"].impls[AssetClass.EQUITY].basis == "native"


def _equity(name: str, ctx: CapabilityContext, params: Any) -> Any:
    return REGISTRY[name].impls[AssetClass.EQUITY].fn(ctx, params)


def test_whale_alerts_for_equities_returns_the_same_columns_the_crypto_half_does(
    ctx: CapabilityContext, tmp_path: Path
) -> None:
    """Symmetry is a shared params struct *and* a matching return shape.

    A surface renders one table for both asset classes off one declaration, so a column that
    exists on one side and not the other is a projection that has to branch. Asserted against
    the crypto half's own output rather than against a list written twice.
    """
    crypto = _call("whale-alerts", ctx, WhaleAlertsParams(_PERP, _T1, _T4))
    equity = _equity(
        "whale-alerts",
        _filings_lake(tmp_path),
        WhaleAlertsParams(_ISSUER, _date_ns("2024-05-01"), _date_ns("2024-05-31")),
    )
    assert crypto.schema == equity.schema


def test_whale_alerts_for_equities_keeps_the_reported_transaction_above_the_threshold(
    tmp_path: Path,
) -> None:
    """One filing in the window clears 100 000 and the other two do not, for two reasons.

    The purchase is genuinely small. The gift states shares and no price, so it has no
    notional — and a notional of `None` is below every threshold rather than above the zero
    one, which is the same treatment a transfer with no `usd_value` gets on the crypto side.
    """
    rows = _equity(
        "whale-alerts",
        _filings_lake(tmp_path),
        WhaleAlertsParams(_ISSUER, _date_ns("2024-05-01"), _date_ns("2024-05-31")),
    ).to_dicts()
    assert len(rows) == 1
    assert rows[0]["event_type"] == "Form 4"
    assert rows[0]["side"] == "sell"
    assert rows[0]["amount"] == 196410.0
    assert math.isclose(rows[0]["usd_value"], 196410.0 * 183.2143)
    assert rows[0]["timestamp"] == _date_ns("2024-05-03")


def test_whale_alerts_for_equities_windows_on_the_transaction_date_not_the_ingest_time(
    tmp_path: Path,
) -> None:
    """The trap a straight port of the crypto half walks into.

    `track_whale_alerts` filters on `local_ts`, which is right for a trade — it is observed
    as it prints. A filing history is fetched in one pass, so every row in the fixture shares
    one `local_ts`; a window over that would return the whole decade or none of it depending
    on when the fetch ran. The January 2023 sale is the biggest row in the lake and is
    outside the window, so a `local_ts` filter would either include it here or exclude
    everything.
    """
    ctx = _filings_lake(tmp_path)
    in_may = _equity(
        "whale-alerts",
        ctx,
        WhaleAlertsParams(_ISSUER, _date_ns("2024-05-01"), _date_ns("2024-05-31"), min_usd=0.0),
    )
    in_2023 = _equity(
        "whale-alerts",
        ctx,
        WhaleAlertsParams(_ISSUER, _date_ns("2023-01-01"), _date_ns("2023-01-31")),
    )
    assert sorted(r["timestamp"] for r in in_may.to_dicts()) == [
        _date_ns("2024-05-01"),
        _date_ns("2024-05-03"),
    ]
    assert [r["usd_value"] for r in in_2023.to_dicts()] == [400000.0 * 200.0]


def test_whale_alerts_for_equities_answers_an_empty_lake_with_an_empty_table(
    tmp_path: Path,
) -> None:
    """A symbol nobody ingested is not an error, it is a symbol with no whales — and the
    empty frame still carries the six columns, so a caller may select on them."""
    load_all()
    empty = CapabilityContext(
        catalog=Catalog(tmp_path),
        settings=Settings(data_dir=tmp_path),
        asset_class=AssetClass.EQUITY,
    )
    frame = _equity("whale-alerts", empty, WhaleAlertsParams("NOSUCH", 0, _date_ns("2030-01-01")))
    assert frame.is_empty()
    assert frame.columns == ["timestamp", "event_type", "price", "amount", "usd_value", "side"]


def test_whale_alerts_for_equities_reads_form_4_and_not_a_13f_position(
    tmp_path: Path,
) -> None:
    """The 13F parser exists and this capability deliberately does not read it.

    An information table states a position, not a transaction: no side, and no date for any
    of the trades behind it. Reporting a large holding as an alert would answer "who holds
    size" under a name that promises "who moved it". The lake here holds a $135M position
    and one $36M sale, and only the sale is an alert.
    """
    load_all()
    asyncio.run(
        _write(
            tmp_path,
            [
                _insider("2024-05-03", "S", "D", 196410.0, 183.2143),
                _holding("2024-03-31", "30161N101", 135_360_898.0),
            ],
        )
    )
    ctx = CapabilityContext(
        catalog=Catalog(tmp_path),
        settings=Settings(data_dir=tmp_path),
        asset_class=AssetClass.EQUITY,
    )
    rows = _equity(
        "whale-alerts",
        ctx,
        WhaleAlertsParams(_ISSUER, _date_ns("2024-01-01"), _date_ns("2024-12-31")),
    ).to_dicts()
    assert [r["event_type"] for r in rows] == ["Form 4"]


def _rows(records: list[Any]) -> tuple[dict[str, Any], ...]:
    """Filing records as the dicts a caller passes in ``transfers``."""
    return tuple(msgspec.structs.asdict(record) for record in records)


def test_smart_money_for_equities_nets_a_filers_reported_purchases_against_their_sales(
    empty_ctx: CapabilityContext,
) -> None:
    """An acquisition is value moving to the filer and a disposition value moving away.

    Which is exactly the ``from``/``to`` pair the crypto tracker already nets, so the
    arithmetic is shared and only the translation is new: the filer bought $1 000 and sold
    $3 000, so the net flow is -$2 000 over $4 000 of volume.
    """
    rows = _rows(
        [
            _insider("2024-05-01", "P", "A", 100.0, 10.0),
            _insider("2024-05-03", "S", "D", 100.0, 30.0),
        ]
    )
    out = _equity("smart-money", empty_ctx, SmartMoneyParams(rows, {_INSIDER_CIK: "the CEO"}))
    assert len(out) == 1
    assert out[0]["net_flow_usd"] == -2000.0
    assert out[0]["total_volume_usd"] == 4000.0
    assert out[0]["tx_count"] == 2
    assert out[0]["label"] == "the CEO"
    assert out[0]["last_active_ts"] == _date_ns("2024-05-03")


def test_smart_money_for_equities_matches_a_watchlist_on_either_handle_a_filing_carries(
    empty_ctx: CapabilityContext,
) -> None:
    """A filing names its party twice, by CIK and by name, and a watchlist may use either.

    Keying the tracker on one of them and matching the other is the bug shape that looks
    like the filer did nothing, which is indistinguishable from a filer who really did.
    """
    rows = _rows([_insider("2024-05-03", "S", "D", 100.0, 30.0)])
    by_cik = _equity("smart-money", empty_ctx, SmartMoneyParams(rows, {_INSIDER_CIK: "the CEO"}))
    by_name = _equity(
        "smart-money", empty_ctx, SmartMoneyParams(rows, {"doe jane q": "the CEO"})
    )
    assert [r["net_flow_usd"] for r in by_cik] == [-3000.0]
    assert by_name == by_cik


def test_smart_money_for_equities_ignores_a_filer_nobody_asked_about(
    empty_ctx: CapabilityContext,
) -> None:
    """The tracker's own contract, kept: an empty watchlist matches no row by construction."""
    rows = _rows([_insider("2024-05-03", "S", "D", 100.0, 30.0)])
    assert _equity("smart-money", empty_ctx, SmartMoneyParams(rows, {})) == []


def test_smart_money_for_equities_leaves_an_unsigned_transaction_out_of_a_signed_total(
    empty_ctx: CapabilityContext,
) -> None:
    """Code ``G`` is one code and two directions — the donor disposes, the donee acquires.

    A row that carries no ``acquired_disposed`` and whose code does not imply one cannot be
    netted, and counting it in ``total_volume_usd`` while leaving it out of ``net_flow_usd``
    would make two columns describe two different sets of rows under one ``tx_count``.
    """
    priced_gift = _insider("2024-05-06", "G", None, 100.0, 10.0)
    rows = _rows([_insider("2024-05-03", "S", "D", 100.0, 30.0), priced_gift])
    out = _equity("smart-money", empty_ctx, SmartMoneyParams(rows, {_INSIDER_CIK: "the CEO"}))
    assert out[0]["tx_count"] == 1
    assert out[0]["total_volume_usd"] == 3000.0


def test_smart_money_for_equities_reads_a_flow_out_of_two_quarters_of_holdings(
    empty_ctx: CapabilityContext,
) -> None:
    """The 13F half of M4: a position is not a flow, and the difference between two is.

    The manager added $15M to one position and trimmed $8M from another between March and
    June, so the net is +$7M over $23M of volume — and neither number is on any single
    information table.
    """
    rows = _rows(
        [
            _holding("2024-03-31", "30161N101", 135_000_000.0),
            _holding("2024-06-30", "30161N101", 150_000_000.0),
            _holding("2024-03-31", "02079K305", 20_000_000.0),
            _holding("2024-06-30", "02079K305", 12_000_000.0),
        ]
    )
    out = _equity(
        "smart-money", empty_ctx, SmartMoneyParams(rows, {_MANAGER_CIK: "Cascade"})
    )
    assert len(out) == 1
    assert out[0]["net_flow_usd"] == 7_000_000.0
    assert out[0]["total_volume_usd"] == 23_000_000.0
    assert out[0]["label"] == "Cascade"


def test_smart_money_for_equities_reports_no_flow_from_a_single_quarter(
    empty_ctx: CapabilityContext,
) -> None:
    """A change cannot be observed before the first observation.

    The tempting alternative is to read an initial appearance as a purchase of the whole
    position, which would report a manager's entire book as bought in whichever quarter the
    caller happened to fetch first. Empty is the true answer and it is worth a test, because
    it is the answer somebody will try to "fix".
    """
    rows = _rows([_holding("2024-03-31", "30161N101", 135_000_000.0)])
    assert _equity("smart-money", empty_ctx, SmartMoneyParams(rows, {_MANAGER_CIK: "C"})) == []


def test_label_transfers_for_equities_labels_every_row_and_still_flags_only_the_watched(
    empty_ctx: CapabilityContext,
) -> None:
    """The claim the M4 ledger entry made: a CIK is already a label.

    An unwatched Ethereum address gets ``""`` from the crypto half because a hex string is
    all the row carries. A filing arrives carrying the filer's own reported name, so the
    label is populated for every row — while ``is_known`` keeps meaning *matched the
    watchlist*, which is what stops ``known_only`` becoming a filter that removes nothing.
    """
    rows = _rows(
        [
            _insider("2024-05-03", "S", "D", 100.0, 30.0),
            _insider("2024-05-04", "P", "A", 50.0, 20.0, name="ROE RICHARD", cik="0009999999"),
        ]
    )
    out = _equity(
        "label-transfers", empty_ctx, LabelTransfersParams(rows, {_INSIDER_CIK: "the CEO"})
    )
    watched, unwatched = out
    assert watched["is_known"] is True
    assert unwatched["is_known"] is False
    # A sale moves value away from the filer, a purchase towards them, so the two label
    # columns follow the money rather than the layout of the filing.
    assert (watched["from_label"], watched["to_label"]) == ("the CEO", _ISSUER)
    assert (unwatched["from_label"], unwatched["to_label"]) == (_ISSUER, "ROE RICHARD")


def test_label_transfers_for_equities_still_drops_the_unknown_rows_when_asked(
    empty_ctx: CapabilityContext,
) -> None:
    """``known_only`` reads ``is_known``, which is the column a "has a label" reading would
    have made universally true."""
    rows = _rows(
        [
            _insider("2024-05-03", "S", "D", 100.0, 30.0),
            _insider("2024-05-04", "P", "A", 50.0, 20.0, name="ROE RICHARD", cik="0009999999"),
        ]
    )
    params = LabelTransfersParams(rows, {_INSIDER_CIK: "the CEO"}, known_only=True)
    kept = _equity("label-transfers", empty_ctx, params)
    assert [row["insider_name"] for row in kept] == ["DOE JANE Q"]


def test_label_transfers_for_equities_reads_the_filings_value_as_the_notional(
    empty_ctx: CapabilityContext,
) -> None:
    """``filter_transfers_by_usd`` is reused unchanged: it already reads ``value``.

    The gift is the interesting row. It reports shares and no price, so it has no notional
    at all, and ``min_usd=0.0`` drops it while ``min_usd=None`` keeps it — the same
    distinction the crypto half draws for a transfer with an unparseable ``usd_value``, on a
    source that produces the case far more often.
    """
    rows = _rows(
        [
            _insider("2024-05-03", "S", "D", 100.0, 30.0),
            _insider("2024-05-06", "G", "D", 5000.0, None),
        ]
    )
    watchlist: dict[str, Any] = {_INSIDER_CIK: "the CEO"}
    assert len(_equity("label-transfers", empty_ctx, LabelTransfersParams(rows, watchlist))) == 2
    thresholded = LabelTransfersParams(rows, watchlist, min_usd=0.0)
    assert len(_equity("label-transfers", empty_ctx, thresholded)) == 1
    high = LabelTransfersParams(rows, watchlist, min_usd=5000.0)
    assert _equity("label-transfers", empty_ctx, high) == []


def test_the_three_m4_capabilities_are_symmetric_and_off_the_ledger() -> None:
    """The definition of done, asserted as one statement rather than inferred from a gate.

    ``test_the_ledger_holds_exactly_the_entries_it_was_pinned_to_hold`` catches a half-done
    deletion, and ``test_gate2_every_capability_is_symmetric`` catches an unscheduled
    asymmetry. Neither says *these three* are done, which is what M4 promised.
    """
    from crocodile.core.capability import PENDING_SYMMETRY

    load_all()
    for name in ("whale-alerts", "smart-money", "label-transfers"):
        assert set(REGISTRY[name].impls) == {AssetClass.CRYPTO, AssetClass.EQUITY}, name
        assert name not in PENDING_SYMMETRY, name


def test_the_two_asset_classes_of_the_m4_capabilities_are_not_the_same_function() -> None:
    """The tell that an equity declaration was never exercised, which `slippage` shipped.

    One function bound twice cannot be two implementations over two different documents, and
    the parameter that would have said so — an asset class on the context — is not read by
    any of these bodies.
    """
    load_all()
    for name in ("whale-alerts", "smart-money", "label-transfers"):
        impls = REGISTRY[name].impls
        assert impls[AssetClass.CRYPTO].fn is not impls[AssetClass.EQUITY].fn, name


def test_label_transfers_for_equities_names_an_institutional_filer_from_the_row_itself(
    empty_ctx: CapabilityContext,
) -> None:
    """The 13F side of the same claim, and the case the ledger comment had in mind.

    A quarter-end position reports no direction, so neither label column asserts a flow —
    what the capability adds here is the identity, and the information table carries it: the
    manager's own name off the cover page and a CIK that survives a rename. An unwatched
    Ethereum address would have come back with two empty strings.
    """
    rows = _rows([_holding("2024-03-31", "30161N101", 135_000_000.0)])
    unwatched = _equity("label-transfers", empty_ctx, LabelTransfersParams(rows, {}))[0]
    assert unwatched["is_known"] is False
    assert unwatched["from_label"] == "Cascade Partners LP"
    assert unwatched["to_label"] == "EXAMPLE INDUSTRIES INC"

    watched = _equity(
        "label-transfers",
        empty_ctx,
        LabelTransfersParams(rows, {"cascade partners lp": "the whale"}),
    )[0]
    assert watched["is_known"] is True
    assert watched["from_label"] == "the whale"


# ---------------------------------------------------------------------------
# Phase 3: the three equity halves M6 and M7 close
# ---------------------------------------------------------------------------


def _equity_call(name: str, ctx: CapabilityContext, params: Any) -> Any:
    """Invoke the equity implementation through the registry, as a surface would."""
    return REGISTRY[name].impls[AssetClass.EQUITY].fn(ctx, params)


_TICKER = "AAPL"


def _quote(index: int, bid_px: float, bid_sz: float, ask_px: float, ask_sz: float) -> Quote:
    ts = _BASE_NS + index * _SEC_NS
    return Quote(
        source="alpaca",
        symbol=_TICKER,
        symbol_raw=_TICKER,
        source_ts=ts,
        local_ts=ts,
        asset_class=AssetClass.EQUITY,
        bid_px=bid_px,
        bid_sz=bid_sz,
        ask_px=ask_px,
        ask_sz=ask_sz,
    )


@pytest.fixture
def quote_ctx(tmp_path: Path) -> CapabilityContext:
    """An equity context over a lake holding three consecutive quotes."""
    load_all()
    asyncio.run(
        _write(
            tmp_path,
            [
                _quote(0, 100.0, 2.0, 101.0, 1.0),
                _quote(1, 100.0, 3.0, 101.0, 2.0),
                _quote(2, 101.0, 4.0, 102.0, 1.0),
            ],
        )
    )
    return CapabilityContext(
        catalog=Catalog(tmp_path),
        settings=Settings(data_dir=tmp_path),
        asset_class=AssetClass.EQUITY,
    )


def test_ofi_for_equities_differences_the_quote_stream_the_lake_actually_holds(
    quote_ctx: CapabilityContext,
) -> None:
    """M7, through the registry rather than through the analytics function.

    A capability wired to the wrong callable, or to one reading a channel no equity provider
    writes, fails here rather than on whichever surface projects it first — which is exactly
    how ``slippage``'s equity half shipped for a phase.
    """
    rows = _equity_call("ofi", quote_ctx, OfiParams(_TICKER, _BASE_NS, _BASE_NS + 10 * _SEC_NS))
    assert rows.columns == ["timestamp", "best_bid", "best_ask", "ofi"]
    assert len(rows) == 1
    # Step one is size-only (+1 bid, +1 ask, so 0.0); step two improves both prices
    # (+4 bid, -2 ask, so 6.0). One 1m bin holds both.
    assert rows["ofi"][0] == pytest.approx(6.0)
    assert rows["best_bid"][0] == pytest.approx(101.0)


def test_the_two_asset_classes_of_ofi_are_not_the_same_function() -> None:
    """One statistic, two channels, and therefore two callables.

    The crypto adapter reads ``book_snapshot`` and no equity provider writes one, so binding
    it for both would be ``slippage``'s defect repeated: a declaration naming a basis whose
    code path cannot execute. What *is* shared is the arithmetic, one level down, and that
    sharing is pinned in ``tests/equity/test_ofi.py`` by running both and comparing frames.
    """
    load_all()
    impls = REGISTRY["ofi"].impls
    assert impls[AssetClass.CRYPTO].fn is not impls[AssetClass.EQUITY].fn
    assert impls[AssetClass.EQUITY].basis == "native"
    assert impls[AssetClass.EQUITY].prov is Provenance.DERIVED


def _tailed_profile(basis: str, inputs: dict[str, int]) -> DepthProfile:
    tail = provenance_fields(basis, inputs)
    return DepthProfile(
        source="synth",
        symbol=f"synth:{_TICKER}",
        symbol_raw=_TICKER,
        local_ts=_BASE_NS,
        source_ts=None,
        asset_class=AssetClass.EQUITY,
        bids=[(99.0, 100.0), (98.0, 200.0)],
        asks=[(101.0, 50.0), (102.0, 200.0)],
        reference_price=100.0,
        depth=4,
        prov=tail.prov,
        prov_basis=tail.prov_basis,
        prov_confidence=tail.prov_confidence,
        prov_inputs=tail.prov_inputs,
    )


def test_liquidity_depth_for_equities_sums_the_ladder_m6_built(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M6, through the registry: bands over the same book ``depth`` and ``slippage`` read."""
    monkeypatch.setattr(
        "crocodile.capabilities.analytics.select_depth_source",
        lambda **_: _StubDepthSource(_tailed_profile("yahoo_1m_vap", {"n_volume_bars": 390})),
    )
    rows = _equity_call(
        "liquidity-depth", _equity_ctx(tmp_path), LiquidityDepthParams(symbol=_TICKER)
    )
    assert len(rows) == 1
    row = rows.row(0, named=True)
    assert row["reference_price"] == pytest.approx(100.0)
    assert row["bid_depth_1pct"] == pytest.approx(100.0)
    assert row["ask_depth_1pct"] == pytest.approx(50.0)
    assert row["bid_depth_2pct"] == pytest.approx(300.0)
    assert row["ask_depth_2pct"] == pytest.approx(250.0)


def test_the_equity_band_row_says_which_branch_of_the_ladder_answered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The declaration is a ceiling; only the row can be a measurement.

    Both branches are exercised because the difference between them is the difference
    between resting quotes and traded volume standing in for them — and the declaration says
    ``alpaca_l1`` either way, by design, because ``Impl.prov`` is documented as a maximum.
    """
    def _fixed(basis: str, inputs: dict[str, int]) -> Any:
        def _select(**_: Any) -> _StubDepthSource:
            return _StubDepthSource(_tailed_profile(basis, inputs))

        return _select

    for basis, inputs, level in (
        ("yahoo_1m_vap", {"n_volume_bars": 195}, Provenance.SYNTHETIC),
        ("alpaca_l1", {"n_quoted_sides": 2}, Provenance.DERIVED),
    ):
        monkeypatch.setattr(
            "crocodile.capabilities.analytics.select_depth_source", _fixed(basis, inputs)
        )
        row = _equity_call(
            "liquidity-depth", _equity_ctx(tmp_path), LiquidityDepthParams(symbol=_TICKER)
        ).row(0, named=True)
        assert row["prov"] == level.value
        assert row["prov_basis"] == basis
    load_all()
    assert REGISTRY["liquidity-depth"].impls[AssetClass.EQUITY].basis == "alpaca_l1"


def test_the_band_sums_ask_for_the_whole_ladder_and_not_the_top_of_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cumulative sum over a truncated ladder under-reports the quantity it names.

    ``depth`` and ``slippage`` take the synthetic source's ``top_n=10`` default because they
    report the top of a ladder and a walk from the touch, where ten levels is an answer. A
    band sum is not: on a session whose range is 2 %, forty buckets span 2 % and ten of them
    span about half a percent, so the default would silently truncate even the 1 % band — by
    an amount that depends on how wide the day happened to be.
    """
    captured: dict[str, Any] = {}

    def _record(**kwargs: Any) -> _StubDepthSource:
        captured.update(kwargs)
        return _StubDepthSource(_tailed_profile("alpaca_l1", {"n_quoted_sides": 2}))

    monkeypatch.setattr("crocodile.capabilities.analytics.select_depth_source", _record)
    _equity_call("liquidity-depth", _equity_ctx(tmp_path), LiquidityDepthParams(symbol=_TICKER))
    assert captured["top_n"] == captured["bins"], (
        f"asked for {captured['top_n']} of {captured['bins']} buckets; a band sum over a "
        f"truncated ladder is a sum over a sample, and nothing in the answer would say so"
    )


def test_liquidity_depth_declares_the_same_ceiling_as_depth_over_the_same_equity_book() -> None:
    """Three capabilities, one ``select_depth_source``, one ceiling between them.

    ``slippage`` and ``depth`` already had to be reconciled after two batches declared
    opposite ends of one range over this book. A third reader arriving with a third opinion
    is the same defect with more places to look, so it is asserted rather than remembered.
    """
    load_all()
    bands = REGISTRY["liquidity-depth"].impls[AssetClass.EQUITY]
    for name in ("depth", "slippage"):
        other = REGISTRY[name].impls[AssetClass.EQUITY]
        assert (bands.prov, bands.basis) == (other.prov, other.basis), name


def test_chaos_score_for_equities_publishes_the_weights_its_index_was_built_from(
    tmp_path: Path,
) -> None:
    """Two of the four terms are re-specified for equities and one may have no reading.

    A composite that keeps its name and scale while silently dropping a term is the quiet
    dishonesty this registry's gates exist to catch, so the weights are in the answer. With
    four finite readings they are 0.25 each and the number is the crypto half's.
    """
    ctx = _equity_ctx(tmp_path)
    params = ChaosScoreParams(
        volatility=0.1,
        stablecoin_deviation=0.01,
        orderbook_imbalance=1.0,
        sequencer_delay=5.0,
    )
    result = _equity_call("chaos-score", ctx, params)
    assert result["chaos_score"] == pytest.approx(_call("chaos-score", ctx, params), rel=1e-12)
    assert {name: term["weight"] for name, term in result["terms"].items()} == {
        "volatility": pytest.approx(0.25),
        "stablecoin_deviation": pytest.approx(0.25),
        "orderbook_imbalance": pytest.approx(0.25),
        "sequencer_delay": pytest.approx(0.25),
    }


def test_the_equity_composite_drops_a_reading_the_crypto_one_would_have_invented(
    tmp_path: Path,
) -> None:
    """The one behavioural divergence between the halves, through the registry.

    The crypto half maps a NaN volatility to 0.0 and a NaN imbalance to 1.0 — two opposite
    inventions from one absence. The equity half drops the term and divides the index
    between the readings that exist, which matters because this tree's equity analytics
    return NaN as their "not enough data" answer.
    """
    ctx = _equity_ctx(tmp_path)
    params = ChaosScoreParams(
        volatility=float("nan"),
        stablecoin_deviation=0.01,
        orderbook_imbalance=1.0,
        sequencer_delay=5.0,
    )
    result = _equity_call("chaos-score", ctx, params)
    assert result["terms"]["volatility"]["weight"] == 0.0
    assert result["chaos_score"] > _call("chaos-score", ctx, params)


def test_the_three_capabilities_this_phase_closed_left_the_ledger() -> None:
    """The ledger's hoarding rule, asserted with these three names rather than in general.

    A settled entry that stays makes a later deletion of the equity half invisible, because
    the name was already excused. ``depth`` is deliberately absent from this list rather than
    asserted to still be on the ledger: its gap runs the other way — the crypto half is the
    missing one — and it belongs to whoever closes that, so pinning its state here would make
    this test fail on their work rather than on this batch's.
    """
    from crocodile.core.capability import PENDING_SYMMETRY

    load_all()
    for name in ("ofi", "liquidity-depth", "chaos-score"):
        assert set(REGISTRY[name].impls) == {AssetClass.CRYPTO, AssetClass.EQUITY}
        assert name not in PENDING_SYMMETRY
