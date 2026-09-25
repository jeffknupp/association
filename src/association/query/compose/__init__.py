"""One compiler over the player-games relation - the step between a
template's refusal and the slower SQL-writing agent.

The pipeline is router -> template -> compiler -> agent. A template on the
relation answers a question at its own fixed point (the six intents in
``association.query.templates``); when a template refuses because the
question's shape is close but not exact - a measure word its list does not
carry, "most ... in a game" rather than a log, a league-wide read with no
player named - :func:`answer` tries the same relation at the point the
question's own words move it to, before the question falls through to the
agent.

Every correctness rule a template on the relation carries - the scoping the
relation narrows by, the rebuilt-line guard, binding parity, the
starter/bench category - is read from the relation exactly once, in
:mod:`association.query.templates.common` and :mod:`association.query.player_games`,
which is what lets this package answer them all through one compiler instead
of a template per shape. See ``core.py``'s module docstring for the rules
themselves and where each is enforced.

Nothing here reaches ollama or the agent: :func:`answer` is a pure function of
a connection, an already-routed intent and slots, and the question's own text.

.. versionadded:: 4.4.0
"""

from __future__ import annotations

from typing import Any

from association.query.templates.common import TemplateContext, TemplateResult, check_coverage, coverage_caveat

from .core import Query, Refused, Unsupported, run
from .move import move_point
from .sentence import _span_phrase
from .sentence import sentence as _sentence
from .sentence import team_sentence as _team_sentence
from .team import TeamQuery, TeamResult, run_team

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
        "span": _span_phrase(out["span"]),
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
        "team": result.team.name,
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


def answer(ctx: TemplateContext, intent: str, slots: dict[str, Any], question: str) -> TemplateResult | None:
    """A router-classified question, answered by the compiler where a
    template refused it - or ``None``, meaning the question is not a point on
    this relation at all and should fall through to the agent.

    ``Refused`` (the relation itself refusing - no such player, an ambiguous
    name, a coverage floor) is returned as the answer: it is a handled
    outcome carrying the template-shaped refusal, not a reason to fall
    through. ``Unsupported`` (the compiler cannot say this question) becomes
    ``None`` instead, since falling through is exactly what it means.

    This function is the whole surface the agent's fall-through wiring calls;
    nothing else in this package is meant to be called from outside it.

    .. versionadded:: 4.4.0

    .. versionchanged:: 4.4.0
       ``move_point`` may return a :class:`~association.query.compose.team.TeamQuery`
       (the team as a subject, step 3, K1) instead of a
       :class:`~association.query.compose.core.Query` - answered through
       :func:`~association.query.compose.team.run_team` and
       :func:`~association.query.compose.sentence.team_sentence` instead, the
       same ``Unsupported``/``Refused`` handling either way.

    .. versionchanged:: 4.4.0
       Checks :func:`~association.query.templates.common.check_coverage`
       before compiling and appends
       :func:`~association.query.templates.common.coverage_caveat` after -
       the same two calls every relation template makes, which this package
       carried neither of before (#197, ISSUES.md: a season under a table's
       floor was answered as confidently as a modern one). The team subject
       makes the same two calls its own way
       (:func:`~association.query.compose.team.team_coverage_refusal`,
       inside :func:`~association.query.compose.team.run_team`).

    .. versionchanged:: 4.4.0
       Appends ``out["notes"]`` - the box-score caveats
       :func:`~association.query.compose.core._box_notes` reads off the
       ``Narrowed``/player/span :func:`~association.query.compose.core.run`
       builds internally (#197, ISSUES.md, the box-score-CAVEAT half: the
       coverage-floor half was fixed first and is a separate call, above).
       ``TeamQuery`` carries none, since the team relation has no box-score
       equivalent to check (:mod:`association.query.compose.team` reads
       ``games``/``team_season_stats``, never a player's box score).
    """
    try:
        query = move_point(ctx.con, intent, slots, question)
        if isinstance(query, TeamQuery):
            result = run_team(ctx.con, query)
            return TemplateResult(data=_team_point_data(query, result), answer=_team_sentence(query, result), artifacts=[])
        refusal = check_coverage(intent, query.slots)
        if refusal is not None:
            raise Refused(TemplateResult(data={"season": query.slots.get("season")}, answer=refusal))
        out = run(ctx.con, query)
    except Unsupported:
        return None
    except Refused as exc:
        return exc.result
    answer_text = _sentence(query, out)
    # The page's headline (renderAnswer, web/static/index.html): the sentence
    # alone, before any note is glued on below - the same "head:" line a rows
    # or grouped read prints before its table, or a scalar's one line whole.
    headline = answer_text.split("\n")[0].rstrip(":")
    note = coverage_caveat(intent, query.slots)
    if note:
        out["notes"] = [*out["notes"], note]
    # Each note on its own line: glued to the sentence with a space, a caveat
    # landed on the last row of a table ("... points 26.9 5 of these games
    # have no box score ...") - seen on the rendered page, 2026-09-24.
    for each_note in out["notes"]:
        answer_text += f"\n{each_note}"
    return TemplateResult(data=_point_data(query, out, headline), answer=answer_text, artifacts=[])
