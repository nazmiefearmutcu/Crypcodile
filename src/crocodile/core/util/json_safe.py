"""JSON-safe encoding helpers for REST and MCP response boundaries.

Starlette/FastAPI ``JSONResponse`` and JSON-RPC tool results reject
non-finite floats (``ValueError: Out of range float values are not JSON
compliant``). Pure analytics and lake DataFrame rows may yield ``inf`` /
``nan`` (zero-debt health factors, undefined correlations, ratio edge
cases). Boundaries map those to JSON ``null`` via these helpers.
"""

from __future__ import annotations

import math
from typing import Any


def json_safe_float(value: float) -> float | None:
    """Return a finite float, or ``None`` when *value* is NaN/±Inf.

    Accepts any value coercible via ``float()``. Callers at HTTP/MCP
    boundaries should use this (or :func:`json_safe_records`) so responses
    always encode.
    """
    f = float(value)
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def json_safe_records(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sanitize non-finite floats in row dicts for JSON encoding.

    Walks each row's values; NaN/±Inf floats become ``None`` (JSON null).
    Non-float values (ints, strs, None, bools) pass through unchanged.
    """
    out: list[dict[str, Any]] = []
    for row in rows:
        cleaned: dict[str, Any] = {}
        for key, value in row.items():
            if isinstance(value, float):
                cleaned[key] = json_safe_float(value)
            else:
                cleaned[key] = value
        out.append(cleaned)
    return out
