"""The point reader: the question's own words, read in code - the way the
grammar's rules (:mod:`association.query.grammar`; the router's ``route()``,
its ``CODE_ASSIGNED_INTENTS`` and ``_validate_*`` helpers, before them) read
them, never through a prompt - move the measure, the skeleton or the
aggregate off the default point of what the words ask
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

from collections.abc import Callable
from dataclasses import replace
from typing import TYPE_CHECKING, Any, Literal

from association.nba.season import current_season
from association.query import lexicon
from association.query.cuts import CutsRead
from association.query.entities import BOX_SCORES, SHOT_AVAILABILITY
from association.query.line import LineCandidate, LineCandidates, LinesRead
from association.query.lines import measure_filters, relation_lines, threshold_count_line, threshold_line, threshold_of
from association.query.measure import GAMES_STATS, MADE_TO_ATTEMPTED, NETPOINTS_PER_100, MeasureContext, MeasureRead, MeasureWords, how_of, key_of, measured, named_by_a_team_metric
from association.query.measures import (
    BOOLEAN_MEASURES,
    DERIVED_LINES,
    HISTORY_STATS,
    LINE,
    SPLIT_LINE,
    STAT_LINE,
    TEAM_GAME_MEASURES,
    TEAM_SEASON_MEASURES,
    THRESHOLD_STAT_NAMES,
    log_extras,
    period_split_measure,
    resolve_metric,
    stat_column,
    stat_measure,
    streak_column,
)
from association.query.metrics import PER_GAME_MIN_GAMES, SEASON_FIGURES, qualifies_by
from association.query.period import PeriodRead
from association.query.reading import (
    DEFAULT_GAME_LOG_LIMIT,
    DEFAULT_LIMIT,
    DEFAULT_NAMED_RUNS,
    DEFAULT_SINGLE_GAME_LIMIT,
    DEFAULT_STREAK_LIMIT,
    GAME_HIGHS,
    GAMES_COUNTED,
    LINE_RECORD,
    LINE_RUNS,
    NETPOINTS_FINGERPRINT,
    NETPOINTS_RATINGS,
    PERIOD_LOG,
    PERIOD_RANKING,
    PLAYER_COMPARISON,
    PLAYER_LINE,
    PLAYER_LOG,
    PLAYER_MEETINGS,
    PLAYER_RANKING,
    PLAYER_SPLITS,
    PLAYERS_COUNTED,
    PRESENCE_SPLIT,
    SEASON_HISTORY,
    SHOT_CHART,
    SHOT_DISTANCE,
    TEAM_COACH,
    TEAM_GAME_HIGHS,
    TEAM_LINE,
    TEAM_MEETINGS,
    TEAM_ONLY_ASKS,
    TEAM_OUTLOOK,
    TEAM_PERIOD_TOTAL,
    TEAM_RANKING,
    TEAM_RECORD,
    TEAM_SEASON_HISTORY,
    Aggregate,
    Cause,
    Claim,
    Filtered,
    Group,
    Line,
    Measure,
    MeasureHow,
    MeasureSide,
    MeasureWhose,
    PointRefused,
    PointShape,
    Reading,
    Span,
    Unsupported,
    Window,
    _career_scope,
    _clamp_limit,
    key_path,
    on_a_declared_relation,
    period_narrowing,
    scope_reads_box_scores,
)
from association.query.reading import named_player_in as _named_player_in
from association.query.span import SpanCandidates, SpanRead, claimed
from association.query.subject import with_without_named
from association.query.window import MinimumRead, WindowCandidates, WindowRead, window_end

if TYPE_CHECKING:
    from association.query.subject import Subject

#: What a ``rows`` point a shared move reads is read as (its
#: :class:`PointShape` on the player-games relation), under the words it
#: was read under - what one row is said BY: a log's and a line's games by date
#: (``compose.logs``, which the retired ``player_stat`` handed a single game
#: to), a single game's high by the measure, a count's games by the count
#: (``compose.counts``); ``""`` under any other words - no reader takes the
#: point, and the compiler's own sentence states what it lists. Keyed by
#: what the words asked (the grammar's key) since Phase 3, step 4, the
#: retired intent's name until then; the ``by`` alone until Phase 5, step 4
#: (D9), when the point declared its shape as three fields.
_ROWS_BY: dict[PointShape | None, PointShape] = {PLAYER_LOG: PLAYER_LOG, PLAYER_LINE: PLAYER_LOG, GAME_HIGHS: GAME_HIGHS, GAMES_COUNTED: PointShape("player_games", "rows", "count")}
#: The same for a count a shared move reads ("how many ... won", a boolean
#: measure counted): his line (``compose.stats``), his count
#: (``compose.counts``), or - under ``record_when``'s words - the record
#: over a line the count is said as (``compose.records``, a split by the
#: line); a scalar nothing reads otherwise.
_COUNT_SHAPES: dict[PointShape | None, PointShape] = {PLAYER_LINE: PointShape("player_games", "scalar", "line"), GAMES_COUNTED: GAMES_COUNTED, LINE_RECORD: LINE_RECORD}
#: The rows and the count no reader takes under any other words (``by`` is
#: ``""``): the compiler's own sentence states what it reads.
_ROWS_UNREAD = PointShape("player_games", "rows", "")
_COUNT_UNREAD = PointShape("player_games", "scalar", "")
#: A team's (or the league's teams') run of wins or losses, read by
#: ``compose.runs.read_team_streak``.
_TEAM_RUN = PointShape("team_games", "runs", "won")


#: A team's record, the one measure its record's points read (the team
#: compiler's ``record``).
_RECORD: tuple[Measure, ...] = (Measure(key="record"),)
#: The team measures the team compiler names apart from their keys: what a
#: team gave up of its points, and its margin (``compose.team.team_metric``
#: names them back).
_TEAM_WORD_KEYS: dict[str, Measure] = {"points_allowed": Measure(key="points", whose="opponent"), "differential": Measure(key="point_differential")}


def _measured(*names: str) -> tuple[Measure, ...]:
    """A point's measures, typed (``Reading.measures``, Phase 4, step 3):
    each name the point reader reads a measure by - the lexicon's measure
    words, a line's stat, a log's or a line's default columns
    (:data:`~association.query.measures.LINE`, ``SPLIT_LINE``,
    ``STAT_LINE``) - as the catalog's
    :class:`~association.query.reading.Measure`
    (:func:`~association.query.measure.measured`). The compiler names them
    back once, as the planner hands it the point
    (:func:`~association.query.compose.core.measure_names`)."""
    return tuple(measured(name) for name in names)


def _led_by(measure: str) -> tuple[Measure, ...]:
    """``measure`` first, then the default line's others: a single game's
    columns, ranked by the one asked about."""
    return _measured(measure, *(m for m in LINE if m != measure))


def _measure_words(question: str) -> list[str]:
    """Every measure :data:`~association.query.lexicon.WORD_MEASURES` finds named in ``question``, in list order."""
    return [name for pattern, name in lexicon.WORD_MEASURES if pattern.search(question)]


def _named_stat(scope: Filtered) -> str | None:
    """The measure's spelling as the reader wrote it, where the question named one - what a refusal prints."""
    return scope.measure.as_typed if scope.measure is not None else None


def _measure_and_predicates(words: list[str], fallback: str | None) -> tuple[str | None, list[tuple[str, str, Any]]]:
    """The measure a question ranks or lists by, and the boolean measures it
    names as conditions: "highest fg% in a triple-double game" measures
    fg_pct under triple_double = true."""
    measure = next((w for w in words if w not in BOOLEAN_MEASURES), None) or fallback
    predicates = [(w, "=", True) for w in words if w in BOOLEAN_MEASURES and w != measure]
    return measure, predicates


def _asc_or_desc(question: str, measure: str | None = None) -> Literal["asc", "desc"]:
    """ "asc" for a "fewest"/"least"/"lowest" question, and for a "worst"
    one on a stat a player wants more of (``measure``, where the ranking
    names it: the worst field-goal percentage is the lowest, the worst
    turnovers the most - ISSUES.md #350; a ranking whose stat the words do
    not name keeps its order: "worst win percentage" is no ranking of
    points from the bottom); "desc" otherwise."""
    if lexicon.FEWEST_WORDS.search(question):
        return "asc"
    if measure is not None and lexicon.WORST_WORD.search(question) and measure not in lexicon.WORST_IS_MOST:
        return "asc"
    return "desc"


def _everyone_guard(asked: PointShape | None, question: str, position: str | None, *, period_is_condition: bool = False) -> None:
    """What a league-wide read cannot answer: a period, a team's own figure
    read literally as a ranking of players (K2's guards), or a team-only
    shape's question at all - "Best NBA record since January 31st 2015"
    reached this read once the reading stopped naming Travis Best for it,
    and a player ranking refusing it for want of a "record" measure names
    the wrong cause; it is not this relation's question."""
    if lexicon.PERIOD_GUARD.search(question) and not period_is_condition:
        raise Unsupported("a quarter or half is the period relation's question")
    # "the top 50 ... with the team they play for" (F017) names no team's
    # question: the team is a column the ranking shows.
    if lexicon.NOT_THE_PLAYERS_QUESTION.search(lexicon.TEAM_FIELD_WORDS.sub(" ", question)) and not position:
        raise Unsupported("a team, an opponent's figure or a franchise is the team relation's question")
    if asked in TEAM_ONLY_ASKS:
        raise Unsupported("a team's own question is not the player relation's")


def _everyone_opponent(scope: Filtered, question: str) -> Filtered:
    """A team beside no player, with "vs"/"against", is the opponent."""
    team = scope.who.team
    if team is not None and not scope.cuts.opponent and lexicon.VERSUS_OR_AGAINST_WORD.search(question):
        return replace(scope, cuts=replace(scope.cuts, opponent=team), who=replace(scope.who, teams=()))
    return scope


def _everyone_threshold_predicates(scope: Filtered, measure: str | None, predicates: list[tuple[str, str, Any]]) -> list[tuple[str, str, Any]]:
    """The line the threshold grammar read ("a 30 point triple double game"
    is points >= 30, whatever stat the model filed beside the number): its
    own measure, read from the number's words; the model's stat only where
    a line was built with no words of its own (one built by hand; the tests'
    slot door's until Phase 4, step 0). Until Phase
    3, step 2 this re-read the number's words from the question, and its
    pattern could not pass a comma ("most games with 20 pt,s 10 reb, 5
    ast" fell back to the model's "rebounds")."""
    line = threshold_line(scope)
    if line is None or int(line.value) <= 0:
        return predicates
    column = line.measure or stat_measure(scope.measure)
    if column and column != measure:
        return [*predicates, (column, ">=", int(line.value))]
    return predicates


def _everyone_single_game(asked: PointShape | None, scope: Filtered, question: str, measure: str | None, predicates: list[tuple[str, str, Any]]) -> Reading | None:
    """ "Most ... in a game" over everyone: rows by measure, league-wide.

    .. versionchanged:: 5.0.0
       Also for a ``single_game_high`` question whose words do not say "in a
       game" - the intent names the shape itself. "What was the highest
       scoring game by a player this year?" used to fall to
       :func:`_everyone_ranking` and answer with a ranking of per-game
       AVERAGES (Luka Doncic, 33.5) where the question, and the template,
       name one game (Bam Adebayo's 83).
    """
    if not ((lexicon.TOP_IN_A_GAME.search(question) or asked == GAME_HIGHS) and measure):
        return None
    return Reading(
        **scope.filters(),
        read_as=_ROWS_BY.get(asked, _ROWS_UNREAD),
        measures=_led_by(measure),
        aggregate=Aggregate.NONE,
        group="none",
        predicates=predicates,
        order="measure",
        direction=_asc_or_desc(question),
        limit=_clamp_limit(scope.window.count, DEFAULT_SINGLE_GAME_LIMIT),
        relation="everyone",
    )


def _boolean_game_measure(question: str) -> str:
    """The measure a "highest/biggest ... triple-double" ranks the
    qualifying games BY - the question's own word (:data:`~association.query.lexicon.BOOLEAN_RANK_WORDS`)
    first, points otherwise: "highest scoring" and "biggest" both mean the
    game's point total unless another stat is named."""
    for pattern, name in lexicon.BOOLEAN_RANK_WORDS:
        if pattern.search(question):
            return name
    return "points"


def _everyone_boolean_game_ranking(asked: PointShape | None, question: str, scope: Filtered, predicates: list[tuple[str, str, Any]]) -> Reading | None:
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
    sized = lexicon.BOOLEAN_GAME_RANKING.search(question) or scope.window.by or any(pattern.search(question) for pattern, _ in lexicon.BOOLEAN_RANK_WORDS)
    if not sized:
        return None
    measure = _boolean_game_measure(question)
    return Reading(
        **scope.filters(),
        read_as=_ROWS_BY.get(asked, _ROWS_UNREAD),
        measures=_led_by(measure),
        aggregate=Aggregate.NONE,
        group="none",
        predicates=predicates,
        order="measure",
        direction=_asc_or_desc(question),
        limit=_clamp_limit(scope.window.count, DEFAULT_SINGLE_GAME_LIMIT),
        relation="everyone",
    )


def _numbered_lines(scope: Filtered) -> list[tuple[str, str, Any]]:
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
        if line.per != "game" or line.period is not None or line.op != ">=" or line.measure is None or int(line.value) < 1 or line.measure in seen:
            continue
        seen.add(line.measure)
        predicates.append(line.as_predicate())
    return predicates


def _everyone_multi_line_games(asked: PointShape | None, scope: Filtered, question: str, predicates: list[tuple[str, str, Any]]) -> Reading | None:
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
    if asked != GAMES_COUNTED or lexicon.RANKING_WORDS.search(question):
        return None
    text_lines = _numbered_lines(scope)
    lines = text_lines if len(text_lines) > len(predicates) else predicates
    if len(lines) < 2:
        return None
    return Reading(
        **scope.filters(),
        read_as=PointShape("player_games", "rows", "count"),
        measures=_measured(*(name for name, _, _ in lines)),
        aggregate=Aggregate.NONE,
        group="none",
        predicates=lines,
        order="date",
        direction="desc",
        limit=_clamp_limit(scope.window.count, 25),
        relation="everyone",
    )


def _everyone_threshold_count_line(scope: Filtered) -> list[tuple[str, str, Any]]:
    """The question's own keyed line (:func:`~association.query.lines.threshold_line`:
    the stat at its number, the router's ``stat`` and ``threshold`` slots
    until Phase 3, step 2), as the one line a
    league-wide count counts - where :func:`_everyone_threshold_predicates`
    added nothing because the line is on the very measure it would rank by.
    "Most games with 15+ assists in 2024?" declined for want of a line until
    this existed: the line was the whole question.

    .. versionadded:: 5.0.0
    """
    column = stat_measure(scope.measure)
    threshold = threshold_of(scope)
    if column is None or column in BOOLEAN_MEASURES or threshold is None or threshold < 1:
        return []
    return [(column, ">=", threshold)]


def _everyone_count_bare_lines(scope: Filtered, predicates: list[tuple[str, str, Any]]) -> list[tuple[str, str, Any]]:
    """The second and later bare lines of a league count ("most games with
    20 pts, 10 reb, 5 ast": the 10 rebounds and the 5 assists, which the
    tagger reads as lines neither keyed nor narrowing), as predicates beside
    the count's own - the games clearing every one are counted (ISSUES.md
    #358). They were read by nothing, and the count answered the first line
    alone; a line the relation narrows by already is no predicate here."""
    counted = {name for name, _, _ in predicates}
    extra: list[tuple[str, str, Any]] = []
    for line in scope.lines:
        if line.keyed or line.narrows or line.per != "game" or line.period is not None or line.op != ">=" or line.measure is None or int(line.value) < 1:
            continue
        if line.measure not in counted:
            counted.add(line.measure)
            extra.append(line.as_predicate())
    return extra


def _everyone_threshold_count(asked: PointShape | None, scope: Filtered, predicates: list[tuple[str, str, Any]]) -> Reading | None:
    """A league-wide count: who had games clearing the line(s). Without a
    line to count there is nothing to rank - refused, never turned into a
    per-game ranking."""
    if asked != GAMES_COUNTED and not (predicates and scope.who.team):
        # A TEAM's players' boolean games ("thunder all-time triple doubles",
        # yardstick-v2 F152) is this count too, whatever the words ask (the
        # router filed it as any intent) - the team narrows the league read
        # to its roster's games.
        return None
    if not predicates and asked == GAMES_COUNTED:
        threshold = threshold_of(scope)
        if threshold is not None and threshold < 1:
            # threshold_count's own refusal: measured, "most 3 pointers made
            # since 2020" arrived as threshold 0 and would count every game.
            raise PointRefused(Cause(kind="threshold_counts_every_game", facts={"shape": GAMES_COUNTED, "threshold": threshold}))
        predicates = _everyone_threshold_count_line(scope)
    if asked == GAMES_COUNTED and predicates:
        predicates = [*predicates, *_everyone_count_bare_lines(scope, predicates)]
    if not predicates:
        raise PointRefused(Cause(kind="needs_line"))
    if scope.cuts.season_n:
        # The refusal threshold_count's retired template gave:
        # "his 15th season" is a place in one career, and the league has none -
        # read over everyone it narrowed to players in their 15th season of the
        # default year while the sentence named only the year.
        raise PointRefused(Cause(kind="career_place_needs_player", facts={"season_n": scope.cuts.season_n}))
    # threshold_count's own leaderboard length (DEFAULT_LIMIT, five names)
    # where it is that shape's question; ten for a team's roster count
    # (F152), which also states the whole count beneath the ones listed.
    listed = DEFAULT_LIMIT if asked == GAMES_COUNTED else 10
    # A count's ranking (compose.counts) under its own words; a team's
    # roster count under any other is a ranking by player no reader takes.
    return Reading(
        **scope.filters(),
        read_as=PointShape("player_games", "ranking", "count" if asked == GAMES_COUNTED else "player"),
        measures=(),
        aggregate=Aggregate.COUNT,
        group="player",
        predicates=predicates,
        order="measure",
        direction="desc",
        limit=_clamp_limit(scope.window.count, listed),
        relation="everyone",
    )


def _everyone_ranking(asked: PointShape | None, scope: Filtered, question: str, measure: str | None, predicates: list[tuple[str, str, Any]]) -> Reading | None:
    """A ranking word, or a leaderboard/single-game-high key: grouped by
    player. A "with at least N games" phrase in the question replaces the
    default minimum sample (:data:`~association.query.metrics.PER_GAME_MIN_GAMES`)
    with the one the question itself named; a unit the relation has no
    HAVING clause for (attempts, minutes, ...) is refused by name rather
    than silently dropped or misapplied as a games count (F056:
    "... by a shooting guard with at least 100 attempts").

    .. versionchanged:: 4.4.0
       Reads a "with at least N <unit>" floor.

    .. versionchanged:: 6.0.0
       The floor is the Reading's typed qualifier (``Filtered.minimum``,
       ISSUES.md #377), read by the window tagger: "minimum 100 games
       played" and "at least 300 gp" are games, where "minimum" was read by
       nothing and "gp" refused.

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
    if not (lexicon.RANKING_WORDS.search(question) or asked in (PLAYER_RANKING, GAME_HIGHS)):
        return None
    seasons = _leaderboard_season_line(asked, scope, question, measure, predicates)
    if seasons is not None:
        return seasons
    if scope.measure is not None and scope.measure.key == "games_played":
        # Players ranked by their games (ISSUES.md #408): a count of each
        # one's games over the narrowed span, ordered by it - the count a
        # ranking of boolean games orders by, with no predicate. No
        # default qualifier: the games ARE the measure.
        return Reading(
            **scope.filters(),
            read_as=PointShape("player_games", "ranking", "player"),
            measures=(),
            aggregate=Aggregate.COUNT,
            group="player",
            predicates=predicates,
            order="measure",
            direction=_asc_or_desc(question),
            limit=_clamp_limit(scope.window.count, 10),
            minimum_games=_games_played_minimum(scope),
            relation="everyone",
        )
    if measure is None:
        stat = _named_stat(scope)
        if scope.measure is not None and scope.measure.key == "shot_distance":
            # The measure tagger's sentinel key (lexicon.SHOT_DISTANCE_RANKED):
            # the retired template's own refusal, naming the real cause.
            raise PointRefused(Cause(kind="shot_distance_ranking"))
        if stat is not None and stat.strip():
            raise PointRefused(Cause(kind="no_ranking_measure", facts={"stat": stat}))
    aggregate = Aggregate.TOTAL if lexicon.TOTAL_WORD.search(question) else Aggregate.PER_GAME
    minimum_games = PER_GAME_MIN_GAMES
    if scope.minimum is not None:
        if scope.minimum.unit != "games":
            # A refusal, not a decline: nothing downstream reads an attempts
            # floor on this ranking, and "None" here sent yardstick-v2 F056 to
            # the agent for a minute. The planner's sentence names the floor
            # that IS applied so the question can be re-asked with it.
            raise PointRefused(Cause(kind="ranking_floor_unit", facts={"unit": scope.minimum.word, "count": scope.minimum.count}))
        minimum_games = scope.minimum.count
    # The game-level ranking by player, which no reader takes: the
    # compiler's own ranking and sentence.
    return Reading(
        **scope.filters(),
        read_as=PointShape("player_games", "ranking", "player"),
        measures=_measured(measure or "points"),
        aggregate=aggregate,
        group="player",
        predicates=predicates,
        order="measure",
        direction=_asc_or_desc(question, measure),
        limit=_clamp_limit(scope.window.count, 10),
        minimum_games=minimum_games,
        relation="everyone",
    )


def _games_played_minimum(scope: Filtered) -> int | None:
    """A ranking by games played's qualifier: the games the words state, or
    none - a floor in another unit refused by name, as every game-level
    ranking refuses it."""
    if scope.minimum is None:
        return None
    if scope.minimum.unit != "games":
        raise PointRefused(Cause(kind="ranking_floor_unit", facts={"unit": scope.minimum.word, "count": scope.minimum.count}))
    return scope.minimum.count


def _leaderboard_season_line(asked: PointShape | None, scope: Filtered, question: str, measure: str | None, predicates: list[tuple[str, str, Any]]) -> Reading | None:
    """``leaderboard``'s own point (its template retired, ROADMAP plan item
    6, step (g)): a ranking of the league or a team over the SEASON LINE -
    ``run_leaderboard``'s pool, floors, traded-player dedup and NetPoints
    tables - read by ``compose.rankings.read_leaderboard`` and said by the sayer,
    for a stat that resolves to a leaderboard metric with no line on a
    column and no position group, each of which the game-level ranking
    reads and the season line does not - a stated qualifier only where the metric qualifies by its
    unit (``metrics.qualifies_by``: "minimum 100 games played" on a per-game
    rate, "with at least 1000 fg attempted" on field-goal percentage), which
    the reader applies in place of its default (ISSUES.md #377). A measure
    the words moved in that is not the stat's own is the
    game-level ranking's too ("most points in a game" is a single game;
    "total" stays a rate the ranking's reader reads from the measure's unit,
    ``Measure.unit``).

    .. versionadded:: 5.0.0
    """
    # A boolean stat's own predicate ("most triple-doubles": triple_double is
    # True) is the count the season line keeps; any other line on a column
    # is the game-level ranking's.
    own_boolean = all(scope.measure is not None and name == scope.measure.key and value is True for name, _, value in predicates)
    if asked != PLAYER_RANKING or not own_boolean or scope.who.position is not None:
        return None
    metric = resolve_metric(scope.measure, career=scope.span.career)
    if metric is None:
        return None
    if scope.minimum is not None and not qualifies_by(metric, scope.minimum.unit, scope.minimum.of):
        # A qualifier the question states in a unit this metric does not
        # qualify by ("highest 3-point percentage ... with at least 40
        # games"): the game-level ranking, which qualifies by games, or its
        # refusal naming the floor asked.
        return None
    if measure is not None and stat_measure(scope.measure) not in (None, measure):
        return None
    unit = scope.measure.unit if scope.measure is not None else None
    if unit is not None and unit != "total":
        # A unit the metric has no form of ("who were the top 10 in
        # defensive netpoints / 90"): the ranking's refusal, naming the
        # forms THIS metric has (the planner's sentence) - "total" is a
        # season total, the one other form every metric's rate reads.
        raise PointRefused(Cause(kind="ranking_unit", facts={"metric": metric, "rate": unit}))
    return Reading(
        **scope.filters(),
        read_as=PLAYER_RANKING,
        measures=_measured(measure or "points"),
        aggregate=Aggregate.PER_GAME,
        group="player",
        predicates=[],
        order="measure",
        direction=_asc_or_desc(question, scope.measure.key if scope.measure is not None else None),
        limit=_clamp_limit(scope.window.count, 10),
        minimum_games=PER_GAME_MIN_GAMES,
        relation="everyone",
    )


def _everyone_position_log(asked: PointShape | None, scope: Filtered, question: str) -> Reading | None:
    """A log word with a position: rows, over that position group."""
    if not (scope.who.position and lexicon.LOG_OR_STATS_WORDS.search(question)):
        return None
    return Reading(
        **scope.filters(),
        read_as=_ROWS_BY.get(asked, _ROWS_UNREAD),
        measures=_measured(*LINE),
        aggregate=Aggregate.NONE,
        group="none",
        predicates=[],
        order="date",
        direction="desc",
        limit=_clamp_limit(scope.window.count, 10),
        relation="everyone",
    )


def _everyone_point(asked: PointShape | None, scope: Filtered, question: str, measure: str | None) -> Reading:
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
    _everyone_guard(asked, question, scope.who.position, period_is_condition=any(line.period is not None for line in scope.lines))
    scope = _everyone_opponent(scope, question)
    words = _measure_words(question)
    measure, predicates = _measure_and_predicates(words, measure if measure not in BOOLEAN_MEASURES else None)
    predicates = _everyone_threshold_predicates(scope, measure, predicates)
    single = _everyone_single_game(asked, scope, question, measure, predicates)
    if single is not None:
        return single
    boolean_ranked = _everyone_boolean_game_ranking(asked, question, scope, predicates)
    if boolean_ranked is not None:
        return boolean_ranked
    multi_line = _everyone_multi_line_games(asked, scope, question, predicates)
    if multi_line is not None:
        return multi_line
    counted = _everyone_threshold_count(asked, scope, predicates)
    if counted is not None:
        return counted
    ranked = _everyone_ranking(asked, scope, question, measure, predicates)
    if ranked is not None:
        return ranked
    logged = _everyone_position_log(asked, scope, question)
    if logged is not None:
        return logged
    raise Unsupported("no player subject and no ranking or position-group reading of the question")


def _measure_for_named(scope: Filtered, question: str) -> str | None:
    """The measure a named-player question moves to: the question's own word first, the measure read (the model's stat where the words name none) otherwise."""
    words = _measure_words(question)
    stat = stat_measure(scope.measure)
    return words[0] if words else stat


def _move_single_game(asked: PointShape | None, scope: Filtered, question: str, measure: str | None) -> Reading | None:
    """ "Most ... in a game" for a named player: rows by measure - and
    "most points by curry vs lebron", with a player on the other side of
    the games (ROADMAP step 3): against a named opponent, "most" is his best
    meeting, the question a pair's summary never answered."""
    if not measure or measure in BOOLEAN_MEASURES:
        return None
    against = any(c.side == "opponent" for c in scope.companions)
    if not (lexicon.TOP_IN_A_GAME.search(question) or (against and lexicon.RANKING_WORDS.search(question))):
        return None
    return Reading(
        **scope.filters(),
        read_as=_ROWS_BY.get(asked, _ROWS_UNREAD),
        measures=_led_by(measure),
        aggregate=Aggregate.NONE,
        group="none",
        predicates=[],
        order="measure",
        direction=_asc_or_desc(question),
        limit=_clamp_limit(scope.window.count, DEFAULT_SINGLE_GAME_LIMIT),
    )


def _move_how_many_won(scope: Filtered, question: str, measure: str | None, asked: PointShape | None, career: Filtered) -> Reading | None:
    """ "How many ... has he won" - a count with the ``won`` predicate."""
    if not (lexicon.HOW_MANY_OR_OFTEN.search(question) and lexicon.WON_WORDS.search(question) and (measure in (None, "won", "points") or asked in (LINE_RECORD, GAMES_COUNTED))):
        return None
    return Reading(**career.filters(), read_as=_COUNT_SHAPES.get(asked, _COUNT_UNREAD), measures=(), aggregate=Aggregate.COUNT, group="none", predicates=[("won", "=", True)])


def _move_boolean_count(question: str, measure: str | None, asked: PointShape | None, career: Filtered) -> Reading | None:
    """A boolean measure ("triple-doubles") asked "how many": a count with that predicate.

    Also the reading for a boolean measure on a one-figure shape with no
    "how many" at all - "sengun double-doubles vs southeast division career
    away" (yardstick-v2 F055) routed ``player_splits``, whose figure is a
    per-game line, and a condition has no per-game line: its count is the
    only figure there is (``core._agg`` refuses the average).
    """
    if not (measure in BOOLEAN_MEASURES and measure != "won" and (lexicon.HOW_MANY_OR_OFTEN.search(question) or asked in (GAMES_COUNTED, PLAYER_LINE, PLAYER_SPLITS, None))):
        return None
    if asked == GAMES_COUNTED and _move_boolean_count_is_line(measure, career):
        # "how many times has embiid fouled out?" arrived as fouls >= 6 - the
        # very line ``fouled_out`` is defined as (DERIVED) - so the keyed
        # line's own count is this one, and threshold_count's default point says it.
        return None
    return Reading(**career.filters(), read_as=_COUNT_SHAPES.get(asked, _COUNT_UNREAD), measures=(), aggregate=Aggregate.COUNT, group="none", predicates=[(measure, "=", True)])


def _move_boolean_count_is_line(measure: str, scope: Filtered) -> bool:
    """Whether a boolean measure is, by its one definition
    (:data:`~association.query.measures.DERIVED_LINES`), exactly the keyed
    line (:func:`~association.query.lines.threshold_line`; the router's
    ``stat``/``threshold`` slots until Phase 3, step 2) - ``fouled_out`` is
    ``fouls >= 6``."""
    column = stat_measure(scope.measure)
    threshold = threshold_of(scope)
    if column is None or threshold is None:
        return False
    return DERIVED_LINES.get(measure) == (column, threshold)


def _move_player_history(asked: PointShape | None, scope: Filtered, career: Filtered, measure: str | None) -> Reading | None:
    """``player_history``: a per-season history, read from the season line
    (``on="player_seasons"``) over the question's own filters - the retired template's own
    read. Where the season line does not say it, the planner plans it as a
    career of games grouped by season, newest first (``compose.plan.plan``)."""
    if asked != SEASON_HISTORY:
        return None
    if measure is None and scope.measure is not None and scope.measure.named and scope.measure.key not in HISTORY_STATS:
        # A stat named that neither the season line nor the games carry
        # ("shot_distance"): refused, as player_history's retired template
        # refused it - never drawn as the points history the default measure
        # below would read in its place.
        raise Unsupported(f"no per-season history for stat {scope.measure.as_typed!r}")
    del career  # the season line reads the question's own span; the planner's game-level point widens it
    return Reading(
        **scope.filters(),
        read_as=SEASON_HISTORY,
        measures=_measured(measure or "points"),
        aggregate=Aggregate.PER_GAME,
        group="season",
        predicates=[],
        order="date",
        direction="desc",
        limit=_clamp_limit(scope.window.count, 10),
    )


def _compare_point(scope: Filtered) -> Reading:
    """``player_compare``'s own point (its template retired, ROADMAP plan
    item 6, step (g)): two or more named players' season lines side by side
    (``on="player_seasons"``, grouped by player), read by
    ``compose.seasons.read_player_compare`` and said by the sayer. The template's own
    refusal is the point's: fewer than two distinct names. A narrowing -
    it honors none - is the planner's to decline
    (:func:`~association.query.compose.plan.cells_declined`).

    .. versionadded:: 5.0.0
    """
    if len(set(scope.who.players)) < 2:
        raise Unsupported("player_compare needs at least two distinct player names")
    return Reading(**scope.filters(), read_as=PLAYER_COMPARISON, measures=(), aggregate=Aggregate.PER_GAME, group="player", predicates=[])


# --- the default points -----------------------------------------------------------
#
# The point a bare key means before any of the question's own words move
# it, for the shapes Phase 2 has ported (``ROADMAP.md``, "Phase 2, the
# expected steps", step 1): read from the filters alone, on the reader's side.
# Every default point is here since the last adapter went (step 3).


def _default_game_log(scope: Filtered) -> Reading:
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
    if _named_stat(scope) and stat_column(scope.measure) is None:
        log_extras(scope.measure)
    date = scope.cuts.date
    return Reading(
        **scope.filters(),
        read_as=PLAYER_LOG,
        measures=_measured(*LINE),
        aggregate=Aggregate.NONE,
        group="none",
        predicates=_threshold_line(scope),
        order="date",
        direction="asc" if scope.window.order == "first" else "desc",
        limit=_clamp_limit(scope.window.count, DEFAULT_GAME_LOG_LIMIT),
        subject_span=scope.span.over_career() if date else scope.span,
    )


def _threshold_line(scope: Filtered) -> list[tuple[str, str, Any]]:
    """A ``threshold`` as the line a log keeps games past, or nothing where
    it is a below/above phrase's own number; a threshold beside no stat is
    refused for the stat."""
    threshold = threshold_of(scope)
    if threshold is None or any(line.value == threshold for line in measure_filters(scope)):
        return []
    col = stat_column(scope.measure)
    if col is None:
        raise PointRefused(Cause(kind="threshold_needs_stat", facts={"shape": PLAYER_LOG, "threshold": threshold}))
    return [(col, ">=", threshold)]


def _default_player_stat(scope: Filtered) -> Reading:
    """``player_stat``'s default point: a per-game average over box scores
    where a narrowing (or a date) sends the read there
    (:func:`~association.query.reading.scope_reads_box_scores`), and the
    season line (``on="player_seasons"``) for an unnarrowed season or career. A
    window ("Jokic averages last 10 games") is the log of exactly those
    games with averages beneath - the shape the question has - so the point
    is ``game_log``'s."""
    if not _named_player_in(scope):
        raise Unsupported("player_stat needs a player")
    col = stat_column(scope.measure)
    measures = [col] if col else list(STAT_LINE)
    if scope.window.count or scope.window.order:
        return _default_game_log(scope)
    if not (scope_reads_box_scores(scope, measure_filters(scope)) or scope.cuts.date):
        return Reading(**scope.filters(), read_as=PLAYER_LINE, measures=_measured(*measures), aggregate=Aggregate.PER_GAME, group="none", predicates=[])
    date = scope.cuts.date
    return Reading(
        **scope.filters(),
        read_as=PointShape("player_games", "scalar", "line"),
        measures=_measured(*measures),
        aggregate=Aggregate.PER_GAME,
        group="none",
        predicates=[],
        subject_span=scope.span.over_career() if date else scope.span,
    )


def _default_player_splits(scope: Filtered) -> Reading:
    """``player_splits``'s default point: a record by venue, or by starter and
    bench. A named half ("as a starter") narrows the games (the relation's own
    ``started``) while the category shown is still starter/bench; by venue it
    read the half's games split by home/away instead."""
    if not _named_player_in(scope):
        raise Unsupported("a team's splits are the team relation's")
    group: Group = "starter" if scope.split in ("starter_bench", "starter", "bench") else "venue"
    return Reading(**scope.filters(), read_as=PLAYER_SPLITS, measures=_measured(*SPLIT_LINE), aggregate=Aggregate.RECORD, group=group, predicates=[], available=BOX_SCORES)


def _default_record_when(scope: Filtered) -> Reading:
    """``record_when``'s default point: the record in games clearing one line.
    A line of 0 is every game he played: never a record "when". Refused by
    the fact missing - the stat, a stat with no per-game column, the number,
    or a number every game clears - where it named one line only in part."""
    col = stat_column(scope.measure)
    threshold = threshold_of(scope)
    if not _named_player_in(scope):
        # read_point reads a named player's moves only; a caller's mistake.
        raise Unsupported("record_when needs a player, a stat and a positive threshold here")
    stat = _named_stat(scope)
    if col is None and stat and stat.strip():
        raise PointRefused(Cause(kind="unknown_stat", facts={"shape": LINE_RECORD, "stat": stat}))
    if col is None:
        raise PointRefused(
            Cause(kind="threshold_needs_stat", facts={"shape": LINE_RECORD, "threshold": threshold}) if threshold is not None else Cause(kind="needs_stat", facts={"shape": LINE_RECORD})
        )
    if threshold is None:
        # The one line in the question is a companion's ("maxey record when
        # embiid scores 20+ points"): the record cannot be keyed on it, and
        # the wordings that reach each reading are named (ISSUES.md #359).
        reached = [c for c in scope.companions if c.predicate == "reached" and c.line is not None]
        if reached and reached[0].line is not None:
            raise PointRefused(Cause(kind="line_is_a_companions", facts={"shape": LINE_RECORD, "player": scope.who.player or "", "companion": reached[0].player, "line": reached[0].line.as_typed}))
        raise PointRefused(Cause(kind="needs_threshold", facts={"shape": LINE_RECORD, "stat": col}))
    if threshold < 1:
        raise PointRefused(Cause(kind="threshold_counts_every_game", facts={"shape": LINE_RECORD, "threshold": threshold}))
    # A record over a line: said as a split by the line (compose.records),
    # compiled as the scalar record above and below it.
    return Reading(**scope.filters(), read_as=LINE_RECORD, measures=(), aggregate=Aggregate.RECORD, group="none", predicates=[(col, ">=", threshold)], available=BOX_SCORES)


def _default_period_split(scope: Filtered) -> Reading:
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
    measure = period_split_measure(scope.measure)
    date = scope.cuts.date
    if period_narrowing(scope) is None:
        return Reading(
            **scope.filters(),
            read_as=PointShape("player_periods", "split", "period"),
            measures=_measured(measure),
            aggregate=Aggregate.PER_GAME,
            group="period",
            predicates=[],
            order="date",
            direction="asc",
            limit=None,
            available=SHOT_AVAILABILITY,
            subject_span=scope.span.over_career() if date else scope.span,
        )
    return Reading(
        **scope.filters(),
        read_as=PERIOD_LOG,
        measures=_measured(measure),
        aggregate=Aggregate.NONE,
        group="none",
        predicates=[],
        order="date",
        direction="asc" if scope.window.order == "first" else "desc",
        limit=_clamp_limit(scope.window.count, DEFAULT_GAME_LOG_LIMIT),
        available=SHOT_AVAILABILITY,
        subject_span=scope.span.over_career() if date else scope.span,
    )


def _default_threshold_count(scope: Filtered) -> Reading:
    """``threshold_count``'s default point: a count of a named player's games
    clearing one line - the threshold, or a below/above phrase that is the
    whole line ("Sga games with under 14 fta": the relation narrows by it,
    and the count is of the games left), read the one way the count's
    reader reads it (:func:`~association.query.lines.threshold_count_line`).
    A league-wide count is declined here; the point reader's own move reads
    it as a count by player."""
    col = stat_column(scope.measure)
    threshold = threshold_of(scope)
    if not _named_player_in(scope):
        raise Unsupported("a league-wide count is not on the one-player relation")
    if threshold is None and relation_lines(scope):
        # Refuses (PointRefused) by the fact missing: a phrase naming no
        # stat, a stat with no per-game column.
        threshold_count_line(scope)
        return Reading(**scope.filters(), read_as=GAMES_COUNTED, measures=(), aggregate=Aggregate.COUNT, group="none", predicates=[], available=BOX_SCORES)
    if col is None or threshold is None or threshold < 1:
        # The reason the count gives, where it has one (a threshold of 0
        # counts every game; no stat it keeps a line on), by its cause.
        threshold_count_line(scope)
        raise Unsupported("threshold_count refuses; nothing to compare")
    # A below/above phrase on the threshold's own number and stat - or
    # holding its words ("games with under 14 fta": the pair "14 fta" inside
    # the phrase, the model's stat another) - IS the count, misread as a
    # threshold; the count's sayer words it as the phrase. The number alone
    # is not: "30 points playing 30 or more minutes" holds a floor of
    # minutes at the same number, and the 30-point line was dropped for it.
    keyed = threshold_line(scope)
    the_count = any(line.value == threshold and (line.measure == col or (keyed is not None and keyed.as_typed in line.as_typed)) for line in relation_lines(scope))
    predicates = [] if the_count else [(col, ">=", threshold)]
    return Reading(**scope.filters(), read_as=GAMES_COUNTED, measures=(), aggregate=Aggregate.COUNT, group="none", predicates=predicates, available=BOX_SCORES)


def _default_single_game_high(scope: Filtered) -> Reading:
    """``single_game_high``'s default point: a named player's top games by
    one stat, the measure first. A league-wide high is the point reader's
    own move (a ranking of games), declined here. The decline names the
    fact that is missing - the stat, or the player - never both where one
    was given: "brice sensabaugh career high asistss" named its player."""
    col = stat_column(scope.measure)
    stat = _named_stat(scope)
    if col is None:
        raise PointRefused(Cause(kind="unknown_stat", facts={"shape": GAME_HIGHS, "stat": stat}) if stat else Cause(kind="needs_stat", facts={"shape": GAME_HIGHS}))
    if not _named_player_in(scope):
        raise Unsupported("single_game_high needs a named player here")
    return Reading(
        **scope.filters(),
        read_as=GAME_HIGHS,
        measures=_measured(col),
        aggregate=Aggregate.NONE,
        group="none",
        predicates=[],
        order="measure",
        direction="desc",
        limit=_clamp_limit(scope.window.count, DEFAULT_SINGLE_GAME_LIMIT),
    )


def _streak_season(scope: Filtered) -> int | None:
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


def _default_streak(scope: Filtered) -> Reading:
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
    (:func:`~association.query.compose.plan.cells_declined`).

    .. versionadded:: 5.0.0
       On the reader's side (``compose.adapt._adapt_streak`` was this).
    """
    threshold = threshold_of(scope)
    column = streak_column(scope.measure, threshold)
    want_win = scope.measure is None or scope.measure.won is not False
    predicates: list[tuple[str, str, Any]] = [(column, ">=", threshold)] if column is not None else [("won", "=", want_win)]
    if _named_player_in(scope):
        season = _streak_season(scope)
        return Reading(
            **scope.filters(),
            read_as=LINE_RUNS,
            measures=(),
            aggregate=Aggregate.NONE,
            group="none",
            predicates=predicates,
            limit=DEFAULT_NAMED_RUNS,
            available=BOX_SCORES,
            # The run's season settled outright: this one, never a
            # defaulted one, so an empty run is refused rather than
            # redirected to his last games.
            subject_span=replace(scope.span, season=season, career=season is None),
        )
    if scope.who.team is not None:
        if column is not None:
            raise PointRefused(Cause(kind="team_streak_of_stat", facts={"stat": column}))
        return Reading(**scope.filters(), read_as=_TEAM_RUN, measures=_measured("won"), aggregate=Aggregate.COUNT, group="none", predicates=predicates, relation="team")
    limit = _clamp_limit(scope.window.count, DEFAULT_STREAK_LIMIT)
    if column is not None:
        return Reading(**scope.filters(), read_as=LINE_RUNS, measures=(), aggregate=Aggregate.NONE, group="none", predicates=predicates, limit=limit, relation="everyone")
    return Reading(**scope.filters(), read_as=_TEAM_RUN, measures=_measured("won"), aggregate=Aggregate.COUNT, group="none", predicates=predicates, limit=limit, relation="team")


def _default_player_matchup(scope: Filtered) -> Reading:
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
    texts = list(dict.fromkeys(n.strip() for n in scope.who.players))
    if len(texts) != 2:
        raise PointRefused(Cause(kind="matchup_needs_two", facts={"names": texts}))
    dated = bool(scope.cuts.date)
    return Reading(
        **scope.filters(),
        read_as=PLAYER_MEETINGS,
        measures=(),
        aggregate=Aggregate.NONE,
        group="none",
        predicates=[],
        available=BOX_SCORES,
        subject_span=scope.span.over_career() if dated else scope.span,
    )


def _default_with_without(scope: Filtered) -> Reading:
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
    its words do not state is the planner's to decline (its shape's row,
    ``team_relation.TEAM_RELATION_SCOPING_EXCLUDED``).

    .. versionadded:: 5.0.0
       On the reader's side (``compose.adapt._adapt_with_without`` was this).
    """
    mate_texts, _asked_without, _roles = with_without_named(scope)
    if not mate_texts:
        texts = list(dict.fromkeys(n.strip() for n in scope.who.players))
        team_named = scope.who.team is not None
        if not ((team_named and len(texts) == 1) or (not team_named and len(texts) == 2)):
            raise Unsupported(f"with_without needs exactly one teammate, got {texts!r}")
    return Reading(**scope.filters(), read_as=PRESENCE_SPLIT, measures=_RECORD, aggregate=Aggregate.RECORD, group="presence", predicates=[], relation="team")


def _default_head_to_head(scope: Filtered) -> Reading:
    """``head_to_head``'s default point: two teams' meetings - a record on
    the team relation grouped by which team won each (``compose.meetings``).
    The two teams are the scope's ``teams``, ``team`` and ``opponent``, read
    by the meetings' reader; a narrowing its words do not state is the
    planner's to decline (``compose.plan.TEAM_SHAPES_STATED``).

    .. versionadded:: 5.0.0
    """
    return Reading(**scope.filters(), read_as=TEAM_MEETINGS, measures=_RECORD, aggregate=Aggregate.RECORD, group="opponent", predicates=[], relation="team")


def _default_team_quarter_points(scope: Filtered) -> Reading:
    """``team_quarter_points``' default point: a team's figure in one quarter
    or half - a total on the team relation over its games, the period the
    relation's own narrowing (``compose.periods.read_team_quarter_points``,
    which reads the stat, the period and the team from the scope and
    refuses what is not on the period's line).

    .. versionadded:: 5.0.0
    """
    return Reading(**scope.filters(), read_as=TEAM_PERIOD_TOTAL, measures=_measured("points"), aggregate=Aggregate.TOTAL, group="period", predicates=[], relation="team")


def _default_period_leaderboard(scope: Filtered) -> Reading:
    """``period_leaderboard``'s default point: the league's players (or one
    team's) ranked per game by a stat in one quarter or half, or by their
    points in each quarter - a grouped read by player on the league-wide
    relation, the period the relation's own narrowing
    (``compose.periods.read_period_leaderboard``).

    .. versionadded:: 5.0.0
    """
    return Reading(
        **scope.filters(),
        read_as=PERIOD_RANKING,
        measures=_measured("points"),
        aggregate=Aggregate.PER_GAME,
        group="player",
        predicates=[],
        order="measure",
        relation="everyone",
    )


def _default_team_game_high(scope: Filtered) -> Reading:
    """A team's best single game's default point: one team's games, or
    every team's where none is named, ranked by the figure the words name
    (points where they name none) - the team relation's rows by measure
    (``compose.highs.read_team_game_high``, over the team box row the
    figure is on). D4 lifted for it (Jeff, 2026-10-13; ISSUES.md #364).

    .. versionadded:: 6.0.0
    """
    measure = scope.measure if scope.measure is not None and scope.measure.key is not None else measured("points")
    return Reading(
        **scope.filters(),
        read_as=TEAM_GAME_HIGHS,
        measures=(measure,),
        aggregate=Aggregate.NONE,
        group="none",
        predicates=[],
        order="measure",
        relation="team",
    )


def _default_team_record(scope: Filtered) -> Reading:
    """``team_record``'s default point: a team's games won and lost - a
    record on the team relation (``compose.team_records``, which reads the
    team, the opponent, the span, the venue, a month or calendar narrowing,
    a game of each series and a split by month from the scope, and reads a
    plain regular season's from the standings).

    .. versionadded:: 5.0.0
    """
    return Reading(**scope.filters(), read_as=TEAM_RECORD, measures=_RECORD, aggregate=Aggregate.RECORD, group="none", predicates=[], relation="team")


def _default_player_netpoints(scope: Filtered) -> Reading:
    """``player_netpoints``' default point: one player's NetPoints - a season's
    ratings and the play-type split behind them, or one game's - on the
    NetPoints relation (``compose.netpoints``, which reads the player, the
    season, its type, the unit and a first or last game from the scope).

    .. versionadded:: 5.0.0
    """
    return Reading(**scope.filters(), read_as=NETPOINTS_RATINGS, relation="netpoints")


def _default_fingerprint(scope: Filtered) -> Reading:
    """``fingerprint``'s default point: one or more players' play-type
    fingerprints, drawn as one radar - a season's, or a first or last game's -
    on the NetPoints relation (``compose.netpoints``).

    .. versionadded:: 5.0.0
    """
    return Reading(**scope.filters(), read_as=NETPOINTS_FINGERPRINT, relation="netpoints")


def _default_shot_chart(scope: Filtered) -> Reading:
    """``shot_chart``'s default point: one player's located shots, drawn -
    a ``chart`` on the declared ``shots`` relation (``compose.shots``, which
    reads the player, the span, the games the relation narrows to and the
    shot value from the scope).

    .. versionadded:: 5.0.0
    """
    return Reading(**scope.filters(), read_as=SHOT_CHART, relation="shots")


def _default_shot_distance(scope: Filtered) -> Reading:
    """``shot_distance``'s default point: one player's average shot distance
    over the same shots - a ``scalar`` on the declared ``shots`` relation
    (``compose.shots``).

    .. versionadded:: 5.0.0
    """
    return Reading(**scope.filters(), read_as=SHOT_DISTANCE, relation="shots")


DEFAULT_POINTS: dict[PointShape, Callable[[Filtered], Reading]] = {
    PLAYER_LOG: _default_game_log,
    PLAYER_LINE: _default_player_stat,
    PLAYER_SPLITS: _default_player_splits,
    LINE_RECORD: _default_record_when,
    PERIOD_LOG: _default_period_split,
    GAMES_COUNTED: _default_threshold_count,
    GAME_HIGHS: _default_single_game_high,
    LINE_RUNS: _default_streak,
    PLAYER_MEETINGS: _default_player_matchup,
    PRESENCE_SPLIT: _default_with_without,
    TEAM_MEETINGS: _default_head_to_head,
    TEAM_PERIOD_TOTAL: _default_team_quarter_points,
    PERIOD_RANKING: _default_period_leaderboard,
    TEAM_RECORD: _default_team_record,
    TEAM_GAME_HIGHS: _default_team_game_high,
    NETPOINTS_RATINGS: _default_player_netpoints,
    NETPOINTS_FINGERPRINT: _default_fingerprint,
    SHOT_CHART: _default_shot_chart,
    SHOT_DISTANCE: _default_shot_distance,
}
"""What the words ask (the grammar's key) -> its default point, read by the reader itself (Phase 2, step 1:
slice (i)'s five; step 2: ``threshold_count``, ``single_game_high``, the
streak and the matchup; step 3: the with/without split, the last of the
adapters, which went with ``compose.adapt``; step 5: the NetPoints relation's
two and the shot relation's two, :data:`~association.query.reading.DECLARED_RELATIONS`). A key with no entry here
has its point read elsewhere in this module (:func:`read_point`) or none.

.. versionadded:: 5.0.0

.. versionchanged:: 6.0.0
   Keyed by the grammar's key (a :class:`~association.query.reading.PointShape`);
   by the intent's name until Phase 3, step 4.
"""


def default_point(asked: PointShape | None, scope: Filtered) -> Reading:
    """The point what the words ask (``asked``) means bare over ``scope``,
    stamped with the key it is the default of: the reader's own
    (:data:`DEFAULT_POINTS`). A key with none is declined.

    .. versionadded:: 5.0.0

    .. versionchanged:: 6.0.0
       Takes the grammar's key where it took an intent.
    """
    reader = None if asked is None else DEFAULT_POINTS.get(asked)
    if reader is None:
        # The sentence the adapters' table gave until it went, kept so no
        # reading's verdict moves with the file - the retired name, from
        # the one table.
        raise Unsupported(f"no adapter for {key_path(asked)}")
    return replace(reader(scope), asked=asked)


def _move_default(asked: PointShape | None, scope: Filtered, measure: str | None) -> Reading:
    """The asked key's default point, with the measure the question named added on."""
    base = default_point(asked, scope)
    if measure and measured(measure) not in base.measures:
        return replace(base, measures=(measured(measure), *base.measures) if base.read_as.shape == "rows" else (measured(measure),))
    return base


def _move_named(asked: PointShape | None, scope: Filtered, question: str) -> Reading:
    """The moves that apply once a player is named, tried in order - a
    single-game high, a career win count, a career boolean count, a
    per-season history, and finally the asked key's own default point. A
    quarter or half of a named player's games is ``period_split``'s own
    point (the retired template read its slots alone, so no word moves it);
    under any other key it is declined, since the point would print the
    game's figure under the quarter's heading."""
    if asked == PERIOD_LOG:
        return _move_default(asked, scope, None)
    if lexicon.PERIOD_GUARD.search(question) and not any(line.period is not None for line in scope.lines):
        # The quarter words are a condition's (read into the scope), or the
        # period relation's question.
        raise Unsupported("a quarter or half is the period relation's question")
    measure = _measure_for_named(scope, question)
    single = _move_single_game(asked, scope, question, measure)
    if single is not None:
        return single
    career = _career_scope(scope)
    how_many_won = _move_how_many_won(scope, question, measure, asked, career)
    if how_many_won is not None:
        return how_many_won
    boolean_count = _move_boolean_count(question, measure, asked, career)
    if boolean_count is not None:
        return boolean_count
    history = _move_player_history(asked, scope, career, measure)
    if history is not None:
        return history
    return _move_default(asked, scope, measure)


def _team_measure(scope: Filtered, question: str) -> Measure | None:
    """The team measure a question names - the question's own word first
    (the same priority :func:`_measure_for_named` gives a player's), the
    measure's key otherwise, when it is a column
    :data:`~association.query.measures.TEAM_GAME_MEASURES` or
    :data:`~association.query.measures.TEAM_SEASON_MEASURES` knows.

    .. versionadded:: 4.4.0
    """
    for pattern, word in lexicon.TEAM_WORD_MEASURES:
        if pattern.search(question):
            return _TEAM_WORD_KEYS.get(word) or measured(word)
    measure = scope.measure
    if measure is None or measure.key is None or named_by_a_team_metric(measure):
        # A team metric's alias text ("fgm", "ppg") names the team-season
        # relation's per-game line, never a total: the alias text
        # matched no column, and the per-game reader answers it as before
        # (ISSUES.md, "A team total named by a metric's alias").
        return None
    name: str | None = "points_allowed" if measure.key == "points" and measure.whose == "opponent" else measure.key if measure.whose == "own" else None
    if name is not None and (name in TEAM_GAME_MEASURES or name in TEAM_SEASON_MEASURES):
        return _TEAM_WORD_KEYS.get(name) or measured(name)
    return None


def _read_as(scope: Filtered, subject: Subject) -> Filtered:
    """``scope`` with the kind the parser read its subject as
    (``subject.kind``) on its typed subject: what the parser's last step
    writes (``subject.apply_subject``), for a caller that hands a scope and
    the subject reading apart."""
    return scope if scope.who.kind == subject.kind else replace(scope, who=replace(scope.who, kind=subject.kind))


def team_log_point(scope: Filtered, subject: Subject) -> Reading | None:
    """A ``game_log`` question about a team and no player - "show me the
    Knicks last 5 games" - as the team's own games listed: a ``rows`` point
    on the team relation, in date order, the window the question asked
    (``compose.logs.read_team_log`` reads it and ``compose.say`` says it in
    the retired template's words). The team is the typed subject's, or the one
    the subject reading found where the model dropped it. ``None`` where
    the question is not that: a named player (his own log, the player
    relation's), or a position group ("centers game log vs kings" - the
    template answered the KINGS' log; the league-wide read lists the
    centers' games against them, which is the question).

    .. versionadded:: 5.0.0
    """
    scope = _read_as(scope, subject)
    if _named_player_in(scope) or scope.who.kind in ("player", "pair", "position"):
        return None
    team_text = scope.who.team if scope.who.team != "any_team" else None
    if team_text is None:
        # The subject reading's own team, where the scope settled none (a
        # Reading built by hand: the parser's scope holds the reading's team).
        if scope.who.kind not in ("team", "team_players") or not subject.teams:
            return None
        team_text = subject.teams[0]
    return Reading(
        **(replace(scope, who=replace(scope.who, teams=(team_text,)))).filters(),
        read_as=PointShape("team_games", "rows", "date"),
        measures=_measured("points"),
        aggregate=Aggregate.NONE,
        group="none",
        predicates=[],
        order="date",
        direction="asc" if scope.window.order == "first" else "desc",
        limit=_clamp_limit(scope.window.count, DEFAULT_GAME_LOG_LIMIT),
        relation="team",
    )


def team_splits_point(scope: Filtered, subject: Subject) -> Reading | None:
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
    return Reading(**log.filters(), read_as=PointShape("team_games", "split", "splits"), measures=_measured("points"), aggregate=Aggregate.RECORD, group="venue", predicates=[], relation="team")


def team_read_point(scope: Filtered, question: str, subject: Subject) -> Reading | None:
    """Whether ``question``/``scope`` name a team as the grammatical
    SUBJECT - no player, a team identifiable (the typed subject's team,
    or :func:`~association.query.subject.team_named_in` when the model
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
       ``lexicon.NOT_THE_PLAYERS_QUESTION`` guard already gives this exact question ("allowed" is
       in it) - through a noisier path, for no different an outcome.

    .. versionchanged:: 5.0.0
       Takes the typed :class:`~association.query.reading.Filtered` and the
       :class:`~association.query.subject.Subject` the parser read, in place
       of a slot dict and a subject read here for a caller with none.

    .. versionchanged:: 5.0.0
       Takes no connection: the reader reads names from the in-memory
       index and asked the warehouse nothing here (the ``con`` was carried
       and never used).
    """
    scope = _read_as(scope, subject)
    if (
        _named_player_in(scope)
        or lexicon.PERIOD_GUARD.search(question)
        or lexicon.RANKING_WORDS.search(question)
        or lexicon.LOG_OR_STATS_WORDS.search(question)
        or lexicon.PLAYERS_ON_A_TEAM.search(question)
    ):
        return None
    if scope.who.kind == "team_players":
        # A team's players ("top scorers on the Lakers") are a ranking of
        # players narrowed by team, never the team's own figure.
        return None
    team_text = scope.who.team
    if team_text is None or team_text == "any_team":
        # The model dropped the team (F127's shape; the router did too): the
        # subject reading has it, from the question's own team word.
        if scope.who.kind not in ("team", "team_players") or not subject.teams:
            return None
        scope = replace(scope, who=replace(scope.who, teams=subject.teams[:1]))
    measure = _team_measure(scope, question)
    if measure is None:
        return None
    # A sum no reader takes (the team compiler's own sentence), unless the
    # words ask a team's own season, which read_point names.
    return Reading(**scope.filters(), read_as=PointShape("team_games", "scalar", ""), measures=(measure,), aggregate=Aggregate.TOTAL, relation="team")


TEAM_SEASON_POINTS: frozenset[PointShape] = frozenset({TEAM_LINE, TEAM_SEASON_HISTORY, TEAM_RANKING, TEAM_OUTLOOK})
"""What the words ask that a team's own season answers - each key the shape of that point:
one team's line on ``team_season_stats`` (``team_stat``), every team ranked
by one metric of it or of the standings (``team_leaderboard``), and one
team's place in ESPN's power index (``team_outlook``). Read where the
question's words read no other point for the key - and a point the words
DO read under one of these keys (a team's own total, "how many 3-pointers
have the Magic made", :func:`team_read_point`) is read and said as this
shape too (:func:`read_point`): the team-season reader is tried first and
the team compiler's sum answers where it declines (``compose.answer``), as
the retired template was tried before the compiler.

.. versionadded:: 5.0.0

.. versionchanged:: 6.0.0
   A :class:`~association.query.reading.PointShape` per intent, which the
   point carries (Phase 3, step 1); the relation and the compiler's skeleton
   until then. The grammar's keys themselves since Phase 3, step 4: what the
   words ask IS the point's shape.
"""


def _team_season_key(reading: Reading, question: str) -> PointShape:
    """The team-season shape a point is read and said as: what the words
    ask, except a team's season line over more than one season - a range
    or every season on record, the span the words name - which is its
    history season by season (:data:`TEAM_SEASON_HISTORY`, ISSUES.md #372:
    "how many games did miami heat play 2009 to 2023" refused the range a
    season line reads one season of). Not where the words rank or keep a
    line ("most points in the history of the 76ers" is a high, "games with
    exactly 99 points since 2000" a count), which stay refused by the
    season line's own cell."""
    assert reading.asked is not None
    if reading.asked != TEAM_LINE:
        return reading.asked
    ranged = reading.span.career or reading.span.since is not None
    if ranged and not reading.lines and not lexicon.RANKING_WORDS.search(question):
        return TEAM_SEASON_HISTORY
    return TEAM_LINE


def team_season_point(asked: PointShape, scope: Filtered) -> Reading:
    """The point on its team-season relation for what the words ask (``asked``, one of
    (:data:`TEAM_SEASON_POINTS`), over ``scope`` as the question settled it:
    the reader names the relation and the shape, and reads no metric - which
    of ``team_metrics.TEAM_METRICS`` the ``stat`` slot names, and what a
    word it does not know is refused with, is the relation's reader's
    (``compose.team_stats``), as it was the retired templates'.

    .. versionadded:: 5.0.0

    .. versionchanged:: 6.0.0
       Takes the grammar's key, which is the point's shape.
    """
    key = asked
    relation: Literal["team_seasons", "team_snapshots"] = "team_snapshots" if key.relation == "team_snapshots" else "team_seasons"
    return Reading(**scope.filters(), read_as=key, relation=relation)


def read_point(reading: Reading, question: str) -> Reading:
    """The default point of what ``reading``'s words ask, moved by ``question``'s own words: a
    measure beyond a retired template's list, a skeleton move ("most ... in a game" =
    rows by measure; "how many ... won" = count with a predicate) - none of it
    through a prompt edit. A team named with no player
    (:func:`team_read_point`) is tried before the league-wide reading, since a
    team's own total or differential is a narrower, more specific claim than
    "no player subject" - the same priority a named player already gets over
    the league-wide read. Called by the parser, once
    (:func:`~association.query.parse.with_point`, from its last step); the
    compiler plans and runs the Reading it returns.

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

    .. versionchanged:: 6.0.0
       The three are one field, ``Reading.read_as``, a ``PointShape``
       (Phase 5, step 4, D9).
    """
    subject = reading.subject
    if subject is None:
        # Who the question is about is read once, by the parser; a Reading
        # with none is a caller's mistake, said here rather than as an
        # AttributeError three moves down.
        raise ValueError("read_point needs the reading's subject - who the question is about, as the parser read it")
    # Who the question is about as the parser read it - its kind and its
    # position group - on the typed subject the point reads: the parser's
    # last step wrote them there (subject.apply_subject), and a Reading
    # built by hand with a subject of its own gets the same here.
    scope = replace(reading, who=replace(reading.who, kind=subject.kind, position=subject.position))
    _player_rating_refused(reading.asked, question)
    _player_win_pct_refused(reading.asked, question)
    scope = _season_lines_refused(reading.asked, question, scope)
    try:
        point = _players_counted(scope, question, subject) if reading.asked == PLAYERS_COUNTED else _seasons_counted(_read_point(reading.asked, scope, question, subject), reading.asked, question)
    except Unsupported:
        if reading.asked not in TEAM_SEASON_POINTS:
            raise
        # No other reading of the point: the team's own season is the
        # question (Phase 2, step 4). Until then this was a decline - "a
        # team's own question is not the player relation's" - which the
        # retired template answered past.
        point = team_season_point(reading.asked, replace(scope, who=scope.who.without_position()))
    _minimum_honored(scope, point)
    _lines_name_a_stat(scope)
    if reading.asked in TEAM_SEASON_POINTS:
        # Whatever the words moved (a team's own total on the team relation),
        # the point is read and said as the team's own season's shape:
        # that reader first, the team compiler's sum where it declines.
        key = _team_season_key(reading, question)
        point = replace(point, read_as=key)
    return replace(point, asked=reading.asked, subject=subject, evidence=(*point.evidence, *subject.evidence))


def _leaderboard_declines(scope: Filtered, subject: Subject) -> None:
    """What a ``leaderboard`` point is not, declined before any move - split
    out of :func:`_read_point` to keep it inside the complexity gate."""
    if scope.measure is not None and scope.measure.key in ("triple_double", "double_double") and scope.who.kind in ("team", "team_players") and not subject.players:
        # A TEAM's total of its players' triple-doubles ("oklahoma city
        # thunder all-time triple doubles vs west", leaderboard with the team
        # filed - day5): not a ranking this relation lacks a measure for, but
        # a team aggregate nothing reads. Declined here, where the subject
        # is in hand (the router-era `repair` read "vs west" as the team's
        # opponent here),
        # so the reading's own cause names it (``team_boolean_count``,
        # Reading.unsupported, said where the answer side declines) rather
        # than the ranking's sentence naming the wrong one.
        raise Unsupported(f"a team's total of its players' {scope.measure.as_typed} is not read")
    if _named_player_in(scope):
        # A leaderboard ranks the league or a team, never one named person -
        # the retired template's own refusal ("Klay Thompson's 3pt percentage
        # over the past 4 seasons" once landed here and came back with the
        # league's true-shooting leaders, Klay silently dropped).
        raise Unsupported(f"a leaderboard cannot answer about one named player ({scope.who.player!r})")


def _team_record_own_line(scope: Filtered) -> Filtered:
    """A team's record over a line under a number - "warriors' record when
    committing 10 or fewer turnovers", "... when they had under 12
    turnovers" (ISSUES.md #325) - keyed on that line with its comparison,
    where the tagger read it as a narrowing beside a keyed bare "12
    turnovers" (or beside no keyed line at all): the record splits the
    team's games at the line, as it does at "120 points", and the team's
    record reader reads the comparison (``compose.team.compile_team_line``'s
    ``op``). One line under a number on a stat, and no other keyed line
    than its own number's."""
    below = [line for line in scope.lines if line.below and line.narrows and line.period is None and line.measure is not None]
    keyed = [line for line in scope.lines if line.keyed]
    if len(below) != 1 or any(line.as_typed not in below[0].as_typed for line in keyed):
        return scope
    own = replace(below[0], keyed=True, narrows=False)
    return replace(scope, lines=tuple(own if line is below[0] else line for line in scope.lines if not line.keyed))


def _team_line_measure(scope: Filtered) -> Measure:
    """The measure a team's record over its own line is said by: the one
    the words named, by its catalog key, or points (``compose.records``
    reads the line itself from the scope)."""
    measure = scope.measure
    return Measure(key=measure.key, whose=measure.whose) if measure is not None and measure.key is not None else measured("points")


def _player_rating_refused(asked: PointShape | None, question: str) -> None:
    """Refuse a player's offensive, defensive or net rating by name - "anthony
    edwards defensive rating 2025" answered his points line (28 of the 2,082
    feed questions, found fixing ISSUES.md #351): the ratings are kept for
    teams alone (``team_metrics``), and a player's NetPoints is the figure
    here that rates his defense. Not where the words name a team."""
    if asked is None or not asked.relation.startswith("player_"):
        return
    rating = lexicon.PLAYER_RATING.search(question)
    if rating is not None and lexicon.TEAM_WORD_HERE.search(question) is None:
        raise PointRefused(Cause(kind="fact_not_read", facts={"fact": "player rating", "words": rating.group(0)}), f"{rating.group(0)!r} is a team's figure")


def _player_win_pct_refused(asked: PointShape | None, question: str) -> None:
    """Refuse a ranking of players by win percentage by name - "player with
    worst win percentage" ranked the league's scorers (ISSUES.md #428): a
    player's record is his team's in the games he played, read for one
    player (his record, his splits, with and without a teammate), and no
    ranking of players reads it. With no player in the words it is the
    teams' standings (``lexicon.LEAGUE_WIN_PCT_ROW``), never asked here."""
    if asked != PLAYER_RANKING:
        return
    named = lexicon.WIN_PCT.search(question)
    if named is not None:
        raise PointRefused(Cause(kind="fact_not_read", facts={"fact": "player win percentage", "words": named.group(0)}), f"{named.group(0)!r} ranks no players")


def _seasons_counted(point: Reading, asked: PointShape | None, question: str) -> Reading:
    """A ranking of players by how many of their seasons a season line holds
    - "most seasons averaging 30 ppg", "most 25 point seasons", "most
    seasons with 2000 points" (ISSUES.md #391) - the ranking's point ordered
    by the count of seasons (``by="seasons"``), as a count of games over a
    line ranks by ``games``: what one row is stays a player, the words ask a
    ranking, and only what it is ordered by moves. The ranking's point
    otherwise."""
    if asked != PLAYER_RANKING or point.read_as.relation != "player_seasons" or point.read_as.shape != "ranking" or lexicon.SEASONS_COUNTED.search(question) is None:
        return point
    if not any(line.per != "game" for line in point.lines):
        return point
    return replace(point, read_as=replace(point.read_as, by="seasons"))


def _players_counted(scope: Filtered, question: str, subject: Subject) -> Reading:
    """How many players a season line holds (ISSUES.md #390): the ranking's
    point, counted - a scalar ``by="count"`` on the season line - where the
    ranking is the season line's and a season line stands; the ranking's
    own point otherwise (no line to count past, or a ranking the game
    level reads, which declines the line by name)."""
    point = _read_point(PLAYER_RANKING, scope, question, subject)
    if point.read_as.relation == "player_seasons" and point.read_as.shape == "ranking" and any(line.per != "game" for line in scope.lines):
        return replace(point, read_as=PLAYERS_COUNTED)
    return point


def _read_point(asked: PointShape | None, scope: Filtered, question: str, subject: Subject) -> Reading:
    """:func:`read_point`'s moves, in order; split out so the record carries
    the asked key and the subject whichever move settled it. A position group
    narrows the league's own read alone (:func:`_everyone_point`, the
    ``position`` its moves carried as a field of the point until Phase 3,
    step 2): every other move reads the scope without it, so the point it
    builds carries none."""
    positioned, scope = scope, replace(scope, who=scope.who.without_position())
    if asked == TEAM_COACH:
        # No table here holds a coach: the reading's verdict, said by the
        # planner in the retired template's words (compose.plan).
        raise PointRefused(Cause(kind="no_coach_table", facts={"unanswerable": "coach"}))
    if on_a_declared_relation(asked):
        # A declared relation's own point, whatever the words: the retired
        # templates read their slots alone, and a player's games are not
        # where a NetPoints rating, a fingerprint or a shot chart is read
        # (Phase 2, step 5).
        return default_point(asked, scope)
    if asked == PLAYER_RANKING:
        _leaderboard_declines(scope, subject)
    if asked == LINE_RECORD and not _named_player_in(scope) and scope.who.team is not None:
        # A team's record above and below its OWN line - "what was the celtics
        # record when they scored 120 points" (ISSUES.md #144) - is neither the
        # season sum nor the window sum the team subject otherwise reads: it is
        # record_when's team reader's (compose.records.read_team_record_when).
        scope = _team_record_own_line(scope)
        return Reading(**scope.filters(), read_as=PointShape("team_games", "split", "line"), measures=(_team_line_measure(scope),), aggregate=Aggregate.RECORD, relation="team")
    if asked == PLAYER_LOG:
        # A team's log, before the team's sums: "knicks last 5 games" lists
        # them (the retired template's team half, ROADMAP plan item 6, step
        # (g)); "total points scored by the raptors in the last 10 games" is
        # routed here too and lists them with the total stated beneath, as
        # the template did.
        team_log = team_log_point(scope, subject)
        if team_log is not None:
            return team_log
    if asked == PLAYER_SPLITS:
        team_splits = team_splits_point(scope, subject)
        if team_splits is not None:
            return team_splits
    if asked == PLAYER_COMPARISON:
        return _compare_point(scope)
    if asked in (LINE_RUNS, PLAYER_MEETINGS, PRESENCE_SPLIT, TEAM_MEETINGS, TEAM_PERIOD_TOTAL, PERIOD_RANKING, TEAM_RECORD, TEAM_GAME_HIGHS):
        # The retired templates read their slots alone, so no word moves the
        # point: a player's, a team's or the league's longest run
        # (_default_streak), two players' meetings
        # (_default_player_matchup), a team's record with and without a
        # teammate (_default_with_without), two teams' meetings
        # (_default_head_to_head), a team's quarter or half
        # (_default_team_quarter_points), the league's ranking by one
        # (_default_period_leaderboard), or a team's record
        # (_default_team_record).
        return default_point(asked, scope)
    if not _named_player_in(scope):
        team_reading = team_read_point(scope, question, subject)
        if team_reading is not None:
            return team_reading
    # The parser's own scope: nothing here repairs it (the filler word, the
    # team in `player`, the dropped subject and the opponent player never
    # reach here from the parser - measured over the 628 questions with a
    # recorded normalizer reply).
    if not _named_player_in(scope):
        if asked == LINE_RECORD:
            # A record "when" is a player's line or a team's own (the team
            # branch above); naming neither, it has nobody to read - the
            # reason record_when's retired template gave.
            raise PointRefused(Cause(kind="needs_subject", facts={"shape": LINE_RECORD}))
        # The league's read, the one point a position group narrows: the
        # scope as read, its position with it.
        return _everyone_point(asked, positioned, question, stat_measure(scope.measure))
    return _move_named(asked, scope, question)


# --- The key's reading of what the taggers read ----------------------------------
#
# Phase 3, step 4, slice (b): the taggers read the words alone - every line,
# window, span, period, cut and measure word the question holds, with no key
# - and the key the words ask (``Reading.asked``) decides here which of them
# stand, one function per family, where the parser writes the Reading
# (``parse._tagged``). They change what the Reading SAYS - a bare "30 points"
# is the count's own line and nothing on a log, a quarter is kept on a
# period shape alone - which is the point reader's to read; the planner's
# check is whether a relation honors a cell (``compose.plan.cells_unhonored``),
# and moving these there would move every recorded reading. Measured before
# the move on the 2,710 readings (``~/association-research/stages/route_family.py``):
# the key moved the lines on 124 readings, the measure on 176, the period on
# 120, the window on 150, the span on 219, the cuts on none.

PERIOD_ASKS: frozenset[PointShape] = frozenset({PERIOD_LOG, PERIOD_RANKING, TEAM_PERIOD_TOTAL})
"""The keys that take a quarter or half: a named player's figure in one,
the league's ranking by one, a team's own. A period the words name is kept
under these alone (:func:`period_under`); on any other key the words are
declined by the point reader's guard.

.. versionadded:: 6.0.0
   The period tagger's (``period.PERIOD_ASKS``) until Phase 3, step 4,
   slice (b).
"""

THRESHOLD_ASKS: frozenset[PointShape] = frozenset({GAMES_COUNTED, LINE_RECORD, LINE_RUNS, GAME_HIGHS})
"""The keys whose shape is a line on a stat: a count of games over it, a
record above and below it, a run of games holding it, a high ranked by its
stat. Under these a bare "30 point games" is a line and the first line the
words state is the shape's own (keyed); under any other a line stands only
where the words say so outright - a floor of minutes, a line under a
number, two or more "N+ stat" pairs on one game (:func:`lines_under`).

.. versionadded:: 6.0.0
   The lines tagger's (``line.THRESHOLD_ASKS``, ``router._THRESHOLD_ASKS``
   before it) until Phase 3, step 4, slice (b).
"""

#: The keys under which exactly one "N+ stat" pair names the measure: the
#: count, assigned from the words under a parent whose stat the model
#: filed, and the record.
_LINES_PAIR_NAMES_THE_STAT: frozenset[PointShape] = frozenset({LINE_RECORD, GAMES_COUNTED})

#: The keys of a player's games that narrow by ONE line stated outright -
#: "N+ stat", "N or more stat", "at least N stat" - as they narrow by two:
#: "lebron game log with 20+ points" is his 20-point games (ISSUES.md #357).
#: A bare "N stat" stays unread there ("harden 61 points" can as well name
#: one game), and a player's line is not among them.
_LINES_ONE_NARROWS: frozenset[PointShape] = frozenset({PLAYER_LOG, PLAYER_SPLITS})

ORDER_ASKS: frozenset[PointShape] = frozenset({NETPOINTS_FINGERPRINT, PLAYER_LOG, PERIOD_LOG, NETPOINTS_RATINGS, SHOT_CHART, SHOT_DISTANCE, TEAM_PERIOD_TOTAL})
"""Keys whose reader honors ``order``, so filling it from the question can
only make the answer match what was asked: on these the end of the span is
read from :data:`~association.query.lexicon.ORDER_WORDS` where the grammar
read none, and on every other key an end the grammar read beside a count of
more than one is dropped, since the reader would refuse the question for it
(:func:`window_under`).

.. versionadded:: 2.1.0

.. versionchanged:: 4.4.0
   Added ``team_quarter_points``: it reads its games through the team-games
   relation, which honors a window.

.. versionchanged:: 6.0.0
   The point reader's (the window tagger's until Phase 3, step 4, slice
   (b), ``router.ORDER_ASKS`` before it).
"""

#: Keys that honor an end of the span only beside a real count - one game
#: at one end of it - because filling the end alone would hand "his last
#: game" to a log of his last ten.
_WINDOW_ORDER_ON_A_SINGLE_GAME: frozenset[PointShape] = frozenset({PLAYER_LINE})

#: Keys where an end of the span narrows to ONE game rather than ordering a
#: list (each resolves it to a single event id): an end read beside a count
#: of two or more costs a whole season here (#153), so the question has to
#: name a game at one end of the span for it to stand.
_WINDOW_ORDER_IS_ONE_GAME: frozenset[PointShape] = frozenset({SHOT_CHART, SHOT_DISTANCE, NETPOINTS_RATINGS, NETPOINTS_FINGERPRINT})

#: The keys that honor an end and read one game named as the subject's own
#: ("his last home game") where the grammar's rows miss it - not the two
#: period readers, whose quarter's ordinal sits where SINGLE_GAME allows two
#: words.
_WINDOW_ONE_GAME_NAMED: frozenset[PointShape] = ORDER_ASKS - {PERIOD_LOG, TEAM_PERIOD_TOTAL}

#: The keys whose ranking has an end the question names ("most", "fewest",
#: "best", "worst"): a team ranking, and a team's single best quarter or half.
_WINDOW_RANKED: frozenset[PointShape] = frozenset({TEAM_RANKING, TEAM_PERIOD_TOTAL})

LIMIT_COUNTS_SEASONS: frozenset[PointShape] = frozenset({SEASON_HISTORY})
"""Keys whose count is of SEASONS rather than games, so a relative span
("the past 5 years") is that count (:func:`window_under`) and not a range
of seasons (:func:`span_under`).

.. versionadded:: 6.0.0
   The span tagger's (``span.LIMIT_COUNTS_SEASONS``) until Phase 3, step 4,
   slice (b).
"""

_MEASURE_ADVANCED: frozenset[PointShape] = frozenset({PLAYER_LINE, PLAYER_COMPARISON, SEASON_HISTORY, PLAYER_RANKING, PLAYER_LOG})
#: Game score's spelling, by the reader: the ranking reads it through the
#: leaderboard metrics (`avg_game_score`), a player's line through the
#: advanced stats (`game_score`).
_MEASURE_GAME_SCORE: dict[PointShape, str] = {PLAYER_RANKING: "avg_game_score", PLAYER_LINE: "game_score"}
_MEASURE_TWO_POINT_PCT: frozenset[PointShape] = frozenset({SEASON_HISTORY, PLAYER_LINE})
_MEASURE_SHOT_VALUE: frozenset[PointShape] = frozenset({SHOT_DISTANCE, SHOT_CHART})


def _ranked_as(asked: PointShape | None) -> PointShape | None:
    """The key the words' families are read under: a count of the players a
    season line holds (:data:`~association.query.reading.PLAYERS_COUNTED`)
    reads every family as the ranking it counts does - its measure, its
    window, its span, its lines (ISSUES.md #390)."""
    return PLAYER_RANKING if asked == PLAYERS_COUNTED else asked


def period_under(asked: PointShape | None, read: PeriodRead) -> PeriodRead:
    """The period the words name, kept under a key that takes one
    (:data:`PERIOD_ASKS`); the whole game and no claim otherwise.

    .. versionadded:: 6.0.0
    """
    asked = _ranked_as(asked)
    return read if asked in PERIOD_ASKS else PeriodRead(None, ())


def cuts_under(asked: PointShape | None, read: CutsRead, *, split: str | None) -> CutsRead:
    """The cuts under the key: a venue beside the splits reader's venue
    split is the split's half, not a cut, and is dropped there; the
    characters read joined once (:func:`~association.query.span.claimed`).

    .. versionadded:: 6.0.0
    """
    asked = _ranked_as(asked)
    venue_split = asked == PLAYER_SPLITS and split == "home_away"
    cuts = replace(read.cuts, venue=None) if venue_split else read.cuts
    kept = () if venue_split or read.venue_claim is None else (read.venue_claim,)
    return CutsRead(cuts, claimed([*kept, *read.claims]), read.dated_since, dated_until=read.dated_until, dated_both=read.dated_both)


def lines_under(asked: PointShape | None, read: LineCandidates, *, beside_line: bool = False, reached: tuple[tuple[int, int], ...] = ()) -> LinesRead:
    """The subject's lines under the key, in the question's order, with
    their claims: fouling out on a count; under a shape that is a line
    (:data:`THRESHOLD_ASKS`) every "N stat" pair, the first the shape's own
    (keyed), and "scores N" where no pair stands; elsewhere two or more "N+
    stat" pairs on one game, which narrow the games together, or the one
    stated outright on a player's log or splits (never a line a companion
    beside him reached, ``beside_line``); a floor of minutes and a line
    under a number on every key; and the measure one "N+ stat" pair names
    under a count or a record.

    .. versionadded:: 6.0.0
       ``line.read_lines``' choice until Phase 3, step 4, slice (b).
    """
    asked = _ranked_as(asked)
    found: list[tuple[int, Line, Claim]] = []
    if asked == PLAYER_RANKING:
        read, found = _lines_season(read)
    if asked == GAMES_COUNTED:
        # Fouling out is the count's own line (fouls at six), whatever else
        # the words number - the grammar's rules chose the count by it.
        found.extend((candidate.start, candidate.line, candidate.claim) for candidate in read.of("fouled"))
    # A pair inside a reached companion's phrase (``reached``, the phrases'
    # spans) is his line, never the subject's, on every key (#359).
    pairs = [candidate for candidate in read.of("plus", "at_least", "bare") if not any(start <= candidate.start < end for start, end in reached)]
    found.extend(_lines_pairs(asked, pairs, beside_line=beside_line))
    stat: str | None = None
    scored = read.of("scored")
    if asked in THRESHOLD_ASKS and scored:
        # "scores 30": the verb names the stat, whatever the model filed.
        found.append((scored[0].start, scored[0].line, scored[0].claim))
        stat = "points"
    found.extend((candidate.start, candidate.line, candidate.claim) for candidate in _lines_phrases(asked, read, has_pair=bool(pairs or scored)))
    stat = _lines_stat(asked, pairs, has_phrase=bool(read.of("phrase", "stated")), stat=stat)
    found.sort(key=lambda each: each[0])
    return LinesRead(tuple(line for _, line, _ in found), tuple(claim for _, _, claim in found), stat)


def _lines_season(read: LineCandidates) -> tuple[LineCandidates, list[tuple[int, Line, Claim]]]:
    """On a ranking of players' seasons, the lines on the season's figure
    ("averaged 30 ppg", "> 5 apg": ISSUES.md #390) with where each stands
    and its claim - and the candidates left once the game lines read inside
    them are set aside ("30 points" of "averaged 30 points", "under 10
    points" of "averaged under 10 points": the season's line, not a game's,
    which the ranking read before averaging until 2026-10-13)."""
    season = read.of("season")
    if not season:
        return read, []
    inside = [(each.claim.start, each.claim.end) for each in season]
    rest = tuple(each for each in read.found if each.how == "season" or not any(start <= each.claim.start and each.claim.end <= end for start, end in inside))
    return LineCandidates(rest), [(each.start, each.line, each.claim) for each in season]


def _lines_phrases(asked: PointShape | None, read: LineCandidates, *, has_pair: bool) -> list[LineCandidate]:
    """The floors of minutes and the lines under a number, on every key -
    and under a record over a line with no "N stat" pair beside it, a lone
    floor is the record's own line ("his record when playing 30 or more
    minutes" splits his games at 30), keyed and narrowing nothing, which a
    narrowing would empty of the games under it: it was refused "none was
    read" until 2026-10-11."""
    # A line stated over or at a number ("more than 40 points", "exactly 129
    # three pointers") on a ranking of players' seasons is a season's line,
    # not a game's (ISSUES.md, "A season average stated as a line on the
    # league"): read by nothing there, as it was.
    phrases = read.of("phrase") if asked == PLAYER_RANKING else read.of("phrase", "stated")
    if asked in THRESHOLD_ASKS and not has_pair:
        phrases = _lines_phrases_over_keyed(phrases)
    if asked == LINE_RECORD and not has_pair and len(phrases) == 1 and not phrases[0].line.below and phrases[0].line.measure is not None:
        return [replace(phrases[0], line=replace(phrases[0].line, keyed=True, narrows=False))]
    return phrases


def _lines_phrases_over_keyed(phrases: list[LineCandidate]) -> list[LineCandidate]:
    """Under a shape that is a line with no "N stat" pair, the first line
    stated over a number on a stat the shape keys on - "more than 40
    points", "above 10 assists" (ISSUES.md #371) - is the shape's own, at
    or over the next whole number: a count, a record, a run and a high
    each read their line at or over its number, and a stat is a count, so
    "more than 40" is "41+" and the answer says so. A floor of minutes
    stays the relation's, as it was."""
    for at, candidate in enumerate(phrases):
        line = candidate.line
        if line.op == ">" and line.measure in THRESHOLD_STAT_NAMES and line.measure != "minutes" and not isinstance(line.value, bool):
            keyed = replace(line, op=">=", value=int(line.value) + 1, keyed=True, narrows=False)
            return [*phrases[:at], replace(candidate, line=keyed), *phrases[at + 1 :]]
    return phrases


def _lines_stat(asked: PointShape | None, pairs: list[LineCandidate], *, has_phrase: bool, stat: str | None) -> str | None:
    """The measure a line names under a count or a record: the one "N+ stat"
    pair's, or the keyed pair's beside a floor or a ceiling on another stat
    ("how many games did wemby have 30 points playing 30 or more minutes"
    counted his 30-minute games, the longer word "minutes" read as the
    measure over the line's "points"); ``stat`` (the scoring verb's) or
    None otherwise."""
    if asked not in _LINES_PAIR_NAMES_THE_STAT:
        return stat
    plus = [candidate for candidate in pairs if candidate.how == "plus"]
    if len(plus) == 1:
        return plus[0].line.measure
    if stat is None and pairs and (has_phrase or (asked == GAMES_COUNTED and len(pairs) > 1 and not plus)):
        # A count over several bare lines counts its first line's stat ("most
        # games with 20 pt,s 10 reb, 5 ast" is points, where the longest
        # box-score word read "reb"; ISSUES.md #358).
        return pairs[0].line.measure
    return stat


def _lines_pairs(asked: PointShape | None, pairs: list[LineCandidate], *, beside_line: bool) -> list[tuple[int, Line, Claim]]:
    """The "N stat" pairs :func:`lines_under` keeps, each with where it
    stands and its claim. Under a shape that is a line, every pair, the
    first the shape's own (keyed); elsewhere two or more "N+ stat" pairs on
    one game, which the relation narrows by together - one alone is read by
    nothing there - or ONE stated outright ("N+ stat", "at least N stat")
    on a player's log or splits (:data:`_LINES_ONE_NARROWS`), never a line a
    companion beside him reached ("splits when tatum scores 30+ points" is
    Tatum's line). A pair standing inside the phrase of a companion who
    REACHED it is his line on every key, the record's and the count's too
    (``reached``, the phrases' spans): "maxey record when embiid scores 20+
    points" keyed the record on Maxey's 20+ as well as Embiid's (ISSUES.md
    #359); a phrase whose companion the rules restored as the subject
    himself hands no span, so "how many games with 40+ points did Lebron
    James have" keeps its count."""
    lined = asked in THRESHOLD_ASKS
    plus = [candidate for candidate in pairs if candidate.how == "plus"]
    stated = [candidate for candidate in pairs if candidate.how in ("plus", "at_least")]
    one = asked in _LINES_ONE_NARROWS and not beside_line and len(pairs) == 1 and len(stated) == 1
    narrowing = stated if one else plus
    kept: list[tuple[int, Line, Claim]] = []
    for candidate in pairs:
        narrows = candidate in narrowing and (len(narrowing) > 1 or one)
        if lined or narrows:
            kept.append((candidate.start, replace(candidate.line, keyed=lined and candidate is pairs[0], narrows=narrows), candidate.claim))
    return kept


def minimum_under(asked: PointShape | None, read: MinimumRead) -> MinimumRead:
    """The qualifier the words state, under the key: kept where the words
    ask for players ranked by a measure (:data:`~association.query.reading.PLAYER_RANKING`,
    whose rankings apply it - :func:`_leaderboard_season_line`,
    :func:`_everyone_ranking` - or refuse it by name), and read by nothing
    on any other key, its words left unread, as a line no reader of the key
    reads is (ISSUES.md #377, 2026-10-11).

    .. versionadded:: 6.0.0
    """
    asked = _ranked_as(asked)
    return read if asked == PLAYER_RANKING else MinimumRead(None)


def _season_lines_refused(asked: PointShape | None, question: str, scope: Reading) -> Reading:
    """A ranking of players' seasons kept past a line on the season's figure
    ("how many players averaged 30 ppg in 2026", "players to average 16
    points 10 rebounds": ISSUES.md #390) reads its season lines - a line
    whose words name no stat ("average 25+") on the ranking's own measure -
    and refuses what it cannot hold by a cause naming the words: a line on
    a figure the season line holds no column of (``season_line_unread``,
    ``metrics.SEASON_FIGURES``), a percentage beside one ("shot 54% or
    better"), the words of a season line no line was read from, and a
    count of a player's seasons over one ("most seasons averaging 30 ppg":
    ``seasons_counted``, ISSUES.md #391). Each answered the unfiltered
    ranking until 2026-10-13."""
    if _ranked_as(asked) != PLAYER_RANKING:
        return scope
    season = [line for line in scope.lines if line.per != "game"]
    counted = lexicon.SEASONS_COUNTED.search(question)
    if counted is not None and not season:
        # A count of seasons over a line no season line was read from ("most
        # 60 win seasons", "most seasons with a per of 30"): nothing here
        # holds the figure, said by name (ISSUES.md #391).
        raise PointRefused(Cause(kind="seasons_counted", facts={"words": counted.group(0)}), f"{counted.group(0)!r} counts seasons over a line")
    if not season:
        return scope
    scope = _season_lines_measured(scope, season)
    _season_lines_unheld(question, [line for line in scope.lines if line.per != "game"])
    return scope


def _season_lines_measured(scope: Reading, season: list[Line]) -> Reading:
    """The season lines on the ranking's own measure where their words name
    none, and the ranking's measure - where the words name none beside the
    line ("how many players averaged 30 ppg") - the first line's stat, or
    points ("average 25+"), as a ranking with no stat ranks."""
    named = next((line.measure for line in season if line.measure is not None), None)
    own = scope.measure.key if scope.measure is not None and scope.measure.key is not None else named or "points"
    if scope.measure is None:
        scope = replace(scope, measure=measured(own))
    return replace(scope, lines=tuple(replace(line, measure=own) if line.per != "game" and line.measure is None else line for line in scope.lines))


def _season_lines_unheld(question: str, season: list[Line]) -> None:
    """Refuse a season line on a figure the season line holds no column of,
    a percentage beside one, and the words of a season line no line was
    read from (``season_line_unread``)."""
    for line in season:
        if line.measure not in SEASON_FIGURES or SEASON_FIGURES[line.measure][0 if line.per == "season" else 1] is None:
            raise PointRefused(Cause(kind="season_line_unread", facts={"words": line.as_typed}), f"{line.as_typed!r} is a line on a figure the season line holds no column of")
    read = " ".join(line.as_typed for line in season)
    unread = next((m for m in lexicon.SEASON_LINE.finditer(question) if " ".join(m.group(0).casefold().split()) not in read), None)
    for found in (unread, lexicon.SEASON_LINE_PERCENT.search(question)):
        if found is not None:
            raise PointRefused(Cause(kind="season_line_unread", facts={"words": found.group(0)}), f"{found.group(0)!r} is a line on a season's figure")


def _lines_name_a_stat(scope: Filtered) -> None:
    """Refuse a point whose games are kept past a line on a percentage -
    "more than 40 field goal percentage" - by the cause naming it
    (``line_on_a_percentage``, :func:`~association.query.lines.measure_filters`),
    on every key: a count's reader read the line first and said the cause,
    where a log declined through the planner ("Nothing here answers this
    question as a game log: relation: ..."). A line naming no stat at all
    ("under 25 years old") is left to the reading's own causes, which name
    what it asks (an age) better than "names no box-score stat" does."""
    for line in relation_lines(scope):
        if line.measure is None and lexicon.PERCENTAGE_WORDS.search(line.as_typed):
            measure_filters(replace(scope, lines=(line,)))


def _minimum_honored(scope: Filtered, point: Reading) -> None:
    """Refuse a point that ranks no players under a qualifier the words
    state - "most points in a game minimum 20 games" is one game's high, and
    a minimum games a player needs to be ranked keeps nothing there - by a
    cause naming the floor asked, never answering past it. A ranking of
    players applies it, or refused it already (:func:`_everyone_ranking`)."""
    minimum = scope.minimum
    ranked = (point.read_as.shape, point.read_as.by) == ("ranking", "player") or point.read_as == PLAYERS_COUNTED
    if minimum is None or (ranked and (point.read_as.relation == "player_seasons" or point.minimum_games == minimum.count)):
        return
    raise PointRefused(Cause(kind="minimum_unranked", facts={"unit": minimum.word, "count": minimum.count}))


def window_under(asked: PointShape | None, read: WindowCandidates) -> WindowRead:
    """The window under the key: the grammar's end and count; a count of
    seasons where the key counts seasons (:data:`LIMIT_COUNTS_SEASONS`);
    the end settled against the key (:func:`_window_order`); a ranking's end
    on the keys that rank by one; the measure a players' ranking of yes/no
    games is ordered by.

    .. versionadded:: 6.0.0
       ``window.read_window``'s reading of its intent until Phase 3, step 4,
       slice (b).
    """
    asked = _ranked_as(asked)
    order, count, grammar_claim = read.grammar
    claims: list[Claim] = [grammar_claim] if grammar_claim is not None else []
    of: Literal["games", "seasons"] = "games"
    if asked in LIMIT_COUNTS_SEASONS and read.seasons is not None:
        # A history's count is of SEASONS: "the past 5 years" IS that count
        # and needs no range (#140, #114); the span claims the same words as
        # a range.
        count, of = read.seasons[0], "seasons"
        claims.append(read.seasons[1])
    order, order_claim, single = _window_order(asked, read, order, count)
    if order_claim is not None:
        claims.append(order_claim)
    if asked == PLAYER_LOG and order is None and count is None and read.every is not None:
        # "for each game", "every game": a log of the whole span, newest
        # first - an end with no count, never the log's default ten
        # (ISSUES.md #402).
        order = "recent"
        claims.append(read.every)
    if single:
        # One game of a player's line: the end and a count of one together.
        count, of = 1, "games"
    rank = None
    if asked in _WINDOW_RANKED and read.rank is not None:
        rank = read.rank[0]
        claims.append(read.rank[1])
    by = None
    if asked == PLAYER_RANKING and read.ranked_by is not None:
        by = read.ranked_by[0]
        claims.extend(read.ranked_by[1])
    window = Window(order=window_end(order), count=count, of=of, rank=rank, by=by)
    return WindowRead(window, claimed(claims))


def _window_order(asked: PointShape | None, read: WindowCandidates, order: str | None, count: int | None) -> tuple[str | None, Claim | None, bool]:
    """Which end of the span the rows are taken from, settled against the
    key - three rules, each measured: one game of a player's line ("his
    last game") is that game, the end and a count of one together; on a key
    whose reader honors an end (:data:`ORDER_ASKS`) one the grammar missed
    is read from the looser end words where exactly one matches - after one
    game named as his ("his last home game") - and on the four where an end
    means ONE game it stands only where the words name a game at one end;
    on any other key the end stands as the grammar read it, for the planner
    to refuse by name where the reader honors none (ISSUES.md #352).
    Returns the end, its claim, and whether one game of a line was named."""
    if asked in _WINDOW_ORDER_ON_A_SINGLE_GAME:
        if read.single is not None:
            return read.single[0], read.single[1], True
        return order, None, False
    if asked in ORDER_ASKS:
        claim = None
        if order is None and count is None and asked in _WINDOW_ONE_GAME_NAMED and read.single is not None:
            return read.single[0], read.single[1], True
        if order is None and len(read.order_words) == 1:
            order, claim = read.order_words[0]
        if order is not None and asked in _WINDOW_ORDER_IS_ONE_GAME and read.single is None and not read.order_words:
            return None, None, False
        return order, claim, False
    # On any other key the end stands as the grammar read it, whatever word
    # follows the count, and the planner refuses it by name where the key's
    # reader cannot honor one (ISSUES.md #352): dropped beside a count of two
    # or more until then, the reader read the bare count as the newest N
    # inside the default season, and "Warriors vs Mavs record last ten games"
    # tallied this season's three meetings.
    return order, None, False


#: The career a key implies, by the words' rule it reads under
#: (``span.SpanCandidates.implied``): a pair's record, a log's meetings with
#: an opponent, a count asked of one player.
_SPAN_IMPLIED: dict[PointShape, str] = {PLAYER_MEETINGS: "meetings", PLAYER_LOG: "log", GAMES_COUNTED: "count"}


def span_under(asked: PointShape | None, read: SpanCandidates) -> SpanRead:
    """The span under the key: a relative "past N seasons" a range of
    seasons, except where the key counts seasons (a history's window counts
    them, :data:`LIMIT_COUNTS_SEASONS`); the career a pair's record, a log's
    meetings with an opponent and a count asked of one player imply, each
    under its own key; and both season types on a log of the newest N
    games where no career or range stands.

    .. versionadded:: 6.0.0
       ``span.read_span``'s reading of its intent until Phase 3, step 4,
       slice (b).
    """
    asked = _ranked_as(asked)
    since = read.since if read.relative is None or asked in LIMIT_COUNTS_SEASONS else read.relative
    season, career = read.season, read.career
    implied = _SPAN_IMPLIED.get(asked) if asked is not None else None
    if implied is not None and implied in read.implied:
        career, season = True, None
    both = read.both or (asked == PLAYER_LOG and read.both_on_log and not career and since is None)
    return SpanRead(Span(season=season, season_type=read.season_type, both=both, career=career, since=since, until=read.until), read.claims)


def measure_under(asked: PointShape | None, words: MeasureWords, context: MeasureContext) -> MeasureRead:
    """The measure under the key, with the claims: the words' own reading
    over the model's key (:func:`~association.query.measure.named`), then
    each rule in order - the keys' own measures (fouling out, a games count
    under a line's reader, a stat kept only where named), an advanced
    metric, game score, a 2-point percentage, a ranking by shot distance,
    the attempts beside a make, a team metric's alias, games won or lost,
    the stat beside a line, a NetPoints rate, a team's total, a quarter's
    figure game by game, a run's result, the side, the shots, and the
    columns beside a ranking.

    .. versionadded:: 6.0.0
       ``measure.read_measure``'s reading of its intent until Phase 3, step
       4, slice (b).
    """
    asked = _ranked_as(asked)
    claims: list[Claim] = []
    key = context.key
    if words.worded is not None:
        key = words.worded[0]
        claims.append(words.worded[1])
    key = _measure_key_by_ask(asked, words, context, key, from_words=words.worded is not None)
    key = _measure_key_by_words(asked, words, context, key, claims)
    key, unit = _measure_rate(asked, words, key, claims)
    how: MeasureHow | None = None
    if unit is not None:
        how = how_of(unit)
    elif asked == PERIOD_LOG and (words.log or (words.period_games and key is None)):
        # A quarter's or half's figure asked game by game: a log, or "games"
        # with no stat named.
        how = "per_game"
    of_wins = None
    if asked == LINE_RUNS:
        of_wins = words.losing is None
        if words.losing is not None:
            claims.append(Claim(words.losing.start(), words.losing.end(), "measure"))
    side = _measure_side(asked, words, context, claims)
    shot_value = _measure_shot_value(asked, words, context, claims)
    beside = _measure_beside(asked, words, context, claims)
    # One claim per stretch read: the grammar and a rule may read the same
    # words ("ts%" is the grammar's key and the advanced metric's word).
    distinct = tuple(dict.fromkeys(claim for claim in claims if claim.end > claim.start))
    if key is None and unit is None and how is None and of_wins is None and side is None and shot_value is None and not beside:
        return MeasureRead(None, distinct)
    catalog_key, whose, keyed_side, keyed_how, category = key_of(key)
    whose, distinct = _measure_whose(asked, words, catalog_key, whose, distinct)
    measure = Measure(
        key=catalog_key,
        as_typed=key,
        how=how if how is not None else keyed_how,
        unit=unit,
        whose=whose,
        side=side if side is not None else keyed_side,
        shot_value=shot_value,
        category=category,
        won=of_wins,
        beside=beside,
    )
    return MeasureRead(measure, distinct)


def _measure_whose(asked: PointShape | None, words: MeasureWords, key: str | None, whose: MeasureWhose, claims: tuple[Claim, ...]) -> tuple[MeasureWhose, tuple[Claim, ...]]:
    """Whose figure the measure is: "opp" before the stat makes it the
    opponent's (ISSUES.md #409), as "allowed" after a team metric does, and
    so does a team's figure given up by the verb wherever it stands ("how
    many points did the sixers allow", "which team gave up the most
    points": ISSUES.md #348, #361), with its claim; the catalog's reading
    otherwise."""
    if key is None or whose != "own":
        return whose, claims
    if words.opponent is not None:
        return "opponent", (*claims, Claim(words.opponent.start(), words.opponent.end() - 1, "measure"))
    if words.given_up is not None and key in lexicon.GIVEN_UP_KEYS and asked is not None and asked.relation.startswith("team_"):
        return "opponent", (*claims, Claim(words.given_up.start(), words.given_up.end(), "measure"))
    return whose, claims


def _measure_key_by_ask(asked: PointShape | None, words: MeasureWords, context: MeasureContext, key: str | None, *, from_words: bool) -> str | None:
    """The key the key's own reading writes or drops: fouling out is the
    count's own stat; a games count on a line's reader is no measure; a
    quarter's or a half's stat is kept only where the words name one (the
    model fills the required key whether or not they do); a comparison's,
    a line's, a team's and a run's likewise. The drop is the MODEL's key's
    alone (``from_words`` False): a measure the words named stands
    ("grayson allen 3s made last season", ISSUES.md, closed)."""
    if words.fouled and asked == GAMES_COUNTED:
        key = "fouls"
    if asked == PLAYER_LOG and key in ("games", "game"):
        key = None
    if asked == PLAYER_LINE and key in GAMES_STATS and words.how_many and not context.order_named:
        key = None
    if asked == PLAYER_COMPARISON and not words.names_a_stat:
        # The comparison's whole line holds the stat asked about, and its
        # reader refuses a named one outright - the words' key too.
        key = None
    if not from_words:
        if asked in (PERIOD_LOG, PERIOD_RANKING, PLAYER_LINE, LINE_RUNS) and not words.names_a_stat:
            key = None
        if asked == TEAM_LINE and not (words.names_a_stat or words.team_stat):
            key = None
    if words.td3:
        key = "triple_double"
    return key


def _measure_key_by_words(asked: PointShape | None, words: MeasureWords, context: MeasureContext, key: str | None, claims: list[Claim]) -> str | None:
    """The key the words name beyond the grammar, each where a reader looks
    it up: an advanced metric, game score (by the reader's own spelling), a
    2-point percentage, a ranking by shot distance (the sentinel the ranking
    refuses by name), the attempts beside a make, a team metric's alias,
    games won or lost under a record over no keyed line, and the stat read
    beside a line."""
    if asked in _MEASURE_ADVANCED and words.advanced is not None:
        key = words.advanced[0]
        claims.append(Claim(words.advanced[1].start(), words.advanced[1].end(), "measure"))
    if asked in _MEASURE_GAME_SCORE and words.game_score is not None:
        key = _MEASURE_GAME_SCORE[asked]
        claims.append(Claim(words.game_score.start(), words.game_score.end(), "measure"))
    if asked in _MEASURE_TWO_POINT_PCT and words.two_point is not None:
        key = "twoPointFieldGoalPct"
        claims.append(Claim(words.two_point.start(), words.two_point.end(), "measure"))
    if asked == PLAYER_RANKING:
        key = _measure_key_ranked(words, key, claims)
    if key in MADE_TO_ATTEMPTED and words.attempted is not None and not words.made:
        key = MADE_TO_ATTEMPTED[key]
        claims.append(Claim(words.attempted.start(), words.attempted.end(), "measure"))
    if asked in (TEAM_LINE, TEAM_RANKING) and words.team_alias is not None:
        key = words.team_alias[0]
        claims.append(words.team_alias[1])
    if asked == LINE_RECORD and not context.keyed_line and words.games_won is not None:
        return "losses" if words.games_won.group(1).lower().startswith("los") else "wins"
    if context.line_stat is not None:
        key = context.line_stat
    return key


def _measure_key_ranked(words: MeasureWords, key: str | None, claims: list[Claim]) -> str | None:
    """The key a ranking of players reads beyond the grammar: games played,
    where the words name no other measure and the model's key none (ISSUES.md
    #408: the ranking's count of games), and a ranking by shot distance (the
    sentinel the ranking refuses by name)."""
    if words.games_played is not None and words.worded is None and key is None:
        key = "games_played"
        claims.append(Claim(words.games_played.start(), words.games_played.end(), "measure"))
    if words.shot_distance is not None:
        key = "shot_distance"
        claims.append(Claim(words.shot_distance.start(), words.shot_distance.end(), "measure"))
    return key


def _measure_rate(asked: PointShape | None, words: MeasureWords, key: str | None, claims: list[Claim]) -> tuple[str | None, str | None]:
    """A unit asked for: a NetPoints rate folded into its key (the one
    adjusted form it has), a per-90 or a per-100 rate of anything else kept
    as the cell the ranking refuses by name, a team's season total."""
    unit: str | None = None
    if words.per_minutes is not None:
        # A rate per N minutes, on every key (ISSUES.md #388): the ``rate``
        # cell, which the season ranking refuses by name and every other
        # reader declines - never a floor of N minutes a game.
        claims.append(Claim(words.per_minutes.start(), words.per_minutes.end(), "measure"))
        return key, words.per_minutes.group(0).casefold()
    if asked == PLAYER_RANKING:
        if key in ("netpoints", "netpoints_per_100", "netpoints_total") and len(words.sides) == 1:
            # "who are the top 10 in adjusted offensive netpoints" arrived as
            # the total after the 5.0.0 prompt shrink; the side word decides.
            name, found = words.sides[0]
            key = f"netpoints_{name}" + ("_per_100" if key.endswith("_per_100") else "")
            claims.append(Claim(found.start(), found.end(), "measure"))
        if words.per_90 is not None:
            claims.append(Claim(words.per_90.start(), words.per_90.end(), "measure"))
            return key, words.per_90.group(0).casefold()
        if words.rate is not None:
            claims.append(Claim(words.rate.start(), words.rate.end(), "measure"))
            if key in NETPOINTS_PER_100:
                key = NETPOINTS_PER_100[key]
            elif not (key is not None and key.endswith("_per_100")):
                unit = words.rate.group(0).casefold()
    if asked == TEAM_LINE and unit is None and words.team_total is not None and not words.per_game:
        unit = "total"
        claims.append(Claim(words.team_total.start(), words.team_total.end(), "measure"))
    return key, unit


def _measure_side(asked: PointShape | None, words: MeasureWords, context: MeasureContext, claims: list[Claim]) -> MeasureSide | None:
    """The side of the ball a fingerprint asked for: the words first
    (exactly one side named; both is the whole radar), the model's where
    the words say nothing."""
    if asked != NETPOINTS_FINGERPRINT:
        return None
    if len(words.sides) == 1:
        name, match = words.sides[0]
        claims.append(Claim(match.start(), match.end(), "measure"))
        return name  # type: ignore[return-value]
    return context.side if context.side in ("offense", "defense", "total") else None  # type: ignore[return-value]


def _measure_shot_value(asked: PointShape | None, words: MeasureWords, context: MeasureContext, claims: list[Claim]) -> Literal[1, 2, 3] | None:
    """Which shots a distance or a chart is about: the model's value where
    it filed one, else exactly one value named ("twos and threes" is
    neither); never beside "td3s", a triple-double the model read as a three."""
    if asked not in _MEASURE_SHOT_VALUE or words.td3:
        return None
    if isinstance(context.shot_value, int) and not isinstance(context.shot_value, bool):
        return context.shot_value  # type: ignore[return-value]
    if len(words.shot_values) != 1:
        return None
    value, match = words.shot_values[0]
    claims.append(Claim(match.start(), match.end(), "measure"))
    return value  # type: ignore[return-value]


def _measure_beside(asked: PointShape | None, words: MeasureWords, context: MeasureContext, claims: list[Claim]) -> tuple[str, ...]:
    """The columns a ranking asks to see beside its measure - a test's own
    where it hands them, on any key - and the words' on a ranking alone."""
    if context.fields:
        return tuple(context.fields)
    if asked != PLAYER_RANKING:
        return ()
    fields, read = words.beside
    claims.extend(read)
    return fields
