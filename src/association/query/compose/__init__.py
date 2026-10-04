"""One compiler over the player-games and team-games relations: the only
answer thirteen intents have (:data:`COMPILED_INTENTS`), the step after a
live template's refusal, and the last one before a refusal naming why.

The pipeline is parser -> template or compiler -> refusal. :func:`answer`
is handed the point the parser read from the question's words
(:attr:`Reading.point <association.query.reading.Reading.point>`), plans it
(:mod:`~association.query.compose.plan`) and answers it one of three ways:
an intent's own default point through its presenter
(:mod:`~association.query.compose.present`, which mostly calls the retired
template's body - its SQL and its words), a team's point through
:mod:`~association.query.compose.team`, and any other point through the
compiler's own SQL (:mod:`~association.query.compose.core`) and sentence
(:mod:`~association.query.compose.sentence`). Which of the three answered
is not visible in the answer; ``ROADMAP.md`` ("Where it stands") has the
measured split, and its Phase 2 is the work of making the last one the
only one.

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

from association.query.result import Result
from association.query.templates.common import TemplateContext, TemplateResult, TemplateUnsupported, check_coverage

from .core import Query, Refused, Unsupported, run
from .logs import read_player_log, read_team_log
from .plan import Planned, games_reading
from .present import STATED_SCOPING, present, present_team
from .records import read_record_when
from .say import say
from .sentence import _span_phrase
from .sentence import sentence as _sentence
from .sentence import team_sentence as _team_sentence
from .splits import read_player_splits, read_team_splits
from .stats import read_player_stat
from .team import TeamQuery, TeamResult, run_team

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
the compiler gained for them). Each is said in its retired template's own words,
through that template's phrasing helpers
(:mod:`~association.query.compose.present`); where the compiler has no
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
    ran: Callable[[Query | TeamQuery], None] | None = None,
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
    subject is answered through :func:`~association.query.compose.team.run_team`
    (a team's record above and below its own line by
    :func:`~association.query.compose.present.present_team`), an intent's own
    default point in its template's words
    (:func:`~association.query.compose.present.present`), and the rest by the
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
       Takes ``ran``: handed the query the compiler itself executed, which
       is the planned one except where the season line's presenter declined
       and :func:`~association.query.compose.plan.games_reading` re-read it
       as the game-level query (3 of the 628 recorded questions). The stage
       snapshot records that one, not the planned one, since 2026-10-03.
    """
    verdict = planned
    if verdict.refusal is not None:
        # A copy: the caller appends its notes to the answer it is handed.
        return copy.deepcopy(verdict.refusal)
    if verdict.query is None or reading.point is None:
        if declined is not None:
            declined(verdict.declined or "the compiler has no reading of this point")
        return None
    return _answer_point(ctx, reading.intent, reading.point, verdict.query, trace, declined, ran)


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
    ``compose.stats``; ``compose.say``): a player's log - ``game_log``'s own
    point, or the window of games ``player_stat``'s retired template handed
    to the log ("stats over his last N games") - a player's record over a
    line, his splits, and his line over the games a narrowing sent the read
    to (``player_stat``'s narrowed point). ``None`` where the
    point is not one of them, or its words do not say it, and a presenter or
    the compiler's own sentence answers."""
    if query.skeleton == "rows" and intent in ("game_log", "player_stat"):
        return _read_log(lambda: read_player_log(con, query, stated=STATED_SCOPING[intent]))
    if intent == "record_when" and query.skeleton == "scalar":
        return _read_log(lambda: read_record_when(con, query, stated=STATED_SCOPING["record_when"]))
    if intent == "player_splits" and query.skeleton == "grouped":
        return _read_log(lambda: read_player_splits(con, query, stated=STATED_SCOPING["player_splits"]))
    if intent == "player_stat" and query.skeleton == "scalar" and query.source == "games":
        return _read_log(lambda: read_player_stat(con, query, stated=STATED_SCOPING["player_stat"]))
    return None


def _read_ported_team(con: duckdb.DuckDBPyConnection, intent: str, query: TeamQuery) -> TemplateResult | None:
    """The team shapes Phase 2 has ported: a team's log and a team's splits,
    read into a Result and said by the sayer. ``None`` where the point is
    not one of them, or its words do not say it."""
    if intent == "game_log" and query.shape == "rows":
        return _read_log(lambda: read_team_log(con, query, stated=STATED_SCOPING["game_log"]))
    if intent == "player_splits" and query.shape == "grouped":
        return _read_log(lambda: read_team_splits(con, query, stated=STATED_SCOPING["player_splits"]))
    return None


def _answer_point(
    ctx: TemplateContext,
    intent: str,
    point: Reading,
    query: Query | TeamQuery,
    trace: Callable[[Reading], None] | None,
    declined: Callable[[str], None] | None,
    ran: Callable[[Query | TeamQuery], None] | None = None,
) -> TemplateResult | None:
    """``point``'s planned ``query``, run: the team subject's reader, the
    intent's own presenter, or the compiler's own sentence -
    :func:`answer`'s tail."""
    try:
        if trace is not None:
            trace(point)
        if isinstance(query, TeamQuery):
            ported_team = _read_ported_team(ctx.con, intent, query)
            if ported_team is not None:
                return ported_team
            # A team's record above and below its own line is said by
            # record_when's own team reader (compose.present.present_team).
            own_team = present_team(ctx.con, intent, query)
            if own_team is not None:
                return own_team
            if ran is not None:
                ran(query)
            result = run_team(ctx.con, query)
            return TemplateResult(data=_team_point_data(query, result), answer=_team_sentence(query, result), artifacts=[])
        # The shared checks read the slot dict until they take the Scope.
        refusal = check_coverage(intent, query.scope)
        if refusal is not None:
            raise Refused(TemplateResult(data={"message": refusal, "season": query.scope.season}, answer=refusal))
        ported = _read_ported(ctx.con, intent, query)
        if ported is not None:
            return ported
        # The intent's own default point is said the way its template says
        # it (compose.present, plan item 2 step 2a) - None for any other.
        own = present(ctx.con, intent, query)
        if own is not None:
            return own
        if query.source != "games":
            # The season line's own reader declined: the game-level reading,
            # checked against its own floor, or nothing.
            query = games_reading(query)
            refusal = check_coverage(intent, query.scope)
            if refusal is not None:
                raise Refused(TemplateResult(data={"message": refusal, "season": query.scope.season}, answer=refusal))
        if ran is not None:
            ran(query)
        out = run(ctx.con, query)
    except Unsupported as exc:
        if declined is not None:
            declined(str(exc))
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
