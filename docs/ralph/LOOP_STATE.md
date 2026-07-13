# Crypcodile Ralph Continuous Development Loop

**Started:** 2026-07-13  
**Mode:** Infinite until user stops  
**Branch:** `ralph/continuous-dev`  
**Base:** `66b44af`  
**Version:** `0.1.044`  
**Rotation:** Bug hunt → Feature → Hardening → Feature → …  
**Status:** Waves 1–33 COMPLETE. Continuous loop still active → Wave 34+.

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

## Next rotation ideas (Wave 35+)

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
