"""The Crocodile exception hierarchy.

One root so callers can catch everything this library raises, and a shallow
tree beneath it so they rarely have to.

This module imports nothing from the rest of the package, so any module may
depend on it without risking a cycle.
"""

from __future__ import annotations

__all__ = [
    "BookGap",
    "CapabilityUnavailable",
    "ConfigError",
    "ConnectorError",
    "CrocodileError",
    "FatalConnectorError",
    "ProvenanceError",
    "SinkError",
    "StoreError",
    "TransientConnectorError",
]


class CrocodileError(Exception):
    """Root of every error this library raises."""


class ConnectorError(CrocodileError):
    """A source-side failure."""


class FatalConnectorError(ConnectorError):
    """Unrecoverable — authentication, configuration. Do not reconnect."""


class TransientConnectorError(ConnectorError):
    """Recoverable — reconnecting with backoff is appropriate."""


class SinkError(CrocodileError):
    """Writing to the output sink failed. Not a bad market-data frame."""


class StoreError(CrocodileError):
    """The lake or its catalog could not satisfy the request."""


class BookGap(StoreError):
    """An order-book sequence discontinuity was detected during reconstruction."""


class ConfigError(CrocodileError):
    """Configuration is missing, malformed, or contradictory."""


class ProvenanceError(CrocodileError):
    """A record's provenance could not be established."""


class CapabilityUnavailable(CrocodileError):
    """A capability has no implementation for this asset class in this deployment.

    This is the raised form of ``Provenance.UNAVAILABLE``. Capabilities that can
    return typed holes should prefer emitting ``UNAVAILABLE`` records; this is for
    the cases where there is nothing to return at all.
    """

    def __init__(self, capability: str, asset_class: str, *, reason: str) -> None:
        self.capability = capability
        self.asset_class = asset_class
        self.reason = reason
        super().__init__(f"capability {capability!r} unavailable for {asset_class}: {reason}")
