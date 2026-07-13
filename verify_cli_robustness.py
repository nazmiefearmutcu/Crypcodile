import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Ensure local src is in PYTHONPATH
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import typer
import datetime
from crypcodile.cli import (
    app,
    query,
    export,
    replay,
    collect,
    funding_apr_cmd,
    basis_cmd,
    iv_surface_cmd,
    term_structure_cmd,
    prompt_time_range_helper,
    select_collect_params_interactively,
    select_symbols_interactively,
    is_interactive_stdin
)

class TestCliAdversarial(unittest.TestCase):
    def setUp(self):
        # Clear stdout/stderr captures
        self.stdout_capture = io.StringIO()
        self.stderr_capture = io.StringIO()
        self._orig_stdout = sys.stdout
        self._orig_stderr = sys.stderr
        sys.stdout = self.stdout_capture
        sys.stderr = self.stderr_capture

    def tearDown(self):
        sys.stdout = self._orig_stdout
        sys.stderr = self._orig_stderr

    def get_output(self):
        return self.stdout_capture.getvalue(), self.stderr_capture.getvalue()

    @patch("crypcodile.cli.is_interactive_stdin", return_value=False)
    @patch("sys.stdin", io.StringIO("SELECT 42"))
    def test_query_piped_input(self, mock_interactive):
        """1. Test stdin redirect / piping input (e.g. echo "SELECT 42" | crypcodile query)"""
        data_dir = Path("test_data")
        query(sql="", data_dir=data_dir)
        out, err = self.get_output()
        self.assertIn("42", out)

    @patch("crypcodile.cli.is_interactive_stdin", return_value=False)
    @patch("sys.stdin", io.StringIO(""))
    def test_query_piped_empty(self, mock_interactive):
        """1b. Test stdin redirect with empty piped input"""
        data_dir = Path("test_data")
        with self.assertRaises(typer.Exit) as cm:
            query(sql="", data_dir=data_dir)
        self.assertEqual(cm.exception.exit_code, 1)
        out, err = self.get_output()
        self.assertIn("Error: SQL query is required and stdin is empty", out + err)

    @patch("crypcodile.cli.is_interactive_stdin", return_value=False)
    def test_non_interactive_validation_failures(self, mock_interactive):
        """2. Test non-interactive mode validation failures across commands"""
        data_dir = Path("test_data")
        
        # export without channel/symbols
        with self.assertRaises(typer.Exit) as cm:
            export(channel=None, symbols=None, data_dir=data_dir)
        self.assertEqual(cm.exception.exit_code, 1)
        out, err = self.get_output()
        self.assertIn("Error: channel and symbols are required in non-interactive mode", out + err)
        self.stdout_capture.seek(0); self.stdout_capture.truncate()
        self.stderr_capture.seek(0); self.stderr_capture.truncate()

        # replay without channels/symbols
        with self.assertRaises(typer.Exit) as cm:
            replay(channels=None, symbols=None, data_dir=data_dir)
        self.assertEqual(cm.exception.exit_code, 1)
        out, err = self.get_output()
        self.assertIn("Error: channels and symbols are required in non-interactive mode", out + err)
        self.stdout_capture.seek(0); self.stdout_capture.truncate()
        self.stderr_capture.seek(0); self.stderr_capture.truncate()

        # collect without exchange/symbols/channels
        with self.assertRaises(typer.Exit) as cm:
            collect(exchange=None, symbols=None, channels=None, data_dir=data_dir)
        self.assertEqual(cm.exception.exit_code, 1)
        out, err = self.get_output()
        self.assertIn("Error: exchange, symbols, and channels are required in non-interactive mode", out + err)
        self.stdout_capture.seek(0); self.stdout_capture.truncate()
        self.stderr_capture.seek(0); self.stderr_capture.truncate()

        # funding_apr without symbol
        with self.assertRaises(typer.Exit) as cm:
            funding_apr_cmd(symbol=None, data_dir=data_dir)
        self.assertEqual(cm.exception.exit_code, 1)
        out, err = self.get_output()
        self.assertIn("Error: symbol is required in non-interactive mode", out + err)
        self.stdout_capture.seek(0); self.stdout_capture.truncate()
        self.stderr_capture.seek(0); self.stderr_capture.truncate()

        # basis without perp/future/spot
        with self.assertRaises(typer.Exit) as cm:
            basis_cmd(perp=None, future=None, spot=None, data_dir=data_dir)
        self.assertEqual(cm.exception.exit_code, 1)
        out, err = self.get_output()
        self.assertIn(
            "Error: Specify one of: --perp; both --future and --spot; "
            "or both --spot and --perp in non-interactive mode",
            out + err,
        )
        self.stdout_capture.seek(0); self.stdout_capture.truncate()
        self.stderr_capture.seek(0); self.stderr_capture.truncate()

        # basis with perp and future (mutually exclusive)
        with self.assertRaises(typer.Exit) as cm:
            basis_cmd(perp="BTC-PERPETUAL", future="BTC-FUTURE", spot="BTC-SPOT", data_dir=data_dir)
        self.assertEqual(cm.exception.exit_code, 1)
        out, err = self.get_output()
        self.assertIn("Error: --perp and --future are mutually exclusive", out + err)
        self.stdout_capture.seek(0); self.stdout_capture.truncate()
        self.stderr_capture.seek(0); self.stderr_capture.truncate()

        # iv_surface without underlying/at
        with self.assertRaises(typer.Exit) as cm:
            iv_surface_cmd(underlying=None, at=None, data_dir=data_dir)
        self.assertEqual(cm.exception.exit_code, 1)
        out, err = self.get_output()
        self.assertIn("Error: underlying and at snapshot instant are required in non-interactive mode", out + err)
        self.stdout_capture.seek(0); self.stdout_capture.truncate()
        self.stderr_capture.seek(0); self.stderr_capture.truncate()

        # term_structure without underlying/at
        with self.assertRaises(typer.Exit) as cm:
            term_structure_cmd(underlying=None, at=None, data_dir=data_dir)
        self.assertEqual(cm.exception.exit_code, 1)
        out, err = self.get_output()
        self.assertIn("Error: underlying and at snapshot instant are required in non-interactive mode", out + err)

    @patch("crypcodile.cli.is_interactive_stdin", return_value=True)
    def test_date_format_overflow_boundaries(self, mock_interactive):
        """3. Test date format/timestamp overflow boundaries"""
        data_dir = Path("test_data")
        
        # Scenario A: 20-digit overflow input (more than 19 digits)
        # Input: "100000000000000000000", "200000000000000000000"
        with patch("typer.prompt", side_effect=["100000000000000000000", "200000000000000000000"]):
            start_ts, end_ts = prompt_time_range_helper(data_dir, "trade", ["deribit:BTC-PERPETUAL"])
            self.assertEqual(start_ts, 0)
            self.assertEqual(end_ts, 9999999999999999999)
            out, err = self.get_output()
            self.assertIn("Invalid date format", out + err)
            self.stdout_capture.seek(0); self.stdout_capture.truncate()
            self.stderr_capture.seek(0); self.stderr_capture.truncate()

        # Scenario B: Corrupted timestamp string
        # Input: "corrupted-date", "2026-99-99 99:99:99"
        with patch("typer.prompt", side_effect=["corrupted-date", "2026-99-99 99:99:99"]):
            start_ts, end_ts = prompt_time_range_helper(data_dir, "trade", ["deribit:BTC-PERPETUAL"])
            self.assertEqual(start_ts, 0)
            self.assertEqual(end_ts, 9999999999999999999)
            out, err = self.get_output()
            self.assertIn("Invalid date format", out + err)
            self.stdout_capture.seek(0); self.stdout_capture.truncate()
            self.stderr_capture.seek(0); self.stderr_capture.truncate()

        # Scenario C: 19-digit timestamp (valid but extremely large)
        # Input: "1718540000000000000", "9999999999999999999"
        with patch("typer.prompt", side_effect=["1718540000000000000", "9999999999999999999"]):
            start_ts, end_ts = prompt_time_range_helper(data_dir, "trade", ["deribit:BTC-PERPETUAL"])
            self.assertEqual(start_ts, 1718540000000000000)
            self.assertEqual(end_ts, 9999999999999999999)

    def test_collect_wizard_invalid_inputs(self):
        """4a. Test exchange/symbol/channel selection wizards with invalid inputs (digit and non-digit) for collect"""
        # 1. Exchange selection (list_exchanges is sorted: 2=binance):
        # Input sequence:
        # - "99" (invalid exchange index) -> "abc" (invalid exchange name) -> "2" (valid index for binance)
        # - "99" (invalid channel index) -> "abc" (invalid channel name) -> "1" (valid index for trade)
        # - "c" (custom symbol choice) -> "BTC"
        with patch("typer.prompt", side_effect=["99", "abc", "2", "99", "abc", "1", "c", "BTC"]):
            exchange, symbols, channels = select_collect_params_interactively(None, None, None)
            self.assertEqual(exchange, "binance")
            self.assertEqual(channels, ["trade"])
            self.assertEqual(symbols, ["BTCUSDT"]) # Normalized from BTC for binance

    def test_symbol_wizard_invalid_inputs(self):
        """4b. Test exchange/symbol/channel selection wizards with invalid inputs (digit and non-digit) for export/replay"""
        data_dir = Path("test_data")
        
        # 1. Channel selection:
        # Input sequence:
        # - "99" (invalid channel index) -> "1" (valid channel index, which selects book_snapshot since book_snapshot is alphabetically first)
        # 2. Grandma's phone filtering loop for symbols:
        # Choice sequence:
        # - "abc" (filter query "abc" matches nothing, shows no symbols)
        # - "" (empty query loops to retry prompt)
        # - "99" (invalid index)
        # - "1" (selects first symbol)
        with patch("typer.prompt", side_effect=["99", "1"]), \
             patch("crypcodile.cli.prompt_with_autocomplete", side_effect=["abc", "", "99", "1"]):
            channel, symbols = select_symbols_interactively(data_dir, channel=None)
            self.assertEqual(channel, "book_snapshot")
            self.assertEqual(symbols, ["base_onchain:AERO-USDC"])

if __name__ == "__main__":
    unittest.main()
