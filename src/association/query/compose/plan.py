"""The planner: a :class:`~association.query.reading.Reading` as the
compiler's point on a relation - :class:`~association.query.compose.core.Query`
over the player-games relation (one player, or everyone), or
:class:`~association.query.compose.team.TeamQuery` over the team-games
relation. A copy, not a decision: nothing here reads the question, and a
field the Reading did not settle is not settled here either.

.. versionadded:: 4.5.0
"""

from __future__ import annotations

from association.query.reading import Reading

from .core import Query
from .team import TeamQuery


def plan(reading: Reading) -> Query | TeamQuery:
    """The point ``reading`` names, on the relation it names.

    .. versionadded:: 4.5.0
    """
    if reading.relation == "team":
        return TeamQuery(scope=reading.scope, measure=reading.measures[0], aggregate=reading.aggregate)
    return Query(
        scope=reading.scope,
        skeleton=reading.shape,
        measures=list(reading.measures),
        aggregate=reading.aggregate,
        group=reading.group,
        predicates=list(reading.predicates),
        order=reading.order,
        direction=reading.direction,
        limit=reading.limit,
        offset=reading.offset,
        minimum_games=reading.minimum_games,
        available=reading.available,
        span=reading.span,
        season=reading.season,
        source=reading.source,
        subject="everyone" if reading.relation == "everyone" else "player",
        position=reading.position,
    )
