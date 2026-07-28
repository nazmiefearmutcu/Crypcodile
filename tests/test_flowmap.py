"""The ``flowmap`` launcher, which is hand-written rather than projected.

``flowmap`` opens a PyQt6 window on one symbol's stored book. It has no asset class, no
parameter schema and no provenance, so it is not a capability and lives in
:mod:`crocodile.surfaces.operate` alongside the other six operator commands; the command
table these tests drive is :func:`crocodile.surfaces.entrypoint.build_app`, which is where
the projected commands and the hand-written ones become one app.

What changed from the legacy crypto CLI, and is asserted here in its new form:

* ``--symbol`` is a required option. There is no interactive symbol picker any more, so the
  old "Error: symbol is required" branch is now Typer's own missing-option failure.
* A symbol that is not ``source:RAW`` is refused with exit code 1 rather than guessed at.
  The legacy command ran ``resolve_input_symbols``, which fell back to guessing a venue from
  the channel list; a launcher that opens a window on a different market than the one asked
  for is the failure that guess buys.
* The lake comes from ``Settings`` via ``dispatch.data_dir_for``. ``resolve_data_dir`` and
  its ``test_data`` walk are gone, so there is nothing left to patch for it.
"""

from __future__ import annotations

import pathlib
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from crocodile.surfaces import entrypoint, operate


def test_flowmap_help() -> None:
    """The command is registered on the one app, and publishes its three options."""
    result = CliRunner().invoke(entrypoint.build_app(), ["flowmap", "--help"])
    assert result.exit_code == 0, result.output
    assert "--symbol" in result.output
    assert "--historical-hours" in result.output
    assert "--data-dir" in result.output


def test_flowmap_requires_a_symbol() -> None:
    """No symbol, no window: the option is required rather than prompted for."""
    result = CliRunner().invoke(entrypoint.build_app(), ["flowmap"])
    assert result.exit_code != 0
    assert "--symbol" in result.output


def test_flowmap_refuses_a_symbol_that_is_not_canonical(tmp_path: pathlib.Path) -> None:
    """``BTCUSDT`` names no venue, and the command says so instead of picking one."""
    with patch("multiprocessing.Process") as process:
        result = CliRunner().invoke(
            entrypoint.build_app(),
            ["flowmap", "--symbol", "BTCUSDT", "--data-dir", str(tmp_path)],
        )
    assert result.exit_code == 1, result.output
    assert "not canonical" in result.output
    process.assert_not_called()


@patch("multiprocessing.Process")
def test_flowmap_command_orchestration(mock_process: MagicMock, tmp_path: pathlib.Path) -> None:
    """A canonical symbol starts the GUI child with the symbol, lake and hours it was given."""
    mock_process_instance = MagicMock()
    mock_process.return_value = mock_process_instance

    result = CliRunner().invoke(
        entrypoint.build_app(),
        [
            "flowmap",
            "--symbol", "deribit:BTC-PERPETUAL",
            "--historical-hours", "1.0",
            "--data-dir", str(tmp_path),
        ],
    )

    assert result.exit_code == 0, f"Command output: {result.output}"
    assert "Launched the flowmap window for deribit:BTC-PERPETUAL." in result.output

    mock_process.assert_called_once()
    args, kwargs = mock_process.call_args
    target = kwargs.get("target") or args[0]
    assert target is operate.run_flowmap_gui

    target_args = kwargs.get("args") or args[1]
    assert target_args[0] == "deribit:BTC-PERPETUAL"
    assert target_args[1] == str(tmp_path)
    assert target_args[2] == 1.0

    mock_process_instance.start.assert_called_once()
