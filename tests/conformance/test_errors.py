import copy
import pickle

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


def _round_trip(err: CapabilityUnavailable) -> CapabilityUnavailable:
    """Pickle and unpickle ``err``, which is the behaviour under test.

    The payload is bytes this function produced one expression earlier from an object
    built in this file, so nothing untrusted is being deserialised — the round trip *is*
    the assertion. ``multiprocessing`` and the surfaces Phase 2 adds do exactly this to
    an exception crossing a process boundary, which is why it has to be exercised here
    rather than replaced with a text format.
    """
    # nosemgrep: python.lang.security.deserialization.pickle.avoid-pickle
    restored = pickle.loads(pickle.dumps(err))
    assert isinstance(restored, CapabilityUnavailable)
    return restored


def test_capability_unavailable_survives_pickle():
    """The one error in the tree designed to cross a process or wire boundary.

    ``BaseException.__reduce__`` returns ``(cls, self.args, ...)``, and ``args`` here is the
    single formatted message, so the default reconstruction calls the two-positional-argument
    constructor with one argument. The resulting ``TypeError`` is raised *during unpickling*,
    which masks the original error entirely and surfaces it far from its origin.
    """
    original = CapabilityUnavailable("ofi", "equity", reason="no L1 quote stream configured")
    restored = _round_trip(original)
    assert restored.capability == "ofi"
    assert restored.asset_class == "equity"
    assert restored.reason == "no L1 quote stream configured"
    assert str(restored) == str(original)


def test_capability_unavailable_survives_copy_and_deepcopy():
    """``copy`` and ``deepcopy`` go through the same ``__reduce__``, so they fail together."""
    original = CapabilityUnavailable("depth", "equity", reason="no free native L2 source")
    for clone in (copy.copy(original), copy.deepcopy(original)):
        assert clone.capability == "depth"
        assert clone.asset_class == "equity"
        assert clone.reason == "no free native L2 source"


def test_capability_unavailable_is_still_catchable_after_a_round_trip():
    """A reconstructed instance that escaped the hierarchy would defeat ``except``."""
    restored = _round_trip(CapabilityUnavailable("mev-sandwich", "equity", reason="no mempool"))
    with pytest.raises(CrocodileError):
        raise restored


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
