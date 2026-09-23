"""The compiler that answers what a relation template refuses, before the
question falls through to the SQL-writing agent.

**This module is a contract stub, not the compiler.** The real package - a
``core.py`` with ``Query``/``Compiled``/``compile_query``/``run``, ``adapt.py``,
``move.py`` and ``sentence.py``, ported from the skeleton spike at
``~/association-research/skeleton-spike`` (``compose.py``, ``adapt.py``,
``move.py``, ``sentence.py``, and the three ``*_RESULT.md`` files) - is being
built on a parallel branch against the same contract this file declares. That
branch's ``__init__.py`` replaces this one wholesale; a merge conflict here
resolves by taking theirs. Nothing else in the tree may reach past this
module into the compose package's internals, so the replacement is a drop-in.

The contract, so a caller written against this stub needs no change once the
real package lands: :func:`answer` takes the same context a template does, the
router's intent and slots, and the original question text; it returns a
:class:`~association.query.templates.common.TemplateResult` exactly like a
template's own answer, or ``None`` to say "not a point on this relation -
fall through to the agent". A non-``None`` result that is itself a refusal
(a clarification, a "no match") is still an answer, not a fall-through: the
compiler looked at the question and had something to say about it, even if
that something is "which one did you mean".

.. versionadded:: 4.4.0
"""

from __future__ import annotations

from typing import Any

from ..templates.common import TemplateContext, TemplateResult


def answer(ctx: TemplateContext, intent: str, slots: dict[str, Any], question: str) -> TemplateResult | None:
    """Compile ``intent``/``slots`` onto a relation and answer, or refuse.

    Stub implementation: always returns ``None``, so every question that
    reaches it falls through to the agent exactly as it did before this
    module existed. The real compiler (see the module docstring) answers a
    template's refusal by measure word, skeleton word and subject kind, with
    its own scope stated, or returns a :class:`~association.query.templates.common.TemplateResult`
    that is itself a refusal - never ``None`` for a question it actually
    looked at.

    Args:
        ctx: The warehouse connection and output directory, the same context
            a template is given.
        intent: The router's classification, unchanged from what a template
            would have received.
        slots: The router's slots for the question, after the same
            name-repair and scope-from-question passes a template sees.
        question: The original question text, for the compiler's own reading
            of it (the way a template never needs to, but a refusal-repair
            step - the router-invented-name check, the dropped-player
            restore - already does).

    Returns:
        ``None``, always, in this stub.

    .. versionadded:: 4.4.0
    """
    del ctx, intent, slots, question  # unused here - the stub answers nothing; see the module docstring
    return None
