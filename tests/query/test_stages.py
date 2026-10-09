"""The stage snapshot and its comparison (ROADMAP.md, Phase 0): the record a
change to the pipeline is proven against, so every way the comparison can
say "identical" is checked here to be a way it can also say "different"."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from routed import ask_routed, slots_route

from association.query.agent import Agent
from association.query.answer import Answer, Artifact, Reply, Timing
from association.query.compose.core import Query
from association.query.compose.plan import plan_point
from association.query.decisions import Decision
from association.query.reading import Cause, Reading, Scope, Span
from association.query.stages import STAGES, WORDING, Difference, differences, plain, read_stages, snapshot
from association.query.subject import Subject

QUESTION = "how many points did jokic score against boston"
_TIMING = Timing(total_seconds=0.0, model_seconds=0.0, model_calls=0, tool_seconds=0.0, tool_calls=0)


def _reading() -> Reading:
    scope = Scope(player="Nikola Jokic", opponent="Boston Celtics", span=Span(season=2026))
    point = Reading(scope=scope, shape="scalar", measures=["points"], aggregate="total", intent="player_stat")
    subject = Subject(kind="player", players=("Nikola Jokic",), opponent="Boston Celtics", evidence=("'jokic' names one player",))
    return Reading(scope=scope, intent="player_stat", subject=subject, point=point, decisions=(Decision("subject", "kind", None, "player", ""),))


def _answer(**changes: Any) -> Answer:
    games = [{"date": "2026-01-02", "points": 31, "rate": 0.5}, {"date": "2026-03-04", "points": 22, "rate": 0.25}]
    data = {"player": "Nikola Jokic", "games": games, "value": 53, "headline": "Jokic scored 53", "notes": ["a caveat"]}
    return Answer(**{"question": QUESTION, "text": "Jokic scored 53 points. a caveat", "answered_by": "fast", "timing": _TIMING, "intent": "player_stat", "data": data, **changes})


def _snapshot(**changes: Any) -> dict[str, Any]:
    return snapshot(_reading(), _answer(**changes), planned=plan_point(_reading()))


def test_a_snapshot_holds_the_four_stages_as_json() -> None:
    record = _snapshot()
    assert set(STAGES) <= set(record) and record["question"] == QUESTION
    assert json.loads(json.dumps(record)) == record
    assert record["reading"]["intent"] == "player_stat" and record["reading"]["scope"] == {"player": "Nikola Jokic", "opponent": "Boston Celtics", "season": 2026}
    assert record["reading"]["decisions"] == [{"stage": "subject", "field": "kind", "before": None, "after": "player", "reason": ""}]
    assert record["reading"]["point"]["shape"] == "scalar" and record["reading"]["point"]["measures"] == ["points"]
    # The query stage is what the PLANNER built from that point, not the point again.
    assert (
        record["query"]["relation"] == "player" and record["query"]["skeleton"] == "scalar" and record["query"]["measures"] == ["points"] and record["query"]["scope"]["opponent"] == "Boston Celtics"
    )
    assert record["result"]["value"] == 53 and record["answer"]["answered_by"] == "fast"


def test_the_query_stage_is_what_the_planner_built_not_the_point_again() -> None:
    # Phase 1's order from here, step 3: a planner that builds something other
    # than the point it was handed is seen in the query stage, which recorded
    # the point itself until then.

    reading = _reading()
    planned = plan_point(reading)
    assert isinstance(planned.query, Query) and planned.query.limit is None
    moved = snapshot(reading, _answer(), planned=replace(planned, query=replace(planned.query, limit=5)))
    assert moved["query"]["limit"] == 5 and moved["reading"]["point"]["limit"] is None
    assert [d.path for d in differences(_snapshot(), moved)] == ["limit"] and differences(_snapshot(), moved)[0].stage == "query"


def test_no_stage_record_holds_the_questions_text() -> None:
    """The text stays in the reader (ROADMAP contract 1): the snapshot names
    the question once, at the top, and no stage's record repeats it - the
    subject carries it today, and the record leaves it and the evidence
    prose out."""
    record = _snapshot()
    assert QUESTION not in json.dumps(record["reading"]) and QUESTION not in json.dumps(record["query"])
    assert "evidence" not in record["reading"]["subject"]


def test_the_reading_and_query_records_can_be_taken_without_an_answer() -> None:
    """What ``scripts/claims_ledger.py`` compares: the same two records a
    snapshot holds, for a caller that stops before the answer."""
    record = _snapshot()
    assert read_stages(_reading(), planned=plan_point(_reading())) == {"reading": record["reading"], "query": record["query"]}


def test_a_question_nothing_read_has_no_reading_or_query() -> None:
    record = snapshot(None, _answer(text="refused", answered_by="refused", intent=None, data=None), unanswered="fewer than 3 words")
    assert record["reading"] is None and record["query"] is None and record["result"] is None
    assert record["answer"]["unanswered"] == "fewer than 3 words"


def test_a_point_the_compiler_has_none_of_says_why() -> None:
    declined = Reading(intent="shot_chart", point_declined="no player subject")
    assert snapshot(declined, _answer(), planned=plan_point(declined))["query"] == {"declined": "no player subject"}
    refused = Reading(intent="leaderboard", point_refusal=Cause(kind="no_ranking_measure", facts={"stat": "bench points"}))
    said = "No ranking reads 'bench points' on the player-games relation - it only ranks the box-score measures it knows, not a NetPoints or other outside figure."
    assert snapshot(refused, _answer(), planned=plan_point(refused))["query"] == {"refused": {"message": said, "stat": "bench points"}, "said": said}
    # The reader's own verdict is on the reading record too, so a decline
    # that moves between the reader and the planner with the same sentence
    # is a difference in the reading stage, not only in the query's.
    assert snapshot(declined, _answer(), planned=plan_point(declined))["reading"]["point_declined"] == "no player subject"
    assert snapshot(refused, _answer(), planned=plan_point(refused))["reading"]["point_refusal"] == {"kind": "no_ranking_measure", "facts": {"stat": "bench points"}}
    whole = snapshot(_reading(), _answer(), planned=plan_point(_reading()))["reading"]
    assert whole["point_declined"] is None and whole["point_refusal"] is None and whole["point"]["intent"] == "player_stat"


def test_plain_values_are_the_same_on_every_run() -> None:
    @dataclass
    class Row:
        when: date
        where: Path
        tags: frozenset[str]

    class Odd:
        def __repr__(self) -> str:
            return "<odd at /tmp/scratch-1/out>"

    mask = {"/tmp/scratch-1/out": "<out>"}
    assert plain(Row(date(2026, 1, 2), Path("/tmp/scratch-1/out/chart.html"), frozenset({"b", "a"})), mask=mask) == {"when": "2026-01-02", "where": "<out>/chart.html", "tags": ["a", "b"]}
    assert plain((1, 2.5, None, True), mask=mask) == [1, 2.5, None, True]
    assert plain({"said": "wrote /tmp/scratch-1/out/x.html"}, mask=mask) == {"said": "wrote <out>/x.html"}
    # A type nothing here knows is kept, visibly, rather than dropped.
    assert plain(Odd(), mask=mask) == {"unknown": "Odd", "repr": "<odd at <out>>"}


def test_a_chart_written_to_another_directory_is_the_same_answer() -> None:
    here = snapshot(
        _reading(), _answer(text="Chart: /tmp/a/out/chart.html", artifacts=[Artifact("shot_chart", Path("/tmp/a/out/chart.html"))]), planned=plan_point(_reading()), mask={"/tmp/a/out": "<out>"}
    )
    there = snapshot(
        _reading(), _answer(text="Chart: /tmp/b/out/chart.html", artifacts=[Artifact("shot_chart", Path("/tmp/b/out/chart.html"))]), planned=plan_point(_reading()), mask={"/tmp/b/out": "<out>"}
    )
    assert differences(here, there) == [] and here["answer"]["artifacts"] == ["shot_chart"]


def test_identical_snapshots_have_no_differences() -> None:
    assert differences(_snapshot(), _snapshot()) == []


def _moved(path: list[Any], value: Any, stage: str = "result") -> dict[str, Any]:
    """A snapshot with one value replaced, by the path to it inside ``stage``."""
    record = copy.deepcopy(_snapshot())
    at = record[stage]
    for key in path[:-1]:
        at = at[key]
    at[path[-1]] = value
    return record


@pytest.mark.parametrize(
    ("path", "value", "stage", "expected"),
    [
        (["value"], 54, "result", ("result", "value", "changed")),
        (["games", 1, "points"], 23, "result", ("result", "games[1].points", "changed")),
        (["games", 0, "rate"], 0.5000001, "result", ("result", "games[0].rate", "changed")),
        (["value"], 53.0, "result", ("result", "value", "type")),
        (["value"], None, "result", ("result", "value", "changed")),
        (["scope", "season"], 2025, "reading", ("reading", "scope.season", "changed")),
        (["intent"], "game_log", "reading", ("reading", "intent", "changed")),
        (["aggregate"], "per_game", "query", ("query", "aggregate", "changed")),
        (["text"], "Jokic scored 54 points.", "answer", ("answer", "text", "changed")),
        (["answered_by"], "refused", "answer", ("answer", "answered_by", "changed")),
    ],
)
def test_one_moved_value_is_one_difference_naming_its_stage_and_path(path: list[Any], value: Any, stage: str, expected: tuple[str, str, str]) -> None:
    found = differences(_snapshot(), _moved(path, value, stage))
    assert [(d.stage, d.path, d.kind) for d in found] == [expected]


def test_a_float_inside_the_tolerance_is_the_same_number_and_nan_equals_nan() -> None:
    assert differences(_snapshot(), _moved(["games", 0, "rate"], 0.5 + 1e-13)) == []
    assert differences(_moved(["games", 0, "rate"], float("nan")), _moved(["games", 0, "rate"], float("nan"))) == []
    assert [d.kind for d in differences(_snapshot(), _moved(["games", 0, "rate"], float("nan")))] == ["changed"]
    # The tolerance is the caller's to widen.
    assert differences(_snapshot(), _moved(["games", 0, "rate"], 0.5000001), tolerance=1e-3) == []


def test_a_key_or_a_row_gained_or_lost_is_named() -> None:
    gained = copy.deepcopy(_snapshot())
    gained["result"]["wins"] = 3
    gained["result"]["games"].append({"date": "2026-04-05", "points": 9, "rate": 0.1})
    del gained["result"]["player"]
    found = {(d.path, d.kind) for d in differences(_snapshot(), gained)}
    assert found == {("wins", "added"), ("games[2]", "added"), ("player", "missing")}
    assert {(d.path, d.kind) for d in differences(gained, _snapshot())} == {("wins", "missing"), ("games[2]", "missing"), ("player", "added")}


def test_the_same_rows_in_another_order_are_one_reordered_difference() -> None:
    """Two rows tied on a ranking's sort key may swap between runs: said as
    one difference of its own kind, so a report can count them apart from a
    moved number - and a moved number inside a reordering is still found."""
    swapped = copy.deepcopy(_snapshot())
    swapped["result"]["games"].reverse()
    assert [(d.path, d.kind) for d in differences(_snapshot(), swapped)] == [("games", "reordered")]
    swapped["result"]["games"][0]["points"] = 99
    assert "reordered" not in {d.kind for d in differences(_snapshot(), swapped)}


def test_the_first_difference_is_in_the_earliest_stage() -> None:
    record = _moved(["scope", "season"], 2025, "reading")
    record["result"]["value"] = 1
    record["answer"]["text"] = "something else"
    assert [d.stage for d in differences(_snapshot(), record)] == ["reading", "result", "answer"]


def test_values_only_leaves_the_sentences_out_and_nothing_else() -> None:
    reworded = copy.deepcopy(_snapshot())
    reworded["answer"]["text"] = "Nikola Jokic had 53."
    reworded["result"]["headline"] = "Nikola Jokic had 53"
    reworded["result"]["notes"] = ["the caveat, reworded"]
    assert {d.path for d in differences(_snapshot(), reworded)} == {"text", "headline", "notes[0]"}
    assert differences(_snapshot(), reworded, wording=False) == []
    reworded["result"]["value"] = 54
    reworded["answer"]["answered_by"] = "refused"
    assert [(d.stage, d.path) for d in differences(_snapshot(), reworded, wording=False)] == [("result", "value"), ("answer", "answered_by")]
    assert set(WORDING) == set(STAGES)


def test_a_difference_prints_on_one_line() -> None:
    assert Difference("result", "games[1].points", "changed", 22, 23).line() == "result: games[1].points changed: 22 -> 23"
    assert len(Difference("answer", "text", "changed", "x" * 500, "y").line()) < 250


def _agent(tmp_path: Path) -> Agent:
    import duckdb

    db_path = tmp_path / "test.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    con.execute("CREATE TABLE teams (team_id VARCHAR, abbreviation VARCHAR, display_name VARCHAR)")
    con.close()
    return Agent(str(db_path), tmp_path / "out", history_dir=tmp_path / ".history", trace=lambda _line: None)


def test_the_agent_keeps_the_reading_it_answered_from_and_forgets_it_on_the_next_question(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("association.query.compose.answer", lambda ctx, reading, trace=None, declined=None, planned=None: Reply(data={"value": 1}, answer="templated"))
    agent = _agent(tmp_path)
    answer = ask_routed(agent, "who scored the most points", slots_route("leaderboard", {"stat": "points"}))
    assert agent.reading is not None and agent.reading.intent == "leaderboard"
    record = snapshot(agent.reading, answer, planned=agent.planned)
    with pytest.raises(ValueError, match="needs its planning"):
        snapshot(agent.reading, answer)
    assert record["reading"]["scope"]["stat"] == "points" and record["result"] == {"value": 1}
    # Refused before anything read it: the last question's Reading is not this one's.
    agent.ask("Tatum rec")
    assert agent.reading is None
