"""The ``player_game`` relation's properties, read directly: what every
box-score template inherits by reading through it."""

from pathlib import Path

import duckdb
import pytest

from association.nba.season import current_season
from association.query.calendar import AlignmentNarrowing
from association.query.player_games import Narrowed, aggregate_sql, grouped_sql, league, rows_sql


@pytest.fixture
def con() -> duckdb.DuckDBPyConnection:
    """One player across the 1993 phantom, a did-not-play entry, an empty line
    (no minutes, no rebuild) and a rebuilt line (no minutes, ``reconstructed``)."""
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE games (event_id VARCHAR, season INTEGER, season_type INTEGER, date VARCHAR, home_team_id VARCHAR, winner_team_id VARCHAR)")
    c.execute(
        "CREATE TABLE player_game_log (event_id VARCHAR, athlete_id VARCHAR, player_name VARCHAR, season INTEGER, season_type INTEGER, team_id VARCHAR, "
        "game_date VARCHAR, opponent_abbr VARCHAR, points INTEGER, minutes INTEGER, did_not_play BOOLEAN, reconstructed BOOLEAN)"
    )
    s = current_season()
    rows = [
        ("p93", 1993, "1993-11-05T00:00Z", 20, 30, False, False),  # the phantom: 1994's game under the 1993 label
        ("p94", 1994, "1994-11-05T00:00Z", 20, 30, False, False),
        ("g1", s, "2026-01-01T00:00Z", 30, 34, False, False),
        ("g2", s, "2026-01-03T00:00Z", 12, 28, False, False),
        ("dnp", s, "2026-01-05T00:00Z", 0, None, True, False),  # listed, did not play
        ("empty", s, "2026-01-07T00:00Z", 0, None, False, False),  # ESPN served no box score
        ("rebuilt", s, "2026-01-09T00:00Z", 41, None, False, True),  # rebuilt from play-by-play
    ]
    for event, season, date, points, minutes, dnp, rebuilt in rows:
        c.execute("INSERT INTO games VALUES (?, ?, 2, ?, 'T', 'T')", [event, season, date])
        c.execute("INSERT INTO player_game_log VALUES (?, '1', 'A Player', ?, 2, 'T', ?, 'OPP', ?, ?, ?, ?)", [event, season, date, points, minutes, dnp, rebuilt])
    return c


def _career() -> Narrowed:
    return Narrowed(base=["pgl.athlete_id = ?", "pgl.season_type = ?", "pgl.season >= ? AND pgl.season NOT IN (?)", "NOT pgl.did_not_play"], base_params=["1", 2, 1994, 1993])


def test_a_career_read_never_sees_the_phantom_season(con: duckdb.DuckDBPyConnection) -> None:
    sql, params = rows_sql(_career(), "pgl.event_id", order="pgl.game_date")
    events = [r[0] for r in con.execute(sql, params).fetchall()]
    assert "p94" in events
    assert "p93" not in events


def test_a_did_not_play_entry_and_an_empty_line_are_not_games_played(con: duckdb.DuckDBPyConnection) -> None:
    sql, params = rows_sql(_career(), "pgl.event_id", order="pgl.game_date")
    events = {r[0] for r in con.execute(sql, params).fetchall()}
    assert "dnp" not in events
    assert "empty" not in events
    # ...and neither is a rebuilt line, unless the reader opts in for a stat a rebuild gets right.
    assert "rebuilt" not in events
    sql, params = rows_sql(_career(), "pgl.event_id", order="pgl.game_date", rebuilt=True)
    assert "rebuilt" in {r[0] for r in con.execute(sql, params).fetchall()}


def test_the_aggregate_counts_exactly_the_rows_the_log_lists(con: duckdb.DuckDBPyConnection) -> None:
    for rebuilt in (False, True):
        listed, params = rows_sql(_career(), "pgl.event_id", order="pgl.game_date", rebuilt=rebuilt)
        counted, cparams = aggregate_sql(_career(), ["COUNT(*)"], rebuilt=rebuilt)
        row = con.execute(counted, cparams).fetchone()
        assert row is not None
        assert row[0] == len(con.execute(listed, params).fetchall())


def test_a_grouped_limit_applies_after_grouping_not_to_the_rows(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("INSERT INTO games VALUES ('h1', ?, 2, '2026-02-01T00:00Z', 'T', 'T')", [current_season()])
    con.execute("INSERT INTO player_game_log VALUES ('h1', '2', 'B Player', ?, 2, 'T', '2026-02-01T00:00Z', 'OPP', 50, 30, FALSE, FALSE)", [current_season()])
    scope = league("pgl.season = ?", [current_season()], 2)
    scope.narrow_measure("points", ">=", 10)
    sql, params = grouped_sql(scope, "pgl.athlete_id, pgl.player_name", ["pgl.player_name", "COUNT(*) AS n"], order="2 DESC, 1", limit=1)
    assert con.execute(sql, params).fetchall() == [("A Player", 2)]  # two games of 10+, ranked above B's one


def test_a_measure_comparison_is_an_allowlist(con: duckdb.DuckDBPyConnection) -> None:
    scope = league("pgl.season = ?", [current_season()], 2)
    with pytest.raises(ValueError, match="no comparison"):
        scope.narrow_measure("points", "; DROP TABLE games; --", 1)


def test_the_played_guard_is_stated_in_every_read(tmp_path: Path) -> None:
    """Perturbation in miniature: the guard is text every skeleton includes, so
    a reader cannot compose a read without it."""
    scope = league("pgl.season = ?", [2026], 2)
    for sql, _ in (rows_sql(scope, "1", order="1"), aggregate_sql(scope, ["1"]), grouped_sql(scope, "1", ["1"])):
        assert "NOT pgl.did_not_play" in sql
        assert "pgl.minutes IS NOT NULL" in sql
        assert "JOIN games g ON g.event_id = pgl.event_id AND g.season = pgl.season" in sql


def test_same_date_rows_are_broken_by_event_then_player_name(con: duckdb.DuckDBPyConnection) -> None:
    """A league-wide or position-group log lists several players' games on
    the same date, and ``rows_sql``'s ORDER BY used to be the caller's date
    alone - so two players' rows on the same date, in the same game, came
    out in DuckDB's parallel scan order, which is not stable run to run
    (ISSUES.md, "A game log's same-date rows come out in an unstable
    order"). :data:`~association.query.player_games.ROWS_TIEBREAK` breaks
    the tie after the caller's own order - event, then player name - inside
    :func:`rows_sql` itself, so every caller gets it. Inserted in reverse
    alphabetical order here (Z before A) to prove the ORDER BY is doing the
    sorting, not insertion order."""
    con.execute("INSERT INTO games VALUES ('same', ?, 2, '2026-02-02T00:00Z', 'T', 'T')", [current_season()])
    con.execute("INSERT INTO player_game_log VALUES ('same', '3', 'Zeke Player', ?, 2, 'T', '2026-02-02T00:00Z', 'OPP', 10, 20, FALSE, FALSE)", [current_season()])
    con.execute("INSERT INTO player_game_log VALUES ('same', '2', 'Amy Player', ?, 2, 'T', '2026-02-02T00:00Z', 'OPP', 12, 22, FALSE, FALSE)", [current_season()])
    scope = league("pgl.season = ? AND pgl.event_id = ?", [current_season(), "same"], 2)
    sql, params = rows_sql(scope, "pgl.player_name", order="pgl.game_date DESC")
    names = [r[0] for r in con.execute(sql, params).fetchall()]
    assert names == ["Amy Player", "Zeke Player"]
    # Run twice: the tiebreak makes a second run come back identical, which
    # is the property the ISSUES.md entry says DuckDB's scan order alone
    # does not guarantee.
    assert con.execute(sql, params).fetchall() == con.execute(sql, params).fetchall()


@pytest.fixture
def alignment_con() -> duckdb.DuckDBPyConnection:
    """One player against three opponents in two seasons that straddle the
    2004-05 realignment: Atlanta (Southeast from ``s``), Chicago (Central
    always) and a fourth opponent, Golden State (Pacific), that never
    matches. ``s - 1`` predates the Southeast division existing at all, so
    Atlanta's ``s - 1`` game is Central, not Southeast - the "alignment is
    read per season" rule this fixture exists to pin."""
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE games (event_id VARCHAR, season INTEGER, season_type INTEGER, date VARCHAR, home_team_id VARCHAR, winner_team_id VARCHAR)")
    c.execute(
        "CREATE TABLE player_game_log (event_id VARCHAR, athlete_id VARCHAR, player_name VARCHAR, season INTEGER, season_type INTEGER, team_id VARCHAR, "
        "opponent_team_id VARCHAR, game_date VARCHAR, points INTEGER, minutes INTEGER, did_not_play BOOLEAN, reconstructed BOOLEAN)"
    )
    c.execute("CREATE TABLE team_alignment (season INTEGER, team_id VARCHAR, conference VARCHAR, division VARCHAR)")
    s = current_season()
    c.executemany(
        "INSERT INTO team_alignment VALUES (?, ?, ?, ?)",
        [
            (s, "ATL", "Eastern Conference", "Southeast"),
            (s, "CHI", "Eastern Conference", "Central"),
            (s, "GS", "Western Conference", "Pacific"),
            (s - 1, "ATL", "Eastern Conference", "Central"),  # pre-realignment: no Southeast yet
            (s - 1, "CHI", "Eastern Conference", "Central"),
        ],
    )
    rows = [
        ("atl_now", s, "ATL", f"{s}-01-01T00:00Z"),
        ("chi_now", s, "CHI", f"{s}-01-02T00:00Z"),
        ("gs_now", s, "GS", f"{s}-01-03T00:00Z"),
        ("atl_prior", s - 1, "ATL", f"{s - 1}-01-01T00:00Z"),
    ]
    for event, season, opponent, date in rows:
        c.execute("INSERT INTO games VALUES (?, ?, 2, ?, 'T', 'T')", [event, season, date])
        c.execute("INSERT INTO player_game_log VALUES (?, '1', 'A Player', ?, 2, 'T', ?, ?, 20, 30, FALSE, FALSE)", [event, season, opponent, date])
    return c


def _league(season: int) -> Narrowed:
    return Narrowed(base=["pgl.season = ?", "pgl.season_type = ?", "NOT pgl.did_not_play"], base_params=[season, 2])


def test_narrow_alignment_reads_the_opponents_conference_or_division(alignment_con: duckdb.DuckDBPyConnection) -> None:
    """ "vs the east"/"vs the southeast division" narrow to the games against
    an opponent aligned that way THAT SEASON - Atlanta counts for the current
    season's Southeast but not the prior one's, since the Southeast division
    did not exist yet."""
    s = current_season()
    conference = _league(s)
    conference.narrow_alignment(AlignmentNarrowing("conference", "Eastern Conference", "against Eastern Conference teams"))
    sql, params = rows_sql(conference, "pgl.event_id", order="pgl.event_id")
    assert {r[0] for r in alignment_con.execute(sql, params).fetchall()} == {"atl_now", "chi_now"}
    assert "against Eastern Conference teams" in conference.filters()

    division = _league(s)
    division.narrow_alignment(AlignmentNarrowing("division", "Southeast", "against the Southeast Division"))
    sql, params = rows_sql(division, "pgl.event_id", order="pgl.event_id")
    assert {r[0] for r in alignment_con.execute(sql, params).fetchall()} == {"atl_now"}

    prior = _league(s - 1)
    prior.narrow_alignment(AlignmentNarrowing("division", "Southeast", "against the Southeast Division"))
    sql, params = rows_sql(prior, "pgl.event_id", order="pgl.event_id")
    assert alignment_con.execute(sql, params).fetchall() == []  # Atlanta was Central in s - 1, pre-realignment
