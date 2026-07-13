# Crypcodile Ralph Continuous Development Loop

**Started:** 2026-07-13  
**Mode:** Infinite until user stops  
**Branch:** `ralph/continuous-dev`  
**Base:** `66b44af`  
**Version:** `0.1.044`  
**Rotation:** Bug hunt → Feature → Hardening → Feature → …  
**Status:** Waves 1–7 COMPLETE. Ready for Wave 8 (Feature).

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

## Next rotation ideas (Wave 8+)

Priority candidates for the next cycles:

1. **Bybit book resync** — extend `OrderBookSync` + `BookResyncBridge` pattern from Binance to Bybit depth gaps  
2. **chaos-score CLI** — surface chaos/stress score analytics on the CLI  
3. **basis spot-perp CLI mode** — dedicated spot–perp basis command mode  
4. **REST API lake endpoints** — HTTP read API over lake/catalog data  
5. **multi-partition sink atomic flush** — atomic flush across multiple parquet partitions (no partial durable state)

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
- **HEAD:** `605ac34` — ready for Wave 8
