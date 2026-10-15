"""Tests for name -> id resolution, and specifically for the distinction
between find_* (caller decides) and resolve_* (never guesses)."""

from typing import Any, NamedTuple

import duckdb
import pytest

from association.query import names as name_index
from association.query.entities import (
    Ambiguous,
    Availability,
    Entity,
    NotFound,
    collect_name_readings,
    find_players,
    find_teams,
    misread_players,
    names_of,
    players_of,
    read_near_spelling,
    resolve_player,
    resolve_team,
    suggest_players,
    teams_of,
)
from association.query.reading import (
    GAME_HIGHS,
    LINE_RECORD,
    NETPOINTS_FINGERPRINT,
    PLAYER_LINE,
    PLAYER_LOG,
    PLAYER_RANKING,
    PLAYER_SPLITS,
    TEAM_MEETINGS,
    TEAM_RANKING,
    TEAM_RECORD,
    Cuts,
    Line,
    Measure,
    Period,
    PointShape,
    Reading,
    Span,
    Window,
)
from association.query.reading import Subject as Who
from association.query.subject import compared_but_unmatched, nicknames_in, player_named_on_a_team_only_question, players_named_in


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
        # 12-15 are names a model invents and what it invents them next to.
        # John S. Williams is real, and is what a possessive "Jokic's" matches
        # if a stray one-letter word is allowed to name somebody.
        "('12','Joel Embiid'),('13','Jusuf Nurkic'),('14','John S. Williams'),('15','Klay Thompson'),"
        # 16-17 are ordinary words inside a name: "game" lands inside
        # Blossomgame and "with" starts Withey, so an ordinary question reads
        # as naming both unless a span has to equal a whole word.
        "('16','Jaron Blossomgame'),('17','Jeff Withey'),"
        # 18 is the false positive a comparison gate exists for: "best"
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
        # A full team name people write; ESPN stores "LA Clippers".
        ("Los Angeles Clippers", "LA Clippers"),
    ],
)
def test_the_abbreviations_people_use_resolve(league_teams: duckdb.DuckDBPyConnection, text: str, want: str) -> None:
    """The abbreviation or full name people write reaches the team ESPN
    stores under another, so "Lauri Markkan vs GSW last 5 games" keeps its
    team."""
    assert [e.name for e in find_teams(teams_of(league_teams), text)] == [want]


def test_an_abbreviation_outranks_a_team_whose_name_merely_contains_it(league_teams: duckdb.DuckDBPyConnection) -> None:
    """ "ORL" is Orlando's abbreviation and also a substring of "New Orleans":
    the abbreviation wins, so a question naming exactly one team is neither
    asked back nor answered about the other."""
    assert [e.name for e in find_teams(teams_of(league_teams), "ORL")] == ["Orlando Magic"]


def test_two_teams_in_one_city_stay_ambiguous(league_teams: duckdb.DuckDBPyConnection) -> None:
    """The ranking settles an abbreviation against a name, and must not settle
    a real ambiguity: no team is abbreviated "LA", so both Los Angeles teams
    sit in the same tier and the caller still has to ask."""
    assert [e.name for e in find_teams(teams_of(league_teams), "LA")] == ["LA Clippers", "Los Angeles Lakers"]


def test_resolve_player_refuses_to_guess_between_two_real_players(con: duckdb.DuckDBPyConnection) -> None:
    """ "Curry" is Seth and Stephen. A ranking's row attributed to the wrong
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
    assert find_players(players_of(con), "   ") == []
    assert isinstance(resolve_player(con, "   "), NotFound)


def test_a_typo_inside_a_hyphenated_surname_reaches_its_player(con: duckdb.DuckDBPyConnection) -> None:
    """ISSUES.md #241: "Jackson-Davs" reaches Trayce Jackson-Davis. Each
    typed token is split as a name's words are, and every piece must be near
    one of them."""
    from association.query.entities import read_near_spelling, suggest_players

    con.execute("INSERT INTO players VALUES ('41', 'Trayce Jackson-Davis')")
    assert [e.name for e in suggest_players(names_of(con), "Jackson-Davs")] == ["Trayce Jackson-Davis"]
    assert (read_near_spelling(names_of(con), "Jackson-Davs") or Entity("", "")).name == "Trayce Jackson-Davis"
    assert suggest_players(names_of(con), "Jackson-Smith") == []  # every piece must be near a word of ONE name


def test_a_typo_near_too_many_players_is_narrowed_to_the_latest_seasons_before_it_is_dropped(con: duckdb.DuckDBPyConnection) -> None:
    """ISSUES.md #240: "crry" is within one edit of six Currys, more than a
    suggestion names. Narrowed to the players with a box score in the latest
    season on record - elimination, as a bare surname's namesakes are - it
    asks between the two who have one; with no games table there is no
    latest season, and the list is dropped."""
    from association.query.entities import read_near_spelling, suggest_players

    con.execute("INSERT INTO players VALUES ('51', 'Dell Curry'), ('52', 'Eddy Curry'), ('53', 'JamesOn Curry'), ('54', 'Michael Curry')")
    assert suggest_players(names_of(con), "crry") == []  # six near, no latest season to narrow by
    con.execute("CREATE TABLE games (event_id VARCHAR, season INTEGER, season_type INTEGER, winner_team_id VARCHAR)")
    con.execute("INSERT INTO games VALUES ('g1', 2026, 2, '9')")
    con.execute("CREATE TABLE player_box_stats (event_id VARCHAR, season INTEGER, athlete_id VARCHAR)")
    con.execute("INSERT INTO player_box_stats VALUES ('g1', 2026, '1'), ('g1', 2026, '2'), ('g1', 2025, '51')")
    # The players active now are read once a question, with the names
    # (names.ActivePlayers): the next question - the next block - sees the
    # box scores added.
    with name_index.loaded():
        assert [e.name for e in suggest_players(names_of(con), "crry")] == ["Seth Curry", "Stephen Curry"]
        assert read_near_spelling(names_of(con), "crry") is None  # two survivors: asked, not defaulted
    con.execute("DELETE FROM player_box_stats WHERE athlete_id = '2'")
    with name_index.loaded():
        assert (read_near_spelling(names_of(con), "crry") or Entity("", "")).name == "Stephen Curry"  # one survivor: the default, said


def test_a_like_wildcard_in_a_name_is_a_literal_character(con: duckdb.DuckDBPyConnection) -> None:
    """ISSUES.md #315: a name holding "_" or "%" matches exactly that
    character, not every one-letter run or every name - and nobody is named
    with one."""
    from association.query.entities import find_teams

    assert find_players(players_of(con), "_") == []
    assert find_players(players_of(con), "%") == []
    assert find_players(players_of(con), "Cur%") == []
    assert find_teams(teams_of(con), "%") == []
    assert find_teams(teams_of(con), "_") == []
    assert [c.name for c in find_players(players_of(con), "Curry")] == ["Seth Curry", "Stephen Curry"]


def test_find_players_returns_all_candidates_best_first(con: duckdb.DuckDBPyConnection) -> None:
    # The chart path relies on this ordering: it narrows these candidates to
    # the ones with data for the season asked about, and falls back to the
    # first when that leaves nobody.
    assert [c.name for c in find_players(players_of(con), "Curry")] == ["Seth Curry", "Stephen Curry"]


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
    """Narrowing sees every match, not the first MAX_CANDIDATES
    alphabetically, which is not elimination: Keldon, who sorts eleventh,
    has shots as Aaron does, so "Johnson" is asked between the two rather
    than drawn as Aaron."""
    from association.query.entities import Availability
    from association.query.shotchart import resolve_chart_player

    shots = Availability("shot_chart")
    assert len(find_players(players_of(johnsons), "Johnson")) == 10
    assert len(find_players(players_of(johnsons), "Johnson", limit=None)) == 11
    assert resolve_player(johnsons, "Johnson", shots, 2026) == Ambiguous(query="Johnson", candidates=["Aaron Johnson", "Keldon Johnson"], active=2)
    assert resolve_chart_player(johnsons, "Johnson", shots, 2026) == Ambiguous(query="Johnson", candidates=["Aaron Johnson", "Keldon Johnson"], active=2)


def test_every_player_from_the_season_asked_about_is_named_however_many(johnsons: duckdb.DuckDBPyConnection) -> None:
    """The cap only ever counts away players who could not be the answer. Seven
    Johnsons took shots this season, more than the five a clarification names
    by default, and cutting any of them would hide a real candidate. 2026
    has 14 Williamses; this names all of them."""
    from association.query.compose.say import clarification
    from association.query.entities import Availability
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
    # applies - otherwise this would read out every Johnson who ever took a
    # shot.
    unscoped = resolve_chart_player(johnsons, "Johnson", shots)
    assert isinstance(unscoped, Ambiguous) and unscoped.active == 0


def test_narrowing_against_several_tables_keeps_a_row_in_any_of_them(johnsons: duckdb.DuckDBPyConnection) -> None:
    from association.query.entities import Availability, narrow_to_available

    johnsons.execute("CREATE TABLE player_game_log (athlete_id VARCHAR, season INTEGER)")
    johnsons.execute("INSERT INTO player_game_log VALUES ('3', 2026)")
    everyone = find_players(players_of(johnsons), "Johnson", limit=None)
    kept = narrow_to_available(johnsons, everyone, (Availability("shot_chart"), Availability("player_game_log")), 2026)
    assert [c.name for c in kept] == ["Aaron Johnson", "Dakari Johnson", "Keldon Johnson"]


def test_a_span_keeps_anybody_with_a_row_up_to_its_last_season(johnsons: duckdb.DuckDBPyConnection) -> None:
    from association.query.entities import Availability, narrow_to_available

    everyone = find_players(players_of(johnsons), "Johnson", limit=None)
    assert [c.name for c in narrow_to_available(johnsons, everyone, Availability("shot_chart"), through=2012)] == ["Frank Johnson"]


def test_a_capped_clarification_counts_what_it_leaves_out_in_english() -> None:
    """The count of the names left out is said in English: "1 other also
    matches", "2 others also match"."""
    from association.query.compose.say import clarification

    six = ["Dell Curry", "Eddy Curry", "JamesOn Curry", "Michael Curry", "Seth Curry", "Stephen Curry"]
    assert clarification("Curry", six).endswith("or Seth Curry (1 other also matches)?")
    assert clarification("Curry", [*six, "Wardell Curry"]).endswith("(2 others also match)?")


def test_a_name_that_starts_a_word_beats_one_it_only_lands_inside(con: duckdb.DuckDBPyConnection) -> None:
    """ "Ball" is LaMelo Ball, not ambiguous with Cedric Ceballos, inside
    whose name it only lands: a match at the start of a word outranks one
    inside it ("Bey" is not Mike Tobey, "Ford" not Al Horford)."""
    assert [c.name for c in find_players(players_of(con), "Ball")] == ["LaMelo Ball"]
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
    assert [c.name for c in find_players(players_of(con), "Rui")] == ["Rui Hachimura"]
    assert resolve_player(con, "Rui") == Entity(id="2", name="Rui Hachimura")
    # Naming Betancourt in full still reaches him - the curated table only
    # intercepts the bare shorthand, never a name spelled out.
    assert [c.name for c in find_players(players_of(con), "Rui Betancourt")] == ["Rui Betancourt"]


def test_both_halves_of_a_hyphenated_name_start_a_word(con: duckdb.DuckDBPyConnection) -> None:
    """The boundary is any non-letter rather than a space, so "Alexander"
    reaches Nickeil Alexander-Walker and Shai Gilgeous-Alexander, ranked
    beside Kyle Alexander rather than below him."""
    assert {c.name for c in find_players(players_of(con), "Alexander")} == {"Kyle Alexander", "Nickeil Alexander-Walker"}
    assert isinstance(resolve_player(con, "Alexander"), Ambiguous)


def test_every_declared_availability_names_a_real_table() -> None:
    """The table name is interpolated into SQL, so a typo is a runtime error on
    a path only an ambiguous name reaches. Checked against the query package's
    own list of tables rather than a warehouse, so it stays offline."""
    import importlib
    import pkgutil

    from association.nba.coverage import KNOWN_TABLES
    from association.query import compose, entities, fingerprint, player_relation, season_line, shotchart, team_relation
    from association.query.entities import Availability

    # Every one each module declares, tuples included, rather than a list here
    # that a new reader's narrowing table would have to remember to join.
    # compose is a package: every submodule of it, so a new reader's
    # narrowing table is covered without being added here.
    modules = [
        shotchart,
        fingerprint,
        season_line,
        entities,
        player_relation,
        team_relation,
        compose,
        *(importlib.import_module(m.name) for m in pkgutil.iter_modules(compose.__path__, compose.__name__ + ".")),
    ]
    declared = [v for module in modules for v in vars(module).values()]
    flat = [a for v in declared for a in (v if isinstance(v, tuple) else (v,)) if isinstance(a, Availability)]
    assert {a.table for a in flat} >= {"shot_chart", "net_points_player_fingerprint", "player_season_stats_deduped", "player_game_log"}
    for available in flat:
        assert available.table in KNOWN_TABLES, available


def test_an_interior_match_still_counts_when_nothing_starts_a_word(con: duckdb.DuckDBPyConnection) -> None:
    """Ranking, not filtering. A fragment that matches nobody at a word
    boundary still finds the people it does match, so a partial name typed
    mid-word is answered rather than reported missing."""
    assert [c.name for c in find_players(players_of(con), "urry")] == ["Seth Curry", "Stephen Curry"]


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


def test_a_teams_single_near_spelling_is_read_and_said(league_teams: duckdb.DuckDBPyConnection) -> None:
    """ISSUES.md #247: a word near exactly one team's word, and the word of
    none, is that team ("sours" the Spurs, "lakrs" the Lakers); a word near
    two, a short word and a team's own word read as nothing here. The
    relation's resolution reads it and the answer says so, as a player's
    near spelling is said (``note_team_typo_reading``)."""
    from association.query.entities import collect_name_readings, near_team, resolved_team, teams_of

    teams = teams_of(league_teams)
    assert near_team(teams, "sours") == Entity(id="24", name="San Antonio Spurs")
    assert near_team(teams, "lakrs") == Entity(id="13", name="Los Angeles Lakers")
    assert near_team(teams, "knics") == Entity(id="18", name="New York Knicks")
    assert near_team(teams, "lakers") is None  # the word outright is no near spelling
    assert near_team(teams, "sas") is None  # three letters are never near anything
    assert near_team(teams, "stats") is None  # "state" is no word a team is near by
    with collect_name_readings() as said:
        assert resolved_team(league_teams, "sours") == Entity(id="24", name="San Antonio Spurs")
    assert said == ["('sours' matches no team exactly and was read as the San Antonio Spurs, the only near spelling on record - spell the name exactly to ask about another team.)"]


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

    What is rejected is deriving the table from prominence; a hand-picked
    entry is a different thing.
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


# ---------------- the question's own span, not the model's spelling ----------------
#
# The subject reading's "any one word is enough" support check passes every
# name below - a truncated one because the word it kept is right there, a
# fabricated one because whichever half is real is right there too - so
# none is ever replaced; `_question_derived_player` is what respells them
# from the question's own span, reached here the way the agent reaches it:
# through subject.read_subject/apply_subject. The fixture below gives each
# a surname shared by several first names (or the reverse), so a wrong
# repair would resolve to the wrong real person, not just fail loudly.


@pytest.fixture
def span_con() -> duckdb.DuckDBPyConnection:
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    c.execute("CREATE TABLE teams (team_id VARCHAR, abbreviation VARCHAR, display_name VARCHAR)")
    c.execute(
        "INSERT INTO players VALUES "
        # "dennis schröder" typed correctly, the model's name just 'Dennis' -
        # grounded, and seven Dennises make it a clarification that need not ask.
        "('1','Dennis Schroder'),('2','Dennis Scott'),('3','Dennis Rodman'),('4','RayJ Dennis'),"
        "('5','Dennis Smith Jr.'),('6','Dexter Dennis'),('7','Dennis Horner'),"
        # "Aaron gordan", the model's name 'Aaron' - grounded, and many Aarons make it
        # a clarification too, when only Gordon's surname is a typo away.
        "('8','Aaron Gordon'),('9','Aaron Holiday'),('10','Aaron Nesmith'),('11','Aaron Wiggins'),"
        # "jayleyn brown", the model's name 'Brown' - grounded, and several Browns make
        # it a clarification, when only Jaylen's given name is a typo away.
        "('12','Jaylen Brown'),('13','Bruce Brown'),('14','Kobe Brown'),"
        # "tatum rec home", the model's name 'Jaylen Tatum' - grounded by 'Tatum' alone
        # - and there is exactly one.
        "('15','Jayson Tatum'),"
        # "Grady dick", the model's name 'Grady Dickinson' - grounded by 'Grady' alone
        # - and the real player is 'Gradey Dick'; 'Dickinson' is a different,
        # real player (ISSUES.md #122).
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


def _apply(con: duckdb.DuckDBPyConnection, question: str, scope: Reading, asked: PointShape | None = PLAYER_LINE) -> tuple[list[tuple[str, str]], list[str], Reading]:
    """The model's names written the way the agent writes them - the subject
    reading applied to a test's typed reading - reduced to the (was, now)
    pairs and the names dropped, with the scope the reading wrote."""
    from association.query.subject import apply_subject, read_subject

    applied = apply_subject(read_subject(names_of(con), question, asked, scope), scope, asked=asked)
    assert isinstance(applied.scope, Reading)  # the subject reading writes onto the record it is handed
    return [(str(d.before), str(d.after)) for d in applied.decisions], applied.dropped, applied.scope


class _Written(NamedTuple):
    """What :func:`_scope` wrote: one line per change, and the scope."""

    lines: list[str]
    scope: Reading


def _scope(con: duckdb.DuckDBPyConnection, question: str, scope: Reading, *, reads_player: bool, asked: PointShape | None = None) -> _Written:
    """What the agent writes of who a question is about: the subject read
    from a test's typed reading under the key the words ask (a player's log,
    or a team's record where no player is read, unless ``asked`` names
    another), then applied (`subject.apply_subject`). One line per change,
    and the scope it wrote."""
    from association.query.subject import apply_subject, read_subject

    asked = asked or (PLAYER_LOG if reads_player else TEAM_RECORD)
    subject = read_subject(names_of(con), question, asked, scope)
    applied = apply_subject(subject, scope, asked=asked)
    assert isinstance(applied.scope, Reading)  # the subject reading writes onto the record it is handed
    return _Written([d.line() for d in applied.decisions], applied.scope)


def test_a_truncated_name_is_expanded_from_the_question(span_con: duckdb.DuckDBPyConnection) -> None:
    """The model dropped the surname the question spelled correctly, and
    'Dennis' alone is grounded (the word is right there): the question's own
    span respells it, rather than asking among seven Dennises."""
    scope = Reading(who=Who(kind="player", players=("Dennis",)))
    changed, invented, scope = _apply(span_con, "how many points does dennis schroder average", scope)
    assert changed == [("Dennis", "Dennis Schroder")]
    assert invented == []
    assert scope.who.player == "Dennis Schroder"


def test_a_typo_on_the_dropped_half_of_a_name_still_resolves(span_con: duckdb.DuckDBPyConnection) -> None:
    """The model dropped the surname entirely, and the question's own
    spelling of it ('gordan') is a typo - but AND-across-tokens with the given
    name it kept narrows four Aarons to the one whose surname is a real edit
    away, the same discipline suggest_players already trusts for a
    suggestion, strong enough here to act on directly."""
    scope = Reading(who=Who(kind="player", players=("Aaron",)))
    changed, invented, scope = _apply(span_con, "aaron gordan points per game", scope)
    assert changed == [("Aaron", "Aaron Gordon")]
    assert invented == []
    assert scope.who.player == "Aaron Gordon"


def test_a_typo_on_the_kept_half_of_a_name_still_resolves(span_con: duckdb.DuckDBPyConnection) -> None:
    """The model kept only the exact surname the question used, and the
    question's given name ('jayleyn') is a typo - narrowed against three
    Browns to the one whose given name is a real edit away."""
    scope = Reading(who=Who(kind="player", players=("Brown",)))
    changed, invented, scope = _apply(span_con, "jayleyn brown last 10 games", scope)
    assert changed == [("Brown", "Jaylen Brown")]
    assert invented == []
    assert scope.who.player == "Jaylen Brown"


def test_a_fabricated_given_name_next_to_an_exact_surname_is_discarded(span_con: duckdb.DuckDBPyConnection) -> None:
    """The model invented 'Jaylen' and bolted it onto the league's only
    Tatum, grounded by 'Tatum' alone. The surname alone is an EXACT match here, not
    a guess, which is what makes discarding the invented half safe rather
    than a substitution."""
    scope = Reading(who=Who(kind="player", players=("Jaylen Tatum",)))
    changed, invented, scope = _apply(span_con, "tatum rec home", scope)
    assert changed == [("Jaylen Tatum", "Jayson Tatum")]
    assert invented == []
    assert scope.who.player == "Jayson Tatum"


def test_a_fabricated_surname_extension_is_discarded_not_the_models_wrong_guess(span_con: duckdb.DuckDBPyConnection) -> None:
    """ISSUES.md #122: the model's 'Dickinson' is a real surname, grounded by
    the question's own typo'd 'Grady', and a surname-only backoff to the
    MODEL's fabricated surname would find Hunter Dickinson, a different real
    player. This resolves from the QUESTION's own 'dick' instead, which
    Hunter Dickinson's surname does not literally contain as a whole word,
    so he is never a candidate here at all."""
    scope = Reading(who=Who(kind="player", players=("Grady Dickinson",)))
    changed, invented, scope = _apply(span_con, "Grady dick last 10 games", scope)
    assert changed == [("Grady Dickinson", "Gradey Dick")]
    assert invented == []
    assert scope.who.player == "Gradey Dick"


def test_a_span_that_already_matches_the_models_name_is_a_no_op(span_con: duckdb.DuckDBPyConnection) -> None:
    """The common case: the model already wrote the exact name the question
    spells out, so nothing should be logged as changed."""
    scope = Reading(who=Who(kind="player", players=("Jayson Tatum",)))
    changed, invented, scope = _apply(span_con, "how many points does jayson tatum average", scope)
    assert (changed, invented) == ([], [])
    assert scope.who.player == "Jayson Tatum"


def test_two_anchored_words_that_fail_together_do_not_fall_back_to_one(span_con: duckdb.DuckDBPyConnection) -> None:
    """ISSUES.md #123's shape, reached through this function instead of
    suggest_players: "Kareem Abdul-Jabbar" and "Bob Lanier" are both real
    players the model correctly named in full - neither is in `players` at
    all, because both retired before the warehouse's 1993-94 floor. "Kareem"
    alone exactly names Kareem Rush and "Lanier" alone exactly names Chaz
    Lanier, two real but wholly unrelated players - a wrong-entity answer.
    Both words of each
    name are exactly in the question, so the failure of the full span must
    be believed rather than repaired from a single leftover word."""
    scope = Reading(who=Who(kind="pair", players=("Kareem Abdul-Jabbar", "Bob Lanier")))
    changed, invented, scope = _apply(span_con, "kareem stats vs bob lanier", scope)
    assert (changed, invented) == ([], [])
    assert scope.who.players == ("Kareem Abdul-Jabbar", "Bob Lanier")


def test_a_given_name_anchor_alone_is_not_trusted_but_its_window_is(span_con: duckdb.DuckDBPyConnection) -> None:
    """The other half of the same guard: "Kareem" anchors only the FIRST
    word of a name the model invented the rest of, with nothing else in the
    question backing "Abdul-Jabbar" at all - unlike "Grady dick", where the
    window around the given-name anchor ("grady dick") is what resolves it.
    A lone given-name anchor with no corroborating window must stay unfixed,
    the same as a lone fuzzy surname."""
    scope = Reading(who=Who(kind="player", players=("Kareem Abdul-Jabbar",)))
    changed, invented, scope = _apply(span_con, "kareem stats this season", scope)
    assert (changed, invented) == ([], [])
    assert scope.who.player == "Kareem Abdul-Jabbar"


def test_a_lone_fuzzy_word_is_still_left_for_a_suggestion_not_a_silent_pick(span_con: duckdb.DuckDBPyConnection) -> None:
    """The mirror image of the cases above: only ONE word resolves, and only
    by near spelling, with nothing else in the window to corroborate it. That
    is exactly what suggest_players treats as a suggestion rather than a
    substitution, so this must stay a no-op and let the grounded/suggestion
    path run."""
    scope = Reading(who=Who(kind="player", players=("Jemel Gradee",)))
    changed, invented, scope = _apply(span_con, "how many rebounds does gradee average", scope)
    assert (changed, invented) == ([], [])
    assert scope.who.player == "Jemel Gradee"


def test_a_stray_possessive_letter_does_not_narrow_an_ambiguous_surname(span_con: duckdb.DuckDBPyConnection) -> None:
    """ "Williams's" splits into a stray one-letter "s" next to the anchor,
    which is a whole word of John S. Williams - and with a second real
    Williams in the fixture, "williams" alone is ambiguous. The stray "s"
    must not be allowed to break that tie: nobody typed it to mean anything,
    and the same trap already caught players_named_in over "Jokic's" without
    a length floor on a span's own words."""
    scope = Reading(who=Who(kind="player", players=("Jemel Williams",)))
    changed, invented, scope = _apply(span_con, "williams's rebounds this game", scope)
    assert (changed, invented) == ([], [])
    assert scope.who.player == "Jemel Williams"


def test_an_ambiguous_span_is_left_for_the_clarification_to_ask(span_con: duckdb.DuckDBPyConnection) -> None:
    """ "who is better, tatum or brown" - 'brown' alone resolves to three
    real players here, so the window must not guess between them."""
    scope = Reading(who=Who(kind="pair", players=("Jayson Tatum", "Jaylen Brown")))
    changed, invented, scope = _apply(span_con, "who is better, tatum or brown", scope)
    assert (changed, invented) == ([], [])
    assert scope.who.players == ("Jayson Tatum", "Jaylen Brown")


# ---------------- what the question itself names ----------------


def test_players_named_in_reads_names_and_nicknames_in_order(con: duckdb.DuckDBPyConnection) -> None:
    assert players_named_in(players_of(con), "compare sga and embiid") == ["Shai Gilgeous-Alexander", "Joel Embiid"]


def test_a_possessive_s_is_not_a_player(con: duckdb.DuckDBPyConnection) -> None:
    """ "Klay Thompson's" splits into a stray one-letter word, which is a whole
    word of "John S. Williams" and names nobody."""
    assert players_named_in(players_of(con), "what was klay thompson's 3pt percentage") == ["Klay Thompson"]


def test_an_ordinary_word_that_only_looks_like_a_name_is_not_one(con: duckdb.DuckDBPyConnection) -> None:
    """Substring matching would read "the highest scoring game" as naming
    Jaron Blossomgame and "with" as naming Jeff Withey, so a span has to equal
    a whole word of the name."""
    assert players_named_in(players_of(con), "what was the highest scoring game with 20 rebounds?") == []


def test_a_surname_two_players_share_names_neither_of_them(con: duckdb.DuckDBPyConnection) -> None:
    """This overrules the model's names, so it may only speak where it is certain."""
    assert players_named_in(players_of(con), "plot curry's threes") == []


# ---------------- did you mean ----------------


def test_a_fabricated_given_name_falls_back_to_the_surname(con: duckdb.DuckDBPyConnection) -> None:
    """Exact matching on one fewer token, not fuzzy: every token has to match,
    so one made-up word would bury a player the warehouse holds."""
    assert [p.name for p in suggest_players(names_of(con), "Jemel Embiid")] == ["Joel Embiid"]


def test_a_misspelling_the_model_did_not_correct_finds_the_player(con: duckdb.DuckDBPyConnection) -> None:
    """ "embid" is not a substring of "Embiid", so no amount of ILIKE reaches
    it and no amount of trusting the question helps - the typo is the
    question."""
    assert [p.name for p in suggest_players(names_of(con), "embid")] == ["Joel Embiid"]


def test_nothing_close_suggests_nobody(con: duckdb.DuckDBPyConnection) -> None:
    assert suggest_players(names_of(con), "asdf qwerty") == []
    assert suggest_players(names_of(con), "") == []


def test_an_incidental_substring_is_not_a_suggestion(con: duckdb.DuckDBPyConnection) -> None:
    """The backoff takes word-boundary matches only. "All" lands inside
    "Ceballos" and "Ball", and suggesting either is worse than saying nothing."""
    assert suggest_players(names_of(con), "Nobody At All") == []


def test_a_suggestion_too_long_to_be_one_is_dropped() -> None:
    """A name near a dozen players narrows nothing. Reading out a directory is
    not a suggestion, and the question is better off with none."""
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    con.execute("INSERT INTO players SELECT i::VARCHAR, 'Chris Smit' || chr((97 + i)::INTEGER) FROM range(8) t(i)")
    assert suggest_players(names_of(con), "Chris Smit") == []  # the surname backoff
    assert suggest_players(names_of(con), "Smitz") == []  # and the near-spelling pass


def test_a_common_surname_falls_back_to_the_near_spelling_pass() -> None:
    """ "Dylon Harper" backs off to six real Harpers - too many to suggest on
    the surname alone (the shape above) - so the stricter pass below it runs,
    which needs BOTH names close and finds the one real match: Dylan, not
    Derek, Jared, Justin or either Ron."""
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    con.execute("INSERT INTO players VALUES ('1','Derek Harper'),('2','Dylan Harper'),('3','Jared Harper'),('4','Justin Harper'),('5','Ron Harper'),('6','Ron Harper Jr.')")
    assert [p.name for p in suggest_players(names_of(con), "Dylon Harper")] == ["Dylan Harper"]


def test_a_team_name_is_not_suggested_as_a_player() -> None:
    """ "Hawks" is one edit from a real "Hawes", but "Most reb by a hawk
    player history" is about a team, which this near-spelling pass cannot
    know unless it checks: a team the text names outright is not a near miss
    on a player, whatever the edit distance says."""
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    con.execute("INSERT INTO players VALUES ('1','Spencer Hawes')")
    con.execute("CREATE TABLE teams (team_id VARCHAR, abbreviation VARCHAR, display_name VARCHAR)")
    con.execute("INSERT INTO teams VALUES ('1','ATL','Atlanta Hawks')")
    assert suggest_players(names_of(con), "Hawks") == []


EMBID_READING = "('embid' matches no player exactly and was read as Joel Embiid, the only near spelling on record - spell the name exactly to ask about someone else.)"


def test_a_single_near_spelling_is_that_player_and_the_answer_says_so(con: duckdb.DuckDBPyConnection) -> None:
    """Typos are the index's job: once names reach resolution as typed,
    asking "did you mean Joel Embiid?" of every typo turns each one into a
    clarification. One near spelling is a default, and a default is allowed
    only where it is visible and correctable - the sentence names what was
    typed, who it was read as, and how to ask about anybody else."""
    with collect_name_readings() as readings:
        assert resolve_player(con, "embid") == Entity(id="12", name="Joel Embiid")
    assert readings == [EMBID_READING]
    # The same with `available`: the default comes before any narrowing, and
    # the sentence is said once however many times the name is resolved.
    con.execute("CREATE TABLE shot_chart (athlete_id VARCHAR, season INTEGER)")
    with collect_name_readings() as readings:
        assert resolve_player(con, "embid", Availability("shot_chart"), 2026) == Entity(id="12", name="Joel Embiid")
        assert read_near_spelling(names_of(con), "embid") == Entity(id="12", name="Joel Embiid")
    assert readings == [EMBID_READING]
    # A possessive typed without its apostrophe: "embids" is two edits from
    # Embiid, one of them the "s".
    with collect_name_readings() as readings:
        assert resolve_player(con, "joel embids") == Entity(id="12", name="Joel Embiid")
    assert len(readings) == 1 and "'joel embids'" in readings[0]


def test_a_near_spelling_of_two_players_still_asks() -> None:
    """ "jolic" is one edit from Jokic and from Jovic. Choosing between them
    is a guess nothing downstream can see, so resolution stays NotFound, says
    nothing, and the caller's suggestion names both."""
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    con.execute("INSERT INTO players VALUES ('1','Nikola Jokic'),('2','Nikola Jovic')")
    con.execute("CREATE TABLE teams (team_id VARCHAR, abbreviation VARCHAR, display_name VARCHAR)")
    with collect_name_readings() as readings:
        assert resolve_player(con, "jolic") == NotFound(query="jolic")
    assert readings == []
    assert _no_match(con, "jolic") == "No player found matching 'jolic' - did you mean Nikola Jokic or Nikola Jovic?"


def test_an_exact_match_is_unchanged_and_says_nothing(con: duckdb.DuckDBPyConnection) -> None:
    with collect_name_readings() as readings:
        assert resolve_player(con, "Joel Embiid") == Entity(id="12", name="Joel Embiid")
        assert resolve_player(con, "embiid") == Entity(id="12", name="Joel Embiid")
        assert isinstance(resolve_player(con, "Curry"), Ambiguous)
    assert readings == []


def test_the_surname_backoff_still_asks(con: duckdb.DuckDBPyConnection) -> None:
    """A given name that is nobody's beside one player's surname is not a
    misspelling of him: "Larry Bird" backs off to whichever Bird the warehouse
    holds, and Larry is not one of them. Only the near-spelling pass, which
    needs every word close, defaults."""
    with collect_name_readings() as readings:
        assert resolve_player(con, "Jemel Embiid") == NotFound(query="Jemel Embiid")
    assert readings == []
    assert _no_match(con, "Jemel Embiid") == "No player found matching 'Jemel Embiid' - did you mean Joel Embiid?"


def test_a_team_name_is_not_read_as_a_near_spelling() -> None:
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    con.execute("INSERT INTO players VALUES ('1','Spencer Hawes')")
    con.execute("CREATE TABLE teams (team_id VARCHAR, abbreviation VARCHAR, display_name VARCHAR)")
    con.execute("INSERT INTO teams VALUES ('1','ATL','Atlanta Hawks')")
    with collect_name_readings() as readings:
        assert resolve_player(con, "Hawks") == NotFound(query="Hawks")
    assert readings == []


def test_a_chart_reads_a_single_near_spelling_the_same_way(con: duckdb.DuckDBPyConnection) -> None:
    from association.query.shotchart import resolve_chart_player

    con.execute("CREATE TABLE shot_chart (athlete_id VARCHAR, season INTEGER)")
    with collect_name_readings() as readings:
        assert resolve_chart_player(con, "embid", Availability("shot_chart"), 2026) == (Entity(id="12", name="Joel Embiid"), [])
        assert resolve_chart_player(con, "Jemel Embiid", Availability("shot_chart"), 2026) is None
    assert readings == [EMBID_READING]


def test_no_match_names_the_near_miss(con: duckdb.DuckDBPyConnection) -> None:
    assert _no_match(con, "Jemel Embiid") == "No player found matching 'Jemel Embiid' - did you mean Joel Embiid?"


def test_no_match_says_only_that_when_nothing_is_near(con: duckdb.DuckDBPyConnection) -> None:
    assert (
        _no_match(con, "asdf qwerty")
        == "No player found matching 'asdf qwerty'. ESPN's box scores, which the warehouse's players come from, start with 1993-94, so a player whose career ended before then may not be on record."
    )


# ---------------- players the model dropped ----------------


def test_a_vs_question_that_matched_one_player_is_flagged(con: duckdb.DuckDBPyConnection) -> None:
    """ "generate fingerprints for embiid vs jolic" draws Joel Embiid alone. The
    typo is not repaired - a near-spelling search over leftover words finds a
    spurious player in 29 of 51 corpus questions - so the answer
    has to say a player is missing rather than quietly drop one: the
    reading carries what the "vs" left out, and the sayer says it."""
    from association.query.compose.say import say_left_out
    from association.query.reading import LeftOut

    left_out = compared_but_unmatched(players_of(con), "generate fingerprints for embiid vs jolic in 2026", ["Joel Embiid"])
    assert left_out == LeftOut(held=("Joel Embiid",), names=())
    assert say_left_out(left_out) == "Note: the question compares two players, but only one of them matches anybody in the warehouse - check the spelling of the other."


def test_a_vs_question_with_both_players_is_not_flagged(con: duckdb.DuckDBPyConnection) -> None:
    assert not compared_but_unmatched(players_of(con), "fingerprints for embiid vs jokic", ["Joel Embiid", "Nikola Jokic"])


def test_a_question_comparing_nobody_is_not_flagged(con: duckdb.DuckDBPyConnection) -> None:
    """One name and no "vs" is a question about one player, which is not a
    half-answer to anything."""
    assert not compared_but_unmatched(players_of(con), "plot embiid's fingerprint", ["Joel Embiid"])


def test_a_name_that_resolves_is_never_called_a_warehouse_miss(con: duckdb.DuckDBPyConnection) -> None:
    """ISSUES.md #143's mirror image: a name that DOES resolve against the
    roster (Jaylen Brown, whom `players` holds) gets a sentence that says so
    - never "only one of them matches anybody in the warehouse - check the
    spelling of the other", which claims the warehouse does not have him. `held` is deliberately left at one name here
    (as if a reading had dropped the second) so this branch is pinned on
    its own."""
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    c.execute("INSERT INTO players VALUES ('1', 'Tyrese Maxey'), ('2', 'Marlon Maxey'), ('3', 'Jaylen Brown')")
    from association.query.compose.say import say_left_out
    from association.query.reading import LeftOut

    left_out = compared_but_unmatched(players_of(c), "show a fingerprint for maxey vs jaylen brown in 2026", ["Maxey"])
    assert left_out == LeftOut(held=("Maxey",), names=("Jaylen Brown",))
    assert say_left_out(left_out) == "Note: the question also names Jaylen Brown, who was not included in this answer."


def test_a_refusal_with_no_names_is_still_a_sentence() -> None:
    """Public and typed, so it may not depend on its caller never passing []."""
    assert misread_players([]).endswith("it was not answered.")


# ---------------- the subject reading: the team a question plays against ----------------


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


def test_an_opponent_naming_a_player_nobody_asked_about_is_left_to_be_refused(scope_con: duckdb.DuckDBPyConnection) -> None:
    """Narrow on purpose: a player read as the opponent is cleared only when
    he is already a subject. Otherwise the opponent cut stays, for the
    planner to refuse where nothing honors it, rather than silently widening
    to every opponent. (The question has to hold the name: an opponent it
    never held is the invented-name shape, dropped by
    `subject.apply_subject`.)"""
    scope = Reading(who=Who(kind="player", players=("Stephen Curry",)), cuts=Cuts(opponent="Kawhi Leonard"))
    scope = _scope(scope_con, "curry fingerprint vs kawhi", scope, reads_player=True, asked=NETPOINTS_FINGERPRINT).scope
    assert scope.cuts.opponent == "Kawhi Leonard"
    # A real team in `opponent` is untouched, whoever the subject is.
    team = Reading(who=Who(kind="player", players=("Stephen Curry",)), cuts=Cuts(opponent="Boston Celtics"))
    team = _scope(scope_con, "curry vs the celtics", team, reads_player=True).scope
    assert team.cuts.opponent == "Boston Celtics"


def test_a_name_typed_with_accents_still_names_its_player(scope_con: duckdb.DuckDBPyConnection) -> None:
    """The warehouse spells every name in plain letters; "dončić" in "luka
    dončić last 15 games vs. magic" still reaches Luka Doncic."""
    assert [p.name for p in find_players(players_of(scope_con), "dončić")] == ["Luka Doncic"]


def test_a_letter_with_no_decomposition_folds_to_its_plain_spelling() -> None:
    """ISSUES.md #244: NFKD leaves a dotless i, a stroked o, d or l, an eszett
    and the ligatures whole; each still folds to its plain spelling, so Omer
    Asik's surname typed with its dotless i is "Asik", not "Ask"."""
    from association.query.entities import _fold

    assert _fold("Ömer Aşık") == "Omer Asik"  # noqa: RUF001 - the dotless i is the letter under test
    assert _fold("Đorđević") == "Dordevic"
    assert _fold("Søren") == "Soren"
    assert _fold("Łukasz Straßer Æsir") == "Lukasz Strasser AEsir"
    assert _fold("dončić") == "doncic"


def test_a_team_beside_a_player_stays_unless_the_question_plays_against_it(scope_con: duckdb.DuckDBPyConnection) -> None:
    # His own team, which no "vs" names.
    scope = Reading(who=Who(kind="player", players=("LeBron James",), teams=("Los Angeles Lakers",)))
    notes, scope = _scope(scope_con, "lebron points for the lakers", scope, reads_player=True)
    assert notes == []
    assert (scope.who.player, scope.who.team, scope.cuts.tenure, scope.cuts.opponent) == ("LeBron James", "Los Angeles Lakers", None, None)
    # A key that reads no player (a team's record) keeps its team whatever the question says.
    kept = Reading(who=Who(kind="player", players=("Stephen Curry",), teams=("Boston Celtics",)))
    kept = _scope(scope_con, "curry vs the celtics", kept, reads_player=False).scope
    assert kept.who.team == "Boston Celtics"


def test_both_sides_of_two_teams_meeting_are_left_alone(scope_con: duckdb.DuckDBPyConnection) -> None:
    scope = Reading(who=Who(kind="teams", teams=("Los Angeles Lakers", "Boston Celtics")))
    notes, scope = _scope(scope_con, "Lakers vs Celtics record this season", scope, reads_player=False)
    assert notes == []
    assert scope.who.teams == ("Los Angeles Lakers", "Boston Celtics") and scope.cuts.opponent is None


def test_an_opponent_already_on_the_reading_is_left_alone(scope_con: duckdb.DuckDBPyConnection) -> None:
    scope = Reading(who=Who(kind="team", teams=("Philadelphia 76ers",)), cuts=Cuts(opponent="Boston Celtics"), period=Period(number=4))
    notes, scope = _scope(scope_con, "76ers 4th quarter points against boston", scope, reads_player=False)
    assert notes == []


def test_a_player_comparison_names_no_opponent(scope_con: duckdb.DuckDBPyConnection) -> None:
    scope = Reading(who=Who(kind="pair", players=("LeBron James", "Kawhi Leonard")))
    notes, scope = _scope(scope_con, "lebron vs kawhi 2015", scope, reads_player=True)
    assert notes == []
    assert scope.who.players == ("LeBron James", "Kawhi Leonard") and scope.cuts.opponent is None


def test_la_is_two_teams_and_names_no_opponent(scope_con: duckdb.DuckDBPyConnection) -> None:
    scope = Reading(who=Who(kind="player", players=("Stephen Curry",)))
    notes, scope = _scope(scope_con, "curry vs la", scope, reads_player=True)
    assert notes == []


def test_a_team_nickname_grounds_the_subjects_team(scope_con: duckdb.DuckDBPyConnection) -> None:
    """ "sixers" is no word of "Philadelphia 76ers", but it is the team."""
    scope = Reading(who=Who(kind="team", teams=("Philadelphia 76ers",)))
    notes, scope = _scope(scope_con, "Top 5 scorers on the sixers", scope, reads_player=True)
    assert notes == []
    assert scope.who.team == "Philadelphia 76ers"


def test_a_team_written_without_its_space_still_names_the_team(scope_con: duckdb.DuckDBPyConnection) -> None:
    """ "trailblazers stats last 10 games 3 point average 1st quarter" keeps
    its team: the grounding check asks whether the question holds a WORD of
    the name, and the single token matches none of {portland, trail,
    blazers}, but the team's own name with a space left out is a trace of it
    like any other."""
    scope = Reading(who=Who(kind="team", teams=("Portland Trail Blazers",)), period=Period(number=1))
    notes, scope = _scope(scope_con, "trailblazers stats last 10 games 3 point average 1st quarter", scope, reads_player=True)
    assert notes == []
    assert scope.who.team == "Portland Trail Blazers" and scope.period is not None and scope.period.number == 1


def test_a_run_together_name_reaches_the_team_it_spells(scope_con: duckdb.DuckDBPyConnection) -> None:
    """The nickname is only the visible half: six of the thirty teams have a
    two-word CITY, and "goldenstate" reaches its team too, so a question
    setting a subject against one keeps the opponent."""
    for spelling in ("trailblazers", "portlandtrailblazers", "goldenstate", "newyork", "laclippers"):
        assert isinstance(resolve_team(scope_con, spelling), Entity), spelling


def test_part_of_a_team_name_run_together_still_names_no_team(scope_con: duckdb.DuckDBPyConnection) -> None:
    """The letters have to equal a whole run of the name's own words, in its
    own order. A fragment is the substring guess `_team_named` exists to
    refuse. "la" is two teams; the rest are the name's words reordered, or one
    of them cut short."""
    for spelling in ("la", "blazerstrail", "yorknew", "trailblaze", "portlandblazers"):
        assert not isinstance(resolve_team(scope_con, spelling), Entity), spelling


def _parsed(con: duckdb.DuckDBPyConnection, question: str, names: list[str]) -> Any:
    """The Reading the parser settles for ``question``, the normalizer's
    names stubbed as ``names`` (``parse.read_route``)."""
    from association.query.parse import read_route

    return read_route(names_of(con), question, names, "")


def test_a_players_record_against_a_team_is_his_own_games(scope_con: duckdb.DuckDBPyConnection) -> None:
    """#163, #231 (F088): "Embiid career record vs boston" is not two
    franchises meeting - two teams' meetings count every meeting, the ones he
    sat out included - and not the team's with/without split, an "out" row
    nobody asked for. The parser reads a player's record against a team as
    his own games' record, narrowed to the opponent (a player's splits,
    ``PLAYER_SPLITS``), from the subject's kind."""
    scope_con.execute("INSERT INTO players VALUES ('20','Joel Embiid')")
    for question, names in (("Embiid career record vs boston", ["embiid", "boston"]), ("Show Embiid's career record against Boston", ["Embiid", "Boston"])):
        reading = _parsed(scope_con, question, names)
        assert reading.asked == PLAYER_SPLITS and reading.who.player == "Joel Embiid" and reading.cuts.opponent == "Boston Celtics", question
        assert reading.span.career and not any(c.absent for c in reading.companions) and not reading.who.team, question


def test_two_teams_meeting_and_a_players_games_against_a_team_stay_what_they_are(scope_con: duckdb.DuckDBPyConnection) -> None:
    """The other half: two teams meeting is their meetings (``TEAM_MEETINGS``), and a player
    and an opponent with no record asked for is his games - "Embiid vs boston
    last 5 games" answered with a won-lost split would be a different
    question, fluently."""
    scope_con.execute("INSERT INTO players VALUES ('20','Joel Embiid')")
    for question, names, asked in (
        ("Lakers vs Celtics record this season", ["Lakers", "Celtics"], TEAM_MEETINGS),
        ("Lakers vs Celtics this season", ["Lakers", "Celtics"], TEAM_MEETINGS),
        ("jaylen brown last 8 games vs pistons", ["jaylen brown", "pistons"], PLAYER_LOG),
        ("Embiid vs boston last 5 games", ["Embiid", "boston"], PLAYER_LOG),
    ):
        assert _parsed(scope_con, question, names).asked == asked, question


def test_a_player_left_out_is_restored_only_where_one_is_required(scope_con: duckdb.DuckDBPyConnection) -> None:
    """ "Sga record 36 plus points" with no player among the model's names.
    An optional player left empty means the league, so only a key that needs
    one (a record over a line) gets him back from the question."""
    scope = Reading(lines=(Line(measure="points", value=36, as_typed="36 points", keyed=True),), measure=Measure(key="points", as_typed="points"))
    scope = _scope(scope_con, "Sga record 36 plus points", scope, reads_player=True, asked=LINE_RECORD).scope
    assert scope.who.player == "Shai Gilgeous-Alexander"
    # A game log's player is optional - and the question has to be one the
    # words leave a game log: "Sga record 36 plus points" itself is the
    # record question, which the reading assigns (subject.KIND_ASSIGNED_ASKS).
    optional = Reading(lines=(Line(measure="points", value=36, as_typed="36 points", keyed=True),), measure=Measure(key="points", as_typed="points"))
    optional = _scope(scope_con, "Sga game log with 36 plus points", optional, reads_player=True).scope
    assert optional.who.player is None


def test_a_subject_named_with_no_verb_is_restored_for_a_single_games_high(scope_con: duckdb.DuckDBPyConnection) -> None:
    """yardstick-v2 F093: "kawhi most threes in a game" is Kawhi Leonard's
    own high, not the league's single-game leaders, though it has neither a
    scoring verb ("kawhi scored") nor a possessive ("kawhi's"): under a key
    whose subject is restorable (``reading.SUBJECT_RESTORABLE_ASKS``, a
    single game's high among them) the reading reads him back from the
    question, as it does for a key with no league reading at all."""
    scope = Reading(measure=Measure(key="threePointFieldGoalsMade", as_typed="threePointFieldGoalsMade"))
    scope = _scope(scope_con, "kawhi most threes in a game", scope, reads_player=True, asked=GAME_HIGHS).scope
    assert scope.who.player == "Kawhi Leonard"
    # Under a key outside SUBJECT_RESTORABLE_ASKS (a game log) the league
    # reading stands - for a question the words leave at that key: "kawhi
    # most threes in a game" IS the single-game high, which the reading
    # assigns under a game log (subject.KIND_ASSIGNED_ASKS).
    unrestored = Reading(measure=Measure(key="threePointFieldGoalsMade", as_typed="threePointFieldGoalsMade"))
    unrestored = _scope(scope_con, "kawhi threes game log", unrestored, reads_player=True).scope
    assert unrestored.who.player is None


@pytest.mark.parametrize(
    "question",
    [
        # yardstick-v2 F093's own false-positive check: "best" is Travis
        # Best and "head" is Luther Head, and neither question is about
        # either of them - both are genuine league/team questions
        # (lexicon._COMMON_WORDS_THAT_NAME_PLAYERS).
        "Best true shooting percentage last season?",
        "Best record from 2010-11 to 2018-19 nba",
        "Celtics vs Bulls head to head record",
    ],
)
def test_a_common_english_word_is_not_restored_as_a_player(scope_con: duckdb.DuckDBPyConnection, question: str) -> None:
    scope = Reading(measure=Measure(key="ts_pct", as_typed="ts_pct"))
    scope = _scope(scope_con, question, scope, reads_player=True, asked=GAME_HIGHS).scope
    assert scope.who.player is None


def test_a_team_named_beside_a_player_with_no_season_is_his_tenure(scope_con: duckdb.DuckDBPyConnection) -> None:
    """yardstick-v2 F166: "lebron stats as a starter for Miami" is his Heat
    years, not his current season: the reading reads "for <team>" beside an
    already-known player and, since no season is named either, defaults the
    span to his career too - a historical team names a tenure, not "now".
    Written as the tenure, not the subject's team - see
    subject._apply_own_team's own docstring for why a team the model
    supplied is not safe to trust directly."""
    scope = Reading(who=Who(kind="player", players=("LeBron James",)), measure=Measure(key="points", as_typed="points"), span=Span(season=2026), split="starter")
    notes, scope = _scope(scope_con, "lebron stats as a starter for the lakers", scope, reads_player=True, asked=PLAYER_LINE)
    assert scope.cuts.tenure == "Los Angeles Lakers"
    assert scope.span.career
    assert scope.span.season is None
    assert any("Los Angeles Lakers" in note for note in notes)
    # A season the question DOES name still wins - no career override.
    named = Reading(who=Who(kind="player", players=("LeBron James",)), measure=Measure(key="points", as_typed="points"), span=Span(season=2026), split="starter")
    named = _scope(scope_con, "lebron stats as a starter for the lakers in 2026", named, reads_player=True, asked=PLAYER_LINE).scope
    assert named.cuts.tenure == "Los Angeles Lakers"
    assert not named.span.career
    assert named.span.season == 2026
    # Off where the words ask for no single player's shape (a ranking, a
    # comparison - outside OWN_TEAM_RESTORABLE_ASKS, every single player's
    # shape, ISSUES.md #436): no tenure is written.
    unrestored = Reading(who=Who(kind="player", players=("LeBron James",)), measure=Measure(key="points", as_typed="points"), span=Span(season=2026))
    unrestored = _scope(scope_con, "lebron stats as a starter for the lakers", unrestored, reads_player=True, asked=PLAYER_RANKING).scope
    assert unrestored.cuts.tenure is None


def test_an_own_team_is_not_restored_with_no_player_and_stands_beside_an_opponent(scope_con: duckdb.DuckDBPyConnection) -> None:
    """A bare "for <team>" with no player at all is a team question, not
    this one - and beside an `opponent` the question names (a genuine "vs"
    reading) the "for" phrase is the tenure, the opponent kept: "lebron vs
    celtics stats for the lakers" is his Lakers games against Boston
    (ISSUES.md #269: "as a laker" beside an opponent is kept)."""
    no_player = Reading(measure=Measure(key="points", as_typed="points"))
    no_player = _scope(scope_con, "points scored for the lakers this season", no_player, reads_player=True, asked=PLAYER_LINE).scope
    assert no_player.cuts.tenure is None
    has_opponent = Reading(who=Who(kind="player", players=("LeBron James",)), cuts=Cuts(opponent="Boston Celtics"))
    has_opponent = _scope(scope_con, "lebron vs celtics stats for the lakers", has_opponent, reads_player=True, asked=PLAYER_LINE).scope
    assert has_opponent.cuts.tenure == "Los Angeles Lakers" and has_opponent.cuts.opponent == "Boston Celtics"


def test_a_model_supplied_team_is_not_trusted_as_a_tenure_narrowing(scope_con: duckdb.DuckDBPyConnection) -> None:
    """ "lebron james 2 3 pointers all-time vs jazz on tuesdays" carries a
    model-supplied team, the Lakers (his own, current, and redundant), beside
    a real opponent, the Jazz. A player's line does not promote that noise
    into a tenure: with the subject's team already set,
    `subject._apply_own_team` declines outright, so no tenure is written and
    the team is left exactly as it was."""
    scope = Reading(who=Who(kind="player", players=("LeBron James",), teams=("Los Angeles Lakers",)), cuts=Cuts(opponent="Utah Jazz"), span=Span(career=True))
    scope = _scope(scope_con, "lebron james 2 3 pointers all-time vs jazz on tuesdays", scope, reads_player=True, asked=PLAYER_LINE).scope
    assert scope.cuts.tenure is None
    assert scope.who.team == "Los Angeles Lakers"


def test_a_team_only_question_naming_one_player_is_read_back(scope_con: duckdb.DuckDBPyConnection) -> None:
    """yardstick-v2 F111: "alperen şengün alltime record" asks a ranking of
    teams, which reads no player, with no team named: the player the
    question names is read back, and the planner refuses by his name rather
    than answering the league's standings. Diacritics are folded
    (``entities._fold``)."""
    named = player_named_on_a_team_only_question(players_of(scope_con), teams_of(scope_con), "alperen şengün alltime record", Who(), None)
    assert named == "Alperen Sengun"
    from association.query.compose.plan import refusal_result
    from association.query.reading import Cause

    said = refusal_result(Cause(kind="no_player_reading", facts={"player": named, "shape": TEAM_RANKING}))
    assert "Alperen Sengun" in said.text and "a ranking of teams has no reading for one" in said.text
    assert said.data == {"named_player": "Alperen Sengun", "message": said.text}


def test_a_team_named_wins_over_a_coincidental_player_word(scope_con: duckdb.DuckDBPyConnection) -> None:
    """A team (or two) already on the subject, and resolving to a REAL
    franchise, means the question is genuinely about that team - a player
    named beside it, by a possessive or a nickname, changes no answer, the
    same reasoning that leaves a stray name alone on two teams' meetings
    elsewhere in this module."""
    with_team = Who(kind="team", teams=("Boston Celtics",))
    assert player_named_on_a_team_only_question(players_of(scope_con), teams_of(scope_con), "alperen şengün celtics record", with_team, None) is None
    with_teams = Who(kind="teams", teams=("Boston Celtics", "Orlando Magic"))
    assert player_named_on_a_team_only_question(players_of(scope_con), teams_of(scope_con), "alperen şengün celtics vs magic", with_teams, None) is None


def test_an_invented_team_holding_the_players_own_name_does_not_block_the_refusal(scope_con: duckdb.DuckDBPyConnection) -> None:
    """The model can file the player's own name as the team ("Alperen
    Şengün" as a franchise, AGENTS.md "A model invents names"). A team
    nothing resolves is the same as no team - otherwise the ranking of teams
    would refuse "no team matching 'Alperen Şengün'" instead, the wrong
    cause."""
    invented = Who(kind="team", teams=("Alperen Şengün",))
    assert player_named_on_a_team_only_question(players_of(scope_con), teams_of(scope_con), "alperen şengün alltime record", invented, None) == "Alperen Sengun"


def test_a_real_team_the_question_never_names_does_not_block_the_refusal(scope_con: duckdb.DuckDBPyConnection) -> None:
    """yardstick-v2 F110: "towns home rec including playoffs since 1/26/20 vs
    spurs" with the Toronto Raptors as the model's team - a real franchise
    with no word in the question - is not the Raptors' record about
    Karl-Anthony Towns. An ungrounded team is no team;
    the question names one player, so it is refused by his name. A team
    the question does name ("lakers") still wins."""
    scope_con.execute("INSERT INTO teams VALUES ('28','Toronto Raptors','TOR')")
    invented = Who(kind="team", teams=("Toronto Raptors",))
    assert player_named_on_a_team_only_question(players_of(scope_con), teams_of(scope_con), "towns home rec including playoffs since 1/26/20 vs spurs", invented, None) == "Karl-Anthony Towns"
    named = Who(kind="team", teams=("Los Angeles Lakers",))
    assert player_named_on_a_team_only_question(players_of(scope_con), teams_of(scope_con), "towns lakers home rec", named, None) is None


def test_a_common_word_is_not_read_as_the_team_only_questions_player(scope_con: duckdb.DuckDBPyConnection) -> None:
    """ "best" is Travis Best and "head" is Luther Head - the same
    false-positive trap as restoring a single game's subject (F093), applied
    here too."""
    assert player_named_on_a_team_only_question(players_of(scope_con), teams_of(scope_con), "Best record from 2010-11 to 2018-19 nba", Who(), None) is None
    assert player_named_on_a_team_only_question(players_of(scope_con), teams_of(scope_con), "Celtics vs Bulls head to head record", Who(), None) is None


def test_a_team_word_beside_a_known_player_is_not_written_as_the_subjects_team(scope_con: duckdb.DuckDBPyConnection) -> None:
    """A bare team word beside an already-known player is a different
    question - his tenure's - and a team-only key naming a player with no
    team of its own is the F111 refusal-by-name shape; the subject's team is
    not written over either one."""
    scope = Reading(who=Who(kind="player", players=("LeBron James",)), measure=Measure(key="points", as_typed="points"))
    scope = _scope(scope_con, "lebron points for the lakers this season", scope, reads_player=True, asked=PLAYER_RANKING).scope
    assert scope.who.team is None


def test_no_team_or_two_teams_named_write_no_subjects_team(scope_con: duckdb.DuckDBPyConnection) -> None:
    """Never a guess: zero teams named, or more than one, leaves the
    subject's team unwritten."""
    none_named = Reading(measure=Measure(key="points", as_typed="points"))
    none_named = _scope(scope_con, "who led the league in scoring", none_named, reads_player=False, asked=PLAYER_RANKING).scope
    assert none_named.who.team is None
    two_named = Reading(measure=Measure(key="points", as_typed="points"))
    two_named = _scope(scope_con, "76ers vs magic total points", two_named, reads_player=False, asked=PLAYER_RANKING).scope
    assert two_named.who.team is None


def test_a_teams_record_handed_no_team_is_given_none_from_the_words(scope_con: duckdb.DuckDBPyConnection) -> None:
    """Writing the subject's names onto a team's record handed no team
    leaves the team unwritten: the subject reading's own team is the
    parser's to hand on, not this step's to restore."""
    scope = Reading(measure=Measure(key="points", as_typed="points"))
    scope = _scope(scope_con, "how many 3 pointers have the magic made so far this season", scope, reads_player=False, asked=TEAM_RECORD).scope
    assert scope.who.team is None


@pytest.mark.parametrize(("nickname", "team"), [("Sixers", "Philadelphia 76ers"), ("cavs", "Cleveland Cavaliers"), ("Mavs", "Dallas Mavericks")])
def test_a_team_nickname_resolves_to_the_team(nickname: str, team: str) -> None:
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE teams (team_id VARCHAR, display_name VARCHAR, abbreviation VARCHAR)")
    c.execute("INSERT INTO teams VALUES ('20','Philadelphia 76ers','PHI'),('5','Cleveland Cavaliers','CLE'),('6','Dallas Mavericks','DAL')")
    got = resolve_team(c, nickname)
    assert isinstance(got, Entity) and got.name == team


def test_the_fragment_fallback_does_not_borrow_an_unrelated_name(scope_con: duckdb.DuckDBPyConnection) -> None:
    """A garbled team sharing no word with the one player the question
    happens to name elsewhere must not borrow that name - only a fragment
    that is actually part of the recovered name is safe to trust."""
    scope = Reading(who=Who(kind="team", teams=("Zqx",)), window=Window(order="recent"))
    notes, scope = _scope(scope_con, "Zqx last 5 games, a Luka Doncic fan favorite", scope, reads_player=True)
    assert scope.who.team == "Zqx" and scope.who.player is None
    assert notes == []


# ---------------- team names across a franchise's history ----------------


@pytest.fixture
def franchises() -> duckdb.DuckDBPyConnection:
    """ESPN's real ids and today's names, with the columns the real `teams` has.

    The ids are the point: ESPN keys a team by FRANCHISE, so id 17 is the Nets
    whether they played in New Jersey or Brooklyn, id 3 was the Charlotte
    Hornets, the New Orleans Hornets and is now the Pelicans, and id 30 was the
    Bobcats before it was today's Hornets. Lakers and Clippers are here for
    Los Angeles; Kings and Blazers for two garbled cities a model writes.
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
    return [(team.id, team.name) for team in find_teams(teams_of(con), text, season)]


def test_a_former_name_resolves_to_the_franchise(franchises: duckdb.DuckDBPyConnection) -> None:
    """ "duren v nets 1h gameloh" can carry the model's opponent "New Jersey
    Nets", which resolves to the franchise: the Nets are one franchise in
    every season."""
    assert [team_id for team_id, _ in _teams(franchises, "New Jersey Nets")] == ["17"]
    assert _teams(franchises, "Nets", 2005) == [("17", "New Jersey Nets")]
    assert _teams(franchises, "Sonics", 2005) == [("25", "Seattle SuperSonics")]


def test_a_name_that_moved_between_franchises_is_read_for_its_season(franchises: duckdb.DuckDBPyConnection) -> None:
    """ "Hornets record 2008" is New Orleans, id 3 (56-26), not the 2008
    Charlotte BOBCATS (32-50): a name is matched as each franchise carried it
    in the season asked, not only by today's names."""
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
    """The model expands a nickname into a full name and sometimes invents the
    city. "Portland Blazers" is right about the city and short a word - ESPN
    writes Portland Trail Blazers - and resolves. "Los Angeles Kings" names a
    city that belongs to two OTHER teams; picking the nickname over it would be
    a guess, so it resolves to nothing here and the question's own words settle
    it in the subject reading."""
    assert _teams(franchises, "Portland Blazers") == [("22", "Portland Trail Blazers")]
    assert _teams(franchises, "Los Angeles Kings") == []
    assert _teams(franchises, "LA") == [("12", "LA Clippers"), ("13", "Los Angeles Lakers")], "a real ambiguity stays one"


def test_a_franchise_id_is_trusted_only_where_the_warehouse_agrees() -> None:
    """The era table names ESPN's ids. A warehouse that files a different team
    under one of them is not renamed or resolved through it: a Detroit
    Pistons filed under "3" stays the Pistons, not the New Orleans
    Pelicans."""
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE teams (team_id VARCHAR, abbreviation VARCHAR, display_name VARCHAR, location VARCHAR, name VARCHAR)")
    c.execute("INSERT INTO teams VALUES ('3','DET','Detroit Pistons','Detroit','Pistons')")
    assert _teams(c, "Detroit Pistons", 2008) == [("3", "Detroit Pistons")]
    assert _teams(c, "Hornets", 2008) == []


def test_two_teams_with_no_player_stay_two_teams_meeting(franchises: duckdb.DuckDBPyConnection) -> None:
    """The case the carried-team rule exists for, and must keep: no player, so
    `teams` are the two sides and nothing is an opponent."""
    scope = Reading(who=Who(kind="teams", teams=("Sacramento Kings", "Portland Trail Blazers")))
    scope = _scope(franchises, "kings vs blazers", scope, reads_player=False).scope
    assert scope.who.teams == ("Sacramento Kings", "Portland Trail Blazers") and scope.cuts.opponent is None


def test_a_team_named_before_the_player_is_not_his_tenure(scope_con: duckdb.DuckDBPyConnection) -> None:
    """yardstick-v2 F087 "show me stats for sixers when maxey scored 20+
    points": the team is the subject and the player follows as a condition,
    so "for sixers" is not his tenure with them - the own-team reading needs
    the player named BEFORE the "for <team>" phrase, the order a tenure is
    asked in ("lebron ... for Miami")."""
    scope = Reading(who=Who(kind="player", players=("Alperen Sengun",)), measure=Measure(key="points", as_typed="points"), span=Span(season=2026))
    scope = _scope(scope_con, "show me stats for the celtics when sengun scored 20+ points", scope, reads_player=True, asked=PLAYER_LINE).scope
    assert scope.cuts.tenure is None
    tenure = Reading(who=Who(kind="player", players=("Alperen Sengun",)), measure=Measure(key="points", as_typed="points"), span=Span(season=2026))
    tenure = _scope(scope_con, "sengun stats as a starter for the celtics", tenure, reads_player=True, asked=PLAYER_LINE).scope
    assert tenure.cuts.tenure == "Boston Celtics"


def _no_match(con: duckdb.DuckDBPyConnection, text: str) -> str:
    """A name nothing matched, as the answer says it: the outcome
    :func:`~association.query.entities.unmatched` returns, worded by the
    sayer."""
    from association.query.compose.say import say
    from association.query.entities import unmatched

    return say(unmatched(names_of(con), text)).text or ""


def test_a_former_name_with_no_season_is_the_franchise_under_it() -> None:
    """ISSUES.md #420: a name no team holds today ("supersonics", "bullets")
    read with no season is that franchise under the name, never today's
    team; a name a team holds today ("hornets") is none."""
    from association.query.entities import former_franchise, franchise_by_name

    assert [team.name for team in franchise_by_name("supersonics") or []] == ["Seattle SuperSonics"]
    assert [team.name for team in franchise_by_name("sonics", 2026) or []] == ["Oklahoma City Thunder"]
    era = former_franchise("bullets")
    assert era is not None and (era.first_season, era.last_season) == (1975, 1997)
    assert former_franchise("hornets") is None and former_franchise("charlotte") is None
