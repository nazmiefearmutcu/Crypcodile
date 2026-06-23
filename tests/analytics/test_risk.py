from __future__ import annotations

from unittest.mock import MagicMock

import polars as pl

from crypcodile.analytics.risk import calculate_chaos_score, calculate_dynamic_chaos_score
from crypcodile.store.catalog import Catalog


def test_calculate_chaos_score_bounds() -> None:
    # Minimal inputs -> score should be 0.0
    score_min = calculate_chaos_score(0.0, 0.0, 0.0, 0.0)
    assert score_min == 0.0

    # Extremely high risk -> score should approach 100.0 (but not exceed it)
    score_max = calculate_chaos_score(1000.0, 1000.0, 1.0, 1000.0)
    assert 0.0 <= score_max <= 100.0
    # Since each metric approaches 1.0, the average approaches 1.0 * 100 = 100.0
    assert score_max > 90.0


def test_calculate_chaos_score_sensitivities() -> None:
    # Test increase in volatility increases the score
    s1 = calculate_chaos_score(0.01, 0.0, 0.0, 0.0)
    s2 = calculate_chaos_score(0.1, 0.0, 0.0, 0.0)
    assert s2 > s1

    # Test increase in stablecoin deviation increases the score
    s3 = calculate_chaos_score(0.0, 0.005, 0.0, 0.0)
    s4 = calculate_chaos_score(0.0, 0.02, 0.0, 0.0)
    assert s4 > s3

    # Test negative inputs are handled gracefully
    s5 = calculate_chaos_score(-0.1, -0.01, -0.5, -5.0)
    s6 = calculate_chaos_score(0.1, 0.01, 0.5, 5.0)
    assert abs(s5 - s6) < 1e-9


def test_calculate_dynamic_chaos_score() -> None:
    # 1. Test when parameters are passed directly (it should bypass Catalog entirely)
    score = calculate_dynamic_chaos_score(
        volatility=0.05,
        stablecoin_deviation=0.002,
        orderbook_imbalance=0.1,
        sequencer_delay=1.0,
    )
    assert 0.0 <= score <= 100.0

    # 2. Test when Catalog is provided and queries are executed
    mock_catalog = MagicMock(spec=Catalog)

    def mock_query(sql: str) -> pl.DataFrame:
        sql_lower = sql.lower()
        if "symbol" in sql_lower and "limit 1" in sql_lower:
            return pl.DataFrame({"symbol": ["base_onchain:USDC-USDbC"]})
        if "max(local_ts)" in sql_lower:
            return pl.DataFrame({"max_ts": [1700000000000000000]})
        if "book_ticker" in sql_lower and "bid_px" in sql_lower:
            return pl.DataFrame({"bid_px": [0.995], "ask_px": [1.005]})
        if "book_snapshot" in sql_lower:
            return pl.DataFrame({"bids": [[[0.99, 100.0]]], "asks": [[[1.01, 100.0]]]})
        if "sequencer_delay" in sql_lower or "exchange_ts" in sql_lower:
            return pl.DataFrame(
                {"local_ts": [1700000000000000000], "exchange_ts": [1699999999000000000]}
            )
        return pl.DataFrame()

    mock_catalog.query.side_effect = mock_query

    # mock scan for volatility (needs at least 2 ticks)
    mock_scan_df = pl.DataFrame({
        "local_ts": [1700000000000000000 - 1000, 1700000000000000000],
        "bid_px": [1.0, 1.05],
        "ask_px": [1.0, 1.05],
    })
    mock_catalog.scan.return_value = mock_scan_df

    score_dynamic = calculate_dynamic_chaos_score(
        catalog=mock_catalog,
        symbol="base_onchain:USDC-USDbC"
    )
    assert 0.0 <= score_dynamic <= 100.0
