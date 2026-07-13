"""Unit tests for MCP analytics tool handlers (slippage, OFI, whale, IV, skew, basis).

Exercises pure handlers with mocked clients; no stdio / live lake required.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import polars as pl

from crypcodile.mcp_server import (
    TOOLS,
    handle_calculate_ofi,
    handle_estimate_slippage,
    handle_get_iv_surface,
    handle_get_perp_basis,
    handle_get_risk_reversal,
    handle_get_spot_perp_basis,
    handle_get_term_structure,
    handle_get_vol_skew,
    handle_track_whale_alerts,
)

_ANALYTICS_TOOLS = {
    "estimate_slippage",
    "calculate_ofi",
    "track_whale_alerts",
    "get_iv_surface",
    "get_term_structure",
    "get_vol_skew",
    "get_risk_reversal",
    "get_perp_basis",
    "get_spot_perp_basis",
}


def test_tools_contains_analytics_names() -> None:
    names = {t["name"] for t in TOOLS}
    assert _ANALYTICS_TOOLS.issubset(names)


def test_handle_estimate_slippage_returns_dicts() -> None:
    client = MagicMock()
    client.estimate_slippage.return_value = pl.DataFrame(
        {"mid": [100.0], "avg_price": [100.5], "slippage_bps": [5.0]}
    )
    rows = handle_estimate_slippage(client, "deribit:BTC-PERPETUAL", "buy", 1.0)
    assert isinstance(rows, list)
    assert len(rows) == 1
    assert rows[0]["mid"] == 100.0
    client.estimate_slippage.assert_called_once_with(
        "deribit:BTC-PERPETUAL", "buy", 1.0
    )


def test_handle_estimate_slippage_empty() -> None:
    client = MagicMock()
    client.estimate_slippage.return_value = pl.DataFrame()
    assert handle_estimate_slippage(client, "x", "buy", 1.0) == []


def test_handle_calculate_ofi_returns_dicts() -> None:
    client = MagicMock()
    client.calculate_ofi.return_value = pl.DataFrame(
        {"bin_start": [1], "ofi": [0.25]}
    )
    rows = handle_calculate_ofi(client, "deribit:BTC-PERPETUAL", 0, 100, "1m")
    assert isinstance(rows, list)
    assert len(rows) == 1
    assert rows[0]["ofi"] == 0.25
    client.calculate_ofi.assert_called_once_with(
        "deribit:BTC-PERPETUAL", 0, 100, "1m"
    )


def test_handle_calculate_ofi_empty() -> None:
    client = MagicMock()
    client.calculate_ofi.return_value = pl.DataFrame()
    assert handle_calculate_ofi(client, "x", 0, 1, "1m") == []


def test_handle_track_whale_alerts_returns_dicts() -> None:
    client = MagicMock()
    client.track_whale_alerts.return_value = pl.DataFrame(
        {"price": [50000.0], "usd_notional": [1_000_000.0]}
    )
    rows = handle_track_whale_alerts(
        client, "deribit:BTC-PERPETUAL", 0, 100, 500_000.0
    )
    assert isinstance(rows, list)
    assert len(rows) == 1
    assert rows[0]["usd_notional"] == 1_000_000.0
    client.track_whale_alerts.assert_called_once_with(
        "deribit:BTC-PERPETUAL", 0, 100, 500_000.0
    )


def test_handle_track_whale_alerts_empty() -> None:
    client = MagicMock()
    client.track_whale_alerts.return_value = pl.DataFrame()
    assert handle_track_whale_alerts(client, "x", 0, 1, 100.0) == []


def test_handle_get_iv_surface_returns_dicts() -> None:
    client = MagicMock()
    client.iv_surface.return_value = pl.DataFrame(
        {"strike": [100.0], "iv": [0.5], "opt_type": ["C"]}
    )
    rows = handle_get_iv_surface(client, "BTC", 1_700_000_000_000_000_000, rate=0.01)
    assert isinstance(rows, list)
    assert len(rows) == 1
    assert rows[0]["iv"] == 0.5
    client.iv_surface.assert_called_once_with(
        "BTC", 1_700_000_000_000_000_000, rate=0.01
    )


def test_handle_get_iv_surface_empty() -> None:
    client = MagicMock()
    client.iv_surface.return_value = pl.DataFrame()
    assert handle_get_iv_surface(client, "BTC", 0) == []


def test_handle_get_term_structure_returns_dicts() -> None:
    client = MagicMock()
    client.term_structure.return_value = pl.DataFrame(
        {"expiry": [1], "atm_iv": [0.4], "days_to_expiry": [30.0]}
    )
    rows = handle_get_term_structure(client, "ETH", 1_700_000_000_000_000_000)
    assert isinstance(rows, list)
    assert len(rows) == 1
    assert rows[0]["atm_iv"] == 0.4
    client.term_structure.assert_called_once_with(
        "ETH", 1_700_000_000_000_000_000, rate=0.0
    )


def test_handle_get_term_structure_empty() -> None:
    client = MagicMock()
    client.term_structure.return_value = pl.DataFrame()
    assert handle_get_term_structure(client, "ETH", 0) == []


def test_handle_get_vol_skew_returns_dicts() -> None:
    client = MagicMock()
    client.vol_skew.return_value = pl.DataFrame(
        {"strike": [100.0], "iv": [0.55], "delta": [0.5]}
    )
    rows = handle_get_vol_skew(
        client, "BTC", 1_700_100_000_000_000_000, 1_700_000_000_000_000_000, rate=0.01
    )
    assert isinstance(rows, list)
    assert len(rows) == 1
    assert rows[0]["iv"] == 0.55
    client.vol_skew.assert_called_once_with(
        "BTC", 1_700_100_000_000_000_000, 1_700_000_000_000_000_000, rate=0.01
    )


def test_handle_get_vol_skew_empty() -> None:
    client = MagicMock()
    client.vol_skew.return_value = pl.DataFrame()
    assert handle_get_vol_skew(client, "BTC", 1, 0) == []


def test_handle_get_risk_reversal_returns_dict() -> None:
    client = MagicMock()
    skew = pl.DataFrame({"strike": [100.0], "iv": [0.5], "delta": [0.5]})
    client.vol_skew.return_value = skew
    client.risk_reversal_butterfly.return_value = (0.02, 0.01)
    result = handle_get_risk_reversal(
        client, "BTC", 1_700_100_000_000_000_000, 1_700_000_000_000_000_000, rate=0.0
    )
    assert result == {"risk_reversal": 0.02, "butterfly": 0.01}
    client.vol_skew.assert_called_once_with(
        "BTC", 1_700_100_000_000_000_000, 1_700_000_000_000_000_000, rate=0.0
    )
    client.risk_reversal_butterfly.assert_called_once_with(skew, target_delta=0.25)


def test_handle_get_risk_reversal_empty_skew() -> None:
    client = MagicMock()
    client.vol_skew.return_value = pl.DataFrame()
    assert handle_get_risk_reversal(client, "BTC", 1, 0) == {
        "risk_reversal": None,
        "butterfly": None,
    }
    client.risk_reversal_butterfly.assert_not_called()


def test_handle_get_perp_basis_returns_dicts() -> None:
    client = MagicMock()
    client.perp_basis.return_value = pl.DataFrame(
        {
            "local_ts": [1],
            "mark_price": [100.5],
            "index_price": [100.0],
            "basis": [0.5],
            "basis_pct": [0.005],
        }
    )
    rows = handle_get_perp_basis(client, "deribit:BTC-PERPETUAL", 0, 100)
    assert isinstance(rows, list)
    assert len(rows) == 1
    assert rows[0]["basis"] == 0.5
    client.perp_basis.assert_called_once_with("deribit:BTC-PERPETUAL", 0, 100)


def test_handle_get_perp_basis_empty() -> None:
    client = MagicMock()
    client.perp_basis.return_value = pl.DataFrame()
    assert handle_get_perp_basis(client, "x", 0, 1) == []


def test_handle_get_spot_perp_basis_returns_dicts() -> None:
    client = MagicMock()
    client.spot_perp_basis.return_value = pl.DataFrame(
        {
            "local_ts": [1],
            "spot_price": [100.0],
            "perp_price": [100.5],
            "basis": [0.5],
            "basis_pct": [0.005],
        }
    )
    rows = handle_get_spot_perp_basis(
        client, "deribit:BTC-SPOT", "deribit:BTC-PERPETUAL", 0, 100
    )
    assert isinstance(rows, list)
    assert len(rows) == 1
    assert rows[0]["basis"] == 0.5
    client.spot_perp_basis.assert_called_once_with(
        "deribit:BTC-SPOT", "deribit:BTC-PERPETUAL", 0, 100
    )


def test_handle_get_spot_perp_basis_empty() -> None:
    client = MagicMock()
    client.spot_perp_basis.return_value = pl.DataFrame()
    assert handle_get_spot_perp_basis(client, "s", "p", 0, 1) == []


def test_analytics_tool_schemas_have_required_fields() -> None:
    by_name: dict[str, Any] = {t["name"]: t for t in TOOLS}
    assert set(by_name["estimate_slippage"]["inputSchema"]["required"]) == {
        "symbol",
        "side",
        "size",
    }
    assert set(by_name["calculate_ofi"]["inputSchema"]["required"]) == {
        "symbol",
        "start",
        "end",
        "interval",
    }
    assert set(by_name["track_whale_alerts"]["inputSchema"]["required"]) == {
        "symbol",
        "start",
        "end",
        "min_usd",
    }
    assert set(by_name["get_iv_surface"]["inputSchema"]["required"]) == {
        "underlying",
        "at",
    }
    assert set(by_name["get_term_structure"]["inputSchema"]["required"]) == {
        "underlying",
        "at",
    }
    assert set(by_name["get_vol_skew"]["inputSchema"]["required"]) == {
        "underlying",
        "expiry_ns",
        "at",
    }
    assert set(by_name["get_risk_reversal"]["inputSchema"]["required"]) == {
        "underlying",
        "expiry_ns",
        "at",
    }
    assert set(by_name["get_perp_basis"]["inputSchema"]["required"]) == {
        "perp_symbol",
        "start",
        "end",
    }
    assert set(by_name["get_spot_perp_basis"]["inputSchema"]["required"]) == {
        "spot_symbol",
        "perp_symbol",
        "start",
        "end",
    }
