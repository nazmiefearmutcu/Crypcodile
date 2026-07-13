# Changelog

All notable changes to the **Crypcodile** project will be documented in this file. This project follows [Semantic Versioning](https://semver.org/).

---

## [0.1.044] - 2026-07-13
### Added
- **API open-interest endpoint**: `GET /api/v1/open-interest` with optional symbols, time range, and row limit (read-only lake HTTP surface for OI aggregation).
- **API funding-apr endpoint**: `GET /api/v1/funding-apr` REST endpoint for funding APR analytics.
- **API indicators endpoint**: `GET /api/v1/indicators` wrapping `get_indicators` (symbol/start/end/interval/indicator/period; hard row limit 10000; unknown indicator → 400).
- **API OFI endpoint**: `GET /api/v1/ofi` wrapping `calculate_ofi` (symbol/start/end/interval; hard row limit 10000; invalid interval → 400).
- **API whale-alerts endpoint**: `GET /api/v1/whale-alerts` wrapping `track_whale_alerts` (symbol/start/end/min_usd; hard row limit 10000; negative min_usd → 400).
- **API slippage endpoint**: `GET /api/v1/slippage` wrapping `estimate_slippage` (query: symbol, side, size; validation errors → 400).
- **API spot-perp basis endpoint**: `GET /api/v1/basis` with `spot` + `perp` query params wrapping `spot_perp_basis` (bounded rows, no payment).
- **MCP MEV sandwich detection tool**: Expose MEV sandwich detection over MCP.
- **MCP chaos-score tool**: Base risk / chaos scoring available as an MCP tool.
- **MCP get_peg_deviation tool**: Pure `peg_deviation_from_price` over MCP (`price` required; optional `threshold`/`target`; no data lake).
- **MCP get_lending_stress tool**: Pure numeric lending health-factor stress test over MCP (wraps `lending_stress_test`).
- **Derive poll connector factory registration**: Register the Derive poll connector in the exchange factory registry.
- **Catalog inventory and ranked symbol search**: Store-layer inventory listing and ranked symbol search over the data catalog.
- **Search system (client, CLI, MCP)**: Client `resolve_symbols`, CLI search, and MCP discovery tools for symbol resolution.
- **MCP analytics tools**: Exposed slippage, OFI, whale alerts, IV surface, and term-structure tools over MCP.
- **MCP vol-skew tool**: Added `get_vol_skew` analytics tool on the MCP server.
- **MCP basis analytics tools**: Exposed basis analytics tools over MCP.
- **MCP liquidity-depth and sequencer-latency tools**: Liquidity-depth and sequencer-latency analytics available as MCP tools.
- **Client/MCP indicators surface**: Technical analysis indicators exposed via client API and MCP.
- **CLI vol-skew and risk-reversal**: New `vol-skew` and risk-reversal commands for options skew analytics.
- **CLI base risk analytics**: Exposed base risk analytics commands on the CLI.
- **CLI liquidity-depth command**: Liquidity-depth analytics command on the CLI.
- **CLI sequencer-latency command**: Sequencer-latency analytics command on the CLI.
- **CLI lending-stress command**: Expose pure `lending_stress_test` as `crypcodile lending-stress` with collateral/debt/threshold/haircut options.
- **CLI collect duration and max-reconnects**: Collect accepts duration and max-reconnects options for bounded runs and reconnect caps.
- **Public `list_exchanges`**: Factory-registry-backed public exchange listing for CLI/API consumers.
- **API lake catalog list and search**: Lake catalog list and search endpoints on the API.
- **API lake catalog scan**: Wire `GET /api/v1/catalog/scan` to client scan with a hard row limit (10000).
- **API lake catalog inventory**: Lake catalog inventory endpoint on the API.
- **API bounded read-only SQL query**: Bounded read-only SQL / lake query endpoint for safe HTTP reads.
- **MCP funding prediction tool**: Funding-rate prediction analytics available as an MCP tool.
- **CLI funding-predict command**: Offline `funding-predict` CLI via rates or file (XGBoost when trainable, rolling-mean fallback).
- **CLI multi-exchange collect**: Collect across multiple exchanges in a single CLI invocation.
- **Superchain connector factory registration**: Register the superchain on-chain connector in the exchange factory registry.
- **Dead-letter queue drain on collect stop**: Drain the ingest dead-letter queue when collect stops and emit a stop report.
- **Book resync bridge (Binance)**: On depth sequence gaps, buffer live deltas, REST re-fetch `/depth`, and emit snapshot plus post-snapshot deltas via `OrderBookSync` + `BookResyncBridge`.
- **Book resync bridge (OKX)**: On `books` `seqId`/`prevSeqId` gaps, buffer live deltas, REST re-fetch `/market/books`, and emit snapshot plus post-snapshot deltas via `OkxOrderBookSync` + `BookResyncBridge` (WS snapshot bootstrap preferred; register after successful bootstrap).
- **Shared book-sync helpers**: Extracted `SyncResult`, `BookSyncMachine` protocol, and buffer filter for multi-venue resync.
- **Smart-money / whale-transfer CLI**: CLI surface for smart-money and whale-transfer analytics.
- **CLI backfill command**: Historical REST backfill command with client-side backfill orchestration.
- **CLI chaos-score command**: New `chaos-score` command for base risk / chaos scoring.
- **CLI spot-perp basis mode**: True spot–perp basis via `--spot X --perp Y` (ASOF join of spot vs perp mark); keep `--perp` alone as mark/index and `--future`/`--spot` as spot–future.

### Fixed
- **Atomic parquet compact**: Compact uses rename-before-delete; in-flight work is awaited on stop; compact executor is awaited across start/stop cancel paths.
- **Atomic parquet part writes**: Parquet part files written via temp path then atomic rename for crash-safe durability.
- **Parquet sink buffer durability**: Drop sink buffer only after durable write; re-buffer rows when a flush is cancelled.
- **Multi-partition rebuffer**: After a partial multi-partition flush, re-buffer only partitions that were not durably written.
- **Partition path sanitization**: Sanitize parquet partition path components; validate catalog scan limits and escape channel IDs.
- **API payment CAS and sim defaults**: Compare-and-swap payment spend before serve; disable simulation by default; lock admin behind admin key.
- **Atomic fail-loud payment DB persistence**: Payment DB writes are atomic and fail loudly on persistence errors.
- **Pending-only paid transitions**: Enforce pending-only transitions to paid for verify and simulate payment flows.
- **MCP stdin EOF**: Exit cleanly on stdin EOF without hanging the executor.
- **Polars min_samples**: Update analytics from deprecated `min_periods` to `min_samples`.
- **WebSocket connect session leak**: Close the aiohttp session when WebSocket connect fails.
- **Binance book bridge bootstrap**: Register the book resync bridge only after a successful bootstrap.
- **Whitespace-only catalog search**: Treat whitespace-only search queries as empty.
- **Portal Python backend detection**: Detect the Python API backend via catalog/channels and metrics first; fall back through admin payments including FastAPI JSON 404 when `ADMIN_API_KEY` is unset.
- **Payment refund on serve failure**: Restore paid status when market-data serve fails after payment CAS spend.
- **Multi-symbol OI exchange overwrite**: Keep multi-symbol open interest without clobbering exchange identity across symbols.
- **Read-only SQL query hardening**: Harden the bounded read-only SQL / lake query API endpoint.
- **Superchain identity and recovery state**: Fix superchain connector identity and per-exchange recovery state isolation.
- **Seen logs cursor advances**: Persist on-chain seen logs together with cursor advances so restarts do not reprocess.
- **IV fit without underlying price**: Skip IV surface fit when underlying price is missing instead of failing the fit path.
- **Gas–vol correlation asof align**: ASOF-align gas and vol series before correlation so mismatched timestamps do not skew results.
- **Null OI samples in aggregation**: Skip null open-interest samples during OI aggregation.

### Changed
- **CLI symbol resolution**: Resolve symbols via `client.resolve_symbols` for consistent catalog-backed lookup.
- **Bybit book resync deferred**: Shared book-sync helpers land for multi-venue use; Bybit `BookResyncBridge` wiring deferred (REST `u` aligns with `orderbook.1000` while the connector uses `orderbook.50`; recovery remains re-snapshot/re-subscribe).
- **Exchange list alignment**: Align CLI exchange lists with the factory registry.
- **CLI exchange lists via registry**: Collect help and interactive suggestions include `derive` and `superchain` via `list_exchanges()`.
- **Search docs**: Document search and discovery commands in the README.

## [0.1.043] - 2026-07-09
### Added
- **Technical Analysis Indicators Engine**: Implemented SMA, EMA, RSI, MACD, and Bollinger Bands calculated using high-performance Polars operations.
- **CLI Subcommand (`indicators` command)**: Added a command to compute and display technical analysis indicators from resampled OHLCV bar data.
- **Client Resampling Interface**: Added the `resample` method to `CrypcodileClient`.
- **Unit Testing**: Implemented complete testing coverage for indicator calculations and CLI commands.

## [0.1.042] - 2026-06-23
### Added
- **Base L2 Ecosystem Integrations**: Integrated asset registry, DEX pool event listeners, OP stack standardization, L1/L2 gas schemas, resilient async web3 client, BNS resolution, Farcaster sentiment correlation, smart wallet tagging, and Seamless/Aave lending logs.
- **Advanced On-Chain Ingest Engine**: Rust log decoder, dynamic basescan/sourcify ABI registry caching, node recovery rollback buffers, and MEV sandwich filter.
- **Advanced Analytics & Modeling**: Options Greeks solver, Lyra options chain builder, GMX/Synthetix position tracker, Open Interest aggregator, basis analyzer, and XGBoost funding rate predictor.

## [0.1.041] - 2026-06-20
### Added
- **Execution Slippage Estimator (`slippage` command)**: Added a command to walk bid/ask depth levels and compute Expected Execution Price (VWAP), Absolute Slippage (USD), and Percentage Slippage (%) from book snapshots.
- **Order Flow Imbalance (`ofi` command)**: Introduced a command calculating time-binned Order Flow Imbalance (OFI) metrics from historical bids/asks changes.
- **Whale Alerts Tracker (`whale-alerts` command)**: Added a tracker to filter and display trades and liquidations exceeding a USD threshold.
- **CLI & Shell Integration**: Integrated all three commands into the interactive `crypcodile shell` with symbol autocomplete support.
- **Unit Testing**: Implemented complete testing coverage for the new commands under `tests/analytics/test_analytics_new.py`.

## [0.1.040] - 2026-06-20
### Added
- **PyQt6 Bookmap Visualizer**: Introduced a high-performance native macOS graphical window showcasing log-scale order book depth heatmap, cumulative volume delta, vertical L2 depth profile sidebar, and volume-weighted trade bubble overlays.
- **Visual CLI Command & Shell Integration**: Integrated `bookmap` command to the main Crypcodile CLI and interactive prompt shell, running non-blocking background visual processes using multiprocessing and thread-safe WebSocket connections.
- **GUI and CLI Unit Testing**: Added automated unit tests under `tests/test_bookmap.py` and `tests/gui/test_bookmap_window.py` verifying data ingestion, layout integrity, and GUI event loop execution.

## [0.1.039] - 2026-06-18
### Changed
- **Query piped multiline**: Enabled non-interactive queries to read from stdin.
- **Non-interactive prompt bypass**: Added check to exit 1 if required parameters are missing in non-interactive environment.
- **Shell on non-TTY**: Enabled fallback to standard Python `input()` on non-TTY.
- **Shell subcommand exit resilience**: Allowed click.exceptions.Exit to be handled gracefully without crashing the shell.
- **Sparkline verification**: Filtered non-finite floats out from make_sparkline input.
- **Param selector validation**: Enhanced wizard checks for digit index selection and custom string selection.
- **Upgrade output capture**: Captured pip upgrade output and printed details on failure.
- **Semantic version comparison**: Used packaging.version.Version with try-except fallback.
- **Basis mutual exclusivity**: Enforced mutually exclusive options and implicit mode configuration for basis command.
- **Exception wrapping**: Wrapped client query and funding_apr to show clean error messages without DuckDB tracebacks.
- **Pruned option scans**: Optimized distinct scans by checking latest date partition directory first.
- **Safe timestamp formatting**: Wrapped options snapshot datetime conversion in try-except to avoid corrupt metadata crashes.
- **Uvicorn import check**: Handled missing uvicorn library with clean error in API server fallback.
- **Empty DataFrame export**: Built schema-rich empty Polars DataFrame based on msgspec fields for empty exports.

## [0.1.038] - 2026-06-18
### Changed
- **Premium UI/UX Design**: Overhauled dashboard look with glowing ambiance backdrops, Plus Jakarta Sans typography, and sleek card hovers.
- **SSE Connection Resilience**: Resolved infinite "Awaiting Price Feed Ticks" spinner when backend is offline by providing local simulation ticks fallback.
- **Transaction Debugger Fix**: Fixed missing payment_id in SSE verification payloads, enabling block confirmation steps to successfully complete (turn green).

## [0.1.037] - 2026-06-18
### Changed
- **Unified API Portal**: Integrated Node.js Express dashboard with Python FastAPI backend, serving identical static templates.
- **SSE Tick Feeds**: Added `/api/events` price tick support on python server.
- **Glassmorphic Design**: Overhauled stylesheet to adopt premium visuals, Outfit/Inter typography, and hover micro-animations.
- **E2E Compatibility**: Ensured full test compatibility for 117 E2E UI assertions.

## [0.1.013] - 2026-06-16
### Changed
- **Logo Mascot Design**: Updated the crocodile mascot logo to use a new, friendly side-profile design with scaled ridges.

---

## [0.1.012] - 2026-06-16
### Changed
- Release updates.

---

## [0.1.011] - 2026-06-16
### Changed
- Release updates.

---

## [0.1.010] - 2026-06-16
### Fixed
- **Logo Presentation**: Suppressed logo display on the `update` command screen.

---

## [0.1.009] - 2026-06-16
### Added
- **CLI Logo Display**: Configured the shaded crocodile logo to always be displayed when running the CLI interactively in TTY terminal environments. The logo output is routed to `sys.stderr` to keep command pipelining and data redirection of `stdout` clean and unpolluted.

---

## [0.1.008] - 2026-06-16
### Changed
- **Logo Mascot Refinement**: Updated the installer logo to use a premium, custom-shaded ASCII art representation of a crocodile as provided.

---

## [0.1.007] - 2026-06-16
### Changed
- **Mascot Styling**: Redesigned the crocodile logo to be a friendly, cute, cartoon-like chubby crocodile, removing all aggressive features and completely eliminating any resemblance to a weapon.

---

## [0.1.006] - 2026-06-16
### Changed
- **Logo Mascot Refinement**: Replaced the previous crocodile ASCII art with a clearer, more distinct horizontal crocodile design (featuring ridges, claws, and open jaws) to prevent any visual resemblance to a weapon.

---

## [0.1.005] - 2026-06-16
### Changed
- **Visual Theme & Logo**: Redesigned the installer branding to use a premium dark green theme. Integrated a crocodile ASCII art mascot above the text logo in the installation scripts.

---

## [0.1.004] - 2026-06-16
### Changed
- **Clean Installer Output**: Refactored `install.sh` and `install.ps1` to adopt a clean, minimal layout with a progress status checklist. All verbose terminal installation logs are redirected to a temporary file and only shown in case of a step failure.

---

## [0.1.003] - 2026-06-16
### Changed
- **CLI Update Presentation**: Cleaned up the `update` command output to show a minimal, premium status progress indicator and hide verbose pip installation logs from the user.

---

## [0.1.002] - 2026-06-16
### Added
- **Smart Update Verification**: The `crypcodile update` command now performs a remote version check against GitHub tags before downloading, warning users if they are already on the latest version. Included a `--force` flag to force reinstallation.

---

## [0.1.001] - 2026-06-16
### Added
- **`update` command**: Added the `crypcodile update` command to upgrade the CLI globally inside its virtual environment directly from GitHub.

### Changed
- **CLI Behavior**: Changed default behavior to show the help menu instead of printing "Missing command" when no subcommand is provided.
- **Dynamic Versioning**: Centralized versioning around package `__version__` across CLI, MCP, and API servers.

---

## [0.1.0] - 2026-06-15
### Added
- **Base L2 On-Chain Integration**: Fully integrated live polling and swap event decoding for **Aerodrome Finance** (Base's largest DEX) and **Uniswap V3 on Base**.
- **Model Context Protocol (MCP) Server**: Exposes standard AI-agent tools including `get_base_market_data` to fetch live price, reserves, and 1-hour volume.
- **Interactive Farcaster Frame**: Added an example Farcaster Frame server at `examples/farcaster_frame.py` for direct, interactive analytics inside Farcaster client feeds.
- **Streamlit Live Dashboard**: Added a premium frontend dashboard at `examples/base_dashboard.py` showcasing real-time pool metrics and price charts.
- **x402 Micropayment Protocol**: Added FastAPI endpoints gated by USDC transaction log verification on Base mainnet.

### Fixed
- Fixed critical race conditions in custom pools IPC disk IO using `asyncio.to_thread` for non-blocking file locking.
- Resolved head-of-line blocking in the polling loop by making RPC queries run concurrently.
- Fixed Streamlit `unsafe_allow_html` keyword argument naming.

---

## [0.0.9] - 2026-05-28
### Added
- **DuckDB Optimization**: Fine-tuned query engine for sub-millisecond execution times over historical Parquet files.
- **Multi-threaded IPC Dict**: Moved heavy file I/O operations out of the main asyncio event loop.
- **Ecosystem Integration**: Early beta integration with custom Telegram bots and Discord tickers.

### Changed
- Refactored options IV surface calculations for improved numerical stability under high volatility conditions.

---

## [0.0.5] - 2026-05-04
### Added
- **Centralized Exchanges (CEX)**: Added live WebSocket support and REST backfill connectors for **Bybit**, **OKX**, and **Coinbase**.
- **Schema Expansion**: Added standardized structs for `Liquidation` and `OpenInterest` channels.
- Comprehensive integration test suites checking exchange normalizer coverage.

---

## [0.0.2] - 2026-04-18
### Added
- **Parquet Storage Engine**: Implemented hive-partitioned zstd compression storage layer.
- **K-Way Merge Replay**: Reconstruct orderbook states deterministically from combined snapshot and delta files.

---

## [0.0.1] - 2026-04-04
### Added
- Initial prototype release.
- Core async WebSocket connector skeleton and standard `Record` schemas (`Trade`, `BookSnapshot`, `BookDelta`, `BookTicker`).
- Base connector for Deribit options and Binance spot/futures.
