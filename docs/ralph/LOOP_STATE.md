# Crypcodile Ralph Continuous Development Loop

**Started:** 2026-07-13  
**Mode:** Infinite until user stops  
**Branch:** `ralph/continuous-dev`  
**Base:** `66b44af`  
**Version:** `0.1.044`  
**Rotation:** Bug hunt → Feature → Hardening → Feature → …  
**Status:** Waves 1–11 COMPLETE. Ready for Wave 12 (Feature).

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

## Next rotation ideas (Wave 12+)

Priority candidates for the next cycles:

1. **Bybit book resync** — wire `BookResyncBridge` for Bybit (deferred: REST `u` vs `orderbook.50` alignment)  
2. **REST API lake read endpoints** — broader HTTP read surface beyond catalog list/search  
3. **More indicator CLI modes** — mirror MCP indicators on the CLI where missing  
4. **MCP liquidity / sequencer tools** — expose depth and sequencer latency over MCP  
5. **Payment / portal polish** — remaining API portal UX and state edge cases  

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
- **HEAD:** `8aaf679` — ready for Wave 12
