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
import types
import typing
from collections.abc import Callable
from typing import Any, cast

import mmh3
import msgspec.structs

from crocodile.core.schema.enums import (
    AssetClass,
    Channel,
    Side,
    successor_channel,
)
from crocodile.core.schema.provenance import Provenance, level_for, registered_bases
from crocodile.core.schema.records import Record, _Header

Flattenable = Record
"""What :func:`to_row` accepts.

``to_row`` reads only the msgspec tag plus ``local_ts`` and ``symbol``, so it
flattens any record-shaped struct — including the legacy equity union, which
:mod:`crocodile.equity.store.rows` still hands it. The annotation names the
canonical union because that is the only family ``core`` may depend on by name.
"""


# ---------------------------------------------------------------------------
# Which record union a row came from
# ---------------------------------------------------------------------------
FAMILY_CRYPTO = "crypto"
FAMILY_EQUITY = "equity"
FAMILY_CANONICAL = "canonical"

# Row field → family, most specific first. One table, read by three callers:
# ``to_row``'s partition key, ``_header``'s asset class, and
# ``parquet_sink._row_family``'s schema selection. They are the same question
# — which fork wrote this — and a second copy of this order is a second place
# for it to be got wrong.
#
# ``provider`` is checked before ``exchange`` and the order is load-bearing.
# Equity's ``Instrument`` carries BOTH: ``provider`` is who served the data,
# ``exchange`` is where the security is *listed*. Matching ``exchange`` first
# filed an Alpaca-sourced instrument under ``source=NASDAQ`` — no error, just
# the wrong directory. The canonical ``Instrument`` inherited that ``exchange``
# field, so the hazard did not retire with the crypto union.
_FAMILY_MARKERS: tuple[tuple[str, str], ...] = (
    ("asset_class", FAMILY_CANONICAL),
    ("provider", FAMILY_EQUITY),
    ("exchange", FAMILY_CRYPTO),
)

_LEGACY_FAMILY_ASSET_CLASS: dict[str, AssetClass] = {
    FAMILY_EQUITY: AssetClass.EQUITY,
    FAMILY_CRYPTO: AssetClass.CRYPTO,
}
"""The market each *legacy* family wrote, for rows that carry no ``asset_class``.

Only the two retired unions appear: a canonical row states its market outright,
so ``FAMILY_CANONICAL`` has no entry and its absence is what makes
:func:`_asset_class_from_legacy_marker` refuse to answer for one.
"""

# What each record family calls the place the data came from, most canonical
# first. The canonical header says ``source``; the legacy equity union, which is
# still live, says ``provider``, and the retired crypto union said ``exchange``.
# Derived from the marker table above so the precedence lives in one place;
# ``source`` leads because the canonical header names its origin outright while
# its *family* marker is ``asset_class``, which is the one row the two differ on.
_ORIGIN_FIELDS: tuple[str, ...] = (
    "source",
    *(marker for marker, family in _FAMILY_MARKERS if family != FAMILY_CANONICAL),
)

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


_LEVEL_SIZE_KEYS = ("amount", "size")
"""How the two lakes spell the second element of a book level, canonical first.

The forks disagreed — the canonical schema writes ``list[struct{price, amount}]``
and the equity fork wrote ``list[struct{price, size}]`` — and ``migrate_lake``
renames partition directories without rewriting a Parquet file, so both shapes
sit in lakes that are read today. Reading only ``amount`` raised ``KeyError`` on
every equity-written level, which is why the equity dialect needed its own reader.

A level whose size is null is refused rather than defaulted: ``0.0`` is not a
missing value in this protocol, it is the canonical *removal* of that price
level, so an unreadable level would silently delete a real one from the
reconstructed book.
"""


def _coerce_levels_from_row(raw: Any) -> list[tuple[float, float]]:
    """Convert list-of-dicts or list-of-tuples book levels to list[tuple[float, float]].

    When read back from Parquet via Polars, book levels arrive as a list of
    dicts ``[{"price": ..., "amount": ...}, ...]`` — or ``{"price", "size"}`` from
    a pre-migration equity file. Both convert to the canonical
    ``Level = tuple[float, float]`` form.

    Raises:
        KeyError: if a level dict names its size under neither spelling, or
            leaves it null. See :data:`_LEVEL_SIZE_KEYS`.
    """
    if not raw:
        return []
    result: list[tuple[float, float]] = []
    for item in raw:
        if isinstance(item, dict):
            size = next((item[k] for k in _LEVEL_SIZE_KEYS if item.get(k) is not None), None)
            if size is None:
                raise KeyError(
                    f"book level {item!r} has no non-null size under any of "
                    f"{list(_LEVEL_SIZE_KEYS)}; defaulting it to 0.0 would read as a "
                    f"level removal"
                )
            result.append((float(item["price"]), float(size)))
        else:
            # Already a tuple/list of two numbers
            result.append((float(item[0]), float(item[1])))
    return result


def _row_family(d: dict[str, Any]) -> str:
    """Return which record union wrote this row, by the **value** of its marker.

    Walks :data:`_FAMILY_MARKERS` in its declared order. Both halves matter and
    each fixes a real misread:

    * *Order.* ``provider`` is consulted before ``exchange``. The equity file
      schema declares an ``exchange`` column, so matching ``exchange`` first
      stamped every equity row ``CRYPTO`` — silently, because an ``ohlcv`` row's
      field names overlap enough between the two forks that nothing else raised.
    * *Value, not key.* That equity ``exchange`` column is vestigial and always
      null; it exists only so new parts match the parts already beside them.
      ``ParquetSink`` materialises every schema key as a column, so the key is
      present on every equity file ever written and presence discriminates
      nothing. The same is true in reverse for a lake holding pre- and
      post-merge files under one source: ``read_parquet`` unions the schemas, so
      the pre-merge rows arrive carrying ``asset_class`` as a null and the
      canonical rows carrying ``exchange`` as one. Only the values separate them.

    ``parquet_sink._row_family`` asks the same question of a row on its way *out*
    and can settle it on key presence, because it is looking at a dict ``to_row``
    just built rather than at a union of two file schemas.

    Raises:
        KeyError: if no marker carries a value. A family guessed here picks the
            column dialect the rest of the read is conducted in, so guessing it
            would mistranslate every field rather than fail.
    """
    for marker, family in _FAMILY_MARKERS:
        if d.get(marker) is not None:
            return family
    raise KeyError(
        f"row names its origin under none of {[m for m, _ in _FAMILY_MARKERS]}; "
        f"neither its market nor its column dialect can be established "
        f"(symbol={d.get('symbol')!r}, local_ts={d.get('local_ts')!r})"
    )


def _asset_class_from_legacy_marker(d: dict[str, Any]) -> AssetClass:
    """Read the market off a row that predates the canonical ``asset_class`` field.

    Raises:
        KeyError: if the family is the canonical one, which is unreachable for a
            row with no ``asset_class``, or if no marker carries a value. An
            asset class invented here is indistinguishable from one recorded.
    """
    asset_class = _LEGACY_FAMILY_ASSET_CLASS.get(_row_family(d))
    if asset_class is None:
        raise KeyError(
            f"row carries no 'asset_class' and none of the pre-migration origin "
            f"columns {sorted(_LEGACY_FAMILY_ASSET_CLASS)} hold a value; "
            f"its market cannot be established"
        )
    return asset_class


# ---------------------------------------------------------------------------
# The legacy equity dialect
# ---------------------------------------------------------------------------
# The equity fork spelled nine shared tags differently, and ``migrate_lake``
# renames directories rather than rewriting files, so those spellings never age
# out. ``crocodile.equity.store.rows.from_row`` reads them correctly and lost its
# last production caller, which is how a working capability became an unreachable
# one. The tables below move that knowledge into the reader everything calls, so
# one reader speaks both dialects and the family decides which.

_EQUITY_COLUMN_ALIASES: dict[str, dict[str, str]] = {
    Channel.TRADE.value: {"size": "amount"},
    Channel.OHLCV.value: {"trade_count": "num_trades"},
    Channel.INSTRUMENT.value: {"exchange_name": "exchange"},
    Channel.OPTIONS_CHAIN.value: {
        "type": "opt_type",
        "bid": "bid_px",
        "ask": "ask_px",
        "last": "last_price",
        "implied_volatility": "mark_iv",
    },
}
"""Legacy equity column → canonical field, keyed by the **canonical** channel tag.

Applied after :func:`successor_channel`, so ``bar`` rows are already ``ohlcv`` and
``option_quote`` rows already ``options_chain`` by the time these are consulted.

``implied_volatility`` becomes ``mark_iv`` on the argument
:class:`~crocodile.core.schema.records.OptionsChain` already makes: a feed that
publishes a single IV per contract is publishing the mark.
"""

_EQUITY_ABSENT_FIELD_VALUES: dict[str, dict[str, Any]] = {
    Channel.TRADE.value: {"side": Side.UNKNOWN.value},
    Channel.OPTIONS_CHAIN.value: {"underlying_price": None},
}
"""Canonical fields no legacy equity column holds, and what the fork's own adapters write.

Neither entry is a guess about the observation; both are facts about the fork:

``Trade.side`` — the legacy equity ``Trade`` struct had no ``side`` field at all,
because a consolidated-tape print does not classify the aggressor. Every equity
adapter in the tree writes ``Side.UNKNOWN`` for the same prints today, which is
the claim the canonical record reserves for exactly this case.

``OptionsChain.underlying_price`` — required with no default and legitimately
``None``; the legacy ``OptionQuote`` carried no such column, and the field's own
contract is that ``None`` means the source published none. It has to be passed
explicitly because a required field absent from the row is a ``TypeError``, not
a default.
"""


def _legacy_expiry_to_ns(value: Any) -> int:
    """Convert the legacy ``YYYY-MM-DD`` option expiry to UTC epoch nanoseconds.

    ``OptionsChain.expiry`` is nanoseconds because a date cannot express the
    intraday expiry of a 0DTE. Midnight UTC is the only reading a bare date
    supports and it is what the fork's own dates meant; the conversion is total
    in this direction, which is the direction that must not lose anything.
    """
    if isinstance(value, int):
        return value
    date = datetime.datetime.strptime(str(value), "%Y-%m-%d").replace(tzinfo=datetime.UTC)
    return int(date.timestamp()) * 1_000_000_000


_EQUITY_VALUE_CONVERSIONS: dict[str, dict[str, Callable[[Any], Any]]] = {
    Channel.OPTIONS_CHAIN.value: {"expiry": _legacy_expiry_to_ns},
}
"""Legacy equity values whose *type* changed, not just their name."""

_LEGACY_DEPTH_PROVENANCE_COLUMNS = ("basis", "is_synthetic")
"""The equity fork's provenance prototype, which the four-field tail generalised.

Their presence is why the old ``_header`` docstring — "the fork only ever wrote
venue-reported records" — was false: the equity fork wrote synthetic
volume-at-price depth profiles and recorded it in these two columns. Reading them
is the difference between a legacy synthetic profile that says so and one that
reads back as NATIVE with a confidence of 1.0.
"""

_UNMEASURED_LEGACY_CONFIDENCE = 0.0
"""What a pre-migration row's confidence is, given it recorded no measurement.

The fork stored the *method* (``basis``) and the *level* (``is_synthetic``) but
never a sampling number, so there is nothing on disk to compute one from. 1.0
would be the claim that the profile is as well sampled as this method can make
it — a legacy profile would then outrank every live one, including the measured
three-bar profile that scores 0.0077. 0.0 is the reading ``unavailable`` already
carries for the same situation: no sampling evidence survives. It is a statement
about the file, not about the profile, and ``prov`` is what says which level the
profile is at.
"""


def _translate_legacy_equity(channel: str, d: dict[str, Any]) -> None:
    """Rewrite one legacy equity row in place into the canonical column dialect.

    ``channel`` is the canonical tag, already resolved through
    :func:`successor_channel`.

    A legacy column wins only where it carries a value. That matters for a lake
    spanning the migration: ``union_by_name`` gives a pre-migration row the
    canonical columns as nulls and a post-migration row the legacy ones, so both
    spellings are present on every row and only the values separate them.
    """
    for legacy, canonical in _EQUITY_COLUMN_ALIASES.get(channel, {}).items():
        value = d.pop(legacy, None)
        if value is not None:
            d[canonical] = value
    for field, convert in _EQUITY_VALUE_CONVERSIONS.get(channel, {}).items():
        if d.get(field) is not None:
            d[field] = convert(d[field])
    for field, fallback in _EQUITY_ABSENT_FIELD_VALUES.get(channel, {}).items():
        if d.get(field) is None:
            d[field] = fallback


def _legacy_provenance(d: dict[str, Any], family: str) -> tuple[Provenance, str, float]:
    """Return the ``(prov, basis, confidence)`` a pre-migration row states about itself.

    The crypto fork's records carried no provenance concept and no method that
    could produce anything but a venue reading, so NATIVE is the truth about
    those files. The equity fork's did not: its ``DepthProfile`` carried ``basis``
    and ``is_synthetic``, the prototype this tail generalised, and defaulting
    those rows to NATIVE reported a modelled volume-at-price ladder as something
    the venue published.

    Raises:
        ValueError: if a row claims ``is_synthetic`` while recording no ``basis``.
            The surfaces are required to emit ``describe(basis)`` as a warning for
            every non-native record, so a synthetic row with no method named is one
            that would be served with an empty warning.
    """
    basis = d.get("basis")
    is_synthetic = d.get("is_synthetic")
    if family != FAMILY_EQUITY or basis is None:
        if is_synthetic:
            raise ValueError(
                f"row claims is_synthetic={is_synthetic!r} but records no 'basis'; "
                f"the method behind a synthetic record cannot be described "
                f"(symbol={d.get('symbol')!r}, local_ts={d.get('local_ts')!r})"
            )
        return Provenance.NATIVE, "native", 1.0

    basis = str(basis)
    if basis in registered_bases():
        level = level_for(basis)
    else:
        level = Provenance.SYNTHETIC if is_synthetic else Provenance.DERIVED
    return level, basis, _UNMEASURED_LEGACY_CONFIDENCE


def _header(d: dict[str, Any], family: str) -> dict[str, Any]:
    """Rebuild the ten canonical header fields from one flattened row.

    Two shapes reach this function and both have to work. Rows ``to_row`` writes
    today carry the canonical header. Rows already sitting in a crypto lake do
    not: they name the origin ``exchange`` / ``exchange_ts`` and have no
    ``asset_class`` and no provenance tail, because that is what the retired
    crypto union wrote. ``migrate_lake`` renames partition directories without
    reading a Parquet byte, so those files keep their columns forever and this
    is the only place the two spellings can be reconciled.

    The market comes from :func:`_row_family`, which reads the same marker table
    the sink picks a file schema with. ``source`` is resolved the same way, by
    value: on a bare Parquet read there is no ``source`` column at all — it is a
    path component — and the previous key-presence fallback turned an equity
    file's null ``exchange`` into the literal string ``'None'``.

    The provenance tail is absent from every pre-migration row, and what stands
    in for it is family-dependent; see :func:`_legacy_provenance`.
    """
    source = next((d[f] for f in _ORIGIN_FIELDS if d.get(f) is not None), None)
    if source is None:
        raise KeyError(
            f"row names its origin under none of {_ORIGIN_FIELDS}; it cannot say where it came from"
        )

    raw_class = d.get("asset_class")
    asset_class = (
        AssetClass(raw_class) if raw_class is not None else _asset_class_from_legacy_marker(d)
    )

    source_ts = d.get("source_ts")
    if source_ts is None:
        source_ts = d.get("exchange_ts")

    prov = d.get("prov")
    if prov is None:
        level, basis, confidence = _legacy_provenance(d, family)
    else:
        level = Provenance(prov)
        basis = d.get("prov_basis") or "native"
        raw_confidence = d.get("prov_confidence")
        confidence = float(raw_confidence) if raw_confidence is not None else 1.0
    return {
        "source": str(source),
        "symbol": str(d["symbol"]),
        "symbol_raw": str(d["symbol_raw"]),
        "local_ts": int(d["local_ts"]),
        "asset_class": asset_class,
        "source_ts": int(source_ts) if source_ts is not None else None,
        "prov": level,
        "prov_basis": basis,
        "prov_confidence": confidence,
        "prov_inputs": list(d.get("prov_inputs") or []),
    }


# ---------------------------------------------------------------------------
# Reading a record body back out of a row
# ---------------------------------------------------------------------------
# The sink derives its canonical file schema from the structs rather than
# hand-listing 30 of them, and says why: a second hand-written copy is a second
# place for a forgotten field to take a channel down at runtime. The reader had
# no such derivation. It was a chain of 17 hand-written arms covering 13 of the
# 30 channels the sink can write, and the other 17 raised ``Unknown channel
# tag`` on read — including ``limit_order_fill``, ``balance_correction`` and
# ``por_update``, which ``base_onchain`` emits live and whose payloads the sink
# had just stopped discarding. A ``base_onchain`` lake recorded data that
# ``CrypcodileClient.replay`` could not read back.
#
# So the reader is derived from the same union the writer is. Both directions
# now widen together, and a field added to a record needs no arm here at all.
_RECORD_BY_CHANNEL: dict[str, Any] = {
    struct.__struct_config__.tag: struct for struct in typing.get_args(Record)
}

_HEADER_FIELDS = frozenset(f.name for f in msgspec.structs.fields(_Header))

# Exact-type lookup, so ``bool`` cannot be caught by the ``int`` entry the way an
# ``issubclass`` chain would catch it. Mirrors ``parquet_sink._SCALAR_DTYPES``:
# the writer declares the column type, this reads the same annotation back.
_SCALAR_COERCIONS: dict[type, Callable[[Any], Any]] = {
    str: str,
    int: int,
    float: float,
    bool: bool,
}


def _coerce_field(annotation: Any, value: Any) -> Any:
    """Coerce one Parquet-read value back to the type its record field declares.

    Raises:
        TypeError: for an annotation with no rule. The writer refuses to invent a
            Parquet dtype for a field it does not understand; the reader refuses
            to invent a Python value for the same reason. Handing back the raw
            Polars value would put a dict where a struct field promised a float
            and let it travel.
    """
    if typing.get_origin(annotation) in (types.UnionType, typing.Union):
        members = [a for a in typing.get_args(annotation) if a is not type(None)]
        if len(members) == 1:
            annotation = members[0]

    if isinstance(annotation, type):
        # Enum before the scalar table: a StrEnum *is* a str subclass, and
        # ``to_row`` wrote it out as its value.
        if issubclass(annotation, enum.Enum):
            return annotation(value)
        coerce = _SCALAR_COERCIONS.get(annotation)
        if coerce is not None:
            return coerce(value)

    if typing.get_origin(annotation) is list:
        (inner,) = typing.get_args(annotation)
        # ``Level`` is ``tuple[float, float]``, which Parquet holds as a struct.
        # Checked, not assumed, for the reason the writer checks it: this is the
        # one branch that reads two named struct fields the annotation never
        # mentioned, so a three-element tuple would come back silently truncated.
        if typing.get_origin(inner) is tuple:
            if typing.get_args(inner) != (float, float):
                raise TypeError(
                    f"only tuple[float, float] is read back as a "
                    f"(price, amount) book level; {annotation!r} declares "
                    f"{typing.get_args(inner)!r}"
                )
            return _coerce_levels_from_row(value)
        return [_coerce_field(inner, item) for item in value]

    raise TypeError(f"no read rule for canonical record field annotation {annotation!r}")


def _admits_none(annotation: Any) -> bool:
    """Return whether ``None`` is a value the field's own annotation permits."""
    if annotation is type(None):
        return True
    if typing.get_origin(annotation) in (types.UnionType, typing.Union):
        return any(a is type(None) for a in typing.get_args(annotation))
    return False


def _record_body(struct: Any, d: dict[str, Any]) -> dict[str, Any]:
    """Rebuild everything below the header for one record class.

    A column that is absent, or present and null on a field that has a default,
    is left out so the struct's own default stands — that is how ``buy_volume``
    reads back as ``0.0`` and ``num_trades`` as ``None`` from the same null.

    A null on a field with no default is passed through only when the field's
    annotation admits it. ``OptionsChain.underlying_price`` is ``float | None``
    and required, so dropping it would turn "the venue published no underlying
    price" into a missing argument. ``Trade.amount`` is ``float`` and
    ``Trade.side`` is ``Side``, and msgspec does not type-check ``__init__`` —
    so the same passthrough handed back ``Trade(amount=None, side=None)``, which
    raises ``TypeError`` in the caller's ``sum()`` and ``AttributeError`` on
    ``rec.side.value``, both several frames from the reader that made them. It
    also breaks the contract ``side`` states outright: ``Side.UNKNOWN`` is how a
    record says the venue did not classify the aggressor, and ``None`` is not
    that value.

    Raises:
        ValueError: for a null on a required field whose annotation forbids it.
            Every such null is a dialect the family dispatch did not translate,
            and a record built around one travels further than the read that
            produced it.
    """
    body: dict[str, Any] = {}
    for field in msgspec.structs.fields(struct):
        if field.name in _HEADER_FIELDS:
            continue
        # ``to_row`` moves a record field that collides with a partition column
        # aside, so the column to read is the moved name (``ShortVolume.date``
        # is a settlement day; the partition ``date`` is the capture day).
        column = f"{field.name}_val" if field.name in _DERIVED_PARTITION_COLS else field.name
        if column not in d:
            continue
        value = d[column]
        if value is None:
            if not field.required:
                continue
            if _admits_none(field.type):
                body[field.name] = None
                continue
            raise ValueError(
                f"{struct.__name__}.{field.name} is required and cannot be None, but "
                f"column {column!r} is null on this row "
                f"(source={d.get('source')!r}, symbol={d.get('symbol')!r}, "
                f"local_ts={d.get('local_ts')!r}); "
                f"no known column dialect supplies it"
            )
        body[field.name] = _coerce_field(field.type, value)
    return body


def from_row(row: dict[str, Any]) -> Record:
    """Reconstruct a canonical Record from a flat dict (e.g., read from Parquet).

    **One reader, three dialects.** :func:`_row_family` decides which union wrote
    the row from the value of its origin marker, and that decides how the rest of
    the row is spelled. A canonical row is read as-is. A pre-migration crypto row
    calls its origin ``exchange`` and its timestamp ``exchange_ts`` and is
    otherwise field-identical. A pre-migration equity row spells a trade quantity
    ``size``, a book level ``{price, size}``, a bar's print count ``trade_count``,
    an instrument's listing venue ``exchange_name``, and tags a bar ``bar`` and an
    option chain ``option_quote``; :func:`_translate_legacy_equity` and
    :data:`~crocodile.core.schema.enums.CHANNEL_SUCCESSORS` move all of it onto the
    canonical names.

    That dispatch is the point. ``crocodile.equity.store.rows.from_row`` read the
    equity dialect correctly and lost its last production caller, so the capability
    was not deferred, it was disconnected — and the reader everything calls
    answered a legacy equity lake with dropped columns and null required fields
    instead of an error. Routing by family closes both at once.

    The ``channel`` tag is resolved against the same ``Record`` union
    :mod:`crocodile.core.store.parquet_sink` builds its file schema from, so every
    channel the sink can write can be read back. Fields are coerced from their
    declared annotations: partition-only columns (``date``, ``bucket``) are
    stripped, enums are rebuilt from their values, and book levels are converted
    from list-of-dicts back to ``list[tuple]``.

    Args:
        row: Flat dict as produced by ``to_row()`` or read from Parquet via
             ``df.to_dicts()``.

    Returns:
        A canonical Record instance.

    Raises:
        KeyError: if the row names its origin under no known marker, so its
            dialect cannot be established.
        ValueError: if the ``channel`` value is unrecognised, or a required field
            is null under every dialect this reader knows.
    """
    # Strip partition-only columns
    d: dict[str, Any] = {k: v for k, v in row.items() if k not in _PARTITION_COLS}
    family = _row_family(d)
    channel = successor_channel(row["channel"])
    struct = _RECORD_BY_CHANNEL.get(channel)
    if struct is None:
        raise ValueError(f"Unknown channel tag: {row['channel']!r}")
    if family == FAMILY_EQUITY:
        _translate_legacy_equity(channel, d)
    return cast("Record", struct(**_header(d, family), **_record_body(struct, d)))
