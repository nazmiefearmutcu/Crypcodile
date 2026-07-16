<p align="center"><img src="assets/logo.svg" width="88" alt="Crypcodile logo"></p>

# Crypcodile

Crypto market-data engine with a deterministic core. It pulls order books,
trades, funding and on-chain DEX events from nine venues into one
Parquet + DuckDB data lake, replays any slice of it byte-for-byte, and runs
options and microstructure analytics on top. It ships with **FlowMap**, a
GPU order-flow visualizer, and an **MCP server** so LLM agents can read real
prices instead of inventing them.

Python 3.12+, Apache-2.0. Public market data needs no API keys; on-chain
reads use a default Base RPC endpoint you can override.

![FlowMap rendering live BTCUSDT order flow](docs/media/flowmap-btcusdt-live.png)

## Install

```bash
uv pip install crypcodile     # or: pip install crypcodile
```

One-shot installers if you prefer: [`install.sh`](install.sh) (macOS/Linux),
[`install.ps1`](install.ps1) (Windows).

## First ten minutes

```bash
# stream Deribit BTC perp trades + book deltas into a local Parquet lake
crypcodile collect --exchange deribit --symbols BTC-PERPETUAL \
    --channels trade --channels book_delta --data-dir data

# find out what you actually have
crypcodile search "btc" --channel trade --exchange deribit
crypcodile data-coverage --symbol deribit:BTC-PERPETUAL --channel trade

# ask the lake anything — it's just DuckDB over partitioned Parquet
crypcodile query "SELECT count(*) FROM records WHERE channel='trade'"

# replay the same window later; identical bytes, every run
crypcodile replay --channels trade --symbols deribit:BTC-PERPETUAL

# open the order-flow visualizer on live Binance data
crypcodile flowmap --symbol binance-spot:BTCUSDT --historical-hours 2.0
```

There is also an interactive shell (`crypcodile shell`) with history and
tab-completion; every command works inside it. No lake yet? `replay` and
`query` fall back to the sample data in [`test_data/`](test_data/), so the
commands above work offline on a fresh clone.

## Commands

43 commands behind one binary. The clusters:

| Cluster | Commands |
|---|---|
| Lake | `collect` `backfill` `replay` `query` `export` |
| Discovery | `search` `resolve-symbols` `data-coverage` `catalog` `catalog-summary` `catalog-stats` `catalog-dates` `catalog-symbols` `catalog-inventory` `catalog-exchanges` `list-exchanges` |
| Options & funding | `iv-surface` `term-structure` `vol-skew` `risk-reversal` `funding-apr` `funding-predict` `basis` `open-interest` |
| Microstructure | `ofi` `slippage` `whale-alerts` `liquidity-depth` `indicators` |
| On-chain / L2 risk | `sequencer-latency` `peg-deviation` `chaos-score` `lending-stress` `gas-vol` `smart-money` `label-transfers` `mev-sandwich` |
| Desktop | `flowmap` `gas-tracker` |
| Servers | `mcp` `api` |
| Housekeeping | `shell` `update` |

Nine connectors sit behind the same record schema: Binance, Bybit, Coinbase,
Deribit, OKX, Base on-chain (Uniswap V3, Aerodrome), GMX/Synthetix, Derive
and Superchain. Ingest survives disconnects with gap-bridging and a
dead-letter queue ([`src/crypcodile/ingest/`](src/crypcodile/ingest/));
whatever made it to disk is normalized, validated and replayable.

## FlowMap

![FlowMap settings and trackers panel](docs/media/flowmap-btcusdt-settings.png)

FlowMap paints resting book depth over time as a liquidity heatmap and layers
the rest of the tape on top: aggressor-colored trade bubbles, VWAP and BBO
tags, COB/CVP/SVP volume profiles, a cumulative-delta strip, DOM ladder, and
iceberg / large-lot trackers. Three data sources: live, lake replay, or a
built-in synthetic market for poking at the UI offline.

```bash
crypcodile flowmap --symbol binance-spot:BTCUSDT --historical-hours 2.0
```

Rendering is `QOpenGLWidget` by default with a pure-NumPy density engine
behind it (force a backend with `FLOWMAP_RENDERER=opengl|cpu`). The uncapped
offscreen benchmark does 100+ FPS at 1920×1080 on Apple Silicon; the window
itself stays comfortably at vsync.

## For agents (MCP)

`crypcodile mcp --data-dir data` starts a Model Context Protocol server over
stdio. Every tool is read-only and deterministic — answers come from the lake
and the chain, not from the model's imagination.

- market data: `get_base_market_data` · `get_onchain_price` ·
  `query_market_data` (bounded DuckDB SQL)
- discovery: `search_symbols` · `list_symbols` · `resolve_symbols` ·
  `inventory_snapshot` · `data_coverage` · `catalog_summary` · `catalog_stats` ·
  `list_data_channels` · `list_dates` · `list_exchanges_on_disk` ·
  `list_registered_exchanges`
- analytics: OFI, slippage, whale alerts, IV surface / term structure /
  vol skew / risk reversal, funding APR + prediction, spot–perp and
  spot–future basis, open interest, liquidity depth, sequencer latency,
  peg deviation, lending stress, MEV sandwich detection, smart-money labels

Works with Claude, Cursor, or anything else that speaks MCP.

## REST API

`crypcodile api` serves the same lake over FastAPI (`/api/v1/*`), with a few
payment-gated demo routes:

| Group | Paths |
|---|---|
| Ops | `/health` `/status` `/version` `/exchanges` |
| Catalog | `/catalog/channels` `/catalog/search` `/catalog/inventory` `/catalog/scan` `/data-coverage` `/resolve-symbols` |
| Query | `POST /query` (bounded read-only SQL) |
| Derivatives | `/open-interest` `/funding-apr` `/funding-predict` `/basis` `/perp-basis` `/spot-future-basis` |
| Microstructure | `/indicators` `/ofi` `/whale-alerts` `/slippage` `POST /simulate-price-impact` |
| Options | `/iv-surface` `/term-structure` `/vol-skew` `/risk-reversal` |
| L2 / DeFi risk | `/liquidity-depth` `/sequencer-latency` `/chaos-score` `/peg-deviation` `/lending-stress` |
| Offline analytics | `POST /gas-vol` `/mev-sandwich` `/smart-money` `/label-transfers` |
| Gated demo | `GET /market-data` + `POST /simulate-payment` (x402) |

## Base L2

`BaseOnchainConnector` reads Uniswap V3 and Aerodrome swap/reserve events from
Base RPC logs and emits the same record types as the CEX connectors, so
cross-venue queries are one SQL statement instead of two codebases. Start
with [docs/base_quickstart.md](docs/base_quickstart.md); there is a Streamlit
dashboard and a Farcaster frame server under [`examples/`](examples/).

## Tests

```bash
uv sync
pytest tests/ -v
```

1,764 test functions across 136 files, including a local mock RPC server for
degraded-network E2E runs (`tests/e2e/`), adversarial payload suites, and a
regression file fed by real exchange API anomalies (`test_empirical_bugs.py`).
`mypy --strict` and Ruff run on `src/`. CI-friendly: Qt and Matplotlib are
forced headless, and BLAS thread caps keep Apple Silicon imports fast.

## What it is not

- Not a trading bot. There is no order-execution path, on purpose.
- Not a hosted service. Everything runs on your machine, against your lake.
- FlowMap is a desktop app; it needs a display (the data pipeline doesn't).
- Options analytics need options data — point `iv-surface` at a lake with
  Deribit snapshots in it.

## Media

Slide decks (16:9 and 9:16) with real screenshots live in
[docs/media/promo/](docs/media/promo/) — use them for talks or posts.

## Contributing

PRs welcome. Read `CHANGELOG.md` for recent direction and make sure the E2E
and adversarial suites pass before opening one.

Apache-2.0 — see [LICENSE](LICENSE).
