"""Convert canonical Records to flat dicts suitable for Polars/Parquet writing.

Each row gets four extra partition columns:
    source  : str           — venue or data provider (e.g. "binance", "yahoo")
    channel : str           — discriminator tag (e.g. "trade", "book_snapshot")
    date    : str           — UTC date "YYYY-MM-DD" derived from local_ts
    bucket  : int           — hash(symbol) % 128, avoids per-symbol directory explosion

``source`` is a path component only: it names the top-level ``source=``
partition and is deliberately kept out of the Parquet schema. A lake migrated
by :func:`crocodile.core.store.migrate.migrate_lake` is renamed, never
rewritten, so files written before and after the rename must agree field for
field; hive partitioning supplies ``source`` on read for both.

``from_row`` is the inverse: reconstruct a Record from a Parquet-read flat dict.
"""

from __future__ import annotations

import datetime
import enum
from typing import Any

import mmh3
import msgspec.structs

from crocodile.core.schema.enums import AssetClass, OptType, Side, Tape
from crocodile.core.schema.provenance import Provenance
from crocodile.core.schema.records import (
    OHLCV,
    BookDelta,
    BookSnapshot,
    BookTicker,
    DerivativeTicker,
    FarcasterCorrelation,
    Funding,
    Liquidation,
    LiquidationCall,
    OpenInterest,
    OptionsChain,
    Record,
    ReserveDataUpdated,
    Trade,
)

Flattenable = Record
"""What :func:`to_row` accepts.

``to_row`` reads only the msgspec tag plus ``local_ts`` and ``symbol``, so it
flattens any record-shaped struct — including the legacy equity union, which
:mod:`crocodile.equity.store.rows` still hands it. The annotation names the
canonical union because that is the only family ``core`` may depend on by name.
"""


# What each record family calls the place the data came from, most canonical
# first. The canonical header says ``source``; the legacy equity union, which is
# still live, says ``provider``, and the retired crypto union said ``exchange``.
#
# ``provider`` is checked before ``exchange`` and the order is load-bearing.
# Equity's ``Instrument`` carries BOTH: ``provider`` is who served the data,
# ``exchange`` is where the security is *listed*. Matching ``exchange`` first
# filed an Alpaca-sourced instrument under ``source=NASDAQ`` — no error, just
# the wrong directory. The canonical ``Instrument`` inherited that ``exchange``
# field, so the hazard did not retire with the crypto union: it is only ``source``
# leading the tuple that keeps a canonical record off the listing venue.
_ORIGIN_FIELDS = ("source", "provider", "exchange")

# Partition columns computed from the record rather than read off it. A record
# field sharing one of these names loses to it, so it is moved to ``<name>_val``
# before they are written.
_DERIVED_PARTITION_COLS = ("channel", "date", "bucket")


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


def to_row(record: Flattenable) -> dict[str, Any]:
    """Flatten a Record Struct into a dict ready for Polars / Parquet.

    Added partition columns:
        - ``source``  : canonical ``source``, or the legacy ``provider`` field
        - ``channel`` : the msgspec tag string (e.g. "trade")
        - ``date``    : UTC date from ``local_ts`` (e.g. "2023-11-14")
        - ``bucket``  : hash(symbol) % 128

    Enum fields (``side``, ``opt_type``) are converted to their string values.
    List-of-tuple fields (``bids``, ``asks``) are preserved as Python
    ``list[tuple[float, float]]`` — Polars can infer these as list[struct].

    Raises:
        KeyError: if the record names its origin under none of the three known
            field names. Every record family names its origin; one that names
            none cannot be partitioned, and guessing a partition is worse than
            refusing.
    """
    # Extract channel tag from the struct class metadata
    channel: str = type(record).__struct_config__.tag  # type: ignore[assignment]

    # Build the base dict from struct fields
    raw = msgspec.structs.asdict(record)

    # Coerce enum values to primitives
    row: dict[str, Any] = {k: _convert_value(v) for k, v in raw.items()}

    # Add partition columns. The canonical records call the origin ``source``
    # and the legacy equity records ``provider`` (the retired crypto union said
    # ``exchange``). One partition key spans all three, so the names collapse
    # here rather than at the sink — this is the single place where "which fork
    # wrote this record" stops mattering.
    for field in _ORIGIN_FIELDS:
        if field in raw:
            row["source"] = raw[field]
            break
    else:
        raise KeyError(
            f"{type(record).__name__} names its origin under none of "
            f"{_ORIGIN_FIELDS}; it cannot be assigned a source= partition"
        )
    # A record field named like a partition column would be overwritten by it.
    # ``ShortVolume.date`` and ``MacroSeries.date`` are business dates — the
    # settlement day the figure belongs to — and the partition ``date`` is the
    # capture day; writing the second over the first destroyed the first, with
    # no error and nothing in the file to recover it from. The equity fork moved
    # its own ``date`` aside; the rule is stated here once and applies to any
    # record from any family. No crypto or canonical record collides today, so
    # crypto output is byte-identical.
    # ``source`` is deliberately not in this list: a canonical record's own
    # ``source`` field *is* the partition value, so moving it aside would empty
    # the column it was just derived from.
    for column in _DERIVED_PARTITION_COLS:
        if column in raw:
            row[f"{column}_val"] = row.pop(column)

    row["channel"] = channel
    row["date"] = _date_from_ns(record.local_ts)
    row["bucket"] = _symbol_bucket(record.symbol)

    return row


# ---------------------------------------------------------------------------
# Inverse: flat dict → Record
# ---------------------------------------------------------------------------

# Partition-only columns added by to_row / hive layout — not Record fields.
# ``source`` is deliberately NOT here any more: it used to be partition-only
# because ``from_row`` built the legacy crypto structs, whose own field was
# ``exchange``. The canonical records carry a real ``source`` field, and hive
# partitioning is the only place a read gets it back, so stripping it would
# leave the header with nothing to say where the record came from.
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


def _header(d: dict[str, Any]) -> dict[str, Any]:
    """Rebuild the ten canonical header fields from one flattened row.

    Two shapes reach this function and both have to work. Rows ``to_row`` writes
    today carry the canonical header. Rows already sitting in a crypto lake do
    not: they name the origin ``exchange`` / ``exchange_ts`` and have no
    ``asset_class`` and no provenance tail, because that is what the retired
    crypto union wrote. ``migrate_lake`` renames partition directories without
    reading a Parquet byte, so those files keep their columns forever and this
    is the only place the two spellings can be reconciled.

    Defaulting such a row to :attr:`AssetClass.CRYPTO` is not a guess:
    ``exchange`` was the crypto union's word for the origin and the equity fork
    said ``provider``, so the column itself identifies the market. A row with
    neither column raises rather than picking a market, since an asset class
    invented here is indistinguishable from one that was recorded.
    """
    if (source := d.get("source")) is None:
        source = d["exchange"]

    raw_class = d.get("asset_class")
    if raw_class is not None:
        asset_class = AssetClass(raw_class)
    elif "exchange" in d:
        asset_class = AssetClass.CRYPTO
    else:
        raise KeyError(
            "row carries neither 'asset_class' nor the pre-migration 'exchange' "
            "column; its market cannot be established"
        )

    source_ts = d.get("source_ts")
    if source_ts is None:
        source_ts = d.get("exchange_ts")

    # The provenance tail is absent from every pre-migration row. Falling back
    # to the struct defaults says NATIVE, which is the truth about those files:
    # the fork only ever wrote venue-reported records.
    prov = d.get("prov")
    prov_confidence = d.get("prov_confidence")
    return {
        "source": str(source),
        "symbol": str(d["symbol"]),
        "symbol_raw": str(d["symbol_raw"]),
        "local_ts": int(d["local_ts"]),
        "asset_class": asset_class,
        "source_ts": int(source_ts) if source_ts is not None else None,
        "prov": Provenance(prov) if prov is not None else Provenance.NATIVE,
        "prov_basis": d.get("prov_basis") or "native",
        "prov_confidence": float(prov_confidence) if prov_confidence is not None else 1.0,
        "prov_inputs": list(d.get("prov_inputs") or []),
    }


def from_row(row: dict[str, Any]) -> Record:
    """Reconstruct a canonical Record from a flat dict (e.g., read from Parquet).

    The ``channel`` field is used as the discriminator to select the correct
    Record type.  Partition-only columns (``date``, ``bucket``) are stripped
    before construction.  Enum fields are coerced back to their enum types.
    Book-level fields are converted from list-of-dicts back to list[tuple].

    The header is rebuilt by :func:`_header`, which also accepts a row from a
    crypto lake written before the union merge — ``exchange`` becomes
    ``source``, ``exchange_ts`` becomes ``source_ts``, and a row carrying an
    ``exchange`` column and no ``asset_class`` reads back as
    :attr:`AssetClass.CRYPTO`.

    Only the channels the crypto union defined are reconstructed here; the
    equity-only tags still belong to :mod:`crocodile.equity.store.rows`, which
    is what knows their legacy structs.

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
    header = _header(d)

    if channel == "trade":
        tape = d.get("tape")
        return Trade(
            **header,
            id=str(d["id"]),
            price=float(d["price"]),
            amount=float(d["amount"]),
            side=Side(d["side"]),
            liquidation=d.get("liquidation"),
            l1_gas_fee=float(d["l1_gas_fee"]) if d.get("l1_gas_fee") is not None else None,
            l2_gas_fee=float(d["l2_gas_fee"]) if d.get("l2_gas_fee") is not None else None,
            gas_price=float(d["gas_price"]) if d.get("gas_price") is not None else None,
            sender=d.get("sender"),
            is_smart_wallet=(
                bool(d["is_smart_wallet"]) if d.get("is_smart_wallet") is not None else None
            ),
            conditions=list(d["conditions"]) if d.get("conditions") is not None else None,
            tape=Tape(tape) if tape is not None else None,
            venue=d.get("venue"),
        )
    if channel == "book_snapshot":
        return BookSnapshot(
            **header,
            bids=_coerce_levels_from_row(d.get("bids", [])),
            asks=_coerce_levels_from_row(d.get("asks", [])),
            depth=int(d["depth"]),
            sequence_id=d.get("sequence_id"),
            is_snapshot=bool(d.get("is_snapshot", True)),
        )
    if channel == "book_delta":
        return BookDelta(
            **header,
            bids=_coerce_levels_from_row(d.get("bids", [])),
            asks=_coerce_levels_from_row(d.get("asks", [])),
            seq_id=d.get("seq_id"),
            prev_seq_id=d.get("prev_seq_id"),
            is_snapshot=bool(d.get("is_snapshot", False)),
        )
    if channel == "book_ticker":
        return BookTicker(
            **header,
            bid_px=float(d["bid_px"]),
            bid_sz=float(d["bid_sz"]),
            ask_px=float(d["ask_px"]),
            ask_sz=float(d["ask_sz"]),
            update_id=d.get("update_id"),
        )
    if channel == "derivative_ticker":
        return DerivativeTicker(
            **header,
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
            **header,
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
            volume=d.get("volume"),
            open_interest=d.get("open_interest"),
            delta=d.get("delta"),
            gamma=d.get("gamma"),
            vega=d.get("vega"),
            theta=d.get("theta"),
            rho=d.get("rho"),
        )
    if channel == "funding":
        return Funding(
            **header,
            funding_rate=float(d["funding_rate"]),
            funding_timestamp=d.get("funding_timestamp"),
            predicted_funding_rate=d.get("predicted_funding_rate"),
            interval_hours=d.get("interval_hours"),
        )
    if channel == "open_interest":
        return OpenInterest(
            **header,
            open_interest=float(d["open_interest"]),
            open_interest_value=d.get("open_interest_value"),
        )
    if channel == "liquidation":
        return Liquidation(
            **header,
            price=float(d["price"]),
            amount=float(d["amount"]),
            side=Side(d["side"]),
            id=d.get("id"),
        )
    if channel == "ohlcv":
        return OHLCV(
            **header,
            interval=str(d["interval"]),
            open=float(d["open"]),
            high=float(d["high"]),
            low=float(d["low"]),
            close=float(d["close"]),
            volume=float(d["volume"]),
            buy_volume=float(d.get("buy_volume") or 0.0),
            sell_volume=float(d.get("sell_volume") or 0.0),
            num_trades=d.get("num_trades"),
            vwap=d.get("vwap"),
        )
    if channel == "farcaster_correlation":
        return FarcasterCorrelation(
            **header,
            mentions_24h=int(d["mentions_24h"]),
            dev_activity_score=float(d["dev_activity_score"]),
            trending_rank=int(d["trending_rank"]),
        )
    if channel == "reserve_data_updated":
        return ReserveDataUpdated(
            **header,
            reserve=d["reserve"],
            liquidity_rate=float(d["liquidity_rate"]),
            stable_borrow_rate=float(d["stable_borrow_rate"]),
            variable_borrow_rate=float(d["variable_borrow_rate"]),
            liquidity_index=int(d["liquidity_index"]),
            variable_borrow_index=int(d["variable_borrow_index"]),
        )
    if channel == "liquidation_call":
        return LiquidationCall(
            **header,
            collateral_asset=d["collateral_asset"],
            debt_asset=d["debt_asset"],
            user=d["user"],
            debt_to_cover=float(d["debt_to_cover"]),
            liquidated_collateral_amount=float(d["liquidated_collateral_amount"]),
            liquidator=d["liquidator"],
            receive_a_token=bool(d["receive_a_token"]),
        )
    raise ValueError(f"Unknown channel tag: {channel!r}")
