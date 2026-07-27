"""No record field may disappear when the two legacy unions are deleted.

Track A of Phase 2 deletes 42 record structs across two legacy unions and moves every
connector's ``normalize()`` onto the canonical one. That is the same move Phase 1 made
with seven shared modules, on the same reasoning — "the canonical version is the superset"
— and Phase 1 was wrong seven times. Nothing raised then either; the queries just came
back empty.

So the superset claim is measured here rather than assumed. ``premerge_record_schema.json``
freezes every struct, tag and field both unions defined while they still existed. This gate
resolves each of those 505 fields onto the canonical union, and the only thing allowed to
stand between a legacy name and a canonical one is an entry in the rename tables below —
each of which carries the argument for why it is a rename and not a loss.

A rename that is not in these tables is a field that disappeared.
"""

from __future__ import annotations

import json
import pathlib
import typing

import msgspec

from crocodile.core.schema import records as canonical

_FIXTURE = pathlib.Path(__file__).parent / "premerge_record_schema.json"


# ---------------------------------------------------------------------------
# The rename tables. Every entry is an argument, not a mapping.
# ---------------------------------------------------------------------------

_HEADER_RENAMES: dict[str, str] = {
    "exchange": "source",
    "provider": "source",
    "exchange_ts": "source_ts",
}
"""Design §2.2: the two forks' record headers differed only in these names.

`exchange` (crypto) and `provider` (equity) named the same thing — where the observation
came from — so one name serves both, and it is the one that does not presume a market.
"""

_STRUCT_RENAMES: dict[tuple[str, str], str] = {
    ("equity", "Bar"): "OHLCV",
    ("equity", "OptionQuote"): "OptionsChain",
    ("equity", "BookTicker"): "Quote",
}
"""Legacy struct → the canonical struct that absorbed it.

- ``Bar`` and equity's own ``OHLCV`` were field-for-field identical (design §13.1); only
  one survives, and the name that says what the record contains wins.
- ``OptionQuote`` and crypto's ``OptionsChain`` describe one option contract in two
  dialects. The crypto dialect wins because it distinguishes a price from a size.
- ``BookTicker = Quote`` was an alias the fork left behind for its on-chain records
  (design §13.1); it never was a separate type.
"""

_FIELD_RENAMES: dict[tuple[str, str, str], tuple[str, str]] = {
    ("equity", "Trade", "size"): ("amount", "one name for one quantity; crypto's spelling"),
    ("equity", "Bar", "trade_count"): ("num_trades", "equity spelling → crypto spelling"),
    ("equity", "OHLCV", "trade_count"): ("num_trades", "same rename, equity's own OHLCV"),
    ("equity", "DepthProfile", "basis"): (
        "prov_basis",
        "design §6.5: equity's basis/is_synthetic pair is the prototype the provenance "
        "tail generalized, so it moved into the header rather than being deleted",
    ),
    ("equity", "DepthProfile", "is_synthetic"): (
        "prov",
        "same: the claim now lives in `prov`. `is_synthetic` is retained as a computed "
        "property for Python readers, and — because a property is not a struct field and so "
        "not a column — the sink derives the persisted `is_synthetic` column from `prov` at "
        "write time. The property alone would have kept `WHERE is_synthetic` working only "
        "for rows already on disk; against a canonical file it matches nothing rather than "
        "erroring, which answers with the pre-merge half of the lake",
    ),
    ("equity", "OptionQuote", "type"): ("opt_type", "`type` shadows the builtin"),
    ("equity", "OptionQuote", "bid"): ("bid_px", "a price, named as one"),
    ("equity", "OptionQuote", "ask"): ("ask_px", "a price, named as one"),
    ("equity", "OptionQuote", "last"): ("last_price", "a price, named as one"),
    ("equity", "OptionQuote", "implied_volatility"): (
        "mark_iv",
        "a feed that publishes one IV per contract is publishing the mark",
    ),
}
"""(fork, legacy struct, legacy field) → (canonical field, why it is a rename)."""


# ---------------------------------------------------------------------------


def _snapshot() -> dict[str, dict[str, dict]]:
    raw = json.loads(_FIXTURE.read_text())
    return {k: v for k, v in raw.items() if not k.startswith("_")}


def _canonical_structs() -> dict[str, type[msgspec.Struct]]:
    return {
        name: obj
        for name in dir(canonical)
        if isinstance(obj := getattr(canonical, name), type)
        and issubclass(obj, msgspec.Struct)
        and obj.__module__ == canonical.__name__
    }


def _canonical_by_tag() -> dict[str, type[msgspec.Struct]]:
    out = {}
    for s in _canonical_structs().values():
        tag = s.__struct_config__.tag
        if tag:
            out[tag] = s
    return out


def _names_on(s: type[msgspec.Struct]) -> set[str]:
    """Fields plus properties: a field that became a computed property is not lost."""
    return set(s.__struct_fields__) | {n for n in dir(s) if not n.startswith("_")}


def _resolve_struct(fork: str, name: str, tag: str | None) -> type[msgspec.Struct] | None:
    by_name = _canonical_structs()
    renamed = _STRUCT_RENAMES.get((fork, name))
    if renamed:
        return by_name.get(renamed)
    return by_name.get(name) or (_canonical_by_tag().get(tag) if tag else None)


def test_the_fixture_is_the_size_it_should_be() -> None:
    """Guard the guard.

    An emptied or truncated fixture would make every assertion below pass while checking
    nothing — which is precisely how a gate goes green over an empty tree.
    """
    snap = _snapshot()
    assert set(snap) == {"crypto", "equity"}
    assert len(snap["crypto"]) == 16, "the crypto union had 16 structs"
    assert len(snap["equity"]) == 26, "the equity union had 26 structs"
    total = sum(len(s["fields"]) for fork in snap.values() for s in fork.values())
    assert total == 505, f"the two unions declared 505 fields between them, found {total}"


def test_every_legacy_record_type_has_a_canonical_counterpart() -> None:
    unresolved = [
        f"{fork}.{name} (tag={body['tag']})"
        for fork, structs in _snapshot().items()
        for name, body in structs.items()
        if _resolve_struct(fork, name, body["tag"]) is None
    ]
    assert not unresolved, (
        "record types with nowhere to go in the canonical union: "
        f"{unresolved}. Either add the struct or record the absorption in _STRUCT_RENAMES."
    )


def test_no_record_field_disappeared() -> None:
    """The assertion Track A is not allowed to proceed without.

    Every field either survives under its own name, survives under a renamed one with a
    written reason, or this fails.
    """
    lost: list[str] = []
    for fork, structs in _snapshot().items():
        for name, body in structs.items():
            target = _resolve_struct(fork, name, body["tag"])
            if target is None:
                continue  # reported by the test above
            available = _names_on(target)
            for field in body["fields"]:
                mapped = (
                    _FIELD_RENAMES.get((fork, name, field), (None, ""))[0]
                    or _HEADER_RENAMES.get(field)
                    or field
                )
                if mapped not in available:
                    lost.append(f"{fork}.{name}.{field} → {target.__name__}.{mapped}")
    assert not lost, (
        f"{len(lost)} record fields exist nowhere in the canonical union: {lost}. "
        "Either carry the field across or add a justified entry to _FIELD_RENAMES."
    )


def test_no_record_property_disappeared() -> None:
    """`BookTicker.price` is a property, not a field, and is just as easy to lose."""
    lost = [
        f"{fork}.{name}.{prop}"
        for fork, structs in _snapshot().items()
        for name, body in structs.items()
        if (target := _resolve_struct(fork, name, body["tag"])) is not None
        for prop in body["properties"]
        if prop not in _names_on(target)
    ]
    assert not lost, f"computed properties that no longer exist: {lost}"


def test_the_rename_tables_are_not_hoarding_entries() -> None:
    """A rename table is a place losses go to be forgotten unless it is kept honest.

    Every entry must still describe something the frozen snapshot actually contained, and
    must still point at something the canonical union actually has.
    """
    snap = _snapshot()
    stale: list[str] = []

    for (fork, name), target in _STRUCT_RENAMES.items():
        if name not in snap.get(fork, {}):
            stale.append(f"_STRUCT_RENAMES[{fork!r}, {name!r}]: no such legacy struct")
        elif target not in _canonical_structs():
            stale.append(f"_STRUCT_RENAMES[{fork!r}, {name!r}] → {target}: not a canonical struct")

    for (fork, name, field), (target_field, why) in _FIELD_RENAMES.items():
        entry = f"_FIELD_RENAMES[{fork!r}, {name!r}, {field!r}]"
        body = snap.get(fork, {}).get(name)
        if body is None:
            stale.append(f"{entry}: no such legacy struct")
        elif field not in body["fields"]:
            stale.append(f"{entry}: no such legacy field")
        else:
            canonical_struct = _resolve_struct(fork, name, body["tag"])
            if canonical_struct is not None and target_field not in _names_on(canonical_struct):
                stale.append(f"{entry} → {target_field}: not on {canonical_struct.__name__}")
        assert why.strip(), f"the rename of {fork}.{name}.{field} carries no justification"

    assert not stale, f"rename entries that no longer describe anything: {stale}"


def test_every_legacy_channel_tag_still_decodes() -> None:
    """A tag is the on-disk spelling; dropping one orphans data already written.

    `bar` and `option_quote` were absorbed into `ohlcv` and `options_chain`, but the
    members stay declared so an existing lake still parses. Rewriting those tags is a
    `migrate-lake` step, not a schema deletion.
    """
    from crocodile.core.schema.enums import Channel

    declared = {m.value for m in Channel}
    missing = sorted(
        {
            body["tag"]
            for structs in _snapshot().values()
            for body in structs.values()
            if body["tag"]
        }
        - declared
    )
    assert not missing, f"channel tags that were written to disk and no longer decode: {missing}"


def test_the_canonical_union_covers_every_resolved_struct() -> None:
    """Resolution is not enough: the target has to be *in* the union that gets decoded."""
    members = set(typing.get_args(canonical.Record))
    orphans = sorted(
        {
            target.__name__
            for fork, structs in _snapshot().items()
            for name, body in structs.items()
            if (target := _resolve_struct(fork, name, body["tag"])) is not None
            and target not in members
        }
    )
    assert not orphans, f"canonical structs outside the Record union: {orphans}"
