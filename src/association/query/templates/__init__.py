"""Deterministic query templates: the second half of the router/template split
described in router.py.

Given an intent and its slots, these build and run the SQL themselves - the
box-score templates by composing the one relation in
:mod:`association.query.player_games`, the rest directly - so every correctness
rule lives in code rather than as prose the model re-derives per query. Only
intents present in TEMPLATES are handled; anything else - including
a recognized intent whose slots don't validate - falls through to the agent
untouched.

The templates live in one module per subject; this package holds the registry,
:data:`TEMPLATES`, and re-exports what callers outside it use.

.. versionchanged:: 3.0.0
   A package rather than a single module.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .common import HONORED_SCOPING as HONORED_SCOPING
from .common import PLAYER_INTENTS as PLAYER_INTENTS
from .common import PLAYER_REQUIRED_INTENTS as PLAYER_REQUIRED_INTENTS
from .common import RANKING_INTENTS as RANKING_INTENTS
from .common import TABLELESS_INTENTS as TABLELESS_INTENTS
from .common import TEMPLATE_SOURCES as TEMPLATE_SOURCES
from .common import TemplateContext, TemplateResult
from .common import TemplateUnsupported as TemplateUnsupported
from .common import check_coverage as check_coverage
from .common import check_scope as check_scope
from .common import coverage_caveat as coverage_caveat
from .games import game_log, head_to_head, period_leaderboard, period_split, player_matchup, team_quarter_points
from .netpoints import fingerprint, player_netpoints
from .players import leaderboard, player_compare, player_history, player_stat, single_game_high, threshold_count
from .shots import shot_chart, shot_distance
from .splits import player_splits, record_when, streak, with_without
from .teams import coach, team_leaderboard, team_outlook, team_record, team_stat

TEMPLATES: dict[str, Callable[[TemplateContext, dict[str, Any]], TemplateResult]] = {
    "threshold_count": threshold_count,
    "leaderboard": leaderboard,
    "player_stat": player_stat,
    "team_record": team_record,
    "game_log": game_log,
    "shot_chart": shot_chart,
    "player_compare": player_compare,
    "single_game_high": single_game_high,
    "head_to_head": head_to_head,
    "team_quarter_points": team_quarter_points,
    "period_leaderboard": period_leaderboard,
    "period_split": period_split,
    "shot_distance": shot_distance,
    "player_history": player_history,
    "player_netpoints": player_netpoints,
    "fingerprint": fingerprint,
    "player_splits": player_splits,
    "with_without": with_without,
    "record_when": record_when,
    "player_matchup": player_matchup,
    "streak": streak,
    "team_stat": team_stat,
    "team_leaderboard": team_leaderboard,
    "team_outlook": team_outlook,
    # No slots and no table: a refusal naming why a coach question has no
    # answer here. Assigned by route() from the question, not by the model.
    "coach": coach,
}
