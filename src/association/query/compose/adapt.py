"""Router intent + slots -> :class:`~association.query.compose.core.Query`:
each intent is the default point of the algebra, as code, for the six
templates on the player-games relation where they read box scores.

.. versionadded:: 4.4.0
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from association.query.measures import MEASURE_WORDS
from association.query.reading import Group, Reading, Scope
from association.query.templates.common import _BOX_SCORES, MAX_LIMIT

# One concept, one definition (scripts/check_duplicate_names.py): the default
# row counts and the default stat line are the same constants the real
# templates already carry (association.query.templates.games/players),
# reused rather than redeclared under the same name.
from association.query.templates.games import DEFAULT_GAME_LOG_LIMIT
from association.query.templates.players import DEFAULT_SINGLE_GAME_LIMIT, STAT_LINE

from .core import COLUMNS, LINE, Query, Unsupported, _iso_date
from .plan import plan

#: The line a splits read carries, beyond the four :data:`~association.query.compose.core.LINE` measures.
SPLIT_LINE: tuple[str, ...] = ("minutes", "points", "rebounds", "assists", "steals", "blocks", "turnovers", "threePointFieldGoalsMade", "fg_pct")
"""The measures a ``player_splits`` read carries.

.. versionadded:: 4.4.0
"""


def _clamp(limit: int | None, default: int) -> int:
    """A router ``limit`` clamped to :data:`~association.query.templates.common.MAX_LIMIT`, or ``default`` for anything else."""
    return min(limit, MAX_LIMIT) if limit is not None and limit >= 1 else default


def _stat_column(stat: str | None) -> str | None:
    """A router ``stat`` as a relation column - the router's own column names
    (``points``, ``threePointFieldGoalsMade``) or a word :data:`~association.query.measures.MEASURE_WORDS` knows."""
    if stat is None or not stat.strip():
        return None
    if stat in COLUMNS:
        return stat
    return MEASURE_WORDS.get(stat.strip().lower())


def _named_player(slots: dict[str, Any]) -> bool:
    """Whether ``slots`` - the router's slot dict, before its scope is read -
    names a player at all."""
    return isinstance(slots.get("player"), str) and bool(slots["player"].strip())


def _named_player_in(scope: Scope) -> bool:
    """Whether ``scope`` names a player at all: :func:`_named_player`, once
    the slots are the typed scope."""
    return scope.player is not None and bool(scope.player.strip())


def _adapt_game_log(scope: Scope) -> Reading:
    """``game_log``'s default point: the newest games, in date order."""
    if not _named_player_in(scope):
        raise Unsupported("a team's log is the team relation's")
    # ``season_type_unstated`` ("his last 5 games", no season type named) is
    # read over both types at once - ``scoped_player`` settles the span with
    # ``_player_relation_season_type`` - and ``compose.present`` says it the
    # way ``game_log`` does, one type at a time merged by date.
    # A team beside the player is settled in compile_query through game_log's own _team_slot_for_player.
    date = _iso_date(scope)
    # game_log settles the name in a career span when a date is given (the
    # date is the scope), and in the named or defaulted season otherwise.
    return Reading(
        scope=scope,
        shape="rows",
        measures=list(LINE),
        aggregate="none",
        group="none",
        predicates=[],
        order="date",
        direction="asc" if scope.order == "first" else "desc",
        limit=_clamp(scope.limit, DEFAULT_GAME_LOG_LIMIT),
        span="career" if date else scope.span,
        season=None if date else scope.season,
    )


def _adapt_player_stat(scope: Scope) -> Reading:
    """``player_stat``'s default point: a per-game average over box scores
    where a narrowing (or a date) sends the read there, and the season line
    (``source="seasons"``) for an unnarrowed season or career - the same
    split ``templates.players.player_stat`` makes."""
    if not _named_player_in(scope):
        raise Unsupported("player_stat needs a player")
    from association.query.templates.common import measure_filters
    from association.query.templates.players import _player_stat_reads_box_scores

    col = _stat_column(scope.stat)
    measures = [col] if col else list(STAT_LINE)
    if scope.limit or scope.order:
        raise Unsupported("player_stat hands a limit or an order to game_log - a log, not an average")
    # The template's own test reads the slot dict until it takes the Scope.
    if not (_player_stat_reads_box_scores(scope.to_slots(), measure_filters(scope.below, scope.above)) or scope.date):
        return Reading(scope=scope, shape="scalar", measures=measures, aggregate="per_game", group="none", predicates=[], source="seasons")
    date = _iso_date(scope)
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
    """``threshold_count``'s default point: a count of games clearing one line."""
    col = _stat_column(scope.stat)
    threshold = scope.threshold
    if not _named_player_in(scope):
        raise Unsupported("a league-wide count is not on the one-player relation")
    if col is None or threshold is None or threshold < 1:
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
        limit=_clamp(scope.limit, DEFAULT_SINGLE_GAME_LIMIT),
    )


def _adapt_player_splits(scope: Scope) -> Reading:
    """``player_splits``'s default point: a record by venue, or by starter/bench."""
    if not _named_player_in(scope):
        raise Unsupported("a team's splits are the team relation's")
    group: Group = "starter" if scope.split == "starter_bench" else "venue"
    return Reading(scope=scope, shape="grouped", measures=list(SPLIT_LINE), aggregate="record", group=group, predicates=[], available=_BOX_SCORES)


def _adapt_record_when(scope: Scope) -> Reading:
    """``record_when``'s default point: the record in games clearing one line."""
    col = _stat_column(scope.stat)
    threshold = scope.threshold
    if not _named_player_in(scope) or col is None or threshold is None:
        raise Unsupported("record_when needs a player, a stat and a threshold here")
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
}


def to_reading(intent: str, slots: dict[str, Any]) -> Reading:
    """The intent's default point of the algebra: the query a bare router
    intent means before any of the question's own words move it (see
    :func:`association.query.compose.move.move_point`). ``slots`` comes in
    by :meth:`~association.query.reading.Scope.from_slots`, the one door a
    slot dict has, and the adapters read the typed scope.

    .. versionadded:: 4.4.0

    .. versionchanged:: 4.5.0
       An unnarrowed ``player_stat`` is a point on the season line
       (``source="seasons"``) rather than :class:`~association.query.compose.core.Unsupported`.

    .. versionchanged:: 4.5.0
       ``game_log`` with ``season_type_unstated`` ("his last 5 games") is a
       point - both season types, which the relation reads at once - rather
       than :class:`~association.query.compose.core.Unsupported`.
    """
    return _to_reading_scope(intent, Scope.from_slots(slots))


def _to_reading_scope(intent: str, scope: Scope) -> Reading:
    """:func:`to_reading` over a scope already read - for the compiler's own
    moves (:func:`association.query.compose.move.read_point` reads the scope
    once) and the presenters, which have the Query's scope and no slot dict."""
    adapter = _ADAPTERS.get(intent)
    if adapter is None:
        raise Unsupported(f"no adapter for {intent}")
    return adapter(scope)


def to_query(intent: str, slots: dict[str, Any]) -> Query:
    """:func:`to_reading`, planned: the default point as the compiler's own
    :class:`~association.query.compose.core.Query`.

    .. versionchanged:: 4.5.0
       Plans :func:`to_reading`; the adapters build
       :class:`~association.query.reading.Reading` records.
    """
    query = plan(to_reading(intent, slots))
    assert isinstance(query, Query)
    return query
