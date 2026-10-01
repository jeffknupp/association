"""What was decided on the way to an answer, as data.

Every override of a field the router came back with, every default, every
reading of the question that the answer rests on, has so far been a trace
line - ``-> (scope) 'Boston Celtics' moved from team to opponent`` - which is
prose, read by a person, and the reason today's subject measurement had to
wrap every template just to learn what slots one received. A
:class:`Decision` is the same fact as a value: the stage that made it, the
field it moved, what it was before and after, and why. It is kept on the run's
history record and on the ``answer`` event, never parsed back out of the
trace (AGENTS.md, "Events carry trace lines verbatim").

Jeff's ask, 2026-09-25: "decision points, every override of a field the model
comes back with, inferences and defaults, all the way to presentation". The
subject reading (:mod:`association.query.subject`) is the first producer; the
repair chain's own moves, the compiler's defaults and the page's renderer
choice follow as each is wired.

.. versionadded:: 4.4.0
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from dataclasses import field as dataclass_field
from typing import Any


@dataclass(frozen=True)
class Decision:
    """One decision: ``stage`` names who made it (``"subject"``,
    ``"nickname"``, ``"scope"``, ``"default"``, ``"render"`` ...), ``field``
    what it is about (a slot name, ``"kind"``), ``before`` and ``after`` the
    values (``after`` alone for a reading that overrides nothing), ``reason``
    the evidence in a sentence.

    A decision the answer has to STATE - a name read as one player, a
    season redirected, a ranking's minimum - also carries a ``kind`` (one of
    :data:`association.query.notes.DECISION_KINDS`), what else it could have
    been (``instead_of``) and the ``facts`` its sentence is made of. With no
    ``kind`` it is the trace's alone.

    .. versionadded:: 4.4.0

    .. versionchanged:: 5.0.0
       ``kind``, ``instead_of`` and ``facts``, for a decision the answer
       states (:func:`association.query.notes.decided`).
    """

    stage: str
    field: str
    before: Any
    after: Any
    reason: str
    kind: str = ""
    instead_of: tuple[Any, ...] = ()
    facts: dict[str, Any] = dataclass_field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """The wire and file form. A decision with no ``kind`` - the
        trace's own - is its five original fields, so a record written
        before 5.0.0 and one written after read the same.

        .. versionadded:: 4.4.0
        """
        whole = asdict(self)
        if not self.kind:
            for stated_only in ("kind", "instead_of", "facts"):
                del whole[stated_only]
        return whole

    def line(self) -> str:
        """The one-line trace form - what ``--verbose`` prints and the history
        file keeps beside the values, so a person reading the record sees it
        where it happened."""
        move = f"{self.before!r} -> {self.after!r}" if self.before is not None else f"{self.after!r}"
        return f"  -> (decision) {self.stage} {self.field}: {move}" + (f" ({self.reason})" if self.reason else "")
