"""Tests for the team templates: records, season numbers, rankings and BPI.

The fixture is small but built out of the warehouse's real faults, each one
measured before it was written down here: a 0-0 phantom game with no winner, a
second event id for a game already listed, the NBA Cup final (a regular-season
game no standings count), a neutral-site game, a season listed under two labels
(1993 and 1994), a postseason labeled by the year its season started, and a
postseason whose game list is one game short of the team's own totals.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb
import pytest

from association.fetch.repairs import real_games
from association.nba.season import current_season
from association.query.team_metrics import TEAM_METRICS, descending_for, resolve_team_metric
from association.query.templates.common import TemplateContext, TemplateUnsupported, check_coverage, check_scope
from association.query.templates.games import team_quarter_points
from association.query.templates.splits import record_when, streak
from association.query.templates.teams import team_leaderboard, team_outlook, team_record, team_stat

S = current_season()
LAST = f"{S - 1}-{S % 100:02d}"

_TSS_COLUMNS = (
    "fieldGoalPct",
    "threePointFieldGoalPct",
    "freeThrowPct",
    "trueShootingPct",
    "effectiveFGPct",
    "avgRebounds",
    "avgOffensiveRebounds",
    "avgDefensiveRebounds",
    "avgAssists",
    "avgSteals",
    "avgBlocks",
    "avgFouls",
    "avgThreePointFieldGoalsMade",
    "avgThreePointFieldGoalsAttempted",
    "avgFieldGoalsMade",
    "avgFreeThrowsMade",
    "avgFreeThrowsAttempted",
)
_TSS_DEFAULTS = (46.0, 36.0, 78.0, 57.0, 54.0, 44.0, 11.0, 33.0, 25.0, 8.0, 5.0, 20.0, 13.0, 36.0, 42.0, 17.0, 22.0)


def _tss(c: duckdb.DuckDBPyConnection, season: int, season_type: int, team_id: str, games: int, points: int, fga: int, oreb: int, tov: int, total_tov: int, fta: int) -> None:
    """One team_season_stats row. Totals are given; per-game figures that no
    test reads are filled with plausible constants."""
    c.execute(
        f"INSERT INTO team_season_stats VALUES ({', '.join('?' for _ in range(13 + len(_TSS_COLUMNS)))})",
        [season, season_type, team_id, games, points, points / games, fga, oreb, tov, total_tov, fta, 48.0 * games, 14.0 * games, *_TSS_DEFAULTS],
    )


@pytest.fixture
def team_ctx(tmp_path: Path) -> TemplateContext:
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE teams (team_id VARCHAR, abbreviation VARCHAR, display_name VARCHAR)")
    c.execute(
        "INSERT INTO teams VALUES ('18','NY','New York Knicks'),('2','BOS','Boston Celtics'),('24','SA','San Antonio Spurs'),"
        "('25','OKC','Oklahoma City Thunder'),('27','WSH','Washington Wizards'),('30','CHA','Charlotte Hornets')"
    )
    c.execute(
        "CREATE TABLE standings (season BIGINT, team_id VARCHAR, wins DOUBLE, losses DOUBLE, winPercent DOUBLE, streak DOUBLE, playoffSeed DOUBLE, "
        'gamesBehind DOUBLE, "Home" VARCHAR, "Road" VARCHAR, "Last Ten Games" VARCHAR, avgPointsFor DOUBLE, avgPointsAgainst DOUBLE, differential DOUBLE)'
    )
    c.execute(
        "INSERT INTO standings VALUES "
        # 30-10 + 22-19 is 81 games of 82: one was at a neutral site.
        "(?,'18',53,29,0.646,3,4,7,'30-10','22-19','6-4',116.5,110.1,6.4),"
        "(?,'2',56,26,0.683,1,2,4,'30-11','26-15','8-2',115.0,109.0,6.0),"
        "(?,'24',62,20,0.756,-1,2,0,'32-8','30-12','7-3',119.8,111.5,8.3),"
        "(?,'25',64,18,0.780,2,1,0,'34-7','30-11','9-1',119.0,107.9,11.1),"
        "(?,'27',17,65,0.207,-4,15,45,'11-30','6-35','1-9',112.9,124.3,-11.4),"
        # Before 1993-94 the split reads 0-0; 2000 is two games short, as ESPN's is.
        "(1990,'18',45,37,0.549,NULL,NULL,NULL,'0-0','0-0',NULL,NULL,NULL,NULL),"
        "(2000,'18',49,31,0.6125,1,2,3,'28-13','21-18','6-4',95.0,92.0,3.0)",
        [S] * 5,
    )
    c.execute(
        "CREATE TABLE games (event_id VARCHAR, season BIGINT, season_type BIGINT, date VARCHAR, home_team_id VARCHAR, away_team_id VARCHAR, "
        "home_score BIGINT, away_score BIGINT, winner_team_id VARCHAR, neutral_site BOOLEAN, venue_city VARCHAR)"
    )
    rows: list[tuple[Any, ...]] = [
        # Regular season. g1 is listed twice under two event ids.
        ("g1", S, 2, f"{S - 1}-11-20T00:30Z", "18", "24", 114, 89, "18", False, "New York"),
        ("g1b", S, 2, f"{S - 1}-11-20T00:30Z", "18", "24", 114, 89, "18", False, None),
        # A 0-0 phantom with no winner, on a date of its own. Beside a real
        # game it would be collapsed as that game's duplicate, and the test
        # would pass without the filter that is actually meant to drop it.
        ("ghost", S, 2, f"{S - 1}-11-25T17:00Z", "18", "24", 0, 0, None, False, "New York"),
        ("g2", S, 2, f"{S}-01-01T01:00Z", "24", "18", 134, 132, "24", False, "San Antonio"),
        # A Cup semifinal (counts) and the Cup final (counts in no standings).
        ("g3", S, 2, f"{S - 1}-12-13T22:30Z", "2", "18", 120, 132, "18", True, "Las Vegas"),
        ("g4", S, 2, f"{S - 1}-12-17T01:30Z", "18", "24", 124, 113, "18", True, "Las Vegas"),
        ("g5", S, 2, f"{S}-02-01T01:00Z", "25", "27", 120, 100, "25", False, "Oklahoma City"),
        ("g6", S, 2, f"{S}-02-03T01:00Z", "27", "25", 99, 110, "25", False, "Washington"),
        # Postseason: the Knicks beat the Celtics 3-1.
        ("p1", S, 3, f"{S}-04-20T23:00Z", "18", "2", 110, 100, "18", False, "New York"),
        ("p2", S, 3, f"{S}-04-22T23:00Z", "18", "2", 99, 101, "2", False, "New York"),
        ("p3", S, 3, f"{S}-04-25T23:00Z", "2", "18", 95, 105, "18", False, "Boston"),
        ("p4", S, 3, f"{S}-04-27T23:00Z", "2", "18", 110, 120, "18", False, "Boston"),
        # Labeled 1990, played in 1991: the 1991 postseason.
        ("old1", 1990, 3, "1991-05-01T23:00Z", "2", "18", 100, 90, "2", False, "Boston"),
        # One game under two season labels, exactly as ESPN answers 1993 and 1994.
        ("ph", 1993, 2, "1994-01-10T00:30Z", "18", "2", 100, 90, "18", False, "New York"),
        ("ph", 1994, 2, "1994-01-10T00:30Z", "18", "2", 100, 90, "18", False, "New York"),
        # And a 1994 playoff game under both labels too. A regular season is
        # read by label from 1994, which drops the 1993 copy on its own; a
        # postseason is read by the year it was played, which does not.
        ("php", 1993, 3, "1994-05-01T23:00Z", "18", "2", 95, 90, "18", False, "New York"),
        ("php", 1994, 3, "1994-05-01T23:00Z", "18", "2", 95, 90, "18", False, "New York"),
    ]
    c.executemany("INSERT INTO games VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    c.execute(
        "CREATE TABLE team_season_stats (season BIGINT, season_type BIGINT, team_id VARCHAR, gamesPlayed DOUBLE, points DOUBLE, avgPoints DOUBLE, "
        "fieldGoalsAttempted DOUBLE, offensiveRebounds DOUBLE, turnovers DOUBLE, totalTurnovers DOUBLE, freeThrowsAttempted DOUBLE, "
        f"pointsInPaint DOUBLE, fastBreakPoints DOUBLE, {', '.join(f'{col} DOUBLE' for col in _TSS_COLUMNS)})"
    )
    # Games and points agree with the game list above, so opponent points are usable.
    _tss(c, S, 2, "18", 3, 378, 270, 30, 40, 42, 60)
    _tss(c, S, 2, "24", 2, 223, 180, 20, 26, 28, 40)
    _tss(c, S, 2, "2", 1, 120, 90, 10, 13, 14, 20)
    _tss(c, S, 2, "25", 2, 230, 176, 18, 22, 24, 44)
    _tss(c, S, 2, "27", 2, 199, 184, 16, 30, 32, 36)
    _tss(c, S, 3, "18", 4, 434, 360, 40, 50, 52, 80)
    # Five postseason games by the Celtics' own totals, four in the game list.
    _tss(c, S, 3, "2", 5, 406, 360, 40, 50, 52, 80)
    _tss(c, 1994, 2, "18", 1, 100, 80, 10, 14, 28, 20)
    _tss(c, 1994, 2, "2", 1, 90, 80, 10, 14, 28, 20)
    _tss(c, 2000, 2, "18", 82, 7790, 6800, 1000, 1300, 2600, 2000)
    # Before 2013 totalTurnovers double-counts: 30 stored for 15 committed.
    _tss(c, 2010, 2, "25", 1, 100, 90, 10, 15, 30, 20)
    c.execute(
        "CREATE TABLE team_power_index (season BIGINT, season_type BIGINT, team_id VARCHAR, last_updated VARCHAR, bpi DOUBLE, bpioffense DOUBLE, bpidefense DOUBLE, "
        "numwins DOUBLE, numlosses DOUBLE, projectedw DOUBLE, projectedl DOUBLE, probmakeplayoffs DOUBLE, probmakeconfchamp DOUBLE, probmaketitlegame DOUBLE, "
        "probwintitle DOUBLE, sosoverall DOUBLE, sosoverallrank DOUBLE)"
    )
    bpi: list[tuple[Any, ...]] = [
        (S, 5, f"{S}-04-18T02:23Z", "18", 7.574, 3.24, 4.334, 53, 29, 53, 29, 100.0, 42.0, 21.5, 7.2, 0.503, 14),
        (S, 5, f"{S}-04-18T02:23Z", "25", 11.013, 3.392, 7.62, 64, 18, 64, 18, 100.0, 84.7, 60.3, 43.5, 0.509, 4),
        (S, 5, f"{S}-04-18T02:23Z", "24", 8.946, 4.533, 4.413, 62, 20, 62, 20, 100.0, 69.1, 28.2, 16.9, 0.508, 5),
        (S, 3, f"{S}-06-15T14:20Z", "18", 10.253, 4.959, 5.294, 69, 32, 53, 29, 100.0, 100.0, 100.0, 100.0, 0.506, 9),
        (S, 3, f"{S}-06-15T14:20Z", "25", 11.196, 4.426, 6.77, 75, 22, 64, 18, 100.0, 100.0, 0.0, 0.0, 0.508, 5),
        (S, 3, f"{S}-06-15T14:20Z", "30", 6.323, 4.059, 2.264, 44, 38, 44, 38, 0.0, 0.0, 0.0, 0.0, 0.51, 3),
        # Stamped three years after its season, with a "rank" that is not one.
        (2017, 2, "2020-10-12T07:48Z", "18", 1.0, 0.5, 0.5, 31, 51, 31.2, 50.8, 0.0, 0.0, 0.0, 0.0, 0.49, 26058),
        (2024, 2, "2024-04-14T00:00Z", "18", 3.0, 1.0, 2.0, 50, 32, 50, 32, 100.0, 10.0, 5.0, 2.0, 0.5, 12),
    ]
    c.executemany(f"INSERT INTO team_power_index VALUES ({', '.join('?' for _ in range(17))})", [(r[0], r[1], r[3], r[2], *r[4:]) for r in bpi])
    # The shared filtered list TEAM_GAMES_SQL is built on: "ghost" and the
    # second event id for g1 are dropped by it, and "ph" - one game under two
    # SEASON labels - is deliberately NOT, so TEAM_GAMES_SQL's own QUALIFY
    # still has the phantom season to collapse.
    real_games.build_table(c, {"games", "teams"})
    return TemplateContext(con=c, out_dir=tmp_path)


# ---------------- team_record: a season, from standings ----------------


def test_a_season_record_is_the_standings_line(team_ctx: TemplateContext) -> None:
    answer = team_record(team_ctx, {"team": "Knicks"}).answer
    # standings stores these as DOUBLE; "53.0-29.0" makes a correct answer look untrustworthy.
    assert "53-29" in answer and "53.0" not in answer
    assert "(.646)" in answer and "4th seed" in answer and "won 3 straight" in answer
    assert "Home 30-10, road 22-19 (1 neutral-site game counts as neither home nor away)" in answer
    assert "last 10: 6-4" in answer and "7 games back" in answer
    assert "116.5 points per game, 110.1 allowed (+6.4)" in answer


def test_a_missing_season_is_reported_honestly(team_ctx: TemplateContext) -> None:
    assert "no 1999 standings" in team_record(team_ctx, {"team": "Knicks", "season": 1999}).answer


def test_a_home_record_is_the_home_record_not_the_season(team_ctx: TemplateContext) -> None:
    """ "Knicks home record" was answered with their overall 53-29."""
    result = team_record(team_ctx, {"team": "Knicks", "venue": "home"})
    assert result.answer.startswith(f"The New York Knicks were 30-10 (.750) at home in the {S} regular season, 53-29 overall")
    assert (result.data["venue_wins"], result.data["venue_losses"]) == (30, 10)
    # The web page draws `wins`/`losses`/`win_pct` as the record card, so they
    # must be the record asked about, with the season's kept beside them.
    assert (result.data["wins"], result.data["losses"], result.data["win_pct"]) == (30, 10, 0.75)
    assert (result.data["season_wins"], result.data["season_losses"]) == (53, 29)


def test_a_road_record(team_ctx: TemplateContext) -> None:
    assert "22-19 (.537) on the road" in team_record(team_ctx, {"team": "Knicks", "venue": "away"}).answer


def test_a_season_with_no_split_says_so_instead_of_giving_the_whole_season(team_ctx: TemplateContext) -> None:
    answer = team_record(team_ctx, {"team": "Knicks", "venue": "home", "season": 1990})
    assert "no home/road split" in answer.answer and "45-37" not in answer.answer


def test_a_record_short_of_its_season_says_so(team_ctx: TemplateContext) -> None:
    """ESPN's 2000 standings stop two games short, and nothing in the row says so."""
    answer = team_record(team_ctx, {"team": "Knicks", "season": 2000}).answer
    assert "49-31" in answer and "2000 (80 of 82 games)" in answer


# ---------------- team_record: against one team, from games ----------------


def test_a_record_against_a_team_counts_each_real_game_once(team_ctx: TemplateContext) -> None:
    """The 0-0 phantom and the second event id for the same game are both gone."""
    result = team_record(team_ctx, {"team": "Knicks", "opponent": "San Antonio Spurs"})
    assert f"went 1-1 (.500) against the San Antonio Spurs in the {S} regular season" in result.answer
    assert (result.data["wins"], result.data["losses"], len(result.data["games"])) == (1, 1, 2)


def test_the_cup_final_is_mentioned_but_not_counted(team_ctx: TemplateContext) -> None:
    result = team_record(team_ctx, {"team": "Knicks", "opponent": "Spurs"})
    assert f"NBA Cup final on {S - 1}-12-16, which counts in no standings: won 124-113" in result.answer
    assert result.data["wins"] == 1


def test_meetings_are_dated_on_the_eastern_calendar(team_ctx: TemplateContext) -> None:
    """A 7:30pm Eastern tip is stored as 00:30 UTC the next day."""
    answer = team_record(team_ctx, {"team": "Knicks", "opponent": "Spurs"}).answer
    assert f"{S - 1}-11-19  W 114-89  vs San Antonio Spurs" in answer
    assert f"{S - 1}-11-20" not in answer


def test_the_opponent_can_arrive_in_the_teams_slot(team_ctx: TemplateContext) -> None:
    result = team_record(team_ctx, {"teams": ["New York Knicks", "San Antonio Spurs"]})
    assert result.data["opponent"] == "San Antonio Spurs" and result.data["wins"] == 1


def test_a_neutral_site_meeting_is_neither_home_nor_away(team_ctx: TemplateContext) -> None:
    assert "Home 0-0, away 0-0, neutral site 1-0." in team_record(team_ctx, {"team": "Knicks", "opponent": "Celtics"}).answer
    away = team_record(team_ctx, {"team": "Knicks", "opponent": "Celtics", "venue": "away"})
    assert away.data["wins"] == 0 and "Neutral-site games count as neither home nor away." in away.answer


def test_an_all_time_record_counts_a_season_under_two_labels_once(team_ctx: TemplateContext) -> None:
    result = team_record(team_ctx, {"team": "Knicks", "opponent": "Celtics", "span": "career"})
    assert result.data["wins"] == 2 and result.data["losses"] == 0
    assert "regular seasons from 1993-94 on" in result.answer
    # The same check that makes a tally from `games` safe to state.
    assert "2000 (0 listed, 82 played)" in result.answer


# ---------------- team_record: postseason, from games ----------------


def test_a_postseason_record_is_tallied_from_postseason_games(team_ctx: TemplateContext) -> None:
    """Refused before, because standings have no postseason; answering it with
    the regular-season number would have been worse."""
    answer = team_record(team_ctx, {"team": "Knicks", "season_type": 3}).answer
    assert f"went 3-1 (.750) in the {S} postseason" in answer and "Home 1-1, away 2-0." in answer
    assert "53-29" not in answer


def test_a_postseason_is_found_by_the_year_it_was_played_not_its_label(team_ctx: TemplateContext) -> None:
    """`games` labels every postseason before 1994 a year early."""
    assert "went 0-1 (.000) in the 1991 postseason" in team_record(team_ctx, {"team": "Knicks", "season": 1991, "season_type": 3}).answer


def test_a_postseason_listed_under_two_labels_is_counted_once(team_ctx: TemplateContext) -> None:
    """ESPN answers season=1993 with the 1994 playoffs as well as the 1994
    season, and a postseason found by calendar year sees both copies."""
    result = team_record(team_ctx, {"team": "Knicks", "season": 1994, "season_type": 3})
    assert (result.data["wins"], result.data["losses"]) == (1, 0)


def test_a_season_with_no_games_blames_the_season_not_the_team(team_ctx: TemplateContext) -> None:
    assert team_record(team_ctx, {"team": "Knicks", "season": 1990, "season_type": 3}).answer == "The warehouse holds no 1990 postseason games for any team."
    assert team_record(team_ctx, {"team": "Wizards", "season_type": 3}).answer == f"The Washington Wizards played no games in the {S} postseason."


def test_a_postseason_short_of_the_teams_own_totals_says_so(team_ctx: TemplateContext) -> None:
    answer = team_record(team_ctx, {"team": "Celtics", "season_type": 3}).answer
    assert "went 1-3" in answer and f"{S} (4 listed, 5 played)" in answer


# ---------------- team_record: every season ----------------


def test_an_all_time_record_says_where_the_data_starts(team_ctx: TemplateContext) -> None:
    answer = team_record(team_ctx, {"team": "Knicks", "span": "career"}).answer
    assert f"147-97 (.602) across the 3 regular seasons from 1989-90 through {LAST}" in answer
    assert "not the franchise's whole history" in answer
    assert "2000 (80 of 82 games)" in answer


def test_an_all_time_home_record_counts_only_seasons_with_a_split(team_ctx: TemplateContext) -> None:
    answer = team_record(team_ctx, {"team": "Knicks", "span": "career", "venue": "home"}).answer
    assert f"58-23 (.716) at home across the 2 regular seasons from 1999-00 through {LAST}" in answer
    assert "1 neutral-site game counts as neither." in answer


# ---------------- team_record: refusals ----------------


def test_a_conference_is_refused_by_name(team_ctx: TemplateContext) -> None:
    """No table maps a team to a conference, so there is nothing to tally."""
    answer = team_record(team_ctx, {"team": "Knicks", "opponent": "Western Conference"}).answer
    assert "no conference or division membership" in answer and "53-29" not in answer


def test_a_career_span_and_a_single_season_at_once_falls_through(team_ctx: TemplateContext) -> None:
    with pytest.raises(TemplateUnsupported):
        team_record(team_ctx, {"team": "Knicks", "span": "career", "season": 2020})


def test_the_team_as_its_own_opponent_falls_through(team_ctx: TemplateContext) -> None:
    with pytest.raises(TemplateUnsupported):
        team_record(team_ctx, {"team": "Knicks", "opponent": "New York Knicks"})


def test_team_record_honors_venue_opponent_and_span_but_not_order() -> None:
    check_scope("team_record", {"venue": "home", "opponent": "Boston Celtics", "span": "career"})
    with pytest.raises(TemplateUnsupported, match="different span"):
        check_scope("team_record", {"order": "recent"})


def test_team_record_honors_situation_and_split_at_the_check_scope_level() -> None:
    """check_scope only checks that the slot is declared - team_record itself
    still refuses a `situation` that names no month and a `split` that is not
    "month" (see the tests above), the same way it always refused `order`."""
    check_scope("team_record", {"situation": "in october"})
    check_scope("team_record", {"split": "month"})


# ---------------- team_stat ----------------


def test_a_team_line_is_a_table_with_league_ranks(team_ctx: TemplateContext) -> None:
    answer = team_stat(team_ctx, {"team": "Knicks"}).answer
    assert answer.startswith(f"New York Knicks, {S} regular season (3 games):")
    # 100 * 343 points allowed / (270 - 30 + 42 + 0.44 * 60) possessions.
    assert "111.2  3rd of 5" in answer
    assert "122.6" in answer  # offensive rating: 100 * 378 / 308.4
    assert "FGA - OREB + TOV + 0.44 x FTA" in answer


def test_a_named_stat_answers_that_stat_with_its_rank(team_ctx: TemplateContext) -> None:
    answer = team_stat(team_ctx, {"team": "Knicks", "stat": "defensiveRating"}).answer
    assert answer.startswith(f"The New York Knicks' defensive rating (points allowed per 100 possessions) was 111.2 in the {S} regular season (3 games), 3rd-best of 5 teams.")


def test_a_singular_team_name_takes_an_apostrophe_s(team_ctx: TemplateContext) -> None:
    assert team_stat(team_ctx, {"team": "Thunder", "stat": "points"}).answer.startswith("The Oklahoma City Thunder's points per game was 115.0")


def test_an_unknown_stat_falls_through_rather_than_matching_something_close(team_ctx: TemplateContext) -> None:
    with pytest.raises(TemplateUnsupported):
        team_stat(team_ctx, {"team": "Knicks", "stat": "vibes"})


def test_points_allowed_over_fewer_games_than_the_rest_is_refused(team_ctx: TemplateContext) -> None:
    answer = team_stat(team_ctx, {"team": "Celtics", "stat": "defensive rating", "season_type": 3}).answer
    assert "can't be given" in answer and "4 of the Boston Celtics' 5 games" in answer


def test_a_team_that_missed_the_postseason_is_told_so(team_ctx: TemplateContext) -> None:
    """Not "no data": the season is there, and the team did not play in it."""
    assert team_stat(team_ctx, {"team": "Wizards", "season_type": 3}).answer == f"The Washington Wizards did not play in the {S} postseason."


def test_turnovers_are_not_double_counted_before_2013(team_ctx: TemplateContext) -> None:
    """90 - 10 + 15 + 0.44 * 20 = 103.8 possessions, not the 118.8 ESPN's total gives."""
    assert "103.8" in team_stat(team_ctx, {"team": "Thunder", "stat": "pace", "season": 2010}).answer


def test_a_metric_before_its_first_season_names_why(team_ctx: TemplateContext) -> None:
    answer = team_stat(team_ctx, {"team": "Knicks", "stat": "points in the paint", "season": 2008}).answer
    assert "can't be given for 2008" in answer and "before 2008-09" in answer


def test_a_record_stat_is_ranked_from_standings(team_ctx: TemplateContext) -> None:
    assert "53-29 (.646)" in (answer := team_stat(team_ctx, {"team": "Knicks", "stat": "record"}).answer)
    assert "the 4th-best record of 5 teams" in answer


# ---------------- team_leaderboard ----------------


def _first_row(answer: str) -> str:
    return answer.splitlines()[1]


def test_which_team_scores_the_most(team_ctx: TemplateContext) -> None:
    """Answered with the players' scoring leaders before this existed."""
    answer = team_leaderboard(team_ctx, {"stat": "points", "rank": "most"}).answer
    assert answer.startswith(f"Points per game, {S} regular season - highest first, of 5 teams:")
    assert _first_row(answer).split() == ["1", "New", "York", "Knicks", "126.0"]


def test_the_lowest_defensive_rating_comes_first(team_ctx: TemplateContext) -> None:
    answer = team_leaderboard(team_ctx, {"stat": "defensive rating", "rank": "fewest"}).answer
    assert "lowest first" in answer and "Oklahoma City Thunder" in _first_row(answer)


def test_best_means_fewest_for_a_stat_a_team_wants_little_of(team_ctx: TemplateContext) -> None:
    answer = team_leaderboard(team_ctx, {"stat": "turnovers", "rank": "best"}).answer
    assert "best first (lowest)" in answer and "Oklahoma City Thunder" in _first_row(answer)
    # Three teams at 14.0 share second place.
    assert [line.split()[0] for line in answer.splitlines()[1:6]] == ["1", "2", "2", "2", "5"]


def test_no_rank_means_best(team_ctx: TemplateContext) -> None:
    assert team_leaderboard(team_ctx, {"stat": "turnovers"}).answer == team_leaderboard(team_ctx, {"stat": "turnovers", "rank": "best"}).answer


def test_the_worst_record_comes_first_when_asked(team_ctx: TemplateContext) -> None:
    answer = team_leaderboard(team_ctx, {"stat": "record", "rank": "worst"}).answer
    assert "worst record first" in answer and _first_row(answer).split() == ["1", "Washington", "Wizards", "17-65", "(.207)"]


def test_the_most_losses_is_the_worst_record(team_ctx: TemplateContext) -> None:
    assert "Washington Wizards" in _first_row(team_leaderboard(team_ctx, {"stat": "losses", "rank": "most"}).answer)


def test_the_best_home_record_reads_the_standings_split(team_ctx: TemplateContext) -> None:
    answer = team_leaderboard(team_ctx, {"stat": "record", "venue": "home"}).answer
    assert answer.startswith(f"Record at home, {S} regular season") and "34-7 (.829)" in _first_row(answer)


def test_a_venue_for_a_stat_with_no_venue_split_falls_through(team_ctx: TemplateContext) -> None:
    with pytest.raises(TemplateUnsupported):
        team_leaderboard(team_ctx, {"stat": "points", "venue": "home"})


def test_a_named_team_outside_the_list_is_shown_with_its_rank(team_ctx: TemplateContext) -> None:
    answer = team_leaderboard(team_ctx, {"stat": "points", "limit": 1, "team": "Wizards"}).answer
    assert answer.splitlines()[2:] == ["    ...", " 5  Washington Wizards   99.5"]


def test_a_ranking_with_a_team_short_of_games_is_refused_not_partial(team_ctx: TemplateContext) -> None:
    answer = team_leaderboard(team_ctx, {"stat": "defensive rating", "season_type": 3}).answer
    assert "can't be given" in answer and "4 of the Boston Celtics' 5 games" in answer


def test_a_leaderboard_needs_a_stat(team_ctx: TemplateContext) -> None:
    with pytest.raises(TemplateUnsupported):
        team_leaderboard(team_ctx, {})


# ---------------- team_outlook ----------------


def test_a_same_dated_preseason_snapshot_never_wins_the_regular_season_question(tmp_path: Path) -> None:
    """A boundary guard, not a live bug - and the distinction is the point.

    ESPN stamps 2018's preseason and regular-season snapshots on the same day it
    backfilled them (2020-10-12), one minute apart, so the regular season sorts
    last in the real table and today's answer is right by 60 seconds. This
    fixture gives them the identical stamp, which is what ESPN would have to
    write for ordering-by-date alone to hand a regular-season question the
    preseason rating. Only reachable at all once the paged fetch stores both
    snapshots: before it, the table held one 25-row page.

    **This test does not fail if the tiebreak is deleted, and cannot be made
    to.** Measured 2026-09-15 with `scripts/perturb.py`: removing the `CASE`
    outright leaves the file at 62 passed, while *reversing* it fails here. The
    reason is that the `CASE` sends preseason to 0 and every other type to 1,
    and DuckDB already emits the groups in ascending `season_type` - so the
    guard agrees with the engine's incidental order for every possible pair,
    and no fixture can separate them. Reordering the inserts was tried and
    changes nothing.

    That makes the tiebreak a guard against an ordering DuckDB does not
    promise, which is worth keeping and is not falsifiable through this API.
    What the test does pin is that the ordering path is exercised and that a
    reversal is caught. ISSUES.md ("The BPI preseason tiebreak cannot be
    perturbation-tested through the template") records the gap, so the next
    reader does not mistake a passing test here for a watched-to-fail guard.

    A regular-season question now reads a regular-season snapshot outright
    (ISSUES.md #88, and the fixture-based guard is
    `test_a_regular_season_question_reads_the_regular_season_snapshot_even_when_a_play_in_one_is_later`
    below) - `team_outlook` finds `season_type == 2` directly rather than by
    comparing dates, so this fixture's answer no longer depends on ordering at
    all for the preseason/regular pair specifically. It is kept because it
    still pins the *observable* outcome (a same-dated preseason snapshot never
    wins), and the ordering it was written to test remains live for a pairing
    this fixture does not cover - two non-regular, non-postseason snapshots
    (preseason and play-in) tied on date with no regular-season snapshot at
    all, which no season in the warehouse has today.
    """
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE teams (team_id VARCHAR, abbreviation VARCHAR, display_name VARCHAR, location VARCHAR, name VARCHAR)")
    c.execute("INSERT INTO teams VALUES ('18','NY','New York Knicks','New York','Knicks')")
    c.execute(
        "CREATE TABLE team_power_index (season BIGINT, season_type BIGINT, team_id VARCHAR, last_updated VARCHAR, bpi DOUBLE, bpioffense DOUBLE, bpidefense DOUBLE, "
        "numwins DOUBLE, numlosses DOUBLE, projectedw DOUBLE, projectedl DOUBLE, probmakeplayoffs DOUBLE, probmakeconfchamp DOUBLE, probmaketitlegame DOUBLE, "
        "probwintitle DOUBLE, sosoverall DOUBLE, sosoverallrank DOUBLE)"
    )
    for season_type, bpi in ((1, -2.5), (2, 1.5)):
        c.execute(
            "INSERT INTO team_power_index VALUES (2018,?,'18','2020-10-12T07:48Z',?,0.5,0.5,31,51,31.2,50.8,0.0,0.0,0.0,0.0,0.49,12)",
            [season_type, bpi],
        )
    answer = team_outlook(TemplateContext(con=c, out_dir=tmp_path), {"team": "Knicks", "season": 2018}).answer or ""
    assert "regular-season snapshot" in answer
    assert "BPI +1.5" in answer, "the preseason rating (-2.5) was chosen on a date tie"


def test_a_same_dated_preseason_and_play_in_snapshot_favors_play_in_with_no_regular_season_snapshot(tmp_path: Path) -> None:
    """Keeps the SQL tiebreak's *own* guard alive.

    Preferring a direct `season_type == 2` match (added for #88, see the test
    above) makes `test_a_same_dated_preseason_snapshot_never_wins_the_regular_season_question`
    pass regardless of the `CASE season_type WHEN {BPI_PRESEASON} ...` ordering
    in `team_outlook`'s SQL - `next()` finds the one `season_type == 2` row no
    matter where it sorts. Measured with `scripts/perturb.py`: reversing that
    `CASE` is now MISSED through that test where it used to be CAUGHT, because
    a season with both a preseason and a regular-season snapshot never reaches
    the tiebreak's `pre`/`post` lists at all any more.

    The tiebreak's only live pairing left is two non-regular, non-postseason
    snapshots - preseason and play-in - tied on date with **no** regular-season
    snapshot to short-circuit past it. No season in the warehouse has that
    shape today, but the ordering exists to handle it deliberately rather than
    by accident of `GROUP BY`'s incidental order, and this fixture is what
    would have to be true for date order alone to decide between them.
    """
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE teams (team_id VARCHAR, abbreviation VARCHAR, display_name VARCHAR, location VARCHAR, name VARCHAR)")
    c.execute("INSERT INTO teams VALUES ('18','NY','New York Knicks','New York','Knicks')")
    c.execute(
        "CREATE TABLE team_power_index (season BIGINT, season_type BIGINT, team_id VARCHAR, last_updated VARCHAR, bpi DOUBLE, bpioffense DOUBLE, bpidefense DOUBLE, "
        "numwins DOUBLE, numlosses DOUBLE, projectedw DOUBLE, projectedl DOUBLE, probmakeplayoffs DOUBLE, probmakeconfchamp DOUBLE, probmaketitlegame DOUBLE, "
        "probwintitle DOUBLE, sosoverall DOUBLE, sosoverallrank DOUBLE)"
    )
    for season_type, bpi in ((1, -2.5), (5, 4.5)):
        c.execute(
            "INSERT INTO team_power_index VALUES (2027,?,'18','2027-04-15T00:00Z',?,0.5,0.5,31,51,31.2,50.8,0.0,0.0,0.0,0.0,0.49,12)",
            [season_type, bpi],
        )
    answer = team_outlook(TemplateContext(con=c, out_dir=tmp_path), {"team": "Knicks", "season": 2027}).answer or ""
    assert "play-in snapshot" in answer
    assert "BPI +4.5" in answer, "the preseason rating (-2.5) was chosen on a date tie"


def test_a_regular_season_question_reads_the_regular_season_snapshot_even_when_a_play_in_one_is_later(tmp_path: Path) -> None:
    """The real bug (ISSUES.md #88), not the boundary case above.

    Once the paging fix gave every power-index snapshot all 30 teams, the
    play-in snapshot (season type 5) started **postdating** the regular-season
    one - measured read-only against the live warehouse on 2026-09-17, this is
    true for every season that has both: 2023 (04-15 vs 04-10), 2025 (04-19 vs
    04-14) and 2026 (04-18 vs 04-13). A regular-season question ("how good were
    the Knicks in the 2026 regular season") used to pick "the latest pre-
    playoff snapshot", which is the play-in one in exactly those seasons -
    answering a regular-season question from the play-in view, silently and
    correctly-looking, because the answer still named the snapshot it read.

    A regular-season question now reads the regular-season snapshot outright
    whenever it holds the team, never comparing its date to any other
    snapshot's. This fixture reproduces the real ordering (regular dated
    *before* play-in) with two BPI values far enough apart that reading the
    wrong one is unmistakable.
    """
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE teams (team_id VARCHAR, abbreviation VARCHAR, display_name VARCHAR, location VARCHAR, name VARCHAR)")
    c.execute("INSERT INTO teams VALUES ('18','NY','New York Knicks','New York','Knicks')")
    c.execute(
        "CREATE TABLE team_power_index (season BIGINT, season_type BIGINT, team_id VARCHAR, last_updated VARCHAR, bpi DOUBLE, bpioffense DOUBLE, bpidefense DOUBLE, "
        "numwins DOUBLE, numlosses DOUBLE, projectedw DOUBLE, projectedl DOUBLE, probmakeplayoffs DOUBLE, probmakeconfchamp DOUBLE, probmaketitlegame DOUBLE, "
        "probwintitle DOUBLE, sosoverall DOUBLE, sosoverallrank DOUBLE)"
    )
    # season_type 2 (regular), stamped first - the real 2023/2025/2026 order.
    c.execute("INSERT INTO team_power_index VALUES (2026,2,'18','2026-04-13T09:43Z',6.9,3.2,3.7,53,29,53,29,100.0,42.0,21.5,7.2,0.503,14)")
    # season_type 5 (play-in), stamped LATER, with a wildly different BPI so a
    # misread is obvious rather than a coincidental match.
    c.execute("INSERT INTO team_power_index VALUES (2026,5,'18','2026-04-18T02:23Z',99.9,50.0,49.9,53,29,53,29,100.0,42.0,21.5,7.2,0.503,14)")
    answer = team_outlook(TemplateContext(con=c, out_dir=tmp_path), {"team": "Knicks", "season": 2026}).answer or ""
    assert "regular-season snapshot" in answer
    assert "BPI +6.9" in answer, "the later play-in snapshot (BPI +99.9) was read instead of the regular-season one"
    assert "BPI +99.9" not in answer


def test_the_outlook_names_its_snapshot_date_and_size(team_ctx: TemplateContext) -> None:
    answer = team_outlook(team_ctx, {"team": "Knicks"}).answer
    assert f"{S} play-in snapshot (updated {S}-04-18, 3 teams)" in answer
    assert "BPI +7.6 (offense +3.2, defense +4.3), 3rd of the 3 teams in the snapshot" in answer
    assert "record 53-29, projected 53-29" in answer
    assert "chances: playoffs 100.0%, conference finals 42.0%, Finals 21.5%, title 7.2%" in answer
    assert "strength of schedule .503, 14th hardest in the league" in answer
    assert f"also has a postseason snapshot ({S}-06-15, 3 teams)" in answer


def test_a_postseason_snapshot_adds_the_playoff_games(team_ctx: TemplateContext) -> None:
    assert "record 69-32 including the playoffs; 53-29 in the regular season" in team_outlook(team_ctx, {"team": "Knicks", "season_type": 3}).answer


def test_a_team_out_before_the_playoffs(team_ctx: TemplateContext) -> None:
    assert "record 44-38, no playoff games" in team_outlook(team_ctx, {"team": "Hornets", "season_type": 3}).answer
    # Only the postseason snapshot holds them, and the answer says it used that one.
    assert "No pre-playoff snapshot for that season holds them" in team_outlook(team_ctx, {"team": "Hornets"}).answer


def test_a_missing_team_is_told_which_snapshots_exist(team_ctx: TemplateContext) -> None:
    """Not "no data" - the snapshots exist, and this team is in neither."""
    assert team_outlook(team_ctx, {"team": "Wizards"}).answer == (
        f"ESPN's power index for {S} has a play-in snapshot ({S}-04-18, 3 teams) and a postseason snapshot ({S}-06-15, 3 teams), and the Washington Wizards are in neither."
    )


def test_a_season_with_no_postseason_snapshot_says_which_one_it_has(team_ctx: TemplateContext) -> None:
    answer = team_outlook(team_ctx, {"team": "Knicks", "season": 2024, "season_type": 3}).answer
    assert "and no postseason snapshot" in answer and "only in a regular-season snapshot (2024-04-14, 1 team)" in answer


def test_a_snapshot_stamped_after_its_season_says_so_and_drops_a_rank_that_is_not_one(team_ctx: TemplateContext) -> None:
    answer = team_outlook(team_ctx, {"team": "Knicks", "season": 2017}).answer
    assert "after the 2017 season ended" in answer
    assert "strength of schedule .490" in answer and "hardest" not in answer


def test_a_season_with_no_snapshot(team_ctx: TemplateContext) -> None:
    assert team_outlook(team_ctx, {"team": "Knicks", "season": 2019}).answer == "ESPN's power index has no 2019 snapshot in the warehouse."


# ---------------- registration ----------------


def test_each_question_is_held_to_the_floor_of_the_table_it_reads() -> None:
    assert check_coverage("team_record", {"season": 1990, "season_type": 2}) is None  # standings, from 1988
    against = check_coverage("team_record", {"season": 1990, "season_type": 2, "opponent": "Boston Celtics"})
    assert against is not None and "1994" in against  # a tally of games, from 1994
    assert check_coverage("team_record", {"season": 1990, "season_type": 3}) is None  # postseason games, from 1988
    stat = check_coverage("team_stat", {"season": 1990, "season_type": 2})
    assert stat is not None and stat.startswith("Team season stats")
    assert check_coverage("team_leaderboard", {"stat": "record", "season": 1990, "season_type": 2}) is None
    assert check_coverage("team_leaderboard", {"stat": "points", "season": 1990, "season_type": 2}) is not None
    outlook = check_coverage("team_outlook", {"season": 2016, "season_type": 2})
    assert outlook is not None and "2017" in outlook


@pytest.mark.parametrize(
    ("stat", "key"),
    [
        ("threePointFieldGoalPct", "three_point_pct"),
        ("defensive rating", "defensive_rating"),
        ("Points Allowed", "opponent_points"),
        ("freeThrowsMade", "free_throws_made"),
        ("pace", "pace"),
        ("wins", "record"),
        ("vibes", None),
        ("", None),
        (None, None),
    ],
)
def test_the_stat_slot_is_read_through_a_whitelist(stat: Any, key: str | None) -> None:
    assert resolve_team_metric(stat) == key


def test_best_and_worst_depend_on_the_metric() -> None:
    assert descending_for(TEAM_METRICS["turnovers"], "best") is False
    assert descending_for(TEAM_METRICS["turnovers"], "most") is True
    assert descending_for(TEAM_METRICS["points"], "worst") is False
    assert descending_for(TEAM_METRICS["pace"], None) is True
    assert descending_for(TEAM_METRICS["pace"], "worst") is False


def test_a_snapshot_with_no_rating_says_so_rather_than_dropping_the_line(tmp_path: Path) -> None:
    """The power index IS this answer's headline, so omitting it silently
    leaves a reader with a record, chances and no sign that the number they
    asked for is missing - short of the truth with no caveat.

    This is live, not hypothetical: ESPN's 2026 regular-season snapshot carries
    a NULL `bpi` for all 30 teams while their records and projections are
    populated, and #88's fix is what makes a 2026 regular-season question read
    it. The other 2026 snapshots do have ratings, and the "also has" line
    already names them."""
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE teams (team_id VARCHAR, abbreviation VARCHAR, display_name VARCHAR, location VARCHAR, name VARCHAR)")
    c.execute("INSERT INTO teams VALUES ('18','NY','New York Knicks','New York','Knicks')")
    c.execute(
        "CREATE TABLE team_power_index (season BIGINT, season_type BIGINT, team_id VARCHAR, last_updated VARCHAR, bpi DOUBLE, bpioffense DOUBLE, bpidefense DOUBLE, "
        "numwins DOUBLE, numlosses DOUBLE, projectedw DOUBLE, projectedl DOUBLE, probmakeplayoffs DOUBLE, probmakeconfchamp DOUBLE, probmaketitlegame DOUBLE, "
        "probwintitle DOUBLE, sosoverall DOUBLE, sosoverallrank DOUBLE)"
    )
    # The rating is NULL; everything else on the row is real, exactly as ESPN
    # serves 2026's regular-season snapshot.
    c.execute("INSERT INTO team_power_index VALUES (2026,2,'18','2026-04-13T09:43Z',NULL,NULL,NULL,53,29,53.0,29.0,100.0,38.1,18.5,5.2,0.506,7)")

    answer = team_outlook(TemplateContext(con=c, out_dir=tmp_path), {"team": "Knicks", "season": 2026}).answer or ""
    assert "no BPI rating in this snapshot" in answer
    # The rest of the row is ESPN's own and still printed.
    assert "record 53-29" in answer
    assert "playoffs 100.0%" in answer


# ---------------- team_record: a calendar month ----------------


def test_a_named_month_filters_the_games_tally(team_ctx: TemplateContext) -> None:
    """standings has no per-game date, so a month reaches this through
    `games` instead - the Knicks' one November game (g1) is a win."""
    answer = team_record(team_ctx, {"team": "Knicks", "situation": "in november"}).answer or ""
    assert "went 1-0 (1.000) in November in the" in answer


def test_a_named_month_with_no_games_says_so(team_ctx: TemplateContext) -> None:
    """g2's UTC stamp of January 1st is December 31st Eastern, so the Knicks
    have no January game this season even though one game's UTC date says so -
    the whole reason a month reads the Eastern date rather than the stored one."""
    answer = team_record(team_ctx, {"team": "Knicks", "situation": "in january"}).answer or ""
    assert answer == f"The New York Knicks played no games in January in the {S} regular season."


def test_a_named_month_combines_with_venue(team_ctx: TemplateContext) -> None:
    """g3, a neutral-site game, is excluded from a venue narrowing - only g2
    (away, a loss) is a December game at a real venue."""
    answer = team_record(team_ctx, {"team": "Knicks", "situation": "in december", "venue": "away"}).answer or ""
    assert "0-1 (.000) in December on the road" in answer


def test_a_situation_naming_no_calendar_narrowing_is_still_refused(team_ctx: TemplateContext) -> None:
    """check_scope lets any `situation` value through (see HONORED_SCOPING);
    team_record itself now reads the full calendar narrowing (step 3, K1: a
    weekday, a month, a fixed holiday, "since <day>" - not only a bare month,
    #84's original scope), and refuses only what names none of those - an
    age, a conference, "since returning"."""
    with pytest.raises(TemplateUnsupported):
        team_record(team_ctx, {"team": "Knicks", "situation": "18 year old"})
    with pytest.raises(TemplateUnsupported):
        team_record(team_ctx, {"team": "Knicks", "situation": "since returning from injury"})


def test_since_a_calendar_day_narrows_a_teams_record(team_ctx: TemplateContext) -> None:
    """Step 3, K1: "since november 20th" is a calendar narrowing the player
    relation already read (`_SINCE_DAY`) and the team relation now does too.
    g1 (the Knicks' Nov 19 Eastern-date win over the Spurs) falls BEFORE the
    cutoff and drops out; g2 (Dec 31 Eastern, a loss at San Antonio) and g3
    (Dec 13 Eastern, a neutral-site Cup semifinal win at Boston) fall on or
    after it and count. g4 (Dec 16 Eastern) also falls after the cutoff but
    is the neutral-site NBA Cup final - correctly excluded from a
    regular-season RECORD by `games_scope`, the same as the plain season
    record - so the narrowed record is 1-1, not 2-1 or the season's 3-1."""
    answer = team_record(team_ctx, {"team": "Knicks", "situation": "since november 20th"}).answer
    assert "1-1" in answer
    assert "since November 20" in answer
    assert "away 0-1, neutral site 1-0" in answer


def test_a_weekday_narrows_a_teams_record(team_ctx: TemplateContext) -> None:
    """Step 3, K1: a weekday narrowing ("on Wednesdays") reads
    `TeamNarrowed.narrow_calendar` too - not only a fixed calendar day. g1's
    Eastern date is computed here rather than hardcoded, so the assertion
    holds whatever `current_season()` happens to be when the suite runs. g1
    (Nov 19 Eastern, a win) and g2 (Dec 31 Eastern, a loss) are exactly six
    weeks apart and so share a weekday, giving 1-1 rather than the plain
    month's 1-0 or the season's 3-1."""
    from datetime import date

    weekday = date(int(S) - 1, 11, 19).strftime("%A")
    answer = team_record(team_ctx, {"team": "Knicks", "situation": f"on {weekday}s"}).answer
    assert "1-1" in answer
    assert f"on {weekday}s" in answer


def test_split_by_month_breaks_the_record_out(team_ctx: TemplateContext) -> None:
    """The Knicks' three regular-season games this year: g1 in November (win),
    g3 in December (a neutral-site win that still counts) and g2, whose UTC
    January 1st stamp is December 31st Eastern - so December is 1-1, and
    January never appears at all."""
    result = team_record(team_ctx, {"team": "Knicks", "split": "month"})
    assert result.data["months"] == [
        {"season": S, "month": "November", "games": 1, "wins": 1, "losses": 0},
        {"season": S, "month": "December", "games": 2, "wins": 1, "losses": 1},
    ]
    assert "January" not in (result.answer or "")


def test_an_unnamed_split_is_refused(team_ctx: TemplateContext) -> None:
    with pytest.raises(TemplateUnsupported):
        team_record(team_ctx, {"team": "Knicks", "split": "starter_bench"})


def test_a_default_limit_does_not_block_a_month_narrowing_or_split(team_ctx: TemplateContext) -> None:
    """The router fills `limit` with a default whether or not the question
    asked for one, the same shape team_record already treats as noise for a
    bare "last N games" refusal - but only once a real month narrowing or a
    by-month split is what is actually driving the answer."""
    assert "1-0" in (team_record(team_ctx, {"team": "Knicks", "situation": "in november", "limit": 12}).answer or "")
    assert team_record(team_ctx, {"team": "Knicks", "split": "month", "limit": 12}).data["months"]
    with pytest.raises(TemplateUnsupported, match="game_log"):
        team_record(team_ctx, {"team": "Knicks", "limit": 12})


# ---------------- team_record: `season_type_unstated` combines both types (F116) ----------------


def test_team_record_combines_both_season_types_for_one_season(team_ctx: TemplateContext) -> None:
    """F116 ("warriors all-time record including playoff record at away"):
    `season_type_unstated` (c9930ad's flag on the player relation, honored
    here for the first time) combines both season types rather than
    silently answering one. The Knicks' season-S regular record (53-29, from
    standings - `test_a_season_record_is_the_standings_line`) plus their
    postseason series against the Celtics (p1 W, p2 L, p3 W, p4 W - 3-1) sum
    to 56-30, with each component named."""
    result = team_record(team_ctx, {"team": "Knicks", "season": S, "season_type_unstated": True})
    assert result.data["wins"] == 56 and result.data["losses"] == 30
    # #204 (ISSUES.md): the regular half reads standings, which carries no
    # first_season at all, so it names none; the postseason half always
    # reads games, which does - here trivially the named season itself,
    # since both halves cover the same one year.
    assert result.data["regular_season"] == {"wins": 53, "losses": 29, "first_season": None}
    assert result.data["postseason"] == {"wins": 3, "losses": 1, "first_season": S}
    assert "56-30" in result.answer
    assert "53-29 (.646) regular season," in result.answer
    assert f"3-1 (.750) playoffs from {S})" in result.answer


def test_team_record_combined_types_honors_opponent(team_ctx: TemplateContext) -> None:
    """The combined reading applies the SAME narrowing to both season types -
    here, an opponent. Knicks-vs-Celtics: g3 is their only regular-season
    meeting (Knicks away, win - 1-0); the postseason series is p1 (home W),
    p2 (home L), p3 (away W), p4 (away W) - 3-1. Combined: 4-1."""
    result = team_record(team_ctx, {"team": "Knicks", "opponent": "Celtics", "season": S, "season_type_unstated": True})
    assert result.data["wins"] == 4 and result.data["losses"] == 1
    # An opponent forces both halves through _games_record (never standings),
    # so both carry the named season as their own first_season - #204,
    # ISSUES.md.
    assert result.data["regular_season"] == {"wins": 1, "losses": 0, "first_season": S}
    assert result.data["postseason"] == {"wins": 3, "losses": 1, "first_season": S}


def test_team_record_combined_career_names_each_halfs_own_start(team_ctx: TemplateContext) -> None:
    """#204 (ISSUES.md): the combined-season-types sentence used to carry only
    "Note:" lines from its halves, so the reader could not see that they
    start in different seasons - true here: the Knicks' standings reach back
    to 1990 in this fixture, but their earliest postseason game ("old1") is
    LABELED 1990 and actually played in 1991 - read by the calendar year it
    was played, per team_games.py, not by ESPN's pre-1993-94 started-year
    label, so the postseason half starts a season LATER than the regular
    one, the opposite direction of the Warriors example in the issue."""
    result = team_record(team_ctx, {"team": "Knicks", "span": "career", "season_type_unstated": True})
    assert result.data["regular_season"]["first_season"] == 1990
    assert result.data["postseason"]["first_season"] == 1991
    assert "regular season from 1989-90" in result.answer
    assert "playoffs from 1991" in result.answer


def test_team_record_combined_types_refuses_game_n(team_ctx: TemplateContext) -> None:
    """A game of a playoff series names one season type outright; combining
    both at once has nothing for it to number."""
    with pytest.raises(TemplateUnsupported):
        team_record(team_ctx, {"team": "Knicks", "season_type_unstated": True, "game_n": 1})


def test_team_record_combined_types_refuses_a_month_split(team_ctx: TemplateContext) -> None:
    with pytest.raises(TemplateUnsupported):
        team_record(team_ctx, {"team": "Knicks", "season_type_unstated": True, "split": "month"})


# ---------------- team_record: `until` beside `since` (step 3, K1) ----------------


def test_until_bounds_a_since_span_record(team_ctx: TemplateContext) -> None:
    """F103's shape, on team_record: "meetings from 1991 to {S} postseason"
    should read a BOUNDED range, not open-ended "since 1991". Across the
    span, the Knicks are 3-1 in the {S} postseason against Boston (p1 W, p2
    L, p3 W, p4 W) and lost the 1990-labeled/1991-played Finals game (old1) -
    4-2 combined, said as "from 1991 through {S}"."""
    answer = team_record(team_ctx, {"team": "Knicks", "opponent": "Celtics", "season_type": 3, "since": 1991, "until": S}).answer
    assert "4-2" in answer
    assert f"from 1991 through {S}" in answer


def test_until_with_no_since_is_refused(team_ctx: TemplateContext) -> None:
    """The slot contract: `until` only ever arrives beside `since` (a decade,
    or a named range the router files as both at once) - a stray `until` with
    no `since` is refused rather than silently read as nothing."""
    with pytest.raises(TemplateUnsupported, match="until"):
        team_record(team_ctx, {"team": "Knicks", "until": 2020})


def test_until_before_since_is_refused(team_ctx: TemplateContext) -> None:
    with pytest.raises(TemplateUnsupported, match="until"):
        team_record(team_ctx, {"team": "Knicks", "since": 2020, "until": 2010})


def test_team_record_by_month_spans_since_until_with_one_table_per_season(team_ctx: TemplateContext) -> None:
    """F095 (ISSUES.md): "Knicks record by month" over a since/until-bounded
    span - here 1994 through the current season - reads one table PER SEASON
    rather than merging distinct years' Novembers into one row or silently
    keeping only the span's first year. 1994 holds one game (`ph`, January);
    the current season holds the same November/December games the plain
    by-month test above reads."""
    result = team_record(team_ctx, {"team": "Knicks", "split": "month", "since": 1994, "until": S})
    seasons = {row["season"] for row in result.data["months"]}
    assert seasons == {1994, S}
    assert result.answer.count("The New York Knicks, record by month") == 2
    jan_1994 = next(row for row in result.data["months"] if row["season"] == 1994)
    assert jan_1994 == {"season": 1994, "month": "January", "games": 1, "wins": 1, "losses": 0}


# ---------------- team_leaderboard: `since` (step 3, C4b) ----------------


def test_a_leaderboard_since_a_season_tallies_the_relation_across_seasons(team_ctx: TemplateContext) -> None:
    """ "nba team with least playoff wins since 2022" (ISSUES.md) used to
    refuse for want of `since`; the relation now tallies wins/losses across
    the span itself, grouped by team. The fixture's Knicks-Celtics postseason
    meetings since 1991: the 1991 Finals (labeled 1990 - "old1", Celtics
    won), a 1994 game filed under two season labels and deduped to one
    ("php", Knicks won), and this season's 3-1 Celtics series (p1-p4) -
    Knicks 4-2, Celtics 2-4. Every OTHER franchise in the fixture (San
    Antonio, OKC, Washington, Charlotte) reads 0-0 rather than being left off
    the ranking (F100, ISSUES.md): a team with no games in the span is a real
    zero, not a non-participant to drop the way a single-season postseason
    ranking already drops one."""
    result = team_leaderboard(team_ctx, {"stat": "record", "season_type": 3, "since": 1991})
    assert "since 1991" in (result.answer or "")
    teams = {t["team"]: t["display"] for t in result.data["teams"]}
    assert teams == {
        "New York Knicks": "4-2 (.667)",
        "Boston Celtics": "2-4 (.333)",
        "San Antonio Spurs": "0-0",
        "Oklahoma City Thunder": "0-0",
        "Washington Wizards": "0-0",
        "Charlotte Hornets": "0-0",
    }


def test_a_leaderboard_since_and_a_named_season_at_once_is_refused(team_ctx: TemplateContext) -> None:
    with pytest.raises(TemplateUnsupported):
        team_leaderboard(team_ctx, {"stat": "record", "season_type": 3, "since": 1991, "season": S})


def test_a_leaderboard_since_refuses_a_rate_metric(team_ctx: TemplateContext) -> None:
    """A season line (team_season_stats) has no way to sum across a span of
    seasons yet - refused by name, rather than answered for one season under
    a since-shaped question that asked for a range."""
    with pytest.raises(TemplateUnsupported, match="span of seasons"):
        team_leaderboard(team_ctx, {"stat": "points", "since": 2020})


def test_a_leaderboard_since_refuses_a_venue_split(team_ctx: TemplateContext) -> None:
    with pytest.raises(TemplateUnsupported):
        team_leaderboard(team_ctx, {"stat": "record", "season_type": 3, "since": 1991, "venue": "home"})


# ---------------- team_leaderboard: `until` beside `since` (step 3, K1) ----------------


def test_a_leaderboard_until_bounds_the_since_span(team_ctx: TemplateContext) -> None:
    """F103's shape: "best playoff record from 1991 to {S}" reads a BOUNDED
    range. Narrowed to `until=1991` alone the span holds only the 1991
    Finals game (`old1`), where the open-ended `since=1991` test above also
    picks up "php" (1994) and this season's series - so the Celtics, who won
    that single 1991 game, lead 1.000 and the Knicks are winless, the mirror
    image of the open-ended ranking."""
    result = team_leaderboard(team_ctx, {"stat": "record", "season_type": 3, "since": 1991, "until": 1991})
    assert "seasons 1991-1991" in (result.answer or "")
    teams = {t["team"]: t["display"] for t in result.data["teams"]}
    assert teams["New York Knicks"] == "0-1 (.000)"
    assert teams["Boston Celtics"] == "1-0 (1.000)"
    # F100: the other four fixture franchises played nothing in 1991 and read
    # 0-0 rather than being left off the ranking.
    assert teams["San Antonio Spurs"] == "0-0"
    assert teams["Charlotte Hornets"] == "0-0"


def test_a_leaderboard_until_with_no_since_is_refused(team_ctx: TemplateContext) -> None:
    with pytest.raises(TemplateUnsupported, match="until"):
        team_leaderboard(team_ctx, {"stat": "record", "season_type": 3, "until": 1991})


# ---------------- team_quarter_points: the team-games relation (step 3, C4b) ----------------


@pytest.fixture
def tqp_ctx(tmp_path: Path) -> TemplateContext:
    """A fixture built for team_quarter_points' new cells: linescores across
    four regular-season games (two home, two away, chronologically ordered
    for an ``order``/``limit`` window), one earlier regular season for
    ``since``, and a four-game Knicks-Celtics postseason series for
    ``game_n``."""
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE teams (team_id VARCHAR, abbreviation VARCHAR, display_name VARCHAR)")
    c.execute("INSERT INTO teams VALUES ('18','NY','New York Knicks'),('2','BOS','Boston Celtics'),('5','LAL','Los Angeles Lakers')")
    c.execute(
        "CREATE TABLE games (event_id VARCHAR, season INTEGER, season_type INTEGER, date VARCHAR, "
        "home_team_id VARCHAR, away_team_id VARCHAR, home_score INTEGER, away_score INTEGER, winner_team_id VARCHAR, "
        "home_linescores VARCHAR, away_linescores VARCHAR, neutral_site BOOLEAN, venue_city VARCHAR)"
    )
    rows: list[tuple[Any, ...]] = [
        # Season S-1, for `since` - the earliest game, Knicks Q1 40.
        ("d1", S - 1, 2, f"{S - 2}-11-01T22:00Z", "18", "2", 120, 90, "18", "40,30,25,25", "20,20,25,25", False, "New York"),
        # Season S regular season, oldest to newest - e3/e4 are the "last 2".
        ("e1", S, 2, f"{S - 1}-11-10T22:00Z", "18", "2", 112, 95, "18", "30,25,28,29", "20,25,25,25", False, "New York"),
        ("e2", S, 2, f"{S - 1}-12-05T22:00Z", "5", "18", 100, 96, "5", "25,25,25,25", "20,20,28,28", False, "Los Angeles"),
        ("e3", S, 2, f"{S}-01-15T22:00Z", "18", "5", 108, 100, "18", "25,28,27,28", "20,25,25,30", False, "New York"),
        ("e4", S, 2, f"{S}-02-20T22:00Z", "2", "18", 110, 105, "2", "30,25,25,30", "15,30,30,30", False, "Boston"),
        # Season S postseason: a 4-game Knicks-Celtics series, for `game_n`.
        ("p1", S, 3, f"{S}-04-19T23:00Z", "18", "2", 100, 90, "18", "18,27,25,30", "20,20,25,25", False, "New York"),
        ("p2", S, 3, f"{S}-04-21T23:00Z", "2", "18", 95, 101, "18", "20,25,25,25", "22,27,26,26", False, "Boston"),
        ("p3", S, 3, f"{S}-04-24T23:00Z", "18", "2", 105, 95, "18", "19,28,29,29", "20,25,25,25", False, "New York"),
        ("p4", S, 3, f"{S}-04-26T23:00Z", "2", "18", 100, 110, "18", "20,25,25,30", "21,30,30,29", False, "Boston"),
    ]
    c.executemany("INSERT INTO games VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    real_games.build_table(c, {"games", "teams"})
    return TemplateContext(con=c, out_dir=tmp_path)


def test_a_quarter_points_window_reads_the_last_n_games(tqp_ctx: TemplateContext) -> None:
    """ "show sixers first quarter scoring for their last 10 games" and
    "trailblazers stats last 10 games 3 point average 1st quarter"
    (ISSUES.md, live yardstick failures) both refused for want of `order`/
    `limit` - the relation now cuts to the window before the linescores are
    summed. The Knicks' last 2 regular-season games by date are e3 (Q1 25)
    and e4 (Q1 15); e1 and e2 are older and must not be counted."""
    result = team_quarter_points(tqp_ctx, {"team": "Knicks", "period": 1, "season": S, "order": "recent", "limit": 2})
    assert [g["points"] for g in result.data["games"]] == [25, 15]
    assert result.data["total"] == 40
    assert "last 2 games" in (result.answer or "")


def test_a_quarter_points_window_oldest_first(tqp_ctx: TemplateContext) -> None:
    result = team_quarter_points(tqp_ctx, {"team": "Knicks", "period": 1, "season": S, "order": "first", "limit": 2})
    assert [g["points"] for g in result.data["games"]] == [30, 20]
    assert "first 2 games" in (result.answer or "")


def test_a_quarter_points_venue_narrows_the_games(tqp_ctx: TemplateContext) -> None:
    """The Knicks' two home games this season are e1 (Q1 30) and e3 (Q1 25);
    e2 and e4, both on the road, must not be counted."""
    result = team_quarter_points(tqp_ctx, {"team": "Knicks", "period": 1, "season": S, "venue": "home"})
    assert [g["points"] for g in result.data["games"]] == [30, 25]
    assert result.data["total"] == 55


def test_a_quarter_points_date_reads_one_game(tqp_ctx: TemplateContext) -> None:
    result = team_quarter_points(tqp_ctx, {"team": "Knicks", "period": 1, "date": f"{S}-01-15"})
    assert result.data["games"] == [{"date": f"{S}-01-15", "opponent": "Los Angeles Lakers", "points": 25}]
    assert f"{S}-01-15" in (result.answer or "")


def test_a_quarter_points_game_n_reads_one_game_of_the_series(tqp_ctx: TemplateContext) -> None:
    """Game 4 of the Knicks-Celtics postseason series is p4 - the Knicks'
    road game, Q1 21 from their own (away) linescore."""
    result = team_quarter_points(tqp_ctx, {"team": "Knicks", "period": 1, "season": S, "season_type": 3, "game_n": 4})
    assert result.data["games"] == [{"date": f"{S}-04-26", "opponent": "Boston Celtics", "points": 21}]
    assert "game 4" in (result.answer or "")


def test_a_quarter_points_game_n_regular_season_is_refused(tqp_ctx: TemplateContext) -> None:
    """A series has games 1-7; a regular season has nothing "game 4" names."""
    with pytest.raises(TemplateUnsupported):
        team_quarter_points(tqp_ctx, {"team": "Knicks", "period": 1, "season": S, "season_type": 2, "game_n": 4})


def test_a_quarter_points_since_spans_more_than_one_season(tqp_ctx: TemplateContext) -> None:
    """Every Knicks regular-season game on record since season S-1: d1 (Q1
    40) plus the four season-S games (30, 20, 25, 15) - 130 total across 5
    games, the postseason series left out since this asks for no season type
    at all (the default is the regular season)."""
    result = team_quarter_points(tqp_ctx, {"team": "Knicks", "period": 1, "since": S - 1})
    assert len(result.data["games"]) == 5
    assert result.data["total"] == 130
    assert f"since {S - 1}" in (result.answer or "")


def test_a_quarter_points_until_bounds_the_since_span(tqp_ctx: TemplateContext) -> None:
    """Step 3, K1: "until" reaches team_quarter_points "for free" through
    `scoped_team` - the same shared step `since` already reaches through.
    Bounded to season {S-1} alone (`since=until={S-1}`), only d1 (Q1 40)
    counts; the open-ended since-only test above also picks up all four
    season-{S} games."""
    result = team_quarter_points(tqp_ctx, {"team": "Knicks", "period": 1, "since": S - 1, "until": S - 1})
    assert len(result.data["games"]) == 1
    assert result.data["total"] == 40
    assert f"from {S - 1} through {S - 1}" in (result.answer or "")


def test_a_quarter_points_situation_narrows_by_calendar_month(tqp_ctx: TemplateContext) -> None:
    """Step 3, K1: `situation` reaches team_quarter_points through the same
    shared `team_games` step every other team template narrows by it -
    "november" keeps only e1 (Q1 30), the season's other three games (e2 in
    December, e3 in January, e4 in February) falling out."""
    result = team_quarter_points(tqp_ctx, {"team": "Knicks", "period": 1, "season": S, "situation": "in november"})
    assert [g["points"] for g in result.data["games"]] == [30]
    assert "in November" in (result.answer or "")


def test_a_quarter_points_opponent_and_venue_compose(tqp_ctx: TemplateContext) -> None:
    """Both narrowings apply together, as every other reader of the relation's
    narrowing composes them: the Knicks' one home game against the Celtics
    this season is e1 (Q1 30); e3 is a home game but against the Lakers."""
    result = team_quarter_points(tqp_ctx, {"team": "Knicks", "opponent": "Celtics", "period": 1, "season": S, "venue": "home"})
    assert [g["points"] for g in result.data["games"]] == [30]


# ---------------- record_when's team branch: `since`/`game_n` (step 3, C4b) ----------------


def test_record_when_team_since_tallies_across_seasons(team_ctx: TemplateContext) -> None:
    """The Celtics' postseason scores since 1991 (old1, php, p1-p4): 100, 90,
    100, 101, 95, 110. Four of the six reach a 100-point threshold (old1,
    p1, p2, p4); that pool's own record is 2-2 (old1 W, p1 L, p2 W, p4 L).
    The two under it, php and p3, are both losses. Refused before (ISSUES.md)
    for want of `since` on the team branch."""
    result = record_when(team_ctx, {"team": "Celtics", "stat": "points", "threshold": 100, "season_type": 3, "since": 1991})
    assert result.data["reached"]["games"] == 4
    assert (result.data["reached"]["wins"], result.data["reached"]["losses"]) == (2, 2)
    assert result.data["fell_short"]["games"] == 2
    assert (result.data["fell_short"]["wins"], result.data["fell_short"]["losses"]) == (0, 2)
    assert "since 1991" in (result.data["span"] or "")


def test_record_when_team_since_and_a_named_season_at_once_is_refused(team_ctx: TemplateContext) -> None:
    with pytest.raises(TemplateUnsupported):
        record_when(team_ctx, {"team": "Celtics", "stat": "points", "threshold": 100, "season_type": 3, "since": 1991, "season": S})


def test_record_when_team_game_n_narrows_to_one_game_of_the_series(team_ctx: TemplateContext) -> None:
    """Game 4 of the Knicks-Celtics series is p4, where the Celtics scored
    110 and lost - one game, reaching the threshold, in the loss column."""
    result = record_when(team_ctx, {"team": "Celtics", "stat": "points", "threshold": 100, "season_type": 3, "season": S, "game_n": 4})
    assert result.data["reached"]["games"] == 1
    assert (result.data["reached"]["wins"], result.data["reached"]["losses"]) == (0, 1)
    assert result.data["fell_short"]["games"] == 0


def test_record_when_team_game_n_regular_season_is_refused(team_ctx: TemplateContext) -> None:
    with pytest.raises(TemplateUnsupported):
        record_when(team_ctx, {"team": "Celtics", "stat": "points", "threshold": 100, "season_type": 2, "season": S, "game_n": 4})


# ---------------- streak's team/league branches: `since` (step 3, C4b) ----------------


def test_streak_team_since_searches_more_than_one_season(team_ctx: TemplateContext) -> None:
    """A team's own streak still runs within ONE season even under `since`
    (:func:`_longest_runs`'s own ``("team_id", "season")`` partition, the
    record-book rule the docstring already states) - `since` widens which
    SEASONS are searched, not whether a run crosses between them. The
    Celtics' postseason since 1991: one win in the 1991 Finals (old1) and one
    win in this year's series (p2), each alone in its own season, so the
    longest run either season held is exactly 1 game."""
    result = streak(team_ctx, {"team": "Celtics", "season_type": 3, "since": 1991})
    assert result.data["streaks"][0]["length"] == 1
    assert "since 1991" in (result.data["span"] or "")


def test_streak_league_since_reads_every_teams_seasons_in_the_span(team_ctx: TemplateContext) -> None:
    """The league-wide (no team, no player) win-streak branch, `since`-bounded:
    the Knicks' 3-1 postseason series this year holds a 2-game win streak
    (p1, then p3-p4 after the one loss) - the longest of anyone's since 1991."""
    result = streak(team_ctx, {"season_type": 3, "since": 1991})
    assert result.data["streaks"][0]["length"] == 2
    assert "New York Knicks" in result.data["streaks"][0]["name"]
