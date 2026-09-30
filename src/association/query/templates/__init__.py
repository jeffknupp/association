"""Deterministic query templates: the second half of the router/template split
described in router.py.

Given an intent and its slots, these build and run the SQL themselves - the
box-score templates by composing the one relation in
:mod:`association.query.player_games`, the rest directly - so every correctness
rule lives in code rather than as prose the model re-derives per query. Only
intents present in TEMPLATES (or the compiler's, ``compose.COMPILED_INTENTS``)
are handled; anything else - including a recognized intent whose slots don't
validate - is refused naming why.

The templates live in one module per subject; this package holds the registry,
:data:`TEMPLATES`, and re-exports what callers outside it use. Seven intents
have no template here: the compiler answers them alone, in their retired
templates' words (:data:`association.query.compose.COMPILED_INTENTS`).

.. versionchanged:: 3.0.0
   A package rather than a single module.
"""

from __future__ import annotations

from collections.abc import Callable

from association.query.reading import Reading

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
from .games import head_to_head, period_leaderboard, team_quarter_points
from .netpoints import fingerprint, player_netpoints
from .shots import shot_chart, shot_distance
from .splits import with_without
from .teams import coach, team_leaderboard, team_outlook, team_record, team_stat

TEMPLATES: dict[str, Callable[[TemplateContext, Reading], TemplateResult]] = {
    "team_record": team_record,
    "shot_chart": shot_chart,
    "head_to_head": head_to_head,
    "team_quarter_points": team_quarter_points,
    "period_leaderboard": period_leaderboard,
    "shot_distance": shot_distance,
    "player_netpoints": player_netpoints,
    "fingerprint": fingerprint,
    "with_without": with_without,
    "team_stat": team_stat,
    "team_leaderboard": team_leaderboard,
    "team_outlook": team_outlook,
    # No slots and no table: a refusal naming why a coach question has no
    # answer here. Assigned by route() from the question, not by the model.
    "coach": coach,
}
