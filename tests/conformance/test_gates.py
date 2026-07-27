"""The contracts that make symmetry a build failure.

Gate 1 — every record carries the canonical header.
Gate 2 — every capability is implemented for both asset classes (Task 13).
Gate 3 — every emitted prov_basis has a registered formula (Task 13).
Gate 4 — every capability appears in all three surfaces (Phase 2).
"""

import ast
import inspect
import pathlib
from collections import Counter
from collections.abc import Iterator, Mapping
from typing import get_args

import msgspec
import pytest

from crocodile.core.schema import records
from crocodile.core.schema.enums import CHANNEL_SUCCESSORS, AssetClass, Channel, Side
from crocodile.core.schema.provenance import Provenance, provenance_fields
from crocodile.core.schema.records import OHLCV, Funding, OptionsChain, Record, Trade, _Header

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
    assert names[: len(CANONICAL_HEADER)] == CANONICAL_HEADER, (
        f"{cls.__name__} header is {names[: len(CANONICAL_HEADER)]}"
    )


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
    assert prov.default is not Provenance.UNAVAILABLE, (
        f"{cls.__name__} defaults prov to UNAVAILABLE; a record is always a real observation"
    )


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


def test_gate1_channel_enum_covers_every_record_tag() -> None:
    """``Channel`` names at least every tag a record declares.

    A subset, deliberately not an equality: ``Channel`` also carries members whose
    structs arrive with the equity port, plus ``bar`` and ``option_quote``, which have
    stored data in existing lakes and stay after their structs collapse into ``ohlcv``
    and ``options_chain``. What keeps those partitions readable is the read path
    ``CHANNEL_SUCCESSORS`` declares, not the member; see the gate below.
    """
    tags = {c.__struct_config__.tag for c in _declared_record_types()}
    members = {c.value for c in Channel}
    assert tags <= members, f"record tags with no Channel member: {sorted(tags - members)}"


def test_gate1_channel_members_without_a_record_have_a_read_path() -> None:
    """Channel may outlive a struct, but only with somewhere for its rows to go.

    This gate used to assert that the member was *declared*, and called deleting one
    "data loss wearing a passing build". The declaration prevented nothing. A lake
    holding ``channel=bar/`` alongside ``channel=ohlcv/`` returned the ``ohlcv`` half
    and called it all of it, and ``replay(["bar"])`` raised ``Unknown channel tag``, so
    those rows were unreachable through the record API entirely — with ``Channel.BAR``
    declared the whole time. Only a read path prevents the loss, so that is what is
    asserted: every orphaned member names the tag that absorbed it, and that tag has a
    live record.

    ``book_ticker`` is not an orphan and is not exempted. Equity's ``BookTicker`` was an
    alias of ``Quote``, but the crypto struct of that name survives, so the member has a
    record and never reaches the check below.

    Any member that is neither backed by a record nor mapped to one is a porting
    mistake. The fix is never to delete it — that really would drop a partition on the
    floor — but to port the record or declare the successor.
    """
    tags = {c.__struct_config__.tag for c in _declared_record_types()}
    orphans = {c.value for c in Channel} - tags

    unmapped = sorted(o for o in orphans if o not in CHANNEL_SUCCESSORS)
    assert not unmapped, (
        f"Channel members with no record and no successor: {unmapped}. "
        f"A declared member is not a read path; map it in CHANNEL_SUCCESSORS or port "
        f"the record."
    )
    dangling = sorted(o for o in orphans if CHANNEL_SUCCESSORS[o] not in tags)
    assert not dangling, f"retired tags whose successor has no record either: {dangling}"


@pytest.mark.parametrize(
    ("retired", "row"),
    [
        pytest.param(
            "bar",
            {
                "interval": "1d",
                "open": 1.0,
                "high": 2.0,
                "low": 0.5,
                "close": 1.5,
                "volume": 9.0,
                "trade_count": 77,
            },
            id="bar",
        ),
        pytest.param(
            "option_quote",
            {"underlying": "AAPL", "expiry": "2026-06-18", "strike": 1.0, "type": "C"},
            id="option_quote",
        ),
    ],
)
def test_gate1_a_row_under_a_retired_tag_decodes_into_its_successor(
    retired: str, row: dict[str, object]
) -> None:
    """The read path the gate above requires, exercised rather than described.

    ``CHANNEL_SUCCESSORS`` naming a tag is only half of a read path; the other half is
    ``from_row`` accepting a row that carries it.
    """
    from crocodile.core.store.rows import from_row

    record = from_row(
        {
            "channel": retired,
            "provider": "alpaca",
            "symbol": "AAPL",
            "symbol_raw": "AAPL",
            "source_ts": None,
            "local_ts": 1_700_000_000_000_000_000,
            "date": "2023-11-14",
            "bucket": 42,
            **row,
        }
    )

    assert record.__struct_config__.tag == CHANNEL_SUCCESSORS[retired]


def test_option_expiry_is_nanoseconds() -> None:
    """Equity spelled expiry ``YYYY-MM-DD``; a date cannot express an intraday expiry.

    Nanoseconds are the unit every other timestamp in this codebase uses, so the merge of
    ``OptionQuote`` into ``OptionsChain`` converts rather than widens to ``int | str``.
    """
    fields = {f.name: f.type for f in msgspec.structs.fields(OptionsChain)}
    assert fields["expiry"] is int, "expiry must be UTC epoch nanoseconds, not a date string"


def test_ohlcv_keeps_vwap_and_keeps_it_optional() -> None:
    """``vwap`` is the one equity bar field with no crypto counterpart.

    Equity's ``Bar`` and crypto's ``OHLCV`` were nearly the same struct, which is exactly
    what makes this field easy to lose: ``trade_count`` was a rename to ``num_trades``,
    but ``vwap`` was not a rename at all. It must stay, and stay optional — most crypto
    venues do not publish one, and ``0.0`` would be a false price where ``None`` is honest.
    """
    field = next(f for f in msgspec.structs.fields(OHLCV) if f.name == "vwap")
    assert field.type == float | None, f"vwap must be float | None, got {field.type}"
    assert field.default is None, "vwap must default to None, not to a fabricated price"


def test_gate1_the_union_discriminates_by_tag() -> None:
    """What widening ``Record`` actually buys: a blob decodes back to its own class."""
    f = Funding(
        source="binance",
        symbol="binance:BTCUSDT",
        symbol_raw="BTCUSDT",
        local_ts=1,
        asset_class=AssetClass.CRYPTO,
        source_ts=None,
        funding_rate=0.0001,
    )
    decoded = msgspec.json.decode(msgspec.json.encode(f), type=Record)
    assert type(decoded) is Funding
    assert decoded.prov_basis == "native"


# ---------------------------------------------------------------------------
# Gate 2 — every capability is implemented for both asset classes.
# ---------------------------------------------------------------------------


def test_gate2_registry_is_not_empty():
    from crocodile.core.capability import REGISTRY

    assert REGISTRY, (
        "an empty registry makes the symmetry gate vacuously true; "
        "Phase 1 seeds it with `indicators`"
    )


def test_gate2_every_capability_is_symmetric():
    """Two ways out, and both cost something.

    ``IRREDUCIBLE`` is a claim about the market and is permanent. ``PENDING_SYMMETRY`` is a
    claim about the schedule and has to be repaid by Phase 3's exit. Anything else fails
    here, which is what stops Phase 2's port from being able to quietly drop a half.
    """
    from crocodile.core.capability import (
        IRREDUCIBLE,
        PENDING_SYMMETRY,
        REGISTRY,
        AssetClass,
    )

    for cap in REGISTRY.values():
        if cap.name in IRREDUCIBLE or cap.name in PENDING_SYMMETRY:
            continue
        assert set(cap.impls) == {AssetClass.CRYPTO, AssetClass.EQUITY}, (
            f"{cap.name} implements {sorted(cap.impls)}; add the missing asset class, "
            f"schedule it in PENDING_SYMMETRY, or justify it in IRREDUCIBLE"
        )


def test_gate2_irreducible_entries_carry_a_justification():
    from crocodile.core.capability import IRREDUCIBLE

    for name, why in IRREDUCIBLE.items():
        assert why.strip(), f"{name} is on IRREDUCIBLE with no justification"


def test_gate2_params_schema_is_json_serialisable():
    """Phase 2 uses this as the MCP inputSchema; a struct that cannot be
    described has to be found now, not then."""
    import msgspec

    from crocodile.core.capability import REGISTRY

    for cap in REGISTRY.values():
        schema = msgspec.json.schema(cap.params)
        assert schema.get("$ref") or schema.get("type"), cap.name


# ---------------------------------------------------------------------------
# Gate 3 — every emitted prov_basis has a registered confidence formula.
# ---------------------------------------------------------------------------


def _crocodile_sources() -> list[pathlib.Path]:
    """Every ``crocodile`` source file, located from this file rather than from the cwd.

    The two gates below scan source text. A relative ``Path("src/crocodile")`` resolves
    against the working directory, so running pytest from anywhere but the repo root would
    make ``rglob`` yield nothing and turn both gates permanently green — the failure mode a
    conformance gate can least afford. Anchoring on ``__file__`` and asserting the tree is
    non-empty means a gate that finds nothing says so instead of passing.
    """
    root = pathlib.Path(__file__).resolve().parents[2] / "src" / "crocodile"
    files = sorted(root.rglob("*.py"))
    assert files, f"no crocodile sources under {root}; these gates would pass vacuously"
    return files


def test_gate3_every_declared_basis_is_registered():
    from crocodile.core.capability import REGISTRY
    from crocodile.core.schema.provenance import load_all_bases, registered_bases

    load_all_bases()
    known = registered_bases()
    for cap in REGISTRY.values():
        for asset_class, impl in cap.impls.items():
            assert impl.basis in known, (
                f"{cap.name}/{asset_class} declares basis {impl.basis!r}, "
                f"which has no registered confidence formula"
            )


def test_gate3_no_source_file_emits_an_unregistered_basis():
    from crocodile.core.schema.provenance import load_all_bases, registered_bases

    # Without this the registry reflects only what happens to have been imported,
    # and a basis registered in an unimported module becomes a false offender.
    load_all_bases()
    known = registered_bases()
    offenders = []
    for path in _crocodile_sources():
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.Call):
                continue
            for kw in node.keywords:
                if kw.arg == "prov_basis" and isinstance(kw.value, ast.Constant):
                    value = kw.value.value
                    if isinstance(value, str) and value not in known:
                        offenders.append(f"{path}:{kw.value.lineno} {value!r}")
    assert not offenders, f"unregistered prov_basis literals: {offenders}"


def test_gate3_no_source_file_hand_writes_a_confidence():
    """The bypass the basis gate alone does not catch.

    ``prov_basis="yahoo_1m_vap", prov_confidence=0.93`` satisfies every other gate
    while never touching the registry — the easy path routing around the
    mechanism. Confidence must come from ``provenance_fields()``, so a literal
    number at a call site is the signature of a hand-assembled tail.
    """
    offenders = []
    for path in _crocodile_sources():
        if path.name == "provenance.py":
            continue  # the registry itself legitimately returns numbers
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.Call):
                continue
            for kw in node.keywords:
                if kw.arg != "prov_confidence" or not isinstance(kw.value, ast.Constant):
                    continue
                if kw.value.value is not None:
                    offenders.append(f"{path}:{kw.value.lineno} = {kw.value.value!r}")
    assert not offenders, (
        f"hand-written prov_confidence literals: {offenders}. "
        f"Build the tail with provenance_fields(basis, inputs) instead."
    )


# ---------------------------------------------------------------------------
# Gate 3b — a module that derives records must say so on every record it builds.
# ---------------------------------------------------------------------------
# Both scanners above look for an explicit ``prov_basis=`` / ``prov_confidence=``
# keyword. Omission is invisible to them, and omission is not a neutral state:
# ``_Header`` defaults to ``prov=NATIVE, prov_basis="native",
# prov_confidence=1.0``, and ``native`` means *the venue reported this value
# directly*. A derived record that simply says nothing therefore ships a false
# claim rather than a missing one — and a silent one, because ``describe()`` is
# what the REST and MCP surfaces are required to emit as a warning for every
# record whose ``prov`` is not NATIVE. ``core.resample.book`` did exactly this:
# it built a reconstructed ``BookSnapshot`` with no ``prov*`` argument at all and
# passed every gate in this file.

DERIVES_RECORDS: dict[str, str] = {
    "core/resample/book.py": (
        "Reconstructs a BookSnapshot from an OrderBook replayed over other records; "
        "nothing it emits was reported by a venue."
    ),
    "equity/resample/book.py": (
        "The same reconstruction for equity input, under the boundary rule that emits "
        "before it applies. It used to fall outside this gate: its snapshot was a legacy "
        "equity struct with no provenance tail, and the only canonical records the "
        "scanner could see were the re-typed inputs `_to_core_record` handed the shared "
        "OrderBook. Both of those are gone — the snapshot it yields is canonical and is "
        "a reconstruction, so it carries `book_resample` with a measured lookahead."
    ),
    "equity/resample/ohlcv.py": (
        "Aggregates bars out of trades, quotes or narrower bars. None of the three was "
        "published by a venue, and the quote one is not even a traded price: its `volume` "
        "is a structural zero. Left silent they would all have inherited the header "
        "default and claimed NATIVE."
    ),
}
"""Modules that build records out of other records, and why each one counts as derived.

The justification is mandatory for the same reason it is on
:data:`crocodile.core.capability.IRREDUCIBLE`: an entry here changes what a gate
demands, so an entry with nothing to say is an entry nobody argued for. A path that
no longer exists, or that no longer builds a canonical record, fails its own gate
rather than sitting here as decoration.

The limit is worth stating: this list is a declaration, and a synthesiser written
somewhere no rule below looks would go undeclared. The discovery rule covers the
``resample`` packages, which is where every derivation in the tree lives today.
"""


def _canonical_record_names() -> frozenset[str]:
    """The class names ``crocodile.core.schema.records`` declares."""
    return frozenset(cls.__name__ for cls in _declared_record_types())


def _canonical_record_calls(tree: ast.AST, names: frozenset[str]) -> list[ast.Call]:
    """Every call in ``tree`` that constructs a canonical record.

    Import aliases are resolved rather than ignored: ``equity.resample.book`` imports
    ``BookSnapshot as CoreBookSnapshot``, and a scanner matching bare class names would
    have read that module as building no records at all — a gate that passes because it
    could not see its subject.
    """
    local: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "crocodile.core.schema.records":
            for alias in node.names:
                if alias.name in names:
                    local[alias.asname or alias.name] = alias.name
    calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id in local:
            calls.append(node)
        elif isinstance(func, ast.Attribute) and func.attr in names:
            # ``records.Trade(...)`` — the module-qualified form.
            calls.append(node)
    return calls


def _source_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[2] / "src" / "crocodile"


def test_gate3b_every_declared_deriving_module_exists_and_builds_records():
    """Self-cleaning, so the list cannot become a place obligations go to be forgotten."""
    names = _canonical_record_names()
    root = _source_root()
    for relative, why in DERIVES_RECORDS.items():
        path = root / relative
        assert path.is_file(), f"DERIVES_RECORDS names {relative}, which does not exist"
        assert why.strip(), f"{relative} is on DERIVES_RECORDS with no justification"
        assert _canonical_record_calls(ast.parse(path.read_text()), names), (
            f"{relative} is on DERIVES_RECORDS but builds no canonical record; "
            f"drop the entry or the gate below guards nothing"
        )


def test_gate3b_a_deriving_module_states_prov_on_every_record_it_builds():
    """Omission is the bypass Gate 3's two scanners cannot see.

    They look for a keyword that is present and wrong. This looks for one that is
    absent, in the modules where absent means a reconstruction inheriting the venue's
    word for itself.
    """
    names = _canonical_record_names()
    root = _source_root()
    offenders = []
    for relative in DERIVES_RECORDS:
        path = root / relative
        for call in _canonical_record_calls(ast.parse(path.read_text()), names):
            if not any(kw.arg == "prov" for kw in call.keywords):
                func = call.func
                built = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "?")
                offenders.append(f"{relative}:{call.lineno} {built}(...)")
    assert not offenders, (
        f"records built without an explicit prov= in a deriving module: {offenders}. "
        f"No prov= means prov=NATIVE, prov_basis='native', prov_confidence=1.0 — "
        f"the claim that a venue reported this value directly."
    )


def test_gate3b_every_resampler_that_builds_a_record_is_declared():
    """A new resampler cannot be added without answering the question.

    Without this the gate only ever asks what it was already told to ask, which is the
    vacuous shape a conformance test can least afford: the list stays green by staying
    short.
    """
    names = _canonical_record_names()
    root = _source_root()
    undeclared = []
    for path in sorted(root.rglob("*.py")):
        if "resample" not in path.relative_to(root).parts:
            continue
        if not _canonical_record_calls(ast.parse(path.read_text()), names):
            continue
        relative = path.relative_to(root).as_posix()
        if relative not in DERIVES_RECORDS:
            undeclared.append(relative)
    assert not undeclared, (
        f"resampler modules building canonical records but not on DERIVES_RECORDS: "
        f"{undeclared}. Add each with the argument for why it derives, so the "
        f"prov= gate covers it."
    )


# ---------------------------------------------------------------------------
# Gate 3c — a registered formula's output must actually vary with its inputs
# ---------------------------------------------------------------------------
# Gate 3 scans *call sites* for a hand-written ``prov_confidence=``. That is the
# bypass it was built for, and it left the registry itself unwatched — so a
# constant moved one indirection inwards was invisible to every gate here, and the
# test that pinned it (``confidence_for(basis, {}) == 1.0``) could not fail: it
# asserted whatever the registry held, plus that a docstring was non-empty, which
# is not the same as arguing. ``ohlcv_from_ohlcv`` reported 1.0 for a 1d bar built
# from three 1m bars while holding both numbers the ratio needs.
#
# So the registry is probed instead of read: feed each formula different inputs and
# require the answers to differ. A constant is still allowed — ``native`` is 1.0 and
# ``unavailable`` is 0.0 *by definition* — but only when declared and argued below,
# and the declaration is checked against the formula rather than trusted.

CONSTANT_BY_DEFINITION: dict[str, tuple[float, str]] = {
    "native": (
        1.0,
        "A venue-reported value is fully sampled at its own level because it was read "
        "rather than reconstructed. There is no partial reading of a number a venue "
        "published.",
    ),
    "unavailable": (
        0.0,
        "A hole carries no information, so it is fully unsampled at any level. This is "
        "the definition of the level, not a measurement of an instance of it.",
    ),
    "ohlcv_from_trades": (
        1.0,
        "A trade stream declares no denominator. How many prints a bucket should hold is "
        "not a property of the market, so there is nothing to divide by — unlike "
        "ohlcv_from_ohlcv, where each input bar declares its own width, and unlike "
        "yahoo_1m_vap, where a session contains 390 one-minute bars whether or not any "
        "were fetched. The sample size is reported on the record as num_trades.",
    ),
    "ohlcv_from_quotes": (
        1.0,
        "Same argument as ohlcv_from_trades, and the same absent denominator: a quote "
        "stream has no count a bucket ought to contain. A one-quote bucket really does "
        "yield open == high == low == close, and what says so is num_trades = 1 and a "
        "volume that is a structural zero, not a ratio with an invented base.",
    ),
}
"""Bases whose confidence is a constant, the value, and the argument for it.

An entry silences the probe below, which is exactly why the argument is mandatory and
why the value is asserted rather than taken on trust — the same discipline
:data:`crocodile.core.capability.IRREDUCIBLE` carries for the symmetry gate.
"""

_PROBE_VALUES = (0, 1, 2, 3)
"""Ints to feed a formula. Small and non-negative: every formula in the registry
validates its inputs, and a probe that only ever trips validation measures nothing."""


class _KeyRecorder(Mapping[str, object]):
    """A mapping that answers every lookup and remembers what was asked for.

    Formulas declare the observables they need by reading them, not in the registration
    — ``inputs=`` names data *channels*, which is a different thing. Rather than keep a
    second hand-written list of key names for the gate to go stale against, the keys are
    discovered by letting the formula ask.
    """

    def __init__(self, value: object) -> None:
        self._value = value
        self.seen: list[str] = []

    def __getitem__(self, key: str) -> object:
        if key not in self.seen:
            self.seen.append(key)
        return self._value

    def __iter__(self):
        return iter(self.seen)

    def __len__(self) -> int:
        return len(self.seen)


def _shipped_registry() -> dict[str, object]:
    """The bases ``crocodile`` itself registers.

    Reaching into ``_REGISTRY`` is deliberate: ``registered_bases()`` returns names, and
    this gate needs the formula. Tests register throwaway bases in the same process, so
    the registry is filtered to formulas defined under ``crocodile.`` — otherwise this
    gate would pass or fail on test ordering.
    """
    from crocodile.core.schema.provenance import _REGISTRY, load_all_bases

    load_all_bases()
    return {
        basis: registered
        for basis, registered in _REGISTRY.items()
        if getattr(registered.fn, "__module__", "").startswith("crocodile.")
    }


def _observed_keys(fn) -> list[str]:
    """Return the input keys ``fn`` reads, discovered by calling it under a recorder."""
    keys: list[str] = []
    for value in _PROBE_VALUES:
        recorder = _KeyRecorder(value)
        try:
            fn(recorder)
        except Exception:  # noqa: BLE001 - a rejected probe still reveals the keys read
            pass
        for key in recorder.seen:
            if key not in keys:
                keys.append(key)
    return keys


def _probe_results(basis: str, keys: list[str]) -> set[float]:
    """Vary one key at a time over :data:`_PROBE_VALUES` and collect the answers.

    One at a time, because the two-key formulas are ratios: scaling every key together
    leaves ``book_resample``'s ``1 - lookahead/interval`` unchanged, and a probe that
    moves nothing would read a real formula as a constant.
    """
    from crocodile.core.schema.provenance import ConfidenceInputError, confidence_for

    results: set[float] = set()
    for key in keys:
        for value in _PROBE_VALUES:
            inputs = dict.fromkeys(keys, 1)
            inputs[key] = value
            try:
                results.add(confidence_for(basis, inputs))
            except ConfidenceInputError:
                continue
    return results


@pytest.mark.parametrize(
    "basis", sorted(set(_shipped_registry()) - set(CONSTANT_BY_DEFINITION))
)
def test_gate3c_a_registered_formula_varies_with_its_inputs(basis: str) -> None:
    """A confidence that cannot change is a constant, whatever it is spelled as."""
    fn = _shipped_registry()[basis].fn
    keys = _observed_keys(fn)
    assert keys, (
        f"{basis} reads no inputs, so its confidence cannot vary. Measure something "
        f"observable, or declare it in CONSTANT_BY_DEFINITION with the argument."
    )
    results = _probe_results(basis, keys)
    assert len(results) > 1, (
        f"{basis} returned {results} for every probe over {keys}; its confidence does "
        f"not depend on its inputs. A number that never moves is a constant living in "
        f"the registry, which is the one place no other gate looks."
    )


@pytest.mark.parametrize("basis", sorted(CONSTANT_BY_DEFINITION))
def test_gate3c_a_declared_constant_is_the_constant_it_declares(basis: str) -> None:
    """The exemption is checked, not trusted.

    A basis on the list that quietly grew a formula, or that returns something other than
    the value argued for, would otherwise be exempt from both this gate and Gate 3.
    """
    from crocodile.core.schema.provenance import confidence_for, describe

    expected, why = CONSTANT_BY_DEFINITION[basis]
    assert why.strip(), f"{basis} is on CONSTANT_BY_DEFINITION with no argument"
    assert basis in _shipped_registry(), f"{basis} is declared constant but is not registered"

    fn = _shipped_registry()[basis].fn
    keys = _observed_keys(fn)
    observed = _probe_results(basis, keys) if keys else {confidence_for(basis, {})}
    assert observed == {expected}, (
        f"{basis} is declared constant at {expected} but returned {sorted(observed)}"
    )
    assert describe(basis).strip(), f"{basis} must carry the argument in its description too"


def test_gate3c_the_constant_list_holds_no_stale_entry() -> None:
    """A name that left the registry must not leave its exemption behind."""
    unknown = sorted(set(CONSTANT_BY_DEFINITION) - set(_shipped_registry()))
    assert not unknown, f"CONSTANT_BY_DEFINITION names unregistered bases: {unknown}"
