"""One compiler over the player-games relation: a :class:`Query` names a point
(skeleton, measures, aggregate, group, window) and the relation supplies the
subject, the span and every scoping slot exactly as it does for the six
relation templates - through :func:`~association.query.templates.common.scoped_player` /
:func:`~association.query.templates.common.scoped_games` for a named player,
:func:`~association.query.templates.common.league_games` for the league-wide
read - so binding parity is not a question here.

Skeletons and readers:

- ``rows`` - :func:`association.query.player_games.rows_sql` (a log, the top
  game by a measure)
- ``scalar`` - :func:`association.query.player_games.aggregate_sql`
  (averages, totals, a count, a record)
- ``grouped`` - :func:`association.query.player_games.grouped_sql` (splits, a
  ranking)

Streaks are out of scope: nothing here reads a run of consecutive games.

The compiler never narrows the relation by hand - every clause on
``pgl.opponent_team_id``, ``pgl.starter``, ``g.home_team_id`` or ``g.date``
lives in :func:`~association.query.templates.common.scoped_games` or
:func:`~association.query.templates.common.league_games`, so a narrowing that
reaches a template reaches this compiler too, without being taught to it
separately - see ``test_templates_on_the_relation_do_not_narrow_it_themselves``
in ``tests/query/test_templates.py``, which walks this module's source for
exactly those tokens.

.. versionadded:: 4.4.0
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field, replace
from typing import Any

import duckdb

from association.nba.season import current_season, eastern_date_sql
from association.query.conditions import _PLAYER_GAME_TABLES, MEETING_STATS, UNGATED_ON_REBUILD, BoxSource, _longest_runs_sql, _meetings_select, _player_streak_rows, box_source
from association.query.entities import Entity
from association.query.measures import MEASURE_WORDS
from association.query.player_games import PERIOD_COLUMNS, REBUILT_STATS, REGULATION_QUARTERS, Narrowed, aggregate_sql, games_subquery, grouped_sql, named, paired_rows_sql, rows_sql
from association.query.reading import Scope
from association.query.templates.common import (
    _BOX_SCORES,
    _GAME_LOGS,
    RELATION_SCOPING,
    SCOPING_SLOTS,
    TEAM_RELATION_SCOPING,
    TemplateResult,
    TemplateUnsupported,
    _apply_period,
    _box_score_notes,
    _career_end,
    _condition_scope,
    _resolved_player,
    _resolved_team,
    _Span,
    _span_of,
    league_games,
    measure_filters,
    period_narrowing,
    scoped_games,
    scoped_player,
)
from association.query.templates.games import PERIOD_RATES, _team_slot_for_player
from association.query.templates.players import _seasons_on_record

EASTERN = eastern_date_sql("g.date")

#: Box-score columns a measure may name: everything ``MEASURE_WORDS`` reaches
#: plus the per-game advanced figures the view carries.
COLUMNS: frozenset[str] = frozenset(MEASURE_WORDS.values()) | frozenset(
    {"plusMinus", "offensiveRebounds", "defensiveRebounds", "ts_pct", "efg_pct", "usage_pct", "game_score", "threePointFieldGoalsMade", "threePointFieldGoalsAttempted"}
)
"""Every column name a ``Query.measures`` entry may hold, taken straight or derived.

.. versionadded:: 4.4.0
"""

#: Measures computed from columns, per game.
DERIVED: dict[str, str] = {
    "pra": "(pgl.points + pgl.rebounds + pgl.assists)",
    "fg_pct": "(pgl.fieldGoalsMade * 100.0 / NULLIF(pgl.fieldGoalsAttempted, 0))",
    "three_pct": "(pgl.threePointFieldGoalsMade * 100.0 / NULLIF(pgl.threePointFieldGoalsAttempted, 0))",
    "ft_pct": "(pgl.freeThrowsMade * 100.0 / NULLIF(pgl.freeThrowsAttempted, 0))",
    "double_double": "(((pgl.points >= 10)::INT + (pgl.rebounds >= 10)::INT + (pgl.assists >= 10)::INT + (pgl.steals >= 10)::INT + (pgl.blocks >= 10)::INT) >= 2)",
    "triple_double": "(((pgl.points >= 10)::INT + (pgl.rebounds >= 10)::INT + (pgl.assists >= 10)::INT + (pgl.steals >= 10)::INT + (pgl.blocks >= 10)::INT) >= 3)",
    "won": "(g.winner_team_id = pgl.team_id)",
    "home": "(g.home_team_id = pgl.team_id)",
    "fouled_out": "(pgl.fouls >= 6)",
}
"""A measure name that is not a stored column, and the SQL that computes it per game.

.. versionadded:: 4.4.0
"""

#: Rates as ratios of sums, never means of per-game rates.
RATES: dict[str, tuple[str, str]] = {
    "fg_pct": ("SUM(pgl.fieldGoalsMade) * 100.0", "NULLIF(SUM(pgl.fieldGoalsAttempted), 0)"),
    "three_pct": ("SUM(pgl.threePointFieldGoalsMade) * 100.0", "NULLIF(SUM(pgl.threePointFieldGoalsAttempted), 0)"),
    "ft_pct": ("SUM(pgl.freeThrowsMade) * 100.0", "NULLIF(SUM(pgl.freeThrowsAttempted), 0)"),
    # A fraction (0.57), like the per-game column the view stores and the
    # sentence and the page both print times 100 (sentence._FRACTION_COLUMNS,
    # the page's FRACTIONS) - computed times 100 here as well, a narrowed
    # line read "Joel Embiid averaged 4622.5% TS% per game ... vs the Boston
    # Celtics", and a ranking "TS% 9213.1%".
    "ts_pct": ("CAST(SUM(pgl.points) AS DOUBLE)", "NULLIF(2 * (SUM(pgl.fieldGoalsAttempted) + 0.44 * SUM(pgl.freeThrowsAttempted)), 0)"),
    "efg_pct": ("CAST(SUM(pgl.fieldGoalsMade) + 0.5 * SUM(pgl.threePointFieldGoalsMade) AS DOUBLE)", "NULLIF(SUM(pgl.fieldGoalsAttempted), 0)"),
}
"""A percentage measure's numerator and denominator, summed over games rather
than averaged per game - on the scale its per-game column has: a percent for
``fg_pct``/``three_pct``/``ft_pct``, a fraction for ``ts_pct``/``efg_pct``.

.. versionadded:: 4.4.0
"""

#: The comparisons a ``Query.predicates`` entry may use - an allowlist, so no
#: question text reaches SQL as an operator.
OPS: dict[str, str] = {">=": ">=", ">": ">", "<=": "<=", "<": "<", "=": "="}
"""``{">=": ">=", ...}`` - the comparisons :func:`compile_query` accepts for a predicate.

.. versionadded:: 4.4.0
"""

#: A ``Query.group`` value, mapped to its ``GROUP BY`` key and the column that
#: labels each group in a grouped read.
GROUPS: dict[str, tuple[str, str]] = {
    "venue": ("(g.home_team_id = pgl.team_id)", "CASE WHEN g.home_team_id = pgl.team_id THEN 'home' ELSE 'away' END AS \"group\""),
    "starter": ("pgl.starter", "CASE WHEN pgl.starter THEN 'starter' ELSE 'bench' END AS \"group\""),
    "season": ("pgl.season", 'pgl.season AS "group"'),
    "season_type": ("pgl.season_type", 'pgl.season_type AS "group"'),
    "month": (f"EXTRACT(YEAR FROM {EASTERN}), EXTRACT(MONTH FROM {EASTERN})", f'EXTRACT(YEAR FROM {EASTERN}) * 100 + EXTRACT(MONTH FROM {EASTERN}) AS "group"'),
    "opponent": ("pgl.opponent_team_id, pgl.opponent_abbr", 'pgl.opponent_abbr AS "group"'),
    "won": ("(g.winner_team_id = pgl.team_id)", "CASE WHEN g.winner_team_id = pgl.team_id THEN 'win' ELSE 'loss' END AS \"group\""),
    "player": ("pgl.athlete_id, pgl.player_name", 'pgl.player_name AS "group"'),
}
"""``Query.group`` -> ``(GROUP BY key, labeled SELECT column)``.

.. versionadded:: 4.4.0
"""

#: The default measures of a ``rows`` read with no measure named.
LINE: tuple[str, ...] = ("minutes", "points", "rebounds", "assists")
"""The default line a game log or single-game read carries.

.. versionadded:: 4.4.0
"""


def _row_select(*, rebuilt: bool) -> str:
    """The fixed columns a ``rows`` read selects, before its measures.

    ``pgl.reconstructed`` is read only where the box source actually carries
    it: ``player_game_log`` falls back to the plain, pre-rebuild table on a
    warehouse built before that view existed (`AGENTS.md`, "a warehouse built
    before a view change is not detected"), which has no such column at all -
    reading it unconditionally would raise a Binder error against exactly the
    warehouse the fallback exists for.
    """
    flag = "pgl.reconstructed" if rebuilt else "FALSE"
    return f"pgl.event_id, pgl.season, {EASTERN} AS day, pgl.opponent_abbr AS opponent, (g.home_team_id = pgl.team_id) AS home, (g.winner_team_id = pgl.team_id) AS won, {flag} AS reconstructed"


class Unsupported(Exception):
    """The compiler cannot say this query - a dimension value it lacks, or a
    scoping slot the relation does not narrow by. The agent may still be able
    to answer it; this is not a claim that nothing can.

    .. versionadded:: 4.4.0
    """


class Refused(Exception):
    """The relation itself refused: no such player, an ambiguous name, a
    coverage floor. Carries the template-shaped :class:`~association.query.templates.common.TemplateResult`
    so the wording is the fast path's, not a second, differently-worded refusal.

    .. versionadded:: 4.4.0
    """

    def __init__(self, result: TemplateResult) -> None:
        """Wrap ``result``, the refusal the relation already composed."""
        super().__init__(result.answer)
        self.result = result


@dataclass(kw_only=True)
class Query:
    """A point over the player-games relation. ``scope`` is the question's own
    scoping (subject, span, and every scoping slot), handed whole to the
    relation - the same discipline :func:`~association.query.templates.common.scoped_games`
    already keeps: a slot read here and not passed through would be a second,
    quieter way to narrow by hand. Every construction names its fields.

    .. versionadded:: 4.4.0

    .. versionchanged:: 5.0.0
       Holds the typed :class:`~association.query.reading.Scope` as ``scope``
       in place of the ``slots`` dict, and every field is keyword-only.
    """

    #: The question's scoping, forwarded whole to the relation.
    scope: Scope
    #: ``"rows"``, ``"scalar"`` or ``"grouped"`` - which reader answers the point.
    skeleton: str = "rows"
    #: The box-score columns (or derived measures) the point reads.
    measures: list[str] = field(default_factory=lambda: list(LINE))
    #: ``"none"``, ``"per_game"``, ``"total"``, ``"count"`` or ``"record"``.
    aggregate: str = "none"
    #: ``"none"`` or a key of :data:`GROUPS`.
    group: str = "none"
    #: Row predicates beyond a below/above line: ``(measure, op, value)``.
    predicates: list[tuple[str, str, Any]] = field(default_factory=list)
    #: ``"date"`` or ``"measure"``.
    order: str = "date"
    #: ``"asc"`` or ``"desc"``.
    direction: str = "desc"
    #: Row count for a ``rows`` read, or the number of groups for a ``grouped`` one.
    limit: int | None = None
    offset: int = 0
    #: The minimum games a group needs to be kept, for a ranking.
    minimum_games: int | None = None
    #: Binding parity with the template being mirrored: which availability
    #: narrows an ambiguous name (game logs vs box scores), and the span and
    #: season the subject is settled in - the templates compute these before
    #: :func:`~association.query.templates.common.scoped_player`, and a raw
    #: slot narrows differently.
    available: Any = None
    span: Any = None
    season: Any = None
    #: ``"games"`` - the player-games relation, one row per player per game -
    #: or ``"seasons"``, the season line (``player_season_stats_deduped``, one
    #: row per player per season) an unnarrowed player line or a per-season
    #: history reads. Only :mod:`~association.query.compose.present` says a
    #: season-line point, through the templates' own readers; :func:`compile_query`
    #: compiles the game-level relation alone.
    source: str = "games"
    #: ``"player"`` (the named one) or ``"everyone"`` - the league-wide read of
    #: the same relation (:func:`~association.query.templates.common.league_games`).
    subject: str = "player"
    #: A position code (``"C"``, ``"G"``, ``"PG"``, ...), honored only when
    #: ``subject`` is ``"everyone"``.
    position: str | None = None


@dataclass
class Compiled:
    """A :class:`Query` turned into SQL: the statement, its parameters and
    what the relation settled about the question - the subject, the span,
    every narrowing, and whether the read was widened to rebuilt lines.

    .. versionadded:: 4.4.0
    """

    sql: str
    #: Positional (``?``) for the rows, scalar and grouped shapes; named
    #: (``$name``) for a run, whose statement nests the relation's own
    #: subquery twice (:func:`association.query.player_games.named`).
    params: list[Any] | dict[str, Any]
    player: Entity | None
    span: Any
    narrowed: Narrowed
    rebuilt: bool
    measures: list[str]
    #: A run's games in date order - the rows the window was read over,
    #: with their own names bound in ``params`` - for a presenter's totals
    #: and unseen-games count (:func:`_compile_run`); ``None`` otherwise.
    run_rows: str | None = None
    run_params: dict[str, Any] | None = None
    #: The second player of a ``pair`` read (:func:`_compile_pair`), whose
    #: line the statement reads beside ``player``'s; ``None`` otherwise.
    other: Entity | None = None


def measure_sql(name: str, *, rebuilt: bool = False) -> str:
    """A measure as SQL. Under the widened (rebuilt) played guard, a column a
    rebuild does not fill is blanked on rebuilt rows - the templates' own
    ``REPLACE`` rule (see :func:`association.query.player_games.column`) - so
    an average is over the games that carry the figure.

    .. versionadded:: 4.4.0
    """
    if name in DERIVED:
        return DERIVED[name]
    if name in COLUMNS:
        if rebuilt and name not in REBUILT_STATS and name in UNGATED_ON_REBUILD:
            return f"CASE WHEN pgl.reconstructed THEN NULL ELSE pgl.{name} END"
        return f"pgl.{name}"
    raise Unsupported(f"no measure {name!r} on the player-games relation")


#: Measures that are conditions rather than counted quantities - "how many
#: triple-doubles" counts games where the measure is true.
BOOLEAN_MEASURES: frozenset[str] = frozenset({"triple_double", "double_double", "won", "fouled_out"})
"""Measures read as a per-game condition rather than a counted quantity.

.. versionadded:: 4.4.0
"""


def _agg(name: str, aggregate: str, *, rebuilt: bool = False) -> str:
    """One aggregate SELECT expression for ``name``, labeled with its own name.

    A boolean measure has no per-game average or total: it is a condition
    the games satisfy or not, and the count of them is the ``count``
    aggregate with the measure as a predicate (``_move_boolean_count``).
    Averaging one is refused here rather than sent to DuckDB, which throws
    ``avg(BOOLEAN)`` - "sengun double-doubles vs southeast division" reached
    that as a ``player_splits`` line once the router kept the division's
    name (#213), and a crash is the one shape worse than a wrong answer.
    """
    expr = measure_sql(name, rebuilt=rebuilt)
    if name in BOOLEAN_MEASURES and aggregate in ("per_game", "total"):
        raise Unsupported(f"{name!r} is a condition, not a quantity - it is counted, never averaged or summed")
    if aggregate == "per_game":
        if name in RATES:
            num, den = RATES[name]
            return f'({num} / {den}) AS "{name}"'
        return f'AVG({expr}) AS "{name}"'
    if aggregate == "total":
        return f'SUM({expr}) AS "{name}"'
    raise Unsupported(f"aggregate {aggregate!r}")


#: Scoping slots the router files FOR the compiler - markers a template refuses
#: on so the question reaches here, which the compiler then reads itself:
#: ``ranked_by`` (the games that satisfy a boolean stat, ranked by another
#: measure - move.py reads the measure off the question). It narrows nothing,
#: so it is not "unhonored" here; measured live on yardstick-v2 F124 ("highest
#: scoring triple doubles"), the marker alone sent the question to the agent.
#:
#: .. versionadded:: 4.4.0
COMPILER_SLOTS: frozenset[str] = frozenset({"ranked_by"})


def _check_relation_scoping(scope: Scope, subject: str = "player", honored_extra: frozenset[str] = frozenset()) -> None:
    """``check_scope``'s rule, for the relation: a scoping slot the relation
    does not narrow by (``situation`` when it names no calendar, ``round``,
    ``rate`` ...) is refused, never dropped - answering "on Tuesdays" for
    every day is the silent widening the templates exist to stop.

    ``season_type_unstated`` (a question that asked for both season types,
    "including the playoffs") is honored for a named player: ``scoped_player``
    settles his span over both (``templates.common._player_relation_season_type``),
    the reading ``game_log``, ``player_stat`` and ``threshold_count`` all
    declare, and for a team's log, which reads both types and merges them by
    date (``templates.games._team_mixed_games``). The league-wide read
    settles one type (:func:`_resolve_everyone`) and still refuses it rather
    than answer the regular season alone.

    Every name in ``SCOPING_SLOTS`` is a :class:`~association.query.reading.Scope`
    field (``tests/query/test_reading.py`` holds the two to it), and a field
    at its default - None, an empty tuple, False - is the slot absent.
    ``honored_extra`` is what the point's own reader honors beyond the
    relation: the season line's ranking reads ``rate`` (a season total, or a
    unit it refuses by name), where the game-level relation cannot."""
    # A team's point is held to the TEAM relation's own cells: checked
    # against the player relation's, a cell only a player's games carry (a
    # teammate's role, a starter half, a line on a box-score column, a
    # period condition) passed the planner and was then ignored by a
    # relation with no such cell - "raptors game log in games where they
    # made 5 threes in the first quarter" listed every game (ISSUES.md, P1,
    # 2026-09-30). An allow list, so a cell added to one relation is refused
    # on the other until it is built there.
    relation = TEAM_RELATION_SCOPING if subject == "team" else RELATION_SCOPING
    honored = relation | COMPILER_SLOTS | honored_extra | ({"season_type_unstated"} if subject in ("player", "team") else set())
    unhonored = sorted(k for k in SCOPING_SLOTS - honored if getattr(scope, k) not in (None, "", (), False))
    if unhonored:
        whose = "a team's games cannot be narrowed by" if subject == "team" else "the relation cannot honor"
        raise Unsupported(f"{whose} {unhonored} - it would answer for a different span than was asked")


#: What a period-narrowed read may measure: the columns the period's line
#: rebuilds, and the measures computed only from them (a rate is a ratio of
#: the period's sums; a game's result is the game's).
_PERIOD_READABLE: frozenset[str] = frozenset(PERIOD_COLUMNS) | {"pra", "fg_pct", "three_pct", "ft_pct", "double_double", "triple_double", "won", "home"}


def _check_period_measures(q: Query) -> None:
    """A quarter or half narrows the relation to the period's line
    (:meth:`~association.query.player_games.Narrowed.narrow_periods`), where
    ``minutes`` still holds the whole game's (the played guard reads it) and
    plus-minus and the advanced columns are blank. A read measuring one of
    those under a period would print the game's figure under the quarter's
    heading - so it is refused, and the default line (which carries minutes)
    with it: the period templates say a period, and this compiler's sentence
    has not been measured saying one. A read grouped by ``period`` sees
    each quarter's line the same way, and is held to the same columns."""
    if period_narrowing(q.scope) is None and q.group != "period":
        return
    read = [*q.measures, *(name for name, _, _ in q.predicates)]
    unread = sorted({m for m in read if m not in _PERIOD_READABLE})
    if unread:
        raise Unsupported(f"a quarter or half rebuilds {', '.join(PERIOD_COLUMNS)} from the plays - not {unread}")


def _check_split_category(q: Query) -> None:
    """``split`` names a HALF (starter/bench) for a row filter; the category
    ``starter_bench`` is a table of both halves, which only a grouped read by
    starter answers - the templates' ``_SPLIT_SIDE_ONLY`` rule. Anything else
    would list every game under a heading that promised the split."""
    if q.scope.split == "starter_bench" and not (q.skeleton == "grouped" and q.group == "starter"):
        raise Unsupported("a starter/bench split is a table of both halves, not a filter - a grouped read answers it")


def _resolve_everyone(con: duckdb.DuckDBPyConnection, q: Query) -> tuple[Entity | None, _Span, Narrowed]:
    """The league-wide subject: every player's games in the span settled the
    way ``threshold_count``'s and ``single_game_high``'s no-player modes
    settle it, narrowed by :func:`~association.query.templates.common.league_games`.

    ``since``/``until`` bound the span exactly as
    :func:`~association.query.templates.common.scoped_player` bounds a named
    player's: every season from ``since`` on (to ``until``), never the
    current season alone. Read from ``season``/``span`` only, "players with
    33 point and 13 rebound ... games since 2000-01" listed 2026's three
    games under a heading saying so, where the warehouse holds eleven since
    2001 (yardstick-v2 F161, #207). ``since`` beside a named season is the
    same contradiction ``_span_of`` refuses for a player.
    """
    scope = q.scope
    season_type = scope.season_type or 2
    season = scope.season
    if season is None and scope.span != "career" and scope.since is None:
        season = current_season()
    try:
        span = _span_of("career" if season is None else None, season, season_type, "player_game_log", since=scope.since, until=scope.until)
    except TemplateUnsupported as exc:
        raise Unsupported(str(exc)) from exc
    # The shared steps read the slot dict until they take the Scope.
    narrowed = league_games(con, span, scope, position=q.position)
    if isinstance(narrowed, TemplateResult):
        raise Refused(narrowed)
    return None, span, narrowed


def _resolve_named(con: duckdb.DuckDBPyConnection, q: Query) -> tuple[Entity | None, _Span, Narrowed]:
    """The named-player subject, settled and narrowed exactly as the six
    relation templates settle and narrow their own - through
    :func:`~association.query.templates.common.scoped_player` and
    :func:`~association.query.templates.common.scoped_games`."""
    scope = q.scope
    # A date names its game outright, so it replaces the season rather than
    # being filtered inside it - the rule game_log and player_stat both
    # state: the name is settled over his career and the date is the scope.
    # The adapters set the point's span and season the same way, but a
    # ``None`` season read here could not mean "unset" - the slot's own
    # season came back and "Bam adebeyo jan 19" declined as "a career span
    # and the 2026 season at once" (#230).
    dated = bool(scope.date)
    subject = scoped_player(
        con,
        scope,
        "no player named",
        table="player_game_log",
        available=q.available or _GAME_LOGS,
        span="career" if dated else (q.span if q.span is not None else scope.span),
        season=None if dated else (q.season if q.season is not None else scope.season),
    )
    if isinstance(subject, TemplateResult):
        raise Refused(subject)
    player, span = subject
    # ``own_team`` ("lebron stats as a starter for Miami" - his games for
    # that team, written by subject._apply_own_team) narrows exactly as it
    # does for player_stat, the one template that reads it; ignored here, the
    # same question averaged his whole career's starts (1,612 games for 294).
    narrowed = scoped_games(con, player, span, scope, opponent=scope.opponent, measures=measure_filters(scope.below, scope.above), date=scope.date, team=scope.own_team)
    if isinstance(narrowed, TemplateResult):
        raise Refused(narrowed)
    return player, span, narrowed


def _resolve_subject(con: duckdb.DuckDBPyConnection, q: Query) -> tuple[Entity | None, _Span, Narrowed]:
    """The subject a point reads: a named player, or the league."""
    if q.subject == "everyone":
        return _resolve_everyone(con, q)
    return _resolve_named(con, q)


def _resolve_pair(con: duckdb.DuckDBPyConnection, q: Query) -> tuple[Entity, Entity, _Span, Narrowed]:
    """The two players a ``pair`` read is about: the first settled and his
    games narrowed exactly as a named player's are (:func:`_resolve_named`,
    with no window and no opponent - the newest meetings are shown beneath
    averages over all of them, and two players' meetings have no third team
    to narrow to), the second resolved over the same span, as the matchup
    template resolved both (``_resolved_player`` over the box scores). Two
    names that resolve to one person are no pair.

    .. versionadded:: 5.0.0
    """
    scope = q.scope
    texts = list(dict.fromkeys(n.strip() for n in [*scope.players, scope.player] if n is not None and n.strip()))
    if len(texts) != 2:
        raise Unsupported(f"player_matchup needs exactly two players, got {texts!r}")
    first = replace(q, scope=replace(scope, player=texts[0], limit=None, order=None, opponent=None))
    a, span, narrowed = _resolve_named(con, first)
    assert a is not None
    b = _resolved_player(con, texts[1], available=_BOX_SCORES, season=span.season, through=_career_end(span.season))
    if isinstance(b, TemplateResult):
        raise Refused(b)
    if a.id == b.id:
        raise Unsupported("the named players resolved to the same person")
    return a, b, span, narrowed


def _apply_predicates(narrowed: Narrowed, q: Query) -> None:
    """A ``Query.predicates`` entry as one more clause over the same rows."""
    for name, op, value in q.predicates:
        if op not in OPS:
            raise Unsupported(f"operator {op!r}")
        narrowed.narrow(f"{measure_sql(name)} {OPS[op]} ?", value)


def _apply_team_slot(con: duckdb.DuckDBPyConnection, q: Query, player: Entity | None, span: _Span, narrowed: Narrowed) -> Narrowed:
    """A ``team`` beside the player: on a ``rows`` read it is ``game_log``'s
    rule (his own team is dropped, another is his opponent, a name nothing
    resolves to is refused); on the condition skeletons (a count, a record, a
    grouped split - :func:`~association.query.templates.common.condition_player`'s
    own shape) it narrows to his games for that team. A per-game average
    (``player_stat``'s shape) reads no ``team`` slot at all - the real
    template ignores it outright, since :func:`~association.query.templates.common.scoped_games`
    itself carries no such narrowing, and treating it as a filter there would
    answer a narrower question than the template does."""
    scope = q.scope
    team_text = scope.team
    if team_text is None or not team_text.strip() or player is None:
        return narrowed
    if q.skeleton == "rows":
        resolved_opponent = _team_slot_for_player(con, player, team_text, season=scope.season, opponent=scope.opponent)
        if isinstance(resolved_opponent, TemplateResult):
            raise Refused(resolved_opponent)
        if resolved_opponent is not None and narrowed.opponent is None:
            rescoped = scoped_games(con, player, span, scope, opponent=resolved_opponent, measures=measure_filters(scope.below, scope.above), date=scope.date, team=scope.own_team)
            if isinstance(rescoped, TemplateResult):
                raise Refused(rescoped)
            narrowed = rescoped
        return narrowed
    if not (q.aggregate in ("count", "record") or q.skeleton in ("grouped", "run")):
        return narrowed
    team = _resolved_team(con, team_text, season=scope.season)
    if isinstance(team, TemplateResult):
        raise Refused(team)
    narrowed.narrow("pgl.team_id = ?", team.id)
    return narrowed


def _apply_window_rule(q: Query, narrowed: Narrowed) -> None:
    """A count, a record or a split is read over every game in the span
    unless the question ORDERED a window ("in his last 10"): a bare ``limit``
    is filler on those skeletons (:func:`~association.query.templates.common.whole_span`;
    ``threshold_count`` reads it as the ranking's size), and only ``rows``
    reads it as a row count."""
    scope = q.scope
    if q.skeleton in ("run", "pair"):
        # A run or a pair is read over every game in the span
        # (``whole_span``): a run's ``limit`` is how many runs are listed,
        # a matchup's the newest meetings shown beneath averages over all of
        # them, never a games window; ``order`` is refused before the point
        # is planned (``RELATION_SCOPING_EXCLUDED``, compose.adapt).
        narrowed.window = None
        return
    if (q.aggregate in ("count", "record") or q.skeleton == "grouped") and scope.order is None:
        limit = scope.limit
        # A limit on a grouped read by a SCOPE (season, month) is the number
        # of groups (grouped_sql applies it after grouping); on a split by
        # venue/starter or a record it can only mean a games window.
        if q.aggregate != "count" and q.group not in ("season", "month", "season_type", "opponent", "player") and limit is not None and limit > 1:
            # The templates' rule (player_splits, record_when): a real limit
            # with no order is "his last N games" - a window they refuse
            # rather than answer for the whole span.
            raise Unsupported("a limited number of recent games is game_log's question")
        narrowed.window = None


def _rebuilt_for(box: BoxSource, q: Query) -> bool:
    """The rebuilt-line rule, as the templates apply it. A grouped read and a
    record widen the guard to rebuilt lines and blank the columns a rebuild
    does not fill; a scalar over measures or a count widens only when every
    column read (measures AND predicate columns) is one a rebuild gets right."""
    if q.skeleton in ("run", "pair"):
        # A run or a pair reads whichever source the warehouse holds, as the
        # streak and matchup templates did (``box_source(con)``): a column
        # a rebuild does not fill is blank on a rebuilt row - a blank never
        # satisfies a run's condition, so the run ends there, and a meeting's
        # minutes are averaged over the games that carry them.
        return box.rebuilt
    read = [*q.measures, *(name for name, _, _ in q.predicates)]
    if q.skeleton == "rows":
        # A listing's own rule (``templates.games._rebuilt_readable``, which
        # ``game_log`` reads by): ``minutes`` is exempt rather than a
        # failure - play-by-play cannot recover it, so a rebuilt row prints
        # it blank - and every other column shown must be one a rebuild gets
        # right. Before this, a log or a top-games read carrying the default
        # line never showed a rebuilt game at all, since that line carries
        # minutes.
        read = [m for m in read if m != "minutes"]
    return box.rebuilt and ((q.skeleton == "grouped" or q.aggregate == "record") or (bool(read) and all(m in REBUILT_STATS for m in read)))


def _compile_rows(q: Query, narrowed: Narrowed, rebuilt: bool, player: Entity | None, span: _Span) -> Compiled:
    """A ``rows`` read: a log, or the top game(s) by a measure."""
    # A rows read applies its own limit (rows_sql never consults the window),
    # so the relation's window is not what cut these rows and must not be said.
    narrowed.window = None
    # A league-wide read lists games of many players, so each row carries
    # its player; measured on yardstick-v2 F124, a ranking of triple-doubles
    # by points printed dates and figures and never said whose they were.
    who = ["pgl.player_name AS player"] if q.subject != "player" else []
    select = ", ".join([_row_select(rebuilt=rebuilt), *who, *(f'{measure_sql(m, rebuilt=rebuilt)} AS "{m}"' for m in q.measures)])
    if q.order == "measure":
        if not q.measures:
            raise Unsupported("ordering by a measure needs one")
        # Ties by date, earliest first - the order single_game_high lists them.
        order = f"{measure_sql(q.measures[0])} {'ASC' if q.direction == 'asc' else 'DESC'} NULLS LAST, g.date ASC, pgl.event_id"
    else:
        order = f"g.date {'ASC' if q.direction == 'asc' else 'DESC'}, pgl.event_id"
    sql, params = rows_sql(narrowed, select, order=order, limit=q.limit, offset=q.offset, rebuilt=rebuilt)
    return Compiled(sql, params, player, span, narrowed, rebuilt, list(q.measures))


def _scalar_selects(q: Query, rebuilt: bool) -> list[str]:
    """The SELECT list for a ``scalar`` or ``grouped`` read: a count, a
    win-loss record, one aggregate per measure, and - guarded the same way
    :func:`_row_select` guards its own ``reconstructed`` column - how many of
    the counted games are rebuilt rather than fetched, for
    :func:`~association.query.templates.common._box_score_notes`' own
    rebuilt-line note (#197, ISSUES.md)."""
    selects = ["COUNT(*) AS games"]
    if q.aggregate == "record":
        selects += [
            "SUM(CASE WHEN g.winner_team_id = pgl.team_id THEN 1 ELSE 0 END) AS wins",
            "SUM(CASE WHEN g.winner_team_id IS NOT NULL AND g.winner_team_id <> pgl.team_id THEN 1 ELSE 0 END) AS losses",
        ]
        selects += [_agg(m, "per_game", rebuilt=rebuilt) for m in q.measures]
    elif q.aggregate != "count":
        selects += [_agg(m, q.aggregate, rebuilt=rebuilt) for m in q.measures]
    selects.append("SUM(CASE WHEN pgl.reconstructed THEN 1 ELSE 0 END) AS rebuilt_shown" if rebuilt else "0 AS rebuilt_shown")
    return selects


def _compile_scalar(q: Query, narrowed: Narrowed, rebuilt: bool, player: Entity | None, span: _Span) -> Compiled:
    """A ``scalar`` read: one row of aggregates over the narrowed games."""
    selects = _scalar_selects(q, rebuilt)
    sql, params = aggregate_sql(narrowed, selects, rebuilt=rebuilt)
    return Compiled(sql, params, player, span, narrowed, rebuilt, list(q.measures))


def _by_period_totals(q: Query, rebuilt: bool) -> list[str]:
    """Beside each quarter's per-game figures, the sums a presenter says
    them from: a column's total (``"<m>_total"``), a rate's makes and
    attempts (``"<m>_made"``, ``"<m>_attempted"``)."""
    extra: list[str] = []
    for m in q.measures:
        if m in PERIOD_RATES:
            made, attempted = PERIOD_RATES[m]
            extra += [f'SUM(pgl.{made}) AS "{m}_made"', f'SUM(pgl.{attempted}) AS "{m}_attempted"']
        elif m not in BOOLEAN_MEASURES:
            extra.append(f'SUM({measure_sql(m, rebuilt=rebuilt)}) AS "{m}_total"')
    return extra


def _compile_by_period(con: duckdb.DuckDBPyConnection, q: Query, narrowed: Narrowed, rebuilt: bool, player: Entity | None, span: _Span) -> Compiled:
    """A ``grouped`` read by ``period``: a named player's four quarters side
    by side ("Jokic points by quarter", #162). One statement, the union of
    four reads of the SAME narrowed games - the opponent, the venue, the
    window, every clause the relation applied once - each seeing one
    quarter's line (:func:`~association.query.templates.common._apply_period`
    on a copy, the way a single-quarter read is narrowed), so a quarter he
    played and did nothing in is a zero over a game he played, and a game the
    shot table does not cover is no game in any quarter. Regulation only
    (:data:`~association.query.player_games.REGULATION_QUARTERS`): overtime
    is no quarter, and the presenter says so.

    .. versionadded:: 5.0.0
    """
    selects = [*_scalar_selects(q, rebuilt), *_by_period_totals(q, rebuilt)]
    parts: list[str] = []
    params: list[Any] = []
    for quarter in REGULATION_QUARTERS:
        each = copy.deepcopy(narrowed)
        _apply_period(con, each, replace(q.scope, period=quarter, half=None))
        sql, each_params = aggregate_sql(each, [f'{quarter} AS "group"', *selects], rebuilt=rebuilt)
        parts.append(f"SELECT * FROM ({sql})")
        params += each_params
    return Compiled(" UNION ALL ".join(parts) + ' ORDER BY "group"', params, player, span, narrowed, rebuilt, list(q.measures))


def _compile_grouped(q: Query, narrowed: Narrowed, rebuilt: bool, player: Entity | None, span: _Span) -> Compiled:
    """A ``grouped`` read: a split, or a ranking of players."""
    if q.group not in GROUPS:
        raise Unsupported(f"no grouping {q.group!r}")
    selects = _scalar_selects(q, rebuilt)
    key, sel = GROUPS[q.group]
    if q.order == "measure":
        target = "games" if q.aggregate == "count" or not q.measures else f'"{q.measures[0]}"'
        order = f"{target} {'ASC' if q.direction == 'asc' else 'DESC'} NULLS LAST, 1"
    elif q.group == "season":
        # A history by season in date order, newest first unless asked
        # otherwise, so its limit keeps the LAST N seasons: ordered by the
        # label alone, "3pt% over the past 4 seasons" listed his first four
        # (Klay Thompson's 2012-2015 for 2023-2026).
        order = f"1 {'ASC' if q.direction == 'asc' else 'DESC'}"
    else:
        order = "1"
    having = f"COUNT(*) >= {int(q.minimum_games)}" if q.minimum_games else None
    sql, params = grouped_sql(narrowed, key, [sel, *selects], having=having, order=order, limit=q.limit, rebuilt=rebuilt)
    return Compiled(sql, params, player, span, narrowed, rebuilt, list(q.measures))


#: How many runs a named player's or team's answer lists - the longest and
#: any that tie it (``templates.splits._single_streak`` shows the ties).
DEFAULT_NAMED_RUNS = 3
"""The runs read for one named subject: the longest, plus two to tie it.

.. versionadded:: 5.0.0
"""


def run_scope(scope: Scope, *, named: bool) -> Any:
    """The games a run is read over, as the streak template's own
    :class:`~association.query.conditions._Scope`: a named player's honors
    ``since`` and reads an ordinal season over a career-shaped outer scope
    (``season_n``, the way ``scoped_player`` settles one); the league-wide
    read (``named=False``) takes the season or the career alone, as its
    retired branch did. Shared with the presenter, which names the span and
    counts the unseen games off the same scope.

    .. versionadded:: 5.0.0
    """
    if named:
        return _condition_scope(scope.season, "career" if scope.season_n else scope.span, scope.season_type, _PLAYER_GAME_TABLES, since=scope.since)
    return _condition_scope(scope.season, scope.span, scope.season_type, _PLAYER_GAME_TABLES)


def _compile_run(q: Query, narrowed: Narrowed, box: BoxSource, player: Entity | None, span: _Span) -> Compiled:
    """A ``run`` read: the longest runs of consecutive games the point's one
    predicate holds along, over the narrowed games in Eastern-date order -
    the streak's skeleton (ROADMAP plan item 6, step (g)), as
    :func:`~association.query.conditions._longest_runs_sql` reads it over
    :func:`~association.query.conditions._player_streak_rows`: each game he
    played, and each game with no box score inside a spell he was playing
    in, which never satisfies the condition and so ends a run rather than
    being carried across. A named player's run is his alone; the league-wide
    read keeps each player's own longest (``best_per_partition``), so a list
    is not one man's season five times.

    The predicate is ``(column, ">=", threshold)`` for a line, or
    ``("won", "=", True|False)`` for a run of wins or losses in the games he
    played. ``limit`` is how many runs come back (:data:`DEFAULT_NAMED_RUNS`
    for a named player).

    .. versionadded:: 5.0.0
    """
    if len(q.predicates) != 1:
        raise Unsupported("a run holds exactly one condition along it")
    name, op, value = q.predicates[0]
    if name == "won":
        if op != "=":
            raise Unsupported(f"a run of results is read as won = True or False, not {op!r}")
        hit, condition, read = "x.won = $want", {"want": bool(value)}, "NULL"
    else:
        if op not in OPS:
            raise Unsupported(f"operator {op!r}")
        if name not in COLUMNS or name in DERIVED:
            raise Unsupported(f"no per-game column {name!r} for a run to hold along")
        hit, condition, read = f"x.value {OPS[op]} $threshold", {"threshold": value}, f"p.{name}"
    covered = run_scope(q.scope, named=player is not None)
    # Named parameters: the played subquery is nested twice in the streak
    # rows (once for the games, once for the spells they span) beside
    # ``covered``'s own $season/$first, and the condition binds its own.
    base, params = named(*games_subquery(narrowed, box))
    rows = _player_streak_rows(covered, base, read, box)
    limit = q.limit if q.limit is not None else DEFAULT_NAMED_RUNS
    sql = _longest_runs_sql(rows, ("athlete_id",), hit, best_per_partition=player is None)
    # ``covered``'s own names bind only where the rows SQL uses them (the
    # missing-box half); DuckDB rejects a name a statement never reads.
    row_params = {**params, **covered.params()}
    return Compiled(sql, {**row_params, **condition, "limit": limit}, player, span, narrowed, box.rebuilt, [], run_rows=rows, run_params=row_params)


def _compile_pair(narrowed: Narrowed, box: BoxSource, a: Entity, b: Entity, span: _Span) -> Compiled:
    """A ``pair`` read: the games ``a`` and ``b`` both played on opposite
    teams, newest first, with both lines - the matchup's skeleton (ROADMAP
    plan item 6, step (g)), the pair relation
    (:func:`~association.query.player_games.paired_rows_sql`) over the first
    player's narrowed games, selecting what a meeting reads
    (:func:`~association.query.conditions._meetings_select`). A second
    player who is also the teammate named as absent is a question with no
    games in it, said so rather than answered "never met" (found by the
    golden the day ``without`` landed on the pair relation).

    .. versionadded:: 5.0.0
    """
    if any(absent.id == b.id for absent in narrowed.without):
        message = f"{b.name} is both the player {a.name} is matched against and the teammate named as absent - no game can be both. Name the opponent team, or drop 'without'."
        raise Refused(TemplateResult(data={"players": [a.name, b.name], "message": message}, answer=message))
    sql, params = paired_rows_sql(narrowed, b.id, _meetings_select(box), rebuilt=box.rebuilt)
    return Compiled(sql, params, a, span, narrowed, box.rebuilt, list(MEETING_STATS), other=b)


def compile_query(con: duckdb.DuckDBPyConnection, q: Query) -> Compiled:
    """``q`` as SQL, over the real relation. Each step below is one rule the
    six relation templates carried before this compiler existed - the
    comment on each names which.

    .. versionadded:: 4.4.0
    """
    if q.source != "games":
        raise Unsupported(f"the {q.source} source is read by the templates' own readers, not compiled")
    _check_relation_scoping(q.scope, q.subject)
    _check_split_category(q)
    _check_period_measures(q)
    player: Entity | None
    other: Entity | None = None
    if q.skeleton == "pair":
        player, other, span, narrowed = _resolve_pair(con, q)
    else:
        player, span, narrowed = _resolve_subject(con, q)
    if q.skeleton != "run":
        # A run's one predicate is the condition the run holds along, not a
        # row filter: a game that misses it ENDS the run rather than
        # leaving the pool (``_compile_run``).
        _apply_predicates(narrowed, q)
    narrowed = _apply_team_slot(con, q, player, span, narrowed)
    _apply_window_rule(q, narrowed)
    box = box_source(con)
    rebuilt = _rebuilt_for(box, q)
    if q.skeleton == "rows":
        return _compile_rows(q, narrowed, rebuilt, player, span)
    if q.skeleton == "scalar":
        return _compile_scalar(q, narrowed, rebuilt, player, span)
    if q.skeleton == "grouped" and q.group == "period":
        return _compile_by_period(con, q, narrowed, rebuilt, player, span)
    if q.skeleton == "grouped":
        return _compile_grouped(q, narrowed, rebuilt, player, span)
    if q.skeleton == "run":
        return _compile_run(q, narrowed, box, player, span)
    if q.skeleton == "pair":
        assert player is not None and other is not None
        return _compile_pair(narrowed, box, player, other, span)
    raise Unsupported(f"skeleton {q.skeleton!r}")


_POSITION_LABELS: dict[str, str] = {
    "C": "every center",
    "F": "every forward",
    "G": "every guard",
    "PG": "every point guard",
    "SG": "every shooting guard",
    "PF": "every power forward",
    "SF": "every small forward",
}


def _everyone_label(position: str | None) -> str:
    """The league-wide subject as an answer names it. A position narrowing
    is part of the subject and must be in the heading - a log of centers
    headed "every player" hides the value that was used."""
    return _POSITION_LABELS.get(position or "", "every player")


def _rebuilt_shown_count(q: Query, rows: list[dict[str, Any]]) -> int:
    """How many of the answer's rows rest on a rebuilt line, however the
    skeleton carries that count. A ``rows`` read has it per row
    (``reconstructed``, from :func:`_row_select`, already exposed on every
    row and left there); a ``scalar``/``grouped`` read has it as its own
    aggregate column (``rebuilt_shown``, from :func:`_scalar_selects`) -
    popped off each row here, so it never leaks into ``data["rows"]``, and
    summed across whatever groups came back."""
    if q.skeleton == "rows":
        return sum(1 for r in rows if r.get("reconstructed"))
    return sum(int(r.pop("rebuilt_shown", 0) or 0) for r in rows)


def _box_notes(con: duckdb.DuckDBPyConnection, q: Query, c: Compiled, rows: list[dict[str, Any]]) -> list[str]:
    """What this answer has to say about its own box scores - a teammate's
    absence explained, the empty lines left out of the count, the games
    rebuilt from play-by-play rather than fetched, and a career predating
    box scores entirely
    (:func:`~association.query.templates.common._box_score_notes`, the
    templates' own notes, threaded through here for the first time: #197,
    ISSUES.md). Only for a named player - the league-wide subject has no
    ONE player's career to check a floor against, which is what
    ``_box_score_notes`` assumes.

    .. versionchanged:: 4.4.0
       Pops the scratch ``rebuilt_shown`` column unconditionally, even for
       the league-wide subject this function otherwise has nothing to say
       about - it is :func:`_scalar_selects`' own internal column, never
       meant to reach a caller, and previously leaked into
       ``data["rows"]`` for exactly the subject this function returns early
       for.
    """
    rebuilt_shown = _rebuilt_shown_count(q, rows)
    if c.player is None:
        return []
    # A dated read's "career" span is only how the game was FOUND (binding
    # parity, K1 rule 6), not what the answer is about - the same reason
    # `game_log`'s own notes turn this note off for one.
    career_note = c.narrowed.date is None
    return _box_score_notes(con, c.player, c.span, c.narrowed, career_note=career_note, rebuilt=c.rebuilt, rebuilt_shown=rebuilt_shown)


def _grouped_total(con: duckdb.DuckDBPyConnection, q: Query, c: Compiled, rows: list[dict[str, Any]]) -> int | None:
    """The whole count behind a by-player count that a window cut: "thunder
    all-time triple doubles" lists ten players and is asked for the 193,
    not the ten rows' 176. Read by re-running the grouped read with no
    limit and summing; None for any other point."""
    if q.skeleton != "grouped" or q.aggregate != "count" or q.group != "player":
        return None
    if not q.limit or len(rows) < q.limit:
        return sum(int(r.get("games") or 0) for r in rows)
    whole = _compile_grouped(replace(q, limit=None), c.narrowed, c.rebuilt, c.player, c.span)
    cur = con.execute(whole.sql, whole.params)
    names = [d[0] for d in cur.description]
    return sum(int(dict(zip(names, r, strict=True)).get("games") or 0) for r in cur.fetchall())


def _player_own_seasons(con: duckdb.DuckDBPyConnection, player: Entity | None, span: _Span) -> tuple[int, int] | None:
    """A named player's own first and last season on record, for
    :func:`~association.query.compose.sentence._span_phrase` to name a plain
    career by instead of the relation's floor.

    ``threshold_count`` and ``single_game_high`` already read this from the
    player's own seasons (`templates.players._seasons_on_record`, via
    `_game_span`) rather than the box-score floor every career otherwise
    starts from, and this reuses that same read rather than a second one -
    "one concept, one definition" (`AGENTS.md`). Only for a *plain* career:
    a named season, or a career explicitly bounded by ``since``/``until``,
    already says exactly what it covers and is left alone (ISSUES.md, "The
    compiler's career span says '(1994 on)' where the template named the
    player's own seasons").

    .. versionadded:: 5.0.0
    """
    if player is None or span.season is not None or span.since is not None:
        return None
    began, ended = _seasons_on_record(con, player.id, span.season_type)
    if not isinstance(began, int) or not isinstance(ended, int):
        return None
    return began, ended


def run(con: duckdb.DuckDBPyConnection, q: Query) -> dict[str, Any]:
    """Compile and execute: rows as dicts, with what the relation settled.

    .. versionchanged:: 4.4.0
       Carries ``notes`` - the box-score caveats a named player's answer
       rests on (:func:`_box_notes`), which :func:`~association.query.compose.answer`
       appends to the sentence the same way it already appends
       :func:`~association.query.templates.common.coverage_caveat` (#197,
       ISSUES.md).

    .. versionchanged:: 5.0.0
       Carries ``player_seasons`` (:func:`_player_own_seasons`), so a plain
       career sentence names the player's own seasons rather than the
       relation's floor.

    .. versionchanged:: 5.0.0
       Carries ``entity`` (the resolved player, or ``None`` for the league)
       and ``rebuilt_by_row`` (each row's own rebuilt-game count), which
       :mod:`association.query.compose.present` phrases a template's own
       answer from.
    """
    try:
        c = compile_query(con, q)
    except TemplateUnsupported as exc:
        raise Unsupported(f"relation: {exc}") from exc
    cur = con.execute(c.sql, c.params)
    names = [d[0] for d in cur.description]
    rows = [dict(zip(names, r, strict=True)) for r in cur.fetchall()]
    # Each grouped row's own rebuilt count, read before _box_notes pops the
    # scratch column - a by-player count says how many of the LEADER's games
    # were rebuilt (compose.present, threshold_count's own note).
    rebuilt_by_row = [int(r.get("rebuilt_shown") or 0) for r in rows]
    notes = _box_notes(con, q, c, rows)
    return {
        "rows": rows,
        "entity": c.player,
        "rebuilt_by_row": rebuilt_by_row,
        "total": _grouped_total(con, q, c, rows),
        "player": c.player.name if c.player else _everyone_label(q.position),
        "span": c.span,
        "player_seasons": _player_own_seasons(con, c.player, c.span),
        "narrowing": c.narrowed.filters(windowed=True),
        "window": c.narrowed.window,
        "sql": c.sql,
        "params": c.params,
        "rebuilt": c.rebuilt,
        "measures": c.measures,
        "notes": notes,
    }
