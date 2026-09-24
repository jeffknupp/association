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

from dataclasses import dataclass, field
from typing import Any

import duckdb

from association.nba.season import current_season, eastern_date_sql
from association.query.conditions import UNGATED_ON_REBUILD, BoxSource, box_source
from association.query.entities import Entity
from association.query.measures import MEASURE_WORDS
from association.query.player_games import REBUILT_STATS, Narrowed, aggregate_sql, grouped_sql, rows_sql
from association.query.templates.common import (
    _GAME_LOGS,
    RELATION_SCOPING,
    SCOPING_SLOTS,
    TemplateResult,
    TemplateUnsupported,
    _box_score_notes,
    _resolved_team,
    _Span,
    _span_of,
    league_games,
    measure_filters,
    scoped_games,
    scoped_player,
)
from association.query.templates.games import _team_slot_for_player

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
    "ts_pct": ("SUM(pgl.points) * 100.0", "NULLIF(2 * (SUM(pgl.fieldGoalsAttempted) + 0.44 * SUM(pgl.freeThrowsAttempted)), 0)"),
    "efg_pct": ("(SUM(pgl.fieldGoalsMade) + 0.5 * SUM(pgl.threePointFieldGoalsMade)) * 100.0", "NULLIF(SUM(pgl.fieldGoalsAttempted), 0)"),
}
"""A percentage measure's numerator and denominator, summed over games rather
than averaged per game.

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


@dataclass
class Query:
    """A point over the player-games relation. ``slots`` is the question's own
    slot dict (subject, span, and every scoping slot), handed whole to the
    relation - the same discipline :func:`~association.query.templates.common.scoped_games`
    already keeps: a slot read here and not passed through would be a second,
    quieter way to narrow by hand.

    .. versionadded:: 4.4.0
    """

    #: The question's slot dict, forwarded whole to the relation.
    slots: dict[str, Any]
    #: ``"rows"``, ``"scalar"`` or ``"grouped"`` - which reader answers the point.
    skeleton: str = "rows"
    #: The box-score columns (or derived measures) the point reads.
    measures: list[str] = field(default_factory=lambda: list(LINE))
    #: ``"none"``, ``"per_game"``, ``"total"``, ``"count"``, ``"max"``, ``"min"``, ``"rate"`` or ``"record"``.
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
    params: list[Any]
    player: Entity | None
    span: Any
    narrowed: Narrowed
    rebuilt: bool
    measures: list[str]


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


def _agg(name: str, aggregate: str, *, rebuilt: bool = False) -> str:
    """One aggregate SELECT expression for ``name``, labeled with its own name."""
    expr = measure_sql(name, rebuilt=rebuilt)
    if aggregate == "per_game":
        if name in RATES:
            num, den = RATES[name]
            return f'({num} / {den}) AS "{name}"'
        return f'AVG({expr}) AS "{name}"'
    if aggregate == "total":
        return f'SUM({expr}) AS "{name}"'
    if aggregate == "max":
        return f'MAX({expr}) AS "{name}"'
    if aggregate == "min":
        return f'MIN({expr}) AS "{name}"'
    if aggregate == "rate":
        if name not in RATES:
            raise Unsupported(f"no ratio-of-sums rate for {name!r}")
        num, den = RATES[name]
        return f'({num} / {den}) AS "{name}"'
    raise Unsupported(f"aggregate {aggregate!r}")


def _iso_date(slots: dict[str, Any]) -> str | None:
    """The ``date`` slot, read only where it is the router's calendar form
    (``YYYY-MM-DD``) - the same check every reader of this slot makes
    (``player_stat``'s own ``_ISO_DATE``), so a non-date value the router
    sometimes files there (``"TUESDAY"``, a weekday word meant for
    ``situation``) is never handed to :func:`eastern_day_utc_range` as though
    it were one."""
    raw = slots.get("date")
    return raw if isinstance(raw, str) and len(raw) == 10 else None


#: Scoping slots the router files FOR the compiler - markers a template refuses
#: on so the question reaches here, which the compiler then reads itself:
#: ``ranked_by`` (the games that satisfy a boolean stat, ranked by another
#: measure - move.py reads the measure off the question) and ``team_restored``
#: (a team the question named as its own subject - the team path's marker).
#: Neither narrows the relation, so neither is "unhonored" here; measured
#: live on yardstick-v2 F124, the marker alone sent the question to the agent.
#:
#: .. versionadded:: 4.4.0
COMPILER_SLOTS: frozenset[str] = frozenset({"ranked_by", "team_restored"})


def _check_relation_scoping(slots: dict[str, Any]) -> None:
    """``check_scope``'s rule, for the relation: a scoping slot the relation
    does not narrow by (``situation`` when it names no calendar, ``round``,
    ``rate`` ...) is refused, never dropped - answering "on Tuesdays" for
    every day is the silent widening the templates exist to stop."""
    unhonored = sorted(k for k in SCOPING_SLOTS - RELATION_SCOPING - COMPILER_SLOTS if slots.get(k) not in (None, "", [], False))
    if unhonored:
        raise Unsupported(f"the relation cannot honor {unhonored} - it would answer for a different span than was asked")


def _check_split_category(q: Query) -> None:
    """``split`` names a HALF (starter/bench) for a row filter; the category
    ``starter_bench`` is a table of both halves, which only a grouped read by
    starter answers - the templates' ``_SPLIT_SIDE_ONLY`` rule. Anything else
    would list every game under a heading that promised the split."""
    if q.slots.get("split") == "starter_bench" and not (q.skeleton == "grouped" and q.group == "starter"):
        raise Unsupported("a starter/bench split is a table of both halves, not a filter - a grouped read answers it")


def _resolve_everyone(con: duckdb.DuckDBPyConnection, q: Query) -> tuple[Entity | None, _Span, Narrowed]:
    """The league-wide subject: every player's games in the span settled the
    way ``threshold_count``'s and ``single_game_high``'s no-player modes
    settle it, narrowed by :func:`~association.query.templates.common.league_games`."""
    slots = q.slots
    season_type = slots.get("season_type") or 2
    season = slots.get("season") if isinstance(slots.get("season"), int) else None
    if season is None and slots.get("span") != "career":
        season = current_season()
    span = _span_of("career" if season is None else None, season, season_type, "player_game_log")
    narrowed = league_games(con, span, slots, position=q.position)
    if isinstance(narrowed, TemplateResult):
        raise Refused(narrowed)
    return None, span, narrowed


def _resolve_named(con: duckdb.DuckDBPyConnection, q: Query) -> tuple[Entity | None, _Span, Narrowed]:
    """The named-player subject, settled and narrowed exactly as the six
    relation templates settle and narrow their own - through
    :func:`~association.query.templates.common.scoped_player` and
    :func:`~association.query.templates.common.scoped_games`."""
    slots = q.slots
    subject = scoped_player(
        con,
        slots,
        "no player named",
        table="player_game_log",
        available=q.available or _GAME_LOGS,
        span=q.span if q.span is not None else slots.get("span"),
        season=q.season if q.season is not None else slots.get("season"),
    )
    if isinstance(subject, TemplateResult):
        raise Refused(subject)
    player, span = subject
    narrowed = scoped_games(con, player, span, slots, opponent=slots.get("opponent"), measures=measure_filters(slots.get("below"), slots.get("above")), date=_iso_date(slots))
    if isinstance(narrowed, TemplateResult):
        raise Refused(narrowed)
    return player, span, narrowed


def _resolve_subject(con: duckdb.DuckDBPyConnection, q: Query) -> tuple[Entity | None, _Span, Narrowed]:
    """The subject a point reads: a named player, or the league."""
    if q.subject == "everyone":
        return _resolve_everyone(con, q)
    return _resolve_named(con, q)


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
    slots = q.slots
    team_text = slots.get("team")
    if not (isinstance(team_text, str) and team_text.strip()) or player is None:
        return narrowed
    if q.skeleton == "rows":
        season = slots.get("season") if isinstance(slots.get("season"), int) else None
        resolved_opponent = _team_slot_for_player(con, player, team_text, season=season, opponent=slots.get("opponent"))
        if isinstance(resolved_opponent, TemplateResult):
            raise Refused(resolved_opponent)
        if resolved_opponent is not None and narrowed.opponent is None:
            rescoped = scoped_games(con, player, span, slots, opponent=resolved_opponent, measures=measure_filters(slots.get("below"), slots.get("above")), date=_iso_date(slots))
            if isinstance(rescoped, TemplateResult):
                raise Refused(rescoped)
            narrowed = rescoped
        return narrowed
    if not (q.aggregate in ("count", "record") or q.skeleton == "grouped"):
        return narrowed
    team = _resolved_team(con, team_text, season=slots.get("season") if isinstance(slots.get("season"), int) else None)
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
    slots = q.slots
    if (q.aggregate in ("count", "record") or q.skeleton == "grouped") and slots.get("order") not in ("recent", "first"):
        limit = slots.get("limit")
        # A limit on a grouped read by a SCOPE (season, month) is the number
        # of groups (grouped_sql applies it after grouping); on a split by
        # venue/starter or a record it can only mean a games window.
        if q.aggregate != "count" and q.group not in ("season", "month", "season_type", "opponent", "player") and isinstance(limit, int) and not isinstance(limit, bool) and limit > 1:
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
    read = [*q.measures, *(name for name, _, _ in q.predicates)]
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


def _compile_grouped(q: Query, narrowed: Narrowed, rebuilt: bool, player: Entity | None, span: _Span) -> Compiled:
    """A ``grouped`` read: a split, or a ranking of players."""
    if q.group not in GROUPS:
        raise Unsupported(f"no grouping {q.group!r}")
    selects = _scalar_selects(q, rebuilt)
    key, sel = GROUPS[q.group]
    if q.order == "measure":
        target = "games" if q.aggregate == "count" or not q.measures else f'"{q.measures[0]}"'
        order = f"{target} {'ASC' if q.direction == 'asc' else 'DESC'} NULLS LAST, 1"
    else:
        order = "1"
    having = f"COUNT(*) >= {int(q.minimum_games)}" if q.minimum_games else None
    sql, params = grouped_sql(narrowed, key, [sel, *selects], having=having, order=order, limit=q.limit, rebuilt=rebuilt)
    return Compiled(sql, params, player, span, narrowed, rebuilt, list(q.measures))


def compile_query(con: duckdb.DuckDBPyConnection, q: Query) -> Compiled:
    """``q`` as SQL, over the real relation. Each step below is one rule the
    six relation templates carried before this compiler existed - the
    comment on each names which.

    .. versionadded:: 4.4.0
    """
    _check_relation_scoping(q.slots)
    _check_split_category(q)
    player, span, narrowed = _resolve_subject(con, q)
    _apply_predicates(narrowed, q)
    narrowed = _apply_team_slot(con, q, player, span, narrowed)
    _apply_window_rule(q, narrowed)
    rebuilt = _rebuilt_for(box_source(con), q)
    if q.skeleton == "rows":
        return _compile_rows(q, narrowed, rebuilt, player, span)
    if q.skeleton == "scalar":
        return _compile_scalar(q, narrowed, rebuilt, player, span)
    if q.skeleton == "grouped":
        return _compile_grouped(q, narrowed, rebuilt, player, span)
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


def run(con: duckdb.DuckDBPyConnection, q: Query) -> dict[str, Any]:
    """Compile and execute: rows as dicts, with what the relation settled.

    .. versionchanged:: 4.4.0
       Carries ``notes`` - the box-score caveats a named player's answer
       rests on (:func:`_box_notes`), which :func:`~association.query.compose.answer`
       appends to the sentence the same way it already appends
       :func:`~association.query.templates.common.coverage_caveat` (#197,
       ISSUES.md).
    """
    try:
        c = compile_query(con, q)
    except TemplateUnsupported as exc:
        raise Unsupported(f"relation: {exc}") from exc
    cur = con.execute(c.sql, c.params)
    names = [d[0] for d in cur.description]
    rows = [dict(zip(names, r, strict=True)) for r in cur.fetchall()]
    notes = _box_notes(con, q, c, rows)
    return {
        "rows": rows,
        "player": c.player.name if c.player else _everyone_label(q.position),
        "span": c.span,
        "narrowing": c.narrowed.filters(windowed=True),
        "window": c.narrowed.window,
        "sql": c.sql,
        "params": c.params,
        "rebuilt": c.rebuilt,
        "measures": c.measures,
        "notes": notes,
    }
