# Crypcodile

**Deterministic Market Data Infrastructure for Quantitative Research and Autonomous Agents**

[![PyPI version](https://img.shields.io/badge/pypi-v0.1.044-blue.svg)](https://pypi.org/project/crypcodile/)
[![Python Supported](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Base Ecosystem](https://img.shields.io/badge/ecosystem-Base_L2-0052FF.svg)](https://base.org)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

### 0.1.044 continuous-dev highlights

- **Multi-exchange collect** — one CLI collect across venues
- **Search** — ranked symbol search + catalog coverage (CLI/MCP)
- **Backfill** — historical REST backfill orchestration
- **REST catalog / query / OI** — lake catalog, bounded SQL query, open-interest surfaces
- **Superchain / Derive** — factory-registered connectors in exchange lists
- **MCP analytics pack** — slippage, OFI, whale alerts, IV/basis/vol-skew, liquidity-depth, and related tools

---

## 🔵 Built for the Base Ecosystem & AI Agents

Crypcodile serves as critical public-goods infrastructure for the **Base L2 network** and the **AI/Agentic Web3 economy**. It decouples RPC node data polling and WebSocket streams from schema standardization, allowing quant researchers and autonomous AI systems to interact with Base DEXs (Uniswap V3 and Aerodrome Finance) and centralized limit order books (CLOBs) in a single, mathematically rigorous framework.

### 🐊 Core Base & AI Capabilities:
1. **🤖 Model Context Protocol (MCP) Server**: Natively hosts an MCP server, allowing AI agents (via environments like Cursor, Claude Desktop, or custom agentic frameworks) to execute deterministic, read-only queries against live Base market data without price hallucinations.
2. **⚡ Native Base On-chain Ingest**: Standardizes Base RPC node event logs for DEX swaps and reserves (Uniswap V3 & Aerodrome Finance) directly to unified time-series schemas.
3. **💬 Farcaster Frame Integration**: Provides a fully functional Farcaster Frame server (`examples/farcaster_frame.py`) yielding real-time Base market snapshots on Warpcast.
4. **🛡️ Swarm-Hardened Core**: Built and secured by an autonomous multi-agent AI swarm (Auditors, Challengers, Implementers, Reviewers).

---

### 🤖 AI Agentic Query Example (MCP Tool)

**AI Agent Prompt:**
> "What is the current slippage and whale positioning on Aerodrome for DEGEN/WETH?"

**Crypcodile MCP response:**
```json
{
  "symbol": "DEGEN-WETH",
  "pool_address": "0xc9034c3e7f40f1cdd08ce8339178ef8a5fc2f71e",
  "current_price": "0.00000284 WETH",
  "volume_1h_quote": "$124,532.18",
  "reserve0": "3,485,210.50 DEGEN",
  "reserve1": "9.89 WETH",
  "block": 15432901,
  "num_swaps_1h": 142,
  "whale_sentiment": "Bullish (Net Flow: +$42,500)",
  "estimated_slippage_10_eth": "0.18%"
}
```

---

## 1. System Architecture

Crypcodile is built around a robust, modular pipeline designed to handle degraded network states, dropped payloads, and adversarial data anomalies.

*   **Ingest & Transport (`src/crypcodile/ingest/`):** Multiplexed connections handling both REST polling and WebSocket streaming. Features automatic gap-bridging (`gap_bridge.py`) and dead-letter queuing (`deadletter.py`) to ensure zero data loss during network partitions.
*   **Normalization Layer (`src/crypcodile/schema/`):** Strict schema enforcement mapping diverse exchange APIs to standardized internal records (L2/L3 order books, aggregated trades, funding rates, and option ticker states).
*   **Resampling Engine (`src/crypcodile/resample/`):** Real-time time-series alignment and aggregation (e.g., tick-to-OHLCV, order book snapshots) with precision interval handling.
*   **Storage & Sinks (`src/crypcodile/store/`):** In-memory caching and highly optimized Parquet serialization (`parquet_sink.py`) integrated with a localized catalog system for historical backtesting data lakes.

## 2. Base Ecosystem & L2 Integration

With the `v0.1.043` release, Crypcodile unifies the comparison of CeFi order book states with DeFi on-chain states. Historically, doing so required maintaining disparate codebases. The `BaseOnchainConnector` queries Base RPC nodes, extracts DEX swap and liquidity events (Uniswap V3 and Aerodrome Finance), and normalizes them into the exact standard record formats used for traditional exchanges like Coinbase or Binance.

This unified approach enables quantitative developers to seamlessly execute cross-venue arbitrage detection, on-chain momentum tracking, and aggregated volume analytics—treating smart contract state identically to centralized exchange APIs.

## 3. Agentic Workflows (Model Context Protocol)

As autonomous systems increasingly participate in on-chain ecosystems, standardizing how Large Language Models interact with market data is critical. 

Crypcodile pioneers this integration via its natively embedded **Model Context Protocol (MCP) Server** (`src/crypcodile/mcp_server.py`). This allows AI agents to execute deterministic, read-only queries against live market data, evaluate option implied volatility surfaces, and fetch Base on-chain metrics without writing custom RPC extraction logic.

```bash
# Initialize the MCP server for agentic consumption
uv run crypcodile mcp --data-dir data
```

## Search / Discovery

Find symbols, list catalog coverage, and expose the same discovery surface to agents via MCP (`search_symbols`, `list_data_channels`, `data_coverage`):

```bash
crypcodile search "btc" --channel trade
crypcodile catalog --symbols
crypcodile mcp  # tools: search_symbols, list_data_channels, data_coverage
```

## 4. Analytics

Beyond data routing, Crypcodile ships with `crypcodile.analytics`, providing optimized implementations for derivatives research and CLI commands like `funding_apr` and `iv_surface`:

*   **Black-Scholes Engine (`blackscholes.py`):** Low-latency European options pricing and Greeks calculation.
*   **Volatility Surfaces (`volsurface.py`):** Multi-dimensional implied volatility (IV) surface generation and interpolation across maturities (exposed via `iv_surface` command).
*   **Perpetual Basis (`funding.py`, `basis.py`):** Real-time funding rate aggregation (exposed via `funding_apr` command) and spot/perp basis analytics.

## 5. Bookmap Visualizer (Order Book Depth & Cumulative Delta)

Crypcodile features a high-performance, macOS-optimized native desktop GUI visualizer built with `PyQt6` and `pyqtgraph`. It allows quantitative researchers to visually analyze market microstructures and order book dynamics in real-time.

Launch the visualizer by specifying the symbol and the amount of historical catalog data to load:
```bash
# Launch directly from the command line
crypcodile bookmap --symbol deribit:BTC-PERPETUAL --historical-hours 2.0

# Or inside the interactive shell
crypcodile shell
crypcodile> bookmap --symbol deribit:BTC-PERPETUAL --historical-hours 1.0
```

## 6. Installation & Quick Start

Crypcodile requires Python 3.12+. We recommend using `uv` for high-performance dependency management.

### Quick Script Installation

**macOS and Linux:**
```bash
curl -sSL https://raw.githubusercontent.com/nazmiefearmutcu/Crypcodile/main/install.sh | bash
```

**Windows:**
```powershell
powershell -ExecutionPolicy Bypass -Command "iwr -useb https://raw.githubusercontent.com/nazmiefearmutcu/Crypcodile/main/install.ps1 | iex"
```

### Standard Package Installation

```bash
pip install crypcodile
# or via uv
uv pip install crypcodile
```

### 🔵 Base Developer Quickstart
To get started tracking Base Whales in less than 10 lines of Python, see our [Base Developer Quickstart](docs/base_quickstart.md).

---

## 🚀 1-Click Deployments (Live Public Demos)

To make it easy for grant reviewers and developers to inspect Crypcodile's features live, we support instant cloud deployments of our dashboard and API portal:

### 1. Streamlit Live DEX Dashboard
Visualize live swap volumes, liquidity depth, and price feeds streaming directly from Aerodrome and Uniswap V3 on Base.
- **Deploy instructions**: Import the project into [Streamlit Community Cloud](https://streamlit.io/cloud) and set the entry file to [examples/base_dashboard.py](examples/base_dashboard.py).

### 2. Node.js Micropayment-Gated API Portal
Inspect our micropayment architecture for on-chain services.
- **Vercel**: The `api_portal` contains a pre-configured [vercel.json](src/crypcodile/api_portal/vercel.json) file. Link the repo, select the `src/crypcodile/api_portal/` folder as the root directory, and deploy in one click.

### REST API endpoint matrix (`/api/v1/*`)

Python FastAPI surface in `src/crypcodile/api_server.py` (local lake + free probes; some routes are payment-gated demos):

| Group | Methods / paths | Notes |
|-------|-----------------|-------|
| **Ops / meta** | `GET /health`, `/status`, `/version`, `/exchanges` | Free; no payment. `/version` → `{version}` only |
| **Catalog / discovery** | `GET /catalog/channels`, `/catalog/search`, `/catalog/inventory`, `/catalog/scan`, `/data-coverage`, `/resolve-symbols` | Lake inventory & symbol resolve |
| **Market data (gated)** | `GET /market-data`; `POST /simulate-payment` | x402 demo gating |
| **Query** | `POST /query` | Bounded read-only SQL against the lake |
| **Derivatives / OI** | `GET /open-interest`, `/funding-apr`, `/funding-predict`, `/basis`, `/perp-basis`, `/spot-future-basis` | Funding & basis surfaces; `/funding-predict` is pure offline (comma-separated `rates`) |
| **Microstructure** | `GET /indicators`, `/ofi`, `/whale-alerts`, `/slippage`; `POST /simulate-price-impact` | Indicators & flow |
| **Offline analytics** | `POST /gas-vol`, `/mev-sandwich`, `/smart-money` | Pure JSON body; no lake / no payment |
| **Options** | `GET /iv-surface`, `/term-structure`, `/vol-skew`, `/risk-reversal` | IV / skew analytics |
| **Base / risk** | `GET /liquidity-depth`, `/sequencer-latency`, `/chaos-score`, `/peg-deviation`, `/lending-stress` | L2 & DeFi risk |

Paths above are relative to `/api/v1`. OpenAPI-style detail is available via the running API server.

---

## 7. Testing & Reliability Guarantee

Financial data infrastructure fails at the edge cases. Crypcodile is hardened by a rigorous, multi-tiered test suite exceeding standard unit testing:

*   **End-to-End RPC Mocking (`tests/e2e/`):** Localized `mock_rpc_server.py` simulates degraded API/RPC states, rate limits, and latency spikes to verify pipeline resilience and state recovery.
*   **Adversarial & Stress Testing:** The `base_onchain` connectors are validated against malicious payloads, extreme data types (`test_adversarial.py`), and sustained throughput maximums (`test_stress_challenger.py`).
*   **Empirical Bug Verification:** Historical edge-cases and exchange API anomalies are logged and tested against continuously (`test_empirical_bugs.py`) to prevent regressions.

To execute the test suite locally:

```bash
uv sync
pytest tests/ -v
```

### 🍏 macOS Apple Silicon & Headless Runs Optimization
To ensure reliable operation on modern macOS Apple Silicon devices and inside headless CI/CD environments, Crypcodile includes several automated platform optimizations:
* **Headless Matplotlib & PyQt6 Mocking**: Visual Qt rendering tests are automatically bypassed inside headless environments. Additionally, Matplotlib is programmatically forced to the non-interactive `Agg` backend globally in [tests/conftest.py](file:///Users/nazmi/Desktop/Crypcodile/tests/conftest.py) to prevent connection hangs with the macOS window manager.
* **Apple Silicon OpenMP/OpenBLAS Thread Limits**: Programmatic environmental limit overrides (`OMP_NUM_THREADS=1`, `OPENBLAS_NUM_THREADS=1`, etc.) are configured at test start to bypass Apple Silicon process thread-group deadlocks, dropping library load times from 80+ seconds to under 1.5 seconds.
* **Robust Stdio MCP Transport**: The Model Context Protocol (MCP) Server utilizes a dedicated background executor thread pool to monitor `sys.stdin` for EOF/client closure, ensuring the server terminates cleanly on parent shell termination without socket leaks.


## 8. Contributing

We welcome pull requests from the quantitative research and Ethereum L2 developer communities. Please review `CHALLENGE_REPORT.md` for context on current architectural trade-offs and `CHANGELOG.md` for recent version iterations. Ensure all adversarial and E2E test suites pass prior to submission.

## 9. License

Crypcodile is distributed under the [Apache-2.0 License](LICENSE).
