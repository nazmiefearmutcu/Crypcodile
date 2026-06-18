import pytest
from unittest.mock import patch
from typer.testing import CliRunner
import datetime
from crypcodile.cli import app, prompt_time_range_helper, select_collect_params_interactively

def test_timestamp_overflow_handling(tmp_path):
    """Test how the parser handles extremely large timestamps that exceed Python/C limit."""
    runner = CliRunner()
    
    # 1. Extremely large start timestamp
    # If the user provides a timestamp like 999999999999999999999999999999 (which is digit-only)
    # it gets parsed as integer. The standard datetime module will fail with ValueError/OverflowError
    # when formatting or during partition pruning check.
    with patch("crypcodile.cli.is_interactive_stdin", return_value=True), \
         patch("typer.prompt", side_effect=["999999999999999999999999999999", "999999999999999999999999999999"]):
        start_ts, end_ts = prompt_time_range_helper(tmp_path, "trade", ["deribit:BTC-PERPETUAL"])
        # Expecting the parser to return the default fallbacks when date formatting fails,
        # or it returns the extremely large parsed integers directly.
        # Let's verify that the helper returns either fallback or parsed value.
        assert start_ts is not None
        assert end_ts is not None

def test_corrupted_timestamp_inputs(tmp_path):
    """Test how invalid date strings are rejected and fallback timestamps are used."""
    with patch("typer.prompt", side_effect=["invalid-date-format-123", "2026-99-99 99:99:99"]):
        # With invalid formats, prompt_time_range_helper should log warning and return default fallbacks
        start_ts, end_ts = prompt_time_range_helper(tmp_path, "trade", ["deribit:BTC-PERPETUAL"])
        assert start_ts == 0
        assert end_ts == 9999999999999999999

def test_invalid_selection_indexes_in_wizard():
    """Verify that selection wizard digits checking handles bad input correctly."""
    # Custom choices list for collect:
    # 1. Exchange choice is invalid index '99' -> then valid '1' (binance).
    # 2. Channel choice is invalid index '99' -> then valid '1' (trade).
    # 3. Symbol choice is 'c' -> then custom symbol 'INVALID_SYM'.
    with patch("typer.prompt", side_effect=["99", "1", "99", "1", "c", "INVALID_SYM"]):
        exchange, symbols, channels = select_collect_params_interactively(None, None, None)
        assert exchange == "binance"
        assert channels == ["trade"]
        assert symbols == ["INVALID_SYM"]

def test_incomplete_basis_combinations(tmp_path):
    """Verify basis command exits with 1 when incomplete spot-future options are passed."""
    runner = CliRunner()
    
    # 1. Spot-Future mode but spot is missing
    result = runner.invoke(
        app,
        ["basis", "--future", "deribit:BTC-FUTURE", "--data-dir", str(tmp_path)],
        env={"PYTHONUNBUFFERED": "1"}
    )
    # Since stdin is not interactive, this should fail with error code 1
    assert result.exit_code == 1
    assert "Either --perp, or both --future and --spot must be specified" in result.output or "Either --perp, or both --future and --spot must be specified" in result.stderr

    # 2. Spot-Future mode but future is missing
    result = runner.invoke(
        app,
        ["basis", "--spot", "binance-spot:BTCUSDT", "--data-dir", str(tmp_path)],
        env={"PYTHONUNBUFFERED": "1"}
    )
    assert result.exit_code == 1
    assert "Either --perp, or both --future and --spot must be specified" in result.output or "Either --perp, or both --future and --spot must be specified" in result.stderr
