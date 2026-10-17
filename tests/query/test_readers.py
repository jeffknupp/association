"""The compiler's readers over the relations - a player's games, the
league's, a period's, the shots and NetPoints - each called through the
answer side for its key (``routed.answered_as``) over the fixture
warehouses here, and the source-reading tests that hold every reader to
its relation's cell table."""

import re
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import duckdb
import pytest
from routed import answered_as, measure_of, planned_answer, planned_query, said

from association.fetch.repairs import real_games
from association.nba.season import current_season
from association.nba.season import eastern_date as _eastern_date_of
from association.query import shotchart
from association.query.answer import AnswerContext
from association.query.compose.core import Query, Unsupported, _compile_pair, _compile_run, _resolve_pair, compile_over, compile_query, rows_of
from association.query.compose.logs import _player_log, _player_log_mixed, _team_log, _team_log_mixed, read_player_log, read_team_log
from association.query.compose.pairs import _pair_absence, _pair_no_meetings, _pair_result, read_player_matchup
from association.query.compose.periods import _period_by_quarter, _period_log, _period_redirect, read_period_split
from association.query.compose.plan import cells_stated, cells_unhonored, plan, plan_point
from association.query.compose.records import _record_when_team_result, read_record_when, read_team_record_when
from association.query.compose.runs import _streak_league_result, _streak_league_teams_result, _streak_player_result, _streak_team_result, read_streak, read_team_streak
from association.query.compose.say import refusal_phrase
from association.query.compose.seasons import _player_line_advanced, _player_line_career, _player_line_season, read_player_line
from association.query.compose.sentence import team_sentence
from association.query.compose.shots import read_shot_chart, read_shot_distance
from association.query.compose.splits import _player_splits, _team_splits, read_player_splits, read_team_splits
from association.query.compose.stats import _player_stat_meetings, _player_stat_result, read_player_stat
from association.query.compose.team import TeamQuery, _compile_team_run, compile_team_count, compile_team_line, compile_team_range, compile_team_run, run_team
from association.query.entities import MAX_CANDIDATES, Availability, Entity, collect_name_readings, resolve_player
from association.query.measures import PERIOD_RATE_STATS
from association.query.metrics import LEADERBOARD_METRICS, PER_GAME_MIN_GAMES, PER_GAME_MIN_POSTSEASON_GAMES
from association.query.parse import with_point
from association.query.player_games import BOTH_SEASON_TYPES, PERIOD_COLUMNS, PERIOD_RATES, Narrowed, period_distrust
from association.query.player_relation import scoped_games, scoped_player, span_of
from association.query.point import default_point
from association.query.reading import (
    GAME_HIGHS,
    GAMES_COUNTED,
    LINE_RECORD,
    LINE_RUNS,
    NETPOINTS_FINGERPRINT,
    NETPOINTS_RATINGS,
    PERIOD_LOG,
    PERIOD_RANKING,
    PLAYER_COMPARISON,
    PLAYER_LINE,
    PLAYER_LOG,
    PLAYER_MEETINGS,
    PLAYER_RANKING,
    PLAYER_SPLITS,
    PRESENCE_SPLIT,
    SEASON_HISTORY,
    SHOT_CHART,
    SHOT_DISTANCE,
    TEAM_MEETINGS,
    TEAM_PERIOD_TOTAL,
    TEAM_RECORD,
    Aggregate,
    Companion,
    Cuts,
    DeclineReason,
    Filtered,
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
from association.query.result import Unanswered
from association.query.season_text import season_phrase
from association.query.shotchart import SHOT_AVAILABILITY
from association.query.subject import Subject

player_log = answered_as(PLAYER_LOG)
player_ranking = answered_as(PLAYER_RANKING)
period_log = answered_as(PERIOD_LOG)
player_comparison = answered_as(PLAYER_COMPARISON)
season_history = answered_as(SEASON_HISTORY)
player_splits = answered_as(PLAYER_SPLITS)
player_meetings = answered_as(PLAYER_MEETINGS)
player_line = answered_as(PLAYER_LINE)
line_runs = answered_as(LINE_RUNS)
netpoints_ratings = answered_as(NETPOINTS_RATINGS)
netpoints_fingerprint = answered_as(NETPOINTS_FINGERPRINT)
team_meetings = answered_as(TEAM_MEETINGS)
team_period_total = answered_as(TEAM_PERIOD_TOTAL)
shot_distance = answered_as(SHOT_DISTANCE)
shot_chart = answered_as(SHOT_CHART)
period_ranking = answered_as(PERIOD_RANKING)
team_record = answered_as(TEAM_RECORD)
presence_split = answered_as(PRESENCE_SPLIT)
game_highs = answered_as(GAME_HIGHS)
games_counted = answered_as(GAMES_COUNTED)


@pytest.fixture
def con(tmp_path: Path) -> AnswerContext:
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    # event_id/minutes/did_not_play are what an empty box score is detected by;
    # the defaults make every game here a real one.
    c.execute(
        "CREATE TABLE player_box_stats (athlete_id VARCHAR, season INTEGER, season_type INTEGER, points INTEGER, rebounds INTEGER, "
        "event_id VARCHAR, minutes INTEGER DEFAULT 30, did_not_play BOOLEAN DEFAULT FALSE)"
    )
    c.execute("INSERT INTO players VALUES ('1','Luka Doncic'),('2','Shai Gilgeous-Alexander'),('3','Bench Guy')")
    season = current_season()
    rows: list[tuple[Any, ...]] = []
    rows += [("1", season, 2, 35, 5)] * 4  # Luka: 4 games of 30+
    rows += [("2", season, 2, 31, 4)] * 2  # SGA: 2 games of 30+
    rows += [("1", season, 2, 12, 22)] * 3  # Luka: 3 games of 20+ rebounds
    rows += [("3", season, 2, 40, 1)] * 9  # postseason-only below, so excluded
    rows += [("3", season, 3, 40, 1)] * 9
    rows += [("2", season - 1, 2, 40, 1)] * 7  # previous season, excluded by default
    c.executemany("INSERT INTO player_box_stats (athlete_id, season, season_type, points, rebounds) VALUES (?,?,?,?,?)", rows)
    # The relation every box-score reader reads through (query/player_games.py)
    # joins `games` on (event_id, season) and reads `player_name` off the log, as
    # the real view does. A view mirroring the warehouse's shape - an id per row,
    # a game per id - keeps a fixture that only wrote the stored table honest.
    c.execute(
        "CREATE VIEW player_game_log AS SELECT pbs.* REPLACE (COALESCE(pbs.event_id, 'e' || pbs.rowid) AS event_id), p.display_name AS player_name, "
        "NULL::VARCHAR AS game_date, NULL::VARCHAR AS opponent_abbr FROM player_box_stats pbs LEFT JOIN players p ON p.athlete_id = pbs.athlete_id"
    )
    # The date, home side and winner the compiler reads off `games` beside the
    # log (compose.core._row_select), as the warehouse view carries them.
    c.execute("CREATE VIEW games AS SELECT DISTINCT event_id, season, season_type, game_date AS date, NULL::VARCHAR AS home_team_id, NULL::VARCHAR AS winner_team_id FROM player_game_log")
    return AnswerContext(con=c, out_dir=tmp_path)


def test_answers_the_question_the_agent_kept_getting_wrong(con: AnswerContext) -> None:
    result = games_counted(con, Reading(lines=(Line(measure="points", value=30, as_typed="30 points", keyed=True),), measure=Measure(key="points", as_typed="points")))
    assert result.data["leaders"][0] == {"player": "Bench Guy", "games": 9}
    assert {"player": "Luka Doncic", "games": 4} in result.data["leaders"]


def test_a_floor_at_the_counts_own_number_on_another_stat_is_a_second_line(con: AnswerContext) -> None:
    """ "how many games did wemby have 30 points playing 30 or more minutes":
    a floor of minutes at the count's own number is a second line, not the
    count. The keyed line's words decide, never its number alone: Luka's 4
    games of 35 points, each of 30 minutes, said with both lines."""
    from association.query.line import read_lines
    from association.query.point import lines_under

    worded = lines_under(GAMES_COUNTED, read_lines("how many games did luka have 30 points playing 30 or more minutes"))
    assert worded.stat == "points"
    result = games_counted(con, Reading(who=Who(kind="player", players=("Luka Doncic",)), lines=worded.lines, measure=measure_of("points")))
    assert result.data["leaders"] == [{"player": "Luka Doncic", "games": 4}]
    assert "games with 30+ points and at least 30 minutes" in (result.text or "")


def test_a_line_below_a_number_counts_the_games_under_it_on_the_stat_the_words_name(con: AnswerContext) -> None:
    """ "Sga games with under 14 fta", with the model's nearest stat beside it:
    a phrase holding the count's own line ("20 points" inside "under 20
    points", as the lines tagger reads the words) IS the count, and its
    direction and its column win over the model's stat - never 14 or MORE.
    Luka has 4 games of 35 and 3 of 12. A phrase on another stat at the same
    number is a second line, not the count (the keyed line's words, never its
    number alone: "30 points playing 30 or more minutes")."""
    from association.query.line import read_lines
    from association.query.point import lines_under

    worded = lines_under(GAMES_COUNTED, read_lines("luka games with under 20 points"))
    result = games_counted(con, Reading(who=Who(kind="player", players=("Luka Doncic",)), lines=worded.lines, measure=measure_of("rebounds")))
    assert result.data["leaders"] == [{"player": "Luka Doncic", "games": 3}]
    assert "games with under 20 points" in (result.text or "") and "20+" not in (result.text or "")
    # A phrase with ANOTHER number is a second line beside the count.
    both = games_counted(
        con,
        Reading(
            who=Who(kind="player", players=("Luka Doncic",)),
            lines=(Line(measure="points", value=30, as_typed="30 points", keyed=True), Line(measure="rebounds", op="<", value=10, as_typed="under 10 rebounds", narrows=True)),
            measure=Measure(key="points", as_typed="points"),
        ),
    )
    assert both.data["leaders"] == [{"player": "Luka Doncic", "games": 4}]
    assert "30+ points and under 10 rebounds" in (both.text or "")
    # A stat no threshold is kept on beside one line and no threshold (the
    # parser reads "fta" as freeThrowsAttempted, which has no count column):
    # the line IS the count, on its own column - never a refusal.
    lined = games_counted(
        con,
        Reading(
            who=Who(kind="player", players=("Luka Doncic",)),
            lines=(Line(measure="points", op="<", value=20, as_typed="under 20 points", narrows=True),),
            measure=Measure(key="freeThrowsAttempted", as_typed="freeThrowsAttempted"),
        ),
    )
    assert lined.data["leaders"] == [{"player": "Luka Doncic", "games": 3}]


def test_a_line_whose_words_name_no_stat_refuses_rather_than_filtering_on_a_guess(con: AnswerContext) -> None:
    # A league count reads its line on the relation, which declines it there.
    with pytest.raises(Unsupported, match="names no box-score stat"):
        games_counted(
            con,
            Reading(
                lines=(Line(measure="points", value=30, as_typed="30 points", keyed=True), Line(measure=None, op="<", value=30, as_typed="under 30 gizmos", narrows=True)),
                measure=Measure(key="points", as_typed="points"),
            ),
        )
    # A named player's point reads it first, and refuses by its cause.
    spoken = player_line(
        con,
        Reading(
            who=Who(kind="player", players=("Luka Doncic",)), lines=(Line(measure=None, op="<", value=30, as_typed="under 30 gizmos", narrows=True),), measure=Measure(key="points", as_typed="points")
        ),
    ).text
    assert spoken == "'under 30 gizmos' names no box-score stat a game can be kept under."


def test_stats_against_one_opponent_end_with_the_meetings_behind_the_average(pg_ctx: AnswerContext) -> None:
    """ "stats vs X" is the averages, the count, and a short footer of the
    meetings themselves, newest first - what makes "in 1 game" honest.
    Podziemski met Detroit twice this season: at Detroit in December (20, a
    win) and at home in January (15, a loss)."""
    result = player_line(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), cuts=Cuts(opponent="Detroit Pistons"), measure=Measure(key="points", as_typed="points")))
    assert result.data["stats"]["gamesPlayed"] == 2 and result.data["stats"]["avgPoints"] == 17.5
    assert [(g["date"][5:], g["home_away"], g["result"], g["points"]) for g in result.data["recent"]] == [("01-10", "home", "L", 15), ("12-01", "away", "W", 20)]
    answer = result.text or ""
    assert "averaged 17.5 points per game in 2 games vs the Detroit Pistons" in answer
    assert answer.endswith("All 2 meetings:\n  " + f"{current_season()}-01-10  vs DET  L  15 PTS, 4 REB, 6 AST\n  {current_season() - 1}-12-01  @ DET  W  20 PTS, 7 REB, 5 AST")
    # One meeting is "the only meeting", which is what makes "in 1 game" honest.
    one = player_line(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), cuts=Cuts(opponent="Boston Celtics"), measure=Measure(key="points", as_typed="points")))
    assert "in 1 game vs the Boston Celtics" in (one.text or "") and "The only meeting:" in (one.text or "")
    # No opponent, no footer: a venue alone is not a "vs X" question.
    home = player_line(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), cuts=Cuts(venue="home"), measure=Measure(key="points", as_typed="points")))
    assert "recent" not in home.data and "meetings" not in (home.text or "")


def test_a_season_named_by_its_place_in_a_career_settles_to_that_year_once_the_player_is_known(pg_ctx: AnswerContext) -> None:
    """ "how many 40+ points games does lebron james have in his 18th season?"
    names a season by its place in his career, never the year 2018.
    Podziemski's two seasons on record are last season and this one, so his
    1st is last season, his 2nd is this one, and he has no 3rd."""
    s = current_season()
    first = player_line(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), cuts=Cuts(season_n=1), measure=Measure(key="points", as_typed="points")))
    assert first.data["season"] == s - 1 and first.data["stats"]["avgPoints"] == 8.0
    assert "in his 1st season (" in (first.text or "")
    second = games_counted(
        pg_ctx,
        Reading(
            who=Who(kind="player", players=("Brandin Podziemski",)),
            cuts=Cuts(season_n=2),
            lines=(Line(measure="points", value=20, as_typed="20 points", keyed=True),),
            measure=Measure(key="points", as_typed="points"),
        ),
    )
    assert second.data["season"] == s and second.data["leaders"] == [{"player": "Brandin Podziemski", "games": 1}]
    assert "had 1 game with 20+ points in his 2nd season (" in (second.text or "")
    log = player_log(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), cuts=Cuts(season_n=1)))
    assert [g["season"] for g in log.data["games"]] == [s - 1]
    none = player_log(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), cuts=Cuts(season_n=3)))
    assert "has 2 seasons on record" in (none.text or "") and "no 3rd season" in (none.text or "")
    assert (
        games_counted(pg_ctx, Reading(cuts=Cuts(season_n=15), lines=(Line(measure="points", value=40, as_typed="40 points", keyed=True),), measure=Measure(key="points", as_typed="points"))).text
        == "The 15th season is a place in one player's career, and no player was named."
    )


def test_one_game_of_each_playoff_series_is_numbered_by_date_over_the_series_own_games(pg_ctx: AnswerContext) -> None:
    """ "Ayton stats in game 4 playoff games": the nth game by date between two
    teams in one postseason. Podziemski's series vs Detroit was inserted with
    game 2 first, so a count by insertion order would name the wrong game."""
    third = player_log(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), cuts=Cuts(game_n=3), span=Span(season_type=3)))
    assert [(g["date"][5:], g["points"]) for g in third.data["games"]] == [("04-24", 30)]
    assert "in game 3 of each series" in (third.text or "")
    second = player_line(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), cuts=Cuts(game_n=2), measure=Measure(key="points", as_typed="points"), span=Span(season_type=3)))
    assert second.data["stats"]["gamesPlayed"] == 2 and second.data["stats"]["avgPoints"] == 17.5 and second.data["series_game"] == 2
    one_series = player_log(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), cuts=Cuts(opponent="Detroit Pistons", game_n=2), span=Span(season_type=3)))
    assert [g["points"] for g in one_series.data["games"]] == [20] and "in game 2 of the series" in (one_series.text or "")
    with pytest.raises(Unsupported, match="playoff series"):
        player_log(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), cuts=Cuts(game_n=3)))
    with pytest.raises(Unsupported, match="team's log"):
        player_log(pg_ctx, Reading(who=Who(kind="team", teams=("Golden State Warriors",)), cuts=Cuts(game_n=3), span=Span(season_type=3)))


def test_a_game_log_keeps_only_the_games_under_a_line_on_the_stat_the_words_name(pg_ctx: AnswerContext) -> None:
    """ "mikal bridges game log with less than 15 fga": a log keeps only the
    games under the line, beside the model's stat. Podziemski's three played
    games this season have 3, 4 and 2 free throw attempts."""
    result = player_log(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), lines=(Line(measure="freeThrowsAttempted", op="<", value=4, as_typed="under 4 fta", narrows=True),)))
    assert [g["date"][5:] for g in result.data["games"]] == ["01-10", "11-01"]
    assert "with under 4 free throw attempts" in (result.text or "")
    two = player_log(
        pg_ctx,
        Reading(
            who=Who(kind="player", players=("Brandin Podziemski",)),
            lines=(Line(measure="freeThrowsAttempted", op="<", value=4, as_typed="under 4 fta", narrows=True), Line(measure="points", op="<", value=12, as_typed="less than 12 points", narrows=True)),
        ),
    )
    assert [g["date"][5:] for g in two.data["games"]] == ["11-01"]
    assert "under 4 free throw attempts and under 12 points" in (two.text or "")
    # A team's log keeps its games past a line on its own figures (ISSUES.md
    # #430), and refuses one on a figure no game of a team carries by name.
    team = player_log(pg_ctx, Reading(who=Who(kind="team", teams=("Golden State Warriors",)), lines=(Line(measure="minutes", value=20, as_typed="with 20 minutes", narrows=True),)))
    assert (team.text or "").startswith("A team's games carry no minutes game by game"), team.text


def test_player_line_averages_over_exactly_the_games_under_a_line(con: AnswerContext) -> None:
    """A line on a box-score column narrows the games the way an opponent
    does, so the average is read from box scores and says what it kept."""
    result = player_line(
        con,
        Reading(
            who=Who(kind="player", players=("Luka Doncic",)),
            lines=(Line(measure="points", op="<", value=20, as_typed="under 20 points", narrows=True),),
            measure=Measure(key="points", as_typed="points"),
        ),
    )
    assert result.data["stats"]["gamesPlayed"] == 3 and result.data["stats"]["avgPoints"] == 12
    assert result.data["measures"] == ["under 20 points"] and "with under 20 points" in (result.text or "")
    floor = player_line(
        con,
        Reading(
            who=Who(kind="player", players=("Luka Doncic",)), lines=(Line(measure="minutes", value=30, as_typed="with 30 minutes", narrows=True),), measure=Measure(key="points", as_typed="points")
        ),
    )
    assert floor.data["stats"]["gamesPlayed"] == 7  # every fixture game is 30 minutes


def test_defaults_to_the_current_season(con: AnswerContext) -> None:
    result = games_counted(con, Reading(lines=(Line(measure="points", value=30, as_typed="30 points", keyed=True),), measure=Measure(key="points", as_typed="points")))
    assert result.data["season"] == current_season()


def test_explicit_season_is_honored(con: AnswerContext) -> None:
    result = games_counted(
        con, Reading(lines=(Line(measure="points", value=40, as_typed="40 points", keyed=True),), measure=Measure(key="points", as_typed="points"), span=Span(season=(current_season() - 1)))
    )
    assert result.data["leaders"] == [{"player": "Shai Gilgeous-Alexander", "games": 7}]


def test_restricted_to_regular_season(con: AnswerContext) -> None:
    # Bench Guy has 9 regular-season and 9 postseason 40-point games; only the
    # regular-season ones count.
    result = games_counted(con, Reading(lines=(Line(measure="points", value=40, as_typed="40 points", keyed=True),), measure=Measure(key="points", as_typed="points")))
    assert result.data["leaders"] == [{"player": "Bench Guy", "games": 9}]


def test_filters_to_a_named_player_on_every_token(con: AnswerContext) -> None:
    result = games_counted(
        con,
        Reading(
            who=Who(kind="player", players=("Luka Doncic",)), lines=(Line(measure="rebounds", value=20, as_typed="20 rebounds", keyed=True),), measure=Measure(key="rebounds", as_typed="rebounds")
        ),
    )
    assert result.data["leaders"] == [{"player": "Luka Doncic", "games": 3}]


def test_an_unknown_stat_is_refused_instead_of_reaching_sql(con: AnswerContext) -> None:
    """Refused by its cause, never read into SQL."""
    refused = games_counted(con, Reading(lines=(Line(measure=None, value=30, as_typed="30 points); DROP TABLE players; --", keyed=True),), measure=Measure(as_typed="points); DROP TABLE players; --")))
    assert refused.text == "A count of games across the league needs the line it counts - a stat and a number, as in '40-point games' - and none could be read from the question."


def test_a_count_with_no_line_is_refused(con: AnswerContext) -> None:
    assert (
        games_counted(con, Reading(measure=Measure(key="points", as_typed="points"))).text
        == "A count of games across the league needs the line it counts - a stat and a number, as in '40-point games' - and none could be read from the question."
    )


def test_limit_is_clamped(con: AnswerContext) -> None:
    result = games_counted(con, Reading(lines=(Line(measure="points", as_typed="1 points", keyed=True),), measure=Measure(key="points", as_typed="points"), window=Window(count=10000)))
    assert len(result.data["leaders"]) <= 50


def test_empty_result_is_reported_as_empty_not_invented(con: AnswerContext) -> None:
    result = games_counted(con, Reading(lines=(Line(measure="points", value=999, as_typed="999 points", keyed=True),), measure=Measure(key="points", as_typed="points")))
    assert result.data["leaders"] == []


def test_answer_is_deterministic_prose_so_no_model_call_is_needed(con: AnswerContext) -> None:
    result = games_counted(con, Reading(lines=(Line(measure="points", value=30, as_typed="30 points", keyed=True),), measure=Measure(key="points", as_typed="points")))
    assert result.text == (f"Bench Guy had the most games with 30+ points in the {current_season()} regular season, with 9. Next: Luka Doncic (4), Shai Gilgeous-Alexander (2).")


def test_answer_always_names_the_season_explicitly(con: AnswerContext) -> None:
    # Naming the season makes an answer for the wrong one visible.
    assert f"{current_season()} regular season" in (
        games_counted(con, Reading(lines=(Line(measure="points", value=30, as_typed="30 points", keyed=True),), measure=Measure(key="points", as_typed="points"))).text or ""
    )


def test_answer_reports_an_empty_result_honestly(con: AnswerContext) -> None:
    result = games_counted(con, Reading(lines=(Line(measure="points", value=999, as_typed="999 points", keyed=True),), measure=Measure(key="points", as_typed="points")))
    assert result.text == f"No player had a game with 999+ points in the {current_season()} regular season."


def test_answer_for_a_single_named_player(con: AnswerContext) -> None:
    result = games_counted(
        con,
        Reading(
            who=Who(kind="player", players=("Luka Doncic",)), lines=(Line(measure="rebounds", value=20, as_typed="20 rebounds", keyed=True),), measure=Measure(key="rebounds", as_typed="rebounds")
        ),
    )
    assert result.text == f"Luka Doncic had 3 games with 20+ rebounds in the {current_season()} regular season."


@pytest.fixture
def currys(tmp_path: Path) -> AnswerContext:
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    c.execute("INSERT INTO players VALUES ('7','Stephen Curry'),('8','Seth Curry')")
    c.execute(
        "CREATE TABLE player_box_stats (athlete_id VARCHAR, season INTEGER, season_type INTEGER, points INTEGER, event_id VARCHAR, minutes INTEGER DEFAULT 30, did_not_play BOOLEAN DEFAULT FALSE)"
    )
    season = current_season()
    rows: list[tuple[Any, ...]] = []
    rows += [("7", season, 2, 35)] * 3 + [("8", season, 2, 31)] * 5  # both have 30-point games
    rows += [("7", season - 1, 2, 32)] * 2  # only Stephen played
    rows += [("7", season - 2, 2, 12)] * 4 + [("8", season - 2, 2, 30)]  # both played, only Seth reached 30
    c.executemany("INSERT INTO player_box_stats (athlete_id, season, season_type, points) VALUES (?,?,?,?)", rows)
    c.execute(
        "CREATE VIEW player_game_log AS SELECT pbs.* REPLACE (COALESCE(pbs.event_id, 'e' || pbs.rowid) AS event_id), p.display_name AS player_name, "
        "NULL::VARCHAR AS game_date, NULL::VARCHAR AS opponent_abbr FROM player_box_stats pbs LEFT JOIN players p ON p.athlete_id = pbs.athlete_id"
    )
    # The date, home side and winner the compiler reads off `games` beside the
    # log (compose.core._row_select), as the warehouse view carries them.
    c.execute("CREATE VIEW games AS SELECT DISTINCT event_id, season, season_type, game_date AS date, NULL::VARCHAR AS home_team_id, NULL::VARCHAR AS winner_team_id FROM player_game_log")
    return AnswerContext(con=c, out_dir=tmp_path)


@pytest.mark.parametrize("back", [0, 2, 3])
def test_a_name_two_players_could_answer_to_is_asked_about(currys: AnswerContext, back: int) -> None:
    """Asked, not counted for whichever Curry leads. Two seasons back only Seth
    has a qualifying game, but both played: narrowing on the answer rather than
    on who played would be the prominence tiebreak by another route. Three back
    neither played, and the question is asked about both."""
    result = games_counted(
        currys,
        Reading(
            who=Who(kind="player", players=("Curry",)),
            lines=(Line(measure="points", value=30, as_typed="30 points", keyed=True),),
            measure=Measure(key="points", as_typed="points"),
            span=Span(season=(current_season() - back)),
        ),
    )
    assert result.data == {"ambiguous": "Curry", "candidates": ["Seth Curry", "Stephen Curry"]}


def test_a_name_only_one_player_with_games_that_season_answers_to_is_answered(currys: AnswerContext) -> None:
    season = current_season() - 1
    result = games_counted(
        currys,
        Reading(
            who=Who(kind="player", players=("Curry",)),
            lines=(Line(measure="points", value=30, as_typed="30 points", keyed=True),),
            measure=Measure(key="points", as_typed="points"),
            span=Span(season=season),
        ),
    )
    assert result.text == f"Stephen Curry had 2 games with 30+ points in the {season} regular season."


def test_a_name_at_the_candidate_cap_is_asked_about_rather_than_narrowed(tmp_path: Path) -> None:
    """find_players stops at MAX_CANDIDATES, alphabetically, so narrowing that
    list can leave one survivor while a player past the cut played too - the
    first Jones here, where the last one also has games."""
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    c.executemany("INSERT INTO players VALUES (?,?)", [(str(i), f"{chr(ord('A') + i)}ay Jones") for i in range(MAX_CANDIDATES + 1)])
    c.execute(
        "CREATE TABLE player_box_stats (athlete_id VARCHAR, season INTEGER, season_type INTEGER, points INTEGER, event_id VARCHAR, minutes INTEGER DEFAULT 30, did_not_play BOOLEAN DEFAULT FALSE)"
    )
    played = [("0", current_season(), 2, 30), (str(MAX_CANDIDATES), current_season(), 2, 30)]  # the first Jones, and the one the cap cuts off
    c.executemany("INSERT INTO player_box_stats (athlete_id, season, season_type, points) VALUES (?,?,?,?)", played)
    result = games_counted(
        AnswerContext(con=c, out_dir=tmp_path),
        Reading(who=Who(kind="player", players=("Jones",)), lines=(Line(measure="points", value=30, as_typed="30 points", keyed=True),), measure=Measure(key="points", as_typed="points")),
    )
    assert result.data.get("ambiguous") == "Jones", result.text


def test_answer_reports_a_tie_as_a_tie(con: AnswerContext) -> None:
    con.con.execute("INSERT INTO player_box_stats (athlete_id, season, season_type, points, rebounds) SELECT '1', season, 2, 35, 5 FROM player_box_stats LIMIT 5")
    result = games_counted(con, Reading(lines=(Line(measure="points", value=30, as_typed="30 points", keyed=True),), measure=Measure(key="points", as_typed="points")))
    assert "tied for the most" in (result.text or "")


@pytest.fixture
def rebuilt_counts(tmp_path: Path) -> AnswerContext:
    """The warehouse's real shape for an empty-box season: EVERY game keeps its
    `player_box_stats` row - ESPN serves the line, just zeroed - while the log
    carries the rebuilt figures with `reconstructed` marking exactly those.
    A fixture that left the stored rows out would resolve no player at all,
    since a count of games narrows a name by who has a box score."""
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    c.execute("INSERT INTO players VALUES ('1','Anthony Davis')")
    c.execute("CREATE TABLE player_box_stats (event_id VARCHAR, athlete_id VARCHAR, season INTEGER, season_type INTEGER, points INTEGER, fouls INTEGER, minutes INTEGER, did_not_play BOOLEAN)")
    c.execute(
        "CREATE TABLE player_game_log (event_id VARCHAR, athlete_id VARCHAR, season INTEGER, season_type INTEGER, "
        "player_name VARCHAR, game_date VARCHAR, opponent_abbr VARCHAR, points INTEGER, fouls INTEGER, minutes INTEGER, "
        "reconstructed BOOLEAN, did_not_play BOOLEAN)"
    )
    s = current_season()
    # Two games ESPN served and two it served empty. The empty pair is zeros in
    # the stored table and real figures in the log: 25 and 31 both clear 20, so
    # reading the stored table alone counts 1 where the truth is 3.
    c.executemany(
        "INSERT INTO player_box_stats VALUES (?,'1',?,2,?,?,?,FALSE)",
        [("e1", s, 24, 3, 31), ("e2", s, 18, 5, 28), ("e3", s, 0, 0, None), ("e4", s, 0, 0, None)],
    )
    c.executemany(
        "INSERT INTO player_game_log VALUES (?,'1',?,2,'Anthony Davis',?,?,?,?,?,?,FALSE)",
        [
            ("e1", s, "2026-01-02T00:30Z", "ORL", 24, 3, 31, False),
            ("e2", s, "2026-01-05T00:30Z", "DAL", 18, 5, 28, False),
            ("e3", s, "2026-01-08T00:30Z", "MIA", 25, 1, None, True),
            ("e4", s, "2026-01-11T00:30Z", "PHX", 31, 6, None, True),
        ],
    )
    # An athlete with no `players` row: the log LEFT JOINs that table, so his
    # name comes back NULL. The stored branch INNER JOINs and drops him; the
    # log branch has to drop him too, or he is counted and reported as a
    # nameless leader ahead of everyone.
    c.executemany(
        "INSERT INTO player_game_log VALUES (?,'99',?,2,NULL,?,?,?,?,?,?,FALSE)",
        [("e5", s, "2026-01-14T00:30Z", "SAC", 60, 2, 33, False), ("e6", s, "2026-01-16T00:30Z", "UTA", 55, 2, 30, False)],
    )
    # The date, home side and winner the compiler reads off `games` beside the
    # log (compose.core._row_select), as the warehouse view carries them.
    c.execute("CREATE VIEW games AS SELECT DISTINCT event_id, season, season_type, game_date AS date, NULL::VARCHAR AS home_team_id, NULL::VARCHAR AS winner_team_id FROM player_game_log")
    # The warehouse builds the log over the filled box view; the relation
    # learns rebuilt lines exist from it (conditions.box_source).
    c.execute("CREATE VIEW player_box_stats_filled AS SELECT * FROM player_game_log")
    return AnswerContext(con=c, out_dir=tmp_path)


def test_a_nameless_athlete_is_never_counted_as_a_leader(rebuilt_counts: AnswerContext) -> None:
    """The log LEFT JOINs `players`, so an athlete missing from it reads back
    with a NULL name. The stored table's INNER JOIN drops him; the log branch
    must too, or the league board's top line is a blank name with 2 games."""
    result = games_counted(rebuilt_counts, Reading(lines=(Line(measure="points", value=20, as_typed="20 points", keyed=True),), measure=Measure(key="points", as_typed="points")))
    assert [leader["player"] for leader in result.data["leaders"]] == ["Anthony Davis"]
    assert None not in [leader["player"] for leader in result.data["leaders"]]


def test_a_count_of_games_over_a_line_counts_rebuilt_games(rebuilt_counts: AnswerContext) -> None:
    """Reading `player_box_stats`, the two empty games are zeros and the count
    is 1; reading the log, the rebuilt 25 and 31 both clear 20 and the count
    is 3. The count reads the log."""
    result = games_counted(
        rebuilt_counts,
        Reading(who=Who(kind="player", players=("Anthony Davis",)), lines=(Line(measure="points", value=20, as_typed="20 points", keyed=True),), measure=Measure(key="points", as_typed="points")),
    )
    assert result.data["leaders"] == [{"player": "Anthony Davis", "games": 3}]
    assert result.data["rebuilt_games"] == 2


def test_a_rebuilt_count_says_how_many_it_rebuilt(rebuilt_counts: AnswerContext) -> None:
    """A count resting on figures ESPN never served has to say so, or it reads
    as a count of box scores. The disclosure the opt-in is conditional on."""
    answer = (
        games_counted(
            rebuilt_counts,
            Reading(who=Who(kind="player", players=("Anthony Davis",)), lines=(Line(measure="points", value=20, as_typed="20 points", keyed=True),), measure=Measure(key="points", as_typed="points")),
        ).text
        or ""
    )
    assert "2 of those 3 games have no box score from ESPN" in answer
    assert "rebuilt from play-by-play" in answer


def test_a_count_of_only_fetched_games_says_nothing_about_rebuilding(rebuilt_counts: AnswerContext) -> None:
    """The note is about the number given, not about what was read. Drop the
    rebuilt games under the threshold and the count is an ordinary one, with
    nothing to disclose - even though the rebuilt lines were still read."""
    rebuilt_counts.con.execute("UPDATE player_game_log SET points = 5 WHERE reconstructed")
    result = games_counted(
        rebuilt_counts,
        Reading(who=Who(kind="player", players=("Anthony Davis",)), lines=(Line(measure="points", value=20, as_typed="20 points", keyed=True),), measure=Measure(key="points", as_typed="points")),
    )
    assert result.data["leaders"] == [{"player": "Anthony Davis", "games": 1}]
    assert result.data["rebuilt_games"] == 0
    assert "rebuilt" not in (result.text or "")


def test_fouls_are_never_counted_from_a_rebuilt_line(rebuilt_counts: AnswerContext) -> None:
    """A rebuilt foul is wrong in one game in six, so fouls sit outside
    REBUILT_STATS. The rebuilt 6 must not be counted - and the count of none
    must name the DECISION rather than implying the games are missing."""
    answer = (
        games_counted(
            rebuilt_counts,
            Reading(who=Who(kind="player", players=("Anthony Davis",)), lines=(Line(measure="fouls", value=6, as_typed="6 fouls", keyed=True),), measure=Measure(key="fouls", as_typed="fouls")),
        ).text
        or ""
    )
    assert "had no games with 6+ fouls" in answer
    assert "were rebuilt from play-by-play" in answer
    assert "not counted from a rebuilt line" in answer


def test_a_count_over_made_threes_keeps_the_rebuilt_games(rebuilt_counts: AnswerContext) -> None:
    """ISSUES.md #501, the yardstick's F160: "jamal murray games with 2 threes
    including playoffs" counted 426 where the box scores hold 434, the 8
    rebuilt games left out and an "empty box score" caveat said in their
    place. A made three is a scoring play the plays hold (98.6% exact per
    game, beside points' 98.3%), so a rebuilt line is read for it as for
    points: the two rebuilt games with 2+ threes are counted and said as
    rebuilt."""
    for table in ("player_box_stats", "player_game_log"):
        rebuilt_counts.con.execute(f"ALTER TABLE {table} ADD COLUMN threePointFieldGoalsMade INTEGER")
    rebuilt_counts.con.execute("UPDATE player_box_stats SET threePointFieldGoalsMade = CASE event_id WHEN 'e1' THEN 3 WHEN 'e2' THEN 1 ELSE 0 END")
    rebuilt_counts.con.execute("UPDATE player_game_log SET threePointFieldGoalsMade = CASE event_id WHEN 'e1' THEN 3 WHEN 'e2' THEN 1 ELSE 2 END")
    answer = (
        games_counted(
            rebuilt_counts,
            Reading(
                who=Who(kind="player", players=("Anthony Davis",)),
                lines=(Line(measure="threePointFieldGoalsMade", value=2, as_typed="2 threes", keyed=True),),
                measure=Measure(key="threePointFieldGoalsMade", as_typed="threePointFieldGoalsMade"),
            ),
        ).text
        or ""
    )
    assert "had 3 games with 2+ 3-pointers" in answer
    assert "2 of those 3 games have no box score from ESPN" in answer
    assert "empty box score" not in answer


@pytest.mark.parametrize(
    ("measure", "why"),
    [
        ("turnovers", "rebuilt turnovers are wrong in about one game in thirteen"),
        ("fouls", "rebuilt fouls are wrong in about one game in six"),
    ],
)
def test_a_withheld_count_names_its_own_stat_s_reason(rebuilt_counts: AnswerContext, measure: str, why: str) -> None:
    """ISSUES.md #501: the withheld stat's caveat gives that stat's reason,
    never the fouls' error rate beside a stat it does not describe."""
    rebuilt_counts.con.execute(f"ALTER TABLE player_game_log ADD COLUMN IF NOT EXISTS {measure} INTEGER")
    rebuilt_counts.con.execute(f"UPDATE player_game_log SET {measure} = CASE WHEN reconstructed THEN 9 ELSE 0 END")
    answer = (
        games_counted(
            rebuilt_counts,
            Reading(who=Who(kind="player", players=("Anthony Davis",)), lines=(Line(measure=measure, value=6, as_typed=f"6 {measure}", keyed=True),), measure=Measure(key=measure, as_typed=measure)),
        ).text
        or ""
    )
    assert "not counted from a rebuilt line" in answer
    assert why in answer
    assert answer.count("wrong in about one game") == 1


def test_the_low_count_caveat_drops_the_games_the_rebuild_counted(rebuilt_counts: AnswerContext) -> None:
    """The self-contradiction this avoids: counting a game from its rebuilt
    line and then reporting that same game as one the count could not see.
    Counted from player_box_stats, e3 and e4 are "empty"; counted from the log,
    which knows they were rebuilt, there is nothing left to disclaim."""
    answer = (
        games_counted(
            rebuilt_counts,
            Reading(who=Who(kind="player", players=("Anthony Davis",)), lines=(Line(measure="points", value=20, as_typed="20 points", keyed=True),), measure=Measure(key="points", as_typed="points")),
        ).text
        or ""
    )
    assert "empty box score" not in answer
    assert "the count may be low" not in answer


def test_a_warehouse_without_the_flag_counts_only_fetched_games(rebuilt_counts: AnswerContext) -> None:
    """The column arrives with a `data load`; an older warehouse has none, and
    the query must not be written as though it were always there - the Binder
    error AGENTS.md records for view changes. The count falls back to 1."""
    rebuilt_counts.con.execute("ALTER TABLE player_game_log DROP COLUMN reconstructed")
    result = games_counted(
        rebuilt_counts,
        Reading(who=Who(kind="player", players=("Anthony Davis",)), lines=(Line(measure="points", value=20, as_typed="20 points", keyed=True),), measure=Measure(key="points", as_typed="points")),
    )
    assert result.data["leaders"] == [{"player": "Anthony Davis", "games": 1}]
    assert "rebuilt" not in (result.text or "")


def test_a_count_made_entirely_of_rebuilt_games_says_so_outright(rebuilt_counts: AnswerContext) -> None:
    """Anthony Davis's real 2015: every single qualifying game is rebuilt, and
    "52 of those 52 games have no box score from ESPN" is true and reads as a
    bug, which costs the sentence the trust it exists to calibrate. A
    threshold of 25 leaves only the two rebuilt games here."""
    answer = (
        games_counted(
            rebuilt_counts,
            Reading(who=Who(kind="player", players=("Anthony Davis",)), lines=(Line(measure="points", value=25, as_typed="25 points", keyed=True),), measure=Measure(key="points", as_typed="points")),
        ).text
        or ""
    )
    assert "had 2 games with 25+ points" in answer
    assert "None of those 2 games has a box score from ESPN" in answer
    # The shape being ruled out, in both its forms.
    assert "2 of those 2 games" not in answer
    assert "games have a box score" not in answer


# ---------------- a ranking of players ----------------


@pytest.fixture
def lb_con(tmp_path: Path) -> AnswerContext:
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    c.execute("CREATE TABLE teams (team_id VARCHAR, abbreviation VARCHAR, display_name VARCHAR)")
    c.execute("CREATE TABLE player_season_stats (athlete_id VARCHAR, team_id VARCHAR, season INTEGER, season_type INTEGER, gamesPlayed INTEGER, avgPoints DOUBLE, points INTEGER)")
    c.execute("INSERT INTO players VALUES ('1','Luka Doncic'),('2','Stephen Curry')")
    c.execute("INSERT INTO teams VALUES ('9','GS','Golden State Warriors'),('6','DAL','Dallas Mavericks')")
    s = current_season()
    # `points` is the season TOTAL beside the per-game average, which is what
    # `rate: "total"` ranks by - 33.5 x 70 and 27.1 x 68, rounded as ESPN
    # stores them.
    c.execute("INSERT INTO player_season_stats VALUES ('1','6',?,2,70,33.5,2345),('2','9',?,2,68,27.1,1843),('2','9',?,3,10,31.0,310)", [s, s, s])
    return AnswerContext(con=c, out_dir=tmp_path)


def test_player_ranking_maps_a_plain_stat_onto_a_real_metric(lb_con: AnswerContext) -> None:
    result = player_ranking(lb_con, Reading(measure=Measure(key="points", as_typed="points")))
    assert result.data["leaders"][0]["display_name"] == "Luka Doncic"


def test_player_ranking_phrases_its_own_answer(lb_con: AnswerContext) -> None:
    result = player_ranking(lb_con, Reading(measure=Measure(key="points", as_typed="points"), window=Window(count=2)))
    assert result.text == (f"Luka Doncic led the league in points per game in the {current_season()} regular season (minimum 20 games), at 33.5. Next: Stephen Curry (27.1).")


def test_player_ranking_shows_each_players_team_when_asked(lb_con: AnswerContext) -> None:
    """F017 (ISSUES.md): "who are the top 50 in total adjusted netpoints with
    the team they play for" asks for each player's team beside the name.
    `fields: ["team"]` adds a "team" column, read from player_game_log so a
    mid-season trade shows the team he played his most recent game for, with
    a note saying so."""
    lb_con.con.execute("CREATE TABLE player_game_log (athlete_id VARCHAR, team_id VARCHAR, season INTEGER, season_type INTEGER, game_date VARCHAR)")
    s = current_season()
    # Luka stays with Dallas all season. Curry is traded from Golden State to
    # Dallas mid-season - his most recent game is with Dallas.
    lb_con.con.executemany(
        "INSERT INTO player_game_log VALUES (?, ?, ?, 2, ?)",
        [("1", "6", s, f"{s - 1}-11-01"), ("2", "9", s, f"{s - 1}-11-01"), ("2", "6", s, f"{s}-02-01")],
    )
    result = player_ranking(lb_con, Reading(measure=Measure(key="points", as_typed="points", beside=("team",))))
    rows = {r["display_name"]: r["team"] for r in result.data["leaders"]}
    assert rows == {"Luka Doncic": "Dallas Mavericks", "Stephen Curry": "Dallas Mavericks"}
    assert "team" in result.text.splitlines()[1]  # the header row
    assert "Team is each player's most recent team that season." in result.text
    assert result.data["notes"] == ["Team is each player's most recent team that season."]
    assert result.data["headline"] == result.text.splitlines()[0].rstrip(":")


def test_player_ranking_team_field_says_nothing_when_nobody_was_traded(lb_con: AnswerContext) -> None:
    """The trade note is said once, and only when it is true: with nobody
    shown having played for more than one team, it does not appear."""
    lb_con.con.execute("CREATE TABLE player_game_log (athlete_id VARCHAR, team_id VARCHAR, season INTEGER, season_type INTEGER, game_date VARCHAR)")
    s = current_season()
    lb_con.con.executemany(
        "INSERT INTO player_game_log VALUES (?, ?, ?, 2, ?)",
        [("1", "6", s, f"{s - 1}-11-01"), ("2", "9", s, f"{s - 1}-11-01")],
    )
    result = player_ranking(lb_con, Reading(measure=Measure(key="points", as_typed="points", beside=("team",))))
    assert "Team is each player's most recent team" not in result.text
    assert {r["team"] for r in result.data["leaders"]} == {"Dallas Mavericks", "Golden State Warriors"}


def test_player_ranking_names_the_team_when_filtered(lb_con: AnswerContext) -> None:
    result = player_ranking(lb_con, Reading(who=Who(kind="team", teams=("Warriors",)), measure=Measure(key="points", as_typed="points")))
    assert "led the Golden State Warriors" in (result.text or "")


def test_player_ranking_honors_playoffs(lb_con: AnswerContext) -> None:
    result = player_ranking(lb_con, Reading(measure=Measure(key="points", as_typed="points"), span=Span(season_type=3)))
    assert "postseason" in (result.text or "") and result.data["leaders"][0]["value"] == 31.0


def test_player_ranking_of_an_unmapped_stat_is_refused_rather_than_fuzzy_matched(lb_con: AnswerContext) -> None:
    # Deliberately NOT get_close_matches: silently ranking by whichever metric
    # scored highest is the substitution failure this design exists to prevent.
    # The compiler refuses by name (say._no_ranking_for).
    result = player_ranking(lb_con, Reading(measure=Measure(as_typed="clutchness")))
    assert "No ranking reads 'clutchness'" in result.text


def test_player_ranking_refuses_a_unit_the_metric_has_no_form_of(lb_con: AnswerContext) -> None:
    """ "who were the top 10 in defensive netpoints / 90": a unit the metric
    has no form of is refused, naming the metric's real forms rather than a
    generic list, which would be the wrong-cause refusal."""
    # Through the shape's row (``cells_unhonored``): calling the reader
    # directly would pass whether or not `rate` is declared stated.
    scope = Reading(measure=measure_of("points", how="per_90", unit="/ 90"))
    assert [cell for cell, _ in cells_unhonored(scope, PLAYER_RANKING)] == []
    result = player_ranking(lb_con, scope)
    assert "per 90 minutes" in (result.text or "")
    assert "per game" in (result.text or "") and "season total" in (result.text or "")
    # Points has no per-100 form, so the refusal must not offer one.
    assert "per 100" not in (result.text or "")
    assert "leaders" not in result.data


def test_player_ranking_reads_a_season_total_when_the_rate_is_total(lb_con: AnswerContext) -> None:
    """A ranking stating `rate` reaches the season-total branch, so "most
    points this season" as a TOTAL is answered."""
    scope = Reading(measure=measure_of("points", how="total", unit="total"))
    assert [cell for cell, _ in cells_unhonored(scope, PLAYER_RANKING)] == []
    result = player_ranking(lb_con, scope)
    assert "total points" in (result.text or "")
    assert result.data["leaders"][0]["display_name"] == "Luka Doncic"


def test_player_ranking_within_an_ambiguous_team_is_declined_rather_than_picking_one(lb_con: AnswerContext) -> None:
    lb_con.con.execute("INSERT INTO teams VALUES ('12','LAC','LA Clippers'),('13','LAL','Los Angeles Lakers')")
    with pytest.raises(Unsupported, match="LA"):
        player_ranking(lb_con, Reading(who=Who(kind="team", teams=("LA",)), measure=Measure(key="points", as_typed="points")))


def test_player_ranking_within_an_unknown_team_is_declined(lb_con: AnswerContext) -> None:
    with pytest.raises(Unsupported):
        player_ranking(lb_con, Reading(who=Who(kind="team", teams=("Not A Team",)), measure=Measure(key="points", as_typed="points")))


def test_games_counted_honors_playoffs(con: AnswerContext) -> None:
    result = games_counted(con, Reading(lines=(Line(measure="points", value=40, as_typed="40 points", keyed=True),), measure=Measure(key="points", as_typed="points"), span=Span(season_type=3)))
    assert "postseason" in (result.text or "")
    assert result.data["leaders"] == [{"player": "Bench Guy", "games": 9}]
    assert result.data["headline"] == result.text
    assert result.data["notes"] == []


@pytest.fixture
def shooting_ctx(tmp_path: Path) -> AnswerContext:
    """Real 2025 rows. On a 20-game qualifier, Kai Jones's 109 shots led the
    league in true shooting at .804, with Patrick Baldwin Jr.'s 35 third; in
    the playoffs, the same qualifier kept Thomas Bryant's 33 shots and dropped
    Jarrett Allen, who shot .792 on 61 in nine games."""
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    c.execute("INSERT INTO players VALUES ('1','Kai Jones'),('2','Patrick Baldwin Jr.'),('3','Jarrett Allen'),('4','Nikola Jokic'),('5','Isaiah Joe'),('6','Thomas Bryant')")
    c.execute(
        "CREATE TABLE player_season_advanced_stats (season INTEGER, season_type INTEGER, athlete_id VARCHAR, games_played BIGINT, "
        "field_goals_attempted BIGINT, true_shooting_attempts DOUBLE, ts_pct DOUBLE, efg_pct DOUBLE)"
    )
    c.executemany(
        "INSERT INTO player_season_advanced_stats VALUES (2025,?,?,?,?,?,?,?)",
        [
            (2, "1", 40, 109, 123.08, 0.804, 0.803),
            (2, "2", 24, 35, 36.76, 0.721, 0.729),
            (2, "3", 82, 640, 761.88, 0.724, 0.706),
            (2, "4", 70, 1364, 1562.44, 0.663, 0.627),
            (3, "3", 9, 61, 76.40, 0.792, 0.721),
            (3, "5", 21, 73, 79.16, 0.676, 0.651),
            (3, "6", 20, 33, 39.16, 0.664, 0.621),
            (3, "4", 14, 268, 312.44, 0.587, 0.539),
        ],
    )
    return AnswerContext(con=c, out_dir=tmp_path)


def test_true_shooting_says_which_qualifier_it_ranked_under(shooting_ctx: AnswerContext) -> None:
    result = player_ranking(shooting_ctx, Reading(measure=Measure(key="ts_pct", as_typed="true_shooting"), span=Span(season=2025)))
    assert result.text == "Jarrett Allen led the league in true shooting % in the 2025 regular season (minimum 550 true-shooting attempts), at 0.724. Next: Nikola Jokic (0.663)."


@pytest.mark.parametrize(("stat", "qualifier"), [("true_shooting", "550 true-shooting attempts"), ("efg_pct", "480 field-goal attempts")])
def test_a_shooting_percentage_qualifies_on_attempts_not_games(shooting_ctx: AnswerContext, stat: str, qualifier: str) -> None:
    """Both low-volume players have the games; neither has the shots."""
    result = player_ranking(shooting_ctx, Reading(measure=measure_of(stat), span=Span(season=2025)))
    assert [r["display_name"] for r in result.data["leaders"]] == ["Jarrett Allen", "Nikola Jokic"]
    assert f"2025 regular season (minimum {qualifier})," in (result.text or "")


@pytest.mark.parametrize(("stat", "qualifier"), [("true_shooting", "67 true-shooting attempts"), ("efg_pct", "59 field-goal attempts")])
def test_a_shooting_percentage_scales_its_qualifier_for_the_postseason(shooting_ctx: AnswerContext, stat: str, qualifier: str) -> None:
    """The season floor is more than one player reached in the whole 2025
    postseason, so it cannot carry over, and games cannot stand in for it."""
    result = player_ranking(shooting_ctx, Reading(measure=measure_of(stat), span=Span(season=2025, season_type=3)))
    assert [r["display_name"] for r in result.data["leaders"]] == ["Jarrett Allen", "Isaiah Joe", "Nikola Jokic"]
    assert f"2025 postseason (minimum {qualifier})," in (result.text or "")


def test_an_empty_board_says_what_nobody_met(shooting_ctx: AnswerContext) -> None:
    result = player_ranking(shooting_ctx, Reading(measure=Measure(key="ts_pct", as_typed="true_shooting"), span=Span(season=2024)))
    assert result.text == "No players qualified for true shooting % in the league in the 2024 regular season (minimum 550 true-shooting attempts)."


@pytest.fixture
def games_ctx(tmp_path: Path) -> AnswerContext:
    """Real rows in the two shapes that had no games qualifier.

    Danny Fortson played 6 games in 2000-01 and averaged 16.3 rebounds, which
    led the league; Dikembe Mutombo, who actually led it, averaged 13.5 over
    79. In the 2023 playoffs Kawhi Leonard played 2 games at 34.5 points and
    led the postseason; Devin Booker led it at 33.7 over 17.
    """
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    c.execute("INSERT INTO players VALUES ('1','Danny Fortson'),('2','Dikembe Mutombo'),('3','Ben Wallace'),('4','Kawhi Leonard'),('5','Devin Booker'),('6','Anthony Edwards')")
    c.execute(
        "CREATE TABLE player_season_stats (athlete_id VARCHAR, team_id VARCHAR, season INTEGER, season_type INTEGER, gamesPlayed INTEGER, "
        "avgRebounds DOUBLE, totalRebounds INTEGER, avgPoints DOUBLE, points INTEGER)"
    )
    c.executemany(
        "INSERT INTO player_season_stats VALUES (?,?,?,?,?,?,?,?,?)",
        [
            ("1", "5", 2001, 2, 6, 16.3, 98, 11.3, 68),
            ("2", "6", 2001, 2, 79, 13.5, 1071, 11.7, 926),
            ("3", "7", 2001, 2, 80, 13.2, 1052, 6.4, 511),
            ("4", "8", 2023, 3, 2, 5.5, 11, 34.5, 69),
            ("5", "9", 2023, 3, 17, 4.7, 80, 33.7, 573),
            ("6", "10", 2023, 3, 5, 6.0, 30, 31.6, 158),
        ],
    )
    return AnswerContext(con=c, out_dir=tmp_path)


def test_a_per_game_leaderboard_qualifies_on_games(games_ctx: AnswerContext) -> None:
    """ "who led the league in rebounding in 2001" is not Danny Fortson, on 6
    games: a per-game board qualifies on games, regular season and
    postseason alike."""
    result = player_ranking(games_ctx, Reading(measure=Measure(key="rebounds", as_typed="rebounds"), span=Span(season=2001)))
    assert [r["display_name"] for r in result.data["leaders"]] == ["Dikembe Mutombo", "Ben Wallace"]
    assert result.data["min_sample"] == PER_GAME_MIN_GAMES


def test_a_per_game_leaderboard_names_the_qualifier_it_applied(games_ctx: AnswerContext) -> None:
    """A floor nobody is told about is why "why isn't Fortson here?" has no
    answer - and the leader's own 79 games are the reason he is."""
    answer = player_ranking(games_ctx, Reading(measure=Measure(key="rebounds", as_typed="rebounds"), span=Span(season=2001))).text
    assert answer == "Dikembe Mutombo led the league in rebounds per game in the 2001 regular season (minimum 20 games), at 13.5. Next: Ben Wallace (13.2)."


def test_a_per_game_leaderboard_scales_its_qualifier_for_the_postseason(games_ctx: AnswerContext) -> None:
    """20 games is more than a title run, so the season floor cannot carry
    over; 5 is more than a first-round sweep. Kawhi Leonard's 2 games led 2023
    playoff scoring, and Anthony Edwards's 5 still qualify."""
    result = player_ranking(games_ctx, Reading(measure=Measure(key="points", as_typed="points"), span=Span(season=2023, season_type=3)))
    assert [r["display_name"] for r in result.data["leaders"]] == ["Devin Booker", "Anthony Edwards"]
    assert result.data["min_sample"] == PER_GAME_MIN_POSTSEASON_GAMES
    assert "(minimum 5 games)" in (result.text or "")


def test_every_per_game_metric_carries_both_games_qualifiers() -> None:
    """The guard is over the whole list of per-game metrics. A per-game average with
    no floor is led by whoever played fewest games. Counting metrics are
    deliberately not here: a season total or a double-double count needs
    volume to rank at all, and measured over 1994-2026 no season total, and no
    double-double count, was ever led from under these floors."""
    unqualified = [
        name
        for name, spec in LEADERBOARD_METRICS.items()
        if spec.table == "player_season_stats" and spec.column.startswith("avg") and (spec.default_min_sample != PER_GAME_MIN_GAMES or spec.postseason_min_sample != PER_GAME_MIN_POSTSEASON_GAMES)
    ]
    assert unqualified == []


# ---------------- a player's line ----------------


# The full box line a player's line shows where its words asked the line
# (``season_line.full_line_asked``): the season table's per-game columns and
# totals, and the game log's columns, a fixture built for one stat lacks. Added
# empty, so a line read off such a fixture shows the full line's cells as "-".
_SEASON_FULL_LINE_COLUMNS: tuple[tuple[str, str], ...] = (
    *((column, "DOUBLE") for column in ("avgMinutes", "avgPoints", "avgRebounds", "avgAssists", "avgSteals", "avgBlocks", "avgTurnovers", "avgFouls")),
    *((column, "INTEGER") for column in ("points", "totalRebounds", "assists", "steals", "blocks", "turnovers", "fouls")),
    *((column, "INTEGER") for column in ("fieldGoalsMade", "fieldGoalsAttempted", "threePointFieldGoalsMade", "threePointFieldGoalsAttempted", "freeThrowsMade", "freeThrowsAttempted")),
)
_GAME_FULL_LINE_COLUMNS: tuple[tuple[str, str], ...] = tuple(
    (column, "INTEGER")
    for column in (
        "minutes",
        "points",
        "rebounds",
        "assists",
        "steals",
        "blocks",
        "turnovers",
        "fouls",
        "fieldGoalsMade",
        "fieldGoalsAttempted",
        "threePointFieldGoalsMade",
        "threePointFieldGoalsAttempted",
        "freeThrowsMade",
        "freeThrowsAttempted",
    )
)


def _with_full_line_columns(con: duckdb.DuckDBPyConnection, table: str, columns: tuple[tuple[str, str], ...]) -> None:
    """``table`` with every column of the full box line it lacks, empty."""
    for column, kind in columns:
        con.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {kind}")


@pytest.fixture
def ps_con(tmp_path: Path) -> AnswerContext:
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    c.execute(
        "CREATE TABLE player_season_stats_deduped (athlete_id VARCHAR, season INTEGER, season_type INTEGER, "
        "gamesPlayed INTEGER, avgPoints DOUBLE, points INTEGER, avgRebounds DOUBLE, avgAssists DOUBLE, assists INTEGER, "
        "avgSteals DOUBLE, avgBlocks DOUBLE, avgTurnovers DOUBLE, avgFouls DOUBLE, avgMinutes DOUBLE, fouls INTEGER, "
        "fieldGoalsMade INTEGER, fieldGoalsAttempted INTEGER, threePointFieldGoalsMade INTEGER, threePointFieldGoalsAttempted INTEGER, freeThrowsMade INTEGER, freeThrowsAttempted INTEGER)"
    )
    c.execute("INSERT INTO players VALUES ('1','Luka Doncic'),('2','Luka Garza'),('3','Nikola Jokic'),('4','Stephen Curry'),('5','Seth Curry')")
    s = current_season()
    c.execute("INSERT INTO player_season_stats_deduped VALUES ('1',?,2,64,33.5,2143,7.7,8.3,531,1.4,0.5,4.0,2.1,37.5,134,731,1504,282,620,399,509)", [s])
    c.execute("INSERT INTO player_season_stats_deduped VALUES ('3',?,2,65,27.7,1799,12.9,10.7,697,1.3,0.9,3.6,2.5,34.6,163,763,1310,117,284,389,471)", [s])
    _with_full_line_columns(c, "player_season_stats_deduped", _SEASON_FULL_LINE_COLUMNS)
    # A comparison reports a NetPoints summary under the box-score line. Only
    # Luka has a row, so the "some players have none" path is exercised too.
    c.execute(
        "CREATE TABLE net_points_player (athlete_id VARCHAR, season INTEGER, net_points_season_type VARCHAR, overall_per_100_poss DOUBLE, offense_per_100_poss DOUBLE, defense_per_100_poss DOUBLE)"
    )
    c.execute("INSERT INTO net_points_player VALUES ('1',?,'Regular Season',5.87,5.20,0.67)", [s])
    return AnswerContext(con=c, out_dir=tmp_path)


def test_player_line_reports_one_named_stat_with_its_total(ps_con: AnswerContext) -> None:
    result = player_line(ps_con, Reading(who=Who(kind="player", players=("Luka Doncic",)), measure=Measure(key="points", as_typed="points")))
    assert result.text == (f"Luka Doncic averaged 33.5 points per game in 64 games in the {current_season()} regular season. That is 2,143 in total.")
    # The page's own label table maps BOTH "avgPoints" and "points" to "PTS"
    # (LABELS, web/static/index.html) - two tiles that would read identically
    # while one is a per-game average and the other a season total in the
    # thousands. `data["labels"]` names each explicitly so a renderer does not
    # have to guess them apart.
    assert result.data["stats"] == {"gamesPlayed": 64, "avgPoints": 33.5, "points": 2143}
    assert result.data["labels"] == {"avgPoints": "points per game", "points": "points total"}


def test_player_line_with_no_stat_gives_a_stat_line(ps_con: AnswerContext) -> None:
    result = player_line(ps_con, Reading(who=Who(kind="player", players=("Nikola Jokic",))))
    # The words named no stat, so the full box line is beneath the sentence:
    # every figure it did not say, the shooting with its makes and attempts
    # (ISSUES.md #471).
    assert result.text == (
        f"Nikola Jokic averaged 27.7 points, 12.9 rebounds and 10.7 assists per game in 65 games in the {current_season()} regular season.\n"
        "   MIN  STL  BLK  TOV   PF               FG%            3PT%             FT%\n"
        "  34.6  1.3  0.9  3.6  2.5  58.2 (763-1,310)  41.2 (117-284)  82.6 (389-471)"
    )
    assert result.data["stats"]["avgBlocks"] == 0.9 and result.data["stats"]["fieldGoalsAttempted"] == 1310 and result.data["stats"]["fieldGoalPct"] == 58.2


def test_player_line_asks_instead_of_guessing_between_players(ps_con: AnswerContext) -> None:
    """Asked, not guessed: the reader knows exactly which names are
    ambiguous, so it says so."""
    result = player_line(ps_con, Reading(who=Who(kind="player", players=("Curry",)), measure=Measure(key="points", as_typed="points")))
    assert result.text == "'Curry' matches more than one player - did you mean Seth Curry or Stephen Curry?"
    assert result.data["candidates"] == ["Seth Curry", "Stephen Curry"]


def test_player_line_unknown_player_is_refused_by_name(ps_con: AnswerContext) -> None:
    """A name nothing matches is the read's own refusal."""
    assert (
        player_line(ps_con, Reading(who=Who(kind="player", players=("Nobody At All",)))).text
        == "No player found matching 'Nobody At All'. ESPN's box scores, which the warehouse's players come from, start with 1993-94, so a player whose career ended before then may not be on record."
    )


def test_player_line_with_no_player_is_declined(ps_con: AnswerContext) -> None:
    with pytest.raises(Unsupported):
        player_line(ps_con, Reading(measure=Measure(key="points", as_typed="points")))


def test_player_line_reports_a_missing_season_honestly(ps_con: AnswerContext) -> None:
    result = player_line(ps_con, Reading(who=Who(kind="player", players=("Luka Doncic",)), span=Span(season=1999)))
    assert result.text == "Luka Doncic has no 1999 regular season numbers in the warehouse."


# The columns a retired player's season row is given in below; the full
# line's columns ps_con carries beside them stay empty.
_OLD_TIMER_INSERT = (
    "INSERT INTO player_season_stats_deduped "
    "(athlete_id, season, season_type, gamesPlayed, avgPoints, points, avgRebounds, avgAssists, assists, avgSteals, avgBlocks, avgTurnovers, avgFouls, avgMinutes, fouls) "
)


def test_player_line_defaulted_season_redirects_to_a_retired_players_range(ps_con: AnswerContext) -> None:
    """The season was never named - it defaulted to "now" - so a retired
    player with nothing there gets pointed at what the warehouse DOES hold for
    him instead of a refusal that reads as though his whole career were
    missing (issue #18). "Allen Iverson has no 2026 regular season numbers"
    is true and answers a different question than the one asked."""
    ps_con.con.execute("INSERT INTO players VALUES ('6','Old Timer')")
    ps_con.con.execute(_OLD_TIMER_INSERT + "VALUES ('6',2005,2,70,20.0,1400,5.0,4.0,280,1.0,0.5,2.0,2.0,35.0,140)")
    ps_con.con.execute(_OLD_TIMER_INSERT + "VALUES ('6',2008,2,60,18.0,1080,4.0,3.5,210,1.0,0.4,1.8,1.8,32.0,108)")
    answer = player_line(ps_con, Reading(who=Who(kind="player", players=("Old Timer",)), measure=Measure(key="points", as_typed="points"))).text
    assert answer == (
        f"Old Timer has no {current_season()} regular season numbers in the warehouse. He last appears in 2008. The warehouse holds his 2005-2008 regular seasons; name one, or ask for his career."
    )


def test_player_line_defaulted_season_with_nothing_on_record_stays_plain(ps_con: AnswerContext) -> None:
    """A defaulted season is only a redirect when there is somewhere to
    redirect to. A player with no rows in the table at all - as opposed to no
    rows in the current season - has nothing to point at, and the plain
    refusal is the honest answer: there is genuinely no data on record."""
    ps_con.con.execute("INSERT INTO players VALUES ('7','Nobody Yet')")
    answer = player_line(ps_con, Reading(who=Who(kind="player", players=("Nobody Yet",)), measure=Measure(key="points", as_typed="points"))).text
    assert answer == f"Nobody Yet has no {current_season()} regular season numbers in the warehouse."


def test_player_line_a_named_season_keeps_the_plain_refusal(ps_con: AnswerContext) -> None:
    """The season the question named IS the fact the answer is about - unlike
    the defaulted case above, redirecting a season asked for outright would be
    a fluent answer to a question nobody asked."""
    ps_con.con.execute("INSERT INTO players VALUES ('6','Old Timer')")
    ps_con.con.execute(_OLD_TIMER_INSERT + "VALUES ('6',2005,2,70,20.0,1400,5.0,4.0,280,1.0,0.5,2.0,2.0,35.0,140)")
    answer = player_line(ps_con, Reading(who=Who(kind="player", players=("Old Timer",)), measure=Measure(key="points", as_typed="points"), span=Span(season=1999))).text
    assert answer == "Old Timer has no 1999 regular season numbers in the warehouse."


def test_player_line_never_reports_a_total_as_a_per_game_number(ps_con: AnswerContext) -> None:
    # A total is said beside the per-game figure, never inlined into it
    # ("33.5 points (2143 total) per game" states something false).
    answer = player_line(ps_con, Reading(who=Who(kind="player", players=("Luka Doncic",)), measure=Measure(key="points", as_typed="points"))).text or ""
    assert "(2,143 total) per game" not in answer and "2143" not in answer


# ---------------- a surname, narrowed to the season asked about ----------------


@pytest.fixture
def curry_ctx(tmp_path: Path) -> AnswerContext:
    """The six Currys the warehouse really holds. Each has a season line in a
    year he really played; only Seth and Stephen have one in the current
    season, as in the warehouse - so a name list sorted by name and cut at
    five would ask about four Currys who never played in the season asked
    about and hide Stephen."""
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    c.execute("INSERT INTO players VALUES ('1','Dell Curry'),('2','Eddy Curry'),('3','JamesOn Curry'),('4','Michael Curry'),('5','Seth Curry'),('6','Stephen Curry')")
    c.execute(
        "CREATE TABLE player_season_stats_deduped (athlete_id VARCHAR, season INTEGER, season_type INTEGER, "
        "gamesPlayed INTEGER, avgPoints DOUBLE, points INTEGER, avgRebounds DOUBLE, avgAssists DOUBLE, assists INTEGER)"
    )
    s = current_season()
    c.executemany(
        "INSERT INTO player_season_stats_deduped VALUES (?,?,2,60,?,?,3.0,4.0,240)",
        [("1", 2000, 10.0, 600), ("2", 2005, 13.0, 780), ("3", 2010, 1.0, 3), ("4", 2000, 5.0, 300), ("5", 2014, 5.0, 300), ("5", s, 8.0, 480), ("6", 2010, 17.5, 1400), ("6", s, 26.6, 1600)],
    )
    c.execute(
        "CREATE TABLE player_game_log (athlete_id VARCHAR, season INTEGER, season_type INTEGER, game_date VARCHAR, "
        "opponent_abbr VARCHAR, minutes DOUBLE, points INTEGER, rebounds INTEGER, assists INTEGER)"
    )
    c.execute("INSERT INTO player_game_log VALUES ('6',?,2,'2026-01-02','BOS',34.0,31,5,6)", [s])
    _with_full_line_columns(c, "player_season_stats_deduped", _SEASON_FULL_LINE_COLUMNS)
    return AnswerContext(con=c, out_dir=tmp_path)


def test_a_surname_asks_only_about_the_players_who_played_that_season(curry_ctx: AnswerContext) -> None:
    """ "how did curry do against the celtics this year" asks about the Currys
    who played this season, never a name list cut at five that leaves out the
    player almost certainly meant. Seth and Stephen both played this season,
    so it asks about them."""
    result = player_line(curry_ctx, Reading(who=Who(kind="player", players=("Curry",)), span=Span(season=current_season())))
    assert result.text == "'Curry' matches more than one player - did you mean Seth Curry or Stephen Curry?"
    assert result.data["candidates"] == ["Seth Curry", "Stephen Curry"]


def test_a_surname_resolves_when_only_one_candidate_played_that_season(curry_ctx: AnswerContext) -> None:
    """Elimination, not preference: the other five have no row to answer
    from, so there is nothing left to choose between."""
    curry_ctx.con.execute("DELETE FROM player_season_stats_deduped WHERE athlete_id = '5'")
    answer = player_line(curry_ctx, Reading(who=Who(kind="player", players=("Curry",)), measure=Measure(key="points", as_typed="points"))).text
    assert answer == f"Stephen Curry averaged 26.6 points per game in 60 games in the {current_season()} regular season. That is 1,600 in total."


def test_narrowing_that_eliminates_everybody_still_asks_about_everybody(curry_ctx: AnswerContext) -> None:
    """No Curry played in 1990, so no answer to "which one?" has numbers - but
    picking one because the season is empty would be a guess, so the question
    is asked about every Curry."""
    result = player_line(curry_ctx, Reading(who=Who(kind="player", players=("Curry",)), span=Span(season=1990)))
    assert result.data["candidates"] == ["Dell Curry", "Eddy Curry", "JamesOn Curry", "Michael Curry", "Seth Curry", "Stephen Curry"]


def _paytons(ctx: AnswerContext) -> None:
    """ "Gary Payton" is one player's whole name and also matches Gary Payton
    II; only the son has a line this season - as in the warehouse, where the
    father's last is 2007. Not "Dell Curry": that name matches one player, so
    it never reaches the exact-match rule."""
    ctx.con.execute("INSERT INTO players VALUES ('7','Gary Payton'),('8','Gary Payton II')")
    ctx.con.execute(
        "INSERT INTO player_season_stats_deduped (athlete_id, season, season_type, gamesPlayed, avgPoints, points, avgRebounds, avgAssists, assists) "
        "VALUES ('7',2007,2,70,5.0,350,2.0,3.0,210),('8',?,2,60,7.0,420,3.0,2.0,120)",
        [current_season()],
    )


def test_a_name_given_in_full_is_its_owner_wherever_he_has_numbers(curry_ctx: AnswerContext) -> None:
    """The father's name in full, in a season he played and over a career, is
    the father - the son being the Payton who plays now changes nothing."""
    _paytons(curry_ctx)
    assert player_line(curry_ctx, Reading(who=Who(kind="player", players=("Gary Payton",)), span=Span(season=2007))).text.startswith("Gary Payton averaged 5 points")
    career = resolve_player(curry_ctx.con, "Gary Payton", Availability("player_season_stats_deduped"), None, current_season())
    assert isinstance(career, Entity) and career.name == "Gary Payton"


def test_a_name_given_in_full_yields_to_the_one_namesake_with_numbers(curry_ctx: AnswerContext) -> None:
    """The father cannot be the answer to a question about this season and
    exactly one namesake can, so it is him, and the reading is said with the
    way back to the father - never "Gary Payton has no 2026 regular season
    numbers", true and about the wrong man (the filed case was "Jabari
    Smith", the retired father's exact name, where the son is "Jabari Smith
    Jr.")."""
    _paytons(curry_ctx)
    with collect_name_readings() as readings:
        answer = player_line(curry_ctx, Reading(who=Who(kind="player", players=("Gary Payton",)), span=Span(season=current_season()))).text
    assert answer.startswith("Gary Payton II averaged 7 points")
    s = current_season()
    assert readings == [f"('Gary Payton' was read as Gary Payton II, the only match who played in {s - 1}-{s % 100:02d}. Gary Payton also matches - name a season he played to ask about him.)"]


def test_a_name_left_open_is_whoever_played_the_last_season_of_the_span(curry_ctx: AnswerContext) -> None:
    """A history through 2005 reads each Curry's seasons up to 2005, so Dell and
    Michael could answer it too. Eddy is the only one who played in 2005, so
    it is his, said with the others named: a default is allowed where it is
    visible and can be corrected."""
    with collect_name_readings() as readings:
        result = season_history(curry_ctx, Reading(who=Who(kind="player", players=("Curry",)), measure=Measure(key="points", as_typed="points"), span=Span(season=2005)))
    assert result.text.startswith("Eddy Curry, points per game by regular season")
    assert readings == [
        "('Curry' was read as Eddy Curry, the only match who played in 2004-05. Dell Curry and Michael Curry also match - use the full name, or name a season they played, to ask about one of them.)"
    ]


def test_a_reading_is_collected_only_where_somebody_is_listening(curry_ctx: AnswerContext) -> None:
    """Outside collect_name_readings the resolution is the same and nothing is
    kept - a reader called directly has nowhere to put the sentence."""
    assert season_history(curry_ctx, Reading(who=Who(kind="player", players=("Curry",)), measure=Measure(key="points", as_typed="points"), span=Span(season=2005))).text.startswith("Eddy Curry")


def test_a_history_names_whoever_reached_its_last_season_first(curry_ctx: AnswerContext) -> None:
    """ "Curry's scoring over the last 4 seasons" keeps all six Currys - each
    has seasons through 2026 to answer with - so the cap could hide Stephen
    as in the one-season question. The two who played in the season the
    history ends at are named first, and never counted away."""
    result = season_history(curry_ctx, Reading(who=Who(kind="player", players=("Curry",)), measure=Measure(key="points", as_typed="points"), window=Window(count=4)))
    assert result.text == "'Curry' matches more than one player - did you mean Seth Curry, Stephen Curry, Dell Curry, Eddy Curry or JamesOn Curry (1 other also matches)?"


def test_a_career_keeps_every_curry_but_names_the_active_ones_first(curry_ctx: AnswerContext) -> None:
    """A career question has every season in scope, so Dell's career is as real
    an answer as Stephen's and nobody is eliminated. Narrowed like one season,
    it would be; left unordered, the cap would hide Stephen."""
    result = player_line(curry_ctx, Reading(who=Who(kind="player", players=("Curry",)), span=Span(career=True)))
    assert result.data["candidates"] == ["Seth Curry", "Stephen Curry", "Dell Curry", "Eddy Curry", "JamesOn Curry", "Michael Curry"]
    assert result.text == "'Curry' matches more than one player - did you mean Seth Curry, Stephen Curry, Dell Curry, Eddy Curry or JamesOn Curry (1 other also matches)?"


def test_a_game_log_narrows_to_whoever_has_games_that_season(curry_ctx: AnswerContext) -> None:
    """No season named means the current one. Dell has games in 2000 and Seth
    and Stephen this season, so only the two of them are asked about - the
    log's span is the season asked, never every season, which would name Dell,
    Eddy, JamesOn, Michael and Seth and hide Stephen."""
    curry_ctx.con.execute("INSERT INTO player_game_log VALUES ('1',2000,2,'2000-01-02','BOS',30.0,12,2,3),('5',?,2,'2026-01-03','BOS',20.0,8,1,2)", [current_season()])
    result = player_log(curry_ctx, Reading(who=Who(kind="player", players=("Curry",)), window=Window(order="recent", count=5)))
    assert result.data["candidates"] == ["Seth Curry", "Stephen Curry"]


def test_a_without_teammate_is_found_past_the_first_page_of_matches() -> None:
    """ "Without williams" is 62 players by name. The teammate narrowing reads
    past find_players' first page of ten, so a teammate who sorts eleventh is
    still his teammate."""
    from association.query.entities import Entity
    from association.query.player_relation import ResolvedSpan, _resolved_teammate

    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    given = ["Alan", "Brandon", "Cody", "Deron", "Eric", "Frank", "Grant", "Hank", "Ike", "Jalen", "Kenrich"]
    c.executemany("INSERT INTO players VALUES (?, ?)", [(str(i), f"{name} Williams") for i, name in enumerate(given)])
    c.execute("INSERT INTO players VALUES ('k', 'Klay Thompson')")
    c.execute("CREATE TABLE player_box_stats (athlete_id VARCHAR, season INTEGER, season_type INTEGER, team_id VARCHAR)")
    c.execute("INSERT INTO player_box_stats VALUES ('k', 2026, 2, '9'), ('10', 2026, 2, '9'), ('0', 2026, 2, '5')")
    got = _resolved_teammate(c, "Williams", Entity(id="k", name="Klay Thompson"), ResolvedSpan(2026, 2))
    assert got == Entity(id="10", name="Kenrich Williams")


def test_a_career_high_names_the_currys_playing_now_first(curry_ctx: AnswerContext) -> None:
    """A game's high reads a career as every season on record, so Dell stays
    a candidate - and the list is anchored, so the cap never cuts Stephen
    behind the retired Currys."""
    curry_ctx.con.execute("INSERT INTO player_game_log VALUES ('1',2000,2,'2000-01-02','BOS',30.0,12,2,3),('5',?,2,'2026-01-03','BOS',20.0,8,1,2)", [current_season()])
    result = game_highs(curry_ctx, Reading(who=Who(kind="player", players=("Curry",)), measure=Measure(key="points", as_typed="points"), span=Span(career=True)))
    assert result.data["candidates"] == ["Seth Curry", "Stephen Curry", "Dell Curry"]


def test_netpoints_narrows_against_either_of_the_tables_it_reads(curry_ctx: AnswerContext) -> None:
    """A player's NetPoints ratings answer from the season totals OR the fingerprint, and
    the two disagree about who they hold - 63 player-seasons are in the first
    only and 8 in the second only. A candidate with a row in either one has an
    answer, so neither table alone may eliminate him."""
    curry_ctx.con.execute("CREATE TABLE net_points_player (athlete_id VARCHAR, season INTEGER)")
    curry_ctx.con.execute("CREATE TABLE net_points_player_fingerprint (athlete_id VARCHAR, season INTEGER)")
    curry_ctx.con.execute("INSERT INTO net_points_player VALUES ('6', ?)", [current_season()])
    curry_ctx.con.execute("INSERT INTO net_points_player_fingerprint VALUES ('5', ?)", [current_season()])
    result = netpoints_ratings(curry_ctx, Reading(who=Who(kind="player", players=("Curry",))))
    assert result.data["candidates"] == ["Seth Curry", "Stephen Curry"]


def test_netpoints_without_its_tables_asks_which_player(curry_ctx: AnswerContext) -> None:
    """NetPoints is an opt-in fetch, so its tables may not exist at all. With
    nothing to narrow against, an ambiguous name is asked about, rather than
    a catalog error out of the resolver."""
    result = netpoints_ratings(curry_ctx, Reading(who=Who(kind="player", players=("Curry",))))
    assert len(result.data["candidates"]) == 6


def test_player_ranking_handles_triple_doubles_as_a_metric_not_a_recount(lb_con: AnswerContext) -> None:
    """ESPN precomputes doubleDouble/tripleDouble as a season count, so "most
    triple-doubles" is a ranking of season counts rather than a per-game threshold recount."""
    lb_con.con.execute("ALTER TABLE player_season_stats ADD COLUMN tripleDouble INTEGER")
    lb_con.con.execute("UPDATE player_season_stats SET tripleDouble = 34 WHERE athlete_id = '1'")
    lb_con.con.execute("UPDATE player_season_stats SET tripleDouble = 2 WHERE athlete_id = '2'")
    result = player_ranking(lb_con, Reading(measure=Measure(key="triple_double", as_typed="triple_double"), window=Window(count=2)))
    assert result.text == (f"Luka Doncic led the league in triple-doubles in the {current_season()} regular season, at 34. Next: Stephen Curry (2).")


# ---------------- a team's record and log ----------------


@pytest.fixture
def gl_con(tmp_path: Path) -> AnswerContext:
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE teams (team_id VARCHAR, abbreviation VARCHAR, display_name VARCHAR)")
    c.execute("CREATE TABLE standings (team_id VARCHAR, season INTEGER, wins DOUBLE, losses DOUBLE, winPercent DOUBLE, streak DOUBLE, playoffSeed DOUBLE)")
    c.execute(
        "CREATE TABLE games (event_id VARCHAR, season INTEGER, season_type INTEGER, date VARCHAR, "
        "home_team_id VARCHAR, away_team_id VARCHAR, home_score INTEGER, away_score INTEGER, winner_team_id VARCHAR, neutral_site BOOLEAN, venue_city VARCHAR)"
    )
    c.execute("CREATE TABLE team_box_stats (event_id VARCHAR, season INTEGER, season_type INTEGER, team_id VARCHAR, opponent_team_id VARCHAR, home_away VARCHAR)")
    c.execute("INSERT INTO teams VALUES ('18','NY','New York Knicks'),('2','BOS','Boston Celtics')")
    s = current_season()
    c.execute("INSERT INTO standings VALUES ('18',?,53.0,29.0,0.646,3.0,4.0)", [s])
    c.execute(
        "INSERT INTO games VALUES ('e1',?,2,'2026-04-10T22:00Z','18','2',112,95,'18',false,'New York'),('e2',?,2,'2026-04-12T22:00Z','2','18',110,96,'2',false,'Boston')",
        [s, s],
    )
    c.execute("INSERT INTO team_box_stats VALUES ('e1',?,2,'18','2','home'),('e2',?,2,'18','2','away')", [s, s])
    # Two rows that are not games, in the two shapes ESPN really serves: a 0-0
    # placeholder with no winner (1999 and 2000 hold 132 of them, 133 involving
    # Chicago) and a second event id for e1 an hour later (2003-01-04 DAL-PHI
    # is stored as both 230104006 and 400222658). BOTH carry team_box_stats
    # rows, because every `games` row does - which is why joining that table
    # filtered out neither of them.
    c.execute(
        "INSERT INTO games VALUES ('e3',?,2,'2026-04-14T17:00Z','18','2',0,0,NULL,false,'New York'),('e1b',?,2,'2026-04-10T23:00Z','18','2',112,95,'18',false,'New York')",
        [s, s],
    )
    c.execute("INSERT INTO team_box_stats VALUES ('e3',?,2,'18','2','home'),('e1b',?,2,'18','2','home')", [s, s])
    real_games.build_table(c, {"games", "teams"})
    return AnswerContext(con=c, out_dir=tmp_path)


@pytest.fixture
def tq_con(tmp_path: Path) -> AnswerContext:
    """games.home_linescores/away_linescores plus the team_box_stats rows for
    BOTH sides of each game (a team's period total needs a team's own
    perspective row regardless of which side of the matchup it queries from,
    unlike gl_con, which only ever queries the Knicks)."""
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE teams (team_id VARCHAR, abbreviation VARCHAR, display_name VARCHAR)")
    c.execute(
        "CREATE TABLE games (event_id VARCHAR, season INTEGER, season_type INTEGER, date VARCHAR, "
        "home_team_id VARCHAR, away_team_id VARCHAR, home_score INTEGER, away_score INTEGER, winner_team_id VARCHAR, "
        "home_linescores VARCHAR, away_linescores VARCHAR, neutral_site BOOLEAN, venue_city VARCHAR)"
    )
    c.execute("CREATE TABLE team_box_stats (event_id VARCHAR, season INTEGER, season_type INTEGER, team_id VARCHAR, opponent_team_id VARCHAR, home_away VARCHAR)")
    c.execute("INSERT INTO teams VALUES ('18','NY','New York Knicks'),('2','BOS','Boston Celtics'),('5','LAL','Los Angeles Lakers')")
    s = current_season()
    # `neutral_site`/`venue_city`: a team's period total reads its games off
    # query/team_games.py's relation, whose own `TEAM_GAMES_SQL`
    # (`association.query.team_games._TEAM_GAMES`) reads both unconditionally
    # (cup-final detection, the home/road split), as every fixture that
    # relation is read against carries them (`tests/query/test_team_readers.py:
    # team_ctx`, this file's own `playoff_ctx`). None of these three games is
    # at a neutral site.
    c.execute(
        "INSERT INTO games VALUES "
        "('e1',?,2,'2026-04-10T22:00Z','18','2',112,95,'18','30,25,28,29','20,25,25,25',false,'New York'),"
        "('e2',?,2,'2026-04-12T22:00Z','2','18',110,96,'2','25,30,25,30','20,20,28,28',false,'Boston'),"
        "('e3',?,2,'2026-04-14T22:00Z','18','5',108,100,'18','10,32,36,30','25,25,25,25',false,'New York')",
        [s, s, s],
    )
    c.execute(
        "INSERT INTO team_box_stats VALUES "
        "('e1',?,2,'18','2','home'),('e1',?,2,'2','18','away'),"
        "('e2',?,2,'18','2','away'),('e2',?,2,'2','18','home'),"
        "('e3',?,2,'18','5','home'),('e3',?,2,'5','18','away')",
        [s, s, s, s, s, s],
    )
    real_games.build_table(c, {"games", "teams"})
    return AnswerContext(con=c, out_dir=tmp_path)


# A team's record has its own tests in test_team_readers.py, over a fixture
# with the standings columns it reads.


def test_player_log_includes_home_and_away_games(gl_con: AnswerContext) -> None:
    """Joining games by home_team_id alone would silently return a team's
    home games and drop every away game, with no error."""
    games = player_log(gl_con, Reading(who=Who(kind="team", teams=("Knicks",)))).data["games"]
    assert {g["home_away"] for g in games} == {"home", "away"}


def test_player_log_reports_each_games_own_score_from_that_teams_side(gl_con: AnswerContext) -> None:
    by_date = {g["date"]: g for g in player_log(gl_con, Reading(who=Who(kind="team", teams=("Knicks",)))).data["games"]}
    assert (by_date["2026-04-10"]["team_score"], by_date["2026-04-10"]["opponent_score"]) == (112, 95)
    # Away game: the Knicks' score is the AWAY score, not the home one.
    assert (by_date["2026-04-12"]["team_score"], by_date["2026-04-12"]["opponent_score"]) == (96, 110)


def test_player_log_tallies_the_record_over_exactly_the_rows_shown(gl_con: AnswerContext) -> None:
    result = player_log(gl_con, Reading(who=Who(kind="team", teams=("Knicks",))))
    assert result.data["wins"] == 1
    assert "(1-1)" in (result.text or "")
    assert result.data["headline"] == (result.text or "").splitlines()[0].rstrip(":")


def test_player_log_order_first_is_ascending(gl_con: AnswerContext) -> None:
    # LIMIT 1 without an explicit ORDER BY returns an arbitrary row, not the
    # earliest one.
    first = player_log(gl_con, Reading(who=Who(kind="team", teams=("Knicks",)), window=Window(order="first", count=1))).data["games"]
    assert first[0]["date"] == "2026-04-10"
    recent = player_log(gl_con, Reading(who=Who(kind="team", teams=("Knicks",)), window=Window(count=1))).data["games"]
    assert recent[0]["date"] == "2026-04-12"


def test_player_log_filters_an_exact_calendar_date(gl_con: AnswerContext) -> None:
    # games.date is a full ISO timestamp, so `= 'YYYY-MM-DD'` is valid SQL that
    # silently matches nothing.
    games = player_log(gl_con, Reading(who=Who(kind="team", teams=("Knicks",)), cuts=Cuts(date="2026-04-12"))).data["games"]
    assert [g["date"] for g in games] == ["2026-04-12"]


def test_player_log_ambiguous_team_asks(gl_con: AnswerContext) -> None:
    gl_con.con.execute("INSERT INTO teams VALUES ('12','LAC','LA Clippers'),('13','LAL','Los Angeles Lakers')")
    assert "did you mean" in (player_log(gl_con, Reading(who=Who(kind="team", teams=("LA",)))).text or "")


def test_player_log_without_team_or_player_is_declined(gl_con: AnswerContext) -> None:
    with pytest.raises(Unsupported):
        player_log(gl_con, Reading(window=Window(count=5)))


def test_team_record_refuses_a_limited_set_rather_than_reporting_the_full_season(gl_con: AnswerContext) -> None:
    """ "How did they do in their last 10?" answered with the full-season record
    is a silent substitution - the log tallies over exactly the games shown."""
    with pytest.raises(Unsupported):
        team_record(gl_con, Reading(who=Who(kind="team", teams=("Knicks",)), window=Window(count=10)))


def _add_knicks_postseason(gl_con: AnswerContext) -> None:
    """Two postseason games after `gl_con`'s two regular-season ones, so a
    "last N games" question has real games of both types to mix."""
    s = current_season()
    gl_con.con.execute(
        "INSERT INTO games VALUES ('p1',?,3,'2026-04-20T22:00Z','18','2',101,90,'18',false,'New York'),('p2',?,3,'2026-04-22T22:00Z','2','18',99,105,'18',false,'Boston')",
        [s, s],
    )
    gl_con.con.execute("INSERT INTO team_box_stats VALUES ('p1',?,3,'18','2','home'),('p2',?,3,'18','2','away')", [s, s])
    real_games.build_table(gl_con.con, {"games", "teams"})


def test_a_teams_last_n_games_with_no_season_type_named_reads_both(gl_con: AnswerContext) -> None:
    """ "Show me the Knicks last 5 games" (ISSUES.md): the real last three are
    both playoff games newer than the regular-season ones, so a log of the
    regular season alone would leave them both out."""
    _add_knicks_postseason(gl_con)
    result = player_log(gl_con, Reading(who=Who(kind="team", teams=("Knicks",)), span=Span(both=True), window=Window(order="recent", count=3)))
    games = result.data["games"]
    assert [g["date"] for g in games] == ["2026-04-22", "2026-04-20", "2026-04-12"]
    assert [g["season"] for g in games] == [2026, 2026, 2026]  # a postseason game's own calendar year
    answer = result.text or ""
    # The default it used is visible, not silent.
    assert "last 3 games (1 regular season and 2 postseason)" in answer
    assert result.data["wins"] == 2 and result.data["losses"] == 1


def test_a_teams_log_over_its_career_including_the_playoffs_reads_both_types(gl_con: AnswerContext) -> None:
    """ISSUES.md #416: "sixers last 10 games against bos including playoffs"
    reads the span's ``both``: both types are one read over the team relation
    (the postseason by the calendar year it was played in), the heading
    saying so - never ten regular-season meetings headed "regular seasons";
    a range of seasons the same."""
    _add_knicks_postseason(gl_con)
    s = current_season()
    for span in (Span(both=True, career=True), Span(both=True, since=s - 1)):
        result = player_log(gl_con, Reading(who=Who(kind="team", teams=("Knicks",)), cuts=Cuts(opponent="Boston Celtics"), span=span, window=Window(order="recent", count=3)))
        assert [g["date"] for g in result.data["games"]] == ["2026-04-22", "2026-04-20", "2026-04-12"], span
        assert "regular season and postseason" in (result.text or ""), result.text
        assert "last 3 of 4 games" in (result.text or ""), result.text


def test_a_teams_last_n_games_states_the_total_points_asked_for(gl_con: AnswerContext) -> None:
    """F128 (ISSUES.md): "total points scored by the raptors in the last 10
    games" states the total over the games it narrowed to, the question's own
    number. e2 (away, 96), p1 (home, 101) and p2 (away, 105) sum to 302."""
    _add_knicks_postseason(gl_con)
    result = player_log(gl_con, Reading(who=Who(kind="team", teams=("Knicks",)), measure=Measure(key="points", as_typed="points"), span=Span(both=True), window=Window(order="recent", count=3)))
    assert "\n  Total points: 302." in (result.text or "")
    # The team compiler's window sum reads the same three games, both types
    # merged as the log merges them, and says which types the default kept.
    scope = Reading(who=Who(kind="team", teams=("Knicks",)), span=Span(both=True), window=Window(order="recent", count=3))
    summed = run_team(gl_con.con, TeamQuery(**scope.filters(), metric="points"))
    assert (summed.value, summed.games, summed.wins, summed.losses) == (302.0, 3, 2, 1)
    assert summed.narrowed_text == " over their last 3 games (1 regular season and 2 postseason)"
    differential = run_team(gl_con.con, TeamQuery(**scope.filters(), metric="differential"))
    assert differential.value == 3.0
    # A range of seasons or a calendar over both types is one read of both
    # with the window cut after it, as the log reads it.
    ranged = Reading(who=Who(kind="team", teams=("Knicks",)), span=Span(both=True, since=2024), window=Window(order="recent", count=3))
    over_range = run_team(gl_con.con, TeamQuery(**ranged.filters(), metric="points"))
    assert (over_range.value, over_range.games, over_range.wins, over_range.losses) == (302.0, 3, 2, 1)
    assert team_sentence(TeamQuery(**ranged.filters(), metric="points"), over_range) == (
        "The New York Knicks had 302 points over their last 3 games in the regular season and postseason career (2024 on) (2-1)."
    )
    on_sundays = Reading(who=Who(kind="team", teams=("Knicks",)), cuts=Cuts(situation=situation_of("on sundays")), span=Span(both=True), window=Window(order="recent", count=3))
    sunday = run_team(gl_con.con, TeamQuery(**on_sundays.filters(), metric="points"))
    assert (sunday.value, sunday.games, sunday.wins, sunday.losses) == (96.0, 1, 0, 1)
    # The log's own both-types read honors a calendar narrowing, reading both
    # types in one: of e1 (a Friday), e2 (a Sunday), p1 (a Monday) and p2 (a
    # Wednesday), Sunday's alone.
    sundays = player_log(gl_con, Reading(who=Who(kind="team", teams=("Knicks",)), cuts=Cuts(situation=situation_of("on sundays")), span=Span(both=True), window=Window(order="recent", count=3)))
    assert [g["date"] for g in sundays.data["games"]] == ["2026-04-12"]
    assert "regular season and postseason" in (sundays.text or ""), sundays.text


def test_a_teams_log_keeps_its_calendar_narrowing(gl_con: AnswerContext) -> None:
    """A team's log keeps its calendar or alignment narrowing over the window
    ("celtics last 5 games vs the west" is never the last five, Orlando among
    them): the log hands the relation every cut, never the venue alone."""
    result = player_log(gl_con, Reading(who=Who(kind="team", teams=("Knicks",)), cuts=Cuts(situation=situation_of("on sundays")), window=Window(order="recent", count=5)))
    assert [g["date"] for g in result.data["games"]] == ["2026-04-12"]


def test_a_teams_last_n_games_states_the_point_differential_asked_for(gl_con: AnswerContext) -> None:
    """F129 (ISSUES.md): "Knicks point differential over the last 7 games" -
    e2 (-14), p1 (+11) and p2 (+6) sum to +3 over 3 games, +1.00 per game."""
    _add_knicks_postseason(gl_con)
    result = player_log(gl_con, Reading(who=Who(kind="team", teams=("Knicks",)), measure=Measure(as_typed="pointsDifference"), span=Span(both=True), window=Window(order="recent", count=3)))
    assert "\n  Point differential: +3 (+1.00 per game)." in (result.text or "")


def test_a_teams_single_type_log_also_states_a_total(gl_con: AnswerContext) -> None:
    """The single-season-type reader (``compose.logs._team_log``, not the mixed one)
    gets the same line: the Knicks' two regular-season games (112 home, 96
    away) total 208 points."""
    result = player_log(gl_con, Reading(who=Who(kind="team", teams=("Knicks",)), measure=Measure(key="points", as_typed="points")))
    assert "\n  Total points: 208." in (result.text or "")


def test_a_teams_plain_stat_names_no_total_line(gl_con: AnswerContext) -> None:
    """A `stat` this module does not map to a total (or none at all) adds no
    total line to the plain listing."""
    result = player_log(gl_con, Reading(who=Who(kind="team", teams=("Knicks",))))
    answer = result.text or ""
    assert "Total points" not in answer and "Point differential" not in answer


def test_a_teams_last_n_games_naming_its_season_type_read_only_that_type(gl_con: AnswerContext) -> None:
    """Saying "playoff games" or "regular season games" outright means only
    that type."""
    _add_knicks_postseason(gl_con)
    playoffs = player_log(gl_con, Reading(who=Who(kind="team", teams=("Knicks",)), span=Span(season_type=3), window=Window(order="recent", count=5)))
    assert [g["date"] for g in playoffs.data["games"]] == ["2026-04-22", "2026-04-20"]
    assert "of the 2026 postseason" in (playoffs.text or "")
    regular = player_log(gl_con, Reading(who=Who(kind="team", teams=("Knicks",)), span=Span(season_type=2), window=Window(order="recent", count=5)))
    assert [g["date"] for g in regular.data["games"]] == ["2026-04-12", "2026-04-10"]
    assert "of the 2026 regular season" in (regular.text or "")


def test_a_teams_log_says_how_many_games_its_window_cut_from(gl_con: AnswerContext) -> None:
    """ISSUES.md #402: "show the 76ers pace for each game last season": the
    heading says how many games the window cut the listing from, as a
    player's log does ("last 10 of 49 games", F149), never "last 10 games of
    the 2025 regular season" alone - and nothing more where the listing is
    every game the span holds."""
    s = current_season()
    newest = player_log(gl_con, Reading(who=Who(kind="team", teams=("Knicks",)), span=Span(season_type=2), window=Window(order="recent", count=1)))
    assert (newest.text or "").startswith(f"New York Knicks, most recent of 2 games of the {s} regular season (0-1):")
    first = player_log(gl_con, Reading(who=Who(kind="team", teams=("Knicks",)), span=Span(season_type=2), window=Window(order="first", count=1)))
    assert (first.text or "").startswith(f"New York Knicks, first of 2 games of the {s} regular season (1-0):")
    every = player_log(gl_con, Reading(who=Who(kind="team", teams=("Knicks",)), span=Span(season_type=2), window=Window(order="recent", count=5)))
    assert (every.text or "").startswith(f"New York Knicks, last 2 games of the {s} regular season (1-1):")


def _gl_box(gl_con: AnswerContext) -> None:
    """The Knicks' and the Celtics' box rows for the two Knicks games, with
    the figures a log is asked for: e1's both sides, e2's Celtics row only
    (the Knicks' e2 row stands with no figures, as an empty box serves it)."""
    for column in ("threePointFieldGoalsMade", "threePointFieldGoalsAttempted", "offensiveRebounds", "defensiveRebounds"):
        gl_con.con.execute(f"ALTER TABLE team_box_stats ADD COLUMN {column} INTEGER")
    gl_con.con.execute("UPDATE team_box_stats SET threePointFieldGoalsMade = 12, threePointFieldGoalsAttempted = 30, offensiveRebounds = 10, defensiveRebounds = 35 WHERE event_id = 'e1'")
    gl_con.con.execute("INSERT INTO team_box_stats VALUES ('e1', ?, 2, '2', '18', 'away', 8, 32, 9, 30), ('e2', ?, 2, '2', '18', 'home', 15, 36, 12, 33)", [current_season()] * 2)


def test_a_teams_log_shows_the_box_score_figure_it_was_asked_for(gl_con: AnswerContext) -> None:
    """ISSUES.md #403: "show the 76ers 3pt% for each game last season": the
    log shows the figure beside each game and over them all (a percentage
    over the makes and attempts summed), says the game with no box row, and
    reads the opponent's figure from the opponent's row."""
    _gl_box(gl_con)
    asked = Reading(who=Who(kind="team", teams=("Knicks",)), span=Span(season_type=2), measure=Measure(key="threePointFieldGoalPct", as_typed="3pt%"))
    lines = (player_log(gl_con, asked).text or "").split("\n")
    assert lines[1].endswith("at Boston Celtics  3P% -") and lines[2].endswith("vs Boston Celtics  3P% 40.0% (12-30)"), lines
    assert lines[3:] == ["  3-point percentage: 40.0% (12-30).", "  (1 of these games has no 3-point percentage on record; the figure is over the rest.)"]
    rebounds = player_log(gl_con, replace(asked, measure=Measure(key="rebounds", as_typed="rebounds", whose="opponent")))
    lines = (rebounds.text or "").split("\n")
    assert lines[1].endswith("opp REB 45") and lines[2].endswith("opp REB 39") and lines[3] == "  Total opponents' rebounds: 84 (42.0 per game).", lines
    assert rebounds.data["figure_total"] == 84 and rebounds.data["figure_whose"] == "opponent"


def test_a_teams_log_keeps_the_games_past_a_line_on_its_figure_or_its_opponents(gl_con: AnswerContext) -> None:
    """ISSUES.md #430: "celtics last 10 games when allowing 120+ points": the
    log keeps the games past a line on its score or its box row, its own or
    its opponents' (``Line.whose``), says the line, and shows the opponents'
    figure where the line is on theirs."""
    _gl_box(gl_con)
    knicks = Reading(who=Who(kind="team", teams=("Knicks",)), span=Span(season_type=2), window=Window(order="recent", count=10))
    scored = player_log(gl_con, replace(knicks, lines=(Line(measure="points", op=">=", value=100, as_typed="100+ points", narrows=True),)))
    assert [g["date"] for g in scored.data["games"]] == ["2026-04-10"], scored.text
    assert (scored.text or "").startswith("New York Knicks when they had 100+ points,"), scored.text
    allowed = player_log(gl_con, replace(knicks, lines=(Line(measure="points", op=">=", value=100, as_typed="100+ points", narrows=True, whose="opponent"),)))
    assert [g["date"] for g in allowed.data["games"]] == ["2026-04-12"], allowed.text
    assert (allowed.text or "").startswith("New York Knicks when their opponents had 100+ points,"), allowed.text
    under = player_log(gl_con, replace(knicks, lines=(Line(measure="points", op="<", value=100, as_typed="under 100 points", narrows=True),)))
    assert [g["date"] for g in under.data["games"]] == ["2026-04-12"], under.text
    # A box-score line: the Knicks' own 12 threes in e1 (their e2 row has no
    # figure, kept by no line), and the Celtics' 15 in e2, shown as theirs.
    threes = Measure(key="threePointFieldGoalsMade", as_typed="threes")
    own = player_log(gl_con, replace(knicks, measure=threes, lines=(Line(measure="threePointFieldGoalsMade", op=">=", value=12, as_typed="12+ threes", narrows=True),)))
    assert [g["date"] for g in own.data["games"]] == ["2026-04-10"], own.text
    theirs = player_log(gl_con, replace(knicks, measure=threes, lines=(Line(measure="threePointFieldGoalsMade", op=">=", value=15, as_typed="15+ threes", narrows=True, whose="opponent"),)))
    lines = (theirs.text or "").split("\n")
    assert [g["date"] for g in theirs.data["games"]] == ["2026-04-12"] and lines[1].endswith("opp 3PM 15"), lines
    # A line on a figure no game of a team carries is refused by name.
    minutes = player_log(gl_con, replace(knicks, lines=(Line(measure="minutes", op=">=", value=240, as_typed="240+ minutes", narrows=True),)))
    assert (minutes.text or "").startswith("A team's games carry no minutes game by game"), minutes.text


def test_a_teams_log_asked_for_a_figure_no_game_carries_is_refused_by_name(gl_con: AnswerContext) -> None:
    """ISSUES.md #403: a figure a team's games do not carry game by game
    (pace, a player's usage) is refused by name, never a listing of the
    scores without it."""
    pace = player_log(gl_con, Reading(who=Who(kind="team", teams=("Knicks",)), measure=Measure(key="pace", as_typed="pace")))
    assert pace.text == (
        "A team's games carry no pace game by game - only the score, the result and the box score (rebounds, assists, shooting, turnovers). "
        "Its season's pace is on record - ask for the New York Knicks' pace over a season."
    )
    usage = player_log(gl_con, Reading(who=Who(kind="team", teams=("Knicks",)), measure=Measure(key="usage_pct", as_typed="usage rate")))
    assert usage.text == "A team's games carry no usage rate game by game - only the score, the result and the box score (rebounds, assists, shooting, turnovers)."
    # A team whose name is no plural takes "'s": "the Orlando Magic's pace", never "Magic'".
    magic = refusal_phrase("team_figure_unread", {"team": "Orlando Magic", "stat": "pace", "what": "log"})
    assert (magic or "").endswith("ask for the Orlando Magic's pace over a season."), magic


def test_a_teams_last_n_games_all_one_type_name_that_one_type(gl_con: AnswerContext) -> None:
    """When the last N happen to be all one type - most teams and players
    never reach the postseason at all - the header names that one type, not
    "1 regular season" repeated N times."""
    result = player_log(gl_con, Reading(who=Who(kind="team", teams=("Knicks",)), span=Span(both=True), window=Window(order="recent", count=2)))
    assert [g["date"] for g in result.data["games"]] == ["2026-04-12", "2026-04-10"]
    assert "last 2 games of the 2026 regular season" in (result.text or "")


def test_a_teams_last_n_games_with_no_games_at_all_says_so_without_blaming_a_type(gl_con: AnswerContext) -> None:
    gl_con.con.execute("INSERT INTO teams VALUES ('99','LAC','LA Clippers')")
    result = player_log(gl_con, Reading(who=Who(kind="team", teams=("LA Clippers",)), span=Span(both=True), window=Window(order="recent", count=5)))
    assert result.data["games"] == []
    assert "No 2026 games found for the LA Clippers" in (result.text or "")


def test_a_players_last_n_games_with_no_season_type_named_reads_both(pg_ctx: AnswerContext) -> None:
    """The player-log counterpart, over `pg_ctx`'s season: Podziemski's five
    newest games this season are all postseason (p5 down to p1), and his
    sixth-newest is the regular-season e3 - so a limit of 6 mixes the two
    types and a limit of 5 stays uniform, the same distinction the team test
    above checks."""
    s = current_season()
    # Eastern dates, not the UTC ones in the fixture's own table: each 00:30Z
    # tip is 8:30pm the previous evening Eastern (see AGENTS.md on
    # `eastern_date` - a fixed offset gets this hour wrong, which is exactly
    # why every conversion here goes through the real one).
    uniform = player_log(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), span=Span(both=True), window=Window(order="recent", count=5)))
    assert [g["date"][5:] for g in uniform.data["games"]] == ["05-04", "05-02", "04-24", "04-21", "04-19"]
    assert f"last 5 games of the {s} postseason" in (uniform.text or "")
    mixed = player_log(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), span=Span(both=True), window=Window(order="recent", count=6)))
    assert [g["date"][5:] for g in mixed.data["games"]] == ["05-04", "05-02", "04-24", "04-21", "04-19", "01-10"]
    assert "last 6 games (1 regular season and 5 postseason)" in (mixed.text or "")
    # His per-game average is over exactly the 6 rows shown, playoffs and
    # regular season points combined - not silently the regular season alone.
    assert mixed.data["averages"]["points"] == pytest.approx((10 + 20 + 30 + 5 + 15 + 15) / 6)


def test_a_players_last_n_games_keeps_its_other_narrowings_under_both_types(pg_ctx: AnswerContext) -> None:
    """`opponent` narrows a mixed-type log - Podziemski's games against
    Detroit span both season types (e2, e3 regular season; p1-p3 postseason),
    so "last 3 games vs Detroit" with no season type stated finds the three
    newest of either type against them, not just the regular-season two."""
    result = player_log(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), cuts=Cuts(opponent="Detroit Pistons"), span=Span(both=True), window=Window(order="recent", count=3)))
    assert [g["date"][5:] for g in result.data["games"]] == ["04-24", "04-21", "04-19"]
    assert "vs the Detroit Pistons" in (result.text or "")


def test_a_players_last_n_games_with_no_games_this_season_says_so(pg_ctx: AnswerContext) -> None:
    result = player_log(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), span=Span(season=2005, both=True), window=Window(order="recent", count=5)))
    assert result.data["games"] == []
    assert "no games recorded in the 2005 season" in (result.text or "")
    # A season under the log's floor is the coverage refusal: never "no
    # games" about a year nothing here can see.
    floored = player_log(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), span=Span(season=1990, both=True), window=Window(order="recent", count=5)))
    assert "only go back to 1994" in floored.text and floored.data["message"] == floored.text


# ---------------- a shot chart ----------------


@pytest.fixture
def sc_ctx(tmp_path: Path) -> AnswerContext:
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    c.execute(
        "CREATE TABLE shot_chart (athlete_id VARCHAR, season INTEGER, season_type INTEGER, event_id VARCHAR, "
        "period INTEGER, clock VARCHAR, made BOOLEAN, shot_type VARCHAR, coordinate_x INTEGER, coordinate_y INTEGER, points_attempted INTEGER, description VARCHAR)"
    )
    # Empty by default - a shot chart question with no narrowing at all never
    # reaches the relation (`compose.shots._shots_has_narrowing`), so most
    # tests here need neither table. The handful that narrow by `order`
    # INSERT into these directly rather than declaring their own copy, since
    # `player_game_log`/`games` carry the columns the relation's played guard
    # and window read (`minutes`, `did_not_play`, `starter`,
    # `opponent_team_id`, `games.date`). `opponent_abbr` is NULL by
    # construction here (no `teams` table), which is what makes `game_label`
    # return None and a single-game chart name its bare event id.
    c.execute(
        "CREATE TABLE player_game_log (athlete_id VARCHAR, season INTEGER, season_type INTEGER, event_id VARCHAR, team_id VARCHAR, opponent_team_id VARCHAR, "
        "starter BOOLEAN, did_not_play BOOLEAN, minutes INTEGER, game_date VARCHAR, opponent_abbr VARCHAR)"
    )
    c.execute(
        "CREATE TABLE games (event_id VARCHAR, season INTEGER, season_type INTEGER, date VARCHAR, home_team_id VARCHAR, away_team_id VARCHAR, "
        "home_score INTEGER, away_score INTEGER, winner_team_id VARCHAR)"
    )
    c.execute("CREATE TABLE teams (team_id VARCHAR, abbreviation VARCHAR, display_name VARCHAR)")
    c.execute("INSERT INTO teams VALUES ('9','GS','Golden State Warriors'),('13','LAL','Los Angeles Lakers')")
    c.execute("INSERT INTO players VALUES ('1','Stephen Curry')")
    # Two labeled threes from the top of the arc, 26 feet from the rim at
    # (25, 0) - a real three's position, so the label and the line agree.
    c.executemany(
        "INSERT INTO shot_chart VALUES ('1',?,2,'e1',1,'10:00',?,'Jump Shot',25,26,3,'26-foot three point jumper')",
        [(current_season(), True), (current_season(), False)],
    )
    return AnswerContext(con=c, out_dir=tmp_path / "out")


def test_shot_chart_writes_a_file_and_reports_its_path(sc_ctx: AnswerContext) -> None:
    result = shot_chart(sc_ctx, Reading(who=Who(kind="player", players=("Stephen Curry",)), span=Span(season=current_season())))
    assert "Rendered shot chart for Stephen Curry" in (result.text or "")
    assert list((sc_ctx.out_dir).glob("*.html"))


def test_shot_chart_reads_a_nickname_token_by_token(sc_ctx: AnswerContext) -> None:
    """ "Steph" is not a substring of "Stephen", so whole-phrase ILIKE matching
    would miss this exact query; per-token AND matching finds him."""
    result = shot_chart(sc_ctx, Reading(who=Who(kind="player", players=("Steph Curry",)), span=Span(season=current_season())))
    assert "Rendered shot chart for Stephen Curry" in (result.text or "")


def test_shot_chart_says_how_it_read_a_near_spelling(sc_ctx: AnswerContext) -> None:
    """A near spelling of one player is drawn for him, and the reading is
    collected for the answer (the answering loop appends it)."""
    with collect_name_readings() as readings:
        result = shot_chart(sc_ctx, Reading(who=Who(kind="player", players=("Stephen Cury",)), span=Span(season=current_season())))
    assert "Rendered shot chart for Stephen Curry" in (result.text or "")
    assert readings == ["('Stephen Cury' matches no player exactly and was read as Stephen Curry, the only near spelling on record - spell the name exactly to ask about someone else.)"]


def test_shot_chart_names_a_player_nothing_matched(sc_ctx: AnswerContext) -> None:
    result = shot_chart(sc_ctx, Reading(who=Who(kind="player", players=("Nobody Real",)), span=Span(season=current_season())))
    assert "No player found" in (result.text or "")
    assert result.artifacts == []


def test_shot_chart_narrows_a_surname_to_whoever_took_shots_that_season(sc_ctx: AnswerContext) -> None:
    """Seth exists and Stephen has the shots, so "Curry" is not a question this
    season - only one of them can have produced the chart being asked for."""
    sc_ctx.con.execute("INSERT INTO players VALUES ('2','Seth Curry')")
    result = shot_chart(sc_ctx, Reading(who=Who(kind="player", players=("Curry",)), span=Span(season=current_season())))
    assert "Rendered shot chart for Stephen Curry" in (result.text or "")


def test_shot_chart_asks_which_player_when_both_took_shots(sc_ctx: AnswerContext) -> None:
    """And when narrowing cannot separate them it asks, rather than drawing
    whichever sorts first and titling the plot with the wrong Curry."""
    sc_ctx.con.execute("INSERT INTO players VALUES ('2','Seth Curry')")
    sc_ctx.con.execute(f"INSERT INTO shot_chart VALUES ('2',{current_season()},2,'e2',1,'9:00',TRUE,'Jump Shot',25,26,3,'26-foot three point jumper')")
    result = shot_chart(sc_ctx, Reading(who=Who(kind="player", players=("Curry",)), span=Span(season=current_season())))
    assert result.text == "'Curry' matches more than one player - did you mean Seth Curry or Stephen Curry?"
    assert not list(sc_ctx.out_dir.glob("*.html"))


def test_shot_chart_reports_no_matching_shots(sc_ctx: AnswerContext) -> None:
    # An empty result is the answer: no other source has a better chart than
    # the table just queried.
    # 2010: a season the shot table covers (1999 is under its floor, refused by the floor first).
    result = shot_chart(sc_ctx, Reading(who=Who(kind="player", players=("Stephen Curry",)), span=Span(season=2010)))
    assert result.text == "No shots found for Stephen Curry in the 2010 regular season."


def test_shot_chart_defaulted_season_redirects_to_a_retired_players_range(sc_ctx: AnswerContext) -> None:
    """No season was named - "now" defaulted - so a player with shots only in
    an earlier season is pointed at it beside the season the read looked in
    (issue #18, ISSUES.md #105), never "the given filters", which were never
    given. No "or ask for his career" - a shot chart draws one season, never
    a career."""
    sc_ctx.con.execute("INSERT INTO players VALUES ('2','Old Timer')")
    sc_ctx.con.execute("INSERT INTO shot_chart VALUES ('2',2010,2,'e9',1,'8:00',TRUE,'Jump Shot',25,26,2,'20-foot two point jumper')")
    result = shot_chart(sc_ctx, Reading(who=Who(kind="player", players=("Old Timer",))))
    assert result.text == f"No shots found for Old Timer in the {current_season()} regular season. He last appears in 2010. The warehouse holds his 2010 regular season; name one."
    assert result.artifacts == []


def test_shot_chart_a_named_season_is_the_season_the_refusal_names(sc_ctx: AnswerContext) -> None:
    """The season the question named is the fact the refusal is about, so
    the sentence names it and nothing redirects - unlike the defaulted case
    above (ISSUES.md #105)."""
    sc_ctx.con.execute("INSERT INTO players VALUES ('2','Old Timer')")
    sc_ctx.con.execute("INSERT INTO shot_chart VALUES ('2',2010,2,'e9',1,'8:00',TRUE,'Jump Shot',25,26,2,'20-foot two point jumper')")
    result = shot_chart(sc_ctx, Reading(who=Who(kind="player", players=("Old Timer",)), span=Span(season=2009)))
    assert result.text == "No shots found for Old Timer in the 2009 regular season."


def test_a_nonsense_shot_value_never_reaches_the_chart() -> None:
    """The Measure refuses a shot value that is neither 2 nor 3."""
    with pytest.raises(ValueError, match="shot_value"):
        Measure(shot_value=0)  # type: ignore[arg-type]


def test_shot_chart_without_a_player_is_declined(sc_ctx: AnswerContext) -> None:
    with pytest.raises(Unsupported):
        shot_chart(sc_ctx, Reading(span=Span(season=current_season())))


def test_a_scoped_chart_uses_the_same_player_it_looked_the_game_up_for(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The chart and the game it is scoped to must be about ONE player.

    The name is resolved once, both to find the event_id and to title the
    chart. Two independent resolutions could pick differently, and the result
    would be a chart titled for one Curry scoped to a game the other one
    played: wrong, and invisible, since the plot looks perfectly normal.

    Both Currys exist and both played this season; only Stephen took a shot in
    it, which is what narrows the name to him. The scoping game must then be
    his, not Seth's.
    """
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    c.execute(
        "CREATE TABLE shot_chart (athlete_id VARCHAR, season INTEGER, season_type INTEGER, event_id VARCHAR, "
        "period INTEGER, clock VARCHAR, made BOOLEAN, shot_type VARCHAR, coordinate_x INTEGER, coordinate_y INTEGER, points_attempted INTEGER, description VARCHAR)"
    )
    c.execute(
        "CREATE TABLE player_game_log (athlete_id VARCHAR, season INTEGER, season_type INTEGER, event_id VARCHAR, team_id VARCHAR, opponent_team_id VARCHAR, "
        "starter BOOLEAN, did_not_play BOOLEAN, minutes INTEGER, game_date VARCHAR, opponent_abbr VARCHAR)"
    )
    c.execute(
        "CREATE TABLE games (event_id VARCHAR, season INTEGER, season_type INTEGER, date VARCHAR, home_team_id VARCHAR, away_team_id VARCHAR, "
        "home_score INTEGER, away_score INTEGER, winner_team_id VARCHAR)"
    )
    # Two players both matching "Curry", each with their own game. Seth's game
    # is the LATER one, so an unnarrowed "most recent Curry game" would scope to
    # it - and only Stephen has a shot this season, so the chart must be his.
    c.execute("INSERT INTO players VALUES ('1','Seth Curry'), ('2','Stephen Curry')")
    c.executemany(
        "INSERT INTO player_game_log VALUES (?,?,2,?,'9','13',TRUE,FALSE,30,?,NULL)",
        [("1", current_season(), "seth_game", "2026-01-03T23:00Z"), ("2", current_season(), "steph_game", "2026-01-02T23:00Z")],
    )
    c.executemany(
        "INSERT INTO games VALUES (?,?,2,?,'9','13',110,100,'9')",
        [("seth_game", current_season(), "2026-01-03T23:00Z"), ("steph_game", current_season(), "2026-01-02T23:00Z")],
    )
    c.executemany(
        "INSERT INTO shot_chart VALUES (?,?,2,?,1,'10:00',true,'Jump Shot',25,26,3,'26-foot three point jumper')",
        [("1", current_season() - 1, "seth_old_game"), ("2", current_season(), "steph_game")],
    )
    ctx = AnswerContext(con=c, out_dir=tmp_path / "out")

    calls = []
    real = shotchart.find_players

    def counting(con: Any, text: str, *args: Any, **kwargs: Any) -> Any:
        calls.append(text)
        return real(con, text, *args, **kwargs)

    monkeypatch.setattr(shotchart, "find_players", counting)
    result = shot_chart(ctx, Reading(who=Who(kind="player", players=("Curry",)), span=Span(season=current_season()), window=Window(order="recent")))

    # The load-bearing assertion: the name is resolved ONCE. Two resolutions
    # agree only as long as both spell the tie-break the same way, which is a
    # convention, not a guarantee.
    assert len(calls) == 1, f"player resolved {len(calls)} times: {calls}"

    answer = result.text or ""
    assert "Stephen Curry" in answer
    written = [f.name for f in ctx.out_dir.glob("*.html")]
    assert len(written) == 1
    # The scoping event_id lands in the filename; it must be the charted
    # player's game, not the other candidate's.
    assert "seth_game" not in written[0] and "steph_game" in written[0]


def test_shot_chart_defaults_an_unspecified_season_to_the_current_one(sc_ctx: AnswerContext) -> None:
    """A season left unnamed is the current one, never the player's entire
    career in one plot."""
    sc_ctx.con.execute("INSERT INTO shot_chart VALUES ('1',2019,2,'e9',1,'5:00',TRUE,'Jump Shot',10,10,2,'makes 18-foot jumper')")
    answer = shot_chart(sc_ctx, Reading(who=Who(kind="player", players=("Stephen Curry",)))).text or ""
    # Only this season's two shots, not the 2019 one as well.
    assert "1/2 made" in answer and str(current_season()) in answer


# ---------------- a shot chart and a shot distance: `span` "career" (#141) ----------------
#
# "show a shot chart for steph curry in all playoff games" reads every
# postseason, never one defaulted season drawn as though it were all of them.
# A career drawn says that it IS a career, and names the seasons it could not
# draw.


def _add_career_table(con: duckdb.DuckDBPyConnection, rows: list[tuple[str, int, int, int]]) -> None:
    """The slice of `player_season_stats_deduped` `season_line.seasons_played` reads -
    just enough columns to say where a player's own career sits against the
    2002 shot floor, independent of the shot chart itself."""
    con.execute("CREATE TABLE player_season_stats_deduped (athlete_id VARCHAR, season INTEGER, season_type INTEGER, gamesPlayed INTEGER)")
    con.executemany("INSERT INTO player_season_stats_deduped VALUES (?,?,?,?)", rows)


def test_shot_chart_honors_a_career_span(sc_ctx: AnswerContext) -> None:
    """Draws every regular season on record, not only the current one - the
    fixture's two current-season shots AND a 2019 one, 2/3 made where a
    single season draws 1/2."""
    _add_career_table(sc_ctx.con, [("1", 2019, 2, 70), ("1", current_season(), 2, 60)])
    sc_ctx.con.execute("INSERT INTO shot_chart VALUES ('1',2019,2,'e9',1,'5:00',TRUE,'Jump Shot',10,10,2,'makes 18-foot jumper')")
    answer = shot_chart(sc_ctx, Reading(who=Who(kind="player", players=("Stephen Curry",)), span=Span(season_type=2, career=True))).text or ""
    assert "2/3 made" in answer
    assert f"Covers his whole regular season career on record (2019-{current_season()})." in answer


def test_shot_chart_career_span_names_a_career_entirely_before_the_floor(sc_ctx: AnswerContext) -> None:
    """A career that ends before shots begin has nothing to draw, and the
    answer says why - not a plain "No shots found ... with the given
    filters", which would blame a filter that was never given and read as
    though the warehouse held nothing of his at all."""
    _add_career_table(sc_ctx.con, [("1", 1997, 3, 20), ("1", 1998, 3, 15)])
    answer = shot_chart(sc_ctx, Reading(who=Who(kind="player", players=("Stephen Curry",)), span=Span(season_type=3, career=True))).text or ""
    assert answer == "No shots found for Stephen Curry in any postseason on record. Stephen Curry's postseason career (1997-1998) ends before shot data begins, in 2002, so none of it can be shown."


def test_shot_chart_career_span_names_the_seasons_the_floor_leaves_out(sc_ctx: AnswerContext) -> None:
    """A career that straddles 2002 draws what it can and names what it
    can't, the same discipline a defaulted single season's redirect uses."""
    _add_career_table(sc_ctx.con, [("1", 1999, 2, 50), ("1", current_season(), 2, 60)])
    answer = shot_chart(sc_ctx, Reading(who=Who(kind="player", players=("Stephen Curry",)), span=Span(season_type=2, career=True))).text or ""
    assert "Shot data begins with the 2002 season, so his 1999-2001 regular seasons are not shown." in answer


def test_shot_chart_refuses_a_career_span_with_a_named_season(sc_ctx: AnswerContext) -> None:
    """ "Career" and a named year at once answer different questions - the same
    conflict `span_of` raises on elsewhere."""
    _add_career_table(sc_ctx.con, [("1", current_season(), 2, 60)])
    with pytest.raises(Unsupported, match="career span and the 2020 season"):
        shot_chart(sc_ctx, Reading(who=Who(kind="player", players=("Stephen Curry",)), span=Span(season=2020, career=True)))


def test_shot_chart_refuses_a_career_span_with_an_order(sc_ctx: AnswerContext) -> None:
    """ "His last game" picks one game inside one season; a career asks for
    every one of them. Declined rather than silently picking one."""
    _add_career_table(sc_ctx.con, [("1", current_season(), 2, 60)])
    with pytest.raises(Unsupported, match="career span"):
        shot_chart(sc_ctx, Reading(who=Who(kind="player", players=("Stephen Curry",)), span=Span(career=True), window=Window(order="recent")))


def test_shot_distance_honors_a_career_span(sc_ctx: AnswerContext) -> None:
    """Averages across every season on record instead of only the latest,
    the same shape as a shot chart."""
    _add_career_table(sc_ctx.con, [("1", 2019, 2, 70), ("1", current_season(), 2, 60)])
    sc_ctx.con.execute("INSERT INTO shot_chart VALUES ('1',2019,2,'e9',1,'5:00',TRUE,'Jump Shot',10,10,2,'makes 18-foot jumper')")
    answer = shot_distance(sc_ctx, Reading(who=Who(kind="player", players=("Stephen Curry",)), span=Span(season_type=2, career=True))).text or ""
    assert "over 3 attempts" in answer
    assert f"Covers his whole regular season career on record (2019-{current_season()})." in answer


def test_shot_distance_career_span_names_a_career_entirely_before_the_floor(sc_ctx: AnswerContext) -> None:
    _add_career_table(sc_ctx.con, [("1", 1997, 3, 20), ("1", 1998, 3, 15)])
    answer = shot_distance(sc_ctx, Reading(who=Who(kind="player", players=("Stephen Curry",)), span=Span(season_type=3, career=True))).text or ""
    assert answer == (
        "No shots with recorded coordinates for Stephen Curry in the career postseason. Stephen Curry's postseason career (1997-1998) ends before "
        "shot data begins, in 2002, so none of it can be shown."
    )


# ---------------- a comparison of players ----------------


def test_player_comparison_puts_players_side_by_side(ps_con: AnswerContext) -> None:
    answer = player_comparison(ps_con, Reading(who=Who(kind="pair", players=("Luka Doncic", "Nikola Jokic")))).text or ""
    assert "Luka Doncic vs Nikola Jokic" in answer
    assert "points" in answer and "33.5" in answer and "27.7" in answer


def test_player_comparison_defaults_to_the_whole_stat_line(ps_con: AnswerContext) -> None:
    """A comparison asks which player is better, and three counting stats
    cannot answer that - they omit both halves of the defensive line and
    everything a player gives back. A table costs nothing per row, so the rows
    a comparison turns on are all there by default. A player's line asking
    "how many points did Luka average" gives the number it asked for."""
    answer = player_comparison(ps_con, Reading(who=Who(kind="pair", players=("Luka Doncic", "Nikola Jokic")))).text or ""
    for label in ("points", "rebounds", "assists", "steals", "blocks", "turnovers", "fouls", "minutes"):
        assert f"\n{label}" in answer, label
    assert "4.0" in answer and "3.6" in answer  # turnovers, which a three-stat line omits


def test_player_comparison_shows_each_shooting_split_with_its_makes_and_attempts(ps_con: AnswerContext) -> None:
    """ "compare lebron and shai 2026 playoffs" showed no shooting, which the
    feed sample's key required (ISSUES.md #471): the whole line's comparison
    says each split as the percentage and the makes and attempts it is of,
    computed from the two; a named stat narrows the table to it."""
    result = player_comparison(ps_con, Reading(who=Who(kind="pair", players=("Luka Doncic", "Nikola Jokic"))))
    rows = {line.split()[0]: line.split()[1:] for line in (result.text or "").split("\n")[2:] if line.strip()}
    assert rows["FG%"] == ["48.6", "(731-1,504)", "58.2", "(763-1,310)"]
    assert rows["3PT%"] == ["45.5", "(282-620)", "41.2", "(117-284)"]
    assert rows["FT%"] == ["78.4", "(399-509)", "82.6", "(389-471)"]
    assert result.data["players"]["Luka Doncic"]["fieldGoalPct"] == 48.6 and result.data["players"]["Nikola Jokic"]["freeThrowsAttempted"] == 471
    named = player_comparison(ps_con, Reading(who=Who(kind="pair", players=("Luka Doncic", "Nikola Jokic")), measure=Measure(key="points", as_typed="points"))).text or ""
    assert "FG%" not in named


def test_a_career_line_shows_the_full_box_line_summed_over_its_seasons(ps_con: AnswerContext) -> None:
    """A career's full line is summed as its sentence is - each per-game
    figure a total over the games that carry it, each split's makes and
    attempts summed over every season - never a mean of the seasons'
    averages: Luka's two seasons, 64 and 20 games, block 0.5 and 1.0 a game,
    so (32 + 20) / 84 = 0.6."""
    ps_con.con.execute("UPDATE player_season_stats_deduped SET blocks = 32 WHERE athlete_id = '1'")
    ps_con.con.execute(
        "INSERT INTO player_season_stats_deduped (athlete_id, season, season_type, gamesPlayed, avgPoints, points, avgRebounds, avgAssists, assists, avgSteals, avgBlocks, "
        "avgTurnovers, avgFouls, avgMinutes, fouls, blocks, fieldGoalsMade, fieldGoalsAttempted, threePointFieldGoalsMade, threePointFieldGoalsAttempted, freeThrowsMade, freeThrowsAttempted) "
        "VALUES ('1', 2019, 2, 20, 10.0, 200, 4.0, 3.0, 60, 1.0, 1.0, 2.0, 2.0, 25.0, 40, 20, 80, 200, 20, 80, 20, 25)"
    )
    text = player_line(ps_con, Reading(who=Who(kind="player", players=("Luka Doncic",)), span=Span(career=True))).text or ""
    sentence, heads, cells = text.split("\n")
    assert sentence.startswith("Luka Doncic averaged 27.9 points") and "in 84 games over his career" in sentence
    assert dict(zip(heads.split(), cells.split("  ")[1:], strict=False))["BLK"].strip() == "0.6"
    assert "47.6 (811-1,704)" in cells and "43.1 (302-700)" in cells and "78.5 (419-534)" in cells


def test_player_comparison_shows_the_netpoints_summary(ps_con: AnswerContext) -> None:
    """Per 100 possessions, not season totals: a comparison is exactly the
    question totals answer badly, since they mostly rank by playing time."""
    result = player_comparison(ps_con, Reading(who=Who(kind="pair", players=("Luka Doncic", "Nikola Jokic"))))
    answer = result.text or ""
    assert "net pts/100" in answer and "offense" in answer and "defense" in answer
    assert "+5.87" in answer and "+5.20" in answer and "+0.67" in answer
    assert result.data["netpoints"]["Luka Doncic"]["overall_per_100_poss"] == 5.87


def test_a_player_with_no_netpoints_row_is_blank_rather_than_zero(ps_con: AnswerContext) -> None:
    """Only Luka has a row in the fixture. Drawing Jokic as +0.00 would read as
    "contributed nothing" rather than "not on record"."""
    answer = player_comparison(ps_con, Reading(who=Who(kind="pair", players=("Luka Doncic", "Nikola Jokic")))).text or ""
    netpoints_row = next(line for line in answer.splitlines() if line.startswith("net pts/100"))
    assert "+5.87" in netpoints_row and "+0.00" not in netpoints_row and netpoints_row.rstrip().endswith("-")


def test_player_comparison_omits_netpoints_entirely_when_nobody_has_any(ps_con: AnswerContext) -> None:
    """An empty NetPoints block under two 1990s players would read as "both
    contributed nothing" rather than "this season predates the data"."""
    ps_con.con.execute("DELETE FROM net_points_player")
    answer = player_comparison(ps_con, Reading(who=Who(kind="pair", players=("Luka Doncic", "Nikola Jokic")))).text or ""
    assert "net pts/100" not in answer
    assert "33.5" in answer  # the rest of the comparison is unaffected


def test_player_comparison_survives_a_warehouse_with_no_netpoints_table(ps_con: AnswerContext) -> None:
    """NetPoints is a separate opt-in fetch, so the table may not exist at all.
    Unlike per-game NetPoints, which has nothing else to say, a comparison is
    complete without it - so this drops the section and answers the rest."""
    ps_con.con.execute("DROP TABLE net_points_player")
    answer = player_comparison(ps_con, Reading(who=Who(kind="pair", players=("Luka Doncic", "Nikola Jokic")))).text or ""
    assert "net pts/100" not in answer and "33.5" in answer


def test_a_named_stat_still_narrows_the_comparison(ps_con: AnswerContext) -> None:
    """ "Who scores more" gets scoring, not a wall."""
    answer = player_comparison(ps_con, Reading(who=Who(kind="pair", players=("Luka Doncic", "Nikola Jokic")), measure=Measure(key="points", as_typed="points"))).text or ""
    assert "\npoints" in answer
    assert "\nrebounds" not in answer and "\nsteals" not in answer


def test_a_comparison_is_not_refused_before_netpoints_begins(ps_con: AnswerContext) -> None:
    """The NetPoints table is deliberately absent from the comparison's
    declared sources (``coverage.SOURCES``): listing it would put a 2019
    coverage floor on every comparison and refuse the 1994-2018 ones
    outright."""
    from association.query.coverage import check_coverage

    assert check_coverage(PLAYER_COMPARISON, Reading(span=Span(season=2005, season_type=2))) is None


def test_a_range_of_seasons_wholly_under_a_floor_is_refused_by_it() -> None:
    """ "most wins by player nba 1980s": a range whose last season is under
    the box scores' floor is refused by the floor, where the game-level
    ranking said "No games for every player in the regular season career
    (1994-1989)"; a range that reaches the floor is answered from it."""
    from association.query.coverage import check_coverage, coverage_refusal

    counted = PointShape("player_games", "ranking", "count")
    decade = Reading(span=Span(since=1980, until=1989, season_type=2))
    refusal = check_coverage(counted, decade)
    assert refusal is not None and "only go back to 1994" in refusal and "no data for 1989" in refusal
    assert coverage_refusal(counted, decade) is not None
    assert check_coverage(counted, Reading(span=Span(since=1990, until=1999, season_type=2))) is None
    assert check_coverage(counted, Reading(span=Span(since=1980, season_type=2))) is None


def test_player_comparison_uses_a_table_not_prose(ps_con: AnswerContext) -> None:
    """Prose can state that a player with 0.4 steals led one with 1.6; a table
    cannot make that mistake."""
    lines = (player_comparison(ps_con, Reading(who=Who(kind="pair", players=("Luka Doncic", "Nikola Jokic")))).text or "").splitlines()
    assert len(lines) >= 5 and lines[1].strip().startswith("Luka Doncic")


def test_player_comparison_aligns_decimals_consistently(ps_con: AnswerContext) -> None:
    ps_con.con.execute("UPDATE player_season_stats_deduped SET avgPoints = 25.0 WHERE athlete_id = '3'")
    answer = player_comparison(ps_con, Reading(who=Who(kind="pair", players=("Luka Doncic", "Nikola Jokic")), measure=Measure(key="points", as_typed="points"))).text or ""
    assert "25.0" in answer  # not a trailing-zero-stripped "25" beside "33.5"


def test_player_comparison_resolves_a_nickname(ps_con: AnswerContext) -> None:
    ps_con.con.execute("INSERT INTO players VALUES ('9','Shai Gilgeous-Alexander')")
    answer = player_comparison(ps_con, Reading(who=Who(kind="pair", players=("Luka Doncic", "SGA")))).text or ""
    assert "Shai Gilgeous-Alexander" in answer


def test_player_comparison_asks_rather_than_guessing_an_ambiguous_name(ps_con: AnswerContext) -> None:
    answer = player_comparison(ps_con, Reading(who=Who(kind="pair", players=("Curry", "Nikola Jokic")))).text or ""
    assert "did you mean Seth Curry or Stephen Curry?" in answer


def test_player_comparison_suggests_the_player_a_fabricated_name_meant(ps_con: AnswerContext) -> None:
    """A model's "compare sga and embid" as 'Jemel Embiid' - the surname
    corrected, the given name invented - must not bury a name the warehouse
    holds behind one made-up word, though every token has to match."""
    answer = player_comparison(ps_con, Reading(who=Who(kind="pair", players=("Luka Doncic", "Jemel Jokic")))).text or ""
    assert answer == "No player found matching 'Jemel Jokic' - did you mean Nikola Jokic?"


def test_player_comparison_asks_about_a_near_miss(ps_con: AnswerContext) -> None:
    """A near miss is answered here, as ambiguity is. "Jemel Jokic" is the
    surname back-off - a given name that is nobody's beside a surname that is
    one player's - which asks rather than defaults: see
    entities.read_near_spelling."""
    answer = player_comparison(ps_con, Reading(who=Who(kind="pair", players=("Luka Doncic", "Jemel Jokic")))).text or ""
    assert "did you mean Nikola Jokic?" in answer


def test_player_comparison_reads_a_single_near_spelling_as_that_player(ps_con: AnswerContext) -> None:
    """A typo of exactly one player's name is that player, and the answer says
    so (entities.read_near_spelling): "Nikoal Jokic" is Nikola Jokic, never
    "did you mean Nikola Jokic?"."""
    with collect_name_readings() as readings:
        answer = player_comparison(ps_con, Reading(who=Who(kind="pair", players=("Luka Doncic", "Nikoal Jokic")))).text or ""
    assert "did you mean" not in answer
    assert "Nikola Jokic" in answer
    assert readings == ["('Nikoal Jokic' matches no player exactly and was read as Nikola Jokic, the only near spelling on record - spell the name exactly to ask about someone else.)"]


def test_a_name_with_nothing_near_it_is_refused_by_name(ps_con: AnswerContext) -> None:
    """No near spelling to ask about: the read's ``name_unmatched`` refusal, naming the one name."""
    assert (
        player_comparison(ps_con, Reading(who=Who(kind="pair", players=("Luka Doncic", "Asdf Qwerty")))).text
        == "No player found matching 'Asdf Qwerty'. ESPN's box scores, which the warehouse's players come from, start with 1993-94, so a player whose career ended before then may not be on record."
    )


def test_player_comparison_needs_two_distinct_players(ps_con: AnswerContext) -> None:
    with pytest.raises(Unsupported):
        player_comparison(ps_con, Reading(who=Who(kind="player", players=("Luka Doncic",))))
    with pytest.raises(Unsupported):
        player_comparison(ps_con, Reading(who=Who(kind="player", players=("Luka Doncic",))))
    with pytest.raises(Unsupported):
        player_comparison(ps_con, Reading(who=Who(kind="player", players=("Luka Doncic",))))


def test_player_comparison_reports_a_player_with_no_rows_rather_than_dropping_them(ps_con: AnswerContext) -> None:
    ps_con.con.execute("INSERT INTO players VALUES ('9','Shai Gilgeous-Alexander')")
    result = player_comparison(ps_con, Reading(who=Who(kind="pair", players=("Luka Doncic", "Shai Gilgeous-Alexander"))))
    answer = result.text or ""
    assert "has no" in answer and "Shai Gilgeous-Alexander" in answer
    assert result.data["notes"] == [answer.splitlines()[-1]]
    assert result.data["headline"] == answer.splitlines()[0].rstrip(":")


def test_player_comparison_is_capped(ps_con: AnswerContext) -> None:
    from association.query.season_line import MAX_COMPARED_PLAYERS

    ps_con.con.execute("INSERT INTO players VALUES ('9','A A'),('10','B B'),('11','C C'),('12','D D')")
    result = player_comparison(ps_con, Reading(who=Who(kind="pair", players=("Luka Doncic", "Nikola Jokic", "A A", "B B", "C C", "D D"))))
    assert len(result.data["players"]) <= MAX_COMPARED_PLAYERS


def test_shot_chart_reads_threes_from_the_shot_value_or_the_stat(sc_ctx: AnswerContext) -> None:
    """ "Curry's threes" comes back as shot_value 3 or as the equivalent
    box-score stat depending on wording; both mean the same thing."""
    sc_ctx.con.execute("INSERT INTO shot_chart VALUES ('1',?,2,'e2',1,'9:00',TRUE,'Layup',5,5,2,'makes layup')", [current_season()])
    by_value = shot_chart(sc_ctx, Reading(who=Who(kind="player", players=("Stephen Curry",)), measure=Measure(shot_value=3))).text or ""
    by_stat = shot_chart(sc_ctx, Reading(who=Who(kind="player", players=("Stephen Curry",)), measure=Measure(key="threePointFieldGoalsMade", as_typed="threePointFieldGoalsMade"))).text or ""
    assert "1/2 made" in by_value and "1/2 made" in by_stat


def test_player_ranking_includes_requested_extra_fields(lb_con: AnswerContext) -> None:
    """ "top N in X alongside their points per game" answers both halves."""
    lb_con.con.execute("ALTER TABLE player_season_stats ADD COLUMN avgRebounds DOUBLE")
    lb_con.con.execute("UPDATE player_season_stats SET avgRebounds = 7.7 WHERE athlete_id = '1'")
    result = player_ranking(lb_con, Reading(measure=Measure(key="points", as_typed="points", beside=("rebounds",))))
    assert result.data["fields"] == ["rebounds"]
    assert "rebounds" in (result.text or "") and "7.7" in (result.text or "")


def test_player_ranking_with_fields_renders_a_table(lb_con: AnswerContext) -> None:
    lb_con.con.execute("ALTER TABLE player_season_stats ADD COLUMN avgRebounds DOUBLE")
    answer = player_ranking(lb_con, Reading(measure=Measure(key="points", as_typed="points", beside=("rebounds",)))).text or ""
    assert len(answer.splitlines()) >= 3


def test_player_ranking_without_fields_stays_a_sentence(lb_con: AnswerContext) -> None:
    answer = player_ranking(lb_con, Reading(measure=Measure(key="points", as_typed="points"))).text or ""
    assert len(answer.splitlines()) == 1 and "led the league" in answer


def test_player_ranking_with_an_unknown_field_is_declined_rather_than_dropping_it(lb_con: AnswerContext) -> None:
    # Silently ignoring it would answer a narrower question than was asked.
    with pytest.raises(Unsupported):
        player_ranking(lb_con, Reading(measure=Measure(key="points", as_typed="points", beside=("clutchness",))))


def test_player_ranking_table_keeps_the_metrics_own_precision(lb_con: AnswerContext) -> None:
    lb_con.con.execute("ALTER TABLE player_season_stats ADD COLUMN avgRebounds DOUBLE")
    lb_con.con.execute("UPDATE player_season_stats SET avgPoints = 9.91 WHERE athlete_id = '1'")
    answer = player_ranking(lb_con, Reading(measure=Measure(key="points", as_typed="points", beside=("rebounds",)))).text or ""
    assert "9.91" in answer  # not rounded to 9.9 by the table's field formatting


def test_player_ranking_table_names_the_qualifying_minimum(lb_con: AnswerContext) -> None:
    """ "Why isn't X on this list?" should have a visible answer."""
    lb_con.con.execute("ALTER TABLE player_season_stats ADD COLUMN avgRebounds DOUBLE")
    answer = player_ranking(lb_con, Reading(measure=Measure(key="points", as_typed="points", beside=("rebounds",)), window=Window(count=2))).text or ""
    assert "minimum" not in answer or "games" in answer or "minutes" in answer


def test_player_ranking_tolerates_a_repeated_field(lb_con: AnswerContext) -> None:
    """A repeated field (["points","minutes","minutes"]) is a harmless slip,
    read once."""
    lb_con.con.execute("ALTER TABLE player_season_stats ADD COLUMN avgRebounds DOUBLE")
    result = player_ranking(lb_con, Reading(measure=Measure(key="points", as_typed="points", beside=("rebounds", "rebounds"))))
    assert result.data["fields"] == ["rebounds"]
    assert (result.text or "").splitlines()[1].count("rebounds") == 1


def test_player_ranking_drops_a_field_that_restates_the_ranked_metric(lb_con: AnswerContext) -> None:
    """ "top scorers with their rebounds" with "points" among the fields shows
    the ranked metric once, never the same 33.5 twice under two headings."""
    lb_con.con.execute("ALTER TABLE player_season_stats ADD COLUMN avgRebounds DOUBLE")
    result = player_ranking(lb_con, Reading(measure=Measure(key="points", as_typed="points", beside=("rebounds", "points"))))
    assert result.data["fields"] == ["rebounds"]


# ---------------- a game's high ----------------


@pytest.fixture
def sgh_ctx(tmp_path: Path) -> AnswerContext:
    c = duckdb.connect(":memory:")
    # `minutes` is last so the positional INSERTs below (and in the tests that
    # add a row of their own) keep their order. A NULL there is the empty line
    # ESPN leaves, which a game's high must not read as a game played.
    c.execute(
        "CREATE TABLE player_game_log_rows (athlete_id VARCHAR, season INTEGER, season_type INTEGER, player_name VARCHAR, "
        "game_date VARCHAR, opponent_abbr VARCHAR, assists INTEGER, points INTEGER, minutes INTEGER, event_id VARCHAR, did_not_play BOOLEAN DEFAULT FALSE)"
    )
    c.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    c.execute("INSERT INTO players VALUES ('1','Ryan Nembhard'),('2','Nikola Jokic')")
    # Read only to count empty box scores; none here.
    c.execute("CREATE TABLE player_box_stats (event_id VARCHAR, season INTEGER, season_type INTEGER, athlete_id VARCHAR, minutes INTEGER, did_not_play BOOLEAN)")
    s = current_season()
    c.executemany(
        "INSERT INTO player_game_log_rows VALUES (?,?,?,?,?,?,?,?,?,?,FALSE)",
        [
            ("1", s, 2, "Ryan Nembhard", "2026-04-13T00:30Z", "CHI", 23, 8, 30, "e1"),
            ("2", s, 2, "Nikola Jokic", "2026-03-26T02:00Z", "DAL", 19, 30, 34, "e2"),
            ("2", s, 2, "Nikola Jokic", "2026-01-02T02:00Z", "UTA", 11, 40, 36, "e3"),
            ("1", s, 3, "Ryan Nembhard", "2026-05-01T00:30Z", "BOS", 30, 5, 31, "e4"),
            ("2", s - 1, 2, "Nikola Jokic", "2025-03-26T02:00Z", "DAL", 25, 30, 33, "e5"),
        ],
    )
    # The warehouse log carries each row's team, which the compiler reads for
    # the home side and the winner; the rows are a table the tests add to.
    c.execute("CREATE VIEW player_game_log AS SELECT *, NULL::VARCHAR AS team_id, NULL::INTEGER AS rebounds FROM player_game_log_rows")
    # The date, home side and winner the compiler reads off `games` beside the
    # log (compose.core._row_select), as the warehouse view carries them.
    c.execute("CREATE VIEW games AS SELECT DISTINCT event_id, season, season_type, game_date AS date, NULL::VARCHAR AS home_team_id, NULL::VARCHAR AS winner_team_id FROM player_game_log")
    return AnswerContext(con=c, out_dir=tmp_path)


def test_game_highs_answer_the_most_in_one_game_not_a_per_game_leader(sgh_ctx: AnswerContext) -> None:
    """ "who had the most assists in a single game" is the most in one game -
    Ryan Nembhard with 23 - never the league's leader in assists per game
    (Nikola Jokic, at 10.7)."""
    answer = game_highs(sgh_ctx, Reading(measure=Measure(key="assists", as_typed="assists"))).text or ""
    assert answer.startswith("Ryan Nembhard had the most assists in a single game")
    # Stored as 2026-04-13T00:30Z: 8:30pm Eastern on the 12th, the day it was played.
    assert "23" in answer and "2026-04-12" in answer and "CHI" in answer


def test_game_highs_is_a_maximum_not_an_average(sgh_ctx: AnswerContext) -> None:
    games = game_highs(sgh_ctx, Reading(measure=Measure(key="assists", as_typed="assists"))).data["games"]
    assert games[0]["value"] == 23  # not Jokic's 15.0 average across his two games


def test_game_highs_defaults_to_the_regular_season(sgh_ctx: AnswerContext) -> None:
    # Nembhard's 30-assist game is postseason and must not win the default.
    assert game_highs(sgh_ctx, Reading(measure=Measure(key="assists", as_typed="assists"))).data["games"][0]["value"] == 23
    assert game_highs(sgh_ctx, Reading(measure=Measure(key="assists", as_typed="assists"), span=Span(season_type=3))).data["games"][0]["value"] == 30


def test_game_highs_defaults_to_the_current_season(sgh_ctx: AnswerContext) -> None:
    assert game_highs(sgh_ctx, Reading(measure=Measure(key="assists", as_typed="assists"))).data["season"] == current_season()
    assert game_highs(sgh_ctx, Reading(measure=Measure(key="assists", as_typed="assists"), span=Span(season=(current_season() - 1)))).data["games"][0]["value"] == 25


def test_game_highs_for_a_named_player(sgh_ctx: AnswerContext) -> None:
    answer = game_highs(sgh_ctx, Reading(who=Who(kind="player", players=("Nikola Jokic",)), measure=Measure(key="assists", as_typed="assists"))).text or ""
    assert answer == f"Nikola Jokic's highest assist total in a single game in the {current_season()} regular season was 19, on 2026-03-25 vs DAL."


def test_a_tie_within_one_game_is_listed_in_one_order(sgh_ctx: AnswerContext) -> None:
    """ISSUES.md #99: a league-wide list is ordered by the stat, the date, the
    game and then the player, so a list cut at two players tied in ONE game
    is the same every run."""
    s = current_season()
    sgh_ctx.con.execute("INSERT INTO players VALUES ('9', 'Zed Tie'), ('8', 'Abe Tie')")
    sgh_ctx.con.executemany(
        "INSERT INTO player_game_log_rows VALUES (?,?,?,?,?,?,?,?,?,?,FALSE)",
        [("9", s, 2, "Zed Tie", "2026-02-02T02:00Z", "MIA", 1, 40, 30, "e9"), ("8", s, 2, "Abe Tie", "2026-02-02T02:00Z", "MIA", 1, 40, 30, "e9")],
    )
    result = game_highs(sgh_ctx, Reading(measure=Measure(key="points", as_typed="points")))
    # Jokic's 40 came first (2026-01-02); the two of one game follow by athlete id.
    assert [g["player"] for g in result.data["games"][:3]] == ["Nikola Jokic", "Abe Tie", "Zed Tie"]
    assert result.text.startswith("Nikola Jokic, Abe Tie and Zed Tie tied for the most points in a single game")


def test_the_leagues_high_keeps_a_tie_past_its_limit(sgh_ctx: AnswerContext) -> None:
    """ISSUES.md #476: the league's high lists three games, and a fourth
    tied with the third was cut, the three said as the holders. Four
    players' games at 40 are all said as tied, and the next figure after."""
    s = current_season()
    sgh_ctx.con.execute("INSERT INTO players VALUES ('9', 'Zed Tie'), ('8', 'Abe Tie'), ('7', 'Cy Tie')")
    sgh_ctx.con.executemany(
        "INSERT INTO player_game_log_rows VALUES (?,?,?,?,?,?,?,?,?,?,FALSE)",
        [
            ("9", s, 2, "Zed Tie", "2026-02-02T02:00Z", "MIA", 1, 40, 30, "e9"),
            ("8", s, 2, "Abe Tie", "2026-02-04T02:00Z", "MIA", 1, 40, 30, "e8"),
            ("7", s, 2, "Cy Tie", "2026-02-06T02:00Z", "MIA", 1, 40, 30, "e7"),
        ],
    )
    result = game_highs(sgh_ctx, Reading(measure=Measure(key="points", as_typed="points")))
    assert [g["value"] for g in result.data["games"]] == [40, 40, 40, 40]
    assert result.text.startswith("Nikola Jokic, Zed Tie, Abe Tie and Cy Tie tied for the most points in a single game")


def test_a_named_players_top_games_keep_a_tie_at_the_cut(sgh_ctx: AnswerContext) -> None:
    """ISSUES.md #476's player half ("chris paul career high as a spur"): a
    named player's games ranked by a figure keep the game tied with the last
    one kept - his top two by points here, 40 and the two 30s."""
    q = replace(planned_query(GAME_HIGHS, Reading(who=Who(kind="player", players=("Nikola Jokic",)), measure=Measure(key="points", as_typed="points"), span=Span(career=True, season_type=2))), limit=2)
    rows = rows_of(sgh_ctx.con, compile_query(sgh_ctx.con, q))
    assert [row["points"] for row in rows] == [40, 30, 30]


def test_game_highs_reports_a_tie_as_a_tie(sgh_ctx: AnswerContext) -> None:
    sgh_ctx.con.execute("UPDATE player_game_log_rows SET assists = 23 WHERE player_name = 'Nikola Jokic' AND opponent_abbr = 'DAL' AND season_type = 2")
    assert "tied for the most" in (game_highs(sgh_ctx, Reading(measure=Measure(key="assists", as_typed="assists"))).text or "")


def test_game_highs_refuses_an_unknown_stat_before_any_sql(sgh_ctx: AnswerContext) -> None:
    """A stat nothing ranks is refused by name - the compiler's own refusal -
    and never reaches SQL: the players table is still there to count."""
    result = game_highs(sgh_ctx, Reading(measure=Measure(as_typed="assists); DROP TABLE players; --")))
    assert "No ranking reads" in (result.text or "")
    assert sgh_ctx.con.execute("SELECT COUNT(*) FROM players").fetchone() == (2,)


def test_game_highs_ambiguous_player_asks(sgh_ctx: AnswerContext) -> None:
    # Jovic needs a game this season to be a candidate at all. A name is
    # narrowed to the players with a row where the answer is read from, and
    # with Jokic alone in this season's log, "Nikola" is answered about him.
    sgh_ctx.con.execute("INSERT INTO players VALUES ('3','Nikola Jovic')")
    sgh_ctx.con.execute("INSERT INTO player_game_log_rows VALUES ('3',?,2,'Nikola Jovic','2026-02-01T00:30Z','BOS',4,12,26,'e9',FALSE)", [current_season()])
    assert "did you mean" in (game_highs(sgh_ctx, Reading(who=Who(kind="player", players=("Nikola",)), measure=Measure(key="assists", as_typed="assists"))).text or "")


@pytest.fixture
def rebuilt_ctx(tmp_path: Path) -> AnswerContext:
    """A season where ESPN served some box scores and left others empty, with
    the empty ones rebuilt from play-by-play. `reconstructed` marks those, and
    a rebuilt line has NULL minutes because play-by-play cannot recover them."""
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    c.execute("INSERT INTO players VALUES ('1','Anthony Davis')")
    # `event_id` and `did_not_play` reach the real view through `pbs.*`, and
    # _empty_box_scores reads both. A fixture without them passes a thinner
    # table than any warehouse ever holds, and hides a column dependency.
    c.execute(
        "CREATE TABLE player_game_log_rows (athlete_id VARCHAR, season INTEGER, season_type INTEGER, player_name VARCHAR, "
        "game_date VARCHAR, opponent_abbr VARCHAR, points INTEGER, fouls INTEGER, minutes INTEGER, reconstructed BOOLEAN, "
        "event_id VARCHAR, did_not_play BOOLEAN)"
    )
    c.execute("CREATE TABLE player_box_stats (event_id VARCHAR, season INTEGER, season_type INTEGER, athlete_id VARCHAR, minutes INTEGER, did_not_play BOOLEAN)")
    s = current_season()
    c.executemany(
        "INSERT INTO player_game_log_rows VALUES ('1',?,2,'Anthony Davis',?,?,?,?,?,?,?,FALSE)",
        [
            # Fetched: real box scores, real minutes.
            (s, "2026-01-02T00:30Z", "ORL", 24, 3, 31, False, "e1"),
            (s, "2026-01-05T00:30Z", "DAL", 18, 5, 28, False, "e2"),
            # Rebuilt: no box score, figures from the plays, no minutes.
            (s, "2026-01-08T00:30Z", "MIA", 43, 1, None, True, "e3"),
            (s, "2026-01-11T00:30Z", "PHX", 12, 6, None, True, "e4"),
        ],
    )
    # The warehouse log carries each row's team, which the compiler reads for
    # the home side and the winner; the rows are a table the tests add to.
    c.execute("CREATE VIEW player_game_log AS SELECT *, NULL::VARCHAR AS team_id, NULL::INTEGER AS rebounds FROM player_game_log_rows")
    # The date, home side and winner the compiler reads off `games` beside the
    # log (compose.core._row_select), as the warehouse view carries them.
    c.execute("CREATE VIEW games AS SELECT DISTINCT event_id, season, season_type, game_date AS date, NULL::VARCHAR AS home_team_id, NULL::VARCHAR AS winner_team_id FROM player_game_log")
    # The warehouse builds the log over the filled box view; the relation
    # learns rebuilt lines exist from it (conditions.box_source).
    c.execute("CREATE VIEW player_box_stats_filled AS SELECT * FROM player_game_log")
    return AnswerContext(con=c, out_dir=tmp_path)


def test_a_rebuilt_game_can_win_a_game_high(rebuilt_ctx: AnswerContext) -> None:
    """A season ESPN served empty has a per-game answer from the rebuild. 43
    beats every fetched game here."""
    answer = game_highs(rebuilt_ctx, Reading(who=Who(kind="player", players=("Anthony Davis",)), measure=Measure(key="points", as_typed="points"))).text or ""
    assert "43" in answer


def test_a_rebuilt_answer_says_it_was_rebuilt(rebuilt_ctx: AnswerContext) -> None:
    """A figure that did not come from ESPN has to say so, or it reads as a box
    score. This is the disclosure the whole opt-in is conditional on."""
    answer = game_highs(rebuilt_ctx, Reading(who=Who(kind="player", players=("Anthony Davis",)), measure=Measure(key="points", as_typed="points"))).text or ""
    assert "rebuilt from its play-by-play" in answer


def test_a_fetched_winner_says_nothing_about_rebuilding(rebuilt_ctx: AnswerContext) -> None:
    """The note is about the number given, not about what was read. Drop the
    rebuilt games below the fetched ones and the answer is an ordinary one."""
    rebuilt_ctx.con.execute("UPDATE player_game_log_rows SET points = 5 WHERE reconstructed")
    answer = game_highs(rebuilt_ctx, Reading(who=Who(kind="player", players=("Anthony Davis",)), measure=Measure(key="points", as_typed="points"))).text or ""
    assert "24" in answer
    assert "rebuilt" not in answer


def test_fouls_are_never_read_from_a_rebuilt_line(rebuilt_ctx: AnswerContext) -> None:
    """A rebuilt foul is wrong in one game in six (83.3% exact against 98.3% for
    points), so fouls are outside REBUILT_STATS. The rebuilt 6 must lose to the
    fetched 5 rather than win the answer."""
    answer = game_highs(rebuilt_ctx, Reading(who=Who(kind="player", players=("Anthony Davis",)), measure=Measure(key="fouls", as_typed="fouls"))).text or ""
    assert "5" in answer
    assert "rebuilt from its play-by-play" not in answer


def test_a_withheld_stat_says_it_was_withheld_not_missing(rebuilt_ctx: AnswerContext) -> None:
    """When every fetched line is gone and only rebuilt ones remain, asking for
    a stat outside REBUILT_STATS must name the decision. "No games with a box
    score" is true of the fetched lines and hides that the data exists and was
    held back for being too inaccurate to quote."""
    rebuilt_ctx.con.execute("DELETE FROM player_game_log_rows WHERE NOT reconstructed")
    answer = game_highs(rebuilt_ctx, Reading(who=Who(kind="player", players=("Anthony Davis",)), measure=Measure(key="fouls", as_typed="fouls"))).text or ""
    assert "were rebuilt from play-by-play" in answer
    assert "not read from a rebuilt line" in answer


def test_the_unseen_count_excludes_games_the_rebuild_answered(rebuilt_ctx: AnswerContext) -> None:
    """The answer never gives a rebuilt figure and then reports the same
    games as ones it could not see."""
    # An empty box row for a game the rebuild DID answer. Counting from
    # player_box_stats would make this one "unseen"; counting from the log,
    # which knows it was rebuilt, makes it nothing.
    rebuilt_ctx.con.execute("INSERT INTO player_box_stats VALUES ('e3', ?, 2, '1', NULL, FALSE)", [current_season()])
    answer = game_highs(rebuilt_ctx, Reading(who=Who(kind="player", players=("Anthony Davis",)), measure=Measure(key="points", as_typed="points"))).text or ""
    assert "43" in answer
    assert "rebuilt from its play-by-play" in answer
    # The discriminating assertion: counting from player_box_stats appends "1
    # of Anthony Davis's games ... has an empty box score ...", disclaiming the
    # very game the 43 came from.
    assert "empty box score" not in answer


def test_a_game_log_reads_rebuilt_lines_only_for_stats_a_rebuild_gets_right(rebuilt_ctx: AnswerContext) -> None:
    """The gate a listing applies before showing a rebuilt row. A column a
    rebuild gets wrong is never shown from one: a log in date order lists the
    games, so it widens and prints that column blank on a rebuilt row
    (``measure_sql``'s blanking, the rebuilt games' note naming it - the full
    box line of ISSUES.md #471 would otherwise hide the Pelicans' 2018 behind
    "the box score is empty"); a listing ordered by a measure would sort a
    blank, so one such column sends it back to fetched lines. Asserted
    directly on the rule, because inline in the query it could be relaxed
    with nothing noticing. The rule is the compiler's (``core._rebuilt_for``)
    since the log executes the compiled statement, over the columns the log
    shows as the point's measures."""
    from association.query.compose.core import Query, _rebuilt_for
    from association.query.compose.logs import LOG_COLUMNS
    from association.query.conditions import box_source

    box = box_source(rebuilt_ctx.con)
    assert box.rebuilt

    def readable(headers: list[str], order: str = "date") -> bool:
        point = Query(**(Reading(who=Who(kind="player", players=("x",)))).filters(), skeleton="rows", order=order, measures=[LOG_COLUMNS[h] for h in headers])
        return _rebuilt_for(box, point)

    assert readable(["MIN", "PTS", "REB", "AST"]) is True
    assert readable(["MIN", "PTS", "STL", "BLK"]) is True
    # 0.181 mean error a game, wrong in one game in six: blank on a dated
    # log's rebuilt row, never sorted by.
    assert readable(["MIN", "PTS", "PF"]) is True
    assert readable(["PF", "PTS"], order="measure") is False
    # 0.080, wrong in one game in thirteen.
    assert readable(["MIN", "PTS", "TO"]) is True
    assert readable(["TO", "PTS"], order="measure") is False
    # A log of a column the rebuild never fills alone is no rebuilt row's.
    assert readable(["MIN", "TO"]) is False


def test_a_yes_no_stat_of_columns_a_rebuild_gets_right_reads_a_rebuilt_line(rebuilt_ctx: AnswerContext) -> None:
    """A double- or triple-double is made of points, rebounds, assists,
    steals and blocks, each a column a rebuild gets right: read on a rebuilt
    line as its columns are. Excluded, "how many triple-doubles did lebron
    james achieve in the regular season" counted 122 where the key reads
    125, and Clint Capela's rebuilt 18-point, 16-rebound game of 2018-03-24
    was no double-double. A column the rebuild never fills beside it still
    keeps the read to fetched lines."""
    from association.query.compose.core import Query, _rebuilt_for
    from association.query.conditions import box_source

    box = box_source(rebuilt_ctx.con)
    assert box.rebuilt

    def readable(measures: list[str], predicates: list[tuple[str, str, object]], skeleton: str = "scalar") -> bool:
        point = Query(**(Reading(who=Who(kind="player", players=("x",)))).filters(), skeleton=skeleton, order="date", measures=measures, predicates=predicates)
        return _rebuilt_for(box, point)

    assert readable([], [("triple_double", "=", True)]) is True
    assert readable(["points", "rebounds", "assists"], [("double_double", "=", True)], skeleton="rows") is True
    assert readable(["turnovers"], [("double_double", "=", True)]) is False


def test_a_warehouse_without_the_flag_still_answers(rebuilt_ctx: AnswerContext) -> None:
    """The column arrives with a `data load`. An older warehouse has no such
    column, and the query must not be written as though it were always there -
    that is the Binder error AGENTS.md records for view changes."""
    rebuilt_ctx.con.execute("ALTER TABLE player_game_log_rows DROP COLUMN reconstructed")
    answer = game_highs(rebuilt_ctx, Reading(who=Who(kind="player", players=("Anthony Davis",)), measure=Measure(key="points", as_typed="points"))).text or ""
    assert "24" in answer  # the best line that has minutes
    assert "rebuilt" not in answer


def test_game_highs_reports_an_empty_season_honestly(sgh_ctx: AnswerContext) -> None:
    assert "no 1999 regular season games" in (game_highs(sgh_ctx, Reading(measure=Measure(key="assists", as_typed="assists"), span=Span(season=1999))).text or "")


def test_game_highs_defaulted_season_redirects_to_a_retired_players_range(sgh_ctx: AnswerContext) -> None:
    """No season was named - the question asked about "now" - so a player
    with games only long before it gets pointed at his own range rather than a
    refusal that reads as though he never played at all (issue #18)."""
    s, past = current_season(), current_season() - 16
    sgh_ctx.con.execute("INSERT INTO players VALUES ('3','Old Timer')")
    sgh_ctx.con.execute("INSERT INTO player_game_log_rows VALUES ('3',?,2,'Old Timer','2010-04-12T00:30Z','BOS',5,20,32,'e8',FALSE)", [past])
    result = game_highs(sgh_ctx, Reading(who=Who(kind="player", players=("Old Timer",)), measure=Measure(key="points", as_typed="points")))
    answer = result.text or ""
    assert answer == (f"Old Timer has no {s} regular season games in the warehouse. He last appears in {past}. The warehouse holds his {past} regular season; name one, or ask for his career.")
    # The redirect ("He last appears in ...") is not part of the headline -
    # it is glued onto the same sentence, but it answers a second question
    # (where else to look) the headline itself does not ask.
    assert result.data["headline"] == f"Old Timer has no {s} regular season games in the warehouse."
    # ... but it is not lost either - a game's high's own caption is
    # `question_shape`, never the raw text, so a page reading `notes` (rather
    # than parsing text, which would see one unbroken line here and find
    # nothing after it) still needs the redirect somewhere.
    assert result.data["notes"] == [f"He last appears in {past}. The warehouse holds his {past} regular season; name one, or ask for his career."]


def test_game_highs_a_named_season_keeps_the_plain_refusal(sgh_ctx: AnswerContext) -> None:
    """The season the question named is the fact the refusal is about; a
    redirect there would answer a season nobody asked about."""
    past = current_season() - 16
    sgh_ctx.con.execute("INSERT INTO players VALUES ('3','Old Timer')")
    sgh_ctx.con.execute("INSERT INTO player_game_log_rows VALUES ('3',?,2,'Old Timer','2010-04-12T00:30Z','BOS',5,20,32,'e8',FALSE)", [past])
    answer = game_highs(sgh_ctx, Reading(who=Who(kind="player", players=("Old Timer",)), measure=Measure(key="points", as_typed="points"), span=Span(season=1999))).text or ""
    assert answer == "Old Timer has no 1999 regular season games in the warehouse."


def _all_box_scores_empty(ctx: AnswerContext, name: str) -> None:
    """One player whose whole season is the empty line ESPN leaves: listed as
    having played, no minutes, every stat 0. Anthony Davis's 2015 in miniature."""
    s = current_season()
    ctx.con.execute("INSERT INTO players VALUES ('9',?)", [name])
    ctx.con.executemany(
        "INSERT INTO player_game_log_rows VALUES ('9',?,2,?,?,'ORL',0,0,NULL,?,FALSE)",
        [(s, name, "2026-01-05T00:30Z", "x1"), (s, name, "2026-01-08T00:30Z", "x2")],
    )
    ctx.con.executemany("INSERT INTO player_box_stats VALUES (?,?,2,'9',NULL,FALSE)", [("x1", s), ("x2", s)])


def test_a_zero_from_an_empty_box_score_never_wins_a_game_high(sgh_ctx: AnswerContext) -> None:
    """Every Chicago and New Orleans box score from 2013 to 2018 is zeros, so
    a maximum over them that read the empty lines would be one of those
    zeros, reported with a date: "Anthony Davis's highest point total in a
    single game in the 2015 regular season was 0, on 2014-10-28 vs ORL."
    Fluent, specific and false - the guard reads no empty line."""
    _all_box_scores_empty(sgh_ctx, "Zion Williamson")
    answer = game_highs(sgh_ctx, Reading(who=Who(kind="player", players=("Zion Williamson",)), measure=Measure(key="points", as_typed="points"))).text or ""
    assert "was 0" not in answer
    assert f"no {current_season()} regular season games with a box score" in answer


def test_an_empty_box_score_season_says_which_fact_is_missing(sgh_ctx: AnswerContext) -> None:
    """The mirror image the guard must not introduce: "he has no games"
    is false of a player who played them, and sends the reader to look for a
    missing season rather than a missing box score. The count and the years
    have to be in the sentence."""
    _all_box_scores_empty(sgh_ctx, "Zion Williamson")
    answer = game_highs(sgh_ctx, Reading(who=Who(kind="player", players=("Zion Williamson",)), measure=Measure(key="points", as_typed="points"))).text or ""
    # The bare sentence must NOT appear. This is the assertion that carries the
    # test: the count and the years below come from the empty-box note, which
    # runs either way, so asserting only those would pass with this branch
    # blinded.
    assert f"no {current_season()} regular season games in the warehouse" not in answer
    assert f"no {current_season()} regular season games with a box score in the warehouse" in answer
    assert "2 of Zion Williamson's games" in answer
    # Said of a season with no games at all, the plain sentence is still right.
    plain = game_highs(sgh_ctx, Reading(measure=Measure(key="assists", as_typed="assists"), span=Span(season=1999))).text or ""
    assert "no 1999 regular season games in the warehouse" in plain
    assert "with a box score" not in plain


# ---------------- two teams' meetings ----------------


def test_team_meetings_counts_games_in_both_directions(gl_con: AnswerContext) -> None:
    """`games` is home/away-oriented, and a team id is never compared to an
    abbreviation: two teams who met home and away met twice."""
    result = team_meetings(gl_con, Reading(who=Who(kind="teams", teams=("Knicks", "Celtics")), span=Span(season=current_season())))
    assert result.data["games"] == 2  # one home, one away


def test_team_meetings_keeps_the_window_of_newest_meetings(gl_con: AnswerContext) -> None:
    """The yardstick's F108, "lakers vs mavs record last 10 home games
    played": a window on two teams' meetings is the last N of them, sliced
    from the one read and said as such."""
    from association.query.reading import Window as AskedWindow

    result = team_meetings(gl_con, Reading(who=Who(kind="teams", teams=("Knicks", "Celtics")), span=Span(season=current_season()), window=AskedWindow(count=1, order="recent")))
    assert result.data["games"] == 1
    assert "their last 1 of 2 meetings" in result.text


def test_a_stretch_of_team_meetings_reads_both_season_types(gl_con: AnswerContext) -> None:
    """Decision D14: the last N meetings are the last N the two played, the
    playoffs included ("spurs record vs thunder last 16 games" holds the
    2026 West finals) - and the regular season alone where the span reads
    one type."""
    from association.query.reading import Window as AskedWindow

    s = current_season()
    gl_con.con.execute("INSERT INTO games VALUES ('p1',?,3,'2026-05-01T23:00Z','18','2',101,99,'18',false,'New York')", [s])
    gl_con.con.execute("INSERT INTO team_box_stats VALUES ('p1',?,3,'18','2','home')", [s])
    real_games.build_table(gl_con.con, {"games", "teams"})
    teams = Who(kind="teams", teams=("Knicks", "Celtics"))
    both = team_meetings(gl_con, Reading(who=teams, span=Span(season=s, both=True), window=AskedWindow(count=2, order="recent")))
    assert both.data["games"] == 2 and "their last 2 of 3 meetings" in both.text
    regular = team_meetings(gl_con, Reading(who=teams, span=Span(season=s), window=AskedWindow(count=2, order="recent")))
    assert regular.data["games"] == 2 and "their last 2 meetings in the 2026 regular season" in regular.text


def test_team_meetings_counts_only_rows_that_are_games(gl_con: AnswerContext) -> None:
    """Only rows that are games count: "how many times did the Mavs play the
    76ers in 2003" is 2, never 3 for one game stored under two event ids, and
    a 1999-2000 matchup's 0-0 placeholders nobody won are no meetings. Both
    shapes are in the fixture; read from `games` this is 4."""
    assert team_meetings(gl_con, Reading(who=Who(kind="teams", teams=("Knicks", "Celtics")), span=Span(season=current_season()))).data["games"] == 2


def test_a_team_log_leaves_out_rows_that_are_not_games(gl_con: AnswerContext) -> None:
    """The same two rows, through the team game log. Joining team_box_stats
    filters nothing - EVERY `games` row has a team_box_stats row - so the log
    leaves the placeholders out by itself."""
    games = player_log(gl_con, Reading(who=Who(kind="team", teams=("Knicks",)))).data["games"]
    assert len(games) == 2
    assert all(g["opponent_score"] is not None and g["won"] is not None for g in games)


def test_team_meetings_reports_the_series_record(gl_con: AnswerContext) -> None:
    result = team_meetings(gl_con, Reading(who=Who(kind="teams", teams=("Knicks", "Celtics")), span=Span(season=current_season())))
    answer = result.text or ""
    assert "met 2 times" in answer and "splitting them 1-1" in answer
    assert result.data["headline"] == answer  # one sentence, no table beneath it


def test_team_meetings_applies_the_season_to_the_whole_matchup(gl_con: AnswerContext) -> None:
    # `A OR B AND season = ...` binds the season to one side only; the read
    # parenthesizes the matchup so the filter covers both orderings.
    assert team_meetings(gl_con, Reading(who=Who(kind="teams", teams=("Knicks", "Celtics")), span=Span(season=1999))).data["games"] == 0


def test_team_meetings_reports_no_meetings_honestly(gl_con: AnswerContext) -> None:
    assert "no" in (team_meetings(gl_con, Reading(who=Who(kind="teams", teams=("Knicks", "Celtics")), span=Span(season=1999))).text or "").lower()


def test_team_meetings_defaults_to_the_current_season(gl_con: AnswerContext) -> None:
    """Not all-time: answering a different span than every other reader,
    silently, is the substitution this design exists to prevent."""
    gl_con.con.execute("INSERT INTO games VALUES ('e9',?,2,'2020-01-01T00:00Z','18','2',100,90,'18',false,'New York')", [current_season() - 3])
    result = team_meetings(gl_con, Reading(who=Who(kind="teams", teams=("Knicks", "Celtics"))))
    assert result.data["games"] == 2
    assert f"{current_season()} regular season" in (result.text or "")


def test_team_meetings_needs_two_distinct_teams(gl_con: AnswerContext) -> None:
    with pytest.raises(Unsupported):
        team_meetings(gl_con, Reading(who=Who(kind="team", teams=("Knicks",))))
    with pytest.raises(Unsupported):
        team_meetings(gl_con, Reading(who=Who(kind="teams", teams=("Knicks", "New York Knicks"))))


def test_team_meetings_asks_on_an_ambiguous_team(gl_con: AnswerContext) -> None:
    gl_con.con.execute("INSERT INTO teams VALUES ('12','LAC','LA Clippers'),('13','LAL','Los Angeles Lakers')")
    assert "did you mean" in (team_meetings(gl_con, Reading(who=Who(kind="teams", teams=("LA", "Celtics")))).text or "")


def test_team_meetings_narrows_to_the_first_named_teams_home_games(gl_con: AnswerContext) -> None:
    """ "lakers vs mavs record last 10 home games played": the fixture's two
    meetings split one home, one away for the Knicks (named first); "home"
    keeps only their home game."""
    result = team_meetings(gl_con, Reading(who=Who(kind="teams", teams=("Knicks", "Celtics")), cuts=Cuts(venue="home"), span=Span(season=current_season())))
    assert result.data["games"] == 1
    assert "New York Knicks" in (result.text or "") and "home games" in (result.text or "")


def test_team_meetings_venue_possessive_drops_the_extra_s(gl_con: AnswerContext) -> None:
    """ "New York Knicks's" reads as a typo - most team names already end in
    "s" (Celtics, Warriors, Nets...), so the possessive is just an apostrophe."""
    answer = team_meetings(gl_con, Reading(who=Who(kind="teams", teams=("Knicks", "Celtics")), cuts=Cuts(venue="home"), span=Span(season=current_season()))).text or ""
    assert "New York Knicks' home games" in answer
    assert "Knicks's" not in answer


def test_team_meetings_narrows_to_the_first_named_teams_road_games(gl_con: AnswerContext) -> None:
    result = team_meetings(gl_con, Reading(who=Who(kind="teams", teams=("Knicks", "Celtics")), cuts=Cuts(venue="away"), span=Span(season=current_season())))
    games = result.data["games"]
    assert games == 1
    # The Knicks' one road game in the fixture (2026-04-12) was a loss.
    assert result.data["wins"]["New York Knicks"] == 0


def test_team_meetings_venue_is_said_in_the_answer_not_silently_applied(gl_con: AnswerContext) -> None:
    """Honoring a cell means filtering by it AND saying so - a table
    with fewer rows and no note reads exactly like the whole-season answer."""
    answer = team_meetings(gl_con, Reading(who=Who(kind="teams", teams=("Knicks", "Celtics")), cuts=Cuts(venue="home"), span=Span(season=current_season()))).text or ""
    assert "home games" in answer


def test_team_meetings_filters_an_exact_calendar_date(gl_con: AnswerContext) -> None:
    """ "celtics record vs sixers on november 11" - a date names its game
    outright, the same way it does for a player's log."""
    result = team_meetings(gl_con, Reading(who=Who(kind="teams", teams=("Knicks", "Celtics")), cuts=Cuts(date="2026-04-12")))
    assert result.data["games"] == 1
    assert "on 2026-04-12" in (result.text or "")


def test_team_meetings_date_is_not_scoped_to_a_named_season(gl_con: AnswerContext) -> None:
    """A date pins one exact game, so - like a log's own `date` - it is not
    additionally filtered to "the current season": a season from a year the
    game was not actually played in must not hide it."""
    result = team_meetings(gl_con, Reading(who=Who(kind="teams", teams=("Knicks", "Celtics")), cuts=Cuts(date="2026-04-12"), span=Span(season=1999)))
    assert result.data["games"] == 1


def test_team_meetings_no_game_on_that_date_is_reported_honestly(gl_con: AnswerContext) -> None:
    result = team_meetings(gl_con, Reading(who=Who(kind="teams", teams=("Knicks", "Celtics")), cuts=Cuts(date="2026-04-11")))
    assert result.data["games"] == 0
    assert "no games" in (result.text or "").lower()
    assert "on 2026-04-11" in (result.text or "")


def test_team_meetings_with_no_venue_or_date_say_the_series_record_alone(gl_con: AnswerContext) -> None:
    """With no venue and no date, the sentence is the series record alone."""
    answer = team_meetings(gl_con, Reading(who=Who(kind="teams", teams=("Knicks", "Celtics")), span=Span(season=current_season()))).text or ""
    assert answer == f"The New York Knicks and the Boston Celtics met 2 times in the {current_season()} regular season, splitting them 1-1."


def test_meetings_asked_with_a_stat_say_each_teams_figure(gl_con: AnswerContext) -> None:
    """ISSUES.md #326: "knicks vs celtics total points" answered the series
    record alone. A stat named beside two teams' meetings is said for each
    team over the games: points from the meetings' own scores, a box-score
    figure from each side's box row (a percentage over the makes and
    attempts summed), with a game a side has no box row for said."""
    s = current_season()
    asked = Reading(who=Who(kind="teams", teams=("Knicks", "Celtics")), span=Span(season=s), measure=Measure(key="points", as_typed="points"))
    points = team_meetings(gl_con, asked)
    assert (points.text or "").endswith("splitting them 1-1. Points: New York Knicks 208, 104.0 a game; Boston Celtics 205, 102.5 a game."), points.text
    assert points.data["figures"] == {"New York Knicks": {"figure": 208, "figure_games": 2}, "Boston Celtics": {"figure": 205, "figure_games": 2}}
    _gl_box(gl_con)
    threes = team_meetings(gl_con, replace(asked, measure=Measure(key="threePointFieldGoalPct", as_typed="3 point percentage")))
    assert (threes.text or "").endswith(
        "3-point percentage: New York Knicks 40.0% (12-30); Boston Celtics 33.8% (23-68). (1 of these games has no 3-point percentage on record; the figure is over the rest.)"
    ), threes.text
    # Neither side's box row holds the figure (the 2013-18 Bulls' empty box
    # scores served both rows blank): said once, not "none" for each.
    gl_con.con.execute("UPDATE team_box_stats SET offensiveRebounds = NULL, defensiveRebounds = NULL")
    rebounds = team_meetings(gl_con, replace(asked, measure=Measure(key="rebounds", as_typed="rebounds")))
    assert (rebounds.text or "").endswith("splitting them 1-1. Neither team has rebounds on record for these games."), rebounds.text


def test_meetings_asked_with_a_figure_no_game_carries_are_refused_by_name(gl_con: AnswerContext) -> None:
    """ISSUES.md #326: a figure no team game carries is named, not dropped."""
    pace = team_meetings(gl_con, Reading(who=Who(kind="teams", teams=("Knicks", "Celtics")), measure=Measure(key="pace", as_typed="pace")))
    assert (pace.text or "").startswith("A team's games carry no pace game by game")


# ---------------- a team's record and two teams' meetings: since, game_n, career ----------------

_TC_S = current_season()
_TC_S1 = _TC_S - 1
_TC_S2 = _TC_S - 2


@pytest.fixture
def team_cells_con(tmp_path: Path) -> AnswerContext:
    """Three regular seasons (S-2, S-1, S) of Celtics games against the
    Knicks and the Pistons, plus two playoff series - an early one (1989, the
    warehouse's own postseason floor) and a current-season one - each best
    checked by counting the rows below rather than guessed:

    ====== ====== ===== ========================= ==============
    event  season type  matchup                    result
    ====== ====== ===== ========================= ==============
    r1     S-2    reg   Celtics (home) - Knicks     Celtics win
    r2     S-2    reg   Pistons (home) - Celtics    Celtics loss
    r3     S-1    reg   Knicks (home) - Celtics     Celtics win
    r4     S      reg   Celtics (home) - Knicks     Celtics win
    r5     S      reg   Celtics (home) - Pistons    Celtics win
    p89_1  1989   post  Celtics (home) - Pistons    Celtics win  (game 1)
    p89_2  1989   post  Pistons (home) - Celtics    Celtics loss (game 2)
    p89_3  1989   post  Celtics (home) - Pistons    Celtics win  (game 3)
    pS_1   S      post  Celtics (home) - Knicks     Celtics win  (game 1)
    pS_2   S      post  Knicks (home) - Celtics     Celtics loss (game 2)
    pS_3   S      post  Celtics (home) - Knicks     Celtics win  (game 3)
    ====== ====== ===== ========================= ==============

    Celtics ``2``, Knicks ``18``, Pistons ``4``. ``team_season_stats`` carries
    exactly the game counts above, so ``_game_list_gaps`` finds no gap to
    caveat with and the answers asserted below are not diluted by a note
    nobody wrote a fixture for.
    """
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE teams (team_id VARCHAR, abbreviation VARCHAR, display_name VARCHAR)")
    c.execute("INSERT INTO teams VALUES ('2','BOS','Boston Celtics'),('18','NY','New York Knicks'),('4','DET','Detroit Pistons')")
    c.execute(
        "CREATE TABLE games (event_id VARCHAR, season INTEGER, season_type INTEGER, date VARCHAR, "
        "home_team_id VARCHAR, away_team_id VARCHAR, home_score INTEGER, away_score INTEGER, winner_team_id VARCHAR, neutral_site BOOLEAN, venue_city VARCHAR)"
    )
    c.executemany(
        "INSERT INTO games VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        [
            ("r1", _TC_S2, 2, f"{_TC_S2 - 1}-11-01T23:00Z", "2", "18", 100, 90, "2", False, "Boston"),
            ("r2", _TC_S2, 2, f"{_TC_S2 - 1}-11-05T23:00Z", "4", "2", 100, 90, "4", False, "Detroit"),
            ("r3", _TC_S1, 2, f"{_TC_S1 - 1}-11-10T23:00Z", "18", "2", 95, 105, "2", False, "New York"),
            ("r4", _TC_S, 2, f"{_TC_S - 1}-11-15T23:00Z", "2", "18", 110, 90, "2", False, "Boston"),
            ("r5", _TC_S, 2, f"{_TC_S - 1}-11-20T23:00Z", "2", "4", 120, 100, "2", False, "Boston"),
            ("p89_1", 1989, 3, "1989-04-20T23:00Z", "2", "4", 100, 95, "2", False, "Boston"),
            ("p89_2", 1989, 3, "1989-04-22T23:00Z", "4", "2", 105, 100, "4", False, "Detroit"),
            ("p89_3", 1989, 3, "1989-04-24T23:00Z", "2", "4", 110, 90, "2", False, "Boston"),
            ("pS_1", _TC_S, 3, f"{_TC_S}-04-20T23:00Z", "2", "18", 100, 90, "2", False, "Boston"),
            ("pS_2", _TC_S, 3, f"{_TC_S}-04-22T23:00Z", "18", "2", 95, 90, "18", False, "New York"),
            ("pS_3", _TC_S, 3, f"{_TC_S}-04-24T23:00Z", "2", "18", 105, 95, "2", False, "Boston"),
        ],
    )
    real_games.build_table(c, {"games", "teams"})
    # Only the four columns _game_list_gaps reads.
    c.execute("CREATE TABLE team_season_stats (season INTEGER, season_type INTEGER, team_id VARCHAR, gamesPlayed INTEGER)")
    c.executemany(
        "INSERT INTO team_season_stats VALUES (?,?,?,?)",
        [
            (_TC_S2, 2, "2", 2),
            (_TC_S2, 2, "18", 1),
            (_TC_S2, 2, "4", 1),
            (_TC_S1, 2, "2", 1),
            (_TC_S1, 2, "18", 1),
            (_TC_S, 2, "2", 2),
            (_TC_S, 2, "18", 1),
            (_TC_S, 2, "4", 1),
            (1989, 3, "2", 3),
            (1989, 3, "4", 3),
            (_TC_S, 3, "2", 3),
            (_TC_S, 3, "18", 3),
        ],
    )
    return AnswerContext(con=c, out_dir=tmp_path)


def test_team_record_honors_since_as_a_since_bounded_span(team_cells_con: AnswerContext) -> None:
    """ "Celtics record since {S-1}" (ISSUES.md): a since-bounded span is
    counted the same shape a whole career is, not one season."""
    result = team_record(team_cells_con, Reading(who=Who(kind="team", teams=("Celtics",)), span=Span(since=_TC_S1)))
    assert result.data["wins"] == 3 and result.data["losses"] == 0
    assert result.text == f"The Boston Celtics are 3-0 (1.000) in the regular seasons since {_TC_S1}.\n  Home 2-0, away 1-0."


def test_team_record_since_narrows_to_a_named_opponent(team_cells_con: AnswerContext) -> None:
    """ "Celtics record vs Knicks since {S-1}" - `since` and `opponent` compose,
    the same way `opponent` composes with a single season."""
    result = team_record(team_cells_con, Reading(who=Who(kind="team", teams=("Celtics",)), cuts=Cuts(opponent="Knicks"), span=Span(since=_TC_S1)))
    assert result.data["wins"] == 2 and result.data["losses"] == 0
    assert result.text == f"The Boston Celtics are 2-0 (1.000) against the New York Knicks in the regular seasons since {_TC_S1}.\n  Home 1-0, away 1-0."


def test_team_record_since_narrows_by_venue(team_cells_con: AnswerContext) -> None:
    result = team_record(team_cells_con, Reading(who=Who(kind="team", teams=("Celtics",)), cuts=Cuts(venue="home"), span=Span(since=_TC_S1)))
    assert result.data["wins"] == 2 and result.data["losses"] == 0
    assert result.text == f"The Boston Celtics are 2-0 (1.000) at home in the regular seasons since {_TC_S1}."


def test_team_record_since_with_no_games_names_the_since_bound(team_cells_con: AnswerContext) -> None:
    """The empty-result sentence says which narrowing emptied it - the same
    false-cause discipline AGENTS.md requires everywhere else - rather than
    reading as no games on record at all."""
    result = team_record(team_cells_con, Reading(who=Who(kind="team", teams=("Pistons",)), span=Span(since=(_TC_S + 10))))
    assert result.text == f"The warehouse holds no regular-season games for the Detroit Pistons since {_TC_S + 10}."


def test_team_record_honors_game_n_within_one_named_postseason(team_cells_con: AnswerContext) -> None:
    """ "Celtics record in game 1 of the {S} playoffs" - one game of each
    series the relation's own `narrow_series_game` numbers."""
    result = team_record(team_cells_con, Reading(who=Who(kind="team", teams=("Celtics",)), cuts=Cuts(game_n=1), span=Span(season=_TC_S, season_type=3)))
    assert result.data["wins"] == 1 and result.data["losses"] == 0
    assert result.text == f"The Boston Celtics went 1-0 (1.000) in game 1 of each series in the {_TC_S} postseason.\n  Home 1-0, away 0-0."


def test_team_record_game_n_combines_with_since_across_postseasons(team_cells_con: AnswerContext) -> None:
    """ "Celtics record in game 1 of each series since 1989" - the warehouse's
    own playoff-game-list floor (AGENTS.md, "Coverage floors"), reaching the
    1989 postseason the same way two teams' meetings' own since-bounded
    postseason test below does."""
    result = team_record(team_cells_con, Reading(who=Who(kind="team", teams=("Celtics",)), cuts=Cuts(game_n=1), span=Span(season_type=3, since=1989)))
    assert result.data["wins"] == 2 and result.data["losses"] == 0
    assert result.text == "The Boston Celtics are 2-0 (1.000) in game 1 of each series in the postseasons since 1989.\n  Home 2-0, away 0-0."


def test_team_record_game_n_refuses_a_regular_season(team_cells_con: AnswerContext) -> None:
    """A series has games 1-7; a regular season has nothing "game 4" names -
    the same check `team_relation.team_games` makes for every other team reader."""
    with pytest.raises(Unsupported):
        team_record(team_cells_con, Reading(who=Who(kind="team", teams=("Celtics",)), cuts=Cuts(game_n=1)))


def test_team_record_since_conflicts_with_a_named_season(team_cells_con: AnswerContext) -> None:
    with pytest.raises(Unsupported):
        team_record(team_cells_con, Reading(who=Who(kind="team", teams=("Celtics",)), span=Span(season=_TC_S, since=_TC_S1)))


def test_team_record_since_conflicts_with_career(team_cells_con: AnswerContext) -> None:
    with pytest.raises(Unsupported):
        team_record(team_cells_con, Reading(who=Who(kind="team", teams=("Celtics",)), span=Span(career=True, since=_TC_S1)))


def test_team_record_since_answers_a_month_split_with_one_table_per_season(team_cells_con: AnswerContext) -> None:
    """F095 (ISSUES.md - "Knicks record by month 2024 2025"): a month split
    with `since` set reads one table PER SEASON in the span. Every Celtics
    game in this fixture is in November: {S-1} holds one (r3, a win) and {S}
    holds two (r4, r5, both wins) - two separate tables, not a single row
    summing three."""
    result = team_record(team_cells_con, Reading(who=Who(kind="team", teams=("Celtics",)), span=Span(since=_TC_S1), split="month"))
    assert result.data["months"] == [
        {"season": _TC_S1, "month": "November", "games": 1, "wins": 1, "losses": 0},
        {"season": _TC_S, "month": "November", "games": 2, "wins": 2, "losses": 0},
    ]
    assert result.text.count("The Boston Celtics, record by month") == 2
    # The headline is the first table's title, the line the answer leads with.
    assert result.data["headline"] == result.text.split("\n")[0].rstrip(":")
    assert f"the {_TC_S1} regular season" in result.data["headline"]


def test_team_record_since_month_split_refuses_game_n(team_cells_con: AnswerContext) -> None:
    """`game_n` has no month-split form: a series-game number and a
    whole-season table of months answer two different shapes of question."""
    with pytest.raises(Unsupported):
        team_record(team_cells_con, Reading(who=Who(kind="team", teams=("Celtics",)), cuts=Cuts(game_n=1), span=Span(since=_TC_S1), split="month"))


def test_team_meetings_honors_since_over_every_meeting_in_the_span(team_cells_con: AnswerContext) -> None:
    """ "Celtics vs Knicks since {S-1}" (ISSUES.md): every meeting in the span,
    not one season."""
    result = team_meetings(team_cells_con, Reading(who=Who(kind="teams", teams=("Celtics", "Knicks")), span=Span(since=_TC_S1)))
    assert result.data["games"] == 2 and result.data["wins"] == {"Boston Celtics": 2, "New York Knicks": 0}
    assert result.text == f"The Boston Celtics and the New York Knicks have met 2 times since {_TC_S1} ({_TC_S1}-{_TC_S} regular seasons); the Boston Celtics lead the all-time series 2-0."


def test_team_meetings_honors_until_bounding_the_since_span(team_cells_con: AnswerContext) -> None:
    """ "Celtics vs Knicks from {S-2} to {S-1}" reads a BOUNDED range - r1
    ({S-2}) and r3 ({S-1}) count, r4 ({S}) does not, unlike the open-ended
    since-only test above, which also picks up r4."""
    result = team_meetings(team_cells_con, Reading(who=Who(kind="teams", teams=("Celtics", "Knicks")), span=Span(since=_TC_S2, until=_TC_S1)))
    assert result.data["games"] == 2 and result.data["wins"] == {"Boston Celtics": 2, "New York Knicks": 0}
    assert f"from {_TC_S2} through {_TC_S1}" in (result.text or "")


def test_team_meetings_until_with_no_since_is_refused(team_cells_con: AnswerContext) -> None:
    with pytest.raises(Unsupported, match="until"):
        team_meetings(team_cells_con, Reading(who=Who(kind="teams", teams=("Celtics", "Knicks")), span=Span(until=_TC_S1)))


def test_team_meetings_honors_career_over_every_meeting_on_record(team_cells_con: AnswerContext) -> None:
    """ "All-time Celtics vs Knicks" (ISSUES.md) - `span` "career", the same
    shape a team's record's own whole-career answer reads."""
    result = team_meetings(team_cells_con, Reading(who=Who(kind="teams", teams=("Celtics", "Knicks")), span=Span(career=True)))
    assert result.data["games"] == 3 and result.data["wins"] == {"Boston Celtics": 3, "New York Knicks": 0}
    assert result.text == f"The Boston Celtics and the New York Knicks have met 3 times over the seasons on record ({_TC_S2}-{_TC_S} regular seasons); the Boston Celtics lead the all-time series 3-0."


def test_team_meetings_since_reaches_the_1989_postseason_floor(team_cells_con: AnswerContext) -> None:
    """The team tables reach 1988-89 for the postseason (AGENTS.md, "Coverage
    floors") - "Celtics vs Pistons since 1989" reads the one series the
    warehouse holds that far back."""
    result = team_meetings(team_cells_con, Reading(who=Who(kind="teams", teams=("Celtics", "Pistons")), span=Span(season_type=3, since=1989)))
    assert result.data["games"] == 3 and result.data["wins"] == {"Boston Celtics": 2, "Detroit Pistons": 1}
    assert result.text == "The Boston Celtics and the Detroit Pistons have met 3 times since 1989 (1989 postseason); the Boston Celtics lead the all-time series 2-1."


def test_team_meetings_since_with_no_meetings_names_the_since_bound(team_cells_con: AnswerContext) -> None:
    result = team_meetings(team_cells_con, Reading(who=Who(kind="teams", teams=("Celtics", "Pistons")), span=Span(since=(_TC_S + 10))))
    assert result.text == f"The Boston Celtics and the Detroit Pistons have not played each other since {_TC_S + 10}."


def test_team_meetings_since_narrows_by_venue(team_cells_con: AnswerContext) -> None:
    """`since` composes with `venue` the same way a single season does -
    narrowed to the first-named team's home games - and an open-ended span is
    led, not won, as the venue-less sentence says it (ISSUES.md #195: one
    shape of span, one sentence)."""
    result = team_meetings(team_cells_con, Reading(who=Who(kind="teams", teams=("Celtics", "Knicks")), cuts=Cuts(venue="home"), span=Span(since=_TC_S1)))
    assert result.data["games"] == 1 and result.data["wins"] == {"Boston Celtics": 1, "New York Knicks": 0}
    assert result.text == f"The Boston Celtics and the New York Knicks met once in the Boston Celtics' home games since {_TC_S1}; the Boston Celtics lead the all-time series 1-0."
    career = team_meetings(team_cells_con, Reading(who=Who(kind="teams", teams=("Celtics", "Knicks")), cuts=Cuts(venue="home"), span=Span(career=True)))
    assert (career.text or "").endswith("home games on record; the Boston Celtics lead the all-time series 2-0."), career.text


def test_team_meetings_since_conflicts_with_a_date(team_cells_con: AnswerContext) -> None:
    with pytest.raises(Unsupported):
        team_meetings(team_cells_con, Reading(who=Who(kind="teams", teams=("Celtics", "Knicks")), cuts=Cuts(date="2026-01-01"), span=Span(since=_TC_S1)))


def test_team_meetings_since_conflicts_with_career(team_cells_con: AnswerContext) -> None:
    with pytest.raises(Unsupported):
        team_meetings(team_cells_con, Reading(who=Who(kind="teams", teams=("Celtics", "Knicks")), span=Span(career=True, since=_TC_S1)))


def test_team_period_total_reads_each_games_own_side_of_linescores(tq_con: AnswerContext) -> None:
    """ "how many points did the 76ers score in the 4th quarter against Boston
    this season?": games.home_linescores/away_linescores store the exact
    per-period score for each side, and the read takes the right one for each
    game (home vs away), not always the same column."""
    result = team_period_total(tq_con, Reading(who=Who(kind="team", teams=("Knicks",)), span=Span(season=current_season()), period=Period(number=1)))
    assert result.data["total"] == 60  # 30 (home in e1) + 20 (away in e2) + 10 (home in e3)
    assert len(result.data["games"]) == 3
    assert result.data["average"] == pytest.approx(20.0)  # 60 / 3 - the text says it, and data carries it
    assert result.data["headline"] == (result.text or "").splitlines()[0].rstrip(":")


def test_team_period_total_reads_a_bare_limit_as_the_newest_games(tq_con: AnswerContext) -> None:
    """A bare count on "last N games" reads the newest games: the team
    relation reads a bare count the way the player relation does - through
    the one `relation_window` rule - rather than answering the whole season."""
    result = team_period_total(tq_con, Reading(who=Who(kind="team", teams=("Knicks",)), span=Span(season=current_season()), period=Period(number=1), window=Window(count=2)))
    assert len(result.data["games"]) == 2 and "last 2 games" in (result.text or "")


def test_team_period_total_filters_to_a_named_opponent(tq_con: AnswerContext) -> None:
    result = team_period_total(tq_con, Reading(who=Who(kind="team", teams=("Knicks",)), cuts=Cuts(opponent="Celtics"), span=Span(season=current_season()), period=Period(number=4)))
    assert result.data["total"] == 57  # 29 (e1) + 28 (e2) - e3 (vs Lakers) excluded
    assert len(result.data["games"]) == 2
    assert "Boston Celtics" in (result.text or "")


def test_team_period_total_reports_no_games_honestly(tq_con: AnswerContext) -> None:
    # The Lakers and Celtics never played each other in this fixture (only
    # each played the Knicks) - an empty result must say so, not answer 0.
    result = team_period_total(tq_con, Reading(who=Who(kind="team", teams=("Lakers",)), cuts=Cuts(opponent="Celtics"), span=Span(season=current_season()), period=Period(number=1)))
    assert result.data["games"] == []
    assert "no" in (result.text or "").lower()


def test_team_period_total_reports_a_period_no_game_reached(tq_con: AnswerContext) -> None:
    # Every game in the fixture has exactly 4 quarters on record - asking for
    # a 5th (overtime) must say none of the games went there, not silently
    # answer 0 or crash on a short list index.
    result = team_period_total(tq_con, Reading(who=Who(kind="team", teams=("Knicks",)), cuts=Cuts(opponent="Celtics"), span=Span(season=current_season()), period=Period(number=5)))
    assert "overtime" in (result.text or "").lower()
    assert "total" not in result.data


def test_team_period_total_refuses_a_named_player(tq_con: AnswerContext) -> None:
    with pytest.raises(Unsupported):
        team_period_total(tq_con, Reading(who=Who(kind="player", players=("Jalen Brunson",), teams=("Knicks",)), period=Period(number=4)))


def test_team_period_total_refuses_a_stat_no_period_splits_and_one_it_cannot_rebuild_here(tq_con: AnswerContext) -> None:
    """A linescore holds one number per period - the score - so answering a
    team's three-point or rebounding average by quarter with POINTS is the
    fluent wrong answer this project keeps producing ("trailblazers stats
    last 10 games 3 point average 1st quarter"). Those columns are rebuilt
    from the plays (the period relation's team half); this fixture holds
    none, so the answer says so, never a zero. A stat nothing splits by
    period is refused naming it."""
    for stat in ("threePointFieldGoalsMade", "rebounds", "assists"):
        result = team_period_total(tq_con, Reading(who=Who(kind="team", teams=("Knicks",)), measure=measure_of(stat), span=Span(season=current_season()), period=Period(number=1)))
        assert "No play-by-play is on record" in (result.text or "") and "total" not in result.data, stat
    for stat in ("minutes", "plusMinus", "pointsInPaint"):
        result = team_period_total(tq_con, Reading(who=Who(kind="team", teams=("Knicks",)), measure=measure_of(stat), span=Span(season=current_season()), period=Period(number=1)))
        assert f"not {stat}." in (result.text or "") and "by quarter is not on record" in (result.text or ""), stat
    # Points is the one the linescore holds, under either spelling, and no
    # stat at all still means the score.
    allowed: tuple[str | None, ...] = ("points", "avg_points", None)
    for held in allowed:
        result = team_period_total(tq_con, Reading(who=Who(kind="team", teams=("Knicks",)), measure=measure_of(held) if held else None, span=Span(season=current_season()), period=Period(number=1)))
        assert result.data["total"] == 60


@pytest.mark.parametrize(
    ("measure", "named"),
    [
        (Measure(key="minutes", as_typed="minutes", whose="opponent"), "opponents' minutes"),
        (Measure(key="plusMinus", as_typed="plusMinus", whose="opponent"), "opponents' plusMinus"),
    ],
)
def test_team_period_total_names_the_opponents_figure_it_refuses(tq_con: AnswerContext, measure: Measure, named: str) -> None:
    """The opponent's figure read by "opp" before the stat (ISSUES.md #409)
    or the verb after it (#348) is refused by quarter, where the team's own
    is, naming it as theirs - never "- not minutes." beside the team's own
    list, saying nothing about whose."""
    result = team_period_total(tq_con, Reading(who=Who(kind="team", teams=("Knicks",)), measure=measure, span=Span(season=current_season()), period=Period(number=1)))
    assert (result.text or "").endswith(f"- not {named}."), result.text
    assert "total" not in result.data


def test_team_period_total_answers_the_opponents_points_from_their_linescore(tq_con: AnswerContext) -> None:
    """ISSUES.md #427: the opponent's points in a quarter ("opp points",
    "points allowed") are on record - the linescore holds both sides' - and
    are read from the other one."""
    for measure in (Measure(key="points", as_typed="points", whose="opponent"), Measure(key="points", as_typed="points allowed", whose="opponent")):
        result = team_period_total(tq_con, Reading(who=Who(kind="team", teams=("Knicks",)), measure=measure, span=Span(season=current_season()), period=Period(number=1)))
        assert "total" in result.data and (result.text or "").startswith("The New York Knicks' opponents"), result.text


def test_team_period_total_refuses_a_missing_period(tq_con: AnswerContext) -> None:
    with pytest.raises(Unsupported):
        team_period_total(tq_con, Reading(who=Who(kind="team", teams=("Knicks",))))


def test_team_period_total_refuses_the_team_as_its_own_opponent(tq_con: AnswerContext) -> None:
    with pytest.raises(Unsupported):
        team_period_total(tq_con, Reading(who=Who(kind="team", teams=("Knicks",)), cuts=Cuts(opponent="New York Knicks"), period=Period(number=1)))


def test_team_period_total_phrases_a_single_game_directly(tq_con: AnswerContext) -> None:
    result = team_period_total(tq_con, Reading(who=Who(kind="team", teams=("Knicks",)), cuts=Cuts(opponent="Lakers"), span=Span(season=current_season()), period=Period(number=1)))
    assert result.data["total"] == 10
    assert "2026-04-14" in (result.text or "") or str(current_season()) in (result.text or "")


def test_team_period_total_summarizes_rather_than_tables_many_games(tq_con: AnswerContext, monkeypatch: pytest.MonkeyPatch) -> None:
    # A whole-season, no-opponent question can span dozens of games - listing
    # every one is unreadable, so above a small cap this reports the total
    # and average instead of a per-game breakdown.
    import sys

    # By the module object: `association.query.compose.say` names the package's `say` function too.
    monkeypatch.setattr(sys.modules["association.query.compose.say"], "_QUARTER_BREAKDOWN_LIMIT", 1)
    result = team_period_total(tq_con, Reading(who=Who(kind="team", teams=("Knicks",)), span=Span(season=current_season()), period=Period(number=1)))
    answer = result.text or ""
    assert "averaging" in answer
    assert "2026-04-10" not in answer


def test_player_line_refuses_a_named_stat_it_cannot_provide(ps_con: AnswerContext) -> None:
    """A named stat the line cannot provide is declined, never answered with
    the default stat line ("avg 3pt shot distance" is not points/rebounds/
    assists)."""
    with pytest.raises(Unsupported):
        player_line(ps_con, Reading(who=Who(kind="player", players=("Luka Doncic",)), measure=Measure(key="shot_distance", as_typed="shot_distance")))


def test_player_line_with_no_stat_named_says_points(ps_con: AnswerContext) -> None:
    assert "points" in (player_line(ps_con, Reading(who=Who(kind="player", players=("Luka Doncic",)))).text or "")


def test_player_comparison_refuses_a_named_stat_it_cannot_provide(ps_con: AnswerContext) -> None:
    with pytest.raises(Unsupported):
        player_comparison(ps_con, Reading(who=Who(kind="pair", players=("Luka Doncic", "Nikola Jokic")), measure=Measure(key="shot_distance", as_typed="shot_distance")))


def test_player_line_answers_three_pointers_made_with_their_attempts_and_percentage(ps_con: AnswerContext) -> None:
    ps_con.con.execute("ALTER TABLE player_season_stats_deduped ADD COLUMN IF NOT EXISTS avgThreePointFieldGoalsMade DOUBLE")
    ps_con.con.execute("ALTER TABLE player_season_stats_deduped ADD COLUMN IF NOT EXISTS threePointFieldGoalsMade INTEGER")
    ps_con.con.execute("ALTER TABLE player_season_stats_deduped ADD COLUMN IF NOT EXISTS threePointFieldGoalsAttempted INTEGER")
    ps_con.con.execute("UPDATE player_season_stats_deduped SET avgThreePointFieldGoalsMade = 4.4, threePointFieldGoalsMade = 282, threePointFieldGoalsAttempted = 620 WHERE athlete_id = '1'")
    answer = player_line(ps_con, Reading(who=Who(kind="player", players=("Luka Doncic",)), measure=Measure(key="threePointFieldGoalsMade", as_typed="threePointFieldGoalsMade"))).text or ""
    # F051 (ISSUES.md): a made-count stat carries its attempts and the
    # percentage they make beside the total - "282 of 620 (45.5%)" - the same
    # "out of how many?" discipline a bare shooting percentage keeps.
    assert "4.4 3-pointers" in answer and "282 of 620 (45.5%)" in answer


def test_player_line_answers_two_point_percentage_for_a_season_and_a_career(ps_con: AnswerContext) -> None:
    """ISSUES.md #114: no stored 2-point percentage column exists in either
    table (checked against the warehouse), so SHOOTING_STATS' entry for it is
    an expression - makes and attempts less the threes - rather than a bare
    column name, unlike its two siblings. Proven here for both the season
    lookup and the career sum, which read the season table two different ways
    (``season_line.season_statement``'s plain SELECT and ``career_statement``'s per-season SUM)."""
    for col in ("fieldGoalsMade", "fieldGoalsAttempted", "threePointFieldGoalsMade", "threePointFieldGoalsAttempted"):
        ps_con.con.execute(f"ALTER TABLE player_season_stats_deduped ADD COLUMN IF NOT EXISTS {col} INTEGER")
    ps_con.con.execute("UPDATE player_season_stats_deduped SET fieldGoalsMade=700, fieldGoalsAttempted=1300, threePointFieldGoalsMade=200, threePointFieldGoalsAttempted=500 WHERE athlete_id='1'")
    season = player_line(ps_con, Reading(who=Who(kind="player", players=("Luka Doncic",)), measure=Measure(key="twoPointFieldGoalPct", as_typed="twoPointFieldGoalPct"))).text
    assert season == (
        f"Luka Doncic shot 62.5% on 2-pointers (500 of 800) in 64 games in the {current_season()} regular season.\n"
        "   MIN   PTS  REB  AST  STL  BLK  TOV   PF               FG%            3PT%             FT%\n"
        "  37.5  33.5  7.7  8.3  1.4  0.5  4.0  2.1  53.8 (700-1,300)  40.0 (200-500)  78.4 (399-509)"
    )

    # A second season, so the career sum is a total over games, not an
    # average of the two seasons' percentages - (500+200)/(800+400) = 58.3%,
    # not the mean of 62.5% and 50.0%.
    ps_con.con.execute(
        "INSERT INTO player_season_stats_deduped (athlete_id, season, season_type, gamesPlayed, avgPoints, "
        "fieldGoalsMade, fieldGoalsAttempted, threePointFieldGoalsMade, threePointFieldGoalsAttempted) "
        "VALUES ('1', ?, 2, 50, 20.0, 300, 600, 100, 200)",
        [current_season() - 1],
    )
    career = (
        player_line(ps_con, Reading(who=Who(kind="player", players=("Luka Doncic",)), measure=Measure(key="twoPointFieldGoalPct", as_typed="twoPointFieldGoalPct"), span=Span(career=True))).text or ""
    )
    assert "58.3% on 2-pointers (700 of 1,200)" in career


def test_player_line_answers_two_point_percentage_narrowed_to_box_scores(pg_ctx: AnswerContext) -> None:
    """The box-score-narrowed path joins `games` and qualifies every shooting
    column with the `pgl.` alias - blindly prepending it to this stat's
    expression would read "pgl.(fieldGoalsMade - threePointFieldGoalsMade)",
    which is not valid SQL. Podziemski's two games vs Detroit (e2: 10-for-20,
    e3: 7-for-15, both all twos in this fixture) sum to 17 of 35 = 48.6%."""
    answer = (
        player_line(
            pg_ctx,
            Reading(who=Who(kind="player", players=("Brandin Podziemski",)), cuts=Cuts(opponent="Detroit Pistons"), measure=Measure(key="twoPointFieldGoalPct", as_typed="twoPointFieldGoalPct")),
        ).text
        or ""
    )
    assert "48.6% on 2-pointers (17 of 35) in 2 games vs the Detroit Pistons" in answer


# ---------------- a shot distance ----------------


def test_shot_distance_filters_to_the_shot_value_asked_for(sc_ctx: AnswerContext) -> None:
    """A three-point distance keeps both the 3-point filter and the season
    filter, never an all-shots, all-seasons average reported as a
    current-season three-point distance."""
    sc_ctx.con.execute("INSERT INTO shot_chart VALUES ('1',?,2,'e5',1,'1:00',TRUE,'Layup',25,1,2,'makes layup')", [current_season()])
    threes = shot_distance(sc_ctx, Reading(who=Who(kind="player", players=("Stephen Curry",)), measure=Measure(shot_value=3)))
    everything = shot_distance(sc_ctx, Reading(who=Who(kind="player", players=("Stephen Curry",))))
    assert threes.data["attempts"] == 2 and everything.data["attempts"] == 3
    assert threes.data["avg_feet"] > everything.data["avg_feet"]


def test_shot_distance_measures_from_the_rim(sc_ctx: AnswerContext) -> None:
    """The rim is at (25, 0) in ESPN's coordinates - y is measured from it, not
    from the baseline - so the fixture's shot at (25, 26) is 26 feet out. A
    frame with the rim at (25, 5.25) would make it 20.75, which would put
    Stephen Curry's 2026 threes at 23.6 feet, inside the line."""
    result = shot_distance(sc_ctx, Reading(who=Who(kind="player", players=("Stephen Curry",)), measure=Measure(shot_value=3)))
    assert result.data["avg_feet"] == pytest.approx(26.0)


def test_shot_distance_counts_threes_espn_left_unlabeled(sc_ctx: AnswerContext) -> None:
    """``points_attempted`` is 0 for an unlabeled shot, not a zero-point one,
    and 96% of 2022 is unlabeled: filtered on it, "Curry's threes in 2022"
    would average 38 of his 751 attempts, every one a miss. The description
    names one of these as a three and the position names the other."""
    sc_ctx.con.executemany(
        "INSERT INTO shot_chart VALUES ('1',?,2,'e7',1,'2:00',?,'Jump Shot',?,?,0,?)",
        [(current_season(), True, 25, 25, "makes 25-foot three point jumper"), (current_season(), False, 2, 3, "misses 23-foot step back jumpshot")],
    )
    assert shot_distance(sc_ctx, Reading(who=Who(kind="player", players=("Stephen Curry",)), measure=Measure(shot_value=3))).data["attempts"] == 4


def test_shot_distance_leaves_out_free_throws_that_carry_a_position(sc_ctx: AnswerContext) -> None:
    """From 2002 to 2018 every free throw has a position under the rim, so
    "has coordinates" does not exclude them - they would average in as
    zero-foot shots."""
    sc_ctx.con.execute("INSERT INTO shot_chart VALUES ('1',?,2,'e1',1,'3:00',TRUE,'Free Throw - 1 of 2',25,0,1,'makes free throw 1 of 2')", [current_season()])
    result = shot_distance(sc_ctx, Reading(who=Who(kind="player", players=("Stephen Curry",))))
    assert result.data["attempts"] == 2
    assert result.data["avg_feet"] == pytest.approx(26.0)


def test_shot_distance_leaves_out_shots_with_no_position(sc_ctx: AnswerContext) -> None:
    sc_ctx.con.execute("INSERT INTO shot_chart VALUES ('1',?,2,'e1',1,'4:00',TRUE,'Layup Shot',0,0,2,'makes layup')", [current_season()])
    assert shot_distance(sc_ctx, Reading(who=Who(kind="player", players=("Stephen Curry",)))).data["attempts"] == 2


def test_shot_distance_refuses_threes_in_a_season_that_cannot_tell_them_apart(sc_ctx: AnswerContext) -> None:
    """2002 has no labels and descriptions that miss threes, so answering from
    what it has would be a narrower answer with nothing saying so. The refusal
    names that cause - not an absence of data, since the same season still
    answers for all of a player's shots."""
    sc_ctx.con.executemany(
        "INSERT INTO shot_chart VALUES ('1',2002,2,'e8',1,'5:00',TRUE,'Jump Shot',?,?,0,?)",
        [(25, 25, "made 25 ft Three Point Jumper."), (47, 0, "made Jumper.")],
    )
    refused = shot_distance(sc_ctx, Reading(who=Who(kind="player", players=("Stephen Curry",)), measure=Measure(shot_value=3), span=Span(season=2002)))
    assert "avg_feet" not in refused.data
    assert (refused.text or "").startswith(shotchart.UNSEPARABLE_SHOT_VALUES[2002])
    assert shot_distance(sc_ctx, Reading(who=Who(kind="player", players=("Stephen Curry",)), span=Span(season=2002))).data["attempts"] == 2


def test_shot_distance_says_when_a_seasons_shot_values_are_derived(sc_ctx: AnswerContext) -> None:
    sc_ctx.con.execute("INSERT INTO shot_chart VALUES ('1',2022,2,'e9',1,'6:00',TRUE,'Jump Shot',25,26,0,'makes 26-foot three point jumper')")
    threes = shot_distance(sc_ctx, Reading(who=Who(kind="player", players=("Stephen Curry",)), measure=Measure(shot_value=3), span=Span(season=2022)))
    assert threes.data["attempts"] == 1
    assert shotchart.DERIVED_SHOT_VALUES[2022] in (threes.text or "")
    # A question about all shots does not rest on the derivation, so says nothing.
    assert "Note" not in (shot_distance(sc_ctx, Reading(who=Who(kind="player", players=("Stephen Curry",)), span=Span(season=2022))).text or "")


def test_shot_chart_draws_threes_espn_left_unlabeled(sc_ctx: AnswerContext) -> None:
    sc_ctx.con.execute("INSERT INTO shot_chart VALUES ('1',?,2,'e7',1,'2:00',TRUE,'Jump Shot',2,3,0,'makes 23-foot step back jumpshot')", [current_season()])
    result = shot_chart(sc_ctx, Reading(who=Who(kind="player", players=("Stephen Curry",)), measure=Measure(shot_value=3), span=Span(season=current_season())))
    assert "(2/3 made" in (result.text or "")


def test_shot_chart_refuses_threes_in_a_season_that_cannot_tell_them_apart(sc_ctx: AnswerContext) -> None:
    sc_ctx.con.execute("INSERT INTO shot_chart VALUES ('1',2002,2,'e8',1,'5:00',TRUE,'Jump Shot',25,25,0,'made 25 ft Three Point Jumper.')")
    result = shot_chart(sc_ctx, Reading(who=Who(kind="player", players=("Stephen Curry",)), measure=Measure(shot_value=3), span=Span(season=2002)))
    assert (result.text or "").startswith(shotchart.UNSEPARABLE_SHOT_VALUES[2002])
    assert not list(sc_ctx.out_dir.glob("*.html"))


def test_shot_chart_leaves_free_throws_off_the_court(sc_ctx: AnswerContext) -> None:
    """A 2002-2018 free throw has a position under the rim, and is not drawn
    there as a shot."""
    sc_ctx.con.execute("INSERT INTO shot_chart VALUES ('1',?,2,'e1',1,'3:00',TRUE,'Free Throw - 1 of 2',25,0,1,'makes free throw 1 of 2')", [current_season()])
    assert "(1/2 made" in (shot_chart(sc_ctx, Reading(who=Who(kind="player", players=("Stephen Curry",)), span=Span(season=current_season()))).text or "")


def test_a_free_throw_chart_is_refused_rather_than_drawn(sc_ctx: AnswerContext) -> None:
    result = shot_chart(sc_ctx, Reading(who=Who(kind="player", players=("Stephen Curry",)), measure=Measure(shot_value=1), span=Span(season=current_season())))
    assert "no free-throw chart" in (result.text or "")
    assert not list(sc_ctx.out_dir.glob("*.html"))


def test_a_free_throw_chart_in_a_defaulted_season_is_refused_without_a_redirect(sc_ctx: AnswerContext) -> None:
    """The season was defaulted and nothing was drawn - because a free-throw
    chart never is, not because the season is empty. The refusal stands
    alone, with no redirect (#18) naming the season he was drawn for as the
    one he "last appears in"."""
    sc_ctx.con.execute("CREATE TABLE player_season_stats_deduped (athlete_id VARCHAR, season INTEGER, season_type INTEGER, gamesPlayed INTEGER)")
    sc_ctx.con.execute("INSERT INTO player_season_stats_deduped VALUES ('1', ?, 2, 70)", [current_season()])
    result = shot_chart(sc_ctx, Reading(who=Who(kind="player", players=("Stephen Curry",)), measure=Measure(shot_value=1)))
    assert result.text == "Free throws are all taken from the same line and carry no court position worth drawing, so there is no free-throw chart to render."
    assert result.artifacts == []


def test_a_chart_a_shot_value_emptied_in_a_defaulted_season_keeps_the_plain_refusal(sc_ctx: AnswerContext) -> None:
    """ISSUES.md #296: a player with shots this season and none of the value
    asked for ("Deandre Ayton's threes": 601 shots in 2026, no three) is not
    told he "last appears in 2026" - the filter emptied the chart, not the
    season. The redirect is for a season holding none of his shots at all;
    under a shot value the plain refusal names the filters."""
    sc_ctx.con.execute("CREATE TABLE player_season_stats_deduped (athlete_id VARCHAR, season INTEGER, season_type INTEGER, gamesPlayed INTEGER)")
    sc_ctx.con.execute("INSERT INTO players VALUES ('3','Big Man')")
    sc_ctx.con.execute("INSERT INTO shot_chart VALUES ('3',?,2,'e1',1,'9:00',TRUE,'Dunk',25,1,2,'2-foot two point dunk')", [current_season()])
    result = shot_chart(sc_ctx, Reading(who=Who(kind="player", players=("Big Man",)), measure=Measure(shot_value=3)))
    assert result.text == f"No 3PT attempts found for Big Man in the {current_season()} regular season."
    assert result.artifacts == []
    distance = shot_distance(sc_ctx, Reading(who=Who(kind="player", players=("Big Man",)), measure=Measure(shot_value=3)))
    assert distance.text == f"No 3-point shots with recorded coordinates for Big Man in the {season_phrase(current_season(), 2)}."


def test_a_career_chart_says_which_shots_it_left_out(sc_ctx: AnswerContext) -> None:
    """One unseparable season is refused outright. Across seasons the chart
    draws what can be told apart and says what it could not, rather than
    dropping it where nobody would know."""
    sc_ctx.con.execute("INSERT INTO shot_chart VALUES ('1',2002,2,'e8',1,'5:00',FALSE,'Jump Shot',47,0,0,'missed Jumper.')")
    # A career chart says what the shot floor leaves of it, read from his seasons on record.
    sc_ctx.con.execute("CREATE TABLE player_season_stats_deduped (athlete_id VARCHAR, season INTEGER, season_type INTEGER, gamesPlayed INTEGER)")
    sc_ctx.con.execute("INSERT INTO player_season_stats_deduped VALUES ('1', 2002, 2, 10), ('1', ?, 2, 70)", [current_season()])
    rendered = shot_chart(sc_ctx, Reading(who=Who(kind="player", players=("Stephen Curry",)), measure=Measure(shot_value=3), span=Span(career=True)))
    assert "(1/2 made" in (rendered.text or "")
    assert "left out 1 shot that cannot be told apart as twos or threes" in (rendered.text or "")


def test_shot_distance_scopes_to_the_current_season_by_default(sc_ctx: AnswerContext) -> None:
    sc_ctx.con.execute("INSERT INTO shot_chart VALUES ('1',2019,2,'e6',1,'1:00',TRUE,'Jump Shot',25,40,3,'40-foot three point jumper')")
    assert shot_distance(sc_ctx, Reading(who=Who(kind="player", players=("Stephen Curry",)), measure=Measure(shot_value=3))).data["attempts"] == 2


def test_shot_distance_reads_three_pointers_made_as_a_shot_value_of_3(sc_ctx: AnswerContext) -> None:
    by_stat = shot_distance(sc_ctx, Reading(who=Who(kind="player", players=("Stephen Curry",)), measure=Measure(key="threePointFieldGoalsMade", as_typed="threePointFieldGoalsMade")))
    assert by_stat.data["shot_value"] == 3


def test_shot_distance_declines_free_throws(sc_ctx: AnswerContext) -> None:
    with pytest.raises(Unsupported):
        shot_distance(sc_ctx, Reading(who=Who(kind="player", players=("Stephen Curry",)), measure=Measure(shot_value=1)))


def test_shot_distance_reports_no_coordinates_honestly(sc_ctx: AnswerContext) -> None:
    # A season the shot table covers (1999 is under its floor, and the floor's refusal answers it: the
    # compiler checks coverage before the reader runs).
    answer = shot_distance(sc_ctx, Reading(who=Who(kind="player", players=("Stephen Curry",)), span=Span(season=2010))).text or ""
    assert answer == "No shots with recorded coordinates for Stephen Curry in the 2010 regular season."


def test_shot_distance_in_a_defaulted_season_redirects_to_a_retired_players_range(sc_ctx: AnswerContext) -> None:
    """The chart's #18 rule, for the distance: no season was named, "now" has
    no shots of his, and the answer names the seasons he IS on record for
    rather than only "none in the 2026 regular season". A distance answers a
    career, so the sentence offers one; a named season keeps the plain line."""
    sc_ctx.con.execute("INSERT INTO players VALUES ('2','Old Timer')")
    sc_ctx.con.execute("INSERT INTO shot_chart VALUES ('2',2010,2,'e9',1,'8:00',TRUE,'Jump Shot',25,26,2,'20-foot two point jumper')")
    result = shot_distance(sc_ctx, Reading(who=Who(kind="player", players=("Old Timer",))))
    assert result.text == (
        f"No shots with recorded coordinates for Old Timer in the {season_phrase(current_season(), 2)}. "
        "He last appears in 2010. The warehouse holds his 2010 regular season; name one, or ask for his career."
    )
    named = shot_distance(sc_ctx, Reading(who=Who(kind="player", players=("Old Timer",)), span=Span(season=2009)))
    assert named.text == "No shots with recorded coordinates for Old Timer in the 2009 regular season."


def test_shot_distance_without_a_player_is_declined(sc_ctx: AnswerContext) -> None:
    with pytest.raises(Unsupported):
        shot_distance(sc_ctx, Reading(measure=Measure(shot_value=3)))


# ---------------- a player's season history ----------------


def test_season_history_spans_several_seasons(ps_con: AnswerContext) -> None:
    """A multi-season question reads one row per season, never a ranking."""
    s = current_season()
    ps_con.con.execute("INSERT INTO player_season_stats_deduped (athlete_id, season, season_type, gamesPlayed, avgPoints) VALUES ('1',?,2,70,30.0),('1',?,2,72,28.0)", [s - 1, s - 2])
    result = season_history(ps_con, Reading(who=Who(kind="player", players=("Luka Doncic",)), measure=Measure(key="points", as_typed="points"), window=Window(count=3)))
    assert [row["season"] for row in result.data["seasons"]] == [s, s - 1, s - 2]


def test_season_history_defaults_to_four_seasons(ps_con: AnswerContext) -> None:
    from association.query.season_line import DEFAULT_HISTORY_SEASONS

    s = current_season()
    for offset in range(1, 8):
        ps_con.con.execute("INSERT INTO player_season_stats_deduped (athlete_id, season, season_type, gamesPlayed, avgPoints) VALUES ('1',?,2,70,20.0)", [s - offset])
    result = season_history(ps_con, Reading(who=Who(kind="player", players=("Luka Doncic",)), measure=Measure(key="points", as_typed="points")))
    assert len(result.data["seasons"]) == DEFAULT_HISTORY_SEASONS


def test_season_history_reports_a_percentage_with_its_volume(ps_con: AnswerContext) -> None:
    # A percentage without makes/attempts is the thing people immediately ask
    # "out of how many?" about.
    for col, typ in [("threePointFieldGoalPct", "DOUBLE"), ("threePointFieldGoalsMade", "INTEGER"), ("threePointFieldGoalsAttempted", "INTEGER")]:
        ps_con.con.execute(f"ALTER TABLE player_season_stats_deduped ADD COLUMN IF NOT EXISTS {col} {typ}")
    ps_con.con.execute("UPDATE player_season_stats_deduped SET threePointFieldGoalPct=38.3, threePointFieldGoalsMade=202, threePointFieldGoalsAttempted=527 WHERE athlete_id='1'")
    answer = season_history(ps_con, Reading(who=Who(kind="player", players=("Luka Doncic",)), measure=Measure(key="threePointFieldGoalPct", as_typed="threePointFieldGoalPct"))).text or ""
    assert "3PT%" in answer and "3PM" in answer and "3PA" in answer
    assert "38.3" in answer and "202" in answer and "527" in answer


def test_season_history_refuses_a_stat_it_has_no_history_for(ps_con: AnswerContext) -> None:
    """A stat neither the season line nor the games carry is refused - never
    drawn as the points history the compiler's default measure would read in
    its place. A history naming no stat at all is the points default, stated
    in its heading
    (test_compose.test_a_history_naming_no_stat_is_points_and_says_so)."""
    with pytest.raises(Unsupported, match="no per-season history for stat 'shot_distance'"):
        season_history(ps_con, Reading(who=Who(kind="player", players=("Luka Doncic",)), measure=Measure(key="shot_distance", as_typed="shot_distance")))


def test_season_history_asks_on_an_ambiguous_player(ps_con: AnswerContext) -> None:
    assert "did you mean" in (season_history(ps_con, Reading(who=Who(kind="player", players=("Curry",)), measure=Measure(key="points", as_typed="points"))).text or "")


def test_season_history_answers_two_point_percentage_computed_not_stored(ps_con: AnswerContext) -> None:
    """ISSUES.md #114: there is no stored 2-point percentage column (checked
    against the warehouse - only fieldGoalPct and threePointFieldGoalPct
    exist), so it has to be computed from field goals less the threes, the
    same makes-and-attempts discipline every other percentage here follows.
    "show me sga's 2pt percentage for the past 5 years" with
    stat='fieldGoalPct' answers two-point shooting, never OVERALL shooting."""
    for col in ("fieldGoalsMade", "fieldGoalsAttempted", "threePointFieldGoalsMade", "threePointFieldGoalsAttempted"):
        ps_con.con.execute(f"ALTER TABLE player_season_stats_deduped ADD COLUMN IF NOT EXISTS {col} INTEGER")
    ps_con.con.execute("UPDATE player_season_stats_deduped SET fieldGoalsMade=700, fieldGoalsAttempted=1300, threePointFieldGoalsMade=200, threePointFieldGoalsAttempted=500 WHERE athlete_id='1'")
    answer = season_history(ps_con, Reading(who=Who(kind="player", players=("Luka Doncic",)), measure=Measure(key="twoPointFieldGoalPct", as_typed="twoPointFieldGoalPct"))).text or ""
    assert "2PT%" in answer and "2PM" in answer and "2PA" in answer
    # (700-200)/(1300-500) = 500/800 = 62.5% - not fieldGoalPct's own 700/1300 = 53.8%.
    assert "62.5" in answer and "500" in answer and "800" in answer
    assert "53.8" not in answer


def test_player_ranking_refuses_when_a_player_is_named(lb_con: AnswerContext) -> None:
    """A ranking ranks the league or a team, never one named person: a
    question about Klay Thompson is not answered with the league's
    true-shooting leaders, Klay silently dropped."""
    with pytest.raises(Unsupported):
        player_ranking(lb_con, Reading(who=Who(kind="player", players=("Klay Thompson",)), measure=Measure(key="points", as_typed="points")))


def test_player_ranking_refuses_a_shot_distance_ranking_naming_the_real_cause(lb_con: AnswerContext) -> None:
    """ISSUES.md #114 and yardstick-v2 F019: "who lead the league in avg 3
    point distance" is neither a PERCENTAGE from the nearest real metric
    (threePointFieldGoalPct) nor a refusal of a named player the question
    does not mention: the refusal says the ranking is not built - never that
    no ranking COULD be, since the key computes a real league leader straight
    from `shot_chart`."""
    result = player_ranking(lb_con, Reading(measure=Measure(key="shot_distance", as_typed="shot_distance")))
    assert result.text == "Shot distance is not ranked league-wide yet - ask about one named player's average shot distance instead."
    # Given a named player, the compiler refuses the named player in the
    # ranking's own words.
    with pytest.raises(Unsupported, match="one named player"):
        player_ranking(lb_con, Reading(who=Who(kind="player", players=("Klay Thompson",)), measure=Measure(key="points", as_typed="points")))


# ---------------- a player's NetPoints ratings ----------------


@pytest.fixture
def np_ctx(tmp_path: Path) -> AnswerContext:
    from association.nba.netpoints import FINGERPRINT_CATEGORIES

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
    return AnswerContext(con=c, out_dir=tmp_path)


def test_netpoints_ratings_answers_about_the_named_player(np_ctx: AnswerContext) -> None:
    """The ratings are the named player's, never the team's NetPoints leader
    with SGA dropped."""
    answer = netpoints_ratings(np_ctx, Reading(who=Who(kind="player", players=("Shai Gilgeous-Alexander",)))).text or ""
    assert answer.startswith("Shai Gilgeous-Alexander")
    assert "468.3" in answer and "403.9" in answer and "64.4" in answer


def test_netpoints_ratings_includes_the_play_type_fingerprint(np_ctx: AnswerContext) -> None:
    result = netpoints_ratings(np_ctx, Reading(who=Who(kind="player", players=("SGA",))))
    categories = {row["category"] for row in result.data["fingerprint"]}
    assert "two pt" in categories and "driving" in categories
    assert "total" not in categories  # the summary, reported on the headline


def test_netpoints_ratings_orders_the_fingerprint_by_magnitude(np_ctx: AnswerContext) -> None:
    rows = netpoints_ratings(np_ctx, Reading(who=Who(kind="player", players=("SGA",)))).data["fingerprint"]
    assert rows[0]["category"] == "two pt"


def test_netpoints_ratings_uses_the_string_season_type(np_ctx: AnswerContext) -> None:
    """net_points_player has its OWN string season_type; filtering it with the
    numeric one every other table uses silently matches nothing."""
    assert netpoints_ratings(np_ctx, Reading(who=Who(kind="player", players=("SGA",)))).data["headline"] is not None


def test_netpoints_ratings_headline_is_the_display_sentence_not_the_raw_row(np_ctx: AnswerContext) -> None:
    """`data["headline"]` is the display sentence under the same key every
    other reader's `data["headline"]` uses - never the raw SQL row (overall,
    offense, defense, per-100 rate, minutes, games), which the page's web
    renderer would show as a 6-number array. It is a string, and it is the
    answer's own first line."""
    result = netpoints_ratings(np_ctx, Reading(who=Who(kind="player", players=("SGA",))))
    assert isinstance(result.data["headline"], str)
    assert result.data["headline"] == (result.text or "").split("\n")[0]
    assert result.data["headline"].startswith("Shai Gilgeous-Alexander, NetPoints in the")


def test_netpoints_ratings_totals_is_a_stable_dict(np_ctx: AnswerContext) -> None:
    """The season row is `data["totals"]`, with named keys rather than
    positional ones."""
    totals = netpoints_ratings(np_ctx, Reading(who=Who(kind="player", players=("SGA",)))).data["totals"]
    assert totals == {"overall": 468.33, "offense": 403.9, "defense": 64.43, "per_100": 9.91, "minutes": 2259, "games": 68}


def test_netpoints_ratings_totals_is_none_without_a_season_row(np_ctx: AnswerContext) -> None:
    np_ctx.con.execute("DELETE FROM net_points_player")
    assert netpoints_ratings(np_ctx, Reading(who=Who(kind="player", players=("SGA",)))).data["totals"] is None


def test_netpoints_ratings_fingerprint_rows_mark_the_six_partition_categories(np_ctx: AnswerContext) -> None:
    """`partition` is read by the page's own renderer to split the Offense/
    Defense sections from the play-type detail table without a second copy of
    FINGERPRINT_PARTITION in JavaScript."""
    rows = {r["category"]: r["partition"] for r in netpoints_ratings(np_ctx, Reading(who=Who(kind="player", players=("SGA",)))).data["fingerprint"]}
    assert rows["two pt"] is True and rows["rebound"] is True and rows["foul"] is True
    assert rows["driving"] is False and rows["rim"] is False


def test_netpoints_ratings_notes_carry_the_headline_detail_and_units(np_ctx: AnswerContext) -> None:
    result = netpoints_ratings(np_ctx, Reading(who=Who(kind="player", players=("SGA",))))
    notes = result.data["notes"]
    assert any("2,259 minutes" in n and "68 games" in n for n in notes)
    assert any(n.startswith("Categories are per 100 possessions over") for n in notes)
    assert any("do not add up" in n for n in notes)


def test_netpoints_ratings_missing_season_refusal_carries_a_headline(np_ctx: AnswerContext) -> None:
    result = netpoints_ratings(np_ctx, Reading(who=Who(kind="player", players=("SGA",)), span=Span(season=2020)))
    assert result.data["headline"] == result.text


def test_netpoints_ratings_reports_a_missing_season_honestly(np_ctx: AnswerContext) -> None:
    assert "no 2020 regular season NetPoints" in (netpoints_ratings(np_ctx, Reading(who=Who(kind="player", players=("SGA",)), span=Span(season=2020))).text or "")


def test_netpoints_ratings_defaulted_season_redirects_to_a_retired_players_range(np_ctx: AnswerContext) -> None:
    """No season was named - "now" defaulted - so a player with NetPoints only
    on record in an earlier season is pointed at it instead of a flat refusal
    that reads as though the warehouse held nothing of his at all (issue #18).
    No "or ask for his career" here: a player's NetPoints ratings have no
    career span to offer."""
    np_ctx.con.execute("INSERT INTO players VALUES ('2','Old Timer')")
    np_ctx.con.execute("INSERT INTO net_points_player VALUES ('2',2020,'Regular Season',100.0,80.0,20.0,5.0,1000,50)")
    s = current_season()
    answer = netpoints_ratings(np_ctx, Reading(who=Who(kind="player", players=("Old Timer",)))).text or ""
    assert answer == f"The warehouse has no {s} regular season NetPoints for Old Timer. He last appears in 2020. The warehouse holds his 2020 regular season; name one."


def test_netpoints_ratings_a_named_season_keeps_the_plain_refusal(np_ctx: AnswerContext) -> None:
    """The season the question named is the fact the refusal is about - unlike
    the defaulted case above, redirecting it would be a different question."""
    np_ctx.con.execute("INSERT INTO players VALUES ('2','Old Timer')")
    np_ctx.con.execute("INSERT INTO net_points_player VALUES ('2',2020,'Regular Season',100.0,80.0,20.0,5.0,1000,50)")
    answer = netpoints_ratings(np_ctx, Reading(who=Who(kind="player", players=("Old Timer",)), span=Span(season=2021))).text or ""
    assert answer == "The warehouse has no 2021 regular season NetPoints for Old Timer."


def test_netpoints_ratings_without_a_player_are_declined(np_ctx: AnswerContext) -> None:
    with pytest.raises(Unsupported):
        netpoints_ratings(np_ctx, Reading())


def test_netpoints_ratings_fingerprint_defaults_to_per_100_possessions(np_ctx: AnswerContext) -> None:
    """Season totals mostly rank by playing time; per 100 possessions is the
    unit that compares players, which is what the fingerprint is for."""
    result = netpoints_ratings(np_ctx, Reading(who=Who(kind="player", players=("SGA",))))
    two_pt = next(r for r in result.data["fingerprint"] if r["category"] == "two pt")
    assert two_pt["total"] == pytest.approx(250.9 / 4725 * 100, rel=1e-3)
    assert two_pt["total_season_total"] == 250.9  # the raw total is still available
    assert "per 100 possessions" in (result.text or "")


def test_netpoints_ratings_falls_back_to_totals_without_a_possession_count(np_ctx: AnswerContext) -> None:
    # No possession count: report totals and say so, rather than dividing by
    # nothing or showing an unlabeled unit.
    np_ctx.con.execute("UPDATE net_points_player_fingerprint SET total_poss = NULL")
    result = netpoints_ratings(np_ctx, Reading(who=Who(kind="player", players=("SGA",))))
    assert next(r for r in result.data["fingerprint"] if r["category"] == "two pt")["total"] == 250.9
    assert "season totals" in (result.text or "")


def test_netpoints_ratings_gives_defense_its_own_section(np_ctx: AnswerContext) -> None:
    """A single table sorted by total renders the defensive profile invisible:
    for SGA, `turnover` carries the largest defensive value of any play type
    and lands 15th of 21 by total, below categories whose defense is ~0."""
    np_ctx.con.execute("UPDATE net_points_player_fingerprint SET turnover_d_net_pts = 170.9, turnover_t_net_pts = 15.7")
    answer = netpoints_ratings(np_ctx, Reading(who=Who(kind="player", players=("SGA",)))).text or ""
    assert "Offense," in answer and "Defense," in answer
    defense_section = answer.split("Defense,")[1]
    assert defense_section.strip().splitlines()[1].strip().startswith("turnover")


def test_netpoints_ratings_sorts_each_section_by_its_own_side(np_ctx: AnswerContext) -> None:
    np_ctx.con.execute("UPDATE net_points_player_fingerprint SET turnover_d_net_pts = 170.9, turnover_o_net_pts = 0.5")
    answer = netpoints_ratings(np_ctx, Reading(who=Who(kind="player", players=("SGA",)))).text or ""
    offense_section = answer.split("Offense,")[1].split("Defense,")[0]
    assert not offense_section.strip().splitlines()[1].strip().startswith("turnover")


def test_netpoints_ratings_warns_that_the_detail_slices_overlap(np_ctx: AnswerContext) -> None:
    # A driving layup at the rim counts in driving, layup AND rim, so the
    # detail rows are not additive - unlike the six partition categories.
    assert "do not add up" in (netpoints_ratings(np_ctx, Reading(who=Who(kind="player", players=("SGA",)))).text or "")


def test_the_six_core_categories_partition_the_total(np_ctx: AnswerContext) -> None:
    """two_pt, three_pt, free_throw, turnover, rebound and foul sum exactly to
    the offensive and defensive totals - verified against the separately stored
    net_points_player.offense/.defense for every top-minutes player in 2026,
    max deviation 0.005. The other categories are overlapping slices."""
    from association.nba.netpoints import FINGERPRINT_PARTITION

    assert set(FINGERPRINT_PARTITION) == {"two_pt", "three_pt", "free_throw", "turnover", "rebound", "foul"}
    answer = netpoints_ratings(np_ctx, Reading(who=Who(kind="player", players=("SGA",)))).text or ""
    offense = answer.split("Offense,")[1].split("Defense,")[0]
    listed = [ln.split()[-1] for ln in offense.strip().splitlines()[1:] if ln.strip() and not ln.strip().startswith("-")]
    assert pytest.approx(sum(float(v) for v in listed[:-1]), rel=1e-6) == float(listed[-1])


def test_overlapping_play_types_are_kept_out_of_the_summing_column(np_ctx: AnswerContext) -> None:
    answer = netpoints_ratings(np_ctx, Reading(who=Who(kind="player", players=("SGA",)))).text or ""
    offense = answer.split("Offense,")[1].split("Defense,")[0]
    assert "rim" not in offense and "layup" not in offense  # detail, not partition
    assert "do not add up" in answer and "rim" in answer.split("Play-type detail")[1]


def _sc_ctx_add_games(sc_ctx: AnswerContext, rows: list[tuple[str, str]]) -> None:
    """Two player_game_log/games rows per ``(event_id, tip)`` pair, for the
    order-scoping tests below - Curry starts, plays, on team 9 vs team 13,
    which is all the relation's played guard and window need."""
    sc_ctx.con.executemany(
        "INSERT INTO player_game_log VALUES ('1',?,2,?,'9','13',TRUE,FALSE,30,?,NULL)",
        [(current_season(), event, tip) for event, tip in rows],
    )
    sc_ctx.con.executemany(
        "INSERT INTO games VALUES (?,?,2,?,'9','13',110,100,'9')",
        [(event, current_season(), tip) for event, tip in rows],
    )


def test_shot_chart_scopes_to_a_single_game_when_order_is_set(sc_ctx: AnswerContext) -> None:
    """ "a shot chart of Curry's last regular season game" charts that game's
    14 attempts, never the whole season's 803."""
    _sc_ctx_add_games(sc_ctx, [("e1", "2026-01-01T00:00Z"), ("eLast", "2026-04-13T00:30Z")])
    sc_ctx.con.execute("INSERT INTO shot_chart VALUES ('1',?,2,'eLast',1,'2:00',TRUE,'Jump Shot',25,26,3,'26-foot three point jumper')", [current_season()])
    answer = shot_chart(sc_ctx, Reading(who=Who(kind="player", players=("Stephen Curry",)), window=Window(order="recent"))).text or ""
    assert "1/1 made" in answer  # only the one shot from the last game
    assert "eLast" in answer


def test_shot_chart_order_first_picks_the_earliest_game(sc_ctx: AnswerContext) -> None:
    _sc_ctx_add_games(sc_ctx, [("e1", "2026-01-01T00:00Z"), ("eLast", "2026-04-13T00:30Z")])
    assert "e1" in (shot_chart(sc_ctx, Reading(who=Who(kind="player", players=("Stephen Curry",)), window=Window(order="first"))).text or "")


def test_shot_chart_an_empty_single_game_names_the_game(sc_ctx: AnswerContext) -> None:
    """ISSUES.md #105: where the window pinned one game and it holds no
    located shot, the sentence names that game - its bare id here, since
    the fixture's games carry nothing to label one by - as a drawn chart's
    context names it, rather than "the given filters"; and nothing
    redirects, since the game, not the season, is what emptied the read."""
    _sc_ctx_add_games(sc_ctx, [("e1", "2026-01-01T00:00Z"), ("eLast", "2026-04-13T00:30Z")])
    result = shot_chart(sc_ctx, Reading(who=Who(kind="player", players=("Stephen Curry",)), window=Window(order="recent")))
    assert result.text == "No shots found for Stephen Curry (game eLast)."
    assert result.artifacts == []


def test_shot_chart_without_order_still_covers_the_season(sc_ctx: AnswerContext) -> None:
    # Both of the fixture's shots (one made, one missed), not one game's worth.
    assert "1/2 made" in (shot_chart(sc_ctx, Reading(who=Who(kind="player", players=("Stephen Curry",)))).text or "")


def test_games_counted_supports_fouls(con: AnswerContext) -> None:
    con.con.execute("ALTER TABLE player_box_stats ADD COLUMN fouls INTEGER")
    con.con.execute("UPDATE player_box_stats SET fouls = 6 WHERE athlete_id = '1'")
    result = games_counted(con, Reading(lines=(Line(measure="fouls", value=6, as_typed="6 fouls", keyed=True),), measure=Measure(key="fouls", as_typed="fouls")))
    assert "6+ fouls" in (result.text or "")
    assert result.data["leaders"][0]["player"] == "Luka Doncic"


def _add_per_game_netpoints(ctx: AnswerContext) -> None:
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


def test_netpoints_ratings_scopes_to_one_game_when_order_is_set(np_ctx: AnswerContext) -> None:
    """ "netpoints from his last regular season game" is that one game, never
    the whole season's 43."""
    _add_per_game_netpoints(np_ctx)
    result = netpoints_ratings(np_ctx, Reading(who=Who(kind="player", players=("SGA",)), window=Window(order="recent")))
    # eLast tips at 2026-04-13T00:30Z - 8:30pm Eastern on the 12th, the day it
    # was played, never the UTC day.
    assert result.data["game"]["date"] == "2026-04-12"
    answer = result.text or ""
    assert "6.3 total" in answer and "most recent" in answer
    assert "Offense," not in answer  # not the season breakdown
    # A single game's headline is the totals sentence alone - "Ask for a
    # fingerprint..." and the possession/win-probability detail sit beneath
    # it, so the headline does not carry the whole multi-line answer.
    assert result.data["headline"] == answer.split("\n")[0]
    assert "possessions" in result.data["notes"][0] and "win probability added" in result.data["notes"][0]


def test_netpoints_ratings_order_first_picks_the_earliest_game(np_ctx: AnswerContext) -> None:
    _add_per_game_netpoints(np_ctx)
    # 2025-10-22T00:00Z is 8pm Eastern on October 21st.
    assert netpoints_ratings(np_ctx, Reading(who=Who(kind="player", players=("SGA",)), window=Window(order="first"))).data["game"]["date"] == "2025-10-21"


def test_single_game_netpoints_points_at_the_fingerprint_for_the_split(np_ctx: AnswerContext) -> None:
    """A single game's NetPoints is a TOTAL with no play-type split of its
    own, so it points at the fingerprint for one - saying nothing would read
    as the split not existing."""
    _add_per_game_netpoints(np_ctx)
    assert "Ask for a fingerprint of that game" in (netpoints_ratings(np_ctx, Reading(who=Who(kind="player", players=("SGA",)), window=Window(order="recent"))).text or "")


def test_netpoints_ratings_without_a_window_give_the_season(np_ctx: AnswerContext) -> None:
    _add_per_game_netpoints(np_ctx)
    assert "Offense," in (netpoints_ratings(np_ctx, Reading(who=Who(kind="player", players=("SGA",)))).text or "")


def test_single_game_netpoints_is_declined_without_the_optin_table(np_ctx: AnswerContext) -> None:
    # net_points_player_game only exists if fetched with
    # --include-net-points-daily; say so rather than answering for the season.
    with pytest.raises(Unsupported):
        netpoints_ratings(np_ctx, Reading(who=Who(kind="player", players=("SGA",)), window=Window(order="recent")))


# ---------------- the cells a shape states ----------------


def test_cells_unhonored_names_a_game_cell_the_shape_does_not_state() -> None:
    """A game cell a shape's words do not state is unhonored, never silently
    dropped: a chart of "his last game" is not the whole season, NetPoints
    for "his last game" is not all 43, and a record "over their last 10" is
    not the full season."""
    for shape, scope in [
        (GAMES_COUNTED, Reading(cuts=Cuts(date="2026-04-12"))),
        (TEAM_RECORD, Reading(window=Window(order="recent"))),
        (PLAYER_RANKING, Reading(window=Window(order="first"))),
        # A history has no game relation behind it: a date is unhonored.
        (SEASON_HISTORY, Reading(cuts=Cuts(date="2026-04-12"))),
    ]:
        # Each shape's words state none of these: its reader steps aside,
        # and the planner refuses what the relation cannot honor.
        assert cells_unhonored(scope, shape), shape


def test_cells_unhonored_is_empty_for_the_game_cells_a_shape_states() -> None:
    # A player's log states a date and a window.
    assert [cell for cell, _ in cells_unhonored(Reading(cuts=Cuts(date="2026-04-12"), window=Window(order="recent")), PLAYER_LOG)] == []
    # A shot chart and a shot distance state the window.
    assert [cell for cell, _ in cells_unhonored(Reading(window=Window(order="recent")), SHOT_CHART)] == []
    assert [cell for cell, _ in cells_unhonored(Reading(window=Window(order="first")), SHOT_DISTANCE)] == []


def test_cells_unhonored_names_both_season_types_only_where_the_shape_does_not_state_them() -> None:
    """Both season types in one read (``Span.both``: "last N games" naming no
    season type, or "including the playoffs") are stated by a player's log,
    a line over his games and a count of games over a line - an aggregate
    reads one combined ``season_type IN (2, 3)``, with no rows to interleave
    as the log's row-merge does; every other shape leaves the cell
    unhonored, refused rather than silently ignored."""
    assert [cell for cell, _ in cells_unhonored(Reading(span=Span(both=True), window=Window(order="recent", count=5)), PLAYER_LOG)] == []
    assert [cell for cell, _ in cells_unhonored(Reading(span=Span(both=True)), PointShape("player_games", "scalar", "line"))] == []
    # A count of games over a line: the relation honors the cell for a named
    # player, and the count's own words state it (``cells_stated``).
    assert [cell for cell, _ in cells_unhonored(Reading(span=Span(both=True)), GAMES_COUNTED)] == []
    assert [cell for cell, _ in cells_unhonored(Reading(span=Span(both=True)), PLAYER_RANKING)] == ["both"]
    # A game's high's words do not state it: its reader steps aside and the
    # compiler's sentence, which does, answers.
    assert [cell for cell, _ in cells_unhonored(Reading(span=Span(both=True)), GAME_HIGHS)] == ["both"]


def test_cells_unhonored_is_empty_for_a_reading_with_no_cells() -> None:
    assert [cell for cell, _ in cells_unhonored(Reading(), PLAYER_RANKING)] == []


def test_shot_distance_scopes_to_one_game(sc_ctx: AnswerContext) -> None:
    # A second game whose shots must NOT be counted.
    _sc_ctx_add_games(sc_ctx, [("e1", "2026-04-13T00:30Z"), ("e2", "2026-01-01T00:00Z")])
    sc_ctx.con.execute("INSERT INTO shot_chart VALUES ('1',?,2,'e2',1,'2:00',TRUE,'Jump Shot',25,40,3,'40-foot three point jumper')", [current_season()])
    answer = shot_distance(sc_ctx, Reading(who=Who(kind="player", players=("Stephen Curry",)), window=Window(order="recent"))).text or ""
    # e1 tips at 2026-04-13T00:30Z - 8:30pm Eastern on the 12th, the day it was
    # played, never the UTC day.
    assert "most recent game (2026-04-12)" in answer
    assert "2 attempts" in answer  # the fixture's two shots in e1, not the third in e2


# ---------------- a NetPoints fingerprint ----------------


@pytest.fixture
def fp_ctx(tmp_path: Path) -> AnswerContext:
    from association.nba.netpoints import FINGERPRINT_CATEGORIES

    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    c.execute("INSERT INTO players VALUES ('1','Shai Gilgeous-Alexander'),('2','Bench Guy')")
    # `total` included: it is not a spoke, but it is the headline above the plot.
    categories = list(FINGERPRINT_CATEGORIES.values())
    columns = ", ".join(f"{category}_{side}_net_pts DOUBLE" for category in categories for side in ("o", "d", "t"))
    c.execute(f"CREATE TABLE net_points_player_fingerprint (athlete_id VARCHAR, season INTEGER, minutes DOUBLE, total_poss DOUBLE, {columns})")
    values = ", ".join(["1.0"] * (3 * len(categories)))
    for athlete_id, minutes in (("1", 2000.0), ("2", 1200.0)):
        c.execute(f"INSERT INTO net_points_player_fingerprint VALUES ('{athlete_id}', {current_season()}, {minutes}, 4000.0, {values})")

    # The per-game half: long, not wide, and needing games (for the date a
    # game is picked by) and net_points_player_game (for the possessions the
    # percentile pool is floored on).
    c.execute("CREATE TABLE games (event_id VARCHAR, season INTEGER, season_type INTEGER, date VARCHAR)")
    c.execute("CREATE TABLE net_points_player_game (event_id VARCHAR, athlete_id VARCHAR, season INTEGER, season_type INTEGER, t_poss DOUBLE)")
    game_fingerprint_columns = "event_id VARCHAR, season INTEGER, season_type INTEGER, team_id VARCHAR, athlete_id VARCHAR, category VARCHAR"
    c.execute(f"CREATE TABLE net_points_player_game_fingerprint ({game_fingerprint_columns}, o_net_pts DOUBLE, d_net_pts DOUBLE, t_net_pts DOUBLE)")
    for event_id, date in (("g1", "2026-01-05"), ("g2", "2026-03-20")):
        c.execute("INSERT INTO games VALUES (?, ?, 2, ?)", [event_id, current_season(), date])
        for athlete_id in ("1", "2"):
            c.execute("INSERT INTO net_points_player_game VALUES (?, ?, ?, 2, 60.0)", [event_id, athlete_id, current_season()])
            for category in categories:
                # g2 is the bigger game, so "most recent" and "first" differ in
                # the numbers as well as in the date.
                size = 2.0 if event_id == "g2" else 1.0
                c.execute(
                    "INSERT INTO net_points_player_game_fingerprint VALUES (?, ?, 2, '9', ?, ?, ?, ?, ?)",
                    [event_id, current_season(), athlete_id, category, size, size, size * 2],
                )
    return AnswerContext(con=c, out_dir=tmp_path)


def test_fingerprint_writes_a_file_and_reports_its_path(fp_ctx: AnswerContext) -> None:
    result = netpoints_fingerprint(fp_ctx, Reading(who=Who(kind="player", players=("Shai Gilgeous-Alexander",))))
    assert "Rendered NetPoints fingerprint (total) for Shai Gilgeous-Alexander" in result.text
    assert list(fp_ctx.out_dir.glob("*.html"))


def test_fingerprint_honors_the_side(fp_ctx: AnswerContext) -> None:
    assert netpoints_fingerprint(fp_ctx, Reading(who=Who(kind="player", players=("Shai",)), measure=Measure(side="defense"))).data["side"] == "defense"


def test_a_nonsense_side_never_reaches_the_fingerprint() -> None:
    """The Measure refuses a side that is neither offense nor defense."""
    with pytest.raises(ValueError, match="side"):
        Measure(side="sideways")  # type: ignore[arg-type]


def test_fingerprint_defaults_to_the_current_season(fp_ctx: AnswerContext) -> None:
    assert netpoints_fingerprint(fp_ctx, Reading(who=Who(kind="player", players=("Shai",)))).data["season"] == current_season()


def test_fingerprint_draws_the_game_that_was_asked_for(fp_ctx: AnswerContext) -> None:
    """ "his last game" draws that game, and the
    plot is titled with its date rather than with the season - so a reader can
    tell which game they are looking at."""
    result = netpoints_fingerprint(fp_ctx, Reading(who=Who(kind="player", players=("Shai",)), window=Window(order="recent")))

    assert result.data["scope"] == "game"
    assert "2026-03-20" in result.text
    assert list(fp_ctx.out_dir.glob("*_recent_game_*.html"))


def test_the_other_end_of_the_season_draws_a_different_game(fp_ctx: AnswerContext) -> None:
    result = netpoints_fingerprint(fp_ctx, Reading(who=Who(kind="player", players=("Shai",)), window=Window(order="first")))

    assert "2026-01-05" in result.text


def test_a_game_plot_is_not_captioned_as_a_per_100_rate(fp_ctx: AnswerContext) -> None:
    """A single game's numbers are that game's net points - a per-100 rate over
    ~30 possessions turns one made three into a league-leading season figure.
    The caption has to say which quantity is on the page, or the plot is the
    right shape under the wrong claim."""
    netpoints_fingerprint(fp_ctx, Reading(who=Who(kind="player", players=("Shai",)), window=Window(order="recent")))
    page = next(fp_ctx.out_dir.glob("*_recent_game_*.html")).read_text()

    assert "per 100 poss" not in page
    assert "in this game" in page


def test_a_season_plot_still_says_per_100(fp_ctx: AnswerContext) -> None:
    """The other half of the check above: a unit label that stopped appearing
    anywhere would pass it."""
    netpoints_fingerprint(fp_ctx, Reading(who=Who(kind="player", players=("Shai",))))
    page = next(p for p in fp_ctx.out_dir.glob("*.html") if "_game_" not in p.name).read_text()

    assert "per 100 poss" in page


def test_a_fingerprint_for_a_particular_date_still_says_it_cannot(fp_ctx: AnswerContext) -> None:
    """`date` is a different question from `order`: a calendar date names one
    game, where a window picks a player's first or last game. Answering one
    with the other is exactly the substitution the fingerprint refuses."""
    answer = netpoints_fingerprint(fp_ctx, Reading(who=Who(kind="player", players=("Shai",)), cuts=Cuts(date="2026-01-02"))).text

    assert "not yet for a particular date" in answer
    assert not list(fp_ctx.out_dir.glob("*.html"))


def test_fingerprint_declares_the_game_scoping_it_handles(fp_ctx: AnswerContext) -> None:
    # It handles them by refusing; the planner must therefore NOT decline the
    # request out from under it, for a refusal naming only the cell.
    assert [cell for cell, _ in cells_unhonored(Reading(who=Who(kind="player", players=("Shai",)), cuts=Cuts(date="2026-01-02"), window=Window(order="recent")), NETPOINTS_FINGERPRINT)] == []
    # The game-scoping pair specifically - the relation also carries an
    # opponent, a venue, a career and a teammate, none of which a
    # fingerprint can narrow to.
    assert cells_stated(NETPOINTS_FINGERPRINT) == {"window", "date"}


def test_fingerprint_without_a_player_is_declined(fp_ctx: AnswerContext) -> None:
    with pytest.raises(Unsupported):
        netpoints_fingerprint(fp_ctx, Reading())


def test_fingerprint_reports_an_unknown_player(fp_ctx: AnswerContext) -> None:
    assert "No player found" in netpoints_fingerprint(fp_ctx, Reading(who=Who(kind="player", players=("Nobody At All",)))).text


def test_fingerprint_reports_a_season_with_no_data(fp_ctx: AnswerContext) -> None:
    # Nothing has a better source than the table this just read. (A season
    # under NetPoints' floor, 2019, is the coverage check's refusal first.)
    assert "no NetPoints fingerprint data for season 2020" in netpoints_fingerprint(fp_ctx, Reading(who=Who(kind="player", players=("Shai",)), span=Span(season=2020))).text


def test_fingerprint_plots_two_players_on_one_radar(fp_ctx: AnswerContext) -> None:
    """Two polygons on shared axes IS the comparison, so "compare their
    fingerprints" is one fingerprint over the subject's two players."""
    result = netpoints_fingerprint(fp_ctx, Reading(who=Who(kind="pair", players=("Shai Gilgeous-Alexander", "Bench Guy"))))
    assert result.data["players"] == ["Shai Gilgeous-Alexander", "Bench Guy"]
    assert "Shai Gilgeous-Alexander vs Bench Guy" in result.text


def test_fingerprint_does_not_compare_a_player_with_himself(fp_ctx: AnswerContext) -> None:
    # Two spellings of one name drew one polygon over itself and called it a
    # comparison.
    assert netpoints_fingerprint(fp_ctx, Reading(who=Who(kind="pair", players=("Shai Gilgeous-Alexander", "Shai")))).data["players"] == ["Shai Gilgeous-Alexander"]


def test_a_shot_chart_reports_the_file_it_wrote_as_an_artifact(sc_ctx: AnswerContext) -> None:
    """The chart's path is a value on the answer (``artifacts``), not only a
    sentence in the message."""
    result = shot_chart(sc_ctx, Reading(who=Who(kind="player", players=("Stephen Curry",)), span=Span(season=current_season())))
    assert [a.kind for a in result.artifacts] == ["shot_chart"]
    assert result.artifacts[0].path.exists()
    assert result.artifacts[0].name.endswith(".html")
    assert result.data["path"] == str(result.artifacts[0].path)


def test_a_shot_chart_that_drew_nothing_reports_no_artifact(sc_ctx: AnswerContext) -> None:
    """ "No shots found" is a real answer, not a failure - but there is no file,
    and claiming one would give a caller a path that does not exist."""
    result = shot_chart(sc_ctx, Reading(who=Who(kind="player", players=("Stephen Curry",)), span=Span(season=2010)))
    assert result.artifacts == []
    assert result.data["path"] is None


def test_a_fingerprint_reports_the_file_it_wrote_as_an_artifact(fp_ctx: AnswerContext) -> None:
    result = netpoints_fingerprint(fp_ctx, Reading(who=Who(kind="player", players=("Shai Gilgeous-Alexander",))))
    assert [a.kind for a in result.artifacts] == ["fingerprint"]
    assert result.artifacts[0].path.exists()


def test_a_reader_that_writes_nothing_reports_no_artifacts(lb_con: AnswerContext) -> None:
    """The default has to be empty, not unset: a caller iterates artifacts on
    every answer, and a None here would be an AttributeError on the common path."""
    assert player_ranking(lb_con, Reading(measure=Measure(key="points", as_typed="points"))).artifacts == []


@pytest.mark.parametrize(
    ("shape", "scope"),
    [
        # A player's log and a shot distance answer the real queries behind
        # these ("jaylen brown last 8 games vs pistons", "Jaylen Brown's
        # average shot distance against the Detroit Pistons"), so the
        # refusal is checked on shapes that cannot narrow that way.
        (NETPOINTS_RATINGS, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), companions=(Companion(player="curry", predicate="absent"),))),
        (SEASON_HISTORY, Reading(who=Who(kind="player", players=("Nikola Jokic",)), cuts=Cuts(opponent="Boston Celtics"), measure=Measure(key="points", as_typed="points"))),
    ],
)
def test_cells_unhonored_names_a_narrowing_the_shape_does_not_state(shape: PointShape, scope: Reading) -> None:
    """A companion's absence on a player's NetPoints ratings, and an opponent
    on a season history, are unhonored - never answered for every game or
    every opponent."""
    assert cells_unhonored(scope, shape)


def test_cells_unhonored_is_empty_for_the_narrowings_the_player_shapes_state() -> None:
    assert [
        cell
        for cell, _ in cells_unhonored(
            Reading(
                who=Who(kind="player", players=("Jaylen Brown",)),
                cuts=Cuts(opponent="Detroit Pistons", venue="home"),
                companions=(Companion(player="x", predicate="absent"),),
                span=Span(career=True),
                window=Window(order="recent"),
            ),
            PLAYER_LOG,
        )
    ] == []
    assert [
        cell
        for cell, _ in cells_unhonored(
            Reading(
                who=Who(kind="player", players=("Evan Mobley",)), cuts=Cuts(opponent="Milwaukee Bucks", venue="away"), companions=(Companion(player="x", predicate="absent"),), span=Span(career=True)
            ),
            PointShape("player_games", "scalar", "line"),
        )
    ] == []
    assert [cell for cell, _ in cells_unhonored(Reading(who=Who(kind="player", players=("Nikola Jokic",)), span=Span(career=True)), SEASON_HISTORY)] == []
    assert [cell for cell, _ in cells_unhonored(Reading(who=Who(kind="player", players=("Jaylen Brown",)), cuts=Cuts(opponent="Detroit Pistons")), SHOT_DISTANCE)] == []


def test_shot_distance_narrows_by_opponent_and_names_it_in_the_answer(sc_ctx: AnswerContext) -> None:
    """ "Jaylen Brown's average shot distance against the Detroit Pistons":
    a shot distance narrows to the opponent on the relation and says so,
    rather than averaging every opponent."""
    _sc_ctx_add_games(sc_ctx, [("eLakers", "2026-01-01T00:00Z")])
    sc_ctx.con.execute("INSERT INTO shot_chart VALUES ('1',?,2,'eLakers',1,'2:00',TRUE,'Jump Shot',25,40,3,'40-foot three point jumper')", [current_season()])
    # A second game, against a different opponent, whose shot must NOT count.
    sc_ctx.con.executemany(
        "INSERT INTO player_game_log VALUES ('1',?,2,'eOther','9','99',TRUE,FALSE,30,?,NULL)",
        [(current_season(), "2026-02-01T00:00Z")],
    )
    sc_ctx.con.execute("INSERT INTO games VALUES ('eOther',?,2,'2026-02-01T00:00Z','9','99',110,100,'9')", [current_season()])
    sc_ctx.con.execute("INSERT INTO shot_chart VALUES ('1',?,2,'eOther',1,'2:00',TRUE,'Jump Shot',25,26,3,'26-foot three point jumper')", [current_season()])
    answer = shot_distance(sc_ctx, Reading(who=Who(kind="player", players=("Stephen Curry",)), cuts=Cuts(opponent="Los Angeles Lakers"))).text or ""
    assert "Los Angeles Lakers" in answer
    assert "over 1 attempts" in answer  # eLakers alone, not eOther's shot


def test_a_teams_period_total_states_the_opponent_and_the_period() -> None:
    # Its words state the opponent and the period.
    assert [cell for cell, _ in cells_unhonored(Reading(who=Who(kind="team", teams=("Philadelphia 76ers",)), cuts=Cuts(opponent="Boston Celtics"), period=Period(number=4)), TEAM_PERIOD_TOTAL)] == []


def test_no_shape_states_a_playoff_round() -> None:
    """Nothing in the warehouse records a round or a series game number."""
    from association.query import compose

    assert not any("round" in cells_stated(shape) for shape in compose._ROUTES)
    assert [cell for cell, _ in cells_unhonored(Reading(who=Who(kind="pair", players=("Jayson Tatum", "Jaylen Brown")), cuts=Cuts(round="finals")), PLAYER_COMPARISON)] == ["round"]
    assert [cell for cell, _ in cells_unhonored(Reading(who=Who(kind="player", players=("Jayson Tatum",)), cuts=Cuts(round="finals")), PointShape("player_games", "scalar", "line"))] == ["round"]


def test_a_leaderboard_refuses_a_position_group_subject_for_the_compiler(lb_con: AnswerContext) -> None:
    """F056 ("highest 3 point percentage in a season. by a shooting guard
    with at least 400 attempts"): a position group is part of the league no
    season-line metric narrows to. Refused there, so the compiler reads the
    group over the box scores - never a named-player refusal of the phrase."""
    from dataclasses import replace

    from association.query.compose.rankings import read_leaderboard
    from association.query.point import read_point

    reading = Reading(**(Reading(measure=Measure(key="points", as_typed="points"))).filters(), asked=PLAYER_RANKING, subject=Subject("position", position="SG"))
    # The compiler's point reads the group over the box scores, not the season line.
    point = read_point(reading, "highest points per game by a shooting guard")
    assert (point.relation, point.who.position, point.read_as.relation) == ("everyone", "SG", "player_games")
    # And the season line's reader, handed the group on its own relation, steps aside for it.
    planned = plan(point)
    assert isinstance(planned, Query)
    on_the_season_line = replace(planned, source="seasons")
    assert on_the_season_line.position == "SG"
    assert read_leaderboard(lb_con.con, on_the_season_line) is None


def test_a_ranking_names_the_season_line_as_its_relation(lb_con: AnswerContext) -> None:
    """A ranking is read from the season line, and its Result says so
    (``Span.source``), as the line, the history and the comparison do - the
    relation is part of what a Result is (ROADMAP-TYPES.md), so ``say()``
    reaches its sayer by asking."""
    from association.query.compose.rankings import read_leaderboard
    from association.query.compose.say import say, say_leaderboard
    from association.query.point import read_point
    from association.query.result import Result

    reading = Reading(**(Reading(measure=Measure(key="points", as_typed="points"), span=Span(season=2024))).filters(), asked=PLAYER_RANKING, subject=Subject("everyone"))
    planned = plan(read_point(reading, "who led the league in points in 2024"))
    assert isinstance(planned, Query) and planned.source == "seasons"
    result = read_leaderboard(lb_con.con, planned)
    assert isinstance(result, Result) and result.span.source == "seasons"
    assert say(result).text == say_leaderboard(result).text


def test_a_stated_minimum_is_the_season_lines_qualifier_and_no_decision(lb_con: AnswerContext) -> None:
    """ISSUES.md #377: "minimum 69 games" is the qualifier the season line
    ranks under, in place of its default 20 - Curry's 68 games leave him
    out - said as the default is, and recorded as what the words asked, not
    as the reader's decision. A floor in a unit the metric does not qualify
    by (points per game counts games, not attempts) is the game-level
    ranking's question, which refuses it by name."""
    from dataclasses import replace

    from association.query.compose.rankings import read_leaderboard
    from association.query.compose.say import say_leaderboard
    from association.query.point import read_point
    from association.query.reading import Minimum, PointRefused
    from association.query.result import RankingFacts, Result

    reading = Reading(**(Reading(measure=Measure(key="points", as_typed="points"))).filters(), asked=PLAYER_RANKING, subject=Subject("everyone"))
    stated = replace(reading, minimum=Minimum(count=69, unit="games", word="games"))
    planned = plan(read_point(stated, "points per game leaders minimum 69 games"))
    assert isinstance(planned, Query) and planned.source == "seasons" and planned.minimum == stated.minimum
    result = read_leaderboard(lb_con.con, planned)
    assert isinstance(result, Result)
    assert [row["key"] for row in result.grouped.rows] == ["Luka Doncic"]  # type: ignore[union-attr]
    assert not any(each.kind == "minimum" for each in result.decisions)
    assert isinstance(result.facts, RankingFacts) and result.facts.stated_minimum == (69, "games")
    assert say_leaderboard(result).text == f"Luka Doncic led the league in points per game in the {current_season()} regular season (minimum 69 games), at 33.5."
    with pytest.raises(PointRefused) as refused:
        read_point(replace(reading, minimum=Minimum(count=100, unit="attempts", word="attempts")), "points per game leaders minimum 100 attempts")
    assert refused.value.cause.kind == "ranking_floor_unit"


def test_a_zero_threshold_is_refused_rather_than_counting_every_game(con: AnswerContext) -> None:
    """A line at 0 ("most 3 pointers made since 2020" read as a threshold of 0) keeps no games past it."""
    assert (
        games_counted(con, Reading(lines=(Line(measure="points", value=0, as_typed="0 points", keyed=True),), measure=Measure(key="points", as_typed="points"))).text
        == "A threshold of 0 counts every game - there is no line there to keep games past."
    )


@pytest.mark.parametrize(
    ("shape", "scope"),
    [
        (PointShape("player_games", "scalar", "line"), Reading(who=Who(kind="player", players=("Joe Ingles",)), split="starter_bench")),
        (PLAYER_RANKING, Reading(measure=Measure(key="points", as_typed="points"), span=Span(since=2020))),
    ],
)
def test_a_split_or_a_range_is_refused_where_nothing_honors_it(shape: PointShape, scope: Reading) -> None:
    assert cells_unhonored(scope, shape)


# ---------------- one player's games: his log and his line, narrowed ----------------

_BOX_COLUMNS = (
    "event_id VARCHAR, season INTEGER, season_type INTEGER, team_id VARCHAR, opponent_team_id VARCHAR, athlete_id VARCHAR, did_not_play BOOLEAN, "
    "minutes INTEGER, points INTEGER, rebounds INTEGER, assists INTEGER, steals INTEGER, blocks INTEGER, turnovers INTEGER, fouls INTEGER, plusMinus INTEGER, "
    "fieldGoalsMade INTEGER, fieldGoalsAttempted INTEGER, threePointFieldGoalsMade INTEGER, threePointFieldGoalsAttempted INTEGER, "
    "freeThrowsMade INTEGER, freeThrowsAttempted INTEGER, offensiveRebounds INTEGER, defensiveRebounds INTEGER"
)


def _box(
    event: str,
    season: int,
    team: str,
    opponent: str,
    athlete: str,
    *,
    dnp: bool = False,
    minutes: int | None = 30,
    pts: int = 0,
    reb: int = 0,
    ast: int = 0,
    ftm: int = 0,
    fta: int = 0,
    season_type: int = 2,
) -> tuple[Any, ...]:
    """One player_box_stats row. ``minutes=None`` is the empty line ESPN leaves
    (listed as played, no minutes, every stat zero); ``dnp`` is a did-not-play
    entry, whose stats are NULL."""
    if dnp:
        return (event, season, season_type, team, opponent, athlete, True, *([None] * 17))
    return (event, season, season_type, team, opponent, athlete, False, minutes, pts, reb, ast, 1, 0, 2, 3, 0, pts // 2, pts, 0, 0, ftm, fta, 0, 0)


_PG_LOG_VIEW_SELECT = (
    "SELECT pbs.*, p.display_name AS player_name, g.date AS game_date, t.abbreviation AS team_abbr, o.abbreviation AS opponent_abbr "
    "FROM player_box_stats pbs LEFT JOIN players p ON p.athlete_id = pbs.athlete_id LEFT JOIN games g ON g.event_id = pbs.event_id AND g.season = pbs.season "
    "LEFT JOIN teams t ON t.team_id = pbs.team_id LEFT JOIN teams o ON o.team_id = pbs.opponent_team_id"
)
"""``pg_ctx``'s log: the warehouse's own view, joins and all - keyed on season
as well as event_id, so the phantom 1993 row joins its own games row only.
A test that adds the filled view rebuilds the log over it, as ``data load``
does."""


@pytest.fixture
def pg_ctx(tmp_path: Path) -> AnswerContext:
    """A Warriors season in miniature, built to exercise every narrowing. ``s``
    is the current season.

    ====  =====  ==============  ====================  =============
    game  season  matchup         tip (UTC)             Eastern date
    ====  =====  ==============  ====================  =============
    e1    s      BOS at GS       {s-1}-11-02T00:30Z    {s-1}-11-01
    e2    s      GS at DET       {s-1}-12-02T00:30Z    {s-1}-12-01
    e3    s      DET at GS       {s}-01-10T20:00Z      {s}-01-10
    e4    s      GS at LAL       {s}-02-10T03:00Z      {s}-02-09
    e6    s      BOS at GS       {s}-03-01T00:30Z      {s}-02-28
    e5    s-1    DET at GS       {s-1}-03-01T00:30Z    {s-1}-02-28
    e7    s-1    DET at BOS      {s-1}-02-01T00:30Z
    e8    1995   CHI at DET, and its phantom copy under 1993
    ====  =====  ==============  ====================  =============

    Podziemski plays e1, e2, e3 and e5, is DNP in e4 and has an empty line in
    e6. Stephen Curry plays e1 and e5, is DNP in e3, and has no row in e2 or e4.
    Seth Curry was a Celtic last season (e7) and joins the Warriors at e3.
    Kuminga ended last season a Warrior (e5) and first appears this one at e3.
    """
    s = current_season()
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    c.execute(
        "INSERT INTO players VALUES ('10','Brandin Podziemski'),('11','Stephen Curry'),('12','Seth Curry'),('13','Jaylen Brown'),('14','Dell Curry'),('15','Jonathan Kuminga'),('20','Michael Jordan')"
    )
    c.execute("CREATE TABLE teams (team_id VARCHAR, abbreviation VARCHAR, display_name VARCHAR)")
    c.execute("INSERT INTO teams VALUES ('1','GS','Golden State Warriors'),('2','BOS','Boston Celtics'),('3','DET','Detroit Pistons'),('4','LAL','Los Angeles Lakers'),('5','CHI','Chicago Bulls')")
    c.execute(
        "CREATE TABLE games (event_id VARCHAR, season INTEGER, season_type INTEGER, date VARCHAR, home_team_id VARCHAR, away_team_id VARCHAR, "
        "home_score INTEGER, away_score INTEGER, winner_team_id VARCHAR)"
    )
    c.executemany(
        "INSERT INTO games VALUES (?, ?, 2, ?, ?, ?, ?, ?, ?)",
        [
            ("e1", s, f"{s - 1}-11-02T00:30Z", "1", "2", 110, 100, "1"),
            ("e2", s, f"{s - 1}-12-02T00:30Z", "3", "1", 99, 105, "1"),
            ("e3", s, f"{s}-01-10T20:00Z", "1", "3", 101, 120, "3"),
            ("e4", s, f"{s}-02-10T03:00Z", "4", "1", 100, 90, "4"),
            ("e6", s, f"{s}-03-01T00:30Z", "1", "2", 100, 95, "1"),
            ("e5", s - 1, f"{s - 1}-03-01T00:30Z", "1", "3", 100, 90, "1"),
            ("e7", s - 1, f"{s - 1}-02-01T00:30Z", "2", "3", 100, 90, "2"),
            ("e8", 1995, "1995-03-20T00:30Z", "3", "5", 90, 100, "5"),
            ("e8", 1993, "1995-03-20T00:30Z", "3", "5", 90, 100, "5"),
        ],
    )
    # A postseason in miniature: a three-game series vs Detroit (p1-p3, dated
    # out of insertion order so the numbering is by date) and a two-game one vs
    # the Lakers (p4-p5). Podziemski plays every one of them.
    c.executemany(
        "INSERT INTO games VALUES (?, ?, 3, ?, ?, ?, ?, ?, ?)",
        [
            ("p2", s, f"{s}-04-22T00:30Z", "3", "1", 90, 100, "1"),
            ("p1", s, f"{s}-04-20T00:30Z", "1", "3", 100, 90, "1"),
            ("p3", s, f"{s}-04-25T00:30Z", "1", "3", 100, 90, "1"),
            ("p4", s, f"{s}-05-03T00:30Z", "4", "1", 100, 90, "4"),
            ("p5", s, f"{s}-05-05T00:30Z", "1", "4", 100, 90, "1"),
        ],
    )
    c.execute(f"CREATE TABLE player_box_stats ({_BOX_COLUMNS})")
    c.executemany(
        f"INSERT INTO player_box_stats VALUES ({', '.join('?' for _ in range(24))})",
        [
            _box("e1", s, "1", "2", "10", pts=10, reb=5, ast=3, ftm=2, fta=3),
            _box("e2", s, "1", "3", "10", minutes=32, pts=20, reb=7, ast=5, ftm=4, fta=4),
            _box("e3", s, "1", "3", "10", minutes=28, pts=15, reb=4, ast=6, ftm=1, fta=2),
            _box("e4", s, "1", "4", "10", dnp=True),
            _box("e6", s, "1", "2", "10", minutes=None),
            _box("e5", s - 1, "1", "3", "10", minutes=20, pts=8, reb=2, ast=1),
            _box("e1", s, "1", "2", "11", pts=30),
            _box("e3", s, "1", "3", "11", dnp=True),
            _box("e6", s, "1", "2", "11", minutes=None),
            _box("e5", s - 1, "1", "3", "11", pts=25),
            _box("e7", s - 1, "2", "3", "12", pts=12),
            _box("e3", s, "1", "3", "12", pts=5),
            _box("e4", s, "1", "4", "12", pts=7),
            _box("e5", s - 1, "1", "3", "15", pts=10),
            _box("e3", s, "1", "3", "15", pts=9),
            _box("e1", s, "2", "1", "13", pts=25),
            _box("e7", s - 1, "2", "3", "13", pts=30),
            _box("e8", 1995, "5", "3", "20", minutes=40, pts=40),
            _box("e8", 1993, "5", "3", "20", minutes=40, pts=40),
            _box("p1", s, "1", "3", "10", pts=10, season_type=3),
            _box("p2", s, "1", "3", "10", pts=20, season_type=3),
            _box("p3", s, "1", "3", "10", pts=30, season_type=3),
            _box("p4", s, "1", "4", "10", pts=5, season_type=3),
            _box("p5", s, "1", "4", "10", pts=15, season_type=3),
        ],
    )
    c.execute(f"CREATE VIEW player_game_log AS {_PG_LOG_VIEW_SELECT}")
    c.execute(
        "CREATE TABLE player_season_stats_deduped (athlete_id VARCHAR, season INTEGER, season_type INTEGER, gamesPlayed INTEGER, avgPoints DOUBLE, points INTEGER, "
        "avgRebounds DOUBLE, totalRebounds INTEGER, avgAssists DOUBLE, assists INTEGER, avgMinutes DOUBLE, threePointFieldGoalsMade INTEGER, threePointFieldGoalsAttempted INTEGER)"
    )
    c.executemany(
        "INSERT INTO player_season_stats_deduped VALUES (?, ?, 2, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("10", s, 70, 14.0, 980, 5.0, 350, 4.0, 280, 30.0, 100, 280),
            ("10", s - 1, 30, 8.0, 240, 3.0, 90, 2.0, 60, 20.0, 20, 60),
            ("20", 1990, 82, 33.6, 2753, 6.9, 565, 6.3, 519, 39.0, 92, 245),
        ],
    )
    _with_full_line_columns(c, "player_season_stats_deduped", _SEASON_FULL_LINE_COLUMNS)
    # Podziemski's postseason shots, for a period log's game_n cell: one
    # labeled three in the first quarter of p1, p2 and p3, two in p5, none in
    # p4 - so the shot table covers four of his five playoff games, and game 2
    # of each series (p2 and p5) holds 3 + 6 of the 15 points.
    c.execute(
        "CREATE TABLE shot_chart (athlete_id VARCHAR, season INTEGER, season_type INTEGER, event_id VARCHAR, team_id VARCHAR, "
        "period INTEGER, clock VARCHAR, made BOOLEAN, shot_type VARCHAR, coordinate_x INTEGER, coordinate_y INTEGER, points_attempted INTEGER, description VARCHAR)"
    )
    for event, n in (("p1", 1), ("p2", 1), ("p3", 1), ("p5", 2)):
        for _ in range(n):
            c.execute("INSERT INTO shot_chart VALUES ('10',?,3,?,'1',1,'10:00',TRUE,'Jump Shot',25,26,3,'26-foot three point jumper')", [s, event])
    # Alignment for `situation`'s other half: GS and LAL are West,
    # BOS/DET/CHI are East - constant across `s` and `s - 1` here, since the
    # realignment itself is pinned separately (tests/query/test_player_games.py).
    c.execute("CREATE TABLE team_alignment (season INTEGER, team_id VARCHAR, conference VARCHAR, division VARCHAR)")
    c.executemany(
        "INSERT INTO team_alignment VALUES (?, ?, ?, ?)",
        [
            (season, team_id, conference, division)
            for season in (s, s - 1)
            for team_id, conference, division in (
                ("1", "Western Conference", "Pacific"),
                ("2", "Eastern Conference", "Atlantic"),
                ("3", "Eastern Conference", "Central"),
                ("4", "Western Conference", "Pacific"),
            )
        ],
    )
    real_games.build_table(c, {"games", "teams"})
    return AnswerContext(con=c, out_dir=tmp_path)


def test_a_situation_naming_the_calendar_narrows_the_relation(pg_ctx: AnswerContext) -> None:
    """`situation` is a cell of the relation: a weekday, a month,
    a holiday and "since <day>" each narrow Podziemski's games by their US
    Eastern day, every reader on the relation reads it through
    `scoped_games`, and the answer says it. The fixture's games and their
    Eastern days: e1 {s-1}-11-01, e2 {s-1}-12-01, e3 {s}-01-10, e5 {s-1}-02-28."""
    from datetime import date

    s = current_season()
    played = [date(s - 1, 11, 1), date(s - 1, 12, 1), date(s, 1, 10)]  # e1, e2, e3, the current season's games
    weekday = played[-1].strftime("%A").lower()  # e3's day of the week
    expected = sorted(d.isoformat() for d in played if d.strftime("%A").lower() == weekday)
    log = player_log(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), cuts=Cuts(situation=situation_of(f"{weekday}s"))))
    assert sorted(g["date"] for g in log.data["games"]) == expected and f"on {weekday.capitalize()}s" in (log.text or "")
    january = player_line(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), cuts=Cuts(situation=situation_of("in january")), measure=Measure(key="points", as_typed="points")))
    assert january.data["stats"]["gamesPlayed"] == 1 and january.data["stats"]["avgPoints"] == 15.0 and "in January" in (january.text or "")
    # "since december 1st" reads within each game's own season: December is
    # the season's first calendar year, so e2 (Dec 1) and e3 (Jan 10) qualify.
    since = player_log(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), cuts=Cuts(situation=situation_of("since december 1st"))))
    assert sorted(g["date"] for g in since.data["games"]) == [f"{s - 1}-12-01", f"{s}-01-10"] and "since December 1" in (since.text or "")
    christmas = player_log(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), cuts=Cuts(situation=situation_of("christmas"))))
    assert christmas.data.get("games", []) == [] and "on Christmas Day" in (christmas.text or "")


def test_a_situation_naming_no_calendar_is_refused_by_value(pg_ctx: AnswerContext) -> None:
    """An age or a return from injury is nothing the relation can filter on.
    Refused with the value in the message and the shapes that ARE read named
    - never dropped, which would answer every game under a heading that
    promised "as an 18 year old"."""
    for situation in ("18 year old", "since returning", "before turning 27"):
        with pytest.raises(Unsupported, match=re.escape(situation)):
            player_log(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), cuts=Cuts(situation=situation_of(situation))))


def test_a_situation_naming_a_conference_or_division_narrows_the_relation(pg_ctx: AnswerContext) -> None:
    """`situation`'s other half: the opponent's conference or division,
    for that game's own season. Podziemski's `s` games actually played: e1 vs
    BOS (East/Atlantic, {s-1}-11-01), e2 and e3 vs DET (East/Central,
    {s-1}-12-01 and {s}-01-10) - e4 (vs LAL, West) is a DNP and e6 (vs BOS) an
    empty line, neither counted as played."""
    s = current_season()
    east = player_log(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), cuts=Cuts(situation=situation_of("vs the east"))))
    assert sorted(g["date"] for g in east.data["games"]) == sorted([f"{s - 1}-11-01", f"{s - 1}-12-01", f"{s}-01-10"])
    assert "against Eastern Conference teams" in (east.text or "")
    # No played game is against a Western opponent at all in this fixture (e4
    # is a DNP) - proves the filter actually narrows rather than passing
    # every game through, not merely that the West has none by default.
    west = player_log(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), cuts=Cuts(situation=situation_of("against western conference teams"))))
    assert west.data["games"] == []
    atlantic = player_log(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), cuts=Cuts(situation=situation_of("vs the atlantic division"))))
    assert [g["date"] for g in atlantic.data["games"]] == [f"{s - 1}-11-01"]
    assert "against the Atlantic Division" in (atlantic.text or "")


def test_a_situation_naming_a_conference_in_no_recognized_shape_is_refused_by_value(pg_ctx: AnswerContext) -> None:
    """A conference/division word that is not one of `parse_alignment`'s
    shapes refuses BY VALUE, the same as any other unread situation - never
    silently as though the cell had never been set."""
    with pytest.raises(Unsupported, match=re.escape("western conference these days")):
        player_log(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), cuts=Cuts(situation=situation_of("western conference these days"))))


def test_period_log_reads_one_game_of_each_series(pg_ctx: AnswerContext) -> None:
    """`game_n` reaches a period log through the relation like every other
    cell, so "game 1 of each series" and the whole postseason answer
    differently."""
    whole = period_log(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), span=Span(season_type=3), period=Period(number=1)))
    assert whole.data["games_played"] == 4 and whole.data["total"] == 15
    second = period_log(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), cuts=Cuts(game_n=2), span=Span(season_type=3), period=Period(number=1)))
    assert second.data["games_played"] == 2 and second.data["total"] == 9, second.text
    assert "game 2 of each series" in (second.text or "")


@pytest.fixture
def ps_redirect_ctx(tmp_path: Path) -> AnswerContext:
    """A minimal warehouse for a period log's cross-season redirect
    (yardstick-v2 F050): one player who started two games with 1st-quarter
    shots in season ``s - 1`` (5 and 2 points) and played, but did not
    START, one game in season ``s`` - so "his last N starts" finds nothing
    in the defaulted (current) season and has to cross into the one
    before it."""
    s = current_season()
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    c.execute("INSERT INTO players VALUES ('1', 'Test Player')")
    c.execute("CREATE TABLE teams (team_id VARCHAR, abbreviation VARCHAR, display_name VARCHAR)")
    c.execute("INSERT INTO teams VALUES ('1','GS','Golden State Warriors'),('2','BOS','Boston Celtics')")
    c.execute(
        "CREATE TABLE games (event_id VARCHAR, season INTEGER, season_type INTEGER, date VARCHAR, home_team_id VARCHAR, away_team_id VARCHAR, "
        "home_score INTEGER, away_score INTEGER, winner_team_id VARCHAR)"
    )
    c.executemany(
        "INSERT INTO games VALUES (?, ?, 2, ?, '1', '2', 100, 90, '1')",
        [("e1", s - 1, f"{s - 1}-03-02T00:30Z"), ("e2", s - 1, f"{s - 1}-03-04T00:30Z"), ("e3", s, f"{s}-01-10T00:30Z")],
    )
    c.execute(
        "CREATE TABLE player_game_log (event_id VARCHAR, season INTEGER, season_type INTEGER, team_id VARCHAR, opponent_team_id VARCHAR, "
        "athlete_id VARCHAR, player_name VARCHAR, did_not_play BOOLEAN, minutes INTEGER, starter BOOLEAN, opponent_abbr VARCHAR)"
    )
    c.executemany(
        "INSERT INTO player_game_log VALUES (?, ?, 2, '1', '2', '1', 'Test Player', FALSE, ?, ?, 'BOS')",
        [("e1", s - 1, 30, True), ("e2", s - 1, 28, True), ("e3", s, 15, False)],
    )
    c.execute(
        "CREATE TABLE shot_chart (athlete_id VARCHAR, season INTEGER, season_type INTEGER, event_id VARCHAR, team_id VARCHAR, "
        "period INTEGER, clock VARCHAR, made BOOLEAN, shot_type VARCHAR, coordinate_x INTEGER, coordinate_y INTEGER, points_attempted INTEGER, description VARCHAR)"
    )
    c.executemany(
        "INSERT INTO shot_chart VALUES ('1', ?, 2, ?, '1', 1, '10:00', TRUE, 'Jump Shot', 25, ?, ?, 'a shot')",
        [(s - 1, "e1", 22, 3), (s - 1, "e1", 23, 2), (s - 1, "e2", 23, 2), (s, "e3", 23, 2)],
    )
    return AnswerContext(con=c, out_dir=tmp_path)


def test_period_log_crosses_into_an_earlier_season_when_the_current_one_has_no_starts(ps_redirect_ctx: AnswerContext) -> None:
    """yardstick-v2 F050: "zach collins first quarter stats last 5 games as
    a starter", with his real last 5 starts all in the season before: a
    "last N games" question naming no season is the newest N over his
    CAREER, the same reading a bare count gets everywhere else on the
    relation - never "No 2026 regular season games found ... as a starter",
    true of the box scores read and about the wrong year."""
    s = current_season()
    result = period_log(ps_redirect_ctx, Reading(who=Who(kind="player", players=("Test Player",)), split="starter", period=Period(number=1), window=Window(order="recent", count=5)))
    assert result.data["season"] == s - 1
    assert result.data["games_played"] == 2
    assert result.data["total"] == 7  # 5 + 2, both his starts, none from the empty current season
    assert result.data["average"] == 3.5
    assert "No games this season" in (result.text or "")
    assert f"{s - 1} regular season" in (result.text or "")
    # A season the question NAMES outright keeps the plain refusal - the
    # redirect only fires for a DEFAULTED one.
    named = period_log(
        ps_redirect_ctx, Reading(who=Who(kind="player", players=("Test Player",)), span=Span(season=s), split="starter", period=Period(number=1), window=Window(order="recent", count=5))
    )
    assert named.data["games_played"] == 0
    assert "No games this season" not in (named.text or "")


def test_period_log_does_not_cross_seasons_with_no_window_asked(ps_redirect_ctx: AnswerContext) -> None:
    """The redirect is for a "last N games" WINDOW - a plain defaulted-season
    question with nothing found stays the plain refusal, since there is no
    window to widen."""
    result = period_log(ps_redirect_ctx, Reading(who=Who(kind="player", players=("Test Player",)), split="starter", period=Period(number=1)))
    assert result.data["games_played"] == 0
    assert "No games this season" not in (result.text or "")


def test_player_log_drops_the_players_own_team(pg_ctx: AnswerContext) -> None:
    """#147: a team on the subject beside a named player is his log, never
    the TEAM's - here, his own team, which narrows nothing and is dropped."""
    with_team = player_log(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",), teams=("Golden State Warriors",))))
    without_team = player_log(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",))))
    assert with_team.data["games"] == without_team.data["games"]
    assert with_team.text == without_team.text


def test_player_log_promotes_a_different_team_to_opponent(pg_ctx: AnswerContext) -> None:
    """The Curry shape from #147: "steph curry vs 76ers last 4 games" with the
    76ers on the subject rather than `opponent` is Curry's games against
    them, never the 76ers' own games."""
    as_team = player_log(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",), teams=("Detroit Pistons",))))
    as_opponent = player_log(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), cuts=Cuts(opponent="Detroit Pistons")))
    assert as_team.data["games"] == as_opponent.data["games"]
    assert [g["opponent"] for g in as_team.data["games"]] == ["DET", "DET"]


def test_player_log_drops_a_team_that_resolves_to_nothing(pg_ctx: AnswerContext) -> None:
    """#147: a team on the subject that nothing resolves to (an invented
    "Phoenix Suns") is dropped, not refused - exactly like an invented
    player name."""
    with_bad_team = player_log(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",), teams=("Not A Real Team",))))
    without_team = player_log(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",))))
    assert with_bad_team.data["games"] == without_team.data["games"]


def test_player_log_own_team_beside_a_real_opponent_keeps_the_opponent(pg_ctx: AnswerContext) -> None:
    """#147, the Kobe Bryant shape: his own Lakers on the subject and the
    Rockets as `opponent` - his own team narrows nothing, so the real
    opponent stands."""
    result = player_log(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",), teams=("Golden State Warriors",)), cuts=Cuts(opponent="Detroit Pistons")))
    assert [g["opponent"] for g in result.data["games"]] == ["DET", "DET"]


def test_player_log_an_opponent_already_named_wins_over_a_disagreeing_team(pg_ctx: AnswerContext) -> None:
    """A team on the subject beside an already-named `opponent` is dropped
    rather than reconciled or refused: "Phoenix Suns" is a real, resolvable
    team, and refusing on its mismatch with an already-correct "Philadelphia
    76ers" opponent would answer nothing for a question that names both a
    player and his opponent."""
    result = player_log(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",), teams=("Boston Celtics",)), cuts=Cuts(opponent="Detroit Pistons")))
    assert [g["opponent"] for g in result.data["games"]] == ["DET", "DET"]


def test_player_log_lists_only_the_games_against_the_named_opponent(pg_ctx: AnswerContext) -> None:
    """ "jaylen brown last 8 games vs pistons" lists his games against that
    opponent alone, never the Celtics' last eight games against anybody."""
    result = player_log(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), cuts=Cuts(opponent="Detroit Pistons")))
    assert [g["opponent"] for g in result.data["games"]] == ["DET", "DET"]
    assert result.text.startswith(f"Brandin Podziemski vs the Detroit Pistons, last 2 games of the {current_season()} regular season:")


def test_a_players_log_is_read_into_a_result_and_said_from_it_alone(pg_ctx: AnswerContext) -> None:
    """``compose.logs.read_player_log`` returns a Result - the
    rows, the count before the window, the per-game summary, the notes as
    kinds and facts - and ``compose.say.say`` words it into exactly the
    answer ``compose.answer`` gives; the sayer is handed the Result and
    nothing else."""
    from association.query.compose.say import say
    from association.query.result import Result

    reading = with_point(
        "",
        Reading(
            **(Reading(who=Who(kind="player", players=("Brandin Podziemski",)), cuts=Cuts(opponent="Detroit Pistons"))).filters(),
            asked=PLAYER_LOG,
            subject=Subject("player", players=("Brandin Podziemski",)),
        ),
    )
    planned = plan_point(reading)
    assert isinstance(planned, Query)
    read = read_player_log(pg_ctx.con, planned)
    assert isinstance(read, Result)
    assert read.subject == "Brandin Podziemski" and read.relation == "player" and read.narrowing.opponent == "Detroit Pistons"
    body = read.rows
    assert body is not None and [g["opponent"] for g in body.rows] == ["DET", "DET"] and body.total_before_window == 2
    # No stat named: the full box line (ISSUES.md #471, the season line's rule).
    assert body.columns == ("MIN", "PTS", "REB", "AST", "STL", "BLK", "TO", "FG%", "3PM", "3P%", "FT%")
    assert all(isinstance(each.kind, str) and isinstance(each.facts, dict) for each in read.notes)
    answered = planned_answer(pg_ctx, reading)
    assert answered is not None and say(read).text == answered.text and say(read).data == answered.data


def test_a_logs_heading_names_the_span_it_counts_not_the_rows_shown(pg_ctx: AnswerContext) -> None:
    """ISSUES.md #217 and #285: the heading names the seasons the count
    covers, never the seasons of the rows SHOWN (a career's "last 10 of 118
    games" is not "(2026 regular season)"), and says "career" only of a
    career (never of a span the question cut, "the past two seasons")."""
    s = current_season()
    newest = player_log(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), cuts=Cuts(opponent="Detroit Pistons"), span=Span(career=True), window=Window(count=1)))
    assert [g["season"] for g in newest.data["games"]] == [s]
    assert f"most recent of 3 games of his career ({s - 1}-{s} regular seasons):" in newest.text
    cut = player_log(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), cuts=Cuts(opponent="Detroit Pistons"), span=Span(since=(s - 1)), window=Window(count=8)))
    assert f"last 3 games ({s - 1}-{s} regular seasons):" in cut.text and "career" not in cut.text.split("\n")[0]


def test_a_career_log_against_an_opponent_crosses_seasons(pg_ctx: AnswerContext) -> None:
    s = current_season()
    result = player_log(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), cuts=Cuts(opponent="Detroit Pistons"), span=Span(career=True), window=Window(count=8)))
    assert [g["season"] for g in result.data["games"]] == [s, s, s - 1]
    assert f"last 3 games of his career ({s - 1}-{s} regular seasons):" in result.text
    assert "Only 3 games vs the Detroit Pistons in his box scores." in result.text


def test_a_short_season_log_says_how_many_there_were_and_where_the_rest_are(pg_ctx: AnswerContext) -> None:
    answer = player_log(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), cuts=Cuts(opponent="Detroit Pistons"), window=Window(count=8))).text
    assert f"Only 2 games vs the Detroit Pistons in the {current_season()} regular season - ask about his career to reach earlier seasons." in answer


def test_player_log_honors_venue(pg_ctx: AnswerContext) -> None:
    home = player_log(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), cuts=Cuts(venue="home"))).data["games"]
    away = player_log(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), cuts=Cuts(venue="away"))).data["games"]
    assert [g["home_away"] for g in home] == ["home", "home"]
    assert [g["opponent"] for g in away] == ["DET"]


def test_player_log_lists_only_games_he_played_and_says_what_it_left_out(pg_ctx: AnswerContext) -> None:
    """The DNP in e4 is not a game he played; the empty line in e6 is not a
    game anybody can average. The empty line is counted in the answer, since in
    2013-2018 about one team-game in eight looks like it."""
    result = player_log(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",))))
    assert len(result.data["games"]) == 3
    assert "Not counted: 1 game in this span whose box score lists him with no minutes and no stats." in result.text
    # The note is in the answer's text either way; `data["notes"]` is what
    # lets the page show it without falling back to guessing at the text.
    assert "Not counted: 1 game in this span whose box score lists him with no minutes and no stats." in result.data["notes"]


def test_player_log_averages_exactly_the_games_it_lists(pg_ctx: AnswerContext) -> None:
    result = player_log(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), window=Window(count=2)))
    assert [g["points"] for g in result.data["games"]] == [15, 20]
    assert result.data["averages"]["points"] == pytest.approx(17.5)
    average_row = next(line for line in result.text.splitlines() if line.strip().startswith("per game"))
    assert "17.5" in average_row


def test_player_log_says_how_many_games_the_window_cut_from(pg_ctx: AnswerContext) -> None:
    """F149 (ISSUES.md): a limit (default or asked) that keeps fewer games
    than qualified says how many it cut from, never a bare "last N games".
    Podziemski has 3 played regular-season games this season (e1, e2, e3 -
    e4 is a DNP and e6 an empty box-score line, neither played); a limit of
    2 keeps the two most recent and says how many it cut from."""
    result = player_log(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), window=Window(count=2)))
    assert result.data["qualifying_games"] == 3
    assert result.text.splitlines()[0].startswith("Brandin Podziemski, last 2 of 3 games")
    # The page's headline (renderAnswer) - the answer's own first line, with
    # the trailing ":" a table heading carries stripped, so a table-less
    # page still shows the same words a table-drawing one would.
    assert result.data["headline"] == result.text.splitlines()[0].rstrip(":")
    # Not a truncation note - Podziemski's e6 empty box score is outside
    # this window either way, so the same note attaches regardless of limit.
    assert result.data["notes"] == ["Not counted: 1 game in this span whose box score lists him with no minutes and no stats."]
    # No truncation, no "of N": every qualifying game fit inside the window.
    full = player_log(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), window=Window(count=10)))
    assert full.data["qualifying_games"] == 3
    assert full.text.splitlines()[0].startswith("Brandin Podziemski, last 3 games")
    assert "of 3" not in full.text.splitlines()[0]


def test_a_named_stat_adds_its_columns_to_the_log(pg_ctx: AnswerContext) -> None:
    result = player_log(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), measure=Measure(key="freeThrowPct", as_typed="freeThrowPct")))
    titles = result.text.splitlines()[1].split()
    # The named stat's own columns after the four, then the rest of the box
    # line a free-throw word asks (ISSUES.md #471), FT% not said twice.
    assert titles[titles.index("AST") + 1 : titles.index("AST") + 4] == ["FTM", "FTA", "FT%"]
    assert titles[-6:] == ["STL", "BLK", "TO", "FG%", "3PM", "3P%"]
    # Over the listed games: 2 of 3, 4 of 4, 1 of 2 - a total, not a mean of percentages.
    assert result.data["averages"]["freeThrowPct"] == pytest.approx(100 * 7 / 9)


def test_an_unnarrowed_line_on_a_stat_with_no_column_names_the_stat(pg_ctx: AnswerContext) -> None:
    """ "what was steph curry's avg shot distance": an unnarrowed line on a
    stat with no per-game column is declined, the reason naming the stat."""
    with pytest.raises(Unsupported, match="no per-game column for stat 'avg_shot_distance'"):
        player_line(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), measure=Measure(as_typed="avg_shot_distance")))


def test_a_real_stat_the_log_cannot_show_is_refused_rather_than_dropped(pg_ctx: AnswerContext) -> None:
    assert (
        player_log(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), measure=Measure(key="netpoints", as_typed="assist_o_net_pts", side="offense", category="assist"))).text
        == "A game log has no per-game column for 'assist_o_net_pts'."
    )
    # Not a stat at all - the model's required stat filled with something - adds nothing.
    assert player_log(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), measure=Measure(as_typed="game log"))).data["columns"] == ["MIN", "PTS", "REB", "AST"]
    # A rate the relation derives per game is the compiler's own measure and
    # is shown, in its own sentence ("luka ts% log").
    pg_ctx.con.execute("ALTER TABLE player_box_stats ADD COLUMN ts_pct DOUBLE DEFAULT 0.55")
    shown = player_log(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), measure=Measure(key="ts_pct", as_typed="ts_pct")))
    assert "TS% 55.0%" in shown.text and shown.data["rows"][0]["ts_pct"] == 0.55


def test_player_log_dates_are_the_eastern_day_the_game_was_played(pg_ctx: AnswerContext) -> None:
    """e2 tipped at 00:30 UTC on the 2nd - 7:30pm Eastern on the 1st. Matching
    the UTC day would find it under the wrong date and miss it under the right one."""
    s = current_season()
    games = player_log(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), cuts=Cuts(opponent="Detroit Pistons"))).data["games"]
    assert games[-1]["date"] == f"{s - 1}-12-01"
    assert [g["date"] for g in player_log(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), cuts=Cuts(date=f"{s - 1}-12-01"))).data["games"]] == [f"{s - 1}-12-01"]
    assert player_log(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), cuts=Cuts(date=f"{s - 1}-12-02"))).data["games"] == []


def test_a_date_finds_its_game_whatever_season_was_given(pg_ctx: AnswerContext) -> None:
    s = current_season()
    result = player_log(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), cuts=Cuts(date=f"{s - 1}-02-28"), span=Span(season=s)))
    assert [g["season"] for g in result.data["games"]] == [s - 1]


def test_without_counts_a_did_not_play_entry_and_a_missing_row_alike(pg_ctx: AnswerContext) -> None:
    """An injured player mostly has no row at all: Stephen Curry's 2026 is 43
    rows for an 82-game Warriors season, none of them did-not-play."""
    s = current_season()
    result = player_log(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), companions=(Companion(player="Stephen Curry", predicate="absent"),)))
    assert sorted(g["date"] for g in result.data["games"]) == [f"{s - 1}-12-01", f"{s}-01-10"]
    assert "a did-not-play entry, or no line in the box score at all" in result.text


def test_a_teammate_who_played_a_rebuilt_game_is_not_counted_as_absent(pg_ctx: AnswerContext) -> None:
    """`without` over a rebuilt game.

    A line rebuilt from play-by-play has no minutes, so a teammate whose
    played game was rebuilt must not read as out, nor the game count as one
    played "without" him ("Anthony Davis without Eric Gordon, 2015": Gordon
    played 48 of Davis's 68).

    Here Stephen Curry gets the empty line ESPN really serves for e2, and the
    filled view rebuilds it. e2 is then a game he played, so only e3 - where he
    is a genuine did-not-play - is "without" him.
    """
    s = current_season()
    con = pg_ctx.con
    con.execute("INSERT INTO player_box_stats VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", list(_box("e2", s, "1", "3", "11", minutes=None)))
    con.execute("""
        CREATE VIEW player_box_stats_filled AS
        SELECT pbs.* REPLACE (CASE WHEN pbs.event_id = 'e2' AND pbs.athlete_id = '11' THEN 31 ELSE pbs.points END AS points),
               (pbs.event_id = 'e2' AND pbs.athlete_id = '11') AS reconstructed
        FROM player_box_stats pbs""")

    # The warehouse builds the log OVER the filled view, so the log carries
    # the flag the moment the view does; the fixture is rebuilt the same way.
    con.execute(f"CREATE OR REPLACE VIEW player_game_log AS {_PG_LOG_VIEW_SELECT.replace('FROM player_box_stats pbs', 'FROM player_box_stats_filled pbs')}")

    result = player_log(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), companions=(Companion(player="Stephen Curry", predicate="absent"),)))
    assert sorted(g["date"] for g in result.data["games"]) == [f"{s}-01-10"], "e2 is a game Curry played, rebuilt"


def test_without_falls_back_when_the_warehouse_has_no_rebuilt_lines(pg_ctx: AnswerContext) -> None:
    """The same question against a warehouse with no filled view: the stored
    table alone. The view arrives with a `data load`, and every older
    warehouse - and every other fixture here - has only the stored table."""
    s = current_season()
    result = player_log(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), companions=(Companion(player="Stephen Curry", predicate="absent"),)))
    assert sorted(g["date"] for g in result.data["games"]) == [f"{s - 1}-12-01", f"{s}-01-10"]


def test_without_two_teammates_means_neither_of_them_played(pg_ctx: AnswerContext) -> None:
    """ "Celtics record without Tatum and Brown" reads both names, the games
    without BOTH of them. Podziemski played e1, e2 and e3 this season: Curry
    played e1 and missed e2 and e3, Kuminga played e3 and missed e1 and e2.
    Only e2 was played without both."""
    s = current_season()
    result = player_log(
        pg_ctx,
        Reading(who=Who(kind="player", players=("Brandin Podziemski",)), companions=(Companion(player="Stephen Curry", predicate="absent"), Companion(player="Jonathan Kuminga", predicate="absent"))),
    )
    assert [g["date"] for g in result.data["games"]] == [f"{s - 1}-12-01"]
    assert result.data["without"] == ["Stephen Curry", "Jonathan Kuminga"]
    assert "without Stephen Curry and Jonathan Kuminga" in result.text
    # The one-name question reads the games without that one.
    assert len(player_log(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), companions=(Companion(player="Stephen Curry", predicate="absent"),))).data["games"]) == 2


def test_without_asks_between_two_teammates_who_share_a_name(pg_ctx: AnswerContext) -> None:
    answer = player_log(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), companions=(Companion(player="curry", predicate="absent"),))).text
    assert answer == "'curry' matches more than one player - did you mean Seth Curry or Stephen Curry?"


def test_without_narrows_a_shared_name_to_that_seasons_teammates(pg_ctx: AnswerContext) -> None:
    # Last season Seth was a Celtic and Dell was long retired: only one Curry could be meant.
    result = player_log(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), companions=(Companion(player="curry", predicate="absent"),), span=Span(season=(current_season() - 1))))
    assert result.data["without"] == ["Stephen Curry"]


def test_over_one_season_without_is_every_game_he_did_not_play_for_the_team(pg_ctx: AnswerContext) -> None:
    """Seth Curry joins at e3. Over one season "without Seth Curry" is every
    game of Podziemski's in which Seth had no row for the Warriors - the games
    before he came as much as a game after a trade (decision D15) - and the
    definition says so rather than "while on the same team"."""
    s = current_season()
    result = player_log(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), companions=(Companion(player="Seth Curry", predicate="absent"),)))
    assert sorted(g["date"] for g in result.data["games"]) == [f"{s - 1}-11-01", f"{s - 1}-12-01"]
    assert "Without Seth Curry means games he did not play for his team, traded or injured alike" in result.text
    assert "while on the same team" not in result.text


def test_both_season_types_read_a_teammates_absence_by_his_own_rows(pg_ctx: AnswerContext) -> None:
    """Over both season types read as one ("last 10 games", decision D14) a
    teammate is still a teammate: the check of who shared his team asked for
    `season_type = 0`, matched no row, and read Seth Curry as a stranger, so
    the absence kept a tenure D15 drops over one season."""
    span = span_of(Span(season=current_season(), both=True), "player_game_log", season_type=BOTH_SEASON_TYPES)
    reading = Reading(who=Who(kind="player", players=("Brandin Podziemski",)), companions=(Companion(player="Seth Curry", predicate="absent"),))
    narrowed = scoped_games(pg_ctx.con, Entity(id="10", name="Brandin Podziemski"), span, reading, opponent=None, measures=[])
    assert isinstance(narrowed, Narrowed)
    assert [c.tenure for c in narrowed.conditions] == [None]


def test_over_a_career_without_keeps_his_time_on_the_team(pg_ctx: AnswerContext) -> None:
    """D15 keeps the tenure cut over a career: the Warriors' games before
    Seth Curry arrived are not games played "without" a man on another
    team's books, and the definition says "while on the same team"."""
    answer = player_log(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), companions=(Companion(player="Seth Curry", predicate="absent"),), span=Span(career=True))).text
    assert "none of them without Seth Curry" in answer


def test_a_teammate_of_another_season_is_said_to_have_played_elsewhere(pg_ctx: AnswerContext) -> None:
    """Last season Seth Curry was a Celtic: over that season every game of
    Podziemski's is "without" him (D15), and a table headed by his name would
    hide that he was elsewhere, so the answer says it."""
    result = player_log(
        pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), companions=(Companion(player="Seth Curry", predicate="absent"),), span=Span(season=current_season() - 1))
    )
    assert "Seth Curry played no game for Brandin Podziemski's team in the season asked, so every game is without him." in result.text


def test_a_teammate_carried_over_is_on_the_team_before_his_first_game(pg_ctx: AnswerContext) -> None:
    """LeBron James's first 2026 row is 2025-11-19; the Lakers' games before it
    were played without him. Kuminga here is the same shape."""
    result = player_log(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), companions=(Companion(player="Jonathan Kuminga", predicate="absent"),)))
    assert [g["opponent"] for g in result.data["games"]] == ["DET", "BOS"]


def test_without_somebody_who_was_never_a_teammate_says_so(pg_ctx: AnswerContext) -> None:
    answer = player_log(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), companions=(Companion(player="Jaylen Brown", predicate="absent"),))).text
    assert answer == f"Jaylen Brown was not Brandin Podziemski's teammate in any of his 3 games in the {current_season()} regular season."


def test_no_games_against_an_opponent_is_not_no_games(pg_ctx: AnswerContext) -> None:
    """The refusal names the missing fact: he has games, none of them against
    that team. "No games found for him" would send the reader to the wrong place."""
    answer = player_log(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), cuts=Cuts(opponent="Los Angeles Lakers"))).text
    assert answer == f"Brandin Podziemski played 3 games in the {current_season()} regular season, none of them vs the Los Angeles Lakers."


def test_a_season_with_no_games_at_all_says_that(pg_ctx: AnswerContext) -> None:
    s = current_season() - 5
    assert player_log(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), span=Span(season=s))).text == f"No {s} regular season games found for Brandin Podziemski."


def test_a_defaulted_season_with_no_games_redirects_to_a_retired_players_range(pg_ctx: AnswerContext) -> None:
    """The season was never named - it defaulted to "now" - so a player whose
    only games are long past gets pointed at his own range instead of a
    refusal that reads as though his whole career were missing (issue #18).
    Podziemski's own case above, with an explicit season, keeps its plain
    refusal: the negative control."""
    pg_ctx.con.execute("INSERT INTO players VALUES ('30','Old Timer')")
    pg_ctx.con.executemany(
        f"INSERT INTO player_box_stats VALUES ({', '.join('?' for _ in range(24))})",
        [_box("e8", 1995, "5", "3", "30", minutes=30, pts=20)],
    )
    s = current_season()
    answer = player_log(pg_ctx, Reading(who=Who(kind="player", players=("Old Timer",)))).text
    assert answer == f"No {s} regular season games found for Old Timer. He last appears in 1995. The warehouse holds his 1995 regular season; name one, or ask for his career."


def test_a_named_season_with_no_games_keeps_the_plain_refusal(pg_ctx: AnswerContext) -> None:
    """Unlike the default case above, the season here is what the question is
    about - redirecting it would answer a year nobody asked for."""
    pg_ctx.con.execute("INSERT INTO players VALUES ('30','Old Timer')")
    pg_ctx.con.executemany(
        f"INSERT INTO player_box_stats VALUES ({', '.join('?' for _ in range(24))})",
        [_box("e8", 1995, "5", "3", "30", minutes=30, pts=20)],
    )
    answer = player_log(pg_ctx, Reading(who=Who(kind="player", players=("Old Timer",)), span=Span(season=1999))).text
    assert answer == "No 1999 regular season games found for Old Timer."


def test_a_career_never_counts_the_phantom_season_twice(pg_ctx: AnswerContext) -> None:
    """1993 is a full copy of 1993-94 under another label; a career that read it
    would list every one of those games twice."""
    result = player_log(pg_ctx, Reading(who=Who(kind="player", players=("Michael Jordan",)), span=Span(career=True)))
    assert [g["season"] for g in result.data["games"]] == [1995]
    assert "Box scores begin with the 1993-94 season, so his 1990-1993 seasons are not counted." in result.text


@pytest.mark.parametrize("answered", [player_log, player_line])
def test_a_career_and_a_named_season_at_once_is_refused(pg_ctx: AnswerContext, answered: Any) -> None:
    with pytest.raises(Unsupported):
        answered(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), span=Span(season=current_season(), career=True)))


def test_player_log_keeps_only_the_games_past_a_threshold_rather_than_ignoring_it(pg_ctx: AnswerContext) -> None:
    """A line on a player's log keeps the games past it - e1 15 FGA and e2 20
    stay, e3 10 goes - and one beside no stat is refused, there being no
    column to keep a line on."""
    kept = player_log(
        pg_ctx,
        Reading(
            who=Who(kind="player", players=("Brandin Podziemski",)),
            lines=(Line(measure="fieldGoalsAttempted", value=15, as_typed="15 fieldGoalsAttempted", keyed=True),),
            measure=Measure(key="fieldGoalsAttempted", as_typed="fieldGoalsAttempted"),
        ),
    )
    assert "with 15+ FGA" in kept.text and len(kept.data["rows"]) == 2 and {r["fieldGoalsAttempted"] for r in kept.data["rows"]} == {15, 20}
    assert (
        player_log(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), lines=(Line(measure=None, value=15, as_typed="15", keyed=True),))).text
        == "Keeping only the games past 15 needs the stat they reach it in, and none was read."
    )


def test_player_line_on_one_date_is_that_games_line(pg_ctx: AnswerContext) -> None:
    """A date is one game: the relation's `date` reaches a player's line as
    it reaches his log. e2 tipped {s-1}-12-02T00:30Z, which is the evening of
    December 1 Eastern - the UTC day would find nothing - and one game is a
    line, not an average, with no season total appended and the career span
    the date replaced left unsaid."""
    s = current_season()
    result = player_line(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), cuts=Cuts(date=f"{s - 1}-12-01")))
    assert result.text == (
        f"Brandin Podziemski had 20 points, 7 rebounds and 5 assists on {s - 1}-12-01.\n"
        "   MIN  STL  BLK  TOV   PF           FG%     3PT%          FT%\n"
        "  32.0  1.0  0.0  2.0  3.0  50.0 (10-20)  - (0-0)  100.0 (4-4)"
    )
    assert result.data["date"] == f"{s - 1}-12-01" and result.data["stats"]["gamesPlayed"] == 1
    # A date in the previous season is found under a season that holds none
    # of it: the date replaces the season, never read beside it as a career
    # span (#230).
    last = player_line(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), cuts=Cuts(date=f"{s - 1}-02-28"), measure=measure_of("points"), span=Span(season=s)))
    assert last.text == f"Brandin Podziemski had 8 points on {s - 1}-02-28."
    none = player_line(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), cuts=Cuts(date=f"{s}-07-04")))
    assert none.text.startswith(f"No regular season game on {s}-07-04 found for Brandin Podziemski")


def test_the_relation_window_is_cut_after_the_row_filters(pg_ctx: AnswerContext) -> None:
    """ "His average over his last 2 games vs Detroit" averages the last two
    DETROIT games, not the last two games of which some were vs Detroit: the
    window is cut after every row filter and before the aggregate. Read
    straight off the relation, since a player's line hands "last N" to his
    log."""
    from association.query.player_games import aggregate_sql
    from association.query.player_relation import _narrow_player_games, span_of

    s = current_season()
    span = span_of(Span(career=True), "player_game_log")
    narrowed = _narrow_player_games(pg_ctx.con, Entity("10", "Brandin Podziemski"), span, opponent="Detroit Pistons", venue=None, without=None)
    assert not isinstance(narrowed, Unanswered)
    narrowed.window = ("recent", 2)
    sql, params = aggregate_sql(narrowed, ["COUNT(*)", "AVG(pgl.points)", "MIN(g.date)"])
    row = pg_ctx.con.execute(sql, params).fetchone()
    assert row is not None
    games, avg, first = row
    # His Detroit games are e5 (8, s-1), e2 (20) and e3 (15): the newest two are e2 and e3.
    assert (games, avg) == (2, 17.5) and first.startswith(f"{s - 1}-12-02")
    # `windowed=True`: `scoped_games` sets `.window` from every caller's
    # window - including a player's log, which says "last N games" its own
    # way and would double it up - so the phrase is opt-in, for the readers
    # (a shot chart, a shot distance) that read their rows through `.window`.
    assert narrowed.filters(windowed=True).endswith(" vs the Detroit Pistons over his last 2 games")
    assert narrowed.filters().endswith(" vs the Detroit Pistons")  # the default stays silent about it
    narrowed.window = ("first", 1)
    sql, params = aggregate_sql(narrowed, ["COUNT(*)", "AVG(pgl.points)"])
    assert pg_ctx.con.execute(sql, params).fetchone() == (1, 8.0)


def test_player_line_averages_the_games_against_an_opponent(pg_ctx: AnswerContext) -> None:
    """ "evan mobley avg against bucks" averages the games against that
    opponent, never his whole season."""
    result = player_line(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), cuts=Cuts(opponent="Detroit Pistons"), measure=Measure(key="points", as_typed="points")))
    # The footer of meetings that follows is tested below.
    assert (result.text or "").startswith(f"Brandin Podziemski averaged 17.5 points per game in 2 games vs the Detroit Pistons in the {current_season()} regular season. That is 35 in total.")


def test_player_line_honors_venue_and_a_teammates_absence(pg_ctx: AnswerContext) -> None:
    assert (
        player_line(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), cuts=Cuts(venue="home"), measure=Measure(key="points", as_typed="points"))).data["stats"]["avgPoints"]
        == 12.5
    )
    without = player_line(
        pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), companions=(Companion(player="Stephen Curry", predicate="absent"),), measure=Measure(key="points", as_typed="points"))
    ).data["stats"]
    assert (without["gamesPlayed"], without["avgPoints"]) == (2, 17.5)


def test_player_line_without_two_teammates_averages_only_the_games_neither_played(pg_ctx: AnswerContext) -> None:
    """e2 is the one game Podziemski played without both of them - 20 points,
    never the 2 games and 17.5 of a question about Curry alone."""
    result = player_line(
        pg_ctx,
        Reading(
            who=Who(kind="player", players=("Brandin Podziemski",)),
            companions=(Companion(player="Stephen Curry", predicate="absent"), Companion(player="Jonathan Kuminga", predicate="absent")),
            measure=Measure(key="points", as_typed="points"),
        ),
    )
    assert (result.data["stats"]["gamesPlayed"], result.data["stats"]["avgPoints"]) == (1, 20.0)
    assert "without Stephen Curry and Jonathan Kuminga" in result.text


def test_player_line_says_one_game_not_one_games(pg_ctx: AnswerContext) -> None:
    answer = player_line(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), cuts=Cuts(opponent="Boston Celtics"), measure=Measure(key="points", as_typed="points"))).text
    assert "per game in 1 game vs the Boston Celtics" in answer


def test_player_line_career_is_totals_over_games_not_an_average_of_averages(pg_ctx: AnswerContext) -> None:
    """(980 + 240) / (70 + 30) = 12.2. The mean of the two season averages would
    be 11.0 - a number no reader could reproduce from the career line."""
    s = current_season()
    result = player_line(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), span=Span(career=True)))
    assert result.data["stats"]["avgPoints"] == 12.2
    assert f"in 100 games over his career (2 regular seasons, {s - 1}-{s})" in result.text


def test_player_line_answers_a_shooting_percentage_with_its_makes_and_attempts(pg_ctx: AnswerContext) -> None:
    """ "What is Jokic's 3 point percentage this season" is answered with the makes and attempts."""
    s = current_season()
    season = player_line(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), measure=Measure(key="threePointFieldGoalPct", as_typed="threePointFieldGoalPct"))).text
    # The season table here carries no steals, blocks, turnovers, fouls or
    # field goals: the full line says so cell by cell rather than dropping them.
    assert season == (
        f"Brandin Podziemski shot 35.7% on 3-pointers (100 of 280) in 70 games in the {s} regular season.\n"
        "   MIN   PTS  REB  AST  STL  BLK  TOV  PF  FG%  FT%\n"
        "  30.0  14.0  5.0  4.0    -    -    -   -    -    -"
    )
    career = player_line(
        pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), measure=Measure(key="threePointFieldGoalPct", as_typed="threePointFieldGoalPct"), span=Span(career=True))
    ).text
    assert "35.3% on 3-pointers (120 of 340)" in career
    against = player_line(
        pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), cuts=Cuts(opponent="Detroit Pistons"), measure=Measure(key="freeThrowPct", as_typed="freeThrowPct"))
    ).text
    assert "83.3% on free throws (5 of 6) in 2 games vs the Detroit Pistons" in against


def test_player_line_answers_a_made_count_stat_with_its_attempts_and_percentage(pg_ctx: AnswerContext) -> None:
    """F051 (ISSUES.md): "davion mitchell 3 point stats" (stat=
    threePointFieldGoalsMade) says the attempts and the percentage they make
    beside the makes, both `must_include` in the yardstick key. Exercised here
    over box scores (an opponent narrows the read), the one path
    `test_player_line_answers_a_shooting_percentage_with_its_makes_and_attempts`
    above does not cover for a made-count stat."""
    against = (
        player_line(
            pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), cuts=Cuts(opponent="Detroit Pistons"), measure=Measure(key="freeThrowsMade", as_typed="freeThrowsMade"))
        ).text
        or ""
    )
    # Same two games (e2, e3) test_player_line_answers_a_shooting_percentage_with_its_makes_and_attempts
    # reads as freeThrowPct (5 of 6, 83.3%) - the made-count reading states
    # the identical makes/attempts/percentage, phrased as a total rather than
    # a percentage-first sentence.
    assert "That is 5 of 6 (83.3%)." in against
    # A multi-stat line (no single `stat` named) adds no attempts to its
    # sentence - singling out one entry's would read as though only it needed
    # the qualifier - and says every shooting split, with its makes and
    # attempts, in the full line beneath (ISSUES.md #471).
    default_line = player_line(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), cuts=Cuts(opponent="Detroit Pistons"))).text or ""
    sentence, heads, cells = default_line.split("\n")[:3]
    assert "of 6" not in sentence and "%" not in sentence
    assert heads.split() == ["MIN", "STL", "BLK", "TOV", "PF", "FG%", "3PT%", "FT%"]
    assert cells.split()[-6:] == ["48.6", "(17-35)", "-", "(0-0)", "83.3", "(5-6)"]


def test_player_line_over_the_last_n_games_is_the_log_with_its_averages(pg_ctx: AnswerContext) -> None:
    """ "stats over his last N games" is a log of those games with averages
    beneath, never the season line: a player's line over a window is the
    log's point, said by the log's sayer."""
    result = player_line(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), window=Window(count=2)))
    assert len(result.data["games"]) == 2
    assert "averages" in result.data
    # F149 (ISSUES.md): the window (2) is narrower than his 3 qualifying
    # games, so the heading says how many it cut from.
    assert "last 2 of 3 games" in result.text


def test_player_line_honors_since_and_order(pg_ctx: AnswerContext) -> None:

    assert [cell for cell, _ in cells_unhonored(Reading(who=Who(kind="player", players=("Brandin Podziemski",)), span=Span(since=2024)), PointShape("player_games", "scalar", "line"))] == []
    assert [
        cell for cell, _ in cells_unhonored(Reading(who=Who(kind="player", players=("Brandin Podziemski",)), window=Window(order="recent", count=3)), PointShape("player_games", "scalar", "line"))
    ] == []


def test_until_closes_a_since_bounded_range_rather_than_reading_through_now(pg_ctx: AnswerContext) -> None:
    """``until`` stops a range at the far end instead of reading every season
    since the start through the present - AGENTS.md's own worst-failure-shape
    example ("best 3 point shooters of the 2010s" answering 2010 through now,
    an unfilled ``until``). yardstick-v2 F045 ("Portis vs bulls 2019-20 to
    2023-24") is 21 games across five seasons, never one season of three
    meetings read from the range's first year alone.

    Podziemski's fixture games: 1 played box score in the earlier season
    (``s - 1``, e5) and 3 in the current one (``s``: e1, e2, e3 - e4 is a DNP
    and e6 an empty box score, neither counted). ``since=s-1`` alone reads
    every season from there on and so all 4; ``since=s-1, until=s-1`` closes
    the range right back where it opened and reads only the 1.
    """
    s = current_season()
    closed = player_line(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), measure=Measure(key="points", as_typed="points"), span=Span(since=(s - 1), until=(s - 1))))
    assert closed.data["stats"]["gamesPlayed"] == 1
    assert closed.data["seasons"] == [s - 1, s - 1]
    open_ended = player_line(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), measure=Measure(key="points", as_typed="points"), span=Span(since=(s - 1))))
    assert open_ended.data["stats"]["gamesPlayed"] == 4
    assert open_ended.data["seasons"] == [s - 1, s]


def test_player_line_honors_an_own_team_as_his_tenure(pg_ctx: AnswerContext) -> None:
    """yardstick-v2 F166: "lebron stats as a starter for Miami": `own_team`
    (distinct from `opponent`, and from a team on the subject - see
    subject._apply_own_team's docstring for why the two are not
    interchangeable) keeps only the games he played FOR that team, never his
    current season. Seth Curry's fixture career has three box-scored games:
    one for Boston (e7, season s-1, 12 points) and two for Golden State
    (e3/e4, season s) - `own_team='Boston Celtics'` keeps only the one."""
    result = player_line(pg_ctx, Reading(who=Who(kind="player", players=("Seth Curry",)), cuts=Cuts(tenure="Boston Celtics"), measure=Measure(key="points", as_typed="points"), span=Span(career=True)))
    assert result.data["stats"]["gamesPlayed"] == 1
    assert result.data["stats"]["avgPoints"] == 12
    assert "with the Boston Celtics" in (result.text or "")
    warriors = player_line(
        pg_ctx, Reading(who=Who(kind="player", players=("Seth Curry",)), cuts=Cuts(tenure="Golden State Warriors"), measure=Measure(key="points", as_typed="points"), span=Span(career=True))
    )
    assert warriors.data["stats"]["gamesPlayed"] == 2


def test_player_line_names_the_real_cause_when_nothing_matches(pg_ctx: AnswerContext) -> None:
    answer = player_line(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), cuts=Cuts(opponent="Los Angeles Lakers"))).text
    assert answer == f"Brandin Podziemski played 3 games in the {current_season()} regular season, none of them vs the Los Angeles Lakers."


def test_a_narrowed_question_carries_the_box_score_floor() -> None:
    """Jordan's 1990 season line is real and answerable; his 1990 line against
    one opponent needs box scores, which start in 1993-94."""
    from association.query.coverage import check_coverage

    # The point reader names which relation a line reads (the season line
    # unnarrowed, the box scores narrowed), and the floor follows the key.
    assert check_coverage(PointShape("player_seasons", "scalar", "line"), Reading(who=Who(kind="player", players=("Michael Jordan",)), span=Span(season=1990, season_type=2))) is None
    assert (
        check_coverage(
            PointShape("player_games", "scalar", "line"), Reading(who=Who(kind="player", players=("Michael Jordan",)), cuts=Cuts(opponent="New York Knicks"), span=Span(season=1990, season_type=2))
        )
        is not None
    )
    assert check_coverage(PLAYER_LOG, Reading(who=Who(kind="player", players=("Michael Jordan",)), span=Span(season=1990, season_type=2))) is not None


# ---------------- two players' meetings, with a team opponent, not a second player ----------------


def test_player_meetings_needs_two_players(pg_ctx: AnswerContext) -> None:
    """One name and a team is a player's own question, never a matchup: the
    parser reads it that way (``test_parser.test_a_player_against_a_team_is_never_a_matchup``),
    and the meetings' reader says a matchup needs two players."""
    assert player_meetings(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)))).text == "A matchup needs two players, and only Brandin Podziemski was read."


def test_the_matchup_point_refuses_a_team_opponent(pg_ctx: AnswerContext) -> None:
    """Two players' meetings are the games they played against each other, so
    there is no third team to narrow them to - refused by declaration
    (its shape's row of ``RELATION_SCOPING_EXCLUDED``, ``refused``, read by
    the planner's ``compose.plan.cells_declined``), with or without a
    teammate named absent, rather than answering the whole matchup as
    though no team was named."""
    with pytest.raises(Unsupported, match="games against Detroit Pistons cannot be read here"):
        plan(default_point(PLAYER_MEETINGS, Reading(who=Who(kind="pair", players=("Brandin Podziemski", "Stephen Curry")), cuts=Cuts(opponent="Detroit Pistons"))))
    with pytest.raises(Unsupported, match="games against Detroit Pistons"):
        plan(
            default_point(
                PLAYER_MEETINGS,
                Reading(
                    who=Who(kind="pair", players=("Brandin Podziemski", "Stephen Curry")), cuts=Cuts(opponent="Detroit Pistons"), companions=(Companion(player="Jaylen Brown", predicate="absent"),)
                ),
            )
        )
    assert "opponent" not in cells_stated(PLAYER_MEETINGS)


def test_the_matchup_point_honors_without_too(pg_ctx: AnswerContext) -> None:
    """A teammate's absence narrows the first player's games on a genuine
    two-player matchup, as it does on every reader of the relation."""
    scope = Reading(who=Who(kind="pair", players=("Brandin Podziemski", "Stephen Curry")), companions=(Companion(player="Jaylen Brown", predicate="absent"),))
    assert default_point(PLAYER_MEETINGS, scope).read_as.shape == "comparison"
    assert [cell for cell, _ in cells_unhonored(scope, PLAYER_MEETINGS)] == []


def test_the_matchup_point_refuses_a_window_and_a_round(pg_ctx: AnswerContext) -> None:
    """The relation's cells being honored does not let every other cell
    through too: a window is excluded (the newest meetings are shown beneath
    averages over all of them), and `round` no relation has - the planner's
    refusal, as the parser plans the point."""
    with pytest.raises(Unsupported, match="newest meetings"):
        plan(default_point(PLAYER_MEETINGS, Reading(who=Who(kind="pair", players=("Brandin Podziemski", "Stephen Curry")), window=Window(order="recent"))))
    with pytest.raises(Unsupported, match="different span"):
        planned_query(PLAYER_MEETINGS, Reading(who=Who(kind="pair", players=("Brandin Podziemski", "Stephen Curry")), cuts=Cuts(round="finals")))


def test_player_meetings_honors_without_on_a_real_two_player_matchup(pg_ctx: AnswerContext) -> None:
    """ "jokic vs embiid without jamal murray" names no team, so it is the
    genuine two-player matchup it looks like, and the first player's games
    are narrowed through the shared step: a teammate's absence is stated,
    never unhonored. Its words state the relation's set less its row's
    exclusions (``compose.plan.cells_stated``), the window among them."""
    assert [
        cell
        for cell, _ in cells_unhonored(
            Reading(who=Who(kind="pair", players=("Brandin Podziemski", "Stephen Curry")), companions=(Companion(player="Jaylen Brown", predicate="absent"),)), PLAYER_MEETINGS
        )
    ] == []
    assert "companion" in cells_stated(PLAYER_MEETINGS) and "venue" in cells_stated(PLAYER_MEETINGS)
    assert "window" not in cells_stated(PLAYER_MEETINGS)
    # The behavior itself is pinned on the league fixture in
    # tests/query/test_conditions.py (a teammate's absence, a venue).


# ---------------- a player's log: a teammate named absent, read by the index ----------------


def test_a_near_spelling_of_without_is_taken_and_the_reading_is_visible(pg_ctx: AnswerContext) -> None:
    """yardstick-v2 F157: "de'aaron fox vs magic last five games without
    wembyanama" takes the near spelling with exactly one candidate rather
    than asking "did you mean Victor Wembanyama?" (`_resolved_teammate`): the
    question falls short of five games by a game count, not a name. The
    reading is carried by `collect_name_readings` - not shown where a reader
    is called directly (as here), shown in the agent's own answer (`agent.py`
    attaches it)."""
    with collect_name_readings() as readings:
        result = player_log(
            pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), cuts=Cuts(opponent="Detroit Pistons"), companions=(Companion(player="Stephen Cury", predicate="absent"),))
        )
    assert "without Stephen Curry" in result.text
    assert readings == ["('Stephen Cury' matches no player exactly and was read as Stephen Curry, the only near spelling on record - spell the name exactly to ask about someone else.)"]


def test_a_surname_backoff_in_without_asks_as_it_does_everywhere(pg_ctx: AnswerContext) -> None:
    """ "Jemel Kuminga" is a given name that is nobody's beside one player's
    surname - the back-off, not a misspelling - so `without` asks, as every
    other name does, and reads nothing."""
    with collect_name_readings() as readings:
        result = player_log(pg_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), companions=(Companion(player="Jemel Kuminga", predicate="absent"),)))
    assert "did you mean Jonathan Kuminga?" in result.text
    assert readings == []


# ---------------- two players' meetings on a date (#231) ----------------


@pytest.fixture
def pm_ctx(tmp_path: Path) -> AnswerContext:
    """Two Warriors/Lakers meetings, Stephen Curry (athlete ``1``) and LeBron
    James (athlete ``2``) both playing in each, in season ``s - 1`` - not the
    current one, so a read that silently defaults the season back to "now"
    is caught rather than coincidentally answered right:

    ====  ==================  ==========  =======================
    game  date                 winner      points (Curry / LeBron)
    ====  ==================  ==========  =======================
    g1    {s-2}-11-10          Warriors    30 / 25
    g2    {s-1}-01-15          Lakers      20 / 35
    ====  ==================  ==========  =======================

    Both dates fall in season ``s - 1`` ("a season is named for the year it
    ends"). Naming g1's date narrows to it alone - never averaged with g2
    ("curry vs lebron on 2025-04-03" is one meeting, not every meeting of the
    season): the meetings' read passes `date` to `scoped_games` with every
    other relation cell.
    """
    s = current_season()
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    c.execute("INSERT INTO players VALUES ('1','Stephen Curry'),('2','LeBron James')")
    c.execute("CREATE TABLE teams (team_id VARCHAR, abbreviation VARCHAR, display_name VARCHAR)")
    c.execute("INSERT INTO teams VALUES ('9','GS','Golden State Warriors'),('13','LAL','Los Angeles Lakers')")
    c.execute(
        "CREATE TABLE games (event_id VARCHAR, season INTEGER, season_type INTEGER, date VARCHAR, home_team_id VARCHAR, away_team_id VARCHAR, "
        "home_score INTEGER, away_score INTEGER, winner_team_id VARCHAR)"
    )
    c.executemany(
        "INSERT INTO games VALUES (?, ?, 2, ?, ?, ?, ?, ?, ?)",
        [
            ("g1", s - 1, f"{s - 2}-11-10T23:00Z", "9", "13", 120, 110, "9"),
            ("g2", s - 1, f"{s - 1}-01-15T23:00Z", "13", "9", 118, 105, "13"),
        ],
    )
    c.execute(f"CREATE TABLE player_box_stats ({_BOX_COLUMNS})")
    c.executemany(
        f"INSERT INTO player_box_stats VALUES ({', '.join('?' for _ in range(24))})",
        [
            _box("g1", s - 1, "9", "13", "1", pts=30),
            _box("g1", s - 1, "13", "9", "2", pts=25),
            _box("g2", s - 1, "13", "9", "1", pts=20),
            _box("g2", s - 1, "9", "13", "2", pts=35),
        ],
    )
    c.execute(
        "CREATE VIEW player_game_log AS SELECT pbs.*, p.display_name AS player_name, g.date AS game_date, t.abbreviation AS team_abbr, o.abbreviation AS opponent_abbr "
        "FROM player_box_stats pbs LEFT JOIN players p ON p.athlete_id = pbs.athlete_id LEFT JOIN games g ON g.event_id = pbs.event_id AND g.season = pbs.season "
        "LEFT JOIN teams t ON t.team_id = pbs.team_id LEFT JOIN teams o ON o.team_id = pbs.opponent_team_id"
    )
    # Empty: two players' meetings always ask `_unseen_meetings` (conditions.py)
    # for team-games with no box score, which joins this table unconditionally
    # - it only needs to exist. No row in it here means none of this
    # fixture's meetings are ever "unseen", which is correct: both players
    # have a real box-score row in both games.
    c.execute("CREATE TABLE team_box_stats (event_id VARCHAR, season INTEGER, season_type INTEGER, team_id VARCHAR, opponent_team_id VARCHAR, home_away VARCHAR)")
    real_games.build_table(c, {"games", "teams"})
    return AnswerContext(con=c, out_dir=tmp_path / "out")


def test_player_meetings_honors_date_over_a_season_full_of_meetings(pm_ctx: AnswerContext) -> None:
    """With the season given correctly (matching where the games actually
    are), a `date` naming g1 narrows to it alone, not the season's 2
    meetings averaged together."""
    s = current_season()
    result = player_meetings(pm_ctx, Reading(who=Who(kind="pair", players=("Stephen Curry", "LeBron James")), cuts=Cuts(date=f"{s - 2}-11-10"), span=Span(season=s - 1)))
    assert result.data["meetings"] == 1
    assert result.data["averages"]["Stephen Curry"]["points"] == 30
    assert result.data["averages"]["LeBron James"]["points"] == 25
    assert f"on {s - 2}-11-10" in result.text


def test_player_meetings_date_replaces_even_an_explicitly_wrong_season(pm_ctx: AnswerContext) -> None:
    """As a player's line's own date handling does
    (`test_player_line_on_one_date_is_that_games_line`): a date names its game
    outright, so it replaces the season - and resolves the names over the
    career - even where the season given disagrees outright and holds
    neither game here at all."""
    s = current_season()
    result = player_meetings(pm_ctx, Reading(who=Who(kind="pair", players=("Stephen Curry", "LeBron James")), cuts=Cuts(date=f"{s - 2}-11-10"), span=Span(season=s)))
    assert result.data["meetings"] == 1
    assert result.data["averages"]["Stephen Curry"]["points"] == 30
    assert result.data["averages"]["LeBron James"]["points"] == 25
    # The heading names the date and the date's OWN season - never the
    # current one the (wrong) `season` asked for: it says the season the
    # meeting it counted actually came from.
    assert f"on {s - 2}-11-10, {s - 1} regular season" in result.text
    assert f"{s} regular season" not in result.text


# ---------------- a narrowed reading over a whole empty-box-score season (#72) ----------------


@pytest.fixture
def empty_season_ctx(tmp_path: Path) -> AnswerContext:
    """One player whose whole season is the empty box score ESPN leaves - every
    Chicago and New Orleans game from 2013 to 2018 in miniature. Built to
    exercise a player's log and line read narrowed, which `sgh_ctx` above
    cannot: a game's high never joins `games`, and these two do. No
    `reconstructed` column, so - like `pg_ctx` - the log carries no rebuild at
    all, the same shape an older warehouse has."""
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    c.execute("INSERT INTO players VALUES ('1','Anthony Davis')")
    c.execute("CREATE TABLE teams (team_id VARCHAR, abbreviation VARCHAR, display_name VARCHAR)")
    c.execute("INSERT INTO teams VALUES ('1','NO','New Orleans Pelicans'),('2','LAL','Los Angeles Lakers')")
    c.execute(
        "CREATE TABLE games (event_id VARCHAR, season INTEGER, season_type INTEGER, date VARCHAR, home_team_id VARCHAR, away_team_id VARCHAR, "
        "home_score INTEGER, away_score INTEGER, winner_team_id VARCHAR)"
    )
    s = current_season()
    c.executemany(
        "INSERT INTO games VALUES (?, ?, 2, ?, ?, ?, ?, ?, ?)",
        [("e1", s, f"{s - 1}-11-02T00:30Z", "2", "1", 100, 90, "2"), ("e2", s, f"{s - 1}-12-02T00:30Z", "1", "2", 95, 92, "1")],
    )
    c.execute(f"CREATE TABLE player_box_stats ({_BOX_COLUMNS})")
    c.executemany(
        f"INSERT INTO player_box_stats VALUES ({', '.join('?' for _ in range(24))})",
        [_box("e1", s, "1", "2", "1", minutes=None), _box("e2", s, "1", "2", "1", minutes=None)],
    )
    c.execute(
        "CREATE VIEW player_game_log AS SELECT pbs.*, p.display_name AS player_name, g.date AS game_date, "
        "t.abbreviation AS team_abbr, o.abbreviation AS opponent_abbr "
        "FROM player_box_stats pbs LEFT JOIN players p ON p.athlete_id = pbs.athlete_id "
        "LEFT JOIN games g ON g.event_id = pbs.event_id AND g.season = pbs.season "
        "LEFT JOIN teams t ON t.team_id = pbs.team_id LEFT JOIN teams o ON o.team_id = pbs.opponent_team_id"
    )
    return AnswerContext(con=c, out_dir=tmp_path)


def test_player_log_says_the_box_score_is_empty_not_that_the_games_are_missing(empty_season_ctx: AnswerContext) -> None:
    """ISSUES.md #72: naming a stat outside REBUILT_STATS sends the log back
    to the fetched lines - empty for his whole season - and the refusal
    names the missing fact: the box score, never "No games found for Anthony
    Davis", false of a man who played both of them (68 in the real 2015)."""
    answer = player_log(empty_season_ctx, Reading(who=Who(kind="player", players=("Anthony Davis",)), measure=Measure(key="turnovers", as_typed="turnovers"))).text
    assert answer == f"Anthony Davis played 2 games in the {current_season()} regular season, but the box score is empty for all of them - ESPN served no minutes or stats for any."


def test_player_line_narrowed_by_opponent_says_the_box_score_is_empty(empty_season_ctx: AnswerContext) -> None:
    """Any player's line narrowed by `opponent`, `venue` or `without` over an
    empty-box-score season names the empty box score, for a stat the rebuild
    does not trust either."""
    answer = player_line(
        empty_season_ctx, Reading(who=Who(kind="player", players=("Anthony Davis",)), cuts=Cuts(opponent="Los Angeles Lakers"), measure=Measure(key="turnovers", as_typed="turnovers"))
    ).text
    assert answer == f"Anthony Davis played 2 games in the {current_season()} regular season, but the box score is empty for all of them - ESPN served no minutes or stats for any."


def test_a_season_with_no_games_at_all_is_still_told_apart_from_an_empty_one(empty_season_ctx: AnswerContext) -> None:
    """The guard above must not fire for a season with no games in it at all -
    that is still "no games found", never "empty box score". A guard that
    always claimed an empty box score is exactly what this catches."""
    answer = player_log(empty_season_ctx, Reading(who=Who(kind="player", players=("Anthony Davis",)), span=Span(season=(current_season() - 5)))).text
    assert answer == f"No {current_season() - 5} regular season games found for Anthony Davis."


@pytest.fixture
def narrowed_rebuilt_ctx(tmp_path: Path) -> AnswerContext:
    """The same empty season as ``empty_season_ctx``, but with a `reconstructed`
    log the way the real warehouse builds one: `player_box_stats` keeps ESPN's
    zeroed rows, and `player_game_log` carries the figures rebuilt from
    play-by-play, `reconstructed` marking exactly those - the split
    `rebuilt_ctx` above uses, with the `team_id`/`opponent_team_id` columns
    a player's narrowed line (``opponent``/``venue``/``without``) reads and
    `rebuilt_ctx` does not carry."""
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    c.execute("INSERT INTO players VALUES ('1','Anthony Davis')")
    c.execute("CREATE TABLE teams (team_id VARCHAR, abbreviation VARCHAR, display_name VARCHAR)")
    c.execute("INSERT INTO teams VALUES ('1','NO','New Orleans Pelicans'),('2','LAL','Los Angeles Lakers'),('3','BOS','Boston Celtics')")
    c.execute(
        "CREATE TABLE games (event_id VARCHAR, season INTEGER, season_type INTEGER, date VARCHAR, home_team_id VARCHAR, away_team_id VARCHAR, "
        "home_score INTEGER, away_score INTEGER, winner_team_id VARCHAR)"
    )
    s = current_season()
    c.executemany(
        "INSERT INTO games VALUES (?, ?, 2, ?, ?, ?, ?, ?, ?)",
        [("e1", s, f"{s - 1}-11-02T00:30Z", "2", "1", 100, 90, "2"), ("e2", s, f"{s - 1}-12-02T00:30Z", "1", "2", 95, 92, "1")],
    )
    c.execute(f"CREATE TABLE player_box_stats ({_BOX_COLUMNS})")
    c.executemany(
        f"INSERT INTO player_box_stats VALUES ({', '.join('?' for _ in range(24))})",
        [_box("e1", s, "1", "2", "1", minutes=None), _box("e2", s, "1", "2", "1", minutes=None)],
    )
    # rebounds/assists are here even though no test reads them: a player's
    # log (compose.logs._LOG_BASE) always shows MIN/PTS/REB/AST, so a default
    # (no-stat) log needs the columns present whatever `stat` was asked for.
    c.execute(
        "CREATE TABLE player_game_log (event_id VARCHAR, season INTEGER, season_type INTEGER, team_id VARCHAR, opponent_team_id VARCHAR, athlete_id VARCHAR, "
        "player_name VARCHAR, did_not_play BOOLEAN, minutes INTEGER, points INTEGER, rebounds INTEGER, assists INTEGER, turnovers INTEGER, fouls INTEGER, "
        "fieldGoalsMade INTEGER, fieldGoalsAttempted INTEGER, game_date VARCHAR, opponent_abbr VARCHAR, reconstructed BOOLEAN)"
    )
    c.executemany(
        "INSERT INTO player_game_log VALUES (?,?,2,'1','2','1','Anthony Davis',FALSE,NULL,?,?,?,?,?,?,?,?,'LAL',TRUE)",
        [("e1", s, 24, 8, 3, 3, 2, 9, 18, f"{s - 1}-11-01"), ("e2", s, 18, 6, 4, 5, 4, 7, 15, f"{s - 1}-12-01")],
    )
    _with_full_line_columns(c, "player_game_log", _GAME_FULL_LINE_COLUMNS)
    # The filled view the log is built over on a real warehouse: both of his
    # games rebuilt, as the log's flags say.
    c.execute("CREATE VIEW player_box_stats_filled AS SELECT pbs.*, TRUE AS reconstructed FROM player_box_stats pbs")
    return AnswerContext(con=c, out_dir=tmp_path)


def test_player_line_reads_rebuilt_games_before_deciding_none_matched_the_opponent(narrowed_rebuilt_ctx: AnswerContext) -> None:
    """The player's line's mirror of the log's case below: both of Anthony
    Davis's games here are covered by the rebuild, so a `points` question
    narrowed to an opponent he never faced is missing the OPPONENT, not a box
    score - ``player_relation.no_narrowed_games`` is given the same `rebuilt`
    reading the line's own compiled statement used, or its diagnostic would
    be stricter than the answer it is explaining."""
    answer = player_line(narrowed_rebuilt_ctx, Reading(who=Who(kind="player", players=("Anthony Davis",)), cuts=Cuts(opponent="Boston Celtics"), measure=Measure(key="points", as_typed="points"))).text
    assert answer == f"Anthony Davis played 2 games in the {current_season()} regular season, none of them vs the Boston Celtics."
    assert "empty box score" not in answer


def test_player_log_reads_rebuilt_games_before_deciding_none_matched_the_opponent(narrowed_rebuilt_ctx: AnswerContext) -> None:
    """The fact that is really missing when a rebuilt season is narrowed to an
    opponent it never faced is the opponent, not the season - and telling them
    apart needs ``player_relation.no_narrowed_games`` to check for a recorded
    game under the SAME `rebuilt` reading the caller's own query used. Both of
    Anthony Davis's games here are covered by the rebuild (`reconstructed`),
    so the season is NOT "empty box score" from the log's point of view;
    asking about an opponent he never faced must say so, not repeat the
    box-score message."""
    answer = player_log(narrowed_rebuilt_ctx, Reading(who=Who(kind="player", players=("Anthony Davis",)), cuts=Cuts(opponent="Boston Celtics"))).text
    assert answer == f"Anthony Davis played 2 games in the {current_season()} regular season, none of them vs the Boston Celtics."
    assert "empty box score" not in answer


def test_a_narrowed_player_line_reads_a_rebuilt_line_for_a_trusted_stat(narrowed_rebuilt_ctx: AnswerContext) -> None:
    """Any narrowing (``opponent``, ``venue`` or ``without``) of a player's
    line reads the rebuilt lines too, where the stored table is empty for
    every one of these games - for points, the one stat the rebuild is
    trusted for: "Anthony Davis points vs the Lakers in 2015" is answered,
    never the wrong-cause refusal."""
    result = player_line(narrowed_rebuilt_ctx, Reading(who=Who(kind="player", players=("Anthony Davis",)), cuts=Cuts(opponent="Los Angeles Lakers"), measure=Measure(key="points", as_typed="points")))
    assert result.data["stats"]["gamesPlayed"] == 2
    assert result.data["stats"]["avgPoints"] == 21.0
    assert "no games found" not in result.text.lower()
    assert "empty box score" not in result.text
    assert "2 of these games have no box score from ESPN" in result.text
    assert "rebuilt from play-by-play" in result.text


def test_a_made_count_over_rebuilt_lines_says_no_attempts_it_cannot_count(narrowed_rebuilt_ctx: AnswerContext) -> None:
    """Field goals made is a stat a rebuild gets right, so the line reads
    both rebuilt games - but their attempts are outside what the rebuild was
    measured for (`UNGATED_ON_REBUILD`), so "16 of 33 (48.5%)" would be a
    percentage of attempts nothing may read. The compiled line reads a made
    count's attempts the way its rate does (over the games that carry them)
    and says the total alone where rebuilt lines are in it."""
    result = player_line(
        narrowed_rebuilt_ctx, Reading(who=Who(kind="player", players=("Anthony Davis",)), cuts=Cuts(opponent="Los Angeles Lakers"), measure=Measure(key="fieldGoalsMade", as_typed="fieldGoalsMade"))
    )
    assert result.data["stats"]["fieldGoalsMade"] == 16
    assert "fieldGoalsAttempted" not in result.data["stats"]
    assert "That is 16 in total." in result.text
    assert " of 33 " not in result.text


def test_a_narrowed_player_line_refuses_a_rebuilt_line_for_an_untrusted_stat(narrowed_rebuilt_ctx: AnswerContext) -> None:
    """Turnovers are outside REBUILT_STATS, so the narrowed reading does not
    widen to the rebuild even though one exists here - and the refusal names
    the fact that is really missing (the box score, not the season)."""
    answer = player_line(
        narrowed_rebuilt_ctx, Reading(who=Who(kind="player", players=("Anthony Davis",)), cuts=Cuts(opponent="Los Angeles Lakers"), measure=Measure(key="turnovers", as_typed="turnovers"))
    ).text
    assert answer == f"Anthony Davis played 2 games in the {current_season()} regular season, but the box score is empty for all of them - ESPN served no minutes or stats for any."


def test_a_narrowed_player_line_never_reads_a_shooting_percentage_from_a_rebuilt_line(narrowed_rebuilt_ctx: AnswerContext) -> None:
    """A rebuilt line's attempts are outside what the rebuild was measured for
    (`UNGATED_ON_REBUILD`), so a shooting percentage must never widen to it
    even though makes and attempts are both present here. Asserted directly on
    the compiler's rule (the line the reader compiles: the rate as its one
    measure) as well as through the answer, since the answer alone cannot
    tell this rule apart from the reader's own choice of measures."""
    con = narrowed_rebuilt_ctx.con
    scope = Reading(who=Who(kind="player", players=("Anthony Davis",)), cuts=Cuts(opponent="Los Angeles Lakers"))
    assert compile_query(con, Query(**scope.filters(), skeleton="scalar", aggregate=Aggregate.LINE, measures=["points"])).rebuilt is True
    assert compile_query(con, Query(**scope.filters(), skeleton="scalar", aggregate=Aggregate.LINE, measures=["fg_pct"])).rebuilt is False
    assert compile_query(con, Query(**scope.filters(), skeleton="scalar", aggregate=Aggregate.LINE, measures=["points", "fg_pct"])).rebuilt is False
    answer = player_line(
        narrowed_rebuilt_ctx, Reading(who=Who(kind="player", players=("Anthony Davis",)), cuts=Cuts(opponent="Los Angeles Lakers"), measure=Measure(key="fieldGoalPct", as_typed="fieldGoalPct"))
    ).text
    assert answer == f"Anthony Davis played 2 games in the {current_season()} regular season, but the box score is empty for all of them - ESPN served no minutes or stats for any."


def test_season_history_career_is_every_season(ps_con: AnswerContext) -> None:
    """ "Jokic's scoring by year, career" is every season, never the default
    four under a heading that does not say how many it left out."""
    s = current_season()
    for offset in range(1, 8):
        ps_con.con.execute("INSERT INTO player_season_stats_deduped (athlete_id, season, season_type, gamesPlayed, avgPoints) VALUES ('1',?,2,70,20.0)", [s - offset])
    result = season_history(ps_con, Reading(who=Who(kind="player", players=("Luka Doncic",)), measure=Measure(key="points", as_typed="points"), span=Span(career=True), window=Window(count=4)))
    assert len(result.data["seasons"]) == 8
    assert result.text.startswith(f"Luka Doncic, points per game by regular season, career, {s - 7}-{s} (most recent first):")
    four = season_history(ps_con, Reading(who=Who(kind="player", players=("Luka Doncic",)), measure=Measure(key="points", as_typed="points"))).text
    assert four.startswith(f"Luka Doncic, points per game by regular season, {s - 3}-{s} (most recent first):")


def test_season_history_career_states_the_combined_percentage(ps_con: AnswerContext) -> None:
    """F041 (ISSUES.md): "show me sga's career 2pt percentage" states the
    combined career figure the question asked for beneath the
    season-by-season table. The career line is the games-weighted total
    (makes/attempts summed), never a mean of the per-season percentages -
    which would give a different, wrong number here ((60% + 20%) / 2 = 40%,
    not the true (600+40)/(1000+200) = 53.3%)."""
    for col in ("fieldGoalsMade", "fieldGoalsAttempted", "threePointFieldGoalsMade", "threePointFieldGoalsAttempted"):
        ps_con.con.execute(f"ALTER TABLE player_season_stats_deduped ADD COLUMN IF NOT EXISTS {col} INTEGER")
    s = current_season()
    ps_con.con.execute(
        "UPDATE player_season_stats_deduped SET fieldGoalsMade=600, fieldGoalsAttempted=1000, threePointFieldGoalsMade=0, threePointFieldGoalsAttempted=0 WHERE athlete_id='1' AND season=?", [s]
    )
    ps_con.con.execute(
        "INSERT INTO player_season_stats_deduped "
        "(athlete_id, season, season_type, gamesPlayed, avgPoints, fieldGoalsMade, fieldGoalsAttempted, threePointFieldGoalsMade, threePointFieldGoalsAttempted) "
        "VALUES ('1',?,2,70,20.0,40,200,0,0)",
        [s - 1],
    )
    answer = (
        season_history(ps_con, Reading(who=Who(kind="player", players=("Luka Doncic",)), measure=Measure(key="twoPointFieldGoalPct", as_typed="twoPointFieldGoalPct"), span=Span(career=True))).text
        or ""
    )
    assert answer.endswith("Luka Doncic's career 2PT%: 53.3% (640 of 1,200).")
    # No span: the default four-season table (here, both rows) states no
    # combined figure - a window nobody asked to see summed.
    windowed = season_history(ps_con, Reading(who=Who(kind="player", players=("Luka Doncic",)), measure=Measure(key="twoPointFieldGoalPct", as_typed="twoPointFieldGoalPct"))).text or ""
    assert "career" not in windowed


def test_season_history_career_states_the_combined_total(ps_con: AnswerContext) -> None:
    """The counting-stat sibling of the percentage career line above: the
    plain career total, summed the way ``season_line.career_statement`` sums one."""
    s = current_season()
    ps_con.con.execute("INSERT INTO player_season_stats_deduped (athlete_id, season, season_type, gamesPlayed, avgPoints, points) VALUES ('1',?,2,70,20.0,1400)", [s - 1])
    answer = season_history(ps_con, Reading(who=Who(kind="player", players=("Luka Doncic",)), measure=Measure(key="points", as_typed="points"), span=Span(career=True))).text or ""
    # Base row (ps_con): 2,143 total points; plus the season just added: 1,400.
    assert answer.endswith("Luka Doncic's career total: 3,543 points.")


def test_a_teams_log_honors_opponent_and_venue(gl_con: AnswerContext) -> None:
    home = player_log(gl_con, Reading(who=Who(kind="team", teams=("Knicks",)), cuts=Cuts(venue="home")))
    assert [g["date"] for g in home.data["games"]] == ["2026-04-10"]
    assert home.text.startswith(f"New York Knicks at home, most recent game of the {current_season()} regular season (1-0):")
    assert len(player_log(gl_con, Reading(who=Who(kind="team", teams=("Knicks",)), cuts=Cuts(opponent="Boston Celtics"))).data["games"]) == 2


def test_a_teams_log_says_when_it_never_met_the_opponent(gl_con: AnswerContext) -> None:
    gl_con.con.execute("INSERT INTO teams VALUES ('13','LAL','Los Angeles Lakers')")
    answer = player_log(gl_con, Reading(who=Who(kind="team", teams=("Knicks",)), cuts=Cuts(opponent="Los Angeles Lakers"))).text
    assert answer == f"The New York Knicks played 2 games in the {current_season()} regular season, none of them vs the Los Angeles Lakers."


def test_a_teams_log_with_a_teammate_absent_is_declined(gl_con: AnswerContext) -> None:
    with pytest.raises(Unsupported):
        player_log(gl_con, Reading(who=Who(kind="team", teams=("Knicks",)), companions=(Companion(player="Jalen Brunson", predicate="absent"),)))


def test_a_teams_log_career_says_all_time(gl_con: AnswerContext) -> None:
    result = player_log(gl_con, Reading(who=Who(kind="team", teams=("Knicks",)), span=Span(career=True)))
    assert len(result.data["games"]) == 2
    assert f"(all-time, {current_season()} regular season) (1-1):" in result.text


def test_a_teams_log_over_a_range_names_the_range_not_all_time(gl_con: AnswerContext) -> None:
    """ "hornets on the road vs magic last 2 seasons": a log over a range says
    the range ("2025-2026 regular seasons"), never "all-time"."""
    result = player_log(gl_con, Reading(who=Who(kind="team", teams=("Knicks",)), span=Span(since=current_season())))
    assert f"(since {current_season()}, {current_season()} regular season) (1-1):" in result.text
    assert "all-time" not in result.text


def test_a_teams_log_is_not_doubled_by_a_phantom_season(gl_con: AnswerContext) -> None:
    """The phantom 1993 shares every event id with 1994, so a join on
    event_id alone would list each of those games twice - 164 rows for the
    Celtics' 82. The log joins on the season too."""
    gl_con.con.execute("INSERT INTO games VALUES ('e1',?,2,'2026-04-10T22:00Z','18','2',112,95,'18',false,'New York')", [current_season() - 1])
    # Rebuilt, or the new row never reaches the list the log reads and this
    # asserts 2 without having exercised the season-keyed join at all. The two
    # rows are in different seasons, so `real_games` keeps both - collapsing a
    # season label is TEAM_GAMES_SQL's job, not its.
    real_games.build_table(gl_con.con, {"games", "teams"})
    assert len(player_log(gl_con, Reading(who=Who(kind="team", teams=("Knicks",)))).data["games"]) == 2


def test_a_teams_log_does_not_call_a_missing_result_a_loss(gl_con: AnswerContext) -> None:
    """ "Not the winner" is not the same fact as "lost".

    Perturbed into `real_games` rather than into `games` underneath it,
    because the two are different questions: a row with no winner is a 0-0
    placeholder, which `real_games` drops, so nulling one in `games` would
    test that the game disappears rather than that a missing result is not a
    loss. The guard is defensive - the filtered list has no NULL winner in
    it - and this is the way to watch it work."""
    gl_con.con.execute("UPDATE real_games SET winner_team_id = NULL WHERE event_id = 'e2'")
    result = player_log(gl_con, Reading(who=Who(kind="team", teams=("Knicks",))))
    assert (result.data["wins"], result.data["losses"]) == (1, 0)
    assert "(1-0, 1 with no recorded result):" in result.text


# ---------------- playoffs before 1993-94, by the year they were played ----------------


@pytest.fixture
def playoff_ctx(tmp_path: Path) -> AnswerContext:
    """ESPN's labels before 1993-94: the postseason games labeled 1990 are the
    1991 Finals, and the phantom 1993 is a copy of 1994's games."""
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE teams (team_id VARCHAR, display_name VARCHAR, abbreviation VARCHAR)")
    c.execute("INSERT INTO teams VALUES ('4','Chicago Bulls','CHI'),('13','Los Angeles Lakers','LAL')")
    c.execute(
        "CREATE TABLE games (event_id VARCHAR, season INTEGER, season_type INTEGER, date VARCHAR, home_team_id VARCHAR, away_team_id VARCHAR, "
        "home_score INTEGER, away_score INTEGER, winner_team_id VARCHAR, home_linescores VARCHAR, away_linescores VARCHAR, neutral_site BOOLEAN, venue_city VARCHAR)"
    )
    rows = [
        ("f1", 1990, 3, "1991-06-03T01:00Z", "4", "13", 91, 93, "13", "20,25,20,26", "25,20,24,24", False, "Chicago"),
        ("f2", 1990, 3, "1991-06-13T01:00Z", "13", "4", 101, 108, "4", "25,25,25,26", "27,27,27,27", False, "Los Angeles"),
        ("x1", 1993, 3, "1994-05-01T01:00Z", "4", "13", 100, 90, "4", "25,25,25,25", "20,20,25,25", False, "Chicago"),
        ("x1", 1994, 3, "1994-05-01T01:00Z", "4", "13", 100, 90, "4", "25,25,25,25", "20,20,25,25", False, "Chicago"),
    ]
    c.executemany("INSERT INTO games VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    c.execute("CREATE TABLE team_box_stats (event_id VARCHAR, season INTEGER, season_type INTEGER, team_id VARCHAR, opponent_team_id VARCHAR, home_away VARCHAR)")
    boxes: list[tuple[Any, ...]] = []
    for event, season, season_type, _date, home, away, *_rest in rows:
        boxes += [(event, season, season_type, home, away, "home"), (event, season, season_type, away, home, "away")]
    c.executemany("INSERT INTO team_box_stats VALUES (?,?,?,?,?,?)", boxes)
    real_games.build_table(c, {"games", "teams"})
    return AnswerContext(con=c, out_dir=tmp_path)


def test_an_early_playoffs_is_found_by_the_year_it_was_played(playoff_ctx: AnswerContext) -> None:
    """ESPN labels the 1991 Finals season 1990, so a postseason is matched by
    the year it was played: "the 1991 playoffs" is 1991's, never 1992's, and
    "the 1990 playoffs" never 1991's."""
    both = Who(kind="teams", teams=("Chicago Bulls", "Los Angeles Lakers"))
    assert team_meetings(playoff_ctx, Reading(who=both, span=Span(season=1991, season_type=3))).data["games"] == 2
    assert team_meetings(playoff_ctx, Reading(who=both, span=Span(season=1990, season_type=3))).data["games"] == 0


def test_the_phantom_season_does_not_double_a_playoffs(playoff_ctx: AnswerContext) -> None:
    assert team_meetings(playoff_ctx, Reading(who=Who(kind="teams", teams=("Chicago Bulls", "Los Angeles Lakers")), span=Span(season=1994, season_type=3))).data["games"] == 1


def test_team_period_total_finds_an_early_playoffs_by_its_year(playoff_ctx: AnswerContext) -> None:
    got = team_period_total(playoff_ctx, Reading(who=Who(kind="team", teams=("Chicago Bulls",)), span=Span(season=1991, season_type=3), period=Period(number=1)))
    assert [g["points"] for g in got.data["games"]] == [20, 27]


def test_a_team_log_labels_an_early_playoffs_by_its_year(playoff_ctx: AnswerContext) -> None:
    got = player_log(playoff_ctx, Reading(who=Who(kind="team", teams=("Chicago Bulls",)), span=Span(season=1991, season_type=3)))
    assert [g["season"] for g in got.data["games"]] == [1991, 1991]


def test_team_meetings_takes_the_opponent_as_the_other_team(playoff_ctx: AnswerContext) -> None:
    """ "Celtics vs Bulls head to head record", read as a team and an opponent, is the two teams' meetings."""
    got = team_meetings(playoff_ctx, Reading(who=Who(kind="team", teams=("Chicago Bulls",)), cuts=Cuts(opponent="Los Angeles Lakers"), span=Span(season=1991, season_type=3)))
    assert got.data["games"] == 2


def test_team_meetings_reads_past_a_team_named_twice(playoff_ctx: AnswerContext) -> None:
    """The first two names are one team; the opponent after them is the second."""
    scope = Reading(who=Who(kind="teams", teams=("Chicago Bulls", "Bulls")), cuts=Cuts(opponent="Los Angeles Lakers"), span=Span(season=1991, season_type=3))
    assert team_meetings(playoff_ctx, scope).data["games"] == 2


# ---------------- a period's log ----------------

SEASON = current_season()


@pytest.fixture
def period_ctx(tmp_path: Path) -> AnswerContext:
    """One player, six games, and shots whose value has to be DERIVED rather
    than read off a label - which is the whole point of a period's line.

    Two of the games exist to hold the denominator honest. In e5 he played and
    scored only in the second quarter, so it is a first-quarter game worth
    ZERO - a game dropped there would inflate every average. In e6
    he did not play at all, so it is no game of his in any period, even though
    a teammate's shots mean the shot table covers it.

    Every made shot here is 26 feet from the rim at (25, 0), a real three's
    position, and carries `points_attempted = 0`, which is ESPN's "unlabeled"
    and not "zero points". Scored by the label alone each of these is worth
    nothing; scored by its position each is worth 3. The fixture is built that
    way on purpose: it is the shape that makes per-quarter points read 76.8%
    against ESPN's own linescores without `SHOT_VALUE_SQL`.
    """
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    c.execute("CREATE TABLE teams (team_id VARCHAR, abbreviation VARCHAR, display_name VARCHAR)")
    c.execute(
        "CREATE TABLE games (event_id VARCHAR, season BIGINT, season_type BIGINT, date VARCHAR, home_team_id VARCHAR, away_team_id VARCHAR, "
        "home_score BIGINT, away_score BIGINT, winner_team_id VARCHAR)"
    )
    c.execute("CREATE TABLE player_box_stats (event_id VARCHAR, season BIGINT, season_type BIGINT, team_id VARCHAR, athlete_id VARCHAR, did_not_play BOOLEAN, minutes BIGINT)")
    c.execute(
        "CREATE TABLE shot_chart (athlete_id VARCHAR, season INTEGER, season_type INTEGER, event_id VARCHAR, team_id VARCHAR, "
        "period INTEGER, clock VARCHAR, made BOOLEAN, shot_type VARCHAR, coordinate_x INTEGER, coordinate_y INTEGER, points_attempted INTEGER, description VARCHAR)"
    )
    c.execute("INSERT INTO players VALUES ('1','Stephen Curry')")
    c.execute("INSERT INTO teams VALUES ('9','GS','Golden State Warriors'),('13','LAL','Los Angeles Lakers'),('2','BOS','Boston Celtics')")
    # e1 and e3 at home against the Lakers, e2 away at Boston, e4 and e5 at home
    # vs Boston, e6 at home vs the Lakers.
    for event, home, away in (("e1", "9", "13"), ("e2", "2", "9"), ("e3", "9", "13"), ("e4", "9", "2"), ("e5", "9", "2"), ("e6", "9", "13")):
        c.execute("INSERT INTO games VALUES (?,?,2,?,?,?,110,100,?)", [event, SEASON, f"{SEASON - 1}-11-0{event[-1]}T00:30Z", home, away, home])
    for event, dnp in (("e1", False), ("e2", False), ("e3", False), ("e4", False), ("e5", False), ("e6", True)):
        c.execute("INSERT INTO player_box_stats VALUES (?,?,2,'9','1',?,?)", [event, SEASON, dnp, None if dnp else 30])
    # e7 he played, and the shot table holds nothing for the game at all -
    # 2003's shots cover 986 of its games. It is no game here rather than a
    # confident zero, which would drag the average down for a gap in the data.
    c.execute("INSERT INTO games VALUES ('e7',?,2,?,'9','13',110,100,'9')", [SEASON, f"{SEASON - 1}-11-07T00:30Z"])
    c.execute("INSERT INTO player_box_stats VALUES ('e7',?,2,'9','1',FALSE,30)", [SEASON])
    # A teammate's make in e6, so the shot table covers the game he sat out.
    c.execute("INSERT INTO shot_chart VALUES ('7',?,2,'e6','9',1,'10:00',TRUE,'Jump Shot',25,26,0,'26-foot jumper')", [SEASON])
    # (event, period, made, n) - an unlabeled 26-foot three each time.
    for event, period, made, n in (
        ("e1", 1, True, 2),
        ("e1", 1, False, 3),
        ("e1", 3, True, 1),
        ("e2", 1, True, 1),
        ("e2", 4, True, 2),
        ("e3", 2, True, 1),
        ("e3", 3, True, 1),
        ("e4", 1, True, 1),
        ("e4", 5, True, 1),
        ("e5", 2, True, 1),
    ):
        for _ in range(n):
            c.execute(
                "INSERT INTO shot_chart VALUES ('1',?,2,?,'9',?,'10:00',?,'Jump Shot',25,26,0,'26-foot jumper')",
                [SEASON, event, period, made],
            )
    # One 2004 game, so the season-accuracy caveat has something to attach to.
    # 2004 is labeled, unlike the rows above, because before
    # TEXT_NAMES_EVERY_THREE_UNTIL the description is what establishes a three.
    c.execute("INSERT INTO games VALUES ('e04',2004,2,'2003-11-05T00:30Z','9','13',110,100,'9')")
    c.execute("INSERT INTO player_box_stats VALUES ('e04',2004,2,'9','1',FALSE,30)")
    c.execute("INSERT INTO shot_chart VALUES ('1',2004,2,'e04','9',1,'10:00',TRUE,'Jump Shot',25,26,3,'26-foot three point jumper')")
    # A teammate who plays e1-e3 and sits e4-e5, so "without him" is two games.
    c.execute("INSERT INTO players VALUES ('2','Klay Thompson')")
    for event in ("e1", "e2", "e3"):
        c.execute("INSERT INTO player_box_stats VALUES (?,?,2,'9','2',FALSE,28)", [event, SEASON])
    # The relation reads the warehouse's log view, not the stored table, so the
    # fixture mirrors its shape: the opponent, the start flag and the game's
    # date are columns there and derived here.
    c.execute(
        "CREATE VIEW player_game_log AS SELECT pbs.*, "
        "CASE WHEN g.home_team_id = pbs.team_id THEN g.away_team_id ELSE g.home_team_id END AS opponent_team_id, "
        "TRUE AS starter, g.date AS game_date, p.display_name AS player_name, o.abbreviation AS opponent_abbr "
        "FROM player_box_stats pbs JOIN games g ON g.event_id = pbs.event_id AND g.season = pbs.season "
        "LEFT JOIN players p ON p.athlete_id = pbs.athlete_id "
        "LEFT JOIN teams o ON o.team_id = CASE WHEN g.home_team_id = pbs.team_id THEN g.away_team_id ELSE g.home_team_id END"
    )
    return AnswerContext(con=c, out_dir=tmp_path / "out")


def test_a_quarter_is_summed_from_the_shots_position_not_its_label(period_ctx: AnswerContext) -> None:
    """Curry's first quarters: 2 threes in e1, 1 in e2, 1 in e4 - 12 points.
    Every one carries `points_attempted = 0`, so a reader that trusted ESPN's
    label would answer 0, and one that looked for the words "three point" in
    the description would answer 8. Only the position gives 12. Misses do not
    count.

    **Five games, not three.** e3 and e5 are games he played without a
    first-quarter make, and they are zeros in the average rather than absent
    from it; e6 he sat out. Counting only the games with a make would answer
    4.0 here - and "RJ Barrett ... over 46 games, averaging 5.4" for a player
    who played 57 and averaged 4.4, which is a fluent wrong number whose sum
    was right.
    """
    result = period_log(period_ctx, Reading(who=Who(kind="player", players=("Stephen Curry",)), span=Span(season=SEASON, season_type=2), period=Period(number=1)))
    assert result.data["total"] == 12
    assert result.data["games_played"] == 5, "e1-e5; e6 is a DNP and e7 has no shot data at all"
    assert result.data["average"] == pytest.approx(2.4), "12 over 5 played games, not 4.0 over the 3 with a make"
    assert "12 points in the 1st quarter over 5 games" in (result.text or "")


def test_a_period_line_with_no_stat_named_says_the_whole_line_beneath_its_figure(period_ctx: AnswerContext) -> None:
    """ "lebron james 4th qtr stats 2026" said his 4th-quarter points alone
    where the feed sample's key required the rest of the line (ISSUES.md
    #471): with no stat named, the period's whole line per game is beneath
    the figure - every column but the points it said, the shooting with its
    makes and attempts summed over the games, and "-" for a column this
    warehouse rebuilt nothing for (the fixture holds shots and no plays)."""
    result = period_log(period_ctx, Reading(who=Who(kind="player", players=("Stephen Curry",)), span=Span(season=SEASON, season_type=2), period=Period(number=1)))
    assert result.text == (
        f"Stephen Curry scored 12 points in the 1st quarter over 5 games of the {SEASON} regular season, averaging 2.4.\n"
        "  REB  AST  STL  BLK  TOV  PF         FG%        3PT%      FT%\n"
        "    -    -    -    -    -   -  57.1 (4-7)  57.1 (4-7)  - (0-0)"
    )
    named = period_log(
        period_ctx, Reading(who=Who(kind="player", players=("Stephen Curry",)), span=Span(season=SEASON, season_type=2), period=Period(number=1), measure=Measure(key="points", as_typed="points"))
    )
    assert "\n" not in (named.text or "")


def test_a_half_is_the_two_quarters_it_holds_and_never_overtime(period_ctx: AnswerContext) -> None:
    """First half is periods 1 and 2; second half is 3 and 4. e4's overtime
    three belongs to neither - a game that went to overtime still had a second
    half, and folding OT in would quietly answer a different question for
    exactly the games people ask about most."""
    first = period_log(period_ctx, Reading(who=Who(kind="player", players=("Stephen Curry",)), span=Span(season=SEASON, season_type=2), period=Period(number=1, half=True)))
    second = period_log(period_ctx, Reading(who=Who(kind="player", players=("Stephen Curry",)), span=Span(season=SEASON, season_type=2), period=Period(number=2, half=True)))
    assert first.data["total"] == 18, "e1 2, e2 1, e3 1 (Q2), e4 1, e5 1 (Q2) - threes all"
    assert second.data["total"] == 12, "e1 Q3, e2 two in Q4, e3 Q3 - and NOT e4's overtime"


def test_an_opponent_and_a_venue_narrow_which_games_count(period_ctx: AnswerContext) -> None:
    """Both are cells a period's log states, so both filter rather than
    refuse. Against the Lakers: e1 and e3. At home: e1, e3, e4 and e5."""
    vs_lakers = period_log(period_ctx, Reading(who=Who(kind="player", players=("Stephen Curry",)), cuts=Cuts(opponent="Lakers"), span=Span(season=SEASON, season_type=2), period=Period(number=1)))
    assert vs_lakers.data["total"] == 6 and vs_lakers.data["games_played"] == 2, "e1 and e3; e3 is a scoreless first quarter, not a missing game"
    at_home = period_log(period_ctx, Reading(who=Who(kind="player", players=("Stephen Curry",)), cuts=Cuts(venue="home"), span=Span(season=SEASON, season_type=2), period=Period(number=1)))
    assert at_home.data["total"] == 9 and at_home.data["games_played"] == 4, "e1, e3, e4, e5 at home; e2 away; e6 a DNP"


def test_a_season_whose_shots_cannot_be_valued_is_refused(period_ctx: AnswerContext) -> None:
    """2002 is `UNSEPARABLE_SHOT_VALUES`: `SHOT_VALUE_SQL` is NULL for 20,534 of
    its made shots, so a sum over them means nothing. Measured against ESPN's
    linescores it reconciles 4.9% of the time. The refusal names that, rather
    than reporting a number nobody should read."""
    answer = period_log(period_ctx, Reading(who=Who(kind="player", players=("Stephen Curry",)), span=Span(season=2002, season_type=2), period=Period(number=1))).text or ""
    assert "cannot be answered for 2002" in answer


def test_the_worst_reconcilable_season_is_refused_and_the_merely_poor_ones_are_caveated(period_ctx: AnswerContext) -> None:
    """2016 reconciles at 76.5% - one quarter in four - and is refused. 2004 is
    95.7%, which is worth answering with the figure attached rather than
    withholding. `PERIOD_REFUSE_BELOW` sits between them on purpose."""
    assert "cannot be answered for 2016" in (
        period_log(period_ctx, Reading(who=Who(kind="player", players=("Stephen Curry",)), span=Span(season=2016, season_type=2), period=Period(number=1))).text or ""
    )
    caveated = period_log(period_ctx, Reading(who=Who(kind="player", players=("Stephen Curry",)), span=Span(season=2004, season_type=2), period=Period(number=1))).text or ""
    assert "96% of the time" in caveated, "a poor season answers, and says how poor"


def test_a_stat_the_period_line_cannot_rebuild_is_refused_rather_than_approximated(period_ctx: AnswerContext) -> None:
    """Play-by-play records no minutes per quarter, so a period's minutes are
    refused by name rather than read off the whole game's box. And rebounds,
    which the plays DO carry, are refused where the warehouse holds no plays
    (this fixture) - summed as zeros they would answer "no rebounds"."""
    minutes = period_log(
        period_ctx, Reading(who=Who(kind="player", players=("Stephen Curry",)), measure=Measure(key="minutes", as_typed="minutes"), span=Span(season=SEASON, season_type=2), period=Period(number=1))
    ).text
    assert minutes is not None and minutes.startswith("A quarter or half has no per-period 'minutes' - the period's line rebuilds points, fieldGoalsMade,")
    assert minutes.endswith("from the plays, and a field goal, 3-point or free throw percentage is a ratio of those; nothing else.")
    # An advanced rate is not a ratio of two period-line columns.
    ts = period_log(
        period_ctx, Reading(who=Who(kind="player", players=("Stephen Curry",)), measure=Measure(key="ts_pct", as_typed="ts_pct"), span=Span(season=SEASON, season_type=2), period=Period(number=1))
    ).text
    assert ts is not None and ts.startswith("A quarter or half has no per-period 'ts_pct'")
    answer = period_log(
        period_ctx, Reading(who=Who(kind="player", players=("Stephen Curry",)), measure=Measure(key="rebounds", as_typed="rebounds"), span=Span(season=SEASON, season_type=2), period=Period(number=1))
    ).text
    assert answer == "Per-quarter rebounds cannot be answered here: they are rebuilt from play-by-play, and this warehouse holds none."


def test_a_shooting_percentage_in_a_period_is_the_periods_makes_over_its_attempts(period_ctx: AnswerContext) -> None:
    """ "vj edgecombe 1st quarter 3pt percentage by game" (yardstick-v2): the
    period line holds the makes and the attempts, so the percentage is the
    ratio of the two sums. Curry's first quarters: 2 of 5 in e1 (two makes,
    three misses), 1 of 1 in e2 and e4, nothing attempted in e3 and e5 - 4 of
    7, 57.1%, over the same five games the points answer counts, and NEVER a
    mean of the per-game rates (which would be 60.0% over the three games
    with an attempt). Every shot in this fixture is a three, so the field
    goal percentage is the same ratio; no free throw was attempted at all,
    and the answer says that rather than printing a zero."""

    def curry(**more: Any) -> Reading:
        """Curry's first quarters this regular season, with ``more`` beside them."""
        return Reading(who=Who(kind="player", players=("Stephen Curry",)), span=Span(season=SEASON, season_type=2), period=Period(number=1), **more)

    result = period_log(period_ctx, curry(measure=measure_of("threePointFieldGoalPct", how="per_game")))
    assert (result.data["stat"], result.data["total"], result.data["attempted"], result.data["games_played"]) == ("three_pct", 4, 7, 5)
    assert result.data["average"] == pytest.approx(400 / 7)
    answer = result.text or ""
    assert "Stephen Curry shot 4 of 7 (57.1%) on 3-pointers in the 1st quarter over 5 games" in answer
    assert "averaging" not in answer, "the percentage is the average; a second figure beside it would be a mean of rates"
    assert "1st quarter 3-pointers made-attempted, every game:" in answer and "2-5     40.0%" in answer and "0-0         -" in answer
    assert [g["three_pct"] for g in result.data["games"]] == [40.0, 100.0, None, 100.0, None], "e1 through e5 in date order; a game with no attempt has no percentage"
    field_goals = period_log(period_ctx, curry(measure=measure_of("fieldGoalPct")))
    assert (field_goals.data["total"], field_goals.data["attempted"]) == (4, 7) and "shot 4 of 7 (57.1%) on field goals" in (field_goals.text or "")
    free_throws = period_log(period_ctx, curry(measure=measure_of("freeThrowPct")))
    assert (free_throws.data["total"], free_throws.data["attempted"], free_throws.data["average"]) == (0, 0, None)
    assert "Stephen Curry attempted no free throws in the 1st quarter over 5 games" in (free_throws.text or "")
    # One game says its own date, the way a column's one-game answer does.
    one = period_log(period_ctx, curry(measure=measure_of("threePointFieldGoalPct"), cuts=Cuts(opponent="Boston", venue="away")))
    assert "Stephen Curry shot 1 of 1 (100.0%) on 3-pointers in the 1st quarter at the Boston Celtics on" in (one.text or "")


def test_a_rate_is_refused_where_either_of_its_columns_is() -> None:
    """A percentage over a season whose makes or attempts cannot be trusted
    is refused as the column would be: 2002's shot values are unseparable,
    so its 3-point makes are refused (``player_games._PERIOD_SHOT_VALUED``) and the
    3-point percentage with them; free throws are not valued, so 2002's free
    throw percentage stands. Every rate the normalizer can name maps to a
    pair of period-line columns, and the ranking refuses them all."""
    assert set(PERIOD_RATE_STATS.values()) == set(PERIOD_RATES)
    for made, attempted in PERIOD_RATES.values():
        assert made in PERIOD_COLUMNS and attempted in PERIOD_COLUMNS
    refused = period_distrust(2002, "three_pct")
    assert refused is not None and refused["column"] == "threePointFieldGoalsMade" and refused["unseparable"]
    assert "cannot be answered for 2002" in refusal_phrase("period_untrusted", refused)
    assert period_distrust(2002, "ft_pct") is None
    assert period_distrust(SEASON, "fg_pct") is None


def test_period_ranking_does_not_rank_a_shooting_percentage(period_ctx: AnswerContext) -> None:
    """A games-played minimum is not an attempts minimum: ranked by
    first-quarter free throw percentage, the leader is whoever went 3 for 3.
    Refused naming that, before any read, and the refusal points at the one
    player's answer that IS built."""
    result = period_ranking(period_ctx, Reading(measure=Measure(key="freeThrowPct", as_typed="freeThrowPct"), span=Span(season=SEASON, season_type=2), period=Period(number=1)))
    assert "leaders" not in result.data and "Players are not ranked by free throw percentage in a quarter or half" in (result.text or "")


def test_a_stat_beyond_points_is_read_from_the_plays(period_ctx: AnswerContext) -> None:
    """A period's rebounds are the rebound plays in it - two in
    e1's first quarter, one in its third - and a quarter with none is a zero
    over a game he played, the denominator rule points already keep."""
    c = period_ctx.con
    c.execute("CREATE TABLE plays (event_id VARCHAR, season BIGINT, season_type BIGINT, period BIGINT, athlete_id VARCHAR, participant_athlete_ids VARCHAR, type VARCHAR, text VARCHAR)")
    for period in (1, 1, 3):
        c.execute("INSERT INTO plays VALUES ('e1', ?, 2, ?, '1', '1', 'Defensive Rebound', 'Stephen Curry defensive rebound')", [SEASON, period])
    result = period_log(
        period_ctx, Reading(who=Who(kind="player", players=("Stephen Curry",)), measure=Measure(key="rebounds", as_typed="rebounds"), span=Span(season=SEASON, season_type=2), period=Period(number=1))
    )
    assert result.data["total"] == 2 and result.data["games_played"] == 5
    assert (result.text or "").startswith("Stephen Curry had 2 rebounds in the 1st quarter over 5 games of the 2026 regular season, averaging 0.4.")


def test_a_period_read_says_the_team_its_games_were_cut_to(period_ctx: AnswerContext) -> None:
    """ "jrue holiday 1st half with celtics vs knicks": the games are cut to
    his own team's (the ``tenure`` cell), and the sentence says so - unsaid,
    the figure reads as every game of his."""
    result = period_log(
        period_ctx, Reading(who=Who(kind="player", players=("Stephen Curry",)), cuts=Cuts(tenure="Golden State Warriors"), span=Span(season=SEASON, season_type=2), period=Period(number=1))
    )
    assert (result.text or "").startswith(f"Stephen Curry scored 12 points in the 1st quarter over 5 games of the {SEASON} regular season with the Golden State Warriors, averaging 2.4.")


def _period_across_seasons_fixture(con: Any) -> None:
    """Beside the fixture's games: a 2016 game, a season whose per-period
    points agree with ESPN's quarter scores too seldom to answer (76.5%), and
    a postseason game this season - each with one first-quarter three."""
    con.execute("INSERT INTO games VALUES ('e16',2016,2,'2015-11-05T00:30Z','9','13',110,100,'9')")
    con.execute("INSERT INTO player_box_stats VALUES ('e16',2016,2,'9','1',FALSE,30)")
    con.execute("INSERT INTO shot_chart VALUES ('1',2016,2,'e16','9',1,'10:00',TRUE,'Jump Shot',25,26,3,'26-foot three point jumper')")
    con.execute("INSERT INTO games VALUES ('p1',?,3,?,'9','2',110,100,'9')", [SEASON, f"{SEASON}-04-25T00:30Z"])
    con.execute("INSERT INTO player_box_stats VALUES ('p1',?,3,'9','1',FALSE,30)", [SEASON])
    con.execute("INSERT INTO shot_chart VALUES ('1',?,3,'p1','9',1,'10:00',TRUE,'Jump Shot',25,26,0,'26-foot jumper')", [SEASON])


def test_a_period_read_across_seasons_caveats_each_and_leaves_an_untrusted_one_out(period_ctx: AnswerContext) -> None:
    """Decision D13 ("kyrie Irving second half stats vs denver" is his career
    against Denver, FS027): a quarter over every season reads them all,
    labeled by the first and last, each season's agreement said - and a
    season whose figures cannot be trusted (2016) is left out and said,
    rather than refusing the whole or summing it in."""
    _period_across_seasons_fixture(period_ctx.con)
    result = period_log(period_ctx, Reading(who=Who(kind="player", players=("Stephen Curry",)), span=Span(career=True), period=Period(number=1)))
    answer = result.text or ""
    assert answer.startswith("Stephen Curry scored 15 points in the 1st quarter over 6 games of his career (2004-2026 regular seasons), averaging 2.5.")
    assert "In 2004 that sum matches ESPN's own quarter scores 96% of the time" in answer
    assert "(Not counted: 1 game from 2016, whose per-quarter figures rebuilt from play-by-play agree with the official ones too seldom to answer.)" in answer


def test_a_period_stretch_reads_both_season_types_and_says_each(period_ctx: AnswerContext) -> None:
    """Decision D14: "last 3 games" in a quarter are the last three he
    played, the postseason game among them, and the heading says how many
    of each type, as the bare log's does."""
    _period_across_seasons_fixture(period_ctx.con)
    window = Window(count=3, order="recent", of="games")
    who = Who(kind="player", players=("Stephen Curry",))
    season = period_log(period_ctx, Reading(who=who, span=Span(season=SEASON, both=True), window=window, period=Period(number=1)))
    assert (season.text or "").startswith(
        f"Stephen Curry scored 6 points in the 1st quarter over the last 3 games of the {SEASON} regular season and postseason (2 regular season and 1 postseason), averaging 2.0."
    )
    career = period_log(period_ctx, Reading(who=who, span=Span(career=True, both=True), window=window, period=Period(number=1)))
    assert (career.text or "").startswith(
        f"Stephen Curry scored 6 points in the 1st quarter over the last 3 games of his career ({SEASON} regular season and postseason; 2 regular season and 1 postseason)"
    )


def test_four_quarters_across_seasons_refuse_a_season_that_cannot_be_trusted(period_ctx: AnswerContext) -> None:
    """One table over every season cannot leave a season's games out of one
    quarter and not another, so a season that cannot be trusted refuses the
    four quarters by name; without it, they are read across the seasons."""
    who = Who(kind="player", players=("Stephen Curry",))
    clean = period_log(period_ctx, Reading(who=who, span=Span(career=True)))
    assert (clean.text or "").startswith("Stephen Curry, points per game by quarter in his career (2004-2026 regular seasons) (6 games):")
    _period_across_seasons_fixture(period_ctx.con)
    refused = period_log(period_ctx, Reading(who=who, span=Span(career=True)))
    assert refused.text == "Per-quarter scoring cannot be answered for 2016: its per-period points agree with ESPN's own quarter scores only 76% of the time."


def test_a_question_with_no_period_is_the_four_quarters_side_by_side(period_ctx: AnswerContext) -> None:
    """ "Jokic points by quarter" (#162): a period's log with no period is the
    breakdown across all four - the compiler's grouped read by period
    (`compose.core._compile_by_period`), four reads of the SAME five games.
    Curry here: 12 in the first quarters (2.4 a game), 6 in each of the
    others (1.2) - 30 in regulation, 6.0 a game - and e4's overtime three is
    in none of them. e3 and e5, first quarters he played and did not score
    in, are zeros over games he played, so every quarter counts the same
    five games; a rate by quarter is each quarter's makes over its
    attempts."""
    result = period_log(period_ctx, Reading(who=Who(kind="player", players=("Stephen Curry",)), span=Span(season=SEASON, season_type=2)))
    assert result.data["games_played"] == 5
    assert [(q["quarter"], q["games"], q["total"], q["average"]) for q in result.data["quarters"]] == [(1, 5, 12, 2.4), (2, 5, 6, 1.2), (3, 5, 6, 1.2), (4, 5, 6, 1.2)]
    answer = result.text or ""
    assert answer.startswith(f"Stephen Curry, points per game by quarter in the {SEASON} regular season (5 games):")
    assert "per game        2.4      1.2      1.2      1.2         6.0" in answer and "total            12        6        6        6          30" in answer
    assert "Overtime is no quarter and is not counted." in answer and result.data["headline"] == answer.splitlines()[0].rstrip(":")
    threes = period_log(
        period_ctx,
        Reading(
            who=Who(kind="player", players=("Stephen Curry",)),
            cuts=Cuts(opponent="Lakers"),
            measure=Measure(key="threePointFieldGoalPct", as_typed="threePointFieldGoalPct"),
            span=Span(season=SEASON, season_type=2),
        ),
    )
    assert [(q["made"], q["attempted"], q["pct"]) for q in threes.data["quarters"]] == [(2, 5, 40.0), (1, 1, 100.0), (2, 2, 100.0), (0, 0, None)], (
        "e1 and e3: first quarters 2-5, then e3's Q2 make, both Q3 makes"
    )
    assert "against the Los Angeles Lakers (2 games)" in (threes.text or "") and "made-att        2-5      1-1      2-2      0-0         5-8" in (threes.text or "")


def test_a_period_answer_says_the_calendar_it_was_narrowed_to(period_ctx: AnswerContext) -> None:
    """A month narrows which games count, and the period sentence names it
    beside the venue, the half, the teammates, the line, the date and the
    game of a series (ISSUES.md #301): "jokic first quarter points in
    january" is his January games, headed as January's."""
    quarters = period_log(period_ctx, Reading(who=Who(kind="player", players=("Stephen Curry",)), cuts=Cuts(situation=situation_of("november")), span=Span(season=SEASON, season_type=2)))
    assert (quarters.text or "").startswith(f"Stephen Curry, points per game by quarter in the {SEASON} regular season in November (4 games):"), (
        "e1 tips at 00:30Z on November 1st, October 31st in Eastern time"
    )
    one = period_log(
        period_ctx, Reading(who=Who(kind="player", players=("Stephen Curry",)), cuts=Cuts(situation=situation_of("november")), span=Span(season=SEASON, season_type=2), period=Period(number=1))
    )
    assert f"over 4 games of the {SEASON} regular season in November, averaging" in (one.text or "")


def test_a_rebuilt_games_quarters_are_read_like_any_others(period_ctx: AnswerContext) -> None:
    """A game whose BOX line ESPN never served is rebuilt from the plays
    (Chicago and New Orleans, 2013-2018), and a rebuilt line cannot be
    trusted for turnovers, fouls or attempts (``UNGATED_ON_REBUILD``) - but
    a quarter's line is rebuilt from the plays for EVERY game, fetched or
    not, so a rebuilt game's quarters read like any other's - never a
    quarter's turnovers summed over the fetched games alone, nor, where every
    game was rebuilt (Anthony Davis, 2015), "Per-quarter turnovers cannot be
    answered here: ... this warehouse holds none", a refusal naming the
    wrong cause about a warehouse holding every one of his plays. Here e2 is
    rebuilt, and its first-quarter turnover counts."""
    c = period_ctx.con
    c.execute("CREATE TABLE plays (event_id VARCHAR, season BIGINT, season_type BIGINT, period BIGINT, athlete_id VARCHAR, participant_athlete_ids VARCHAR, type VARCHAR, text VARCHAR)")
    for event, period in (("e1", 1), ("e2", 1), ("e2", 3)):
        c.execute("INSERT INTO plays VALUES (?, ?, 2, ?, '1', '1', 'Bad Pass Turnover', 'Stephen Curry bad pass turnover')", [event, SEASON, period])
    rebuilt_e2 = "CASE WHEN event_id = 'e2' THEN NULL ELSE minutes END AS minutes"
    c.execute(f"CREATE VIEW player_box_stats_filled AS SELECT * REPLACE ({rebuilt_e2}), event_id = 'e2' AS reconstructed, 0 AS turnovers FROM player_box_stats")
    c.execute(
        "CREATE OR REPLACE VIEW player_game_log AS SELECT pbs.*, "
        "CASE WHEN g.home_team_id = pbs.team_id THEN g.away_team_id ELSE g.home_team_id END AS opponent_team_id, "
        "TRUE AS starter, g.date AS game_date, p.display_name AS player_name, o.abbreviation AS opponent_abbr "
        "FROM player_box_stats_filled pbs JOIN games g ON g.event_id = pbs.event_id AND g.season = pbs.season "
        "LEFT JOIN players p ON p.athlete_id = pbs.athlete_id "
        "LEFT JOIN teams o ON o.team_id = CASE WHEN g.home_team_id = pbs.team_id THEN g.away_team_id ELSE g.home_team_id END"
    )
    result = period_log(period_ctx, Reading(who=Who(kind="player", players=("Stephen Curry",)), measure=Measure(key="turnovers", as_typed="turnovers"), span=Span(season=SEASON, season_type=2)))
    assert [(q["quarter"], q["games"], q["total"]) for q in result.data["quarters"]] == [(1, 5, 2), (2, 5, 0), (3, 5, 1), (4, 5, 0)], "e1's and e2's first-quarter turnovers, e2's third"
    one = period_log(
        period_ctx,
        Reading(who=Who(kind="player", players=("Stephen Curry",)), measure=Measure(key="turnovers", as_typed="turnovers"), span=Span(season=SEASON, season_type=2), period=Period(number=1)),
    )
    assert one.data["total"] == 2 and one.data["games_played"] == 5, "a quarter of the rebuilt game counts as any other quarter does"


def test_a_period_logs_narrowings_and_spans_are_stated() -> None:
    """A period's log states the opponent, the venue, a companion, the window,
    the date, a career, a since-bounded span and both season types: each is
    a cell the read filters on, so none is unhonored - an unhonored one would
    refuse a question this answers perfectly well (decisions D13, D14: the
    accuracy caveat is said season by season across them, and a season that
    cannot be trusted is left out and said). The other direction matters
    too: a cell stated that the read ignores would silently answer a broader
    question, which the reader's own tests below hold it to.
    """
    # Through its shape's row (``compose.plan.cells_unhonored``).
    curry = Who(kind="player", players=("Stephen Curry",))
    for given in (
        Reading(who=curry, period=Period(number=1), cuts=Cuts(opponent="Boston Celtics")),
        Reading(who=curry, period=Period(number=1), cuts=Cuts(venue="home")),
        Reading(who=curry, period=Period(number=1), companions=(Companion(player="Klay Thompson", predicate="absent"),)),
        Reading(who=curry, period=Period(number=1), window=Window(order="recent")),
        Reading(who=curry, period=Period(number=1), cuts=Cuts(date="2026-01-02")),
        Reading(who=curry, period=Period(number=1), span=Span(career=True)),
        Reading(who=curry, period=Period(number=1), span=Span(since=2023)),
        Reading(who=curry, period=Period(number=1), span=Span(both=True)),
    ):
        assert [cell for cell, _ in cells_unhonored(given, PERIOD_LOG)] == []


def test_a_log_lists_the_games_and_keeps_the_season_in_the_header(period_ctx: AnswerContext) -> None:
    """ "rj barrett 4th qtr log" is a log: the rows list every played game,
    zeros included, beside the total and the average, and the header
    answers the season rather than the rows shown."""
    answer = (
        period_log(period_ctx, Reading(who=Who(kind="player", players=("Stephen Curry",)), measure=Measure(how="per_game"), span=Span(season=SEASON, season_type=2), period=Period(number=1))).text
        or ""
    )
    assert "over 5 games" in answer
    assert "1st quarter line, every game:" in answer, "no stat named: the period's whole line, as a game log lists a game's"
    assert len([line for line in answer.splitlines() if line.strip()[:4].isdigit()]) == 5
    points = (
        period_log(
            period_ctx,
            Reading(
                who=Who(kind="player", players=("Stephen Curry",)), measure=Measure(key="points", as_typed="points", how="per_game"), span=Span(season=SEASON, season_type=2), period=Period(number=1)
            ),
        ).text
        or ""
    )
    assert "1st quarter points, every game:" in points, "a stat named: that column alone"


def test_a_date_narrows_to_that_one_game(period_ctx: AnswerContext) -> None:
    """`date` reaches a period's log through `scoped_games`, the same as
    every other narrowing here. e1 is the only game on its own Eastern date
    (a team plays at most one game a day), so the answer is the one-game
    header rather than a season total - and, since no season is passed (a
    date replaces it, as it does on a player's log), the season named in
    that header comes from the game itself (LeBron James on 2025-11-18, 2nd
    quarter: "... scored 7 points in the 2nd quarter vs the Utah Jazz on
    2025-11-18 (2026 regular season)").
    """
    e1_date = _eastern_date_of(f"{SEASON - 1}-11-01T00:30Z")
    result = period_log(period_ctx, Reading(who=Who(kind="player", players=("Stephen Curry",)), cuts=Cuts(date=e1_date), period=Period(number=1)))
    assert result.data["total"] == 6, "e1's two made first-quarter threes, not the 12 points across e1/e2/e4"
    assert result.data["games_played"] == 1
    assert result.data["season"] == SEASON, "read off the game itself, not defaulted separately"
    assert f"vs the Los Angeles Lakers on {e1_date}" in (result.text or "")


def test_a_date_with_no_game_says_which_date_was_empty(period_ctx: AnswerContext) -> None:
    """The "no games found" refusal names the date, the same way every other
    narrowing here is said in the answer - a silent date would read as a
    season with nothing on record, which is a different (false) claim."""
    answer = period_log(period_ctx, Reading(who=Who(kind="player", players=("Stephen Curry",)), cuts=Cuts(date="2019-07-04"), period=Period(number=1))).text or ""
    assert answer == "No games found for Stephen Curry on 2019-07-04.", "no season the date is not in (it said 'No 2026 regular season games')"
    quarters = period_log(period_ctx, Reading(who=Who(kind="player", players=("Stephen Curry",)), cuts=Cuts(date="2019-07-04"))).text or ""
    assert quarters == "No games found for Stephen Curry on 2019-07-04."


def test_a_date_in_a_badly_reconciled_season_is_refused_for_that_season(period_ctx: AnswerContext) -> None:
    """The accuracy refusal is read off the game a date finds, not assumed
    from a season defaulted to "now" - so a date from 2004 (labeled
    `PERIOD_RECONCILIATION`) is caveated even with no season named at all."""
    answer = period_log(period_ctx, Reading(who=Who(kind="player", players=("Stephen Curry",)), cuts=Cuts(date="2003-11-04"), period=Period(number=1))).text or ""
    assert "96% of the time" in answer
    # The four quarters of that one game the same way (ISSUES.md #302): read
    # as the game's own season, read off its row, and caveated by that
    # season's agreement - never "the 2026 regular season on 2003-11-04".
    quarters = period_log(period_ctx, Reading(who=Who(kind="player", players=("Stephen Curry",)), cuts=Cuts(date="2003-11-04")))
    assert quarters.data["season"] == 2004 and (quarters.text or "").startswith("Stephen Curry, points per game by quarter in the 2004 regular season on 2003-11-04 (1 games):")
    assert "96% of the time" in (quarters.text or "")


def test_a_career_read_of_a_period_sums_every_season_over_the_relations_games(period_ctx: AnswerContext) -> None:
    """The shot CTEs join to the relation's own selected games, never a
    literal ``season = ?``/``season_type = ?`` pair, so a career - whose
    ``span.season`` is ``None`` - sums every season rather than binding
    ``season = NULL``, which matches nothing and would answer "No {current
    season} games found for Stephen Curry", a false-cause refusal about a
    player the fixture holds two seasons of games for.
    """
    # The compiler's statement over a career span, as the period reader reads
    # one (decision D13).
    scope = Reading(who=Who(kind="player", players=("Stephen Curry",)), span=Span(career=True), period=Period(number=1))
    subject = scoped_player(period_ctx.con, scope, "no player named", table="player_game_log", available=SHOT_AVAILABILITY, span=Span(career=True))
    assert not isinstance(subject, Unanswered)
    player, span = subject
    narrowed = scoped_games(period_ctx.con, player, span, scope, opponent=None, measures=[])
    assert not isinstance(narrowed, Unanswered)
    rows = rows_of(period_ctx.con, compile_over(period_ctx.con, Query(**scope.filters(), skeleton="rows", measures=[*PERIOD_COLUMNS, "opponent_name"], direction="asc"), player, span, narrowed))
    assert rows, "a career read finds the games on record, never a false 'no games' refusal"
    assert sum(row["points"] for row in rows) == 15, "e1(6) + e2(3) + e4(3) across SEASON, plus e04(3) in 2004"
    assert len(rows) == 6, "e1,e2,e3,e4,e5 (SEASON) and e04 (2004); e6 a DNP, e7 uncovered by the shot table"


def test_a_team_log_names_each_opponent_as_it_was_that_season(gl_con: AnswerContext) -> None:
    """ESPN files the Nets under one id in New Jersey and Brooklyn, and `teams`
    holds only today's name, so a 2005 Knicks log names the opponent by its
    name that season, never "vs Brooklyn Nets" eight years before the team
    moved."""
    c = gl_con.con
    c.execute("INSERT INTO teams VALUES ('17','BKN','Brooklyn Nets')")
    c.execute("INSERT INTO games VALUES ('n05',2005,2,'2005-01-10T00:30Z','18','17',100,90,'18',false,'New York')")
    c.execute("INSERT INTO team_box_stats VALUES ('n05',2005,2,'18','17','home')")
    real_games.build_table(c, {"games", "teams"})
    games = player_log(gl_con, Reading(who=Who(kind="team", teams=("Knicks",)), span=Span(season=2005))).data["games"]
    assert [g["opponent"] for g in games] == ["New Jersey Nets"]


@pytest.fixture
def period_rank_ctx(tmp_path: Path) -> AnswerContext:
    """Three players over six postseason games, built to exercise the three
    things a per-game period ranking gets wrong.

    Nobody's shots carry a usable label: every made shot is 26 feet from the
    rim at (25, 0) with ``points_attempted = 0``, ESPN's "unlabeled". Scored
    off the label each is worth nothing; scored off its position each is a
    three, which is what ``SHOT_VALUE_SQL`` reads.

    - **Ace** plays all six and scores one first-quarter three in five of
      them - 15 points over 6 games, 2.5 a game. The sixth is a first quarter
      he played and did not score in, which is a real zero and must stay in
      the denominator.
    - **Role** plays all six and scores one first-quarter three in two of
      them: 6 points, 1.0 a game.
    - **Cameo** plays twice and scores a three in each: 6 points at 3.0 a
      game, the best average here and below the postseason minimum of five
      games, so he must not rank at all.

    The postseason is used so the qualifier is
    :data:`association.query.metrics.PER_GAME_MIN_POSTSEASON_GAMES` (5) rather
    than the 20 a regular season needs, which keeps the fixture readable.
    """
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    c.execute("CREATE TABLE teams (team_id VARCHAR, abbreviation VARCHAR, display_name VARCHAR)")
    c.execute(
        "CREATE TABLE games (event_id VARCHAR, season BIGINT, season_type BIGINT, date VARCHAR, home_team_id VARCHAR, away_team_id VARCHAR, "
        "home_score BIGINT, away_score BIGINT, winner_team_id VARCHAR)"
    )
    c.execute("CREATE TABLE player_box_stats (event_id VARCHAR, season BIGINT, season_type BIGINT, team_id VARCHAR, athlete_id VARCHAR, did_not_play BOOLEAN, minutes BIGINT)")
    c.execute(
        "CREATE TABLE shot_chart (athlete_id VARCHAR, season INTEGER, season_type INTEGER, event_id VARCHAR, team_id VARCHAR, "
        "period INTEGER, clock VARCHAR, made BOOLEAN, shot_type VARCHAR, coordinate_x INTEGER, coordinate_y INTEGER, points_attempted INTEGER, description VARCHAR)"
    )
    c.execute("INSERT INTO players VALUES ('1','Ace Scorer'),('2','Role Player'),('3','Cameo Sub')")
    c.execute("INSERT INTO teams VALUES ('9','GS','Golden State Warriors'),('13','LAL','Los Angeles Lakers')")
    events = [f"p{n}" for n in range(1, 7)]
    for n, event in enumerate(events, start=1):
        c.execute("INSERT INTO games VALUES (?,?,3,?,'9','13',110,100,'9')", [event, SEASON, f"{SEASON}-05-0{n}T00:30Z"])
    for event in events:
        c.execute("INSERT INTO player_box_stats VALUES (?,?,3,'9','1',FALSE,30)", [event, SEASON])
        c.execute("INSERT INTO player_box_stats VALUES (?,?,3,'9','2',FALSE,24)", [event, SEASON])
    for event in events[:2]:
        c.execute("INSERT INTO player_box_stats VALUES (?,?,3,'9','3',FALSE,8)", [event, SEASON])

    def shot(athlete: str, event: str, period: int) -> None:
        c.execute(
            "INSERT INTO shot_chart VALUES (?,?,3,?,'9',?,'5:00',TRUE,'Jump Shot',25,26,0,'makes 26-foot jump shot')",
            [athlete, SEASON, event, period],
        )

    for event in events[:5]:  # Ace: a first-quarter three in five of six
        shot("1", event, 1)
    shot("1", events[5], 2)  # and a second-quarter one in the sixth, so he played a scoreless first quarter
    for event in events[:2]:  # Role: two
        shot("2", event, 1)
    for event in events[:2]:  # Cameo: two, in his only two games
        shot("3", event, 1)
    # The ranking reads the relation, which reads the warehouse's log view -
    # mirrored here as period_ctx mirrors it.
    c.execute(
        "CREATE VIEW player_game_log AS SELECT pbs.*, "
        "CASE WHEN g.home_team_id = pbs.team_id THEN g.away_team_id ELSE g.home_team_id END AS opponent_team_id, "
        "TRUE AS starter, g.date AS game_date, p.display_name AS player_name "
        "FROM player_box_stats pbs JOIN games g ON g.event_id = pbs.event_id AND g.season = pbs.season "
        "LEFT JOIN players p ON p.athlete_id = pbs.athlete_id"
    )
    return AnswerContext(con=c, out_dir=tmp_path)


def test_a_period_ranking_averages_over_games_played_not_games_scored_in(period_rank_ctx: AnswerContext) -> None:
    """The denominator a period's log keeps, in the ranking: counting only the
    games with a made shot in the period drops every scoreless quarter and
    lifts the average. Ace scored in five of six first quarters, so his
    average is 15/6 = 2.5, not 15/5 = 3.0."""
    result = period_ranking(period_rank_ctx, Reading(span=Span(season=SEASON, season_type=3), period=Period(number=1)))
    leaders = result.data["leaders"]
    assert [row["player"] for row in leaders] == ["Ace Scorer", "Role Player"]
    assert leaders[0] == {"player": "Ace Scorer", "games": 6, "points": 15, "average": 2.5}
    assert leaders[1]["average"] == 1.0
    assert "led the league in 1st quarter points per game" in (result.text or "")


def test_a_period_ranking_states_and_applies_its_games_qualifier(period_rank_ctx: AnswerContext) -> None:
    """Cameo averages 3.0, the best here, over two games. A per-game ranking
    without a minimum is whoever played once and scored, so he is out - and
    the answer says which minimum it used rather than leaving a reader to
    wonder why he is missing."""
    result = period_ranking(period_rank_ctx, Reading(span=Span(season=SEASON, season_type=3), period=Period(number=1)))
    assert "Cameo Sub" not in str(result.data["leaders"])
    assert result.data["minimum_games"] == 5
    assert "(minimum 5 games)" in (result.text or "")


def test_a_period_ranking_reads_the_shots_value_from_its_position(period_rank_ctx: AnswerContext) -> None:
    """Every shot here is unlabeled (`points_attempted = 0`) and 26 feet out.
    Read off the label these are worth nothing; read through SHOT_VALUE_SQL
    each is a three, which is the difference between 76.8% and 99.95%
    agreement with ESPN's own quarter scores."""
    result = period_ranking(period_rank_ctx, Reading(span=Span(season=SEASON, season_type=3), period=Period(number=1)))
    assert result.data["leaders"][0]["points"] == 15  # five threes, not five zeros


def test_a_period_ranking_narrowed_to_a_team_says_so(period_rank_ctx: AnswerContext) -> None:
    result = period_ranking(period_rank_ctx, Reading(who=Who(kind="team", teams=("Golden State Warriors",)), span=Span(season=SEASON, season_type=3), period=Period(number=1)))
    assert "led the Golden State Warriors in 1st quarter points per game" in (result.text or "")
    assert result.data["team"] == "Golden State Warriors"


def test_a_period_ranking_covers_a_half_as_well_as_a_quarter(period_rank_ctx: AnswerContext) -> None:
    """Ace's six threes are five in the first quarter and one in the second,
    so a first-half ranking sees all six: 18 points over 6 games."""
    result = period_ranking(period_rank_ctx, Reading(span=Span(season=SEASON, season_type=3), period=Period(number=1, half=True)))
    assert result.data["leaders"][0] == {"player": "Ace Scorer", "games": 6, "points": 18, "average": 3.0}
    assert "1st half points per game" in (result.text or "")


def test_a_period_ranking_refuses_a_stat_it_cannot_rank(period_rank_ctx: AnswerContext) -> None:
    """Minutes are not split by period in any source, so they are refused by
    name; rebounds are rebuilt from the plays, and refused only where the
    warehouse holds none (this fixture) - never ranked as zeros."""
    with pytest.raises(Unsupported, match="no per-period 'minutes'"):
        period_ranking(period_rank_ctx, Reading(measure=Measure(key="minutes", as_typed="minutes"), span=Span(season=SEASON, season_type=3), period=Period(number=1)))
    answer = period_ranking(period_rank_ctx, Reading(measure=Measure(key="rebounds", as_typed="rebounds"), span=Span(season=SEASON, season_type=3), period=Period(number=1))).text
    assert answer == "Per-quarter rebounds cannot be ranked here: they are rebuilt from play-by-play, and this warehouse holds none."


def test_points_by_quarter_is_every_quarter_side_by_side(period_rank_ctx: AnswerContext) -> None:
    """yardstick-v2 F048, "nba playerspoints by quarter average": no period
    named is the four quarters per player, ranked by the four together. Ace:
    five first-quarter threes and one second-quarter one over six games -
    2.5 and 0.5; Role: two first-quarter threes over six - 1.0. Cameo played
    two games and does not qualify."""
    result = period_ranking(period_rank_ctx, Reading(span=Span(season=SEASON, season_type=3)))
    leaders = result.data["leaders"]
    assert [row["player"] for row in leaders] == ["Ace Scorer", "Role Player"]
    assert leaders[0] == {"player": "Ace Scorer", "games": 6, "q1": 2.5, "q2": 0.5, "q3": 0.0, "q4": 0.0, "total": 3.0}
    assert "ranked by the four quarters together" in (result.text or "")
    assert "Overtime is no quarter" in (result.text or "")


def test_a_period_ranking_narrowed_to_an_opponent_or_venue_keeps_a_share_of_the_narrowed_games(period_rank_ctx: AnswerContext) -> None:
    """#185: a pool narrowed to an opponent or a
    venue cannot keep the season's minimum (nobody plays twenty games
    against one team), so the qualifier is half of the most anyone played
    in the pool, capped at the season's own minimum, and the answer says
    both. Two road games against Boston are added: Ace and Role play both,
    Ace scores a first-quarter three in one (and Role a second-quarter one
    in the other, so the shot table covers both). Against the Celtics the most
    anyone played is 2, so the minimum is 1 and both rank (Ace 1.5, Role
    0.0); against the Lakers it is half of 6, capped at the postseason's 5
    - 3 - which still keeps Cameo (2 games) out; on the road is the same
    two Boston games."""
    c = period_rank_ctx.con
    c.execute("INSERT INTO teams VALUES ('2','BOS','Boston Celtics')")
    for n, event in enumerate(("p7", "p8"), start=7):
        c.execute("INSERT INTO games VALUES (?,?,3,?,'2','9',100,110,'9')", [event, SEASON, f"{SEASON}-05-0{n}T00:30Z"])
        c.execute("INSERT INTO player_box_stats VALUES (?,?,3,'9','1',FALSE,30)", [event, SEASON])
        c.execute("INSERT INTO player_box_stats VALUES (?,?,3,'9','2',FALSE,24)", [event, SEASON])
    c.execute("INSERT INTO shot_chart VALUES ('1',?,3,'p7','9',1,'5:00',TRUE,'Jump Shot',25,26,0,'makes 26-foot jump shot')", [SEASON])
    # A second-quarter make in p8, so the shot table covers it (a game with no located shots is no game in any period).
    c.execute("INSERT INTO shot_chart VALUES ('2',?,3,'p8','9',2,'5:00',TRUE,'Jump Shot',25,26,0,'makes 26-foot jump shot')", [SEASON])
    boston = period_ranking(period_rank_ctx, Reading(cuts=Cuts(opponent="Celtics"), span=Span(season=SEASON, season_type=3), period=Period(number=1)))
    assert [(row["player"], row["games"], row["average"]) for row in boston.data["leaders"]] == [("Ace Scorer", 2, 1.5), ("Role Player", 2, 0.0)]
    assert boston.data["minimum_games"] == 1 and boston.data["narrowing"] == "vs the Boston Celtics"
    assert f"in the {SEASON} postseason vs the Boston Celtics (minimum 1 games, half of the 2 anyone played)" in (boston.text or "")
    lakers = period_ranking(period_rank_ctx, Reading(cuts=Cuts(opponent="Lakers"), span=Span(season=SEASON, season_type=3), period=Period(number=1)))
    assert lakers.data["minimum_games"] == 3 and "Cameo Sub" not in str(lakers.data["leaders"]) and "(minimum 3 games, half of the 6 anyone played)" in (lakers.text or "")
    road = period_ranking(period_rank_ctx, Reading(cuts=Cuts(venue="away"), span=Span(season=SEASON, season_type=3), period=Period(number=1)))
    assert road.data["narrowing"] == "on the road" and [row["games"] for row in road.data["leaders"]] == [2, 2]
    # The whole-season ranking keeps its own minimum, said without a share.
    whole = period_ranking(period_rank_ctx, Reading(span=Span(season=SEASON, season_type=3), period=Period(number=1)))
    assert whole.data["minimum_games"] == 5 and "(minimum 5 games)" in (whole.text or "") and whole.data["narrowing"] == ""
    # The by-quarter table reads the same share off its pool.
    table = period_ranking(period_rank_ctx, Reading(cuts=Cuts(opponent="Celtics"), span=Span(season=SEASON, season_type=3)))
    assert table.data["minimum_games"] == 1 and "vs the Boston Celtics (minimum 1 games, half of the 2 anyone played)" in (table.text or "")


def test_a_period_ranking_nobody_qualifies_for_says_so(period_rank_ctx: AnswerContext) -> None:
    """A regular season needs 20 games and this fixture has six postseason
    ones, so the honest answer names the qualifier rather than reading as
    though nobody scored."""
    result = period_ranking(period_rank_ctx, Reading(span=Span(season=SEASON, season_type=2), period=Period(number=1)))
    assert result.data["leaders"] == []
    assert "played the 20 games needed to rank" in (result.text or "")


def test_a_period_is_narrowed_by_a_teammates_absence(period_ctx: AnswerContext) -> None:
    """ "scottie barnes stats 2nd half log without rj": a period's log reads a
    teammate's absence through the relation like every other reader. Klay
    plays e1-e3 and sits e4-e5, so without him Curry has two games - and the
    header says so, because a half answered over the games a teammate missed
    and headed as though it covered every game is a silent narrowing."""
    result = period_log(
        period_ctx,
        Reading(
            who=Who(kind="player", players=("Stephen Curry",)), companions=(Companion(player="Klay Thompson", predicate="absent"),), span=Span(season=SEASON, season_type=2), period=Period(number=1)
        ),
    )
    assert result.data["games_played"] == 2
    assert "over 2 games" in (result.text or "") and "without Klay Thompson" in (result.text or "")
    # Every game, for contrast: five, since e6 is a game he did not play.
    whole = period_log(period_ctx, Reading(who=Who(kind="player", players=("Stephen Curry",)), span=Span(season=SEASON, season_type=2), period=Period(number=1)))
    assert whole.data["games_played"] == 5 and "without" not in (whole.text or "")


def test_a_period_is_narrowed_by_a_line_on_a_box_score_column(period_ctx: AnswerContext) -> None:
    """A line on a stat narrows which of the player's games the period sum
    covers, the same as every other reader on the relation - and since the
    relation carries the period, the line reads the PERIOD's figure, the one
    the answer reports: "his first quarters with at least 3 points" is the
    games where the first quarter held 3, never the whole game's points
    printed under the quarter's heading. Measured on the fixture's first
    quarters: e1 6, e2 3, e4 3, e3 and e5 0 - 12 over 5 games."""
    high = period_log(
        period_ctx,
        Reading(
            who=Who(kind="player", players=("Stephen Curry",)),
            lines=(Line(measure="points", value=3, as_typed="3 points", narrows=True),),
            span=Span(season=SEASON, season_type=2),
            period=Period(number=1),
        ),
    )
    assert high.data["games_played"] == 3
    assert high.data["total"] == 12
    assert high.data["measures"] == ["at least 3 points"]
    assert "with at least 3 points" in (high.text or "")
    low = period_log(
        period_ctx,
        Reading(
            who=Who(kind="player", players=("Stephen Curry",)),
            lines=(Line(measure="points", op="<", value=3, as_typed="3 points", narrows=True),),
            span=Span(season=SEASON, season_type=2),
            period=Period(number=1),
        ),
    )
    assert low.data["games_played"] == 2
    assert low.data["total"] == 0
    assert "with under 3 points" in (low.text or "")
    with pytest.raises(Unsupported):
        period_log(
            period_ctx,
            Reading(
                who=Who(kind="player", players=("Stephen Curry",)),
                lines=(Line(measure=None, value=24, as_typed="24 vibes", narrows=True),),
                span=Span(season=SEASON, season_type=2),
                period=Period(number=1),
            ),
        )


def test_a_period_log_takes_the_end_of_the_season_the_question_asked_for(period_ctx: AnswerContext) -> None:
    """`order` picks which end the rows come from, as it does for a player's
    log: "his first 5 games" are his first five, never his last five."""
    recent = period_log(
        period_ctx,
        Reading(
            who=Who(kind="player", players=("Stephen Curry",)),
            measure=Measure(how="per_game"),
            span=Span(season=SEASON, season_type=2),
            period=Period(number=1),
            window=Window(order="recent", count=2),
        ),
    )
    first = period_log(
        period_ctx,
        Reading(
            who=Who(kind="player", players=("Stephen Curry",)), measure=Measure(how="per_game"), span=Span(season=SEASON, season_type=2), period=Period(number=1), window=Window(order="first", count=2)
        ),
    )
    recent_dates = [line.split()[0] for line in (recent.text or "").splitlines() if line.strip()[:4].isdigit()]
    first_dates = [line.split()[0] for line in (first.text or "").splitlines() if line.strip()[:4].isdigit()]
    assert len(recent_dates) == 2 and len(first_dates) == 2
    assert first_dates == sorted(first_dates) and recent_dates == sorted(recent_dates, reverse=True)
    assert first_dates[0] < recent_dates[-1]
    # A count of games the words asked is the games the figure is over, said
    # as that count from its end - never the whole season's 5 beneath a log of
    # 2 (#473: "cade cunningham 1st half stats in his last 4 games" answered
    # all 64 of his games).
    assert (first.text or "").startswith(f"Stephen Curry scored 9 points in the 1st quarter over the first 2 games of the {SEASON} regular season, averaging 4.5.")
    assert (recent.text or "").startswith("Stephen Curry scored 3 points in the 1st quarter over the last 2 games of the")
    assert "line, every game:" in (first.text or "") and "line, every game:" in (recent.text or "")
    assert first.data["games_played"] == 2 and recent.data["games_played"] == 2


def test_a_period_without_a_teammate_nothing_resolves_refuses_rather_than_dropping_him(period_ctx: AnswerContext) -> None:
    """A name the roster does not hold is refused by name - the read's own
    ``name_unmatched`` refusal, as every reader on the relation says it -
    never dropped with the period totaled over every game, which would answer
    a wider question than was asked with nothing saying so."""
    result = period_log(
        period_ctx,
        Reading(
            who=Who(kind="player", players=("Stephen Curry",)), companions=(Companion(player="Nobody At All", predicate="absent"),), span=Span(season=SEASON, season_type=2), period=Period(number=1)
        ),
    )
    assert (
        result.text
        == "No player found matching 'Nobody At All'. ESPN's box scores, which the warehouse's players come from, start with 1993-94, so a player whose career ended before then may not be on record."
    )


def test_a_teams_half_is_the_two_quarters_of_its_own_linescore(tq_con: AnswerContext) -> None:
    """A team's half is the two quarters of its linescore - "first half" is
    never period 1, for a team the same way as for a player. The linescore
    holds both quarters, so it is addition, not a second source. The Knicks'
    second halves here are 28+29, 28+28 and 36+30: 179 across three games."""
    result = team_period_total(tq_con, Reading(who=Who(kind="team", teams=("Knicks",)), span=Span(season=current_season(), season_type=2), period=Period(number=2, half=True)))
    assert result.data["total"] == 179
    assert [g["points"] for g in result.data["games"]] == [57, 56, 66]
    assert "2nd half" in (result.text or "")


def test_a_teams_most_in_a_half_is_one_game_not_the_average(tq_con: AnswerContext) -> None:
    """ "Detroit Pistons most points in a first half this season" asks for ONE
    game. The Knicks' first halves are 30+25, 20+20 and 10+32, so the most is
    55 against Boston and the fewest 40, and the answer names the game rather
    than a season average nobody asked for."""
    most = team_period_total(
        tq_con, Reading(who=Who(kind="team", teams=("Knicks",)), span=Span(season=current_season(), season_type=2), period=Period(number=1, half=True), window=Window(rank="most"))
    )
    assert most.data["extreme"] == 55
    assert "scored 55 in the 1st half vs the Boston Celtics on 2026-04-10" in (most.text or "")
    assert "their most" in (most.text or "")
    fewest = team_period_total(
        tq_con, Reading(who=Who(kind="team", teams=("Knicks",)), span=Span(season=current_season(), season_type=2), period=Period(number=1, half=True), window=Window(rank="fewest"))
    )
    assert fewest.data["extreme"] == 40 and "their fewest" in (fewest.text or "")
    # Without a rank it is the season's scoring.
    plain = team_period_total(tq_con, Reading(who=Who(kind="team", teams=("Knicks",)), span=Span(season=current_season(), season_type=2), period=Period(number=1, half=True)))
    assert "extreme" not in plain.data and plain.data["total"] == 137


def test_a_teams_quarter_is_one_period_of_its_linescore(tq_con: AnswerContext) -> None:
    """A team's quarter is one period of the linescore, read by number rather
    than summed."""
    result = team_period_total(tq_con, Reading(who=Who(kind="team", teams=("Knicks",)), span=Span(season=current_season(), season_type=2), period=Period(number=1)))
    assert [g["points"] for g in result.data["games"]] == [30, 20, 10]
    assert result.data["period"] == 1


def test_a_tied_extreme_names_every_game_that_reached_it() -> None:
    """Two games at the same high are both the answer. Resolving the tie by
    whichever row sorted first would report one game as though it stood
    alone."""
    from association.query.compose.say import say_team_quarter_points
    from association.query.result import Narrowing, Part, Period, Result, Rows, Scalar, Span, TeamPeriodFacts

    games = (
        {"date": "2026-01-02", "opponent": "Boston Celtics", "points": 60},
        {"date": "2026-01-09", "opponent": "Chicago Bulls", "points": 60},
        {"date": "2026-01-16", "opponent": "Miami Heat", "points": 41},
    )
    line = Scalar(games=3, values={"points": 53.67, "most": 60, "fewest": 41}, sums={"points": 161})
    read = Result(
        subject="New York Knicks",
        relation="team",
        span=Span(season=2026, season_type=2, phrase="2026 regular season"),
        narrowing=Narrowing(cells=(Period(label="1st half", periods=(1, 2)),)),
        parts=(Part(body=line), Part(role="detail", body=Rows(rows=games))),
        facts=TeamPeriodFacts(measure="points", rank="most", dateless=""),
    )
    result = said(say_team_quarter_points(read))
    assert result.data["extreme"] == 60 and len(result.data["extreme_games"]) == 2
    assert "vs the Boston Celtics on 2026-01-02 and vs the Chicago Bulls on 2026-01-09" in (result.text or "")


# ---------------- the cell tables: no reader narrows the relation itself ----------------


def test_readers_on_the_relation_declare_no_scoping_of_their_own() -> None:
    """The readers settle their player or team and narrow the games through
    the shared steps, and what each honors is declared ONCE: its relation's
    table (RELATION_SCOPING, TEAM_RELATION_SCOPING) less its shape's row of
    exclusions, each with a written reason, and what the row takes beyond the
    table. A reader that declared its own list would be a second declaration
    of the same thing - so every shape a reader reads has exactly one row, in
    one of the two tables; every cell a row says its words do not state is a
    cell its relation carries or the row takes, with a reason; what it
    refuses outright is among those; and no reader takes a list of its own
    (``stated=``). The span's cells (``career``, ``range``, ``both``) are the
    relation's own, stated through the table like every other cell.
    """
    import inspect

    from association.query import compose
    from association.query.player_relation import RELATION_SCOPING, RELATION_SCOPING_EXCLUDED
    from association.query.team_relation import TEAM_RELATION_SCOPING, TEAM_RELATION_SCOPING_EXCLUDED

    assert not set(RELATION_SCOPING_EXCLUDED) & set(TEAM_RELATION_SCOPING_EXCLUDED)
    assert set(RELATION_SCOPING_EXCLUDED) | set(TEAM_RELATION_SCOPING_EXCLUDED) == set(compose._ROUTES)
    for table, rows in ((RELATION_SCOPING, RELATION_SCOPING_EXCLUDED), (TEAM_RELATION_SCOPING, TEAM_RELATION_SCOPING_EXCLUDED)):
        assert table <= Filtered.CELLS
        for shape, row in rows.items():
            for cell, reason in row.unstated.items():
                assert cell in table | row.taken and isinstance(reason, DeclineReason), f"{shape} excludes {cell!r} without a reason, or a cell its relation does not carry"
            assert row.refused <= set(row.unstated) and not row.taken & table and row.taken <= Filtered.CELLS
            assert cells_stated(shape) == (table | row.taken) - set(row.unstated)
    # The player relation's own readers take nothing beyond its table: what
    # their words state is the relation's set less their exclusions.
    for shape in (PLAYER_LOG, PointShape("player_games", "scalar", "line"), PERIOD_LOG, PLAYER_SPLITS, LINE_RECORD, LINE_RUNS, SHOT_CHART, SHOT_DISTANCE):
        assert not RELATION_SCOPING_EXCLUDED[shape].taken, shape
    for shape, route in compose._ROUTES.items():
        assert "stated" not in inspect.signature(route.reader).parameters, f"{shape}'s reader takes a list of its own"


def test_readers_on_the_relation_do_not_narrow_it_themselves() -> None:
    """A reader on the relation reads its cells only to label the answer or
    to decide what a bare threshold means. The NARROWING - the clause on the
    relation's rows - is the shared step's, so a cell the relation learns
    reaches every reader at once. A direct call to the narrowing function, or
    a hand-written clause on an opponent, venue, starter or date column (the
    player relation's, or the TEAM relation's ``tg.opponent_id``,
    ``tg.side``, ``tg.eastern_date``), is a reader teaching itself one cell.

    A shot read is held apart, in a separate list and a separate loop over
    the shot relation's two readers alone: it may not narrow itself to one
    game (``event_id = ?``) or to one player-season on the shot table
    (``athlete_id = ? AND season = ?``). Folded into the ``forbidden`` tuple,
    ``"athlete_id = ? AND season = ?"`` would false-positive on an unrelated
    read of the SEASON-LINE table that shares the same three-column WHERE
    shape by coincidence.
    """

    forbidden = (
        "_narrow_player_games(",
        "pgl.opponent_team_id = ?",
        "g.home_team_id = pgl.team_id) = ?",
        "pgl.starter = ?",
        "g.date >= ? AND g.date < ?",
        "tg.opponent_id = ?",
        "tg.side = ?",
        "tg.eastern_date = ?",
    )
    from association.query.team_relation import league_team_narrowed

    readers: dict[str, list[Callable[..., Any]]] = {}
    # The readers of the relation's shapes, and the private steps they call,
    # walked by the key each answers.
    readers["line_record"] = [read_record_when, read_team_record_when, _record_when_team_result, compile_team_line, compile_team_count]
    readers["period_log"] = [read_period_split, _period_log, _period_redirect, _period_by_quarter]
    readers["player_splits"] = [read_player_splits, read_team_splits, _player_splits, _team_splits]
    readers["player_log"] = [read_team_log, read_player_log, _player_log, _player_log_mixed, _team_log, _team_log_mixed]
    readers["player_line"] = [read_player_stat, _player_stat_result, _player_stat_meetings, read_player_line, _player_line_season, _player_line_career, _player_line_advanced]
    # The `run` shape: the compiler's skeleton and the team compiler's, and
    # the readers that say them.
    readers["line_runs"] = [
        _compile_run,
        compile_team_run,
        compile_team_range,
        _compile_team_run,
        read_streak,
        _streak_player_result,
        _streak_league_result,
        read_team_streak,
        _streak_team_result,
        _streak_league_teams_result,
        league_team_narrowed,
    ]
    # The `pair` shape.
    readers["player_meetings"] = [_resolve_pair, _compile_pair, read_player_matchup, _pair_result, _pair_no_meetings, _pair_absence]
    # A team with and without named teammates (the team relation's `presence`
    # group) is not walked: its games are read by the relation module's own
    # cell (`conditions.presence_games_sql`, which narrows the team relation
    # to the windows' teams and the opponent), and this walk cannot tell the
    # relation writing a clause from a reader writing one.
    for shape, functions in readers.items():
        for function in functions:
            # The reader and the private steps it calls, transitively - a
            # log's player narrowing and its team narrowing live in private
            # steps, not in the reader itself.
            source = _source_with_private_steps(function)
            for token in forbidden:
                assert token not in source, f"{shape} narrows the relation itself ({token!r}); use scoped_games / team_games"
        # The quieter way to narrow by hand: hand the shared step a dict BUILT
        # here instead of the question's cells - `{"venue": venue, "without":
        # without, "split": split}` would keep `game_n` from the relation while
        # its table said it was honored, and the token check above would see
        # nothing, because no clause was written. The reader passes the
        # Reading's cells whole.
        assert not re.search(r'(scoped_games|condition_player)\([^\n]*\{"', source), f"{shape} hands the shared step a hand-built dict; pass the question's cells"

    # The shot relation's readers (compose.shots) are walked.
    shot_forbidden = ("event_id = ?", "athlete_id = ? AND season = ?")
    for shape, handler in (("shot_chart", read_shot_chart), ("shot_distance", read_shot_distance)):
        source = _source_with_private_steps(handler)
        for token in shot_forbidden:
            assert token not in source, f"{shape} narrows the relation itself ({token!r}); use scoped_games / team_games"

    # The compiler (association.query.compose.core), the team compiler
    # (compose/team.py) and the point reader read the same shared steps
    # (scoped_games, league_games, scoped_team, team_games), and the same
    # discipline applies: they narrow the relation only through them, never by
    # writing their own clause on an opponent, venue, starter or date column.
    # They are walked by module source directly rather than through
    # _source_with_private_steps.
    import inspect

    import association.query.compose.core as _compose_core
    import association.query.compose.team as _compose_team
    import association.query.point as _point

    for module in (_compose_core, _point, _compose_team):
        source = inspect.getsource(module)
        for token in forbidden:
            assert token not in source, f"{module.__name__} narrows the relation itself ({token!r}); use scoped_games / league_games / scoped_team / team_games"
        assert not re.search(r'(scoped_games|condition_player|league_games|scoped_team|team_games)\([^\n]*\{"', source), (
            f"{module.__name__} hands the shared step a hand-built dict; pass the question's cells"
        )


def _source_with_private_steps(handler: Any) -> str:
    """A reader's source plus every private function of its module it
    reaches, transitively. The walker stops at every shared step
    (``team_games``, ``scoped_team``, ``scoped_games`` ...): the regex below
    only follows a name that STARTS WITH an underscore, and none of the
    shared narrowing steps do, by design (see their own module docstrings).
    """
    import inspect
    import re as _re

    module = inspect.getmodule(handler)
    assert module is not None
    seen, todo, out = set(), [handler], []
    while todo:
        fn = todo.pop()
        if fn in seen or not inspect.isfunction(fn):
            continue
        seen.add(fn)
        src = inspect.getsource(fn)
        out.append(src)
        for name in set(_re.findall(r"\b(_[a-z][a-z0-9_]*)\(", src)):
            step = getattr(module, name, None)
            if step is not None:
                todo.append(step)
    return "\n".join(out)


def test_a_leaderboard_ranked_by_another_measure_refuses_to_the_compiler() -> None:
    """yardstick-v2 F124: "highest scoring triple doubles" orders a ranking of
    yes/no games by another measure (``Window.by``, the ``ranked_by`` cell);
    a ranking of players' season lines does not state it, so its reader
    steps aside and the compiler's boolean-game ranking answers. The bare
    count - the same reading without it - states every cell."""
    assert [cell for cell, _ in cells_unhonored(Reading(measure=Measure(key="triple_double", as_typed="triple_double"), window=Window(count=10, by="points")), PLAYER_RANKING)] == ["ranked_by"]
    assert [cell for cell, _ in cells_unhonored(Reading(measure=Measure(key="triple_double", as_typed="triple_double"), window=Window(count=10)), PLAYER_RANKING)] == []
