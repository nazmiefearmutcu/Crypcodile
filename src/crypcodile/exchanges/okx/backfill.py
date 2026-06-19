"""OKX V5 REST backfill for trades, funding rate, and open interest.

Appendix §7:
- Recent trades:   GET /api/v5/market/trades (≤500; ``after``/``before`` tradeId pagination)
- Funding history: GET /api/v5/public/funding-rate-history (``after``/``before`` ms pagination)
- Open interest:   GET /api/v5/market/history-open-interest (``after``/``before`` ms, ``period``)

All endpoints return ``{"code":"0","data":[...]}``.  OKX uses a cursor-style
``after``/``before`` pagination (trade IDs for trades, timestamps for funding/OI).

Pagination stops when the ``data`` array is empty (no ``nextPageCursor`` field,
unlike Bybit).  The ``after`` parameter is set to the last item's tradeId/ts from
the previous page to advance the cursor forward (or backward, depending on
sort order).

OKX default funding interval is 8 hours.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Coroutine
from typing import Any

from crypcodile.schema.records import Funding, OpenInterest, Record, Trade
from crypcodile.util.time import ms_to_ns

from .normalize import _side

EXCHANGE = "okx"
REST_BASE = "https://openapi.okx.com/api/v5"
_DEFAULT_PAGE_SIZE = 100  # OKX max is 500 for trades, 100 for funding/OI


# ---------------------------------------------------------------------------
# Pure page parsers (no I/O — testable independently)
# ---------------------------------------------------------------------------


def parse_trades_page(
    raw: dict[str, Any],
    venue: str,
    symbol: str,
    local_ts: int,
) -> list[Trade]:
    """Parse one page of ``/api/v5/market/trades`` response to canonical ``Trade`` records.

    Field mapping (OKX V5):
    - ``tradeId`` → ``id``
    - ``px``      → ``price``
    - ``sz``      → ``amount``
    - ``side``    → side (``buy``/``sell``, already lowercase)
    - ``ts``      → ``exchange_ts`` (ms → ns)
    """
    out: list[Trade] = []
    items: list[dict[str, Any]] = raw.get("data") or []
    for entry in items:
        out.append(
            Trade(
                exchange=venue,
                symbol=f"{venue}:{symbol}",
                symbol_raw=symbol,
                exchange_ts=ms_to_ns(int(entry["ts"])),
                local_ts=local_ts,
                id=str(entry["tradeId"]),
                price=float(entry["px"]),
                amount=float(entry["sz"]),
                side=_side(entry["side"]),
            )
        )
    return out


def parse_funding_page(
    raw: dict[str, Any],
    venue: str,
    symbol: str,
    local_ts: int,
) -> list[Funding]:
    """Parse one page of ``/api/v5/public/funding-rate-history`` response.

    Field mapping:
    - ``fundingRate``  → ``funding_rate``
    - ``fundingTime``  → ``funding_timestamp`` (ms → ns) + ``exchange_ts``
    """
    out: list[Funding] = []
    items: list[dict[str, Any]] = raw.get("data") or []
    for entry in items:
        ts_ns = ms_to_ns(int(entry["fundingTime"]))
        out.append(
            Funding(
                exchange=venue,
                symbol=f"{venue}:{symbol}",
                symbol_raw=symbol,
                exchange_ts=ts_ns,
                local_ts=local_ts,
                funding_rate=float(entry["fundingRate"]),
                funding_timestamp=ts_ns,
                interval_hours=8,  # OKX default 8h cadence
            )
        )
    return out


def parse_open_interest_page(
    raw: dict[str, Any],
    venue: str,
    symbol: str,
    local_ts: int,
) -> list[OpenInterest]:
    """Parse one page of ``/api/v5/market/history-open-interest`` response.

    Field mapping:
    - ``oi``    → ``open_interest``
    - ``oiCcy`` → ``open_interest_value`` (base-currency equivalent)
    - ``ts``    → ``exchange_ts`` (ms → ns)
    """
    out: list[OpenInterest] = []
    items: list[dict[str, Any]] = raw.get("data") or []
    for entry in items:
        out.append(
            OpenInterest(
                exchange=venue,
                symbol=f"{venue}:{symbol}",
                symbol_raw=symbol,
                exchange_ts=ms_to_ns(int(entry["ts"])),
                local_ts=local_ts,
                open_interest=float(entry["oi"]),
                open_interest_value=float(entry["oiCcy"]) if entry.get("oiCcy") else None,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Callback type aliases
# ---------------------------------------------------------------------------

FetchTradesFn = Callable[
    # symbol, inst_type, after, before, limit
    [str, str, "str | None", "str | None", int],
    Coroutine[Any, Any, dict[str, Any]],
]
FetchFundingFn = Callable[
    # symbol, inst_type, after, before, limit
    [str, str, "str | None", "str | None", int],
    Coroutine[Any, Any, dict[str, Any]],
]
FetchOpenInterestFn = Callable[
    # symbol, inst_type, period, after, before, limit
    [str, str, str, "str | None", "str | None", int],
    Coroutine[Any, Any, dict[str, Any]],
]


# ---------------------------------------------------------------------------
# OKXBackfill — pagination logic, injectable I/O callbacks
# ---------------------------------------------------------------------------


class OKXBackfill:
    """Paginated OKX V5 REST backfill for trades, funding, and open interest.

    All I/O is injected via callback parameters so pagination logic is testable
    without a live network connection.

    OKX pagination uses ``after`` / ``before`` cursor-style parameters.  For
    trades, ``after`` is the last ``tradeId`` seen (moving backward in time on
    each page since OKX returns descending order).  Pagination stops when the
    ``data`` array is empty.
    """

    def __init__(
        self,
        fetch_trades: FetchTradesFn | None,
        fetch_funding: FetchFundingFn | None,
        fetch_open_interest: FetchOpenInterestFn | None,
    ) -> None:
        self._fetch_trades = fetch_trades
        self._fetch_funding = fetch_funding
        self._fetch_open_interest = fetch_open_interest

    async def backfill_trades(
        self,
        venue: str,
        symbol: str,
        inst_type: str,
        start_ns: int,
        end_ns: int,
        page_size: int = _DEFAULT_PAGE_SIZE,
        local_ts: int = 0,
    ) -> AsyncIterator[Record]:
        """Yield Trade records for the given time range.

        OKX /market/trades supports ``after`` (tradeId) cursor pagination.
        Pagination stops when the ``data`` array is empty.  Records are
        filtered to [start_ns, end_ns] after fetching.
        """
        if self._fetch_trades is None:
            return

        after: str | None = None
        while True:
            raw = await self._fetch_trades(symbol, inst_type, after, None, page_size)
            records = parse_trades_page(raw, venue=venue, symbol=symbol, local_ts=local_ts)

            if not records:
                break

            stop = False
            for r in records:
                if r.exchange_ts is not None:
                    if r.exchange_ts > end_ns:
                        continue
                    if r.exchange_ts < start_ns:
                        stop = True
                        break
                yield r

            if stop:
                break

            # Use the last tradeId as the cursor for the next page
            last_record = records[-1]
            after = last_record.id

    async def backfill_funding(
        self,
        venue: str,
        symbol: str,
        inst_type: str,
        start_ns: int,
        end_ns: int,
        page_size: int = _DEFAULT_PAGE_SIZE,
        local_ts: int = 0,
    ) -> AsyncIterator[Funding]:
        """Yield Funding records for the given time range.

        OKX /public/funding-rate-history supports ``after`` timestamp cursor.
        Pagination stops when the ``data`` array is empty.
        """
        if self._fetch_funding is None:
            return

        after: str | None = None
        while True:
            raw = await self._fetch_funding(symbol, inst_type, after, None, page_size)
            records = parse_funding_page(raw, venue=venue, symbol=symbol, local_ts=local_ts)

            if not records:
                break

            stop = False
            for record in records:
                if record.exchange_ts is not None:
                    if record.exchange_ts > end_ns:
                        continue
                    if record.exchange_ts < start_ns:
                        stop = True
                        break
                yield record

            if stop:
                break

            # Advance cursor using the last fundingTime string
            raw_items: list[dict[str, Any]] = raw.get("data") or []
            if raw_items:
                after = str(raw_items[-1].get("fundingTime", ""))
            else:
                break

            if not after:
                break

    async def backfill_open_interest(
        self,
        venue: str,
        symbol: str,
        inst_type: str,
        start_ns: int,
        end_ns: int,
        period: str = "1H",
        page_size: int = _DEFAULT_PAGE_SIZE,
        local_ts: int = 0,
    ) -> AsyncIterator[OpenInterest]:
        """Yield OpenInterest records for the given time range.

        OKX /market/history-open-interest supports ``after`` timestamp cursor
        and a ``period`` parameter (``5m``, ``1H``, ``1D``, etc.).
        Pagination stops when the ``data`` array is empty.
        """
        if self._fetch_open_interest is None:
            return

        after: str | None = None
        while True:
            raw = await self._fetch_open_interest(
                symbol, inst_type, period, after, None, page_size
            )
            records = parse_open_interest_page(raw, venue=venue, symbol=symbol, local_ts=local_ts)

            if not records:
                break

            stop = False
            for record in records:
                if record.exchange_ts is not None:
                    if record.exchange_ts > end_ns:
                        continue
                    if record.exchange_ts < start_ns:
                        stop = True
                        break
                yield record

            if stop:
                break

            # Advance cursor using the last ts
            raw_items = raw.get("data") or []
            if raw_items:
                after = str(raw_items[-1].get("ts", ""))
            else:
                break

            if not after:
                break


# ---------------------------------------------------------------------------
# Live aiohttp fetch helpers (used by the connector at runtime)
# ---------------------------------------------------------------------------


async def _live_fetch_trades(  # pragma: no cover
    symbol: str,
    inst_type: str,
    after: str | None,
    before: str | None,
    limit: int,
    session: aiohttp.ClientSession | None = None,
) -> dict[str, Any]:
    """Fetch one trades page from the OKX V5 REST API."""
    from crypcodile.exchanges.base import http_get_helper

    params: dict[str, Any] = {"instId": symbol, "limit": limit}
    if after:
        params["after"] = after
    if before:
        params["before"] = before

    url = f"{rest_base}/market/trades"
    return await http_get_helper(url, params=params, session=session)


async def _live_fetch_funding(  # pragma: no cover
    symbol: str,
    inst_type: str,
    after: str | None,
    before: str | None,
    limit: int,
    *,
    rest_base: str = REST_BASE,
    session: aiohttp.ClientSession | None = None,
) -> dict[str, Any]:
    """Fetch one funding rate history page from the OKX V5 REST API."""
    from crypcodile.exchanges.base import http_get_helper

    params: dict[str, Any] = {"instId": symbol, "limit": limit}
    if after:
        params["after"] = after
    if before:
        params["before"] = before

    url = f"{rest_base}/public/funding-rate-history"
    return await http_get_helper(url, params=params, session=session)


async def _live_fetch_open_interest(  # pragma: no cover
    symbol: str,
    inst_type: str,
    period: str,
    after: str | None,
    before: str | None,
    limit: int,
    *,
    rest_base: str = REST_BASE,
    session: aiohttp.ClientSession | None = None,
) -> dict[str, Any]:
    """Fetch one open interest history page from the OKX V5 REST API."""
    from crypcodile.exchanges.base import http_get_helper

    params: dict[str, Any] = {
        "instId": symbol,
        "instType": inst_type,
        "period": period,
        "limit": limit,
    }
    if after:
        params["after"] = after
    if before:
        params["before"] = before

    url = f"{rest_base}/market/history-open-interest"
    return await http_get_helper(url, params=params, session=session)


def make_live_backfill(rest_base: str = REST_BASE) -> OKXBackfill:  # pragma: no cover
    """Create an ``OKXBackfill`` wired to live OKX V5 REST endpoints."""
    import functools

    return OKXBackfill(
        fetch_trades=functools.partial(_live_fetch_trades, rest_base=rest_base),
        fetch_funding=functools.partial(_live_fetch_funding, rest_base=rest_base),
        fetch_open_interest=functools.partial(_live_fetch_open_interest, rest_base=rest_base),
    )
