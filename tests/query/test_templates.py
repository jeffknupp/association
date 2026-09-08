"""Tests for the deterministic threshold_count template - including the exact
question that failed three times through the tool-calling agent."""

from pathlib import Path
from typing import Any

import duckdb
import pytest

from association.query import shotchart
from association.query.templates import (
    HONORED_SCOPING,
    TemplateContext,
    TemplateUnsupported,
    check_scope,
    game_log,
    head_to_head,
    leaderboard,
    player_compare,
    player_history,
    player_netpoints,
    player_stat,
    shot_chart,
    shot_distance,
    single_game_high,
    team_quarter_points,
    team_record,
    threshold_count,
)
from association.season import current_season


@pytest.fixture
def con(tmp_path: Path) -> TemplateContext:
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    c.execute("CREATE TABLE player_box_stats (athlete_id VARCHAR, season INTEGER, season_type INTEGER, points INTEGER, rebounds INTEGER)")
    c.execute("INSERT INTO players VALUES ('1','Luka Doncic'),('2','Shai Gilgeous-Alexander'),('3','Bench Guy')")
    season = current_season()
    rows: list[tuple[Any, ...]] = []
    rows += [("1", season, 2, 35, 5)] * 4  # Luka: 4 games of 30+
    rows += [("2", season, 2, 31, 4)] * 2  # SGA: 2 games of 30+
    rows += [("1", season, 2, 12, 22)] * 3  # Luka: 3 games of 20+ rebounds
    rows += [("3", season, 2, 40, 1)] * 9  # postseason-only below, so excluded
    rows += [("3", season, 3, 40, 1)] * 9
    rows += [("2", season - 1, 2, 40, 1)] * 7  # previous season, excluded by default
    c.executemany("INSERT INTO player_box_stats VALUES (?,?,?,?,?)", rows)
    return TemplateContext(con=c, out_dir=tmp_path)


def test_answers_the_question_the_agent_kept_getting_wrong(con: TemplateContext) -> None:
    result = threshold_count(con, {"stat": "points", "threshold": 30})
    assert result.data["leaders"][0] == {"player": "Bench Guy", "games": 9}
    assert {"player": "Luka Doncic", "games": 4} in result.data["leaders"]


def test_defaults_to_the_current_season(con: TemplateContext) -> None:
    # The old path lost this rule to prompt truncation and answered for 2024.
    result = threshold_count(con, {"stat": "points", "threshold": 30})
    assert result.data["season"] == current_season()


def test_explicit_season_is_honoured(con: TemplateContext) -> None:
    result = threshold_count(con, {"stat": "points", "threshold": 40, "season": current_season() - 1})
    assert result.data["leaders"] == [{"player": "Shai Gilgeous-Alexander", "games": 7}]


def test_restricted_to_regular_season(con: TemplateContext) -> None:
    # Bench Guy has 9 regular-season and 9 postseason 40-point games; only the
    # regular-season ones count.
    result = threshold_count(con, {"stat": "points", "threshold": 40})
    assert result.data["leaders"] == [{"player": "Bench Guy", "games": 9}]


def test_filters_to_a_named_player_on_every_token(con: TemplateContext) -> None:
    result = threshold_count(con, {"stat": "rebounds", "threshold": 20, "player": "Luka Doncic"})
    assert result.data["leaders"] == [{"player": "Luka Doncic", "games": 3}]


def test_unknown_stat_falls_through_instead_of_reaching_sql(con: TemplateContext) -> None:
    with pytest.raises(TemplateUnsupported):
        threshold_count(con, {"stat": "points); DROP TABLE players; --", "threshold": 30})


def test_missing_threshold_falls_through(con: TemplateContext) -> None:
    with pytest.raises(TemplateUnsupported):
        threshold_count(con, {"stat": "points"})


def test_limit_is_clamped(con: TemplateContext) -> None:
    result = threshold_count(con, {"stat": "points", "threshold": 1, "limit": 10_000})
    assert len(result.data["leaders"]) <= 50


def test_empty_result_is_reported_as_empty_not_invented(con: TemplateContext) -> None:
    result = threshold_count(con, {"stat": "points", "threshold": 999})
    assert result.data["leaders"] == []


def test_answer_is_deterministic_prose_so_no_model_call_is_needed(con: TemplateContext) -> None:
    result = threshold_count(con, {"stat": "points", "threshold": 30})
    assert result.answer == (f"Bench Guy had the most games with 30+ points in the {current_season()} regular season, with 9. Next: Luka Doncic (4), Shai Gilgeous-Alexander (2).")


def test_answer_always_names_the_season_explicitly(con: TemplateContext) -> None:
    # The original failure silently answered for 2024 when the user meant the
    # current season; naming it makes that class of mistake visible.
    assert f"{current_season()} regular season" in (threshold_count(con, {"stat": "points", "threshold": 30}).answer or "")


def test_answer_reports_an_empty_result_honestly(con: TemplateContext) -> None:
    result = threshold_count(con, {"stat": "points", "threshold": 999})
    assert result.answer == f"No player had a game with 999+ points in the {current_season()} regular season."


def test_answer_for_a_single_named_player(con: TemplateContext) -> None:
    result = threshold_count(con, {"stat": "rebounds", "threshold": 20, "player": "Luka Doncic"})
    assert result.answer == f"Luka Doncic had 3 games with 20+ rebounds in the {current_season()} regular season."


def test_answer_reports_a_tie_as_a_tie(con: TemplateContext) -> None:
    con.con.execute("INSERT INTO player_box_stats SELECT '1', season, 2, 35, 5 FROM player_box_stats LIMIT 5")
    result = threshold_count(con, {"stat": "points", "threshold": 30})
    assert "tied for the most" in (result.answer or "")


# ---------------- leaderboard ----------------


@pytest.fixture
def lb_con(tmp_path: Path) -> TemplateContext:
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    c.execute("CREATE TABLE teams (team_id VARCHAR, abbreviation VARCHAR, display_name VARCHAR)")
    c.execute("CREATE TABLE player_season_stats (athlete_id VARCHAR, team_id VARCHAR, season INTEGER, season_type INTEGER, gamesPlayed INTEGER, avgPoints DOUBLE)")
    c.execute("INSERT INTO players VALUES ('1','Luka Doncic'),('2','Stephen Curry')")
    c.execute("INSERT INTO teams VALUES ('9','GS','Golden State Warriors'),('6','DAL','Dallas Mavericks')")
    s = current_season()
    c.execute("INSERT INTO player_season_stats VALUES ('1','6',?,2,70,33.5),('2','9',?,2,68,27.1),('2','9',?,3,10,31.0)", [s, s, s])
    return TemplateContext(con=c, out_dir=tmp_path)


def test_leaderboard_maps_a_plain_stat_slot_onto_a_real_metric(lb_con: TemplateContext) -> None:
    result = leaderboard(lb_con, {"stat": "points"})
    assert result.data["leaders"][0]["display_name"] == "Luka Doncic"


def test_leaderboard_phrases_its_own_answer(lb_con: TemplateContext) -> None:
    result = leaderboard(lb_con, {"stat": "points", "limit": 2})
    assert result.answer == (f"Luka Doncic led the league in points per game in the {current_season()} regular season, at 33.5. Next: Stephen Curry (27.1).")


def test_leaderboard_names_the_team_when_filtered(lb_con: TemplateContext) -> None:
    result = leaderboard(lb_con, {"stat": "points", "team": "Warriors"})
    assert "led the Golden State Warriors" in (result.answer or "")


def test_leaderboard_honours_playoffs(lb_con: TemplateContext) -> None:
    result = leaderboard(lb_con, {"stat": "points", "season_type": 3})
    assert "postseason" in (result.answer or "") and result.data["leaders"][0]["value"] == 31.0


def test_leaderboard_unmapped_stat_falls_through_rather_than_fuzzy_matching(lb_con: TemplateContext) -> None:
    # Deliberately NOT get_close_matches: silently ranking by whichever metric
    # scored highest is the substitution failure this design exists to prevent.
    with pytest.raises(TemplateUnsupported):
        leaderboard(lb_con, {"stat": "clutchness"})


def test_leaderboard_ambiguous_team_falls_through_rather_than_picking_one(lb_con: TemplateContext) -> None:
    lb_con.con.execute("INSERT INTO teams VALUES ('12','LAC','LA Clippers'),('13','LAL','Los Angeles Lakers')")
    with pytest.raises(TemplateUnsupported):
        leaderboard(lb_con, {"stat": "points", "team": "LA"})


def test_leaderboard_unknown_team_falls_through(lb_con: TemplateContext) -> None:
    with pytest.raises(TemplateUnsupported):
        leaderboard(lb_con, {"stat": "points", "team": "Not A Team"})


def test_threshold_count_honours_playoffs(con: TemplateContext) -> None:
    result = threshold_count(con, {"stat": "points", "threshold": 40, "season_type": 3})
    assert "postseason" in (result.answer or "")
    assert result.data["leaders"] == [{"player": "Bench Guy", "games": 9}]


# ---------------- player_stat ----------------


@pytest.fixture
def ps_con(tmp_path: Path) -> TemplateContext:
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
    return TemplateContext(con=c, out_dir=tmp_path)


def test_player_stat_reports_one_named_stat_with_its_total(ps_con: TemplateContext) -> None:
    result = player_stat(ps_con, {"player": "Luka Doncic", "stat": "points"})
    assert result.answer == (f"Luka Doncic averaged 33.5 points per game in 64 games in the {current_season()} regular season. That is 2,143 in total.")


def test_player_stat_with_no_stat_gives_a_stat_line(ps_con: TemplateContext) -> None:
    result = player_stat(ps_con, {"player": "Nikola Jokic"})
    assert result.answer == (f"Nikola Jokic averaged 27.7 points, 12.9 rebounds and 10.7 assists per game in 65 games in the {current_season()} regular season.")


def test_player_stat_asks_instead_of_guessing_between_players(ps_con: TemplateContext) -> None:
    """Neither guess nor fall through: the template knows exactly what is
    ambiguous, so it says so in ~1.5s instead of handing the agent a problem
    it would spend minutes guessing at."""
    result = player_stat(ps_con, {"player": "Luka", "stat": "points"})
    assert result.answer == "'Luka' matches more than one player - did you mean Luka Doncic or Luka Garza?"
    assert result.data["candidates"] == ["Luka Doncic", "Luka Garza"]


def test_player_stat_unknown_player_falls_through(ps_con: TemplateContext) -> None:
    with pytest.raises(TemplateUnsupported):
        player_stat(ps_con, {"player": "Nobody At All"})


def test_player_stat_missing_player_slot_falls_through(ps_con: TemplateContext) -> None:
    with pytest.raises(TemplateUnsupported):
        player_stat(ps_con, {"stat": "points"})


def test_player_stat_reports_a_missing_season_honestly(ps_con: TemplateContext) -> None:
    result = player_stat(ps_con, {"player": "Luka Doncic", "season": 1999})
    assert result.answer == "Luka Doncic has no 1999 regular season numbers in the warehouse."


def test_player_stat_never_reports_a_total_as_a_per_game_number(ps_con: TemplateContext) -> None:
    # Regression on phrasing: the total used to be inlined as "33.5 points
    # (2143 total) per game", which states something false.
    answer = player_stat(ps_con, {"player": "Luka Doncic", "stat": "points"}).answer or ""
    assert "(2,143 total) per game" not in answer and "2143" not in answer


def test_leaderboard_handles_triple_doubles_as_a_metric_not_a_recount(lb_con: TemplateContext) -> None:
    """ESPN precomputes doubleDouble/tripleDouble as a season count, so "most
    triple-doubles" is a leaderboard rather than a per-game threshold recount."""
    lb_con.con.execute("ALTER TABLE player_season_stats ADD COLUMN tripleDouble INTEGER")
    lb_con.con.execute("UPDATE player_season_stats SET tripleDouble = 34 WHERE athlete_id = '1'")
    lb_con.con.execute("UPDATE player_season_stats SET tripleDouble = 2 WHERE athlete_id = '2'")
    result = leaderboard(lb_con, {"stat": "triple_double", "limit": 2})
    assert result.answer == (f"Luka Doncic led the league in triple-doubles in the {current_season()} regular season, at 34. Next: Stephen Curry (2).")


# ---------------- team_record / game_log ----------------


@pytest.fixture
def gl_con(tmp_path: Path) -> TemplateContext:
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE teams (team_id VARCHAR, abbreviation VARCHAR, display_name VARCHAR)")
    c.execute("CREATE TABLE standings (team_id VARCHAR, season INTEGER, wins DOUBLE, losses DOUBLE, winPercent DOUBLE, streak DOUBLE, playoffSeed DOUBLE)")
    c.execute(
        "CREATE TABLE games (event_id VARCHAR, season INTEGER, season_type INTEGER, date VARCHAR, "
        "home_team_id VARCHAR, away_team_id VARCHAR, home_score INTEGER, away_score INTEGER, winner_team_id VARCHAR)"
    )
    c.execute("CREATE TABLE team_box_stats (event_id VARCHAR, season INTEGER, season_type INTEGER, team_id VARCHAR, opponent_team_id VARCHAR, home_away VARCHAR)")
    c.execute("INSERT INTO teams VALUES ('18','NY','New York Knicks'),('2','BOS','Boston Celtics')")
    s = current_season()
    c.execute("INSERT INTO standings VALUES ('18',?,53.0,29.0,0.646,3.0,4.0)", [s])
    c.execute(
        "INSERT INTO games VALUES ('e1',?,2,'2026-04-10T22:00Z','18','2',112,95,'18'),('e2',?,2,'2026-04-12T22:00Z','2','18',110,96,'2')",
        [s, s],
    )
    c.execute("INSERT INTO team_box_stats VALUES ('e1',?,2,'18','2','home'),('e2',?,2,'18','2','away')", [s, s])
    return TemplateContext(con=c, out_dir=tmp_path)


@pytest.fixture
def tq_con(tmp_path: Path) -> TemplateContext:
    """games.home_linescores/away_linescores plus the team_box_stats rows for
    BOTH sides of each game (team_quarter_points needs a team's own
    perspective row regardless of which side of the matchup it queries from,
    unlike gl_con, which only ever queries the Knicks)."""
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE teams (team_id VARCHAR, abbreviation VARCHAR, display_name VARCHAR)")
    c.execute(
        "CREATE TABLE games (event_id VARCHAR, season INTEGER, season_type INTEGER, date VARCHAR, "
        "home_team_id VARCHAR, away_team_id VARCHAR, home_score INTEGER, away_score INTEGER, winner_team_id VARCHAR, "
        "home_linescores VARCHAR, away_linescores VARCHAR)"
    )
    c.execute("CREATE TABLE team_box_stats (event_id VARCHAR, season INTEGER, season_type INTEGER, team_id VARCHAR, opponent_team_id VARCHAR, home_away VARCHAR)")
    c.execute("INSERT INTO teams VALUES ('18','NY','New York Knicks'),('2','BOS','Boston Celtics'),('5','LAL','Los Angeles Lakers')")
    s = current_season()
    c.execute(
        "INSERT INTO games VALUES "
        "('e1',?,2,'2026-04-10T22:00Z','18','2',112,95,'18','30,25,28,29','20,25,25,25'),"
        "('e2',?,2,'2026-04-12T22:00Z','2','18',110,96,'2','25,30,25,30','20,20,28,28'),"
        "('e3',?,2,'2026-04-14T22:00Z','18','5',108,100,'18','10,32,36,30','25,25,25,25')",
        [s, s, s],
    )
    c.execute(
        "INSERT INTO team_box_stats VALUES "
        "('e1',?,2,'18','2','home'),('e1',?,2,'2','18','away'),"
        "('e2',?,2,'18','2','away'),('e2',?,2,'2','18','home'),"
        "('e3',?,2,'18','5','home'),('e3',?,2,'5','18','away')",
        [s, s, s, s, s, s],
    )
    return TemplateContext(con=c, out_dir=tmp_path)


def test_team_record_formats_a_double_backed_record_as_integers(gl_con: TemplateContext) -> None:
    # standings stores wins/losses as DOUBLE; "53.0-29.0" makes a correct
    # answer look untrustworthy.
    answer = team_record(gl_con, {"team": "Knicks"}).answer or ""
    assert "53-29" in answer and "53.0" not in answer
    assert "(.646)" in answer and "4th seed" in answer and "won 3 straight" in answer


def test_team_record_falls_through_for_playoffs(gl_con: TemplateContext) -> None:
    # standings has no season_type, so a playoff record must not be answered
    # with the regular-season number under a playoff-sounding label.
    with pytest.raises(TemplateUnsupported):
        team_record(gl_con, {"team": "Knicks", "season_type": 3})


def test_team_record_reports_a_missing_season_honestly(gl_con: TemplateContext) -> None:
    assert "no 1999 standings" in (team_record(gl_con, {"team": "Knicks", "season": 1999}).answer or "")


def test_game_log_includes_home_and_away_games(gl_con: TemplateContext) -> None:
    """Regression: joining games by home_team_id only silently returns a
    team's home games and drops every away game, with no error."""
    games = game_log(gl_con, {"team": "Knicks"}).data["games"]
    assert {g["home_away"] for g in games} == {"home", "away"}


def test_game_log_reports_each_games_own_score_from_that_teams_side(gl_con: TemplateContext) -> None:
    by_date = {g["date"]: g for g in game_log(gl_con, {"team": "Knicks"}).data["games"]}
    assert (by_date["2026-04-10"]["team_score"], by_date["2026-04-10"]["opponent_score"]) == (112, 95)
    # Away game: the Knicks' score is the AWAY score, not the home one.
    assert (by_date["2026-04-12"]["team_score"], by_date["2026-04-12"]["opponent_score"]) == (96, 110)


def test_game_log_tallies_the_record_over_exactly_the_rows_shown(gl_con: TemplateContext) -> None:
    result = game_log(gl_con, {"team": "Knicks"})
    assert result.data["wins"] == 1
    assert "(1-1)" in (result.answer or "")


def test_game_log_order_first_is_ascending(gl_con: TemplateContext) -> None:
    # LIMIT 1 without an explicit ORDER BY returns an arbitrary row, not the
    # earliest one.
    first = game_log(gl_con, {"team": "Knicks", "order": "first", "limit": 1}).data["games"]
    assert first[0]["date"] == "2026-04-10"
    recent = game_log(gl_con, {"team": "Knicks", "limit": 1}).data["games"]
    assert recent[0]["date"] == "2026-04-12"


def test_game_log_filters_an_exact_calendar_date(gl_con: TemplateContext) -> None:
    # games.date is a full ISO timestamp, so `= 'YYYY-MM-DD'` is valid SQL that
    # silently matches nothing.
    games = game_log(gl_con, {"team": "Knicks", "date": "2026-04-12"}).data["games"]
    assert [g["date"] for g in games] == ["2026-04-12"]


def test_game_log_ignores_a_malformed_date_rather_than_matching_nothing(gl_con: TemplateContext) -> None:
    games = game_log(gl_con, {"team": "Knicks", "date": "April 12"}).data["games"]
    assert len(games) == 2


def test_game_log_ambiguous_team_asks(gl_con: TemplateContext) -> None:
    gl_con.con.execute("INSERT INTO teams VALUES ('12','LAC','LA Clippers'),('13','LAL','Los Angeles Lakers')")
    assert "did you mean" in (game_log(gl_con, {"team": "LA"}).answer or "")


def test_game_log_without_team_or_player_falls_through(gl_con: TemplateContext) -> None:
    with pytest.raises(TemplateUnsupported):
        game_log(gl_con, {"limit": 5})


def test_team_record_refuses_a_limited_set_rather_than_reporting_the_full_season(gl_con: TemplateContext) -> None:
    """ "How did they do in their last 10?" answered with the full-season record
    is a silent substitution - game_log tallies over exactly the games shown."""
    with pytest.raises(TemplateUnsupported):
        team_record(gl_con, {"team": "Knicks", "limit": 10})


# ---------------- shot_chart ----------------


@pytest.fixture
def sc_ctx(tmp_path: Path) -> TemplateContext:
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    c.execute(
        "CREATE TABLE shot_chart (athlete_id VARCHAR, season INTEGER, season_type INTEGER, event_id VARCHAR, "
        "period INTEGER, clock VARCHAR, made BOOLEAN, shot_type VARCHAR, coordinate_x INTEGER, coordinate_y INTEGER, points_attempted INTEGER)"
    )
    c.execute("INSERT INTO players VALUES ('1','Stephen Curry')")
    c.executemany(
        "INSERT INTO shot_chart VALUES ('1',?,2,'e1',1,'10:00',?,'Jump Shot',25,20,3)",
        [(current_season(), True), (current_season(), False)],
    )
    return TemplateContext(con=c, out_dir=tmp_path / "out")


def test_shot_chart_writes_a_file_and_reports_its_path(sc_ctx: TemplateContext) -> None:
    result = shot_chart(sc_ctx, {"player": "Stephen Curry", "season": current_season()})
    assert "Rendered shot chart for Stephen Curry" in (result.answer or "")
    assert list((sc_ctx.out_dir).glob("*.html"))


def test_shot_chart_reports_no_matching_shots_rather_than_falling_through(sc_ctx: TemplateContext) -> None:
    # The agent has no better source for a chart than the table just queried,
    # so an empty result is the answer, not a reason to spend minutes.
    result = shot_chart(sc_ctx, {"player": "Stephen Curry", "season": 1999})
    assert "No shots found" in (result.answer or "")


def test_shot_chart_ignores_a_nonsense_shot_value(sc_ctx: TemplateContext) -> None:
    result = shot_chart(sc_ctx, {"player": "Stephen Curry", "season": current_season(), "shot_value": 0})
    assert "Rendered shot chart" in (result.answer or "")


def test_shot_chart_without_a_player_falls_through(sc_ctx: TemplateContext) -> None:
    with pytest.raises(TemplateUnsupported):
        shot_chart(sc_ctx, {"season": current_season()})


def test_a_scoped_chart_uses_the_same_player_it_looked_the_game_up_for(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The chart and the game it is scoped to must be about ONE player.

    shot_chart used to resolve the name twice - once to find the event_id and
    again inside the renderer - agreeing only by convention. Two independent
    best-match resolutions can pick differently, and the result would be a chart
    titled for one Curry scoped to a game the other one played: wrong, and
    invisible, since the plot looks perfectly normal.
    """
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    c.execute(
        "CREATE TABLE shot_chart (athlete_id VARCHAR, season INTEGER, season_type INTEGER, event_id VARCHAR, "
        "period INTEGER, clock VARCHAR, made BOOLEAN, shot_type VARCHAR, coordinate_x INTEGER, coordinate_y INTEGER, points_attempted INTEGER)"
    )
    c.execute("CREATE TABLE player_game_log (athlete_id VARCHAR, season INTEGER, season_type INTEGER, event_id VARCHAR, game_date VARCHAR)")
    # Two players both matching "Curry", each with their own game and shots.
    c.execute("INSERT INTO players VALUES ('1','Seth Curry'), ('2','Stephen Curry')")
    c.executemany(
        "INSERT INTO player_game_log VALUES (?,?,2,?,?)",
        [("1", current_season(), "seth_game", "2026-01-02"), ("2", current_season(), "steph_game", "2026-01-03")],
    )
    c.executemany(
        "INSERT INTO shot_chart VALUES (?,?,2,?,1,'10:00',true,'Jump Shot',25,20,3)",
        [("1", current_season(), "seth_game"), ("2", current_season(), "steph_game")],
    )
    ctx = TemplateContext(con=c, out_dir=tmp_path / "out")

    calls = []
    real = shotchart.find_players

    def counting(con: Any, text: str) -> Any:
        calls.append(text)
        return real(con, text)

    monkeypatch.setattr(shotchart, "find_players", counting)
    result = shot_chart(ctx, {"player": "Curry", "order": "recent", "season": current_season()})

    # The load-bearing assertion: the name is resolved ONCE. Two resolutions
    # agree only as long as both spell the tie-break the same way, which is a
    # convention, not a guarantee.
    assert len(calls) == 1, f"player resolved {len(calls)} times: {calls}"

    answer = result.answer or ""
    charted, other = ("Seth Curry", "steph_game") if "Seth Curry" in answer else ("Stephen Curry", "seth_game")
    assert charted in answer
    written = [f.name for f in ctx.out_dir.glob("*.html")]
    assert len(written) == 1
    # The scoping event_id lands in the filename; it must be the charted
    # player's game, not the other candidate's.
    assert other not in written[0]


def test_shot_chart_defaults_an_unspecified_season_to_the_current_one(sc_ctx: TemplateContext) -> None:
    """Passing None through charted a player's entire career in one plot
    (confirmed live: 3,665 Curry attempts across every season)."""
    sc_ctx.con.execute("INSERT INTO shot_chart VALUES ('1',2019,2,'e9',1,'5:00',TRUE,'Jump Shot',10,10,2)")
    answer = shot_chart(sc_ctx, {"player": "Stephen Curry"}).answer or ""
    # Only this season's two shots, not the 2019 one as well.
    assert "1/2 made" in answer and str(current_season()) in answer


# ---------------- player_compare ----------------


def test_player_compare_puts_players_side_by_side(ps_con: TemplateContext) -> None:
    answer = player_compare(ps_con, {"players": ["Luka Doncic", "Nikola Jokic"]}).answer or ""
    assert "Luka Doncic vs Nikola Jokic" in answer
    assert "points" in answer and "33.5" in answer and "27.7" in answer


def test_player_compare_uses_a_table_not_prose(ps_con: TemplateContext) -> None:
    """The agent's prose version of this stated that a player with 0.4 steals
    led one with 1.6. A table cannot make that mistake."""
    lines = (player_compare(ps_con, {"players": ["Luka Doncic", "Nikola Jokic"]}).answer or "").splitlines()
    assert len(lines) >= 5 and lines[1].strip().startswith("Luka Doncic")


def test_player_compare_aligns_decimals_consistently(ps_con: TemplateContext) -> None:
    ps_con.con.execute("UPDATE player_season_stats_deduped SET avgPoints = 25.0 WHERE athlete_id = '3'")
    answer = player_compare(ps_con, {"players": ["Luka Doncic", "Nikola Jokic"], "stat": "points"}).answer or ""
    assert "25.0" in answer  # not a trailing-zero-stripped "25" beside "33.5"


def test_player_compare_resolves_a_nickname(ps_con: TemplateContext) -> None:
    ps_con.con.execute("INSERT INTO players VALUES ('9','Shai Gilgeous-Alexander')")
    answer = player_compare(ps_con, {"players": ["Luka Doncic", "SGA"]}).answer or ""
    assert "Shai Gilgeous-Alexander" in answer


def test_player_compare_asks_rather_than_guessing_an_ambiguous_name(ps_con: TemplateContext) -> None:
    answer = player_compare(ps_con, {"players": ["Luka", "Nikola Jokic"]}).answer or ""
    assert "did you mean Luka Doncic or Luka Garza?" in answer


def test_player_compare_needs_two_distinct_players(ps_con: TemplateContext) -> None:
    with pytest.raises(TemplateUnsupported):
        player_compare(ps_con, {"players": ["Luka Doncic"]})
    with pytest.raises(TemplateUnsupported):
        player_compare(ps_con, {"players": ["Luka Doncic", "Luka Doncic"]})
    with pytest.raises(TemplateUnsupported):
        player_compare(ps_con, {"player": "Luka Doncic"})


def test_player_compare_reports_a_player_with_no_rows_rather_than_dropping_them(ps_con: TemplateContext) -> None:
    ps_con.con.execute("INSERT INTO players VALUES ('9','Shai Gilgeous-Alexander')")
    answer = player_compare(ps_con, {"players": ["Luka Doncic", "Shai Gilgeous-Alexander"]}).answer or ""
    assert "has no" in answer and "Shai Gilgeous-Alexander" in answer


def test_player_compare_is_capped(ps_con: TemplateContext) -> None:
    from association.query.templates import MAX_COMPARED_PLAYERS

    ps_con.con.execute("INSERT INTO players VALUES ('9','A A'),('10','B B'),('11','C C'),('12','D D')")
    result = player_compare(ps_con, {"players": ["Luka Doncic", "Nikola Jokic", "A A", "B B", "C C", "D D"]})
    assert len(result.data["players"]) <= MAX_COMPARED_PLAYERS


def test_shot_chart_reads_threes_from_either_slot(sc_ctx: TemplateContext) -> None:
    """ "Curry's threes" comes back as shot_value 3 or as the equivalent
    box-score stat depending on wording; both mean the same thing."""
    sc_ctx.con.execute("INSERT INTO shot_chart VALUES ('1',?,2,'e2',1,'9:00',TRUE,'Layup',5,5,2)", [current_season()])
    by_value = shot_chart(sc_ctx, {"player": "Stephen Curry", "shot_value": 3}).answer or ""
    by_stat = shot_chart(sc_ctx, {"player": "Stephen Curry", "stat": "threePointFieldGoalsMade"}).answer or ""
    assert "1/2 made" in by_value and "1/2 made" in by_stat


def test_leaderboard_includes_requested_extra_fields(lb_con: TemplateContext) -> None:
    """Regression: "top N in X alongside their points per game" was answered
    without the second half, and without saying so."""
    lb_con.con.execute("ALTER TABLE player_season_stats ADD COLUMN avgRebounds DOUBLE")
    lb_con.con.execute("UPDATE player_season_stats SET avgRebounds = 7.7 WHERE athlete_id = '1'")
    result = leaderboard(lb_con, {"stat": "points", "fields": ["rebounds"]})
    assert result.data["fields"] == ["rebounds"]
    assert "rebounds" in (result.answer or "") and "7.7" in (result.answer or "")


def test_leaderboard_with_fields_renders_a_table(lb_con: TemplateContext) -> None:
    lb_con.con.execute("ALTER TABLE player_season_stats ADD COLUMN avgRebounds DOUBLE")
    answer = leaderboard(lb_con, {"stat": "points", "fields": ["rebounds"]}).answer or ""
    assert len(answer.splitlines()) >= 3


def test_leaderboard_without_fields_stays_a_sentence(lb_con: TemplateContext) -> None:
    answer = leaderboard(lb_con, {"stat": "points"}).answer or ""
    assert len(answer.splitlines()) == 1 and "led the league" in answer


def test_leaderboard_unknown_field_falls_through_rather_than_being_dropped(lb_con: TemplateContext) -> None:
    # Silently ignoring it would answer a narrower question than was asked.
    with pytest.raises(TemplateUnsupported):
        leaderboard(lb_con, {"stat": "points", "fields": ["clutchness"]})


def test_leaderboard_table_keeps_the_metrics_own_precision(lb_con: TemplateContext) -> None:
    lb_con.con.execute("ALTER TABLE player_season_stats ADD COLUMN avgRebounds DOUBLE")
    lb_con.con.execute("UPDATE player_season_stats SET avgPoints = 9.91 WHERE athlete_id = '1'")
    answer = leaderboard(lb_con, {"stat": "points", "fields": ["rebounds"]}).answer or ""
    assert "9.91" in answer  # not rounded to 9.9 by the table's field formatting


def test_leaderboard_table_names_the_qualifying_minimum(lb_con: TemplateContext) -> None:
    """ "Why isn't X on this list?" should have a visible answer."""
    lb_con.con.execute("ALTER TABLE player_season_stats ADD COLUMN avgRebounds DOUBLE")
    answer = leaderboard(lb_con, {"stat": "points", "fields": ["rebounds"], "limit": 2}).answer or ""
    assert "minimum" not in answer or "games" in answer or "minutes" in answer


def test_leaderboard_tolerates_a_repeated_field(lb_con: TemplateContext) -> None:
    """Confirmed live: the router returned ["points","minutes","minutes"]. A
    repeat is a harmless slip, not a reason to fall through to the agent."""
    lb_con.con.execute("ALTER TABLE player_season_stats ADD COLUMN avgRebounds DOUBLE")
    result = leaderboard(lb_con, {"stat": "points", "fields": ["rebounds", "rebounds"]})
    assert result.data["fields"] == ["rebounds"]
    assert (result.answer or "").splitlines()[1].count("rebounds") == 1


def test_leaderboard_drops_a_field_that_restates_the_ranked_metric(lb_con: TemplateContext) -> None:
    """Confirmed live: asked for "top scorers with their rebounds", the router
    also returned "points", which rendered the same 33.5 twice under two
    different headings."""
    lb_con.con.execute("ALTER TABLE player_season_stats ADD COLUMN avgRebounds DOUBLE")
    result = leaderboard(lb_con, {"stat": "points", "fields": ["rebounds", "points"]})
    assert result.data["fields"] == ["rebounds"]


# ---------------- single_game_high ----------------


@pytest.fixture
def sgh_ctx(tmp_path: Path) -> TemplateContext:
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE player_game_log (athlete_id VARCHAR, season INTEGER, season_type INTEGER, player_name VARCHAR, game_date VARCHAR, opponent_abbr VARCHAR, assists INTEGER, points INTEGER)")
    c.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    c.execute("INSERT INTO players VALUES ('1','Ryan Nembhard'),('2','Nikola Jokic')")
    s = current_season()
    c.executemany(
        "INSERT INTO player_game_log VALUES (?,?,?,?,?,?,?,?)",
        [
            ("1", s, 2, "Ryan Nembhard", "2026-04-13T00:30Z", "CHI", 23, 8),
            ("2", s, 2, "Nikola Jokic", "2026-03-26T02:00Z", "DAL", 19, 30),
            ("2", s, 2, "Nikola Jokic", "2026-01-02T02:00Z", "UTA", 11, 40),
            ("1", s, 3, "Ryan Nembhard", "2026-05-01T00:30Z", "BOS", 30, 5),
            ("2", s - 1, 2, "Nikola Jokic", "2025-03-26T02:00Z", "DAL", 25, 30),
        ],
    )
    return TemplateContext(con=c, out_dir=tmp_path)


def test_single_game_high_answers_the_question_a_leaderboard_answered_wrongly(sgh_ctx: TemplateContext) -> None:
    """Confirmed live: with no such intent, "who had the most assists in a
    single game" was answered "Nikola Jokic led the league in assists per game,
    at 10.7" in 1.76s. The real answer was Ryan Nembhard with 23."""
    answer = single_game_high(sgh_ctx, {"stat": "assists"}).answer or ""
    assert answer.startswith("Ryan Nembhard had the most assists in a single game")
    assert "23" in answer and "2026-04-13" in answer and "CHI" in answer


def test_single_game_high_is_a_maximum_not_an_average(sgh_ctx: TemplateContext) -> None:
    games = single_game_high(sgh_ctx, {"stat": "assists"}).data["games"]
    assert games[0]["value"] == 23  # not Jokic's 15.0 average across his two games


def test_single_game_high_defaults_to_the_regular_season(sgh_ctx: TemplateContext) -> None:
    # Nembhard's 30-assist game is postseason and must not win the default.
    assert single_game_high(sgh_ctx, {"stat": "assists"}).data["games"][0]["value"] == 23
    assert single_game_high(sgh_ctx, {"stat": "assists", "season_type": 3}).data["games"][0]["value"] == 30


def test_single_game_high_defaults_to_the_current_season(sgh_ctx: TemplateContext) -> None:
    assert single_game_high(sgh_ctx, {"stat": "assists"}).data["season"] == current_season()
    assert single_game_high(sgh_ctx, {"stat": "assists", "season": current_season() - 1}).data["games"][0]["value"] == 25


def test_single_game_high_for_a_named_player(sgh_ctx: TemplateContext) -> None:
    answer = single_game_high(sgh_ctx, {"stat": "assists", "player": "Nikola Jokic"}).answer or ""
    assert answer == f"Nikola Jokic's highest assist total in a single game in the {current_season()} regular season was 19, on 2026-03-26 vs DAL."


def test_single_game_high_reports_a_tie_as_a_tie(sgh_ctx: TemplateContext) -> None:
    sgh_ctx.con.execute("UPDATE player_game_log SET assists = 23 WHERE player_name = 'Nikola Jokic' AND opponent_abbr = 'DAL' AND season_type = 2")
    assert "tied for the most" in (single_game_high(sgh_ctx, {"stat": "assists"}).answer or "")


def test_single_game_high_unknown_stat_falls_through(sgh_ctx: TemplateContext) -> None:
    with pytest.raises(TemplateUnsupported):
        single_game_high(sgh_ctx, {"stat": "assists); DROP TABLE players; --"})


def test_single_game_high_ambiguous_player_asks(sgh_ctx: TemplateContext) -> None:
    sgh_ctx.con.execute("INSERT INTO players VALUES ('3','Nikola Jovic')")
    assert "did you mean" in (single_game_high(sgh_ctx, {"stat": "assists", "player": "Nikola"}).answer or "")


def test_single_game_high_reports_an_empty_season_honestly(sgh_ctx: TemplateContext) -> None:
    assert "no 1999 regular season games" in (single_game_high(sgh_ctx, {"stat": "assists", "season": 1999}).answer or "")


# ---------------- head_to_head ----------------


def test_head_to_head_counts_games_in_both_directions(gl_con: TemplateContext) -> None:
    """Regression: `games` is home/away-oriented, and the agent's version also
    compared team_id to an abbreviation, so it reported that two teams who met
    four times had never played."""
    result = head_to_head(gl_con, {"teams": ["Knicks", "Celtics"], "season": current_season()})
    assert result.data["games"] == 2  # one home, one away


def test_head_to_head_reports_the_series_record(gl_con: TemplateContext) -> None:
    answer = head_to_head(gl_con, {"teams": ["Knicks", "Celtics"], "season": current_season()}).answer or ""
    assert "met 2 times" in answer and "splitting them 1-1" in answer


def test_head_to_head_applies_the_season_to_the_whole_matchup(gl_con: TemplateContext) -> None:
    # `A OR B AND season = ...` binds the season to one side only; the template
    # parenthesizes the matchup so the filter covers both orderings.
    assert head_to_head(gl_con, {"teams": ["Knicks", "Celtics"], "season": 1999}).data["games"] == 0


def test_head_to_head_reports_no_meetings_honestly(gl_con: TemplateContext) -> None:
    assert "no" in (head_to_head(gl_con, {"teams": ["Knicks", "Celtics"], "season": 1999}).answer or "").lower()


def test_head_to_head_defaults_to_the_current_season(gl_con: TemplateContext) -> None:
    """Not all-time: answering a different span than every other template,
    silently, is the substitution this design exists to prevent."""
    gl_con.con.execute("INSERT INTO games VALUES ('e9',?,2,'2020-01-01T00:00Z','18','2',100,90,'18')", [current_season() - 3])
    result = head_to_head(gl_con, {"teams": ["Knicks", "Celtics"]})
    assert result.data["games"] == 2
    assert f"{current_season()} regular season" in (result.answer or "")


def test_head_to_head_needs_two_distinct_teams(gl_con: TemplateContext) -> None:
    with pytest.raises(TemplateUnsupported):
        head_to_head(gl_con, {"teams": ["Knicks"]})
    with pytest.raises(TemplateUnsupported):
        head_to_head(gl_con, {"teams": ["Knicks", "New York Knicks"]})


def test_head_to_head_asks_on_an_ambiguous_team(gl_con: TemplateContext) -> None:
    gl_con.con.execute("INSERT INTO teams VALUES ('12','LAC','LA Clippers'),('13','LAL','Los Angeles Lakers')")
    assert "did you mean" in (head_to_head(gl_con, {"teams": ["LA", "Celtics"]}).answer or "")


def test_head_to_head_reads_the_second_team_from_the_team_slot(gl_con: TemplateContext) -> None:
    """Regression: "how many times did the Knicks play Boston?" - a city name
    with no nickname - routes the router to split the two teams across `team`
    and a one-element `teams`, rather than both into `teams` as its prompt
    asks (confirmed live: qwen2.5:3b, "...play Boston?" vs. "...play the
    Celtics?"). `team` resolves the city name on its own; the miss was this
    slot split, not name resolution, so a real one-vs-one matchup should still
    answer rather than fall through to the agent."""
    result = head_to_head(gl_con, {"team": "Knicks", "teams": ["Celtics"], "season": current_season()})
    assert result.data["games"] == 2


def test_head_to_head_ignores_a_team_slot_that_only_restates_teams(gl_con: TemplateContext) -> None:
    with pytest.raises(TemplateUnsupported):
        head_to_head(gl_con, {"team": "Knicks", "teams": ["Knicks"]})


def test_team_quarter_points_reads_each_games_own_side_of_linescores(tq_con: TemplateContext) -> None:
    """Regression: "how many points did the 76ers score in the 4th quarter
    against Boston this season?" forced the router's _AGENT_ONLY override to
    the agent, which spent 3 model calls (~150s) filtering a nonexistent
    games.period column, then a broken LAG() over play_id, then comparing
    home_team_id directly to an abbreviation - the opaque-id mistake its own
    prompt warns against. games.home_linescores/away_linescores already store
    the exact per-period score for each side; this just has to read the right
    one for each game (home vs away), not always the same column."""
    result = team_quarter_points(tq_con, {"team": "Knicks", "period": 1, "season": current_season()})
    assert result.data["total"] == 60  # 30 (home in e1) + 20 (away in e2) + 10 (home in e3)
    assert len(result.data["games"]) == 3


def test_team_quarter_points_filters_to_a_named_opponent(tq_con: TemplateContext) -> None:
    result = team_quarter_points(tq_con, {"team": "Knicks", "opponent": "Celtics", "period": 4, "season": current_season()})
    assert result.data["total"] == 57  # 29 (e1) + 28 (e2) - e3 (vs Lakers) excluded
    assert len(result.data["games"]) == 2
    assert "Boston Celtics" in (result.answer or "")


def test_team_quarter_points_reports_no_games_honestly(tq_con: TemplateContext) -> None:
    # The Lakers and Celtics never played each other in this fixture (only
    # each played the Knicks) - an empty result must say so, not answer 0.
    result = team_quarter_points(tq_con, {"team": "Lakers", "opponent": "Celtics", "period": 1, "season": current_season()})
    assert result.data["games"] == []
    assert "no" in (result.answer or "").lower()


def test_team_quarter_points_reports_a_period_no_game_reached(tq_con: TemplateContext) -> None:
    # Every game in the fixture has exactly 4 quarters on record - asking for
    # a 5th (overtime) must say none of the games went there, not silently
    # answer 0 or crash on a short list index.
    result = team_quarter_points(tq_con, {"team": "Knicks", "opponent": "Celtics", "period": 5, "season": current_season()})
    assert "overtime" in (result.answer or "").lower()
    assert "total" not in result.data


def test_team_quarter_points_refuses_a_named_player(tq_con: TemplateContext) -> None:
    with pytest.raises(TemplateUnsupported):
        team_quarter_points(tq_con, {"team": "Knicks", "period": 4, "player": "Jalen Brunson"})


def test_team_quarter_points_refuses_a_missing_period(tq_con: TemplateContext) -> None:
    with pytest.raises(TemplateUnsupported):
        team_quarter_points(tq_con, {"team": "Knicks"})


def test_team_quarter_points_refuses_the_team_as_its_own_opponent(tq_con: TemplateContext) -> None:
    with pytest.raises(TemplateUnsupported):
        team_quarter_points(tq_con, {"team": "Knicks", "opponent": "New York Knicks", "period": 1})


def test_team_quarter_points_phrases_a_single_game_directly(tq_con: TemplateContext) -> None:
    result = team_quarter_points(tq_con, {"team": "Knicks", "opponent": "Lakers", "period": 1, "season": current_season()})
    assert result.data["total"] == 10
    assert "2026-04-14" in (result.answer or "") or str(current_season()) in (result.answer or "")


def test_team_quarter_points_summarizes_rather_than_tables_many_games(tq_con: TemplateContext, monkeypatch: pytest.MonkeyPatch) -> None:
    # A whole-season, no-opponent question can span dozens of games - listing
    # every one is unreadable, so above a small cap this reports the total
    # and average instead of a per-game breakdown.
    monkeypatch.setattr("association.query.templates._QUARTER_BREAKDOWN_LIMIT", 1)
    result = team_quarter_points(tq_con, {"team": "Knicks", "period": 1, "season": current_season()})
    answer = result.answer or ""
    assert "averaging" in answer
    assert "2026-04-10" not in answer


def test_player_stat_refuses_a_named_stat_it_cannot_provide(ps_con: TemplateContext) -> None:
    """Confirmed live: an unsupported stat fell back to the default stat line,
    so "avg 3pt shot distance" was answered with points/rebounds/assists."""
    with pytest.raises(TemplateUnsupported):
        player_stat(ps_con, {"player": "Luka Doncic", "stat": "shot_distance"})


def test_player_stat_still_defaults_when_no_stat_was_named(ps_con: TemplateContext) -> None:
    assert "points" in (player_stat(ps_con, {"player": "Luka Doncic"}).answer or "")
    assert "points" in (player_stat(ps_con, {"player": "Luka Doncic", "stat": ""}).answer or "")


def test_player_compare_refuses_a_named_stat_it_cannot_provide(ps_con: TemplateContext) -> None:
    with pytest.raises(TemplateUnsupported):
        player_compare(ps_con, {"players": ["Luka Doncic", "Nikola Jokic"], "stat": "shot_distance"})


def test_player_stat_supports_the_shooting_stats_the_router_emits(ps_con: TemplateContext) -> None:
    ps_con.con.execute("ALTER TABLE player_season_stats_deduped ADD COLUMN avgThreePointFieldGoalsMade DOUBLE")
    ps_con.con.execute("ALTER TABLE player_season_stats_deduped ADD COLUMN threePointFieldGoalsMade INTEGER")
    ps_con.con.execute("UPDATE player_season_stats_deduped SET avgThreePointFieldGoalsMade = 4.4, threePointFieldGoalsMade = 282 WHERE athlete_id = '1'")
    answer = player_stat(ps_con, {"player": "Luka Doncic", "stat": "threePointFieldGoalsMade"}).answer or ""
    assert "4.4 3-pointers" in answer and "282 in total" in answer


# ---------------- shot_distance ----------------


def test_shot_distance_filters_to_the_shot_value_asked_for(sc_ctx: TemplateContext) -> None:
    """Confirmed live: the agent wrote the right distance formula, then dropped
    both the 3-point filter and the season filter and reported an all-shots,
    all-seasons average of 16.94 as a current-season three-point distance."""
    sc_ctx.con.execute("INSERT INTO shot_chart VALUES ('1',?,2,'e5',1,'1:00',TRUE,'Layup',25,7,2)", [current_season()])
    threes = shot_distance(sc_ctx, {"player": "Stephen Curry", "shot_value": 3})
    everything = shot_distance(sc_ctx, {"player": "Stephen Curry"})
    assert threes.data["attempts"] == 2 and everything.data["attempts"] == 3
    assert threes.data["avg_feet"] > everything.data["avg_feet"]


def test_shot_distance_measures_from_the_hoop_not_the_origin(sc_ctx: TemplateContext) -> None:
    # The hoop is at (25, 5.25); the fixture's shot sits at (25, 20).
    result = shot_distance(sc_ctx, {"player": "Stephen Curry", "shot_value": 3})
    assert 14.0 < result.data["avg_feet"] < 15.5


def test_shot_distance_scopes_to_the_current_season_by_default(sc_ctx: TemplateContext) -> None:
    sc_ctx.con.execute("INSERT INTO shot_chart VALUES ('1',2019,2,'e6',1,'1:00',TRUE,'Jump Shot',25,40,3)")
    assert shot_distance(sc_ctx, {"player": "Stephen Curry", "shot_value": 3}).data["attempts"] == 2


def test_shot_distance_reads_the_shot_value_from_either_slot(sc_ctx: TemplateContext) -> None:
    by_stat = shot_distance(sc_ctx, {"player": "Stephen Curry", "stat": "threePointFieldGoalsMade"})
    assert by_stat.data["shot_value"] == 3


def test_shot_distance_declines_free_throws(sc_ctx: TemplateContext) -> None:
    with pytest.raises(TemplateUnsupported):
        shot_distance(sc_ctx, {"player": "Stephen Curry", "shot_value": 1})


def test_shot_distance_reports_no_coordinates_honestly(sc_ctx: TemplateContext) -> None:
    assert "No " in (shot_distance(sc_ctx, {"player": "Stephen Curry", "season": 1999}).answer or "")


def test_shot_distance_without_a_player_falls_through(sc_ctx: TemplateContext) -> None:
    with pytest.raises(TemplateUnsupported):
        shot_distance(sc_ctx, {"shot_value": 3})


# ---------------- player_history ----------------


def test_player_history_spans_several_seasons(ps_con: TemplateContext) -> None:
    """Every other template answers about one season, so a multi-season
    question had nowhere to go and was absorbed by leaderboard."""
    s = current_season()
    ps_con.con.execute("INSERT INTO player_season_stats_deduped (athlete_id, season, season_type, gamesPlayed, avgPoints) VALUES ('1',?,2,70,30.0),('1',?,2,72,28.0)", [s - 1, s - 2])
    result = player_history(ps_con, {"player": "Luka Doncic", "stat": "points", "limit": 3})
    assert [row["season"] for row in result.data["seasons"]] == [s, s - 1, s - 2]


def test_player_history_defaults_to_four_seasons(ps_con: TemplateContext) -> None:
    from association.query.templates import DEFAULT_HISTORY_SEASONS

    s = current_season()
    for offset in range(1, 8):
        ps_con.con.execute("INSERT INTO player_season_stats_deduped (athlete_id, season, season_type, gamesPlayed, avgPoints) VALUES ('1',?,2,70,20.0)", [s - offset])
    result = player_history(ps_con, {"player": "Luka Doncic", "stat": "points"})
    assert len(result.data["seasons"]) == DEFAULT_HISTORY_SEASONS


def test_player_history_reports_a_percentage_with_its_volume(ps_con: TemplateContext) -> None:
    # A percentage without makes/attempts is the thing people immediately ask
    # "out of how many?" about.
    for col, typ in [("threePointFieldGoalPct", "DOUBLE"), ("threePointFieldGoalsMade", "INTEGER"), ("threePointFieldGoalsAttempted", "INTEGER")]:
        ps_con.con.execute(f"ALTER TABLE player_season_stats_deduped ADD COLUMN {col} {typ}")
    ps_con.con.execute("UPDATE player_season_stats_deduped SET threePointFieldGoalPct=38.3, threePointFieldGoalsMade=202, threePointFieldGoalsAttempted=527 WHERE athlete_id='1'")
    answer = player_history(ps_con, {"player": "Luka Doncic", "stat": "threePointFieldGoalPct"}).answer or ""
    assert "3PT%" in answer and "3PM" in answer and "3PA" in answer
    assert "38.3" in answer and "202" in answer and "527" in answer


def test_player_history_refuses_a_stat_it_has_no_history_for(ps_con: TemplateContext) -> None:
    with pytest.raises(TemplateUnsupported):
        player_history(ps_con, {"player": "Luka Doncic", "stat": "shot_distance"})
    with pytest.raises(TemplateUnsupported):
        player_history(ps_con, {"player": "Luka Doncic"})


def test_player_history_asks_on_an_ambiguous_player(ps_con: TemplateContext) -> None:
    assert "did you mean" in (player_history(ps_con, {"player": "Luka", "stat": "points"}).answer or "")


def test_leaderboard_refuses_when_a_player_is_named(lb_con: TemplateContext) -> None:
    """A leaderboard ranks the league or a team, never one named person.
    Confirmed live: it answered a question about Klay Thompson with the
    league's true-shooting leaders, Klay silently dropped."""
    with pytest.raises(TemplateUnsupported):
        leaderboard(lb_con, {"stat": "points", "player": "Klay Thompson"})


# ---------------- player_netpoints ----------------


@pytest.fixture
def np_ctx(tmp_path: Path) -> TemplateContext:
    from association.net_points_categories import FINGERPRINT_CATEGORIES

    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    c.execute("INSERT INTO players VALUES ('1','Shai Gilgeous-Alexander')")
    c.execute(
        "CREATE TABLE net_points_player (athlete_id VARCHAR, season INTEGER, net_points_season_type VARCHAR, "
        "overall DOUBLE, offense DOUBLE, defense DOUBLE, overall_per_100_poss DOUBLE, total_minutes DOUBLE, games INTEGER)"
    )
    c.execute("INSERT INTO net_points_player VALUES ('1',?,'Regular Season',468.33,403.9,64.43,9.91,2259,68)", [current_season()])
    cats = list(FINGERPRINT_CATEGORIES.values())
    cols = ", ".join(f"{cat}_{side}_net_pts DOUBLE" for cat in cats for side in ("o", "d", "t"))
    c.execute(f"CREATE TABLE net_points_player_fingerprint (athlete_id VARCHAR, season INTEGER, total_poss DOUBLE, {cols})")
    values = ", ".join("1.0" for _ in cats for _ in range(3))
    c.execute(f"INSERT INTO net_points_player_fingerprint VALUES ('1', {current_season()}, 4725, {values})")
    c.execute("UPDATE net_points_player_fingerprint SET two_pt_t_net_pts = 250.9, two_pt_o_net_pts = 251.3")
    return TemplateContext(con=c, out_dir=tmp_path)


def test_player_netpoints_answers_about_the_named_player(np_ctx: TemplateContext) -> None:
    """Confirmed live: this fell through and the agent answered "Nikola Jokic
    leads the team in NetPoints", with SGA dropped entirely."""
    answer = player_netpoints(np_ctx, {"player": "Shai Gilgeous-Alexander"}).answer or ""
    assert answer.startswith("Shai Gilgeous-Alexander")
    assert "468.3" in answer and "403.9" in answer and "64.4" in answer


def test_player_netpoints_includes_the_play_type_fingerprint(np_ctx: TemplateContext) -> None:
    result = player_netpoints(np_ctx, {"player": "SGA"})
    categories = {row["category"] for row in result.data["fingerprint"]}
    assert "two pt" in categories and "driving" in categories
    assert "total" not in categories  # the summary, reported on the headline


def test_player_netpoints_orders_the_fingerprint_by_magnitude(np_ctx: TemplateContext) -> None:
    rows = player_netpoints(np_ctx, {"player": "SGA"}).data["fingerprint"]
    assert rows[0]["category"] == "two pt"


def test_player_netpoints_uses_the_string_season_type(np_ctx: TemplateContext) -> None:
    """net_points_player has its OWN string season_type; filtering it with the
    numeric one every other table uses silently matches nothing."""
    assert player_netpoints(np_ctx, {"player": "SGA"}).data["headline"] is not None


def test_player_netpoints_reports_a_missing_season_honestly(np_ctx: TemplateContext) -> None:
    assert "no 1999 regular season NetPoints" in (player_netpoints(np_ctx, {"player": "SGA", "season": 1999}).answer or "")


def test_player_netpoints_without_a_player_falls_through(np_ctx: TemplateContext) -> None:
    with pytest.raises(TemplateUnsupported):
        player_netpoints(np_ctx, {})


def test_player_netpoints_fingerprint_defaults_to_per_100_possessions(np_ctx: TemplateContext) -> None:
    """Season totals mostly rank by playing time; per 100 possessions is the
    unit that compares players, which is what the fingerprint is for."""
    result = player_netpoints(np_ctx, {"player": "SGA"})
    two_pt = next(r for r in result.data["fingerprint"] if r["category"] == "two pt")
    assert two_pt["total"] == pytest.approx(250.9 / 4725 * 100, rel=1e-3)
    assert two_pt["total_season_total"] == 250.9  # the raw total is still available
    assert "per 100 possessions" in (result.answer or "")


def test_player_netpoints_rate_total_reports_season_totals(np_ctx: TemplateContext) -> None:
    result = player_netpoints(np_ctx, {"player": "SGA", "rate": "total"})
    two_pt = next(r for r in result.data["fingerprint"] if r["category"] == "two pt")
    assert two_pt["total"] == 250.9
    assert "season totals" in (result.answer or "")


def test_player_netpoints_falls_back_to_totals_without_a_possession_count(np_ctx: TemplateContext) -> None:
    # No possession count: report totals and say so, rather than dividing by
    # nothing or showing an unlabeled unit.
    np_ctx.con.execute("UPDATE net_points_player_fingerprint SET total_poss = NULL")
    result = player_netpoints(np_ctx, {"player": "SGA"})
    assert next(r for r in result.data["fingerprint"] if r["category"] == "two pt")["total"] == 250.9
    assert "season totals" in (result.answer or "")


def test_player_netpoints_gives_defense_its_own_section(np_ctx: TemplateContext) -> None:
    """A single table sorted by total renders the defensive profile invisible:
    for SGA, `turnover` carries the largest defensive value of any play type
    and lands 15th of 21 by total, below categories whose defense is ~0."""
    np_ctx.con.execute("UPDATE net_points_player_fingerprint SET turnover_d_net_pts = 170.9, turnover_t_net_pts = 15.7")
    answer = player_netpoints(np_ctx, {"player": "SGA"}).answer or ""
    assert "Offense," in answer and "Defense," in answer
    defense_section = answer.split("Defense,")[1]
    assert defense_section.strip().splitlines()[1].strip().startswith("turnover")


def test_player_netpoints_sorts_each_section_by_its_own_side(np_ctx: TemplateContext) -> None:
    np_ctx.con.execute("UPDATE net_points_player_fingerprint SET turnover_d_net_pts = 170.9, turnover_o_net_pts = 0.5")
    answer = player_netpoints(np_ctx, {"player": "SGA"}).answer or ""
    offense_section = answer.split("Offense,")[1].split("Defense,")[0]
    assert not offense_section.strip().splitlines()[1].strip().startswith("turnover")


def test_player_netpoints_warns_that_the_detail_slices_overlap(np_ctx: TemplateContext) -> None:
    # A driving layup at the rim counts in driving, layup AND rim, so the
    # detail rows are not additive - unlike the six partition categories.
    assert "do not add up" in (player_netpoints(np_ctx, {"player": "SGA"}).answer or "")


def test_the_six_core_categories_partition_the_total(np_ctx: TemplateContext) -> None:
    """two_pt, three_pt, free_throw, turnover, rebound and foul sum exactly to
    the offensive and defensive totals - verified against the separately stored
    net_points_player.offense/.defense for every top-minutes player in 2026,
    max deviation 0.005. The other categories are overlapping slices."""
    from association.query.templates import FINGERPRINT_PARTITION

    assert set(FINGERPRINT_PARTITION) == {"two_pt", "three_pt", "free_throw", "turnover", "rebound", "foul"}
    answer = player_netpoints(np_ctx, {"player": "SGA", "rate": "total"}).answer or ""
    offense = answer.split("Offense,")[1].split("Defense,")[0]
    listed = [ln.split()[-1] for ln in offense.strip().splitlines()[1:] if ln.strip() and not ln.strip().startswith("-")]
    assert pytest.approx(sum(float(v) for v in listed[:-1]), rel=1e-6) == float(listed[-1])


def test_overlapping_play_types_are_kept_out_of_the_summing_column(np_ctx: TemplateContext) -> None:
    answer = player_netpoints(np_ctx, {"player": "SGA"}).answer or ""
    offense = answer.split("Offense,")[1].split("Defense,")[0]
    assert "rim" not in offense and "layup" not in offense  # detail, not partition
    assert "do not add up" in answer and "rim" in answer.split("Play-type detail")[1]


def test_shot_chart_scopes_to_a_single_game_when_order_is_set(sc_ctx: TemplateContext) -> None:
    """Confirmed live: "a shot chart of Curry's last regular season game"
    charted the whole season - 803 attempts instead of that game's 14."""
    sc_ctx.con.execute("CREATE TABLE player_game_log (athlete_id VARCHAR, season INTEGER, season_type INTEGER, event_id VARCHAR, game_date VARCHAR)")
    sc_ctx.con.execute(
        "INSERT INTO player_game_log VALUES ('1',?,2,'e1','2026-01-01T00:00Z'),('1',?,2,'eLast','2026-04-13T00:30Z')",
        [current_season(), current_season()],
    )
    sc_ctx.con.execute("INSERT INTO shot_chart VALUES ('1',?,2,'eLast',1,'2:00',TRUE,'Jump Shot',25,26,3)", [current_season()])
    answer = shot_chart(sc_ctx, {"player": "Stephen Curry", "order": "recent"}).answer or ""
    assert "1/1 made" in answer  # only the one shot from the last game
    assert "eLast" in answer


def test_shot_chart_order_first_picks_the_earliest_game(sc_ctx: TemplateContext) -> None:
    sc_ctx.con.execute("CREATE TABLE player_game_log (athlete_id VARCHAR, season INTEGER, season_type INTEGER, event_id VARCHAR, game_date VARCHAR)")
    sc_ctx.con.execute(
        "INSERT INTO player_game_log VALUES ('1',?,2,'e1','2026-01-01T00:00Z'),('1',?,2,'eLast','2026-04-13T00:30Z')",
        [current_season(), current_season()],
    )
    assert "e1" in (shot_chart(sc_ctx, {"player": "Stephen Curry", "order": "first"}).answer or "")


def test_shot_chart_without_order_still_covers_the_season(sc_ctx: TemplateContext) -> None:
    # Both of the fixture's shots (one made, one missed), not one game's worth.
    assert "1/2 made" in (shot_chart(sc_ctx, {"player": "Stephen Curry"}).answer or "")


def test_threshold_count_supports_fouls(con: TemplateContext) -> None:
    con.con.execute("ALTER TABLE player_box_stats ADD COLUMN fouls INTEGER")
    con.con.execute("UPDATE player_box_stats SET fouls = 6 WHERE athlete_id = '1'")
    result = threshold_count(con, {"stat": "fouls", "threshold": 6})
    assert "6+ fouls" in (result.answer or "")
    assert result.data["leaders"][0]["player"] == "Luka Doncic"


def _add_per_game_netpoints(ctx: TemplateContext) -> None:
    ctx.con.execute("CREATE TABLE games (event_id VARCHAR, date VARCHAR)")
    ctx.con.execute("INSERT INTO games VALUES ('eFirst','2025-10-22T00:00Z'),('eLast','2026-04-13T00:30Z')")
    ctx.con.execute(
        "CREATE TABLE net_points_player_game (event_id VARCHAR, season INTEGER, season_type INTEGER, athlete_id VARCHAR, "
        "o_net_pts DOUBLE, d_net_pts DOUBLE, t_net_pts DOUBLE, o_usage DOUBLE, d_usage DOUBLE, "
        "o_poss DOUBLE, d_poss DOUBLE, t_poss DOUBLE, o_wpa DOUBLE, d_wpa DOUBLE, t_wpa DOUBLE)"
    )
    ctx.con.execute(
        "INSERT INTO net_points_player_game VALUES ('eFirst',?,2,'1',0.8,1.4,2.2,0.2,0.2,16,15,31,0.1,0.1,0.215),('eLast',?,2,'1',2.4,3.9,6.3,0.2,0.2,17,11,28,0.1,0.1,0.190)",
        [current_season(), current_season()],
    )


def test_player_netpoints_scopes_to_one_game_when_order_is_set(np_ctx: TemplateContext) -> None:
    """Confirmed live: "netpoints from his last regular season game" returned
    the whole season - 43 games - because nothing scoped it."""
    _add_per_game_netpoints(np_ctx)
    result = player_netpoints(np_ctx, {"player": "SGA", "order": "recent"})
    assert result.data["game"]["date"] == "2026-04-13"
    answer = result.answer or ""
    assert "6.3 total" in answer and "most recent" in answer
    assert "Offense," not in answer  # not the season breakdown


def test_player_netpoints_order_first_picks_the_earliest_game(np_ctx: TemplateContext) -> None:
    _add_per_game_netpoints(np_ctx)
    assert player_netpoints(np_ctx, {"player": "SGA", "order": "first"}).data["game"]["date"] == "2025-10-22"


def test_single_game_netpoints_says_there_is_no_fingerprint(np_ctx: TemplateContext) -> None:
    # The play-type breakdown is season-level only; silently omitting it would
    # look like the data was missing.
    _add_per_game_netpoints(np_ctx)
    assert "season-level only" in (player_netpoints(np_ctx, {"player": "SGA", "order": "recent"}).answer or "")


def test_player_netpoints_without_order_still_gives_the_season(np_ctx: TemplateContext) -> None:
    _add_per_game_netpoints(np_ctx)
    assert "Offense," in (player_netpoints(np_ctx, {"player": "SGA"}).answer or "")


def test_single_game_netpoints_falls_through_without_the_optin_table(np_ctx: TemplateContext) -> None:
    # net_points_player_game only exists if fetched with
    # --include-net-points-daily; say so rather than answering for the season.
    with pytest.raises(TemplateUnsupported):
        player_netpoints(np_ctx, {"player": "SGA", "order": "recent"})


# ---------------- scoping guard ----------------


def test_scope_guard_blocks_a_template_that_would_ignore_a_game_scope() -> None:
    """Three live failures were slots the router extracted CORRECTLY and the
    template silently dropped: a chart of "his last game" drew the whole season,
    NetPoints for "his last game" reported all 43, and a record "over their last
    10" would have covered the full season. The routing check cannot catch
    those - routing was right every time."""
    for intent, slots in [
        ("threshold_count", {"date": "2026-04-12"}),
        ("team_record", {"order": "recent"}),
        ("leaderboard", {"order": "first"}),
        ("player_stat", {"date": "2026-04-12"}),
    ]:
        with pytest.raises(TemplateUnsupported, match="different span"):
            check_scope(intent, slots)


def test_scope_guard_allows_templates_that_honor_the_slot() -> None:
    check_scope("game_log", {"order": "recent", "date": "2026-04-12"})
    check_scope("shot_chart", {"order": "recent"})
    check_scope("shot_distance", {"order": "first"})
    check_scope("player_netpoints", {"order": "recent"})


def test_scope_guard_ignores_absent_or_empty_slots() -> None:
    check_scope("leaderboard", {})
    check_scope("leaderboard", {"order": None, "date": ""})


def test_every_template_honoring_a_scope_slot_actually_reads_it() -> None:
    # Guards against the list drifting from the code it describes.
    import inspect

    from association.query import templates as module

    for intent, honored in HONORED_SCOPING.items():
        source = inspect.getsource(module.TEMPLATES[intent])
        for slot in honored:
            assert f'"{slot}"' in source, f"{intent} claims to honor {slot} but never reads it"


def test_shot_distance_scopes_to_one_game(sc_ctx: TemplateContext) -> None:
    sc_ctx.con.execute("CREATE TABLE player_game_log (athlete_id VARCHAR, season INTEGER, season_type INTEGER, event_id VARCHAR, game_date VARCHAR)")
    sc_ctx.con.execute("INSERT INTO player_game_log VALUES ('1',?,2,'e1','2026-04-13T00:30Z')", [current_season()])
    # A second game whose shots must NOT be counted.
    sc_ctx.con.execute("INSERT INTO player_game_log VALUES ('1',?,2,'e2','2026-01-01T00:00Z')", [current_season()])
    sc_ctx.con.execute("INSERT INTO shot_chart VALUES ('1',?,2,'e2',1,'2:00',TRUE,'Jump Shot',25,40,3)", [current_season()])
    answer = shot_distance(sc_ctx, {"player": "Stephen Curry", "order": "recent"}).answer or ""
    assert "most recent game (2026-04-13)" in answer
    assert "2 attempts" in answer  # the fixture's two shots in e1, not the third in e2
