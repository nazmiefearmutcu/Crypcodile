import pathlib
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from typer.testing import CliRunner
import polars as pl
from crypcodile.cli import app, make_sparkline, select_collect_params_interactively
from crypcodile.client.export import export as client_export
from crypcodile.store.catalog import Catalog

_BASE_TS = 1_700_000_000_000_000_000

def test_piped_query_command(tmp_path):
    runner = CliRunner()
    with patch("crypcodile.cli.is_interactive_stdin", return_value=False):
        result = runner.invoke(app, ["query", "--data-dir", str(tmp_path)], input="SELECT 42 AS val")
        assert result.exit_code == 0
        assert "42" in result.output

def test_piped_query_command_empty(tmp_path):
    runner = CliRunner()
    with patch("crypcodile.cli.is_interactive_stdin", return_value=False):
        result = runner.invoke(app, ["query", "--data-dir", str(tmp_path)], input="   ")
        assert result.exit_code == 1
        assert "Error: SQL query is required and stdin is empty." in result.stderr or "Error: SQL query is required and stdin is empty." in result.output

def test_non_interactive_validation_failures(tmp_path):
    runner = CliRunner()
    with patch("crypcodile.cli.is_interactive_stdin", return_value=False):
        result = runner.invoke(app, ["export", "--data-dir", str(tmp_path)])
        assert result.exit_code == 1
        assert "Error:" in result.stderr or "Error:" in result.output

        result = runner.invoke(app, ["replay", "--data-dir", str(tmp_path)])
        assert result.exit_code == 1
        assert "Error:" in result.stderr or "Error:" in result.output

        result = runner.invoke(app, ["collect", "--data-dir", str(tmp_path)])
        assert result.exit_code == 1
        assert "Error:" in result.stderr or "Error:" in result.output

        result = runner.invoke(app, ["funding-apr", "--data-dir", str(tmp_path)])
        assert result.exit_code == 1
        assert "Error:" in result.stderr or "Error:" in result.output

def test_basis_mutually_exclusive_and_non_interactive(tmp_path):
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["basis", "--perp", "BTC-PERPETUAL", "--future", "BTC-FUTURE", "--data-dir", str(tmp_path)]
    )
    assert result.exit_code == 1
    assert "mutually exclusive" in result.output or "mutually exclusive" in result.stderr

    with patch("crypcodile.cli.is_interactive_stdin", return_value=False):
        result = runner.invoke(app, ["basis", "--data-dir", str(tmp_path)])
        assert result.exit_code == 1
        assert "Error:" in result.output or "Error:" in result.stderr

def test_basis_implicit_mode_interactive(tmp_path):
    runner = CliRunner()
    with patch("crypcodile.cli.is_interactive_stdin", return_value=True), \
         patch("crypcodile.cli.select_symbols_interactively", return_value=(None, ["binance-spot:BTCUSDT"])), \
         patch("crypcodile.cli.prompt_symbol", return_value="binance-spot:BTCUSDT"), \
         patch("crypcodile.cli.prompt_time_range_helper", return_value=(0, 9999999999999999999)), \
         patch("crypcodile.client.client.CrypcodileClient.spot_future_basis", return_value=pl.DataFrame()):
        result = runner.invoke(
            app,
            ["basis", "--future", "deribit:BTC-FUTURE", "--data-dir", str(tmp_path)]
        )
        assert result.exit_code == 0
        assert "No basis data found." in result.output

def test_sparkline_nan_inf_validation():
    assert make_sparkline([100.0, float("nan"), 200.0]) != ""
    assert make_sparkline([100.0, float("inf")]) == ""
    assert make_sparkline([float("nan"), float("inf"), None]) == ""
    assert make_sparkline([100.0, 100.0]) == "██"

def test_selection_wizard_digit_checks():
    with patch("typer.prompt") as mock_prompt:
        # list_exchanges() is sorted: 1=base_onchain, 2=binance, ...
        mock_prompt.side_effect = ["2", "1, trade", "trade,book_ticker", "1"]
        exchange, symbols, channels = select_collect_params_interactively(None, None, None)
        assert exchange == "binance"
        assert channels == ["trade", "book_ticker"]
        assert symbols == ["BTCUSDT"]

def test_empty_dataframe_export_schema(tmp_path):
    catalog = Catalog(tmp_path)
    dest = tmp_path / "empty_export.parquet"
    client_export(catalog, "trade", [], 0, 9999999999999999999, "parquet", dest)
    assert dest.exists()
    df = pl.read_parquet(dest)
    assert len(df) == 0
    assert "price" in df.columns
    assert "amount" in df.columns
    assert "channel" in df.columns
    assert "date" in df.columns


def test_adversarial_timestamp_overflow(tmp_path):
    # Testing extremely large timestamp overflow
    from crypcodile.store.parquet_sink import _channel_schema
    schema = _channel_schema("trade")
    df = pl.DataFrame([{
        "exchange": "deribit",
        "symbol": "deribit:BTC-PERPETUAL",
        "symbol_raw": "BTC-PERPETUAL",
        "exchange_ts": 1700000000000,
        "local_ts": 1700000000000,
        "channel": "trade",
        "date": "2023-11-14",
        "bucket": 0,
        "id": "t1",
        "price": 30000.0,
        "amount": 1.0,
        "side": "buy",
        "liquidation": "false",
    }], schema=schema)
    part_dir = tmp_path / "exchange=deribit" / "channel=trade" / "date=2023-11-14" / "bucket=0"
    part_dir.mkdir(parents=True, exist_ok=True)
    df.write_parquet(part_dir / "part-0.parquet")

    from crypcodile.client.client import CrypcodileClient
    client = CrypcodileClient(data_dir=tmp_path)
    
    # 21-digit timestamp or larger should cause datetime/OverflowError when scanned
    with pytest.raises((OverflowError, OSError, ValueError)):
        client.scan("trade", ["deribit:BTC-PERPETUAL"], 0, 999999999999999999999)


def test_adversarial_selection_wizard_loops():
    # Test that select_collect_params_interactively rejects invalid/out-of-bound indexes and eventually accepts a valid one
    with patch("typer.prompt") as mock_prompt:
        # exchange: 99 (invalid), 2 (valid -> binance; list_exchanges is sorted)
        # channels: 99 (invalid), 1 (valid -> trade)
        # symbols: 99 (invalid), 1 (valid -> BTCUSDT)
        mock_prompt.side_effect = ["99", "2", "99", "1", "99", "1"]
        exchange, symbols, channels = select_collect_params_interactively(None, None, None)
        assert exchange == "binance"
        assert channels == ["trade"]
        assert symbols == ["BTCUSDT"]


def test_adversarial_selection_wizard_non_digit():
    # Test that select_collect_params_interactively rejects non-digit / random strings and loops
    with patch("typer.prompt") as mock_prompt:
        # exchange: "invalid", "2" (binance)
        # channels: "invalid", "1"
        # symbols: "invalid", "1"
        mock_prompt.side_effect = ["invalid", "2", "invalid", "1", "invalid", "1"]
        exchange, symbols, channels = select_collect_params_interactively(None, None, None)
        assert exchange == "binance"
        assert channels == ["trade"]
        assert symbols == ["BTCUSDT"]


def test_collect_is_interactive_nameerror_fix(tmp_path):
    runner = CliRunner()
    with patch("crypcodile.cli.is_interactive_stdin", return_value=False), \
         patch("crypcodile.cli.collect_live", new_callable=AsyncMock) as mock_collect_live, \
         patch("crypcodile.cli.AiohttpWsTransport") as mock_transport, \
         patch("crypcodile.cli.make_connector") as mock_connector:
        
        mock_conn = MagicMock()
        mock_conn.transport = MagicMock()
        mock_connector.return_value = mock_conn
        
        result = runner.invoke(
            app,
            ["collect", "--exchange", "binance", "--symbols", "BTCUSDT", "--channels", "trade", "--data-dir", str(tmp_path)]
        )
        assert "NameError" not in result.output
        assert result.exit_code == 0


def test_prompt_time_range_helper_overflow_fallback(tmp_path):
    from crypcodile.cli import prompt_time_range_helper
    
    with patch("crypcodile.store.catalog.Catalog") as mock_catalog_class, \
         patch("typer.prompt") as mock_prompt, \
         patch("typer.echo") as mock_echo:
        
        mock_cat = MagicMock()
        mock_catalog_class.return_value = mock_cat
        mock_cat._registered_channels = ["trade"]
        mock_cat.query.return_value = pl.DataFrame({
            "min_t": [999999999999999999999], # 21 digits, causes overflow
            "max_t": [999999999999999999999]
        })
        
        # Mock user entering a 21-digit start timestamp and an empty end timestamp
        mock_prompt.side_effect = ["999999999999999999999", ""]
        
        start, end = prompt_time_range_helper(
            data_dir=tmp_path,
            channel="trade",
            symbols=["BTCUSDT"],
            default_start=123,
            default_end=456
        )
        
        # With len(val) > 19, parse_time treats the start_input as invalid date format,
        # prints the warning, and returns the fallback.
        # Fallback here is min_ts if min_ts is not None else default_start.
        # Since min_ts is 999999999999999999999, it returns 999999999999999999999.
        assert start == 999999999999999999999
        
        warning_calls = [call for call in mock_echo.call_args_list if "Invalid date format" in call[0][0]]
        assert len(warning_calls) > 0
        assert "999999999999999999999" in warning_calls[0][0][0]

