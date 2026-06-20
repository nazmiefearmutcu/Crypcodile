# Project Plan: Crypcodile Analytics Commands Integration

## Architecture
The new analytics commands are added to `src/crypcodile/cli.py` using the `typer` framework and are registered to the main Typer application (`app`). This automatically makes them available in the CLI and the interactive `crypcodile shell`.
The business logic for calculations will be implemented in dedicated analytics modules inside `src/crypcodile/analytics/`:
1. `src/crypcodile/analytics/slippage.py`: Walks the latest order book snapshot to calculate expected execution price, absolute slippage, and percentage slippage.
2. `src/crypcodile/analytics/ofi.py`: Computes the Order Flow Imbalance (OFI) index over time-binned intervals using `book_delta` or `book_snapshot` records.
3. `src/crypcodile/analytics/whale.py`: Filters trades and liquidations exceeding a specified USD threshold.

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| 1 | Plan & Codebase Research | Analyze current `cli.py`, table schemas, autocomplete setup, and catalog APIs. | None | IN_PROGRESS |
| 2 | Implementation of Slippage Estimator | Create `slippage.py`, write core VWAP and slippage logic, and add unit tests. | M1 | PLANNED |
| 3 | Implementation of Order Flow Imbalance | Create `ofi.py`, implement time-binned OFI calculation, and add unit tests. | M2 | PLANNED |
| 4 | Implementation of Whale Alerts Tracker | Create `whale.py` or similar helper, query `trade` & `liquidation` tables, and add unit tests. | M3 | PLANNED |
| 5 | CLI & Shell Integration | Register commands in `src/crypcodile/cli.py`, verify interactive shell autocomplete, run final verification. | M4 | PLANNED |

## Interface Contracts
- **Slippage Calculator**:
  - Input: `catalog: Catalog`, `symbol: str`, `side: str`, `size: float`
  - Output: `dict` containing expected execution price, absolute slippage, and percentage slippage, or raises an error if size exceeds total depth.
- **OFI Indexer**:
  - Input: `catalog: Catalog`, `symbol: str`, `interval: str` (e.g. `1m`, `5m`)
  - Output: `pl.DataFrame` with `timestamp`, `best_bid`, `best_ask`, and `ofi` metric columns.
- **Whale Alert Tracker**:
  - Input: `catalog: Catalog`, `symbol: str`, `min_usd: float`
  - Output: `pl.DataFrame` or `list[dict]` containing `event_time`, `event_type`, `price`, `amount`, `usd_value`, and `side`.

## Code Layout
- `src/crypcodile/analytics/slippage.py`: Slippage logic.
- `src/crypcodile/analytics/ofi.py`: OFI logic.
- `src/crypcodile/analytics/whale.py`: Whale/liquidation filtering.
- `src/crypcodile/cli.py`: Register CLI commands.
- `tests/analytics/test_analytics_new.py`: Automated tests for the new commands and logic.
