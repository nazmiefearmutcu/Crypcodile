"""Dead-letter queue for unparseable / normalize-failed frames.

Items land here from ``Connector.run()`` so the supervised loop can continue.
On collect stop, callers drain the queue and optionally write a JSON report.
"""

from __future__ import annotations

import json
import logging
from collections import deque
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import msgspec

log = logging.getLogger(__name__)

# Default filename when writing under data_dir without an explicit path.
DEFAULT_DLQ_REPORT_NAME = "dlq_report.json"


class DeadLetter(msgspec.Struct, frozen=True):
    local_ts: int
    raw: bytes
    error_type: str
    traceback: str


class DeadLetterQueue:
    def __init__(self, max_size: int = 10_000) -> None:
        self._dq: deque[DeadLetter] = deque(maxlen=max_size)

    def __len__(self) -> int:
        return len(self._dq)

    async def put(self, local_ts: int, raw: bytes, error_type: str, traceback: str) -> None:
        self._dq.append(
            DeadLetter(local_ts=local_ts, raw=raw, error_type=error_type, traceback=traceback)
        )

    def drain(self) -> list[DeadLetter]:
        """Return all queued items and clear the queue."""
        items = list(self._dq)
        self._dq.clear()
        return items


def dead_letter_to_dict(item: DeadLetter, *, connector: str | None = None) -> dict[str, Any]:
    """Serialize one dead letter for JSON report output."""
    out: dict[str, Any] = {
        "local_ts": item.local_ts,
        "raw": item.raw.decode("utf-8", errors="replace"),
        "error_type": item.error_type,
        "traceback": item.traceback,
    }
    if connector is not None:
        out["connector"] = connector
    return out


def build_dlq_report(
    entries: Sequence[tuple[str, DeadLetter]] | Sequence[DeadLetter],
) -> dict[str, Any]:
    """Build a JSON-serializable DLQ report.

    *entries* may be bare ``DeadLetter`` items or ``(connector_name, item)`` pairs.
    """
    items: list[dict[str, Any]] = []
    by_error: dict[str, int] = {}
    for entry in entries:
        if isinstance(entry, DeadLetter):
            d = dead_letter_to_dict(entry)
        else:
            name, letter = entry
            d = dead_letter_to_dict(letter, connector=name)
        items.append(d)
        et = d["error_type"]
        by_error[et] = by_error.get(et, 0) + 1
    return {
        "count": len(items),
        "by_error_type": by_error,
        "items": items,
    }


def write_dlq_report(path: Path | str, report: dict[str, Any]) -> Path:
    """Write *report* as JSON to *path*. Creates parent directories as needed."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return p


def drain_connector_dlqs(
    connectors: Sequence[Any],
) -> list[tuple[str, DeadLetter]]:
    """Drain ``_dlq`` from each connector; return ``(name, item)`` pairs."""
    out: list[tuple[str, DeadLetter]] = []
    for conn in connectors:
        dlq = getattr(conn, "_dlq", None)
        if dlq is None or not hasattr(dlq, "drain"):
            continue
        name = str(getattr(conn, "name", type(conn).__name__))
        for item in dlq.drain():
            out.append((name, item))
    return out


def report_drained_dlqs(
    connectors: Sequence[Any],
    *,
    report_path: Path | str | None = None,
    data_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Drain all connector DLQs; write a JSON report if non-empty.

    Resolution for the report file when *count > 0*:
    1. Explicit *report_path* if given
    2. Else ``{data_dir}/dlq_report.json`` if *data_dir* is given
    3. Else no file is written (summary only via return value / log)

    Returns the report dict (``count == 0`` and empty items when nothing drained).
    """
    entries = drain_connector_dlqs(connectors)
    report = build_dlq_report(entries)
    count = report["count"]
    if count == 0:
        return report

    dest: Path | None = None
    if report_path is not None:
        dest = Path(report_path)
    elif data_dir is not None:
        dest = Path(data_dir) / DEFAULT_DLQ_REPORT_NAME

    if dest is not None:
        written = write_dlq_report(dest, report)
        report["path"] = str(written)
        log.warning(
            "DLQ drained %d item(s); report written to %s",
            count,
            written,
        )
    else:
        log.warning(
            "DLQ drained %d item(s) (no report path/data_dir; not written to disk)",
            count,
        )
    return report
