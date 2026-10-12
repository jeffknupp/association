"""The estimator on a hand-built warehouse of three games: two in the window
(one of them played the evening before ``as_of`` by the Eastern clock, so
the UTC date alone would exclude it) and one on the ``as_of`` day, which is
excluded. Every expectation below is computed from the fixture's own rows,
so a rate's prior, trials and shrinkage are checked against hand-countable
numbers.

Home team H against A in game 601 (H's h1 hits a corner three assisted by
h2, misses a layup, scores an and-one from the paint; h2 a mid-range
jumper; a1 misses a three, takes a two-shot trip, turns it over twice, once
live; a2 heaves at the buzzer), A at home in game 602 (h1 an assisted
layup, h3 two restricted makes - the two-shot player - a1 a corner three, A
a shot-clock turnover nobody's), and game 603 on the ``as_of`` day, where h1
takes 100 shots nobody should count.
"""

from __future__ import annotations

import datetime as dt
from collections import Counter
from collections.abc import Iterator
from typing import Any

import duckdb
import pyarrow as pa
import pytest

from association.sim import database, plays, possessions, rates, shrink
from association.sim.params import DURATION_BINS, DURATION_KEYS, ZONES, Window

SEASON = 2025
AS_OF = dt.date(2025, 1, 12)

GAMES = [
    {"event_id": "601", "season": SEASON, "season_type": 2, "date": "2025-01-10T00:30Z", "home_team_id": "H", "away_team_id": "A", "home_score": 8, "away_score": 1},
    # 00:30Z on the 12th is the evening of the 11th in the East: inside a window as of the 12th.
    {"event_id": "602", "season": SEASON, "season_type": 2, "date": "2025-01-12T00:30Z", "home_team_id": "A", "away_team_id": "H", "home_score": 3, "away_score": 6},
    # Played on the as_of day: outside the window.
    {"event_id": "603", "season": SEASON, "season_type": 2, "date": "2025-01-12T19:00Z", "home_team_id": "H", "away_team_id": "A", "home_score": 2, "away_score": 0},
]


def _play(event: str, pid: int, clock: str, team: str | None, athlete: str | None, parts: str | None, type_: str, text: str, scoring: bool = False) -> dict[str, Any]:
    return {
        "event_id": event,
        "play_id": f"{event}{pid:02d}",
        "season": SEASON,
        "season_type": 2,
        "period": 1,
        "clock": clock,
        "team_id": team,
        "athlete_id": athlete,
        "participant_athlete_ids": parts,
        "type": type_,
        "text": text,
        "home_score": 0,
        "away_score": 0,
        "scoring_play": scoring,
    }


PLAYS = [
    _play("601", 1, "12:00", "H", "h1", "h1,a1,h2", "Jumpball", "h1 vs. a1 (h2 gains possession)"),
    _play("601", 2, "11:30", "H", "h1", "h1,h2", "Jump Shot", "h1 makes 23-foot three point jumper (h2 assists)", True),
    _play("601", 3, "11:00", "H", "h1", "h1", "Layup Shot", "h1 misses layup"),
    _play("601", 4, "10:59", "A", "a1", "a1", "Defensive Rebound", "a1 defensive rebound"),
    _play("601", 5, "10:30", "H", "h1", "h1", "Jump Shot", "h1 makes 10-foot jumper", True),
    _play("601", 6, "10:30", "A", "a1", "a1", "Shooting Foul", "a1 shooting foul"),
    _play("601", 7, "10:30", "H", "h1", "h1", "Free Throw - 1 of 1", "h1 makes free throw 1 of 1", True),  # the and-one
    _play("601", 8, "10:00", "H", "h2", "h2", "Jump Shot", "h2 makes 16-foot jumper", True),
    _play("601", 9, "9:30", "A", "a1", "a1", "Jump Shot", "a1 misses 26-foot three point jumper"),
    _play("601", 10, "9:00", "H", "h2", "h2", "Shooting Foul", "h2 shooting foul"),
    _play("601", 11, "9:00", "A", "a1", "a1", "Free Throw - 1 of 2", "a1 makes free throw 1 of 2", True),
    _play("601", 12, "9:00", "A", "a1", "a1", "Free Throw - 2 of 2", "a1 misses free throw 2 of 2"),
    # A bonus trip: a non-shooting foul in the penalty, then two free throws. The engine's
    # non-shooting-foul path awards these, so the estimator must NOT count it as a1's trip.
    _play("601", 16, "8:45", "H", "h2", "h2", "Personal Foul", "h2 personal foul"),
    _play("601", 17, "8:45", "A", "a1", "a1", "Free Throw - 1 of 2", "a1 makes free throw 1 of 2", True),
    _play("601", 18, "8:45", "A", "a1", "a1", "Free Throw - 2 of 2", "a1 makes free throw 2 of 2", True),
    _play("601", 13, "8:30", "A", "a1", "a1,h1", "Bad Pass\nTurnover", "a1 bad pass (h1 steals)"),  # live
    _play("601", 14, "8:00", "A", "a1", "a1", "Traveling", "a1 traveling"),  # dead
    _play("601", 15, "0:01", "A", "a2", "a2", "Jump Shot", "a2 misses 40-foot three point jumper"),  # the heave
    _play("601", 16, "0.0", None, None, None, "End Period", "End of the 1st Quarter"),
    _play("601", 17, "0.0", None, None, None, "End Game", "End of Game"),
    _play("602", 1, "11:00", "H", "h1", "h1,h2", "Layup Shot", "h1 makes layup (h2 assists)", True),
    _play("602", 2, "10:00", "H", "h3", "h3", "Layup Shot", "h3 makes layup", True),
    _play("602", 3, "9:00", "H", "h3", "h3", "Layup Shot", "h3 makes layup", True),
    _play("602", 4, "8:00", "A", "a1", "a1", "Jump Shot", "a1 makes 23-foot three point jumper", True),
    _play("602", 5, "7:00", "A", None, None, "Shot Clock Turnover", "A shot clock turnover"),  # a team turnover, nobody's
    _play("602", 6, "0.0", None, None, None, "End Period", "End of the 1st Quarter"),
    _play("602", 7, "0.0", None, None, None, "End Game", "End of Game"),
    _play("603", 1, "11:00", "H", "h1", "h1", "Jump Shot", "h1 makes 16-foot jumper", True),
    _play("603", 2, "0.0", None, None, None, "End Game", "End of Game"),
]


def _shot(event: str, pid: int, athlete: str, team: str, parts: str, made: bool, value: int, x: int, y: int, shot_type: str = "Jump Shot") -> dict[str, Any]:
    return {
        "event_id": event,
        "play_id": f"{event}{pid:02d}",
        "season": SEASON,
        "season_type": 2,
        "athlete_id": athlete,
        "participant_athlete_ids": parts,
        "team_id": team,
        "period": 1,
        "clock": "0:00",
        "made": made,
        "shot_type": shot_type,
        "points_attempted": value,
        "coordinate_x": x,
        "coordinate_y": y,
        "description": "",
    }


SHOTS = [
    _shot("601", 2, "h1", "H", "h1,h2", True, 3, 2, 3),  # corner three, assisted
    _shot("601", 3, "h1", "H", "h1", False, 2, 25, 1),  # restricted, missed
    _shot("601", 5, "h1", "H", "h1", True, 2, 25, 10),  # paint, the and-one
    _shot("601", 7, "h1", "H", "h1", True, 1, 25, 1, "Free Throw - 1 of 1"),  # a free throw: no zone
    _shot("601", 8, "h2", "H", "h2", True, 2, 25, 16),  # midrange
    _shot("601", 9, "a1", "A", "a1", False, 3, 25, 26),  # above the break, missed
    _shot("601", 15, "a2", "A", "a2", False, 3, 25, 40),  # the heave
    _shot("602", 1, "h1", "H", "h1,h2", True, 2, 25, 2),  # restricted, assisted
    _shot("602", 2, "h3", "H", "h3", True, 2, 24, 1),  # restricted
    _shot("602", 3, "h3", "H", "h3", True, 2, 25, 3),  # restricted
    _shot("602", 4, "a1", "A", "a1", True, 3, 48, 2),  # the other corner
    _shot("603", 1, "h1", "H", "h1", True, 2, 25, 16),  # outside the window
]

# (event, team, athlete, minutes, fga, fta, ftm, tov, ast, oreb, dreb, stl, blk, pf); None minutes is did not play.
BOX: list[tuple[str, str, str, int | None, int, int, int, int, int, int, int, int, int, int]] = [
    ("601", "H", "h1", 36, 3, 1, 1, 0, 0, 0, 2, 1, 0, 1),
    ("601", "H", "h2", 36, 1, 0, 0, 1, 1, 1, 3, 0, 1, 2),
    ("601", "H", "h3", 12, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0),
    ("601", "H", "h4", 48, 4, 2, 1, 1, 2, 1, 4, 1, 0, 3),
    ("601", "H", "h5", 48, 2, 1, 1, 1, 0, 0, 2, 0, 1, 1),
    ("601", "H", "h6", 60, 0, 0, 0, 0, 1, 0, 1, 0, 0, 0),
    ("601", "H", "h9", None, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0),
    ("601", "A", "a1", 36, 1, 2, 1, 2, 0, 0, 1, 0, 0, 1),
    ("601", "A", "a2", 36, 1, 0, 0, 0, 0, 1, 2, 1, 0, 0),
    ("601", "A", "a3", 48, 5, 0, 0, 0, 1, 2, 3, 0, 0, 2),
    ("601", "A", "a4", 48, 3, 2, 2, 1, 0, 0, 2, 0, 1, 1),
    ("601", "A", "a5", 72, 2, 0, 0, 0, 0, 0, 1, 0, 0, 0),
    ("601", "A", "a9", None, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0),
    ("602", "H", "h1", 30, 1, 0, 0, 1, 0, 0, 1, 0, 0, 0),
    ("602", "H", "h2", 30, 0, 0, 0, 0, 1, 0, 1, 0, 0, 1),
    ("602", "H", "h3", 12, 2, 0, 0, 0, 0, 1, 0, 0, 0, 0),
    ("602", "H", "h4", 48, 3, 0, 0, 0, 0, 0, 2, 1, 0, 2),
    ("602", "H", "h5", 48, 1, 0, 0, 1, 0, 0, 1, 0, 0, 0),
    ("602", "H", "h6", 72, 2, 0, 0, 0, 0, 1, 2, 0, 1, 1),
    ("602", "A", "a1", 36, 1, 0, 0, 0, 0, 0, 1, 1, 0, 0),
    ("602", "A", "a2", 36, 2, 0, 0, 1, 0, 0, 1, 0, 0, 1),
    ("602", "A", "a3", 48, 1, 0, 0, 0, 0, 1, 2, 0, 0, 0),
    ("602", "A", "a4", 48, 2, 2, 1, 0, 0, 0, 1, 0, 0, 1),
    ("602", "A", "a5", 72, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0),
    ("602", "A", "a9", None, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0),
    ("603", "H", "h1", 48, 100, 0, 0, 0, 0, 0, 0, 0, 0, 0),
    ("603", "H", "h9", 48, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0),
    ("603", "A", "a1", 48, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0),
]

_COLUMNS = ("fieldGoalsAttempted", "freeThrowsAttempted", "freeThrowsMade", "turnovers", "assists", "offensiveRebounds", "defensiveRebounds", "steals", "blocks", "fouls")


def _box_rows() -> list[dict[str, Any]]:
    rows = []
    for event, team, athlete, minutes, *stats in BOX:
        row: dict[str, Any] = {"event_id": event, "season": SEASON, "season_type": 2, "team_id": team, "athlete_id": athlete, "starter": False, "minutes": minutes, "did_not_play": minutes is None}
        row.update(zip(_COLUMNS, stats, strict=True))
        rows.append(row)
    return rows


def _in_window(event: str) -> bool:
    return event in ("601", "602")


def _box_total(column: int, athlete: str | None = None, team: str | None = None) -> int:
    """A box column summed over the window's games, for one player, one
    team or everyone. ``column`` indexes the BOX tuple (3 is minutes)."""
    return sum(int(r[column] or 0) for r in BOX if _in_window(r[0]) and r[3] is not None and (athlete is None or r[2] == athlete) and (team is None or r[1] == team))


@pytest.fixture(scope="module")
def con(tmp_path_factory: pytest.TempPathFactory) -> Iterator[duckdb.DuckDBPyConnection]:
    root = tmp_path_factory.mktemp("rates")
    wh = duckdb.connect(str(root / "nba.duckdb"))
    tables: list[tuple[str, list[dict[str, Any]]]] = [
        ("plays", PLAYS),
        ("shot_chart", SHOTS),
        ("real_games", GAMES),
        ("player_box_stats_deduped", _box_rows()),
        ("players", [{"athlete_id": "h1", "display_name": "Hal One"}, {"athlete_id": "a1", "display_name": "Al One"}]),
        ("teams", [{"team_id": "H", "display_name": "Home Hosts"}, {"team_id": "A", "display_name": "Away Guests"}]),
    ]
    for name, rows in tables:
        wh.register("rows", pa.Table.from_pylist(rows))
        wh.execute(f"CREATE TABLE {name} AS SELECT * FROM rows")
        wh.unregister("rows")
    wh.close()
    c = database.connect(root / "sim.duckdb", root / "nba.duckdb")
    plays.build_plays(c)
    possessions.build_possessions(c)
    yield c
    c.close()


@pytest.fixture(scope="module")
def estimates(con: duckdb.DuckDBPyConnection) -> rates.Estimates:
    return rates.estimate(con, Window(seasons=(SEASON,), as_of=AS_OF))


def _check_rate(rate: Any, successes: float, trials: float, prior: float) -> None:
    """The rate is the fixture's count shrunk toward the fixture's league
    mean by its own fitted strength, and lies between the raw rate and the
    prior."""
    assert rate.trials == trials
    assert rate.prior == pytest.approx(prior)
    assert rate.value == pytest.approx(shrink.shrink(successes, trials, prior, rate.strength))
    raw = successes / trials if trials else prior
    assert min(raw, prior) - 1e-9 <= rate.value <= max(raw, prior) + 1e-9


def test_the_window_is_the_eastern_date_strictly_before_as_of(estimates: rates.Estimates) -> None:
    h1 = estimates.players["h1"]
    assert h1.minutes == 66  # 36 in 601 and 30 in 602 (00:30Z on the 12th is the 11th in the East); 603's 48 are on the as_of day
    assert h1.shots_per_minute.trials == 66
    assert h1.name == "Hal One"
    assert h1.team_id == "H"
    assert estimates.players["h3"].name == "h3"  # a player the players table lacks keeps his id


def test_the_league_means_are_the_fixtures_sums(estimates: rates.Estimates) -> None:
    league = estimates.league
    minutes = _box_total(3)
    assert league.per_minute_rate("shots_per_minute") == pytest.approx(_box_total(4) / minutes)
    assert league.per_minute_rate("fouls_per_minute") == pytest.approx(_box_total(13) / minutes)
    assert set(league.per_minute) == rates.PER_MINUTE_RATES
    with pytest.raises(KeyError):
        league.per_minute_rate("pace")  # a per-possession figure has no per-minute prior
    assert league.free_throw_rate == pytest.approx(_box_total(6) / _box_total(5))
    # 11 charted field goals in the window less the heave: 4 restricted (3 made), 1 paint, 1 midrange, 2 corner (both made), 1 above the break (missed)
    assert league.zone_mix == pytest.approx({"restricted": 4 / 9, "paint": 1 / 9, "midrange": 1 / 9, "corner_three": 2 / 9, "above_break_three": 1 / 9})
    assert league.make_rate["restricted"] == pytest.approx(3 / 4)
    assert league.make_rate["corner_three"] == pytest.approx(1.0)  # both corners went in
    assert league.make_rate["above_break_three"] == 0.0
    assert league.and_one_rate == pytest.approx(1 / 7)  # seven makes outside the heave, one followed by a 1-of-1 at its clock
    assert league.assisted_rate == pytest.approx(2 / 7)
    assert league.live_ball_share == pytest.approx(1 / 2)  # a1's bad pass and traveling
    assert league.home_court == pytest.approx(((8 - 1) + (3 - 6)) / 2)
    assert set(league.strengths) == {name for name, _, _ in rates.PLAYER_RATES} | {name for name, _, _ in rates.TEAM_RATES} | {"zone_mix", "allowed_zone_mix"}


def test_a_players_rates_are_his_counts_shrunk_toward_the_league(estimates: rates.Estimates) -> None:
    league, h1 = estimates.league, estimates.players["h1"]
    _check_rate(h1.shots_per_minute, 4, 66, league.per_minute["shots_per_minute"])
    _check_rate(h1.free_throw_trips_per_minute, 0, 66, league.per_minute["free_throw_trips_per_minute"])  # the 1-of-1 is an and-one, not a trip
    _check_rate(h1.turnovers_per_minute, 1, 66, league.per_minute["turnovers_per_minute"])
    _check_rate(h1.make_rate_in("restricted"), 1, 2, 3 / 4)
    _check_rate(h1.make_rate_in("corner_three"), 1, 1, 1.0)
    _check_rate(h1.make_rate_in("midrange"), 0, 0, league.make_rate["midrange"])  # no attempts: the prior
    _check_rate(h1.and_one_rate, 1, 3, 1 / 7)
    _check_rate(h1.assisted_rate, 2, 3, 2 / 7)
    _check_rate(h1.free_throw_rate, 1, 1, league.free_throw_rate)
    assert h1.zone_mix.trials == 4
    assert sum(h1.zone_mix.probabilities.values()) == pytest.approx(1.0)
    assert set(h1.zone_mix.probabilities) == set(ZONES)
    a1 = estimates.players["a1"]
    _check_rate(a1.live_ball_share, 1, 2, 1 / 2)
    _check_rate(a1.free_throw_trips_per_minute, 1, 72, league.per_minute["free_throw_trips_per_minute"])  # the shooting-foul 1-of-2 is one trip; the bonus trip at 8:45 is not


def test_a_two_shot_player_is_near_the_prior_and_shrunk_toward_it(estimates: rates.Estimates) -> None:
    h3 = estimates.players["h3"]
    assert h3.near_prior
    restricted = h3.make_rate_in("restricted")
    assert restricted.trials == 2
    assert restricted.prior == pytest.approx(3 / 4)
    assert 3 / 4 < restricted.value < 1.0  # two of two made, pulled back toward the league's three of four
    assert h3.zone_mix.probability("restricted") < 1.0  # two restricted shots do not make a one-zone shooter
    assert h3.zone_mix.probability("above_break_three") > 0.0


def test_a_player_with_no_minutes_in_the_window_is_missing(estimates: rates.Estimates) -> None:
    assert "a9" in estimates.missing  # did not play in either game
    assert "h9" in estimates.missing  # played only on the as_of day
    assert "a9" not in estimates.players
    assert "h1" not in estimates.missing


def test_as_of_moves_the_window(con: duckdb.DuckDBPyConnection) -> None:
    later = rates.estimate(con, Window(seasons=(SEASON,), as_of=dt.date(2025, 1, 13)))
    assert later.players["h1"].minutes == 114
    assert later.players["h1"].shots_per_minute.trials == 114
    assert "h9" not in later.missing
    assert later.players["h9"].minutes == 48
    early = rates.estimate(con, Window(seasons=(SEASON,), as_of=dt.date(2025, 1, 10)))
    assert early.players["h1"].minutes == 36  # 601 alone: 00:30Z on the 10th is the evening of the 9th


def test_team_rates_are_the_box_possession_formula_and_the_opponents_counts(estimates: rates.Estimates) -> None:
    league, home = estimates.league, estimates.teams["H"]
    assert home.name == "Home Hosts"

    def poss(team: str) -> float:
        team_tov = 1 if team == "A" else 0  # A's shot-clock turnover in 602
        return _box_total(4, team=team) - _box_total(9, team=team) + _box_total(7, team=team) + team_tov + 0.44 * _box_total(5, team=team)

    games48 = _box_total(3, team="H") / 240
    assert home.pace.trials == pytest.approx(games48)
    assert home.pace.prior == pytest.approx((poss("H") + poss("A")) / (games48 + _box_total(3, team="A") / 240))
    assert league.pace == home.pace.prior
    _check_rate(home.forced_turnover_rate, _box_total(7, team="A") + 1, poss("A"), league.turnover_rate)
    _check_rate(home.foul_rate, 1, poss("A"), league.foul_rate)  # a1's one trip against H
    opp_misses = _box_total(10, team="H") + _box_total(9, team="A")
    _check_rate(home.defensive_rebound_rate, _box_total(10, team="H"), opp_misses, 1 - league.offensive_rebound_rate)
    _check_rate(home.allowed_make_rate["corner_three"], 1, 1, league.make_rate["corner_three"])  # a1's corner three in 602
    _check_rate(home.allowed_make_rate["above_break_three"], 0, 1, league.make_rate["above_break_three"])
    assert home.allowed_zone_mix.trials == 2  # the heave is nobody's zone
    away = estimates.teams["A"]
    assert away.allowed_zone_mix.trials == 7


def test_requested_ids_choose_the_records_and_the_missing(con: duckdb.DuckDBPyConnection) -> None:
    only_home = rates.estimate(con, Window(seasons=(SEASON,), as_of=AS_OF), team_ids=["H", "Z"], athlete_ids=["a1", "nobody"])
    assert set(only_home.teams) == {"H"}
    assert "Z" in only_home.missing
    assert "nobody" in only_home.missing
    assert "h9" in only_home.missing
    assert "a9" not in only_home.missing  # A was not asked about
    assert set(only_home.players) == {"h1", "h2", "h3", "h4", "h5", "h6", "a1"}
    # The league and the strengths are the whole window's, whoever was asked for.
    whole = rates.estimate(con, Window(seasons=(SEASON,), as_of=AS_OF))
    assert only_home.league == whole.league


def test_as_of_after_is_the_day_after_the_last_eastern_game_date(con: duckdb.DuckDBPyConnection) -> None:
    assert rates.as_of_after(con, SEASON) == dt.date(2025, 1, 13)  # 603 is 19:00Z on the 12th, the 12th in the East
    with pytest.raises(ValueError, match="no games"):
        rates.as_of_after(con, 1999)


def test_the_totals_weight_an_earlier_season_by_the_windows_decay() -> None:
    counts: rates.Counts = {("a1", 2026, 0): Counter(fga=10, minutes=20), ("a1", 2026, 1): Counter(fga=10), ("a1", 2025, 0): Counter(fga=40, minutes=20), ("t", 2024, 1): Counter(poss=100)}
    window = Window(seasons=(2024, 2025, 2026), as_of=dt.date(2026, 4, 13))
    totals = rates._totals(counts, window)
    assert dict(totals["a1"]) == {"fga": 40.0, "minutes": 30.0}  # 20 + 0.5 * 40, 20 + 0.5 * 20
    assert dict(totals["t"]) == {"poss": 25.0}
    players, teams = rates._league_totals({k: v for k, v in counts.items() if k[0] == "a1"}, {k: v for k, v in counts.items() if k[0] == "t"}, window)
    assert dict(players) == {"fga": 40.0, "minutes": 30.0} and dict(teams) == {"poss": 25.0}
    # Equal weights leave the counts alone.
    assert dict(rates._totals(counts, Window(seasons=(2024, 2025, 2026), as_of=dt.date(2026, 4, 13), season_decay=1.0))["a1"]) == {"fga": 60, "minutes": 40}


def test_the_duration_histograms_are_shares_over_the_bins_and_the_first_use_seconds_a_team_rate(estimates: rates.Estimates) -> None:
    league = estimates.league
    assert set(league.durations) <= set(DURATION_KEYS)
    for key, shares in league.durations.items():
        assert len(shares) == DURATION_BINS and sum(shares) == pytest.approx(1.0) and min(shares) >= 0.0, key
    # The fixture's possessions give the league a mean time to the first use and every team a shrunk rate toward it.
    assert league.first_use_seconds >= 0.0
    for team in estimates.teams.values():
        assert team.first_use_seconds.prior == pytest.approx(league.first_use_seconds)
        assert 0.0 <= team.first_use_seconds.value <= 24.0 * 60


def test_the_usage_share_is_unknown_without_segments(estimates: rates.Estimates) -> None:
    # The fixture builds plays and possessions but no segments, so no lineup can be read: every share is zero and the engine falls back to the per-minute sum.
    assert all(p.usage_share.value == 0.0 and p.usage_share.trials == 0 for p in estimates.players.values())
