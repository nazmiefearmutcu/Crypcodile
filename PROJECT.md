# Project: Bookmap Visualizer CLI & Interactive Shell Command

## Architecture
The Bookmap visualizer runs as a PyQt6 GUI application. It is spawned by a new CLI command (`bookmap`) registered in `src/crypcodile/cli.py` (which also makes it available in the interactive `crypcodile shell`).
To prevent blocking user input in the interactive shell or the CLI prompt, the GUI window runs in a separate thread or process.
The application consumes two data sources:
1. Historical Data: Queried from the local Parquet data lake using the `Catalog` utility (DuckDB views).
2. Live Data Stream: Real-time `BookDelta` and `Trade` events received by subscribing to the live exchange connector.

The GUI itself comprises:
- Depth Heatmap: Displays order book size (liquidity) at various price levels over a rolling time window.
- Cumulative Delta Line Chart: Shows running cumulative volume delta (buys - sells).
- L2 Depth Profile: Vertical sidebar showing current bids and asks depth.
- Trade Bubbles: Circles overlaid on the price chart whose sizes reflect trade volume and colors reflect the execution side (buy/sell).

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| 1 | Exploration & Environment Verification | Verify PyQt6 package availability, Parquet schemas, and data flow. | None | DONE (verified by Conv ID: 007671a8-d99a-444b-8654-a090431075e5) |
| 2 | PyQt6 Bookmap Window Development | Implement the native PyQt6 visual components (Heatmap, Cumulative Delta, L2 depth, Trade bubbles) with dark theme. | M1 | DONE (verified by Conv ID: 50a34f1d-8f53-44b1-a215-4fecb094196f) |
| 3 | CLI & Interactive Shell Command | Register `bookmap` command in `cli.py`, load historical Parquet data, stream live events, and run in separate thread/process. | M2 | DONE (verified by Conv ID: fda6b22d-0f58-4af6-81a3-154f53375566) |
| 4 | Programmatic Verification & Unit Tests | Write `tests/test_bookmap.py` using pytest/pytest-qt, and ensure existing test suite passes cleanly. | M3 | DONE (verified by Conv ID: fda6b22d-0f58-4af6-81a3-154f53375566) |

## Interface Contracts
### CLI ↔ GUI Thread/Process
- When `crypcodile bookmap --symbol <symbol> --historical-hours <hours>` is executed:
  - The CLI thread queries historical snapshot/delta/trade records from `Catalog`.
  - It launches a separate thread or process running the PyQt6 application, passing the historical data.
  - It establishes a live subscription stream for `BookDelta` and `Trade` events, sending them via a thread-safe queue/channel to the GUI thread for real-time visualization updates.

## Code Layout
- `src/crypcodile/cli.py`: Registry for the `bookmap` command.
- `src/crypcodile/gui/bookmap_window.py`: PyQt6 GUI implementation.
- `tests/test_bookmap.py`: Unit and integration tests for the bookmap visualizer.
