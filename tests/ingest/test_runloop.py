import pathlib

from crocodile.core.ingest.transport import FakeTransport
from crocodile.core.schema.records import Trade
from crocodile.core.sink.memory import MemorySink
from crocodile.crypto.exchanges.deribit.connector import DeribitConnector
from crocodile.crypto.instruments.registry import InstrumentRegistry

_FIXTURE = (
    pathlib.Path(__file__).parent.parent / "exchanges" / "deribit" / "fixtures" / "trades.json"
)
FIX = _FIXTURE.read_text()


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
