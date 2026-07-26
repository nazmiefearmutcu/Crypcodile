"""Scheduled low-frequency pulls, and the calendar they answer to.

The coordinator itself is asset-class agnostic: it schedules, catches up on missed
periods, and backs off on failure. :class:`USMarketCalendar` is the equity-shaped policy
it consults, and is one argument — crypto passes a calendar that never closes, or none.
"""

from crocodile.core.scheduler.calendar import MARKET_TZ, USMarketCalendar
from crocodile.core.scheduler.coordinator import PullTask, ScheduledPullCoordinator
from crocodile.core.scheduler.state import (
    InMemoryStateStore,
    JSONFileStateStore,
    SchedulerStateStore,
    TaskStateRecord,
)

__all__ = [
    "MARKET_TZ",
    "InMemoryStateStore",
    "JSONFileStateStore",
    "PullTask",
    "ScheduledPullCoordinator",
    "SchedulerStateStore",
    "TaskStateRecord",
    "USMarketCalendar",
]
