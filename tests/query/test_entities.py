"""Tests for name -> id resolution, and specifically for the distinction
between find_* (caller decides) and resolve_* (never guesses)."""

from typing import Any

import duckdb
import pytest

from association.query.entities import (
    Ambiguous,
    Entity,
    NotFound,
    compared_but_unmatched,
    find_players,
    misread_players,
    nicknames_in,
    no_match,
    override_invented_players,
    override_nicknames,
    players_named_in,
    resolve_player,
    resolve_team,
    restore_dropped_players,
    scope_from_question,
    suggest_players,
    undo_name_completion,
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
        "('6','Cedric Ceballos'),('7','LaMelo Ball'),('8','Nickeil Alexander-Walker'),('11','Kyle Alexander'),"
        # 12-15 are the router's inventions and what it invents them next to.
        # John S. Williams is real, and is what a possessive "Jokic's" matches
        # if a stray one-letter word is allowed to name somebody.
        "('12','Joel Embiid'),('13','Jusuf Nurkic'),('14','John S. Williams'),('15','Klay Thompson'),"
        # 16-17 are the two the measurement caught: "game" lands inside
        # Blossomgame and "with" starts Withey, so an ordinary question reads
        # as naming both unless a span has to equal a whole word.
        "('16','Jaron Blossomgame'),('17','Jeff Withey'),"
        # 18 is the false positive that gates restore_dropped_players: "best"
        # really is somebody's whole surname.
        "('18','Travis Best')"
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


@pytest.fixture
def johnsons() -> duckdb.DuckDBPyConnection:
    """Eleven Johnsons, one more than find_players returns, with the first and
    the eleventh the two who took shots this season. The warehouse has 47
    Johnsons and 71 name words that match more than ten players."""
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    given = ["Aaron", "Brice", "Cameron", "Dakari", "Eric", "Frank", "George", "Hal", "Ivan", "Jalen", "Keldon"]
    c.executemany("INSERT INTO players VALUES (?, ?)", [(str(i), f"{name} Johnson") for i, name in enumerate(given)])
    c.execute("CREATE TABLE shot_chart (athlete_id VARCHAR, season INTEGER)")
    c.execute("INSERT INTO shot_chart VALUES ('0', 2026), ('10', 2026), ('5', 2010)")
    return c


def test_narrowing_sees_every_match_rather_than_the_first_ten(johnsons: duckdb.DuckDBPyConnection) -> None:
    """Narrowing the first MAX_CANDIDATES alphabetically is not elimination: it
    left Aaron as the only Johnson with shots and drew his chart, because
    Keldon - who also has shots - sorted eleventh and was never looked at.
    Measured on the warehouse's 2026 shot charts, that turned 23 ambiguous
    names into a single player: "Davis" drew Anthony Davis with four players
    eligible, "Robinson" drew Duncan Robinson with four."""
    from association.query.entities import Availability
    from association.query.shotchart import resolve_chart_player

    shots = Availability("shot_chart")
    assert len(find_players(johnsons, "Johnson")) == 10
    assert len(find_players(johnsons, "Johnson", limit=None)) == 11
    assert resolve_player(johnsons, "Johnson", shots, 2026) == Ambiguous(query="Johnson", candidates=["Aaron Johnson", "Keldon Johnson"], active=2)
    assert resolve_chart_player(johnsons, "Johnson", shots, 2026) == Ambiguous(query="Johnson", candidates=["Aaron Johnson", "Keldon Johnson"], active=2)


def test_every_player_from_the_season_asked_about_is_named_however_many(johnsons: duckdb.DuckDBPyConnection) -> None:
    """The cap only ever counts away players who could not be the answer. Seven
    Johnsons took shots this season, more than the five a clarification names
    by default, and cutting any of them would hide a real candidate the way
    Stephen Curry was hidden. 2026 has 14 Williamses; this names all of them."""
    from association.query.entities import Availability, clarification
    from association.query.shotchart import resolve_chart_player

    johnsons.execute("INSERT INTO shot_chart SELECT athlete_id, 2026 FROM players WHERE athlete_id IN ('1', '2', '3', '4', '6')")
    shots = Availability("shot_chart")
    asked = resolve_player(johnsons, "Johnson", shots, 2026)
    assert isinstance(asked, Ambiguous) and asked.active == len(asked.candidates) == 7
    sentence = clarification("Johnson", asked.candidates, active=asked.active)
    assert all(name in sentence for name in asked.candidates) and "other" not in sentence

    charted = resolve_chart_player(johnsons, "Johnson", shots, 2026)
    assert isinstance(charted, Ambiguous) and charted.active == 7
    # With no season, nobody played in "the season asked about", and the cap
    # applies as it always did - otherwise this would read out every Johnson
    # who ever took a shot.
    unscoped = resolve_chart_player(johnsons, "Johnson", shots)
    assert isinstance(unscoped, Ambiguous) and unscoped.active == 0


def test_narrowing_against_several_tables_keeps_a_row_in_any_of_them(johnsons: duckdb.DuckDBPyConnection) -> None:
    from association.query.entities import Availability, narrow_to_available

    johnsons.execute("CREATE TABLE player_game_log (athlete_id VARCHAR, season INTEGER)")
    johnsons.execute("INSERT INTO player_game_log VALUES ('3', 2026)")
    everyone = find_players(johnsons, "Johnson", limit=None)
    kept = narrow_to_available(johnsons, everyone, (Availability("shot_chart"), Availability("player_game_log")), 2026)
    assert [c.name for c in kept] == ["Aaron Johnson", "Dakari Johnson", "Keldon Johnson"]


def test_a_span_keeps_anybody_with_a_row_up_to_its_last_season(johnsons: duckdb.DuckDBPyConnection) -> None:
    from association.query.entities import Availability, narrow_to_available

    everyone = find_players(johnsons, "Johnson", limit=None)
    assert [c.name for c in narrow_to_available(johnsons, everyone, Availability("shot_chart"), through=2012)] == ["Frank Johnson"]


def test_a_capped_clarification_counts_what_it_leaves_out_in_english() -> None:
    """ "(1 others also match)" was the exact phrase that hid Stephen Curry."""
    from association.query.entities import clarification

    six = ["Dell Curry", "Eddy Curry", "JamesOn Curry", "Michael Curry", "Seth Curry", "Stephen Curry"]
    assert clarification("Curry", six).endswith("or Seth Curry (1 other also matches)?")
    assert clarification("Curry", [*six, "Wardell Curry"]).endswith("(2 others also match)?")


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
    from association.query import fingerprint, shotchart, templates
    from association.query.entities import Availability
    from association.query.prompt import KNOWN_TABLES

    # Every one each module declares, tuples included, rather than a list here
    # that a new template's narrowing table would have to remember to join.
    declared = [v for module in (shotchart, fingerprint, templates) for v in vars(module).values()]
    flat = [a for v in declared for a in (v if isinstance(v, tuple) else (v,)) if isinstance(a, Availability)]
    assert {a.table for a in flat} >= {"shot_chart", "net_points_player_fingerprint", "player_season_stats_deduped", "player_game_log"}
    for available in flat:
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


# ---------------- names the question does not support ----------------


def test_a_player_the_question_never_mentions_is_replaced_by_one_it_does(con: duckdb.DuckDBPyConnection) -> None:
    """The bug this exists for, measured live: "compare sga and embiid" routed
    to ['Shai Gilgeous-Alexander', 'Jusuf Nurkic'] and answered with a fluent
    table of two real players, one of whom the question never named. Nothing
    downstream could notice - "Jusuf Nurkic" resolves perfectly."""
    slots = {"players": ["Shai Gilgeous-Alexander", "Jusuf Nurkic"]}
    changed, invented = override_invented_players(con, "compare sga and embiid", slots)
    assert slots["players"] == ["Shai Gilgeous-Alexander", "Joel Embiid"]
    assert changed == [("Jusuf Nurkic", "Joel Embiid")] and invented == []


def test_an_invented_name_with_nothing_to_replace_it_is_reported_rather_than_answered(con: duckdb.DuckDBPyConnection) -> None:
    """Reported, not repaired: the caller falls through to the agent, which at
    least reads the question. What must not happen is answering about Nurkic."""
    slots = {"players": ["Jusuf Nurkic", "Joel Embiid"]}
    changed, invented = override_invented_players(con, "compare the two best centers", slots)
    assert changed == [] and invented == ["Jusuf Nurkic", "Joel Embiid"]
    assert slots["players"] == ["Jusuf Nurkic", "Joel Embiid"]


def test_half_a_name_in_the_question_supports_the_whole_of_it(con: duckdb.DuckDBPyConnection) -> None:
    """Expanding "Luka" to "Luka Doncic" is the router doing its job. Trimming
    the name back to what the question literally holds would undo it - "Luka"
    alone is ambiguous against Luka Garza in the real warehouse."""
    slots = {"player": "Luka Doncic"}
    assert override_invented_players(con, "how many points did Luka average?", slots) == ([], [])
    assert slots["player"] == "Luka Doncic"


def test_a_near_spelling_still_counts_as_naming_somebody(con: duckdb.DuckDBPyConnection) -> None:
    """ "compare sga and embid" - the router corrected the surname and invented
    the given name. The surname is a trace of the question, so this leaves the
    slot alone and lets resolution answer with a suggestion rather than
    replacing a name the question half-supports."""
    slots = {"players": ["Shai Gilgeous-Alexander", "Jemel Embiid"]}
    assert override_invented_players(con, "compare sga and embid", slots) == ([], [])


def test_initials_count_as_naming_somebody(con: duckdb.DuckDBPyConnection) -> None:
    """ "KAT" and "SGA" are how questions carry a name the nickname table may
    not have. Without this, expanding one would look like an invention and
    every such question would fall through to the agent."""
    slots = {"players": ["Jaylen Brown", "Joel Embiid"]}
    assert override_invented_players(con, "compare jb and embiid", slots) == ([], [])


def test_a_nickname_the_question_uses_counts_as_naming_somebody(con: duckdb.DuckDBPyConnection) -> None:
    slots = {"player": "Allen Iverson"}
    assert override_invented_players(con, "Show me The Answer's avg points", slots) == ([], [])


def test_no_player_slot_is_nothing_to_check(con: duckdb.DuckDBPyConnection) -> None:
    assert override_invented_players(con, "who led the league in scoring?", {"stat": "points"}) == ([], [])


# ---------------- what the question itself names ----------------


def test_players_named_in_reads_names_and_nicknames_in_order(con: duckdb.DuckDBPyConnection) -> None:
    assert players_named_in(con, "compare sga and embiid") == ["Shai Gilgeous-Alexander", "Joel Embiid"]


def test_a_possessive_s_is_not_a_player(con: duckdb.DuckDBPyConnection) -> None:
    """ "Klay Thompson's" splits into a stray one-letter word, which is a whole
    word of "John S. Williams" - measured against the warehouse, that put him
    in nine of the routing corpus's questions."""
    assert players_named_in(con, "what was klay thompson's 3pt percentage") == ["Klay Thompson"]


def test_an_ordinary_word_that_only_looks_like_a_name_is_not_one(con: duckdb.DuckDBPyConnection) -> None:
    """Substring matching would read "the highest scoring game" as naming
    Jaron Blossomgame and "with" as naming Jeff Withey, so a span has to equal
    a whole word of the name."""
    assert players_named_in(con, "what was the highest scoring game with 20 rebounds?") == []


def test_a_surname_two_players_share_names_neither_of_them(con: duckdb.DuckDBPyConnection) -> None:
    """This overrules the router, so it may only speak where it is certain."""
    assert players_named_in(con, "plot curry's threes") == []


# ---------------- did you mean ----------------


def test_a_fabricated_given_name_falls_back_to_the_surname(con: duckdb.DuckDBPyConnection) -> None:
    """Exact matching on one fewer token, not fuzzy: every token has to match,
    so one made-up word buried a player the warehouse holds."""
    assert [p.name for p in suggest_players(con, "Jemel Embiid")] == ["Joel Embiid"]


def test_a_misspelling_the_router_did_not_correct_finds_the_player(con: duckdb.DuckDBPyConnection) -> None:
    """ "embid" is not a substring of "Embiid", so no amount of ILIKE reaches
    it and no amount of trusting the question helps - the typo is the
    question."""
    assert [p.name for p in suggest_players(con, "embid")] == ["Joel Embiid"]


def test_nothing_close_suggests_nobody(con: duckdb.DuckDBPyConnection) -> None:
    assert suggest_players(con, "asdf qwerty") == []
    assert suggest_players(con, "") == []


def test_an_incidental_substring_is_not_a_suggestion(con: duckdb.DuckDBPyConnection) -> None:
    """The backoff takes word-boundary matches only. "All" lands inside
    "Ceballos" and "Ball", and suggesting either is worse than saying nothing."""
    assert suggest_players(con, "Nobody At All") == []


def test_a_suggestion_too_long_to_be_one_is_dropped() -> None:
    """A name near a dozen players narrowed nothing. Reading out a directory is
    not a suggestion, and the question is better off falling through."""
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    con.execute("INSERT INTO players SELECT i::VARCHAR, 'Chris Smit' || chr((97 + i)::INTEGER) FROM range(8) t(i)")
    assert suggest_players(con, "Chris Smit") == []  # the surname backoff
    assert suggest_players(con, "Smitz") == []  # and the near-spelling pass


def test_no_match_names_the_near_miss(con: duckdb.DuckDBPyConnection) -> None:
    assert no_match(con, "Jemel Embiid") == "No player found matching 'Jemel Embiid' - did you mean Joel Embiid?"


def test_no_match_says_only_that_when_nothing_is_near(con: duckdb.DuckDBPyConnection) -> None:
    assert no_match(con, "asdf qwerty") == "No player found matching 'asdf qwerty'."


# ---------------- players the router dropped ----------------


def test_a_question_naming_two_players_is_not_a_fingerprint_of_one(con: duckdb.DuckDBPyConnection) -> None:
    """ "compare fingerprints for embiid vs jokic in 2026" came back as a single
    `player` slot. One polygon is not a narrower answer to that - it is a
    different question, answered without saying so."""
    slots = {"player": "Jusuf Nurkic"}
    assert restore_dropped_players(con, "compare fingerprints for embiid vs klay thompson", slots) == ("Jusuf Nurkic", "Joel Embiid and Klay Thompson")


def test_slots_that_already_hold_every_name_are_left_alone(con: duckdb.DuckDBPyConnection) -> None:
    slots = {"players": ["Joel Embiid", "Klay Thompson"]}
    assert restore_dropped_players(con, "compare fingerprints for embiid and klay thompson", slots) is None
    assert restore_dropped_players(con, "plot embiid's fingerprint", {"player": "Joel Embiid"}) is None


def test_restoring_players_clears_the_single_slot_it_replaces(con: duckdb.DuckDBPyConnection) -> None:
    """The template prefers `players`, so a stale `player` would sit in the
    trace saying something the answer did not do."""
    slots = {"player": "Jusuf Nurkic"}
    restore_dropped_players(con, "compare fingerprints for embiid vs klay thompson", slots)
    assert "player" not in slots and slots["players"] == ["Joel Embiid", "Klay Thompson"]


def test_a_vs_question_that_matched_one_player_is_flagged(con: duckdb.DuckDBPyConnection) -> None:
    """ "generate fingerprints for embiid vs jolic" drew Joel Embiid alone. The
    typo cannot be repaired - measured, a near-spelling search over leftover
    words finds a spurious player in 29 of 51 corpus questions - so the answer
    has to say a player is missing rather than quietly drop one."""
    assert compared_but_unmatched("generate fingerprints for embiid vs jolic in 2026", ["Joel Embiid"])


def test_a_vs_question_with_both_players_is_not_flagged(con: duckdb.DuckDBPyConnection) -> None:
    assert not compared_but_unmatched("fingerprints for embiid vs jokic", ["Joel Embiid", "Nikola Jokic"])


def test_a_question_comparing_nobody_is_not_flagged(con: duckdb.DuckDBPyConnection) -> None:
    """One name and no "vs" is a question about one player, which is not a
    half-answer to anything."""
    assert not compared_but_unmatched("plot embiid's fingerprint", ["Joel Embiid"])


def test_a_question_that_compares_nothing_does_not_gain_a_player(con: duckdb.DuckDBPyConnection) -> None:
    """players_named_in is strict but not infallible - "best" is Travis Best
    and "boston" is Brandon Boston Jr. Without a comparison in the question,
    "plot jokic's fingerprint from his best season" drew Travis Best a
    polygon."""
    slots = {"player": "Nikola Jokic"}
    assert restore_dropped_players(con, "plot embiid's fingerprint from his best season", slots) is None
    assert slots == {"player": "Nikola Jokic"}


def test_a_comparison_that_does_not_say_vs_still_restores(con: duckdb.DuckDBPyConnection) -> None:
    slots = {"player": "Jusuf Nurkic"}
    assert restore_dropped_players(con, "compare fingerprints for embiid and klay thompson", slots) is not None


def test_a_refusal_with_no_names_is_still_a_sentence() -> None:
    """Public and typed, so it may not depend on its caller never passing []."""
    assert misread_players([]).endswith("it was not answered.")


# ---------------- ambiguity the router resolved on its own ----------------


def test_a_bare_surname_asks_even_when_the_router_completed_it(con: duckdb.DuckDBPyConnection) -> None:
    """The canonical case. "who is better, tatum or brown" routed to Jaylen
    Brown, and a bare surname is exactly what this project asks about - it
    stopped asking as soon as the router started completing it."""
    slots = {"players": ["Luka Doncic", "Jaylen Brown"]}
    assert undo_name_completion(con, "who is better, doncic or brown", slots) == [("Jaylen Brown", "Brown")]
    assert isinstance(resolve_player(con, slots["players"][1]), Ambiguous)


def test_a_surname_only_one_player_has_is_left_completed(con: duckdb.DuckDBPyConnection) -> None:
    """Completing "embiid" changes no answer, so undoing it would only cost a
    question its answer."""
    slots = {"player": "Joel Embiid"}
    assert undo_name_completion(con, "compare sga and embiid", slots) == []
    assert slots == {"player": "Joel Embiid"}


def test_a_name_the_question_spells_in_full_is_not_a_part_of_one(con: duckdb.DuckDBPyConnection) -> None:
    slots = {"player": "Jaylen Brown"}
    assert undo_name_completion(con, "plot jaylen brown's shot chart", slots) == []


def test_a_nickname_the_table_holds_is_a_resolution_not_a_guess(con: duckdb.DuckDBPyConnection) -> None:
    """PLAYER_NICKNAMES is an audited list; the router's completion is not.
    Cutting "Stephen Curry" back to "Curry" for a question that said "steph"
    would ask about something the question already answered."""
    slots = {"player": "Stephen Curry"}
    assert undo_name_completion(con, "what was steph curry's 3pt percentage", slots) == []


# ---------------- scope_from_question: the team a question plays against ----------------


@pytest.fixture
def scope_con() -> duckdb.DuckDBPyConnection:
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE teams (team_id VARCHAR, display_name VARCHAR, abbreviation VARCHAR)")
    c.execute(
        "INSERT INTO teams VALUES ('2','Boston Celtics','BOS'),('8','Detroit Pistons','DET'),('13','Los Angeles Lakers','LAL'),"
        "('12','LA Clippers','LAC'),('20','Philadelphia 76ers','PHI'),('18','New York Knicks','NY')"
    )
    c.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    c.execute("INSERT INTO players VALUES ('1','Jaylen Brown'),('2','Luka Doncic'),('3','Stephen Curry'),('4','Seth Curry'),('5','Brandon Boston Jr.'),('6','Kawhi Leonard'),('7','LeBron James')")
    return c


def test_a_player_the_router_swapped_for_his_own_team_comes_back(scope_con: duckdb.DuckDBPyConnection) -> None:
    """Measured: routed to game_log with team='Boston Celtics' and no player, and
    answered with the Celtics' last eight games."""
    slots: dict[str, Any] = {"team": "Boston Celtics", "limit": 8}
    notes = scope_from_question(scope_con, "jaylen brown last 8 games vs pistons", slots, reads_player=True)
    assert slots == {"player": "Jaylen Brown", "opponent": "Detroit Pistons", "limit": 8}
    assert len(notes) == 2


def test_an_opponent_in_the_team_slot_becomes_the_opponent(scope_con: duckdb.DuckDBPyConnection) -> None:
    slots: dict[str, Any] = {"team": "Los Angeles Lakers"}
    scope_from_question(scope_con, "Luka Doncic game log vs Lakers this season", slots, reads_player=True)
    assert slots == {"player": "Luka Doncic", "opponent": "Los Angeles Lakers"}


def test_a_team_is_not_a_player_to_compare(scope_con: duckdb.DuckDBPyConnection) -> None:
    """ "boston" alone names Brandon Boston Jr., so leaving the Celtics in
    `players` was a comparison with a player nobody asked about."""
    slots: dict[str, Any] = {"players": ["Stephen Curry", "Boston Celtics"]}
    scope_from_question(scope_con, "how did curry do against the celtics this year", slots, reads_player=True)
    assert slots == {"player": "Stephen Curry", "opponent": "Boston Celtics"}


def test_an_opponent_filed_as_the_team_beside_a_player_becomes_the_opponent(scope_con: duckdb.DuckDBPyConnection) -> None:
    """Measured: came back with team='Boston Celtics' beside both players. No
    template read it and no opponent was set, so nothing refused."""
    slots: dict[str, Any] = {"players": ["Stephen Curry", "LeBron James"], "team": "Boston Celtics"}
    scope_from_question(scope_con, "compare curry and lebron vs the celtics", slots, reads_player=True)
    assert slots == {"players": ["Stephen Curry", "LeBron James"], "opponent": "Boston Celtics"}
    one: dict[str, Any] = {"player": "Stephen Curry", "team": "Boston Celtics"}
    scope_from_question(scope_con, "how did steph curry do against the celtics last season", one, reads_player=True)
    assert one == {"player": "Stephen Curry", "opponent": "Boston Celtics"}


def test_a_team_beside_a_player_stays_unless_the_question_plays_against_it(scope_con: duckdb.DuckDBPyConnection) -> None:
    # His own team, which no "vs" names.
    slots: dict[str, Any] = {"player": "LeBron James", "team": "Los Angeles Lakers"}
    assert scope_from_question(scope_con, "lebron points for the lakers", slots, reads_player=True) == []
    assert slots == {"player": "LeBron James", "team": "Los Angeles Lakers"}
    # A template that reads no player keeps its team whatever the question says.
    kept: dict[str, Any] = {"player": "Stephen Curry", "team": "Boston Celtics"}
    scope_from_question(scope_con, "curry vs the celtics", kept, reads_player=False)
    assert kept["team"] == "Boston Celtics"


def test_both_sides_of_a_head_to_head_are_left_alone(scope_con: duckdb.DuckDBPyConnection) -> None:
    slots: dict[str, Any] = {"teams": ["Los Angeles Lakers", "Boston Celtics"]}
    assert scope_from_question(scope_con, "Lakers vs Celtics record this season", slots, reads_player=False) == []
    assert slots == {"teams": ["Los Angeles Lakers", "Boston Celtics"]}


def test_an_opponent_the_router_already_gave_is_left_alone(scope_con: duckdb.DuckDBPyConnection) -> None:
    slots: dict[str, Any] = {"team": "Philadelphia 76ers", "opponent": "Boston Celtics", "period": 4}
    assert scope_from_question(scope_con, "76ers 4th quarter points against boston", slots, reads_player=False) == []


def test_a_player_comparison_names_no_opponent(scope_con: duckdb.DuckDBPyConnection) -> None:
    slots: dict[str, Any] = {"players": ["LeBron James", "Kawhi Leonard"]}
    assert scope_from_question(scope_con, "lebron vs kawhi 2015", slots, reads_player=True) == []
    assert slots == {"players": ["LeBron James", "Kawhi Leonard"]}


def test_la_is_two_teams_and_names_no_opponent(scope_con: duckdb.DuckDBPyConnection) -> None:
    slots: dict[str, Any] = {"player": "Stephen Curry"}
    assert scope_from_question(scope_con, "curry vs la", slots, reads_player=True) == []


def test_a_team_nickname_grounds_the_team_slot(scope_con: duckdb.DuckDBPyConnection) -> None:
    """ "sixers" is no word of "Philadelphia 76ers", but it is the team."""
    slots: dict[str, Any] = {"team": "Philadelphia 76ers"}
    assert scope_from_question(scope_con, "Top 5 scorers on the sixers", slots, reads_player=True) == []
    assert slots == {"team": "Philadelphia 76ers"}


def test_a_team_nickname_names_an_opponent(scope_con: duckdb.DuckDBPyConnection) -> None:
    slots: dict[str, Any] = {"player": "Jaylen Brown"}
    scope_from_question(scope_con, "jaylen brown against the sixers", slots, reads_player=True)
    assert slots["opponent"] == "Philadelphia 76ers"


def test_a_player_is_only_restored_where_a_template_reads_one(scope_con: duckdb.DuckDBPyConnection) -> None:
    slots: dict[str, Any] = {"team": "Boston Celtics"}
    scope_from_question(scope_con, "jaylen brown last 8 games vs pistons", slots, reads_player=False)
    assert slots == {"team": "Boston Celtics", "opponent": "Detroit Pistons"}


def test_a_player_left_out_is_restored_only_where_one_is_required(scope_con: duckdb.DuckDBPyConnection) -> None:
    """ "Sga record 36 plus points" came back with no player at all. An optional
    player slot left empty means the league, so only a template that needs one
    gets it back."""
    slots: dict[str, Any] = {"stat": "points", "threshold": 36}
    scope_from_question(scope_con, "Sga record 36 plus points", slots, reads_player=True, needs_player=True)
    assert slots["player"] == "Shai Gilgeous-Alexander"
    optional: dict[str, Any] = {"stat": "points", "threshold": 36}
    scope_from_question(scope_con, "Sga record 36 plus points", optional, reads_player=True)
    assert "player" not in optional


@pytest.mark.parametrize(("nickname", "team"), [("Sixers", "Philadelphia 76ers"), ("cavs", "Cleveland Cavaliers"), ("Mavs", "Dallas Mavericks")])
def test_a_team_nickname_resolves_to_the_team(nickname: str, team: str) -> None:
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE teams (team_id VARCHAR, display_name VARCHAR, abbreviation VARCHAR)")
    c.execute("INSERT INTO teams VALUES ('20','Philadelphia 76ers','PHI'),('5','Cleveland Cavaliers','CLE'),('6','Dallas Mavericks','DAL')")
    got = resolve_team(c, nickname)
    assert isinstance(got, Entity) and got.name == team


def test_a_player_in_the_team_slot_becomes_the_subject(scope_con: duckdb.DuckDBPyConnection) -> None:
    """Measured: team='Podziemski', player='Curry' for "Podziemski game log without curry"."""
    scope_con.execute("INSERT INTO players VALUES ('9','Brandin Podziemski')")
    slots: dict[str, Any] = {"player": "Curry", "team": "Podziemski", "without": "curry"}
    scope_from_question(scope_con, "Podziemski game log without curry", slots, reads_player=True)
    assert slots["player"] == "Brandin Podziemski" and "team" not in slots and slots["without"] == "curry"
