"""A depth profile written canonically still answers the query it always answered.

``basis`` and ``is_synthetic`` were record fields on the equity fork's ``DepthProfile``.
On the canonical struct the first is spelled ``prov_basis`` and the second is a computed
property — and a property is not a struct field, so ``to_row`` writes no such key. Left
there, ``SELECT basis, is_synthetic FROM depth`` would not fail against a canonical file;
it would return nulls, and ``WHERE is_synthetic`` would match nothing at all while legacy
rows beside it still matched. The sink derives both columns from the provenance tail at
write time, and this is where that is exercised against the real lake.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from crocodile.core.schema.enums import AssetClass
from crocodile.core.schema.provenance import Provenance, provenance_fields
from crocodile.core.schema.records import DepthProfile, Record
from crocodile.core.store.catalog import Catalog
from crocodile.core.store.parquet_sink import ParquetSink
from crocodile.core.store.rows import from_row, to_row

_TS = 1_700_000_000_000_000_000  # 2023-11-14


def _profile(basis: str = "yahoo_1m_vap", **inputs: int) -> DepthProfile:
    tail = provenance_fields(basis, inputs or {"n_volume_bars": 195})
    return DepthProfile(
        source="synth",
        symbol="synth:AAPL",
        symbol_raw="AAPL",
        local_ts=_TS,
        asset_class=AssetClass.EQUITY,
        source_ts=None,
        prov=tail.prov,
        prov_basis=tail.prov_basis,
        prov_confidence=tail.prov_confidence,
        prov_inputs=tail.prov_inputs,
        bids=[(100.0, 5.0), (99.0, 3.0)],
        asks=[(101.0, 4.0)],
        reference_price=100.5,
        depth=3,
    )


def test_a_depth_profile_is_a_member_of_the_canonical_union() -> None:
    rec = _profile()
    assert type(rec).__struct_config__.tag == "depth"
    assert isinstance(rec, Record)


def test_a_depth_profile_survives_the_flattener_unchanged() -> None:
    rec = _profile()
    row = to_row(rec)
    assert row["channel"] == "depth"
    assert from_row(row) == rec


def test_the_flattener_alone_does_not_produce_the_two_sql_columns() -> None:
    """The premise of the sink's derivation, stated where it can be seen to hold.

    ``is_synthetic`` is a property and ``basis`` is spelled ``prov_basis``, so neither
    survives ``msgspec.structs.asdict``. If either ever became a struct field this would
    fail and the derivation below would be writing a column twice.
    """
    row = to_row(_profile())
    assert "is_synthetic" not in row
    assert "basis" not in row
    assert row["prov_basis"] == "yahoo_1m_vap"


@pytest.mark.parametrize(
    ("basis", "inputs", "expected_synthetic"),
    [
        ("yahoo_1m_vap", {"n_volume_bars": 195}, True),
        ("alpaca_l1", {"n_quoted_sides": 2}, False),
        ("native", {}, False),
    ],
)
def test_the_persisted_columns_answer_the_pre_merge_query(
    tmp_path: Path, basis: str, inputs: dict[str, int], expected_synthetic: bool
) -> None:
    """``SELECT symbol, basis, is_synthetic, depth FROM depth`` — the fork's own query.

    Parametrised over the provenance levels a depth profile can carry, because a
    derivation that hard-coded ``True`` would pass a single-case test and mislabel every
    real L1 snapshot as synthetic.
    """
    rec = _profile(basis, **inputs)
    assert rec.is_synthetic is expected_synthetic, "the property, before anything is written"

    async def _run() -> None:
        sink = ParquetSink(data_dir=tmp_path, max_buffer_rows=1, flush_interval_seconds=0.1)
        await sink.put(rec)
        await sink.close()

    asyncio.run(_run())

    cat = Catalog(tmp_path)
    df = cat.query("SELECT symbol, basis, is_synthetic, depth FROM depth", readonly=True)
    assert df.height == 1
    assert df["basis"][0] == basis
    assert bool(df["is_synthetic"][0]) is expected_synthetic
    assert df["depth"][0] == 3


def test_where_is_synthetic_selects_only_the_synthetic_row(tmp_path: Path) -> None:
    """The predicate form, not just the column.

    A null column does not make ``WHERE is_synthetic`` raise — it makes it match nothing,
    which is the shape of failure this whole derivation exists to prevent.
    """

    async def _run() -> None:
        sink = ParquetSink(data_dir=tmp_path, max_buffer_rows=10, flush_interval_seconds=9999)
        await sink.put(_profile("yahoo_1m_vap", n_volume_bars=195))
        await sink.put(_profile("alpaca_l1", n_quoted_sides=2))
        await sink.flush()

    asyncio.run(_run())

    cat = Catalog(tmp_path)
    df = cat.query("SELECT basis FROM depth WHERE is_synthetic", readonly=True)
    assert df["basis"].to_list() == ["yahoo_1m_vap"]


def test_the_derived_column_agrees_with_the_property(tmp_path: Path) -> None:
    """One predicate, written twice — so it is checked in both places.

    ``DepthProfile.is_synthetic`` compares ``prov`` to ``SYNTHETIC`` on the struct; the
    sink restates that against the flattened row, where ``prov`` has already become a
    string. Nothing but this test stops the two from drifting apart.
    """
    profiles = {
        Provenance.SYNTHETIC: _profile("yahoo_1m_vap", n_volume_bars=1),
        Provenance.DERIVED: _profile("alpaca_l1", n_quoted_sides=1),
        Provenance.NATIVE: _profile("native"),
    }
    assert {p.prov for p in profiles.values()} == set(profiles), "one profile per level"

    async def _run() -> None:
        sink = ParquetSink(data_dir=tmp_path, max_buffer_rows=10, flush_interval_seconds=9999)
        for profile in profiles.values():
            await sink.put(profile)
        await sink.flush()

    asyncio.run(_run())

    cat = Catalog(tmp_path)
    df = cat.query("SELECT prov, is_synthetic FROM depth", readonly=True)
    persisted = {row["prov"]: row["is_synthetic"] for row in df.to_dicts()}
    assert persisted == {level.value: profile.is_synthetic for level, profile in profiles.items()}
