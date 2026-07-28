"""The keyless Treasury par yield curve, driven entirely off a checked-in response.

The fixture is a real four-day slice of ``daily-treasury-rates.csv`` for 2024, kept
verbatim including the two things that make the parser interesting: the ``1.5 Month``
column, which was added mid-file after the parser's first eleven tenors were written, and
the empty and ``N/A`` cells Treasury leaves for a tenor it did not publish that day.

Nothing here opens a socket. :func:`parse_par_yield_csv` is a function over a string, and
the one test that exercises the client drives it through a fake session — which is the
whole reason the session is a constructor parameter.
"""

from __future__ import annotations

import asyncio
import pathlib
from typing import Any

import pytest

from crocodile.core.schema.enums import AssetClass
from crocodile.core.schema.provenance import Provenance
from crocodile.equity.providers.treasury import (
    TreasuryYieldClient,
    parse_par_yield_csv,
    parse_tenor,
    tenor_days,
)

_FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "treasury_par_yield_2024.csv"

# 2024-01-02 00:00:00 UTC, which is the first date in the fixture.
_JAN_2 = 1_704_153_600_000_000_000
_DAY_NS = 86_400_000_000_000
_LOCAL_TS = 1_720_000_000_000_000_000

_TENORS_IN_FIXTURE = 14
_DATES_IN_FIXTURE = 4
# 1.5 Month is published on one of the four days; the other three are blank or N/A.
_MISSING_CELLS = 3


@pytest.fixture
def csv_text() -> str:
    return _FIXTURE.read_text()


# ---------------------------------------------------------------------------
# Tenor naming — the part a new Treasury column lands on
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("header", "symbol"),
    [
        ("1 Mo", "treasury:UST1M"),
        ("1.5 Month", "treasury:UST1_5M"),
        ("4 Mo", "treasury:UST4M"),
        ("6 Months", "treasury:UST6M"),
        ("1 Yr", "treasury:UST1Y"),
        ("10 Yr", "treasury:UST10Y"),
        ("30 Year", "treasury:UST30Y"),
    ],
)
def test_a_tenor_header_becomes_a_symbol(header: str, symbol: str) -> None:
    parsed = parse_tenor(header)
    assert parsed is not None
    assert parsed.symbol == symbol
    assert parsed.header == header.strip()


def test_a_fractional_tenor_keeps_its_value_without_a_dot_in_the_symbol() -> None:
    """A dot in a symbol reads as a file extension to every glob in the store layer."""
    parsed = parse_tenor("1.5 Month")
    assert parsed is not None
    assert "." not in parsed.symbol
    assert parsed.days == pytest.approx(1.5 * 365.0 / 12.0)


@pytest.mark.parametrize("header", ["Date", "", "Coupon", "10", "Yr", "1 Fortnight"])
def test_a_column_that_is_not_a_tenor_is_declined_rather_than_guessed(header: str) -> None:
    assert parse_tenor(header) is None


@pytest.mark.parametrize("header", ["1 Mo", "1.5 Month", "3 Mo", "2 Yr", "30 Yr"])
def test_tenor_days_inverts_the_naming_rule(header: str) -> None:
    """The carry analytics rank stored yields by tenor and must not keep a second table."""
    parsed = parse_tenor(header)
    assert parsed is not None
    assert tenor_days(parsed.symbol) == pytest.approx(parsed.days)


@pytest.mark.parametrize("symbol", ["AAPL", "treasury:", "treasury:UST", "treasury:USTxM", "^SPX"])
def test_tenor_days_answers_none_for_a_symbol_it_did_not_mint(symbol: str) -> None:
    assert tenor_days(symbol) is None


def test_the_ordering_of_tenors_is_by_length_not_by_name() -> None:
    """``UST1Y`` sorts before ``UST3M`` alphabetically and is four times as long."""
    assert tenor_days("treasury:UST3M") is not None
    assert tenor_days("treasury:UST1Y") is not None
    assert tenor_days("treasury:UST3M") < tenor_days("treasury:UST1Y")  # type: ignore[operator]


# ---------------------------------------------------------------------------
# Parsing the published file
# ---------------------------------------------------------------------------


def test_every_published_cell_becomes_one_record(csv_text: str) -> None:
    records = parse_par_yield_csv(csv_text, local_ts=_LOCAL_TS)
    assert len(records) == _TENORS_IN_FIXTURE * _DATES_IN_FIXTURE - _MISSING_CELLS


def test_a_blank_or_na_cell_yields_no_record_rather_than_a_zero(csv_text: str) -> None:
    """A zero there is a yield of zero, which would make a carry look unfinanced.

    ``1.5 Month`` is blank on two days and ``N/A`` on a third; only 01/05 published one.
    """
    records = parse_par_yield_csv(csv_text, local_ts=_LOCAL_TS)
    one_and_a_half = [r for r in records if r.symbol == "treasury:UST1_5M"]
    assert len(one_and_a_half) == 1
    assert one_and_a_half[0].date == "2024-01-05"
    assert all(r.value is not None for r in records)


def test_a_published_percentage_is_stored_as_a_decimal_fraction(csv_text: str) -> None:
    """4.05 % becomes 0.0405, which is the unit every other rate in this tree carries.

    Storing the percent would put two units in one ``value`` column and make the
    subtraction that turns a basis into a carry wrong by a factor of a hundred, with
    nothing on the row to notice it by.
    """
    records = parse_par_yield_csv(csv_text, local_ts=_LOCAL_TS)
    ten_year = {r.date: r.value for r in records if r.symbol == "treasury:UST10Y"}
    assert ten_year["2024-01-05"] == pytest.approx(0.0405)
    assert ten_year["2024-01-02"] == pytest.approx(0.0395)


def test_the_date_is_iso_and_the_instant_is_that_date_at_utc_midnight(csv_text: str) -> None:
    """The file states a date and no time of day, so the instant is the date boundary."""
    records = parse_par_yield_csv(csv_text, local_ts=_LOCAL_TS)
    first = next(r for r in records if r.date == "2024-01-02")
    assert first.source_ts == _JAN_2
    last = next(r for r in records if r.date == "2024-01-05")
    assert last.source_ts == _JAN_2 + 3 * _DAY_NS


def test_the_header_contract_is_kept_on_every_record(csv_text: str) -> None:
    records = parse_par_yield_csv(csv_text, local_ts=_LOCAL_TS)
    for record in records:
        assert record.source == "treasury"
        assert record.asset_class is AssetClass.EQUITY
        assert record.local_ts == _LOCAL_TS
        assert record.symbol.startswith("treasury:UST")


def test_the_raw_symbol_is_the_column_header_the_file_spelled(csv_text: str) -> None:
    records = parse_par_yield_csv(csv_text, local_ts=_LOCAL_TS)
    by_symbol = {r.symbol: r.symbol_raw for r in records}
    assert by_symbol["treasury:UST1_5M"] == "1.5 Month"
    assert by_symbol["treasury:UST10Y"] == "10 Yr"


def test_the_records_are_native_because_the_treasury_published_every_number(
    csv_text: str,
) -> None:
    """A division by a hundred is a unit conversion, not a derivation.

    Stated explicitly through ``provenance_fields`` rather than inherited from the header
    default, so the claim is one this module makes rather than one it fell into — the
    distinction ``core.resample.book`` was caught on.
    """
    records = parse_par_yield_csv(csv_text, local_ts=_LOCAL_TS)
    assert {r.prov for r in records} == {Provenance.NATIVE}
    assert {r.prov_basis for r in records} == {"native"}
    assert {r.prov_confidence for r in records} == {1.0}


def test_iso_dates_parse_too() -> None:
    """The same parser is pointed at the JSON API's ``record_date`` by anyone who
    prefers that source, and refusing the unambiguous spelling would be perverse."""
    records = parse_par_yield_csv("Date,3 Mo\n2024-01-02,5.49\n", local_ts=_LOCAL_TS)
    assert len(records) == 1
    assert records[0].date == "2024-01-02"
    assert records[0].source_ts == _JAN_2


@pytest.mark.parametrize(
    "text",
    ["", "Date,3 Mo\n", "3 Mo,6 Mo\n5.49,5.29\n", "Date,Coupon\n01/02/2024,3.5\n"],
)
def test_a_file_this_cannot_read_yields_nothing_rather_than_raising(text: str) -> None:
    """An empty file, a header with no rows, a file with no Date column, and a file whose
    only non-date column is not a tenor. All four are states the endpoint can return
    behind a redirect or an outage page, and none of them is a crash."""
    assert parse_par_yield_csv(text, local_ts=_LOCAL_TS) == []


def test_an_unparseable_date_skips_its_row_and_keeps_the_others() -> None:
    text = "Date,3 Mo\n01/02/2024,5.49\nnot-a-date,5.50\n01/03/2024,5.48\n"
    records = parse_par_yield_csv(text, local_ts=_LOCAL_TS)
    assert [r.date for r in records] == ["2024-01-02", "2024-01-03"]


def test_a_short_row_does_not_index_past_its_end() -> None:
    """Treasury has shipped truncated rows on days a tenor was retired mid-file."""
    text = "Date,3 Mo,10 Yr\n01/02/2024,5.49\n01/03/2024,5.48,3.91\n"
    records = parse_par_yield_csv(text, local_ts=_LOCAL_TS)
    assert len(records) == 3


# ---------------------------------------------------------------------------
# The client, over a fake session
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, status: int, body: str) -> None:
        self.status = status
        self._body = body

    async def __aenter__(self) -> _FakeResponse:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def text(self) -> str:
        return self._body


class _FakeSession:
    """Answers one URL and records what it was asked for. Never touches a socket."""

    def __init__(self, body: str, status: int = 200) -> None:
        self._body = body
        self._status = status
        self.closed = False
        self.urls: list[str] = []

    def get(self, url: str, **_: Any) -> _FakeResponse:
        self.urls.append(url)
        return _FakeResponse(self._status, self._body)

    async def close(self) -> None:
        self.closed = True


def test_the_client_parses_what_the_endpoint_returns(csv_text: str) -> None:
    session = _FakeSession(csv_text)
    client = TreasuryYieldClient(session=session)  # type: ignore[arg-type]
    records = asyncio.run(client.par_yield_curve(2024, local_ts=_LOCAL_TS))
    assert len(records) == _TENORS_IN_FIXTURE * _DATES_IN_FIXTURE - _MISSING_CELLS
    assert "2024" in session.urls[0]


def test_a_failed_request_raises_rather_than_reading_as_an_empty_curve(csv_text: str) -> None:
    """An unreachable Treasury and a Treasury that published nothing are different facts.

    The carry analytics report an absent risk-free leg on the row, so turning a 503 into
    "no yield published" would put the second into the first's column.
    """
    client = TreasuryYieldClient(session=_FakeSession(csv_text, status=503))  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="HTTP 503"):
        asyncio.run(client.par_yield_curve(2024))


def test_close_leaves_an_injected_session_alone(csv_text: str) -> None:
    """A caller that handed in a session owns it; closing it under them would be rude and
    would break the next request they make on it."""
    session = _FakeSession(csv_text)
    client = TreasuryYieldClient(session=session)  # type: ignore[arg-type]
    asyncio.run(client.close())
    assert session.closed is False


def test_backfill_filters_on_the_publication_date_not_the_fetch_instant(csv_text: str) -> None:
    """A backfill asked for two days must not answer with the whole year it fetched."""
    session = _FakeSession(csv_text)
    client = TreasuryYieldClient(session=session)  # type: ignore[arg-type]
    records = asyncio.run(
        client.backfill(_JAN_2, _JAN_2 + _DAY_NS, local_ts=_LOCAL_TS)
    )
    assert {r.date for r in records} == {"2024-01-02", "2024-01-03"}
    # Every record shares the same local_ts — which is exactly why the filter cannot be
    # on local_ts, and is the failure the equity carry module's yield lookup avoids.
    assert {r.local_ts for r in records} == {_LOCAL_TS}


def test_backfill_of_an_inverted_range_is_empty_rather_than_a_year_of_requests(
    csv_text: str,
) -> None:
    session = _FakeSession(csv_text)
    client = TreasuryYieldClient(session=session)  # type: ignore[arg-type]
    assert asyncio.run(client.backfill(_JAN_2 + _DAY_NS, _JAN_2)) == []
    assert session.urls == []
