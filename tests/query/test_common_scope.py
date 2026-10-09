"""The shared scoping steps read the typed Scope (ROADMAP plan item 6, step (d)).

Every shared step takes the :class:`~association.query.reading.Scope`
alone, except the three coverage checks (``check_coverage``,
``coverage_caveat`` and ``sources_for``), which still take a route's slot
dict from the tests: these check that a check gives the same answer, or the
same refusal, for a Scope and for its slot dict.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import fields
from typing import Any

import pytest

from association.query.coverage import check_coverage, coverage_caveat, sources_for
from association.query.reading import SCOPING_SLOTS, PointShape, Scope, Unsupported

_FIELDS = {field.name for field in fields(Scope)}


def test_every_scoping_slot_is_a_field_of_the_typed_scope() -> None:
    """``unhonored_scoping`` reads each name in ``SCOPING_SLOTS`` as a Scope
    field: a name that is no field would raise AttributeError on every
    question rather than refuse the one that set it."""
    assert SCOPING_SLOTS <= _FIELDS, sorted(SCOPING_SLOTS - _FIELDS)


def _outcome(step: Callable[..., Any], *args: Any) -> Any:
    """What a step gives: its value, or the refusal it raised."""
    try:
        return step(*args)
    except Unsupported as exc:
        return ("refused", str(exc))


# One slot dict per branch the steps take: nothing at all, a player's and a
# team's own reads, an advanced stat, a record's tally, a career ranking, a
# starter/bench category, a window, both season types, a condition, a
# team's own count, and every other scoping slot at once.
_SLOTS: list[dict[str, Any]] = [
    {},
    {"player": "Jayson Tatum", "season": 1990, "season_type": 3, "stat": "points", "threshold": 30},
    {"player": "Brandin Podziemski", "opponent": "Detroit Pistons", "venue": "home", "without": ["Stephen Curry", "Jonathan Kuminga"], "season": 2002},
    {"player": "Kareem Abdul-Jabbar", "stat": "ts_pct", "season": 1980, "season_type": 2},
    {"team": "Boston Celtics", "teams": ["Boston Celtics", "Los Angeles Lakers"], "season": 1990, "season_type": 2},
    {"stat": "record", "season": 1990, "season_type": 3},
    {"stat": "points", "span": "career", "season": 2001, "season_type": 3},
    {"player": "Joe Ingles", "split": "starter_bench", "order": "recent", "limit": 5},
    {"player": "Stephen Curry", "limit": 2, "season_type_unstated": True},
    {"player": "Jaylen Brown", "conditions": [{"player": "Jayson Tatum", "side": "own", "predicate": "absent"}], "situation": "on christmas"},
    {"team": "Orlando Magic", "stat": "threePointFieldGoalsMade", "season": 2026},
    {
        "stat": "triple_double",
        "ranked_by": "points",
        "rate": "total",
        "round": "finals",
        "game_n": 4,
        "season_n": 18,
        "since": 2019,
        "until": 2024,
        "date": "2026-01-02",
        "below": ["under 14 fta"],
        "above": ["with 25 minutes"],
    },
]

# The shapes whose floor resolves per question, the fixed ones, and no
# point at all (a refusal the reading came to).
_KEYS: list[PointShape | None] = [
    PointShape("player_games", "rows", "date"),
    PointShape("team_games", "rows", "date"),
    PointShape("player_seasons", "scalar", "line"),
    PointShape("player_games", "scalar", "line"),
    PointShape("player_games", "split", "splits"),
    PointShape("player_games", "runs", "line"),
    PointShape("team_games", "runs", "won"),
    PointShape("player_games", "split", "line"),
    PointShape("team_games", "scalar", "record"),
    PointShape("team_seasons", "ranking", "team"),
    PointShape("player_seasons", "ranking", "player"),
    PointShape("player_games", "ranking", "player"),
    PointShape("shots", "chart", "shots"),
    PointShape("player_periods", "rows", "date"),
    PointShape("player_games", "scalar", "count"),
    PointShape("team_games", "comparison", "opponent"),
    PointShape("team_periods", "scalar", "total"),
    PointShape("player_games", "rows", ""),
    None,
]


@pytest.mark.parametrize("slots", _SLOTS)
def test_a_check_reads_a_scope_and_its_slot_dict_alike(slots: dict[str, Any]) -> None:
    """The scoping and coverage checks over both doors."""
    scope = Scope.from_slots(slots)
    for shape in _KEYS:
        for step in (check_coverage, coverage_caveat, sources_for):
            assert _outcome(step, shape, scope) == _outcome(step, shape, slots), (step.__name__, shape)
