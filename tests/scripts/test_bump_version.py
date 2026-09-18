"""Tests for the install-pin rewriting `scripts/bump_version.py` added for #60.

These exercise ``find_pinned_files``, ``check_install_pins`` and
``rewrite_install_pins`` directly, against a throwaway git repository built
fresh under ``tmp_path`` for each test - never against this project's own
tree, and never through ``main()``, which also touches ``pyproject.toml``,
``uv.lock`` and git tags in the real repository. ``git grep`` needs a real
git repository to search, so each fixture is a minimal one (``git init`` plus
one commit), not a bare directory of files.

``bump_version.py`` lives under ``scripts/``, not ``src/``, so it is not part
of the installed ``association`` package; it is imported here by file path.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "bump_version.py"


def _load_bump_version() -> ModuleType:
    """Import ``scripts/bump_version.py`` by file path, since it is not a package."""
    spec = importlib.util.spec_from_file_location("bump_version_under_test", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bump_version = _load_bump_version()


def _repo(tmp_path: Path) -> Path:
    """Create an empty git repository under ``tmp_path`` for a test to populate."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    return repo


def _commit_all(repo: Path) -> None:
    """Stage and commit everything currently in ``repo``, so ``git grep`` can search it."""
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=repo, check=True)


def test_find_pinned_files_locates_every_file_with_a_pin(tmp_path: Path) -> None:
    """`git grep` discovery finds a pin in any tracked file, not just a fixed pair.

    This is the shape of the original bug: a hardcoded README.md/installation.rst
    list left docs/usage.rst's pin to rot untouched. ``find_pinned_files`` is a
    broad, literal-substring prefilter, so it also picks up a file that only
    mentions the bare pattern in prose with no version attached (the way
    docs/releasing.rst itself does, describing the ``git grep`` command) - the
    next test shows ``check_install_pins`` is what excludes that one from
    actual rewriting, since it holds no version for ``PIN`` to capture.
    """
    repo = _repo(tmp_path)
    (repo / "README.md").write_text("install: git+https://github.com/jeffknupp/association@v1.0.0\n")
    docs = repo / "docs"
    docs.mkdir()
    (docs / "usage.rst").write_text("   $ pip install git+https://github.com/jeffknupp/association@v1.0.0\n")
    (docs / "releasing.rst").write_text("``git grep -n 'association@v'`` finds all of them.\n")
    (repo / "unrelated.py").write_text("print('hello')\n")
    _commit_all(repo)

    found = {p.relative_to(repo) for p in bump_version.find_pinned_files(repo)}
    assert found == {Path("README.md"), Path("docs/usage.rst"), Path("docs/releasing.rst")}


def test_check_install_pins_ignores_a_mention_with_no_version_attached(tmp_path: Path) -> None:
    """A file `find_pinned_files` catches by raw substring but with no digits after ``@v`` is not a pin.

    docs/releasing.rst's own prose ("``git grep -n 'association@v'`` finds all
    of them") matches the broad grep but names no version, so it must not be
    treated as something to rewrite or as an unrecognized pin to refuse on.
    """
    repo = _repo(tmp_path)
    (repo / "README.md").write_text("git+https://github.com/jeffknupp/association@v1.0.0\n")
    (repo / "releasing.rst").write_text("``git grep -n 'association@v'`` finds all of them.\n")
    _commit_all(repo)

    files = bump_version.check_install_pins("1.0.0", "1.1.0", repo)
    assert [p.name for p in files] == ["README.md"]


def test_check_install_pins_selects_only_files_naming_the_current_version(tmp_path: Path) -> None:
    """A pin already at the new version needs no rewrite; one at the old version does."""
    repo = _repo(tmp_path)
    (repo / "README.md").write_text("git+https://github.com/jeffknupp/association@v1.0.0\n")
    (repo / "ALREADY.md").write_text("git+https://github.com/jeffknupp/association@v1.1.0\n")
    _commit_all(repo)

    to_rewrite = bump_version.check_install_pins("1.0.0", "1.1.0", repo)
    assert [p.name for p in to_rewrite] == ["README.md"]


def test_check_install_pins_refuses_a_pin_naming_neither_version(tmp_path: Path) -> None:
    """A pin naming neither the current nor the new version means something is already wrong.

    Bumping 1.0.0 -> 1.1.0 with a pin frozen at v9.9.9 (a hand-edit, or a
    rewrite an earlier bump missed) must stop and say which file and which
    version it found, not guess and overwrite it.
    """
    repo = _repo(tmp_path)
    (repo / "README.md").write_text("git+https://github.com/jeffknupp/association@v9.9.9\n")
    _commit_all(repo)

    with pytest.raises(SystemExit) as excinfo:
        bump_version.check_install_pins("1.0.0", "1.1.0", repo)
    message = str(excinfo.value)
    assert "README.md" in message
    assert "9.9.9" in message
    assert "neither" in message


def test_rewrite_install_pins_updates_every_matched_file_and_leaves_no_old_pin(tmp_path: Path) -> None:
    """Every flagged file is rewritten to the new tag, and none keeps the old one."""
    repo = _repo(tmp_path)
    (repo / "README.md").write_text("uv tool install git+https://github.com/jeffknupp/association@v1.0.0\n")
    docs = repo / "docs"
    docs.mkdir()
    (docs / "usage.rst").write_text("pin: git+https://github.com/jeffknupp/association@v1.0.0\n")
    _commit_all(repo)

    files = bump_version.check_install_pins("1.0.0", "1.1.0", repo)
    bump_version.rewrite_install_pins(files, "1.0.0", "1.1.0", repo)

    readme = (repo / "README.md").read_text()
    usage = (docs / "usage.rst").read_text()
    assert "association@v1.1.0" in readme
    assert "association@v1.1.0" in usage
    assert "association@v1.0.0" not in readme
    assert "association@v1.0.0" not in usage


def test_symlink_is_never_a_pin_candidate_and_survives_rewriting(tmp_path: Path) -> None:
    """A symlink like CLAUDE.md is a git blob holding only its target path.

    It can never contain the pin pattern, so it is never returned by
    ``find_pinned_files``/``check_install_pins`` and is never opened for
    writing by ``rewrite_install_pins`` - proving the fix needs no special-case
    exclusion to keep the symlink (which a bulk rewrite has broken once in
    this project's history) intact.
    """
    repo = _repo(tmp_path)
    (repo / "AGENTS.md").write_text("# Working on association\nNo install pin in this file.\n")
    (repo / "CLAUDE.md").symlink_to("AGENTS.md")
    (repo / "README.md").write_text("git+https://github.com/jeffknupp/association@v1.0.0\n")
    _commit_all(repo)

    files = bump_version.check_install_pins("1.0.0", "1.1.0", repo)
    assert {p.name for p in files} == {"README.md"}

    bump_version.rewrite_install_pins(files, "1.0.0", "1.1.0", repo)

    claude_md = repo / "CLAUDE.md"
    assert claude_md.is_symlink()
    assert os.readlink(claude_md) == "AGENTS.md"
    assert "association@v" not in (repo / "AGENTS.md").read_text()
