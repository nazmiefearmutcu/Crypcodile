"""Convert canonical Records to flat dicts suitable for Polars/Parquet writing.

Each row gets three extra partition columns:
    channel : str           — discriminator tag (e.g. "trade", "book_snapshot")
    date    : str           — UTC date "YYYY-MM-DD" derived from local_ts
    bucket  : int           — hash(symbol) % 128, avoids per-symbol directory explosion

``from_row`` is the inverse: reconstruct a Record from a Parquet-read flat dict.
"""

from __future__ import annotations

import datetime
import enum
from typing import Any

import mmh3
import msgspec.structs

from crocodile.schema.enums import OptType, Side
from crocodile.schema.records import (
    OHLCV,
    BookDelta,
    BookSnapshot,
    BookTicker,
    DerivativeTicker,
    Funding,
    Liquidation,
    OpenInterest,
    OptionsChain,
    Record,
    Trade,
)


def _symbol_bucket(symbol: str) -> int:
    """Stable MurmurHash3 bucket for a canonical symbol string.

    Uses MurmurHash3 (unsigned) over the UTF-8 bytes of symbol mod 128.
    This gives uniform distribution across [0, 127].
    """
    return mmh3.hash(symbol, signed=False) % 128


def _date_from_ns(local_ts: int) -> str:
    """Return UTC date string "YYYY-MM-DD" from a nanosecond epoch integer."""
    seconds = local_ts // 1_000_000_000
    dt = datetime.datetime.fromtimestamp(seconds, tz=datetime.UTC)
    return dt.strftime("%Y-%m-%d")


def _convert_value(v: Any) -> Any:
    """Coerce enum values to their primitive form."""
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


# ---------------------------------------------------------------------------
# Inverse: flat dict → Record
# ---------------------------------------------------------------------------

# Partition-only columns added by to_row / hive layout — not Record fields.
_PARTITION_COLS = frozenset({"channel", "date", "bucket"})


def _coerce_levels_from_row(raw: Any) -> list[tuple[float, float]]:
    """Convert list-of-dicts or list-of-tuples book levels to list[tuple[float, float]].

    When read back from Parquet via Polars, book levels arrive as a list of
    dicts ``[{"price": ..., "amount": ...}, ...]``.  This converts to the
    canonical Level = tuple[float, float] form.
    """
    if not raw:
        return []
    result: list[tuple[float, float]] = []
    for item in raw:
        if isinstance(item, dict):
            result.append((float(item["price"]), float(item["amount"])))
        else:
            # Already a tuple/list of two numbers
            result.append((float(item[0]), float(item[1])))
    return result


def from_row(row: dict[str, Any]) -> Record:
    """Reconstruct a canonical Record from a flat dict (e.g., read from Parquet).

    The ``channel`` field is used as the discriminator to select the correct
    Record type.  Partition-only columns (``date``, ``bucket``) are stripped
    before construction.  Enum fields are coerced back to their enum types.
    Book-level fields are converted from list-of-dicts back to list[tuple].

    Args:
        row: Flat dict as produced by ``to_row()`` or read from Parquet via
             ``df.to_dicts()``.

    Returns:
        A canonical Record instance.

    Raises:
        ValueError: If the ``channel`` value is unrecognised.
    """
    channel = row["channel"]
    # Strip partition-only columns
    d: dict[str, Any] = {k: v for k, v in row.items() if k not in _PARTITION_COLS}

    if channel == "trade":
        return Trade(
            exchange=d["exchange"],
            symbol=d["symbol"],
            symbol_raw=d["symbol_raw"],
            exchange_ts=d.get("exchange_ts"),
            local_ts=int(d["local_ts"]),
            id=str(d["id"]),
            price=float(d["price"]),
            amount=float(d["amount"]),
            side=Side(d["side"]),
            liquidation=d.get("liquidation"),
        )
    if channel == "book_snapshot":
        return BookSnapshot(
            exchange=d["exchange"],
            symbol=d["symbol"],
            symbol_raw=d["symbol_raw"],
            exchange_ts=d.get("exchange_ts"),
            local_ts=int(d["local_ts"]),
            bids=_coerce_levels_from_row(d.get("bids", [])),
            asks=_coerce_levels_from_row(d.get("asks", [])),
            depth=int(d["depth"]),
            sequence_id=d.get("sequence_id"),
            is_snapshot=bool(d.get("is_snapshot", True)),
        )
    if channel == "book_delta":
        return BookDelta(
            exchange=d["exchange"],
            symbol=d["symbol"],
            symbol_raw=d["symbol_raw"],
            exchange_ts=d.get("exchange_ts"),
            local_ts=int(d["local_ts"]),
            bids=_coerce_levels_from_row(d.get("bids", [])),
            asks=_coerce_levels_from_row(d.get("asks", [])),
            seq_id=d.get("seq_id"),
            prev_seq_id=d.get("prev_seq_id"),
            is_snapshot=bool(d.get("is_snapshot", False)),
        )
    if channel == "book_ticker":
        return BookTicker(
            exchange=d["exchange"],
            symbol=d["symbol"],
            symbol_raw=d["symbol_raw"],
            exchange_ts=d.get("exchange_ts"),
            local_ts=int(d["local_ts"]),
            bid_px=float(d["bid_px"]),
            bid_sz=float(d["bid_sz"]),
            ask_px=float(d["ask_px"]),
            ask_sz=float(d["ask_sz"]),
            update_id=d.get("update_id"),
        )
    if channel == "derivative_ticker":
        return DerivativeTicker(
            exchange=d["exchange"],
            symbol=d["symbol"],
            symbol_raw=d["symbol_raw"],
            exchange_ts=d.get("exchange_ts"),
            local_ts=int(d["local_ts"]),
            last_price=d.get("last_price"),
            mark_price=d.get("mark_price"),
            index_price=d.get("index_price"),
            funding_rate=d.get("funding_rate"),
            predicted_funding_rate=d.get("predicted_funding_rate"),
            funding_timestamp=d.get("funding_timestamp"),
            open_interest=d.get("open_interest"),
        )
    if channel == "options_chain":
        return OptionsChain(
            exchange=d["exchange"],
            symbol=d["symbol"],
            symbol_raw=d["symbol_raw"],
            exchange_ts=d.get("exchange_ts"),
            local_ts=int(d["local_ts"]),
            underlying=str(d["underlying"]),
            underlying_price=d.get("underlying_price"),
            strike=float(d["strike"]),
            expiry=int(d["expiry"]),
            opt_type=OptType(d["opt_type"]),
            mark_price=d.get("mark_price"),
            mark_iv=d.get("mark_iv"),
            bid_px=d.get("bid_px"),
            bid_sz=d.get("bid_sz"),
            bid_iv=d.get("bid_iv"),
            ask_px=d.get("ask_px"),
            ask_sz=d.get("ask_sz"),
            ask_iv=d.get("ask_iv"),
            last_price=d.get("last_price"),
            open_interest=d.get("open_interest"),
            delta=d.get("delta"),
            gamma=d.get("gamma"),
            vega=d.get("vega"),
            theta=d.get("theta"),
            rho=d.get("rho"),
        )
    if channel == "funding":
        return Funding(
            exchange=d["exchange"],
            symbol=d["symbol"],
            symbol_raw=d["symbol_raw"],
            exchange_ts=d.get("exchange_ts"),
            local_ts=int(d["local_ts"]),
            funding_rate=float(d["funding_rate"]),
            funding_timestamp=d.get("funding_timestamp"),
            predicted_funding_rate=d.get("predicted_funding_rate"),
            interval_hours=d.get("interval_hours"),
        )
    if channel == "open_interest":
        return OpenInterest(
            exchange=d["exchange"],
            symbol=d["symbol"],
            symbol_raw=d["symbol_raw"],
            exchange_ts=d.get("exchange_ts"),
            local_ts=int(d["local_ts"]),
            open_interest=float(d["open_interest"]),
            open_interest_value=d.get("open_interest_value"),
        )
    if channel == "liquidation":
        return Liquidation(
            exchange=d["exchange"],
            symbol=d["symbol"],
            symbol_raw=d["symbol_raw"],
            exchange_ts=d.get("exchange_ts"),
            local_ts=int(d["local_ts"]),
            price=float(d["price"]),
            amount=float(d["amount"]),
            side=Side(d["side"]),
            id=d.get("id"),
        )
    if channel == "ohlcv":
        return OHLCV(
            exchange=d["exchange"],
            symbol=d["symbol"],
            symbol_raw=d["symbol_raw"],
            exchange_ts=d.get("exchange_ts"),
            local_ts=int(d["local_ts"]),
            interval=str(d["interval"]),
            open=float(d["open"]),
            high=float(d["high"]),
            low=float(d["low"]),
            close=float(d["close"]),
            volume=float(d["volume"]),
            buy_volume=float(d.get("buy_volume") or 0.0),
            sell_volume=float(d.get("sell_volume") or 0.0),
            num_trades=d.get("num_trades"),
        )
    raise ValueError(f"Unknown channel tag: {channel!r}")
