"""Default warehouse and data-dir paths that work from a git worktree, not
only the main checkout.

``association data pull``/``load`` and the audit scripts under ``scripts/``
all defaulted ``--db-path``/``--data-dir`` to the literal ``./nba.duckdb`` and
``./data/parquet``. Both are gitignored build artifacts that live beside the
main checkout, not files a worktree carries a copy of, so a fresh worktree
failed every one of them with "database does not exist" - nine call sites
(``cli.py``, and ``check_routing``, ``check_coverage``, ``check_nicknames``,
``check_net_points_games``, ``check_team_box``, ``backfill_season_totals``,
``backfill_missing_playoffs`` and ``backfill_power_index`` under ``scripts/``)
each hardcoding the same wrong default independently.

``git rev-parse --git-common-dir`` answers "where is the real ``.git``" from
inside any worktree, main checkout included - a worktree's own ``.git`` is a
file pointing at a subdirectory of the common one, and that subdirectory's
parent is the main checkout's root. Falling back to the literal default when
git is unavailable, or the resolved path does not exist, keeps the old
behavior (and its error messages) everywhere this was never broken - a plain
clone, a CI checkout, a machine with no warehouse built yet at all.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

DB_FILENAME = "nba.duckdb"
"""The warehouse file's name, relative to a checkout's root."""

DATA_DIRNAME = "data/parquet"
"""The pulled Parquet tree's path, relative to a checkout's root."""


def _main_checkout_root(start: Path) -> Path | None:
    """The root of the main checkout sharing ``start``'s ``.git``, or
    ``None`` when ``start`` is not inside a git worktree at all, or git
    cannot be run."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=start,
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    common_dir = Path(result.stdout.strip())
    if not common_dir.is_absolute():
        common_dir = (start / common_dir).resolve()
    return common_dir.parent


def _resolve(name: str) -> str:
    """``name`` under the current directory if it is there, else under the
    main checkout if a worktree has one, else the plain relative name (the
    original, possibly-wrong-but-unchanged default)."""
    cwd = Path.cwd()
    if (cwd / name).exists():
        return name
    root = _main_checkout_root(cwd)
    if root is not None and root != cwd and (root / name).exists():
        return str(root / name)
    return name


def default_db_path() -> str:
    """``./nba.duckdb`` where that exists, else the main checkout's copy,
    else the literal default unchanged.

    .. versionadded:: 2.2.0
    """
    return _resolve(DB_FILENAME)


def default_data_dir() -> str:
    """``./data/parquet`` where that exists, else the main checkout's copy,
    else the literal default unchanged.

    .. versionadded:: 2.2.0
    """
    return _resolve(DATA_DIRNAME)
