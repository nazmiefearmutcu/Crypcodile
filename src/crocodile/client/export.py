"""Multi-format export helper for CrocodileClient (Task 3.3).

Supported formats:
    parquet — Apache Parquet (zstd-5, row_group_size=250k)
    csv     — Comma-separated values with header row
    arrow   — Arrow IPC (Feather v2) stream format
    json    — JSON array of objects
    jsonl   — Newline-delimited JSON (one object per line)

Usage::

    client.export(
        channel="trade",
        symbols=["deribit:BTC-PERPETUAL"],
        frm=start_ns,
        to=end_ns,
        fmt="csv",
        dest=pathlib.Path("/data/out/trades.csv"),
    )

Design notes:
    - The function scans the catalog for the requested channel x symbols x time
      range, then writes the resulting Polars DataFrame to the specified format.
    - The parent directory of ``dest`` is created automatically.
    - An empty result set (no matching rows) still creates the destination file
      (zero-byte for formats that have no schema-only representation; a valid
      empty structure for formats that do).
    - Arrow export uses PyArrow IPC (``ipc.new_file``) rather than Polars'
      native Arrow writer so that the file is readable by standard Arrow
      tooling (``pyarrow.ipc.open_file``).
    - Book-level columns (``bids``/``asks``) are stored as Polars
      ``list[struct]`` in Parquet.  JSON/JSONL serialisation relies on Polars'
      built-in ``write_ndjson`` / ``write_json``, which handle nested structs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import polars as pl
import pyarrow.ipc as pa_ipc

from crocodile.store.catalog import Catalog

# Supported format strings (exposed as a Literal type for type-checker friendliness).
ExportFmt = Literal["parquet", "csv", "arrow", "json", "jsonl"]

_VALID_FMTS: frozenset[str] = frozenset({"parquet", "csv", "arrow", "json", "jsonl"})


def export(
    catalog: Catalog,
    channel: str,
    symbols: list[str],
    frm: int,
    to: int,
    fmt: str,
    dest: Path | str,
) -> None:
    """Export rows for ``(channel, symbols, [frm, to])`` to a file in ``fmt`` format.

    Args:
        catalog:  A :class:`~crocodile.store.catalog.Catalog` instance pointing
                  at the data lake root.
        channel:  Channel name, e.g. ``"trade"``, ``"book_snapshot"``.
        symbols:  List of canonical symbol strings.  An empty list writes an
                  empty file.
        frm:      Inclusive lower bound on ``local_ts`` (nanoseconds UTC).
        to:       Inclusive upper bound on ``local_ts`` (nanoseconds UTC).
        fmt:      One of ``parquet``, ``csv``, ``arrow``, ``json``, ``jsonl``.
        dest:     Destination file path.  Parent directories are created if
                  they do not exist.

    Raises:
        ValueError: If ``fmt`` is not one of the supported format strings.
    """
    if fmt not in _VALID_FMTS:
        raise ValueError(
            f"Unsupported fmt={fmt!r}. Must be one of: {sorted(_VALID_FMTS)}"
        )

    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)

    # Collect rows across all requested symbols.
    frames: list[pl.DataFrame] = []
    for symbol in symbols:
        df = catalog.scan(channel, symbol, frm, to)
        if len(df) > 0:
            frames.append(df)

    if frames:
        if len(frames) > 1:
            result = pl.concat(frames, how="diagonal").sort("local_ts")
        else:
            result = frames[0]
    else:
        result = pl.DataFrame()

    _write(result, fmt, dest)


# ---------------------------------------------------------------------------
# Per-format write helpers
# ---------------------------------------------------------------------------


def _write(df: pl.DataFrame, fmt: str, dest: Path) -> None:
    if fmt == "parquet":
        _write_parquet(df, dest)
    elif fmt == "csv":
        _write_csv(df, dest)
    elif fmt == "arrow":
        _write_arrow(df, dest)
    elif fmt == "json":
        _write_json(df, dest)
    elif fmt == "jsonl":
        _write_jsonl(df, dest)


def _write_parquet(df: pl.DataFrame, dest: Path) -> None:
    """Write a Polars DataFrame as Parquet (zstd-5, row_group_size=250k)."""
    if len(df) == 0:
        # write_parquet on an empty DataFrame still produces a valid file.
        dest.write_bytes(b"")
        return
    df.write_parquet(
        dest,
        compression="zstd",
        compression_level=5,
        row_group_size=250_000,
    )


def _write_csv(df: pl.DataFrame, dest: Path) -> None:
    """Write a Polars DataFrame as CSV with header."""
    if len(df) == 0:
        dest.write_bytes(b"")
        return
    df.write_csv(dest)


def _write_arrow(df: pl.DataFrame, dest: Path) -> None:
    """Write a Polars DataFrame as Arrow IPC (Feather v2) using PyArrow IPC."""
    if len(df) == 0:
        dest.write_bytes(b"")
        return
    # Convert to PyArrow Table then write IPC stream.
    table = df.to_arrow()
    with pa_ipc.new_file(str(dest), table.schema) as writer:  # type: ignore[no-untyped-call]
        writer.write_table(table)


def _write_json(df: pl.DataFrame, dest: Path) -> None:
    """Write a Polars DataFrame as a JSON array of objects."""
    if len(df) == 0:
        dest.write_text("[]")
        return
    # Polars write_json produces a JSON array.
    df.write_json(dest)


def _write_jsonl(df: pl.DataFrame, dest: Path) -> None:
    """Write a Polars DataFrame as JSONL (one JSON object per line)."""
    if len(df) == 0:
        dest.write_bytes(b"")
        return
    # Polars write_ndjson produces newline-delimited JSON.
    df.write_ndjson(dest)
