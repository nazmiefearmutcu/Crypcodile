"""The equity halves of the four spread capabilities, and the risk-free leg they share.

``basis``, ``perp-basis``, ``spot-future-basis`` and ``funding-apr`` are one trade priced
four ways. Crypto reads all four off records a venue publishes directly: a perpetual's
``derivative_ticker`` carries its mark *and* its index on one row, and its ``funding``
channel is the financing rate settled in cash every eight hours. Equity publishes neither.
An equity derivative quotes a price and nothing else, and the cost of holding the shares it
substitutes for — the financing an equity long pays and the dividends it receives — is
published somewhere else entirely: by the US Treasury, and in the issuer's own dividend
declarations. Assembling those into the same four answers is what M5 is.

**What each equity half computes, and why it is that and not something else.**

``basis``
    Two symbols, a cash leg and a derivative leg, ASOF-joined and spread. Structurally
    identical to :func:`~crocodile.crypto.analytics.basis.spot_perp_basis`, because
    nothing in that measurement is about perpetuals: it is "where the derivative market
    prices this thing against where the cash market does", and equity index futures and
    single-stock futures are quoted continuously against a cash leg exactly as a perpetual
    is. The shared ``BasisParams`` spells the second leg ``perp_symbol``, which for
    equities names the derivative leg; the field name is the capability's contract across
    both asset classes and renaming it per market is the drift the single registry exists
    to stop.

``perp-basis``
    One symbol, and the reason it is one symbol is that ``derivative_ticker`` carries the
    derivative's price and its reference price on the *same record*. No equity source in
    this tree publishes such a record — checked: nothing under ``equity/providers``
    constructs a ``DerivativeTicker``. Binding the crypto function to ``EQUITY`` would
    therefore repeat the ``fn=slippage`` defect exactly, a declaration naming a code path
    an equity lake can never reach.

    What equity *does* publish, natively and for one symbol, is an option chain — and put-
    call parity is the equity market's own statement of the same spread. At a given strike
    and expiry, ``C - P = (F - K) / (1 + rT)``, so the option market's price for the
    underlying is ``F = K + (C - P)(1 + rT)``. That is a mark, quoted by the derivative
    market, against an index, quoted by the cash market — the same two columns and the
    same two ratios, from one symbol, and it needs the discount factor, which is precisely
    why this capability sits on M5 alongside the other three rather than on M1 with the
    options family. The forward is read at the strike nearest the cash price, because
    parity is exact at every strike in theory and least contaminated by bid-ask width at
    the money in practice.

``spot-future-basis``
    Two symbols and an expiry, so this is the one with a horizon, and a horizon is what
    turns a spread into a carry: ``carry_pct = annualized_pct - risk_free_rate``. This is
    the flagship of M5 and the only one of the four whose answer gains a column that the
    crypto half does not have, because the crypto half never needed to ask what money
    costs — a perpetual quotes that directly.

``funding-apr``
    One symbol, and the crypto shape is a settlement log: one row per funding event with
    the rate, the interval, its APR and a running sum. The equity form of "what does a
    holder of this position pay per period" is not published as a rate, it is the two
    halves of the cost of carry: the holder pays financing and receives dividends. The
    dividend leg is in ``corp_action``, natively, from three different providers; the
    financing leg is the Treasury curve. So a row is emitted per dividend event, the
    period is the gap since the previous one, and the sign convention is crypto's
    unchanged — positive means the position holder pays — which makes a received dividend
    a *negative* funding rate and the two series readable on one axis.

**Which instant bounds which leg.** ``[start_ns, end_ns]`` bounds the *prices*, on
``local_ts``, which is what every other capability here means by a window and what
``Catalog.scan`` filters. The risk-free leg is not a price in the window: it is the rate in
force at each price's instant, and "in force" is a fact about the Treasury's publication
date rather than about when this process happened to fetch the file. So the yield lookup
runs against ``source_ts`` — the date on the curve — and is the one place in this module
where the window is not ``local_ts``. Doing it the other way round has a specific failure:
a year of curve backfilled in one pass shares one ``local_ts``, so every quote in the lake
would fall inside whichever window contained the ingest, and outside every other one.

**Raw SQL, and the rule about it.** ``CapabilityContext.query`` is the only way an
implementation may run *caller-supplied* SQL, because that is what its readonly and
row-limit policy is for. The three statements below are module-authored constants with
bound parameters, filtering on data columns ``Catalog.scan`` cannot express — an option
chain's ``underlying``, a curve's ``source_ts``. They go through
:meth:`~crocodile.core.store.catalog.Catalog.query`, which is the shape
:func:`~crocodile.crypto.analytics.volsurface.iv_surface` and
:func:`~crocodile.crypto.analytics.basis.spot_perp_basis` already use for the same reason;
a row limit applied to an internal analytical scan would silently truncate an answer rather
than protect anything.
"""

from __future__ import annotations

import logging
from bisect import bisect_right
from typing import Final, NamedTuple

import polars as pl

from crocodile.core.analytics.carry import (
    DAYS_PER_YEAR,
    NS_PER_DAY,
    annualise_over_days,
    apr_from_rate,
    carry_over_risk_free,
    days_between,
    hours_between,
    spread,
)
from crocodile.core.schema.enums import CorpActionType
from crocodile.core.schema.provenance import confidence_for
from crocodile.core.store.catalog import Catalog
from crocodile.equity.providers.treasury.client import SOURCE as TREASURY_SOURCE
from crocodile.equity.providers.treasury.client import tenor_days

log = logging.getLogger(__name__)

__all__ = [
    "CARRY_BASIS",
    "RiskFreeCurve",
    "RiskFreeQuote",
    "equity_basis",
    "equity_forward_basis",
    "equity_funding_apr",
    "equity_spot_future_carry",
    "price_leg",
    "risk_free_curve",
]

CARRY_BASIS: Final = "treasury_carry"
"""The registered basis every carry row here scores itself against."""

_PRICE_CHANNELS: Final[tuple[tuple[str, str], ...]] = (
    ("trade", "price"),
    ("ohlcv", "close"),
    ("index_value", "value"),
)
"""Where an equity price series lives, best first.

Three channels rather than one because equity's price series are not all trades. A single
stock arrives as ``trade`` prints from Alpaca or as daily ``ohlcv`` bars from Stooq; an
index arrives as ``index_value``, which is the only channel a level like ``^SPX`` is ever
written to. Crypto needs no such list — ``spot_future_basis`` reads ``trade`` for both legs
and that is where crypto spot lives — so this is a genuine asymmetry in the *data*, not in
the measurement, and it belongs here rather than in ``core``.

First non-empty wins, and the order is a preference for granularity: a print is an
observation of a transaction, a bar's close is the last print in a bucket, and an index
level is a computed statistic the index provider publishes. Mixing two channels into one
leg would put a print and a daily close in one series with nothing on the row to say
which.
"""

_OPTION_MID_COLUMNS: Final[tuple[str, ...]] = ("bid_px", "ask_px", "mark_price", "last_price")

_MIN_PARITY_STRIKES: Final = 1
"""Strikes needed at one expiry before a forward can be read. One: parity holds at every
strike individually, so a single call/put pair is a complete observation of it. This is
not the ``iv-surface`` case, where a cross-section needs a cross-section."""


class RiskFreeQuote(NamedTuple):
    """One published curve point, and what it is being used for.

    Every field is on the emitted row, so a reader can see which number was subtracted
    rather than take the carry on trust — the caveat ``treasury_carry``'s registration
    states is that a par yield is a proxy for a repo rate, and a proxy that names itself is
    a different thing from one that does not.
    """

    rate: float
    """The par yield as a decimal fraction (0.0412 is 4.12 %)."""

    tenor_days: float
    """Nominal length of the curve point chosen, in days."""

    quote_ts: int
    """Publication instant — the curve's own date at UTC midnight."""

    date: str
    """That date, as the file spelled it after normalisation: ``YYYY-MM-DD``."""

    symbol: str
    """Which curve point, e.g. ``treasury:UST3M``."""


class RiskFreeCurve:
    """Every Treasury curve point in the lake up to an instant, queryable by horizon.

    Built once per call and asked once per row. The alternative — a query per row — is the
    same answer at a few thousand times the cost, because the curve moves once a day and a
    capability's window holds one price series.
    """

    def __init__(self, points: dict[str, list[tuple[int, str, float]]]) -> None:
        self._points = points
        self._stamps = {symbol: [point[0] for point in series] for symbol, series in points.items()}
        self._by_tenor: list[tuple[float, str]] = sorted(
            (days, symbol) for symbol in points if (days := tenor_days(symbol)) is not None
        )

    def __bool__(self) -> bool:
        return bool(self._by_tenor)

    def _tenor_for(self, horizon_days: float) -> tuple[float, str] | None:
        """The shortest published tenor at least as long as the horizon, else the longest.

        No interpolation between curve points, and that is a decision rather than a
        shortcut. The row reports ``risk_free_rate`` beside a ``risk_free_tenor_days`` and
        a ``risk_free_date``, which together say *this published number was subtracted*.
        A linear blend of the 1-month and 3-month points is not a number the Treasury
        published, so it could not be reported that way — it would need its own basis and
        its own argument about how well a straight line stands in for the curve's shape,
        and that argument is not one this module has evidence for.

        Rounding up rather than to the nearest is the conservative direction for a carry
        over a short horizon: the curve is usually upward-sloping, so the longer tenor
        yields more, and subtracting more understates the excess return the capability
        reports.
        """
        for days, symbol in self._by_tenor:
            if days >= horizon_days:
                return days, symbol
        return self._by_tenor[-1] if self._by_tenor else None

    def at(self, at_ns: int, horizon_days: float) -> RiskFreeQuote | None:
        """The most recent curve point at or before ``at_ns`` for ``horizon_days``.

        ``None`` when the lake holds no curve at all, or none published early enough. The
        caller reports the absence rather than substituting a zero; see
        :func:`~crocodile.core.analytics.carry.carry_over_risk_free` for why a zero would
        be indistinguishable from a real zero-rate answer.
        """
        chosen = self._tenor_for(horizon_days)
        if chosen is None:
            return None
        days, symbol = chosen
        index = bisect_right(self._stamps[symbol], at_ns) - 1
        if index < 0:
            return None
        quote_ts, date, value = self._points[symbol][index]
        return RiskFreeQuote(
            rate=value, tenor_days=days, quote_ts=quote_ts, date=date, symbol=symbol
        )


_CURVE_SQL: Final = """
    SELECT symbol, coalesce(source_ts, local_ts) AS quote_ts, date_val, value
    FROM macro_series
    WHERE symbol LIKE ?
      AND value IS NOT NULL
      AND coalesce(source_ts, local_ts) <= ?
    ORDER BY symbol, quote_ts
"""


def risk_free_curve(catalog: Catalog, up_to_ns: int) -> RiskFreeCurve:
    """Load every Treasury curve point published at or before ``up_to_ns``.

    An empty curve is an empty :class:`RiskFreeCurve` and not an exception: a lake that has
    never been pointed at the Treasury feed is the ordinary state of a fresh install, and
    the capabilities that can still answer without a financing leg — ``basis``, and
    ``spot-future-basis`` asked without an expiry — must go on answering.

    ``coalesce(source_ts, local_ts)`` because ``source_ts`` is nullable on every record by
    construction and a curve point whose publication date went missing is better placed at
    its ingest instant than dropped: the staleness it is then scored on is an overstatement
    of how fresh it is, which is the safe direction.
    """
    catalog.refresh_views()
    try:
        raw = catalog.query(_CURVE_SQL, params=[f"{TREASURY_SOURCE}:%", up_to_ns])
    except Exception as exc:
        # A lake that has never held a macro_series partition raises rather than
        # returning nothing, and that is not an error here: it is the ordinary state of
        # an install nobody has pointed at the Treasury feed.
        log.debug("no Treasury curve available: %s", exc)
        return RiskFreeCurve({})
    points: dict[str, list[tuple[int, str, float]]] = {}
    for row in raw.iter_rows(named=True):
        symbol = str(row["symbol"])
        if tenor_days(symbol) is None:
            continue
        points.setdefault(symbol, []).append(
            (int(row["quote_ts"]), str(row["date_val"]), float(row["value"]))
        )
    for series in points.values():
        series.sort()
    return RiskFreeCurve(points)


def price_leg(catalog: Catalog, symbol: str, start_ns: int, end_ns: int) -> pl.DataFrame:
    """Return ``[local_ts, price]`` for ``symbol``, from the first channel that has rows.

    See :data:`_PRICE_CHANNELS` for the order and the argument for it. Non-positive and
    null prices are dropped here rather than downstream, because every consumer divides by
    one of them: a zero cash leg is an infinite ``basis_pct``, which is the guard
    ``perp_basis`` already applies to ``index_price``
    (``crypto/analytics/basis.py:335``).

    Returns an empty frame with no columns when no channel has anything, which is the
    empty-result contract ``resample_ohlcv``, ``funding_apr`` and both basis functions
    share.
    """
    for channel, column in _PRICE_CHANNELS:
        raw = catalog.scan(channel, symbol, start_ns, end_ns)
        if len(raw) == 0 or column not in raw.columns:
            continue
        leg = (
            raw.select(["local_ts", column])
            .rename({column: "price"})
            .with_columns(
                [pl.col("local_ts").cast(pl.Int64), pl.col("price").cast(pl.Float64)]
            )
            .filter(pl.col("price").is_not_null() & (pl.col("price") > 0.0))
            .unique(subset=["local_ts"], keep="last", maintain_order=False)
            .sort("local_ts")
        )
        if len(leg) > 0:
            return leg
    return pl.DataFrame()


def _asof_pair(rich: pl.DataFrame, cheap: pl.DataFrame) -> pl.DataFrame:
    """Pair each ``rich`` observation with the nearest *prior* ``cheap`` one.

    ``join_asof`` rather than the DuckDB ``ASOF JOIN`` the crypto functions register two
    temporary relations to run. The join is the same one — backward, on ``local_ts`` — and
    doing it in Polars keeps this module from having to name, register and unregister
    relations on a shared connection, which is three ``try/except/pass`` blocks in
    ``spot_perp_basis`` guarding against a name collision that a second concurrent caller
    on one catalog would produce.
    """
    if len(rich) == 0 or len(cheap) == 0:
        return pl.DataFrame()
    paired = rich.rename({"price": "rich"}).join_asof(
        cheap.rename({"price": "cheap"}), on="local_ts", strategy="backward"
    )
    return paired.filter(pl.col("cheap").is_not_null() & (pl.col("cheap") > 0.0))


def _spread_columns(paired: pl.DataFrame) -> tuple[list[float], list[float | None]]:
    """Compute ``(basis, basis_pct)`` for a paired frame through the shared helper."""
    values: list[float] = []
    percents: list[float | None] = []
    for row in paired.iter_rows(named=True):
        difference, percent = spread(float(row["rich"]), float(row["cheap"]))
        values.append(difference)
        percents.append(percent)
    return values, percents


def equity_basis(
    catalog: Catalog,
    spot_symbol: str,
    derivative_symbol: str,
    start_ns: int,
    end_ns: int,
) -> pl.DataFrame:
    """Spread the derivative leg against the cash leg, ASOF-joined onto the prior print.

    Args:
        catalog: The lake.
        spot_symbol: The cash leg.
        derivative_symbol: The derivative quoted against it — an index or single-stock
            future, or any instrument whose price is expected to track the cash one.
        start_ns: Inclusive lower bound on ``local_ts``.
        end_ns: Inclusive upper bound on ``local_ts``.

    Returns:
        ``[local_ts, spot_price, perp_price, basis, basis_pct]`` ordered by ``local_ts``,
        column for column what :func:`~crocodile.crypto.analytics.basis.spot_perp_basis`
        returns — including the column named ``perp_price``, which for equities holds the
        derivative leg. One capability publishes one result schema; a per-asset-class
        column name would mean a caller had to know which market answered before it could
        read the answer, which is the opposite of what the symmetry promise is for.

        ``pl.DataFrame()`` when either leg is empty.

    No risk-free term and no annualisation, because there is no horizon here: ``basis``
    takes no expiry on either asset class, and annualising a spread over an unstated
    horizon would require inventing one.
    """
    derivative = price_leg(catalog, derivative_symbol, start_ns, end_ns)
    cash = price_leg(catalog, spot_symbol, start_ns, end_ns)
    paired = _asof_pair(derivative, cash)
    if len(paired) == 0:
        return pl.DataFrame()
    values, percents = _spread_columns(paired)
    return pl.DataFrame(
        {
            "local_ts": paired["local_ts"].cast(pl.Int64),
            "spot_price": paired["cheap"].cast(pl.Float64),
            "perp_price": paired["rich"].cast(pl.Float64),
            "basis": pl.Series(values, dtype=pl.Float64),
            "basis_pct": pl.Series(percents, dtype=pl.Float64),
        }
    ).sort("local_ts")


def equity_spot_future_carry(
    catalog: Catalog,
    future_symbol: str,
    spot_symbol: str,
    start_ns: int,
    end_ns: int,
    expiry_ns: int | None = None,
) -> pl.DataFrame:
    """A dated future against spot, annualised and net of the published financing rate.

    Args:
        catalog: The lake.
        future_symbol: The dated contract.
        spot_symbol: The cash leg it settles against.
        start_ns: Inclusive lower bound on ``local_ts``.
        end_ns: Inclusive upper bound on ``local_ts``.
        expiry_ns: The contract's expiry. Without it there is no horizon, so there is no
            annualisation and no carry, and the result is the same five columns
            ``equity_basis`` returns — which is exactly how the crypto half behaves
            (``crypto/analytics/basis.py:269``) and what
            ``tests/analytics/test_basis.py:228`` pins for it.

    Returns:
        ``[local_ts, future_price, spot_price, basis, basis_pct]``, and when ``expiry_ns``
        is given additionally ``annualized_pct``, ``risk_free_rate``,
        ``risk_free_tenor_days``, ``risk_free_date``, ``carry_pct`` and
        ``prov_confidence``.

        ``carry_pct`` is the number this capability exists for: what the trade earns above
        the cost of borrowing the money to put it on, annualised. ``risk_free_rate`` and
        its two companions are on the row so the subtraction is auditable — see
        :class:`RiskFreeQuote`.

        ``pl.DataFrame()`` when either price leg is empty.

    A row whose expiry has passed carries ``annualized_pct = None`` and therefore
    ``carry_pct = None``, propagated from
    :func:`~crocodile.core.analytics.carry.annualise_over_days`. The rows are kept rather
    than dropped: the basis they report is a real spread that was really quoted, and the
    fact that it cannot be annualised is stated in the column rather than by the row's
    absence.
    """
    future = price_leg(catalog, future_symbol, start_ns, end_ns)
    cash = price_leg(catalog, spot_symbol, start_ns, end_ns)
    paired = _asof_pair(future, cash)
    if len(paired) == 0:
        return pl.DataFrame()

    values, percents = _spread_columns(paired)
    frame = pl.DataFrame(
        {
            "local_ts": paired["local_ts"].cast(pl.Int64),
            "future_price": paired["rich"].cast(pl.Float64),
            "spot_price": paired["cheap"].cast(pl.Float64),
            "basis": pl.Series(values, dtype=pl.Float64),
            "basis_pct": pl.Series(percents, dtype=pl.Float64),
        }
    ).sort("local_ts")

    if expiry_ns is None:
        return frame

    curve = risk_free_curve(catalog, end_ns)
    annualised: list[float | None] = []
    rates: list[float | None] = []
    tenors: list[float | None] = []
    dates: list[str | None] = []
    carries: list[float | None] = []
    confidences: list[float] = []

    for row in frame.iter_rows(named=True):
        at_ns = int(row["local_ts"])
        horizon_days = days_between(at_ns, expiry_ns)
        percent = row["basis_pct"]
        annual = annualise_over_days(percent, horizon_days) if percent is not None else None
        quote = curve.at(at_ns, horizon_days) if horizon_days > 0.0 else None
        annualised.append(annual)
        rates.append(None if quote is None else quote.rate)
        tenors.append(None if quote is None else quote.tenor_days)
        dates.append(None if quote is None else quote.date)
        carries.append(carry_over_risk_free(annual, None if quote is None else quote.rate))
        confidences.append(
            _carry_confidence(at_ns=at_ns, horizon_days=horizon_days, quote=quote)
        )

    return frame.with_columns(
        [
            pl.Series("annualized_pct", annualised, dtype=pl.Float64),
            pl.Series("risk_free_rate", rates, dtype=pl.Float64),
            pl.Series("risk_free_tenor_days", tenors, dtype=pl.Float64),
            pl.Series("risk_free_date", dates, dtype=pl.Utf8),
            pl.Series("carry_pct", carries, dtype=pl.Float64),
            pl.Series("prov_confidence", confidences, dtype=pl.Float64),
        ]
    )


def _carry_confidence(
    *, at_ns: int, horizon_days: float, quote: RiskFreeQuote | None, n_price_legs: int = 2
) -> float:
    """Score one carry row through the registered ``treasury_carry`` formula.

    The three observables it needs are assembled here and nowhere else, so there is one
    place where "the yield leg was absent" is encoded as "the yield is exactly as stale as
    the horizon is long" — the equivalence ``treasury_carry``'s registration argues for.

    ``horizon_days`` is clamped to at least one nanosecond because a non-positive horizon
    is already reported as ``annualized_pct = None``; the formula rejects a zero
    denominator, and raising out of a confidence computation for a row that has already
    said it cannot be annualised would turn an expired contract into an exception.
    """
    horizon_ns = max(int(horizon_days * NS_PER_DAY), 1)
    age_ns = horizon_ns if quote is None else max(at_ns - quote.quote_ts, 0)
    return confidence_for(
        CARRY_BASIS,
        {
            "n_price_legs": n_price_legs,
            "yield_age_ns": min(age_ns, horizon_ns),
            "horizon_ns": horizon_ns,
        },
    )


_CHAIN_SQL: Final = """
    SELECT local_ts, expiry, strike, opt_type, bid_px, ask_px, mark_price, last_price
    FROM options_chain
    WHERE UPPER(underlying) = UPPER(?)
      AND local_ts BETWEEN ? AND ?
    ORDER BY local_ts, expiry, strike
"""


def _option_mid(row: dict[str, object]) -> float | None:
    """A contract's price: the quoted mid, else the mark, else the last print.

    The order is a preference for *currency*, not for precision. A mid is where the market
    is right now; a mark is the venue's own valuation, which is a mid on a quiet contract
    and a model on an illiquid one; a last print may be hours old. Parity read off two
    stale prints from different times is not a forward, it is two unrelated observations
    subtracted — so ``last_price`` is the fallback of last resort rather than a peer of
    the other two.
    """
    bid = row.get("bid_px")
    ask = row.get("ask_px")
    if isinstance(bid, float) and isinstance(ask, float) and bid > 0.0 and ask > 0.0:
        return (bid + ask) / 2.0
    for column in ("mark_price", "last_price"):
        value = row.get(column)
        if isinstance(value, float) and value > 0.0:
            return value
    return None


def equity_forward_basis(
    catalog: Catalog,
    symbol: str,
    start_ns: int,
    end_ns: int,
) -> pl.DataFrame:
    """The option market's forward for ``symbol`` against its cash price, per snapshot.

    The equity reading of "mark against index" for a single symbol. Put-call parity at a
    strike ``K`` and expiry ``T`` says ``C - P = (F - K) / (1 + rT)``, so the forward the
    option market is pricing is ``F = K + (C - P)(1 + rT)``. That is a derivative-market
    price for the underlying; the cash price at the same instant is the index leg; the
    spread between them is what a perpetual's mark-minus-index measures.

    Args:
        catalog: The lake.
        symbol: The underlying, as ``options_chain.underlying`` spells it, and as the cash
            price series is symboled. One symbol, which is the shared ``PerpBasisParams``
            schema and the constraint the whole construction is built to honour.
        start_ns: Inclusive lower bound on ``local_ts``.
        end_ns: Inclusive upper bound on ``local_ts``.

    Returns:
        ``[local_ts, mark_price, index_price, basis, basis_pct]`` — the five columns
        :func:`~crocodile.crypto.analytics.basis.perp_basis` returns — plus ``expiry``,
        ``strike``, ``risk_free_rate`` and ``prov_confidence``, which say which contract
        pair the forward came off and what it was discounted at.

        ``pl.DataFrame()`` when there is no chain, no cash price, or no curve.

    **Why a missing curve means no rows rather than an undiscounted forward.** Setting
    ``r = 0`` gives ``F = K + (C - P)``, which is a perfectly computable number and a
    slightly wrong one — and nothing on the row would distinguish it from the same answer
    in a genuinely zero-rate market. This capability is scheduled against M5 *because* it
    needs the financing leg; emitting rows without one would make that schedule's own
    justification false. The absence is reported by the empty result, which is the
    empty-result contract every function in this family already shares.

    The nearest expiry strictly after the snapshot is used, and within it the strike
    closest to the cash price. Parity holds at every strike, so the choice is about noise
    rather than about correctness: away from the money one of the two legs is nearly
    worthless and its bid-ask width is most of its price, so the difference ``C - P``
    inherits that width undiluted.
    """
    catalog.refresh_views()
    try:
        chain = catalog.query(_CHAIN_SQL, params=[symbol, start_ns, end_ns])
    except Exception as exc:
        # Same as `risk_free_curve`: an absent channel is an absent leg, not a fault.
        log.debug("no option chain for %s: %s", symbol, exc)
        return pl.DataFrame()
    if len(chain) == 0:
        return pl.DataFrame()

    cash = price_leg(catalog, symbol, start_ns, end_ns)
    if len(cash) == 0:
        return pl.DataFrame()
    curve = risk_free_curve(catalog, end_ns)
    if not curve:
        return pl.DataFrame()

    cash_ts = cash["local_ts"].to_list()
    cash_px = cash["price"].to_list()

    rows: list[dict[str, object]] = []
    for (at_raw,), snapshot in chain.group_by(["local_ts"], maintain_order=True):
        at_ns = int(at_raw)  # type: ignore[call-overload]
        index = bisect_right(cash_ts, at_ns) - 1
        if index < 0:
            continue
        spot = float(cash_px[index])
        pair = _nearest_parity_pair(snapshot, at_ns=at_ns, spot=spot)
        if pair is None:
            continue
        expiry_ns, strike, call_px, put_px = pair
        horizon_days = days_between(at_ns, expiry_ns)
        quote = curve.at(at_ns, horizon_days)
        if quote is None:
            continue
        forward = strike + (call_px - put_px) * (
            1.0 + quote.rate * horizon_days / DAYS_PER_YEAR
        )
        difference, percent = spread(forward, spot)
        rows.append(
            {
                "local_ts": at_ns,
                "mark_price": forward,
                "index_price": spot,
                "basis": difference,
                "basis_pct": percent,
                "expiry": expiry_ns,
                "strike": strike,
                "risk_free_rate": quote.rate,
                "prov_confidence": _carry_confidence(
                    at_ns=at_ns, horizon_days=horizon_days, quote=quote
                ),
            }
        )

    if not rows:
        return pl.DataFrame()
    return pl.DataFrame(
        rows,
        schema={
            "local_ts": pl.Int64,
            "mark_price": pl.Float64,
            "index_price": pl.Float64,
            "basis": pl.Float64,
            "basis_pct": pl.Float64,
            "expiry": pl.Int64,
            "strike": pl.Float64,
            "risk_free_rate": pl.Float64,
            "prov_confidence": pl.Float64,
        },
    ).sort("local_ts")


def _nearest_parity_pair(
    snapshot: pl.DataFrame, *, at_ns: int, spot: float
) -> tuple[int, float, float, float] | None:
    """Return ``(expiry, strike, call, put)`` for the nearest expiry's most-ATM pair."""
    live = snapshot.filter(pl.col("expiry") > at_ns)
    if len(live) == 0:
        return None
    expiry_ns = int(live["expiry"].min())  # type: ignore[arg-type]
    calls: dict[float, float] = {}
    puts: dict[float, float] = {}
    for row in live.filter(pl.col("expiry") == expiry_ns).iter_rows(named=True):
        price = _option_mid(row)
        if price is None:
            continue
        strike = float(row["strike"])
        side = calls if str(row["opt_type"]).upper().startswith("C") else puts
        side[strike] = price
    shared = sorted(set(calls) & set(puts))
    if len(shared) < _MIN_PARITY_STRIKES:
        return None
    strike = min(shared, key=lambda k: abs(k - spot))
    return expiry_ns, strike, calls[strike], puts[strike]


_DIVIDEND_SQL: Final = """
    SELECT coalesce(source_ts, local_ts) AS event_ts, local_ts, value
    FROM corp_action
    WHERE symbol = ?
      AND type = ?
      AND value IS NOT NULL
      AND local_ts BETWEEN ? AND ?
    ORDER BY event_ts
"""


def equity_funding_apr(
    catalog: Catalog,
    symbol: str,
    start_ns: int,
    end_ns: int,
) -> pl.DataFrame:
    """What holding ``symbol`` costs per period, in the shape ``funding-apr`` returns.

    A crypto perpetual settles a financing rate in cash every few hours and the ``funding``
    channel is the log of it. An equity position is financed too — the holder has money
    tied up at the risk-free rate — and is paid a dividend for the privilege. Those two
    are the cost of carry, and they are the equity form of the same question ``funding-apr``
    asks: what does a holder of this position pay per period?

    Args:
        catalog: The lake.
        symbol: The security.
        start_ns: Inclusive lower bound on ``local_ts``.
        end_ns: Inclusive upper bound on ``local_ts``.

    Returns:
        ``[funding_ts, funding_rate, interval_hours, apr, cumulative_funding]`` — column
        for column what :func:`~crocodile.crypto.analytics.funding.funding_apr` returns —
        plus ``risk_free_apr``, ``carry_apr``, ``dividend`` and ``prov_confidence``.

        ``pl.DataFrame()`` when the symbol has no dividends in the window, or no price to
        express them as a yield against.

    **The sign, which is the one thing that could quietly be backwards.** Crypto's
    convention is that a positive ``funding_rate`` means the long pays. An equity long
    *receives* the dividend, so the dividend leg is negative here, and ``carry_apr =
    risk_free_apr + apr`` is financing paid minus dividends received: the net annual cost
    of holding the position. A positive ``carry_apr`` means carrying the shares costs
    money, which is the ordinary state of a stock yielding less than bills; a negative one
    means the dividend more than covers the financing, which is the equity form of
    negative funding.

    **The period.** ``interval_hours`` is the time since the previous dividend event in
    the window, and for the first row the time since ``start_ns``. That is what makes
    ``apr`` the same operation as its crypto counterpart — the identical
    :func:`~crocodile.core.analytics.carry.apr_from_rate`, called with hours-to-the-next-
    payment instead of the eight hours a perpetual settles on. Where the previous event is
    the window's own start the period is an artefact of the question rather than of the
    security, and a caller asking for a window that begins the day before an ex-date will
    see a very large APR for one row; the interval is on the row so that this is legible
    rather than mysterious.

    ``interval_hours`` is Int64 to match the crypto frame's dtype, and a period shorter
    than an hour is floored to one — two dividends in the same hour is a data defect, and
    a zero there would raise out of :func:`~crocodile.core.analytics.carry.periods_per_year`
    rather than report it.
    """
    catalog.refresh_views()
    try:
        events = catalog.query(
            _DIVIDEND_SQL,
            params=[symbol, CorpActionType.DIVIDEND_CASH.value, start_ns, end_ns],
        )
    except Exception as exc:
        # Same as `risk_free_curve`: an absent channel is an absent leg, not a fault.
        log.debug("no corporate actions for %s: %s", symbol, exc)
        return pl.DataFrame()
    if len(events) == 0:
        return pl.DataFrame()

    prices = price_leg(catalog, symbol, start_ns, end_ns)
    if len(prices) == 0:
        return pl.DataFrame()
    price_ts = prices["local_ts"].to_list()
    price_px = prices["price"].to_list()
    curve = risk_free_curve(catalog, end_ns)

    previous_ns = start_ns
    running = 0.0
    rows: list[dict[str, object]] = []
    for event in events.iter_rows(named=True):
        event_ns = int(event["event_ts"])
        dividend = float(event["value"])
        if dividend <= 0.0:
            continue
        index = bisect_right(price_ts, int(event["local_ts"])) - 1
        if index < 0:
            continue
        price = float(price_px[index])
        interval_hours = max(int(hours_between(previous_ns, event_ns)), 1)
        # Negative: crypto's sign convention is "positive means the holder pays", and a
        # dividend is paid *to* the holder. Flipping it here rather than at the call site
        # is what lets a consumer put the crypto and equity series on one axis.
        rate = -(dividend / price)
        apr = apr_from_rate(rate, interval_hours)
        running += rate
        horizon_days = days_between(previous_ns, event_ns)
        quote = curve.at(event_ns, horizon_days) if horizon_days > 0.0 else None
        rows.append(
            {
                "funding_ts": event_ns,
                "funding_rate": rate,
                "interval_hours": interval_hours,
                "apr": apr,
                "cumulative_funding": running,
                "dividend": dividend,
                "risk_free_apr": None if quote is None else quote.rate,
                "carry_apr": None if quote is None else quote.rate + apr,
                "prov_confidence": _carry_confidence(
                    at_ns=event_ns, horizon_days=horizon_days, quote=quote
                ),
            }
        )
        previous_ns = event_ns

    if not rows:
        return pl.DataFrame()
    return pl.DataFrame(
        rows,
        schema={
            "funding_ts": pl.Int64,
            "funding_rate": pl.Float64,
            "interval_hours": pl.Int64,
            "apr": pl.Float64,
            "cumulative_funding": pl.Float64,
            "dividend": pl.Float64,
            "risk_free_apr": pl.Float64,
            "carry_apr": pl.Float64,
            "prov_confidence": pl.Float64,
        },
    )
