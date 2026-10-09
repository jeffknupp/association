"""One compiler over the player-games and team-games relations: the only
answer the intents in :data:`COMPILED_INTENTS` have, the step after a
live template's refusal, and the last one before a refusal naming why.

The pipeline is parser -> template or compiler -> refusal. :func:`answer`
is handed the point the parser read from the question's words
(:attr:`Reading.point <association.query.reading.Reading.point>`), plans it
(:mod:`~association.query.compose.plan`) and answers it one of three ways,
chosen by the shape the point reader declared and the planner settled
(:class:`~association.query.reading.PointShape`: the relation, the
shape and what it is by) and by nothing else: the shape's reader and the
sayer (``compose.logs``, ``compose.runs``, ``compose.presence``, ... and
:mod:`~association.query.compose.say`), a team's sum through
:mod:`~association.query.compose.team`, and any other point through the
compiler's own SQL (:mod:`~association.query.compose.core`) and sentence
(:mod:`~association.query.compose.sentence`). Which of the three answered
is not visible in the answer. The presenters that called the retired
templates' bodies are gone (``compose/present.py``, deleted with Phase 2's
slice (iv)).

Every correctness rule a reader on the relation carries - the scoping the
relation narrows by, the rebuilt-line guard, binding parity, the
starter/bench category - is read from the relation exactly once, in
:mod:`association.query.player_relation` and :mod:`association.query.player_games`,
which is what lets this package answer them all through one compiler instead
of a template per shape. See ``core.py``'s module docstring for the rules
themselves and where each is enforced.

Nothing here reaches ollama, and nothing here reads the question:
:func:`answer` is a function of a connection and the
:class:`~association.query.reading.Reading` the parser settled.

.. versionadded:: 4.4.0
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import duckdb

from association.query.answer import AnswerContext, Reply
from association.query.coverage import coverage_refusal
from association.query.reading import PointShape
from association.query.result import Result, Unanswered

from .core import Query, Refused, Unsupported, run
from .counts import read_threshold_count
from .highs import read_single_game_high
from .logs import read_player_log, read_team_log
from .meetings import read_head_to_head
from .netpoints import NetPointsQuery, draw_fingerprint, read_fingerprint, read_player_netpoints
from .pairs import read_player_matchup
from .periods import read_period_leaderboard, read_period_split, read_team_quarter_points
from .plan import SHAPE_NAMES, STATED_SCOPING, Planned
from .presence import read_with_without
from .rankings import read_leaderboard
from .records import read_record_when, read_team_record_when
from .runs import read_streak, read_team_streak
from .say import say
from .seasons import read_player_compare, read_player_history, read_player_line
from .sentence import _span_phrase
from .sentence import sentence as _sentence
from .sentence import team_sentence as _team_sentence
from .shots import ShotQuery, draw_shot_chart, read_shot_chart, read_shot_distance
from .splits import read_player_splits, read_team_splits
from .stats import read_player_stat
from .team import TeamQuery, TeamResult, run_team
from .team_records import read_team_record
from .team_stats import TeamSeasonQuery, read_team_leaderboard, read_team_outlook, read_team_stat

if TYPE_CHECKING:
    from association.query.reading import Reading

__all__ = ["answer"]


def _point_data(query: Query, out: dict[str, Any], headline: str) -> dict[str, Any]:
    """The point a compiled query answered, as plain values - what a caller
    checks an answer against without re-parsing the sentence.

    ``headline`` is the sentence's own first line (the page's headline, when
    a template does not carry one of its own - ``renderAnswer`` in
    ``web/static/index.html``). ``total`` is the whole count behind a
    by-player count a window cut short (:func:`~association.query.compose.core._grouped_total`,
    e.g. "thunder all-time triple doubles": ten rows shown, ``total`` the
    real 193) - ``None`` for every other point, since only that one shape has
    a listed count smaller than the real one."""
    return {
        "rows": out["rows"],
        "player": out["player"],
        "span": _span_phrase(out["span"], out.get("player_seasons")),
        "narrowing": out["narrowing"],
        "skeleton": query.skeleton,
        "measures": out["measures"],
        "aggregate": query.aggregate,
        "group": query.group,
        "predicates": query.predicates,
        "window": out["window"],
        "notes": out["notes"],
        "headline": headline,
        "total": out["total"],
    }


def _team_point_data(query: TeamQuery, result: TeamResult) -> dict[str, Any]:
    """The point a compiled :class:`~association.query.compose.team.TeamQuery`
    answered, as plain values - the team subject's counterpart of :func:`_point_data`."""
    return {
        "team": result.team.name if result.team is not None else None,
        "span": _span_phrase(result.span),
        "narrowing": result.narrowed_text,
        "measure": query.measure,
        "aggregate": query.aggregate,
        "value": result.value,
        "games": result.games,
        "wins": result.wins,
        "losses": result.losses,
        "from_season_line": result.from_season_line,
    }


COMPILED_INTENTS: frozenset[str] = frozenset(
    {
        "threshold_count",
        "single_game_high",
        "record_when",
        "player_history",
        "game_log",
        "player_stat",
        "player_splits",
        "leaderboard",
        "period_split",
        "player_compare",
        "streak",
        "player_matchup",
        "with_without",
        "coach",
        "team_outlook",
        "team_stat",
        "team_leaderboard",
        "head_to_head",
        "team_quarter_points",
        "period_leaderboard",
        "team_record",
        "player_netpoints",
        "fingerprint",
        "shot_distance",
        "shot_chart",
    }
)
"""The intents the compiler alone answers - the four whose templates it
reproduced exactly (``~/association-research/intent-shrink/parity.py``:
18/18, 10/10, 10/10, 20/20 on the recorded corpus) and then replaced
(ROADMAP plan item 6, step (d), part 4), and ``game_log``, ``player_stat``,
``player_splits``, ``leaderboard``, ``period_split``, ``player_compare``,
``streak``, ``player_matchup`` and ``with_without`` (step (g):
``intent-shrink/g/``, every unit-test call and recorded question answered
both ways; ``streak`` is the ``run`` shape, ``player_matchup`` the ``pair``
shape and ``with_without`` the team relation's ``presence`` group, skeletons
the compiler gained for them), and the team shapes of Phase 2's slice
(iv): ``head_to_head`` (:mod:`~association.query.compose.meetings`),
``team_quarter_points`` and ``period_leaderboard``
(:mod:`~association.query.compose.periods`) and ``team_record``
(:mod:`~association.query.compose.team_records`), the NetPoints
relation's ``player_netpoints`` and ``fingerprint``
(:mod:`~association.query.compose.netpoints`, slice (v): the fingerprint's
chart drawn between its reader and the sayer), and ``shot_distance`` and
``shot_chart`` (:mod:`~association.query.compose.shots`, slice (v): the
shot relation, a declared relation with its own reader, the chart drawn
between it and the sayer). Each is read by its reader and said in its
retired template's own words by the sayer
(:mod:`~association.query.compose.say`); where the compiler has no
reading of a point, the question is refused with the reason
(``agent._run_compiled``).

.. versionadded:: 5.0.0
"""


def answer(
    ctx: AnswerContext,
    reading: Reading,
    *,
    planned: Planned,
    trace: Callable[[Reading], None] | None = None,
    declined: Callable[[str], None] | None = None,
) -> Reply | None:
    """The point the parser read for a question (:attr:`Reading.point`,
    :func:`~association.query.parse.reading_from_route`), answered - run,
    never read from the question again. ``planned`` is the point planned
    onto its relation (:func:`~association.query.compose.plan.plan_point`):
    the PLAN stage's verdict, made once by whoever calls - the answering
    loop, or a test - and never here. Where there is
    no query, the verdict stands: a refusal is the answer
    (:attr:`~association.query.compose.plan.Planned.refusal`), and a decline
    is ``None`` - the question is not a point on this relation, or carries a
    narrowing the relation cannot honor, and the caller refuses it - with
    the reason given to ``declined``.

    ``Refused`` (the relation itself refusing - no such player, an ambiguous
    name, a coverage floor) is returned as the answer: a handled outcome
    carrying the typed refusal or question, said, not a reason to decline.
    ``Unsupported`` (the compiler cannot say this question) becomes ``None``
    instead, since declining is exactly what it means. The team
    subject is answered by its readers (a team's log, splits, streak,
    with/without split and record over its own line) or
    :func:`~association.query.compose.team.run_team`, a point by its
    shape's reader and the sayer (``planned.shape``: the answer side reads
    no intent), and the rest by the
    compiler's own sentence, with the box-score caveats
    :func:`~association.query.compose.core.run` reads appended (#197); the
    answering loop appends :func:`~association.query.coverage.coverage_caveat`
    as it does to a template's answer. ``trace`` is handed the point before
    it is planned - the answering loop logs it as the decision record.

    This function is the whole surface ``agent.py`` calls; nothing else in
    this package is meant to be called from outside it.

    .. versionadded:: 4.4.0

    .. versionchanged:: 5.0.0
       Takes the :class:`~association.query.reading.Reading` the parser
       settled, in place of an intent, a slot dict and the question it read
       its own point from (ROADMAP plan item 6, step (f)): the compiler plans
       and runs, and never reads the question. Until then this was
       ``answer_reading``, beside the slot-taking ``answer``.

    .. versionchanged:: 5.0.0
       Takes ``planned``: the planner runs once per question, in the
       answering loop, where the parser used to plan the point as it read
       it and this function again (``ROADMAP.md``, Phase 1). Required: until
       the step after, a caller that left it out had the Reading planned
       here, a second planner path only tests took.

    .. versionchanged:: 5.0.0
       No ``ran`` callback: the query a point is answered by is the planned
       one, always - since Phase 2, step 3 the planner plans a season-line
       point its reader does not read as the game-level query, where
       ``plan.games_reading`` re-planned it here (3 of the 628 recorded
       questions), and the callback that reported the re-planned query to
       the stage snapshot went with step 4.

    .. versionchanged:: 5.0.0
       Chooses the reader by ``planned.shape``
       (:class:`~association.query.reading.PointShape`), where it read
       the reading's intent (the Phase 2 review's cleanup (b)6); a reader's
       refusal or question is typed and said by the sayer.
    """
    verdict = planned
    if verdict.refusal is not None:
        # A copy: the caller appends its notes to the answer it is handed.
        return copy.deepcopy(verdict.refusal)
    if verdict.query is None or reading.point is None:
        if declined is not None:
            declined(verdict.declined or "the compiler has no reading of this point")
        return None
    return _answer_point(ctx, reading.point, verdict, trace, declined)


def _read_log(read: Callable[[], Result | Unanswered | None]) -> Reply | None:
    """A log read and said: a Result, or the relation's own refusal or
    question, through the sayer; ``None`` as ``None`` (the compiler's own
    sentence answers). A cell the relation refuses while reading is the
    compiler's decline, as ``present`` made it."""
    try:
        read_log = read()
    except Unsupported as exc:
        raise Unsupported(f"relation: {exc}") from exc
    return None if read_log is None else say(read_log)


def _read_only(read: Callable[[], Result | Unanswered | None], name: str) -> Result | Unanswered:
    """A shape whose reader is the point's only answer, as its retired
    template was: a cell the reader refuses while reading is the compiler's
    decline with the reader's own reason (no prefix), and a point it does
    not read is declined too, named by its words (``name``), never handed
    to the compiler's sentence."""
    try:
        found = read()
    except Unsupported as exc:
        raise Unsupported(str(exc)) from exc
    if found is None:
        raise Unsupported(f"{name} has no reading of this point")
    return found


@dataclass(frozen=True)
class _Route:
    """How a shape is read: its ``reader``, and whether that reader is the
    point's only answer (``only``: a decline is the shape's, never the
    compiler's turn - the team shapes slice (iv) ported, the declared
    relations) or steps aside for the compiler's own sentence."""

    reader: Callable[..., Result | Unanswered | None]
    only: bool = False


#: The readers, by the shape the point declares (:class:`~association.query.reading.PointShape`):
#: the one table the answer side chooses by - never by an intent.
_ROUTES: dict[PointShape, _Route] = {
    PointShape("player_games", "rows", "date"): _Route(read_player_log),
    PointShape("player_games", "scalar", "line"): _Route(read_player_stat),
    PointShape("player_games", "split", "line"): _Route(read_record_when),
    PointShape("player_games", "split", "splits"): _Route(read_player_splits),
    PointShape("player_games", "scalar", "count"): _Route(read_threshold_count),
    PointShape("player_games", "ranking", "count"): _Route(read_threshold_count),
    PointShape("player_games", "rows", "count"): _Route(read_threshold_count),
    PointShape("player_games", "rows", "measure"): _Route(read_single_game_high),
    PointShape("player_games", "runs", "line"): _Route(read_streak),
    PointShape("player_games", "comparison", "met"): _Route(read_player_matchup),
    PointShape("player_periods", "rows", "date"): _Route(read_period_split),
    PointShape("player_periods", "split", "period"): _Route(read_period_split),
    PointShape("player_periods", "ranking", "player"): _Route(read_period_leaderboard, only=True),
    PointShape("player_seasons", "ranking", "player"): _Route(read_leaderboard),
    PointShape("player_seasons", "scalar", "line"): _Route(read_player_line),
    PointShape("player_seasons", "split", "season"): _Route(read_player_history),
    PointShape("player_seasons", "comparison", "subject"): _Route(read_player_compare),
    PointShape("team_games", "rows", "date"): _Route(read_team_log),
    PointShape("team_games", "split", "splits"): _Route(read_team_splits),
    PointShape("team_games", "runs", "won"): _Route(read_team_streak),
    PointShape("team_games", "split", "presence"): _Route(read_with_without),
    PointShape("team_games", "split", "line"): _Route(read_team_record_when),
    PointShape("team_games", "comparison", "opponent"): _Route(read_head_to_head, only=True),
    PointShape("team_periods", "scalar", "total"): _Route(read_team_quarter_points, only=True),
    PointShape("team_games", "scalar", "record"): _Route(read_team_record, only=True),
    PointShape("team_seasons", "scalar", "line"): _Route(read_team_stat, only=True),
    PointShape("team_seasons", "ranking", "team"): _Route(read_team_leaderboard, only=True),
    PointShape("team_snapshots", "scalar", "projection"): _Route(read_team_outlook, only=True),
    PointShape("netpoints", "scalar", "ratings"): _Route(read_player_netpoints, only=True),
    PointShape("netpoints", "chart", "fingerprint"): _Route(read_fingerprint, only=True),
    PointShape("shots", "chart", "shots"): _Route(read_shot_chart, only=True),
    PointShape("shots", "scalar", "distance"): _Route(read_shot_distance, only=True),
}


def _read(con: duckdb.DuckDBPyConnection, shape: PointShape, query: Query | TeamQuery | TeamSeasonQuery | NetPointsQuery | ShotQuery) -> Result | Unanswered | Reply | None:
    """``query`` read by its shape's reader, held to the scoping the shape's
    words state (``STATED_SCOPING``): a Result or the relation's refusal,
    or ``None`` where the reader steps aside (or no reader takes the shape)
    for the compiler's own sentence. A reader that is the point's only
    answer declines rather than step aside (:func:`_read_only`); one that
    steps aside has a cell it refuses said with ``relation:`` before it."""
    route = _ROUTES.get(shape)
    if route is None:
        return None
    stated = STATED_SCOPING[shape]
    if route.only:
        return _read_only(lambda: route.reader(con, query, stated=stated), SHAPE_NAMES[shape])
    return _read_log(lambda: route.reader(con, query, stated=stated))


def _read_drawn(ctx: AnswerContext, shape: PointShape, query: NetPointsQuery | ShotQuery) -> Reply:
    """A point on a declared relation (NetPoints, located shots; Phase 2,
    slice (v)) read, drawn and said - its only answer, as its retired
    template was, and after the same coverage floor: a season before the
    table begins is refused, never read. A chart is drawn to the answering
    loop's output directory between the read and the sayer
    (:func:`~association.query.compose.netpoints.draw_fingerprint`,
    :func:`~association.query.compose.shots.draw_shot_chart`), which names
    the file."""
    refusal = coverage_refusal(shape, query.scope)
    if refusal is not None:
        raise Refused(refusal)
    read = _read(ctx.con, shape, query)
    if isinstance(read, Unanswered):
        return say(read)
    assert isinstance(read, Result)
    if read.chart is None:
        return say(read)
    return say(draw_fingerprint(read, ctx.out_dir) if shape.relation == "netpoints" else draw_shot_chart(read, ctx.out_dir))


def _said(read: Result | Unanswered | Reply | None) -> Reply | None:
    """A read said: a Result or a refusal through the sayer, an answer
    already worded as it stands, ``None`` as ``None``."""
    if read is None or isinstance(read, Reply):
        return read
    return say(read)


def _team_season_first(con: duckdb.DuckDBPyConnection, shape: PointShape, query: Query | TeamQuery) -> tuple[Reply | None, str | None]:
    """A team's own season read for a point planned on another relation (a
    team's own total, "how many 3-pointers have the Magic made"): the same
    scope on the team-season relation first, as the retired template was
    tried before the compiler - its answer, or its decline's reason, which
    is the one said where every reader declines."""
    season_query = TeamSeasonQuery(scope=query.scope, relation="team_snapshots" if shape.relation == "team_snapshots" else "team_seasons", shape="grouped" if shape.shape == "ranking" else "scalar")
    try:
        return _said(_read(con, shape, season_query)), None
    except Unsupported as exc:
        return None, str(exc)


def _answer_point(
    ctx: AnswerContext,
    point: Reading,
    planned: Planned,
    trace: Callable[[Reading], None] | None,
    declined: Callable[[str], None] | None,
) -> Reply | None:
    """``point``'s planned query, run by its shape (``planned.shape``): a
    declared relation's reader, a team's own season (and, for a point
    planned on another relation, the team compiler's sum where it
    declines), a team's games, or a player's - each the shape's reader,
    then the compiler's own sentence where it steps aside. :func:`answer`'s
    tail."""
    query, shape = planned.query, planned.shape
    assert query is not None and shape is not None
    # The team-season reader's decline is the one said where every reader
    # declines: the retired template's reason was, since it ran first.
    season_declined: str | None = None
    try:
        if trace is not None:
            trace(point)
        if isinstance(query, (NetPointsQuery, ShotQuery)):
            return _read_drawn(ctx, shape, query)
        if isinstance(query, TeamSeasonQuery):
            return _said(_read(ctx.con, shape, query))
        if shape.relation in ("team_seasons", "team_snapshots"):
            said, season_declined = _team_season_first(ctx.con, shape, query)
            if said is not None:
                return said
        if isinstance(query, TeamQuery):
            ported_team = _said(_read(ctx.con, shape, query)) if shape.relation not in ("team_seasons", "team_snapshots") else None
            if ported_team is not None:
                return ported_team
            result = run_team(ctx.con, query)
            return Reply(data=_team_point_data(query, result), answer=_team_sentence(query, result), artifacts=[])
        # The season's coverage floor, before any reader runs.
        refusal = coverage_refusal(shape, query.scope)
        if refusal is not None:
            raise Refused(refusal)
        ported = _said(_read(ctx.con, shape, query)) if shape.relation not in ("team_seasons", "team_snapshots") else None
        if ported is not None:
            return ported
        out = run(ctx.con, query)
    except Unsupported as exc:
        if declined is not None:
            declined(season_declined or str(exc))
        return None
    except Refused as exc:
        return say(exc.result)
    answer_text = _sentence(query, out)
    # The page's headline (renderAnswer, web/static/index.html): the sentence
    # alone, before any note is glued on below - the same "head:" line a rows
    # or grouped read prints before its table, or a scalar's one line whole.
    headline = answer_text.split("\n")[0].rstrip(":")
    # No coverage caveat here: the one caller (agent._try_compose) appends
    # coverage_caveat to a composed answer exactly as it does to a template's,
    # and this appending it too printed the same note twice, in the sentence
    # and in data["notes"] - measured on "allen iverson usage game log vs
    # milwaukee 2001 playoffs".
    # Each note on its own line: glued to the sentence with a space, a caveat
    # landed on the last row of a table ("... points 26.9 5 of these games
    # have no box score ...") - seen on the rendered page, 2026-09-24.
    for each_note in out["notes"]:
        answer_text += f"\n{each_note}"
    return Reply(data=_point_data(query, out, headline), answer=answer_text, artifacts=[])
