"""Binance REST backfill for aggTrades, klines (OHLCV), and open interest.

Appendix §3.2:
- aggTrades: ``/aggTrades`` paginated by ``fromId``; ``m=true`` → SELL (buyer is maker,
  so taker sold); ``T`` field is trade time (ms → ns).
- klines: ``/klines`` response is a list of arrays:
  [openTime, o, h, l, c, v, closeTime, quoteVol, count,
   takerBuyBaseVol, takerBuyQuoteVol, ignored]
  Map to OHLCV with buy_volume=takerBuyBaseVol,
  sell_volume=volume-takerBuyBaseVol, num_trades=count.
- openInterest snapshot: ``/fapi/v1/openInterest`` / ``/dapi/v1/openInterest``
  returns ``{openInterest, symbol, time}``; ``time`` is ms.
- openInterest historical: ``/futures/data/openInterestHist`` returns a list of
  ``{symbol, sumOpenInterest, sumOpenInterestValue, timestamp}``; ``timestamp`` is ms.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Coroutine
from typing import Any

from crocodile.schema.enums import Side
from crocodile.schema.records import OHLCV, OpenInterest, Record, Trade
from crocodile.util.time import ms_to_ns

# ---------------------------------------------------------------------------
# Pure page parsers (no I/O — testable independently)
# ---------------------------------------------------------------------------


def parse_aggtrades_page(
    raw: list[dict[str, Any]],
    venue: str,
    symbol: str,
    local_ts: int,
) -> list[Trade]:
    """Parse one page of ``/aggTrades`` response to canonical ``Trade`` records.

    Appendix §3.2:
    - ``m=true`` → buyer is maker → taker sold → ``Side.SELL``
    - ``T`` = trade time (ms → ns) for ``exchange_ts``
    - ``a`` = agg trade id
    """
    out: list[Trade] = []
    for entry in raw:
        side = Side.SELL if entry["m"] else Side.BUY
        out.append(
            Trade(
                exchange=venue,
                symbol=f"{venue}:{symbol}",
                symbol_raw=symbol,
                exchange_ts=ms_to_ns(entry["T"]),
                local_ts=local_ts,
                id=str(entry["a"]),
                price=float(entry["p"]),
                amount=float(entry["q"]),
                side=side,
            )
        )
    return out


def parse_klines_page(
    raw: list[list[Any]],
    venue: str,
    symbol: str,
    interval: str,
    local_ts: int,
) -> list[OHLCV]:
    """Parse one page of ``/klines`` response to canonical ``OHLCV`` records.

    Kline array layout (appendix §3.2):
    index 0  = openTime (ms)
    index 1  = open
    index 2  = high
    index 3  = low
    index 4  = close
    index 5  = volume (base)
    index 6  = closeTime (ms)
    index 7  = quoteAssetVolume
    index 8  = numberOfTrades
    index 9  = takerBuyBaseAssetVolume  -> buy_volume
    index 10 = takerBuyQuoteAssetVolume
    index 11 = ignored
    """
    out: list[OHLCV] = []
    for kline in raw:
        volume = float(kline[5])
        buy_volume = float(kline[9])
        out.append(
            OHLCV(
                exchange=venue,
                symbol=f"{venue}:{symbol}",
                symbol_raw=symbol,
                exchange_ts=ms_to_ns(int(kline[0])),
                local_ts=local_ts,
                interval=interval,
                open=float(kline[1]),
                high=float(kline[2]),
                low=float(kline[3]),
                close=float(kline[4]),
                volume=volume,
                buy_volume=buy_volume,
                sell_volume=volume - buy_volume,
                num_trades=int(kline[8]),
            )
        )
    return out


def parse_open_interest(
    raw: dict[str, Any],
    venue: str,
    local_ts: int,
) -> OpenInterest:
    """Parse a ``/fapi/v1/openInterest`` (or ``/dapi/v1/openInterest``) snapshot response.

    Returns a single ``OpenInterest`` record. The ``time`` field (ms) maps to
    ``exchange_ts``; there is no ``open_interest_value`` in the snapshot endpoint.
    """
    symbol: str = raw["symbol"]
    return OpenInterest(
        exchange=venue,
        symbol=f"{venue}:{symbol}",
        symbol_raw=symbol,
        exchange_ts=ms_to_ns(int(raw["time"])),
        local_ts=local_ts,
        open_interest=float(raw["openInterest"]),
        open_interest_value=None,
    )


def parse_open_interest_hist(
    raw: list[dict[str, Any]],
    venue: str,
    symbol: str,
    local_ts: int,
) -> list[OpenInterest]:
    """Parse a ``/futures/data/openInterestHist`` response.

    Each entry has ``{symbol, sumOpenInterest, sumOpenInterestValue, timestamp}``
    where ``timestamp`` is milliseconds.
    """
    out: list[OpenInterest] = []
    for entry in raw:
        out.append(
            OpenInterest(
                exchange=venue,
                symbol=f"{venue}:{symbol}",
                symbol_raw=symbol,
                exchange_ts=ms_to_ns(int(entry["timestamp"])),
                local_ts=local_ts,
                open_interest=float(entry["sumOpenInterest"]),
                open_interest_value=float(entry["sumOpenInterestValue"]),
            )
        )
    return out


# ---------------------------------------------------------------------------
# Callback type aliases
# ---------------------------------------------------------------------------

FetchAggradesFn = Callable[
    [str, "int | None", "int | None", "int | None", int],  # symbol, from_id, start, end, limit
    Coroutine[Any, Any, list[dict[str, Any]]],
]
FetchKlinesFn = Callable[
    [str, str, "int | None", "int | None", int],  # symbol, interval, start_ms, end_ms, limit
    Coroutine[Any, Any, list[list[Any]]],
]
FetchOpenInterestFn = Callable[
    [str],  # symbol
    Coroutine[Any, Any, dict[str, Any]],
]
FetchOpenInterestHistFn = Callable[
    [str, str, "int | None", "int | None", int],  # symbol, period, start_ms, end_ms, limit
    Coroutine[Any, Any, list[dict[str, Any]]],
]

# ---------------------------------------------------------------------------
# BinanceBackfill — pagination logic, injectable I/O callbacks
# ---------------------------------------------------------------------------

_DEFAULT_PAGE_SIZE = 1000


class BinanceBackfill:
    """Paginated Binance REST backfill for aggTrades, klines, and open interest.

    All I/O is injected via callback parameters so pagination logic is testable
    without a live network connection.

    aggTrade pagination (appendix §3.2): start from the beginning (or a given
    ``fromId``/``startTime``), yield pages, advance ``fromId = last_id + 1`` on
    each page.  Stop when a page returns fewer items than ``page_size`` (no more
    data available for the requested range).

    klines pagination: issue a single call; Binance returns at most ``limit``
    bars.  For long ranges the caller should chunk by time; this implementation
    issues one call per invocation for simplicity (the backfill bridge in T4.3
    handles multi-chunk iteration).

    openInterest history: single call per invocation (same rationale as klines).
    """

    def __init__(
        self,
        fetch_aggtrades: FetchAggradesFn | None,
        fetch_klines: FetchKlinesFn | None,
        fetch_open_interest: FetchOpenInterestFn | None,
        fetch_open_interest_hist: FetchOpenInterestHistFn | None,
    ) -> None:
        self._fetch_aggtrades = fetch_aggtrades
        self._fetch_klines = fetch_klines
        self._fetch_open_interest = fetch_open_interest
        self._fetch_open_interest_hist = fetch_open_interest_hist

    async def backfill_aggtrades(
        self,
        venue: str,
        symbol: str,
        start_ns: int,
        end_ns: int,
        page_size: int = _DEFAULT_PAGE_SIZE,
        local_ts: int = 0,
    ) -> AsyncIterator[Record]:
        """Yield Trade records for the given time range, paginating by ``fromId``.

        The first page is fetched using ``startTime``/``endTime`` (ms); subsequent
        pages use ``fromId = last_agg_id + 1`` until a partial page is returned
        (fewer items than ``page_size`` → no more data).
        """
        if self._fetch_aggtrades is None:
            return

        start_ms = start_ns // 1_000_000
        end_ms = end_ns // 1_000_000

        from_id: int | None = None
        while True:
            if from_id is None:
                page = await self._fetch_aggtrades(symbol, None, start_ms, end_ms, page_size)
            else:
                page = await self._fetch_aggtrades(symbol, from_id, None, None, page_size)

            if not page:
                break

            records = parse_aggtrades_page(page, venue=venue, symbol=symbol, local_ts=local_ts)
            stop = False
            for r in records:
                if r.exchange_ts is not None and r.exchange_ts > end_ns:
                    # fromId pagination ignores endTime on the wire, so enforce the
                    # requested end bound client-side: drop this trade and stop.
                    stop = True
                    break
                yield r
            if stop:
                break

            if len(page) < page_size:
                # Partial page — no more data
                break

            # Advance fromId to last_id + 1
            last_id = int(page[-1]["a"])
            from_id = last_id + 1

    async def backfill_klines(
        self,
        venue: str,
        symbol: str,
        interval: str,
        start_ns: int,
        end_ns: int,
        page_size: int = _DEFAULT_PAGE_SIZE,
        local_ts: int = 0,
    ) -> AsyncIterator[OHLCV]:
        """Yield OHLCV bars for the given time range.

        Issues a single REST call (one chunk).  For ranges spanning many bars the
        caller should chunk by ``startTime``/``endTime`` and call this method for
        each chunk.
        """
        if self._fetch_klines is None:
            return

        start_ms = start_ns // 1_000_000
        end_ms = end_ns // 1_000_000

        page = await self._fetch_klines(symbol, interval, start_ms, end_ms, page_size)
        bars = parse_klines_page(
            page, venue=venue, symbol=symbol, interval=interval, local_ts=local_ts
        )
        for bar in bars:
            yield bar

    async def backfill_open_interest(
        self,
        venue: str,
        symbol: str,
        local_ts: int = 0,
    ) -> OpenInterest | None:
        """Fetch the current open interest snapshot.

        Returns a single ``OpenInterest`` record or ``None`` if the callback is
        not configured.
        """
        if self._fetch_open_interest is None:
            return None

        raw = await self._fetch_open_interest(symbol)
        return parse_open_interest(raw, venue=venue, local_ts=local_ts)

    async def backfill_open_interest_hist(
        self,
        venue: str,
        symbol: str,
        period: str,
        start_ns: int,
        end_ns: int,
        page_size: int = _DEFAULT_PAGE_SIZE,
        local_ts: int = 0,
    ) -> AsyncIterator[OpenInterest]:
        """Yield historical ``OpenInterest`` records from ``/futures/data/openInterestHist``.

        Issues a single REST call per invocation; the caller should chunk for
        long time ranges.
        """
        if self._fetch_open_interest_hist is None:
            return

        start_ms = start_ns // 1_000_000
        end_ms = end_ns // 1_000_000

        page = await self._fetch_open_interest_hist(symbol, period, start_ms, end_ms, page_size)
        for rec in parse_open_interest_hist(page, venue=venue, symbol=symbol, local_ts=local_ts):
            yield rec


# ---------------------------------------------------------------------------
# Live aiohttp fetch helpers (used by the connector at runtime)
# ---------------------------------------------------------------------------


async def _live_fetch_aggtrades(  # pragma: no cover
    symbol: str,
    from_id: int | None,
    start_time_ms: int | None,
    end_time_ms: int | None,
    limit: int,
    *,
    rest_base: str = "https://api.binance.com/api/v3",
) -> list[dict[str, Any]]:
    """Fetch one aggTrades page from the Binance REST API."""
    import aiohttp

    params: dict[str, Any] = {"symbol": symbol, "limit": limit}
    if from_id is not None:
        params["fromId"] = from_id
    if start_time_ms is not None:
        params["startTime"] = start_time_ms
    if end_time_ms is not None:
        params["endTime"] = end_time_ms

    async with aiohttp.ClientSession() as session:
        url = f"{rest_base}/aggTrades"
        async with session.get(url, params=params) as resp:
            resp.raise_for_status()
            data: list[dict[str, Any]] = await resp.json()
    return data


async def _live_fetch_klines(  # pragma: no cover
    symbol: str,
    interval: str,
    start_time_ms: int | None,
    end_time_ms: int | None,
    limit: int,
    *,
    rest_base: str = "https://api.binance.com/api/v3",
) -> list[list[Any]]:
    """Fetch one klines page from the Binance REST API."""
    import aiohttp

    params: dict[str, Any] = {"symbol": symbol, "interval": interval, "limit": limit}
    if start_time_ms is not None:
        params["startTime"] = start_time_ms
    if end_time_ms is not None:
        params["endTime"] = end_time_ms

    async with aiohttp.ClientSession() as session:
        url = f"{rest_base}/klines"
        async with session.get(url, params=params) as resp:
            resp.raise_for_status()
            data: list[list[Any]] = await resp.json()
    return data


async def _live_fetch_open_interest(  # pragma: no cover
    symbol: str,
    *,
    rest_base: str = "https://fapi.binance.com/fapi/v1",
) -> dict[str, Any]:
    """Fetch current open interest from the Binance USDⓂ REST API."""
    import aiohttp

    async with aiohttp.ClientSession() as session:
        url = f"{rest_base}/openInterest"
        async with session.get(url, params={"symbol": symbol}) as resp:
            resp.raise_for_status()
            data: dict[str, Any] = await resp.json()
    return data


async def _live_fetch_open_interest_hist(  # pragma: no cover
    symbol: str,
    period: str,
    start_time_ms: int | None,
    end_time_ms: int | None,
    limit: int,
    *,
    rest_base: str = "https://fapi.binance.com",
) -> list[dict[str, Any]]:
    """Fetch historical open interest from the Binance futures data endpoint."""
    import aiohttp

    params: dict[str, Any] = {"symbol": symbol, "period": period, "limit": limit}
    if start_time_ms is not None:
        params["startTime"] = start_time_ms
    if end_time_ms is not None:
        params["endTime"] = end_time_ms

    async with aiohttp.ClientSession() as session:
        url = f"{rest_base}/futures/data/openInterestHist"
        async with session.get(url, params=params) as resp:
            resp.raise_for_status()
            data: list[dict[str, Any]] = await resp.json()
    return data


def make_live_backfill(  # pragma: no cover
    rest_base_spot: str = "https://api.binance.com/api/v3",
    rest_base_futures: str = "https://fapi.binance.com/fapi/v1",
    rest_base_futures_data: str = "https://fapi.binance.com",
) -> BinanceBackfill:
    """Create a ``BinanceBackfill`` wired to live Binance REST endpoints."""
    import functools

    return BinanceBackfill(
        fetch_aggtrades=functools.partial(_live_fetch_aggtrades, rest_base=rest_base_spot),
        fetch_klines=functools.partial(_live_fetch_klines, rest_base=rest_base_spot),
        fetch_open_interest=functools.partial(
            _live_fetch_open_interest, rest_base=rest_base_futures
        ),
        fetch_open_interest_hist=functools.partial(
            _live_fetch_open_interest_hist, rest_base=rest_base_futures_data
        ),
    )
