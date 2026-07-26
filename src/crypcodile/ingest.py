"""Deprecated: moved to crocodile.core.ingest."""

import sys as _sys
import warnings as _warnings

from crocodile.core.ingest import (  # noqa: F401
    book_sync,
    deadletter,
    gap_bridge,
    rollback_manager,
    sync_recovery,
    transport,
)
from crocodile.core.ingest.book_sync import (  # noqa: F401
    BookSyncMachine,
    SyncResult,
    filter_buffered_book_deltas,
    keep_delta_after_snapshot,
)
from crocodile.core.ingest.deadletter import (  # noqa: F401
    DEFAULT_DLQ_REPORT_NAME,
    DeadLetter,
    DeadLetterQueue,
    build_dlq_report,
    dead_letter_to_dict,
    drain_connector_dlqs,
    report_drained_dlqs,
    write_dlq_report,
)
from crocodile.core.ingest.gap_bridge import BookResyncBridge, TradeSeqGap  # noqa: F401
from crocodile.core.ingest.rollback_manager import RollbackManager  # noqa: F401
from crocodile.core.ingest.sync_recovery import SyncRecovery  # noqa: F401
from crocodile.core.ingest.transport import (  # noqa: F401
    AiohttpWsTransport,
    FakeTransport,
    Transport,
)

# See the note in crypcodile/sink.py for why the sys.modules aliases are needed.
for _alias, _module in (
    ("book_sync", book_sync),
    ("deadletter", deadletter),
    ("gap_bridge", gap_bridge),
    ("rollback_manager", rollback_manager),
    ("sync_recovery", sync_recovery),
    ("transport", transport),
):
    _sys.modules[f"{__name__}.{_alias}"] = _module

__all__ = [
    "DEFAULT_DLQ_REPORT_NAME",
    "AiohttpWsTransport",
    "BookResyncBridge",
    "BookSyncMachine",
    "DeadLetter",
    "DeadLetterQueue",
    "FakeTransport",
    "RollbackManager",
    "SyncRecovery",
    "SyncResult",
    "TradeSeqGap",
    "Transport",
    "book_sync",
    "build_dlq_report",
    "dead_letter_to_dict",
    "deadletter",
    "drain_connector_dlqs",
    "filter_buffered_book_deltas",
    "gap_bridge",
    "keep_delta_after_snapshot",
    "report_drained_dlqs",
    "rollback_manager",
    "sync_recovery",
    "transport",
    "write_dlq_report",
]

_warnings.warn(
    "crypcodile.ingest moved to crocodile.core.ingest",
    DeprecationWarning,
    stacklevel=2,
)
