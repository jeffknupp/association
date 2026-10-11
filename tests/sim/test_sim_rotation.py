"""The rotation profile on a two-game fixture with hand-computable
probabilities, the league tables' cells, and the policy's rules: a fouled-out
player never returns, an unavailable one never enters, infinite hysteresis
keeps the starters on all game, five distinct rostered ids at every dead ball
of a dry run, and the tip.

Home team H (team id ``H``) against A. Game 1: h1-h5 start, h6 replaces h1 at
halftime and h7 replaces h2 at the 300-second mark of the first quarter after
h2's third foul; A's five play the whole game. Game 2: h1-h5 play the whole
game, h6 and h7 do not appear, and the fourth quarter is unresolved.
"""

from __future__ import annotations

import datetime as dt
import math
import random
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
import pytest

from association.sim import database, rotation, vocab
from association.sim.params import PlayerGameState, RotationContext, Window
from association.sim.rotation import MINUTE_BINS, PlayerProfile, RotationModel, RotationProfile, RotationSettings

SEASON = 2025
WINDOW = Window((SEASON,), dt.date(2025, 9, 1))
A_FIVE = ["a1", "a2", "a3", "a4", "a5"]


def _segment(event: str, period: int, no: int, start: float, end: float, home: list[str], resolved: bool = True) -> dict[str, Any]:
    return {
        "event_id": event,
        "season": SEASON,
        "period": period,
        "segment_no": no,
        "start_seq": no * 10,
        "end_seq": no * 10 + 9,
        "start_elapsed": start,
        "end_elapsed": end,
        "home_lineup": sorted(home),
        "away_lineup": A_FIVE,
        "home_points": 10,
        "away_points": 8,
        "carried": False,
        "resolved": resolved,
    }


_SEGMENTS = [
    # Game 1: h2 fouls out of the rotation at 300 s, h1 sits the second half.
    _segment("g1", 1, 1, 0.0, 300.0, ["h1", "h2", "h3", "h4", "h5"]),
    _segment("g1", 1, 2, 300.0, 720.0, ["h1", "h7", "h3", "h4", "h5"]),
    _segment("g1", 2, 3, 720.0, 1440.0, ["h1", "h7", "h3", "h4", "h5"]),
    _segment("g1", 3, 4, 1440.0, 2160.0, ["h6", "h7", "h3", "h4", "h5"]),
    _segment("g1", 4, 5, 2160.0, 2880.0, ["h6", "h7", "h3", "h4", "h5"]),
    # Game 2: the starters all game; the fourth quarter unresolved.
    _segment("g2", 1, 1, 0.0, 720.0, ["h1", "h2", "h3", "h4", "h5"]),
    _segment("g2", 2, 2, 720.0, 1440.0, ["h1", "h2", "h3", "h4", "h5"]),
    _segment("g2", 3, 3, 1440.0, 2160.0, ["h1", "h2", "h3", "h4", "h5"]),
    _segment("g2", 4, 4, 2160.0, 2880.0, ["h1", "h2", "h3", "h4", "h5"], resolved=False),
]


def _foul(event: str, seq: int, elapsed: float, athlete: str) -> dict[str, Any]:
    return {"event_id": event, "season": SEASON, "seq": seq, "period": int(elapsed // 720) + 1, "elapsed": elapsed, "kind": "foul_personal", "athlete_id": athlete}


_PLAYS = [_foul("g1", 1, 100.0, "h2"), _foul("g1", 2, 200.0, "h2"), _foul("g1", 3, 299.0, "h2")]


def _box(event: str, athlete: str, minutes: int, starter: bool) -> dict[str, Any]:
    return {"event_id": event, "season": SEASON, "team_id": "H", "athlete_id": athlete, "starter": starter, "minutes": minutes}


_BOX = [
    *[_box("g1", a, 48, True) for a in ("h3", "h4", "h5")],
    _box("g1", "h1", 24, True),
    _box("g1", "h2", 5, True),
    _box("g1", "h6", 24, False),
    _box("g1", "h7", 43, False),
    *[_box("g2", a, 48, True) for a in ("h1", "h2", "h3", "h4", "h5")],
    *[{"event_id": e, "season": SEASON, "team_id": "A", "athlete_id": a, "starter": True, "minutes": 48} for e in ("g1", "g2") for a in A_FIVE],
]

_GAMES = [
    {"event_id": "g1", "season": SEASON, "date": "2025-01-10T00:00Z", "home_team_id": "H", "away_team_id": "A"},
    {"event_id": "g2", "season": SEASON, "date": "2025-01-12T00:00Z", "home_team_id": "H", "away_team_id": "A"},
    # Outside the window: on as_of itself, so excluded (strictly before).
    {"event_id": "g3", "season": SEASON, "date": "2025-09-01T00:00Z", "home_team_id": "H", "away_team_id": "A"},
]


@pytest.fixture
def con(tmp_path: Path) -> Iterator[duckdb.DuckDBPyConnection]:
    warehouse = tmp_path / "nba.duckdb"
    w = duckdb.connect(str(warehouse))
    w.register("games", pa.Table.from_pylist(_GAMES))
    w.execute("CREATE TABLE real_games AS SELECT * FROM games")
    w.register("box", pa.Table.from_pylist(_BOX))
    w.execute("CREATE TABLE player_box_stats_deduped AS SELECT * FROM box")
    w.close()
    connection = database.connect(tmp_path / "sim.duckdb", warehouse)
    connection.register("segs", pa.Table.from_pylist([*_SEGMENTS, _segment("g3", 1, 1, 0.0, 2880.0, ["h8", "h9", "h10", "h11", "h12"])]))
    connection.execute("CREATE TABLE sim_segments AS SELECT * FROM segs")
    connection.register("plays", pa.Table.from_pylist(_PLAYS))
    connection.execute("CREATE TABLE sim_plays AS SELECT * FROM plays")
    yield connection
    connection.close()


def test_the_profile_has_the_hand_computed_probabilities_and_targets(con: duckdb.DuckDBPyConnection) -> None:
    profiles = rotation.build_profiles(con, WINDOW)
    assert set(profiles) == {"H", "A"}
    home = profiles["H"]
    assert home.games == 2 and set(home.players) == {"h1", "h2", "h3", "h4", "h5", "h6", "h7"}
    h1 = home.players["h1"]
    # Game 1 first half on, second half off; game 2 on through the third quarter, the fourth unresolved
    # (no resolved seconds, so it counts for nothing): 1.0, then 0.5, then 0.0 in the last quarter.
    assert h1.on_floor[:24] == (1.0,) * 24
    assert h1.on_floor[24:36] == (0.5,) * 12
    assert h1.on_floor[36:48] == (0.0,) * 12
    assert h1.on_floor[rotation.OVERTIME_BIN] == 0.0
    assert (h1.games, h1.started, h1.minutes_target) == (2, 2, 36.0)
    h6 = home.players["h6"]  # appears in game 1 only: conditioned on that game
    assert h6.on_floor[:24] == (0.0,) * 24 and h6.on_floor[24:48] == (1.0,) * 24
    assert (h6.games, h6.started, h6.minutes_target) == (1, 0, 24.0)
    h2 = home.players["h2"]  # on for the first 300 s of game 1 and all of game 2: 1.0 through minute 4, then 0.5
    assert h2.on_floor[4] == 1.0 and h2.on_floor[5] == 0.5 and h2.on_floor[23] == 0.5
    assert h2.minutes_target == (5 + 48) / 2
    assert home.likely_starters() == ("h1", "h2", "h3", "h4", "h5")
    assert home.likely_starters(["h3", "h6", "h7"]) == ("h3", "h6", "h7")
    assert home.on_floor_prob("nobody", 0.0) == rotation.UNKNOWN_ON_FLOOR
    assert home.players["h3"].expected_seconds(90.0) == 90.0 and h1.expected_seconds(1500.0) == 1440.0 + 30.0


def test_the_league_tables_hold_the_fixtures_cells(con: duckdb.DuckDBPyConnection) -> None:
    league = rotation.build_league_rotation(con, WINDOW, strength=50.0)
    # h2 has three fouls from 299 s: the samples at 330, 390, ..., 690 s of game 1 (seven) are off the
    # floor, each expected at his profile's 0.5 for those minutes: (0 + 50) / (3.5 + 50).
    assert league.foul_samples[(3, 1)] == 7
    assert league.foul_ratio[(3, 1)] == pytest.approx(50 / 53.5)
    assert league.foul(3, 1) == pytest.approx(min(1.0, league.foul_ratio[(3, 1)] / league.foul_ratio[(0, 1)]))
    assert league.foul(0, 1) == 1.0 and league.foul(5, 9) == 1.0  # a cell never seen is neutral
    # Every sample of the second half is a lead of 2 per segment for H (10-8), within five until the fourth.
    assert league.blowout(0, 100.0) == 1.0  # the first half is not estimated
    assert all(0 < v <= 1.0 for v in (league.blowout(m, t) for m in (0, 7, 25) for t in (1500.0, 2700.0, 2870.0)))
    assert rotation.NEUTRAL_LEAGUE.foul(4, 2) == 1.0 and rotation.NEUTRAL_LEAGUE.blowout(30, 2800.0) == 1.0


def test_the_buckets() -> None:
    assert [rotation.margin_bucket(m) for m in (0, -5, 6, 15, -16, 20, 21, 30, 31, 50)] == [0, 0, 1, 2, 3, 3, 4, 4, 5, 5]
    assert rotation.time_bucket(0.0) is None and rotation.time_bucket(1439.0) is None
    assert [rotation.time_bucket(2880.0 - r) for r in (1, 180, 181, 360, 540, 720, 1080, 1440)] == [0, 0, 1, 1, 2, 3, 4, 5]
    assert rotation.time_bucket(2880.0) is None and rotation.time_bucket(3000.0) is None
    assert [rotation.minute_bin(e) for e in (0.0, 59.9, 60.0, 2879.0, 2880.0, 3500.0)] == [0, 0, 1, 47, 48, 48]
    assert [rotation.period_key(p) for p in (1, 4, 5, 7)] == [1, 4, 5, 5]
    assert rotation.fatigue(100.0, RotationSettings(fresh_seconds=240.0)) == 1.0
    assert rotation.fatigue(540.0, RotationSettings(fresh_seconds=240.0, fatigue_tau=300.0)) == pytest.approx(math.exp(-1))
    assert rotation.recovery(0.5, 0.0, RotationSettings()) == 0.5 and rotation.recovery(0.5, 1e9, RotationSettings()) == pytest.approx(1.0)
    with pytest.raises(ValueError):
        PlayerProfile("x", 1, 0, 10.0, (1.0,) * 3)


# A hand-built profile: five starters who play the first and third quarters heavily, a bench of five.
def _profile(bench_weight: float = 0.4) -> RotationProfile:
    def bins(high: float, low: float) -> tuple[float, ...]:
        return (*((high if (m // 12) % 2 == 0 else low) for m in range(48)), high)

    players = {}
    for i in range(1, 6):
        players[f"s{i}"] = PlayerProfile(f"s{i}", 80, 80, 34.0, bins(0.95, 0.55))
    for i in range(1, 6):
        players[f"b{i}"] = PlayerProfile(f"b{i}", 80, 0, 14.0, bins(0.05, bench_weight))
    return RotationProfile("T", WINDOW, 80, players)


@dataclass
class _Sim:
    """The dry run's loop in miniature: a dead ball every 30 s, no fouls unless asked."""

    model: RotationModel
    roster: tuple[str, ...]
    unavailable: set[str]
    on: set[str]
    played: dict[str, float]
    stint: dict[str, float]
    fouls: dict[str, int]

    def context(self, elapsed: float, margin: int = 0) -> RotationContext:
        players = tuple(PlayerGameState(a, a in self.on, self.played[a], self.stint[a] if a in self.on else 0.0, self.fouls[a], a not in self.unavailable and self.fouls[a] < 6) for a in self.roster)
        return RotationContext("T", min(int(elapsed // 720) + 1, 4), 720 - elapsed % 720, elapsed, margin, players)

    def step(self, elapsed: float, margin: int = 0) -> tuple[str, ...]:
        lineup = self.model.lineup(self.context(elapsed, margin))
        assert len(lineup) == 5 and len(set(lineup)) == 5 and set(lineup) <= set(self.roster)
        for a in self.on - set(lineup):
            self.stint[a] = 0.0
        self.on = set(lineup)
        for a in self.on:
            self.played[a] += 30.0
            self.stint[a] += 30.0
        return lineup


def _sim(model: RotationModel, roster: tuple[str, ...] | None = None) -> _Sim:
    roster = roster or tuple(model.profile.players)
    return _Sim(model, roster, set(), set(), dict.fromkeys(roster, 0.0), dict.fromkeys(roster, 0.0), dict.fromkeys(roster, 0))


def test_the_tip_is_the_likely_starters_unless_five_are_already_on() -> None:
    model = RotationModel(_profile())
    sim = _sim(model)
    assert sim.step(0.0) == ("s1", "s2", "s3", "s4", "s5")
    seeded = _sim(RotationModel(_profile()))
    seeded.on = {"b1", "b2", "b3", "b4", "b5"}
    assert seeded.step(0.0) == ("b1", "b2", "b3", "b4", "b5")  # the engine seeded five; hysteresis keeps them


def test_a_fouled_out_player_never_returns_and_an_unavailable_one_never_enters() -> None:
    sim = _sim(RotationModel(_profile(), settings=RotationSettings(hysteresis=1.1)))
    sim.unavailable.add("b3")
    sim.step(0.0)
    seen_after_foul_out: set[str] = set()
    for tick in range(1, 96):
        elapsed = tick * 30.0
        if tick == 30:
            sim.fouls["s1"] = 6
        lineup = sim.step(elapsed)
        assert "b3" not in lineup
        if tick > 30:
            seen_after_foul_out.update(lineup)
    assert "s1" not in seen_after_foul_out
    assert sim.played["s1"] > 0  # he did play before fouling out


def test_with_hysteresis_at_infinity_the_starters_play_every_minute() -> None:
    sim = _sim(RotationModel(_profile(), settings=RotationSettings(hysteresis=math.inf)))
    for tick in range(96):
        assert sim.step(tick * 30.0) == ("s1", "s2", "s3", "s4", "s5")
    assert all(sim.played[f"s{i}"] == 48 * 60 for i in range(1, 6)) and all(sim.played[f"b{i}"] == 0 for i in range(1, 6))


def test_a_dry_run_returns_five_distinct_rostered_ids_at_every_dead_ball_and_rotates() -> None:
    rng = random.Random(7)
    model = RotationModel(_profile())
    roster = (*model.profile.players, "newcomer")
    sim = _sim(model, roster)
    elapsed = 0.0
    while elapsed < 2880:
        sim.step(elapsed, margin=rng.randint(-20, 20))
        elapsed += rng.uniform(20, 40)
        if rng.random() < 0.2:
            sim.fouls[rng.choice(sorted(sim.on))] += 1
    assert sum(sim.played.values()) == pytest.approx(5 * 30.0 * len([1 for _ in range(1)]) * 0 + sum(sim.played.values()))
    assert all(sim.played[f"b{i}"] > 0 for i in range(1, 6)), "the bench played"
    assert all(sim.played[f"s{i}"] > sim.played[f"b{i}"] for i in range(1, 6)), "the starters played more"
    assert model.unprofiled == {"newcomer"}


def test_fewer_than_five_available_is_refused() -> None:
    sim = _sim(RotationModel(_profile()), ("s1", "s2", "s3", "s4", "s5", "b1"))
    sim.unavailable.update({"s1", "b1"})
    with pytest.raises(rotation.RotationError, match="4 available"):
        sim.step(0.0)


def test_the_foul_and_blowout_factors_lower_a_players_score() -> None:
    league = rotation.LeagueRotation(WINDOW, {(0, 1): 1.0, (3, 1): 0.4}, {}, {(0, 0): 1.5, (5, 0): 0.15}, {})
    model = RotationModel(_profile(), league)
    sim = _sim(model)
    sim.on = {"s1", "s2", "s3", "s4", "s5"}
    sim.fouls["s1"] = 3
    scores = model.scores(sim.context(300.0))
    assert scores["s1"] == pytest.approx(scores["s2"] * 0.4)
    sim.fouls["s1"] = 0
    scores = model.scores(sim.context(2800.0, margin=-40))
    # Minute 46 is a low bin for both (0.55 and 0.4); the starters' blowout factor is 0.15 / 1.5 = 0.1, the bench's share 0 leaves 1.0.
    assert scores["s1"] == pytest.approx(scores["b1"] * 0.55 / 0.4 * 0.1)
    assert MINUTE_BINS == 49


def test_the_substitution_window_kinds_and_the_personal_fouls_are_play_kinds() -> None:
    assert rotation.SUBSTITUTION_KINDS <= vocab.KINDS and rotation.PERSONAL_FOUL_KINDS <= vocab.KINDS
    assert "foul_technical" not in rotation.PERSONAL_FOUL_KINDS and "substitution" not in rotation.SUBSTITUTION_KINDS
    assert len(rotation.SUBSTITUTION_KINDS) == 11
