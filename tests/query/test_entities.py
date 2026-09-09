"""Tests for name -> id resolution, and specifically for the distinction
between find_* (caller decides) and resolve_* (never guesses)."""

import duckdb
import pytest

from association.query.entities import (
    Ambiguous,
    Entity,
    NotFound,
    find_players,
    nicknames_in,
    override_nicknames,
    resolve_player,
    resolve_team,
)


@pytest.fixture
def con() -> duckdb.DuckDBPyConnection:
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    c.execute("CREATE TABLE teams (team_id VARCHAR, abbreviation VARCHAR, display_name VARCHAR)")
    # 6-9 exist for the word-boundary ranking: "Ball" lands inside "Ceballos"
    # by accident, and "Alexander" starts a word in a hyphenated surname.
    c.execute(
        "INSERT INTO players VALUES ('1','Stephen Curry'),('2','Seth Curry'),('3','Luka Doncic'),('4','Jaylen Brown'),('5','Jaylen Brown Jr.'),"
        "('6','Cedric Ceballos'),('7','LaMelo Ball'),('8','Nickeil Alexander-Walker'),('11','Kyle Alexander')"
    )
    c.execute("INSERT INTO teams VALUES ('13','LAL','Los Angeles Lakers'),('12','LAC','LA Clippers'),('9','GS','Golden State Warriors')")
    return c


def test_resolve_player_refuses_to_guess_between_two_real_players(con: duckdb.DuckDBPyConnection) -> None:
    """ "Curry" is Seth and Stephen. A leaderboard row attributed to the wrong
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
    # The chart path relies on this ordering: it narrows these candidates to
    # the ones with data for the season asked about, and falls back to the
    # first when that leaves nobody.
    assert [c.name for c in find_players(con, "Curry")] == ["Seth Curry", "Stephen Curry"]


def test_a_name_that_starts_a_word_beats_one_it_only_lands_inside(con: duckdb.DuckDBPyConnection) -> None:
    """Substring matching kept "Ball" honestly ambiguous - between LaMelo Ball
    and Cedric Ceballos, which is not a question anybody would ask. Measured
    against the warehouse, this changed 46 surnames' best match and every one
    of them was an improvement: "Bey" was Mike Tobey, "Ford" was Al Horford."""
    assert [c.name for c in find_players(con, "Ball")] == ["LaMelo Ball"]
    assert resolve_player(con, "Ball") == Entity(id="7", name="LaMelo Ball")


def test_both_halves_of_a_hyphenated_name_start_a_word(con: duckdb.DuckDBPyConnection) -> None:
    """The boundary is any non-letter rather than a space, so "Alexander"
    reaches Nickeil Alexander-Walker and Shai Gilgeous-Alexander. Requiring a
    space would have demoted both below Kyle Alexander and then dropped them
    entirely."""
    assert {c.name for c in find_players(con, "Alexander")} == {"Kyle Alexander", "Nickeil Alexander-Walker"}
    assert isinstance(resolve_player(con, "Alexander"), Ambiguous)


def test_every_declared_availability_names_a_real_table() -> None:
    """The table name is interpolated into SQL, so a typo is a runtime error on
    a path only an ambiguous name reaches. Checked against the query package's
    own list of tables rather than a warehouse, so it stays offline."""
    from association.query.fingerprint import FINGERPRINT_AVAILABILITY
    from association.query.prompt import KNOWN_TABLES
    from association.query.shotchart import SHOT_AVAILABILITY

    for available in (SHOT_AVAILABILITY, FINGERPRINT_AVAILABILITY):
        assert available.table in KNOWN_TABLES, available


def test_an_interior_match_still_counts_when_nothing_starts_a_word(con: duckdb.DuckDBPyConnection) -> None:
    """Ranking, not filtering. A fragment that matches nobody at a word
    boundary still finds the people it does match, so a partial name typed
    mid-word is answered rather than reported missing."""
    assert [c.name for c in find_players(con, "urry")] == ["Seth Curry", "Stephen Curry"]


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


def test_a_first_name_one_player_owns_resolves_but_a_shared_surname_still_asks(con: duckdb.DuckDBPyConnection) -> None:
    """The rule the table follows, and the line it draws.

    "Luka" is shared with Luka Garza and Luka Samanic, and it resolves anyway:
    a question carrying only "luka" cannot reliably have meant either of them,
    so asking buys nothing. "Curry" stays out because Seth and Stephen are both
    plausible answers to a question about Currys, and "Brown" stays out because
    a surname on its own is not something anyone can expect to complete.

    This reverses the earlier rule, which kept every first name out. What was
    actually rejected then - and is still rejected - is deriving the table from
    prominence; a hand-picked entry is a different thing.
    """
    from association.query.entities import PLAYER_NICKNAMES

    assert PLAYER_NICKNAMES["luka"] == "Luka Doncic"
    assert "curry" not in PLAYER_NICKNAMES and "brown" not in PLAYER_NICKNAMES
    assert isinstance(resolve_player(con, "Curry"), Ambiguous)
    assert isinstance(resolve_player(con, "Brown"), Ambiguous)


def test_nicknames_are_found_in_the_question_as_whole_words(con: duckdb.DuckDBPyConnection) -> None:
    assert nicknames_in("Show me The Answer's avg points") == ["Allen Iverson"]
    assert nicknames_in("Compare SGA and Wemby") == ["Shai Gilgeous-Alexander", "Victor Wembanyama"]
    # Substrings must not count, or "ant" matches Durant and Antetokounmpo.
    assert nicknames_in("show me the notebook") == []
    assert nicknames_in("how many points did Durant average") == []
    # A key ending in a non-word character still needs a boundary after it.
    assert nicknames_in("what are A.I.'s numbers") == ["Allen Iverson"]


def test_override_replaces_a_player_the_router_invented() -> None:
    """The reason this exists. Measured against qwen2.5:3b, "Show me The
    Answer's avg points" routed to `player='Klay Thompson'` - a real player,
    resolving cleanly, answered confidently. The nickname is gone by then, so
    nothing downstream of the router can catch it; the question is the only
    place it still exists."""
    slots = {"stat": "points", "player": "Klay Thompson"}
    changed = override_nicknames("Show me The Answer's avg points", slots)
    assert changed == [("Klay Thompson", "Allen Iverson")]
    assert slots["player"] == "Allen Iverson"


def test_override_is_a_no_op_when_the_router_already_agrees() -> None:
    slots = {"player": "Luka Doncic"}
    assert override_nicknames("Show me luka's avg points", slots) == []
    assert slots["player"] == "Luka Doncic"


def test_override_leaves_a_single_slot_alone_when_the_question_names_two_players() -> None:
    """Nothing says which of two nicknames a single `player` slot refers to,
    and guessing wrong is the same bug this is here to fix."""
    slots = {"player": "Klay Thompson"}
    assert override_nicknames("Compare SGA and Wemby", slots) == []
    assert slots["player"] == "Klay Thompson"


def test_override_fills_a_players_list_positionally_when_the_counts_match() -> None:
    slots = {"players": ["SGA", "Klay Thompson"]}
    changed = override_nicknames("Compare SGA and Wemby", slots)
    assert slots["players"] == ["Shai Gilgeous-Alexander", "Victor Wembanyama"]
    assert changed == [("SGA", "Shai Gilgeous-Alexander"), ("Klay Thompson", "Victor Wembanyama")]


def test_override_leaves_a_players_list_alone_when_the_counts_disagree() -> None:
    slots = {"players": ["Kobe Bryant", "Michael Jordan", "Somebody Else"]}
    assert override_nicknames("Compare Kobe and MJ", slots) == []
    assert slots["players"] == ["Kobe Bryant", "Michael Jordan", "Somebody Else"]


def test_override_does_nothing_without_a_nickname() -> None:
    slots = {"player": "Jaylen Brown"}
    assert override_nicknames("How many points does Jaylen Brown average?", slots) == []
    assert slots["player"] == "Jaylen Brown"
