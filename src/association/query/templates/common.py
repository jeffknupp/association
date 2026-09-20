"""The machinery every template shares: the context and result types, which slots each template honors, the coverage checks, and the helpers more than one subject module uses.

.. versionadded:: 3.0.0
   Split out of the former ``association.query.templates`` module.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import duckdb

from association.nba.coverage import COVERAGE, REGULAR_SEASON, caveat, unavailable
from association.nba.season import current_season

from ..answer import Artifact
from ..conditions import _PLAYER_GAME_TABLES, _TEAM_GAME_TABLES, _game_scope, _Scope, box_source
from ..entities import Ambiguous, Availability, Entity, clarification, find_players, resolve_player, resolve_team, suggest_players, suggestion, teammate_names
from ..leaderboard import resolve_metric
from ..measures import MEASURE_WORDS
from ..metrics import LEADERBOARD_METRICS
from ..player_games import (  # noqa: F401 - the relation's names, re-exported for the templates and tests that read them here
    _OPEN_END,
    _OPEN_START,
    _PLAYER_GAMES,
    _RECORDED,
    _RECORDED_OR_REBUILT,
    REBUILT_STATS,
    STARTER_SIDES,
    Narrowed,
    _joined,
    _log_carries_rebuilt,
    _teammate_played,
    _teammate_stints,
)
from ..player_games import _tenure_clause as _relation_tenure_clause
from ..team_metrics import TEAM_METRICS, resolve_team_metric

# Slot value -> real player_box_stats column. A whitelist, not a passthrough:
# the router's `stat` slot is model-generated text, and this is the only place
# it can reach SQL. Same reasoning as metrics.EXTRA_FIELD_COLUMNS.
THRESHOLD_STAT_COLUMNS = {
    "points": "points",
    "rebounds": "rebounds",
    "assists": "assists",
    "steals": "steals",
    "blocks": "blocks",
    "turnovers": "turnovers",
    "threePointFieldGoalsMade": "threePointFieldGoalsMade",
    "fieldGoalsMade": "fieldGoalsMade",
    "freeThrowsMade": "freeThrowsMade",
    "minutes": "minutes",
    "fouls": "fouls",
}


STAT_LABELS = {
    "points": "point",
    "rebounds": "rebound",
    "assists": "assist",
    "steals": "steal",
    "blocks": "block",
    "turnovers": "turnover",
    "threePointFieldGoalsMade": "3-pointer",
    "fieldGoalsMade": "field goal",
    "freeThrowsMade": "free throw",
    "minutes": "minute",
    "fouls": "foul",
}


DEFAULT_LIMIT = 5


MAX_LIMIT = 50


# Named in every answer, so answering the wrong one is visible rather than silent.
SEASON_TYPE_NAMES = {1: "preseason", 2: "regular season", 3: "postseason"}


# Slots that narrow WHICH games an answer covers. A template that ignores one
# gives a different answer, not a broader one, and says nothing - confirmed
# three times ("his last game" charting a whole season, and so on). The router
# extracts these CORRECTLY in each case, so check_routing cannot catch a
# template dropping them; only this can.
#
# The five after those are read from the question text by the router and by
# entities.scope_from_question, never asked of the model, and exist for the same
# reason. Measured against real StatMuse queries before they did: "jaylen brown
# last 8 games vs pistons" answered with the Celtics' last 8 games, "Knicks
# home record" with their overall record, "career points leaders" with this
# season's, and "Podziemski game log without curry" with his whole log. Each
# was fast, fluent and about something else. `round` ("finals", "game 7") is
# honored by no template at all: nothing in the warehouse records one. `split`
# and `since` (a range of seasons) are read for every intent for the same reason:
# a template that is not about splits or ranges answered them with one season.
# `below` ("under 14 FTA") and `above` ("with 25 minutes") are lines a game's
# box score is kept under or over - `measure_filters` reads them onto the
# relation for the templates listed with them. `situation` (back-to-backs,
# overtime, a conference) is refused by every template: nothing narrows to it
# yet, and answering without it answered the whole season.
# `game_n` ("game 4") is one game of each playoff series, numbered by date over
# `real_games`; the relation finds it, and a regular-season question refuses.
# `season_n` ("his 18th season") is one season named by its place in a career;
# `settle_ordinal_season` turns it into a year once the player is resolved.
# `rate` is a per-possession rate asked of a metric that has no such form
# ("points per 100 possessions", "netpoints / 90"): set by the router only
# where it could not switch the metric itself, and honored by nothing.
SCOPING_SLOTS = frozenset({"order", "date", "opponent", "venue", "span", "without", "round", "split", "since", "below", "above", "game_n", "season_n", "situation", "rate"})


# What each template actually honors. Anything not listed here honors none.
HONORED_SCOPING: dict[str, frozenset[str]] = {
    # Every one of them, for a player: opponent, venue and a teammate's absence
    # are filters on the box-score rows, and a career is every season of them.
    # A team's log refuses `without` itself - that is with_without's question.
    # `split` only for a NAMED half of the starter/bench split - a question
    # naming both halves is a player_splits question, and check_scope still
    # refuses it here, because `route()` leaves the category in place then.
    # `below` and `above` are lines on a box-score column ("under 14 fta",
    # "with 25 minutes"): filters on the same rows, through measure_filters.
    "game_log": frozenset({"order", "date", "opponent", "venue", "span", "without", "split", "since", "below", "above", "game_n", "season_n"}),
    # The three that narrow games are answered from box scores rather than the
    # season line; a career is summed from the season table.
    "player_stat": frozenset({"opponent", "venue", "span", "without", "split", "since", "order", "below", "above", "game_n", "season_n"}),
    "player_history": frozenset({"span"}),
    # It always read `opponent`; listed now that `opponent` is a scoping slot.
    "team_quarter_points": frozenset({"opponent"}),
    # A period is not a scoping slot - it IS the question - so only the two
    # filters on WHICH games count are listed.
    # `split` only for a NAMED half, like game_log and player_stat.
    # `without` and `order` arrived with the relation: the games are the
    # relation's now, so a teammate's absence composes here exactly as it does
    # for game_log, and `order` picks which end of the log the rows come from.
    "period_split": frozenset({"opponent", "venue", "split", "without", "order"}),
    # The opponent IS the second team of a head-to-head. `venue` narrows to
    # the first-named team's home or road games, and `date` replaces the
    # season with one calendar day - both filters on `real_games`, the same
    # table the plain answer already reads.
    "head_to_head": frozenset({"opponent", "venue", "date"}),
    # `span` "career" is honored by drawing (or averaging) every season of the
    # requested season type rather than the latest with data - see
    # shots._career_shot_span. Before this, "all playoff games" carried no
    # span at all (no word of _SPAN_WORDS is in it) and a `span` the router
    # itself might emit would have been refused here rather than drawn (#141).
    "shot_chart": frozenset({"order", "span"}),
    "shot_distance": frozenset({"order", "span"}),
    "player_netpoints": frozenset({"order"}),
    # `order` is honored by DRAWING that game, from the long per-game table.
    # `date` is still honored by refusing: the router gives a calendar date
    # and the loader picks a player's first or last game of a season, which are
    # different questions - answering one with the other is the substitution
    # this whole module exists to prevent. Both stay listed either way, since
    # leaving one unlisted falls through to an agent with no better source,
    # which is slower and free to answer the season instead.
    "fingerprint": frozenset({"order", "date"}),
    # `span` "career" is honored by summing every season: a career leaderboard
    # from the per-team season rows, and a career count or high from every box
    # score since 1993-94. Each answer names the pool, since neither is all-time.
    # `rate` is honored by ANSWERING it where the metric has that form (a
    # season total) and by refusing, in the metric's own name, where it does
    # not ("/ 90"). It was unlisted, so check_scope raised before the template
    # ran: the per-90 question fell through to an agent with nothing to read,
    # and the `rate == "total"` branch below was unreachable in the pipeline.
    "leaderboard": frozenset({"span", "rate"}),
    # A count is already a line on a column; `below` is the same line the
    # other way ("games with under 14 fta"), and a phrase carrying the count's
    # own number IS the count, misread - see _threshold_count_lines.
    "threshold_count": frozenset({"span", "below", "above", "season_n"}),
    "single_game_high": frozenset({"span"}),
    # A career is every season on record rather than the current one; see
    # _condition_scope. `without` is the teammate with_without divides by, and
    # `split` is the one player_splits was asked for. `venue` and `opponent`
    # are filters on the same box-score rows `team` already narrows - a home
    # or road split for a player is answerable the same way a team's already
    # is (see team_record below).
    "player_splits": frozenset({"span", "split", "venue", "opponent"}),
    # `opponent` narrows BOTH rows of the split to one opponent's games, and
    # the title says so - "Embiid career record vs boston" is his record in the
    # games his team played Boston, not overall (#163).
    "with_without": frozenset({"span", "without", "opponent"}),
    "record_when": frozenset({"span"}),
    # `opponent` and `without` are honored only for the one-name-and-a-team
    # shape that is really a player-vs-team question in disguise - see the
    # versionchanged note on player_matchup itself. A genuine two-player
    # matchup with either left over refuses it from inside the template,
    # since check_scope cannot tell the two shapes apart from the slots alone.
    "player_matchup": frozenset({"span", "opponent", "without", "since"}),
    "streak": frozenset({"span"}),
    # The home/road split, the record against one team, and every season at
    # once - "Knicks home record" was answered with their overall 53-29.
    # `situation` is honored only where it names a real calendar month
    # ("in october") - team_record itself refuses every other value, the same
    # way `check_scope` used to refuse all of them (see ISSUES.md #84: this
    # does not touch the weekday/holiday/age/"since returning" narrowings that
    # stay refused). `split` is honored only as "month" - a record broken out
    # by calendar month, read from the same per-game date a month filter uses.
    "team_record": frozenset({"venue", "opponent", "span", "situation", "split"}),
    # Honored for the record metrics, from the standings' own home/road
    # strings; any other metric refuses it, since team season stats carry no
    # venue split at all.
    "team_leaderboard": frozenset({"venue"}),
}


# Which warehouse tables each template's answer is built from, so a question
# about a season none of them reach is refused rather than answered with the
# empty result that season produces. Hand-maintained, like HONORED_SCOPING
# above and for the same reason - deriving it by scanning for table names
# picks up every one mentioned in a comment - and guarded by
# test_every_template_declares_the_tables_it_reads.
#
# `leaderboard` is absent on purpose: its table depends on the metric asked
# for, and _sources_for resolves it per question.
TEMPLATE_SOURCES: dict[str, tuple[str, ...]] = {
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
    # (shots._career_shot_span). Its own floor (1977) is earlier than
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


TABLELESS_INTENTS: frozenset[str] = frozenset({"coach"})
"""Intents whose template reads no warehouse table at all.

Only ``coach`` today: it is a refusal, and there is nothing for it to read -
no table here holds a coach, which is the whole reason it refuses. So it
declares no sources, and :func:`check_coverage` and :func:`coverage_caveat`
both come back None for it, which is right: appending "there is no data for
1996" to a sentence that already explains what is missing would name a second,
wrong cause.

A named constant rather than a literal in a test, for the reason
:data:`association.query.router.CODE_ASSIGNED_INTENTS` is: a template absent
from ``TEMPLATE_SOURCES`` is normally one no floor can refuse, and the two
lists have to disagree deliberately rather than by drift.

.. versionadded:: 4.0.0
"""


# Templates that read a player name at all - resolving it, filtering on it, or
# refusing because of it. A name the question does not support is only worth
# refusing over where the answer would actually be about that player; for
# `team_record` and `head_to_head` the slot is not read, so a stray one changes
# nothing. Guarded by test_no_template_outside_player_intents_reads_a_player,
# which reads the source rather than trusting this list.
PLAYER_INTENTS: frozenset[str] = frozenset(
    {
        "fingerprint",
        "game_log",
        "leaderboard",
        "player_compare",
        "player_history",
        "player_matchup",
        "player_netpoints",
        "player_splits",
        "period_split",
        "player_stat",
        "record_when",
        "shot_chart",
        "shot_distance",
        "single_game_high",
        "streak",
        "team_quarter_points",
        "threshold_count",
        "with_without",
    }
)
"""Intents whose template reads a ``player`` or ``players`` slot.

.. versionadded:: 2.1.0
"""


PLAYER_REQUIRED_INTENTS: frozenset[str] = frozenset({"record_when", "period_split"})
"""Intents whose template cannot answer at all without a player, so a player the
router left out is worth restoring from the question.

Deliberately not every intent that reads one: where the player is optional -
``threshold_count``, ``single_game_high`` - an empty slot means "the league", and
filling it would turn a league question into a question about somebody the
question may only appear to name ("best" is Travis Best).

``record_when`` left this set once it grew a team branch (ISSUES.md #144): a
threshold on a TEAM's own scoring is a real, player-less question now, not an
unanswerable one, so restoring a stray name found elsewhere in the question
onto it would risk narrowing a team question into a player's. The player half
still restores a name the router dropped - through ``router.py``'s own
``_SUBJECT_RESTORED_INTENTS``, which reads the "X scored" grammar at routing
time, before this set is ever consulted - so nothing here was relied on for
that case in the first place.

.. versionadded:: 2.1.0
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
_BOX_SCORE_SCOPING = ("opponent", "venue", "without")


_PLAYER_BOX_SOURCES = ("player_game_log", "player_box_stats", "games")


# The computed advanced stats live in their own table with its own floor, and
# `player_stat` answers them from it. Named here rather than imported from the
# template, which imports this module.
_ADVANCED_STAT_NAMES = frozenset({"ts_pct", "efg_pct", "usage_pct", "game_score"})


def _sources_for_player_stat(slots: dict[str, Any]) -> tuple[str, ...]:
    """The table one player's numbers would come from.

    An advanced stat is charged its own floor (1994, from box scores) rather
    than the season line's (1977): "Kareem's true shooting in 1980" is refused
    because that stat is not computed that far back, and refusing it in the
    season line's words would name a floor the question does not depend on.
    """
    if isinstance(slots.get("stat"), str) and slots["stat"] in _ADVANCED_STAT_NAMES:
        return ("player_season_advanced_stats",)
    return _PLAYER_BOX_SOURCES if any(slots.get(s) for s in _BOX_SCORE_SCOPING) else ("player_season_stats_deduped",)


def _sources_for(intent: str, slots: dict[str, Any]) -> tuple[str, ...]:
    """The tables an answer would be built from, resolved per question because
    a leaderboard's depends on which metric was asked for."""
    if intent == "game_log":
        return _sources_for_game_log(slots)
    if intent == "player_stat":
        return _sources_for_player_stat(slots)
    if intent in ("player_splits", "streak"):
        return _sources_for_splits_or_streak(intent, slots)
    if intent == "record_when":
        return _sources_for_record_when(slots)
    if intent == "team_record":
        return _sources_for_team_record(slots)
    if intent == "team_leaderboard":
        return _sources_for_team_leaderboard(slots)
    if intent != "leaderboard":
        return TEMPLATE_SOURCES.get(intent, ())
    return _sources_for_leaderboard(slots)


def _sources_for_game_log(slots: dict[str, Any]) -> tuple[str, ...]:
    """A player's log reads the box scores; a team's, the team tables."""
    named_player = isinstance(slots.get("player"), str) and slots["player"].strip()
    return _PLAYER_BOX_SOURCES if named_player else ("games", "team_box_stats")


def _sources_for_record_when(slots: dict[str, Any]) -> tuple[str, ...]:
    """A named player's threshold reads the player tables; a team's own
    threshold (no ``player`` slot) reads only the team tables - the same split
    _sources_for_splits_or_streak makes, and for the same reason: a team
    question refused in player box scores' words names the wrong cause."""
    named_player = isinstance(slots.get("player"), str) and slots["player"].strip()
    return _PLAYER_GAME_TABLES if named_player else _TEAM_GAME_TABLES


def _sources_for_splits_or_streak(intent: str, slots: dict[str, Any]) -> tuple[str, ...]:
    """The player tables for a player's splits or streak, the team tables otherwise."""
    # A team's splits or streak never touch a player box score, and
    # charging them that table's floor would refuse a 1990 playoff question
    # with a sentence about player box scores - the wrong cause.
    named_player = isinstance(slots.get("player"), str) and slots["player"].strip()
    by_player = named_player or (intent == "streak" and isinstance(slots.get("threshold"), int))
    return _PLAYER_GAME_TABLES if by_player else _TEAM_GAME_TABLES


def _sources_for_team_record(slots: dict[str, Any]) -> tuple[str, ...]:
    """The standings for a season's record, ``games`` for a tally."""
    # A season's record, and its home/road split, are the standings'; a
    # record against one team, or in a postseason, can only be tallied
    # from `games`, whose regular seasons start later.
    against = bool(slots.get("opponent")) or (isinstance(slots.get("teams"), list) and len(slots["teams"]) > 1)
    return ("games",) if against or slots.get("season_type") == 3 else ("standings",)


def _sources_for_team_leaderboard(slots: dict[str, Any]) -> tuple[str, ...]:
    """The record metrics read standings (or ``games`` for a postseason); the rest, the team season stats."""
    key = resolve_team_metric(slots.get("stat"))
    if key is not None and TEAM_METRICS[key].expression is None:
        return ("games",) if slots.get("season_type") == 3 else ("standings",)
    return TEMPLATE_SOURCES["team_leaderboard"]


def _sources_for_leaderboard(slots: dict[str, Any]) -> tuple[str, ...]:
    """The table the asked-for leaderboard metric is ranked from."""
    stat = slots.get("stat")
    metric = resolve_metric(stat, career=slots.get("span") == "career") if isinstance(stat, str) else None
    spec = LEADERBOARD_METRICS.get(metric) if metric else None
    # An unrecognized metric is left to the template, which refuses it with a
    # better message than a coverage floor could.
    return (spec.table,) if spec else ()


def check_coverage(intent: str, slots: dict[str, Any]) -> str | None:
    """Why this question's season is out of reach, or None.

    Returned rather than raised, which is the opposite of :func:`check_scope`
    and deliberate. check_scope raises so the question falls through to the
    agent, which may do better. Nothing does better here: the agent would query
    the same empty tables, more slowly, and is then free to fill the silence
    from its own weights. The refusal IS the answer.

    .. versionadded:: 2.1.0
    """
    season = slots.get("season")
    if not isinstance(season, int):
        # No season means the current one, which every table covers.
        return None
    season_type = slots.get("season_type")
    return unavailable(
        _sources_for(intent, slots),
        season,
        season_type if isinstance(season_type, int) else 2,
        ranking=intent in RANKING_INTENTS,
    )


def coverage_caveat(intent: str, slots: dict[str, Any]) -> str | None:
    """A note for a season this question can reach but only partly, or None.

    .. versionadded:: 2.1.0
    """
    season = slots.get("season")
    season_type = slots.get("season_type") or REGULAR_SEASON
    return caveat(_sources_for(intent, slots), season, season_type) if isinstance(season, int) else None


#: Templates that honor one NAMED half of the starter/bench split and refuse
#: the bare category, which asks for a table they do not produce.
_SPLIT_SIDE_ONLY = frozenset({"game_log", "player_stat", "period_split"})


"""``{"fta": "freeThrowsAttempted", ...}`` - the question's word for a box-score column.

.. versionadded:: 4.3.0
"""

#: How the answer names each column a game was kept under or over.
_MEASURE_LABELS: dict[str, str] = {
    "fieldGoalsAttempted": "field goal attempts",
    "fieldGoalsMade": "field goals made",
    "freeThrowsAttempted": "free throw attempts",
    "freeThrowsMade": "free throws made",
    "threePointFieldGoalsAttempted": "3-point attempts",
    "threePointFieldGoalsMade": "3-pointers",
    "offensiveRebounds": "offensive rebounds",
    "defensiveRebounds": "defensive rebounds",
}

# The number and the words after it in a `below` / `above` phrase. The
# leading words ("under", "at most", "with") say which way the line faces.
_MEASURE_PHRASE = re.compile(r"^(?P<lead>.*?)\b(?P<n>\d+)\+?%?\s*(?P<words>.*)$")
_AT_MOST = ("at most", "no more than")
_STRICTLY_BELOW = ("under", "fewer than", "less than", "below")


@dataclass(frozen=True)
class MeasureFilter:
    """One line a question keeps games under or over: the box-score column,
    the comparison (a key of :data:`association.query.player_games.MEASURE_OPS`),
    the number, and how the answer says it.

    .. versionadded:: 4.3.0
    """

    column: str
    op: str
    value: int
    label: str


def _measure_column(words: str) -> str | None:
    """The column the words after a number name - the longest run of them
    that is in MEASURE_WORDS, so "free throw attempts in his career" reads the
    first three words and ignores the rest."""
    tokens = words.casefold().replace("-", " ").split()
    for width in (3, 2, 1):
        candidate = " ".join(tokens[:width])
        if candidate in MEASURE_WORDS:
            return MEASURE_WORDS[candidate]
    return None


def measure_filters(below: Any, above: Any) -> list[MeasureFilter]:
    """The lines a question keeps games under (the ``below`` slot) or over
    (``above``), read from the phrases ``route()`` kept - "under 14 fta",
    "with 25 minutes" - as filters on the ``player_game`` relation.

    The model's own ``stat`` is not consulted: beside "under 14 fta" it said
    ``freeThrowsMade``, the nearest name it knows, so the phrase is the only
    honest carrier of which column was meant. A phrase whose words name no
    column refuses (:class:`TemplateUnsupported`) rather than filtering on a
    guess - the same rule ``check_scope`` applies to a slot nothing honors.

    .. versionadded:: 4.3.0
    """
    filters: list[MeasureFilter] = []
    for key, phrases, default_op in (("below", below, "<"), ("above", above, ">=")):
        for phrase in [phrases] if isinstance(phrases, str) else (phrases or []):
            match = _MEASURE_PHRASE.match(str(phrase).strip())
            column = _measure_column(match.group("words")) if match else None
            if match is None or column is None:
                raise TemplateUnsupported(f"{phrase!r} names no box-score stat a game can be kept {'under' if key == 'below' else 'over'}")
            lead, words = match.group("lead").strip().casefold(), match.group("words").casefold()
            op = default_op
            if key == "below" and (lead.startswith(_AT_MOST) or " or less" in words):
                op = "<="
            elif key == "below" and not lead.startswith(_STRICTLY_BELOW):
                op = "<"
            value = int(match.group("n"))
            how = {"<": "under", "<=": "at most", ">=": "at least", ">": "over"}[op]
            filters.append(MeasureFilter(column, op, value, f"{how} {value} {_MEASURE_LABELS.get(column, column)}"))
    return filters


def narrow_measures(narrowed: Narrowed, filters: list[MeasureFilter]) -> None:
    """Apply :func:`measure_filters`' lines to a relation read, each with its
    label so the answer names what it kept."""
    for line in filters:
        narrowed.narrow_measure(line.column, line.op, line.value, line.label)


def check_scope(intent: str, slots: dict[str, Any]) -> None:
    """Raise if the question scoped to particular games and this template
    cannot honor that. Falling through is slow; answering a different question
    quickly is worse."""
    ignored = sorted(s for s in SCOPING_SLOTS if slots.get(s) and s not in HONORED_SCOPING.get(intent, frozenset()))
    # `split` is honored by the filtering templates only for a NAMED half. The
    # bare category means "show me both groups", which is player_splits' whole
    # answer and something they cannot do - so it is refused here rather than
    # quietly filtered to one side or quietly ignored.
    if slots.get("split") == "starter_bench" and intent in _SPLIT_SIDE_ONLY:
        ignored = sorted({*ignored, "split"})
    if ignored:
        raise TemplateUnsupported(f"{intent} cannot honor {ignored} - it would answer for a different span than was asked")


class TemplateUnsupported(Exception):
    """Raised when slots don't validate. The caller treats this exactly like an
    unrecognized intent - fall through to the agent - so a router slip
    degrades to the old (slow) path rather than to a wrong answer."""


@dataclass(frozen=True)
class TemplateContext:
    """What a template is given: the warehouse, and somewhere to write output.

    Templates took a bare connection until shot_chart needed an output
    directory too. Passing a small context rather than the whole Toolbox keeps
    templates testable with a plain in-memory DuckDB connection."""

    con: duckdb.DuckDBPyConnection
    out_dir: Path


@dataclass
class TemplateResult:
    """`answer` is the final prose, so the fast path makes NO model call after
    the router. Required, not optional: ollama keeps one KV cache slot per
    model, so a second call with a different system prompt evicts the router's
    prefix (measured: three consecutive router calls run 11.6s / 1.3s / 1.7s,
    but interleaving one narration call puts the next back to 11.2s). Phrasing
    every answer here also removes the last place on this path where a number
    could be invented.

    `data` is the same result as structured values - resolved names and numbers,
    no ids and no schema. Tests assert against it, and from 2.0 it is carried
    out to the caller in :class:`association.query.answer.Answer` rather than
    discarded once `answer` had been read.

    `artifacts` is whatever the template wrote to disk - a chart, or nothing.

    .. versionchanged:: 1.2.0
       ``answer`` is required rather than optional, making "the fast path makes
       no model call after the router" a type-checked property. The unused
       ``summary`` field was removed.

    .. versionchanged:: 2.0.0
       Added ``artifacts``.
    """

    data: dict[str, Any]
    answer: str
    artifacts: list[Artifact] = field(default_factory=list)


def _clamp_limit(limit: Any, default: int = DEFAULT_LIMIT) -> int:
    if not isinstance(limit, int) or limit < 1:
        return default
    return min(limit, MAX_LIMIT)


def _clarify(text: str, candidates: list[str], kind: str = "player", active: int = 0) -> TemplateResult:
    """A handled outcome, not a fall-through: the template knows exactly what
    is ambiguous, so it says so instead of passing the problem along.

    The sentence itself is entities.clarification, because the chart entry
    points reach the same ambiguity without going through a template and have
    to phrase it identically."""
    return TemplateResult(data={"ambiguous": text, "candidates": candidates}, answer=clarification(text, candidates, kind, active))


_GAME_LOGS = Availability("player_game_log")


_BOX_SCORES = Availability("player_box_stats")


def _career_end(season: int | None) -> int | None:
    """The ``through`` a span narrows a name by. None for one season, which
    narrows by ``season`` itself; the current season for a career, which keeps
    everybody with a row on record - Dell Curry's career is a real answer to
    "curry career points" - but names whoever plays now first, rather than
    cutting Stephen behind five retired Currys the way the season question did."""
    return current_season() if season is None else None


def _resolved_player(
    con: duckdb.DuckDBPyConnection,
    text: Any,
    missing: str = "no player named",
    *,
    available: Availability | tuple[Availability, ...],
    season: int | None = None,
    through: int | None = None,
) -> Entity | TemplateResult:
    """One player, a clarifying question, or a refusal - the player counterpart
    to _resolved_team. Returning the TemplateResult rather than raising it keeps
    ambiguity a handled outcome: the caller answers with the question instead of
    falling through to an agent that would guess. Callers must forward it.

    `available` is required, so no template can resolve a name without saying
    where its answer comes from: an ambiguous name is narrowed to the players
    with a row there, for `season` or any season up to `through`, before
    anybody is asked about. See entities.resolve_player - "Curry" this season
    asked about four men who never played in it and left out Stephen."""
    if not isinstance(text, str) or not text.strip():
        raise TemplateUnsupported(missing)
    try:
        resolution = resolve_player(con, text, available, season, through)
    except duckdb.CatalogException:
        # The NetPoints tables exist only if that opt-in fetch was run. With
        # nothing to narrow against the name is asked about as it always was,
        # and the template's own query reports the missing table.
        resolution = resolve_player(con, text)
    match resolution:
        case Entity() as player:
            return player
        case Ambiguous(candidates=candidates, active=active):
            return _clarify(text, candidates, active=active)
        case _:
            # A near miss is answered rather than passed along, for the same
            # reason ambiguity is: the agent would resolve the same name
            # against the same table, and a name nothing matches is a fact,
            # not a shape this template happens not to cover.
            near = [player.name for player in suggest_players(con, text)]
            if near:
                return TemplateResult(data={"unmatched": text, "suggestions": near}, answer=suggestion(text, near))
            raise TemplateUnsupported(f"no player matching {text!r}")


def _resolved_team(con: duckdb.DuckDBPyConnection, text: Any, season: int | None = None) -> Entity | TemplateResult:
    """One team, a clarifying question, or a refusal - read for ``season``,
    because a franchise's name is a fact about a season. "Hornets" is New
    Orleans in 2008 and Charlotte in 2026; see entities.franchise_by_name."""
    if not isinstance(text, str) or not text.strip():
        raise TemplateUnsupported("no team named")
    match resolve_team(con, text, season):
        case Entity() as team:
            return team
        case Ambiguous(candidates=candidates):
            return _clarify(text, candidates, kind="team")
        case _:
            raise TemplateUnsupported(f"no team matching {text!r}")


def _slot_season(slots: dict[str, Any]) -> int | None:
    """The season a question's team names are read for: the one it named, or
    None for "now" - the same default every template applies."""
    season = slots.get("season")
    return season if isinstance(season, int) else None


def _period(season: int, season_type: int) -> str:
    return f"{season} {SEASON_TYPE_NAMES.get(season_type, 'regular season')}"


def _table_cell(value: Any) -> str:
    """A value in an aligned column: a fixed decimal, never trailing-zero
    stripped - "25" next to "27.7" reads as a different unit."""
    if value is None:
        return "-"
    return f"{value:.1f}" if isinstance(value, float) else str(value)


def _format_value(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.3f}".rstrip("0").rstrip(".") if abs(value) < 1 else f"{value:.2f}".rstrip("0").rstrip(".")
    return str(value)


# stat -> (per-game column, season-total column or None, display label).
# Per-game and total are reported together, so "how many points did X average"
# and "how many did X score" need not be told apart from wording - a
# distinction the router got wrong more often than right.
PLAYER_STAT_COLUMNS: dict[str, tuple[str, str | None, str]] = {
    "points": ("avgPoints", "points", "points"),
    "rebounds": ("avgRebounds", None, "rebounds"),
    "assists": ("avgAssists", "assists", "assists"),
    "steals": ("avgSteals", "steals", "steals"),
    "blocks": ("avgBlocks", "blocks", "blocks"),
    "turnovers": ("avgTurnovers", "turnovers", "turnovers"),
    "minutes": ("avgMinutes", None, "minutes"),
    # ROUTER_PROMPT lists `fouls` among the stat names it may emit, and without
    # a column here every question naming one fell through to the agent.
    "fouls": ("avgFouls", "fouls", "fouls"),
    # The router emits these routinely; without them a question naming one was
    # silently answered with the default stat line instead.
    "threePointFieldGoalsMade": ("avgThreePointFieldGoalsMade", "threePointFieldGoalsMade", "3-pointers"),
    "fieldGoalsMade": ("avgFieldGoalsMade", "fieldGoalsMade", "field goals"),
    "freeThrowsMade": ("avgFreeThrowsMade", "freeThrowsMade", "free throws"),
}


# Per-season history columns: stat -> (label, [(column, header), ...]).
# A percentage is reported with its makes and attempts, because a percentage
# without volume behind it is the thing people ask "out of how many?" about.
HISTORY_COLUMNS: dict[str, tuple[str, list[tuple[str, str]]]] = {
    "threePointFieldGoalPct": ("3PT%", [("threePointFieldGoalPct", "3PT%"), ("threePointFieldGoalsMade", "3PM"), ("threePointFieldGoalsAttempted", "3PA")]),
    "fieldGoalPct": ("FG%", [("fieldGoalPct", "FG%"), ("fieldGoalsMade", "FGM"), ("fieldGoalsAttempted", "FGA")]),
    "freeThrowPct": ("FT%", [("freeThrowPct", "FT%"), ("freeThrowsMade", "FTM"), ("freeThrowsAttempted", "FTA")]),
    # No stored 2-point percentage column (checked against the warehouse: only
    # fieldGoalPct and threePointFieldGoalPct exist) - ESPN's season table has
    # a total and a 3-point split, not a 2-point one. Computed the same way a
    # career figure is elsewhere in this module: makes and attempts less the
    # threes, never the stored FG% read as though it meant this (ISSUES.md
    # #114 - "2pt percentage" used to arrive as fieldGoalPct and answer OVERALL
    # shooting, 55.3% for a season whose real 2-point split is 60.2%).
    # NULLIF guards a season with no 2-point attempts at all (every shot a
    # three) rather than dividing by zero.
    "twoPointFieldGoalPct": (
        "2PT%",
        [
            ("100.0 * (fieldGoalsMade - threePointFieldGoalsMade) / NULLIF(fieldGoalsAttempted - threePointFieldGoalsAttempted, 0)", "2PT%"),
            ("(fieldGoalsMade - threePointFieldGoalsMade)", "2PM"),
            ("(fieldGoalsAttempted - threePointFieldGoalsAttempted)", "2PA"),
        ],
    ),
    "points": ("points per game", [("avgPoints", "PPG")]),
    "rebounds": ("rebounds per game", [("avgRebounds", "RPG")]),
    "assists": ("assists per game", [("avgAssists", "APG")]),
    "steals": ("steals per game", [("avgSteals", "SPG")]),
    "blocks": ("blocks per game", [("avgBlocks", "BPG")]),
    "minutes": ("minutes per game", [("avgMinutes", "MPG")]),
    "threePointFieldGoalsMade": ("3-pointers per game", [("avgThreePointFieldGoalsMade", "3PM/G")]),
}


def _season_name(season: int) -> str:
    """1994 -> "1993-94": seasons are named for the year they end in."""
    return f"{season - 1}-{season % 100:02d}"


def _count_games(count: int) -> str:
    return f"{count:,} game{'' if count == 1 else 's'}"


@dataclass(frozen=True)
class _Span:
    """The seasons an answer covers: one (``season``), or a whole career
    (``season`` None) from ``first`` on, less any ``phantom`` season that is a
    copy of another.

    ``defaulted`` is True when ``season`` was never named - the question asked
    about "now", not about this particular year - and False when the question
    named it outright, career spans included (where the field is meaningless:
    a career has no single season to have been defaulted). It is what lets a
    refusal built from this span tell "the current season has nothing on
    record" from "the season you named has nothing on record": only the first
    is safe to redirect toward the player's other seasons (issue #18), because
    the second is a correct, specific answer and redirecting it would be the
    same guess-dressed-as-an-answer this project keeps refusing to make."""

    season: int | None
    season_type: int
    first: int = 0
    phantom: tuple[int, ...] = ()
    defaulted: bool = False
    #: The season a "since" question starts from - a career cut at the front,
    #: so the answer says "since 2022" and skips the box-score-floor note that
    #: a whole career carries (the question asked for no earlier season).
    since: int | None = None
    #: Which season of his career this one is, when the question named it that
    #: way ("his 18th season") - so the answer says so beside the year.
    ordinal: int | None = None

    @property
    def career(self) -> bool:
        """Every season, rather than one."""
        return self.season is None

    @property
    def kind(self) -> str:
        """``"regular season"`` or ``"postseason"``."""
        return SEASON_TYPE_NAMES.get(self.season_type, "regular season")

    def clause(self, column: str) -> tuple[str, list[Any]]:
        """SQL restricting ``column`` to these seasons, and its parameters."""
        if self.season is not None:
            return f"{column} = ?", [self.season]
        # The phantom is excluded by name, not left to the floor: 1993 is a full,
        # healthy-looking copy of 1994 (see coverage.Coverage.phantom), and a
        # career that counted it would list every 1993-94 game twice.
        excluded = f" AND {column} NOT IN ({', '.join('?' for _ in self.phantom)})" if self.phantom else ""
        return f"{column} >= ?{excluded}", [self.first, *self.phantom]

    def years(self, first: Any, last: Any) -> str:
        """The seasons a career answer's rows actually reach: ``"2024-2026
        regular seasons"``, or one season's name."""
        if first is None or last is None:
            return f"{self.kind}s"
        return f"{first} {self.kind}" if first == last else f"{first}-{last} {self.kind}s"

    def during(self, first: Any = None, last: Any = None, whose: str = "his career") -> str:
        """The span as it ends a sentence: ``"in the 2026 regular season"`` or
        ``"over his career (2019-2026 regular seasons)"``."""
        if self.season is not None and self.ordinal is not None:
            return f"in his {ordinal_word(self.ordinal)} season ({_period(self.season, self.season_type)})"
        if self.season is not None:
            return f"in the {_period(self.season, self.season_type)}"
        if self.since is not None:
            return f"since {self.since} ({self.years(first, last)})"
        return f"over {whose} ({self.years(first, last)})"


def _span_of(span: Any, season: Any, season_type: int, table: str, since: Any = None) -> _Span:
    """The seasons a question covers. ``table`` sets how far back a career
    reaches - box scores from 1994, the season line from 1977 - since a career
    is only as long as the table it is summed from. ``since`` (a season) is a
    career that starts there instead: every season from it on, the phantom
    still excluded, and never earlier than the table reaches.

    .. versionchanged:: 4.3.0
       Honors ``since``.
    """
    if isinstance(since, int) and since and not isinstance(since, bool):
        if isinstance(season, int) and season:
            raise TemplateUnsupported(f"since {since} and the {season} season at once")
        coverage = COVERAGE[table]
        return _Span(None, season_type, max(since, coverage.floor(season_type).season), coverage.phantom, since=since)
    if not span:
        named = isinstance(season, int) and bool(season)
        return _Span(season if named else current_season(), season_type, defaulted=not named)
    if span != "career":
        raise TemplateUnsupported(f"no span called {span!r}")
    if isinstance(season, int) and season:
        # "Career" and a named year at once. Either reading answers a different
        # question from the other, so neither is picked.
        raise TemplateUnsupported(f"a career span and the {season} season at once")
    coverage = COVERAGE[table]
    return _Span(None, season_type, coverage.floor(season_type).season, coverage.phantom)


def ordinal_word(n: int) -> str:
    """``1`` -> ``"1st"``, ``12`` -> ``"12th"``, ``23`` -> ``"23rd"``."""
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")  # codespell:ignore nd - an ordinal suffix
    return f"{n}{suffix}"


def settle_ordinal_season(con: duckdb.DuckDBPyConnection, player: Entity, season_n: Any, span: _Span) -> _Span | TemplateResult:
    """The span a question naming a season by its place in ``player``'s career
    ("his 18th season") actually covers: that year, with the ordinal kept so
    the answer names both. Unchanged when no ordinal was named.

    A career's seasons are its regular seasons on the per-player season table
    (which reaches back to 1976-77, before any box score), counted from his
    first, so a postseason question about "his 18th season" is that year's
    postseason. A player with fewer seasons than the ordinal gets a refusal
    naming how many he has, rather than his last one or the current year.

    .. versionadded:: 4.3.0
    """
    if not season_n:
        return span
    seasons = [
        int(row[0]) for row in con.execute("SELECT DISTINCT season FROM player_season_stats_deduped WHERE athlete_id = ? AND season_type = ? ORDER BY season", [player.id, REGULAR_SEASON]).fetchall()
    ]
    n = int(season_n)
    if n < 1 or n > len(seasons):
        have = f"{len(seasons)} seasons on record ({seasons[0]}-{seasons[-1]})" if seasons else "no season on record"
        message = f"{player.name} has {have}, so there is no {ordinal_word(n)} season to answer for."
        return TemplateResult(data={"player": player.name, "message": message}, answer=message)
    return _Span(seasons[n - 1], span.season_type, ordinal=n)


def _checked_venue(venue: Any) -> str:
    if venue not in ("home", "away"):
        raise TemplateUnsupported(f"no venue called {venue!r}")
    return str(venue)


def _narrow_player_games(con: duckdb.DuckDBPyConnection, player: Entity, span: _Span, *, opponent: Any, venue: Any, without: Any, split: Any = None, game_n: Any = None) -> Narrowed | TemplateResult:
    """``player``'s games in ``span``, narrowed to an opponent, a venue, a
    teammate's absence and a starter/bench half where the question named them.
    A name that needs a clarifying question comes back as the TemplateResult
    asking it.

    Every narrowing here is a filter over the same set of player-games, which
    is why they compose: a new one becomes available to every caller at once
    rather than being taught to each template separately. ``split`` was the
    fourth, and it reaches both `game_log` and `player_stat` through this one
    change.

    .. versionchanged:: 4.3.0
       Honors one half of the starter/bench split (``split``), and one game of
       each playoff series (``game_n``).
    """
    if game_n and span.season_type != 3:
        # A series has games 1-7; a regular season has nothing "game 4" names.
        raise TemplateUnsupported(f"game {game_n} names a game of a playoff series, and this is a {span.kind} question")
    season_clause, season_params = span.clause("pgl.season")
    narrowed = Narrowed(
        base=["pgl.athlete_id = ?", "pgl.season_type = ?", season_clause, "NOT pgl.did_not_play"],
        base_params=[player.id, span.season_type, *season_params],
    )
    if opponent:
        team = _resolved_team(con, opponent, season=span.season)
        if isinstance(team, TemplateResult):
            return team
        narrowed.opponent = team
        narrowed.extra.append("pgl.opponent_team_id = ?")
        narrowed.extra_params.append(team.id)
    if venue:
        narrowed.venue = _checked_venue(venue)
        narrowed.extra.append("(g.home_team_id = pgl.team_id) = ?")
        narrowed.extra_params.append(narrowed.venue == "home")
    if split in STARTER_SIDES:
        # Only a NAMED half filters. `starter_bench` reaches here unchanged
        # when the question named both, and is refused by check_scope for the
        # templates that cannot show a split table.
        narrowed.started = STARTER_SIDES[split]
        narrowed.extra.append("pgl.starter = ?")
        narrowed.extra_params.append(narrowed.started)
    # Every name the question gave, required together: "without Tatum and
    # Brown" is the games NEITHER played. Reading only the first answered a
    # question about two players with the games one of them missed - fluently,
    # and with nothing in the answer saying the other had been dropped.
    for text in teammate_names(without):
        mate = _resolved_teammate(con, text, player, span)
        if isinstance(mate, TemplateResult):
            return mate
        if any(mate.id == held.id for held in narrowed.without):
            continue  # the same man named twice narrows nothing
        tenure, tenure_params = _relation_tenure_clause(con, mate, span.season)
        narrowed.without.append(mate)
        narrowed.tenure.append((tenure, tenure_params))
        narrowed.extra += [tenure, f"NOT {_teammate_played(box_source(con))}"]
        narrowed.extra_params += [*tenure_params, mate.id]
    if game_n:
        narrowed.narrow_series_game(int(game_n))
    return narrowed


def _teammates_among(con: duckdb.DuckDBPyConnection, candidates: list[Entity], player: Entity, span: _Span) -> list[Entity]:
    """The candidates who were on one of ``player``'s teams in a season of
    ``span``. Elimination, never preference - the same move as
    entities.narrow_to_available: it drops the Currys who cannot be the one a
    Warriors question means, and still asks between two who both can."""
    if not candidates:
        return []
    clause, params = span.clause("season")
    placeholders = ", ".join("?" for _ in candidates)
    rows = con.execute(
        f"SELECT DISTINCT m.athlete_id FROM player_box_stats m "
        f"JOIN (SELECT DISTINCT season, team_id FROM player_box_stats WHERE athlete_id = ? AND season_type = ? AND {clause}) s ON s.season = m.season AND s.team_id = m.team_id "
        f"WHERE m.athlete_id IN ({placeholders})",
        [player.id, span.season_type, *params, *(c.id for c in candidates)],
    ).fetchall()
    have = {str(row[0]) for row in rows}
    return [c for c in candidates if c.id in have]


def _resolved_teammate(con: duckdb.DuckDBPyConnection, text: Any, player: Entity, span: _Span) -> Entity | TemplateResult:
    """The teammate a "without" names. "Without curry" is six players by name
    and at most two by roster, so an ambiguous name is narrowed to the ones who
    shared a team with ``player`` in the span before anything is asked."""
    if not isinstance(text, str) or not text.strip():
        raise TemplateUnsupported("'without' names nobody")
    resolved = resolve_player(con, text)
    if isinstance(resolved, Ambiguous):
        # Every match, not find_players' first page of ten: "without williams"
        # is 62 players by name, and a teammate who sorted past the tenth was
        # reported as nobody's teammate at all.
        candidates = find_players(con, text, limit=None)
        shared = _teammates_among(con, candidates, player, span)
        if len(shared) > 1:
            # Each of them shared his team in the span, so none is counted away.
            return _clarify(text, [c.name for c in shared], active=len(shared))
        if not shared:
            message = f"No player matching {text!r} was {player.name}'s teammate {span.during()}."
            return TemplateResult(data={"unmatched": text, "candidates": [c.name for c in candidates]}, answer=message)
        resolved = shared[0]
    if not isinstance(resolved, Entity):
        found = _resolved_player(con, text, available=_BOX_SCORES)  # a suggestion, or a refusal
        if isinstance(found, TemplateResult):
            return found
        resolved = found
    if resolved.id == player.id:
        raise TemplateUnsupported(f"{player.name} cannot play without himself")
    return resolved


def _season_redirect(con: duckdb.DuckDBPyConnection, athlete_id: str, season_type: int, table: str, *, athlete_column: str = "athlete_id") -> tuple[int, int] | None:
    """The first and last season ``athlete_id`` has a row in ``table`` for
    ``season_type`` - or None with nothing on record there at all.

    Backs the redirect a defaulted season's empty refusal gives (issue #18): a
    retired player's question that names no season used to be answered as
    though the current one had been asked outright - a true statement about
    the wrong year, the mirror-image refusal AGENTS.md warns against. Reading
    the player's own range lets the answer redirect instead of guessing which
    season, career, or nothing was meant."""
    row = con.execute(f"SELECT MIN(season), MAX(season) FROM {table} WHERE {athlete_column} = ? AND season_type = ?", [athlete_id, season_type]).fetchone()
    if row is None or row[0] is None:
        return None
    return int(row[0]), int(row[1])


def _defaulted_season_note(season_range: tuple[int, int] | None, kind: str, *, career_hint: bool = True) -> str:
    """The sentence a defaulted-season refusal appends when :func:`_season_redirect`
    found something to point at - empty with nothing on record at all, which
    leaves the plain refusal standing: that is a genuine gap, not a wrong
    default, and there is nothing here to redirect toward.

    Never substitutes an answer, only names where to ask again - the same
    discipline `entities.suggest_players` follows for a near-miss name."""
    if season_range is None:
        return ""
    first, last = season_range
    # Singular for one season, plural for a range - the same rule _Span.years
    # uses, so "he last appears in 2010" is never followed by "his 2010
    # seasons" for a player on record in exactly one.
    span = f"{first} {kind}" if first == last else f"{first}-{last} {kind}s"
    tail = ", or ask for his career." if career_hint else "."
    return f" He last appears in {last}. The warehouse holds his {span}; name one{tail}"


def _no_narrowed_games(con: duckdb.DuckDBPyConnection, player: Entity, span: _Span, narrowed: Narrowed, *, rebuilt: bool = False) -> str:
    """Why a narrowed question found no games, naming the fact that is really
    missing - his games in that span, the teammate, the match, or an empty box
    score. They are different sentences, and "X has no games" said of a player
    who simply never met that opponent - or whose games are every one of
    them there, with ESPN's box score served empty - sends the reader to look
    in the wrong place.

    ``rebuilt`` has to match whatever the caller's own query used to decide a
    played game: with it, a game reconstructed from play-by-play already counts
    as recorded, so what is left over here is genuinely unrecorded, not merely
    unread. Passing the wrong value would either call a rebuilt game "empty" or
    call a truly empty one "recorded".
    """
    if narrowed.date:
        # One named day: the rest of his career is not the fact that is missing.
        return f"No {span.kind} game on {narrowed.date} found for {player.name}{narrowed.filters(dated=False)}."
    where, params = narrowed.clauses(narrowed=False, rebuilt=rebuilt)
    total, first, last = con.execute(f"SELECT COUNT(*), MIN(pgl.season), MAX(pgl.season) {_PLAYER_GAMES} WHERE {where}", params).fetchone() or (0, None, None)
    if not total:
        # Before saying his games do not exist, check whether they do and ESPN
        # simply served no box score for them - the mirror-image bug AGENTS.md
        # records, in its own shape: a refusal that is confident and names the
        # wrong missing fact (the season, rather than the box scores). Every
        # Chicago and New Orleans game from 2013 to 2018 is one of these, and a
        # player whose games in the span are entirely such games has none that
        # pass the guard above - which used to read as "he has no games at all".
        empty_where, empty_params = narrowed.clauses(narrowed=False, recorded=False, rebuilt=rebuilt)
        empty_total, empty_first, empty_last = con.execute(f"SELECT COUNT(*), MIN(pgl.season), MAX(pgl.season) {_PLAYER_GAMES} WHERE {empty_where}", empty_params).fetchone() or (0, None, None)
        if empty_total:
            return f"{player.name} played {_count_games(empty_total)} {span.during(empty_first, empty_last)}, but the box score is empty for all of them - ESPN served no minutes or stats for any."
        if span.career:
            return f"{player.name} has no {span.kind} box scores in the warehouse, which begin with the {_season_name(span.first)} season."
        message = f"No {span.during()[len('in the ') :]} games found for {player.name}."
        if span.defaulted:
            # The season was never named - the question asked about "now", and
            # a retired player's "now" is empty. Redirecting to his own range
            # beats a refusal that reads as though his career itself were the
            # gap (issue #18); a season the question named keeps this plain,
            # because that refusal is correct as given.
            redirect = _season_redirect(con, player.id, span.season_type, "player_game_log")
            message += _defaulted_season_note(redirect, span.kind)
        return message
    during = span.during(first, last)
    # One at a time: with two teammates named, the fact that is missing is
    # which of them never shared a team with him, and saying "one of them did
    # not" sends the reader to look in the wrong place.
    for mate, (tenure, tenure_params) in zip(narrowed.without, narrowed.tenure, strict=True):
        together = con.execute(f"SELECT COUNT(*) {_PLAYER_GAMES} WHERE {where} AND {tenure}", [*params, *tenure_params]).fetchone()
        if not together or not together[0]:
            return f"{mate.name} was not {player.name}'s teammate in any of his {_count_games(total)} {during}."
    return f"{player.name} played {_count_games(total)} {during}, none of them{narrowed.filters()}."


def _box_score_notes(con: duckdb.DuckDBPyConnection, player: Entity, span: _Span, narrowed: Narrowed, *, career_note: bool = True, rebuilt: bool = False, rebuilt_shown: int = 0) -> list[str]:
    """What a box-score answer has to say about itself: what "without" was
    taken to mean, the empty lines left out, the figures that were rebuilt
    rather than fetched, and - unless ``career_note`` is off, as it is for one
    dated game - a career older than the box scores."""
    notes = []
    if narrowed.without:
        names = [mate.name for mate in narrowed.without]
        who = "he did not play" if len(names) == 1 else ("neither of them played" if len(names) == 2 else "none of them played")
        notes.append(f"Without {_joined(names)} means games {who} while on the same team - a did-not-play entry, or no line in the box score at all, which is how most injuries appear.")
    if rebuilt_shown:
        # Said outright, because these numbers did not come from ESPN. Per game
        # they are close (see REBUILT_STATS) but they are not the box score, and
        # a reader quoting one should know which kind of number they hold.
        notes.append(
            f"{rebuilt_shown} of these game{'s have' if rebuilt_shown != 1 else ' has'} no box score from ESPN: "
            f"{'their' if rebuilt_shown != 1 else 'its'} figures are rebuilt from play-by-play, and minutes cannot be recovered at all."
        )
    where, params = narrowed.clauses(recorded=False, rebuilt=rebuilt)
    row = con.execute(f"SELECT COUNT(*) {_PLAYER_GAMES} WHERE {where}", params).fetchone()
    empty = row[0] if row else 0
    if empty:
        notes.append(f"Not counted: {empty} game{'s' if empty != 1 else ''} in this span whose box score lists him with no minutes and no stats.")
    if span.career and career_note and span.since is None:
        row = con.execute(
            "SELECT MIN(season) FROM player_season_stats_deduped WHERE athlete_id = ? AND season_type = ? AND gamesPlayed > 0",
            [player.id, span.season_type],
        ).fetchone()
        earliest = row[0] if row else None
        if earliest is not None and earliest < span.first:
            notes.append(f"Box scores begin with the {_season_name(span.first)} season, so his {earliest}-{span.first - 1} seasons are not counted.")
    return notes


def _ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")  # codespell:ignore nd - an ordinal suffix
    return f"{n}{suffix}"


def _condition_scope(season: Any, span: Any, season_type: Any, tables: tuple[str, ...], since: Any = None) -> _Scope:
    """The games a question covers. No season means the current one - except
    for a career, where it means every season on record, which is what the
    word asked for. A season the question named beats "career": the router keeps
    a named year alongside it, and "career ... in 2015" is asking about 2015.
    ``since`` is every season from that one on.

    .. versionchanged:: 4.3.0
       Honors ``since``.
    """
    kind = season_type if season_type in (2, 3) else 2
    if isinstance(since, int) and since and not isinstance(since, bool):
        scope = _game_scope(None, kind, tables)
        return _Scope(None, kind, max(since, scope.first), scope.phantoms)
    if isinstance(season, int) and not isinstance(season, bool):
        return _game_scope(season, kind, tables)
    return _game_scope(None if span == "career" else current_season(), kind, tables)


def _where_in(scope: _Scope) -> str:
    """ "in the 2026 regular season", or "in any regular season on record" for a span with nothing in it."""
    return f"in the {scope.label()}" if scope.season is not None else f"in any {scope.kind} on record ({scope.first} onward)"


def _optional_team(con: duckdb.DuckDBPyConnection, text: Any, season: int | None = None) -> Entity | TemplateResult | None:
    if not isinstance(text, str) or not text.strip():
        return None
    return _resolved_team(con, text, season=season)


def _no_games(con: duckdb.DuckDBPyConnection, player: Entity, scope: _Scope, team: Entity | None) -> TemplateResult:
    """Nothing to report for a player, saying which fact is missing.

    Not the season: check_coverage has already refused any season the tables
    do not reach. What is left is the player - either no box score lists him
    at all, or the ones that do are all games he sat out, and those are
    different sentences."""
    params: dict[str, Any] = {**scope.params(), "player": player.id}
    where = f"pbs.athlete_id = $player AND {scope.where('pbs')}"
    if team is not None:
        where += " AND pbs.team_id = $team"
        params["team"] = team.id
    listed = con.execute(f"SELECT COUNT(*) FROM player_box_stats pbs WHERE {where}", params).fetchone()
    count = int(listed[0]) if listed else 0
    for_team = f" for the {team.name}" if team else ""
    if count:
        which = "it" if count == 1 else "any of them"
        message = f"{player.name} was listed in {count} box score{'' if count == 1 else 's'}{for_team} {_where_in(scope)} but did not play in {which}."
    else:
        message = f"{player.name} has no games{for_team} {_where_in(scope)} in the warehouse."
    return TemplateResult(data={"player": player.name, "team": team.name if team else None, "span": scope.label(), "games": 0}, answer=message)


#: The relation's clause builder under the name the templates and tests knew it by.
_Narrowed = Narrowed
