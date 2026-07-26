import pytest

from crocodile.core.schema.provenance import (
    Provenance,
    UnregisteredBasisError,
    confidence_for,
    level_for,
    register_basis,
    registered_bases,
)


def test_native_basis_is_certain():
    assert confidence_for("native", {}) == 1.0
    assert level_for("native") is Provenance.NATIVE


def test_unavailable_basis_is_zero():
    assert confidence_for("unavailable", {}) == 0.0
    assert level_for("unavailable") is Provenance.UNAVAILABLE


def test_vap_confidence_saturates_with_sample_size():
    few = confidence_for("yahoo_1m_vap", {"n_volume_bars": 10})
    many = confidence_for("yahoo_1m_vap", {"n_volume_bars": 3900})
    assert 0.0 < few < many < 1.0
    # 390 = minutes in one regular US trading session, the documented reference
    assert confidence_for("yahoo_1m_vap", {"n_volume_bars": 390}) == pytest.approx(0.5)


def test_unregistered_basis_is_an_error_not_a_default():
    with pytest.raises(UnregisteredBasisError):
        confidence_for("something_invented", {})


def test_duplicate_registration_is_rejected():
    with pytest.raises(ValueError):
        register_basis("native", level=Provenance.NATIVE)(lambda _: 1.0)


def test_formula_returning_out_of_range_is_rejected():
    register_basis("_test_bad_range", level=Provenance.DERIVED)(lambda _: 1.5)
    with pytest.raises(ValueError):
        confidence_for("_test_bad_range", {})


def test_registered_bases_is_a_frozenset():
    assert isinstance(registered_bases(), frozenset)
    assert "native" in registered_bases()
