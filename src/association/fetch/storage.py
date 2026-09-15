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


def _aligned(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every row widened to the union of all the rows' keys, missing ones None.

    ``pa.Table.from_pylist`` takes its schema from the FIRST row and silently
    drops every key no later row can add. Measured: writing
    ``[{"season": 2014}, {"season": 2016, "points": 299}]`` produces a file with
    no ``points`` column at all, while the same two rows in the other order keep
    it. Nothing raises, and the loss is permanent - the value is gone from disk,
    not merely unreadable.

    That is not a hypothetical shape here. ESPN's career endpoint omits a season
    from its ``totals`` category when the player scored nothing, so a career
    whose EARLIEST line is a scoreless one-game stint parses to a 26-key first
    row followed by 51-key ones. Five files on disk were written that way
    (Seth Curry's among them, whose 2014 opens with a single game for Charlotte),
    costing every one of those players all 25 totals columns - which is why a
    career points leaderboard dropped them entirely.

    Homogeneous rows are returned untouched, which is the overwhelmingly common
    case and keeps this free: a full pull writes ~218,000 files and almost none
    of them is ragged.

    .. versionadded:: 2.2.0
    """
    columns: dict[str, None] = {}
    for row in rows:
        columns.update(dict.fromkeys(row))
    if all(len(row) == len(columns) for row in rows):
        return rows
    return [{name: row.get(name) for name in columns} for row in rows]


def write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    """Write rows to Parquet atomically. An empty list writes nothing, so a
    legitimately empty result never creates a file that looks like a checkpoint.

    Rows need not share a key set: they are aligned to the union of their keys
    first, because Arrow would otherwise infer the schema from the first row
    alone and drop the rest. See :func:`_aligned`.

    .. versionchanged:: 2.2.0
       Ragged rows keep every key. Before this, a row narrower than its
       successors truncated the whole file to its own columns, silently.
    """
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pylist(_aligned(rows))
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
