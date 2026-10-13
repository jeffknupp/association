"""What a question's words name that nothing here reads, and the refusals
the words come to before any reader runs - read by the parser onto the
Reading as causes (``Reading.unsupported``, ``Reading.refused``) and said by
the planner (``compose.plan.refusal_result``). Each test pairs the refusal
with the fact that makes it safe: nothing reads the same shape (so the
refusal never shadows an answer - the answering loop says it only where the
answer side declined), and the sentence names the thing that is actually
missing.

Until Phase 3, step 0 these were ``refusals.unanswerable``'s checks and
``refusals.by_question``, which the answering loop called with the question
after the parser had settled it; the cases are the same, re-seated on the
reader's verdict and the planner's sentence."""

from __future__ import annotations

from dataclasses import replace

import duckdb
import pytest

from association.query.answer import Reply
from association.query.calendar import parse_alignment, parse_situation
from association.query.compose.plan import refusal_result
from association.query.entities import names_of, players_of, teams_of
from association.query.measure import measure_of
from association.query.parse import _reading_from_route_refused, _reading_from_route_unsupported
from association.query.reading import (
    PERIOD_LOG,
    PERIOD_RANKING,
    PLAYER_LINE,
    PLAYER_LOG,
    PLAYER_RANKING,
    PLAYER_SPLITS,
    TEAM_OUTLOOK,
    TEAM_PERIOD_TOTAL,
    TEAM_RANKING,
    Cuts,
    Line,
    Measure,
    Period,
    PointShape,
    Reading,
    Span,
    Window,
    situation_of,
)
from association.query.reading import Subject as Who
from association.query.subject import Subject, read_subject


def unanswerable(con: duckdb.DuckDBPyConnection, asked: PointShape | None, scope: Reading, question: str) -> Reply | None:
    """A test's typed reading - the filters it states, what the words ask
    (``asked``) and the subject read from the question - and the first
    thing its words name that nothing reads, as the planner says it (or
    None)."""
    reading = replace(scope, asked=asked, subject=read_subject(names_of(con), question, asked, scope))
    causes = _reading_from_route_unsupported(question, reading)
    return refusal_result(causes[0]) if causes else None


def by_question(question: str, asked: PointShape | None, con: duckdb.DuckDBPyConnection) -> Reply | None:
    """The refusal the words come to before any reader runs, as the planner
    says it (or None)."""
    cause = _reading_from_route_refused(players_of(con), teams_of(con), question, Reading(asked=asked, subject=Subject("everyone")))
    return refusal_result(cause) if cause is not None else None


@pytest.fixture
def con() -> duckdb.DuckDBPyConnection:
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    c.execute("INSERT INTO players VALUES ('1','LeBron James'),('2','Kawhi Leonard'),('3','VJ Edgecombe')")
    c.execute("CREATE TABLE teams (team_id VARCHAR, display_name VARCHAR, abbreviation VARCHAR)")
    c.execute("INSERT INTO teams VALUES ('1','Atlanta Hawks','ATL'),('2','Los Angeles Lakers','LAL')")
    return c


def test_an_answerable_question_is_not_refused(con: duckdb.DuckDBPyConnection) -> None:
    """The module says None for anything it has no shape for - the agent's
    turn is only taken where nothing here can read the question."""
    assert (
        unanswerable(con, PLAYER_LOG, Reading(who=Who(kind="player", players=("LeBron James",)), cuts=Cuts(opponent="Atlanta Hawks"), window=Window(count=5)), "lebron's last 5 vs the hawks") is None
    )
    assert (
        unanswerable(con, PERIOD_LOG, Reading(who=Who(kind="player", players=("VJ Edgecombe",)), measure=Measure(key="points", as_typed="points"), period=Period(number=1)), "vj 1st quarter points")
        is None
    )
    assert unanswerable(con, PLAYER_LINE, Reading(who=Who(kind="player", players=("LeBron James",)), cuts=Cuts(situation=situation_of("on christmas"))), "lebron on christmas") is None


def test_a_playoff_round_is_refused_naming_the_missing_label(con: duckdb.DuckDBPyConnection) -> None:
    """yardstick-v2 F165 "nba finals game log 2025": `round` is a slot no
    reader honors (the planner declines it), and the games carry no round
    label (ISSUES #10), so the agent had nothing to read either."""
    scope = Reading(who=Who(kind="team", teams=("NBA Finals",)), cuts=Cuts(round="finals"), span=Span(season=2025, season_type=3))
    refusal = unanswerable(con, PLAYER_LOG, scope, "nba finals game log 2025")
    assert refusal is not None
    assert "not labeled by playoff round" in refusal.answer and "'finals'" in refusal.answer
    assert refusal.data["refused"] == "playoff_round"


def test_an_age_is_refused_because_no_birth_date_is_on_record(con: duckdb.DuckDBPyConnection) -> None:
    """yardstick-v2 F054 "lebron ppg as an 18 year old": the calendar reader
    refuses the value (no weekday, month, holiday or "since <day>" in it),
    and the cause is the missing birth date - not "no data"."""
    assert parse_situation("18 year old") is None
    refusal = unanswerable(
        con,
        PLAYER_LINE,
        Reading(who=Who(kind="player", players=("LeBron James",)), cuts=Cuts(situation=situation_of("18 year old")), measure=Measure(key="points", as_typed="points")),
        "lebron ppg as an 18 year old",
    )
    assert refusal is not None
    assert "birth date" in refusal.answer and "'18 year old'" in refusal.answer
    # Hyphenated, as "18-year-old Lebron's ppg total" types it (ISSUES.md #290): read as the same
    # age, where until Phase 3, step 3 nothing read it and his 2026 line was answered.
    from association.query.cuts import CutsContext, read_cuts
    from association.query.point import cuts_under

    situation = cuts_under(PLAYER_LINE, read_cuts("18-year-old Lebron's ppg total", CutsContext()), split=None).cuts.situation
    assert situation is not None and situation.text == "18-year-old" and not situation.read
    hyphenated = unanswerable(
        con,
        PLAYER_LINE,
        Reading(who=Who(kind="player", players=("LeBron James",)), cuts=Cuts(situation=situation_of("18-year-old")), measure=Measure(key="points", as_typed="points")),
        "18-year-old Lebron's ppg total",
    )
    assert hyphenated is not None and "birth date" in hyphenated.answer and "'18-year-old'" in hyphenated.answer


def test_a_conference_or_division_in_no_recognized_shape_names_its_own_cause(con: duckdb.DuckDBPyConnection) -> None:
    """A conference/division word outside the shapes ``parse_alignment`` reads
    ("vs the west", "against eastern conference teams", "vs the southeast
    division", "in the west") still gets its own sentence naming what would
    be read, not the generic "not something the games are read by" one - the
    words are right and only the phrasing is not, which is a different cause
    than an unreadable value entirely.

    .. versionchanged:: 4.4.0
       A conference or division IS read now (K3-2, yardstick-v2 F055 "vs
       southeast division") - this used to refuse every shape naming one,
       "not read from the standings yet"; see
       test_an_alignment_situation_is_never_refused_here below.
    """
    refusal = unanswerable(
        con,
        PLAYER_SPLITS,
        Reading(who=Who(kind="player", players=("LeBron James",)), cuts=Cuts(situation=situation_of("the Central Division these days"))),
        "lebron in the central division these days",
    )
    assert refusal is not None
    assert "conference or division" in refusal.answer and "not in a shape this reads" in refusal.answer
    other = unanswerable(con, PLAYER_LINE, Reading(who=Who(kind="player", players=("LeBron James",)), cuts=Cuts(situation=situation_of("since returning"))), "lebron since returning")
    assert other is not None
    assert "not something the games are read by" in other.answer


def test_a_calendar_situation_is_never_refused_here(con: duckdb.DuckDBPyConnection) -> None:
    """A situation the relation reads (the K3 shapes) is the relation's to
    answer or refuse by value - this module must not claim it."""
    for situation in ("on tuesdays", "in march", "on christmas", "since january 31st"):
        assert parse_situation(situation) is not None
        assert unanswerable(con, PLAYER_LOG, Reading(who=Who(kind="player", players=("LeBron James",)), cuts=Cuts(situation=situation_of(situation))), f"lebron games {situation}") is None


def test_an_alignment_situation_is_never_refused_here(con: duckdb.DuckDBPyConnection) -> None:
    """yardstick-v2 F055 "vs southeast division": a conference or division
    situation the relation reads (K3-2) is the relation's to answer or refuse
    by value - the same discipline the calendar shapes above already keep."""
    for situation in ("vs southeast division", "vs the west", "against eastern conference teams", "in the west"):
        assert parse_alignment(situation) is not None
        assert unanswerable(con, PLAYER_SPLITS, Reading(who=Who(kind="player", players=("LeBron James",)), cuts=Cuts(situation=situation_of(situation))), f"lebron {situation}") is None


def test_a_stat_the_period_line_does_not_rebuild_is_refused(con: duckdb.DuckDBPyConnection) -> None:
    """yardstick-v2 F066-F068 "vj edgecombe 1st quarter assists by game" was
    refused here for "only points are on record" - true until the period
    relation (plan item 4) rebuilt assists, rebounds and the rest from the
    plays, and a false cause after. Now only a stat no play carries per
    period is refused, and the refusal names what IS rebuilt."""
    assert (
        unanswerable(
            con,
            PERIOD_LOG,
            Reading(who=Who(kind="player", players=("VJ Edgecombe",)), measure=Measure(key="assists", as_typed="assists"), period=Period(number=1)),
            "vj edgecombe 1st quarter assists by game",
        )
        is None
    )
    assert unanswerable(con, PERIOD_RANKING, Reading(measure=Measure(key="rebounds", as_typed="rebounds"), period=Period(number=2, half=True)), "most 2nd half rebounds") is None
    refusal = unanswerable(
        con, PERIOD_LOG, Reading(who=Who(kind="player", players=("VJ Edgecombe",)), measure=Measure(key="minutes", as_typed="minutes"), period=Period(number=1)), "vj edgecombe 1st quarter minutes"
    )
    assert refusal is not None
    assert "'minutes' is not among them" in refusal.answer and "rebounds, assists" in refusal.answer and "the 1st quarter" in refusal.answer
    half = unanswerable(
        con,
        PERIOD_LOG,
        Reading(who=Who(kind="player", players=("VJ Edgecombe",)), measure=Measure(key="plusMinus", as_typed="plusMinus"), period=Period(number=2, half=True)),
        "vj 2nd half plus minus",
    )
    assert half is not None and "the 2nd half" in half.answer
    # A shooting percentage is a ratio of two rebuilt columns, read by both
    # period templates' successors (ROADMAP step 2) - so it is not refused
    # here, for a player or a team; an advanced rate still is, and the
    # refusal names the percentages that ARE read.
    assert (
        unanswerable(
            con,
            PERIOD_LOG,
            Reading(who=Who(kind="player", players=("VJ Edgecombe",)), measure=Measure(key="threePointFieldGoalPct", as_typed="threePointFieldGoalPct"), period=Period(number=1)),
            "vj edgecombe 1st quarter 3pt percentage by game",
        )
        is None
    )
    assert (
        unanswerable(
            con,
            TEAM_PERIOD_TOTAL,
            Reading(who=Who(kind="team", teams=("Knicks",)), measure=Measure(key="freeThrowPct", as_typed="freeThrowPct"), period=Period(number=4)),
            "knicks free throw percentage in the 4th quarter",
        )
        is None
    )
    advanced = unanswerable(
        con, PERIOD_LOG, Reading(who=Who(kind="player", players=("VJ Edgecombe",)), measure=Measure(key="ts_pct", as_typed="ts_pct"), period=Period(number=1)), "vj edgecombe 1st quarter true shooting"
    )
    assert advanced is not None and "free throw percentages from them" in advanced.answer and "'ts_pct' is not among them" in advanced.answer


def test_a_player_set_against_another_is_the_second_of_two_players(con: duckdb.DuckDBPyConnection) -> None:
    """yardstick-v2 F081 "lebron vs kawhi head to head": the pair relation
    is read by ``player_matchup``. The router filed the second player as the
    opponent and a reroute (``refusals.pair_from_opponent``, then the subject
    reading's) put him back; the parser names the pair from the subject's
    kind - two players - so the reroute went with the router (ROADMAP plan
    item 6, step (d), part 3). A team opponent stays a narrowing of the one
    player's games."""
    from association.query.parse import read_route

    def parsed(question: str, names: list[str]) -> Reading:
        return read_route(names_of(con), question, names, "")

    for question in ("lebron vs kawhi head to head", "lebron vs kawhi record"):
        reading = parsed(question, ["lebron", "kawhi"])
        assert reading.intent == "player_matchup" and reading.who.players == ("LeBron James", "Kawhi Leonard") and not reading.cuts.opponent, question
    team = parsed("lebron vs the hawks", ["lebron", "hawks"])
    assert team.intent != "player_matchup" and team.who.player == "LeBron James" and team.cuts.opponent == "Atlanta Hawks"


def test_a_teams_stat_no_period_splits_is_refused_and_a_rebuilt_one_is_not(con: duckdb.DuckDBPyConnection) -> None:
    """yardstick-v2 F065 "trailblazers stats last 10 games 3 point average
    1st quarter" is answered now - the period's line rebuilds threes from the
    plays (the period relation's team half) - so only a stat neither the
    linescore nor that line holds is refused, by name."""
    refusal = unanswerable(
        con,
        TEAM_PERIOD_TOTAL,
        Reading(who=Who(kind="team", teams=("Portland Trail Blazers",)), measure=Measure(key="plusMinus", as_typed="plusMinus"), period=Period(number=1)),
        "blazers 1st quarter plus minus",
    )
    assert refusal is not None
    assert "linescore" in refusal.answer and "'plusMinus'" in refusal.answer
    for stat in ("points", "threePointFieldGoalsMade", "rebounds", "turnovers"):
        scope = Reading(who=Who(kind="team", teams=("Portland Trail Blazers",)), measure=measure_of(stat), period=Period(number=1))
        assert unanswerable(con, TEAM_PERIOD_TOTAL, scope, f"blazers 1st quarter {stat}") is None, stat


def test_a_period_used_as_a_condition_is_refused_by_name(con: duckdb.DuckDBPyConnection) -> None:
    """yardstick-v2 F062: "vj edgecombe three points made per game after
    making one three in first quarter" asks his whole-game threes over the
    games whose first quarter held one - read into the scope now; a wording
    whose line cannot be read is kept off the period templates, and this
    names what a readable line looks like instead of falling through."""
    question = "vj edgecombe three points made per game after scoring a lot in the first quarter"
    result = unanswerable(con, None, Reading(who=Who(kind="player", players=("VJ Edgecombe",)), measure=Measure(key="threePointFieldGoalsMade", as_typed="threePointFieldGoalsMade")), question)
    assert result is not None and result.data["refused"] == "period_as_condition" and "no line could be read from it" in result.answer
    # A condition the parser read is the relation's narrowing (ROADMAP step 2, #275), not this refusal.
    read = Reading(
        who=Who(kind="player", players=("VJ Edgecombe",)),
        lines=(Line(measure="threePointFieldGoalsMade", period=Period(number=1)),),
        measure=Measure(key="threePointFieldGoalsMade", as_typed="threePointFieldGoalsMade"),
    )
    assert unanswerable(con, PLAYER_LINE, read, "vj edgecombe three points made per game after making one three in first quarter") is None
    # A period that is the part measured is not this refusal.
    plain = "vj edgecombe 1st quarter assists by game"
    assert unanswerable(con, None, Reading(who=Who(kind="player", players=("VJ Edgecombe",)), measure=Measure(key="assists", as_typed="assists"), period=Period(number=1)), plain) is None


def test_bench_points_are_refused_as_a_gap_of_ours_not_missing_data(con: duckdb.DuckDBPyConnection) -> None:
    """yardstick-v2 F106: the box score flags starters, so bench points are
    derivable - the refusal says nothing reads them yet, never "no data"."""
    from association.query.parse import read_route

    # The recognizer reads the word "bench" beside the typed measure the WORDS
    # named (slice (c)); a reading built from the model's key alone names none.
    reading = read_route(names_of(con), "most opponent bench points allowed in the west at home by team this month", [], "points")
    assert [cause.kind for cause in reading.unsupported] == ["bench_points"]
    refusal = refusal_result(reading.unsupported[0])
    assert "not read yet" in refusal.answer and "flags starters" in refusal.answer
    assert (
        unanswerable(con, None, Reading(cuts=Cuts(venue="home"), measure=Measure(key="points", as_typed="points")), "most opponent bench points allowed in the west at home by team this month") is None
    )


def test_a_championship_question_is_refused_before_a_ranking_answers_it(con: duckdb.DuckDBPyConnection) -> None:
    """ "show which team won the nba championship for the past 10 years" routed
    team_leaderboard and ranked records since 2017 - a fluent wrong answer;
    titles are not on record as such. "Title odds" is team_outlook's and is
    left alone."""
    refusal = by_question("show which team won the nba championship for the past 10 years", TEAM_RANKING, con)
    assert refusal is not None and "not on record as such" in refusal.answer
    assert refusal.data == {"refused": "championship", "message": refusal.answer}
    assert by_question("what are the sixers title odds?", TEAM_OUTLOOK, con) is None
    assert by_question("best record from 2010-11 to 2018-19", TEAM_RANKING, con) is None


def test_a_teams_total_of_triple_doubles_is_refused_for_that_cause(con: duckdb.DuckDBPyConnection) -> None:
    result = unanswerable(
        con,
        PLAYER_RANKING,
        Reading(who=Who(kind="team", teams=("Los Angeles Lakers",)), measure=Measure(key="triple_double", as_typed="triple_double"), span=Span(career=True)),
        "los angeles lakers all-time triple doubles vs west",
    )
    assert result is not None and result.data["refused"] == "team_boolean_count" and "team's total" in result.answer
    assert unanswerable(con, PLAYER_RANKING, Reading(measure=Measure(key="triple_double", as_typed="triple_double")), "who has the most triple doubles this season") is None
