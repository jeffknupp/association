"""Router intent + slots -> :class:`~association.query.compose.core.Query`:
each intent is the default point of the algebra, as code, for the six
templates on the player-games relation where they read box scores.

.. versionadded:: 4.4.0
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from association.query.measures import MEASURE_WORDS
from association.query.templates.common import _BOX_SCORES, MAX_LIMIT

# One concept, one definition (scripts/check_duplicate_names.py): the default
# row counts and the default stat line are the same constants the real
# templates already carry (association.query.templates.games/players),
# reused rather than redeclared under the same name.
from association.query.templates.games import DEFAULT_GAME_LOG_LIMIT
from association.query.templates.players import DEFAULT_SINGLE_GAME_LIMIT, STAT_LINE

from .core import COLUMNS, LINE, Query, Unsupported

#: The line a splits read carries, beyond the four :data:`~association.query.compose.core.LINE` measures.
SPLIT_LINE: tuple[str, ...] = ("minutes", "points", "rebounds", "assists", "steals", "blocks", "turnovers", "threePointFieldGoalsMade", "fg_pct")
"""The measures a ``player_splits`` read carries.

.. versionadded:: 4.4.0
"""


def _clamp(limit: Any, default: int) -> int:
    """A router ``limit`` clamped to :data:`~association.query.templates.common.MAX_LIMIT`, or ``default`` for anything else."""
    return min(limit, MAX_LIMIT) if isinstance(limit, int) and not isinstance(limit, bool) and limit >= 1 else default


def _stat_column(stat: Any) -> str | None:
    """A router ``stat`` as a relation column - the router's own column names
    (``points``, ``threePointFieldGoalsMade``) or a word :data:`~association.query.measures.MEASURE_WORDS` knows."""
    if not isinstance(stat, str) or not stat.strip():
        return None
    if stat in COLUMNS:
        return stat
    return MEASURE_WORDS.get(stat.strip().lower())


def _named_player(slots: dict[str, Any]) -> bool:
    """Whether ``slots`` names a player at all."""
    return isinstance(slots.get("player"), str) and bool(slots["player"].strip())


def _adapt_game_log(slots: dict[str, Any]) -> Query:
    """``game_log``'s default point: the newest games, in date order."""
    if not _named_player(slots):
        raise Unsupported("a team's log is the team relation's")
    # ``season_type_unstated`` ("his last 5 games", no season type named) is
    # read over both types at once - ``scoped_player`` settles the span with
    # ``_player_relation_season_type`` - and ``compose.present`` says it the
    # way ``game_log`` does, one type at a time merged by date.
    # A team beside the player is settled in compile_query through game_log's own _team_slot_for_player.
    date = slots.get("date") if isinstance(slots.get("date"), str) and len(slots["date"]) == 10 else None
    # game_log settles the name in a career span when a date is given (the
    # date is the scope), and in the named or defaulted season otherwise.
    return Query(
        slots,
        "rows",
        list(LINE),
        "none",
        "none",
        [],
        "date",
        "asc" if slots.get("order") == "first" else "desc",
        _clamp(slots.get("limit"), DEFAULT_GAME_LOG_LIMIT),
        span="career" if date else slots.get("span"),
        season=None if date else slots.get("season"),
    )


def _adapt_player_stat(slots: dict[str, Any]) -> Query:
    """``player_stat``'s default point: a per-game average over box scores -
    only where a narrowing (or a date) sends the read there; an unnarrowed
    season or career reads the season line, another relation entirely."""
    if not _named_player(slots):
        raise Unsupported("player_stat needs a player")
    from association.query.templates.common import measure_filters
    from association.query.templates.players import _player_stat_reads_box_scores

    col = _stat_column(slots.get("stat"))
    measures = [col] if col else list(STAT_LINE)
    if not (_player_stat_reads_box_scores(slots, measure_filters(slots.get("below"), slots.get("above"))) or slots.get("date")):
        raise Unsupported("an unnarrowed player_stat reads the season line - another relation")
    if slots.get("limit") or slots.get("order"):
        raise Unsupported("player_stat hands a limit or an order to game_log - a log, not an average")
    date = slots.get("date") if isinstance(slots.get("date"), str) and len(slots["date"]) == 10 else None
    return Query(slots, "scalar", measures, "per_game", "none", [], span="career" if date else slots.get("span"), season=None if date else slots.get("season"))


def _adapt_threshold_count(slots: dict[str, Any]) -> Query:
    """``threshold_count``'s default point: a count of games clearing one line."""
    col = _stat_column(slots.get("stat"))
    threshold = slots.get("threshold")
    if not _named_player(slots):
        raise Unsupported("a league-wide count is not on the one-player relation")
    if col is None or not isinstance(threshold, int) or isinstance(threshold, bool) or threshold < 1:
        raise Unsupported("threshold_count refuses; nothing to compare")
    # A below/above phrase carrying the threshold's own number IS the count,
    # misread as a threshold (threshold_count's own _threshold_count_lines).
    lines = [str(x) for k in ("below", "above") for x in (slots.get(k) or [])]
    predicates = [] if any(str(threshold) in line for line in lines) else [(col, ">=", threshold)]
    return Query(slots, "scalar", [], "count", "none", predicates, available=_BOX_SCORES)


def _adapt_single_game_high(slots: dict[str, Any]) -> Query:
    """``single_game_high``'s default point: the top games by one stat."""
    col = _stat_column(slots.get("stat"))
    if not _named_player(slots) or col is None:
        raise Unsupported("single_game_high needs a player and a known stat here")
    return Query(slots, "rows", [col], "none", "none", [], "measure", "desc", _clamp(slots.get("limit"), DEFAULT_SINGLE_GAME_LIMIT))


def _adapt_player_splits(slots: dict[str, Any]) -> Query:
    """``player_splits``'s default point: a record by venue, or by starter/bench."""
    if not _named_player(slots):
        raise Unsupported("a team's splits are the team relation's")
    group = "starter" if slots.get("split") == "starter_bench" else "venue"
    return Query(slots, "grouped", list(SPLIT_LINE), "record", group, [], available=_BOX_SCORES)


def _adapt_record_when(slots: dict[str, Any]) -> Query:
    """``record_when``'s default point: the record in games clearing one line."""
    col = _stat_column(slots.get("stat"))
    threshold = slots.get("threshold")
    if not _named_player(slots) or col is None or not isinstance(threshold, int) or isinstance(threshold, bool):
        raise Unsupported("record_when needs a player, a stat and a threshold here")
    return Query(slots, "scalar", [], "record", "none", [(col, ">=", threshold)], available=_BOX_SCORES)


#: Intent -> its default-point adapter. Kept as a mapping rather than an
#: if/elif chain so a new intent is one entry, not a longer function.
_ADAPTERS: dict[str, Callable[[dict[str, Any]], Query]] = {
    "game_log": _adapt_game_log,
    "player_stat": _adapt_player_stat,
    "threshold_count": _adapt_threshold_count,
    "single_game_high": _adapt_single_game_high,
    "player_splits": _adapt_player_splits,
    "record_when": _adapt_record_when,
}


def to_query(intent: str, slots: dict[str, Any]) -> Query:
    """The intent's default point of the algebra: the query a bare router
    intent means before any of the question's own words move it (see
    :func:`association.query.compose.move.move_point`).

    .. versionadded:: 4.4.0

    .. versionchanged:: 4.5.0
       ``game_log`` with ``season_type_unstated`` ("his last 5 games") is a
       point - both season types, which the relation reads at once - rather
       than :class:`~association.query.compose.core.Unsupported`.
    """
    adapter = _ADAPTERS.get(intent)
    if adapter is None:
        raise Unsupported(f"no adapter for {intent}")
    return adapter(slots)
