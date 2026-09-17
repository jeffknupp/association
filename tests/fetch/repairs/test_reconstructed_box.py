"""Tests for the box line rebuilt from play-by-play.

The shape under test is the one ESPN really serves for every Chicago and New
Orleans game from 2013 to 2018: each player listed, no minutes, every stat
zero, with the plays intact beside it.
"""

from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from association.fetch import warehouse

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


def _box(event: str, athlete: str, *, minutes: int | None, points: int = 0) -> dict[str, Any]:
    """One `player_box_stats` row. ``minutes=None`` with every stat 0 is the
    empty line ESPN serves for these games; a row with minutes is a real one."""
    row: dict[str, Any] = {
        "event_id": event,
        "season": 2015,
        "season_type": 2,
        "team_id": "1",
        "opponent_team_id": "2",
        "athlete_id": athlete,
        "minutes": minutes,
        "starter": False,
        "did_not_play": False,
        "dnp_reason": "COACH'S DECISION",
        "ejected": False,
        "plusMinus": None if minutes is None else 4,
    }
    row.update(dict.fromkeys(_STATS, 0))
    row["points"] = points
    return row


def _stored_plus_minus(row: dict[str, Any], value: int) -> dict[str, Any]:
    """An empty line that carries ESPN's placeholder plus-minus. Measured over
    the warehouse's 21,169 substituted rows it is always exactly 0 - never
    NULL - which is what makes it look like surviving data."""
    return {**row, "plusMinus": value}


# event e1 is the empty game: three players listed, none with minutes.
# event e2 is a normal game, and must stay out of the rebuild entirely.
# "real_zero" genuinely scored nothing in e2 and must keep his 0.
_BOX_ROWS = [
    # The empty lines carry ESPN's placeholder plusMinus=0, exactly as the real
    # ones do. A None here would let the nulling be removed with no test failing.
    _stored_plus_minus(_box("e1", "a", minutes=None), 0),
    _stored_plus_minus(_box("e1", "b", minutes=None), 0),
    _stored_plus_minus(_box("e1", "bench", minutes=None), 0),
    _box("e2", "a", minutes=30, points=22),
    _box("e2", "real_zero", minutes=12, points=0),
]


def _play(pid: str, event: str, athlete: str | None, type_: str, text: str, scoring: bool, participants: str | None = None) -> dict[str, Any]:
    return {
        "event_id": event,
        "play_id": pid,
        "season": 2015,
        "season_type": 2,
        "athlete_id": athlete,
        "participant_athlete_ids": participants,
        "type": type_,
        "text": text,
        "scoring_play": scoring,
    }


# Player "a" scores a three (assisted by "b"), makes one free throw, takes a
# defensive rebound, loses the ball to a steal by "b", and commits a foul.
# "bench" appears in no play at all.
_PLAY_ROWS = [
    _play("1", "e1", "a", "Jump Shot", "a makes 25-foot three point jumper (b assists)", True, "a,b"),
    _play("2", "e1", "a", "Free Throw - 1 of 2", "a makes free throw 1 of 2", True, "a"),
    _play("3", "e1", "a", "Free Throw - 2 of 2", "a misses free throw 2 of 2", False, "a"),
    _play("4", "e1", "a", "Defensive Rebound", "a defensive rebound", False, "a"),
    _play("5", "e1", "a", "Bad Pass Turnover", "a bad pass (b steals)", False, "a,b"),
    _play("6", "e1", "a", "Shooting Foul", "a shooting foul (b draws the foul)", False, "a,b"),
    # A normal game's plays must not reach the view.
    _play("7", "e2", "a", "Dunk Shot", "a makes dunk", True, "a"),
]


def _build(tmp_path: Path, *, with_plays: bool = True) -> duckdb.DuckDBPyConnection:
    data_dir = tmp_path / "parquet"
    box = data_dir / "player_box_stats" / "season=2015" / "season_type=2"
    box.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist(_BOX_ROWS), box / "f.parquet")
    if with_plays:
        plays = data_dir / "plays" / "season=2015" / "season_type=2"
        plays.mkdir(parents=True)
        pq.write_table(pa.Table.from_pylist(_PLAY_ROWS), plays / "f.parquet")
    db_path = tmp_path / "test.duckdb"
    warehouse.build(data_dir, db_path)
    return duckdb.connect(str(db_path))


def _row(con: duckdb.DuckDBPyConnection, athlete: str) -> dict[str, Any] | None:
    cur = con.execute("SELECT * FROM player_box_stats_reconstructed WHERE athlete_id = ?", [athlete])
    names = [d[0] for d in cur.description]
    got = cur.fetchone()
    return dict(zip(names, got, strict=True)) if got else None


def test_the_line_is_rebuilt_from_the_plays(tmp_path: Path) -> None:
    con = _build(tmp_path)
    a = _row(con, "a")
    con.close()
    assert a is not None
    assert a["points"] == 4  # a three and one made free throw
    assert (a["field_goals_made"], a["field_goals_attempted"]) == (1, 1)
    assert (a["three_point_field_goals_made"], a["three_point_field_goals_attempted"]) == (1, 1)
    assert (a["free_throws_made"], a["free_throws_attempted"]) == (1, 2)
    assert (a["defensive_rebounds"], a["rebounds"]) == (1, 1)
    assert a["turnovers"] == 1
    assert a["fouls"] == 1


def test_assists_steals_and_blocks_go_to_the_second_participant(tmp_path: Path) -> None:
    """On "a makes three (b assists)" the play's own athlete is the SHOOTER.
    Crediting `athlete_id` would give the assist to the man who scored."""
    con = _build(tmp_path)
    b = _row(con, "b")
    a = _row(con, "a")
    con.close()
    assert b is not None and a is not None
    assert (b["assists"], b["steals"]) == (1, 1)
    assert (a["assists"], a["steals"]) == (0, 0)
    # The foul's second participant DREW it, so it is not his.
    assert b["fouls"] == 0


def test_a_player_in_no_play_is_absent_rather_than_zero(tmp_path: Path) -> None:
    """Minutes are gone, so "did not play" and "played and did nothing" cannot
    be told apart. A zero for both would recreate the bug this view exists for:
    a zero that reads as a real performance."""
    con = _build(tmp_path)
    bench = _row(con, "bench")
    con.close()
    assert bench is None


def test_a_game_with_a_real_box_score_is_left_out(tmp_path: Path) -> None:
    """The view covers only the games whose box score is empty. e2 has minutes,
    so it is ESPN's to report and not this module's."""
    con = _build(tmp_path)
    events = {r[0] for r in con.execute("SELECT DISTINCT event_id FROM player_box_stats_reconstructed").fetchall()}
    con.close()
    assert events == {"e1"}


def test_identity_comes_from_the_stored_row(tmp_path: Path) -> None:
    """Only the stats were zeroed: team, opponent and season_type survived, so
    they are read from the box row rather than guessed from the plays."""
    con = _build(tmp_path)
    a = _row(con, "a")
    con.close()
    assert a is not None
    assert (a["team_id"], a["opponent_team_id"], a["season"], a["season_type"]) == ("1", "2", 2015, 2)


def test_the_view_is_skipped_without_plays(tmp_path: Path) -> None:
    """A partial `data load` must not fail the whole build over this view."""
    con = _build(tmp_path, with_plays=False)
    tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
    con.close()
    assert "player_box_stats_reconstructed" not in tables
    assert "player_box_stats_filled" not in tables
    assert "player_box_stats" in tables


# ---------------- player_box_stats_filled ----------------


def _filled(con: duckdb.DuckDBPyConnection, event: str, athlete: str) -> dict[str, Any]:
    cur = con.execute("SELECT * FROM player_box_stats_filled WHERE event_id = ? AND athlete_id = ?", [event, athlete])
    names = [d[0] for d in cur.description]
    got = cur.fetchone()
    assert got is not None, f"no filled row for {athlete!r} in {event!r}"
    return dict(zip(names, got, strict=True))


def _scalar(con: duckdb.DuckDBPyConnection, sql: str) -> Any:
    """One value from a one-row query. ``fetchone()`` is typed as optional, so
    the assertion is what lets a count be read without a type error."""
    got = con.execute(sql).fetchone()
    assert got is not None
    return got[0]


def test_an_empty_line_takes_its_figures_from_the_rebuild(tmp_path: Path) -> None:
    con = _build(tmp_path)
    a = _filled(con, "e1", "a")
    con.close()
    assert a["reconstructed"] is True
    assert a["points"] == 4  # the stored row says 0
    assert a["freeThrowsAttempted"] == 2
    assert a["defensiveRebounds"] == 1


def test_a_real_line_is_left_exactly_as_stored(tmp_path: Path) -> None:
    con = _build(tmp_path)
    a = _filled(con, "e2", "a")
    con.close()
    assert a["reconstructed"] is False
    assert a["points"] == 22
    assert a["minutes"] == 30


def test_a_genuine_zero_in_a_real_game_is_not_substituted(tmp_path: Path) -> None:
    """The distinction the whole view turns on. A player who really scored
    nothing keeps his 0; only a line with NO box score is filled in."""
    con = _build(tmp_path)
    z = _filled(con, "e2", "real_zero")
    con.close()
    assert z["reconstructed"] is False
    assert z["points"] == 0
    assert z["minutes"] == 12


def test_every_stored_row_survives_exactly_once(tmp_path: Path) -> None:
    """The view substitutes; it must never add or drop a row. `bench` appears
    in no play, so he has no rebuilt figures - he must still be here, unflagged,
    rather than vanishing with the rebuild's INNER join."""
    con = _build(tmp_path)
    stored = _scalar(con, "SELECT count(*) FROM player_box_stats")
    filled = _scalar(con, "SELECT count(*) FROM player_box_stats_filled")
    bench = _filled(con, "e1", "bench")
    con.close()
    assert filled == stored == 5
    assert bench["reconstructed"] is False
    assert bench["points"] == 0


def test_minutes_are_never_invented(tmp_path: Path) -> None:
    """Play-by-play cannot recover minutes, so a substituted row keeps its NULL
    rather than being given a plausible number."""
    con = _build(tmp_path)
    rows = _scalar(con, "SELECT count(*) FROM player_box_stats_filled WHERE reconstructed AND minutes IS NOT NULL")
    con.close()
    assert rows == 0


def test_the_placeholder_plus_minus_is_dropped_not_passed_through(tmp_path: Path) -> None:
    """`plusMinus` survives on an empty line where every stat beside it is
    NULL, which makes it look like real data. Measured over the warehouse's
    21,169 substituted rows it takes exactly one value, 0, and every team-game
    sums to 0.0 - a placeholder, not a plus-minus. The fixture's empty rows
    carry that same 0, and it must not reach a reader as one."""
    con = _build(tmp_path)
    a = _filled(con, "e1", "a")
    real = _filled(con, "e2", "a")
    con.close()
    assert a["plusMinus"] is None
    # A real row keeps the value it was stored with.
    assert real["plusMinus"] == 4
