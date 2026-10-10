"""The period relation (ROADMAP plan item 4): a quarter or a half as a narrowing
of the player-games relation, its line rebuilt from the shots and the plays.

The rules each have a measured reason (``player_games.PERIOD_AGREEMENT`` and
the comment above ``period_line_sql``); each test here pins one of them with a
row in the shape of the real play that taught it.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import duckdb
import pytest
from shapes import key
from test_templates import team_quarter_points  # the compiler's since Phase 2's slice (iv) (compose.COMPILED_INTENTS)

from association.fetch.repairs import real_games
from association.query.answer import AnswerContext
from association.query.player_games import PERIOD_AGREEMENT, PERIOD_COLUMNS, Narrowed, aggregate_sql, period_line_sql, rows_sql
from association.query.player_relation import RELATION_SCOPING, RELATION_SCOPING_EXCLUDED
from association.query.reading import Period, Reading, Scope, Span, period_narrowing
from association.query.reading import Subject as Who
from association.query.team_games import TEAM_PERIOD_AGREEMENT, TEAM_PERIOD_COLUMNS, TeamNarrowed, team_period_line_sql
from association.query.team_games import aggregate_sql as team_aggregate_sql
from association.query.team_games import rows_sql as team_rows_sql
from association.query.team_relation import TEAM_RELATION_SCOPING, TEAM_RELATION_SCOPING_EXCLUDED

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
        # 2018 types 436 real turnovers "No Turnover" and 89 blocking fouls
        # "Not Available"; the text says what they are. A "no turnover" text
        # is the overturned call the type names, and is not counted.
        ("g2", 3, "1", "1", "No Turnover", "Player One turnover"),
        ("g2", 3, "1", "1", "No Turnover", "Player One  no turnover X"),
        ("g2", 3, "1", "1", "Not Available", "Player One personal blocking foul"),
        ("g2", 3, "1", "1", "Not Available", "Player One turnover"),
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


def test_a_turnover_or_foul_the_type_mislabels_is_read_from_its_text(con: duckdb.DuckDBPyConnection) -> None:
    line = _line(con, (3,), "g2")
    assert (line["turnovers"], line["fouls"]) == (2, 1), "the 'No Turnover' and the 'Not Available' that say turnover, the 'Not Available' blocking foul"


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


def test_a_period_as_a_condition_keeps_the_games_whose_period_held_the_line_and_reads_the_whole_game(con: duckdb.DuckDBPyConnection) -> None:
    """ROADMAP step 2, #275 ("three points made per game after making one
    three in first quarter"): the condition is on the period's own rebuilt
    line and the read is the WHOLE game's. g1's first quarter holds one made
    three and g2's none, so "1+ threes in the 1st quarter" keeps g1 alone -
    and its points are the box score's 40, never the quarter's 4. "Exactly
    2" keeps nothing; a line on the third quarter (g2's layup) keeps g2. A
    measured quarter beside a conditioning one reads each on its own line:
    g2's first quarter, in the games whose third held a basket, is 0."""
    narrowed = Narrowed(base=["pgl.athlete_id = ?", "pgl.season = ?"], base_params=["1", SEASON])
    narrowed.narrow_period_condition((1,), "threePointFieldGoalsMade", 1, "1+ 3-pointers in the 1st quarter")
    sql, params = aggregate_sql(narrowed, ["COUNT(*)", "SUM(pgl.points)"])
    assert con.execute(sql, params).fetchone() == (1, 40)
    assert narrowed.filters() == " in games with 1+ 3-pointers in the 1st quarter"
    exact = Narrowed(base=["pgl.athlete_id = ?", "pgl.season = ?"], base_params=["1", SEASON])
    exact.narrow_period_condition((1,), "threePointFieldGoalsMade", 2, "exactly 2 3-pointers in the 1st quarter", op="=")
    sql, params = aggregate_sql(exact, ["COUNT(*)"])
    assert con.execute(sql, params).fetchone() == (0,)
    third = _narrowed(con, (1,), "1st quarter")
    third.narrow_period_condition((3,), "fieldGoalsMade", 1, "1+ field goals in the 3rd quarter")
    sql, params = rows_sql(third, "pgl.event_id, pgl.points", order="g.date")
    assert con.execute(sql, params).fetchall() == [("g2", 0)], "g2 is the game whose third quarter held a basket; its FIRST quarter is the measured, scoreless one"
    assert third.filters() == " in the 1st quarter in games with 1+ field goals in the 3rd quarter"
    with pytest.raises(ValueError, match="no period-line column"):
        narrowed.narrow_period_condition((1,), "minutes", 1, "1+ minutes in the 1st quarter")


def test_a_period_condition_sends_a_players_line_to_the_box_scores() -> None:
    """The season line has no quarter in it: a condition on one narrows the
    GAMES, and `player_stat` reads them from box scores as it does for an
    opponent or a teammate's role (#212's shape, guarded)."""
    from association.query.reading import Line, Period, scope_reads_box_scores

    assert scope_reads_box_scores(Scope(subject=Who(kind="player", players=("x",)), lines=(Line(measure="points", value=10, period=Period(number=1)),)), [])
    assert not scope_reads_box_scores(Scope(subject=Who(kind="player", players=("x",))), [])


def test_the_scope_narrows_to_a_half_or_a_quarter_through_the_typed_period() -> None:
    assert period_narrowing(Scope(period=Period(number=2, half=True))) == ((3, 4), "2nd half")
    assert period_narrowing(Scope(period=Period(number=5))) == ((5,), "overtime")
    assert period_narrowing(Scope(period=Period(number=11))) is None, "no game has an 11th period: the period readers decline it by name"
    assert period_narrowing(Scope()) is None


def test_period_is_a_relation_cell_and_every_exclusion_says_why() -> None:
    assert Period.CELLS <= RELATION_SCOPING
    for shape, row in RELATION_SCOPING_EXCLUDED.items():
        assert "half" not in row.unstated, f"{shape}: a half is the period cell since Phase 3, step 2"
        if "period" in row.unstated:
            assert row.unstated["period"].strip(), f"{shape} excludes period without a reason"


def test_the_agreement_table_names_only_period_columns_and_real_percentages() -> None:
    assert set(PERIOD_AGREEMENT) <= set(PERIOD_COLUMNS)
    for column, seasons in PERIOD_AGREEMENT.items():
        for season, pct in seasons.items():
            assert season >= 2002 and 0 < pct < 99.05, f"{column} {season}: {pct} (the table lists seasons under 99% only)"


def test_the_compiler_refuses_a_period_read_of_a_column_no_play_splits() -> None:
    """Under a period, ``minutes`` still holds the game's figure (the played
    guard reads it) - so the compiler's default line, which carries minutes,
    is refused rather than printed under a quarter's heading, and a read of
    the rebuilt columns passes."""
    from association.query.compose.core import Query, Unsupported, _check_period_measures

    with pytest.raises(Unsupported, match="minutes"):
        _check_period_measures(Query(scope=Scope(subject=Who(kind="player", players=("x",)), period=Period(number=1))))
    with pytest.raises(Unsupported, match="plusMinus"):
        _check_period_measures(Query(scope=Scope(subject=Who(kind="player", players=("x",)), period=Period(number=2, half=True)), measures=["points"], predicates=[("plusMinus", ">=", 5)]))
    _check_period_measures(Query(scope=Scope(subject=Who(kind="player", players=("x",)), period=Period(number=1)), measures=["points", "rebounds", "fg_pct"]))
    _check_period_measures(Query(scope=Scope(subject=Who(kind="player", players=("x",))), measures=["minutes"]))  # no period: nothing to refuse
    # A read grouped by period sees each quarter's line, and is held to the same columns.
    with pytest.raises(Unsupported, match="minutes"):
        _check_period_measures(Query(scope=Scope(subject=Who(kind="player", players=("x",))), skeleton="grouped", group="period", measures=["minutes"]))
    _check_period_measures(Query(scope=Scope(subject=Who(kind="player", players=("x",))), skeleton="grouped", group="period", measures=["three_pct"]))


def test_compiling_a_period_read_of_minutes_refuses_before_the_warehouse_is_read() -> None:
    """The guard sits in ``compile_query`` itself, ahead of any read: an empty
    connection is refused for the cause, not failed on a missing table."""
    from association.query.compose.core import Query, Unsupported, compile_query

    with pytest.raises(Unsupported, match="minutes"):
        compile_query(duckdb.connect(":memory:"), Query(scope=Scope(subject=Who(kind="player", players=("x",)), period=Period(number=1), span=Span(season=SEASON, season_type=2))))


# ---------------------------------------------------------------------------
# The team half (ISSUES.md #161): a team's period line is its players' lines
# plus its own plays, and its points are the linescore's.
# ---------------------------------------------------------------------------


@pytest.fixture
def team_con(con: duckdb.DuckDBPyConnection) -> duckdb.DuckDBPyConnection:
    """The player fixture's two players on team 9, at home to team 13 in g1
    and g2, with the tables the team relation reads beside them: the
    linescores, a roster, and the team's own plays in the shapes ESPN serves
    them."""
    con.execute("CREATE TABLE teams (team_id VARCHAR, abbreviation VARCHAR, display_name VARCHAR)")
    con.execute("INSERT INTO teams VALUES ('9', 'AAA', 'Home Team'), ('13', 'BBB', 'Away Team')")
    con.execute("ALTER TABLE games ADD COLUMN home_linescores VARCHAR")
    con.execute("ALTER TABLE games ADD COLUMN away_linescores VARCHAR")
    con.execute("ALTER TABLE games ADD COLUMN neutral_site BOOLEAN")
    con.execute("ALTER TABLE games ADD COLUMN venue_city VARCHAR")
    con.execute("UPDATE games SET home_linescores = '30,20,25,25', away_linescores = '20,25,20,25', neutral_site = FALSE, venue_city = 'Home'")
    real_games.build_table(con, {"games", "teams"})
    con.execute("CREATE TABLE player_box_stats (event_id VARCHAR, season BIGINT, season_type BIGINT, team_id VARCHAR, athlete_id VARCHAR)")
    for event in ("g1", "g2"):
        con.executemany("INSERT INTO player_box_stats VALUES (?, ?, 2, '9', ?)", [(event, SEASON, "1"), (event, SEASON, "2")])
    con.execute("ALTER TABLE plays ADD COLUMN team_id VARCHAR")
    con.execute("UPDATE plays SET team_id = '9'")
    team_plays: list[tuple[Any, ...]] = [
        # (event, period, type, text) - no athlete, the team's own.
        # A shot-clock violation is charged to the team: a turnover the box
        # counts (`totalTurnovers`), in no player's line.
        ("g1", 1, "Shot Clock Turnover", "Home Team shot clock turnover"),
        # A team rebound: in the plays, never in the box's rebound split
        # (measured: counted, offensive rebounds agree in 0.1-3.4% of
        # team-games instead of 98-99%).
        ("g1", 1, "Offensive Rebound", "Home Team offensive team rebound"),
        ("g1", 3, "8-Second Turnover", "Home Team 8 second turnover"),
    ]
    for event, period, kind, text in team_plays:
        con.execute("INSERT INTO plays VALUES (?, ?, 2, ?, NULL, NULL, ?, ?, '9')", [event, SEASON, period, kind, text])
    # A shot ESPN charted with no shooter still counts for the team.
    con.execute("INSERT INTO shot_chart VALUES ('g1', ?, 2, NULL, '9', 1, TRUE, 'Jump Shot', 2, 25, 10, 'shot')", [SEASON])
    return con


def _team_line(con: duckdb.DuckDBPyConnection, periods: tuple[int, ...] | None, event: str = "g1", *, plays: bool = True) -> dict[str, Any]:
    sql = team_period_line_sql(periods, "SELECT DISTINCT event_id, season FROM games", plays=plays)
    res = con.execute(f"SELECT * FROM ({sql}) WHERE team_id = '9' AND event_id = ?", [event])
    names = [d[0] for d in res.description]
    row = res.fetchone()
    assert row is not None
    return dict(zip(names, row, strict=True))


def test_a_teams_line_is_its_players_plus_its_own_turnovers_and_shots(team_con: duckdb.DuckDBPyConnection) -> None:
    line = _team_line(team_con, (1,))
    assert line["turnovers"] == 5, "his traveling, bad pass and offensive-foul turnover, Player Two's lost ball, and the team's shot-clock violation"
    assert line["teamTurnovers"] == 1, "the team's own share, carried apart for the 2018 check"
    assert (line["offensiveRebounds"], line["defensiveRebounds"], line["rebounds"]) == (1, 1, 2), "his two - never the team rebound the box's split leaves out"
    assert (line["fieldGoalsMade"], line["fieldGoalsAttempted"]) == (2, 3), "his make and miss (the heave is no attempt), and the shooterless make"
    assert line["assists"] == 1 and line["steals"] == 1 and line["blocks"] == 1


def test_a_teams_half_is_its_two_quarters(team_con: duckdb.DuckDBPyConnection) -> None:
    first, second, half = _team_line(team_con, (1,)), _team_line(team_con, (2,)), _team_line(team_con, (1, 2))
    for column in (c for c in TEAM_PERIOD_COLUMNS if c != "points"):
        assert half[column] == first[column] + second[column], column
    assert _team_line(team_con, (3, 4))["turnovers"] == 1, "the 8-second violation, in the second half"


def test_without_plays_a_teams_plays_columns_are_unknown_not_zero(team_con: duckdb.DuckDBPyConnection) -> None:
    line = _team_line(team_con, (1,), plays=False)
    assert line["fieldGoalsMade"] == 2
    for column in ("rebounds", "offensiveRebounds", "assists", "steals", "blocks", "turnovers", "fouls"):
        assert line[column] is None, column


def _team_narrowed(periods: tuple[int, ...], label: str, **flags: bool) -> TeamNarrowed:
    narrowed = TeamNarrowed(base=["tg.team_id = ?", "tg.season_type = ?", "tg.season = ?"], base_params=["9", 2, SEASON])
    narrowed.narrow_periods(periods, label, **flags)
    return narrowed


def test_a_period_narrowed_team_read_sees_the_linescore_and_the_rebuilt_line(team_con: duckdb.DuckDBPyConnection) -> None:
    """The reader's SQL is unchanged - ``tg.team_score`` - and gets the
    period's score from ESPN's own linescore, each side's own; a rebuilt
    column beside it. ``won`` stays the game's result."""
    narrowed = _team_narrowed((1, 2), "1st half")
    sql, params = team_rows_sql(narrowed, "tg.event_id, tg.team_score, tg.opponent_score, tg.points, tg.turnovers, tg.won", order="tg.eastern_date")
    assert team_con.execute(sql, params).fetchall() == [("g1", 50, 45, 50, 5, True), ("g2", 50, 45, 50, 0, True)]
    assert narrowed.filters() == " in the 1st half" and narrowed.filters(period=False) == ""


def test_an_overtime_no_game_reached_is_null_not_zero(team_con: duckdb.DuckDBPyConnection) -> None:
    sql, params = team_aggregate_sql(_team_narrowed((5,), "overtime"), ["COUNT(*)", "COUNT(tg.points)", "SUM(tg.points)"])
    assert team_con.execute(sql, params).fetchone() == (2, 0, None)


def test_a_team_game_the_shot_table_does_not_cover_has_no_rebuilt_line(team_con: duckdb.DuckDBPyConnection) -> None:
    """The linescore still answers its points; the rebuilt columns are
    unknown - a confident zero would drag every average down."""
    team_con.execute("INSERT INTO games VALUES ('g3', ?, 2, '2025-11-05T00:30Z', '9', '13', 100, 90, '9', '25,25,25,25', '20,20,25,25', FALSE, 'Home')", [SEASON])
    team_con.execute("DROP TABLE real_games")
    real_games.build_table(team_con, {"games", "teams"})
    sql, params = team_rows_sql(_team_narrowed((1,), "1st quarter"), "tg.event_id, tg.points, tg.rebounds", order="tg.eastern_date")
    assert team_con.execute(sql, params).fetchall()[-1] == ("g3", 25, None)


def test_a_team_read_without_shots_knows_only_the_linescore(team_con: duckdb.DuckDBPyConnection) -> None:
    sql, params = team_rows_sql(_team_narrowed((1,), "1st quarter", shots=False, plays=False), "tg.points, tg.fieldGoalsMade, tg.turnovers", order="tg.eastern_date")
    assert team_con.execute(sql, params).fetchall() == [(30, None, None), (30, None, None)]


def test_period_is_a_team_relation_cell_and_every_exclusion_says_why() -> None:
    assert Period.CELLS <= TEAM_RELATION_SCOPING
    for words in ("team_record", "head_to_head", "team_leaderboard"):
        assert TEAM_RELATION_SCOPING_EXCLUDED[key(words)].unstated["period"].strip(), f"{words} excludes period without a reason"
        assert "half" not in TEAM_RELATION_SCOPING_EXCLUDED[key(words)].unstated, f"{words}: a half is the period cell since Phase 3, step 2"


def test_the_team_agreement_table_names_only_period_columns_and_real_percentages() -> None:
    assert set(TEAM_PERIOD_AGREEMENT) <= set(TEAM_PERIOD_COLUMNS)
    for column, seasons in TEAM_PERIOD_AGREEMENT.items():
        for season, pct in seasons.items():
            assert season >= 2002 and 0 <= pct < 99.05, f"{column} {season}: {pct} (the table lists seasons under 99% only)"


def test_team_quarter_points_answers_a_rebuilt_stat_with_its_seasons_caveat(team_con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    """ "home team first half turnovers": the relation's rebuilt column, per
    game and averaged, and the season's measured agreement said beside it."""
    ctx = AnswerContext(con=team_con, out_dir=tmp_path)
    result = team_quarter_points(ctx, Reading.from_slots({"team": "Home Team", "half": 1, "season": SEASON, "season_type": 2, "stat": "turnovers"}))
    assert (result.data["total"], result.data["stat"], [g["turnovers"] for g in result.data["games"]]) == (5, "turnovers", [5, 0])
    assert "averaged 2.5 turnovers in the 1st half" in (result.answer or "")
    listed = TEAM_PERIOD_AGREEMENT["turnovers"].get(SEASON)
    assert listed is None or f"{listed:.1f}% of the time in {SEASON}" in (result.answer or "")


def test_team_quarter_points_refuses_a_season_the_line_rebuilds_badly(team_con: duckdb.DuckDBPyConnection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(TEAM_PERIOD_AGREEMENT, "turnovers", {SEASON: 60.6})
    ctx = AnswerContext(con=team_con, out_dir=tmp_path)
    result = team_quarter_points(ctx, Reading.from_slots({"team": "Home Team", "half": 1, "season": SEASON, "season_type": 2, "stat": "turnovers"}))
    assert "total" not in result.data and f"cannot be answered for {SEASON} (61%)" in (result.answer or "")


def test_team_quarter_points_answers_a_shooting_percentage_as_makes_over_attempts(team_con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    """A team's free throw percentage in a half is the ratio of its rebuilt
    makes and attempts over the games (ROADMAP step 2): 1 of 2 in g1's first
    half (Player One's pair), none attempted in g2 - 50.0% over both, each
    game listed as made-attempted. A "most" question is refused rather than
    naming g1 a record over two attempts, and a season either column
    rebuilds badly refuses the percentage with it."""
    ctx = AnswerContext(con=team_con, out_dir=tmp_path)
    slots: dict[str, Any] = {"team": "Home Team", "half": 1, "season": SEASON, "season_type": 2, "stat": "freeThrowPct"}
    result = team_quarter_points(ctx, Reading.from_slots(slots))
    assert (result.data["stat"], result.data["total"], result.data["attempted"], result.data["average"]) == ("ft_pct", 1, 2, 50.0)
    assert [(g["freeThrowsMade"], g["freeThrowsAttempted"], g["ft_pct"]) for g in result.data["games"]] == [(1, 2, 50.0), (0, 0, None)]
    answer = result.answer or ""
    assert "The Home Team shot 1 of 2 (50.0%) on free throws in the 1st half across 2 2026 regular season games:" in answer and "1-2  50.0%" in answer and "0-0  -" in answer
    most = team_quarter_points(ctx, Reading.from_slots({**slots, "rank": "most"}))
    assert "extreme" not in most.data and "is not ranked" in (most.answer or "")


def test_team_quarter_points_refuses_a_shooting_percentage_where_a_column_it_divides_rebuilds_badly(team_con: duckdb.DuckDBPyConnection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(TEAM_PERIOD_AGREEMENT, "freeThrowsAttempted", {SEASON: 70.0})
    ctx = AnswerContext(con=team_con, out_dir=tmp_path)
    result = team_quarter_points(ctx, Reading.from_slots({"team": "Home Team", "half": 1, "season": SEASON, "season_type": 2, "stat": "freeThrowPct"}))
    assert "total" not in result.data
    assert f"1st half free throw percentage cannot be answered for {SEASON} (70%)" in (result.answer or "") and "free throws and free throw attempts match" in (result.answer or "")
