"""Router intent + slots -> :class:`~association.query.compose.core.Query`:
each intent is the default point of the algebra, as code, for the six
templates on the player-games relation where they read box scores.

.. versionadded:: 4.4.0
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Any

from association.query.measures import MEASURE_WORDS
from association.query.reading import Group, Reading, Scope, _clamp_limit
from association.query.reading import named_player_in as _named_player_in
from association.query.shotchart import SHOT_AVAILABILITY
from association.query.templates.common import _BOX_SCORES, TemplateUnsupported, period_narrowing

# One concept, one definition (scripts/check_duplicate_names.py): the default
# row counts and the default stat line are the same constants the real
# templates already carry (association.query.templates.games/players),
# reused rather than redeclared under the same name.
from association.query.templates.games import DEFAULT_GAME_LOG_LIMIT, _period_split_measure
from association.query.templates.players import DEFAULT_SINGLE_GAME_LIMIT, STAT_LINE, _threshold_count_ask
from association.query.templates.splits import _DEFAULT_STREAK_LIMIT, _streak_words, _with_without_named

from .core import COLUMNS, DEFAULT_NAMED_RUNS, LINE, Unsupported, run_scope
from .logs import _log_extras

#: The line a splits read carries, beyond the four :data:`~association.query.compose.core.LINE` measures.
SPLIT_LINE: tuple[str, ...] = ("minutes", "points", "rebounds", "assists", "steals", "blocks", "turnovers", "threePointFieldGoalsMade", "fg_pct")
"""The measures a ``player_splits`` read carries.

.. versionadded:: 4.4.0
"""


def _stat_column(stat: str | None) -> str | None:
    """A router ``stat`` as a relation column - the router's own column names
    (``points``, ``threePointFieldGoalsMade``) or a word :data:`~association.query.measures.MEASURE_WORDS` knows."""
    if stat is None or not stat.strip():
        return None
    if stat in COLUMNS:
        return stat
    return MEASURE_WORDS.get(stat.strip().lower())


def _game_log_threshold(scope: Scope) -> list[tuple[str, str, Any]]:
    """A ``threshold`` on a log as the line it keeps games past - "games with
    15+ fga" lists those games, on the stat's column, rather than his last
    ten whatever they held. The retired template refused the slot outright
    (``_game_log_lines``) and the compiler then answered the whole log with
    the threshold dropped - the silent widening the templates exist to stop.
    One carrying a below/above phrase's own number is that phrase, misread
    ("less than 15 fga" arrived as threshold 15), and the phrase answers it;
    one beside no stat has no column to keep a line on and is refused.

    .. versionadded:: 5.0.0
    """
    from association.query.templates.common import measure_filters

    threshold = scope.threshold
    if threshold is None or any(line.value == threshold for line in measure_filters(scope.below, scope.above)):
        return []
    col = _stat_column(scope.stat)
    if col is None:
        raise Unsupported("game_log cannot keep only the games past a threshold on no stat")
    return [(col, ">=", threshold)]


def _adapt_game_log(scope: Scope) -> Reading:
    """``game_log``'s default point: the newest games, in date order."""
    if not _named_player_in(scope):
        raise Unsupported("a team's log is the team relation's")
    if scope.stat and _stat_column(scope.stat) is None:
        # A REAL stat neither the log's columns nor the relation carries is
        # refused, as the retired template refused it (``_log_extras``):
        # "luka shot distance log" listed without it would be the narrower
        # answer passed off as the one asked for. One the relation derives
        # per game (TS%) is the compiler's own measure, and shown.
        try:
            _log_extras(scope.stat)
        except TemplateUnsupported as exc:
            raise Unsupported(str(exc)) from exc
    # ``season_type_unstated`` ("his last 5 games", no season type named) is
    # read over both types at once - ``scoped_player`` settles the span with
    # ``_player_relation_season_type`` - and ``compose.present`` says it the
    # way ``game_log`` does, one type at a time merged by date.
    # A team beside the player is settled in compile_query through game_log's own _team_slot_for_player.
    date = scope.date
    # game_log settles the name in a career span when a date is given (the
    # date is the scope), and in the named or defaulted season otherwise.
    return Reading(
        scope=scope,
        shape="rows",
        measures=list(LINE),
        aggregate="none",
        group="none",
        predicates=_game_log_threshold(scope),
        order="date",
        direction="asc" if scope.order == "first" else "desc",
        limit=_clamp_limit(scope.limit, DEFAULT_GAME_LOG_LIMIT),
        span="career" if date else scope.span,
        season=None if date else scope.season,
    )


def _adapt_player_stat(scope: Scope) -> Reading:
    """``player_stat``'s default point: a per-game average over box scores
    where a narrowing (or a date) sends the read there, and the season line
    (``source="seasons"``) for an unnarrowed season or career - the split
    the retired template made (``templates.players._player_stat_reads_box_scores``)."""
    if not _named_player_in(scope):
        raise Unsupported("player_stat needs a player")
    from association.query.templates.common import measure_filters
    from association.query.templates.players import _player_stat_reads_box_scores

    col = _stat_column(scope.stat)
    measures = [col] if col else list(STAT_LINE)
    if scope.limit or scope.order:
        # "Jokic averages last 10 games" answered with his season line would
        # be the substitution this module exists to stop: the log of exactly
        # those games with averages beneath is the shape the question has,
        # so the point is game_log's, said by its presenter
        # (compose.present._present_player_stat hands a rows point on).
        return _adapt_game_log(scope)
    if not (_player_stat_reads_box_scores(scope, measure_filters(scope.below, scope.above)) or scope.date):
        return Reading(scope=scope, shape="scalar", measures=measures, aggregate="per_game", group="none", predicates=[], source="seasons")
    date = scope.date
    return Reading(
        scope=scope,
        shape="scalar",
        measures=measures,
        aggregate="per_game",
        group="none",
        predicates=[],
        span="career" if date else scope.span,
        season=None if date else scope.season,
    )


def _adapt_threshold_count(scope: Scope) -> Reading:
    """``threshold_count``'s default point: a count of games clearing one
    line - the threshold, or a below/above phrase that is the whole line
    ("Sga games with under 14 fta": the relation narrows by it, and the
    count is of the games left), read the one way the count's presenter reads
    it (``templates.players._threshold_count_ask``)."""
    col = _stat_column(scope.stat)
    threshold = scope.threshold
    if not _named_player_in(scope):
        raise Unsupported("a league-wide count is not on the one-player relation")
    if threshold is None and (scope.below or scope.above):
        try:
            _threshold_count_ask(scope)
        except TemplateUnsupported as exc:
            raise Unsupported(f"threshold_count: {exc}") from exc
        return Reading(scope=scope, shape="scalar", measures=[], aggregate="count", group="none", predicates=[], available=_BOX_SCORES)
    if col is None or threshold is None or threshold < 1:
        # The reason threshold_count's retired template gave, where it has one
        # (a threshold of 0 counts every game; no stat it keeps a line on).
        try:
            _threshold_count_ask(scope)
        except TemplateUnsupported as exc:
            raise Unsupported(f"threshold_count: {exc}") from exc
        raise Unsupported("threshold_count refuses; nothing to compare")
    # A below/above phrase carrying the threshold's own number IS the count,
    # misread as a threshold (threshold_count's own _threshold_count_lines).
    lines = [str(x) for x in (*scope.below, *scope.above)]
    predicates = [] if any(str(threshold) in line for line in lines) else [(col, ">=", threshold)]
    return Reading(scope=scope, shape="scalar", measures=[], aggregate="count", group="none", predicates=predicates, available=_BOX_SCORES)


def _adapt_single_game_high(scope: Scope) -> Reading:
    """``single_game_high``'s default point: the top games by one stat."""
    col = _stat_column(scope.stat)
    if not _named_player_in(scope) or col is None:
        raise Unsupported("single_game_high needs a player and a known stat here")
    return Reading(
        scope=scope,
        shape="rows",
        measures=[col],
        aggregate="none",
        group="none",
        predicates=[],
        order="measure",
        direction="desc",
        limit=_clamp_limit(scope.limit, DEFAULT_SINGLE_GAME_LIMIT),
    )


def _adapt_player_splits(scope: Scope) -> Reading:
    """``player_splits``'s default point: a record by venue, or by starter/bench."""
    if not _named_player_in(scope):
        raise Unsupported("a team's splits are the team relation's")
    # A named half ("as a starter") narrows the games (the relation's own
    # `started`) while the category shown is still starter/bench - the
    # template's own fold (_STARTER_BENCH_SIDES); by venue it read the
    # half's games split by home/away instead.
    group: Group = "starter" if scope.split in ("starter_bench", "starter", "bench") else "venue"
    return Reading(scope=scope, shape="grouped", measures=list(SPLIT_LINE), aggregate="record", group=group, predicates=[], available=_BOX_SCORES)


def _adapt_period_split(scope: Scope) -> Reading:
    """``period_split``'s default point: a named player's games in date
    order, each read as the quarter's or half's line (the relation's
    ``period``/``half`` cells), measuring the column the period's line
    rebuilds - points where no stat was named - or, with no period named,
    his four quarters side by side, a ``grouped`` read by ``period``. The
    retired template's own early refusals are the point's (ROADMAP plan
    item 6, step (g)): a column play-by-play cannot restrict to a period
    (:func:`~association.query.templates.games._period_split_measure`), and
    the narrowings its accuracy caveat cannot survive - a career, ``since``,
    ``until`` (:data:`~association.query.templates.common.RELATION_SCOPING_EXCLUDED`).
    The player is settled over the shot table
    (:data:`~association.query.shotchart.SHOT_AVAILABILITY`), as the
    template settled him, and over his career when a date names the game.

    .. versionadded:: 5.0.0

    .. versionchanged:: 5.0.0
       No period is the by-quarter breakdown (ROADMAP step 2, #162), not
       a refusal.
    """
    if not _named_player_in(scope):
        raise Unsupported("period_split needs a player")
    try:
        measure = _period_split_measure(scope.stat)
    except TemplateUnsupported as exc:
        raise Unsupported(str(exc)) from exc
    date = scope.date
    if period_narrowing(scope) is None:
        # No period named: the four quarters side by side (#162, "Jokic
        # points by quarter") - a grouped read by period over the same
        # narrowed games (compose.core._compile_by_period), one row a
        # quarter, said by the presenter in the template's words. The stage
        # assigns period_split only where a period or the "by quarter"
        # words were read, so a scope with neither is this shape.
        return Reading(
            scope=scope,
            shape="grouped",
            measures=[measure],
            aggregate="per_game",
            group="period",
            predicates=[],
            order="date",
            direction="asc",
            limit=None,
            available=SHOT_AVAILABILITY,
            span="career" if date else scope.span,
            season=None if date else scope.season,
        )
    return Reading(
        scope=scope,
        shape="rows",
        measures=[measure],
        aggregate="none",
        group="none",
        predicates=[],
        order="date",
        direction="asc" if scope.order == "first" else "desc",
        limit=_clamp_limit(scope.limit, DEFAULT_GAME_LOG_LIMIT),
        available=SHOT_AVAILABILITY,
        span="career" if date else scope.span,
        season=None if date else scope.season,
    )


def _adapt_streak(scope: Scope) -> Reading:
    """``streak``'s default point: the longest run of consecutive games
    meeting one condition (the ``run`` shape), on the relation the question
    names. A named player's is over his games (a stat at or above its
    threshold, or his team's wins in games he played); a named team's is
    its own run of wins or losses within a season, on the team relation;
    nobody named is the league's - each player's or each team-season's own
    longest, the count asked for or :data:`~association.query.templates.splits._DEFAULT_STREAK_LIMIT`.
    The retired template's own early refusals are the point's (ROADMAP plan
    item 6, step (g)): a stat with no threshold or a threshold with no stat
    (:func:`~association.query.templates.splits._streak_kind`), a team's run
    of a stat, the narrowings a run cannot take (one date, a window, a
    quarter - :data:`~association.query.templates.common.RELATION_SCOPING_EXCLUDED`),
    the cells only a named player's games settle
    (:func:`~association.query.templates.splits._condition_needs_player_refusal`)
    and, for the league, an opponent or a venue with no subject to narrow
    (:func:`~association.query.templates.splits._streak_league_needs_named_subject`).

    .. versionadded:: 5.0.0
    """
    try:
        column, by_stat, _unit, want_win, _result = _streak_words(scope)
    except TemplateUnsupported as exc:
        raise Unsupported(str(exc)) from exc
    predicates: list[tuple[str, str, Any]] = [(column, ">=", scope.threshold)] if by_stat and column is not None else [("won", "=", want_win)]
    if _named_player_in(scope):
        try:
            covered = run_scope(scope, named=True)
        except TemplateUnsupported as exc:
            raise Unsupported(str(exc)) from exc
        return Reading(
            scope=scope,
            shape="run",
            measures=[],
            aggregate="none",
            group="none",
            predicates=predicates,
            limit=DEFAULT_NAMED_RUNS,
            available=_BOX_SCORES,
            span="career" if covered.season is None else None,
            season=covered.season,
        )
    # The team and league branches' cell checks (game_n, the player-only
    # cells, a league run's opponent or venue) are the planner's
    # (compose.plan._shape_declines).
    if scope.team and scope.team.strip():
        if by_stat:
            raise Unsupported("a team's streak is of wins or losses, not of a stat")
        return Reading(scope=scope, shape="run", measures=["won"], aggregate="count", group="none", predicates=predicates, relation="team")
    limit = _clamp_limit(scope.limit, _DEFAULT_STREAK_LIMIT)
    if by_stat:
        return Reading(scope=scope, shape="run", measures=[], aggregate="none", group="none", predicates=predicates, limit=limit, relation="everyone")
    return Reading(scope=scope, shape="run", measures=["won"], aggregate="count", group="none", predicates=predicates, limit=limit, relation="team")


def _adapt_player_matchup(scope: Scope) -> Reading:
    """``player_matchup``'s default point: two named players' lines over the
    games they met in, on opposite teams (the ``pair`` shape), the names
    settled over the box scores as the template settled them, and over the
    career when a date names the game (a date replaces the season, the way
    ``game_log``'s own does). The retired template's own early refusals are
    the point's (ROADMAP plan item 6, step (g)): fewer or more than two
    names, and the narrowings a matchup cannot take - a third team, a
    window, an ordinal season, a quarter
    (:data:`~association.query.templates.common.RELATION_SCOPING_EXCLUDED`);
    two names that resolve to one person are refused as the pair is settled
    (``compose.core._resolve_pair``).

    .. versionadded:: 5.0.0
    """
    # The scoping - a player in the opponent slot ("lebron vs kawhi head to
    # head" read as one name against a team) - is the planner's to refuse
    # (compose.plan._shape_declines), the cause the reader can fix.
    texts = list(dict.fromkeys(n.strip() for n in [*scope.players, scope.player] if n is not None and n.strip()))
    if len(texts) != 2:
        raise Unsupported(f"player_matchup needs exactly two players, got {texts!r}")
    dated = bool(scope.date)
    return Reading(
        scope=scope,
        shape="pair",
        measures=[],
        aggregate="none",
        group="none",
        predicates=[],
        available=_BOX_SCORES,
        span="career" if dated else scope.span,
        season=None if dated else scope.season,
    )


WITH_WITHOUT_STATED: frozenset[str] = frozenset({"span", "without", "opponent", "conditions"})
"""The scoping ``with_without``'s words state - a career, the teammates
divided by, one opponent (both rows narrow together, #163) and a
companion's role - the retired template's own declaration; any other
narrowing is refused by name.

.. versionadded:: 5.0.0
"""


def _adapt_with_without(scope: Scope) -> Reading:
    """``with_without``'s default point: a team's record in the games named
    teammates played against the games they missed - the team relation's
    ``presence`` group (``compose.team._compile_team_presence``) - with the
    subject's averages in each where a player is named. The retired
    template's own early refusals are the point's (ROADMAP plan item 6, step
    (g)): a narrowing its words do not state (:data:`WITH_WITHOUT_STATED`),
    and no teammate to divide by - the teammates come from ``without`` or
    ``with_player``, a ``conditions`` role, or failing those the one name
    beside a team or the second of two; more than that is "record when A
    and B and C play", which nobody has defined.

    .. versionadded:: 5.0.0
    """
    mate_texts, _asked_without, _roles = _with_without_named(scope)
    if not mate_texts:
        texts = list(dict.fromkeys(n.strip() for n in (scope.player, *scope.players) if n is not None and n.strip()))
        team_named = bool(scope.team and scope.team.strip())
        if not ((team_named and len(texts) == 1) or (not team_named and len(texts) == 2)):
            raise Unsupported(f"with_without needs exactly one teammate, got {texts!r}")
    return Reading(scope=scope, shape="grouped", measures=["record"], aggregate="record", group="presence", predicates=[], relation="team")


def _adapt_record_when(scope: Scope) -> Reading:
    """``record_when``'s default point: the record in games clearing one line."""
    col = _stat_column(scope.stat)
    threshold = scope.threshold
    if not _named_player_in(scope) or col is None or threshold is None or threshold < 1:
        # A line of 0 is every game he played: never a record "when", as
        # record_when's retired template refused it too.
        raise Unsupported("record_when needs a player, a stat and a positive threshold here")
    return Reading(scope=scope, shape="scalar", measures=[], aggregate="record", group="none", predicates=[(col, ">=", threshold)], available=_BOX_SCORES)


#: Intent -> its default-point adapter, over the typed scope. Kept as a
#: mapping rather than an if/elif chain so a new intent is one entry, not a
#: longer function.
_ADAPTERS: dict[str, Callable[[Scope], Reading]] = {
    "game_log": _adapt_game_log,
    "player_stat": _adapt_player_stat,
    "threshold_count": _adapt_threshold_count,
    "single_game_high": _adapt_single_game_high,
    "player_splits": _adapt_player_splits,
    "record_when": _adapt_record_when,
    "period_split": _adapt_period_split,
    "streak": _adapt_streak,
    "player_matchup": _adapt_player_matchup,
    "with_without": _adapt_with_without,
}


def to_reading(intent: str, slots: dict[str, Any]) -> Reading:
    """The intent's default point of the algebra: the query a bare router
    intent means before any of the question's own words move it (see
    :func:`association.query.point.read_point`). ``slots`` comes in
    by :meth:`~association.query.reading.Scope.from_slots`, the one door a
    slot dict has, and the adapters read the typed scope.

    .. versionadded:: 4.4.0

    .. versionchanged:: 5.0.0
       An unnarrowed ``player_stat`` is a point on the season line
       (``source="seasons"``) rather than :class:`~association.query.compose.core.Unsupported`.

    .. versionchanged:: 5.0.0
       ``game_log`` with ``season_type_unstated`` ("his last 5 games") is a
       point - both season types, which the relation reads at once - rather
       than :class:`~association.query.compose.core.Unsupported`.
    """
    return _to_reading_scope(intent, Scope.from_slots(slots))


def _to_reading_scope(intent: str, scope: Scope) -> Reading:
    """:func:`to_reading` over a scope already read - for the compiler's own
    moves (:func:`association.query.point.read_point` reads the scope
    once) and the presenters, which have the Query's scope and no slot dict."""
    adapter = _ADAPTERS.get(intent)
    if adapter is None:
        raise Unsupported(f"no adapter for {intent}")
    # The point says whose default it is: the planner declines by it
    # (compose.plan._shape_declines) until intent leaves the reader in
    # Phase 3.
    return replace(adapter(scope), intent=intent)
