"""Fixture tests for :mod:`association.query.refusals` - the shapes nothing
here can answer, refused fast and with their cause. Each test pairs the
refusal with the fact that makes it safe: the template refuses the same
shape (so the refusal never shadows an answer), and the sentence names the
thing that is actually missing."""

from __future__ import annotations

from typing import Any

import duckdb
import pytest

from association.query.calendar import parse_situation
from association.query.refusals import pair_from_opponent, unanswerable
from association.query.templates.common import TemplateUnsupported, check_scope


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


def test_a_conference_or_division_names_its_own_cause(con: duckdb.DuckDBPyConnection) -> None:
    """yardstick-v2 F055 "vs southeast division": in the standings, not yet
    read - a different sentence from the age one."""
    refusal = unanswerable(con, "player_splits", {"player": "LeBron James", "situation": "vs southeast division"}, "lebron vs southeast division")
    assert refusal is not None
    assert "conference or division" in refusal.answer and "not read from the standings yet" in refusal.answer
    other = unanswerable(con, "player_stat", {"player": "LeBron James", "situation": "since returning"}, "lebron since returning")
    assert other is not None
    assert "not something the games are read by" in other.answer


def test_a_calendar_situation_is_never_refused_here(con: duckdb.DuckDBPyConnection) -> None:
    """A situation the relation reads (the K3 shapes) is the relation's to
    answer or refuse by value - this module must not claim it."""
    for situation in ("on tuesdays", "in march", "on christmas", "since january 31st"):
        assert parse_situation(situation) is not None
        assert unanswerable(con, "game_log", {"player": "LeBron James", "situation": situation}, f"lebron games {situation}") is None


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


def test_a_player_in_the_opponent_slot_becomes_the_second_of_two_players(con: duckdb.DuckDBPyConnection) -> None:
    """yardstick-v2 F081 "lebron vs kawhi head to head": the pair relation
    is read by player_matchup, so a player filed as the opponent is the
    second player - not a refusal. A team opponent, or a name matching
    nothing, is left alone."""
    slots: dict[str, Any] = {"player": "LeBron James", "opponent": "Kawhi Leonard", "limit": 5}
    assert pair_from_opponent(con, "game_log", slots) == "Kawhi Leonard"
    assert slots == {"players": ["LeBron James", "Kawhi Leonard"], "limit": 5}
    team: dict[str, Any] = {"player": "LeBron James", "opponent": "Atlanta Hawks"}
    assert pair_from_opponent(con, "game_log", team) is None and team["opponent"] == "Atlanta Hawks"
    nobody: dict[str, Any] = {"player": "LeBron James", "opponent": "Nobody Real"}
    assert pair_from_opponent(con, "player_matchup", nobody) is None
    assert pair_from_opponent(con, "team_record", {"player": "LeBron James", "opponent": "Kawhi Leonard"}) is None
