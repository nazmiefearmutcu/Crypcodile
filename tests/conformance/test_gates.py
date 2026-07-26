"""The contracts that make symmetry a build failure.

Gate 1 — every record carries the canonical header.
Gate 2 — every capability is implemented for both asset classes (Task 13).
Gate 3 — every emitted prov_basis has a registered formula (Task 13).
Gate 4 — every capability appears in all three surfaces (Phase 2).
"""

from typing import get_args

import msgspec
import pytest

from crocodile.core.schema.records import Record

LEADING = ("source", "symbol", "symbol_raw", "local_ts")
TRAILING = ("source_ts", "prov", "prov_basis", "prov_confidence", "prov_inputs")


def _record_types() -> tuple[type[msgspec.Struct], ...]:
    args = get_args(Record)
    return args if args else (Record,)


def test_the_record_union_is_not_empty():
    assert len(_record_types()) > 0


@pytest.mark.parametrize("cls", _record_types(), ids=lambda c: c.__name__)
def test_gate1_record_header_conformance(cls: type[msgspec.Struct]) -> None:
    names = tuple(f.name for f in msgspec.structs.fields(cls))
    assert names[:4] == LEADING, f"{cls.__name__} leading fields are {names[:4]}"
    assert names[-5:] == TRAILING, f"{cls.__name__} trailing fields are {names[-5:]}"


@pytest.mark.parametrize("cls", _record_types(), ids=lambda c: c.__name__)
def test_gate1_every_record_is_a_frozen_tagged_struct(cls: type[msgspec.Struct]) -> None:
    cfg = cls.__struct_config__
    assert cfg.frozen, f"{cls.__name__} is not frozen"
    assert cfg.tag_field == "channel", f"{cls.__name__} discriminates on {cfg.tag_field!r}"
    assert isinstance(cfg.tag, str) and cfg.tag, f"{cls.__name__} has no tag"


def test_gate1_round_trips_through_msgspec():
    from crocodile.core.schema.enums import Side
    from crocodile.core.schema.records import Trade

    original = Trade(
        source="deribit",
        symbol="deribit:BTC-PERPETUAL",
        symbol_raw="BTC-PERPETUAL",
        local_ts=1_700_000_000_000_000_000,
        id="42",
        price=42_000.5,
        amount=0.25,
        side=Side.BUY,
    )
    blob = msgspec.json.encode(original)
    assert msgspec.json.decode(blob, type=Record) == original


def test_gate1_records_default_to_native_provenance():
    from crocodile.core.schema.enums import Side
    from crocodile.core.schema.provenance import Provenance
    from crocodile.core.schema.records import Trade

    t = Trade(
        source="deribit",
        symbol="deribit:BTC-PERPETUAL",
        symbol_raw="BTC-PERPETUAL",
        local_ts=1,
        id="1",
        price=1.0,
        amount=1.0,
        side=Side.SELL,
    )
    assert t.prov is Provenance.NATIVE
    assert t.prov_basis is None
    assert t.prov_confidence is None
    assert t.prov_inputs is None
