## 2026-06-18T14:55:24Z
Please go to `/Users/nazmi/Crypcodile/src/crypcodile/api_portal` and run the tests. You can do this by running `node tests/e2e.test.js` or `npm test`. Report the results back to me, listing how many tests passed/failed. Do not change any files yet.

## 2026-06-20T13:48:24Z
Enhance the Crypcodile CLI and its interactive shell with a Bookmap-like native macOS visualization window. The tool should display order book depth, cumulative delta, trade bubbles, and the current L2 order book profile. It must load historical data from the Parquet data lake first and then seamlessly switch to streaming live data updates in real-time, without blocking the terminal command prompt.

Working directory: /Users/nazmi/Crypcodile
Integrity mode: development

## Requirements

### R1. PyQt6-based Bookmap Visual Window
Create a beautiful, macOS-friendly native desktop window using `PyQt6` (or `PySide6`) that displays:
- **Order Book Depth Heatmap**: A price-vs-time grid or rolling plot where cell colors represent order book size (liquidity) at that price.
- **Cumulative Delta Line Chart**: A time-series chart showing the running cumulative volume delta (buy volume - sell volume) from trades.
- **L2 Depth Profile**: A vertical sidebar showing horizontal bars for current bids and asks depth.
- **Trade Bubbles**: Overlay circles on the price chart corresponding to trade executions, where size represents trade volume, and color represents buy/sell side.

### R2. CLI and Interactive Shell Command
Add a new CLI command to `src/crypcodile/cli.py` (e.g., `bookmap` or similar name):
- It must be available both as a direct CLI command and within the interactive `crypcodile shell`.
- It must accept options for symbol and duration/history parameters (e.g. `--symbol`, `--historical-hours`).
- Running the command must retrieve historical data from the local Parquet data lake (using the `Catalog` or DuckDB view query) to populate the visualizer's initial historical chart.
- It must subscribe to or launch the live connector to apply real-time `BookDelta` and `Trade` events to update the window dynamically.
- The command must open the window in a separate thread/process so that the interactive shell does not freeze or block user input.

### R3. macOS Friendly Experience & Styling
- The GUI must use a premium dark theme, modern color palettes, and standard window frames.
- It must support resizing, panning, and zooming without freezing or crashing, and must be responsive on macOS.

### R4. Programmatic Verification & Unit Tests
- Provide automated unit tests (e.g., in `tests/test_bookmap.py`) using `pytest` and `pytest-qt` or standard mocking to verify the CLI argument parsing, data ingestion logic, and GUI window initialization.

## Acceptance Criteria

### CLI Shell Integration
- [ ] Running the interactive shell (`crypcodile shell`) lists the new command under the available commands or help text.
- [ ] Running the new command from the shell opens the GUI window and immediately returns control to the shell prompt, letting the user continue typing commands.

### GUI Completeness & Visuals
- [ ] The GUI window displays the order book depth heatmap, cumulative delta chart, L2 profile, and trade bubbles.
- [ ] The charts update correctly when historical data is loaded and new streaming deltas/trades arrive.
- [ ] Resizing or interacting with the window on macOS does not trigger standard beachballing, lock-ups, or crashes.

### Tests and Code Quality
- [ ] All newly added tests pass cleanly under the project's pytest environment (`pytest tests/`).
- [ ] Existing project tests under `tests/` continue to pass without regression.
