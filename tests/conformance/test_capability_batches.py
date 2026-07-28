"""The batch package's own rules: nothing loads silently short, nothing loads silently.

``crocodile.capabilities`` is filled by four agents in parallel, and the two ways that goes
wrong are both quiet. A module nobody added to :data:`BATCHES` is never imported, so its
capabilities do not exist and no gate downstream can tell the difference between "not
declared" and "not loaded". A module that raises on import takes everything after it with
it, and :func:`~crocodile.core.schema.provenance.load_all_bases` — which walks this same
package — turns that into a ``RuntimeWarning`` and carries on.
"""

from __future__ import annotations

import importlib
import pathlib
import re
import sys
from collections.abc import Iterator

import pytest

from crocodile import capabilities
from crocodile.capabilities import BATCHES, load_all
from crocodile.core.capability import REGISTRY

_PACKAGE_DIR = pathlib.Path(capabilities.__file__).parent


def test_the_batch_scan_is_anchored_where_this_test_thinks_it_is() -> None:
    """Guard the guard: an empty directory listing would pass the gate below in silence."""
    assert _PACKAGE_DIR.is_dir(), f"batch package not found at {_PACKAGE_DIR}"
    assert BATCHES, "BATCHES is empty; load_all() would import nothing"


def test_every_module_in_the_package_is_on_the_load_list() -> None:
    """A batch module missing from ``BATCHES`` is a block of capabilities that never exist.

    The direction that matters is *disk → list*: adding a fifth file is the easy step and
    remembering to name it is the one that gets skipped, which produces a registry short by
    a whole family with nothing raised anywhere.
    """
    on_disk = {
        path.stem
        for path in _PACKAGE_DIR.glob("*.py")
        if not path.stem.startswith("_")
    }
    assert on_disk == set(BATCHES), (
        f"on disk but not loaded: {sorted(on_disk - set(BATCHES))}; "
        f"loaded but not on disk: {sorted(set(BATCHES) - on_disk)}"
    )


def test_every_batch_module_imports_and_the_load_is_idempotent() -> None:
    load_all()
    load_all()
    for name in BATCHES:
        assert f"crocodile.capabilities.{name}" in sys.modules


def test_the_declarations_left_the_machinery_module() -> None:
    """``core/capability.py`` holds the mechanism and no capability of its own.

    Asserted on the source text because the registry cannot tell where a name was declared
    — a declaration moved back into ``capability.py`` would look identical from ``REGISTRY``
    and would re-serialise the four porting agents behind one file.
    """
    source = pathlib.Path(
        importlib.import_module("crocodile.core.capability").__file__ or ""
    ).read_text()
    assert "declare(" in source, "the anchor is wrong; capability.py should define declare()"
    assert "\ndeclare(\n" not in source, "capability.py declares a capability at module level"
    assert "Capability(\n        name=" not in source, "capability.py holds a declaration"


@pytest.fixture
def _restore_batches() -> Iterator[None]:
    """``BATCHES`` is a module constant; the breakage below has to put it back."""
    original = capabilities.BATCHES
    try:
        yield
    finally:
        capabilities.BATCHES = original  # type: ignore[misc]
        sys.modules.pop("crocodile.capabilities.no_such_batch", None)


def test_a_batch_module_that_cannot_be_imported_stops_the_load(_restore_batches: None) -> None:
    """The rejecting branch, driven rather than described.

    ``load_all`` has no ``try`` on purpose, and the only way to show that is to give it
    something that fails. A swallowed import here would leave the registry silently missing
    every capability after the broken module — which is precisely what
    ``load_all_bases()`` does with the same package, and why this loader exists separately.
    """
    capabilities.BATCHES = ("analytics", "no_such_batch", "ops")  # type: ignore[misc]
    with pytest.raises(ModuleNotFoundError, match="no_such_batch"):
        load_all()


def test_loading_populates_the_registry() -> None:
    """The point of the whole package, asserted once."""
    load_all()
    assert {"indicators", "slippage"} <= set(REGISTRY)


# ---------------------------------------------------------------------------
# One vocabulary across four batches
# ---------------------------------------------------------------------------
#
# Four agents filled this package in parallel, which is what made it fast and what let the
# same concept acquire four spellings. The registry is the one place the drift is visible,
# because it is the only place all four batches' parameter schemas sit side by side — and
# it is also where the drift stops being cosmetic: `Capability.params` is projected to Typer
# options, FastAPI query parameters and an MCP inputSchema, so two names for one thing
# become two names on three surfaces each.


def _render(annotation: object) -> str:
    """Render a struct field's annotation as a comparable string, optionality removed.

    ``msgspec.structs.fields`` hands back a ``type`` for some fields and the source text for
    others, depending on whether the module deferred its annotations — so a naive ``str()``
    reads ``<class 'str'>`` and ``str`` as two different types and every parameter in the
    registry looks like a collision. Optionality is normalised away for a reason of its own:
    ``int | None`` and ``int`` are the same *concept* with different defaults, and a gate
    that read them as a collision would push four batches into agreeing about a default
    rather than about a meaning.
    """
    if isinstance(annotation, type):
        text = annotation.__name__
    else:
        text = re.sub(
            r"<class '([^']+)'>",
            lambda match: match.group(1).rsplit(".", 1)[-1],
            str(annotation),
        ).replace("typing.", "")
    parts = [part.strip() for part in text.split("|")]
    return " | ".join(part for part in parts if part not in {"None", "NoneType"})


def _fields() -> dict[str, list[tuple[str, str]]]:
    """``field name → [(capability, rendered type)]`` over every registered params struct."""
    import msgspec

    load_all()
    found: dict[str, list[tuple[str, str]]] = {}
    for name, cap in sorted(REGISTRY.items()):
        for field in msgspec.structs.fields(cap.params):
            found.setdefault(field.name, []).append((name, _render(field.type)))
    return found


_VENUE_SPELLINGS = ("exchange", "exchanges", "provider", "providers")
"""Names for "which venue" that this product settled against.

``core.store.migrate`` renamed the lake's top-level partition from ``exchange={venue}/``
and ``provider={name}/`` to ``source={name}/`` — one key, because one command now serves
both markets and neither fork's word covers the other's half. A parameter that decides
which partition is read should not disagree with the directory it produces.
"""


def test_which_venue_is_spelled_the_way_the_lake_spells_it() -> None:
    """The ops batch said ``source``; the catalog and market batches said ``exchange``.

    Not a style question. ``crocodile collect --source binance`` and ``crocodile universe
    --exchange binance`` name the same venue in one CLI, and the second word is the losing
    fork's — it cannot describe ``stooq`` or ``alpaca``, which is the whole reason the
    partition key was merged.
    """
    offenders = sorted(
        f"{capability}.{field}"
        for field, uses in _fields().items()
        if field in _VENUE_SPELLINGS
        for capability, _type in uses
    )
    assert not offenders, (
        f"{offenders} name a venue under a pre-merge spelling; the merged key is `source` "
        f"(or `sources` where the parameter takes several). Add a _PARAM_RENAMES entry so "
        f"the frozen wire name still resolves."
    )


def test_no_parameter_name_carries_two_meanings_across_the_batches() -> None:
    """``period`` was ``str = "5m"`` in ops and ``int = 14`` in analytics.

    Two batches, one word, two concepts — a venue's open-interest history bucket and a
    count of bars an indicator looks back over. REST coerces with ``strict=False``, so
    ``?period=14`` on ``backfill`` becomes the string ``"14"`` and is sent to Binance as a
    bucket width, which is not an error anywhere along the path.

    Types are the readable proxy for meaning here rather than the point: two parameters that
    genuinely mean one thing agree about what they hold.
    """
    collisions = {
        field: sorted(uses)
        for field, uses in _fields().items()
        if len({annotation for _capability, annotation in uses}) > 1
    }
    assert not collisions, (
        f"one parameter name, two types, so at least two meanings: {collisions}. Rename the "
        f"odd one so the two stop colliding — do not force one spelling onto two meanings."
    )


def test_a_venue_wide_subscription_is_not_bounded_by_something_called_limit() -> None:
    """``limit`` means rows in four batches and *subscription breadth* in ``collect-market``.

    ``collect-market --all-symbols`` on a 2 000-market venue silently collected 500 and
    nothing in the request or the result said so. A row cap truncates an answer; this one
    decides how much of the market is watched, which is a different question with the same
    word on it.
    """
    load_all()
    fields = REGISTRY["collect-market"].params.__struct_fields__
    assert "limit" not in fields, (
        "`collect-market.limit` caps how many symbols are *subscribed to*, not how many "
        "rows come back; every other `limit` in the registry is a row cap"
    )
    assert "max_symbols" in fields


def test_collect_market_and_universe_agree_that_an_unnamed_quote_means_every_quote() -> None:
    """One venue, two capabilities, two answers about a quote nobody asked to filter on.

    ``universe.quote`` defaults ``None`` and argues it is the only safe value; ``collect-
    market.quote`` defaulted ``"USDT"``. On Coinbase USD, Bitstamp EUR or Upbit KRW,
    ``collect-market --all-symbols`` raised "no symbols matched the requested market slice"
    while ``universe`` enumerated the same venue — a filter the caller never asked for,
    emptying a market that was there.
    """
    import msgspec

    load_all()
    defaults = {}
    for name in ("collect-market", "universe"):
        field = next(
            f for f in msgspec.structs.fields(REGISTRY[name].params) if f.name == "quote"
        )
        defaults[name] = field.default
    assert defaults["collect-market"] == defaults["universe"] is None, (
        f"quote defaults disagree across batches: {defaults}; an unnamed quote means every "
        f"quote, which is what the venue-agnostic half already argued"
    )
