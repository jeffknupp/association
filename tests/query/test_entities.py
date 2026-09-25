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
    find_teams,
    misread_players,
    nicknames_in,
    no_match,
    override_nicknames,
    player_named_on_a_team_only_question,
    players_named_in,
    resolve_player,
    resolve_team,
    restore_dropped_players,
    suggest_players,
    team_only_question_names_a_player,
    teams_named_in,
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


@pytest.fixture
def league_teams() -> duckdb.DuckDBPyConnection:
    """The four teams whose real ESPN abbreviation is not the one people use,
    plus New Orleans, which collides with Orlando's abbreviation as a
    substring."""
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE teams (team_id VARCHAR, abbreviation VARCHAR, display_name VARCHAR)")
    c.execute(
        "INSERT INTO teams VALUES ('9','GS','Golden State Warriors'),('3','NO','New Orleans Pelicans'),"
        "('18','NY','New York Knicks'),('24','SA','San Antonio Spurs'),('19','ORL','Orlando Magic'),"
        "('12','LAC','LA Clippers'),('13','LAL','Los Angeles Lakers')"
    )
    return c


@pytest.mark.parametrize(
    ("text", "want"),
    [
        # ESPN abbreviates these "GS", "NO", "NY" and "SA"; nobody writes that.
        ("GSW", "Golden State Warriors"),
        ("NOP", "New Orleans Pelicans"),
        ("NYK", "New York Knicks"),
        ("SAS", "San Antonio Spurs"),
        # The router is asked for full team names and writes this one; ESPN
        # stores "LA Clippers", so it matched nothing at all.
        ("Los Angeles Clippers", "LA Clippers"),
    ],
)
def test_the_abbreviations_people_use_resolve(league_teams: duckdb.DuckDBPyConnection, text: str, want: str) -> None:
    """Measured live before this existed: every one of these returned NOTHING,
    so "Lauri Markkan vs GSW last 5 games" lost the team entirely."""
    assert [e.name for e in find_teams(league_teams, text)] == [want]


def test_an_abbreviation_outranks_a_team_whose_name_merely_contains_it(league_teams: duckdb.DuckDBPyConnection) -> None:
    """ "ORL" is Orlando's abbreviation and also a substring of "New Orleans",
    and both used to come back - an ambiguity offered to the user over a
    question that names exactly one team, and a chance to answer about the
    other one."""
    assert [e.name for e in find_teams(league_teams, "ORL")] == ["Orlando Magic"]


def test_two_teams_in_one_city_stay_ambiguous(league_teams: duckdb.DuckDBPyConnection) -> None:
    """The ranking settles an abbreviation against a name, and must not settle
    a real ambiguity: no team is abbreviated "LA", so both Los Angeles teams
    sit in the same tier and the caller still has to ask."""
    assert [e.name for e in find_teams(league_teams, "LA")] == ["LA Clippers", "Los Angeles Lakers"]


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


def test_a_curated_first_name_beats_an_unrelated_alphabetical_tie() -> None:
    """ "Rui" is Hachimura's real given name, the same shape as "luka" and
    "kobe" - and the warehouse also holds a "Rui Betancourt" who shares the
    token, which is why this matters: unresolved, plain word-boundary
    matching hands the answer to whichever name sorts first alphabetically,
    and "Betancourt" does. The curated table is checked before that ordering
    ever runs."""
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    con.execute("INSERT INTO players VALUES ('1','Rui Betancourt'),('2','Rui Hachimura')")
    assert [c.name for c in find_players(con, "Rui")] == ["Rui Hachimura"]
    assert resolve_player(con, "Rui") == Entity(id="2", name="Rui Hachimura")
    # Naming Betancourt in full still reaches him - the curated table only
    # intercepts the bare shorthand, never a name spelled out.
    assert [c.name for c in find_players(con, "Rui Betancourt")] == ["Rui Betancourt"]


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
    import importlib
    import pkgutil

    from association.query import fingerprint, shotchart, templates
    from association.query.entities import Availability
    from association.query.prompt import KNOWN_TABLES

    # Every one each module declares, tuples included, rather than a list here
    # that a new template's narrowing table would have to remember to join.
    # templates is a package: every submodule of it, so a new subject module's
    # narrowing table is covered without being added here.
    modules = [shotchart, fingerprint, templates, *(importlib.import_module(m.name) for m in pkgutil.iter_modules(templates.__path__, templates.__name__ + "."))]
    declared = [v for module in modules for v in vars(module).values()]
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
    # "OG" is Anunoby's own given name, the same shape as "Ja" and "Ant"; "ant
    # man" is a two-word key, matched the same way "chef curry" is.
    assert nicknames_in("OG last 15 home games") == ["OG Anunoby"]
    assert nicknames_in("ant man log vs portland") == ["Anthony Edwards"]


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


def test_a_nickname_another_slot_already_holds_is_not_the_subject() -> None:
    """ "myles turner bucks stats without giannis last 10" routed to
    player='Myles Turner', without=['giannis'], and the one nickname in the
    question rewrote the subject to Giannis - who could not play without
    himself. The nickname is spoken for by the slot the router put it in."""
    slots = {"player": "Myles Turner", "team": "Bucks", "without": ["giannis"]}
    assert override_nicknames("myles turner bucks stats without giannis last 10", slots) == []
    assert slots["player"] == "Myles Turner"
    # Spelled out by the router rather than left as the nickname: still spoken for.
    resolved = {"player": "Myles Turner", "without": ["Giannis Antetokounmpo"]}
    assert override_nicknames("myles turner stats without giannis", resolved) == []
    # With nothing else claiming it, the nickname still corrects the subject.
    alone = {"player": "Jayson Tatum"}
    assert override_nicknames("how many points does giannis average", alone) == [("Jayson Tatum", "Giannis Antetokounmpo")]


# ---------------- the question's own span, not the router's spelling ----------------
#
# The subject reading's "any one word is enough" support check passes every
# name below - a truncated one because the word it kept is right there, a
# fabricated one because whichever half is real is right there too - so
# none is ever replaced; `_question_derived_player` is what respells them
# from the question's own span, reached here the way the agent reaches it:
# through subject.read_subject/apply_subject. These are the five rows
# AGENTS.md records as
# measured against the live router, and the fixture below matches its own
# description of each: a surname shared by several first names (or the
# reverse), so a wrong repair would resolve to the wrong real person, not
# just fail loudly.


@pytest.fixture
def span_con() -> duckdb.DuckDBPyConnection:
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    c.execute("CREATE TABLE teams (team_id VARCHAR, abbreviation VARCHAR, display_name VARCHAR)")
    c.execute(
        "INSERT INTO players VALUES "
        # "dennis schröder" typed correctly arrived as just 'Dennis' - grounded,
        # and seven Dennises make it a clarification that need not have asked.
        "('1','Dennis Schroder'),('2','Dennis Scott'),('3','Dennis Rodman'),('4','RayJ Dennis'),"
        "('5','Dennis Smith Jr.'),('6','Dexter Dennis'),('7','Dennis Horner'),"
        # "Aaron gordan" arrived as 'Aaron' - grounded, and many Aarons make it
        # a clarification too, when only Gordon's surname is a typo away.
        "('8','Aaron Gordon'),('9','Aaron Holiday'),('10','Aaron Nesmith'),('11','Aaron Wiggins'),"
        # "jayleyn brown" arrived as 'Brown' - grounded, and several Browns make
        # it a clarification, when only Jaylen's given name is a typo away.
        "('12','Jaylen Brown'),('13','Bruce Brown'),('14','Kobe Brown'),"
        # "tatum rec home" arrived as 'Jaylen Tatum' - grounded by 'Tatum' alone
        # - and there is exactly one.
        "('15','Jayson Tatum'),"
        # "Grady dick" arrived as 'Grady Dickinson' - grounded by 'Grady' alone
        # - and the real player is 'Gradey Dick'; 'Dickinson' is a different,
        # real player (ISSUES.md #122's own wrong suggestion).
        "('16','Gradey Dick'),('17','Hunter Dickinson'),('18','Dickey Simpkins'),"
        # 19-20 are what guards the short-word span filter: "Williams's"
        # splits into a stray one-letter "s" (the same possessive
        # test_a_possessive_s_is_not_a_player guards against for
        # players_named_in), which is a whole word of John S. Williams. Two
        # real Williamses make "williams" alone ambiguous, so only the stray
        # "s" could spuriously narrow it - never a name anyone typed.
        "('19','John S. Williams'),('20','Aaron Williams'),"
        # 21-22 guard the two rules above: "kareem stats vs bob lanier" names
        # two pre-1994 legends the warehouse has no row for at all (ISSUES.md
        # #123). Kareem Rush and Chaz Lanier are the real, unrelated players
        # each near-miss coincidentally and confidently names instead.
        "('21','Kareem Rush'),('22','Chaz Lanier')"
    )
    return c


def _apply(con: duckdb.DuckDBPyConnection, question: str, slots: dict[str, Any], intent: str = "player_stat") -> tuple[list[tuple[str, str]], list[str]]:
    """The router's names written the way the agent writes them - the subject
    reading applied to the slots - reduced to the (was, now) pairs and the
    names dropped, the shape these tests were first written against."""
    from association.query.subject import apply_subject, read_subject

    applied = apply_subject(read_subject(con, question, intent, slots), slots, con=con, intent=intent)
    return [(str(d.before), str(d.after)) for d in applied.decisions], applied.dropped


def _scope(con: duckdb.DuckDBPyConnection, question: str, slots: dict[str, Any], *, reads_player: bool, intent: str = "", **flags: Any) -> list[str]:
    """The slot repair the agent runs: the subject read from the router's
    slots, then applied (`subject.apply_subject`, which took every step of
    `scope_from_question` over in 4.5.0). One line per change, so a test
    written against `scope_from_question` reads the same."""
    from association.query.subject import apply_subject, read_subject

    # The chain's flags were one per intent set; the reading reads the
    # intent itself, so a flag picks a representative intent from its set.
    for flag, representative in (("needs_player", "record_when"), ("restore_subject", "single_game_high"), ("restore_team", "player_stat")):
        if flags.pop(flag, False):
            intent = intent or representative
    flags.pop("restore_team_subject", None)
    assert not flags, flags
    intent = intent or ("game_log" if reads_player else "team_record")
    subject = read_subject(con, question, intent, slots)
    return [d.line() for d in apply_subject(subject, slots, con=con, intent=intent).decisions]


def test_a_truncated_name_is_expanded_from_the_question(span_con: duckdb.DuckDBPyConnection) -> None:
    """The router dropped the surname the question spelled correctly, and
    'Dennis' alone is grounded (the word is right there) - so the old check
    never touched it, and seven Dennises would have been asked about, one
    right answer among them."""
    slots = {"player": "Dennis"}
    changed, invented = _apply(span_con, "how many points does dennis schroder average", slots)
    assert changed == [("Dennis", "Dennis Schroder")]
    assert invented == []
    assert slots["player"] == "Dennis Schroder"


def test_a_typo_on_the_dropped_half_of_a_name_still_resolves(span_con: duckdb.DuckDBPyConnection) -> None:
    """The router dropped the surname entirely, and the question's own
    spelling of it ('gordan') is a typo - but AND-across-tokens with the given
    name it kept narrows four Aarons to the one whose surname is a real edit
    away, the same discipline suggest_players already trusts for a
    suggestion, strong enough here to act on directly."""
    slots = {"player": "Aaron"}
    changed, invented = _apply(span_con, "aaron gordan points per game", slots)
    assert changed == [("Aaron", "Aaron Gordon")]
    assert invented == []
    assert slots["player"] == "Aaron Gordon"


def test_a_typo_on_the_kept_half_of_a_name_still_resolves(span_con: duckdb.DuckDBPyConnection) -> None:
    """The router kept only the exact surname the question used, and the
    question's given name ('jayleyn') is a typo - narrowed against three
    Browns to the one whose given name is a real edit away."""
    slots = {"player": "Brown"}
    changed, invented = _apply(span_con, "jayleyn brown last 10 games", slots)
    assert changed == [("Brown", "Jaylen Brown")]
    assert invented == []
    assert slots["player"] == "Jaylen Brown"


def test_a_fabricated_given_name_next_to_an_exact_surname_is_discarded(span_con: duckdb.DuckDBPyConnection) -> None:
    """The router invented 'Jaylen' out of nothing and bolted it onto the
    league's only Tatum; grounded by 'Tatum' alone, so the old check let the
    invented half ride through. The surname alone is an EXACT match here, not
    a guess, which is what makes discarding the invented half safe rather
    than a substitution."""
    slots = {"player": "Jaylen Tatum"}
    changed, invented = _apply(span_con, "tatum rec home", slots)
    assert changed == [("Jaylen Tatum", "Jayson Tatum")]
    assert invented == []
    assert slots["player"] == "Jayson Tatum"


def test_a_fabricated_surname_extension_is_discarded_not_the_router_s_wrong_guess(span_con: duckdb.DuckDBPyConnection) -> None:
    """ISSUES.md #122: the router's 'Dickinson' is a real surname, grounded by
    the question's own typo'd 'Grady', so the old check never touched it -
    and suggest_players' surname-only backoff then found Hunter Dickinson, a
    different real player, by backing off to the ROUTER's fabricated
    surname. This resolves from the QUESTION's own 'dick' instead, which
    Hunter Dickinson's surname does not literally contain as a whole word,
    so he is never a candidate here at all."""
    slots = {"player": "Grady Dickinson"}
    changed, invented = _apply(span_con, "Grady dick last 10 games", slots)
    assert changed == [("Grady Dickinson", "Gradey Dick")]
    assert invented == []
    assert slots["player"] == "Gradey Dick"


def test_a_span_that_already_matches_the_router_is_a_no_op(span_con: duckdb.DuckDBPyConnection) -> None:
    """The common case: the router already wrote the exact name the question
    spells out, so nothing should be logged as changed."""
    slots = {"player": "Jayson Tatum"}
    assert _apply(span_con, "how many points does jayson tatum average", slots) == ([], [])
    assert slots["player"] == "Jayson Tatum"


def test_two_anchored_words_that_fail_together_do_not_fall_back_to_one(span_con: duckdb.DuckDBPyConnection) -> None:
    """ISSUES.md #123's shape, reached through this function instead of
    suggest_players: "Kareem Abdul-Jabbar" and "Bob Lanier" are both real
    players the router correctly named in full - neither is in `players` at
    all, because both retired before the warehouse's 1993-94 floor. "Kareem"
    alone exactly names Kareem Rush and "Lanier" alone exactly names Chaz
    Lanier, two real but wholly unrelated players - a wrong-entity answer
    that measurably happened before this guard existed. Both words of each
    name are exactly in the question, so the failure of the full span must
    be believed rather than repaired from a single leftover word."""
    slots = {"players": ["Kareem Abdul-Jabbar", "Bob Lanier"]}
    assert _apply(span_con, "kareem stats vs bob lanier", slots) == ([], [])
    assert slots["players"] == ["Kareem Abdul-Jabbar", "Bob Lanier"]


def test_a_given_name_anchor_alone_is_not_trusted_but_its_window_is(span_con: duckdb.DuckDBPyConnection) -> None:
    """The other half of the same guard: "Kareem" anchors only the FIRST
    word of a name the router invented the rest of, with nothing else in the
    question backing "Abdul-Jabbar" at all - unlike "Grady dick", where the
    window around the given-name anchor ("grady dick") is what resolves it.
    A lone given-name anchor with no corroborating window must stay unfixed,
    the same as a lone fuzzy surname."""
    slots = {"player": "Kareem Abdul-Jabbar"}
    assert _apply(span_con, "kareem stats this season", slots) == ([], [])
    assert slots["player"] == "Kareem Abdul-Jabbar"


def test_a_lone_fuzzy_word_is_still_left_for_a_suggestion_not_a_silent_pick(span_con: duckdb.DuckDBPyConnection) -> None:
    """The mirror image of the cases above: only ONE word resolves, and only
    by near spelling, with nothing else in the window to corroborate it. That
    is exactly what suggest_players already treats as a suggestion rather
    than a substitution (test_a_near_spelling_still_counts_as_naming_somebody
    covers the same shape against the smaller `con` fixture), so this must
    stay a no-op and let the existing grounded/suggestion path run."""
    slots = {"player": "Jemel Gradee"}
    assert _apply(span_con, "how many rebounds does gradee average", slots) == ([], [])
    assert slots["player"] == "Jemel Gradee"


def test_a_stray_possessive_letter_does_not_narrow_an_ambiguous_surname(span_con: duckdb.DuckDBPyConnection) -> None:
    """ "Williams's" splits into a stray one-letter "s" next to the anchor,
    which is a whole word of John S. Williams - and with a second real
    Williams in the fixture, "williams" alone is ambiguous. The stray "s"
    must not be allowed to break that tie: nobody typed it to mean anything,
    and the same trap already caught players_named_in over "Jokic's" without
    a length floor on a span's own words."""
    slots = {"player": "Jemel Williams"}
    assert _apply(span_con, "williams's rebounds this game", slots) == ([], [])
    assert slots["player"] == "Jemel Williams"


def test_an_ambiguous_span_is_left_for_the_clarification_to_ask(span_con: duckdb.DuckDBPyConnection) -> None:
    """ "who is better, tatum or brown" - 'brown' alone resolves to three
    real players here, so the window must not guess between them; the
    existing undo_name_completion trims the router's completed 'Jaylen
    Brown' back to the ambiguous 'brown' afterward, unaffected by this."""
    slots = {"players": ["Jayson Tatum", "Jaylen Brown"]}
    assert _apply(span_con, "who is better, tatum or brown", slots) == ([], [])
    assert slots["players"] == ["Jayson Tatum", "Jaylen Brown"]


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


def test_a_common_surname_falls_back_to_the_near_spelling_pass() -> None:
    """ "Dylon Harper" backs off to six real Harpers - too many to suggest on
    the surname alone (the shape above) - but the surname backoff used to give
    up right there instead of trying the stricter pass below it, which needs
    BOTH names close and finds the one real match: Dylan, not Derek, Jared,
    Justin or either Ron."""
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    con.execute("INSERT INTO players VALUES ('1','Derek Harper'),('2','Dylan Harper'),('3','Jared Harper'),('4','Justin Harper'),('5','Ron Harper'),('6','Ron Harper Jr.')")
    assert [p.name for p in suggest_players(con, "Dylon Harper")] == ["Dylan Harper"]


def test_a_team_name_is_not_suggested_as_a_player() -> None:
    """ "Hawks" is one edit from a real "Hawes", and "Most reb by a hawk player
    history" answered "did you mean Spencer Hawes?" instead of naming the
    actual cause: the question is about a team, which this near-spelling pass
    cannot know unless it checks. A team the text names outright is not a near
    miss on a player, whatever the edit distance says."""
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    con.execute("INSERT INTO players VALUES ('1','Spencer Hawes')")
    con.execute("CREATE TABLE teams (team_id VARCHAR, abbreviation VARCHAR, display_name VARCHAR)")
    con.execute("INSERT INTO teams VALUES ('1','ATL','Atlanta Hawks')")
    assert suggest_players(con, "Hawks") == []


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


def test_a_held_name_ambiguous_alone_still_lets_its_partner_in(con: duckdb.DuckDBPyConnection) -> None:
    """ISSUES.md #143: "show a fingerprint for maxey vs jaylen brown in 2026"
    held one name ("Maxey") and players_named_in found a DIFFERENT one
    ("Jaylen Brown") - "Maxey" alone names two players in the real warehouse
    (Tyrese and Marlon), so players_named_in's own strictness (a span counts
    only when it names EXACTLY one player) drops it, and the two lists being
    the same LENGTH used to read as nothing to restore, even though they name
    two different people. A fresh connection, not the shared `con` fixture:
    its own "Jaylen Brown Jr." makes "jaylen brown" ambiguous too, which would
    hide the very bug this pins."""
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    c.execute("INSERT INTO players VALUES ('1', 'Tyrese Maxey'), ('2', 'Marlon Maxey'), ('3', 'Jaylen Brown')")
    slots: dict[str, Any] = {"player": "Maxey"}
    assert restore_dropped_players(c, "show a fingerprint for maxey vs jaylen brown in 2026", slots) == ("Maxey", "Maxey and Jaylen Brown")
    assert slots == {"players": ["Maxey", "Jaylen Brown"]}


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
    assert compared_but_unmatched(con, "generate fingerprints for embiid vs jolic in 2026", ["Joel Embiid"])


def test_a_vs_question_with_both_players_is_not_flagged(con: duckdb.DuckDBPyConnection) -> None:
    assert not compared_but_unmatched(con, "fingerprints for embiid vs jokic", ["Joel Embiid", "Nikola Jokic"])


def test_a_question_comparing_nobody_is_not_flagged(con: duckdb.DuckDBPyConnection) -> None:
    """One name and no "vs" is a question about one player, which is not a
    half-answer to anything."""
    assert not compared_but_unmatched(con, "plot embiid's fingerprint", ["Joel Embiid"])


def test_a_name_that_resolves_is_never_called_a_warehouse_miss(con: duckdb.DuckDBPyConnection) -> None:
    """ISSUES.md #143's mirror-image bug: "only one of them matches anybody in
    the warehouse - check the spelling of the other" said that about Jaylen
    Brown, whom `players` holds. A name that DOES resolve against the roster
    must get a sentence that says so - never the one that claims the
    warehouse does not have him. `held` is deliberately left at one name here
    (as if some future restoration bug dropped the second again) so this
    branch is pinned on its own, independent of restore_dropped_players."""
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    c.execute("INSERT INTO players VALUES ('1', 'Tyrese Maxey'), ('2', 'Marlon Maxey'), ('3', 'Jaylen Brown')")
    note = compared_but_unmatched(c, "show a fingerprint for maxey vs jaylen brown in 2026", ["Maxey"])
    assert note == "Note: the question also names Jaylen Brown, who was not included in this answer."


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
        "('12','LA Clippers','LAC'),('20','Philadelphia 76ers','PHI'),('18','New York Knicks','NY'),('17','Brooklyn Nets','BKN'),('19','Orlando Magic','ORL'),('21','Phoenix Suns','PHX'),"
        "('22','Portland Trail Blazers','POR'),('9','Golden State Warriors','GS')"
    )
    c.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    c.execute(
        "INSERT INTO players VALUES ('1','Jaylen Brown'),('2','Luka Doncic'),('3','Stephen Curry'),('4','Seth Curry'),('5','Brandon Boston Jr.'),('6','Kawhi Leonard'),('7','LeBron James'),"
        "('8','Magic Johnson'),('9','Karl-Anthony Towns'),('10','Travis Best'),('11','Luther Head'),('12','Alperen Sengun')"
    )
    return c


def test_a_player_the_router_swapped_for_his_own_team_comes_back(scope_con: duckdb.DuckDBPyConnection) -> None:
    """Measured: routed to game_log with team='Boston Celtics' and no player, and
    answered with the Celtics' last eight games."""
    slots: dict[str, Any] = {"team": "Boston Celtics", "limit": 8}
    notes = _scope(scope_con, "jaylen brown last 8 games vs pistons", slots, reads_player=True)
    assert slots == {"player": "Jaylen Brown", "opponent": "Detroit Pistons", "limit": 8}
    assert len(notes) == 2


def test_the_routers_team_stays_the_opponent_when_the_player_it_displaced_comes_back(scope_con: duckdb.DuckDBPyConnection) -> None:
    """ "karl towns stats vs netslast 5 games": the router read the garbled
    "vs nets" as team='Brooklyn Nets' and dropped Towns. Restoring him and
    dropping the team answered his last five games against anybody."""
    slots: dict[str, Any] = {"team": "Brooklyn Nets", "limit": 5}
    _scope(scope_con, "karl towns stats vs netslast 5 games", slots, reads_player=True)
    assert slots == {"player": "Karl-Anthony Towns", "opponent": "Brooklyn Nets", "limit": 5}


def test_a_word_that_names_a_team_the_question_is_about_is_not_a_player(scope_con: duckdb.DuckDBPyConnection) -> None:
    """ "magic vs nets last 10" named Magic Johnson by its one word "magic" and
    the Magic's log became his. Nobody is named, so it is a team's log against
    another - and the router had the two sides backwards."""
    slots: dict[str, Any] = {"team": "Brooklyn Nets", "opponent": "Orlando Magic", "limit": 10}
    _scope(scope_con, "magic vs nets last 10", slots, reads_player=True)
    assert slots == {"team": "Orlando Magic", "opponent": "Brooklyn Nets", "limit": 10}


def test_an_opponent_alone_leaves_no_subject_rather_than_the_opponents_log(scope_con: duckdb.DuckDBPyConnection) -> None:
    """ "Jersmi grant last 5 games vs the suns": the player is a typo nothing
    resolves, the router filed the Suns as the team, and the answer was the
    Suns' last five games. The Suns are the opponent; the subject is missing,
    which is the template's to refuse."""
    slots: dict[str, Any] = {"team": "Phoenix Suns", "limit": 5}
    _scope(scope_con, "Jersmi grant last 5 games vs the suns", slots, reads_player=True)
    assert slots == {"opponent": "Phoenix Suns", "limit": 5}


def test_a_team_the_question_never_names_goes_even_when_nobody_is_named(scope_con: duckdb.DuckDBPyConnection) -> None:
    """ "stating centers vs phoenix suns log" arrived as the Lakers, whose log it then was."""
    slots: dict[str, Any] = {"team": "Los Angeles Lakers", "opponent": "Phoenix Suns"}
    _scope(scope_con, "stating centers vs phoenix suns log", slots, reads_player=True)
    assert slots == {"opponent": "Phoenix Suns"}


def test_an_opponent_that_names_one_of_the_questions_own_players_is_dropped(scope_con: duckdb.DuckDBPyConnection) -> None:
    """ "sga vs tyrese maxey fingerprint" put Maxey in `opponent`; the pair was
    rebuilt into `players` and then check_scope refused the leftover slot, so
    a question the system answers under other words had no answer at all."""
    slots: dict[str, Any] = {"players": ["Stephen Curry", "Jaylen Brown"], "opponent": "Jaylen Brown", "side": "total"}
    _scope(scope_con, "curry vs jaylen brown fingerprint", slots, reads_player=True, intent="fingerprint")
    assert slots == {"players": ["Stephen Curry", "Jaylen Brown"], "side": "total"}


def test_an_opponent_naming_a_player_nobody_asked_about_is_left_to_be_refused(scope_con: duckdb.DuckDBPyConnection) -> None:
    """Narrow on purpose: the slot goes only when the person it names is
    already a subject. Otherwise a template that cannot honor it must still
    refuse, rather than silently widening to every opponent. (The question
    has to hold the name: an opponent it never held is the invented-name
    shape, dropped by `subject.apply_subject` since 4.4.0.)"""
    slots: dict[str, Any] = {"player": "Stephen Curry", "opponent": "Kawhi Leonard"}
    _scope(scope_con, "curry fingerprint vs kawhi", slots, reads_player=True, intent="fingerprint")
    assert slots["opponent"] == "Kawhi Leonard"
    # A real team in `opponent` is untouched, whoever the subject is.
    team: dict[str, Any] = {"player": "Stephen Curry", "opponent": "Boston Celtics"}
    _scope(scope_con, "curry vs the celtics", team, reads_player=True)
    assert team["opponent"] == "Boston Celtics"


def test_a_name_typed_with_accents_still_names_its_player(scope_con: duckdb.DuckDBPyConnection) -> None:
    """The warehouse spells every name in plain letters; "luka dončić last 15
    games vs. magic" matched nobody and answered the Lakers' log."""
    slots: dict[str, Any] = {"team": "Los Angeles Lakers", "opponent": "Orlando Magic", "limit": 15}
    _scope(scope_con, "luka dončić last 15 games vs. magic", slots, reads_player=True)
    assert slots == {"player": "Luka Doncic", "opponent": "Orlando Magic", "limit": 15}
    assert [p.name for p in find_players(scope_con, "dončić")] == ["Luka Doncic"]


def test_an_opponent_in_the_team_slot_becomes_the_opponent(scope_con: duckdb.DuckDBPyConnection) -> None:
    slots: dict[str, Any] = {"team": "Los Angeles Lakers"}
    _scope(scope_con, "Luka Doncic game log vs Lakers this season", slots, reads_player=True)
    assert slots == {"player": "Luka Doncic", "opponent": "Los Angeles Lakers"}


def test_a_team_is_not_a_player_to_compare(scope_con: duckdb.DuckDBPyConnection) -> None:
    """ "boston" alone names Brandon Boston Jr., so leaving the Celtics in
    `players` was a comparison with a player nobody asked about."""
    slots: dict[str, Any] = {"players": ["Stephen Curry", "Boston Celtics"]}
    _scope(scope_con, "how did curry do against the celtics this year", slots, reads_player=True)
    assert slots == {"player": "Stephen Curry", "opponent": "Boston Celtics"}


def test_an_opponent_filed_as_the_team_beside_a_player_becomes_the_opponent(scope_con: duckdb.DuckDBPyConnection) -> None:
    """Measured: came back with team='Boston Celtics' beside both players. No
    template read it and no opponent was set, so nothing refused."""
    slots: dict[str, Any] = {"players": ["Stephen Curry", "LeBron James"], "team": "Boston Celtics"}
    _scope(scope_con, "compare curry and lebron vs the celtics", slots, reads_player=True)
    assert slots == {"players": ["Stephen Curry", "LeBron James"], "opponent": "Boston Celtics"}
    one: dict[str, Any] = {"player": "Stephen Curry", "team": "Boston Celtics"}
    _scope(scope_con, "how did steph curry do against the celtics last season", one, reads_player=True)
    assert one == {"player": "Stephen Curry", "opponent": "Boston Celtics"}


def test_a_team_beside_a_player_stays_unless_the_question_plays_against_it(scope_con: duckdb.DuckDBPyConnection) -> None:
    # His own team, which no "vs" names.
    slots: dict[str, Any] = {"player": "LeBron James", "team": "Los Angeles Lakers"}
    assert _scope(scope_con, "lebron points for the lakers", slots, reads_player=True) == []
    assert slots == {"player": "LeBron James", "team": "Los Angeles Lakers"}
    # A template that reads no player keeps its team whatever the question says.
    kept: dict[str, Any] = {"player": "Stephen Curry", "team": "Boston Celtics"}
    _scope(scope_con, "curry vs the celtics", kept, reads_player=False)
    assert kept["team"] == "Boston Celtics"


def test_both_sides_of_a_head_to_head_are_left_alone(scope_con: duckdb.DuckDBPyConnection) -> None:
    slots: dict[str, Any] = {"teams": ["Los Angeles Lakers", "Boston Celtics"]}
    assert _scope(scope_con, "Lakers vs Celtics record this season", slots, reads_player=False) == []
    assert slots == {"teams": ["Los Angeles Lakers", "Boston Celtics"]}


def test_an_opponent_the_router_already_gave_is_left_alone(scope_con: duckdb.DuckDBPyConnection) -> None:
    slots: dict[str, Any] = {"team": "Philadelphia 76ers", "opponent": "Boston Celtics", "period": 4}
    assert _scope(scope_con, "76ers 4th quarter points against boston", slots, reads_player=False) == []


def test_a_player_comparison_names_no_opponent(scope_con: duckdb.DuckDBPyConnection) -> None:
    slots: dict[str, Any] = {"players": ["LeBron James", "Kawhi Leonard"]}
    assert _scope(scope_con, "lebron vs kawhi 2015", slots, reads_player=True) == []
    assert slots == {"players": ["LeBron James", "Kawhi Leonard"]}


def test_la_is_two_teams_and_names_no_opponent(scope_con: duckdb.DuckDBPyConnection) -> None:
    slots: dict[str, Any] = {"player": "Stephen Curry"}
    assert _scope(scope_con, "curry vs la", slots, reads_player=True) == []


def test_a_team_nickname_grounds_the_team_slot(scope_con: duckdb.DuckDBPyConnection) -> None:
    """ "sixers" is no word of "Philadelphia 76ers", but it is the team."""
    slots: dict[str, Any] = {"team": "Philadelphia 76ers"}
    assert _scope(scope_con, "Top 5 scorers on the sixers", slots, reads_player=True) == []
    assert slots == {"team": "Philadelphia 76ers"}


def test_a_team_written_without_its_space_still_names_the_team(scope_con: duckdb.DuckDBPyConnection) -> None:
    """ "trailblazers stats last 10 games 3 point average 1st quarter" lost its
    team: the grounding check asks whether the question holds a WORD of the
    name, and the single token matches none of {portland, trail, blazers}, so
    a team the question opens with was dropped as one it never mentioned. The
    team's own name with a space left out is a trace of it like any other."""
    slots: dict[str, Any] = {"team": "Portland Trail Blazers", "period": 1}
    # reads_player as the pipeline passes it: team_quarter_points is in
    # PLAYER_INTENTS, and it is that path which drops an ungrounded team.
    assert _scope(scope_con, "trailblazers stats last 10 games 3 point average 1st quarter", slots, reads_player=True) == []
    assert slots == {"team": "Portland Trail Blazers", "period": 1}


def test_a_run_together_name_reaches_the_team_it_spells(scope_con: duckdb.DuckDBPyConnection) -> None:
    """The nickname is only the visible half: six of the thirty teams have a
    two-word CITY, and "goldenstate" resolved to nothing either - so a
    question setting a subject against one lost the opponent outright."""
    slots: dict[str, Any] = {"player": "Jaylen Brown"}
    _scope(scope_con, "jaylen brown last 10 games vs goldenstate", slots, reads_player=True)
    assert slots["opponent"] == "Golden State Warriors"
    for spelling in ("trailblazers", "portlandtrailblazers", "goldenstate", "newyork", "laclippers"):
        assert isinstance(resolve_team(scope_con, spelling), Entity), spelling


def test_part_of_a_team_name_run_together_still_names_no_team(scope_con: duckdb.DuckDBPyConnection) -> None:
    """The letters have to equal a whole run of the name's own words, in its
    own order. A fragment is the substring guess `_team_named` exists to
    refuse. "la" is two teams; the rest are the name's words reordered, or one
    of them cut short."""
    for spelling in ("la", "blazerstrail", "yorknew", "trailblaze", "portlandblazers"):
        assert not isinstance(resolve_team(scope_con, spelling), Entity), spelling


def _rerouted(con: duckdb.DuckDBPyConnection, question: str, intent: str, slots: dict[str, Any]) -> str:
    """The intent the reading settles for the question, with `slots` rewritten for it."""
    from association.query.subject import apply_subject, read_subject

    return apply_subject(read_subject(con, question, intent, slots), slots, con=con, intent=intent).intent


def test_a_players_record_against_a_team_is_not_two_teams_meeting(scope_con: duckdb.DuckDBPyConnection) -> None:
    """#163: the router sends "Embiid career record vs boston" to head_to_head,
    which is two franchises meeting - a different question, counting every
    meeting including the ones he sat out. It arrives two ways: with the player
    in the TEAM list, and with him replaced by his own team and named only in
    the question. Both become with_without, whose split is exactly "the games
    he played against the ones he missed"."""
    scope_con.execute("INSERT INTO players VALUES ('20','Joel Embiid')")
    in_teams: dict[str, Any] = {"teams": ["Joel Embiid", "Boston Celtics"], "season_type": 2, "span": "career"}
    assert _rerouted(scope_con, "Embiid career record vs boston", "head_to_head", in_teams) == "with_without"
    assert in_teams == {"season_type": 2, "span": "career", "without": ["Joel Embiid"], "opponent": "Boston Celtics"}
    displaced: dict[str, Any] = {"stat": "wins", "teams": ["Philadelphia 76ers", "Boston Celtics"], "season_type": 2, "span": "career"}
    assert _rerouted(scope_con, "Show Embiid's career record against Boston", "head_to_head", displaced) == "with_without"
    # `players_named_in` returns the roster's own spelling, not the question's.
    assert displaced["without"] == ["Joel Embiid"] and displaced["opponent"] == "Boston Celtics"
    # The team slots must not survive: they are the reading being replaced.
    assert "teams" not in displaced and "team" not in displaced


def test_a_real_head_to_head_is_left_exactly_as_it_was(scope_con: duckdb.DuckDBPyConnection) -> None:
    """The other half. A question naming no player, one naming a player but no
    opponent, and any other intent all come back None, so nothing that answers
    today can move."""
    # He has to be on the roster, or the "no record asked" case below would
    # come back None because nothing named a player at all - passing for a
    # reason that has nothing to do with the rule being tested.
    scope_con.execute("INSERT INTO players VALUES ('20','Joel Embiid')")
    for question, intent, slots in (
        ("Lakers vs Celtics record this season", "head_to_head", {"teams": ["Los Angeles Lakers", "Boston Celtics"], "season": 2026}),
        # "boston" names Brandon Boston Jr. by word, and must not be read as
        # the subject of his own opponent's question.
        ("celtics record vs boston", "head_to_head", {"teams": ["Boston Celtics", "Boston Celtics"]}),
        ("jaylen brown last 8 games vs pistons", "game_log", {"player": "Jaylen Brown"}),
        # A player and an opponent, but no record asked for: "Embiid vs boston
        # last 5 games" wants his games, and answering it with a won-lost
        # split would be a different question, fluently.
        ("Embiid vs boston last 5 games", "head_to_head", {"teams": ["Joel Embiid", "Boston Celtics"], "limit": 5}),
        ("Lakers vs Celtics this season", "head_to_head", {"teams": ["Los Angeles Lakers", "Boston Celtics"]}),
    ):
        assert _rerouted(scope_con, question, intent, dict(slots)) == intent, question


def test_a_team_nickname_names_an_opponent(scope_con: duckdb.DuckDBPyConnection) -> None:
    slots: dict[str, Any] = {"player": "Jaylen Brown"}
    _scope(scope_con, "jaylen brown against the sixers", slots, reads_player=True)
    assert slots["opponent"] == "Philadelphia 76ers"


def test_a_player_is_only_restored_where_a_template_reads_one(scope_con: duckdb.DuckDBPyConnection) -> None:
    slots: dict[str, Any] = {"team": "Boston Celtics"}
    _scope(scope_con, "jaylen brown last 8 games vs pistons", slots, reads_player=False)
    assert slots == {"team": "Boston Celtics", "opponent": "Detroit Pistons"}


def test_a_player_left_out_is_restored_only_where_one_is_required(scope_con: duckdb.DuckDBPyConnection) -> None:
    """ "Sga record 36 plus points" came back with no player at all. An optional
    player slot left empty means the league, so only a template that needs one
    gets it back."""
    slots: dict[str, Any] = {"stat": "points", "threshold": 36}
    _scope(scope_con, "Sga record 36 plus points", slots, reads_player=True, needs_player=True)
    assert slots["player"] == "Shai Gilgeous-Alexander"
    optional: dict[str, Any] = {"stat": "points", "threshold": 36}
    _scope(scope_con, "Sga record 36 plus points", optional, reads_player=True)
    assert "player" not in optional


def test_a_subject_named_with_no_verb_is_restored_for_single_game_high(scope_con: duckdb.DuckDBPyConnection) -> None:
    """yardstick-v2 F093: "kawhi most threes in a game" used to answer the
    league's single-game leaders, Kawhi Leonard's own 7 never mentioned -
    router._SUBJECT_OF_HIGH needs a scoring verb ("kawhi scored") or a
    possessive ("kawhi's"), and this shape has neither. `restore_subject`
    (SUBJECT_RESTORABLE_INTENTS: single_game_high, threshold_count) reads
    him back from the question the same way `needs_player` already does for
    a template with no league reading at all."""
    slots: dict[str, Any] = {"stat": "threePointFieldGoalsMade"}
    _scope(scope_con, "kawhi most threes in a game", slots, reads_player=True, restore_subject=True)
    assert slots["player"] == "Kawhi Leonard"
    # Off by default, same as needs_player: a caller that does not ask for it
    # (an intent outside SUBJECT_RESTORABLE_INTENTS) gets the league reading.
    unrestored: dict[str, Any] = {"stat": "threePointFieldGoalsMade"}
    _scope(scope_con, "kawhi most threes in a game", unrestored, reads_player=True)
    assert "player" not in unrestored


@pytest.mark.parametrize(
    "question",
    [
        # yardstick-v2 F093's own false-positive check: "best" is Travis
        # Best and "head" is Luther Head, and neither question is about
        # either of them - both are genuine league/team questions.
        # Corpus-measured (scripts/check_routing.py + the StatMuse feed,
        # entities._COMMON_WORDS_THAT_NAME_PLAYERS) before restore_subject
        # shipped.
        "Best true shooting percentage last season?",
        "Best record from 2010-11 to 2018-19 nba",
        "Celtics vs Bulls head to head record",
    ],
)
def test_a_common_english_word_is_not_restored_as_a_player(scope_con: duckdb.DuckDBPyConnection, question: str) -> None:
    slots: dict[str, Any] = {"stat": "ts_pct"}
    _scope(scope_con, question, slots, reads_player=True, restore_subject=True)
    assert "player" not in slots


def test_a_team_named_beside_a_player_with_no_season_is_his_tenure(scope_con: duckdb.DuckDBPyConnection) -> None:
    """yardstick-v2 F166: "lebron stats as a starter for Miami" used to
    answer his current season, "Miami" never read at all - the router filed
    no `team` and no `opponent`. `restore_team` reads "for <team>" beside an
    already-known player and, since no season is named either, defaults
    `span` to "career" too - a historical team names a tenure, not "now".
    Written to `own_team`, not `team` - see
    subject._apply_own_team's own docstring for why a
    router-supplied `team` is not safe to trust directly."""
    slots: dict[str, Any] = {"player": "LeBron James", "stat": "points", "season": 2026, "split": "starter"}
    notes = _scope(scope_con, "lebron stats as a starter for the lakers", slots, reads_player=True, restore_team=True)
    assert slots["own_team"] == "Los Angeles Lakers"
    assert slots["span"] == "career"
    assert "season" not in slots
    assert any("Los Angeles Lakers" in note for note in notes)
    # A season the question DOES name still wins - no career override.
    named: dict[str, Any] = {"player": "LeBron James", "stat": "points", "season": 2026, "split": "starter"}
    _scope(scope_con, "lebron stats as a starter for the lakers in 2026", named, reads_player=True, restore_team=True)
    assert named["own_team"] == "Los Angeles Lakers"
    assert named.get("span") != "career"
    assert named["season"] == 2026
    # Off by default, same as restore_subject: a caller that does not ask
    # for it (an intent outside OWN_TEAM_RESTORABLE_INTENTS) leaves
    # `own_team` alone, since nothing on that relation could honor it either
    # way.
    unrestored: dict[str, Any] = {"player": "LeBron James", "stat": "points", "season": 2026}
    _scope(scope_con, "lebron stats as a starter for the lakers", unrestored, reads_player=True)
    assert "own_team" not in unrestored


def test_an_own_team_is_not_restored_with_no_player_or_over_an_existing_opponent(scope_con: duckdb.DuckDBPyConnection) -> None:
    """A bare "for <team>" with no player at all is a team question, not
    this one - and an `opponent` the question already names (a genuine "vs"
    reading) is not overwritten by a coincidental "for" phrase elsewhere in
    the question."""
    no_player: dict[str, Any] = {"stat": "points"}
    _scope(scope_con, "points scored for the lakers this season", no_player, reads_player=True, restore_team=True)
    assert "own_team" not in no_player
    has_opponent: dict[str, Any] = {"player": "LeBron James", "opponent": "Boston Celtics"}
    _scope(scope_con, "lebron vs celtics stats for the lakers", has_opponent, reads_player=True, restore_team=True)
    assert "own_team" not in has_opponent and has_opponent["opponent"] == "Boston Celtics"


def test_a_router_supplied_team_is_not_trusted_as_a_tenure_narrowing(scope_con: duckdb.DuckDBPyConnection) -> None:
    """The recorded shape that motivated `own_team` over reusing `team`:
    "lebron james 2 3 pointers all-time vs jazz on tuesdays" carries a
    router-supplied `team='Los Angeles Lakers'` (his own, current, and
    redundant) beside a real `opponent='Utah Jazz'` - noise
    `games._team_slot_for_player` already drops for `game_log`. `restore_team`
    must not promote that same noise into a real narrowing for `player_stat`:
    with `team` already set, `subject._apply_own_team` declines
    outright, so `own_team` is never written and the recorded `team` value
    is left exactly as it was."""
    slots: dict[str, Any] = {"player": "LeBron James", "team": "Los Angeles Lakers", "opponent": "Utah Jazz", "span": "career"}
    _scope(scope_con, "lebron james 2 3 pointers all-time vs jazz on tuesdays", slots, reads_player=True, restore_team=True)
    assert "own_team" not in slots
    assert slots["team"] == "Los Angeles Lakers"


def test_a_team_only_question_naming_one_player_is_read_back(scope_con: duckdb.DuckDBPyConnection) -> None:
    """yardstick-v2 F111: "alperen şengün alltime record" routed to
    team_leaderboard - no player slot on that intent, and no `team` slot
    either - and answered the league standings, Sengun never read.
    Diacritics are already folded (players_named_in._fold)."""
    slots: dict[str, Any] = {"stat": "record", "limit": 1}
    named = player_named_on_a_team_only_question(scope_con, "alperen şengün alltime record", slots)
    assert named == "Alperen Sengun"
    message = team_only_question_names_a_player(named, "team_leaderboard")
    assert "Alperen Sengun" in message and "team leaderboard" in message


def test_a_team_named_wins_over_a_coincidental_player_word(scope_con: duckdb.DuckDBPyConnection) -> None:
    """A `team` (or `teams`) slot already present, and resolving to a REAL
    franchise, means the question is genuinely about that team - a player
    named beside it, by a possessive or a nickname, changes no answer, the
    same reasoning that leaves a stray name alone on head_to_head elsewhere
    in this module."""
    with_team: dict[str, Any] = {"stat": "record", "team": "Boston Celtics"}
    assert player_named_on_a_team_only_question(scope_con, "alperen şengün celtics record", with_team) is None
    with_teams: dict[str, Any] = {"stat": "record", "teams": ["Boston Celtics", "Orlando Magic"]}
    assert player_named_on_a_team_only_question(scope_con, "alperen şengün celtics vs magic", with_teams) is None


def test_an_invented_team_holding_the_players_own_name_does_not_block_the_refusal(scope_con: duckdb.DuckDBPyConnection) -> None:
    """Measured live: "alperen şengün alltime record" arrived one run with
    no `team` slot at all, and another with `team='Alperen Şengün'` - the
    player's own name, filed as though it were a franchise
    (AGENTS.md, "the router invents names", the team-slot version). A team
    slot nothing resolves is functionally the same as no team slot -
    otherwise `team_leaderboard` would refuse "no team matching 'Alperen
    Şengün'" instead, the same wrong-cause shape this check exists to fix."""
    invented: dict[str, Any] = {"stat": "record", "team": "Alperen Şengün"}
    assert player_named_on_a_team_only_question(scope_con, "alperen şengün alltime record", invented) == "Alperen Sengun"


def test_a_real_team_the_question_never_names_does_not_block_the_refusal(scope_con: duckdb.DuckDBPyConnection) -> None:
    """yardstick-v2 F110: "towns home rec including playoffs since 1/26/20 vs
    spurs" routed team_record with `team='Toronto Raptors'` - a real
    franchise with no word in the question - and would have answered the
    Raptors' record about Karl-Anthony Towns. An ungrounded team is no team;
    the question names one player, so it is refused by his name. A team
    the question does name ("lakers") still wins."""
    scope_con.execute("INSERT INTO teams VALUES ('28','Toronto Raptors','TOR')")
    invented: dict[str, Any] = {"stat": "wins", "team": "Toronto Raptors", "venue": "home", "opponent": "San Antonio Spurs"}
    assert player_named_on_a_team_only_question(scope_con, "towns home rec including playoffs since 1/26/20 vs spurs", invented) == "Karl-Anthony Towns"
    named: dict[str, Any] = {"stat": "wins", "team": "Los Angeles Lakers"}
    assert player_named_on_a_team_only_question(scope_con, "towns lakers home rec", named) is None


def test_a_common_word_is_not_read_as_the_team_only_questions_player(scope_con: duckdb.DuckDBPyConnection) -> None:
    """ "best" is Travis Best and "head" is Luther Head - the same
    false-positive trap F093's restore_subject was measured against, applied
    here too."""
    slots: dict[str, Any] = {"stat": "record", "limit": 1}
    assert player_named_on_a_team_only_question(scope_con, "Best record from 2010-11 to 2018-19 nba", slots) is None
    assert player_named_on_a_team_only_question(scope_con, "Celtics vs Bulls head to head record", slots) is None


def test_teams_named_in_reads_a_whole_word_span(scope_con: duckdb.DuckDBPyConnection) -> None:
    """The team counterpart of players_named_in: a run of up to three words
    that names one team, longest span first, so "trail blazers" and
    "los angeles lakers" are each read as one team rather than falling back
    to a single failed word."""
    assert [t.name for t in teams_named_in(scope_con, "how did the lakers do")] == ["Los Angeles Lakers"]
    assert [t.name for t in teams_named_in(scope_con, "warriors vs magic last night")] == ["Golden State Warriors", "Orlando Magic"]
    assert teams_named_in(scope_con, "how many points did luka score") == []


def test_teams_named_in_ignores_common_words_that_collide_with_abbreviations(scope_con: duckdb.DuckDBPyConnection) -> None:
    """_team_named matches an abbreviation with no length floor of its own,
    so a bare scan without a guard reads "was" as the Washington Wizards
    (abbreviation WAS) and "in" as the Indiana Pacers (IN) - measured
    against the full routing corpus before this shipped (78 false
    candidates with no guard at all, 61 after a length-3 floor alone, 0
    once "was"/"min" were excluded outright). Neither collision is an NBA
    team the question means."""
    scope_con.execute("INSERT INTO teams VALUES ('23','Washington Wizards','WAS'),('24','Indiana Pacers','IN')")
    assert teams_named_in(scope_con, "What was the highest scoring game by a player this year?") == []
    assert teams_named_in(scope_con, "Most games with 15+ assists in 2024?") == []
    # A real mention of either team still resolves - the guard is on the
    # coincidental short word, not on the teams themselves.
    assert [t.name for t in teams_named_in(scope_con, "least points scored by the wizards in the first half")] == ["Washington Wizards"]


def test_scope_from_question_restores_a_dropped_team_subject_for_leaderboard(scope_con: duckdb.DuckDBPyConnection) -> None:
    """yardstick-v2 F127: "how many 3 pointers have the magic made so far
    this season" arrived at `leaderboard` with `stat`/`season` only, no
    `team` at all, and ranked the league's individual leaders instead of
    answering the Magic's own total. `restore_team_subject` puts the team
    back into `team` (so `query.compose` can see it) AND marks it
    `team_restored` for `leaderboard` alone, forcing `check_scope` to refuse
    rather than let `leaderboard` rank players "on" a team that was meant to
    be the whole subject."""
    slots: dict[str, Any] = {"stat": "threePointFieldGoalsMade", "season": 2026, "season_type": 2}
    notes = _scope(scope_con, "how many 3 pointers have the magic made so far this season", slots, reads_player=False, restore_team_subject=True, intent="leaderboard")
    assert slots["team"] == "Orlando Magic"
    assert slots["team_restored"] is True
    assert any("Orlando Magic" in note for note in notes)


def test_scope_from_question_restores_a_dropped_team_subject_for_team_stat_with_no_marker(scope_con: duckdb.DuckDBPyConnection) -> None:
    """`team_stat` already raises TemplateUnsupported("no team named") on an
    empty `team` on its own (`_resolved_team`), so restoring the team there
    is a strict improvement and needs no refusing marker - unlike
    `leaderboard`, which has its own, different, legitimate reading of
    `team` that must keep answering directly."""
    slots: dict[str, Any] = {"stat": "pace"}
    _scope(scope_con, "knicks pace this season", slots, reads_player=False, restore_team_subject=True, intent="team_stat")
    assert slots["team"] == "New York Knicks"
    assert "team_restored" not in slots


def test_restore_team_subject_does_not_fire_with_a_player_already_present(scope_con: duckdb.DuckDBPyConnection) -> None:
    """A bare team word beside an already-known player is a different
    question - `own_team`'s - and a team-only intent naming a player with no
    team of its own is the F111 refusal-by-name shape; this restore must not
    quietly paper over either one."""
    slots: dict[str, Any] = {"stat": "points", "player": "LeBron James"}
    _scope(scope_con, "lebron points for the lakers this season", slots, reads_player=True, restore_team_subject=True, intent="leaderboard")
    assert "team" not in slots and "team_restored" not in slots


def test_restore_team_subject_does_not_fire_on_an_ambiguous_or_absent_team(scope_con: duckdb.DuckDBPyConnection) -> None:
    """Never a guess: zero teams named, or more than one, leaves the slots
    untouched and the question falls through exactly as it did before this
    restore existed."""
    none_named: dict[str, Any] = {"stat": "points"}
    _scope(scope_con, "who led the league in scoring", none_named, reads_player=False, restore_team_subject=True, intent="leaderboard")
    assert "team" not in none_named
    two_named: dict[str, Any] = {"stat": "points"}
    _scope(scope_con, "76ers vs magic total points", two_named, reads_player=False, restore_team_subject=True, intent="leaderboard")
    assert "team" not in two_named


def test_restore_team_subject_is_off_by_default(scope_con: duckdb.DuckDBPyConnection) -> None:
    """Same discipline as the player and own-team restores: an intent
    outside TEAM_SUBJECT_RESTORABLE_INTENTS leaves `team` alone."""
    slots: dict[str, Any] = {"stat": "points"}
    _scope(scope_con, "how many 3 pointers have the magic made so far this season", slots, reads_player=False, intent="team_record")
    assert "team" not in slots


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
    slots: dict[str, Any] = {"player": "Curry", "team": "Podziemski", "without": ["curry"]}
    _scope(scope_con, "Podziemski game log without curry", slots, reads_player=True)
    assert slots["player"] == "Brandin Podziemski" and "team" not in slots and slots["without"] == ["curry"]


def test_an_ambiguous_fragment_in_the_team_slot_is_settled_by_the_question(scope_con: duckdb.DuckDBPyConnection) -> None:
    """Measured: "Will Riley last 5 game s" arrived as team='Riley', and
    find_players alone cannot settle it - three Rileys share the surname. The
    question spells the whole name, so players_named_in does, the same
    discipline subject.apply_subject applies to a name the router
    invented outright rather than merely truncated."""
    scope_con.execute("INSERT INTO players VALUES ('9','Eric Riley'),('10','Riley Minix'),('11','Will Riley')")
    slots: dict[str, Any] = {"team": "Riley", "order": "recent", "limit": 5}
    notes = _scope(scope_con, "Will Riley last 5 game s", slots, reads_player=True)
    assert slots == {"order": "recent", "limit": 5, "player": "Will Riley"}
    assert len(notes) == 1 and "'Riley' is a player, not a team; the subject is 'Will Riley'" in notes[0]


def test_the_fragment_fallback_does_not_borrow_an_unrelated_name(scope_con: duckdb.DuckDBPyConnection) -> None:
    """A garbled `team` sharing no word with the one player the question
    happens to name elsewhere must not borrow that name - only a fragment
    that is actually part of the recovered name is safe to trust."""
    slots: dict[str, Any] = {"team": "Zqx", "order": "recent"}
    notes = _scope(scope_con, "Zqx last 5 games, a Luka Doncic fan favorite", slots, reads_player=True)
    assert slots["team"] == "Zqx" and "player" not in slots
    assert notes == []


# ---------------- team names across a franchise's history ----------------


@pytest.fixture
def franchises() -> duckdb.DuckDBPyConnection:
    """ESPN's real ids and today's names, with the columns the real `teams` has.

    The ids are the point: ESPN keys a team by FRANCHISE, so id 17 is the Nets
    whether they played in New Jersey or Brooklyn, id 3 was the Charlotte
    Hornets, the New Orleans Hornets and is now the Pelicans, and id 30 was the
    Bobcats before it was today's Hornets. Lakers and Clippers are here for
    Los Angeles; Kings and Blazers for the two garbled cities the router wrote.
    """
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE teams (team_id VARCHAR, abbreviation VARCHAR, display_name VARCHAR, location VARCHAR, name VARCHAR)")
    c.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    c.execute(
        "INSERT INTO teams VALUES "
        "('17','BKN','Brooklyn Nets','Brooklyn','Nets'),('3','NO','New Orleans Pelicans','New Orleans','Pelicans'),"
        "('30','CHA','Charlotte Hornets','Charlotte','Hornets'),('25','OKC','Oklahoma City Thunder','Oklahoma City','Thunder'),"
        "('22','POR','Portland Trail Blazers','Portland','Trail Blazers'),('23','SAC','Sacramento Kings','Sacramento','Kings'),"
        "('13','LAL','Los Angeles Lakers','Los Angeles','Lakers'),('12','LAC','LA Clippers','LA','Clippers'),('8','DET','Detroit Pistons','Detroit','Pistons')"
    )
    return c


def _teams(con: duckdb.DuckDBPyConnection, text: str, season: int | None = None) -> list[tuple[str, str]]:
    return [(team.id, team.name) for team in find_teams(con, text, season)]


def test_a_former_name_resolves_to_the_franchise(franchises: duckdb.DuckDBPyConnection) -> None:
    """ "duren v nets 1h gameloh" arrived as opponent="New Jersey Nets" in every
    replay since the first, and it resolved to nothing - hidden until a template
    finally read the slot. The Nets are one franchise in every season."""
    assert [team_id for team_id, _ in _teams(franchises, "New Jersey Nets")] == ["17"]
    assert _teams(franchises, "Nets", 2005) == [("17", "New Jersey Nets")]
    assert _teams(franchises, "Sonics", 2005) == [("25", "Seattle SuperSonics")]


def test_a_name_that_moved_between_franchises_is_read_for_its_season(franchises: duckdb.DuckDBPyConnection) -> None:
    """The one that answered wrongly rather than not at all. "Hornets record
    2008" came back with the 2008 Charlotte BOBCATS' 32-50, because only
    today's names were matched. In 2008 the Hornets were New Orleans, id 3,
    and went 56-26."""
    assert _teams(franchises, "Hornets", 2008) == [("3", "New Orleans Hornets")]
    assert _teams(franchises, "Hornets", 2000) == [("3", "Charlotte Hornets")]
    assert _teams(franchises, "Hornets", 2026) == [("30", "Charlotte Hornets")]
    assert _teams(franchises, "Charlotte", 2010) == [("30", "Charlotte Bobcats")]


def test_a_name_nobody_held_that_season_asks_rather_than_guesses(franchises: duckdb.DuckDBPyConnection) -> None:
    """No team was called the Hornets in 2014 - id 3 had just become the
    Pelicans and id 30 was still the Bobcats. Both are offered, under the names
    they carried that year, and resolve_team turns that into a question."""
    assert _teams(franchises, "Hornets", 2014) == [("3", "New Orleans Pelicans"), ("30", "Charlotte Bobcats")]
    assert isinstance(resolve_team(franchises, "Hornets", 2014), Ambiguous)


def test_a_current_name_asked_about_before_it_existed_still_means_the_franchise(franchises: duckdb.DuckDBPyConnection) -> None:
    """ "Pelicans in 2008" names nobody that year, but only one franchise has
    ever held it, so it is that franchise under the name it had then."""
    assert _teams(franchises, "Pelicans", 2008) == [("3", "New Orleans Hornets")]


def test_a_garbled_city_is_read_by_its_nickname_unless_the_city_contradicts_it(franchises: duckdb.DuckDBPyConnection) -> None:
    """The router expands a nickname into a full name and sometimes invents the
    city. "Portland Blazers" is right about the city and short a word - ESPN
    writes Portland Trail Blazers - and resolves. "Los Angeles Kings" names a
    city that belongs to two OTHER teams; picking the nickname over it would be
    a guess, so it resolves to nothing here and the question's own words settle
    it in scope_from_question."""
    assert _teams(franchises, "Portland Blazers") == [("22", "Portland Trail Blazers")]
    assert _teams(franchises, "Los Angeles Kings") == []
    assert _teams(franchises, "LA") == [("12", "LA Clippers"), ("13", "Los Angeles Lakers")], "a real ambiguity stays one"


def test_a_franchise_id_is_trusted_only_where_the_warehouse_agrees() -> None:
    """The era table names ESPN's ids. A warehouse that files a different team
    under one of them must not be renamed or resolved through it - the first
    version of this renamed a Detroit Pistons filed under "3" to the New
    Orleans Pelicans."""
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE teams (team_id VARCHAR, abbreviation VARCHAR, display_name VARCHAR, location VARCHAR, name VARCHAR)")
    c.execute("INSERT INTO teams VALUES ('3','DET','Detroit Pistons','Detroit','Pistons')")
    assert _teams(c, "Detroit Pistons", 2008) == [("3", "Detroit Pistons")]
    assert _teams(c, "Hornets", 2008) == []


@pytest.mark.parametrize(
    ("question", "held", "want"),
    [
        # The router's string resolves to no team; the question names one.
        ("steve adam's vs kings last 10 games", "Los Angeles Kings", "Sacramento Kings"),
        # The router's is a real team the question never mentions.
        ("tatum vs lakers", "Portland Trail Blazers", "Los Angeles Lakers"),
        # The router got it right, in a form the question does not use.
        ("tatum vs lakers", "Los Angeles Lakers", "Los Angeles Lakers"),
        # Nothing after "vs" is a team, so there is nothing to correct it with.
        ("curry vs lebron", "Lakers", "Lakers"),
    ],
)
def test_the_questions_own_opponent_beats_one_the_router_could_not_ground(franchises: duckdb.DuckDBPyConnection, question: str, held: str, want: str) -> None:
    """scope_from_question already read the team after "vs" correctly - for
    "duren v nets" it found the Brooklyn Nets - and then only used it when the
    `opponent` slot was EMPTY. A router string that resolves to nothing, or to a
    team the question never names, counted as filled, so it won. Same rule as
    subject.apply_subject: the question is the source. The player is the
    question's own first word: a router name the question never held is
    refused by name before any opponent matters."""
    slots: dict[str, Any] = {"player": question.split()[0], "opponent": held}
    _scope(franchises, question, slots, reads_player=True)
    assert slots["opponent"] == want


def test_a_team_after_vs_filed_in_teams_is_a_players_opponent(franchises: duckdb.DuckDBPyConnection) -> None:
    """ "Keyonte George against blazers" arrived as teams=["Portland Blazers"].
    It was answered correctly only while that name failed to resolve: once it
    did, the "slots already carry this team" rule - written for head_to_head,
    which reads `teams` as its two sides - left it there, and player_stat, which
    never reads `teams`, answered his whole season instead of his games against
    Portland. With a player as the subject it is his opponent."""
    slots: dict[str, Any] = {"player": "Keyonte George", "teams": ["Portland Blazers"]}
    _scope(franchises, "Keyonte George against blazers", slots, reads_player=True)
    assert slots.get("opponent") == "Portland Trail Blazers" and "teams" not in slots


def test_two_teams_with_no_player_stay_a_head_to_head(franchises: duckdb.DuckDBPyConnection) -> None:
    """The case the carried-team rule exists for, and must keep: no player, so
    `teams` are the two sides and nothing is an opponent."""
    slots: dict[str, Any] = {"teams": ["Sacramento Kings", "Portland Trail Blazers"]}
    _scope(franchises, "kings vs blazers", slots, reads_player=False)
    assert slots == {"teams": ["Sacramento Kings", "Portland Trail Blazers"]}


def test_a_player_swapped_into_the_opponent_slot_is_replaced_by_the_questions_team(franchises: duckdb.DuckDBPyConnection) -> None:
    """ "andrew wiggins last 15 games vs warriors" arrived with the two slots
    swapped - team="Golden State Warriors", opponent="Andrew Wiggins". The player
    was already moved into `player`, but the opponent kept his name, which
    resolves to no team, and the question fell through. The team after "vs" is
    the opponent."""
    franchises.execute("INSERT INTO teams VALUES ('9','GS','Golden State Warriors','Golden State','Warriors')")
    slots: dict[str, Any] = {"player": "Andrew Wiggins", "opponent": "Andrew Wiggins"}
    _scope(franchises, "andrew wiggins last 15 games vs warriors", slots, reads_player=True)
    assert slots["opponent"] == "Golden State Warriors"


def test_a_team_named_before_the_player_is_not_his_tenure(scope_con: duckdb.DuckDBPyConnection) -> None:
    """yardstick-v2 F087 "show me stats for sixers when maxey scored 20+
    points": the team is the subject and the player follows as a condition,
    so "for sixers" is not his tenure with them - the own-team reading needs
    the player named BEFORE the "for <team>" phrase, the order a tenure is
    asked in ("lebron ... for Miami")."""
    slots: dict[str, Any] = {"player": "Alperen Sengun", "stat": "points", "season": 2026}
    _scope(scope_con, "show me stats for the celtics when sengun scored 20+ points", slots, reads_player=True, restore_team=True)
    assert "own_team" not in slots
    tenure: dict[str, Any] = {"player": "Alperen Sengun", "stat": "points", "season": 2026}
    _scope(scope_con, "sengun stats as a starter for the celtics", tenure, reads_player=True, restore_team=True)
    assert tenure["own_team"] == "Boston Celtics"
