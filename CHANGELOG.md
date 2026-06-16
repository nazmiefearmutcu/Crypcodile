# Changelog

All notable changes to the **Crypcodile** project will be documented in this file. This project follows [Semantic Versioning](https://semver.org/).

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
