"""Tracing: a traced game is the untraced game event for event, every draw is
recorded with its probabilities and inputs, and the explanation renders."""

from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import toy

from association.sim import render
from association.sim.engine import simulate
from association.sim.events import Decision

NAMES = render.Names(
    players={f"{t}-{i}": f"Player {t}-{i}" for t in ("home", "away") for i in range(1, 11)}, abbreviations={"home": "HOM", "away": "AWY"}, teams={"home": "Home Town", "away": "Away City"}
)


def test_tracing_changes_nothing_and_records_every_draw() -> None:
    plain = simulate(toy.setup(), seed=11)
    traced = simulate(toy.setup(), seed=11, trace=True)
    assert plain.events == traced.events and plain.lines == traced.lines
    assert plain.decisions == () and traced.decisions
    kinds = {d.kind for d in traced.decisions}
    assert {"duration", "user", "action", "zone", "make", "offensive_rebound", "rebounder", "free_throw", "fouler", "rotation"} <= kinds
    # Every decision points at an event, and every shot event has the make draw that produced it.
    assert all(0 <= d.event_index <= len(traced.events) for d in traced.decisions)
    by_event: dict[int, list[Decision]] = {}
    for d in traced.decisions:
        by_event.setdefault(d.event_index, []).append(d)
    shots = [i for i, e in enumerate(traced.events) if e.kind == "shot" and e.zone != "heave"]
    assert shots
    for i in shots:
        makes = [d for d in by_event[i] if d.kind == "make"]
        assert len(makes) == 1
        make = makes[0]
        assert make.chosen == ("yes" if traced.events[i].outcome == "made" else "no")
        assert set(make.inputs) == {"player", "defense", "league", "home"}
        assert 0.0 <= make.draw <= 1.0 and math.isclose(make.probabilities["yes"] + make.probabilities["no"], 1.0)
    # A weighted pick's probabilities sum to one over the five on the floor.
    users = [d for d in traced.decisions if d.kind == "user"]
    assert users and all(len(d.probabilities) == 5 and math.isclose(sum(d.probabilities.values()), 1.0) for d in users)
    assert all(math.isnan(d.draw) for d in users)


def test_explain_renders_each_kind() -> None:
    traced = simulate(toy.setup(), seed=5, trace=True)
    text = render.play_by_play(traced, NAMES, explain_decisions=True)
    assert "| make p=" in text and "odds(player" in text
    assert "| user: Player " in text and "-> Player " in text
    assert "| duration " in text and "the game's tempo" in text and "histogram mean" in text
    assert "| action: shot " in text
    assert "| rotation: " in text
    # The untraced game says so rather than printing nothing.
    plain = render.play_by_play(simulate(toy.setup(), seed=5), NAMES, explain_decisions=True)
    assert plain.endswith("(no decisions recorded: the game was not simulated with trace=True)")
    # Decisions ride the JSON.
    payload = render.game_json(traced, NAMES, {})
    assert len(payload["decisions"]) == len(traced.decisions) and payload["decisions"][0]["kind"] == traced.decisions[0].kind


def test_explain_formats_a_yes_no_and_a_pick() -> None:
    d = Decision(3, "make", "no", 0.62, {"yes": 0.352, "no": 0.648}, {"player": 0.36, "defense": 0.349, "league": 0.355, "home": 1.011})
    assert render.explain(d, NAMES) == "make p=0.352 = odds(player 0.360 x defense 0.349 / league 0.355) x home 1.011; draw 0.620 -> no"
    pick = Decision(3, "rebounder", "home-5", float("nan"), {"home-5": 0.5, "home-1": 0.3, "home-2": 0.2}, {})
    assert render.explain(pick, NAMES) == "rebounder: Player home-5 0.50 | Player home-1 0.30 | Player home-2 0.20 -> Player home-5"
    zone = Decision(3, "zone", "corner_three", float("nan"), {"corner_three": 0.6, "paint": 0.4}, {})
    assert render.explain(zone, NAMES).startswith("zone: corner three 0.60 | shot in the paint 0.40 -> corner three")
