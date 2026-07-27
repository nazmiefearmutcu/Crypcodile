from crocodile.core.schema.enums import AssetClass, Side
from crocodile.core.schema.records import Trade
from crocodile.core.sink.memory import MemorySink


async def test_memory_sink_collects():
    s = MemorySink()
    t = Trade(source="x", symbol="A", symbol_raw="A", source_ts=1, local_ts=2,
    asset_class=AssetClass.CRYPTO,
              id="1", price=1.0, amount=1.0, side=Side.BUY)
    await s.put(t)
    await s.flush()
    assert s.records == [t]
