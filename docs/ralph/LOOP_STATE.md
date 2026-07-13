# Crypcodile Ralph Continuous Development Loop

**Started:** 2026-07-13  
**Mode:** Infinite until user stops  
**Branch:** `ralph/continuous-dev`  
**Base:** `66b44af`  
**Version:** `0.1.044`  
**Rotation:** Bug hunt → Feature → Hardening → Feature → …  
**Status:** Waves 1–59 COMPLETE. Continuous loop still active → Wave 60+.

## Wave 1 — Bug hunt — COMPLETE

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1 | Compactor atomic rename-before-delete | DONE | `1a60834` |
| 1b | Compactor stop cancel await | DONE + APPROVED | `747c217` |
| 2 | Payment CAS before serve | DONE + APPROVED | `5813278` |
| 2b | pending-only → paid | DONE + APPROVED | `23442c0` |
| 3 | ALLOW_SIMULATION default false; admin lock | DONE (in 5813278) | |
| 4 | MCP stdin EOF (no executor hang) | DONE | `1a60834` |

## Wave 2 — Search system — COMPLETE

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1–2 | Catalog inventory + search_symbols | DONE + APPROVED | `a19228f` |
| 3–5 | Client + CLI + MCP discovery | DONE + APPROVED | `6fe12a5` |
| 6 | CLI resolve via client | DONE | `1a60834` |

## Wave 3 — Hardening + surfaces — COMPLETE

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1 | ParquetSink buffer after durable write | DONE | `1a60834` |
| 1b | Re-buffer on CancelledError | DONE | `1a60834` |
| 2 | Path traversal sanitize partitions | DONE | `1a60834` |
| 3 | MCP analytics pack (slippage, ofi, whale, iv, term) | DONE | `1a60834` |
| 4 | CLI vol-skew / risk-reversal | DONE | `1a60834` |
| 5 | Polars min_periods → min_samples | DONE | `1a60834` |

## Wave 4 — Feature (analytics + catalog) — COMPLETE

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1 | Catalog scan limit + escape channel ids | DONE | `1a60834` |
| 2 | CLI base risk analytics commands | DONE | `1a60834` |
| 3 | MCP get_vol_skew analytics tool | DONE | `1a60834` |

## Wave 5 — Bug hunt / ingest reliability — COMPLETE

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1 | Drain dead-letter queue on collect stop + report | DONE | `1a60834` |
| 2 | Close aiohttp session when ws connect fails | DONE | `1a60834` |
| 3 | Changelog + version bump 0.1.044 | DONE | `1a60834`, `15acaa1` |

## Wave 6 — Feature (book resync bridge) — COMPLETE

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1 | Wire book resync bridge on sequence gap (Binance) | DONE | `1a60834` |

## Wave 7 — Feature (docs + smart-money surface) — COMPLETE

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1 | Document search/discovery commands in README | DONE | `1a60834` |
| 2 | Align CLI exchange lists with factory registry | DONE | `1a60834` |
| 3 | CLI smart-money / whale-transfer surface | DONE | `1a60834` |

## Wave 8 — Feature (CLI backfill + chaos + basis) — COMPLETE

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1 | Binance book bridge only after successful bootstrap | DONE | `1a60834` |
| 2 | CLI backfill command (historical REST) | DONE | `1a60834` |
| 3 | CLI chaos-score command | DONE | `1a60834` |
| 4 | CLI spot-perp basis mode (`--spot X --perp Y`) | DONE | `1a60834` |

## Wave 9 — Hardening + docs (search + ingest notes) — COMPLETE

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1 | Bybit book resync deferred (shared helpers only) | DONE | `1a60834` |
| 2 | Whitespace-only catalog search treated as empty | DONE | `1a60834` |
| 3 | Expand 0.1.044 changelog with wave 7–9 work | DONE | `1a60834` |

## Wave 10 — Feature (API catalog + liquidity + sequencer + MCP basis) — COMPLETE

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1 | REST API lake catalog list and search endpoints | DONE | `1a60834` |
| 2 | CLI liquidity-depth command | DONE | `1a60834` |
| 3 | CLI sequencer-latency command | DONE | `1a60834` |
| 4 | MCP basis analytics tools | DONE | `1a60834` |

## Wave 11 — Hardening + indicators surface — COMPLETE

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1 | Multi-partition sink: re-buffer only unwritten partitions after partial flush | DONE | `1a60834` |
| 2 | Client/MCP technical indicators surface | DONE | `1a60834` |

## Wave 12 — Feature (exchanges + collect + lending + MCP depth/seq) — COMPLETE

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1 | Public `list_exchanges` from factory registry | DONE | `1a60834` |
| 2 | CLI collect `--duration` and `--max-reconnects` | DONE | `1a60834` |
| 3 | CLI lending-stress command | DONE | `1a60834` |
| 4 | MCP liquidity-depth and sequencer-latency tools | DONE | `1a60834` |

## Wave 13 — Hardening (store + payments + changelog) — COMPLETE

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1 | Atomic temp-rename for parquet part writes | DONE | `1a60834` |
| 2 | Atomic fail-loud payment DB persistence | DONE | `1a60834` |
| 3 | Changelog catch-up for waves 10–12 | DONE | `1a60834` |

## Wave 14 — Bug hunt (API + replay + resample + onchain) — COMPLETE

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1 | Reduce internal exception detail in HTTP responses | DONE | `1a60834` |
| 2 | Validate orderbook price and amount on apply | DONE | `1a60834` |
| 3 | Drop NaN price/amount trades from OHLCV bars | DONE | `1a60834` |
| 4 | Onchain: persist seen logs; advance cursors only on success | DONE | `1a60834` |

## Wave 15 — Hardening + feature (e2e + open interest) — COMPLETE

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1 | Harden API server fixture and tier waits (e2e) | DONE | `1a60834` |
| 2 | MCP open-interest tool | DONE | `1a60834` |

## Wave 16 — Feature (portal + lake API + multi-collect + funding) — COMPLETE

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1 | Implement lake catalog scan endpoint | DONE | `1a60834` |
| 2 | Lake catalog inventory endpoint | DONE | `1a60834` |
| 3 | MCP funding prediction tool | DONE | `1a60834` |
| 4 | Portal: detect Python backend when admin returns 404 | DONE | `1a60834` |
| 5 | Bounded read-only SQL / REST query endpoint | DONE | `1a60834` |
| 6 | Register superchain connector in factory | DONE | `1a60834` |
| 7 | Multi-exchange collect CLI | DONE | `1a60834` |

## Wave 17 — Bug hunt — COMPLETE

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1 | Payment refund: restore paid when market-data serve fails after CAS | DONE | `1a60834` |
| 2 | Multi-symbol OI without exchange overwrite | DONE | `1a60834` |
| 3 | Harden read-only SQL query endpoint | DONE | `1a60834` |
| 4 | Superchain identity + per-exchange recovery state | DONE | `1a60834` |
| 5 | Onchain: co-persist seen logs with cursor advances | DONE | `1a60834` |
| 6 | MCP chaos-score tool | DONE | `1a60834` |
| 7 | CLI: include derive / superchain in exchange lists | DONE | `1a60834` |
| 8 | Register derive poll connector | DONE | `1a60834` |

## Wave 18 — Feature (open-interest REST) — COMPLETE

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1 | REST endpoint for open-interest aggregation | DONE | `1a60834` |

## Wave 21 — Feature (OKX resync + risk REST + options surface) — COMPLETE

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1 | Wire BookResyncBridge for OKX books (seqId/prevSeqId + REST `/market/books`) | DONE | `1a60834` |
| 2 | Base risk REST: `liquidity-depth`, `sequencer-latency`, `chaos-score`, `peg-deviation` | DONE | `1a60834` |
| 3 | Options REST: `GET /api/v1/iv-surface`, `GET /api/v1/term-structure` | DONE | `1a60834` |

## Wave 22 — Feature (vol-skew + lending-stress REST) — COMPLETE

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1 | REST `GET /api/v1/vol-skew` (underlying/expiry_ns/at/rate/limit) | DONE | `1a60834` |
| 2 | REST `GET /api/v1/lending-stress` pure CLI-matching query params | DONE | `1a60834` |

## Wave 23 — Feature (risk-reversal REST) — COMPLETE

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1 | REST `GET /api/v1/risk-reversal` (vol_skew → risk_reversal_butterfly; target_delta) | DONE | `1a60834` |

## Wave 24 — Feature (data-coverage REST) — COMPLETE

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1 | REST `GET /api/v1/data-coverage` (symbol/channel → inventory filter) | DONE | `1a60834` |
| 2 | Skip `/api/v1/search` alias — already `GET /api/v1/catalog/search` | SKIP | — |

## Wave 25 — Feature (perp-basis REST + label_transfers MCP) — COMPLETE

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1 | REST `GET /api/v1/perp-basis` (symbol/start/end/limit → client.perp_basis); skip bulk `/export` | DONE | `1a60834` |
| 2 | MCP `label_transfers` wrapping pure `label_transfer_addresses` | DONE | `1a60834` |

## Wave 26 — Feature (spot-future-basis REST) + bugfix — COMPLETE

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1 | REST `GET /api/v1/spot-future-basis` (future/spot/start/end/limit → client.spot_future_basis) | DONE | `1a60834` |
| 2 | Fix `Catalog.search_symbols` non-positive limit (Polars `head(-n)` trap) | DONE | `1a60834` |

## Wave 27 — Feature (resolve-symbols REST) + bugfix — COMPLETE

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1 | REST `GET /api/v1/resolve-symbols` (symbols/channel/ambiguous → client.resolve_symbols) | DONE | `1a60834` |
| 2 | Fix `resolve_symbols` empty/whitespace channel treated as no filter | DONE | `1a60834` |

## Wave 28 — Feature (health/status REST) + bugfix — COMPLETE

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1 | REST `GET /api/v1/health` + `/api/v1/status` (`ok`, `__version__`, `lake_channels` count; no payment) | DONE | `1a60834` |
| 2 | Fix `Catalog.inventory` empty/whitespace channel/exchange treated as no filter | DONE | `1a60834` |

## Wave 29 — Hardening (portal detect) + feature (exchanges REST) — COMPLETE

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1 | Portal `detectBackend`: treat `/api/v1/health` 200 as Python when catalog/metrics probes fail | DONE | `1a60834` |
| 2 | REST `GET /api/v1/exchanges` → `list_exchanges()` (free, no lake, no payment) | DONE | `1a60834` |

## Wave 30 — Bug hunt (derive ns, Aave HF, OKX/Bybit expiry) — COMPLETE

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1 | Derive options: store timestamps in nanoseconds | DONE | `1a60834` |
| 2 | Aave health factor: treat HF zero as zero, not infinity | DONE | `1a60834` |
| 3 | OKX/Bybit options: parse expiry from symbol when instrument unregistered | DONE | `1a60834` |

## Wave 31 — Feature (MCP spot-future basis) — COMPLETE

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1 | MCP `get_spot_future_basis` (handler + TOOLS schema + dispatch; optional `expiry_ns`) | DONE | `1a60834` |

## Wave 32 — Feature (funding-predict REST) + bugfix — COMPLETE

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1 | REST `GET /api/v1/funding-predict` (comma-separated `rates`, `window_size` → `predict_next_funding`) | DONE | `1a60834` |
| 2 | Fix OI symbol filter: `str.contains` literal match + skip empty tokens | DONE | `1a60834` |

## Wave 33 — Feature (gas-vol pure REST) — COMPLETE

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1 | REST `POST /api/v1/gas-vol` pure JSON body `{gas, vol}` → `gas_to_volatility_correlation` (no files/lake) | DONE | `1a60834` |

Skipped: file-based GET gas-vol; GET list-channels alias (catalog/channels exists); mev-sandwich / smart-money deferred (Wave 34).

## Wave 34 — Feature (mev-sandwich + smart-money pure REST) — COMPLETE

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1 | REST `POST /api/v1/mev-sandwich` body `{trades: [...]}` → `detect_sandwiches` | DONE | `5cb3294` |
| 2 | REST `POST /api/v1/smart-money` body `{transfers, watchlist}` → `summarize_smart_money` | DONE | `5cb3294` |

## Wave 35 — Feature (label-transfers pure REST) + bugfix — COMPLETE

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1 | REST `POST /api/v1/label-transfers` body `{transfers, watchlist, known_only?, min_usd?}` → `label_transfer_addresses` + filter | DONE | `7d005c98da2d6e4cbb340972d4df63e5d38becec` |
| 2 | Drop blank/whitespace watchlist address keys; never label empty transfer sides | DONE | `7d005c98da2d6e4cbb340972d4df63e5d38becec` |

## Wave 36 — Feature (k8s readiness probe) — COMPLETE

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1 | Prefer readiness over metrics-summary: `GET /api/v1/ready` (200 when `health.ok`, else 503); leave Prometheus at `/metrics` | DONE | `8c4ca948e795762b68c53036148f4af4bdd91d22` |

## Wave 37 — Feature (capabilities + portal ready probe) — COMPLETE

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1 | Portal `detectBackend`: also probe `/api/v1/ready` (200 → Python) before `/api/v1/health` | DONE | dbe54574440bce876a12f5da0dbd66f1d9be467e |
| 2 | Prefer `GET /api/v1/capabilities` `{rest, mcp_tools_hint}` hardcoded short free discovery lists (skip openapi-paths) | DONE | dbe54574440bce876a12f5da0dbd66f1d9be467e |

## Wave 38 — Feature (capabilities expand) + bug fix — COMPLETE

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1 | Expand `GET /api/v1/capabilities` `rest` (+ `mcp_tools_hint`) to free routes missing from short list (scan, basis suite, vol suite, pure risk, simulate-price-impact, …) | DONE | `44f1950f049a481723d3ba60f21b74e7f8a8bead` |
| 2 | Fix lending-stress REST/MCP: non-finite HF → JSON `null` (Starlette rejects `inf`) | DONE | `44f1950f049a481723d3ba60f21b74e7f8a8bead` |

## Wave 39 — Bugfix (pure REST/MCP JSON-safe floats) + capabilities — COMPLETE

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1 | Audit pure free REST float fields; apply `_json_safe_float` to `chaos-score`, `peg-deviation`, `funding-predict`, `gas-vol` (NaN/±Inf → JSON `null`) | DONE | `040c457575a40d6ecd88c16fb1b327350165aaf9` |
| 2 | Mirror sanitization on MCP `get_chaos_score` / `get_peg_deviation` / `get_funding_prediction` (+ shared `_json_safe_float`) | DONE | `040c457575a40d6ecd88c16fb1b327350165aaf9` |
| 3 | Capabilities: add missing MCP hints `get_onchain_price`, `get_base_market_data` (REST free list already complete) | DONE | `040c457575a40d6ecd88c16fb1b327350165aaf9` |

## Wave 40 — Hardening (lake DF REST JSON-safe records) — COMPLETE

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1 | Add `_json_safe_records(rows)` helper (walk dict values; NaN/±Inf floats → `None`) | DONE | `0ee00cb0c7d92d399a137920d8c336951e9927a4` |
| 2 | Apply to major DF-returning free lake endpoints: `open-interest`, `funding-apr`, `basis`, `perp-basis`, `spot-future-basis`, `ofi` | DONE | `0ee00cb0c7d92d399a137920d8c336951e9927a4` |
| 3 | Tests for helper + non-finite head rows on ofi / funding-apr / OI / basis | DONE | `0ee00cb0c7d92d399a137920d8c336951e9927a4` |
| 4 | Remaining free DF endpoints (indicators, whale-alerts, vol suite, catalog scan, …) | DONE | `01bca69c81dd5e1e7db0c55dd1c23e7ef49d80fd` |

## Wave 41 — Hardening (POST pure/offline row list JSON-safe) — COMPLETE

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1 | Apply `_json_safe_records` to `POST /api/v1/simulate-price-impact` (slippage DF floats) | DONE | `eb7e88e376b605524f231de961e374651ff6e316` |
| 2 | Apply `_json_safe_records` to `POST /api/v1/smart-money` | DONE | `eb7e88e376b605524f231de961e374651ff6e316` |
| 3 | Apply `_json_safe_records` to `POST /api/v1/label-transfers` | DONE | `eb7e88e376b605524f231de961e374651ff6e316` |
| 4 | Tests for non-finite sanitization on those three POST endpoints; full API + MCP suites green | DONE | `eb7e88e376b605524f231de961e374651ff6e316` |

## Wave 42 — Hardening (MCP list[dict] DF JSON-safe records) — COMPLETE

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1 | Add MCP `_json_safe_records` (mirror REST walk; NaN/±Inf floats → `None`) | DONE | `9845aeb4870b83cb47ec95d8bdf874a3d91dbbcb` |
| 2 | Apply to lake/analytics handlers returning `list[dict]` from DataFrames: ofi, slippage, whale, vol suite, basis trio, indicators, depth, sequencer, OI, discovery (search/coverage/inventory), MEV; plus smart-money / label-transfers | DONE | `9845aeb4870b83cb47ec95d8bdf874a3d91dbbcb` |
| 3 | Sanitize inline `query_market_data` / `get_funding_apr` tools/call paths | DONE | `9845aeb4870b83cb47ec95d8bdf874a3d91dbbcb` |
| 4 | Tests for helper + non-finite ofi/slippage/OI/basis/indicators/whale; MCP analytics suite green | DONE | `9845aeb4870b83cb47ec95d8bdf874a3d91dbbcb` |

## Wave 43 — Feature (catalog dates discovery) — COMPLETE

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1 | `Catalog.list_dates(channel)` + client wrapper (filesystem hive `date=` partitions; path-safe) | DONE | `992010d` |
| 2 | REST `GET /api/v1/catalog/dates?channel=` + capabilities entry | DONE | `992010d` |
| 3 | Tests: store list_dates + API empty/mock/strip; capabilities includes dates | DONE | `992010d` |

## Wave 44 — Feature (MCP list_dates) + Hardening (shared json_safe util) — COMPLETE

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1 | MCP `list_dates` tool wrapping `client.list_dates(channel)` (handler, TOOLS, tools/call, capabilities hint) | DONE | `e84834135e9ab641910393893fe1870f886713a7` |
| 2 | Extract `crypcodile.util.json_safe` (`json_safe_float` / `json_safe_records`); api_server + mcp_server re-export | DONE | `e84834135e9ab641910393893fe1870f886713a7` |
| 3 | Tests: MCP list_dates empty/data/strip; util json_safe + re-export identity; capabilities includes list_dates | DONE | `e84834135e9ab641910393893fe1870f886713a7` |

## Wave 45 — Feature (catalog exchanges on-disk discovery) — COMPLETE

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1 | `Catalog.list_exchanges_on_disk()` + client wrapper (filesystem hive `exchange=` partitions; distinct from factory `list_exchanges`) | DONE | `4e7c2ff39c2078f95c8a34d5da696c358b26b922` |
| 2 | REST `GET /api/v1/catalog/exchanges` + capabilities entry | DONE | `4e7c2ff39c2078f95c8a34d5da696c358b26b922` |
| 3 | Tests: store list_exchanges_on_disk + client + API empty/mock; capabilities includes catalog/exchanges | DONE | `4e7c2ff39c2078f95c8a34d5da696c358b26b922` |

## Wave 46 — Feature (MCP list_exchanges_on_disk + filesystem list_channels) — COMPLETE

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1 | MCP `list_exchanges_on_disk` tool wrapping `client.list_exchanges_on_disk` (handler, TOOLS, tools/call, capabilities hint) | DONE | `3886d1dc9b66e52fe5d7207c2aba43f04b43c735` |
| 2 | `Catalog.list_channels` filesystem-based (`exchange=*/channel=*`; empty partitions without DuckDB); `_refresh_views` skips empty channel suffixes | DONE | `3886d1dc9b66e52fe5d7207c2aba43f04b43c735` |
| 3 | Tests: MCP empty/data/delegate; catalog empty-partition dirs; capabilities includes list_exchanges_on_disk | DONE | `3886d1dc9b66e52fe5d7207c2aba43f04b43c735` |

## Wave 47 — Bug hunt (broad regression) + Feature (catalog summary) — COMPLETE

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1 | Broad regression: api_endpoints, mcp_analytics, mcp_discovery, catalog_search, client_search, compactor, parquet_sink, binance/okx book resync, factory, json_safe, util — all green (no fixes needed) | DONE | baseline `4a99b89` |
| 2 | REST `GET /api/v1/catalog/summary` → `{channels, exchanges_on_disk, exchange_count, channel_count}` + capabilities entry | DONE | `ab6b353253210e6b08b1cd70ae792edfed90dab4` |
| 3 | Tests: empty lake + mock lists/counts; capabilities includes catalog/summary | DONE | `ab6b353253210e6b08b1cd70ae792edfed90dab4` |

## Wave 48 — Feature (MCP catalog_summary) + docs polish — COMPLETE

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1 | MCP `catalog_summary` tool wrapping channels + exchanges_on_disk with counts (handler, TOOLS, tools/call, capabilities hint) | DONE | `43a246af61467e0567d9909010a1646dec53e7b9` |
| 2 | Small fix: README discovery section lists MCP catalog tools (`list_exchanges_on_disk`, `catalog_summary`) | DONE | `43a246af61467e0567d9909010a1646dec53e7b9` |
| 3 | Tests: MCP empty/data/delegate; capabilities includes catalog_summary | DONE | `43a246af61467e0567d9909010a1646dec53e7b9` |

## Wave 49 — Feature (CLI catalog-summary) + list_channels special-char hardening — COMPLETE

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1 | Skip `GET /api/v1/catalog/buckets` (too granular) | SKIP | — |
| 2 | `_is_safe_hive_suffix` + filter in `list_channels` / `list_exchanges_on_disk` / `_refresh_views` / `list_dates`; channel dir resolve check | DONE | `52ed26389b9bb4ff8fbd41b4c7569d78804ae010` |
| 3 | CLI `catalog-summary` via client `list_channels` + `list_exchanges_on_disk` counts | DONE | `52ed26389b9bb4ff8fbd41b4c7569d78804ae010` |
| 4 | Tests: special-char/symlink skips; CLI empty/data/delegate | DONE | `52ed26389b9bb4ff8fbd41b4c7569d78804ae010` |

## Wave 50 — Bug hunt (broad regression) + Feature (CLI catalog list_channels / --symbols) — COMPLETE

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1 | Broad regression: api_endpoints, mcp_analytics/discovery, catalog_search/catalog, compactor, parquet_sink, client search, json_safe, factory, binance/okx, cli, schema, sink, instruments, replay — green (no pre-existing failures) | DONE | baseline `231070a` |
| 2 | CLI `catalog` uses filesystem `list_channels`; empty partitions → `0` rows; `--symbols` inventory still works with empty dirs coexisting | DONE | `6c65a1818487827aaca33f27ffcda63ef449462f` |
| 3 | Fix `_create_view` skip when no parquet (Catalog init no longer raises on empty hive dirs) | DONE | `6c65a1818487827aaca33f27ffcda63ef449462f` |
| 4 | Tests: empty partitions catalog listing; --symbols with/without data + empty dirs; construct-before-data Catalog; list_channels delegate | DONE | `6c65a1818487827aaca33f27ffcda63ef449462f` |

## Wave 51 — Feature (API catalog/search channel + exchange filters) — COMPLETE

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1 | REST `GET /api/v1/catalog/search` optional `channel` + `exchange` query filters (strip empty → no filter; forward to client) | DONE | `9afda11` |
| 2 | Tests: forwards filters; strips empty/padded filters; existing search tests updated for kwargs | DONE | `9afda11` |

## Wave 52 — Feature (MCP search exchange + REST catalog/symbols) — COMPLETE

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1 | MCP `search_symbols` optional `exchange` filter (handler + schema + dispatch; parity with REST/CLI/Catalog) | DONE | `f152671` |
| 2 | Verify MCP `inventory_snapshot` already has channel + exchange filters (no code change) | DONE | verified |
| 3 | REST `GET /api/v1/catalog/symbols?channel=&exchange=` distinct sorted symbols from inventory + capabilities | DONE | `f152671` |
| 4 | Tests: MCP exchange filter; catalog symbols empty/distinct/filters/strip/route; capabilities includes symbols | DONE | `f152671` |

## Wave 53 — Feature (MCP list_symbols) + broad discovery regression — COMPLETE

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1 | MCP `list_symbols` wrapping inventory distinct sorted symbols; optional channel/exchange (strip empty → no filter); TOOLS + tools/call + capabilities hint | DONE | `3a240d2` |
| 2 | Tests: empty/data/channel/exchange/strip/delegate; TOOLS schema; capabilities includes `list_symbols` | DONE | `3a240d2` |
| 3 | Broad regression: mcp_discovery, mcp_analytics, api_endpoints, catalog, catalog_search, client search, json_safe, factory — **537 passed** | DONE | baseline `3a240d2` |

## Wave 54 — Feature (CLI catalog-dates / catalog-symbols / catalog-exchanges) — COMPLETE

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1 | CLI `catalog-dates --channel` via `client.list_dates` (strip; empty → No dates.; required channel) | DONE | `0c418c4` |
| 2 | CLI `catalog-symbols` optional `--channel` / `--exchange` via inventory distinct sorted symbols | DONE | `0c418c4` |
| 3 | CLI `catalog-exchanges` via `client.list_exchanges_on_disk` | DONE | `0c418c4` |
| 4 | Tests: empty/data/filter/strip/delegate for all three; **16 passed** | DONE | `0c418c4` |

## Wave 55 — Bug hunt (broad regression) + Feature (MCP resolve_symbols) — COMPLETE

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1 | Broad regression: api_endpoints, mcp_analytics/discovery, catalog/catalog_search, store (compactor/parquet_sink/rows), binance/okx book resync + gap_bridge, cli, client search, json_safe, factory — **667 passed** (no pre-existing failures) | DONE | baseline `5bb3290` |
| 2 | MCP `resolve_symbols` wrapping `client.resolve_symbols` (list or comma-separated symbols; optional channel + ambiguous; empty → `[]`; ValueError → `{error}`; TOOLS + tools/call + capabilities hint) | DONE | `982de65` |
| 3 | Tests: empty/data/comma/strip/error/delegate + capabilities includes `resolve_symbols`; post-feature critical suites **675 passed** | DONE | `982de65` |

## Wave 56 — Feature (CLI resolve-symbols / data-coverage) — COMPLETE

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1 | CLI `resolve-symbols` wrapping `client.resolve_symbols` (`--channel`, `--ambiguous=error\|first\|all`; comma-separated arg; ValueError → exit 1) | DONE | `5f38b0f` |
| 2 | CLI `data-coverage --symbol` optional `--channel` wrapping inventory exact-symbol filter (empty → `No coverage.`) | DONE | `5f38b0f` |
| 3 | Tests: empty/data/comma/channel/strip/error/delegate for resolve-symbols; empty/data/channel/no-match/strip/delegate for data-coverage — **15 passed**; CLI+discovery regression green | DONE | `5f38b0f` |

## Wave 57 — Bug hunt (broad regression) + Feature (CLI search --exchange docs/tests) — COMPLETE

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1 | Broad regression: api_endpoints, mcp_analytics/discovery, catalog/catalog_search, store (compactor/parquet_sink), binance/okx, cli, client search, json_safe, factory, schema, sink, instruments, replay, gap_bridge — **790 passed** (no pre-existing failures) | DONE | baseline `fe3e668` |
| 2 | CLI `search`: document `--exchange` (module docstring, command help docstring, README example); strip empty/whitespace `--channel`/`--exchange` to `None` (parity with `catalog-symbols`) | DONE | `b45add3` |
| 3 | Tests: `test_cli_search_exchange_filter` (hit/miss) + `test_cli_search_strips_exchange_filter`; post-feature critical suites **792 passed** (+2) | DONE | `b45add3` |

## Wave 58 — Feature (CLI list-exchanges + MCP list_registered_exchanges) — COMPLETE

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1 | CLI `list-exchanges` wrapping factory `list_exchanges` (no lake; ≠ `catalog-exchanges`) | DONE | `6c69d28` |
| 2 | MCP `list_registered_exchanges` handler + TOOLS + tools/call + capabilities hint | DONE | `6c69d28` |
| 3 | Tests: CLI factory/delegate/distinct-from-catalog; MCP factory/delegate/distinct-from-on-disk; capabilities includes tool | DONE | `6c69d28` |
| 4 | README/CHANGELOG/progress discovery surface docs | DONE | `6c69d28` |

## Wave 59 — Bug hunt (broad regression) + Feature (list-exchanges --help lock) — COMPLETE

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1 | Broad regression: api_endpoints, mcp_analytics/discovery, catalog/catalog_search, store (compactor/parquet_sink), binance/okx, cli, client search, json_safe, factory, schema, sink, instruments, replay, gap_bridge — **798 passed** (no pre-existing failures) | DONE | baseline `6956c82` |
| 2 | Ensure `list-exchanges` in CLI `--help` main Commands listing (already registered; module docstring Commands list present from wave 58; no CLI code change needed) | DONE | verified |
| 3 | Test: `test_cli_list_exchanges_in_main_help` (top-level `--help` + module docstring); post-feature critical suites **799 passed** (+1) | DONE | PENDING_SHA |

## Next rotation ideas (Wave 60+)

Priority candidates for the next cycles:

1. **Bybit book resync** — wire `BookResyncBridge` for Bybit (deferred: REST `u` vs `orderbook.50` alignment)  
2. **More indicator CLI modes** — mirror MCP indicators on the CLI where missing  
3. **Payment / portal polish** — remaining API portal UX beyond backend detection  
4. **Coinbase book gap counter** — deferred: level2 has no sequence fields (`2150bba`)  



## Subagent policy

Every task: implementer → spec reviewer → quality reviewer → fix if needed. Parallel explore agents at wave start.

## Iteration log

- Iter 1: 4 explores (bugs, search, product, quality)
- Wave 1: compactor + payments security (multiple review loops); MCP EOF later
- Wave 2: full search stack Catalog→Client→CLI→MCP
- Wave 3: sink safety, path sanitize, MCP analytics pack, CLI vol tools, polars deprecation
- Wave 4: catalog hardening, base risk CLI, MCP vol-skew
- Wave 5: DLQ drain on stop, aiohttp connect-fail cleanup, 0.1.044 release notes
- Wave 6: Binance book resync bridge on sequence gap
- Wave 7: README/docs alignment, smart-money / whale-transfer CLI surface
- Wave 8: backfill CLI, chaos-score, spot-perp basis; Binance bridge bootstrap fix
- Wave 9: Bybit resync deferred note, whitespace search fix, changelog 7–9
- Wave 10: API lake catalog endpoints, liquidity-depth, sequencer-latency, MCP basis tools
- Wave 11: multi-partition partial-flush re-buffer, client/MCP indicators surface
- Wave 12: list_exchanges, collect duration/max-reconnects, lending-stress, MCP depth/seq
- Wave 13: atomic parquet parts, fail-loud payment DB, changelog 10–12
- Wave 14: API exception detail, orderbook apply validation, NaN OHLCV drop, onchain cursor/seen-log
- Wave 15: e2e fixture/tier hardening, MCP open-interest tool
- Wave 16: portal detect, multi-exchange collect, REST query, superchain factory, inventory, funding MCP, catalog scan
- Wave 17: payment refund, multi-symbol OI, SQL harden, superchain identity, seen-logs co-persist, chaos MCP, CLI derive/superchain lists, derive poll connector
- Wave 18: REST open-interest endpoint (`GET /api/v1/open-interest`)
- Wave 19: REST funding-apr / basis / indicators endpoints; MCP lending-stress / peg_deviation / MEV sandwich
- Wave 20: REST OFI / whale-alerts / slippage endpoints
- Wave 21: OKX book resync bridge; base risk REST (depth/seq/chaos/peg); iv-surface / term-structure REST
- Wave 22: REST vol-skew / lending-stress endpoints
- Wave 33: REST gas-vol pure JSON correlation
- Wave 34: REST mev-sandwich + smart-money pure JSON bodies
- Wave 23: REST risk-reversal (`vol_skew` → `risk_reversal_butterfly`)
- Wave 24: REST data-coverage (`inventory` filter by symbol/channel); skip search alias
- Wave 25: REST perp-basis (`client.perp_basis`); MCP `label_transfers`; skip bulk export HTTP
- Wave 26: REST spot-future-basis (`client.spot_future_basis`); catalog search non-positive limit fix
- Wave 27: REST resolve-symbols; empty-channel resolve_symbols fix
- Wave 28: REST health/status probe; catalog inventory empty filter fix
- Wave 29: portal detectBackend health fallback; REST `/api/v1/exchanges`
- Wave 30: derive options ns timestamps; Aave HF zero≠∞; OKX/Bybit option expiry parse from symbol
- Wave 31: MCP `get_spot_future_basis` (completes basis trio with perp + spot-perp)
- Wave 32: REST `/api/v1/funding-predict`; OI symbol filter literal `str.contains`
- Wave 33: REST `POST /api/v1/gas-vol` pure JSON gas/vol correlation
- Wave 34: REST mev-sandwich + smart-money pure JSON bodies
- Wave 35: REST `POST /api/v1/label-transfers`; blank watchlist key fix
- Wave 36: REST `GET /api/v1/ready` k8s readiness (200/`ok` else 503); `/metrics` kept as-is
- Wave 37: portal detectBackend `/api/v1/ready` probe; REST `GET /api/v1/capabilities` agent discovery
- Wave 38: expand capabilities free rest/mcp lists; lending-stress JSON-safe null HF
- Wave 39: pure REST/MCP JSON-safe floats (chaos/peg/funding-predict/gas-vol); MCP onchain capability hints
- Wave 40: lake DF REST `_json_safe_records` on ofi / funding-apr / open-interest / basis suite (+ remaining free DF endpoints)
- Wave 41: `_json_safe_records` on POST simulate-price-impact / smart-money / label-transfers
- Wave 42: MCP `_json_safe_records` on list[dict] DF handlers (ofi/slippage/whale/vol/basis/indicators/OI/…) + query/funding_apr paths
- Wave 43: REST `GET /api/v1/catalog/dates?channel=` via Catalog/Client `list_dates` (filesystem date partition discovery)
- Wave 44: MCP `list_dates` tool + shared `crypcodile.util.json_safe` (dedupe REST/MCP JSON-safe helpers)
- Wave 45: REST `GET /api/v1/catalog/exchanges` via Catalog/Client `list_exchanges_on_disk` (filesystem exchange partition discovery; ≠ factory `list_exchanges`)
- Wave 46: MCP `list_exchanges_on_disk` tool + filesystem `Catalog.list_channels` (empty partitions without DuckDB)
- Wave 47: broad regression green (645→647 after +2 tests); REST `GET /api/v1/catalog/summary` agent discovery
- Wave 48: MCP `catalog_summary` mirrors REST summary; README MCP discovery list updated
- Wave 49: CLI `catalog-summary`; hive suffix special-char filtering on list_channels/exchanges walks
- Wave 50: broad regression green (714→719 after +5 tests); CLI catalog filesystem list_channels; `_create_view` empty-partition skip; `--symbols` still inventories with empty dirs present
- Wave 51: REST `GET /api/v1/catalog/search` optional `channel`/`exchange` filters (parity with CLI search / Catalog.search_symbols)
- Wave 52: MCP `search_symbols` exchange filter; REST `GET /api/v1/catalog/symbols` distinct inventory symbols; inventory_snapshot filters verified
- Wave 53: MCP `list_symbols` (inventory distinct symbols + channel/exchange filters; REST catalog/symbols parity); broad discovery regression 537 passed
- Wave 54: CLI `catalog-dates` / `catalog-symbols` / `catalog-exchanges` (list_dates + inventory symbols + exchanges_on_disk; REST/MCP parity)
- Wave 55: broad regression green (667→675 after +8 tests); MCP `resolve_symbols` (REST resolve-symbols parity for agents)
- Wave 56: CLI `resolve-symbols` + `data-coverage` (REST/MCP discovery parity; 15 new tests)
- Wave 57: broad regression green (790→792 after +2 tests); CLI search `--exchange` docs + strip + tests
- Wave 58: CLI `list-exchanges` + MCP `list_registered_exchanges` (factory connector registry; ≠ lake on-disk)
- Wave 59: broad regression green (798→799 after +1 test); list-exchanges `--help` main listing + module docstring lock
