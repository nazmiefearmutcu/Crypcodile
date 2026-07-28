"""Aligning open-interest samples onto one clock, for either market's idea of a series.

Open interest arrives as one sample per series per observation, and the series report on
their own schedules — two venues poll at different intervals, and an equity option chain
is one fetch of several thousand contracts. A board that showed only the timestamps every
series happened to share would be almost always empty, so the samples are forward-filled
onto the union of the instants any of them was observed at.

What a *series* is differs between the two halves and nothing else does. Crypto tracks
open interest per ``(venue, perpetual)`` off the ``open_interest`` channel; equity sums a
chain's per-contract open interest per ``(provider, underlying)`` off ``options_chain``,
because a listed underlying's open interest is the sum over its contracts and no feed
publishes it as one number. The two callers therefore build the sample map their own way
and hand it here, which is the only thing that keeps ``open-interest``'s two
implementations returning the same table: one ``local_ts`` column, one column per source,
one ``total_oi``.

Forward-filling from a starting value of 0.0 is what makes a series that has not reported
yet contribute nothing rather than a hole, and is why a null sample is dropped by the
caller rather than written: a null that overwrote the last known value would zero a live
series and then forward-fill the zero across every later instant.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import polars as pl

__all__ = ["SeriesKey", "align_open_interest"]

SeriesKey = tuple[str, str]
"""``(source, series)`` — the venue or provider, and whatever that half counts per."""


def align_open_interest(
    timestamps: Sequence[int],
    series_keys: Sequence[SeriesKey],
    samples: Mapping[int, Mapping[SeriesKey, float]],
) -> pl.DataFrame:
    """Forward-fill every series across ``timestamps``, then widen by source.

    Args:
        timestamps: The instants to emit, ascending. One row each.
        series_keys: Every ``(source, series)`` the board tracks. A key absent from
            ``samples`` entirely still gets a column contribution of 0.0, which is what
            makes the source columns comparable across rows.
        samples: ``{timestamp: {key: open_interest}}``. Each caller decides how several
            observations of one key at one instant combine — crypto has at most one, and
            equity sums a chain's contracts — because that is the half's own arithmetic
            and not this alignment's.

    Returns:
        A frame of ``local_ts``, one column per source (that source's series summed), and
        ``total_oi`` across all of them. Sources are ordered by name so two runs over one
        lake produce the same column order.
    """
    sources = sorted({source for source, _ in series_keys})
    last_seen: dict[SeriesKey, float] = dict.fromkeys(series_keys, 0.0)
    records: list[dict[str, float | int]] = []

    for ts in timestamps:
        current = samples.get(ts, {})
        for key in series_keys:
            if key in current:
                last_seen[key] = current[key]

        record: dict[str, float | int] = {"local_ts": ts}
        for source in sources:
            record[source] = sum(value for key, value in last_seen.items() if key[0] == source)
        record["total_oi"] = sum(last_seen.values())
        records.append(record)

    return pl.DataFrame(records)
