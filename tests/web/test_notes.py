"""Tests for annotating an answer - `POST /api/notes`, which appends a note to
the history file that answer was recorded to.

The route reads AND writes a directory a person owns, the same shape
`GET /api/artifacts/{name}` already guards - so, as in `test_artifacts.py`,
the interesting tests here are mostly about what a name is allowed to be. The
guard itself (`history_file_path`) is tested directly as well as through the
route, for the same reason `test_artifacts.py` tests `artifact_path` directly:
measured, most hostile names never reach the guard at all because Starlette
never matches a path parameter containing a separator, so a route-only test
proves less than it looks like it does.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from association.query.answer import Answer, Timing
from association.web.app import create_app, history_file_path
from association.web.runner import Answered

HISTORY_NAME = "abc1234-deadbeefdeadbeef0.log"


class Idle:
    """An answerer nothing here asks a question of - every test posts notes,
    never questions."""

    busy = False
    ready = True

    def ask(self, question: str, label: str, trace: Callable[[str], None] = lambda line: None) -> Answered:
        return Answered(answer=Answer(question=question, text="", answered_by="fast", timing=Timing(0.0, 0.0, 0, 0.0, 0)), history_file=None)


# The `.log` counterpart of test_artifacts.py's HOSTILE_NAMES: every shape a
# name could take to escape the history directory, plus ordinary-looking ones
# that are still not history file names.
HOSTILE_NAMES = [
    "../secret.log",
    "../../etc/passwd",
    "..%2f..%2fsecret.log",
    "..",
    ".",
    "",
    "/etc/passwd",
    "%2Fetc%2Fpasswd",
    "sub/run.log",
    "run.log/../../secret.log",
    ".hidden.log",
    "run.txt",
    "run.log.txt",
    "run",
]


@pytest.fixture
def served(tmp_path: Path) -> tuple[TestClient, Path]:
    """A client over a history directory holding one real run, with a file the
    server must never touch sitting one level above it."""
    history_dir = tmp_path / ".history"
    history_dir.mkdir()
    (history_dir / HISTORY_NAME).write_text("command: q\nbuild: abc1234\n" + "=" * 80 + "\ntrace\n" + "=" * 80 + "\nanswer:\nthe answer\n")
    (tmp_path / "secret.log").write_text("NOT YOURS")
    app = create_app(Idle(), db_path=str(tmp_path / "nba.duckdb"), out_dir=tmp_path / "out", model="m", router_model="r", history_dir=history_dir)
    return TestClient(app), history_dir


def test_a_note_is_appended_to_the_named_history_file(served: tuple[TestClient, Path]) -> None:
    client, history_dir = served
    response = client.post("/api/notes", json={"history_file": HISTORY_NAME, "note": "this undercounts rebounds"})

    assert response.status_code == 200
    assert response.json()["saved"] is True
    content = (history_dir / HISTORY_NAME).read_text()
    assert content.startswith("command: q\n")  # the run's own record, untouched
    last_line = content.splitlines()[-1]
    assert re.fullmatch(r"\[note \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\] this undercounts rebounds", last_line), last_line
    assert response.json()["line"] == last_line  # what a re-save hands back as `replaces`


def test_saving_an_edited_note_again_replaces_the_earlier_line(served: tuple[TestClient, Path]) -> None:
    """One text box per answer means saving twice is editing the note, not
    adding a second: Jeff saved two notes on 2026-09-24, corrected each and
    saved again, and expected the file to hold the correction alone. The
    client passes the line the first save returned as ``replaces``."""
    client, history_dir = served
    first = client.post("/api/notes", json={"history_file": HISTORY_NAME, "note": "first draft"}).json()["line"]
    second = client.post("/api/notes", json={"history_file": HISTORY_NAME, "note": "corrected", "replaces": first}).json()["line"]

    note_lines = [line for line in (history_dir / HISTORY_NAME).read_text().splitlines() if line.startswith("[note ")]
    assert note_lines == [second]
    assert second.endswith("corrected") and first not in note_lines


def test_more_than_one_note_is_allowed_and_each_appends_its_own_line(served: tuple[TestClient, Path]) -> None:
    """The page lets someone save more than once per answer; the file has to
    keep every one of them rather than the last write winning."""
    client, history_dir = served
    client.post("/api/notes", json={"history_file": HISTORY_NAME, "note": "first thought"})
    client.post("/api/notes", json={"history_file": HISTORY_NAME, "note": "second thought"})

    note_lines = [line for line in (history_dir / HISTORY_NAME).read_text().splitlines() if line.startswith("[note ")]
    assert len(note_lines) == 2
    assert note_lines[0].endswith("first thought")
    assert note_lines[1].endswith("second thought")


def test_a_multi_line_note_stays_on_one_line(served: tuple[TestClient, Path]) -> None:
    """The whole reason this file records one thing per line: a note with an
    embedded newline must not be readable as a second note, or as trace text."""
    client, history_dir = served
    client.post("/api/notes", json={"history_file": HISTORY_NAME, "note": "line one\nline two"})

    content = (history_dir / HISTORY_NAME).read_text()
    note_lines = [line for line in content.splitlines() if line.startswith("[note ")]
    assert len(note_lines) == 1
    assert note_lines[0].endswith("line one\\nline two")
    assert "\nline two" not in content  # never landed as a real second line


def test_an_empty_note_is_rejected(served: tuple[TestClient, Path]) -> None:
    client, history_dir = served
    response = client.post("/api/notes", json={"history_file": HISTORY_NAME, "note": "   "})

    assert response.status_code == 400
    assert (history_dir / HISTORY_NAME).read_text().count("[note ") == 0


@pytest.mark.parametrize("name", HOSTILE_NAMES)
def test_a_name_that_is_not_a_history_file_gets_nothing(name: str, served: tuple[TestClient, Path]) -> None:
    client, _ = served
    response = client.post("/api/notes", json={"history_file": name, "note": "x"})

    assert response.status_code == 404, f"{name!r} returned {response.status_code}"


@pytest.mark.parametrize("name", HOSTILE_NAMES)
def test_the_guard_itself_rejects_every_one_of_those(name: str, tmp_path: Path) -> None:
    """Independently of the route, which is doing some of the work above."""
    assert history_file_path(tmp_path, name) is None


def test_a_well_shaped_but_unknown_name_is_a_404(served: tuple[TestClient, Path]) -> None:
    """A history file the page never actually reported - never created, or
    already cleaned up."""
    client, _ = served
    response = client.post("/api/notes", json={"history_file": "0000000-0000000000000000.log", "note": "x"})

    assert response.status_code == 404


def test_a_symlink_out_of_the_history_dir_is_not_followed(served: tuple[TestClient, Path]) -> None:
    """The one attack the name check cannot see on its own - only resolving it
    and checking where it landed catches this, the same as `test_artifacts.py`
    pins for charts."""
    client, history_dir = served
    (history_dir / "evil.log").symlink_to(history_dir.parent / "secret.log")

    response = client.post("/api/notes", json={"history_file": "evil.log", "note": "x"})

    assert response.status_code == 404
    assert "NOT YOURS" not in response.text  # never read
    assert (history_dir.parent / "secret.log").read_text() == "NOT YOURS"  # and certainly never appended to


def test_a_directory_named_like_a_history_file_is_not_a_target(tmp_path: Path) -> None:
    """`is_file()`, not `exists()` - opening a directory for append is an
    error, not a missing file, and the guard has to rule it out before that."""
    history_dir = tmp_path / ".history"
    (history_dir / "fake.log").mkdir(parents=True)
    assert history_file_path(history_dir, "fake.log") is None


def test_the_history_dir_not_existing_yet_is_a_miss_not_an_error(tmp_path: Path) -> None:
    """A fresh install with no run recorded yet - it must not raise on the way
    to saying there is nothing to annotate."""
    assert history_file_path(tmp_path / "never_created", "abc1234-deadbeefdeadbeef0.log") is None
