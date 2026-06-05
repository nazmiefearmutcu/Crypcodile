"""Deribit REST backfill for trades and funding history.

Appendix §3.1:
- Trades: ``public/get_last_trades_by_instrument_and_time``
  (``instrument_name``, ``end_timestamp`` ms, ``count`` 1-1000, ``sorting`` asc/desc)
  Returns ``has_more``; paginate by walking ``end_timestamp`` backward using the
  earliest trade timestamp on each page.
- Funding: ``public/get_funding_rate_history``
  (``start_timestamp``, ``end_timestamp``, hourly aggregation).
  Returns ``interest_1h`` and ``interest_8h`` per entry (no field named ``funding_rate``).
  Canonical mapping: ``interest_8h`` -> ``funding_rate``;
  ``interest_1h`` -> ``predicted_funding_rate``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Coroutine
from typing import Any

from crocodile.schema.enums import Side
from crocodile.schema.records import Funding, Liquidation, Record, Trade
from crocodile.util.time import ms_to_ns

EXCHANGE = "deribit"
_DEFAULT_TRADE_COUNT = 1000


# ---------------------------------------------------------------------------
# Pure page parsers (no I/O — testable independently)
# ---------------------------------------------------------------------------


def _side(direction: str) -> Side:
    return Side.BUY if direction == "buy" else Side.SELL if direction == "sell" else Side.UNKNOWN


def parse_trades_page(raw: dict[str, Any], local_ts: int) -> list[Record]:
    """Parse one page of ``public/get_last_trades_by_instrument_and_time`` response.

    Emits a ``Trade`` for every entry and a ``Liquidation`` alongside when the
    trade's ``liquidation`` string-enum field is present (``M``/``T``/``MT``).
    """
    out: list[Record] = []
    result: dict[str, Any] = raw.get("result") or {}
    for t in result.get("trades") or []:
        sym: str = t["instrument_name"]
        side = _side(t["direction"])
        exchange_ts = ms_to_ns(t["timestamp"])
        trade = Trade(
            exchange=EXCHANGE,
            symbol=f"{EXCHANGE}:{sym}",
            symbol_raw=sym,
            exchange_ts=exchange_ts,
            local_ts=local_ts,
            id=str(t["trade_id"]),
            price=float(t["price"]),
            amount=float(t["amount"]),
            side=side,
            liquidation=t.get("liquidation"),
        )
        out.append(trade)
        if t.get("liquidation"):
            out.append(
                Liquidation(
                    exchange=EXCHANGE,
                    symbol=f"{EXCHANGE}:{sym}",
                    symbol_raw=sym,
                    exchange_ts=exchange_ts,
                    local_ts=local_ts,
                    price=float(t["price"]),
                    amount=float(t["amount"]),
                    side=side,
                    id=str(t["trade_id"]),
                )
            )
    return out


def parse_funding_page(
    raw: dict[str, Any], symbol: str, local_ts: int
) -> list[Funding]:
    """Parse one response from ``public/get_funding_rate_history``.

    Canonical mapping (appendix §3.1):
    - ``interest_8h`` → ``funding_rate``
    - ``interest_1h`` → ``predicted_funding_rate``
    """
    out: list[Funding] = []
    entries: list[dict[str, Any]] = raw.get("result") or []
    for entry in entries:
        out.append(
            Funding(
                exchange=EXCHANGE,
                symbol=f"{EXCHANGE}:{symbol}",
                symbol_raw=symbol,
                exchange_ts=ms_to_ns(entry["timestamp"]),
                local_ts=local_ts,
                funding_rate=float(entry["interest_8h"]),
                predicted_funding_rate=float(entry["interest_1h"]),
                interval_hours=8,
                funding_timestamp=ms_to_ns(entry["timestamp"]),
            )
        )
    return out


# ---------------------------------------------------------------------------
# Callback type aliases
# ---------------------------------------------------------------------------

FetchTradesFn = Callable[
    [str, int, int, int],  # instrument, end_ts_ms, count, start_ts_ms
    Coroutine[Any, Any, dict[str, Any]],
]
FetchFundingFn = Callable[
    [str, int, int],  # instrument, start_ts_ms, end_ts_ms
    Coroutine[Any, Any, dict[str, Any]],
]


# ---------------------------------------------------------------------------
# DeribitBackfill — pagination logic, injectable I/O callbacks
# ---------------------------------------------------------------------------


class DeribitBackfill:
    """Paginated Deribit REST backfill for trades and funding.

    I/O is fully injected via ``fetch_trades``/``fetch_funding`` callbacks so the
    pagination logic is testable without a live network.

    Trade pagination (appendix §3.1): start from ``end_ns``, walk ``end_timestamp``
    backward using the earliest (lowest) ``timestamp`` in each page until
    ``has_more`` is ``False`` or all trades are below ``start_ns``.

    Funding pagination: single call per monthly chunk; caller responsible for
    chunking if needed (the current implementation issues one call for the full
    range as Deribit returns monthly chunks natively).
    """

    def __init__(
        self,
        fetch_trades: FetchTradesFn | None,
        fetch_funding: FetchFundingFn | None,
    ) -> None:
        self._fetch_trades = fetch_trades
        self._fetch_funding = fetch_funding

    async def backfill_trades(
        self,
        instrument: str,
        start_ns: int,
        end_ns: int,
        count: int = _DEFAULT_TRADE_COUNT,
        local_ts: int = 0,
    ) -> AsyncIterator[Record]:
        """Yield canonical Records (Trade + Liquidation) for the given time range.

        Paginates by walking ``end_timestamp`` backward:
        each page returns up to ``count`` trades sorted descending; the next page
        uses the smallest ``timestamp`` from the current page as the new
        ``end_timestamp``.
        """
        if self._fetch_trades is None:
            return

        start_ms = start_ns // 1_000_000
        end_ms = end_ns // 1_000_000

        current_end_ms = end_ms
        while True:
            raw = await self._fetch_trades(instrument, current_end_ms, count, start_ms)
            result: dict[str, Any] = raw.get("result") or {}
            trades_data: list[dict[str, Any]] = result.get("trades") or []
            has_more: bool = result.get("has_more", False)

            # Emit records
            records = parse_trades_page(raw, local_ts=local_ts)
            for r in records:
                yield r

            if not has_more or not trades_data:
                break

            # Walk end_timestamp to the earliest trade on this page.
            # Guard: if the earliest timestamp is >= current_end_ms, no progress
            # was made (e.g. 1000+ trades sharing the same millisecond during a
            # liquidation cascade). Break to avoid an infinite re-fetch loop.
            earliest_ts_ms = min(t["timestamp"] for t in trades_data)
            if earliest_ts_ms >= current_end_ms:
                break
            if earliest_ts_ms <= start_ms:
                break
            current_end_ms = earliest_ts_ms

    async def backfill_funding(
        self,
        instrument: str,
        start_ns: int,
        end_ns: int,
        local_ts: int = 0,
    ) -> AsyncIterator[Funding]:
        """Yield Funding records for the given time range from ``get_funding_rate_history``."""
        if self._fetch_funding is None:
            return

        start_ms = start_ns // 1_000_000
        end_ms = end_ns // 1_000_000

        raw = await self._fetch_funding(instrument, start_ms, end_ms)
        for record in parse_funding_page(raw, symbol=instrument, local_ts=local_ts):
            yield record


# ---------------------------------------------------------------------------
# Live aiohttp fetch helpers (used by the connector at runtime)
# ---------------------------------------------------------------------------


async def _live_fetch_trades(  # pragma: no cover
    instrument: str,
    end_ts_ms: int,
    count: int,
    start_ts_ms: int,
    *,
    rest_base: str = "https://www.deribit.com/api/v2",
) -> dict[str, Any]:
    """Fetch one trades page from the Deribit REST API."""
    import aiohttp

    params: dict[str, Any] = {
        "instrument_name": instrument,
        "end_timestamp": end_ts_ms,
        "count": count,
        "sorting": "desc",
        "start_timestamp": start_ts_ms,
    }
    async with aiohttp.ClientSession() as session:
        url = f"{rest_base}/public/get_last_trades_by_instrument_and_time"
        async with session.get(url, params=params) as resp:
            resp.raise_for_status()
            data: dict[str, Any] = await resp.json()
    return data


async def _live_fetch_funding(  # pragma: no cover
    instrument: str,
    start_ts_ms: int,
    end_ts_ms: int,
    *,
    rest_base: str = "https://www.deribit.com/api/v2",
) -> dict[str, Any]:
    """Fetch one funding history page from the Deribit REST API."""
    import aiohttp

    params: dict[str, Any] = {
        "instrument_name": instrument,
        "start_timestamp": start_ts_ms,
        "end_timestamp": end_ts_ms,
    }
    async with aiohttp.ClientSession() as session:
        url = f"{rest_base}/public/get_funding_rate_history"
        async with session.get(url, params=params) as resp:
            resp.raise_for_status()
            data: dict[str, Any] = await resp.json()
    return data


def make_live_backfill(  # pragma: no cover
    rest_base: str = "https://www.deribit.com/api/v2",
) -> DeribitBackfill:
    """Create a ``DeribitBackfill`` wired to live Deribit REST endpoints."""
    import functools

    return DeribitBackfill(
        fetch_trades=functools.partial(_live_fetch_trades, rest_base=rest_base),
        fetch_funding=functools.partial(_live_fetch_funding, rest_base=rest_base),
    )
