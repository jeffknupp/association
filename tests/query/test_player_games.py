"""The ``player_game`` relation's properties, read directly: what every
box-score template inherits by reading through it."""

from pathlib import Path

import duckdb
import pytest

from association.nba.season import current_season
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
