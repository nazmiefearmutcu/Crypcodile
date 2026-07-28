"""The daily Treasury par yield curve, read keylessly and emitted as ``MacroSeries``.

**Why this is a client and not a** :class:`~crocodile.equity.providers.base.Provider`.
That ABC is a supervised websocket run loop: ``run()`` connects a transport, subscribes,
and drains frames through ``normalize()`` into a sink, reconnecting with backoff. The
Treasury publishes one CSV per calendar year, updated once per business day at about
3:30pm ET, over plain HTTP with no subscription and no stream. A connector shaped for that
loop would implement ``normalize`` as ``return ()``, ``_subscribe`` as ``pass`` and
``list_instruments`` over a fixed table — which is what ``stooq`` had to do, and it is
three no-ops standing in for a contract this source does not have. The three equity
reference sources that were already in this position — ``sec_edgar``, ``tiingo`` and
``yahoo`` — are all plain clients in exactly this shape, and between them they publish the
``corp_action`` and ``options_chain`` rows the equity carry reads on its other legs. This
is the fourth, and it is deliberately the same shape as its three siblings rather than a
fifth entry in ``providers.factory._REGISTRY``: adding one there would also mean adding
``macro_series`` to :data:`~crocodile.equity.providers.factory.VALID_CHANNELS`, and that
list is the CLI's channel menu for *every* provider — including the ones that declare
nothing and therefore get the whole vocabulary offered to them. Offering ``macro_series``
for ``alpaca`` is precisely the dead channel ``tests/conformance/test_provider_channels.py``
exists to stop the picker walking a user into.

**Why the CSV and not the JSON API.** Both are keyless. ``fiscaldata.treasury.gov``
returns JSON with stable field names and pagination; ``home.treasury.gov`` returns one CSV
per year with the tenors as column headers and no pagination at all. The CSV wins on the
property that matters for a fixture-backed test: one request is one complete year, so the
checked-in fixture is a real, whole response rather than the first page of one, and a
parser that only ever sees page one is a parser whose paging is untested.

**The tenor set is read, not listed.** Treasury has changed it: the 4-month bill was added
in October 2022 and the 1.5-month in 2024, both mid-file, both as new columns. A parser
holding a hardcoded list of eleven tenors silently drops a twelfth the day it appears —
and drops it in the direction that matters, since a new short tenor is usually the one
closest to a short carry horizon. :func:`parse_tenor` therefore derives the tenor from the
header text, so an unknown column is either a tenor this understands or is skipped by
name in the log rather than by omission.
"""

from __future__ import annotations

import csv
import io
import logging
import re
from datetime import UTC, datetime
from typing import Final, NamedTuple

import aiohttp

from crocodile.core.analytics.carry import DAYS_PER_YEAR
from crocodile.core.schema.enums import AssetClass
from crocodile.core.schema.provenance import provenance_fields
from crocodile.core.schema.records import MacroSeries
from crocodile.core.util.time import now_ns

log = logging.getLogger(__name__)

__all__ = [
    "PAR_YIELD_CURVE_URL",
    "SOURCE",
    "Tenor",
    "TreasuryYieldClient",
    "parse_par_yield_csv",
    "parse_tenor",
    "tenor_days",
]

SOURCE: Final = "treasury"
"""The ``source`` every record here carries, and the prefix of every symbol it mints."""

PAR_YIELD_CURVE_URL: Final = (
    "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
    "daily-treasury-rates.csv/{year}/all"
    "?type=daily_treasury_yield_curve&field_tdr_date_value={year}&page&_format=csv"
)
"""One request, one calendar year of the daily par yield curve, no key and no pagination."""

_DEFAULT_TIMEOUT_S: Final = 30.0
"""Total request budget. A literal and not a setting: this is one small CSV over plain
HTTP from a government host, there is no credential to configure beside it, and
:class:`~crocodile.core.config.Settings` carries no timeout for any other source either —
adding the first one here would be inventing public API (``CROCODILE_TREASURY_TIMEOUT``)
under cover of a provider. A caller that needs a different budget passes ``timeout_s``, or
passes its own configured :class:`aiohttp.ClientSession`."""

_MONTHS_PER_YEAR: Final = 12.0
_NS_PER_SECOND: Final = 1_000_000_000

_TENOR_HEADER = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(mo|month|yr|year)s?\s*$", re.IGNORECASE)
"""``1 Mo``, ``1.5 Month``, ``10 Yr``. Both spellings and both plurals appear in the wild:
the CSV export writes ``Mo``/``Yr`` and the XML feed's own documentation writes the long
forms, and the file has changed which it uses without announcement."""

_UNIT_MONTHS: Final[dict[str, float]] = {"mo": 1.0, "month": 1.0, "yr": 12.0, "year": 12.0}

_DATE_HEADER: Final = "date"

_DATE_FORMATS: Final = ("%m/%d/%Y", "%Y-%m-%d")
"""The CSV writes ``MM/DD/YYYY``. ISO is accepted too, because the same parser is pointed
at the JSON API's ``record_date`` by anyone who prefers that source, and refusing the
unambiguous spelling in order to insist on the ambiguous one would be perverse."""


class Tenor(NamedTuple):
    """One point on the published curve: what to call it, and how long it is.

    ``days`` is what the carry maths needs — a horizon to compare against — and it is
    derived from the header rather than tabulated, so a tenor Treasury adds tomorrow
    arrives with a length rather than with a lookup miss.
    """

    symbol: str
    """Canonical symbol, e.g. ``treasury:UST10Y``."""

    header: str
    """The column header exactly as the file spelled it, e.g. ``10 Yr``. This becomes
    ``symbol_raw``, which is the header's own contract: the symbol as the source spells
    it."""

    days: float
    """Nominal length of the tenor in days, on the same 365-day year the carry annualises
    over. A month is ``365/12`` days rather than 30 or 31: the curve point is a *nominal*
    tenor, not a dated contract, so a calendar month would imply a precision the label
    does not have."""


def parse_tenor(header: str) -> Tenor | None:
    """Turn a column header into a :class:`Tenor`, or ``None`` if it is not one.

    ``None`` rather than an exception, because the file legitimately carries columns that
    are not tenors — ``Date`` today, and whatever Treasury appends tomorrow. The caller
    logs what it skipped, so a genuinely new tenor spelled in a way this does not
    recognise is visible as a name rather than as an absence.

    The symbol is ``treasury:UST`` plus the tenor: ``1M``, ``4M``, ``10Y``. A fractional
    tenor keeps its value with the point replaced by an underscore (``1.5 Month`` becomes
    ``treasury:UST1_5M``), because a symbol is a partition-directory name and a dot in one
    reads as a file extension to every glob in the store layer.
    """
    match = _TENOR_HEADER.match(header)
    if match is None:
        return None
    magnitude = float(match.group(1))
    unit = match.group(2).lower()
    months = magnitude * _UNIT_MONTHS[unit]
    letter = "M" if _UNIT_MONTHS[unit] == 1.0 else "Y"
    label = f"{magnitude:g}".replace(".", "_")
    return Tenor(
        symbol=f"{SOURCE}:UST{label}{letter}",
        header=header.strip(),
        days=months * DAYS_PER_YEAR / _MONTHS_PER_YEAR,
    )


def _parse_date(raw: str) -> tuple[str, int] | None:
    """Return ``(ISO date, midnight-UTC nanoseconds)``, or ``None`` for an unparseable cell.

    The file states a *date* and no time of day, so the instant is that date's UTC
    midnight — the same conversion ``stooq`` makes for a daily bar
    (``equity/providers/stooq/connector.py:583``). It is not a claim that the curve was
    published at midnight: what the source stamped is the date, and a date-to-instant
    conversion is total in this direction, which is the direction that must not lose
    anything. How far that instant is from the prices a yield is subtracted from is
    measured rather than assumed, by ``treasury_carry``'s confidence formula.
    """
    for fmt in _DATE_FORMATS:
        try:
            parsed = datetime.strptime(raw.strip(), fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
        return parsed.date().isoformat(), int(parsed.timestamp()) * 1_000_000_000
    return None


def _parse_percent(raw: str) -> float | None:
    """Turn a published percentage into a decimal fraction, or ``None`` for a blank cell.

    The Treasury quotes ``4.12`` and means 4.12 %; everything downstream of here — a
    ``basis_pct``, an ``apr``, a ``mark_iv`` — is a decimal fraction, and
    :class:`~crocodile.core.schema.records.OptionsChain` says so in its own annotation.
    Storing the percent would put two units in one ``value`` column depending on which
    macro series wrote the row, and the subtraction that turns a basis into a carry would
    be off by a factor of a hundred with nothing on the row to notice it by.

    A blank or ``N/A`` cell is ``None`` and not 0.0. Treasury leaves a tenor blank on days
    it does not publish one — the 20-year was absent for two decades, the 30-year for
    four years — and a zero there is a yield of zero, which would make a carry look
    unfinanced rather than unmeasured. ``MacroSeries.value`` is optional precisely so this
    has somewhere to go.
    """
    token = raw.strip()
    if not token or token.upper() in {"N/A", "NA", "-"}:
        return None
    try:
        return float(token) / 100.0
    except ValueError:
        return None


def parse_par_yield_csv(text: str, *, local_ts: int | None = None) -> list[MacroSeries]:
    """Parse one year of the daily par yield curve into one record per date and tenor.

    Args:
        text: The CSV body exactly as the endpoint returns it.
        local_ts: Observation instant stamped on every record. Defaults to now, and is a
            parameter so a test can assert on a whole record rather than on the fields
            that happen not to move.

    Returns:
        One :class:`~crocodile.core.schema.records.MacroSeries` per (date, tenor) cell
        that carries a number, in file order. A blank cell yields no record at all rather
        than a record with a null ``value``: the row would assert that Treasury published
        this tenor on this date, which is the fact the blank denies.

    Records are :attr:`~crocodile.core.schema.provenance.Provenance.NATIVE` on the
    ``native`` basis. The Treasury published every number here; the only thing this
    function does to one is divide it by a hundred, and a unit conversion is not a
    derivation. The tail is built through
    :func:`~crocodile.core.schema.provenance.provenance_fields` rather than left to the
    header's default, so that the claim is one this module makes rather than one it
    inherits — the distinction ``core.resample.book`` was caught on.
    """
    stamped = now_ns() if local_ts is None else local_ts
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        log.warning("Treasury par yield CSV is empty; no records emitted")
        return []

    date_index: int | None = None
    tenors: dict[int, Tenor] = {}
    for index, column in enumerate(header):
        if column.strip().lower() == _DATE_HEADER:
            date_index = index
            continue
        tenor = parse_tenor(column)
        if tenor is None:
            log.warning("Treasury par yield CSV column %r is not a tenor; skipping it", column)
            continue
        tenors[index] = tenor

    if date_index is None or not tenors:
        log.error(
            "Treasury par yield CSV header %r has no Date column or no recognised tenor; "
            "no records emitted",
            header,
        )
        return []

    tail = provenance_fields("native")
    records: list[MacroSeries] = []
    for row in reader:
        if len(row) <= date_index:
            continue
        stamped_date = _parse_date(row[date_index])
        if stamped_date is None:
            continue
        iso_date, source_ts = stamped_date
        for index, tenor in tenors.items():
            if index >= len(row):
                continue
            value = _parse_percent(row[index])
            if value is None:
                continue
            records.append(
                MacroSeries(
                    source=SOURCE,
                    symbol=tenor.symbol,
                    symbol_raw=tenor.header,
                    local_ts=stamped,
                    asset_class=AssetClass.EQUITY,
                    source_ts=source_ts,
                    prov=tail.prov,
                    prov_basis=tail.prov_basis,
                    prov_confidence=tail.prov_confidence,
                    prov_inputs=tail.prov_inputs,
                    date=iso_date,
                    value=value,
                )
            )
    return records


def tenor_days(symbol: str) -> float | None:
    """Return the nominal length in days of a symbol this module minted, or ``None``.

    The inverse of the naming rule in :func:`parse_tenor`, and it exists so the carry
    analytics can rank stored yields by tenor without re-deriving the convention or
    keeping a second table of it. A symbol from anywhere else answers ``None``.
    """
    prefix = f"{SOURCE}:UST"
    if not symbol.startswith(prefix):
        return None
    tail = symbol[len(prefix) :]
    if not tail:
        return None
    unit = tail[-1].upper()
    if unit not in {"M", "Y"}:
        return None
    magnitude = tail[:-1].replace("_", ".")
    try:
        months = float(magnitude) * (1.0 if unit == "M" else _MONTHS_PER_YEAR)
    except ValueError:
        return None
    return months * DAYS_PER_YEAR / _MONTHS_PER_YEAR


class TreasuryYieldClient:
    """Fetches the par yield curve over HTTP. Keyless, and injectable for tests.

    ``session`` is a constructor parameter rather than something built on first use so a
    test can drive :meth:`par_yield_curve` end to end against a fake without a socket.
    :func:`parse_par_yield_csv` is a module-level function over a string for the same
    reason at one remove: the parsing — which is where every decision this module makes
    lives — is reachable from a checked-in fixture with no client at all.

    Nothing here reads :data:`os.environ`. The endpoint is keyless, so there is no
    credential to resolve; a caller that needs a proxy, a header or a different timeout
    hands in a session that already has them.
    """

    def __init__(
        self,
        *,
        session: aiohttp.ClientSession | None = None,
        url_template: str = PAR_YIELD_CURVE_URL,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
    ) -> None:
        self._session = session
        self._owns_session = session is None
        self._url_template = url_template
        self._timeout_s = timeout_s

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self._timeout_s)
            )
            self._owns_session = True
        return self._session

    async def close(self) -> None:
        """Close the session, but only one this client opened for itself."""
        if self._owns_session and self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    async def fetch_par_yield_csv(self, year: int) -> str:
        """Return one calendar year of the daily par yield curve as CSV text.

        Raises:
            RuntimeError: on any non-200 response. Not a soft ``None``: an empty curve and
                an unreachable Treasury are different facts, and the carry analytics
                report an absent risk-free leg on the row. Turning a 503 into "no yield
                published" would put the second into the first's column.
        """
        url = self._url_template.format(year=year)
        session = self._get_session()
        async with session.get(url) as response:
            if response.status != 200:
                raise RuntimeError(
                    f"Treasury par yield curve request for {year} failed: HTTP {response.status}"
                )
            return await response.text()

    async def par_yield_curve(self, year: int, *, local_ts: int | None = None) -> list[MacroSeries]:
        """Fetch and parse one calendar year of the curve."""
        return parse_par_yield_csv(await self.fetch_par_yield_csv(year), local_ts=local_ts)

    async def backfill(
        self, start_ns: int, end_ns: int, *, local_ts: int | None = None
    ) -> list[MacroSeries]:
        """Every published curve point whose date falls in ``[start_ns, end_ns]``.

        The endpoint is per calendar year, so this is one request per year the range
        touches, filtered to the range afterwards. The filter is on ``source_ts`` — the
        date Treasury published — and not on ``local_ts``, which is when this process
        happened to fetch it; a backfill asked for 2023 must not answer with today.
        """
        if end_ns < start_ns:
            return []
        first = datetime.fromtimestamp(start_ns / _NS_PER_SECOND, tz=UTC).year
        last = datetime.fromtimestamp(end_ns / _NS_PER_SECOND, tz=UTC).year
        out: list[MacroSeries] = []
        for year in range(first, last + 1):
            for record in await self.par_yield_curve(year, local_ts=local_ts):
                if record.source_ts is not None and start_ns <= record.source_ts <= end_ns:
                    out.append(record)
        return out
