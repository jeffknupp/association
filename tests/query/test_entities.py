"""Tests for name -> id resolution, and specifically for the distinction
between find_* (caller decides) and resolve_* (never guesses)."""

import duckdb
import pytest

from association.query.entities import Ambiguous, Entity, NotFound, find_players, resolve_player, resolve_team


@pytest.fixture
def con() -> duckdb.DuckDBPyConnection:
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    c.execute("CREATE TABLE teams (team_id VARCHAR, abbreviation VARCHAR, display_name VARCHAR)")
    c.execute("INSERT INTO players VALUES ('1','Stephen Curry'),('2','Seth Curry'),('3','Luka Doncic'),('4','Jaylen Brown'),('5','Jaylen Brown Jr.')")
    c.execute("INSERT INTO teams VALUES ('13','LAL','Los Angeles Lakers'),('12','LAC','LA Clippers'),('9','GS','Golden State Warriors')")
    return c


def test_resolve_player_refuses_to_guess_between_two_real_players(con: duckdb.DuckDBPyConnection) -> None:
    """"Curry" is Seth and Stephen. A leaderboard row attributed to the wrong
    one is indistinguishable from a right answer, so this must not pick."""
    got = resolve_player(con, "Curry")
    assert isinstance(got, Ambiguous) and got.candidates == ["Seth Curry", "Stephen Curry"]


def test_resolve_player_matches_every_token(con: duckdb.DuckDBPyConnection) -> None:
    assert resolve_player(con, "Stephen Curry") == Entity(id="1", name="Stephen Curry")


def test_exact_full_name_beats_a_substring_sibling(con: duckdb.DuckDBPyConnection) -> None:
    # "Jaylen Brown" also substring-matches "Jaylen Brown Jr.", but the user
    # named one of them exactly.
    assert resolve_player(con, "Jaylen Brown") == Entity(id="4", name="Jaylen Brown")


def test_resolve_player_reports_not_found(con: duckdb.DuckDBPyConnection) -> None:
    assert resolve_player(con, "Nobody At All") == NotFound(query="Nobody At All")


def test_empty_text_is_not_found_rather_than_everyone(con: duckdb.DuckDBPyConnection) -> None:
    assert find_players(con, "   ") == []
    assert isinstance(resolve_player(con, "   "), NotFound)


def test_find_players_returns_all_candidates_best_first(con: duckdb.DuckDBPyConnection) -> None:
    # render_shot_chart relies on this: a chart of the wrong Curry is visible
    # on sight, so it takes the first and names the rest.
    assert [c.name for c in find_players(con, "Curry")] == ["Seth Curry", "Stephen Curry"]


def test_resolve_team_by_name_and_by_abbreviation(con: duckdb.DuckDBPyConnection) -> None:
    assert resolve_team(con, "Lakers") == Entity(id="13", name="Los Angeles Lakers")
    assert resolve_team(con, "LAL") == Entity(id="13", name="Los Angeles Lakers")


def test_resolve_team_by_id(con: duckdb.DuckDBPyConnection) -> None:
    assert resolve_team(con, "13") == Entity(id="13", name="Los Angeles Lakers")


def test_resolve_team_refuses_an_ambiguous_substring(con: duckdb.DuckDBPyConnection) -> None:
    got = resolve_team(con, "LA")
    assert isinstance(got, Ambiguous) and set(got.candidates) == {"LA Clippers", "Los Angeles Lakers"}


def test_resolve_team_reports_not_found(con: duckdb.DuckDBPyConnection) -> None:
    assert resolve_team(con, "Not A Team") == NotFound(query="Not A Team")


def test_nickname_resolves_to_the_player_it_unambiguously_means(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("INSERT INTO players VALUES ('9','Shai Gilgeous-Alexander'),('10','Victor Wembanyama')")
    assert resolve_player(con, "SGA") == Entity(id="9", name="Shai Gilgeous-Alexander")
    assert resolve_player(con, "wemby") == Entity(id="10", name="Victor Wembanyama")


def test_nickname_matches_the_whole_query_not_a_substring(con: duckdb.DuckDBPyConnection) -> None:
    # "book" means Devin Booker; "notebook" means nothing.
    assert isinstance(resolve_player(con, "notebook"), NotFound)


def test_ordinary_first_names_are_deliberately_not_nicknames(con: duckdb.DuckDBPyConnection) -> None:
    """"Luka" and "Curry" are shared with real players; asking is the honest
    answer, and a curated table must not quietly turn into a popularity guess."""
    from association.query.entities import PLAYER_NICKNAMES

    assert "luka" not in PLAYER_NICKNAMES and "curry" not in PLAYER_NICKNAMES
    assert isinstance(resolve_player(con, "Curry"), Ambiguous)
