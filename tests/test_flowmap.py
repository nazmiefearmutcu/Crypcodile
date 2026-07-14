from __future__ import annotations

import pathlib
from unittest.mock import MagicMock, patch
import pytest
from typer.testing import CliRunner

from crypcodile.cli import app


def test_flowmap_help() -> None:
    """Verify that the flowmap command is registered and shows help."""
    runner = CliRunner()
    result = runner.invoke(app, ["flowmap", "--help"])
    assert result.exit_code == 0
    assert "--symbol" in result.output
    assert "--historical-hours" in result.output
    assert "--data-dir" in result.output



def test_flowmap_missing_symbol_non_interactive() -> None:
    """Verify that the flowmap command fails if symbol is missing in non-interactive mode."""
    with patch("crypcodile.cli.is_interactive_stdin", return_value=False):
        runner = CliRunner()
        result = runner.invoke(app, ["flowmap"])
        assert result.exit_code != 0
        assert "Error: symbol is required" in result.output



@patch("multiprocessing.Process")
@patch("crypcodile.cli.resolve_data_dir")
@patch("crypcodile.cli.is_interactive_stdin")
def test_flowmap_command_orchestration(
    mock_is_interactive: MagicMock,
    mock_resolve: MagicMock,
    mock_process: MagicMock,
) -> None:
    """Verify flowmap command triggers the PyQt6 GUI process."""
    mock_is_interactive.return_value = False
    mock_resolve.return_value = pathlib.Path("/dummy/data")

    mock_process_instance = MagicMock()
    mock_process.return_value = mock_process_instance

    runner = CliRunner()
    result = runner.invoke(app, [
        "flowmap",
        "--symbol", "deribit:BTC-PERPETUAL",
        "--historical-hours", "1.0",
        "--data-dir", "/dummy/data"
    ])

    assert result.exit_code == 0, f"Command output: {result.output}"
    assert "Launched flowmap visualizer" in result.output

    # Verify process configuration and start call
    mock_process.assert_called_once()
    args, kwargs = mock_process.call_args
    target = kwargs.get("target") or args[0]
    assert target.__name__ == "run_flowmap_gui"

    target_args = kwargs.get("args") or args[1]
    assert target_args[0] == "deribit:BTC-PERPETUAL"
    assert target_args[1] == "/dummy/data"
    assert target_args[2] == 1.0

    mock_process_instance.start.assert_called_once()

