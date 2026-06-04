# 🐊 Crocodile

> Open-source crypto market-data engine — ingest, normalize, store, and retrieve
> financial data **anywhere, at any resolution**. Aiming for data coverage as good as
> Laevitas, Amberdata, and Tardis.dev.

**Status:** early development (core in progress). See the design spec:
[`docs/superpowers/specs/2026-06-04-crocodile-core-design.md`](docs/superpowers/specs/2026-06-04-crocodile-core-design.md).

## What it does (core, v1)

- **Ingest** live (WebSocket) + historical (REST) data from many crypto exchanges.
- **Normalize** everything to one canonical schema (trades, L2 order book, tickers,
  derivative tickers, options chains, funding, open interest, liquidations, OHLCV).
- **Store** as hive-partitioned Parquet (zstd) with a DuckDB SQL layer.
- **Retrieve anywhere**: Python client + CLI, time-ordered replay, SQL query, and
  multi-format export (Parquet / CSV / Arrow / JSON / JSONL) — at any resolution
  (raw ticks stored; OHLCV/snapshots/VWAP resampled on demand).

## Roadmap

- **Core** (this spec): ingestion → normalization → storage → client/export/replay.
- **Analytics** (next): IV surface, greeks, skew, term structure, basis, funding APR.
- **Server API** (next): self-hosted REST + WebSocket.
- **Dashboard** (later): visual exploration.

## Stack

Python 3.12+ · asyncio · msgspec · Polars · PyArrow · DuckDB · websockets/aiohttp ·
Typer · Apache-2.0.

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
```

## License

Apache-2.0 — see [LICENSE](LICENSE).
