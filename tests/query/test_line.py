"""The line and the companions, typed (Phase 3, step 2's fifth slice): the
one tagger that reads the lines on a stat a question keeps the subject's
games past (:func:`association.query.line.read_lines`) and the one that
reads a line in a quarter or half (:func:`association.query.line.read_period_line`),
the characters each claims, the typed values' doors and projections
(:class:`association.query.reading.Line`,
:class:`association.query.reading.Companion`), the companions the subject
reading hands the stages with their claims, the cells and the slot names a
decline still says, the tables declaring each once, and the behavioral
check contract 4 asks for: applying each cell on its relation changes the
result.
"""

from __future__ import annotations

from typing import Any

import duckdb
import pytest
from test_conditions import BROWN, TATUM, league, with_without  # noqa: F401 - the league fixture and the split's reader, imported by name
from test_period_relation import SEASON as PERIOD_SEASON
from test_period_relation import con as period_con  # noqa: F401 - the plays fixture, imported by name
from test_templates import pg_ctx  # noqa: F401 - the player relation's fixture, imported by name

from association.query import lexicon
from association.query.answer import AnswerContext
from association.query.compose.core import Query, compile_query, rows_of
from association.query.compose.plan import STATED_SCOPING
from association.query.line import THRESHOLD_INTENTS, LineContext, LinesRead, read_lines, read_period_line, threshold_named
from association.query.lines import measure_filters, phrase_line, relation_lines, threshold_line, threshold_of
from association.query.parse import read_route, reading_from_route
from association.query.player_games import Narrowed, aggregate_sql
from association.query.player_relation import RELATION_SCOPING, RELATION_SCOPING_EXCLUDED, _apply_period_condition, period_lines, relation_scoping
from association.query.reading import (
    Claim,
    Companion,
    Line,
    Period,
    PointRefused,
    PointShape,
    Reading,
    Scope,
    ScopeError,
    cell_set,
    companion_slot_names,
    line_slot_names,
    slot_names_set,
    unhonored_cells,
)
from association.query.subject import read_subject
from association.query.team_relation import TEAM_RELATION_SCOPING, TEAM_RELATION_SCOPING_EXCLUDED

#: The slot names the family was carried under until this slice: none may come back as a cell name.
_SLOT_ERA = ("threshold", "above", "below", "period_condition", "with_player", "without", "conditions")


def _shape(line: Line) -> tuple[str | None, str, int | bool, str, bool, bool]:
    return (line.measure, line.op, line.value, line.as_typed, line.keyed, line.narrows)


# ---------------- the tagger: words to Lines ----------------


@pytest.mark.parametrize(
    ("question", "intent", "expected", "stat"),
    [
        # A reader whose shape is a line reads a bare "N stat" as its own (keyed) line.
        ("how many 30 point games did maxey have", "threshold_count", [("points", ">=", 30, "30 point", True, False)], None),
        ("warriors record when curry has 30 or more points", "record_when", [("points", ">=", 30, "30 or more points", True, False)], "points"),
        # "scores N": the verb names the stat, whatever the model filed.
        ("76ers record when maxey scores 30", "record_when", [("points", ">=", 30, "scores 30", True, False)], "points"),
        # Fouling out is fouls at six, the count's own line.
        ("how many times has embiid fouled out", "threshold_count", [("fouls", ">=", 6, "fouled out", True, False)], None),
        # Two or more "N+ stat" pairs on one game narrow the games together, on every reader; under a count the first is the count's own too.
        ("20+ point 5+ assist games for jokic", "game_log", [("points", ">=", 20, "20+ point", False, True), ("assists", ">=", 5, "5+ assist", False, True)], None),
        ("most 20+ point 5+ assist games", "threshold_count", [("points", ">=", 20, "20+ point", True, True), ("assists", ">=", 5, "5+ assist", False, True)], None),
        # A phrase kept under or over a number is a line the relation narrows by, on every reader.
        ("sga games with under 14 fta", "threshold_count", [("freeThrowsAttempted", "<", 14, "under 14 fta", False, True)], None),
        ("jokic games with at most 5 turnovers", "game_log", [("turnovers", "<=", 5, "at most 5 turnovers", False, True)], None),
        ("curry games with 25 minutes", "game_log", [("minutes", ">=", 25, "with 25 minutes", False, True)], None),
        # One "N+ stat" pair on a reader whose shape is not a line is read by nothing - the stages' rule, measured (a team's line carried no threshold).
        ("How many 10+ point leads did the Sacramento Kings have", "team_leaderboard", [], None),
        # ... and on a log the same (ISSUES.md, "A single line on a game log is read by nothing": the measure slice's).
        ("lebron game log with 20+ points this season", "game_log", [], None),
        # A second and third bare line under a count are read, keyed on nothing and narrowing nothing: the league's multi-line listing alone applies them.
        (
            "most playoff games with 46 points 6 assists 6 rebounds",
            "threshold_count",
            [("points", ">=", 46, "46 points", True, False), ("assists", ">=", 6, "6 assists", False, False), ("rebounds", ">=", 6, "6 rebounds", False, False)],
            None,
        ),
        ("jokic stats", "player_stat", [], None),
    ],
)
def test_the_words_read_as_lines(question: str, intent: str, expected: list[tuple[Any, ...]], stat: str | None) -> None:
    read = read_lines(question, LineContext(intent=intent))
    assert isinstance(read, LinesRead)
    assert [_shape(line) for line in read.lines] == expected
    assert read.stat == stat


def test_the_tagger_claims_the_characters_of_each_line_once() -> None:
    question = "most 20+ point 5+ assist games with under 14 fta"
    read = read_lines(question, LineContext(intent="threshold_count"))
    assert len(read.claims) == len(read.lines) == 3
    assert all(claim.what == "line" and question[claim.start : claim.end].casefold() == line.as_typed for claim, line in zip(read.claims, read.lines, strict=True))
    assert [c.start for c in read.claims] == sorted(c.start for c in read.claims)


def test_threshold_named_is_what_the_intent_stages_ask() -> None:
    assert threshold_named("how many 30 point games did maxey have") == 30
    assert threshold_named("76ers record when maxey scores 30") == 30
    assert threshold_named("how many times has embiid fouled out") == lexicon.FOUL_OUT_THRESHOLD == 6
    assert threshold_named("jokic stats") is None
    assert {"threshold_count", "record_when", "streak", "single_game_high"} == THRESHOLD_INTENTS


def test_a_line_in_a_quarter_is_read_by_the_parser_with_its_claim() -> None:
    question = "jokic's assists after making one three in the first quarter"
    found = read_period_line(question)
    assert found is not None
    line, claim = found
    assert line == Line(measure="threePointFieldGoalsMade", op="=", value=1, period=Period(number=1), as_typed="after making one three in the first quarter")
    assert claim == Claim(16, 59, "line") and question[claim.start : claim.end] == "after making one three in the first quarter"
    assert read_period_line("jokic assists per game") is None


# ---------------- the typed value: the door, the projection, the cells ----------------


@pytest.mark.parametrize(
    "slots",
    [
        {"stat": "points", "threshold": 30},
        {"above": ["with 25 minutes"]},
        {"below": ["under 14 fta", "at most 5 turnovers"]},
        {"period_condition": {"stat": "threePointFieldGoalsMade", "threshold": 1, "op": "=", "period": 1}},
        {"period_condition": {"stat": "points", "threshold": 10, "op": ">=", "half": 2}},
        {"without": ["Jayson Tatum"]},
        {"conditions": [{"player": "Joel Embiid", "side": "own", "predicate": "started"}]},
        {"conditions": [{"player": "Tyrese Maxey", "side": "own", "predicate": "reached", "stat": "points", "threshold": 20}]},
        {"conditions": [{"player": "Joel Embiid", "side": "opponent", "predicate": "played"}], "without": ["Paul George"]},
    ],
)
def test_the_slots_round_trip_through_the_typed_values(slots: dict[str, Any]) -> None:
    scope = Scope.from_slots(slots)
    assert scope.to_slots() == slots
    assert Scope.from_slots(scope.to_slots()) == scope


def test_the_splits_teammates_project_under_its_own_slot_and_as_conditions_elsewhere() -> None:
    scope = Scope.from_slots({"with_player": ["Jayson Tatum", "Jaylen Brown"]})
    assert scope.companions == (Companion(player="Jayson Tatum"), Companion(player="Jaylen Brown"))
    assert scope.to_slots(split_by_presence=True) == {"with_player": ["Jayson Tatum", "Jaylen Brown"]}
    assert scope.to_slots() == {"conditions": [{"player": "Jayson Tatum", "side": "own", "predicate": "played"}, {"player": "Jaylen Brown", "side": "own", "predicate": "played"}]}
    # A teammate who played beside a role the same name holds: both, as the two slots held them (the split divides by the one and narrows by the other).
    both = Scope.from_slots({"with_player": ["Jayson Tatum"], "conditions": [{"player": "Jayson Tatum", "side": "own", "predicate": "started"}]})
    assert len(both.companions) == 2 and both.to_slots(split_by_presence=True) == {"with_player": ["Jayson Tatum"], "conditions": [{"player": "Jayson Tatum", "side": "own", "predicate": "started"}]}
    # Where one is absent the split divides by the absent ones and the teammates who played went unwritten - the slot era's silent drop, kept in the
    # projection and held typed (ISSUES.md, "A teammate who played beside an absent one on the with/without split is dropped").
    absent = Scope.from_slots({"with_player": ["Jaylen Brown"], "without": ["Jayson Tatum"]})
    assert len(absent.companions) == 2 and absent.to_slots(split_by_presence=True) == {"without": ["Jayson Tatum"]}
    assert absent.to_slots() == {"without": ["Jayson Tatum"], "conditions": [{"player": "Jaylen Brown", "side": "own", "predicate": "played"}]}
    # The Reading projects by its intent; a Query by its group.
    assert list(Reading(scope=scope, intent="with_without").projected()["scope"]["with_player"]) == ["Jayson Tatum", "Jaylen Brown"]
    assert list(Reading(scope=scope, intent="game_log").projected()["scope"]["with_player"]) == []


def test_the_projection_keeps_the_slot_era_shape() -> None:
    projected = Scope(
        player="X",
        lines=(Line(measure="points", value=30, as_typed="30 point", keyed=True), phrase_line("under 14 fta", below=True)),
        companions=(Companion(player="Jayson Tatum", predicate="absent"),),
    ).projected()
    assert (
        projected["threshold"],
        list(projected["below"]),
        list(projected["above"]),
        list(projected["without"]),
        list(projected["conditions"]),
        list(projected["with_player"]),
        projected["period_condition"],
    ) == (30, ["under 14 fta"], [], ["Jayson Tatum"], [], [], None)
    assert "lines" not in projected and "companions" not in projected and all(not isinstance(v, (Line, Companion)) for v in projected.values())
    assert set(_SLOT_ERA) <= set(projected)


def test_a_line_or_a_companion_the_relation_could_not_read_is_refused_at_the_door() -> None:
    with pytest.raises(ScopeError, match="op"):
        Line(measure="points", op="!=", value=3)  # type: ignore[arg-type]
    with pytest.raises(ScopeError, match="whole number"):
        Line(measure="points", value=3.5)  # type: ignore[arg-type]
    with pytest.raises(ScopeError, match="reached"):
        Companion(player="X", predicate="started", line=Line(measure="points", value=3))
    with pytest.raises(ScopeError, match="reached"):
        Companion(player="X", predicate="reached")
    with pytest.raises(ScopeError, match="no player"):
        Companion.from_slot({"predicate": "absent"})
    with pytest.raises(ScopeError, match="exactly one of period and half"):
        Line.from_period_slot({"stat": "points", "threshold": 1})
    with pytest.raises(ScopeError, match="no predicate"):
        phrase_line("under 25 years old", below=True).as_predicate()
    assert Line(measure="points", value=30).as_predicate() == ("points", ">=", 30)


def test_the_cells_and_the_slot_names_a_decline_still_says() -> None:
    assert {"line", "period_line"} == Line.CELLS and {"companion"} == Companion.CELLS
    keyed = Scope.from_slots({"stat": "points", "threshold": 30})
    assert keyed.cells() == frozenset() and threshold_of(keyed) == 30 and _shape(threshold_line(keyed) or Line(measure=None))[4]  # the shape's own line sets no cell, as the threshold slot never did
    below = Scope.from_slots({"below": ["under 14 fta"]})
    assert below.cells() == {"line"} and cell_set(below, "line") and line_slot_names(below, "line") == ["below"] and unhonored_cells(below, frozenset()) == ["below"]
    assert unhonored_cells(below, frozenset({"line"})) == []
    above = Scope.from_slots({"above": ["with 25 minutes"], "below": ["under 14 fta"]})
    assert line_slot_names(above, "line") == ["below", "above"] and unhonored_cells(above, frozenset()) == ["above", "below"]
    quarter = Scope.from_slots({"period_condition": {"stat": "points", "threshold": 10, "period": 1}})
    assert quarter.cells() == {"period_line"} and unhonored_cells(quarter, frozenset()) == ["period_condition"] and period_lines(quarter) == list(quarter.lines)
    without = Scope.from_slots({"without": ["Jayson Tatum"]})
    assert without.cells() == {"companion"} and companion_slot_names(without) == ["without"] and unhonored_cells(without, frozenset()) == ["without"]
    started = Scope.from_slots({"conditions": [{"player": "Joel Embiid", "predicate": "started"}], "without": ["Paul George"]})
    assert companion_slot_names(started) == ["without", "conditions"] and unhonored_cells(started, frozenset({"companion"})) == []
    assert slot_names_set(started, ("companion", "line", "split")) == ["conditions", "without"]
    assert slot_names_set(Scope.from_slots({"below": ["under 14 fta"], "split": "home_away"}), ("companion", "line", "split")) == ["below", "split"]


def test_the_relation_tables_declare_the_cells_once_and_by_the_cell_names() -> None:
    assert Line.CELLS | Companion.CELLS <= RELATION_SCOPING
    assert relation_scoping("game_log") >= Line.CELLS | Companion.CELLS
    assert "companion" in STATED_SCOPING[PointShape("team_games", "split", "presence")]  # the with/without split: the team relation's one cell its words state
    # The team compiler refuses every companion and line by name (`compose.plan._TEAM_READER_REFUSES`), so the team relation's table declares neither.
    assert not (Line.CELLS | Companion.CELLS) & TEAM_RELATION_SCOPING
    for table in (RELATION_SCOPING_EXCLUDED, TEAM_RELATION_SCOPING_EXCLUDED):
        assert all(not set(_SLOT_ERA) & set(row) for row in table.values()), "an exclusion names a slot-era name"
    assert all(not set(_SLOT_ERA) & cells for cells in STATED_SCOPING.values())
    assert not set(_SLOT_ERA) & (RELATION_SCOPING | TEAM_RELATION_SCOPING)


# ---------------- the relation's reading of the lines ----------------


def test_relation_lines_are_the_narrowing_ones_in_the_slots_order() -> None:
    scope = Scope.from_slots({"stat": "points", "threshold": 30, "above": ["with 25 minutes"], "below": ["under 14 fta"]})
    assert [line.as_typed for line in relation_lines(scope)] == ["under 14 fta", "with 25 minutes"]
    assert [(f.column, f.op, f.value, f.label) for f in measure_filters(scope)] == [("freeThrowsAttempted", "<", 14, "under 14 free throw attempts"), ("minutes", ">=", 25, "at least 25 minutes")]
    pairs = read_lines("most 20+ point 5+ assist games", LineContext(intent="threshold_count"))
    assert [line.as_typed for line in relation_lines(Scope(lines=pairs.lines))] == ["20+ point", "5+ assist"] and threshold_of(Scope(lines=pairs.lines)) == 20
    with pytest.raises(PointRefused, match="names no box-score stat"):
        measure_filters(Scope.from_slots({"below": ["under 25 years old"]}))


# ---------------- the companions: one reader, with its claims ----------------


def test_the_subject_reading_reads_the_companions_and_claims_their_phrase(league: AnswerContext) -> None:  # noqa: F811 - the fixture
    question = "jaylen brown game log without jayson tatum this season"
    subject = read_subject(league.con, question, "game_log", Scope(player="Jaylen Brown"))
    assert subject.conditions == (Companion(player="Jayson Tatum", predicate="absent"),)
    assert len(subject.claims) == 1 and subject.claims[0].what == "companion" and question[subject.claims[0].start : subject.claims[0].end] == "without jayson tatum"
    reached = read_subject(league.con, "celtics record when jayson tatum scores 20+ points", "record_when", Scope(team="Boston Celtics"))
    assert reached.conditions == (Companion(player="Jayson Tatum", predicate="reached", line=Line(measure="points", value=20, as_typed="20+ points")),)
    # The companions reach the Reading typed, through the one writer, and ride the route's claims.
    route, _, _ = read_route(league.con, question, ["Jaylen Brown", "Jayson Tatum"], "")
    reading = reading_from_route(league.con, question, route)
    assert reading.scope.companions == (Companion(player="Jayson Tatum", predicate="absent"),) and reading.scope.player == "Jaylen Brown"
    assert any(c.what == "companion" for c in reading.claims)


def test_a_count_over_several_lines_reads_the_first_as_the_points_not_the_models_stat(league: AnswerContext) -> None:  # noqa: F811 - the fixture
    """The one moved feed answer: "most games with 20 pt,s 10 reb, 5 ast" -
    the point's own re-read could not pass the comma and fell back to the
    model's "rebounds", so it counted games with 20+ rebounds. The lines
    are read once, by the tagger, and the point's predicate is the first."""
    question = "most games with 20 pt,s 10 reb, 5 ast"
    route, _, _ = read_route(league.con, question, [], "rebounds")
    reading = reading_from_route(league.con, question, route)
    assert reading.intent == "threshold_count" and reading.point is not None
    assert reading.point.predicates[0][:2] == ("points", ">=") and reading.point.predicates[0][2] == 20
    assert [_shape(line)[:3] for line in reading.scope.lines] == [("points", ">=", 20), ("rebounds", ">=", 10), ("assists", ">=", 5)]


# ---------------- contract 4: applying each cell changes the result ----------------


def _games(ctx: AnswerContext, scope: Scope) -> tuple[int, int]:
    (row,) = rows_of(ctx.con, compile_query(ctx.con, Query(scope=scope, skeleton="scalar", measures=["points"], aggregate="total")))
    return int(row["games"]), int(row["points"])


def test_the_line_cell_changes_what_the_player_relation_reads(pg_ctx: AnswerContext) -> None:  # noqa: F811 - the fixture
    assert _games(pg_ctx, Scope(player="Brandin Podziemski")) == (3, 45)  # e1 (30 min, 10), e2 (32, 20), e3 (28, 15)
    assert _games(pg_ctx, Scope(player="Brandin Podziemski", lines=(phrase_line("with 30 minutes", below=False),))) == (2, 30)
    assert _games(pg_ctx, Scope(player="Brandin Podziemski", lines=(phrase_line("under 30 minutes", below=True),))) == (1, 15)
    pairs = read_lines("20+ point 5+ assist games", LineContext(intent="game_log")).lines
    assert _games(pg_ctx, Scope(player="Brandin Podziemski", lines=pairs)) == (1, 20)  # e2 alone holds both


def test_the_period_line_cell_changes_what_the_player_relation_reads(period_con: duckdb.DuckDBPyConnection) -> None:  # noqa: F811 - the fixture
    def total(line: Line | None) -> tuple[int, int]:
        narrowed = Narrowed(base=["pgl.athlete_id = ?", "pgl.season = ?"], base_params=["1", PERIOD_SEASON])
        if line is not None:
            assert _apply_period_condition(period_con, narrowed, line) is None
        sql, params = aggregate_sql(narrowed, ["COUNT(*)", "SUM(pgl.points)"])
        row = period_con.execute(sql, params).fetchone()
        assert row is not None
        return int(row[0]), int(row[1] or 0)

    whole = total(None)
    one_three = total(Line(measure="threePointFieldGoalsMade", op=">=", value=1, period=Period(number=1), as_typed="1+ threes in the 1st quarter"))
    assert whole[0] > one_three[0] == 1 and one_three[1] == 40  # g1's first quarter holds the three; the read is the whole game's 40
    assert total(Line(measure="threePointFieldGoalsMade", op="=", value=2, period=Period(number=1)))[0] == 0


def test_the_companion_cell_changes_what_the_player_relation_reads(league: AnswerContext) -> None:  # noqa: F811 - the fixture
    every = _games(league, Scope(player="Jaylen Brown"))
    without = _games(league, Scope(player="Jaylen Brown", companions=(Companion(player="Jayson Tatum", predicate="absent"),)))
    with_tatum = _games(league, Scope(player="Jaylen Brown", companions=(Companion(player="Jayson Tatum"),)))
    assert every[0] > without[0] > 0 and every[0] > with_tatum[0] > 0 and without[0] + with_tatum[0] <= every[0]
    assert without == (2, 43)  # e2 (Tatum DNP, 25) and e3 (Tatum not in the box score, 18)


def test_the_companion_cell_changes_what_the_team_relation_reads(league: AnswerContext) -> None:  # noqa: F811 - the fixture
    split = with_without(league, Reading.from_slots({"team": "Boston Celtics", "season_type": 2, "with_player": ["Jayson Tatum"]}, intent="with_without"))
    assert not isinstance(split, str)
    groups = {row["group"]: row for row in split.data["splits"]["presence"]} if "presence" in split.data.get("splits", {}) else None
    assert "with" in split.answer.lower() and "without" in split.answer.lower()
    assert groups is None or len(groups) == 2
