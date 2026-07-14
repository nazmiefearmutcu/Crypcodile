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
from pathlib import Path

from crypcodile.exchanges.base import Connector
from crypcodile.ingest.deadletter import report_drained_dlqs
from crypcodile.sink.base import Sink

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
    dlq_report_path: Path | str | None = None,
    data_dir: Path | str | None = None,
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
        dlq_report_path: Optional explicit path for the dead-letter report JSON
                        written on stop when the DLQ is non-empty.
        data_dir:       If set and *dlq_report_path* is not, a non-empty DLQ
                        is written to ``{data_dir}/dlq_report.json``.

    Behaviour on errors:
        If a connector raises an exception it is logged and the remaining
        connectors continue uninterrupted.  ``CancelledError`` is always
        re-raised (so SIGINT / ``task.cancel()`` still terminates the run).

    Behaviour on cancellation:
        The ``CancelledError`` from the outer task is re-raised after
        ``sink.close()`` completes, giving callers the normal asyncio
        cancellation semantics.

    On stop (normal or cancelled), each connector's dead-letter queue is drained.
    If any items were present, a summary is logged and a JSON report may be
    written (see *dlq_report_path* / *data_dir*).
    """
    if not connectors:
        await sink.close()
        return

    # Check if target sink is a ParquetSink and spin up compactor
    compactor = None
    inferred_data_dir: Path | str | None = data_dir
    try:
        from crypcodile.store.parquet_sink import ParquetSink
        from crypcodile.store.compactor import ParquetCompactor
        
        target_sink = sink
        while hasattr(target_sink, "target"):
            target_sink = getattr(target_sink, "target")
            
        if isinstance(target_sink, ParquetSink):
            if inferred_data_dir is None:
                inferred_data_dir = target_sink._data_dir
            compactor = ParquetCompactor(
                data_dir=target_sink._data_dir,
                min_age_seconds=10.0,
                poll_interval=30.0
            )
            compactor.start()
    except Exception as e:
        log.warning("Could not initialize ParquetCompactor service: %s", e)

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
        if compactor:
            try:
                await compactor.stop()
            except Exception as e:
                log.warning("Error stopping ParquetCompactor service: %s", e)
        await sink.close()
        try:
            report_drained_dlqs(
                connectors,
                report_path=dlq_report_path,
                data_dir=inferred_data_dir,
            )
        except Exception as e:
            log.warning("DLQ drain/report failed: %s", e)

    if _cancelled:
        raise asyncio.CancelledError()
