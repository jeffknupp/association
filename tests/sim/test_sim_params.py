"""The parameter records: frozen, self-describing, and honest about how much
of each rate is prior."""

import dataclasses
import datetime as dt

import pytest

from association.sim import params


def _rate(value: float, trials: float = 500, prior: float | None = None, strength: float = 100) -> params.Rate:
    return params.Rate(value=value, trials=trials, prior=value if prior is None else prior, strength=strength)


def _player(trials: float = 500) -> params.PlayerParams:
    return params.PlayerParams(
        athlete_id="3934672",
        name="Jalen Brunson",
        team_id="18",
        minutes=2400.0,
        shots_per_minute=_rate(0.5, trials),
        free_throw_trips_per_minute=_rate(0.12, trials),
        turnovers_per_minute=_rate(0.07, trials),
        zone_mix=params.Mix(dict.fromkeys(params.ZONES, 0.2), trials, 80),
        make_rate={z: _rate(0.45, trials) for z in params.ZONES},
        and_one_rate=_rate(0.04, trials),
        assisted_rate=_rate(0.4, trials),
        free_throw_rate=_rate(0.84, trials),
        live_ball_share=_rate(0.6, trials),
        assists_per_minute=_rate(0.2, trials),
        offensive_rebounds_per_minute=_rate(0.02, trials),
        defensive_rebounds_per_minute=_rate(0.08, trials),
        steals_per_minute=_rate(0.03, trials),
        blocks_per_minute=_rate(0.01, trials),
        fouls_per_minute=_rate(0.06, trials),
    )


def test_a_rate_knows_how_much_of_it_is_prior() -> None:
    r = params.Rate(value=0.38, trials=100, prior=0.36, strength=100)
    assert r.prior_weight == pytest.approx(0.5)
    assert not r.near_prior
    assert params.Rate(value=0.36, trials=0, prior=0.36, strength=100).prior_weight == 1.0
    assert params.Rate(value=0.36, trials=99, prior=0.36, strength=100).near_prior
    assert params.Rate(value=0.36, trials=0, prior=0.36, strength=0).prior_weight == 1.0


def test_a_mix_answers_for_a_category_it_lacks_with_zero() -> None:
    m = params.Mix({"restricted": 0.6, "midrange": 0.4}, trials=50, strength=20)
    assert m.probability("restricted") == 0.6
    assert m.probability("corner_three") == 0.0
    assert m.near_prior


def test_the_window_describes_itself() -> None:
    assert params.Window((2024, 2025, 2026), dt.date(2026, 4, 13)).describe() == "2024-2026 through 2026-04-13, half-life 180 days"
    assert params.Window((2024, 2025, 2026), dt.date(2026, 4, 13), half_life_days=None).describe() == "2024-2026 through 2026-04-13"
    assert params.Window((2026,), dt.date(2026, 1, 1), half_life_days=None).describe() == "2026 through 2026-01-01"


def test_the_window_weights_each_game_by_its_age() -> None:
    window = params.Window((2024, 2025, 2026), dt.date(2026, 4, 13))
    assert window.half_life_days == params.HALF_LIFE_DAYS == 180.0
    assert window.weight(dt.date(2026, 4, 12)) == pytest.approx(0.5 ** (1 / 180))
    assert window.weight(dt.date(2026, 4, 13) - dt.timedelta(days=180)) == pytest.approx(0.5)
    assert window.weight(dt.date(2026, 4, 13) - dt.timedelta(days=360)) == pytest.approx(0.25)
    assert params.Window((2026,), dt.date(2026, 4, 13), half_life_days=None).weight(dt.date(2024, 1, 1)) == 1.0
    assert window.weight_sql("d") == "power(0.5, datediff('day', d, DATE '2026-04-13') / 180.0)"
    assert params.Window((2026,), dt.date(2026, 4, 13), half_life_days=None).weight_sql("d") == "1.0"


def test_a_player_record_is_frozen_and_reports_near_prior_from_his_shots() -> None:
    p = _player()
    assert not p.near_prior
    assert _player(trials=20).near_prior
    assert p.make_rate_in("corner_three").value == 0.45
    with pytest.raises(KeyError):
        p.make_rate_in("parking_lot")
    with pytest.raises(dataclasses.FrozenInstanceError):
        p.minutes = 0  # type: ignore[misc]
    # Every usage and event rate is there to be read by the engine.
    assert p.shots_per_minute.value + p.free_throw_trips_per_minute.value + p.turnovers_per_minute.value == pytest.approx(0.69)
    assert (p.and_one_rate.value, p.assisted_rate.value, p.free_throw_rate.value, p.live_ball_share.value) == (0.04, 0.4, 0.84, 0.6)
    assert (p.assists_per_minute.value, p.offensive_rebounds_per_minute.value, p.defensive_rebounds_per_minute.value) == (0.2, 0.02, 0.08)
    assert (p.steals_per_minute.value, p.blocks_per_minute.value, p.fouls_per_minute.value) == (0.03, 0.01, 0.06)
    assert (p.athlete_id, p.name, p.team_id, p.minutes) == ("3934672", "Jalen Brunson", "18", 2400.0)
    assert p.zone_mix.probability("paint") == pytest.approx(0.2)


def test_team_and_league_records_hold_the_defense_and_the_priors() -> None:
    team = params.TeamParams(
        team_id="18",
        name="New York Knicks",
        pace=_rate(98.5),
        allowed_make_rate={z: _rate(0.44) for z in params.ZONES},
        forced_turnover_rate=_rate(0.13),
        foul_rate=_rate(0.2),
        defensive_rebound_rate=_rate(0.72),
        allowed_zone_mix=params.Mix(dict.fromkeys(params.ZONES, 0.2), 5000, 80),
    )
    assert team.allowed_make_rate["restricted"].value == 0.44
    assert (team.pace.value, team.forced_turnover_rate.value, team.foul_rate.value, team.defensive_rebound_rate.value) == (98.5, 0.13, 0.2, 0.72)
    assert team.allowed_zone_mix.probability("midrange") == pytest.approx(0.2)
    assert (team.team_id, team.name) == ("18", "New York Knicks")
    league = params.LeagueParams(
        window=params.Window((2026,), dt.date(2026, 4, 13)),
        pace=99.0,
        make_rate=dict.fromkeys(params.ZONES, 0.45),
        zone_mix=dict.fromkeys(params.ZONES, 0.2),
        and_one_rate=0.04,
        assisted_rate=0.6,
        free_throw_rate=0.78,
        live_ball_share=0.55,
        turnover_rate=0.14,
        foul_rate=0.21,
        offensive_rebound_rate=0.27,
        home_court=2.5,
        strengths={"make_rate.restricted": 320.0},
    )
    assert league.strength("make_rate.restricted") == 320.0
    assert league.strength("free_throw_rate") == params.DEFAULT_STRENGTH
    assert league.window.describe() == "2026 through 2026-04-13, half-life 180 days"
    assert (league.pace, league.and_one_rate, league.assisted_rate, league.free_throw_rate) == (99.0, 0.04, 0.6, 0.78)
    assert (league.live_ball_share, league.turnover_rate, league.foul_rate, league.offensive_rebound_rate, league.home_court) == (0.55, 0.14, 0.21, 0.27, 2.5)
    assert league.make_rate["paint"] == 0.45 and league.zone_mix["paint"] == 0.2


def test_zone_values() -> None:
    assert [params.value_of(z) for z in params.ZONES] == [2, 2, 2, 3, 3]
    assert params.value_of(params.HEAVE) == 3
    assert params.value_of(params.UNKNOWN_THREE) == 3
    assert params.value_of(params.UNKNOWN_TWO) == 2


class _Starters:
    """A policy that keeps whoever is on the floor: the shape the engine ships."""

    def lineup(self, context: params.RotationContext) -> tuple[str, ...]:
        return context.on_floor()


def test_the_rotation_contract_is_a_five_from_the_context() -> None:
    players = tuple(params.PlayerGameState(athlete_id=f"p{i}", on_floor=i < 5, seconds_played=0.0, stint_seconds=0.0, fouls=0, available=True) for i in range(9))
    context = params.RotationContext(team_id="18", period=2, clock_remaining=480.0, elapsed=960.0, margin=-3, players=players)
    policy: params.RotationPolicy = _Starters()
    assert policy.lineup(context) == ("p0", "p1", "p2", "p3", "p4")
    assert context.on_floor() == ("p0", "p1", "p2", "p3", "p4")
    assert (context.team_id, context.period, context.clock_remaining, context.elapsed, context.margin) == ("18", 2, 480.0, 960.0, -3)
    assert (players[0].seconds_played, players[0].stint_seconds, players[0].fouls, players[0].available) == (0.0, 0.0, 0, True)
