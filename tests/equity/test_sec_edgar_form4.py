"""The Form 4 ownership parser, against checked-in filings.

Offline by construction: :func:`~crocodile.equity.providers.sec_edgar.form4.parse_form4` is
XML in and records out, with the observation timestamp passed rather than read off a clock,
so a fixture pins whole records instead of every field but one. Nothing here reaches EDGAR —
which is not only a speed decision. EDGAR rate-limits by User-Agent and answers a burst with
403, so a suite that fetched would be a suite that fails on somebody else's schedule.
"""

from __future__ import annotations

import asyncio
import math
import pathlib
from unittest.mock import AsyncMock, patch

import aiohttp
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


# ---------------------------------------------------------------------------
# The batch that a timeout must not end
# ---------------------------------------------------------------------------


def _form4_filing(accession: str) -> Filing:
    return Filing(
        source="sec_edgar",
        symbol="EXCO",
        symbol_raw="EXCO",
        local_ts=_LOCAL_TS,
        source_ts=None,
        asset_class=AssetClass.EQUITY,
        accession_number=accession,
        form="4",
        filing_date="2024-05-03",
        primary_document=f"xslF345X03/wf-form4_{accession}.xml",
        document_url=f"https://www.sec.gov/Archives/{accession}.xml",
    )


def test_asyncio_timeout_is_not_an_aiohttp_client_error() -> None:
    """The fact the two handlers below rest on, asserted rather than assumed.

    ``aiohttp.ClientError`` covers ``ServerTimeoutError`` and its two subclasses, and those
    come from connect and socket-read deadlines. The ``total`` deadline on
    ``ClientTimeout`` does not raise them — it raises the bare ``asyncio.TimeoutError``,
    which since 3.11 is ``builtins.TimeoutError`` and inherits from ``OSError``, not from
    anything of aiohttp's. If a future aiohttp reparents it, this fails and the handlers
    can drop the extra name; until then they need it.
    """
    assert asyncio.TimeoutError is TimeoutError
    assert not issubclass(TimeoutError, aiohttp.ClientError)
    assert issubclass(aiohttp.ServerTimeoutError, aiohttp.ClientError)


@pytest.mark.asyncio
async def test_one_filing_timing_out_does_not_lose_the_rest_of_the_batch() -> None:
    """"Logged and skipped rather than failing the batch" — for the failure most likely to happen.

    The handler caught ``(Form4ParseError, aiohttp.ClientError)``, and a request that
    exceeded ``ClientTimeout(total=60.0)`` raises neither. So the one shape a
    forty-request batch against a rate-limited EDGAR actually hits was the one shape that
    escaped, and it took every other filing's transactions with it: two filings parsed
    fine here, and the caller received an exception instead of their four records.
    """
    client = SecEdgarClient()
    client._ticker_to_cik["EXCO"] = 1234
    filings = [_form4_filing("0000000000-24-00000" + str(i)) for i in range(3)]

    with (
        patch.object(client, "ensure_ticker_map", new_callable=AsyncMock),
        patch.object(client, "get_filings", new_callable=AsyncMock) as mock_filings,
        patch.object(client, "_request_text", new_callable=AsyncMock) as mock_text,
    ):
        mock_filings.return_value = filings
        mock_text.side_effect = [
            _fixture("form4_officer.xml"),
            # What `ClientTimeout(total=...)` raises. Spelled with the builtin name
            # because that is what `asyncio.TimeoutError` has been since 3.11 — the test
            # above pins the identity, and this is the same object either way.
            TimeoutError(),
            _fixture("form4_officer.xml"),
        ]
        records = await client.get_insider_transactions("EXCO")

    # Before: `TimeoutError` out of `get_insider_transactions`, and 0 records reached the
    # caller where the two readable filings held 4 between them.
    assert len(records) == 4
    assert [r.transaction_type for r in records] == ["S", "G", "S", "G"]


@pytest.mark.asyncio
async def test_a_body_that_stalls_is_retried_rather_than_surfaced() -> None:
    """``session.get`` returns on headers; the body read is the half the deadline governs.

    The read used to sit after the retry loop had already returned, so a stalled body had
    five unused attempts above it and failed the request outright. It is inside the loop
    now, and the whole request is reissued.
    """
    client = SecEdgarClient()
    reads = [TimeoutError(), b"<ownershipDocument/>"]

    class _Response:
        status = 200

        def raise_for_status(self) -> None:
            return None

        def close(self) -> None:
            return None

        async def read(self) -> bytes:
            outcome = reads.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

    class _Session:
        closed = False

        async def get(self, *_args: object, **_kwargs: object) -> _Response:
            return _Response()

    with (
        patch.object(client, "_get_session", return_value=_Session()),
        patch.object(asyncio, "sleep", new_callable=AsyncMock),
    ):
        body = await client._request_text("https://www.sec.gov/anything.xml")

    assert body == "<ownershipDocument/>"
    assert reads == []


@pytest.mark.asyncio
async def test_a_status_the_server_meant_is_not_retried() -> None:
    """A 404 cannot become a 200 by waiting, and five backoffs before saying so is a minute.

    The guard on moving the body read inside the retry loop: ``raise_for_status`` moved in
    with it, and without the ``ClientResponseError`` re-raise every missing attachment
    would now cost the full backoff ladder before failing.
    """
    client = SecEdgarClient()
    attempts = 0

    class _Response:
        status = 404

        def raise_for_status(self) -> None:
            raise aiohttp.ClientResponseError(None, (), status=404)  # type: ignore[arg-type]

        def close(self) -> None:
            return None

        async def read(self) -> bytes:  # pragma: no cover - never reached
            raise AssertionError("a 404 body must not be read")

    class _Session:
        closed = False

        async def get(self, *_args: object, **_kwargs: object) -> _Response:
            nonlocal attempts
            attempts += 1
            return _Response()

    with patch.object(client, "_get_session", return_value=_Session()):
        with pytest.raises(aiohttp.ClientResponseError):
            await client._request_text("https://www.sec.gov/missing.xml")

    assert attempts == 1
