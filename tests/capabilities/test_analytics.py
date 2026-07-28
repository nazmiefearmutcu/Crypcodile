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
import math
from pathlib import Path
from typing import Any

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
from crocodile.core.schema.records import (
    BookSnapshot,
    DepthProfile,
    DerivativeTicker,
    Funding,
    Liquidation,
    OptionsChain,
    Trade,
)
from crocodile.core.store.catalog import Catalog
from crocodile.core.store.parquet_sink import ParquetSink

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


_SYMMETRIC = (
    "indicators",
    "slippage",
    # M5's five. `basis`, `perp-basis`, `spot-future-basis` and `funding-apr` read equity
    # legs through `crocodile.equity.analytics.carry`; `funding-predict` binds the one
    # offline forecaster to both classes, on the `indicators` argument rather than the
    # `fn=slippage` one — it reads no lake at all, so there is no channel for the two
    # halves to differ over.
    "basis",
    "funding-apr",
    "funding-predict",
    "perp-basis",
    "spot-future-basis",
)


@pytest.mark.parametrize("name", [n for n in _DECLARED if n not in _SYMMETRIC])
def test_every_crypto_only_capability_is_scheduled_against_a_spec_method(name: str) -> None:
    """The other half of declaring an asymmetric capability, asserted per name.

    ``test_pending_symmetry.py`` proves the ledger's rules hold; this proves this batch
    actually filed under them, so a declaration added later without a schedule fails here
    with its own name rather than in a loop over the whole registry.
    """
    from crocodile.core.capability import PENDING_SYMMETRY, SPEC_METHODS

    load_all()
    assert set(REGISTRY[name].impls) == {AssetClass.CRYPTO}
    assert PENDING_SYMMETRY[name] in SPEC_METHODS


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
    assert answer["method"] in {"xgboost", "rolling_mean"}
    assert isinstance(answer["predicted_funding_rate"], float)


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
