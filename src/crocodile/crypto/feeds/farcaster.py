import logging
import time

import aiohttp

from crocodile.core.schema.enums import AssetClass
from crocodile.core.schema.provenance import provenance_fields
from crocodile.core.schema.records import FarcasterCorrelation

log = logging.getLogger(__name__)

CAST_SEARCH = "farcaster_cast_search"
"""The registered basis for social metrics modelled out of a page of casts.

None of ``FarcasterCorrelation``'s three required fields is published by the endpoint
this client calls: the mention count is a page length, the developer score is a
substring test over author bios, and the rank is arithmetic on the count. Left silent
they all shipped the header default — ``prov=native, prov_confidence=1.0``, the claim
that Farcaster reported a trending rank directly.
"""

_SEARCH_LIMIT = 100
"""Casts per search page. Not a measurement — it is the request the client makes."""


class FarcasterSocialClient:
    """Client for querying Farcaster social metrics via the Neynar API."""

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key
        self.base_url = "https://api.neynar.com/v2"

    async def get_token_correlation(self, symbol: str) -> FarcasterCorrelation | None:
        """Fetch social metrics for ``symbol``, or ``None`` when Neynar answers nothing.

        It used to fall back to a table of numbers typed into this file — DEGEN at 1250
        mentions, rank 1 — returned as a record at ``prov=native``. That path was taken
        with no API key, on any non-200, and inside a bare ``except``, so a timeout during
        live collection substituted literals for measurements mid-run:
        ``SELECT mentions_24h ... WHERE symbol='farcaster:DEGEN'`` returned 1250 on every
        row ever written, and an unknown symbol returned the padded 50. There is no record
        to build when the call fails, so none is built.
        """
        symbol_upper = symbol.upper()
        if not self.api_key:
            log.debug("farcaster: client is unauthenticated; no metrics for %s", symbol_upper)
            return None

        headers = {"api_key": self.api_key}
        url = f"{self.base_url}/farcaster/casts/search"
        params: dict[str, object] = {"q": symbol_upper, "limit": _SEARCH_LIMIT}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, params=params, timeout=5) as resp:
                    if resp.status != 200:
                        log.warning("farcaster: Neynar answered %s for %s", resp.status,
                                    symbol_upper)
                        return None
                    data = await resp.json()
        except Exception as e:
            log.warning("farcaster: Neynar query for %s failed: %s", symbol_upper, e)
            return None

        casts = data.get("casts", [])
        mentions = len(casts)

        # Developer share of the page, by substring over author bios. A heuristic, which
        # is what the SYNTHETIC level and the basis's 0.0 confidence say out loud.
        dev_casts = 0
        for cast in casts:
            author = cast.get("author", {})
            bio = (author.get("profile", {}).get("bio", {}).get("text", "") or "").lower()
            if any(word in bio for word in ("developer", "dev", "builder", "engineer", "coder")):
                dev_casts += 1

        dev_score = float(dev_casts) / max(1, mentions) * 10.0
        trending_rank = 100 - min(99, mentions)

        # The count is reported as counted. It used to be multiplied by 24 under the
        # comment "scale mock velocity", so `SELECT max(mentions_24h)` returned exactly
        # 2400 for every token that filled the page — the page size times a constant,
        # presented as a measured 24-hour volume. The search carries no time filter, so
        # the field name still promises a window the query does not ask for; that is a
        # schema question and the basis's docstring records it.
        tail = provenance_fields(CAST_SEARCH)
        now_ns = int(time.time() * 1_000_000_000)
        return FarcasterCorrelation(
            source="farcaster",
            symbol=f"farcaster:{symbol_upper}",
            symbol_raw=symbol_upper,
            source_ts=None,
            local_ts=now_ns,
            asset_class=AssetClass.CRYPTO,
            mentions_24h=mentions,
            dev_activity_score=round(dev_score, 2),
            trending_rank=trending_rank,
            prov=tail.prov,
            prov_basis=tail.prov_basis,
            prov_confidence=tail.prov_confidence,
            prov_inputs=tail.prov_inputs,
        )

    async def get_trending_tokens(self) -> list[FarcasterCorrelation]:
        """Social metrics for the tokens this feed follows, skipping the ones with none."""
        symbols = ["DEGEN", "BRETT", "AERO"]
        found = [await self.get_token_correlation(sym) for sym in symbols]
        return [record for record in found if record is not None]
