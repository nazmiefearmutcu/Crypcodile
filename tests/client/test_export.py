"""Acceptance tests for CrocodileClient.export (Task 3.3).

.export(channel, symbols, frm, to, fmt, dest) for fmt ∈ {parquet, csv, arrow, json, jsonl}.

Each format writes a non-empty file that re-reads to the same row count;
JSONL has one record per line.
"""

from __future__ import annotations

import json
import pathlib

import polars as pl
import pyarrow.ipc as pa_ipc
import pytest

from crocodile.schema.enums import Side
from crocodile.schema.records import BookSnapshot, Trade
from crocodile.store.parquet_sink import ParquetSink

_BASE_TS = 1_700_000_000_000_000_000  # 2023-11-14
_SYMBOL = "deribit:BTC-PERPETUAL"


def _trade(local_ts: int, price: float = 1.0) -> Trade:
    return Trade(
        exchange="deribit",
        symbol=_SYMBOL,
        symbol_raw="BTC-PERPETUAL",
        exchange_ts=local_ts,
        local_ts=local_ts,
        id=str(local_ts),
        price=price,
        amount=1.0,
        side=Side.BUY,
    )


def _snap(local_ts: int = _BASE_TS) -> BookSnapshot:
    return BookSnapshot(
        exchange="deribit",
        symbol=_SYMBOL,
        symbol_raw="BTC-PERPETUAL",
        exchange_ts=local_ts,
        local_ts=local_ts,
        bids=[(100.0, 5.0)],
        asks=[(101.0, 4.0)],
        depth=1,
        sequence_id=1,
        is_snapshot=True,
    )


_N_TRADES = 3

_FRM = _BASE_TS
_TO = _BASE_TS + (_N_TRADES + 1) * 1_000_000_000


async def _write_fixtures(data_dir: pathlib.Path) -> None:
    sink = ParquetSink(data_dir=data_dir, max_buffer_rows=100, flush_interval_seconds=9999)
    for i in range(_N_TRADES):
        await sink.put(_trade(local_ts=_BASE_TS + i * 1_000_000_000, price=float(i + 1)))
    await sink.put(_snap(local_ts=_BASE_TS))
    await sink.flush()


# ---------------------------------------------------------------------------
# Parquet export
# ---------------------------------------------------------------------------


async def test_export_parquet(tmp_path: pathlib.Path) -> None:
    """Parquet export writes a non-empty .parquet file that re-reads to same row count."""
    from crocodile.client.client import CrocodileClient

    await _write_fixtures(tmp_path)
    client = CrocodileClient(data_dir=tmp_path)

    dest = tmp_path / "out" / "trades.parquet"
    client.export("trade", [_SYMBOL], _FRM, _TO, fmt="parquet", dest=dest)

    assert dest.exists(), "Output file must be created"
    assert dest.stat().st_size > 0, "Output file must be non-empty"

    df = pl.read_parquet(dest)
    assert len(df) == _N_TRADES, f"Expected {_N_TRADES} rows, got {len(df)}"


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------


async def test_export_csv(tmp_path: pathlib.Path) -> None:
    """CSV export writes a non-empty .csv file that re-reads to same row count."""
    from crocodile.client.client import CrocodileClient

    await _write_fixtures(tmp_path)
    client = CrocodileClient(data_dir=tmp_path)

    dest = tmp_path / "out" / "trades.csv"
    client.export("trade", [_SYMBOL], _FRM, _TO, fmt="csv", dest=dest)

    assert dest.exists()
    assert dest.stat().st_size > 0

    df = pl.read_csv(dest)
    assert len(df) == _N_TRADES


# ---------------------------------------------------------------------------
# Arrow IPC (Feather v2) export
# ---------------------------------------------------------------------------


async def test_export_arrow(tmp_path: pathlib.Path) -> None:
    """Arrow IPC export writes a non-empty .arrow file that re-reads to same row count."""
    from crocodile.client.client import CrocodileClient

    await _write_fixtures(tmp_path)
    client = CrocodileClient(data_dir=tmp_path)

    dest = tmp_path / "out" / "trades.arrow"
    client.export("trade", [_SYMBOL], _FRM, _TO, fmt="arrow", dest=dest)

    assert dest.exists()
    assert dest.stat().st_size > 0

    # Read with PyArrow IPC
    with pa_ipc.open_file(str(dest)) as reader:
        table = reader.read_all()
    assert table.num_rows == _N_TRADES


# ---------------------------------------------------------------------------
# JSON export (array of objects)
# ---------------------------------------------------------------------------


async def test_export_json(tmp_path: pathlib.Path) -> None:
    """JSON export writes a non-empty JSON array that parses to the same row count."""
    from crocodile.client.client import CrocodileClient

    await _write_fixtures(tmp_path)
    client = CrocodileClient(data_dir=tmp_path)

    dest = tmp_path / "out" / "trades.json"
    client.export("trade", [_SYMBOL], _FRM, _TO, fmt="json", dest=dest)

    assert dest.exists()
    assert dest.stat().st_size > 0

    data = json.loads(dest.read_text())
    assert isinstance(data, list), "JSON output must be a JSON array"
    assert len(data) == _N_TRADES


# ---------------------------------------------------------------------------
# JSONL export (one JSON object per line)
# ---------------------------------------------------------------------------


async def test_export_jsonl(tmp_path: pathlib.Path) -> None:
    """JSONL export writes one record per line; line count matches row count."""
    from crocodile.client.client import CrocodileClient

    await _write_fixtures(tmp_path)
    client = CrocodileClient(data_dir=tmp_path)

    dest = tmp_path / "out" / "trades.jsonl"
    client.export("trade", [_SYMBOL], _FRM, _TO, fmt="jsonl", dest=dest)

    assert dest.exists()
    assert dest.stat().st_size > 0

    lines = [ln for ln in dest.read_text().splitlines() if ln.strip()]
    assert len(lines) == _N_TRADES, f"Expected {_N_TRADES} lines, got {len(lines)}"

    # Each line must be valid JSON
    for line in lines:
        obj = json.loads(line)
        assert isinstance(obj, dict), "Each JSONL line must be a JSON object"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


async def test_export_creates_parent_dirs(tmp_path: pathlib.Path) -> None:
    """export() creates intermediate directories if they don't exist."""
    from crocodile.client.client import CrocodileClient

    await _write_fixtures(tmp_path)
    client = CrocodileClient(data_dir=tmp_path)

    dest = tmp_path / "deep" / "nested" / "dir" / "trades.parquet"
    client.export("trade", [_SYMBOL], _FRM, _TO, fmt="parquet", dest=dest)

    assert dest.exists()


@pytest.mark.parametrize(
    "fmt,extension",
    [
        ("csv", "csv"),
        ("parquet", "parquet"),
        ("arrow", "arrow"),
        ("json", "json"),
        ("jsonl", "jsonl"),
    ],
)
async def test_export_empty_result_creates_empty_file(
    tmp_path: pathlib.Path, fmt: str, extension: str
) -> None:
    """export() with a time range that matches nothing still creates the dest file.

    Parquet and Arrow formats must produce valid/readable files even when there
    are zero matching rows (regression guard against the empty-file bug fixed in
    the 3.3 commit).  CSV and JSON formats are also verified for completeness.
    """
    from crocodile.client.client import CrocodileClient

    await _write_fixtures(tmp_path)
    client = CrocodileClient(data_dir=tmp_path)

    dest = tmp_path / "out" / f"empty.{extension}"
    # Far-future range — no data
    client.export(
        "trade",
        [_SYMBOL],
        _BASE_TS + 999_000_000_000_000,
        _BASE_TS + 999_999_000_000_000,
        fmt=fmt,  # type: ignore[arg-type]
        dest=dest,
    )

    assert dest.exists(), f"Output file must be created for fmt={fmt!r}"

    # Read-back assertions per format — ensures the file is valid, not just present.
    if fmt == "parquet":
        df = pl.read_parquet(dest)
        assert len(df) == 0, f"Empty parquet must have 0 rows, got {len(df)}"
    elif fmt == "arrow":
        with pa_ipc.open_file(str(dest)) as reader:
            table = reader.read_all()
        assert table.num_rows == 0, f"Empty arrow must have 0 rows, got {table.num_rows}"
    elif fmt == "csv":
        content = dest.read_text()
        # Empty CSV is written as a zero-byte file (no header when schema is unknown)
        assert content == "", f"Empty CSV must be empty bytes, got {content!r}"
    elif fmt == "json":
        content = dest.read_text()
        assert content == "[]", f"Empty JSON must be '[]', got {content!r}"
    elif fmt == "jsonl":
        content = dest.read_text()
        assert content == "", f"Empty JSONL must be empty bytes, got {content!r}"


async def test_export_invalid_fmt_raises(tmp_path: pathlib.Path) -> None:
    """export() raises ValueError for an unsupported format string."""
    from crocodile.client.client import CrocodileClient

    await _write_fixtures(tmp_path)
    client = CrocodileClient(data_dir=tmp_path)

    dest = tmp_path / "out.xyz"
    with pytest.raises(ValueError, match="fmt"):
        client.export("trade", [_SYMBOL], _FRM, _TO, fmt="xyz", dest=dest)


# ---------------------------------------------------------------------------
# Multi-symbol export (diagonal concat + time-order correctness)
# ---------------------------------------------------------------------------

_SYMBOL_B = "deribit:ETH-PERPETUAL"


async def test_export_multi_symbol_sorted_and_no_null_leakage(
    tmp_path: pathlib.Path,
) -> None:
    """Two interleaved symbols are merged, sorted by local_ts, and have no spurious nulls.

    This exercises the ``pl.concat(frames, how="diagonal").sort("local_ts")`` path
    in export().  Both symbols share the same schema (same columns), so a diagonal
    concat must NOT introduce null columns.  The output must be globally non-decreasing
    in ``local_ts``.
    """
    from crocodile.client.client import CrocodileClient

    # Write trades for _SYMBOL and _SYMBOL_B with interleaved timestamps.
    sink = ParquetSink(data_dir=tmp_path, max_buffer_rows=100, flush_interval_seconds=9999)

    # Interleave: A@T+0, B@T+1, A@T+2, B@T+3
    for i in range(4):
        sym = _SYMBOL if i % 2 == 0 else _SYMBOL_B
        ts = _BASE_TS + i * 1_000_000_000
        trade = Trade(
            exchange="deribit",
            symbol=sym,
            symbol_raw=sym.split(":")[1],
            exchange_ts=ts,
            local_ts=ts,
            id=str(ts),
            price=float(i + 1),
            amount=1.0,
            side=Side.BUY,
        )
        await sink.put(trade)
    await sink.flush()

    client = CrocodileClient(data_dir=tmp_path)
    dest = tmp_path / "out" / "multi.parquet"
    client.export("trade", [_SYMBOL, _SYMBOL_B], _FRM, _TO, fmt="parquet", dest=dest)

    assert dest.exists()
    assert dest.stat().st_size > 0

    df = pl.read_parquet(dest)

    # Total row count: 2 from each symbol
    assert len(df) == 4, f"Expected 4 rows (2 per symbol), got {len(df)}"

    # Output must be non-decreasing in local_ts (merge+sort correctness)
    ts_values = df["local_ts"].to_list()
    assert ts_values == sorted(ts_values), "Rows must be sorted by local_ts"

    # No spurious nulls introduced by diagonal concat in shared columns
    for col in ("exchange", "symbol", "price", "amount", "side"):
        null_count = df[col].null_count()
        assert null_count == 0, f"Column {col!r} has {null_count} unexpected nulls"
