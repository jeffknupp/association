"""``scripts/check_changes_per_commit.py``: every commit of a pushed range that
changes ``src/`` touches ``CHANGES.md`` (#380) - over a scratch
repository, so the check reads real commits."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_changes_per_commit.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_changes_per_commit_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


def _commit(repo: Path, message: str, files: dict[str, str]) -> str:
    for name, text in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    _git(tmp_path, "init", "-q", "-b", "master")
    monkeypatch.chdir(tmp_path)
    for name in ("BEFORE", "AFTER", "PR_BASE", "PR_HEAD"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


def _run(monkeypatch: pytest.MonkeyPatch, *args: str) -> int:
    monkeypatch.setattr(sys, "argv", ["check_changes_per_commit.py", *args])
    return int(_load().main())


def test_a_commit_changing_src_without_changes_md_fails_and_one_with_it_passes(repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    base = _commit(repo, "base", {"README": "x"})
    _commit(repo, "with its entry", {"src/a.py": "1", "CHANGES.md": "- a"})
    _commit(repo, "docs only", {"docs/b.rst": "b"})
    assert _run(monkeypatch, "--range", f"{base}..HEAD", "--no-floor") == 0
    bad = _commit(repo, "source with no entry", {"src/a.py": "2"})
    assert _run(monkeypatch, "--range", f"{base}..HEAD", "--no-floor") == 1
    said = capsys.readouterr().out
    assert f"{bad[:7]} source with no entry" in said and "3 commit(s)" in said and "1 touch src/ without CHANGES.md" in said


def test_a_merge_is_read_as_its_sides_and_the_floor_leaves_history_out(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    base = _commit(repo, "base", {"README": "x"})
    _git(repo, "checkout", "-q", "-b", "side")
    _commit(repo, "side", {"src/s.py": "1", "CHANGES.md": "- s"})
    _git(repo, "checkout", "-q", "master")
    _commit(repo, "main", {"src/m.py": "1", "CHANGES.md": "- s\n- m"})
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "merge", "-q", "--no-ff", "-X", "theirs", "side", "-m", "merge")
    assert _run(monkeypatch, "--range", f"{base}..HEAD", "--no-floor") == 0
    old = _commit(repo, "old, unrecorded", {"src/m.py": "2"})
    monkeypatch.setattr(sys, "argv", ["check_changes_per_commit.py", "--range", f"{base}..HEAD"])
    module = _load()
    assert module.main() == 1
    setattr(module, "FLOOR", old)  # noqa: B010 - the loaded script module's own constant, which mypy cannot see
    assert module.main() == 0


def test_a_push_reads_before_to_after_and_a_force_push_its_head_alone(repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _commit(repo, "base", {"README": "x"})
    bad = _commit(repo, "unrecorded", {"src/a.py": "1"})
    good = _commit(repo, "recorded", {"src/a.py": "2", "CHANGES.md": "- a"})
    monkeypatch.setenv("BEFORE", _git(repo, "rev-parse", "HEAD~2"))
    monkeypatch.setenv("AFTER", good)
    assert _run(monkeypatch, "--no-floor") == 1 and bad[:7] in capsys.readouterr().out
    monkeypatch.setenv("BEFORE", "0" * 40)
    assert _run(monkeypatch, "--no-floor") == 0 and "the pushed head" in capsys.readouterr().out
