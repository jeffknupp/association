"""A remark recorded where it is written (``query/notes.py``): the sentence
goes back unchanged, and the kind and facts are kept beside it."""

from __future__ import annotations

import ast
from datetime import date
from pathlib import Path

import pytest
from routed import ask_routed, slots_route

from association.query import notes
from association.query.agent import Agent
from association.query.decisions import Decision
from association.query.notes import Note, collect, decided, note, unsaid
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
    def answered(ctx: object, reading: object, trace: object = None, declined: object = None, planned: object = None) -> TemplateResult:
        said = note("games_unseen", " 5 of these games have no box score.", games=5, why="empty_box_score")
        note("floor", "Box scores start with 1994.", table="box_scores", first=1994)  # computed, never attached
        text = "53 points" + decided("minimum", ", minimum 20 games", field="minimum", chose=20, of="games") + "." + said
        return TemplateResult(data={"value": 53}, answer=text)

    monkeypatch.setattr("association.query.compose.answer", answered)
    agent = _agent(tmp_path)
    answer = ask_routed(agent, "who scored the most points", slots_route("leaderboard", {"stat": "points"}))
    assert answer.notes == (Note("games_unseen", {"games": 5, "why": "empty_box_score"}), Note("floor", {"table": "box_scores", "first": 1994}))
    assert [(d.kind, d.after, d.facts) for d in answer.decisions if d.kind] == [("minimum", 20, {"of": "games"})]
    assert agent.unsaid == ["floor"]
    # The next question starts clean.
    monkeypatch.setattr("association.query.compose.answer", lambda ctx, reading, trace=None, declined=None, planned=None: TemplateResult(data={}, answer="plain"))
    plain = ask_routed(agent, "who scored the most points", slots_route("leaderboard", {"stat": "points"}))
    assert plain.notes == () and agent.unsaid == [] and not [d for d in plain.decisions if d.kind]


def test_a_fact_is_a_plain_value_whoever_is_listening() -> None:
    """An entity or a date handed over as a fact would reach the wire as an
    object: refused where the writer runs, in any test, not at the first
    answer served."""
    with collect() as collected:
        note("rebuilt_agreement", "said", pct=99.5, seasons=(2013, 2018), columns={"b", "a"}, what={"season": 2013})
        decided("name_reading", "said", field="player", before="maxey", chose="Tyrese Maxey", instead_of=("Marlon Maxey",))
    assert collected.notes[0].facts == {"pct": 99.5, "seasons": [2013, 2018], "columns": ["a", "b"], "what": {"season": 2013}}
    with pytest.raises(TypeError, match="the fact 'first' is a date"):
        note("games_unseen", "said", first=date(2026, 1, 2))
    with pytest.raises(TypeError, match="'chose' is a object"):
        decided("minimum", "said", field="minimum", chose=object())


def _remark_calls() -> list[tuple[str, int, str, ast.Call]]:
    """Every ``note(...)`` and ``decided(...)`` call under ``src``: the
    file, the line, which of the two, and the call."""
    found: list[tuple[str, int, str, ast.Call]] = []
    root = Path(notes.__file__).resolve().parents[1]
    for path in sorted(root.rglob("*.py")):
        if path.name == "notes.py":
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {"note", "decided"} and node.args:
                found += [(str(path.relative_to(root)), node.lineno, node.func.id, node)]
    return found


def test_every_remark_in_the_source_names_a_declared_kind_and_its_declared_facts() -> None:
    """A kind and a fact's name are checked when their line runs, and not
    every line runs under a test: so every call in the source is read here.
    The kind is a literal (a computed one cannot be checked), a ``note``
    names a note kind and a ``decided`` a decision kind, and each fact
    written by name is one the kind declares."""
    own = {"field", "chose", "before", "instead_of", "why"}
    calls = _remark_calls()
    assert len(calls) >= 90
    for where, line, function, call in calls:
        kind = call.args[0]
        assert isinstance(kind, ast.Constant) and isinstance(kind.value, str), f"{where}:{line}: the kind is not a string literal"
        declared = notes.NOTE_KINDS if function == "note" else notes.DECISION_KINDS
        assert kind.value in declared, f"{where}:{line}: {function}({kind.value!r}, ...) is not a declared kind"
        named = {keyword.arg for keyword in call.keywords if keyword.arg is not None} - (own if function == "decided" else set())
        assert named <= notes.FACTS[kind.value], f"{where}:{line}: {kind.value!r} holds no fact named {sorted(named - notes.FACTS[kind.value])}"


def test_every_kind_declares_its_facts_and_is_written_somewhere() -> None:
    assert set(notes.FACTS) == set(notes.NOTE_KINDS) | set(notes.DECISION_KINDS)
    written = {call.args[0].value for _, _, _, call in _remark_calls() if isinstance(call.args[0], ast.Constant)}
    assert written == set(notes.FACTS), "a kind nothing writes is one nothing can check"
    with pytest.raises(ValueError, match="holds no fact named \\['seasons'\\]"):
        note("still_open", " (still going)", seasons=[2026])
