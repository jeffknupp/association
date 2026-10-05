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
:data:`TEMPLATES` (the two shot charts), and re-exports what callers outside it
use. Twenty-three more intents have no entry here
(:data:`association.query.compose.COMPILED_INTENTS`): the compiler plans
them, and each is read by its reader and worded by the sayer
(``association.query.compose.say``) since Phase 2 moved them out of these
modules; the presenters that called the retired bodies (``compose/present.py``)
went with slice (iv).

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

TEMPLATES: dict[str, Callable[[TemplateContext, Reading], TemplateResult]] = {}
