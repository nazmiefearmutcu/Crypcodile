"""Deprecated: moved to crocodile.core.connector."""

# Re-exported (redundant alias, deliberately): tests reach through this module
# for ``base.asyncio.sleep`` when they patch out the reconnect backoff.
import asyncio as asyncio
import warnings as _warnings

from crocodile.core.connector import (  # noqa: F401
    Connector,
    ConnectorError,
    FatalConnectorError,
    SinkError,
    TransientConnectorError,
    backoff_delays,
    http_get_helper,
    log,
)

_warnings.warn(
    "crypcodile.exchanges.base moved to crocodile.core.connector",
    DeprecationWarning,
    stacklevel=2,
)
