"""The point reader: the question's own words, read in code - the way
``route()``'s ``CODE_ASSIGNED_INTENTS`` and its ``_validate_*`` helpers read
them, never through a prompt - move the measure, the skeleton or the
aggregate off the intent's default point
(:func:`default_point`; until Phase 2 deleted the adapters it was read on
the answer side, ``compose.adapt``, this module's one reach there).
:func:`read_point` is the parser's last step
(``parse.with_point``): it writes the point into the Reading, or why there
is none - a decline (:class:`~association.query.reading.Unsupported`) or a
refusal's cause (:class:`~association.query.reading.PointRefused`) the
planner says. Nothing here plans, runs or words an answer.

.. versionadded:: 4.4.0

.. versionchanged:: 5.0.0
   ``query/point.py``, on the reader's side, where it was
   ``compose/move.py`` (``ROADMAP.md``, Phase 1, the ``read_point`` move,
   step 5); ``games_reading`` went to the planner and ``team_move_point``
   to the test that used it.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import replace
from typing import TYPE_CHECKING, Any, Literal

from association.nba.season import current_season
from association.query import lexicon
from association.query.entities import BOX_SCORES, SHOT_AVAILABILITY
from association.query.lines import measure_filters, relation_lines, threshold_count_line, threshold_line, threshold_of
from association.query.measures import (
    BOOLEAN_MEASURES,
    DERIVED_LINES,
    HISTORY_STATS,
    LINE,
    SPLIT_LINE,
    STAT_LINE,
    TEAM_GAME_MEASURES,
    TEAM_SEASON_MEASURES,
    WORD_MEASURES,
    log_extras,
    period_split_measure,
    resolve_metric,
    stat_column,
    stat_measure,
    streak_column,
)
from association.query.metrics import PER_GAME_MIN_GAMES, TEAM_FIELD_WORDS
from association.query.reading import (
    CHART_INTENTS,
    DEFAULT_GAME_LOG_LIMIT,
    DEFAULT_LIMIT,
    DEFAULT_NAMED_RUNS,
    DEFAULT_SINGLE_GAME_LIMIT,
    DEFAULT_STREAK_LIMIT,
    TEAM_ONLY_INTENTS,
    Aggregate,
    Cause,
    Group,
    PointRefused,
    PointShape,
    Reading,
    Scope,
    Shape,
    Unsupported,
    _career_scope,
    _clamp_limit,
    period_narrowing,
    scope_reads_box_scores,
)
from association.query.reading import named_player_in as _named_player_in
from association.query.subject import with_without_named

if TYPE_CHECKING:
    from association.query.subject import Subject

_TOP_IN_A_GAME = re.compile(r"\b(most|highest|best|career[- ]high|record)\b.*\b(in a\b.*\bgame|single[- ]game|career[- ]high)\b|\bcareer[- ]high\b", re.I)
# Not this relation's question: a team as the subject, an opponent's or
# allowed figure, or a period. Refused, never answered with a ranking of
# players - "least points by the Wizards in the first half" ranked players
# by fewest points until this existed.
_NOT_PLAYERS = re.compile(r"\bby team\b|\bteams?\b|\ballowed\b|\bopponent'?s?\b|\bbench points\b|\bfranchise\b", re.I)
# The period words are the lexicon's (PERIOD_GUARD) since Phase 3, step 2.
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

#: What one row of a ``rows`` point a shared move reads is said BY, under
#: the words it was read under: a log's and a line's games by date
#: (``compose.logs``, which the retired ``player_stat`` handed a single game
#: to), a single game's high by the measure, a count's games by the count
#: (``compose.counts``); ``""`` under any other words - no reader takes the
#: point, and the compiler's own sentence states what it lists. The
#: grammar names the shape itself once ``intent`` leaves the reader (Phase
#: 3, step 4); until then the move is read under the retired words.
_ROWS_BY: dict[str, str] = {"game_log": "date", "player_stat": "date", "single_game_high": "measure", "threshold_count": "count"}
#: The same for a count a shared move reads ("how many ... won", a boolean
#: measure counted): his line (``compose.stats``), his count
#: (``compose.counts``), or - under ``record_when``'s words - the record
#: over a line the count is said as (``compose.records``, a split by the
#: line); a scalar nothing reads otherwise.
_COUNT_SHAPES: dict[str, tuple[Shape, str]] = {"player_stat": ("scalar", "line"), "threshold_count": ("scalar", "count"), "record_when": ("split", "line")}


def _measure_words(question: str) -> list[str]:
    """Every measure :data:`WORD_MEASURES` finds named in ``question``, in list order."""
    found: list[str] = []
    ql = question.lower()
    for pattern, name in WORD_MEASURES:
        if re.search(pattern, ql):
            found.append(name)
    return found


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
    if lexicon.PERIOD_GUARD.search(question) and not period_is_condition:
        raise Unsupported("a quarter or half is the period relation's question")
    # "the top 50 ... with the team they play for" (F017) names no team's
    # question: the team is a column the ranking shows.
    if _NOT_PLAYERS.search(TEAM_FIELD_WORDS.sub(" ", question)) and not position:
        raise Unsupported("a team, an opponent's figure or a franchise is the team relation's question")
    if intent in TEAM_ONLY_INTENTS:
        raise Unsupported("a team's own question is not the player relation's")


def _everyone_opponent(scope: Scope, question: str) -> Scope:
    """A team beside no player, with "vs"/"against", is the opponent."""
    if scope.team is not None and scope.team.strip() and not scope.cuts.opponent and _VS.search(question):
        return replace(scope, cuts=replace(scope.cuts, opponent=scope.team), team=None)
    return scope


def _everyone_threshold_predicates(scope: Scope, measure: str | None, predicates: list[tuple[str, str, Any]]) -> list[tuple[str, str, Any]]:
    """The line the threshold grammar read ("a 30 point triple double game"
    is points >= 30, whatever stat the model filed beside the number): its
    own measure, read from the number's words; the model's stat only where
    a line was built with no words of its own (a slot door's). Until Phase
    3, step 2 this re-read the number's words from the question, and its
    pattern could not pass a comma ("most games with 20 pt,s 10 reb, 5
    ast" fell back to the model's "rebounds")."""
    line = threshold_line(scope)
    if line is None or int(line.value) <= 0:
        return predicates
    column = line.measure or stat_measure(scope.stat)
    if column and column != measure:
        return [*predicates, (column, ">=", int(line.value))]
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
        by=_ROWS_BY.get(intent, ""),
        measures=[measure, *(m for m in LINE if m != measure)],
        aggregate="none",
        group="none",
        predicates=predicates,
        order="measure",
        direction=_asc_or_desc(question),
        limit=_clamp_limit(scope.window.count, DEFAULT_SINGLE_GAME_LIMIT),
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


def _everyone_boolean_game_ranking(intent: str, question: str, scope: Scope, predicates: list[tuple[str, str, Any]], position: str | None) -> Reading | None:
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
    sized = _BOOLEAN_GAME_RANKING.search(question) or scope.window.by or any(re.search(pattern, question, re.I) for pattern, _ in _BOOLEAN_RANK_WORDS)
    if not sized:
        return None
    measure = _boolean_game_measure(question)
    return Reading(
        scope=scope,
        shape="rows",
        by=_ROWS_BY.get(intent, ""),
        measures=[measure, *(m for m in LINE if m != measure)],
        aggregate="none",
        group="none",
        predicates=predicates,
        order="measure",
        direction=_asc_or_desc(question),
        limit=_clamp_limit(scope.window.count, DEFAULT_SINGLE_GAME_LIMIT),
        relation="everyone",
        position=position,
    )


def _numbered_lines(scope: Scope) -> list[tuple[str, str, Any]]:
    """Every "<N> <stat>" line the question itself names, as predicates - the
    league-wide multi-line count a single ``threshold`` slot could not carry
    (F161: "33 point and 13 rebound and 10 assist 2 blocks and 2 steals"):
    the subject's whole-game lines at or above a number, each column once.
    Until Phase 3, step 2 this re-read every number's own trailing words
    from the question (``_NUMBER_STAT``); the typed lines are the one
    reading of them.

    .. versionadded:: 4.4.0
    """
    predicates: list[tuple[str, str, Any]] = []
    seen: set[str] = set()
    for line in scope.lines:
        if line.period is not None or line.op != ">=" or line.measure is None or int(line.value) < 1 or line.measure in seen:
            continue
        seen.add(line.measure)
        predicates.append(line.as_predicate())
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
    text_lines = _numbered_lines(scope)
    lines = text_lines if len(text_lines) > len(predicates) else predicates
    if len(lines) < 2:
        return None
    return Reading(
        scope=scope,
        shape="rows",
        by="count",
        measures=[name for name, _, _ in lines],
        aggregate="none",
        group="none",
        predicates=lines,
        order="date",
        direction="desc",
        limit=_clamp_limit(scope.window.count, 25),
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
    column = stat_measure(scope.stat)
    threshold = threshold_of(scope)
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
        threshold = threshold_of(scope)
        if threshold is not None and threshold < 1:
            # threshold_count's own refusal: measured, "most 3 pointers made
            # since 2020" arrived as threshold 0 and would count every game.
            raise PointRefused(Cause(kind="threshold_counts_every_game", facts={"intent": "threshold_count", "threshold": threshold}))
        predicates = _everyone_threshold_count_line(scope)
    if not predicates:
        raise PointRefused(Cause(kind="needs_line"))
    if scope.cuts.season_n:
        # The refusal threshold_count's retired template gave:
        # "his 15th season" is a place in one career, and the league has none -
        # read over everyone it narrowed to players in their 15th season of the
        # default year while the sentence named only the year.
        raise PointRefused(Cause(kind="career_place_needs_player", facts={"season_n": scope.cuts.season_n}))
    # threshold_count's own leaderboard length (DEFAULT_LIMIT, five names)
    # where it is that intent's question; ten for a team's roster count
    # (F152), which also states the whole count beneath the ones listed.
    listed = DEFAULT_LIMIT if intent == "threshold_count" else 10
    # A count's ranking (compose.counts) under its own words; a team's
    # roster count under any other is a ranking by player no reader takes.
    return Reading(
        scope=scope,
        shape="ranking",
        by="count" if intent == "threshold_count" else "player",
        measures=[],
        aggregate="count",
        group="player",
        predicates=predicates,
        order="measure",
        direction="desc",
        limit=_clamp_limit(scope.window.count, listed),
        relation="everyone",
        position=position,
    )


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
       (:func:`~association.query.measures.stat_measure`) - and only the second is a refusal; the
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
            raise PointRefused(Cause(kind="shot_distance_ranking"))
        if stat is not None and stat.strip():
            raise PointRefused(Cause(kind="no_ranking_measure", facts={"stat": stat}))
    aggregate: Aggregate = "total" if _TOTAL.search(question) else "per_game"
    minimum_games = PER_GAME_MIN_GAMES
    named_minimum = _ranking_minimum(question)
    if named_minimum is not None:
        unit, count = named_minimum
        if not unit.startswith("game"):
            # A refusal, not a decline: nothing downstream reads an attempts or
            # minutes floor either, and "None" here sent yardstick-v2 F056 to
            # the agent for a minute. The planner's sentence names the floor
            # that IS applied so the question can be re-asked with it.
            raise PointRefused(Cause(kind="ranking_floor_unit", facts={"unit": unit, "count": count}))
        minimum_games = count
    # The game-level ranking by player, which no reader takes: the
    # compiler's own ranking and sentence.
    return Reading(
        scope=scope,
        shape="ranking",
        by="player",
        measures=[measure or "points"],
        aggregate=aggregate,
        group="player",
        predicates=predicates,
        order="measure",
        direction=_asc_or_desc(question),
        limit=_clamp_limit(scope.window.count, 10),
        minimum_games=minimum_games,
        relation="everyone",
        position=position,
    )


def _leaderboard_season_line(intent: str, scope: Scope, question: str, measure: str | None, predicates: list[tuple[str, str, Any]], position: str | None) -> Reading | None:
    """``leaderboard``'s own point (its template retired, ROADMAP plan item
    6, step (g)): a ranking of the league or a team over the SEASON LINE -
    ``run_leaderboard``'s pool, floors, traded-player dedup and NetPoints
    tables - read by ``compose.rankings.read_leaderboard`` and said by the sayer,
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
    metric = resolve_metric(scope.stat, career=scope.span.career)
    if metric is None:
        return None
    if measure is not None and stat_measure(scope.stat) not in (None, measure):
        return None
    if scope.rate is not None and scope.rate != "total":
        # A unit the metric has no form of ("who were the top 10 in
        # defensive netpoints / 90"): the ranking's refusal, naming the
        # forms THIS metric has (the planner's sentence) - "total" is a
        # season total, the one other form every metric's rate reads.
        raise PointRefused(Cause(kind="ranking_unit", facts={"metric": metric, "rate": scope.rate}))
    return Reading(
        scope=scope,
        shape="ranking",
        by="player",
        on="player_seasons",
        measures=[measure or "points"],
        aggregate="per_game",
        group="player",
        predicates=[],
        order="measure",
        direction=_asc_or_desc(question),
        limit=_clamp_limit(scope.window.count, 10),
        minimum_games=PER_GAME_MIN_GAMES,
        relation="everyone",
    )


def _everyone_position_log(intent: str, scope: Scope, question: str, position: str | None) -> Reading | None:
    """A log word with a position: rows, over that position group."""
    if not (position and _LOG.search(question)):
        return None
    return Reading(
        scope=scope,
        shape="rows",
        by=_ROWS_BY.get(intent, ""),
        measures=list(LINE),
        aggregate="none",
        group="none",
        predicates=[],
        order="date",
        direction="desc",
        limit=_clamp_limit(scope.window.count, 10),
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
       Tries :func:`_everyone_boolean_game_ranking` (#199) and
       :func:`_everyone_multi_line_games` (F161) before the per-player count
       and ranking moves, since both are more specific readings of a
       ``threshold_count``/ranking question than either of those.
    """
    _everyone_guard(intent, question, position, period_is_condition=any(line.period is not None for line in scope.lines))
    scope = _everyone_opponent(scope, question)
    words = _measure_words(question)
    measure, predicates = _measure_and_predicates(words, measure if measure not in BOOLEAN_MEASURES else None)
    predicates = _everyone_threshold_predicates(scope, measure, predicates)
    single = _everyone_single_game(intent, scope, question, measure, predicates, position)
    if single is not None:
        return single
    boolean_ranked = _everyone_boolean_game_ranking(intent, question, scope, predicates, position)
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
    logged = _everyone_position_log(intent, scope, question, position)
    if logged is not None:
        return logged
    raise Unsupported("no player subject and no ranking or position-group reading of the question")


def _measure_for_named(scope: Scope, question: str) -> str | None:
    """The measure a named-player question moves to: the question's own word first, the router's stat otherwise."""
    words = _measure_words(question)
    stat = stat_measure(scope.stat)
    return words[0] if words else stat


def _move_single_game(intent: str, scope: Scope, question: str, measure: str | None) -> Reading | None:
    """ "Most ... in a game" for a named player: rows by measure - and
    "most points by curry vs lebron", with a player on the other side of
    the games (ROADMAP step 3): against a named opponent, "most" is his best
    meeting, the question a pair's summary never answered."""
    if not measure or measure in BOOLEAN_MEASURES:
        return None
    against = any(c.side == "opponent" for c in scope.companions)
    if not (_TOP_IN_A_GAME.search(question) or (against and _RANKING.search(question))):
        return None
    return Reading(
        scope=scope,
        shape="rows",
        by=_ROWS_BY.get(intent, ""),
        measures=[measure, *(m for m in LINE if m != measure)],
        aggregate="none",
        group="none",
        predicates=[],
        order="measure",
        direction=_asc_or_desc(question),
        limit=_clamp_limit(scope.window.count, DEFAULT_SINGLE_GAME_LIMIT),
    )


def _move_how_many_won(scope: Scope, question: str, measure: str | None, intent: str, career: Scope) -> Reading | None:
    """ "How many ... has he won" - a count with the ``won`` predicate."""
    if not (_HOW_MANY_OR_OFTEN.search(question) and _WON.search(question) and (measure in (None, "won", "points") or intent in ("record_when", "threshold_count"))):
        return None
    shape, by = _COUNT_SHAPES.get(intent, ("scalar", ""))
    return Reading(scope=career, shape=shape, by=by, measures=[], aggregate="count", group="none", predicates=[("won", "=", True)])


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
    shape, by = _COUNT_SHAPES.get(intent, ("scalar", ""))
    return Reading(scope=career, shape=shape, by=by, measures=[], aggregate="count", group="none", predicates=[(measure, "=", True)])


def _move_boolean_count_is_line(measure: str, scope: Scope) -> bool:
    """Whether a boolean measure is, by its one definition
    (:data:`~association.query.measures.DERIVED_LINES`), exactly the router's
    own ``stat``/``threshold`` line (``fouled_out`` is ``fouls >= 6``)."""
    column = stat_measure(scope.stat)
    threshold = threshold_of(scope)
    if column is None or threshold is None:
        return False
    return DERIVED_LINES.get(measure) == (column, threshold)


def _move_player_history(intent: str, scope: Scope, career: Scope, measure: str | None) -> Reading | None:
    """``player_history``: a per-season history, read from the season line
    (``on="player_seasons"``) over the question's own slots - the template's own
    read. Where the season line does not say it, the planner plans it as a
    career of games grouped by season, newest first (``compose.plan.plan``)."""
    if intent != "player_history":
        return None
    if measure is None and scope.stat is not None and scope.stat not in HISTORY_STATS:
        # A stat named that neither the season line nor the games carry
        # ("shot_distance"): refused, as player_history's retired template
        # refused it - never drawn as the points history the default measure
        # below would read in its place.
        raise Unsupported(f"no per-season history for stat {scope.stat!r}")
    del career  # the season line reads the question's own span; the planner's game-level point widens it
    return Reading(
        scope=scope,
        shape="split",
        by="season",
        on="player_seasons",
        measures=[measure or "points"],
        aggregate="per_game",
        group="season",
        predicates=[],
        order="date",
        direction="desc",
        limit=_clamp_limit(scope.window.count, 10),
    )


def _compare_point(scope: Scope) -> Reading:
    """``player_compare``'s own point (its template retired, ROADMAP plan
    item 6, step (g)): two or more named players' season lines side by side
    (``on="player_seasons"``, grouped by player), read by
    ``compose.seasons.read_player_compare`` and said by the sayer. The template's own
    refusal is the point's: fewer than two distinct names. A narrowing -
    it honors none - is the planner's to decline
    (:func:`~association.query.compose.plan._shape_declines`).

    .. versionadded:: 5.0.0
    """
    if len({name for name in scope.players if name.strip()}) < 2:
        raise Unsupported("player_compare needs at least two distinct player names")
    return Reading(scope=scope, shape="comparison", by="subject", on="player_seasons", measures=[], aggregate="per_game", group="player", predicates=[])


# --- the default points -----------------------------------------------------------
#
# The point a bare intent means before any of the question's own words move
# it, for the shapes Phase 2 has ported (``ROADMAP.md``, "Phase 2, the
# expected steps", step 1): read from the Scope alone, on the reader's side.
# Every default point is here since the last adapter went (step 3).


def _default_game_log(scope: Scope) -> Reading:
    """``game_log``'s default point: the newest games, in date order. A REAL
    stat neither the log's columns nor the relation carries is declined, as
    the retired template refused it (:func:`~association.query.measures.log_extras`):
    "luka shot distance log" listed without it would be the narrower answer
    passed off as the one asked for. A ``threshold`` on a log is the line it
    keeps games past ("games with 15+ fga"), on the stat's column - one that
    is a below/above phrase's own number (the model files it twice) is that
    phrase, and one beside no stat has no column to keep a line on. A date
    names its game outright, so the span is the career and the season is the
    date's."""
    if not _named_player_in(scope):
        raise Unsupported("a team's log is the team relation's")
    if scope.stat and stat_column(scope.stat) is None:
        log_extras(scope.stat)
    date = scope.cuts.date
    return Reading(
        scope=scope,
        shape="rows",
        by="date",
        measures=list(LINE),
        aggregate="none",
        group="none",
        predicates=_threshold_line(scope),
        order="date",
        direction="asc" if scope.window.order == "first" else "desc",
        limit=_clamp_limit(scope.window.count, DEFAULT_GAME_LOG_LIMIT),
        subject_span=scope.span.over_career() if date else scope.span,
    )


def _threshold_line(scope: Scope) -> list[tuple[str, str, Any]]:
    """A ``threshold`` as the line a log keeps games past, or nothing where
    it is a below/above phrase's own number; a threshold beside no stat is
    refused for the stat."""
    threshold = threshold_of(scope)
    if threshold is None or any(line.value == threshold for line in measure_filters(scope)):
        return []
    col = stat_column(scope.stat)
    if col is None:
        raise PointRefused(Cause(kind="threshold_needs_stat", facts={"intent": "game_log", "threshold": threshold}))
    return [(col, ">=", threshold)]


def _default_player_stat(scope: Scope) -> Reading:
    """``player_stat``'s default point: a per-game average over box scores
    where a narrowing (or a date) sends the read there
    (:func:`~association.query.reading.scope_reads_box_scores`), and the
    season line (``on="player_seasons"``) for an unnarrowed season or career. A
    window ("Jokic averages last 10 games") is the log of exactly those
    games with averages beneath - the shape the question has - so the point
    is ``game_log``'s."""
    if not _named_player_in(scope):
        raise Unsupported("player_stat needs a player")
    col = stat_column(scope.stat)
    measures = [col] if col else list(STAT_LINE)
    if scope.window.count or scope.window.order:
        return _default_game_log(scope)
    if not (scope_reads_box_scores(scope, measure_filters(scope)) or scope.cuts.date):
        return Reading(scope=scope, shape="scalar", by="line", on="player_seasons", measures=measures, aggregate="per_game", group="none", predicates=[])
    date = scope.cuts.date
    return Reading(
        scope=scope,
        shape="scalar",
        by="line",
        measures=measures,
        aggregate="per_game",
        group="none",
        predicates=[],
        subject_span=scope.span.over_career() if date else scope.span,
    )


def _default_player_splits(scope: Scope) -> Reading:
    """``player_splits``'s default point: a record by venue, or by starter and
    bench. A named half ("as a starter") narrows the games (the relation's own
    ``started``) while the category shown is still starter/bench; by venue it
    read the half's games split by home/away instead."""
    if not _named_player_in(scope):
        raise Unsupported("a team's splits are the team relation's")
    group: Group = "starter" if scope.split in ("starter_bench", "starter", "bench") else "venue"
    return Reading(scope=scope, shape="split", by="splits", measures=list(SPLIT_LINE), aggregate="record", group=group, predicates=[], available=BOX_SCORES)


def _default_record_when(scope: Scope) -> Reading:
    """``record_when``'s default point: the record in games clearing one line.
    A line of 0 is every game he played: never a record "when". Refused by
    the fact missing - the stat, a stat with no per-game column, the number,
    or a number every game clears - where it named one line only in part."""
    col = stat_column(scope.stat)
    threshold = threshold_of(scope)
    if not _named_player_in(scope):
        # read_point reads a named player's moves only; a caller's mistake.
        raise Unsupported("record_when needs a player, a stat and a positive threshold here")
    if col is None and scope.stat and scope.stat.strip():
        raise PointRefused(Cause(kind="unknown_stat", facts={"intent": "record_when", "stat": scope.stat}))
    if col is None:
        raise PointRefused(
            Cause(kind="threshold_needs_stat", facts={"intent": "record_when", "threshold": threshold}) if threshold is not None else Cause(kind="needs_stat", facts={"intent": "record_when"})
        )
    if threshold is None:
        raise PointRefused(Cause(kind="needs_threshold", facts={"intent": "record_when", "stat": col}))
    if threshold < 1:
        raise PointRefused(Cause(kind="threshold_counts_every_game", facts={"intent": "record_when", "threshold": threshold}))
    # A record over a line: said as a split by the line (compose.records),
    # compiled as the scalar record above and below it.
    return Reading(scope=scope, shape="split", by="line", measures=[], aggregate="record", group="none", predicates=[(col, ">=", threshold)], available=BOX_SCORES)


def _default_period_split(scope: Scope) -> Reading:
    """``period_split``'s default point: a named player's games in date
    order, each read as the quarter's or half's line (the relation's
    ``period``/``half`` cells), measuring the column the period's line
    rebuilds (:func:`~association.query.measures.period_split_measure`) -
    points where no stat was named - or, with no period named, his four
    quarters side by side, a ``grouped`` read by ``period`` (#162). The
    player is settled over the shot table, as the template settled him,
    and over his career when a date names the game."""
    if not _named_player_in(scope):
        raise Unsupported("period_split needs a player")
    measure = period_split_measure(scope.stat)
    date = scope.cuts.date
    if period_narrowing(scope) is None:
        return Reading(
            scope=scope,
            shape="split",
            by="period",
            on="player_periods",
            measures=[measure],
            aggregate="per_game",
            group="period",
            predicates=[],
            order="date",
            direction="asc",
            limit=None,
            available=SHOT_AVAILABILITY,
            subject_span=scope.span.over_career() if date else scope.span,
        )
    return Reading(
        scope=scope,
        shape="rows",
        by="date",
        on="player_periods",
        measures=[measure],
        aggregate="none",
        group="none",
        predicates=[],
        order="date",
        direction="asc" if scope.window.order == "first" else "desc",
        limit=_clamp_limit(scope.window.count, DEFAULT_GAME_LOG_LIMIT),
        available=SHOT_AVAILABILITY,
        subject_span=scope.span.over_career() if date else scope.span,
    )


def _default_threshold_count(scope: Scope) -> Reading:
    """``threshold_count``'s default point: a count of a named player's games
    clearing one line - the threshold, or a below/above phrase that is the
    whole line ("Sga games with under 14 fta": the relation narrows by it,
    and the count is of the games left), read the one way the count's
    reader reads it (:func:`~association.query.lines.threshold_count_line`).
    A league-wide count is declined here; the point reader's own move reads
    it as a count by player."""
    col = stat_column(scope.stat)
    threshold = threshold_of(scope)
    if not _named_player_in(scope):
        raise Unsupported("a league-wide count is not on the one-player relation")
    if threshold is None and relation_lines(scope):
        # Refuses (PointRefused) by the fact missing: a phrase naming no
        # stat, a stat with no per-game column.
        threshold_count_line(scope)
        return Reading(scope=scope, shape="scalar", by="count", measures=[], aggregate="count", group="none", predicates=[], available=BOX_SCORES)
    if col is None or threshold is None or threshold < 1:
        # The reason the count gives, where it has one (a threshold of 0
        # counts every game; no stat it keeps a line on), by its cause.
        threshold_count_line(scope)
        raise Unsupported("threshold_count refuses; nothing to compare")
    # A below/above phrase carrying the threshold's own number IS the count,
    # misread as a threshold (the count's sayer words it as the phrase).
    lines = [line.as_typed for line in relation_lines(scope)]
    predicates = [] if any(str(threshold) in line for line in lines) else [(col, ">=", threshold)]
    return Reading(scope=scope, shape="scalar", by="count", measures=[], aggregate="count", group="none", predicates=predicates, available=BOX_SCORES)


def _default_single_game_high(scope: Scope) -> Reading:
    """``single_game_high``'s default point: a named player's top games by
    one stat, the measure first. A league-wide high is the point reader's
    own move (a ranking of games), declined here. The decline names the
    fact that is missing - the stat, or the player - never both where one
    was given: "brice sensabaugh career high asistss" named its player."""
    col = stat_column(scope.stat)
    if col is None:
        raise PointRefused(Cause(kind="unknown_stat", facts={"intent": "single_game_high", "stat": scope.stat}) if scope.stat else Cause(kind="needs_stat", facts={"intent": "single_game_high"}))
    if not _named_player_in(scope):
        raise Unsupported("single_game_high needs a named player here")
    return Reading(
        scope=scope,
        shape="rows",
        by="measure",
        measures=[col],
        aggregate="none",
        group="none",
        predicates=[],
        order="measure",
        direction="desc",
        limit=_clamp_limit(scope.window.count, DEFAULT_SINGLE_GAME_LIMIT),
    )


def _streak_season(scope: Scope) -> int | None:
    """The season a named player's run is settled in, ``None`` for every
    season - the season the relation's own span for a run reads
    (``compose.core.run_scope``): ``since`` is every season from that one
    on, and refused beside a named season rather than silently preferring
    one; an ordinal season ("his 5th season") is read over his career; no
    season is this one, except for a career."""
    span = scope.span
    if span.since:
        if span.season:
            raise Unsupported(f"since {span.since} and the {span.season} season at once")
        return None
    if span.season is not None:
        return span.season
    return None if scope.cuts.season_n or span.career else current_season()


def _default_streak(scope: Scope) -> Reading:
    """``streak``'s default point: the longest run of consecutive games
    meeting one condition (the ``run`` shape), on the relation the question
    names. A named player's is over his games (a stat at or above its
    threshold, or his team's wins in games he played); a named team's is
    its own run of wins or losses within a season, on the team relation;
    nobody named is the league's - each player's or each team-season's own
    longest, the count asked for or :data:`~association.query.reading.DEFAULT_STREAK_LIMIT`.
    A stat with no threshold or a threshold with no stat is declined
    (:func:`~association.query.measures.streak_column`), and so is a team's
    run of a stat; the narrowings a run cannot take are the planner's
    (:func:`~association.query.compose.plan._shape_declines`).

    .. versionadded:: 5.0.0
       On the reader's side (``compose.adapt._adapt_streak`` was this).
    """
    threshold = threshold_of(scope)
    column = streak_column(scope.stat, threshold)
    want_win = scope.kind != "loss"
    predicates: list[tuple[str, str, Any]] = [(column, ">=", threshold)] if column is not None else [("won", "=", want_win)]
    if _named_player_in(scope):
        season = _streak_season(scope)
        return Reading(
            scope=scope,
            shape="runs",
            by="line",
            measures=[],
            aggregate="none",
            group="none",
            predicates=predicates,
            limit=DEFAULT_NAMED_RUNS,
            available=BOX_SCORES,
            # The run's season settled outright: this one, never a
            # defaulted one, so an empty run is refused rather than
            # redirected to his last games.
            subject_span=replace(scope.span, season=season, career=season is None),
        )
    if scope.team and scope.team.strip():
        if column is not None:
            raise PointRefused(Cause(kind="team_streak_of_stat", facts={"stat": column}))
        return Reading(scope=scope, shape="runs", by="won", on="team_games", measures=["won"], aggregate="count", group="none", predicates=predicates, relation="team")
    limit = _clamp_limit(scope.window.count, DEFAULT_STREAK_LIMIT)
    if column is not None:
        return Reading(scope=scope, shape="runs", by="line", measures=[], aggregate="none", group="none", predicates=predicates, limit=limit, relation="everyone")
    return Reading(scope=scope, shape="runs", by="won", on="team_games", measures=["won"], aggregate="count", group="none", predicates=predicates, limit=limit, relation="team")


def _default_player_matchup(scope: Scope) -> Reading:
    """``player_matchup``'s default point: two named players' lines over the
    games they met in, on opposite teams (the ``pair`` shape), the names
    settled over the box scores, and over the career when a date names the
    game (a date replaces the season, the way ``game_log``'s own does).
    Fewer or more than two names is declined; the narrowings a matchup
    cannot take are the planner's, and two names that resolve to one
    person are declined as the pair is settled (``compose.core._resolve_pair``).

    .. versionadded:: 5.0.0
       On the reader's side (``compose.adapt._adapt_player_matchup`` was this).
    """
    texts = list(dict.fromkeys(n.strip() for n in [*scope.players, scope.player] if n is not None and n.strip()))
    if len(texts) != 2:
        raise PointRefused(Cause(kind="matchup_needs_two", facts={"names": texts}))
    dated = bool(scope.cuts.date)
    return Reading(
        scope=scope,
        shape="comparison",
        by="met",
        measures=[],
        aggregate="none",
        group="none",
        predicates=[],
        available=BOX_SCORES,
        subject_span=scope.span.over_career() if dated else scope.span,
    )


def _default_with_without(scope: Scope) -> Reading:
    """``with_without``'s default point: a team's record in the games named
    teammates played against the games they missed - the team relation's
    ``presence`` group (``compose.team.compile_team_presence``) - with the
    subject's averages in each where a player is named. The retired
    template's own early refusal is the point's (ROADMAP plan item 6, step
    (g)): no teammate to divide by - the teammates come from ``without`` or
    ``with_player``, a ``conditions`` role
    (:func:`~association.query.subject.with_without_named`), or failing those
    the one name beside a team or the second of two; more than that is
    "record when A and B and C play", which nobody has defined. A narrowing
    its words do not state is the planner's to decline (its row of
    ``compose.plan.STATED_SCOPING``).

    .. versionadded:: 5.0.0
       On the reader's side (``compose.adapt._adapt_with_without`` was this).
    """
    mate_texts, _asked_without, _roles = with_without_named(scope)
    if not mate_texts:
        texts = list(dict.fromkeys(n.strip() for n in (scope.player, *scope.players) if n is not None and n.strip()))
        team_named = bool(scope.team and scope.team.strip())
        if not ((team_named and len(texts) == 1) or (not team_named and len(texts) == 2)):
            raise Unsupported(f"with_without needs exactly one teammate, got {texts!r}")
    return Reading(scope=scope, shape="split", by="presence", on="team_games", measures=["record"], aggregate="record", group="presence", predicates=[], relation="team")


def _default_head_to_head(scope: Scope) -> Reading:
    """``head_to_head``'s default point: two teams' meetings - a record on
    the team relation grouped by which team won each (``compose.meetings``).
    The two teams are the scope's ``teams``, ``team`` and ``opponent``, read
    by the meetings' reader; a narrowing its words do not state is the
    planner's to decline (``compose.plan.TEAM_SHAPES_STATED``).

    .. versionadded:: 5.0.0
    """
    return Reading(scope=scope, shape="comparison", by="opponent", on="team_games", measures=["record"], aggregate="record", group="opponent", predicates=[], relation="team")


def _default_team_quarter_points(scope: Scope) -> Reading:
    """``team_quarter_points``' default point: a team's figure in one quarter
    or half - a total on the team relation over its games, the period the
    relation's own narrowing (``compose.periods.read_team_quarter_points``,
    which reads the stat, the period and the team from the scope and
    refuses what is not on the period's line).

    .. versionadded:: 5.0.0
    """
    return Reading(scope=scope, shape="scalar", by="total", on="team_periods", measures=["points"], aggregate="total", group="period", predicates=[], relation="team")


def _default_period_leaderboard(scope: Scope) -> Reading:
    """``period_leaderboard``'s default point: the league's players (or one
    team's) ranked per game by a stat in one quarter or half, or by their
    points in each quarter - a grouped read by player on the league-wide
    relation, the period the relation's own narrowing
    (``compose.periods.read_period_leaderboard``).

    .. versionadded:: 5.0.0
    """
    return Reading(scope=scope, shape="ranking", by="player", on="player_periods", measures=["points"], aggregate="per_game", group="player", predicates=[], order="measure", relation="everyone")


def _default_team_record(scope: Scope) -> Reading:
    """``team_record``'s default point: a team's games won and lost - a
    record on the team relation (``compose.team_records``, which reads the
    team, the opponent, the span, the venue, a month or calendar narrowing,
    a game of each series and a split by month from the scope, and reads a
    plain regular season's from the standings).

    .. versionadded:: 5.0.0
    """
    return Reading(scope=scope, shape="scalar", by="record", on="team_games", measures=["record"], aggregate="record", group="none", predicates=[], relation="team")


def _default_player_netpoints(scope: Scope) -> Reading:
    """``player_netpoints``' default point: one player's NetPoints - a season's
    ratings and the play-type split behind them, or one game's - on the
    NetPoints relation (``compose.netpoints``, which reads the player, the
    season, its type, the unit and a first or last game from the scope).

    .. versionadded:: 5.0.0
    """
    return Reading(scope=scope, shape="scalar", by="ratings", on="netpoints", relation="netpoints")


def _default_fingerprint(scope: Scope) -> Reading:
    """``fingerprint``'s default point: one or more players' play-type
    fingerprints, drawn as one radar - a season's, or a first or last game's -
    on the NetPoints relation (``compose.netpoints``).

    .. versionadded:: 5.0.0
    """
    return Reading(scope=scope, shape="chart", by="fingerprint", on="netpoints", relation="netpoints")


def _default_shot_chart(scope: Scope) -> Reading:
    """``shot_chart``'s default point: one player's located shots, drawn -
    a ``chart`` on the declared ``shots`` relation (``compose.shots``, which
    reads the player, the span, the games the relation narrows to and the
    shot value from the scope).

    .. versionadded:: 5.0.0
    """
    return Reading(scope=scope, shape="chart", by="shots", on="shots", relation="shots")


def _default_shot_distance(scope: Scope) -> Reading:
    """``shot_distance``'s default point: one player's average shot distance
    over the same shots - a ``scalar`` on the declared ``shots`` relation
    (``compose.shots``).

    .. versionadded:: 5.0.0
    """
    return Reading(scope=scope, shape="scalar", by="distance", on="shots", relation="shots")


DEFAULT_POINTS: dict[str, Callable[[Scope], Reading]] = {
    "game_log": _default_game_log,
    "player_stat": _default_player_stat,
    "player_splits": _default_player_splits,
    "record_when": _default_record_when,
    "period_split": _default_period_split,
    "threshold_count": _default_threshold_count,
    "single_game_high": _default_single_game_high,
    "streak": _default_streak,
    "player_matchup": _default_player_matchup,
    "with_without": _default_with_without,
    "head_to_head": _default_head_to_head,
    "team_quarter_points": _default_team_quarter_points,
    "period_leaderboard": _default_period_leaderboard,
    "team_record": _default_team_record,
    "player_netpoints": _default_player_netpoints,
    "fingerprint": _default_fingerprint,
    "shot_chart": _default_shot_chart,
    "shot_distance": _default_shot_distance,
}
"""Intent -> its default point, read by the reader itself (Phase 2, step 1:
slice (i)'s five; step 2: ``threshold_count``, ``single_game_high``, the
streak and the matchup; step 3: the with/without split, the last of the
adapters, which went with ``compose.adapt``; step 5: the NetPoints relation's
two and the shot relation's two, :data:`~association.query.reading.CHART_INTENTS`). An intent with no entry here
has its point read elsewhere in this module (:func:`read_point`) or none.

.. versionadded:: 5.0.0
"""


def default_point(intent: str, scope: Scope) -> Reading:
    """The point a bare ``intent`` means over ``scope``, stamped with the
    intent it is the default of (the planner declines by it until intent
    leaves the reader in Phase 3): the reader's own
    (:data:`DEFAULT_POINTS`). An intent with none is declined.

    .. versionadded:: 5.0.0
    """
    reader = DEFAULT_POINTS.get(intent)
    if reader is None:
        # The sentence the adapters' table gave until it went, kept so no
        # reading's verdict moves with the file.
        raise Unsupported(f"no adapter for {intent}")
    return replace(reader(scope), intent=intent)


def _move_default(intent: str, scope: Scope, measure: str | None) -> Reading:
    """The intent's default point, with the measure the question named added on."""
    base = default_point(intent, scope)
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
    if lexicon.PERIOD_GUARD.search(question) and not any(line.period is not None for line in scope.lines):
        # The quarter words are a condition's (read into the scope), or the
        # period relation's question.
        raise Unsupported("a quarter or half is the period relation's question")
    measure = _measure_for_named(scope, question)
    single = _move_single_game(intent, scope, question, measure)
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
#: counterpart of :data:`WORD_MEASURES`, over :data:`~association.query.measures.TEAM_GAME_MEASURES`
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
    (``compose.logs.read_team_log`` reads it and ``compose.say`` says it in
    the retired template's words). The team is the route's own slot, or the one
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
        by="date",
        on="team_games",
        measures=["points"],
        aggregate="none",
        group="none",
        predicates=[],
        order="date",
        direction="asc" if scope.window.order == "first" else "desc",
        limit=_clamp_limit(scope.window.count, DEFAULT_GAME_LOG_LIMIT),
        relation="team",
    )


def team_splits_point(scope: Scope, subject: Subject) -> Reading | None:
    """A ``player_splits`` question about a team and no player - "76ers wins
    vs losses" - as the team's own splits: a ``grouped`` point on the team
    relation (``compose.splits.read_team_splits`` reads it and ``compose.say``
    says it in the retired template's words).
    The team is read as :func:`team_log_point` reads it.

    .. versionadded:: 5.0.0
    """
    log = team_log_point(scope, subject)
    if log is None:
        return None
    return Reading(scope=log.scope, shape="split", by="splits", on="team_games", measures=["points"], aggregate="record", group="venue", predicates=[], relation="team")


def team_read_point(scope: Scope, question: str, subject: Subject) -> Reading | None:
    """Whether ``question``/``scope`` name a team as the grammatical
    SUBJECT - no player, a team identifiable (the router's own ``team`` slot,
    or :func:`~association.query.subject.team_named_in` when the router
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
       treating it as a real name to resolve - it already fails the team
       compiler's resolution (``entities.resolved_team`` RAISES
       for it, unlike a name with no match, which returns a clarification),
       so returning a team point here only delayed the same decline
       `_everyone_point`'s own
       ``_NOT_PLAYERS`` guard already gives this exact question ("allowed" is
       in it) - through a noisier path, for no different an outcome.

    .. versionchanged:: 5.0.0
       Takes the typed :class:`~association.query.reading.Scope` and the
       :class:`~association.query.subject.Subject` the parser read, in place
       of a slot dict and a subject read here for a caller with none.

    .. versionchanged:: 5.0.0
       Takes no connection: the reader reads names from the in-memory
       index and asked the warehouse nothing here (the ``con`` was carried
       and never used).
    """
    if _named_player_in(scope) or lexicon.PERIOD_GUARD.search(question) or _RANKING.search(question) or _LOG.search(question) or _TEAM_NOT_SUBJECT.search(question):
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
    # A sum no reader takes (the team compiler's own sentence), unless the
    # intent is a team's own season's, which read_point names.
    return Reading(scope=scope, shape="scalar", by="", on="team_games", measures=[measure], aggregate="total", relation="team")


TEAM_SEASON_POINTS: dict[str, PointShape] = {
    "team_stat": PointShape("team_seasons", "scalar", "line"),
    "team_leaderboard": PointShape("team_seasons", "ranking", "team"),
    "team_outlook": PointShape("team_snapshots", "scalar", "projection"),
}
"""The intents a team's own season answers, and the shape of that point:
one team's line on ``team_season_stats`` (``team_stat``), every team ranked
by one metric of it or of the standings (``team_leaderboard``), and one
team's place in ESPN's power index (``team_outlook``). Read where the
question's words read no other point for the intent - and a point the words
DO read under one of these intents (a team's own total, "how many 3-pointers
have the Magic made", :func:`team_read_point`) is read and said as this
shape too (:func:`read_point`): the team-season reader is tried first and
the team compiler's sum answers where it declines (``compose.answer``), as
the retired template was tried before the compiler.

.. versionadded:: 5.0.0

.. versionchanged:: 6.0.0
   A :class:`~association.query.reading.PointShape` per intent, which the
   point carries (Phase 3, step 1); the relation and the compiler's skeleton
   until then.
"""


def team_season_point(intent: str, scope: Scope) -> Reading:
    """``intent``'s point on its team-season relation
    (:data:`TEAM_SEASON_POINTS`), over ``scope`` as the question settled it:
    the reader names the relation and the shape, and reads no metric - which
    of ``team_metrics.TEAM_METRICS`` the ``stat`` slot names, and what a
    word it does not know is refused with, is the relation's reader's
    (``compose.team_stats``), as it was the retired templates'.

    .. versionadded:: 5.0.0
    """
    key = TEAM_SEASON_POINTS[intent]
    relation: Literal["team_seasons", "team_snapshots"] = "team_snapshots" if key.relation == "team_snapshots" else "team_seasons"
    return Reading(scope=scope, shape=key.shape, by=key.by, on=key.relation, relation=relation)


def read_point(reading: Reading, question: str) -> Reading:
    """``reading``'s intent's default point, moved by ``question``'s own words: a
    measure beyond a template's list, a skeleton move ("most ... in a game" =
    rows by measure; "how many ... won" = count with a predicate) - none of it
    through a prompt edit. A team named with no player
    (:func:`team_read_point`) is tried before the league-wide reading, since a
    team's own total or differential is a narrower, more specific claim than
    "no player subject" - the same priority a named player already gets over
    the league-wide read. Called by the parser, once
    (:func:`~association.query.parse.reading_from_route`); the compiler plans
    and runs the Reading it returns.

    .. versionchanged:: 4.4.0
       Tries :func:`team_read_point` (the team as a subject) before the
       league-wide reading; the point it returns is the team relation's
       (``relation="team"``), planned as a
       :class:`~association.query.compose.team.TeamQuery`.

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

    .. versionchanged:: 6.0.0
       The point names what it is read and said as - its ``shape``, ``by``
       and the relation it is ``on`` (Phase 3, step 1); the planner
       translated the intent into them until then (``plan.shape_of``).
    """
    subject = reading.subject
    if subject is None:
        # Who the question is about is read once, by the parser; a Reading
        # with none is a caller's mistake, said here rather than as an
        # AttributeError three moves down.
        raise ValueError("read_point needs the reading's subject - who the question is about, as the parser read it")
    try:
        point = _read_point(reading.intent, reading.scope, question, subject)
    except Unsupported:
        if reading.intent not in TEAM_SEASON_POINTS:
            raise
        # No other reading of the point: the team's own season is the
        # question (Phase 2, step 4). Until then this was a decline - "a
        # team's own question is not the player relation's" - which the
        # retired template answered past.
        point = team_season_point(reading.intent, reading.scope)
    if reading.intent in TEAM_SEASON_POINTS:
        # Whatever the words moved (a team's own total on the team relation),
        # the point is read and said as the team's own season's shape:
        # that reader first, the team compiler's sum where it declines.
        key = TEAM_SEASON_POINTS[reading.intent]
        point = replace(point, on=key.relation, shape=key.shape, by=key.by)
    return replace(point, intent=reading.intent, subject=subject, evidence=(*point.evidence, *subject.evidence))


def _leaderboard_declines(scope: Scope, subject: Subject) -> None:
    """What a ``leaderboard`` point is not, declined before any move - split
    out of :func:`_read_point` to keep it inside the complexity gate."""
    if scope.stat in ("triple_double", "double_double") and subject.kind in ("team", "team_players") and not subject.players:
        # A TEAM's total of its players' triple-doubles ("oklahoma city
        # thunder all-time triple doubles vs west", leaderboard with the team
        # filed - day5): not a ranking this relation lacks a measure for, but
        # a team aggregate nothing reads. Declined here, where the subject
        # is in hand (the router-era `repair` read "vs west" as the team's
        # opponent here),
        # so the reading's own cause names it (``team_boolean_count``,
        # Reading.unsupported, said where the answer side declines) rather
        # than the ranking's sentence naming the wrong one.
        raise Unsupported(f"a team's total of its players' {scope.stat} is not read")
    if _named_player_in(scope):
        # A leaderboard ranks the league or a team, never one named person -
        # the retired template's own refusal ("Klay Thompson's 3pt percentage
        # over the past 4 seasons" once landed here and came back with the
        # league's true-shooting leaders, Klay silently dropped).
        raise Unsupported(f"a leaderboard cannot answer about one named player ({scope.player!r})")


def _read_point(intent: str, scope: Scope, question: str, subject: Subject) -> Reading:
    """:func:`read_point`'s moves, in order; split out so the record carries
    the intent and the subject whichever move settled it."""
    if intent == "coach":
        # No table here holds a coach: the reading's verdict, said by the
        # planner in the retired template's words (compose.plan).
        raise PointRefused(Cause(kind="no_coach_table", facts={"unanswerable": "coach"}))
    if intent in CHART_INTENTS:
        # A declared relation's own point, whatever the words: the retired
        # templates read their slots alone, and a player's games are not
        # where a NetPoints rating, a fingerprint or a shot chart is read
        # (Phase 2, step 5).
        return default_point(intent, scope)
    if intent == "leaderboard":
        _leaderboard_declines(scope, subject)
    if intent == "record_when" and not _named_player_in(scope) and scope.team is not None and scope.team.strip():
        # A team's record above and below its OWN line - "what was the celtics
        # record when they scored 120 points" (ISSUES.md #144) - is neither the
        # season sum nor the window sum the team subject otherwise reads: it is
        # record_when's team reader's (compose.records.read_team_record_when).
        return Reading(scope=scope, shape="split", by="line", on="team_games", measures=[scope.stat or "points"], aggregate="record", relation="team")
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
    if intent in ("streak", "player_matchup", "with_without", "head_to_head", "team_quarter_points", "period_leaderboard", "team_record"):
        # The retired templates read their slots alone, so no word moves the
        # point: a player's, a team's or the league's longest run
        # (_default_streak), two players' meetings
        # (_default_player_matchup), a team's record with and without a
        # teammate (_default_with_without), two teams' meetings
        # (_default_head_to_head), a team's quarter or half
        # (_default_team_quarter_points), the league's ranking by one
        # (_default_period_leaderboard), or a team's record
        # (_default_team_record).
        return default_point(intent, scope)
    if not _named_player_in(scope):
        team_reading = team_read_point(scope, question, subject)
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
            raise PointRefused(Cause(kind="needs_subject", facts={"intent": "record_when"}))
        return _everyone_point(intent, scope, question, stat_measure(scope.stat), subject.position)
    return _move_named(intent, scope, question)
