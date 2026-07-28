"""The 13F-HR cover-page and information-table parsers, against checked-in filings.

Offline for the reason ``test_sec_edgar_form4`` gives, and with one extra subject of its own:
the two fixtures spell the information-table namespace differently on purpose. The 2024Q1
table binds it to an ``ns1:`` prefix and the 2024Q2 table makes it the default namespace,
which are both live spellings of the same schema. A parser that matched on the qualified tag
would answer one of them with an empty list — a manager holding nothing — rather than an
error.
"""

from __future__ import annotations

import pathlib

import pytest

from crocodile.core.schema.enums import AssetClass, Channel
from crocodile.core.schema.provenance import Provenance, confidence_for
from crocodile.equity.providers.sec_edgar import (
    Form13FParseError,
    parse_13f_information_table,
    parse_13f_primary_document,
)

_FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "sec_edgar"
_LOCAL_TS = 1_717_200_000_000_000_000  # 2024-06-01 00:00:00 UTC
_ACCESSION = "0000933136-24-000011"


def _fixture(name: str) -> str:
    return (_FIXTURES / name).read_text(encoding="utf-8")


def _cover():
    return parse_13f_primary_document(_fixture("form13f_primary_doc.xml"))


def _holdings(filing_date: str = "2024-05-15", cover=None):
    return parse_13f_information_table(
        _fixture("form13f_infotable_2024q1.xml"),
        cover=cover or _cover(),
        filing_date=filing_date,
        accession_number=_ACCESSION,
        local_ts=_LOCAL_TS,
    )


def test_the_cover_page_normalises_the_one_date_format_nothing_else_here_uses() -> None:
    """``periodOfReport`` is ``MM-DD-YYYY`` and every other date in this tree is ISO.

    Left as filed it would sort wrong, compare wrong against the submissions index's
    ``reportDate``, and — because the disclosure lag is a subtraction of two dates — produce
    a confidence computed from a misread quarter.
    """
    cover = _cover()
    assert cover.report_date == "2024-03-31"
    assert cover.manager_name == "Cascade Partners LP"
    assert cover.manager_cik == "0000933136"
    assert cover.report_type == "13F HOLDINGS REPORT"


def test_a_cover_page_with_no_manager_is_refused() -> None:
    """The name and the period are what every holdings row is stamped with, so a table
    parsed without them would carry an unattributed position and an unmeasurable lag."""
    with pytest.raises(Form13FParseError):
        parse_13f_primary_document("<edgarSubmission><headerData/></edgarSubmission>")


def test_each_reported_position_becomes_a_record_stamped_with_the_cover_page() -> None:
    holdings = _holdings()
    assert [h.issuer_name for h in holdings] == ["EXAMPLE INDUSTRIES INC", "SECOND HOLDING CORP"]
    first = holdings[0]
    assert first.source == "sec_edgar"
    assert first.asset_class is AssetClass.EQUITY
    assert first.manager_name == "Cascade Partners LP"
    assert first.manager_cik == "0000933136"
    assert first.shares == 789368450.0
    assert first.shares_type == "SH"
    assert first.discretion == "DFND"
    assert (first.voting_sole, first.voting_shared, first.voting_none) == (789368450.0, 0.0, 0.0)
    assert first.report_date == "2024-03-31"
    assert first.accession_number == _ACCESSION
    assert first.local_ts == _LOCAL_TS
    assert first.source_ts is None
    assert first.__struct_config__.tag == Channel.HOLDING_13F.value


def test_the_symbol_is_the_cusip_because_the_table_states_no_ticker() -> None:
    """A 13F identifies its issuer by CUSIP and by name and never by ticker.

    Mapping one to the other needs a reference table this parser is not given, and inventing
    a ticker here would be a join performed with no evidence — silently wrong for every
    issuer whose CUSIP maps to more than one listed class. The canonical symbol is upper-cased
    so a lake groups the two fixtures' ``30161n101`` and ``30161N101`` together, while
    ``cusip`` keeps what the filing printed.
    """
    first = _holdings()[0]
    assert first.symbol == "30161N101"
    assert first.symbol_raw == "30161n101"
    assert first.cusip == "30161n101"


def test_a_row_missing_its_value_is_skipped_rather_than_zeroed() -> None:
    """``value`` and ``shares`` are required fields, so a blank has nowhere honest to go.

    The fixture's third row states a share count and no value. Filing it at ``0.0`` would put
    a position of no worth into ``SELECT sum(value)`` as a fact, and — because the value is
    what ``smart-money`` differences — would report the manager as having sold the whole
    position the next quarter.
    """
    assert len(_holdings()) == 2
    assert "NO VALUE REPORTED CO" not in {h.issuer_name for h in _holdings()}


def test_the_default_namespace_spelling_parses_to_the_same_thing() -> None:
    """The 2024Q2 fixture binds the schema as the default namespace instead of to ``ns1:``."""
    holdings = parse_13f_information_table(
        _fixture("form13f_infotable_2024q2.xml"),
        cover=_cover()._replace(report_date="2024-06-30"),
        filing_date="2024-08-14",
        accession_number="0000933136-24-000019",
        local_ts=_LOCAL_TS,
    )
    assert [h.cusip for h in holdings] == ["30161N101", "02079K305"]
    assert holdings[0].value == 150360898.0


def test_the_value_column_is_normalised_across_the_whole_dollar_amendment() -> None:
    """Column 5 was thousands of dollars until the amendment and whole dollars from it.

    The XML is byte-identical either side of the boundary — ``135360898`` is $135M in one era
    and $135bn in the other — so a lake that stored the number as filed would hold two units
    in one column and every threshold across the boundary would be wrong by a thousand. The
    era is a function of the *filing* date, which is why it is a parameter rather than
    something read off the report date: a manager filing in February 2023 for the December
    2022 quarter files whole dollars for a quarter whose earlier peers were in thousands.
    """
    modern = _holdings(filing_date="2024-05-15")[0]
    legacy = _holdings(
        filing_date="2022-05-13", cover=_cover()._replace(report_date="2022-03-31")
    )[0]
    assert modern.value == 135360898.0
    assert legacy.value == 135360898000.0


def test_the_confidence_measures_how_long_the_table_was_withheld() -> None:
    """Rule 13f-1 allows 45 days, and the fixture's own signature date is the deadline.

    2024-03-31 to 2024-05-15 is exactly 45 calendar days, so this filing scores 0.0: as a
    description of where the portfolio is *now* it carries no sampling evidence at all. The
    same table filed a month earlier scores two thirds. Neither number says the positions are
    false — that claim is ``prov``'s, and it stays NATIVE at both ends.
    """
    at_the_deadline = _holdings(filing_date="2024-05-15")[0]
    assert at_the_deadline.prov is Provenance.NATIVE
    assert at_the_deadline.prov_basis == "sec_13f_hr"
    assert at_the_deadline.prov_confidence == 0.0
    assert at_the_deadline.prov_inputs == ["holding_13f"]

    prompt = _holdings(filing_date="2024-04-15")[0]
    assert prompt.prov_confidence == pytest.approx(1.0 - 15 / 45)
    assert confidence_for("sec_13f_hr", {"disclosure_lag_days": 0}) == 1.0


def test_a_filing_stamped_before_its_own_quarter_end_does_not_score_above_one() -> None:
    """A negative lag is a defect in the document, and the formula's ceiling is 1.0.

    Left unclamped it would drive ``sec_13f_hr`` past 1.0, which ``confidence_for`` rejects
    as a broken *formula* — turning a malformed date on one filing into a crash a long way
    from the document that carried it.
    """
    early = _holdings(filing_date="2024-03-01")[0]
    assert early.prov_confidence == 1.0


def test_an_unreadable_filing_date_is_refused_rather_than_guessed() -> None:
    """It settles two unrelated things and neither has a safe default: which unit the value
    column is in, and how long the table was withheld."""
    with pytest.raises(Form13FParseError):
        _holdings(filing_date="not-a-date")


def test_a_row_that_states_no_share_type_is_skipped_rather_than_called_shares() -> None:
    """``sshPrnamtType`` is the unit of ``sshPrnamt``, and it was defaulted to ``"SH"``.

    The other values it takes are ``PRN`` and ``CALL``. A note reported as ``10000000``
    ``PRN`` is ten million dollars of face value; the same row with the type element absent
    and the default applied is ten million *shares* of an instrument that has no shares —
    a quantity the filer never stated, spelled identically to one they did, so nothing
    downstream can tell it from a real share count.

    The parser's own contract two paragraphs up says required fields are skipped rather
    than defaulted. This was the one field that was not.
    """
    xml = """<?xml version="1.0"?>
    <informationTable xmlns="http://www.sec.gov/edgar/document/thirteenf/informationtable">
      <infoTable>
        <nameOfIssuer>NOTE ISSUER CO</nameOfIssuer>
        <cusip>30161N101</cusip>
        <value>10000000</value>
        <shrsOrPrnAmt><sshPrnamt>10000000</sshPrnamt></shrsOrPrnAmt>
        <investmentDiscretion>SOLE</investmentDiscretion>
      </infoTable>
      <infoTable>
        <nameOfIssuer>ORDINARY EQUITY CO</nameOfIssuer>
        <cusip>02079K305</cusip>
        <value>250000</value>
        <shrsOrPrnAmt><sshPrnamt>5000</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt>
        <investmentDiscretion>SOLE</investmentDiscretion>
      </infoTable>
    </informationTable>
    """
    holdings = parse_13f_information_table(
        xml,
        cover=_cover(),
        filing_date="2024-05-15",
        accession_number=_ACCESSION,
        local_ts=_LOCAL_TS,
    )

    # Before: two rows, the first of them `shares=10000000.0, shares_type="SH"`.
    assert [h.issuer_name for h in holdings] == ["ORDINARY EQUITY CO"]
    assert [(h.shares, h.shares_type) for h in holdings] == [(5000.0, "SH")]


def test_a_stated_share_type_other_than_shares_survives_unchanged() -> None:
    """The guard is about an absent element, not about refusing the units EDGAR defines.

    A row that says ``PRN`` states its unit, and skipping it would lose a position the
    filer reported in full. Only silence is refused.
    """
    xml = """<?xml version="1.0"?>
    <informationTable xmlns="http://www.sec.gov/edgar/document/thirteenf/informationtable">
      <infoTable>
        <nameOfIssuer>NOTE ISSUER CO</nameOfIssuer>
        <cusip>30161N101</cusip>
        <value>10000000</value>
        <shrsOrPrnAmt>
          <sshPrnamt>10000000</sshPrnamt><sshPrnamtType>PRN</sshPrnamtType>
        </shrsOrPrnAmt>
      </infoTable>
    </informationTable>
    """
    holdings = parse_13f_information_table(
        xml,
        cover=_cover(),
        filing_date="2024-05-15",
        accession_number=_ACCESSION,
        local_ts=_LOCAL_TS,
    )
    assert [(h.shares, h.shares_type) for h in holdings] == [(10000000.0, "PRN")]
