from collections import deque

import msgspec


class DeadLetter(msgspec.Struct, frozen=True):
    raw: bytes
    error_type: str
    traceback: str


class DeadLetterQueue:
    def __init__(self, max_size: int = 10_000) -> None:
        self._dq: deque[DeadLetter] = deque(maxlen=max_size)

    async def put(self, raw: bytes, error_type: str, traceback: str) -> None:
        self._dq.append(DeadLetter(raw=raw, error_type=error_type, traceback=traceback))

    def drain(self) -> list[DeadLetter]:
        items = list(self._dq)
        self._dq.clear()
        return items
