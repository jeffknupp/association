"""The shared scoping steps read the typed Scope (ROADMAP plan item 6, step (d)).

Each step in ``templates.common`` takes a
:class:`~association.query.reading.Scope` or, until every caller passes
``reading.scope``, the slot dict it is read from. The templates, ``agent.py``
and ``query.compose`` all still pass the dict, so nothing else in the suite
goes through the Scope door yet: these check that every step gives the same
answer, or the same refusal, for a Scope and for its slot dict.

Kept in its own file rather than added to ``test_templates.py``, which the
agents moving the templates onto the Reading edit this round.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import fields
from typing import Any

import duckdb
import pytest

from association.query.entities import Entity
from association.query.reading import Scope
from association.query.templates.common import (
    _BOX_SCORE_SCOPING,
    SCOPING_SLOTS,
    TemplateResult,
    TemplateUnsupported,
    _condition_player_opponent,
    _player_relation_season_type,
    _relation_window,
    _slot_season,
    _sources_for,
    _Span,
    check_coverage,
    check_scope,
    coverage_caveat,
    league_games,
    scoped_games,
    team_games,
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
    """The scoping and coverage checks, and the small readers the templates
    call, over both doors."""
    scope = Scope.from_slots(slots)
    for intent in _INTENTS:
        for step in (check_scope, check_coverage, coverage_caveat, _sources_for):
            assert _outcome(step, intent, scope) == _outcome(step, intent, slots), (step.__name__, intent)
    for reader in (_slot_season, _player_relation_season_type, _relation_window):
        assert reader(scope) == reader(slots), reader.__name__


# Name-free, so no step here reads the warehouse: each builds its narrowing
# (or refuses one) from the scope alone. A situation that names nothing
# narrowable is refused by value, on both doors.
_NARROWINGS: list[dict[str, Any]] = [
    {},
    {"venue": "away", "split": "starter", "situation": "on christmas", "order": "first", "limit": 3},
    {"venue": "home", "game_n": 4, "season_n": 18, "below": ["under 14 fta"], "above": ["with 25 minutes"], "limit": 2},
    {"split": "bench", "situation": "vs the west", "order": "recent"},
    {"situation": "at age 30"},
]


@pytest.mark.parametrize("season_type", [2, 3])
@pytest.mark.parametrize("slots", _NARROWINGS)
def test_a_relation_step_narrows_alike_from_a_scope_and_its_slot_dict(slots: dict[str, Any], season_type: int) -> None:
    """``scoped_games``, ``league_games`` and ``team_games`` over both doors:
    the same clauses, window and labels, or the same refusal (a game of a
    series in a regular season, a situation nothing narrows by)."""
    con = duckdb.connect(":memory:")
    scope = Scope.from_slots(slots)
    span = _Span(2026, season_type)
    player, team = Entity("10", "Brandin Podziemski"), Entity("1", "Golden State Warriors")
    steps: list[Callable[[Any], Any]] = [
        lambda given: scoped_games(con, player, span, given, opponent=None, measures=[]),
        lambda given: league_games(con, span, given, position=None),
        lambda given: team_games(con, team, span, given, opponent=None),
    ]
    for step in steps:
        from_scope, from_slots = _outcome(step, scope), _outcome(step, slots)
        assert not isinstance(from_scope, TemplateResult)
        assert from_scope == from_slots


def test_a_resolved_opponent_in_a_slot_dict_is_carried_past_the_typing() -> None:
    """``player_splits`` resolves the opponent before the player and hands
    ``condition_player`` the Entity inside its slot dict. A Scope holds names
    and refuses an Entity, so it is taken out before the rest is typed, and
    carried to ``scoped_games`` as the team it already is."""
    lakers = Entity("13", "Los Angeles Lakers")
    with pytest.raises(ValueError, match="opponent"):
        Scope.from_slots({"player": "Jayson Tatum", "opponent": lakers})
    carried, scope = _condition_player_opponent({"player": "Jayson Tatum", "opponent": lakers, "venue": "away"})
    assert carried is lakers
    assert scope == Scope(player="Jayson Tatum", venue="away")
    carried, scope = _condition_player_opponent({"player": "Jayson Tatum", "opponent": "Los Angeles Lakers"})
    assert (carried, scope.opponent) == ("Los Angeles Lakers", "Los Angeles Lakers")
    typed = Scope(player="Jayson Tatum", opponent="Los Angeles Lakers")
    assert _condition_player_opponent(typed) == ("Los Angeles Lakers", typed)
