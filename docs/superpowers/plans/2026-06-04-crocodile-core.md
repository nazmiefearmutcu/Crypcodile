# Crocodile Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Authoritative references (read before each task):**
> - Design: `docs/superpowers/specs/2026-06-04-crocodile-core-design.md`
> - **Technical appendix (field mappings, sync algorithms, gotchas):** `docs/superpowers/specs/2026-06-04-crocodile-core-research-appendix.md` — when a low-level detail is ambiguous, the appendix is authoritative.

**Goal:** Build a Python-first engine that ingests live + historical crypto market data from multiple exchanges, normalizes it to one canonical schema, stores it as hive-partitioned Parquet queryable via DuckDB, and serves it anywhere via a client/CLI with time-ordered replay and multi-format export.

**Architecture:** Async (asyncio) connectors per exchange-venue emit canonical `Record`s (msgspec Structs) into a `Sink`. A Parquet sink writes hive-partitioned files; a DuckDB catalog queries them. A replay engine k-way-merges sorted partitions by `local_ts` and reconstructs order books from snapshot+deltas. A `CrocodileClient` + Typer CLI expose stream/replay/query/export. Connectors are isolated behind a `Connector` ABC with supervised reconnect, gap-detection, and REST backfill.

**Tech Stack:** Python ≥3.12 · asyncio · msgspec · Polars · PyArrow · DuckDB · websockets · aiohttp · Typer · Rich. Dev: pytest · pytest-asyncio · ruff · mypy.

---

## Milestone Map (Ralph drives these gates in order)

| Gate | Tasks | Definition of done |
|---|---|---|
| **Phase 0** | T0.1–T0.4 | Repo installs, lints, types, tests run green (empty). |
| **M1** | T1.1–T1.12 | Canonical schema + InstrumentRegistry + Connector ABC + Deribit & Binance connectors live-normalizing trade/book/book_ticker/derivative_ticker (+Deribit options_chain); golden tests pass; ruff+mypy clean. |
| **M2** | T2.1–T2.6 | Parquet sink (hive-partition, buffered) + DuckDB catalog + replay k-way merge + order-book reconstruction; round-trip + ordering + reconstruction tests pass. |
| **M3** | T3.1–T3.5 | `CrocodileClient` + Typer CLI: `collect`/`replay`/`export`/`query`/`catalog`; multi-format export verified. |
| **M4** | T4.1–T4.6 | Backfill + gap-detect wired end-to-end; Bybit/OKX/Coinbase connectors; connector coverage ≥90%. |
| **M5** | T5.1–T5.5 | `resample` (OHLCV/snapshots/VWAP at any interval) + full derivative/options completeness; README quickstart + runnable examples. |

**Commit discipline:** one commit per task (or per TDD red→green cycle). Never use a Claude/AI co-author trailer (repo policy). Commit messages: Conventional Commits (`feat:`, `test:`, `fix:`, `chore:`, `docs:`).

---

## Canonical Schema Reference (locked — used by every task)

`src/crocodile/schema/enums.py`:
```python
from enum import Enum

class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"
    UNKNOWN = "unknown"

class OptType(str, Enum):
    CALL = "C"
    PUT = "P"

class Channel(str, Enum):
    TRADE = "trade"
    BOOK_SNAPSHOT = "book_snapshot"
    BOOK_DELTA = "book_delta"
    BOOK_TICKER = "book_ticker"
    DERIVATIVE_TICKER = "derivative_ticker"
    OPTIONS_CHAIN = "options_chain"
    FUNDING = "funding"
    OPEN_INTEREST = "open_interest"
    LIQUIDATION = "liquidation"
    OHLCV = "ohlcv"
```

`src/crocodile/schema/records.py` (msgspec Structs, `frozen=True`; `Level = tuple[float, float]` = (price, amount)):
```python
import msgspec
from crocodile.schema.enums import Side, OptType

Level = tuple[float, float]  # (price, amount); amount == 0.0 means REMOVE this level

class Trade(msgspec.Struct, frozen=True, tag="trade", tag_field="channel"):
    exchange: str; symbol: str; symbol_raw: str
    exchange_ts: int | None; local_ts: int
    id: str; price: float; amount: float; side: Side
    liquidation: str | None = None  # Deribit enum "M"/"T"/"MT" when present

class BookSnapshot(msgspec.Struct, frozen=True, tag="book_snapshot", tag_field="channel"):
    exchange: str; symbol: str; symbol_raw: str
    exchange_ts: int | None; local_ts: int
    bids: list[Level]; asks: list[Level]
    depth: int; sequence_id: int | None = None; is_snapshot: bool = True

class BookDelta(msgspec.Struct, frozen=True, tag="book_delta", tag_field="channel"):
    exchange: str; symbol: str; symbol_raw: str
    exchange_ts: int | None; local_ts: int
    bids: list[Level]; asks: list[Level]
    seq_id: int | None = None; prev_seq_id: int | None = None; is_snapshot: bool = False

class BookTicker(msgspec.Struct, frozen=True, tag="book_ticker", tag_field="channel"):
    exchange: str; symbol: str; symbol_raw: str
    exchange_ts: int | None; local_ts: int
    bid_px: float; bid_sz: float; ask_px: float; ask_sz: float
    update_id: int | None = None

class DerivativeTicker(msgspec.Struct, frozen=True, tag="derivative_ticker", tag_field="channel"):
    exchange: str; symbol: str; symbol_raw: str
    exchange_ts: int | None; local_ts: int
    last_price: float | None = None; mark_price: float | None = None
    index_price: float | None = None; funding_rate: float | None = None
    predicted_funding_rate: float | None = None; funding_timestamp: int | None = None
    open_interest: float | None = None

class OptionsChain(msgspec.Struct, frozen=True, tag="options_chain", tag_field="channel"):
    exchange: str; symbol: str; symbol_raw: str
    exchange_ts: int | None; local_ts: int
    underlying: str; underlying_price: float | None
    strike: float; expiry: int; opt_type: OptType
    mark_price: float | None = None; mark_iv: float | None = None
    bid_px: float | None = None; bid_sz: float | None = None; bid_iv: float | None = None
    ask_px: float | None = None; ask_sz: float | None = None; ask_iv: float | None = None
    last_price: float | None = None; open_interest: float | None = None
    delta: float | None = None; gamma: float | None = None
    vega: float | None = None; theta: float | None = None; rho: float | None = None

class Funding(msgspec.Struct, frozen=True, tag="funding", tag_field="channel"):
    exchange: str; symbol: str; symbol_raw: str
    exchange_ts: int | None; local_ts: int
    funding_rate: float; funding_timestamp: int | None = None
    predicted_funding_rate: float | None = None; interval_hours: int | None = None

class OpenInterest(msgspec.Struct, frozen=True, tag="open_interest", tag_field="channel"):
    exchange: str; symbol: str; symbol_raw: str
    exchange_ts: int | None; local_ts: int
    open_interest: float; open_interest_value: float | None = None

class Liquidation(msgspec.Struct, frozen=True, tag="liquidation", tag_field="channel"):
    exchange: str; symbol: str; symbol_raw: str
    exchange_ts: int | None; local_ts: int
    price: float; amount: float; side: Side; id: str | None = None

class OHLCV(msgspec.Struct, frozen=True, tag="ohlcv", tag_field="channel"):
    exchange: str; symbol: str; symbol_raw: str
    exchange_ts: int | None; local_ts: int
    interval: str; open: float; high: float; low: float; close: float
    volume: float; buy_volume: float = 0.0; sell_volume: float = 0.0
    num_trades: int | None = None

Record = (
    Trade | BookSnapshot | BookDelta | BookTicker | DerivativeTicker
    | OptionsChain | Funding | OpenInterest | Liquidation | OHLCV
)
```

> Note: `tag_field="channel"` makes every record carry a `channel` discriminator when encoded by msgspec — this is the partition key for storage and the routing key for the catalog.

---

## Phase 0 — Project Setup

### Task 0.1: pyproject + package skeleton

**Files:**
- Create: `pyproject.toml`
- Create: `src/crocodile/__init__.py`
- Create: `src/crocodile/py.typed`

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[project]
name = "crocodile"
version = "0.0.1"
description = "Open-source crypto market-data engine: ingest, normalize, store, retrieve anywhere."
readme = "README.md"
requires-python = ">=3.12"
license = { text = "Apache-2.0" }
dependencies = [
    "msgspec>=0.18",
    "polars>=1.0",
    "pyarrow>=16",
    "duckdb>=1.0",
    "websockets>=12",
    "aiohttp>=3.9",
    "typer>=0.12",
    "rich>=13",
]

[project.scripts]
crocodile = "crocodile.cli:app"

[dependency-groups]
dev = [
    "pytest>=8",
    "pytest-asyncio>=0.23",
    "pytest-cov>=5",
    "ruff>=0.6",
    "mypy>=1.11",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/crocodile"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
addopts = "-q"

[tool.ruff]
line-length = 100
src = ["src", "tests"]
[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "ASYNC", "RUF"]

[tool.mypy]
python_version = "3.12"
packages = ["crocodile"]
strict = true
ignore_missing_imports = true
```

- [ ] **Step 2: Create empty package files**

`src/crocodile/__init__.py`:
```python
"""Crocodile — open-source crypto market-data engine."""

__version__ = "0.0.1"
```
`src/crocodile/py.typed`: (empty file)

- [ ] **Step 3: Sync and verify**

Run: `uv sync`
Expected: resolves and installs all deps into `.venv`.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml src/crocodile/__init__.py src/crocodile/py.typed
git commit -m "chore: project skeleton + dependencies"
```

### Task 0.2: tooling smoke test

**Files:**
- Create: `tests/__init__.py`
- Create: `tests/test_smoke.py`

- [ ] **Step 1: Write smoke test**

`tests/test_smoke.py`:
```python
import crocodile

def test_version():
    assert crocodile.__version__
```

- [ ] **Step 2: Run the test**

Run: `uv run pytest`
Expected: 1 passed.

- [ ] **Step 3: Run lint + types**

Run: `uv run ruff check . && uv run mypy`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add tests/
git commit -m "test: tooling smoke test"
```

### Task 0.3: time + util helpers

**Files:**
- Create: `src/crocodile/util/time.py`
- Test: `tests/util/test_time.py`

- [ ] **Step 1: Write failing test**

`tests/util/test_time.py`:
```python
from crocodile.util.time import ms_to_ns, us_to_ns, now_ns

def test_ms_to_ns():
    assert ms_to_ns(1_700_000_000_000) == 1_700_000_000_000_000_000

def test_us_to_ns():
    assert us_to_ns(1_700_000_000_000_000) == 1_700_000_000_000_000_000

def test_now_ns_monotonic_realtime():
    a = now_ns(); b = now_ns()
    assert b >= a and a > 1_700_000_000_000_000_000
```

- [ ] **Step 2: Run to verify it fails** — `uv run pytest tests/util/test_time.py` → FAIL (module missing).

- [ ] **Step 3: Implement**

`src/crocodile/util/time.py`:
```python
import time

def ms_to_ns(ms: int | float) -> int:
    return int(ms) * 1_000_000

def us_to_ns(us: int | float) -> int:
    return int(us) * 1_000

def now_ns() -> int:
    """Capture clock for local_ts. Realtime so it's comparable to exchange_ts."""
    return time.clock_gettime_ns(time.CLOCK_REALTIME)
```
(Also create empty `tests/util/__init__.py`.)

- [ ] **Step 4: Run** — `uv run pytest tests/util/test_time.py` → PASS.

- [ ] **Step 5: Commit** — `git commit -am "feat: ns time helpers"`

### Task 0.4: README dev section + Makefile-style scripts

**Files:**
- Modify: `README.md` (append a `## Development` section with `uv sync`, `uv run pytest`, `uv run ruff check .`, `uv run mypy`).

- [ ] **Step 1:** Append the Development section to README.
- [ ] **Step 2: Commit** — `git commit -am "docs: dev quickstart"`

---

## M1 — Schema, Registry, Connector ABC, Deribit & Binance

### Task 1.1: Canonical enums + records

**Files:**
- Create: `src/crocodile/schema/__init__.py`, `src/crocodile/schema/enums.py`, `src/crocodile/schema/records.py`
- Test: `tests/schema/test_records.py`

- [ ] **Step 1: Write failing test**

`tests/schema/test_records.py`:
```python
import msgspec
from crocodile.schema.records import Trade, BookDelta
from crocodile.schema.enums import Side

def test_trade_encodes_with_channel_tag():
    t = Trade(exchange="deribit", symbol="BTC-PERPETUAL", symbol_raw="BTC-PERPETUAL",
              exchange_ts=1, local_ts=2, id="x", price=100.0, amount=1.0, side=Side.BUY)
    raw = msgspec.json.encode(t)
    d = msgspec.json.decode(raw)
    assert d["channel"] == "trade"
    assert d["side"] == "buy"

def test_book_delta_remove_level_is_zero_amount():
    d = BookDelta(exchange="binance-spot", symbol="BTC-USDT", symbol_raw="BTCUSDT",
                  exchange_ts=None, local_ts=2, bids=[(100.0, 0.0)], asks=[], seq_id=5)
    assert d.bids[0][1] == 0.0  # canonical removal signal
```

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** `enums.py` and `records.py` exactly as in the "Canonical Schema Reference" section above. Create empty `tests/schema/__init__.py`.
- [ ] **Step 4: Run** — `uv run pytest tests/schema/` → PASS.
- [ ] **Step 5: Commit** — `git commit -am "feat(schema): canonical records + enums"`

### Task 1.2: InstrumentRegistry

**Files:**
- Create: `src/crocodile/instruments/__init__.py`, `src/crocodile/instruments/registry.py`
- Test: `tests/instruments/test_registry.py`

**Design:** an `Instrument` Struct (canonical id, exchange, symbol_raw, kind, base, quote, strike?, expiry?, opt_type?, tick_size?, contract_size?, settlement_currency?, oi_unit?) and a `Registry` that maps `symbol_raw → Instrument` and `canonical → Instrument`, populated per-connector from REST `list_instruments`.

- [ ] **Step 1: Write failing test**

`tests/instruments/test_registry.py`:
```python
from crocodile.instruments.registry import Instrument, InstrumentRegistry, Kind

def test_register_and_resolve():
    reg = InstrumentRegistry()
    inst = Instrument(canonical="deribit:BTC-PERPETUAL", exchange="deribit",
                      symbol_raw="BTC-PERPETUAL", kind=Kind.PERPETUAL, base="BTC", quote="USD")
    reg.add(inst)
    assert reg.by_raw("deribit", "BTC-PERPETUAL").canonical == "deribit:BTC-PERPETUAL"
    assert reg.by_canonical("deribit:BTC-PERPETUAL").symbol_raw == "BTC-PERPETUAL"

def test_option_metadata_round_trip():
    reg = InstrumentRegistry()
    inst = Instrument(canonical="deribit:BTC-30JUN-50000-C", exchange="deribit",
                      symbol_raw="BTC-30JUN-50000-C", kind=Kind.OPTION, base="BTC", quote="USD",
                      strike=50000.0, expiry=1_900_000_000_000_000_000, opt_type="C")
    reg.add(inst)
    got = reg.by_raw("deribit", "BTC-30JUN-50000-C")
    assert got.strike == 50000.0 and got.opt_type == "C"
```

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement**

`src/crocodile/instruments/registry.py`:
```python
from enum import Enum
import msgspec

class Kind(str, Enum):
    SPOT = "spot"; PERPETUAL = "perpetual"; FUTURE = "future"; OPTION = "option"

class Instrument(msgspec.Struct, frozen=True):
    canonical: str; exchange: str; symbol_raw: str; kind: Kind
    base: str; quote: str
    strike: float | None = None; expiry: int | None = None; opt_type: str | None = None
    tick_size: float | None = None; contract_size: float | None = None
    settlement_currency: str | None = None; oi_unit: str | None = None

class InstrumentRegistry:
    def __init__(self) -> None:
        self._by_raw: dict[tuple[str, str], Instrument] = {}
        self._by_canonical: dict[str, Instrument] = {}

    def add(self, inst: Instrument) -> None:
        self._by_raw[(inst.exchange, inst.symbol_raw)] = inst
        self._by_canonical[inst.canonical] = inst

    def by_raw(self, exchange: str, symbol_raw: str) -> Instrument:
        return self._by_raw[(exchange, symbol_raw)]

    def by_canonical(self, canonical: str) -> Instrument:
        return self._by_canonical[canonical]

    def get_raw(self, exchange: str, symbol_raw: str) -> Instrument | None:
        return self._by_raw.get((exchange, symbol_raw))
```

- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** — `git commit -am "feat(instruments): registry + Instrument model"`

### Task 1.3: Sink ABC + in-memory sink

**Files:**
- Create: `src/crocodile/sink/__init__.py`, `src/crocodile/sink/base.py`, `src/crocodile/sink/memory.py`
- Test: `tests/sink/test_memory.py`

**Design:** `Sink` is the async output boundary every connector writes to. `MemorySink` collects records (for tests). The Parquet sink (M2) implements the same ABC.

- [ ] **Step 1: Write failing test**

`tests/sink/test_memory.py`:
```python
import pytest
from crocodile.sink.memory import MemorySink
from crocodile.schema.records import Trade
from crocodile.schema.enums import Side

async def test_memory_sink_collects():
    s = MemorySink()
    t = Trade(exchange="x", symbol="A", symbol_raw="A", exchange_ts=1, local_ts=2,
              id="1", price=1.0, amount=1.0, side=Side.BUY)
    await s.put(t)
    await s.flush()
    assert s.records == [t]
```

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement**

`src/crocodile/sink/base.py`:
```python
from abc import ABC, abstractmethod
from crocodile.schema.records import Record

class Sink(ABC):
    @abstractmethod
    async def put(self, record: Record) -> None: ...
    @abstractmethod
    async def flush(self) -> None: ...
    async def close(self) -> None:
        await self.flush()
```
`src/crocodile/sink/memory.py`:
```python
from crocodile.schema.records import Record
from crocodile.sink.base import Sink

class MemorySink(Sink):
    def __init__(self) -> None:
        self.records: list[Record] = []
    async def put(self, record: Record) -> None:
        self.records.append(record)
    async def flush(self) -> None:
        return None
```

- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** — `git commit -am "feat(sink): Sink ABC + MemorySink"`

### Task 1.4: Connector ABC + DLQ + WS transport helper

**Files:**
- Create: `src/crocodile/exchanges/__init__.py`, `src/crocodile/exchanges/base.py`
- Create: `src/crocodile/ingest/__init__.py`, `src/crocodile/ingest/deadletter.py`
- Test: `tests/exchanges/test_base.py`

**Design (appendix §2, §6):** `Connector` ABC with lifecycle (`run/connect/subscribe/close`), hot-path (`on_message` captures `local_ts` FIRST then calls `normalize`), book methods (`apply_book`, `resync_book`), `backfill`, `list_instruments`. The ABC provides a concrete `run()` with supervised reconnect (exponential backoff 1s→×2→cap 30s + 0–25% jitter) and re-subscribe from cached subs. `normalize` is the only required abstract per-message method for the unit tests; transport is injected so connectors are testable without network.

- [ ] **Step 1: Write failing test (normalize contract + DLQ)**

`tests/exchanges/test_base.py`:
```python
import pytest
from crocodile.ingest.deadletter import DeadLetterQueue
from crocodile.exchanges.base import backoff_delays

def test_backoff_is_bounded_and_jittered():
    delays = [backoff_delays(i, base=1.0, cap=30.0, jitter=0.0) for i in range(10)]
    assert delays[0] == 1.0
    assert delays[1] == 2.0
    assert max(delays) <= 30.0
    assert delays[-1] == 30.0  # capped

async def test_dead_letter_bounded():
    dlq = DeadLetterQueue(max_size=2)
    await dlq.put(b"a", "parse", "trace")
    await dlq.put(b"b", "parse", "trace")
    await dlq.put(b"c", "parse", "trace")  # evicts oldest
    items = dlq.drain()
    assert len(items) == 2
    assert items[-1].raw == b"c"
```

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement**

`src/crocodile/exchanges/base.py`:
```python
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Iterable

from crocodile.instruments.registry import Instrument, InstrumentRegistry
from crocodile.schema.records import Record
from crocodile.sink.base import Sink

def backoff_delays(attempt: int, base: float = 1.0, cap: float = 30.0,
                   jitter: float = 0.25, rand: float = 0.0) -> float:
    raw = min(cap, base * (2 ** attempt))
    return raw * (1.0 + jitter * rand)

class Connector(ABC):
    name: str
    ws_url: str
    rest_url: str

    def __init__(self, symbols: list[str], channels: list[str], out: Sink,
                 registry: InstrumentRegistry) -> None:
        self.symbols = symbols
        self.channels = channels
        self.out = out
        self.registry = registry

    @abstractmethod
    def normalize(self, msg: object, local_ts: int) -> Iterable[Record]: ...

    @abstractmethod
    async def list_instruments(self) -> list[Instrument]: ...

    async def backfill(self, channel: str, symbol: str, start_ns: int,
                       end_ns: int) -> AsyncIterator[Record]:
        raise NotImplementedError
        yield  # pragma: no cover  (makes this an async generator)
```
`src/crocodile/ingest/deadletter.py`:
```python
from collections import deque
import msgspec

class DeadLetter(msgspec.Struct, frozen=True):
    raw: bytes; error_type: str; traceback: str

class DeadLetterQueue:
    def __init__(self, max_size: int = 10_000) -> None:
        self._dq: deque[DeadLetter] = deque(maxlen=max_size)
    async def put(self, raw: bytes, error_type: str, traceback: str) -> None:
        self._dq.append(DeadLetter(raw=raw, error_type=error_type, traceback=traceback))
    def drain(self) -> list[DeadLetter]:
        items = list(self._dq); self._dq.clear(); return items
```

- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** — `git commit -am "feat(exchanges): Connector ABC + backoff + dead-letter queue"`

### Task 1.5: Deribit — trade normalization + golden fixture

**Files:**
- Create: `src/crocodile/exchanges/deribit/__init__.py`, `src/crocodile/exchanges/deribit/connector.py`, `src/crocodile/exchanges/deribit/normalize.py`
- Create: `tests/exchanges/deribit/__init__.py`, `tests/exchanges/deribit/fixtures/trades.json`
- Test: `tests/exchanges/deribit/test_normalize_trades.py`

**Appendix §3.1:** `trades.{instrument}.raw` → fields `trade_id`, `price`, `amount`, `direction`(buy/sell), `timestamp`(ms), `liquidation`(string enum `M`/`T`/`MT`, optional). Emit `Trade`; if `liquidation` present, also emit `Liquidation` (side from `direction`).

- [ ] **Step 1: Create golden fixture** `tests/exchanges/deribit/fixtures/trades.json`:
```json
{"params":{"channel":"trades.BTC-PERPETUAL.raw","data":[
 {"trade_id":"ETH-1","price":2000.5,"amount":10.0,"direction":"buy","timestamp":1700000000000,"instrument_name":"BTC-PERPETUAL","index_price":2000.0,"mark_price":2000.4},
 {"trade_id":"ETH-2","price":1999.0,"amount":3.0,"direction":"sell","timestamp":1700000000500,"instrument_name":"BTC-PERPETUAL","index_price":1999.5,"mark_price":1999.4,"liquidation":"T"}
]}}
```

- [ ] **Step 2: Write failing test**

`tests/exchanges/deribit/test_normalize_trades.py`:
```python
import json, pathlib, msgspec
from crocodile.exchanges.deribit.normalize import normalize_message
from crocodile.schema.records import Trade, Liquidation
from crocodile.schema.enums import Side

FIX = pathlib.Path(__file__).parent / "fixtures" / "trades.json"

def test_trade_and_liquidation_emitted():
    msg = json.loads(FIX.read_text())
    out = list(normalize_message(msg, local_ts=42))
    trades = [r for r in out if isinstance(r, Trade)]
    liqs = [r for r in out if isinstance(r, Liquidation)]
    assert len(trades) == 2
    assert trades[0].price == 2000.5 and trades[0].side == Side.BUY
    assert trades[0].exchange_ts == 1700000000000 * 1_000_000  # ms→ns
    assert trades[0].local_ts == 42
    assert trades[1].liquidation == "T"
    assert len(liqs) == 1 and liqs[0].side == Side.SELL  # from direction "sell"
```

- [ ] **Step 3: Run** → FAIL.
- [ ] **Step 4: Implement** `normalize.py` (trade path only for now):
```python
from collections.abc import Iterable
from crocodile.schema.records import Record, Trade, Liquidation
from crocodile.schema.enums import Side
from crocodile.util.time import ms_to_ns

EXCHANGE = "deribit"

def _side(direction: str) -> Side:
    return Side.BUY if direction == "buy" else Side.SELL if direction == "sell" else Side.UNKNOWN

def normalize_message(msg: dict, local_ts: int) -> Iterable[Record]:
    params = msg.get("params") or {}
    channel = params.get("channel", "")
    data = params.get("data")
    if channel.startswith("trades."):
        for t in (data or []):
            sym = t["instrument_name"]
            side = _side(t["direction"])
            yield Trade(exchange=EXCHANGE, symbol=f"{EXCHANGE}:{sym}", symbol_raw=sym,
                        exchange_ts=ms_to_ns(t["timestamp"]), local_ts=local_ts,
                        id=str(t["trade_id"]), price=float(t["price"]),
                        amount=float(t["amount"]), side=side,
                        liquidation=t.get("liquidation"))
            if t.get("liquidation"):
                yield Liquidation(exchange=EXCHANGE, symbol=f"{EXCHANGE}:{sym}", symbol_raw=sym,
                                  exchange_ts=ms_to_ns(t["timestamp"]), local_ts=local_ts,
                                  price=float(t["price"]), amount=float(t["amount"]),
                                  side=side, id=str(t["trade_id"]))
```

- [ ] **Step 5: Run** → PASS.
- [ ] **Step 6: Commit** — `git commit -am "feat(deribit): trade + liquidation normalization (golden test)"`

### Task 1.6: Deribit — order book (3-tuple WS deltas, action=delete → amount=0)

**Files:**
- Modify: `src/crocodile/exchanges/deribit/normalize.py`
- Create: `tests/exchanges/deribit/fixtures/book.json`
- Test: `tests/exchanges/deribit/test_normalize_book.py`

**Appendix §3.1 + §8:** WS `book.{instrument}` levels are **3-tuples** `[action, price, amount]`, action ∈ `new|change|delete`. First message `type=="snapshot"` → `BookSnapshot`; `type=="change"` → `BookDelta`. **`action=="delete"` ⇒ canonical `amount=0.0`** (ignore the wire amount). Carries `change_id` + `prev_change_id` → `seq_id`/`prev_seq_id`.

- [ ] **Step 1: Fixture** `book.json` (snapshot then a delta with a delete):
```json
[
 {"params":{"channel":"book.BTC-PERPETUAL.raw","data":{"type":"snapshot","timestamp":1700000000000,"instrument_name":"BTC-PERPETUAL","change_id":100,"bids":[["new",100.0,5.0],["new",99.0,2.0]],"asks":[["new",101.0,4.0]]}}},
 {"params":{"channel":"book.BTC-PERPETUAL.raw","data":{"type":"change","timestamp":1700000000100,"instrument_name":"BTC-PERPETUAL","change_id":101,"prev_change_id":100,"bids":[["delete",99.0,0.0],["change",100.0,7.0]],"asks":[["new",102.0,1.0]]}}}
]
```

- [ ] **Step 2: Write failing test**

```python
import json, pathlib
from crocodile.exchanges.deribit.normalize import normalize_message
from crocodile.schema.records import BookSnapshot, BookDelta

FIX = pathlib.Path(__file__).parent / "fixtures" / "book.json"

def test_book_snapshot_then_delta_with_delete():
    msgs = json.loads(FIX.read_text())
    snap = list(normalize_message(msgs[0], local_ts=1))[0]
    assert isinstance(snap, BookSnapshot)
    assert snap.sequence_id == 100 and snap.is_snapshot
    assert (100.0, 5.0) in snap.bids

    delta = list(normalize_message(msgs[1], local_ts=2))[0]
    assert isinstance(delta, BookDelta)
    assert delta.seq_id == 101 and delta.prev_seq_id == 100
    # action=delete normalized to amount 0.0 (canonical removal)
    assert (99.0, 0.0) in delta.bids
    assert (100.0, 7.0) in delta.bids
```

- [ ] **Step 3: Run** → FAIL.
- [ ] **Step 4: Implement** — add book branch to `normalize_message`:
```python
def _levels(rows: list[list]) -> list[tuple[float, float]]:
    out = []
    for action, price, amount in rows:
        out.append((float(price), 0.0 if action == "delete" else float(amount)))
    return out

# inside normalize_message, after the trades branch:
    if channel.startswith("book."):
        d = data or {}
        sym = d["instrument_name"]
        common = dict(exchange=EXCHANGE, symbol=f"{EXCHANGE}:{sym}", symbol_raw=sym,
                      exchange_ts=ms_to_ns(d["timestamp"]), local_ts=local_ts,
                      bids=_levels(d.get("bids", [])), asks=_levels(d.get("asks", [])))
        if d.get("type") == "snapshot":
            yield BookSnapshot(**common, depth=len(d.get("bids", [])) + len(d.get("asks", [])),
                               sequence_id=d.get("change_id"), is_snapshot=True)
        else:
            yield BookDelta(**common, seq_id=d.get("change_id"),
                            prev_seq_id=d.get("prev_change_id"), is_snapshot=False)
```
(Import `BookSnapshot, BookDelta` at top.)

- [ ] **Step 5: Run** → PASS.
- [ ] **Step 6: Commit** — `git commit -am "feat(deribit): order-book normalization (action=delete -> amount 0)"`

### Task 1.7: Deribit — ticker → derivative_ticker / options_chain / funding

**Files:**
- Modify: `src/crocodile/exchanges/deribit/normalize.py`
- Create: `tests/exchanges/deribit/fixtures/ticker_perp.json`, `tests/exchanges/deribit/fixtures/ticker_option.json`
- Test: `tests/exchanges/deribit/test_normalize_ticker.py`

**Appendix §1 + §3.1:** perp ticker → `DerivativeTicker` (mark/index/last/open_interest) **plus `Funding`** derived from `current_funding` (canonical `funding_rate = current_funding`; `funding_8h` → `predicted_funding_rate`). Option ticker → `OptionsChain` with greeks/IV (nullable). Strike/expiry/opt_type/underlying resolved from the registry (fall back to parsing the symbol if absent).

- [ ] **Step 1: Fixtures**

`ticker_perp.json`:
```json
{"params":{"channel":"ticker.BTC-PERPETUAL","data":{"instrument_name":"BTC-PERPETUAL","timestamp":1700000000000,"mark_price":2000.4,"index_price":2000.0,"last_price":2000.5,"open_interest":12345.0,"current_funding":0.0001,"funding_8h":0.0003}}}
```
`ticker_option.json`:
```json
{"params":{"channel":"ticker.BTC-30JUN-50000-C","data":{"instrument_name":"BTC-30JUN-50000-C","timestamp":1700000000000,"mark_price":0.05,"mark_iv":65.0,"underlying_price":50000.0,"open_interest":10.0,"best_bid_price":0.04,"best_bid_amount":2.0,"bid_iv":64.0,"best_ask_price":0.06,"best_ask_amount":1.0,"ask_iv":66.0,"greeks":{"delta":0.5,"gamma":0.001,"vega":12.0,"theta":-3.0,"rho":1.0}}}}
```

- [ ] **Step 2: Write failing test**

```python
import json, pathlib
from crocodile.instruments.registry import Instrument, InstrumentRegistry, Kind
from crocodile.exchanges.deribit.normalize import normalize_message
from crocodile.schema.records import DerivativeTicker, Funding, OptionsChain
from crocodile.schema.enums import OptType

P = pathlib.Path(__file__).parent / "fixtures"

def test_perp_ticker_emits_derivative_and_funding():
    msg = json.loads((P / "ticker_perp.json").read_text())
    out = list(normalize_message(msg, local_ts=7))
    dt = [r for r in out if isinstance(r, DerivativeTicker)][0]
    fn = [r for r in out if isinstance(r, Funding)][0]
    assert dt.mark_price == 2000.4 and dt.open_interest == 12345.0
    assert fn.funding_rate == 0.0001 and fn.predicted_funding_rate == 0.0003

def test_option_ticker_emits_options_chain():
    reg = InstrumentRegistry()
    reg.add(Instrument(canonical="deribit:BTC-30JUN-50000-C", exchange="deribit",
                       symbol_raw="BTC-30JUN-50000-C", kind=Kind.OPTION, base="BTC", quote="USD",
                       strike=50000.0, expiry=1_900_000_000_000_000_000, opt_type="C"))
    msg = json.loads((P / "ticker_option.json").read_text())
    out = list(normalize_message(msg, local_ts=7, registry=reg))
    oc = [r for r in out if isinstance(r, OptionsChain)][0]
    assert oc.strike == 50000.0 and oc.opt_type == OptType.CALL
    assert oc.mark_iv == 65.0 and oc.delta == 0.5 and oc.bid_iv == 64.0
```

- [ ] **Step 3: Run** → FAIL.
- [ ] **Step 4: Implement** — add `registry` optional param to `normalize_message` and a ticker branch. Distinguish option vs perp by presence of `greeks`/`mark_iv` (or registry kind). Resolve strike/expiry/opt_type/underlying from registry if available, else parse the Deribit symbol (`BASE-DDMMM-STRIKE-C|P`). Map greeks fields. Emit `Funding` from `current_funding`/`funding_8h` for perps. (Document: canonical `funding_rate = current_funding`.)
- [ ] **Step 5: Run** → PASS.
- [ ] **Step 6: Commit** — `git commit -am "feat(deribit): ticker -> derivative_ticker/options_chain/funding"`

### Task 1.8: Deribit connector wiring (REST instruments + WS subscribe build)

**Files:**
- Modify: `src/crocodile/exchanges/deribit/connector.py`
- Test: `tests/exchanges/deribit/test_connector.py`

**Design:** `DeribitConnector(Connector)` implements `normalize` (delegates to `normalize_message`), `list_instruments` (REST `public/get_instruments` via aiohttp; parse strike/expiry/contract_size/tick_size), and `subscribe_channels()` building channel strings from `symbols`×`channels` (e.g. `trades.{sym}.raw`, `book.{sym}.raw`, `ticker.{sym}`). Network calls are isolated; `subscribe_channels()` is pure and unit-tested. `list_instruments` is tested against a saved REST fixture (parse-only, no live call).

- [ ] **Step 1: Write failing test (pure channel builder + REST parse)**

```python
from crocodile.exchanges.deribit.connector import DeribitConnector, build_channels, parse_instruments
import json, pathlib

def test_build_channels():
    chans = build_channels(["BTC-PERPETUAL"], ["trade", "book_delta", "derivative_ticker"])
    assert "trades.BTC-PERPETUAL.raw" in chans
    assert "book.BTC-PERPETUAL.raw" in chans
    assert "ticker.BTC-PERPETUAL" in chans

def test_parse_instruments():
    raw = {"result":[{"instrument_name":"BTC-30JUN-50000-C","kind":"option","base_currency":"BTC",
            "quote_currency":"USD","strike":50000.0,"expiration_timestamp":1700000000000,
            "option_type":"call","tick_size":0.0005,"contract_size":1.0}]}
    insts = parse_instruments(raw)
    assert insts[0].canonical == "deribit:BTC-30JUN-50000-C"
    assert insts[0].opt_type == "C" and insts[0].expiry == 1700000000000 * 1_000_000
```

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** `build_channels`, `parse_instruments`, and the `DeribitConnector` class (with `list_instruments` doing the aiohttp GET then `parse_instruments`).
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** — `git commit -am "feat(deribit): connector wiring (channels + instrument parse)"`

### Task 1.9: Binance spot — trade + bookTicker normalization

**Files:**
- Create: `src/crocodile/exchanges/binance/__init__.py`, `src/crocodile/exchanges/binance/normalize.py`, `src/crocodile/exchanges/binance/connector.py`
- Create: `tests/exchanges/binance/fixtures/spot_aggtrade.json`, `tests/exchanges/binance/fixtures/spot_bookticker.json`
- Test: `tests/exchanges/binance/test_normalize_spot.py`

**Appendix §3.2:** `aggTrade` → `Trade` (`m`=buyer_is_maker → side `sell` if true else `buy`; `T`=trade time ms; `a`=agg id; `p`/`q`). `bookTicker` → `BookTicker` (`b`/`B`/`a`/`A`, `u`). Symbol uppercase native; canonical `binance-spot:BTC-USDT` (map `BTCUSDT`→`BTC-USDT` via registry; for the unit test, accept a passed venue + a symbol resolver).

- [ ] **Step 1: Fixtures**

`spot_aggtrade.json`:
```json
{"stream":"btcusdt@aggTrade","data":{"e":"aggTrade","E":1700000000123,"s":"BTCUSDT","a":555,"p":"50000.10","q":"0.5","f":1,"l":3,"T":1700000000100,"m":true}}
```
`spot_bookticker.json`:
```json
{"stream":"btcusdt@bookTicker","data":{"u":99,"s":"BTCUSDT","b":"49999.0","B":"1.2","a":"50001.0","A":"0.8"}}
```

- [ ] **Step 2: Write failing test**

```python
import json, pathlib
from crocodile.exchanges.binance.normalize import normalize_message
from crocodile.schema.records import Trade, BookTicker
from crocodile.schema.enums import Side

P = pathlib.Path(__file__).parent / "fixtures"

def test_spot_aggtrade():
    msg = json.loads((P / "spot_aggtrade.json").read_text())
    t = list(normalize_message(msg, local_ts=9, venue="binance-spot"))[0]
    assert isinstance(t, Trade)
    assert t.price == 50000.10 and t.amount == 0.5
    assert t.side == Side.SELL            # m=true => buyer is maker => taker sold
    assert t.exchange_ts == 1700000000100 * 1_000_000   # uses T, not E
    assert t.symbol_raw == "BTCUSDT"

def test_spot_bookticker():
    msg = json.loads((P / "spot_bookticker.json").read_text())
    bt = list(normalize_message(msg, local_ts=9, venue="binance-spot"))[0]
    assert isinstance(bt, BookTicker)
    assert bt.bid_px == 49999.0 and bt.ask_sz == 0.8 and bt.update_id == 99
```

- [ ] **Step 3: Run** → FAIL.
- [ ] **Step 4: Implement** `normalize_message(msg, local_ts, venue, registry=None)` handling `@aggTrade` and `@bookTicker`. Side rule: `Side.SELL if data["m"] else Side.BUY`. `exchange_ts = ms_to_ns(data.get("T") or data["E"])` for trades; bookTicker has no timestamp → `exchange_ts=None`. Canonical symbol via registry or `venue + ":" + raw` fallback.
- [ ] **Step 5: Run** → PASS.
- [ ] **Step 6: Commit** — `git commit -am "feat(binance): spot aggTrade + bookTicker normalization"`

### Task 1.10: Binance — depth diff normalization + OrderBookSync (spot vs futures rules)

**Files:**
- Create: `src/crocodile/exchanges/binance/book.py`
- Create: `tests/exchanges/binance/fixtures/spot_depth.json`, `tests/exchanges/binance/fixtures/usdm_depth.json`
- Test: `tests/exchanges/binance/test_book_sync.py`

**Appendix §3.2 + §8 (the highest-risk task — implement the exact rules):**
- Diff event `{U, u, pu?, b, a}`; `qty=0` ⇒ remove. **spot** has no `pu`; **futures** has `pu`.
- Map to `BookDelta`: `seq_id=u`; spot `prev_seq_id=None`; futures `prev_seq_id=pu`.
- `OrderBookSync` state machine: buffer events; on REST snapshot `lastUpdateId`:
  - spot: drop buffered `u <= lastUpdateId`; first applied event needs `U <= lastUpdateId+1 AND u >= lastUpdateId+1`; thereafter `U == prev_u + 1`.
  - futures: drop buffered `u < lastUpdateId`; first applied event needs `U <= lastUpdateId AND u >= lastUpdateId`; thereafter `pu == prev_u`.
  - On continuity break → signal `RESYNC` (caller re-fetches snapshot).

- [ ] **Step 1: Write failing test (the sync algorithm, both venues, no network)**

```python
from crocodile.exchanges.binance.book import OrderBookSync, SyncResult

def test_spot_first_event_offset_and_continuity():
    s = OrderBookSync(venue="spot")
    s.set_snapshot(last_update_id=100)
    # stale, dropped (u <= 100)
    assert s.feed(U=90, u=100, pu=None) == SyncResult.DROP
    # first valid: U<=101<=u
    assert s.feed(U=101, u=105, pu=None) == SyncResult.APPLY
    # continuity U == prev_u+1 == 106
    assert s.feed(U=106, u=110, pu=None) == SyncResult.APPLY
    # gap -> resync
    assert s.feed(U=120, u=130, pu=None) == SyncResult.RESYNC

def test_futures_first_event_no_offset_and_pu_continuity():
    s = OrderBookSync(venue="futures")
    s.set_snapshot(last_update_id=100)
    assert s.feed(U=80, u=99, pu=70) == SyncResult.DROP      # u < 100
    assert s.feed(U=95, u=100, pu=90) == SyncResult.APPLY    # U<=100<=u
    assert s.feed(U=101, u=110, pu=100) == SyncResult.APPLY  # pu == prev_u(100)
    assert s.feed(U=111, u=120, pu=999) == SyncResult.RESYNC # pu != prev_u
```

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** `SyncResult` (enum DROP/APPLY/RESYNC) and `OrderBookSync` exactly per the rules above (track `prev_u`, `have_first`). Add a `normalize_depth(msg, local_ts, venue, registry)` producing a `BookDelta`.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** — `git commit -am "feat(binance): depth diff + order-book sync state machine (spot/futures)"`

### Task 1.11: Binance USDⓂ — markPrice (funding) + forceOrder (liquidation)

**Files:**
- Modify: `src/crocodile/exchanges/binance/normalize.py`
- Create fixtures: `usdm_markprice.json`, `usdm_forceorder.json`
- Test: `tests/exchanges/binance/test_normalize_usdm.py`

**Appendix §3.2:** `@markPrice` → `DerivativeTicker` + `Funding` (`p`=mark, `i`=index, `r`=funding_rate, `T`=next funding ts). `@forceOrder` → `Liquidation` (`o.S` side, `o.ap` exec price, `o.q` qty, `o.T` ts).

- [ ] **Step 1: Fixtures**

`usdm_markprice.json`:
```json
{"stream":"btcusdt@markPrice","data":{"e":"markPriceUpdate","E":1700000000000,"s":"BTCUSDT","p":"50000.0","i":"50001.0","r":"0.0001","T":1700003600000}}
```
`usdm_forceorder.json`:
```json
{"stream":"btcusdt@forceOrder","data":{"e":"forceOrder","E":1700000000000,"o":{"s":"BTCUSDT","S":"SELL","q":"1.5","p":"49000.0","ap":"48950.0","T":1700000000000}}}
```

- [ ] **Step 2: Write failing test**

```python
import json, pathlib
from crocodile.exchanges.binance.normalize import normalize_message
from crocodile.schema.records import DerivativeTicker, Funding, Liquidation
from crocodile.schema.enums import Side

P = pathlib.Path(__file__).parent / "fixtures"

def test_markprice_emits_derivative_and_funding():
    msg = json.loads((P / "usdm_markprice.json").read_text())
    out = list(normalize_message(msg, local_ts=1, venue="binance-usdm"))
    dt = [r for r in out if isinstance(r, DerivativeTicker)][0]
    fn = [r for r in out if isinstance(r, Funding)][0]
    assert dt.mark_price == 50000.0 and dt.index_price == 50001.0
    assert fn.funding_rate == 0.0001 and fn.funding_timestamp == 1700003600000 * 1_000_000

def test_forceorder_emits_liquidation():
    msg = json.loads((P / "usdm_forceorder.json").read_text())
    liq = list(normalize_message(msg, local_ts=1, venue="binance-usdm"))[0]
    assert isinstance(liq, Liquidation)
    assert liq.side == Side.SELL and liq.price == 48950.0 and liq.amount == 1.5
```

- [ ] **Step 3: Run** → FAIL.
- [ ] **Step 4: Implement** the `@markPrice` and `@forceOrder` branches (route `@depth` to `normalize_depth` from Task 1.10). Liquidation `price = o.ap` (exec price), `side = BUY/SELL` from `o.S`.
- [ ] **Step 5: Run** → PASS.
- [ ] **Step 6: Commit** — `git commit -am "feat(binance): markPrice funding + forceOrder liquidation"`

### Task 1.12: Connector run-loop integration (mock transport) + M1 gate

**Files:**
- Modify: `src/crocodile/exchanges/base.py` (concrete `run()` with injected transport)
- Create: `src/crocodile/ingest/transport.py` (WS transport protocol + a `FakeTransport` for tests)
- Test: `tests/ingest/test_runloop.py`

**Design:** `run()` loops: `connect()` (open transport), `subscribe()` (send cached sub frames), then `async for raw in transport: local_ts = now_ns(); for rec in normalize(decode(raw), local_ts): await out.put(rec)`. On exception: increment attempt, sleep `backoff_delays(...)`, re-`connect`/`subscribe`. Unparseable → DLQ, continue. Transport is a Protocol so a `FakeTransport` (yields canned frames then raises `StopAsyncIteration`/closes) drives the loop deterministically without network.

- [ ] **Step 1: Write failing test**

```python
from crocodile.ingest.transport import FakeTransport
from crocodile.sink.memory import MemorySink
from crocodile.exchanges.deribit.connector import DeribitConnector
from crocodile.instruments.registry import InstrumentRegistry
from crocodile.schema.records import Trade
import json, pathlib

FIX = pathlib.Path("tests/exchanges/deribit/fixtures/trades.json").read_text()

async def test_runloop_drains_transport_into_sink():
    sink = MemorySink()
    conn = DeribitConnector(symbols=["BTC-PERPETUAL"], channels=["trade"],
                            out=sink, registry=InstrumentRegistry())
    conn.transport = FakeTransport(frames=[FIX.encode()])
    await conn.run(max_reconnects=0)  # run until transport exhausts, no reconnect
    assert any(isinstance(r, Trade) for r in sink.records)
```

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** `transport.py` (`Transport` Protocol: `connect()`, `__aiter__`, `send()`, `close()`; `FakeTransport`) and the concrete `run()` in `base.py` honoring `max_reconnects`. Decode raw via `msgspec.json.decode` (or `json.loads`) into a dict; on decode error → DLQ.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Run full M1 suite + lint + types**

Run: `uv run pytest && uv run ruff check . && uv run mypy`
Expected: all green. **M1 GATE PASSED.**

- [ ] **Step 6: Commit** — `git commit -am "feat(ingest): supervised run-loop + transport abstraction (M1 complete)"`

---

## M2 — Storage (Parquet + DuckDB) & Replay

> Tasks here follow the same TDD rhythm (write failing test → run → implement → run → commit). Each references the appendix's concrete code. Acceptance tests are given; implement to pass them.

### Task 2.1: Record → row mapping (Polars-ready dicts)

**Files:** Create `src/crocodile/store/rows.py`; Test `tests/store/test_rows.py`.
**Appendix §4.** Convert any `Record` to a flat dict with a `channel` field, `date` (UTC `YYYY-MM-DD` from `local_ts`), and a `bucket = mh3(symbol) % 128` partition column. Book `bids`/`asks` (list of tuples) stored as a Parquet list-of-structs or JSON-encoded string column (decide: **list-of-structs** via Polars `list[struct[price,amount]]`).
**Acceptance test:**
```python
from crocodile.store.rows import to_row
from crocodile.schema.records import Trade
from crocodile.schema.enums import Side
def test_to_row_adds_partition_cols():
    t = Trade(exchange="deribit", symbol="deribit:BTC-PERPETUAL", symbol_raw="BTC-PERPETUAL",
              exchange_ts=1700000000000000000, local_ts=1700000000000000000,
              id="1", price=1.0, amount=2.0, side=Side.BUY)
    row = to_row(t)
    assert row["channel"] == "trade"
    assert row["date"] == "2023-11-14"
    assert 0 <= row["bucket"] < 128
    assert row["side"] == "buy"
```

### Task 2.2: ParquetSink (buffered, hive-partitioned)

**Files:** Create `src/crocodile/store/parquet_sink.py`; Test `tests/store/test_parquet_sink.py`.
**Appendix §4.** Implements `Sink`. Buffers rows per `(channel)`; flush on **≥N rows OR ≥T seconds** to `data/exchange=…/channel=…/date=…/bucket=…/part-{uuid}.parquet`, zstd level 5, row_group_size 250k. Never append to an existing file (new `part-*` each flush). One Polars schema per channel (records of a channel are homogeneous).
**Acceptance test:** write 3 trades + 1 book_snapshot to a tmp dir; assert files exist under `exchange=.../channel=trade/...` and `channel=book_snapshot/...`; read back with `pl.read_parquet(glob)` and assert row counts + that a removed book level `(px, 0.0)` round-trips.

### Task 2.3: DuckDB catalog + reader

**Files:** Create `src/crocodile/store/catalog.py`; Test `tests/store/test_catalog.py`.
**Appendix §4.** `Catalog(data_dir)` builds per-channel `read_parquet(glob, hive_partitioning=true, union_by_name=true)` views; `query(sql) -> pl.DataFrame`; `scan(channel, symbol, start_ns, end_ns) -> pl.DataFrame` that **narrows the glob path by exchange/channel/date before WHERE** (partition pruning). 
**Acceptance test:** after writing fixtures via ParquetSink, `catalog.scan("trade", "deribit:BTC-PERPETUAL", start, end)` returns the rows ordered by `local_ts`; a raw `catalog.query("SELECT count(*) FROM trade")` matches.

### Task 2.4: Replay k-way merge

**Files:** Create `src/crocodile/replay/merge.py`; Test `tests/replay/test_merge.py`.
**Appendix §5.** `replay(streams) -> Iterator[Record]` using `heapq.merge` with key `(local_ts, exchange_ts or -inf, seq or 0)`. Inputs are per-(channel,symbol) iterators already sorted by `local_ts`. 
**Acceptance test:** feed two pre-sorted in-memory record lists with interleaved `local_ts`; assert output is globally non-decreasing in `local_ts` and tie-breaks deterministically when `local_ts` ties (NULL `exchange_ts` sorts before a present one).

### Task 2.5: Order-book reconstruction

**Files:** Create `src/crocodile/replay/orderbook.py`; Test `tests/replay/test_orderbook.py`.
**Appendix §5 (state machine):** `OrderBook` applies `BookSnapshot` (reset) then `BookDelta`s; `amount>0` set level, `amount==0` remove; batch deltas sharing one `local_ts` atomically; skip rows before first snapshot; gap detection per both shapes (`prev_seq_id==last seq_id`, or spot `U==prev_u+1` when `prev_seq_id is None`) → raise `BookGap` (caller resyncs).
**Acceptance test:** apply the Deribit `book.json` fixture (snapshot + delta-with-delete): after applying, price `99.0` is **absent** (deleted), `100.0` has size `7.0`, `102.0` present at `1.0`; top-of-book bid = `100.0`. Then feed a delta with a non-contiguous `seq_id` → expect `BookGap`.

### Task 2.6: M2 integration + gate

**Files:** Test `tests/store/test_end_to_end.py`.
End-to-end: normalize fixtures → ParquetSink → Catalog query → replay merge → reconstruct book. Run `uv run pytest && uv run ruff check . && uv run mypy`. **M2 GATE.** Commit `feat(store,replay): parquet+duckdb+replay (M2 complete)`.

---

## M3 — Client & CLI

### Task 3.1: CrocodileClient.query / scan

**Files:** Create `src/crocodile/client/__init__.py`, `src/crocodile/client/client.py`; Test `tests/client/test_query.py`.
`CrocodileClient(data_dir)` wraps `Catalog`. `.query(sql)`, `.scan(channel, symbols, start, end)`. **Acceptance:** returns Polars DataFrame matching catalog output.

### Task 3.2: CrocodileClient.replay

**Files:** Modify `client.py`; Test `tests/client/test_replay.py`.
`.replay(channels, symbols, frm, to) -> Iterator[Record]` reads matching partitions sorted by `local_ts` and applies the M2 merge. **Acceptance:** time-ordered Records across two symbols.

### Task 3.3: CrocodileClient.export (multi-format)

**Files:** Create `src/crocodile/client/export.py`; Test `tests/client/test_export.py`.
`.export(channel, symbols, frm, to, fmt, dest)` for `fmt ∈ {parquet, csv, arrow, json, jsonl}`. **Acceptance:** each format writes a non-empty file that re-reads to the same row count; JSONL has one record per line.

### Task 3.4: live collect orchestrator

**Files:** Create `src/crocodile/client/collect.py`; Test `tests/client/test_collect.py`.
`collect(connectors, sink)` runs N connectors concurrently in an `asyncio.TaskGroup`, each supervised; SIGINT → graceful `sink.close()`. **Acceptance (with FakeTransport):** two fake connectors drain into one ParquetSink; files appear; no unhandled exception when one connector raises (isolated).

### Task 3.5: Typer CLI + M3 gate

**Files:** Create `src/crocodile/cli.py`; Test `tests/test_cli.py` (Typer `CliRunner`).
Commands: `collect --exchange --symbols --channels --data-dir`; `replay --channel --symbols --from --to`; `export --fmt --dest …`; `query "SQL"`; `catalog` (list channels/symbols/row counts). **Acceptance:** `CliRunner` invokes `query` and `catalog` against a fixture data dir and returns exit code 0 with expected output. Run full suite + lint + types. **M3 GATE.** Commit `feat(client,cli): client + CLI (M3 complete)`.

---

## M4 — Backfill, Gap-Detect Wiring, More Exchanges

### Task 4.1: REST backfill for Deribit (trades, funding, klines)
`src/crocodile/exchanges/deribit/backfill.py` + tests (parse saved REST fixtures; paginate by walking `end_timestamp`; map `interest_8h`→`funding_rate`). Appendix §3.1.

### Task 4.2: REST backfill for Binance (aggTrades, klines, openInterest)
`src/crocodile/exchanges/binance/backfill.py` + tests (paginate by `fromId`; `/klines`→OHLCV; `/openInterest`→OpenInterest). Appendix §3.2.

### Task 4.3: Gap-detect → backfill bridge
Wire `OrderBookSync.RESYNC` and trade-sequence gaps to trigger `resync_book`/`backfill`; buffer live deltas during resync and apply after snapshot (drop `seq < snapshot.seq`). Appendix §6. Test the race with a scripted sequence.

### Task 4.4: Bybit V5 connector
`src/crocodile/exchanges/bybit/` — `publicTrade.{sym}`, `orderbook.{depth}.{sym}` (snapshot+delta), `tickers.{sym}` (+greeks for options), funding/OI/liq via REST. Lowercase `side`. Appendix §7. Golden tests per channel.

### Task 4.5: OKX V5 connector
`src/crocodile/exchanges/okx/` — `trades`, `books`/`bbo-tbt` (**snapshots; maintain L2 locally**), `tickers`, `funding-rate`, `open-interest`, `liq-orders`, `option-summary`. Region endpoint config. Compound `instId` parser. Appendix §7. Golden tests.

### Task 4.6: Coinbase Advanced Trade connector + M4 gate
`src/crocodile/exchanges/coinbase/` — `matches`, `level2` (snapshot+incremental), `ticker`; `product_id` canonical, cache `/products`. Spot only (no funding/OI/liq). Appendix §7. Then assert connector package coverage ≥90% (`uv run pytest --cov=crocodile.exchanges --cov-report=term-missing`). **M4 GATE.** Commit per connector.

---

## M5 — Resampling, Completeness, Docs

### Task 5.1: Resample OHLCV from trades (any interval)
`src/crocodile/resample/ohlcv.py` — DuckDB `time_bucket` over `trade` → OHLCV with buy/sell volume + num_trades; forward-fill empty bars optional. Appendix §4/§5. Test 1s/1m/1h against a fixture; assert `sum(volume)` equals sum of trade amounts.

### Task 5.2: Resample book snapshots at interval
`src/crocodile/resample/book.py` — reconstruct book via M2 engine, emit `BookSnapshot` at fixed wall-clock intervals (e.g. every 1s) with top-N depth. Test against `book.json`.

### Task 5.3: VWAP + derived metrics
`src/crocodile/resample/metrics.py` — VWAP, trade-count, dollar-volume per interval. Tests.

### Task 5.4: Options/derivative completeness pass
Ensure every connector emits `open_interest` (REST poll where no WS), `funding` (settlement cadence), and full options greeks where available; add missing golden tests. Cross-check against appendix §1 field table per exchange.

### Task 5.5: README quickstart + runnable examples + M5 gate
`examples/collect_deribit.py`, `examples/replay_to_csv.py`, `examples/query_ohlcv.py`; expand README with install + collect + replay + export + query walkthrough. Run full suite + lint + types + coverage. **M5 GATE — core "advanced enough"; next spec (analytics) begins.**

---

## Self-Review (completed by plan author)

- **Spec coverage:** schema (T1.1), instruments (T1.2), connector ABC + runtime (T1.4/T1.12), Deribit incl options (T1.5–1.8), Binance 3 venues incl the exact sync algorithm (T1.9–1.11), storage Parquet+DuckDB (T2.1–2.3), replay + book reconstruction (T2.4–2.5), client+CLI+export (T3.x), backfill+gap-detect+≥5 exchanges (T4.x), resample/all-resolutions + completeness + docs (T5.x). Every spec §5–§11 item maps to a task. ✅
- **Placeholder scan:** M1 tasks carry full code; M2–M5 carry concrete acceptance tests + appendix section refs (the appendix holds the exact SQL/algorithms — not placeholders). ✅
- **Type consistency:** record field names, `Side`/`OptType`/`Channel` enums, `Sink.put/flush`, `Connector.normalize/list_instruments`, `OrderBookSync.feed/SyncResult`, `Catalog.scan/query`, `CrocodileClient.{query,scan,replay,export}` are used consistently across tasks. ✅
- **Highest-risk task flagged:** T1.10 (Binance spot≠futures order-book sync) — exact rules pinned with both-venue tests. ✅
