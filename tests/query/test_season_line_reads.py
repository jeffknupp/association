"""The season line's reads, called with a fixture warehouse: each statement
the season line's readers (``compose.seasons``) take is built AND run on the
relation (:mod:`association.query.season_line`), and handed back as the rows
or the one row the reader reads by position (Phase 4, step 5: the readers
ran each statement themselves through the compiler's door until then).
"""

from __future__ import annotations

from collections.abc import Iterator

import duckdb
import pytest

from association.query.player_relation import ResolvedSpan
from association.query.season_line import ADVANCED_STATS, SHOOTING_STATS, advanced_row, career_row, history_rows, history_total, netpoints_row, season_row


@pytest.fixture
def con() -> Iterator[duckdb.DuckDBPyConnection]:
    """One player's three regular seasons and one postseason on the season
    line, two seasons of an advanced stat, and one NetPoints row."""
    con = duckdb.connect()
    con.execute(
        "CREATE TABLE player_season_stats_deduped (athlete_id VARCHAR, season INTEGER, season_type INTEGER, gamesPlayed INTEGER, avgPoints DOUBLE, points DOUBLE, "
        "avgRebounds DOUBLE, totalRebounds DOUBLE, fieldGoalPct DOUBLE, fieldGoalsMade DOUBLE, fieldGoalsAttempted DOUBLE)"
    )
    con.execute(
        "INSERT INTO player_season_stats_deduped VALUES "
        "('1', 2024, 2, 70, 20.0, 1400, 5.0, NULL, 50.0, 500, 1000), "
        "('1', 2025, 2, 80, 25.0, 2000, 6.0, 480, 40.0, 600, 1500), "
        "('1', 2026, 2, 10, 30.0, 300, NULL, NULL, 60.0, 120, 200), "
        "('1', 2025, 3, 12, 28.0, 336, 7.0, 84, 45.0, 90, 200)"
    )
    con.execute("CREATE TABLE player_season_advanced_stats (athlete_id VARCHAR, season INTEGER, season_type INTEGER, games_played INTEGER, ts_pct DOUBLE, true_shooting_attempts DOUBLE)")
    con.execute("INSERT INTO player_season_advanced_stats VALUES ('1', 2025, 2, 80, 0.60, 1000), ('1', 2026, 2, 10, 0.50, 200)")
    con.execute(
        "CREATE TABLE net_points_player (athlete_id VARCHAR, season INTEGER, net_points_season_type VARCHAR, overall_per_100_poss DOUBLE, offense_per_100_poss DOUBLE, defense_per_100_poss DOUBLE)"
    )
    con.execute("INSERT INTO net_points_player VALUES ('1', 2025, 'Regular Season', 3.5, 2.0, 1.5)")
    yield con
    con.close()


def test_a_season_row_is_the_columns_asked_for_in_their_order_or_none(con: duckdb.DuckDBPyConnection) -> None:
    assert season_row(con, "1", ["gamesPlayed", "avgPoints", "points"], 2025, 2) == (80, 25.0, 2000.0)
    assert season_row(con, "1", ["avgPoints", "gamesPlayed"], 2025, 3) == (28.0, 12)
    assert season_row(con, "1", ["gamesPlayed"], 2023, 2) is None
    assert season_row(con, "2", ["gamesPlayed"], 2025, 2) is None


def test_a_career_row_sums_the_regular_seasons_and_names_no_attempts_for_a_line(con: duckdb.DuckDBPyConnection) -> None:
    row, attempted = career_row(con, "1", 2, ["points", "rebounds"], None)
    assert attempted is None
    assert row is not None
    games, first, last, seasons = row[:4]
    assert (games, first, last, seasons) == (160, 2024, 2026, 3)
    # Points per game is the career total over the career's games.
    assert row[4] == pytest.approx(3700 / 160)
    assert row[5] == pytest.approx(3700)
    # A season with no rebounds total falls back to its average times its
    # games, and the per-game figure is over the games that carry one.
    assert row[6] == pytest.approx((5.0 * 70 + 6.0 * 80) / 150)


def test_a_career_row_of_a_percentage_sums_its_makes_and_attempts(con: duckdb.DuckDBPyConnection) -> None:
    row, attempted = career_row(con, "1", 3, [], SHOOTING_STATS["fieldGoalPct"])
    assert attempted is None
    assert row == (12, 2025, 2025, 1, 90.0, 200.0)


def test_an_advanced_row_reads_one_season_or_weights_a_career(con: duckdb.DuckDBPyConnection) -> None:
    spec = ADVANCED_STATS["ts_pct"]
    assert advanced_row(con, "1", ResolvedSpan(season=2025, season_type=2), spec) == (0.60, None, 80, 2025, 2025, 1, 0)
    assert advanced_row(con, "1", ResolvedSpan(season=2024, season_type=2), spec) is None
    career = advanced_row(con, "1", ResolvedSpan(season=None, season_type=2, first=1994, phantom=(1993,)), spec)
    assert career is not None
    assert career[0] == pytest.approx((0.60 * 1000 + 0.50 * 200) / 1200)
    assert career[1:] == (1200.0, 90, 2025, 2026, 2, 0)


def test_history_rows_are_newest_first_and_cut_to_the_seasons_asked(con: duckdb.DuckDBPyConnection) -> None:
    assert history_rows(con, "1", "points", 2, 2026, 2) == [(2026, 10, 30.0), (2025, 80, 25.0)]
    assert history_rows(con, "1", "points", 2, 2025, None) == [(2025, 80, 25.0), (2024, 70, 20.0)]


def test_a_history_total_is_the_career_total_through_the_last_season(con: duckdb.DuckDBPyConnection) -> None:
    assert history_total(con, "1", "points", 2, 2025) == (3400.0,)
    assert history_total(con, "1", "rebounds", 2, 2026) == (pytest.approx(5.0 * 70 + 6.0 * 80),)


def test_a_netpoints_row_reads_the_tables_own_season_type_and_raises_without_the_table(con: duckdb.DuckDBPyConnection) -> None:
    assert netpoints_row(con, "1", 2025, 2) == (3.5, 2.0, 1.5)
    assert netpoints_row(con, "1", 2025, 3) is None
    con.execute("DROP TABLE net_points_player")
    with pytest.raises(duckdb.Error):
        netpoints_row(con, "1", 2025, 2)
