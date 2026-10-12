"""The renderer on a toy game: every event becomes a line, free throws are
numbered within their trip, the box score sums, and the JSON round-trips."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import toy

from association.sim import render
from association.sim.engine import simulate
from association.sim.events import SimEvent, SimGame

_PLAYERS = {f"{t}{i}": f"Player {t}{i}" for t in ("H", "A") for i in range(10)} | {f"{t}-{i}": f"Player {t}-{i}" for t in ("home", "away") for i in range(1, 11)}
NAMES = render.Names(players=_PLAYERS, abbreviations={"H": "HOM", "A": "AWY", "home": "HOM", "away": "AWY"}, teams={"H": "Home Town", "A": "Away City", "home": "Home Town", "away": "Away City"})


def _game() -> SimGame:
    return simulate(toy.setup(), seed=3)


def _event(kind: str, outcome: str = "", actors: dict[str, str] | None = None, zone: str = "", clock: float = 300.0, period: int = 1, points: int = 0) -> SimEvent:
    return SimEvent(kind, period, clock, 720 - clock, "H", actors or {}, zone, outcome, points, 0, 0)


def test_clock_and_period_labels() -> None:
    assert render.clock_text(702.0) == "11:42"
    assert render.clock_text(60.0) == "1:00"
    assert render.clock_text(42.3) == "42.3"
    assert [render.period_label(p) for p in (1, 4, 5, 6)] == ["Q1", "Q4", "OT", "2OT"]


def test_describe_speaks_espn() -> None:
    assert render.describe(_event("shot", "made", {"shooter": "H1", "assister": "H2"}, "above_break_three"), NAMES) == "Player H1 makes three-pointer (Player H2 assists)"
    assert render.describe(_event("shot", "blocked", {"shooter": "H1", "blocker": "A3"}, "restricted"), NAMES) == "Player A3 blocks Player H1's layup"
    assert render.describe(_event("shot", "missed", {"shooter": "H1"}, "midrange"), NAMES) == "Player H1 misses mid-range jumper"
    assert render.describe(_event("free_throw", "made", {"shooter": "H1"}), NAMES, free_throw=(2, 2)) == "Player H1 makes free throw 2 of 2"
    assert render.describe(_event("rebound", "defensive", {}), NAMES) == "Home Town defensive team rebound"
    assert render.describe(_event("rebound", "offensive", {"rebounder": "H5"}), NAMES) == "Player H5 offensive rebound"
    assert render.describe(_event("turnover", "live", {"player": "H1", "stealer": "A1"}), NAMES) == "Player H1 turnover (Player A1 steals)"
    assert render.describe(_event("foul", "shooting", {"fouler": "A2", "fouled": "H1"}, "paint"), NAMES) == "Player A2 shooting foul (Player H1 draws the foul)"
    assert render.describe(_event("foul", "non_shooting", {"fouler": "A2"}), NAMES) == "Player A2 personal foul"
    assert render.describe(_event("foul_out", "", {"player": "A2"}), NAMES) == "Player A2 fouls out"
    assert render.describe(_event("substitution", "", {"in": "H6", "out": "H1"}), NAMES) == "Player H6 enters the game for Player H1"
    assert render.describe(_event("period_start", period=5), NAMES) == "Start of the Overtime"
    assert render.describe(_event("period_end", period=4), NAMES, last_period=True) == "End of Game"
    assert render.describe(_event("possession", "after_made"), NAMES) is None
    assert render.describe(_event("shot", "made", {"shooter": "zzz"}, "paint"), render.Names()) == "zzz makes shot in the paint"


def test_free_throws_are_numbered_within_their_trip_across_a_substitution() -> None:
    events = (
        _event("foul", "shooting", {"fouler": "A2", "fouled": "H1"}),
        _event("free_throw", "made", {"shooter": "H1"}),
        _event("substitution", "", {"in": "A6", "out": "A2"}),
        _event("free_throw", "missed", {"shooter": "H1"}),
        _event("rebound", "defensive", {"rebounder": "A5"}),
        _event("free_throw", "made", {"shooter": "A1"}, clock=200.0),
    )
    positions = render._free_throw_positions(events)
    assert positions == {1: (1, 2), 3: (2, 2), 5: (1, 1)}


def test_play_by_play_has_a_line_per_event_but_the_possession_markers() -> None:
    game = _game()
    text = render.play_by_play(game, NAMES)
    lines = text.split("\n")
    assert lines[0] == "AWY at HOM - simulated game, seed 3"
    assert len(lines) - 1 == sum(1 for e in game.events if e.kind != "possession")
    assert lines[1].startswith(" Q1 12:00  Start of the 1st Quarter")
    assert lines[-1].rstrip().endswith(f"AWY {game.away_score}-{game.home_score} HOM")
    assert "End of Game" in lines[-1]


def test_box_score_sums_to_the_final_and_marks_near_prior_players() -> None:
    game = _game()
    text = render.box_score(game, NAMES, near_prior=["home-9"])
    assert text.startswith(f"AWY {game.away_score}, HOM {game.home_score} (simulated, seed 3")
    home_block = text.split("Home Town (HOM)")[1]
    totals = next(line for line in home_block.split("\n") if line.startswith("Totals"))
    assert totals.split()[1:3] == ["240", str(game.home_score)]
    played_h9 = any(line.athlete_id == "home-9" and line.seconds > 0 for line in game.lines)
    assert ("Player home-9 *" in text) == played_h9
    assert ("* simulated at nearly the league prior" in text) == played_h9


def test_game_json_round_trips_with_a_schema_version() -> None:
    game = _game()
    payload = render.game_json(game, NAMES, {"window": "2024-2026 through 2026-04-13"})
    text = json.dumps(payload)
    back = json.loads(text)
    assert back["schema_version"] == render.SCHEMA_VERSION
    assert back["window"] == "2024-2026 through 2026-04-13"
    assert back["home"]["abbreviation"] == "HOM" and back["home"]["score"] == game.home_score
    assert len(back["events"]) == len(game.events)
    assert sum(e["points"] for e in back["events"]) == game.home_score + game.away_score
    assert all(line["seconds"] > 0 for line in back["lines"])
