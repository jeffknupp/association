"""The planner: a :class:`~association.query.reading.Reading` as the
compiler's point on a relation - :class:`~association.query.compose.core.Query`
over the player-games relation (one player, or everyone), or
:class:`~association.query.compose.team.TeamQuery` over the team-games
relation. A copy, not a decision: nothing here reads the question, and a
field the Reading did not settle is not settled here either.

.. versionadded:: 5.0.0
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from association.query.measures import stat_measure
from association.query.reading import Cause, Reading, _career_scope
from association.query.templates.common import RELATION_SCOPING_EXCLUDED, TemplateResult, unhonored_scoping
from association.query.templates.players import leaderboard_shot_distance_refusal

from .adapt import WITH_WITHOUT_STATED
from .core import Query, Refused, Unsupported, _check_relation_scoping
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


def _shape_declines(point: Reading) -> str | None:
    """A cell the point's own reader cannot honor beyond the relation's
    cells - why the planner declines the point, or None. The comparison
    over the season line honors no narrowing at all ("compare curry and
    lebron vs the celtics" answered for the whole season would be the
    substitution ``check_scope`` exists to stop); the with/without split
    only what its words state; a quarter's split, a run and two players'
    meetings each refuse the cells their retired template excluded, with
    that template's reason. Until 5.0.0's last change the point reader
    raised these itself while reading, so what was read depended on what
    would answer (``ROADMAP.md``, Phase 1, the ``read_point`` move, step 3).
    """

    intent, scope = point.intent, point.scope
    if intent == "player_compare":
        ignored = unhonored_scoping(intent, scope, frozenset())
        return f"player_compare cannot honor {ignored} - it would answer for a different span than was asked" if ignored else None
    if intent == "with_without":
        ignored = unhonored_scoping(intent, scope, WITH_WITHOUT_STATED)
        return f"with_without cannot honor {ignored} - it would answer for a different span than was asked" if ignored else None
    if intent in ("period_split", "streak", "player_matchup"):
        excluded = RELATION_SCOPING_EXCLUDED[intent]
        # A period condition is excluded from period_split's presenter's
        # WORDS only: the point keeps it, and the compiler's own sentence
        # names both quarters.
        refused = [slot for slot in excluded if (intent != "period_split" or slot != "period_condition") and getattr(scope, slot) not in (None, "", (), False)]
        return f"{intent} cannot honor {refused} - {excluded[refused[0]]}" if refused else None
    return None


def plan(reading: Reading) -> Query | TeamQuery:
    """The point ``reading`` names, on the relation it names - or
    :class:`~association.query.compose.core.Unsupported` where that relation
    cannot honor a narrowing the scope carries (``round``, ``rate``, a
    ``situation`` naming no calendar): the planner's own refusal, the rule
    ``check_scope`` applies for a template, applied for the relation
    (:func:`~association.query.compose.core._check_relation_scoping`).
    The answering loop plans a question's point once, through
    :func:`plan_point`.

    .. versionadded:: 5.0.0

    .. versionchanged:: 5.0.0
       Refuses a narrowing the relation cannot honor (ROADMAP plan item 6,
       step (f)); the compiler's own compile step had, one call later.
    """
    declined = _shape_declines(reading)
    if declined is not None:
        raise Unsupported(declined)
    if reading.relation == "team":
        # Every team shape, the sums included, against the TEAM relation's
        # own cells and what this shape's reader takes beside them.
        _check_relation_scoping(reading.scope, "team", _team_shape_cells(reading))
        return TeamQuery(scope=reading.scope, measure=reading.measures[0], aggregate=reading.aggregate, shape=reading.shape, group=reading.group)
    subject = "everyone" if reading.relation == "everyone" else "player"
    # The season line's ranking (leaderboard's retired reader) honors `rate`
    # - a season total, or a unit refused by name - which no game-level read
    # does; a point its reader declines is refused with it (plan.games_reading).
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


@dataclass(frozen=True)
class Planned:
    """What planning one question's Reading came to: the ``query`` on its
    relation, or why there is none - ``declined`` (no reading of the point,
    or a narrowing the relation cannot honor: the caller refuses, naming
    the reason) or ``refusal`` (an answer of its own to give: a stat nothing
    ranks, a floor no ranking applies).

    .. versionadded:: 5.0.0
    """

    query: Query | TeamQuery | None = None
    declined: str | None = None
    refusal: TemplateResult | None = None


def no_ranking_for(stat: str) -> TemplateResult:
    """The refusal for a ranking by a stat this relation has no measure for.

    .. versionadded:: 5.0.0
    """
    message = f"No ranking reads {stat!r} on the player-games relation - it only ranks the box-score measures it knows, not a NetPoints or other outside figure."
    return TemplateResult(data={"message": message, "stat": stat}, answer=message)


def _ranking_floor_unit(unit: str, count: int) -> TemplateResult:
    """The refusal for a ranking floor in a unit no ranking applies (F056:
    "... with at least 100 attempts"). The sentence names the floor that IS
    applied, so the question can be re-asked with it."""
    message = f"A minimum of {count} {unit} is not a floor this ranking can apply yet - only a minimum number of games is. Ask with 'at least N games', or without the floor."
    return TemplateResult(data={"message": message, "floor": {"unit": unit, "count": count}}, answer=message)


def refusal_result(cause: Cause) -> TemplateResult:
    """The refusal a point reading's :class:`~association.query.reading.Cause`
    is said with: the sentence and the template-shaped data the answering
    loop hands on, one per kind in :data:`~association.query.reading.CAUSES`.
    The reader carries the cause and never the sentence (``ROADMAP.md``,
    Phase 1, the ``read_point`` move, step 4).

    .. versionadded:: 5.0.0
    """
    if cause.kind == "shot_distance_ranking":
        # The retired leaderboard template's own refusal, naming the real
        # cause (ISSUES.md #114).
        return leaderboard_shot_distance_refusal()
    if cause.kind == "no_ranking_measure":
        return no_ranking_for(cause.facts["stat"])
    if cause.kind == "ranking_floor_unit":
        return _ranking_floor_unit(cause.facts["unit"], cause.facts["count"])
    raise ValueError(f"no sentence for the cause {cause.kind!r}")


def games_reading(q: Query) -> Query:
    """A season-line point (``source="seasons"``) the season line's own
    readers declined, as the game-level relation reads it: a per-season
    history becomes his career's games grouped by season (the reading the
    compiler gave every history before the season line was a source). An
    unnarrowed ``player_stat`` has no game-level reading that answers the
    same question, so it raises :class:`~association.query.compose.core.Unsupported`,
    as it did before.

    .. versionadded:: 5.0.0

    .. versionchanged:: 5.0.0
       Lives with the planner (it was ``point.games_reading``).
    """
    if q.source != "seasons":
        return q
    if q.group == "season":
        return replace(q, scope=_career_scope(q.scope), source="games")
    if q.group == "player" and q.subject == "everyone":
        # The season line's ranking declined (leaderboard's retired refusals:
        # a metric with no season form, an unknown field, an ambiguous team):
        # the game-level ranking, exactly as it answered behind the template's
        # refusal - except for what it cannot say. A stat this relation has no
        # measure for is refused by name rather than ranked as points, and a
        # `rate` only the season line reads is not dropped.
        stat = q.scope.stat
        if stat is not None and stat.strip() and stat_measure(stat) is None:
            raise Refused(no_ranking_for(stat))
        if q.scope.rate:
            raise Unsupported("the relation cannot honor ['rate'] - it would answer for a different span than was asked")
        if q.scope.fields:
            raise Unsupported("the game-level ranking shows no columns beside its measure")
        return replace(q, source="games")
    raise Unsupported("an unnarrowed player line the season line's reader did not say")


def plan_point(reading: Reading) -> Planned:
    """The PLAN stage for one question (``ROADMAP.md``, Phase 1): the point
    the parser read (:attr:`Reading.point <association.query.reading.Reading.point>`)
    planned onto its relation, once, by the answering loop - never by the
    parser, which only reads. The parser's own verdict stands where it read
    no point (:attr:`~association.query.reading.Reading.point_declined`,
    :attr:`~association.query.reading.Reading.point_refusal`); the planner's
    refusal of a narrowing the relation cannot honor is a decline, with
    its reason.

    .. versionadded:: 5.0.0
    """
    if reading.point_refusal is not None:
        return Planned(refusal=refusal_result(reading.point_refusal))
    if reading.point is None:
        return Planned(declined=reading.point_declined or "the compiler has no reading of this point")
    try:
        return Planned(query=plan(reading.point))
    except Unsupported as exc:
        return Planned(declined=str(exc))
    except Refused as exc:
        return Planned(refusal=exc.result)
