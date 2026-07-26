"""Rename legacy top-level lake partitions to the unified `source=` key.

Crypcodile wrote `exchange={venue}/`; Stockodile wrote `provider={name}/`. Both
now write `source={name}/`. Because the partition key lives in the directory
name rather than inside the Parquet files, migration is a rename — no data is
read, decoded, or rewritten.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

__all__ = ["LEGACY_PREFIXES", "SOURCE_PREFIX", "SOURCE_PREFIXES", "migrate_lake"]

LEGACY_PREFIXES: Final = ("exchange=", "provider=")
SOURCE_PREFIX: Final = "source="

SOURCE_PREFIXES: Final = (SOURCE_PREFIX, *LEGACY_PREFIXES)
"""Every top-level partition prefix a lake may legitimately be on.

The sink only ever writes ``source=``; readers accept all three for one minor
version so a lake stays queryable before and during its migration. This tuple
is the single place that vocabulary is defined — a reader that hard-codes one
prefix goes blind on a half-migrated lake."""


def migrate_lake(data_dir: Path | str) -> int:
    """Rename legacy partition directories under ``data_dir``.

    Returns:
        How many directories were renamed. Zero means the lake is already
        migrated — or does not exist yet — which makes repeated runs safe.

    Raises:
        FileExistsError: if a target ``source=`` directory already exists, which
            would mean silently merging two partitions.
    """
    root = Path(data_dir)
    if not root.is_dir():
        return 0

    renamed = 0
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        for prefix in LEGACY_PREFIXES:
            if not child.name.startswith(prefix):
                continue
            target = child.with_name(SOURCE_PREFIX + child.name[len(prefix) :])
            if target.exists():
                raise FileExistsError(
                    f"cannot rename {child.name} to {target.name}: target exists. "
                    "Merge them by hand — renaming would silently combine two partitions."
                )
            child.rename(target)
            renamed += 1
            break
    return renamed
