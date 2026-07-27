"""The rules that keep `PENDING_SYMMETRY` a schedule instead of a second exemption list.

Phase 2 registers 48 crypto capabilities whose equity halves arrive in Phase 3. Without
somewhere honest to record that, the port has only dishonest options: leave them out of the
registry (and the surfaces stop being projections of it), or park them on ``IRREDUCIBLE``
(and claim the market cannot have an equity analogue, which is false and permanent).

``PENDING_SYMMETRY`` is the honest option, and it is only honest while these hold.
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
    """Both the registry and the ledger are module state; a leak would order-couple gates."""
    registry, pending = dict(REGISTRY), dict(PENDING_SYMMETRY)
    builtins = set(capability._DECLARED_NAMES)
    try:
        yield
    finally:
        REGISTRY.clear()
        REGISTRY.update(registry)
        PENDING_SYMMETRY.clear()
        PENDING_SYMMETRY.update(pending)
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


def test_the_spec_methods_are_the_seven_the_design_committed_to() -> None:
    """Pinning the set stops a new letter being invented to buy more time."""
    assert sorted(SPEC_METHODS) == ["M1", "M2", "M3", "M4", "M5", "M6", "M7"]
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
    PENDING_SYMMETRY["gas-tracker"] = "M1"
    with pytest.raises(AssertionError, match="gas-tracker"):
        test_no_capability_is_both_scheduled_and_irreducible()


def test_a_settled_entry_left_behind_is_caught(_isolate: None) -> None:
    PENDING_SYMMETRY["indicators"] = "M1"
    with pytest.raises(AssertionError, match="indicators"):
        test_the_ledger_is_not_hoarding_capabilities_that_became_symmetric()


# ---------------------------------------------------------------------------
# Phase 3's exit criterion, asserted here so it cannot be forgotten there.
# ---------------------------------------------------------------------------


def test_phase_3_exit_the_ledger_must_be_empty() -> None:
    """Skipped until Phase 3 closes, and it says so out loud.

    Written now rather than then: the reason a schedule works is that the assertion
    which retires it already exists.
    """
    if PENDING_SYMMETRY:
        pytest.skip(
            f"Phase 2/3 in progress: {len(PENDING_SYMMETRY)} capabilities still scheduled "
            f"({', '.join(f'{k}→{v}' for k, v in sorted(PENDING_SYMMETRY.items()))})"
        )
    assert not PENDING_SYMMETRY
