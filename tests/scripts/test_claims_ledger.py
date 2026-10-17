"""``scripts/claims_ledger.py``: which of a question's words the reading
depends on, measured by deleting each one."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

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
    """One rule, the lexicon's: the ledger's content
    words are the package's, so its count and ``Reading.unread`` count the
    same words."""
    question = "How many threes did Steph Curry's team make against the Celtics in the playoffs?"
    names, stat = ["Steph Curry", "Celtics"], "threePointFieldGoalsMade"
    assert ledger.content_words(question, names, stat) == content_words(question, names, stat)


def test_a_name_the_reading_settled_on_counts_as_read() -> None:
    """The model copied "steph" alone; the nickname table reads Stephen
    Curry, so "curry" was read as part of the name it is part of."""
    whole: dict[str, Any] = {
        "reading": {
            "who": {"kind": "player", "players": ["Stephen Curry"], "teams": []},
            "cuts": {"opponent": "Boston Celtics", "tenure": None},
            "companions": [{"player": "Draymond Green", "side": "own", "predicate": "absent"}, {"player": "Klay Thompson", "side": "own", "predicate": "played"}],
            "span": {"season": 2026},
            "subject": {"players": ["Stephen Curry"], "teams": [], "opponent": "Boston Celtics", "own_team": None},
        }
    }
    # A teammate who played counts on the with/without split alone, and only with nobody absent.
    assert ledger.names_read(whole) == ["Stephen Curry", "Boston Celtics", "Draymond Green", "Stephen Curry", "Boston Celtics"]
    whole["reading"]["asked"] = {"relation": "team_games", "shape": "split", "by": "presence"}
    assert ledger.names_read(whole) == ["Stephen Curry", "Boston Celtics", "Draymond Green", "Stephen Curry", "Boston Celtics"]
    whole["reading"]["companions"] = whole["reading"]["companions"][1:]
    assert ledger.names_read(whole) == ["Stephen Curry", "Boston Celtics", "Klay Thompson", "Stephen Curry", "Boston Celtics"]
    assert ledger.names_read({"refused": "a slot nothing can hold"}) == []
    words = [word for _, _, word in ledger.content_words("show steph curry games vs boston", ["steph", *ledger.names_read(whole)], "")]
    assert words == ["games"]


def test_a_run_reports_the_words_the_reading_does_not_depend_on(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The whole instrument over an empty warehouse. "what did the lakers
    coach say about practice": deleting "coach" changes the reading (it is what
    names the key the words ask), deleting "practice" does not; the Reading's own unread
    words are recorded beside the deletion's."""
    import duckdb

    monkeypatch.setenv("ASSOCIATION_TODAY", "2026-01-01")
    db_path = tmp_path / "test.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    con.execute("CREATE TABLE teams (team_id VARCHAR, abbreviation VARCHAR, display_name VARCHAR)")
    con.close()
    recorded = tmp_path / "recorded.jsonl"
    rows = [{"q": "what did the lakers coach say about practice", "out": {"names": [], "stat": ""}}, {"q": "a reply that failed to decode", "out": {"error": "bad json"}}]
    recorded.write_text("".join(json.dumps(row) + "\n" for row in rows))
    out = tmp_path / "ledger.jsonl"
    monkeypatch.setattr(sys, "argv", ["claims_ledger.py", "run", str(out), "--recorded", str(recorded), "--db-path", str(db_path)])
    assert ledger.main() == 0
    meta, row = [json.loads(line) for line in out.read_text().splitlines()]
    assert meta["meta"]["population"] == "other"
    assert row["question"] == "what did the lakers coach say about practice"
    assert "coach" in row["content_words"] and "coach" not in row["unread"] and "practice" in row["unread"]
    assert "practice" in row["reading_unread"]
    capsys.readouterr()
    monkeypatch.setattr(sys, "argv", ["claims_ledger.py", "report", str(out)])
    agrees = row["reading_unread"] == row["unread"]
    assert ledger.main() == (0 if agrees else 1)
    said = capsys.readouterr().out
    assert said.startswith(f"1 questions, {len(row['content_words'])} content words: {len(row['unread'])} unread") and "practice 1" in said
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


def test_the_held_disagreements_pass_and_one_that_clears_is_slack(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The subject reading's two holdouts are
    held by question text; another question differing fails the report; a
    held one that agrees now is reported as slack and passes (the lead
    removes it at the merge)."""
    import argparse

    held = list(ledger.HELD_DISAGREEMENTS)
    assert len(held) == 2 and all(ledger.HELD_DISAGREEMENTS[question] for question in held)
    rows = [{"question": question, "content_words": ["against"], "unread": ["against"], "reading_unread": []} for question in held]
    path = tmp_path / "ledger.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    assert ledger.report(argparse.Namespace(ledger=path, top=5, show=0)) == 0
    assert "(held)" in capsys.readouterr().out
    cleared = [{**rows[0], "reading_unread": rows[0]["unread"]}, *rows[1:]]
    path.write_text("".join(json.dumps(row) + "\n" for row in cleared))
    assert ledger.report(argparse.Namespace(ledger=path, top=5, show=0)) == 0
    assert f"GONE: {held[0]!r}" in capsys.readouterr().out


def test_the_joined_claims_are_held_by_kind_and_count(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    """A partial overlap joins into one claim
    named for both readings, and the joins are held by kind and count
    (``HELD_JOINS``): a new kind or a grown count fails the report, and so
    does one that fell or vanished, on a run of every recorded question,
    until the table is lowered with it."""
    import argparse

    def report(joins: list[str]) -> int:
        rows = [{"question": f"q{n}", "content_words": [], "unread": [], "reading_unread": [], "joined": joins if n == 0 else []} for n in range(ledger.HELD_JOINS_QUESTIONS)]
        path = tmp_path / "ledger.jsonl"
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))
        return int(ledger.report(argparse.Namespace(ledger=path, top=5, show=0)))

    # The table is empty: any join on the 628 is new.
    assert ledger.HELD_JOINS == {} and report([]) == 0
    assert report(["window+period"]) == 1 and "NEW: window+period 0 -> 1" in capsys.readouterr().out
    # A kind the table does not hold is new.
    assert report(["intent+line"]) == 1 and "NEW: intent+line 0 -> 1" in capsys.readouterr().out
    # A held table: a grown count and a vanished kind each fail.
    monkeypatch.setattr(ledger, "HELD_JOINS", {"companion+intent": 2, "refused+measure": 1})
    held = [what for what, count in ledger.HELD_JOINS.items() for _ in range(count)]
    assert report(held) == 0
    assert report([*held, "companion+intent"]) == 1 and "GREW: companion+intent" in capsys.readouterr().out
    assert report([what for what in held if what != "refused+measure"]) == 0 and "GONE: refused+measure 1 -> 0" in capsys.readouterr().out


def _held_run(tmp_path: Path, population: str, rows: list[dict[str, Any]]) -> Path:
    """A ledger file of ``rows`` read over ``population`` (its meta line, as ``run`` writes it)."""
    path = tmp_path / f"{population}.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in [{"meta": {"population": population}}, *rows]))
    return path


def test_a_whole_populations_unread_words_and_numbers_are_held(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    """The unread words
    and the questions holding an unread number are held per population
    (``HELD_PATH``) - a grown count or a question new to the list fails the
    report, and so does one that fell or left it, on a run of the whole
    population, until ``report --shrink`` lowers the file; a run of part of
    one, or of neither, is held to what it can show grew."""
    import argparse

    held = tmp_path / "held.json"
    monkeypatch.setattr(ledger, "HELD_PATH", held)
    monkeypatch.setattr(ledger, "POPULATIONS", {"corpus": 3, "feed": 3})
    held.write_text(json.dumps({"corpus": {"unread_words": 2, "unread_numbers": ["q1 with 30"]}}))

    def report(rows: list[dict[str, Any]], shrink: bool = False) -> int:
        return int(ledger.report(argparse.Namespace(ledger=_held_run(tmp_path, "corpus", rows), top=5, show=0, shrink=shrink)))

    def row(question: str, unread: list[str]) -> dict[str, Any]:
        return {"question": question, "content_words": unread, "unread": unread, "reading_unread": unread}

    rows = [row("q0", ["average"]), row("q1 with 30", ["30"]), row("q2", [])]
    assert report(rows) == 0
    capsys.readouterr()
    # A word more, unread: the count grew.
    assert report([row("q0", ["average", "stats"]), *rows[1:]]) == 1 and "GREW: unread words 2 -> 3" in capsys.readouterr().out
    # A number newly unread on another question: new to the list, though the count of words held.
    assert report([row("q0", []), rows[1], row("q2", ["5"])]) == 1 and "NEW unread number: 'q2'" in capsys.readouterr().out
    # The held number read now: gone from the list, and the words fell - both slack, reported and passing.
    cleared = [rows[0], row("q1 with 30", []), rows[2]]
    assert report(cleared) == 0
    said = capsys.readouterr().out
    assert "GONE unread number: 'q1 with 30'" in said and "FELL: unread words 2 -> 1" in said
    # Part of the population vouches for no fall.
    assert report(cleared[:2]) == 0 and "FELL" not in capsys.readouterr().out
    assert report(rows[:2]) == 0
    # --shrink lowers the file to the whole run, and never raises it.
    assert report(cleared, shrink=True) == 0
    assert json.loads(held.read_text())["corpus"] == {"unread_words": 1, "unread_numbers": []}
    assert report(cleared) == 0
    assert report(rows, shrink=True) == 1 and json.loads(held.read_text())["corpus"]["unread_words"] == 1
    # A ledger read over neither population is held to nothing.
    path = tmp_path / "other.jsonl"
    path.write_text(json.dumps(row("q9", ["7", "x"])) + "\n")
    assert ledger.report(argparse.Namespace(ledger=path, top=5, show=0)) == 0


def test_the_feeds_disagreements_and_joins_are_held_by_question_and_kind(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    """The feed's questions whose Reading and deletion disagree are held by
    question, and its joins
    by kind and count (#354's ``situation+date``): a new one fails, and a
    held one gone fails until it is removed."""
    import argparse

    held = tmp_path / "held.json"
    monkeypatch.setattr(ledger, "HELD_PATH", held)
    monkeypatch.setattr(ledger, "POPULATIONS", {"corpus": 3, "feed": 3})
    held.write_text(json.dumps({"feed": {"unread_words": 3, "unread_numbers": [], "disagreements": ["q0"], "joins": {"situation+date": 1}}}))

    def report(rows: list[dict[str, Any]]) -> int:
        return int(ledger.report(argparse.Namespace(ledger=_held_run(tmp_path, "feed", rows), top=5, show=0)))

    rows = [
        {"question": "q0", "content_words": ["head"], "unread": ["head"], "reading_unread": [], "joined": ["situation+date"]},
        {"question": "q1", "content_words": ["stats"], "unread": ["stats"], "reading_unread": ["stats"], "joined": []},
        {"question": "q2", "content_words": ["most"], "unread": ["most"], "reading_unread": ["most"], "joined": []},
    ]
    assert report(rows) == 0
    capsys.readouterr()
    assert report([*rows[:2], {**rows[2], "reading_unread": []}]) == 1 and "NEW disagreement: 'q2'" in capsys.readouterr().out
    assert report([{**rows[0], "reading_unread": ["head"]}, *rows[1:]]) == 0 and "GONE disagreement: 'q0'" in capsys.readouterr().out
    assert report([rows[0], {**rows[1], "joined": ["situation+date"]}, rows[2]]) == 1 and "GREW: situation+date 1 -> 2" in capsys.readouterr().out
    assert report([{**rows[0], "joined": []}, *rows[1:]]) == 0 and "GONE: situation+date 1 -> 0" in capsys.readouterr().out


def test_the_held_file_holds_both_populations() -> None:
    """``scripts/ledger_held.json`` holds the 628's and the 2,082's counts,
    each list a list of questions and each count a number (the values are
    the report's to hold, and ``--shrink``'s to lower)."""
    held = json.loads(ledger.HELD_PATH.read_text())
    assert set(held) == {"corpus", "feed"}
    assert all(isinstance(population["unread_words"], int) for population in held.values())
    assert all(isinstance(question, str) for population in held.values() for question in population["unread_numbers"])
    assert all(isinstance(question, str) for question in held["feed"]["disagreements"])
    assert all(isinstance(count, int) for count in held["feed"]["joins"].values())


def test_shrink_keeps_a_questions_own_characters(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``report --shrink`` writes the held questions as typed - "dončić", not
    its escape - and the lists sorted, so a lowered file differs from the one
    before only where a count fell or a question left."""
    import argparse

    held = tmp_path / "held.json"
    monkeypatch.setattr(ledger, "HELD_PATH", held)
    monkeypatch.setattr(ledger, "POPULATIONS", {"corpus": 2, "feed": 2})
    held.write_text(json.dumps({"corpus": {"unread_words": 3, "unread_numbers": ["luka dončić 30 points", "q1 with 5"]}}, ensure_ascii=False))
    rows = [
        {"question": "luka dončić 30 points", "content_words": ["30"], "unread": ["30"], "reading_unread": ["30"]},
        {"question": "q1 with 5", "content_words": ["5"], "unread": [], "reading_unread": []},
    ]
    assert ledger.report(argparse.Namespace(ledger=_held_run(tmp_path, "corpus", rows), top=5, show=0, shrink=True)) == 0
    text = held.read_text()
    assert "luka dončić 30 points" in text and "\\u" not in text
    assert json.loads(text)["corpus"] == {"unread_words": 1, "unread_numbers": ["luka dončić 30 points"]}
