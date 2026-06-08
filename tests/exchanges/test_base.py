import pytest

from crypcodile.exchanges.base import backoff_delays
from crypcodile.ingest.deadletter import DeadLetterQueue


def test_backoff_is_bounded_and_jittered():
    delays = [backoff_delays(i, base=1.0, cap=30.0, jitter=0.0) for i in range(10)]
    assert delays[0] == 1.0
    assert delays[1] == 2.0
    assert max(delays) <= 30.0
    assert delays[-1] == 30.0  # capped
    # jitter=0.25, rand=1.0: attempt 4 -> raw = min(30, 1*(2**4)) = 16; * (1+0.25*1.0) = 20.0
    assert backoff_delays(4, jitter=0.25, rand=1.0) == pytest.approx(20.0)


def test_backoff_cap_holds_even_with_jitter():
    """Regression: jitter must NOT push a capped delay above `cap`.

    attempt 5 -> raw = min(30, 2**5=32) = 30. Naive jitter gives 30*(1+0.25*1.0)=37.5,
    overshooting the documented 30s ceiling. The cap is re-applied after jitter, so the
    returned delay stays bounded by `cap` for any rand in [0, 1].
    """
    assert backoff_delays(5, cap=30.0, jitter=0.25, rand=1.0) == pytest.approx(30.0)
    for attempt in range(0, 12):
        for rand in (0.0, 0.5, 0.999, 1.0):
            assert backoff_delays(attempt, cap=30.0, jitter=0.25, rand=rand) <= 30.0


async def test_dead_letter_bounded():
    dlq = DeadLetterQueue(max_size=2)
    await dlq.put(1_000_000, b"a", "parse", "trace")
    await dlq.put(2_000_000, b"b", "parse", "trace")
    await dlq.put(3_000_000, b"c", "parse", "trace")  # evicts oldest
    items = dlq.drain()
    assert len(items) == 2
    assert items[-1].raw == b"c"
    assert items[-1].local_ts == 3_000_000
