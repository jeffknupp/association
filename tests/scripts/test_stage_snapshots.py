"""``scripts/stage_snapshots.py``: the corpus run that writes each stage's
output per question, and the comparison a pipeline change is proven by
(ROADMAP.md, Phase 0). The comparison's exit status is the guard, so every
test here is of a way it must come back 1."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "stage_snapshots.py"


def _load() -> ModuleType:
    """Import the script by file path, since ``scripts`` is not a package."""
    spec = importlib.util.spec_from_file_location("stage_snapshots_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


stage_snapshots = _load()

META = {"meta": {"build": "abc", "code": "/x", "db": "/w.duckdb", "today": "2026-09-30", "threads": 1}}


def _record(question: str, **changes: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "question": question,
        "reading": {"intent": "player_stat", "scope": {"season": 2026}},
        "query": {"shape": "scalar"},
        "result": {"value": 53, "headline": "Jokic scored 53"},
        "answer": {"text": "Jokic scored 53 points.", "answered_by": "fast"},
    }
    for path, value in changes.items():
        stage, key = path.split("__")
        record[stage][key] = value
    return record


def _write(path: Path, *rows: dict[str, Any]) -> Path:
    path.write_text("".join(json.dumps(row) + "\n" for row in (META, *rows)))
    return path


def _compare(monkeypatch: pytest.MonkeyPatch, before: Path, after: Path, *flags: str) -> int:
    monkeypatch.setattr(sys, "argv", ["stage_snapshots.py", "compare", str(before), str(after), *flags])
    return int(stage_snapshots.main())


def test_two_identical_runs_compare_clean(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    before = _write(tmp_path / "a.jsonl", _record("q one"), _record("q two"))
    after = _write(tmp_path / "b.jsonl", _record("q two"), _record("q one"))
    assert _compare(monkeypatch, before, after) == 0
    assert "2 questions compared (values and wording, tolerance 1e-09): 2 identical, 0 differ, 0 in one run only" in capsys.readouterr().out


def test_a_moved_value_fails_the_comparison_and_names_the_first_stage(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    before = _write(tmp_path / "a.jsonl", _record("q one"), _record("q two"))
    after = _write(tmp_path / "b.jsonl", _record("q one"), _record("q two", reading__intent="game_log", result__value=54))
    assert _compare(monkeypatch, before, after) == 1
    out = capsys.readouterr().out
    assert "q two\n    reading: intent changed: 'player_stat' -> 'game_log'\n    result: value changed: 53 -> 54" in out
    assert "1 identical, 1 differ" in out and "first stage that differs: reading 1" in out


def test_a_rewording_fails_unless_only_values_are_compared(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    before = _write(tmp_path / "a.jsonl", _record("q one"))
    reworded = _write(tmp_path / "b.jsonl", _record("q one", answer__text="Nikola Jokic had 53.", result__headline="Nikola Jokic had 53"))
    assert _compare(monkeypatch, before, reworded) == 1
    assert _compare(monkeypatch, before, reworded, "--values-only") == 0
    moved = _write(tmp_path / "c.jsonl", _record("q one", answer__text="Nikola Jokic had 54.", result__value=54))
    assert _compare(monkeypatch, before, moved, "--values-only") == 1


def test_a_question_in_one_run_only_fails_the_comparison(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    before = _write(tmp_path / "a.jsonl", _record("q one"), _record("q two"))
    after = _write(tmp_path / "b.jsonl", _record("q one"))
    assert _compare(monkeypatch, before, after) == 1
    assert "ONLY IN before: q two" in capsys.readouterr().out


def test_a_crash_on_one_side_is_a_difference_and_the_same_crash_on_both_is_not(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    crash = {"question": "q one", "error": "KeyError: 'x'"}
    before = _write(tmp_path / "a.jsonl", _record("q one"))
    after = _write(tmp_path / "b.jsonl", crash)
    assert _compare(monkeypatch, before, after) == 1
    assert "error: (whole) changed: None -> \"KeyError: 'x'\"" in capsys.readouterr().out
    assert _compare(monkeypatch, after, _write(tmp_path / "c.jsonl", crash)) == 0


def test_runs_pinned_differently_are_flagged(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    before = _write(tmp_path / "a.jsonl", _record("q one"))
    after = tmp_path / "b.jsonl"
    after.write_text(json.dumps({"meta": {**META["meta"], "today": "2026-10-05"}}) + "\n" + json.dumps(_record("q one")) + "\n")
    assert _compare(monkeypatch, before, after) == 0
    assert "WARNING: the runs differ in today: '2026-09-30' against '2026-10-05'" in capsys.readouterr().out


def test_a_run_answers_each_recorded_question_with_no_model_and_pins_what_moves(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The whole runner over an empty warehouse: the recorded reply stands in
    for the model, the date is pinned, and the first line says which code and
    warehouse were read."""
    import duckdb

    import association.query.normalizer as normalizer

    # The run replaces the normalizer and pins the date for its process;
    # handed to monkeypatch first, both are put back when this test ends.
    monkeypatch.setattr(normalizer, "normalize", normalizer.normalize)
    monkeypatch.setenv("ASSOCIATION_TODAY", "2026-01-01")
    db_path = tmp_path / "test.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    con.execute("CREATE TABLE teams (team_id VARCHAR, abbreviation VARCHAR, display_name VARCHAR)")
    con.close()
    recorded = tmp_path / "recorded.jsonl"
    rows = [
        {"q": "what did the lakers coach say last night", "out": {"names": [], "stat": ""}},
        {"q": "Tatum rec", "out": {"names": ["Tatum"], "stat": ""}},
        {"q": "a reply that failed to decode", "out": {"error": "bad json"}},
    ]
    recorded.write_text("".join(json.dumps(row) + "\n" for row in rows))
    out = tmp_path / "run.jsonl"
    monkeypatch.setattr(sys, "argv", ["stage_snapshots.py", "run", str(out), "--recorded", str(recorded), "--db-path", str(db_path), "--today", "2026-09-30"])
    assert stage_snapshots.main() == 0
    meta, records = stage_snapshots._load(out)
    assert meta["today"] == "2026-09-30" and meta["threads"] == 1 and meta["questions"] == 3 and meta["db"] == str(db_path.resolve())
    assert Path(meta["code"]).name == "__init__.py"
    coach = records["what did the lakers coach say last night"]
    assert coach["reading"]["intent"] == "coach" and coach["answer"]["answered_by"] == "fast"
    # Refused unread: no reading, no query, no result - and the reason kept.
    short = records["Tatum rec"]
    assert short["reading"] is None and short["answer"]["answered_by"] == "refused" and short["answer"]["unanswered"] == "fewer than 3 words"
    assert records["a reply that failed to decode"]["answer"]["unanswered"] == "the normalizer returned no usable reply"


def test_an_empty_comparison_is_not_a_clean_one(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A wrong path, or a run that died before its first question, must not
    read as "0 differ"."""
    before = _write(tmp_path / "a.jsonl")
    after = _write(tmp_path / "b.jsonl")
    assert _compare(monkeypatch, before, after) == 2
    assert "NOTHING COMPARED" in capsys.readouterr().out
    monkeypatch.setattr(sys, "argv", ["stage_snapshots.py", "compare-calls", str(tmp_path / "none"), str(tmp_path / "nothing")])
    assert stage_snapshots.main() == 2


def _calls(directory: Path, *rows: dict[str, Any]) -> Path:
    directory.mkdir()
    lines = [{"meta": {"code": f"/{directory.name}/association/__init__.py"}}, *rows]
    (directory / "calls-1.jsonl").write_text("".join(json.dumps(row) + "\n" for row in lines))
    return directory


def _call(test: str, call: int, returned: Any = None, **more: Any) -> dict[str, Any]:
    return {"test": test, "call": call, "boundary": "association.query.compose:answer", "args": [], "kwargs": {}, "returned": returned, **more}


def test_two_recorded_suite_runs_are_compared_call_by_call(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rows = [_call("t.py::test_a", 1, {"value": 1}), _call("t.py::test_a", 2, {"value": 2.0}), _call("t.py::test_b", 1, {"value": 3})]
    before = _calls(tmp_path / "before", *rows)

    def compared(after: Path) -> int:
        monkeypatch.setattr(sys, "argv", ["stage_snapshots.py", "compare-calls", str(before), str(after)])
        return int(stage_snapshots.main())

    assert compared(_calls(tmp_path / "same", *rows)) == 0
    out = capsys.readouterr().out
    assert "3 calls compared (tolerance 1e-09): 3 identical, 0 differ, 0 in one run only (0 tests)" in out
    assert "before: /before/association/__init__.py" in out and "after:  /same/association/__init__.py" in out
    # A moved value, in the second call of one test.
    assert compared(_calls(tmp_path / "moved", rows[0], _call("t.py::test_a", 2, {"value": 2.5}), rows[2])) == 1
    assert "t.py::test_a call 2\n    association.query.compose:answer: returned.value changed: 2.0 -> 2.5" in capsys.readouterr().out
    # A call that raised where it returned.
    assert compared(_calls(tmp_path / "raised", rows[0], rows[1], {key: value for key, value in _call("t.py::test_b", 1, raised="Unsupported: no").items() if key != "returned"})) == 1
    # A call one run never made.
    assert compared(_calls(tmp_path / "fewer", rows[0], rows[1])) == 1
    assert "CALLS IN ONE RUN ONLY: t.py::test_b" in capsys.readouterr().out


def test_what_an_answer_said_beside_its_numbers_is_compared_where_both_runs_recorded_it(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A caveat held as a kind and its facts cannot vanish under a
    rewording: the remarks are compared in either mode, unless one run is
    from before they were recorded or the change is the one adding them."""
    said = {"notes": [{"kind": "games_unseen", "facts": {"games": 5}}], "decisions": [], "unsaid": []}
    with_note = dict(_record("q one"), remarks=said)
    without = dict(_record("q one", answer__text="Jokic had 53."), remarks={"notes": [], "decisions": [], "unsaid": []})
    before = _write(tmp_path / "a.jsonl", with_note)
    after = _write(tmp_path / "b.jsonl", without)
    assert _compare(monkeypatch, before, after, "--values-only") == 1
    assert "remarks: notes[0] missing" in capsys.readouterr().out
    assert _compare(monkeypatch, before, after, "--values-only", "--ignore-remarks") == 0
    # A run from before the remarks were recorded has none to compare.
    assert _compare(monkeypatch, _write(tmp_path / "c.jsonl", _record("q one")), before) == 0


def test_a_runs_remarks_are_counted_by_kind_and_a_dropped_one_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    kept = {"notes": [{"kind": "games_unseen", "facts": {"games": 5}}, {"kind": "floor", "facts": {}}], "decisions": [{"kind": "minimum", "after": 20}], "unsaid": []}
    run = _write(tmp_path / "a.jsonl", dict(_record("q one"), remarks=kept), _record("q two"))
    monkeypatch.setattr(sys, "argv", ["stage_snapshots.py", "remarks", str(run)])
    assert stage_snapshots.main() == 0
    out = capsys.readouterr().out
    assert "2 questions: 1 answers carry 3 remarks of 3 kinds" in out and "   1  minimum" in out
    dropped = _write(tmp_path / "b.jsonl", dict(_record("q one"), remarks={**kept, "unsaid": ["floor"]}))
    monkeypatch.setattr(sys, "argv", ["stage_snapshots.py", "remarks", str(dropped)])
    assert stage_snapshots.main() == 1
    assert "WRITTEN AND NOT SAID (floor): q one" in capsys.readouterr().out
