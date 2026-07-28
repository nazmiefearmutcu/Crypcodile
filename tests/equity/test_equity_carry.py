"""The four equity halves of M5, driven over a real Parquet lake.

Every fixture below writes real records through ``ParquetSink`` and reads them back
through ``Catalog``, for the reason ``tests/capabilities/test_analytics.py`` gives: a
stubbed catalog would exercise the argument order and nothing else, and three of these
four functions turn on *which channel* a leg came out of and on a join against a
publication date, both of which only exist once the rows are on disk.

Nothing here reaches the network. The Treasury rows are constructed in-process from the
same :func:`~crocodile.equity.providers.treasury.parse_par_yield_csv` the provider tests
drive off a checked-in fixture, so the curve the carry is measured against is the curve
that file publishes rather than a number invented for the test.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest

from crocodile.core.analytics.carry import DAYS_PER_YEAR
from crocodile.core.schema.enums import AssetClass, CorpActionType, OptType, Side
from crocodile.core.schema.records import (
    OHLCV,
    CorporateAction,
    IndexValue,
    MacroSeries,
    OptionsChain,
    Record,
    Trade,
)
from crocodile.core.store.catalog import Catalog
from crocodile.core.store.parquet_sink import ParquetSink
from crocodile.equity.analytics.carry import (
    equity_basis,
    equity_forward_basis,
    equity_funding_apr,
    equity_spot_future_carry,
    price_leg,
    risk_free_curve,
)
from crocodile.equity.providers.treasury import parse_par_yield_csv

_SEC_NS = 1_000_000_000
_DAY_NS = 86_400 * _SEC_NS

# 2024-01-08 12:00 UTC — a Monday lunchtime, three days after the last curve the fixture
# below publishes, so the "nearest prior quote" lookup has a weekend to carry over.
_NOW = 1_704_715_200_000_000_000
_T1 = _NOW + 1 * _SEC_NS
_T2 = _NOW + 2 * _SEC_NS
_T3 = _NOW + 3 * _SEC_NS
_WINDOW = (_NOW - _DAY_NS, _NOW + _DAY_NS)

_SPOT = "SPX"
_FUTURE = "ESH24"
_STOCK = "AAPL"

# A quarter out, so the 3-month point is the tenor the curve lookup picks.
_EXPIRY = _NOW + 91 * _DAY_NS

_CURVE_CSV = (
    "Date,1 Mo,3 Mo,6 Mo,1 Yr,2 Yr,10 Yr\n"
    "01/05/2024,5.53,5.46,5.24,4.84,4.40,4.05\n"
    "01/04/2024,5.55,5.47,5.26,4.85,4.38,3.99\n"
)
_THREE_MONTH_ON_JAN_5 = 0.0546
_ONE_MONTH_ON_JAN_5 = 0.0553


def _trade(symbol: str, ts: int, price: float, tid: str) -> Trade:
    return Trade(
        source="alpaca",
        symbol=symbol,
        symbol_raw=symbol,
        local_ts=ts,
        asset_class=AssetClass.EQUITY,
        source_ts=ts,
        id=tid,
        price=price,
        amount=100.0,
        side=Side.UNKNOWN,
    )


def _bar(symbol: str, ts: int, close: float) -> OHLCV:
    return OHLCV(
        source="stooq",
        symbol=symbol,
        symbol_raw=symbol,
        local_ts=ts,
        asset_class=AssetClass.EQUITY,
        source_ts=ts,
        interval="1d",
        open=close,
        high=close,
        low=close,
        close=close,
        volume=1000.0,
    )


def _index(symbol: str, ts: int, value: float) -> IndexValue:
    return IndexValue(
        source="stooq",
        symbol=symbol,
        symbol_raw=symbol,
        local_ts=ts,
        asset_class=AssetClass.EQUITY,
        source_ts=ts,
        value=value,
    )


def _option(
    ts: int,
    strike: float,
    opt: OptType,
    bid: float,
    ask: float,
    spot: float | None = 100.0,
    expiry: int | None = None,
) -> OptionsChain:
    """One contract, carrying the spot out of the payload that produced it.

    ``spot`` defaults to a real number rather than to ``None``, which is what it was
    pinned at until this fixture was found to be structurally unable to see the field the
    provider writes: every chain built here answered as if Yahoo had left the column empty,
    so the one column the forward's index leg comes from was never exercised. ``None``
    remains reachable by passing it, because a chain with no spot is a real state and now
    has a real consequence.
    """
    return OptionsChain(
        source="yahoo",
        symbol=f"{_STOCK}{strike:g}{opt.value}",
        symbol_raw=f"{_STOCK}{strike:g}{opt.value}",
        local_ts=ts,
        asset_class=AssetClass.EQUITY,
        source_ts=ts,
        underlying=_STOCK,
        underlying_price=spot,
        strike=strike,
        expiry=_EXPIRY if expiry is None else expiry,
        opt_type=opt,
        bid_px=bid,
        ask_px=ask,
    )


def _dividend(ts: int, value: float) -> CorporateAction:
    return CorporateAction(
        source="tiingo",
        symbol=_STOCK,
        symbol_raw=_STOCK,
        local_ts=ts,
        asset_class=AssetClass.EQUITY,
        source_ts=ts,
        ex_date="2024-01-08",
        type=CorpActionType.DIVIDEND_CASH,
        value=value,
    )


def _curve_records(local_ts: int = _NOW) -> list[MacroSeries]:
    return parse_par_yield_csv(_CURVE_CSV, local_ts=local_ts)


def _published_ns(date_val: str) -> int:
    """When that day's par curve actually went up: 3:30 pm New York, DST included.

    Computed here from the same market timezone the module uses rather than written out as
    a UTC constant, because the constant is what the code under test got wrong — a literal
    would be an hour off for two thirds of the year and would agree with a fixed-offset
    implementation for the wrong reason.
    """
    from datetime import date as _date
    from datetime import datetime as _datetime
    from datetime import time as _time

    from crocodile.core.scheduler.calendar import MARKET_TZ

    published = _datetime.combine(
        _date.fromisoformat(date_val), _time(15, 30), tzinfo=MARKET_TZ
    )
    return int(published.timestamp()) * _SEC_NS


def _lake(tmp_path: Path, records: list[Record]) -> Catalog:
    async def _write() -> None:
        sink = ParquetSink(tmp_path)
        for record in records:
            await sink.put(record)
        await sink.flush()
        await sink.close()

    asyncio.run(_write())
    return Catalog(tmp_path)


# ---------------------------------------------------------------------------
# price_leg — the channel fallback that has no crypto counterpart
# ---------------------------------------------------------------------------


def test_a_trade_series_is_preferred_over_a_bar_series(tmp_path: Path) -> None:
    """A print is an observation of a transaction; a close is the last print in a bucket.

    Both channels hold the symbol here and the trade prices are deliberately different
    from the bar closes, so the assertion cannot pass by coincidence.
    """
    catalog = _lake(
        tmp_path,
        [_trade(_SPOT, _T1, 100.0, "a"), _bar(_SPOT, _T1, 999.0)],
    )
    leg = price_leg(catalog, _SPOT, *_WINDOW)
    assert leg["price"].to_list() == [100.0]


def test_a_bar_series_serves_a_symbol_with_no_prints(tmp_path: Path) -> None:
    catalog = _lake(tmp_path, [_bar(_SPOT, _T1, 4750.0)])
    assert price_leg(catalog, _SPOT, *_WINDOW)["price"].to_list() == [4750.0]


def test_an_index_level_serves_a_symbol_with_neither(tmp_path: Path) -> None:
    """``index_value`` is the only channel a level like ``^SPX`` is ever written to."""
    catalog = _lake(tmp_path, [_index("^SPX", _T1, 4750.0)])
    assert price_leg(catalog, "^SPX", *_WINDOW)["price"].to_list() == [4750.0]


def test_a_symbol_with_nothing_yields_the_empty_frame_contract(tmp_path: Path) -> None:
    catalog = _lake(tmp_path, [_trade(_SPOT, _T1, 100.0, "a")])
    assert price_leg(catalog, "NOSUCH", *_WINDOW).is_empty()


def test_a_non_positive_price_is_dropped_before_anything_divides_by_it(
    tmp_path: Path,
) -> None:
    """A zero cash leg is an infinite ``basis_pct``; the crypto half guards the same way."""
    catalog = _lake(
        tmp_path,
        [_trade(_SPOT, _T1, 0.0, "a"), _trade(_SPOT, _T2, 100.0, "b")],
    )
    assert price_leg(catalog, _SPOT, *_WINDOW)["price"].to_list() == [100.0]


# ---------------------------------------------------------------------------
# risk_free_curve — the leg M5 adds
# ---------------------------------------------------------------------------


def test_an_empty_lake_yields_an_empty_curve_rather_than_raising(tmp_path: Path) -> None:
    """A lake nobody has pointed at the Treasury feed is the ordinary state of a fresh
    install, and the capabilities that can answer without a financing leg must go on
    answering."""
    catalog = _lake(tmp_path, [_trade(_SPOT, _T1, 100.0, "a")])
    assert not risk_free_curve(catalog, _NOW)


def test_the_curve_picks_the_shortest_tenor_that_covers_the_horizon(tmp_path: Path) -> None:
    """Rounding up is the conservative direction: the curve usually slopes upward, so the
    longer tenor yields more and subtracting more understates the excess reported."""
    catalog = _lake(tmp_path, _curve_records())
    curve = risk_free_curve(catalog, _NOW)
    assert curve.at(_NOW, 60.0).symbol == "treasury:UST3M"  # type: ignore[union-attr]
    assert curve.at(_NOW, 91.0).symbol == "treasury:UST3M"  # type: ignore[union-attr]
    assert curve.at(_NOW, 100.0).symbol == "treasury:UST6M"  # type: ignore[union-attr]
    assert curve.at(_NOW, 5.0).symbol == "treasury:UST1M"  # type: ignore[union-attr]


def test_a_horizon_past_the_long_end_falls_back_to_the_longest_point(
    tmp_path: Path,
) -> None:
    catalog = _lake(tmp_path, _curve_records())
    curve = risk_free_curve(catalog, _NOW)
    assert curve.at(_NOW, 100_000.0).symbol == "treasury:UST10Y"  # type: ignore[union-attr]


def test_the_quote_is_the_most_recent_one_published_at_or_before_the_instant(
    tmp_path: Path,
) -> None:
    """Friday's curve serves Monday's prices; the fixture's two dates are Jan 4 and 5."""
    catalog = _lake(tmp_path, _curve_records())
    curve = risk_free_curve(catalog, _NOW)
    quote = curve.at(_NOW, 91.0)
    assert quote is not None
    assert quote.date == "2024-01-05"
    assert quote.rate == pytest.approx(_THREE_MONTH_ON_JAN_5)


def test_a_price_earlier_than_every_published_quote_has_no_rate(tmp_path: Path) -> None:
    """``None`` and not the earliest quote: a rate cannot be in force before it exists."""
    catalog = _lake(tmp_path, _curve_records())
    curve = risk_free_curve(catalog, _NOW)
    assert curve.at(_NOW - 30 * _DAY_NS, 91.0) is None


def test_a_quote_is_filed_under_the_hour_treasury_publishes_it_not_utc_midnight(
    tmp_path: Path,
) -> None:
    """The record stores a date; midnight is not a claim about when the curve went up.

    ``treasury/client.py`` says so in as many words, and this module read it as one anyway.
    Treasury posts the par curve at 3:30 pm ET, so a price stamped at 09:30 ET on 2024-01-05
    is *before* that day's curve existed — and subtracting it is lookahead, which the
    provenance registry treats as categorical ("there is no confidence at which it is a
    legal record") rather than as staleness to be scored in ``[0, 1]``.
    """
    catalog = _lake(tmp_path, _curve_records())
    curve = risk_free_curve(catalog, _NOW)
    assert curve.at(_published_ns("2024-01-05"), 91.0) is not None
    morning = _published_ns("2024-01-05") - 6 * 3600 * _SEC_NS  # 09:30 ET, same session
    quote = curve.at(morning, 91.0)
    assert quote is not None
    assert quote.date == "2024-01-04", "a morning price cannot see the afternoon's curve"


def test_the_publication_hour_follows_the_market_timezone_across_the_dst_boundary(
    tmp_path: Path,
) -> None:
    """3:30 pm ET is 20:30 UTC in winter and 19:30 UTC in summer.

    A fixed offset would be an hour wrong for one half of the year in one direction or the
    other, and the direction that is wrong admits the lookahead this is here to close.
    """
    winter = _lake(tmp_path / "w", parse_par_yield_csv("Date,3 Mo\n01/05/2024,5.46\n"))
    summer = _lake(tmp_path / "s", parse_par_yield_csv("Date,3 Mo\n07/05/2024,5.46\n"))
    winter_stamp = risk_free_curve(winter, _NOW + 400 * _DAY_NS).at(_NOW + 400 * _DAY_NS, 91.0)
    summer_stamp = risk_free_curve(summer, _NOW + 400 * _DAY_NS).at(_NOW + 400 * _DAY_NS, 91.0)
    assert winter_stamp is not None and summer_stamp is not None
    assert (winter_stamp.quote_ts // _SEC_NS) % 86_400 == 20 * 3600 + 30 * 60
    assert (summer_stamp.quote_ts // _SEC_NS) % 86_400 == 19 * 3600 + 30 * 60


def test_the_lookup_is_by_publication_date_not_by_ingest_instant(tmp_path: Path) -> None:
    """A year of curve backfilled in one pass shares one ``local_ts``.

    Filtering on it would put every quote in whichever window contained the ingest and
    outside every other one, which is the specific failure the module docstring names. The
    records here are stamped with a ``local_ts`` a year after their publication dates.
    """
    catalog = _lake(tmp_path, _curve_records(local_ts=_NOW + 365 * _DAY_NS))
    curve = risk_free_curve(catalog, _NOW)
    quote = curve.at(_NOW, 91.0)
    assert quote is not None
    assert quote.date == "2024-01-05"


# ---------------------------------------------------------------------------
# basis — two legs, no horizon, no financing term
# ---------------------------------------------------------------------------


@pytest.fixture
def two_leg_lake(tmp_path: Path) -> Iterator[Catalog]:
    """Spot at 100/102, derivative at 101/104, interleaved so the ASOF join has work."""
    yield _lake(
        tmp_path,
        [
            _trade(_SPOT, _T1, 100.0, "s1"),
            _trade(_FUTURE, _T2, 101.0, "f1"),
            _trade(_SPOT, _T3, 102.0, "s2"),
            _trade(_FUTURE, _T3 + _SEC_NS, 104.0, "f2"),
            *_curve_records(),
        ],
    )


def test_basis_returns_the_crypto_halfs_columns(two_leg_lake: Catalog) -> None:
    """One capability publishes one result schema, including the column named
    ``perp_price`` — which for equities holds the derivative leg."""
    frame = equity_basis(two_leg_lake, _SPOT, _FUTURE, *_WINDOW)
    assert frame.columns == ["local_ts", "spot_price", "perp_price", "basis", "basis_pct"]


def test_basis_pairs_each_derivative_print_with_the_nearest_prior_cash_print(
    two_leg_lake: Catalog,
) -> None:
    frame = equity_basis(two_leg_lake, _SPOT, _FUTURE, *_WINDOW)
    assert frame["spot_price"].to_list() == [100.0, 102.0]
    assert frame["perp_price"].to_list() == [101.0, 104.0]
    assert frame["basis"].to_list() == [pytest.approx(1.0), pytest.approx(2.0)]
    assert frame["basis_pct"].to_list() == [pytest.approx(0.01), pytest.approx(2.0 / 102.0)]


def test_basis_carries_no_risk_free_column_because_it_has_no_horizon(
    two_leg_lake: Catalog,
) -> None:
    """``basis`` takes no expiry on either asset class, so annualising would need a
    horizon nobody stated. ``treasury_carry`` would claim a third leg it does not have."""
    frame = equity_basis(two_leg_lake, _SPOT, _FUTURE, *_WINDOW)
    assert "risk_free_rate" not in frame.columns
    assert "carry_pct" not in frame.columns


def test_basis_is_empty_when_either_leg_is(tmp_path: Path) -> None:
    catalog = _lake(tmp_path, [_trade(_SPOT, _T1, 100.0, "s1")])
    assert equity_basis(catalog, _SPOT, _FUTURE, *_WINDOW).is_empty()
    assert equity_basis(catalog, _FUTURE, _SPOT, *_WINDOW).is_empty()


def test_a_derivative_print_before_any_cash_print_is_dropped(tmp_path: Path) -> None:
    """A backward ASOF join has nothing to pair it with, and pairing it forward would be
    a spread against a price that had not been quoted yet."""
    catalog = _lake(
        tmp_path,
        [_trade(_FUTURE, _T1, 101.0, "f0"), _trade(_SPOT, _T2, 100.0, "s1")],
    )
    assert equity_basis(catalog, _SPOT, _FUTURE, *_WINDOW).is_empty()


# ---------------------------------------------------------------------------
# spot-future-basis — the carry M5 is named for
# ---------------------------------------------------------------------------


def test_without_an_expiry_the_answer_is_the_five_basis_columns(
    two_leg_lake: Catalog,
) -> None:
    """Which is exactly how the crypto half behaves, and what a crypto test pins for it."""
    frame = equity_spot_future_carry(two_leg_lake, _FUTURE, _SPOT, *_WINDOW)
    assert frame.columns == ["local_ts", "future_price", "spot_price", "basis", "basis_pct"]


def test_with_an_expiry_the_carry_is_the_annualised_basis_less_the_published_yield(
    two_leg_lake: Catalog,
) -> None:
    frame = equity_spot_future_carry(two_leg_lake, _FUTURE, _SPOT, *_WINDOW, _EXPIRY)
    row = frame.row(0, named=True)
    days = 91.0 - 2.0 / 86_400.0
    assert row["annualized_pct"] == pytest.approx(row["basis_pct"] * DAYS_PER_YEAR / days)
    assert row["risk_free_rate"] == pytest.approx(_THREE_MONTH_ON_JAN_5)
    assert row["carry_pct"] == pytest.approx(row["annualized_pct"] - row["risk_free_rate"])


def test_the_row_says_which_published_number_was_subtracted(two_leg_lake: Catalog) -> None:
    """A par yield is a proxy for a repo rate, and a proxy that names itself is a
    different thing from one that does not."""
    row = equity_spot_future_carry(two_leg_lake, _FUTURE, _SPOT, *_WINDOW, _EXPIRY).row(
        0, named=True
    )
    assert row["risk_free_date"] == "2024-01-05"
    assert row["risk_free_tenor_days"] == pytest.approx(3 * DAYS_PER_YEAR / 12.0)


def test_a_lake_with_no_curve_reports_an_absent_leg_rather_than_a_zero_rate(
    tmp_path: Path,
) -> None:
    """A defaulted 0.0 would make the carry equal the basis, which is the answer a real
    zero-rate world gives, with nothing on the row to tell the two apart."""
    catalog = _lake(
        tmp_path,
        [_trade(_SPOT, _T1, 100.0, "s1"), _trade(_FUTURE, _T2, 101.0, "f1")],
    )
    row = equity_spot_future_carry(catalog, _FUTURE, _SPOT, *_WINDOW, _EXPIRY).row(0, named=True)
    assert row["annualized_pct"] is not None
    assert row["risk_free_rate"] is None
    assert row["carry_pct"] is None


def test_an_expired_contract_keeps_its_basis_and_loses_its_annualisation(
    two_leg_lake: Catalog,
) -> None:
    """The spread was really quoted; that it cannot be annualised is stated in the column
    rather than by the row's absence."""
    frame = equity_spot_future_carry(two_leg_lake, _FUTURE, _SPOT, *_WINDOW, _NOW - _DAY_NS)
    assert len(frame) == 2
    assert frame["basis"].to_list() == [pytest.approx(1.0), pytest.approx(2.0)]
    assert frame["annualized_pct"].to_list() == [None, None]
    assert frame["carry_pct"].to_list() == [None, None]


# ---------------------------------------------------------------------------
# Confidence — the number the registered formula produces, at the call site
# ---------------------------------------------------------------------------


def test_a_fresh_yield_scores_higher_than_a_stale_one(tmp_path: Path) -> None:
    """The observable ``treasury_carry`` is a function of is the yield's age against the
    horizon it is being applied over. Two lakes, same prices, curves three days apart.
    """
    prices = [_trade(_SPOT, _T1, 100.0, "s1"), _trade(_FUTURE, _T2, 101.0, "f1")]
    fresh = _lake(tmp_path / "fresh", [*prices, *_curve_records()])
    stale_csv = "Date,3 Mo\n11/06/2023,5.40\n"
    stale = _lake(
        tmp_path / "stale",
        [*prices, *parse_par_yield_csv(stale_csv, local_ts=_NOW)],
    )
    fresh_confidence = equity_spot_future_carry(fresh, _FUTURE, _SPOT, *_WINDOW, _EXPIRY)[
        "prov_confidence"
    ][0]
    stale_confidence = equity_spot_future_carry(stale, _FUTURE, _SPOT, *_WINDOW, _EXPIRY)[
        "prov_confidence"
    ][0]
    assert fresh_confidence > stale_confidence
    assert 0.0 <= stale_confidence <= fresh_confidence <= 1.0


def test_an_absent_yield_scores_the_two_price_legs_and_nothing_more(
    tmp_path: Path,
) -> None:
    """Two of three legs, the third scoring zero — the encoding the basis argues for."""
    catalog = _lake(
        tmp_path,
        [_trade(_SPOT, _T1, 100.0, "s1"), _trade(_FUTURE, _T2, 101.0, "f1")],
    )
    frame = equity_spot_future_carry(catalog, _FUTURE, _SPOT, *_WINDOW, _EXPIRY)
    assert frame["prov_confidence"][0] == pytest.approx(2.0 / 3.0)


def test_the_confidence_is_the_registered_formula_and_not_a_number_written_here(
    two_leg_lake: Catalog,
) -> None:
    from crocodile.core.schema.provenance import confidence_for

    frame = equity_spot_future_carry(two_leg_lake, _FUTURE, _SPOT, *_WINDOW, _EXPIRY)
    row = frame.row(0, named=True)
    horizon_ns = _EXPIRY - int(row["local_ts"])
    age_ns = int(row["local_ts"]) - 1_704_412_800_000_000_000  # 2024-01-05 UTC midnight
    assert frame["prov_confidence"][0] == pytest.approx(
        confidence_for(
            "treasury_carry",
            {"n_price_legs": 2, "yield_age_ns": age_ns, "horizon_ns": horizon_ns},
        )
    )


# ---------------------------------------------------------------------------
# perp-basis — put-call parity, one symbol
# ---------------------------------------------------------------------------


@pytest.fixture
def parity_lake(tmp_path: Path) -> Iterator[Catalog]:
    """Spot at 100, and a chain whose 100-strike call is 3.00 over its put.

    Parity then puts the forward at ``100 + 3 * (1 + r * 91/365)``, which is above spot —
    the ordinary state of a non-dividend-paying underlying with positive rates.
    """
    yield _lake(
        tmp_path,
        [
            _trade(_STOCK, _T1, 100.0, "p1"),
            _option(_T2, 90.0, OptType.CALL, 11.5, 12.5),
            _option(_T2, 90.0, OptType.PUT, 0.9, 1.1),
            _option(_T2, 100.0, OptType.CALL, 5.4, 5.6),
            _option(_T2, 100.0, OptType.PUT, 2.4, 2.6),
            *_curve_records(),
        ],
    )


def test_forward_basis_returns_the_crypto_halfs_first_five_columns(
    parity_lake: Catalog,
) -> None:
    frame = equity_forward_basis(parity_lake, _STOCK, *_WINDOW)
    assert frame.columns[:5] == [
        "local_ts",
        "mark_price",
        "index_price",
        "basis",
        "basis_pct",
    ]


def test_the_forward_is_put_call_parity_at_the_strike_nearest_the_cash_price(
    parity_lake: Catalog,
) -> None:
    """Parity holds at every strike, so the choice is about noise: away from the money one
    leg is nearly worthless and its bid-ask width is most of its price."""
    row = equity_forward_basis(parity_lake, _STOCK, *_WINDOW).row(0, named=True)
    assert row["strike"] == pytest.approx(100.0)
    horizon = (_EXPIRY - _T2) / _DAY_NS
    expected = 100.0 + 3.0 * (1.0 + _THREE_MONTH_ON_JAN_5 * horizon / DAYS_PER_YEAR)
    assert row["mark_price"] == pytest.approx(expected)
    assert row["index_price"] == pytest.approx(100.0)
    assert row["basis"] == pytest.approx(expected - 100.0)


def test_the_discount_factor_actually_moves_the_forward(tmp_path: Path) -> None:
    """If it did not, this capability's place on M5 would be decorative."""
    chain = [
        _trade(_STOCK, _T1, 100.0, "p1"),
        _option(_T2, 100.0, OptType.CALL, 5.4, 5.6),
        _option(_T2, 100.0, OptType.PUT, 2.4, 2.6),
    ]
    low = _lake(tmp_path / "low", [*chain, *parse_par_yield_csv("Date,3 Mo\n01/05/2024,0.05\n")])
    high = _lake(tmp_path / "high", [*chain, *parse_par_yield_csv("Date,3 Mo\n01/05/2024,9.00\n")])
    low_mark = equity_forward_basis(low, _STOCK, *_WINDOW)["mark_price"][0]
    high_mark = equity_forward_basis(high, _STOCK, *_WINDOW)["mark_price"][0]
    assert high_mark > low_mark


def test_a_lake_with_no_curve_yields_no_forward_rather_than_an_undiscounted_one(
    tmp_path: Path,
) -> None:
    """``r = 0`` gives a computable and slightly wrong number that nothing on the row would
    distinguish from the same answer in a genuinely zero-rate market."""
    catalog = _lake(
        tmp_path,
        [
            _trade(_STOCK, _T1, 100.0, "p1"),
            _option(_T2, 100.0, OptType.CALL, 5.4, 5.6),
            _option(_T2, 100.0, OptType.PUT, 2.4, 2.6),
        ],
    )
    assert equity_forward_basis(catalog, _STOCK, *_WINDOW).is_empty()


def test_a_strike_quoted_on_only_one_side_cannot_close_parity(tmp_path: Path) -> None:
    catalog = _lake(
        tmp_path,
        [
            _trade(_STOCK, _T1, 100.0, "p1"),
            _option(_T2, 100.0, OptType.CALL, 5.4, 5.6),
            *_curve_records(),
        ],
    )
    assert equity_forward_basis(catalog, _STOCK, *_WINDOW).is_empty()


def test_a_symbol_with_no_chain_is_empty(tmp_path: Path) -> None:
    """A cash price on its own is not half a forward — parity needs both option legs.

    This used to assert a second thing: that a chain with no separate cash *series* was
    also empty. It is not, and it should never have been — the chain carries the cash leg.
    That case is now
    ``test_a_lake_holding_only_a_chain_and_a_curve_still_answers``, which asserts the
    opposite outcome for the same lake, and the case this one still covers is unchanged.
    """
    no_chain = _lake(tmp_path, [_trade(_STOCK, _T1, 100.0, "p1"), *_curve_records()])
    assert equity_forward_basis(no_chain, _STOCK, *_WINDOW).is_empty()


def test_the_index_leg_is_the_chains_own_spot_and_not_a_stored_print(tmp_path: Path) -> None:
    """One snapshot, one spot: the mark and the index come off the same observation.

    The chain below carries 105.00 on every row while the stored cash series holds a
    day-old bar at 100.00. Taking the stored print made the answer a function of how old
    the cash series happened to be — the same snapshot read 0.0057 against a same-instant
    print and 0.5600 against this one, ten times the basis, with no column moving to say
    so. The crypto twin cannot express that: ``perp_basis`` reads mark and index off one
    ``derivative_ticker`` row.
    """
    catalog = _lake(
        tmp_path,
        [
            _bar(_STOCK, _NOW - _DAY_NS, 100.0),
            _option(_T2, 100.0, OptType.CALL, 5.4, 5.6, spot=105.0),
            _option(_T2, 100.0, OptType.PUT, 2.4, 2.6, spot=105.0),
            *_curve_records(),
        ],
    )
    row = equity_forward_basis(catalog, _STOCK, *_WINDOW).row(0, named=True)
    assert row["index_price"] == pytest.approx(105.0)
    horizon = (_EXPIRY - _T2) / _DAY_NS
    forward = 100.0 + 3.0 * (1.0 + _THREE_MONTH_ON_JAN_5 * horizon / DAYS_PER_YEAR)
    assert row["basis_pct"] == pytest.approx((forward - 105.0) / 105.0)


def test_a_lake_holding_only_a_chain_and_a_curve_still_answers(tmp_path: Path) -> None:
    """Every row carried a usable spot while the function returned nothing.

    ``price_leg`` was a precondition, so an ``options_chain`` partition with no ``trade``,
    ``ohlcv`` or ``index_value`` beside it produced zero rows — an empty answer reported
    for a lake that held both legs of the measurement.
    """
    catalog = _lake(
        tmp_path,
        [
            _option(_T2, 100.0, OptType.CALL, 5.4, 5.6, spot=100.0),
            _option(_T2, 100.0, OptType.PUT, 2.4, 2.6, spot=100.0),
            *_curve_records(),
        ],
    )
    frame = equity_forward_basis(catalog, _STOCK, *_WINDOW)
    assert len(frame) == 1
    assert frame["index_price"][0] == pytest.approx(100.0)


def test_a_chain_with_no_spot_of_its_own_is_no_forward(tmp_path: Path) -> None:
    """The cost of the decision above, stated: no contemporaneous cash leg, no row.

    A cash print from some other instant is available here and is deliberately not used —
    a bound on how stale it may be would need a number nothing publishes, which is the
    denominator ``_aggregate_of_an_undeclared_stream`` and ``book_resample`` both decline
    to invent.
    """
    catalog = _lake(
        tmp_path,
        [
            _trade(_STOCK, _T1, 100.0, "p1"),
            _option(_T2, 100.0, OptType.CALL, 5.4, 5.6, spot=None),
            _option(_T2, 100.0, OptType.PUT, 2.4, 2.6, spot=None),
            *_curve_records(),
        ],
    )
    assert equity_forward_basis(catalog, _STOCK, *_WINDOW).is_empty()


def test_the_spot_is_scoped_to_the_expiry_the_forward_is_read_off(tmp_path: Path) -> None:
    """One ``local_ts`` can hold several expiries, each fetched in its own request.

    ``yahoo/client.py`` issues one call per expiration and stamps them with one local
    instant, so the spot that came back with *these* strikes is the one on *these* rows.
    The nearest expiry is chosen first and its own spot is what the strike is measured
    against — the far expiry's 200.00 below must not select the 200-strike.
    """
    near = _EXPIRY
    far = _EXPIRY + 30 * _DAY_NS
    catalog = _lake(
        tmp_path,
        [
            _option(_T2, 100.0, OptType.CALL, 5.4, 5.6, spot=100.0, expiry=near),
            _option(_T2, 100.0, OptType.PUT, 2.4, 2.6, spot=100.0, expiry=near),
            _option(_T2, 200.0, OptType.CALL, 9.4, 9.6, spot=200.0, expiry=far),
            _option(_T2, 200.0, OptType.PUT, 1.4, 1.6, spot=200.0, expiry=far),
            *_curve_records(),
        ],
    )
    row = equity_forward_basis(catalog, _STOCK, *_WINDOW).row(0, named=True)
    assert row["expiry"] == near
    assert row["strike"] == pytest.approx(100.0)
    assert row["index_price"] == pytest.approx(100.0)


def test_an_expiry_already_past_the_snapshot_is_not_a_forward(tmp_path: Path) -> None:
    catalog = _lake(
        tmp_path,
        [
            _trade(_STOCK, _T1, 100.0, "p1"),
            _option(_T2, 100.0, OptType.CALL, 5.4, 5.6),
            _option(_T2, 100.0, OptType.PUT, 2.4, 2.6),
            *_curve_records(),
        ],
    )
    # Re-read the same lake but ask for a window whose chain rows all expire behind it.
    assert equity_forward_basis(catalog, _STOCK, _EXPIRY, _EXPIRY + _DAY_NS).is_empty()


# ---------------------------------------------------------------------------
# funding-apr — the equity cost of carry, in the crypto frame's columns
# ---------------------------------------------------------------------------


@pytest.fixture
def dividend_lake(tmp_path: Path) -> Iterator[Catalog]:
    yield _lake(
        tmp_path,
        [
            _bar(_STOCK, _NOW - 12 * 3600 * _SEC_NS, 200.0),
            _dividend(_NOW, 1.0),
            _bar(_STOCK, _NOW + 12 * 3600 * _SEC_NS, 200.0),
            _dividend(_NOW + _DAY_NS, 2.0),
            *_curve_records(),
        ],
    )


def test_funding_apr_returns_the_crypto_halfs_columns(dividend_lake: Catalog) -> None:
    frame = equity_funding_apr(dividend_lake, _STOCK, *_WINDOW)
    assert frame.columns[:5] == [
        "funding_ts",
        "funding_rate",
        "interval_hours",
        "apr",
        "cumulative_funding",
    ]


def test_a_received_dividend_is_a_negative_funding_rate(dividend_lake: Catalog) -> None:
    """Crypto's convention is that a positive rate means the long pays, and an equity long
    is paid. Flipping the sign here is what lets the two series share one axis."""
    frame = equity_funding_apr(dividend_lake, _STOCK, *_WINDOW)
    assert frame["funding_rate"].to_list() == [
        pytest.approx(-1.0 / 200.0),
        pytest.approx(-2.0 / 200.0),
    ]


def test_the_apr_is_the_same_annualisation_the_crypto_half_calls(
    dividend_lake: Catalog,
) -> None:
    from crocodile.core.analytics.carry import apr_from_rate

    frame = equity_funding_apr(dividend_lake, _STOCK, *_WINDOW)
    for row in frame.iter_rows(named=True):
        assert row["apr"] == pytest.approx(
            apr_from_rate(row["funding_rate"], row["interval_hours"])
        )


def test_the_period_is_the_gap_since_the_previous_event(dividend_lake: Catalog) -> None:
    """24 hours between the two dividends; the first measures from the window's start."""
    frame = equity_funding_apr(dividend_lake, _STOCK, *_WINDOW)
    assert frame["interval_hours"].to_list() == [24, 24]


def test_cumulative_funding_is_a_running_sum(dividend_lake: Catalog) -> None:
    frame = equity_funding_apr(dividend_lake, _STOCK, *_WINDOW)
    assert frame["cumulative_funding"].to_list() == [
        pytest.approx(-0.005),
        pytest.approx(-0.015),
    ]


def test_carry_apr_is_financing_paid_minus_dividends_received(
    dividend_lake: Catalog,
) -> None:
    """A positive number means carrying the shares costs money, which is the ordinary
    state of a stock yielding less than bills.

    The tenor here is the 1-month point and not the 3-month one, because the horizon a
    dividend period finances is the gap to the next payment — a day, in this fixture —
    and the curve lookup rounds up to the shortest published point that covers it. That
    the tenor tracks the measurement rather than the capability is the whole reason
    ``RiskFreeCurve.at`` takes a horizon.
    """
    row = equity_funding_apr(dividend_lake, _STOCK, *_WINDOW).row(0, named=True)
    assert row["risk_free_apr"] == pytest.approx(_ONE_MONTH_ON_JAN_5)
    assert row["carry_apr"] == pytest.approx(row["risk_free_apr"] + row["apr"])
    assert row["carry_apr"] < 0.0  # a 1.8 %/day dividend run-rate swamps 5.46 %/yr


def test_the_dividend_leg_still_answers_without_a_curve(tmp_path: Path) -> None:
    """This is the one of the three carry capabilities that degrades rather than empties:
    the dividend yield is a real measurement whether or not financing is known."""
    catalog = _lake(tmp_path, [_bar(_STOCK, _NOW - _SEC_NS, 200.0), _dividend(_NOW, 1.0)])
    row = equity_funding_apr(catalog, _STOCK, *_WINDOW).row(0, named=True)
    assert row["funding_rate"] == pytest.approx(-0.005)
    assert row["risk_free_apr"] is None
    assert row["carry_apr"] is None
    assert row["prov_confidence"] == pytest.approx(2.0 / 3.0)


def test_a_symbol_with_no_dividends_or_no_price_is_empty(tmp_path: Path) -> None:
    no_dividend = _lake(tmp_path / "a", [_bar(_STOCK, _NOW, 200.0), *_curve_records()])
    assert equity_funding_apr(no_dividend, _STOCK, *_WINDOW).is_empty()
    no_price = _lake(tmp_path / "b", [_dividend(_NOW, 1.0), *_curve_records()])
    assert equity_funding_apr(no_price, _STOCK, *_WINDOW).is_empty()


def test_a_split_is_not_a_dividend(tmp_path: Path) -> None:
    """``corp_action`` carries both, and a 2-for-1 split's ``value`` of 2.0 read as a cash
    dividend on a 200-dollar share would report a 1 % payment nobody made."""
    split = CorporateAction(
        source="tiingo",
        symbol=_STOCK,
        symbol_raw=_STOCK,
        local_ts=_NOW,
        asset_class=AssetClass.EQUITY,
        source_ts=_NOW,
        ex_date="2024-01-08",
        type=CorpActionType.SPLIT,
        value=2.0,
    )
    catalog = _lake(tmp_path, [_bar(_STOCK, _NOW - _SEC_NS, 200.0), split, *_curve_records()])
    assert equity_funding_apr(catalog, _STOCK, *_WINDOW).is_empty()
