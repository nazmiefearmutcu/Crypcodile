import pytest

from crocodile.core import errors
from crocodile.core.errors import (
    BookGap,
    CapabilityUnavailable,
    ConnectorError,
    CrocodileError,
    FatalConnectorError,
    ProvenanceError,
    StoreError,
    TransientConnectorError,
)


# Walking __all__ rather than a hand-written list: a whitelist silently stops covering
# any class added after it was written, which is exactly the drift this test guards.
@pytest.mark.parametrize("name", [n for n in errors.__all__ if n != "CrocodileError"])
def test_every_exported_error_descends_from_the_root(name: str) -> None:
    assert issubclass(getattr(errors, name), CrocodileError)


def test_connector_errors_split_fatal_from_transient():
    assert issubclass(FatalConnectorError, ConnectorError)
    assert issubclass(TransientConnectorError, ConnectorError)
    assert not issubclass(FatalConnectorError, TransientConnectorError)


def test_book_gap_is_a_store_error():
    assert issubclass(BookGap, StoreError)


def test_capability_unavailable_names_the_capability_and_asset_class():
    err = CapabilityUnavailable("ofi", "equity", reason="no L1 quote stream configured")
    assert err.capability == "ofi"
    assert err.asset_class == "equity"
    assert "ofi" in str(err)
    assert "no L1 quote stream configured" in str(err)


def test_provenance_errors_are_inside_the_hierarchy():
    from crocodile.core.schema.provenance import (
        ConfidenceFormulaError,
        ConfidenceInputError,
        UnregisteredBasisError,
    )

    for cls in (UnregisteredBasisError, ConfidenceInputError, ConfidenceFormulaError):
        assert issubclass(cls, CrocodileError), f"{cls.__name__} escapes the root"
        # Asserting the root alone would stay green if ProvenanceError were deleted and
        # these were re-parented straight onto it, losing the grouping this commit adds.
        assert issubclass(cls, ProvenanceError), f"{cls.__name__} escapes the provenance group"

    # the legacy bases callers already catch must survive the re-parenting
    assert issubclass(UnregisteredBasisError, LookupError)
    assert issubclass(ConfidenceInputError, ValueError)
    assert issubclass(ConfidenceFormulaError, RuntimeError)
