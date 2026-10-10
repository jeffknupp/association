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

from association.query.answer import Reply
from association.query.coverage import coverage_refusal
from association.query.measure import spelled
from association.query.measures import stat_measure
from association.query.player_relation import RELATION_SCOPING, RELATION_SCOPING_EXCLUDED
from association.query.point import TEAM_SEASON_POINTS, team_season_point
from association.query.reading import SHAPE_NAMES, Cause, Measure, PointShape, Reading, Scope, ShapeCells, ShapeDeclined, _career_scope, cell_set, cell_slots
from association.query.result import Refusal
from association.query.team_relation import TEAM_RELATION_SCOPING, TEAM_RELATION_SCOPING_EXCLUDED

from .core import Query, Refused, Unsupported, _check_relation_scoping
from .netpoints import NetPointsQuery
from .rankings import leaderboard_reads
from .say import say, say_refusal
from .seasons import player_compare_reads, player_history_reads, player_line_reads
from .shots import ShotQuery
from .team import TeamQuery
from .team_stats import TeamSeasonQuery


def skeleton_of(point: Reading) -> str:
    """The compiler's skeleton (``rows``, ``scalar``, ``grouped``, ``run``,
    ``pair``, ``chart``: :class:`~association.query.compose.core.Query`'s
    and the team compiler's) a point's own shape and ``by`` compile as
    (contract 6, ``ROADMAP.md``: one vocabulary, the reader's). A ranking
    and a split group; a comparison groups, except two players' meetings
    (``met``), the pair skeleton; a record over a line (a split ``by``
    ``line``) compiles as the scalar record above and below it, which its
    reader says as the split (``compose.records``); runs are the ``run``
    window; a team's total read on the team relation under its season's
    shape is the sum the team compiler takes where the season reader
    declines.

    .. versionadded:: 6.0.0
    """
    if point.relation == "team" and point.on in ("team_seasons", "team_snapshots"):
        # A team's own total the words read on the team relation, said as
        # its season's shape (``point.TEAM_SEASON_POINTS``: the team-season
        # reader first): what the team compiler sums where that reader
        # declines is the scalar it always was.
        return "scalar"
    if point.shape == "comparison":
        return "pair" if point.by == "met" else "grouped"
    if point.shape == "split":
        return "scalar" if point.by == "line" else "grouped"
    return {"ranking": "grouped", "runs": "run"}.get(point.shape, point.shape)


def point_shape(point: Reading, query: Query | TeamQuery | TeamSeasonQuery | NetPointsQuery | ShotQuery | None = None) -> PointShape:
    """The key the answer side reads ``point`` by: the relation it is
    ``on``, its ``shape`` and its ``by``, as the point reader declared them
    - with the planner's one move: a season-line point its reader did not
    read, planned at the game level (:func:`_game_level`), is on the
    player-games relation, where no reader takes it and the compiler's own
    ranking or grouping answers.

    .. versionadded:: 6.0.0
    """
    relation = point.on
    if isinstance(query, Query) and query.source == "games" and relation == "player_seasons":
        relation = "player_games"
    return PointShape(relation, point.shape, point.by)


def shape_cells(key: PointShape) -> tuple[frozenset[str], ShapeCells] | None:
    """The relation's table a shape is read on and the shape's row of it
    (:class:`~association.query.reading.ShapeCells`): the player relation's
    (:data:`~association.query.player_relation.RELATION_SCOPING`, with
    :data:`~association.query.player_relation.RELATION_SCOPING_EXCLUDED`)
    or the team relation's (:data:`~association.query.team_relation.TEAM_RELATION_SCOPING`,
    :data:`~association.query.team_relation.TEAM_RELATION_SCOPING_EXCLUDED`);
    None for a point no reader takes, which its relation's compiler reads
    whole.

    .. versionadded:: 6.0.0
    """
    if key in RELATION_SCOPING_EXCLUDED:
        return RELATION_SCOPING, RELATION_SCOPING_EXCLUDED[key]
    if key in TEAM_RELATION_SCOPING_EXCLUDED:
        return TEAM_RELATION_SCOPING, TEAM_RELATION_SCOPING_EXCLUDED[key]
    return None


def cells_stated(key: PointShape) -> frozenset[str]:
    """The cells the shape ``key``'s words state: its relation's table and
    what the shape takes beyond it, less what its row says the words do not
    state - where the planner's ``STATED_SCOPING`` row stood until Phase 3,
    step 2's closing slice. Empty for a point no reader takes.

    .. versionadded:: 6.0.0
    """
    found = shape_cells(key)
    if found is None:
        return frozenset()
    table, row = found
    return (table | row.taken) - set(row.unstated)


def cells_unhonored(scope: Scope, key: PointShape) -> list[tuple[str, str, str]]:
    """The planner's one check of a point's cells against its shape's row and
    its relation's table: ``(slot, cell, why)`` for every cell ``scope`` sets
    (:meth:`~association.query.reading.Scope.cells`) that the shape ``key``'s
    words do not state (:func:`cells_stated`), sorted by the slot name a
    decline says (:func:`~association.query.reading.cell_slots`) - and the
    bare starter/bench category on a shape that honors one named half
    (``ShapeCells.sides``). ``why`` is the row's reason, or the relation's
    where its table carries no such cell. A reader steps aside for these, or
    declines them where its shape is the point's only answer
    (``ShapeCells.declined``); what the planner refuses before any reader
    runs is :func:`cells_declined`'s.

    Replaces ``reading.unhonored_scoping`` (over ``STATED_SCOPING``'s row,
    by an intent) and the readers' own ``stated=`` parameter.

    .. versionadded:: 6.0.0
    """
    found = shape_cells(key)
    if found is None:
        return []
    table, row = found
    stated = (table | row.taken) - set(row.unstated)
    unhonored = [(slot, cell, row.unstated.get(cell, _NOT_THE_RELATIONS)) for cell in scope.cells() - stated for slot in cell_slots(scope, cell)]
    if row.sides and scope.split == "starter_bench" and not any(slot == "split" for slot, _, _ in unhonored):
        # The bare category is "show me both groups", the splits' whole
        # answer: a reader of one named half would quietly filter to one
        # side or ignore it, so it is refused rather than either.
        unhonored.append(("split", "split", _BOTH_HALVES))
    return sorted(unhonored)


#: Why a cell its relation's table does not carry is not stated, and why the
#: bare starter/bench category is not stated by a reader of one named half.
_NOT_THE_RELATIONS = "the relation carries no such cell"
_BOTH_HALVES = "the bare starter/bench category is a table of both halves; this reader honors one named half"


#: Why a shape's reader is declined for a cell its words do not state,
#: where it is the point's only answer (:func:`beyond_words`).
_BEYOND_WORDS = "it would answer for a different span than was asked"


def cannot_honor(name: str, slots: list[str], why: str, *, without: str | None = None) -> ShapeDeclined:
    """The decline of a narrowing the shape ``name`` cannot honor - the
    ``cannot_honor`` :class:`~association.query.reading.Cause` the planner
    says, its facts the shape's retired name, the ``slots`` the point sets,
    the subject a cell needs and the point has none of (``without``) and
    the row's reason (``why``) - with the decline's sentence, "<name> cannot
    honor [...] - <why>", as its message.

    .. versionadded:: 6.0.0
    """
    message = f"{name} cannot honor {slots}" + (f" without a named {without}" if without else "") + f" - {why}"
    return ShapeDeclined("cannot_honor", {"name": name, "slots": list(slots), "without": without, "why": why}, message)


def beyond_words(name: str, unhonored: list[tuple[str, str, str]]) -> ShapeDeclined:
    """The decline a shape's reader is declined in, where it is the point's
    only answer and the point sets ``unhonored`` (:func:`cells_unhonored`):
    "<name> cannot honor [...] - it would answer for a different span than
    was asked", the slots as the retired scope check listed them
    (:func:`cannot_honor`).

    .. versionadded:: 6.0.0

    .. versionchanged:: 6.0.0
       The typed decline (:class:`~association.query.reading.ShapeDeclined`),
       its sentence the message; the sentence alone until Phase 3, step 4.
    """
    return cannot_honor(name, [slot for slot, _, _ in unhonored], _BEYOND_WORDS)


def cells_declined(point: Reading, key: PointShape) -> ShapeDeclined | None:
    """The planner's refusal of a cell the point's shape (``key``) cannot
    honor beyond its relation's cells - why it declines the point, or None.
    In the order the slot-era checks ran, each a field of the shape's row
    (:class:`~association.query.reading.ShapeCells`): a cell only a named
    player's games can be narrowed by, where no player is named (a team's or
    the league's run); one only a named team's or player's, where neither
    is (the league's run); a cell the shape refuses outright, said with the
    first one's why (a run's one date, a quarter's split over a range, two
    players' meetings against a third team); and, where the shape's reader
    is the point's only answer and the planner says so before any reader
    runs, anything its words do not state (a comparison, the declared
    relations' readers, the team shapes Phase 2 ported). Until Phase 3,
    step 2's closing slice these were ``_shape_declines``' four branches by
    the reading's intent, ``conditions.condition_needs_player_refusal`` and
    ``_excluded_cells_set``.

    .. versionadded:: 6.0.0

    .. versionchanged:: 6.0.0
       Returns the typed decline (:class:`~association.query.reading.ShapeDeclined`,
       the ``cannot_honor`` cause the planner says), its sentence the
       message; the sentence alone until Phase 3, step 4.
    """
    found = shape_cells(key)
    if found is None:
        return None
    _table, row = found
    name, scope = SHAPE_NAMES[key], point.scope
    if point.relation != "player":
        claimed = sorted(slot for cell in row.named_player for slot in cell_slots(scope, cell))
        if claimed:
            return cannot_honor(name, claimed, "only his own games can be narrowed that way", without="player")
        claimed = sorted(cell for cell in row.named_subject if cell_set(scope, cell)) if scope.subject.team is None else []
        if claimed:
            return cannot_honor(name, claimed, "the league-wide streak has no single subject to narrow", without="team or player")
    refused = [(slot, cell) for cell in row.unstated if cell in row.refused for slot in cell_slots(scope, cell)]
    if refused:
        return cannot_honor(name, [slot for slot, _ in refused], row.unstated[refused[0][1]])
    if row.declined == "plan":
        unhonored = cells_unhonored(scope, key)
        if unhonored:
            return beyond_words(name, unhonored)
    return None


def _cells_taken(key: PointShape, team: bool) -> frozenset[str]:
    """What the shape ``key`` takes beyond its relation's table (``ShapeCells.taken``)
    - for a team's point no reader takes, the unit the team compiler's own
    total reads (:attr:`~association.query.reading.Measure.CELLS`)."""
    found = shape_cells(key)
    if found is not None:
        return found[1].taken
    return Measure.CELLS if team else frozenset()


def plan(reading: Reading) -> Query | TeamQuery | TeamSeasonQuery | NetPointsQuery | ShotQuery:
    """The point ``reading`` names, on the relation it names - see
    :func:`_plan`. A team-season intent's point on another relation (a
    team's own total, "how many 3-pointers have the Magic made") that the
    relation declines is that intent's own team-season point instead, or
    the team-season reader's refusal of the narrowing (:func:`beyond_words`
    over :func:`cells_unhonored`, its shape's row): the retired template was
    tried before the compiler, so the compiler declining never decided such
    a question (Phase 2, step 4).

    .. versionadded:: 5.0.0

    .. versionchanged:: 5.0.0
       Plans a team-season point (:data:`~association.query.point.TEAM_SEASON_POINTS`)
       as a :class:`~association.query.compose.team_stats.TeamSeasonQuery`.
    """
    try:
        return _plan(reading)
    except Unsupported as exc:
        key = reading.asked
        if key not in TEAM_SEASON_POINTS:
            raise
        unhonored = cells_unhonored(reading.scope, key)
        if unhonored:
            raise beyond_words(SHAPE_NAMES[key], unhonored) from exc
        return _team_season_query(team_season_point(key, reading.scope))


def _team_season_query(point: Reading) -> TeamSeasonQuery:
    """A team's own season's point as the team-season reader's query: its
    relation, and the compiler's skeleton its shape compiles as."""
    assert point.relation in ("team_seasons", "team_snapshots")
    return TeamSeasonQuery(scope=point.scope, relation=point.relation, shape="grouped" if skeleton_of(point) == "grouped" else "scalar")


def _plan(reading: Reading) -> Query | TeamQuery | TeamSeasonQuery | NetPointsQuery | ShotQuery:
    """The point ``reading`` names, on the relation it names - or
    :class:`~association.query.compose.core.Unsupported` where that relation
    cannot honor a narrowing the scope carries (``round``, ``rate``, a
    ``situation`` naming no calendar): the planner's own refusal, the rule
    the retired templates' scope check applied, applied for the relation
    (:func:`~association.query.compose.core._check_relation_scoping`) -
    after what the point's shape refuses beyond its relation's cells
    (:func:`cells_declined`), each read off the shape's row of its
    relation's table. The answering loop plans a question's point once,
    through :func:`plan_point`.

    .. versionadded:: 5.0.0

    .. versionchanged:: 5.0.0
       Refuses a narrowing the relation cannot honor (ROADMAP plan item 6,
       step (f)); the compiler's own compile step had, one call later.

    .. versionchanged:: 6.0.0
       Checks the point's typed cells against its shape's row and its
       relation's table (Phase 3, step 2's closing slice), by the planned
       shape; by the reading's intent, through ``STATED_SCOPING`` and
       ``_shape_declines``, until then.
    """
    key = point_shape(reading)
    declined = cells_declined(reading, key)
    if declined is not None:
        raise declined
    if reading.relation == "netpoints":
        # A declared relation's point (Phase 2, slice (v)): its reader reads
        # the scope as the retired template read its slots.
        return NetPointsQuery(scope=reading.scope, shape="chart" if skeleton_of(reading) == "chart" else "scalar")
    if reading.relation == "shots":
        # A declared chart relation's point: its reader reads the scope
        # whole, held above to what its words state.
        return ShotQuery(scope=reading.scope, shape="chart" if skeleton_of(reading) == "chart" else "scalar")
    if reading.relation in ("team_seasons", "team_snapshots"):
        # A team's own season: its reader holds the question to the
        # narrowings its words state when it is asked (its row's
        # `declined="read"`), in the retired template's order - before the
        # coverage floor.
        return _team_season_query(reading)
    if reading.relation == "team":
        # Every team shape, the sums included, against the TEAM relation's
        # own cells and what this shape takes beside them.
        _check_relation_scoping(reading.scope, "team", _cells_taken(key, team=True))
        return TeamQuery(scope=reading.scope, measure=reading.measures[0], aggregate=reading.aggregate, shape=skeleton_of(reading), group=reading.group)
    subject = "everyone" if reading.relation == "everyone" else "player"
    # The season line's ranking (leaderboard's retired reader) takes `rate`
    # - a season total, or a unit refused by name - which no game-level read
    # does; a point its reader declines is refused with it (_game_level).
    _check_relation_scoping(reading.scope, subject, _cells_taken(key, team=False))
    planned = Query(
        scope=reading.scope,
        skeleton=skeleton_of(reading),
        measures=list(reading.measures),
        aggregate=reading.aggregate,
        group=reading.group,
        predicates=list(reading.predicates),
        order=reading.order,
        direction=reading.direction,
        limit=reading.limit,
        minimum_games=reading.minimum_games,
        available=reading.available,
        subject_span=reading.subject_span,
        source="seasons" if key.relation == "player_seasons" else "games",
        subject=subject,
        position=reading.scope.subject.position,
    )
    if planned.source == "seasons" and not _season_line_reads(key, planned):
        return _game_level(key, planned)
    return planned


#: The season line's readers' own checks, by the shape they read: whether
#: the reader reads a point on the line (``compose.rankings``,
#: ``compose.seasons``), from the point alone.
_SEASON_LINE_READS = {
    PointShape("player_seasons", "ranking", "player"): leaderboard_reads,
    PointShape("player_seasons", "scalar", "line"): player_line_reads,
    PointShape("player_seasons", "split", "season"): player_history_reads,
    PointShape("player_seasons", "comparison", "subject"): player_compare_reads,
}


def _season_line_reads(key: PointShape, q: Query) -> bool:
    """Whether the season line's reader for the shape ``key`` reads ``q`` -
    the relation saying for itself whether it reads a point, and the point
    setting no cell the shape's words do not state (:func:`cells_unhonored`)."""
    reads = _SEASON_LINE_READS.get(key)
    return reads is not None and not cells_unhonored(q.scope, key) and reads(q)


def _game_level(key: PointShape, q: Query) -> Query:
    """A season-line point (``source="seasons"``) its reader does not read,
    as the game-level relation reads it: a per-season history becomes his
    career's games grouped by season (the reading the compiler gave every
    history before the season line was a source); a ranking is the
    game-level ranking, exactly as it answered behind the retired
    template's refusal - except for what it cannot say: a stat this
    relation has no measure for is refused by name rather than ranked as
    points, and a ``rate`` only the season line reads is not dropped. An
    unnarrowed ``player_stat`` has no game-level reading that answers the
    same question, so it is declined. The season's coverage floor is
    checked first, as the answering loop checked the season-line point
    before it re-planned it (until Phase 2, step 3, ``games_reading``, in
    ``compose.answer``).
    """
    refusal = coverage_refusal(key, q.scope)
    if refusal is not None:
        raise Refused(refusal)
    if q.group == "season":
        return replace(q, scope=_career_scope(q.scope), source="games")
    if q.group == "player" and q.subject == "everyone":
        stat = spelled(q.scope.measure)
        if stat is not None and stat.strip() and stat_measure(q.scope.measure) is None:
            raise Refused(Refusal(kind="no_ranking_measure", facts={"stat": stat}, shown={"stat": stat}))
        if cell_set(q.scope, "rate"):
            raise Unsupported("the relation cannot honor ['rate'] - it would answer for a different span than was asked")
        if q.scope.measure is not None and q.scope.measure.beside:
            raise Unsupported("the game-level ranking shows no columns beside its measure")
        return replace(q, source="games")
    raise Unsupported("an unnarrowed player line the season line's reader did not say")


@dataclass(frozen=True)
class Planned:
    """What planning one question's Reading came to: the ``query`` on its
    relation, or why there is none - ``declined`` (no reading of the point,
    or a narrowing the relation cannot honor: the caller refuses, naming
    the reason) or ``refusal`` (an answer of its own to give: a stat nothing
    ranks, a floor no ranking applies).

    .. versionadded:: 5.0.0

    .. versionchanged:: 6.0.0
       ``shape`` is the point's own declaration, on every verdict; ``floor``
       (the coverage entry by the retired words) is gone - the shape keys
       the floor (Phase 3, step 1).
    """

    query: Query | TeamQuery | TeamSeasonQuery | NetPointsQuery | ShotQuery | None = None
    declined: str | None = None
    refusal: Reply | None = None
    #: What the point is read and said as (:func:`point_shape`): the answer
    #: side's one key, and the coverage floor's (``coverage.SOURCES``).
    #: Wherever the Reading holds a point - a planned query, a decline or a
    #: refusal alike - so the answering loop's declined path checks the
    #: floor of the point the words named.
    shape: PointShape | None = None
    #: The cause a decline is said by, where the planner declined by one
    #: (:class:`~association.query.reading.ShapeDeclined`: a narrowing the
    #: point's shape cannot honor) - said by the answering loop where the
    #: decline was, after the floor and the reading's own causes
    #: (:func:`refusal_result`); ``declined`` keeps its sentence, the record's.
    #:
    #: .. versionadded:: 6.0.0
    cause: Cause | None = None


#: The reading's causes whose page names the shape refused (``refused``)
#: beside the intent, as the answering loop's own refusals did until Phase 3,
#: step 0: the championship and what the words name that nothing reads
#: (:attr:`Reading.unsupported <association.query.reading.Reading.unsupported>`).
_REFUSED_BY_NAME: frozenset[str] = frozenset(
    {"championship", "playoff_round", "non_calendar_situation", "period_stat", "period_as_condition", "team_period_stat", "bench_points", "team_boolean_count"}
)


def refusal_of(cause: Cause) -> Refusal:
    """A point reading's :class:`~association.query.reading.Cause` as the
    :class:`~association.query.result.Refusal` it is said by: its kind and
    facts, and what the page shows beside the sentence - the facts
    themselves, but for the rankings' refusals, whose page held the
    sentence as its headline too, or the floor asked for.

    .. versionadded:: 5.0.0
    """
    if cause.kind in ("shot_distance_ranking", "ranking_unit"):
        return Refusal(kind=cause.kind, facts=cause.facts, under=("message", "headline"))
    if cause.kind == "ranking_floor_unit":
        return Refusal(kind=cause.kind, facts=cause.facts, shown={"floor": {"unit": cause.facts["unit"], "count": cause.facts["count"]}})
    if cause.kind in _REFUSED_BY_NAME:
        # What the page showed beside these sentences when the answering
        # loop said them itself: the shape refused, by name, and the intent.
        return Refusal(kind=cause.kind, facts=cause.facts, shown={"refused": cause.kind, "intent": cause.facts["intent"]})
    if cause.kind == "no_player_reading":
        return Refusal(kind=cause.kind, facts=cause.facts, shown={"named_player": cause.facts["player"]})
    return Refusal(kind=cause.kind, facts=cause.facts, shown=dict(cause.facts))


def refusal_result(cause: Cause) -> Reply:
    """The refusal a point reading's :class:`~association.query.reading.Cause`
    is said with: the sentence and the data the answering loop hands on,
    one per kind in :data:`~association.query.reading.CAUSES`, said by the
    sayer's one phrase table (:func:`~association.query.compose.say.refusal_phrase`).
    The reader carries the cause and never the sentence (``ROADMAP.md``,
    Phase 1, the ``read_point`` move, step 4).

    .. versionadded:: 5.0.0
    """
    return say_refusal(refusal_of(cause))


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

    .. versionchanged:: 6.0.0
       A refusal the words come to before any point
       (:attr:`~association.query.reading.Reading.refused`: a championship,
       a player named on a question whose shape has no reading for one) is
       the planning's refusal, ahead of the point's own.
    """
    if reading.refused is not None:
        # The words' own refusal, before any point: a championship, a player
        # named on a question whose shape has no reading for one.
        return Planned(refusal=refusal_result(reading.refused))
    if reading.point_refusal is not None:
        return Planned(refusal=refusal_result(reading.point_refusal))
    if reading.point is None:
        return Planned(declined=reading.point_declined or "the compiler has no reading of this point")
    shape = point_shape(reading.point)
    try:
        query = plan(reading.point)
    except ShapeDeclined as exc:
        # A decline the planner says by its cause (the answering loop puts
        # the page's label on it, ShapeDeclined.labeled).
        return Planned(declined=str(exc), shape=shape, cause=exc.cause)
    except Unsupported as exc:
        return Planned(declined=str(exc), shape=shape)
    except Refused as exc:
        return Planned(refusal=say(exc.result), shape=shape)
    return Planned(query=query, shape=point_shape(reading.point, query))
