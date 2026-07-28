"""Equity open interest: summed out of the chain, aligned like the crypto board.

M2's sentence is "aggregate the Yahoo option chain's ``open_interest`` per underlying", and
the two halves of it are tested separately here: the *sum* is this module's own arithmetic
and the *alignment* is shared with crypto through
:func:`crocodile.core.analytics.open_interest.align_open_interest`, so a test that only
checked totals could not tell a working forward fill from a missing one.

The last test is the symmetry claim itself: two lakes, one crypto and one equity, and one
column set. That is what ``open-interest`` promises by being a single capability.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from crocodile.core.schema.enums import AssetClass, OptType
from crocodile.core.schema.records import OpenInterest, OptionsChain, Record
from crocodile.core.store.catalog import Catalog
from crocodile.core.store.parquet_sink import ParquetSink
from crocodile.crypto.analytics.oi_aggregator import aggregate_open_interest
from crocodile.equity.analytics.oi_aggregator import aggregate_option_open_interest

_BASE_NS = 1_704_067_200_000_000_000
_T1 = _BASE_NS + 1
_T2 = _BASE_NS + 2
_T3 = _BASE_NS + 3
_EXPIRY = _BASE_NS + 365 * 86_400 * 1_000_000_000
_END = 2**63 - 1


def _contract(
    ts: int,
    underlying: str,
    strike: float,
    opt_type: OptType,
    open_interest: float | None,
    source: str = "yahoo",
) -> OptionsChain:
    symbol = f"{source}:{underlying}-{int(strike)}-{opt_type.value}"
    return OptionsChain(
        source=source,
        symbol=symbol,
        symbol_raw=symbol.split(":", 1)[-1],
        source_ts=ts,
        local_ts=ts,
        asset_class=AssetClass.EQUITY,
        underlying=underlying,
        underlying_price=100.0,
        strike=strike,
        expiry=_EXPIRY,
        opt_type=opt_type,
        open_interest=open_interest,
    )


async def _write(data_dir: Path, records: list[Record]) -> None:
    sink = ParquetSink(data_dir, max_buffer_rows=10_000, flush_interval_seconds=9999)
    for record in records:
        await sink.put(record)
    await sink.flush()


def _lake(tmp_path: Path, records: list[Record]) -> Catalog:
    asyncio.run(_write(tmp_path, records))
    return Catalog(tmp_path)


def _one_poll() -> list[Record]:
    """One fetch of two underlyings' chains: three AAPL contracts and one MSFT."""
    return [
        _contract(_T1, "AAPL", 90.0, OptType.CALL, 10.0),
        _contract(_T1, "AAPL", 100.0, OptType.CALL, 20.0),
        _contract(_T1, "AAPL", 100.0, OptType.PUT, 30.0),
        _contract(_T1, "MSFT", 400.0, OptType.CALL, 7.0),
    ]


def test_an_underlyings_open_interest_is_the_sum_over_its_contracts(tmp_path: Path) -> None:
    """The one thing M2 asks for. No equity feed publishes a per-underlying figure.

    Calls and puts are summed together, which is the convention "open interest in AAPL
    options" names and what a chain page totals. Someone who wants the two sides apart is
    asking a different question, and the chain itself — which ``catalog-scan`` and
    ``replay`` already serve per contract, with ``opt_type`` on the row — is the answer.
    """
    frame = aggregate_option_open_interest(_lake(tmp_path, _one_poll()), [], 0, _END)
    assert frame.height == 1
    assert frame.row(0, named=True)["total_oi"] == pytest.approx(67.0)


def test_a_pattern_selects_underlyings_the_way_the_crypto_half_selects_symbols(
    tmp_path: Path,
) -> None:
    """``OpenInterestParams.symbols`` is one field with one meaning across both halves.

    Every element is a case-insensitive literal substring pattern and the elements are
    OR-ed — the semantic the crypto aggregator has always had, applied here to the series
    this half counts per. A field that meant "pattern" for one asset class and "identity"
    for the other would be the divergence-under-one-name the registry exists to end.
    """
    catalog = _lake(tmp_path, _one_poll())
    assert aggregate_option_open_interest(catalog, ["aapl"], 0, _END).row(0, named=True)[
        "total_oi"
    ] == pytest.approx(60.0)
    assert aggregate_option_open_interest(catalog, ["AAPL", "MSFT"], 0, _END).row(0, named=True)[
        "total_oi"
    ] == pytest.approx(67.0)
    # A lone string is the one-element case, not a sequence of characters.
    assert aggregate_option_open_interest(catalog, "MSFT", 0, _END).row(0, named=True)[
        "total_oi"
    ] == pytest.approx(7.0)
    # The comma string REST used to send through as one pattern matches nothing, loudly.
    assert aggregate_option_open_interest(catalog, ["AAPL,MSFT"], 0, _END).is_empty()
    # A blank token would become contains(""), which matches every underlying.
    assert aggregate_option_open_interest(catalog, ["   "], 0, _END).row(0, named=True)[
        "total_oi"
    ] == pytest.approx(67.0)


def test_a_series_that_did_not_report_at_an_instant_is_carried_forward(tmp_path: Path) -> None:
    """The alignment, which is where a board of several series stops being mostly empty.

    Two providers polling on their own clocks share almost no timestamps, so a row is
    emitted for every instant any of them observed and each series contributes its last
    known figure. Without the fill, ``total_oi`` would sawtooth between one provider's
    number and the other's.
    """
    records: list[Record] = [
        _contract(_T1, "AAPL", 100.0, OptType.CALL, 10.0, source="yahoo"),
        _contract(_T2, "AAPL", 100.0, OptType.CALL, 4.0, source="finnhub"),
        _contract(_T3, "AAPL", 100.0, OptType.CALL, 12.0, source="yahoo"),
    ]
    frame = aggregate_option_open_interest(_lake(tmp_path, records), [], 0, _END)
    assert frame["local_ts"].to_list() == [_T1, _T2, _T3]
    assert frame["yahoo"].to_list() == pytest.approx([10.0, 10.0, 12.0])
    assert frame["finnhub"].to_list() == pytest.approx([0.0, 4.0, 4.0])
    assert frame["total_oi"].to_list() == pytest.approx([10.0, 14.0, 16.0])


def test_a_contract_with_no_published_open_interest_does_not_zero_its_series(
    tmp_path: Path,
) -> None:
    """Yahoo omits the field on contracts that have never traded.

    Summing the null as 0.0 would give the same total at this instant and a different
    *series*: a poll in which every contract omitted the field would write a real zero over
    the last known figure and then forward-fill that zero. The crypto half applies the same
    rule to a null sample, which is why it lives in both aggregators rather than in the
    shared alignment.
    """
    records: list[Record] = [
        _contract(_T1, "AAPL", 100.0, OptType.CALL, 10.0),
        _contract(_T2, "AAPL", 100.0, OptType.CALL, None),
    ]
    frame = aggregate_option_open_interest(_lake(tmp_path, records), [], 0, _END)
    assert frame["total_oi"].to_list() == pytest.approx([10.0, 10.0])


def test_the_range_bounds_are_inclusive_and_bite(tmp_path: Path) -> None:
    catalog = _lake(tmp_path, _one_poll())
    assert aggregate_option_open_interest(catalog, [], _T1, _T1).height == 1
    assert aggregate_option_open_interest(catalog, [], _T2, _END).is_empty()


def test_a_lake_with_no_chain_returns_an_empty_frame(tmp_path: Path) -> None:
    """The contract every analytics function here keeps, including on a lake with no view."""
    assert aggregate_option_open_interest(Catalog(tmp_path), [], 0, _END).is_empty()


def test_both_halves_return_the_same_board(tmp_path: Path) -> None:
    """The claim ``open-interest`` makes by being one capability with one params struct.

    Same columns in the same order — ``local_ts``, one per source, ``total_oi`` — over two
    lakes that share no channel, because both widen their samples through the one function
    in ``core.analytics.open_interest``.
    """
    equity = _lake(tmp_path / "equity", _one_poll())
    crypto_records: list[Record] = [
        OpenInterest(
            source="yahoo",
            symbol="yahoo:AAPL-PERP",
            symbol_raw="AAPL-PERP",
            source_ts=_T1,
            local_ts=_T1,
            asset_class=AssetClass.CRYPTO,
            open_interest=67.0,
        )
    ]
    crypto = _lake(tmp_path / "crypto", crypto_records)

    from_equity = aggregate_option_open_interest(equity, [], 0, _END)
    from_crypto = aggregate_open_interest(crypto, [], 0, _END)
    assert from_equity.columns == ["local_ts", "yahoo", "total_oi"]
    assert from_equity.columns == from_crypto.columns
    assert from_equity.dtypes == from_crypto.dtypes
    assert from_equity["total_oi"][0] == from_crypto["total_oi"][0] == pytest.approx(67.0)
