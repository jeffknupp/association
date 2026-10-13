"""The suite's environment holds no git variable (``tests/conftest.py``,
:func:`conftest.without_git_variables`): a ``GIT_DIR`` exported by
``git rebase --exec`` once redirected the tests' scratch repositories into
the real one (2026-10-12)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from conftest import without_git_variables


def test_no_git_variable_reaches_a_test() -> None:
    """Whatever the suite was started with, a test sees no ``GIT_*``."""
    assert [name for name in os.environ if name.startswith("GIT_")] == []


def test_the_git_variables_are_removed_and_nothing_else() -> None:
    environ = {"GIT_DIR": "/elsewhere", "GIT_WORK_TREE": "/w", "GIT_INDEX_FILE": "/i", "GITHUB_SHA": "abc", "PATH": "/bin", "HOME": "/h"}
    assert without_git_variables(environ) == ["GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE"]
    assert environ == {"GITHUB_SHA": "abc", "PATH": "/bin", "HOME": "/h"}


def test_a_scratch_repository_is_written_where_it_stands_and_nowhere_else(tmp_path: Path) -> None:
    """The accident itself, against a decoy: with ``GIT_DIR`` naming another
    directory, ``git init`` and ``git config`` write there, not into the
    scratch repository; with the environment cleared they write the scratch
    repository and leave the decoy empty."""
    decoy, repo = tmp_path / "decoy", tmp_path / "repo"
    decoy.mkdir()
    repo.mkdir()
    redirected = {**os.environ, "GIT_DIR": str(decoy)}
    without_git_variables(redirected)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, env=redirected)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True, env=redirected)
    assert list(decoy.iterdir()) == []
    assert (repo / ".git" / "config").read_text().count("name = Test") == 1
