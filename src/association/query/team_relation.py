"""The team-games relation's shared steps: settling the team a question is about
and the seasons it covers, and narrowing its games by every cell the relation
carries (:data:`TEAM_RELATION_SCOPING`).

The counterpart of :mod:`association.query.player_relation` over
:class:`~association.query.team_games.TeamNarrowed`. A module of its own
rather than part of :mod:`association.query.team_games` because these steps
read the span and window steps on the player relation's side, which imports
:mod:`association.query.conditions`, which imports ``team_games`` - one module
would be an import cycle.

.. versionadded:: 5.0.0
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import duckdb

from association.nba.coverage import COVERAGE, POSTSEASON, REGULAR_SEASON
from association.query.entities import Entity, name_missing, resolved_team
from association.query.player_games import BOTH_SEASON_TYPES
from association.query.player_relation import RELATION_SCOPING_EXCLUDED, ResolvedSpan, apply_situation, has_table, relation_window, span_of
from association.query.reading import Cuts, DeclineReason, Filtered, Measure, Period, PointShape, ShapeCells, ShapeDeclined, Span, Unsupported, period_narrowing
from association.query.reading import Line as ReadLine
from association.query.result import Refusal, Unanswered
from association.query.season_text import season_phrase
from association.query.team_games import TEAM_GAMES_SQL, TeamNarrowed, aggregate_sql, game_list_gaps_sql, season_game_counts_sql

# What the team-games relation narrows by, declared ONCE - the team
# counterpart of RELATION_SCOPING. Every reader (and the team compiler) that
# settles its team and span through `scoped_team` and its games through
# `team_games` honors these:
# an opponent, a venue, one Eastern date, a career that starts partway through
# (`since`), one game of each playoff series (`game_n`), and a window of the
# newest or oldest N of the narrowed games (the typed `reading.Window`'s
# `window` cell, an end with its count). `span`
# ("career") is here too, even though it is settled by `scoped_team`/`span_of`
# rather than narrowed by `team_games` itself - the same shape RELATION_SCOPING
# already keeps `season_n` in for the player relation, which `scoped_player`
# settles the same way.
#
# `without` has a team meaning - the games a TEAMMATE missed - but that is
# with_without's own question, not a team's plain games; `split`
# (starter/bench) and `below`/`above` (a line on a box-score column) have no
# shared team-scale reading yet (ISSUES.md, "record_when's team branch and
# streak's team/league branches still refuse ..."), so none of the three is a
# cell the relation itself carries. A read that wants one leans on
# `TeamNarrowed.narrow` directly, the way the team's record over its own line
# (`compose.team.compile_team_line`) joins `team_box_stats` for its own stat
# threshold.
#
# `situation` and `until` keep the team relation at parity with the player
# one's own RELATION_SCOPING:
# `situation` is a calendar narrowing - a weekday, a month, a fixed holiday,
# or "since <month day>" within each game's own season - applied by
# `team_games` itself via `TeamNarrowed.narrow_calendar`, the exact mirror of
# `scoped_games`' own reading for the player relation, OR an
# opponent's conference or division for that game's own season, via
# `TeamNarrowed.narrow_alignment` - both read by the one `apply_situation`
# helper both relations' shared steps call; `until` is the
# inclusive LAST season of a `since`-bounded range (a decade, "2019-20 to
# 2023-24"), read the same way `since` already is (`span_of`/`scoped_team`) -
# never alone (`validated_until`), so a reader honors it only by also
# honoring `since`.
#
# `period` is the period relation's team half (the typed `Period`, one cell
# for a quarter or a half): it
# narrows what a read SEES of each game - the linescore's points, and every
# other column rebuilt from the plays (`team_games.team_period_line_sql`) -
# applied by `team_games` through `TeamNarrowed.narrow_periods`, the
# counterpart of the player relation's.
#: The games' cuts a team's games carry (:attr:`~association.query.reading.Cuts.CELLS`
#: less three): no game is labeled by its ``round``, and a ``tenure`` and
#: an ordinal ``season_n`` are one player's.
#:
#: .. versionadded:: 6.0.0
TEAM_CUTS: frozenset[str] = Cuts.CELLS - {"round", "tenure", "season_n"}
TEAM_RELATION_SCOPING = frozenset({"window", *Period.CELLS, *TEAM_CUTS, *Span.CELLS})
"""The cells every reader on the team-games relation honors: the games'
cuts a team's games carry (:data:`TEAM_CUTS`: ``opponent``, ``venue``,
``date``, ``situation``, ``game_n``, each applied by :func:`team_games`
and said by :meth:`~association.query.team_games.TeamNarrowed.filters`),
the window, a quarter or half, and the span's three cells
(:attr:`~association.query.reading.Span.CELLS`) -
``career``, ``range`` (``since``, ``until``) and ``both`` - each applied
by :func:`~association.query.player_relation.span_of` through
:func:`scoped_team` and :func:`team_span_clause` (a postseason by the
calendar year it was played in), said by :func:`team_span_label`, and
refused where the span contradicts itself by ``span_of``. ``both`` is read
by a team's log and record, which merge or combine the two types and say
so; a reader whose words do not state a cell steps aside for it, or
refuses it, by :data:`TEAM_RELATION_SCOPING_EXCLUDED`.

.. versionadded:: 4.4.0

.. versionchanged:: 4.4.0
   Adds ``situation`` and ``until`` (step 3, K1).

.. versionchanged:: 5.0.0
   Adds ``period`` and ``half`` (the period relation's team half).

.. versionchanged:: 6.0.0
   The span's cells by name (``career``, ``range``, ``both``) in place of
   the slots ``span``, ``since`` and ``until``; the games' cuts by name; the
   period's one cell (``period``, a quarter or a half) in place of the slots
   ``period`` and ``half``.
"""


# Each shape's row on the team relation - the team counterpart of
# RELATION_SCOPING_EXCLUDED, keyed the same way (`reading.PointShape`) and
# read by the same one check (`compose.plan.cells_unhonored`): the cells a
# shape's words do not state, each with why, and what the planner does about
# one. A reason has to be about the shape's answer, not its code - the same
# rule RELATION_SCOPING_EXCLUDED follows.
#
# A team's log, splits and record over a line take a player's cells beyond
# the team relation's (`taken`): a teammate's absence or role, a line on a
# box-score column (which the log and the record read on the team's own
# figure or its opponents', ISSUES.md #430), an ordinal season - refused in
# the reader's own words where they do not read it,
# which say where the question belongs (`compose.logs._team_log_refusals`,
# `compose.splits`, `compose.records.read_team_record_when`); anything else a
# team's games do not carry is the planner's refusal ("a team's games cannot
# be narrowed by [...]").
TEAM_RELATION_SCOPING_EXCLUDED: dict[PointShape, ShapeCells] = {
    # A team's log keeps its games past a line on its own figure or its
    # opponents' (ISSUES.md #430: "when allowing 120+ points"), as the record
    # over a line does.
    PointShape("team_games", "rows", "date"): ShapeCells(
        unstated=RELATION_SCOPING_EXCLUDED[PointShape("player_games", "rows", "date")].unstated,
        taken=frozenset({"companion", "line", "season_n"}) | ReadLine.OPPONENT_CELLS,
    ),
    PointShape("team_games", "split", "splits"): ShapeCells(
        unstated=RELATION_SCOPING_EXCLUDED[PointShape("player_games", "split", "splits")].unstated,
        taken=frozenset({"companion", "line", "season_n", "split"}),
    ),
    # A team's record over a line reads the line on its opponents' figure
    # too (`Line.whose`, ISSUES.md #426): the other side of each game, which
    # no other shape of either relation reads - it refuses the cell.
    PointShape("team_games", "split", "line"): ShapeCells(
        unstated=RELATION_SCOPING_EXCLUDED[PointShape("player_games", "split", "line")].unstated,
        taken=frozenset({"companion", "line", "season_n"}) | ReadLine.OPPONENT_CELLS,
    ),
    # A team's or the league's run of wins: the player relation's run, and its
    # two refusals for a run no named player's games make (a team's, the
    # league's).
    PointShape("team_games", "runs", "won"): RELATION_SCOPING_EXCLUDED[PointShape("player_games", "runs", "line")],
    # The with/without split's words: a career or one season, the companions
    # it divides by and holds to their roles, and one opponent (both rows
    # narrow together, #163); nothing else that would narrow the teammates'
    # games without its sentence saying so. Its reader is the point's only
    # answer: the team compiler has no reading of a split narrowed beyond it.
    PointShape("team_games", "split", "presence"): ShapeCells(
        unstated={
            "range": DeclineReason.PRESENCE_RANGE,
            "both": DeclineReason.PRESENCE_BOTH,
            **dict.fromkeys(sorted(TEAM_CUTS - {"opponent"}), DeclineReason.PRESENCE_CUTS),
            "window": DeclineReason.PRESENCE_WINDOW,
            "period": DeclineReason.PRESENCE_PERIOD,
        },
        taken=frozenset({"companion"}),
        declined="plan",
    ),
    # The shapes whose reader is the point's only answer: every meeting in a season, on a
    # date, at a venue, or over a since-bounded or whole-career span (a window
    # and `game_n` pick out a subset of the tally); a team's quarter or half
    # over the relation's whole set, the quarter the relation's own
    # narrowing; a team's record, by month as well ("including the playoffs"
    # both season types together).
    PointShape("team_games", "comparison", "opponent"): ShapeCells(
        unstated={
            # The window is read: the last or first N meetings, sliced from
            # the one read (compose.meetings).
            "game_n": DeclineReason.MEETINGS_TALLY_GAME_N,
            # A team's quarter and its record read the calendar narrowing;
            # the meetings' tally does not - a weekday or holiday cut of an
            # all-time series is a real question no reader answers.
            "situation": DeclineReason.MEETINGS_TALLY_SITUATION,
            "period": DeclineReason.MEETINGS_TALLY_PERIOD,
            "both": DeclineReason.MEETINGS_TALLY_BOTH,
        },
        declined="plan",
    ),
    PointShape("team_periods", "scalar", "total"): ShapeCells(
        unstated={"both": DeclineReason.TEAM_PERIOD_BOTH},
        declined="plan",
    ),
    # A record for one game is a single result, which a log already
    # answers directly, and a record over a limited number of recent games is
    # the same substitution the player relation refuses for the same two
    # cells. `since` and `game_n` are read: `since` the same since-bounded
    # `ResolvedSpan` a career already reads (`compose.team_records._record_narrowed`),
    # `game_n` one game of each playoff series (`compose.team_records`).
    PointShape("team_games", "scalar", "record"): ShapeCells(
        unstated={
            "date": DeclineReason.TEAM_RECORD_ONE_GAME,
            "window": DeclineReason.TEAM_RECORD_RECENT_GAMES,
            "period": DeclineReason.TEAM_RECORD_WHOLE_GAMES,
        },
        taken=frozenset({"split"}),
        declined="plan",
    ),
    # A team's best single game, or every team's: its games ranked by one
    # figure over the span, at a venue,
    # against an opponent, on a weekday, month or holiday, or in one game of
    # a series - the point's only answer.
    PointShape("team_games", "rows", "measure"): ShapeCells(
        unstated={
            "window": DeclineReason.TEAM_HIGH_WINDOW,
            "period": DeclineReason.TEAM_HIGH_PERIOD,
            "both": DeclineReason.TEAM_HIGH_BOTH,
            "date": DeclineReason.TEAM_HIGH_ONE_GAME,
        },
        declined="plan",
    ),
    # The team-season readers (compose.team_stats, over the standings and the
    # power index) settle no span of their own: one season's line, one
    # season's projection, or a ranking of one season's (or one since-bounded
    # span's) teams. Each declines what its words do not state when it is
    # asked (`declined="read"`): a team's own total the words read on the
    # team relation is said as this shape (`point.TEAM_SEASON_POINTS`), and
    # the team compiler's sum - which reads a unit, `taken` - answers where
    # the season reader declines.
    PointShape("team_seasons", "scalar", "line"): ShapeCells(
        unstated={
            "career": DeclineReason.TEAM_LINE_CAREER,
            "range": DeclineReason.TEAM_LINE_RANGE,
            "both": DeclineReason.TEAM_LINE_BOTH,
            **dict.fromkeys(sorted(TEAM_CUTS), DeclineReason.TEAM_LINE_CUTS),
            "window": DeclineReason.TEAM_LINE_WINDOW,
            "period": DeclineReason.TEAM_LINE_PERIOD,
            "rate": DeclineReason.TEAM_LINE_RATE,
        },
        taken=Measure.CELLS,
        declined="read",
    ),
    # A team's line season by season over a range or every season on record
    # (ISSUES.md #372): the season line's rows, one per season, with the
    # range's total - every cell the season line does not state but the span.
    PointShape("team_seasons", "split", "season"): ShapeCells(
        unstated={
            "both": DeclineReason.TEAM_HISTORY_BOTH,
            **dict.fromkeys(sorted(TEAM_CUTS), DeclineReason.TEAM_HISTORY_CUTS),
            "window": DeclineReason.TEAM_HISTORY_WINDOW,
            "period": DeclineReason.TEAM_HISTORY_PERIOD,
            "rate": DeclineReason.TEAM_HISTORY_RATE,
        },
        taken=Measure.CELLS,
        declined="read",
    ),
    PointShape("team_snapshots", "scalar", "projection"): ShapeCells(
        unstated={
            "career": DeclineReason.PROJECTION_SPAN,
            "range": DeclineReason.PROJECTION_SPAN,
            "both": DeclineReason.PROJECTION_BOTH,
            **dict.fromkeys(sorted(TEAM_CUTS), DeclineReason.PROJECTION_CUTS),
            "window": DeclineReason.PROJECTION_WINDOW,
            "period": DeclineReason.PROJECTION_PERIOD,
            "rate": DeclineReason.PROJECTION_RATE,
        },
        taken=Measure.CELLS,
        declined="read",
    ),
    # A leaderboard ranks one season's (or one since-bounded span's) teams
    # against each other; none of these narrow that pool to a single game or
    # a single opponent, and a career total across every season on record is
    # not built.
    PointShape("team_seasons", "ranking", "team"): ShapeCells(
        unstated={
            "opponent": DeclineReason.TEAM_RANKING_OPPONENT,
            "date": DeclineReason.TEAM_RANKING_DATE,
            "career": DeclineReason.TEAM_RANKING_CAREER,
            "both": DeclineReason.TEAM_RANKING_BOTH,
            "window": DeclineReason.TEAM_RANKING_WINDOW,
            "game_n": DeclineReason.TEAM_RANKING_GAME_N,
            # A ranking ranks a season or a since/until-bounded
            # span of them; narrowing that pool to one weekday, month or holiday
            # within it is a different question from ranking the span itself.
            "situation": DeclineReason.TEAM_RANKING_SITUATION,
            "period": DeclineReason.TEAM_RANKING_PERIOD,
            "rate": DeclineReason.TEAM_RANKING_RATE,
        },
        taken=Measure.CELLS,
        declined="read",
    ),
}
"""Per shape, what its reader's words do not state of the team relation's
cells (:data:`TEAM_RELATION_SCOPING`), why, what it takes beyond them, and
what the planner does about a cell it does not state
(:class:`~association.query.reading.ShapeCells`).

.. versionadded:: 4.4.0

.. versionchanged:: 6.0.0
   The span's cells (``career``, ``range``, ``both``) per reader, the
   team-season readers' and the with/without split's included (Phase 3,
   step 2); the games' cuts per reader whose words state fewer than the
   relation's.

.. versionchanged:: 6.0.0
   Keyed by :class:`~association.query.reading.PointShape`, one
   :class:`~association.query.reading.ShapeCells` per shape a reader on the
   team relation reads, a team's log, splits, record over a line and run
   among them (Phase 3, step 2's closing slice): by the retired templates'
   names until then, the three team readers above said through the PLAYER
   relation's rows, the cells they refuse in their own words through
   ``compose.plan._TEAM_READER_REFUSES``, and the rest through
   ``team_relation_scoping``, ``team_relation_span`` and
   ``team_relation_cuts``, all deleted.
"""


def scoped_team(con: duckdb.DuckDBPyConnection, scope: Filtered, needs: str, *, span: Span | None = None) -> tuple[Entity, ResolvedSpan] | Unanswered:
    """The team a question is about and the seasons it covers - the team
    counterpart of :func:`scoped_player`. A franchise's name is a fact about a
    season (see :func:`resolved_team`: "Hornets" is New Orleans in 2008 and
    Charlotte in 2026), so the span is settled first and the team's name read
    against the season it settles on - the same order ``scoped_player`` keeps,
    even though a team's name (unlike an ambiguous player's) never needs the
    span to disambiguate it.

    Takes: ``con``; ``scope`` (read here for ``team`` and its typed span);
    ``needs`` (what the read needs a name of, ``"team"``: the
    ``name_missing`` decline, :func:`~association.query.entities.name_missing`,
    when no team was named); ``span`` (a :class:`~association.query.reading.Span` overriding
    the scope's own, the same as ``scoped_player``'s, so a caller with a
    reason to override it can).

    Returns ``(team, span)``, or the :class:`~association.query.result.Clarify` asking which team was
    meant. Honors ``since`` the same way :func:`scoped_player` does for a
    player - a career that starts partway through, rather than at the table's
    own floor - and ``until`` beside it: the
    inclusive last season of a range, read by :func:`span_of`.

    .. versionadded:: 4.4.0

    .. versionchanged:: 5.0.0
       Reads the typed :class:`~association.query.reading.Filtered`. A slot dict
       was taken until every caller passed ``reading``, the tests last,
       through their slot door (``tests/slots.py``, deleted in Phase 4,
       step 0).
    """
    seasons = span_of(span if span is not None else scope.span, "games")
    if scope.who.team is None:
        raise name_missing(needs)
    team = resolved_team(con, scope.who.team, season=seasons.season)
    if isinstance(team, Unanswered):
        return team
    return team, seasons


def team_span_clause(span: ResolvedSpan) -> tuple[str, list[Any]]:
    """``tg.season``/``tg.eastern_date`` clause for a team's games in
    ``span``, over :data:`association.query.team_games.TEAM_GAMES_SQL`'s
    ``team_games``.

    A postseason is selected by the CALENDAR YEAR it was played in, from the
    relation's own Eastern date, never by ESPN's pre-1993-94 label
    (`AGENTS.md`, "Select a postseason by the calendar year"). It does not
    exclude the phantom 1993 label by hand: ``team_games``' own ``played`` CTE
    already keeps one row per ``(season_type, home, away, eastern_date)``, so
    a 1993 row that is really 1994's game has already been collapsed into it
    before this clause ever runs, and asking for ``year(eastern_date) = 1994``
    cannot double it.

    Deliberately never excludes the NBA Cup final - see
    :func:`team_games`.

    .. versionchanged:: 4.4.0
       Bounds the upper end too when ``span.until`` is set (step 3, K1).

    .. versionadded:: 5.0.0
       Public, as the relation's shared step.

    .. versionchanged:: 6.0.0
       Reads :data:`~association.query.player_games.BOTH_SEASON_TYPES`: each
       type's own games in one clause, the postseason from its own floor
       (ISSUES.md #416).
    """
    if span.season_type == BOTH_SEASON_TYPES:
        # Both types in one read ("including the playoffs", ISSUES.md #416):
        # each type's own games, the postseason's by the calendar year -
        # the type is part of the clause, so `team_games` adds none.
        # The postseason reaches back to its own floor (1989, by the
        # calendar year), not the regular season's a both-types span carries.
        post_first = span.first if span.season is not None else max(span.since or 0, COVERAGE["games"].floor(POSTSEASON).season)
        regular, regular_params = span.clause("tg.season")
        post, post_params = team_span_clause(replace(span, season_type=POSTSEASON, first=post_first))
        return f"((tg.season_type = {REGULAR_SEASON} AND {regular}) OR (tg.season_type = {POSTSEASON} AND {post}))", [*regular_params, *post_params]
    if span.season_type == 3:
        if span.season is not None:
            return "year(tg.eastern_date) = ?", [span.season]
        if span.until is not None:
            return "year(tg.eastern_date) BETWEEN ? AND ?", [span.first, span.until]
        return "year(tg.eastern_date) >= ?", [span.first]
    return span.clause("tg.season")


def _team_games_type(span: ResolvedSpan) -> tuple[list[str], list[Any]]:
    """The season-type condition :func:`team_games` puts before the span's
    clause - none for both types, whose clause carries each type's own."""
    return ([], []) if span.season_type == BOTH_SEASON_TYPES else (["tg.season_type = ?"], [span.season_type])


def team_games(con: duckdb.DuckDBPyConnection, team: Entity | None, span: ResolvedSpan, scope: Filtered, *, opponent: Any, date: str | None = None) -> TeamNarrowed | Unanswered:
    """``team``'s games in ``span``, narrowed to an opponent, a venue, one
    Eastern date, one game of each playoff series (``game_n``) and a window of
    the newest or oldest N (``scope.window``) where the question named
    them - the team counterpart of :func:`scoped_games`, over
    :class:`association.query.team_games.TeamNarrowed`.

    Every game the team actually played is included, the NBA Cup final among
    them: that exclusion is :func:`association.query.team_metrics.games_scope`'s,
    for a win-loss RECORD, and a plain game list or count is not one - the
    cup final is a real game the team played, the same reasoning
    ``TEAM_RECORD``'s own cup-final mention already carries.

    ``opponent`` is passed rather than read from ``scope`` because a caller
    that has already resolved the team (it needs the name for its answer
    before the games are read) passes the Entity, exactly as
    :func:`_narrow_player_games` does for a player's opponent; text is
    resolved here, so a clarification about the team comes back as the answer
    either way. ``venue``, ``game_n`` and the window are read from
    ``scope`` because no caller has a reason to resolve any of them first - a
    caller that must NOT honor one (``PLAYER_LOG``'s team half already lists its
    own games with its own LIMIT; ``TEAM_MEETINGS`` slices the window itself from
    its one read rather
    than a window of them) passes a scope without it, the same way both
    already do for every cell but ``venue``.

    .. versionchanged:: 4.4.0
       Honors ``game_n`` (step 3, C4b): one game of each playoff series,
       numbered the way :func:`_narrow_player_games` numbers a player's own.
       Honors the window (step 3, C4b; typed in Phase 3, step 2) - the newest or
       oldest N of the narrowed games, cut after every other filter - via
       :attr:`~association.query.team_games.TeamNarrowed.window`.

    .. versionchanged:: 4.4.0
       Honors ``situation`` (step 3, K1): a weekday, a month, a fixed holiday,
       or "since <month day>" within each game's own season - read the same
       way :func:`scoped_games` reads it for the player relation, over
       :meth:`association.query.team_games.TeamNarrowed.narrow_calendar`. A
       value that names no calendar narrowing (an age, "since returning") is
       refused by value rather than silently dropped, the same as the player
       relation's own refusal.

    .. versionchanged:: 4.4.0
       Honors the other half of ``situation`` (K3-2): an opponent's conference
       or division for that game's own season ("vs the west", "against the
       southeast division"), over
       :meth:`association.query.team_games.TeamNarrowed.narrow_alignment` -
       see :func:`apply_situation`.

    .. versionchanged:: 5.0.0
       Reads the typed :class:`~association.query.reading.Filtered`. A slot dict
       was taken until every caller passed ``reading``, the tests last,
       through their slot door (``tests/slots.py``, deleted in Phase 4,
       step 0).

    .. versionchanged:: 5.0.0
       Honors ``period``/``half``: every read then sees that part of each
       game (:meth:`~association.query.team_games.TeamNarrowed.narrow_periods`).
    """
    clause, params = team_span_clause(span)
    typed, type_params = _team_games_type(span)
    if team is None:
        # Every team's games - the league's teams, each game once per side
        # (a team's best single game asked of any team: "most 3s made in a
        # single game by a team").
        narrowed = TeamNarrowed(base=[*typed, clause], base_params=[*type_params, *params], team=None)
    else:
        narrowed = TeamNarrowed(base=["tg.team_id = ?", *typed, clause], base_params=[team.id, *type_params, *params], team=team)
    if opponent:
        rival = opponent if isinstance(opponent, Entity) else resolved_team(con, opponent, season=span.season)
        if isinstance(rival, Unanswered):
            return rival
        if team is not None and rival.id == team.id:
            # A cause the planner says, naming the team named twice (ISSUES.md
            # #277) - the question's slip, not only the rule.
            raise ShapeDeclined("own_opponent", {"team": rival.name}, f"the {rival.name} are named as both the team and its opponent - name the team they played")
        narrowed.opponent = rival
        narrowed.narrow("tg.opponent_id = ?", rival.id)
    if date:
        narrowed.narrow("tg.eastern_date = ?", date)
        narrowed.date = date
    if scope.cuts.game_n:
        if span.season_type != 3:
            # A series has games 1-7; a regular season has nothing "game 4" names.
            raise Unsupported(f"game {scope.cuts.game_n} names a game of a playoff series, and this is a {span.kind} question")
        narrowed.narrow_series_game(scope.cuts.game_n)
    if scope.cuts.situation:
        # Same discipline as scoped_games: honored where it names the
        # calendar or a conference/division, refused BY VALUE (never
        # silently dropped) otherwise - see apply_situation.
        apply_situation(narrowed, scope.cuts.situation)
    if scope.cuts.venue:
        # Last of the row filters, so the neutral-site games it leaves out
        # are counted over every other one.
        _team_games_narrow_venue(con, narrowed, scope.cuts.venue)
    # The same window rule as the player relation's - a named order, or a
    # bare limit read as the newest N (see relation_window).
    narrowed.window = relation_window(scope)
    _team_games_apply_period(con, narrowed, scope)
    return narrowed


def _team_games_narrow_venue(con: duckdb.DuckDBPyConnection, narrowed: TeamNarrowed, venue: str) -> None:
    """Only the team's games on ``venue``'s side, never one played at a
    neutral site - which counts as neither home nor away, as the record
    and the league's venue ranking count it (ISSUES.md #173: the NBA Cup
    final, where the Knicks were the designated home side, was one of
    their "home" games in a log, a split, a streak and a quarter, and not
    in their home record) - and how many such games the rule left out,
    which the narrowing's phrase says."""
    where, params = narrowed.clauses()
    row = con.execute(f"{TEAM_GAMES_SQL} SELECT COUNT(*) FROM team_games tg WHERE {where} AND tg.side = ? AND tg.neutral", [*params, venue]).fetchone()
    narrowed.neutral_left_out = int(row[0]) if row else 0
    narrowed.venue = venue
    narrowed.narrow("tg.side = ? AND NOT tg.neutral", venue)


def _team_games_apply_period(con: duckdb.DuckDBPyConnection, narrowed: TeamNarrowed, scope: Filtered) -> None:
    """A quarter or half narrows every read of the team relation to that part
    of each game (:meth:`~association.query.team_games.TeamNarrowed.narrow_periods`)
    - the ``period`` cell of :data:`TEAM_RELATION_SCOPING` (the typed
    :class:`~association.query.reading.Period`), the team counterpart of
    :func:`apply_period`. A warehouse without the shots,
    the player rows or the plays - or with a shot or play table that carries
    no ``team_id`` (a fixture, a partial load) - leaves the columns rebuilt
    from them unknown rather than zero, and the linescore's points still
    answer."""
    asked = period_narrowing(scope)
    if asked is not None:
        shots = has_table(con, "player_box_stats") and _team_games_has_team_id(con, "shot_chart")
        narrowed.narrow_periods(*asked, shots=shots, plays=shots and _team_games_has_team_id(con, "plays"))


def _team_games_has_team_id(con: duckdb.DuckDBPyConnection, table: str) -> bool:
    """Whether ``table`` exists and carries the ``team_id`` a team's period line groups by."""
    row = con.execute("SELECT COUNT(*) FROM information_schema.columns WHERE table_name = ? AND column_name = 'team_id'", [table]).fetchone()
    return bool(row and row[0])


def team_span_label(span: ResolvedSpan, first: Any = None, last: Any = None) -> str:
    """The team span in words - :meth:`conditions._Scope.label`'s shape, over
    a :class:`~association.query.player_relation.ResolvedSpan` instead: one season,
    a since-bounded range, or the seasons the rows actually came from. Shared
    by :func:`_player_splits_team`, :func:`_record_when_team_answer` and
    :func:`_streak_team`, the same way :func:`condition_span_label` is shared
    by the player branches - and, like that function, has to check ``since``
    itself rather than merely a career's own ``first``/``last``: a since-bounded
    span is career-SHAPED (``span.season`` is None the same way a career's is),
    so without this a "since 2022" record read exactly like "every season on
    record" once its first and last rows were known.

    .. versionchanged:: 4.4.0
       Reads ``span.since`` (step 3, C4b) - before this, a since-bounded team
       span rendered the same label as a plain career one, with nothing
       saying the question had named a starting year at all.

    .. versionchanged:: 5.0.0
       Says "from X through Y" once ``span.until`` bounds the other end too -
       :meth:`ResolvedSpan.during`'s own wording. Before this, a team's own record
       "from 2019-20 to 2021-22" was labeled "since 2020 (2020-2026 ...)",
       the range's real upper bound nowhere in the sentence, because
       ``span.until`` never reached here at all (ISSUES.md).
    """
    if span.season is not None:
        return season_phrase(span.season, span.season_type)
    if span.since is not None:
        years = span.years(first, last) if isinstance(first, int) and isinstance(last, int) else f"{span.kind}s"
        return f"from {span.since} through {span.until} ({years})" if span.until is not None else f"since {span.since} ({years})"
    if isinstance(first, int) and isinstance(last, int):
        return span.years(first, last)
    return f"every {span.kind} on record ({span.first} onward)"


def team_where_in(span: ResolvedSpan) -> str:
    """:func:`common._where_in`'s shape, over a ``ResolvedSpan``: "in the 2026
    regular season", or "in any regular season on record" for a span with
    nothing in it."""
    return f"in the {team_span_label(span)}" if span.season is not None else f"in any {span.kind} on record ({span.first} onward)"


def condition_team_no_games(con: duckdb.DuckDBPyConnection, team: Entity, span: ResolvedSpan, narrowed: TeamNarrowed) -> Refusal:
    """Nothing to report for a team's own games under a condition read
    (:func:`~association.query.compose.splits.read_team_splits`,
    :func:`~association.query.compose.records.read_team_record_when`,
    :func:`~association.query.compose.runs.read_team_streak`) - which fact
    is missing, the team's games in this span at all or the match to an
    opponent/venue narrowing, the same discipline the team's log applies
    (``compose.logs._team_log_none``): a team with real games in a span but
    none against a named opponent gets that sentence, never the misleading
    "no games in this span"."""
    where, params = narrowed.clauses(narrowed=False)
    season_col = "year(tg.eastern_date)" if span.season_type == 3 else "tg.season"
    found = con.execute(f"{TEAM_GAMES_SQL} SELECT COUNT(*), MIN({season_col}), MAX({season_col}) FROM team_games tg WHERE {where}", params).fetchone()
    total, first, last = found if found else (0, None, None)
    shown = {"team": team.name, "span": team_span_label(span, first, last), "games": 0}
    if not total:
        return Refusal(kind="no_team_games", facts={"team": team.name, "where": team_where_in(span)}, shown=shown, under=())
    facts = {"team": team.name, "games": total, "during": span.during(first, last, whose="all seasons on record"), "narrowing": narrowed.filters()}
    return Refusal(kind="team_none_matched", facts=facts, shown=shown, under=())


def league_team_narrowed(span: ResolvedSpan) -> TeamNarrowed:
    """Every team's games in ``span``, for the league's longest run of wins
    or losses - over the relation, with no single team to narrow to the way
    :func:`common.team_games` always takes one, so the base clause is built
    the same way it builds one, minus the ``team_id`` clause it always adds.
    A postseason before 1993-94 is selected by the calendar year it was
    played in, not ESPN's own-year label.

    .. versionadded:: 5.0.0
    """
    clause, clause_params = team_span_clause(span)
    # Teams the `teams` table does not hold are exhibition opponents that
    # turn up in a few regular-season rows (1992-2000), not franchises - the
    # same guard every team read applies.
    return TeamNarrowed(base=["tg.team_id IN (SELECT team_id FROM teams)", "tg.season_type = ?", clause], base_params=[span.season_type, *clause_params])


# --- the reads the team readers take ----------------------------------------------
# Statements over the team-games relation a team reader needs
# (compose.team_records' two checks, the team compiler's narrowed
# total), run here and handed back as rows - the relation's own executor,
# as condition_team_no_games' is.


def team_season_game_counts(con: duckdb.DuckDBPyConnection, scope: str, scope_params: list[Any], team_id: str) -> tuple[int, int]:
    """How many games the relation holds in a season scope for any team, and
    how many of them are ``team_id``'s
    (:func:`~association.query.team_games.season_game_counts_sql`) - what
    tells "the warehouse holds no games that season" from "the team played
    none"; ``(0, 0)`` where the statement returns no row.

    .. versionadded:: 6.0.0
    """
    sql, params = season_game_counts_sql(scope, scope_params, team_id)
    counts = con.execute(sql, params).fetchall()
    return counts[0] if counts else (0, 0)


def team_game_list_gaps(
    con: duckdb.DuckDBPyConnection, narrowed: TeamNarrowed, team_id: str, season_type: int, *, season: int | None, since: int | None, until: int | None, floor: int
) -> list[tuple[Any, ...]]:
    """``(season, games listed, games played)`` for each season from
    ``floor`` where the narrowed games tallied for a team do not number its
    games in ``team_season_stats``
    (:func:`~association.query.team_games.game_list_gaps_sql`).

    .. versionadded:: 6.0.0
    """
    sql, params = game_list_gaps_sql(narrowed, team_id, season_type, season=season, since=since, until=until, floor=floor)
    return con.execute(sql, params).fetchall()


def team_games_total(con: duckdb.DuckDBPyConnection, narrowed: TeamNarrowed, column: str, *, join: str = "") -> tuple[Any, ...] | None:
    """A sum over the narrowed team games' ``column`` (a game-level
    measure's, or a box-score figure over ``join``'s box row - never a slot)
    with the games, wins and losses it covers and how many games have a
    figure to sum - ``(total, games, wins, losses, counted)``, or ``None``
    with no row.

    .. versionadded:: 6.0.0

    .. versionchanged:: 6.0.0
       ``join`` and ``counted``: a box-score count summed over the narrowed
       games (ISSUES.md #403), and the games it could not be read in.
    """
    selects = [f"SUM({column}) AS total", "COUNT(*) AS games", "SUM(tg.won::INT) AS wins", "SUM((NOT tg.won)::INT) AS losses", f"COUNT({column}) AS counted"]
    sql, params = aggregate_sql(narrowed, selects, join=join)
    return con.execute(sql, params).fetchone()
