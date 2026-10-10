"""Which warehouse tables each shape's answer is built from, and the refusal
or caveat a season under or partly under their floors gets.

The floors themselves are claims about the data, in
:mod:`association.nba.coverage`; what this module adds is the readers'
declaration of which tables they read (:data:`SOURCES`, one entry per
:class:`~association.query.reading.PointShape` the answer side routes -
resolved per question where the table depends on the measure asked for -
and :data:`RELATION_SOURCES` for a point no reader takes) and the checks
the answering loop and the readers run against it, keyed by the planned
point's shape and never by an intent.

.. versionadded:: 5.0.0
   Moved from ``association.query.templates.common`` (Phase 2, step 6); ``SOURCES`` was ``TEMPLATE_SOURCES``.

.. versionchanged:: 6.0.0
   Keyed by the planned point's :class:`~association.query.reading.PointShape`
   (Phase 3, step 1); by intent, resolved per question by the retired
   words' slot lists, until then. ``TABLELESS_INTENTS`` and
   ``RANKING_INTENTS`` are gone: a refusal the reading comes to has no
   point and so no floor, and whether a floor is a ranking's is the shape.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from association.nba.coverage import REGULAR_SEASON, caveat, unavailable
from association.query.conditions import _PLAYER_GAME_TABLES, _TEAM_GAME_TABLES
from association.query.measure import keyed
from association.query.measures import resolve_metric
from association.query.metrics import LEADERBOARD_METRICS
from association.query.notes import note
from association.query.reading import PointShape, Scope
from association.query.result import Refusal
from association.query.team_metrics import TEAM_METRICS, resolve_team_metric

# The box scores a player's games are read from: the log for the stats a
# rebuild gets right, the stored table for the rest, and `games` for the
# game itself; both box floors are 1994 with the same phantom 1993.
# team_alignment last: a conference or division narrowing reads it on every
# game relation, and its 1988 floor binds nowhere beside these (#216).
_PLAYER_BOX_SOURCES = ("player_game_log", "player_box_stats", "games", "team_alignment")

# A count of games over a line, or a single game's high: the box scores,
# and player_season_stats, read to tell whether a named player's career
# began before the box scores do. Listed after the box-score tables so a
# season under both floors is refused in the box scores' words, not as a
# ranking.
_PLAYER_COUNT_SOURCES = ("player_game_log", "player_box_stats", "player_season_stats", "team_alignment")

# The computed advanced stats live in their own table with its own floor,
# and a player's line answers them from it. Named here rather than imported
# from the reader, which imports this module.
_ADVANCED_STAT_NAMES = frozenset({"ts_pct", "efg_pct", "usage_pct", "game_score"})


def _player_line_tables(scope: Scope) -> tuple[str, ...]:
    """One player's unnarrowed line: the season line, or - for a computed
    stat (true shooting, effective FG%, usage, game score) - the advanced
    table alone, which reaches back only to 1994 where the season line
    reaches 1977: "Kareem's true shooting in 1980" is refused because that
    stat is not computed that far back, and refusing it in the season
    line's words would name a floor the question does not depend on."""
    return ("player_season_advanced_stats",) if keyed(scope.measure) in _ADVANCED_STAT_NAMES else ("player_season_stats_deduped",)


def _player_games_line_tables(scope: Scope) -> tuple[str, ...]:
    """One player's line over his narrowed games: the box scores, or the
    advanced table for a computed stat (the same floor year, named for the
    stat)."""
    return ("player_season_advanced_stats",) if keyed(scope.measure) in _ADVANCED_STAT_NAMES else _PLAYER_BOX_SOURCES


def _metric_tables(scope: Scope) -> tuple[str, ...]:
    """The table the asked-for leaderboard metric is ranked from; nothing
    for a metric the ranking's reader refuses with a better message than a
    coverage floor could."""
    metric = resolve_metric(scope.measure, career=scope.span.career)
    spec = LEADERBOARD_METRICS.get(metric) if metric else None
    return (spec.table,) if spec else ()


def _team_record_tables(scope: Scope) -> tuple[str, ...]:
    """The standings for a season's record (and its home/road split);
    ``games`` for a tally - a record against one team, or in a postseason,
    can only be tallied from `games`, whose regular seasons start later."""
    against = bool(scope.cuts.opponent) or len(scope.teams) > 1
    return ("games",) if against or scope.span.season_type == 3 else ("standings",)


def _team_ranking_tables(scope: Scope) -> tuple[str, ...]:
    """The record metrics read standings (or ``games`` for a postseason);
    the rest, the team season stats - and opponent points come from
    `games`, so its floor applies too: a rating from a season whose games
    are one team's schedule would be no rating."""
    key = resolve_team_metric(scope.measure)
    if key is not None and TEAM_METRICS[key].expression is None:
        return ("games",) if scope.span.season_type == 3 else ("standings",)
    return ("team_season_stats", "games")


def _team_line_tables(scope: Scope) -> tuple[str, ...]:
    """One team's own season line or total (``team_seasons.team_lines_statement``,
    ``compose.team``'s unnarrowed read): ESPN's season totals, with the
    games beside them only where the read needs opponent points - the
    whole line (no stat asked: its ratings and points allowed are read
    from ``real_games``, and dashed where the tally is short), or a metric
    whose expression takes ``opp_points`` - so the games table's floor and
    its gaps apply there and not to a count ESPN's own line holds: a 2001
    postseason 3-point total carried "Philadelphia's run reads 16 games
    against the 23" about a figure read from the 23-game line (ISSUES.md
    #227)."""
    key = resolve_team_metric(scope.measure)
    if key is None:
        return ("team_season_stats", "games")
    metric = TEAM_METRICS.get(key)
    if metric is not None and metric.expression is not None and "opp_points" in metric.expression:
        return ("team_season_stats", "games")
    return ("team_season_stats",)


Tables = tuple[str, ...] | Callable[[Scope], tuple[str, ...]]
"""A shape's declared tables: the tuple itself, or a resolver over the
scope where the table depends on the measure asked for.

.. versionadded:: 6.0.0
"""

# Which warehouse tables each shape's answer is built from, so a question
# about a season none of them reach is refused rather than answered with the
# empty result that season produces. One entry per shape the answer side
# routes (``compose._ROUTES``; a test holds the two sets equal) rather than
# a declaration on each reader module - hand-maintained, since deriving it
# by scanning for table names picks up every one mentioned in a comment.
SOURCES: dict[PointShape, Tables] = {
    PointShape("player_games", "scalar", "count"): _PLAYER_COUNT_SOURCES,
    PointShape("player_games", "ranking", "count"): _PLAYER_COUNT_SOURCES,
    PointShape("player_games", "rows", "count"): _PLAYER_COUNT_SOURCES,
    PointShape("player_games", "rows", "measure"): _PLAYER_COUNT_SOURCES,
    # A player's log reads the box scores; a team's, the team tables - a
    # team question refused with "Player game logs only go back to..."
    # names the wrong thing.
    PointShape("player_games", "rows", "date"): _PLAYER_BOX_SOURCES,
    PointShape("team_games", "rows", "date"): ("games", "team_box_stats"),
    # The season line for an unnarrowed line and the box scores for a
    # narrowed one - which is which the point reader settled when it named
    # the relation: a 1990 season line is answerable, and a 1990 line
    # against one opponent, or since a date, is not (#212, closed by
    # keying the floor on the point: until Phase 3, step 1 the floor read
    # four slots of its own and the reader a longer list).
    PointShape("player_seasons", "scalar", "line"): _player_line_tables,
    PointShape("player_games", "scalar", "line"): _player_games_line_tables,
    PointShape("player_seasons", "comparison", "subject"): ("player_season_stats_deduped",),
    PointShape("player_seasons", "split", "season"): ("player_season_stats_deduped",),
    # A season-line ranking's table depends on the metric asked for. The
    # game-level ranking (the planner's re-plan of a season-line ranking its
    # reader did not read, which no reader takes) has no entry: it reads
    # the box scores, and RELATION_SOURCES floors it there. Until the commit
    # after Phase 3, step 1 it was held to the metric's table - none, for a
    # line the season line has no metric for - and "how many players
    # averaged 30 ppg in 1986" answered "No games for every player in the
    # 1986 regular season", the wrong cause.
    PointShape("player_seasons", "ranking", "player"): _metric_tables,
    PointShape("netpoints", "scalar", "ratings"): ("net_points_player", "net_points_player_fingerprint"),
    PointShape("netpoints", "chart", "fingerprint"): ("net_points_player_fingerprint", "net_points_player_game_fingerprint"),
    PointShape("team_games", "scalar", "record"): _team_record_tables,
    # Answered from `real_games`, but the FLOOR is `games`': the filtered list
    # is the same data with the rows that are not games removed, and it reaches
    # exactly as far back. Declaring `real_games` would need a second, identical
    # COVERAGE entry to drift out of step with the first.
    PointShape("team_games", "comparison", "opponent"): ("games",),
    PointShape("team_periods", "scalar", "total"): ("team_box_stats", "games"),
    # Points per period are summed out of the shot table, so the shot floor is
    # the one that applies - not the play-by-play floor, even though the two
    # start in the same year, because a season whose plays are complete can
    # still be missing the located shots this reads.
    PointShape("player_periods", "rows", "date"): ("shot_chart", "games"),
    PointShape("player_periods", "split", "period"): ("shot_chart", "games"),
    # Same two, plus the box table the denominator (games PLAYED) comes from.
    PointShape("player_periods", "ranking", "player"): ("shot_chart", "games", "player_box_stats"),
    # player_season_stats_deduped is read only on a career span, to say
    # whether the 2002 shot floor clips a career that started earlier
    # (season_line.seasons_played). Its own floor (1977) is earlier than
    # shot_chart's, so declaring it here changes no refusal - shot_chart's
    # 2002 still wins as the narrower of the two.
    PointShape("shots", "chart", "shots"): ("shot_chart", "player_season_stats_deduped"),
    PointShape("shots", "scalar", "distance"): ("shot_chart", "player_season_stats_deduped"),
    # A player's splits, record over a line and run read the player tables;
    # a team's read only the team tables - charging them a player box
    # score's floor would refuse a 1990 playoff question with a sentence
    # about player box scores, the wrong cause (team_box_stats and games
    # both floor postseasons at 1989; player_box_stats at 1994).
    PointShape("player_games", "split", "splits"): _PLAYER_GAME_TABLES,
    PointShape("team_games", "split", "splits"): _TEAM_GAME_TABLES,
    PointShape("player_games", "split", "line"): _PLAYER_GAME_TABLES,
    PointShape("team_games", "split", "line"): _TEAM_GAME_TABLES,
    PointShape("player_games", "runs", "line"): _PLAYER_GAME_TABLES,
    PointShape("team_games", "runs", "won"): _TEAM_GAME_TABLES,
    PointShape("player_games", "comparison", "met"): _PLAYER_GAME_TABLES,
    # The with/without split reads whether named teammates played: the
    # player box scores, on the team relation.
    PointShape("team_games", "split", "presence"): _PLAYER_GAME_TABLES,
    # Opponent points come from `games`, so its floor applies where the
    # read needs them (the whole line, a rating, points allowed) - not to a
    # count ESPN's own line holds (#227).
    PointShape("team_seasons", "scalar", "line"): _team_line_tables,
    PointShape("team_seasons", "ranking", "team"): _team_ranking_tables,
    PointShape("team_snapshots", "scalar", "projection"): ("team_power_index",),
}
"""Per shape the answer side routes, the warehouse tables its reader reads,
whose coverage floors :func:`check_coverage` refuses a season under.

.. versionadded:: 5.0.0
   ``association.query.templates.TEMPLATE_SOURCES`` until the templates were
   gone (Phase 2, step 6).

.. versionchanged:: 6.0.0
   Keyed by :class:`~association.query.reading.PointShape` (Phase 3, step 1).
"""


RELATION_SOURCES: dict[str, tuple[str, ...]] = {
    "player_games": _PLAYER_BOX_SOURCES,
    "player_periods": ("shot_chart", "games", "team_alignment"),
    "player_seasons": ("player_season_stats_deduped",),
    "team_games": ("games", "team_box_stats", "team_alignment"),
    "team_periods": ("team_box_stats", "games", "team_alignment"),
    "team_seasons": ("team_season_stats", "games"),
    "team_snapshots": ("team_power_index",),
    "netpoints": ("net_points_player", "net_points_player_fingerprint"),
    "shots": ("shot_chart", "player_season_stats_deduped"),
}
"""Per relation, the tables a point no reader takes reads - the compiler's
own sentence over the relation's games or line (a league ranking of games
by a measure, a history re-planned over the games) - for a shape with no
entry in :data:`SOURCES`.

.. versionadded:: 6.0.0
"""


def _as_scope(value: Scope | Mapping[str, Any]) -> Scope:
    """The typed scope a shared step reads: a :class:`Scope` as given, and a
    slot dict through :meth:`Scope.from_slots` - the one door a slot dict
    comes in by, so a key or a value nothing types is refused here exactly as
    it is where the parser builds the Reading.

    Only the three coverage checks take a slot dict still
    (:func:`check_coverage`, :func:`coverage_caveat` and :func:`sources_for`),
    and only from the tests: the agent hands them the planned point's own
    scope. Every other step here takes the Scope alone."""
    return value if isinstance(value, Scope) else Scope.from_slots(value)


def sources_for(shape: PointShape | None, scope: Scope | Mapping[str, Any]) -> tuple[str, ...]:
    """The tables the answer to a point of ``shape`` would be built from,
    resolved per question where the table depends on the measure asked for
    (:data:`SOURCES`); the relation's own for a shape no reader takes
    (:data:`RELATION_SOURCES`); none for no point at all (``None``: a
    refusal the reading came to, which read nothing - appending "there is
    no data for 1996" to a sentence that already explains what is missing
    would name a second, wrong cause).

    .. versionadded:: 6.0.0
    """
    if shape is None:
        return ()
    scope = _as_scope(scope)
    declared = SOURCES.get(shape)
    if declared is None:
        return RELATION_SOURCES.get(shape.relation, ())
    return declared(scope) if callable(declared) else declared


def check_coverage(shape: PointShape | None, scope: Scope | Mapping[str, Any]) -> str | None:
    """Why this question's season is out of reach for a point of ``shape``,
    or None.

    Returned rather than raised, and deliberately: a narrowing a reader
    cannot honor is declined so the compiler's own sentence gets its turn,
    and may do better. Nothing does better here: a season under the floor
    is empty for every reader. The refusal IS the answer. A ranking's
    floor (``player_season_stats``' survivor sample ranks nobody before
    1994) applies where the shape is a ranking.

    .. versionadded:: 2.1.0

    .. versionchanged:: 5.0.0
       Reads the typed :class:`~association.query.reading.Scope`. A slot dict
       is still taken, through :meth:`~association.query.reading.Scope.from_slots`,
       until every caller passes ``reading.scope``.

    .. versionchanged:: 6.0.0
       Keyed by the planned point's :class:`~association.query.reading.PointShape`
       (Phase 3, step 1), not an intent.
    """
    scope = _as_scope(scope)
    if scope.span.season is None or shape is None:
        # No season means the current one, which every table covers; no
        # point means nothing was read.
        return None
    return unavailable(sources_for(shape, scope), scope.span.season, scope.span.season_type or 2, ranking=shape.shape == "ranking")


def floor_refusal(tables: tuple[str, ...], season: int, season_type: int, *, ranking: bool = False, shown: Mapping[str, Any] | None = None, under: tuple[str, ...] = ("message",)) -> Refusal | None:
    """:func:`~association.nba.coverage.unavailable` as a typed
    :class:`~association.query.result.Refusal` (``season_out_of_reach``):
    the tables, the season and its type, and whether the answer ranks -
    the facts the floor's sentence is made of, said by the sayer through
    the same floor table. ``shown`` is the page's values beside it, the
    season asked about unless a reader's page carried more or less, and
    ``under`` the keys it reads the sentence under (none, for the team
    compiler's).

    .. versionadded:: 5.0.0
    """
    if unavailable(tables, season, season_type, ranking=ranking) is None:
        return None
    facts = {"tables": list(tables), "season": season, "season_type": season_type, "ranking": ranking}
    return Refusal(kind="season_out_of_reach", facts=facts, shown={"season": season} if shown is None else shown, under=under)


def coverage_refusal(shape: PointShape | None, scope: Scope) -> Refusal | None:
    """:func:`check_coverage`, typed: the floor this question's season is
    under, as the :class:`~association.query.result.Refusal` a reader
    returns, or None.

    .. versionadded:: 5.0.0

    .. versionchanged:: 6.0.0
       Keyed by the planned point's :class:`~association.query.reading.PointShape`.
    """
    if scope.span.season is None or shape is None:
        return None
    return floor_refusal(sources_for(shape, scope), scope.span.season, scope.span.season_type or 2, ranking=shape.shape == "ranking")


def coverage_caveat(shape: PointShape | None, scope: Scope | Mapping[str, Any], *, intent: str = "") -> str | None:
    """A note for a season this question can reach but only partly, or None.
    ``intent`` is the page's label, recorded on the note beside the season
    (``Answer.intent`` stays for the page until Phase 4).

    .. versionadded:: 2.1.0

    .. versionchanged:: 5.0.0
       Reads the typed :class:`~association.query.reading.Scope`. A slot dict
       is still taken, through :meth:`~association.query.reading.Scope.from_slots`,
       until every caller passes ``reading.scope``.

    .. versionchanged:: 6.0.0
       Keyed by the planned point's :class:`~association.query.reading.PointShape`;
       the intent is the note's label only.
    """
    scope = _as_scope(scope)
    if scope.span.season is None or shape is None:
        return None
    said = caveat(sources_for(shape, scope), scope.span.season, scope.span.season_type or REGULAR_SEASON, ranking=shape.shape == "ranking")
    return note("partial_season", said, season=scope.span.season, season_type=scope.span.season_type or REGULAR_SEASON, intent=intent) if said else None
