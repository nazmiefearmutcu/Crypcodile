# Crypcodile Ralph Continuous Development Loop

**Started:** 2026-07-13  
**Mode:** Infinite until user stops  
**Branch:** `ralph/continuous-dev`  
**Base:** `66b44af`  
**Version:** `0.1.044`  
**Rotation:** Bug hunt → Feature → Hardening → Feature → …  
**Status:** Waves 1–24 COMPLETE. Ready for Wave 25.

## Wave 1 — Bug hunt — COMPLETE

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1 | Compactor atomic rename-before-delete | DONE | `912c79b` |
| 1b | Compactor stop cancel await | DONE + APPROVED | `747c217` |
| 2 | Payment CAS before serve | DONE + APPROVED | `5813278` |
| 2b | pending-only → paid | DONE + APPROVED | `23442c0` |
| 3 | ALLOW_SIMULATION default false; admin lock | DONE (in 5813278) | |
| 4 | MCP stdin EOF (no executor hang) | DONE | `f6c2a5d` |

## Wave 2 — Search system — COMPLETE

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1–2 | Catalog inventory + search_symbols | DONE + APPROVED | `a19228f` |
| 3–5 | Client + CLI + MCP discovery | DONE + APPROVED | `6fe12a5` |
| 6 | CLI resolve via client | DONE | `69ba0fd` |

## Wave 3 — Hardening + surfaces — COMPLETE

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1 | ParquetSink buffer after durable write | DONE | `1813f6f` |
| 1b | Re-buffer on CancelledError | DONE | `16c5dbc` |
| 2 | Path traversal sanitize partitions | DONE | `17d8803` |
| 3 | MCP analytics pack (slippage, ofi, whale, iv, term) | DONE | `b5c7005` |
| 4 | CLI vol-skew / risk-reversal | DONE | `f43298e` |
| 5 | Polars min_periods → min_samples | DONE | `102e5fd` |

## Wave 4 — Feature (analytics + catalog) — COMPLETE

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1 | Catalog scan limit + escape channel ids | DONE | `649721f` |
| 2 | CLI base risk analytics commands | DONE | `e211e2c` |
| 3 | MCP get_vol_skew analytics tool | DONE | `2d8a6ad` |

## Wave 5 — Bug hunt / ingest reliability — COMPLETE

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1 | Drain dead-letter queue on collect stop + report | DONE | `0410ba7` |
| 2 | Close aiohttp session when ws connect fails | DONE | `7edff04` |
| 3 | Changelog + version bump 0.1.044 | DONE | `c54c7cb`, `15acaa1` |

## Wave 6 — Feature (book resync bridge) — COMPLETE

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1 | Wire book resync bridge on sequence gap (Binance) | DONE | `a2df5de` |

## Wave 7 — Feature (docs + smart-money surface) — COMPLETE

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1 | Document search/discovery commands in README | DONE | `fd840bc` |
| 2 | Align CLI exchange lists with factory registry | DONE | `04da164` |
| 3 | CLI smart-money / whale-transfer surface | DONE | `605ac34` |

## Wave 8 — Feature (CLI backfill + chaos + basis) — COMPLETE

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1 | Binance book bridge only after successful bootstrap | DONE | `2e51798` |
| 2 | CLI backfill command (historical REST) | DONE | `600f574` |
| 3 | CLI chaos-score command | DONE | `e172f7a` |
| 4 | CLI spot-perp basis mode (`--spot X --perp Y`) | DONE | `2bad0dd` |

## Wave 9 — Hardening + docs (search + ingest notes) — COMPLETE

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1 | Bybit book resync deferred (shared helpers only) | DONE | `6a6ae7a` |
| 2 | Whitespace-only catalog search treated as empty | DONE | `a871d14` |
| 3 | Expand 0.1.044 changelog with wave 7–9 work | DONE | `9cc58d0` |

## Wave 10 — Feature (API catalog + liquidity + sequencer + MCP basis) — COMPLETE

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1 | REST API lake catalog list and search endpoints | DONE | `cb00578` |
| 2 | CLI liquidity-depth command | DONE | `3d490a1` |
| 3 | CLI sequencer-latency command | DONE | `1c09a09` |
| 4 | MCP basis analytics tools | DONE | `7d8a4ac` |

## Wave 11 — Hardening + indicators surface — COMPLETE

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1 | Multi-partition sink: re-buffer only unwritten partitions after partial flush | DONE | `cc51a71` |
| 2 | Client/MCP technical indicators surface | DONE | `8aaf679` |

## Wave 12 — Feature (exchanges + collect + lending + MCP depth/seq) — COMPLETE

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1 | Public `list_exchanges` from factory registry | DONE | `f5aadf7` |
| 2 | CLI collect `--duration` and `--max-reconnects` | DONE | `ba09fdd` |
| 3 | CLI lending-stress command | DONE | `4f3dc96` |
| 4 | MCP liquidity-depth and sequencer-latency tools | DONE | `2ff6251` |

## Wave 13 — Hardening (store + payments + changelog) — COMPLETE

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1 | Atomic temp-rename for parquet part writes | DONE | `5059c95` |
| 2 | Atomic fail-loud payment DB persistence | DONE | `eec2522` |
| 3 | Changelog catch-up for waves 10–12 | DONE | `143c65e` |

## Wave 14 — Bug hunt (API + replay + resample + onchain) — COMPLETE

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1 | Reduce internal exception detail in HTTP responses | DONE | `742ac64` |
| 2 | Validate orderbook price and amount on apply | DONE | `a608d45` |
| 3 | Drop NaN price/amount trades from OHLCV bars | DONE | `7016780` |
| 4 | Onchain: persist seen logs; advance cursors only on success | DONE | `c769bbc` |

## Wave 15 — Hardening + feature (e2e + open interest) — COMPLETE

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1 | Harden API server fixture and tier waits (e2e) | DONE | `c38d317` |
| 2 | MCP open-interest tool | DONE | `4746678` |

## Wave 16 — Feature (portal + lake API + multi-collect + funding) — COMPLETE

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1 | Implement lake catalog scan endpoint | DONE | `9189d62` |
| 2 | Lake catalog inventory endpoint | DONE | `bcb26e2` |
| 3 | MCP funding prediction tool | DONE | `6f06d78` |
| 4 | Portal: detect Python backend when admin returns 404 | DONE | `0a183e1` |
| 5 | Bounded read-only SQL / REST query endpoint | DONE | `d6b5e85` |
| 6 | Register superchain connector in factory | DONE | `aaf7893` |
| 7 | Multi-exchange collect CLI | DONE | `c8a22ae` |

## Wave 17 — Bug hunt — COMPLETE

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1 | Payment refund: restore paid when market-data serve fails after CAS | DONE | `5af5c7f` |
| 2 | Multi-symbol OI without exchange overwrite | DONE | `55e55bf` |
| 3 | Harden read-only SQL query endpoint | DONE | `92dcf3f` |
| 4 | Superchain identity + per-exchange recovery state | DONE | `cb2bcad` |
| 5 | Onchain: co-persist seen logs with cursor advances | DONE | `b1032b5` |
| 6 | MCP chaos-score tool | DONE | `9644341` |
| 7 | CLI: include derive / superchain in exchange lists | DONE | `72bdd64` |
| 8 | Register derive poll connector | DONE | `994ed98` |

## Wave 18 — Feature (open-interest REST) — COMPLETE

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1 | REST endpoint for open-interest aggregation | DONE | `8f80bd0` |

## Wave 21 — Feature (OKX resync + risk REST + options surface) — COMPLETE

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1 | Wire BookResyncBridge for OKX books (seqId/prevSeqId + REST `/market/books`) | DONE | `71fec44` |
| 2 | Base risk REST: `liquidity-depth`, `sequencer-latency`, `chaos-score`, `peg-deviation` | DONE | `73a8287` |
| 3 | Options REST: `GET /api/v1/iv-surface`, `GET /api/v1/term-structure` | DONE | `7d41f7a` |

## Wave 22 — Feature (vol-skew + lending-stress REST) — COMPLETE

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1 | REST `GET /api/v1/vol-skew` (underlying/expiry_ns/at/rate/limit) | DONE | `fd92785` |
| 2 | REST `GET /api/v1/lending-stress` pure CLI-matching query params | DONE | `fd92785` |

## Wave 23 — Feature (risk-reversal REST) — COMPLETE

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1 | REST `GET /api/v1/risk-reversal` (vol_skew → risk_reversal_butterfly; target_delta) | DONE | `eb23611` |

## Wave 24 — Feature (data-coverage REST) — COMPLETE

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1 | REST `GET /api/v1/data-coverage` (symbol/channel → inventory filter) | DONE | `3438684` |
| 2 | Skip `/api/v1/search` alias — already `GET /api/v1/catalog/search` | SKIP | — |

## Wave 25 — Feature (perp-basis REST + label_transfers MCP) — COMPLETE

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1 | REST `GET /api/v1/perp-basis` (symbol/start/end/limit → client.perp_basis); skip bulk `/export` | DONE | `275a9d2` |
| 2 | MCP `label_transfers` wrapping pure `label_transfer_addresses` | DONE | `6ae47e3` |

## Next rotation ideas (Wave 26+)

Priority candidates for the next cycles:

1. **Bybit book resync** — wire `BookResyncBridge` for Bybit (deferred: REST `u` vs `orderbook.50` alignment)  
2. **More indicator CLI modes** — mirror MCP indicators on the CLI where missing  
3. **Payment / portal polish** — remaining API portal UX beyond backend detection  
4. **MEV sandwich surface** — further CLI/MCP wiring if pure detect helpers remain unexposed  
5. **Broader lake HTTP reads** — extend beyond catalog scan/inventory/query/open-interest as needed  
6. **Coinbase book gap counter** — deferred: level2 has no sequence fields (`2150bba`)  

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
- Wave 23: REST risk-reversal (`vol_skew` → `risk_reversal_butterfly`)
- Wave 24: REST data-coverage (`inventory` filter by symbol/channel); skip search alias
- Wave 25: REST perp-basis (`client.perp_basis`); MCP `label_transfers`; skip bulk export HTTP
