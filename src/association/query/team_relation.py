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
   Moved from ``association.query.templates.common`` and ``templates.splits`` (Phase 2, step 6).
"""

from __future__ import annotations

from typing import Any

import duckdb

from association.query.entities import Entity, resolved_team
from association.query.player_relation import ResolvedSpan, apply_situation, has_table, relation_window, span_of
from association.query.reading import Cuts, Period, Scope, Span, Unsupported, period_narrowing
from association.query.result import Refusal, Unanswered
from association.query.season_text import season_phrase
from association.query.team_games import TEAM_GAMES_SQL, TeamNarrowed

# What the team-games relation narrows by, declared ONCE - the team
# counterpart of RELATION_SCOPING. Every template that settles its team and
# span through `scoped_team` and its games through `team_games` honors these:
# an opponent, a venue, one Eastern date, a career that starts partway through
# (`since`), one game of each playoff series (`game_n`), and a window of the
# newest or oldest N of the narrowed games (the typed `reading.Window`'s
# `window` cell, an end with its count, since Phase 3, step 2). `span`
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
# cell the relation itself carries. A template that wants one leans on
# `TeamNarrowed.narrow` directly, the way `_record_when_team_base` already
# joins `team_box_stats` for its own stat threshold.
#
# `situation` and `until` are step 3, K1's two additions, bringing the team
# relation to parity with the player one's own RELATION_SCOPING:
# `situation` is a calendar narrowing - a weekday, a month, a fixed holiday,
# or "since <month day>" within each game's own season - applied by
# `team_games` itself via `TeamNarrowed.narrow_calendar`, the exact mirror of
# `scoped_games`' own reading for the player relation, OR (K3-2) an
# opponent's conference or division for that game's own season, via
# `TeamNarrowed.narrow_alignment` - both read by the one `apply_situation`
# helper both relations' shared steps call; `until` is the
# inclusive LAST season of a `since`-bounded range (a decade, "2019-20 to
# 2023-24"), read the same way `since` already is (`span_of`/`scoped_team`) -
# never alone (`validated_until`), so a template honors it only by also
# honoring `since`.
#
# `period` is the period relation's team half (ROADMAP plan item 4; the
# typed `Period` since Phase 3, step 2, one cell for a quarter or a half): it
# narrows what a read SEES of each game - the linescore's points, and every
# other column rebuilt from the plays (`team_games.team_period_line_sql`) -
# applied by `team_games` through `TeamNarrowed.narrow_periods`, the
# counterpart of the player relation's.
#: The games' cuts a team's games carry (:attr:`~association.query.reading.Cuts.CELLS`
#: less three): no game is labeled by its ``round``, and a ``tenure`` and
#: an ordinal ``season_n`` are one player's.
TEAM_CUTS: frozenset[str] = Cuts.CELLS - {"round", "tenure", "season_n"}
TEAM_RELATION_SCOPING = frozenset({"window", *Period.CELLS, *TEAM_CUTS, *Span.CELLS})
"""The cells every reader on the team-games relation honors: the games'
cuts a team's games carry (:data:`TEAM_CUTS`: ``opponent``, ``venue``,
``date``, ``situation``, ``game_n``, each applied by :func:`team_games`
and said by :meth:`~association.query.team_games.TeamNarrowed.filters`),
the window, a quarter or half, and the span's three cells
(:attr:`~association.query.reading.Span.CELLS`, Phase 3, step 2) -
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


# The cells a template on the team relation does NOT honor, each with why. A
# reason has to be about the template's answer, not its code - the same rule
# RELATION_SCOPING_EXCLUDED follows.
TEAM_RELATION_SCOPING_EXCLUDED: dict[str, dict[str, str]] = {
    # A leaderboard ranks one season's (or one since-bounded span's) teams
    # against each other; none of these four narrow that pool to a single
    # game or a single opponent, and a career total across every season on
    # record is not built.
    "team_leaderboard": {
        "opponent": "a leaderboard ranks every team; it has no reading for one named opponent",
        "date": "a leaderboard ranks a season, not one day's games",
        "career": "a leaderboard ranks one season's teams; a career total across every season is not built",
        "both": "a leaderboard ranks one season type's lines; the standings hold no row for both at once",
        "window": "a leaderboard ranks a season, not a window of games",
        "game_n": "a leaderboard ranks a season, not one game of a series",
        # step 3, K1: a leaderboard ranks a season or a since/until-bounded
        # span of them; narrowing that pool to one weekday, month or holiday
        # within it is a different question from ranking the span itself.
        "situation": "a leaderboard ranks a season, not the games in one weekday, month or holiday within it",
        "period": "a leaderboard ranks teams' season lines, and no season line is split by quarter or half",
    },
    # A record for one game is a single result, which game_log already answers
    # directly, and a record over a limited number of recent games is the same
    # substitution the player relation refuses for the same two cells. `since`
    # and `game_n` used to be excluded here too ("not built yet" - a reason
    # about the code, which the rule above this dict forbids) - step 3, team
    # cells, reads both: `since` the same since-bounded `ResolvedSpan` a career
    # already reads (`_record_narrowed`), `game_n` the relation's own
    # `narrow_series_game` check (`_games_record_games`).
    "team_record": {
        "date": "a record for one calendar date is a single game, which game_log already answers directly",
        "window": "a record over a limited set of games is a game_log question",
        "period": "a record is won and lost over whole games; a quarter or half has no winner the record could count",
    },
    # head_to_head tallies every meeting in the span; `order` and `game_n`
    # pick out a subset of that tally, and neither is built. `since` and
    # `span` used to be excluded too ("not built yet") - step 3, team cells,
    # reads both the same since-bounded or whole-career `ResolvedSpan` team_record's
    # own `since`/`span` now read, over every meeting in it rather than one
    # season.
    "head_to_head": {
        "window": "head_to_head counts every meeting in the span; picking the last N of them is not built",
        "game_n": "head_to_head counts every meeting; one numbered game of a series is not read here",
        # step 3, K1 wires the calendar narrowing into team_quarter_points and
        # team_record; head_to_head's own since/career span result
        # (_head_to_head_span_result) does not read it yet - a weekday or
        # holiday cut of an all-time series is a real question, just not this
        # step's.
        "situation": "head_to_head tallies every meeting in the span; narrowing that tally to one weekday, month or holiday within it is not built",
        "period": "a series is won and lost in whole games; a quarter or half of each meeting has no winner to tally",
        "both": "a series is tallied in one season type; both at once would count regular-season and playoff meetings as one series",
    },
    "team_quarter_points": {"both": "a quarter's figures are reconciled per season and per type; one read over both types would carry one caveat for two"},
    # The team-season readers (compose.team_stats, over the standings and the
    # power index) and the with/without split settle no span of their own:
    # one season's line, one season's projection, or the teammates' games
    # over a career; what their words do not state is declared here with
    # the rest.
    "team_stat": {
        "career": "a team's line is one season's row of the standings; there is no career row to read",
        "range": "a team's line is one season's row of the standings; a range of seasons is not summed",
        "both": "a team's line is one season type's row of the standings",
    },
    "team_outlook": {
        "career": "a projection is one season's snapshot",
        "range": "a projection is one season's snapshot",
        "both": "a projection is one season type's snapshot",
    },
    "with_without": {
        "range": "the split's words state a career or one season; a range of seasons would cut the teammates' stints without saying so",
        "both": "the split reads one season type's games; both at once would join a teammate's regular-season and playoff absences as one",
    },
}
# The games' cuts (Phase 3, step 2's third slice), per reader whose words
# state fewer than the relation's five: the team-season readers read one
# row of the standings or one snapshot and have no game to cut; the
# with/without split states one opponent (both rows narrow together, #163)
# and nothing else that would narrow the teammates' games without its
# sentence saying so.
for _cut in TEAM_CUTS:
    TEAM_RELATION_SCOPING_EXCLUDED["team_stat"][_cut] = "a team's line is one season's row of the standings, with no game to cut"
    TEAM_RELATION_SCOPING_EXCLUDED["team_outlook"][_cut] = "a projection is one season's snapshot, with no game to cut"
for _cut in TEAM_CUTS - {"opponent"}:
    TEAM_RELATION_SCOPING_EXCLUDED["with_without"][_cut] = "the split's words state one opponent; another cut would narrow the teammates' games without the sentence saying so"
"""Per reader, the team relation's cells it refuses or steps aside for, and why.

.. versionadded:: 4.4.0

.. versionchanged:: 6.0.0
   The span's cells (``career``, ``range``, ``both``) per reader, the
   team-season readers' and the with/without split's included (Phase 3,
   step 2); the games' cuts per reader whose words state fewer than the
   relation's.
"""


def team_relation_span(intent: str) -> frozenset[str]:
    """The span's cells ``intent`` states on the team relation
    (:attr:`~association.query.reading.Span.CELLS` less
    :data:`TEAM_RELATION_SCOPING_EXCLUDED`'s), for a reader whose other
    cells are its own list (the with/without split, a team's own line).

    .. versionadded:: 6.0.0
    """
    return Span.CELLS - set(TEAM_RELATION_SCOPING_EXCLUDED.get(intent, {}))


def team_relation_cuts(intent: str) -> frozenset[str]:
    """The games' cuts ``intent`` states on the team relation
    (:data:`TEAM_CUTS` less :data:`TEAM_RELATION_SCOPING_EXCLUDED`'s), for a
    reader whose other cells are its own list.

    .. versionadded:: 6.0.0
    """
    return TEAM_CUTS - set(TEAM_RELATION_SCOPING_EXCLUDED.get(intent, {}))


def team_relation_scoping(intent: str, *extra: str) -> frozenset[str]:
    """The team relation's cells ``intent`` honors: the whole set and
    ``extra``, less the cells it excludes (:data:`TEAM_RELATION_SCOPING_EXCLUDED`).

    .. versionadded:: 5.0.0
       Public, for the team-season readers' declaration
       (``compose.present.STATED_SCOPING``); ``team_relation_scoping`` until then.
    """
    return frozenset((TEAM_RELATION_SCOPING | set(extra)) - set(TEAM_RELATION_SCOPING_EXCLUDED.get(intent, {})))


def scoped_team(con: duckdb.DuckDBPyConnection, scope: Scope, missing: str, *, span: Span | None = None) -> tuple[Entity, ResolvedSpan] | Unanswered:
    """The team a question is about and the seasons it covers - the team
    counterpart of :func:`scoped_player`. A franchise's name is a fact about a
    season (see :func:`resolved_team`: "Hornets" is New Orleans in 2008 and
    Charlotte in 2026), so the span is settled first and the team's name read
    against the season it settles on - the same order ``scoped_player`` keeps,
    even though a team's name (unlike an ambiguous player's) never needs the
    span to disambiguate it.

    Takes: ``con``; ``scope`` (read here for ``team`` and its typed span);
    ``missing`` (the :class:`Unsupported` message when no team was
    named); ``span`` (a :class:`~association.query.reading.Span` overriding
    the scope's own, the same as ``scoped_player``'s, so a caller with a
    reason to override it can).

    Returns ``(team, span)``, or the :class:`~association.query.result.Clarify` asking which team was
    meant. Honors ``since`` the same way :func:`scoped_player` does for a
    player - a career that starts partway through, rather than at the table's
    own floor (step 3, C4b) - and ``until`` beside it (step 3, K1): the
    inclusive last season of a range, read by :func:`span_of`.

    .. versionadded:: 4.4.0

    .. versionchanged:: 5.0.0
       Reads the typed :class:`~association.query.reading.Scope`. A slot dict
       is still taken, through :meth:`~association.query.reading.Scope.from_slots`,
       until every caller passes ``reading.scope``.
    """
    seasons = span_of(span if span is not None else scope.span, "games")
    if not scope.team or not scope.team.strip():
        raise Unsupported(missing)
    team = resolved_team(con, scope.team, season=seasons.season)
    if isinstance(team, Unanswered):
        return team
    return team, seasons


def team_span_clause(span: ResolvedSpan) -> tuple[str, list[Any]]:
    """``tg.season``/``tg.eastern_date`` clause for a team's games in
    ``span``, over :data:`association.query.team_games.TEAM_GAMES_SQL`'s
    ``team_games``.

    A postseason is selected by the CALENDAR YEAR it was played in, from the
    relation's own Eastern date, never by ESPN's pre-1993-94 label
    (`AGENTS.md`, "Select a postseason by the calendar year"). Unlike
    ``association.query.templates.games._season_games``, this does not also
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
    """
    if span.season_type == 3:
        if span.season is not None:
            return "year(tg.eastern_date) = ?", [span.season]
        if span.until is not None:
            return "year(tg.eastern_date) BETWEEN ? AND ?", [span.first, span.until]
        return "year(tg.eastern_date) >= ?", [span.first]
    return span.clause("tg.season")


def team_games(con: duckdb.DuckDBPyConnection, team: Entity, span: ResolvedSpan, scope: Scope, *, opponent: Any, date: str | None = None) -> TeamNarrowed | Unanswered:
    """``team``'s games in ``span``, narrowed to an opponent, a venue, one
    Eastern date, one game of each playoff series (``game_n``) and a window of
    the newest or oldest N (``scope.window``) where the question named
    them - the team counterpart of :func:`scoped_games`, over
    :class:`association.query.team_games.TeamNarrowed`.

    Every game the team actually played is included, the NBA Cup final among
    them: that exclusion is :func:`association.query.team_metrics.games_scope`'s,
    for a win-loss RECORD, and a plain game list or count is not one - the
    cup final is a real game the team played, the same reasoning
    ``team_record``'s own cup-final mention already carries.

    ``opponent`` is passed rather than read from ``scope`` because a caller
    that has already resolved the team (it needs the name for its answer
    before the games are read) passes the Entity, exactly as
    :func:`_narrow_player_games` does for a player's opponent; text is
    resolved here, so a clarification about the team comes back as the answer
    either way. ``venue``, ``game_n`` and the window are read from
    ``scope`` because no caller has a reason to resolve any of them first - a
    caller that must NOT honor one (``game_log``'s team half already lists its
    own games with its own LIMIT; ``head_to_head`` counts every meeting rather
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
       Reads the typed :class:`~association.query.reading.Scope`. A slot dict
       is still taken, through :meth:`~association.query.reading.Scope.from_slots`,
       until every caller passes ``reading.scope``.

    .. versionchanged:: 5.0.0
       Honors ``period``/``half``: every read then sees that part of each
       game (:meth:`~association.query.team_games.TeamNarrowed.narrow_periods`).
    """
    clause, params = team_span_clause(span)
    narrowed = TeamNarrowed(base=["tg.team_id = ?", "tg.season_type = ?", clause], base_params=[team.id, span.season_type, *params], team=team)
    if opponent:
        rival = opponent if isinstance(opponent, Entity) else resolved_team(con, opponent, season=span.season)
        if isinstance(rival, Unanswered):
            return rival
        if rival.id == team.id:
            raise Unsupported("a team cannot be its own opponent")
        narrowed.opponent = rival
        narrowed.narrow("tg.opponent_id = ?", rival.id)
    if scope.cuts.venue:
        narrowed.venue = scope.cuts.venue
        narrowed.narrow("tg.side = ?", scope.cuts.venue)
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
    # The same window rule as the player relation's - a named order, or a
    # bare limit read as the newest N (see relation_window).
    narrowed.window = relation_window(scope)
    _team_games_apply_period(con, narrowed, scope)
    return narrowed


def _team_games_apply_period(con: duckdb.DuckDBPyConnection, narrowed: TeamNarrowed, scope: Scope) -> None:
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
    """Nothing to report for a team's own games under a condition template
    (:func:`_player_splits_team`, :func:`_record_when_team_answer`,
    :func:`_streak_team`) - which fact is missing, the team's games in this
    span at all or the match to an opponent/venue narrowing, the same
    discipline :func:`~association.query.templates.games._team_game_log_none`
    and :func:`common._no_games` already apply. Before ``opponent``/``venue``
    reached these three branches (step 3, C4) there was only one tier to get
    wrong; now a team with real games in a span but none against a named
    opponent gets that sentence instead of the misleading "no games in this
    span" it would have read as before opponent/venue could narrow anything
    here at all."""
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
    played in, not ESPN's own-year label (step 3, C4).

    .. versionadded:: 5.0.0
    """
    clause, clause_params = team_span_clause(span)
    # Teams the `teams` table does not hold are exhibition opponents that
    # turn up in a few regular-season rows (1992-2000), not franchises - the
    # same guard `_team_games` used to apply inline.
    return TeamNarrowed(base=["tg.team_id IN (SELECT team_id FROM teams)", "tg.season_type = ?", clause], base_params=[span.season_type, *clause_params])
