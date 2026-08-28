"""Flat-file (Parquet) storage: one small file per unit of fetch work.

Existence of a file *is* the resumability checkpoint - no separate manifest.
Writes are atomic (write to a .tmp sibling, then os.replace) so a killed
process never leaves a file on disk that looks complete but isn't.

Completion markers (mark_complete/is_complete) are a second, coarser
checkpoint: a single empty sentinel file meaning "everything in this scope
was already fully fetched, verified against ESPN's schedule - don't even
bother re-deriving that." Checking one is a single stat() call, independent
of how many games/players are in the scope it covers.
"""

from __future__ import annotations

import os
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq


def exists(path: Path) -> bool:
    return path.exists() and path.stat().st_size > 0


def write_rows(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pylist(rows)
    tmp_path = path.with_name(path.name + ".tmp")
    pq.write_table(table, tmp_path)
    os.replace(tmp_path, path)


def write_row(path: Path, row: dict) -> None:
    write_rows(path, [row])


def mark_complete(path: Path) -> None:
    """Write an empty sentinel marking a scope (e.g. one season+season_type)
    as fully, verifiably fetched. Atomic like write_rows, for the same reason."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.touch()
    os.replace(tmp_path, path)


def is_complete(path: Path) -> bool:
    """O(1): a single stat() call, regardless of how much data the scope covers."""
    return path.exists()
