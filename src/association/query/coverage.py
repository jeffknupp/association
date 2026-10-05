"""Which warehouse tables each intent's answer is built from, and the refusal or
caveat a season under or partly under their floors gets.

The floors themselves are claims about the data, in
:mod:`association.nba.coverage`; what this module adds is the readers'
declaration of which tables they read (:data:`SOURCES`, one table for every
intent, resolved per question where the table depends on what was asked) and
the two checks the answering loop and the readers run against it.

.. versionadded:: 5.0.0
   Moved from ``association.query.templates.common`` (Phase 2, step 6); ``SOURCES`` was ``TEMPLATE_SOURCES``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from association.nba.coverage import REGULAR_SEASON, caveat, unavailable
from association.query.conditions import _PLAYER_GAME_TABLES, _TEAM_GAME_TABLES
from association.query.measures import resolve_metric
from association.query.metrics import LEADERBOARD_METRICS
from association.query.notes import note
from association.query.reading import Scope
from association.query.result import Refusal
from association.query.team_metrics import TEAM_METRICS, resolve_team_metric

# Which warehouse tables each intent's answer is built from, so a question
# about a season none of them reach is refused rather than answered with the
# empty result that season produces. One table for every reader rather than
# a declaration on each reader module: several readers share an intent's
# floor (a player's log and a team's), and _sources_for picks between them
# per question. Hand-maintained - deriving it by scanning for table names
# picks up every one mentioned in a comment - and guarded by
# test_every_template_declares_the_tables_it_reads.
#
# `leaderboard` is absent on purpose: its table depends on the metric asked
# for, and _sources_for resolves it per question.
SOURCES: dict[str, tuple[str, ...]] = {
    # player_season_stats is read to tell whether a named player's career began
    # before the box scores do. Listed after the box-score table so a season
    # under both floors is refused in the box scores' words, not as a ranking.
    # player_game_log is read for the stats a rebuild gets right, and the
    # stored table for the rest; both floors are 1994 with the same phantom
    # 1993, so declaring the log refuses no question the box scores answer.
    "threshold_count": ("player_game_log", "player_box_stats", "player_season_stats"),
    "single_game_high": ("player_game_log", "player_box_stats", "player_season_stats"),
    # The season line by default and box scores once the question narrows the
    # games, so _sources_for picks per question: a 1990 season line is
    # answerable, and a 1990 line against one opponent is not.
    # player_season_advanced_stats is the third case: a computed stat (true
    # shooting, effective FG%, usage, game score) is answered from it alone,
    # and it reaches back only to 1994 where the season line reaches 1977.
    "player_stat": ("player_season_stats_deduped", "player_game_log", "player_box_stats", "games", "player_season_advanced_stats"),
    "player_compare": ("player_season_stats_deduped",),
    "player_history": ("player_season_stats_deduped",),
    "player_netpoints": ("net_points_player", "net_points_player_fingerprint"),
    "team_record": ("standings",),
    # Answered from `real_games`, but the FLOOR is `games`': the filtered list
    # is the same data with the rows that are not games removed, and it reaches
    # exactly as far back. Declaring `real_games` would need a second, identical
    # COVERAGE entry to drift out of step with the first.
    "head_to_head": ("games",),
    "team_quarter_points": ("team_box_stats", "games"),
    # Points per period are summed out of the shot table, so the shot floor is
    # the one that applies - not the play-by-play floor, even though the two
    # start in the same year, because a season whose plays are complete can
    # still be missing the located shots this reads.
    "period_split": ("shot_chart", "games"),
    # Same two, plus the box table the denominator (games PLAYED) comes from.
    "period_leaderboard": ("shot_chart", "games", "player_box_stats"),
    # A player's log and a team's come from different tables, and _sources_for
    # picks between them - a team question refused with "Player game logs only
    # go back to..." names the wrong thing.
    "game_log": ("games", "team_box_stats", "player_game_log", "player_box_stats", "player_season_stats_deduped"),
    # player_season_stats_deduped is read only on a career span, to say
    # whether the 2002 shot floor clips a career that started earlier
    # (season_line.seasons_played). Its own floor (1977) is earlier than
    # shot_chart's, so declaring it here changes no refusal - shot_chart's
    # 2002 still wins as the narrower of the two.
    "shot_chart": ("shot_chart", "player_season_stats_deduped"),
    "shot_distance": ("shot_chart", "player_season_stats_deduped"),
    "fingerprint": ("net_points_player_fingerprint", "net_points_player_game_fingerprint"),
    # A team's splits and streaks read only the team tables; _sources_for
    # picks between the two per question.
    "player_splits": _PLAYER_GAME_TABLES,
    "with_without": _PLAYER_GAME_TABLES,
    # The fallback for a player question; _sources_for picks the team tables
    # instead for a team's own threshold (no `player` slot) - declaring
    # player_box_stats there too would refuse a team's 1989-1993 postseason
    # question in player box scores' words, the wrong cause, since that table
    # sets no postseason_first_season override and so floors at 1994 like its
    # regular season (team_box_stats and games both floor postseasons at 1989).
    "record_when": _PLAYER_GAME_TABLES,
    "player_matchup": _PLAYER_GAME_TABLES,
    "streak": _PLAYER_GAME_TABLES,
    # Opponent points come from `games`, so its floor applies too - a rating
    # from a season whose games are one team's schedule would be no rating.
    "team_stat": ("team_season_stats", "games"),
    # The record metrics read standings instead; _sources_for picks per metric.
    "team_leaderboard": ("team_season_stats", "games"),
    "team_outlook": ("team_power_index",),
}
"""Per intent, the warehouse tables its answer reads, whose coverage floors
:func:`check_coverage` refuses a season under.

.. versionadded:: 5.0.0
   ``association.query.templates.TEMPLATE_SOURCES`` until the templates were
   gone (Phase 2, step 6).
"""


TABLELESS_INTENTS: frozenset[str] = frozenset({"coach"})
"""Intents whose answer reads no warehouse table at all.

Only ``coach`` today: it is a refusal (the reading's ``no_coach_table``
cause, said by ``compose.plan.refusal_result``), and there is nothing for it to read -
no table here holds a coach, which is the whole reason it refuses. So it
declares no sources, and :func:`check_coverage` and :func:`coverage_caveat`
both come back None for it, which is right: appending "there is no data for
1996" to a sentence that already explains what is missing would name a second,
wrong cause.

A named constant rather than a literal in a test, for the reason
:data:`association.query.router.CODE_ASSIGNED_INTENTS` is: a template absent
from ``SOURCES`` is normally one no floor can refuse, and the two
lists have to disagree deliberately rather than by drift.

.. versionadded:: 4.0.0
"""


# Templates that rank players AGAINST each other, rather than reporting the
# numbers of players the question named. The distinction is the whole reason
# coverage.Coverage carries two floors: player_season_stats holds Michael
# Jordan's real 1990 line, so "how many did Jordan average" is answerable from
# it, while "who led the league" is not - the pool it would rank is 217 players
# out of a ~350-player league, and 7 out of a full league in 1980.
#
# player_compare is NOT here. It compares players the question named, which a
# per-player table answers exactly as well as a single lookup does.
# `streak` ranks players when no one is named ("most 40 point games in a row").
RANKING_INTENTS = frozenset({"leaderboard", "threshold_count", "single_game_high", "streak"})


# The slots that turn player_stat from a season-line lookup into a sum over box
# scores, and the tables that sum reads. Kept here so _sources_for and the
# template cannot disagree about which question needs which floor.
_BOX_SCORE_SCOPING = ("opponent", "venue", "without", "conditions")


_PLAYER_BOX_SOURCES = ("player_game_log", "player_box_stats", "games")


# The computed advanced stats live in their own table with its own floor, and
# `player_stat` answers them from it. Named here rather than imported from the
# template, which imports this module.
_ADVANCED_STAT_NAMES = frozenset({"ts_pct", "efg_pct", "usage_pct", "game_score"})


def _as_scope(value: Scope | Mapping[str, Any]) -> Scope:
    """The typed scope a shared step reads: a :class:`Scope` as given, and a
    slot dict through :meth:`Scope.from_slots` - the one door a slot dict
    comes in by, so a key or a value nothing types is refused here exactly as
    it is where the parser builds the Reading.

    Only the three coverage checks take a slot dict still
    (:func:`check_coverage`, :func:`coverage_caveat` and ``_sources_for``),
    and only from the tests: the agent hands them the Reading's own Scope,
    which the parser writes (:func:`~association.query.parse.reading_from_route`).
    Every other step here takes the Scope alone."""
    return value if isinstance(value, Scope) else Scope.from_slots(value)


def _sources_for_player_stat(scope: Scope) -> tuple[str, ...]:
    """The table one player's numbers would come from.

    An advanced stat is charged its own floor (1994, from box scores) rather
    than the season line's (1977): "Kareem's true shooting in 1980" is refused
    because that stat is not computed that far back, and refusing it in the
    season line's words would name a floor the question does not depend on.
    """
    if scope.stat in _ADVANCED_STAT_NAMES:
        return ("player_season_advanced_stats",)
    return _PLAYER_BOX_SOURCES if any(getattr(scope, name) for name in _BOX_SCORE_SCOPING) else ("player_season_stats_deduped",)


def _sources_for(intent: str, scope: Scope | Mapping[str, Any]) -> tuple[str, ...]:
    """The tables an answer would be built from, resolved per question because
    a leaderboard's depends on which metric was asked for."""
    scope = _as_scope(scope)
    if intent == "game_log":
        return _sources_for_game_log(scope)
    if intent == "player_stat":
        return _sources_for_player_stat(scope)
    if intent in ("player_splits", "streak"):
        return _sources_for_splits_or_streak(intent, scope)
    if intent == "record_when":
        return _sources_for_record_when(scope)
    if intent == "team_record":
        return _sources_for_team_record(scope)
    if intent == "team_leaderboard":
        return _sources_for_team_leaderboard(scope)
    if intent != "leaderboard":
        return SOURCES.get(intent, ())
    return _sources_for_leaderboard(scope)


def _sources_for_game_log(scope: Scope) -> tuple[str, ...]:
    """A player's log reads the box scores; a team's, the team tables."""
    named_player = bool(scope.player and scope.player.strip())
    return _PLAYER_BOX_SOURCES if named_player else ("games", "team_box_stats")


def _sources_for_record_when(scope: Scope) -> tuple[str, ...]:
    """A named player's threshold reads the player tables; a team's own
    threshold (no ``player`` slot) reads only the team tables - the same split
    _sources_for_splits_or_streak makes, and for the same reason: a team
    question refused in player box scores' words names the wrong cause."""
    named_player = bool(scope.player and scope.player.strip())
    return _PLAYER_GAME_TABLES if named_player else _TEAM_GAME_TABLES


def _sources_for_splits_or_streak(intent: str, scope: Scope) -> tuple[str, ...]:
    """The player tables for a player's splits or streak, the team tables otherwise."""
    # A team's splits or streak never touch a player box score, and
    # charging them that table's floor would refuse a 1990 playoff question
    # with a sentence about player box scores - the wrong cause.
    named_player = bool(scope.player and scope.player.strip())
    by_player = named_player or (intent == "streak" and scope.threshold is not None)
    return _PLAYER_GAME_TABLES if by_player else _TEAM_GAME_TABLES


def _sources_for_team_record(scope: Scope) -> tuple[str, ...]:
    """The standings for a season's record, ``games`` for a tally."""
    # A season's record, and its home/road split, are the standings'; a
    # record against one team, or in a postseason, can only be tallied
    # from `games`, whose regular seasons start later.
    against = bool(scope.opponent) or len(scope.teams) > 1
    return ("games",) if against or scope.season_type == 3 else ("standings",)


def _sources_for_team_leaderboard(scope: Scope) -> tuple[str, ...]:
    """The record metrics read standings (or ``games`` for a postseason); the rest, the team season stats."""
    key = resolve_team_metric(scope.stat)
    if key is not None and TEAM_METRICS[key].expression is None:
        return ("games",) if scope.season_type == 3 else ("standings",)
    return SOURCES["team_leaderboard"]


def _sources_for_leaderboard(scope: Scope) -> tuple[str, ...]:
    """The table the asked-for leaderboard metric is ranked from."""
    metric = resolve_metric(scope.stat, career=scope.span == "career")
    spec = LEADERBOARD_METRICS.get(metric) if metric else None
    # An unrecognized metric is left to the template, which refuses it with a
    # better message than a coverage floor could.
    return (spec.table,) if spec else ()


def check_coverage(intent: str, scope: Scope | Mapping[str, Any]) -> str | None:
    """Why this question's season is out of reach, or None.

    Returned rather than raised, and deliberately: a narrowing a reader
    cannot honor is declined so the compiler's own sentence gets its turn,
    and may do better. Nothing does better here: a season under the floor
    is empty for every reader. The refusal IS the answer.

    .. versionadded:: 2.1.0

    .. versionchanged:: 5.0.0
       Reads the typed :class:`~association.query.reading.Scope`. A slot dict
       is still taken, through :meth:`~association.query.reading.Scope.from_slots`,
       until every caller passes ``reading.scope``.
    """
    scope = _as_scope(scope)
    if scope.season is None:
        # No season means the current one, which every table covers.
        return None
    return unavailable(
        _sources_for(intent, scope),
        scope.season,
        scope.season_type or 2,
        ranking=intent in RANKING_INTENTS,
    )


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


def coverage_refusal(intent: str, scope: Scope) -> Refusal | None:
    """:func:`check_coverage`, typed: the floor this question's season is
    under, as the :class:`~association.query.result.Refusal` a reader
    returns, or None.

    .. versionadded:: 5.0.0
    """
    if scope.season is None:
        return None
    return floor_refusal(_sources_for(intent, scope), scope.season, scope.season_type or 2, ranking=intent in RANKING_INTENTS)


def coverage_caveat(intent: str, scope: Scope | Mapping[str, Any]) -> str | None:
    """A note for a season this question can reach but only partly, or None.

    .. versionadded:: 2.1.0

    .. versionchanged:: 5.0.0
       Reads the typed :class:`~association.query.reading.Scope`. A slot dict
       is still taken, through :meth:`~association.query.reading.Scope.from_slots`,
       until every caller passes ``reading.scope``.
    """
    scope = _as_scope(scope)
    if scope.season is None:
        return None
    said = caveat(_sources_for(intent, scope), scope.season, scope.season_type or REGULAR_SEASON)
    return note("partial_season", said, season=scope.season, season_type=scope.season_type or REGULAR_SEASON, intent=intent) if said else None
