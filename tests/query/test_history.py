"""Regression + sanity tests for RunHistory - every run's command/trace/timing
evidence, written regardless of --verbose."""

import re
from pathlib import Path

import pytest

from association import __version__
from association.query import history as history_module
from association.query.history import RunHistory, append_note, build_id


def test_log_always_recorded_but_only_printed_when_verbose(capsys: pytest.CaptureFixture[str]) -> None:
    """The whole point: a line must land in the file even when --verbose was
    never passed, and only ALSO go to stderr when it was."""
    quiet = RunHistory(verbose=False)
    quiet.log("quiet line")
    assert quiet.lines == ["quiet line"]
    assert capsys.readouterr().err == ""

    loud = RunHistory(verbose=True)
    loud.log("loud line")
    assert loud.lines == ["loud line"]
    assert "loud line" in capsys.readouterr().err


def test_record_model_call_accumulates_timing() -> None:
    history = RunHistory(verbose=False)
    history.record_model_call(1.5)
    history.record_model_call(2.5)
    assert history.model_calls == 2
    assert history.model_seconds == 4.0
    assert "model inference #1" in history.lines[0]
    assert "model inference #2" in history.lines[1]


def test_record_tool_call_accumulates_timing() -> None:
    history = RunHistory(verbose=False)
    history.record_tool_call("run_sql", 0.2)
    history.record_tool_call("get_leaderboard", 0.1)
    assert history.tool_calls == 2
    assert history.tool_seconds == pytest.approx(0.3)


def test_write_creates_file_named_with_a_hash_containing_everything(tmp_path: Path) -> None:
    history_dir = tmp_path / ".history"
    history = RunHistory(verbose=False, history_dir=history_dir)
    history.log("  -> run_sql({'query': 'SELECT 1'})")
    history.record_model_call(1.0)
    history.record_tool_call("run_sql", 0.1)

    path = history.write(command="association query 'x'", model="qwen2.5:7b", think=False, question="x", answer="the answer")

    assert path.parent == history_dir
    assert path.suffix == ".log"
    # The build that produced the run, then a random hash - the hash keeps the
    # name unpredictable (two runs of one commit must not collide), the prefix
    # ties the file to the code.
    assert path.stem.startswith(f"{build_id()}-")
    assert len(path.stem.removeprefix(f"{build_id()}-")) == 16
    content = path.read_text()
    assert "command: association query 'x'" in content
    assert "question: x" in content
    assert "run_sql({'query': 'SELECT 1'})" in content
    assert "model inference #1: 1.00s" in content
    assert "run_sql: 0.10s" in content
    assert "answer:\nthe answer" in content


def test_write_creates_history_dir_if_missing(tmp_path: Path) -> None:
    history_dir = tmp_path / "nested" / ".history"
    history = RunHistory(verbose=False, history_dir=history_dir)
    path = history.write(command="c", model="m", think=False, question="q", answer="a")
    assert path.exists()


def test_two_runs_get_different_hashes(tmp_path: Path) -> None:
    history_dir = tmp_path / ".history"
    path1 = RunHistory(verbose=False, history_dir=history_dir).write(command="c", model="m", think=False, question="q", answer="a")
    path2 = RunHistory(verbose=False, history_dir=history_dir).write(command="c", model="m", think=False, question="q", answer="a")
    assert path1 != path2


def test_a_sink_replaces_stderr_for_the_live_trace(capsys: pytest.CaptureFixture[str]) -> None:
    """The trace went to stderr and nowhere else, which is why a server had no
    way to forward it. With a sink it goes wherever the caller says - and stops
    going to stderr, or a server would also be writing to its own terminal."""
    seen: list[str] = []
    history = RunHistory(verbose=True, sink=seen.append)
    history.log("routed")
    history.record_model_call(1.0)

    assert seen == ["routed", "  [timing] model inference #1: 1.00s"]
    assert history.lines == seen  # the file still gets everything, as always
    assert capsys.readouterr().err == ""


def test_a_sink_is_still_gated_on_verbose(capsys: pytest.CaptureFixture[str]) -> None:
    """`verbose` means "trace live" regardless of where live goes. A sink that
    fired anyway would make every non-verbose run pay for a trace nobody reads."""
    seen: list[str] = []
    RunHistory(verbose=False, sink=seen.append).log("quiet line")
    assert seen == []


def test_the_history_file_names_the_build_that_produced_it(tmp_path: Path) -> None:
    """A history file is evidence about a behavior, and the version alone cannot
    say which: dozens of commits share one release number, and the answers this
    project keeps changing are exactly the ones a reader needs to tie to a
    build."""
    history = RunHistory(verbose=False, history_dir=tmp_path)
    path = history.write(command="query 'x'", model="m", think=False, question="x", answer="y")
    build = build_id()
    assert path.name.startswith(f"{build}-"), path.name
    assert f"build: {build}" in path.read_text()


def test_the_build_id_falls_back_to_the_version_without_git(monkeypatch: pytest.MonkeyPatch) -> None:
    """A wheel installed outside a checkout still writes history. Losing the
    file would be far worse than identifying it a little more loosely, so every
    way git can fail lands on the version."""

    def no_git(*_args: object, **_kwargs: object) -> object:
        raise OSError("git is not installed")

    build_id.cache_clear()
    monkeypatch.setattr("association.query.history.subprocess.run", no_git)
    try:
        assert build_id() == __version__
    finally:
        build_id.cache_clear()


def test_the_build_id_asks_about_the_package_not_the_caller(monkeypatch: pytest.MonkeyPatch) -> None:
    """Running `association query` inside an unrelated checkout must not stamp
    THAT repository's commit onto the run - git is asked about the package's own
    directory, which is what makes the recorded build meaningful."""
    seen: list[list[str]] = []

    class _Done:
        stdout = "abc1234"

    def record(argv: list[str], **_kwargs: object) -> _Done:
        seen.append(argv)
        return _Done()

    build_id.cache_clear()
    monkeypatch.setattr("association.query.history.subprocess.run", record)
    try:
        build_id()
    finally:
        build_id.cache_clear()
    package = str(Path(history_module.__file__).resolve().parent)
    assert seen and all(argv[:3] == ["git", "-C", package] for argv in seen), seen


def test_append_note_adds_one_timestamped_line(tmp_path: Path) -> None:
    """The web UI's way of recording what is wrong with an answer, or a thought
    on how it should look, beside the trace and the answer it is about."""
    path = tmp_path / "run.log"
    path.write_text("command: q\nanswer:\nthe answer\n")

    line = append_note(path, "this undercounts rebounds")

    assert re.fullmatch(r"\[note \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\] this undercounts rebounds", line), line
    content = path.read_text()
    assert content == f"command: q\nanswer:\nthe answer\n{line}\n"  # appended, not mixed into the run's own record


def test_append_note_can_be_called_more_than_once(tmp_path: Path) -> None:
    """Each call adds its own line - the second call must not overwrite or
    absorb the first, which is the whole point of allowing more than one note
    per answer."""
    path = tmp_path / "run.log"
    path.write_text("")

    first = append_note(path, "first thought")
    second = append_note(path, "second thought")

    assert first != second
    assert path.read_text() == f"{first}\n{second}\n"


def test_append_note_replacing_rewrites_that_line_in_place(tmp_path: Path) -> None:
    """A corrected note replaces its earlier draft - same position in the file,
    new stamp and text - and the run's own record above it is untouched. A
    ``replacing`` line no longer there (edited by hand) appends instead, so
    the correction is never dropped; and only a note line can be replaced,
    never a line of the run's record."""
    path = tmp_path / "run.log"
    path.write_text("command: q\nanswer:\nthe answer\n")
    first = append_note(path, "first draft")
    after = append_note(path, "a later note")

    corrected = append_note(path, "corrected", replacing=first)
    assert path.read_text() == f"command: q\nanswer:\nthe answer\n{corrected}\n{after}\n"

    appended = append_note(path, "again", replacing="[note 2026-01-01T00:00:00Z] gone")
    assert path.read_text().endswith(f"{after}\n{appended}\n")
    untouched = append_note(path, "not a rewrite", replacing="command: q")
    assert path.read_text().startswith("command: q\n") and path.read_text().endswith(f"{untouched}\n")


def test_append_note_escapes_an_embedded_newline(tmp_path: Path) -> None:
    """The whole reason this file records one thing per line: a note with a
    newline in it must not be readable as a second note, or as trace text
    around it."""
    path = tmp_path / "run.log"
    path.write_text("")

    line = append_note(path, "line one\nline two")

    assert line.endswith("line one\\nline two")  # the two characters \, n - not a real newline
    assert path.read_text().count("\n") == 1  # the one line terminator append_note itself writes


def test_append_note_escapes_a_literal_backslash_too(tmp_path: Path) -> None:
    """Escaping only `\\n` and leaving a bare backslash alone would make
    `r"a\nb"` (a literal backslash-n, never a newline) indistinguishable from
    an escaped real newline on the way back out."""
    path = tmp_path / "run.log"
    path.write_text("")

    line = append_note(path, "a\\nb")

    assert line.endswith("a\\\\nb")
