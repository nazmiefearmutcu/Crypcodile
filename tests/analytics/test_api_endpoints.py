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


def test_get_lake_client_uses_env(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CRYPCODILE_DATA_DIR", str(tmp_path))
    from crypcodile.api_server import _get_lake_client

    client = _get_lake_client()
    assert client._catalog._data_dir == tmp_path
    assert client.list_channels() == []