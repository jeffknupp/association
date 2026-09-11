"""Tests for the team templates: records, season numbers, rankings and BPI.

The fixture is small but built out of the warehouse's real faults, each one
measured before it was written down here: a 0-0 phantom game with no winner, a
second event id for a game already listed, the NBA Cup final (a regular-season
game no standings count), a neutral-site game, a season listed under two labels
(1993 and 1994), a postseason labelled by the year its season started, and a
postseason whose game list is one game short of the team's own totals.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb
import pytest

from association.query.team_metrics import TEAM_METRICS, descending_for, resolve_team_metric
from association.query.templates import (
    TemplateContext,
    TemplateUnsupported,
    check_coverage,
    check_scope,
    team_leaderboard,
    team_outlook,
    team_record,
    team_stat,
)
from association.season import current_season

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
        # Labelled 1990, played in 1991: the 1991 postseason.
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


def test_team_record_honours_venue_opponent_and_span_but_not_order() -> None:
    check_scope("team_record", {"venue": "home", "opponent": "Boston Celtics", "span": "career"})
    with pytest.raises(TemplateUnsupported, match="different span"):
        check_scope("team_record", {"order": "recent"})


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
