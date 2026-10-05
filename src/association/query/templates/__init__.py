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
:data:`TEMPLATES` (eleven intents), and re-exports what callers outside it
use. Thirteen more intents have no entry here
(:data:`association.query.compose.COMPILED_INTENTS`): the compiler plans
them, and most are still read and worded by their retired templates' bodies,
which stay in these modules and are called by the compiler's presenters
(:mod:`association.query.compose.present`). Measured over the 277 yardstick
questions on 2026-09-30, the compiler's own SQL read 45 of the 205 answers
those intents gave and its own sentence worded 16; ``ROADMAP.md``, Phase 2,
is the work of moving the rest.

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
from .teams import team_leaderboard, team_outlook, team_record, team_stat

TEMPLATES: dict[str, Callable[[TemplateContext, Reading], TemplateResult]] = {
    "team_record": team_record,
    "shot_chart": shot_chart,
    "head_to_head": head_to_head,
    "team_quarter_points": team_quarter_points,
    "period_leaderboard": period_leaderboard,
    "shot_distance": shot_distance,
    "player_netpoints": player_netpoints,
    "fingerprint": fingerprint,
    "team_stat": team_stat,
    "team_leaderboard": team_leaderboard,
    "team_outlook": team_outlook,
}
