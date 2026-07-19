<p align="center"><img src="assets/logo.svg" width="84" alt="Crypcodile"></p>

<h1 align="center">Crypcodile</h1>

<p align="center"><b>A deterministic engine for the whole crypto market.</b><br>
Pull order books, trades, funding and on-chain DEX events from 100+ venues into one
Parquet lake — then replay any slice of it byte-for-byte.</p>

<p align="center"><sub>Python 3.12+ · Apache-2.0 · public market data needs no API keys</sub></p>

---

Every market-data tool can fetch a price. Two things make this one different.

**One schema for everything.** A hand-written Deribit connector, a ccxt Kraken
venue, and a Uniswap V3 pool on Base all emit the *same* record types. So a
question like "show me every BTC trade across all my sources" is one SQL
statement, not three codebases.

**The lake is deterministic.** What lands on disk is normalized, validated, and
replayable — `replay` a window today or next year and you get identical bytes.
No hidden clocks, no re-fetching, no drift.

On top of that sits an options and microstructure analytics library, **FlowMap**
(a GPU order-flow visualizer), and an **MCP server** so LLM agents read real
prices instead of inventing them.

![FlowMap rendering live BTCUSDT order flow](docs/media/flowmap-btcusdt-live.png)

## Install

```bash
uv pip install crypcodile          # or: pip install crypcodile
```

The base install is the whole streaming core: every native connector, the
Parquet lake, replay, and the 47-command CLI. Heavier surfaces are opt-in extras
so you never pay for a dependency tree you don't use:

```bash
uv pip install 'crypcodile[market]'   # +100 exchanges via the universal ccxt connector
uv pip install 'crypcodile[gui]'      # FlowMap visualizer + gas tracker (PyQt6)
uv pip install 'crypcodile[ml]'       # funding prediction + Black-Scholes (xgboost/scipy)
uv pip install 'crypcodile[web]'      # FastAPI server + Streamlit examples
uv pip install 'crypcodile[onchain]'  # Base L2 / GMX / Superchain connectors (web3)
uv pip install 'crypcodile[full]'     # everything
```

Prefer one command? [`install.sh`](install.sh) (macOS/Linux) and
[`install.ps1`](install.ps1) (Windows) install `crypcodile[full]`.

## Five minutes

```bash
# stream Deribit BTC-perp trades + book deltas into a local lake
crypcodile collect --exchange deribit --symbols BTC-PERPETUAL \
    --channels trade --channels book_delta --data-dir data

# the lake is just partitioned Parquet — ask it anything in DuckDB SQL
crypcodile query "SELECT count(*) FROM records WHERE channel = 'trade'"

# replay that window later — identical bytes, every run
crypcodile replay --channels trade --symbols deribit:BTC-PERPETUAL

# open the order-flow visualizer on live Binance data  ([gui] extra)
crypcodile flowmap --symbol binance-spot:BTCUSDT --historical-hours 2.0
```

No lake yet? `replay` and `query` fall back to the bundled sample in
[`test_data/`](test_data/), so a fresh clone works offline. There's also an
interactive shell — `crypcodile shell` — with history and tab-completion, and
every command runs inside it.

## Reaching the whole market

Crypcodile speaks to **108 venues**: ten native connectors, hand-written for
fidelity, plus the entire [ccxt](https://github.com/ccxt/ccxt) family (104
exchanges) behind one universal connector. When a name exists in both, the
native connector wins.

| | Venues |
|---|---|
| **Native** | Binance · Bybit · Coinbase · Deribit · OKX · Base on-chain (Uniswap V3, Aerodrome) · GMX/Synthetix · Derive · Superchain · CoinGecko |
| **Universal** | any of ccxt's 104 exchanges — Kraken, KuCoin, MEXC, Gate, HTX, Bitget, … |

You don't have to name symbols. Name a *slice of the market* and Crypcodile
resolves the concrete list from the live universe:

```bash
# the 200 most-liquid pairs on Binance, streamed over a single WebSocket
crypcodile collect-market --exchange binance --top 200 --use-ws \
    --channels trade --channels book_ticker

# every USDT perpetual across three venues at once, order books included
crypcodile collect-market --exchange bybit,okx,mexc --all \
    --quote USDT --kind perpetual --channels book_snapshot --limit 400

# the whole coin universe — 17k+ coins, including the long tail no CEX lists
crypcodile collect --exchange coingecko --symbols _ --channels ohlcv
```

Two design choices make that scale honestly. The ccxt path is **REST-poll-first**
(works on every venue) but upgrades to a **single multi-symbol WebSocket** per
channel where the exchange supports it (`watchTradesForSymbols` / `watchTickers`)
— the difference between one socket for three symbols and one socket for a whole
exchange's book. And `universe` ranks any venue's markets by live 24h volume, so
`--top N` covers the liquid core instead of ten thousand dead pairs.

### The whole market, on one screen

`crypcodile census` measures the market live and writes a self-contained HTML
dashboard — venue market counts (ccxt), the coin universe + market cap +
dominance (CoinGecko), and total value locked (DeFiLlama). Every figure comes
from a keyless public feed; a recent run:

> **108** reachable venues · **34,171** markets across the majors ·
> **17,657** active coins · **$2.29T** market cap · **$75.8B** DeFi TVL

```bash
crypcodile census                  # → census.html + a terminal summary
```

## The 47 commands

| Cluster | Commands |
|---|---|
| **Lake** | `collect` · `collect-market` · `backfill` · `replay` · `query` · `export` |
| **Discovery** | `census` · `markets` · `universe` · `search` · `resolve-symbols` · `data-coverage` · `catalog*` (7) · `list-exchanges` |
| **Options & funding** | `iv-surface` · `term-structure` · `vol-skew` · `risk-reversal` · `funding-apr` · `funding-predict` · `basis` · `open-interest` |
| **Microstructure** | `ofi` · `slippage` · `whale-alerts` · `liquidity-depth` · `indicators` |
| **On-chain / L2 risk** | `sequencer-latency` · `peg-deviation` · `chaos-score` · `lending-stress` · `gas-vol` · `smart-money` · `label-transfers` · `mev-sandwich` |
| **Desktop** | `flowmap` · `gas-tracker` |
| **Servers** | `mcp` · `api` |
| **Shell** | `shell` · `update` |

Ingest survives disconnects with sequence-gap bridging and a dead-letter queue
([`src/crypcodile/ingest/`](src/crypcodile/ingest/)); whatever reaches disk is
normalized against the [16-record schema](src/crypcodile/schema/records.py) and
replayable.

## FlowMap

![FlowMap settings and trackers panel](docs/media/flowmap-btcusdt-settings.png)

FlowMap paints resting book depth over time as a liquidity heatmap and layers
the tape on top: aggressor-colored trade bubbles, VWAP and BBO tags,
COB/CVP/SVP volume profiles, a cumulative-delta strip, a DOM ladder, and
iceberg / large-lot trackers. Feed it live data, a lake replay, or a built-in
synthetic market for poking at the UI offline.

```bash
crypcodile flowmap --symbol binance-spot:BTCUSDT --historical-hours 2.0
```

It renders on `QOpenGLWidget` with a pure-NumPy density engine behind it (pin a
backend with `FLOWMAP_RENDERER=opengl|cpu`). The uncapped offscreen benchmark
clears 100 FPS at 1920×1080 on Apple Silicon; the window itself sits at vsync.

## Analytics

The lake feeds an options + microstructure library, reachable three ways — CLI,
MCP tool, or plain Python over a `Catalog`. Two runnable examples in
[`examples/`](examples/):

```python
from crypcodile.analytics.funding import funding_apr
from crypcodile.analytics.volsurface import iv_surface
from crypcodile.store.catalog import Catalog

catalog = Catalog(data_dir="data")
apr     = funding_apr(catalog, "binance:BTCUSDT", from_ns, to_ns)  # Polars DataFrame
surface = iv_surface(catalog, "BTC", at_ns, rate=0.0)             # strike × expiry × IV
```

The full set spans OFI, slippage, whale alerts, term structure, vol skew, risk
reversal, spot–perp / spot–future basis, open interest, and an L2/DeFi-risk
family (sequencer latency, peg deviation, lending stress, MEV-sandwich
detection). Each reads the same normalized records — native venue or ccxt, it
can't tell the difference.

## For agents (MCP)

```bash
crypcodile mcp --data-dir data     # Model Context Protocol server over stdio
```

Every tool is read-only and deterministic — answers come from the lake and the
chain, never the model's imagination. Tools cover market-data reads (bounded
DuckDB SQL, on-chain prices), catalog discovery (`search_symbols`,
`list_all_exchanges`, coverage, inventory), and the analytics library. Works
with Claude, Cursor, or anything that speaks MCP.

## REST API

`crypcodile api` serves the same lake over FastAPI at `/api/v1/*` — ops and
catalog discovery, a bounded read-only `POST /query`, the derivatives and
microstructure analytics, and a payment-gated demo route over the x402 protocol.

## Base L2

`BaseOnchainConnector` reads Uniswap V3 and Aerodrome swap/reserve events from
Base RPC logs and emits the same record types as the CEX connectors, so a
cross-venue query is one SQL statement instead of two codebases. Start with
[docs/base_quickstart.md](docs/base_quickstart.md); there's a Streamlit
dashboard and a Farcaster frame server under [`examples/`](examples/). Public
data needs no keys; on-chain reads use a default Base RPC you can override.

## Tests

```bash
uv sync --all-extras
pytest tests/
```

1,760 test functions across 141 files: a local mock-RPC server for
degraded-network E2E runs (`tests/e2e/`), adversarial payload suites, and a
regression file seeded by real exchange API anomalies. `mypy --strict` and Ruff
gate `src/`. CI-friendly — Qt and Matplotlib run headless, and BLAS thread caps
keep imports fast on Apple Silicon.

## What it isn't

- **Not a trading bot.** There is no order-execution path, by design.
- **Not a hosted service.** Everything runs on your machine, against your lake.
- **Not magic.** Options analytics need options data — point `iv-surface` at a
  lake with Deribit snapshots in it, not an empty directory.
- FlowMap is a desktop app; it needs a display. The data pipeline doesn't.

## Contributing

PRs welcome. Skim [`CHANGELOG.md`](CHANGELOG.md) for direction, keep the
`mypy --strict` and Ruff gates green, and make sure the E2E and adversarial
suites pass before opening one.

Apache-2.0 — see [LICENSE](LICENSE).
