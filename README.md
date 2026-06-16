# Crypcodile

**Deterministic Market Data Infrastructure for Quantitative Research and Autonomous Agents**

[![PyPI version](https://img.shields.io/badge/pypi-v0.1.011-blue.svg)](https://pypi.org/project/crypcodile/)
[![Python Supported](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Base Ecosystem](https://img.shields.io/badge/ecosystem-Base_L2-0052FF.svg)](https://base.org)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

## Overview

**Crypcodile** is an open-source, high-performance market data engineering framework. It provides a deterministic pipeline for ingesting, normalizing, resampling, and storing fragmented cryptocurrency market data across both centralized limit order books (CLOBs) and decentralized automated market makers (AMMs).

Designed for quantitative researchers, algorithmic traders, and autonomous AI agents, Crypcodile abstracts the underlying complexities of RPC node management, WebSocket state reconciliation, and schema fragmentation into a unified, mathematically rigorous interface.

---

## 1. System Architecture

Crypcodile is built around a robust, modular pipeline designed to handle degraded network states, dropped payloads, and adversarial data anomalies.

*   **Ingest & Transport (`src/crypcodile/ingest/`):** Multiplexed connections handling both REST polling and WebSocket streaming. Features automatic gap-bridging (`gap_bridge.py`) and dead-letter queuing (`deadletter.py`) to ensure zero data loss during network partitions.
*   **Normalization Layer (`src/crypcodile/schema/`):** Strict schema enforcement mapping diverse exchange APIs to standardized internal records (L2/L3 order books, aggregated trades, funding rates, and option ticker states).
*   **Resampling Engine (`src/crypcodile/resample/`):** Real-time time-series alignment and aggregation (e.g., tick-to-OHLCV, order book snapshots) with precision interval handling.
*   **Storage & Sinks (`src/crypcodile/store/`):** In-memory caching and highly optimized Parquet serialization (`parquet_sink.py`) integrated with a localized catalog system for historical backtesting data lakes.

## 2. Base Ecosystem & L2 Integration

With the `v0.1.011` release, Crypcodile serves as critical public-goods infrastructure for the **Base L2 network**. 

Historically, comparing CeFi order book states with DeFi on-chain states required maintaining disparate codebases. Crypcodile unifies this paradigm. The `BaseOnchainConnector` directly queries Base RPC nodes, extracts DEX swap and liquidity events (e.g., from Aerodrome Finance), and normalizes them into the exact standard record formats used for traditional exchanges like Coinbase or Binance.

This unified approach enables quantitative developers to seamlessly execute cross-venue arbitrage detection, on-chain momentum tracking, and aggregated volume analytics—treating smart contract state identically to centralized exchange APIs.

## 3. Agentic Workflows (Model Context Protocol)

As autonomous systems increasingly participate in on-chain ecosystems, standardizing how Large Language Models interact with market data is critical. 

Crypcodile pioneers this integration via its natively embedded **Model Context Protocol (MCP) Server** (`src/crypcodile/mcp_server.py`). This allows AI agents (via environments like Cursor, Claude Desktop, or custom agentic frameworks) to execute deterministic, read-only queries against live market data, evaluate option implied volatility surfaces, and fetch Base on-chain metrics without writing custom RPC extraction logic or hallucinating pricing data.

```bash
# Initialize the MCP server for agentic consumption
uv run crypcodile mcp --data-dir data
```

## Analytics

Beyond data routing, Crypcodile ships with `crypcodile.analytics`, providing optimized implementations for derivatives research and CLI commands like `funding_apr` and `iv_surface`:

*   **Black-Scholes Engine (`blackscholes.py`):** Low-latency European options pricing and Greeks calculation.
*   **Volatility Surfaces (`volsurface.py`):** Multi-dimensional implied volatility (IV) surface generation and interpolation across maturities (exposed via `iv_surface` command).
*   **Perpetual Basis (`funding.py`, `basis.py`):** Real-time funding rate aggregation (exposed via `funding_apr` command) and spot/perp basis analytics.

## 5. Installation & Quick Start

Crypcodile requires Python 3.12+. We recommend using `uv` for high-performance dependency management.

### Quick Script Installation (Recommended)

You can install the Crypcodile CLI globally inside an isolated virtual environment with a single copy-pasteable command:

**macOS and Linux:**
```bash
curl -sSL https://raw.githubusercontent.com/nazmiefearmutcu/Crypcodile/main/install.sh | bash
```

**Windows:**
```powershell
powershell -ExecutionPolicy Bypass -Command "iwr -useb https://raw.githubusercontent.com/nazmiefearmutcu/Crypcodile/main/install.ps1 | iex"
```

*Note: The installer automatically creates an isolated virtual environment, installs all dependencies, and configures the `crypcodile` command in your PATH.*

### Standard Package Installation

```bash
pip install crypcodile
# or via uv
uv pip install crypcodile
```

### Example: Normalizing Base On-Chain Data

The following demonstrates instantiating a client to extract and query normalized trade events from the Base ecosystem, outputting directly to a Pandas DataFrame for downstream statistical analysis.

```python
import pandas as pd
from crypcodile.client.client import CrypcodileClient

def analyze_base_liquidity():
    # Instantiate the client pointing to your local Parquet data lake
    client = CrypcodileClient(data_dir="data")
    
    # Query the latest 500 normalized trade events from Base on-chain DEXs
    df = client.query(
        "SELECT * FROM trade WHERE exchange = 'base_onchain' ORDER BY local_ts DESC LIMIT 500"
    )
    
    # Export to Pandas DataFrame for downstream quant modeling
    df_pandas = df.to_pandas()
    print(df_pandas.describe())

if __name__ == "__main__":
    analyze_base_liquidity()
```

*(Additional production-ready implementation architectures, including Farcaster Frame backends and interactive Dashboards, are available in the `examples/` directory).*

## 6. Testing & Reliability Guarantee

Financial data infrastructure fails at the edge cases. Crypcodile is hardened by a rigorous, multi-tiered test suite exceeding standard unit testing:

*   **End-to-End RPC Mocking (`tests/e2e/`):** Localized `mock_rpc_server.py` simulates degraded API/RPC states, rate limits, and latency spikes to verify pipeline resilience and state recovery.
*   **Adversarial & Stress Testing:** The `base_onchain` connectors are validated against malicious payloads, extreme data types (`test_adversarial.py`), and sustained throughput maximums (`test_stress_challenger.py`).
*   **Empirical Bug Verification:** Historical edge-cases and exchange API anomalies are logged and tested against continuously (`test_empirical_bugs.py`) to prevent regressions.

To execute the test suite locally:

```bash
uv sync
pytest tests/ -v
```

## 7. Contributing

We welcome pull requests from the quantitative research and Ethereum L2 developer communities. Please review `CHALLENGE_REPORT.md` for context on current architectural trade-offs and `CHANGELOG.md` for recent version iterations. Ensure all adversarial and E2E test suites pass prior to submission.

## 8. License

Crypcodile is distributed under the [Apache-2.0 License](LICENSE).
