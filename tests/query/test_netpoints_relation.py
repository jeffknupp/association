"""The NetPoints relation's reads (:mod:`association.query.netpoints`), each
over a fixture warehouse in the shape of the real tables: the three a
player's NetPoints are read from, the three a fingerprint is, and a
comparison's NetPoints rows (from ``season_line``, the same day). These hold
what each returns, apart from the readers that say it.
"""

from __future__ import annotations

import duckdb
import pytest

from association.query import netpoints


@pytest.fixture
def con() -> duckdb.DuckDBPyConnection:
    """Two players' NetPoints in 2026: the season line (on its own STRING
    season type), the season fingerprint (no season type at all) for two
    categories, and three games of the per-game tables (numeric 2/3), the
    middle one a postseason game."""
    c = duckdb.connect(":memory:")
    c.execute(
        "CREATE TABLE net_points_player (athlete_id VARCHAR, season INTEGER, net_points_season_type VARCHAR, overall DOUBLE, offense DOUBLE, defense DOUBLE, "
        "overall_per_100_poss DOUBLE, total_minutes DOUBLE, games INTEGER, offense_per_100_poss DOUBLE, defense_per_100_poss DOUBLE)"
    )
    c.execute("INSERT INTO net_points_player VALUES ('1', 2026, 'Regular Season', 10.0, 7.0, 3.0, 1.5, 2000.0, 70, 1.0, 0.5), ('1', 2026, 'Postseason', 2.0, 1.0, 1.0, 0.5, 300.0, 9, 0.25, 0.25)")
    columns = ", ".join(f"{category}_{side}_net_pts DOUBLE" for category in ("total", "rim", "corner") for side in ("o", "d", "t"))
    c.execute(f"CREATE TABLE net_points_player_fingerprint (athlete_id VARCHAR, season INTEGER, minutes DOUBLE, total_poss DOUBLE, {columns})")
    c.execute("INSERT INTO net_points_player_fingerprint VALUES ('1', 2026, 2000.0, 4000.0, 1, 2, 3, 4, 5, 6, 7, 8, 9), ('2', 2026, 900.0, 1800.0, 9, 8, 7, 6, 5, 4, 3, 2, 1)")
    c.execute("CREATE TABLE games (event_id VARCHAR, season INTEGER, season_type INTEGER, date VARCHAR)")
    c.execute("INSERT INTO games VALUES ('g1', 2026, 2, '2026-01-05'), ('g2', 2026, 3, '2026-04-25'), ('g3', 2026, 2, '2026-03-05')")
    c.execute(
        "CREATE TABLE net_points_player_game (event_id VARCHAR, athlete_id VARCHAR, season INTEGER, season_type INTEGER, o_net_pts DOUBLE, d_net_pts DOUBLE, t_net_pts DOUBLE, "
        "o_poss DOUBLE, d_poss DOUBLE, t_poss DOUBLE, t_wpa DOUBLE)"
    )
    c.execute(
        "INSERT INTO net_points_player_game VALUES ('g1', '1', 2026, 2, 1, 2, 3, 30, 31, 61, 0.1), ('g2', '1', 2026, 3, 4, 5, 9, 40, 41, 81, 0.2), ('g3', '1', 2026, 2, 6, 7, 13, 50, 51, 101, 0.3)"
    )
    c.execute(
        "CREATE TABLE net_points_player_game_fingerprint (event_id VARCHAR, season INTEGER, season_type INTEGER, athlete_id VARCHAR, category VARCHAR, "
        "o_net_pts DOUBLE, d_net_pts DOUBLE, t_net_pts DOUBLE)"
    )
    for event_id, season_type, size in (("g1", 2, 1.0), ("g2", 3, 2.0), ("g3", 2, 3.0)):
        for category, scale in (("total", 1.0), ("rim", 10.0)):
            c.execute("INSERT INTO net_points_player_game_fingerprint VALUES (?, 2026, ?, '1', ?, ?, ?, ?)", [event_id, season_type, category, size * scale, -size * scale, 0.0])
    return c


def test_a_season_line_is_read_on_its_own_string_season_type(con: duckdb.DuckDBPyConnection) -> None:
    """The regular season's line and the postseason's are two rows told apart
    by the label, never the number every other table uses."""
    assert netpoints.season_totals(con, "1", 2026, 2) == (10.0, 7.0, 3.0, 1.5, 2000.0, 70)
    assert netpoints.season_totals(con, "1", 2026, 3) == (2.0, 1.0, 1.0, 0.5, 300.0, 9)
    assert netpoints.season_totals(con, "2", 2026, 2) is None


def test_a_comparisons_row_is_read_per_100_on_the_tables_own_season_type_and_raises_without_the_table(con: duckdb.DuckDBPyConnection) -> None:
    """A comparison's NetPoints rows (``season_line.NETPOINTS_COMPARE_ROWS``'
    columns), told apart by the season type's label; a missing table raises,
    which the comparison's reader catches and drops the section for."""
    assert netpoints.netpoints_row(con, "1", 2026, 2) == (1.5, 1.0, 0.5)
    assert netpoints.netpoints_row(con, "1", 2026, 3) == (0.5, 0.25, 0.25)
    assert netpoints.netpoints_row(con, "2", 2026, 2) is None
    con.execute("DROP TABLE net_points_player")
    with pytest.raises(duckdb.Error):
        netpoints.netpoints_row(con, "1", 2026, 2)


def test_a_season_fingerprint_reads_each_category_offense_defense_and_total(con: duckdb.DuckDBPyConnection) -> None:
    """The possessions, then each category asked for in turn."""
    assert netpoints.season_categories(con, "1", 2026, ["corner", "rim"]) == (4000.0, 7, 8, 9, 4, 5, 6)
    assert netpoints.season_categories(con, "1", 2025, ["rim"]) is None


def test_a_game_line_is_the_first_or_the_latest_game_of_the_season_type(con: duckdb.DuckDBPyConnection) -> None:
    """Ordered by the game's date, within the season type asked for: the
    postseason game is not the regular season's latest."""
    latest = netpoints.game_line(con, "1", 2026, 2, "recent")
    first = netpoints.game_line(con, "1", 2026, 2, "first")
    assert latest is not None and first is not None
    assert (latest[0], latest[2:]) == ("g3", (6, 7, 13, 50, 51, 0.3))
    assert first[0] == "g1"
    assert netpoints.game_line(con, "1", 2026, 3, "recent") is not None
    assert netpoints.game_line(con, "2", 2026, 2, "recent") is None


def test_the_season_pool_is_every_player_with_the_columns_in_order(con: duckdb.DuckDBPyConnection) -> None:
    """One read for the pool and the players' own rows: everyone in the
    season, minutes and possessions first, then the columns as named."""
    rows = netpoints.season_fingerprints(con, 2026, [("total", "t"), ("rim", "o"), ("corner", "d")])
    assert sorted(rows) == [("1", 2000.0, 4000.0, 3, 4, 8), ("2", 900.0, 1800.0, 7, 6, 2)]
    assert netpoints.season_fingerprints(con, 2025, [("rim", "o")]) == []


def test_the_game_pool_pivots_the_long_table_back_to_columns(con: duckdb.DuckDBPyConnection) -> None:
    """One row per player-game of the season type, with that game's
    possessions and each category's side as a column."""
    rows = netpoints.game_fingerprints(con, 2026, 2, [("total", "o"), ("rim", "d")])
    assert sorted(rows) == [("g1", "1", 61.0, 1.0, -10.0), ("g3", "1", 101.0, 3.0, -30.0)]


def test_the_game_pool_raises_where_the_opt_in_tables_are_missing() -> None:
    """The loader names that cause; the relation does not swallow it."""
    with pytest.raises(duckdb.CatalogException):
        netpoints.game_fingerprints(duckdb.connect(":memory:"), 2026, 2, [("rim", "o")])


def test_a_fingerprint_game_is_dated_from_the_games_table(con: duckdb.DuckDBPyConnection) -> None:
    """The first or latest game with a per-game fingerprint, by real date."""
    assert netpoints.fingerprint_game_of(con, "1", 2026, 2, "recent") == ("g3", "2026-03-05")
    assert netpoints.fingerprint_game_of(con, "1", 2026, 2, "first") == ("g1", "2026-01-05")
    assert netpoints.fingerprint_game_of(con, "2", 2026, 2, "recent") is None
