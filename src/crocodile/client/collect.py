"""Live collect orchestrator (Task 3.4).

``collect(connectors, sink)`` runs N connectors concurrently using
``asyncio.TaskGroup``, each fully supervised (reconnect logic lives inside
``Connector.run()``).  SIGINT / ``CancelledError`` → graceful ``sink.close()``.

Each connector task is individually isolated: an unhandled exception from one
connector is caught, logged, and does **not** cancel the other tasks.  This is
achieved by wrapping each ``connector.run()`` call in a shielded helper that
swallows non-cancellation exceptions.

Design notes
------------
- ``asyncio.TaskGroup`` is used (Python 3.11+) for structured concurrency.
- Cancellation propagation: when the outer task is cancelled (e.g. SIGINT),
  ``TaskGroup.__aexit__`` cancels all child tasks.  We catch ``CancelledError``
  in the outer ``finally`` block to ensure ``sink.close()`` is called before
  re-raising.
- Connector isolation: each task wraps ``connector.run(max_reconnects=…)`` in a
  ``try/except Exception`` so that a crash in one connector is only logged, not
  propagated to the group.  ``CancelledError`` is **not** caught so that
  external cancellation still terminates everything.
- ``max_reconnects`` (default ``-1``, unlimited) is forwarded to every
  ``Connector.run()`` call.  For live collection the built-in backoff loop
  should remain active; individual permanent failures are still isolated by the
  ``except Exception`` wrapper.  Pass ``max_reconnects=0`` in tests where the
  transport exhausts naturally and you do not want reconnect delays.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence

from crocodile.exchanges.base import Connector
from crocodile.sink.base import Sink

log = logging.getLogger(__name__)


async def _run_isolated(connector: Connector, max_reconnects: int) -> None:
    """Run a connector, catching and logging any non-cancellation exception."""
    try:
        await connector.run(max_reconnects=max_reconnects)
    except asyncio.CancelledError:
        raise  # must propagate so TaskGroup can cancel the group
    except Exception as exc:
        log.error(
            "Connector %r raised an unhandled exception (isolated): %s",
            getattr(connector, "name", repr(connector)),
            exc,
            exc_info=True,
        )


async def collect(
    connectors: Sequence[Connector],
    sink: Sink,
    *,
    max_reconnects: int = -1,
) -> None:
    """Run *connectors* concurrently, writing all emitted Records into *sink*.

    Args:
        connectors:     Sequence of configured ``Connector`` instances.  Each
                        must have a ``transport`` set before calling this
                        function (or rely on its own ``ws_url`` for live
                        connections).
        sink:           Destination sink.  ``sink.close()`` is called when all
                        connectors finish **or** on cancellation (e.g. SIGINT).
        max_reconnects: Forwarded to every ``Connector.run()`` call.
                        ``-1`` (default) = unlimited reconnects — correct for
                        live collection.  ``0`` = no reconnect; the connector
                        raises on the first failure, which is then isolated and
                        logged (useful when transports exhaust naturally in
                        tests).

    Behaviour on errors:
        If a connector raises an exception it is logged and the remaining
        connectors continue uninterrupted.  ``CancelledError`` is always
        re-raised (so SIGINT / ``task.cancel()`` still terminates the run).

    Behaviour on cancellation:
        The ``CancelledError`` from the outer task is re-raised after
        ``sink.close()`` completes, giving callers the normal asyncio
        cancellation semantics.
    """
    if not connectors:
        await sink.close()
        return

    _cancelled = False
    try:
        async with asyncio.TaskGroup() as tg:
            for connector in connectors:
                tg.create_task(_run_isolated(connector, max_reconnects))
    except* asyncio.CancelledError:
        _cancelled = True
    except* Exception as eg:
        # All non-cancelled exceptions have already been swallowed inside
        # _run_isolated; this branch is a safety net that should never trigger
        # in normal operation.
        for exc in eg.exceptions:
            log.error("Unexpected group-level exception from collect(): %s", exc, exc_info=True)
    finally:
        await sink.close()

    if _cancelled:
        raise asyncio.CancelledError()
