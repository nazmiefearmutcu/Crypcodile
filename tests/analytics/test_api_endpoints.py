from __future__ import annotations

import asyncio
import inspect
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
