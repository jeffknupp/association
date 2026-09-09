"""Flat-file (Parquet) storage: one small file per unit of fetch work.

Existence of a file *is* the resumability checkpoint - no separate manifest.
Writes are atomic (write to a .tmp sibling, then os.replace) so a killed
process never leaves a file on disk that looks complete but isn't. The
temporary name is unique per process and thread: with `--workers` above 1, two
threads really do write the same path at the same time (two games sharing a
player both cache that player's bio), and a shared .tmp would have them
interleaving into one file before each replaced it into place.

Completion markers (mark_complete/is_complete) are a second, coarser
checkpoint: a single empty sentinel file meaning "everything in this scope
was already fully fetched, verified against ESPN's schedule - don't even
bother re-deriving that." Checking one is a single stat() call, independent
of how many games/players are in the scope it covers.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq


def exists(path: Path) -> bool:
    """Whether a Parquet file is present AND non-empty - a zero-byte file is
    treated as absent, since that is what an interrupted write leaves behind."""
    return path.exists() and path.stat().st_size > 0


def _tmp_name(path: Path) -> Path:
    """A temporary sibling nobody else is writing to.

    Unique per process and thread. Concurrent writers of the same path are
    expected (see the module docstring); each writes its own complete file and
    the last os.replace wins, which is fine because they are writing the same
    content. A shared name instead lets two of them interleave into one file.

    .. versionadded:: 1.6.0
    """
    return path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")


def write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    """Write rows to Parquet atomically. An empty list writes nothing, so a
    legitimately empty result never creates a file that looks like a checkpoint."""
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pylist(rows)
    tmp_path = _tmp_name(path)
    try:
        pq.write_table(table, tmp_path)
        os.replace(tmp_path, path)
    except BaseException:
        # A killed or failed write must not leave its scratch file behind -
        # unlike the old fixed name, these do not get reused and cleaned up by
        # the next attempt.
        tmp_path.unlink(missing_ok=True)
        raise


def write_row(path: Path, row: dict[str, Any]) -> None:
    """Write a single row, for endpoints that return one record per file."""
    write_rows(path, [row])


def mark_complete(path: Path) -> None:
    """Write an empty sentinel marking a scope (e.g. one season+season_type)
    as fully, verifiably fetched. Atomic like write_rows, for the same reason."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = _tmp_name(path)
    tmp_path.touch()
    os.replace(tmp_path, path)


def is_complete(path: Path) -> bool:
    """O(1): a single stat() call, regardless of how much data the scope covers."""
    return path.exists()
