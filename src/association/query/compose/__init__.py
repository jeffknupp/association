"""One compiler over the player-games relation - the step between a
template's refusal and the slower SQL-writing agent.

The pipeline is router -> template -> compiler -> agent. A template on the
relation answers a question at its own fixed point (the six intents in
``association.query.templates``); when a template refuses because the
question's shape is close but not exact - a measure word its list does not
carry, "most ... in a game" rather than a log, a league-wide read with no
player named - :func:`answer` tries the same relation at the point the
question's own words move it to, before the question falls through to the
agent.

Every correctness rule a template on the relation carries - the scoping the
relation narrows by, the rebuilt-line guard, binding parity, the
starter/bench category - is read from the relation exactly once, in
:mod:`association.query.templates.common` and :mod:`association.query.player_games`,
which is what lets this package answer them all through one compiler instead
of a template per shape. See ``core.py``'s module docstring for the rules
themselves and where each is enforced.

Nothing here reaches ollama or the agent: :func:`answer` is a pure function of
a connection, an already-routed intent and slots, and the question's own text.

.. versionadded:: 4.4.0
"""

from __future__ import annotations

from typing import Any

from association.query.templates.common import TemplateContext, TemplateResult

from .core import Query, Refused, Unsupported, run
from .move import move_point
from .sentence import _span_phrase
from .sentence import sentence as _sentence

__all__ = ["answer"]


def _point_data(query: Query, out: dict[str, Any]) -> dict[str, Any]:
    """The point a compiled query answered, as plain values - what a caller
    checks an answer against without re-parsing the sentence."""
    return {
        "rows": out["rows"],
        "player": out["player"],
        "span": _span_phrase(out["span"]),
        "narrowing": out["narrowing"],
        "skeleton": query.skeleton,
        "measures": out["measures"],
        "aggregate": query.aggregate,
        "group": query.group,
        "predicates": query.predicates,
        "window": out["window"],
    }


def answer(ctx: TemplateContext, intent: str, slots: dict[str, Any], question: str) -> TemplateResult | None:
    """A router-classified question, answered by the compiler where a
    template refused it - or ``None``, meaning the question is not a point on
    this relation at all and should fall through to the agent.

    ``Refused`` (the relation itself refusing - no such player, an ambiguous
    name, a coverage floor) is returned as the answer: it is a handled
    outcome carrying the template-shaped refusal, not a reason to fall
    through. ``Unsupported`` (the compiler cannot say this question) becomes
    ``None`` instead, since falling through is exactly what it means.

    This function is the whole surface the agent's fall-through wiring calls;
    nothing else in this package is meant to be called from outside it.

    .. versionadded:: 4.4.0
    """
    try:
        query = move_point(ctx.con, intent, slots, question)
        out = run(ctx.con, query)
    except Unsupported:
        return None
    except Refused as exc:
        return exc.result
    return TemplateResult(data=_point_data(query, out), answer=_sentence(query, out), artifacts=[])
