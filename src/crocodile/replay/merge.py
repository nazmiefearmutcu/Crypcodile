"""K-way merge replay engine.

Merges N pre-sorted per-(channel, symbol) iterators of Records into a single
globally time-ordered stream using heapq.merge.

Sort key (deterministic tie-break):
    (local_ts, exchange_ts_or_neg_inf, seq_or_0)

Where:
    - local_ts         — primary ordering key; monotonically increasing capture clock
    - exchange_ts_or_neg_inf — NULL exchange_ts is treated as -inf so it sorts
                               BEFORE any present exchange_ts at the same local_ts
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

from crocodile.schema.records import (
    BookDelta,
    BookSnapshot,
    BookTicker,
    Record,
)

# Sentinel for NULL exchange_ts — must be less than any real ns value.
# Real timestamps start around 1_000_000_000_000_000_000 ns (2001), so -1 is safely less.
_NEG_INF: int = -1


def _sort_key(record: Record) -> tuple[int, int, int]:
    """Return the (local_ts, exchange_ts_or_neg_inf, seq_or_0) tuple for ordering.

    NULL exchange_ts → -1 (sorts BEFORE any real nanosecond timestamp).
    seq is extracted from whichever field is present on the record type:
        BookDelta   → seq_id
        BookSnapshot → sequence_id
        BookTicker  → update_id
        all others  → 0 (no sequence concept)
    """
    local_ts: int = record.local_ts
    exchange_ts: int = record.exchange_ts if record.exchange_ts is not None else _NEG_INF

    seq: int
    if isinstance(record, BookDelta):
        seq = record.seq_id or 0
    elif isinstance(record, BookSnapshot):
        seq = record.sequence_id or 0
    elif isinstance(record, BookTicker):
        seq = record.update_id or 0
    else:
        seq = 0

    return (local_ts, exchange_ts, seq)


class _Keyed:
    """Wrapper that makes a Record sortable by _sort_key without storing the key twice."""

    __slots__ = ("key", "record")

    def __init__(self, record: Record) -> None:
        self.key: tuple[int, int, int] = _sort_key(record)
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
        Records in non-decreasing ``(local_ts, exchange_ts_or_neg_inf, seq_or_0)`` order.

    Note:
        Each input stream MUST be pre-sorted by ``local_ts``; heapq.merge does
        not verify this and will silently emit incorrect order if violated.
    """
    keyed_streams = ((_Keyed(r) for r in s) for s in streams)
    for keyed in heapq.merge(*keyed_streams):
        yield keyed.record
