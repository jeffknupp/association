"""The shared scoping steps read the typed Scope (ROADMAP plan item 6, step (d)).

Every step in ``templates.common`` takes the
:class:`~association.query.reading.Scope` alone, except the four checks
(``check_scope``, ``check_coverage``, ``coverage_caveat`` and
``_sources_for``), which ``agent.py`` still asks about a route's slot dict
before it builds the Reading: these check that a check gives the same answer,
or the same refusal, for a Scope and for its slot dict.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import fields
from typing import Any

import pytest

from association.query.reading import Scope
from association.query.templates.common import (
    _BOX_SCORE_SCOPING,
    SCOPING_SLOTS,
    TemplateUnsupported,
    _sources_for,
    check_coverage,
    check_scope,
    coverage_caveat,
)

_FIELDS = {field.name for field in fields(Scope)}


def test_every_scoping_slot_is_a_field_of_the_typed_scope() -> None:
    """``check_scope`` reads each name in ``SCOPING_SLOTS`` as a Scope field,
    and the box-score sources each in ``_BOX_SCORE_SCOPING``: a name that is
    no field would raise AttributeError on every question rather than refuse
    the one that set it."""
    assert SCOPING_SLOTS <= _FIELDS, sorted(SCOPING_SLOTS - _FIELDS)
    assert set(_BOX_SCORE_SCOPING) <= _FIELDS, sorted(set(_BOX_SCORE_SCOPING) - _FIELDS)


def _outcome(step: Callable[..., Any], *args: Any) -> Any:
    """What a step gives: its value, or the refusal it raised."""
    try:
        return step(*args)
    except TemplateUnsupported as exc:
        return ("refused", str(exc))


# One slot dict per branch the steps take: nothing at all, a player's and a
# team's own reads, an advanced stat, a record's tally, a career ranking, a
# starter/bench category, a window, both season types, a condition, the
# restored-team marker, and every other scoping slot at once.
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
    {"team": "Orlando Magic", "team_restored": True, "stat": "threePointFieldGoalsMade", "season": 2026},
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

_INTENTS = [
    "game_log",
    "player_stat",
    "player_splits",
    "streak",
    "record_when",
    "team_record",
    "team_leaderboard",
    "leaderboard",
    "shot_chart",
    "period_split",
    "threshold_count",
    "head_to_head",
    "team_quarter_points",
    "coach",
]


@pytest.mark.parametrize("slots", _SLOTS)
def test_a_check_reads_a_scope_and_its_slot_dict_alike(slots: dict[str, Any]) -> None:
    """The scoping and coverage checks over both doors."""
    scope = Scope.from_slots(slots)
    for intent in _INTENTS:
        for step in (check_scope, check_coverage, coverage_caveat, _sources_for):
            assert _outcome(step, intent, scope) == _outcome(step, intent, slots), (step.__name__, intent)
