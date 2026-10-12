"""The game module's pieces that need no estimator: team resolution and the
real-game lookup on a fixture warehouse, the quantiles, the summary over toy
games and its text and JSON."""

from __future__ import annotations

import datetime as dt
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
import pytest

sys.path.insert(0, str(Path(__file__).parent))

import toy

from association.sim import database, game, rotation
from association.sim.engine import simulate
from association.sim.params import Window
from association.sim.render import Names

_TEAMS = [
    {"team_id": "2", "abbreviation": "BOS", "name": "Celtics", "nickname": "Boston", "location": "Boston", "display_name": "Boston Celtics"},
    {"team_id": "18", "abbreviation": "NY", "name": "Knicks", "nickname": "New York", "location": "New York", "display_name": "New York Knicks"},
    {"team_id": "13", "abbreviation": "LAL", "name": "Lakers", "nickname": "Los Angeles", "location": "Los Angeles", "display_name": "Los Angeles Lakers"},
    {"team_id": "12", "abbreviation": "LAC", "name": "Clippers", "nickname": "Los Angeles", "location": "Los Angeles", "display_name": "LA Clippers"},
]
_GAMES = [
    {"event_id": "401", "season": 2025, "season_type": 2, "date": "2025-04-14T00:30Z", "home_team_id": "18", "away_team_id": "2", "home_score": 101, "away_score": 99},
    {"event_id": "402", "season": 2026, "season_type": 2, "date": "2026-01-02T02:00Z", "home_team_id": "2", "away_team_id": "18", "home_score": 110, "away_score": 90},
]


def _box(event: str, team: str, athlete: str, minutes: int | None, starter: bool) -> dict[str, Any]:
    return {"event_id": event, "season": 2025, "season_type": 2, "team_id": team, "athlete_id": athlete, "minutes": minutes, "starter": starter, "did_not_play": minutes is None}


_BOX = [_box("401", "18", f"h{i}", 30 if i < 5 else 12, i < 5) for i in range(8)] + [_box("401", "18", "h9", None, False)] + [_box("401", "2", f"a{i}", 25, i < 5) for i in range(7)]


@pytest.fixture
def con(tmp_path: Path) -> Iterator[duckdb.DuckDBPyConnection]:
    wh = duckdb.connect(str(tmp_path / "nba.duckdb"))
    for name, rows in (("teams", _TEAMS), ("real_games", _GAMES), ("player_box_stats_deduped", _BOX)):
        wh.register("rows", pa.Table.from_pylist(rows))
        wh.execute(f"CREATE TABLE {name} AS SELECT * FROM rows")
        wh.unregister("rows")
    wh.close()
    c = database.connect(tmp_path / "sim.duckdb", tmp_path / "nba.duckdb")
    yield c
    c.close()


def test_resolve_team_by_abbreviation_name_city_or_full_name(con: duckdb.DuckDBPyConnection) -> None:
    assert game.resolve_team(con, "bos") == "2"
    assert game.resolve_team(con, "Knicks") == "18"
    assert game.resolve_team(con, "new york") == "18"
    assert game.resolve_team(con, "Los Angeles Lakers") == "13"
    with pytest.raises(game.SimulationRefused, match="abbreviations on record: BOS, LAC, LAL, NY"):
        game.resolve_team(con, "Sonics")
    with pytest.raises(game.SimulationRefused, match="names 2 teams"):
        game.resolve_team(con, "Los Angeles")


def test_latest_season_and_the_window_reach_back_three_seasons(con: duckdb.DuckDBPyConnection) -> None:
    assert game.latest_season(con) == 2026
    window = game.window_for(con, 2026, dt.date(2026, 3, 1))
    assert window.seasons == (2024, 2025, 2026) and window.as_of == dt.date(2026, 3, 1)
    # Without a date, the day after the season's last regular-season game, in Eastern terms.
    assert game.window_for(con, 2025).as_of == dt.date(2025, 4, 14)


def test_real_game_holds_the_players_who_appeared_and_the_box_five(con: duckdb.DuckDBPyConnection) -> None:
    real = game.real_game(con, "401")
    assert (real.home, real.away, real.home_score, real.away_score) == ("18", "2", 101, 99)
    assert real.date == dt.date(2025, 4, 13)  # 00:30Z is the evening before, Eastern
    assert real.rosters["18"] == ("h0", "h1", "h2", "h3", "h4", "h5", "h6", "h7")  # h9 did not play
    assert real.starters["18"] == ("h0", "h1", "h2", "h3", "h4")
    assert len(real.rosters["2"]) == 7
    with pytest.raises(game.SimulationRefused, match="no real game 999"):
        game.real_game(con, "999")


def test_quantiles_interpolate() -> None:
    assert game.quantiles([1, 2, 3, 4, 5]) == {"p10": 1.4, "p25": 2.0, "p50": 3.0, "p75": 4.0, "p90": 4.6}
    assert game.quantiles([], (0.5,)) == {"p50": 0.0}
    assert game.quantiles([7.0], (0.1, 0.9)) == {"p10": 7.0, "p90": 7.0}


def _prepared() -> game.Prepared:
    setup = toy.setup()
    h, a = setup.home.team.team_id, setup.away.team.team_id
    home = game.Roster(h, tuple(setup.home.players), tuple(setup.home.starters), "toy")
    away = game.Roster(a, tuple(setup.away.players), tuple(setup.away.starters), "toy")
    names = Names(players={p: f"Player {p}" for p in (*home.players, *away.players)}, abbreviations={h: "HOM", a: "AWY"}, teams={h: "Home Town", a: "Away City"})
    return game.Prepared(
        home,
        away,
        Window((2026,), dt.date(2026, 4, 13)),
        setup.league,
        {h: setup.home.team, a: setup.away.team},
        {**setup.home.players, **setup.away.players},
        {},
        rotation.NEUTRAL_LEAGUE,
        names,
        ("a note",),
    )


def test_summary_over_toy_games_and_its_text_and_json() -> None:
    prepared = _prepared()
    games = [simulate(toy.setup(), seed=s) for s in range(7, 19)]
    summary = game.summarize(games, prepared, 7)
    assert summary.n == 12 and summary.seed == 7
    assert 0 <= summary.home_win_probability <= 1
    assert summary.margin["p10"] <= summary.margin["p50"] <= summary.margin["p90"]
    assert abs(summary.home_points - sum(g.home_score for g in games) / 12) < 1e-9
    assert len(summary.players) == 20 and summary.players[0].team_id == prepared.away.team_id  # away side first, by minutes
    assert all(p.games <= 12 for p in summary.players)
    text = game.summary_text(summary, prepared.names)
    assert text.startswith("Away City at Home Town: 12 simulated games, seeds 7-18, rates from 2026 through 2026-04-13")
    assert "note: a note" in text and "HOM wins" in text and "--home-roster" in text
    payload = game.summary_json(summary, prepared.names)
    assert payload["schema_version"] == "sim.1" and payload["n"] == 12
    assert payload["home"]["roster"] == list(prepared.home.players) and payload["notes"] == ["a note"]
    assert {p["name"] for p in payload["players"]} == {f"Player {a}" for a in (*prepared.home.players, *prepared.away.players)}


def test_run_refuses_zero_games_and_prepared_near_prior_lists_the_rostered(con: duckdb.DuckDBPyConnection) -> None:
    prepared = _prepared()
    assert prepared.near_prior == ()
    with pytest.raises(game.SimulationRefused):
        game.run(prepared, 0, 1)
