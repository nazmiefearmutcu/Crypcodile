"""The contracts that make symmetry a build failure.

Gate 1 — every record carries the canonical header.
Gate 2 — every capability is implemented for both asset classes (Task 13).
Gate 3 — every emitted prov_basis has a registered formula (Task 13).
Gate 4 — every capability appears in all three surfaces (Phase 2).
"""

import inspect
from collections import Counter
from typing import get_args

import msgspec
import pytest

from crocodile.core.schema import records
from crocodile.core.schema.enums import AssetClass, Channel, Side
from crocodile.core.schema.provenance import Provenance, provenance_fields
from crocodile.core.schema.records import Record, Trade, _Header

CANONICAL_HEADER = (
    "source",
    "symbol",
    "symbol_raw",
    "local_ts",
    "asset_class",
    "source_ts",
    "prov",
    "prov_basis",
    "prov_confidence",
    "prov_inputs",
)


def _declared_record_types() -> tuple[type[msgspec.Struct], ...]:
    """Every tagged struct *declared in* ``records.py``, found without consulting ``Record``.

    The subject list must not come from the thing under test: a struct left out of the
    ``Record`` union is undecodable, and if the union also chose the tests, nothing would
    notice. Merging two modules whose tags overlap on nine names makes exactly that the
    likeliest mistake.
    """
    found = {
        obj: None
        for obj in vars(records).values()
        if isinstance(obj, type)
        and issubclass(obj, msgspec.Struct)
        and obj.__module__ == records.__name__
        and obj.__struct_config__.tag is not None
    }
    # Deduplicated by identity, not by name: while ``Record`` is a single-member alias it
    # is a second module-level binding of the same class, and counting it twice would make
    # the tag-uniqueness check fail on a module that is perfectly correct.
    return tuple(found)


def _union_members() -> tuple[type[msgspec.Struct], ...]:
    """The members of the ``Record`` union, tolerating the single-member alias form."""
    args = get_args(Record)
    return args if args else (Record,)


def _a_trade(**overrides: object) -> Trade:
    """A minimal valid Trade, so each test states only the field it is about."""
    kwargs: dict[str, object] = {
        "source": "deribit",
        "symbol": "deribit:BTC-PERPETUAL",
        "symbol_raw": "BTC-PERPETUAL",
        "local_ts": 1_700_000_000_000_000_000,
        "asset_class": AssetClass.CRYPTO,
        "source_ts": None,
        "id": "42",
        "price": 42_000.5,
        "amount": 0.25,
        "side": Side.BUY,
    }
    kwargs.update(overrides)
    return Trade(**kwargs)  # type: ignore[arg-type]


def test_the_record_module_declares_at_least_one_record() -> None:
    assert len(_declared_record_types()) > 0


def test_every_declared_record_is_reachable_through_the_union() -> None:
    declared = set(_declared_record_types())
    union = set(_union_members())
    assert declared == union, (
        f"declared but not in Record (undecodable): "
        f"{sorted(c.__name__ for c in declared - union)}; "
        f"in Record but not declared here: {sorted(c.__name__ for c in union - declared)}"
    )


@pytest.mark.parametrize("cls", _declared_record_types(), ids=lambda c: c.__name__)
def test_gate1_record_header_conformance(cls: type[msgspec.Struct]) -> None:
    names = tuple(f.name for f in msgspec.structs.fields(cls))
    assert (
        names[: len(CANONICAL_HEADER)] == CANONICAL_HEADER
    ), f"{cls.__name__} header is {names[: len(CANONICAL_HEADER)]}"


@pytest.mark.parametrize("cls", _declared_record_types(), ids=lambda c: c.__name__)
def test_gate1_every_record_is_a_frozen_tagged_struct(cls: type[msgspec.Struct]) -> None:
    cfg = cls.__struct_config__
    assert cfg.frozen, f"{cls.__name__} is not frozen"
    assert cfg.tag_field == "channel", f"{cls.__name__} discriminates on {cfg.tag_field!r}"
    assert isinstance(cfg.tag, str) and cfg.tag, f"{cls.__name__} has no tag"


@pytest.mark.parametrize("cls", _declared_record_types(), ids=lambda c: c.__name__)
def test_gate1_every_tag_is_a_channel_member(cls: type[msgspec.Struct]) -> None:
    tag = cls.__struct_config__.tag
    assert tag in {c.value for c in Channel}, f"{cls.__name__} tag {tag!r} is not a Channel"


def test_gate1_tags_are_unique() -> None:
    counts = Counter(cls.__struct_config__.tag for cls in _declared_record_types())
    assert [tag for tag, n in counts.items() if n > 1] == []


@pytest.mark.parametrize("cls", _declared_record_types(), ids=lambda c: c.__name__)
def test_gate1_no_record_defaults_to_unavailable(cls: type[msgspec.Struct]) -> None:
    """UNAVAILABLE is a capability-envelope state, never a record state.

    A record on disk is always a real observation; a capability with nothing to return
    says so in its envelope rather than fabricating hole-records.
    """
    prov = next(f for f in msgspec.structs.fields(cls) if f.name == "prov")
    assert (
        prov.default is not Provenance.UNAVAILABLE
    ), f"{cls.__name__} defaults prov to UNAVAILABLE; a record is always a real observation"


def test_gate1_the_header_base_is_not_kw_only() -> None:
    """``kw_only`` belongs on the records, never on ``_Header``.

    msgspec orders positional fields ahead of keyword-only ones. Marking the base
    ``kw_only`` while a record is not flips the order to subclass-first and silently
    destroys the header-leads invariant, so probe the base directly rather than waiting
    for a downstream ordering test to notice.
    """
    kinds = {p.name: p.kind for p in inspect.signature(_Header).parameters.values()}
    assert kinds, "_Header has no fields"
    keyword_only = sorted(n for n, k in kinds.items() if k is inspect.Parameter.KEYWORD_ONLY)
    assert keyword_only == [], (
        f"_Header fields are keyword-only ({keyword_only}); "
        f"kw_only belongs on the record structs, not the base"
    )


@pytest.mark.parametrize("cls", _declared_record_types(), ids=lambda c: c.__name__)
def test_gate1_record_bodies_are_kw_only(cls: type[msgspec.Struct]) -> None:
    """The record's own fields are keyword-only; that is what lets the tail live in the base."""
    header = set(CANONICAL_HEADER)
    body = [
        p.name
        for p in inspect.signature(cls).parameters.values()
        if p.name not in header and p.kind is not inspect.Parameter.KEYWORD_ONLY
    ]
    assert body == [], f"{cls.__name__} is missing kw_only=True; positional body fields: {body}"


def test_gate1_round_trips_through_msgspec() -> None:
    original = _a_trade()
    blob = msgspec.json.encode(original)
    assert msgspec.json.decode(blob, type=Record) == original


def test_gate1_source_ts_is_required() -> None:
    """An adapter must state whether the venue supplied a timestamp, as crypto always made it."""
    with pytest.raises(TypeError, match="source_ts"):
        Trade(  # type: ignore[call-arg]
            source="deribit",
            symbol="deribit:BTC-PERPETUAL",
            symbol_raw="BTC-PERPETUAL",
            local_ts=1,
            asset_class=AssetClass.CRYPTO,
            id="1",
            price=1.0,
            amount=1.0,
            side=Side.SELL,
        )


def test_gate1_default_tail_matches_what_provenance_fields_produces() -> None:
    """One encoding of "native", not two.

    A default-constructed record and ``provenance_fields("native")`` must agree, or
    ``WHERE prov_basis = 'native'`` misses every default-constructed record.
    """
    t = _a_trade()
    assert (t.prov, t.prov_basis, t.prov_confidence, t.prov_inputs) == provenance_fields("native")


def test_gate1_mutable_prov_inputs_default_is_not_shared() -> None:
    a, b = _a_trade(), _a_trade()
    a.prov_inputs.append("MUTATED")
    assert b.prov_inputs == []
    assert _a_trade().prov_inputs == []
