"""The line and the companions, typed: the one tagger that reads the lines on a stat a question keeps the subject's
games past (:func:`association.query.line.read_lines`) and the one that
reads a line in a quarter or half (:func:`association.query.line.read_period_line`),
the characters each claims, the typed values' doors and projections
(:class:`association.query.reading.Line`,
:class:`association.query.reading.Companion`), the companions the subject
reading hands the grammar's rules and the taggers with their claims, the
cells and what a decline says of them, the tables declaring each once, and
the behavioral check that applying each cell on its relation changes the
result.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import duckdb
import pytest
from test_conditions import BROWN, TATUM, league, presence_split  # noqa: F401 - the league fixture and the split's reader, imported by name
from test_period_relation import SEASON as PERIOD_SEASON
from test_period_relation import con as period_con  # noqa: F401 - the plays fixture, imported by name
from test_readers import pg_ctx  # noqa: F401 - the player relation's fixture, imported by name

from association.query import lexicon
from association.query.answer import AnswerContext
from association.query.compose.core import Query, compile_query, rows_of
from association.query.compose.plan import cells_stated
from association.query.entities import names_of
from association.query.line import LinesRead, read_lines, read_period_line, threshold_named
from association.query.lines import measure_filters, phrase_line, relation_lines, threshold_line, threshold_of
from association.query.parse import read_route
from association.query.player_games import Narrowed, aggregate_sql
from association.query.player_relation import RELATION_SCOPING, RELATION_SCOPING_EXCLUDED, _apply_period_condition, period_lines
from association.query.point import THRESHOLD_ASKS, lines_under
from association.query.reading import (
    GAME_HIGHS,
    GAMES_COUNTED,
    LINE_RECORD,
    LINE_RUNS,
    PLAYER_LINE,
    PLAYER_LOG,
    PLAYER_RANKING,
    PLAYER_SPLITS,
    PRESENCE_SPLIT,
    TEAM_RANKING,
    Aggregate,
    Cause,
    Claim,
    Companion,
    Filtered,
    Line,
    LineOp,
    Measure,
    Period,
    PointRefused,
    PointShape,
    Reading,
    ScopeError,
    Span,
    cell_facts,
    cell_set,
    unhonored_cells,
)
from association.query.reading import Subject as Who
from association.query.subject import read_subject
from association.query.team_relation import TEAM_RELATION_SCOPING, TEAM_RELATION_SCOPING_EXCLUDED

#: Names that are no cell of the line's or the companions' family (their cells are ``line``, ``period_line``
#: and ``companion``): none may be a cell name.
_NOT_CELL_NAMES = ("threshold", "above", "below", "period_condition", "with_player", "without", "conditions")


def _shape(line: Line) -> tuple[str | None, str, int | float | bool, str, bool, bool]:
    return (line.measure, line.op, line.value, line.as_typed, line.keyed, line.narrows)


# ---------------- the tagger: words to Lines ----------------


@pytest.mark.parametrize(
    ("question", "shape", "expected", "stat"),
    [
        # A reader whose shape is a line reads a bare "N stat" as its own (keyed) line.
        ("how many 30 point games did maxey have", GAMES_COUNTED, [("points", ">=", 30, "30 point", True, False)], None),
        ("warriors record when curry has 30 or more points", LINE_RECORD, [("points", ">=", 30, "30 or more points", True, False)], "points"),
        # "scores N": the verb names the stat, whatever the model filed.
        ("76ers record when maxey scores 30", LINE_RECORD, [("points", ">=", 30, "scores 30", True, False)], "points"),
        # Fouling out is fouls at six, the count's own line.
        ("how many times has embiid fouled out", GAMES_COUNTED, [("fouls", ">=", 6, "fouled out", True, False)], None),
        # Two or more "N+ stat" pairs on one game narrow the games together, on every reader; under a count the first is the count's own too.
        ("20+ point 5+ assist games for jokic", PLAYER_LOG, [("points", ">=", 20, "20+ point", False, True), ("assists", ">=", 5, "5+ assist", False, True)], None),
        ("most 20+ point 5+ assist games", GAMES_COUNTED, [("points", ">=", 20, "20+ point", True, True), ("assists", ">=", 5, "5+ assist", False, True)], None),
        # A phrase kept under or over a number is a line the relation narrows by, on every reader.
        ("sga games with under 14 fta", GAMES_COUNTED, [("freeThrowsAttempted", "<", 14, "under 14 fta", False, True)], None),
        ("jokic games with at most 5 turnovers", PLAYER_LOG, [("turnovers", "<=", 5, "at most 5 turnovers", False, True)], None),
        ("curry games with 25 minutes", PLAYER_LOG, [("minutes", ">=", 25, "with 25 minutes", False, True)], None),
        # "N or more minutes" and the lead "playing" are the same floor, on every reader (#374).
        ("Victor wembanyama stats when playing 20 or more minutes playoffs", PLAYER_LINE, [("minutes", ">=", 20, "playing 20 or more minutes", False, True)], None),
        ("wemby game log with 30 or more minutes", PLAYER_LOG, [("minutes", ">=", 30, "with 30 or more minutes", False, True)], None),
        ("wemby splits playing 30+ minutes", PLAYER_SPLITS, [("minutes", ">=", 30, "playing 30+ minutes", False, True)], None),
        ("wemby stats playing at least 30 minutes", PLAYER_LINE, [("minutes", ">=", 30, "playing at least 30 minutes", False, True)], None),
        ("wemby stats playing 30 minutes", PLAYER_LINE, [("minutes", ">=", 30, "playing 30 minutes", False, True)], None),
        # "over N minutes" and "more than N minutes" keep the games OVER the number, not at it.
        ("collin murray-boyles stats this season when playing over 26 minutes", PLAYER_LINE, [("minutes", ">", 26, "playing over 26 minutes", False, True)], None),
        ("wemby game log with more than 30 minutes", PLAYER_LOG, [("minutes", ">", 30, "with more than 30 minutes", False, True)], None),
        ("wemby game log with no more than 30 minutes", PLAYER_LOG, [("minutes", "<=", 30, "no more than 30 minutes", False, True)], None),
        # Under a record over a line, a floor and no pair beside it is the record's own line - which a narrowing would empty.
        ("victor wembanyama record when playing 30 or more minutes", LINE_RECORD, [("minutes", ">=", 30, "playing 30 or more minutes", True, False)], None),
        # One "N+ stat" pair on a reader whose shape is not a line is read by nothing.
        ("How many 10+ point leads did the Sacramento Kings have", TEAM_RANKING, [], None),
        # ... but on a reader of a player's games one line stated outright narrows them (#357), "at least N stat"
        # as well as "N+ stat"; a bare "N stat" there stays unread ("harden 61 points").
        ("lebron game log with 20+ points this season", PLAYER_LOG, [("points", ">=", 20, "20+ points", False, True)], None),
        ("anthony edwards log with at least 50 points", PLAYER_LOG, [("points", ">=", 50, "50 points", False, True)], None),
        # A player's line is not among them ("lebron 20+ points this season" can ask whether he averages it).
        ("lebron 20+ points per game this season", PLAYER_LINE, [], None),
        # ... unless the words set the line on the games, however it is stated (#357): his line, log or splits over
        # the games holding it.
        ("2 threes in games Jamal Murray played", PLAYER_LINE, [("threePointFieldGoalsMade", ">=", 2, "2 threes", False, True)], None),
        ("lebron stats in games with 30 points", PLAYER_LINE, [("points", ">=", 30, "30 points", False, True)], None),
        ("lebron 30 point games log", PLAYER_LOG, [("points", ">=", 30, "30 point", False, True)], None),
        ("lebron splits in games with 10 assists", PLAYER_SPLITS, [("assists", ">=", 10, "10 assists", False, True)], None),
        ("harden 61 points", PLAYER_LOG, [], None),
        ("harden 61 points", PLAYER_LINE, [], None),
        # "min N stat" is the floor "at least N stat" states.
        ("centers min 2 threes v bos gamelog 2024", PLAYER_LOG, [("threePointFieldGoalsMade", ">=", 2, "2 threes", False, True)], None),
        # A second and third bare line under a count are read, keyed on nothing and narrowing nothing: the league's multi-line listing alone applies them.
        (
            "most playoff games with 46 points 6 assists 6 rebounds",
            GAMES_COUNTED,
            [("points", ">=", 46, "46 points", True, False), ("assists", ">=", 6, "6 assists", False, False), ("rebounds", ">=", 6, "6 rebounds", False, False)],
            "points",
        ),
        # A line stated over or at a number by its lead, on any box-score stat (ISSUES.md #371): read where a line under
        # a number is, the pair inside it ("40 points" of "more than 40 points") no line of its own.
        ("kobe bryant career games with more than 40 points", PLAYER_LOG, [("points", ">", 40, "more than 40 points", False, True)], None),
        (
            "lebron game log with over 30 points and no more than 5 turnovers",
            PLAYER_LOG,
            [("points", ">", 30, "over 30 points", False, True), ("turnovers", "<=", 5, "no more than 5 turnovers", False, True)],
            None,
        ),
        ("towns games with above 9 rebounds", PLAYER_LOG, [("rebounds", ">", 9, "above 9 rebounds", False, True)], None),
        ("LeBron James PPG in games with exactly 25 field goal attempts", PLAYER_LINE, [("fieldGoalsAttempted", "=", 25, "exactly 25 field goal attempts", False, True)], None),
        ("LeBron James stats in games with FGA = 25", PLAYER_LOG, [("fieldGoalsAttempted", "=", 25, "fga = 25", False, True)], None),
        ("how many games did lebron have exactly 30 points", GAMES_COUNTED, [("points", "=", 30, "exactly 30 points", False, True)], None),
        # ... under a shape that is a line, the line stated over a number is the shape's own, at or over the next number.
        ("how many games has lebron scored more than 40 points", GAMES_COUNTED, [("points", ">=", 41, "more than 40 points", True, False)], None),
        ("longest streak of games with more than 30 points for luka", LINE_RUNS, [("points", ">=", 31, "more than 30 points", True, False)], None),
        # ... a percentage is read as the line it is, with no column (refused by name, below).
        ("kobe bryant career games with more than 40 field goal percentage", PLAYER_LOG, [(None, ">", 40, "more than 40 field goal percentage", False, True)], None),
        # A line at or over a number stated outright on a stat the threshold spellings do not hold (ISSUES.md #357);
        # a threshold spelling's pair stays the pair's, and words naming no stat ("30+ feet") are no line.
        ("jalen suggs game log 15+ fga", PLAYER_LOG, [("fieldGoalsAttempted", ">=", 15, "15+ fga", False, True)], None),
        ("lebron game log with 15 or more field goal attempts", PLAYER_LOG, [("fieldGoalsAttempted", ">=", 15, "15 or more field goal attempts", False, True)], None),
        ("games with 13+ free throw attempts by the 76ers", PLAYER_LOG, [("freeThrowsAttempted", ">=", 13, "13+ free throw attempts", False, True)], None),
        ("lebron game log with 20+ points this season", PLAYER_LOG, [("points", ">=", 20, "20+ points", False, True)], None),
        ("tyrese haliburton shot chart from 30+ feet", PLAYER_LOG, [], None),
        ("Most 10+ regular season games", PLAYER_LOG, [], None),
        # ... and not on a ranking of players' seasons, where it is a season's line; nor an average, a count of games or
        # a lead glued to its number.
        ("players who made exactly 129 three pointers in the NBA in 2025-26", PLAYER_RANKING, [], None),
        ("who averaged over 25 points in 2024", PLAYER_LOG, [], None),
        ("lebron stats over 10 games", PLAYER_LOG, [], None),
        ("kobe stats over1346 games", PLAYER_LOG, [], None),
        # A rate per N minutes, and the last minutes of a game, are no floor of minutes (ISSUES.md #388).
        ("jayson tatum per 36 minutes stats", PLAYER_LINE, [], None),
        ("field goal percentage last 5 minutes of a nba game", PLAYER_LOG, [], None),
        # "N or fewer"/"N or less" is a line at or under the number (ISSUES.md #325).
        ("warriors' record last season when committing 10 or fewer turnovers", PLAYER_LOG, [("turnovers", "<=", 10, "10 or fewer turnovers", False, True)], None),
        ("lebron games with 12 or less fga", PLAYER_LOG, [("fieldGoalsAttempted", "<=", 12, "12 or less fga", False, True)], None),
        ("jokic stats", PLAYER_LINE, [], None),
        # "2pt", "2-point", "2 point" name the two-point shot, as "3 pt" does the three, and a number after "top" is a
        # ranking's count: no line, even under a shape that is one.
        ("sga 2pt percentage this season", GAMES_COUNTED, [], None),
        ("display lebron's 2-point shooting percentage over the last 5 years", GAMES_COUNTED, [], None),
        ("Damian Lillard 2 point attempts", GAMES_COUNTED, [], None),
        ("top 20 rebound leaders this season", GAMES_COUNTED, [], None),
        ("how many games with 2 points did he have", GAMES_COUNTED, [("points", ">=", 2, "2 points", True, False)], None),
    ],
)
def test_the_words_read_as_lines(question: str, shape: PointShape | None, expected: list[tuple[Any, ...]], stat: str | None) -> None:
    read = lines_under(shape, read_lines(question))
    assert isinstance(read, LinesRead)
    assert [_shape(line) for line in read.lines] == expected
    assert read.stat == stat


def test_the_tagger_claims_the_characters_of_each_line_once() -> None:
    question = "most 20+ point 5+ assist games with under 14 fta"
    read = lines_under(GAMES_COUNTED, read_lines(question))
    assert len(read.claims) == len(read.lines) == 3
    assert all(claim.what == "line" and question[claim.start : claim.end].casefold() == line.as_typed for claim, line in zip(read.claims, read.lines, strict=True))
    assert [c.start for c in read.claims] == sorted(c.start for c in read.claims)


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("celtics record when allowing 100+ points", [("100+ points", "opponent", "allowing 100+ points")]),
        ("celtics record when they allow less than 100 points", [("less than 100 points", "opponent", "allow less than 100 points"), ("100 points", "opponent", "100 points")]),
        ("celtics record when they allow more than 110 points", [("more than 110 points", "opponent", "allow more than 110 points")]),
        ("celtics record when giving up 15+ threes", [("15+ threes", "opponent", "giving up 15+ threes")]),
        ("celtics record when opponents score 120+", [("score 120+", "opponent", "opponents score 120+")]),
        ("celtics record when they score 120+ points", [("120+ points", "own", "120+ points")]),
    ],
)
def test_a_line_after_a_given_up_verb_is_on_the_opponents_figure(question: str, expected: list[tuple[str, str, str]]) -> None:
    """#426: "celtics record when allowing 100+ points" reads the line on
    the opponents' points, not the Celtics' own. A line right
    after a given-up verb or "opponents" is on the opponents' figure
    (``Line.whose``), and its claim takes the words that make it so; a pair
    read inside such a line is theirs too."""
    read = lines_under(LINE_RECORD, read_lines(question))
    found = sorted(((line.as_typed, line.whose, question[claim.start : claim.end]) for line, claim in zip(read.lines, read.claims, strict=True)), key=lambda each: question.index(each[2]))
    assert found == sorted(expected, key=lambda each: question.index(each[2]))


def test_a_line_on_the_opponents_figure_is_a_cell_only_a_teams_record_over_a_line_reads() -> None:
    """ISSUES.md #426: a line on the opponents' figure sets the
    ``opponent_line`` cell, which the team's record over a line takes and
    no other shape of either relation honors - so it is refused, never read
    as the subject's own line."""
    line = Line(measure="points", value=100, as_typed="100+ points", keyed=True, whose="opponent")
    scope = Filtered(lines=(line,))
    assert cell_set(scope, "opponent_line") and not cell_set(Filtered(lines=(replace(line, whose="own"),)), "opponent_line")
    assert "opponent_line" not in RELATION_SCOPING and "opponent_line" not in TEAM_RELATION_SCOPING
    assert "opponent_line" in TEAM_RELATION_SCOPING_EXCLUDED[PointShape("team_games", "split", "line")].taken
    assert cell_facts(scope, "opponent_line")["lines"] == [{"as_typed": "100+ points", "measure": "points", "op": ">=", "value": 100, "period": None, "whose": "opponent"}]
    with pytest.raises(ScopeError):
        Line(measure="points", value=30, per="season", whose="opponent")


def test_threshold_named_is_what_the_grammars_rules_ask() -> None:
    assert threshold_named("how many 30 point games did maxey have") == 30
    assert threshold_named("76ers record when maxey scores 30") == 30
    assert threshold_named("how many times has embiid fouled out") == lexicon.FOUL_OUT_THRESHOLD == 6
    assert threshold_named("jokic stats") is None
    assert {GAMES_COUNTED, LINE_RECORD, LINE_RUNS, GAME_HIGHS} == THRESHOLD_ASKS


def test_a_line_in_a_quarter_is_read_by_the_parser_with_its_claim() -> None:
    question = "jokic's assists after making one three in the first quarter"
    found = read_period_line(question)
    assert found is not None
    line, claim = found
    assert line == Line(measure="threePointFieldGoalsMade", op="=", value=1, period=Period(number=1), as_typed="after making one three in the first quarter")
    assert claim == Claim(16, 59, "line") and question[claim.start : claim.end] == "after making one three in the first quarter"
    assert read_period_line("jokic assists per game") is None


# ---------------- the typed value: the door, the projection, the cells ----------------


def test_a_decline_names_each_companion_by_his_name_side_and_role() -> None:
    """What a decline names of the companion cell: each companion by his
    name, side and role, in the order the reading holds them."""
    absent = Reading(companions=(Companion(player="Jayson Tatum", predicate="absent"), Companion(player="Jaylen Brown")))
    assert cell_facts(absent, "companion")["companions"] == [
        {"player": "Jayson Tatum", "side": "own", "predicate": "absent"},
        {"player": "Jaylen Brown", "side": "own", "predicate": "played"},
    ]


def test_a_line_or_a_companion_the_relation_could_not_read_is_refused() -> None:
    with pytest.raises(ScopeError, match="op"):
        Line(measure="points", op="!=", value=3)  # type: ignore[arg-type]
    with pytest.raises(ScopeError, match="whole number"):
        Line(measure="points", value=3.5)
    with pytest.raises(ScopeError, match="reached"):
        Companion(player="X", predicate="started", line=Line(measure="points", value=3))
    with pytest.raises(ScopeError, match="reached"):
        Companion(player="X", predicate="reached")
    with pytest.raises(ScopeError, match="needs a player"):
        Companion(player=" ", predicate="absent")
    with pytest.raises(ScopeError, match="no predicate"):
        phrase_line("under 25 years old", below=True).as_predicate()
    assert Line(measure="points", value=30).as_predicate() == ("points", ">=", 30)


def test_the_cells_and_what_a_decline_says_of_them() -> None:
    assert {"line", "period_line"} == Line.CELLS and {"companion"} == Companion.CELLS
    keyed = Reading(lines=(Line(measure="points", value=30, as_typed="30 points", keyed=True),), measure=Measure(key="points", as_typed="points"))
    assert keyed.cells() == frozenset() and threshold_of(keyed) == 30 and _shape(threshold_line(keyed) or Line(measure=None))[4]  # the shape's own line sets no cell
    below = Reading(lines=(Line(measure="freeThrowsAttempted", op="<", value=14, as_typed="under 14 fta", narrows=True),))
    assert below.cells() == {"line"} and cell_set(below, "line") and unhonored_cells(below, frozenset()) == ["line"]
    assert unhonored_cells(below, frozenset({"line"})) == []
    above = Reading(lines=(Line(measure="freeThrowsAttempted", op="<", value=14, as_typed="under 14 fta", narrows=True), Line(measure="minutes", value=25, as_typed="with 25 minutes", narrows=True)))
    assert unhonored_cells(above, frozenset()) == ["line"] and [line["op"] for line in cell_facts(above, "line")["lines"]] == ["<", ">="]
    quarter = Reading(lines=(Line(measure="points", value=10, period=Period(number=1)),))
    assert quarter.cells() == {"period_line"} and unhonored_cells(quarter, frozenset()) == ["period_line"] and period_lines(quarter) == list(quarter.lines)
    assert cell_facts(quarter, "period_line")["lines"][0]["period"] == {"number": 1, "half": False}
    without = Reading(companions=(Companion(player="Jayson Tatum", predicate="absent"),))
    assert without.cells() == {"companion"} and unhonored_cells(without, frozenset()) == ["companion"]
    started = Reading(companions=(Companion(player="Joel Embiid", predicate="started"), Companion(player="Paul George", predicate="absent")))
    assert unhonored_cells(started, frozenset({"companion"})) == []
    assert unhonored_cells(Reading(lines=(Line(measure="freeThrowsAttempted", op="<", value=14, as_typed="under 14 fta", narrows=True),), split="home_away"), frozenset()) == ["line", "split"]


def test_the_relation_tables_declare_the_cells_once_and_by_the_cell_names() -> None:
    assert Line.CELLS | Companion.CELLS <= RELATION_SCOPING
    assert cells_stated(PointShape("player_games", "rows", "date")) >= Line.CELLS | Companion.CELLS
    assert "companion" in cells_stated(PointShape("team_games", "split", "presence"))  # the with/without split: the team relation's one cell its words state
    # A team's log, splits and record over a line take a companion and a line beyond the team relation's table
    # (their rows' `taken`), to refuse them by name; the team relation's table declares neither.
    assert not (Line.CELLS | Companion.CELLS) & TEAM_RELATION_SCOPING
    assert {"companion", "line"} <= TEAM_RELATION_SCOPING_EXCLUDED[PointShape("team_games", "rows", "date")].taken
    for table in (RELATION_SCOPING_EXCLUDED, TEAM_RELATION_SCOPING_EXCLUDED):
        assert all(not set(_NOT_CELL_NAMES) & (set(row.unstated) | row.taken | row.refused) for row in table.values()), "an exclusion names a name that is no cell"
    from association.query import compose

    assert all(not set(_NOT_CELL_NAMES) & cells_stated(shape) for shape in compose._ROUTES)
    assert not set(_NOT_CELL_NAMES) & (RELATION_SCOPING | TEAM_RELATION_SCOPING)


# ---------------- the relation's reading of the lines ----------------


def test_relation_lines_are_the_narrowing_ones_in_the_readings_order() -> None:
    scope = Reading(
        lines=(
            Line(measure="points", value=30, as_typed="30 points", keyed=True),
            Line(measure="freeThrowsAttempted", op="<", value=14, as_typed="under 14 fta", narrows=True),
            Line(measure="minutes", value=25, as_typed="with 25 minutes", narrows=True),
        ),
        measure=Measure(key="points", as_typed="points"),
    )
    assert [line.as_typed for line in relation_lines(scope)] == ["under 14 fta", "with 25 minutes"]
    assert [(f.column, f.op, f.value, f.label) for f in measure_filters(scope)] == [("freeThrowsAttempted", "<", 14, "under 14 free throw attempts"), ("minutes", ">=", 25, "at least 25 minutes")]
    pairs = lines_under(GAMES_COUNTED, read_lines("most 20+ point 5+ assist games"))
    assert [line.as_typed for line in relation_lines(Reading(lines=pairs.lines))] == ["20+ point", "5+ assist"] and threshold_of(Reading(lines=pairs.lines)) == 20
    with pytest.raises(PointRefused, match="names no box-score stat"):
        measure_filters(Reading(lines=(Line(measure=None, op="<", value=25, as_typed="under 25 years old", narrows=True),)))
    # A line on a percentage is refused by what it names (ISSUES.md #371): no game's box score holds one.
    percentages: tuple[tuple[str, LineOp, str], ...] = (("more than 40 field goal percentage", ">", "over"), ("under 40 fg%", "<", "under"), ("exactly 50 fg%", "=", "at"))
    for typed, op, side in percentages:
        with pytest.raises(PointRefused) as refused:
            measure_filters(Reading(lines=(Line(measure=None, op=op, value=40, as_typed=typed, narrows=True),)))
        assert refused.value.cause == Cause(kind="line_on_a_percentage", facts={"phrase": typed, "side": side})


# ---------------- the companions: one reader, with its claims ----------------


def test_the_subject_reading_reads_the_companions_and_claims_their_phrase(league: AnswerContext) -> None:  # noqa: F811 - the fixture
    question = "jaylen brown game log without jayson tatum this season"
    subject = read_subject(names_of(league.con), question, PLAYER_LOG, Reading(who=Who(kind="player", players=("Jaylen Brown",))))
    assert subject.conditions == (Companion(player="Jayson Tatum", predicate="absent"),)
    # The phrase's claim cut to the words the companion reader needed
    # (span.needed): either of "jayson" and "tatum" names him alone, so the keyword is the one
    # word it cannot do without; and the season the names are settled in is the reading's too.
    assert [(c.what, question[c.start : c.end]) for c in subject.claims] == [("companion", "without"), ("season", "season")]
    reached = read_subject(names_of(league.con), "celtics record when jayson tatum scores 20+ points", LINE_RECORD, Reading(who=Who(kind="team", teams=("Boston Celtics",))))
    assert reached.conditions == (Companion(player="Jayson Tatum", predicate="reached", line=Line(measure="points", value=20, as_typed="20+ points")),)
    # The companions reach the Reading typed, through the one writer, and ride the route's claims.
    route = read_route(names_of(league.con), question, ["Jaylen Brown", "Jayson Tatum"], "")
    reading = route
    assert reading.companions == (Companion(player="Jayson Tatum", predicate="absent"),) and reading.who.player == "Jaylen Brown"
    assert any(c.what == "companion" for c in reading.claims)


def test_a_count_over_several_lines_reads_the_first_as_the_points_not_the_models_stat(league: AnswerContext) -> None:  # noqa: F811 - the fixture
    """ "most games with 20 pt,s 10 reb, 5 ast" counts games with 20+ points,
    not the model's "rebounds": the lines are read once, by the tagger, past
    the comma, and the point's predicate is the first."""
    question = "most games with 20 pt,s 10 reb, 5 ast"
    route = read_route(names_of(league.con), question, [], "rebounds")
    reading = route
    assert reading.asked == GAMES_COUNTED and reading.has_point
    assert reading.predicates[0][:2] == ("points", ">=") and reading.predicates[0][2] == 20
    assert [_shape(line)[:3] for line in reading.lines] == [("points", ">=", 20), ("rebounds", ">=", 10), ("assists", ">=", 5)]


# ---------------- applying each cell changes the result ----------------


def _games(ctx: AnswerContext, scope: Filtered) -> tuple[int, int]:
    (row,) = rows_of(ctx.con, compile_query(ctx.con, Query(**scope.filters(), skeleton="scalar", measures=["points"], aggregate=Aggregate.TOTAL)))
    return int(row["games"]), int(row["points"])


def test_the_line_cell_changes_what_the_player_relation_reads(pg_ctx: AnswerContext) -> None:  # noqa: F811 - the fixture
    assert _games(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)))) == (3, 45)  # e1 (30 min, 10), e2 (32, 20), e3 (28, 15)
    assert _games(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), lines=(phrase_line("with 30 minutes", below=False),))) == (2, 30)
    assert _games(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), lines=(phrase_line("under 30 minutes", below=True),))) == (1, 15)
    pairs = lines_under(PLAYER_LOG, read_lines("20+ point 5+ assist games")).lines
    assert _games(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), lines=pairs)) == (1, 20)  # e2 alone holds both


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
    every = _games(league, Reading(who=Who(kind="player", players=("Jaylen Brown",))))
    without = _games(league, Reading(who=Who(kind="player", players=("Jaylen Brown",)), companions=(Companion(player="Jayson Tatum", predicate="absent"),)))
    with_tatum = _games(league, Reading(who=Who(kind="player", players=("Jaylen Brown",)), companions=(Companion(player="Jayson Tatum"),)))
    assert every[0] > without[0] > 0 and every[0] > with_tatum[0] > 0 and without[0] + with_tatum[0] <= every[0]
    assert without == (2, 43)  # e2 (Tatum DNP, 25) and e3 (Tatum not in the box score, 18)


def test_the_companion_cell_changes_what_the_team_relation_reads(league: AnswerContext) -> None:  # noqa: F811 - the fixture
    split = presence_split(league, Reading(who=Who(kind="team", teams=("Boston Celtics",)), companions=(Companion(player="Jayson Tatum"),), span=Span(season_type=2), asked=PRESENCE_SPLIT))
    assert not isinstance(split, str)
    groups = {row["group"]: row for row in split.data["splits"]["presence"]} if "presence" in split.data.get("splits", {}) else None
    assert "with" in split.text.lower() and "without" in split.text.lower()
    assert groups is None or len(groups) == 2
