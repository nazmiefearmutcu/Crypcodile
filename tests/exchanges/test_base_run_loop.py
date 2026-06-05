"""Tests for Connector.run() loop paths — DLQ, normalize error, reconnect.

These tests drive the run-loop through fake Transport instances so that we
cover:
 - lines 70  : subscribe_channels NotImplementedError
 - line  92  : backfill NotImplementedError
 - line  109 : run() with no transport set
 - lines 118-122: unparseable frame → DLQ path
 - lines 127-130: normalize raises → DLQ path
 - lines 139-142: reconnect backoff path
"""

from __future__ import annotations

import json
from collections.abc import Iterable

import pytest

from crocodile.exchanges.base import Connector
from crocodile.ingest.transport import FakeTransport
from crocodile.instruments.registry import Instrument, InstrumentRegistry
from crocodile.schema.records import Record
from crocodile.sink.memory import MemorySink

# ---------------------------------------------------------------------------
# Minimal concrete connector for testing — doesn't touch the network
# ---------------------------------------------------------------------------


class _FakeConnector(Connector):
    """Minimal Connector subclass for testing the run loop."""

    name = "fake"
    ws_url = "wss://fake"
    rest_url = "http://fake"

    def __init__(
        self,
        out: MemorySink,
        normalize_raises: bool = False,
    ) -> None:
        super().__init__(
            symbols=["X"],
            channels=["trade"],
            out=out,
            registry=InstrumentRegistry(),
        )
        self._normalize_raises = normalize_raises

    def normalize(self, msg: object, local_ts: int) -> Iterable[Record]:
        if self._normalize_raises:
            raise ValueError("normalize exploded")
        return iter([])  # no records emitted

    async def list_instruments(self) -> list[Instrument]:
        return []

    async def _subscribe(self, transport) -> None:
        pass  # no-op


# ---------------------------------------------------------------------------
# subscribe_channels — base class raises NotImplementedError
# ---------------------------------------------------------------------------


def test_subscribe_channels_not_implemented_raises() -> None:
    """Connector.subscribe_channels() raises NotImplementedError on the base class."""
    conn = _FakeConnector(out=MemorySink())
    # _FakeConnector does not override subscribe_channels, so it delegates to base
    with pytest.raises(NotImplementedError):
        conn.subscribe_channels()


# ---------------------------------------------------------------------------
# backfill — base class raises NotImplementedError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backfill_not_implemented_raises() -> None:
    """Connector.backfill() raises NotImplementedError on the base class."""
    conn = _FakeConnector(out=MemorySink())
    with pytest.raises(NotImplementedError):
        async for _ in conn.backfill("trade", "X", 0, 1):
            pass


# ---------------------------------------------------------------------------
# run() — no transport configured raises RuntimeError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_no_transport_raises() -> None:
    """run() with transport=None raises RuntimeError immediately."""
    conn = _FakeConnector(out=MemorySink())
    # transport is None by default
    with pytest.raises(RuntimeError, match="No transport configured"):
        await conn.run(max_reconnects=0)


# ---------------------------------------------------------------------------
# run() — clean exit (all frames consumed, transport exhausted)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_clean_exit() -> None:
    """run() exits cleanly when transport is exhausted (StopAsyncIteration)."""
    out = MemorySink()
    conn = _FakeConnector(out=out)
    conn.transport = FakeTransport(
        [json.dumps({"type": "trade", "x": 1}).encode()]
    )
    await conn.run(max_reconnects=0)
    # No exception → clean exit; DLQ should be empty
    assert conn._dlq.drain() == []


# ---------------------------------------------------------------------------
# run() — unparseable frame goes to DLQ
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_unparseable_frame_goes_to_dlq() -> None:
    """An unparseable (non-JSON) frame is sent to the DLQ and the loop continues."""
    out = MemorySink()
    conn = _FakeConnector(out=out)
    conn.transport = FakeTransport([b"not-valid-json"])
    await conn.run(max_reconnects=0)
    dlq_items = conn._dlq.drain()
    assert len(dlq_items) == 1
    assert dlq_items[0].raw == b"not-valid-json"
    assert dlq_items[0].error_type == "JSONDecodeError"


# ---------------------------------------------------------------------------
# run() — normalize error goes to DLQ
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_normalize_error_goes_to_dlq() -> None:
    """A normalize() exception is caught, sent to the DLQ, and the loop continues."""
    out = MemorySink()
    conn = _FakeConnector(out=out, normalize_raises=True)
    conn.transport = FakeTransport(
        [json.dumps({"type": "trade"}).encode()]
    )
    await conn.run(max_reconnects=0)
    dlq_items = conn._dlq.drain()
    assert len(dlq_items) == 1
    assert dlq_items[0].error_type == "ValueError"


# ---------------------------------------------------------------------------
# run() — max_reconnects=0 re-raises on transport error
# ---------------------------------------------------------------------------


class _ErrorTransport:
    """Transport that raises on connect (simulates network failure)."""

    async def connect(self) -> None:
        raise ConnectionError("network down")

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        return
        yield  # pragma: no cover  — never reached

    async def send(self, data: bytes) -> None:
        pass  # pragma: no cover

    async def close(self) -> None:
        pass


@pytest.mark.asyncio
async def test_run_max_reconnects_zero_reraises() -> None:
    """With max_reconnects=0, the first connection error is re-raised."""
    out = MemorySink()
    conn = _FakeConnector(out=out)
    conn.transport = _ErrorTransport()
    with pytest.raises(ConnectionError, match="network down"):
        await conn.run(max_reconnects=0)


# ---------------------------------------------------------------------------
# run() — reconnect happens up to max_reconnects times
# ---------------------------------------------------------------------------


class _FailOnceThenSucceedTransport:
    """Fails on the first connect; succeeds with an empty frame set on the second."""

    def __init__(self) -> None:
        self._attempt = 0

    async def connect(self) -> None:
        self._attempt += 1
        if self._attempt == 1:
            raise ConnectionError("first attempt fails")

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        return
        yield  # pragma: no cover

    async def send(self, data: bytes) -> None:
        pass  # pragma: no cover

    async def close(self) -> None:
        pass


@pytest.mark.asyncio
async def test_run_reconnects_on_error(monkeypatch) -> None:
    """run() retries after a connection error when max_reconnects>0."""
    import crocodile.exchanges.base as base_mod

    # Patch asyncio.sleep to avoid actual delay in tests
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(base_mod.asyncio, "sleep", fake_sleep)

    out = MemorySink()
    conn = _FakeConnector(out=out)
    transport = _FailOnceThenSucceedTransport()
    conn.transport = transport

    # max_reconnects=2: should tolerate 1 failure and succeed on retry
    await conn.run(max_reconnects=2)

    # Sleep was called once (for the reconnect delay)
    assert len(sleeps) == 1
    # Second attempt succeeded (transport._attempt == 2)
    assert transport._attempt == 2
