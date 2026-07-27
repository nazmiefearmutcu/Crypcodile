"""One module decides where the lake is, and it is ``core.config``.

``crypto/legacy/api_server.py`` carried two data-directory resolvers. ``_get_lake_client``
read ``CRYPCODILE_DATA_DIR`` defaulting to ``data``; ``_get_api_catalog`` read a bare
``DATA_DIR`` and otherwise probed ``test_data``, then ``data``, then
``~/Crypcodile/test_data``, taking the first that held a registered channel.
``POST /api/v1/simulate-price-impact`` used the second and every other lake route used the
first — so two routes over the *same capability* could answer from two different lakes,
and with no environment set at all they read ``./data`` and ``./test_data``. Nothing
raised. Both are perfectly good lakes; they are just not the same one.

``Settings`` existed the whole time and was imported by nothing but ``crocodile/__init__``.
The test below is the part that keeps it wired: a route-level assertion catches the two
resolvers disagreeing, but only a scan catches a *third* one being added next to them,
which is how the second one arrived.
"""

from __future__ import annotations

import ast
import pathlib

_REPO = pathlib.Path(__file__).resolve().parents[2]
_SRC = _REPO / "src" / "crocodile"

_LAKE_DIR_VARS = frozenset(
    {"DATA_DIR", "CROCODILE_DATA_DIR", "CRYPCODILE_DATA_DIR", "STOCKODILE_DATA_DIR"}
)
"""Every spelling that has ever meant "the lake root".

Deliberately not a suffix match: ``FLOWMAP_DATA_DIR`` in the GUI names where that window
keeps its own files, which is a different setting that happens to end in the same words.
"""

_MAY_READ_THE_ENVIRONMENT = {
    # `Settings` is the one place a name maps to a meaning. Its `_VARS` table holds the
    # unprefixed suffix and `_lookup` composes the prefixes, so both spellings appear here
    # as data rather than as a read.
    pathlib.Path("core/config.py"),
}


def _package_files() -> list[pathlib.Path]:
    return sorted(p for p in _SRC.rglob("*.py") if "__pycache__" not in p.parts)


def _env_reads(tree: ast.AST) -> list[tuple[int, str]]:
    """Return ``(lineno, name)`` for every environment read of a lake-directory variable.

    Covers the three spellings that occur in this tree — ``os.getenv(...)``,
    ``os.environ.get(...)`` and ``os.environ[...]`` — because a scanner that only knew one
    of them would pass while the resolver it was written to catch sat in the next line.
    """
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        name: str | None = None
        if isinstance(node, ast.Call) and node.args:
            func = node.func
            is_getenv = isinstance(func, ast.Attribute) and func.attr in ("getenv", "get")
            if is_getenv and isinstance(node.args[0], ast.Constant):
                value = node.args[0].value
                if isinstance(value, str):
                    name = value
        elif isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
            value = node.slice.value
            if isinstance(value, str):
                name = value
        if name in _LAKE_DIR_VARS:
            assert name is not None
            found.append((node.lineno, name))
    return found


def test_no_module_outside_config_resolves_the_lake_from_the_environment() -> None:
    """A second reader of the same variable is a second answer waiting to happen.

    The failure this prevents is not a crash. It is two callers of one capability quietly
    reporting on different data, which no amount of testing either caller in isolation
    would surface.
    """
    offenders: list[str] = []
    for path in _package_files():
        relative = path.relative_to(_SRC)
        if relative in _MAY_READ_THE_ENVIRONMENT:
            continue
        for lineno, name in _env_reads(ast.parse(path.read_text())):
            offenders.append(f"{relative}:{lineno} reads {name}")

    assert not offenders, (
        "these resolve the lake root themselves instead of going through "
        "crocodile.core.config.Settings:\n  " + "\n  ".join(offenders)
    )


def test_the_scanner_sees_every_spelling_of_an_environment_read() -> None:
    """Guard the guard.

    The gate above is an AST scan, and a scan that silently matches nothing passes. Each
    form below is one the tree actually used at some point.
    """
    for source in (
        'os.getenv("DATA_DIR")',
        'os.getenv("CRYPCODILE_DATA_DIR", "data")',
        'os.environ.get("STOCKODILE_DATA_DIR")',
        'os.environ["CROCODILE_DATA_DIR"]',
    ):
        assert _env_reads(ast.parse(source)), f"scanner missed {source}"


def test_the_scanner_leaves_an_unrelated_directory_variable_alone() -> None:
    """``FLOWMAP_DATA_DIR`` is where a GUI window keeps its files, not the lake."""
    assert not _env_reads(ast.parse('os.environ.get("FLOWMAP_DATA_DIR")'))
    assert not _env_reads(ast.parse('os.getenv("STOCKODILE_HOME")'))


def test_settings_is_actually_reachable_from_the_servers() -> None:
    """The gate above passes trivially if nothing resolves a lake at all.

    Both REST servers must expose a resolver that accepts a ``Settings`` — which is the
    shape ``CapabilityContext`` needs — so that "one resolver" means one that something
    can hand a configuration to, rather than one that reads the ambient environment.
    """
    import inspect

    from crocodile.crypto.legacy.api_server import _lake_dir as crypto_lake_dir
    from crocodile.equity.legacy.api_server import _lake_dir as equity_lake_dir

    for resolver in (crypto_lake_dir, equity_lake_dir):
        params = inspect.signature(resolver).parameters
        assert "settings" in params, f"{resolver.__module__} cannot be handed a Settings"
        assert params["settings"].default is None
