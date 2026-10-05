"""One compiler over the player-games and team-games relations: the only
answer the intents in :data:`COMPILED_INTENTS` have, the step after a
live template's refusal, and the last one before a refusal naming why.

The pipeline is parser -> template or compiler -> refusal. :func:`answer`
is handed the point the parser read from the question's words
(:attr:`Reading.point <association.query.reading.Reading.point>`), plans it
(:mod:`~association.query.compose.plan`) and answers it one of three ways:
an intent's own point through its reader and the sayer (``compose.logs``,
``compose.runs``, ``compose.presence``, ... and
:mod:`~association.query.compose.say`), a team's sum through
:mod:`~association.query.compose.team`, and any other point through the
compiler's own SQL (:mod:`~association.query.compose.core`) and sentence
(:mod:`~association.query.compose.sentence`). Which of the three answered
is not visible in the answer. The presenters that called the retired
templates' bodies are gone (``compose/present.py``, deleted with Phase 2's
slice (iv)).

Every correctness rule a template on the relation carries - the scoping the
relation narrows by, the rebuilt-line guard, binding parity, the
starter/bench category - is read from the relation exactly once, in
:mod:`association.query.templates.common` and :mod:`association.query.player_games`,
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
from typing import TYPE_CHECKING, Any

import duckdb

from association.query.point import TEAM_SEASON_POINTS
from association.query.result import Result
from association.query.templates.common import TemplateContext, TemplateResult, TemplateUnsupported, check_coverage

from .core import Query, Refused, Unsupported, run
from .counts import read_threshold_count
from .highs import read_single_game_high
from .logs import read_player_log, read_team_log
from .meetings import read_head_to_head
from .netpoints import NetPointsQuery, draw_fingerprint, read_fingerprint, read_player_netpoints
from .pairs import read_player_matchup
from .periods import read_period_leaderboard, read_period_split, read_team_quarter_points
from .plan import STATED_SCOPING, Planned
from .presence import read_with_without
from .rankings import read_leaderboard
from .records import read_record_when, read_team_record_when
from .runs import read_streak, read_team_streak
from .say import say
from .seasons import read_player_compare, read_player_history, read_player_line
from .sentence import _span_phrase
from .sentence import sentence as _sentence
from .sentence import team_sentence as _team_sentence
from .shots import ShotQuery, read_shot_distance
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
chart drawn between its reader and the sayer), and ``shot_distance``
(:mod:`~association.query.compose.shots`, slice (v): the shot relation, a
declared relation with its own reader). Each is read by its reader and said in its
retired template's own words by the sayer
(:mod:`~association.query.compose.say`); where the compiler has no
reading of a point, the question is refused with the reason
(``agent._run_compiled``).

.. versionadded:: 5.0.0
"""


def answer(
    ctx: TemplateContext,
    reading: Reading,
    *,
    planned: Planned,
    trace: Callable[[Reading], None] | None = None,
    declined: Callable[[str], None] | None = None,
) -> TemplateResult | None:
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
    carrying the template-shaped refusal, not a reason to decline.
    ``Unsupported`` (the compiler cannot say this question) becomes ``None``
    instead, since declining is exactly what it means. The team
    subject is answered by its readers (a team's log, splits, streak,
    with/without split and record over its own line) or
    :func:`~association.query.compose.team.run_team`, an intent's own
    point by its reader and the sayer, and the rest by the
    compiler's own sentence, with the box-score caveats
    :func:`~association.query.compose.core.run` reads appended (#197); the
    answering loop appends :func:`~association.query.templates.common.coverage_caveat`
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
    """
    verdict = planned
    if verdict.refusal is not None:
        # A copy: the caller appends its notes to the answer it is handed.
        return copy.deepcopy(verdict.refusal)
    if verdict.query is None or reading.point is None:
        if declined is not None:
            declined(verdict.declined or "the compiler has no reading of this point")
        return None
    return _answer_point(ctx, reading.intent, reading.point, verdict.query, trace, declined)


def _read_log(read: Callable[[], Result | TemplateResult | None]) -> TemplateResult | None:
    """A log read and said: the relation's own refusal (a ``TemplateResult``)
    as it stands, a Result through the sayer, ``None`` as ``None``. A cell
    the relation refuses while reading is the compiler's decline, as
    ``present`` made it."""
    try:
        read_log = read()
    except TemplateUnsupported as exc:
        raise Unsupported(f"relation: {exc}") from exc
    if read_log is None or isinstance(read_log, TemplateResult):
        return read_log
    return say(read_log)


def _read_ported(con: duckdb.DuckDBPyConnection, intent: str, query: Query) -> TemplateResult | None:
    """The shapes Phase 2 has ported, read into a Result and said by the
    sayer (``compose.logs``, ``compose.records``, ``compose.splits``,
    ``compose.stats``, ``compose.periods``, ``compose.counts``, ``compose.highs``, ``compose.runs``,
    ``compose.pairs``, ``compose.rankings``; ``compose.say``): a player's log - ``game_log``'s own
    point, or the window of games ``player_stat``'s retired template handed
    to the log ("stats over his last N games") - a player's record over a
    line, his splits, and his line over the games a narrowing sent the read
    to (``player_stat``'s narrowed point), his quarter or half, a count
    of games over a line and a single game's high, his or the league's,
    his or the league's longest runs of a line (``streak``), two
    players' meetings (``player_matchup``), and the league's leaders by a
    season-line metric (``leaderboard``). ``None``
    where the
    point is not one of them, or its words do not say it, and another reader or
    the compiler's own sentence answers."""
    if query.skeleton == "rows" and intent in ("game_log", "player_stat"):
        return _read_log(lambda: read_player_log(con, query, stated=STATED_SCOPING[intent]))
    if intent == "record_when" and query.skeleton == "scalar":
        return _read_log(lambda: read_record_when(con, query, stated=STATED_SCOPING["record_when"]))
    if intent == "player_splits" and query.skeleton == "grouped":
        return _read_log(lambda: read_player_splits(con, query, stated=STATED_SCOPING["player_splits"]))
    if intent == "player_stat" and query.skeleton == "scalar" and query.source == "games":
        return _read_log(lambda: read_player_stat(con, query, stated=STATED_SCOPING["player_stat"]))
    if intent == "period_split":
        return _read_log(lambda: read_period_split(con, query, stated=STATED_SCOPING["period_split"]))
    if intent == "threshold_count":
        return _read_log(lambda: read_threshold_count(con, query, stated=STATED_SCOPING["threshold_count"]))
    if intent == "single_game_high":
        return _read_log(lambda: read_single_game_high(con, query, stated=STATED_SCOPING["single_game_high"]))
    if intent == "streak" and query.skeleton == "run":
        return _read_log(lambda: read_streak(con, query, stated=STATED_SCOPING["streak"]))
    if intent == "player_matchup" and query.skeleton == "pair":
        return _read_log(lambda: read_player_matchup(con, query, stated=STATED_SCOPING["player_matchup"]))
    if intent in _PORTED_SHAPE_READERS:
        return _read_ported_shape(con, intent, query)
    if query.source == "seasons":
        return _read_season_line(con, intent, query)
    return None


#: The season line's ported readers, by intent (``compose.seasons``).
_SEASON_LINE_READERS: dict[str, Callable[..., Result | TemplateResult | None]] = {
    "leaderboard": read_leaderboard,
    "player_stat": read_player_line,
    "player_history": read_player_history,
    "player_compare": read_player_compare,
}


def _read_season_line(con: duckdb.DuckDBPyConnection, intent: str, query: Query) -> TemplateResult | None:
    """A point on the season line read by its intent's reader and said by
    the sayer: the league's ranking, a player's unnarrowed line, his
    history, a comparison. ``None`` for any other intent, or where the
    reader declines the point."""
    reader = _SEASON_LINE_READERS.get(intent)
    if reader is None:
        return None
    return _read_log(lambda: reader(con, query, stated=STATED_SCOPING[intent]))


#: The team shapes slice (iv) ported, by intent: each the only answer its
#: intent has, so a decline is the planner's and a refusal the relation's.
_PORTED_SHAPE_READERS: dict[str, Callable[..., Result | TemplateResult | None]] = {
    "head_to_head": read_head_to_head,
    "team_quarter_points": read_team_quarter_points,
    "period_leaderboard": read_period_leaderboard,
    "team_record": read_team_record,
}


def _read_ported_shape(con: duckdb.DuckDBPyConnection, intent: str, query: Query | TeamQuery) -> TemplateResult:
    """A team shape slice (iv) ported, read and said - the intent's only
    answer, as its retired template was: a cell its reader refuses while
    reading is the compiler's decline, with the reader's own reason (the
    template's sentence, no prefix), and a point its reader does not read
    is declined too, never handed to the team compiler's sums."""
    try:
        read = _PORTED_SHAPE_READERS[intent](con, query, stated=STATED_SCOPING[intent])
    except TemplateUnsupported as exc:
        raise Unsupported(str(exc)) from exc
    if read is None:
        raise Unsupported(f"{intent} has no reading of this point")
    return read if isinstance(read, TemplateResult) else say(read)


def _read_ported_team(con: duckdb.DuckDBPyConnection, intent: str, query: TeamQuery) -> TemplateResult | None:
    """The team shapes Phase 2 has ported: a team's log and a team's splits,
    read into a Result and said by the sayer. ``None`` where the point is
    not one of them, or its words do not say it."""
    if intent in _PORTED_SHAPE_READERS:
        return _read_ported_shape(con, intent, query)
    if intent == "game_log" and query.shape == "rows":
        return _read_log(lambda: read_team_log(con, query, stated=STATED_SCOPING["game_log"]))
    if intent == "player_splits" and query.shape == "grouped":
        return _read_log(lambda: read_team_splits(con, query, stated=STATED_SCOPING["player_splits"]))
    if intent == "streak" and query.shape == "run":
        return _read_log(lambda: read_team_streak(con, query, stated=STATED_SCOPING["streak"]))
    if intent == "with_without" and query.shape == "grouped" and query.group == "presence":
        return _read_log(lambda: read_with_without(con, query, stated=STATED_SCOPING["with_without"]))
    if intent == "record_when":
        # A team's record above and below its OWN line (ISSUES.md #144).
        return _read_log(lambda: read_team_record_when(con, query, stated=STATED_SCOPING["record_when"]))
    return None


#: The team-season relations' ported readers, by intent (``compose.team_stats``).
_TEAM_SEASON_READERS: dict[str, Callable[..., Result | TemplateResult]] = {
    "team_outlook": read_team_outlook,
    "team_stat": read_team_stat,
    "team_leaderboard": read_team_leaderboard,
}


def _read_team_season(con: duckdb.DuckDBPyConnection, intent: str, query: Query | TeamQuery | TeamSeasonQuery) -> TemplateResult:
    """``intent``'s team-season reader over ``query``'s scope, said by the
    sayer - its own point (a :class:`TeamSeasonQuery`), or, ahead of
    another relation's point the question's words read (a team's own total
    under ``team_stat``), the same scope on the team-season relation, as
    the retired template was tried before the compiler. A decline is the
    reader's ``Unsupported``, a refusal its ``TemplateResult`` or
    ``Refused``."""
    if not isinstance(query, TeamSeasonQuery):
        relation, shape = TEAM_SEASON_POINTS[intent]
        query = TeamSeasonQuery(scope=query.scope, relation=relation, shape=shape)
    read = _TEAM_SEASON_READERS[intent](con, query, stated=STATED_SCOPING[intent])
    return read if isinstance(read, TemplateResult) else say(read)


#: The NetPoints relation's readers, by intent (``compose.netpoints``).
_NETPOINTS_READERS: dict[str, Callable[..., Result | TemplateResult | None]] = {
    "player_netpoints": read_player_netpoints,
    "fingerprint": read_fingerprint,
}


def _read_netpoints(ctx: TemplateContext, intent: str, query: NetPointsQuery) -> TemplateResult:
    """A point on the NetPoints relation (Phase 2, slice (v)), read and
    said - the intent's only answer, as its retired template was, and after
    the same coverage floor: a season before NetPoints begins is refused,
    never read. A cell the reader refuses while reading (no player named, a
    per-game table not pulled) is the compiler's decline with the reader's
    own reason, no prefix, as the template's refusal was. A chart is drawn
    to the answering loop's output directory between the read and the
    sayer (:func:`~association.query.compose.netpoints.draw_fingerprint`)."""
    refusal = check_coverage(intent, query.scope)
    if refusal is not None:
        raise Refused(TemplateResult(data={"message": refusal, "season": query.scope.season}, answer=refusal))
    try:
        read = _NETPOINTS_READERS[intent](ctx.con, query, stated=STATED_SCOPING[intent])
    except TemplateUnsupported as exc:
        raise Unsupported(str(exc)) from exc
    if read is None:
        raise Unsupported(f"{intent} has no reading of this point")
    if isinstance(read, TemplateResult):
        return read
    return say(draw_fingerprint(read, ctx.out_dir) if read.chart is not None else read)
#: The shot relation's readers, by intent (``compose.shots``): each the only
#: answer its intent has, as its retired template was.
_SHOT_READERS: dict[str, Callable[..., Result | TemplateResult | None]] = {
    "shot_distance": read_shot_distance,
}


def _read_shots(intent: str, query: ShotQuery, con: duckdb.DuckDBPyConnection) -> TemplateResult:
    """A point on the shot relation, read and said: the season's coverage
    floor first, as it was checked before the retired template ran; a cell
    the reader refuses while reading is the compiler's decline, with the
    template's own reason (no prefix); a refusal of its own (a name, no
    games, a season that cannot separate twos from threes) is the answer."""
    reader = _SHOT_READERS.get(intent)
    if reader is None:
        raise Unsupported("the shot relation's readers are not ported yet")
    refusal = check_coverage(intent, query.scope)
    if refusal is not None:
        raise Refused(TemplateResult(data={"message": refusal, "season": query.scope.season}, answer=refusal))
    try:
        read = reader(con, query, stated=STATED_SCOPING[intent])
    except TemplateUnsupported as exc:
        raise Unsupported(str(exc)) from exc
    if read is None:
        raise Unsupported(f"{intent} has no reading of this point")
    return read if isinstance(read, TemplateResult) else say(read)


def _answer_point(
    ctx: TemplateContext,
    intent: str,
    point: Reading,
    query: Query | TeamQuery | TeamSeasonQuery | NetPointsQuery | ShotQuery,
    trace: Callable[[Reading], None] | None,
    declined: Callable[[str], None] | None,
) -> TemplateResult | None:
    """``point``'s planned ``query``, run: a team-season intent's reader
    first (and another relation's point only where it declines), the team
    subject's reader, the intent's own reader, or the compiler's own
    sentence - :func:`answer`'s tail."""
    # The team-season reader's decline is the one said where every reader
    # declines: the retired template's reason was, since it ran first.
    season_declined: str | None = None
    try:
        if trace is not None:
            trace(point)
        if isinstance(query, NetPointsQuery):
            return _read_netpoints(ctx, intent, query)
        if isinstance(query, ShotQuery):
            return _read_shots(intent, query, ctx.con)
        if intent in _TEAM_SEASON_READERS:
            try:
                return _read_team_season(ctx.con, intent, query)
            except Unsupported as exc:
                if isinstance(query, TeamSeasonQuery):
                    raise
                season_declined = str(exc)
        if isinstance(query, TeamSeasonQuery):
            raise Unsupported("the team-season relation's readers are not ported yet")
        if isinstance(query, TeamQuery):
            ported_team = _read_ported_team(ctx.con, intent, query)
            if ported_team is not None:
                return ported_team
            result = run_team(ctx.con, query)
            return TemplateResult(data=_team_point_data(query, result), answer=_team_sentence(query, result), artifacts=[])
        # The shared checks read the slot dict until they take the Scope.
        refusal = check_coverage(intent, query.scope)
        if refusal is not None:
            raise Refused(TemplateResult(data={"message": refusal, "season": query.scope.season}, answer=refusal))
        ported = _read_ported(ctx.con, intent, query)
        if ported is not None:
            return ported
        out = run(ctx.con, query)
    except Unsupported as exc:
        if declined is not None:
            declined(season_declined or str(exc))
        return None
    except Refused as exc:
        return exc.result
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
    return TemplateResult(data=_point_data(query, out, headline), answer=answer_text, artifacts=[])
