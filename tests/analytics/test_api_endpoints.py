from __future__ import annotations

import asyncio
import inspect
import math
from typing import Any
from unittest.mock import MagicMock, patch
from fastapi import HTTPException
from pydantic import ValidationError
import polars as pl
import pytest

from crypcodile.api_server import app


class MockTestClient:
    """A mock TestClient that executes FastAPI routes directly.

    This bypasses the need for starlette's testclient which requires
    httpx/httpx2.
    """

    def __init__(self, fastapi_app: Any) -> None:
        self.app = fastapi_app

    def post(self, url: str, json: dict[str, Any] | None = None) -> Any:
        for route in self.app.routes:
            # Match the path
            if route.path == url and "POST" in route.methods:
                sig = inspect.signature(route.endpoint)
                payload_param = list(sig.parameters.values())[0]
                annotation = payload_param.annotation

                # Resolve the payload class if it is a string annotation (due to future annotations import)
                if isinstance(annotation, str):
                    import sys
                    module = sys.modules[route.endpoint.__module__]
                    payload_class = getattr(module, annotation)
                else:
                    payload_class = annotation

                # Validate using Pydantic model
                try:
                    payload = payload_class(**(json or {}))
                    # Execute endpoint
                    coro = route.endpoint(payload)
                    res = asyncio.run(coro)

                    mock_resp = MagicMock()
                    mock_resp.status_code = 200
                    mock_resp.json.return_value = res
                    return mock_resp
                except ValidationError as e:
                    mock_resp = MagicMock()
                    mock_resp.status_code = 422
                    mock_resp.json.return_value = {"detail": str(e)}
                    return mock_resp
                except HTTPException as e:
                    mock_resp = MagicMock()
                    mock_resp.status_code = e.status_code
                    mock_resp.json.return_value = {"detail": e.detail}
                    return mock_resp
                except Exception as e:
                    mock_resp = MagicMock()
                    mock_resp.status_code = 400
                    mock_resp.json.return_value = {"detail": str(e)}
                    return mock_resp

        raise ValueError(f"Route {url} not found")


client = MockTestClient(app)


def test_simulate_price_impact_route_success() -> None:
    mock_df = pl.DataFrame({
        "symbol": ["binance:BTC-USDT"],
        "side": ["buy"],
        "size": [1.5],
        "best_price": [60000.0],
        "expected_price": [60100.0],
        "slippage_usd": [100.0],
        "slippage_pct": [0.1666],
    })

    with patch(
        "crypcodile.analytics.slippage.estimate_slippage",
        return_value=mock_df,
    ):
        resp = client.post(
            "/api/v1/simulate-price-impact",
            json={
                "symbol": "binance:BTC-USDT",
                "side": "buy",
                "amount": 1.5,
            },
        )
        assert resp.status_code == 200
        data_list = resp.json()
        assert isinstance(data_list, list)
        data = data_list[0]
        assert data["symbol"] == "binance:BTC-USDT"
        assert data["side"] == "buy"
        assert data["size"] == 1.5
        assert data["best_price"] == 60000.0
        assert data["expected_price"] == 60100.0
        assert data["slippage_usd"] == 100.0
        assert abs(data["slippage_pct"] - 0.1666) < 1e-4

        # Test with size instead of amount
        resp_size = client.post(
            "/api/v1/simulate-price-impact",
            json={
                "symbol": "binance:BTC-USDT",
                "side": "buy",
                "size": 1.5,
            },
        )
        assert resp_size.status_code == 200
        data_size = resp_size.json()[0]
        assert data_size["symbol"] == "binance:BTC-USDT"



def test_simulate_price_impact_invalid_side() -> None:
    resp = client.post(
        "/api/v1/simulate-price-impact",
        json={
            "symbol": "binance:BTC-USDT",
            "side": "hold",
            "amount": 1.5,
        },
    )
    assert resp.status_code == 400
    assert "Side must be 'buy' or 'sell'" in resp.json()["detail"]


def test_simulate_price_impact_invalid_amount() -> None:
    resp = client.post(
        "/api/v1/simulate-price-impact",
        json={
            "symbol": "binance:BTC-USDT",
            "side": "buy",
            "amount": -0.5,
        },
    )
    assert resp.status_code == 400
    assert "Amount must be greater than zero" in resp.json()["detail"]


def test_simulate_price_impact_estimation_error() -> None:
    with patch(
        "crypcodile.analytics.slippage.estimate_slippage",
        side_effect=ValueError("No book snapshots found"),
    ):
        resp = client.post(
            "/api/v1/simulate-price-impact",
            json={
                "symbol": "binance:BTC-USDT",
                "side": "buy",
                "amount": 1.5,
            },
        )
        assert resp.status_code == 400
        assert "No book snapshots found" in resp.json()["detail"]


def test_simulate_price_impact_non_finite_floats_json_safe_null() -> None:
    """Slippage rows may contain nan/inf; REST maps them to JSON null."""
    mock_df = pl.DataFrame(
        {
            "symbol": ["binance:BTC-USDT"],
            "side": ["buy"],
            "size": [1.5],
            "best_price": [60000.0],
            "expected_price": [float("inf")],
            "slippage_usd": [float("nan")],
            "slippage_pct": [float("-inf")],
        }
    )
    with patch(
        "crypcodile.analytics.slippage.estimate_slippage",
        return_value=mock_df,
    ):
        resp = client.post(
            "/api/v1/simulate-price-impact",
            json={
                "symbol": "binance:BTC-USDT",
                "side": "buy",
                "amount": 1.5,
            },
        )
    assert resp.status_code == 200
    data = resp.json()[0]
    assert data["symbol"] == "binance:BTC-USDT"
    assert data["size"] == 1.5
    assert data["best_price"] == 60000.0
    assert data["expected_price"] is None
    assert data["slippage_usd"] is None
    assert data["slippage_pct"] is None


# ---------------------------------------------------------------------------
# Lake catalog discovery (read-only, no payment)
# ---------------------------------------------------------------------------


def test_catalog_list_channels_empty_lake(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CRYPCODILE_DATA_DIR", str(tmp_path))
    from crypcodile.api_server import catalog_list_channels

    result = asyncio.run(catalog_list_channels())
    assert result == []


def test_catalog_list_channels_returns_channels() -> None:
    mock_client = MagicMock()
    mock_client.list_channels.return_value = ["book_snapshot", "trade"]
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import catalog_list_channels

        result = asyncio.run(catalog_list_channels())
    assert result == ["book_snapshot", "trade"]
    mock_client.list_channels.assert_called_once_with()


def test_catalog_list_dates_empty_channel() -> None:
    from crypcodile.api_server import catalog_list_dates

    assert asyncio.run(catalog_list_dates()) == []
    assert asyncio.run(catalog_list_dates(channel="")) == []
    assert asyncio.run(catalog_list_dates(channel="   ")) == []


def test_catalog_list_dates_empty_lake(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CRYPCODILE_DATA_DIR", str(tmp_path))
    from crypcodile.api_server import catalog_list_dates

    result = asyncio.run(catalog_list_dates(channel="trade"))
    assert result == []


def test_catalog_list_dates_returns_dates() -> None:
    mock_client = MagicMock()
    mock_client.list_dates.return_value = ["2023-11-14", "2023-11-15"]
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import catalog_list_dates

        result = asyncio.run(catalog_list_dates(channel="trade"))
    assert result == ["2023-11-14", "2023-11-15"]
    mock_client.list_dates.assert_called_once_with("trade")


def test_catalog_list_dates_strips_channel() -> None:
    mock_client = MagicMock()
    mock_client.list_dates.return_value = ["2024-01-01"]
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import catalog_list_dates

        result = asyncio.run(catalog_list_dates(channel="  book_snapshot  "))
    assert result == ["2024-01-01"]
    mock_client.list_dates.assert_called_once_with("book_snapshot")


def test_catalog_search_symbols_empty() -> None:
    mock_client = MagicMock()
    mock_client.search_symbols.return_value = pl.DataFrame(
        schema={
            "symbol": pl.Utf8,
            "exchange": pl.Utf8,
            "channels": pl.Utf8,
            "score": pl.Float64,
            "min_ts": pl.Int64,
            "max_ts": pl.Int64,
            "row_count": pl.Int64,
        }
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import catalog_search_symbols

        result = asyncio.run(catalog_search_symbols(q="BTC", limit=20))
    assert result == []
    mock_client.search_symbols.assert_called_once_with("BTC", limit=20)


def test_catalog_search_symbols_returns_rows() -> None:
    mock_client = MagicMock()
    mock_client.search_symbols.return_value = pl.DataFrame(
        {
            "symbol": ["deribit:BTC-PERPETUAL"],
            "exchange": ["deribit"],
            "channels": ["trade"],
            "score": [1.0],
            "min_ts": [1],
            "max_ts": [2],
            "row_count": [10],
        }
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import catalog_search_symbols

        result = asyncio.run(catalog_search_symbols(q="BTC", limit=5))
    assert len(result) == 1
    assert result[0]["symbol"] == "deribit:BTC-PERPETUAL"
    assert result[0]["exchange"] == "deribit"
    mock_client.search_symbols.assert_called_once_with("BTC", limit=5)


def test_catalog_search_clamps_limit_minimum() -> None:
    mock_client = MagicMock()
    mock_client.search_symbols.return_value = pl.DataFrame(
        schema={
            "symbol": pl.Utf8,
            "exchange": pl.Utf8,
            "channels": pl.Utf8,
            "score": pl.Float64,
            "min_ts": pl.Int64,
            "max_ts": pl.Int64,
            "row_count": pl.Int64,
        }
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import catalog_search_symbols

        asyncio.run(catalog_search_symbols(q="x", limit=0))
    mock_client.search_symbols.assert_called_once_with("x", limit=1)


def test_catalog_inventory_empty_lake(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CRYPCODILE_DATA_DIR", str(tmp_path))
    from crypcodile.api_server import catalog_inventory

    result = asyncio.run(catalog_inventory())
    assert result == []


def test_catalog_inventory_empty_dataframe() -> None:
    mock_client = MagicMock()
    mock_client.inventory.return_value = pl.DataFrame(
        schema={
            "exchange": pl.Utf8,
            "channel": pl.Utf8,
            "symbol": pl.Utf8,
            "min_ts": pl.Int64,
            "max_ts": pl.Int64,
            "row_count": pl.Int64,
        }
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import catalog_inventory

        result = asyncio.run(catalog_inventory())
    assert result == []
    mock_client.inventory.assert_called_once_with(channel=None, exchange=None)


def test_catalog_inventory_returns_rows() -> None:
    mock_client = MagicMock()
    mock_client.inventory.return_value = pl.DataFrame(
        {
            "exchange": ["deribit"],
            "channel": ["trade"],
            "symbol": ["deribit:BTC-PERPETUAL"],
            "min_ts": [1],
            "max_ts": [2],
            "row_count": [10],
        }
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import catalog_inventory

        result = asyncio.run(
            catalog_inventory(channel="trade", exchange="deribit")
        )
    assert len(result) == 1
    assert result[0]["symbol"] == "deribit:BTC-PERPETUAL"
    assert result[0]["exchange"] == "deribit"
    assert result[0]["channel"] == "trade"
    assert result[0]["row_count"] == 10
    mock_client.inventory.assert_called_once_with(
        channel="trade", exchange="deribit"
    )


def test_catalog_inventory_strips_empty_filters() -> None:
    mock_client = MagicMock()
    mock_client.inventory.return_value = pl.DataFrame(
        schema={
            "exchange": pl.Utf8,
            "channel": pl.Utf8,
            "symbol": pl.Utf8,
            "min_ts": pl.Int64,
            "max_ts": pl.Int64,
            "row_count": pl.Int64,
        }
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import catalog_inventory

        asyncio.run(catalog_inventory(channel="  ", exchange=""))
    mock_client.inventory.assert_called_once_with(channel=None, exchange=None)


def test_get_lake_client_uses_env(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CRYPCODILE_DATA_DIR", str(tmp_path))
    from crypcodile.api_server import _get_lake_client

    client = _get_lake_client()
    assert client._catalog._data_dir == tmp_path
    assert client.list_channels() == []


def test_catalog_scan_empty_params() -> None:
    from crypcodile.api_server import catalog_scan

    assert asyncio.run(catalog_scan()) == []
    assert asyncio.run(catalog_scan(channel="", symbol="x")) == []
    assert asyncio.run(catalog_scan(channel="trade", symbol="")) == []
    assert asyncio.run(catalog_scan(channel="  ", symbol="  ")) == []


def test_catalog_scan_empty_lake(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CRYPCODILE_DATA_DIR", str(tmp_path))
    from crypcodile.api_server import catalog_scan

    result = asyncio.run(
        catalog_scan(
            channel="trade",
            symbol="deribit:BTC-PERPETUAL",
            start=0,
            end=10**18,
        )
    )
    assert result == []


def test_catalog_scan_returns_rows() -> None:
    mock_client = MagicMock()
    mock_client.scan.return_value = pl.DataFrame(
        {
            "local_ts": [100, 200],
            "symbol": ["deribit:BTC-PERPETUAL", "deribit:BTC-PERPETUAL"],
            "price": [1.0, 2.0],
        }
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import catalog_scan

        result = asyncio.run(
            catalog_scan(
                channel="trade",
                symbol="deribit:BTC-PERPETUAL",
                start=0,
                end=1000,
                limit=100,
            )
        )
    assert len(result) == 2
    assert result[0]["price"] == 1.0
    assert result[1]["local_ts"] == 200
    mock_client.scan.assert_called_once_with(
        "trade", ["deribit:BTC-PERPETUAL"], 0, 1000
    )


def test_catalog_scan_applies_limit() -> None:
    mock_client = MagicMock()
    mock_client.scan.return_value = pl.DataFrame(
        {
            "local_ts": [1, 2, 3, 4, 5],
            "price": [10.0, 20.0, 30.0, 40.0, 50.0],
        }
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import catalog_scan

        result = asyncio.run(
            catalog_scan(
                channel="trade",
                symbol="binance-spot:BTC-USDT",
                start=0,
                end=99,
                limit=2,
            )
        )
    assert len(result) == 2
    assert result[0]["local_ts"] == 1
    assert result[1]["local_ts"] == 2


def test_catalog_scan_clamps_limit_max() -> None:
    mock_client = MagicMock()
    mock_client.scan.return_value = pl.DataFrame(
        {"local_ts": list(range(20)), "price": [1.0] * 20}
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import _CATALOG_SCAN_MAX_LIMIT, catalog_scan

        result = asyncio.run(
            catalog_scan(
                channel="trade",
                symbol="x:Y",
                start=0,
                end=1,
                limit=_CATALOG_SCAN_MAX_LIMIT + 5000,
            )
        )
    # Mock returns only 20 rows; clamp must not raise and scan is still called.
    assert len(result) == 20
    mock_client.scan.assert_called_once()


def test_catalog_scan_clamps_limit_minimum() -> None:
    mock_client = MagicMock()
    mock_client.scan.return_value = pl.DataFrame(
        {"local_ts": [1, 2, 3], "price": [1.0, 2.0, 3.0]}
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import catalog_scan

        result = asyncio.run(
            catalog_scan(
                channel="trade",
                symbol="x:Y",
                start=0,
                end=1,
                limit=0,
            )
        )
    assert len(result) == 1
    assert result[0]["local_ts"] == 1


def test_catalog_scan_empty_dataframe_from_client() -> None:
    mock_client = MagicMock()
    mock_client.scan.return_value = pl.DataFrame()
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import catalog_scan

        result = asyncio.run(
            catalog_scan(channel="trade", symbol="x:Y", start=0, end=1)
        )
    assert result == []


# ---------------------------------------------------------------------------
# POST /api/v1/query — bounded read-only SQL
# ---------------------------------------------------------------------------


def test_is_mutating_sql_detects_keywords() -> None:
    from crypcodile.api_server import _MUTATING_SQL_KEYWORDS, _is_mutating_sql

    for kw in _MUTATING_SQL_KEYWORDS:
        assert _is_mutating_sql(f"{kw} INTO trade VALUES (1)") is True
        assert _is_mutating_sql(f"  {kw.lower()} table trade;") is True
        assert _is_mutating_sql(f"select 1; {kw} x") is True

    assert _is_mutating_sql("SELECT * FROM trade") is False
    assert _is_mutating_sql("SELECT count(*) AS n FROM book_snapshot") is False
    # Word-boundary: substring alone is not enough
    assert _is_mutating_sql("SELECT deleted_at FROM trade") is False
    assert _is_mutating_sql("SELECT installed_at FROM trade") is False


def test_is_single_select_and_wrap() -> None:
    from crypcodile.api_server import _is_single_select, _wrap_select_limit

    assert _is_single_select("SELECT * FROM trade") is True
    assert _is_single_select("  select 1 as x;  ") is True
    assert _is_single_select("WITH cte AS (SELECT 1 AS x) SELECT * FROM cte") is True
    assert _is_single_select("SELECT 1; SELECT 2") is False
    assert _is_single_select("EXPLAIN SELECT 1") is False
    assert _is_single_select("DESCRIBE trade") is False
    assert _is_single_select("") is False

    wrapped = _wrap_select_limit("SELECT * FROM trade", 5)
    assert wrapped == "SELECT * FROM (SELECT * FROM trade) AS _q LIMIT 5"
    assert _wrap_select_limit("SELECT 1; SELECT 2", 5) is None
    assert _wrap_select_limit("EXPLAIN SELECT 1", 5) is None


def test_query_lake_returns_rows() -> None:
    mock_client = MagicMock()
    mock_client.query.return_value = pl.DataFrame(
        {
            "symbol": ["deribit:BTC-PERPETUAL"],
            "n": [42],
        }
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import QueryPayload, _QUERY_MAX_LIMIT, query_lake

        result = asyncio.run(
            query_lake(QueryPayload(sql="SELECT symbol, count(*) AS n FROM trade"))
        )
    assert len(result) == 1
    assert result[0]["symbol"] == "deribit:BTC-PERPETUAL"
    assert result[0]["n"] == 42
    mock_client.query.assert_called_once_with(
        "SELECT * FROM (SELECT symbol, count(*) AS n FROM trade) AS _q "
        f"LIMIT {_QUERY_MAX_LIMIT}"
    )


def test_query_lake_empty_result() -> None:
    mock_client = MagicMock()
    mock_client.query.return_value = pl.DataFrame()
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import QueryPayload, query_lake

        result = asyncio.run(query_lake(QueryPayload(sql="SELECT 1 WHERE false")))
    assert result == []


def test_query_lake_empty_lake(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CRYPCODILE_DATA_DIR", str(tmp_path))
    from crypcodile.api_server import QueryPayload, query_lake

    # Empty lake: unknown relation should surface as 400 SQL failure, or
    # a trivial select that returns empty after a real client may raise.
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(query_lake(QueryPayload(sql="SELECT * FROM trade")))
    assert exc_info.value.status_code == 400
    # Do not leak engine exception text to the client.
    assert exc_info.value.detail == "SQL execution failed."


def test_query_lake_rejects_mutating_sql() -> None:
    from crypcodile.api_server import QueryPayload, query_lake

    for sql in (
        "INSERT INTO trade VALUES (1)",
        "update trade set price = 0",
        "Delete FROM trade",
        "DROP TABLE trade",
        "CREATE TABLE x (a INT)",
        "ALTER TABLE trade ADD COLUMN x INT",
        "ATTACH 'other.db' AS other",
        "COPY trade TO 'out.parquet'",
        "TRUNCATE trade",
        "DETACH other",
        "PRAGMA show_tables",
        "INSTALL httpfs",
        "LOAD httpfs",
        "EXPORT DATABASE 'out'",
        "CALL my_macro()",
        "SET threads=1",
        "REPLACE INTO trade VALUES (1)",
        "MERGE INTO trade USING s ON trade.id = s.id WHEN MATCHED THEN UPDATE SET price = s.price",
        "VACUUM",
    ):
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(query_lake(QueryPayload(sql=sql)))
        assert exc_info.value.status_code == 400
        assert "Mutating SQL" in str(exc_info.value.detail)


def test_query_lake_rejects_empty_sql() -> None:
    from crypcodile.api_server import QueryPayload, query_lake

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(query_lake(QueryPayload(sql="   ")))
    assert exc_info.value.status_code == 400
    assert "required" in str(exc_info.value.detail).lower()


def test_query_lake_applies_limit() -> None:
    mock_client = MagicMock()
    # Simulate SQL LIMIT already applied by the wrapped query.
    mock_client.query.return_value = pl.DataFrame(
        {
            "local_ts": [1, 2],
            "price": [10.0, 20.0],
        }
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import QueryPayload, query_lake

        result = asyncio.run(
            query_lake(QueryPayload(sql="SELECT * FROM trade", limit=2))
        )
    assert len(result) == 2
    assert result[0]["local_ts"] == 1
    assert result[1]["local_ts"] == 2
    mock_client.query.assert_called_once_with(
        "SELECT * FROM (SELECT * FROM trade) AS _q LIMIT 2"
    )


def test_query_lake_wrap_fallback_uses_head() -> None:
    """If wrapped SELECT fails, re-run original SQL and bound with head()."""
    mock_client = MagicMock()
    mock_client.query.side_effect = [
        RuntimeError("cannot wrap subquery"),
        pl.DataFrame(
            {
                "local_ts": [1, 2, 3, 4, 5],
                "price": [10.0, 20.0, 30.0, 40.0, 50.0],
            }
        ),
    ]
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import QueryPayload, query_lake

        result = asyncio.run(
            query_lake(QueryPayload(sql="SELECT * FROM trade", limit=2))
        )
    assert len(result) == 2
    assert result[0]["local_ts"] == 1
    assert result[1]["local_ts"] == 2
    assert mock_client.query.call_count == 2
    assert mock_client.query.call_args_list[0].args[0] == (
        "SELECT * FROM (SELECT * FROM trade) AS _q LIMIT 2"
    )
    assert mock_client.query.call_args_list[1].args[0] == "SELECT * FROM trade"


def test_query_lake_non_select_uses_head_not_wrap() -> None:
    mock_client = MagicMock()
    mock_client.query.return_value = pl.DataFrame(
        {"local_ts": [1, 2, 3], "price": [1.0, 2.0, 3.0]}
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import QueryPayload, query_lake

        result = asyncio.run(
            query_lake(QueryPayload(sql="EXPLAIN SELECT * FROM trade", limit=2))
        )
    assert len(result) == 2
    mock_client.query.assert_called_once_with("EXPLAIN SELECT * FROM trade")


def test_query_lake_clamps_limit_max() -> None:
    mock_client = MagicMock()
    # Return more than the hard max so head() is exercised after clamp.
    n = 20
    mock_client.query.return_value = pl.DataFrame(
        {"local_ts": list(range(n)), "price": [1.0] * n}
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import QueryPayload, _QUERY_MAX_LIMIT, query_lake

        result = asyncio.run(
            query_lake(
                QueryPayload(
                    sql="SELECT * FROM trade",
                    limit=_QUERY_MAX_LIMIT + 5000,
                )
            )
        )
    # Mock only has 20 rows; clamp must not raise.
    assert len(result) == 20
    mock_client.query.assert_called_once_with(
        f"SELECT * FROM (SELECT * FROM trade) AS _q LIMIT {_QUERY_MAX_LIMIT}"
    )


def test_query_lake_clamps_limit_minimum() -> None:
    mock_client = MagicMock()
    mock_client.query.return_value = pl.DataFrame(
        {"local_ts": [1], "price": [1.0]}
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import QueryPayload, query_lake

        result = asyncio.run(
            query_lake(QueryPayload(sql="SELECT * FROM trade", limit=0))
        )
    assert len(result) == 1
    assert result[0]["local_ts"] == 1
    mock_client.query.assert_called_once_with(
        "SELECT * FROM (SELECT * FROM trade) AS _q LIMIT 1"
    )


def test_query_lake_default_limit_is_max() -> None:
    mock_client = MagicMock()
    mock_client.query.return_value = pl.DataFrame(
        {"local_ts": list(range(5)), "price": [1.0] * 5}
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import QueryPayload, _QUERY_MAX_LIMIT, query_lake

        result = asyncio.run(query_lake(QueryPayload(sql="SELECT * FROM trade")))
    assert len(result) == 5
    mock_client.query.assert_called_once_with(
        f"SELECT * FROM (SELECT * FROM trade) AS _q LIMIT {_QUERY_MAX_LIMIT}"
    )


def test_query_lake_sql_error() -> None:
    mock_client = MagicMock()
    mock_client.query.side_effect = RuntimeError("syntax error at or near X")
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import QueryPayload, query_lake

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(query_lake(QueryPayload(sql="SELEC bogus")))
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "SQL execution failed."
    # Sanitize: never return full exception detail to the client.
    assert "syntax error" not in str(exc_info.value.detail)
    assert "near X" not in str(exc_info.value.detail)


def test_query_lake_route_via_mock_client() -> None:
    mock_client = MagicMock()
    mock_client.query.return_value = pl.DataFrame({"x": [1]})
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        resp = client.post("/api/v1/query", json={"sql": "SELECT 1 AS x", "limit": 10})
    assert resp.status_code == 200
    assert resp.json() == [{"x": 1}]
    mock_client.query.assert_called_once_with(
        "SELECT * FROM (SELECT 1 AS x) AS _q LIMIT 10"
    )


def test_query_lake_route_rejects_mutating() -> None:
    resp = client.post(
        "/api/v1/query",
        json={"sql": "DROP TABLE trade"},
    )
    assert resp.status_code == 400
    assert "Mutating SQL" in resp.json()["detail"]


def test_query_lake_route_sanitizes_sql_error() -> None:
    mock_client = MagicMock()
    mock_client.query.side_effect = RuntimeError("internal path /secret/data leaked")
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        resp = client.post(
            "/api/v1/query",
            json={"sql": "SELECT * FROM missing_table"},
        )
    assert resp.status_code == 400
    body = resp.json()
    assert body["detail"] == "SQL execution failed."
    assert "secret" not in body["detail"]
    assert "/secret" not in str(body)


# ---------------------------------------------------------------------------
# GET /api/v1/open-interest — aggregate OI (read-only, no payment)
# ---------------------------------------------------------------------------


def test_open_interest_empty_lake(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CRYPCODILE_DATA_DIR", str(tmp_path))
    from crypcodile.api_server import open_interest

    result = asyncio.run(open_interest(symbols="BTC", start=0, end=10**18))
    assert result == []


def test_open_interest_empty_dataframe() -> None:
    mock_client = MagicMock()
    mock_client.aggregate_open_interest.return_value = pl.DataFrame()
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import open_interest

        result = asyncio.run(open_interest(symbols="BTC", start=0, end=100))
    assert result == []
    mock_client.aggregate_open_interest.assert_called_once_with("BTC", 0, 100)


def test_open_interest_returns_rows() -> None:
    mock_client = MagicMock()
    mock_client.aggregate_open_interest.return_value = pl.DataFrame(
        {
            "local_ts": [100, 200],
            "binance": [1000.0, 1100.0],
            "bybit": [500.0, 550.0],
            "total_oi": [1500.0, 1650.0],
        }
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import open_interest

        result = asyncio.run(
            open_interest(symbols="BTC", start=0, end=1000, limit=100)
        )
    assert len(result) == 2
    assert result[0]["local_ts"] == 100
    assert result[0]["binance"] == 1000.0
    assert result[0]["total_oi"] == 1500.0
    assert result[1]["local_ts"] == 200
    assert result[1]["total_oi"] == 1650.0
    mock_client.aggregate_open_interest.assert_called_once_with("BTC", 0, 1000)


def test_open_interest_all_symbols_when_empty_filter() -> None:
    mock_client = MagicMock()
    mock_client.aggregate_open_interest.return_value = pl.DataFrame(
        {"local_ts": [1], "total_oi": [42.0]}
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import open_interest

        result = asyncio.run(open_interest(symbols="", start=0, end=99))
    assert len(result) == 1
    assert result[0]["total_oi"] == 42.0
    mock_client.aggregate_open_interest.assert_called_once_with(None, 0, 99)


def test_open_interest_strips_whitespace_symbols() -> None:
    mock_client = MagicMock()
    mock_client.aggregate_open_interest.return_value = pl.DataFrame()
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import open_interest

        asyncio.run(open_interest(symbols="  ", start=1, end=2))
        asyncio.run(open_interest(symbols="  ETH  ", start=1, end=2))
    assert mock_client.aggregate_open_interest.call_args_list[0].args == (
        None,
        1,
        2,
    )
    assert mock_client.aggregate_open_interest.call_args_list[1].args == (
        "ETH",
        1,
        2,
    )


def test_open_interest_applies_limit() -> None:
    mock_client = MagicMock()
    mock_client.aggregate_open_interest.return_value = pl.DataFrame(
        {
            "local_ts": [1, 2, 3, 4, 5],
            "total_oi": [10.0, 20.0, 30.0, 40.0, 50.0],
        }
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import open_interest

        result = asyncio.run(
            open_interest(symbols="BTC", start=0, end=99, limit=2)
        )
    assert len(result) == 2
    assert result[0]["local_ts"] == 1
    assert result[1]["local_ts"] == 2


def test_open_interest_clamps_limit_max() -> None:
    mock_client = MagicMock()
    mock_client.aggregate_open_interest.return_value = pl.DataFrame(
        {"local_ts": list(range(20)), "total_oi": [1.0] * 20}
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import _OPEN_INTEREST_MAX_LIMIT, open_interest

        result = asyncio.run(
            open_interest(
                symbols="x",
                start=0,
                end=1,
                limit=_OPEN_INTEREST_MAX_LIMIT + 5000,
            )
        )
    assert len(result) == 20
    mock_client.aggregate_open_interest.assert_called_once()


def test_open_interest_clamps_limit_minimum() -> None:
    mock_client = MagicMock()
    mock_client.aggregate_open_interest.return_value = pl.DataFrame(
        {"local_ts": [1, 2, 3], "total_oi": [1.0, 2.0, 3.0]}
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import open_interest

        result = asyncio.run(
            open_interest(symbols="x", start=0, end=1, limit=0)
        )
    assert len(result) == 1
    assert result[0]["local_ts"] == 1


def test_open_interest_aggregation_error() -> None:
    mock_client = MagicMock()
    mock_client.aggregate_open_interest.side_effect = RuntimeError(
        "internal path /secret/lake"
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import open_interest

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(open_interest(symbols="BTC", start=0, end=1))
    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Open interest aggregation failed."
    assert "secret" not in str(exc_info.value.detail)


def test_open_interest_route_registered() -> None:
    """Ensure FastAPI route table includes GET /api/v1/open-interest."""
    paths = {
        (getattr(r, "path", None), tuple(sorted(getattr(r, "methods", set()) or [])))
        for r in app.routes
    }
    assert ("/api/v1/open-interest", ("GET",)) in paths


# ---------------------------------------------------------------------------
# GET /api/v1/funding-apr — per-event funding APR (read-only, no payment)
# ---------------------------------------------------------------------------


def test_funding_apr_empty_symbol_skips_client() -> None:
    mock_client = MagicMock()
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import funding_apr

        result = asyncio.run(funding_apr(symbol="", start=0, end=100))
        result_ws = asyncio.run(funding_apr(symbol="  ", start=0, end=100))
    assert result == []
    assert result_ws == []
    mock_client.funding_apr.assert_not_called()


def test_funding_apr_empty_lake(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CRYPCODILE_DATA_DIR", str(tmp_path))
    from crypcodile.api_server import funding_apr

    result = asyncio.run(
        funding_apr(symbol="deribit:BTC-PERPETUAL", start=0, end=10**18)
    )
    assert result == []


def test_funding_apr_empty_dataframe() -> None:
    mock_client = MagicMock()
    mock_client.funding_apr.return_value = pl.DataFrame()
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import funding_apr

        result = asyncio.run(
            funding_apr(symbol="deribit:BTC-PERPETUAL", start=0, end=100)
        )
    assert result == []
    mock_client.funding_apr.assert_called_once_with("deribit:BTC-PERPETUAL", 0, 100)


def test_funding_apr_returns_rows() -> None:
    mock_client = MagicMock()
    mock_client.funding_apr.return_value = pl.DataFrame(
        {
            "funding_ts": [100, 200],
            "funding_rate": [0.0001, 0.0002],
            "interval_hours": [8.0, 8.0],
            "apr": [0.1095, 0.219],
            "cumulative_funding": [0.0001, 0.0003],
        }
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import funding_apr

        result = asyncio.run(
            funding_apr(
                symbol="deribit:BTC-PERPETUAL",
                start=0,
                end=1000,
                limit=100,
            )
        )
    assert len(result) == 2
    assert result[0]["funding_ts"] == 100
    assert result[0]["funding_rate"] == 0.0001
    assert result[0]["apr"] == 0.1095
    assert result[1]["cumulative_funding"] == 0.0003
    mock_client.funding_apr.assert_called_once_with("deribit:BTC-PERPETUAL", 0, 1000)


def test_funding_apr_strips_whitespace_symbol() -> None:
    mock_client = MagicMock()
    mock_client.funding_apr.return_value = pl.DataFrame()
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import funding_apr

        asyncio.run(funding_apr(symbol="  binance:BTCUSDT  ", start=1, end=2))
    mock_client.funding_apr.assert_called_once_with("binance:BTCUSDT", 1, 2)


def test_funding_apr_applies_limit() -> None:
    mock_client = MagicMock()
    mock_client.funding_apr.return_value = pl.DataFrame(
        {
            "funding_ts": [1, 2, 3, 4, 5],
            "funding_rate": [0.0001] * 5,
            "interval_hours": [8.0] * 5,
            "apr": [0.1] * 5,
            "cumulative_funding": [0.0001 * i for i in range(1, 6)],
        }
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import funding_apr

        result = asyncio.run(
            funding_apr(symbol="x", start=0, end=99, limit=2)
        )
    assert len(result) == 2
    assert result[0]["funding_ts"] == 1
    assert result[1]["funding_ts"] == 2


def test_funding_apr_clamps_limit_max() -> None:
    mock_client = MagicMock()
    mock_client.funding_apr.return_value = pl.DataFrame(
        {
            "funding_ts": list(range(20)),
            "funding_rate": [0.0001] * 20,
            "interval_hours": [8.0] * 20,
            "apr": [0.1] * 20,
            "cumulative_funding": [0.0001] * 20,
        }
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import _FUNDING_APR_MAX_LIMIT, funding_apr

        result = asyncio.run(
            funding_apr(
                symbol="x",
                start=0,
                end=1,
                limit=_FUNDING_APR_MAX_LIMIT + 5000,
            )
        )
    assert len(result) == 20
    mock_client.funding_apr.assert_called_once()


def test_funding_apr_clamps_limit_minimum() -> None:
    mock_client = MagicMock()
    mock_client.funding_apr.return_value = pl.DataFrame(
        {
            "funding_ts": [1, 2, 3],
            "funding_rate": [0.0001, 0.0002, 0.0003],
            "interval_hours": [8.0, 8.0, 8.0],
            "apr": [0.1, 0.2, 0.3],
            "cumulative_funding": [0.0001, 0.0003, 0.0006],
        }
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import funding_apr

        result = asyncio.run(funding_apr(symbol="x", start=0, end=1, limit=0))
    assert len(result) == 1
    assert result[0]["funding_ts"] == 1


def test_funding_apr_query_error() -> None:
    mock_client = MagicMock()
    mock_client.funding_apr.side_effect = RuntimeError("internal path /secret/lake")
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import funding_apr

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(funding_apr(symbol="deribit:BTC-PERPETUAL", start=0, end=1))
    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Funding APR query failed."
    assert "secret" not in str(exc_info.value.detail)


def test_funding_apr_route_registered() -> None:
    """Ensure FastAPI route table includes GET /api/v1/funding-apr."""
    paths = {
        (getattr(r, "path", None), tuple(sorted(getattr(r, "methods", set()) or [])))
        for r in app.routes
    }
    assert ("/api/v1/funding-apr", ("GET",)) in paths


# ---------------------------------------------------------------------------
# GET /api/v1/basis — spot-perp basis (read-only, no payment)
# ---------------------------------------------------------------------------


def test_basis_missing_params_skips_client() -> None:
    mock_client = MagicMock()
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import basis

        assert asyncio.run(basis(spot="", perp="deribit:BTC-PERPETUAL")) == []
        assert asyncio.run(basis(spot="deribit:BTC-SPOT", perp="")) == []
        assert asyncio.run(basis(spot="  ", perp="  ")) == []
        assert asyncio.run(basis(spot="", perp="")) == []
    mock_client.spot_perp_basis.assert_not_called()


def test_basis_empty_lake(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CRYPCODILE_DATA_DIR", str(tmp_path))
    from crypcodile.api_server import basis

    result = asyncio.run(
        basis(
            spot="deribit:BTC-SPOT",
            perp="deribit:BTC-PERPETUAL",
            start=0,
            end=10**18,
        )
    )
    assert result == []


def test_basis_empty_dataframe() -> None:
    mock_client = MagicMock()
    mock_client.spot_perp_basis.return_value = pl.DataFrame()
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import basis

        result = asyncio.run(
            basis(
                spot="deribit:BTC-SPOT",
                perp="deribit:BTC-PERPETUAL",
                start=0,
                end=100,
            )
        )
    assert result == []
    mock_client.spot_perp_basis.assert_called_once_with(
        "deribit:BTC-SPOT", "deribit:BTC-PERPETUAL", 0, 100
    )


def test_basis_returns_rows() -> None:
    mock_client = MagicMock()
    mock_client.spot_perp_basis.return_value = pl.DataFrame(
        {
            "local_ts": [100, 200],
            "spot_price": [50000.0, 50100.0],
            "perp_price": [50100.0, 50200.0],
            "basis": [100.0, 100.0],
            "basis_pct": [0.002, 0.001996],
        }
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import basis

        result = asyncio.run(
            basis(
                spot="deribit:BTC-SPOT",
                perp="deribit:BTC-PERPETUAL",
                start=0,
                end=1000,
                limit=100,
            )
        )
    assert len(result) == 2
    assert result[0]["local_ts"] == 100
    assert result[0]["spot_price"] == 50000.0
    assert result[0]["perp_price"] == 50100.0
    assert result[0]["basis"] == 100.0
    assert result[1]["local_ts"] == 200
    mock_client.spot_perp_basis.assert_called_once_with(
        "deribit:BTC-SPOT", "deribit:BTC-PERPETUAL", 0, 1000
    )


def test_basis_strips_whitespace_symbols() -> None:
    mock_client = MagicMock()
    mock_client.spot_perp_basis.return_value = pl.DataFrame()
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import basis

        asyncio.run(
            basis(
                spot="  deribit:BTC-SPOT  ",
                perp="  deribit:BTC-PERPETUAL  ",
                start=1,
                end=2,
            )
        )
    mock_client.spot_perp_basis.assert_called_once_with(
        "deribit:BTC-SPOT", "deribit:BTC-PERPETUAL", 1, 2
    )


def test_basis_applies_limit() -> None:
    mock_client = MagicMock()
    mock_client.spot_perp_basis.return_value = pl.DataFrame(
        {
            "local_ts": [1, 2, 3, 4, 5],
            "spot_price": [100.0] * 5,
            "perp_price": [101.0] * 5,
            "basis": [1.0] * 5,
            "basis_pct": [0.01] * 5,
        }
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import basis

        result = asyncio.run(
            basis(spot="s", perp="p", start=0, end=99, limit=2)
        )
    assert len(result) == 2
    assert result[0]["local_ts"] == 1
    assert result[1]["local_ts"] == 2


def test_basis_clamps_limit_max() -> None:
    mock_client = MagicMock()
    mock_client.spot_perp_basis.return_value = pl.DataFrame(
        {
            "local_ts": list(range(20)),
            "spot_price": [100.0] * 20,
            "perp_price": [101.0] * 20,
            "basis": [1.0] * 20,
            "basis_pct": [0.01] * 20,
        }
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import _BASIS_MAX_LIMIT, basis

        result = asyncio.run(
            basis(
                spot="s",
                perp="p",
                start=0,
                end=1,
                limit=_BASIS_MAX_LIMIT + 5000,
            )
        )
    assert len(result) == 20
    mock_client.spot_perp_basis.assert_called_once()


def test_basis_clamps_limit_minimum() -> None:
    mock_client = MagicMock()
    mock_client.spot_perp_basis.return_value = pl.DataFrame(
        {
            "local_ts": [1, 2, 3],
            "spot_price": [100.0, 101.0, 102.0],
            "perp_price": [101.0, 102.0, 103.0],
            "basis": [1.0, 1.0, 1.0],
            "basis_pct": [0.01, 0.01, 0.01],
        }
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import basis

        result = asyncio.run(basis(spot="s", perp="p", start=0, end=1, limit=0))
    assert len(result) == 1
    assert result[0]["local_ts"] == 1


def test_basis_query_error() -> None:
    mock_client = MagicMock()
    mock_client.spot_perp_basis.side_effect = RuntimeError(
        "internal path /secret/lake"
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import basis

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(
                basis(
                    spot="deribit:BTC-SPOT",
                    perp="deribit:BTC-PERPETUAL",
                    start=0,
                    end=1,
                )
            )
    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Spot-perp basis query failed."
    assert "secret" not in str(exc_info.value.detail)


def test_basis_route_registered() -> None:
    """Ensure FastAPI route table includes GET /api/v1/basis."""
    paths = {
        (getattr(r, "path", None), tuple(sorted(getattr(r, "methods", set()) or [])))
        for r in app.routes
    }
    assert ("/api/v1/basis", ("GET",)) in paths


# ---------------------------------------------------------------------------
# GET /api/v1/perp-basis — mark-index perp basis (read-only, no payment)
# ---------------------------------------------------------------------------


def test_perp_basis_empty_symbol_skips_client() -> None:
    mock_client = MagicMock()
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import perp_basis

        assert asyncio.run(perp_basis(symbol="")) == []
        assert asyncio.run(perp_basis(symbol="  ")) == []
    mock_client.perp_basis.assert_not_called()


def test_perp_basis_empty_lake(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CRYPCODILE_DATA_DIR", str(tmp_path))
    from crypcodile.api_server import perp_basis

    result = asyncio.run(
        perp_basis(symbol="deribit:BTC-PERPETUAL", start=0, end=10**18)
    )
    assert result == []


def test_perp_basis_empty_dataframe() -> None:
    mock_client = MagicMock()
    mock_client.perp_basis.return_value = pl.DataFrame()
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import perp_basis

        result = asyncio.run(
            perp_basis(symbol="deribit:BTC-PERPETUAL", start=0, end=100)
        )
    assert result == []
    mock_client.perp_basis.assert_called_once_with(
        "deribit:BTC-PERPETUAL", 0, 100
    )


def test_perp_basis_returns_rows() -> None:
    mock_client = MagicMock()
    mock_client.perp_basis.return_value = pl.DataFrame(
        {
            "local_ts": [100, 200],
            "mark_price": [50100.0, 50200.0],
            "index_price": [50000.0, 50100.0],
            "basis": [100.0, 100.0],
            "basis_pct": [0.002, 0.001996],
        }
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import perp_basis

        result = asyncio.run(
            perp_basis(
                symbol="deribit:BTC-PERPETUAL",
                start=0,
                end=1000,
                limit=100,
            )
        )
    assert len(result) == 2
    assert result[0]["local_ts"] == 100
    assert result[0]["mark_price"] == 50100.0
    assert result[0]["index_price"] == 50000.0
    assert result[0]["basis"] == 100.0
    assert result[1]["local_ts"] == 200
    mock_client.perp_basis.assert_called_once_with(
        "deribit:BTC-PERPETUAL", 0, 1000
    )


def test_perp_basis_strips_whitespace_symbol() -> None:
    mock_client = MagicMock()
    mock_client.perp_basis.return_value = pl.DataFrame()
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import perp_basis

        asyncio.run(
            perp_basis(symbol="  deribit:BTC-PERPETUAL  ", start=1, end=2)
        )
    mock_client.perp_basis.assert_called_once_with(
        "deribit:BTC-PERPETUAL", 1, 2
    )


def test_perp_basis_applies_limit() -> None:
    mock_client = MagicMock()
    mock_client.perp_basis.return_value = pl.DataFrame(
        {
            "local_ts": [1, 2, 3, 4, 5],
            "mark_price": [101.0] * 5,
            "index_price": [100.0] * 5,
            "basis": [1.0] * 5,
            "basis_pct": [0.01] * 5,
        }
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import perp_basis

        result = asyncio.run(
            perp_basis(symbol="p", start=0, end=99, limit=2)
        )
    assert len(result) == 2
    assert result[0]["local_ts"] == 1
    assert result[1]["local_ts"] == 2


def test_perp_basis_clamps_limit_max() -> None:
    mock_client = MagicMock()
    mock_client.perp_basis.return_value = pl.DataFrame(
        {
            "local_ts": list(range(20)),
            "mark_price": [101.0] * 20,
            "index_price": [100.0] * 20,
            "basis": [1.0] * 20,
            "basis_pct": [0.01] * 20,
        }
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import _PERP_BASIS_MAX_LIMIT, perp_basis

        result = asyncio.run(
            perp_basis(
                symbol="p",
                start=0,
                end=1,
                limit=_PERP_BASIS_MAX_LIMIT + 5000,
            )
        )
    assert len(result) == 20
    mock_client.perp_basis.assert_called_once()


def test_perp_basis_clamps_limit_minimum() -> None:
    mock_client = MagicMock()
    mock_client.perp_basis.return_value = pl.DataFrame(
        {
            "local_ts": [1, 2, 3],
            "mark_price": [101.0, 102.0, 103.0],
            "index_price": [100.0, 101.0, 102.0],
            "basis": [1.0, 1.0, 1.0],
            "basis_pct": [0.01, 0.01, 0.01],
        }
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import perp_basis

        result = asyncio.run(perp_basis(symbol="p", start=0, end=1, limit=0))
    assert len(result) == 1
    assert result[0]["local_ts"] == 1


def test_perp_basis_query_error() -> None:
    mock_client = MagicMock()
    mock_client.perp_basis.side_effect = RuntimeError(
        "internal path /secret/lake"
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import perp_basis

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(
                perp_basis(
                    symbol="deribit:BTC-PERPETUAL",
                    start=0,
                    end=1,
                )
            )
    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Perp basis query failed."
    assert "secret" not in str(exc_info.value.detail)


def test_perp_basis_route_registered() -> None:
    """Ensure FastAPI route table includes GET /api/v1/perp-basis."""
    paths = {
        (getattr(r, "path", None), tuple(sorted(getattr(r, "methods", set()) or [])))
        for r in app.routes
    }
    assert ("/api/v1/perp-basis", ("GET",)) in paths


# ---------------------------------------------------------------------------
# GET /api/v1/spot-future-basis — spot-future ASOF basis (read-only, no payment)
# ---------------------------------------------------------------------------


def test_spot_future_basis_missing_params_skips_client() -> None:
    mock_client = MagicMock()
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import spot_future_basis

        assert asyncio.run(spot_future_basis(future="", spot="deribit:BTC-SPOT")) == []
        assert asyncio.run(spot_future_basis(future="deribit:BTC-FUTURE", spot="")) == []
        assert asyncio.run(spot_future_basis(future="  ", spot="  ")) == []
        assert asyncio.run(spot_future_basis(future="", spot="")) == []
    mock_client.spot_future_basis.assert_not_called()


def test_spot_future_basis_empty_lake(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CRYPCODILE_DATA_DIR", str(tmp_path))
    from crypcodile.api_server import spot_future_basis

    result = asyncio.run(
        spot_future_basis(
            future="deribit:BTC-FUTURE",
            spot="deribit:BTC-SPOT",
            start=0,
            end=10**18,
        )
    )
    assert result == []


def test_spot_future_basis_empty_dataframe() -> None:
    mock_client = MagicMock()
    mock_client.spot_future_basis.return_value = pl.DataFrame()
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import spot_future_basis

        result = asyncio.run(
            spot_future_basis(
                future="deribit:BTC-FUTURE",
                spot="deribit:BTC-SPOT",
                start=0,
                end=100,
            )
        )
    assert result == []
    mock_client.spot_future_basis.assert_called_once_with(
        "deribit:BTC-FUTURE", "deribit:BTC-SPOT", 0, 100
    )


def test_spot_future_basis_returns_rows() -> None:
    mock_client = MagicMock()
    mock_client.spot_future_basis.return_value = pl.DataFrame(
        {
            "local_ts": [100, 200],
            "future_price": [50100.0, 50200.0],
            "spot_price": [50000.0, 50100.0],
            "basis": [100.0, 100.0],
            "basis_pct": [0.002, 0.001996],
        }
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import spot_future_basis

        result = asyncio.run(
            spot_future_basis(
                future="deribit:BTC-FUTURE",
                spot="deribit:BTC-SPOT",
                start=0,
                end=1000,
                limit=100,
            )
        )
    assert len(result) == 2
    assert result[0]["local_ts"] == 100
    assert result[0]["future_price"] == 50100.0
    assert result[0]["spot_price"] == 50000.0
    assert result[0]["basis"] == 100.0
    assert result[1]["local_ts"] == 200
    mock_client.spot_future_basis.assert_called_once_with(
        "deribit:BTC-FUTURE", "deribit:BTC-SPOT", 0, 1000
    )


def test_spot_future_basis_strips_whitespace_symbols() -> None:
    mock_client = MagicMock()
    mock_client.spot_future_basis.return_value = pl.DataFrame()
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import spot_future_basis

        asyncio.run(
            spot_future_basis(
                future="  deribit:BTC-FUTURE  ",
                spot="  deribit:BTC-SPOT  ",
                start=1,
                end=2,
            )
        )
    mock_client.spot_future_basis.assert_called_once_with(
        "deribit:BTC-FUTURE", "deribit:BTC-SPOT", 1, 2
    )


def test_spot_future_basis_applies_limit() -> None:
    mock_client = MagicMock()
    mock_client.spot_future_basis.return_value = pl.DataFrame(
        {
            "local_ts": [1, 2, 3, 4, 5],
            "future_price": [101.0] * 5,
            "spot_price": [100.0] * 5,
            "basis": [1.0] * 5,
            "basis_pct": [0.01] * 5,
        }
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import spot_future_basis

        result = asyncio.run(
            spot_future_basis(future="f", spot="s", start=0, end=99, limit=2)
        )
    assert len(result) == 2
    assert result[0]["local_ts"] == 1
    assert result[1]["local_ts"] == 2


def test_spot_future_basis_clamps_limit_max() -> None:
    mock_client = MagicMock()
    mock_client.spot_future_basis.return_value = pl.DataFrame(
        {
            "local_ts": list(range(20)),
            "future_price": [101.0] * 20,
            "spot_price": [100.0] * 20,
            "basis": [1.0] * 20,
            "basis_pct": [0.01] * 20,
        }
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import (
            _SPOT_FUTURE_BASIS_MAX_LIMIT,
            spot_future_basis,
        )

        result = asyncio.run(
            spot_future_basis(
                future="f",
                spot="s",
                start=0,
                end=1,
                limit=_SPOT_FUTURE_BASIS_MAX_LIMIT + 5000,
            )
        )
    assert len(result) == 20
    mock_client.spot_future_basis.assert_called_once()


def test_spot_future_basis_clamps_limit_minimum() -> None:
    mock_client = MagicMock()
    mock_client.spot_future_basis.return_value = pl.DataFrame(
        {
            "local_ts": [1, 2, 3],
            "future_price": [101.0, 102.0, 103.0],
            "spot_price": [100.0, 101.0, 102.0],
            "basis": [1.0, 1.0, 1.0],
            "basis_pct": [0.01, 0.01, 0.01],
        }
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import spot_future_basis

        result = asyncio.run(
            spot_future_basis(future="f", spot="s", start=0, end=1, limit=0)
        )
    assert len(result) == 1
    assert result[0]["local_ts"] == 1


def test_spot_future_basis_query_error() -> None:
    mock_client = MagicMock()
    mock_client.spot_future_basis.side_effect = RuntimeError(
        "internal path /secret/lake"
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import spot_future_basis

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(
                spot_future_basis(
                    future="deribit:BTC-FUTURE",
                    spot="deribit:BTC-SPOT",
                    start=0,
                    end=1,
                )
            )
    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Spot-future basis query failed."
    assert "secret" not in str(exc_info.value.detail)


def test_spot_future_basis_route_registered() -> None:
    """Ensure FastAPI route table includes GET /api/v1/spot-future-basis."""
    paths = {
        (getattr(r, "path", None), tuple(sorted(getattr(r, "methods", set()) or [])))
        for r in app.routes
    }
    assert ("/api/v1/spot-future-basis", ("GET",)) in paths


# ---------------------------------------------------------------------------
# GET /api/v1/indicators — technical indicators on OHLCV (read-only, no payment)
# ---------------------------------------------------------------------------


def test_indicators_empty_symbol_skips_client() -> None:
    mock_client = MagicMock()
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import indicators

        result = asyncio.run(indicators(symbol="", start=0, end=100))
        result_ws = asyncio.run(indicators(symbol="  ", start=0, end=100))
    assert result == []
    assert result_ws == []
    mock_client.get_indicators.assert_not_called()


def test_indicators_empty_lake(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CRYPCODILE_DATA_DIR", str(tmp_path))
    from crypcodile.api_server import indicators

    result = asyncio.run(
        indicators(symbol="deribit:BTC-PERPETUAL", start=0, end=10**18)
    )
    assert result == []


def test_indicators_empty_dataframe() -> None:
    mock_client = MagicMock()
    mock_client.get_indicators.return_value = pl.DataFrame()
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import indicators

        result = asyncio.run(
            indicators(symbol="deribit:BTC-PERPETUAL", start=0, end=100)
        )
    assert result == []
    mock_client.get_indicators.assert_called_once_with(
        "deribit:BTC-PERPETUAL",
        0,
        100,
        interval="1d",
        indicator=None,
        period=14,
    )


def test_indicators_returns_rows() -> None:
    mock_client = MagicMock()
    mock_client.get_indicators.return_value = pl.DataFrame(
        {
            "bar": [1, 2],
            "open": [100.0, 101.0],
            "high": [105.0, 106.0],
            "low": [99.0, 100.0],
            "close": [101.0, 102.0],
            "volume": [10.0, 11.0],
            "sma": [100.5, 101.5],
        }
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import indicators

        result = asyncio.run(
            indicators(
                symbol="deribit:BTC-PERPETUAL",
                start=0,
                end=1000,
                interval="1h",
                indicator="sma",
                period=20,
                limit=100,
            )
        )
    assert len(result) == 2
    assert result[0]["bar"] == 1
    assert result[0]["sma"] == 100.5
    assert result[1]["close"] == 102.0
    mock_client.get_indicators.assert_called_once_with(
        "deribit:BTC-PERPETUAL",
        0,
        1000,
        interval="1h",
        indicator="sma",
        period=20,
    )


def test_indicators_strips_whitespace_symbol() -> None:
    mock_client = MagicMock()
    mock_client.get_indicators.return_value = pl.DataFrame()
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import indicators

        asyncio.run(
            indicators(
                symbol="  binance:BTCUSDT  ",
                start=1,
                end=2,
                interval=" 1m ",
                indicator="  rsi  ",
                period=7,
            )
        )
    mock_client.get_indicators.assert_called_once_with(
        "binance:BTCUSDT",
        1,
        2,
        interval="1m",
        indicator="rsi",
        period=7,
    )


def test_indicators_empty_indicator_means_all() -> None:
    mock_client = MagicMock()
    mock_client.get_indicators.return_value = pl.DataFrame()
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import indicators

        asyncio.run(
            indicators(symbol="x", start=0, end=1, indicator="", interval="")
        )
    mock_client.get_indicators.assert_called_once_with(
        "x",
        0,
        1,
        interval="1d",
        indicator=None,
        period=14,
    )


def test_indicators_applies_limit() -> None:
    mock_client = MagicMock()
    mock_client.get_indicators.return_value = pl.DataFrame(
        {
            "bar": [1, 2, 3, 4, 5],
            "close": [100.0] * 5,
            "sma": [100.0] * 5,
        }
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import indicators

        result = asyncio.run(
            indicators(symbol="x", start=0, end=99, limit=2)
        )
    assert len(result) == 2
    assert result[0]["bar"] == 1
    assert result[1]["bar"] == 2


def test_indicators_clamps_limit_max() -> None:
    mock_client = MagicMock()
    mock_client.get_indicators.return_value = pl.DataFrame(
        {
            "bar": list(range(20)),
            "close": [100.0] * 20,
            "sma": [100.0] * 20,
        }
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import _INDICATORS_MAX_LIMIT, indicators

        result = asyncio.run(
            indicators(
                symbol="x",
                start=0,
                end=1,
                limit=_INDICATORS_MAX_LIMIT + 5000,
            )
        )
    assert len(result) == 20
    mock_client.get_indicators.assert_called_once()


def test_indicators_clamps_limit_minimum() -> None:
    mock_client = MagicMock()
    mock_client.get_indicators.return_value = pl.DataFrame(
        {
            "bar": [1, 2, 3],
            "close": [100.0, 101.0, 102.0],
            "sma": [100.0, 100.5, 101.0],
        }
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import indicators

        result = asyncio.run(indicators(symbol="x", start=0, end=1, limit=0))
    assert len(result) == 1
    assert result[0]["bar"] == 1


def test_indicators_clamps_period_minimum() -> None:
    mock_client = MagicMock()
    mock_client.get_indicators.return_value = pl.DataFrame()
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import indicators

        asyncio.run(indicators(symbol="x", start=0, end=1, period=0))
    mock_client.get_indicators.assert_called_once_with(
        "x",
        0,
        1,
        interval="1d",
        indicator=None,
        period=1,
    )


def test_indicators_unknown_indicator_400() -> None:
    mock_client = MagicMock()
    mock_client.get_indicators.side_effect = ValueError(
        "Unknown indicator 'nope'"
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import indicators

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(
                indicators(
                    symbol="deribit:BTC-PERPETUAL",
                    start=0,
                    end=1,
                    indicator="nope",
                )
            )
    assert exc_info.value.status_code == 400
    assert "nope" in str(exc_info.value.detail)


def test_indicators_query_error() -> None:
    mock_client = MagicMock()
    mock_client.get_indicators.side_effect = RuntimeError(
        "internal path /secret/lake"
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import indicators

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(
                indicators(symbol="deribit:BTC-PERPETUAL", start=0, end=1)
            )
    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Indicators query failed."
    assert "secret" not in str(exc_info.value.detail)


def test_indicators_route_registered() -> None:
    """Ensure FastAPI route table includes GET /api/v1/indicators."""
    paths = {
        (getattr(r, "path", None), tuple(sorted(getattr(r, "methods", set()) or [])))
        for r in app.routes
    }
    assert ("/api/v1/indicators", ("GET",)) in paths


# ---------------------------------------------------------------------------
# GET /api/v1/ofi — order flow imbalance (read-only, no payment)
# ---------------------------------------------------------------------------


def test_ofi_empty_symbol_skips_client() -> None:
    mock_client = MagicMock()
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import ofi

        result = asyncio.run(ofi(symbol="", start=0, end=100))
        result_ws = asyncio.run(ofi(symbol="  ", start=0, end=100))
    assert result == []
    assert result_ws == []
    mock_client.calculate_ofi.assert_not_called()


def test_ofi_empty_lake(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CRYPCODILE_DATA_DIR", str(tmp_path))
    from crypcodile.api_server import ofi

    result = asyncio.run(
        ofi(symbol="deribit:BTC-PERPETUAL", start=0, end=10**18, interval="1m")
    )
    assert result == []


def test_ofi_empty_dataframe() -> None:
    mock_client = MagicMock()
    mock_client.calculate_ofi.return_value = pl.DataFrame()
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import ofi

        result = asyncio.run(
            ofi(symbol="deribit:BTC-PERPETUAL", start=0, end=100, interval="1m")
        )
    assert result == []
    mock_client.calculate_ofi.assert_called_once_with(
        "deribit:BTC-PERPETUAL", 0, 100, "1m"
    )


def test_ofi_returns_rows() -> None:
    mock_client = MagicMock()
    mock_client.calculate_ofi.return_value = pl.DataFrame(
        {
            "timestamp": [100, 200],
            "best_bid": [100.0, 101.0],
            "best_ask": [100.5, 101.5],
            "ofi": [1.5, -0.5],
        }
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import ofi

        result = asyncio.run(
            ofi(
                symbol="deribit:BTC-PERPETUAL",
                start=0,
                end=1000,
                interval="5m",
                limit=100,
            )
        )
    assert len(result) == 2
    assert result[0]["timestamp"] == 100
    assert result[0]["best_bid"] == 100.0
    assert result[0]["ofi"] == 1.5
    assert result[1]["timestamp"] == 200
    assert result[1]["ofi"] == -0.5
    mock_client.calculate_ofi.assert_called_once_with(
        "deribit:BTC-PERPETUAL", 0, 1000, "5m"
    )


def test_ofi_strips_whitespace_symbol_and_interval() -> None:
    mock_client = MagicMock()
    mock_client.calculate_ofi.return_value = pl.DataFrame()
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import ofi

        asyncio.run(
            ofi(
                symbol="  binance:BTCUSDT  ",
                start=1,
                end=2,
                interval=" 1h ",
            )
        )
    mock_client.calculate_ofi.assert_called_once_with(
        "binance:BTCUSDT", 1, 2, "1h"
    )


def test_ofi_default_interval_is_1m() -> None:
    mock_client = MagicMock()
    mock_client.calculate_ofi.return_value = pl.DataFrame()
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import ofi

        asyncio.run(ofi(symbol="x", start=0, end=1, interval=""))
    mock_client.calculate_ofi.assert_called_once_with("x", 0, 1, "1m")


def test_ofi_applies_limit() -> None:
    mock_client = MagicMock()
    mock_client.calculate_ofi.return_value = pl.DataFrame(
        {
            "timestamp": [1, 2, 3, 4, 5],
            "best_bid": [100.0] * 5,
            "best_ask": [100.5] * 5,
            "ofi": [0.1, 0.2, 0.3, 0.4, 0.5],
        }
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import ofi

        result = asyncio.run(ofi(symbol="x", start=0, end=99, limit=2))
    assert len(result) == 2
    assert result[0]["timestamp"] == 1
    assert result[1]["timestamp"] == 2


def test_ofi_clamps_limit_max() -> None:
    mock_client = MagicMock()
    mock_client.calculate_ofi.return_value = pl.DataFrame(
        {
            "timestamp": list(range(20)),
            "best_bid": [100.0] * 20,
            "best_ask": [100.5] * 20,
            "ofi": [0.1] * 20,
        }
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import _OFI_MAX_LIMIT, ofi

        result = asyncio.run(
            ofi(
                symbol="x",
                start=0,
                end=1,
                limit=_OFI_MAX_LIMIT + 5000,
            )
        )
    assert len(result) == 20
    mock_client.calculate_ofi.assert_called_once()


def test_ofi_clamps_limit_minimum() -> None:
    mock_client = MagicMock()
    mock_client.calculate_ofi.return_value = pl.DataFrame(
        {
            "timestamp": [1, 2, 3],
            "best_bid": [100.0, 101.0, 102.0],
            "best_ask": [100.5, 101.5, 102.5],
            "ofi": [0.1, 0.2, 0.3],
        }
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import ofi

        result = asyncio.run(ofi(symbol="x", start=0, end=1, limit=0))
    assert len(result) == 1
    assert result[0]["timestamp"] == 1


def test_ofi_invalid_interval_400() -> None:
    mock_client = MagicMock()
    mock_client.calculate_ofi.side_effect = ValueError(
        "Unknown interval unit 'x' in '1x'. Supported units are s, m, h, d."
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import ofi

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(
                ofi(
                    symbol="deribit:BTC-PERPETUAL",
                    start=0,
                    end=1,
                    interval="1x",
                )
            )
    assert exc_info.value.status_code == 400
    assert "1x" in str(exc_info.value.detail)


def test_ofi_query_error() -> None:
    mock_client = MagicMock()
    mock_client.calculate_ofi.side_effect = RuntimeError(
        "internal path /secret/lake"
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import ofi

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(
                ofi(symbol="deribit:BTC-PERPETUAL", start=0, end=1, interval="1m")
            )
    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "OFI query failed."
    assert "secret" not in str(exc_info.value.detail)


def test_ofi_route_registered() -> None:
    """Ensure FastAPI route table includes GET /api/v1/ofi."""
    paths = {
        (getattr(r, "path", None), tuple(sorted(getattr(r, "methods", set()) or [])))
        for r in app.routes
    }
    assert ("/api/v1/ofi", ("GET",)) in paths


# ---------------------------------------------------------------------------
# GET /api/v1/whale-alerts — whale trades/liquidations (read-only, no payment)
# ---------------------------------------------------------------------------


def test_whale_alerts_empty_symbol_skips_client() -> None:
    mock_client = MagicMock()
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import whale_alerts

        result = asyncio.run(whale_alerts(symbol="", start=0, end=100, min_usd=1000.0))
        result_ws = asyncio.run(
            whale_alerts(symbol="  ", start=0, end=100, min_usd=1000.0)
        )
    assert result == []
    assert result_ws == []
    mock_client.track_whale_alerts.assert_not_called()


def test_whale_alerts_empty_lake(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CRYPCODILE_DATA_DIR", str(tmp_path))
    from crypcodile.api_server import whale_alerts

    result = asyncio.run(
        whale_alerts(
            symbol="deribit:BTC-PERPETUAL",
            start=0,
            end=10**18,
            min_usd=1000.0,
        )
    )
    assert result == []


def test_whale_alerts_empty_dataframe() -> None:
    mock_client = MagicMock()
    mock_client.track_whale_alerts.return_value = pl.DataFrame()
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import whale_alerts

        result = asyncio.run(
            whale_alerts(
                symbol="deribit:BTC-PERPETUAL",
                start=0,
                end=100,
                min_usd=500.0,
            )
        )
    assert result == []
    mock_client.track_whale_alerts.assert_called_once_with(
        "deribit:BTC-PERPETUAL", 0, 100, 500.0
    )


def test_whale_alerts_returns_rows() -> None:
    mock_client = MagicMock()
    mock_client.track_whale_alerts.return_value = pl.DataFrame(
        {
            "timestamp": [100, 200],
            "event_type": ["Trade", "Liquidation"],
            "price": [50000.0, 49000.0],
            "amount": [2.0, 3.0],
            "usd_value": [100000.0, 147000.0],
            "side": ["buy", "sell"],
        }
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import whale_alerts

        result = asyncio.run(
            whale_alerts(
                symbol="deribit:BTC-PERPETUAL",
                start=0,
                end=1000,
                min_usd=1000.0,
                limit=100,
            )
        )
    assert len(result) == 2
    assert result[0]["timestamp"] == 100
    assert result[0]["event_type"] == "Trade"
    assert result[0]["usd_value"] == 100000.0
    assert result[1]["event_type"] == "Liquidation"
    mock_client.track_whale_alerts.assert_called_once_with(
        "deribit:BTC-PERPETUAL", 0, 1000, 1000.0
    )


def test_whale_alerts_strips_whitespace_symbol() -> None:
    mock_client = MagicMock()
    mock_client.track_whale_alerts.return_value = pl.DataFrame()
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import whale_alerts

        asyncio.run(
            whale_alerts(
                symbol="  binance:BTCUSDT  ",
                start=1,
                end=2,
                min_usd=0.0,
            )
        )
    mock_client.track_whale_alerts.assert_called_once_with(
        "binance:BTCUSDT", 1, 2, 0.0
    )


def test_whale_alerts_applies_limit() -> None:
    mock_client = MagicMock()
    mock_client.track_whale_alerts.return_value = pl.DataFrame(
        {
            "timestamp": [1, 2, 3, 4, 5],
            "event_type": ["Trade"] * 5,
            "price": [100.0] * 5,
            "amount": [1.0] * 5,
            "usd_value": [100.0] * 5,
            "side": ["buy"] * 5,
        }
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import whale_alerts

        result = asyncio.run(
            whale_alerts(symbol="x", start=0, end=99, min_usd=0.0, limit=2)
        )
    assert len(result) == 2
    assert result[0]["timestamp"] == 1
    assert result[1]["timestamp"] == 2


def test_whale_alerts_clamps_limit_max() -> None:
    mock_client = MagicMock()
    mock_client.track_whale_alerts.return_value = pl.DataFrame(
        {
            "timestamp": list(range(20)),
            "event_type": ["Trade"] * 20,
            "price": [100.0] * 20,
            "amount": [1.0] * 20,
            "usd_value": [100.0] * 20,
            "side": ["buy"] * 20,
        }
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import _WHALE_ALERTS_MAX_LIMIT, whale_alerts

        result = asyncio.run(
            whale_alerts(
                symbol="x",
                start=0,
                end=1,
                min_usd=0.0,
                limit=_WHALE_ALERTS_MAX_LIMIT + 5000,
            )
        )
    assert len(result) == 20
    mock_client.track_whale_alerts.assert_called_once()


def test_whale_alerts_clamps_limit_minimum() -> None:
    mock_client = MagicMock()
    mock_client.track_whale_alerts.return_value = pl.DataFrame(
        {
            "timestamp": [1, 2, 3],
            "event_type": ["Trade", "Trade", "Liquidation"],
            "price": [100.0, 101.0, 102.0],
            "amount": [1.0, 1.0, 1.0],
            "usd_value": [100.0, 101.0, 102.0],
            "side": ["buy", "sell", "buy"],
        }
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import whale_alerts

        result = asyncio.run(
            whale_alerts(symbol="x", start=0, end=1, min_usd=0.0, limit=0)
        )
    assert len(result) == 1
    assert result[0]["timestamp"] == 1


def test_whale_alerts_negative_min_usd_400() -> None:
    mock_client = MagicMock()
    mock_client.track_whale_alerts.side_effect = ValueError(
        "min_usd must be non-negative."
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import whale_alerts

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(
                whale_alerts(
                    symbol="deribit:BTC-PERPETUAL",
                    start=0,
                    end=1,
                    min_usd=-1.0,
                )
            )
    assert exc_info.value.status_code == 400
    assert "min_usd" in str(exc_info.value.detail)


def test_whale_alerts_query_error() -> None:
    mock_client = MagicMock()
    mock_client.track_whale_alerts.side_effect = RuntimeError(
        "internal path /secret/lake"
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import whale_alerts

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(
                whale_alerts(
                    symbol="deribit:BTC-PERPETUAL",
                    start=0,
                    end=1,
                    min_usd=1000.0,
                )
            )
    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Whale alerts query failed."
    assert "secret" not in str(exc_info.value.detail)


def test_whale_alerts_route_registered() -> None:
    """Ensure FastAPI route table includes GET /api/v1/whale-alerts."""
    paths = {
        (getattr(r, "path", None), tuple(sorted(getattr(r, "methods", set()) or [])))
        for r in app.routes
    }
    assert ("/api/v1/whale-alerts", ("GET",)) in paths


# ---------------------------------------------------------------------------
# GET /api/v1/slippage — execution slippage estimate (read-only, no payment)
# ---------------------------------------------------------------------------


def test_slippage_empty_symbol_skips_client() -> None:
    mock_client = MagicMock()
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import slippage

        result = asyncio.run(slippage(symbol="", side="buy", size=1.0))
        result_ws = asyncio.run(slippage(symbol="  ", side="buy", size=1.0))
    assert result == []
    assert result_ws == []
    mock_client.estimate_slippage.assert_not_called()


def test_slippage_empty_lake_no_book(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CRYPCODILE_DATA_DIR", str(tmp_path))
    from crypcodile.api_server import slippage

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            slippage(symbol="deribit:BTC-PERPETUAL", side="buy", size=1.0)
        )
    assert exc_info.value.status_code == 400
    assert "No book snapshots" in str(exc_info.value.detail)


def test_slippage_returns_row() -> None:
    mock_client = MagicMock()
    mock_client.estimate_slippage.return_value = pl.DataFrame(
        {
            "symbol": ["deribit:BTC-PERPETUAL"],
            "side": ["buy"],
            "size": [1.5],
            "best_price": [50000.0],
            "expected_price": [50010.0],
            "slippage_usd": [10.0],
            "slippage_pct": [0.02],
        }
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import slippage

        result = asyncio.run(
            slippage(symbol="deribit:BTC-PERPETUAL", side="buy", size=1.5)
        )
    assert len(result) == 1
    assert result[0]["symbol"] == "deribit:BTC-PERPETUAL"
    assert result[0]["side"] == "buy"
    assert result[0]["size"] == 1.5
    assert result[0]["best_price"] == 50000.0
    assert result[0]["expected_price"] == 50010.0
    assert result[0]["slippage_usd"] == 10.0
    assert result[0]["slippage_pct"] == 0.02
    mock_client.estimate_slippage.assert_called_once_with(
        "deribit:BTC-PERPETUAL", "buy", 1.5
    )


def test_slippage_strips_whitespace_symbol_and_side() -> None:
    mock_client = MagicMock()
    mock_client.estimate_slippage.return_value = pl.DataFrame(
        {
            "symbol": ["binance:BTCUSDT"],
            "side": ["sell"],
            "size": [2.0],
            "best_price": [100.0],
            "expected_price": [99.5],
            "slippage_usd": [0.5],
            "slippage_pct": [0.5],
        }
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import slippage

        asyncio.run(
            slippage(symbol="  binance:BTCUSDT  ", side="  sell  ", size=2.0)
        )
    mock_client.estimate_slippage.assert_called_once_with(
        "binance:BTCUSDT", "sell", 2.0
    )


def test_slippage_empty_dataframe() -> None:
    mock_client = MagicMock()
    mock_client.estimate_slippage.return_value = pl.DataFrame()
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import slippage

        result = asyncio.run(slippage(symbol="x", side="buy", size=1.0))
    assert result == []


def test_slippage_invalid_side_400() -> None:
    mock_client = MagicMock()
    mock_client.estimate_slippage.side_effect = ValueError(
        "Invalid side 'hold'. Must be 'buy' or 'sell'."
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import slippage

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(slippage(symbol="x", side="hold", size=1.0))
    assert exc_info.value.status_code == 400
    assert "side" in str(exc_info.value.detail).lower()


def test_slippage_invalid_size_400() -> None:
    mock_client = MagicMock()
    mock_client.estimate_slippage.side_effect = ValueError(
        "Size must be greater than zero."
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import slippage

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(slippage(symbol="x", side="buy", size=0.0))
    assert exc_info.value.status_code == 400
    assert "Size" in str(exc_info.value.detail) or "size" in str(
        exc_info.value.detail
    ).lower()


def test_slippage_exceeds_depth_400() -> None:
    mock_client = MagicMock()
    mock_client.estimate_slippage.side_effect = ValueError(
        "Requested size 100 exceeds total order book depth (5.000000) "
        "for symbol 'x' on the buy side."
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import slippage

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(slippage(symbol="x", side="buy", size=100.0))
    assert exc_info.value.status_code == 400
    assert "depth" in str(exc_info.value.detail).lower()


def test_slippage_query_error() -> None:
    mock_client = MagicMock()
    mock_client.estimate_slippage.side_effect = RuntimeError(
        "internal path /secret/lake"
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import slippage

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(slippage(symbol="x", side="buy", size=1.0))
    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Slippage estimation failed."
    assert "secret" not in str(exc_info.value.detail)


def test_slippage_route_registered() -> None:
    """Ensure FastAPI route table includes GET /api/v1/slippage."""
    paths = {
        (getattr(r, "path", None), tuple(sorted(getattr(r, "methods", set()) or [])))
        for r in app.routes
    }
    assert ("/api/v1/slippage", ("GET",)) in paths


# ---------------------------------------------------------------------------
# GET /api/v1/iv-surface — implied-vol surface snapshot (read-only, no payment)
# ---------------------------------------------------------------------------


def test_iv_surface_empty_underlying_skips_client() -> None:
    mock_client = MagicMock()
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import iv_surface

        assert asyncio.run(iv_surface(underlying="", at=1)) == []
        assert asyncio.run(iv_surface(underlying="  ", at=1)) == []
    mock_client.iv_surface.assert_not_called()


def test_iv_surface_empty_lake(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CRYPCODILE_DATA_DIR", str(tmp_path))
    from crypcodile.api_server import iv_surface

    result = asyncio.run(
        iv_surface(underlying="BTC", at=1_700_000_000_000_000_000, rate=0.0)
    )
    assert result == []


def test_iv_surface_empty_dataframe() -> None:
    mock_client = MagicMock()
    mock_client.iv_surface.return_value = pl.DataFrame()
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import iv_surface

        result = asyncio.run(
            iv_surface(underlying="BTC", at=1_700_000_000_000_000_000, rate=0.01)
        )
    assert result == []
    mock_client.iv_surface.assert_called_once_with(
        "BTC", 1_700_000_000_000_000_000, rate=0.01
    )


def test_iv_surface_returns_rows() -> None:
    mock_client = MagicMock()
    mock_client.iv_surface.return_value = pl.DataFrame(
        {
            "expiry": [100, 100],
            "strike": [50000.0, 55000.0],
            "moneyness": [1.0, 1.1],
            "opt_type": ["C", "P"],
            "iv": [0.5, 0.55],
            "source": ["mark", "mark"],
        }
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import iv_surface

        result = asyncio.run(
            iv_surface(
                underlying="BTC",
                at=1_700_000_000_000_000_000,
                rate=0.0,
                limit=100,
            )
        )
    assert len(result) == 2
    assert result[0]["strike"] == 50000.0
    assert result[0]["iv"] == 0.5
    assert result[1]["opt_type"] == "P"
    mock_client.iv_surface.assert_called_once_with(
        "BTC", 1_700_000_000_000_000_000, rate=0.0
    )


def test_iv_surface_strips_whitespace_underlying() -> None:
    mock_client = MagicMock()
    mock_client.iv_surface.return_value = pl.DataFrame()
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import iv_surface

        asyncio.run(iv_surface(underlying="  BTC  ", at=42, rate=0.02))
    mock_client.iv_surface.assert_called_once_with("BTC", 42, rate=0.02)


def test_iv_surface_applies_limit() -> None:
    mock_client = MagicMock()
    mock_client.iv_surface.return_value = pl.DataFrame(
        {
            "expiry": [1, 2, 3, 4, 5],
            "strike": [100.0] * 5,
            "moneyness": [1.0] * 5,
            "opt_type": ["C"] * 5,
            "iv": [0.5] * 5,
            "source": ["mark"] * 5,
        }
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import iv_surface

        result = asyncio.run(
            iv_surface(underlying="BTC", at=1, rate=0.0, limit=2)
        )
    assert len(result) == 2
    assert result[0]["expiry"] == 1
    assert result[1]["expiry"] == 2


def test_iv_surface_clamps_limit_max() -> None:
    mock_client = MagicMock()
    mock_client.iv_surface.return_value = pl.DataFrame(
        {
            "expiry": list(range(20)),
            "strike": [100.0] * 20,
            "moneyness": [1.0] * 20,
            "opt_type": ["C"] * 20,
            "iv": [0.5] * 20,
            "source": ["mark"] * 20,
        }
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import _IV_SURFACE_MAX_LIMIT, iv_surface

        result = asyncio.run(
            iv_surface(
                underlying="BTC",
                at=1,
                rate=0.0,
                limit=_IV_SURFACE_MAX_LIMIT + 5000,
            )
        )
    assert len(result) == 20
    mock_client.iv_surface.assert_called_once()


def test_iv_surface_clamps_limit_minimum() -> None:
    mock_client = MagicMock()
    mock_client.iv_surface.return_value = pl.DataFrame(
        {
            "expiry": [1, 2, 3],
            "strike": [100.0, 101.0, 102.0],
            "moneyness": [1.0, 1.0, 1.0],
            "opt_type": ["C", "C", "C"],
            "iv": [0.5, 0.5, 0.5],
            "source": ["mark", "mark", "mark"],
        }
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import iv_surface

        result = asyncio.run(
            iv_surface(underlying="BTC", at=1, rate=0.0, limit=0)
        )
    assert len(result) == 1
    assert result[0]["expiry"] == 1


def test_iv_surface_query_error() -> None:
    mock_client = MagicMock()
    mock_client.iv_surface.side_effect = RuntimeError(
        "internal path /secret/lake"
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import iv_surface

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(iv_surface(underlying="BTC", at=1, rate=0.0))
    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "IV surface query failed."
    assert "secret" not in str(exc_info.value.detail)


def test_iv_surface_route_registered() -> None:
    """Ensure FastAPI route table includes GET /api/v1/iv-surface."""
    paths = {
        (getattr(r, "path", None), tuple(sorted(getattr(r, "methods", set()) or [])))
        for r in app.routes
    }
    assert ("/api/v1/iv-surface", ("GET",)) in paths


# ---------------------------------------------------------------------------
# GET /api/v1/term-structure — ATM IV term structure (read-only, no payment)
# ---------------------------------------------------------------------------


def test_term_structure_empty_underlying_skips_client() -> None:
    mock_client = MagicMock()
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import term_structure

        assert asyncio.run(term_structure(underlying="", at=1)) == []
        assert asyncio.run(term_structure(underlying="  ", at=1)) == []
    mock_client.term_structure.assert_not_called()


def test_term_structure_empty_lake(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CRYPCODILE_DATA_DIR", str(tmp_path))
    from crypcodile.api_server import term_structure

    result = asyncio.run(
        term_structure(underlying="ETH", at=1_700_000_000_000_000_000, rate=0.0)
    )
    assert result == []


def test_term_structure_empty_dataframe() -> None:
    mock_client = MagicMock()
    mock_client.term_structure.return_value = pl.DataFrame()
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import term_structure

        result = asyncio.run(
            term_structure(underlying="ETH", at=1_700_000_000_000_000_000, rate=0.01)
        )
    assert result == []
    mock_client.term_structure.assert_called_once_with(
        "ETH", 1_700_000_000_000_000_000, rate=0.01
    )


def test_term_structure_returns_rows() -> None:
    mock_client = MagicMock()
    mock_client.term_structure.return_value = pl.DataFrame(
        {
            "expiry": [100, 200],
            "days_to_expiry": [7.0, 30.0],
            "atm_strike": [50000.0, 51000.0],
            "atm_iv": [0.45, 0.50],
        }
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import term_structure

        result = asyncio.run(
            term_structure(
                underlying="ETH",
                at=1_700_000_000_000_000_000,
                rate=0.0,
                limit=100,
            )
        )
    assert len(result) == 2
    assert result[0]["days_to_expiry"] == 7.0
    assert result[0]["atm_iv"] == 0.45
    assert result[1]["atm_strike"] == 51000.0
    mock_client.term_structure.assert_called_once_with(
        "ETH", 1_700_000_000_000_000_000, rate=0.0
    )


def test_term_structure_strips_whitespace_underlying() -> None:
    mock_client = MagicMock()
    mock_client.term_structure.return_value = pl.DataFrame()
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import term_structure

        asyncio.run(term_structure(underlying="  ETH  ", at=99, rate=0.03))
    mock_client.term_structure.assert_called_once_with("ETH", 99, rate=0.03)


def test_term_structure_applies_limit() -> None:
    mock_client = MagicMock()
    mock_client.term_structure.return_value = pl.DataFrame(
        {
            "expiry": [1, 2, 3, 4, 5],
            "days_to_expiry": [1.0, 2.0, 3.0, 4.0, 5.0],
            "atm_strike": [100.0] * 5,
            "atm_iv": [0.4] * 5,
        }
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import term_structure

        result = asyncio.run(
            term_structure(underlying="ETH", at=1, rate=0.0, limit=2)
        )
    assert len(result) == 2
    assert result[0]["expiry"] == 1
    assert result[1]["expiry"] == 2


def test_term_structure_clamps_limit_max() -> None:
    mock_client = MagicMock()
    mock_client.term_structure.return_value = pl.DataFrame(
        {
            "expiry": list(range(20)),
            "days_to_expiry": [float(i) for i in range(20)],
            "atm_strike": [100.0] * 20,
            "atm_iv": [0.4] * 20,
        }
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import _TERM_STRUCTURE_MAX_LIMIT, term_structure

        result = asyncio.run(
            term_structure(
                underlying="ETH",
                at=1,
                rate=0.0,
                limit=_TERM_STRUCTURE_MAX_LIMIT + 5000,
            )
        )
    assert len(result) == 20
    mock_client.term_structure.assert_called_once()


def test_term_structure_clamps_limit_minimum() -> None:
    mock_client = MagicMock()
    mock_client.term_structure.return_value = pl.DataFrame(
        {
            "expiry": [1, 2, 3],
            "days_to_expiry": [1.0, 2.0, 3.0],
            "atm_strike": [100.0, 101.0, 102.0],
            "atm_iv": [0.4, 0.41, 0.42],
        }
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import term_structure

        result = asyncio.run(
            term_structure(underlying="ETH", at=1, rate=0.0, limit=0)
        )
    assert len(result) == 1
    assert result[0]["expiry"] == 1


def test_term_structure_query_error() -> None:
    mock_client = MagicMock()
    mock_client.term_structure.side_effect = RuntimeError(
        "internal path /secret/lake"
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import term_structure

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(term_structure(underlying="ETH", at=1, rate=0.0))
    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Term structure query failed."
    assert "secret" not in str(exc_info.value.detail)


def test_term_structure_route_registered() -> None:
    """Ensure FastAPI route table includes GET /api/v1/term-structure."""
    paths = {
        (getattr(r, "path", None), tuple(sorted(getattr(r, "methods", set()) or [])))
        for r in app.routes
    }
    assert ("/api/v1/term-structure", ("GET",)) in paths


# ---------------------------------------------------------------------------
# GET /api/v1/vol-skew — per-strike IV/delta for one expiry (read-only, no payment)
# ---------------------------------------------------------------------------


def test_vol_skew_empty_underlying_skips_client() -> None:
    mock_client = MagicMock()
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import vol_skew

        assert asyncio.run(vol_skew(underlying="", expiry_ns=1, at=1)) == []
        assert asyncio.run(vol_skew(underlying="  ", expiry_ns=1, at=1)) == []
    mock_client.vol_skew.assert_not_called()


def test_vol_skew_empty_lake(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CRYPCODILE_DATA_DIR", str(tmp_path))
    from crypcodile.api_server import vol_skew

    result = asyncio.run(
        vol_skew(
            underlying="BTC",
            expiry_ns=1_735_689_600_000_000_000,
            at=1_700_000_000_000_000_000,
            rate=0.0,
        )
    )
    assert result == []


def test_vol_skew_empty_dataframe() -> None:
    mock_client = MagicMock()
    mock_client.vol_skew.return_value = pl.DataFrame()
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import vol_skew

        result = asyncio.run(
            vol_skew(
                underlying="BTC",
                expiry_ns=100,
                at=1_700_000_000_000_000_000,
                rate=0.01,
            )
        )
    assert result == []
    mock_client.vol_skew.assert_called_once_with(
        "BTC", 100, 1_700_000_000_000_000_000, rate=0.01
    )


def test_vol_skew_returns_rows() -> None:
    mock_client = MagicMock()
    mock_client.vol_skew.return_value = pl.DataFrame(
        {
            "strike": [50000.0, 55000.0],
            "moneyness": [1.0, 1.1],
            "opt_type": ["C", "P"],
            "iv": [0.5, 0.55],
            "delta": [0.5, -0.45],
        }
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import vol_skew

        result = asyncio.run(
            vol_skew(
                underlying="BTC",
                expiry_ns=100,
                at=1_700_000_000_000_000_000,
                rate=0.0,
                limit=100,
            )
        )
    assert len(result) == 2
    assert result[0]["strike"] == 50000.0
    assert result[0]["iv"] == 0.5
    assert result[0]["delta"] == 0.5
    assert result[1]["opt_type"] == "P"
    mock_client.vol_skew.assert_called_once_with(
        "BTC", 100, 1_700_000_000_000_000_000, rate=0.0
    )


def test_vol_skew_strips_whitespace_underlying() -> None:
    mock_client = MagicMock()
    mock_client.vol_skew.return_value = pl.DataFrame()
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import vol_skew

        asyncio.run(vol_skew(underlying="  BTC  ", expiry_ns=42, at=99, rate=0.02))
    mock_client.vol_skew.assert_called_once_with("BTC", 42, 99, rate=0.02)


def test_vol_skew_applies_limit() -> None:
    mock_client = MagicMock()
    mock_client.vol_skew.return_value = pl.DataFrame(
        {
            "strike": [90.0, 100.0, 110.0, 120.0, 130.0],
            "moneyness": [0.9, 1.0, 1.1, 1.2, 1.3],
            "opt_type": ["C"] * 5,
            "iv": [0.5] * 5,
            "delta": [0.5] * 5,
        }
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import vol_skew

        result = asyncio.run(
            vol_skew(underlying="BTC", expiry_ns=1, at=1, rate=0.0, limit=2)
        )
    assert len(result) == 2
    assert result[0]["strike"] == 90.0
    assert result[1]["strike"] == 100.0


def test_vol_skew_clamps_limit_max() -> None:
    mock_client = MagicMock()
    mock_client.vol_skew.return_value = pl.DataFrame(
        {
            "strike": [float(i) for i in range(20)],
            "moneyness": [1.0] * 20,
            "opt_type": ["C"] * 20,
            "iv": [0.5] * 20,
            "delta": [0.5] * 20,
        }
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import _VOL_SKEW_MAX_LIMIT, vol_skew

        result = asyncio.run(
            vol_skew(
                underlying="BTC",
                expiry_ns=1,
                at=1,
                rate=0.0,
                limit=_VOL_SKEW_MAX_LIMIT + 5000,
            )
        )
    assert len(result) == 20
    mock_client.vol_skew.assert_called_once()


def test_vol_skew_clamps_limit_minimum() -> None:
    mock_client = MagicMock()
    mock_client.vol_skew.return_value = pl.DataFrame(
        {
            "strike": [90.0, 100.0, 110.0],
            "moneyness": [0.9, 1.0, 1.1],
            "opt_type": ["C", "C", "C"],
            "iv": [0.5, 0.5, 0.5],
            "delta": [0.5, 0.5, 0.5],
        }
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import vol_skew

        result = asyncio.run(
            vol_skew(underlying="BTC", expiry_ns=1, at=1, rate=0.0, limit=0)
        )
    assert len(result) == 1
    assert result[0]["strike"] == 90.0


def test_vol_skew_query_error() -> None:
    mock_client = MagicMock()
    mock_client.vol_skew.side_effect = RuntimeError("internal path /secret/lake")
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import vol_skew

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(vol_skew(underlying="BTC", expiry_ns=1, at=1, rate=0.0))
    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Vol skew query failed."
    assert "secret" not in str(exc_info.value.detail)


def test_vol_skew_route_registered() -> None:
    """Ensure FastAPI route table includes GET /api/v1/vol-skew."""
    paths = {
        (getattr(r, "path", None), tuple(sorted(getattr(r, "methods", set()) or [])))
        for r in app.routes
    }
    assert ("/api/v1/vol-skew", ("GET",)) in paths


# ---------------------------------------------------------------------------
# GET /api/v1/risk-reversal — RR/BF from vol skew (read-only, no payment)
# ---------------------------------------------------------------------------


def test_risk_reversal_empty_underlying_skips_client() -> None:
    mock_client = MagicMock()
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import risk_reversal

        result = asyncio.run(risk_reversal(underlying="", expiry_ns=1, at=1))
        result_ws = asyncio.run(risk_reversal(underlying="  ", expiry_ns=1, at=1))
    assert result["risk_reversal"] is None
    assert result["butterfly"] is None
    assert result["underlying"] == ""
    assert result_ws["risk_reversal"] is None
    assert result_ws["butterfly"] is None
    mock_client.vol_skew.assert_not_called()
    mock_client.risk_reversal_butterfly.assert_not_called()


def test_risk_reversal_empty_lake(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CRYPCODILE_DATA_DIR", str(tmp_path))
    from crypcodile.api_server import risk_reversal

    result = asyncio.run(
        risk_reversal(
            underlying="BTC",
            expiry_ns=1_735_689_600_000_000_000,
            at=1_700_000_000_000_000_000,
            rate=0.0,
            target_delta=0.25,
        )
    )
    assert result["underlying"] == "BTC"
    assert result["risk_reversal"] is None
    assert result["butterfly"] is None
    assert result["target_delta"] == 0.25


def test_risk_reversal_empty_skew_skips_butterfly() -> None:
    mock_client = MagicMock()
    mock_client.vol_skew.return_value = pl.DataFrame()
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import risk_reversal

        result = asyncio.run(
            risk_reversal(
                underlying="BTC",
                expiry_ns=100,
                at=1_700_000_000_000_000_000,
                rate=0.01,
                target_delta=0.25,
            )
        )
    assert result["risk_reversal"] is None
    assert result["butterfly"] is None
    assert result["rate"] == 0.01
    mock_client.vol_skew.assert_called_once_with(
        "BTC", 100, 1_700_000_000_000_000_000, rate=0.01
    )
    mock_client.risk_reversal_butterfly.assert_not_called()


def test_risk_reversal_returns_metrics() -> None:
    mock_client = MagicMock()
    skew = pl.DataFrame(
        {
            "strike": [50000.0, 55000.0],
            "moneyness": [1.0, 1.1],
            "opt_type": ["C", "P"],
            "iv": [0.5, 0.55],
            "delta": [0.25, -0.25],
        }
    )
    mock_client.vol_skew.return_value = skew
    mock_client.risk_reversal_butterfly.return_value = (0.05, -0.01)
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import risk_reversal

        result = asyncio.run(
            risk_reversal(
                underlying="BTC",
                expiry_ns=100,
                at=1_700_000_000_000_000_000,
                rate=0.0,
                target_delta=0.25,
            )
        )
    assert result["underlying"] == "BTC"
    assert result["expiry_ns"] == 100
    assert result["at"] == 1_700_000_000_000_000_000
    assert result["rate"] == 0.0
    assert result["target_delta"] == 0.25
    assert result["risk_reversal"] == pytest.approx(0.05)
    assert result["butterfly"] == pytest.approx(-0.01)
    mock_client.vol_skew.assert_called_once_with(
        "BTC", 100, 1_700_000_000_000_000_000, rate=0.0
    )
    mock_client.risk_reversal_butterfly.assert_called_once()
    call_args = mock_client.risk_reversal_butterfly.call_args
    assert call_args.args[0] is skew or call_args[0][0] is skew
    assert call_args.kwargs.get("target_delta") == 0.25 or (
        len(call_args.args) > 1 and call_args.args[1] == 0.25
    )


def test_risk_reversal_strips_whitespace_underlying() -> None:
    mock_client = MagicMock()
    mock_client.vol_skew.return_value = pl.DataFrame()
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import risk_reversal

        result = asyncio.run(
            risk_reversal(
                underlying="  ETH  ",
                expiry_ns=42,
                at=99,
                rate=0.02,
                target_delta=0.10,
            )
        )
    assert result["underlying"] == "ETH"
    mock_client.vol_skew.assert_called_once_with("ETH", 42, 99, rate=0.02)


def test_risk_reversal_custom_target_delta() -> None:
    mock_client = MagicMock()
    skew = pl.DataFrame(
        {
            "strike": [100.0],
            "moneyness": [1.0],
            "opt_type": ["C"],
            "iv": [0.4],
            "delta": [0.5],
        }
    )
    mock_client.vol_skew.return_value = skew
    mock_client.risk_reversal_butterfly.return_value = (None, None)
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import risk_reversal

        result = asyncio.run(
            risk_reversal(
                underlying="BTC",
                expiry_ns=1,
                at=2,
                rate=0.0,
                target_delta=0.10,
            )
        )
    assert result["target_delta"] == 0.10
    assert result["risk_reversal"] is None
    assert result["butterfly"] is None
    mock_client.risk_reversal_butterfly.assert_called_once_with(
        skew, target_delta=0.10
    )


def test_risk_reversal_query_error() -> None:
    mock_client = MagicMock()
    mock_client.vol_skew.side_effect = RuntimeError("internal path /secret/lake")
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import risk_reversal

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(
                risk_reversal(underlying="BTC", expiry_ns=1, at=1, rate=0.0)
            )
    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Risk reversal query failed."
    assert "secret" not in str(exc_info.value.detail)


def test_risk_reversal_butterfly_error() -> None:
    mock_client = MagicMock()
    mock_client.vol_skew.return_value = pl.DataFrame(
        {
            "strike": [100.0],
            "moneyness": [1.0],
            "opt_type": ["C"],
            "iv": [0.5],
            "delta": [0.5],
        }
    )
    mock_client.risk_reversal_butterfly.side_effect = RuntimeError(
        "internal path /secret/skew"
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import risk_reversal

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(
                risk_reversal(underlying="BTC", expiry_ns=1, at=1, rate=0.0)
            )
    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Risk reversal query failed."
    assert "secret" not in str(exc_info.value.detail)


def test_risk_reversal_route_registered() -> None:
    """Ensure FastAPI route table includes GET /api/v1/risk-reversal."""
    paths = {
        (getattr(r, "path", None), tuple(sorted(getattr(r, "methods", set()) or [])))
        for r in app.routes
    }
    assert ("/api/v1/risk-reversal", ("GET",)) in paths


# ---------------------------------------------------------------------------
# GET /api/v1/liquidity-depth — per-block book depth (read-only, no payment)
# ---------------------------------------------------------------------------


def test_liquidity_depth_empty_symbol_skips_client() -> None:
    mock_client = MagicMock()
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import liquidity_depth

        result = asyncio.run(liquidity_depth(symbol=""))
        result_ws = asyncio.run(liquidity_depth(symbol="  "))
    assert result == []
    assert result_ws == []
    mock_client.calculate_block_liquidity_depth.assert_not_called()


def test_liquidity_depth_empty_lake(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CRYPCODILE_DATA_DIR", str(tmp_path))
    from crypcodile.api_server import liquidity_depth

    result = asyncio.run(liquidity_depth(symbol="base_onchain:DEGEN-WETH"))
    assert result == []


def test_liquidity_depth_empty_dataframe() -> None:
    mock_client = MagicMock()
    mock_client.calculate_block_liquidity_depth.return_value = pl.DataFrame()
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import liquidity_depth

        result = asyncio.run(liquidity_depth(symbol="base_onchain:DEGEN-WETH"))
    assert result == []
    mock_client.calculate_block_liquidity_depth.assert_called_once_with(
        "base_onchain:DEGEN-WETH"
    )


def test_liquidity_depth_returns_rows() -> None:
    mock_client = MagicMock()
    mock_client.calculate_block_liquidity_depth.return_value = pl.DataFrame(
        {
            "block": [100, 101],
            "bid_depth_1pct": [10.0, 11.0],
            "ask_depth_1pct": [12.0, 13.0],
            "bid_depth_2pct": [20.0, 21.0],
            "ask_depth_2pct": [22.0, 23.0],
            "bid_depth_5pct": [50.0, 51.0],
            "ask_depth_5pct": [52.0, 53.0],
        }
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import liquidity_depth

        result = asyncio.run(
            liquidity_depth(symbol="base_onchain:DEGEN-WETH", limit=100)
        )
    assert len(result) == 2
    assert result[0]["block"] == 100
    assert result[0]["bid_depth_1pct"] == 10.0
    assert result[1]["block"] == 101
    assert result[1]["ask_depth_5pct"] == 53.0
    mock_client.calculate_block_liquidity_depth.assert_called_once_with(
        "base_onchain:DEGEN-WETH"
    )


def test_liquidity_depth_strips_whitespace_symbol() -> None:
    mock_client = MagicMock()
    mock_client.calculate_block_liquidity_depth.return_value = pl.DataFrame()
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import liquidity_depth

        asyncio.run(liquidity_depth(symbol="  base_onchain:WETH-USDC  "))
    mock_client.calculate_block_liquidity_depth.assert_called_once_with(
        "base_onchain:WETH-USDC"
    )


def test_liquidity_depth_applies_limit() -> None:
    mock_client = MagicMock()
    mock_client.calculate_block_liquidity_depth.return_value = pl.DataFrame(
        {
            "block": [1, 2, 3, 4, 5],
            "bid_depth_1pct": [1.0] * 5,
            "ask_depth_1pct": [1.0] * 5,
            "bid_depth_2pct": [2.0] * 5,
            "ask_depth_2pct": [2.0] * 5,
            "bid_depth_5pct": [5.0] * 5,
            "ask_depth_5pct": [5.0] * 5,
        }
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import liquidity_depth

        result = asyncio.run(liquidity_depth(symbol="x", limit=2))
    assert len(result) == 2
    assert result[0]["block"] == 1
    assert result[1]["block"] == 2


def test_liquidity_depth_clamps_limit_max() -> None:
    mock_client = MagicMock()
    mock_client.calculate_block_liquidity_depth.return_value = pl.DataFrame(
        {
            "block": list(range(20)),
            "bid_depth_1pct": [1.0] * 20,
            "ask_depth_1pct": [1.0] * 20,
            "bid_depth_2pct": [2.0] * 20,
            "ask_depth_2pct": [2.0] * 20,
            "bid_depth_5pct": [5.0] * 20,
            "ask_depth_5pct": [5.0] * 20,
        }
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import _LIQUIDITY_DEPTH_MAX_LIMIT, liquidity_depth

        result = asyncio.run(
            liquidity_depth(
                symbol="x",
                limit=_LIQUIDITY_DEPTH_MAX_LIMIT + 5000,
            )
        )
    assert len(result) == 20
    mock_client.calculate_block_liquidity_depth.assert_called_once()


def test_liquidity_depth_clamps_limit_minimum() -> None:
    mock_client = MagicMock()
    mock_client.calculate_block_liquidity_depth.return_value = pl.DataFrame(
        {
            "block": [1, 2, 3],
            "bid_depth_1pct": [1.0, 2.0, 3.0],
            "ask_depth_1pct": [1.0, 2.0, 3.0],
            "bid_depth_2pct": [1.0, 2.0, 3.0],
            "ask_depth_2pct": [1.0, 2.0, 3.0],
            "bid_depth_5pct": [1.0, 2.0, 3.0],
            "ask_depth_5pct": [1.0, 2.0, 3.0],
        }
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import liquidity_depth

        result = asyncio.run(liquidity_depth(symbol="x", limit=0))
    assert len(result) == 1
    assert result[0]["block"] == 1


def test_liquidity_depth_query_error() -> None:
    mock_client = MagicMock()
    mock_client.calculate_block_liquidity_depth.side_effect = RuntimeError(
        "internal path /secret/lake"
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import liquidity_depth

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(liquidity_depth(symbol="base_onchain:DEGEN-WETH"))
    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Liquidity depth query failed."
    assert "secret" not in str(exc_info.value.detail)


def test_liquidity_depth_route_registered() -> None:
    """Ensure FastAPI route table includes GET /api/v1/liquidity-depth."""
    paths = {
        (getattr(r, "path", None), tuple(sorted(getattr(r, "methods", set()) or [])))
        for r in app.routes
    }
    assert ("/api/v1/liquidity-depth", ("GET",)) in paths


# ---------------------------------------------------------------------------
# GET /api/v1/sequencer-latency — production interval + ingest delay
# ---------------------------------------------------------------------------


def test_sequencer_latency_empty_lake(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CRYPCODILE_DATA_DIR", str(tmp_path))
    from crypcodile.api_server import sequencer_latency

    result = asyncio.run(sequencer_latency(exchange="base_onchain"))
    assert result == []


def test_sequencer_latency_empty_dataframe() -> None:
    mock_client = MagicMock()
    mock_client.calculate_sequencer_latency.return_value = pl.DataFrame()
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import sequencer_latency

        result = asyncio.run(sequencer_latency(exchange="base_onchain"))
    assert result == []
    mock_client.calculate_sequencer_latency.assert_called_once_with("base_onchain")


def test_sequencer_latency_returns_rows() -> None:
    mock_client = MagicMock()
    mock_client.calculate_sequencer_latency.return_value = pl.DataFrame(
        {
            "metric": ["production_interval", "ingestion_delay"],
            "avg_seconds": [2.0, 0.1],
            "max_seconds": [5.0, 0.5],
            "std_seconds": [0.5, 0.05],
        }
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import sequencer_latency

        result = asyncio.run(sequencer_latency(exchange="base_onchain", limit=100))
    assert len(result) == 2
    assert result[0]["metric"] == "production_interval"
    assert result[0]["avg_seconds"] == 2.0
    assert result[1]["metric"] == "ingestion_delay"
    assert result[1]["max_seconds"] == 0.5
    mock_client.calculate_sequencer_latency.assert_called_once_with("base_onchain")


def test_sequencer_latency_default_exchange_is_base_onchain() -> None:
    mock_client = MagicMock()
    mock_client.calculate_sequencer_latency.return_value = pl.DataFrame()
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import sequencer_latency

        asyncio.run(sequencer_latency(exchange=""))
        asyncio.run(sequencer_latency(exchange="  "))
    assert mock_client.calculate_sequencer_latency.call_args_list == [
        (("base_onchain",),),
        (("base_onchain",),),
    ]


def test_sequencer_latency_strips_whitespace_exchange() -> None:
    mock_client = MagicMock()
    mock_client.calculate_sequencer_latency.return_value = pl.DataFrame()
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import sequencer_latency

        asyncio.run(sequencer_latency(exchange="  optimism  "))
    mock_client.calculate_sequencer_latency.assert_called_once_with("optimism")


def test_sequencer_latency_applies_limit() -> None:
    mock_client = MagicMock()
    mock_client.calculate_sequencer_latency.return_value = pl.DataFrame(
        {
            "metric": ["a", "b", "c"],
            "avg_seconds": [1.0, 2.0, 3.0],
            "max_seconds": [1.0, 2.0, 3.0],
            "std_seconds": [0.0, 0.0, 0.0],
        }
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import sequencer_latency

        result = asyncio.run(sequencer_latency(exchange="base_onchain", limit=1))
    assert len(result) == 1
    assert result[0]["metric"] == "a"


def test_sequencer_latency_clamps_limit_max() -> None:
    mock_client = MagicMock()
    mock_client.calculate_sequencer_latency.return_value = pl.DataFrame(
        {
            "metric": [f"m{i}" for i in range(20)],
            "avg_seconds": [1.0] * 20,
            "max_seconds": [2.0] * 20,
            "std_seconds": [0.1] * 20,
        }
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import (
            _SEQUENCER_LATENCY_MAX_LIMIT,
            sequencer_latency,
        )

        result = asyncio.run(
            sequencer_latency(
                exchange="base_onchain",
                limit=_SEQUENCER_LATENCY_MAX_LIMIT + 5000,
            )
        )
    assert len(result) == 20
    mock_client.calculate_sequencer_latency.assert_called_once()


def test_sequencer_latency_clamps_limit_minimum() -> None:
    mock_client = MagicMock()
    mock_client.calculate_sequencer_latency.return_value = pl.DataFrame(
        {
            "metric": ["a", "b", "c"],
            "avg_seconds": [1.0, 2.0, 3.0],
            "max_seconds": [1.0, 2.0, 3.0],
            "std_seconds": [0.0, 0.0, 0.0],
        }
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import sequencer_latency

        result = asyncio.run(sequencer_latency(exchange="base_onchain", limit=0))
    assert len(result) == 1
    assert result[0]["metric"] == "a"


def test_sequencer_latency_query_error() -> None:
    mock_client = MagicMock()
    mock_client.calculate_sequencer_latency.side_effect = RuntimeError(
        "internal path /secret/lake"
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import sequencer_latency

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(sequencer_latency(exchange="base_onchain"))
    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Sequencer latency query failed."
    assert "secret" not in str(exc_info.value.detail)


def test_sequencer_latency_route_registered() -> None:
    """Ensure FastAPI route table includes GET /api/v1/sequencer-latency."""
    paths = {
        (getattr(r, "path", None), tuple(sorted(getattr(r, "methods", set()) or [])))
        for r in app.routes
    }
    assert ("/api/v1/sequencer-latency", ("GET",)) in paths


# ---------------------------------------------------------------------------
# GET /api/v1/chaos-score — pure risk score (no lake, no payment)
# ---------------------------------------------------------------------------


def test_chaos_score_zeros() -> None:
    from crypcodile.api_server import chaos_score

    result = asyncio.run(
        chaos_score(
            volatility=0.0,
            stablecoin_deviation=0.0,
            orderbook_imbalance=0.0,
            sequencer_delay=0.0,
        )
    )
    assert result["volatility"] == 0.0
    assert result["stablecoin_deviation"] == 0.0
    assert result["orderbook_imbalance"] == 0.0
    assert result["sequencer_delay"] == 0.0
    assert result["chaos_score"] == 0.0


def test_chaos_score_matches_analytics() -> None:
    from crypcodile.analytics.risk import calculate_chaos_score
    from crypcodile.api_server import chaos_score

    vol, dev, imb, delay = 0.05, 0.02, 0.5, 3.0
    expected = calculate_chaos_score(vol, dev, imb, delay)
    result = asyncio.run(
        chaos_score(
            volatility=vol,
            stablecoin_deviation=dev,
            orderbook_imbalance=imb,
            sequencer_delay=delay,
        )
    )
    assert result["volatility"] == vol
    assert result["stablecoin_deviation"] == dev
    assert result["orderbook_imbalance"] == imb
    assert result["sequencer_delay"] == delay
    assert result["chaos_score"] == expected
    assert 0.0 <= result["chaos_score"] <= 100.0


def test_chaos_score_high_risk_bounded() -> None:
    from crypcodile.api_server import chaos_score

    result = asyncio.run(
        chaos_score(
            volatility=1000.0,
            stablecoin_deviation=1000.0,
            orderbook_imbalance=1.0,
            sequencer_delay=1000.0,
        )
    )
    assert 0.0 <= result["chaos_score"] <= 100.0


def test_chaos_score_defaults() -> None:
    from crypcodile.api_server import chaos_score

    result = asyncio.run(chaos_score())
    assert result["chaos_score"] == 0.0


def test_chaos_score_route_registered() -> None:
    """Ensure FastAPI route table includes GET /api/v1/chaos-score."""
    paths = {
        (getattr(r, "path", None), tuple(sorted(getattr(r, "methods", set()) or [])))
        for r in app.routes
    }
    assert ("/api/v1/chaos-score", ("GET",)) in paths


def test_chaos_score_inf_input_json_safe_null() -> None:
    """±Inf volatility yields NaN in pure analytics; REST returns null (JSON-safe)."""
    import math

    from starlette.responses import JSONResponse

    from crypcodile.analytics.risk import calculate_chaos_score
    from crypcodile.api_server import chaos_score

    pure = calculate_chaos_score(float("inf"), 0.0, 0.0, 0.0)
    assert math.isnan(pure)

    result = asyncio.run(
        chaos_score(
            volatility=float("inf"),
            stablecoin_deviation=0.0,
            orderbook_imbalance=0.0,
            sequencer_delay=0.0,
        )
    )
    assert result["volatility"] is None
    assert result["chaos_score"] is None
    body = JSONResponse(result).body
    assert b"null" in body
    assert b"Infinity" not in body
    assert b"NaN" not in body


# ---------------------------------------------------------------------------
# GET /api/v1/peg-deviation — pure peg check (no lake, no payment)
# ---------------------------------------------------------------------------


def test_peg_deviation_alert() -> None:
    from crypcodile.api_server import peg_deviation

    result = asyncio.run(peg_deviation(price=0.98, threshold=0.01))
    assert result["price"] == 0.98
    assert abs(result["deviation_pct"] - 0.02) < 1e-12
    assert result["is_alert_triggered"] is True
    assert result["threshold"] == 0.01


def test_peg_deviation_ok() -> None:
    from crypcodile.api_server import peg_deviation

    result = asyncio.run(peg_deviation(price=1.0))
    assert result["price"] == 1.0
    assert result["deviation_pct"] == 0.0
    assert result["is_alert_triggered"] is False
    assert result["threshold"] == 0.01


def test_peg_deviation_custom_target() -> None:
    from crypcodile.api_server import peg_deviation

    result = asyncio.run(peg_deviation(price=1.05, threshold=0.02, target=1.0))
    assert result["is_alert_triggered"] is True
    result_ok = asyncio.run(peg_deviation(price=2.0, threshold=0.05, target=2.0))
    assert result_ok["is_alert_triggered"] is False
    assert result_ok["deviation_pct"] == 0.0


def test_peg_deviation_matches_analytics() -> None:
    from crypcodile.analytics.peg_deviation import peg_deviation_from_price
    from crypcodile.api_server import peg_deviation

    expected = peg_deviation_from_price(0.975, threshold=0.01, target=1.0)
    result = asyncio.run(peg_deviation(price=0.975, threshold=0.01, target=1.0))
    assert result == expected


def test_peg_deviation_default_threshold() -> None:
    from crypcodile.api_server import peg_deviation

    result = asyncio.run(peg_deviation(price=0.995))
    assert result["threshold"] == 0.01
    assert result["is_alert_triggered"] is False


def test_peg_deviation_route_registered() -> None:
    """Ensure FastAPI route table includes GET /api/v1/peg-deviation."""
    paths = {
        (getattr(r, "path", None), tuple(sorted(getattr(r, "methods", set()) or [])))
        for r in app.routes
    }
    assert ("/api/v1/peg-deviation", ("GET",)) in paths


def test_peg_deviation_inf_price_json_safe_null() -> None:
    """Inf price yields Inf deviation in pure analytics; REST returns null."""
    from starlette.responses import JSONResponse

    from crypcodile.analytics.peg_deviation import peg_deviation_from_price
    from crypcodile.api_server import peg_deviation

    pure = peg_deviation_from_price(float("inf"))
    assert pure["price"] == float("inf")
    assert pure["deviation_pct"] == float("inf")

    result = asyncio.run(peg_deviation(price=float("inf")))
    assert result["price"] is None
    assert result["deviation_pct"] is None
    assert result["is_alert_triggered"] is True
    body = JSONResponse(result).body
    assert b"null" in body
    assert b"Infinity" not in body


def test_peg_deviation_nan_price_json_safe_null() -> None:
    """NaN price yields NaN deviation; REST returns null."""
    import math

    from crypcodile.analytics.peg_deviation import peg_deviation_from_price
    from crypcodile.api_server import peg_deviation

    pure = peg_deviation_from_price(float("nan"))
    assert math.isnan(pure["price"])

    result = asyncio.run(peg_deviation(price=float("nan")))
    assert result["price"] is None
    assert result["deviation_pct"] is None


# ---------------------------------------------------------------------------
# GET /api/v1/lending-stress — pure LTV / health-factor stress (no lake, no payment)
# ---------------------------------------------------------------------------


def test_lending_stress_healthy() -> None:
    from crypcodile.api_server import lending_stress

    result = asyncio.run(
        lending_stress(
            collateral_usd=10_000.0,
            debt_usd=4_000.0,
            liquidation_threshold=0.8,
            haircut_pct=0.10,
        )
    )
    assert result["collateral_usd"] == 10_000.0
    assert result["debt_usd"] == 4_000.0
    assert result["liquidation_threshold"] == 0.8
    assert result["haircut_pct"] == 0.10
    assert result["current_health_factor"] == pytest.approx(2.0)
    assert result["simulated_health_factor"] == pytest.approx(1.8)
    assert result["is_liquidatable"] is False
    assert result["simulated_is_liquidatable"] is False


def test_lending_stress_liquidation() -> None:
    from crypcodile.api_server import lending_stress

    # Current HF = (10000 * 0.8) / 9000 ≈ 0.889 → liquidatable now
    result = asyncio.run(
        lending_stress(
            collateral_usd=10_000.0,
            debt_usd=9_000.0,
            liquidation_threshold=0.8,
            haircut_pct=10.0,
        )
    )
    assert result["current_health_factor"] < 1.0
    assert result["is_liquidatable"] is True
    assert result["simulated_is_liquidatable"] is True


def test_lending_stress_zero_debt_json_safe_null() -> None:
    """Zero debt yields inf in pure analytics; REST returns null (JSON-safe)."""
    from starlette.responses import JSONResponse

    from crypcodile.analytics.lending_stress import lending_stress_test
    from crypcodile.api_server import lending_stress

    pure = lending_stress_test(
        collateral_usd=5_000.0,
        debt_usd=0.0,
        liquidation_threshold=0.8,
        haircut_pct=0.20,
    )
    assert pure["current_health_factor"] == float("inf")
    assert pure["simulated_health_factor"] == float("inf")

    result = asyncio.run(
        lending_stress(
            collateral_usd=5_000.0,
            debt_usd=0.0,
            liquidation_threshold=0.8,
            haircut_pct=0.20,
        )
    )
    # HTTP boundary must not return non-finite floats (Starlette JSONResponse
    # raises ValueError: Out of range float values are not JSON compliant).
    assert result["current_health_factor"] is None
    assert result["simulated_health_factor"] is None
    assert result["is_liquidatable"] is False
    assert result["simulated_is_liquidatable"] is False
    # Prove the payload is actually JSON-encodable.
    body = JSONResponse(result).body
    assert b"null" in body
    assert b"Infinity" not in body


def test_lending_stress_matches_analytics() -> None:
    from crypcodile.analytics.lending_stress import lending_stress_test
    from crypcodile.api_server import lending_stress

    kwargs = {
        "collateral_usd": 12_500.0,
        "debt_usd": 3_000.0,
        "liquidation_threshold": 0.75,
        "haircut_pct": 20.0,
    }
    result = asyncio.run(lending_stress(**kwargs))
    expected = lending_stress_test(**kwargs)
    assert result["current_health_factor"] == expected["current_health_factor"]
    assert result["simulated_health_factor"] == expected["simulated_health_factor"]
    assert result["is_liquidatable"] == expected["is_liquidatable"]
    assert result["simulated_is_liquidatable"] == expected["simulated_is_liquidatable"]
    assert result["collateral_usd"] == kwargs["collateral_usd"]
    assert result["debt_usd"] == kwargs["debt_usd"]
    assert result["liquidation_threshold"] == kwargs["liquidation_threshold"]
    assert result["haircut_pct"] == kwargs["haircut_pct"]


def test_lending_stress_percent_vs_fraction_haircut() -> None:
    """Haircut 20 and 0.20 must yield the same stress metrics (CLI parity)."""
    from crypcodile.api_server import lending_stress

    a = asyncio.run(
        lending_stress(
            collateral_usd=10_000.0,
            debt_usd=5_000.0,
            liquidation_threshold=0.8,
            haircut_pct=20.0,
        )
    )
    b = asyncio.run(
        lending_stress(
            collateral_usd=10_000.0,
            debt_usd=5_000.0,
            liquidation_threshold=0.8,
            haircut_pct=0.20,
        )
    )
    assert a["simulated_health_factor"] == b["simulated_health_factor"]
    assert a["current_health_factor"] == b["current_health_factor"]


def test_lending_stress_defaults() -> None:
    from crypcodile.api_server import lending_stress

    result = asyncio.run(lending_stress())
    assert result["collateral_usd"] == 0.0
    assert result["debt_usd"] == 0.0
    assert result["liquidation_threshold"] == 0.0
    assert result["haircut_pct"] == 0.0
    # Defaults imply zero debt → JSON-safe null health factors
    assert result["current_health_factor"] is None
    assert result["simulated_health_factor"] is None
    assert result["is_liquidatable"] is False
    assert result["simulated_is_liquidatable"] is False


def test_lending_stress_route_registered() -> None:
    """Ensure FastAPI route table includes GET /api/v1/lending-stress."""
    paths = {
        (getattr(r, "path", None), tuple(sorted(getattr(r, "methods", set()) or [])))
        for r in app.routes
    }
    assert ("/api/v1/lending-stress", ("GET",)) in paths


def test_json_safe_float_maps_non_finite_to_none() -> None:
    """Helper used by pure REST float fields for JSON encoding."""
    from crypcodile.api_server import _json_safe_float

    assert _json_safe_float(1.5) == 1.5
    assert _json_safe_float(0.0) == 0.0
    assert _json_safe_float(-2.25) == -2.25
    assert _json_safe_float(float("inf")) is None
    assert _json_safe_float(float("-inf")) is None
    assert _json_safe_float(float("nan")) is None


def test_json_safe_records_sanitizes_float_fields() -> None:
    """Lake DF rows: NaN/±Inf floats → None; ints/str/None unchanged."""
    from crypcodile.api_server import _json_safe_records

    rows = [
        {
            "local_ts": 100,
            "symbol": "deribit:BTC-PERPETUAL",
            "ofi": float("inf"),
            "apr": float("nan"),
            "basis_pct": float("-inf"),
            "total_oi": 1500.0,
            "note": None,
        },
        {
            "local_ts": 200,
            "symbol": "x",
            "ofi": 1.25,
            "apr": 0.0,
            "basis_pct": -0.5,
            "total_oi": 0.0,
            "note": "ok",
        },
    ]
    out = _json_safe_records(rows)
    assert out[0]["local_ts"] == 100
    assert out[0]["symbol"] == "deribit:BTC-PERPETUAL"
    assert out[0]["ofi"] is None
    assert out[0]["apr"] is None
    assert out[0]["basis_pct"] is None
    assert out[0]["total_oi"] == 1500.0
    assert out[0]["note"] is None
    assert out[1]["ofi"] == 1.25
    assert out[1]["apr"] == 0.0
    assert out[1]["basis_pct"] == -0.5
    assert out[1]["total_oi"] == 0.0
    assert out[1]["note"] == "ok"
    # Empty input stays empty.
    assert _json_safe_records([]) == []


def test_open_interest_non_finite_floats_json_safe_null() -> None:
    """OI aggregation may yield inf/nan; REST maps them to JSON null."""
    mock_client = MagicMock()
    mock_client.aggregate_open_interest.return_value = pl.DataFrame(
        {
            "local_ts": [100],
            "binance": [float("inf")],
            "bybit": [float("nan")],
            "total_oi": [float("-inf")],
        }
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import open_interest

        result = asyncio.run(
            open_interest(symbols="BTC", start=0, end=1000, limit=100)
        )
    assert len(result) == 1
    assert result[0]["local_ts"] == 100
    assert result[0]["binance"] is None
    assert result[0]["bybit"] is None
    assert result[0]["total_oi"] is None


def test_funding_apr_non_finite_floats_json_safe_null() -> None:
    """Funding APR head rows may contain nan/inf from edge rates."""
    mock_client = MagicMock()
    mock_client.funding_apr.return_value = pl.DataFrame(
        {
            "funding_ts": [100],
            "funding_rate": [0.0001],
            "interval_hours": [8.0],
            "apr": [float("inf")],
            "cumulative_funding": [float("nan")],
        }
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import funding_apr

        result = asyncio.run(
            funding_apr(
                symbol="deribit:BTC-PERPETUAL",
                start=0,
                end=1000,
                limit=100,
            )
        )
    assert len(result) == 1
    assert result[0]["funding_ts"] == 100
    assert result[0]["funding_rate"] == 0.0001
    assert result[0]["apr"] is None
    assert result[0]["cumulative_funding"] is None


def test_basis_non_finite_floats_json_safe_null() -> None:
    """Spot-perp basis rows may contain nan/inf from zero-price joins."""
    mock_client = MagicMock()
    mock_client.spot_perp_basis.return_value = pl.DataFrame(
        {
            "local_ts": [100],
            "spot_price": [0.0],
            "perp_price": [100.0],
            "basis": [100.0],
            "basis_pct": [float("inf")],
        }
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import basis

        result = asyncio.run(
            basis(
                spot="deribit:BTC-SPOT",
                perp="deribit:BTC-PERPETUAL",
                start=0,
                end=1000,
                limit=100,
            )
        )
    assert len(result) == 1
    assert result[0]["local_ts"] == 100
    assert result[0]["basis"] == 100.0
    assert result[0]["basis_pct"] is None


def test_ofi_non_finite_floats_json_safe_null() -> None:
    """OFI bins may contain nan/inf from empty-book edge cases."""
    mock_client = MagicMock()
    mock_client.calculate_ofi.return_value = pl.DataFrame(
        {
            "local_ts": [100],
            "ofi": [float("nan")],
            "bid_size": [float("inf")],
            "ask_size": [1.0],
        }
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import ofi

        result = asyncio.run(
            ofi(
                symbol="deribit:BTC-PERPETUAL",
                start=0,
                end=1000,
                interval="1m",
                limit=100,
            )
        )
    assert len(result) == 1
    assert result[0]["local_ts"] == 100
    assert result[0]["ofi"] is None
    assert result[0]["bid_size"] is None
    assert result[0]["ask_size"] == 1.0


def test_iv_surface_non_finite_floats_json_safe_null() -> None:
    """IV surface rows may contain nan/inf from deep OTM / zero-price edges."""
    mock_client = MagicMock()
    mock_client.iv_surface.return_value = pl.DataFrame(
        {
            "expiry": [1_700_000_000_000_000_000],
            "strike": [50000.0],
            "iv": [float("inf")],
            "delta": [float("nan")],
            "mark_price": [0.01],
        }
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import iv_surface

        result = asyncio.run(
            iv_surface(underlying="BTC", at=1_700_000_000_000_000_000, limit=100)
        )
    assert len(result) == 1
    assert result[0]["strike"] == 50000.0
    assert result[0]["iv"] is None
    assert result[0]["delta"] is None
    assert result[0]["mark_price"] == 0.01


def test_whale_alerts_non_finite_floats_json_safe_null() -> None:
    """Whale alert notionals may contain nan/inf from zero-price trades."""
    mock_client = MagicMock()
    mock_client.track_whale_alerts.return_value = pl.DataFrame(
        {
            "local_ts": [100],
            "symbol": ["deribit:BTC-PERPETUAL"],
            "price": [0.0],
            "amount": [10.0],
            "notional_usd": [float("nan")],
            "side": ["buy"],
        }
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import whale_alerts

        result = asyncio.run(
            whale_alerts(
                symbol="deribit:BTC-PERPETUAL",
                start=0,
                end=1000,
                min_usd=0.0,
                limit=100,
            )
        )
    assert len(result) == 1
    assert result[0]["local_ts"] == 100
    assert result[0]["amount"] == 10.0
    assert result[0]["notional_usd"] is None
    assert result[0]["side"] == "buy"


def test_indicators_non_finite_floats_json_safe_null() -> None:
    """Indicator series may contain nan/inf during warmup / zero-volume bars."""
    mock_client = MagicMock()
    mock_client.get_indicators.return_value = pl.DataFrame(
        {
            "local_ts": [100],
            "rsi": [float("nan")],
            "sma": [float("inf")],
            "close": [42000.0],
        }
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import indicators

        result = asyncio.run(
            indicators(
                symbol="deribit:BTC-PERPETUAL",
                start=0,
                end=1000,
                indicator="rsi",
                limit=100,
            )
        )
    assert len(result) == 1
    assert result[0]["local_ts"] == 100
    assert result[0]["rsi"] is None
    assert result[0]["sma"] is None
    assert result[0]["close"] == 42000.0


# ---------------------------------------------------------------------------
# GET /api/v1/funding-predict — pure offline next-period funding (no lake)
# ---------------------------------------------------------------------------


def test_funding_predict_basic() -> None:
    from crypcodile.api_server import funding_predict

    result = asyncio.run(funding_predict(rates="0.1,0.2,0.3", window_size=5))
    assert result["n_history"] == 3
    assert result["window_size"] == 5
    assert result["method"] in ("rolling_mean", "xgboost")
    assert isinstance(result["predicted_funding_rate"], float)
    assert isinstance(result["xgboost_available"], bool)
    if result["method"] == "rolling_mean":
        # Full history is shorter than window → mean of all three rates.
        assert abs(result["predicted_funding_rate"] - 0.2) < 1e-9


def test_funding_predict_custom_window() -> None:
    from crypcodile.api_server import funding_predict

    result = asyncio.run(funding_predict(rates="0.1,0.2,0.3", window_size=2))
    assert result["window_size"] == 2
    assert result["n_history"] == 3
    if result["method"] == "rolling_mean":
        # Last 2 rates: (0.2 + 0.3) / 2 = 0.25
        assert abs(result["predicted_funding_rate"] - 0.25) < 1e-9


def test_funding_predict_matches_analytics() -> None:
    from crypcodile.analytics.funding_prediction import predict_next_funding
    from crypcodile.api_server import funding_predict

    rates = [0.01, 0.02, 0.03, 0.04]
    expected = predict_next_funding(rates, window_size=3)
    result = asyncio.run(
        funding_predict(rates="0.01,0.02,0.03,0.04", window_size=3)
    )
    assert result["n_history"] == expected["n_history"]
    assert result["window_size"] == expected["window_size"]
    assert result["method"] == expected["method"]
    assert result["xgboost_available"] == expected["xgboost_available"]
    assert abs(
        result["predicted_funding_rate"] - expected["predicted_funding_rate"]
    ) < 1e-12


def test_funding_predict_whitespace_and_trailing_commas() -> None:
    from crypcodile.api_server import funding_predict

    result = asyncio.run(funding_predict(rates=" 0.1 , 0.2 , 0.3 ,", window_size=3))
    assert result["n_history"] == 3
    if result["method"] == "rolling_mean":
        assert abs(result["predicted_funding_rate"] - 0.2) < 1e-9


def test_funding_predict_empty_rates_400() -> None:
    from crypcodile.api_server import funding_predict

    with pytest.raises(HTTPException) as ei:
        asyncio.run(funding_predict(rates=""))
    assert ei.value.status_code == 400
    assert "rates" in str(ei.value.detail).lower()

    with pytest.raises(HTTPException) as ei2:
        asyncio.run(funding_predict(rates="  , , "))
    assert ei2.value.status_code == 400


def test_funding_predict_invalid_token_400() -> None:
    from crypcodile.api_server import funding_predict

    with pytest.raises(HTTPException) as ei:
        asyncio.run(funding_predict(rates="0.1,abc,0.3"))
    assert ei.value.status_code == 400
    assert "invalid" in str(ei.value.detail).lower()


def test_funding_predict_invalid_window_400() -> None:
    from crypcodile.api_server import funding_predict

    with pytest.raises(HTTPException) as ei:
        asyncio.run(funding_predict(rates="0.1,0.2", window_size=0))
    assert ei.value.status_code == 400
    assert "window" in str(ei.value.detail).lower()


def test_funding_predict_default_window() -> None:
    from crypcodile.api_server import funding_predict

    result = asyncio.run(funding_predict(rates="0.01,0.02,0.03"))
    assert result["window_size"] == 5


def test_funding_predict_route_registered() -> None:
    """Ensure FastAPI route table includes GET /api/v1/funding-predict."""
    paths = {
        (getattr(r, "path", None), tuple(sorted(getattr(r, "methods", set()) or [])))
        for r in app.routes
    }
    assert ("/api/v1/funding-predict", ("GET",)) in paths


def test_funding_predict_inf_rates_json_safe_null() -> None:
    """Inf rates can yield Inf prediction; REST returns null (JSON-safe)."""
    from starlette.responses import JSONResponse

    from crypcodile.analytics.funding_prediction import predict_next_funding
    from crypcodile.api_server import funding_predict

    pure = predict_next_funding([float("inf"), 0.1, 0.2], window_size=3)
    assert pure["predicted_funding_rate"] == float("inf") or pure[
        "predicted_funding_rate"
    ] != pure["predicted_funding_rate"]  # Inf or NaN

    result = asyncio.run(
        funding_predict(rates="inf,0.1,0.2", window_size=3)
    )
    assert result["predicted_funding_rate"] is None
    assert result["n_history"] == 3
    body = JSONResponse(result).body
    assert b"null" in body
    assert b"Infinity" not in body
    assert b"NaN" not in body


# ---------------------------------------------------------------------------
# POST /api/v1/gas-vol — pure offline gas/vol correlation (no lake, no payment)
# ---------------------------------------------------------------------------


def test_gas_vol_perfect_correlation() -> None:
    from crypcodile.api_server import GasVolPayload, gas_vol

    payload = GasVolPayload(
        gas=[
            {"local_ts": 1, "gas": 10.0},
            {"local_ts": 2, "gas": 20.0},
            {"local_ts": 3, "gas": 30.0},
            {"local_ts": 4, "gas": 40.0},
            {"local_ts": 5, "gas": 50.0},
        ],
        vol=[
            {"local_ts": 1, "vol": 0.1},
            {"local_ts": 2, "vol": 0.2},
            {"local_ts": 3, "vol": 0.3},
            {"local_ts": 4, "vol": 0.4},
            {"local_ts": 5, "vol": 0.5},
        ],
    )
    result = asyncio.run(gas_vol(payload))
    assert result["n_gas"] == 5
    assert result["n_vol"] == 5
    assert result["pearson"] == pytest.approx(1.0, abs=1e-7)
    assert result["spearman"] == pytest.approx(1.0, abs=1e-7)


def test_gas_vol_column_aliases() -> None:
    """gas_price / volatility column names are accepted (analytics parity)."""
    from crypcodile.api_server import GasVolPayload, gas_vol

    payload = GasVolPayload(
        gas=[{"local_ts": i, "gas_price": float(i * 10)} for i in range(1, 6)],
        vol=[{"local_ts": i, "volatility": float(i) * 0.1} for i in range(1, 6)],
    )
    result = asyncio.run(gas_vol(payload))
    assert result["pearson"] == pytest.approx(1.0, abs=1e-7)
    assert result["spearman"] == pytest.approx(1.0, abs=1e-7)


def test_gas_vol_empty_series_null_corr() -> None:
    from crypcodile.api_server import GasVolPayload, gas_vol

    result = asyncio.run(gas_vol(GasVolPayload(gas=[], vol=[])))
    assert result["pearson"] is None
    assert result["spearman"] is None
    assert result["n_gas"] == 0
    assert result["n_vol"] == 0


def test_gas_vol_constant_series_null_corr_json_safe() -> None:
    """Constant gas vs varying vol → NaN correlations; REST returns null."""
    import math

    from starlette.responses import JSONResponse

    from crypcodile.analytics.gas_vol_correlation import gas_to_volatility_correlation
    from crypcodile.api_server import GasVolPayload, gas_vol

    gas_rows = [{"local_ts": i, "gas": 1.0} for i in range(1, 4)]
    vol_rows = [{"local_ts": i, "vol": float(i) * 0.1} for i in range(1, 4)]
    pure = gas_to_volatility_correlation(
        pl.DataFrame(gas_rows), pl.DataFrame(vol_rows)
    )
    assert math.isnan(pure["pearson"])
    assert math.isnan(pure["spearman"])

    result = asyncio.run(gas_vol(GasVolPayload(gas=gas_rows, vol=vol_rows)))
    assert result["pearson"] is None
    assert result["spearman"] is None
    body = JSONResponse(result).body
    assert b"null" in body
    assert b"NaN" not in body
    assert b"Infinity" not in body


def test_gas_vol_insufficient_data_null_corr() -> None:
    from crypcodile.api_server import GasVolPayload, gas_vol

    payload = GasVolPayload(
        gas=[{"local_ts": 1, "gas": 10.0}],
        vol=[{"local_ts": 2, "vol": 0.2}, {"local_ts": 3, "vol": 0.3}],
    )
    result = asyncio.run(gas_vol(payload))
    assert result["pearson"] is None
    assert result["spearman"] is None
    assert result["n_gas"] == 1
    assert result["n_vol"] == 2


def test_gas_vol_matches_analytics() -> None:
    from crypcodile.analytics.gas_vol_correlation import gas_to_volatility_correlation
    from crypcodile.api_server import GasVolPayload, gas_vol

    gas_rows = [
        {"local_ts": 100, "gas": 10.0},
        {"local_ts": 200, "gas": 20.0},
        {"local_ts": 300, "gas": 30.0},
        {"local_ts": 400, "gas": 40.0},
        {"local_ts": 500, "gas": 50.0},
    ]
    vol_rows = [
        {"local_ts": 101, "vol": 0.1},
        {"local_ts": 199, "vol": 0.2},
        {"local_ts": 302, "vol": 0.3},
        {"local_ts": 398, "vol": 0.4},
        {"local_ts": 505, "vol": 0.5},
    ]
    expected = gas_to_volatility_correlation(
        pl.DataFrame(gas_rows),
        pl.DataFrame(vol_rows),
    )
    result = asyncio.run(gas_vol(GasVolPayload(gas=gas_rows, vol=vol_rows)))
    assert result["pearson"] == pytest.approx(expected["pearson"], abs=1e-12)
    assert result["spearman"] == pytest.approx(expected["spearman"], abs=1e-12)
    assert result["n_gas"] == 5
    assert result["n_vol"] == 5


def test_gas_vol_missing_local_ts_400() -> None:
    from crypcodile.api_server import GasVolPayload, gas_vol

    payload = GasVolPayload(
        gas=[{"gas": 10.0}, {"local_ts": 2, "gas": 20.0}],
        vol=[{"local_ts": 1, "vol": 0.1}],
    )
    with pytest.raises(HTTPException) as ei:
        asyncio.run(gas_vol(payload))
    assert ei.value.status_code == 400
    assert "local_ts" in str(ei.value.detail)


def test_gas_vol_route_via_mock_client() -> None:
    resp = client.post(
        "/api/v1/gas-vol",
        json={
            "gas": [
                {"local_ts": 1, "gas": 1.0},
                {"local_ts": 2, "gas": 2.0},
                {"local_ts": 3, "gas": 3.0},
            ],
            "vol": [
                {"local_ts": 1, "vol": 2.0},
                {"local_ts": 2, "vol": 4.0},
                {"local_ts": 3, "vol": 6.0},
            ],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["n_gas"] == 3
    assert data["n_vol"] == 3
    assert data["pearson"] == pytest.approx(1.0, abs=1e-7)
    assert data["spearman"] == pytest.approx(1.0, abs=1e-7)


def test_gas_vol_route_missing_body_fields_422() -> None:
    resp = client.post("/api/v1/gas-vol", json={})
    assert resp.status_code == 422


def test_gas_vol_route_registered() -> None:
    """Ensure FastAPI route table includes POST /api/v1/gas-vol."""
    paths = {
        (getattr(r, "path", None), tuple(sorted(getattr(r, "methods", set()) or [])))
        for r in app.routes
    }
    assert ("/api/v1/gas-vol", ("POST",)) in paths


# ---------------------------------------------------------------------------
# POST /api/v1/mev-sandwich — pure offline sandwich flags (no lake, no payment)
# ---------------------------------------------------------------------------

_MEV_SANDWICH_TRADES = [
    {
        "block": 100,
        "pool": "AERO-USDC",
        "log_index": 10,
        "sender": "0xattacker",
        "is_buy": True,
    },
    {
        "block": 100,
        "pool": "AERO-USDC",
        "log_index": 11,
        "sender": "0xvictim",
        "is_buy": True,
    },
    {
        "block": 100,
        "pool": "AERO-USDC",
        "log_index": 12,
        "sender": "0xattacker",
        "is_buy": False,
    },
    {
        "block": 100,
        "pool": "AERO-USDC",
        "log_index": 13,
        "sender": "0xnormal",
        "is_buy": True,
    },
]


def test_mev_sandwich_detects_positive() -> None:
    from crypcodile.api_server import MevSandwichPayload, mev_sandwich

    rows = asyncio.run(mev_sandwich(MevSandwichPayload(trades=_MEV_SANDWICH_TRADES)))
    assert len(rows) == 4
    assert [r["is_sandwich"] for r in rows] == [True, True, True, False]


def test_mev_sandwich_empty_trades() -> None:
    from crypcodile.api_server import MevSandwichPayload, mev_sandwich

    assert asyncio.run(mev_sandwich(MevSandwichPayload(trades=[]))) == []


def test_mev_sandwich_no_pattern_across_blocks() -> None:
    from crypcodile.api_server import MevSandwichPayload, mev_sandwich

    trades = [
        {
            "block": 100,
            "pool": "AERO-USDC",
            "log_index": 10,
            "sender": "0xattacker",
            "is_buy": True,
        },
        {
            "block": 101,
            "pool": "AERO-USDC",
            "log_index": 11,
            "sender": "0xvictim",
            "is_buy": True,
        },
        {
            "block": 102,
            "pool": "AERO-USDC",
            "log_index": 12,
            "sender": "0xattacker",
            "is_buy": False,
        },
    ]
    rows = asyncio.run(mev_sandwich(MevSandwichPayload(trades=trades)))
    assert len(rows) == 3
    assert not any(r["is_sandwich"] for r in rows)


def test_mev_sandwich_matches_analytics() -> None:
    from crypcodile.analytics.mev_sandwich import detect_sandwiches
    from crypcodile.api_server import MevSandwichPayload, mev_sandwich

    expected = detect_sandwiches(pl.DataFrame(_MEV_SANDWICH_TRADES)).to_dicts()
    rows = asyncio.run(mev_sandwich(MevSandwichPayload(trades=_MEV_SANDWICH_TRADES)))
    assert rows == expected


def test_mev_sandwich_missing_cols_400() -> None:
    from crypcodile.api_server import MevSandwichPayload, mev_sandwich

    with pytest.raises(HTTPException) as ei:
        asyncio.run(mev_sandwich(MevSandwichPayload(trades=[{"block": 1}])))
    assert ei.value.status_code == 400
    assert "missing required columns" in str(ei.value.detail)


def test_mev_sandwich_route_via_mock_client() -> None:
    resp = client.post("/api/v1/mev-sandwich", json={"trades": _MEV_SANDWICH_TRADES})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 4
    assert [r["is_sandwich"] for r in data] == [True, True, True, False]


def test_mev_sandwich_route_missing_body_422() -> None:
    resp = client.post("/api/v1/mev-sandwich", json={})
    assert resp.status_code == 422


def test_mev_sandwich_route_registered() -> None:
    """Ensure FastAPI route table includes POST /api/v1/mev-sandwich."""
    paths = {
        (getattr(r, "path", None), tuple(sorted(getattr(r, "methods", set()) or [])))
        for r in app.routes
    }
    assert ("/api/v1/mev-sandwich", ("POST",)) in paths


# ---------------------------------------------------------------------------
# POST /api/v1/smart-money — pure offline transfers+watchlist (no lake)
# ---------------------------------------------------------------------------

_SMART_ADDR = "0xAb5801a7D398351b8bE11C439e05C5B3259aeC9B"
_SMART_OTHER = "0x1111111111111111111111111111111111111111"
_SMART_TRANSFERS = [
    {
        "from": _SMART_ADDR,
        "to": _SMART_OTHER,
        "usd_value": 100.0,
        "timestamp": 1,
    },
    {
        "from": _SMART_OTHER,
        "to": _SMART_ADDR,
        "usd_value": 40.0,
        "timestamp": 2,
    },
]


def test_smart_money_with_labels() -> None:
    from crypcodile.api_server import SmartMoneyPayload, smart_money

    rows = asyncio.run(
        smart_money(
            SmartMoneyPayload(
                transfers=_SMART_TRANSFERS,
                watchlist={_SMART_ADDR: "vitalik"},
            )
        )
    )
    assert len(rows) == 1
    assert rows[0]["net_flow_usd"] == -60.0
    assert rows[0]["total_volume_usd"] == 140.0
    assert rows[0]["tx_count"] == 2
    assert rows[0]["label"] == "vitalik"
    assert rows[0]["last_active_ts"] == 2


def test_smart_money_list_watchlist() -> None:
    from crypcodile.api_server import SmartMoneyPayload, smart_money

    rows = asyncio.run(
        smart_money(
            SmartMoneyPayload(transfers=_SMART_TRANSFERS, watchlist=[_SMART_ADDR])
        )
    )
    assert len(rows) == 1
    assert rows[0]["net_flow_usd"] == -60.0
    assert rows[0]["label"] == _SMART_ADDR


def test_smart_money_nested_watchlist() -> None:
    from crypcodile.api_server import SmartMoneyPayload, smart_money

    rows = asyncio.run(
        smart_money(
            SmartMoneyPayload(
                transfers=_SMART_TRANSFERS,
                watchlist={"watchlist": {_SMART_ADDR: "mev-bot"}},
            )
        )
    )
    assert len(rows) == 1
    assert rows[0]["label"] == "mev-bot"


def test_smart_money_empty_watchlist() -> None:
    from crypcodile.api_server import SmartMoneyPayload, smart_money

    assert (
        asyncio.run(
            smart_money(SmartMoneyPayload(transfers=_SMART_TRANSFERS, watchlist={}))
        )
        == []
    )
    assert (
        asyncio.run(
            smart_money(SmartMoneyPayload(transfers=_SMART_TRANSFERS, watchlist=[]))
        )
        == []
    )


def test_smart_money_empty_transfers() -> None:
    from crypcodile.api_server import SmartMoneyPayload, smart_money

    assert (
        asyncio.run(
            smart_money(
                SmartMoneyPayload(
                    transfers=[],
                    watchlist={_SMART_ADDR: "x"},
                )
            )
        )
        == []
    )


def test_smart_money_matches_analytics() -> None:
    from crypcodile.analytics.smart_money import summarize_smart_money
    from crypcodile.api_server import SmartMoneyPayload, smart_money

    watchlist = {_SMART_ADDR: "vitalik"}
    expected = summarize_smart_money(_SMART_TRANSFERS, watchlist)
    rows = asyncio.run(
        smart_money(
            SmartMoneyPayload(transfers=_SMART_TRANSFERS, watchlist=watchlist)
        )
    )
    assert rows == expected


def test_smart_money_route_via_mock_client() -> None:
    resp = client.post(
        "/api/v1/smart-money",
        json={
            "transfers": _SMART_TRANSFERS,
            "watchlist": {_SMART_ADDR: "vitalik"},
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["net_flow_usd"] == -60.0
    assert data[0]["label"] == "vitalik"


def test_smart_money_route_missing_body_422() -> None:
    resp = client.post("/api/v1/smart-money", json={})
    assert resp.status_code == 422


def test_smart_money_route_missing_watchlist_422() -> None:
    resp = client.post(
        "/api/v1/smart-money",
        json={"transfers": _SMART_TRANSFERS},
    )
    assert resp.status_code == 422


def test_smart_money_route_registered() -> None:
    """Ensure FastAPI route table includes POST /api/v1/smart-money."""
    paths = {
        (getattr(r, "path", None), tuple(sorted(getattr(r, "methods", set()) or [])))
        for r in app.routes
    }
    assert ("/api/v1/smart-money", ("POST",)) in paths


def test_smart_money_non_finite_floats_json_safe_null() -> None:
    """Non-finite flow metrics (e.g. Inf usd_value) → JSON null on REST."""
    from crypcodile.api_server import SmartMoneyPayload, smart_money

    # Single Inf outflow: net_flow_usd = -inf, total_volume_usd = +inf.
    transfers = [
        {
            "from": _SMART_ADDR,
            "to": _SMART_OTHER,
            "usd_value": float("inf"),
            "timestamp": 1,
        },
    ]
    rows = asyncio.run(
        smart_money(
            SmartMoneyPayload(
                transfers=transfers,
                watchlist={_SMART_ADDR: "vitalik"},
            )
        )
    )
    assert len(rows) == 1
    assert rows[0]["net_flow_usd"] is None
    assert rows[0]["total_volume_usd"] is None
    assert rows[0]["tx_count"] == 1
    assert rows[0]["label"] == "vitalik"
    # MockTestClient path also encodes without raising.
    resp = client.post(
        "/api/v1/smart-money",
        json={
            "transfers": transfers,
            "watchlist": {_SMART_ADDR: "vitalik"},
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["net_flow_usd"] is None
    assert body[0]["total_volume_usd"] is None


# ---------------------------------------------------------------------------
# POST /api/v1/label-transfers — pure offline label + filter (no lake)
# ---------------------------------------------------------------------------


def test_label_transfers_basic() -> None:
    from crypcodile.api_server import LabelTransfersPayload, label_transfers

    rows = asyncio.run(
        label_transfers(
            LabelTransfersPayload(
                transfers=_SMART_TRANSFERS,
                watchlist={_SMART_ADDR: "vitalik"},
            )
        )
    )
    assert len(rows) == 2
    assert rows[0]["from_label"] == "vitalik"
    assert rows[0]["to_label"] == ""
    assert rows[0]["is_known"] is True
    assert rows[1]["from_label"] == ""
    assert rows[1]["to_label"] == "vitalik"
    assert rows[1]["is_known"] is True


def test_label_transfers_known_only() -> None:
    from crypcodile.api_server import LabelTransfersPayload, label_transfers

    rows = asyncio.run(
        label_transfers(
            LabelTransfersPayload(
                transfers=[
                    {"from": _SMART_ADDR, "to": _SMART_OTHER, "usd_value": 1},
                    {"from": _SMART_OTHER, "to": _SMART_OTHER, "usd_value": 2},
                ],
                watchlist={_SMART_ADDR: "vitalik"},
                known_only=True,
            )
        )
    )
    assert len(rows) == 1
    assert rows[0]["from_label"] == "vitalik"
    assert rows[0]["is_known"] is True


def test_label_transfers_min_usd() -> None:
    from crypcodile.api_server import LabelTransfersPayload, label_transfers

    rows = asyncio.run(
        label_transfers(
            LabelTransfersPayload(
                transfers=_SMART_TRANSFERS,
                watchlist={_SMART_ADDR: "vitalik"},
                min_usd=50.0,
            )
        )
    )
    assert len(rows) == 1
    assert rows[0]["usd_value"] == 100.0
    assert rows[0]["from_label"] == "vitalik"


def test_label_transfers_list_watchlist() -> None:
    from crypcodile.api_server import LabelTransfersPayload, label_transfers

    rows = asyncio.run(
        label_transfers(
            LabelTransfersPayload(
                transfers=_SMART_TRANSFERS,
                watchlist=[_SMART_ADDR],
            )
        )
    )
    # List watchlist uses the address string itself as the label.
    assert rows[0]["from_label"] == _SMART_ADDR
    assert rows[0]["is_known"] is True


def test_label_transfers_nested_watchlist() -> None:
    from crypcodile.api_server import LabelTransfersPayload, label_transfers

    rows = asyncio.run(
        label_transfers(
            LabelTransfersPayload(
                transfers=_SMART_TRANSFERS,
                watchlist={"watchlist": {_SMART_ADDR: "mev-bot"}},
            )
        )
    )
    assert rows[0]["from_label"] == "mev-bot"


def test_label_transfers_empty_transfers() -> None:
    from crypcodile.api_server import LabelTransfersPayload, label_transfers

    assert (
        asyncio.run(
            label_transfers(
                LabelTransfersPayload(
                    transfers=[],
                    watchlist={_SMART_ADDR: "x"},
                )
            )
        )
        == []
    )


def test_label_transfers_empty_watchlist_still_labels() -> None:
    from crypcodile.api_server import LabelTransfersPayload, label_transfers

    rows = asyncio.run(
        label_transfers(
            LabelTransfersPayload(transfers=_SMART_TRANSFERS, watchlist={})
        )
    )
    assert len(rows) == 2
    assert all(r["from_label"] == "" for r in rows)
    assert all(r["to_label"] == "" for r in rows)
    assert all(r["is_known"] is False for r in rows)


def test_label_transfers_aliases() -> None:
    from crypcodile.api_server import LabelTransfersPayload, label_transfers

    transfers = [
        {
            "from_address": _SMART_ADDR,
            "to_address": _SMART_OTHER,
            "amount": 10,
        }
    ]
    rows = asyncio.run(
        label_transfers(
            LabelTransfersPayload(
                transfers=transfers,
                watchlist={_SMART_ADDR: "smart"},
            )
        )
    )
    assert len(rows) == 1
    assert rows[0]["from_label"] == "smart"
    assert rows[0]["is_known"] is True


def test_label_transfers_matches_analytics() -> None:
    from crypcodile.analytics.smart_money import normalize_watchlist
    from crypcodile.analytics.whale_transfers import label_transfer_addresses
    from crypcodile.api_server import LabelTransfersPayload, label_transfers

    watch = {_SMART_ADDR: "vitalik"}
    expected = label_transfer_addresses(
        _SMART_TRANSFERS, normalize_watchlist(watch)
    )
    rows = asyncio.run(
        label_transfers(
            LabelTransfersPayload(transfers=_SMART_TRANSFERS, watchlist=watch)
        )
    )
    assert rows == expected


def test_label_transfers_negative_min_usd_400() -> None:
    from crypcodile.api_server import LabelTransfersPayload, label_transfers

    with pytest.raises(HTTPException) as ei:
        asyncio.run(
            label_transfers(
                LabelTransfersPayload(
                    transfers=_SMART_TRANSFERS,
                    watchlist={_SMART_ADDR: "x"},
                    min_usd=-1.0,
                )
            )
        )
    assert ei.value.status_code == 400
    assert "non-negative" in str(ei.value.detail)


def test_label_transfers_route_via_mock_client() -> None:
    resp = client.post(
        "/api/v1/label-transfers",
        json={
            "transfers": _SMART_TRANSFERS,
            "watchlist": {_SMART_ADDR: "vitalik"},
            "known_only": True,
            "min_usd": 50.0,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["from_label"] == "vitalik"
    assert data[0]["usd_value"] == 100.0
    assert data[0]["is_known"] is True


def test_label_transfers_route_missing_body_422() -> None:
    resp = client.post("/api/v1/label-transfers", json={})
    assert resp.status_code == 422


def test_label_transfers_route_missing_watchlist_422() -> None:
    resp = client.post(
        "/api/v1/label-transfers",
        json={"transfers": _SMART_TRANSFERS},
    )
    assert resp.status_code == 422


def test_label_transfers_route_registered() -> None:
    """Ensure FastAPI route table includes POST /api/v1/label-transfers."""
    paths = {
        (getattr(r, "path", None), tuple(sorted(getattr(r, "methods", set()) or [])))
        for r in app.routes
    }
    assert ("/api/v1/label-transfers", ("POST",)) in paths


def test_label_transfers_non_finite_floats_json_safe_null() -> None:
    """Pass-through float fields with nan/inf → JSON null on REST."""
    from crypcodile.api_server import LabelTransfersPayload, label_transfers

    transfers = [
        {
            "from": _SMART_ADDR,
            "to": _SMART_OTHER,
            "usd_value": float("inf"),
        },
        {
            "from": _SMART_OTHER,
            "to": _SMART_ADDR,
            "usd_value": float("nan"),
        },
    ]
    rows = asyncio.run(
        label_transfers(
            LabelTransfersPayload(
                transfers=transfers,
                watchlist={_SMART_ADDR: "vitalik"},
            )
        )
    )
    assert len(rows) == 2
    assert rows[0]["usd_value"] is None
    assert rows[0]["from_label"] == "vitalik"
    assert rows[1]["usd_value"] is None
    assert rows[1]["to_label"] == "vitalik"
    # MockTestClient path also encodes without raising.
    resp = client.post(
        "/api/v1/label-transfers",
        json={
            "transfers": transfers,
            "watchlist": {_SMART_ADDR: "vitalik"},
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    assert data[0]["usd_value"] is None
    assert data[1]["usd_value"] is None


# ---------------------------------------------------------------------------
# GET /api/v1/data-coverage — inventory filter for one symbol (read-only)
# ---------------------------------------------------------------------------


def test_data_coverage_empty_symbol() -> None:
    """Missing / blank symbol yields [] without touching the lake client."""
    from crypcodile.api_server import data_coverage

    assert asyncio.run(data_coverage()) == []
    assert asyncio.run(data_coverage(symbol="")) == []
    assert asyncio.run(data_coverage(symbol="   ")) == []


def test_data_coverage_empty_lake(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CRYPCODILE_DATA_DIR", str(tmp_path))
    from crypcodile.api_server import data_coverage

    result = asyncio.run(data_coverage(symbol="deribit:BTC-PERPETUAL"))
    assert result == []


def test_data_coverage_empty_dataframe() -> None:
    mock_client = MagicMock()
    mock_client.inventory.return_value = pl.DataFrame(
        schema={
            "exchange": pl.Utf8,
            "channel": pl.Utf8,
            "symbol": pl.Utf8,
            "min_ts": pl.Int64,
            "max_ts": pl.Int64,
            "row_count": pl.Int64,
        }
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import data_coverage

        result = asyncio.run(data_coverage(symbol="deribit:BTC-PERPETUAL"))
    assert result == []
    mock_client.inventory.assert_called_once_with(channel=None)


def test_data_coverage_filters_by_symbol() -> None:
    mock_client = MagicMock()
    mock_client.inventory.return_value = pl.DataFrame(
        {
            "exchange": ["deribit", "deribit", "binance"],
            "channel": ["trade", "book_snapshot", "trade"],
            "symbol": [
                "deribit:BTC-PERPETUAL",
                "deribit:BTC-PERPETUAL",
                "binance:BTCUSDT",
            ],
            "min_ts": [1, 2, 3],
            "max_ts": [10, 20, 30],
            "row_count": [5, 8, 99],
        }
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import data_coverage

        result = asyncio.run(data_coverage(symbol="deribit:BTC-PERPETUAL"))
    assert len(result) == 2
    assert {r["channel"] for r in result} == {"trade", "book_snapshot"}
    for row in result:
        assert row["symbol"] == "deribit:BTC-PERPETUAL"
        assert row["exchange"] == "deribit"
    mock_client.inventory.assert_called_once_with(channel=None)


def test_data_coverage_channel_filter() -> None:
    mock_client = MagicMock()
    mock_client.inventory.return_value = pl.DataFrame(
        {
            "exchange": ["deribit"],
            "channel": ["trade"],
            "symbol": ["deribit:BTC-PERPETUAL"],
            "min_ts": [1],
            "max_ts": [2],
            "row_count": [10],
        }
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import data_coverage

        result = asyncio.run(
            data_coverage(symbol="deribit:BTC-PERPETUAL", channel="trade")
        )
    assert len(result) == 1
    assert result[0]["channel"] == "trade"
    assert result[0]["row_count"] == 10
    mock_client.inventory.assert_called_once_with(channel="trade")


def test_data_coverage_strips_empty_channel() -> None:
    mock_client = MagicMock()
    mock_client.inventory.return_value = pl.DataFrame(
        schema={
            "exchange": pl.Utf8,
            "channel": pl.Utf8,
            "symbol": pl.Utf8,
            "min_ts": pl.Int64,
            "max_ts": pl.Int64,
            "row_count": pl.Int64,
        }
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import data_coverage

        asyncio.run(data_coverage(symbol="x:Y", channel="  "))
    mock_client.inventory.assert_called_once_with(channel=None)


def test_data_coverage_no_symbol_match() -> None:
    mock_client = MagicMock()
    mock_client.inventory.return_value = pl.DataFrame(
        {
            "exchange": ["binance"],
            "channel": ["trade"],
            "symbol": ["binance:BTCUSDT"],
            "min_ts": [1],
            "max_ts": [2],
            "row_count": [3],
        }
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import data_coverage

        result = asyncio.run(data_coverage(symbol="deribit:BTC-PERPETUAL"))
    assert result == []


def test_data_coverage_route_registered() -> None:
    """Ensure FastAPI route table includes GET /api/v1/data-coverage."""
    paths = {
        (getattr(r, "path", None), tuple(sorted(getattr(r, "methods", set()) or [])))
        for r in app.routes
    }
    assert ("/api/v1/data-coverage", ("GET",)) in paths


def test_catalog_search_route_registered() -> None:
    """Search already lives at /api/v1/catalog/search — no /api/v1/search alias."""
    paths = {
        (getattr(r, "path", None), tuple(sorted(getattr(r, "methods", set()) or [])))
        for r in app.routes
    }
    assert ("/api/v1/catalog/search", ("GET",)) in paths


# ---------------------------------------------------------------------------
# GET /api/v1/resolve-symbols — wrap client.resolve_symbols (read-only)
# ---------------------------------------------------------------------------


def test_resolve_symbols_empty_input_skips_client() -> None:
    mock_client = MagicMock()
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import resolve_symbols

        assert asyncio.run(resolve_symbols()) == []
        assert asyncio.run(resolve_symbols(symbols="")) == []
        assert asyncio.run(resolve_symbols(symbols="   ")) == []
        assert asyncio.run(resolve_symbols(symbols=",,,")) == []
    mock_client.resolve_symbols.assert_not_called()


def test_resolve_symbols_success_list() -> None:
    mock_client = MagicMock()
    mock_client.resolve_symbols.return_value = [
        "deribit:BTC-PERPETUAL",
        "deribit:ETH-PERPETUAL",
    ]
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import resolve_symbols

        result = asyncio.run(
            resolve_symbols(symbols="BTC-PERPETUAL,ETH-PERPETUAL", ambiguous="first")
        )
    assert result == ["deribit:BTC-PERPETUAL", "deribit:ETH-PERPETUAL"]
    mock_client.resolve_symbols.assert_called_once_with(
        ["BTC-PERPETUAL", "ETH-PERPETUAL"],
        channel=None,
        ambiguous="first",
    )


def test_resolve_symbols_strips_parts_and_channel() -> None:
    mock_client = MagicMock()
    mock_client.resolve_symbols.return_value = ["deribit:BTC-PERPETUAL"]
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import resolve_symbols

        result = asyncio.run(
            resolve_symbols(
                symbols="  BTC-PERPETUAL ,  ",
                channel="  trade  ",
                ambiguous="first",
            )
        )
    assert result == ["deribit:BTC-PERPETUAL"]
    mock_client.resolve_symbols.assert_called_once_with(
        ["BTC-PERPETUAL"],
        channel="trade",
        ambiguous="first",
    )


def test_resolve_symbols_default_ambiguous_error() -> None:
    mock_client = MagicMock()
    mock_client.resolve_symbols.return_value = ["deribit:BTC-PERPETUAL"]
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import resolve_symbols

        asyncio.run(resolve_symbols(symbols="BTC-PERPETUAL"))
    mock_client.resolve_symbols.assert_called_once_with(
        ["BTC-PERPETUAL"],
        channel=None,
        ambiguous="error",
    )


def test_resolve_symbols_ambiguous_error_structure() -> None:
    """ambiguous=error multi-match → HTTP 400 with detail message."""
    mock_client = MagicMock()
    mock_client.resolve_symbols.side_effect = ValueError(
        "Ambiguous symbol 'PERPETUAL': 2 matches: "
        "deribit:BTC-PERPETUAL (score=40), deribit:ETH-PERPETUAL (score=40)"
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import resolve_symbols

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(resolve_symbols(symbols="PERPETUAL", ambiguous="error"))
    assert exc_info.value.status_code == 400
    assert "Ambiguous symbol" in str(exc_info.value.detail)
    assert "BTC-PERPETUAL" in str(exc_info.value.detail)


def test_resolve_symbols_no_match_error_structure() -> None:
    mock_client = MagicMock()
    mock_client.resolve_symbols.side_effect = ValueError(
        "No symbols matched 'ZZZZ-NO-MATCH'"
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import resolve_symbols

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(resolve_symbols(symbols="ZZZZ-NO-MATCH"))
    assert exc_info.value.status_code == 400
    assert "No symbols matched" in str(exc_info.value.detail)


def test_resolve_symbols_invalid_ambiguous_mode() -> None:
    mock_client = MagicMock()
    mock_client.resolve_symbols.side_effect = ValueError(
        "ambiguous must be 'error', 'first', or 'all'; got 'maybe'"
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import resolve_symbols

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(resolve_symbols(symbols="BTC", ambiguous="maybe"))
    assert exc_info.value.status_code == 400
    assert "ambiguous must be" in str(exc_info.value.detail)


def test_resolve_symbols_empty_lake(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CRYPCODILE_DATA_DIR", str(tmp_path))
    from crypcodile.api_server import resolve_symbols

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(resolve_symbols(symbols="BTC-PERPETUAL"))
    assert exc_info.value.status_code == 400
    assert "No symbols matched" in str(exc_info.value.detail)


def test_resolve_symbols_unexpected_error_is_500() -> None:
    mock_client = MagicMock()
    mock_client.resolve_symbols.side_effect = RuntimeError("disk failed")
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import resolve_symbols

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(resolve_symbols(symbols="BTC"))
    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Symbol resolution failed."


def test_resolve_symbols_route_registered() -> None:
    """Ensure FastAPI route table includes GET /api/v1/resolve-symbols."""
    paths = {
        (getattr(r, "path", None), tuple(sorted(getattr(r, "methods", set()) or [])))
        for r in app.routes
    }
    assert ("/api/v1/resolve-symbols", ("GET",)) in paths


# ---------------------------------------------------------------------------
# GET /api/v1/health and /api/v1/status — lightweight probe (no payment)
# ---------------------------------------------------------------------------


def test_health_empty_lake(tmp_path, monkeypatch) -> None:
    """Empty / missing lake still reports ok with lake_channels=0."""
    monkeypatch.setenv("CRYPCODILE_DATA_DIR", str(tmp_path))
    from crypcodile import __version__
    from crypcodile.api_server import health

    result = asyncio.run(health())
    assert result == {
        "ok": True,
        "version": __version__,
        "lake_channels": 0,
    }


def test_health_returns_channel_count() -> None:
    mock_client = MagicMock()
    mock_client.list_channels.return_value = ["book_snapshot", "trade"]
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile import __version__
        from crypcodile.api_server import health

        result = asyncio.run(health())
    assert result["ok"] is True
    assert result["version"] == __version__
    assert result["lake_channels"] == 2
    mock_client.list_channels.assert_called_once_with()


def test_status_alias_matches_health() -> None:
    """GET /api/v1/status is the same payload as /api/v1/health."""
    mock_client = MagicMock()
    mock_client.list_channels.return_value = ["trade"]
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import health, status

        h = asyncio.run(health())
        s = asyncio.run(status())
    assert h == s
    assert s["ok"] is True
    assert s["lake_channels"] == 1


def test_health_lake_failure_reports_not_ok() -> None:
    mock_client = MagicMock()
    mock_client.list_channels.side_effect = RuntimeError("disk failed")
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile import __version__
        from crypcodile.api_server import health

        result = asyncio.run(health())
    assert result["ok"] is False
    assert result["version"] == __version__
    assert result["lake_channels"] == 0
    assert result["error"] == "lake_unavailable"


def test_health_and_status_routes_registered() -> None:
    paths = {
        (getattr(r, "path", None), tuple(sorted(getattr(r, "methods", set()) or [])))
        for r in app.routes
    }
    assert ("/api/v1/health", ("GET",)) in paths
    assert ("/api/v1/status", ("GET",)) in paths


# ---------------------------------------------------------------------------
# GET /api/v1/ready — k8s-style readiness (200 when health.ok, else 503)
# ---------------------------------------------------------------------------


def test_ready_returns_200_when_ok() -> None:
    """Ready when lake is available: same payload as health, HTTP 200."""
    from fastapi import Response

    mock_client = MagicMock()
    mock_client.list_channels.return_value = ["trade"]
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile import __version__
        from crypcodile.api_server import health, ready

        response = Response()
        result = asyncio.run(ready(response))
        h = asyncio.run(health())
    assert result == h
    assert result["ok"] is True
    assert result["version"] == __version__
    assert result["lake_channels"] == 1
    # Default Response status is 200; ready does not downgrade when ok.
    assert response.status_code == 200


def test_ready_returns_503_when_not_ok() -> None:
    """Lake failure → readiness fails with HTTP 503; health body still ok=False."""
    from fastapi import Response

    mock_client = MagicMock()
    mock_client.list_channels.side_effect = RuntimeError("disk failed")
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile import __version__
        from crypcodile.api_server import health, ready

        response = Response()
        result = asyncio.run(ready(response))
        h = asyncio.run(health())
    assert result == h
    assert result["ok"] is False
    assert result["error"] == "lake_unavailable"
    assert result["version"] == __version__
    assert response.status_code == 503


def test_ready_empty_lake_is_ready(tmp_path, monkeypatch) -> None:
    """Empty lake is still ready (ok=true, lake_channels=0) — same as health."""
    from fastapi import Response

    monkeypatch.setenv("CRYPCODILE_DATA_DIR", str(tmp_path))
    from crypcodile import __version__
    from crypcodile.api_server import ready

    response = Response()
    result = asyncio.run(ready(response))
    assert result == {
        "ok": True,
        "version": __version__,
        "lake_channels": 0,
    }
    assert response.status_code == 200


def test_ready_route_registered() -> None:
    paths = {
        (getattr(r, "path", None), tuple(sorted(getattr(r, "methods", set()) or [])))
        for r in app.routes
    }
    assert ("/api/v1/ready", ("GET",)) in paths
    # metrics stays at /metrics; readiness is the separate k8s probe
    assert ("/metrics", ("GET",)) in paths


def test_ready_separate_from_health_status_semantics() -> None:
    """health/status always return body (liveness); ready sets 503 when not ok.

    Direct handler calls: health ignores Response status; ready mutates it.
    """
    from fastapi import Response

    mock_fail = MagicMock()
    mock_fail.list_channels.side_effect = RuntimeError("disk failed")
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_fail):
        from crypcodile.api_server import health, ready, status

        ready_resp = Response()
        ready_body = asyncio.run(ready(ready_resp))
        health_body = asyncio.run(health())
        status_body = asyncio.run(status())

    assert ready_body == health_body == status_body
    assert ready_body["ok"] is False
    assert ready_resp.status_code == 503


# ---------------------------------------------------------------------------
# GET /api/v1/capabilities — agent discovery (hardcoded free route + MCP hints)
# ---------------------------------------------------------------------------


def test_capabilities_shape_and_contents() -> None:
    """Returns {rest, mcp_tools_hint} with major free endpoints and tool names."""
    from crypcodile.api_server import (
        _CAPABILITIES_MCP_TOOLS_HINT,
        _CAPABILITIES_REST,
        capabilities,
    )

    with patch("crypcodile.api_server._get_lake_client") as mock_lake:
        result = asyncio.run(capabilities())
        mock_lake.assert_not_called()

    assert set(result.keys()) == {"rest", "mcp_tools_hint"}
    assert result["rest"] == _CAPABILITIES_REST
    assert result["mcp_tools_hint"] == _CAPABILITIES_MCP_TOOLS_HINT
    assert isinstance(result["rest"], list)
    assert isinstance(result["mcp_tools_hint"], list)
    assert len(result["rest"]) >= 10
    assert len(result["mcp_tools_hint"]) >= 8

    # Core free meta / catalog / analytics routes agents should discover
    for route in (
        "GET /api/v1/health",
        "GET /api/v1/ready",
        "GET /api/v1/status",
        "GET /api/v1/capabilities",
        "GET /api/v1/catalog/channels",
        "GET /api/v1/catalog/inventory",
        "GET /api/v1/catalog/dates",
        "GET /api/v1/catalog/scan",
        "GET /api/v1/open-interest",
        "GET /api/v1/perp-basis",
        "GET /api/v1/lending-stress",
        "POST /api/v1/query",
        "POST /api/v1/simulate-price-impact",
    ):
        assert route in result["rest"]

    # Paid/admin routes must not appear in free discovery
    for route in (
        "GET /api/v1/market-data",
        "POST /api/v1/simulate-payment",
        "GET /api/v1/admin/payments",
    ):
        assert route not in result["rest"]

    for tool in (
        "list_data_channels",
        "list_dates",
        "search_symbols",
        "get_indicators",
        "get_spot_future_basis",
        "get_lending_stress",
        "label_transfers",
        "get_onchain_price",
        "get_base_market_data",
        "get_chaos_score",
        "get_funding_prediction",
    ):
        assert tool in result["mcp_tools_hint"]

    # All REST entries are METHOD + path; no duplicates
    for entry in result["rest"]:
        assert entry.startswith("GET ") or entry.startswith("POST ")
        assert "/api/v1/" in entry
    assert len(result["rest"]) == len(set(result["rest"]))
    assert len(result["rest"]) >= 30


def test_capabilities_returns_defensive_copies() -> None:
    """Mutating the response lists must not corrupt subsequent calls."""
    from crypcodile.api_server import capabilities

    a = asyncio.run(capabilities())
    a["rest"].append("GET /api/v1/not-real")
    a["mcp_tools_hint"].append("not_a_tool")
    b = asyncio.run(capabilities())
    assert "GET /api/v1/not-real" not in b["rest"]
    assert "not_a_tool" not in b["mcp_tools_hint"]


def test_capabilities_route_registered() -> None:
    paths = {
        (getattr(r, "path", None), tuple(sorted(getattr(r, "methods", set()) or [])))
        for r in app.routes
    }
    assert ("/api/v1/capabilities", ("GET",)) in paths


# ---------------------------------------------------------------------------
# GET /api/v1/version — package version only (no payment, no lake)
# ---------------------------------------------------------------------------


def test_version_returns_package_version_only() -> None:
    """Endpoint returns exactly {\"version\": __version__}; no lake touch."""
    from crypcodile import __version__
    from crypcodile.api_server import version

    with patch("crypcodile.api_server._get_lake_client") as mock_lake:
        result = asyncio.run(version())
        mock_lake.assert_not_called()

    assert result == {"version": __version__}
    assert set(result.keys()) == {"version"}
    assert isinstance(result["version"], str)
    assert result["version"]


def test_version_route_registered() -> None:
    paths = {
        (getattr(r, "path", None), tuple(sorted(getattr(r, "methods", set()) or [])))
        for r in app.routes
    }
    assert ("/api/v1/version", ("GET",)) in paths


# ---------------------------------------------------------------------------
# GET /api/v1/exchanges — factory registry (no payment, no lake)
# ---------------------------------------------------------------------------


def test_exchanges_returns_factory_list() -> None:
    """Endpoint mirrors list_exchanges() without touching the lake."""
    from crypcodile.api_server import exchanges
    from crypcodile.exchanges.factory import list_exchanges

    with patch("crypcodile.api_server._get_lake_client") as mock_lake:
        result = asyncio.run(exchanges())
        mock_lake.assert_not_called()

    assert result == list_exchanges()
    assert result == sorted(result)
    assert "binance" in result
    assert "base_onchain" in result
    assert "superchain" in result


def test_exchanges_returns_copy_semantics() -> None:
    """Mutating the response list must not corrupt subsequent calls."""
    from crypcodile.api_server import exchanges

    a = asyncio.run(exchanges())
    a.append("not-an-exchange")
    b = asyncio.run(exchanges())
    assert "not-an-exchange" not in b


def test_exchanges_route_registered() -> None:
    paths = {
        (getattr(r, "path", None), tuple(sorted(getattr(r, "methods", set()) or [])))
        for r in app.routes
    }
    assert ("/api/v1/exchanges", ("GET",)) in paths
