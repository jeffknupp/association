"""Fixture tests for :mod:`association.query.refusals` - the shapes nothing
here can answer, refused fast and with their cause. Each test pairs the
refusal with the fact that makes it safe: the template refuses the same
shape (so the refusal never shadows an answer), and the sentence names the
thing that is actually missing."""

from __future__ import annotations

from typing import Any

import duckdb
import pytest

from association.query import refusals
from association.query.calendar import parse_alignment, parse_situation
from association.query.reading import Reading, Scope
from association.query.refusals import by_question
from association.query.subject import read_subject
from association.query.templates.common import TemplateResult, TemplateUnsupported, check_scope


def unanswerable(con: duckdb.DuckDBPyConnection, intent: str, slots: dict[str, Any], question: str) -> TemplateResult | None:
    """A test's slot dict as the Reading the agent hands
    :func:`association.query.refusals.unanswerable` (5.0.0: it takes the
    Reading alone): the typed scope, and the subject read from the question
    the way the module read it for a caller with none."""
    return refusals.unanswerable(con, Reading(scope=Scope.from_slots(slots), intent=intent, subject=read_subject(con, question, intent, Scope.from_slots(dict(slots)))), question)


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
    template honors (check_scope raises), and the games carry no round
    label (ISSUES #10), so the agent had nothing to read either."""
    slots: dict[str, Any] = {"team": "NBA Finals", "season": 2025, "season_type": 3, "round": "finals"}
    with pytest.raises(TemplateUnsupported):
        check_scope("game_log", slots)
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


def test_a_stat_other_than_points_by_quarter_is_refused(con: duckdb.DuckDBPyConnection) -> None:
    """yardstick-v2 F066-F068 "vj edgecombe 1st quarter assists by game":
    period_split answers points only (its own refusal), and the per-period
    figures are rebuilt from scoring plays, so nothing else reads a quarter's
    assists either."""
    refusal = unanswerable(con, "period_split", {"player": "VJ Edgecombe", "stat": "assists", "period": 1}, "vj edgecombe 1st quarter assists by game")
    assert refusal is not None
    assert "only points" in refusal.answer and "'assists'" in refusal.answer and "the 1st quarter" in refusal.answer
    half = unanswerable(con, "period_split", {"player": "VJ Edgecombe", "stat": "rebounds", "half": 2}, "vj 2nd half rebounds")
    assert half is not None and "the 2nd half" in half.answer


def test_a_team_in_the_player_slot_asks_which_player(con: duckdb.DuckDBPyConnection) -> None:
    """yardstick-v2 F125 "Most reb by a hawk player history" routed
    player_history with player='Hawks': a team where one player belongs."""
    refusal = unanswerable(con, "player_history", {"player": "Hawks", "stat": "rebounds"}, "Most reb by a hawk player history")
    assert refusal is not None
    assert "'Hawks' is a team" in refusal.answer and "rebounds" in refusal.answer
    assert unanswerable(con, "player_history", {"player": "LeBron James", "stat": "rebounds"}, "lebron rebounds by year") is None
    # A team-only intent is not this module's business - the team templates read it.
    assert unanswerable(con, "team_record", {"player": "Hawks"}, "hawks record") is None


def test_a_player_beside_a_team_is_never_called_a_team(con: duckdb.DuckDBPyConnection) -> None:
    """ "celtics record when jayson tatum scores 30" was refused as "'jayson
    tatum' is a team": the reading is the team's, with Tatum beside it, and
    the refusal fired on the kind without asking the team index. What is
    missing there is the line the record is split by, and the refusal says
    so; with the line it is no refusal at all."""
    con.execute("INSERT INTO players VALUES ('4','Jayson Tatum')")
    con.execute("INSERT INTO teams VALUES ('3','Boston Celtics','BOS')")
    slots: dict[str, Any] = {"player": "jayson tatum", "team": "Boston Celtics", "stat": "points", "season_type": 2}
    refusal = unanswerable(con, "record_when", slots, "celtics record when jayson tatum has 30")
    assert refusal is not None and refusal.data["refused"] == "team_where_a_player_belongs"
    assert "is a team" not in refusal.answer and "needs a line" in refusal.answer and "'jayson tatum'" in refusal.answer
    assert unanswerable(con, "record_when", {**slots, "threshold": 30}, "celtics record when jayson tatum scores 30") is None
    # Beside a team, a player in any other template's slot has no sentence here: nothing is claimed.
    assert unanswerable(con, "player_stat", {"player": "jayson tatum", "team": "Boston Celtics"}, "celtics stats when jayson tatum has a big game") is None


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
        assert reading.intent == "player_matchup" and reading.scope.players == ("LeBron James", "Kawhi Leonard") and not reading.scope.opponent, question
    team = parsed("lebron vs the hawks", ["lebron", "hawks"])
    assert team.intent != "player_matchup" and team.scope.player == "LeBron James" and team.scope.opponent == "Atlanta Hawks"


def test_a_teams_stat_other_than_points_by_period_is_refused(con: duckdb.DuckDBPyConnection) -> None:
    """yardstick-v2 F065 "trailblazers stats last 10 games 3 point average
    1st quarter": the linescore holds points per period and nothing else."""
    refusal = unanswerable(con, "team_quarter_points", {"team": "Portland Trail Blazers", "stat": "threePointFieldGoalsMade", "period": 1}, "trailblazers 3 point average 1st quarter")
    assert refusal is not None
    assert "linescore" in refusal.answer and "'threePointFieldGoalsMade'" in refusal.answer
    assert unanswerable(con, "team_quarter_points", {"team": "Portland Trail Blazers", "stat": "points", "period": 1}, "blazers 1st quarter points") is None


def test_bench_points_are_refused_as_a_gap_of_ours_not_missing_data(con: duckdb.DuckDBPyConnection) -> None:
    """yardstick-v2 F106: the box score flags starters, so bench points are
    derivable - the refusal says nothing reads them yet, never "no data"."""
    refusal = unanswerable(con, "other", {"stat": "points", "venue": "home"}, "most opponent bench points allowed in the west at home by team this month")
    assert refusal is not None
    assert "not read yet" in refusal.answer and "flags starters" in refusal.answer


def test_a_championship_question_is_refused_before_a_ranking_answers_it() -> None:
    """ "show which team won the nba championship for the past 10 years" routed
    team_leaderboard and ranked records since 2017 - a fluent wrong answer;
    titles are not on record as such. "Title odds" is team_outlook's and is
    left alone."""
    refusal = by_question("show which team won the nba championship for the past 10 years", "team_leaderboard")
    assert refusal is not None and "not on record as such" in refusal.answer
    assert by_question("what are the sixers title odds?", "team_outlook") is None
    assert by_question("best record from 2010-11 to 2018-19", "team_leaderboard") is None


def test_a_teams_total_of_triple_doubles_is_refused_for_that_cause(con: duckdb.DuckDBPyConnection) -> None:
    result = unanswerable(con, "leaderboard", {"stat": "triple_double", "team": "Los Angeles Lakers", "span": "career"}, "los angeles lakers all-time triple doubles vs west")
    assert result is not None and result.data["refused"] == "team_boolean_count" and "team's total" in result.answer
    assert unanswerable(con, "leaderboard", {"stat": "triple_double"}, "who has the most triple doubles this season") is None
