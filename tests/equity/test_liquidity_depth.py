"""M6: band depth over the equity ladder, and the tail that says which ladder it was.

The band sums themselves are pinned in ``tests/core/test_liquidity_depth_bands.py``. This
file is about the row built around them — that the reference price is the profile's own,
that the provenance the ladder measured for itself survives onto the frame, and that a
profile with no usable centre yields no row rather than six zeros.
"""

from __future__ import annotations

import polars as pl
import pytest

from crocodile.core.schema.enums import AssetClass
from crocodile.core.schema.provenance import Provenance, provenance_fields
from crocodile.core.schema.records import DepthProfile
from crocodile.equity.analytics.liquidity_depth import (
    LIQUIDITY_DEPTH_SCHEMA,
    liquidity_depth_from_profile,
)

_TS = 1_704_067_200_000_000_000  # 2024-01-01 00:00:00 UTC


def _profile(
    basis: str = "yahoo_1m_vap",
    *,
    reference_price: float = 100.0,
    inputs: dict[str, int] | None = None,
) -> DepthProfile:
    tail = provenance_fields(basis, inputs if inputs is not None else {"n_volume_bars": 195})
    return DepthProfile(
        source="synth",
        symbol="synth:AAPL",
        symbol_raw="AAPL",
        local_ts=_TS,
        source_ts=None,
        asset_class=AssetClass.EQUITY,
        bids=[(99.5, 10.0), (99.0, 20.0), (98.0, 40.0), (95.0, 80.0), (90.0, 160.0)],
        asks=[(100.5, 1.0), (101.0, 2.0), (102.0, 4.0), (105.0, 8.0), (110.0, 16.0)],
        reference_price=reference_price,
        depth=10,
        prov=tail.prov,
        prov_basis=tail.prov_basis,
        prov_confidence=tail.prov_confidence,
        prov_inputs=tail.prov_inputs,
    )


def test_one_profile_is_one_row_of_band_sums() -> None:
    """The crypto half emits one row per stored book sequence; a live ladder is one look."""
    frame = liquidity_depth_from_profile(_profile())
    assert len(frame) == 1
    row = frame.row(0, named=True)
    assert row["local_ts"] == _TS
    assert row["reference_price"] == pytest.approx(100.0)
    assert row["depth"] == 10
    assert row["bid_depth_1pct"] == pytest.approx(30.0)
    assert row["ask_depth_1pct"] == pytest.approx(3.0)
    assert row["bid_depth_5pct"] == pytest.approx(150.0)
    assert row["ask_depth_5pct"] == pytest.approx(15.0)


def test_the_bands_are_centred_on_the_profiles_own_reference_and_not_on_the_touch() -> None:
    """The crypto half's mid is not available here, and substituting one would be wrong.

    On the synthetic branch the innermost levels are histogram buckets either side of the
    last close, not quotes anyone posted, so their mid is a price the market never showed.
    Moving the reference moves every band with it, which is what this measures.
    """
    near = liquidity_depth_from_profile(_profile(reference_price=100.0)).row(0, named=True)
    far = liquidity_depth_from_profile(_profile(reference_price=96.0)).row(0, named=True)
    assert near["bid_depth_1pct"] == pytest.approx(30.0)
    assert near["ask_depth_1pct"] == pytest.approx(3.0)
    # Centred at 96.0 the 1 % band runs from 95.04 to 96.96. The bid side gains the levels
    # that were more than a percent below 100.0 and loses the 95.0 one; the ask side loses
    # everything, because every ask on this ladder is above the band's top. Bids above the
    # centre still count — the crossed-book rule, pinned in tests/core.
    assert far["bid_depth_1pct"] == pytest.approx(70.0)
    assert far["ask_depth_1pct"] == pytest.approx(0.0)


@pytest.mark.parametrize(
    ("basis", "inputs", "level", "confidence"),
    [
        ("yahoo_1m_vap", {"n_volume_bars": 195}, Provenance.SYNTHETIC, 0.5),
        ("yahoo_1m_vap", {"n_volume_bars": 390}, Provenance.SYNTHETIC, 1.0),
        ("alpaca_l1", {"n_quoted_sides": 2}, Provenance.DERIVED, 1.0),
        ("alpaca_l1", {"n_quoted_sides": 1}, Provenance.DERIVED, 0.5),
    ],
)
def test_the_tail_the_ladder_measured_for_itself_reaches_the_row(
    basis: str, inputs: dict[str, int], level: Provenance, confidence: float
) -> None:
    """The declaration states a ceiling; only the row can say which branch ran.

    ``Impl.prov`` is fixed at import time as ``DERIVED``/``alpaca_l1`` — the best the switch
    can do — so a keyless deployment summing a volume-at-price histogram would otherwise be
    indistinguishable, in the answer, from a keyed one summing quotes. That is the whole
    difference between a book and a histogram, and it is carried here.
    """
    row = liquidity_depth_from_profile(_profile(basis, inputs=inputs)).row(0, named=True)
    assert row["prov"] == level.value
    assert row["prov_basis"] == basis
    assert row["prov_confidence"] == pytest.approx(confidence)
    assert row["prov_inputs"] == (["ohlcv"] if basis == "yahoo_1m_vap" else ["quote"])


def test_a_profile_with_no_usable_centre_yields_no_row_rather_than_six_zeros() -> None:
    """Every band is multiplicative, so a non-positive centre collapses all of them.

    Six zero sums under the ladder's own confidence would read as a book with no near depth
    — a measurement of nothing, reported as a measurement. No row says the same thing
    without asserting it.
    """
    frame = liquidity_depth_from_profile(_profile(reference_price=0.0))
    assert frame.is_empty()
    assert frame.schema == pl.Schema(LIQUIDITY_DEPTH_SCHEMA)


def test_an_empty_answer_carries_the_columns_a_populated_one_does() -> None:
    """A caller selecting ``bid_depth_1pct`` on a quiet symbol gets a column, not an error."""
    empty = liquidity_depth_from_profile(_profile(reference_price=-1.0))
    populated = liquidity_depth_from_profile(_profile())
    assert empty.columns == populated.columns
