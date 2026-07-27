"""K-way merge replay engine.

Merges N pre-sorted per-(channel, symbol) iterators of Records into a single
globally time-ordered stream using heapq.merge.

Sort key (deterministic tie-break):
    (local_ts, source_ts_or_neg_inf, seq_or_0)

Where:
    - local_ts         — primary ordering key; monotonically increasing capture clock
    - source_ts_or_neg_inf — NULL source_ts is treated as -inf so it sorts
                             BEFORE any present source_ts at the same local_ts
    - seq_or_0         — seq_id / sequence_id / update_id (whichever is present)
                         falls back to 0 when absent; breaks remaining ties

Requirements:
    - Inputs MUST already be sorted by local_ts within each stream; heapq.merge
      silently produces wrong output if they are not (per the appendix warning).
    - Memory: O(k) — one buffered item per stream.
    - Time: O(N log k) where N = total records, k = number of streams.
"""

from __future__ import annotations

import heapq
from collections.abc import Iterable, Iterator
from typing import Any

from crocodile.core.schema.records import Record

# Sentinel for NULL source_ts — must be less than any real ns value.
# Real timestamps start around 1_000_000_000_000_000_000 ns (2001), so -1 is safely less.
_NEG_INF: int = -1

# The venue's own timestamp, under each record family's name for it. The
# canonical header and the legacy equity records both say ``source_ts``; only
# the retired crypto union said ``exchange_ts``, and it is read second so an
# instance decoded from a stream that predates the migration still orders
# correctly instead of silently collapsing to -inf.
_SOURCE_TS_FIELDS = ("source_ts", "exchange_ts")

# Sequence field per channel tag. Both families tag their records identically,
# so the tag is a safer discriminator here than the Python class.
_SEQ_FIELD_BY_TAG = {
    "book_delta": "seq_id",
    "book_snapshot": "sequence_id",
    "book_ticker": "update_id",
}

# Where the record came from, under each family's name for it. Same list, in the
# same order, as the store's partition key derivation — and the order is the
# load-bearing part. ``provider`` must be read before ``exchange`` because the
# equity ``Instrument`` carries both and they mean different things: who served
# the data versus where the security is listed. Reading ``exchange`` first tied
# an Alpaca-served record to NASDAQ, which is the same confusion
# :mod:`crocodile.core.store.rows` documents against the partition path.
_ORIGIN_FIELDS = ("source", "provider", "exchange")


def _sort_key(record: Record) -> tuple[int, int, int, str, str]:
    """Return the (local_ts, source_ts_or_neg_inf, seq_or_0) tuple for ordering.

    NULL source_ts → -1 (sorts BEFORE any real nanosecond timestamp).
    seq is extracted from whichever field is present on the record type:
        BookDelta   → seq_id
        BookSnapshot → sequence_id
        BookTicker  → update_id
        all others  → 0 (no sequence concept)
    """
    local_ts: int = record.local_ts

    source_ts: int = _NEG_INF
    for field in _SOURCE_TS_FIELDS:
        value = getattr(record, field, None)
        if value is not None:
            source_ts = value
            break

    seq: int
    tag = getattr(getattr(type(record), "__struct_config__", None), "tag", None)
    seq_field = _SEQ_FIELD_BY_TAG.get(tag) if isinstance(tag, str) else None
    if seq_field is not None:
        seq = getattr(record, seq_field, None) or 0
    else:
        seq = 0

    # Two records from different sources at the same instant with no sequence
    # number tie on all three leading fields, and heapq then returns them in
    # whatever order the streams happened to be passed in. The equity fork broke
    # that tie by origin and symbol; the crypto fork left it to the heap. For an
    # engine whose whole claim is determinism, leaving it to the heap is a bug,
    # so the tie-break is restored — appended, never inserted, so no ordering
    # that was already decided by the first three fields can change.
    origin = ""
    for field in _ORIGIN_FIELDS:
        value = getattr(record, field, None)
        if value is not None:
            origin = value
            break

    return (local_ts, source_ts, seq, origin, record.symbol)


class _Keyed:
    """Wrapper that makes a Record sortable by _sort_key without storing the key twice."""

    __slots__ = ("key", "record")

    def __init__(self, record: Record) -> None:
        self.key: tuple[int, int, int, str, str] = _sort_key(record)
        self.record: Record = record

    def __lt__(self, other: Any) -> bool:
        return bool(self.key < other.key)

    def __le__(self, other: Any) -> bool:
        return bool(self.key <= other.key)

    def __eq__(self, other: Any) -> bool:
        return bool(self.key == other.key)

    def __ge__(self, other: Any) -> bool:
        return bool(self.key >= other.key)

    def __gt__(self, other: Any) -> bool:
        return bool(self.key > other.key)


def replay(streams: Iterable[Iterator[Record]]) -> Iterator[Record]:
    """Merge N pre-sorted Record iterators into a single globally time-ordered stream.

    Args:
        streams: Iterable of iterators, each already sorted by ``local_ts``.
                 An empty iterable (or all-empty streams) produces an empty output.

    Yields:
        Records in non-decreasing ``(local_ts, source_ts_or_neg_inf, seq_or_0)`` order.

    Note:
        Each input stream MUST be pre-sorted by ``local_ts``; heapq.merge does
        not verify this and will silently emit incorrect order if violated.
    """
    keyed_streams = ((_Keyed(r) for r in s) for s in streams)
    for keyed in heapq.merge(*keyed_streams):
        yield keyed.record
