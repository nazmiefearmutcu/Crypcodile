"""Universal CCXT-backed connector — one class, 100+ exchanges.

This package wraps the `ccxt <https://github.com/ccxt/ccxt>`_ unified API so
that every exchange ccxt supports becomes a first-class Crypcodile venue,
normalising ccxt's unified market-data structures into the same
:mod:`crypcodile.schema.records` types the hand-written native connectors emit.

The pure ``ccxt-dict -> Record`` transforms live in :mod:`.normalize` (no
network, fully unit-testable); the poll/stream loop lives in
:mod:`.connector`.
"""
