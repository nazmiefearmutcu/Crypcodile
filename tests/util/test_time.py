from crocodile.util.time import ms_to_ns, now_ns, us_to_ns


def test_ms_to_ns():
    assert ms_to_ns(1_700_000_000_000) == 1_700_000_000_000_000_000


def test_us_to_ns():
    assert us_to_ns(1_700_000_000_000_000) == 1_700_000_000_000_000_000


def test_now_ns_monotonic_realtime():
    a = now_ns()
    b = now_ns()
    assert b >= a and a > 1_700_000_000_000_000_000
