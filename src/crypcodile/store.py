"""Deprecated: moved to crocodile.core.store."""

import sys as _sys
import warnings as _warnings

from crocodile.core.store import catalog, compactor, parquet_sink, rows  # noqa: F401
from crocodile.core.store.catalog import (  # noqa: F401
    Catalog,
    _ns_range_to_dates,
    _ns_to_date,
)
from crocodile.core.store.compactor import ParquetCompactor  # noqa: F401
from crocodile.core.store.parquet_sink import (  # noqa: F401
    ParquetSink,
    _channel_schema,
    _sanitize_path_segment,
)
from crocodile.core.store.rows import (  # noqa: F401
    _coerce_levels_from_row,
    from_row,
    to_row,
)

# See the note in crypcodile/sink.py: the sys.modules aliases are what keep
# ``from crypcodile.store.catalog import Catalog`` and
# ``patch("crypcodile.store.catalog.Catalog")`` working now that the package
# directory is gone.
for _alias, _module in (
    ("catalog", catalog),
    ("compactor", compactor),
    ("parquet_sink", parquet_sink),
    ("rows", rows),
):
    _sys.modules[f"{__name__}.{_alias}"] = _module

__all__ = [
    "Catalog",
    "ParquetCompactor",
    "ParquetSink",
    "_channel_schema",
    "_coerce_levels_from_row",
    "_ns_range_to_dates",
    "_ns_to_date",
    "_sanitize_path_segment",
    "catalog",
    "compactor",
    "from_row",
    "parquet_sink",
    "rows",
    "to_row",
]

_warnings.warn(
    "crypcodile.store moved to crocodile.core.store",
    DeprecationWarning,
    stacklevel=2,
)
