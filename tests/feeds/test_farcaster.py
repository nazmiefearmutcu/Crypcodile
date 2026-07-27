from unittest.mock import AsyncMock, patch

import pytest

from crocodile.core.schema.enums import AssetClass
from crocodile.core.schema.records import FarcasterCorrelation
from crocodile.core.store.parquet_sink import FAMILY_CANONICAL, _channel_schema
from crocodile.core.store.rows import from_row, to_row
from crocodile.crypto.feeds.farcaster import FarcasterSocialClient


@pytest.mark.asyncio
async def test_farcaster_social_client_offline():
    client = FarcasterSocialClient(api_key=None)
    
    # Check default mock resolution for DEGEN
    degen = await client.get_token_correlation("DEGEN")
    assert isinstance(degen, FarcasterCorrelation)
    assert degen.symbol == "farcaster:DEGEN"
    assert degen.symbol_raw == "DEGEN"
    assert degen.mentions_24h == 1250
    assert degen.dev_activity_score == 8.5
    assert degen.trending_rank == 1

    # Check trending list
    trending = await client.get_trending_tokens()
    assert len(trending) == 3
    symbols = [t.symbol_raw for t in trending]
    assert "DEGEN" in symbols
    assert "BRETT" in symbols
    assert "AERO" in symbols

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
