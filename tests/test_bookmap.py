from __future__ import annotations

import pathlib
from unittest.mock import MagicMock, patch
import polars as pl
import pytest
from typer.testing import CliRunner

from crypcodile.cli import app, TaskDoneQueueWrapper, QueueSink


def test_bookmap_help() -> None:
    """Verify that the bookmap command is registered and shows help."""
    runner = CliRunner()
    result = runner.invoke(app, ["bookmap", "--help"])
    assert result.exit_code == 0
    assert "--symbol" in result.output
    assert "--historical-hours" in result.output
    assert "--data-dir" in result.output


def test_bookmap_missing_symbol_non_interactive() -> None:
    """Verify that the bookmap command fails if symbol is missing in non-interactive mode."""
    with patch("crypcodile.cli.is_interactive_stdin", return_value=False):
        runner = CliRunner()
        result = runner.invoke(app, ["bookmap"])
        assert result.exit_code != 0
        assert "Error: symbol is required" in result.output


def test_task_done_queue_wrapper() -> None:
    """Verify that TaskDoneQueueWrapper behaves as a safe queue wrapper with task_done."""
    mock_q = MagicMock()
    wrapper = TaskDoneQueueWrapper(mock_q)

    wrapper.put("item")
    mock_q.put.assert_called_once_with("item", True, None)

    wrapper.put_nowait("item2")
    mock_q.put_nowait.assert_called_once_with("item2")

    mock_q.get.return_value = "item3"
    assert wrapper.get() == "item3"
    mock_q.get.assert_called_once_with(True, None)

    mock_q.get_nowait.return_value = "item4"
    assert wrapper.get_nowait() == "item4"
    mock_q.get_nowait.assert_called_once()

    mock_q.empty.return_value = True
    assert wrapper.empty() is True
    mock_q.empty.assert_called_once()

    # task_done must be a safe no-op that doesn't propagate to the mock_q
    wrapper.task_done()
    assert len(mock_q.task_done.mock_calls) == 0


@pytest.mark.asyncio
async def test_queue_sink() -> None:
    """Verify that QueueSink forwards records to the underlying queue."""
    mock_q = MagicMock()
    sink = QueueSink(mock_q)
    await sink.put("test_record")
    mock_q.put.assert_called_once_with("test_record")

    # flush is a no-op
    await sink.flush()


@patch("multiprocessing.Process")
@patch("threading.Thread")
@patch("crypcodile.store.catalog.Catalog")
@patch("crypcodile.cli.resolve_data_dir")
@patch("crypcodile.cli.is_interactive_stdin")
def test_bookmap_command_orchestration(
    mock_is_interactive: MagicMock,
    mock_resolve: MagicMock,
    mock_catalog_class: MagicMock,
    mock_thread: MagicMock,
    mock_process: MagicMock,
) -> None:
    """Verify the entire bookmap command orchestration pipeline:

    - Catalog database range lookups (query/scan)
    - Normalization of bid/ask list of dicts to list of tuples
    - Starting of PyQt6 GUI process
    - Starting of live streaming thread
    """
    mock_is_interactive.return_value = False
    mock_resolve.return_value = pathlib.Path("/dummy/data")

    mock_catalog = MagicMock()
    mock_catalog_class.return_value = mock_catalog

    # Mock catalog.query for max timestamp lookup
    mock_catalog.query.return_value = pl.DataFrame({"max_t": [1700000000000000000]})

    # Mock catalog.scan for historical data channels
    def mock_scan_side_effect(channel: str, symbol: str | list[str], start_ns: int, end_ns: int) -> pl.DataFrame:
        if channel == "book_snapshot":
            return pl.DataFrame({
                "local_ts": [1700000000000000000],
                "bids": [[{"price": 100.0, "amount": 5.0}]],
                "asks": [[{"price": 101.0, "amount": 4.0}]],
            })
        elif channel == "book_delta":
            return pl.DataFrame({
                "local_ts": [1700000000100000000],
                "bids": [[{"price": 100.0, "amount": 0.0}]],
                "asks": [[]],
            })
        elif channel == "trade":
            return pl.DataFrame({
                "local_ts": [1700000000200000000],
                "price": [100.5],
                "amount": [1.5],
            })
        return pl.DataFrame()

    mock_catalog.scan.side_effect = mock_scan_side_effect

    mock_process_instance = MagicMock()
    mock_process.return_value = mock_process_instance

    mock_thread_instance = MagicMock()
    mock_thread.return_value = mock_thread_instance

    runner = CliRunner()
    result = runner.invoke(app, [
        "bookmap",
        "--symbol", "deribit:BTC-PERPETUAL",
        "--historical-hours", "1.0",
        "--data-dir", "/dummy/data"
    ])

    assert result.exit_code == 0, f"Command output: {result.output}"
    assert "Launched bookmap visualizer" in result.output

    # Verify catalog scan range logic: 1.0 hour in ns = 3,600,000,000,000 ns
    # end_ns = 1,700,000,000,000,000,000 -> start_ns = 1,699,996,400,000,000,000
    mock_catalog.scan.assert_any_call("book_snapshot", "deribit:BTC-PERPETUAL", 1699996400000000000, 1700000000000000000)
    mock_catalog.scan.assert_any_call("book_delta", "deribit:BTC-PERPETUAL", 1699996400000000000, 1700000000000000000)
    mock_catalog.scan.assert_any_call("trade", "deribit:BTC-PERPETUAL", 1699996400000000000, 1700000000000000000)

    # Verify process configuration and start call
    mock_process.assert_called_once()
    args, kwargs = mock_process.call_args
    # Target function should be the gui launcher
    target = kwargs.get("target") or args[0]
    assert target.__name__ == "run_bookmap_gui"

    # Verify events contains normalized data
    target_args = kwargs.get("args") or args[1]
    events = target_args[1]
    assert len(events) == 3

    # Check bids/asks list of dicts got normalized to list of tuples
    snap_event = next(e for e in events if e["channel"] == "book_snapshot")
    assert snap_event["bids"] == [(100.0, 5.0)]
    assert snap_event["asks"] == [(101.0, 4.0)]

    delta_event = next(e for e in events if e["channel"] == "book_delta")
    assert delta_event["bids"] == [(100.0, 0.0)]
    assert delta_event["asks"] == []

    # Check that events are sorted chronologically
    assert events[0]["channel"] == "book_snapshot"
    assert events[1]["channel"] == "book_delta"
    assert events[2]["channel"] == "trade"

    mock_process_instance.start.assert_called_once()

    # Verify thread configuration and start call
    mock_thread.assert_called_once()
    t_args, t_kwargs = mock_thread.call_args
    t_target = t_kwargs.get("target") or t_args[0]
    assert t_target.__name__ == "run_live_feeder"
    
    # Check thread target arguments
    t_args_val = t_kwargs.get("args") or t_args[1]
    assert t_args_val[0] == "deribit"
    assert t_args_val[1] == "BTC-PERPETUAL"

    mock_thread_instance.start.assert_called_once()
