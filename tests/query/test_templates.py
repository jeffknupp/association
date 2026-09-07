"""Tests for the deterministic threshold_count template - including the exact
question that failed three times through the tool-calling agent."""

from typing import Any

import duckdb
import pytest

from association.query.templates import TemplateUnsupported, game_log, leaderboard, player_stat, team_record, threshold_count
from association.season import current_season


@pytest.fixture
def con() -> duckdb.DuckDBPyConnection:
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    c.execute("CREATE TABLE player_box_stats (athlete_id VARCHAR, season INTEGER, season_type INTEGER, points INTEGER, rebounds INTEGER)")
    c.execute("INSERT INTO players VALUES ('1','Luka Doncic'),('2','Shai Gilgeous-Alexander'),('3','Bench Guy')")
    season = current_season()
    rows: list[tuple[Any, ...]] = []
    rows += [("1", season, 2, 35, 5)] * 4          # Luka: 4 games of 30+
    rows += [("2", season, 2, 31, 4)] * 2          # SGA: 2 games of 30+
    rows += [("1", season, 2, 12, 22)] * 3         # Luka: 3 games of 20+ rebounds
    rows += [("3", season, 2, 40, 1)] * 9          # postseason-only below, so excluded
    rows += [("3", season, 3, 40, 1)] * 9
    rows += [("2", season - 1, 2, 40, 1)] * 7      # previous season, excluded by default
    c.executemany("INSERT INTO player_box_stats VALUES (?,?,?,?,?)", rows)
    return c


def test_answers_the_question_the_agent_kept_getting_wrong(con: duckdb.DuckDBPyConnection) -> None:
    result = threshold_count(con, {"stat": "points", "threshold": 30})
    assert result.data["leaders"][0] == {"player": "Bench Guy", "games": 9}
    assert {"player": "Luka Doncic", "games": 4} in result.data["leaders"]


def test_defaults_to_the_current_season(con: duckdb.DuckDBPyConnection) -> None:
    # The old path lost this rule to prompt truncation and answered for 2024.
    result = threshold_count(con, {"stat": "points", "threshold": 30})
    assert result.data["season"] == current_season()


def test_explicit_season_is_honoured(con: duckdb.DuckDBPyConnection) -> None:
    result = threshold_count(con, {"stat": "points", "threshold": 40, "season": current_season() - 1})
    assert result.data["leaders"] == [{"player": "Shai Gilgeous-Alexander", "games": 7}]


def test_restricted_to_regular_season(con: duckdb.DuckDBPyConnection) -> None:
    # Bench Guy has 9 regular-season and 9 postseason 40-point games; only the
    # regular-season ones count.
    result = threshold_count(con, {"stat": "points", "threshold": 40})
    assert result.data["leaders"] == [{"player": "Bench Guy", "games": 9}]


def test_filters_to_a_named_player_on_every_token(con: duckdb.DuckDBPyConnection) -> None:
    result = threshold_count(con, {"stat": "rebounds", "threshold": 20, "player": "Luka Doncic"})
    assert result.data["leaders"] == [{"player": "Luka Doncic", "games": 3}]


def test_unknown_stat_falls_through_instead_of_reaching_sql(con: duckdb.DuckDBPyConnection) -> None:
    with pytest.raises(TemplateUnsupported):
        threshold_count(con, {"stat": "points); DROP TABLE players; --", "threshold": 30})


def test_missing_threshold_falls_through(con: duckdb.DuckDBPyConnection) -> None:
    with pytest.raises(TemplateUnsupported):
        threshold_count(con, {"stat": "points"})


def test_limit_is_clamped(con: duckdb.DuckDBPyConnection) -> None:
    result = threshold_count(con, {"stat": "points", "threshold": 1, "limit": 10_000})
    assert len(result.data["leaders"]) <= 50


def test_empty_result_is_reported_as_empty_not_invented(con: duckdb.DuckDBPyConnection) -> None:
    result = threshold_count(con, {"stat": "points", "threshold": 999})
    assert result.data["leaders"] == []


def test_answer_is_deterministic_prose_so_no_model_call_is_needed(con: duckdb.DuckDBPyConnection) -> None:
    result = threshold_count(con, {"stat": "points", "threshold": 30})
    assert result.answer == (
        f"Bench Guy had the most games with 30+ points in the {current_season()} regular season, with 9. "
        "Next: Luka Doncic (4), Shai Gilgeous-Alexander (2)."
    )


def test_answer_always_names_the_season_explicitly(con: duckdb.DuckDBPyConnection) -> None:
    # The original failure silently answered for 2024 when the user meant the
    # current season; naming it makes that class of mistake visible.
    assert f"{current_season()} regular season" in (threshold_count(con, {"stat": "points", "threshold": 30}).answer or "")


def test_answer_reports_an_empty_result_honestly(con: duckdb.DuckDBPyConnection) -> None:
    result = threshold_count(con, {"stat": "points", "threshold": 999})
    assert result.answer == f"No player had a game with 999+ points in the {current_season()} regular season."


def test_answer_for_a_single_named_player(con: duckdb.DuckDBPyConnection) -> None:
    result = threshold_count(con, {"stat": "rebounds", "threshold": 20, "player": "Luka Doncic"})
    assert result.answer == f"Luka Doncic had 3 games with 20+ rebounds in the {current_season()} regular season."


def test_answer_reports_a_tie_as_a_tie(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("INSERT INTO player_box_stats SELECT '1', season, 2, 35, 5 FROM player_box_stats LIMIT 5")
    result = threshold_count(con, {"stat": "points", "threshold": 30})
    assert "tied for the most" in (result.answer or "")


# ---------------- leaderboard ----------------


@pytest.fixture
def lb_con() -> duckdb.DuckDBPyConnection:
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    c.execute("CREATE TABLE teams (team_id VARCHAR, abbreviation VARCHAR, display_name VARCHAR)")
    c.execute("CREATE TABLE player_season_stats (athlete_id VARCHAR, team_id VARCHAR, season INTEGER, season_type INTEGER, gamesPlayed INTEGER, avgPoints DOUBLE)")
    c.execute("INSERT INTO players VALUES ('1','Luka Doncic'),('2','Stephen Curry')")
    c.execute("INSERT INTO teams VALUES ('9','GS','Golden State Warriors'),('6','DAL','Dallas Mavericks')")
    s = current_season()
    c.execute("INSERT INTO player_season_stats VALUES ('1','6',?,2,70,33.5),('2','9',?,2,68,27.1),('2','9',?,3,10,31.0)", [s, s, s])
    return c


def test_leaderboard_maps_a_plain_stat_slot_onto_a_real_metric(lb_con: duckdb.DuckDBPyConnection) -> None:
    result = leaderboard(lb_con, {"stat": "points"})
    assert result.data["leaders"][0]["display_name"] == "Luka Doncic"


def test_leaderboard_phrases_its_own_answer(lb_con: duckdb.DuckDBPyConnection) -> None:
    result = leaderboard(lb_con, {"stat": "points", "limit": 2})
    assert result.answer == (
        f"Luka Doncic led the league in points per game in the {current_season()} regular season, at 33.5. Next: Stephen Curry (27.1)."
    )


def test_leaderboard_names_the_team_when_filtered(lb_con: duckdb.DuckDBPyConnection) -> None:
    result = leaderboard(lb_con, {"stat": "points", "team": "Warriors"})
    assert "led the Golden State Warriors" in (result.answer or "")


def test_leaderboard_honours_playoffs(lb_con: duckdb.DuckDBPyConnection) -> None:
    result = leaderboard(lb_con, {"stat": "points", "season_type": 3})
    assert "postseason" in (result.answer or "") and result.data["leaders"][0]["value"] == 31.0


def test_leaderboard_unmapped_stat_falls_through_rather_than_fuzzy_matching(lb_con: duckdb.DuckDBPyConnection) -> None:
    # Deliberately NOT get_close_matches: silently ranking by whichever metric
    # scored highest is the substitution failure this design exists to prevent.
    with pytest.raises(TemplateUnsupported):
        leaderboard(lb_con, {"stat": "clutchness"})


def test_leaderboard_ambiguous_team_falls_through_rather_than_picking_one(lb_con: duckdb.DuckDBPyConnection) -> None:
    lb_con.execute("INSERT INTO teams VALUES ('12','LAC','LA Clippers'),('13','LAL','Los Angeles Lakers')")
    with pytest.raises(TemplateUnsupported):
        leaderboard(lb_con, {"stat": "points", "team": "LA"})


def test_leaderboard_unknown_team_falls_through(lb_con: duckdb.DuckDBPyConnection) -> None:
    with pytest.raises(TemplateUnsupported):
        leaderboard(lb_con, {"stat": "points", "team": "Not A Team"})


def test_threshold_count_honours_playoffs(con: duckdb.DuckDBPyConnection) -> None:
    result = threshold_count(con, {"stat": "points", "threshold": 40, "season_type": 3})
    assert "postseason" in (result.answer or "")
    assert result.data["leaders"] == [{"player": "Bench Guy", "games": 9}]


# ---------------- player_stat ----------------


@pytest.fixture
def ps_con() -> duckdb.DuckDBPyConnection:
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    c.execute(
        "CREATE TABLE player_season_stats_deduped (athlete_id VARCHAR, season INTEGER, season_type INTEGER, "
        "gamesPlayed INTEGER, avgPoints DOUBLE, points INTEGER, avgRebounds DOUBLE, avgAssists DOUBLE, assists INTEGER)"
    )
    c.execute("INSERT INTO players VALUES ('1','Luka Doncic'),('2','Luka Garza'),('3','Nikola Jokic')")
    s = current_season()
    c.execute("INSERT INTO player_season_stats_deduped VALUES ('1',?,2,64,33.5,2143,7.7,8.3,531)", [s])
    c.execute("INSERT INTO player_season_stats_deduped VALUES ('3',?,2,65,27.7,1799,12.9,10.7,697)", [s])
    return c


def test_player_stat_reports_one_named_stat_with_its_total(ps_con: duckdb.DuckDBPyConnection) -> None:
    result = player_stat(ps_con, {"player": "Luka Doncic", "stat": "points"})
    assert result.answer == (
        f"Luka Doncic averaged 33.5 points per game in 64 games in the {current_season()} regular season. That is 2,143 in total."
    )


def test_player_stat_with_no_stat_gives_a_stat_line(ps_con: duckdb.DuckDBPyConnection) -> None:
    result = player_stat(ps_con, {"player": "Nikola Jokic"})
    assert result.answer == (
        f"Nikola Jokic averaged 27.7 points, 12.9 rebounds and 10.7 assists per game in 65 games in the {current_season()} regular season."
    )


def test_player_stat_asks_instead_of_guessing_between_players(ps_con: duckdb.DuckDBPyConnection) -> None:
    """Neither guess nor fall through: the template knows exactly what is
    ambiguous, so it says so in ~1.5s instead of handing the agent a problem
    it would spend minutes guessing at."""
    result = player_stat(ps_con, {"player": "Luka", "stat": "points"})
    assert result.answer == "'Luka' matches more than one player - did you mean Luka Doncic or Luka Garza?"
    assert result.data["candidates"] == ["Luka Doncic", "Luka Garza"]


def test_player_stat_unknown_player_falls_through(ps_con: duckdb.DuckDBPyConnection) -> None:
    with pytest.raises(TemplateUnsupported):
        player_stat(ps_con, {"player": "Nobody At All"})


def test_player_stat_missing_player_slot_falls_through(ps_con: duckdb.DuckDBPyConnection) -> None:
    with pytest.raises(TemplateUnsupported):
        player_stat(ps_con, {"stat": "points"})


def test_player_stat_reports_a_missing_season_honestly(ps_con: duckdb.DuckDBPyConnection) -> None:
    result = player_stat(ps_con, {"player": "Luka Doncic", "season": 1999})
    assert result.answer == "Luka Doncic has no 1999 regular season numbers in the warehouse."


def test_player_stat_never_reports_a_total_as_a_per_game_number(ps_con: duckdb.DuckDBPyConnection) -> None:
    # Regression on phrasing: the total used to be inlined as "33.5 points
    # (2143 total) per game", which states something false.
    answer = player_stat(ps_con, {"player": "Luka Doncic", "stat": "points"}).answer or ""
    assert "(2,143 total) per game" not in answer and "2143" not in answer


def test_leaderboard_handles_triple_doubles_as_a_metric_not_a_recount(lb_con: duckdb.DuckDBPyConnection) -> None:
    """ESPN precomputes doubleDouble/tripleDouble as a season count, so "most
    triple-doubles" is a leaderboard rather than a per-game threshold recount."""
    lb_con.execute("ALTER TABLE player_season_stats ADD COLUMN tripleDouble INTEGER")
    lb_con.execute("UPDATE player_season_stats SET tripleDouble = 34 WHERE athlete_id = '1'")
    lb_con.execute("UPDATE player_season_stats SET tripleDouble = 2 WHERE athlete_id = '2'")
    result = leaderboard(lb_con, {"stat": "triple_double", "limit": 2})
    assert result.answer == (
        f"Luka Doncic led the league in triple-doubles in the {current_season()} regular season, at 34. Next: Stephen Curry (2)."
    )


# ---------------- team_record / game_log ----------------


@pytest.fixture
def gl_con() -> duckdb.DuckDBPyConnection:
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE teams (team_id VARCHAR, abbreviation VARCHAR, display_name VARCHAR)")
    c.execute("CREATE TABLE standings (team_id VARCHAR, season INTEGER, wins DOUBLE, losses DOUBLE, winPercent DOUBLE, streak DOUBLE, playoffSeed DOUBLE)")
    c.execute("CREATE TABLE games (event_id VARCHAR, date VARCHAR, home_score INTEGER, away_score INTEGER, winner_team_id VARCHAR)")
    c.execute("CREATE TABLE team_box_stats (event_id VARCHAR, season INTEGER, season_type INTEGER, team_id VARCHAR, opponent_team_id VARCHAR, home_away VARCHAR)")
    c.execute("INSERT INTO teams VALUES ('18','NY','New York Knicks'),('2','BOS','Boston Celtics')")
    s = current_season()
    c.execute("INSERT INTO standings VALUES ('18',?,53.0,29.0,0.646,3.0,4.0)", [s])
    c.execute("INSERT INTO games VALUES ('e1','2026-04-10T22:00Z',112,95,'18'),('e2','2026-04-12T22:00Z',110,96,'2')")
    c.execute("INSERT INTO team_box_stats VALUES ('e1',?,2,'18','2','home'),('e2',?,2,'18','2','away')", [s, s])
    return c


def test_team_record_formats_a_double_backed_record_as_integers(gl_con: duckdb.DuckDBPyConnection) -> None:
    # standings stores wins/losses as DOUBLE; "53.0-29.0" makes a correct
    # answer look untrustworthy.
    answer = team_record(gl_con, {"team": "Knicks"}).answer or ""
    assert "53-29" in answer and "53.0" not in answer
    assert "(.646)" in answer and "4th seed" in answer and "won 3 straight" in answer


def test_team_record_falls_through_for_playoffs(gl_con: duckdb.DuckDBPyConnection) -> None:
    # standings has no season_type, so a playoff record must not be answered
    # with the regular-season number under a playoff-sounding label.
    with pytest.raises(TemplateUnsupported):
        team_record(gl_con, {"team": "Knicks", "season_type": 3})


def test_team_record_reports_a_missing_season_honestly(gl_con: duckdb.DuckDBPyConnection) -> None:
    assert "no 1999 standings" in (team_record(gl_con, {"team": "Knicks", "season": 1999}).answer or "")


def test_game_log_includes_home_and_away_games(gl_con: duckdb.DuckDBPyConnection) -> None:
    """Regression: joining games by home_team_id only silently returns a
    team's home games and drops every away game, with no error."""
    games = game_log(gl_con, {"team": "Knicks"}).data["games"]
    assert {g["home_away"] for g in games} == {"home", "away"}


def test_game_log_reports_each_games_own_score_from_that_teams_side(gl_con: duckdb.DuckDBPyConnection) -> None:
    by_date = {g["date"]: g for g in game_log(gl_con, {"team": "Knicks"}).data["games"]}
    assert (by_date["2026-04-10"]["team_score"], by_date["2026-04-10"]["opponent_score"]) == (112, 95)
    # Away game: the Knicks' score is the AWAY score, not the home one.
    assert (by_date["2026-04-12"]["team_score"], by_date["2026-04-12"]["opponent_score"]) == (96, 110)


def test_game_log_tallies_the_record_over_exactly_the_rows_shown(gl_con: duckdb.DuckDBPyConnection) -> None:
    result = game_log(gl_con, {"team": "Knicks"})
    assert result.data["wins"] == 1
    assert "(1-1)" in (result.answer or "")


def test_game_log_order_first_is_ascending(gl_con: duckdb.DuckDBPyConnection) -> None:
    # LIMIT 1 without an explicit ORDER BY returns an arbitrary row, not the
    # earliest one.
    first = game_log(gl_con, {"team": "Knicks", "order": "first", "limit": 1}).data["games"]
    assert first[0]["date"] == "2026-04-10"
    recent = game_log(gl_con, {"team": "Knicks", "limit": 1}).data["games"]
    assert recent[0]["date"] == "2026-04-12"


def test_game_log_filters_an_exact_calendar_date(gl_con: duckdb.DuckDBPyConnection) -> None:
    # games.date is a full ISO timestamp, so `= 'YYYY-MM-DD'` is valid SQL that
    # silently matches nothing.
    games = game_log(gl_con, {"team": "Knicks", "date": "2026-04-12"}).data["games"]
    assert [g["date"] for g in games] == ["2026-04-12"]


def test_game_log_ignores_a_malformed_date_rather_than_matching_nothing(gl_con: duckdb.DuckDBPyConnection) -> None:
    games = game_log(gl_con, {"team": "Knicks", "date": "April 12"}).data["games"]
    assert len(games) == 2


def test_game_log_ambiguous_team_asks(gl_con: duckdb.DuckDBPyConnection) -> None:
    gl_con.execute("INSERT INTO teams VALUES ('12','LAC','LA Clippers'),('13','LAL','Los Angeles Lakers')")
    assert "did you mean" in (game_log(gl_con, {"team": "LA"}).answer or "")


def test_game_log_without_team_or_player_falls_through(gl_con: duckdb.DuckDBPyConnection) -> None:
    with pytest.raises(TemplateUnsupported):
        game_log(gl_con, {"limit": 5})


def test_team_record_refuses_a_limited_set_rather_than_reporting_the_full_season(gl_con: duckdb.DuckDBPyConnection) -> None:
    """"How did they do in their last 10?" answered with the full-season record
    is a silent substitution - game_log tallies over exactly the games shown."""
    with pytest.raises(TemplateUnsupported):
        team_record(gl_con, {"team": "Knicks", "limit": 10})
