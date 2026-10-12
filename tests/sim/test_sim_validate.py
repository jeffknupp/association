"""The harness's arithmetic, offline: the Brier score, the PIT and its
histogram, the strength fit recovering known parameters, and the report
over hand-built checks."""

from __future__ import annotations

import datetime as dt
import math
import random

import pytest

from association.sim import validate
from association.sim.validate import Baselines, GameCheck, PlayerCheck, TeamLine, Validation


def test_brier_scores_a_coin_at_a_quarter_and_certainty_at_zero_or_one() -> None:
    assert validate.brier([0.5, 0.5], [True, False]) == 0.25
    assert validate.brier([1.0, 0.0], [True, False]) == 0.0
    assert validate.brier([0.0], [True]) == 1.0
    assert math.isnan(validate.brier([], []))


def test_pit_places_the_real_value_among_the_simulated_with_ties_split() -> None:
    assert validate.pit(5.0, [1, 2, 3, 4]) == 1.0
    assert validate.pit(0.0, [1, 2, 3, 4]) == 0.0
    assert validate.pit(2.0, [1, 2, 3, 4]) == pytest.approx((1 + 0.5) / 4)
    hist = validate.pit_histogram([0.0, 0.05, 0.15, 0.5, 0.95, 1.0], bins=10)
    assert hist[0] == pytest.approx(2 / 6) and hist[1] == pytest.approx(1 / 6) and hist[9] == pytest.approx(2 / 6)
    assert sum(hist) == pytest.approx(1.0)


def test_the_strength_fit_recovers_known_parameters() -> None:
    rng = random.Random(3)
    a_true, b_true = 0.3, 0.15
    xs = [rng.gauss(0, 6) for _ in range(6000)]
    ys = [rng.random() < 1 / (1 + math.exp(-(a_true + b_true * x))) for x in xs]
    a, b = validate.fit_strength_model(xs, ys)
    assert abs(a - a_true) < 0.08 and abs(b - b_true) < 0.03
    assert validate.fit_strength_model([], []) == (0.0, 0.0)


def _line(points: float, fga: float = 88, tpa: float = 36, fta: float = 22, oreb: float = 10, dreb: float = 33, tov: float = 13) -> TeamLine:
    return TeamLine(points, fga, tpa, fta, oreb, dreb, tov)


def _check(event: str, home_score: int, away_score: int, p_sim: float, p_strength: float | None, p_espn: float | None, pit_value: float) -> GameCheck:
    players = (
        PlayerCheck("p1", "H", 34.0, 20, 8, 5, 100, 33.0, 12.0, 28.0, 5.0, 11.0, 2.0, 7.0),  # inside on all three
        PlayerCheck("p2", "A", 10.0, 30, 2, 1, 100, 12.0, 2.0, 9.0, 0.0, 4.0, 0.0, 3.0),  # points outside
    )
    return GameCheck(
        event,
        dt.date(2025, 1, 2),
        "H",
        "A",
        home_score,
        away_score,
        100,
        p_sim,
        0.55,
        p_strength,
        p_espn,
        3.0,
        13.0,
        pit_value,
        228.0,
        18.0,
        _line(110),
        _line(104),
        _line(112),
        _line(106),
        players,
        2.5,
    )


def _run() -> Validation:
    baselines = Baselines(0.55, 2000, 0.2, 0.1, {}, {})
    run = Validation(2025, dt.date(2025, 1, 1), 100, 7, baselines)
    run.games = [
        _check("1", 110, 104, 0.7, 0.65, 0.6, 0.55),
        _check("2", 99, 105, 0.4, None, 0.45, 0.15),
        _check("3", 120, 119, 0.5, 0.5, None, 0.95),
    ]
    run.refused.append(("4", "no minutes before 2025-01-02 for 999"))
    return run


def test_metrics_and_report_over_hand_built_checks() -> None:
    run = _run()
    m = validate.metrics(run)
    assert m["games"] == 3 and m["refused"] == 1
    assert m["brier_sim"] == pytest.approx(((0.7 - 1) ** 2 + (0.4 - 0) ** 2 + (0.5 - 1) ** 2) / 3)
    assert m["brier_strength_games"] == 2 and m["brier_espn_games"] == 2
    assert m["brier_strength"] == pytest.approx(((0.65 - 1) ** 2 + (0.5 - 1) ** 2) / 2)
    assert m["home_win_rate_real"] == pytest.approx(2 / 3)
    assert sum(m["margin_pit"]) == pytest.approx(1.0) and m["margin_pit"][5] == pytest.approx(1 / 3)
    assert m["margin_mean_error"] == pytest.approx((3 - 6 + 3 + 6 + 3 - 1) / 3)
    assert m["league_real"]["pace"] == pytest.approx(88 - 10 + 13 + 0.44 * 22)
    assert m["league_sim"]["points_per_100"] > m["league_real"]["points_per_100"]
    assert m["player_games"] == 6
    assert m["coverage_points"] == pytest.approx(0.5) and m["coverage_rebounds"] == pytest.approx(1.0)
    assert m["minutes_mean_error"] == pytest.approx(((33 - 34) + (12 - 10)) / 2)
    text = validate.report(run, m)
    assert text.startswith("# Replay validation: 2025 regular season from 2025-01-01")
    assert "| simulation | 3 |" in text and "| ESPN pre-game | 2 |" in text
    assert "## Refused" in text and "999" in text
    assert "possessions (box formula)" in text
    data = validate.games_json(run)
    assert len(data) == 3 and data[0]["date"] == "2025-01-02" and data[0]["players"][0]["athlete_id"] == "p1"


def test_baselines_answer_for_a_game_with_and_without_ratings() -> None:
    b = Baselines(0.57, 100, 0.0, 0.2, {"g1": (3.0, -2.0), "g2": (None, 1.0)}, {"g1": 0.61})
    assert b.for_game("g1") == {"home_rate": 0.57, "strength": pytest.approx(1 / (1 + math.exp(-1.0))), "espn": 0.61}
    assert b.for_game("g2") == {"home_rate": 0.57, "strength": None, "espn": None}
    assert b.for_game("g3")["strength"] is None


def test_held_out_days_group_the_games_by_eastern_day_and_honor_the_limit() -> None:
    import duckdb

    con = duckdb.connect(":memory:")
    con.execute("ATTACH ':memory:' AS nba")
    con.execute("CREATE TABLE nba.real_games (event_id VARCHAR, season INTEGER, season_type INTEGER, date VARCHAR)")
    rows = [
        ("g1", 2025, 2, "2025-01-02T00:30Z"),  # 7:30 pm Eastern on January 1
        ("g2", 2025, 2, "2025-01-02T01:00Z"),  # the same Eastern day
        ("g3", 2025, 2, "2025-01-03T00:00Z"),  # January 2
        ("g0", 2025, 2, "2024-12-31T23:00Z"),  # before the start
        ("g4", 2025, 2, "2025-01-04T00:00Z"),  # January 3
        ("p1", 2025, 3, "2025-01-04T00:00Z"),  # a playoff game is not held out
    ]
    con.executemany("INSERT INTO nba.real_games VALUES (?, ?, ?, ?)", rows)
    assert validate.held_out_days(con, 2025, dt.date(2025, 1, 1)) == [["g1", "g2"], ["g3"], ["g4"]]
    assert validate.held_out_days(con, 2025, dt.date(2025, 1, 1), limit=3) == [["g1", "g2"], ["g3"]]
    assert validate.held_out(con, 2025, dt.date(2025, 1, 1)) == ["g1", "g2", "g3", "g4"]


def test_parallel_validation_needs_a_connect_factory() -> None:
    import duckdb

    with pytest.raises(ValueError, match="connect factory"):
        validate.validate(duckdb.connect(":memory:"), 2025, dt.date(2025, 1, 1), 1, 7, jobs=2)


def test_crps_is_the_absolute_error_of_a_point_forecast_and_rewards_a_right_sized_spread() -> None:
    assert validate.crps([7.0, 7.0, 7.0], 10.0) == pytest.approx(3.0)
    assert validate.crps([0.0, 2.0], 1.0) == pytest.approx(0.5)  # mean |x - 1| = 1, mean |x - x'| = 1
    assert validate.crps([0.0, 2.0], 1.0) < validate.crps([1.0, 1.0], 2.0) < validate.crps([0.0, 4.0], 10.0)
    assert math.isnan(validate.crps([], 3.0))
    # Shrinking a well-centered spread toward the truth lowers the score; widening a centered spread raises it.
    centered = [8.0, 9.0, 10.0, 11.0, 12.0]
    assert validate.crps(centered, 10.0) < validate.crps([6.0, 8.0, 10.0, 12.0, 14.0], 10.0)


def test_player_accuracy_is_reported_by_tier_of_simulated_minutes() -> None:
    def player(athlete: str, team: str, minutes: float, pts: int, sim_minutes: float, pts_mean: float) -> PlayerCheck:
        return PlayerCheck(athlete, team, minutes, pts, 5, 3, 100, sim_minutes, pts_mean - 5, pts_mean + 5, 2.0, 8.0, 1.0, 5.0, pts_mean, 5.0, 3.0, 2.5, 1.0, 0.8, 4.0)

    game = GameCheck(
        "g1",
        dt.date(2025, 1, 2),
        "H",
        "A",
        100,
        90,
        100,
        0.6,
        0.55,
        None,
        None,
        10.0,
        13.0,
        0.5,
        190.0,
        19.0,
        _line(100),
        _line(90),
        _line(101),
        _line(89),
        (player("s1", "H", 36.0, 30, 34.0, 24.0), player("b1", "H", 12.0, 4, 10.0, 6.0), player("s2", "A", 31.0, 18, 31.0, 20.0), player("r1", "A", 22.0, 9, 20.0, 9.0)),
        1.0,
    )
    m = validate._player_metrics([game])
    assert m["points_mae"] == pytest.approx((6 + 2 + 2 + 0) / 4) and m["points_crps"] == pytest.approx(2.5) and m["minutes_mae"] == pytest.approx((2 + 2 + 0 + 2) / 4)
    tiers = {t["tier"]: t for t in m["tiers"]}
    assert tiers["30+ simulated minutes"]["n"] == 2 and tiers["30+ simulated minutes"]["points_mae"] == pytest.approx(4.0)
    assert tiers["15-30 simulated minutes"]["n"] == 1 and tiers["under 15 simulated minutes"]["n"] == 1
    assert tiers["the model's top scorer on each side"]["n"] == 2 and tiers["the model's top scorer on each side"]["points_mae"] == pytest.approx(4.0)
    run = Validation(2025, dt.date(2025, 1, 1), 100, 7, Baselines(0.55, 10, 0.0, 0.1, {}, {}), games=[game])
    text = validate.report(run)
    assert "| 30+ simulated minutes | 2 | 4.00 | 2.50 |" in text and "the model's top scorer on each side" in text and "CRPS" in text
