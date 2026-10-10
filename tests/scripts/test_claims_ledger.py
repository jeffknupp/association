"""``scripts/claims_ledger.py``: which of a question's words the reading
depends on, measured by deleting each one (ROADMAP.md, contract 2)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

from association.query.lexicon import content_tokens, content_words

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "claims_ledger.py"


def _load() -> ModuleType:
    """Import the script by file path, since ``scripts`` is not a package."""
    spec = importlib.util.spec_from_file_location("claims_ledger_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ledger = _load()


def test_one_word_is_deleted_and_the_gap_closed() -> None:
    question = "jokic points  in the last 10 games"
    spans = {word: (start, end) for start, end, word in content_tokens(question)}
    assert ledger.without(question, *spans["10"]) == "jokic points in the last games"
    assert ledger.without(question, *spans["jokic"]) == "points in the last 10 games"
    assert ledger.without(question, *spans["games"]) == "jokic points in the last 10"


def test_the_ledger_counts_by_the_rule_the_reading_states_its_unread_words_by() -> None:
    """One rule, the lexicon's (Phase 3, step 3): the ledger's content
    words are the package's, so its count and ``Reading.unread`` count the
    same words."""
    question = "How many threes did Steph Curry's team make against the Celtics in the playoffs?"
    names, stat = ["Steph Curry", "Celtics"], "threePointFieldGoalsMade"
    assert ledger.content_words(question, names, stat) == content_words(question, names, stat)


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
    names the intent), deleting "night" does not; the Reading's own unread
    words are recorded beside the deletion's."""
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
    assert "night" in row["reading_unread"]
    capsys.readouterr()
    monkeypatch.setattr(sys, "argv", ["claims_ledger.py", "report", str(out)])
    agrees = row["reading_unread"] == row["unread"]
    assert ledger.main() == (0 if agrees else 1)
    said = capsys.readouterr().out
    assert said.startswith(f"1 questions, {len(row['content_words'])} content words: {len(row['unread'])} unread") and "night 1" in said
    assert f"{int(agrees)} of 1 questions agree with the deletion" in said


def test_the_report_names_each_question_the_reading_and_the_deletion_disagree_on(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The check from outside: a question whose Reading states other unread
    words than the deletion finds is named, and the report exits 1; a ledger
    from a tree whose Reading states none is not failed for it."""
    import argparse

    rows = [
        {"question": "how many points does embiid average", "content_words": ["average"], "unread": ["average"], "reading_unread": ["average"]},
        {"question": "show me luka's avg assists year over year", "content_words": ["avg", "year", "over", "year"], "unread": ["avg"], "reading_unread": ["avg", "year", "over", "year"]},
    ]
    path = tmp_path / "ledger.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    assert ledger.report(argparse.Namespace(ledger=path, top=5, show=0)) == 1
    said = capsys.readouterr().out
    assert "1 of 2 questions agree with the deletion, 1 differ" in said and "show me luka's avg assists year over year" in said
    path.write_text("".join(json.dumps({key: value for key, value in row.items() if key != "reading_unread"}) + "\n" for row in rows))
    assert ledger.report(argparse.Namespace(ledger=path, top=5, show=0)) == 0
    assert "not recorded" in capsys.readouterr().out


def test_the_held_disagreements_pass_and_one_that_clears_fails_until_it_is_removed(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Jeff's ruling (2026-10-10): the subject reading's three holdouts are
    held by question text; another question differing fails the report, and
    so does a held one that agrees now (it leaves the list with its fix)."""
    import argparse

    held = list(ledger.HELD_DISAGREEMENTS)
    assert len(held) == 3 and all(ledger.HELD_DISAGREEMENTS[question] for question in held)
    rows = [{"question": question, "content_words": ["against"], "unread": ["against"], "reading_unread": []} for question in held]
    path = tmp_path / "ledger.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    assert ledger.report(argparse.Namespace(ledger=path, top=5, show=0)) == 0
    assert "(held)" in capsys.readouterr().out
    cleared = [{**rows[0], "reading_unread": rows[0]["unread"]}, *rows[1:]]
    path.write_text("".join(json.dumps(row) + "\n" for row in cleared))
    assert ledger.report(argparse.Namespace(ledger=path, top=5, show=0)) == 1
    assert f"GONE: {held[0]!r}" in capsys.readouterr().out
