# Crocodile

> Open-source crypto market-data engine — ingest, normalize, store, and retrieve
> financial data **anywhere, at any resolution**. Aiming for data coverage as good as
> Laevitas, Amberdata, and Tardis.dev.

**Status:** core complete (M5). See the design spec:
[`docs/superpowers/specs/2026-06-04-crocodile-core-design.md`](docs/superpowers/specs/2026-06-04-crocodile-core-design.md).

## What it does (core, v1)

- **Ingest** live (WebSocket) + historical (REST) data from many crypto exchanges
  (Deribit, Binance spot/USD-M, Bybit, OKX, Coinbase).
- **Normalize** everything to one canonical schema (trades, L2 order book, tickers,
  derivative tickers, options chains, funding, open interest, liquidations, OHLCV).
- **Store** as hive-partitioned Parquet (zstd-5) with a DuckDB SQL layer.
- **Retrieve anywhere**: Python client + CLI, time-ordered replay, SQL query, and
  multi-format export (Parquet / CSV / Arrow / JSON / JSONL).
- **Resample on demand**: OHLCV bars at any interval (1s to 1w), periodic book
  snapshots, and VWAP / dollar-volume metrics — all from stored raw ticks.

## Roadmap

- **Core** (this spec — done): ingestion → normalization → storage → client/export/replay/resample.
- **Analytics** (next): IV surface, greeks, skew, term structure, basis, funding APR.
- **Server API** (next): self-hosted REST + WebSocket.
- **Dashboard** (later): visual exploration.

## Stack

Python 3.12+ · asyncio · msgspec · Polars · PyArrow · DuckDB · websockets/aiohttp ·
Typer · Apache-2.0.

---

## Quickstart

### 1. Install

```bash
# Clone and enter the repo
git clone https://github.com/your-org/crocodile.git
cd crocodile

# Install all dependencies (Python 3.12 pinned)
uv sync

# Verify the CLI is available
uv run crocodile --help
```

### 2. Collect live data

Stream BTC-PERPETUAL trades + book deltas from Deribit and write to the local
data lake under `./data/`:

```bash
# Run until Ctrl-C
uv run python examples/collect_deribit.py
```

Or use the CLI:

```bash
uv run crocodile collect \
  --exchange deribit \
  --symbols BTC-PERPETUAL \
  --channels trade book_delta derivative_ticker \
  --data-dir data
```

The data lake is hive-partitioned Parquet:

```
data/
  exchange=deribit/
    channel=trade/
      date=2024-01-15/
        bucket=42/
          part-<uuid>.parquet
    channel=book_delta/
      ...
```

### 3. Query with DuckDB SQL

After collecting data, run arbitrary SQL against the lake:

```bash
# Count trades collected
uv run crocodile query "SELECT count(*) FROM trade" --data-dir data

# Top 5 most-traded symbols by volume
uv run crocodile query \
  "SELECT symbol, sum(amount) AS volume FROM trade GROUP BY symbol ORDER BY volume DESC LIMIT 5" \
  --data-dir data
```

Or from Python:

```python
from crocodile.client.client import CrocodileClient

client = CrocodileClient(data_dir="data")
df = client.query("SELECT count(*) FROM trade")
print(df)
```

### 4. Replay records in time order

The replay engine k-way-merges stored partitions by `local_ts` across channels
and symbols without loading everything into memory at once.

```bash
# Print the first 20 BTC-PERPETUAL trade records
uv run crocodile replay \
  --channels trade \
  --symbols deribit:BTC-PERPETUAL \
  --from 0 --to 9223372036854775807 \
  --limit 20 \
  --data-dir data
```

From Python:

```python
from crocodile.client.client import CrocodileClient

client = CrocodileClient(data_dir="data")
for record in client.replay(
    channels=["trade"],
    symbols=["deribit:BTC-PERPETUAL"],
    frm=0,
    to=9_223_372_036_854_775_807,
):
    print(record)
```

### 5. Export to a file

Export a time range to CSV, Parquet, Arrow IPC, JSON, or JSONL:

```bash
uv run crocodile export \
  --channel trade \
  --symbols deribit:BTC-PERPETUAL \
  --from 0 --to 9223372036854775807 \
  --fmt csv --dest trades.csv \
  --data-dir data
```

The convenience script does the same thing:

```bash
uv run python examples/replay_to_csv.py \
  --symbols deribit:BTC-PERPETUAL \
  --out trades.csv \
  --data-dir data
```

From Python:

```python
client.export(
    channel="trade",
    symbols=["deribit:BTC-PERPETUAL"],
    frm=0,
    to=9_223_372_036_854_775_807,
    fmt="csv",
    dest="trades.csv",
)
```

### 6. Resample OHLCV bars

Resample stored trade records into OHLCV bars at any interval — no exchange
calls, all from the DuckDB catalog:

```bash
# Print 1-minute bars for all stored history
uv run python examples/query_ohlcv.py \
  --symbol deribit:BTC-PERPETUAL \
  --interval 1m \
  --data-dir data
```

From Python:

```python
from crocodile.store.catalog import Catalog
from crocodile.resample.ohlcv import resample_ohlcv
from crocodile.resample.metrics import resample_metrics

catalog = Catalog("data")

# OHLCV bars
ohlcv = resample_ohlcv(catalog, "deribit:BTC-PERPETUAL", 0, 9_223_372_036_854_775_807, "1m")
print(ohlcv)

# VWAP + dollar volume per 5-minute bucket
metrics = resample_metrics(catalog, "deribit:BTC-PERPETUAL", 0, 9_223_372_036_854_775_807, "5m")
print(metrics)
```

Supported interval strings: `1s`, `5s`, `30s`, `1m`, `5m`, `15m`, `30m`,
`1h`, `4h`, `1d`, `1w`.

### 7. Reconstruct the order book

Use the M2 book reconstruction engine to replay book snapshots at fixed
intervals:

```python
from crocodile.client.client import CrocodileClient
from crocodile.resample.book import resample_book_snapshots
from crocodile.schema.records import BookSnapshot, BookDelta

client = CrocodileClient(data_dir="data")
book_records = list(client.replay(
    channels=["book_snapshot", "book_delta"],
    symbols=["deribit:BTC-PERPETUAL"],
    frm=0,
    to=9_223_372_036_854_775_807,
))

# Emit a book snapshot every 1 second
for snap in resample_book_snapshots(
    [r for r in book_records if isinstance(r, (BookSnapshot, BookDelta))],
    interval_ns=1_000_000_000,
    top_n=5,
):
    print(snap)
```

---

## Supported exchanges and channels

| Exchange | Venue key | Trades | L2 book | Tickers | Funding | OI | Liq | Options |
|---|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| Deribit | `deribit` | Y | Y | Y | Y | Y | Y | Y |
| Binance spot | `binance-spot` | Y | Y | Y | — | — | — | — |
| Binance USD-M | `binance-usdm` | Y | Y | Y | Y | Y | Y | — |
| Bybit | `bybit` | Y | Y | Y | Y | Y | Y | — |
| OKX | `okx` | Y | Y | Y | Y | Y | Y | — |
| Coinbase | `coinbase` | Y | Y | Y | — | — | — | — |

---

## Development

```bash
# Install all dependencies (including dev extras) into a local .venv
uv sync

# Run the test suite
uv run pytest

# Lint (ruff)
uv run ruff check .

# Type-check (mypy)
uv run mypy

# Coverage report
uv run pytest --cov=crocodile --cov-report=term-missing
```

---

## License

Apache-2.0 — see [LICENSE](LICENSE).
