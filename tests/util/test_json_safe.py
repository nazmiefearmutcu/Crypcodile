"""Unit tests for crypcodile.util.json_safe (shared REST/MCP boundary helpers)."""

from __future__ import annotations

import json

from crypcodile.api_server import (
    _json_safe_float as api_json_safe_float,
    _json_safe_records as api_json_safe_records,
)
from crypcodile.mcp_server import (
    _json_safe_float as mcp_json_safe_float,
    _json_safe_records as mcp_json_safe_records,
)
from crypcodile.util.json_safe import json_safe_float, json_safe_records


def test_json_safe_float_maps_non_finite_to_none() -> None:
    assert json_safe_float(1.5) == 1.5
    assert json_safe_float(0.0) == 0.0
    assert json_safe_float(-2.25) == -2.25
    assert json_safe_float(float("inf")) is None
    assert json_safe_float(float("-inf")) is None
    assert json_safe_float(float("nan")) is None


def test_json_safe_records_sanitizes_float_fields() -> None:
    rows = [
        {
            "local_ts": 100,
            "symbol": "deribit:BTC-PERPETUAL",
            "ofi": float("inf"),
            "apr": float("nan"),
            "basis_pct": float("-inf"),
            "total_oi": 1500.0,
            "flag": True,
            "note": None,
        },
        {
            "local_ts": 200,
            "symbol": "x",
            "ofi": 1.25,
            "apr": 0.0,
            "basis_pct": -0.5,
            "total_oi": 0.0,
            "flag": False,
            "note": "ok",
        },
    ]
    out = json_safe_records(rows)
    assert out[0]["local_ts"] == 100
    assert out[0]["symbol"] == "deribit:BTC-PERPETUAL"
    assert out[0]["ofi"] is None
    assert out[0]["apr"] is None
    assert out[0]["basis_pct"] is None
    assert out[0]["total_oi"] == 1500.0
    assert out[0]["flag"] is True
    assert out[0]["note"] is None
    assert out[1]["ofi"] == 1.25
    assert out[1]["apr"] == 0.0
    assert out[1]["basis_pct"] == -0.5
    assert out[1]["total_oi"] == 0.0
    assert out[1]["flag"] is False
    assert out[1]["note"] == "ok"
    assert json_safe_records([]) == []
    encoded = json.dumps(out)
    assert "null" in encoded
    assert "Infinity" not in encoded
    assert "NaN" not in encoded


def test_api_and_mcp_reexport_same_helpers() -> None:
    """api_server / mcp_server re-export the shared util (dedupe check)."""
    assert api_json_safe_float is json_safe_float
    assert mcp_json_safe_float is json_safe_float
    assert api_json_safe_records is json_safe_records
    assert mcp_json_safe_records is json_safe_records
