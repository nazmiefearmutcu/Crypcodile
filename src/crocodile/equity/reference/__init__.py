"""Reference data modules for Stockodile."""

from crocodile.equity.reference.identity import InstrumentIdentity
from crocodile.equity.reference.master import SecurityMaster
from crocodile.equity.reference.models import Security, TickerMapping
from crocodile.equity.reference.registry import InstrumentRegistry

__all__ = [
    "InstrumentIdentity",
    "InstrumentRegistry",
    "Security",
    "SecurityMaster",
    "TickerMapping",
]

# `universe` is deliberately not re-exported here. It reaches three provider packages, and
# `equity.providers.factory` imports `equity.reference.registry` at module scope — so a
# re-export would make importing this package and importing that one two halves of an import
# cycle, resolved or not depending on which of them a caller happened to reach first. Import
# `crocodile.equity.reference.universe` by its own name.
