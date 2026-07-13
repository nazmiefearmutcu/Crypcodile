# Changelog

All notable changes to the **Crypcodile** project will be documented in this file. This project follows [Semantic Versioning](https://semver.org/).

---

## [0.1.044] - 2026-07-14
### Added
- **MCP resolve_symbols tool**: `resolve_symbols` wraps `CrypcodileClient.resolve_symbols` for free-form → canonical catalog symbol resolution over MCP (list or comma-separated input; optional `channel` / `ambiguous=error|first|all`; empty input → `[]`; no match / ambiguous / invalid mode → `{error: ...}`; listed in capabilities `mcp_tools_hint`). Mirrors REST `GET /api/v1/resolve-symbols`.
- **CLI catalog-dates / catalog-symbols / catalog-exchanges**: `crypcodile catalog-dates --channel` lists hive `date=` partitions via `client.list_dates`; `catalog-symbols` lists distinct inventory symbols with optional `--channel` / `--exchange` (empty/whitespace → no filter); `catalog-exchanges` lists on-disk hive `exchange=` partitions via `client.list_exchanges_on_disk`. Empty results print `No dates.` / `No symbols.` / `No exchanges.` and exit 0 — parity with REST/MCP discovery surfaces.
- **MCP list_symbols tool**: `list_symbols` wraps inventory distinct symbols with optional `channel` / `exchange` filters (empty/whitespace → no filter; empty lake → `[]`; listed in capabilities `mcp_tools_hint`). Lighter than `inventory_snapshot`; mirrors REST `GET /api/v1/catalog/symbols`.
- **API catalog symbols discovery**: `GET /api/v1/catalog/symbols?channel=&exchange=` returns sorted distinct inventory symbols (lighter than full inventory rows; empty/whitespace filters → no filter; empty lake → `[]`; listed in capabilities).
- **MCP search_symbols exchange filter**: `search_symbols` accepts optional `exchange` (alongside existing `channel` / `limit`) and forwards it to `client.search_symbols` — parity with REST/CLI/Catalog.
- **API catalog/search channel + exchange filters**: `GET /api/v1/catalog/search` accepts optional `channel` and `exchange` query params (empty/whitespace → no filter; values stripped) and forwards them to `client.search_symbols` — parity with CLI `search` / `Catalog.search_symbols`.
- **CLI catalog-summary**: `crypcodile catalog-summary` prints one-shot lake discovery via client `list_channels` + `list_exchanges_on_disk` (channel/exchange counts and lists; empty lake → zero counts / `(none)`). Mirrors REST `GET /api/v1/catalog/summary` and MCP `catalog_summary`.
- **MCP catalog_summary tool**: `catalog_summary` wraps `list_channels` + `list_exchanges_on_disk` with counts, returning `{channels, exchanges_on_disk, exchange_count, channel_count}` (empty lake → empty lists + zero counts; listed in capabilities `mcp_tools_hint`). Mirrors REST `GET /api/v1/catalog/summary` for agents without HTTP.
- **API catalog summary discovery**: `GET /api/v1/catalog/summary` returns `{channels, exchanges_on_disk, exchange_count, channel_count}` in one call for agent discovery (empty lake → empty lists + zero counts; listed in capabilities). Combines `list_channels` + `list_exchanges_on_disk`.
- **MCP list_exchanges_on_disk tool**: `list_exchanges_on_disk` wraps `CrypcodileClient.list_exchanges_on_disk` for hive `exchange=` partition discovery over MCP (empty lake → `[]`; listed in capabilities `mcp_tools_hint`). Distinct from factory connector registry.
- **API catalog exchanges discovery**: `GET /api/v1/catalog/exchanges` lists distinct hive `exchange=` partitions present on disk (sorted; empty lake → `[]`). Wired through `Catalog.list_exchanges_on_disk` / `CrypcodileClient.list_exchanges_on_disk` and listed in capabilities. Distinct from `GET /api/v1/exchanges` (factory registry of registered connectors).
- **MCP list_dates tool**: `list_dates` wraps `CrypcodileClient.list_dates(channel)` for hive `date=` partition discovery over MCP (strip empty channel → `[]`; listed in capabilities `mcp_tools_hint`).
- **Shared `crypcodile.util.json_safe`**: `json_safe_float` / `json_safe_records` extracted once and re-exported by `api_server` and `mcp_server` (dedupe of prior private copies).

### Changed
- **CLI catalog uses filesystem list_channels**: `crypcodile catalog` discovers channels via client `list_channels` (hive walk) so empty partition dirs appear with `0` rows; `catalog --symbols` remains inventory-backed (parquet/views only) and still works when empty partitions coexist with real data.
- **Catalog.list_channels filesystem discovery**: walks hive `exchange=*/channel=*` without requiring DuckDB view registration, so empty partition dirs (no parquet yet) still appear in channel listings; `_refresh_views` skips empty / relative `channel=` suffixes.
- **Hive partition suffix safety**: shared `_is_safe_hive_suffix` rejects path separators, null/control bytes, glob metacharacters (`* ? [ ]`), relative (`.`, `..`), empty, and leading/trailing whitespace suffixes in `list_channels`, `list_exchanges_on_disk`, `list_dates`, and `_refresh_views`. Channel dirs are resolve-checked under the lake root (symlink escape defence).

### Fixed
- **Catalog empty-partition view registration**: `_create_view` skips channels with no `part-*.parquet` (and swallows race failures) so `Catalog` / client construction no longer raises DuckDB "No files found" when hive `channel=` dirs exist without data — unblocks CLI `catalog` / `catalog --symbols` with filesystem `list_channels`.
- **MCP list[dict] DF JSON safety**: `_json_safe_records` on MCP handlers that return DataFrame row dicts (OFI, slippage, whale alerts, vol suite, basis trio, indicators, depth, sequencer latency, open interest, discovery search/coverage/inventory, MEV sandwiches, smart-money, label-transfers) plus inline `query_market_data` / `get_funding_apr` tools/call paths — NaN/±Inf floats encode as JSON `null` (parity with REST).
- **API POST row-list JSON safety**: `_json_safe_records` applied to `POST /api/v1/simulate-price-impact`, `/smart-money`, and `/label-transfers` so NaN/±Inf floats in returned row dicts encode as JSON `null` (matches lake DF and pure-float REST boundaries).
- **API/MCP pure float JSON safety**: non-finite floats from pure offline analytics are mapped to JSON `null` via `_json_safe_float` at REST/MCP boundaries — covers `chaos-score` (±Inf inputs → NaN score), `peg-deviation` (Inf/NaN price), `funding-predict` (Inf/NaN history), `gas-vol` (undefined correlations), and the prior `lending-stress` zero-debt health-factor case. Prevents Starlette `ValueError: Out of range float values are not JSON compliant`.
- **API/MCP lending-stress JSON safety**: zero-debt health factors (`float('inf')` in pure analytics) are returned as JSON `null` at the REST and MCP boundaries so Starlette/JSON-RPC encoding no longer raises `ValueError: Out of range float values are not JSON compliant`.

### Changed
- **API capabilities discovery lists expanded**: `GET /api/v1/capabilities` `rest` now covers free routes previously missing from the short list (`status`, `capabilities`, `catalog/scan`, `perp-basis`, `spot-future-basis`, whale/slippage/vol suite, base-risk pure endpoints, `funding-predict`, `simulate-price-impact`, etc.); `mcp_tools_hint` aligned with major MCP tools including `get_onchain_price` / `get_base_market_data`. Paid/admin routes still omitted.

### Added
- **API catalog dates discovery**: `GET /api/v1/catalog/dates?channel=` lists distinct hive `date=` partitions for a channel from the filesystem (sorted; empty channel/lake → `[]`; path-safe). Wired through `Catalog.list_dates` / `CrypcodileClient.list_dates` and listed in capabilities.
- **API capabilities endpoint**: `GET /api/v1/capabilities` — free agent discovery returning `{rest, mcp_tools_hint}` hardcoded lists of free REST routes (METHOD + path) and MCP tool names (no payment, no lake; defensive list copies).
- **API ready probe**: `GET /api/v1/ready` — k8s-style readiness (same body as `/api/v1/health`; HTTP **200** when `ok`, **503** when lake unavailable). Prometheus remains at `GET /metrics`.
- **API label-transfers endpoint**: `POST /api/v1/label-transfers` body `{transfers, watchlist, known_only?, min_usd?}` wrapping pure offline `label_transfer_addresses` (+ optional `filter_transfers_by_usd`); empty transfers → `[]`; empty watchlist still returns unlabeled rows; negative `min_usd` → 400.
- **API gas-vol / mev-sandwich / smart-money endpoints**: `POST /api/v1/gas-vol`, `/mev-sandwich`, `/smart-money` pure JSON offline analytics (no lake, no payment).
- **API funding-predict endpoint**: `GET /api/v1/funding-predict` with comma-separated `rates` and optional `window_size` wrapping pure offline `predict_next_funding` (no payment, no lake; empty/invalid rates or window → 400).
- **API health/status endpoints**: `GET /api/v1/health` and alias `GET /api/v1/status` — free lightweight probe returning `ok`, `crypcodile.__version__`, and `lake_channels` count (no payment). Empty lake is still `ok`; `list_channels` failure reports `ok: false` with `lake_unavailable`.
- **API version endpoint**: `GET /api/v1/version` — free meta probe returning `{version}` only (no payment, no lake).
- **API exchanges endpoint**: `GET /api/v1/exchanges` — free listing of sorted registered exchange connector names via `list_exchanges()` (no payment, no lake).
- **API resolve-symbols endpoint**: `GET /api/v1/resolve-symbols` with comma-separated `symbols`, optional `channel`, and `ambiguous=error|first|all` wrapping `CrypcodileClient.resolve_symbols` (list of canonical symbols on success; ambiguous/no-match → 400; no payment).
- **API spot-future-basis endpoint**: `GET /api/v1/spot-future-basis` with `future`/`spot`/`start`/`end`/`limit` wrapping `CrypcodileClient.spot_future_basis` (trade ASOF join; hard row limit 10000; no payment).
- **API perp-basis endpoint**: `GET /api/v1/perp-basis` with `symbol`/`start`/`end`/`limit` wrapping `CrypcodileClient.perp_basis` (mark–index basis; hard row limit 10000; no payment). Skipped bulk `/api/v1/export` lake dump in favor of this bounded analytics surface.
- **MCP get_spot_future_basis tool**: Spot–future basis via ASOF join over MCP (`future_symbol`/`spot_symbol`/`start`/`end`; optional `expiry_ns` for `annualized_pct`). Completes the basis trio with `get_perp_basis` and `get_spot_perp_basis`.
- **MCP label_transfers tool**: Pure offline transfer labeling via `label_transfer_addresses` (`transfers` + `watchlist`; optional `min_usd` / `known_only`).
- **API data-coverage endpoint**: `GET /api/v1/data-coverage` with `symbol` + optional `channel` wrapping inventory filter for per-symbol coverage (read-only, no payment; same contract as MCP `data_coverage`).
- **API open-interest endpoint**: `GET /api/v1/open-interest` with optional symbols, time range, and row limit (read-only lake HTTP surface for OI aggregation).
- **API funding-apr endpoint**: `GET /api/v1/funding-apr` REST endpoint for funding APR analytics.
- **API indicators endpoint**: `GET /api/v1/indicators` wrapping `get_indicators` (symbol/start/end/interval/indicator/period; hard row limit 10000; unknown indicator → 400).
- **API OFI endpoint**: `GET /api/v1/ofi` wrapping `calculate_ofi` (symbol/start/end/interval; hard row limit 10000; invalid interval → 400).
- **API whale-alerts endpoint**: `GET /api/v1/whale-alerts` wrapping `track_whale_alerts` (symbol/start/end/min_usd; hard row limit 10000; negative min_usd → 400).
- **API slippage endpoint**: `GET /api/v1/slippage` wrapping `estimate_slippage` (query: symbol, side, size; validation errors → 400).
- **API spot-perp basis endpoint**: `GET /api/v1/basis` with `spot` + `perp` query params wrapping `spot_perp_basis` (bounded rows, no payment).
- **API vol-skew endpoint**: `GET /api/v1/vol-skew` with `underlying`, `expiry_ns`, `at`, `rate`, `limit` wrapping `CrypcodileClient.vol_skew` (hard row limit 10000).
- **API risk-reversal endpoint**: `GET /api/v1/risk-reversal` with `underlying`, `expiry_ns`, `at`, `rate`, `target_delta` wrapping `vol_skew` then `risk_reversal_butterfly` (returns `risk_reversal` / `butterfly`, nulls when empty).
- **API lending-stress endpoint**: `GET /api/v1/lending-stress` pure query params matching CLI (`collateral_usd`, `debt_usd`, `liquidation_threshold`, `haircut_pct`) wrapping `lending_stress_test`.
- **API base risk analytics endpoints**: `GET /api/v1/liquidity-depth`, `/sequencer-latency`, `/chaos-score`, `/peg-deviation` (lake + pure risk metrics, no payment).
- **API iv-surface and term-structure endpoints**: `GET /api/v1/iv-surface` and `GET /api/v1/term-structure` wrapping client options analytics (hard row limit 10000).
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
- **Blank watchlist address keys**: `normalize_watchlist` and `label_transfer_addresses` drop blank/whitespace address keys and never treat missing/empty transfer sides as labeled (prevents phantom `is_known` from `""` keys).
- **OI symbol filter literal match**: `aggregate_open_interest` uses Polars `str.contains(..., literal=True)` so dots/parens in filter tokens are not regex metacharacters (e.g. `BTC.USDT` no longer matches `BTCXUSDT`); empty/whitespace filter tokens are ignored instead of matching every symbol via `contains("")`.
- **resolve_symbols empty channel**: Empty / whitespace `channel` is treated as no filter (was falsely resolving nothing via inventory filter on unregistered `""`).
- **Catalog inventory empty channel/exchange**: Empty or whitespace `channel`/`exchange` inventory filters are treated as no filter (same contract as `resolve_symbols`), so `channel=""` no longer falsely empties inventory/search.
- **Option expiry parse (OKX/Bybit)**: When the instrument registry has no entry (or no expiry), option normalizers parse the `DDMMMYY` date token from the symbol into midnight-UTC nanoseconds, matching Binance/Deribit behavior.
- **Derive options timestamps in nanoseconds**: Store Derive options `local_ts` / `expiry` in nanoseconds UTC (schema convention); convert on-chain expiry seconds and compute `t_years` from ns.
- **Aave health factor zero**: Treat Aave HF raw `0` as a real zero health factor (underwater), not infinity; only max `uint256` means “no debt” / infinite HF.
- **Catalog search non-positive limit**: `Catalog.search_symbols` returns empty schema for `limit < 1` instead of Polars ``head(-n)`` (which drops the last *n* rows).
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
- **Portal Python backend detection**: Detect the Python API backend via catalog/channels and metrics first; fall back to free `GET /api/v1/ready` 200 (Python readiness), then `GET /api/v1/health` 200, then admin payments including FastAPI JSON 404 when `ADMIN_API_KEY` is unset.
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
- **REST API endpoint matrix**: README documents a brief `/api/v1/*` matrix covering ops/meta, catalog/discovery, market-data, query, derivatives, microstructure, options, and Base/risk routes.

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
