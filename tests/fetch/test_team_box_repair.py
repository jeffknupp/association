"""Tests for the load-time repair of the two team-box faults.

Every fixture row below is the shape ESPN really serves. The 2018 team row is
event ``400974437``'s Boston line verbatim - ``assists`` 4 beside a player-box
assist sum of 24, ``blocks`` 24 beside a block sum of 4 - and the empty row is
the all-NULL shape every Chicago and New Orleans game from 2013 to 2018 has,
beside player rows listing everyone as having played with no minutes.
"""

from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from association.fetch import warehouse

# Every column of a team row, so a fixture only has to name what it changes.
_TEAM_DEFAULTS: dict[str, Any] = {
    "event_id": "x",
    "season": 2017,
    "season_type": 2,
    "team_id": "2",
    "opponent_team_id": "13",
    "home_away": "home",
    "fieldGoalsMade": 36,
    "fieldGoalsAttempted": 88,
    "fieldGoalPct": 41,
    "threePointFieldGoalsMade": 8,
    "threePointFieldGoalsAttempted": 32,
    "threePointFieldGoalPct": 25,
    "freeThrowsMade": 19,
    "freeThrowsAttempted": 25,
    "freeThrowPct": 76,
    "totalRebounds": 50,
    "offensiveRebounds": 10,
    "defensiveRebounds": 40,
    "assists": 24,
    "steals": 11,
    "blocks": 4,
    "turnovers": 10,
    "teamTurnovers": 1,
    "totalTurnovers": 11,
    "technicalFouls": 1,
    "totalTechnicalFouls": 1,
    "flagrantFouls": 0,
    "pointsInPaint": 44,
    "fouls": 24,
    "largestLead": 12,
}

_PLAYER_DEFAULTS: dict[str, Any] = {
    "event_id": "x",
    "season": 2017,
    "season_type": 2,
    "team_id": "2",
    "opponent_team_id": "13",
    "athlete_id": "a",
    "minutes": 30,
    "assists": 0,
    "steals": 0,
    "blocks": 0,
    "turnovers": 0,
    "fouls": 0,
    "rebounds": 0,
    "offensiveRebounds": 0,
    "defensiveRebounds": 0,
}


def _team(**changed: Any) -> dict[str, Any]:
    return {**_TEAM_DEFAULTS, **changed}


def _empty_team(**changed: Any) -> dict[str, Any]:
    """The all-NULL row ESPN serves for a game it has no box score for."""
    blank = {key: (value if key in ("event_id", "season", "season_type", "team_id", "opponent_team_id", "home_away") else None) for key, value in _TEAM_DEFAULTS.items()}
    return {**blank, **changed}


def _player(**changed: Any) -> dict[str, Any]:
    return {**_PLAYER_DEFAULTS, **changed}


# --- 2018: the real shifted row, beside the player rows that hold the truth ---
# assists 4 is really its blocks, steals 10 its turnovers, blocks 24 its fouls,
# flagrantFouls 11 its steals; fieldGoalPct 76 is FT% and freeThrowPct 25 is 3P%.
_SHIFTED_2018 = _team(
    event_id="g18",
    season=2018,
    assists=4,
    steals=10,
    blocks=24,
    turnovers=2,
    teamTurnovers=3,
    totalTurnovers=5,
    fouls=0,
    flagrantFouls=11,
    technicalFouls=0,
    totalTechnicalFouls=0,
    pointsInPaint=-1,
    fieldGoalPct=76,
    freeThrowPct=25,
    threePointFieldGoalPct=25,
)
# Sums: assists 24, steals 11, blocks 4, turnovers 10, fouls 24.
_PLAYERS_2018 = [
    _player(event_id="g18", season=2018, athlete_id="a", minutes=36, assists=10, steals=5, blocks=2, turnovers=4, fouls=9),
    _player(event_id="g18", season=2018, athlete_id="b", minutes=33, assists=9, steals=4, blocks=1, turnovers=3, fouls=8),
    _player(event_id="g18", season=2018, athlete_id="c", minutes=20, assists=5, steals=2, blocks=1, turnovers=3, fouls=7),
]

# --- 2018: the Chicago/New Orleans empty game, which must stay empty ---
_EMPTY_2018 = _empty_team(event_id="g18e", season=2018, team_id="4")
_PLAYERS_2018_EMPTY = [
    _player(event_id="g18e", season=2018, team_id="4", athlete_id="d", minutes=None),
    _player(event_id="g18e", season=2018, team_id="4", athlete_id="e", minutes=None),
]

# --- 2017 control: correct row whose player sums differ on purpose, so a
# repair leaking out of 2018 shows up as 77 assists rather than as nothing.
_CONTROL_2017 = _team(event_id="g17", season=2017)
_PLAYERS_2017 = [_player(event_id="g17", season=2017, athlete_id="a", assists=77, steals=66, blocks=55, turnovers=44, fouls=33)]

# --- 2000: turnovers 0, teamTurnovers a copy of totalTurnovers ---
_TURNOVERLESS_2000 = _team(event_id="g00", season=2000, turnovers=0, teamTurnovers=16, totalTurnovers=16, assists=20)
_PLAYERS_2000 = [
    _player(event_id="g00", season=2000, athlete_id="a", assists=45, steals=5, blocks=3, turnovers=9, fouls=12),
    _player(event_id="g00", season=2000, athlete_id="b", assists=32, steals=4, blocks=2, turnovers=6, fouls=11),
]

# --- 1996: an all-NULL TEAM row whose PLAYER rows are real (Vancouver's shape).
# A different fault with a different fix; a turnover rebuild must not give this
# row a lone turnover count in an otherwise empty line.
_EMPTY_TEAM_REAL_PLAYERS_1996 = _empty_team(event_id="g96", season=1996, team_id="29")
_PLAYERS_1996 = [_player(event_id="g96", season=1996, team_id="29", athlete_id="f", minutes=31, turnovers=4, assists=6)]

# --- 2018: a STORED row beside player rows that are all zeros with no minutes.
# The empty games have both halves missing at once, so only this shape can tell
# the two gates apart - and here the player sums are zeros that would overwrite
# real numbers rather than merely fill a blank.
_ZEROED_PLAYERS_2018 = _team(event_id="g18z", season=2018, team_id="5", assists=4, steals=10, blocks=24, turnovers=2, fouls=0)
_PLAYERS_2018_ZEROED = [
    _player(event_id="g18z", season=2018, team_id="5", athlete_id="g", minutes=None),
    _player(event_id="g18z", season=2018, team_id="5", athlete_id="h", minutes=None),
]

# --- 2008: event 271107026's Cleveland line as ESPN serves it, refetched on
# 2026-09-16. offensiveRebounds 8 is the team rebounds, defensiveRebounds 15 is
# the real offensive boards, and totalRebounds 70 is 47 + 8 + 15. The player
# rows are that game's real sums: 15 offensive, 32 defensive, 47 in all.
_SWAPPED_REBOUNDS_2008 = _team(event_id="g08", season=2008, team_id="5", offensiveRebounds=8, defensiveRebounds=15, totalRebounds=70)
_PLAYERS_2008 = [
    _player(event_id="g08", season=2008, team_id="5", athlete_id="lbj", minutes=41, rebounds=15, offensiveRebounds=2, defensiveRebounds=13),
    _player(event_id="g08", season=2008, team_id="5", athlete_id="zi", minutes=31, rebounds=14, offensiveRebounds=7, defensiveRebounds=7),
    _player(event_id="g08", season=2008, team_id="5", athlete_id="rest", minutes=39, rebounds=18, offensiveRebounds=6, defensiveRebounds=12),
]
# The 2008 postseason is clean; the same stored shape there must be left alone.
_PLAYOFF_2008 = _team(event_id="g08p", season=2008, season_type=3, team_id="5", offensiveRebounds=8, defensiveRebounds=15, totalRebounds=70)
_PLAYERS_2008_PLAYOFF = [_player(event_id="g08p", season=2008, season_type=3, team_id="5", athlete_id="lbj", rebounds=47, offensiveRebounds=15, defensiveRebounds=32)]

_TEAM_ROWS = [_SHIFTED_2018, _EMPTY_2018, _ZEROED_PLAYERS_2018, _CONTROL_2017, _TURNOVERLESS_2000, _EMPTY_TEAM_REAL_PLAYERS_1996, _SWAPPED_REBOUNDS_2008, _PLAYOFF_2008]
_PLAYER_ROWS = [*_PLAYERS_2018, *_PLAYERS_2018_EMPTY, *_PLAYERS_2018_ZEROED, *_PLAYERS_2017, *_PLAYERS_2000, *_PLAYERS_1996, *_PLAYERS_2008, *_PLAYERS_2008_PLAYOFF]


def _build(tmp_path: Path, *, with_players: bool = True, builds: int = 1) -> duckdb.DuckDBPyConnection:
    data_dir = tmp_path / "parquet"
    team_dir = data_dir / "team_box_stats"
    team_dir.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist(_TEAM_ROWS), team_dir / "f.parquet")
    if with_players:
        player_dir = data_dir / "player_box_stats"
        player_dir.mkdir(parents=True)
        pq.write_table(pa.Table.from_pylist(_PLAYER_ROWS), player_dir / "f.parquet")
    db_path = tmp_path / "test.duckdb"
    for _ in range(builds):
        warehouse.build(data_dir, db_path)
    return duckdb.connect(str(db_path))


def _row(con: duckdb.DuckDBPyConnection, event_id: str) -> dict[str, Any]:
    cur = con.execute("SELECT * FROM team_box_stats WHERE event_id = ?", [event_id])
    names = [d[0] for d in cur.description]
    got = cur.fetchone()
    assert got is not None
    return dict(zip(names, got, strict=True))


def test_the_2018_shifted_columns_are_rebuilt_from_the_player_rows(tmp_path: Path) -> None:
    """ESPN's 2018 team line holds its blocks under `assists`, its turnovers
    under `steals` and its fouls under `blocks`. Each is the sum of the game's
    own player rows, which survived intact."""
    con = _build(tmp_path)
    row = _row(con, "g18")
    con.close()
    assert (row["assists"], row["steals"], row["blocks"], row["turnovers"], row["fouls"]) == (24, 11, 4, 10, 24)


def test_the_2018_percentages_are_recomputed_from_the_row_itself(tmp_path: Path) -> None:
    """`fieldGoalPct` held FT% and `freeThrowPct` held 3P%. The made and
    attempted columns beside them are right, so the two are re-derived from
    them - rounded, which is how ESPN publishes them in every other season."""
    con = _build(tmp_path)
    row = _row(con, "g18")
    con.close()
    assert row["fieldGoalPct"] == 41  # round(100 * 36 / 88)
    assert row["freeThrowPct"] == 76  # round(100 * 19 / 25)
    assert row["threePointFieldGoalPct"] == 25  # already right, left alone


def test_the_2018_columns_with_no_source_are_nulled_rather_than_left_wrong(tmp_path: Path) -> None:
    """The player box has no flagrant fouls, technicals, points in the paint or
    team turnovers, so those cannot be rebuilt. A wrong value that reads as a
    real one is worse than a NULL, and `pointsInPaint` is -1 in every 2018 row."""
    con = _build(tmp_path)
    row = _row(con, "g18")
    con.close()
    assert row["flagrantFouls"] is None
    assert row["technicalFouls"] is None
    assert row["totalTechnicalFouls"] is None
    assert row["totalTurnovers"] is None
    assert row["pointsInPaint"] is None


def test_2018_team_turnovers_are_left_alone(tmp_path: Path) -> None:
    """The one column in the shifted block nothing proves wrong: 0.596 a game
    against 0.586 in 2017 and 0.548 in 2019. Clearing it would throw away a
    number that is probably right; rebuilding it is impossible."""
    con = _build(tmp_path)
    row = _row(con, "g18")
    con.close()
    assert row["teamTurnovers"] == 3


def test_an_empty_2018_team_game_stays_empty(tmp_path: Path) -> None:
    """The trap. Every Chicago and New Orleans game from 2013 to 2018 has an
    all-NULL team row beside player rows that are all zeros with no minutes.
    Summing those gives 0, and writing it would turn "ESPN has no box score"
    into "this team recorded no assists"."""
    con = _build(tmp_path)
    row = _row(con, "g18e")
    con.close()
    for column in ("assists", "steals", "blocks", "turnovers", "fouls", "fieldGoalPct", "freeThrowPct", "teamTurnovers", "totalTurnovers", "pointsInPaint", "flagrantFouls"):
        assert row[column] is None, f"{column} was fabricated for an empty team-game"


def test_a_stored_2018_row_whose_player_rows_are_zeros_is_left_alone(tmp_path: Path) -> None:
    """The gate the empty rows cannot test on their own: there both halves are
    missing at once, so the stored-row check alone would look sufficient. A
    team row that IS stored, beside player rows with no minutes, is what
    separates them - and summing those zeros would write 0 over ESPN's numbers
    rather than merely fill a blank. Left untouched, wrong values and all:
    fully corrected or fully untouched, never half of each."""
    con = _build(tmp_path)
    row = _row(con, "g18z")
    con.close()
    assert (row["assists"], row["steals"], row["blocks"], row["turnovers"], row["fouls"]) == (4, 10, 24, 2, 0)
    assert row["pointsInPaint"] == 44  # not cleared either


def test_an_empty_team_row_with_real_player_rows_stays_empty(tmp_path: Path) -> None:
    """Vancouver 1996 and Chicago 2000: the team row is all NULL but the player
    rows are real. That is a different fault with a different fix, and filling
    only this era's turnover column would leave one number in an empty line."""
    con = _build(tmp_path)
    row = _row(con, "g96")
    con.close()
    assert row["turnovers"] is None
    assert row["assists"] is None
    assert row["teamTurnovers"] is None


def test_a_control_season_is_untouched(tmp_path: Path) -> None:
    """2017 comes from the same code with the same column order and the right
    values. Its player sums are deliberately nothing like its team row, so a
    repair reaching outside 2018 would show up as 77 assists."""
    con = _build(tmp_path)
    row = _row(con, "g17")
    con.close()
    assert (row["assists"], row["steals"], row["blocks"], row["turnovers"], row["fouls"]) == (24, 11, 4, 10, 24)
    assert (row["flagrantFouls"], row["technicalFouls"], row["pointsInPaint"], row["totalTurnovers"]) == (0, 1, 44, 11)
    assert row["teamTurnovers"] == 1


def test_the_pre_2013_turnover_columns_are_rebuilt(tmp_path: Path) -> None:
    """`turnovers` is 0 in every row up to 2012 and `teamTurnovers` holds a
    copy of `totalTurnovers` rather than the ~0.6 team turnovers a game it
    names. `totalTurnovers` itself is right in that era and is left alone."""
    con = _build(tmp_path)
    row = _row(con, "g00")
    con.close()
    assert row["turnovers"] == 15  # 9 + 6 from the player rows
    assert row["teamTurnovers"] is None
    assert row["totalTurnovers"] == 16
    # Only the turnover columns: the 2018 shift did not reach back here.
    assert row["assists"] == 20


def test_the_2008_rebound_columns_are_rebuilt(tmp_path: Path) -> None:
    """The splits come from the player rows; the total is the players'
    rebounds plus the team rebounds ESPN filed under offensiveRebounds, the
    definition every season around 2008 follows."""
    con = _build(tmp_path)
    row = _row(con, "g08")
    con.close()
    assert (row["offensiveRebounds"], row["defensiveRebounds"], row["totalRebounds"]) == (15, 32, 55)
    # Rebounds only: nothing else in a 2008 row moved.
    assert (row["assists"], row["steals"], row["fieldGoalPct"]) == (24, 11, 41)


def test_the_2008_postseason_rebounds_are_untouched(tmp_path: Path) -> None:
    con = _build(tmp_path)
    row = _row(con, "g08p")
    con.close()
    assert (row["offensiveRebounds"], row["defensiveRebounds"], row["totalRebounds"]) == (8, 15, 70)


def test_the_repair_is_idempotent(tmp_path: Path) -> None:
    """Keyed on the season and on whether a row is empty, never on whether a
    value looks wrong - so a second build, or a partial `data load`, writes the
    same numbers instead of summing the sums."""
    con = _build(tmp_path, builds=2)
    shifted, control, old, rebounds = _row(con, "g18"), _row(con, "g17"), _row(con, "g00"), _row(con, "g08")
    con.close()
    assert (rebounds["offensiveRebounds"], rebounds["defensiveRebounds"], rebounds["totalRebounds"]) == (15, 32, 55)
    assert (shifted["assists"], shifted["steals"], shifted["blocks"], shifted["fouls"]) == (24, 11, 4, 24)
    assert shifted["fieldGoalPct"] == 41
    assert control["assists"] == 24
    assert old["turnovers"] == 15


def test_the_repair_is_skipped_without_player_box_stats(tmp_path: Path) -> None:
    """A partial `data load --tables team_box_stats` must not fail the whole
    build over a table it was not asked to load."""
    con = _build(tmp_path, with_players=False)
    row = _row(con, "g18")
    tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
    con.close()
    assert tables == {"team_box_stats"}
    assert row["assists"] == 4  # left exactly as ESPN served it


def test_the_repair_does_not_change_the_column_set_or_types(tmp_path: Path) -> None:
    """SELECT * REPLACE keeps every other column and its position, so a column
    the parser gains later survives without this module knowing about it - and
    a rebuilt column must not widen from BIGINT to the HUGEINT a SUM returns."""
    con = _build(tmp_path)
    described = con.execute("DESCRIBE team_box_stats").fetchall()
    con.close()
    assert [r[0] for r in described] == list(_TEAM_DEFAULTS)
    types = dict((r[0], r[1]) for r in described)
    assert types["assists"] == "BIGINT"
    assert types["fieldGoalPct"] == "BIGINT"
