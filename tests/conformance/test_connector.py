"""The one Connector ABC: its contract, its backoff, and how it treats failure.

Everything here drives a real ``run()`` over a fake transport. Asserting on the
module's source text instead would pass for the wrong reasons — a comment
mentioning ``FatalConnectorError`` is not a run loop that stops for one.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable

import pytest

from crocodile.core.connector import Connector, backoff_delays
from crocodile.core.errors import ConnectorError, FatalConnectorError, SinkError

# Still under crypcodile until Task 9 relocates the pipeline packages.
from crypcodile.ingest.transport import FakeTransport
from crypcodile.instruments.registry import Instrument, InstrumentRegistry
from crypcodile.sink.base import Sink


def test_backoff_is_capped_after_jitter():
    # the cap is applied twice on purpose: jitter must not lift a capped delay
    # above the documented ceiling during sustained reconnect failures
    assert backoff_delays(attempt=20, base=1.0, cap=30.0, jitter=0.25, rand=1.0) == 30.0


def test_backoff_grows_geometrically():
    assert backoff_delays(0, rand=0.0) == 1.0
    assert backoff_delays(3, rand=0.0) == 8.0


def test_the_three_abstract_methods_are_the_contract():
    assert Connector.__abstractmethods__ == frozenset(
        {"normalize", "list_instruments", "_subscribe"}
    )


@pytest.mark.parametrize("name", ["subscribe_channels", "backfill"])
def test_optional_hooks_are_not_abstract(name: str) -> None:
    assert name not in Connector.__abstractmethods__
    assert hasattr(Connector, name)


def test_the_error_taxonomy_is_reachable_from_the_connector_module():
    assert issubclass(FatalConnectorError, ConnectorError)
    assert issubclass(SinkError, Exception)


# ---------------------------------------------------------------------------
# Fakes: a sink that can fail on demand, transports that fail on connect, and a
# connector whose normalize() does whatever the test needs.
# ---------------------------------------------------------------------------


class _RecordingSink(Sink):
    """Collects records, or raises *fail* on every write."""

    def __init__(self, fail: BaseException | None = None) -> None:
        self.records: list[object] = []
        self._fail = fail

    async def put(self, record: object) -> None:
        if self._fail is not None:
            raise self._fail
        self.records.append(record)

    async def flush(self) -> None:
        return None


class _FailingTransport:
    """Raises *exc* from ``connect()``; heals after *heal_after* attempts.

    Trips an assertion once the connect count runs away, so a run loop that
    ignores a fatal error fails the test in milliseconds instead of hanging
    until the suite timeout.
    """

    def __init__(self, exc: BaseException, *, heal_after: int | None = None) -> None:
        self._exc = exc
        self._heal_after = heal_after
        self.connects = 0

    async def connect(self) -> None:
        self.connects += 1
        if self.connects > 5:
            raise AssertionError("run() kept reconnecting when it should have stopped")
        if self._heal_after is not None and self.connects > self._heal_after:
            return
        raise self._exc

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        return
        yield  # pragma: no cover  — makes this an async generator

    async def send(self, data: bytes) -> None:
        pass

    async def close(self) -> None:
        pass


class _ScriptedConnector(Connector):
    """Concrete Connector whose normalize() is driven by the test."""

    name = "scripted"
    ws_url = "wss://scripted"
    rest_url = "https://scripted"

    def __init__(
        self,
        out: Sink,
        *,
        normalize_raises: BaseException | None = None,
        emits: int = 0,
    ) -> None:
        super().__init__(
            symbols=["X"],
            channels=["trade"],
            out=out,
            registry=InstrumentRegistry(),
        )
        self._normalize_raises = normalize_raises
        self._emits = emits
        self.normalize_calls = 0

    def normalize(self, msg: object, local_ts: int) -> Iterable[object]:
        self.normalize_calls += 1
        if self._normalize_raises is not None:
            raise self._normalize_raises
        return [object() for _ in range(self._emits)]

    async def list_instruments(self) -> list[Instrument]:
        return []

    async def _subscribe(self, transport: object) -> None:
        pass


@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record backoff delays instead of serving them."""
    slept: list[float] = []

    async def fake_sleep(delay: float) -> None:
        slept.append(delay)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    return slept


# ---------------------------------------------------------------------------
# Fatal errors stop; everything else keeps going.
# ---------------------------------------------------------------------------


async def test_a_fatal_error_from_normalize_stops_the_run() -> None:
    conn = _ScriptedConnector(
        _RecordingSink(), normalize_raises=FatalConnectorError("unsupported channel")
    )
    conn.transport = FakeTransport([b'{"n": 1}'] * 5)

    with pytest.raises(FatalConnectorError):
        await conn.run(max_reconnects=-1)

    assert conn.normalize_calls == 1, "it drained the rest of the frames anyway"
    assert conn._dlq.drain() == [], "a fatal error is not a bad frame"


async def test_a_fatal_error_on_connect_does_not_reconnect(no_sleep: list[float]) -> None:
    transport = _FailingTransport(FatalConnectorError("bad credentials"))
    conn = _ScriptedConnector(_RecordingSink())
    conn.transport = transport

    # max_reconnects=-1 is unlimited: only the fatal branch can end this loop.
    with pytest.raises(FatalConnectorError):
        await conn.run(max_reconnects=-1)

    assert transport.connects == 1
    assert no_sleep == [], "it backed off before giving up"


async def test_a_generic_error_on_connect_still_reconnects(no_sleep: list[float]) -> None:
    transport = _FailingTransport(ConnectionError("network down"), heal_after=1)
    conn = _ScriptedConnector(_RecordingSink())
    conn.transport = transport

    await conn.run(max_reconnects=2)

    assert transport.connects == 2
    assert len(no_sleep) == 1


async def test_a_generic_error_from_normalize_is_still_dead_lettered() -> None:
    conn = _ScriptedConnector(_RecordingSink(), normalize_raises=ValueError("bad frame"))
    conn.transport = FakeTransport([b'{"n": 1}', b'{"n": 2}'])

    await conn.run(max_reconnects=0)

    assert [item.error_type for item in conn._dlq.drain()] == ["ValueError", "ValueError"]


async def test_a_sink_failure_is_distinguishable_from_a_bad_frame() -> None:
    conn = _ScriptedConnector(_RecordingSink(fail=OSError("no space left on device")), emits=1)
    conn.transport = FakeTransport([b'{"n": 1}'])

    await conn.run(max_reconnects=0)

    (item,) = conn._dlq.drain()
    assert item.error_type == "SinkError"
