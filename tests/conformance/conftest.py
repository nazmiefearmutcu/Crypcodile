"""Keep the provenance registry free of test residue.

Registration mutates module-level state, so a test that registers a basis would
otherwise leak it into every later test in the process: the conformance gate
that walks ``registered_bases()`` would become collection-order dependent, and a
second in-process run would die with "already registered".

Restoring a snapshot is only safe once every basis is already in it. A snapshot
taken before a submodule had been imported does not contain that submodule's
basis, so restoring it *evicts* one — and because ``sys.modules`` caches the
module, a later ``load_all_bases()`` never re-runs its ``@register_basis`` and the
basis never comes back. Loading every basis once per session, ahead of any
snapshot, is what makes the restore lossless.
"""

from collections.abc import Iterator

import pytest

from crocodile import capabilities
from crocodile.core.schema import provenance


@pytest.fixture(scope="session", autouse=True)
def _load_every_basis_once() -> None:
    """Import every basis before any snapshot is taken, so all snapshots are complete."""
    provenance.load_all_bases()


@pytest.fixture(scope="session", autouse=True)
def _declare_every_capability_once() -> None:
    """Import every batch module before any registry snapshot is taken.

    The capability registry has the same hazard the docstring above describes for bases,
    and for the same mechanical reason: ``test_capability.py`` and
    ``test_pending_symmetry.py`` both snapshot ``REGISTRY`` per test and restore it
    afterwards. A snapshot taken before ``crocodile.capabilities`` had been imported is
    missing every declaration in it, so restoring that snapshot *evicts* them — and
    ``sys.modules`` has the batch module cached, so a later ``load_all()`` never re-runs
    its ``declare()`` calls and the capabilities never come back. The symmetry gates that
    ran afterwards would then be measuring an empty registry, vacuously.
    """
    capabilities.load_all()


@pytest.fixture(autouse=True)
def _isolate_provenance_registry(_load_every_basis_once: None) -> Iterator[None]:
    """Snapshot the registry before each test and restore it afterwards."""
    snapshot = dict(provenance._REGISTRY)
    try:
        yield
    finally:
        provenance._REGISTRY.clear()
        provenance._REGISTRY.update(snapshot)
