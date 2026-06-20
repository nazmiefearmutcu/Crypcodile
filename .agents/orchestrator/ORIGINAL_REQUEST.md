# Original User Request

## Follow-up — 2026-06-20T15:39:37Z

Extend the Crypcodile CLI and its interactive shell with three new analytics commands that leverage the existing data lake: a Slippage Estimator, an Order Flow Imbalance (OFI) calculator, and a Whale/Liquidation Alert tracker. Ensure all commands are fully functional, integrated with autocomplete, and covered by unit tests.

## Requirements

### R1. Execution Slippage Estimator (`slippage` Command)
Add a command `slippage` to the CLI and shell:
- Command: `crypcodile slippage --symbol <symbol> --side <buy|sell> --size <amount>`
- It must query the latest order book depth snapshot (`book_snapshot`) from the data lake for the symbol.
- It must walk the bids (for sells) or asks (for buys) to calculate the Volume Weighted Average Price (VWAP) for the given size, comparing it to the best bid/ask to output:
  - Expected execution price (average fill price)
  - Absolute slippage (USD)
  - Percentage slippage (%)

### R2. Order Flow Imbalance (`ofi` Command)
Add a command `ofi` to the CLI and shell:
- Command: `crypcodile ofi --symbol <symbol> --interval <duration>` (e.g., `1m`, `5m`)
- It must calculate the Order Flow Imbalance (OFI) index over time-binned intervals using historical `book_delta` or `book_snapshot` records.
- OFI tracks the supply/demand pressure changes at the best bid and ask levels. The output must display a tabular timeseries showing:
  - Timestamp
  - Best Bid / Best Ask
  - OFI metric value (indicating net buy/sell pressure)

### R3. Whale & Liquidation Alert Tracker (`whale-alerts` Command)
Add a command `whale-alerts` to the CLI and shell:
- Command: `crypcodile whale-alerts --symbol <symbol> --min-usd <value>`
- It must query the `trade` and `liquidation` tables for the given symbol.
- It must filter and print all trade executions and liquidations that exceed the specified USD threshold, showing:
  - Event Time (UTC)
  - Event Type (Trade vs Liquidation)
  - Price & Amount (USD Value)
  - Execution Side (Buy/Sell)

### R4. Shell Integration & Unit Testing
- Register all three commands inside `src/crypcodile/cli.py` so they are fully accessible via the standalone CLI and the interactive `crypcodile shell` (with symbol autocomplete).
- Provide comprehensive automated tests under `tests/` verifying command arguments, data filtering, and calculation correctness.

## Acceptance Criteria

### CLI & Shell Integration
- [ ] Running `crypcodile shell` lists `slippage`, `ofi`, and `whale-alerts` as valid commands in the help/completion list.
- [ ] Commands support interactive symbol selection and autocomplete suggestions from the catalog.

### Correctness of Logic
- [ ] The `slippage` command correctly calculates average fill price and slippage percentages, failing gracefully if the requested size exceeds the total order book depth.
- [ ] The `ofi` command aggregates changes correctly and outputs the timeseries.
- [ ] The `whale-alerts` command correctly filters and displays only events exceeding the USD value threshold.

### Code Quality and Verification
- [ ] All tests run and pass cleanly under `pytest`.
- [ ] No regression of existing tests.
