"""An intent's own default point, said the way that intent's template says it.

The compiler answers a point on the player-games relation with one generic
sentence per skeleton (:mod:`~association.query.compose.sentence`). Where the
point a question compiled to IS an intent's own default point - the one its
template answers - the answer has to read exactly as the template's does,
text and ``data`` both (plan item 2, step 2a: presentation parity), or folding
that template into the compiler would move every answer it gives.

So this module phrases nothing of its own, and what it reads it reads
through a template's body: most presenters hand the compiler's settled
player and narrowing to the retired template's reader, which builds and
executes its own SQL - the compiled SQL is discarded on those paths (55 of
the 205 compiled answers on the yardstick, 2026-09-30), and three presenters
execute SQL here directly. That is the detour ``ROADMAP.md``'s Phase 2
removes. The
subject, the span and every narrowing are the compiler's
(:func:`~association.query.compose.core.compile_query`, through the shared
steps in :mod:`association.query.templates.common`); the numbers are either
the compiler's own rows (a streak, a matchup) or the template's own
reader over the compiler's narrowing (the ported shapes - the game log, a
record over a line, splits, a player's narrowed line, a quarter, a count
over a line, a single game's high - went to readers and the sayer,
``compose.logs``/``records``/``splits``/``stats``/``periods``/``counts``/
``highs`` and ``compose.say``); and every
sentence, caveat and ``data`` key
comes from the template's own phrasing helpers - one definition each, never a
second copy here.

A presenter returns ``None`` for a point that is not the intent's own (the
question's words moved it, or it carries something the template would have
refused), and the compiler's generic sentence answers instead, as before.

.. versionadded:: 5.0.0
"""

from __future__ import annotations

import duckdb

from association.query.templates.common import (
    TemplateResult,
    TemplateUnsupported,
    check_coverage,
    relation_scoping,
    unhonored_scoping,
)
from association.query.templates.splits import (
    _condition_team_no_games,
    _record_when_team_answer,
    _streak_league_result_words,
    _streak_team_answer,
    _team_span_label,
    _team_where_in,
    _with_without_said,
)

from .core import Refused, Unsupported
from .plan import WITH_WITHOUT_STATED
from .say import say_run_listing, streak_result
from .team import TeamQuery, _team_games_narrowed, run_team

TEAM_ONLY_PRESENTERS: frozenset[str] = frozenset({"with_without"})
"""The intents whose only presenter is the team relation's
(:func:`present_team`): ``with_without``'s split is the team's record, so
its point is a :class:`~association.query.compose.team.TeamQuery` whoever
the question names. :data:`STATED_SCOPING` declares for it as for every
compiled intent.

.. versionadded:: 5.0.0
"""

STATED_SCOPING: dict[str, frozenset[str]] = {
    # game_log and player_stat retired stating the relation's whole set, and
    # `season_type_unstated`: read over both season types merged by date for
    # a log (compose.logs, which takes this set since Phase 2's first
    # slice), and from box scores as one combined read for an average
    # (`_player_stat_reads_box_scores`, `player_relation_season_type`).
    "game_log": relation_scoping("game_log", "season_type_unstated"),
    "player_stat": relation_scoping("player_stat", "season_type_unstated"),
    # player_splits' words: the relation's set less a date and a window
    # (RELATION_SCOPING_EXCLUDED: one game has nothing to split).
    "player_splits": relation_scoping("player_splits"),
    # The retired templates' words, as they stated their narrowings when they
    # retired (ROADMAP plan item 6, step (d), part 4).
    "record_when": relation_scoping("record_when"),
    "player_history": frozenset({"span"}),
    # leaderboard's words: a career pool, and a season total or a unit it
    # refuses by name (the template's own HONORED_SCOPING when it retired).
    "leaderboard": frozenset({"span", "rate"}),
    # period_split's words: the relation's set less a career and a since/until
    # range (RELATION_SCOPING_EXCLUDED: the accuracy caveat is per season),
    # which its point refuses outright (compose.adapt._adapt_period_split).
    # A period CONDITION (a quarter conditioning which games count, beside
    # the quarter measured - "first quarter points in games he made a
    # fourth-quarter three") is not among those words either
    # (RELATION_SCOPING_EXCLUDED): the presenter steps aside and the
    # compiler's sentence, which names both, answers.
    "period_split": relation_scoping("period_split"),
    # player_compare's words state no narrowing at all; its point refuses
    # one outright (query/point.py._compare_point), as check_scope did.
    "player_compare": frozenset(),
    # streak's words: the relation's set less one date, a window and a
    # quarter (RELATION_SCOPING_EXCLUDED: a run is a run of whole games over
    # every game in the span), which the planner refuses outright
    # (compose.plan._shape_declines), and the cells only a named player's
    # games settle on a team's or the league's run, by name, there too.
    "streak": relation_scoping("streak"),
    # player_matchup's words: the relation's set less a third team, a window,
    # an ordinal season and a quarter (RELATION_SCOPING_EXCLUDED), which the
    # planner refuses outright (compose.plan._shape_declines).
    "player_matchup": relation_scoping("player_matchup"),
    # with_without's words: a career, the teammates, one opponent and a
    # companion's role, the template's own declaration when it retired.
    "with_without": WITH_WITHOUT_STATED,
    "single_game_high": frozenset({"span"}),
    # A count is already a line on a column; `below` is the same line the
    # other way ("games with under 14 fta"), and a phrase carrying the count's
    # own number IS the count, misread - compose.counts reads it so.
    # `season_type_unstated` is stated the way `scoped_player` reads it -
    # one combined `season_type IN (2, 3)` read (player_relation_season_type).
    "threshold_count": frozenset({"span", "below", "above", "season_n", "season_type_unstated"}),
}
"""Intent -> the scoping its presenter's WORDS state. A presenter answers in
its template's sentence, which names the narrowings that template honored
and no other: asked a point narrowed beyond them (an opponent on a
single-game high, a condition on a history), it steps aside
(:func:`present`) and the compiler's own sentence, which states every
narrowing the relation applied, answers. A narrowing the relation cannot
honor at all is the planner's refusal (:func:`~association.query.compose.plan.plan`),
before any presenter runs; until 5.0.0 these lists lived in
``HONORED_SCOPING`` under the retired templates' names, where
``agent._run_compiled`` also read them as the refusal's reason - which
could name a slot where the compiler had declined for another cause.

.. versionadded:: 5.0.0
"""


def _present_team_streak(con: duckdb.DuckDBPyConnection, q: TeamQuery) -> TemplateResult | None:
    """A team's longest run of wins or losses, or the league's with no team
    named (``streak``'s retired team and league branches), said in the
    template's words over the team compiler's ``run`` shape
    (``compose.team._compile_team_run``, through :func:`~association.query.compose.team.run_team`,
    which checks the coverage floor first).

    .. versionadded:: 5.0.0
    """
    if unhonored_scoping("streak", q.scope, STATED_SCOPING["streak"]):
        return None
    want_win = q.scope.kind != "loss"
    found = run_team(con, q)
    if found.team is not None:
        # The narrowing the run was read over, for the sentence - and, with
        # no games in it, which fact is missing (the team's games in this
        # span at all, or the match to an opponent/venue narrowing).
        narrowed, team, span = _team_games_narrowed(con, q)
        if not found.games:
            return _condition_team_no_games(con, team, span, narrowed)
        return _streak_team_answer(found.team, narrowed, found.span, found.first_season, found.last_season, found.runs, want_win)
    runs, rule = _streak_league_result_words(con, found.span, found.runs)
    label = _team_span_label(found.span, found.first_season, found.last_season)
    return say_run_listing(runs, streak_result(want_win), rule, label, _team_where_in(found.span), by_stat=False, stat=None, threshold=None, unit="", want_win=want_win)


def _present_with_without(con: duckdb.DuckDBPyConnection, q: TeamQuery) -> TemplateResult | None:
    """A team's record with and without named teammates, said in the
    retired template's words (``templates.splits._with_without_said``) over
    the team compiler's ``presence`` group
    (``compose.team._compile_team_presence``, through
    :func:`~association.query.compose.team.run_team`, which checks the
    coverage floor first).

    .. versionadded:: 5.0.0
    """
    if unhonored_scoping("with_without", q.scope, STATED_SCOPING["with_without"]):
        return None
    found = run_team(con, q)
    if found.presence is None:
        return None
    return _with_without_said(found.presence)


def present_team(con: duckdb.DuckDBPyConnection, intent: str, q: TeamQuery) -> TemplateResult | None:
    """A team subject's point said the way its intent's template says it -
    ``record_when``'s team branch (a team's game log, the retired
    template's other team half, is ``compose.logs.read_team_log``'s since
    Phase 2's first slice), a
    team's record above and below its OWN line ("what was the celtics record
    when they scored 120 points", ISSUES.md #144), which the team subject's
    own readers (a season sum, a window sum) cannot represent. Read by the template's own team reader
    (``templates.splits._record_when_team_answer``) behind the same two
    checks the template ran behind: the slots its words state
    (:data:`STATED_SCOPING`) and the coverage floor. ``None`` for any other point, which
    :func:`~association.query.compose.team.run_team` answers.

    .. versionadded:: 5.0.0
    """
    if intent == "streak" and q.shape == "run":
        try:
            return _present_team_streak(con, q)
        except TemplateUnsupported as exc:
            raise Unsupported(f"relation: {exc}") from exc
    if intent == "with_without" and q.shape == "grouped" and q.group == "presence":
        try:
            return _present_with_without(con, q)
        except TemplateUnsupported as exc:
            raise Unsupported(f"relation: {exc}") from exc
    if intent != "record_when" or q.scope.threshold is None:
        return None
    if unhonored_scoping(intent, q.scope, STATED_SCOPING[intent]):
        return None
    refused = check_coverage(intent, q.scope)
    if refused is not None:
        raise Refused(TemplateResult(data={"message": refused, "season": q.scope.season}, answer=refused))
    try:
        return _record_when_team_answer(con, q.scope)
    except TemplateUnsupported as exc:
        raise Unsupported(f"relation: {exc}") from exc
