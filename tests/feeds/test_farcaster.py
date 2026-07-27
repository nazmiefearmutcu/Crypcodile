from unittest.mock import AsyncMock, patch

import pytest

from crocodile.core.schema.enums import AssetClass
from crocodile.core.schema.provenance import Provenance
from crocodile.core.schema.records import FarcasterCorrelation
from crocodile.core.store.parquet_sink import FAMILY_CANONICAL, _channel_schema
from crocodile.core.store.rows import from_row, to_row
from crocodile.crypto.feeds.farcaster import FarcasterSocialClient


@pytest.mark.asyncio
async def test_a_client_that_cannot_call_neynar_returns_no_record():
    """It used to return a table of numbers typed into the source file.

    DEGEN at 1250 mentions, rank 1, at `prov=native, prov_confidence=1.0`. The same path
    was taken on any non-200 and inside a bare `except`, so a timeout during live
    collection substituted literals for measurements mid-run and
    `SELECT mentions_24h ... WHERE symbol='farcaster:DEGEN'` returned 1250 on every row
    ever written.
    """
    client = FarcasterSocialClient(api_key=None)

    assert await client.get_token_correlation("DEGEN") is None
    assert await client.get_trending_tokens() == []


@pytest.mark.asyncio
async def test_metrics_modelled_from_a_page_of_casts_do_not_claim_to_be_reported():
    """Neynar returns casts; none of the record's three required fields is published.

    The count is a page length, the score is a substring test over author bios and the
    rank is arithmetic on the count — so the row carries the basis that says so, and the
    REST and MCP surfaces are required to warn on it.
    """
    payload = {
        "casts": [
            {"author": {"profile": {"bio": {"text": "solidity developer"}}}},
            {"author": {"profile": {"bio": {"text": "just here for the memes"}}}},
        ]
    }
    client = FarcasterSocialClient(api_key="a-key")

    with patch("aiohttp.ClientSession.get") as mock_get:
        response = AsyncMock()
        response.status = 200
        response.json = AsyncMock(return_value=payload)
        mock_get.return_value.__aenter__ = AsyncMock(return_value=response)
        mock_get.return_value.__aexit__ = AsyncMock(return_value=None)
        record = await client.get_token_correlation("DEGEN")

    assert isinstance(record, FarcasterCorrelation)
    assert record.prov is Provenance.SYNTHETIC
    assert record.prov_basis == "farcaster_cast_search"
    assert record.prov_confidence == 0.0
    # Counted, not scaled. `mentions * 24` under the comment "scale mock velocity" made
    # `max(mentions_24h)` return the page size times a constant for every saturating token.
    assert record.mentions_24h == 2
    assert record.dev_activity_score == 5.0

def test_farcaster_correlation_row_conversions():
    record = FarcasterCorrelation(
        source="farcaster",
        symbol="farcaster:DEGEN",
        symbol_raw="DEGEN",
        source_ts=1700000000000000000,
        local_ts=1700000000500000000,
        asset_class=AssetClass.CRYPTO,
        mentions_24h=1200,
        dev_activity_score=7.8,
        trending_rank=2
    )

    row = to_row(record)
    assert row["channel"] == "farcaster_correlation"
    assert row["mentions_24h"] == 1200
    assert row["dev_activity_score"] == 7.8
    assert row["trending_rank"] == 2

    reconstructed = from_row(row)
    assert isinstance(reconstructed, FarcasterCorrelation)
    assert reconstructed.mentions_24h == 1200
    assert reconstructed.dev_activity_score == 7.8
    assert reconstructed.trending_rank == 2

def test_farcaster_parquet_schema():
    # The farcaster feed emits canonical records, so the canonical table is the
    # one describing its file. The default is the crypto table, which nothing
    # writes any more.
    schema = _channel_schema("farcaster_correlation", FAMILY_CANONICAL)
    assert "mentions_24h" in schema
    assert "dev_activity_score" in schema
    assert "trending_rank" in schema
