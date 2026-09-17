"""Tests for the worktree-aware `--db-path`/`--data-dir` defaults.

Real git repos, not mocks: what makes these defaults right is
``git rev-parse --git-common-dir`` behaving the way a real worktree makes it
behave, and a mock of that command would just restate the code under test.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from association.cli.paths import DATA_DIRNAME, DB_FILENAME, default_data_dir, default_db_path


def _init_repo(root: Path) -> None:
    """A minimal real git repo at ``root``, with one commit."""
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "t@example.com"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "t"], check=True)
    (root / "a.txt").write_text("x")
    subprocess.run(["git", "add", "a.txt"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)


def test_the_local_copy_wins_when_one_exists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A checkout that already has its own warehouse is unaffected - no git call changes its answer."""
    _init_repo(tmp_path)
    (tmp_path / DB_FILENAME).write_text("")
    monkeypatch.chdir(tmp_path)
    assert default_db_path() == DB_FILENAME


def test_a_worktree_with_no_warehouse_gets_the_main_checkouts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The failure this exists to fix: a worktree has no `nba.duckdb` of its
    own, and used to fail with "database does not exist" rather than reading
    the main checkout's."""
    main = tmp_path / "main"
    main.mkdir()
    _init_repo(main)
    (main / DB_FILENAME).write_text("")
    worktree = tmp_path / "wt"
    subprocess.run(["git", "worktree", "add", "-q", "-b", "wt-branch", str(worktree)], cwd=main, check=True)
    monkeypatch.chdir(worktree)
    assert default_db_path() == str(main / DB_FILENAME)


def test_data_dir_resolves_the_same_way(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`--data-dir` follows the same rule as `--db-path`, for a directory rather than a file."""
    main = tmp_path / "main"
    main.mkdir()
    _init_repo(main)
    (main / DATA_DIRNAME).mkdir(parents=True)
    worktree = tmp_path / "wt"
    subprocess.run(["git", "worktree", "add", "-q", "-b", "wt-branch-2", str(worktree)], cwd=main, check=True)
    monkeypatch.chdir(worktree)
    assert default_data_dir() == str(main / DATA_DIRNAME)


def test_the_literal_default_survives_when_neither_copy_exists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A worktree with no warehouse anywhere - not even in the main checkout -
    gets the original literal default back, the same "database does not
    exist" a plain clone always gave."""
    main = tmp_path / "main"
    main.mkdir()
    _init_repo(main)
    worktree = tmp_path / "wt"
    subprocess.run(["git", "worktree", "add", "-q", "-b", "wt-branch-3", str(worktree)], cwd=main, check=True)
    monkeypatch.chdir(worktree)
    assert default_db_path() == DB_FILENAME
    assert default_data_dir() == DATA_DIRNAME


def test_the_literal_default_survives_outside_any_git_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Not every caller runs inside a git checkout at all - git failing outright is not an error here."""
    monkeypatch.chdir(tmp_path)
    assert default_db_path() == DB_FILENAME
    assert default_data_dir() == DATA_DIRNAME
