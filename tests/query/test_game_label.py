"""Pins :func:`association.query.game_label.game_label` - the sentence a
single-game shot chart or fingerprint uses to say which game was drawn
(``ISSUES.md`` #155: a bare event id told a reader nothing)."""

from __future__ import annotations

import duckdb
import pytest

from association.query.game_label import game_label


@pytest.fixture
def con() -> duckdb.DuckDBPyConnection:
    """One game (event 100): Golden State beat Portland 118-104 on a stamp
    that reads as the next UTC day - 2025-04-13T00:30Z is 2025-04-12 Eastern -
    so a wrong stamp-to-date conversion would be caught here too."""
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE games (event_id VARCHAR, season INTEGER, home_team_id VARCHAR, away_team_id VARCHAR, home_score INTEGER, away_score INTEGER, winner_team_id VARCHAR)")
    c.execute("INSERT INTO games VALUES ('100', 2025, '9', '20', 118, 104, '9')")
    c.execute("CREATE TABLE player_game_log (athlete_id VARCHAR, event_id VARCHAR, season INTEGER, team_id VARCHAR, opponent_abbr VARCHAR, game_date VARCHAR)")
    c.execute("INSERT INTO player_game_log VALUES ('1', '100', 2025, '9', 'POR', '2025-04-13T00:30Z'), ('2', '100', 2025, '20', 'GS', '2025-04-13T00:30Z')")
    return c


def test_names_date_opponent_and_result_for_the_winning_side(con: duckdb.DuckDBPyConnection) -> None:
    assert game_label(con, "1", "100") == "2025-04-12 vs POR, W 118-104"


def test_names_the_losing_side_from_the_same_game(con: duckdb.DuckDBPyConnection) -> None:
    """The same row, read from the other team's player: an opponent and a
    result relative to THAT player's own team, not always the home team's."""
    assert game_label(con, "2", "100") == "2025-04-12 vs GS, L 104-118"


def test_a_nonexistent_event_falls_back_to_none(con: duckdb.DuckDBPyConnection) -> None:
    assert game_label(con, "1", "does-not-exist") is None


def test_a_nonexistent_athlete_falls_back_to_none(con: duckdb.DuckDBPyConnection) -> None:
    """A real game, but this athlete has no box-score row for it - the shape a
    placeholder or missing box score takes, per AGENTS.md."""
    assert game_label(con, "nobody", "100") is None


def test_a_warehouse_with_no_player_game_log_falls_back_to_none() -> None:
    """A minimal test fixture, or a warehouse built before this existed, has
    neither table - a duckdb.CatalogException, not a crash or a 'None vs
    None' sentence."""
    assert game_label(duckdb.connect(":memory:"), "1", "100") is None


def test_a_score_not_yet_posted_falls_back_to_none(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("UPDATE games SET home_score = NULL, away_score = NULL WHERE event_id = '100'")
    assert game_label(con, "1", "100") is None
