"""Tests for the deterministic threshold_count template - including the exact
question that failed three times through the tool-calling agent."""

import re
from pathlib import Path
from typing import Any

import duckdb
import pytest

from association.fetch.repairs import real_games
from association.nba.season import current_season
from association.nba.season import eastern_date as _eastern_date_of
from association.query import shotchart
from association.query.entities import MAX_CANDIDATES, Availability, Entity, collect_name_readings, resolve_player
from association.query.metrics import LEADERBOARD_METRICS, PER_GAME_MIN_GAMES, PER_GAME_MIN_POSTSEASON_GAMES
from association.query.templates.common import HONORED_SCOPING, SCOPING_SLOTS, TemplateContext, TemplateResult, TemplateUnsupported, check_scope
from association.query.templates.games import _rebuilt_readable, game_log, head_to_head, period_leaderboard, period_split, player_matchup, team_quarter_points
from association.query.templates.netpoints import fingerprint, player_netpoints
from association.query.templates.players import SHOOTING_STATS, _box_score_stat_rebuilt, leaderboard, player_compare, player_history, player_stat, single_game_high, threshold_count
from association.query.templates.shots import shot_chart, shot_distance
from association.query.templates.teams import team_record


@pytest.fixture
def con(tmp_path: Path) -> TemplateContext:
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
    # The relation every box-score template reads through (query/player_games.py)
    # joins `games` on (event_id, season) and reads `player_name` off the log, as
    # the real view does. A view mirroring the warehouse's shape - an id per row,
    # a game per id - keeps a fixture that only wrote the stored table honest.
    c.execute(
        "CREATE VIEW player_game_log AS SELECT pbs.* REPLACE (COALESCE(pbs.event_id, 'e' || pbs.rowid) AS event_id), p.display_name AS player_name, "
        "NULL::VARCHAR AS game_date, NULL::VARCHAR AS opponent_abbr FROM player_box_stats pbs LEFT JOIN players p ON p.athlete_id = pbs.athlete_id"
    )
    c.execute("CREATE VIEW games AS SELECT DISTINCT event_id, season, season_type FROM player_game_log")
    return TemplateContext(con=c, out_dir=tmp_path)


def test_answers_the_question_the_agent_kept_getting_wrong(con: TemplateContext) -> None:
    result = threshold_count(con, {"stat": "points", "threshold": 30})
    assert result.data["leaders"][0] == {"player": "Bench Guy", "games": 9}
    assert {"player": "Luka Doncic", "games": 4} in result.data["leaders"]


def test_a_line_below_a_number_counts_the_games_under_it_on_the_stat_the_words_name(con: TemplateContext) -> None:
    """ "Sga games with under 14 fta" arrived as threshold 14 on freeThrowsMade -
    the model's nearest stat - and was answered as 14 or MORE free throws made.
    The phrase carries the count's own number, so it IS the count, misread:
    its direction and its column win. Luka has 4 games of 35 and 3 of 12."""
    result = threshold_count(con, {"stat": "rebounds", "threshold": 20, "player": "Luka Doncic", "below": ["under 20 points"]})
    assert result.data["leaders"] == [{"player": "Luka Doncic", "games": 3}]
    assert "games with under 20 points" in (result.answer or "") and "20+" not in (result.answer or "")
    # A phrase with ANOTHER number is a second line beside the count.
    both = threshold_count(con, {"stat": "points", "threshold": 30, "player": "Luka Doncic", "below": ["under 10 rebounds"]})
    assert both.data["leaders"] == [{"player": "Luka Doncic", "games": 4}]
    assert "30+ points and under 10 rebounds" in (both.answer or "")


def test_a_line_whose_words_name_no_stat_refuses_rather_than_filtering_on_a_guess(con: TemplateContext) -> None:
    with pytest.raises(TemplateUnsupported, match="names no box-score stat"):
        threshold_count(con, {"stat": "points", "threshold": 30, "below": ["under 30 gizmos"]})
    with pytest.raises(TemplateUnsupported, match="names no box-score stat"):
        player_stat(con, {"player": "Luka Doncic", "stat": "points", "below": ["under 30 gizmos"]})


def test_stats_against_one_opponent_end_with_the_meetings_behind_the_average(pg_ctx: TemplateContext) -> None:
    """Product decision, 2026-09-19: "stats vs X" is the averages, the count,
    and a short footer of the meetings themselves, newest first - what makes
    "in 1 game" honest. Podziemski met Detroit twice this season: at Detroit
    in December (20, a win) and at home in January (15, a loss)."""
    result = player_stat(pg_ctx, {"player": "Brandin Podziemski", "stat": "points", "opponent": "Detroit Pistons"})
    assert result.data["stats"]["gamesPlayed"] == 2 and result.data["stats"]["avgPoints"] == 17.5
    assert [(g["date"][5:], g["home_away"], g["result"], g["points"]) for g in result.data["recent"]] == [("01-10", "home", "L", 15), ("12-01", "away", "W", 20)]
    answer = result.answer or ""
    assert "averaged 17.5 points per game in 2 games vs the Detroit Pistons" in answer
    assert answer.endswith("All 2 meetings:\n  " + f"{current_season()}-01-10  vs DET  L  15 PTS, 4 REB, 6 AST\n  {current_season() - 1}-12-01  @ DET  W  20 PTS, 7 REB, 5 AST")
    # One meeting is "the only meeting", which is what makes "in 1 game" honest.
    one = player_stat(pg_ctx, {"player": "Brandin Podziemski", "stat": "points", "opponent": "Boston Celtics"})
    assert "in 1 game vs the Boston Celtics" in (one.answer or "") and "The only meeting:" in (one.answer or "")
    # No opponent, no footer: a venue alone is not a "vs X" question.
    home = player_stat(pg_ctx, {"player": "Brandin Podziemski", "stat": "points", "venue": "home"})
    assert "recent" not in home.data and "meetings" not in (home.answer or "")


def test_a_season_named_by_its_place_in_a_career_settles_to_that_year_once_the_player_is_known(pg_ctx: TemplateContext) -> None:
    """ "how many 40+ points games does lebron james have in his 18th season?"
    arrived as season 2018 - the ordinal read as a year. Podziemski's two
    seasons on record are last season and this one, so his 1st is last
    season, his 2nd is this one, and he has no 3rd."""
    s = current_season()
    first = player_stat(pg_ctx, {"player": "Brandin Podziemski", "stat": "points", "season_n": 1})
    assert first.data["season"] == s - 1 and first.data["stats"]["avgPoints"] == 8.0
    assert "in his 1st season (" in (first.answer or "")
    second = threshold_count(pg_ctx, {"stat": "points", "threshold": 20, "player": "Brandin Podziemski", "season_n": 2})
    assert second.data["season"] == s and second.data["leaders"] == [{"player": "Brandin Podziemski", "games": 1}]
    assert "had 1 game with 20+ points in his 2nd season (" in (second.answer or "")
    log = game_log(pg_ctx, {"player": "Brandin Podziemski", "season_n": 1})
    assert [g["season"] for g in log.data["games"]] == [s - 1]
    none = game_log(pg_ctx, {"player": "Brandin Podziemski", "season_n": 3})
    assert "has 2 seasons on record" in (none.answer or "") and "no 3rd season" in (none.answer or "")
    with pytest.raises(TemplateUnsupported, match="no player was named"):
        threshold_count(pg_ctx, {"stat": "points", "threshold": 40, "season_n": 15})


def test_one_game_of_each_playoff_series_is_numbered_by_date_over_the_series_own_games(pg_ctx: TemplateContext) -> None:
    """ "Ayton stats in game 4 playoff games": the nth game by date between two
    teams in one postseason. Podziemski's series vs Detroit was inserted with
    game 2 first, so a count by insertion order would name the wrong game."""
    third = game_log(pg_ctx, {"player": "Brandin Podziemski", "season_type": 3, "game_n": 3})
    assert [(g["date"][5:], g["points"]) for g in third.data["games"]] == [("04-24", 30)]
    assert "in game 3 of each series" in (third.answer or "")
    second = player_stat(pg_ctx, {"player": "Brandin Podziemski", "stat": "points", "season_type": 3, "game_n": 2})
    assert second.data["stats"]["gamesPlayed"] == 2 and second.data["stats"]["avgPoints"] == 17.5 and second.data["series_game"] == 2
    one_series = game_log(pg_ctx, {"player": "Brandin Podziemski", "season_type": 3, "game_n": 2, "opponent": "Detroit Pistons"})
    assert [g["points"] for g in one_series.data["games"]] == [20] and "in game 2 of the series" in (one_series.answer or "")
    with pytest.raises(TemplateUnsupported, match="playoff series"):
        game_log(pg_ctx, {"player": "Brandin Podziemski", "game_n": 3})
    with pytest.raises(TemplateUnsupported, match="team's log"):
        game_log(pg_ctx, {"team": "Golden State Warriors", "season_type": 3, "game_n": 3})


def test_a_game_log_keeps_only_the_games_under_a_line_on_the_stat_the_words_name(pg_ctx: TemplateContext) -> None:
    """ "mikal bridges game log with less than 15 fga" used to refuse (the
    model's `threshold` beside it) or list every game. Podziemski's three
    played games this season have 3, 4 and 2 free throw attempts."""
    result = game_log(pg_ctx, {"player": "Brandin Podziemski", "below": ["under 4 fta"]})
    assert [g["date"][5:] for g in result.data["games"]] == ["01-10", "11-01"]
    assert "with under 4 free throw attempts" in (result.answer or "")
    two = game_log(pg_ctx, {"player": "Brandin Podziemski", "below": ["under 4 fta", "less than 12 points"]})
    assert [g["date"][5:] for g in two.data["games"]] == ["11-01"]
    assert "under 4 free throw attempts and under 12 points" in (two.answer or "")
    with pytest.raises(TemplateUnsupported, match="PLAYER's games"):
        game_log(pg_ctx, {"team": "Golden State Warriors", "above": ["with 20 minutes"]})


def test_player_stat_averages_over_exactly_the_games_under_a_line(con: TemplateContext) -> None:
    """A line on a box-score column narrows the games the way an opponent
    does, so the average is read from box scores and says what it kept."""
    result = player_stat(con, {"player": "Luka Doncic", "stat": "points", "below": ["under 20 points"]})
    assert result.data["stats"]["gamesPlayed"] == 3 and result.data["stats"]["avgPoints"] == 12
    assert result.data["measures"] == ["under 20 points"] and "with under 20 points" in (result.answer or "")
    floor = player_stat(con, {"player": "Luka Doncic", "stat": "points", "above": ["with 30 minutes"]})
    assert floor.data["stats"]["gamesPlayed"] == 7  # every fixture game is 30 minutes


def test_defaults_to_the_current_season(con: TemplateContext) -> None:
    # The old path lost this rule to prompt truncation and answered for 2024.
    result = threshold_count(con, {"stat": "points", "threshold": 30})
    assert result.data["season"] == current_season()


def test_explicit_season_is_honored(con: TemplateContext) -> None:
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


@pytest.fixture
def currys(tmp_path: Path) -> TemplateContext:
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
    c.execute("CREATE VIEW games AS SELECT DISTINCT event_id, season, season_type FROM player_game_log")
    return TemplateContext(con=c, out_dir=tmp_path)


@pytest.mark.parametrize("back", [0, 2, 3])
def test_a_name_two_players_could_answer_to_is_asked_about(currys: TemplateContext, back: int) -> None:
    """Asked, not counted for whichever Curry leads. Two seasons back only Seth
    has a qualifying game, but both played: narrowing on the answer rather than
    on who played would be the prominence tiebreak by another route. Three back
    neither played, and the question is asked about both, as it always was."""
    result = threshold_count(currys, {"stat": "points", "threshold": 30, "player": "Curry", "season": current_season() - back})
    assert result.data == {"ambiguous": "Curry", "candidates": ["Seth Curry", "Stephen Curry"]}


def test_a_name_only_one_player_with_games_that_season_answers_to_is_answered(currys: TemplateContext) -> None:
    season = current_season() - 1
    result = threshold_count(currys, {"stat": "points", "threshold": 30, "player": "Curry", "season": season})
    assert result.answer == f"Stephen Curry had 2 games with 30+ points in the {season} regular season."


def test_a_name_at_the_candidate_cap_is_asked_about_rather_than_narrowed(tmp_path: Path) -> None:
    """find_players stops at MAX_CANDIDATES, alphabetically, so narrowing that
    list can leave one survivor while a player past the cut played too - the
    first Jones here, where the last one also has games. Measured on the real
    warehouse, "Williams" in 2023 answered Alondes Williams."""
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    c.executemany("INSERT INTO players VALUES (?,?)", [(str(i), f"{chr(ord('A') + i)}ay Jones") for i in range(MAX_CANDIDATES + 1)])
    c.execute(
        "CREATE TABLE player_box_stats (athlete_id VARCHAR, season INTEGER, season_type INTEGER, points INTEGER, event_id VARCHAR, minutes INTEGER DEFAULT 30, did_not_play BOOLEAN DEFAULT FALSE)"
    )
    played = [("0", current_season(), 2, 30), (str(MAX_CANDIDATES), current_season(), 2, 30)]  # the first Jones, and the one the cap cuts off
    c.executemany("INSERT INTO player_box_stats (athlete_id, season, season_type, points) VALUES (?,?,?,?)", played)
    result = threshold_count(TemplateContext(con=c, out_dir=tmp_path), {"stat": "points", "threshold": 30, "player": "Jones"})
    assert result.data.get("ambiguous") == "Jones", result.answer


def test_answer_reports_a_tie_as_a_tie(con: TemplateContext) -> None:
    con.con.execute("INSERT INTO player_box_stats (athlete_id, season, season_type, points, rebounds) SELECT '1', season, 2, 35, 5 FROM player_box_stats LIMIT 5")
    result = threshold_count(con, {"stat": "points", "threshold": 30})
    assert "tied for the most" in (result.answer or "")


@pytest.fixture
def rebuilt_counts(tmp_path: Path) -> TemplateContext:
    """The warehouse's real shape for an empty-box season: EVERY game keeps its
    `player_box_stats` row - ESPN serves the line, just zeroed - while the log
    carries the rebuilt figures with `reconstructed` marking exactly those.
    A fixture that left the stored rows out would resolve no player at all,
    since threshold_count narrows a name by who has a box score."""
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
    c.execute("CREATE VIEW games AS SELECT DISTINCT event_id, season, season_type FROM player_game_log")
    return TemplateContext(con=c, out_dir=tmp_path)


def test_a_nameless_athlete_is_never_counted_as_a_leader(rebuilt_counts: TemplateContext) -> None:
    """The log LEFT JOINs `players`, so an athlete missing from it reads back
    with a NULL name. The stored table's INNER JOIN drops him; the log branch
    must too, or the league board's top line is a blank name with 2 games."""
    result = threshold_count(rebuilt_counts, {"stat": "points", "threshold": 20})
    assert [leader["player"] for leader in result.data["leaders"]] == ["Anthony Davis"]
    assert None not in [leader["player"] for leader in result.data["leaders"]]


def test_a_threshold_count_counts_rebuilt_games(rebuilt_counts: TemplateContext) -> None:
    """The P1 remainder this closes. Reading `player_box_stats`, the two empty
    games are zeros and the count is 1; reading the log, the rebuilt 25 and 31
    both clear 20 and the count is 3. This is the whole behavior change."""
    result = threshold_count(rebuilt_counts, {"stat": "points", "threshold": 20, "player": "Anthony Davis"})
    assert result.data["leaders"] == [{"player": "Anthony Davis", "games": 3}]
    assert result.data["rebuilt_games"] == 2


def test_a_rebuilt_count_says_how_many_it_rebuilt(rebuilt_counts: TemplateContext) -> None:
    """A count resting on figures ESPN never served has to say so, or it reads
    as a count of box scores. The disclosure the opt-in is conditional on."""
    answer = threshold_count(rebuilt_counts, {"stat": "points", "threshold": 20, "player": "Anthony Davis"}).answer or ""
    assert "2 of those 3 games have no box score from ESPN" in answer
    assert "rebuilt from play-by-play" in answer


def test_a_count_of_only_fetched_games_says_nothing_about_rebuilding(rebuilt_counts: TemplateContext) -> None:
    """The note is about the number given, not about what was read. Drop the
    rebuilt games under the threshold and the count is an ordinary one, with
    nothing to disclose - even though the rebuilt lines were still read."""
    rebuilt_counts.con.execute("UPDATE player_game_log SET points = 5 WHERE reconstructed")
    result = threshold_count(rebuilt_counts, {"stat": "points", "threshold": 20, "player": "Anthony Davis"})
    assert result.data["leaders"] == [{"player": "Anthony Davis", "games": 1}]
    assert result.data["rebuilt_games"] == 0
    assert "rebuilt" not in (result.answer or "")


def test_fouls_are_never_counted_from_a_rebuilt_line(rebuilt_counts: TemplateContext) -> None:
    """A rebuilt foul is wrong in one game in six, so fouls sit outside
    REBUILT_STATS. The rebuilt 6 must not be counted - and the count of none
    must name the DECISION rather than implying the games are missing."""
    answer = threshold_count(rebuilt_counts, {"stat": "fouls", "threshold": 6, "player": "Anthony Davis"}).answer or ""
    assert "had no games with 6+ fouls" in answer
    assert "were rebuilt from play-by-play" in answer
    assert "not counted from a rebuilt line" in answer


def test_the_low_count_caveat_drops_the_games_the_rebuild_counted(rebuilt_counts: TemplateContext) -> None:
    """The self-contradiction this avoids: counting a game from its rebuilt
    line and then reporting that same game as one the count could not see.
    Counted from player_box_stats, e3 and e4 are "empty"; counted from the log,
    which knows they were rebuilt, there is nothing left to disclaim."""
    answer = threshold_count(rebuilt_counts, {"stat": "points", "threshold": 20, "player": "Anthony Davis"}).answer or ""
    assert "empty box score" not in answer
    assert "the count may be low" not in answer


def test_a_warehouse_without_the_flag_counts_only_fetched_games(rebuilt_counts: TemplateContext) -> None:
    """The column arrives with a `data load`; an older warehouse has none, and
    the query must not be written as though it were always there - the Binder
    error AGENTS.md records for view changes. The count falls back to 1."""
    rebuilt_counts.con.execute("ALTER TABLE player_game_log DROP COLUMN reconstructed")
    result = threshold_count(rebuilt_counts, {"stat": "points", "threshold": 20, "player": "Anthony Davis"})
    assert result.data["leaders"] == [{"player": "Anthony Davis", "games": 1}]
    assert "rebuilt" not in (result.answer or "")


def test_a_count_made_entirely_of_rebuilt_games_says_so_outright(rebuilt_counts: TemplateContext) -> None:
    """Anthony Davis's real 2015: every single qualifying game is rebuilt, and
    the live answer read "52 of those 52 games have no box score from ESPN" -
    true, and it reads as a bug, which costs the sentence the trust it exists
    to calibrate. A threshold of 25 leaves only the two rebuilt games here."""
    answer = threshold_count(rebuilt_counts, {"stat": "points", "threshold": 25, "player": "Anthony Davis"}).answer or ""
    assert "had 2 games with 25+ points" in answer
    assert "None of those 2 games has a box score from ESPN" in answer
    # The shape being ruled out, in both its forms.
    assert "2 of those 2 games" not in answer
    assert "games have a box score" not in answer


# ---------------- leaderboard ----------------


@pytest.fixture
def lb_con(tmp_path: Path) -> TemplateContext:
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
    return TemplateContext(con=c, out_dir=tmp_path)


def test_leaderboard_maps_a_plain_stat_slot_onto_a_real_metric(lb_con: TemplateContext) -> None:
    result = leaderboard(lb_con, {"stat": "points"})
    assert result.data["leaders"][0]["display_name"] == "Luka Doncic"


def test_leaderboard_phrases_its_own_answer(lb_con: TemplateContext) -> None:
    result = leaderboard(lb_con, {"stat": "points", "limit": 2})
    assert result.answer == (f"Luka Doncic led the league in points per game in the {current_season()} regular season (minimum 20 games), at 33.5. Next: Stephen Curry (27.1).")


def test_leaderboard_names_the_team_when_filtered(lb_con: TemplateContext) -> None:
    result = leaderboard(lb_con, {"stat": "points", "team": "Warriors"})
    assert "led the Golden State Warriors" in (result.answer or "")


def test_leaderboard_refuses_a_team_the_question_named_as_its_own_subject(lb_con: TemplateContext) -> None:
    """yardstick-v2 F127: "how many 3 pointers have the magic made so far
    this season" routed to `leaderboard` with `stat` and `season` only - no
    `team` at all - and ranked the league's individual leaders in makes,
    the Magic never named. `entities._scope_from_question_team_subject`
    restores the dropped team AND marks it `team_restored`
    (`SCOPING_SLOTS`, absent from every `HONORED_SCOPING` entry), so
    `check_scope` refuses this exact shape and hands the question to
    `query.compose` instead of `leaderboard` quietly ranking players.

    The marker is the whole point: `leaderboard`'s OWN, router-supplied
    `team` reading - ranking players WITHIN a team, proven by
    `test_leaderboard_names_the_team_when_filtered` just above - must keep
    answering directly. `team_restored` is set only where THIS restore
    itself wrote `team`, never where the router supplied it."""
    router_supplied = {"stat": "points", "team": "Warriors"}
    check_scope("leaderboard", router_supplied)  # does not raise
    restored = {"stat": "threePointFieldGoalsMade", "season": 2026, "team": "Orlando Magic", "team_restored": True}
    with pytest.raises(TemplateUnsupported, match="team_restored"):
        check_scope("leaderboard", restored)


def test_leaderboard_honors_playoffs(lb_con: TemplateContext) -> None:
    result = leaderboard(lb_con, {"stat": "points", "season_type": 3})
    assert "postseason" in (result.answer or "") and result.data["leaders"][0]["value"] == 31.0


def test_leaderboard_unmapped_stat_falls_through_rather_than_fuzzy_matching(lb_con: TemplateContext) -> None:
    # Deliberately NOT get_close_matches: silently ranking by whichever metric
    # scored highest is the substitution failure this design exists to prevent.
    with pytest.raises(TemplateUnsupported):
        leaderboard(lb_con, {"stat": "clutchness"})


def test_leaderboard_refuses_a_unit_the_metric_has_no_form_of(lb_con: TemplateContext) -> None:
    """ "who were the top 10 in defensive netpoints / 90" fell through: `rate`
    was in no template's HONORED_SCOPING, so check_scope raised - which reads
    as a refusal in the trace and is not one. The question reached an agent
    with no per-90 anything to read, free to fill the silence from its own
    weights. It is a refusal now, and it names the metric's real forms rather
    than a generic list, which would be the wrong-cause refusal again."""
    # Through check_scope, which is what raised before: calling the template
    # directly would pass whether or not `rate` is declared honored.
    slots = {"stat": "points", "rate": "/ 90"}
    check_scope("leaderboard", dict(slots))
    result = leaderboard(lb_con, slots)
    assert "per 90 minutes" in (result.answer or "")
    assert "per game" in (result.answer or "") and "season total" in (result.answer or "")
    # Points has no per-100 form, so the refusal must not offer one.
    assert "per 100" not in (result.answer or "")
    assert "leaders" not in result.data


def test_leaderboard_reads_a_season_total_now_that_rate_reaches_it(lb_con: TemplateContext) -> None:
    """The other half of the same omission: `leaderboard` already had a
    `rate == "total"` branch, and check_scope refused before it could ever
    run, so "most points this season" as a TOTAL was unreachable through the
    pipeline."""
    slots = {"stat": "points", "rate": "total"}
    check_scope("leaderboard", dict(slots))
    result = leaderboard(lb_con, slots)
    assert "total points" in (result.answer or "")
    assert result.data["leaders"][0]["display_name"] == "Luka Doncic"


def test_leaderboard_ambiguous_team_falls_through_rather_than_picking_one(lb_con: TemplateContext) -> None:
    lb_con.con.execute("INSERT INTO teams VALUES ('12','LAC','LA Clippers'),('13','LAL','Los Angeles Lakers')")
    with pytest.raises(TemplateUnsupported):
        leaderboard(lb_con, {"stat": "points", "team": "LA"})


def test_leaderboard_unknown_team_falls_through(lb_con: TemplateContext) -> None:
    with pytest.raises(TemplateUnsupported):
        leaderboard(lb_con, {"stat": "points", "team": "Not A Team"})


def test_threshold_count_honors_playoffs(con: TemplateContext) -> None:
    result = threshold_count(con, {"stat": "points", "threshold": 40, "season_type": 3})
    assert "postseason" in (result.answer or "")
    assert result.data["leaders"] == [{"player": "Bench Guy", "games": 9}]


@pytest.fixture
def shooting_ctx(tmp_path: Path) -> TemplateContext:
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
    return TemplateContext(con=c, out_dir=tmp_path)


def test_true_shooting_says_which_qualifier_it_ranked_under(shooting_ctx: TemplateContext) -> None:
    result = leaderboard(shooting_ctx, {"stat": "true_shooting", "season": 2025})
    assert result.answer == "Jarrett Allen led the league in true shooting % in the 2025 regular season (minimum 550 true-shooting attempts), at 0.724. Next: Nikola Jokic (0.663)."


@pytest.mark.parametrize(("stat", "qualifier"), [("true_shooting", "550 true-shooting attempts"), ("efg_pct", "480 field-goal attempts")])
def test_a_shooting_percentage_qualifies_on_attempts_not_games(shooting_ctx: TemplateContext, stat: str, qualifier: str) -> None:
    """Both low-volume players have the games; neither has the shots."""
    result = leaderboard(shooting_ctx, {"stat": stat, "season": 2025})
    assert [r["display_name"] for r in result.data["leaders"]] == ["Jarrett Allen", "Nikola Jokic"]
    assert f"2025 regular season (minimum {qualifier})," in (result.answer or "")


@pytest.mark.parametrize(("stat", "qualifier"), [("true_shooting", "67 true-shooting attempts"), ("efg_pct", "59 field-goal attempts")])
def test_a_shooting_percentage_scales_its_qualifier_for_the_postseason(shooting_ctx: TemplateContext, stat: str, qualifier: str) -> None:
    """The season floor is more than one player reached in the whole 2025
    postseason, so it cannot carry over, and games cannot stand in for it."""
    result = leaderboard(shooting_ctx, {"stat": stat, "season": 2025, "season_type": 3})
    assert [r["display_name"] for r in result.data["leaders"]] == ["Jarrett Allen", "Isaiah Joe", "Nikola Jokic"]
    assert f"2025 postseason (minimum {qualifier})," in (result.answer or "")


def test_an_empty_board_says_what_nobody_met(shooting_ctx: TemplateContext) -> None:
    result = leaderboard(shooting_ctx, {"stat": "true_shooting", "season": 2024})
    assert result.answer == "No players qualified for true shooting % in the league in the 2024 regular season (minimum 550 true-shooting attempts)."


@pytest.fixture
def games_ctx(tmp_path: Path) -> TemplateContext:
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
    return TemplateContext(con=c, out_dir=tmp_path)


def test_a_per_game_leaderboard_qualifies_on_games(games_ctx: TemplateContext) -> None:
    """Regression: "who led the league in rebounding in 2001" answered Danny
    Fortson, on 6 games. Measured over 1994-2026 against the warehouse, two
    regular-season boards (2000 and 2001 rebounding) and 15 postseason ones
    were led from under the floors the newer per-game metrics already used."""
    result = leaderboard(games_ctx, {"stat": "rebounds", "season": 2001})
    assert [r["display_name"] for r in result.data["leaders"]] == ["Dikembe Mutombo", "Ben Wallace"]
    assert result.data["min_sample"] == PER_GAME_MIN_GAMES


def test_a_per_game_leaderboard_names_the_qualifier_it_applied(games_ctx: TemplateContext) -> None:
    """A floor nobody is told about is why "why isn't Fortson here?" has no
    answer - and the leader's own 79 games are the reason he is."""
    answer = leaderboard(games_ctx, {"stat": "rebounds", "season": 2001}).answer
    assert answer == "Dikembe Mutombo led the league in rebounds per game in the 2001 regular season (minimum 20 games), at 13.5. Next: Ben Wallace (13.2)."


def test_a_per_game_leaderboard_scales_its_qualifier_for_the_postseason(games_ctx: TemplateContext) -> None:
    """20 games is more than a title run, so the season floor cannot carry
    over; 5 is more than a first-round sweep. Kawhi Leonard's 2 games led 2023
    playoff scoring, and Anthony Edwards's 5 still qualify."""
    result = leaderboard(games_ctx, {"stat": "points", "season": 2023, "season_type": 3})
    assert [r["display_name"] for r in result.data["leaders"]] == ["Devin Booker", "Anthony Edwards"]
    assert result.data["min_sample"] == PER_GAME_MIN_POSTSEASON_GAMES
    assert "(minimum 5 games)" in (result.answer or "")


def test_every_per_game_metric_carries_both_games_qualifiers() -> None:
    """The bug was one omission in a list, so the guard is over the whole list
    rather than the three stats it was reported for. A per-game average with
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


# ---------------- player_stat ----------------


@pytest.fixture
def ps_con(tmp_path: Path) -> TemplateContext:
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    c.execute(
        "CREATE TABLE player_season_stats_deduped (athlete_id VARCHAR, season INTEGER, season_type INTEGER, "
        "gamesPlayed INTEGER, avgPoints DOUBLE, points INTEGER, avgRebounds DOUBLE, avgAssists DOUBLE, assists INTEGER, "
        "avgSteals DOUBLE, avgBlocks DOUBLE, avgTurnovers DOUBLE, avgFouls DOUBLE, avgMinutes DOUBLE, fouls INTEGER)"
    )
    c.execute("INSERT INTO players VALUES ('1','Luka Doncic'),('2','Luka Garza'),('3','Nikola Jokic'),('4','Stephen Curry'),('5','Seth Curry')")
    s = current_season()
    c.execute("INSERT INTO player_season_stats_deduped VALUES ('1',?,2,64,33.5,2143,7.7,8.3,531,1.4,0.5,4.0,2.1,37.5,134)", [s])
    c.execute("INSERT INTO player_season_stats_deduped VALUES ('3',?,2,65,27.7,1799,12.9,10.7,697,1.3,0.9,3.6,2.5,34.6,163)", [s])
    # player_compare reports a NetPoints summary under the box-score line. Only
    # Luka has a row, so the "some players have none" path is exercised too.
    c.execute(
        "CREATE TABLE net_points_player (athlete_id VARCHAR, season INTEGER, net_points_season_type VARCHAR, overall_per_100_poss DOUBLE, offense_per_100_poss DOUBLE, defense_per_100_poss DOUBLE)"
    )
    c.execute("INSERT INTO net_points_player VALUES ('1',?,'Regular Season',5.87,5.20,0.67)", [s])
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
    result = player_stat(ps_con, {"player": "Curry", "stat": "points"})
    assert result.answer == "'Curry' matches more than one player - did you mean Seth Curry or Stephen Curry?"
    assert result.data["candidates"] == ["Seth Curry", "Stephen Curry"]


def test_player_stat_unknown_player_falls_through(ps_con: TemplateContext) -> None:
    with pytest.raises(TemplateUnsupported):
        player_stat(ps_con, {"player": "Nobody At All"})


def test_player_stat_missing_player_slot_falls_through(ps_con: TemplateContext) -> None:
    with pytest.raises(TemplateUnsupported):
        player_stat(ps_con, {"stat": "points"})


def test_player_stat_reports_a_missing_season_honestly(ps_con: TemplateContext) -> None:
    result = player_stat(ps_con, {"player": "Luka Doncic", "season": 1999})
    assert result.answer == "Luka Doncic has no 1999 regular season numbers in the warehouse."


def test_player_stat_defaulted_season_redirects_to_a_retired_players_range(ps_con: TemplateContext) -> None:
    """The season was never named - it defaulted to "now" - so a retired
    player with nothing there gets pointed at what the warehouse DOES hold for
    him instead of a refusal that reads as though his whole career were
    missing (issue #18). "Allen Iverson has no 2026 regular season numbers"
    is true and answers a different question than the one asked."""
    ps_con.con.execute("INSERT INTO players VALUES ('6','Old Timer')")
    ps_con.con.execute("INSERT INTO player_season_stats_deduped VALUES ('6',2005,2,70,20.0,1400,5.0,4.0,280,1.0,0.5,2.0,2.0,35.0,140)")
    ps_con.con.execute("INSERT INTO player_season_stats_deduped VALUES ('6',2008,2,60,18.0,1080,4.0,3.5,210,1.0,0.4,1.8,1.8,32.0,108)")
    answer = player_stat(ps_con, {"player": "Old Timer", "stat": "points"}).answer
    assert answer == (
        f"Old Timer has no {current_season()} regular season numbers in the warehouse. He last appears in 2008. The warehouse holds his 2005-2008 regular seasons; name one, or ask for his career."
    )


def test_player_stat_defaulted_season_with_nothing_on_record_stays_plain(ps_con: TemplateContext) -> None:
    """A defaulted season is only a redirect when there is somewhere to
    redirect to. A player with no rows in the table at all - as opposed to no
    rows in the current season - has nothing to point at, and the plain
    refusal is the honest answer: there is genuinely no data on record."""
    ps_con.con.execute("INSERT INTO players VALUES ('7','Nobody Yet')")
    answer = player_stat(ps_con, {"player": "Nobody Yet", "stat": "points"}).answer
    assert answer == f"Nobody Yet has no {current_season()} regular season numbers in the warehouse."


def test_player_stat_a_named_season_keeps_the_plain_refusal(ps_con: TemplateContext) -> None:
    """The season the question named IS the fact the answer is about - unlike
    the defaulted case above, redirecting a season asked for outright would be
    a fluent answer to a question nobody asked."""
    ps_con.con.execute("INSERT INTO players VALUES ('6','Old Timer')")
    ps_con.con.execute("INSERT INTO player_season_stats_deduped VALUES ('6',2005,2,70,20.0,1400,5.0,4.0,280,1.0,0.5,2.0,2.0,35.0,140)")
    answer = player_stat(ps_con, {"player": "Old Timer", "stat": "points", "season": 1999}).answer
    assert answer == "Old Timer has no 1999 regular season numbers in the warehouse."


def test_player_stat_never_reports_a_total_as_a_per_game_number(ps_con: TemplateContext) -> None:
    # Regression on phrasing: the total used to be inlined as "33.5 points
    # (2143 total) per game", which states something false.
    answer = player_stat(ps_con, {"player": "Luka Doncic", "stat": "points"}).answer or ""
    assert "(2,143 total) per game" not in answer and "2143" not in answer


# ---------------- a surname, narrowed to the season asked about ----------------


@pytest.fixture
def curry_ctx(tmp_path: Path) -> TemplateContext:
    """The six Currys the warehouse really holds. "How did curry do against the
    celtics this year" asked about five of them - Dell, Eddy, JamesOn, Michael
    and Seth - and hid Stephen behind "(1 others also match)", because the list
    was sorted by name and cut at five. Four of the five it named never played
    in the season being asked about.

    Each has a season line in a year he really played; only Seth and Stephen
    have one in the current season, as in the warehouse."""
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
    return TemplateContext(con=c, out_dir=tmp_path)


def test_a_surname_asks_only_about_the_players_who_played_that_season(curry_ctx: TemplateContext) -> None:
    """The reported answer, verbatim, was "did you mean Dell Curry, Eddy Curry,
    JamesOn Curry, Michael Curry or Seth Curry (1 others also match)?" - the
    player almost certainly meant was the one it left out. Seth and Stephen
    both played this season, so it still asks; it just asks about them."""
    result = player_stat(curry_ctx, {"player": "Curry", "season": current_season()})
    assert result.answer == "'Curry' matches more than one player - did you mean Seth Curry or Stephen Curry?"
    assert result.data["candidates"] == ["Seth Curry", "Stephen Curry"]


def test_a_surname_resolves_when_only_one_candidate_played_that_season(curry_ctx: TemplateContext) -> None:
    """Elimination, not preference: the other five have no row to answer
    from, so there is nothing left to choose between."""
    curry_ctx.con.execute("DELETE FROM player_season_stats_deduped WHERE athlete_id = '5'")
    answer = player_stat(curry_ctx, {"player": "Curry", "stat": "points"}).answer
    assert answer == f"Stephen Curry averaged 26.6 points per game in 60 games in the {current_season()} regular season. That is 1,600 in total."


def test_narrowing_that_eliminates_everybody_still_asks_about_everybody(curry_ctx: TemplateContext) -> None:
    """No Curry played in 1990, so no answer to "which one?" has numbers - but
    picking one because the season is empty would be a guess, so the question
    is asked exactly as it was before narrowing existed."""
    result = player_stat(curry_ctx, {"player": "Curry", "season": 1990})
    assert result.data["candidates"] == ["Dell Curry", "Eddy Curry", "JamesOn Curry", "Michael Curry", "Seth Curry", "Stephen Curry"]


def _paytons(ctx: TemplateContext) -> None:
    """ "Gary Payton" is one player's whole name and also matches Gary Payton
    II; only the son has a line this season - as in the warehouse, where the
    father's last is 2007. Not "Dell Curry": that name matches one player, so it
    never reaches the exact-match rule - and a perturbation removing the rule
    passed against it."""
    ctx.con.execute("INSERT INTO players VALUES ('7','Gary Payton'),('8','Gary Payton II')")
    ctx.con.execute("INSERT INTO player_season_stats_deduped VALUES ('7',2007,2,70,5.0,350,2.0,3.0,210),('8',?,2,60,7.0,420,3.0,2.0,120)", [current_season()])


def test_a_name_given_in_full_is_its_owner_wherever_he_has_numbers(curry_ctx: TemplateContext) -> None:
    """The father's name in full, in a season he played and over a career, is
    the father - the son being the Payton who plays now changes nothing."""
    _paytons(curry_ctx)
    assert player_stat(curry_ctx, {"player": "Gary Payton", "season": 2007}).answer.startswith("Gary Payton averaged 5 points")
    career = resolve_player(curry_ctx.con, "Gary Payton", Availability("player_season_stats_deduped"), None, current_season())
    assert isinstance(career, Entity) and career.name == "Gary Payton"


def test_a_name_given_in_full_yields_to_the_one_namesake_with_numbers(curry_ctx: TemplateContext) -> None:
    """This used to answer "Gary Payton has no 2026 regular season numbers" -
    true, and about the wrong man when the question was the son's (the filed
    case was "Jabari Smith", the retired father's exact name, where the son is
    "Jabari Smith Jr."). The father cannot be the answer to a question about
    this season and exactly one namesake can, so it is him, and the reading is
    said with the way back to the father."""
    _paytons(curry_ctx)
    with collect_name_readings() as readings:
        answer = player_stat(curry_ctx, {"player": "Gary Payton", "season": current_season()}).answer
    assert answer.startswith("Gary Payton II averaged 7 points")
    s = current_season()
    assert readings == [f"('Gary Payton' was read as Gary Payton II, the only match who played in {s - 1}-{s % 100:02d}. Gary Payton also matches - name a season he played to ask about him.)"]


def test_a_name_left_open_is_whoever_played_the_last_season_of_the_span(curry_ctx: TemplateContext) -> None:
    """A history through 2005 reads each Curry's seasons up to 2005, so Dell and
    Michael could answer it too - and it used to ask which of the three. Eddy is
    the only one who played in 2005, so it is his, said with the others named:
    a default is allowed where it is visible and can be corrected."""
    with collect_name_readings() as readings:
        result = player_history(curry_ctx, {"player": "Curry", "stat": "points", "season": 2005})
    assert result.answer.startswith("Eddy Curry, points per game by regular season")
    assert readings == [
        "('Curry' was read as Eddy Curry, the only match who played in 2004-05. Dell Curry and Michael Curry also match - use the full name, or name a season they played, to ask about one of them.)"
    ]


def test_a_reading_is_collected_only_where_somebody_is_listening(curry_ctx: TemplateContext) -> None:
    """Outside collect_name_readings the resolution is the same and nothing is
    kept - a template called directly has nowhere to put the sentence."""
    assert player_history(curry_ctx, {"player": "Curry", "stat": "points", "season": 2005}).answer.startswith("Eddy Curry")


def test_a_history_names_whoever_reached_its_last_season_first(curry_ctx: TemplateContext) -> None:
    """ "Curry's scoring over the last 4 seasons" keeps all six Currys - each
    has seasons through 2026 to answer with - and so hid Stephen behind the
    cap exactly as the one-season question did. The two who played in the
    season the history ends at are named first, and never counted away."""
    result = player_history(curry_ctx, {"player": "Curry", "stat": "points", "limit": 4})
    assert result.answer == "'Curry' matches more than one player - did you mean Seth Curry, Stephen Curry, Dell Curry, Eddy Curry or JamesOn Curry (1 other also matches)?"


def test_a_career_keeps_every_curry_but_names_the_active_ones_first(curry_ctx: TemplateContext) -> None:
    """A career question has every season in scope, so Dell's career is as real
    an answer as Stephen's and nobody is eliminated. Narrowed like one season,
    it would have been; left unordered, the cap would hide Stephen again."""
    result = player_stat(curry_ctx, {"player": "Curry", "span": "career"})
    assert result.data["candidates"] == ["Seth Curry", "Stephen Curry", "Dell Curry", "Eddy Curry", "JamesOn Curry", "Michael Curry"]
    assert result.answer == "'Curry' matches more than one player - did you mean Seth Curry, Stephen Curry, Dell Curry, Eddy Curry or JamesOn Curry (1 other also matches)?"


def test_a_game_log_narrows_to_whoever_has_games_that_season(curry_ctx: TemplateContext) -> None:
    """No season named means the current one. Dell has games in 2000 and Seth
    and Stephen this season, so only the two of them are asked about.

    Measured on the warehouse, not in a fixture: after game_log learned spans,
    its `season` became the raw slot, and passing that through narrowed
    "curry's last 5 games" over every season - the clarification named Dell,
    Eddy, JamesOn, Michael and Seth, and hid Stephen again."""
    curry_ctx.con.execute("INSERT INTO player_game_log VALUES ('1',2000,2,'2000-01-02','BOS',30.0,12,2,3),('5',?,2,'2026-01-03','BOS',20.0,8,1,2)", [current_season()])
    result = game_log(curry_ctx, {"player": "Curry", "limit": 5, "order": "recent"})
    assert result.data["candidates"] == ["Seth Curry", "Stephen Curry"]


def test_a_without_teammate_is_found_past_the_first_page_of_matches() -> None:
    """ "Without williams" is 62 players by name. The teammate narrowing read
    only find_players' first page of ten, so a teammate who sorted eleventh was
    reported as nobody's teammate at all."""
    from association.query.entities import Entity
    from association.query.templates.common import _resolved_teammate, _Span

    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    given = ["Alan", "Brandon", "Cody", "Deron", "Eric", "Frank", "Grant", "Hank", "Ike", "Jalen", "Kenrich"]
    c.executemany("INSERT INTO players VALUES (?, ?)", [(str(i), f"{name} Williams") for i, name in enumerate(given)])
    c.execute("INSERT INTO players VALUES ('k', 'Klay Thompson')")
    c.execute("CREATE TABLE player_box_stats (athlete_id VARCHAR, season INTEGER, season_type INTEGER, team_id VARCHAR)")
    c.execute("INSERT INTO player_box_stats VALUES ('k', 2026, 2, '9'), ('10', 2026, 2, '9'), ('0', 2026, 2, '5')")
    got = _resolved_teammate(c, "Williams", Entity(id="k", name="Klay Thompson"), _Span(2026, 2))
    assert got == Entity(id="10", name="Kenrich Williams")


def test_a_career_high_names_the_currys_playing_now_first(curry_ctx: TemplateContext) -> None:
    """single_game_high reads a career as every season on record, so Dell stays
    a candidate - but left unanchored, the list was in name order and the cap
    would cut Stephen behind the retired Currys."""
    curry_ctx.con.execute("INSERT INTO player_game_log VALUES ('1',2000,2,'2000-01-02','BOS',30.0,12,2,3),('5',?,2,'2026-01-03','BOS',20.0,8,1,2)", [current_season()])
    result = single_game_high(curry_ctx, {"stat": "points", "player": "Curry", "span": "career"})
    assert result.data["candidates"] == ["Seth Curry", "Stephen Curry", "Dell Curry"]


def test_netpoints_narrows_against_either_of_the_tables_it_reads(curry_ctx: TemplateContext) -> None:
    """player_netpoints answers from the season totals OR the fingerprint, and
    the two disagree about who they hold - 63 player-seasons are in the first
    only and 8 in the second only. A candidate with a row in either one has an
    answer, so neither table alone may eliminate him."""
    curry_ctx.con.execute("CREATE TABLE net_points_player (athlete_id VARCHAR, season INTEGER)")
    curry_ctx.con.execute("CREATE TABLE net_points_player_fingerprint (athlete_id VARCHAR, season INTEGER)")
    curry_ctx.con.execute("INSERT INTO net_points_player VALUES ('6', ?)", [current_season()])
    curry_ctx.con.execute("INSERT INTO net_points_player_fingerprint VALUES ('5', ?)", [current_season()])
    result = player_netpoints(curry_ctx, {"player": "Curry"})
    assert result.data["candidates"] == ["Seth Curry", "Stephen Curry"]


def test_netpoints_without_its_tables_asks_as_it_always_did(curry_ctx: TemplateContext) -> None:
    """NetPoints is an opt-in fetch, so its tables may not exist at all. With
    nothing to narrow against, an ambiguous name gets the question it always
    got, rather than a catalog error out of the resolver."""
    result = player_netpoints(curry_ctx, {"player": "Curry"})
    assert len(result.data["candidates"]) == 6


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
        "home_linescores VARCHAR, away_linescores VARCHAR, neutral_site BOOLEAN, venue_city VARCHAR)"
    )
    c.execute("CREATE TABLE team_box_stats (event_id VARCHAR, season INTEGER, season_type INTEGER, team_id VARCHAR, opponent_team_id VARCHAR, home_away VARCHAR)")
    c.execute("INSERT INTO teams VALUES ('18','NY','New York Knicks'),('2','BOS','Boston Celtics'),('5','LAL','Los Angeles Lakers')")
    s = current_season()
    # `neutral_site`/`venue_city` are new columns (step 3, C4b): team_quarter_points
    # now reads its games off query/team_games.py's relation, whose own
    # `TEAM_GAMES_SQL` (`association.query.team_games._TEAM_GAMES`) reads both
    # unconditionally (cup-final detection, the home/road split) - every other
    # fixture that relation is read against already carries them
    # (`tests/query/test_team_templates.py: team_ctx`, this file's own
    # `playoff_ctx`); this one predates any reader of the relation reaching it.
    # None of these three games is at a neutral site.
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
    return TemplateContext(con=c, out_dir=tmp_path)


# team_record's own tests are in test_team_templates.py, over a fixture with
# the standings columns it reads.


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


def _add_knicks_postseason(gl_con: TemplateContext) -> None:
    """Two postseason games after `gl_con`'s two regular-season ones, so a
    "last N games" question has real games of both types to mix - the shape
    ISSUES.md's "'Last N games' means the last N regular-season games, even
    when playoff games came after" is about."""
    s = current_season()
    gl_con.con.execute(
        "INSERT INTO games VALUES ('p1',?,3,'2026-04-20T22:00Z','18','2',101,90,'18',false,'New York'),('p2',?,3,'2026-04-22T22:00Z','2','18',99,105,'18',false,'Boston')",
        [s, s],
    )
    gl_con.con.execute("INSERT INTO team_box_stats VALUES ('p1',?,3,'18','2','home'),('p2',?,3,'18','2','away')", [s, s])
    real_games.build_table(gl_con.con, {"games", "teams"})


def test_a_teams_last_n_games_with_no_season_type_named_reads_both(gl_con: TemplateContext) -> None:
    """ "Show me the Knicks last 5 games" (ISSUES.md): the real last three are
    both playoff games newer than the regular-season ones, so the default
    game_log used before this fix - the regular season alone - would have
    left them both out."""
    _add_knicks_postseason(gl_con)
    result = game_log(gl_con, {"team": "Knicks", "order": "recent", "limit": 3, "season_type_unstated": True})
    games = result.data["games"]
    assert [g["date"] for g in games] == ["2026-04-22", "2026-04-20", "2026-04-12"]
    assert [g["season"] for g in games] == [2026, 2026, 2026]  # a postseason game's own calendar year
    answer = result.answer or ""
    # The default it used is visible, not silent - the whole point of the fix.
    assert "last 3 games (1 regular season and 2 postseason)" in answer
    assert result.data["wins"] == 2 and result.data["losses"] == 1


def test_a_teams_last_n_games_states_the_total_points_asked_for(gl_con: TemplateContext) -> None:
    """F128 (ISSUES.md): "total points scored by the raptors in the last 10
    games" narrowed correctly but never stated the total - the games were
    right and the question's own number was still missing. e2 (away, 96),
    p1 (home, 101) and p2 (away, 105) sum to 302."""
    _add_knicks_postseason(gl_con)
    result = game_log(gl_con, {"team": "Knicks", "order": "recent", "limit": 3, "season_type_unstated": True, "stat": "points"})
    assert "\n  Total points: 302." in (result.answer or "")


def test_a_teams_last_n_games_states_the_point_differential_asked_for(gl_con: TemplateContext) -> None:
    """F129 (ISSUES.md): "Knicks point differential over the last 7 games" -
    e2 (-14), p1 (+11) and p2 (+6) sum to +3 over 3 games, +1.00 per game."""
    _add_knicks_postseason(gl_con)
    result = game_log(gl_con, {"team": "Knicks", "order": "recent", "limit": 3, "season_type_unstated": True, "stat": "pointsDifference"})
    assert "\n  Point differential: +3 (+1.00 per game)." in (result.answer or "")


def test_a_teams_single_type_log_also_states_a_total(gl_con: TemplateContext) -> None:
    """The single-season-type reader (``_team_game_log``, not the mixed one)
    gets the same line: the Knicks' two regular-season games (112 home, 96
    away) total 208 points."""
    result = game_log(gl_con, {"team": "Knicks", "stat": "points"})
    assert "\n  Total points: 208." in (result.answer or "")


def test_a_teams_plain_stat_names_no_total_line(gl_con: TemplateContext) -> None:
    """A `stat` this module does not map to a total (or none at all) changes
    nothing about the plain listing - the fix is additive, not a rewording of
    every log."""
    result = game_log(gl_con, {"team": "Knicks"})
    answer = result.answer or ""
    assert "Total points" not in answer and "Point differential" not in answer


def test_a_teams_last_n_games_naming_its_season_type_is_unchanged(gl_con: TemplateContext) -> None:
    """The correction: saying "playoff games" or "regular season games"
    outright still means only that - the shape this fix must not touch."""
    _add_knicks_postseason(gl_con)
    playoffs = game_log(gl_con, {"team": "Knicks", "order": "recent", "limit": 5, "season_type": 3})
    assert [g["date"] for g in playoffs.data["games"]] == ["2026-04-22", "2026-04-20"]
    assert "of the 2026 postseason" in (playoffs.answer or "")
    regular = game_log(gl_con, {"team": "Knicks", "order": "recent", "limit": 5, "season_type": 2})
    assert [g["date"] for g in regular.data["games"]] == ["2026-04-12", "2026-04-10"]
    assert "of the 2026 regular season" in (regular.answer or "")


def test_a_teams_last_n_games_all_one_type_reads_exactly_as_before(gl_con: TemplateContext) -> None:
    """When the last N happen to be all one type - most teams and players
    never reach the postseason at all - the header reads exactly as it always
    did, not "1 regular season" repeated N times."""
    result = game_log(gl_con, {"team": "Knicks", "order": "recent", "limit": 2, "season_type_unstated": True})
    assert [g["date"] for g in result.data["games"]] == ["2026-04-12", "2026-04-10"]
    assert "last 2 games of the 2026 regular season" in (result.answer or "")


def test_a_teams_last_n_games_with_no_games_at_all_says_so_without_blaming_a_type(gl_con: TemplateContext) -> None:
    gl_con.con.execute("INSERT INTO teams VALUES ('99','LAC','LA Clippers')")
    result = game_log(gl_con, {"team": "LA Clippers", "order": "recent", "limit": 5, "season_type_unstated": True})
    assert result.data["games"] == []
    assert "No 2026 games found for the LA Clippers" in (result.answer or "")


def test_a_players_last_n_games_with_no_season_type_named_reads_both(pg_ctx: TemplateContext) -> None:
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
    uniform = game_log(pg_ctx, {"player": "Brandin Podziemski", "order": "recent", "limit": 5, "season_type_unstated": True})
    assert [g["date"][5:] for g in uniform.data["games"]] == ["05-04", "05-02", "04-24", "04-21", "04-19"]
    assert f"last 5 games of the {s} postseason" in (uniform.answer or "")
    mixed = game_log(pg_ctx, {"player": "Brandin Podziemski", "order": "recent", "limit": 6, "season_type_unstated": True})
    assert [g["date"][5:] for g in mixed.data["games"]] == ["05-04", "05-02", "04-24", "04-21", "04-19", "01-10"]
    assert "last 6 games (1 regular season and 5 postseason)" in (mixed.answer or "")
    # His per-game average is over exactly the 6 rows shown, playoffs and
    # regular season points combined - not silently the regular season alone.
    assert mixed.data["averages"]["points"] == pytest.approx((10 + 20 + 30 + 5 + 15 + 15) / 6)


def test_a_players_last_n_games_keeps_its_other_narrowings_under_both_types(pg_ctx: TemplateContext) -> None:
    """`opponent` still narrows a mixed-type log - Podziemski's games against
    Detroit span both season types (e2, e3 regular season; p1-p3 postseason),
    so "last 3 games vs Detroit" with no season type stated finds the three
    newest of either type against them, not just the regular-season two."""
    result = game_log(pg_ctx, {"player": "Brandin Podziemski", "order": "recent", "limit": 3, "season_type_unstated": True, "opponent": "Detroit Pistons"})
    assert [g["date"][5:] for g in result.data["games"]] == ["04-24", "04-21", "04-19"]
    assert "vs the Detroit Pistons" in (result.answer or "")


def test_a_players_last_n_games_with_no_games_this_season_says_so(pg_ctx: TemplateContext) -> None:
    result = game_log(pg_ctx, {"player": "Brandin Podziemski", "order": "recent", "limit": 5, "season_type_unstated": True, "season": 1990})
    assert result.data["games"] == []
    assert "no games recorded in the 1990 season" in (result.answer or "")


# ---------------- shot_chart ----------------


@pytest.fixture
def sc_ctx(tmp_path: Path) -> TemplateContext:
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    c.execute(
        "CREATE TABLE shot_chart (athlete_id VARCHAR, season INTEGER, season_type INTEGER, event_id VARCHAR, "
        "period INTEGER, clock VARCHAR, made BOOLEAN, shot_type VARCHAR, coordinate_x INTEGER, coordinate_y INTEGER, points_attempted INTEGER, description VARCHAR)"
    )
    # Empty by default - a shot_chart question with no narrowing at all never
    # reaches the relation (step 3, C5's `_shots_has_narrowing`), so most
    # tests here need neither table. The handful that narrow by `order`
    # INSERT into these directly rather than declaring their own copy, since
    # `player_game_log`/`games` now have to carry the columns the relation's
    # played guard and window read (`minutes`, `did_not_play`, `starter`,
    # `opponent_team_id`, `games.date`). `opponent_abbr` is NULL by
    # construction here (no `teams` table), which is what makes
    # `game_label` return None and a single-game chart fall back to naming
    # its bare event id, exactly as it did before this existed.
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
    return TemplateContext(con=c, out_dir=tmp_path / "out")


def test_shot_chart_writes_a_file_and_reports_its_path(sc_ctx: TemplateContext) -> None:
    result = shot_chart(sc_ctx, {"player": "Stephen Curry", "season": current_season()})
    assert "Rendered shot chart for Stephen Curry" in (result.answer or "")
    assert list((sc_ctx.out_dir).glob("*.html"))


def test_shot_chart_narrows_a_surname_to_whoever_took_shots_that_season(sc_ctx: TemplateContext) -> None:
    """Seth exists and Stephen has the shots, so "Curry" is not a question this
    season - only one of them can have produced the chart being asked for."""
    sc_ctx.con.execute("INSERT INTO players VALUES ('2','Seth Curry')")
    result = shot_chart(sc_ctx, {"player": "Curry", "season": current_season()})
    assert "Rendered shot chart for Stephen Curry" in (result.answer or "")


def test_shot_chart_asks_which_player_when_both_took_shots(sc_ctx: TemplateContext) -> None:
    """And when narrowing cannot separate them it asks, rather than drawing
    whichever sorts first and titling the plot with the wrong Curry."""
    sc_ctx.con.execute("INSERT INTO players VALUES ('2','Seth Curry')")
    sc_ctx.con.execute(f"INSERT INTO shot_chart VALUES ('2',{current_season()},2,'e2',1,'9:00',TRUE,'Jump Shot',25,26,3,'26-foot three point jumper')")
    result = shot_chart(sc_ctx, {"player": "Curry", "season": current_season()})
    assert result.answer == "'Curry' matches more than one player - did you mean Seth Curry or Stephen Curry?"
    assert not list(sc_ctx.out_dir.glob("*.html"))


def test_shot_chart_reports_no_matching_shots_rather_than_falling_through(sc_ctx: TemplateContext) -> None:
    # The agent has no better source for a chart than the table just queried,
    # so an empty result is the answer, not a reason to spend minutes.
    result = shot_chart(sc_ctx, {"player": "Stephen Curry", "season": 1999})
    assert "No shots found" in (result.answer or "")


def test_shot_chart_defaulted_season_redirects_to_a_retired_players_range(sc_ctx: TemplateContext) -> None:
    """No season was named - "now" defaulted - so a player with shots only in
    an earlier season is pointed at it instead of "with the given filters",
    which blames a filter that was never given and reads as though the
    warehouse held nothing of his at all (issue #18). No "or ask for his
    career" - shot_chart draws one season, never a career."""
    sc_ctx.con.execute("INSERT INTO players VALUES ('2','Old Timer')")
    sc_ctx.con.execute("INSERT INTO shot_chart VALUES ('2',2010,2,'e9',1,'8:00',TRUE,'Jump Shot',25,26,2,'20-foot two point jumper')")
    result = shot_chart(sc_ctx, {"player": "Old Timer"})
    assert result.answer == "No shots found for Old Timer with the given filters. He last appears in 2010. The warehouse holds his 2010 regular season; name one."
    assert result.artifacts == []


def test_shot_chart_a_named_season_keeps_the_plain_refusal(sc_ctx: TemplateContext) -> None:
    """The season the question named is the fact the refusal is about, so it
    is left exactly as it read before this fix - unlike the defaulted case
    above."""
    sc_ctx.con.execute("INSERT INTO players VALUES ('2','Old Timer')")
    sc_ctx.con.execute("INSERT INTO shot_chart VALUES ('2',2010,2,'e9',1,'8:00',TRUE,'Jump Shot',25,26,2,'20-foot two point jumper')")
    result = shot_chart(sc_ctx, {"player": "Old Timer", "season": 1999})
    assert result.answer == "No shots found for Old Timer with the given filters."


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
    resolutions can pick differently, and the result would be a chart titled for
    one Curry scoped to a game the other one played: wrong, and invisible, since
    the plot looks perfectly normal.

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
    ctx = TemplateContext(con=c, out_dir=tmp_path / "out")

    calls = []
    real = shotchart.find_players

    def counting(con: Any, text: str, *args: Any, **kwargs: Any) -> Any:
        calls.append(text)
        return real(con, text, *args, **kwargs)

    monkeypatch.setattr(shotchart, "find_players", counting)
    result = shot_chart(ctx, {"player": "Curry", "order": "recent", "season": current_season()})

    # The load-bearing assertion: the name is resolved ONCE. Two resolutions
    # agree only as long as both spell the tie-break the same way, which is a
    # convention, not a guarantee.
    assert len(calls) == 1, f"player resolved {len(calls)} times: {calls}"

    answer = result.answer or ""
    assert "Stephen Curry" in answer
    written = [f.name for f in ctx.out_dir.glob("*.html")]
    assert len(written) == 1
    # The scoping event_id lands in the filename; it must be the charted
    # player's game, not the other candidate's.
    assert "seth_game" not in written[0] and "steph_game" in written[0]


def test_shot_chart_defaults_an_unspecified_season_to_the_current_one(sc_ctx: TemplateContext) -> None:
    """Passing None through charted a player's entire career in one plot
    (confirmed live: 3,665 Curry attempts across every season)."""
    sc_ctx.con.execute("INSERT INTO shot_chart VALUES ('1',2019,2,'e9',1,'5:00',TRUE,'Jump Shot',10,10,2,'makes 18-foot jumper')")
    answer = shot_chart(sc_ctx, {"player": "Stephen Curry"}).answer or ""
    # Only this season's two shots, not the 2019 one as well.
    assert "1/2 made" in answer and str(current_season()) in answer


# ---------------- shot_chart / shot_distance: `span` "career" (#141) ----------------
#
# "show a shot chart for steph curry in all playoff games" routed to a single
# defaulted season and drew it as though it were every postseason. These
# exercise the fix: HONORED_SCOPING now lets `span` reach the template, and
# `_career_shot_note` is what keeps drawing every season from becoming the
# same silent-narrowing bug pointed the other way (a career drawn with no
# mention that it IS a career, or - worse - drawn with seasons quietly missing).


def _add_career_table(con: duckdb.DuckDBPyConnection, rows: list[tuple[str, int, int, int]]) -> None:
    """The slice of `player_season_stats_deduped` `_career_shot_span` reads -
    just enough columns to say where a player's own career sits against the
    2002 shot floor, independent of `shot_chart` itself."""
    con.execute("CREATE TABLE player_season_stats_deduped (athlete_id VARCHAR, season INTEGER, season_type INTEGER, gamesPlayed INTEGER)")
    con.executemany("INSERT INTO player_season_stats_deduped VALUES (?,?,?,?)", rows)


def test_shot_chart_honors_a_career_span(sc_ctx: TemplateContext) -> None:
    """Draws every regular season on record, not only the one the season slot
    would have defaulted to - the fixture's two current-season shots AND a
    2019 one, 2/3 made where a single season drew 1/2."""
    _add_career_table(sc_ctx.con, [("1", 2019, 2, 70), ("1", current_season(), 2, 60)])
    sc_ctx.con.execute("INSERT INTO shot_chart VALUES ('1',2019,2,'e9',1,'5:00',TRUE,'Jump Shot',10,10,2,'makes 18-foot jumper')")
    answer = shot_chart(sc_ctx, {"player": "Stephen Curry", "season_type": 2, "span": "career"}).answer or ""
    assert "2/3 made" in answer
    assert f"Covers his whole regular season career on record (2019-{current_season()})." in answer


def test_shot_chart_career_span_names_a_career_entirely_before_the_floor(sc_ctx: TemplateContext) -> None:
    """A career that ends before shots begin has nothing to draw, and the
    answer says why - not a plain "No shots found ... with the given
    filters", which would blame a filter that was never given and read as
    though the warehouse held nothing of his at all."""
    _add_career_table(sc_ctx.con, [("1", 1997, 3, 20), ("1", 1998, 3, 15)])
    answer = shot_chart(sc_ctx, {"player": "Stephen Curry", "season_type": 3, "span": "career"}).answer or ""
    assert answer == "No shots found for Stephen Curry with the given filters. Stephen Curry's postseason career (1997-1998) ends before shot data begins, in 2002, so none of it can be shown."


def test_shot_chart_career_span_names_the_seasons_the_floor_leaves_out(sc_ctx: TemplateContext) -> None:
    """A career that straddles 2002 draws what it can and names what it
    can't, the same discipline a defaulted single season's redirect uses."""
    _add_career_table(sc_ctx.con, [("1", 1999, 2, 50), ("1", current_season(), 2, 60)])
    answer = shot_chart(sc_ctx, {"player": "Stephen Curry", "season_type": 2, "span": "career"}).answer or ""
    assert "Shot data begins with the 2002 season, so his 1999-2001 regular seasons are not shown." in answer


def test_shot_chart_refuses_a_career_span_with_a_named_season(sc_ctx: TemplateContext) -> None:
    """ "Career" and a named year at once answer different questions - the same
    conflict `_span_of` raises on elsewhere."""
    _add_career_table(sc_ctx.con, [("1", current_season(), 2, 60)])
    with pytest.raises(TemplateUnsupported, match="career span and the 2020 season"):
        shot_chart(sc_ctx, {"player": "Stephen Curry", "season": 2020, "span": "career"})


def test_shot_chart_refuses_a_career_span_with_an_order(sc_ctx: TemplateContext) -> None:
    """ "His last game" picks one game inside one season; a career asks for
    every one of them. Falls through rather than silently picking one."""
    _add_career_table(sc_ctx.con, [("1", current_season(), 2, 60)])
    with pytest.raises(TemplateUnsupported, match="career span"):
        shot_chart(sc_ctx, {"player": "Stephen Curry", "span": "career", "order": "recent"})


def test_shot_distance_honors_a_career_span(sc_ctx: TemplateContext) -> None:
    """Averages across every season on record instead of only the latest,
    the same shape as shot_chart."""
    _add_career_table(sc_ctx.con, [("1", 2019, 2, 70), ("1", current_season(), 2, 60)])
    sc_ctx.con.execute("INSERT INTO shot_chart VALUES ('1',2019,2,'e9',1,'5:00',TRUE,'Jump Shot',10,10,2,'makes 18-foot jumper')")
    answer = shot_distance(sc_ctx, {"player": "Stephen Curry", "season_type": 2, "span": "career"}).answer or ""
    assert "over 3 attempts" in answer
    assert f"Covers his whole regular season career on record (2019-{current_season()})." in answer


def test_shot_distance_career_span_names_a_career_entirely_before_the_floor(sc_ctx: TemplateContext) -> None:
    _add_career_table(sc_ctx.con, [("1", 1997, 3, 20), ("1", 1998, 3, 15)])
    answer = shot_distance(sc_ctx, {"player": "Stephen Curry", "season_type": 3, "span": "career"}).answer or ""
    assert answer == (
        "No shots with recorded coordinates for Stephen Curry in the career postseason. Stephen Curry's postseason career (1997-1998) ends before "
        "shot data begins, in 2002, so none of it can be shown."
    )


# ---------------- player_compare ----------------


def test_player_compare_puts_players_side_by_side(ps_con: TemplateContext) -> None:
    answer = player_compare(ps_con, {"players": ["Luka Doncic", "Nikola Jokic"]}).answer or ""
    assert "Luka Doncic vs Nikola Jokic" in answer
    assert "points" in answer and "33.5" in answer and "27.7" in answer


def test_player_compare_defaults_to_the_whole_stat_line(ps_con: TemplateContext) -> None:
    """A comparison asks which player is better, and three counting stats
    cannot answer that - they omit both halves of the defensive line and
    everything a player gives back. A table costs nothing per row, so the rows
    a comparison turns on are all there by default. player_stat is unchanged:
    "how many points did Luka average" wants the number it asked for."""
    answer = player_compare(ps_con, {"players": ["Luka Doncic", "Nikola Jokic"]}).answer or ""
    for label in ("points", "rebounds", "assists", "steals", "blocks", "turnovers", "fouls", "minutes"):
        assert f"\n{label}" in answer, label
    assert "4.0" in answer and "3.6" in answer  # turnovers, which the old line omitted


def test_player_compare_shows_the_netpoints_summary(ps_con: TemplateContext) -> None:
    """Per 100 possessions, not season totals: a comparison is exactly the
    question totals answer badly, since they mostly rank by playing time."""
    result = player_compare(ps_con, {"players": ["Luka Doncic", "Nikola Jokic"]})
    answer = result.answer or ""
    assert "net pts/100" in answer and "offense" in answer and "defense" in answer
    assert "+5.87" in answer and "+5.20" in answer and "+0.67" in answer
    assert result.data["netpoints"]["Luka Doncic"]["overall_per_100_poss"] == 5.87


def test_a_player_with_no_netpoints_row_is_blank_rather_than_zero(ps_con: TemplateContext) -> None:
    """Only Luka has a row in the fixture. Drawing Jokic as +0.00 would read as
    "contributed nothing" rather than "not on record"."""
    answer = player_compare(ps_con, {"players": ["Luka Doncic", "Nikola Jokic"]}).answer or ""
    netpoints_row = next(line for line in answer.splitlines() if line.startswith("net pts/100"))
    assert "+5.87" in netpoints_row and "+0.00" not in netpoints_row and netpoints_row.rstrip().endswith("-")


def test_player_compare_omits_netpoints_entirely_when_nobody_has_any(ps_con: TemplateContext) -> None:
    """An empty NetPoints block under two 1990s players would read as "both
    contributed nothing" rather than "this season predates the data"."""
    ps_con.con.execute("DELETE FROM net_points_player")
    answer = player_compare(ps_con, {"players": ["Luka Doncic", "Nikola Jokic"]}).answer or ""
    assert "net pts/100" not in answer
    assert "33.5" in answer  # the rest of the comparison is unaffected


def test_player_compare_survives_a_warehouse_with_no_netpoints_table(ps_con: TemplateContext) -> None:
    """NetPoints is a separate opt-in fetch, so the table may not exist at all.
    Unlike per-game NetPoints, which has nothing else to say, a comparison is
    complete without it - so this drops the section instead of falling through
    to an agent that has no better source."""
    ps_con.con.execute("DROP TABLE net_points_player")
    answer = player_compare(ps_con, {"players": ["Luka Doncic", "Nikola Jokic"]}).answer or ""
    assert "net pts/100" not in answer and "33.5" in answer


def test_a_named_stat_still_narrows_the_comparison(ps_con: TemplateContext) -> None:
    """ "Who scores more" gets scoring, not a wall."""
    answer = player_compare(ps_con, {"players": ["Luka Doncic", "Nikola Jokic"], "stat": "points"}).answer or ""
    assert "\npoints" in answer
    assert "\nrebounds" not in answer and "\nsteals" not in answer


def test_a_comparison_is_not_refused_before_netpoints_begins(ps_con: TemplateContext) -> None:
    """The NetPoints table is deliberately absent from player_compare's
    TEMPLATE_SOURCES: listing it would put a 2019 coverage floor on every
    comparison and refuse the 1994-2018 ones outright."""
    from association.query.templates.common import check_coverage

    assert check_coverage("player_compare", {"season": 2005, "season_type": 2}) is None


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
    answer = player_compare(ps_con, {"players": ["Curry", "Nikola Jokic"]}).answer or ""
    assert "did you mean Seth Curry or Stephen Curry?" in answer


def _source_a_template_reads_slots_in(handler: Any) -> str:
    """A template's source, plus the shared scoping steps it hands its slots
    to. Since step 3 a template on the player-games relation no longer reads
    ``venue`` or ``without`` itself: common.scoped_player and scoped_games read
    them, once, for every template that calls them - so "does this template
    read the slot" has to follow that call. It follows ONLY a call the template
    actually makes: one that neither reads a slot nor calls the step that does
    still fails, which is the drift these guards exist for."""
    import inspect

    from association.query.templates import common

    source = inspect.getsource(handler)
    # One level of the template's own named steps: game_log is split into
    # _game_log_team and _game_log_player to stay inside the complexity gate,
    # and it is the player half that hands the slots on.
    module = inspect.getmodule(handler)
    for step in sorted(set(re.findall(r"\b(_[a-z][a-z0-9_]*)\(", source))):
        if inspect.isfunction(getattr(module, step, None)):
            source += inspect.getsource(getattr(module, step))
    # The shared steps, and the shared steps THEY call: condition_player hands
    # its slots to scoped_games, which is where a condition template's
    # `situation` (and every other relation cell) is read. Two passes, since
    # the chain is two deep and a step already added is not added twice.
    added: set[str] = set()
    for _ in range(2):
        for shared in ("scoped_player", "scoped_games", "condition_player"):
            if f"{shared}(" in source and shared not in added:
                source += inspect.getsource(getattr(common, shared))
                added.add(shared)
    return source


def test_no_template_outside_player_intents_reads_a_player_slot() -> None:
    """PLAYER_INTENTS decides whether a name the question does not support is
    refused or ignored, so a template drifting into reading a player slot
    without being listed would answer about somebody the question never named.
    Read out of the source rather than trusted, the way TEMPLATE_SOURCES is
    checked against TEMPLATES."""

    from association.query.templates import TEMPLATES
    from association.query.templates.common import PLAYER_INTENTS

    for intent, handler in TEMPLATES.items():
        source = _source_a_template_reads_slots_in(handler)
        reads = 'slots.get("player' in source or 'slots["player' in source
        assert reads == (intent in PLAYER_INTENTS), f"{intent} reads a player slot: {reads}, listed: {intent in PLAYER_INTENTS}"


def test_player_compare_suggests_the_player_a_fabricated_name_meant(ps_con: TemplateContext) -> None:
    """The router answered "compare sga and embid" with 'Jemel Embiid' - the
    surname corrected, the given name invented - and every token has to match,
    so a name the warehouse holds was buried by one made-up word."""
    answer = player_compare(ps_con, {"players": ["Luka Doncic", "Jemel Jokic"]}).answer or ""
    assert answer == "No player found matching 'Jemel Jokic' - did you mean Nikola Jokic?"


def test_player_compare_answers_a_near_miss_rather_than_falling_through(ps_con: TemplateContext) -> None:
    """Handled here for the same reason ambiguity is: the agent would resolve
    the same name against the same table, more slowly."""
    answer = player_compare(ps_con, {"players": ["Luka Doncic", "Nikoal Jokic"]}).answer or ""
    assert "did you mean Nikola Jokic?" in answer


def test_a_name_with_nothing_near_it_still_falls_through(ps_con: TemplateContext) -> None:
    with pytest.raises(TemplateUnsupported):
        player_compare(ps_con, {"players": ["Luka Doncic", "Asdf Qwerty"]})


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
    from association.query.templates.players import MAX_COMPARED_PLAYERS

    ps_con.con.execute("INSERT INTO players VALUES ('9','A A'),('10','B B'),('11','C C'),('12','D D')")
    result = player_compare(ps_con, {"players": ["Luka Doncic", "Nikola Jokic", "A A", "B B", "C C", "D D"]})
    assert len(result.data["players"]) <= MAX_COMPARED_PLAYERS


def test_shot_chart_reads_threes_from_either_slot(sc_ctx: TemplateContext) -> None:
    """ "Curry's threes" comes back as shot_value 3 or as the equivalent
    box-score stat depending on wording; both mean the same thing."""
    sc_ctx.con.execute("INSERT INTO shot_chart VALUES ('1',?,2,'e2',1,'9:00',TRUE,'Layup',5,5,2,'makes layup')", [current_season()])
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
    # `minutes` is last so the positional INSERTs below (and in the tests that
    # add a row of their own) keep their order. A NULL there is the empty line
    # ESPN leaves, which single_game_high must not read as a game played.
    c.execute(
        "CREATE TABLE player_game_log (athlete_id VARCHAR, season INTEGER, season_type INTEGER, player_name VARCHAR, "
        "game_date VARCHAR, opponent_abbr VARCHAR, assists INTEGER, points INTEGER, minutes INTEGER, event_id VARCHAR, did_not_play BOOLEAN DEFAULT FALSE)"
    )
    c.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    c.execute("INSERT INTO players VALUES ('1','Ryan Nembhard'),('2','Nikola Jokic')")
    # Read only to count empty box scores; none here.
    c.execute("CREATE TABLE player_box_stats (event_id VARCHAR, season INTEGER, season_type INTEGER, athlete_id VARCHAR, minutes INTEGER, did_not_play BOOLEAN)")
    s = current_season()
    c.executemany(
        "INSERT INTO player_game_log VALUES (?,?,?,?,?,?,?,?,?,?,FALSE)",
        [
            ("1", s, 2, "Ryan Nembhard", "2026-04-13T00:30Z", "CHI", 23, 8, 30, "e1"),
            ("2", s, 2, "Nikola Jokic", "2026-03-26T02:00Z", "DAL", 19, 30, 34, "e2"),
            ("2", s, 2, "Nikola Jokic", "2026-01-02T02:00Z", "UTA", 11, 40, 36, "e3"),
            ("1", s, 3, "Ryan Nembhard", "2026-05-01T00:30Z", "BOS", 30, 5, 31, "e4"),
            ("2", s - 1, 2, "Nikola Jokic", "2025-03-26T02:00Z", "DAL", 25, 30, 33, "e5"),
        ],
    )
    c.execute("CREATE VIEW games AS SELECT DISTINCT event_id, season, season_type FROM player_game_log")
    return TemplateContext(con=c, out_dir=tmp_path)


def test_single_game_high_answers_the_question_a_leaderboard_answered_wrongly(sgh_ctx: TemplateContext) -> None:
    """Confirmed live: with no such intent, "who had the most assists in a
    single game" was answered "Nikola Jokic led the league in assists per game,
    at 10.7" in 1.76s. The real answer was Ryan Nembhard with 23."""
    answer = single_game_high(sgh_ctx, {"stat": "assists"}).answer or ""
    assert answer.startswith("Ryan Nembhard had the most assists in a single game")
    # Stored as 2026-04-13T00:30Z: 8:30pm Eastern on the 12th, the day it was played.
    assert "23" in answer and "2026-04-12" in answer and "CHI" in answer


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
    assert answer == f"Nikola Jokic's highest assist total in a single game in the {current_season()} regular season was 19, on 2026-03-25 vs DAL."


def test_single_game_high_reports_a_tie_as_a_tie(sgh_ctx: TemplateContext) -> None:
    sgh_ctx.con.execute("UPDATE player_game_log SET assists = 23 WHERE player_name = 'Nikola Jokic' AND opponent_abbr = 'DAL' AND season_type = 2")
    assert "tied for the most" in (single_game_high(sgh_ctx, {"stat": "assists"}).answer or "")


def test_single_game_high_unknown_stat_falls_through(sgh_ctx: TemplateContext) -> None:
    with pytest.raises(TemplateUnsupported):
        single_game_high(sgh_ctx, {"stat": "assists); DROP TABLE players; --"})


def test_single_game_high_ambiguous_player_asks(sgh_ctx: TemplateContext) -> None:
    # Jovic needs a game this season to be a candidate at all. A name is
    # narrowed to the players with a row where the answer is read from, and
    # with Jokic alone in this season's log, "Nikola" is answered about him.
    sgh_ctx.con.execute("INSERT INTO players VALUES ('3','Nikola Jovic')")
    sgh_ctx.con.execute("INSERT INTO player_game_log VALUES ('3',?,2,'Nikola Jovic','2026-02-01T00:30Z','BOS',4,12,26,'e9',FALSE)", [current_season()])
    assert "did you mean" in (single_game_high(sgh_ctx, {"stat": "assists", "player": "Nikola"}).answer or "")


@pytest.fixture
def rebuilt_ctx(tmp_path: Path) -> TemplateContext:
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
        "CREATE TABLE player_game_log (athlete_id VARCHAR, season INTEGER, season_type INTEGER, player_name VARCHAR, "
        "game_date VARCHAR, opponent_abbr VARCHAR, points INTEGER, fouls INTEGER, minutes INTEGER, reconstructed BOOLEAN, "
        "event_id VARCHAR, did_not_play BOOLEAN)"
    )
    c.execute("CREATE TABLE player_box_stats (event_id VARCHAR, season INTEGER, season_type INTEGER, athlete_id VARCHAR, minutes INTEGER, did_not_play BOOLEAN)")
    s = current_season()
    c.executemany(
        "INSERT INTO player_game_log VALUES ('1',?,2,'Anthony Davis',?,?,?,?,?,?,?,FALSE)",
        [
            # Fetched: real box scores, real minutes.
            (s, "2026-01-02T00:30Z", "ORL", 24, 3, 31, False, "e1"),
            (s, "2026-01-05T00:30Z", "DAL", 18, 5, 28, False, "e2"),
            # Rebuilt: no box score, figures from the plays, no minutes.
            (s, "2026-01-08T00:30Z", "MIA", 43, 1, None, True, "e3"),
            (s, "2026-01-11T00:30Z", "PHX", 12, 6, None, True, "e4"),
        ],
    )
    c.execute("CREATE VIEW games AS SELECT DISTINCT event_id, season, season_type FROM player_game_log")
    return TemplateContext(con=c, out_dir=tmp_path)


def test_a_rebuilt_game_can_win_a_single_game_high(rebuilt_ctx: TemplateContext) -> None:
    """The whole point of the rebuild: before it, a season ESPN served empty had
    no per-game answer at all. 43 beats every fetched game here."""
    answer = single_game_high(rebuilt_ctx, {"stat": "points", "player": "Anthony Davis"}).answer or ""
    assert "43" in answer


def test_a_rebuilt_answer_says_it_was_rebuilt(rebuilt_ctx: TemplateContext) -> None:
    """A figure that did not come from ESPN has to say so, or it reads as a box
    score. This is the disclosure the whole opt-in is conditional on."""
    answer = single_game_high(rebuilt_ctx, {"stat": "points", "player": "Anthony Davis"}).answer or ""
    assert "rebuilt from its play-by-play" in answer


def test_a_fetched_winner_says_nothing_about_rebuilding(rebuilt_ctx: TemplateContext) -> None:
    """The note is about the number given, not about what was read. Drop the
    rebuilt games below the fetched ones and the answer is an ordinary one."""
    rebuilt_ctx.con.execute("UPDATE player_game_log SET points = 5 WHERE reconstructed")
    answer = single_game_high(rebuilt_ctx, {"stat": "points", "player": "Anthony Davis"}).answer or ""
    assert "24" in answer
    assert "rebuilt" not in answer


def test_fouls_are_never_read_from_a_rebuilt_line(rebuilt_ctx: TemplateContext) -> None:
    """A rebuilt foul is wrong in one game in six (83.3% exact against 98.3% for
    points), so fouls are outside REBUILT_STATS. The rebuilt 6 must lose to the
    fetched 5 rather than win the answer."""
    answer = single_game_high(rebuilt_ctx, {"stat": "fouls", "player": "Anthony Davis"}).answer or ""
    assert "5" in answer
    assert "rebuilt from its play-by-play" not in answer


def test_a_withheld_stat_says_it_was_withheld_not_missing(rebuilt_ctx: TemplateContext) -> None:
    """When every fetched line is gone and only rebuilt ones remain, asking for
    a stat outside REBUILT_STATS must name the decision. "No games with a box
    score" is true of the fetched lines and hides that the data exists and was
    held back for being too inaccurate to quote."""
    rebuilt_ctx.con.execute("DELETE FROM player_game_log WHERE NOT reconstructed")
    answer = single_game_high(rebuilt_ctx, {"stat": "fouls", "player": "Anthony Davis"}).answer or ""
    assert "were rebuilt from play-by-play" in answer
    assert "not read from a rebuilt line" in answer


def test_the_unseen_count_excludes_games_the_rebuild_answered(rebuilt_ctx: TemplateContext) -> None:
    """The contradiction this fixes: the answer used to give a rebuilt figure
    and then report the same games as ones it could not see."""
    # An empty box row for a game the rebuild DID answer. Counting from
    # player_box_stats, as the unfixed branch does, makes this one "unseen";
    # counting from the log, which knows it was rebuilt, makes it nothing.
    rebuilt_ctx.con.execute("INSERT INTO player_box_stats VALUES ('e3', ?, 2, '1', NULL, FALSE)", [current_season()])
    answer = single_game_high(rebuilt_ctx, {"stat": "points", "player": "Anthony Davis"}).answer or ""
    assert "43" in answer
    assert "rebuilt from its play-by-play" in answer
    # The discriminating assertion: the unfixed branch appends "1 of Anthony
    # Davis's games ... has an empty box score ...", disclaiming the very game
    # the 43 came from. With the fix there is nothing left to disclaim.
    assert "empty box score" not in answer


def test_a_game_log_reads_rebuilt_lines_only_for_stats_a_rebuild_gets_right(rebuilt_ctx: TemplateContext) -> None:
    """The gate `game_log` applies before showing a rebuilt row. A table has no
    room to caveat one column, so a single untrustworthy column sends the whole
    log back to fetched lines. Asserted directly on the rule, because inline in
    the query it could be relaxed with nothing noticing."""
    con = rebuilt_ctx.con
    assert _rebuilt_readable(con, ["MIN", "PTS", "REB", "AST"]) is True
    assert _rebuilt_readable(con, ["MIN", "PTS", "STL", "BLK"]) is True
    # 0.181 mean error a game, wrong in one game in six.
    assert _rebuilt_readable(con, ["MIN", "PTS", "PF"]) is False
    # 0.080, wrong in one game in thirteen.
    assert _rebuilt_readable(con, ["MIN", "PTS", "TO"]) is False


def test_a_warehouse_without_the_flag_still_answers(rebuilt_ctx: TemplateContext) -> None:
    """The column arrives with a `data load`. An older warehouse has no such
    column, and the query must not be written as though it were always there -
    that is the Binder error AGENTS.md records for view changes."""
    rebuilt_ctx.con.execute("ALTER TABLE player_game_log DROP COLUMN reconstructed")
    answer = single_game_high(rebuilt_ctx, {"stat": "points", "player": "Anthony Davis"}).answer or ""
    assert "24" in answer  # the best line that has minutes
    assert "rebuilt" not in answer


def test_single_game_high_reports_an_empty_season_honestly(sgh_ctx: TemplateContext) -> None:
    assert "no 1999 regular season games" in (single_game_high(sgh_ctx, {"stat": "assists", "season": 1999}).answer or "")


def test_single_game_high_defaulted_season_redirects_to_a_retired_players_range(sgh_ctx: TemplateContext) -> None:
    """No season was named - the question asked about "now" - so a player
    with games only long before it gets pointed at his own range rather than a
    refusal that reads as though he never played at all (issue #18)."""
    s, past = current_season(), current_season() - 16
    sgh_ctx.con.execute("INSERT INTO players VALUES ('3','Old Timer')")
    sgh_ctx.con.execute("INSERT INTO player_game_log VALUES ('3',?,2,'Old Timer','2010-04-12T00:30Z','BOS',5,20,32,'e8',FALSE)", [past])
    answer = single_game_high(sgh_ctx, {"stat": "points", "player": "Old Timer"}).answer or ""
    assert answer == (f"Old Timer has no {s} regular season games in the warehouse. He last appears in {past}. The warehouse holds his {past} regular season; name one, or ask for his career.")


def test_single_game_high_a_named_season_keeps_the_plain_refusal(sgh_ctx: TemplateContext) -> None:
    """The season the question named is the fact the refusal is about; a
    redirect there would answer a season nobody asked about."""
    past = current_season() - 16
    sgh_ctx.con.execute("INSERT INTO players VALUES ('3','Old Timer')")
    sgh_ctx.con.execute("INSERT INTO player_game_log VALUES ('3',?,2,'Old Timer','2010-04-12T00:30Z','BOS',5,20,32,'e8',FALSE)", [past])
    answer = single_game_high(sgh_ctx, {"stat": "points", "player": "Old Timer", "season": 1999}).answer or ""
    assert answer == "Old Timer has no 1999 regular season games in the warehouse."


def _all_box_scores_empty(ctx: TemplateContext, name: str) -> None:
    """One player whose whole season is the empty line ESPN leaves: listed as
    having played, no minutes, every stat 0. Anthony Davis's 2015 in miniature."""
    s = current_season()
    ctx.con.execute("INSERT INTO players VALUES ('9',?)", [name])
    ctx.con.executemany(
        "INSERT INTO player_game_log VALUES ('9',?,2,?,?,'ORL',0,0,NULL,?,FALSE)",
        [(s, name, "2026-01-05T00:30Z", "x1"), (s, name, "2026-01-08T00:30Z", "x2")],
    )
    ctx.con.executemany("INSERT INTO player_box_stats VALUES (?,?,2,'9',NULL,FALSE)", [("x1", s), ("x2", s)])


def test_a_zero_from_an_empty_box_score_never_wins_a_single_game_high(sgh_ctx: TemplateContext) -> None:
    """The P1 this fixes. Every Chicago and New Orleans box score from 2013 to
    2018 is zeros, so before the guard the maximum over them WAS one of those
    zeros, reported with a date: "Anthony Davis's highest point total in a
    single game in the 2015 regular season was 0, on 2014-10-28 vs ORL."
    Fluent, specific and false."""
    _all_box_scores_empty(sgh_ctx, "Zion Williamson")
    answer = single_game_high(sgh_ctx, {"stat": "points", "player": "Zion Williamson"}).answer or ""
    assert "was 0" not in answer
    assert f"no {current_season()} regular season games with a box score" in answer


def test_an_empty_box_score_season_says_which_fact_is_missing(sgh_ctx: TemplateContext) -> None:
    """The mirror-image bug the guard could have introduced: "he has no games"
    is false of a player who played them, and sends the reader to look for a
    missing season rather than a missing box score. The count and the years
    have to be in the sentence."""
    _all_box_scores_empty(sgh_ctx, "Zion Williamson")
    answer = single_game_high(sgh_ctx, {"stat": "points", "player": "Zion Williamson"}).answer or ""
    # The bare sentence must NOT appear. This is the assertion that carries the
    # test: the count and the years below come from _empty_note, which runs
    # either way, so asserting only those passed even with this branch blinded.
    assert f"no {current_season()} regular season games in the warehouse" not in answer
    assert f"no {current_season()} regular season games with a box score in the warehouse" in answer
    assert "2 of Zion Williamson's games" in answer
    # Said of a season with no games at all, the plain sentence is still right.
    plain = single_game_high(sgh_ctx, {"stat": "assists", "season": 1999}).answer or ""
    assert "no 1999 regular season games in the warehouse" in plain
    assert "with a box score" not in plain


# ---------------- head_to_head ----------------


def test_head_to_head_counts_games_in_both_directions(gl_con: TemplateContext) -> None:
    """Regression: `games` is home/away-oriented, and the agent's version also
    compared team_id to an abbreviation, so it reported that two teams who met
    four times had never played."""
    result = head_to_head(gl_con, {"teams": ["Knicks", "Celtics"], "season": current_season()})
    assert result.data["games"] == 2  # one home, one away


def test_head_to_head_counts_only_rows_that_are_games(gl_con: TemplateContext) -> None:
    """Regression: it counted every row of `games`, so "how many times did the
    Mavs play the 76ers in 2003" answered 3 for a season holding 2 - one game
    stored under two event ids - and 1999-2000 matchups counted 0-0 meetings
    nobody won. Both shapes are in the fixture; read from `games` this is 4."""
    assert head_to_head(gl_con, {"teams": ["Knicks", "Celtics"], "season": current_season()}).data["games"] == 2


def test_a_team_log_leaves_out_rows_that_are_not_games(gl_con: TemplateContext) -> None:
    """The same two rows, through the team game log. This was believed to be
    safe because the log joins team_box_stats - but EVERY `games` row has a
    team_box_stats row, so the join filtered nothing and a 1999 or 2000 Bulls
    log listed placeholders as games."""
    games = game_log(gl_con, {"team": "Knicks"}).data["games"]
    assert len(games) == 2
    assert all(g["opponent_score"] is not None and g["won"] is not None for g in games)


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
    gl_con.con.execute("INSERT INTO games VALUES ('e9',?,2,'2020-01-01T00:00Z','18','2',100,90,'18',false,'New York')", [current_season() - 3])
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


def test_head_to_head_narrows_to_the_first_named_teams_home_games(gl_con: TemplateContext) -> None:
    """ "lakers vs mavs record last 10 home games played" - `venue` used to be
    refused outright. The fixture's two meetings split one home, one away for
    the Knicks (named first); "home" keeps only their home game."""
    result = head_to_head(gl_con, {"teams": ["Knicks", "Celtics"], "season": current_season(), "venue": "home"})
    assert result.data["games"] == 1
    assert "New York Knicks" in (result.answer or "") and "home games" in (result.answer or "")


def test_head_to_head_venue_possessive_drops_the_extra_s(gl_con: TemplateContext) -> None:
    """ "New York Knicks's" reads as a typo - most team names already end in
    "s" (Celtics, Warriors, Nets...), so the possessive is just an apostrophe."""
    answer = head_to_head(gl_con, {"teams": ["Knicks", "Celtics"], "season": current_season(), "venue": "home"}).answer or ""
    assert "New York Knicks' home games" in answer
    assert "Knicks's" not in answer


def test_head_to_head_narrows_to_the_first_named_teams_road_games(gl_con: TemplateContext) -> None:
    result = head_to_head(gl_con, {"teams": ["Knicks", "Celtics"], "season": current_season(), "venue": "away"})
    games = result.data["games"]
    assert games == 1
    # The Knicks' one road game in the fixture (2026-04-12) was a loss.
    assert result.data["wins"]["New York Knicks"] == 0


def test_head_to_head_venue_is_said_in_the_answer_not_silently_applied(gl_con: TemplateContext) -> None:
    """Honoring a scoping slot means filtering by it AND saying so - a table
    with fewer rows and no note reads exactly like the whole-season answer."""
    answer = head_to_head(gl_con, {"teams": ["Knicks", "Celtics"], "season": current_season(), "venue": "home"}).answer or ""
    assert "home games" in answer


def test_head_to_head_filters_an_exact_calendar_date(gl_con: TemplateContext) -> None:
    """ "celtics record vs sixers on november 11" - a date names its game
    outright, the same way it does for game_log."""
    result = head_to_head(gl_con, {"teams": ["Knicks", "Celtics"], "date": "2026-04-12"})
    assert result.data["games"] == 1
    assert "on 2026-04-12" in (result.answer or "")


def test_head_to_head_date_is_not_scoped_to_a_named_season(gl_con: TemplateContext) -> None:
    """A date pins one exact game, so - like game_log's own `date` - it is not
    additionally filtered to "the current season": a season from a year the
    game was not actually played in must not hide it."""
    result = head_to_head(gl_con, {"teams": ["Knicks", "Celtics"], "date": "2026-04-12", "season": 1999})
    assert result.data["games"] == 1


def test_head_to_head_no_game_on_that_date_is_reported_honestly(gl_con: TemplateContext) -> None:
    result = head_to_head(gl_con, {"teams": ["Knicks", "Celtics"], "date": "2026-04-11"})
    assert result.data["games"] == 0
    assert "no games" in (result.answer or "").lower()
    assert "on 2026-04-11" in (result.answer or "")


def test_head_to_head_plain_wording_is_unchanged_by_the_venue_and_date_additions(gl_con: TemplateContext) -> None:
    """The existing (no venue, no date) sentence must read exactly as it did
    before - a regression on wording nobody asked to change."""
    answer = head_to_head(gl_con, {"teams": ["Knicks", "Celtics"], "season": current_season()}).answer or ""
    assert answer == f"The New York Knicks and the Boston Celtics met 2 times in the {current_season()} regular season, splitting them 1-1."


# ---------------- team_record / head_to_head: since, game_n, career (step 3, team cells) ----------------

_TC_S = current_season()
_TC_S1 = _TC_S - 1
_TC_S2 = _TC_S - 2


@pytest.fixture
def team_cells_con(tmp_path: Path) -> TemplateContext:
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
    return TemplateContext(con=c, out_dir=tmp_path)


def test_team_record_honors_since_as_a_since_bounded_span(team_cells_con: TemplateContext) -> None:
    """ "Celtics record since {S-1}" (ISSUES.md): a since-bounded span is
    counted the same shape a whole career already is, not one season."""
    result = team_record(team_cells_con, {"team": "Celtics", "since": _TC_S1})
    assert result.data["wins"] == 3 and result.data["losses"] == 0
    assert result.answer == f"The Boston Celtics are 3-0 (1.000) in the regular seasons since {_TC_S1}.\n  Home 2-0, away 1-0."


def test_team_record_since_narrows_to_a_named_opponent(team_cells_con: TemplateContext) -> None:
    """ "Celtics record vs Knicks since {S-1}" - `since` and `opponent` compose,
    the same way `opponent` already composes with a single season."""
    result = team_record(team_cells_con, {"team": "Celtics", "opponent": "Knicks", "since": _TC_S1})
    assert result.data["wins"] == 2 and result.data["losses"] == 0
    assert result.answer == f"The Boston Celtics are 2-0 (1.000) against the New York Knicks in the regular seasons since {_TC_S1}.\n  Home 1-0, away 1-0."


def test_team_record_since_narrows_by_venue(team_cells_con: TemplateContext) -> None:
    result = team_record(team_cells_con, {"team": "Celtics", "since": _TC_S1, "venue": "home"})
    assert result.data["wins"] == 2 and result.data["losses"] == 0
    assert result.answer == f"The Boston Celtics are 2-0 (1.000) at home in the regular seasons since {_TC_S1}."


def test_team_record_since_with_no_games_names_the_since_bound(team_cells_con: TemplateContext) -> None:
    """The empty-result sentence says which narrowing emptied it - the same
    false-cause discipline AGENTS.md requires everywhere else - rather than
    reading as no games on record at all."""
    result = team_record(team_cells_con, {"team": "Pistons", "since": _TC_S + 10})
    assert result.answer == f"The warehouse holds no regular-season games for the Detroit Pistons since {_TC_S + 10}."


def test_team_record_honors_game_n_within_one_named_postseason(team_cells_con: TemplateContext) -> None:
    """ "Celtics record in game 1 of the {S} playoffs" - one game of each
    series the relation's own `narrow_series_game` already numbers."""
    result = team_record(team_cells_con, {"team": "Celtics", "season_type": 3, "season": _TC_S, "game_n": 1})
    assert result.data["wins"] == 1 and result.data["losses"] == 0
    assert result.answer == f"The Boston Celtics went 1-0 (1.000) in game 1 of each series in the {_TC_S} postseason.\n  Home 1-0, away 0-0."


def test_team_record_game_n_combines_with_since_across_postseasons(team_cells_con: TemplateContext) -> None:
    """ "Celtics record in game 1 of each series since 1989" - the warehouse's
    own playoff-game-list floor (AGENTS.md, "Coverage floors"), reaching the
    1989 postseason the same way `head_to_head`'s own since-bounded postseason
    test below does."""
    result = team_record(team_cells_con, {"team": "Celtics", "season_type": 3, "since": 1989, "game_n": 1})
    assert result.data["wins"] == 2 and result.data["losses"] == 0
    assert result.answer == "The Boston Celtics are 2-0 (1.000) in game 1 of each series in the postseasons since 1989.\n  Home 2-0, away 0-0."


def test_team_record_game_n_refuses_a_regular_season(team_cells_con: TemplateContext) -> None:
    """A series has games 1-7; a regular season has nothing "game 4" names -
    the same check `common.team_games` makes for every other team template."""
    with pytest.raises(TemplateUnsupported):
        team_record(team_cells_con, {"team": "Celtics", "game_n": 1})


def test_team_record_since_conflicts_with_a_named_season(team_cells_con: TemplateContext) -> None:
    with pytest.raises(TemplateUnsupported):
        team_record(team_cells_con, {"team": "Celtics", "since": _TC_S1, "season": _TC_S})


def test_team_record_since_conflicts_with_career(team_cells_con: TemplateContext) -> None:
    with pytest.raises(TemplateUnsupported):
        team_record(team_cells_con, {"team": "Celtics", "since": _TC_S1, "span": "career"})


def test_team_record_since_answers_a_month_split_with_one_table_per_season(team_cells_con: TemplateContext) -> None:
    """Step 3, K1 (F095, ISSUES.md - "Knicks record by month 2024 2025"): a
    month split with `since` set now reads one table PER SEASON in the span
    rather than refusing outright. Every Celtics game in this fixture is in
    November: {S-1} holds one (r3, a win) and {S} holds two (r4, r5, both
    wins) - two separate tables, not a single row summing three."""
    result = team_record(team_cells_con, {"team": "Celtics", "since": _TC_S1, "split": "month"})
    assert result.data["months"] == [
        {"season": _TC_S1, "month": "November", "games": 1, "wins": 1, "losses": 0},
        {"season": _TC_S, "month": "November", "games": 2, "wins": 2, "losses": 0},
    ]
    assert result.answer.count("The Boston Celtics, record by month") == 2


def test_team_record_since_month_split_refuses_game_n(team_cells_con: TemplateContext) -> None:
    """`game_n` still has no month-split form - the one combination step 3, K1
    leaves refused, since a series-game number and a whole-season table of
    months answer two different shapes of question."""
    with pytest.raises(TemplateUnsupported):
        team_record(team_cells_con, {"team": "Celtics", "since": _TC_S1, "split": "month", "game_n": 1})


def test_head_to_head_honors_since_over_every_meeting_in_the_span(team_cells_con: TemplateContext) -> None:
    """ "Celtics vs Knicks since {S-1}" (ISSUES.md): every meeting in the span,
    not one season."""
    result = head_to_head(team_cells_con, {"teams": ["Celtics", "Knicks"], "since": _TC_S1})
    assert result.data["games"] == 2 and result.data["wins"] == {"Boston Celtics": 2, "New York Knicks": 0}
    assert result.answer == f"The Boston Celtics and the New York Knicks have met 2 times since {_TC_S1} ({_TC_S1}-{_TC_S} regular seasons); the Boston Celtics lead the all-time series 2-0."


def test_head_to_head_honors_until_bounding_the_since_span(team_cells_con: TemplateContext) -> None:
    """Step 3, K1: "Celtics vs Knicks from {S-2} to {S-1}" reads a BOUNDED
    range - r1 ({S-2}) and r3 ({S-1}) count, r4 ({S}) does not, unlike the
    open-ended since-only test above, which also picks up r4."""
    result = head_to_head(team_cells_con, {"teams": ["Celtics", "Knicks"], "since": _TC_S2, "until": _TC_S1})
    assert result.data["games"] == 2 and result.data["wins"] == {"Boston Celtics": 2, "New York Knicks": 0}
    assert f"from {_TC_S2} through {_TC_S1}" in (result.answer or "")


def test_head_to_head_until_with_no_since_is_refused(team_cells_con: TemplateContext) -> None:
    with pytest.raises(TemplateUnsupported, match="until"):
        head_to_head(team_cells_con, {"teams": ["Celtics", "Knicks"], "until": _TC_S1})


def test_head_to_head_honors_career_over_every_meeting_on_record(team_cells_con: TemplateContext) -> None:
    """ "All-time Celtics vs Knicks" (ISSUES.md) - `span` "career", the same
    shape team_record's own whole-career answer already reads."""
    result = head_to_head(team_cells_con, {"teams": ["Celtics", "Knicks"], "span": "career"})
    assert result.data["games"] == 3 and result.data["wins"] == {"Boston Celtics": 3, "New York Knicks": 0}
    assert (
        result.answer == f"The Boston Celtics and the New York Knicks have met 3 times over the seasons on record ({_TC_S2}-{_TC_S} regular seasons); the Boston Celtics lead the all-time series 3-0."
    )


def test_head_to_head_since_reaches_the_1989_postseason_floor(team_cells_con: TemplateContext) -> None:
    """The team tables reach 1988-89 for the postseason (AGENTS.md, "Coverage
    floors") - "Celtics vs Pistons since 1989" reads the one series the
    warehouse holds that far back."""
    result = head_to_head(team_cells_con, {"teams": ["Celtics", "Pistons"], "season_type": 3, "since": 1989})
    assert result.data["games"] == 3 and result.data["wins"] == {"Boston Celtics": 2, "Detroit Pistons": 1}
    assert result.answer == "The Boston Celtics and the Detroit Pistons have met 3 times since 1989 (1989 postseason); the Boston Celtics lead the all-time series 2-1."


def test_head_to_head_since_with_no_meetings_names_the_since_bound(team_cells_con: TemplateContext) -> None:
    result = head_to_head(team_cells_con, {"teams": ["Celtics", "Pistons"], "since": _TC_S + 10})
    assert result.answer == f"The Boston Celtics and the Detroit Pistons have not played each other since {_TC_S + 10}."


def test_head_to_head_since_narrows_by_venue(team_cells_con: TemplateContext) -> None:
    """`since` composes with `venue` the same way a single season already
    does - narrowed to the first-named team's home games."""
    result = head_to_head(team_cells_con, {"teams": ["Celtics", "Knicks"], "since": _TC_S1, "venue": "home"})
    assert result.data["games"] == 1 and result.data["wins"] == {"Boston Celtics": 1, "New York Knicks": 0}
    assert result.answer == f"The Boston Celtics and the New York Knicks met once in the Boston Celtics' home games since {_TC_S1}; the Boston Celtics won the series 1-0."


def test_head_to_head_since_conflicts_with_a_date(team_cells_con: TemplateContext) -> None:
    with pytest.raises(TemplateUnsupported):
        head_to_head(team_cells_con, {"teams": ["Celtics", "Knicks"], "since": _TC_S1, "date": "2026-01-01"})


def test_head_to_head_since_conflicts_with_career(team_cells_con: TemplateContext) -> None:
    with pytest.raises(TemplateUnsupported):
        head_to_head(team_cells_con, {"teams": ["Celtics", "Knicks"], "since": _TC_S1, "span": "career"})


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


def test_team_quarter_points_reads_a_bare_limit_as_the_newest_games(tq_con: TemplateContext) -> None:
    """The router drops `order` and keeps `limit` on "last N games" phrasings
    (measured on the shot templates, four runs, three builds), so the team
    relation reads a bare limit the way the player relation does - through
    the one `_relation_window` rule - rather than answering the whole season."""
    result = team_quarter_points(tq_con, {"team": "Knicks", "period": 1, "season": current_season(), "limit": 2})
    assert len(result.data["games"]) == 2 and "last 2 games" in (result.answer or "")


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


def test_team_quarter_points_refuses_a_stat_the_linescore_does_not_hold(tq_con: TemplateContext) -> None:
    """A linescore holds one number per period - the score - so a question
    asking for a team's three-point or rebounding average by quarter is a
    different question, and answering it with POINTS is the fluent wrong
    answer this project keeps producing. It was invisible while "trailblazers
    stats last 10 games 3 point average 1st quarter" was refused for naming no
    team at all; restoring the team is what exposed it."""
    for stat in ("threePointFieldGoalsMade", "rebounds", "assists"):
        with pytest.raises(TemplateUnsupported, match="linescore"):
            team_quarter_points(tq_con, {"team": "Knicks", "period": 1, "season": current_season(), "stat": stat})
    # Points is the one it does hold, under either spelling, and no stat at
    # all still means the score.
    allowed: tuple[str | None, ...] = ("points", "avg_points", None)
    for held in allowed:
        result = team_quarter_points(tq_con, {"team": "Knicks", "period": 1, "season": current_season(), "stat": held})
        assert result.data["total"] == 60


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
    monkeypatch.setattr("association.query.templates.games._QUARTER_BREAKDOWN_LIMIT", 1)
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
    ps_con.con.execute("ALTER TABLE player_season_stats_deduped ADD COLUMN threePointFieldGoalsAttempted INTEGER")
    ps_con.con.execute("UPDATE player_season_stats_deduped SET avgThreePointFieldGoalsMade = 4.4, threePointFieldGoalsMade = 282, threePointFieldGoalsAttempted = 620 WHERE athlete_id = '1'")
    answer = player_stat(ps_con, {"player": "Luka Doncic", "stat": "threePointFieldGoalsMade"}).answer or ""
    # F051 (ISSUES.md): a made-count stat now carries its attempts and the
    # percentage they make beside the total - "282 of 620 (45.5%)" - the same
    # "out of how many?" discipline a bare shooting percentage already keeps.
    assert "4.4 3-pointers" in answer and "282 of 620 (45.5%)" in answer


def test_player_stat_answers_two_point_percentage_for_a_season_and_a_career(ps_con: TemplateContext) -> None:
    """ISSUES.md #114: no stored 2-point percentage column exists in either
    table (checked against the warehouse), so SHOOTING_STATS' entry for it is
    an expression - makes and attempts less the threes - rather than a bare
    column name, unlike its two siblings. Proven here for both the season
    lookup and the career sum, which read the season table two different ways
    (`_season_row`'s plain SELECT and `_career_player_stat`'s per-season SUM)."""
    for col in ("fieldGoalsMade", "fieldGoalsAttempted", "threePointFieldGoalsMade", "threePointFieldGoalsAttempted"):
        ps_con.con.execute(f"ALTER TABLE player_season_stats_deduped ADD COLUMN {col} INTEGER")
    ps_con.con.execute("UPDATE player_season_stats_deduped SET fieldGoalsMade=700, fieldGoalsAttempted=1300, threePointFieldGoalsMade=200, threePointFieldGoalsAttempted=500 WHERE athlete_id='1'")
    season = player_stat(ps_con, {"player": "Luka Doncic", "stat": "twoPointFieldGoalPct"}).answer
    assert season == f"Luka Doncic shot 62.5% on 2-pointers (500 of 800) in 64 games in the {current_season()} regular season."

    # A second season, so the career sum is a total over games, not an
    # average of the two seasons' percentages - (500+200)/(800+400) = 58.3%,
    # not the mean of 62.5% and 50.0%.
    ps_con.con.execute(
        "INSERT INTO player_season_stats_deduped (athlete_id, season, season_type, gamesPlayed, avgPoints, "
        "fieldGoalsMade, fieldGoalsAttempted, threePointFieldGoalsMade, threePointFieldGoalsAttempted) "
        "VALUES ('1', ?, 2, 50, 20.0, 300, 600, 100, 200)",
        [current_season() - 1],
    )
    career = player_stat(ps_con, {"player": "Luka Doncic", "stat": "twoPointFieldGoalPct", "span": "career"}).answer or ""
    assert "58.3% on 2-pointers (700 of 1,200)" in career


def test_player_stat_answers_two_point_percentage_narrowed_to_box_scores(pg_ctx: TemplateContext) -> None:
    """The box-score-narrowed path joins `games` and qualifies every shooting
    column with the `pgl.` alias - blindly prepending it to this stat's
    expression would read "pgl.(fieldGoalsMade - threePointFieldGoalsMade)",
    which is not valid SQL. Podziemski's two games vs Detroit (e2: 10-for-20,
    e3: 7-for-15, both all twos in this fixture) sum to 17 of 35 = 48.6%."""
    answer = player_stat(pg_ctx, {"player": "Brandin Podziemski", "stat": "twoPointFieldGoalPct", "opponent": "Detroit Pistons"}).answer or ""
    assert "48.6% on 2-pointers (17 of 35) in 2 games vs the Detroit Pistons" in answer


# ---------------- shot_distance ----------------


def test_shot_distance_filters_to_the_shot_value_asked_for(sc_ctx: TemplateContext) -> None:
    """Confirmed live: the agent wrote the right distance formula, then dropped
    both the 3-point filter and the season filter and reported an all-shots,
    all-seasons average of 16.94 as a current-season three-point distance."""
    sc_ctx.con.execute("INSERT INTO shot_chart VALUES ('1',?,2,'e5',1,'1:00',TRUE,'Layup',25,1,2,'makes layup')", [current_season()])
    threes = shot_distance(sc_ctx, {"player": "Stephen Curry", "shot_value": 3})
    everything = shot_distance(sc_ctx, {"player": "Stephen Curry"})
    assert threes.data["attempts"] == 2 and everything.data["attempts"] == 3
    assert threes.data["avg_feet"] > everything.data["avg_feet"]


def test_shot_distance_measures_from_the_rim(sc_ctx: TemplateContext) -> None:
    """The rim is at (25, 0) in ESPN's coordinates - y is measured from it, not
    from the baseline - so the fixture's shot at (25, 26) is 26 feet out. The
    old frame put the rim at (25, 5.25) and made it 20.75, which is how Stephen
    Curry's 2026 threes came out at 23.6 feet, inside the line."""
    result = shot_distance(sc_ctx, {"player": "Stephen Curry", "shot_value": 3})
    assert result.data["avg_feet"] == pytest.approx(26.0)


def test_shot_distance_counts_threes_espn_left_unlabeled(sc_ctx: TemplateContext) -> None:
    """``points_attempted`` is 0 for an unlabeled shot, not a zero-point one,
    and 96% of 2022 is unlabeled: filtered on it, "Curry's threes in 2022"
    averaged 38 of his 751 attempts, every one a miss. The description names
    one of these as a three and the position names the other."""
    sc_ctx.con.executemany(
        "INSERT INTO shot_chart VALUES ('1',?,2,'e7',1,'2:00',?,'Jump Shot',?,?,0,?)",
        [(current_season(), True, 25, 25, "makes 25-foot three point jumper"), (current_season(), False, 2, 3, "misses 23-foot step back jumpshot")],
    )
    assert shot_distance(sc_ctx, {"player": "Stephen Curry", "shot_value": 3}).data["attempts"] == 4


def test_shot_distance_leaves_out_free_throws_that_carry_a_position(sc_ctx: TemplateContext) -> None:
    """From 2002 to 2018 every free throw has a position under the rim, so
    "has coordinates" does not exclude them - they averaged in as zero-foot
    shots."""
    sc_ctx.con.execute("INSERT INTO shot_chart VALUES ('1',?,2,'e1',1,'3:00',TRUE,'Free Throw - 1 of 2',25,0,1,'makes free throw 1 of 2')", [current_season()])
    result = shot_distance(sc_ctx, {"player": "Stephen Curry"})
    assert result.data["attempts"] == 2
    assert result.data["avg_feet"] == pytest.approx(26.0)


def test_shot_distance_leaves_out_shots_with_no_position(sc_ctx: TemplateContext) -> None:
    sc_ctx.con.execute("INSERT INTO shot_chart VALUES ('1',?,2,'e1',1,'4:00',TRUE,'Layup Shot',0,0,2,'makes layup')", [current_season()])
    assert shot_distance(sc_ctx, {"player": "Stephen Curry"}).data["attempts"] == 2


def test_shot_distance_refuses_threes_in_a_season_that_cannot_tell_them_apart(sc_ctx: TemplateContext) -> None:
    """2002 has no labels and descriptions that miss threes, so answering from
    what it has would be a narrower answer with nothing saying so. The refusal
    names that cause - not an absence of data, since the same season still
    answers for all of a player's shots."""
    sc_ctx.con.executemany(
        "INSERT INTO shot_chart VALUES ('1',2002,2,'e8',1,'5:00',TRUE,'Jump Shot',?,?,0,?)",
        [(25, 25, "made 25 ft Three Point Jumper."), (47, 0, "made Jumper.")],
    )
    refused = shot_distance(sc_ctx, {"player": "Stephen Curry", "season": 2002, "shot_value": 3})
    assert "avg_feet" not in refused.data
    assert (refused.answer or "").startswith(shotchart.UNSEPARABLE_SHOT_VALUES[2002])
    assert shot_distance(sc_ctx, {"player": "Stephen Curry", "season": 2002}).data["attempts"] == 2


def test_shot_distance_says_when_a_seasons_shot_values_are_derived(sc_ctx: TemplateContext) -> None:
    sc_ctx.con.execute("INSERT INTO shot_chart VALUES ('1',2022,2,'e9',1,'6:00',TRUE,'Jump Shot',25,26,0,'makes 26-foot three point jumper')")
    threes = shot_distance(sc_ctx, {"player": "Stephen Curry", "season": 2022, "shot_value": 3})
    assert threes.data["attempts"] == 1
    assert shotchart.DERIVED_SHOT_VALUES[2022] in (threes.answer or "")
    # A question about all shots does not rest on the derivation, so says nothing.
    assert "Note" not in (shot_distance(sc_ctx, {"player": "Stephen Curry", "season": 2022}).answer or "")


def test_shot_chart_draws_threes_espn_left_unlabeled(sc_ctx: TemplateContext) -> None:
    sc_ctx.con.execute("INSERT INTO shot_chart VALUES ('1',?,2,'e7',1,'2:00',TRUE,'Jump Shot',2,3,0,'makes 23-foot step back jumpshot')", [current_season()])
    result = shot_chart(sc_ctx, {"player": "Stephen Curry", "season": current_season(), "shot_value": 3})
    assert "(2/3 made" in (result.answer or "")


def test_shot_chart_refuses_threes_in_a_season_that_cannot_tell_them_apart(sc_ctx: TemplateContext) -> None:
    sc_ctx.con.execute("INSERT INTO shot_chart VALUES ('1',2002,2,'e8',1,'5:00',TRUE,'Jump Shot',25,25,0,'made 25 ft Three Point Jumper.')")
    result = shot_chart(sc_ctx, {"player": "Stephen Curry", "season": 2002, "shot_value": 3})
    assert (result.answer or "").startswith(shotchart.UNSEPARABLE_SHOT_VALUES[2002])
    assert not list(sc_ctx.out_dir.glob("*.html"))


def test_shot_chart_leaves_free_throws_off_the_court(sc_ctx: TemplateContext) -> None:
    """A 2002-2018 free throw has a position under the rim, and was drawn
    there as a shot."""
    sc_ctx.con.execute("INSERT INTO shot_chart VALUES ('1',?,2,'e1',1,'3:00',TRUE,'Free Throw - 1 of 2',25,0,1,'makes free throw 1 of 2')", [current_season()])
    assert "(1/2 made" in (shot_chart(sc_ctx, {"player": "Stephen Curry", "season": current_season()}).answer or "")


def test_a_free_throw_chart_is_refused_rather_than_drawn(sc_ctx: TemplateContext) -> None:
    result = shot_chart(sc_ctx, {"player": "Stephen Curry", "season": current_season(), "shot_value": 1})
    assert "no free-throw chart" in (result.answer or "")
    assert not list(sc_ctx.out_dir.glob("*.html"))


def test_a_career_chart_says_which_shots_it_left_out(sc_ctx: TemplateContext) -> None:
    """One unseparable season is refused outright. Across seasons the chart
    draws what can be told apart and says what it could not, rather than
    dropping it where nobody would know."""
    sc_ctx.con.execute("INSERT INTO shot_chart VALUES ('1',2002,2,'e8',1,'5:00',FALSE,'Jump Shot',47,0,0,'missed Jumper.')")
    rendered = shotchart.render_shot_chart(sc_ctx.con, sc_ctx.out_dir, "Stephen Curry", shot_value=3)
    assert "(1/2 made" in rendered.message
    assert "left out 1 shot that cannot be told apart as twos or threes" in rendered.message


def test_shot_distance_scopes_to_the_current_season_by_default(sc_ctx: TemplateContext) -> None:
    sc_ctx.con.execute("INSERT INTO shot_chart VALUES ('1',2019,2,'e6',1,'1:00',TRUE,'Jump Shot',25,40,3,'40-foot three point jumper')")
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
    from association.query.templates.players import DEFAULT_HISTORY_SEASONS

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
    assert "did you mean" in (player_history(ps_con, {"player": "Curry", "stat": "points"}).answer or "")


def test_player_history_answers_two_point_percentage_computed_not_stored(ps_con: TemplateContext) -> None:
    """ISSUES.md #114: there is no stored 2-point percentage column (checked
    against the warehouse - only fieldGoalPct and threePointFieldGoalPct
    exist), so it has to be computed from field goals less the threes, the
    same makes-and-attempts discipline every other percentage here follows.
    "show me sga's 2pt percentage for the past 5 years" used to arrive with
    stat='fieldGoalPct' and answer OVERALL shooting instead."""
    for col in ("fieldGoalsMade", "fieldGoalsAttempted", "threePointFieldGoalsMade", "threePointFieldGoalsAttempted"):
        ps_con.con.execute(f"ALTER TABLE player_season_stats_deduped ADD COLUMN {col} INTEGER")
    ps_con.con.execute("UPDATE player_season_stats_deduped SET fieldGoalsMade=700, fieldGoalsAttempted=1300, threePointFieldGoalsMade=200, threePointFieldGoalsAttempted=500 WHERE athlete_id='1'")
    answer = player_history(ps_con, {"player": "Luka Doncic", "stat": "twoPointFieldGoalPct"}).answer or ""
    assert "2PT%" in answer and "2PM" in answer and "2PA" in answer
    # (700-200)/(1300-500) = 500/800 = 62.5% - not fieldGoalPct's own 700/1300 = 53.8%.
    assert "62.5" in answer and "500" in answer and "800" in answer
    assert "53.8" not in answer


def test_leaderboard_refuses_when_a_player_is_named(lb_con: TemplateContext) -> None:
    """A leaderboard ranks the league or a team, never one named person.
    Confirmed live: it answered a question about Klay Thompson with the
    league's true-shooting leaders, Klay silently dropped."""
    with pytest.raises(TemplateUnsupported):
        leaderboard(lb_con, {"stat": "points", "player": "Klay Thompson"})


def test_leaderboard_refuses_a_shot_distance_ranking_naming_the_real_cause(lb_con: TemplateContext) -> None:
    """ISSUES.md #114: "who lead the league in avg 3 point distance" used to
    resolve to the nearest real metric (threePointFieldGoalPct) and answer a
    PERCENTAGE; its "shot distance" sibling used to arrive with a filler
    `player: "player"` and get refused for naming a player the question does
    not mention - the wrong cause. router._route_leaderboard_shot_distance
    sets a sentinel `stat` this checks BEFORE resolve_metric and before the
    named-player refusal above, so a lingering filler player slot (left here
    on purpose, to prove the ordering) cannot produce either wrong-cause
    refusal first.

    .. versionchanged:: 4.4.0
       yardstick-v2 F019: the old wording ("No leaderboard ranks shot
       distance") reads as a claim that none COULD - false, since the key
       computes a real league leader straight from `shot_chart`. The refusal
       now says the ranking is not built, which is the true state of things.
    """
    result = leaderboard(lb_con, {"stat": "shot_distance", "player": "player"})
    assert result.answer == "Shot distance is not ranked league-wide yet - ask about one named player's average shot distance instead."


# ---------------- player_netpoints ----------------


@pytest.fixture
def np_ctx(tmp_path: Path) -> TemplateContext:
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


def test_player_netpoints_defaulted_season_redirects_to_a_retired_players_range(np_ctx: TemplateContext) -> None:
    """No season was named - "now" defaulted - so a player with NetPoints only
    on record in an earlier season is pointed at it instead of a flat refusal
    that reads as though the warehouse held nothing of his at all (issue #18).
    No "or ask for his career" here: player_netpoints has no career span to
    offer."""
    np_ctx.con.execute("INSERT INTO players VALUES ('2','Old Timer')")
    np_ctx.con.execute("INSERT INTO net_points_player VALUES ('2',2020,'Regular Season',100.0,80.0,20.0,5.0,1000,50)")
    s = current_season()
    answer = player_netpoints(np_ctx, {"player": "Old Timer"}).answer or ""
    assert answer == f"The warehouse has no {s} regular season NetPoints for Old Timer. He last appears in 2020. The warehouse holds his 2020 regular season; name one."


def test_player_netpoints_a_named_season_keeps_the_plain_refusal(np_ctx: TemplateContext) -> None:
    """The season the question named is the fact the refusal is about - unlike
    the defaulted case above, redirecting it would be a different question."""
    np_ctx.con.execute("INSERT INTO players VALUES ('2','Old Timer')")
    np_ctx.con.execute("INSERT INTO net_points_player VALUES ('2',2020,'Regular Season',100.0,80.0,20.0,5.0,1000,50)")
    answer = player_netpoints(np_ctx, {"player": "Old Timer", "season": 1999}).answer or ""
    assert answer == "The warehouse has no 1999 regular season NetPoints for Old Timer."


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
    from association.query.templates.netpoints import FINGERPRINT_PARTITION

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


def _sc_ctx_add_games(sc_ctx: TemplateContext, rows: list[tuple[str, str]]) -> None:
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


def test_shot_chart_scopes_to_a_single_game_when_order_is_set(sc_ctx: TemplateContext) -> None:
    """Confirmed live: "a shot chart of Curry's last regular season game"
    charted the whole season - 803 attempts instead of that game's 14."""
    _sc_ctx_add_games(sc_ctx, [("e1", "2026-01-01T00:00Z"), ("eLast", "2026-04-13T00:30Z")])
    sc_ctx.con.execute("INSERT INTO shot_chart VALUES ('1',?,2,'eLast',1,'2:00',TRUE,'Jump Shot',25,26,3,'26-foot three point jumper')", [current_season()])
    answer = shot_chart(sc_ctx, {"player": "Stephen Curry", "order": "recent"}).answer or ""
    assert "1/1 made" in answer  # only the one shot from the last game
    assert "eLast" in answer


def test_shot_chart_order_first_picks_the_earliest_game(sc_ctx: TemplateContext) -> None:
    _sc_ctx_add_games(sc_ctx, [("e1", "2026-01-01T00:00Z"), ("eLast", "2026-04-13T00:30Z")])
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
    # eLast tips at 2026-04-13T00:30Z - 8:30pm Eastern on the 12th, the day it
    # was played. The UTC day this used to assert was the bug.
    assert result.data["game"]["date"] == "2026-04-12"
    answer = result.answer or ""
    assert "6.3 total" in answer and "most recent" in answer
    assert "Offense," not in answer  # not the season breakdown


def test_player_netpoints_order_first_picks_the_earliest_game(np_ctx: TemplateContext) -> None:
    _add_per_game_netpoints(np_ctx)
    # 2025-10-22T00:00Z is 8pm Eastern on October 21st.
    assert player_netpoints(np_ctx, {"player": "SGA", "order": "first"}).data["game"]["date"] == "2025-10-21"


def test_single_game_netpoints_points_at_the_fingerprint_for_the_split(np_ctx: TemplateContext) -> None:
    """This template answers one game's NetPoints TOTAL and has no play-type
    split of its own. Saying nothing would read as the split not existing -
    which is what it used to say, back when it did not."""
    _add_per_game_netpoints(np_ctx)
    assert "Ask for a fingerprint of that game" in (player_netpoints(np_ctx, {"player": "SGA", "order": "recent"}).answer or "")


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
        # player_stat honored `date` from step 3, C2 on; a template with no
        # relation behind it still refuses one.
        ("player_history", {"date": "2026-04-12"}),
    ]:
        with pytest.raises(TemplateUnsupported, match="different span"):
            check_scope(intent, slots)


def test_scope_guard_allows_templates_that_honor_the_slot() -> None:
    check_scope("game_log", {"order": "recent", "date": "2026-04-12"})
    check_scope("shot_chart", {"order": "recent"})
    check_scope("shot_distance", {"order": "first"})
    check_scope("player_netpoints", {"order": "recent"})


def test_scope_guard_lets_only_the_templates_that_read_it_honor_season_type_unstated() -> None:
    """`season_type_unstated` is set by `router._route_game_log_recent_span`
    (a "last N games" question naming no season type) for `game_log` and,
    since 4.4.0, by `router._BOTH_SEASON_TYPES_WORDS` for any intent whose
    question asks for both season types outright ("including the playoffs").
    `game_log`, `player_stat` and `threshold_count` are the player-relation
    templates that read it (`_player_relation_season_type`, one combined
    `season_type IN (2, 3)` read - simpler than `game_log`'s own row-merge,
    since an aggregate has no rows to interleave); the discipline for every
    other intent is the same as any other scoping slot - refuse rather than
    silently ignore."""
    check_scope("game_log", {"order": "recent", "limit": 5, "season_type_unstated": True})
    check_scope("player_stat", {"season_type_unstated": True})
    check_scope("threshold_count", {"season_type_unstated": True})
    for intent in ("leaderboard", "single_game_high"):
        with pytest.raises(TemplateUnsupported, match="different span"):
            check_scope(intent, {"season_type_unstated": True})


def test_scope_guard_ignores_absent_or_empty_slots() -> None:
    check_scope("leaderboard", {})
    check_scope("leaderboard", {"order": None, "date": ""})


def test_every_template_honoring_a_scope_slot_actually_reads_it() -> None:
    # Guards against the list drifting from the code it describes.

    from association.query import templates as module

    for intent, honored in HONORED_SCOPING.items():
        source = _source_a_template_reads_slots_in(module.TEMPLATES[intent])
        for slot in honored:
            assert f'"{slot}"' in source, f"{intent} claims to honor {slot} but never reads it"


def test_shot_distance_scopes_to_one_game(sc_ctx: TemplateContext) -> None:
    # A second game whose shots must NOT be counted.
    _sc_ctx_add_games(sc_ctx, [("e1", "2026-04-13T00:30Z"), ("e2", "2026-01-01T00:00Z")])
    sc_ctx.con.execute("INSERT INTO shot_chart VALUES ('1',?,2,'e2',1,'2:00',TRUE,'Jump Shot',25,40,3,'40-foot three point jumper')", [current_season()])
    answer = shot_distance(sc_ctx, {"player": "Stephen Curry", "order": "recent"}).answer or ""
    # e1 tips at 2026-04-13T00:30Z - 8:30pm Eastern on the 12th, the day it was
    # played. The UTC day this used to assert was the bug.
    assert "most recent game (2026-04-12)" in answer
    assert "2 attempts" in answer  # the fixture's two shots in e1, not the third in e2


# ---------------- fingerprint ----------------


@pytest.fixture
def fp_ctx(tmp_path: Path) -> TemplateContext:
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
    return TemplateContext(con=c, out_dir=tmp_path)


def test_fingerprint_writes_a_file_and_reports_its_path(fp_ctx: TemplateContext) -> None:
    result = fingerprint(fp_ctx, {"player": "Shai Gilgeous-Alexander"})
    assert "Rendered NetPoints fingerprint (total) for Shai Gilgeous-Alexander" in result.answer
    assert list(fp_ctx.out_dir.glob("*.html"))


def test_fingerprint_honors_the_side_slot(fp_ctx: TemplateContext) -> None:
    assert fingerprint(fp_ctx, {"player": "Shai", "side": "defense"}).data["side"] == "defense"


def test_fingerprint_ignores_a_nonsense_side(fp_ctx: TemplateContext) -> None:
    assert fingerprint(fp_ctx, {"player": "Shai", "side": "sideways"}).data["side"] == "total"


def test_fingerprint_defaults_to_the_current_season(fp_ctx: TemplateContext) -> None:
    assert fingerprint(fp_ctx, {"player": "Shai"}).data["season"] == current_season()


def test_fingerprint_draws_the_game_that_was_asked_for(fp_ctx: TemplateContext) -> None:
    """The whole point of the scoping: "his last game" draws that game, and the
    plot is titled with its date rather than with the season - so a reader can
    tell which game they are looking at."""
    result = fingerprint(fp_ctx, {"player": "Shai", "order": "recent"})

    assert result.data["scope"] == "game"
    assert "2026-03-20" in result.answer
    assert list(fp_ctx.out_dir.glob("*_recent_game_*.html"))


def test_the_other_end_of_the_season_draws_a_different_game(fp_ctx: TemplateContext) -> None:
    result = fingerprint(fp_ctx, {"player": "Shai", "order": "first"})

    assert "2026-01-05" in result.answer


def test_a_game_plot_is_not_captioned_as_a_per_100_rate(fp_ctx: TemplateContext) -> None:
    """A single game's numbers are that game's net points - a per-100 rate over
    ~30 possessions turns one made three into a league-leading season figure.
    The caption has to say which quantity is on the page, or the plot is the
    right shape under the wrong claim."""
    fingerprint(fp_ctx, {"player": "Shai", "order": "recent", "scale": "value"})
    page = next(fp_ctx.out_dir.glob("*_recent_game_*.html")).read_text()

    assert "per 100 poss" not in page
    assert "in this game" in page


def test_a_season_plot_still_says_per_100(fp_ctx: TemplateContext) -> None:
    """The other half of the check above: a unit label that stopped appearing
    anywhere would pass it."""
    fingerprint(fp_ctx, {"player": "Shai"})
    page = next(p for p in fp_ctx.out_dir.glob("*.html") if "_game_" not in p.name).read_text()

    assert "per 100 poss" in page


def test_a_fingerprint_for_a_particular_date_still_says_it_cannot(fp_ctx: TemplateContext) -> None:
    """`date` is a different question from `order`: the router gives a calendar
    date and the loader picks a player's first or last game. Answering one with
    the other is exactly the substitution this template exists to refuse."""
    answer = fingerprint(fp_ctx, {"player": "Shai", "date": "2026-01-02"}).answer

    assert "not yet for a particular date" in answer
    assert not list(fp_ctx.out_dir.glob("*.html"))


def test_fingerprint_declares_the_game_scoping_it_handles(fp_ctx: TemplateContext) -> None:
    # It handles them by refusing; check_scope must therefore NOT strip the
    # request out from under it and fall through to an agent with no better source.
    check_scope("fingerprint", {"player": "Shai", "order": "recent", "date": "2026-01-02"})
    # The game-scoping pair specifically - SCOPING_SLOTS also holds opponent,
    # venue, span and without, none of which a fingerprint can narrow to.
    assert {"order", "date"} <= HONORED_SCOPING["fingerprint"] <= SCOPING_SLOTS


def test_fingerprint_without_a_player_falls_through(fp_ctx: TemplateContext) -> None:
    with pytest.raises(TemplateUnsupported):
        fingerprint(fp_ctx, {})


def test_fingerprint_reports_an_unknown_player_rather_than_falling_through(fp_ctx: TemplateContext) -> None:
    assert "No player found" in fingerprint(fp_ctx, {"player": "Nobody At All"}).answer


def test_fingerprint_reports_a_season_with_no_data_rather_than_falling_through(fp_ctx: TemplateContext) -> None:
    # The agent has no better source than the table this just read.
    assert "no NetPoints fingerprint data for season 1999" in fingerprint(fp_ctx, {"player": "Shai", "season": 1999}).answer


def test_fingerprint_plots_two_players_on_one_radar(fp_ctx: TemplateContext) -> None:
    """Two polygons on shared axes IS the comparison, so "compare their
    fingerprints" needs no second intent - only the `players` slot."""
    result = fingerprint(fp_ctx, {"players": ["Shai Gilgeous-Alexander", "Bench Guy"]})
    assert result.data["players"] == ["Shai Gilgeous-Alexander", "Bench Guy"]
    assert "Shai Gilgeous-Alexander vs Bench Guy" in result.answer


def test_fingerprint_does_not_compare_a_player_with_himself(fp_ctx: TemplateContext) -> None:
    # Two spellings of one name drew one polygon over itself and called it a
    # comparison.
    assert fingerprint(fp_ctx, {"players": ["Shai Gilgeous-Alexander", "Shai"]}).data["players"] == ["Shai Gilgeous-Alexander"]


def test_a_chart_template_reports_the_file_it_wrote_as_an_artifact(sc_ctx: TemplateContext) -> None:
    """The path used to exist only inside the message, so showing the chart
    meant parsing a sentence. It is a value now, and this is what says so."""
    result = shot_chart(sc_ctx, {"player": "Stephen Curry", "season": current_season()})
    assert [a.kind for a in result.artifacts] == ["shot_chart"]
    assert result.artifacts[0].path.exists()
    assert result.artifacts[0].name.endswith(".html")
    assert result.data["path"] == str(result.artifacts[0].path)


def test_a_chart_template_that_drew_nothing_reports_no_artifact(sc_ctx: TemplateContext) -> None:
    """ "No shots found" is a real answer, not a failure - but there is no file,
    and claiming one would give a caller a path that does not exist."""
    result = shot_chart(sc_ctx, {"player": "Stephen Curry", "season": 1999})
    assert result.artifacts == []
    assert result.data["path"] is None


def test_the_fingerprint_template_reports_the_file_it_wrote_as_an_artifact(fp_ctx: TemplateContext) -> None:
    result = fingerprint(fp_ctx, {"player": "Shai Gilgeous-Alexander"})
    assert [a.kind for a in result.artifacts] == ["fingerprint"]
    assert result.artifacts[0].path.exists()


def test_templates_that_write_nothing_report_no_artifacts(lb_con: TemplateContext) -> None:
    """The default has to be empty, not unset: a caller iterates artifacts on
    every answer, and a None here would be an AttributeError on the common path."""
    assert leaderboard(lb_con, {"stat": "points"}).artifacts == []


@pytest.mark.parametrize(
    ("intent", "slots"),
    [
        # game_log and shot_distance answer the real queries behind these now
        # ("jaylen brown last 8 games vs pistons", "Jaylen Brown's average
        # shot distance against the Detroit Pistons" - step 3, C5), so the
        # refusal is checked on templates that still cannot narrow that way.
        ("player_netpoints", {"player": "Brandin Podziemski", "without": "curry"}),
        ("player_history", {"player": "Nikola Jokic", "stat": "points", "opponent": "Boston Celtics"}),
    ],
)
def test_scope_guard_refuses_what_the_question_text_narrowed_to(intent: str, slots: dict[str, Any]) -> None:
    """The real StatMuse queries behind these slots were each answered for
    every opponent, every venue, one season, or every game respectively."""
    with pytest.raises(TemplateUnsupported, match="different span"):
        check_scope(intent, slots)


def test_scope_guard_lets_through_what_the_player_templates_now_honor() -> None:
    check_scope("game_log", {"player": "Jaylen Brown", "opponent": "Detroit Pistons", "venue": "home", "span": "career", "without": "x", "order": "recent"})
    check_scope("player_stat", {"player": "Evan Mobley", "opponent": "Milwaukee Bucks", "venue": "away", "span": "career", "without": "x"})
    check_scope("player_history", {"player": "Nikola Jokic", "span": "career"})
    check_scope("shot_distance", {"player": "Jaylen Brown", "opponent": "Detroit Pistons"})


def test_shot_distance_narrows_by_opponent_and_names_it_in_the_answer(sc_ctx: TemplateContext) -> None:
    """`shot_distance` used to refuse `opponent` outright - the real query
    behind it was "Jaylen Brown's average shot distance against the Detroit
    Pistons". Ported onto the relation (step 3, C5), it now narrows to it and
    says so, rather than averaging every opponent."""
    _sc_ctx_add_games(sc_ctx, [("eLakers", "2026-01-01T00:00Z")])
    sc_ctx.con.execute("INSERT INTO shot_chart VALUES ('1',?,2,'eLakers',1,'2:00',TRUE,'Jump Shot',25,40,3,'40-foot three point jumper')", [current_season()])
    # A second game, against a different opponent, whose shot must NOT count.
    sc_ctx.con.executemany(
        "INSERT INTO player_game_log VALUES ('1',?,2,'eOther','9','99',TRUE,FALSE,30,?,NULL)",
        [(current_season(), "2026-02-01T00:00Z")],
    )
    sc_ctx.con.execute("INSERT INTO games VALUES ('eOther',?,2,'2026-02-01T00:00Z','9','99',110,100,'9')", [current_season()])
    sc_ctx.con.execute("INSERT INTO shot_chart VALUES ('1',?,2,'eOther',1,'2:00',TRUE,'Jump Shot',25,26,3,'26-foot three point jumper')", [current_season()])
    answer = shot_distance(sc_ctx, {"player": "Stephen Curry", "opponent": "Los Angeles Lakers"}).answer or ""
    assert "Los Angeles Lakers" in answer
    assert "over 1 attempts" in answer  # eLakers alone, not eOther's shot


def test_team_quarter_points_still_honors_the_opponent_it_always_read() -> None:
    check_scope("team_quarter_points", {"team": "Philadelphia 76ers", "period": 4, "opponent": "Boston Celtics"})


def test_no_template_narrows_to_a_playoff_round() -> None:
    """Nothing in the warehouse records a round or a series game number."""
    assert not any("round" in honored for honored in HONORED_SCOPING.values())
    with pytest.raises(TemplateUnsupported, match="different span"):
        check_scope("player_stat", {"player": "Jayson Tatum", "round": "finals"})


def test_a_zero_threshold_is_refused_rather_than_counting_every_game(con: TemplateContext) -> None:
    """Measured: "most 3 pointers made since 2020" arrived as threshold 0."""
    with pytest.raises(TemplateUnsupported, match="counts every game"):
        threshold_count(con, {"stat": "points", "threshold": 0})


@pytest.mark.parametrize(("intent", "slots"), [("player_stat", {"player": "Joe Ingles", "split": "starter_bench"}), ("leaderboard", {"stat": "points", "since": 2020})])
def test_a_split_or_a_range_is_refused_where_nothing_honors_it(intent: str, slots: dict[str, Any]) -> None:
    with pytest.raises(TemplateUnsupported, match="different span"):
        check_scope(intent, slots)


# ---------------- one player's games: game_log and player_stat, narrowed ----------------

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


@pytest.fixture
def pg_ctx(tmp_path: Path) -> TemplateContext:
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
    # The warehouse's own view, joins and all - keyed on season as well as
    # event_id, so the phantom 1993 row joins its own games row only.
    c.execute(
        "CREATE VIEW player_game_log AS SELECT pbs.*, p.display_name AS player_name, g.date AS game_date, t.abbreviation AS team_abbr, o.abbreviation AS opponent_abbr "
        "FROM player_box_stats pbs LEFT JOIN players p ON p.athlete_id = pbs.athlete_id LEFT JOIN games g ON g.event_id = pbs.event_id AND g.season = pbs.season "
        "LEFT JOIN teams t ON t.team_id = pbs.team_id LEFT JOIN teams o ON o.team_id = pbs.opponent_team_id"
    )
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
    # Podziemski's postseason shots, for period_split's game_n cell: one
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
    real_games.build_table(c, {"games", "teams"})
    return TemplateContext(con=c, out_dir=tmp_path)


def test_a_situation_naming_the_calendar_narrows_the_relation(pg_ctx: TemplateContext) -> None:
    """`situation` is a cell of the relation (step 3, K3): a weekday, a month,
    a holiday and "since <day>" each narrow Podziemski's games by their US
    Eastern day, every template on the relation reads it through
    `scoped_games`, and the answer says it. The fixture's games and their
    Eastern days: e1 {s-1}-11-01, e2 {s-1}-12-01, e3 {s}-01-10, e5 {s-1}-02-28."""
    from datetime import date

    s = current_season()
    played = [date(s - 1, 11, 1), date(s - 1, 12, 1), date(s, 1, 10)]  # e1, e2, e3, the current season's games
    weekday = played[-1].strftime("%A").lower()  # e3's day of the week
    expected = sorted(d.isoformat() for d in played if d.strftime("%A").lower() == weekday)
    log = game_log(pg_ctx, {"player": "Brandin Podziemski", "situation": f"{weekday}s"})
    assert sorted(g["date"] for g in log.data["games"]) == expected and f"on {weekday.capitalize()}s" in (log.answer or "")
    january = player_stat(pg_ctx, {"player": "Brandin Podziemski", "stat": "points", "situation": "in january"})
    assert january.data["stats"]["gamesPlayed"] == 1 and january.data["stats"]["avgPoints"] == 15.0 and "in January" in (january.answer or "")
    # "since december 1st" reads within each game's own season: December is
    # the season's first calendar year, so e2 (Dec 1) and e3 (Jan 10) qualify.
    since = game_log(pg_ctx, {"player": "Brandin Podziemski", "situation": "since december 1st"})
    assert sorted(g["date"] for g in since.data["games"]) == [f"{s - 1}-12-01", f"{s}-01-10"] and "since December 1" in (since.answer or "")
    christmas = game_log(pg_ctx, {"player": "Brandin Podziemski", "situation": "christmas"})
    assert christmas.data.get("games", []) == [] and "on Christmas Day" in (christmas.answer or "")


def test_a_situation_naming_no_calendar_is_refused_by_value(pg_ctx: TemplateContext) -> None:
    """An age, a conference or a return from injury is nothing the relation
    can filter on. Refused with the value in the message and the shapes that
    ARE read named - never dropped, which would answer every game under a
    heading that promised "as an 18 year old"."""
    for situation in ("18 year old", "western conference", "since returning", "before turning 27"):
        with pytest.raises(TemplateUnsupported, match=re.escape(situation)):
            game_log(pg_ctx, {"player": "Brandin Podziemski", "situation": situation})


def test_period_split_reads_one_game_of_each_series(pg_ctx: TemplateContext) -> None:
    """`game_n` reaches period_split through the relation like every other
    slot. It was declared honored and never applied: `_period_split_rows`
    handed `scoped_games` a dict it built itself, with no `game_n` key, so
    "game 1 of each series" and the whole postseason answered identically."""
    whole = period_split(pg_ctx, {"player": "Brandin Podziemski", "period": 1, "season_type": 3})
    assert whole.data["games_played"] == 4 and whole.data["total"] == 15
    second = period_split(pg_ctx, {"player": "Brandin Podziemski", "period": 1, "season_type": 3, "game_n": 2})
    assert second.data["games_played"] == 2 and second.data["total"] == 9, second.answer
    assert "game 2 of each series" in (second.answer or "")


@pytest.fixture
def ps_redirect_ctx(tmp_path: Path) -> TemplateContext:
    """A minimal warehouse for period_split's cross-season redirect
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
        "athlete_id VARCHAR, did_not_play BOOLEAN, minutes INTEGER, starter BOOLEAN)"
    )
    c.executemany(
        "INSERT INTO player_game_log VALUES (?, ?, 2, '1', '2', '1', FALSE, ?, ?)",
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
    return TemplateContext(con=c, out_dir=tmp_path)


def test_period_split_crosses_into_an_earlier_season_when_the_current_one_has_no_starts(ps_redirect_ctx: TemplateContext) -> None:
    """yardstick-v2 F050: "zach collins first quarter stats last 5 games as
    a starter" answered "No 2026 regular season games found ... as a
    starter" - true of the box scores read, and about the wrong year, since
    his real last 5 starts are all in the season before. A "last N games"
    question naming no season is the newest N over his CAREER, the same
    reading a bare `limit` already gets everywhere else on the relation."""
    s = current_season()
    result = period_split(ps_redirect_ctx, {"player": "Test Player", "period": 1, "split": "starter", "order": "recent", "limit": 5})
    assert result.data["season"] == s - 1
    assert result.data["games_played"] == 2
    assert result.data["total"] == 7  # 5 + 2, both his starts, none from the empty current season
    assert result.data["average"] == 3.5
    assert "No games this season" in (result.answer or "")
    assert f"{s - 1} regular season" in (result.answer or "")
    # A season the question NAMES outright keeps the plain refusal - the
    # redirect only fires for a DEFAULTED one.
    named = period_split(ps_redirect_ctx, {"player": "Test Player", "period": 1, "split": "starter", "order": "recent", "limit": 5, "season": s})
    assert named.data["games_played"] == 0
    assert "No games this season" not in (named.answer or "")


def test_period_split_does_not_cross_seasons_with_no_window_asked(ps_redirect_ctx: TemplateContext) -> None:
    """The redirect is for a "last N games" WINDOW - a plain defaulted-season
    question with nothing found stays the plain refusal, since there is no
    window to widen."""
    result = period_split(ps_redirect_ctx, {"player": "Test Player", "period": 1, "split": "starter"})
    assert result.data["games_played"] == 0
    assert "No games this season" not in (result.answer or "")


def test_game_log_drops_the_players_own_team(pg_ctx: TemplateContext) -> None:
    """#147: `game_log` took its `team` branch before it read `player`, so a
    `team` slot beside a named player answered the TEAM's log instead of his -
    here, his own team, which narrows nothing and is dropped."""
    with_team = game_log(pg_ctx, {"player": "Brandin Podziemski", "team": "Golden State Warriors"})
    without_team = game_log(pg_ctx, {"player": "Brandin Podziemski"})
    assert with_team.data["games"] == without_team.data["games"]
    assert with_team.answer == without_team.answer


def test_game_log_promotes_a_different_team_to_opponent(pg_ctx: TemplateContext) -> None:
    """The Curry shape from #147: "steph curry vs 76ers last 4 games" put the
    76ers in `team` rather than `opponent`, and used to answer the 76ers' own
    games instead of Curry's games against them."""
    as_team = game_log(pg_ctx, {"player": "Brandin Podziemski", "team": "Detroit Pistons"})
    as_opponent = game_log(pg_ctx, {"player": "Brandin Podziemski", "opponent": "Detroit Pistons"})
    assert as_team.data["games"] == as_opponent.data["games"]
    assert [g["opponent"] for g in as_team.data["games"]] == ["DET", "DET"]


def test_game_log_drops_a_team_that_resolves_to_nothing(pg_ctx: TemplateContext) -> None:
    """#147: Payton Pritchard's question arrived with an invented "Phoenix
    Suns" in `team`. A name nothing resolves to is dropped, not refused -
    exactly like an invented player name."""
    with_bad_team = game_log(pg_ctx, {"player": "Brandin Podziemski", "team": "Not A Real Team"})
    without_team = game_log(pg_ctx, {"player": "Brandin Podziemski"})
    assert with_bad_team.data["games"] == without_team.data["games"]


def test_game_log_own_team_beside_a_real_opponent_keeps_the_opponent(pg_ctx: TemplateContext) -> None:
    """#147, the Kobe Bryant shape: `team` held his own Lakers and `opponent`
    already held the Rockets - his own team narrows nothing, so the real
    opponent stands."""
    result = game_log(pg_ctx, {"player": "Brandin Podziemski", "team": "Golden State Warriors", "opponent": "Detroit Pistons"})
    assert [g["opponent"] for g in result.data["games"]] == ["DET", "DET"]


def test_game_log_an_opponent_already_named_wins_over_a_disagreeing_team(pg_ctx: TemplateContext) -> None:
    """A `team` beside an already-named `opponent` is dropped rather than
    reconciled or refused: measured on the filed corpus row, "Phoenix Suns" is
    a real, resolvable team, so comparing it against an already-correct
    "Philadelphia 76ers" opponent and refusing on the mismatch answered
    nothing for a question that names both a player and his opponent."""
    result = game_log(pg_ctx, {"player": "Brandin Podziemski", "team": "Boston Celtics", "opponent": "Detroit Pistons"})
    assert [g["opponent"] for g in result.data["games"]] == ["DET", "DET"]


def test_game_log_lists_only_the_games_against_the_named_opponent(pg_ctx: TemplateContext) -> None:
    """Before the contract commit, "jaylen brown last 8 games vs pistons" listed
    the Celtics' last eight games against anybody."""
    result = game_log(pg_ctx, {"player": "Brandin Podziemski", "opponent": "Detroit Pistons"})
    assert [g["opponent"] for g in result.data["games"]] == ["DET", "DET"]
    assert result.answer.startswith(f"Brandin Podziemski vs the Detroit Pistons, last 2 games of the {current_season()} regular season:")


def test_a_career_log_against_an_opponent_crosses_seasons(pg_ctx: TemplateContext) -> None:
    s = current_season()
    result = game_log(pg_ctx, {"player": "Brandin Podziemski", "opponent": "Detroit Pistons", "span": "career", "limit": 8})
    assert [g["season"] for g in result.data["games"]] == [s, s, s - 1]
    assert f"last 3 games of his career ({s - 1}-{s} regular seasons):" in result.answer
    assert "Only 3 games vs the Detroit Pistons in his box scores." in result.answer


def test_a_short_season_log_says_how_many_there_were_and_where_the_rest_are(pg_ctx: TemplateContext) -> None:
    answer = game_log(pg_ctx, {"player": "Brandin Podziemski", "opponent": "Detroit Pistons", "limit": 8}).answer
    assert f"Only 2 games vs the Detroit Pistons in the {current_season()} regular season - ask about his career to reach earlier seasons." in answer


def test_game_log_honors_venue(pg_ctx: TemplateContext) -> None:
    home = game_log(pg_ctx, {"player": "Brandin Podziemski", "venue": "home"}).data["games"]
    away = game_log(pg_ctx, {"player": "Brandin Podziemski", "venue": "away"}).data["games"]
    assert [g["home_away"] for g in home] == ["home", "home"]
    assert [g["opponent"] for g in away] == ["DET"]


def test_game_log_lists_only_games_he_played_and_says_what_it_left_out(pg_ctx: TemplateContext) -> None:
    """The DNP in e4 is not a game he played; the empty line in e6 is not a
    game anybody can average. The empty line is counted in the answer, since in
    2013-2018 about one team-game in eight looks like it."""
    result = game_log(pg_ctx, {"player": "Brandin Podziemski"})
    assert len(result.data["games"]) == 3
    assert "Not counted: 1 game in this span whose box score lists him with no minutes and no stats." in result.answer


def test_game_log_averages_exactly_the_games_it_lists(pg_ctx: TemplateContext) -> None:
    result = game_log(pg_ctx, {"player": "Brandin Podziemski", "limit": 2})
    assert [g["points"] for g in result.data["games"]] == [15, 20]
    assert result.data["averages"]["points"] == pytest.approx(17.5)
    average_row = next(line for line in result.answer.splitlines() if line.strip().startswith("per game"))
    assert "17.5" in average_row


def test_game_log_says_how_many_games_the_window_cut_from(pg_ctx: TemplateContext) -> None:
    """F149 (ISSUES.md): a limit (default or asked) that keeps fewer games
    than qualified used to head the log "last N games" with no word about
    the rest. Podziemski has 3 played regular-season games this season (e1,
    e2, e3 - e4 is a DNP and e6 an empty box-score line, neither played); a
    limit of 2 keeps the two most recent and now says how many it cut from."""
    result = game_log(pg_ctx, {"player": "Brandin Podziemski", "limit": 2})
    assert result.data["qualifying_games"] == 3
    assert result.answer.splitlines()[0].startswith("Brandin Podziemski, last 2 of 3 games")
    # No truncation, no "of N": every qualifying game fit inside the window.
    full = game_log(pg_ctx, {"player": "Brandin Podziemski", "limit": 10})
    assert full.data["qualifying_games"] == 3
    assert full.answer.splitlines()[0].startswith("Brandin Podziemski, last 3 games")
    assert "of 3" not in full.answer.splitlines()[0]


def test_a_named_stat_adds_its_columns_to_the_log(pg_ctx: TemplateContext) -> None:
    result = game_log(pg_ctx, {"player": "Brandin Podziemski", "stat": "freeThrowPct"})
    titles = result.answer.splitlines()[1].split()
    assert titles[-3:] == ["FTM", "FTA", "FT%"]
    # Over the listed games: 2 of 3, 4 of 4, 1 of 2 - a total, not a mean of percentages.
    assert result.data["averages"]["freeThrowPct"] == pytest.approx(100 * 7 / 9)


def test_a_real_stat_the_log_cannot_show_is_refused_rather_than_dropped(pg_ctx: TemplateContext) -> None:
    with pytest.raises(TemplateUnsupported):
        game_log(pg_ctx, {"player": "Brandin Podziemski", "stat": "ts_pct"})
    # Not a stat at all - the required slot filled with something - adds nothing.
    assert game_log(pg_ctx, {"player": "Brandin Podziemski", "stat": "game log"}).data["columns"] == ["MIN", "PTS", "REB", "AST"]


def test_game_log_dates_are_the_eastern_day_the_game_was_played(pg_ctx: TemplateContext) -> None:
    """e2 tipped at 00:30 UTC on the 2nd - 7:30pm Eastern on the 1st. Matching
    the UTC day found it under the wrong date and missed it under the right one."""
    s = current_season()
    games = game_log(pg_ctx, {"player": "Brandin Podziemski", "opponent": "Detroit Pistons"}).data["games"]
    assert games[-1]["date"] == f"{s - 1}-12-01"
    assert [g["date"] for g in game_log(pg_ctx, {"player": "Brandin Podziemski", "date": f"{s - 1}-12-01"}).data["games"]] == [f"{s - 1}-12-01"]
    assert game_log(pg_ctx, {"player": "Brandin Podziemski", "date": f"{s - 1}-12-02"}).data["games"] == []


def test_a_date_finds_its_game_whatever_season_the_router_assumed(pg_ctx: TemplateContext) -> None:
    s = current_season()
    result = game_log(pg_ctx, {"player": "Brandin Podziemski", "date": f"{s - 1}-02-28", "season": s})
    assert [g["season"] for g in result.data["games"]] == [s - 1]


def test_without_counts_a_did_not_play_entry_and_a_missing_row_alike(pg_ctx: TemplateContext) -> None:
    """An injured player mostly has no row at all: Stephen Curry's 2026 is 43
    rows for an 82-game Warriors season, none of them did-not-play."""
    s = current_season()
    result = game_log(pg_ctx, {"player": "Brandin Podziemski", "without": "Stephen Curry"})
    assert sorted(g["date"] for g in result.data["games"]) == [f"{s - 1}-12-01", f"{s}-01-10"]
    assert "a did-not-play entry, or no line in the box score at all" in result.answer


def test_a_teammate_who_played_a_rebuilt_game_is_not_counted_as_absent(pg_ctx: TemplateContext) -> None:
    """The `without` half of the P1 the rebuild left behind.

    `_teammate_played` asked the stored table for minutes, and a line rebuilt
    from play-by-play has none - so every teammate in a rebuilt game read as
    out, and the game was counted as one played "without" him. Measured on the
    real warehouse before the fix: "Anthony Davis without Eric Gordon, 2015"
    listed games Gordon played in; he played 48 of Davis's 68.

    Here Stephen Curry gets the empty line ESPN really serves for e2, and the
    filled view rebuilds it. e2 is then a game he played, so only e3 - where he
    is a genuine did-not-play - is still "without" him.
    """
    s = current_season()
    con = pg_ctx.con
    con.execute("INSERT INTO player_box_stats VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", list(_box("e2", s, "1", "3", "11", minutes=None)))
    con.execute("""
        CREATE VIEW player_box_stats_filled AS
        SELECT pbs.* REPLACE (CASE WHEN pbs.event_id = 'e2' AND pbs.athlete_id = '11' THEN 31 ELSE pbs.points END AS points),
               (pbs.event_id = 'e2' AND pbs.athlete_id = '11') AS reconstructed
        FROM player_box_stats pbs""")

    result = game_log(pg_ctx, {"player": "Brandin Podziemski", "without": "Stephen Curry"})
    assert sorted(g["date"] for g in result.data["games"]) == [f"{s}-01-10"], "e2 is a game Curry played, rebuilt"


def test_without_falls_back_when_the_warehouse_has_no_rebuilt_lines(pg_ctx: TemplateContext) -> None:
    """The same question against a warehouse with no filled view: unchanged.
    The view arrives with a `data load`, and every older warehouse - and every
    other fixture here - has only the stored table."""
    s = current_season()
    result = game_log(pg_ctx, {"player": "Brandin Podziemski", "without": "Stephen Curry"})
    assert sorted(g["date"] for g in result.data["games"]) == [f"{s - 1}-12-01", f"{s}-01-10"]


def test_without_two_teammates_means_neither_of_them_played(pg_ctx: TemplateContext) -> None:
    """ "Celtics record without Tatum and Brown" read only the first name, so
    the answer covered the games without ONE of them. Podziemski played e1, e2
    and e3 this season: Curry played e1 and missed e2 and e3, Kuminga played e3
    and missed e1 and e2. Only e2 was played without both."""
    s = current_season()
    result = game_log(pg_ctx, {"player": "Brandin Podziemski", "without": ["Stephen Curry", "Jonathan Kuminga"]})
    assert [g["date"] for g in result.data["games"]] == [f"{s - 1}-12-01"]
    assert result.data["without"] == ["Stephen Curry", "Jonathan Kuminga"]
    assert "without Stephen Curry and Jonathan Kuminga" in result.answer
    # The one-name question is the same question it always was.
    assert len(game_log(pg_ctx, {"player": "Brandin Podziemski", "without": ["Stephen Curry"]}).data["games"]) == 2


def test_without_asks_between_two_teammates_who_share_a_name(pg_ctx: TemplateContext) -> None:
    answer = game_log(pg_ctx, {"player": "Brandin Podziemski", "without": "curry"}).answer
    assert answer == "'curry' matches more than one player - did you mean Seth Curry or Stephen Curry?"


def test_without_narrows_a_shared_name_to_that_seasons_teammates(pg_ctx: TemplateContext) -> None:
    # Last season Seth was a Celtic and Dell was long retired: only one Curry could be meant.
    result = game_log(pg_ctx, {"player": "Brandin Podziemski", "without": "curry", "season": current_season() - 1})
    assert result.data["without"] == ["Stephen Curry"]


def test_a_mid_season_arrival_is_not_missing_from_the_games_before_he_came(pg_ctx: TemplateContext) -> None:
    """Seth Curry joins at e3. The Warriors' games before that were not played
    "without" a man who was on another team's books."""
    answer = game_log(pg_ctx, {"player": "Brandin Podziemski", "without": "Seth Curry"}).answer
    assert answer == f"Brandin Podziemski played 3 games in the {current_season()} regular season, none of them without Seth Curry."


def test_a_teammate_carried_over_is_on_the_team_before_his_first_game(pg_ctx: TemplateContext) -> None:
    """LeBron James's first 2026 row is 2025-11-19; the Lakers' games before it
    were played without him. Kuminga here is the same shape."""
    result = game_log(pg_ctx, {"player": "Brandin Podziemski", "without": "Jonathan Kuminga"})
    assert [g["opponent"] for g in result.data["games"]] == ["DET", "BOS"]


def test_without_somebody_who_was_never_a_teammate_says_so(pg_ctx: TemplateContext) -> None:
    answer = game_log(pg_ctx, {"player": "Brandin Podziemski", "without": "Jaylen Brown"}).answer
    assert answer == f"Jaylen Brown was not Brandin Podziemski's teammate in any of his 3 games in the {current_season()} regular season."


def test_no_games_against_an_opponent_is_not_no_games(pg_ctx: TemplateContext) -> None:
    """The refusal names the missing fact: he has games, none of them against
    that team. "No games found for him" would send the reader to the wrong place."""
    answer = game_log(pg_ctx, {"player": "Brandin Podziemski", "opponent": "Los Angeles Lakers"}).answer
    assert answer == f"Brandin Podziemski played 3 games in the {current_season()} regular season, none of them vs the Los Angeles Lakers."


def test_a_season_with_no_games_at_all_says_that(pg_ctx: TemplateContext) -> None:
    s = current_season() - 5
    assert game_log(pg_ctx, {"player": "Brandin Podziemski", "season": s}).answer == f"No {s} regular season games found for Brandin Podziemski."


def test_a_defaulted_season_with_no_games_redirects_to_a_retired_players_range(pg_ctx: TemplateContext) -> None:
    """The season was never named - it defaulted to "now" - so a player whose
    only games are long past gets pointed at his own range instead of a
    refusal that reads as though his whole career were missing (issue #18).
    Podziemski's own case above, with an explicit season, must keep its plain
    refusal: that is the negative control this fix must not move."""
    pg_ctx.con.execute("INSERT INTO players VALUES ('30','Old Timer')")
    pg_ctx.con.executemany(
        f"INSERT INTO player_box_stats VALUES ({', '.join('?' for _ in range(24))})",
        [_box("e8", 1995, "5", "3", "30", minutes=30, pts=20)],
    )
    s = current_season()
    answer = game_log(pg_ctx, {"player": "Old Timer"}).answer
    assert answer == f"No {s} regular season games found for Old Timer. He last appears in 1995. The warehouse holds his 1995 regular season; name one, or ask for his career."


def test_a_named_season_with_no_games_keeps_the_plain_refusal(pg_ctx: TemplateContext) -> None:
    """Unlike the default case above, the season here is what the question is
    about - redirecting it would answer a year nobody asked for."""
    pg_ctx.con.execute("INSERT INTO players VALUES ('30','Old Timer')")
    pg_ctx.con.executemany(
        f"INSERT INTO player_box_stats VALUES ({', '.join('?' for _ in range(24))})",
        [_box("e8", 1995, "5", "3", "30", minutes=30, pts=20)],
    )
    answer = game_log(pg_ctx, {"player": "Old Timer", "season": 1999}).answer
    assert answer == "No 1999 regular season games found for Old Timer."


def test_a_career_never_counts_the_phantom_season_twice(pg_ctx: TemplateContext) -> None:
    """1993 is a full copy of 1993-94 under another label; a career that read it
    would list every one of those games twice."""
    result = game_log(pg_ctx, {"player": "Michael Jordan", "span": "career"})
    assert [g["season"] for g in result.data["games"]] == [1995]
    assert "Box scores begin with the 1993-94 season, so his 1990-1993 seasons are not counted." in result.answer


@pytest.mark.parametrize("template", [game_log, player_stat])
def test_a_career_and_a_named_season_at_once_is_refused(pg_ctx: TemplateContext, template: Any) -> None:
    with pytest.raises(TemplateUnsupported):
        template(pg_ctx, {"player": "Brandin Podziemski", "span": "career", "season": current_season()})


def test_game_log_refuses_a_threshold_rather_than_ignoring_it(pg_ctx: TemplateContext) -> None:
    with pytest.raises(TemplateUnsupported):
        game_log(pg_ctx, {"player": "Brandin Podziemski", "stat": "fieldGoalsAttempted", "threshold": 15})


def test_player_stat_on_one_date_is_that_games_line(pg_ctx: TemplateContext) -> None:
    """A date is one game (step 3, C2: the relation's `date` reaches
    player_stat as it always reached game_log). e2 tipped {s-1}-12-02T00:30Z,
    which is the evening of December 1 Eastern - the UTC day would find
    nothing - and one game is a line, not an average, with no season total
    appended and the career span the date replaced left unsaid."""
    s = current_season()
    result = player_stat(pg_ctx, {"player": "Brandin Podziemski", "date": f"{s - 1}-12-01"})
    assert result.answer == f"Brandin Podziemski had 20 points, 7 rebounds and 5 assists on {s - 1}-12-01."
    assert result.data["date"] == f"{s - 1}-12-01" and result.data["stats"]["gamesPlayed"] == 1
    # The router's season is usually its "current" default: a date in the
    # previous season is still found.
    last = player_stat(pg_ctx, {"player": "Brandin Podziemski", "stat": "points", "date": f"{s - 1}-02-28", "season": s})
    assert last.answer == f"Brandin Podziemski had 8 points on {s - 1}-02-28."
    none = player_stat(pg_ctx, {"player": "Brandin Podziemski", "date": f"{s}-07-04"})
    assert none.answer.startswith(f"No regular season game on {s}-07-04 found for Brandin Podziemski")


def test_the_relation_window_is_cut_after_the_row_filters(pg_ctx: TemplateContext) -> None:
    """ "His average over his last 2 games vs Detroit" averages the last two
    DETROIT games, not the last two games of which some were vs Detroit. C0's
    one skeleton-specific rule: the window is cut after every row filter and
    before the aggregate. Read straight off the relation, since no template
    sets a window yet (player_stat hands "last N" to game_log by decision)."""
    from association.query.player_games import aggregate_sql
    from association.query.templates.common import _narrow_player_games, _span_of

    s = current_season()
    span = _span_of("career", None, 2, "player_game_log")
    narrowed = _narrow_player_games(pg_ctx.con, Entity("10", "Brandin Podziemski"), span, opponent="Detroit Pistons", venue=None, without=None)
    assert not isinstance(narrowed, TemplateResult)
    narrowed.window = ("recent", 2)
    sql, params = aggregate_sql(narrowed, ["COUNT(*)", "AVG(pgl.points)", "MIN(g.date)"])
    row = pg_ctx.con.execute(sql, params).fetchone()
    assert row is not None
    games, avg, first = row
    # His Detroit games are e5 (8, s-1), e2 (20) and e3 (15): the newest two are e2 and e3.
    assert (games, avg) == (2, 17.5) and first.startswith(f"{s - 1}-12-02")
    # `windowed=True`: step 3, C5 made `scoped_games` set `.window` from
    # EVERY caller's slots that carry `order`/`limit` - including `game_log`,
    # which already says "last N games" its own way and would double it up -
    # so the phrase is opt-in, for the templates (`shot_chart`/`shot_distance`)
    # that read their rows through `.window` in the first place.
    assert narrowed.filters(windowed=True).endswith(" vs the Detroit Pistons over his last 2 games")
    assert narrowed.filters().endswith(" vs the Detroit Pistons")  # the default stays silent about it
    narrowed.window = ("first", 1)
    sql, params = aggregate_sql(narrowed, ["COUNT(*)", "AVG(pgl.points)"])
    assert pg_ctx.con.execute(sql, params).fetchone() == (1, 8.0)


def test_player_stat_averages_the_games_against_an_opponent(pg_ctx: TemplateContext) -> None:
    """ "evan mobley avg against bucks" refused before this - and before the
    refusal, it was answered with his whole season."""
    result = player_stat(pg_ctx, {"player": "Brandin Podziemski", "opponent": "Detroit Pistons", "stat": "points"})
    # The footer of meetings that follows is the product decision tested below.
    assert (result.answer or "").startswith(f"Brandin Podziemski averaged 17.5 points per game in 2 games vs the Detroit Pistons in the {current_season()} regular season. That is 35 in total.")


def test_player_stat_honors_venue_and_a_teammates_absence(pg_ctx: TemplateContext) -> None:
    assert player_stat(pg_ctx, {"player": "Brandin Podziemski", "venue": "home", "stat": "points"}).data["stats"]["avgPoints"] == 12.5
    without = player_stat(pg_ctx, {"player": "Brandin Podziemski", "without": "Stephen Curry", "stat": "points"}).data["stats"]
    assert (without["gamesPlayed"], without["avgPoints"]) == (2, 17.5)


def test_player_stat_without_two_teammates_averages_only_the_games_neither_played(pg_ctx: TemplateContext) -> None:
    """e2 is the one game Podziemski played without both of them - 20 points.
    Read as a question about Curry alone it was 2 games and 17.5."""
    result = player_stat(pg_ctx, {"player": "Brandin Podziemski", "without": ["Stephen Curry", "Jonathan Kuminga"], "stat": "points"})
    assert (result.data["stats"]["gamesPlayed"], result.data["stats"]["avgPoints"]) == (1, 20.0)
    assert "without Stephen Curry and Jonathan Kuminga" in result.answer


def test_player_stat_says_one_game_not_one_games(pg_ctx: TemplateContext) -> None:
    answer = player_stat(pg_ctx, {"player": "Brandin Podziemski", "opponent": "Boston Celtics", "stat": "points"}).answer
    assert "per game in 1 game vs the Boston Celtics" in answer


def test_player_stat_career_is_totals_over_games_not_an_average_of_averages(pg_ctx: TemplateContext) -> None:
    """(980 + 240) / (70 + 30) = 12.2. The mean of the two season averages would
    be 11.0 - a number no reader could reproduce from the career line."""
    s = current_season()
    result = player_stat(pg_ctx, {"player": "Brandin Podziemski", "span": "career"})
    assert result.data["stats"]["avgPoints"] == 12.2
    assert f"in 100 games over his career (2 regular seasons, {s - 1}-{s})" in result.answer


def test_player_stat_answers_a_shooting_percentage_with_its_makes_and_attempts(pg_ctx: TemplateContext) -> None:
    """ "What is Jokic's 3 point percentage this season" fell through to the agent."""
    s = current_season()
    season = player_stat(pg_ctx, {"player": "Brandin Podziemski", "stat": "threePointFieldGoalPct"}).answer
    assert season == f"Brandin Podziemski shot 35.7% on 3-pointers (100 of 280) in 70 games in the {s} regular season."
    career = player_stat(pg_ctx, {"player": "Brandin Podziemski", "stat": "threePointFieldGoalPct", "span": "career"}).answer
    assert "35.3% on 3-pointers (120 of 340)" in career
    against = player_stat(pg_ctx, {"player": "Brandin Podziemski", "stat": "freeThrowPct", "opponent": "Detroit Pistons"}).answer
    assert "83.3% on free throws (5 of 6) in 2 games vs the Detroit Pistons" in against


def test_player_stat_answers_a_made_count_stat_with_its_attempts_and_percentage(pg_ctx: TemplateContext) -> None:
    """F051 (ISSUES.md): "davion mitchell 3 point stats" (stat=
    threePointFieldGoalsMade) printed the makes and the games and nothing
    else - not the attempts or the percentage they make, both
    `must_include` in the yardstick key. Exercised here over box scores
    (an opponent narrows the read), the one path `test_player_stat_answers_a_shooting_percentage_with_its_makes_and_attempts`
    above does not cover for a made-count stat."""
    against = player_stat(pg_ctx, {"player": "Brandin Podziemski", "stat": "freeThrowsMade", "opponent": "Detroit Pistons"}).answer or ""
    # Same two games (e2, e3) test_player_stat_answers_a_shooting_percentage_with_its_makes_and_attempts
    # reads as freeThrowPct (5 of 6, 83.3%) - the made-count reading states
    # the identical makes/attempts/percentage, phrased as a total rather than
    # a percentage-first sentence.
    assert "That is 5 of 6 (83.3%)." in against
    # A multi-stat line (no single `stat` named) is unaffected: singling out
    # one entry's attempts would read as though only it needed the qualifier.
    default_line = player_stat(pg_ctx, {"player": "Brandin Podziemski", "opponent": "Detroit Pistons"}).answer or ""
    assert "of 6" not in default_line and "%" not in default_line


def test_player_stat_over_the_last_n_games_is_the_log_with_its_averages(pg_ctx: TemplateContext) -> None:
    """The product decision: "stats over his last N games" is a log of those
    games with averages beneath, never the season line - so player_stat hands
    the question to game_log rather than refusing it or answering the season."""
    result = player_stat(pg_ctx, {"player": "Brandin Podziemski", "limit": 2})
    assert len(result.data["games"]) == 2
    assert "averages" in result.data
    # F149 (ISSUES.md): the window (2) is narrower than his 3 qualifying
    # games, so the heading now says how many it cut from.
    assert "last 2 of 3 games" in result.answer


def test_check_scope_lets_player_stat_honor_since_and_order(pg_ctx: TemplateContext) -> None:
    from association.query.templates.common import check_scope

    check_scope("player_stat", {"player": "Brandin Podziemski", "since": 2024})
    check_scope("player_stat", {"player": "Brandin Podziemski", "order": "recent", "limit": 3})


def test_until_closes_a_since_bounded_range_rather_than_reading_through_now(pg_ctx: TemplateContext) -> None:
    """``until`` stops a range at the far end instead of reading every season
    since the start through the present - AGENTS.md's own worst-failure-shape
    example ("best 3 point shooters of the 2010s" answering 2010 through now,
    an unfilled ``until``). yardstick-v2 F045 ("Portis vs bulls 2019-20 to
    2023-24") was this bug: the router read the range's first year alone and
    answered one season of three meetings where 21 games across five spanned.

    Podziemski's fixture games: 1 played box score in the earlier season
    (``s - 1``, e5) and 3 in the current one (``s``: e1, e2, e3 - e4 is a DNP
    and e6 an empty box score, neither counted). ``since=s-1`` alone reads
    every season from there on and so all 4; ``since=s-1, until=s-1`` closes
    the range right back where it opened and reads only the 1.
    """
    s = current_season()
    closed = player_stat(pg_ctx, {"player": "Brandin Podziemski", "stat": "points", "since": s - 1, "until": s - 1})
    assert closed.data["stats"]["gamesPlayed"] == 1
    assert closed.data["seasons"] == [s - 1, s - 1]
    open_ended = player_stat(pg_ctx, {"player": "Brandin Podziemski", "stat": "points", "since": s - 1})
    assert open_ended.data["stats"]["gamesPlayed"] == 4
    assert open_ended.data["seasons"] == [s - 1, s]


def test_player_stat_honors_an_own_team_slot_as_his_tenure(pg_ctx: TemplateContext) -> None:
    """yardstick-v2 F166: "lebron stats as a starter for Miami" used to
    answer his current season, with no way to narrow to a team he no longer
    plays for at all - `own_team` (distinct from `opponent`, and from the
    router's own `team` - see entities._scope_from_question_own_team's
    docstring for why the two are not interchangeable) now keeps only the
    games he played FOR that team. Seth Curry's fixture career has three
    box-scored games: one for Boston (e7, season s-1, 12 points) and two for
    Golden State (e3/e4, season s) - `own_team='Boston Celtics'` keeps only
    the one."""
    result = player_stat(pg_ctx, {"player": "Seth Curry", "stat": "points", "own_team": "Boston Celtics", "span": "career"})
    assert result.data["stats"]["gamesPlayed"] == 1
    assert result.data["stats"]["avgPoints"] == 12
    assert "with the Boston Celtics" in (result.answer or "")
    warriors = player_stat(pg_ctx, {"player": "Seth Curry", "stat": "points", "own_team": "Golden State Warriors", "span": "career"})
    assert warriors.data["stats"]["gamesPlayed"] == 2


def test_player_stat_names_the_real_cause_when_nothing_matches(pg_ctx: TemplateContext) -> None:
    answer = player_stat(pg_ctx, {"player": "Brandin Podziemski", "opponent": "Los Angeles Lakers"}).answer
    assert answer == f"Brandin Podziemski played 3 games in the {current_season()} regular season, none of them vs the Los Angeles Lakers."


def test_a_narrowed_question_carries_the_box_score_floor() -> None:
    """Jordan's 1990 season line is real and answerable; his 1990 line against
    one opponent needs box scores, which start in 1993-94."""
    from association.query.templates.common import check_coverage

    assert check_coverage("player_stat", {"player": "Michael Jordan", "season": 1990, "season_type": 2}) is None
    assert check_coverage("player_stat", {"player": "Michael Jordan", "season": 1990, "season_type": 2, "opponent": "New York Knicks"}) is not None
    assert check_coverage("game_log", {"player": "Michael Jordan", "season": 1990, "season_type": 2}) is not None


# ---------------- player_matchup with a team opponent, not a second player ----------------


def test_player_matchup_with_one_name_and_a_team_opponent_answers_like_game_log(pg_ctx: TemplateContext) -> None:
    """ "sam hauser v mil", "julius randle stats vs blazers with minnestota" and
    "Curry vs dallas last q0 games" all reach player_matchup with one name and
    a team `opponent` - router._route_matchup_against_team cannot see the
    player name entities.scope_from_question restores after it runs, so the
    intent stays player_matchup. This used to refuse `opponent` outright; it
    now answers exactly what game_log would for the same slots."""
    slots = {"player": "Brandin Podziemski", "opponent": "Detroit Pistons"}
    matchup = player_matchup(pg_ctx, slots)
    log = game_log(pg_ctx, dict(slots))
    assert matchup.answer == log.answer
    assert matchup.data == log.data
    assert matchup.data["games"]  # the fixture has real Podziemski-vs-Pistons games


def test_player_matchup_falls_back_from_a_single_element_players_list_too(pg_ctx: TemplateContext) -> None:
    """The router sometimes fills `players` rather than `player` even with one
    name in it - the fallback has to read both, the same way the two-player
    path already merges them."""
    matchup = player_matchup(pg_ctx, {"players": ["Brandin Podziemski"], "opponent": "Detroit Pistons"})
    log = game_log(pg_ctx, {"player": "Brandin Podziemski", "opponent": "Detroit Pistons"})
    assert matchup.answer == log.answer


def test_player_matchup_refuses_a_real_two_player_matchup_with_a_leftover_opponent(pg_ctx: TemplateContext) -> None:
    """check_scope now lets `opponent` through for player_matchup, to let the
    fallback above run - a genuine two-player matchup has no third team to
    narrow the meetings by, so it has to refuse it itself rather than silently
    answer the whole matchup."""
    with pytest.raises(TemplateUnsupported):
        player_matchup(pg_ctx, {"players": ["Brandin Podziemski", "Stephen Curry"], "opponent": "Detroit Pistons"})


def test_check_scope_lets_player_matchup_honor_a_team_opponent(pg_ctx: TemplateContext) -> None:
    check_scope("player_matchup", {"player": "Brandin Podziemski", "opponent": "Detroit Pistons"})


def test_check_scope_lets_player_matchup_honor_without_too(pg_ctx: TemplateContext) -> None:
    """`without` is honored the same way `opponent` is - only for the shape a
    fabricated second player or a teammate named twice collapses down to a
    single player vs a team (ISSUES #34's last two rows: "de'aaron fox vs
    magic ... without wembyanama", "oubre vs warriors without embiid").
    check_scope cannot tell that shape from a genuine two-player matchup by
    the slots alone, so it lets both through, and the template itself is what
    refuses a leftover slot on a genuine matchup - see
    test_player_matchup_refuses_a_real_two_player_matchup_with_a_leftover_without."""
    check_scope("player_matchup", {"player": "Brandin Podziemski", "without": ["Stephen Curry"]})


def test_check_scope_still_refuses_player_matchup_on_an_unhonored_slot(pg_ctx: TemplateContext) -> None:
    """`opponent` and `without` being honored must not quietly let every
    other scoping slot through too."""
    with pytest.raises(TemplateUnsupported, match="different span"):
        check_scope("player_matchup", {"players": ["Brandin Podziemski", "Stephen Curry"], "venue": "home"})


def test_player_matchup_refuses_a_real_two_player_matchup_with_a_leftover_without(pg_ctx: TemplateContext) -> None:
    """ "jokic vs embiid without jamal murray" names no team, so
    _player_matchup_drop_fabricated_second never runs (it only looks for a
    fabricated second player once there is an `opponent` to fold the question
    into) and this stays the genuine two-player matchup it looks like. A
    meeting's own teammates are not what either player's box-score row
    narrows, so `without` is refused rather than silently dropped, the same
    way a leftover `opponent` already is."""
    with pytest.raises(TemplateUnsupported, match="teammate's absence"):
        player_matchup(pg_ctx, {"players": ["Brandin Podziemski", "Stephen Curry"], "without": ["Jaylen Brown"]})


def test_player_matchup_refuses_a_real_two_player_matchup_with_opponent_and_without(pg_ctx: TemplateContext) -> None:
    """The same refusal, with a team present too: two real, unrelated players
    and a `without` naming neither of them - not the fabricated-second-player
    shape _player_matchup_drop_fabricated_second exists for, since Jaylen
    Brown is not confirmably the same person as Stephen Curry - so the
    reduction attempt leaves `texts` untouched and this is still a genuine
    two-player matchup with two scoping slots it cannot honor."""
    with pytest.raises(TemplateUnsupported, match="cannot narrow a two-player matchup"):
        player_matchup(pg_ctx, {"players": ["Brandin Podziemski", "Stephen Curry"], "opponent": "Detroit Pistons", "without": ["Jaylen Brown"]})


# ---------------- player_matchup: a fabricated second "player" that is really `without` or noise ----------------


def test_player_matchup_drops_a_second_player_who_matches_no_one(pg_ctx: TemplateContext) -> None:
    """ "oubre vs warriors without embiid" keeps a garbled team name in
    `players` beside the `opponent` already resolved correctly from it - not
    a second player, noise with no match in the warehouse at all. Dropped
    outright, and the rest reads exactly like the one-name-and-a-team
    shape."""
    matchup = player_matchup(pg_ctx, {"players": ["Brandin Podziemski", "Pistns"], "opponent": "Detroit Pistons", "without": ["Stephen Curry"]})
    log = game_log(pg_ctx, {"player": "Brandin Podziemski", "opponent": "Detroit Pistons", "without": ["Stephen Curry"]})
    assert matchup.answer == log.answer
    assert matchup.data == log.data
    assert matchup.data["games"]  # the fixture has real Podziemski-vs-Pistons games


def test_player_matchup_drops_a_second_player_confirmed_by_without(pg_ctx: TemplateContext) -> None:
    """ "de'aaron fox vs magic ... without wembyanama" carries Fox's own
    teammate both as the fabricated second "player" and, in `without`. Here
    Stephen Curry plays that role for Podziemski: dropped only because
    `without` independently names the very same player - never merely
    because the two share a team, which would silently drop a genuine second
    player a real comparison had named (see the refusal test above, where
    Jaylen Brown does NOT confirm Stephen Curry and the matchup is refused)."""
    matchup = player_matchup(pg_ctx, {"players": ["Brandin Podziemski", "Stephen Curry"], "opponent": "Detroit Pistons", "without": ["Stephen Curry"]})
    log = game_log(pg_ctx, {"player": "Brandin Podziemski", "opponent": "Detroit Pistons", "without": ["Stephen Curry"]})
    assert matchup.answer == log.answer
    assert matchup.data == log.data


def test_player_matchup_drops_a_second_player_confirmed_by_a_near_spelling_of_without(pg_ctx: TemplateContext) -> None:
    """The router corrects the fabricated second player's spelling
    ("Wembanyama") while `without` still carries the user's own typo
    ("wembyanama") - so the identity confirmation has to reach through
    suggest_players' near-spelling pass, not just an exact match. "Stephen
    Cury" here is one letter short of Stephen Curry and matches nobody else,
    the same shape "wembyanama" is for Victor Wembanyama.

    .. versionchanged:: 4.4.0
       A near spelling with exactly one candidate is taken rather than asked
       about (`_resolved_teammate`, F157) - the same default `resolve_player`
       already applies to a bare surname - so both paths now answer the
       narrowed game log instead of refusing over a typo the question's own
       words resolve cleanly. Still checked for agreeing with each other:
       that is the point of the case, not which way `without` resolves.
    """
    matchup = player_matchup(pg_ctx, {"players": ["Brandin Podziemski", "Stephen Curry"], "opponent": "Detroit Pistons", "without": ["Stephen Cury"]})
    log = game_log(pg_ctx, {"player": "Brandin Podziemski", "opponent": "Detroit Pistons", "without": ["Stephen Cury"]})
    assert matchup.answer == log.answer
    assert "did you mean" not in log.answer
    assert "without Stephen Curry" in log.answer


def test_a_near_spelling_of_without_is_taken_and_the_reading_is_visible(pg_ctx: TemplateContext) -> None:
    """yardstick-v2 F157: "de'aaron fox vs magic last five games without
    wembyanama" refused "did you mean Victor Wembanyama?" over a typo the
    question's own key note says resolves cleanly - the true reason the
    question falls short of five games is a game count (only one qualifying
    game exists), not a name that failed to resolve. `_resolved_teammate`
    now takes a near spelling with exactly one candidate rather than asking,
    and `collect_name_readings` carries the sentence saying so - visible
    where a template called directly (as here) does not show it, and shown
    in the agent's own answer (`agent.py` attaches it, not the template)."""
    with collect_name_readings() as readings:
        result = game_log(pg_ctx, {"player": "Brandin Podziemski", "opponent": "Detroit Pistons", "without": ["Stephen Cury"]})
    assert "without Stephen Curry" in result.answer
    assert readings == ["('Stephen Cury' was read as Stephen Curry - a near spelling with no other match.)"]


# ---------------- a narrowed reading over a whole empty-box-score season (#72) ----------------


@pytest.fixture
def empty_season_ctx(tmp_path: Path) -> TemplateContext:
    """One player whose whole season is the empty box score ESPN leaves - every
    Chicago and New Orleans game from 2013 to 2018 in miniature. Built to
    exercise `game_log` and `player_stat`'s narrowed reading, which `sgh_ctx`
    above cannot: `single_game_high` never joins `games`, and these two do. No
    `reconstructed` column, so - like `pg_ctx` - the log carries no rebuild at
    all, the same shape an older warehouse still has."""
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
    return TemplateContext(con=c, out_dir=tmp_path)


def test_game_log_says_the_box_score_is_empty_not_that_the_games_are_missing(empty_season_ctx: TemplateContext) -> None:
    """The named case in ISSUES.md #72: naming a stat outside REBUILT_STATS
    sent the log back to the fetched lines - empty for his whole season - and
    the refusal named the wrong missing fact. "No games found for Anthony
    Davis" is false of a man who played both of them; live against the real
    warehouse this was "No 2015 regular season games found for Anthony Davis"
    for a man who played 68."""
    answer = game_log(empty_season_ctx, {"player": "Anthony Davis", "stat": "turnovers"}).answer
    assert answer == f"Anthony Davis played 2 games in the {current_season()} regular season, but the box score is empty for all of them - ESPN served no minutes or stats for any."


def test_player_stat_narrowed_by_opponent_says_the_box_score_is_empty(empty_season_ctx: TemplateContext) -> None:
    """The other named case: any `player_stat` narrowed by `opponent`, `venue`
    or `without` over an empty-box-score season gave the same wrong-cause
    refusal, for a stat the rebuild does not trust either."""
    answer = player_stat(empty_season_ctx, {"player": "Anthony Davis", "opponent": "Los Angeles Lakers", "stat": "turnovers"}).answer
    assert answer == f"Anthony Davis played 2 games in the {current_season()} regular season, but the box score is empty for all of them - ESPN served no minutes or stats for any."


def test_a_season_with_no_games_at_all_is_still_told_apart_from_an_empty_one(empty_season_ctx: TemplateContext) -> None:
    """The guard above must not fire for a season with no games in it at all -
    that is still "no games found", never "empty box score". Perturbing the
    fix to always claim an empty box score is exactly what this catches."""
    answer = game_log(empty_season_ctx, {"player": "Anthony Davis", "season": current_season() - 5}).answer
    assert answer == f"No {current_season() - 5} regular season games found for Anthony Davis."


@pytest.fixture
def narrowed_rebuilt_ctx(tmp_path: Path) -> TemplateContext:
    """The same empty season as ``empty_season_ctx``, but with a `reconstructed`
    log the way the real warehouse builds one: `player_box_stats` keeps ESPN's
    zeroed rows, and `player_game_log` carries the figures rebuilt from
    play-by-play, `reconstructed` marking exactly those - the split
    `rebuilt_ctx` above uses, with the `team_id`/`opponent_team_id` columns
    `player_stat`'s narrowed (``opponent``/``venue``/``without``) reading
    needs and `rebuilt_ctx` does not carry."""
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
    # rebounds/assists are here even though no test reads them: game_log's
    # _LOG_BASE always shows MIN/PTS/REB/AST, so a default (no-stat) game_log
    # query needs the columns present whatever `stat` was asked for.
    c.execute(
        "CREATE TABLE player_game_log (event_id VARCHAR, season INTEGER, season_type INTEGER, team_id VARCHAR, opponent_team_id VARCHAR, athlete_id VARCHAR, "
        "player_name VARCHAR, did_not_play BOOLEAN, minutes INTEGER, points INTEGER, rebounds INTEGER, assists INTEGER, turnovers INTEGER, fouls INTEGER, "
        "fieldGoalsMade INTEGER, fieldGoalsAttempted INTEGER, game_date VARCHAR, opponent_abbr VARCHAR, reconstructed BOOLEAN)"
    )
    c.executemany(
        "INSERT INTO player_game_log VALUES (?,?,2,'1','2','1','Anthony Davis',FALSE,NULL,?,?,?,?,?,?,?,?,'LAL',TRUE)",
        [("e1", s, 24, 8, 3, 3, 2, 9, 18, f"{s - 1}-11-01"), ("e2", s, 18, 6, 4, 5, 4, 7, 15, f"{s - 1}-12-01")],
    )
    return TemplateContext(con=c, out_dir=tmp_path)


def test_player_stat_reads_rebuilt_games_before_deciding_none_matched_the_opponent(narrowed_rebuilt_ctx: TemplateContext) -> None:
    """The `player_stat` mirror of the `game_log` case below: both of Anthony
    Davis's games here are covered by the rebuild, so a `points` question
    narrowed to an opponent he never faced is missing the OPPONENT, not a box
    score - `_no_narrowed_games` has to be given the same `rebuilt` reading
    `_box_score_player_stat`'s own query used, or its diagnostic is stricter
    than the answer it is explaining."""
    answer = player_stat(narrowed_rebuilt_ctx, {"player": "Anthony Davis", "opponent": "Boston Celtics", "stat": "points"}).answer
    assert answer == f"Anthony Davis played 2 games in the {current_season()} regular season, none of them vs the Boston Celtics."
    assert "empty box score" not in answer


def test_game_log_reads_rebuilt_games_before_deciding_none_matched_the_opponent(narrowed_rebuilt_ctx: TemplateContext) -> None:
    """The fact that is really missing when a rebuilt season is narrowed to an
    opponent it never faced is the opponent, not the season - and telling them
    apart needs `_no_narrowed_games` to check for a recorded game under the
    SAME `rebuilt` reading the caller's own query used. Both of Anthony Davis's
    games here are covered by the rebuild (`reconstructed`), so the season is
    NOT "empty box score" from `game_log`'s point of view; asking about an
    opponent he never faced must say so, not repeat the box-score message."""
    answer = game_log(narrowed_rebuilt_ctx, {"player": "Anthony Davis", "opponent": "Boston Celtics"}).answer
    assert answer == f"Anthony Davis played 2 games in the {current_season()} regular season, none of them vs the Boston Celtics."
    assert "empty box score" not in answer


def test_narrowed_player_stat_reads_a_rebuilt_line_for_a_trusted_stat(narrowed_rebuilt_ctx: TemplateContext) -> None:
    """The P2 this closes for `player_stat`: before it, ANY narrowing
    (``opponent``, ``venue`` or ``without``) read only the stored table, which
    is empty for every one of these games - even for points, the one stat the
    rebuild is trusted for. Live against the real warehouse this was "Anthony
    Davis points vs the Lakers in 2015" answering the wrong-cause refusal."""
    result = player_stat(narrowed_rebuilt_ctx, {"player": "Anthony Davis", "opponent": "Los Angeles Lakers", "stat": "points"})
    assert result.data["stats"]["gamesPlayed"] == 2
    assert result.data["stats"]["avgPoints"] == 21.0
    assert "no games found" not in result.answer.lower()
    assert "empty box score" not in result.answer
    assert "2 of these games have no box score from ESPN" in result.answer
    assert "rebuilt from play-by-play" in result.answer


def test_narrowed_player_stat_still_refuses_a_rebuilt_line_for_an_untrusted_stat(narrowed_rebuilt_ctx: TemplateContext) -> None:
    """Turnovers are outside REBUILT_STATS, so the narrowed reading must not
    widen to the rebuild even though one exists here - and the refusal still
    has to name the fact that is really missing (the box score, not the
    season)."""
    answer = player_stat(narrowed_rebuilt_ctx, {"player": "Anthony Davis", "opponent": "Los Angeles Lakers", "stat": "turnovers"}).answer
    assert answer == f"Anthony Davis played 2 games in the {current_season()} regular season, but the box score is empty for all of them - ESPN served no minutes or stats for any."


def test_narrowed_player_stat_never_reads_a_shooting_percentage_from_a_rebuilt_line(narrowed_rebuilt_ctx: TemplateContext) -> None:
    """A rebuilt line's attempts are outside what the rebuild was measured for
    (`UNGATED_ON_REBUILD`), so a shooting percentage must never widen to it
    even though makes and attempts are both present here. Asserted directly on
    the gate as well as through the template: `player_stat` itself never asks
    for a shooting stat and a per-game stat at once (`wanted` is always empty
    when `shooting` is set), so a query-level assertion alone cannot tell this
    guard apart from that caller's own invariant - the same reasoning
    `_rebuilt_readable` in `games.py` is asserted on directly for."""
    con = narrowed_rebuilt_ctx.con
    assert _box_score_stat_rebuilt(con, ["points"], None) is True
    assert _box_score_stat_rebuilt(con, ["points"], SHOOTING_STATS["fieldGoalPct"]) is False
    assert _box_score_stat_rebuilt(con, [], SHOOTING_STATS["fieldGoalPct"]) is False
    answer = player_stat(narrowed_rebuilt_ctx, {"player": "Anthony Davis", "opponent": "Los Angeles Lakers", "stat": "fieldGoalPct"}).answer
    assert answer == f"Anthony Davis played 2 games in the {current_season()} regular season, but the box score is empty for all of them - ESPN served no minutes or stats for any."


def test_player_history_career_is_every_season(ps_con: TemplateContext) -> None:
    """ "Jokic's scoring by year, career" came back as the default four seasons,
    under a heading that did not say how many it had left out."""
    s = current_season()
    for offset in range(1, 8):
        ps_con.con.execute("INSERT INTO player_season_stats_deduped (athlete_id, season, season_type, gamesPlayed, avgPoints) VALUES ('1',?,2,70,20.0)", [s - offset])
    result = player_history(ps_con, {"player": "Luka Doncic", "stat": "points", "span": "career", "limit": 4})
    assert len(result.data["seasons"]) == 8
    assert result.answer.startswith(f"Luka Doncic, points per game by regular season, career, {s - 7}-{s} (most recent first):")
    four = player_history(ps_con, {"player": "Luka Doncic", "stat": "points"}).answer
    assert four.startswith(f"Luka Doncic, points per game by regular season, {s - 3}-{s} (most recent first):")


def test_player_history_career_states_the_combined_percentage(ps_con: TemplateContext) -> None:
    """F041 (ISSUES.md): "show me sga's career 2pt percentage" answered a
    season-by-season table whose rows summed exactly to the combined career
    figure the question asked for, without ever stating it. The career line
    is the games-weighted total (makes/attempts summed), never a mean of the
    per-season percentages - which would give a different, wrong number here
    ((60% + 20%) / 2 = 40%, not the true (600+40)/(1000+200) = 53.3%)."""
    for col in ("fieldGoalsMade", "fieldGoalsAttempted", "threePointFieldGoalsMade", "threePointFieldGoalsAttempted"):
        ps_con.con.execute(f"ALTER TABLE player_season_stats_deduped ADD COLUMN {col} INTEGER")
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
    answer = player_history(ps_con, {"player": "Luka Doncic", "stat": "twoPointFieldGoalPct", "span": "career"}).answer or ""
    assert answer.endswith("Luka Doncic's career 2PT%: 53.3% (640 of 1,200).")
    # No span: the default four-season table (here, both rows) states no
    # combined figure - a window nobody asked to see summed.
    windowed = player_history(ps_con, {"player": "Luka Doncic", "stat": "twoPointFieldGoalPct"}).answer or ""
    assert "career" not in windowed


def test_player_history_career_states_the_combined_total(ps_con: TemplateContext) -> None:
    """The counting-stat sibling of the percentage career line above: the
    plain career total, summed the same way `_career_player_stat` sums one."""
    s = current_season()
    ps_con.con.execute("INSERT INTO player_season_stats_deduped (athlete_id, season, season_type, gamesPlayed, avgPoints, points) VALUES ('1',?,2,70,20.0,1400)", [s - 1])
    answer = player_history(ps_con, {"player": "Luka Doncic", "stat": "points", "span": "career"}).answer or ""
    # Base row (ps_con): 2,143 total points; plus the season just added: 1,400.
    assert answer.endswith("Luka Doncic's career total: 3,543 points.")


def test_team_game_log_honors_opponent_and_venue(gl_con: TemplateContext) -> None:
    home = game_log(gl_con, {"team": "Knicks", "venue": "home"})
    assert [g["date"] for g in home.data["games"]] == ["2026-04-10"]
    assert home.answer.startswith(f"New York Knicks at home, most recent game of the {current_season()} regular season (1-0):")
    assert len(game_log(gl_con, {"team": "Knicks", "opponent": "Boston Celtics"}).data["games"]) == 2


def test_team_game_log_says_when_it_never_met_the_opponent(gl_con: TemplateContext) -> None:
    gl_con.con.execute("INSERT INTO teams VALUES ('13','LAL','Los Angeles Lakers')")
    answer = game_log(gl_con, {"team": "Knicks", "opponent": "Los Angeles Lakers"}).answer
    assert answer == f"The New York Knicks played 2 games in the {current_season()} regular season, none of them vs the Los Angeles Lakers."


def test_team_game_log_leaves_without_to_with_without(gl_con: TemplateContext) -> None:
    with pytest.raises(TemplateUnsupported):
        game_log(gl_con, {"team": "Knicks", "without": "Jalen Brunson"})


def test_team_game_log_career_says_all_time(gl_con: TemplateContext) -> None:
    result = game_log(gl_con, {"team": "Knicks", "span": "career"})
    assert len(result.data["games"]) == 2
    assert f"(all-time, {current_season()} regular season) (1-1):" in result.answer


def test_team_game_log_is_not_doubled_by_a_phantom_season(gl_con: TemplateContext) -> None:
    """The phantom 1993 shares every event id with 1994, and a join on event_id
    alone listed each of those games twice - 164 rows for the Celtics' 82."""
    gl_con.con.execute("INSERT INTO games VALUES ('e1',?,2,'2026-04-10T22:00Z','18','2',112,95,'18',false,'New York')", [current_season() - 1])
    # Rebuilt, or the new row never reaches the list the log reads and this
    # asserts 2 without having exercised the season-keyed join at all. The two
    # rows are in different seasons, so `real_games` keeps both - collapsing a
    # season label is TEAM_GAMES_SQL's job, not its.
    real_games.build_table(gl_con.con, {"games", "teams"})
    assert len(game_log(gl_con, {"team": "Knicks"}).data["games"]) == 2


def test_team_game_log_does_not_call_a_missing_result_a_loss(gl_con: TemplateContext) -> None:
    """ "Not the winner" is not the same fact as "lost".

    Perturbed into `real_games` rather than into `games` underneath it, because
    the two are no longer the same question: a row with no winner is a 0-0
    placeholder, which `real_games` drops, so nulling one in `games` would test
    that the game disappears rather than that a missing result is not a loss.
    The guard this protects is now defensive - the filtered list has no NULL
    winner in it - and this is the only way left to watch it work."""
    gl_con.con.execute("UPDATE real_games SET winner_team_id = NULL WHERE event_id = 'e2'")
    result = game_log(gl_con, {"team": "Knicks"})
    assert (result.data["wins"], result.data["losses"]) == (1, 0)
    assert "(1-0, 1 with no recorded result):" in result.answer


# ---------------- playoffs before 1993-94, by the year they were played ----------------


@pytest.fixture
def playoff_ctx(tmp_path: Path) -> TemplateContext:
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
    return TemplateContext(con=c, out_dir=tmp_path)


def test_an_early_playoffs_is_found_by_the_year_it_was_played(playoff_ctx: TemplateContext) -> None:
    """ESPN labels the 1991 Finals season 1990. Matched by label, "the 1991
    playoffs" found 1992's, and "the 1990 playoffs" found 1991's."""
    both = ["Chicago Bulls", "Los Angeles Lakers"]
    assert head_to_head(playoff_ctx, {"teams": both, "season": 1991, "season_type": 3}).data["games"] == 2
    assert head_to_head(playoff_ctx, {"teams": both, "season": 1990, "season_type": 3}).data["games"] == 0


def test_the_phantom_season_does_not_double_a_playoffs(playoff_ctx: TemplateContext) -> None:
    assert head_to_head(playoff_ctx, {"teams": ["Chicago Bulls", "Los Angeles Lakers"], "season": 1994, "season_type": 3}).data["games"] == 1


def test_team_quarter_points_finds_an_early_playoffs_by_its_year(playoff_ctx: TemplateContext) -> None:
    got = team_quarter_points(playoff_ctx, {"team": "Chicago Bulls", "period": 1, "season": 1991, "season_type": 3})
    assert [g["points"] for g in got.data["games"]] == [20, 27]


def test_a_team_log_labels_an_early_playoffs_by_its_year(playoff_ctx: TemplateContext) -> None:
    got = game_log(playoff_ctx, {"team": "Chicago Bulls", "season": 1991, "season_type": 3})
    assert [g["season"] for g in got.data["games"]] == [1991, 1991]


def test_head_to_head_takes_the_opponent_as_the_other_team(playoff_ctx: TemplateContext) -> None:
    """ "Celtics vs Bulls head to head record" arrived as team + opponent and was refused."""
    check_scope("head_to_head", {"team": "Chicago Bulls", "opponent": "Los Angeles Lakers"})
    got = head_to_head(playoff_ctx, {"team": "Chicago Bulls", "opponent": "Los Angeles Lakers", "season": 1991, "season_type": 3})
    assert got.data["games"] == 2


def test_head_to_head_reads_past_a_team_named_twice(playoff_ctx: TemplateContext) -> None:
    """The first two names are one team; the opponent after them is the second."""
    slots = {"team": "Chicago Bulls", "teams": ["Bulls"], "opponent": "Los Angeles Lakers", "season": 1991, "season_type": 3}
    assert head_to_head(playoff_ctx, slots).data["games"] == 2


# ---------------- period_split ----------------

SEASON = current_season()


@pytest.fixture
def period_ctx(tmp_path: Path) -> TemplateContext:
    """One player, six games, and shots whose value has to be DERIVED rather
    than read off a label - which is the whole point of the template.

    Two of the games exist to hold the denominator honest. In e5 he played and
    scored only in the second quarter, so it is a first-quarter game worth
    ZERO - the case the first version dropped, inflating every average. In e6
    he did not play at all, so it is no game of his in any period, even though
    a teammate's shots mean the shot table covers it.

    Every made shot here is 26 feet from the rim at (25, 0), a real three's
    position, and carries `points_attempted = 0`, which is ESPN's "unlabeled"
    and not "zero points". Scored by the label alone each of these is worth
    nothing; scored by its position each is worth 3. The fixture is built that
    way on purpose: it is the shape that made per-quarter points read 76.8%
    against ESPN's own linescores before `SHOT_VALUE_SQL` was used.
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
        "TRUE AS starter, g.date AS game_date, p.display_name AS player_name "
        "FROM player_box_stats pbs JOIN games g ON g.event_id = pbs.event_id AND g.season = pbs.season "
        "LEFT JOIN players p ON p.athlete_id = pbs.athlete_id"
    )
    return TemplateContext(con=c, out_dir=tmp_path / "out")


def test_a_quarter_is_summed_from_the_shots_position_not_its_label(period_ctx: TemplateContext) -> None:
    """Curry's first quarters: 2 threes in e1, 1 in e2, 1 in e4 - 12 points.
    Every one carries `points_attempted = 0`, so a template that trusted ESPN's
    label would answer 0, and one that looked for the words "three point" in
    the description would answer 8. Only the position gives 12. Misses do not
    count.

    **Five games, not three.** e3 and e5 are games he played without a
    first-quarter make, and they are zeros in the average rather than absent
    from it; e6 he sat out. Counting only the games with a make answered 4.0
    here - and "RJ Barrett ... over 46 games, averaging 5.4" for a player who
    played 57 and averaged 4.4, which is a fluent wrong number whose sum was
    right.
    """
    result = period_split(period_ctx, {"player": "Stephen Curry", "period": 1, "season": SEASON, "season_type": 2})
    assert result.data["total"] == 12
    assert result.data["games_played"] == 5, "e1-e5; e6 is a DNP and e7 has no shot data at all"
    assert result.data["average"] == pytest.approx(2.4), "12 over 5 played games, not 4.0 over the 3 with a make"
    assert "12 points in the 1st quarter over 5 games" in (result.answer or "")


def test_a_half_is_the_two_quarters_it_holds_and_never_overtime(period_ctx: TemplateContext) -> None:
    """First half is periods 1 and 2; second half is 3 and 4. e4's overtime
    three belongs to neither - a game that went to overtime still had a second
    half, and folding OT in would quietly answer a different question for
    exactly the games people ask about most."""
    first = period_split(period_ctx, {"player": "Stephen Curry", "half": 1, "season": SEASON, "season_type": 2})
    second = period_split(period_ctx, {"player": "Stephen Curry", "half": 2, "season": SEASON, "season_type": 2})
    assert first.data["total"] == 18, "e1 2, e2 1, e3 1 (Q2), e4 1, e5 1 (Q2) - threes all"
    assert second.data["total"] == 12, "e1 Q3, e2 two in Q4, e3 Q3 - and NOT e4's overtime"


def test_an_opponent_and_a_venue_narrow_which_games_count(period_ctx: TemplateContext) -> None:
    """Both are in HONORED_SCOPING for this template, so both filter rather
    than refuse. Against the Lakers: e1 and e3. At home: e1, e3, e4 and e5."""
    vs_lakers = period_split(period_ctx, {"player": "Stephen Curry", "period": 1, "season": SEASON, "season_type": 2, "opponent": "Lakers"})
    assert vs_lakers.data["total"] == 6 and vs_lakers.data["games_played"] == 2, "e1 and e3; e3 is a scoreless first quarter, not a missing game"
    at_home = period_split(period_ctx, {"player": "Stephen Curry", "period": 1, "season": SEASON, "season_type": 2, "venue": "home"})
    assert at_home.data["total"] == 9 and at_home.data["games_played"] == 4, "e1, e3, e4, e5 at home; e2 away; e6 a DNP"


def test_a_season_whose_shots_cannot_be_valued_is_refused(period_ctx: TemplateContext) -> None:
    """2002 is `UNSEPARABLE_SHOT_VALUES`: `SHOT_VALUE_SQL` is NULL for 20,534 of
    its made shots, so a sum over them means nothing. Measured against ESPN's
    linescores it reconciles 4.9% of the time. The refusal names that, rather
    than reporting a number nobody should read."""
    answer = period_split(period_ctx, {"player": "Stephen Curry", "period": 1, "season": 2002, "season_type": 2}).answer or ""
    assert "cannot be answered for 2002" in answer


def test_the_worst_reconcilable_season_is_refused_and_the_merely_poor_ones_are_caveated(period_ctx: TemplateContext) -> None:
    """2016 reconciles at 76.5% - one quarter in four - and is refused. 2004 is
    95.7%, which is worth answering with the figure attached rather than
    withholding. `PERIOD_REFUSE_BELOW` sits between them on purpose."""
    assert "cannot be answered for 2016" in (period_split(period_ctx, {"player": "Stephen Curry", "period": 1, "season": 2016, "season_type": 2}).answer or "")
    caveated = period_split(period_ctx, {"player": "Stephen Curry", "period": 1, "season": 2004, "season_type": 2}).answer or ""
    assert "96% of the time" in caveated, "a poor season answers, and says how poor"


def test_a_stat_that_is_not_points_is_refused_rather_than_approximated(period_ctx: TemplateContext) -> None:
    """`shot_chart` holds shots. Rebounds and assists are not in it at all, and
    deriving them per period from `plays` carries its own fidelity per stat -
    fouls rebuild at 83%. Refusing names that instead of answering from a
    weaker source."""
    with pytest.raises(TemplateUnsupported, match="points only"):
        period_split(period_ctx, {"player": "Stephen Curry", "period": 1, "season": SEASON, "season_type": 2, "stat": "rebounds"})


def test_a_question_with_no_period_is_not_this_template(period_ctx: TemplateContext) -> None:
    """ "by quarter" is a breakdown across all four, which is a different shape."""
    with pytest.raises(TemplateUnsupported, match="needs a period"):
        period_split(period_ctx, {"player": "Stephen Curry", "season": SEASON, "season_type": 2})


def test_the_scoping_slots_this_template_filters_on_are_declared_honored() -> None:
    """`HONORED_SCOPING` is what `check_scope` reads, and the template's own SQL
    is what actually filters - two hand-maintained facts about the same thing.
    Testing the template directly cannot catch them disagreeing, because the
    SQL filters whether or not the slot is declared; the failure is a REFUSAL
    of a question this answers perfectly well.

    The other direction matters too: a slot listed here that the SQL ignores
    would silently answer a broader question, which is the shape this whole
    module exists to prevent.

    .. versionchanged:: 4.4.0
       ``date`` moved from refused to honored (step 3, C5): its games come
       from :func:`common.scoped_games` like every other narrowing here, so it
       needs no clause of its own. ``span`` and ``since`` moved the other way
       - see the next assertion.
    """
    for slot in ("opponent", "venue", "without", "order", "date"):
        check_scope("period_split", {"player": "Stephen Curry", "period": 1, slot: "home"})
    # Two slots it still does not filter on, for the same reason: the accuracy
    # caveat (PERIOD_RECONCILIATION) is measured per season, and this reads
    # only one - see RELATION_SCOPING_EXCLUDED["period_split"].
    for slot, value in (("span", "career"), ("since", 2023)):
        with pytest.raises(TemplateUnsupported):
            check_scope("period_split", {"player": "Stephen Curry", "period": 1, slot: value})


def test_a_log_lists_the_games_and_keeps_the_season_in_the_header(period_ctx: TemplateContext) -> None:
    """ "rj barrett 4th qtr log" got a total and an average, and 7 of the 11
    questions this template answered in its first replay asked for a log. The
    rows list every played game, zeros included, and the header still answers
    the season rather than the rows shown."""
    answer = period_split(period_ctx, {"player": "Stephen Curry", "period": 1, "season": SEASON, "season_type": 2, "per_game": True}).answer or ""
    assert "over 5 games" in answer
    assert "1st quarter points, every game:" in answer
    assert len([line for line in answer.splitlines() if line.strip()[:4].isdigit()]) == 5


def test_a_date_narrows_to_that_one_game(period_ctx: TemplateContext) -> None:
    """Step 3, C5: `date` now reaches this template through `scoped_games`,
    the same as every other narrowing here, instead of being refused. e1 is
    the only game on its own Eastern date (a team plays at most one game a
    day), so the answer is the one-game header rather than a season total -
    and, since the season slot is deliberately NOT passed (a date replaces
    it, the same override `game_log` makes), the season named in that header
    has to come from the game itself.

    Checked against the real warehouse first: LeBron James on 2025-11-18
    (2nd quarter, no season slot given) answers "... scored 7 points in the
    2nd quarter vs the Utah Jazz on 2025-11-18 (2026 regular season)" -
    matching a direct, unscoped read of that one game's shots.
    """
    e1_date = _eastern_date_of(f"{SEASON - 1}-11-01T00:30Z")
    result = period_split(period_ctx, {"player": "Stephen Curry", "period": 1, "date": e1_date})
    assert result.data["total"] == 6, "e1's two made first-quarter threes, not the 12 points across e1/e2/e4"
    assert result.data["games_played"] == 1
    assert result.data["season"] == SEASON, "read off the game itself, not defaulted separately"
    assert f"vs the Los Angeles Lakers on {e1_date}" in (result.answer or "")


def test_a_date_with_no_game_says_which_date_was_empty(period_ctx: TemplateContext) -> None:
    """The "no games found" refusal names the date, the same way every other
    narrowing here is said in the answer - a silent date would read as a
    season with nothing on record, which is a different (false) claim."""
    answer = period_split(period_ctx, {"player": "Stephen Curry", "period": 1, "date": "2019-07-04"}).answer or ""
    assert "on 2019-07-04" in answer


def test_a_date_in_a_badly_reconciled_season_is_refused_for_that_season(period_ctx: TemplateContext) -> None:
    """The accuracy refusal is read off the game a date finds, not assumed
    from a season slot the router usually defaults to "now" - so a date from
    2004 (labeled `PERIOD_RECONCILIATION`) is caveated even with no season
    named at all."""
    answer = period_split(period_ctx, {"player": "Stephen Curry", "period": 1, "date": "2003-11-04"}).answer or ""
    assert "96% of the time" in answer


def test_a_career_span_is_refused_for_the_reconciliation_caveat_it_cannot_apply(period_ctx: TemplateContext) -> None:
    """`span` "career" moved from silently honored (and silently WRONG - see
    the SQL fix below) to explicitly excluded: `PERIOD_RECONCILIATION` is
    measured per season, and summing points across a career would need to
    apply it once per season summed in, which this does not do. The refusal
    is at the routing layer (`check_scope`, via `RELATION_SCOPING_EXCLUDED`),
    the same as every other excluded cell here - see
    `test_the_scoping_slots_this_template_filters_on_are_declared_honored`.
    """
    with pytest.raises(TemplateUnsupported, match="different span"):
        check_scope("period_split", {"player": "Stephen Curry", "period": 1, "span": "career"})


def test_a_career_read_sums_every_season_now_the_shot_join_needs_no_season_param(period_ctx: TemplateContext) -> None:
    """The bug the join-based rewrite fixes, called directly (check_scope is
    what actually refuses `span` "career" in the pipeline - see the test
    above): before, `span.season` was `None` for a career, and the shot
    query bound it as a literal SQL parameter - `season = NULL` matches
    nothing, so this answered "No {current season} games found for Stephen
    Curry", a false-cause refusal about a player the fixture holds two
    seasons of games for. Measured against the real warehouse too: LeBron
    James's career 1st-quarter read went from that same false "no games"
    refusal to a real sum (11,112 points over 1,622 games) once the shot CTEs
    joined to the relation's own selected games instead of a literal
    ``season = ?``/``season_type = ?`` pair.
    """
    result = period_split(period_ctx, {"player": "Stephen Curry", "period": 1, "span": "career"})
    assert result.data.get("season") is None or "message" not in result.data, "the old bug: a false 'no games' refusal for a player with games on record"
    assert result.data["total"] == 15, "e1(6) + e2(3) + e4(3) across SEASON, plus e04(3) in 2004"
    assert result.data["games_played"] == 6, "e1,e2,e3,e4,e5 (SEASON) and e04 (2004); e6 a DNP, e7 uncovered by the shot table"


def test_a_team_log_names_each_opponent_as_it_was_that_season(gl_con: TemplateContext) -> None:
    """ESPN files the Nets under one id in New Jersey and Brooklyn, and `teams`
    holds only today's name, so a 2005 Knicks log listed a game "vs Brooklyn
    Nets" eight years before the team moved."""
    c = gl_con.con
    c.execute("INSERT INTO teams VALUES ('17','BKN','Brooklyn Nets')")
    c.execute("INSERT INTO games VALUES ('n05',2005,2,'2005-01-10T00:30Z','18','17',100,90,'18',false,'New York')")
    c.execute("INSERT INTO team_box_stats VALUES ('n05',2005,2,'18','17','home')")
    real_games.build_table(c, {"games", "teams"})
    games = game_log(gl_con, {"team": "Knicks", "season": 2005}).data["games"]
    assert [g["opponent"] for g in games] == ["New Jersey Nets"]


@pytest.fixture
def period_rank_ctx(tmp_path: Path) -> TemplateContext:
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
    return TemplateContext(con=c, out_dir=tmp_path)


def test_a_period_ranking_averages_over_games_played_not_games_scored_in(period_rank_ctx: TemplateContext) -> None:
    """The mistake period_split records, in the ranking: counting only the
    games with a made shot in the period drops every scoreless quarter and
    lifts the average. Ace scored in five of six first quarters, so his
    average is 15/6 = 2.5, not 15/5 = 3.0."""
    result = period_leaderboard(period_rank_ctx, {"period": 1, "season": SEASON, "season_type": 3})
    leaders = result.data["leaders"]
    assert [row["player"] for row in leaders] == ["Ace Scorer", "Role Player"]
    assert leaders[0] == {"player": "Ace Scorer", "games": 6, "points": 15, "average": 2.5}
    assert leaders[1]["average"] == 1.0
    assert "led the league in 1st quarter points per game" in (result.answer or "")


def test_a_period_ranking_states_and_applies_its_games_qualifier(period_rank_ctx: TemplateContext) -> None:
    """Cameo averages 3.0, the best here, over two games. A per-game ranking
    without a minimum is whoever played once and scored, so he is out - and
    the answer says which minimum it used rather than leaving a reader to
    wonder why he is missing."""
    result = period_leaderboard(period_rank_ctx, {"period": 1, "season": SEASON, "season_type": 3})
    assert "Cameo Sub" not in str(result.data["leaders"])
    assert result.data["minimum_games"] == 5
    assert "(minimum 5 games)" in (result.answer or "")


def test_a_period_ranking_reads_the_shots_value_from_its_position(period_rank_ctx: TemplateContext) -> None:
    """Every shot here is unlabeled (`points_attempted = 0`) and 26 feet out.
    Read off the label these are worth nothing; read through SHOT_VALUE_SQL
    each is a three, which is the difference between 76.8% and 99.95%
    agreement with ESPN's own quarter scores."""
    result = period_leaderboard(period_rank_ctx, {"period": 1, "season": SEASON, "season_type": 3})
    assert result.data["leaders"][0]["points"] == 15  # five threes, not five zeros


def test_a_period_ranking_narrowed_to_a_team_says_so(period_rank_ctx: TemplateContext) -> None:
    result = period_leaderboard(period_rank_ctx, {"period": 1, "season": SEASON, "season_type": 3, "team": "Golden State Warriors"})
    assert "led the Golden State Warriors in 1st quarter points per game" in (result.answer or "")
    assert result.data["team"] == "Golden State Warriors"


def test_a_period_ranking_covers_a_half_as_well_as_a_quarter(period_rank_ctx: TemplateContext) -> None:
    """Ace's six threes are five in the first quarter and one in the second,
    so a first-half ranking sees all six: 18 points over 6 games."""
    result = period_leaderboard(period_rank_ctx, {"half": 1, "season": SEASON, "season_type": 3})
    assert result.data["leaders"][0] == {"player": "Ace Scorer", "games": 6, "points": 18, "average": 3.0}
    assert "1st half points per game" in (result.answer or "")


def test_a_period_ranking_refuses_a_stat_it_cannot_rank(period_rank_ctx: TemplateContext) -> None:
    """Only points are in shot_chart. Rebounds per quarter would have to be
    derived from plays, at a fidelity period_split already refuses over."""
    with pytest.raises(TemplateUnsupported, match="ranks points only"):
        period_leaderboard(period_rank_ctx, {"period": 1, "season": SEASON, "season_type": 3, "stat": "rebounds"})


def test_a_period_ranking_nobody_qualifies_for_says_so(period_rank_ctx: TemplateContext) -> None:
    """A regular season needs 20 games and this fixture has six postseason
    ones, so the honest answer names the qualifier rather than reading as
    though nobody scored."""
    result = period_leaderboard(period_rank_ctx, {"period": 1, "season": SEASON, "season_type": 2})
    assert result.data["leaders"] == []
    assert "played the 20 games needed to rank" in (result.answer or "")


def test_a_period_is_narrowed_by_a_teammates_absence(period_ctx: TemplateContext) -> None:
    """ "scottie barnes stats 2nd half log without rj" was refused for a slot
    no period template honored, while the relation had been answering exactly
    that narrowing for four other templates since the port. Klay plays e1-e3
    and sits e4-e5, so without him Curry has two games - and the header says
    so, because a half answered over the games a teammate missed and headed as
    though it covered every game is the silent narrowing check_scope exists to
    stop."""
    result = period_split(period_ctx, {"player": "Stephen Curry", "period": 1, "season": SEASON, "season_type": 2, "without": ["Klay Thompson"]})
    assert result.data["games_played"] == 2
    assert "over 2 games" in (result.answer or "") and "without Klay Thompson" in (result.answer or "")
    # Every game, for contrast: five, since e6 is a game he did not play.
    whole = period_split(period_ctx, {"player": "Stephen Curry", "period": 1, "season": SEASON, "season_type": 2})
    assert whole.data["games_played"] == 5 and "without" not in (whole.answer or "")


def test_a_period_is_narrowed_by_a_line_on_a_box_score_column(period_ctx: TemplateContext) -> None:
    """A line on a box-score column narrows which of the player's games the
    period sum covers, the same as every other template on the relation
    (step 3, C2) - previously `_period_split_rows` hard-coded `measures=[]`
    into its own call to `common.scoped_games`, so `below`/`above` reached
    `check_scope`'s declaration and nothing past it. e2 (30 total points) and
    e4 (25) are the only games at or above 24 here; each holds one
    first-quarter three (3 points) - 6 over 2 games, where the whole season
    (5 games) totals 12."""
    c = period_ctx.con
    c.execute("ALTER TABLE player_box_stats ADD COLUMN points INTEGER")
    for event, points in (("e1", 10), ("e2", 30), ("e3", 20), ("e4", 25), ("e5", 15)):
        c.execute("UPDATE player_box_stats SET points = ? WHERE event_id = ? AND athlete_id = '1'", [points, event])
    high = period_split(period_ctx, {"player": "Stephen Curry", "period": 1, "season": SEASON, "season_type": 2, "above": "24 points"})
    assert high.data["games_played"] == 2
    assert high.data["total"] == 6
    assert high.data["measures"] == ["at least 24 points"]
    assert "with at least 24 points" in (high.answer or "")
    low = period_split(period_ctx, {"player": "Stephen Curry", "period": 1, "season": SEASON, "season_type": 2, "below": "24 points"})
    assert low.data["games_played"] == 3
    assert "with under 24 points" in (low.answer or "")
    with pytest.raises(TemplateUnsupported):
        period_split(period_ctx, {"player": "Stephen Curry", "period": 1, "season": SEASON, "season_type": 2, "above": "24 vibes"})


def test_a_period_log_takes_the_end_of_the_season_the_question_asked_for(period_ctx: TemplateContext) -> None:
    """`order` picks which end the rows come from, as it does for game_log.
    Before it was honored, "his first 5 games" showed his last five - a
    different five games, with nothing saying so."""
    recent = period_split(period_ctx, {"player": "Stephen Curry", "period": 1, "season": SEASON, "season_type": 2, "per_game": True, "limit": 2, "order": "recent"})
    first = period_split(period_ctx, {"player": "Stephen Curry", "period": 1, "season": SEASON, "season_type": 2, "per_game": True, "limit": 2, "order": "first"})
    recent_dates = [line.split()[0] for line in (recent.answer or "").splitlines() if line.strip()[:4].isdigit()]
    first_dates = [line.split()[0] for line in (first.answer or "").splitlines() if line.strip()[:4].isdigit()]
    assert len(recent_dates) == 2 and len(first_dates) == 2
    assert first_dates == sorted(first_dates) and recent_dates == sorted(recent_dates, reverse=True)
    assert first_dates[0] < recent_dates[-1]
    assert "the 2 earliest" in (first.answer or "") and "the 2 most recent" in (recent.answer or "")
    # The header still answers the whole season either way.
    assert "over 5 games" in (first.answer or "") and "over 5 games" in (recent.answer or "")


def test_a_period_without_a_teammate_nothing_resolves_refuses_rather_than_dropping_him(period_ctx: TemplateContext) -> None:
    """A name the roster does not hold falls through, exactly as it does for
    every other template on the relation. What must not happen is the name
    being dropped and the period totaled over every game, which would answer
    a wider question than was asked with nothing saying so."""
    with pytest.raises(TemplateUnsupported, match="no player matching"):
        period_split(period_ctx, {"player": "Stephen Curry", "period": 1, "season": SEASON, "season_type": 2, "without": ["Nobody At All"]})


def test_a_teams_half_is_the_two_quarters_of_its_own_linescore(tq_con: TemplateContext) -> None:
    """A team's half had no template and fell through, because the model maps
    "first half" onto period 1 - wrong for a team the same way it is for a
    player. The linescore already holds both quarters, so it is addition, not
    a second source. The Knicks' second halves here are 28+29, 28+28 and
    36+30: 179 across three games."""
    result = team_quarter_points(tq_con, {"team": "Knicks", "half": 2, "season": current_season(), "season_type": 2})
    assert result.data["total"] == 179
    assert [g["points"] for g in result.data["games"]] == [57, 56, 66]
    assert "2nd half" in (result.answer or "")


def test_a_teams_most_in_a_half_is_one_game_not_the_average(tq_con: TemplateContext) -> None:
    """ "Detroit Pistons most points in a first half this season" asks for ONE
    game. The Knicks' first halves are 30+25, 20+20 and 10+32, so the most is
    55 against Boston and the fewest 40, and the answer names the game rather
    than a season average nobody asked for."""
    most = team_quarter_points(tq_con, {"team": "Knicks", "half": 1, "season": current_season(), "season_type": 2, "rank": "most"})
    assert most.data["extreme"] == 55
    assert "scored 55 in the 1st half vs the Boston Celtics on 2026-04-10" in (most.answer or "")
    assert "their most" in (most.answer or "")
    fewest = team_quarter_points(tq_con, {"team": "Knicks", "half": 1, "season": current_season(), "season_type": 2, "rank": "fewest"})
    assert fewest.data["extreme"] == 40 and "their fewest" in (fewest.answer or "")
    # Without a rank it is still the season's scoring, as it always was.
    plain = team_quarter_points(tq_con, {"team": "Knicks", "half": 1, "season": current_season(), "season_type": 2})
    assert "extreme" not in plain.data and plain.data["total"] == 137


def test_a_teams_quarter_is_unchanged_by_halves_arriving(tq_con: TemplateContext) -> None:
    """The quarter path is what it was: one period of the linescore, read by
    number rather than summed."""
    result = team_quarter_points(tq_con, {"team": "Knicks", "period": 1, "season": current_season(), "season_type": 2})
    assert [g["points"] for g in result.data["games"]] == [30, 20, 10]
    assert result.data["period"] == 1


def test_a_tied_extreme_names_every_game_that_reached_it() -> None:
    """Two games at the same high are both the answer. Resolving the tie by
    whichever row sorted first would report one game as though it stood
    alone."""
    from association.query.entities import Entity
    from association.query.templates.games import _team_quarter_points_answer

    games = [
        {"date": "2026-01-02", "opponent": "Boston Celtics", "points": 60},
        {"date": "2026-01-09", "opponent": "Chicago Bulls", "points": 60},
        {"date": "2026-01-16", "opponent": "Miami Heat", "points": 41},
    ]
    result = _team_quarter_points_answer(Entity(id="18", name="New York Knicks"), None, games, periods=(1, 2), period_label="1st half", period_str="2026 regular season", rank="most")
    assert result.data["extreme"] == 60 and len(result.data["extreme_games"]) == 2
    assert "vs the Boston Celtics on 2026-01-02 and vs the Chicago Bulls on 2026-01-09" in (result.answer or "")


# ---------------- step 3, C3: the scoping matrix cannot grow back ----------------


def _c5_shots_ported() -> bool:
    """Whether the parallel "shots" half of step 3, C5 (README_c5.md) has
    landed on this tree - checked at run time rather than assumed, since the
    two halves are built on separate branches and merge independently.
    ``_scoping_game`` is the unported implementation's own marker: it is one
    of the four helpers that README names as going away once ``shot_chart``
    and ``shot_distance`` read the relation
    (``_scoping_game``, ``_shot_chart_event_id``, ``_shot_distance_order_scope``,
    ``_shot_distance_where``), so its absence is a reliable single-point
    signal for "the port has landed" without hand-parsing source."""
    from association.query.templates import shots as shots_module

    return not hasattr(shots_module, "_scoping_game")


def test_templates_on_the_relation_declare_no_scoping_of_their_own() -> None:
    """Step 3, C3. Six templates settle their player and narrow his games
    through the shared steps, and what they honor is declared ONCE
    (RELATION_SCOPING), less an exclusion with a written reason. A template
    that declared its own list would be the first cell of the matrix growing
    back - so its HONORED_SCOPING entry has to be exactly the relation's minus
    its exclusions, and every exclusion has to carry a reason.

    .. versionchanged:: 4.4.0
       Covers ``shot_chart``/``shot_distance`` (step 3, C5) once the parallel
       "shots" branch that ports them has merged - see :func:`_c5_shots_ported`.
       ``extra=set()`` matches what that branch's own README commits to
       (``HONORED_SCOPING["shot_chart"] = _relation_scoping("shot_chart")``),
       and ``RELATION_SCOPING_EXCLUDED`` is read live below, so this does not
       need to guess which cells that branch ends up excluding or why - only
       that the two facts still balance the same equation every other
       relation template does. ``player_stat`` now also carries the
       ``season_type_unstated`` extra beside ``game_log``'s - the season line
       has no "both at once" row, so the slot sends it to box scores the same
       way a ``since`` range already does.
    """
    from association.query.templates.common import RELATION_SCOPING, RELATION_SCOPING_EXCLUDED

    on_the_relation = {
        "game_log": {"season_type_unstated"},
        "player_stat": {"season_type_unstated"},
        "period_split": set(),
        "player_splits": set(),
        "record_when": set(),
        "streak": set(),
    }
    if _c5_shots_ported():
        on_the_relation |= {"shot_chart": set(), "shot_distance": set()}
    for intent, extra in on_the_relation.items():
        excluded = RELATION_SCOPING_EXCLUDED.get(intent, {})
        for slot, reason in excluded.items():
            assert slot in RELATION_SCOPING and reason.strip(), f"{intent} excludes {slot!r} without a reason"
        assert HONORED_SCOPING[intent] == (RELATION_SCOPING | extra) - set(excluded), f"{intent} declares scoping of its own"


def test_until_is_declared_wherever_since_is() -> None:
    """A new scoping dimension is one clause on ``_Span`` plus a template
    turning it on - never a slot honored for ``since`` and silently dropped
    for ``until``, the same shape ``RELATION_SCOPING`` exists to stop for
    every other cell. ``until`` closes a ``since``-bounded range at the far
    end (``router._validate_range``'s closed forms - "2019-20 to 2023-24",
    "between 2020 and 2024", "2020-2024", two adjacent bare years) and is
    read nowhere ``since`` is not: ``period_split`` excludes both for the
    same reason (its accuracy caveat is measured per season), and every
    player-relation template that honors ``since`` honors ``until`` beside
    it - checked by reading the source dicts rather than trusting a comment.

    Scoped to the PLAYER relation's own structures (``RELATION_SCOPING``,
    ``RELATION_SCOPING_EXCLUDED``, and ``HONORED_SCOPING`` for the intents on
    it) rather than ``HONORED_SCOPING`` as a whole: the team relation's
    ``since``/``until`` pairing is ``TEAM_RELATION_SCOPING``'s own claim, made
    and tested separately.

    .. versionadded:: 4.4.0
    """
    from association.query.templates.common import RELATION_SCOPING, RELATION_SCOPING_EXCLUDED

    assert {"since", "until"} <= RELATION_SCOPING
    for intent, excluded in RELATION_SCOPING_EXCLUDED.items():
        assert ("since" in excluded) == ("until" in excluded), f"{intent} excludes since XOR until"
    on_the_relation = ["game_log", "player_stat", "period_split", "player_splits", "record_when", "streak"]
    if _c5_shots_ported():
        on_the_relation += ["shot_chart", "shot_distance"]
    for intent in on_the_relation:
        honored = HONORED_SCOPING[intent]
        assert ("since" in honored) == ("until" in honored), f"{intent} honors since XOR until"


def test_templates_on_the_relation_do_not_narrow_it_themselves() -> None:
    """The other half of C3: a template on the relation reads its scoping
    slots only to label the answer or to decide what a bare threshold means.
    The NARROWING - the clause on the relation's rows - is the shared step's,
    so a slot the relation learns reaches every template at once. A direct
    call to the narrowing function, or a hand-written clause on an opponent,
    venue, starter or date column, is a template teaching itself one slot -
    which is the O(templates x slots) matrix this step removed.

    .. versionchanged:: 4.4.0
       Also forbids a hand-written clause on the TEAM relation's own opponent,
       venue and date columns (``tg.opponent_id``, ``tg.side``,
       ``tg.eastern_date``) - step 3, C4 gave game_log's team half the same
       shared narrowing (``common.scoped_team`` / ``common.team_games``) the
       player half already had, and ``_source_with_private_steps`` now walks
       into it instead of stopping short.

    .. versionchanged:: 4.4.0
       Forbids hand-narrowing a shot read to one game (``event_id = ?``) or to
       one player-season on the shot table (``athlete_id = ? AND season = ?``)
       - the shape ``shots._scoping_game`` and ``shots._shot_distance_where``
       wrote before step 3, C5's "shots" half ported ``shot_chart`` and
       ``shot_distance`` onto the relation. In a separate list and a separate
       loop, scoped to those two templates' own source only (and only once
       that parallel branch has landed - :func:`_c5_shots_ported`, so the
       merge needs no second edit here): folded into the ``forbidden`` tuple
       above, ``"athlete_id = ? AND season = ?"`` false-positived on
       ``players._season_row``, an unrelated read of the SEASON-LINE table
       that happens to share the same three-column WHERE shape by coincidence.
    """

    from association.query.templates import TEMPLATES

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
    for intent in ("game_log", "player_stat", "period_split", "player_splits", "record_when", "streak"):
        # The template and the private steps it calls, transitively -
        # game_log's player narrowing lives in _game_log_player and its team
        # narrowing in _game_log_team, neither in game_log itself.
        source = _source_with_private_steps(TEMPLATES[intent])
        for token in forbidden:
            assert token not in source, f"{intent} narrows the relation itself ({token!r}); use scoped_games / team_games"
        # The quieter way to narrow by hand: hand the shared step a dict BUILT
        # here instead of the question's slots. period_split did exactly that -
        # `{"venue": venue, "without": without, "split": split}` - so `game_n`
        # never reached the relation while HONORED_SCOPING said it did, and
        # the token check above saw nothing, because no clause was written.
        # The slots dict is the relation's input; a template passes it whole.
        # `team_games` joins this check with step 3, C4b, whose port of the
        # team half is where its `{"venue": venue}` callers are rewritten.
        assert not re.search(r'(scoped_games|condition_player)\([^\n]*\{"', source), f"{intent} hands the shared step a hand-built dict; pass the question's slots"

    if _c5_shots_ported():
        shot_forbidden = ("event_id = ?", "athlete_id = ? AND season = ?")
        for intent in ("shot_chart", "shot_distance"):
            source = _source_with_private_steps(TEMPLATES[intent])
            for token in shot_forbidden:
                assert token not in source, f"{intent} narrows the relation itself ({token!r}); use scoped_games / team_games"

    # The compose package (association.query.compose - the compiler landed
    # from the skeleton spike) sits above the templates but reads the same
    # shared steps (scoped_games, league_games, and - step 3, K1's team
    # subject, compose/team.py - scoped_team/team_games), and the same
    # discipline applies: it narrows the relation only through them, never by
    # writing its own clause on an opponent, venue, starter or date column.
    # It is not registered in TEMPLATES, so it is walked by module source
    # directly rather than through _source_with_private_steps.
    import inspect

    import association.query.compose.adapt as _compose_adapt
    import association.query.compose.core as _compose_core
    import association.query.compose.move as _compose_move
    import association.query.compose.team as _compose_team

    for module in (_compose_core, _compose_adapt, _compose_move, _compose_team):
        source = inspect.getsource(module)
        for token in forbidden:
            assert token not in source, f"{module.__name__} narrows the relation itself ({token!r}); use scoped_games / league_games / scoped_team / team_games"
        assert not re.search(r'(scoped_games|condition_player|league_games|scoped_team|team_games)\([^\n]*\{"', source), (
            f"{module.__name__} hands the shared step a hand-built dict; pass the question's slots"
        )


def _source_with_private_steps(handler: Any) -> str:
    """A template's source plus every private function of its module it
    reaches, transitively.

    .. versionchanged:: 4.4.0
       game_log's TEAM half (``_game_log_team``, ``_team_game_log``,
       ``_team_game_log_mixed``) is no longer stopped at: step 3, C4 gave it
       shared steps of its own (``common.scoped_team``, ``common.team_games``),
       so it is walked exactly like the player half. The walker still stops on
       its own at ``team_games`` and ``scoped_team`` themselves, and at every
       other shared step - the regex below only follows a name that STARTS
       WITH an underscore, and none of the shared narrowing steps do, by
       design (see their own module docstrings).
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
