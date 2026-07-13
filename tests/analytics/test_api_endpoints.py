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
    from crypcodile.api_server import _is_mutating_sql

    for kw in (
        "INSERT",
        "UPDATE",
        "DELETE",
        "DROP",
        "CREATE",
        "ALTER",
        "ATTACH",
        "COPY",
    ):
        assert _is_mutating_sql(f"{kw} INTO trade VALUES (1)") is True
        assert _is_mutating_sql(f"  {kw.lower()} table trade;") is True
        assert _is_mutating_sql(f"select 1; {kw} x") is True

    assert _is_mutating_sql("SELECT * FROM trade") is False
    assert _is_mutating_sql("SELECT count(*) AS n FROM book_snapshot") is False
    # Word-boundary: substring alone is not enough
    assert _is_mutating_sql("SELECT deleted_at FROM trade") is False


def test_query_lake_returns_rows() -> None:
    mock_client = MagicMock()
    mock_client.query.return_value = pl.DataFrame(
        {
            "symbol": ["deribit:BTC-PERPETUAL"],
            "n": [42],
        }
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import QueryPayload, query_lake

        result = asyncio.run(
            query_lake(QueryPayload(sql="SELECT symbol, count(*) AS n FROM trade"))
        )
    assert len(result) == 1
    assert result[0]["symbol"] == "deribit:BTC-PERPETUAL"
    assert result[0]["n"] == 42
    mock_client.query.assert_called_once_with(
        "SELECT symbol, count(*) AS n FROM trade"
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
    assert "SQL execution failed" in str(exc_info.value.detail)


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
    mock_client.query.return_value = pl.DataFrame(
        {
            "local_ts": [1, 2, 3, 4, 5],
            "price": [10.0, 20.0, 30.0, 40.0, 50.0],
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
    mock_client.query.assert_called_once()


def test_query_lake_clamps_limit_minimum() -> None:
    mock_client = MagicMock()
    mock_client.query.return_value = pl.DataFrame(
        {"local_ts": [1, 2, 3], "price": [1.0, 2.0, 3.0]}
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import QueryPayload, query_lake

        result = asyncio.run(
            query_lake(QueryPayload(sql="SELECT * FROM trade", limit=0))
        )
    assert len(result) == 1
    assert result[0]["local_ts"] == 1


def test_query_lake_default_limit_is_max() -> None:
    mock_client = MagicMock()
    mock_client.query.return_value = pl.DataFrame(
        {"local_ts": list(range(5)), "price": [1.0] * 5}
    )
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import QueryPayload, query_lake

        result = asyncio.run(query_lake(QueryPayload(sql="SELECT * FROM trade")))
    assert len(result) == 5


def test_query_lake_sql_error() -> None:
    mock_client = MagicMock()
    mock_client.query.side_effect = RuntimeError("syntax error at or near X")
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        from crypcodile.api_server import QueryPayload, query_lake

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(query_lake(QueryPayload(sql="SELEC bogus")))
    assert exc_info.value.status_code == 400
    assert "SQL execution failed" in str(exc_info.value.detail)


def test_query_lake_route_via_mock_client() -> None:
    mock_client = MagicMock()
    mock_client.query.return_value = pl.DataFrame({"x": [1]})
    with patch("crypcodile.api_server._get_lake_client", return_value=mock_client):
        resp = client.post("/api/v1/query", json={"sql": "SELECT 1 AS x", "limit": 10})
    assert resp.status_code == 200
    assert resp.json() == [{"x": 1}]


def test_query_lake_route_rejects_mutating() -> None:
    resp = client.post(
        "/api/v1/query",
        json={"sql": "DROP TABLE trade"},
    )
    assert resp.status_code == 400
    assert "Mutating SQL" in resp.json()["detail"]
