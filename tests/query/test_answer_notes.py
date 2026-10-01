"""A remark recorded where it is written (``query/notes.py``): the sentence
goes back unchanged, and the kind and facts are kept beside it."""

from __future__ import annotations

from pathlib import Path

import pytest

from association.query import notes
from association.query.agent import Agent
from association.query.decisions import Decision
from association.query.notes import Note, collect, decided, note, unsaid
from association.query.router import Route
from association.query.templates.common import TemplateResult


def test_a_writer_gets_its_sentence_back_unchanged_and_records_nothing_when_nobody_listens() -> None:
    said = " 5 of these games have no box score."
    assert note("games_unseen", said, games=5, why="empty_box_score") is said
    assert decided("minimum", ", minimum 20 games", field="minimum", chose=20, of="games") == ", minimum 20 games"


def test_a_note_is_recorded_as_its_kind_and_facts_once() -> None:
    with collect() as collected:
        note("games_unseen", " 5 of these games have no box score.", games=5, why="empty_box_score")
        note("games_unseen", " 5 of these games have no box score.", games=5, why="empty_box_score")
        note("floor", "Box scores start with 1994.", table="box_scores", first=1994)
    assert collected.notes == [Note("games_unseen", {"games": 5, "why": "empty_box_score"}), Note("floor", {"table": "box_scores", "first": 1994})]
    assert collected.decisions == []
    assert collected.notes[0].as_dict() == {"kind": "games_unseen", "facts": {"games": 5, "why": "empty_box_score"}}


def test_a_stated_decision_is_a_decision_with_a_kind() -> None:
    with collect() as collected:
        decided("name_reading", "('maxey' was read as Tyrese Maxey.)", field="player", before="maxey", chose="Tyrese Maxey", instead_of=["Marlon Maxey"], why="only_active", season=2026)
    assert collected.notes == []
    assert collected.decisions == [Decision("answer", "player", "maxey", "Tyrese Maxey", "only_active", kind="name_reading", instead_of=("Marlon Maxey",), facts={"season": 2026})]
    assert collected.decisions[0].as_dict()["kind"] == "name_reading"
    # A decision that is the trace's alone keeps its five fields on the wire.
    assert set(Decision("subject", "kind", None, "player", "").as_dict()) == {"stage", "field", "before", "after", "reason"}


def test_an_empty_sentence_is_no_remark() -> None:
    """A writer wraps its sentence whether or not it had one to write."""
    with collect() as collected:
        assert note("floor", "", first=1994) == ""
        assert decided("minimum", "   ", field="minimum", chose=20) == "   "
    assert collected.notes == [] and collected.decisions == [] and collected.said == []


def test_a_kind_nothing_declares_is_refused_whoever_is_listening() -> None:
    with pytest.raises(ValueError, match="not a note kind"):
        note("made_up", "a sentence")
    with pytest.raises(ValueError, match="not a decision kind"):
        decided("games_unseen", "a sentence", field="x", chose=1)
    assert not set(notes.NOTE_KINDS) & set(notes.DECISION_KINDS)


def test_a_remark_written_and_not_said_is_named() -> None:
    with collect() as collected:
        kept = note("games_unseen", " 5 of these games have\nno box score.", games=5)
        note("floor", "Box scores start with 1994.", first=1994)
        decided("minimum", ", minimum 20 games", field="minimum", chose=20)
    answer = f"Jokic scored 53 (points per game, minimum 20 games).{kept}".replace("\n", " ")
    assert unsaid(collected, answer) == ["floor"]
    assert unsaid(collected, answer + " Box scores start  with 1994.") == []


def test_collections_do_not_leak_between_questions() -> None:
    with collect() as first:
        note("still_open", " (still going)")
    with collect() as second:
        pass
    assert len(first.notes) == 1 and second.notes == []
    note("still_open", " (still going)")
    assert len(first.notes) == 1


def _agent(tmp_path: Path) -> Agent:
    import duckdb

    db_path = tmp_path / "test.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    con.execute("CREATE TABLE teams (team_id VARCHAR, abbreviation VARCHAR, display_name VARCHAR)")
    con.close()
    return Agent(str(db_path), tmp_path / "out", history_dir=tmp_path / ".history", trace=lambda _line: None)


def test_the_answer_carries_what_was_written_for_it_and_the_agent_names_what_was_dropped(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def answered(ctx: object, reading: object, trace: object = None, declined: object = None) -> TemplateResult:
        said = note("games_unseen", " 5 of these games have no box score.", games=5, why="empty_box_score")
        note("floor", "Box scores start with 1994.", table="box_scores", first=1994)  # computed, never attached
        text = "53 points" + decided("minimum", ", minimum 20 games", field="minimum", chose=20, of="games") + "." + said
        return TemplateResult(data={"value": 53}, answer=text)

    monkeypatch.setattr("association.query.compose.answer", answered)
    agent = _agent(tmp_path)
    answer = agent.ask("who scored the most points", route=Route.from_slots(intent="leaderboard", slots={"stat": "points"}))
    assert answer.notes == (Note("games_unseen", {"games": 5, "why": "empty_box_score"}), Note("floor", {"table": "box_scores", "first": 1994}))
    assert [(d.kind, d.after, d.facts) for d in answer.decisions if d.kind] == [("minimum", 20, {"of": "games"})]
    assert agent.unsaid == ["floor"]
    # The next question starts clean.
    monkeypatch.setattr("association.query.compose.answer", lambda ctx, reading, trace=None, declined=None: TemplateResult(data={}, answer="plain"))
    plain = agent.ask("who scored the most points", route=Route.from_slots(intent="leaderboard", slots={"stat": "points"}))
    assert plain.notes == () and agent.unsaid == [] and not [d for d in plain.decisions if d.kind]
