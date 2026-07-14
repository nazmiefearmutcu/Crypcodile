"""Unit + CLI tests for offline MEV sandwich detection."""

from __future__ import annotations

from pathlib import Path

import polars as pl
from typer.testing import CliRunner

from crypcodile.analytics.mev_sandwich import (
    MEVSandwichFilter,
    detect_sandwiches,
    prepare_trade_frame,
)
from crypcodile.cli import app

_RUNNER = CliRunner()

_SANDWICH_ROWS = [
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


def test_detect_sandwiches_positive() -> None:
    res = detect_sandwiches(pl.DataFrame(_SANDWICH_ROWS))
    assert res.height == 4
    assert res["is_sandwich"].to_list() == [True, True, True, False]


def test_detect_sandwiches_negative_across_blocks() -> None:
    trades = [
        {"block": 100, "pool": "AERO-USDC", "log_index": 10, "sender": "0xattacker", "is_buy": True},
        {"block": 101, "pool": "AERO-USDC", "log_index": 11, "sender": "0xvictim", "is_buy": True},
        {"block": 102, "pool": "AERO-USDC", "log_index": 12, "sender": "0xattacker", "is_buy": False},
    ]
    res = detect_sandwiches(pl.DataFrame(trades))
    assert not res["is_sandwich"].any()


def test_prepare_trade_frame_coerces_string_is_buy() -> None:
    df = pl.DataFrame(
        {
            "block": [1, 1],
            "pool": ["p", "p"],
            "log_index": [0, 1],
            "sender": ["a", "b"],
            "is_buy": ["true", "false"],
        }
    )
    out = prepare_trade_frame(df)
    assert out["is_buy"].dtype == pl.Boolean
    assert out["is_buy"].to_list() == [True, False]


def test_prepare_trade_frame_missing_cols() -> None:
    try:
        prepare_trade_frame(pl.DataFrame({"block": [1]}))
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert "missing required columns" in str(e)


def test_empty_frame() -> None:
    df = pl.DataFrame(
        schema={
            "block": pl.Int64,
            "pool": pl.Utf8,
            "log_index": pl.Int64,
            "sender": pl.Utf8,
            "is_buy": pl.Boolean,
        }
    )
    res = MEVSandwichFilter.detect_sandwiches(df)
    assert res.height == 0
    assert "is_sandwich" in res.columns


def test_cli_mev_sandwich_csv(tmp_path: Path) -> None:
    path = tmp_path / "trades.csv"
    pl.DataFrame(_SANDWICH_ROWS).write_csv(path)

    result = _RUNNER.invoke(app, ["mev-sandwich", "--trades", str(path)])
    assert result.exit_code == 0, result.output
    assert "is_sandwich" in result.output
    assert "0xattacker" in result.output
    assert "sandwich_legs: 3 / 4" in result.output


def test_cli_mev_sandwich_json_sandwiches_only(tmp_path: Path) -> None:
    path = tmp_path / "trades.json"
    pl.DataFrame(_SANDWICH_ROWS).write_json(path)

    result = _RUNNER.invoke(
        app,
        ["mev-sandwich", "--trades", str(path), "--sandwiches-only"],
    )
    assert result.exit_code == 0, result.output
    assert "0xnormal" not in result.output
    assert "0xvictim" in result.output
    assert "sandwich_legs: 3 / 4" in result.output


def test_cli_mev_sandwich_missing_args_exits_1() -> None:
    result = _RUNNER.invoke(app, ["mev-sandwich"])
    assert result.exit_code == 1
    assert "Error" in result.output


def test_cli_mev_sandwich_registered() -> None:
    result = _RUNNER.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "mev-sandwich" in result.output
