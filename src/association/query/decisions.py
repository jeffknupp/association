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
from typing import Any


@dataclass(frozen=True)
class Decision:
    """One decision: ``stage`` names who made it (``"subject"``,
    ``"nickname"``, ``"scope"``, ``"default"``, ``"render"`` ...), ``field``
    what it is about (a slot name, ``"kind"``), ``before`` and ``after`` the
    values (``after`` alone for a reading that overrides nothing), ``reason``
    the evidence in a sentence.

    .. versionadded:: 4.4.0
    """

    stage: str
    field: str
    before: Any
    after: Any
    reason: str

    def as_dict(self) -> dict[str, Any]:
        """The wire and file form.

        .. versionadded:: 4.4.0
        """
        return asdict(self)

    def line(self) -> str:
        """The one-line trace form - what ``--verbose`` prints and the history
        file keeps beside the values, so a person reading the record sees it
        where it happened."""
        move = f"{self.before!r} -> {self.after!r}" if self.before is not None else f"{self.after!r}"
        return f"  -> (decision) {self.stage} {self.field}: {move}" + (f" ({self.reason})" if self.reason else "")
