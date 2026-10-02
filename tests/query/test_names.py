"""The in-memory name index (query/names.py) answers what the SQL it replaced
answered: DuckDB's own lowering, tokenizing, distance and ILIKE, the tables'
order, and the errors a partial warehouse raised - and a question reads each
table once."""

from __future__ import annotations

from typing import Any

import duckdb
import pytest

from association.query import entities, names
from association.query.entities import Entity, find_players, find_teams, players_named_in


class Counting:
    """A connection that counts the statements run through it."""

    def __init__(self, con: duckdb.DuckDBPyConnection) -> None:
        self.con = con
        self.statements: list[str] = []

    def execute(self, sql: str, *args: Any) -> Any:
        self.statements.append(sql)
        return self.con.execute(sql, *args)


@pytest.fixture
def con() -> duckdb.DuckDBPyConnection:
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    # Deliberately not in name order: a LIMIT without ORDER BY returned rows
    # in the order the table holds them.
    c.execute("INSERT INTO players VALUES ('3','Seth Curry'),('1','Stephen Curry'),('2','Dell Curry'),('9','Chris Smith'),('8','Chris Smith'),('4','Nikola Jokic')")
    c.execute("CREATE TABLE teams (team_id VARCHAR, abbreviation VARCHAR, display_name VARCHAR, location VARCHAR, name VARCHAR)")
    c.execute(
        "INSERT INTO teams VALUES ('13','LAL','Los Angeles Lakers','Los Angeles','Lakers'),"
        "('22','POR','Portland Trail Blazers','Portland','Trail Blazers'),('11','IND','Indiana Pacers','Indiana','Pacers')"
    )
    return c


# Names and texts no warehouse holds today, chosen for where Python and DuckDB
# could part: accents, a dotted capital I, a Kelvin sign, a long s, a final
# sigma, separators at either end, digits and punctuation.
TRICKY = [
    "Luka Don\N{LATIN SMALL LETTER C WITH CARON}i\N{LATIN SMALL LETTER C WITH ACUTE}",
    "\N{LATIN CAPITAL LETTER I WITH DOT ABOVE}sa Kelvin",
    "\N{KELVIN SIGN}evin Smith",
    "\N{LATIN SMALL LETTER LONG S}am Smith",
    "\N{GREEK CAPITAL LETTER SIGMA}\N{GREEK CAPITAL LETTER ALPHA}\N{GREEK CAPITAL LETTER SIGMA} \N{GREEK CAPITAL LETTER SIGMA}mith",
    " Lead",
    "Trail ",
    "--",
    "",
    "O'Neal",
    "Gilgeous-Alexander",
    "Jr. 3rd",
    "\N{LATIN CAPITAL LETTER D WITH SMALL LETTER Z WITH CARON}oe \N{LATIN SMALL LETTER DZ WITH CARON}oe",
    "\N{LATIN CAPITAL LETTER A WITH RING ABOVE}ngstr\N{LATIN SMALL LETTER O WITH DIAERESIS}m",
]


def test_lowering_is_duckdbs_over_every_code_point() -> None:
    every = "".join(chr(code) for code in range(1, 0x110000) if not 0xD800 <= code <= 0xDFFF)
    assert names.sql_lower(every) == duckdb.connect().execute("SELECT lower(?)", [every]).fetchone()[0]  # type: ignore[index]
    # Two places Python's own lower() differs on every interpreter, both kept
    # as DuckDB's; the table is DuckDB's own, so a letter only the
    # interpreter's Unicode version cases (U+1C89, new in Unicode 16) is too.
    assert names.sql_lower("\N{LATIN CAPITAL LETTER I WITH DOT ABOVE}") == "i"
    assert (
        names.sql_lower("\N{GREEK CAPITAL LETTER SIGMA}\N{GREEK CAPITAL LETTER ALPHA}\N{GREEK CAPITAL LETTER SIGMA}")
        == "\N{GREEK SMALL LETTER SIGMA}\N{GREEK SMALL LETTER ALPHA}\N{GREEK SMALL LETTER SIGMA}"
    )


def test_words_split_as_the_sql_split_them() -> None:
    row = (
        duckdb.connect()
        .execute(
            "SELECT list_transform(?::VARCHAR[], n -> regexp_split_to_array(lower(n), '[^a-z]+')), "
            "list_transform(?::VARCHAR[], n -> list_transform(regexp_split_to_array(n, '[^A-Za-z]+'), w -> lower(w)))",
            [TRICKY, TRICKY],
        )
        .fetchone()
    )
    assert row is not None
    assert [names.sql_words(n) for n in TRICKY] == row[0]
    assert [names.spelled_words(n) for n in TRICKY] == row[1]
    assert names.sql_words("Luka Don\N{LATIN SMALL LETTER C WITH CARON}i\N{LATIN SMALL LETTER C WITH ACUTE}") == ["luka", "don", "i", ""]


def test_distance_is_duckdbs_damerau_levenshtein_over_bytes() -> None:
    pairs = [
        ("CA", "ABC"),
        ("embid", "embiid"),
        ("jokci", "jokic"),
        ("\N{LATIN SMALL LETTER C WITH CARON}", "c"),
        ("don\N{LATIN SMALL LETTER C WITH CARON}i\N{LATIN SMALL LETTER C WITH ACUTE}", "doncic"),
        ("", "abc"),
        ("Abc", "abc"),
    ]
    duck = duckdb.connect().execute("SELECT list_transform(?::VARCHAR[][], p -> damerau_levenshtein(p[1], p[2]))", [[list(p) for p in pairs]]).fetchone()
    assert duck is not None
    assert [names.distance(a, b) for a, b in pairs] == duck[0]
    assert names.distance("CA", "ABC") == 2  # a transposition and an insert, not three edits
    assert names.distance("\N{LATIN SMALL LETTER C WITH CARON}", "c") == 2


def test_a_word_starts_where_re2_says_it_does() -> None:
    """``regexp_matches(..., 'i')`` folds the Kelvin sign onto "k" and the long
    s onto "s", and no other code point onto an ASCII letter."""
    folded = duckdb.connect().execute("SELECT list(c) FROM range(128, 1114112) t(c) WHERE (c < 55296 OR c > 57343) AND regexp_matches(chr(c::INTEGER), '^[a-z]$', 'i')").fetchone()
    assert folded is not None
    assert sorted(folded[0]) == [0x017F, 0x212A]
    cases = [
        (n, t)
        for n in [*TRICKY, "xKelvin", "a\N{LATIN CAPITAL LETTER I WITH DOT ABOVE}b", "Shai Gilgeous-Alexander", "Cedric Ceballos"]
        for t in ("k", "kevin", "s", "sam", "smith", "b", "ball", "alexander", "lead", "neal")
    ]
    duck = (
        duckdb.connect()
        .execute(
            "SELECT list_transform(?::VARCHAR[][], p -> regexp_matches(p[1], '(^|[^A-Za-z])' || p[2], 'i'))",
            [[list(c) for c in cases]],
        )
        .fetchone()
    )
    assert duck is not None
    assert [entities._starts_a_word(n, t) for n, t in cases] == duck[0]


@pytest.mark.parametrize("plain", [True, False])
def test_ilike_is_duckdbs_on_either_kind_of_column(plain: bool) -> None:
    """An all-ASCII column is matched by DuckDB's ASCII-only ILIKE, so a
    pattern with any other character finds nothing in it; one accented value
    anywhere in the column changes that."""
    c = duckdb.connect()
    c.execute("CREATE TABLE t (v VARCHAR)")
    values = ["Indiana Pacers", "Kelvin", "a_b", "50%", "LA Clippers"] + ([] if plain else ["Montr\N{LATIN SMALL LETTER E WITH ACUTE}al"])
    c.execute("INSERT INTO t SELECT unnest(?)", [values])
    patterns = [
        "indiana%",
        "\N{LATIN CAPITAL LETTER I WITH DOT ABOVE}ndiana%",
        "%\N{KELVIN SIGN}elvin",
        "kelvin",
        "a\\_b",
        "a_b",
        "%0\\%",
        "50%",
        "_a%",
        "% clip%",
        "montr\N{LATIN SMALL LETTER E WITH ACUTE}%",
        "MONTR\N{LATIN CAPITAL LETTER E WITH ACUTE}%",
        "%",
    ]
    for pattern in patterns:
        expected = [row[0] for row in c.execute("SELECT v FROM t WHERE v ILIKE ?", [pattern]).fetchall()]
        assert [v for v in values if entities._ilike(v, pattern, plain)] == expected, pattern
    assert not entities._ilike(None, "%", plain)


def test_rows_come_in_table_order(con: duckdb.DuckDBPyConnection) -> None:
    assert entities._exact_name_span(con, ["curry"]) == [Entity("3", "Seth Curry"), Entity("1", "Stephen Curry")]
    assert [e.id for e in entities._exact_name_span(con, ["curry"], limit=10)] == ["3", "1", "2"]
    # Two players of one name: table order, which ORDER BY display_name left
    # to the engine.
    assert [e.id for e in find_players(con, "chris smith")] == ["9", "8"]
    assert [e.id for e in find_players(con, "curry")] == ["2", "3", "1"]


def test_a_question_reads_each_table_once_inside_the_block(con: duckdb.DuckDBPyConnection) -> None:
    counting = Counting(con)
    with names.loaded():
        assert players_named_in(counting, "how did jokic and seth curry do") == ["Nikola Jokic", "Seth Curry"]  # type: ignore[arg-type]
        assert find_teams(counting, "lakers") == [Entity("13", "Los Angeles Lakers")]  # type: ignore[arg-type]
        assert find_teams(counting, "blazers")[0].id == "22"  # type: ignore[arg-type]
        assert entities.suggest_players(counting, "jokci") == [Entity("4", "Nikola Jokic")]  # type: ignore[arg-type]
    assert counting.statements == ["SELECT athlete_id, display_name FROM players", "SELECT * FROM teams"]
    # The next block (the next question) reads again, once.
    with names.loaded():
        find_players(counting, "jokic")  # type: ignore[arg-type]
        find_players(counting, "curry")  # type: ignore[arg-type]
    assert len(counting.statements) == 3


def test_a_block_serves_only_its_own_connection(con: duckdb.DuckDBPyConnection) -> None:
    other = duckdb.connect(":memory:")
    other.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    other.execute("INSERT INTO players VALUES ('7', 'Seth Curry')")
    with names.loaded():
        assert find_players(con, "seth curry") == [Entity("3", "Seth Curry")]
        assert find_players(other, "seth curry") == [Entity("7", "Seth Curry")]


def test_a_lookup_outside_a_block_is_refused_and_a_new_block_reads_a_changed_table(con: duckdb.DuckDBPyConnection) -> None:
    # Every test runs inside a block (tests/conftest.py); step outside it.
    token = names._LOADED.set(None)
    try:
        with pytest.raises(names.NotLoaded, match="outside names"):
            find_players(con, "jokic")
    finally:
        names._LOADED.reset(token)
    # Inside one, a table is read once: a row added after is not seen ...
    assert find_players(con, "wembanyama") == []
    con.execute("INSERT INTO players VALUES ('5', 'Victor Wembanyama')")
    assert find_players(con, "wembanyama") == []
    # ... until the next block, as the next question sees a reloaded warehouse.
    with names.loaded():
        assert find_players(con, "wembanyama") == [Entity("5", "Victor Wembanyama")]


def test_a_warehouse_without_teams_raises_what_the_sql_raised() -> None:
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    c.execute("INSERT INTO players VALUES ('4', 'Nikola Jokic')")
    assert entities._team_named(c, "lakers") is None  # caught: a partial load has no team to find
    assert entities.suggest_players(c, "jokci") == [Entity("4", "Nikola Jokic")]
    with pytest.raises(duckdb.CatalogException):
        find_teams(c, "lakers")


def test_a_teams_without_its_name_columns_raises_what_the_sql_raised() -> None:
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE teams (team_id VARCHAR, abbreviation VARCHAR, display_name VARCHAR)")
    c.execute("INSERT INTO teams VALUES ('22', 'POR', 'Portland Trail Blazers')")
    # No `name` column: the nickname reading's statement failed to bind, and
    # its caller reads that as finding nothing.
    assert entities._by_nickname(c, "Portland Blazers", None) is None
    assert entities._team_named(c, "trailblazers") == Entity("22", "Portland Trail Blazers")
    bare = duckdb.connect(":memory:")
    bare.execute("CREATE TABLE teams (team_id VARCHAR, display_name VARCHAR)")
    with pytest.raises(duckdb.BinderException):
        entities._team_named(bare, "lakers")  # no `abbreviation`, and nothing caught that


def test_a_warehouse_without_players_raises_what_the_sql_raised() -> None:
    c = duckdb.connect(":memory:")
    with pytest.raises(duckdb.CatalogException):
        players_named_in(c, "how did jokic do")
    assert entities._question_derived_player(c, "how did jokic do", "Nikola Jokic") is None


def test_the_readers_own_team_lookups_come_from_the_index_too(con: duckdb.DuckDBPyConnection) -> None:
    """A team named by one whole word of its name, and a team's
    abbreviation: the three statements the parser, the subject reading and
    the team compiler still issued per word after the entity module's own
    went to the index. One read of ``teams`` inside the block serves them
    all, and each gives what its SQL gave."""
    from association.query.compose.team import team_named_in
    from association.query.entities import team_abbreviations, teams_named_by_word
    from association.query.parse import _classify_span_abbreviation
    from association.query.subject import _team_abbreviation

    assert teams_named_by_word(con, "blazers") == ["Portland Trail Blazers"] and teams_named_by_word(con, "trail") == ["Portland Trail Blazers"]
    assert teams_named_by_word(con, "blazer") == [] and teams_named_by_word(con, "los") == ["Los Angeles Lakers"]
    assert team_abbreviations(con) == {"lal": "Los Angeles Lakers", "por": "Portland Trail Blazers", "ind": "Indiana Pacers"}
    counting = Counting(con)
    with names.loaded():
        assert team_named_in(counting, "how many threes have the Blazers' guards made") == "Portland Trail Blazers"  # type: ignore[arg-type]
        assert team_named_in(counting, "who led the league in scoring") is None  # type: ignore[arg-type]
        assert _team_abbreviation(counting, "POR record 2026") == "Portland Trail Blazers" and _team_abbreviation(counting, "por record") is None  # type: ignore[arg-type]
        assert _classify_span_abbreviation(counting, "ind") and not _classify_span_abbreviation(counting, "indy")  # type: ignore[arg-type]
    assert counting.statements == ["SELECT * FROM teams"]
    # A team with no abbreviation has none, and a warehouse with no teams raises what the SQL raised.
    con.execute("INSERT INTO teams VALUES ('99', NULL, 'Seattle SuperSonics', 'Seattle', 'SuperSonics')")
    assert "none" not in team_abbreviations(con) and len(team_abbreviations(con)) == 3
    bare = duckdb.connect(":memory:")
    with pytest.raises(duckdb.CatalogException):
        teams_named_by_word(bare, "lakers")
