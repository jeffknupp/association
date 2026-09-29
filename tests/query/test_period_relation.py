"""The period relation (ROADMAP plan item 4): a quarter or a half as a narrowing
of the player-games relation, its line rebuilt from the shots and the plays.

The rules each have a measured reason (``player_games.PERIOD_AGREEMENT`` and
the comment above ``period_line_sql``); each test here pins one of them with a
row in the shape of the real play that taught it.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import duckdb
import pytest

from association.query.player_games import PERIOD_AGREEMENT, PERIOD_COLUMNS, Narrowed, aggregate_sql, period_line_sql, rows_sql
from association.query.reading import Scope
from association.query.templates.common import RELATION_SCOPING, RELATION_SCOPING_EXCLUDED, period_narrowing

SEASON = 2026


@pytest.fixture
def con() -> Iterator[duckdb.DuckDBPyConnection]:
    """One player (athlete 1) and his teammate (2) in two games. Every play
    below is in the shape ESPN serves it, including the ones a naive rule
    miscounts."""
    c = duckdb.connect(":memory:")
    c.execute(
        "CREATE TABLE games (event_id VARCHAR, season BIGINT, season_type BIGINT, date VARCHAR, home_team_id VARCHAR, away_team_id VARCHAR, "
        "home_score BIGINT, away_score BIGINT, winner_team_id VARCHAR)"
    )
    cols = ", ".join(f"{col} BIGINT" for col in PERIOD_COLUMNS)
    c.execute(
        "CREATE TABLE player_game_log (event_id VARCHAR, season BIGINT, season_type BIGINT, team_id VARCHAR, opponent_team_id VARCHAR, athlete_id VARCHAR, "
        f"player_name VARCHAR, did_not_play BOOLEAN, minutes BIGINT, plusMinus BIGINT, {cols})"
    )
    c.execute(
        "CREATE TABLE shot_chart (event_id VARCHAR, season BIGINT, season_type BIGINT, athlete_id VARCHAR, team_id VARCHAR, period BIGINT, made BOOLEAN, "
        "shot_type VARCHAR, points_attempted BIGINT, coordinate_x BIGINT, coordinate_y BIGINT, description VARCHAR)"
    )
    c.execute("CREATE TABLE plays (event_id VARCHAR, season BIGINT, season_type BIGINT, period BIGINT, athlete_id VARCHAR, participant_athlete_ids VARCHAR, type VARCHAR, text VARCHAR)")
    for event in ("g1", "g2"):
        c.execute("INSERT INTO games VALUES (?, ?, 2, ?, '9', '13', 100, 90, '9')", [event, SEASON, f"2025-11-0{event[-1]}T00:30Z"])
        # The box line is the WHOLE game's: 40 points, +12 - a period read must
        # never show these under a quarter's heading.
        values = ", ".join("40" if col == "points" else "9" for col in PERIOD_COLUMNS)
        c.execute(f"INSERT INTO player_game_log VALUES (?, ?, 2, '9', '13', '1', 'Player One', FALSE, 36, 12, {values})", [event, SEASON])
    shots: list[tuple[Any, ...]] = [
        # (event, athlete, period, made, type, points_attempted)
        ("g1", "1", 1, True, "Jump Shot", 3),
        ("g1", "1", 1, False, "Jump Shot", 2),
        ("g1", "1", 1, True, "Free Throw - 1 of 2", 0),
        ("g1", "1", 1, False, "Free Throw - 2 of 2", 0),
        # A missed end-of-quarter heave: not a field goal attempt (2026 types
        # them; counting them put attempts right 95.8% of the time, not 99.6%).
        ("g1", "1", 1, False, "Heave Jump Shot", 3),
        ("g1", "1", 2, True, "Driving Layup Shot", 2),
        ("g1", "1", 5, True, "Jump Shot", 3),
        ("g2", "1", 3, True, "Jump Shot", 2),
    ]
    for event, athlete, period, made, kind, value in shots:
        c.execute("INSERT INTO shot_chart VALUES (?, ?, 2, ?, '9', ?, ?, ?, ?, 25, 10, 'shot')", [event, SEASON, athlete, period, made, kind, value])
    plays: list[tuple[Any, ...]] = [
        # (event, period, athlete, participants, type, text)
        ("g1", 1, "1", "1", "Offensive Rebound", "Player One offensive rebound"),
        ("g1", 1, "1", "1", "Defensive Rebound", "Player One defensive rebound"),
        # Traveling is a turnover whose type does not say so.
        ("g1", 1, "1", "1", "Traveling", "Player One traveling"),
        ("g1", 1, "1", "1", "Bad Pass\nTurnover", "Player One bad pass (Player Two steals)"),
        # An offensive foul is two plays: the foul, and its turnover half, which
        # is a turnover and NOT a second foul.
        ("g1", 1, "1", "1,5", "Offensive Foul", "Player One offensive foul (X draws the foul)"),
        ("g1", 1, "1", "1", "Offensive Foul Turnover", "Player One turnover"),
        # Fouls whose type never says "foul".
        ("g1", 2, "1", "1,5", "Shooting Block", "Player One foul (X draws the foul)"),
        ("g1", 2, "1", "1,5", "Offensive Charge", "Player One offensive charge (X draws the foul)"),
        # Not fouls: a technical, and a reviewed call overturned.
        ("g1", 2, "1", "1", "Technical Foul", "Player One technical foul"),
        ("g1", 2, "1", "1", "No Foul", "Player One foul"),
        # The SECOND participant is credited with the assist, steal and block;
        # the first (the shooter, the ball-loser) is not.
        ("g1", 1, "2", "2,1", "Jump Shot", "Player Two makes jumper (Player One assists)"),
        ("g1", 1, "2", "2,1", "Lost Ball Turnover", "Player Two lost ball (Player One steals)"),
        ("g1", 1, "2", "2,1", "Layup Shot", "Player One blocks Player Two 's layup"),
        # A foul's second id is the man who drew it, and is never credited.
        ("g1", 1, "5", "5,1", "Personal Foul", "X personal foul (Player One draws the foul)"),
        ("g2", 3, "1", "1", "Defensive Rebound", "Player One defensive rebound"),
    ]
    for event, period, athlete, parts, kind, text in plays:
        c.execute("INSERT INTO plays VALUES (?, ?, 2, ?, ?, ?, ?, ?)", [event, SEASON, period, athlete, parts, kind, text])
    yield c
    c.close()


def _line(con: duckdb.DuckDBPyConnection, periods: tuple[int, ...] | None, event: str = "g1", *, plays: bool = True) -> dict[str, Any]:
    sql = period_line_sql(periods, "SELECT DISTINCT event_id, season FROM games", plays=plays)
    res = con.execute(f"SELECT * FROM ({sql}) WHERE athlete_id = '1' AND event_id = ?", [event])
    names = [d[0] for d in res.description]
    row = res.fetchone()
    assert row is not None
    return dict(zip(names, row, strict=True))


def test_a_quarters_line_counts_each_play_by_its_measured_rule(con: duckdb.DuckDBPyConnection) -> None:
    line = _line(con, (1,))
    assert line["points"] == 4, "a three and a made free throw"
    assert (line["fieldGoalsMade"], line["fieldGoalsAttempted"]) == (1, 2), "the missed heave is no attempt"
    assert (line["threePointFieldGoalsMade"], line["threePointFieldGoalsAttempted"]) == (1, 1)
    assert (line["freeThrowsMade"], line["freeThrowsAttempted"]) == (1, 2)
    assert (line["offensiveRebounds"], line["defensiveRebounds"], line["rebounds"]) == (1, 1, 2)
    assert line["turnovers"] == 3, "traveling, the bad pass, and the offensive foul's turnover half"
    assert line["fouls"] == 1, "the offensive foul once - its turnover half is not a second foul, the foul he drew is not his"
    assert (line["assists"], line["steals"], line["blocks"]) == (1, 1, 1), "credited to the second participant"


def test_a_foul_the_type_does_not_name_is_counted_and_a_technical_is_not(con: duckdb.DuckDBPyConnection) -> None:
    assert _line(con, (2,))["fouls"] == 2, "a shooting block and a charge; not the technical, not the overturned call"


def test_a_half_is_its_two_quarters_and_every_period_includes_overtime(con: duckdb.DuckDBPyConnection) -> None:
    assert _line(con, (1, 2))["points"] == 6
    assert _line(con, None)["points"] == 9, "overtime's three counts toward the whole game"


def test_without_plays_the_plays_columns_are_unknown_not_zero(con: duckdb.DuckDBPyConnection) -> None:
    line = _line(con, (1,), plays=False)
    assert line["points"] == 4
    assert line["rebounds"] is None and line["assists"] is None


def test_periods_are_validated_before_they_reach_sql() -> None:
    with pytest.raises(ValueError, match="1-10"):
        period_line_sql((0,), "SELECT 1")
    with pytest.raises(ValueError, match="1-10"):
        Narrowed(base=[], base_params=[]).narrow_periods((11,), "nothing")


def _narrowed(con: duckdb.DuckDBPyConnection, periods: tuple[int, ...], label: str) -> Narrowed:
    narrowed = Narrowed(base=["pgl.athlete_id = ?", "pgl.season = ?"], base_params=["1", SEASON])
    log_columns = frozenset(row[0] for row in con.execute("DESCRIBE player_game_log").fetchall())
    narrowed.narrow_periods(periods, label, log_columns=log_columns)
    return narrowed


def test_a_period_narrowed_read_sees_the_periods_line_and_never_the_games(con: duckdb.DuckDBPyConnection) -> None:
    """The reader's SQL is unchanged - it selects ``pgl.points`` - and gets the
    quarter's figure; plus-minus, which no play can split, is blank rather
    than the whole game's +12."""
    narrowed = _narrowed(con, (1,), "1st quarter")
    sql, params = rows_sql(narrowed, "pgl.event_id, pgl.points, pgl.rebounds, pgl.plusMinus", order="g.date")
    assert con.execute(sql, params).fetchall() == [("g1", 4, 2, None), ("g2", 0, 0, None)]
    assert narrowed.filters() == " in the 1st quarter"


def test_a_line_on_a_stat_reads_the_period(con: duckdb.DuckDBPyConnection) -> None:
    """ "First quarters with 3+ points" is the games whose FIRST QUARTER held
    three - never the whole game's 40, which every game here clears."""
    narrowed = _narrowed(con, (1,), "1st quarter")
    narrowed.narrow_measure("points", ">=", 3)
    sql, params = aggregate_sql(narrowed, ["COUNT(*)", "SUM(pgl.points)"])
    assert con.execute(sql, params).fetchone() == (1, 4)


def test_a_game_the_shot_table_does_not_cover_is_no_game(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("INSERT INTO games VALUES ('g3', ?, 2, '2025-11-03T00:30Z', '9', '13', 100, 90, '9')", [SEASON])
    values = ", ".join("9" for _ in PERIOD_COLUMNS)
    con.execute(f"INSERT INTO player_game_log VALUES ('g3', ?, 2, '9', '13', '1', 'Player One', FALSE, 36, 12, {values})", [SEASON])
    sql, params = aggregate_sql(_narrowed(con, (1,), "1st quarter"), ["COUNT(*)"])
    assert con.execute(sql, params).fetchone() == (2,), "a confident zero for g3 would drag every average down"


def test_the_scope_names_a_half_before_a_quarter() -> None:
    assert period_narrowing(Scope(half=2)) == ((3, 4), "2nd half")
    assert period_narrowing(Scope(period=5)) == ((5,), "overtime")
    assert period_narrowing(Scope(period=1, half=1)) == ((1, 2), "1st half")
    assert period_narrowing(Scope()) is None


def test_period_is_a_relation_cell_and_every_exclusion_says_why() -> None:
    assert {"period", "half"} <= RELATION_SCOPING
    for intent, cells in RELATION_SCOPING_EXCLUDED.items():
        for cell in ("period", "half"):
            if cell in cells:
                assert cells[cell].strip(), f"{intent} excludes {cell} without a reason"


def test_the_agreement_table_names_only_period_columns_and_real_percentages() -> None:
    assert set(PERIOD_AGREEMENT) <= set(PERIOD_COLUMNS)
    for column, seasons in PERIOD_AGREEMENT.items():
        for season, pct in seasons.items():
            assert season >= 2002 and 0 < pct < 99.05, f"{column} {season}: {pct} (the table lists seasons under 99% only)"
