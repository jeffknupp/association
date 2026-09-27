"""The Reading: what a question asks, as one typed record, and the whole
record of the decision.

A question is read once - who it is about (the :class:`~association.query.subject.Subject`),
which relation answers it, what it measures, which rows count, over what
scope, in what window, in what shape - and everything after the reading
consumes this record and reads nothing from the question again. The planner
(:func:`association.query.compose.plan.plan`) turns it into the compiler's
point on a relation; the agent logs it as the trace line that says where
every value came from.

ROADMAP plan item 6, step (a): the record exists and the compiler plans from
it. What still builds it is the compiler's own word reading
(:func:`association.query.compose.move.read_point`), on the router's slots;
the parser that builds it from the question directly is step (b), and the
relations reading it in place of a slot dict is step (d). Until then
:attr:`Reading.scope` carries the slot dict the relations read today.

.. versionadded:: 4.6.0
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from association.query.subject import Subject


@dataclass
class Reading:
    """One question, read. The point fields (shape, measures, aggregate,
    group, predicates, order, direction, limit) mirror
    :class:`~association.query.compose.core.Query` on purpose: the planner
    is a copy, not a second decision.

    .. versionadded:: 4.6.0
    """

    #: The scoping the relation narrows by - today the question's slot dict,
    #: forwarded whole (season, span, season_type, venue, opponent, without,
    #: conditions, ...), with the subject's names still inside it. Step (d)
    #: types it and takes the names out.
    scope: dict[str, Any]
    #: ``"rows"``, ``"scalar"`` or ``"grouped"``.
    shape: str = "rows"
    #: The measures the answer reads (box-score columns or derived measures).
    measures: list[str] = field(default_factory=list)
    #: ``"none"``, ``"per_game"``, ``"total"``, ``"count"``, ``"max"``, ``"min"``, ``"rate"`` or ``"record"``.
    aggregate: str = "none"
    #: ``"none"`` or a key of :data:`~association.query.compose.core.GROUPS`.
    group: str = "none"
    #: Row predicates: ``(measure, op, value)``.
    predicates: list[tuple[str, str, Any]] = field(default_factory=list)
    #: ``"date"`` or ``"measure"``.
    order: str = "date"
    #: ``"asc"`` or ``"desc"``.
    direction: str = "desc"
    #: Row count for a ``rows`` read, or the number of groups for a ``grouped`` one.
    limit: int | None = None
    offset: int = 0
    #: The minimum games a group needs to be kept, for a ranking.
    minimum_games: int | None = None
    #: Binding parity with the template being mirrored (see
    #: :class:`~association.query.compose.core.Query`).
    available: Any = None
    span: Any = None
    season: Any = None
    #: ``"games"`` (the player-games relation) or ``"seasons"`` (the season line).
    source: str = "games"
    #: ``"player"`` (one named player), ``"everyone"`` (the league-wide read of
    #: the player relation) or ``"team"`` (the team-games relation).
    relation: str = "player"
    #: A position code, honored on the ``"everyone"`` relation.
    position: str | None = None
    #: The intent label the trace and the presenters use.
    intent: str = ""
    #: Who the question is about, as the subject reading read it.
    subject: Subject | None = None
    #: One line per finding, for the trace.
    evidence: tuple[str, ...] = ()

    def describe(self) -> str:
        """The one trace line: every field that decides the answer."""
        scope = {k: v for k, v in self.scope.items() if v not in (None, [], "")}
        window = f"{self.order}/{self.direction}" + (f"/{self.limit}" if self.limit is not None else "")
        who = self.subject.kind if self.subject is not None else "?"
        return (
            f"relation={self.relation} subject={who} shape={self.shape} measures={self.measures} aggregate={self.aggregate} "
            f"group={self.group} predicates={self.predicates} window={window} source={self.source} scope={scope}"
        )
