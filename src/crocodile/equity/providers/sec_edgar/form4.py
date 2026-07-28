"""Form 4 ownership XML → :class:`~crocodile.core.schema.records.InsiderTransaction`.

Section 16(a) requires an officer, director or ten-percent owner to report a transaction in
their issuer's securities, and Rule 16a-3(g) gives them two business days to do it. The
resulting document is an ``ownershipDocument`` attached to the filing; this module turns one
into records and does nothing else — no HTTP, no lake, no clock beyond the ``local_ts`` the
caller supplies — so a fixture drives it exactly as EDGAR does.

**Table I only.** A Form 4 has two tables and only the first is parsed here. Table II
reports transactions in *derivative* securities, and its two quantities do not multiply into
one: ``transactionShares`` counts the shares the derivative converts to, while
``transactionPricePerShare`` is the price of the derivative — commonly ``0`` on a grant,
and the strike on an exercise. ``whale-alerts`` thresholds on a USD notional, so a Table II
line would contribute a number that is neither the premium nor the underlying's value.
:class:`~crocodile.core.schema.records.InsiderTransaction` has no column that would let a
consumer tell the two tables apart afterwards, so emitting both would put two different
quantities in one ``value`` column under one name — which is the shape of collision the
merge these records came out of exists to end. ``nonDerivativeHolding`` elements are
excluded for the plainer reason that a holding is not a transaction.

**One record per transaction line, not per reporting owner.** A joint Form 4 repeats
``reportingOwner`` — a fund and its general partner, an executive and their family trust —
over one transaction table, because the same shares are beneficially owned by each of them.
The shares moved once. Emitting the line per owner would report a $10M sale as two $10M
sales, and ``whale-alerts`` would raise two alerts for one trade; that is the worse error of
the two available, so the line is attributed to the first reported owner and the co-reporters
are logged rather than emitted. What is lost is a watchlist hit on a co-reporter, which is a
missing row; what is avoided is a duplicated notional, which is a wrong number.
"""

from __future__ import annotations

import logging
from typing import Any
from xml.etree.ElementTree import Element, ParseError, fromstring

from crocodile.core.schema.enums import AssetClass
from crocodile.core.schema.provenance import provenance_fields
from crocodile.core.schema.records import InsiderTransaction
from crocodile.equity.providers.sec_edgar._xml import child, children, text_of

log = logging.getLogger(__name__)

__all__ = ["Form4ParseError", "parse_form4"]

_TRUE_FLAGS = frozenset({"1", "true", "y", "yes"})
"""How EDGAR spells a set relationship flag. ``0``/``false`` and an absent element are all
"not set"; nothing else appears in the corpus, and an unrecognised value reads as unset
rather than raising, because a relationship nobody claimed is not a malformed document."""


class Form4ParseError(ValueError):
    """The payload is not a Form 4 ownership document.

    Raised rather than answered with an empty list, because the two states are different
    facts and a caller that cannot tell them apart will read a truncated download as an
    insider who did nothing. An ownership document with a well-formed but *empty* Table I is
    the empty list, and it is a real thing: a Form 4 can report holdings only.
    """


def _flag(relationship: Element | None, name: str) -> bool:
    value = text_of(relationship, name)
    return value is not None and value.strip().lower() in _TRUE_FLAGS


def _position(owner: Element) -> str:
    """Every relationship the filing claims, in the form's own order, joined.

    ``position`` is a required field on the record and a Form 4 reports the relationship as
    four independent booleans plus two free-text boxes, so there is no single value to read
    off. All of them are kept: a CFO who also sits on the board is both, and picking one
    would drop a fact the filing states. ``Unknown`` is the answer when the filer checked
    nothing, which happens, and it says so rather than defaulting to ``Officer``.
    """
    relationship = child(owner, "reportingOwnerRelationship")
    roles: list[str] = []
    if _flag(relationship, "isOfficer"):
        roles.append(text_of(relationship, "officerTitle") or "Officer")
    if _flag(relationship, "isDirector"):
        roles.append("Director")
    if _flag(relationship, "isTenPercentOwner"):
        roles.append("10% Owner")
    if _flag(relationship, "isOther"):
        roles.append(text_of(relationship, "otherText") or "Other")
    return ", ".join(roles) if roles else "Unknown"


def _float_or_none(raw: str | None) -> float | None:
    """Parse a reported amount, answering ``None`` where the box was blank or unreadable.

    The same rule ``_safe_float`` applies to an XBRL fact one module over, and for the same
    reason: ``shares``, ``price`` and ``value`` are optional on the record, so an
    unparseable box has somewhere honest to go. Returning ``0.0`` would put a gift's blank
    price into ``SELECT avg(price)`` as a free share.
    """
    if raw is None:
        return None
    try:
        return float(raw.replace(",", ""))
    except ValueError:
        return None


def _issuer_symbol(document: Element) -> tuple[str, str]:
    """Return ``(canonical symbol, raw symbol)`` for the issuer named on the document.

    The trading symbol is the identity every other equity record in this tree uses, so it
    wins when the filing states one. It is optional on the schema and genuinely absent for a
    debt-only or newly-registered issuer, and the fallback is the issuer CIK under the
    ``CIK##########`` spelling ``SecEdgarClient._normalize_facts`` already writes for a
    company with no ticker in the map — one spelling for "we have an identifier and not a
    ticker", not two.
    """
    issuer = child(document, "issuer")
    ticker = text_of(issuer, "issuerTradingSymbol")
    if ticker is not None and ticker.upper() not in {"NONE", "N/A"}:
        return ticker.upper(), ticker
    cik = text_of(issuer, "issuerCik")
    if cik is None:
        raise Form4ParseError("ownership document names neither a trading symbol nor an issuer CIK")
    return f"CIK{int(cik):010d}", cik


def _reporting_owners(document: Element) -> list[Element]:
    owners = list(children(document, "reportingOwner"))
    if not owners:
        raise Form4ParseError("ownership document has no reportingOwner")
    return owners


def _transaction(
    node: Element,
    *,
    header: dict[str, Any],
    insider_name: str,
    insider_cik: str | None,
    position: str,
) -> InsiderTransaction | None:
    """One Table I line, or ``None`` when it states no date or no transaction code.

    Those two are what make a line a transaction. A line without them is either a holding
    that wandered into the transaction table or a document this parser has misread, and
    inventing either would put a dated event in the lake that the filing never dated.
    """
    date = text_of(node, "transactionDate", "value")
    coding = child(node, "transactionCoding")
    code = text_of(coding, "transactionCode")
    if date is None or code is None:
        return None

    amounts = child(node, "transactionAmounts")
    shares = _float_or_none(text_of(amounts, "transactionShares", "value"))
    price = _float_or_none(text_of(amounts, "transactionPricePerShare", "value"))
    # `value` is the product and exists only when both factors do. A price-less gift keeps
    # `value=None`, which is what `filter_transfers_by_usd` reads as "below any threshold" —
    # the same treatment a crypto transfer with no `usd_value` gets, rather than a zero that
    # would sum into a notional total as if the shares had been given away for nothing.
    value = shares * price if shares is not None and price is not None else None

    # The confidence formula's observable: how many of the two boxes a notional needs the
    # filer actually filled. Never a literal — `provenance_fields` is the only path that
    # populates the tail, and `sec_form4` is what turns this count into the number.
    reported_amounts = int(shares is not None) + int(price is not None)
    prov = provenance_fields("sec_form4", {"n_reported_amounts": reported_amounts})

    return InsiderTransaction(
        **header,
        prov=prov.prov,
        prov_basis=prov.prov_basis,
        prov_confidence=prov.prov_confidence,
        prov_inputs=prov.prov_inputs,
        insider_name=insider_name,
        insider_cik=insider_cik,
        position=position,
        transaction_type=code,
        transaction_date=date,
        shares=shares,
        price=price,
        value=value,
        ownership=text_of(child(node, "ownershipNature"), "directOrIndirectOwnership", "value"),
        acquired_disposed=text_of(amounts, "transactionAcquiredDisposedCode", "value"),
    )


def parse_form4(xml_text: str | bytes, *, local_ts: int) -> list[InsiderTransaction]:
    """Parse one Form 4 ownership document into its Table I transactions.

    Args:
        xml_text: The ``ownershipDocument`` attachment, as fetched or as a fixture.
        local_ts: UTC epoch nanoseconds at which the caller observed the document. Supplied
            rather than read from the clock so the function is pure and a fixture test pins
            a whole record rather than every field but one.

    Returns:
        One record per Table I transaction line, in document order. Empty when the document
        reports holdings only, which is a real filing and not an error.

    Raises:
        Form4ParseError: the payload is not well-formed XML, is not an ownership document,
            or names no reporting owner.
    """
    try:
        # Entity-expansion exposure and why it is accepted: see `_xml`'s module docstring.
        document = fromstring(xml_text)
    except ParseError as exc:
        raise Form4ParseError(f"not well-formed XML: {exc}") from exc

    form = text_of(document, "documentType")
    if form is None:
        raise Form4ParseError("payload declares no documentType; it is not an ownership document")

    symbol, symbol_raw = _issuer_symbol(document)
    owners = _reporting_owners(document)
    if len(owners) > 1:
        # Named at DEBUG rather than dropped in silence — the module docstring has the
        # argument for attributing the line once, and this is where the cost of it shows up.
        log.debug(
            "sec_edgar: %s Form %s is a joint filing by %d owners; the transactions are "
            "attributed to %r and the co-reporters are not emitted separately",
            symbol,
            form,
            len(owners),
            text_of(child(owners[0], "reportingOwnerId"), "rptOwnerName"),
        )
    owner = owners[0]
    owner_id = child(owner, "reportingOwnerId")
    insider_name = text_of(owner_id, "rptOwnerName")
    if insider_name is None:
        raise Form4ParseError("reportingOwner states no rptOwnerName")
    raw_cik = text_of(owner_id, "rptOwnerCik")

    header: dict[str, Any] = {
        "source": "sec_edgar",
        "symbol": symbol,
        "symbol_raw": symbol_raw,
        "local_ts": local_ts,
        # A Form 4 stamps calendar dates and never a time of day, so there is no instant to
        # put here; midnight would be a time the document does not claim. The dates it does
        # state are on the record, in `transaction_date`. This is the same answer
        # `_parse_filing_dict` and `_normalize_facts` give for the same reason.
        "source_ts": None,
        "asset_class": AssetClass.EQUITY,
    }
    position = _position(owner)
    insider_cik = f"{int(raw_cik):010d}" if raw_cik is not None and raw_cik.isdigit() else raw_cik

    records: list[InsiderTransaction] = []
    for table in children(document, "nonDerivativeTable"):
        for node in children(table, "nonDerivativeTransaction"):
            record = _transaction(
                node,
                header=header,
                insider_name=insider_name,
                insider_cik=insider_cik,
                position=position,
            )
            if record is not None:
                records.append(record)
    return records
