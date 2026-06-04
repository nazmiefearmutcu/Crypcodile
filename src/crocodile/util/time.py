import time


def ms_to_ns(ms: int | float) -> int:
    return int(ms) * 1_000_000


def us_to_ns(us: int | float) -> int:
    return int(us) * 1_000


def now_ns() -> int:
    """Capture clock for local_ts. Realtime so it's comparable to exchange_ts."""
    return time.clock_gettime_ns(time.CLOCK_REALTIME)
