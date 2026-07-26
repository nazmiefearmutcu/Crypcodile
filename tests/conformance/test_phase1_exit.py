"""What has to be true before Phase 2 may begin.

Phase 1 pulled one core out of two forks. These are the assertions that say it
actually happened, rather than that it looks like it happened from a distance.
"""

from __future__ import annotations

import ast
import inspect
import json
import pathlib
import subprocess

_REPO = pathlib.Path(__file__).resolve().parents[2]
_SRC = _REPO / "src" / "crocodile"


def _package_files() -> list[pathlib.Path]:
    return sorted(p for p in _SRC.rglob("*.py") if "__pycache__" not in p.parts)


def test_the_subtree_import_directory_is_gone() -> None:
    assert not (_REPO / "src" / "_incoming_stockodile").exists(), (
        "Task 1 imported Stockodile into a transient directory; Phase 1 dissolves it"
    )


def test_the_package_tree_is_anchored_where_this_test_thinks_it_is() -> None:
    """Guard the guards.

    Every scan below walks ``_SRC``. If that path stopped resolving — a rename,
    a move, running from another directory — the scans would find nothing and
    pass in silence, which is exactly how two AST gates went green while
    checking an empty tree earlier in this merge.
    """
    assert _SRC.is_dir(), f"package tree not found at {_SRC}"
    assert len(_package_files()) > 100, "package tree is implausibly small; check the anchor"


def test_no_source_file_still_imports_the_old_packages() -> None:
    """Nothing under `crocodile` may still *depend* on `crypcodile` / `stockodile`.

    The check is on imports, not on the words. Both names legitimately survive
    as text: CLI help, PyPI install hints, and — deliberately — the on-disk
    state paths ``~/.crypcodile/`` and ``STOCKODILE_HOME``, which were left
    alone so the rename does not orphan a live deployment's cache and sync
    state. Renaming those is a migration, not a rename, and it is not Phase 1's.
    """
    offenders: list[str] = []
    for path in _package_files():
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                root = name.split(".", 1)[0]
                if root in {"crypcodile", "stockodile"}:
                    offenders.append(f"{path.relative_to(_REPO)}:{node.lineno} → {name}")

    assert not offenders, f"stale package imports: {offenders}"


def test_the_two_old_distributions_are_shims_and_nothing_else() -> None:
    """`src/crypcodile` and `src/stockodile` hold a deprecation notice, no code.

    A shim that still carried modules would keep the old import paths working by
    duplicating the tree, which is the opposite of a merge.
    """
    for legacy in ("crypcodile", "stockodile"):
        files = sorted(
            p.name for p in (_REPO / "src" / legacy).rglob("*.py") if "__pycache__" not in p.parts
        )
        assert files == ["__init__.py"], f"src/{legacy} still ships modules: {files}"


def test_the_deprecated_commands_say_they_are_deprecated() -> None:
    """A silent alias is not a deprecation.

    Both old commands still work, and both are removed in 0.4. Pointed straight
    at the merged CLI they would have run without a word, leaving the notice in
    a pyproject comment nobody reads.
    """
    for command, replacement in (("crypcodile", "crocodile"), ("stockodile", "crocodile-equity")):
        result = subprocess.run(
            [str(_REPO / ".venv" / "bin" / command), "--help"],
            cwd=_REPO,
            capture_output=True,
            text=True,
        )
        assert "deprecated" in result.stderr, f"{command} ran without a deprecation notice"
        assert "0.4" in result.stderr, f"{command} does not say when it goes away"
        assert replacement in result.stderr, f"{command} does not name its replacement"


def test_the_public_api_is_no_longer_empty() -> None:
    import crocodile

    assert getattr(crocodile, "__all__", None), (
        "both source repos shipped an empty __init__; the merge exists partly to fix that"
    )


def test_every_public_name_actually_resolves() -> None:
    """`__all__` that lies is worse than `__all__` that is empty."""
    import crocodile

    missing = [name for name in crocodile.__all__ if not hasattr(crocodile, name)]
    assert not missing, f"__all__ names that do not exist: {missing}"


def test_no_exception_class_escapes_the_root() -> None:
    """Carried forward from Task 3: the `__all__` walk has one hole.

    Task 3's root check iterates `errors.__all__`, so a class defined in the
    module and forgotten from `__all__` is invisible to it. Walk the module's
    own namespace instead — that hole is exactly how an exception ends up
    outside the hierarchy the root promises to cover.
    """
    from crocodile.core import errors
    from crocodile.core.errors import CrocodileError

    strays = [
        name
        for name, obj in vars(errors).items()
        if inspect.isclass(obj)
        and issubclass(obj, BaseException)
        and obj.__module__ == errors.__name__
        and not issubclass(obj, CrocodileError)
    ]
    assert not strays, f"exception classes outside CrocodileError: {strays}"


# ---------------------------------------------------------------------------
# The promise the merge actually made
# ---------------------------------------------------------------------------

# Names that were public before the merge and are deliberately gone. Each needs
# a reason, and the reason has to be better than "we did not need it".
_DELIBERATELY_DROPPED = {
    # A test stub that lived inside the equity fork's *duplicate* of the Base L2
    # connector. The duplicate was deleted as fork residue — an on-chain
    # connector inside an equities library — and the surviving crypto connector
    # has the real CoinbaseSmartWalletDetector. Dropping a stub that shadowed a
    # real implementation is the point of the deduplication, not a loss.
    "DummySmartWalletDetector",
}


def _merged_public_names() -> set[str]:
    names: set[str] = set()
    for path in _package_files():
        for node in ast.parse(path.read_text()).body:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                if not node.name.startswith("_"):
                    names.add(node.name)
    return names


def test_no_capability_disappeared_in_the_merge() -> None:
    """Every public name either fork had is still reachable, or is listed with a reason.

    This is the assertion the merge needed from the start and did not have.
    Seven capabilities were promoted away and lost — the read-only SQL guard,
    the Catalog lifecycle, the non-bucketed lake reader, six resamplers,
    `OrderBookSync`, the equity `from_row`, the equity `resample_ohlcv` — each
    on the unmeasured assumption that the crypto copy was the superset. None of
    the existing gates saw it, because they all ask whether an implementation
    exists rather than whether one stopped existing. Nothing raised; queries
    just came back empty.

    The inventory beside this file is the two forks' public surface at the last
    commit where both trees were intact. Deleting a name from it to make this
    pass is the one thing that defeats it.
    """
    inventory = json.loads((pathlib.Path(__file__).parent / "premerge_public_api.json").read_text())
    merged = _merged_public_names()

    lost = {
        fork: sorted(set(names) - merged - _DELIBERATELY_DROPPED)
        for fork, names in inventory.items()
        if not fork.startswith("_")
    }
    assert not any(lost.values()), (
        "public names that existed before the merge and exist nowhere now: "
        f"{ {k: v for k, v in lost.items() if v} }. Either restore them or add them to "
        "_DELIBERATELY_DROPPED with a reason."
    )


def test_the_dropped_list_is_not_quietly_hoarding_names() -> None:
    """A name that came back must leave the exemption list.

    Otherwise the list grows into a place where losses go to be forgotten.
    """
    merged = _merged_public_names()
    resurrected = sorted(_DELIBERATELY_DROPPED & merged)
    assert not resurrected, f"these exist again and no longer need an exemption: {resurrected}"


# ---------------------------------------------------------------------------
# Inherited debt: capped, not hidden
# ---------------------------------------------------------------------------

# Ruff findings in the two legacy surfaces on the day Phase 1 closed. They are
# inherited — both forks carried them under their old names, where nothing was
# ever pointed at them. The number exists so the debt can only shrink: lower it
# when you fix something, and never raise it. `core` and `contrib` are held to
# zero separately below, and that is the line new code is written on.
_LEGACY_RUFF_BUDGET = 478


def _ruff(*paths: str) -> list[str]:
    result = subprocess.run(
        [".venv/bin/python", "-m", "ruff", "check", "--output-format=concise", *paths],
        cwd=_REPO,
        capture_output=True,
        text=True,
    )
    return [line for line in result.stdout.splitlines() if ": " in line]


def test_the_merged_core_is_lint_clean() -> None:
    """Everything Phase 1 actually wrote holds to zero."""
    findings = _ruff("src/crocodile/core", "src/crocodile/contrib")
    assert not findings, f"{len(findings)} findings in core/contrib:\n" + "\n".join(findings[:20])


def test_the_legacy_surfaces_do_not_get_worse() -> None:
    """The inherited debt is a ratchet, not a licence."""
    findings = _ruff("src/crocodile/crypto", "src/crocodile/equity")
    assert len(findings) <= _LEGACY_RUFF_BUDGET, (
        f"legacy lint debt grew to {len(findings)}, budget is {_LEGACY_RUFF_BUDGET}. "
        "Phase 2 lowers this number; nothing raises it."
    )
