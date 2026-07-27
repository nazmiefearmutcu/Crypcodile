from itertools import pairwise

import pytest

from crocodile.core.schema.provenance import (
    ConfidenceFormulaError,
    ConfidenceInputError,
    Provenance,
    ProvenanceFields,
    UnregisteredBasisError,
    confidence_for,
    describe,
    level_for,
    load_all_bases,
    provenance_fields,
    register_basis,
    registered_bases,
)

_EXPECTED_BASES = frozenset({"book_resample", "native", "unavailable", "yahoo_1m_vap"})
_SESSION_BARS = 390


def test_native_basis_is_certain():
    assert confidence_for("native", {}) == 1.0
    assert level_for("native") is Provenance.NATIVE


def test_unavailable_basis_is_zero():
    assert confidence_for("unavailable", {}) == 0.0
    assert level_for("unavailable") is Provenance.UNAVAILABLE


def test_vap_confidence_is_session_coverage():
    counts = [0, 10, 97, 195, 389, _SESSION_BARS, 780, 3900]
    values = [confidence_for("yahoo_1m_vap", {"n_volume_bars": n}) for n in counts]

    assert values[0] == 0.0
    # Monotone non-decreasing: a two-point check would pass a curve that rises then falls.
    assert all(a <= b for a, b in pairwise(values))
    # Coverage has a reading: half a session's bars is half a session covered.
    assert confidence_for("yahoo_1m_vap", {"n_volume_bars": 195}) == pytest.approx(0.5)
    # A full session saturates, and stays saturated beyond it.
    assert confidence_for("yahoo_1m_vap", {"n_volume_bars": _SESSION_BARS}) == 1.0
    assert confidence_for("yahoo_1m_vap", {"n_volume_bars": 3900}) == 1.0


def test_unregistered_basis_is_an_error_not_a_default():
    with pytest.raises(UnregisteredBasisError):
        confidence_for("something_invented", {})


def test_level_for_unregistered_basis_is_an_error():
    with pytest.raises(UnregisteredBasisError):
        level_for("something_invented")


def test_duplicate_registration_is_rejected():
    def formula(_):
        """A duplicate registration of an already-registered basis."""
        return 1.0

    with pytest.raises(ValueError):
        register_basis("native", level=Provenance.NATIVE, inputs=[])(formula)


def test_missing_docstring_is_rejected():
    with pytest.raises(ValueError, match="docstring"):
        register_basis("_test_no_doc", level=Provenance.DERIVED, inputs=[])(lambda _: 0.5)


def test_the_package_imports_under_docstring_stripping():
    """-OO strips docstrings; the mandatory-docstring rule must not become a crash.

    This is a subprocess test on purpose: the running interpreter's optimize flag
    cannot be changed, and monkeypatching sys.flags would test the mock, not the
    behaviour that breaks a container build.
    """
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-OO", "-c", "import crocodile.core.schema.provenance"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_every_registered_basis_is_documented():
    load_all_bases()
    for basis in registered_bases():
        assert describe(basis).strip(), f"basis {basis!r} carries no docstring"


@pytest.mark.parametrize("bad", [1.5, -0.5])
def test_formula_returning_out_of_range_is_rejected(bad):
    def formula(_):
        """A deliberately broken formula, for the range guard."""
        return bad

    register_basis("_test_bad_range", level=Provenance.DERIVED, inputs=[])(formula)
    with pytest.raises(ConfidenceFormulaError):
        confidence_for("_test_bad_range", {})


def test_formula_raising_unexpectedly_is_a_formula_error():
    def formula(_):
        """A formula that blows up, for the wrapping guard."""
        raise ZeroDivisionError("boom")

    register_basis("_test_raises", level=Provenance.DERIVED, inputs=[])(formula)
    with pytest.raises(ConfidenceFormulaError) as excinfo:
        confidence_for("_test_raises", {})
    assert "_test_raises" in str(excinfo.value)


def test_missing_input_is_a_caller_error_not_a_formula_error():
    with pytest.raises(ConfidenceInputError):
        confidence_for("yahoo_1m_vap", {})


def test_bare_keyerror_from_a_formula_becomes_an_input_error():
    def formula(inputs):
        """Indexes an input directly, without the strict helper."""
        return float(inputs["absent"])

    register_basis("_test_bare_key", level=Provenance.DERIVED, inputs=[])(formula)
    with pytest.raises(ConfidenceInputError):
        confidence_for("_test_bare_key", {})


@pytest.mark.parametrize("bad", [3.9, "390", True, None])
def test_vap_rejects_a_non_int_count(bad):
    with pytest.raises(ConfidenceInputError):
        confidence_for("yahoo_1m_vap", {"n_volume_bars": bad})


def test_vap_rejects_a_negative_count():
    with pytest.raises(ConfidenceInputError):
        confidence_for("yahoo_1m_vap", {"n_volume_bars": -1})


def test_registered_bases_is_a_frozenset():
    bases = registered_bases()
    assert isinstance(bases, frozenset)
    assert _EXPECTED_BASES <= bases

    def formula(_):
        """A basis registered after the snapshot was taken."""
        return 1.0

    register_basis("_test_later", level=Provenance.DERIVED, inputs=[])(formula)
    assert "_test_later" not in bases, "an earlier result must not track later registrations"
    assert "_test_later" in registered_bases()


def test_provenance_fields_is_the_blessed_tail():
    tail = provenance_fields("yahoo_1m_vap", {"n_volume_bars": 195})
    assert isinstance(tail, ProvenanceFields)
    assert tail.prov is Provenance.SYNTHETIC
    assert tail.prov_basis == "yahoo_1m_vap"
    assert tail.prov_confidence == pytest.approx(0.5)
    assert tail.prov_inputs == ["bar"]

    tail.prov_inputs.append("mutated")
    assert provenance_fields("yahoo_1m_vap", {"n_volume_bars": 195}).prov_inputs == ["bar"]


def test_provenance_fields_defaults_inputs_for_input_free_bases():
    tail = provenance_fields("native")
    assert tail.prov is Provenance.NATIVE
    assert tail.prov_confidence == 1.0
    assert tail.prov_inputs == []


def test_provenance_fields_rejects_an_unregistered_basis():
    with pytest.raises(UnregisteredBasisError):
        provenance_fields("something_invented")


def test_describe_returns_the_formula_docstring():
    assert "coverage" in describe("yahoo_1m_vap")
    with pytest.raises(UnregisteredBasisError):
        describe("something_invented")


def test_explicit_doc_is_preferred_over_the_docstring():
    def formula(_):
        """The docstring, which an explicit doc= must win over."""
        return 1.0

    register_basis(
        "_test_explicit_doc",
        level=Provenance.DERIVED,
        inputs=[],
        doc="Survives -OO, which strips docstrings.",
    )(formula)
    assert describe("_test_explicit_doc") == "Survives -OO, which strips docstrings."


def test_load_all_bases_is_idempotent_and_survives_bad_imports():
    load_all_bases()
    assert _EXPECTED_BASES <= registered_bases()
    load_all_bases()
    assert _EXPECTED_BASES <= registered_bases()


# ---------------------------------------------------------------------------
# book_resample — the basis a reconstructed BookSnapshot rests on
# ---------------------------------------------------------------------------


def test_book_resample_scores_a_capture_that_earns_its_timestamp():
    """No lookahead means the capture describes the instant it is stamped with."""
    assert confidence_for("book_resample", {"lookahead_ns": 0, "interval_ns": 1_000}) == 1.0
    assert level_for("book_resample") is Provenance.DERIVED


def test_book_resample_falls_off_linearly_and_saturates_at_zero():
    assert confidence_for(
        "book_resample", {"lookahead_ns": 250, "interval_ns": 1_000}
    ) == pytest.approx(0.75)
    assert confidence_for("book_resample", {"lookahead_ns": 1_000, "interval_ns": 1_000}) == 0.0
    # A run of boundaries dragged along by one late record: still zero, never negative,
    # so the formula cannot hand the registry a value it has to reject.
    assert confidence_for("book_resample", {"lookahead_ns": 90_000, "interval_ns": 1_000}) == 0.0


def test_book_resample_refuses_inputs_that_could_not_have_been_measured():
    with pytest.raises(ConfidenceInputError, match="lookahead_ns"):
        confidence_for("book_resample", {"lookahead_ns": -1, "interval_ns": 1_000})
    with pytest.raises(ConfidenceInputError, match="interval_ns"):
        confidence_for("book_resample", {"lookahead_ns": 0, "interval_ns": 0})
    with pytest.raises(ConfidenceInputError, match="interval_ns"):
        confidence_for("book_resample", {"lookahead_ns": 0})
