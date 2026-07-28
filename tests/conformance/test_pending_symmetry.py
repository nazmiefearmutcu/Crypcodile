"""The rules that keep the two symmetry ledgers honest, and stop either becoming a hiding place.

Phase 2 registers 48 crypto capabilities whose equity halves arrive in Phase 3. Without
somewhere honest to record that, the port has only dishonest options: leave them out of the
registry (and the surfaces stop being projections of it), or park them on ``IRREDUCIBLE``
(and claim the market cannot have an equity analogue, which is false and permanent).

``PENDING_SYMMETRY`` is the honest option, and it is only honest while these hold.

``IRREDUCIBLE``'s rules live here too, beside its twin's, which is the change an exit review
forced. ``PENDING_SYMMETRY`` had a hoarding gate and ``IRREDUCIBLE`` had none — and the
hazard the hoarding gate exists for is *stronger* on the permanent list, not weaker. The
review gave ``peg-deviation`` an equity implementation at runtime and ran every gate that
mentions ``IRREDUCIBLE``: all of them passed, over a capability that had just stopped being
irreducible. A stale entry there means a regression to asymmetry is invisible forever,
because the name was already excused forever.
"""

from __future__ import annotations

from collections.abc import Iterator

import msgspec
import pytest

from crocodile.core import capability
from crocodile.core.capability import (
    IRREDUCIBLE,
    PENDING_SYMMETRY,
    REGISTRY,
    SPEC_METHODS,
    AssetClass,
    Capability,
    Impl,
    ReturnKind,
    register,
)
from crocodile.core.schema.provenance import Provenance


class _Params(msgspec.Struct, frozen=True):
    symbol: str


@pytest.fixture
def _isolate() -> Iterator[None]:
    """Registry and both ledgers are module state; a leak would order-couple gates."""
    registry, pending = dict(REGISTRY), dict(PENDING_SYMMETRY)
    irreducible = dict(IRREDUCIBLE)
    builtins = set(capability._DECLARED_NAMES)
    try:
        yield
    finally:
        REGISTRY.clear()
        REGISTRY.update(registry)
        PENDING_SYMMETRY.clear()
        PENDING_SYMMETRY.update(pending)
        IRREDUCIBLE.clear()
        IRREDUCIBLE.update(irreducible)
        capability._DECLARED_NAMES.clear()
        capability._DECLARED_NAMES.update(builtins)


def _crypto_only(name: str) -> Capability:
    return Capability(
        name=name,
        summary="Crypto today, equity in Phase 3.",
        params=_Params,
        returns=ReturnKind.TABLE,
        impls={AssetClass.CRYPTO: Impl(fn=len, prov=Provenance.NATIVE, basis="native")},
    )


def _symmetric(name: str) -> Capability:
    return Capability(
        name=name,
        summary="Both asset classes, today.",
        params=_Params,
        returns=ReturnKind.TABLE,
        impls={
            AssetClass.CRYPTO: Impl(fn=len, prov=Provenance.NATIVE, basis="native"),
            AssetClass.EQUITY: Impl(fn=len, prov=Provenance.NATIVE, basis="native"),
        },
    )


# ---------------------------------------------------------------------------
# The ledger's own rules
# ---------------------------------------------------------------------------


def test_every_entry_names_a_method_that_was_actually_specified() -> None:
    """A deadline pointing at a method nobody wrote down is a deadline nobody owns."""
    unknown = {
        name: method for name, method in PENDING_SYMMETRY.items() if method not in SPEC_METHODS
    }
    assert not unknown, (
        f"scheduled against methods that do not exist in design §9.1: {unknown}. "
        f"Known methods: {sorted(SPEC_METHODS)}"
    )


def test_no_capability_is_both_scheduled_and_irreducible() -> None:
    """They are opposite claims: "coming in Phase 3" and "can never exist"."""
    both = sorted(set(PENDING_SYMMETRY) & set(IRREDUCIBLE))
    assert not both, f"{both} claim both that an equity analogue is coming and that it cannot"


def test_the_ledger_only_holds_capabilities_that_exist() -> None:
    """An entry for an unregistered name is a deadline against nothing."""
    ghosts = sorted(set(PENDING_SYMMETRY) - set(REGISTRY))
    assert not ghosts, f"scheduled but never registered: {ghosts}"


def test_the_ledger_is_not_hoarding_capabilities_that_became_symmetric() -> None:
    """The same rule Phase 1 put on its dropped-names list.

    A name whose equity half has landed must leave, or the ledger drifts into a place
    where completed work goes to look unfinished — and, worse, where a *regression* to
    asymmetry would be invisible because the name was already excused.
    """
    settled = sorted(
        name
        for name in PENDING_SYMMETRY
        if (cap := REGISTRY.get(name)) is not None
        and set(cap.impls) == {AssetClass.CRYPTO, AssetClass.EQUITY}
    )
    assert not settled, f"these are symmetric now and no longer need scheduling: {settled}"


# ---------------------------------------------------------------------------
# The same two rules on IRREDUCIBLE, which had neither
# ---------------------------------------------------------------------------


def test_the_irreducible_list_only_holds_capabilities_that_exist() -> None:
    """An exemption for an unregistered name excuses nothing and hides a deletion.

    The mirror of :func:`test_the_ledger_only_holds_capabilities_that_exist`, and it is the
    same hazard the ``_INFRASTRUCTURE`` pin closes one file over: a ledger whose entries are
    only ever *checked against what is registered* reads a deleted capability as a satisfied
    rule. ``gas-tracker`` is the precedent — it sat here naming a capability nobody had
    declared, and being unregistered is exactly what let it sit.
    """
    ghosts = sorted(
        name
        for name in IRREDUCIBLE
        if name not in REGISTRY and not any(name in c.aliases for c in REGISTRY.values())
    )
    assert not ghosts, (
        f"excused as irreducible but not registered: {ghosts}. IRREDUCIBLE says an equity "
        f"analogue cannot exist for a capability this product serves; with nothing "
        f"registered under the name it says nothing at all, and it would go on saying "
        f"nothing after the crypto half was deleted too."
    )


def test_the_irreducible_list_is_not_hoarding_capabilities_that_became_symmetric() -> None:
    """The hoarding rule its twin has had all along, and needs more because it is permanent.

    ``PENDING_SYMMETRY`` entries are meant to leave; the review that added this found that
    ``IRREDUCIBLE`` entries had no way to. Giving ``peg-deviation`` an equity implementation
    at runtime left ``test_gate2_every_capability_is_symmetric``,
    ``test_gate2_irreducible_entries_carry_a_justification``,
    ``test_no_capability_is_both_scheduled_and_irreducible`` and the ledger's own hoarding
    test all green — because the first three ``continue`` past an excused name and the
    fourth reads the other ledger.

    A capability with both halves is a live disproof of the claim its entry makes. Leaving
    the entry means the next commit that *removes* the equity half is invisible, which is
    the regression the twin's docstring names — with the difference that this list never
    expires, so the invisibility does not either.
    """
    disproved = sorted(
        name
        for name in IRREDUCIBLE
        if (cap := REGISTRY.get(name)) is not None
        and set(cap.impls) == {AssetClass.CRYPTO, AssetClass.EQUITY}
    )
    assert not disproved, (
        f"{disproved} are implemented for both asset classes while IRREDUCIBLE claims no "
        f"equity analogue can exist. The implementation is the answer; delete the entry."
    )


def test_the_spec_methods_are_the_set_that_was_argued_for() -> None:
    """Pinning the set stops a new letter being invented to buy more time.

    Renamed from ``..._the_seven_the_design_committed_to``: it is eight. M8 was added
    when porting the surfaces found the design's unstated assumption — that a gap always
    runs equity-ward — and its counterexample, ``depth``, whose missing half is the crypto
    one. The count moving is exactly what this test exists to make someone justify, so
    the number lives here rather than in the name, where it read as a fact rather than
    as a thing being asserted.
    """
    assert sorted(SPEC_METHODS) == ["M1", "M2", "M3", "M4", "M5", "M6", "M7", "M8"]
    for method, what in SPEC_METHODS.items():
        assert what.strip(), f"{method} carries no description"


# ---------------------------------------------------------------------------
# The mechanism, exercised — the ledger is empty today, so the branch it controls
# would otherwise never run under test. A gate whose only path is the one nothing
# takes is the vacuous-green shape this merge keeps finding.
# ---------------------------------------------------------------------------


def test_an_unscheduled_asymmetric_capability_fails_gate_2(_isolate: None) -> None:
    from tests.conformance.test_gates import test_gate2_every_capability_is_symmetric

    register(_crypto_only("fixture-unscheduled"))
    with pytest.raises(AssertionError, match="fixture-unscheduled"):
        test_gate2_every_capability_is_symmetric()


def test_a_scheduled_asymmetric_capability_passes_gate_2(_isolate: None) -> None:
    from tests.conformance.test_gates import test_gate2_every_capability_is_symmetric

    register(_crypto_only("fixture-scheduled"))
    PENDING_SYMMETRY["fixture-scheduled"] = "M1"
    test_gate2_every_capability_is_symmetric()


def test_scheduling_against_an_invented_method_is_caught(_isolate: None) -> None:
    PENDING_SYMMETRY["fixture-scheduled"] = "M9"
    with pytest.raises(AssertionError, match="do not exist"):
        test_every_entry_names_a_method_that_was_actually_specified()


def test_a_name_on_both_lists_is_caught(_isolate: None) -> None:
    """The fixture is any live IRREDUCIBLE name; it used to be `gas-tracker`.

    That entry was removed — it opens a Qt window, so it is a launcher rather than a
    capability, and an exemption list that covers one makes the list mean two things.
    `mev-sandwich` stands in because it is what IRREDUCIBLE is actually for: it needs a
    public mempool, which is a property of the market and not of the schedule.
    """
    PENDING_SYMMETRY["mev-sandwich"] = "M1"
    with pytest.raises(AssertionError, match="mev-sandwich"):
        test_no_capability_is_both_scheduled_and_irreducible()


def test_a_settled_entry_left_behind_is_caught(_isolate: None) -> None:
    PENDING_SYMMETRY["indicators"] = "M1"
    with pytest.raises(AssertionError, match="indicators"):
        test_the_ledger_is_not_hoarding_capabilities_that_became_symmetric()


def test_an_irreducible_capability_that_grew_an_equity_half_is_caught(_isolate: None) -> None:
    """The reviewer's experiment, run as a test rather than by hand.

    ``peg-deviation`` is the name they used and it is used here for the same reason: it is a
    live ``IRREDUCIBLE`` entry with a real crypto implementation, so nothing about the setup
    is contrived except the equity half.
    """
    REGISTRY["peg-deviation"] = _symmetric("peg-deviation")
    with pytest.raises(AssertionError, match="peg-deviation"):
        test_the_irreducible_list_is_not_hoarding_capabilities_that_became_symmetric()


def test_an_irreducible_entry_naming_nothing_registered_is_caught(_isolate: None) -> None:
    """What ``gas-tracker`` looked like while it sat here, and what a deletion looks like."""
    IRREDUCIBLE["fixture-deleted"] = "A justification for a capability nobody declared."
    with pytest.raises(AssertionError, match="fixture-deleted"):
        test_the_irreducible_list_only_holds_capabilities_that_exist()


def test_the_four_gates_the_review_ran_no_longer_all_pass(_isolate: None) -> None:
    """The experiment's actual shape: four gates run together, all four green.

    Kept as one test because the finding was about the *set* — each of the four had a
    defensible reason to pass, and the conclusion was only visible when they were run
    together. This asserts the set is no longer unanimous.
    """
    from tests.conformance.test_gates import (
        test_gate2_every_capability_is_symmetric,
        test_gate2_irreducible_entries_carry_a_justification,
    )

    REGISTRY["peg-deviation"] = _symmetric("peg-deviation")

    test_gate2_every_capability_is_symmetric()
    test_gate2_irreducible_entries_carry_a_justification()
    test_no_capability_is_both_scheduled_and_irreducible()
    test_the_ledger_is_not_hoarding_capabilities_that_became_symmetric()

    with pytest.raises(AssertionError, match="peg-deviation"):
        test_the_irreducible_list_is_not_hoarding_capabilities_that_became_symmetric()


# ---------------------------------------------------------------------------
# Phase 3's exit criterion, asserted here so it cannot be forgotten there.
# ---------------------------------------------------------------------------

_LEDGER_AS_SHIPPED: dict[str, str] = {
    "census": "M3",
    "collect-market": "M3",
    "markets": "M3",
    "universe": "M3",
}
"""What the ledger holds right now, pinned so its size is a watched number.

Phase 2 closed with 21 entries and Phase 3 empties it one method at a time. Each name
leaves in the same commit that gives its capability the implementation it was missing,
which is the only way an entry is allowed to leave: delete it from
``capabilities.analytics`` alone and this pin names a capability that is no longer
scheduled; delete it from here alone and a schedule survives that nothing records. Both
deletions in one commit is the only state
:func:`test_the_ledger_holds_exactly_the_entries_it_was_pinned_to_hold` accepts, and it
fails in *both* directions, so a half-done deletion is as red as an undeclared addition.

``PENDING_SYMMETRY``'s own docstring says "the count only ever moving in those two
directions is the property worth watching", and until an exit review looked, nothing was
watching. Two things made that hard to see. The declaration in ``core/capability.py`` reads
``PENDING_SYMMETRY = {}``, so the module a reviewer opens shows an empty dict; every live
entry is assembled at import time by three batch modules calling ``update()`` on it. And
:func:`test_phase_3_exit_the_ledger_must_be_empty` skipped whenever the ledger was
non-empty, so it was green at 21 and would have been green at 48.

No count is written into this docstring, on purpose. Several agents empty this dict in
parallel and a sentence naming a number is a merge conflict that resolves to a lie — this
paragraph said "It is 16" and then "It is 10" across two merges, each true for exactly one
commit. The number that matters is the length of the literal above, which is what the test
reads.

Pinned here rather than beside the declaration for the reason the batches write it here:
``core/capability.py`` is shared and this is a test's assertion about a fact, not a fact.
"""


def test_the_ledger_holds_exactly_the_entries_it_was_pinned_to_hold() -> None:
    """A schedule that can grow silently is an exemption list with better manners.

    Both directions are failures worth a diff. A new entry is a capability whose equity half
    somebody decided not to write, which is a decision and not a detail; a departed entry is
    Phase 3 delivering, which is the outcome the ledger exists to reach. Neither should
    happen without this list changing in the same commit.
    """
    unpinned = sorted(set(PENDING_SYMMETRY) - set(_LEDGER_AS_SHIPPED))
    added = {name: PENDING_SYMMETRY[name] for name in unpinned}
    assert not added, (
        f"scheduled since Phase 2 closed and unrecorded here: {added}. Deferring an equity "
        f"half is allowed and deferring it quietly is not — record it, with the method."
    )

    settled = sorted(set(_LEDGER_AS_SHIPPED) - set(PENDING_SYMMETRY))
    assert not settled, (
        f"{settled} left PENDING_SYMMETRY; if their equity halves landed, delete them here "
        f"too so the remaining count keeps meaning what it says."
    )

    remapped = {
        name: (was, PENDING_SYMMETRY[name])
        for name, was in _LEDGER_AS_SHIPPED.items()
        if name in PENDING_SYMMETRY and PENDING_SYMMETRY[name] != was
    }
    assert not remapped, (
        f"rescheduled against a different method: {remapped}. A method is the plan that "
        f"closes the gap; swapping it is a re-plan, not a correction."
    )


def test_phase_3_exit_the_ledger_must_be_empty() -> None:
    """Skipped only while the ledger is exactly what Phase 2 pinned, and it says so.

    Written now rather than then: the reason a schedule works is that the assertion which
    retires it already exists. But the version that shipped skipped on ``if
    PENDING_SYMMETRY``, which made it unfailable — green at 21 entries and equally green at
    48, so the one test named for Phase 3's exit criterion could not report the ledger
    moving away from it. The skip is now conditioned on the pin, so the only state that
    silences this test is the state a reviewer already agreed to.
    """
    if PENDING_SYMMETRY == _LEDGER_AS_SHIPPED:
        pytest.skip(
            f"Phase 2/3 in progress: {len(PENDING_SYMMETRY)} capabilities still scheduled, "
            f"exactly the set pinned in _LEDGER_AS_SHIPPED "
            f"({', '.join(f'{k}→{v}' for k, v in sorted(PENDING_SYMMETRY.items()))})"
        )
    assert not PENDING_SYMMETRY, (
        f"the ledger no longer matches the pin and is not empty either: "
        f"{dict(sorted(PENDING_SYMMETRY.items()))}. See "
        f"test_the_ledger_holds_exactly_the_entries_it_was_pinned_to_hold for what moved."
    )
