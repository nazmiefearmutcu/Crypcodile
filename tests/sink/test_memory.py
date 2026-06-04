from crocodile.schema.enums import Side
from crocodile.schema.records import Trade
from crocodile.sink.memory import MemorySink


async def test_memory_sink_collects():
    s = MemorySink()
    t = Trade(exchange="x", symbol="A", symbol_raw="A", exchange_ts=1, local_ts=2,
              id="1", price=1.0, amount=1.0, side=Side.BUY)
    await s.put(t)
    await s.flush()
    assert s.records == [t]
