"""Async CoinGecko fetch helpers (keyless public API).

Kept separate from the connector so the ``census`` command can reuse them
without constructing a full :class:`~crypcodile.exchanges.base.Connector`.

Public API base: ``https://api.coingecko.com/api/v3``.  No key required; the
free tier is rate-limited (~10-30 req/min), so callers should page politely.
An optional ``COINGECKO_API_KEY`` env var, when set, is sent as the
``x-cg-demo-api-key`` header to lift the limit.
"""

from __future__ import annotations

import os
from typing import Any

import aiohttp

_BASE = "https://api.coingecko.com/api/v3"


def _headers() -> dict[str, str]:
    key = os.environ.get("COINGECKO_API_KEY", "").strip()
    return {"x-cg-demo-api-key": key} if key else {}


async def _get(
    session: aiohttp.ClientSession, path: str, params: dict[str, Any] | None = None
) -> Any:
    timeout = aiohttp.ClientTimeout(total=20.0)
    async with session.get(
        f"{_BASE}{path}", params=params, headers=_headers(), timeout=timeout
    ) as resp:
        resp.raise_for_status()
        return await resp.json()


async def fetch_global(session: aiohttp.ClientSession) -> dict[str, Any]:
    """Return CoinGecko's global market snapshot (``data`` object).

    Keys include ``active_cryptocurrencies``, ``markets``, ``total_market_cap``,
    ``total_volume`` and ``market_cap_percentage`` (dominance).
    """
    payload = await _get(session, "/global")
    data = payload.get("data") if isinstance(payload, dict) else None
    return data or {}


async def fetch_markets(
    session: aiohttp.ClientSession,
    *,
    vs_currency: str = "usd",
    pages: int = 1,
    per_page: int = 250,
) -> list[dict[str, Any]]:
    """Fetch the top ``pages * per_page`` coins by market cap.

    Each row is a full ccxt-independent coin market: ``id`` (unique),
    ``symbol``, ``current_price``, ``high_24h`` / ``low_24h``,
    ``total_volume``, ``market_cap``, ``market_cap_rank``,
    ``price_change_percentage_24h``.  ``per_page`` is capped at CoinGecko's max
    of 250.
    """
    per_page = max(1, min(per_page, 250))
    out: list[dict[str, Any]] = []
    for page in range(1, max(1, pages) + 1):
        rows = await _get(
            session,
            "/coins/markets",
            params={
                "vs_currency": vs_currency,
                "order": "market_cap_desc",
                "per_page": str(per_page),
                "page": str(page),
                "price_change_percentage": "24h",
            },
        )
        if not rows:
            break
        out.extend(rows)
        if len(rows) < per_page:
            break  # last page
    return out
