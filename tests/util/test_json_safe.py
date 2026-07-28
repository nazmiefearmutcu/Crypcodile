"""Unit tests for crocodile.core.util.json_safe (the shared REST/MCP boundary helper).

The last test used to assert that the crypto REST server and the crypto MCP server
re-exported the *same* function object, which is what stopped the two hand-written stacks
from growing two answers to "what is NaN on the wire". Both stacks are gone; the projection
is the one boundary now, so the same property is asserted about it instead. Dropping the
test with the stacks would have quietly permitted the fourth copy — and there nearly was
one: ``dispatch._jsonable`` had reimplemented ``math.isfinite`` inline.
"""

from __future__ import annotations

import json

from crocodile.core.util.json_safe import json_safe_float, json_safe_records


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


def test_the_one_surface_boundary_uses_the_shared_helper() -> None:
    """The dedupe check, moved onto the surface that replaced the four that had it.

    Asserted by behaviour rather than by identity because ``_jsonable`` takes a cell of any
    type and delegates only the floats: an identity check would have to be against a
    wrapper, and would pass for a wrapper that had stopped calling anything.
    """
    from crocodile.surfaces import dispatch

    for non_finite in (float("inf"), float("-inf"), float("nan")):
        assert dispatch._jsonable(non_finite) is json_safe_float(non_finite) is None
    for finite in (1.5, 0.0, -2.25):
        assert dispatch._jsonable(finite) == json_safe_float(finite) == finite
    for passthrough in (7, "x", None, True):
        assert dispatch._jsonable(passthrough) is passthrough
