"""Bybit V5 REST backfill for trades, funding, and open interest.

Appendix §7:
- Recent trades:  GET /v5/market/recent-trade   (≤1000 results; cursor pagination)
- Funding history: GET /v5/market/funding/history  (cursor pagination)
- Open interest history: GET /v5/market/open-interest  (cursor pagination)

All endpoints return ``{"result": {"list": [...], "nextPageCursor": "..."}}``; an
empty ``nextPageCursor`` signals the last page.

Side on trades is capitalized (``Buy``/``Sell``) → lowercase canonical.
Bybit default funding interval is 8 hours.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Coroutine
from typing import Any

from crocodile.schema.enums import Side
from crocodile.schema.records import Funding, OpenInterest, Record, Trade
from crocodile.util.time import ms_to_ns

EXCHANGE = "bybit"
REST_BASE = "https://api.bybit.com/v5"
_DEFAULT_PAGE_SIZE = 1000
_DEFAULT_OI_INTERVAL_MIN = 60  # Bybit OI history minimum granularity (minutes)


# ---------------------------------------------------------------------------
# Pure page parsers (no I/O — testable independently)
# ---------------------------------------------------------------------------


def _side(raw: str) -> Side:
    low = raw.lower()
    if low == "buy":
        return Side.BUY
    if low == "sell":
        return Side.SELL
    return Side.UNKNOWN


def parse_trades_page(
    raw: dict[str, Any],
    venue: str,
    symbol: str,
    local_ts: int,
) -> list[Trade]:
    """Parse one page of ``/v5/market/recent-trade`` response to canonical ``Trade`` records.

    Field mapping (Bybit V5):
    - ``execId`` → ``id``
    - ``price``  → ``price``
    - ``size``   → ``amount``
    - ``side``   → side (``Buy``/``Sell``, capitalized → lowercase)
    - ``time``   → ``exchange_ts`` (ms → ns)
    """
    out: list[Trade] = []
    result: dict[str, Any] = raw.get("result") or {}
    items: list[dict[str, Any]] = result.get("list") or []
    for entry in items:
        out.append(
            Trade(
                exchange=venue,
                symbol=f"{venue}:{symbol}",
                symbol_raw=symbol,
                exchange_ts=ms_to_ns(int(entry["time"])),
                local_ts=local_ts,
                id=str(entry.get("execId") or entry.get("i") or ""),
                price=float(entry["price"]),
                amount=float(entry["size"]),
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
    """Parse one page of ``/v5/market/funding/history`` response.

    Field mapping:
    - ``fundingRate``          → ``funding_rate``
    - ``fundingRateTimestamp`` → ``funding_timestamp`` (ms → ns) + ``exchange_ts``
    """
    out: list[Funding] = []
    result: dict[str, Any] = raw.get("result") or {}
    items: list[dict[str, Any]] = result.get("list") or []
    for entry in items:
        ts_ns = ms_to_ns(int(entry["fundingRateTimestamp"]))
        out.append(
            Funding(
                exchange=venue,
                symbol=f"{venue}:{symbol}",
                symbol_raw=symbol,
                exchange_ts=ts_ns,
                local_ts=local_ts,
                funding_rate=float(entry["fundingRate"]),
                funding_timestamp=ts_ns,
                interval_hours=8,  # Bybit default 8h cadence
            )
        )
    return out


def parse_open_interest_page(
    raw: dict[str, Any],
    venue: str,
    symbol: str,
    local_ts: int,
) -> list[OpenInterest]:
    """Parse one page of ``/v5/market/open-interest`` response.

    Field mapping:
    - ``openInterest`` → ``open_interest``
    - ``timestamp``    → ``exchange_ts`` (ms → ns)
    """
    out: list[OpenInterest] = []
    result: dict[str, Any] = raw.get("result") or {}
    items: list[dict[str, Any]] = result.get("list") or []
    for entry in items:
        out.append(
            OpenInterest(
                exchange=venue,
                symbol=f"{venue}:{symbol}",
                symbol_raw=symbol,
                exchange_ts=ms_to_ns(int(entry["timestamp"])),
                local_ts=local_ts,
                open_interest=float(entry["openInterest"]),
                open_interest_value=None,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Callback type aliases
# ---------------------------------------------------------------------------

FetchTradesFn = Callable[
    [str, str, int, "str | None"],  # symbol, category, limit, cursor
    Coroutine[Any, Any, dict[str, Any]],
]
FetchFundingFn = Callable[
    [str, str, int, int, int, "str | None"],  # symbol, category, start_ms, end_ms, limit, cursor
    Coroutine[Any, Any, dict[str, Any]],
]
FetchOpenInterestFn = Callable[
    # symbol, category, interval_min, start_ms, end_ms, limit, cursor
    [str, str, int, int, int, int, "str | None"],
    Coroutine[Any, Any, dict[str, Any]],
]


# ---------------------------------------------------------------------------
# BybitBackfill — pagination logic, injectable I/O callbacks
# ---------------------------------------------------------------------------


class BybitBackfill:
    """Paginated Bybit V5 REST backfill for trades, funding, and open interest.

    All I/O is injected via callback parameters so pagination logic is testable
    without a live network connection.

    Trade pagination: cursor-based (``nextPageCursor``); stop when the cursor is
    empty or when a page is exhausted within the requested time bounds.

    Funding/OI pagination: same cursor approach; the Bybit endpoints also accept
    ``startTime``/``endTime`` (ms) so the first request can be time-bounded.
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
        category: str,
        start_ns: int,
        end_ns: int,
        page_size: int = _DEFAULT_PAGE_SIZE,
        local_ts: int = 0,
    ) -> AsyncIterator[Record]:
        """Yield Trade records for the given time range via cursor pagination.

        ASSUMPTION: Bybit /v5/market/recent-trade returns records in ASCENDING
        timestamp order (oldest first within each page, and pages advance
        forward in time as the cursor progresses).  The stop condition
        ``exchange_ts > end_ns`` is therefore safe: once we see a record above
        ``end_ns`` all subsequent records on the same page (and later pages)
        will also be above the bound, so we can break immediately.  If this
        assumption ever changes (descending order), the stop logic would need to
        be replaced with a full-page filter rather than an early break.
        """
        if self._fetch_trades is None:
            return

        cursor: str | None = None
        while True:
            raw = await self._fetch_trades(symbol, category, page_size, cursor)
            records = parse_trades_page(raw, venue=venue, symbol=symbol, local_ts=local_ts)

            stop = False
            for r in records:
                if r.exchange_ts is not None:
                    if r.exchange_ts > end_ns:
                        stop = True
                        break
                    if r.exchange_ts < start_ns:
                        continue
                yield r

            if stop:
                break

            # Advance cursor
            result: dict[str, Any] = raw.get("result") or {}
            next_cursor: str = result.get("nextPageCursor", "")
            if not next_cursor or not records:
                break
            cursor = next_cursor

    async def backfill_funding(
        self,
        venue: str,
        symbol: str,
        category: str,
        start_ns: int,
        end_ns: int,
        page_size: int = _DEFAULT_PAGE_SIZE,
        local_ts: int = 0,
    ) -> AsyncIterator[Funding]:
        """Yield Funding records for the given time range via cursor pagination.

        Bybit /v5/market/funding/history supports cursor pagination via
        ``nextPageCursor``; long date ranges (>200 records) require multiple
        pages.  The first request is time-bounded via ``startTime``/``endTime``;
        subsequent requests carry the cursor returned by the previous page.
        """
        if self._fetch_funding is None:
            return

        start_ms = start_ns // 1_000_000
        end_ms = end_ns // 1_000_000
        cursor: str | None = None

        while True:
            raw = await self._fetch_funding(symbol, category, start_ms, end_ms, page_size, cursor)
            records = parse_funding_page(raw, venue=venue, symbol=symbol, local_ts=local_ts)
            for record in records:
                yield record

            result: dict[str, Any] = raw.get("result") or {}
            next_cursor: str = result.get("nextPageCursor", "")
            if not next_cursor or not records:
                break
            cursor = next_cursor

    async def backfill_open_interest(
        self,
        venue: str,
        symbol: str,
        category: str,
        start_ns: int,
        end_ns: int,
        interval_min: int = _DEFAULT_OI_INTERVAL_MIN,
        page_size: int = _DEFAULT_PAGE_SIZE,
        local_ts: int = 0,
    ) -> AsyncIterator[OpenInterest]:
        """Yield OpenInterest records for the given time range via cursor pagination.

        Bybit /v5/market/open-interest supports cursor pagination via
        ``nextPageCursor``; long date ranges (>1000 OI intervals) require
        multiple pages.  The first request is time-bounded via
        ``startTime``/``endTime``; subsequent requests carry the cursor
        returned by the previous page.
        """
        if self._fetch_open_interest is None:
            return

        start_ms = start_ns // 1_000_000
        end_ms = end_ns // 1_000_000
        cursor: str | None = None

        while True:
            raw = await self._fetch_open_interest(
                symbol, category, interval_min, start_ms, end_ms, page_size, cursor
            )
            records = parse_open_interest_page(raw, venue=venue, symbol=symbol, local_ts=local_ts)
            for record in records:
                yield record

            result: dict[str, Any] = raw.get("result") or {}
            next_cursor: str = result.get("nextPageCursor", "")
            if not next_cursor or not records:
                break
            cursor = next_cursor


# ---------------------------------------------------------------------------
# Live aiohttp fetch helpers (used by the connector at runtime)
# ---------------------------------------------------------------------------


async def _live_fetch_trades(  # pragma: no cover
    symbol: str,
    category: str,
    limit: int,
    cursor: str | None,
    *,
    rest_base: str = REST_BASE,
) -> dict[str, Any]:
    """Fetch one trades page from the Bybit V5 REST API."""
    import aiohttp

    params: dict[str, Any] = {"symbol": symbol, "category": category, "limit": limit}
    if cursor:
        params["cursor"] = cursor
    async with aiohttp.ClientSession() as session:
        url = f"{rest_base}/market/recent-trade"
        async with session.get(url, params=params) as resp:
            resp.raise_for_status()
            data: dict[str, Any] = await resp.json()
    return data


async def _live_fetch_funding(  # pragma: no cover
    symbol: str,
    category: str,
    start_time_ms: int,
    end_time_ms: int,
    limit: int,
    cursor: str | None = None,
    *,
    rest_base: str = REST_BASE,
) -> dict[str, Any]:
    """Fetch one funding history page from the Bybit V5 REST API."""
    import aiohttp

    params: dict[str, Any] = {
        "symbol": symbol,
        "category": category,
        "startTime": start_time_ms,
        "endTime": end_time_ms,
        "limit": limit,
    }
    if cursor:
        params["cursor"] = cursor
    async with aiohttp.ClientSession() as session:
        url = f"{rest_base}/market/funding/history"
        async with session.get(url, params=params) as resp:
            resp.raise_for_status()
            data: dict[str, Any] = await resp.json()
    return data


async def _live_fetch_open_interest(  # pragma: no cover
    symbol: str,
    category: str,
    interval_min: int,
    start_time_ms: int,
    end_time_ms: int,
    limit: int,
    cursor: str | None = None,
    *,
    rest_base: str = REST_BASE,
) -> dict[str, Any]:
    """Fetch one open interest history page from the Bybit V5 REST API."""
    import aiohttp

    # Bybit intervalTime accepts "5min","15min","30min","1h","4h","1d"
    if interval_min < 15:
        interval_str = "5min"
    elif interval_min < 30:
        interval_str = "15min"
    elif interval_min < 60:
        interval_str = "30min"
    elif interval_min < 240:
        interval_str = "1h"
    elif interval_min < 1440:
        interval_str = "4h"
    else:
        interval_str = "1d"

    params: dict[str, Any] = {
        "symbol": symbol,
        "category": category,
        "intervalTime": interval_str,
        "startTime": start_time_ms,
        "endTime": end_time_ms,
        "limit": limit,
    }
    if cursor:
        params["cursor"] = cursor
    async with aiohttp.ClientSession() as session:
        url = f"{rest_base}/market/open-interest"
        async with session.get(url, params=params) as resp:
            resp.raise_for_status()
            data: dict[str, Any] = await resp.json()
    return data


def make_live_backfill(rest_base: str = REST_BASE) -> BybitBackfill:  # pragma: no cover
    """Create a ``BybitBackfill`` wired to live Bybit V5 REST endpoints."""
    import functools

    return BybitBackfill(
        fetch_trades=functools.partial(_live_fetch_trades, rest_base=rest_base),
        fetch_funding=functools.partial(_live_fetch_funding, rest_base=rest_base),
        fetch_open_interest=functools.partial(_live_fetch_open_interest, rest_base=rest_base),
    )
