"""``scripts/claims_ledger.py``: which of a question's words the reading
depends on, measured by deleting each one (ROADMAP.md, contract 2)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "claims_ledger.py"


def _load() -> ModuleType:
    """Import the script by file path, since ``scripts`` is not a package."""
    spec = importlib.util.spec_from_file_location("claims_ledger_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ledger = _load()


def test_a_question_is_cut_into_words_without_their_punctuation() -> None:
    assert [word for _, _, word in ledger.tokens("How far was Curry's average three-pointer, (2026)?")] == ["how", "far", "was", "curry's", "average", "three-pointer", "2026"]
    assert [word for _, _, word in ledger.tokens("20+ points, 45% from 3")] == ["20+", "points", "45%", "from", "3"]


def test_one_word_is_deleted_and_the_gap_closed() -> None:
    question = "jokic points  in the last 10 games"
    spans = {word: (start, end) for start, end, word in ledger.tokens(question)}
    assert ledger.without(question, *spans["10"]) == "jokic points in the last games"
    assert ledger.without(question, *spans["jokic"]) == "points in the last 10 games"
    assert ledger.without(question, *spans["games"]) == "jokic points in the last 10"


def test_a_word_the_models_reply_accounts_for_is_not_the_readers_to_read() -> None:
    names, stat = ["steph curry", "the Celtics"], "threePointFieldGoalsMade"
    assert ledger.read_by_the_model("curry's", names, stat) and ledger.read_by_the_model("curry" + chr(0x2019) + "s", names, stat) and ledger.read_by_the_model("celtics", names, stat)
    assert ledger.read_by_the_model("threes", names, stat) and ledger.read_by_the_model("3s", names, stat)
    assert not ledger.read_by_the_model("rebounds", names, stat) and not ledger.read_by_the_model("playoffs", names, stat)
    # No stat picked: no stat word is the model's.
    assert not ledger.read_by_the_model("points", names, "")


def test_content_words_leave_out_function_words_and_the_models_words() -> None:
    words = [word for _, _, word in ledger.content_words("How many threes did Steph Curry's team make against the Celtics in the playoffs?", ["Steph Curry", "Celtics"], "threePointFieldGoalsMade")]
    # "make" is the stat's own word (three-pointers MADE), so the model's.
    assert words == ["team", "against", "playoffs"]


def test_a_name_the_reading_settled_on_counts_as_read() -> None:
    """The model copied "steph" alone; the nickname table reads Stephen
    Curry, so "curry" was read as part of the name it is part of."""
    whole = {"reading": {"scope": {"player": "Stephen Curry", "opponent": "Boston Celtics", "season": 2026}, "subject": {"players": ["Stephen Curry"], "teams": []}}}
    assert ledger.names_read(whole) == ["Stephen Curry", "Boston Celtics", "Stephen Curry"]
    assert ledger.names_read({"refused": "a slot nothing can hold"}) == []
    words = [word for _, _, word in ledger.content_words("show steph curry games vs boston", ["steph", *ledger.names_read(whole)], "")]
    assert words == ["games"]


def test_a_run_reports_the_words_the_reading_does_not_depend_on(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The whole instrument over an empty warehouse. "what did the lakers
    coach say last night": deleting "coach" changes the reading (it is what
    names the intent), deleting "night" does not."""
    import duckdb

    monkeypatch.setenv("ASSOCIATION_TODAY", "2026-01-01")
    db_path = tmp_path / "test.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    con.execute("CREATE TABLE teams (team_id VARCHAR, abbreviation VARCHAR, display_name VARCHAR)")
    con.close()
    recorded = tmp_path / "recorded.jsonl"
    rows = [{"q": "what did the lakers coach say last night", "out": {"names": [], "stat": ""}}, {"q": "a reply that failed to decode", "out": {"error": "bad json"}}]
    recorded.write_text("".join(json.dumps(row) + "\n" for row in rows))
    out = tmp_path / "ledger.jsonl"
    monkeypatch.setattr(sys, "argv", ["claims_ledger.py", "run", str(out), "--recorded", str(recorded), "--db-path", str(db_path)])
    assert ledger.main() == 0
    (row,) = [json.loads(line) for line in out.read_text().splitlines()]
    assert row["question"] == "what did the lakers coach say last night"
    assert "coach" in row["content_words"] and "coach" not in row["unread"] and "night" in row["unread"]
    capsys.readouterr()
    monkeypatch.setattr(sys, "argv", ["claims_ledger.py", "report", str(out)])
    assert ledger.main() == 0
    said = capsys.readouterr().out
    assert said.startswith(f"1 questions, {len(row['content_words'])} content words: {len(row['unread'])} unread") and "night 1" in said
