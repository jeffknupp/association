"""The planner: a :class:`~association.query.reading.Reading` as the
compiler's point on a relation - :class:`~association.query.compose.core.Query`
over the player-games relation (one player, or everyone), or
:class:`~association.query.compose.team.TeamQuery` over the team-games
relation. A copy, not a decision: nothing here reads the question, and a
field the Reading did not settle is not settled here either.

.. versionadded:: 5.0.0
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from association.query.answer import Answer
from association.query.coverage import coverage_refusal
from association.query.measure import spelled, yes_no
from association.query.measures import stat_measure
from association.query.player_relation import RELATION_SCOPING, RELATION_SCOPING_EXCLUDED
from association.query.point import TEAM_SEASON_POINTS, team_season_point
from association.query.reading import (
    CELL_ORDER,
    PLAYERS_COUNTED,
    Aggregate,
    Cause,
    DeclineReason,
    Filtered,
    Measure,
    PointShape,
    Reading,
    ShapeCells,
    ShapeDeclined,
    _career_scope,
    cell_facts,
    cell_set,
)
from association.query.result import Refusal, Unanswered
from association.query.team_relation import TEAM_RELATION_SCOPING, TEAM_RELATION_SCOPING_EXCLUDED

from .core import Query, Refused, Unsupported, _check_relation_scoping, measure_names
from .netpoints import NetPointsQuery
from .rankings import leaderboard_reads, players_counted_reads, seasons_counted_reads
from .say import decline_body, say_refusal
from .seasons import player_compare_reads, player_history_reads, player_line_reads
from .shots import ShotQuery
from .team import TeamQuery, team_metric
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
    declared = point.read_as
    if point.relation == "team" and declared.relation in ("team_seasons", "team_snapshots"):
        # A team's own total the words read on the team relation, said as
        # its season's shape (``point.TEAM_SEASON_POINTS``: the team-season
        # reader first): what the team compiler sums where that reader
        # declines is the scalar it always was.
        return "scalar"
    if declared.shape == "comparison":
        return "pair" if declared.by == "met" else "grouped"
    if declared.shape == "split":
        return "scalar" if declared.by == "line" else "grouped"
    return {"ranking": "grouped", "runs": "run"}.get(declared.shape, declared.shape)


def aggregate_of(point: Reading) -> Aggregate:
    """How the planned query reduces ``point``'s rows (:class:`~association.query.reading.Aggregate`),
    derived from what the point is read as (its declared
    :attr:`~association.query.reading.Reading.read_as`) and its measure -
    one rule, where the point reader stamped it at each of its sites until
    Phase 5, step 4 (D5, Jeff, 2026-10-14). The planner sets it on the
    ``Query`` and the ``TeamQuery`` it builds, and nothing else reads it.

    The point counts a yes or a no (:func:`counts_yes_no`) where its
    measure is one of the catalog's yes-or-no measures
    (:attr:`~association.query.measure.MeasureSpec.yes_no`: a
    double-double, a triple-double, a foul-out, a win, a game played) or it
    keeps its games by one held true ("how many games has embiid won when
    he scores 30 points": a points measure, a ``won`` predicate).

    What the point is read as, and its aggregate:

    - any ``rows``, any ``chart``, ``netpoints``, ``shots``: ``NONE``;
    - ``team_seasons``, ``team_snapshots``: ``TOTAL`` (the team compiler's
      sum, where the season reader declines; its own query carries none);
    - ``player_seasons``, ``player_periods``: ``PER_GAME`` (a season line is
      per game; a point planned at the game level keeps it);
    - ``runs``: ``COUNT`` on ``team_games`` (a team's wins), ``NONE``
      otherwise;
    - ``comparison``: ``RECORD`` by ``opponent``, ``PER_GAME`` by
      ``subject``, ``NONE`` by ``met``;
    - ``split``: by ``line``, ``COUNT`` where it counts a yes or a no and
      ``RECORD`` otherwise; by ``splits`` or ``presence``, ``RECORD``; by
      anything else, ``PER_GAME``;
    - ``scalar``: by ``line``, ``COUNT`` where it counts a yes or a no and
      ``PER_GAME`` otherwise; by ``count``, ``record`` or ``total``, that;
      by ``""``, ``TOTAL`` on a team relation and ``COUNT`` otherwise;
    - ``ranking``: by ``count``, or where it counts a yes or a no or
      measures nothing, ``COUNT``; where its measure is asked as a total
      (``Measure.how``), ``TOTAL``; ``PER_GAME`` otherwise.

    Measured first (``ROADMAP.md``, Phase 5, step 4): on the 2,710
    readings the planned key alone left 52 points off the stamped value,
    with ``Reading.relation`` 39 and with the yes-or-no facet 24 - a
    games-played ranking, a team's total stamped on its season's shape, and
    one question declined either way - which ``games_played`` in the facet,
    the declared shape and the held-true predicate close: on the 2,710 and
    the 518 reader cases every planned ``Query`` and ``TeamQuery`` reduces
    as the stamped point did.

    .. versionadded:: 6.0.0
    """
    declared = point.read_as
    if declared.shape in ("rows", "chart") or declared.relation in ("netpoints", "shots"):
        return Aggregate.NONE
    if declared.relation in ("team_seasons", "team_snapshots"):
        return Aggregate.TOTAL
    if declared.relation in ("player_seasons", "player_periods"):
        return Aggregate.PER_GAME
    if declared.shape == "runs":
        return Aggregate.COUNT if declared.relation == "team_games" else Aggregate.NONE
    if declared.shape == "comparison":
        return {"opponent": Aggregate.RECORD, "subject": Aggregate.PER_GAME}.get(declared.by, Aggregate.NONE)
    if declared.shape == "ranking":
        return _ranking_aggregate(point)
    return _scalar_or_split_aggregate(point)


def counts_yes_no(point: Reading) -> bool:
    """Whether ``point`` counts a yes or a no of each game: its measure is
    one of the catalog's yes-or-no measures, or it keeps its games by one
    held true (:func:`aggregate_of`).

    .. versionadded:: 6.0.0
    """
    return yes_no(point.measure) or any(op == "=" and value is True and yes_no(name) for name, op, value in point.predicates)


def _ranking_aggregate(point: Reading) -> Aggregate:
    """:func:`aggregate_of` for a ranking on the games: a count of games -
    by ``count``, of a yes or a no, or with no measure to rank by - a total
    where the measure is asked as one, per game otherwise."""
    if point.read_as.by == "count" or counts_yes_no(point) or not point.measures:
        return Aggregate.COUNT
    if point.measure is not None and point.measure.how == "total":
        return Aggregate.TOTAL
    return Aggregate.PER_GAME


def _scalar_or_split_aggregate(point: Reading) -> Aggregate:
    """:func:`aggregate_of` for a scalar or a split on the games: a line's
    count of a yes or a no, its record or its figure per game; the shapes
    that name their own reduction by it."""
    declared = point.read_as
    if declared.by == "line":
        if counts_yes_no(point):
            return Aggregate.COUNT
        return Aggregate.RECORD if declared.shape == "split" else Aggregate.PER_GAME
    if declared.shape == "split":
        return Aggregate.RECORD if declared.by in ("splits", "presence") else Aggregate.PER_GAME
    if declared.by in ("count", "record", "total"):
        return Aggregate(declared.by)
    return Aggregate.TOTAL if declared.relation.startswith("team") else Aggregate.COUNT


def point_shape(point: Reading, query: Query | TeamQuery | TeamSeasonQuery | NetPointsQuery | ShotQuery | None = None) -> PointShape:
    """The key the answer side reads ``point`` by: what the point reader
    declared it read as (:attr:`~association.query.reading.Reading.read_as`)
    - with the planner's one move: a season-line point its reader did not
    read, planned at the game level (:func:`_game_level`), is on the
    player-games relation, where no reader takes it and the compiler's own
    ranking or grouping answers.

    .. versionadded:: 6.0.0
    """
    declared = point.read_as
    if isinstance(query, Query) and query.source == "games" and declared.relation == "player_seasons":
        return replace(declared, relation="player_games")
    return declared


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


def cells_unhonored(scope: Filtered, key: PointShape) -> list[tuple[str, DeclineReason]]:
    """The planner's one check of a point's cells against its shape's row and
    its relation's table: ``(cell, why)`` for every cell ``scope`` sets
    (:meth:`~association.query.reading.Filtered.cells`) that the shape ``key``'s
    words do not state (:func:`cells_stated`), in the order a decline names
    them (:data:`~association.query.reading.CELL_ORDER`) - and the
    bare starter/bench category on a shape that honors one named half
    (``ShapeCells.sides``). ``why`` is the row's reason (a
    :class:`~association.query.reading.DeclineReason`), or the relation's
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
    held = scope.cells() - stated
    unhonored = [(cell, row.unstated.get(cell, DeclineReason.NOT_THE_RELATIONS)) for cell in CELL_ORDER if cell in held]
    if row.sides and scope.split == "starter_bench" and "split" not in held:
        # The bare category is "show me both groups", the splits' whole
        # answer: a reader of one named half would quietly filter to one
        # side or ignore it, so it is refused rather than either.
        unhonored.append(("split", DeclineReason.BOTH_HALVES))
    return unhonored


def cannot_honor(scope: Filtered, cells: list[str], why: DeclineReason, *, without: str | None = None) -> ShapeDeclined:
    """The decline of a narrowing the point's shape cannot honor - the
    ``cannot_honor`` :class:`~association.query.reading.Cause` the planner
    says, its facts what the words asked under each of the ``cells`` the
    point sets (:func:`~association.query.reading.cell_facts`), the subject
    a cell needs and the point has none of (``without``) and the row's
    reason (``why``, the ``reason`` fact: a
    :class:`~association.query.reading.DeclineReason`, which the sayer
    words) - with the decline's sentence
    (:func:`~association.query.compose.say.decline_body`: "the last 5 games
    cannot be read here - <why>") as its message.

    .. versionadded:: 6.0.0

    .. versionchanged:: 6.0.0
       Takes the point and its cells, and says each by what the words asked;
       the shape's retired name and the slot names ("<name> cannot honor
       ['order']") until the Phase 3 review's cleanup (b)3 (ISSUES.md #281).

    .. versionchanged:: 6.0.0
       ``why`` is a :class:`~association.query.reading.DeclineReason`, the
       cause's ``reason`` fact; a row's sentence, the ``why`` fact, until
       Phase 5, step 5.
    """
    facts = {"cells": [cell_facts(scope, cell) for cell in cells], "without": without, "reason": why}
    return ShapeDeclined("cannot_honor", facts, decline_body(facts))


def beyond_words(scope: Filtered, unhonored: list[tuple[str, DeclineReason]]) -> ShapeDeclined:
    """The decline a shape's reader is declined in, where it is the point's
    only answer and the point sets ``unhonored`` (:func:`cells_unhonored`):
    "<the cells> cannot be read here - it would answer for a different span
    than was asked" (:func:`cannot_honor`).

    .. versionadded:: 6.0.0

    .. versionchanged:: 6.0.0
       The typed decline (:class:`~association.query.reading.ShapeDeclined`),
       its sentence the message; the sentence alone until Phase 3, step 4.
    """
    return cannot_honor(scope, [cell for cell, _ in unhonored], DeclineReason.BEYOND_WORDS)


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
    scope = point
    if point.relation != "player":
        claimed = [cell for cell in CELL_ORDER if cell in row.named_player and cell_set(scope, cell)]
        if claimed:
            return cannot_honor(scope, claimed, DeclineReason.NEEDS_NAMED_PLAYER, without="player")
        claimed = [cell for cell in CELL_ORDER if cell in row.named_subject and cell_set(scope, cell)] if scope.who.team is None else []
        if claimed:
            return cannot_honor(scope, claimed, DeclineReason.NO_SINGLE_SUBJECT, without="team or player")
    refused = [cell for cell in row.unstated if cell in row.refused and cell_set(scope, cell)]
    if refused:
        return cannot_honor(scope, [cell for cell in CELL_ORDER if cell in refused], row.unstated[refused[0]])
    if row.declined == "plan":
        unhonored = cells_unhonored(scope, key)
        if unhonored:
            return beyond_words(scope, unhonored)
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
    :func:`_plan`. A team-season key's point on another relation (a
    team's own total, "how many 3-pointers have the Magic made") that the
    relation declines is that key's own team-season point instead, or
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
        unhonored = cells_unhonored(reading, key)
        if unhonored:
            raise beyond_words(reading, unhonored) from exc
        return _team_season_query(team_season_point(key, reading))


def _team_season_query(point: Reading) -> TeamSeasonQuery:
    """A team's own season's point as the team-season reader's query: its
    relation, and the compiler's skeleton its shape compiles as."""
    assert point.relation in ("team_seasons", "team_snapshots")
    return TeamSeasonQuery(**point.filters(), relation=point.relation, shape="grouped" if skeleton_of(point) == "grouped" else "scalar")


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
        return NetPointsQuery(**reading.filters(), shape="chart" if skeleton_of(reading) == "chart" else "scalar")
    if reading.relation == "shots":
        # A declared chart relation's point: its reader reads the scope
        # whole, held above to what its words state.
        return ShotQuery(**reading.filters(), shape="chart" if skeleton_of(reading) == "chart" else "scalar")
    if reading.relation in ("team_seasons", "team_snapshots"):
        # A team's own season: its reader holds the question to the
        # narrowings its words state when it is asked (its row's
        # `declined="read"`), in the retired template's order - before the
        # coverage floor.
        return _team_season_query(reading)
    if reading.relation == "team":
        # Every team shape, the sums included, against the TEAM relation's
        # own cells and what this shape takes beside them.
        _check_relation_scoping(reading, "team", _cells_taken(key, team=True))
        return TeamQuery(**reading.filters(), metric=team_metric(reading.measures[0]), aggregate=aggregate_of(reading), shape=skeleton_of(reading), group=reading.group)
    subject = "everyone" if reading.relation == "everyone" else "player"
    # The season line's ranking (leaderboard's retired reader) takes `rate`
    # - a season total, or a unit refused by name - which no game-level read
    # does; a point its reader declines is refused with it (_game_level).
    _check_relation_scoping(reading, subject, _cells_taken(key, team=False))
    planned = Query(
        **reading.filters(),
        skeleton=skeleton_of(reading),
        measures=measure_names(reading.measures),
        aggregate=aggregate_of(reading),
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
        position=reading.who.position,
    )
    if planned.source == "seasons" and not _season_line_reads(key, planned):
        return _game_level(key, planned)
    return planned


#: The season line's readers' own checks, by the shape they read: whether
#: the reader reads a point on the line (``compose.rankings``,
#: ``compose.seasons``), from the point alone.
_SEASON_LINE_READS = {
    PointShape("player_seasons", "ranking", "player"): leaderboard_reads,
    PointShape("player_seasons", "scalar", "count"): players_counted_reads,
    PointShape("player_seasons", "ranking", "seasons"): seasons_counted_reads,
    PointShape("player_seasons", "scalar", "line"): player_line_reads,
    PointShape("player_seasons", "split", "season"): player_history_reads,
    PointShape("player_seasons", "comparison", "subject"): player_compare_reads,
}


def _season_line_reads(key: PointShape, q: Query) -> bool:
    """Whether the season line's reader for the shape ``key`` reads ``q`` -
    the relation saying for itself whether it reads a point, and the point
    setting no cell the shape's words do not state (:func:`cells_unhonored`)."""
    reads = _SEASON_LINE_READS.get(key)
    return reads is not None and not cells_unhonored(q, key) and reads(q)


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
    refusal = coverage_refusal(key, q)
    if refusal is not None:
        raise Refused(refusal)
    if q.group == "season":
        return replace(q, **(_career_scope(q)).filters(), source="games")
    if key in (PLAYERS_COUNTED, PointShape("player_seasons", "ranking", "seasons")):
        # How many players a season line holds, or how many of a player's
        # seasons it holds, has no game-level reading: the line is a
        # season's, declined by name (ISSUES.md #390, #391).
        raise cannot_honor(q, ["season_line"], DeclineReason.BEYOND_WORDS)
    if q.group == "player" and q.subject == "everyone":
        stat = spelled(q.measure)
        if stat is not None and stat.strip() and stat_measure(q.measure) is None:
            raise Refused(Refusal(kind="no_ranking_measure", facts={"stat": stat}, shown={"stat": stat}))
        if cell_set(q, "rate"):
            raise cannot_honor(q, ["rate"], DeclineReason.BEYOND_WORDS)
        if q.measure is not None and q.measure.beside:
            raise Unsupported("the game-level ranking shows no columns beside its measure")
        return _game_level_minimum(replace(q, source="games"))
    raise Unsupported("an unnarrowed player line the season line's reader did not say")


def _game_level_minimum(q: Query) -> Query:
    """The game-level ranking's qualifier where the season line's point
    carried the default: a minimum the question states in games is the
    one it ranks by (the season-line point carries the season line's
    default 20, which its own reader replaces by the stated one), and a
    minimum in another unit is refused by name, as the game-level ranking
    the point reader builds refuses it (``point._everyone_ranking``) -
    before, "most points vs nuggets this season minimum 2 games" ranked at
    20 games and said no player had played the 2 asked for, and a minimum
    in attempts was dropped while the heading said "minimum 20 games" as
    though asked."""
    if q.minimum is None:
        return q
    if q.minimum.unit != "games":
        raise Refused(refusal_of(Cause(kind="ranking_floor_unit", facts={"unit": q.minimum.word, "count": q.minimum.count})))
    return replace(q, minimum_games=q.minimum.count)


PlannedQuery = Query | TeamQuery | TeamSeasonQuery | NetPointsQuery | ShotQuery
"""A point planned onto its relation: the compiler's
:class:`~association.query.compose.core.Query` (a player's or the league's
games, or the season line), the team compiler's
:class:`~association.query.compose.team.TeamQuery`, a team's own season
(:class:`~association.query.compose.team_stats.TeamSeasonQuery`), or a
declared relation's (:class:`~association.query.compose.netpoints.NetPointsQuery`,
:class:`~association.query.compose.shots.ShotQuery`).

.. versionadded:: 6.0.0
"""

Verdict = PlannedQuery | Unanswered | Unsupported
"""What planning one question's Reading comes to (:func:`plan_point`): the
Query-or-Cause, one value (``ROADMAP.md``, Phase 4, step 4) - the query on
its relation (:data:`PlannedQuery`); the refusal it came to, unworded, which
the sayer words where it is answered (a :class:`~association.query.result.Refusal`,
the Outcome's own type: a cause the words came to before any point, a stat
nothing ranks, a floor no ranking applies); or the decline it gave the
point up with (an :class:`~association.query.compose.core.Unsupported`
carrying its sentence: no reading of the point, a narrowing the relation
cannot honor - a :class:`~association.query.reading.ShapeDeclined` where a
shape declines by the :class:`~association.query.reading.Cause` the planner
says, ``cannot_honor``). The shape the point is read and said as is
:func:`planned_shape`'s, from the Reading and this.

.. versionadded:: 6.0.0
   Folds ``compose.plan.Planned``, the record that carried the query, the
   decline's sentence, the said refusal, the shape and the decline's cause
   beside one another until Phase 4, step 4.
"""

#: What a point nothing reads is declined with, where neither the reader nor
#: the planner named a reason of its own.
NO_READING = "the compiler has no reading of this point"


#: The reading's causes whose page names the cause refused by (``refused``),
#: as the answering loop's own refusals did until Phase 3, step 0 (with the
#: page's label beside it, ``intent``, until Phase 4, step 2): the
#: championship and what the words name that nothing reads
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
    if cause.kind in ("ranking_floor_unit", "minimum_unranked"):
        return Refusal(kind=cause.kind, facts=cause.facts, shown={"floor": {"unit": cause.facts["unit"], "count": cause.facts["count"]}})
    if cause.kind in _REFUSED_BY_NAME:
        # What the page showed beside these sentences when the answering
        # loop said them itself: the cause refused by, by name.
        return Refusal(kind=cause.kind, facts=cause.facts, shown={"refused": cause.kind})
    if cause.kind == "no_player_reading":
        return Refusal(kind=cause.kind, facts=cause.facts, shown={"named_player": cause.facts["player"]})
    return Refusal(kind=cause.kind, facts=cause.facts, shown=_page_facts(cause.facts))


def _page_facts(facts: Mapping[str, Any]) -> dict[str, Any]:
    """A cause's facts as the page shows them beside its sentence: every
    plain fact, and none of the shapes it names (``shape``, the key the
    words ask; ``planned``) - the answer carries the shape it was read as
    (``Answer.shape``). Until Phase 4, step 2 a shape was shown by the
    page's label, as ``intent`` (and ``name``)."""
    return {key: value for key, value in facts.items() if key not in ("shape", "planned")}


def refusal_result(cause: Cause) -> Answer:
    """The refusal a point reading's :class:`~association.query.reading.Cause`
    is said with: the sentence and the data the answering loop hands on,
    one per kind in :data:`~association.query.reading.CAUSES`, said by the
    sayer's one phrase table (:func:`~association.query.compose.say.refusal_phrase`).
    The reader carries the cause and never the sentence (``ROADMAP.md``,
    Phase 1, the ``read_point`` move, step 4).

    .. versionadded:: 5.0.0
    """
    return say_refusal(refusal_of(cause))


def plan_point(reading: Reading) -> Verdict:
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

    .. versionchanged:: 6.0.0
       Returns the Query-or-Cause (:data:`Verdict`): the planned query, the
       refusal unworded or the decline itself, where it returned the
       ``Planned`` record holding them and the shape beside one another, a
       refusal worded (Phase 4, step 4).
    """
    if reading.refused is not None:
        # The words' own refusal, before any point: a championship, a player
        # named on a question whose shape has no reading for one.
        return refusal_of(reading.refused)
    if reading.point_refusal is not None:
        return refusal_of(reading.point_refusal)
    if reading.point is None:
        return Unsupported(reading.point_declined or NO_READING)
    try:
        query = plan(reading.point)
    except Unsupported as exc:
        # A decline, with its sentence - by its cause where the planner says
        # one (ShapeDeclined: the answering loop puts the key the words ask
        # on it, ShapeDeclined.under). Kept without the frames it was raised
        # through, which a value outlives.
        return exc.with_traceback(None)
    except Refused as exc:
        return exc.result
    if reading.defaulted is not None:
        # Phase 5, step 1: the planning would answer, past a word that
        # carries a meaning the reading never read - a number, a stat, a
        # window, a date - and the answer would be fluent and wrong. Said
        # here and nowhere earlier, so a decline or a refusal above keeps
        # its own sentence.
        return refusal_of(reading.defaulted)
    return query


def planned_shape(reading: Reading, planned: Verdict) -> PointShape | None:
    """What ``reading``'s point is read and said as (:func:`point_shape`),
    under its planning (``planned``, :func:`plan_point`): the answer side's
    one key and the coverage floor's (``coverage.SOURCES``) - wherever the
    Reading holds a point the words named, a planned query, a decline or a
    refusal alike, so the answering loop's declined path checks the floor of
    that point; the planned query's where there is one (a season-line point
    planned at the game level is on the player-games relation). None where
    the words came to a refusal before any point, or to no point.

    .. versionadded:: 6.0.0
       ``Planned.shape``, carried beside the query on the planning until
       Phase 4, step 4.
    """
    if reading.refused is not None or reading.point_refusal is not None or reading.point is None:
        return None
    return point_shape(reading.point, planned if isinstance(planned, Query) else None)
