"""Flatten and reconstruct the legacy **equity** record union.

Why a second ``from_row`` exists
--------------------------------
``crocodile.core.store.rows.from_row`` reconstructs the legacy *crypto* union;
this one reconstructs the legacy *equity* union. They cannot merge today,
because the two forks reused the same ``channel`` tags for different structs:
``trade``, ``ohlcv``, ``book_snapshot``, ``book_delta``, ``liquidation_call``
and ``reserve_data_updated`` all exist in both unions with different fields
(crypto ``trade`` carries ``exchange``/``amount``/``side``; equity ``trade``
carries ``provider``/``size``/``tape``/``venue``). A single ``from_row`` would
therefore need a second discriminator beyond ``channel`` to pick a class.

``to_row`` needs no such discriminator — it reads the tag and the struct fields
off the record it is handed — which is why the core one was generalised over
both families and this one is only a thin specialisation of it.

The real fix is to stop reconstructing legacy classes at all: have ``from_row``
build the canonical :mod:`crocodile.core.schema.records` structs, which carry a
``source``/``asset_class`` header instead of a fork-specific one. That means
porting every connector's ``normalize()`` onto the canonical records — the same
work Task 9 deferred — so the duplication is deliberate and temporary. The
layering rule holds meanwhile: ``core`` must not import from ``equity``, so the
equity channels live here rather than being bolted onto the core ``from_row``.

How this ``to_row`` differs from the core one
---------------------------------------------
Not a re-export: :func:`to_row` here differs from
``crocodile.core.store.rows.to_row`` in two ways, both of which are silent data
corruption rather than a raised error when the core one is used on an equity
record.

1. **Origin field.** Core picks the first of ``source``/``provider``/
   ``exchange`` the record carries. Every equity record's origin is its
   ``provider``, and :class:`~crocodile.equity.schema.records.Instrument` also
   has an ``exchange`` field — the *listing venue*. Core used to match
   ``exchange`` first and file an Alpaca-sourced Instrument under
   ``source=NASDAQ``; the ordering was fixed with the ``source=`` unification,
   so core and this one now agree for every equity record. This one still never
   consults anything but ``provider``, which makes the guarantee structural
   rather than a property of core's tuple order.

2. **Field/partition name collisions.** ``ShortVolume`` and ``MacroSeries``
   have their own ``date`` field (the business date, "2026-06-18"), and core's
   ``row["date"] = _date_from_ns(...)`` overwrites it with the partition date
   derived from ``local_ts``. The business date is then gone from the row, not
   merely unreadable. This one moves it to ``date_val`` first. The same guard
   moves ``Instrument.exchange`` to ``exchange_name``; that name is what legacy
   equity lakes have on disk, so the rename is kept for read compatibility even
   though the partition key is no longer called ``exchange``.

   This divergence is still live and it is load-bearing:
   :class:`~crocodile.core.store.parquet_sink.ParquetSink` flattens with the
   *core* ``to_row``, so ``ShortVolume``, ``MacroSeries`` and ``Instrument``
   written through the sink land with a null ``date_val`` / ``exchange_name``
   and do not round-trip. The other 23 equity channels do. The fix belongs in
   core's ``to_row`` (move any record field that collides with a partition
   column aside before writing the partition columns — a family-agnostic rule);
   the sink cannot recover a value that was already overwritten by the time it
   sees the row.

The partition key itself is ``source``, per the Task 14 unification — the fork
wrote ``exchange={provider}``, and ``migrate_lake`` renames those directories.
Date and bucket arithmetic is imported from core rather than reimplemented, so
the two flatteners cannot drift on the values that decide file placement.
"""

from __future__ import annotations

from typing import Any

import msgspec.structs

from crocodile.core.store.rows import _convert_value, _date_from_ns, _symbol_bucket
from crocodile.equity.schema.enums import (
    CorpActionType,
    FundPeriod,
    OptType,
    SecurityType,
    Side,
    Tape,
)
from crocodile.equity.schema.records import (
    OHLCV,
    Auction,
    BalanceCorrection,
    Bar,
    BookDelta,
    BookSnapshot,
    CorporateAction,
    DepthProfile,
    Filing,
    Fundamental,
    Holding13F,
    IndexValue,
    InsiderTransaction,
    Instrument,
    LimitOrderFill,
    LiquidationCall,
    MacroSeries,
    OptionQuote,
    PoRUpdate,
    Quote,
    Record,
    ReserveDataUpdated,
    ShortInterest,
    ShortVolume,
    Trade,
    TradingStatus,
)

__all__ = ["from_row", "to_row"]


def to_row(record: Record) -> dict[str, Any]:
    """Flatten an equity Record Struct into a dict ready for Polars / Parquet.

    Added partition columns:
        - ``source``  : the record's ``provider``
        - ``channel`` : the msgspec tag string (e.g. "trade")
        - ``date``    : UTC date from ``local_ts`` (e.g. "2023-11-14")
        - ``bucket``  : hash(symbol) % 128

    Enum fields (``tape``, ``type``, ...) are converted to their string values.
    List-of-tuple fields (``bids``, ``asks``) are preserved as Python
    ``list[tuple[float, float]]`` — Polars can infer these as list[struct].

    Two record fields are renamed out of the way of the partition columns
    before those are added; see the module docstring.
    """
    # Extract channel tag from the struct class metadata
    channel: str = type(record).__struct_config__.tag  # type: ignore[assignment]

    # Build the base dict from struct fields
    raw = msgspec.structs.asdict(record)

    # Coerce enum values to primitives
    row: dict[str, Any] = {k: _convert_value(v) for k, v in raw.items()}

    if channel == "instrument" and "exchange" in row:
        row["exchange_name"] = row.pop("exchange")

    if channel in ("short_volume", "macro_series") and "date" in row:
        row["date_val"] = row.pop("date")

    # Add partition columns. Every equity record names its origin ``provider``;
    # ``Instrument.exchange`` is a listing venue, not a data source, and must
    # not be mistaken for one.
    row["source"] = raw["provider"]
    row["channel"] = channel
    row["date"] = _date_from_ns(record.local_ts)
    row["bucket"] = _symbol_bucket(record.symbol)

    return row


# Partition-only columns added by to_row / hive layout — not Record fields.
_PARTITION_COLS = frozenset({"source", "channel", "date", "bucket"})


_LEVEL_SIZE_KEYS = ("size", "amount")
"""How the two lakes spell the second element of a book level, ours first.

The forks disagreed — crypto writes ``list[struct{price, amount}]``, equity
``list[struct{price, size}]`` — and the merged
:class:`~crocodile.core.store.parquet_sink.ParquetSink` keeps both rather than
picking one: ``migrate_lake`` renames partition directories without rewriting a
single Parquet file, so the parts already on disk keep their fork's struct and
anything appended beside them has to match. The sink therefore writes ``size``
into ``source=`` partitions holding equity records, which is what this reader
expects first. ``amount`` stays accepted because a book that came from the
crypto side is still readable as (price, size) — unlike
``crocodile.core.store.rows._coerce_levels_from_row``, which reads
``item["amount"]`` outright and raises ``KeyError`` on an equity-written level.
"""


def _coerce_levels_from_row(raw: Any) -> list[tuple[float, float]]:
    """Convert list-of-dicts or list-of-tuples book levels to list[tuple[float, float]].

    Raises:
        KeyError: if a level dict names its size under neither spelling, or
            leaves it null. Both used to become ``0.0``, and ``0.0`` is not a
            missing value in this protocol — it is the canonical *removal* of
            that price level, so an unreadable level silently deleted a real
            one from the reconstructed book.
    """
    if not raw:
        return []
    result: list[tuple[float, float]] = []
    for item in raw:
        if isinstance(item, dict):
            size_val = next((item[k] for k in _LEVEL_SIZE_KEYS if item.get(k) is not None), None)
            if size_val is None:
                raise KeyError(
                    f"book level {item!r} has no non-null size under any of "
                    f"{list(_LEVEL_SIZE_KEYS)}; defaulting it to 0.0 would read as a "
                    f"level removal"
                )
            result.append((float(item["price"]), float(size_val)))
        else:
            result.append((float(item[0]), float(item[1])))
    return result


def from_row(row: dict[str, Any]) -> Record:
    """Reconstruct an equity Record from a flat dict (e.g., read from Parquet)."""
    channel = row["channel"]
    d: dict[str, Any] = {k: v for k, v in row.items() if k not in _PARTITION_COLS}

    if channel == "trade":
        return Trade(
            provider=d["provider"],
            symbol=d["symbol"],
            symbol_raw=d["symbol_raw"],
            source_ts=d.get("source_ts"),
            local_ts=int(d["local_ts"]),
            id=str(d["id"]),
            price=float(d["price"]),
            size=float(d["size"]),
            conditions=d.get("conditions"),
            tape=Tape(d["tape"]) if d.get("tape") else None,
            venue=d.get("venue"),
        )
    if channel == "quote":
        return Quote(
            provider=d["provider"],
            symbol=d["symbol"],
            symbol_raw=d["symbol_raw"],
            source_ts=d.get("source_ts"),
            local_ts=int(d["local_ts"]),
            bid_px=float(d["bid_px"]),
            bid_sz=float(d["bid_sz"]),
            ask_px=float(d["ask_px"]),
            ask_sz=float(d["ask_sz"]),
            is_nbbo=bool(d.get("is_nbbo", False)),
            is_consolidated=bool(d.get("is_consolidated", False)),
            conditions=d.get("conditions"),
            tape=Tape(d["tape"]) if d.get("tape") else None,
        )
    if channel == "book_snapshot":
        return BookSnapshot(
            provider=d["provider"],
            symbol=d["symbol"],
            symbol_raw=d["symbol_raw"],
            source_ts=d.get("source_ts"),
            local_ts=int(d["local_ts"]),
            bids=_coerce_levels_from_row(d.get("bids", [])),
            asks=_coerce_levels_from_row(d.get("asks", [])),
            depth=int(d["depth"]),
            sequence_id=d.get("sequence_id"),
            is_snapshot=bool(d["is_snapshot"]) if d.get("is_snapshot") is not None else True,
        )
    if channel == "book_delta":
        return BookDelta(
            provider=d["provider"],
            symbol=d["symbol"],
            symbol_raw=d["symbol_raw"],
            source_ts=d.get("source_ts"),
            local_ts=int(d["local_ts"]),
            bids=_coerce_levels_from_row(d.get("bids", [])),
            asks=_coerce_levels_from_row(d.get("asks", [])),
            seq_id=d.get("seq_id"),
            prev_seq_id=d.get("prev_seq_id"),
            is_snapshot=bool(d["is_snapshot"]) if d.get("is_snapshot") is not None else False,
        )
    if channel == "depth":
        return DepthProfile(
            provider=d["provider"],
            symbol=d["symbol"],
            symbol_raw=d["symbol_raw"],
            local_ts=int(d["local_ts"]),
            bids=_coerce_levels_from_row(d.get("bids", [])),
            asks=_coerce_levels_from_row(d.get("asks", [])),
            reference_price=float(d["reference_price"]),
            basis=d["basis"],
            is_synthetic=bool(d["is_synthetic"]),
            depth=int(d["depth"]),
            source_ts=(int(d["source_ts"]) if d.get("source_ts") is not None else None),
        )
    if channel == "corp_action":
        return CorporateAction(
            provider=d["provider"],
            symbol=d["symbol"],
            symbol_raw=d["symbol_raw"],
            source_ts=d.get("source_ts"),
            local_ts=int(d["local_ts"]),
            ex_date=str(d["ex_date"]),
            type=CorpActionType(d["type"]),
            value=float(d["value"]),
        )
    if channel == "bar":
        return Bar(
            provider=d["provider"],
            symbol=d["symbol"],
            symbol_raw=d["symbol_raw"],
            source_ts=d.get("source_ts"),
            local_ts=int(d["local_ts"]),
            interval=str(d["interval"]),
            open=float(d["open"]),
            high=float(d["high"]),
            low=float(d["low"]),
            close=float(d["close"]),
            volume=float(d["volume"]),
            vwap=d.get("vwap"),
            trade_count=d.get("trade_count"),
        )
    if channel == "fundamental":
        return Fundamental(
            provider=d["provider"],
            symbol=d["symbol"],
            symbol_raw=d["symbol_raw"],
            source_ts=d.get("source_ts"),
            local_ts=int(d["local_ts"]),
            taxonomy=str(d["taxonomy"]) if d.get("taxonomy") is not None else None,  # type: ignore[arg-type]
            tag=str(d["tag"]) if d.get("tag") is not None else None,  # type: ignore[arg-type]
            unit=str(d["unit"]) if d.get("unit") is not None else None,  # type: ignore[arg-type]
            val=float(d["val"]) if d.get("val") is not None else None,  # type: ignore[arg-type]
            end=str(d["end"]) if d.get("end") is not None else None,  # type: ignore[arg-type]
            start=d.get("start"),
            fy=int(d["fy"]) if d.get("fy") is not None else None,
            fp=FundPeriod(d["fp"]) if d.get("fp") else None,
            form=d.get("form"),
            filed=d.get("filed"),
            accn=d.get("accn"),
            frame=d.get("frame"),
        )
    if channel == "filing":
        return Filing(
            provider=d["provider"],
            symbol=d["symbol"],
            symbol_raw=d["symbol_raw"],
            source_ts=d.get("source_ts"),
            local_ts=int(d["local_ts"]),
            accession_number=d["accession_number"],
            form=d["form"],
            filing_date=d["filing_date"],
            primary_document=d["primary_document"],
            document_url=d["document_url"],
            report_date=d.get("report_date"),
            is_xbrl=bool(d["is_xbrl"]) if d.get("is_xbrl") is not None else None,
        )
    if channel == "ohlcv":
        return OHLCV(
            provider=d["provider"],
            symbol=d["symbol"],
            symbol_raw=d["symbol_raw"],
            source_ts=d.get("source_ts"),
            local_ts=int(d["local_ts"]),
            interval=str(d["interval"]),
            open=float(d["open"]),
            high=float(d["high"]),
            low=float(d["low"]),
            close=float(d["close"]),
            volume=float(d["volume"]),
            vwap=d.get("vwap"),
            trade_count=d.get("trade_count"),
        )
    if channel == "index_value":
        return IndexValue(
            provider=d["provider"],
            symbol=d["symbol"],
            symbol_raw=d["symbol_raw"],
            source_ts=d.get("source_ts"),
            local_ts=int(d["local_ts"]),
            value=float(d["value"]),
        )
    if channel == "auction":
        return Auction(
            provider=d["provider"],
            symbol=d["symbol"],
            symbol_raw=d["symbol_raw"],
            source_ts=d.get("source_ts"),
            local_ts=int(d["local_ts"]),
            paired_shares=(
                float(d["paired_shares"]) if d.get("paired_shares") is not None else None
            ),
            imbalance_shares=(
                float(d["imbalance_shares"]) if d.get("imbalance_shares") is not None else None
            ),
            imbalance_side=Side(d["imbalance_side"]) if d.get("imbalance_side") else None,
            reference_price=(
                float(d["reference_price"]) if d.get("reference_price") is not None else None
            ),
            indicative_price=(
                float(d["indicative_price"]) if d.get("indicative_price") is not None else None
            ),
            auction_type=d.get("auction_type"),
        )
    if channel == "trading_status":
        return TradingStatus(
            provider=d["provider"],
            symbol=d["symbol"],
            symbol_raw=d["symbol_raw"],
            source_ts=d.get("source_ts"),
            local_ts=int(d["local_ts"]),
            status=str(d["status"]),
            reason=d.get("reason"),
            limit_up_price=(
                float(d["limit_up_price"]) if d.get("limit_up_price") is not None else None
            ),
            limit_down_price=(
                float(d["limit_down_price"]) if d.get("limit_down_price") is not None else None
            ),
            indicator=d.get("indicator"),
        )
    if channel == "instrument":
        return Instrument(
            provider=d["provider"],
            symbol=d["symbol"],
            symbol_raw=d["symbol_raw"],
            source_ts=d.get("source_ts"),
            local_ts=int(d["local_ts"]),
            name=d.get("name"),
            cik=d.get("cik"),
            figi=d.get("figi"),
            composite_figi=d.get("composite_figi"),
            share_class_figi=d.get("share_class_figi"),
            cusip=d.get("cusip"),
            # Three spellings reach this field. Legacy equity lakes and this
            # module's own `to_row` write `exchange_name`, because under the old
            # layout a plain `exchange` column collided with the `exchange=`
            # partition key. That key is now `source=`, so the sink — which
            # flattens with core's `to_row` — writes the field under its real
            # name. Reading both is what lets one reader serve files written
            # before and after the merge.
            exchange=d.get("exchange_name") or d.get("exchange"),
            security_type=SecurityType(d["security_type"]) if d.get("security_type") else None,
            sic=d.get("sic"),
            shares_outstanding=(
                int(d["shares_outstanding"]) if d.get("shares_outstanding") is not None else None
            ),
            listing_date=d.get("listing_date"),
            status=d.get("status"),
        )
    if channel == "insider":
        return InsiderTransaction(
            provider=d["provider"],
            symbol=d["symbol"],
            symbol_raw=d["symbol_raw"],
            source_ts=d.get("source_ts"),
            local_ts=int(d["local_ts"]),
            insider_name=str(d["insider_name"]),
            position=str(d["position"]),
            transaction_type=str(d["transaction_type"]),
            transaction_date=str(d["transaction_date"]),
            shares=float(d["shares"]) if d.get("shares") is not None else None,
            price=float(d["price"]) if d.get("price") is not None else None,
            value=float(d["value"]) if d.get("value") is not None else None,
            ownership=d.get("ownership"),
        )
    if channel == "holding_13f":
        return Holding13F(
            provider=d["provider"],
            symbol=d["symbol"],
            symbol_raw=d["symbol_raw"],
            source_ts=d.get("source_ts"),
            local_ts=int(d["local_ts"]),
            manager_name=str(d["manager_name"]),
            issuer_name=str(d["issuer_name"]),
            cusip=str(d["cusip"]),
            value=float(d["value"]),
            shares=float(d["shares"]),
            shares_type=str(d["shares_type"]),
            discretion=d.get("discretion"),
            voting_sole=float(d["voting_sole"]) if d.get("voting_sole") is not None else None,
            voting_shared=float(d["voting_shared"]) if d.get("voting_shared") is not None else None,
            voting_none=float(d["voting_none"]) if d.get("voting_none") is not None else None,
            report_date=d.get("report_date"),
            accession_number=d.get("accession_number"),
        )
    if channel == "short_interest":
        return ShortInterest(
            provider=d["provider"],
            symbol=d["symbol"],
            symbol_raw=d["symbol_raw"],
            source_ts=d.get("source_ts"),
            local_ts=int(d["local_ts"]),
            settlement_date=str(d["settlement_date"]),
            short_interest=float(d["short_interest"]),
            prev_short_interest=(
                float(d["prev_short_interest"])
                if d.get("prev_short_interest") is not None
                else None
            ),
            days_to_cover=float(d["days_to_cover"]) if d.get("days_to_cover") is not None else None,
            change_pct=float(d["change_pct"]) if d.get("change_pct") is not None else None,
        )
    if channel == "short_volume":
        return ShortVolume(
            provider=d["provider"],
            symbol=d["symbol"],
            symbol_raw=d["symbol_raw"],
            source_ts=d.get("source_ts"),
            local_ts=int(d["local_ts"]),
            date=str(d["date_val"]),
            short_volume=float(d["short_volume"]),
            short_exempt_volume=(
                float(d["short_exempt_volume"])
                if d.get("short_exempt_volume") is not None
                else None
            ),
            total_volume=float(d["total_volume"]),
        )
    if channel == "option_quote":
        return OptionQuote(
            provider=d["provider"],
            symbol=d["symbol"],
            symbol_raw=d["symbol_raw"],
            source_ts=d.get("source_ts"),
            local_ts=int(d["local_ts"]),
            underlying=str(d["underlying"]),
            expiry=str(d["expiry"]),
            strike=float(d["strike"]),
            type=OptType(d["type"]),
            bid=float(d["bid"]) if d.get("bid") is not None else None,
            ask=float(d["ask"]) if d.get("ask") is not None else None,
            last=float(d["last"]) if d.get("last") is not None else None,
            volume=float(d["volume"]) if d.get("volume") is not None else None,
            open_interest=float(d["open_interest"]) if d.get("open_interest") is not None else None,
            implied_volatility=(
                float(d["implied_volatility"]) if d.get("implied_volatility") is not None else None
            ),
            delta=float(d["delta"]) if d.get("delta") is not None else None,
            gamma=float(d["gamma"]) if d.get("gamma") is not None else None,
            vega=float(d["vega"]) if d.get("vega") is not None else None,
            theta=float(d["theta"]) if d.get("theta") is not None else None,
            rho=float(d["rho"]) if d.get("rho") is not None else None,
        )
    if channel == "macro_series":
        return MacroSeries(
            provider=d["provider"],
            symbol=d["symbol"],
            symbol_raw=d["symbol_raw"],
            source_ts=d.get("source_ts"),
            local_ts=int(d["local_ts"]),
            date=str(d["date_val"]),
            value=float(d["value"]) if d.get("value") is not None else None,
            realtime_start=d.get("realtime_start"),
            realtime_end=d.get("realtime_end"),
        )
    if channel == "reserve_data_updated":
        return ReserveDataUpdated(
            provider=d["provider"],
            symbol=d["symbol"],
            symbol_raw=d["symbol_raw"],
            local_ts=int(d["local_ts"]),
            source_ts=d.get("source_ts"),
            exchange_ts=d.get("exchange_ts"),
            reserve=d.get("reserve"),
            liquidity_rate=(
                float(d["liquidity_rate"]) if d.get("liquidity_rate") is not None else None
            ),
            stable_borrow_rate=(
                float(d["stable_borrow_rate"]) if d.get("stable_borrow_rate") is not None else None
            ),
            variable_borrow_rate=(
                float(d["variable_borrow_rate"])
                if d.get("variable_borrow_rate") is not None
                else None
            ),
            liquidity_index=(
                int(d["liquidity_index"]) if d.get("liquidity_index") is not None else None
            ),
            variable_borrow_index=(
                int(d["variable_borrow_index"])
                if d.get("variable_borrow_index") is not None
                else None
            ),
        )
    if channel == "liquidation_call":
        return LiquidationCall(
            provider=d["provider"],
            symbol=d["symbol"],
            symbol_raw=d["symbol_raw"],
            local_ts=int(d["local_ts"]),
            source_ts=d.get("source_ts"),
            exchange_ts=d.get("exchange_ts"),
            collateral_asset=d.get("collateral_asset"),
            debt_asset=d.get("debt_asset"),
            user=d.get("user"),
            debt_to_cover=(
                float(d["debt_to_cover"]) if d.get("debt_to_cover") is not None else None
            ),
            liquidated_collateral_amount=(
                float(d["liquidated_collateral_amount"])
                if d.get("liquidated_collateral_amount") is not None
                else None
            ),
            liquidator=d.get("liquidator"),
            receive_a_token=(
                bool(d["receive_a_token"]) if d.get("receive_a_token") is not None else None
            ),
        )
    if channel == "limit_order_fill":
        return LimitOrderFill(
            provider=d["provider"],
            symbol=d["symbol"],
            symbol_raw=d["symbol_raw"],
            local_ts=int(d["local_ts"]),
            source_ts=d.get("source_ts"),
            exchange_ts=d.get("exchange_ts"),
            tx_hash=d.get("tx_hash"),
            log_index=int(d["log_index"]) if d.get("log_index") is not None else None,
            protocol=d.get("protocol"),
            maker=d.get("maker"),
            taker=d.get("taker"),
            maker_token=d.get("maker_token"),
            taker_token=d.get("taker_token"),
            maker_amount=(float(d["maker_amount"]) if d.get("maker_amount") is not None else None),
            taker_amount=(float(d["taker_amount"]) if d.get("taker_amount") is not None else None),
            order_hash=d.get("order_hash"),
        )
    if channel == "balance_correction":
        return BalanceCorrection(
            provider=d["provider"],
            symbol=d["symbol"],
            symbol_raw=d["symbol_raw"],
            exchange_ts=d.get("exchange_ts"),
            local_ts=int(d["local_ts"]),
            holder_address=str(d["holder_address"]),
            token_address=str(d["token_address"]),
            local_balance=float(d["local_balance"]),
            onchain_balance=float(d["onchain_balance"]),
            correction_amount=float(d["correction_amount"]),
            source_ts=d.get("source_ts"),
        )
    if channel == "por_update":
        return PoRUpdate(
            provider=d["provider"],
            symbol=d["symbol"],
            symbol_raw=d["symbol_raw"],
            exchange_ts=int(d["exchange_ts"]),
            local_ts=int(d["local_ts"]),
            feed_address=str(d["feed_address"]),
            token_address=str(d["token_address"]),
            reserves=float(d["reserves"]),
            total_supply=float(d["total_supply"]),
            backing_ratio=float(d["backing_ratio"]),
            is_backed=bool(d["is_backed"]),
            source_ts=d.get("source_ts"),
        )
    raise ValueError(f"Unknown channel tag: {channel!r}")
