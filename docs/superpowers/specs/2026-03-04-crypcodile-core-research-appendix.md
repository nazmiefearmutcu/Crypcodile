# Crypcodile Core — Technical Research Appendix

> Implementation-ready synthesis for engineers building Crypcodile, a Python-first crypto market-data engine (asyncio, msgspec, Polars, PyArrow, DuckDB, websockets/aiohttp). Targets Tardis.dev-grade coverage. All timestamps in the canonical layer are **nanosecond UTC integers** with both `exchange_ts` and `local_ts`. Exchange feeds report milliseconds (Deribit/Binance) or ISO 8601 (Tardis); convert `ms × 1_000_000 → ns`, `µs × 1_000 → ns`.

---

## 1. Canonical Schema Field Decisions

Design principles (from the Tardis gold-standard model, https://docs.tardis.dev/tardis-machine/data-types and https://docs.tardis.dev/downloadable-csv-files/data-types):

- **Dual timestamps, always.** `exchange_ts` (server-native, may be non-monotonic) + `local_ts` (capture clock, **monotonically increasing**, the ground truth for replay ordering). Capture `local_ts` at WebSocket message ingress with `time.clock_gettime_ns(CLOCK_REALTIME)` — never an application-layer timestamp (asyncio jitter is 10–100ms under load).
- **snake_case** field names, **uppercase exchange-native** symbols, plus a canonical `symbol` resolved by an InstrumentRegistry.
- `amount == 0` in any book level means **REMOVE that price level** (not "set to zero"), matching Tardis `book_change` and exchange diff semantics. **Caveat — this rule is canonical, not universal at the wire level**: Deribit WS book deltas signal removal via `action=delete` (the level still carries a non-zero `amount`), so the connector must translate `action=delete → amount=0`/omit *before* the canonical rule applies (see §3.1).
- Prefer `Decimal`/fixed-point for canonical price storage where exactness matters (crypto prices span BTC ~$70k → microcap ~$0.00001); `float64` acceptable for analytics columns. Volumes should remain exact.
- **`exchange_ts` may be absent on some REST snapshots.** Binance **spot** `/api/v3/depth` returns only `{lastUpdateId, bids, asks}` — no `E`/`T` timestamp. Define an explicit null policy: store `exchange_ts = NULL` (preferred — never fabricate) and rely on `local_ts` for ordering; do not silently copy `local_ts` into `exchange_ts` (see §3.2).

Common columns on **every** record: `exchange: str`, `symbol: str` (canonical), `symbol_raw: str` (exchange-native), `exchange_ts: int64` (ns), `local_ts: int64` (ns).

| Channel | Final fields (beyond common) | Notes |
|---|---|---|
| **trade** | `id: str`, `price: float64`, `amount: float64`, `side: enum(buy\|sell\|unknown)`, `liquidation: enum(M\|T\|MT)?` | `side` = aggressor/taker side. Binance: `m`(is_buyer_maker) → side = `sell` if true else `buy`. Bybit caps side (`Buy`/`Sell`) → lowercase. Deribit `liquidation` is a **string enum** `M\|T\|MT` (maker/taker/both liquidated), **not** a boolean — presence marks a liquidation event; route a copy to the `liquidation` channel (see §3.1). |
| **book_snapshot** | `bids: list[[price,amount]]`, `asks: list[[price,amount]]`, `depth: int`, `interval_ms: int`, `sequence_id: int64?`, `is_snapshot: bool=true` | Full L2 state. `depth` = level count. REST snapshots seed the WS continuity chain: Deribit seeds `sequence_id = change_id`; Binance seeds from `lastUpdateId`. |
| **book_delta** | `bids: list[[price,amount]]`, `asks: list[[price,amount]]`, `is_snapshot: bool`, `seq_id: int64`, `prev_seq_id: int64?` | `amount=0` ⇒ remove level. `prev_seq_id`/`seq_id` drive gap detection. First message post-subscribe carries `is_snapshot=true`. **`prev_seq_id` is nullable by design**: Binance **spot** WS has no `pu` field → `prev_seq_id=NULL` (continuity is `U == prev_u+1`); Binance **futures** maps `pu → prev_seq_id`; Deribit maps `prev_change_id → prev_seq_id`. |
| **book_ticker** | `bid_px: float64`, `bid_sz: float64`, `ask_px: float64`, `ask_sz: float64`, `update_id: int64?` | Top-of-book only. |
| **derivative_ticker** | `last_price`, `mark_price`, `index_price`, `funding_rate`, `predicted_funding_rate`, `funding_timestamp: int64` (next funding, **future-dated**), `open_interest` | One row collapses perp state. `funding_timestamp` is when the *next* funding occurs, not publish time. For Deribit, `funding_rate` is sourced from the ticker stream — see the `funding_rate` field-selection note below. |
| **funding** | `funding_rate: float64`, `funding_timestamp: int64` (settlement time), `predicted_funding_rate: float64?`, `interval_hours: int?` | Dedicated stream for funding settlements/history. **Deribit has no dedicated funding WS channel** — live funding must be derived from `ticker.{instrument}` (`current_funding`/`funding_8h`); only REST history exists (`interest_1h`/`interest_8h`). See §3.1. |
| **open_interest** | `open_interest: float64` (contracts/base), `open_interest_value: float64?` (USD/notional) | Deribit perp OI is USD; options OI is base currency — record units in registry. |
| **liquidation** | `id: str?`, `price: float64`, `amount: float64`, `side: enum(buy\|sell\|unknown)` | Deribit liquidations arrive embedded in the trade feed via the string `liquidation` enum (`M\|T\|MT`), not as a separate event; derive `side` from the trade `direction` and record which counterparty (`M`/`T`/`MT`) was liquidated. |
| **options_chain** (`option_summary`) | `underlying: str`, `underlying_price: float64`, `strike: float64`, `expiry: int64` (ns UTC), `opt_type: enum(C\|P)`, `mark_price`, `mark_iv`, `bid_px`, `bid_sz`, `bid_iv`, `ask_px`, `ask_sz`, `ask_iv`, `last_price`, `open_interest`, `delta`, `gamma`, `vega`, `theta`, `rho` (all greeks **nullable**) | Greeks/IV nullable — not all venues publish all. |
| **ohlcv** (derived) | `interval: str` (`1s`,`1m`,`1h`…), `open`, `high`, `low`, `close`, `volume: float64`, `buy_volume: float64`, `sell_volume: float64`, `num_trades: int?` | Derived from `trade`; can also source exchange klines for backfill. |

**`funding_rate` field-selection (Deribit) — engineer must decide and document.** Deribit exposes **two** perpetual funding fields and gives no official disambiguation of which maps to the canonical `funding_rate`:
- **`current_funding`** — the current (instantaneous) funding rate.
- **`funding_8h`** — the trailing 8-hour average funding rate.

Both update live in `ticker.{instrument}`. The connector must pick one as the canonical `funding_rate` and stash the other (recommended: canonical `funding_rate = current_funding`; carry `funding_8h` as `predicted_funding_rate`/auxiliary), **document the choice**, and define a dedup/cadence window for emission (ticker fires far more often than funding changes). The REST history endpoint reports `interest_1h`/`interest_8h` instead (there is **no** field literally named `funding_rate`), so the backfill mapper must select `interest_8h` (or `interest_1h`) explicitly.

---

## 2. Connector ABC Interface

Async ABC; one connector instance per exchange-venue. Hot path uses **msgspec Structs** (`frozen=True`), 4–17× faster than Pydantic, zero-copy decode (https://jcristharif.com/msgspec/structs.html). Use attribute access only.

```python
class Connector(ABC):
    name: str                      # e.g. "deribit", "binance-usdm"
    ws_url: str
    rest_url: str

    def __init__(self, symbols: list[str], channels: list[str], out: Sink, registry: InstrumentRegistry): ...

    # --- lifecycle ---
    async def run(self) -> None:           # supervised loop: connect→subscribe→consume→reconnect
    async def connect(self) -> None:       # open WS, set ping_interval/timeout
    async def subscribe(self) -> None:     # send sub frames; cache for replay-on-reconnect
    async def close(self) -> None:         # graceful teardown (try/finally cleanup)

    # --- per-message hot path ---
    async def on_message(self, raw: bytes) -> None:   # capture local_ts FIRST, then dispatch
    def normalize(self, msg, local_ts: int) -> Iterable[Record]:  # exchange msg → canonical Record(s)

    # --- order book ---
    def apply_book(self, msg) -> Record | None:        # diff-sync state machine, gap detection
    async def resync_book(self, symbol: str) -> None:  # REST snapshot + buffered-delta replay

    # --- historical ---
    async def backfill(self, channel: str, symbol: str,
                       start_ns: int, end_ns: int) -> AsyncIterator[Record]:  # paginated REST

    # --- introspection ---
    async def list_instruments(self) -> list[Instrument]
```

**Snapshot vs delta parsers must be separate.** A connector cannot reuse one book parser for both REST and WS on every venue. Deribit REST `get_order_book` returns **2-tuples** `[price, amount]`, while the Deribit WS `book.*` stream returns **3-tuples** `[action, price, amount]`. `apply_book` (WS deltas) and `resync_book` (REST snapshot) therefore need distinct level-parsing paths — applying WS 3-tuple parsing to a REST 2-tuple snapshot will silently mis-index price/amount.

**Lifecycle / supervision** (grounded in cryptofeed + CCXT Pro):
1. `run()` wraps `connect→subscribe→consume` in a reconnect loop with exponential backoff: **start 1s, ×2 per failure, cap 30s, add 0–25% jitter** (avoid thundering herd).
2. Heartbeat: send ping at **75% of the shortest proxy timeout** (~45s for 60s defaults); close + reconnect if no pong within 10s. Futures servers ping every 3min; spot 20s. Zombie TCP connections only surface via heartbeat timeout.
3. On reconnect: **re-subscribe from cached subscriptions** before resuming; books re-snapshot.
4. Each connector runs as a supervised task in `asyncio.TaskGroup` (3.11+); wrap a **circuit breaker** (aiobreaker: CLOSED→OPEN→HALF_OPEN) around the feed handler to isolate a flapping venue. Note TaskGroup cancels siblings on failure — use `try/finally` for cleanup.
5. **Dead-letter queue**: unparseable messages → `asyncio.Queue` storing `(local_ts, raw, error_type, traceback)`; periodic flush to Parquet; bound by max age + size; alert on spike. Never crash the runtime on one bad message.

---

## 3. Per-Exchange Connector Notes

### 3.1 Deribit (options-first derivatives)
Docs: https://docs.deribit.com/ · Tardis mapper: https://github.com/tardis-dev/tardis-node/blob/master/src/mappers/deribit.ts

- **Transport**: JSON-RPC 2.0 over WebSocket. Prod `wss://www.deribit.com/ws/api/v2`; Testnet `wss://test.deribit.com/ws/api/v2` (separate accounts, no key sharing). REST `https://www.deribit.com/api/v2`.
- **Subscribe**: `public/subscribe` with `{channels:[...]}` (named params only, no batch/positional). Max **500 channels** per subscription. Notifications arrive as `method="subscription", params={channel, data}`.
- **Channels**:
  - `trades.{instrument}.raw` | `.100ms` — fields: `trade_id`, `trade_seq` (monotonic per instrument, may skip), `price`, `amount`, `direction`(buy/sell), `timestamp`(ms), `index_price`, `mark_price`, `liquidation`(**string enum** `M`/`T`/`MT`, optional — see liquidation note), `iv`(options).
  - `book.{instrument}.raw` | `.100ms` | `.agg2` — first message is a **full snapshot**; subsequent are deltas. Levels are **3-tuples** `[action, price, amount]` with action ∈ `new|change|delete`. Carries `change_id` + `prev_change_id` for sequence validation.
  - `ticker.{instrument}` — `mark_price`, `last_price`, `index_price`, `open_interest`, best `bid/ask` px+sz; **perpetuals add `current_funding` and `funding_8h`** (the only live funding source on Deribit); options add `underlying_price`, `bid_iv`/`ask_iv`/`mark_iv`, `greeks{delta,gamma,theta,vega,rho}` (null for non-options).
  - `quote.{instrument}` — best bid/ask → `book_ticker`.
  - `deribit_price_index.{currency}` → `index_price`; `markprice.options.{index}`.
- **Symbols**: perp `BTC-PERPETUAL`; option `BTC-30JUN-50000-C` (`underlying-DDMMM-strike-C|P`, uppercase date); futures `BTC-27JUN25`. `public/get_instruments?currency={BTC|ETH|SOL|USDC}&kind={future|option|spot|future_combo|option_combo}` returns `expiration_timestamp`(ms), `strike`, `tick_size`, `contract_size`, `settlement_currency`. Note inverse (`BTC-PERPETUAL`, USD-settled) vs linear (USDC) are distinct instruments.
- **Order-book diff-sync (action-based, not amount-based)**: keep `change_id`. Each delta's `prev_change_id` must equal the last stored `change_id`. On mismatch → gap → **re-subscribe / fetch fresh book** (`public/get_order_book`). Removal is signaled by **`action=delete`** (the deleted level still carries a non-zero `amount` — ignore that amount); `action=new`/`change` set the absolute size. The universal "`amount==0` ⇒ remove" rule does **not** apply on the wire here — normalize first: emit canonical `amount=0` (or omit the level) **iff** `action=delete`.
- **REST order-book snapshot (2-tuple, seeds continuity)**: `public/get_order_book` returns `bids`/`asks` as **2-tuples** `[price, amount]` (no `action` field) plus a `change_id`, `mark_price`, `index_price`, and (perp) `funding_8h`/`current_funding`. **Store the snapshot `change_id` to seed the WS `prev_change_id` chain** — without matching it, WS continuity breaks immediately after resync. Parse this with the snapshot parser, not the WS delta parser (§2).
- **Liquidation (string enum, embedded in trades)**: the trade `liquidation` field is an **optional string** `M`/`T`/`MT` (maker / taker / both counterparties liquidated), **not** a boolean. Route: every `trades.*` message → `trade`; **if `liquidation` is present**, also emit a `liquidation` record. Derive `side` from the trade `direction`; record the enum value to identify which counterparty was liquidated. (Routing logic must test for *string presence + enum value*, never `if liquidation == true`.)
- **Live funding (no dedicated channel)**: Deribit publishes **no** WS funding stream. To capture live perpetual funding, subscribe `ticker.{instrument}` and extract `current_funding`/`funding_8h`, emitting `funding`/`derivative_ticker` records at ticker cadence with an explicit dedup window (document it). **M1 will be missing live Deribit funding entirely unless this ticker-derived path is implemented** — the `funding` channel in §1 maps to REST history + this ticker derivation, not a native stream.
- **REST backfill**:
  - Trades: `public/get_last_trades_by_instrument_and_time` (`instrument_name`, `end_timestamp` ms, `count` 1–1000, `sorting` asc/desc) → returns `has_more`; paginate by walking `end_timestamp`.
  - Funding: `public/get_funding_rate_history` (`start_timestamp`,`end_timestamp`, hourly aggregation; loop for long ranges — returns ~monthly chunks). **Returns `interest_1h` and `interest_8h` (hourly / 8-hourly rates), plus `index_price`, `prev_index_price`, `timestamp` — there is NO field named `funding_rate`.** The mapper must select `interest_8h` (or `interest_1h`) as canonical `funding_rate` and document the choice (keep it consistent with the live ticker selection).
  - Index: `public/get_index_price?index_name=btc_usd`. Instruments: `public/get_instrument`. Clock: `public/get_time` (ms) for skew checks.
- **Gotchas**: ms→ns conversion required; liquidation history removed from archive after 2023-10-03 (live `liquidation` enum still present); credit-based rate limit (default ~20 credits/s refill, pool ~200), depletion → disconnect with error **10028**; must ping ≥ every 10s or risk inactivity disconnect; options Greeks are Black-Scholes-derived (theta = min(1-day, lifetime)).
- **Schema routing**: `trades.*`→trade (+`liquidation` record when the string `liquidation` enum is present); `book.*` first msg→book_snapshot (2-tuple REST or first WS frame), rest→book_delta (3-tuple, `action`-driven); `ticker` perp→derivative_ticker (+ funding from `current_funding`/`funding_8h`), option→options_chain; `get_funding_rate_history`→funding (from `interest_8h`/`interest_1h`); `deribit_price_index`→index/derivative_ticker.

### 3.2 Binance — Spot, USDⓂ Futures, COIN-M Futures, Options (EAPI)
Docs hub: https://developers.binance.com/docs · Order-book sync: https://developers.binance.com/docs/derivatives/usds-margined-futures/websocket-market-streams/How-to-manage-a-local-order-book-correctly

| Venue | WS base | REST base | Symbol example |
|---|---|---|---|
| Spot | `wss://stream.binance.com` (data: `wss://data-stream.binance.vision`) | `https://api.binance.com/api/v3` | `BTCUSDT` |
| USDⓂ | `wss://fstream.binance.com` | `https://fapi.binance.com/fapi/v1` | `BTCUSDT`, `BTCUSDT_240927` |
| COIN-M | `wss://dstream.binance.com` | `https://dapi.binance.com/dapi/v1` | `BTCUSD_PERP`, `BTCUSD_240927` |
| Options | `wss://nbstream.binance.com/eoptions/` | `https://eapi.binance.com` | `BTC-240927-50000-C` |

- **Stream names are lowercase**: `btcusdt@aggTrade`. Up to 1024 streams/conn (Options 200); 10 incoming msgs/s; 24h session; pong required (3min futures / 20s spot, 10min timeout).
- **Channels**:
  - Trade: `{sym}@aggTrade` — `a`(agg trade id), `p`,`q`, `f`/`l`(first/last id), `T`(trade time), `m`(buyer_is_maker). `nq` (non-RPI qty) lands Dec 2025.
  - Book diff: `{sym}@depth` (`@100ms`/`@500ms`; spot default 1000ms) — `U`(first update id), `u`(final update id), `pu`(prev final id, **futures only — absent on spot**), `b`/`a` as `[price,qty]`; `qty=0` ⇒ delete.
  - Book ticker: `{sym}@bookTicker` — `b`/`B`(bid px/qty), `a`/`A`(ask px/qty), `u`(update id).
  - Mark/funding (USDⓂ/COIN-M): `{sym}@markPrice[@1s]` — `p`(mark), `i`(index), `r`(**funding_rate**), `T`(next funding time ms), `P`(est settlement).
  - Liquidation (USDⓂ/COIN-M): `{sym}@forceOrder` — `o{S(side),ap(exec px),q,z(filled),T}`. Pushes only the **largest** liquidation per 1000ms; small ones are dropped.
  - Options: `{underlying}@optionMarkPrice` (e.g. `BTC@optionMarkPrice`) — `mp`(mark), greeks `d/g/t/v`, `b`/`a`(buy/sell IV), `vo`(vol), best `bo`/`ao`. No streaming order book for options.
- **Order-book diff-sync (canonical Binance algorithm — spot and futures differ in the comparison operators)**:
  1. Open `{sym}@depth` stream, **buffer** events.
  2. GET REST snapshot (`/depth?limit=1000`, or `/fapi/v1/depth`) → `lastUpdateId`.
  3. **Discard buffered events** — **spot**: drop where `u <= lastUpdateId`; **futures**: drop where `u < lastUpdateId` (note the **strict `<`**, not `<=`).
  4. **First applied event** — **spot**: must satisfy `U <= lastUpdateId+1 AND u >= lastUpdateId+1` (the `+1` offset). **Futures**: must satisfy `U <= lastUpdateId AND u >= lastUpdateId` (no offset). Conflating the spot/futures variants silently drops valid deltas or applies stale ones.
  5. Apply sequentially. **Spot continuity**: each new event's `U` must equal previous event's `u+1`. **Futures continuity**: each event's `pu` must equal the previous event's `u`; if not, the local book is out-of-sync → **re-init from step 1**. (This is async state validation, not just a gap counter — `pu != prev_u` mandates a full REST resync.)
  6. `qty=0` removes a level; new levels may appear — never error on deleting a missing level.
- **REST depth snapshot timestamp differs by venue**:
  - **Spot** `/api/v3/depth` returns `{lastUpdateId, bids, asks}` **only — NO `E` or `T` timestamp**. The "prefer `T` over `E`" rule cannot apply to the spot snapshot; set canonical `exchange_ts = NULL` (do not fabricate from `local_ts`) and seed continuity from `lastUpdateId`.
  - **Futures** `/fapi/v1/depth` returns `{E: message_output_time_ms, T: transaction_time_ms, lastUpdateId, bids, asks}` — map `exchange_ts = T` (transaction time, preferred over `E`).
- **book_delta seq mapping**:
  - **Spot**: `u → seq_id`; **`prev_seq_id = NULL`** (no `pu` field); gap-check via `U == prev_u+1` (track the previous `u`).
  - **Futures**: `u → seq_id`; **`pu → prev_seq_id`**; gap-check via `pu == prev_u` (plus the first-event bounds above).
- **Symbol mapping**: Spot/USDⓂ = base+quote concatenated (`BTCUSDT`); dated futures append `_YYMMDD`; COIN-M perp = `BTCUSD_PERP` (inverse, no "T"); contract multipliers differ (BTC=100 USD, ETH=10 USD). Pull full lists from `/exchangeInfo` (spot), `/fapi/v1/exchangeInfo`, `/dapi/v1/exchangeInfo`.
- **REST backfill**:
  - Trades/aggTrades: `/aggTrades` (`symbol`, `startTime`/`endTime` or `fromId`, `limit`≤1000) — paginate by `fromId`.
  - OHLCV: `/klines` (`interval`, `startTime`,`endTime`, `limit`≤1000, weight 2) → `[openTime,o,h,l,c,v,closeTime,quoteVol,count,takerBuyBaseVol,takerBuyQuoteVol]`.
  - Depth snapshot: `/depth?symbol&limit` (weight 5→250 by depth).
  - Open interest: `/fapi/v1/openInterest` / `/dapi/v1/openInterest` (weight 1; `{openInterest,symbol,time}`); historical via `/futures/data/openInterestHist`.
- **Gotchas**: all stream names lowercase; rate limits **IP-based** (spot 6000 weight/min, futures 2400); `E`=event-receive time, `T`=transaction time (closer to exchange clock) — prefer `T` for `exchange_ts` **where present** (absent on spot REST depth); USDⓂ funding settles every **4h** (00/04/08/12/16/20 UTC) as of 2025; RPI orders excluded from bookTicker/depth but counted in aggTrade qty; Options greeks are single-letter in WS, full names in REST; EAPI is a separate API surface.

---

## 4. Storage Layout & DuckDB Query Patterns

Production tuning (2025–26): https://duckdb.org/docs/current/data/parquet/overview · https://duckdb.org/docs/1.3/data/partitioning/partitioned_writes

**Partition layout** — hash-bucket symbols to avoid directory explosion (>1000 symbol dirs = small-file hell):
```
data/exchange={E}/channel={C}/date=YYYY-MM-DD/bucket={0..127}/part-*.parquet
bucket = hash(symbol) % 128          # partition column, NOT stored in record body
```
Partition column order for predicate pushdown: `[exchange, channel, date, bucket]`.

**Write parameters**:
- Prefer **Polars** (≈2.5× faster than PyArrow): `df.write_parquet(path, compression="zstd", compression_level=5, row_group_size=250_000)`.
- PyArrow when per-column codecs needed: `write_table(t, path, compression="zstd", compression_level=5, row_group_size=250_000, use_dictionary=True, version="2.6")` (`2.6` = nanosecond timestamp support).
- ZSTD **level 5** is the streaming sweet spot (stay ≤5; level >10 is far too slow for ingest). Row groups 250K–500K rows. Target file size 128 MB–1 GB; avoid <128 KB.
- Dictionary-encode `exchange`/`channel`/`symbol`.

**Buffered async sink** (kills the small-files problem): queue records → flush on **100K–500K rows OR every 5s** (whichever first; the time bound prevents losing recent ticks on crash). Parquet footers are immutable — **never append to a file**; write new files into the partition dir or use DuckDB `PARTITION_BY ... APPEND`.

```sql
-- Partitioned write
COPY (SELECT * FROM staging) TO 'data'
  (FORMAT parquet, PARTITION_BY (exchange, channel, date),
   COMPRESSION zstd, COMPRESSION_LEVEL 5, ROW_GROUP_SIZE 250000, APPEND);

-- Catalog view over the lake
CREATE VIEW trades AS
  SELECT * FROM read_parquet(
    'data/exchange=*/channel=trade/date=*/bucket=*/part-*.parquet',
    hive_partitioning => true, union_by_name => true);

-- Pushdown-friendly query (narrow the glob, then filter)
SELECT * FROM read_parquet(
  'data/exchange=deribit/channel=trade/date=2026-03-04/bucket=*/part-*.parquet',
  hive_partitioning => true)
WHERE symbol = 'BTC-PERPETUAL'
  AND local_ts BETWEEN 1749000000000000000 AND 1749003600000000000
ORDER BY local_ts;

-- OHLCV from trades (1m bars, buy/sell split)
SELECT symbol,
       time_bucket(INTERVAL '1 minute', to_timestamp(local_ts/1e9)) AS bar,
       first(price ORDER BY local_ts) AS open,
       max(price) AS high, min(price) AS low,
       last(price ORDER BY local_ts) AS close,
       sum(amount) AS volume,
       sum(CASE WHEN side='buy'  THEN amount ELSE 0 END) AS buy_volume,
       sum(CASE WHEN side='sell' THEN amount ELSE 0 END) AS sell_volume
FROM trades
WHERE symbol='BTC-PERPETUAL' AND date='2026-03-04'
GROUP BY 1,2 ORDER BY 2;
```

- Partition pruning + row-group min/max cut full scans dramatically (~1.2s→0.3s in cited benches). **Always narrow the glob path before `WHERE`** — `data/*/*.parquet` discovers all files first, then filters (extra HTTP GETs on S3).
- **Schema evolution**: add **nullable columns only**; stamp a `schema_version` in Parquet metadata on every batch; `union_by_name` fills missing cols with NULL (test roundtrips — silent drift otherwise). Write sorted by `local_ts` within a partition so replay is a sequential read.
- **Nullable `exchange_ts` is expected** (Binance spot REST snapshots, etc.) — queries that filter or align on `exchange_ts` must tolerate NULLs; use `local_ts` as the ordering fallback.

---

## 5. Replay Engine + Order-Book Reconstruction

Model: Tardis `replay-normalized` sorts **all** messages across symbols/exchanges by `local_ts` (~50–100k msg/s with book snapshots). Nautilus enforces pre-sorted input; cryptofeed checkpoints snapshots to prevent drift.

**Memory-bounded k-way merge** — `heapq.merge(*streams, key=...)` keeps O(k) memory (one buffered item per stream), O(N log k) time. **Inputs must already be sorted** (heapq.merge silently produces wrong output otherwise — files are written sorted by `local_ts` per §4).
- Sort key (deterministic tie-break): `(local_ts, exchange_ts, seq_num)`. Coarse `local_ts` granularity is why the secondary keys matter; capture `local_ts` at ≥µs precision. **`exchange_ts` may be NULL** (spot REST snapshots) — push NULLs to a consistent position (e.g. treat NULL as `-inf` or fall through to `seq_num`) so the tie-break stays deterministic.

**Streaming parquet iteration**: Polars `scan_parquet(...).filter(...)` (filter pushdown is streaming-safe) or DuckDB row-group parallelism. **`sort()` is NOT streaming** in Polars — it silently falls back to full materialization; verify with `explain(streaming=True)`, defer sorts post-filter, or `sink_parquet`. Pre-sorting on disk avoids this entirely. Process date-partitioned batches to bound memory.

**Order-book reconstruction (snapshot-anchored state machine)**:
1. Skip all rows **before the first snapshot** (`is_snapshot=true`); reconstruction silently corrupts otherwise.
2. On snapshot: **reset** book state (a later `is_snapshot=true` after deltas = connection restart/resync — discard pending deltas).
3. Maintain bids/asks as a sorted structure — Red-Black tree / `sortedcontainers` (O(log n)) or `bisect` on lists (simpler, O(n) insert).
4. Apply each delta level: `amount>0` ⇒ set absolute size at that price; `amount==0` ⇒ **remove** level. (This is the *canonical* representation — venue-specific removal signals like Deribit `action=delete` must already have been normalized to `amount=0`/omit at ingest, §3.1.)
5. **Batch by identical `local_ts`** — one exchange WS message emits multiple levels; apply them as one atomic transaction so intermediate states aren't observable.
6. **Gap detection — handle both venue shapes**: the canonical check is `seq_id` monotonic with `prev_seq_id == last seq_id`. But `prev_seq_id` may be **NULL** (Binance spot has only the `U/u` pair, no `pu`): for spot-shaped records, validate `U == prev_u+1` against the tracked previous `u` instead of `prev_seq_id`. For futures/Deribit-shaped records use the `prev_seq_id == last seq_id` scalar check. On gap → halt, resync from REST snapshot (`/depth`, `get_order_book`), discard deltas with `seq < snapshot.seq`, resume. Even one missed delta corrupts the book.
7. Flag (don't crash on) bid/ask overlap — some venues omit opposite-side deletes; emit a data-quality warning. Handle 32/64-bit sequence wrap as a warning, not error.

**OHLCV derivation**: group `trade` by time window → `open=first`, `high=max`, `low=min`, `close=last`, `volume=Σamount`, split `buy_volume`/`sell_volume` by `side`; emit empty/forward-filled bars for gap intervals. Use fixed-point/Decimal for price aggregation given the dynamic range.

---

## 6. Ingestion Runtime Patterns

| Concern | Pattern |
|---|---|
| **Reconnect/backoff** | Exponential: 1s → ×2 → cap 30s, **+0–25% jitter**. Always jitter (thundering-herd on shared outage recovery). |
| **Heartbeat** | Ping at 75% of shortest proxy timeout (~45s); drop + reconnect if no pong in 10s. Configure `ping_interval`/`ping_timeout` on the `websockets` client. Zombie TCP only detectable via heartbeat. |
| **Re-subscribe** | Cache subscription requests; replay them on every reconnect before resuming the live feed. Re-snapshot all books. |
| **Gap detect** | Per-symbol seq tracking. **Binance spot** = `U/u` only (`U==prev_u+1`, no `pu`); **Binance futures** = `pu==prev_u` (resync if violated); **Deribit** = `prev_change_id`; Bybit/Kraken seq. Gap ⇒ buffer live deltas, fire REST snapshot, apply REST then buffered deltas in order (handle in-flight race). REST snapshot seeds the continuity chain (Deribit `change_id`, Binance `lastUpdateId`). |
| **Supervision** | `asyncio.TaskGroup` (3.11+) per connector; `try/finally` cleanup (siblings cancel on one failure). Circuit breaker (aiobreaker) around each feed handler — CLOSED→OPEN→HALF_OPEN isolates a flapping venue. |
| **Dead-letter queue** | Failed decodes → `asyncio.Queue` of `(local_ts, raw, error_type, traceback)`; periodic Parquet dump; bound by max age+size; metrics + alert on spike. Never crash on one bad message. |
| **Decode hot path** | msgspec `Struct(frozen=True)` — 4–17× faster than Pydantic, zero-copy. Attribute access only. |
| **Timestamps** | `local_ts` via `clock_gettime_ns(CLOCK_REALTIME)` at ingress; `exchange_ts` from `T`/transaction field **where present** (absent on Binance spot REST depth ⇒ `exchange_ts=NULL`). Order runtime by `local_ts`; align backtests by `exchange_ts`. Stale (`local_ts - exchange_ts > 1min`) ⇒ warn, don't discard. |
| **Snapshot drift** | If local book ≠ fresh REST snapshot, rebuild from snapshot (missed/misapplied deltas). |

---

## 7. M4 Exchanges Quick-Reference (Bybit / OKX / Coinbase)

| | **Bybit V5** | **OKX V5** | **Coinbase Advanced Trade** |
|---|---|---|---|
| WS public | `wss://stream.bybit.com/v5/public/{spot\|linear\|inverse\|option}` | `wss://ws.okx.com:8443/ws/v5/public` (+`/business`,`/private`) | `wss://ws-feed.exchange.coinbase.com` (pub+priv, JWT) |
| REST | `https://api.bybit.com/v5` | `https://openapi.okx.com` (region: `us.okx.com`, `eea.okx.com`) | `https://api.coinbase.com/api/v1/brokerage` |
| Symbols | `BTCUSDT` (linear), `BTCUSD` (inverse), upper | `BTC-USDT` (spot), `BTC-USDT-SWAP` (perp), `BTC-USD-25DEC22-40000-C` (opt) | `BTC-USD` (product_id, canonical) |
| Sub format | `{op:"subscribe",args:["publicTrade.BTCUSDT"]}` (dot-delimited) | `{op:"subscribe",args:[{channel:"trades",instId:"BTC-USDT"}]}` | `{type:"subscribe",product_ids:["BTC-USD"],channels:["matches"]}` |
| Trades | `publicTrade.{sym}` | `trades` | `matches` |
| Book | `orderbook.{1\|50\|200\|500}.{sym}` (snapshot+delta) | `books`/`books5`/`bbo-tbt`/`books50-l2-tbt` (snapshots; apply incrementally) | `level2` (snapshot then incremental) |
| Ticker | `tickers.{sym}` (+greeks if option) | `tickers` | `ticker` |
| Funding | REST `/v5/market/funding-rate` (no public WS) | WS `funding-rate` + REST `/api/v5/public/funding-rate` | n/a (spot only) |
| OI | REST `/v5/market/open-interest` | WS `open-interest` + REST | n/a |
| Liquidation | REST `/v5/market/liqrecords` (no WS) | WS `liq-orders` (public) | n/a |
| Options | `/v5/market/option-chain`, ticker greeks `delta/gamma/theta/vega` | `option-summary` channel + `/api/v5/market/option-summary` | n/a |
| Recent trades REST | `/v5/market/recent-trade` (≤1000) | `/api/v5/market/trades` (≤500) | `/products/{id}/trades` (≤100) |
| Rate limits | market 10/s; WS 1 sub/s/IP | public 10/s/IP, WS 3 req/s, 480 sub/h, 30 ch/sub-acct | REST ~5/s public; ~100 subs/conn |

**Critical quirks**: Bybit `side` is capitalized (`Buy`/`Sell`) → lowercase; greeks only on `category=option`; `category` must match symbol type. OKX `books*` are **snapshots, not deltas** — maintain L2 locally; **region endpoints are mandatory** (US/AU→`us.okx.com`, EU→`eea.okx.com`, mixing invalidates keys/rate limits); demo needs header `x-simulated-trading: 1`; option `instId` needs a compound parser; signing timestamp must be ISO 8601 ms-precision UTC. Coinbase `product_id` is canonical — cache `/products` (cannot derive from symbol); `level2` has no funding/OI/liquidation (spot only); WS JWT is ~2min-lived (resubscribe on expiry).

---

## 8. Key Gotchas (Consolidated)

- **Timestamps**: Deribit/Binance = ms (`×1e6→ns`); Tardis CSV = µs (`×1e3→ns`). `exchange_ts` can be non-monotonic — only `local_ts` is ordering-safe. Prefer Binance `T` over `E` **where present**; Binance **spot** REST `/api/v3/depth` has **neither** `E` nor `T` ⇒ `exchange_ts=NULL` (never fabricate from `local_ts`); Binance **futures** `/fapi/v1/depth` does carry `E`/`T` ⇒ use `T`.
- **Book deltas (canonical vs wire)**: canonical `amount==0` = **remove level**, never "set 0". But the *wire* removal signal differs: Binance `qty==0` = delete; **Deribit uses `action=delete`** (level still carries a non-zero `amount` — ignore it; normalize to `amount=0`/omit at ingest). First post-subscribe message = snapshot. One missed delta corrupts the whole book → mandatory seq validation + REST resync.
- **Deribit book parsers split by transport**: WS `book.*` levels are **3-tuples** `[action, price, amount]`; REST `get_order_book` levels are **2-tuples** `[price, amount]` + `change_id`. Use separate parsers; **store the REST `change_id` to seed the WS `prev_change_id` chain** or continuity breaks at resync.
- **Binance sync (spot ≠ futures)**: discard threshold differs — spot drops `u <= lastUpdateId`, futures drops `u < lastUpdateId` (strict). First-event bounds differ — spot needs the `+1` offset (`U<=lastUpdateId+1 AND u>=lastUpdateId+1`), futures has no offset (`U<=lastUpdateId AND u>=lastUpdateId`). Continuity — spot `U==prev_u+1`; futures `pu==prev_u` (and **`pu` is absent on spot**). Re-init on continuity break. Map spot `seq_id=u, prev_seq_id=NULL`; futures `seq_id=u, prev_seq_id=pu`.
- **Deribit**: `prev_change_id` continuity; credit rate-limit → error 10028 disconnect; liquidation history gone post-2023-10-03; ping ≤10s.
- **Liquidations**: Deribit `liquidation` is a **string enum** `M\|T\|MT` embedded in the trade feed (not boolean, not a separate stream) — test for *presence + enum value*, never `== true`; Binance `@forceOrder` only emits the **largest** per 1000ms; Bybit has no WS (REST only); OKX `liq-orders` is event-sparse.
- **Funding**: USDⓂ now 4h cadence; `funding_timestamp` is the *next/future* event, not publish time; Bybit funding is REST-only. **Deribit has NO dedicated funding WS channel** — live funding is ticker-derived (`current_funding` vs `funding_8h`; pick one as canonical and document); REST history reports `interest_1h`/`interest_8h` (no `funding_rate` field). M1 misses live Deribit funding unless the ticker path is built.
- **Options**: greeks/IV nullable and venue-specific (Deribit full BS greeks; Binance WS single-letter `d/g/t/v`); no streaming options order book on Binance.
- **Symbols**: always uppercase exchange-native; COIN-M perp = `BTCUSD_PERP` (no "T"), inverse multipliers differ; OKX option `instId` needs compound parsing; Coinbase `product_id` must be cached from `/products`.
- **Storage**: never append to a Parquet file (immutable footer); avoid per-symbol partitions (use hash buckets); narrow glob before `WHERE`; ZSTD ≤5 for ingest; additive-nullable schema changes only; `exchange_ts` may legitimately be NULL.
- **Replay**: `heapq.merge` needs pre-sorted inputs (silent corruption otherwise); Polars `sort()` breaks streaming; skip pre-first-snapshot rows; batch deltas by identical `local_ts`; sort tie-break must tolerate NULL `exchange_ts`; gap-detector must handle **both** the spot `U/u` shape and the futures/Deribit `seq_id/prev_seq_id` scalar shape.
- **Runtime**: always jitter backoff; heartbeat is the only zombie-connection detector; TaskGroup cancels siblings on one failure; bound the DLQ; RPI trades can mislead `is_buyer_maker`.
- **Rate limits**: Binance IP-based (shared IP throttles all); OKX trading limits shared REST+WS; respect per-connection sub caps.

---

## 9. Sources

**Tardis (canonical model)**: https://docs.tardis.dev/tardis-machine/data-types · https://docs.tardis.dev/downloadable-csv-files/data-types · https://docs.tardis.dev/faq/order-books · https://docs.tardis.dev/node-client/normalization · https://github.com/tardis-dev/tardis-node · https://github.com/tardis-dev/tardis-node/blob/master/src/mappers/deribit.ts

**Deribit**: https://docs.deribit.com/ · https://docs.deribit.com/subscriptions/orderbook/bookinstrument_nameinterval.md · https://docs.deribit.com/api-reference/market-data/public-get_order_book.md · https://docs.deribit.com/articles/market-data-collection-best-practices · https://docs.deribit.com/api-reference/market-data/public-ticker.md · https://docs.deribit.com/api-reference/market-data/public-get_last_trades_by_instrument_and_time.md · https://docs.deribit.com/api-reference/upcoming/market-data/public-get_funding_rate_history · https://docs.deribit.com/api-reference/market-data/public-get_funding_rate_history.md · https://support.deribit.com/hc/en-us/articles/25944617523357-Rate-Limits · https://docs.tardis.dev/historical-data-details/deribit

**Binance**: https://developers.binance.com/docs/binance-spot-api-docs/web-socket-streams · https://github.com/binance/binance-spot-api-docs/blob/master/web-socket-streams.md#how-to-manage-a-local-order-book-correctly · https://raw.githubusercontent.com/binance/binance-spot-api-docs/master/rest-api.md · https://developers.binance.com/docs/derivatives/usds-margined-futures/websocket-market-streams · https://developers.binance.com/docs/derivatives/usds-margined-futures/websocket-market-streams/How-to-manage-a-local-order-book-correctly · https://developers.binance.com/docs/derivatives/usds-margined-futures/websocket-market-streams/Mark-Price-Stream · https://developers.binance.com/docs/derivatives/coin-margined-futures/websocket-market-streams · https://developers.binance.com/docs/derivatives/options-trading/websocket-market-streams · https://developers.binance.com/docs/binance-spot-api-docs/rest-api/market-data-endpoints · https://developers.binance.com/docs/binance-spot-api-docs/rest-api/limits

**Storage (Parquet/DuckDB/Polars)**: https://arrow.apache.org/docs/python/parquet.html · https://duckdb.org/docs/current/data/parquet/overview · https://duckdb.org/docs/1.3/data/partitioning/partitioned_writes · https://docs.pola.rs/api/python/stable/reference/api/polars.DataFrame.write_parquet.html · https://docs.duckdb.org/en/stable/data/parquet/tips

**Replay / order book**: https://docs.tardis.dev/node-client/normalization · https://nautilustrader.io/docs/latest/concepts/backtesting/ · https://github.com/bmoscon/cryptofeed/discussions/648 · https://docs.python.org/3/library/heapq.html · https://www.rhosignal.com/posts/streaming-in-polars/ · https://kb.dxfeed.com/en/data-model/market-events/dxfeed-order-book/order-book-reconstruction.html

**Runtime**: https://github.com/bmoscon/cryptofeed · https://docs.ccxt.com/en/latest/ccxt.pro.manual.html · https://websockets.readthedocs.io/en/stable/topics/keepalive.html · https://websocket.org/guides/heartbeat/ · https://jcristharif.com/msgspec/structs.html · https://github.com/arlyon/aiobreaker · https://docs.python.org/3/library/asyncio-task.html

**M4 (Bybit/OKX/Coinbase)**: https://bybit-exchange.github.io/docs/v5/intro · https://bybit-exchange.github.io/docs/v5/market/orderbook · https://www.okx.com/docs-v5/en/ · https://www.okx.com/docs-v5/en/#order-books · https://www.okx.com/docs-v5/en/#funding-rate · https://docs.cdp.coinbase.com/advanced-trade/docs/rest-api-overview · https://github.com/bybit-exchange/pybit
