"""The possession walk on a hand-built game: every boundary rule of
``SIMULATION_PLAN.md`` P3.1 and the ones the data forced, the actions and
their second participants, the segment each action is attributed to, the
exclusion of a game whose plays do not reach the final, and the sibling
checks against the box score.

Home team H (h1-h5 start, h6 on the bench) against away team A (a1-a5, a6
on the bench; a9 appears only in a technical from the bench). Two quarters.
The plays are listed in the order of play with ascending ids, and the
running score is computed from the scoring plays so the fixture cannot
disagree with itself.
"""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
import pytest

from association.sim import database, plays, possessions, segments

EVENT = "601"
SEASON = 2025

# (period, clock, team, athlete, participants, type, text, scoring)
_SPEC: list[tuple[int, str, str | None, str | None, str | None, str, str, bool]] = [
    # --- first quarter ---
    (1, "12:00", "H", "h1", "h1,a1,h2", "Jumpball", "h1 vs. a1 (h2 gains possession)", False),  # 1
    (1, "11:40", "H", "h2", "h2,h1", "Jump Shot", "h2 makes 20-foot jumper (h1 assists)", True),  # 2  a made shot
    (1, "11:20", "A", "a1", "a1", "Layup Shot", "a1 misses layup", False),  # 3
    (1, "11:19", "H", "h3", "h3", "Defensive Rebound", "h3 defensive rebound", False),  # 4  a defensive rebound
    (1, "11:00", "A", "a2", "a2,h3", "Shooting Foul", "a2 shooting foul (h3 draws the foul)", False),  # 5
    (1, "11:00", "H", "h3", "h3", "Free Throw - 1 of 2", "h3 misses free throw 1 of 2", False),  # 6
    (1, "11:00", "H", None, None, "Offensive Rebound", "H offensive team rebound", False),  # 7  the dead-ball team rebound
    (1, "11:00", "A", "a6", "a6,a2", "Substitution", "a6 enters the game for a2", False),  # 8  a substitution during the trip
    (1, "11:00", "H", "h3", "h3", "Free Throw - 2 of 2", "h3 makes free throw 2 of 2", True),  # 9  a two-shot trip, made
    (1, "10:40", "A", "a4", "a4,h1", "Bad Pass\nTurnover", "a4 bad pass (h1 steals)", False),  # 10 a live turnover
    (1, "10:30", "H", "h1", "h1", "Driving Layup Shot", "h1 makes driving layup", True),  # 11 an and-one...
    (1, "10:30", "A", "a3", "a3", "Shooting Foul", "a3 shooting foul", False),  # 12
    (1, "10:30", "H", "h1", "h1", "Free Throw - 1 of 1", "h1 makes free throw 1 of 1", True),  # 13 ...ends on the free throw
    (1, "10:00", "A", "a1", "a1", "Jump Shot", "a1 misses 18-foot jumper", False),  # 14 a miss...
    (1, "9:59", "A", "a4", "a4", "Offensive Rebound", "a4 offensive rebound", False),  # 15 ...an offensive rebound...
    (1, "9:55", "A", "a4", "a4", "Tip Shot", "a4 makes tip shot", True),  # 16 ...then a make, one possession
    (1, "9:40", "H", "h2", "h2", "Traveling", "h2 traveling", False),  # 17 a dead turnover
    (1, "9:20", "H", "h4", "h4,a3", "Personal Foul", "h4 personal foul (a3 draws the foul)", False),  # 18 a non-shooting foul before the bonus
    (1, "9:10", "A", "a3", "a3", "Jump Shot", "a3 misses 25-foot three point jumper", False),  # 19
    (1, "9:08", "H", None, None, "Defensive Rebound", "H defensive team rebound", False),  # 20 a defensive TEAM rebound ends it
    (1, "9:00", "A", "a9", "a9", "Technical Foul", "a9 technical foul", False),  # 21 a technical from the bench
    (1, "9:00", "H", "h4", "h4", "Free Throw - Technical", "h4 makes technical free throw", True),  # 22 a technical free throw
    (1, "8:40", "A", "a5", "a5,h5", "Shooting Foul", "a5 shooting foul (h5 draws the foul)", False),  # 23
    (1, "8:40", "H", "h5", "h5", "Free Throw - 1 of 2", "h5 misses free throw 1 of 2", False),  # 24
    (1, "8:40", "H", "h5", "h5", "Free Throw - 2 of 2", "h5 misses free throw 2 of 2", False),  # 25 a two-shot trip missed...
    (1, "8:38", "A", "a1", "a1", "Defensive Rebound", "a1 defensive rebound", False),  # 26 ...rebounded by the defense
    (1, "8:20", "H", "h3", "h3,a1", "Shooting Foul", "h3 shooting foul (a1 draws the foul)", False),  # 27
    (1, "8:20", "A", "a1", "a1", "Free Throw - 1 of 2", "a1 misses free throw 1 of 2", False),  # 28
    (1, "8:20", "A", None, None, "Offensive Rebound", "A offensive team rebound", False),  # 29 the dead-ball team rebound
    (1, "8:20", "A", "a1", "a1", "Free Throw - 2 of 2", "a1 misses free throw 2 of 2", False),  # 30 a two-shot trip missed...
    (1, "8:18", "A", "a4", "a4", "Offensive Rebound", "a4 offensive rebound", False),  # 31 ...rebounded by the offense
    (1, "8:10", "A", "a4", "a4", "Layup Shot", "a4 makes layup", True),  # 32
    (1, "0:05", "H", "h5", "h5", "Jump Shot", "h5 misses 15-foot jumper", False),  # 33
    (1, "0:03", "A", "a6", "a6", "Defensive Rebound", "a6 defensive rebound", False),  # 34
    (1, "0.0", None, None, None, "End Period", "End of the 1st Quarter", False),  # 35 a period end
    # --- second quarter ---
    (2, "12:00", "H", "h6", "h6,h5", "Substitution", "h6 enters the game for h5", False),  # 36
    (2, "11:30", "A", "a1", "a1", "Jump Shot", "a1 makes 16-foot jumper", True),  # 37 the first touch names the offense
    (2, "11:10", "H", "h1", "h1", "Jump Shot", "h1 misses 20-foot jumper", False),  # 38
    (2, "11:09", "H", "h3", "h3", "Offensive Rebound", "h3 offensive rebound", False),  # 39
    (2, "11:05", "A", "a3", "a3,h3", "Personal Take Foul", "a3 personal take foul (h3 draws the foul)", False),  # 40
    (2, "11:05", "H", "h3", "h3", "Free Throw - 1 of 1", "h3 makes free throw 1 of 1", True),  # 41 a lone one-of-one: H keeps the ball
    (2, "10:50", "H", "h3", "h3", "Layup Shot", "h3 makes layup", True),  # 42
    (2, "10:30", "A", "a4", "a4", "Jump Shot", "a4 misses 12-foot jumper", False),  # 43
    (2, "10:29", "A", "a4", "a4,h4,h2", "Jumpball", "a4 vs. h4 (h2 gains possession)", False),  # 44 a held ball won by the defense...
    (2, "10:20", "H", "h2", "h2", "Jump Shot", "h2 misses 15-foot jumper", False),  # 45 ...confirmed by H's next touch
    (2, "10:19", "H", "h3", "h3", "Offensive Rebound", "h3 offensive rebound", False),  # 46
    (2, "10:10", "A", "a5", "a5,h3", "Flagrant Foul Type 1", "a5 flagrant foul type 1 (h3 draws the foul)", False),  # 47
    (2, "10:10", "H", "h3", "h3", "Free Throw - Flagrant 1 of 2", "h3 makes free throw flagrant 1 of 2", True),  # 48
    (2, "10:10", "H", "h3", "h3", "Free Throw - Flagrant 2 of 2", "h3 misses free throw flagrant 2 of 2", False),  # 49
    (2, "10:10", "H", None, None, "Offensive Rebound", "H offensive team rebound", False),  # 50 dead ball; H keeps it
    (2, "10:00", "H", "h2", "h2,h1", "Jump Shot", "h2 makes 25-foot three point jumper (h1 assists)", True),  # 51
    (2, "9:40", "A", "a1", "a1", "Layup Shot", "a1 misses layup", False),  # 52 no rebound is logged...
    (2, "9:30", "H", "h1", "h1", "Dunk Shot", "h1 makes dunk", True),  # 53 ...so H's shot implies the change
    (2, "0:02", "A", "a1", "a1", "Out of Bounds - Lost Ball Turnover", "a1 out of bounds lost ball turnover", False),  # 54
    (2, "0.0", None, None, None, "End Period", "End of the 2nd Quarter", False),  # 55
    (2, "0.0", None, None, None, "End Game", "End of Game", False),  # 56
]

# Shot values: a three where the text says so, else two (the shot chart carries the label).
_THREES = {19, 51}


def _plays() -> list[dict[str, Any]]:
    rows = []
    home = away = 0
    for n, (period, clock, team, athlete, parts, type_, text, scoring) in enumerate(_SPEC, start=1):
        if scoring:
            value = 1 if type_.startswith("Free Throw") else (3 if n in _THREES else 2)
            if team == "H":
                home += value
            else:
                away += value
        rows.append(
            {
                "event_id": EVENT,
                "play_id": f"{EVENT}{n:03d}",
                "season": SEASON,
                "season_type": 2,
                "period": period,
                "clock": clock,
                "team_id": team,
                "athlete_id": athlete,
                "participant_athlete_ids": parts,
                "type": type_,
                "text": text,
                "home_score": home,
                "away_score": away,
                "scoring_play": scoring,
            }
        )
    return rows


FINAL = (16, 6)


def _shots() -> list[dict[str, Any]]:
    rows = []
    for n, (_, _, _, _, _, type_, text, scoring) in enumerate(_SPEC, start=1):
        if plays.vocab.kind_of(type_) is None or plays.vocab.kind_of(type_).kind != "shot":  # type: ignore[union-attr]
            continue
        three = n in _THREES
        rows.append(
            {
                "event_id": EVENT,
                "play_id": f"{EVENT}{n:03d}",
                "season": SEASON,
                "season_type": 2,
                "shot_type": type_,
                "points_attempted": 3 if three else 2,
                "coordinate_x": 25,
                "coordinate_y": 26 if three else 2,
                "description": text,
                "made": scoring,
            }
        )
    return rows


# The box score the actions should reproduce exactly: FGA, FTA, OREB, DREB, AST, TOV.
_BOX_LINES = {
    "H": {"h1": (3, 1, 0, 0, 2, 0), "h2": (3, 0, 0, 0, 0, 1), "h3": (1, 5, 2, 1, 0, 0), "h4": (0, 1, 0, 0, 0, 0), "h5": (1, 2, 0, 0, 0, 0), "h6": (0, 0, 0, 0, 0, 0)},
    "A": {"a1": (4, 2, 0, 1, 0, 1), "a2": (0, 0, 0, 0, 0, 0), "a3": (1, 0, 0, 0, 0, 0), "a4": (3, 0, 2, 0, 0, 1), "a5": (0, 0, 0, 0, 0, 0), "a6": (0, 0, 0, 1, 0, 0)},
}


def _box() -> list[dict[str, Any]]:
    rows = []
    for team, lines in _BOX_LINES.items():
        for athlete, (fga, fta, oreb, dreb, ast, tov) in lines.items():
            rows.append(
                {
                    "event_id": EVENT,
                    "season": SEASON,
                    "season_type": 2,
                    "team_id": team,
                    "athlete_id": athlete,
                    "starter": not athlete.endswith("6"),
                    "minutes": 20,
                    "plusMinus": 0,
                    "did_not_play": False,
                    "fieldGoalsAttempted": fga,
                    "freeThrowsAttempted": fta,
                    "offensiveRebounds": oreb,
                    "defensiveRebounds": dreb,
                    "assists": ast,
                    "turnovers": tov,
                }
            )
    rows.append({**rows[-1], "athlete_id": "a9", "starter": False, "minutes": None, "did_not_play": True})
    return rows


def _games(*, short_final: bool = False) -> list[dict[str, Any]]:
    home, away = FINAL
    games = [{"event_id": EVENT, "season": SEASON, "season_type": 2, "home_team_id": "H", "away_team_id": "A", "home_score": home, "away_score": away}]
    if short_final:
        # A second game whose only play is a two but whose final says five: excluded, not patched.
        games.append({"event_id": "602", "season": SEASON, "season_type": 2, "home_team_id": "H", "away_team_id": "A", "home_score": 5, "away_score": 0})
    return games


def _warehouse(path: Path, *, with_short_game: bool = False) -> None:
    play_rows = _plays()
    if with_short_game:
        play_rows.append({**play_rows[1], "event_id": "602", "play_id": "602001", "home_score": 2, "away_score": 0})
        play_rows.append({**play_rows[34], "event_id": "602", "play_id": "602002", "home_score": 2, "away_score": 0})
    wh = duckdb.connect(str(path))
    for name, rows in (("plays", play_rows), ("shot_chart", _shots()), ("real_games", _games(short_final=with_short_game)), ("player_box_stats_deduped", _box())):
        wh.register("rows", pa.Table.from_pylist(rows))
        wh.execute(f"CREATE TABLE {name} AS SELECT * FROM rows")
        wh.unregister("rows")
    wh.close()


@pytest.fixture
def con(tmp_path: Path) -> Iterator[duckdb.DuckDBPyConnection]:
    _warehouse(tmp_path / "nba.duckdb")
    c = database.connect(tmp_path / "sim.duckdb", tmp_path / "nba.duckdb")
    plays.build_plays(c)
    segments.build_segments(c)
    yield c
    c.close()


def _possessions(con: duckdb.DuckDBPyConnection) -> list[tuple[Any, ...]]:
    columns = "poss_no, period, offense_team_id, start_seq, end_seq, start_elapsed, end_elapsed, start_type, end_type, points, actions, segment_no"
    return con.execute(f"SELECT {columns} FROM sim_possessions ORDER BY poss_no").fetchall()


def test_the_first_quarter_boundaries(con: duckdb.DuckDBPyConnection) -> None:
    possessions.build_possessions(con)
    first = [p for p in _possessions(con) if p[1] == 1]
    # (offense, start_seq, end_seq, start_type, end_type, points)
    assert [(p[2], p[3], p[4], p[7], p[8], p[9]) for p in first] == [
        ("H", 1, 2, "period_start", "made_shot", 2),  # the made shot ends it; the next starts at the miss
        ("A", 3, 4, "after_made", "dreb_shot", 0),
        ("H", 5, 9, "after_dreb", "made_ft", 1),  # the trip: a miss, the dead-ball team rebound, a substitution, the make
        ("A", 10, 10, "after_ft", "live_turnover", 0),
        ("H", 11, 13, "after_live_turnover", "made_ft", 3),  # the and-one ends on the free throw, not the shot
        ("A", 14, 16, "after_ft", "made_shot", 2),  # a miss, an offensive rebound and a make are one possession
        ("H", 17, 17, "after_made", "dead_turnover", 0),
        ("A", 18, 20, "after_dead_turnover", "dreb_shot", 0),  # the non-shooting foul does not end it; the defensive TEAM rebound does
        ("H", 21, 26, "after_dreb", "dreb_ft", 0),  # the technical is beside it; the missed trip ends on the defense's rebound
        ("A", 27, 32, "after_dreb", "made_shot", 2),  # the missed trip's offensive rebound continues it
        ("H", 33, 34, "after_made", "dreb_shot", 0),
        ("A", 35, 35, "after_dreb", "period_end", 0),  # three seconds with the ball and no action: kept, it has time
    ]
    assert [(p[5], p[6]) for p in first][:3] == [(0.0, 20.0), (20.0, 41.0), (41.0, 60.0)]
    assert first[-1][5:7] == (717.0, 720.0)


def test_the_second_quarter_rules_the_data_forced(con: duckdb.DuckDBPyConnection) -> None:
    possessions.build_possessions(con)
    second = [p for p in _possessions(con) if p[1] == 2]
    assert [(p[2], p[3], p[4], p[7], p[8], p[9]) for p in second] == [
        ("A", 36, 37, "period_start", "made_shot", 2),  # the first touch names the offense
        ("H", 38, 42, "after_made", "made_shot", 3),  # the lone one-of-one keeps the ball: 1 + 2 in one possession
        ("A", 43, 44, "after_made", "jump_ball", 0),  # the held ball won by the defense, confirmed by H's shot
        ("H", 45, 51, "after_other", "made_shot", 4),  # the flagrant trip keeps the ball, its miss is a dead ball: 1 + 3
        ("A", 52, 52, "after_made", "implied", 0),  # no rebound logged: H's shot implies the change
        ("H", 53, 53, "after_other", "made_shot", 2),
        ("A", 54, 54, "after_made", "dead_turnover", 0),
        ("H", 55, 56, "after_dead_turnover", "period_end", 0),  # closed by the End Game row
    ]


def test_the_score_check_sums_possession_points_and_technical_free_throws(con: duckdb.DuckDBPyConnection) -> None:
    (report,) = possessions.build_possessions(con)
    assert report.season == SEASON and report.games == 1 and report.games_excluded == 0
    assert report.team_games == 2 and report.score_exact == 2 and report.score_share == 1.0
    home, away = con.execute("SELECT sum(points) FILTER (WHERE offense_team_id = 'H'), sum(points) FILTER (WHERE offense_team_id = 'A') FROM sim_possessions").fetchone()  # type: ignore[misc]
    technical = con.execute("SELECT team_id, points FROM sim_actions WHERE poss_no IS NULL AND kind = 'free_throw'").fetchall()
    assert (home, away) == (15, 6) and technical == [("H", 1)]
    assert report.possessions == 20 and report.possessions_per_team_game == 10.0


def test_the_box_checks(con: duckdb.DuckDBPyConnection) -> None:
    (report,) = possessions.build_possessions(con)
    # H: FGA 8 - OREB 2 + TOV 1 + 0.44 * 9 FTA = 10.96 against 10; A: 8 - 2 + 2 + 0.88 = 8.88 against 10.
    assert report.box_team_games == 2
    assert report.box_mean_abs_diff == pytest.approx((0.96 + 1.12) / 2)
    assert report.box_mean_diff == pytest.approx((-0.96 + 1.12) / 2)
    assert (report.games_compared, report.games_within_periods) == (1, 1)
    # Twelve players with minutes; a9 (no minutes) is not compared. Every stat matches the box exactly.
    assert report.player_games == 12
    assert all(report.player_share(stat) == 1.0 for stat in ("fga", "fta", "oreb", "dreb", "ast", "tov"))
    assert (report.and_ones, report.lone_one_of_ones, report.implied, report.jump_ball_changes, report.dead_rebounds, report.side_disagreements) == (1, 1, 1, 1, 3, 0)


def test_actions_carry_the_second_participant_the_zone_and_the_trip(con: duckdb.DuckDBPyConnection) -> None:
    possessions.build_possessions(con)
    columns = "seq, kind, athlete_id, partner_id, partner_role, zone, shot_value, made, points, ft_kind, ft_number, ft_of, team_play, live_ball, dead_ball, ends_possession, poss_no"
    rows = con.execute(f"SELECT {columns} FROM sim_actions ORDER BY seq").fetchall()
    by_seq = {r[0]: r[1:] for r in rows}
    assert by_seq[2] == ("shot", "h2", "h1", "assist", "restricted", 2, True, 2, None, None, None, False, None, False, True, 1)
    assert by_seq[51][:8] == ("shot", "h2", "h1", "assist", "above_break_three", 3, True, 3)
    assert by_seq[10] == ("turnover", "a4", "h1", "steal", None, None, None, 0, None, None, None, False, True, False, True, 4)
    assert by_seq[17][12:] == (False, False, True, 7)  # a dead turnover
    assert by_seq[5] == ("foul_shooting", "a2", "h3", "fouled", None, None, None, 0, None, None, None, False, None, False, False, 3)
    assert by_seq[6][8:12] == ("regular", 1, 2, False) and by_seq[9][8:12] == ("regular", 2, 2, False)
    assert by_seq[7][1:3] == (None, None) and by_seq[7][11:15] == (True, None, True, False)  # the dead-ball team rebound ends nothing
    assert by_seq[20][11:15] == (True, None, False, True)  # the defensive team rebound ends the possession
    assert by_seq[22][8:] == ("technical", 1, 1, False, None, False, False, None)  # a technical free throw belongs to no possession
    assert by_seq[21][-1] is None  # nor does the technical foul
    assert by_seq[48][8:12] == ("flagrant", 1, 2, False) and by_seq[50][12:15] == (None, True, False)
    assert by_seq[44][1:4] == ("a4", "h2", "gains_possession") and by_seq[44][14:] == (True, 15)  # the jump ball that ended possession 15
    assert con.execute("SELECT count(*) FROM sim_actions").fetchone() == (len([s for s in _SPEC if plays.vocab.kind_of(s[5]) and plays.vocab.kind_of(s[5]).kind in possessions.ACTION_KINDS]),)  # type: ignore[union-attr]


def test_actions_are_attributed_to_the_segment_current_at_them(con: duckdb.DuckDBPyConnection) -> None:
    possessions.build_possessions(con)
    # a6 replaces a2 at 11:00 between the free throws; the segment closes at the next clock reading (10:40),
    # so the second free throw is credited to the five on the floor before the substitution, as ESPN does.
    seg = dict(con.execute("SELECT seq, segment_no FROM sim_actions WHERE seq IN (6, 9, 10)").fetchall())
    first_segment = con.execute("SELECT segment_no, away_lineup, end_seq FROM sim_segments WHERE period = 1 ORDER BY segment_no LIMIT 1").fetchone()
    assert first_segment is not None and "a2" in first_segment[1] and first_segment[2] == 9
    assert seg[6] == seg[9] == first_segment[0] and seg[10] == first_segment[0] + 1
    # The possession carries the segment at its start; the second quarter's opening possession starts at the
    # substitution at 12:00, whose zero-length segment is dropped, and falls back to the period's first segment.
    assert [p[11] for p in _possessions(con) if p[0] in (3, 4)] == [first_segment[0], first_segment[0] + 1]
    second_first = con.execute("SELECT min(segment_no) FROM sim_segments WHERE period = 2").fetchone()
    assert next(p[11] for p in _possessions(con) if p[1] == 2) == second_first[0]  # type: ignore[index]


def test_a_game_whose_plays_do_not_reach_the_final_is_excluded_and_counted(tmp_path: Path) -> None:
    _warehouse(tmp_path / "nba.duckdb", with_short_game=True)
    c = database.connect(tmp_path / "sim.duckdb", tmp_path / "nba.duckdb")
    try:
        plays.build_plays(c)
        (report,) = possessions.build_possessions(c)
        assert (report.games, report.games_excluded) == (1, 1)
        assert c.execute("SELECT count(DISTINCT event_id) FROM sim_possessions").fetchone() == (1,)
        # Without segments, segment_no is NULL rather than a failure.
        assert c.execute("SELECT count(*) FILTER (WHERE segment_no IS NOT NULL) FROM sim_actions").fetchone() == (0,)
    finally:
        c.close()


def test_free_throw_shape() -> None:
    assert possessions.free_throw_shape("Free Throw - 1 of 2") == ("regular", 1, 2)
    assert possessions.free_throw_shape("Free Throw - 3 of 3") == ("regular", 3, 3)
    assert possessions.free_throw_shape("Free Throw - Flagrant 2 of 2") == ("flagrant", 2, 2)
    assert possessions.free_throw_shape("Free Throw - Clear Path 1 of 2") == ("clear_path", 1, 2)
    assert possessions.free_throw_shape("Free Throw - Technical") == ("technical", 1, 1)
    assert possessions.free_throw_shape(None) == ("regular", 1, 1)


def test_start_and_end_types_are_the_documented_sets(con: duckdb.DuckDBPyConnection) -> None:
    possessions.build_possessions(con)
    starts = {r[0] for r in con.execute("SELECT DISTINCT start_type FROM sim_possessions").fetchall()}
    ends = {r[0] for r in con.execute("SELECT DISTINCT end_type FROM sim_possessions").fetchall()}
    assert starts <= set(possessions.START_TYPES) and ends <= set(possessions.END_TYPES)
    assert "after_live_turnover" in starts and "after_dead_turnover" in starts and "after_ft" in starts
