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

from typing import Any

import duckdb
import pytest

from association.query.answer import Reply
from association.query.calendar import parse_alignment, parse_situation
from association.query.compose.plan import refusal_result
from association.query.entities import players_of, teams_of
from association.query.parse import _reading_from_route_refused, _reading_from_route_unsupported
from association.query.reading import Reading, Scope
from association.query.subject import Subject, read_subject


def unanswerable(con: duckdb.DuckDBPyConnection, intent: str, slots: dict[str, Any], question: str) -> Reply | None:
    """A test's slot dict as a Reading - the typed scope, and the subject
    read from the question - and the first thing its words name that
    nothing reads, as the planner says it (or None)."""
    reading = Reading(scope=Scope.from_slots(slots), intent=intent, subject=read_subject(con, question, intent, Scope.from_slots(dict(slots))))
    causes = _reading_from_route_unsupported(question, reading)
    return refusal_result(causes[0]) if causes else None


def by_question(question: str, intent: str, con: duckdb.DuckDBPyConnection) -> Reply | None:
    """The refusal the words come to before any reader runs, as the planner
    says it (or None)."""
    cause = _reading_from_route_refused(players_of(con), teams_of(con), question, Reading(intent=intent, subject=Subject("everyone")))
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
    assert unanswerable(con, "game_log", {"player": "LeBron James", "opponent": "Atlanta Hawks", "limit": 5}, "lebron's last 5 vs the hawks") is None
    assert unanswerable(con, "period_split", {"player": "VJ Edgecombe", "period": 1, "stat": "points"}, "vj 1st quarter points") is None
    assert unanswerable(con, "player_stat", {"player": "LeBron James", "situation": "on christmas"}, "lebron on christmas") is None


def test_a_playoff_round_is_refused_naming_the_missing_label(con: duckdb.DuckDBPyConnection) -> None:
    """yardstick-v2 F165 "nba finals game log 2025": `round` is a slot no
    reader honors (the planner declines it), and the games carry no round
    label (ISSUES #10), so the agent had nothing to read either."""
    slots: dict[str, Any] = {"team": "NBA Finals", "season": 2025, "season_type": 3, "round": "finals"}
    refusal = unanswerable(con, "game_log", slots, "nba finals game log 2025")
    assert refusal is not None
    assert "not labeled by playoff round" in refusal.answer and "'finals'" in refusal.answer
    assert refusal.data["refused"] == "playoff_round"


def test_an_age_is_refused_because_no_birth_date_is_on_record(con: duckdb.DuckDBPyConnection) -> None:
    """yardstick-v2 F054 "lebron ppg as an 18 year old": the calendar reader
    refuses the value (no weekday, month, holiday or "since <day>" in it),
    and the cause is the missing birth date - not "no data"."""
    assert parse_situation("18 year old") is None
    refusal = unanswerable(con, "player_stat", {"player": "LeBron James", "stat": "points", "situation": "18 year old"}, "lebron ppg as an 18 year old")
    assert refusal is not None
    assert "birth date" in refusal.answer and "'18 year old'" in refusal.answer


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
    refusal = unanswerable(con, "player_splits", {"player": "LeBron James", "situation": "the Central Division these days"}, "lebron in the central division these days")
    assert refusal is not None
    assert "conference or division" in refusal.answer and "not in a shape this reads" in refusal.answer
    other = unanswerable(con, "player_stat", {"player": "LeBron James", "situation": "since returning"}, "lebron since returning")
    assert other is not None
    assert "not something the games are read by" in other.answer


def test_a_calendar_situation_is_never_refused_here(con: duckdb.DuckDBPyConnection) -> None:
    """A situation the relation reads (the K3 shapes) is the relation's to
    answer or refuse by value - this module must not claim it."""
    for situation in ("on tuesdays", "in march", "on christmas", "since january 31st"):
        assert parse_situation(situation) is not None
        assert unanswerable(con, "game_log", {"player": "LeBron James", "situation": situation}, f"lebron games {situation}") is None


def test_an_alignment_situation_is_never_refused_here(con: duckdb.DuckDBPyConnection) -> None:
    """yardstick-v2 F055 "vs southeast division": a conference or division
    situation the relation reads (K3-2) is the relation's to answer or refuse
    by value - the same discipline the calendar shapes above already keep."""
    for situation in ("vs southeast division", "vs the west", "against eastern conference teams", "in the west"):
        assert parse_alignment(situation) is not None
        assert unanswerable(con, "player_splits", {"player": "LeBron James", "situation": situation}, f"lebron {situation}") is None


def test_a_stat_the_period_line_does_not_rebuild_is_refused(con: duckdb.DuckDBPyConnection) -> None:
    """yardstick-v2 F066-F068 "vj edgecombe 1st quarter assists by game" was
    refused here for "only points are on record" - true until the period
    relation (plan item 4) rebuilt assists, rebounds and the rest from the
    plays, and a false cause after. Now only a stat no play carries per
    period is refused, and the refusal names what IS rebuilt."""
    assert unanswerable(con, "period_split", {"player": "VJ Edgecombe", "stat": "assists", "period": 1}, "vj edgecombe 1st quarter assists by game") is None
    assert unanswerable(con, "period_leaderboard", {"stat": "rebounds", "half": 2}, "most 2nd half rebounds") is None
    refusal = unanswerable(con, "period_split", {"player": "VJ Edgecombe", "stat": "minutes", "period": 1}, "vj edgecombe 1st quarter minutes")
    assert refusal is not None
    assert "'minutes' is not among them" in refusal.answer and "rebounds, assists" in refusal.answer and "the 1st quarter" in refusal.answer
    half = unanswerable(con, "period_split", {"player": "VJ Edgecombe", "stat": "plusMinus", "half": 2}, "vj 2nd half plus minus")
    assert half is not None and "the 2nd half" in half.answer
    # A shooting percentage is a ratio of two rebuilt columns, read by both
    # period templates' successors (ROADMAP step 2) - so it is not refused
    # here, for a player or a team; an advanced rate still is, and the
    # refusal names the percentages that ARE read.
    assert unanswerable(con, "period_split", {"player": "VJ Edgecombe", "stat": "threePointFieldGoalPct", "period": 1}, "vj edgecombe 1st quarter 3pt percentage by game") is None
    assert unanswerable(con, "team_quarter_points", {"team": "Knicks", "stat": "freeThrowPct", "period": 4}, "knicks free throw percentage in the 4th quarter") is None
    advanced = unanswerable(con, "period_split", {"player": "VJ Edgecombe", "stat": "ts_pct", "period": 1}, "vj edgecombe 1st quarter true shooting")
    assert advanced is not None and "free throw percentages from them" in advanced.answer and "'ts_pct' is not among them" in advanced.answer


def test_a_player_set_against_another_is_the_second_of_two_players(con: duckdb.DuckDBPyConnection) -> None:
    """yardstick-v2 F081 "lebron vs kawhi head to head": the pair relation
    is read by ``player_matchup``. The router filed the second player as the
    opponent and a reroute (``refusals.pair_from_opponent``, then the subject
    reading's) put him back; the parser names the pair from the subject's
    kind - two players - so the reroute went with the router (ROADMAP plan
    item 6, step (d), part 3). A team opponent stays a narrowing of the one
    player's games."""
    from association.query.parse import read_route, reading_from_route

    def parsed(question: str, names: list[str]) -> Reading:
        route, _, _ = read_route(con, question, names, "")
        return reading_from_route(con, question, route)

    for question in ("lebron vs kawhi head to head", "lebron vs kawhi record"):
        reading = parsed(question, ["lebron", "kawhi"])
        assert reading.intent == "player_matchup" and reading.scope.players == ("LeBron James", "Kawhi Leonard") and not reading.scope.cuts.opponent, question
    team = parsed("lebron vs the hawks", ["lebron", "hawks"])
    assert team.intent != "player_matchup" and team.scope.player == "LeBron James" and team.scope.cuts.opponent == "Atlanta Hawks"


def test_a_teams_stat_no_period_splits_is_refused_and_a_rebuilt_one_is_not(con: duckdb.DuckDBPyConnection) -> None:
    """yardstick-v2 F065 "trailblazers stats last 10 games 3 point average
    1st quarter" is answered now - the period's line rebuilds threes from the
    plays (the period relation's team half) - so only a stat neither the
    linescore nor that line holds is refused, by name."""
    refusal = unanswerable(con, "team_quarter_points", {"team": "Portland Trail Blazers", "stat": "plusMinus", "period": 1}, "blazers 1st quarter plus minus")
    assert refusal is not None
    assert "linescore" in refusal.answer and "'plusMinus'" in refusal.answer
    for stat in ("points", "threePointFieldGoalsMade", "rebounds", "turnovers"):
        assert unanswerable(con, "team_quarter_points", {"team": "Portland Trail Blazers", "stat": stat, "period": 1}, f"blazers 1st quarter {stat}") is None, stat


def test_a_period_used_as_a_condition_is_refused_by_name(con: duckdb.DuckDBPyConnection) -> None:
    """yardstick-v2 F062: "vj edgecombe three points made per game after
    making one three in first quarter" asks his whole-game threes over the
    games whose first quarter held one - read into the scope now; a wording
    whose line cannot be read is kept off the period templates, and this
    names what a readable line looks like instead of falling through."""
    question = "vj edgecombe three points made per game after scoring a lot in the first quarter"
    result = unanswerable(con, "other", {"player": "VJ Edgecombe", "stat": "threePointFieldGoalsMade"}, question)
    assert result is not None and result.data["refused"] == "period_as_condition" and "no line could be read from it" in result.answer
    # A condition the parser read is the relation's narrowing (ROADMAP step 2, #275), not this refusal.
    read = {"player": "VJ Edgecombe", "stat": "threePointFieldGoalsMade", "period_condition": {"stat": "threePointFieldGoalsMade", "threshold": 1, "period": 1}}
    assert unanswerable(con, "player_stat", read, "vj edgecombe three points made per game after making one three in first quarter") is None
    # A period that is the part measured is not this refusal.
    plain = "vj edgecombe 1st quarter assists by game"
    assert unanswerable(con, "other", {"player": "VJ Edgecombe", "stat": "assists", "period": 1}, plain) is None


def test_bench_points_are_refused_as_a_gap_of_ours_not_missing_data(con: duckdb.DuckDBPyConnection) -> None:
    """yardstick-v2 F106: the box score flags starters, so bench points are
    derivable - the refusal says nothing reads them yet, never "no data"."""
    refusal = unanswerable(con, "other", {"stat": "points", "venue": "home"}, "most opponent bench points allowed in the west at home by team this month")
    assert refusal is not None
    assert "not read yet" in refusal.answer and "flags starters" in refusal.answer


def test_a_championship_question_is_refused_before_a_ranking_answers_it(con: duckdb.DuckDBPyConnection) -> None:
    """ "show which team won the nba championship for the past 10 years" routed
    team_leaderboard and ranked records since 2017 - a fluent wrong answer;
    titles are not on record as such. "Title odds" is team_outlook's and is
    left alone."""
    refusal = by_question("show which team won the nba championship for the past 10 years", "team_leaderboard", con)
    assert refusal is not None and "not on record as such" in refusal.answer
    assert refusal.data == {"refused": "championship", "intent": "team_leaderboard", "message": refusal.answer}
    assert by_question("what are the sixers title odds?", "team_outlook", con) is None
    assert by_question("best record from 2010-11 to 2018-19", "team_leaderboard", con) is None


def test_a_teams_total_of_triple_doubles_is_refused_for_that_cause(con: duckdb.DuckDBPyConnection) -> None:
    result = unanswerable(con, "leaderboard", {"stat": "triple_double", "team": "Los Angeles Lakers", "span": "career"}, "los angeles lakers all-time triple doubles vs west")
    assert result is not None and result.data["refused"] == "team_boolean_count" and "team's total" in result.answer
    assert unanswerable(con, "leaderboard", {"stat": "triple_double"}, "who has the most triple doubles this season") is None
