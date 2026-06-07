# Crypcodile — Core Design (v1)

**Date:** 2026-06-04
**Status:** Approved (architecture). Implementation plan to follow via `writing-plans`.
**Scope:** This spec covers the **normalized market-data core** only — the foundation
shared by Tardis.dev (normalized replay), Laevitas (derivatives analytics), and
Amberdata (data API). Analytics, the server API, and the dashboard are **separate
future specs** that build on top of this core.

**Companion (authoritative implementation detail):**
[`2026-06-04-crypcodile-core-research-appendix.md`](2026-06-04-crypcodile-core-research-appendix.md)
— exchange-verified field mappings, order-book diff-sync algorithms (Binance spot ≠ futures,
Deribit `action=delete`), the Connector ABC, storage/replay/ingestion patterns, and a
consolidated gotchas list. Where this design and the appendix differ on a low-level detail,
**the appendix wins** (it is sourced from official docs and was adversarially gap-checked).

---

## 1. Vision & Goal

Crypcodile is an **open-source** engine that ingests crypto market data (live + historical)
from many exchanges, normalizes everything into **one canonical schema**, stores it in a
compressed columnar store, and makes it retrievable **anywhere, at any resolution**
(replay + multi-format export + SQL).

The north star: have access to crypto financial data **as good as Laevitas, Amberdata,
and Tardis.dev**. The core spec delivers the Tardis-class foundation; analytics and API
layers (Laevitas/Amberdata equivalents) sit on top in later cycles.

**Explicit user constraint:** do not consider the project "delivered" until it is a very
advanced application. The Ralph loop drives through milestone gates M1→M5 (Section 11)
and does not stop early.

---

## 2. Non-Goals (v1 / this spec)

- No REST/WebSocket **server** yet (next spec; the *client* + export covers delivery now).
- No analytics (IV surface, greeks, skew, basis, etc.) — next spec, on top of this core.
- No dashboard/UI — later.
- No TradFi (equities/FX) — crypto-first. Multi-asset is a later breadth expansion.
- No managed/cloud service — local-first, self-hostable.

---

## 3. Architecture Decisions (locked)

| Decision | Choice | Why |
|---|---|---|
| Language/runtime | **Python-first** (asyncio) | Fastest iteration; richest crypto-data + columnar ecosystem. Hot path can move to Rust later. |
| Packaging | **Single installable package** `crypcodile` with submodules | Simple under `uv`; fast Ralph iteration; no early cross-package version hell. |
| In-memory / decode | **msgspec** structs (hot path), **Polars** transforms | msgspec decode ~5–10x faster than pydantic; matters at 10k+ msg/s. |
| Storage | **Hive-partitioned Parquet** (zstd) + **DuckDB** SQL layer | Zero-copy SQL over files; no DB server required. |
| Delivery (v1) | **Python client + CLI + multi-format export + replay** | Chosen path: client/export first, server API next spec. |
| Scope | **Crypto-first** | All three references' core is crypto; data is free/public. |
| First exchanges | **Deribit + Binance** (reference), then Bybit/OKX/Coinbase | Deribit = options/derivatives gold standard; Binance = spot+futures breadth. |
| License | **Apache-2.0** | Standard permissive for data infra. |
| Tooling | uv, ruff, pytest, mypy | — |

Alternatives considered and rejected for v1: monorepo multi-package (boilerplate-heavy too
early), Rust core (2–3x slower iteration), plugin/entry-point connectors (YAGNI until M5+),
TS/Node (weaker analytics/columnar ecosystem).

---

## 4. Repository Layout

```
crypcodile/
  pyproject.toml          # uv, Apache-2.0, ruff + pytest + mypy
  README.md
  LICENSE
  docs/
    superpowers/specs/    # this spec + future specs
  examples/               # runnable usage examples
  src/crypcodile/
    __init__.py
    schema/               # canonical data models (msgspec.Struct) + enums
    instruments/          # InstrumentRegistry: native <-> canonical symbol mapping
    exchanges/            # connectors
      base.py             # Connector ABC (subscribe, normalize, rest_historical)
      deribit/
      binance/
      bybit/  okx/  coinbase/   # added by Ralph after the abstraction is proven
    ingest/               # async runtime: WS manager, reconnect/backoff, gap-detect, backfill, writer
    store/                # Parquet writer (hive-partition) + DuckDB catalog/reader
    replay/               # time-ordered historical merge (Tardis replay equivalent)
    resample/             # OHLCV / snapshot / VWAP derivation at any interval
    client/               # CrypcodileClient: stream/replay/query/export/to_polars|arrow|pandas
    cli.py                # typer CLI: collect | replay | export | query | catalog
  tests/                  # golden fixtures + property + roundtrip + integration (gated)
```

---

## 5. Canonical Schema (the heart)

All exchanges are reduced to these channels. **Every record carries both `exchange_ts`
and `local_ts` as nanosecond UTC integers** — essential for latency modeling and correct
ordering in backtests (the critical Tardis detail).

Common fields on every record: `exchange_ts: int`, `local_ts: int`, `exchange: str`,
`symbol: str` (canonical instrument id).

| Channel | Key fields (beyond common) |
|---|---|
| `trade` | `price`, `amount`, `side` (buy/sell), `id` |
| `book_snapshot` | `bids: list[(px, sz)]`, `asks: list[(px, sz)]`, `depth` |
| `book_delta` | `bids: list[(px, sz)]`, `asks: list[(px, sz)]` (sz=0 ⇒ remove) |
| `book_ticker` | `bid_px, bid_sz, ask_px, ask_sz` (top-of-book quote) |
| `derivative_ticker` | `mark_price, index_price, funding_rate, predicted_funding, open_interest, last_price` |
| `options_chain` | `underlying, strike, expiry, opt_type` (C/P), `mark_iv, bid_iv, ask_iv, delta, gamma, vega, theta, mark_price, underlying_price, oi` |
| `funding` | `funding_rate, funding_timestamp` |
| `open_interest` | `open_interest, open_interest_value` |
| `liquidation` | `price, amount, side` |
| `ohlcv` | `open, high, low, close, volume, interval` (also derivable via resample) |

### Instrument identity
Canonical id is human-readable and stable, e.g.:
- `deribit:BTC-30JUN-50000-C` (option)
- `binance-futures:BTC-USDT-PERP` (perp)
- `binance-spot:BTC-USDT` (spot)

`InstrumentRegistry` maps native exchange symbols ↔ canonical ids and stores instrument
metadata (tick size, contract size, expiry, strike, kind).

---

## 6. "All Resolutions"

Store **raw ticks** (every trade, every book delta) = native resolution. Any aggregate
(OHLCV / book snapshots / VWAP) at **any interval** (1s/1m/1h/1d/…) is derived on demand
via the `resample/` module (DuckDB/Polars). "All resolutions" = store raw + resample on
request — no exploding copies of the same data.

---

## 7. Storage

- **Parquet**, hive-partitioned: `data/{exchange}/{channel}/{symbol}/date=YYYY-MM-DD/part-*.parquet`, zstd.
- **DuckDB** as the query layer over Parquet (zero-copy, SQL); catalog views per channel.
- **Polars** for in-memory transforms and as the default return type.
- Writer is append-safe, buffered, periodic flush; schema is enforced on write.

---

## 8. Ingestion Runtime

- `asyncio` + `websockets`/`aiohttp`. One connector instance per exchange-venue.
- **Supervision:** a failing connector never takes down the runtime; it is isolated,
  logged, metered, and restarted.
- **Reconnect:** exponential backoff + jitter; subscription resume; heartbeat/ping.
- **Gap detection:** sequence/timestamp gaps trigger **REST backfill** to fill holes.
- **Backfill:** REST historical pull (trades/ohlcv/funding/oi where the exchange exposes it),
  merged with live to avoid gaps at startup and after disconnects.
- **Dead-letter:** unparseable messages go to a dead-letter sink with metrics; runtime continues.

---

## 9. Delivery (v1: client + export)

`CrypcodileClient`:
- `.stream(channels, symbols) -> AsyncIterator[Record]` — live normalized stream.
- `.replay(channels, symbols, frm, to) -> Iterator[Record]` — historical, **time-ordered
  merge across symbols/channels** (Tardis replay equivalent).
- `.query(sql) -> polars.DataFrame` — DuckDB over the store.
- `.export(..., fmt=parquet|csv|arrow|json|jsonl, dest=path)` — pipe data anywhere.
- `.to_polars()/.to_pandas()/.to_arrow()` — interop.

CLI (`typer`) wraps the same: `crypcodile collect | replay | export | query | catalog`.

> The self-hosted **REST + WebSocket server** is the next spec; it serves the same
> normalized records.

---

## 10. Testing & Error Handling (TDD)

- **Golden tests:** each connector has recorded native fixture messages → asserted
  normalized output (one golden file per channel per exchange).
- **Round-trip:** write → read Parquet preserves types/values.
- **Replay ordering:** merged stream is monotonic in `local_ts` across symbols.
- **Property tests:** applying `book_delta`s to a `book_snapshot` reconstructs the book;
  resample invariants (e.g., sum of trade volume == OHLCV volume).
- **Integration (gated):** optional live smoke against a public WS, network-flagged.
- Error handling: per-connector supervision, dead-letter for bad messages, structured
  logging + counters; never crash the runtime on a single bad message or feed.

---

## 11. Milestone Gates (the "advanced enough" goal — Ralph drives these)

| Gate | Definition of done |
|---|---|
| **M1** | `schema/` + `instruments/` + `exchanges/base.py` + **Deribit & Binance** connectors live-normalizing `trade`+`book`+`book_ticker`+`derivative_ticker` (and Deribit `options_chain`); golden tests pass; `uv run pytest` green; ruff+mypy clean. |
| **M2** | Parquet store (hive-partition) + DuckDB catalog + `replay/` time-ordered merge; round-trip + ordering tests pass. |
| **M3** | `CrypcodileClient` + CLI: `collect`/`replay`/`export`/`query`/`catalog` working; multi-format export verified. |
| **M4** | Backfill + gap-detection; **≥5 exchanges** (add Bybit/OKX/Coinbase); connector test coverage ≥90%. |
| **M5** | `resample/` (all resolutions) + full derivative/options normalization; docs + runnable examples; README quickstart. |

After M5, the **next spec** begins: analytics (Laevitas-class) → server API (Amberdata-class)
→ dashboard. Per the user constraint, the project is not "delivered" until these advanced
layers exist; each is its own spec → plan → Ralph cycle.

---

## 12. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Exchange API drift / undocumented quirks | Golden fixtures pin behavior; connector tests catch drift; per-exchange normalization isolated. |
| WS rate limits / bans during dev | Use public endpoints, backoff+jitter, integration tests gated/off by default. |
| Parquet small-file explosion | Buffered batched writes, daily partitions, periodic compaction (M4+). |
| Throughput ceiling in Python | msgspec hot path now; Rust hot path is a later, isolated swap behind the connector ABC. |
| Schema churn breaking stored data | Schema version stamped in partition metadata; readers tolerate additive evolution. |
