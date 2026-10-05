"""Router intent + slots -> :class:`~association.query.compose.core.Query`:
each intent is the default point of the algebra, as code, for the six
templates on the player-games relation where they read box scores.

.. versionadded:: 4.4.0
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Any

from association.query.reading import Reading, Scope, _clamp_limit
from association.query.reading import named_player_in as _named_player_in
from association.query.templates.common import BOX_SCORES, TemplateUnsupported

# One concept, one definition (scripts/check_duplicate_names.py): the default
# row counts are the same constants the real templates already carry
# (association.query.templates.splits), reused rather than redeclared under
# the same name.
from association.query.templates.splits import _DEFAULT_STREAK_LIMIT, _streak_words, _with_without_named

from .core import DEFAULT_NAMED_RUNS, Unsupported, run_scope


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
            available=BOX_SCORES,
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
        available=BOX_SCORES,
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


#: Intent -> its default-point adapter, over the typed scope. Kept as a
#: mapping rather than an if/elif chain so a new intent is one entry, not a
#: longer function.
_ADAPTERS: dict[str, Callable[[Scope], Reading]] = {
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
    from association.query.point import default_point

    return default_point(intent, Scope.from_slots(slots))


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
