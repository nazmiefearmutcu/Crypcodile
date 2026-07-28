"""The Form 4 ownership parser, against checked-in filings.

Offline by construction: :func:`~crocodile.equity.providers.sec_edgar.form4.parse_form4` is
XML in and records out, with the observation timestamp passed rather than read off a clock,
so a fixture pins whole records instead of every field but one. Nothing here reaches EDGAR —
which is not only a speed decision. EDGAR rate-limits by User-Agent and answers a burst with
403, so a suite that fetched would be a suite that fails on somebody else's schedule.
"""

from __future__ import annotations

import math
import pathlib

import pytest

from crocodile.core.schema.enums import AssetClass, Channel
from crocodile.core.schema.provenance import Provenance, confidence_for
from crocodile.core.schema.records import Filing
from crocodile.equity.providers.sec_edgar import Form4ParseError, SecEdgarClient, parse_form4

_FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "sec_edgar"
_LOCAL_TS = 1_717_200_000_000_000_000  # 2024-06-01 00:00:00 UTC


def _fixture(name: str) -> str:
    return (_FIXTURES / name).read_text(encoding="utf-8")


def _officer_filing() -> list:
    return parse_form4(_fixture("form4_officer.xml"), local_ts=_LOCAL_TS)


def test_only_table_one_transactions_become_records() -> None:
    """Three transaction-shaped elements in the fixture and one of them is not a Table I line.

    The document carries two ``nonDerivativeTransaction`` lines, one ``nonDerivativeHolding``
    and one ``derivativeTransaction``. A holding is not a transaction, and a derivative line's
    ``transactionShares`` counts the underlying while its ``transactionPricePerShare`` is the
    price of the derivative — ``0`` on the RSU grant in this fixture — so multiplying them
    would put a $0 notional next to a $36M one in the same ``value`` column with no column
    saying which table each came from.
    """
    records = _officer_filing()
    assert [r.transaction_type for r in records] == ["S", "G"]


def test_the_priced_sale_carries_every_number_the_filing_states() -> None:
    sale = _officer_filing()[0]
    assert sale.symbol == "EXCO"
    assert sale.symbol_raw == "EXCO"
    assert sale.source == "sec_edgar"
    assert sale.asset_class is AssetClass.EQUITY
    assert sale.insider_name == "DOE JANE Q"
    assert sale.insider_cik == "0001051401"
    assert sale.transaction_date == "2024-05-03"
    assert sale.acquired_disposed == "D"
    assert sale.ownership == "D"
    assert sale.shares == 196410.0
    assert sale.price == 183.2143
    assert math.isclose(sale.value or 0.0, 196410.0 * 183.2143)
    assert sale.local_ts == _LOCAL_TS
    # A Form 4 stamps calendar dates and no time of day; midnight would be an instant the
    # document does not claim. The date it does claim is on `transaction_date`.
    assert sale.source_ts is None


def test_every_relationship_the_filer_checked_survives_into_position() -> None:
    """The fixture's owner is both an officer and a director, and ``position`` is one field.

    Picking one would drop a fact the filing states, and picking the officer title happens to
    be the one that reads best — which is exactly why it needs a test rather than a habit.
    """
    assert _officer_filing()[0].position == "Chief Executive Officer, Director"


def test_a_gift_reports_shares_and_no_price_and_says_so_in_its_confidence() -> None:
    """The case ``sec_form4``'s formula exists for.

    Code ``G`` states a share count and leaves ``transactionPricePerShare`` empty, so one of
    the two boxes a USD notional needs is filled. The record's ``value`` stays ``None`` rather
    than becoming ``0.0`` — 5 000 shares changing hands for nothing is a number where the
    truth is a hole — and the tail says the line is half-sampled for the measurement
    ``whale-alerts`` thresholds on.
    """
    gift = _officer_filing()[1]
    assert gift.shares == 5000.0
    assert gift.price is None
    assert gift.value is None
    assert gift.prov is Provenance.NATIVE
    assert gift.prov_basis == "sec_form4"
    assert gift.prov_confidence == 0.5
    assert gift.prov_inputs == ["insider"]


def test_a_fully_priced_line_saturates_the_same_formula() -> None:
    """Both ends of the ratio, so 0.5 is a point on a scale rather than a magic number."""
    sale = _officer_filing()[0]
    assert sale.prov_confidence == 1.0
    assert confidence_for("sec_form4", {"n_reported_amounts": 0}) == 0.0


def test_the_tail_is_never_the_headers_default() -> None:
    """``prov_basis='native'`` would claim a fully-sampled venue reading on every line.

    The header defaults to exactly that, so a parser that forgot the tail would ship a
    price-less gift at ``prov_confidence=1.0`` and a consumer filtering on the number would
    see nothing wrong. Asserted on the whole batch rather than on one record because
    forgetting it on one branch is the realistic version of this bug.
    """
    for record in _officer_filing():
        assert record.prov_basis == "sec_form4"


def test_a_joint_filing_reports_the_shares_once() -> None:
    """Two reporting owners, one transaction, and the shares moved once.

    Emitting the line per owner would raise two whale alerts for one purchase and double the
    notional in every sum over the channel. The cost of the choice is the other direction and
    is real: a watchlist naming only ``Cascade GP LLC`` misses this filing.
    """
    records = parse_form4(_fixture("form4_joint_purchase.xml"), local_ts=_LOCAL_TS)
    assert len(records) == 1
    assert records[0].insider_name == "Cascade Partners LP"
    assert records[0].insider_cik == "0000933136"
    assert records[0].position == "10% Owner"
    assert records[0].acquired_disposed == "A"


def test_the_record_lands_on_the_channel_the_lake_reads_it_back_from() -> None:
    """A record whose tag no reader asks for is data written into a directory nobody opens."""
    assert _officer_filing()[0].__struct_config__.tag == Channel.INSIDER.value


@pytest.mark.parametrize(
    ("payload", "why"),
    [
        ("<ownershipDocument", "not well-formed"),
        ("<notAForm4><documentType>4</documentType></notAForm4>", "no reportingOwner"),
        ("<ownershipDocument><issuer/></ownershipDocument>", "no documentType"),
    ],
)
def test_a_payload_that_is_not_a_form_4_raises_rather_than_answering_empty(
    payload: str, why: str
) -> None:
    """An empty list is a real answer — a Form 4 can report holdings only — so it cannot
    also be the answer for a truncated download. Two different facts need two outcomes."""
    with pytest.raises(Form4ParseError):
        parse_form4(payload, local_ts=_LOCAL_TS)


def test_the_client_asks_for_the_raw_xml_and_not_the_rendered_view() -> None:
    """``primaryDocument`` names an XSL-rendered HTML view under an ``xsl…/`` prefix.

    Fetching that yields a well-formed document with no ``ownershipDocument`` in it, so the
    symptom is an insider who never traded rather than an error — the failure mode this tree
    treats as the dangerous one. Stripping the prefix is the whole fix and it is easy to lose.
    """
    filing = Filing(
        source="sec_edgar",
        symbol="EXCO",
        symbol_raw="EXCO",
        local_ts=_LOCAL_TS,
        source_ts=None,
        asset_class=AssetClass.EQUITY,
        accession_number="0001234567-24-000042",
        form="4",
        filing_date="2024-05-06",
        primary_document="xslF345X03/wf-form4_171492.xml",
        document_url="",
    )
    url = SecEdgarClient.raw_ownership_document_url(1234567, filing)
    assert url == (
        "https://www.sec.gov/Archives/edgar/data/1234567/000123456724000042/wf-form4_171492.xml"
    )
