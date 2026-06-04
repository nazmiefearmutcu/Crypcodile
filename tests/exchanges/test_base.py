import pytest

from crocodile.exchanges.base import backoff_delays
from crocodile.ingest.deadletter import DeadLetterQueue


def test_backoff_is_bounded_and_jittered():
    delays = [backoff_delays(i, base=1.0, cap=30.0, jitter=0.0) for i in range(10)]
    assert delays[0] == 1.0
    assert delays[1] == 2.0
    assert max(delays) <= 30.0
    assert delays[-1] == 30.0  # capped
    # jitter=0.25, rand=1.0: attempt 4 -> raw = min(30, 1*(2**4)) = 16; * (1+0.25*1.0) = 20.0
    assert backoff_delays(4, jitter=0.25, rand=1.0) == pytest.approx(20.0)


async def test_dead_letter_bounded():
    dlq = DeadLetterQueue(max_size=2)
    await dlq.put(1_000_000, b"a", "parse", "trace")
    await dlq.put(2_000_000, b"b", "parse", "trace")
    await dlq.put(3_000_000, b"c", "parse", "trace")  # evicts oldest
    items = dlq.drain()
    assert len(items) == 2
    assert items[-1].raw == b"c"
    assert items[-1].local_ts == 3_000_000
