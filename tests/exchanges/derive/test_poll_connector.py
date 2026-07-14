"""Tests for DerivePollConnector — poll adapter that writes OptionsChain to sink.

No live network: DeriveConnector.connect / fetch_options_chain are mocked.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from crypcodile.exchanges.derive.connector import (
    DerivePollConnector,
    _underlying_from_symbol,
)
from crypcodile.instruments.registry import InstrumentRegistry, Kind
from crypcodile.schema.enums import OptType
from crypcodile.schema.records import OptionsChain
from crypcodile.sink.memory import MemorySink


def _sample_chain(underlying: str = "BTC") -> OptionsChain:
    return OptionsChain(
        exchange="derive",
        symbol=f"{underlying}_{underlying}-250101-60000-C",
        symbol_raw=f"{underlying}-250101-60000-C",
        exchange_ts=1_700_000_000_000,
        local_ts=1_700_000_000_000,
        underlying=underlying,
        underlying_price=60000.0,
        strike=60000.0,
        expiry=1_735_689_600,
        opt_type=OptType.CALL,
        mark_price=1500.0,
        mark_iv=0.5,
    )


def _make_poll(
    symbols: list[str] | None = None,
    channels: list[str] | None = None,
    **kw: object,
) -> DerivePollConnector:
    defaults: dict[str, object] = {
        "symbols": symbols or ["BTC"],
        "channels": channels or ["options_chain"],
        "out": MemorySink(),
        "registry": InstrumentRegistry(),
        "rpc_url": "http://dummy-rpc.example",
        "poll_interval": 0.01,
    }
    defaults.update(kw)
    return DerivePollConnector(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# kwargs / construction
# ---------------------------------------------------------------------------


def test_constructor_stores_rpc_and_viewer_kwargs() -> None:
    conn = _make_poll(
        viewer_address="0x2222222222222222222222222222222222222222",
        underlying_price=42000.0,
        poll_interval=5.0,
    )
    assert conn.rpc_url == "http://dummy-rpc.example"
    assert conn.viewer_address == "0x2222222222222222222222222222222222222222"
    assert conn.underlying_price == 42000.0
    assert conn.poll_interval == 5.0
    assert conn.client.rpc_url == conn.rpc_url
    assert conn.client.viewer_address == conn.viewer_address
    assert conn.transport is None
    assert conn.name == "derive"


def test_underlying_from_symbol_variants() -> None:
    assert _underlying_from_symbol("BTC") == "BTC"
    assert _underlying_from_symbol("eth") == "ETH"
    assert _underlying_from_symbol("BTC-USD") == "BTC"
    assert _underlying_from_symbol("DERIVE:ETH") == "ETH"


# ---------------------------------------------------------------------------
# list_instruments
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_instruments() -> None:
    conn = _make_poll(symbols=["BTC", "ETH-USD"])
    insts = await conn.list_instruments()
    assert len(insts) == 2
    assert insts[0].exchange == "derive"
    assert insts[0].kind == Kind.OPTION
    assert insts[0].base == "BTC"
    assert insts[0].quote == "USD"
    assert insts[1].base == "ETH"
    assert insts[1].canonical == "ETH-USD"


# ---------------------------------------------------------------------------
# poll → sink
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_poll_once_puts_options_chain_into_sink() -> None:
    sink = MemorySink()
    conn = DerivePollConnector(
        symbols=["BTC"],
        channels=["options_chain"],
        out=sink,
        registry=InstrumentRegistry(),
        rpc_url="http://dummy-rpc.example",
        poll_interval=60.0,
        underlying_price=60000.0,
    )
    chain = _sample_chain("BTC")
    conn.client.connect = MagicMock()  # type: ignore[method-assign]
    conn.client.fetch_options_chain = MagicMock(return_value=[chain])  # type: ignore[method-assign]
    # Pretend already connected so connect() is not required for w3 check path
    # (connect is still called when w3 is None).
    conn.client.w3 = None
    conn.client.viewer_contract = None

    n = await conn._poll_once()

    assert n == 1
    assert len(sink.records) == 1
    rec = sink.records[0]
    assert isinstance(rec, OptionsChain)
    assert rec.exchange == "derive"
    assert rec.underlying == "BTC"
    conn.client.connect.assert_called_once()
    conn.client.fetch_options_chain.assert_called_once_with(
        "BTC",
        60000.0,
        None,
        0.0,
    )


@pytest.mark.asyncio
async def test_run_loop_writes_then_cancels() -> None:
    """run() should poll at least once; cancel stops the loop cleanly."""
    sink = MemorySink()
    conn = DerivePollConnector(
        symbols=["BTC", "ETH"],
        channels=["options_chain"],
        out=sink,
        registry=InstrumentRegistry(),
        rpc_url="http://dummy-rpc.example",
        poll_interval=0.05,
    )
    btc = _sample_chain("BTC")
    eth = _sample_chain("ETH")

    def _fetch(underlying: str, *args: object, **kwargs: object) -> list[OptionsChain]:
        if underlying == "BTC":
            return [btc]
        if underlying == "ETH":
            return [eth]
        return []

    conn.client.connect = MagicMock()  # type: ignore[method-assign]
    conn.client.fetch_options_chain = MagicMock(side_effect=_fetch)  # type: ignore[method-assign]

    task = asyncio.create_task(conn.run(max_reconnects=-1))
    # Wait until sink has both underlyings from at least one full cycle.
    for _ in range(50):
        if len(sink.records) >= 2:
            break
        await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert len(sink.records) >= 2
    underlyings = {r.underlying for r in sink.records if isinstance(r, OptionsChain)}
    assert "BTC" in underlyings
    assert "ETH" in underlyings


@pytest.mark.asyncio
async def test_run_max_reconnects_zero_raises_on_first_error() -> None:
    conn = _make_poll()
    conn.client.connect = MagicMock(side_effect=RuntimeError("rpc down"))  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="rpc down"):
        await conn.run(max_reconnects=0)


def test_normalize_is_empty() -> None:
    conn = _make_poll()
    assert list(conn.normalize({"anything": True}, local_ts=1)) == []


def test_subscribe_channels_defaults() -> None:
    conn = _make_poll(channels=["options_chain"])
    assert conn.subscribe_channels() == ["options_chain"]
