"""The planner: a :class:`~association.query.reading.Reading` as the
compiler's point on a relation - :class:`~association.query.compose.core.Query`
over the player-games relation (one player, or everyone), or
:class:`~association.query.compose.team.TeamQuery` over the team-games
relation. A copy, not a decision: nothing here reads the question, and a
field the Reading did not settle is not settled here either.

.. versionadded:: 5.0.0
"""

from __future__ import annotations

from association.query.reading import Reading

from .core import Query, _check_relation_scoping
from .team import TeamQuery

#: The player-relation cells a team's log, splits and run refuse by name
#: with a sentence of their own (``templates.games._team_game_log_refusals``,
#: ``templates.splits.team_splits``, ``compose.adapt._adapt_streak``): let
#: through here so that sentence, which says where the question belongs, is
#: the refusal. Anything else a team's games do not carry is refused here.
_TEAM_READER_REFUSES: frozenset[str] = frozenset({"without", "below", "above", "season_n", "conditions"})


def _team_shape_cells(reading: Reading) -> frozenset[str]:
    """What a team point's reader takes beyond the team relation's own
    cells: the with/without split reads the teammates it divides by, a
    splits table its category, a sum the unit it is asked in (which its
    mover refuses or reads), and the log, the splits and the run refuse a
    handful of player cells with their own sentence."""
    if reading.group == "presence":
        return frozenset({"without", "conditions"})
    if reading.shape == "grouped":
        return _TEAM_READER_REFUSES | {"split"}
    if reading.shape in ("rows", "run") or reading.aggregate == "record":
        # The log, the run and a record over a line (record_when's team
        # reader) each refuse these by name.
        return _TEAM_READER_REFUSES
    return frozenset({"rate"})


def plan(reading: Reading) -> Query | TeamQuery:
    """The point ``reading`` names, on the relation it names - or
    :class:`~association.query.compose.core.Unsupported` where that relation
    cannot honor a narrowing the scope carries (``round``, ``rate``, a
    ``situation`` naming no calendar): the planner's own refusal, the rule
    ``check_scope`` applies for a template, applied for the relation
    (:func:`~association.query.compose.core._check_relation_scoping`). The
    parser plans the point it reads (:func:`~association.query.parse.with_point`),
    so a Reading carries the refusal from the start (``point_declined``).

    .. versionadded:: 5.0.0

    .. versionchanged:: 5.0.0
       Refuses a narrowing the relation cannot honor (ROADMAP plan item 6,
       step (f)); the compiler's own compile step had, one call later.
    """
    if reading.relation == "team":
        # Every team shape, the sums included, against the TEAM relation's
        # own cells and what this shape's reader takes beside them.
        _check_relation_scoping(reading.scope, "team", _team_shape_cells(reading))
        return TeamQuery(scope=reading.scope, measure=reading.measures[0], aggregate=reading.aggregate, shape=reading.shape, group=reading.group)
    subject = "everyone" if reading.relation == "everyone" else "player"
    # The season line's ranking (leaderboard's retired reader) honors `rate`
    # - a season total, or a unit refused by name - which no game-level read
    # does; a point its reader declines is refused with it (move.games_reading).
    _check_relation_scoping(reading.scope, subject, frozenset({"rate"}) if reading.source == "seasons" and subject == "everyone" else frozenset())
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
        subject=subject,
        position=reading.position,
    )
