"""What is left of the deterministic query templates: no template, only
the relations' shared steps (``common``) and the helpers the readers import
from ``players``, ``splits`` and ``games``, until Phase 2's step 6 moves each
to its relation. Every intent is the compiler's
(:data:`association.query.compose.COMPILED_INTENTS`), read by its reader and
worded by the sayer (``association.query.compose.say``).

.. versionchanged:: 3.0.0
   A package rather than a single module.
"""

from __future__ import annotations

from .common import PLAYER_INTENTS as PLAYER_INTENTS
from .common import PLAYER_REQUIRED_INTENTS as PLAYER_REQUIRED_INTENTS
from .common import RANKING_INTENTS as RANKING_INTENTS
from .common import TABLELESS_INTENTS as TABLELESS_INTENTS
from .common import TEMPLATE_SOURCES as TEMPLATE_SOURCES
from .common import check_coverage as check_coverage
from .common import coverage_caveat as coverage_caveat
