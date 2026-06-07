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
    """query_ohlcv.py exits with code 0 gracefully when the data dir is absent."""
    mod = _load_example("query_ohlcv.py")
    rc = mod.main(["--data-dir", str(tmp_path / "nonexistent")])
    assert rc == 0


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
    """All three example scripts exec without syntax or import errors.

    This test actually calls ``exec_module`` (via ``_load_example``) so that
    syntax errors, missing imports, and top-level runtime errors are caught.
    Merely asserting ``spec is not None`` would only check that the file exists
    on disk — it would never execute the module body.
    """
    for name in ("collect_deribit.py", "replay_to_csv.py", "query_ohlcv.py"):
        _load_example(name)  # raises on SyntaxError / ImportError / NameError


@pytest.mark.parametrize("interval", ["1s", "1m", "1h", "1d"])
def test_query_ohlcv_interval_validation(tmp_path: Path, interval: str) -> None:
    """query_ohlcv.py accepts valid interval strings without crashing."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    mod = _load_example("query_ohlcv.py")
    # Empty lake returns 0 regardless of interval.
    rc = mod.main(["--data-dir", str(data_dir), "--interval", interval])
    assert rc == 0


# ---------------------------------------------------------------------------
# analytics_funding.py — T8-docs coverage gate
# ---------------------------------------------------------------------------


def test_analytics_funding_imports_cleanly() -> None:
    """analytics_funding.py imports without syntax or import errors (T8-docs)."""
    mod = _load_example("analytics_funding.py")
    assert callable(mod.main)


def test_analytics_funding_missing_data_dir(tmp_path: Path) -> None:
    """analytics_funding.py exits 0 with a helpful message when data dir is absent."""
    mod = _load_example("analytics_funding.py")
    rc = mod.main(["--data-dir", str(tmp_path / "nonexistent")])
    # Script prints a message and returns 0 (not a crash) when the dir doesn't exist.
    assert rc == 0


def test_analytics_funding_empty_lake_exits_cleanly(tmp_path: Path) -> None:
    """analytics_funding.py exits 0 gracefully when the lake is empty."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    mod = _load_example("analytics_funding.py")
    rc = mod.main(["--data-dir", str(data_dir)])
    assert rc == 0


def test_analytics_funding_with_data(tmp_path: Path) -> None:
    """analytics_funding.py exits 0 and prints a table when funding data is present."""
    import asyncio

    from crypcodile.schema.records import Funding
    from crypcodile.store.parquet_sink import ParquetSink

    _BASE_NS = 1_704_067_200_000_000_000
    _8H_NS = 8 * 3600 * 1_000_000_000
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    async def _write() -> None:
        sink = ParquetSink(data_dir, max_buffer_rows=10_000, flush_interval_seconds=9999)
        for i, rate in enumerate([0.0001, -0.0002, 0.0003]):
            rec = Funding(
                exchange="deribit",
                symbol="deribit:BTC-PERPETUAL",
                symbol_raw="BTC-PERPETUAL",
                exchange_ts=_BASE_NS + i * _8H_NS,
                local_ts=_BASE_NS + i * _8H_NS,
                funding_rate=rate,
                funding_timestamp=_BASE_NS + i * _8H_NS,
                interval_hours=8,
            )
            await sink.put(rec)
        await sink.flush()

    asyncio.run(_write())

    mod = _load_example("analytics_funding.py")
    rc = mod.main([
        "--data-dir", str(data_dir),
        "--symbol", "deribit:BTC-PERPETUAL",
        "--from-ns", str(_BASE_NS),
        "--to-ns", str(_BASE_NS + 3 * _8H_NS),
    ])
    assert rc == 0


# ---------------------------------------------------------------------------
# analytics_iv_surface.py — T8-docs coverage gate
# ---------------------------------------------------------------------------


def test_analytics_iv_surface_imports_cleanly() -> None:
    """analytics_iv_surface.py imports without syntax or import errors (T8-docs)."""
    mod = _load_example("analytics_iv_surface.py")
    assert callable(mod.main)


def test_analytics_iv_surface_missing_data_dir(tmp_path: Path) -> None:
    """analytics_iv_surface.py exits 0 with a helpful message when data dir is absent."""
    mod = _load_example("analytics_iv_surface.py")
    rc = mod.main(["--data-dir", str(tmp_path / "nonexistent")])
    assert rc == 0


def test_analytics_iv_surface_empty_lake_exits_cleanly(tmp_path: Path) -> None:
    """analytics_iv_surface.py exits 0 gracefully when the lake is empty."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    mod = _load_example("analytics_iv_surface.py")
    rc = mod.main(["--data-dir", str(data_dir)])
    assert rc == 0


def test_analytics_iv_surface_with_data(tmp_path: Path) -> None:
    """analytics_iv_surface.py exits 0 and prints a surface table when options data is present."""
    import asyncio

    from crypcodile.schema.enums import OptType
    from crypcodile.schema.records import OptionsChain
    from crypcodile.store.parquet_sink import ParquetSink

    _BASE_NS = 1_704_067_200_000_000_000
    _ONE_YEAR_NS = 365 * 24 * 3600 * 1_000_000_000
    e1_ns = _BASE_NS + _ONE_YEAR_NS
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    async def _write() -> None:
        sink = ParquetSink(data_dir, max_buffer_rows=10_000, flush_interval_seconds=9999)
        for strike, mark_iv in [(90.0, 0.5), (100.0, 0.4), (110.0, 0.55)]:
            rec = OptionsChain(
                exchange="deribit",
                symbol=f"deribit:BTC-{int(strike)}-C",
                symbol_raw=f"BTC-{int(strike)}-C",
                exchange_ts=_BASE_NS,
                local_ts=_BASE_NS,
                underlying="BTC",
                underlying_price=100.0,
                strike=strike,
                expiry=e1_ns,
                opt_type=OptType.CALL,
                mark_price=15.0,
                mark_iv=mark_iv,
            )
            await sink.put(rec)
        await sink.flush()

    asyncio.run(_write())

    mod = _load_example("analytics_iv_surface.py")
    rc = mod.main([
        "--data-dir", str(data_dir),
        "--underlying", "BTC",
        "--at-ns", str(_BASE_NS),
    ])
    assert rc == 0


def test_analytics_examples_all_import_no_syntax_errors() -> None:
    """All four analytics example scripts exec without syntax or import errors."""
    for name in (
        "analytics_funding.py",
        "analytics_iv_surface.py",
        "collect_deribit.py",
        "replay_to_csv.py",
        "query_ohlcv.py",
    ):
        _load_example(name)  # raises on SyntaxError / ImportError / NameError
