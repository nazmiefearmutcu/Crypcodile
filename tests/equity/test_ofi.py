"""M7: order-flow imbalance read off an equity L1 quote stream.

The measurement itself is pinned in ``tests/core/test_ofi_core.py``. What is checked here is
the *read* — that quotes in a real lake reach it, that a one-sided quote does not, and that
the answer is the number the crypto half would give for the same four numbers arriving on
the other channel. That last one is the point of the whole file: two halves of one
capability agreeing in prose is not agreement.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from crocodile.core.schema.enums import AssetClass
from crocodile.core.schema.records import BookSnapshot, Quote
from crocodile.core.store.catalog import Catalog
from crocodile.core.store.parquet_sink import ParquetSink
from crocodile.crypto.analytics.ofi import calculate_ofi
from crocodile.equity.analytics.ofi import calculate_quote_ofi

_BASE_NS = 1_704_067_200_000_000_000  # 2024-01-01 00:00:00 UTC
_SEC = 1_000_000_000
_TICKER = "AAPL"
_PERP = "deribit:BTC-PERPETUAL"

# One top of book per observation, chosen so every branch of the increment fires: sizes
# only, then both prices improving, then both worsening.
_TOPS = (
    (100.0, 2.0, 101.0, 1.0),
    (100.0, 3.0, 101.0, 2.0),
    (101.0, 4.0, 102.0, 1.0),
    (100.0, 2.0, 101.0, 3.0),
)


def _quote(index: int, bid_px: float, bid_sz: float, ask_px: float, ask_sz: float) -> Quote:
    ts = _BASE_NS + index * 10 * _SEC
    return Quote(
        source="alpaca",
        symbol=_TICKER,
        symbol_raw=_TICKER,
        source_ts=ts,
        local_ts=ts,
        asset_class=AssetClass.EQUITY,
        bid_px=bid_px,
        bid_sz=bid_sz,
        ask_px=ask_px,
        ask_sz=ask_sz,
    )


def _snapshot(
    index: int, bid_px: float, bid_sz: float, ask_px: float, ask_sz: float
) -> BookSnapshot:
    ts = _BASE_NS + index * 10 * _SEC
    return BookSnapshot(
        source="deribit",
        symbol=_PERP,
        symbol_raw="BTC-PERPETUAL",
        source_ts=ts,
        local_ts=ts,
        asset_class=AssetClass.CRYPTO,
        bids=[(bid_px, bid_sz)],
        asks=[(ask_px, ask_sz)],
        depth=1,
    )


async def _write(data_dir: Path, records: list[object]) -> None:
    sink = ParquetSink(data_dir, max_buffer_rows=10_000, flush_interval_seconds=9999)
    for record in records:
        await sink.put(record)
    await sink.flush()


@pytest.fixture
def quote_lake(tmp_path: Path) -> Path:
    path = tmp_path / "quotes"
    path.mkdir()
    asyncio.run(_write(path, [_quote(i, *top) for i, top in enumerate(_TOPS)]))
    return path


def test_consecutive_quotes_are_differenced_and_binned(quote_lake: Path) -> None:
    """The same three bins the crypto fixture produces, from quotes instead of snapshots.

    Bin 1 holds the size-only step (bid +1, ask +1, so 0.0); bin 2 the step where both
    prices improved (bid +4, ask -2, so 6.0); bin 3 the step where both worsened
    (bid -4, ask +3, so -7.0).
    """
    with Catalog(quote_lake) as catalog:
        frame = calculate_quote_ofi(
            catalog, _TICKER, _BASE_NS, _BASE_NS + 40 * _SEC, "15s"
        )
    assert frame["timestamp"].to_list() == [
        _BASE_NS,
        _BASE_NS + 15 * _SEC,
        _BASE_NS + 30 * _SEC,
    ]
    assert frame["ofi"].to_list() == pytest.approx([0.0, 6.0, -7.0])
    assert frame["best_bid"].to_list() == pytest.approx([100.0, 101.0, 100.0])
    assert frame["best_ask"].to_list() == pytest.approx([101.0, 102.0, 101.0])


def test_the_two_halves_report_the_same_imbalance_for_the_same_top_of_book(
    tmp_path: Path,
) -> None:
    """One statistic, two channels — asserted by running both and comparing frames.

    This is the assertion the shared :func:`~crocodile.core.analytics.ofi.ofi_increment`
    exists for. Before it, the conditioning lived inline in the crypto reader, and an equity
    half would have been a second copy of sixteen lines: copies do not diverge loudly, they
    diverge on the branch one fixture happens not to cover, and both keep answering under one
    capability name. Comparing the arithmetic rather than the signature is the only form of
    this claim that stays true.
    """
    crypto_dir = tmp_path / "crypto"
    equity_dir = tmp_path / "equity"
    crypto_dir.mkdir()
    equity_dir.mkdir()
    asyncio.run(_write(crypto_dir, [_snapshot(i, *top) for i, top in enumerate(_TOPS)]))
    asyncio.run(_write(equity_dir, [_quote(i, *top) for i, top in enumerate(_TOPS)]))

    end = _BASE_NS + 40 * _SEC
    with Catalog(crypto_dir) as crypto_catalog, Catalog(equity_dir) as equity_catalog:
        from_book = calculate_ofi(crypto_catalog, _PERP, _BASE_NS, end, "15s")
        from_quotes = calculate_quote_ofi(equity_catalog, _TICKER, _BASE_NS, end, "15s")
    assert from_quotes.equals(from_book)


def test_a_one_sided_quote_is_not_a_top_of_book_and_is_dropped(tmp_path: Path) -> None:
    """Alpaca sends ``bp: 0`` outside a symbol's quoting hours.

    Kept rather than dropped, the zero would be differenced as an entire bid queue being
    cancelled and then reinstated — two large imbalances either side of a gap in which
    nothing happened. The equity form of the crypto half's empty ``bids``/``asks`` list.
    """
    path = tmp_path / "onesided"
    path.mkdir()
    asyncio.run(
        _write(
            path,
            [
                _quote(0, 100.0, 2.0, 101.0, 1.0),
                _quote(1, 0.0, 0.0, 101.0, 1.0),
                _quote(2, 100.0, 5.0, 101.0, 1.0),
            ],
        )
    )
    with Catalog(path) as catalog:
        frame = calculate_quote_ofi(catalog, _TICKER, _BASE_NS, _BASE_NS + 40 * _SEC, "1h")
    # One step survives, from the first usable quote to the third: bid size 2 -> 5.
    assert len(frame) == 1
    assert frame["ofi"][0] == pytest.approx(3.0)


def test_a_lake_with_no_quotes_answers_with_a_table_rather_than_a_bare_frame(
    tmp_path: Path,
) -> None:
    """An empty answer a caller can still select columns on; see ``OFI_SCHEMA``."""
    with Catalog(tmp_path) as catalog:
        frame = calculate_quote_ofi(catalog, _TICKER, _BASE_NS, _BASE_NS + 40 * _SEC, "15s")
    assert frame.is_empty()
    assert frame.columns == ["timestamp", "best_bid", "best_ask", "ofi"]


def test_a_lake_of_bars_and_no_quotes_yields_nothing_rather_than_a_column_of_zeros(
    tmp_path: Path,
) -> None:
    """The wrong turn this implementation had to avoid, asserted as behaviour.

    ``OHLCV.buy_volume`` and ``sell_volume`` are the obvious ingredients for an equity flow
    statistic, and no equity writer in this tree fills them — an Alpaca bar carries neither.
    An imbalance built on them would answer every call for every symbol with zeros, which is
    a fabricated calm rather than a missing answer. A lake holding only bars therefore has to
    produce *no rows*, not quiet ones.

    The premise below was written as ``== 0.0`` and is now ``is None``, and the change is the
    point rather than an adjustment to it. Both halves of this phase landed independently:
    this test's branch avoided the two fields because they were an all-zeros trap, and the
    follow-up branch removed the trap by making an unfilled split a typed hole instead of a
    measured zero. Neither knew about the other, git merged both without a conflict — the
    two edits are in different files — and the only thing that noticed was this line going
    red. A premise asserted out loud is what turns somebody else's fix into a failing test
    rather than into a test that quietly stops meaning anything.
    """
    from crocodile.core.schema.records import OHLCV

    path = tmp_path / "bars"
    path.mkdir()
    bars = [
        OHLCV(
            source="alpaca",
            symbol=_TICKER,
            symbol_raw=_TICKER,
            source_ts=_BASE_NS + i * 60 * _SEC,
            local_ts=_BASE_NS + i * 60 * _SEC,
            asset_class=AssetClass.EQUITY,
            interval="1m",
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.5,
            volume=1_000.0,
        )
        for i in range(3)
    ]
    asyncio.run(_write(path, list(bars)))
    with Catalog(path) as catalog:
        frame = calculate_quote_ofi(catalog, _TICKER, _BASE_NS, _BASE_NS + 600 * _SEC, "1m")
    assert frame.is_empty()
    assert all(bar.buy_volume is None and bar.sell_volume is None for bar in bars), (
        "the premise of this test: an equity writer fills no bar volume split, and an "
        "unfilled one is a hole rather than a zero"
    )
