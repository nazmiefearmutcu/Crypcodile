"""Who is moving size, when the report of the move is a filing rather than a transfer.

The three capabilities this module serves — ``whale-alerts``, ``smart-money`` and
``label-transfers`` — were crypto-only, and their crypto halves all read the same shape: an
on-chain transfer, with a sender, a recipient, a USD value and an instant. Equity has no
transfer to read. What it has is a *disclosure*: Section 16(a) makes an insider report a
transaction in their issuer's stock within two business days, and Section 13(f) makes an
institutional manager report the positions it held at each quarter end. So the equity form
of "who moved size" is a filing, and this module is the adapter between the two.

Three things follow from that substitution and each of them is a decision made here rather
than a detail:

**A position is not a transaction.** ``whale-alerts`` answers "what large trades printed in
this window", and its crypto half returns trades and liquidations — events, each with a
side. A 13F row is a *holding*: it has a value and no side, and no date for any of the
trades that built it. Reporting one as an alert would answer "who holds size" under a name
that promises "who moved it", so :func:`track_filing_whales` reads Form 4 only. The 13F
half earns its place in ``smart-money`` instead, where a *difference* between two quarters
is a flow.

**A flow needs two observations, and the first one is not a flow.** :func:`filing_flows`
turns a run of information tables into per-position changes, and the earliest table for a
given ``(filer, cusip)`` produces nothing. It establishes the baseline; a change before the
first observation is not something anybody saw. A caller handing in a single quarter
therefore gets no institutional flow, which is the true answer and not an empty result to
be worked around.

**A filer is not an address, and that is the one place equity is better off.** An Ethereum
address is an opaque hex string, so the crypto ``label-transfers`` can only say "known" or
"unknown" and leaves the label empty for everything off the watchlist. A filing arrives
carrying both a stable identifier — the CIK, assigned once and never re-spelled — and the
filer's own reported name. :func:`label_filing_parties` therefore fills a label for *every*
row while keeping ``is_known`` meaning what it means in the crypto half: matched the
watchlist. The two claims are separate columns because they are separate facts, and
collapsing them would make ``known_only`` a no-op.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Mapping, Sequence
from itertools import pairwise
from typing import Any, Final

import polars as pl

from crocodile.core.store.catalog import Catalog

__all__ = [
    "WHALE_ALERT_SCHEMA",
    "filing_flows",
    "label_filing_parties",
    "resolve_watched_filers",
    "track_filing_whales",
]

WHALE_ALERT_SCHEMA: Final[dict[str, pl.DataType]] = {
    "timestamp": pl.Int64(),
    "event_type": pl.Utf8(),
    "price": pl.Float64(),
    "amount": pl.Float64(),
    "usd_value": pl.Float64(),
    "side": pl.Utf8(),
}
"""The six columns ``whale-alerts`` returns, for either asset class.

Written out here and asserted against the crypto half in the tests rather than imported
from it, because :func:`~crocodile.crypto.analytics.whale.track_whale_alerts` builds its
empty frame from a literal and its populated one from a ``select``, so there is no object
to import. Symmetry of return shape is half of what the registry means by symmetry, and a
column set that agrees today by inspection is one that stops agreeing without a test.
"""

_ACQUIRED: Final = "A"
_DISPOSED: Final = "D"

_CODE_DIRECTION: Final[dict[str, str]] = {
    # Table I transaction codes whose direction is not in question. The authoritative field
    # is `acquired_disposed`, and this map is only reached when a row does not carry one —
    # which is every insider row from the Yahoo scrape, since that page publishes a prose
    # label and no A/D column.
    "P": _ACQUIRED,  # open-market or private purchase
    "A": _ACQUIRED,  # grant, award or other acquisition from the issuer
    "M": _ACQUIRED,  # exercise or conversion of a derivative security
    "C": _ACQUIRED,  # conversion of a derivative security
    "X": _ACQUIRED,  # exercise of an in-the-money or at-the-money derivative
    "S": _DISPOSED,  # open-market or private sale
    "D": _DISPOSED,  # disposition to the issuer
    "F": _DISPOSED,  # shares withheld to pay an exercise price or a tax liability
    "U": _DISPOSED,  # disposition pursuant to a tender of shares
    # `G` (gift) and `J` (other) are deliberately absent. A gift is one code and two
    # directions — the donor disposes and the donee acquires — so a guess here would be
    # wrong for exactly half of them, silently, in a signed total. `J` is the form's
    # catch-all and means whatever the footnote says.
    "Purchase": _ACQUIRED,  # the prose spellings the Yahoo scrape emits
    "Sale": _DISPOSED,
}


def _text(row: Mapping[str, Any], *keys: str) -> str | None:
    """First non-blank string among ``keys``, or ``None``.

    Filing rows arrive under two vocabularies — an insider row says ``insider_name`` and a
    holdings row says ``manager_name`` — and every function here has to serve both. Reading
    a list of aliases is how the crypto half already handles ``from``/``from_address``/
    ``sender``; this is the same move over the two forms rather than over three spellings of
    one field.
    """
    for key in keys:
        value = row.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _number(row: Mapping[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = row.get(key)
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _date_ns(raw: str | None) -> int:
    """A ``YYYY-MM-DD`` filing date as epoch nanoseconds at midnight UTC, or ``0``.

    A filing states a calendar date and never a time of day, so every instant this module
    reports is a date at day resolution. Midnight UTC is the instant a date names with the
    least invention — it is the boundary of the day rather than a guess at when inside it
    something happened — and the date itself stays on the row it came from, so a consumer
    that wants the day rather than the instant has it unrounded.
    """
    if raw is None:
        return 0
    try:
        day = dt.date.fromisoformat(raw.strip()[:10])
    except ValueError:
        return 0
    return int(
        dt.datetime.combine(day, dt.time.min, tzinfo=dt.UTC).timestamp() * 1_000_000_000
    )


def _direction(row: Mapping[str, Any]) -> str | None:
    """``"A"``, ``"D"`` or ``None`` for a Form 4 row.

    ``acquired_disposed`` wins whenever the row carries it, because it is the box the form
    puts the answer in. :data:`_CODE_DIRECTION` is the fallback and covers only the codes
    that have one direction; ``None`` for the rest is what keeps a gift out of a signed
    total rather than in it with a coin-flip sign.
    """
    stated = _text(row, "acquired_disposed")
    if stated is not None and stated.upper() in {_ACQUIRED, _DISPOSED}:
        return stated.upper()
    code = _text(row, "transaction_type")
    if code is None:
        return None
    return _CODE_DIRECTION.get(code) or _CODE_DIRECTION.get(code.upper())


def _notional(row: Mapping[str, Any]) -> float | None:
    """The USD size of a filing line, or ``None`` when the filing did not state one.

    ``value`` when the row has it, and ``shares * price`` when it has both factors instead.
    ``None`` otherwise, and never ``0.0``: a Form 4 gift states shares and no price, and a
    zero would report it as a whale-sized transaction that changed hands for nothing —
    which is a number, where the truth is a hole. It is the same rule
    ``filter_transfers_by_usd`` applies to a transfer with no ``usd_value``, and it means
    such a row is below every threshold rather than above the zero one.
    """
    value = _number(row, "value", "usd_value")
    if value is not None:
        return abs(value)
    shares = _number(row, "shares", "amount")
    price = _number(row, "price")
    if shares is None or price is None:
        return None
    return abs(shares * price)


def filer_identity(row: Mapping[str, Any]) -> str | None:
    """The stable key a filing row's party is tracked under.

    The CIK when the row carries one and the reported name otherwise. A CIK is assigned once
    and a name is however the filing agent typed it that quarter — ``COOK TIMOTHY D`` and
    ``Cook Timothy D.`` are one person and two group-by keys — so netting a filer's flow on
    the name silently splits it in two the first time an agent changes.
    """
    return _text(row, "insider_cik", "manager_cik") or _text(row, "insider_name", "manager_name")


def filer_name(row: Mapping[str, Any]) -> str | None:
    """The party's own reported name, which is what makes an unwatched equity row labellable."""
    return _text(row, "insider_name", "manager_name")


def counterparty_name(row: Mapping[str, Any]) -> str | None:
    """The other side of a filing line: the issuer whose stock moved.

    An insider row names it by ticker in the header ``symbol``; a holdings row names it by
    CUSIP and by ``issuer_name``. Both are the issuer, which is what makes them one field
    here.
    """
    return _text(row, "issuer_name", "symbol", "cusip", "symbol_raw")


def resolve_watched_filers(
    rows: Iterable[Mapping[str, Any]], watchlist: Mapping[str, str]
) -> dict[str, str]:
    """Return ``{filer identity: label}`` for the filers ``watchlist`` names.

    Written because a filing carries *two* handles on the same party and a watchlist is
    entitled to use either. ``{"0001051401": "Apple CEO"}`` and ``{"COOK TIMOTHY D": "Apple
    CEO"}`` name one person, and a flow tracker keyed on the CIK would match the first and
    miss the second. So the rows are scanned once, each party's CIK and name are both tested
    against the watchlist, and any hit is re-keyed onto the identity
    :func:`filer_identity` will use — which is what lets the crypto ``SmartMoneyTracker``
    serve equities without knowing anything about filings.

    Matching is case-insensitive on both handles, the way ``normalize_watchlist`` already
    lower-cases the addresses it is given.
    """
    labels = {str(k).strip().lower(): str(v) for k, v in watchlist.items() if str(k).strip()}
    resolved: dict[str, str] = {}
    for row in rows:
        identity = filer_identity(row)
        if identity is None:
            continue
        for handle in (
            _text(row, "insider_cik", "manager_cik"),
            _text(row, "insider_name", "manager_name"),
        ):
            if handle is not None and handle.lower() in labels:
                resolved[identity] = labels[handle.lower()]
                break
    return resolved


def _insider_flows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """One signed transfer per dated insider transaction.

    An acquisition moves value from the issuer to the filer and a disposition the other way,
    which is exactly the ``from``/``to`` pair the crypto tracker nets over — so an insider
    who bought shows a positive ``net_flow_usd`` and one who sold a negative, with no
    special-casing inside the tracker at all.

    A row whose direction cannot be established is dropped rather than counted unsigned. The
    alternative — contributing to ``total_volume_usd`` and not to ``net_flow_usd`` — would
    make the two columns describe different sets of rows under one ``tx_count``, which is a
    worse answer than a smaller one.
    """
    flows: list[dict[str, Any]] = []
    for row in rows:
        identity = filer_identity(row)
        notional = _notional(row)
        direction = _direction(row)
        if identity is None or notional is None or direction is None:
            continue
        issuer = counterparty_name(row) or "issuer"
        timestamp = _date_ns(_text(row, "transaction_date"))
        acquired = direction == _ACQUIRED
        flows.append(
            {
                "from": issuer if acquired else identity,
                "to": identity if acquired else issuer,
                "usd_value": notional,
                "timestamp": timestamp,
            }
        )
    return flows


def _holding_flows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """One signed transfer per quarter-over-quarter change in a reported position.

    Grouped by ``(filer identity, cusip)`` and ordered by report date. The first table in
    each group is the baseline and contributes nothing — see this module's docstring — and
    every later one contributes the change in reported value since the previous table, so a
    manager that added to a position shows an inflow and one that trimmed shows an outflow.
    A position that disappears from a later table is not visible here at all: its absence is
    a row that is not there, and this function sees rows.

    Restatements are collapsed by keeping the last row for a given ``(filer, cusip, report
    date)``. An amendment restating a quarter would otherwise difference against the
    original of the same quarter and report a flow of the correction.
    """
    latest: dict[tuple[str, str, str], Mapping[str, Any]] = {}
    for row in rows:
        identity = filer_identity(row)
        cusip = _text(row, "cusip", "symbol")
        report_date = _text(row, "report_date")
        value = _number(row, "value")
        if identity is None or cusip is None or report_date is None or value is None:
            continue
        latest[(identity, cusip.upper(), report_date)] = row

    by_position: dict[tuple[str, str], list[tuple[str, float]]] = {}
    for (identity, cusip, report_date), row in latest.items():
        value = _number(row, "value")
        if value is None:  # pragma: no cover - filtered above, restated for the type checker
            continue
        by_position.setdefault((identity, cusip), []).append((report_date, value))

    flows: list[dict[str, Any]] = []
    for (identity, cusip), series in sorted(by_position.items()):
        series.sort()
        for (_, previous), (report_date, current) in pairwise(series):
            change = current - previous
            if change == 0.0:
                continue
            flows.append(
                {
                    "from": cusip if change > 0.0 else identity,
                    "to": identity if change > 0.0 else cusip,
                    "usd_value": abs(change),
                    "timestamp": _date_ns(report_date),
                }
            )
    return flows


def filing_flows(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Turn filing rows into the transfer shape ``summarize_smart_money`` already nets.

    Both forms are accepted in one call and each is routed on the field that identifies it:
    a row naming a ``manager_name`` or a ``manager_cik`` is an information-table row and a
    row naming an ``insider_name`` is a Form 4 line. Routing on the field rather than asking
    the caller to say which is which is what lets ``smart-money`` take one ``transfers``
    parameter for equities exactly as it does for crypto — the shared params struct is the
    point of the whole exercise, so a second field naming the form would break the symmetry
    it exists to express.

    Rows matching neither are ignored, which is what a transfer row handed to the equity
    implementation is: the crypto half already answers a filing row with no flows at all,
    for the same reason and in the same direction.
    """
    insider: list[Mapping[str, Any]] = []
    holdings: list[Mapping[str, Any]] = []
    for row in rows:
        if _text(row, "manager_name", "manager_cik") is not None:
            holdings.append(row)
        elif _text(row, "insider_name", "insider_cik") is not None:
            insider.append(row)
    return _insider_flows(insider) + _holding_flows(holdings)


def label_filing_parties(
    rows: Iterable[Mapping[str, Any]],
    watchlist: Mapping[str, str],
) -> list[dict[str, Any]]:
    """Annotate filing rows with ``from_label`` / ``to_label`` / ``is_known``.

    The same three columns the crypto half writes, so a surface renders one table for both
    asset classes. What differs is what an *unmatched* party gets. On the crypto side an
    address off the watchlist gets ``""``, because a hex string is all the row carries. A
    filing carries the filer's own reported name, so the label falls back to it and the
    column is populated for every row — the concrete form of the claim in
    ``capabilities/analytics.py``'s ledger that this is the one capability equity serves
    better.

    ``is_known`` still means *matched the watchlist* and nothing else. It would have been
    easy to make it mean "has a label", which is now every row, and that would silently turn
    ``known_only`` into a filter that removes nothing.

    ``from_label`` and ``to_label`` follow the direction of the money, not the layout of the
    filing: on a purchase the issuer is the ``from`` side and the filer the ``to`` side, so
    the two columns line up with the crypto half's sender and recipient rather than with
    "party" and "counterparty".

    A row with no direction — every 13F position, and a Form 4 gift that states no A/D code
    — puts the filer on the ``from`` side. That is a layout, not a claim: an information
    table reports a holding and no flow at all, so neither column is asserting one, and the
    columns this capability exists to add are the labels and ``is_known``. The capability
    that does read a direction out of holdings is ``smart-money``, and it gets it by
    differencing two quarters rather than by reading one row.
    """
    labels = {str(k).strip().lower(): str(v) for k, v in watchlist.items() if str(k).strip()}
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        identity = filer_identity(row)
        handles = [
            handle.lower()
            for handle in (
                _text(row, "insider_cik", "manager_cik"),
                _text(row, "insider_name", "manager_name"),
            )
            if handle is not None
        ]
        matched = next((labels[handle] for handle in handles if handle in labels), None)
        filer_label = matched or filer_name(row) or identity or ""
        issuer_label = counterparty_name(row) or ""
        acquired = _direction(row) == _ACQUIRED
        item["from_label"] = issuer_label if acquired else filer_label
        item["to_label"] = filer_label if acquired else issuer_label
        item["is_known"] = matched is not None
        out.append(item)
    return out


def track_filing_whales(
    catalog: Catalog,
    symbol: str,
    start_ns: int,
    end_ns: int,
    min_usd: float,
) -> pl.DataFrame:
    """Reported insider transactions in ``symbol`` whose notional clears ``min_usd``.

    The equity twin of :func:`~crocodile.crypto.analytics.whale.track_whale_alerts`, column
    for column: ``timestamp``, ``event_type``, ``price``, ``amount``, ``usd_value``,
    ``side``, ordered by timestamp. ``event_type`` is the form — ``Form 4`` — where crypto
    writes ``Trade`` or ``Liquidation``, and ``side`` is ``buy`` for an acquisition and
    ``sell`` for a disposition, which is the same word the aggressor side of a print gets.

    **The window is read against the transaction date, not against ``local_ts``.** The
    crypto half calls ``catalog.scan``, which filters on ``local_ts`` — right there, because
    a trade is observed as it prints. A filing history is not: ``get_insider_transactions``
    fetches forty filings in one pass, so a decade of transactions enters the lake carrying
    one ingest timestamp. A window over ``local_ts`` would therefore return the whole
    history or none of it depending on when the fetch ran, which is not a window over
    anything a caller asked about. ``transaction_date`` is the day the shares actually moved
    and is on every row.

    Reading through ``catalog`` rather than ``ctx.query`` is the handoff ``open-interest``
    documents in ``capabilities/market.py``: the SQL below is fixed and internal to this
    function, so there is no caller-supplied string for the readonly guard to vet, and the
    row cap a network surface sets would silently truncate a threshold query into a
    partial answer.

    The mapping from a row to a side runs in Python rather than as a Polars expression so
    that :func:`_direction` is the *only* definition of which way a transaction went —
    ``smart-money`` reads it too, and a second copy expressed as a ``when``/``then`` chain
    is a second place for the two capabilities to disagree about a gift.
    """
    if min_usd < 0:
        raise ValueError("min_usd must be non-negative.")

    rows = _scan_insider(catalog, symbol, start_ns, end_ns)
    alerts: list[dict[str, Any]] = []
    for row in rows:
        notional = _notional(row)
        if notional is None or notional < min_usd:
            continue
        direction = _direction(row)
        alerts.append(
            {
                "timestamp": _date_ns(_text(row, "transaction_date")),
                "event_type": "Form 4",
                "price": _number(row, "price"),
                "amount": _number(row, "shares"),
                "usd_value": notional,
                "side": {_ACQUIRED: "buy", _DISPOSED: "sell"}.get(direction or "", "unknown"),
            }
        )
    if not alerts:
        return pl.DataFrame(schema=WHALE_ALERT_SCHEMA)
    return pl.DataFrame(alerts, schema=WHALE_ALERT_SCHEMA).sort("timestamp")


def _scan_insider(
    catalog: Catalog, symbol: str, start_ns: int, end_ns: int
) -> list[dict[str, Any]]:
    """Insider rows for ``symbol`` whose transaction date falls in the window.

    An absent ``insider`` channel answers with no rows rather than raising, which is the
    contract :func:`~crocodile.crypto.analytics.whale.track_whale_alerts` already keeps for
    a lake holding no trades: a symbol nobody has ingested is not an error, it is a symbol
    with no whales.
    """
    if end_ns < start_ns:
        return []
    first = dt.datetime.fromtimestamp(start_ns / 1_000_000_000, tz=dt.UTC).date().isoformat()
    last = dt.datetime.fromtimestamp(end_ns / 1_000_000_000, tz=dt.UTC).date().isoformat()
    sql = (
        "SELECT * FROM insider "
        "WHERE symbol = ? AND transaction_date >= ? AND transaction_date <= ? "
        "ORDER BY transaction_date"
    )
    try:
        catalog.refresh_views()
        frame = catalog.query(sql, params=[symbol, first, last])
    except Exception:
        return []
    return [] if frame.is_empty() else frame.to_dicts()
