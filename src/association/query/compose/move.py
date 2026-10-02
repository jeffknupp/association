"""The point moves. :func:`~association.query.compose.adapt.to_query` gives
the intent's default point; the question's own words - read in code, the way
``route()``'s ``CODE_ASSIGNED_INTENTS`` and its ``_validate_*`` helpers read
them, never through the router prompt - may move the measure, the skeleton or
the aggregate. Two slot repairs are included because the question text
supports them exactly (the ``subject.apply_subject`` discipline described in
``AGENTS.md``): a subject the router dropped, and player names the router
filed as the ``opponent``.

.. versionadded:: 4.4.0
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import TYPE_CHECKING, Any, Literal

import duckdb

from association.query.leaderboard import resolve_metric
from association.query.measures import BOOLEAN_MEASURES, DERIVED_LINES, DERIVED_MEASURES, GAME_COLUMNS, HISTORY_STATS, LINE, MEASURE_WORDS, TEAM_GAME_MEASURES, TEAM_SEASON_MEASURES
from association.query.metrics import PER_GAME_MIN_GAMES, TEAM_FIELD_WORDS
from association.query.reading import Aggregate, Reading, Scope
from association.query.templates.common import DEFAULT_LIMIT, TEAM_ONLY_INTENTS, TemplateResult, _clamp_limit, ordinal_word, unhonored_scoping
from association.query.templates.players import leaderboard_shot_distance_refusal

from .adapt import DEFAULT_GAME_LOG_LIMIT, DEFAULT_SINGLE_GAME_LIMIT, _named_player_in, _to_reading_scope
from .core import Query, Refused, Unsupported
from .plan import plan
from .team import TeamQuery

if TYPE_CHECKING:
    from association.query.subject import Subject

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


_TOP_IN_A_GAME = re.compile(r"\b(most|highest|best|career[- ]high|record)\b.*\b(in a\b.*\bgame|single[- ]game|career[- ]high)\b|\bcareer[- ]high\b", re.I)
# Not this relation's question: a team as the subject, an opponent's or
# allowed figure, or a period. Refused, never answered with a ranking of
# players - "least points by the Wizards in the first half" ranked players
# by fewest points until this existed.
_NOT_PLAYERS = re.compile(r"\bby team\b|\bteams?\b|\ballowed\b|\bopponent'?s?\b|\bbench points\b|\bfranchise\b", re.I)
_PERIOD = re.compile(r"\b(quarter|qtr|half|period|overtime|\d(?:st|nd|rd|th) q|q[1-4]|[1-4]q|[12]h)\b", re.I)  # codespell:ignore nd - an ordinal suffix
_HOW_MANY_OR_OFTEN = re.compile(r"\bhow many\b|\bhow often\b|\bnumber of\b|\btimes\b", re.I)
_WON = re.compile(r"\b(won|wins|win)\b", re.I)
#: "least" on its own is ascending ("the least points"); "AT least" is the
#: minimum-sample floor phrase (:func:`_ranking_minimum`, F056: "with at
#: least 100 attempts") and names no direction at all - the negative
#: lookbehind keeps that phrase from flipping a "highest ..." ranking to
#: ascending, which it did before this existed (measured against the real
#: warehouse: "highest 3-point percentage ... with at least 40 games"
#: answered lowest-first).
_FEWEST = re.compile(r"\b(fewest|lowest)\b|(?<!at )\bleast\b", re.I)
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


def _stat_measure(stat: str | None) -> str | None:
    """A router ``stat`` as a measure this package knows, through
    :data:`MEASURE_ALIASES` and then :data:`~association.query.measures.MEASURE_WORDS`."""
    if stat is None or not stat.strip():
        return None
    if stat in MEASURE_ALIASES:
        return MEASURE_ALIASES[stat]
    if stat in GAME_COLUMNS or stat in DERIVED_MEASURES:
        return stat
    return MEASURE_WORDS.get(stat.strip().lower())


_RANKING = re.compile(r"\b(leaders?|most|highest|top|best|fewest|least|lowest)\b", re.I)
_LOG = re.compile(r"\b(log|gamelog|game log|each game|by game|stats)\b", re.I)


def _measure_and_predicates(words: list[str], fallback: str | None) -> tuple[str | None, list[tuple[str, str, Any]]]:
    """The measure a question ranks or lists by, and the boolean measures it
    names as conditions: "highest fg% in a triple-double game" measures
    fg_pct under triple_double = true."""
    measure = next((w for w in words if w not in BOOLEAN_MEASURES), None) or fallback
    predicates = [(w, "=", True) for w in words if w in BOOLEAN_MEASURES and w != measure]
    return measure, predicates


def _asc_or_desc(question: str) -> Literal["asc", "desc"]:
    """ "asc" for a "fewest"/"least"/"lowest" question, "desc" otherwise."""
    return "asc" if _FEWEST.search(question) else "desc"


_EVER = re.compile(r"\bever\b|\ball[- ]time\b", re.I)


def _everyone_career_scope(scope: Scope, question: str) -> Scope:
    """ "Ever"/"all-time" in the question is a career span for a league-wide
    read, the way :func:`_career_scope` gives a named player's own unscoped
    count his whole career - stated because a league-wide read with no
    season named otherwise defaults to the CURRENT season
    (:func:`~association.query.compose.core._resolve_everyone`), not "every
    season on record" the word asks for.

    .. versionadded:: 4.4.0
    """
    if _EVER.search(question) and scope.season is None:
        return replace(scope, span="career")
    return scope


_AT_LEAST = re.compile(r"\bat least\s+(\d+)\s+([a-z]+)", re.I)


def _ranking_minimum(question: str) -> tuple[str, int] | None:
    """The "at least N <unit>" phrase naming a league ranking's own minimum
    sample ("with at least 100 attempts", F056) - the unit and the number,
    or ``None`` where the question names no such floor.

    .. versionadded:: 4.4.0
    """
    match = _AT_LEAST.search(question)
    if not match:
        return None
    return match.group(2).lower(), int(match.group(1))


def _everyone_guard(intent: str, question: str, position: str | None, *, period_is_condition: bool = False) -> None:
    """What a league-wide read cannot answer: a period, a team's own figure
    read literally as a ranking of players (K2's guards), or a team-only
    intent's question at all - "Best NBA record since January 31st 2015"
    reached this read once the reading stopped naming Travis Best for it,
    and a player ranking refusing it for want of a "record" measure names
    the wrong cause; it is not this relation's question."""
    if _PERIOD.search(question) and not period_is_condition:
        raise Unsupported("a quarter or half is the period relation's question")
    # "the top 50 ... with the team they play for" (F017) names no team's
    # question: the team is a column the ranking shows.
    if _NOT_PLAYERS.search(TEAM_FIELD_WORDS.sub(" ", question)) and not position:
        raise Unsupported("a team, an opponent's figure or a franchise is the team relation's question")
    if intent in TEAM_ONLY_INTENTS:
        raise Unsupported("a team's own question is not the player relation's")


def _everyone_opponent(scope: Scope, question: str) -> Scope:
    """A team beside no player, with "vs"/"against", is the opponent."""
    if scope.team is not None and scope.team.strip() and not scope.opponent and _VS.search(question):
        return replace(scope, opponent=scope.team, team=None)
    return scope


def _everyone_threshold_predicates(scope: Scope, question: str, measure: str | None, predicates: list[tuple[str, str, Any]]) -> list[tuple[str, str, Any]]:
    """The number's own words in the question name its column ("a 30 point
    triple double game" is points >= 30, whatever stat the router filed
    beside the threshold); the router's stat is read only when the question
    carries no such phrase."""
    threshold = scope.threshold
    if threshold is None or threshold <= 0:
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
    if column is None and scope.stat:
        column = _stat_measure(scope.stat)
    if column and column != measure:
        return [*predicates, (column, ">=", threshold)]
    return predicates


def _everyone_single_game(intent: str, scope: Scope, question: str, measure: str | None, predicates: list[tuple[str, str, Any]], position: str | None) -> Reading | None:
    """ "Most ... in a game" over everyone: rows by measure, league-wide.

    .. versionchanged:: 5.0.0
       Also for a ``single_game_high`` question whose words do not say "in a
       game" - the intent names the shape itself. "What was the highest
       scoring game by a player this year?" used to fall to
       :func:`_everyone_ranking` and answer with a ranking of per-game
       AVERAGES (Luka Doncic, 33.5) where the question, and the template,
       name one game (Bam Adebayo's 83).
    """
    if not ((_TOP_IN_A_GAME.search(question) or intent == "single_game_high") and measure):
        return None
    return Reading(
        scope=scope,
        shape="rows",
        measures=[measure, *(m for m in LINE if m != measure)],
        aggregate="none",
        group="none",
        predicates=predicates,
        order="measure",
        direction=_asc_or_desc(question),
        limit=_clamp_limit(scope.limit, DEFAULT_SINGLE_GAME_LIMIT),
        relation="everyone",
        position=position,
    )


#: A word in the question naming what a "highest/biggest ... triple-double"
#: ranking of GAMES orders by - the local counterpart of ``WORD_MEASURES``
#: for the plain box-score words that list does not carry (those name the
#: default four-stat line already; this is only reached once a boolean
#: measure has taken the ranking word - see :func:`_everyone_boolean_game_ranking`).
_BOOLEAN_RANK_WORDS: list[tuple[str, str]] = [
    (r"\bscoring\b|\bpoints?\b|\bpts\b", "points"),
    (r"\brebounds?\b|\bboards?\b", "rebounds"),
    (r"\bassists?\b", "assists"),
    (r"\bsteals?\b", "steals"),
    (r"\bblocks?\b", "blocks"),
]
_BOOLEAN_GAME_RANKING = re.compile(r"\b(highest|biggest|largest|best)\b", re.I)


def _boolean_game_measure(question: str) -> str:
    """The measure a "highest/biggest ... triple-double" ranks the
    qualifying games BY - the question's own word (:data:`_BOOLEAN_RANK_WORDS`)
    first, points otherwise: "highest scoring" and "biggest" both mean the
    game's point total unless another stat is named."""
    for pattern, name in _BOOLEAN_RANK_WORDS:
        if re.search(pattern, question, re.I):
            return name
    return "points"


def _everyone_boolean_game_ranking(question: str, scope: Scope, predicates: list[tuple[str, str, Any]], position: str | None) -> Reading | None:
    """ "players with the highest scoring triple doubles", "biggest triple
    double", "most rebounds in a double double" (#199, F124): a RANKING OF
    THE GAMES that satisfy a boolean measure (:data:`BOOLEAN_MEASURES`) by
    another measure - rows over everyone, the boolean as a predicate,
    ordered by the question's own stat word. Not
    :func:`_everyone_ranking`'s per-player AVERAGE, which would need
    ``minimum_games`` triple-doubles just to rank anyone, and not
    :func:`_everyone_single_game`'s "in a game" phrasing, which this shape
    does not use ("the highest scoring triple doubles" names no game at
    all - it is the games themselves being ranked).

    .. versionadded:: 4.4.0
    """
    boolean = [name for name, _, value in predicates if name in BOOLEAN_MEASURES and value is True]
    if not boolean:
        return None
    # "Most triple-doubles" is a COUNT per player (the season line's own
    # metric, leaderboard's retired reader): the games are ranked only where
    # the question sizes them - "highest scoring", "biggest", or a stat word
    # of its own ("most rebounds in a double double"), or the parser's
    # `ranked_by`.
    sized = _BOOLEAN_GAME_RANKING.search(question) or scope.ranked_by or any(re.search(pattern, question, re.I) for pattern, _ in _BOOLEAN_RANK_WORDS)
    if not sized:
        return None
    measure = _boolean_game_measure(question)
    return Reading(
        scope=scope,
        shape="rows",
        measures=[measure, *(m for m in LINE if m != measure)],
        aggregate="none",
        group="none",
        predicates=predicates,
        order="measure",
        direction=_asc_or_desc(question),
        limit=_clamp_limit(scope.limit, DEFAULT_SINGLE_GAME_LIMIT),
        relation="everyone",
        position=position,
    )


_NUMBER_STAT = re.compile(r"\b(\d{1,3})\s*\+?\s*((?:[a-z]+\s*){1,3})", re.I)


def _numbered_stat_lines(question: str) -> list[tuple[str, str, Any]]:
    """Every "<N> <stat>" pair ``question`` itself names, as predicates - the
    league-wide multi-line count a single ``threshold`` slot cannot carry
    (F161: "33 point and 13 rebound and 10 assist 2 blocks and 2 steals").
    Each number's own trailing word(s) name its column through
    :data:`~association.query.measures.MEASURE_WORDS`, the lookup
    :func:`_everyone_threshold_predicates` already uses for one number -
    applied here to every number the question names, not only the router's
    own ``threshold``, and never a duplicate column.

    .. versionadded:: 4.4.0
    """
    predicates: list[tuple[str, str, Any]] = []
    seen: set[str] = set()
    for match in _NUMBER_STAT.finditer(question):
        value = int(match.group(1))
        if not value:
            continue
        words = match.group(2).lower().split()
        column = None
        for width in (3, 2, 1):
            candidate = " ".join(words[:width])
            if candidate in MEASURE_WORDS:
                column = MEASURE_WORDS[candidate]
                break
        if column and column not in seen:
            seen.add(column)
            predicates.append((column, ">=", value))
    return predicates


def _everyone_multi_line_games(intent: str, scope: Scope, question: str, predicates: list[tuple[str, str, Any]], position: str | None) -> Reading | None:
    """Several "<N> <stat>" lines named in one question at once (F161) are
    all conditions on the SAME game, so the answer is which GAMES cleared
    every line and who had them - rows over everyone, the predicates
    stated, never :func:`_everyone_threshold_count`'s per-player COUNT of a
    single line (still the right shape once there is only one - this move
    stands aside for it, below).

    .. versionadded:: 4.4.0

    .. versionchanged:: 5.0.0
       Stands aside for a ranking word ("who had the MOST 30+ point 10+
       rebound games"): that asks who cleared every line most often - the
       per-player count :func:`_everyone_threshold_count` gives, with the
       lines the relation already narrows by (``below``/``above``) - not
       which games did. It used to list the last 25 such games under a
       heading naming no leader at all, where the template names Nikola
       Jokic's 20.
    """
    if intent != "threshold_count" or _RANKING.search(question):
        return None
    text_lines = _numbered_stat_lines(question)
    lines = text_lines if len(text_lines) > len(predicates) else predicates
    if len(lines) < 2:
        return None
    return Reading(
        scope=scope,
        shape="rows",
        measures=[name for name, _, _ in lines],
        aggregate="none",
        group="none",
        predicates=lines,
        order="date",
        direction="desc",
        limit=_clamp_limit(scope.limit, 25),
        relation="everyone",
        position=position,
    )


def _everyone_threshold_count_line(scope: Scope) -> list[tuple[str, str, Any]]:
    """The router's own ``stat`` at its own ``threshold``, as the one line a
    league-wide count counts - where :func:`_everyone_threshold_predicates`
    added nothing because the line is on the very measure it would rank by.
    "Most games with 15+ assists in 2024?" declined for want of a line until
    this existed: the line was the whole question.

    .. versionadded:: 5.0.0
    """
    column = _stat_measure(scope.stat)
    threshold = scope.threshold
    if column is None or column in BOOLEAN_MEASURES or threshold is None or threshold < 1:
        return []
    return [(column, ">=", threshold)]


def _everyone_threshold_count(intent: str, scope: Scope, predicates: list[tuple[str, str, Any]], position: str | None) -> Reading | None:
    """A league-wide count: who had games clearing the line(s). Without a
    line to count there is nothing to rank - refused, never turned into a
    per-game ranking."""
    if intent != "threshold_count" and not (predicates and scope.team):
        # A TEAM's players' boolean games ("thunder all-time triple doubles",
        # yardstick-v2 F152) is this count too, whatever intent the router
        # filed - the team narrows the league read to its roster's games.
        return None
    if not predicates and intent == "threshold_count":
        if scope.threshold is not None and scope.threshold < 1:
            # threshold_count's own refusal: measured, "most 3 pointers made
            # since 2020" arrived as threshold 0 and would count every game.
            raise Unsupported(f"a threshold of {scope.threshold} counts every game - not a question threshold_count answers")
        predicates = _everyone_threshold_count_line(scope)
    if not predicates:
        raise Unsupported("a league-wide count needs the line(s) it counts; none could be read from the question")
    if scope.season_n:
        # The refusal threshold_count's retired template gave:
        # "his 15th season" is a place in one career, and the league has none -
        # read over everyone it narrowed to players in their 15th season of the
        # default year while the sentence named only the year.
        raise Unsupported(f"the {ordinal_word(scope.season_n)} season is a place in one player's career, and no player was named")
    # threshold_count's own leaderboard length (DEFAULT_LIMIT, five names)
    # where it is that intent's question; ten for a team's roster count
    # (F152), which also states the whole count beneath the ones listed.
    listed = DEFAULT_LIMIT if intent == "threshold_count" else 10
    return Reading(
        scope=scope,
        shape="grouped",
        measures=[],
        aggregate="count",
        group="player",
        predicates=predicates,
        order="measure",
        direction="desc",
        limit=_clamp_limit(scope.limit, listed),
        relation="everyone",
        position=position,
    )


def _no_ranking_for(stat: str) -> TemplateResult:
    """The refusal for a ranking by a stat this relation has no measure for."""
    message = f"No ranking reads {stat!r} on the player-games relation - it only ranks the box-score measures it knows, not a NetPoints or other outside figure."
    return TemplateResult(data={"message": message, "stat": stat}, answer=message)


def _everyone_ranking(intent: str, scope: Scope, question: str, measure: str | None, predicates: list[tuple[str, str, Any]], position: str | None) -> Reading | None:
    """A ranking word, or a leaderboard/single-game-high intent: grouped by
    player. A "with at least N games" phrase in the question replaces the
    default minimum sample (:data:`~association.query.metrics.PER_GAME_MIN_GAMES`)
    with the one the question itself named; a unit the relation has no
    HAVING clause for (attempts, minutes, ...) is refused by name rather
    than silently dropped or misapplied as a games count (F056:
    "... by a shooting guard with at least 100 attempts").

    .. versionchanged:: 4.4.0
       Reads a "with at least N <unit>" floor (:func:`_ranking_minimum`).

    .. versionchanged:: 4.4.0
       Refuses by name, rather than silently ranking by points, when the
       question named a real ``stat`` this relation has no measure for -
       "who had the highest netpoint game this season" used to rank by
       POINTS instead (measured live: the stat this relation cannot read,
       NetPoints, fell back to the one measure every league-wide ranking
       already defaults to, and the answer read as though it had ranked
       what was asked). ``measure`` is ``None`` for two different reasons -
       no stat was named at all (the plain "top scorers" ranking, still
       points by default) or a real one was named and did not map
       (:func:`_stat_measure`) - and only the second is a refusal; the
       first keeps its default.
    """
    if not (_RANKING.search(question) or intent in ("leaderboard", "single_game_high")):
        return None
    seasons = _leaderboard_season_line(intent, scope, question, measure, predicates, position)
    if seasons is not None:
        return seasons
    if measure is None:
        stat = scope.stat
        if stat == "shot_distance":
            # The parser's sentinel (router._route_leaderboard_shot_distance):
            # the retired template's own refusal, naming the real cause.
            raise Refused(leaderboard_shot_distance_refusal())
        if stat is not None and stat.strip():
            raise Refused(_no_ranking_for(stat))
    aggregate: Aggregate = "total" if _TOTAL.search(question) else "per_game"
    minimum_games = PER_GAME_MIN_GAMES
    named_minimum = _ranking_minimum(question)
    if named_minimum is not None:
        unit, count = named_minimum
        if not unit.startswith("game"):
            # A refusal, not a decline: nothing downstream reads an attempts or
            # minutes floor either, and "None" here sent yardstick-v2 F056 to
            # the agent for a minute. The sentence names the floor that IS
            # applied so the question can be re-asked with it.
            message = f"A minimum of {count} {unit} is not a floor this ranking can apply yet - only a minimum number of games is. Ask with 'at least N games', or without the floor."
            raise Refused(TemplateResult(data={"message": message, "floor": {"unit": unit, "count": count}}, answer=message))
        minimum_games = count
    return Reading(
        scope=scope,
        shape="grouped",
        measures=[measure or "points"],
        aggregate=aggregate,
        group="player",
        predicates=predicates,
        order="measure",
        direction=_asc_or_desc(question),
        limit=_clamp_limit(scope.limit, 10),
        minimum_games=minimum_games,
        relation="everyone",
        position=position,
    )


def _leaderboard_season_line(intent: str, scope: Scope, question: str, measure: str | None, predicates: list[tuple[str, str, Any]], position: str | None) -> Reading | None:
    """``leaderboard``'s own point (its template retired, ROADMAP plan item
    6, step (g)): a ranking of the league or a team over the SEASON LINE -
    ``run_leaderboard``'s pool, floors, traded-player dedup and NetPoints
    tables - said by its presenter (``compose.present._present_leaderboard``),
    for a stat that resolves to a leaderboard metric with no line on a
    column, no "at least N" floor of the question's own and no position
    group, each of which the game-level ranking reads and the season line
    does not. A measure the words moved in that is not the stat's own is the
    game-level ranking's too ("most points in a game" is a single game;
    "total" stays a rate the presenter reads from ``scope.rate``).

    .. versionadded:: 5.0.0
    """
    # A boolean stat's own predicate ("most triple-doubles": triple_double is
    # True) is the count the season line keeps; any other line on a column
    # is the game-level ranking's.
    own_boolean = all(name == scope.stat and value is True for name, _, value in predicates)
    if intent != "leaderboard" or not own_boolean or position is not None or _ranking_minimum(question) is not None:
        return None
    if resolve_metric(scope.stat, career=scope.span == "career") is None:
        return None
    if measure is not None and _stat_measure(scope.stat) not in (None, measure):
        return None
    return Reading(
        scope=scope,
        shape="grouped",
        measures=[measure or "points"],
        aggregate="per_game",
        group="player",
        predicates=[],
        order="measure",
        direction=_asc_or_desc(question),
        limit=_clamp_limit(scope.limit, 10),
        minimum_games=PER_GAME_MIN_GAMES,
        relation="everyone",
        source="seasons",
    )


def _everyone_position_log(scope: Scope, question: str, position: str | None) -> Reading | None:
    """A log word with a position: rows, over that position group."""
    if not (position and _LOG.search(question)):
        return None
    return Reading(
        scope=scope,
        shape="rows",
        measures=list(LINE),
        aggregate="none",
        group="none",
        predicates=[],
        order="date",
        direction="desc",
        limit=_clamp_limit(scope.limit, 10),
        relation="everyone",
        position=position,
    )


def _everyone_point(intent: str, scope: Scope, question: str, measure: str | None, position: str | None = None) -> Reading:
    """No player named: the league-wide read of the same relation. A ranking
    word makes it grouped by player; a log word with a position makes it
    rows; "most ... in a game" is rows by measure over everyone; a
    "highest/biggest ..." boolean-measure question ranks the GAMES rather
    than counting them; several "<N> <stat>" lines at once list the games
    clearing every one.

    .. versionchanged:: 4.4.0
       "Ever"/"all-time" moves the default current-season span to a career
       one (:func:`_everyone_career_scope`), and tries
       :func:`_everyone_boolean_game_ranking` (#199) and
       :func:`_everyone_multi_line_games` (F161) before the per-player count
       and ranking moves, since both are more specific readings of a
       ``threshold_count``/ranking question than either of those.
    """
    _everyone_guard(intent, question, position, period_is_condition=scope.period_condition is not None)
    scope = _everyone_opponent(scope, question)
    scope = _everyone_career_scope(scope, question)
    words = _measure_words(question)
    measure, predicates = _measure_and_predicates(words, measure if measure not in BOOLEAN_MEASURES else None)
    predicates = _everyone_threshold_predicates(scope, question, measure, predicates)
    single = _everyone_single_game(intent, scope, question, measure, predicates, position)
    if single is not None:
        return single
    boolean_ranked = _everyone_boolean_game_ranking(question, scope, predicates, position)
    if boolean_ranked is not None:
        return boolean_ranked
    multi_line = _everyone_multi_line_games(intent, scope, question, predicates, position)
    if multi_line is not None:
        return multi_line
    counted = _everyone_threshold_count(intent, scope, predicates, position)
    if counted is not None:
        return counted
    ranked = _everyone_ranking(intent, scope, question, measure, predicates, position)
    if ranked is not None:
        return ranked
    logged = _everyone_position_log(scope, question, position)
    if logged is not None:
        return logged
    raise Unsupported("no player subject and no ranking or position-group reading of the question")


def _measure_for_named(scope: Scope, question: str) -> str | None:
    """The measure a named-player question moves to: the question's own word first, the router's stat otherwise."""
    words = _measure_words(question)
    stat = _stat_measure(scope.stat)
    return words[0] if words else stat


def _career_scope(scope: Scope) -> Scope:
    """``route()``'s own rule for a count by a player (``_HOW_MANY_OR_OFTEN``, step 2
    B5): with no season named, "how many ... has he" is his career."""
    unscoped = scope.season is None and not scope.span and not scope.since
    return replace(scope, span="career") if unscoped else scope


def _move_single_game(scope: Scope, question: str, measure: str | None) -> Reading | None:
    """ "Most ... in a game" for a named player: rows by measure - and
    "most points by curry vs lebron", with a player on the other side of
    the games (ROADMAP step 3): against a named opponent, "most" is his best
    meeting, the question a pair's summary never answered."""
    if not measure or measure in BOOLEAN_MEASURES:
        return None
    against = any(c.side == "opponent" for c in scope.conditions)
    if not (_TOP_IN_A_GAME.search(question) or (against and _RANKING.search(question))):
        return None
    return Reading(
        scope=scope,
        shape="rows",
        measures=[measure, *(m for m in LINE if m != measure)],
        aggregate="none",
        group="none",
        predicates=[],
        order="measure",
        direction=_asc_or_desc(question),
        limit=_clamp_limit(scope.limit, DEFAULT_SINGLE_GAME_LIMIT),
    )


def _move_how_many_won(scope: Scope, question: str, measure: str | None, intent: str, career: Scope) -> Reading | None:
    """ "How many ... has he won" - a count with the ``won`` predicate."""
    if not (_HOW_MANY_OR_OFTEN.search(question) and _WON.search(question) and (measure in (None, "won", "points") or intent in ("record_when", "threshold_count"))):
        return None
    return Reading(scope=career, shape="scalar", measures=[], aggregate="count", group="none", predicates=[("won", "=", True)])


def _move_boolean_count(question: str, measure: str | None, intent: str, career: Scope) -> Reading | None:
    """A boolean measure ("triple-doubles") asked "how many": a count with that predicate.

    Also the reading for a boolean measure on a one-figure intent with no
    "how many" at all - "sengun double-doubles vs southeast division career
    away" (yardstick-v2 F055) routed ``player_splits``, whose figure is a
    per-game line, and a condition has no per-game line: its count is the
    only figure there is (``core._agg`` refuses the average).
    """
    if not (measure in BOOLEAN_MEASURES and measure != "won" and (_HOW_MANY_OR_OFTEN.search(question) or intent in ("threshold_count", "player_stat", "player_splits", "other"))):
        return None
    if intent == "threshold_count" and _move_boolean_count_is_line(measure, career):
        # "how many times has embiid fouled out?" arrived as fouls >= 6 - the
        # very line ``fouled_out`` is defined as (DERIVED) - so the router's
        # own count is this one, and threshold_count's default point says it.
        return None
    return Reading(scope=career, shape="scalar", measures=[], aggregate="count", group="none", predicates=[(measure, "=", True)])


def _move_boolean_count_is_line(measure: str, scope: Scope) -> bool:
    """Whether a boolean measure is, by its one definition
    (:data:`~association.query.measures.DERIVED_LINES`), exactly the router's
    own ``stat``/``threshold`` line (``fouled_out`` is ``fouls >= 6``)."""
    column = _stat_measure(scope.stat)
    threshold = scope.threshold
    if column is None or threshold is None:
        return False
    return DERIVED_LINES.get(measure) == (column, threshold)


def _move_player_history(intent: str, scope: Scope, career: Scope, measure: str | None) -> Reading | None:
    """``player_history``: a per-season history, read from the season line
    (``source="seasons"``) over the question's own slots - the template's own
    read. Where the season line does not say it, :func:`games_reading` turns
    it into a career of games grouped by season, newest first."""
    if intent != "player_history":
        return None
    if measure is None and scope.stat is not None and scope.stat not in HISTORY_STATS:
        # A stat named that neither the season line nor the games carry
        # ("shot_distance"): refused, as player_history's retired template
        # refused it - never drawn as the points history the default measure
        # below would read in its place.
        raise Unsupported(f"no per-season history for stat {scope.stat!r}")
    del career  # the season line reads the question's own span; games_reading widens it
    return Reading(
        scope=scope,
        shape="grouped",
        measures=[measure or "points"],
        aggregate="per_game",
        group="season",
        predicates=[],
        order="date",
        direction="desc",
        limit=_clamp_limit(scope.limit, 10),
        source="seasons",
    )


def games_reading(q: Query) -> Query:
    """A season-line point (``source="seasons"``) the season line's own
    readers declined, as the game-level relation reads it: a per-season
    history becomes his career's games grouped by season (the reading the
    compiler gave every history before the season line was a source). An
    unnarrowed ``player_stat`` has no game-level reading that answers the
    same question, so it raises :class:`~association.query.compose.core.Unsupported`,
    as it did before.

    .. versionadded:: 5.0.0
    """
    if q.source != "seasons":
        return q
    if q.group == "season":
        return replace(q, scope=_career_scope(q.scope), source="games")
    if q.group == "player" and q.subject == "everyone":
        # The season line's ranking declined (leaderboard's retired refusals:
        # a metric with no season form, an unknown field, an ambiguous team):
        # the game-level ranking, exactly as it answered behind the template's
        # refusal - except for what it cannot say. A stat this relation has no
        # measure for is refused by name rather than ranked as points, and a
        # `rate` only the season line reads is not dropped.
        stat = q.scope.stat
        if stat is not None and stat.strip() and _stat_measure(stat) is None:
            raise Refused(_no_ranking_for(stat))
        if q.scope.rate:
            raise Unsupported("the relation cannot honor ['rate'] - it would answer for a different span than was asked")
        if q.scope.fields:
            raise Unsupported("the game-level ranking shows no columns beside its measure")
        return replace(q, source="games")
    raise Unsupported("an unnarrowed player line the season line's reader did not say")


def _compare_point(scope: Scope) -> Reading:
    """``player_compare``'s own point (its template retired, ROADMAP plan
    item 6, step (g)): two or more named players' season lines side by side
    (``source="seasons"``, grouped by player), said by its presenter
    (``compose.present._present_player_compare``). The template's own
    refusals are the point's: fewer than two distinct names, and any
    narrowing at all - it honored no scoping slot, and a comparison "vs the
    celtics" answered for the whole season would be the substitution
    ``check_scope`` existed to stop.

    .. versionadded:: 5.0.0
    """
    ignored = unhonored_scoping("player_compare", scope, frozenset())
    if ignored:
        raise Unsupported(f"player_compare cannot honor {ignored} - it would answer for a different span than was asked")
    if len({name for name in scope.players if name.strip()}) < 2:
        raise Unsupported("player_compare needs at least two distinct player names")
    return Reading(scope=scope, shape="grouped", measures=[], aggregate="per_game", group="player", predicates=[], source="seasons")


def _move_default(intent: str, scope: Scope, measure: str | None) -> Reading:
    """The intent's default point, with the measure the question named added on."""
    base = _to_reading_scope(intent, scope)
    if measure and measure not in base.measures:
        return replace(base, measures=[measure, *base.measures] if base.shape == "rows" else [measure])
    return base


def _move_named(intent: str, scope: Scope, question: str) -> Reading:
    """The moves that apply once a player is named, tried in order - a
    single-game high, a career win count, a career boolean count, a
    per-season history, and finally the intent's own default point. A
    quarter or half of a named player's games is ``period_split``'s own
    point (the retired template read its slots alone, so no word moves it);
    under any other intent it is declined, since the point would print the
    game's figure under the quarter's heading."""
    if intent == "period_split":
        return _move_default(intent, scope, None)
    if _PERIOD.search(question) and scope.period_condition is None:
        # The quarter words are a condition's (read into the scope), or the
        # period relation's question.
        raise Unsupported("a quarter or half is the period relation's question")
    measure = _measure_for_named(scope, question)
    single = _move_single_game(scope, question, measure)
    if single is not None:
        return single
    career = _career_scope(scope)
    how_many_won = _move_how_many_won(scope, question, measure, intent, career)
    if how_many_won is not None:
        return how_many_won
    boolean_count = _move_boolean_count(question, measure, intent, career)
    if boolean_count is not None:
        return boolean_count
    history = _move_player_history(intent, scope, career, measure)
    if history is not None:
        return history
    return _move_default(intent, scope, measure)


#: A word in the question naming a team's own measure directly - the team
#: counterpart of :data:`WORD_MEASURES`, over :data:`~association.query.compose.team.GAME_MEASURES`
#: and :data:`~association.query.compose.team.SEASON_MEASURES` rather than
#: the player relation's columns.
_TEAM_WORD_MEASURES: list[tuple[str, str]] = [
    (r"\bpoint(?:s)? differential\b|\bpoint diff\b|\bdifferential\b", "differential"),
    (r"\bpoints? allowed\b|\bopponent'?s? points\b", "points_allowed"),
    (r"\b(?:3|three)[- ]?point(?:er)?s?\b(?:.{0,10}\bmade\b)?", "threePointFieldGoalsMade"),
    (r"\btotal points\b|\bpoints scored\b|\bhow many points\b", "points"),
    (r"\brebounds\b", "rebounds"),
    (r"\bassists\b", "assists"),
    (r"\bsteals\b", "steals"),
    (r"\bblocks\b", "blocks"),
    (r"\bturnovers\b", "turnovers"),
]
"""``(pattern, measure)`` - a phrase naming one of :data:`~association.query.measures.TEAM_GAME_MEASURES`
or :data:`~association.query.measures.TEAM_SEASON_MEASURES` directly.

.. versionadded:: 4.4.0
"""

#: A question about players ON a team - a ranking, a log, or an explicit
#: "who"/"which player" framing - is the league-wide read's question, never
#: the team's own subject.
_TEAM_NOT_SUBJECT = re.compile(r"\bwho\b|\bwhich player\b|\bwhich\b.{0,15}\bplayer\b", re.I)


def _team_measure(scope: Scope, question: str) -> str | None:
    """The team measure a question names - the question's own word first
    (the same priority :func:`_measure_for_named` gives a player's), the
    router's ``stat`` slot otherwise, when it is a column
    :data:`~association.query.measures.TEAM_GAME_MEASURES` or
    :data:`~association.query.measures.TEAM_SEASON_MEASURES` knows.

    .. versionadded:: 4.4.0
    """
    ql = question.lower()
    for pattern, name in _TEAM_WORD_MEASURES:
        if re.search(pattern, ql):
            return name
    stat = scope.stat
    if stat is not None and (stat in TEAM_GAME_MEASURES or stat in TEAM_SEASON_MEASURES):
        return stat
    return None


def team_log_point(scope: Scope, subject: Subject) -> Reading | None:
    """A ``game_log`` question about a team and no player - "show me the
    Knicks last 5 games" - as the team's own games listed: a ``rows`` point
    on the team relation, in date order, the window the question asked
    (:func:`~association.query.compose.present.present_team` says it in the
    retired template's words). The team is the route's own slot, or the one
    the subject reading found where the router dropped it. ``None`` where
    the question is not that: a named player (his own log, the player
    relation's), or a position group ("centers game log vs kings" - the
    template answered the KINGS' log; the league-wide read lists the
    centers' games against them, which is the question).

    .. versionadded:: 5.0.0
    """
    if _named_player_in(scope) or subject.kind in ("player", "pair", "position"):
        return None
    team_text = scope.team if isinstance(scope.team, str) and scope.team.strip() and scope.team != "any_team" else None
    if team_text is None:
        if subject.kind not in ("team", "team_players") or not subject.teams:
            return None
        team_text = subject.teams[0]
    return Reading(
        scope=replace(scope, team=team_text),
        shape="rows",
        measures=["points"],
        aggregate="none",
        group="none",
        predicates=[],
        order="date",
        direction="asc" if scope.order == "first" else "desc",
        limit=_clamp_limit(scope.limit, DEFAULT_GAME_LOG_LIMIT),
        relation="team",
    )


def team_splits_point(scope: Scope, subject: Subject) -> Reading | None:
    """A ``player_splits`` question about a team and no player - "76ers wins
    vs losses" - as the team's own splits: a ``grouped`` point on the team
    relation (:func:`~association.query.compose.present.present_team` says
    it in the retired template's words, ``templates.splits.team_splits``).
    The team is read as :func:`team_log_point` reads it.

    .. versionadded:: 5.0.0
    """
    log = team_log_point(scope, subject)
    if log is None:
        return None
    return Reading(scope=log.scope, shape="grouped", measures=["points"], aggregate="record", group="venue", predicates=[], relation="team")


def team_read_point(con: duckdb.DuckDBPyConnection, scope: Scope, question: str, subject: Subject) -> Reading | None:
    """Whether ``question``/``scope`` name a team as the grammatical
    SUBJECT - no player, a team identifiable (the router's own ``team`` slot,
    or :func:`~association.query.compose.team.team_named_in` when the router
    dropped it, F127's shape), and no ranking/log/period/"who" framing that
    would make it a league-wide read of the PLAYER relation instead
    (``_everyone_point``'s own question - "who leads the Lakers in scoring"
    narrows a player ranking by team, and is not this module's) - and, if so,
    the point it means. ``None`` where nothing here answers, so the caller
    falls back to the league-wide reading exactly as before this existed.

    .. versionadded:: 4.4.0

    .. versionchanged:: 4.4.0
       Ignores the router's ``"any_team"`` placeholder (K2's own corpus:
       "rebounds allowed per team", filed ``team: "any_team"``) rather than
       treating it as a real name to resolve - it already fails
       :func:`~association.query.templates.common._resolved_team` (which
       RAISES for it, unlike a name with no match, which returns a
       clarification), so returning a :class:`~association.query.compose.team.TeamQuery`
       here only delayed the same decline `_everyone_point`'s own
       ``_NOT_PLAYERS`` guard already gives this exact question ("allowed" is
       in it) - through a noisier path, for no different an outcome.

    .. versionchanged:: 5.0.0
       Takes the typed :class:`~association.query.reading.Scope` and the
       :class:`~association.query.subject.Subject` the parser read, in place
       of a slot dict and a subject read here for a caller with none.
    """
    if _named_player_in(scope) or _PERIOD.search(question) or _RANKING.search(question) or _LOG.search(question) or _TEAM_NOT_SUBJECT.search(question):
        return None
    if subject.kind == "team_players":
        # A team's players ("top scorers on the Lakers") are a ranking of
        # players narrowed by team, never the team's own figure.
        return None
    team_text = scope.team
    if not (isinstance(team_text, str) and team_text.strip()) or team_text == "any_team":
        # The router dropped the team (F127's shape): the subject reading
        # has it, from the question's own team word.
        if subject.kind not in ("team", "team_players") or not subject.teams:
            return None
        scope = replace(scope, team=subject.teams[0])
    measure = _team_measure(scope, question)
    if measure is None:
        return None
    return Reading(scope=scope, shape="scalar", measures=[measure], aggregate="total", relation="team")


def team_move_point(con: duckdb.DuckDBPyConnection, scope: Scope, question: str, subject: Subject) -> TeamQuery | None:
    """:func:`team_read_point`, planned - the team's point as the team
    compiler runs it, or ``None`` where the team is not the subject.

    .. versionchanged:: 5.0.0
       Plans :func:`team_read_point`'s :class:`~association.query.reading.Reading`,
       over the typed :class:`~association.query.reading.Scope` and the
       :class:`~association.query.subject.Subject` the parser read.
    """
    reading = team_read_point(con, scope, question, subject)
    if reading is None:
        return None
    query = plan(reading)
    assert isinstance(query, TeamQuery)
    return query


def read_point(con: duckdb.DuckDBPyConnection, reading: Reading, question: str) -> Reading:
    """``reading``'s intent's default point, moved by ``question``'s own words: a
    measure beyond a template's list, a skeleton move ("most ... in a game" =
    rows by measure; "how many ... won" = count with a predicate) - none of it
    through a prompt edit. A team named with no player
    (:func:`team_move_point`) is tried before the league-wide reading, since a
    team's own total or differential is a narrower, more specific claim than
    "no player subject" - the same priority a named player already gets over
    the league-wide read. Called by the parser, once
    (:func:`~association.query.parse.reading_from_route`); the compiler plans
    and runs the Reading it returns.

    .. versionchanged:: 4.4.0
       Tries :func:`team_move_point` (the team as a subject) before the
       league-wide reading. May return a
       :class:`~association.query.compose.team.TeamQuery` instead of a
       :class:`~association.query.compose.core.Query`.

    .. versionchanged:: 5.0.0
       Takes the :class:`~association.query.reading.Reading` the parser
       settled - its intent, its typed scope and the subject it read - in
       place of an intent, a slot dict and a subject read here for a caller
       with none (ROADMAP plan item 6, step (f)); the position group, the
       team and the subject's kind are read off that subject.

    .. versionchanged:: 5.0.0
       Returns the :class:`~association.query.reading.Reading` (the record of
       what was read) rather than the planned query;
       :func:`~association.query.compose.plan.plan` turns it into the query.

    .. versionchanged:: 5.0.0
       Repairs no slot: the position phrase, the filler word or team in
       ``player``, the dropped subject and the opponent player it once
       rewrote never reach it from the parser (ROADMAP plan item 6, step (e)).
    """
    subject = reading.subject
    if subject is None:
        # Who the question is about is read once, by the parser; a Reading
        # with none is a caller's mistake, said here rather than as an
        # AttributeError three moves down.
        raise ValueError("read_point needs the reading's subject - who the question is about, as the parser read it")
    point = _read_point(con, reading.intent, reading.scope, question, subject)
    return replace(point, intent=reading.intent, subject=subject, evidence=(*point.evidence, *subject.evidence))


def _leaderboard_declines(scope: Scope, subject: Subject) -> None:
    """What a ``leaderboard`` point is not, declined before any move - split
    out of :func:`_read_point` to keep it inside the complexity gate."""
    if scope.stat in ("triple_double", "double_double") and subject.kind in ("team", "team_players") and not subject.players:
        # A TEAM's total of its players' triple-doubles ("oklahoma city
        # thunder all-time triple doubles vs west", leaderboard with the team
        # filed - day5): not a ranking this relation lacks a measure for, but
        # a team aggregate nothing reads. Declined here, where the subject
        # is in hand (`repair` reads "vs west" as the team's opponent below),
        # so the refusals module names that cause
        # (refusals._team_boolean_count) rather than the ranking's sentence
        # naming the wrong one.
        raise Unsupported(f"a team's total of its players' {scope.stat} is not read")
    if _named_player_in(scope):
        # A leaderboard ranks the league or a team, never one named person -
        # the retired template's own refusal ("Klay Thompson's 3pt percentage
        # over the past 4 seasons" once landed here and came back with the
        # league's true-shooting leaders, Klay silently dropped).
        raise Unsupported(f"a leaderboard cannot answer about one named player ({scope.player!r})")


def _read_point(con: duckdb.DuckDBPyConnection, intent: str, scope: Scope, question: str, subject: Subject) -> Reading:
    """:func:`read_point`'s moves, in order; split out so the record carries
    the intent and the subject whichever move settled it."""
    if intent == "leaderboard":
        _leaderboard_declines(scope, subject)
    if intent == "record_when" and not _named_player_in(scope) and scope.team is not None and scope.team.strip():
        # A team's record above and below its OWN line - "what was the celtics
        # record when they scored 120 points" (ISSUES.md #144) - is neither the
        # season sum nor the window sum the team subject otherwise reads: it is
        # record_when's team reader's (compose.present.present_team).
        return Reading(scope=scope, shape="scalar", measures=[scope.stat or "points"], aggregate="record", relation="team")
    if intent == "game_log":
        # A team's log, before the team's sums: "knicks last 5 games" lists
        # them (the retired template's team half, ROADMAP plan item 6, step
        # (g)); "total points scored by the raptors in the last 10 games" is
        # routed here too and lists them with the total stated beneath, as
        # the template did.
        team_log = team_log_point(scope, subject)
        if team_log is not None:
            return team_log
    if intent == "player_splits":
        team_splits = team_splits_point(scope, subject)
        if team_splits is not None:
            return team_splits
    if intent == "player_compare":
        return _compare_point(scope)
    if intent in ("streak", "player_matchup", "with_without"):
        # The retired templates read their slots alone, so no word moves the
        # point: a player's, a team's or the league's longest run
        # (compose.adapt._adapt_streak), two players' meetings
        # (_adapt_player_matchup), or a team's record with and without a
        # teammate (_adapt_with_without).
        return _to_reading_scope(intent, scope)
    if not _named_player_in(scope):
        team_reading = team_read_point(con, scope, question, subject)
        if team_reading is not None:
            return team_reading
    # The parser's own scope: nothing here repairs it (the filler word, the
    # team in `player`, the dropped subject and the opponent player never
    # reach here from the parser - measured over the 628 questions with a
    # recorded normalizer reply).
    if not _named_player_in(scope):
        if intent == "record_when":
            # A record "when" is a player's line or a team's own (the team
            # branch above); naming neither, it has nobody to read - the
            # reason record_when's retired template gave.
            raise Unsupported("record_when needs a player or a team")
        return _everyone_point(intent, scope, question, _stat_measure(scope.stat), subject.position)
    return _move_named(intent, scope, question)
