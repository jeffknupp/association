"""Regression + sanity tests for RunHistory - every run's command/trace/timing
evidence, written regardless of --verbose."""

from pathlib import Path

import pytest

from association.query.history import RunHistory


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
    assert len(path.stem) == 16  # random hash, not a fixed/predictable name
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
