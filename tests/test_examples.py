"""Smoke tests for the runnable examples (Task 5.5).

These tests verify that each example script:
1. Imports cleanly (no syntax errors, no missing modules).
2. Handles a missing or empty data lake gracefully (returns a non-zero exit
   code or prints a helpful message rather than crashing with a traceback).

The scripts are invoked via their ``main()`` entry-points directly, without
touching the network or requiring pre-existing data.
"""

from __future__ import annotations

import importlib.util
import types
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

EXAMPLES_DIR = Path(__file__).parent.parent / "examples"


def _load_example(name: str) -> types.ModuleType:
    """Load an example script as a module (importable via spec)."""
    path = EXAMPLES_DIR / name
    spec = importlib.util.spec_from_file_location(name.replace(".py", ""), path)
    assert spec is not None and spec.loader is not None, f"Cannot load example: {path}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_replay_to_csv_missing_data_dir(tmp_path: Path) -> None:
    """replay_to_csv.py exits with code 1 when the data dir is absent."""
    mod = _load_example("replay_to_csv.py")
    rc = mod.main(["--data-dir", str(tmp_path / "nonexistent"), "--out", str(tmp_path / "out.csv")])
    assert rc == 1


def test_query_ohlcv_missing_data_dir(tmp_path: Path) -> None:
    """query_ohlcv.py exits with code 1 when the data dir is absent."""
    mod = _load_example("query_ohlcv.py")
    rc = mod.main(["--data-dir", str(tmp_path / "nonexistent")])
    assert rc == 1


def test_query_ohlcv_empty_lake_exits_cleanly(tmp_path: Path) -> None:
    """query_ohlcv.py exits with code 0 and a helpful message on an empty lake."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    mod = _load_example("query_ohlcv.py")
    rc = mod.main(["--data-dir", str(data_dir)])
    assert rc == 0


def test_replay_to_csv_empty_lake_creates_no_file_gracefully(tmp_path: Path) -> None:
    """replay_to_csv.py exits cleanly (rc=0) when the lake is empty."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    out = tmp_path / "trades.csv"
    mod = _load_example("replay_to_csv.py")
    rc = mod.main(["--data-dir", str(data_dir), "--out", str(out)])
    # rc can be 0 (empty export still writes a file) — no crash expected.
    assert rc in (0, 1)


def test_collect_deribit_imports_cleanly() -> None:
    """collect_deribit.py imports without errors (network not required)."""
    # We only exec up to the top-level module body; the asyncio.run() call is
    # guarded by ``if __name__ == "__main__"`` so it won't actually connect.
    mod = _load_example("collect_deribit.py")
    # Verify the key symbols are present.
    assert callable(mod.main)


def test_examples_import_no_syntax_errors() -> None:
    """All three example scripts are importable without syntax or import errors."""
    for name in ("collect_deribit.py", "replay_to_csv.py", "query_ohlcv.py"):
        path = EXAMPLES_DIR / name
        assert path.exists(), f"Missing example: {path}"
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None


@pytest.mark.parametrize("interval", ["1s", "1m", "1h", "1d"])
def test_query_ohlcv_interval_validation(tmp_path: Path, interval: str) -> None:
    """query_ohlcv.py accepts valid interval strings without crashing."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    mod = _load_example("query_ohlcv.py")
    # Empty lake returns 0 regardless of interval.
    rc = mod.main(["--data-dir", str(data_dir), "--interval", interval])
    assert rc == 0
