"""13F-HR XML → :class:`~crocodile.core.schema.records.Holding13F`.

A 13F-HR is two attachments and both are needed. ``primary_doc.xml`` is the cover page: who
filed, under which CIK, and for which calendar quarter. ``form13fInfoTable.xml`` is the
information table: one ``infoTable`` element per position, identifying its issuer by CUSIP
and stating a value, a share count and the voting authority behind it. Neither document
repeats what the other says, so :func:`parse_13f_primary_document` produces the
:class:`Form13FCoverPage` that :func:`parse_13f_information_table` needs.

Both functions are pure: XML in, records out, no HTTP and no clock. The client one module
over does the fetching.

**The value column changed units and nothing in the payload says so.** Column 5 of the
information table was reported in *thousands* of dollars for the whole history of the form
until the SEC's whole-dollar amendment, which applies to filings made on or after
2023-01-03; from then on it is whole dollars. The XML is byte-identical either way — a
$135 million position is ``135360`` in one era and ``135360898`` in the other — so a lake
that stores the number as filed holds two units in one column, and every threshold, sum and
comparison across the boundary is wrong by a factor of a thousand in one direction or the
other. The rule is deterministic in the filing date, so this module normalises on it and
:attr:`Holding13F.value` is whole dollars at every report date. :data:`_WHOLE_DOLLAR_FROM`
is the boundary and :func:`_value_scale` is the whole of the conversion.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Final, NamedTuple
from xml.etree.ElementTree import Element, ParseError, fromstring

from crocodile.core.schema.enums import AssetClass
from crocodile.core.schema.provenance import provenance_fields
from crocodile.core.schema.records import Holding13F
from crocodile.equity.providers.sec_edgar._xml import (
    child,
    descendants,
    local_name,
    text_of,
)

log = logging.getLogger(__name__)

__all__ = [
    "Form13FCoverPage",
    "Form13FParseError",
    "parse_13f_information_table",
    "parse_13f_primary_document",
]

_WHOLE_DOLLAR_FROM: Final[dt.date] = dt.date(2023, 1, 3)
"""First filing date on which Column 5 is whole dollars rather than thousands.

The boundary is the *filing* date and not the report date, because that is how the
amendment is written: a manager filing in February 2023 for the quarter ended December 2022
files whole dollars for a quarter whose earlier peers were reported in thousands. Keying on
the report date would misconvert exactly that filing, which is the most common one there is
— a whole quarter's worth of filers crossing the boundary at once.
"""

_THOUSANDS: Final[float] = 1000.0


class Form13FParseError(ValueError):
    """The payload is not the 13F attachment it was passed as.

    A separate state from "the manager reported no positions", which is a real filing — a
    13F-NT reports none by definition, and an amendment restating a cover page carries an
    empty table. Collapsing the two would read a truncated download as a liquidated fund.
    """


class Form13FCoverPage(NamedTuple):
    """What ``primary_doc.xml`` states and the information table does not.

    A :class:`NamedTuple` rather than a record: nothing here is an observation of a market,
    it is the identity and period the holdings rows are stamped with, and it never reaches
    the lake on its own.
    """

    manager_name: str
    """The filing manager as the cover page spells it. Free text, and it moves between
    quarters — which is why :attr:`manager_cik` exists."""

    manager_cik: str | None
    """Ten-digit CIK, or ``None`` when the cover page nests it somewhere this parser did not
    find. Optional rather than required because a missing identifier must not cost the
    caller the positions; the name still identifies the filer, less stably."""

    report_date: str
    """Quarter end the table describes, normalised to ``YYYY-MM-DD``. The cover page states
    it as ``MM-DD-YYYY``, which is the one place in this provider where a date arrives in a
    format nothing else in the tree uses."""

    report_type: str | None
    """``13F HOLDINGS REPORT``, ``13F NOTICE`` or ``13F COMBINATION REPORT``, verbatim."""


def _to_iso_date(raw: str | None) -> str | None:
    """Normalise EDGAR's two date spellings to ``YYYY-MM-DD``, or ``None``.

    The cover page writes ``03-31-2024`` and the submissions index writes ``2024-03-31`` for
    the same instant. Both are accepted here so a caller does not have to know which
    document a date came out of; anything else answers ``None`` rather than being coerced,
    because a date this parser cannot read is what would silently become a wrong
    ``disclosure_lag_days`` and therefore a wrong confidence.
    """
    if raw is None:
        return None
    text = raw.strip()
    for fmt in ("%Y-%m-%d", "%m-%d-%Y"):
        try:
            return dt.datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _value_scale(iso_filing_date: str) -> float:
    """Return the multiplier turning a reported Column 5 value into whole dollars.

    ``1000.0`` before the whole-dollar amendment and ``1.0`` from it, on the filing date.
    See this module's docstring for why a lake cannot store the number as filed, and
    :data:`_WHOLE_DOLLAR_FROM` for why the boundary is the filing date and not the quarter.
    """
    return _THOUSANDS if dt.date.fromisoformat(iso_filing_date) < _WHOLE_DOLLAR_FROM else 1.0


def _disclosure_lag_days(report_date: str, filing_date: str) -> int:
    """Calendar days from the quarter the table describes to the day it became public.

    Clamped at zero. A filing stamped before its own report date is a defect in the
    document rather than a portfolio observed early, and a negative lag would drive
    ``sec_13f_hr``'s formula above 1.0, which ``confidence_for`` rejects as a broken formula
    — turning a malformed date into a crash a long way from the document that carried it.
    """
    filed = dt.date.fromisoformat(filing_date)
    reported = dt.date.fromisoformat(report_date)
    return max((filed - reported).days, 0)


def _float_or_none(raw: str | None) -> float | None:
    if raw is None:
        return None
    try:
        return float(raw.replace(",", ""))
    except ValueError:
        return None


def parse_13f_primary_document(xml_text: str | bytes) -> Form13FCoverPage:
    """Parse a 13F ``primary_doc.xml`` cover page.

    Raises:
        Form13FParseError: the payload is not well-formed, or states no filing manager or no
            reporting period. Both are what the information table is stamped with, so a
            table parsed without them would carry an unattributed position and an
            unmeasurable disclosure lag.
    """
    try:
        # Entity-expansion exposure and why it is accepted: see `_xml`'s module docstring.
        document = fromstring(xml_text)
    except ParseError as exc:
        raise Form13FParseError(f"not well-formed XML: {exc}") from exc

    manager = next(descendants(document, "filingManager"), None)
    manager_name = text_of(manager, "name")
    if manager_name is None:
        raise Form13FParseError("cover page names no filingManager")

    # `periodOfReport` lives under `headerData/filerInfo` and `reportCalendarOrQuarter`
    # under `formData/coverPage`. Both are present on a conforming filing and each is
    # missing from some live ones, so either answers.
    period = _to_iso_date(
        text_of(next(descendants(document, "filerInfo"), None), "periodOfReport")
    ) or _to_iso_date(
        text_of(next(descendants(document, "coverPage"), None), "reportCalendarOrQuarter")
    )
    if period is None:
        raise Form13FParseError("cover page states no readable reporting period")

    cik = None
    for credentials in descendants(document, "credentials"):
        cik = text_of(credentials, "cik")
        if cik is not None:
            break
    return Form13FCoverPage(
        manager_name=manager_name,
        manager_cik=f"{int(cik):010d}" if cik is not None and cik.isdigit() else cik,
        report_date=period,
        report_type=text_of(next(descendants(document, "coverPage"), None), "reportType"),
    )


def parse_13f_information_table(
    xml_text: str | bytes,
    *,
    cover: Form13FCoverPage,
    filing_date: str,
    accession_number: str | None,
    local_ts: int,
) -> list[Holding13F]:
    """Parse a 13F information table into one record per reported position.

    Args:
        xml_text: The ``form13fInfoTable.xml`` attachment.
        cover: What :func:`parse_13f_primary_document` read off the same filing's cover page.
        filing_date: The day the filing became public, from the submissions index. It fixes
            both the units of the value column and the disclosure lag that scores the row.
        accession_number: EDGAR's identifier for the filing, carried onto every row so a
            restatement can be told from the filing it restates.
        local_ts: UTC epoch nanoseconds at which the caller observed the document.

    Returns:
        One record per ``infoTable`` element that states every field
        :class:`~crocodile.core.schema.records.Holding13F` requires without a default —
        ``cusip``, ``nameOfIssuer``, ``value``, ``sshPrnamt`` and ``sshPrnamtType`` — in
        document order. A row missing any of them is skipped with a DEBUG line naming which,
        rather than defaulted: a zero would be a position of no value reported as a fact.

        ``sshPrnamtType`` belongs on that list and was the one exception to it, defaulted to
        ``"SH"``. It is the unit of ``sshPrnamt``, and the two other values it takes are
        ``PRN`` and ``CALL`` — principal amount and option contracts. A note reported as
        ``10000000`` ``PRN`` is ten million dollars of face value; the same row defaulted to
        ``SH`` is ten million *shares*, a quantity the filer never stated, of an instrument
        that has no shares. Nothing downstream can tell the two apart afterwards, because a
        defaulted ``SH`` is spelled exactly like a stated one, and a manager reporting only
        notes would come back as one of the larger equity holders in the lake.

    Raises:
        Form13FParseError: the payload is not well-formed, or ``filing_date`` cannot be read.
    """
    try:
        # Entity-expansion exposure and why it is accepted: see `_xml`'s module docstring.
        document = fromstring(xml_text)
    except ParseError as exc:
        raise Form13FParseError(f"not well-formed XML: {exc}") from exc

    iso_filing_date = _to_iso_date(filing_date)
    if iso_filing_date is None:
        # Refused rather than defaulted, because the filing date settles two unrelated
        # things and there is no safe guess for either: which unit the value column is in —
        # a thousand-fold error in whichever direction the guess went — and how long the
        # table was withheld, which is the whole of `sec_13f_hr`'s confidence.
        raise Form13FParseError(
            f"filing date {filing_date!r} is unreadable, so neither the era of the value "
            f"column nor the disclosure lag can be established; a 13F value is thousands of "
            f"dollars before {_WHOLE_DOLLAR_FROM.isoformat()} and whole dollars from it"
        )
    scale = _value_scale(iso_filing_date)
    lag_days = _disclosure_lag_days(cover.report_date, iso_filing_date)
    prov = provenance_fields("sec_13f_hr", {"disclosure_lag_days": lag_days})

    records: list[Holding13F] = []
    for entry in _info_tables(document):
        cusip = text_of(entry, "cusip")
        issuer = text_of(entry, "nameOfIssuer")
        amount = child(entry, "shrsOrPrnAmt")
        value = _float_or_none(text_of(entry, "value"))
        shares = _float_or_none(text_of(amount, "sshPrnamt"))
        shares_type = text_of(amount, "sshPrnamtType")
        missing = [
            name
            for name, stated in (
                ("cusip", cusip is not None),
                ("nameOfIssuer", issuer is not None),
                ("value", value is not None),
                ("sshPrnamt", shares is not None),
                ("sshPrnamtType", shares_type is not None),
            )
            if not stated
        ]
        if missing:
            log.debug(
                "sec_edgar: 13F row for %r under %s states no %s; skipping rather than "
                "filing a default",
                issuer,
                accession_number,
                ", ".join(missing),
            )
            continue
        voting = child(entry, "votingAuthority")
        records.append(
            Holding13F(
                source="sec_edgar",
                # A 13F identifies its issuer by CUSIP and by name and never by ticker, and
                # mapping one to the other needs a reference table this parser is not given.
                # The CUSIP is therefore the symbol: it is the identifier the document
                # actually states, and inventing a ticker for it here would be a join
                # performed with no evidence, silently wrong for every issuer whose CUSIP
                # maps to more than one listed class.
                symbol=cusip.upper(),
                symbol_raw=cusip,
                local_ts=local_ts,
                # As with Form 4: the filing stamps dates, never an instant. `report_date`
                # carries the quarter end, which is the date that means anything here.
                source_ts=None,
                asset_class=AssetClass.EQUITY,
                prov=prov.prov,
                prov_basis=prov.prov_basis,
                prov_confidence=prov.prov_confidence,
                prov_inputs=prov.prov_inputs,
                manager_name=cover.manager_name,
                manager_cik=cover.manager_cik,
                issuer_name=issuer,
                cusip=cusip,
                value=value * scale,
                shares=shares,
                shares_type=shares_type,
                discretion=text_of(entry, "investmentDiscretion"),
                voting_sole=_float_or_none(text_of(voting, "Sole")),
                voting_shared=_float_or_none(text_of(voting, "Shared")),
                voting_none=_float_or_none(text_of(voting, "None")),
                report_date=cover.report_date,
                accession_number=accession_number,
            )
        )
    return records


def _info_tables(document: Element) -> list[Element]:
    """Every ``infoTable`` element, however the filing agent nested or prefixed it.

    ``descendants`` rather than ``children``: the conforming shape puts ``infoTable``
    directly under ``informationTable``, and filings in the corpus wrap it one level deeper.
    A direct-children lookup answers those with an empty list, which reads as a manager
    holding nothing.
    """
    entries = list(descendants(document, "infoTable"))
    if not entries and local_name(document.tag) == "infoTable":
        # A single-position table submitted without its wrapper element.
        return [document]
    return entries
