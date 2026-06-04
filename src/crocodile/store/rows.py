"""Convert canonical Records to flat dicts suitable for Polars/Parquet writing.

Each row gets three extra partition columns:
    channel : str           — discriminator tag (e.g. "trade", "book_snapshot")
    date    : str           — UTC date "YYYY-MM-DD" derived from local_ts
    bucket  : int           — hash(symbol) % 128, avoids per-symbol directory explosion
"""

from __future__ import annotations

import datetime
import enum
import hashlib
from typing import Any

from crocodile.schema.records import Record


def _symbol_bucket(symbol: str) -> int:
    """Stable MurmurHash3-equivalent bucket for a canonical symbol string.

    Uses MD5 (stdlib, deterministic) over the UTF-8 bytes of symbol,
    taking the first 4 bytes as an unsigned little-endian integer mod 128.
    This gives uniform distribution across [0, 127].
    """
    digest = hashlib.md5(symbol.encode(), usedforsecurity=False).digest()
    return int.from_bytes(digest[:4], "little") % 128


def _date_from_ns(local_ts: int) -> str:
    """Return UTC date string "YYYY-MM-DD" from a nanosecond epoch integer."""
    dt = datetime.datetime.fromtimestamp(local_ts / 1_000_000_000.0, tz=datetime.UTC)
    return dt.strftime("%Y-%m-%d")


def _convert_value(v: Any) -> Any:
    """Recursively coerce enum values to their primitive form."""
    if isinstance(v, enum.Enum):
        return v.value
    return v


def to_row(record: Record) -> dict[str, Any]:
    """Flatten a Record Struct into a dict ready for Polars / Parquet.

    Added partition columns:
        - ``channel`` : the msgspec tag string (e.g. "trade")
        - ``date``    : UTC date from ``local_ts`` (e.g. "2023-11-14")
        - ``bucket``  : hash(symbol) % 128

    Enum fields (``side``, ``opt_type``) are converted to their string values.
    List-of-tuple fields (``bids``, ``asks``) are preserved as Python
    ``list[tuple[float, float]]`` — Polars can infer these as list[struct].
    """
    import msgspec.structs

    # Extract channel tag from the struct class metadata
    channel: str = type(record).__struct_config__.tag  # type: ignore[assignment]

    # Build the base dict from struct fields
    raw = msgspec.structs.asdict(record)

    # Coerce enum values to primitives
    row: dict[str, Any] = {k: _convert_value(v) for k, v in raw.items()}

    # Add partition columns
    row["channel"] = channel
    row["date"] = _date_from_ns(record.local_ts)
    row["bucket"] = _symbol_bucket(record.symbol)

    return row
