from crocodile.schema.records import Record
from crocodile.sink.base import Sink


class MemorySink(Sink):
    def __init__(self) -> None:
        self.records: list[Record] = []
    async def put(self, record: Record) -> None:
        self.records.append(record)
    async def flush(self) -> None:
        return None
