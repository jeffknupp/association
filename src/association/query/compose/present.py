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
the compiler's own rows (``single_game_high``, ``threshold_count``, whose
templates have no reader separate from their orchestration) or the template's
own reader over the compiler's narrowing (the ported shapes - the game log,
a record over a line, splits, a player's narrowed line - went to readers
and the sayer, ``compose.logs``/``records``/``splits``/``stats`` and
``compose.say``); and every
sentence, caveat and ``data`` key
comes from the template's own phrasing helpers - one definition each, never a
second copy here.

A presenter returns ``None`` for a point that is not the intent's own (the
question's words moved it, or it carries something the template would have
refused), and the compiler's generic sentence answers instead, as before.

.. versionadded:: 5.0.0
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import duckdb

from association.nba.season import current_season, eastern_date
from association.query.conditions import _meeting_rows, _teammate_games, _totals
from association.query.measures import stat_measure
from association.query.player_games import REBUILT_STATS
from association.query.reading import Scope
from association.query.templates.common import (
    HISTORY_COLUMNS,
    STAT_LABELS,
    THRESHOLD_STAT_COLUMNS,
    ResolvedSpan,
    TemplateResult,
    TemplateUnsupported,
    check_coverage,
    optional_team,
    player_relation_season_type,
    relation_scoping,
    unhonored_scoping,
    where_in,
)
from association.query.templates.games import (
    _period_scope,
    _period_split_by_quarter_from,
    _period_split_from,
    _period_split_measure,
    _period_split_reconciliation_refusal,
    _player_matchup_covered,
    _player_matchup_from,
)
from association.query.templates.players import (
    ADVANCED_STATS,
    SHOOTING_STATS,
    LeaderboardStepsAside,
    _empty_box_scores,
    _game_span,
    _leaderboard_ranking,
    _phrase_threshold_count,
    _player_compare_lines,
    _player_history_read,
    _player_history_subject,
    _player_stat_season_line,
    _player_stat_season_line_subject,
    _rebuilt_in_scope,
    _single_game_high_answer,
    _single_game_high_redirect,
    _single_game_high_result_data,
    _threshold_count_ask,
    _threshold_count_lines,
    _threshold_count_notes,
    wanted_stats,
)
from association.query.templates.splits import (
    _condition_team_no_games,
    _misfiled_postseason,
    _record_when_team_answer,
    _streak_league_answer,
    _streak_league_result_words,
    _streak_league_stat_words,
    _streak_player_answer,
    _streak_team_answer,
    _streak_words,
    _team_span_label,
    _team_where_in,
    _with_without_said,
)

from .adapt import WITH_WITHOUT_STATED
from .core import Query, Refused, Unsupported, compile_query, run, run_scope
from .team import TeamQuery, _team_games_narrowed, run_team

#: A presenter: the connection and the compiled point (its scope the intent's
#: slots, typed), to the template's own answer - or ``None`` where the point
#: is not the intent's own. The templates' own helpers below take the Scope.
Presenter = Callable[[duckdb.DuckDBPyConnection, Query], TemplateResult | None]


def _stat_column(scope: Scope) -> str | None:
    """The router's ``stat`` as the box-score column the count and single-game
    templates whitelist (:data:`~association.query.templates.common.THRESHOLD_STAT_COLUMNS`)."""
    return THRESHOLD_STAT_COLUMNS.get(scope.stat) if scope.stat is not None else None


def _present_period_split(con: duckdb.DuckDBPyConnection, q: Query) -> TemplateResult | None:
    """``period_split``'s own sentence, log and caveats over the compiler's
    settled player, span and period-narrowed games
    (``templates.games._period_split_from``). The template's own order is
    kept: a season whose per-period figures cannot be trusted is refused
    before any name is resolved (``_period_split_reconciliation_refusal``,
    over :data:`~association.query.player_games.PERIOD_RECONCILIATION`),
    and again off the game a date names once it is found."""
    if q.skeleton == "grouped" and q.group == "period":
        return _present_period_by_quarter(con, q)
    if q.skeleton != "rows" or q.order != "date" or q.subject != "player" or q.predicates or q.group != "none":
        return None
    scope = q.scope
    periods, period_label = _period_scope(scope)
    measure = _period_split_measure(scope.stat)
    if q.measures != [measure]:
        return None
    if scope.date is None:
        refusal = _period_split_reconciliation_refusal(scope.season or current_season(), measure)
        if refusal is not None:
            return refusal
    compiled = compile_query(con, q)
    if compiled.player is None:
        return None
    return _period_split_from(con, scope, compiled.player, compiled.span, compiled.narrowed, periods, period_label, measure)


def _present_period_by_quarter(con: duckdb.DuckDBPyConnection, q: Query) -> TemplateResult | None:
    """A named player's four quarters side by side (#162) - the compiler's
    ``grouped``-by-``period`` read (:func:`~association.query.compose.core._compile_by_period`),
    said the way ``period_split`` says a quarter and ``period_leaderboard``
    says the league's table (``templates.games._period_split_by_quarter_from``).
    The template's own order is kept: a season whose figures cannot be
    trusted is refused before any name is resolved.

    .. versionadded:: 5.0.0
    """
    scope = q.scope
    if q.subject != "player" or q.predicates or q.aggregate != "per_game":
        return None
    measure = _period_split_measure(scope.stat)
    if q.measures != [measure]:
        return None
    if scope.date is None:
        refusal = _period_split_reconciliation_refusal(scope.season or current_season(), measure)
        if refusal is not None:
            return refusal
    compiled = compile_query(con, q)
    if compiled.player is None:
        return None
    cur = con.execute(compiled.sql, compiled.params)
    names = [d[0] for d in cur.description]
    rows = [dict(zip(names, r, strict=True)) for r in cur.fetchall()]
    return _period_split_by_quarter_from(scope, compiled.player, compiled.narrowed, rows, measure)


def _present_player_stat_season_line(con: duckdb.DuckDBPyConnection, q: Query) -> TemplateResult | None:
    """``player_stat``'s unnarrowed line - a season or a career, read from
    the season line (``player_season_stats_deduped``) - over the player and
    span the template settles for it (``_player_stat_season_line_subject``)
    and read by its own reader (``_player_stat_season_line``): the second
    relation, never re-derived from box scores. The narrowed line, over box
    scores, is ``compose.stats``' (read before any presenter runs), and a
    window ("stats over his last N games") the log's.

    Only where the question's own words left the router's stat alone (the
    adapter's own measures): a measure the words moved in is a point the
    season line does not say, and the compiler declines it as before."""
    if q.skeleton != "scalar" or q.aggregate != "per_game" or q.subject != "player" or q.predicates or q.source != "seasons":
        return None
    scope = q.scope
    stat = scope.stat
    try:
        # At call time: the reader imports this package for the adapters
        # still here, so a module-level import would cycle.
        from association.query.point import default_point

        own = default_point("player_stat", scope)
    except Unsupported:
        return None
    # The router's own stat, whichever way the point carries it: the
    # adapter's measures, or the question's word for that same stat ("3pt
    # percentage" is three_pct, which the router filed threePointFieldGoalPct).
    named = stat_measure(stat)
    if own.source != "seasons" or (q.measures != own.measures and (named is None or q.measures != [named])):
        return None
    if not (stat is not None and (stat in ADVANCED_STATS or stat in SHOOTING_STATS)):
        # A stat with no per-game column ("avg_shot_distance") is the
        # template's own refusal, and its reason - raised, so the
        # refusal names the stat rather than "a line the season line's
        # reader did not say" (the games relation has no column for it
        # either; `present` says it as the relation's).
        wanted_stats(scope)
    subject = _player_stat_season_line_subject(con, scope)
    if isinstance(subject, TemplateResult):
        return subject
    return _player_stat_season_line(con, *subject, scope)


def _present_leaderboard(con: duckdb.DuckDBPyConnection, q: Query) -> TemplateResult | None:
    """``leaderboard``'s own ranking - the season line's pool, floors,
    traded-player dedup and NetPoints tables (``run_leaderboard``, the
    retired template's reader ``templates.players._leaderboard_ranking``) -
    over the compiler's league-wide point on the season line. Where the
    template declined a point the game-level ranking reads at least as well
    (``LeaderboardStepsAside``: a stat with no season metric, a position
    group) it steps aside and that ranking answers, as it did behind the
    template's refusal, or refuses by name (``plan.games_reading``); the
    template's other refusals (an unknown field, an ambiguous team, a career
    list with columns) stand as the answer's reason.

    .. versionadded:: 5.0.0
    """
    if q.subject != "everyone" or q.source != "seasons" or q.skeleton != "grouped" or q.group != "player" or q.predicates:
        return None
    try:
        return _leaderboard_ranking(con, q.scope, position=q.position)
    except LeaderboardStepsAside:
        return None


def _present_player_compare(con: duckdb.DuckDBPyConnection, q: Query) -> TemplateResult | None:
    """``player_compare``'s own table - each named player's season line side
    by side, with the NetPoints summary beneath
    (``templates.players._player_compare_lines``) - over the compiler's
    point for a pair on the season line. The reader's own refusals (an
    unknown or ambiguous name, a stat the line has no column for, names
    that resolve to one person) stand as the answer's reason.

    .. versionadded:: 5.0.0
    """
    if q.subject != "player" or q.source != "seasons" or q.skeleton != "grouped" or q.group != "player" or q.predicates or q.measures:
        return None
    return _player_compare_lines(con, q.scope)


def _present_streak(con: duckdb.DuckDBPyConnection, q: Query) -> TemplateResult | None:
    """``streak``'s own sentence over the compiler's ``run`` shape on the
    player relation: a named player's longest run of games meeting the
    condition (``templates.splits._streak_player_answer``), or the league's
    longest, one per player (``_streak_league_stat_words`` and
    ``_streak_league_answer``). The runs are the compiled statement's own
    rows (``compose.core._compile_run``); the span, the rule and the
    unseen-games note are read off the same relation subquery. A single
    postseason before 1993-94 is refused as the template refused it
    (``_misfiled_postseason``) before any run is read.

    .. versionadded:: 5.0.0
    """
    if q.skeleton != "run" or q.source != "games":
        return None
    scope = q.scope
    _column, by_stat, unit, want_win, result = _streak_words(scope)
    team = optional_team(con, scope.team, season=scope.season)
    if isinstance(team, TemplateResult):
        return team
    covered = run_scope(scope, named=q.subject == "player")
    if q.subject != "player":
        misfiled = _misfiled_postseason(covered)
        if misfiled is not None:
            return misfiled
    compiled = compile_query(con, q)
    cur = con.execute(compiled.sql, compiled.params)
    names = [d[0] for d in cur.description]
    runs = [dict(zip(names, row, strict=True)) for row in cur.fetchall()]
    if compiled.player is not None:
        return _streak_player_answer(con, scope, covered, compiled.player, compiled.narrowed, team, runs, by_stat, scope.threshold, unit, want_win, result)
    what, who, rule = _streak_league_stat_words(con, covered, runs, scope.threshold, unit)
    # The span searched, not the seasons the leaders' runs happen to fall in:
    # "1997-2023" under a question about every season reads as a narrower search.
    _, first, last = _totals(con, compiled.run_rows or "", compiled.run_params or {})
    return _streak_league_answer(runs, what, who, rule, covered.label(first, last), where_in(covered), by_stat, scope.stat, scope.threshold, unit, want_win)


def _present_player_matchup(con: duckdb.DuckDBPyConnection, q: Query) -> TemplateResult | None:
    """``player_matchup``'s own tables over the compiler's ``pair`` shape:
    the head-to-head record, each player's averages in the meetings and the
    newest of them (``templates.games._player_matchup_from``), over the
    meetings the compiled statement read (``compose.core._compile_pair``)
    and the games the two played as teammates
    (``conditions._teammate_games``, the pair relation's other read).

    .. versionadded:: 5.0.0
    """
    if q.skeleton != "pair" or q.source != "games":
        return None
    scope = q.scope
    covered = _player_matchup_covered(scope)
    compiled = compile_query(con, q)
    if compiled.player is None or compiled.other is None:
        return None
    meetings = _meeting_rows(con.execute(compiled.sql, compiled.params).fetchall())
    together = _teammate_games(con, compiled.narrowed, compiled.other.id)
    return _player_matchup_from(con, scope, covered, compiled.player, compiled.other, compiled.narrowed, meetings, together)


def _present_player_history(con: duckdb.DuckDBPyConnection, q: Query) -> TemplateResult | None:
    """``player_history``'s own table - the stat season by season from the
    season line, newest first, the default four or the count asked for, and
    the career line under a career - over the player the template settles
    (``_player_history_subject``) and read by its own reader
    (``_player_history_read``).

    Only where the point is a season-line history of the router's own stat:
    a stat with no per-season column or a measure the question's words moved
    in is the game-level reading's
    (``plan.games_reading``), answered by the compiler's sentence."""
    scope = q.scope
    stat = scope.stat
    if q.source != "seasons" or q.group != "season" or stat is None or stat not in HISTORY_COLUMNS:
        return None
    if stat_measure(stat) not in (None, *q.measures[:1]):
        return None
    player = _player_history_subject(con, scope)
    if isinstance(player, TemplateResult):
        return player
    return _player_history_read(con, player, scope)


def _count_season(scope: Scope, span: ResolvedSpan) -> tuple[int | None, int, int | None]:
    """The season a count or a single-game high covers (``None`` for a
    career), the season type named in its sentence, and the ordinal that
    named the season, if one did - read off the compiler's settled span,
    since that is the span the rows were counted over."""
    return span.season, player_relation_season_type(scope), span.ordinal


def _present_single_game_high(con: duckdb.DuckDBPyConnection, q: Query) -> TemplateResult | None:
    """``single_game_high``'s own sentence and ``data`` over the compiler's
    top games by the stat: the leader, the date and opponent, "Next: ..."
    for the league, and the template's span, empty-box-score, withheld-stat,
    rebuilt-line and defaulted-season notes, each from its own helper."""
    scope = q.scope
    stat = scope.stat
    column = _stat_column(scope)
    if stat is None or column is None or q.skeleton != "rows" or q.order != "measure" or q.direction != "desc" or q.predicates or q.position or not q.measures or q.measures[0] != column:
        return None
    out = run(con, q)
    span: ResolvedSpan = out["span"]
    player = out["entity"]
    season, season_type, _ = _count_season(scope, span)
    career = season is None
    defaulted = not career and not scope.season
    from_rebuilt = bool(out["rebuilt"])
    label = STAT_LABELS.get(stat, stat)
    name = player.name if player is not None else None
    games = [
        {"player": r.get("player") or name, "value": r[column], "date": eastern_date(r["day"]), "opponent": r["opponent"], "reconstructed": bool(r["reconstructed"])}
        for r in out["rows"]
        if r[column] is not None
    ]
    game_span = _game_span(con, season, season_type, player)
    shape = f"most {label}s in a single game" + (f", {name}" if name else "") + f", {game_span.caption}"
    player_id = player.id if player is not None else None
    empty = _empty_box_scores(con, season, season_type, player_id, covered_by_rebuild=from_rebuilt)
    withheld = 0 if games or column in REBUILT_STATS else _rebuilt_in_scope(con, season, season_type, player_id)
    headline = _single_game_high_answer(games, label, game_span, name, empty=empty, withheld=withheld)
    redirect = _single_game_high_redirect(con, defaulted, player, games, empty, withheld, season_type)
    data = {"question_shape": shape, "season": season, "span": "career" if career else None, "stat": stat, "games": games, "empty_box_scores": empty[0]}
    return TemplateResult(data=_single_game_high_result_data(data, headline, redirect), answer=headline + redirect)


def _threshold_count_is_own_point(q: Query, column: str) -> bool:
    """Whether ``q`` counts exactly the games ``threshold_count`` counts: the
    router's own stat at its own threshold (or a below/above line carrying
    that threshold instead, which the template reads as the count), for one
    player or grouped by player over the league - nothing the question's
    words added."""
    threshold = q.scope.threshold
    counted = [(column, ">=", threshold)] if threshold is not None else []
    if q.predicates not in (counted, []) or q.position:
        return False
    if q.subject == "player":
        return q.skeleton == "scalar" and q.aggregate == "count"
    return q.skeleton == "grouped" and q.aggregate == "count" and q.group == "player"


def _present_threshold_count(con: duckdb.DuckDBPyConnection, q: Query) -> TemplateResult | None:
    """``threshold_count``'s own sentence and ``data`` over the compiler's
    count - one player's, or the league's by player - with the template's
    span, empty-box-score, withheld-stat and rebuilt-line notes, each from
    its own helper."""
    scope = q.scope
    stat = scope.stat
    try:
        # The count's column and threshold, read the one way the template
        # reads them: a below/above phrase may be the whole line, with no
        # threshold at all ("Sga games with under 14 fta").
        column, threshold = _threshold_count_ask(scope)
    except TemplateUnsupported:
        return None
    if not _threshold_count_is_own_point(q, column):
        return None
    try:
        _, _, scope_text = _threshold_count_lines(stat, threshold, scope.below, scope.above)
    except TemplateUnsupported:
        return None
    out = run(con, q)
    span: ResolvedSpan = out["span"]
    player = out["entity"]
    season, season_type, ordinal = _count_season(scope, span)
    rows = _present_threshold_count_rows(q, out)
    player_name = player.name if player is not None else None
    game_span = _game_span(con, season, season_type, player, ordinal=ordinal)
    empty = _empty_box_scores(con, season, season_type, player.id if player is not None else None, covered_by_rebuild=bool(out["rebuilt"]))
    phrase = game_span.preface + _phrase_threshold_count(rows, scope_text, game_span.when, player_name)
    notes = _threshold_count_notes(con, (season, season_type), player, rows, column, STAT_LABELS.get(stat or "", stat or ""), game_span, empty)
    data = {
        "question_shape": f"games with {scope_text}, {game_span.caption}",
        "season": season,
        "span": "career" if season is None else None,
        "leaders": [{"player": name, "games": games} for name, games, _ in rows],
        "empty_box_scores": empty[0],
        "rebuilt_games": rows[0][2] if rows else 0,
        # The trailing "Next: ..." restates the table in prose - not the headline.
        "headline": phrase.split(" Next: ")[0],
        "notes": notes,
    }
    return TemplateResult(data=data, answer=" ".join([phrase, *notes]))


def _present_threshold_count_rows(q: Query, out: dict[str, Any]) -> list[tuple[Any, int, int]]:
    """The compiler's count as ``threshold_count``'s own rows - ``(name,
    qualifying games, rebuilt games among them)``, most first: one row for a
    named player with any, none for one with none, one per player for the
    league."""
    player = out["entity"]
    if q.subject == "player":
        games = int(out["rows"][0].get("games") or 0) if out["rows"] else 0
        return [(player.name, games, out["rebuilt_by_row"][0] if out["rebuilt_by_row"] else 0)] if games and player is not None else []
    return [(r["group"], int(r["games"]), rebuilt) for r, rebuilt in zip(out["rows"], out["rebuilt_by_row"], strict=True)]


#: Intent -> the presenter for its own default point.
PRESENTERS: dict[str, Presenter] = {
    "player_stat": _present_player_stat_season_line,
    "single_game_high": _present_single_game_high,
    "threshold_count": _present_threshold_count,
    "player_history": _present_player_history,
    "leaderboard": _present_leaderboard,
    "period_split": _present_period_split,
    "player_compare": _present_player_compare,
    "streak": _present_streak,
    "player_matchup": _present_player_matchup,
}
"""The intents whose own default point the compiler answers in that intent's
template's words - see the module docstring.

.. versionadded:: 5.0.0
"""

TEAM_ONLY_PRESENTERS: frozenset[str] = frozenset({"with_without"})
"""The intents whose only presenter is the team relation's
(:func:`present_team`): ``with_without``'s split is the team's record, so
its point is a :class:`~association.query.compose.team.TeamQuery` whoever
the question names, and :data:`PRESENTERS` (the player relation's) has no
entry for it. :data:`STATED_SCOPING` declares for both.

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
    # every game in the span), which its point refuses outright
    # (compose.adapt._adapt_streak); its team and league branches refuse
    # the cells only a named player's games settle, by name, there too.
    "streak": relation_scoping("streak"),
    # player_matchup's words: the relation's set less a third team, a window,
    # an ordinal season and a quarter (RELATION_SCOPING_EXCLUDED), which its
    # point refuses outright (compose.adapt._adapt_player_matchup).
    "player_matchup": relation_scoping("player_matchup"),
    # with_without's words: a career, the teammates, one opponent and a
    # companion's role, the template's own declaration when it retired.
    "with_without": WITH_WITHOUT_STATED,
    "single_game_high": frozenset({"span"}),
    # A count is already a line on a column; `below` is the same line the
    # other way ("games with under 14 fta"), and a phrase carrying the count's
    # own number IS the count, misread - see _threshold_count_lines.
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


def present(con: duckdb.DuckDBPyConnection, intent: str, q: Query) -> TemplateResult | None:
    """``q`` answered as ``intent``'s template answers its own default point,
    or ``None`` where ``q`` is not that point (or the intent has no presenter)
    and the compiler's own sentence should answer instead. The intent's slots
    are ``q``'s own scope.

    .. versionadded:: 5.0.0
    """
    presenter = PRESENTERS.get(intent)
    if presenter is None:
        return None
    # Only where the presenter's words state every narrowing asked: a
    # scoping slot they do not (a league-wide ordinal season on
    # single_game_high, an opponent on threshold_count) is exactly a point
    # that is NOT the template's own, and the compiler's sentence says what
    # was read (STATED_SCOPING).
    if unhonored_scoping(intent, q.scope, STATED_SCOPING[intent]):
        return None
    try:
        return presenter(con, q)
    except TemplateUnsupported as exc:
        # The relation refusing a slot while the point was settled - the
        # same outcome core.run gives the compiler's own sentence.
        raise Unsupported(f"relation: {exc}") from exc


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
    _column, _by_stat, _unit, want_win, result = _streak_words(q.scope)
    found = run_team(con, q)
    if found.team is not None:
        # The narrowing the run was read over, for the sentence - and, with
        # no games in it, which fact is missing (the team's games in this
        # span at all, or the match to an opponent/venue narrowing).
        narrowed, team, span = _team_games_narrowed(con, q)
        if not found.games:
            return _condition_team_no_games(con, team, span, narrowed)
        return _streak_team_answer(found.team, narrowed, found.span, found.first_season, found.last_season, found.runs, want_win, result)
    what, who, rule = _streak_league_result_words(con, found.span, found.runs, result)
    return _streak_league_answer(found.runs, what, who, rule, _team_span_label(found.span, found.first_season, found.last_season), _team_where_in(found.span), False, None, None, "", want_win)


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
