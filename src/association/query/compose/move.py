"""The point moves. :func:`~association.query.compose.adapt.to_query` gives
the intent's default point; the question's own words - read in code, the way
``route()``'s ``CODE_ASSIGNED_INTENTS`` and its ``_validate_*`` helpers read
them, never through the router prompt - may move the measure, the skeleton or
the aggregate. Two slot repairs are included because the question text
supports them exactly (the ``override_invented_players`` /
``restore_dropped_players`` discipline described in ``AGENTS.md``): a subject
the router dropped, and player names the router filed as the ``opponent``.

.. versionadded:: 4.4.0
"""

from __future__ import annotations

import re
from typing import Any

import duckdb

from association.query.entities import players_named_in
from association.query.measures import MEASURE_WORDS
from association.query.metrics import PER_GAME_MIN_GAMES

from .adapt import DEFAULT_SINGLE_GAME_LIMIT, _clamp, _named_player, to_query
from .core import COLUMNS, DERIVED, LINE, Query, Unsupported

#: The router's own stat names that are not relation columns, as measures.
MEASURE_ALIASES: dict[str, str] = {
    "ts_pct": "ts_pct",
    "true_shooting": "ts_pct",
    "efg_pct": "efg_pct",
    "usage_pct": "usage_pct",
    "game_score": "game_score",
    "plus_minus": "plusMinus",
    "plusMinus": "plusMinus",
    "threePointFieldGoalPct": "three_pct",
    "three_point_pct": "three_pct",
    "fieldGoalPct": "fg_pct",
    "fg_pct": "fg_pct",
    "freeThrowPct": "ft_pct",
    "points_per_game": "points",
    "rebounds_per_game": "rebounds",
    "assists_per_game": "assists",
    "triple_double": "triple_double",
    "triple_doubles": "triple_double",
    "double_double": "double_double",
    "double_doubles": "double_double",
    "pra": "pra",
    "wins": "won",
}
"""A router ``stat`` value that names a measure this package computes rather
than a stored column, mapped to that measure's name.

.. versionadded:: 4.4.0
"""

#: Words in the question for a measure the router may not have named.
WORD_MEASURES: list[tuple[str, str]] = [
    (r"\bts ?%|\btrue shooting\b", "ts_pct"),
    (r"\befg\b|\beffective field goal", "efg_pct"),
    (r"\bplus[ /-]?minus\b|\+/-", "plusMinus"),
    (r"\bgame score\b", "game_score"),
    (r"\busage\b", "usage_pct"),
    (r"\btriple[ -]?doubles?\b|\btd3s?\b|\btds\b", "triple_double"),
    (r"\bdouble[ -]?doubles?\b|\bdd\b", "double_double"),
    (r"\bfg ?%|\bfg percentage\b|\bfield goal percentage\b", "fg_pct"),
    (r"\b3 ?pt ?%|\b3 point percentage\b|\bthree point percentage\b|\b3p%", "three_pct"),
    (r"\bft ?%|\bfree throw percentage\b", "ft_pct"),
    (r"\bpra\b|\bpts\+reb\+ast\b|points\+rebounds\+assists", "pra"),
    (r"\bfouled out\b|\bfoul(ed)? outs?\b", "fouled_out"),
]
"""``(pattern, measure)`` - a phrase the question carries that names a measure directly.

.. versionadded:: 4.4.0
"""

#: Measures that are conditions rather than counted quantities - "how many
#: triple-doubles" counts games where the measure is true.
BOOLEAN_MEASURES: frozenset[str] = frozenset({"triple_double", "double_double", "won", "fouled_out"})
"""Measures read as a per-game condition rather than a counted quantity.

.. versionadded:: 4.4.0
"""

_TOP_IN_A_GAME = re.compile(r"\b(most|highest|best|career[- ]high|record)\b.*\b(in a\b.*\bgame|single[- ]game|career[- ]high)\b|\bcareer[- ]high\b", re.I)
# Not this relation's question: a team as the subject, an opponent's or
# allowed figure, or a period. Refused, never answered with a ranking of
# players - "least points by the Wizards in the first half" ranked players
# by fewest points until this existed.
_NOT_PLAYERS = re.compile(r"\bby team\b|\bteams?\b|\ballowed\b|\bopponent'?s?\b|\bbench points\b|\bfranchise\b", re.I)
_PERIOD = re.compile(r"\b(quarter|qtr|half|period|overtime|\d(?:st|nd|rd|th) q|q[1-4]|[1-4]q|[12]h)\b", re.I)  # codespell:ignore nd - an ordinal suffix
_HOW_MANY_OR_OFTEN = re.compile(r"\bhow many\b|\bhow often\b|\bnumber of\b|\btimes\b", re.I)
_WON = re.compile(r"\b(won|wins|win)\b", re.I)
_FEWEST = re.compile(r"\b(fewest|least|lowest)\b", re.I)
_TOTAL = re.compile(r"\btotal\b", re.I)
_VS = re.compile(r"\b(vs\.?|versus|against)\b", re.I)


def _measure_words(question: str) -> list[str]:
    """Every measure :data:`WORD_MEASURES` finds named in ``question``, in list order."""
    found: list[str] = []
    ql = question.lower()
    for pattern, name in WORD_MEASURES:
        if re.search(pattern, ql):
            found.append(name)
    return found


def _stat_measure(stat: Any) -> str | None:
    """A router ``stat`` as a measure this package knows, through
    :data:`MEASURE_ALIASES` and then :data:`~association.query.measures.MEASURE_WORDS`."""
    if not isinstance(stat, str) or not stat.strip():
        return None
    if stat in MEASURE_ALIASES:
        return MEASURE_ALIASES[stat]
    if stat in COLUMNS or stat in DERIVED:
        return stat
    return MEASURE_WORDS.get(stat.strip().lower())


def repair(con: duckdb.DuckDBPyConnection, slots: dict[str, Any], question: str) -> dict[str, Any]:
    """Two repairs the question text supports exactly - never a guess between
    candidates. Returns a new dict.

    .. versionadded:: 4.4.0
    """
    slots = dict(slots)
    named = players_named_in(con, question)
    # A subject the router dropped ("how many playoff games has embiid won?"
    # arrived with a team and no player): restore it when the question names
    # exactly one player.
    if not _named_player(slots) and len(named) == 1:
        slots["player"] = str(named[0])
    # Player names filed as the opponent ("bane game log without anthony
    # black and franz wagner") are teammates absent, when the question says so.
    opp = slots.get("opponent")
    if isinstance(opp, str) and opp.strip() and re.search(r"\bwithout\b", question, re.I):
        parts = [p.strip() for p in re.split(r",|\band\b", opp) if p.strip()]
        if parts and all(any(p.lower() in str(n).lower() or str(n).lower() in p.lower() for n in named) for p in parts):
            slots["without"] = [*(slots.get("without") or []), *parts]
            slots["opponent"] = None
    return slots


#: A question's position word, mapped to :data:`~association.query.templates.common.POSITION_CODES`' own letter.
POSITIONS: list[tuple[str, str]] = [
    (r"\bcenters?\b", "C"),
    (r"\bpoint guards?\b", "PG"),
    (r"\bshooting guards?\b", "SG"),
    (r"\bpower forwards?\b", "PF"),
    (r"\bsmall forwards?\b", "SF"),
    (r"\bforwards?\b", "F"),
    (r"\bguards?\b", "G"),
]
"""``(pattern, position code)`` - the words a position-group question uses.

.. versionadded:: 4.4.0
"""

_RANKING = re.compile(r"\b(leaders?|most|highest|top|best|fewest|least|lowest)\b", re.I)
_LOG = re.compile(r"\b(log|gamelog|game log|each game|by game|stats)\b", re.I)


def _position(question: str) -> str | None:
    """The position code :data:`POSITIONS` finds named in ``question``, or None."""
    for pattern, code in POSITIONS:
        if re.search(pattern, question, re.I):
            return code
    return None


def _measure_and_predicates(words: list[str], fallback: str | None) -> tuple[str | None, list[tuple[str, str, Any]]]:
    """The measure a question ranks or lists by, and the boolean measures it
    names as conditions: "highest fg% in a triple-double game" measures
    fg_pct under triple_double = true."""
    measure = next((w for w in words if w not in BOOLEAN_MEASURES), None) or fallback
    predicates = [(w, "=", True) for w in words if w in BOOLEAN_MEASURES and w != measure]
    return measure, predicates


def _asc_or_desc(question: str) -> str:
    """ "asc" for a "fewest"/"least"/"lowest" question, "desc" otherwise."""
    return "asc" if _FEWEST.search(question) else "desc"


def _everyone_guard(question: str) -> None:
    """What a league-wide read cannot answer: a period, or a team's own
    figure read literally as a ranking of players (K2's guards)."""
    if _PERIOD.search(question):
        raise Unsupported("a quarter or half is the period relation's question")
    if _NOT_PLAYERS.search(question) and not _position(question):
        raise Unsupported("a team, an opponent's figure or a franchise is the team relation's question")


def _everyone_opponent(slots: dict[str, Any], question: str) -> dict[str, Any]:
    """A team beside no player, with "vs"/"against", is the opponent."""
    slots = dict(slots)
    if isinstance(slots.get("team"), str) and slots["team"].strip() and not slots.get("opponent") and _VS.search(question):
        slots["opponent"], slots["team"] = slots["team"], None
    return slots


def _everyone_threshold_predicates(slots: dict[str, Any], question: str, measure: str | None, predicates: list[tuple[str, str, Any]]) -> list[tuple[str, str, Any]]:
    """The number's own words in the question name its column ("a 30 point
    triple double game" is points >= 30, whatever stat the router filed
    beside the threshold); the router's stat is read only when the question
    carries no such phrase."""
    threshold = slots.get("threshold")
    if not (isinstance(threshold, int) and not isinstance(threshold, bool) and threshold > 0):
        return predicates
    phrase = re.search(rf"\b{threshold}\s*\+?\s*[-]?\s*([a-z][a-z ]{{0,24}}?)\b(?=\s|$)", question, re.I)
    column = None
    if phrase:
        tokens = phrase.group(1).lower().split()
        for width in (3, 2, 1):
            candidate = " ".join(tokens[:width])
            if candidate in MEASURE_WORDS:
                column = MEASURE_WORDS[candidate]
                break
    if column is None and slots.get("stat"):
        column = _stat_measure(slots["stat"])
    if column and column != measure:
        return [*predicates, (column, ">=", threshold)]
    return predicates


def _everyone_single_game(slots: dict[str, Any], question: str, measure: str | None, predicates: list[tuple[str, str, Any]], position: str | None) -> Query | None:
    """ "Most ... in a game" over everyone: rows by measure, league-wide."""
    if not (_TOP_IN_A_GAME.search(question) and measure):
        return None
    return Query(
        slots,
        "rows",
        [measure, *(m for m in LINE if m != measure)],
        "none",
        "none",
        predicates,
        "measure",
        _asc_or_desc(question),
        _clamp(slots.get("limit"), DEFAULT_SINGLE_GAME_LIMIT),
        subject="everyone",
        position=position,
    )


def _everyone_threshold_count(intent: str, slots: dict[str, Any], predicates: list[tuple[str, str, Any]], position: str | None) -> Query | None:
    """A league-wide count: who had games clearing the line(s). Without a
    line to count there is nothing to rank - refused, never turned into a
    per-game ranking."""
    if intent != "threshold_count":
        return None
    if not predicates:
        raise Unsupported("a league-wide count needs the line(s) it counts; none could be read from the question")
    return Query(slots, "grouped", [], "count", "player", predicates, "measure", "desc", _clamp(slots.get("limit"), 10), subject="everyone", position=position)


def _everyone_ranking(intent: str, slots: dict[str, Any], question: str, measure: str | None, predicates: list[tuple[str, str, Any]], position: str | None) -> Query | None:
    """A ranking word, or a leaderboard/single-game-high intent: grouped by player."""
    if not (_RANKING.search(question) or intent in ("leaderboard", "single_game_high")):
        return None
    aggregate = "total" if _TOTAL.search(question) else "per_game"
    return Query(
        slots,
        "grouped",
        [measure or "points"],
        aggregate,
        "player",
        predicates,
        "measure",
        _asc_or_desc(question),
        _clamp(slots.get("limit"), 10),
        minimum_games=PER_GAME_MIN_GAMES,
        subject="everyone",
        position=position,
    )


def _everyone_position_log(slots: dict[str, Any], question: str, position: str | None) -> Query | None:
    """A log word with a position: rows, over that position group."""
    if not (position and _LOG.search(question)):
        return None
    return Query(slots, "rows", list(LINE), "none", "none", [], "date", "desc", _clamp(slots.get("limit"), 10), subject="everyone", position=position)


def _everyone_point(intent: str, slots: dict[str, Any], question: str, measure: str | None) -> Query:
    """No player named: the league-wide read of the same relation. A ranking
    word makes it grouped by player; a log word with a position makes it
    rows; "most ... in a game" is rows by measure over everyone."""
    _everyone_guard(question)
    slots = _everyone_opponent(slots, question)
    position = _position(question)
    words = _measure_words(question)
    measure, predicates = _measure_and_predicates(words, measure if measure not in BOOLEAN_MEASURES else None)
    predicates = _everyone_threshold_predicates(slots, question, measure, predicates)
    single = _everyone_single_game(slots, question, measure, predicates, position)
    if single is not None:
        return single
    counted = _everyone_threshold_count(intent, slots, predicates, position)
    if counted is not None:
        return counted
    ranked = _everyone_ranking(intent, slots, question, measure, predicates, position)
    if ranked is not None:
        return ranked
    logged = _everyone_position_log(slots, question, position)
    if logged is not None:
        return logged
    raise Unsupported("no player subject and no ranking or position-group reading of the question")


def _measure_for_named(slots: dict[str, Any], question: str) -> str | None:
    """The measure a named-player question moves to: the question's own word first, the router's stat otherwise."""
    words = _measure_words(question)
    stat = _stat_measure(slots.get("stat"))
    return words[0] if words else stat


def _career_slots(slots: dict[str, Any]) -> dict[str, Any]:
    """``route()``'s own rule for a count by a player (``_HOW_MANY_OR_OFTEN``, step 2
    B5): with no season named, "how many ... has he" is his career."""
    unscoped = not isinstance(slots.get("season"), int) and not slots.get("span") and not slots.get("since")
    return {**slots, "span": "career"} if unscoped else slots


def _move_single_game(slots: dict[str, Any], question: str, measure: str | None) -> Query | None:
    """ "Most ... in a game" for a named player: rows by measure."""
    if not (_TOP_IN_A_GAME.search(question) and measure and measure not in BOOLEAN_MEASURES):
        return None
    limit = slots.get("limit") if slots.get("limit", 0) > 0 else None
    return Query(slots, "rows", [measure, *(m for m in LINE if m != measure)], "none", "none", [], "measure", "desc", _clamp(limit, DEFAULT_SINGLE_GAME_LIMIT))


def _move_how_many_won(slots: dict[str, Any], question: str, measure: str | None, intent: str, career: dict[str, Any]) -> Query | None:
    """ "How many ... has he won" - a count with the ``won`` predicate."""
    if not (_HOW_MANY_OR_OFTEN.search(question) and _WON.search(question) and (measure in (None, "won", "points") or intent in ("record_when", "threshold_count"))):
        return None
    return Query(career, "scalar", [], "count", "none", [("won", "=", True)])


def _move_boolean_count(question: str, measure: str | None, intent: str, career: dict[str, Any]) -> Query | None:
    """A boolean measure ("triple-doubles") asked "how many": a count with that predicate."""
    if not (measure in BOOLEAN_MEASURES and measure != "won" and (_HOW_MANY_OR_OFTEN.search(question) or intent in ("threshold_count", "player_stat", "other"))):
        return None
    return Query(career, "scalar", [], "count", "none", [(measure, "=", True)])


def _move_player_history(intent: str, slots: dict[str, Any], career: dict[str, Any], measure: str | None) -> Query | None:
    """``player_history``: a per-season history is a career grouped by season, newest first."""
    if intent != "player_history":
        return None
    return Query(career, "grouped", [measure or "points"], "per_game", "season", [], "date", "desc", _clamp(slots.get("limit"), 10))


def _move_default(intent: str, slots: dict[str, Any], measure: str | None) -> Query:
    """The intent's default point, with the measure the question named added on."""
    base = to_query(intent, slots)
    if measure and measure not in base.measures:
        if base.skeleton == "rows":
            base.measures = [measure, *base.measures]
        else:
            base.measures = [measure]
    return base


def _move_named(intent: str, slots: dict[str, Any], question: str) -> Query:
    """The moves that apply once a player is named, tried in order - a
    single-game high, a career win count, a career boolean count, a
    per-season history, and finally the intent's own default point."""
    if _PERIOD.search(question):
        raise Unsupported("a quarter or half is the period relation's question")
    measure = _measure_for_named(slots, question)
    single = _move_single_game(slots, question, measure)
    if single is not None:
        return single
    career = _career_slots(slots)
    how_many_won = _move_how_many_won(slots, question, measure, intent, career)
    if how_many_won is not None:
        return how_many_won
    boolean_count = _move_boolean_count(question, measure, intent, career)
    if boolean_count is not None:
        return boolean_count
    history = _move_player_history(intent, slots, career, measure)
    if history is not None:
        return history
    return _move_default(intent, slots, measure)


def move_point(con: duckdb.DuckDBPyConnection, intent: str, slots: dict[str, Any], question: str) -> Query:
    """The intent's default point, moved by the question's own words: a
    measure beyond a template's list, a skeleton move ("most ... in a game" =
    rows by measure; "how many ... won" = count with a predicate), and two
    slot repairs (:func:`repair`) - none of it through a prompt edit.

    .. versionadded:: 4.4.0
    """
    slots = repair(con, slots, question)
    if not _named_player(slots):
        return _everyone_point(intent, slots, question, _stat_measure(slots.get("stat")))
    return _move_named(intent, slots, question)
