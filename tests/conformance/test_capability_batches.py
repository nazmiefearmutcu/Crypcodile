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
