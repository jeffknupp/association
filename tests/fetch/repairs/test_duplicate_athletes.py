"""Tests for merging a person ESPN lists twice in one team's box score.

Every shape here is one measured in the live warehouse (see the module
docstring and ``DATA.md``, "ESPN files one player under two athlete ids in the
same box score"): two real, identical lines; one real line beside a fabricated
all-zero blank; and two blanks, one flagged ``did_not_play`` and one not. A
fifth shape - two real, DIFFERING lines - was never observed, and is tested
here as the refusal case: nothing this module has seen justifies summing or
picking a winner, so it leaves the pair alone.
"""

from __future__ import annotations

from typing import Any

import duckdb

from association.fetch.repairs import duplicate_athletes

_STATS = (
    "points",
    "fieldGoalsMade",
    "fieldGoalsAttempted",
    "threePointFieldGoalsMade",
    "threePointFieldGoalsAttempted",
    "freeThrowsMade",
    "freeThrowsAttempted",
    "rebounds",
    "offensiveRebounds",
    "defensiveRebounds",
    "assists",
    "steals",
    "blocks",
    "turnovers",
    "fouls",
)


def _real(event: str, athlete: str, team: str = "1", *, points: int = 10, minutes: int = 20) -> dict[str, Any]:
    """A row with real minutes and stats - what a player who actually appeared records."""
    row: dict[str, Any] = {
        "event_id": event,
        "season": 2019,
        "season_type": 2,
        "team_id": team,
        "opponent_team_id": "9",
        "athlete_id": athlete,
        "minutes": minutes,
        "starter": False,
        "did_not_play": False,
        "dnp_reason": None,
        "ejected": False,
        "plusMinus": 3,
    }
    row.update(dict.fromkeys(_STATS, 1))
    row["points"] = points
    return row


def _ghost(event: str, athlete: str, team: str = "1") -> dict[str, Any]:
    """The fabricated blank ESPN's duplicate id carries: no minutes, every stat
    0, but NOT flagged did_not_play - measured to never once carry real minutes
    anywhere in its history."""
    row: dict[str, Any] = {
        "event_id": event,
        "season": 2019,
        "season_type": 2,
        "team_id": team,
        "opponent_team_id": "9",
        "athlete_id": athlete,
        "minutes": None,
        "starter": False,
        "did_not_play": False,
        "dnp_reason": None,
        "ejected": False,
        "plusMinus": 0,
    }
    row.update(dict.fromkeys(_STATS, 0))
    return row


def _blank_dnp(event: str, athlete: str, team: str = "1") -> dict[str, Any]:
    """A genuine scratch: did_not_play True, every stat NULL."""
    row: dict[str, Any] = {
        "event_id": event,
        "season": 2019,
        "season_type": 2,
        "team_id": team,
        "opponent_team_id": "9",
        "athlete_id": athlete,
        "minutes": None,
        "starter": False,
        "did_not_play": True,
        "dnp_reason": "COACH'S DECISION",
        "ejected": False,
        "plusMinus": None,
    }
    row.update(dict.fromkeys(_STATS, None))
    return row


# Every player_box_stats column's real type, so a fixture row need not guess.
_COLUMN_TYPES: dict[str, str] = {
    "event_id": "VARCHAR",
    "season": "BIGINT",
    "season_type": "BIGINT",
    "team_id": "VARCHAR",
    "opponent_team_id": "VARCHAR",
    "athlete_id": "VARCHAR",
    "starter": "BOOLEAN",
    "did_not_play": "BOOLEAN",
    "dnp_reason": "VARCHAR",
    "ejected": "BOOLEAN",
    "minutes": "BIGINT",
    "plusMinus": "BIGINT",
    **dict.fromkeys(_STATS, "BIGINT"),
}


def _rows_to_table(con: duckdb.DuckDBPyConnection, table: str, rows: list[dict[str, Any]]) -> None:
    columns = list(rows[0].keys())
    col_defs = ", ".join(f'"{c}" {_COLUMN_TYPES[c]}' for c in columns)
    con.execute(f"CREATE TABLE {table} ({col_defs})")
    placeholders = ", ".join("?" for _ in columns)
    con.executemany(f"INSERT INTO {table} VALUES ({placeholders})", [[row[c] for c in columns] for row in rows])


def _build(box_rows: list[dict[str, Any]], player_rows: list[tuple[str, str]]) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    _rows_to_table(con, "player_box_stats", box_rows)
    con.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    con.executemany("INSERT INTO players VALUES (?, ?)", player_rows)
    duplicate_athletes.build_table(con, {"player_box_stats", "players"})
    return con


def _table_rows(con: duckdb.DuckDBPyConnection) -> list[tuple[Any, ...]]:
    return con.execute(f"SELECT event_id, athlete_id, minutes, did_not_play FROM {duplicate_athletes.TABLE} ORDER BY event_id, athlete_id").fetchall()


def test_two_identical_real_lines_merge_to_one_row() -> None:
    """18 of Isaiah Canaan's 20 shared 2019 games are exactly this shape."""
    con = _build(
        [
            _real("g1", "established", points=10),
            _real("g1", "shortlived", points=10),
            # Give "established" a longer real-minutes history so it wins the rank.
            _real("g2", "established", points=20),
        ],
        [("established", "Canaan"), ("shortlived", "Canaan")],
    )
    rows = _table_rows(con)
    assert rows == [("g1", "established", 20, False), ("g2", "established", 20, False)]


def test_a_real_line_beside_a_fabricated_blank_keeps_the_real_one() -> None:
    """21 of the 69 known team-games are this shape: one side real, the other
    an all-zero, not-flagged-DNP blank with no minutes."""
    con = _build(
        [
            _real("g1", "established", minutes=25),
            _ghost("g1", "shortlived"),
            _real("g2", "established", minutes=30),  # more history for "established"
        ],
        [("established", "Brewer"), ("shortlived", "Brewer")],
    )
    rows = _table_rows(con)
    assert rows == [("g1", "established", 25, False), ("g2", "established", 30, False)]


def test_two_blanks_keep_the_explicit_did_not_play_over_the_fabricated_zero() -> None:
    """17 of Ken Johnson's 33 shared games are this shape: neither side has
    minutes, but only one is a real "did not play" - the other is the
    fabricated zero with did_not_play left False. The blank_dnp row belongs to
    the MINORITY id here on purpose (the reverse of every real case), so the
    assertion actually exercises "prefer did_not_play" rather than being
    satisfiable by "prefer the canonical id's own row" alone - the two
    criteria agree in every real occurrence of this shape, which would let a
    swapped tiebreak order pass unnoticed."""
    con = _build(
        [
            _ghost("g1", "established"),
            _blank_dnp("g1", "shortlived"),
            _real("g2", "established"),  # gives "established" real-minutes history, still canonical
        ],
        [("established", "Johnson"), ("shortlived", "Johnson")],
    )
    rows = _table_rows(con)
    kept = next(r for r in rows if r[0] == "g1")
    assert kept == ("g1", "established", None, True)


def test_more_career_minutes_wins_even_with_fewer_total_rows() -> None:
    """Ken Johnson's fabricated id has ONE MORE total row than his real one -
    row count alone would pick the wrong side. Games actually carrying minutes
    is what has to decide it."""
    con = _build(
        [
            _real("shared", "fewer_rows_but_real", minutes=10),
            _ghost("shared", "more_rows_but_ghost"),
            # "more_rows_but_ghost" has an extra row nowhere near "fewer_rows_but_real" -
            # more total rows, but still never a real one.
            _ghost("solo", "more_rows_but_ghost"),
        ],
        [("fewer_rows_but_real", "Johnson"), ("more_rows_but_ghost", "Johnson")],
    )
    canonical_ids = {r[1] for r in _table_rows(con)}
    assert canonical_ids == {"fewer_rows_but_real"}


def test_a_shared_name_that_never_shares_a_game_is_left_alone() -> None:
    """Two different real people can share a display name (ISSUES.md #21) -
    that is not this module's fault to fix, and merging on the name alone
    would be exactly the mistake #21 already made once. Never co-occurring in
    the same team's box score for the same game is what keeps them apart."""
    con = _build(
        [
            _real("g1", "player_a", team="1"),
            _real("g2", "player_b", team="1"),
        ],
        [("player_a", "Selden"), ("player_b", "Selden")],
    )
    ids = {r[1] for r in _table_rows(con)}
    assert ids == {"player_a", "player_b"}


def test_three_ids_in_one_team_game_is_refused_not_merged() -> None:
    """Never observed (every known group has exactly 2 distinct ids - see
    the module docstring), and deliberately not handled: the co-occurrence
    signal that proves two ids are one person says nothing about three, and
    picking two of the three to merge would be a guess. The requirement is
    `= 2`, not `>= 2`, precisely to refuse this rather than merge a pair of
    the three arbitrarily."""
    con = _build(
        [
            _real("g1", "id_a", team="1"),
            _real("g1", "id_b", team="1"),
            _real("g1", "id_c", team="1"),
        ],
        [("id_a", "Triplicate"), ("id_b", "Triplicate"), ("id_c", "Triplicate")],
    )
    ids = {r[1] for r in _table_rows(con)}
    assert ids == {"id_a", "id_b", "id_c"}


def test_a_pair_with_two_real_differing_lines_is_refused() -> None:
    """Never observed in the warehouse, and deliberately not handled: this
    would mean summing or picking a winner and losing real data, neither of
    which this module does. Left as two ids rather than guessed at."""
    con = _build(
        [
            _real("g1", "id_a", points=10, minutes=20),
            _real("g1", "id_b", points=25, minutes=30),
            _real("g2", "id_a", points=12, minutes=22),
        ],
        [("id_a", "Mystery"), ("id_b", "Mystery")],
    )
    ids = {r[1] for r in _table_rows(con)}
    assert ids == {"id_a", "id_b"}


def test_the_columns_are_exactly_the_box_sources() -> None:
    """A drop-in for player_box_stats: same columns, same order, or swapping
    the name silently changes an answer's shape."""
    c = _build([_real("g1", "a"), _real("g1", "b")], [("a", "X"), ("b", "X")])
    result_cols = [r[0] for r in c.execute(f"DESCRIBE {duplicate_athletes.TABLE}").fetchall()]
    source_cols = [r[0] for r in c.execute("DESCRIBE player_box_stats").fetchall()]
    assert result_cols == source_cols


def test_prefers_player_box_stats_filled_when_it_is_loaded() -> None:
    """reconstructed_box builds a richer box score - read it when it exists,
    same as player_game_log does."""
    con = duckdb.connect(":memory:")
    _rows_to_table(con, "player_box_stats", [_real("g1", "a", points=1)])
    con.execute("CREATE TABLE player_box_stats_filled AS SELECT * REPLACE (99 AS points) FROM player_box_stats")
    con.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    con.execute("INSERT INTO players VALUES ('a', 'X')")
    duplicate_athletes.build_table(con, {"player_box_stats", "player_box_stats_filled", "players"})
    points = con.execute(f"SELECT points FROM {duplicate_athletes.TABLE}").fetchone()
    assert points == (99,)


def test_the_table_is_skipped_when_players_is_missing() -> None:
    con = duckdb.connect(":memory:")
    _rows_to_table(con, "player_box_stats", [_real("g1", "a")])
    duplicate_athletes.build_table(con, {"player_box_stats"})
    exists = con.execute(f"SELECT count(*) FROM information_schema.tables WHERE table_name = '{duplicate_athletes.TABLE}'").fetchone()
    assert exists == (0,)


def test_the_table_is_skipped_when_player_box_stats_lacks_a_column() -> None:
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE player_box_stats (event_id VARCHAR, season BIGINT, athlete_id VARCHAR)")
    con.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    duplicate_athletes.build_table(con, {"player_box_stats", "players"})
    exists = con.execute(f"SELECT count(*) FROM information_schema.tables WHERE table_name = '{duplicate_athletes.TABLE}'").fetchone()
    assert exists == (0,)
