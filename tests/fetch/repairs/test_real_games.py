"""What `real_games` keeps, and what it refuses to call a game.

Every row in the fixture is one of the shapes measured in the real warehouse
and catalogued in DATA.md ("`games` carries placeholder, duplicate and phantom
rows"). The two that must SURVIVE matter as much as the ones that must go: the
1988-1992 archive is stored date-only with no box scores anywhere, and the real
games from 1994 on that ESPN simply lacks a box score for (the whole 1997 ECF
among them) carry a real tip time.
"""

from __future__ import annotations

import duckdb
import pytest

from association.fetch.repairs import real_games

# 40 is in no franchise: the shape of ESPN's 1202, 75, 1300, 100, 31, 125, 83.
REAL, ALSO_REAL, GHOST_TEAM = "1", "2", "40"


def _events(con: duckdb.DuckDBPyConnection) -> list[str]:
    return [r[0] for r in con.execute("SELECT event_id FROM real_games ORDER BY event_id").fetchall()]


@pytest.fixture
def con() -> duckdb.DuckDBPyConnection:
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE teams (team_id VARCHAR, display_name VARCHAR)")
    c.execute("INSERT INTO teams VALUES ('1','Home Team'),('2','Away Team')")
    c.execute(
        "CREATE TABLE games (event_id VARCHAR, season BIGINT, season_type BIGINT, date VARCHAR, "
        "home_team_id VARCHAR, away_team_id VARCHAR, home_score BIGINT, away_score BIGINT, winner_team_id VARCHAR)"
    )
    c.executemany(
        "INSERT INTO games VALUES (?,?,?,?,?,?,?,?,?)",
        [
            # Kept: an ordinary game.
            ("ok", 2020, 2, "2020-01-05T00:30Z", REAL, ALSO_REAL, 110, 100, REAL),
            # Dropped: a 0-0 placeholder with no winner, on a date of its own, so
            # it is not merely collapsed as some other game's duplicate.
            ("placeholder", 2020, 2, "2020-01-07T17:00Z", REAL, ALSO_REAL, 0, 0, None),
            # Dropped: a team id no franchise has, on each side in turn.
            ("ghost_home", 2020, 2, "2020-01-09T00:30Z", GHOST_TEAM, ALSO_REAL, 99, 90, GHOST_TEAM),
            ("ghost_away", 2020, 2, "2020-01-11T00:30Z", REAL, GHOST_TEAM, 99, 90, REAL),
            # Dropped: a phantom - date-only stamp, no box score, in a season
            # that has box scores. Summer (EDT) and winter (EST) spellings.
            ("phantom_edt", 2020, 3, "2020-05-01T04:00Z", REAL, ALSO_REAL, 106, 103, REAL),
            ("phantom_est", 2020, 2, "2020-01-13T05:00Z", REAL, ALSO_REAL, 92, 83, REAL),
            # Kept: a real game ESPN has no box score for. Its tip time is real,
            # which is the whole difference - this is the 1997 ECF's shape.
            ("boxless_real", 2020, 3, "2020-05-03T23:30Z", REAL, ALSO_REAL, 95, 90, REAL),
            # Kept: date-only, but it HAS a box score. Ten 2026 games look like this.
            ("dateless_played", 2020, 2, "2020-01-15T04:00Z", REAL, ALSO_REAL, 101, 99, REAL),
            # Kept: the 1988-1992 archive - date-only and boxless, in a season
            # with no box scores at all, so there is no evidence of a phantom.
            ("archive", 1991, 3, "1991-05-01T04:00Z", REAL, ALSO_REAL, 100, 90, REAL),
            # Collapsed: one game under two event ids, an hour apart. The copy
            # with the box score is the one to keep.
            ("dup_boxed", 2021, 2, "2021-01-04T17:30Z", REAL, ALSO_REAL, 102, 83, REAL),
            ("dup_empty", 2021, 2, "2021-01-04T18:30Z", REAL, ALSO_REAL, 102, 83, REAL),
            # Kept, both: one game under two SEASON labels, as ESPN answers 1993
            # and 1994. A phantom season is coverage.py's job, not this one.
            ("two_labels", 1993, 2, "1994-01-10T00:30Z", REAL, ALSO_REAL, 100, 90, REAL),
            ("two_labels", 1994, 2, "1994-01-10T00:30Z", REAL, ALSO_REAL, 100, 90, REAL),
        ],
    )
    c.execute("CREATE TABLE player_box_stats (event_id VARCHAR, season BIGINT, season_type BIGINT, athlete_id VARCHAR)")
    c.executemany(
        "INSERT INTO player_box_stats VALUES (?,?,?,?)",
        [
            ("ok", 2020, 2, "p1"),
            ("dateless_played", 2020, 2, "p1"),
            ("dup_boxed", 2021, 2, "p1"),
            ("two_labels", 1993, 2, "p1"),
            ("two_labels", 1994, 2, "p1"),
            # 2020's postseason has box scores, which is what makes phantom_edt
            # a phantom rather than a game older than the box scores.
            ("some_2020_playoff_game", 2020, 3, "p1"),
        ],
    )
    real_games.build_table(c, {"games", "teams", "player_box_stats"})
    return c


def test_a_placeholder_with_no_winner_is_not_a_game(con: duckdb.DuckDBPyConnection) -> None:
    """134 regular-season rows are scored 0-0 with no winner, 133 of them
    involving Chicago. Counted, each is a meeting nobody won."""
    assert "placeholder" not in _events(con)


def test_a_team_id_no_franchise_has_is_not_a_game(con: duckdb.DuckDBPyConnection) -> None:
    """`teams` holds the 30 CURRENT franchises and a relocation keeps its ESPN
    id, so this drops no historical franchise - only ESPN's 1202, 75, 1300,
    100, 31, 125 and 83, which are in no season's league."""
    kept = _events(con)
    assert "ghost_home" not in kept and "ghost_away" not in kept


def test_a_date_only_stamp_with_no_box_score_is_a_phantom(con: duckdb.DuckDBPyConnection) -> None:
    """Both spellings of midnight Eastern. Four of the eleven real phantoms are
    winter games stamped 05:00Z, so testing 04:00Z alone would pass while
    letting them through."""
    kept = _events(con)
    assert "phantom_edt" not in kept and "phantom_est" not in kept


def test_a_real_game_with_no_box_score_survives(con: duckdb.DuckDBPyConnection) -> None:
    """The 1997 ECF, the 1995 Finals Game 5 and about 30 regular-season games
    have no box score and are real. Their tip times are real too."""
    assert "boxless_real" in _events(con)


def test_a_date_only_game_that_was_played_survives(con: duckdb.DuckDBPyConnection) -> None:
    """Ten 2026 games carry a date-only stamp and full box scores."""
    assert "dateless_played" in _events(con)


def test_the_pre_box_score_archive_survives(con: duckdb.DuckDBPyConnection) -> None:
    """Every 1988-1992 game is date-only and boxless, because ESPN publishes no
    box scores before 1993-94 - and coverage.py declares those playoffs
    answerable from 1989. Dropping them would delete 595 real games."""
    assert "archive" in _events(con)


def test_one_game_under_two_event_ids_is_counted_once(con: duckdb.DuckDBPyConnection) -> None:
    """A team cannot play the same opponent twice on one Eastern date."""
    assert _events(con).count("dup_boxed") + _events(con).count("dup_empty") == 1


def test_the_surviving_duplicate_is_the_one_with_a_box_score(con: duckdb.DuckDBPyConnection) -> None:
    """2003-01-04 DAL-PHI is 24 player rows under `230104006` and none under
    `400222658`. Keeping the empty one would orphan the box scores."""
    kept = _events(con)
    assert "dup_boxed" in kept and "dup_empty" not in kept


def test_a_season_under_two_labels_keeps_both_rows(con: duckdb.DuckDBPyConnection) -> None:
    """Season 1993 is a phantom SEASON, not a phantom row: its rows are a
    healthy copy of 1994's under a second label. coverage.py declares it and
    team_metrics collapses it; hiding it here would take it away from both."""
    seasons = [r[0] for r in con.execute("SELECT season FROM real_games WHERE event_id = 'two_labels' ORDER BY season").fetchall()]
    assert seasons == [1993, 1994]


def test_the_columns_are_exactly_games_columns(con: duckdb.DuckDBPyConnection) -> None:
    """A drop-in replacement: anything reading `g.*` off `games` must get the
    same row shape, or swapping the name silently changes an answer's data."""
    assert [r[0] for r in con.execute("DESCRIBE real_games").fetchall()] == [r[0] for r in con.execute("DESCRIBE games").fetchall()]


def test_without_box_scores_the_phantom_rule_does_not_fire(con: duckdb.DuckDBPyConnection) -> None:
    """A warehouse with no player box scores has no evidence that a game is
    missing one, so the rule is left out rather than guessed at. The other two
    filters still apply."""
    real_games.build_table(con, {"games", "teams"})
    kept = _events(con)
    assert "phantom_edt" in kept and "phantom_est" in kept
    assert "placeholder" not in kept and "ghost_home" not in kept


def test_the_table_is_skipped_when_teams_is_missing() -> None:
    """Without `teams` there is nothing to check a team id against, and a list
    that skipped that check would be a different list under the same name.

    `games` is given every column the filter reads on purpose. Written with a
    one-column stub it passed whether or not the `teams` check was there at
    all, because the column guard below skipped the build first - green, and
    testing nothing."""
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE games (event_id VARCHAR, season BIGINT, season_type BIGINT, date VARCHAR, home_team_id VARCHAR, away_team_id VARCHAR, winner_team_id VARCHAR)")
    real_games.build_table(c, {"games"})
    assert c.execute("SELECT count(*) FROM information_schema.tables WHERE table_name = 'real_games'").fetchone() == (0,)


def test_the_table_is_skipped_when_games_lacks_a_column() -> None:
    """A Parquet tree can hold a `games` file with only the columns somebody
    wrote by hand - five of the warehouse's own tests build one. Without this,
    the build raised a BinderException from inside `data load`, which is a long
    way from the column that was actually missing."""
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE games (event_id VARCHAR, season BIGINT, season_type BIGINT)")
    c.execute("CREATE TABLE teams (team_id VARCHAR)")
    real_games.build_table(c, {"games", "teams"})
    assert c.execute("SELECT count(*) FROM information_schema.tables WHERE table_name = 'real_games'").fetchone() == (0,)


def test_the_real_games_eastern_day_is_the_shared_rule() -> None:
    """The Eastern date a duplicate is judged on has to be the same one the
    NetPoints matcher and every answer use, or two parts of the warehouse
    disagree about which day a game happened."""
    from association.season import eastern_date_sql

    assert eastern_date_sql("g.date") in real_games.real_games_sql(box_scores=True)
