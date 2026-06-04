import pathlib

from crocodile.exchanges.deribit.connector import DeribitConnector
from crocodile.ingest.transport import FakeTransport
from crocodile.instruments.registry import InstrumentRegistry
from crocodile.schema.records import Trade
from crocodile.sink.memory import MemorySink

FIX = pathlib.Path("tests/exchanges/deribit/fixtures/trades.json").read_text()


async def test_runloop_drains_transport_into_sink():
    sink = MemorySink()
    conn = DeribitConnector(
        symbols=["BTC-PERPETUAL"],
        channels=["trade"],
        out=sink,
        registry=InstrumentRegistry(),
    )
    conn.transport = FakeTransport(frames=[FIX.encode()])
    await conn.run(max_reconnects=0)  # run until transport exhausts, no reconnect
    assert any(isinstance(r, Trade) for r in sink.records)
