"""Games under a condition: a player's splits, with or without a teammate, a record when a stat line is reached, and streaks.

.. versionadded:: 3.0.0
   Split out of the former ``association.query.templates`` module.
"""

from __future__ import annotations

from typing import Any

import duckdb

from association.query.answer import Reply
from association.query.reading import SPLIT_KINDS as SPLIT_KINDS
from association.query.reading import Scope, Unsupported

from ..conditions import (
    _Scope,
)
from ..entities import Entity
from ..team_games import TEAM_GAMES_SQL, TeamNarrowed
from .common import _period, _Span, _team_span_clause, ordinal_word


def condition_span_label(covered: _Scope, scope: Scope, first: Any, last: Any) -> str:
    """The season(s) a condition answer covers, in words: "since 2022
    (2022-2026 regular seasons)" or "in his 18th season (2021 regular
    season)" when the question named it that way - the same phrasing
    ``_Span.during`` gives ``game_log`` and ``player_stat`` (``_Span.since``,
    ``_Span.ordinal``).

    record_when and streak build their heading off the relation's own
    ``_Scope`` (``covered``) rather than the ``_Span`` ``condition_player``
    resolves internally (its docstring: "the one place the two readers of a
    player's games disagreed, and not this refactor's to settle"), so the
    phrase is composed here from the question's own ``since`` and
    ``season_n`` instead. ``first``/``last`` already carry the real
    narrowing - ``covered`` has its own ``season`` forced to None wherever
    ``since`` or ``season_n`` apply (the same "career"-shaped outer scope
    ``scoped_player`` builds internally for ``season_n``), so
    :meth:`_Scope.label` reads the actual seasons the narrowed games came
    from rather than defaulting to "now".

    .. versionchanged:: 5.0.0
       Says "from X through Y" once ``scope.until`` bounds the other end too,
       matching :meth:`_Span.during`'s wording (see :func:`team_span_label`,
       fixed the same way) - before this, "from 2019-20 to 2021-22" was
       labeled "since 2019 (...)", with the range's real upper bound nowhere
       in the sentence (ISSUES.md).
    """
    label = covered.label(first, last)
    if scope.since:
        return f"from {scope.since} through {scope.until} ({label})" if scope.until else f"since {scope.since} ({label})"
    if scope.season_n:
        return f"in his {ordinal_word(scope.season_n)} season ({label})"
    return label


# The relation's cells record_when's team branch and streak's team/league
# branches cannot honor: each needs a named PLAYER to settle a teammate's
# absence, a starter/bench half, or a line on a box-score column against -
# `condition_player` is what reads all of them, and neither branch calls it.
# `HONORED_SCOPING` claims the whole relation for both intents regardless of
# branch (the same declaration the player branch needs), so a team-only or
# league-wide question setting one of these would otherwise be silently
# answered as though it had been applied. Refusing here, by name, is the same
# discipline `_player_splits_team` already applies to a bare `starter_bench`
# split.
#
# `opponent` and `venue` are NOT here (step 3, C4): both branches now read the
# team-games relation through `common.team_games`, the same shared narrowing
# `team_record` reads, so a team's own opponent/venue narrowing is a real,
# implemented shape rather than a refusal. `since` is NOT here either (step 3,
# C4b): `_span_of`'s own `since` branch already exists and both team branches
# call it for the ordinary span, so honoring it needed no new mechanism (see
# ISSUES.md, "record_when's team branch and streak's team/league branches
# still refuse ..." - rewritten to match). `streak`'s LEAGUE branch (no team
# named either) still cannot narrow to a single opponent or venue - a
# league-wide streak has no one team's home/road split or rival to read - so
# the planner refuses those two there specifically (`compose.plan._streak_league_cells`).
#
# `game_n` is honored for `record_when`'s team branch (a threshold record can
# meaningfully be narrowed to one game of each series - "Celtics record when
# they scored 120+, game 4 of the series") but stays refused for `streak`'s
# team and league branches, passed as `extra` at each of those two call sites:
# a streak is a run of CONSECUTIVE games, and the games "game 4 of each
# series" picks out are not consecutive to each other - a streak over them
# would silently answer a run over a scattered, non-adjacent subset rather
# than the real games in between.
#
# `conditions` is here (added 5.0.0, ISSUES.md) for the same reason as
# `without`/`split`/`below`/`above`: a companion's role - he started, came off
# the bench, or reached a line - is a fact about a named PLAYER's game, and a
# team or league branch has no such player settled to check it against.
# Silently dropping it answered a team's or the league's whole span as though
# "76ers record when they score 120 when embiid starts" had named no
# condition at all.
_CONDITION_PLAYER_ONLY_CELLS: tuple[str, ...] = ("without", "split", "season_n", "below", "above", "conditions")


def condition_needs_player_refusal(intent: str, scope: Scope, *extra: str) -> None:
    """Raise if a team-only or league-wide question set a relation cell that
    needs a named player to honor - see :data:`_CONDITION_PLAYER_ONLY_CELLS`.
    ``extra`` adds cells refused for this call site only (see ``streak``'s own
    call, which passes ``"game_n"``).

    .. versionchanged:: 4.4.0
       Takes ``*extra`` (step 3, C4b), so the two intents' team branches no
       longer have to agree on exactly the same refused set.
    """
    claimed = sorted(cell for cell in (*_CONDITION_PLAYER_ONLY_CELLS, *extra) if getattr(scope, cell))
    if claimed:
        raise Unsupported(f"{intent} cannot honor {claimed} without a named player - only his own games can be narrowed that way")


def team_span_label(span: _Span, first: Any = None, last: Any = None) -> str:
    """The team span in words - :meth:`conditions._Scope.label`'s shape, over
    a :class:`~association.query.templates.common._Span` instead: one season,
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
       :meth:`_Span.during`'s own wording. Before this, a team's own record
       "from 2019-20 to 2021-22" was labeled "since 2020 (2020-2026 ...)",
       the range's real upper bound nowhere in the sentence, because
       ``span.until`` never reached here at all (ISSUES.md).
    """
    if span.season is not None:
        return _period(span.season, span.season_type)
    if span.since is not None:
        years = span.years(first, last) if isinstance(first, int) and isinstance(last, int) else f"{span.kind}s"
        return f"from {span.since} through {span.until} ({years})" if span.until is not None else f"since {span.since} ({years})"
    if isinstance(first, int) and isinstance(last, int):
        return span.years(first, last)
    return f"every {span.kind} on record ({span.first} onward)"


def team_where_in(span: _Span) -> str:
    """:func:`common._where_in`'s shape, over a ``_Span``: "in the 2026
    regular season", or "in any regular season on record" for a span with
    nothing in it."""
    return f"in the {team_span_label(span)}" if span.season is not None else f"in any {span.kind} on record ({span.first} onward)"


def condition_team_no_games(con: duckdb.DuckDBPyConnection, team: Entity, span: _Span, narrowed: TeamNarrowed) -> Reply:
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
    label = team_span_label(span, first, last)
    if not total:
        message = f"The warehouse has no games with a result for the {team.name} {team_where_in(span)}."
        return Reply(data={"team": team.name, "span": label, "games": 0}, answer=message)
    message = f"The {team.name} played {total:,} games {span.during(first, last, whose='all seasons on record')}, none of them{narrowed.filters()}."
    return Reply(data={"team": team.name, "span": label, "games": 0}, answer=message)


def _streak_league_team_narrowed(span: _Span) -> TeamNarrowed:
    """Every team's games in ``span``, for the league's longest run of wins
    or losses - over the relation, with no single team to narrow to the way
    :func:`common.team_games` always takes one, so the base clause is built
    the same way it builds one, minus the ``team_id`` clause it always adds.
    A postseason before 1993-94 is selected by the calendar year it was
    played in, not ESPN's own-year label (step 3, C4).

    .. versionadded:: 5.0.0
    """
    clause, clause_params = _team_span_clause(span)
    # Teams the `teams` table does not hold are exhibition opponents that
    # turn up in a few regular-season rows (1992-2000), not franchises - the
    # same guard `_team_games` used to apply inline.
    return TeamNarrowed(base=["tg.team_id IN (SELECT team_id FROM teams)", "tg.season_type = ?", clause], base_params=[span.season_type, *clause_params])
