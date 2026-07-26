"""Keep the provenance registry free of test residue.

Registration mutates module-level state, so a test that registers a basis would
otherwise leak it into every later test in the process: the conformance gate
that walks ``registered_bases()`` would become collection-order dependent, and a
second in-process run would die with "already registered".
"""

from collections.abc import Iterator

import pytest

from crocodile.core.schema import provenance


@pytest.fixture(autouse=True)
def _isolate_provenance_registry() -> Iterator[None]:
    """Snapshot the registry before each test and restore it afterwards."""
    snapshot = dict(provenance._REGISTRY)
    try:
        yield
    finally:
        provenance._REGISTRY.clear()
        provenance._REGISTRY.update(snapshot)
