"""The machinery every template shares: the context and result types, which slots each template honors, the coverage checks, and the helpers more than one subject module uses.

.. versionadded:: 3.0.0
   Split out of the former ``association.query.templates`` module.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import duckdb

from association.nba.coverage import COVERAGE, REGULAR_SEASON, caveat, unavailable
from association.nba.season import current_season, eastern_day_utc_range

from ..answer import Artifact
from ..calendar import parse_alignment, parse_situation
from ..conditions import _PLAYER_GAME_TABLES, _TEAM_GAME_TABLES, _game_scope, _Scope, box_source
from ..entities import BOX_SCORES as BOX_SCORES
from ..entities import GAME_LOGS as GAME_LOGS
from ..entities import Ambiguous, Availability, Entity, clarification, find_players, resolve_player, resolve_team, suggest_players, suggestion, teammate_names
from ..lines import MeasureFilter as MeasureFilter
from ..lines import measure_filters as measure_filters
from ..measures import resolve_metric
from ..metrics import LEADERBOARD_METRICS
from ..notes import Note, note
from ..player_games import (  # noqa: F401 - the relation's names, re-exported for the templates and tests that read them here
    _OPEN_END,
    _OPEN_START,
    _PLAYER_GAMES,
    _RECORDED,
    _RECORDED_OR_REBUILT,
    BOTH_SEASON_TYPES,
    CONDITION_PREDICATES,
    PERIOD_COLUMNS,
    PERIOD_PLAYS_COLUMNS,
    REBUILT_STATS,
    STARTER_SIDES,
    Condition,
    Narrowed,
    _joined,
    _teammate_played,
    _teammate_stints,
    league,
    season_type_clause,
)
from ..player_games import _tenure_clause as _relation_tenure_clause
from ..reading import DEFAULT_LIMIT as DEFAULT_LIMIT
from ..reading import FILLER_PLAYER_WORDS as FILLER_PLAYER_WORDS
from ..reading import OWN_TEAM_RESTORABLE_INTENTS as OWN_TEAM_RESTORABLE_INTENTS
from ..reading import PLAYER_REQUIRED_INTENTS as PLAYER_REQUIRED_INTENTS
from ..reading import POSITIONS as POSITIONS
from ..reading import SUBJECT_RESTORABLE_INTENTS as SUBJECT_RESTORABLE_INTENTS
from ..reading import TEAM_ONLY_INTENTS as TEAM_ONLY_INTENTS
from ..reading import ConditionSpec, PeriodCondition, Scope, Unsupported
from ..reading import _clamp_limit as _clamp_limit
from ..reading import ordinal_word as ordinal_word
from ..reading import period_label as period_label
from ..reading import period_narrowing as period_narrowing
from ..reading import scope_reads_box_scores as scope_reads_box_scores
from ..team_games import TeamNarrowed
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


# Named in every answer, so answering the wrong one is visible rather than silent.
# `0` is `player_games.BOTH_SEASON_TYPES` - never a real value a row carries,
# only a question that asked for both at once ("including the playoffs"). One
# entry here means every existing `SEASON_TYPE_NAMES.get(season_type, ...)`
# call site names it correctly with no further change.
SEASON_TYPE_NAMES = {0: "regular season and postseason", 1: "preseason", 2: "regular season", 3: "postseason"}


# Slots that narrow WHICH games an answer covers. A template that ignores one
# gives a different answer, not a broader one, and says nothing - confirmed
# three times ("his last game" charting a whole season, and so on). The router
# extracts these CORRECTLY in each case, so check_routing cannot catch a
# template dropping them; only this can.
#
# The five after those are read from the question text by the router and by
# subject.apply_subject, never asked of the model, and exist for the same
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
# relation for the templates listed with them. `situation` is a weekday, a
# month, a fixed holiday, "since <day>" (`calendar.parse_situation`), or - the
# other half of the same slot, K3-2 - a conference or division the opponent is
# in (`calendar.parse_alignment`), applied together by `_apply_situation`
# below. Anything else it could name (back-to-backs, overtime, an age, "since
# returning") is refused by every template: nothing narrows to it yet, and
# answering without it answered the whole season.
# `until` closes a `since`-bounded range at the far end ("2019-20 to 2023-24",
# "the 2010s") - `router._validate_range` - and is declared and read
# everywhere `since` is (`_span_of`, `_Span.clause`), never on its own: a
# template that honors `since` but not `until` would read a CLOSED range as an
# open one and answer every season after it too, the same silent-widening
# shape `since` itself exists to stop. `test_until_is_declared_wherever_since_is`
# (tests/query/test_templates.py) enforces this pairing by reading the source.
# `game_n` ("game 4") is one game of each playoff series, numbered by date over
# `real_games`; the relation finds it, and a regular-season question refuses.
# `season_n` ("his 18th season") is one season named by its place in a career;
# `settle_ordinal_season` turns it into a year once the player is resolved.
# `rate` is a per-possession rate asked of a metric that has no such form
# ("points per 100 possessions", "netpoints / 90"): set by the router only
# where it could not switch the metric itself, and honored by nothing.
# `season_type_unstated` is not a narrowing at all but its opposite - a
# "last N games" question naming no season type at all
# (`router._route_game_log_recent_span`) - and it is listed here for the same
# reason `situation` is: the discipline that a new slot is declared by the
# templates that honor it and refused by the rest applies whether the slot
# widens or narrows. Only `game_log` can ever see it - the router sets it for
# no other intent - so it is refused everywhere else only in principle.
# `until` (step 3, K1) is the inclusive LAST season of a range whose first the
# router already files as `since` ("2019-20 to 2023-24", a decade) - never
# alone, so a template honors it only by honoring `since` and reading `until`
# beside it (`_span_of`/`_validated_until`); one not wired to `until` at all
# would otherwise silently read only the range's first half.
# `ranked_by` is read by nothing: the router files it when a
# `leaderboard` question ranks the GAMES that satisfy a boolean stat by another
# measure ("highest scoring triple doubles" - yardstick-v2 F124), the same
# slots as the count "most triple doubles" otherwise. No template honors it,
# so check_scope refuses and the compiler's boolean-game ranking answers.
SCOPING_SLOTS = frozenset(
    {
        "order",
        "date",
        "opponent",
        "venue",
        "span",
        "without",
        "round",
        "split",
        "since",
        "until",
        "below",
        "above",
        "game_n",
        "season_n",
        "situation",
        "conditions",
        "rate",
        "season_type_unstated",
        "ranked_by",
        "period",
        "half",
        "period_condition",
    }
)


# What the player-games relation narrows by, declared ONCE. Every template that
# settles its player through `scoped_player` and his games through
# `scoped_games` honors all of these, because the narrowing is done there and
# not in the template: an opponent, a venue, a teammate's absence, a named half
# of the starter/bench split, one game of each playoff series, a line on a
# box-score column, one Eastern date, a span of seasons, a first season, an
# ordinal season. `order` (with `limit`) is the window - the newest or oldest
# N of the narrowed games - which the relation cuts after every row filter and
# before whatever the template does with the rows, so "30-point games in his
# last 10" counts inside the ten (step 3, C0's one skeleton-specific rule).
# `scoped_games` sets it (`relation_window`, below): a NAMED `order` wins
# outright, and a bare `limit` with no `order` still means the newest N - the
# router's own traces for "Create a shot chart for Steph Curry's last two
# games of the regular season" never emit `order` at all, only `limit`, so a
# rule gated on `order` alone would never reach that question (step 3, C5).
#
# This replaced six per-template lists that had drifted: game_log honored
# twelve of these, single_game_high one, on the same relation - a slot taught
# to one template at a time, which is the O(templates x slots) matrix the
# algebra port exists to remove. A template on the relation that cannot honor
# one of these says so in RELATION_SCOPING_EXCLUDED, with the reason.
RELATION_SCOPING = frozenset(
    {"order", "date", "opponent", "venue", "span", "without", "split", "since", "until", "below", "above", "game_n", "season_n", "situation", "conditions", "period", "half", "period_condition"}
)
"""The scoping slots every template on the player-games relation honors.

.. versionadded:: 4.4.0

.. versionchanged:: 5.0.0
   ``period_condition`` - a quarter or half as a condition on which games
   count (ROADMAP step 2, #275), applied by :func:`_apply_period_condition`.
"""

# The cells a template on the relation does NOT honor, each with why. A reason
# has to be about the template's answer, not its code: a slot that merely was
# not wired is not excluded, it is wired.
RELATION_SCOPING_EXCLUDED: dict[str, dict[str, str]] = {
    # The two retired templates' WORDS (compose.plan.STATED_SCOPING) never
    # said a quarter: their presenters step aside for one, and the compiler's
    # own sentence, which names the period through Narrowed.filters, answers.
    "game_log": {
        "period": "the log's retired sentence heads whole games and never names a quarter",
        "half": "the log's retired sentence heads whole games and never names a half",
    },
    "player_stat": {
        "period": "the season line's retired sentence heads whole games and never names a quarter",
        "half": "the season line's retired sentence heads whole games and never names a half",
    },
    # A single date is one game, and one game is not a streak.
    "streak": {
        "date": "one game is not a run",
        "order": "a run is read over every game in the span, not the last N",
        "period": "a run is a run of whole games; a quarter of each is a different streak nobody has defined",
        "half": "a run is a run of whole games; a half of each is a different streak nobody has defined",
    },
    # A split is a division of a span into groups; "the last N" is a window
    # that game_log answers.
    "player_splits": {
        "date": "one game has nothing to split",
        "order": "a limited number of recent games is game_log's question",
        "period": "the splits table is headed as whole games; a quarter's split would print under the same heading",
        "half": "the splits table is headed as whole games; a half's split would print under the same heading",
    },
    "record_when": {
        "date": "one game has no record",
        "order": "a record over the last N games is game_log's question",
        "period": "a record is won and lost over whole games; its sentence would not say the condition was read in one quarter",
        "half": "a record is won and lost over whole games; its sentence would not say the condition was read in one half",
    },
    # A period question's accuracy caveat (PERIOD_RECONCILIATION) is measured
    # per SEASON against ESPN's own linescores - summing across several would
    # mix seasons of different reliability under one caveat, or none, and the
    # header names ONE season regardless (`_period`), which would be wrong for
    # a range too: measured, `since=2023` (honored before this exclusion,
    # since scoped_player reads it directly off the full slots dict) pulled
    # the correct 257 games back to 2023 but still headed them "the 2026
    # regular season". `date` is no longer here: it narrows to one game (and
    # so one season) through `scoped_games`, the same as every other template
    # on the relation.
    "player_matchup": {
        "opponent": "two players' meetings are the games they played against each other - there is no third team to narrow them to",
        "order": "the newest meetings are shown beneath averages over all of them - a window would cut the averages the matchup exists to give",
        "season_n": "an ordinal season is one player's - a matchup names two, and the question does not say whose fifth season is meant",
        "period": "a meeting's line is both players' whole game; only one side of the pair would be read for the quarter",
        "half": "a meeting's line is both players' whole game; only one side of the pair would be read for the half",
    },
    # A shot read draws every shot of the games the relation narrows to; a
    # quarter narrows the LINE of each game, not which of its shots are drawn,
    # so the chart would be the whole game under a quarter's heading.
    "shot_chart": {"period": "the chart draws every shot of each game, not the quarter's", "half": "the chart draws every shot of each game, not the half's"},
    "shot_distance": {"period": "the average reads every shot of each game, not the quarter's", "half": "the average reads every shot of each game, not the half's"},
    "period_split": {
        "span": "the accuracy caveat is measured per season, not across a career",
        "since": "the accuracy caveat is measured per season, and the header names one season - both wrong for a range",
        "until": "the accuracy caveat is measured per season, and the header names one season - both wrong for a range",
        # Excluded from the presenter's WORDS, not from the point: its point
        # keeps the cell and the compiler's own sentence, which names both
        # the quarter measured and the quarter conditioning the games,
        # answers (point._default_period_split, the game_log rule).
        "period_condition": "a period answer says the one period it measures, never a second one conditioning which games count",
    },
}
RELATION_SCOPING_EXCLUDED["player_matchup"]["period_condition"] = "a meeting is both players' whole game; a quarter's line conditioning it would be read on one side of the pair only"
"""Per template, the relation's slots it refuses, and why.

.. versionadded:: 4.4.0
"""


def relation_scoping(intent: str, *extra: str) -> frozenset[str]:
    """The player relation's cells ``intent`` honors: :data:`RELATION_SCOPING`
    plus ``extra``, less the cells :data:`RELATION_SCOPING_EXCLUDED` names
    for it.

    .. versionadded:: 5.0.0
       Public, as the relation's shared step; ``_relation_scoping`` is this.
    """
    return frozenset((RELATION_SCOPING | set(extra)) - set(RELATION_SCOPING_EXCLUDED.get(intent, {})))


_relation_scoping = relation_scoping


# What the team-games relation narrows by, declared ONCE - the team
# counterpart of RELATION_SCOPING. Every template that settles its team and
# span through `scoped_team` and its games through `team_games` honors these:
# an opponent, a venue, one Eastern date, a career that starts partway through
# (`since`), one game of each playoff series (`game_n`), and a window of the
# newest or oldest N of the narrowed games (`order`, with `limit`). `span`
# ("career") is here too, even though it is settled by `scoped_team`/`_span_of`
# rather than narrowed by `team_games` itself - the same shape RELATION_SCOPING
# already keeps `season_n` in for the player relation, which `scoped_player`
# settles the same way.
#
# `without` has a team meaning - the games a TEAMMATE missed - but that is
# with_without's own question, not a team's plain games; `split`
# (starter/bench) and `below`/`above` (a line on a box-score column) have no
# shared team-scale reading yet (ISSUES.md, "record_when's team branch and
# streak's team/league branches still refuse ..."), so none of the three is a
# cell the relation itself carries. A template that wants one leans on
# `TeamNarrowed.narrow` directly, the way `_record_when_team_base` already
# joins `team_box_stats` for its own stat threshold.
#
# `situation` and `until` are step 3, K1's two additions, bringing the team
# relation to parity with the player one's own RELATION_SCOPING:
# `situation` is a calendar narrowing - a weekday, a month, a fixed holiday,
# or "since <month day>" within each game's own season - applied by
# `team_games` itself via `TeamNarrowed.narrow_calendar`, the exact mirror of
# `scoped_games`' own reading for the player relation, OR (K3-2) an
# opponent's conference or division for that game's own season, via
# `TeamNarrowed.narrow_alignment` - both read by the one `_apply_situation`
# helper both relations' shared steps call; `until` is the
# inclusive LAST season of a `since`-bounded range (a decade, "2019-20 to
# 2023-24"), read the same way `since` already is (`_span_of`/`scoped_team`) -
# never alone (`_validated_until`), so a template honors it only by also
# honoring `since`.
#
# `period` and `half` are the period relation's team half (ROADMAP plan item
# 4): a quarter or a half narrows what a read SEES of each game - the
# linescore's points, and every other column rebuilt from the plays
# (`team_games.team_period_line_sql`) - applied by `team_games` through
# `TeamNarrowed.narrow_periods`, the counterpart of the player relation's.
TEAM_RELATION_SCOPING = frozenset({"opponent", "venue", "date", "since", "until", "span", "order", "game_n", "situation", "period", "half"})
"""The scoping slots every template on the team-games relation honors.

.. versionadded:: 4.4.0

.. versionchanged:: 4.4.0
   Adds ``situation`` and ``until`` (step 3, K1).

.. versionchanged:: 5.0.0
   Adds ``period`` and ``half`` (the period relation's team half).
"""

# The cells a template on the team relation does NOT honor, each with why. A
# reason has to be about the template's answer, not its code - the same rule
# RELATION_SCOPING_EXCLUDED follows.
TEAM_RELATION_SCOPING_EXCLUDED: dict[str, dict[str, str]] = {
    # A leaderboard ranks one season's (or one since-bounded span's) teams
    # against each other; none of these four narrow that pool to a single
    # game or a single opponent, and a career total across every season on
    # record is not built.
    "team_leaderboard": {
        "opponent": "a leaderboard ranks every team; it has no reading for one named opponent",
        "date": "a leaderboard ranks a season, not one day's games",
        "span": "a leaderboard ranks one season's teams; a career total across every season is not built",
        "order": "a leaderboard ranks a season, not a window of games",
        "game_n": "a leaderboard ranks a season, not one game of a series",
        # step 3, K1: a leaderboard ranks a season or a since/until-bounded
        # span of them; narrowing that pool to one weekday, month or holiday
        # within it is a different question from ranking the span itself.
        "situation": "a leaderboard ranks a season, not the games in one weekday, month or holiday within it",
        "period": "a leaderboard ranks teams' season lines, and no season line is split by quarter",
        "half": "a leaderboard ranks teams' season lines, and no season line is split by half",
    },
    # A record for one game is a single result, which game_log already answers
    # directly, and a record over a limited number of recent games is the same
    # substitution the player relation refuses for the same two cells. `since`
    # and `game_n` used to be excluded here too ("not built yet" - a reason
    # about the code, which the rule above this dict forbids) - step 3, team
    # cells, reads both: `since` the same since-bounded `_Span` a career
    # already reads (`_record_narrowed`), `game_n` the relation's own
    # `narrow_series_game` check (`_games_record_games`).
    "team_record": {
        "date": "a record for one calendar date is a single game, which game_log already answers directly",
        "order": "a record over a limited set of games is a game_log question",
        "period": "a record is won and lost over whole games; a quarter has no winner the record could count",
        "half": "a record is won and lost over whole games; a half has no winner the record could count",
    },
    # head_to_head tallies every meeting in the span; `order` and `game_n`
    # pick out a subset of that tally, and neither is built. `since` and
    # `span` used to be excluded too ("not built yet") - step 3, team cells,
    # reads both the same since-bounded or whole-career `_Span` team_record's
    # own `since`/`span` now read, over every meeting in it rather than one
    # season.
    "head_to_head": {
        "order": "head_to_head counts every meeting in the span; picking the last N of them is not built",
        "game_n": "head_to_head counts every meeting; one numbered game of a series is not read here",
        # step 3, K1 wires the calendar narrowing into team_quarter_points and
        # team_record; head_to_head's own since/career span result
        # (_head_to_head_span_result) does not read it yet - a weekday or
        # holiday cut of an all-time series is a real question, just not this
        # step's.
        "situation": "head_to_head tallies every meeting in the span; narrowing that tally to one weekday, month or holiday within it is not built",
        "period": "a series is won and lost in whole games; a quarter of each meeting has no winner to tally",
        "half": "a series is won and lost in whole games; a half of each meeting has no winner to tally",
    },
}
"""Per template, the team relation's slots it refuses, and why.

.. versionadded:: 4.4.0
"""


def team_relation_scoping(intent: str, *extra: str) -> frozenset[str]:
    """The team relation's cells ``intent`` honors: the whole set and
    ``extra``, less the cells it excludes (:data:`TEAM_RELATION_SCOPING_EXCLUDED`).

    .. versionadded:: 5.0.0
       Public, for the team-season readers' declaration
       (``compose.present.STATED_SCOPING``); ``_team_relation_scoping`` until then.
    """
    return frozenset((TEAM_RELATION_SCOPING | set(extra)) - set(TEAM_RELATION_SCOPING_EXCLUDED.get(intent, {})))


_team_relation_scoping = team_relation_scoping


# What each template actually honors. Anything not listed here honors none.
HONORED_SCOPING: dict[str, frozenset[str]] = {
    # The templates on the player-games relation: see RELATION_SCOPING.
    # game_log is the compiler's (compose.COMPILED_INTENTS): what its retired
    # words state is compose.plan.STATED_SCOPING's.
    # player_stat is the compiler's too (step (g)): its retired words state
    # the relation's set and `season_type_unstated`, read from box scores
    # (`_player_stat_reads_box_scores`, `_player_relation_season_type`).
    # team_quarter_points is the compiler's (Phase 2, slice (iv)):
    # compose.present.STATED_SCOPING.
    # period_leaderboard is the compiler's (Phase 2, slice (iv)):
    # compose.present.STATED_SCOPING.
    # head_to_head is the compiler's (Phase 2, slice (iv)): what its retired
    # words state is compose.present.STATED_SCOPING's.
    # A shot read that takes its games from the relation by event id (step 3,
    # C5), the same shape as period_split: every relation slot is answerable -
    # `span` "career" by drawing (or averaging) every season on record rather
    # than the latest with data (shots._career_shot_note); `order` now honors
    # `limit` as a window of games rather than always exactly one
    # (common.relation_window / the relation's own windowed read), which is
    # what fixes "his last two games" having drawn the whole season. No cell
    # is excluded - unlike period_split, a shot read has no reason a venue, an
    # opponent, a date or a box-score line on the games it draws from cannot
    # narrow it.
    "shot_chart": _relation_scoping("shot_chart"),
    # shot_distance is the compiler's (Phase 2, slice (v): the shot relation's
    # reader, compose.shots): compose.plan.STATED_SCOPING.
    # player_netpoints and fingerprint are the compiler's (Phase 2, slice (v)):
    # compose.plan.STATED_SCOPING, which keeps why `date` is listed for the
    # fingerprint (honored by refusing it in the reader's own words).
    # `span` "career" is honored by summing every season: a career leaderboard
    # from the per-team season rows, and a career count or high from every box
    # score since 1993-94. Each answer names the pool, since neither is all-time.
    # `rate` is honored by ANSWERING it where the metric has that form (a
    # season total) and by refusing, in the metric's own name, where it does
    # not ("/ 90"). It was unlisted, so check_scope raised before the template
    # ran: the per-90 question was refused naming only the slot, and the
    # `rate == "total"` branch below was unreachable in the pipeline.
    # A career is every season on record rather than the current one; see
    # _condition_scope. `without` is the teammate with_without divides by, and
    # `split` is the one player_splits was asked for. `venue` and `opponent`
    # are filters on the same box-score rows `team` already narrows - a home
    # or road split for a player is answerable the same way a team's already
    # is (see team_record below).
    # player_splits is the compiler's too (step (g)): compose.plan.STATED_SCOPING.
    # `opponent` narrows BOTH rows of the split to one opponent's games, and
    # the title says so - "Embiid career record vs boston" is his record in the
    # games his team played Boston, not overall (#163).
    # with_without is the compiler's too (step (g), the team relation's
    # `presence` group): compose.plan.WITH_WITHOUT_STATED.
    # `opponent` and `order` are excluded, each with its reason
    # (RELATION_SCOPING_EXCLUDED); a teammate's absence, a venue and a date
    # narrow the first player's games as they do on every relation template.
    # player_matchup and streak are the compiler's too (step (g), the `pair`
    # and `run` shapes): compose.plan.STATED_SCOPING.
    # team_record is the compiler's (Phase 2, slice (iv)): compose.plan.STATED_SCOPING.
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

    Only the four checks take a slot dict still (:func:`check_scope`,
    :func:`check_coverage`, :func:`coverage_caveat` and ``_sources_for``),
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
        return TEMPLATE_SOURCES.get(intent, ())
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
    return TEMPLATE_SOURCES["team_leaderboard"]


def _sources_for_leaderboard(scope: Scope) -> tuple[str, ...]:
    """The table the asked-for leaderboard metric is ranked from."""
    metric = resolve_metric(scope.stat, career=scope.span == "career")
    spec = LEADERBOARD_METRICS.get(metric) if metric else None
    # An unrecognized metric is left to the template, which refuses it with a
    # better message than a coverage floor could.
    return (spec.table,) if spec else ()


def check_coverage(intent: str, scope: Scope | Mapping[str, Any]) -> str | None:
    """Why this question's season is out of reach, or None.

    Returned rather than raised, which is the opposite of :func:`check_scope`
    and deliberate. check_scope raises so the compiler gets its turn at the
    same point, and may do better. Nothing does better here: a season under
    the floor is empty for every reader. The refusal IS the answer.

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


#: Templates that honor one NAMED half of the starter/bench split and refuse
#: the bare category, which asks for a table they do not produce.
_SPLIT_SIDE_ONLY = frozenset({"game_log", "player_stat", "period_split", "shot_chart", "shot_distance"})


"""``{"fta": "freeThrowsAttempted", ...}`` - the question's word for a box-score column.

.. versionadded:: 4.3.0
"""


def narrow_measures(narrowed: Narrowed, filters: list[MeasureFilter]) -> None:
    """Apply :func:`measure_filters`' lines to a relation read, each with its
    label so the answer names what it kept."""
    for line in filters:
        narrowed.narrow_measure(line.column, line.op, line.value, line.label)


def check_scope(intent: str, scope: Scope | Mapping[str, Any]) -> None:
    """Raise if the question scoped to particular games and this template
    cannot honor that. Falling through is slow; answering a different question
    quickly is worse.

    A scoping slot is set when its :class:`~association.query.reading.Scope`
    field is truthy: a field at its default (None, an empty tuple, False) is
    the slot absent, the reading this has always taken of a falsy slot.

    .. versionchanged:: 5.0.0
       Reads the typed :class:`~association.query.reading.Scope`. A slot dict
       is still taken, through :meth:`~association.query.reading.Scope.from_slots`,
       until every caller passes ``reading.scope``.
    """
    scope = _as_scope(scope)
    ignored = unhonored_scoping(intent, scope, HONORED_SCOPING.get(intent, frozenset()))
    if ignored:
        raise TemplateUnsupported(f"{intent} cannot honor {ignored} - it would answer for a different span than was asked")


def unhonored_scoping(intent: str, scope: Scope, honored: frozenset[str]) -> list[str]:
    """The scoping slots ``scope`` sets that ``honored`` does not hold, for
    ``intent`` - :func:`check_scope`'s rule, on its own so the compiler's
    presenters apply it too: a template refuses such a slot, and a presenter
    steps aside for one its words do not state
    (:data:`~association.query.compose.plan.STATED_SCOPING`), leaving the
    compiler's own sentence to answer. A slot is set when its field is truthy:
    a field at its default (None, an empty tuple, False) is the slot absent.

    .. versionadded:: 5.0.0
    """
    ignored = sorted(name for name in SCOPING_SLOTS if getattr(scope, name) and name not in honored)
    # `split` is honored by the filtering templates only for a NAMED half. The
    # bare category means "show me both groups", which is player_splits' whole
    # answer and something they cannot do - so it is refused here rather than
    # quietly filtered to one side or quietly ignored.
    if scope.split == "starter_bench" and intent in _SPLIT_SIDE_ONLY:
        ignored = sorted({*ignored, "split"})
    return ignored


TemplateUnsupported = Unsupported
"""Raised when slots don't validate. The caller offers the compiler the
same point and then refuses naming the reason, so a slip in the reading
degrades to a refusal rather than to a wrong answer. The same exception as
the reader's :class:`~association.query.reading.Unsupported` since Phase
2's first slice: a template refusing a slot, the reader having no reading of
a point and the planner unable to say a query are one verdict, "this
cannot be answered as asked", and the reader-side phrase readers
(``query/lines.py``) raise it for the templates too.

.. versionchanged:: 5.0.0
   An alias of ``reading.Unsupported``, not a class of its own.
"""


@dataclass(frozen=True)
class TemplateContext:
    """What a template is given: the warehouse, and somewhere to write output.

    Templates took a bare connection until shot_chart needed an output
    directory too. A small context rather than the whole answering loop keeps
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


def _clarify(text: str, candidates: list[str], kind: str = "player", active: int = 0) -> TemplateResult:
    """A handled outcome, not a fall-through: the template knows exactly what
    is ambiguous, so it says so instead of passing the problem along.

    The sentence itself is entities.clarification, because the chart entry
    points reach the same ambiguity without going through a template and have
    to phrase it identically."""
    return TemplateResult(data={"ambiguous": text, "candidates": candidates}, answer=clarification(text, candidates, kind, active))


_GAME_LOGS = GAME_LOGS


_BOX_SCORES = BOX_SCORES


def career_end(season: int | None) -> int | None:
    """The ``through`` a span narrows a name by. None for one season, which
    narrows by ``season`` itself; the current season for a career, which keeps
    everybody with a row on record - Dell Curry's career is a real answer to
    "curry career points" - but names whoever plays now first, rather than
    cutting Stephen behind five retired Currys the way the season question did."""
    return current_season() if season is None else None


_career_end = career_end


def resolved_player(
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
    guessing. Callers must forward it.

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


_resolved_player = resolved_player


def resolved_team(con: duckdb.DuckDBPyConnection, text: Any, season: int | None = None) -> Entity | TemplateResult:
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


_resolved_team = resolved_team


def slot_season(scope: Scope) -> int | None:
    """The season a question's team names are read for: the one it named, or
    None for "now" - the same default every template applies."""
    return scope.season


_slot_season = slot_season


def season_phrase(season: int, season_type: int) -> str:
    """``"2026 regular season"``: a season and its type as an answer names them.

    .. versionadded:: 5.0.0
       Public, as the relation's shared step; ``_period`` is this.
    """
    return f"{season} {SEASON_TYPE_NAMES.get(season_type, 'regular season')}"


_period = season_phrase


def table_cell(value: Any) -> str:
    """A value in an aligned column: a fixed decimal, never trailing-zero
    stripped - "25" next to "27.7" reads as a different unit.

    .. versionadded:: 5.0.0
       Public, as the sayer's phrase helper; ``_table_cell`` is this.
    """
    if value is None:
        return "-"
    return f"{value:.1f}" if isinstance(value, float) else str(value)


_table_cell = table_cell


def format_value(value: Any) -> str:
    """A figure as an answer prints it: a float to two places (three below
    one), trailing zeros dropped; anything else as itself.

    .. versionadded:: 5.0.0
       Public, as the sayer's phrase helper; ``_format_value`` is this.
    """
    if isinstance(value, float):
        return f"{value:.3f}".rstrip("0").rstrip(".") if abs(value) < 1 else f"{value:.2f}".rstrip("0").rstrip(".")
    return str(value)


_format_value = format_value


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


# Per-season history columns: stat -> (label, [(column, header, key), ...]).
# A percentage is reported with its makes and attempts, because a percentage
# without volume behind it is the thing people ask "out of how many?" about.
#
# `key` is the STABLE name `player_history`'s `data["seasons"]` rows carry
# each column under - identical to `column` for every entry but
# `twoPointFieldGoalPct`'s, whose `column` is a SQL expression (see its own
# comment below), not a valid key. Before `key` existed, that expression WAS
# the dict key: the page looked up "twoPointFieldGoalPct" by its own slot
# name, found nothing under a key that long, and printed the raw SQL as a
# column header with an empty column beneath it and a flat sparkline (seen
# live on the rendered page, 2026-09-24, on "lebron's 2-pt percentage over
# the last 10 years").
HISTORY_COLUMNS: dict[str, tuple[str, list[tuple[str, str, str]]]] = {
    "threePointFieldGoalPct": (
        "3PT%",
        [
            ("threePointFieldGoalPct", "3PT%", "threePointFieldGoalPct"),
            ("threePointFieldGoalsMade", "3PM", "threePointFieldGoalsMade"),
            ("threePointFieldGoalsAttempted", "3PA", "threePointFieldGoalsAttempted"),
        ],
    ),
    "fieldGoalPct": ("FG%", [("fieldGoalPct", "FG%", "fieldGoalPct"), ("fieldGoalsMade", "FGM", "fieldGoalsMade"), ("fieldGoalsAttempted", "FGA", "fieldGoalsAttempted")]),
    "freeThrowPct": (
        "FT%",
        [("freeThrowPct", "FT%", "freeThrowPct"), ("freeThrowsMade", "FTM", "freeThrowsMade"), ("freeThrowsAttempted", "FTA", "freeThrowsAttempted")],
    ),
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
            ("100.0 * (fieldGoalsMade - threePointFieldGoalsMade) / NULLIF(fieldGoalsAttempted - threePointFieldGoalsAttempted, 0)", "2PT%", "twoPointFieldGoalPct"),
            ("(fieldGoalsMade - threePointFieldGoalsMade)", "2PM", "twoPointFieldGoalsMade"),
            ("(fieldGoalsAttempted - threePointFieldGoalsAttempted)", "2PA", "twoPointFieldGoalsAttempted"),
        ],
    ),
    "points": ("points per game", [("avgPoints", "PPG", "avgPoints")]),
    "rebounds": ("rebounds per game", [("avgRebounds", "RPG", "avgRebounds")]),
    "assists": ("assists per game", [("avgAssists", "APG", "avgAssists")]),
    "steals": ("steals per game", [("avgSteals", "SPG", "avgSteals")]),
    "blocks": ("blocks per game", [("avgBlocks", "BPG", "avgBlocks")]),
    "minutes": ("minutes per game", [("avgMinutes", "MPG", "avgMinutes")]),
    "threePointFieldGoalsMade": ("3-pointers per game", [("avgThreePointFieldGoalsMade", "3PM/G", "avgThreePointFieldGoalsMade")]),
}


def season_label(season: int) -> str:
    """1994 -> "1993-94": seasons are named for the year they end in."""
    return f"{season - 1}-{season % 100:02d}"


_season_name = season_label


def count_games(count: int) -> str:
    """``"1 game"``, ``"1,200 games"``.

    .. versionadded:: 5.0.0
       Public, as the sayer's phrase helper; ``_count_games`` is this.
    """
    return f"{count:,} game{'' if count == 1 else 's'}"


_count_games = count_games


@dataclass(frozen=True)
class ResolvedSpan:
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
    #: The inclusive LAST season of a ``since``-bounded range ("2019-20 to
    #: 2023-24", a decade) - None for an open-ended "since 2022", which still
    #: reaches the present. Only ever set alongside ``since`` (see
    #: :func:`_span_of`); the answer says "2019-2024" rather than "since 2019"
    #: once it is.
    #:
    #: .. versionadded:: 4.4.0
    until: int | None = None
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
        """SQL restricting ``column`` to these seasons, and its parameters.

        .. versionchanged:: 4.4.0
           Bounds the upper end too when ``until`` is set.
        """
        if self.season is not None:
            return f"{column} = ?", [self.season]
        # The phantom is excluded by name, not left to the floor: 1993 is a full,
        # healthy-looking copy of 1994 (see coverage.Coverage.phantom), and a
        # career that counted it would list every 1993-94 game twice.
        excluded = f" AND {column} NOT IN ({', '.join('?' for _ in self.phantom)})" if self.phantom else ""
        if self.until is not None:
            return f"{column} BETWEEN ? AND ?{excluded}", [self.first, self.until, *self.phantom]
        return f"{column} >= ?{excluded}", [self.first, *self.phantom]

    def years(self, first: Any, last: Any) -> str:
        """The seasons a career answer's rows actually reach: ``"2024-2026
        regular seasons"``, or one season's name.

        .. versionchanged:: 4.4.0
           A :data:`~association.query.player_games.BOTH_SEASON_TYPES` span
           pluralizes each half ("2024-2026 regular seasons and postseasons")
           rather than tacking an "s" onto the end of "regular season and
           postseason", which reads as though only the second half repeated.
        """
        if first is None or last is None:
            return f"{self.kind}s"
        if first == last:
            return f"{first} {self.kind}"
        if self.season_type == BOTH_SEASON_TYPES:
            return f"{first}-{last} regular seasons and postseasons"
        return f"{first}-{last} {self.kind}s"

    def during(self, first: Any = None, last: Any = None, whose: str = "his career") -> str:
        """The span as it ends a sentence: ``"in the 2026 regular season"`` or
        ``"over his career (2019-2026 regular seasons)"``.

        .. versionchanged:: 4.4.0
           Says "from 2019 through 2024" rather than "since 2019" once
           ``until`` bounds the range - the "2011-2019" wording (step 3, K1).
        """
        if self.season is not None and self.ordinal is not None:
            return f"in his {ordinal_word(self.ordinal)} season ({_period(self.season, self.season_type)})"
        if self.season is not None:
            return f"in the {_period(self.season, self.season_type)}"
        if self.since is not None and self.until is not None:
            return f"from {self.since} through {self.until} ({self.years(first, last)})"
        if self.since is not None:
            return f"since {self.since} ({self.years(first, last)})"
        return f"over {whose} ({self.years(first, last)})"


_Span = ResolvedSpan


def validated_until(until: int | None, since: int | None) -> int | None:
    """The validated ``until`` slot: an inclusive last season, named beside
    ``since`` only - the router never emits one without the other (a decade,
    or a named range like "2019-20 to 2023-24"), so a caller checks this
    before ``since`` has necessarily reached :func:`_span_of` itself (a
    template that reads ``since`` through its own code path, the way
    ``team_leaderboard`` does for a non-record metric, would otherwise drop
    ``until`` silently rather than refusing it - the failure shape AGENTS.md
    warns against).

    .. versionadded:: 4.4.0

    .. versionchanged:: 5.0.0
       Public, for the team-season ranking's reader; ``_validated_until`` until then.
    """
    if not until:
        return None
    if not since:
        raise TemplateUnsupported(f"until {until} with no since")
    if until < since:
        raise TemplateUnsupported(f"until {until} before since {since}")
    return until


_validated_until = validated_until


def span_of(span: Literal["career"] | None, season: int | None, season_type: int, table: str, since: int | None = None, until: int | None = None) -> _Span:
    """The seasons a question covers. ``table`` sets how far back a career
    reaches - box scores from 1994, the season line from 1977 - since a career
    is only as long as the table it is summed from. ``since`` (a season) is a
    career that starts there instead: every season from it on, the phantom
    still excluded, and never earlier than the table reaches. ``until`` bounds
    the other end - the inclusive last season of a range - and is validated
    against ``since`` here too (see :func:`_validated_until`), so a caller
    that reads ``since`` straight off the slots and hands both here without
    checking first still gets the same refusal.

    .. versionchanged:: 4.3.0
       Honors ``since``.

    .. versionchanged:: 4.4.0
       Honors ``until`` (step 3, K1).
    """
    until = _validated_until(until, since)
    if since:
        if season:
            raise TemplateUnsupported(f"since {since} and the {season} season at once")
        coverage = COVERAGE[table]
        return _Span(None, season_type, max(since, coverage.floor(season_type).season), coverage.phantom, since=since, until=until)
    if span is None:
        return _Span(season or current_season(), season_type, defaulted=not season)
    if season:
        # "Career" and a named year at once. Either reading answers a different
        # question from the other, so neither is picked.
        raise TemplateUnsupported(f"a career span and the {season} season at once")
    coverage = COVERAGE[table]
    return _Span(None, season_type, coverage.floor(season_type).season, coverage.phantom)


_span_of = span_of


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


def _narrow_player_games(
    con: duckdb.DuckDBPyConnection,
    player: Entity,
    span: _Span,
    *,
    opponent: Any,
    venue: Any,
    without: Any,
    split: Any = None,
    game_n: Any = None,
    team: Any = None,
    conditions: Sequence[ConditionSpec] = (),
) -> Narrowed | TemplateResult:
    """``player``'s games in ``span``, narrowed to an opponent, a venue, a
    teammate's absence and a starter/bench half where the question named them.
    A name that needs a clarifying question comes back as the TemplateResult
    asking it.

    Every narrowing here is a filter over the same set of player-games, which
    is why they compose: a new one becomes available to every caller at once
    rather than being taught to each template separately. ``split`` was the
    fourth, and it reaches both `game_log` and `player_stat` through this one
    change. ``conditions`` are the scope's typed entries
    (:class:`~association.query.reading.ConditionSpec`), each read by
    :func:`_condition_from_slot`.

    .. versionchanged:: 4.3.0
       Honors one half of the starter/bench split (``split``), and one game of
       each playoff series (``game_n``).

    .. versionchanged:: 4.4.0
       Takes ``team`` - the player's OWN team, as opposed to ``opponent`` -
       for the shape ``player_stat`` alone opts into
       (``templates.common.OWN_TEAM_RESTORABLE_INTENTS``): "lebron stats as a
       starter for Miami" (yardstick-v2 F166) keeps only the games he played
       for that team, unlike ``game_log``'s own ``team``/``opponent`` dance
       (``games._team_slot_for_player``), which still drops a team the
       player actually played for rather than narrowing by it - a template
       has to ask for this explicitly, so nothing else on the relation is
       affected.
    """
    if game_n and span.season_type != 3:
        # A series has games 1-7; a regular season has nothing "game 4" names -
        # true of BOTH_SEASON_TYPES too (0 != 3), so "game 4 including the
        # playoffs" still refuses rather than guessing which type "game 4" was.
        raise TemplateUnsupported(f"game {game_n} names a game of a playoff series, and this is a {span.kind} question")
    season_clause, season_params = span.clause("pgl.season")
    type_clause, type_params = season_type_clause("pgl.season_type", span.season_type)
    narrowed = Narrowed(
        base=["pgl.athlete_id = ?", type_clause, season_clause, "NOT pgl.did_not_play"],
        base_params=[player.id, *type_params, *season_params],
    )
    if team:
        resolved_team = team if isinstance(team, Entity) else _resolved_team(con, team, season=span.season)
        if isinstance(resolved_team, TemplateResult):
            return resolved_team
        narrowed.team = resolved_team
        narrowed.extra.append("pgl.team_id = ?")
        narrowed.extra_params.append(resolved_team.id)
    if opponent:
        # A caller that has already resolved the team (it needs the name for
        # its answer before the games are read) passes the Entity; text is
        # resolved here, so a clarification about the team comes back as the
        # answer either way.
        team = opponent if isinstance(opponent, Entity) else _resolved_team(con, opponent, season=span.season)
        if isinstance(team, TemplateResult):
            return team
        narrowed.opponent = team
        narrowed.extra.append("pgl.opponent_team_id = ?")
        narrowed.extra_params.append(team.id)
    if venue:
        narrowed.venue = venue
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
        narrowed.add_condition(_absence_condition(con, mate, player, span, narrowed.opponent), box_source(con))
    # The general shape of the same thing (ROADMAP plan item 3): any
    # player, on either side, under any predicate - "when Embiid and Paul
    # George start", "vs LeBron without Durant", "in games Maxey had 20+".
    for entry in conditions:
        condition = _condition_from_slot(con, entry, player, span, narrowed.opponent)
        if isinstance(condition, TemplateResult):
            return condition
        narrowed.add_condition(condition, box_source(con))
    if game_n:
        narrowed.narrow_series_game(int(game_n))
    return narrowed


def player_relation_season_type(scope: Scope) -> int:
    """The ``season_type`` to read the player relation for: ``BOTH_SEASON_TYPES``
    when the question asked for both explicitly ("including the playoffs") or
    named none at all in a "last N games" question
    (``season_type_unstated`` - ``router._BOTH_SEASON_TYPES_WORDS`` and
    ``router._route_game_log_recent_span`` both set it, for the same honored
    meaning), else the value the router read from the question's own words.

    ``game_log``'s own "last N games" merge (``_player_game_log_mixed``) does
    not call this - it interleaves two separate reads rather than reading one
    relation with ``season_type IN (2, 3)``, because it needs each type's own
    count for the header. Every other reader on the relation (``scoped_player``
    here, and ``threshold_count``'s own league/one-player read in
    ``templates/players.py``) wants exactly the single combined read this
    gives, which is simpler than a merge: an aggregate has no rows to
    interleave.

    .. versionadded:: 4.4.0
    """
    if scope.season_type_unstated:
        return BOTH_SEASON_TYPES
    return scope.season_type or REGULAR_SEASON


_player_relation_season_type = player_relation_season_type


def scoped_player(
    con: duckdb.DuckDBPyConnection,
    scope: Scope,
    missing: str,
    *,
    table: str,
    available: Availability | tuple[Availability, ...],
    span: Any,
    season: Any,
) -> tuple[Entity, _Span] | TemplateResult:
    """The player a question is about and the seasons it covers, settled in the
    one order that works - or the TemplateResult asking which player was meant.

    The span comes first because it is what narrows an ambiguous name: a career
    keeps Dell Curry and this season does not. An ordinal season ("his 18th
    season") cannot be a year until he is known, so the name is narrowed over
    his career and the ordinal settled after. Every template that reads a
    player's games wrote these same steps out for itself; a fix to one - the
    raw ``season`` slot, not a defaulted one, is what narrows the name - had to
    be found and repeated in each.

    ``span`` and ``season`` are passed rather than read, because a template may
    have a reason to override them: a date names its own game, so ``game_log``
    reads the career for it. ``since``, ``season_n`` and ``season_type`` are the
    question's and are read here.

    .. versionadded:: 4.4.0

    .. versionchanged:: 5.0.0
       Reads the typed :class:`~association.query.reading.Scope`. A slot dict
       is still taken, through :meth:`~association.query.reading.Scope.from_slots`,
       until every caller passes ``reading.scope``.
    """
    season_n = scope.season_n
    seasons = _span_of("career" if season_n else span, None if season_n else season, _player_relation_season_type(scope), table, since=scope.since, until=scope.until)
    player = _resolved_player(con, scope.player, missing, available=available, season=seasons.season, through=_career_end(seasons.season))
    if isinstance(player, TemplateResult):
        return player
    settled = settle_ordinal_season(con, player, season_n, seasons)
    if isinstance(settled, TemplateResult):
        return settled
    return player, settled


def _apply_situation[NarrowedT: (Narrowed, TeamNarrowed)](narrowed: NarrowedT, situation: str) -> None:
    """Read a ``situation`` value as the calendar narrowing it names (a
    weekday, a month, a fixed day, "since <day>") or - the other half of the
    same slot - the conference/division narrowing it names ("vs the west",
    "against the southeast division"), and apply whichever one it is to
    ``narrowed``. Refused BY VALUE (never silently dropped) when it names
    neither: the relation carries the game's Eastern day and each opponent's
    season-alignment, and nothing about the player's age or a return from
    injury, so dropping the slot would answer a wider question under a
    heading that promised the narrower.

    The one place :func:`scoped_games`/:func:`league_games` (the player
    relation) and :func:`team_games` (the team relation) turn a ``situation``
    value into a clause, so a reading either function adds here reaches every
    template on both relations at once - the same discipline every other
    relation-scoping cell keeps (see ``RELATION_SCOPING``/``TEAM_RELATION_SCOPING``
    above).

    .. versionadded:: 4.4.0
    """
    calendar = parse_situation(situation)
    if calendar is not None:
        narrowed.narrow_calendar(calendar)
        return
    alignment = parse_alignment(situation)
    if alignment is not None:
        narrowed.narrow_alignment(alignment)
        return
    raise TemplateUnsupported(
        f'no narrowing in situation {situation!r} - a weekday, a month, a holiday, "since <day>", a conference ("vs the west") or a division '
        '("vs the southeast division") is read; an age or anything else is not'
    )


def relation_window(scope: Scope) -> tuple[str, int] | None:
    """The WINDOW :func:`scoped_games` cuts the narrowed games to - the
    newest or oldest N, after every other filter
    (:attr:`association.query.player_games.Narrowed.window`) - or ``None``
    where neither slot narrows anything.

    A named ``order`` wins outright. Absent one, a ``limit`` alone still
    means "his last N games": measured against the router's own traces for
    "Create a shot chart for Steph Curry's last two games of the regular
    season" (step 3, C5's finding) - four separate runs, three different
    builds, all emit ``{'limit': 2, ...}`` with no ``order`` at all, so a rule
    gated on ``order`` alone would never reach the real question. No template
    on the relation has any other use for a bare ``limit`` - it ranks nothing
    here, only `leaderboard` does that, over a different table - so there is
    no other reading for one to collide with.

    .. versionadded:: 4.4.0

    .. versionchanged:: 5.0.0
       Public (was ``_relation_window``): the period reader cuts its
       cross-season window by it (``compose.periods``).
    """
    order: str | None = scope.order
    if order is None:
        # A limit, when set, is 1 or more: the Scope's own range rule.
        if scope.limit is None:
            return None
        order = "recent"
    return order, _clamp_limit(scope.limit, default=1)


def _apply_period_condition(con: duckdb.DuckDBPyConnection, narrowed: Narrowed, condition: PeriodCondition) -> TemplateResult | None:
    """A quarter or half used as a condition on which games count - the
    ``period_condition`` cell of :data:`RELATION_SCOPING`
    (:class:`~association.query.reading.PeriodCondition`), applied here for
    a named player's games and a league-wide read alike
    (:meth:`~association.query.player_games.Narrowed.narrow_period_condition`).
    A column rebuilt from the plays, in a warehouse holding none, is refused
    rather than read as zero - every game would fail the condition.

    .. versionadded:: 5.0.0
    """
    asked = period_narrowing(Scope(period=condition.period, half=condition.half))
    if asked is None or condition.stat not in PERIOD_COLUMNS or condition.threshold < 1:
        raise TemplateUnsupported(f"no period condition reads {condition!r}")
    periods, label = asked
    plays = _has_table(con, "plays")
    if not plays and condition.stat in PERIOD_PLAYS_COLUMNS:
        noun = STAT_LABELS.get(condition.stat, condition.stat)
        message = f"Games with {condition.threshold}+ {noun}s in the {label} cannot be picked out here: a period's {noun}s are rebuilt from play-by-play, and this warehouse holds none."
        return TemplateResult(data={"message": message}, answer=message)
    noun = STAT_LABELS.get(condition.stat, condition.stat)
    # Said outright either way, so the reading is visible and the other is
    # one word away: "exactly 1 3-pointer" against "1+ 3-pointers".
    phrase = f"exactly {condition.threshold} {noun}{'' if condition.threshold == 1 else 's'} in the {label}" if condition.op == "=" else f"{condition.threshold}+ {noun}s in the {label}"
    log_columns = frozenset(row[0] for row in con.execute("DESCRIBE player_game_log").fetchall())
    narrowed.narrow_period_condition(periods, condition.stat, condition.threshold, phrase, op=condition.op, plays=plays, log_columns=log_columns)
    return None


def apply_period(con: duckdb.DuckDBPyConnection, narrowed: Narrowed, scope: Scope) -> None:
    """A quarter or half narrows every read of the relation to that part of
    each game (:meth:`~association.query.player_games.Narrowed.narrow_periods`)
    - the ``period``/``half`` cells of :data:`RELATION_SCOPING`, applied here
    for a named player's games and a league-wide read alike."""
    asked = period_narrowing(scope)
    if asked is not None:
        log_columns = frozenset(row[0] for row in con.execute("DESCRIBE player_game_log").fetchall())
        narrowed.narrow_periods(*asked, plays=_has_table(con, "plays"), log_columns=log_columns)


_apply_period = apply_period


def _has_table(con: duckdb.DuckDBPyConnection, name: str) -> bool:
    """Whether the warehouse holds ``name`` - a fixture or a partial load may not."""
    row = con.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?", [name]).fetchone()
    return bool(row and row[0])


def scoped_games(
    con: duckdb.DuckDBPyConnection,
    player: Entity,
    span: _Span,
    scope: Scope,
    *,
    opponent: Any,
    measures: list[MeasureFilter],
    date: str | None = None,
    team: Any = None,
) -> Narrowed | TemplateResult:
    """``player``'s games in ``span`` under every row-level narrowing the
    question carries: opponent, venue, an absent teammate, a starter/bench
    half, a game of each playoff series, lines on box-score columns, one date,
    and a window (``order``/``limit``) cut after all of the above.

    Each is a filter over the same rows, so each means the same thing whatever
    the template then does with the rows - list them, average them, count them.
    That is why they are read from ``scope`` HERE and not by each template: a
    slot this does not read is one no template on the relation can honor, and a
    slot it does read reaches all of them at once. ``check_scope`` has already
    refused any the calling template does not declare, so nothing arrives here
    that the template has not claimed.

    ``opponent`` is passed because ``game_log`` may have rewritten it (a
    ``team`` beside a named player is his opponent) and a template that needs
    the team's name before the read passes it already resolved; ``measures``
    because each template decides what a bare ``threshold`` means before any
    name is resolved. ``team`` is passed the same way, but stays ``None`` for
    every caller except ``player_stat`` - see
    :func:`_narrow_player_games`'s own note on why this is a caller's explicit
    choice rather than a plain read of ``scope.team`` here.

    .. versionadded:: 4.4.0

    .. versionchanged:: 4.4.0
       Sets :attr:`Narrowed.window` from ``order``/``limit`` (step 3, C5) -
       see :func:`relation_window`. A no-op for a caller that reads its rows
       through :func:`association.query.player_games.rows_sql` directly
       (``game_log``, ``player_stat``, ``period_split``): that reader takes
       its own ``order``/``limit`` arguments and never consults ``.window``,
       so setting it here changes nothing for them. Only a reader built on
       :func:`association.query.player_games.aggregate_sql`,
       :func:`~association.query.player_games.grouped_sql` or
       :func:`~association.query.player_games.games_subquery` - all three
       already honor it - is affected, and only when the question's own
       slots set a window.

    .. versionchanged:: 4.4.0
       Takes ``team``.

    .. versionchanged:: 5.0.0
       Reads the typed :class:`~association.query.reading.Scope`. A slot dict
       is still taken, through :meth:`~association.query.reading.Scope.from_slots`,
       until every caller passes ``reading.scope``.
    """
    narrowed = _narrow_player_games(
        con,
        player,
        span,
        opponent=opponent,
        venue=scope.venue,
        # A list, as the slot always was: teammate_names reads a list or one
        # bare name, and a tuple would be neither - every teammate dropped.
        without=list(scope.without),
        split=scope.split,
        game_n=scope.game_n,
        team=team,
        conditions=scope.conditions,
    )
    if isinstance(narrowed, TemplateResult):
        return narrowed
    narrow_measures(narrowed, measures)
    if date:
        start, end = eastern_day_utc_range(date)
        narrowed.extra.append("g.date >= ? AND g.date < ?")
        narrowed.extra_params += [start, end]
        narrowed.date = date
    if scope.situation:
        # Honored where it names the calendar or a conference/division, and
        # refused BY VALUE where it names anything else (an age, "since
        # returning") - see _apply_situation.
        _apply_situation(narrowed, scope.situation)
    _apply_period(con, narrowed, scope)
    if scope.period_condition is not None:
        refused = _apply_period_condition(con, narrowed, scope.period_condition)
        if refused is not None:
            return refused
    narrowed.window = relation_window(scope)
    return narrowed


#: A ``position`` cell's letter as :func:`league_games` narrows the relation by
#: it - the generic code plus every specific one it covers. ``players.position_abbr``
#: holds both the generic letter and the specific one (measured against the
#: warehouse: G 862, F 724, C 502, SG 254, PF 252, SF 247, PG 237), so "forwards"
#: reaches every forward on record and "shooting guards" only those listed as SG.
POSITION_CODES: dict[str, list[str]] = {"G": ["G", "PG", "SG", "GF"], "F": ["F", "PF", "SF", "GF"], "C": ["C"], "PG": ["PG"], "SG": ["SG"], "PF": ["PF"], "SF": ["SF"]}


"""``players.position_abbr`` values a question's position word reaches.

.. versionadded:: 4.4.0
"""


def league_games(con: duckdb.DuckDBPyConnection, span: _Span, scope: Scope, *, position: str | None) -> Narrowed | TemplateResult:
    """Every player's games in ``span`` - the league-wide read a question with
    no player subject narrows the same way one player's games are: an
    opponent, a venue, a team's roster, lines on box-score columns and the
    calendar ``situation`` narrowing - plus one dimension a single player's
    games have no use for, a position.

    Built on :func:`association.query.player_games.league`, the "everyone
    at once" read one optional ``athlete_id`` filter narrows to one man,
    narrowed by the same clauses
    :func:`scoped_games` applies to a named player's - so a narrowing that
    reaches a player's games reaches this read too, without being taught to
    it separately. ``without``, ``split``, ``game_n`` and ``date`` are
    :func:`scoped_games`' own cells that this read has no subject for (whose
    teammate would "without" name? whose start would "split" count?) and are
    not narrowings here; a caller that wants a game-of-series number or a
    fixed date over the league still resolves a player first.

    .. versionadded:: 4.4.0

    .. versionchanged:: 5.0.0
       Reads the typed :class:`~association.query.reading.Scope`. A slot dict
       is still taken, through :meth:`~association.query.reading.Scope.from_slots`,
       until every caller passes ``reading.scope``.
    """
    season_clause, season_params = span.clause("pgl.season")
    narrowed = league(season_clause, season_params, span.season_type)
    # The log LEFT JOINs players; a box score for an athlete missing there
    # would otherwise be counted under a NULL name and reported as a
    # nameless leader.
    narrowed.narrow("pgl.player_name IS NOT NULL")
    if scope.opponent and scope.opponent.strip():
        team = _resolved_team(con, scope.opponent, season=span.season)
        if isinstance(team, TemplateResult):
            return team
        narrowed.opponent = team
        narrowed.narrow("pgl.opponent_team_id = ?", team.id)
    if scope.team and scope.team.strip():
        team = _resolved_team(con, scope.team, season=span.season)
        if isinstance(team, TemplateResult):
            return team
        narrowed.team = team
        narrowed.narrow("pgl.team_id = ?", team.id)
    if scope.venue:
        narrowed.venue = scope.venue
        narrowed.narrow("(g.home_team_id = pgl.team_id) = ?", scope.venue == "home")
    narrow_measures(narrowed, measure_filters(scope.below, scope.above))
    season_n = scope.season_n
    if season_n is not None and season_n > 0:
        # Each player's Nth regular season, counted the way settle_ordinal_season
        # counts one player's: distinct regular seasons on the per-player season
        # table, in order. "Most points in 15th season played" (yardstick-v2
        # F099) is every player's own 15th season, not the 15th season on
        # record.
        narrowed.narrow(
            "pgl.season = (SELECT s.season FROM (SELECT athlete_id, season, ROW_NUMBER() OVER (PARTITION BY athlete_id ORDER BY season) AS n "
            "FROM (SELECT DISTINCT athlete_id, season FROM player_season_stats_deduped WHERE season_type = 2)) s WHERE s.athlete_id = pgl.athlete_id AND s.n = ?)",
            season_n,
        )
        narrowed.ordinal = season_n
    if scope.situation:
        _apply_situation(narrowed, scope.situation)
    if position:
        codes = POSITION_CODES.get(position, [position])
        narrowed.narrow(f"pgl.athlete_id IN (SELECT athlete_id FROM players WHERE position_abbr IN ({', '.join('?' for _ in codes)}))", *codes)
    _apply_period(con, narrowed, scope)
    if scope.period_condition is not None:
        refused = _apply_period_condition(con, narrowed, scope.period_condition)
        if refused is not None:
            return refused
    return narrowed


def condition_player(
    con: duckdb.DuckDBPyConnection,
    scope: Scope,
    missing: str,
    condition_scope: _Scope,
    *,
    team: Entity | None = None,
    measures: list[MeasureFilter] | None = None,
    opponent: Entity | None = None,
) -> tuple[Entity, Narrowed] | TemplateResult:
    """The player a condition template is about, and his games in
    ``condition_scope`` under the question's row-level narrowings, read off
    ``scope``: for the templates that group a player's games by a condition
    (splits, a record above a threshold, a streak, with/without) and read
    them as a subquery (:func:`association.query.player_games.games_subquery`).

    ``condition_scope`` is the template's own ``_Scope``, kept because it reads "career
    ... in 2015" as 2015 where ``_span_of`` refuses the pair - the one place
    the two readers of a player's games disagreed, and not this refactor's to
    settle. ``team`` narrows to the games he played for that team. ``measures``
    is the lines a caller has already read off ``below``/``above`` with
    :func:`measure_filters` - built in the template body, before any name is
    resolved, the same way :func:`player_stat` does it - and defaults to none
    so a caller that does not pass any keeps reading every game in scope.
    ``opponent`` is the team the games are against when the caller has
    already resolved it - ``player_splits`` does, so that a clarification
    about the team comes before one about the player - and is handed to
    :func:`scoped_games` as it is, never resolved a second time; left None,
    the scope's own ``opponent`` is read.

    .. versionadded:: 4.4.0

    .. versionchanged:: 5.0.0
       Reads the typed :class:`~association.query.reading.Scope`, and takes
       ``opponent``: a Scope holds names, so a team already resolved goes
       beside it. The template's own ``_Scope`` is ``condition_scope``.
    """
    subject = scoped_player(con, scope, missing, table="player_game_log", available=_BOX_SCORES, span=None if condition_scope.season else "career", season=condition_scope.season)
    if isinstance(subject, TemplateResult):
        return subject
    player, span = subject
    narrowed = scoped_games(con, player, span, scope, opponent=scope.opponent if opponent is None else opponent, measures=measures or [])
    if isinstance(narrowed, TemplateResult):
        return narrowed
    if team is not None:
        narrowed.narrow("pgl.team_id = ?", team.id)
    return player, whole_span(narrowed)


def whole_span[NarrowedT: (Narrowed, TeamNarrowed)](narrowed: NarrowedT) -> NarrowedT:
    """``narrowed`` with no window: the condition skeletons - a split, a
    record, a run - are read over every game in the span, which is why each
    of them excludes ``order`` in :data:`RELATION_SCOPING_EXCLUDED` ("a
    limited number of recent games is game_log's question"). A bare ``limit``
    is the router's filler on those questions (``limit: 1`` beside "76ers
    record when Maxey scores 20+"), and :func:`relation_window` reads a bare
    limit as the newest N for the templates that DO honor a window - so the
    skeleton that does not says so here, once, instead of the filler cutting a
    63-game record to one game. Measured on the step 3 golden set: three
    recorded questions did exactly that before this existed.

    .. versionadded:: 4.4.0
    """
    narrowed.window = None
    return narrowed


def scoped_team(con: duckdb.DuckDBPyConnection, scope: Scope, missing: str, *, span: Any, season: Any) -> tuple[Entity, _Span] | TemplateResult:
    """The team a question is about and the seasons it covers - the team
    counterpart of :func:`scoped_player`. A franchise's name is a fact about a
    season (see :func:`_resolved_team`: "Hornets" is New Orleans in 2008 and
    Charlotte in 2026), so the span is settled first and the team's name read
    against the season it settles on - the same order ``scoped_player`` keeps,
    even though a team's name (unlike an ambiguous player's) never needs the
    span to disambiguate it.

    Takes: ``con``; ``scope`` (read here for ``team``, ``season_type``,
    ``since`` and ``until``);
    ``missing`` (the :class:`TemplateUnsupported` message when no team was
    named); ``span`` and ``season`` (the raw ``span``/``season`` slot values -
    passed rather than read, the same as ``scoped_player``'s own, so a caller
    with a reason to override them can).

    Returns ``(team, span)``, or the ``TemplateResult`` asking which team was
    meant. Honors ``since`` the same way :func:`scoped_player` does for a
    player - a career that starts partway through, rather than at the table's
    own floor (step 3, C4b) - and ``until`` beside it (step 3, K1): the
    inclusive last season of a range, read by :func:`_span_of`.

    .. versionadded:: 4.4.0

    .. versionchanged:: 5.0.0
       Reads the typed :class:`~association.query.reading.Scope`. A slot dict
       is still taken, through :meth:`~association.query.reading.Scope.from_slots`,
       until every caller passes ``reading.scope``.
    """
    seasons = _span_of(span, season, scope.season_type or 2, "games", since=scope.since, until=scope.until)
    if not scope.team or not scope.team.strip():
        raise TemplateUnsupported(missing)
    team = _resolved_team(con, scope.team, season=seasons.season)
    if isinstance(team, TemplateResult):
        return team
    return team, seasons


def team_span_clause(span: _Span) -> tuple[str, list[Any]]:
    """``tg.season``/``tg.eastern_date`` clause for a team's games in
    ``span``, over :data:`association.query.team_games.TEAM_GAMES_SQL`'s
    ``team_games``.

    A postseason is selected by the CALENDAR YEAR it was played in, from the
    relation's own Eastern date, never by ESPN's pre-1993-94 label
    (`AGENTS.md`, "Select a postseason by the calendar year"). Unlike
    ``association.query.templates.games._season_games``, this does not also
    exclude the phantom 1993 label by hand: ``team_games``' own ``played`` CTE
    already keeps one row per ``(season_type, home, away, eastern_date)``, so
    a 1993 row that is really 1994's game has already been collapsed into it
    before this clause ever runs, and asking for ``year(eastern_date) = 1994``
    cannot double it.

    Deliberately never excludes the NBA Cup final - see
    :func:`team_games`.

    .. versionchanged:: 4.4.0
       Bounds the upper end too when ``span.until`` is set (step 3, K1).

    .. versionadded:: 5.0.0
       Public, as the relation's shared step; ``_team_span_clause`` is this.
    """
    if span.season_type == 3:
        if span.season is not None:
            return "year(tg.eastern_date) = ?", [span.season]
        if span.until is not None:
            return "year(tg.eastern_date) BETWEEN ? AND ?", [span.first, span.until]
        return "year(tg.eastern_date) >= ?", [span.first]
    return span.clause("tg.season")


_team_span_clause = team_span_clause


def team_games(con: duckdb.DuckDBPyConnection, team: Entity, span: _Span, scope: Scope, *, opponent: Any, date: str | None = None) -> TeamNarrowed | TemplateResult:
    """``team``'s games in ``span``, narrowed to an opponent, a venue, one
    Eastern date, one game of each playoff series (``game_n``) and a window of
    the newest or oldest N (``order``/``limit``) where the question named
    them - the team counterpart of :func:`scoped_games`, over
    :class:`association.query.team_games.TeamNarrowed`.

    Every game the team actually played is included, the NBA Cup final among
    them: that exclusion is :func:`association.query.team_metrics.games_scope`'s,
    for a win-loss RECORD, and a plain game list or count is not one - the
    cup final is a real game the team played, the same reasoning
    ``team_record``'s own cup-final mention already carries.

    ``opponent`` is passed rather than read from ``scope`` because a caller
    that has already resolved the team (it needs the name for its answer
    before the games are read) passes the Entity, exactly as
    :func:`_narrow_player_games` does for a player's opponent; text is
    resolved here, so a clarification about the team comes back as the answer
    either way. ``venue``, ``game_n`` and ``order``/``limit`` are read from
    ``scope`` because no caller has a reason to resolve any of them first - a
    caller that must NOT honor one (``game_log``'s team half already lists its
    own games with its own LIMIT; ``head_to_head`` counts every meeting rather
    than a window of them) passes a scope without it, the same way both
    already do for every cell but ``venue``.

    .. versionchanged:: 4.4.0
       Honors ``game_n`` (step 3, C4b): one game of each playoff series,
       numbered the way :func:`_narrow_player_games` numbers a player's own.
       Honors ``order``/``limit`` (step 3, C4b) as a window - the newest or
       oldest N of the narrowed games, cut after every other filter - via
       :attr:`~association.query.team_games.TeamNarrowed.window`.

    .. versionchanged:: 4.4.0
       Honors ``situation`` (step 3, K1): a weekday, a month, a fixed holiday,
       or "since <month day>" within each game's own season - read the same
       way :func:`scoped_games` reads it for the player relation, over
       :meth:`association.query.team_games.TeamNarrowed.narrow_calendar`. A
       value that names no calendar narrowing (an age, "since returning") is
       refused by value rather than silently dropped, the same as the player
       relation's own refusal.

    .. versionchanged:: 4.4.0
       Honors the other half of ``situation`` (K3-2): an opponent's conference
       or division for that game's own season ("vs the west", "against the
       southeast division"), over
       :meth:`association.query.team_games.TeamNarrowed.narrow_alignment` -
       see :func:`_apply_situation`.

    .. versionchanged:: 5.0.0
       Reads the typed :class:`~association.query.reading.Scope`. A slot dict
       is still taken, through :meth:`~association.query.reading.Scope.from_slots`,
       until every caller passes ``reading.scope``.

    .. versionchanged:: 5.0.0
       Honors ``period``/``half``: every read then sees that part of each
       game (:meth:`~association.query.team_games.TeamNarrowed.narrow_periods`).
    """
    clause, params = _team_span_clause(span)
    narrowed = TeamNarrowed(base=["tg.team_id = ?", "tg.season_type = ?", clause], base_params=[team.id, span.season_type, *params], team=team)
    if opponent:
        rival = opponent if isinstance(opponent, Entity) else _resolved_team(con, opponent, season=span.season)
        if isinstance(rival, TemplateResult):
            return rival
        if rival.id == team.id:
            raise TemplateUnsupported("a team cannot be its own opponent")
        narrowed.opponent = rival
        narrowed.narrow("tg.opponent_id = ?", rival.id)
    if scope.venue:
        narrowed.venue = scope.venue
        narrowed.narrow("tg.side = ?", scope.venue)
    if date:
        narrowed.narrow("tg.eastern_date = ?", date)
        narrowed.date = date
    if scope.game_n:
        if span.season_type != 3:
            # A series has games 1-7; a regular season has nothing "game 4" names.
            raise TemplateUnsupported(f"game {scope.game_n} names a game of a playoff series, and this is a {span.kind} question")
        narrowed.narrow_series_game(scope.game_n)
    if scope.situation:
        # Same discipline as scoped_games: honored where it names the
        # calendar or a conference/division, refused BY VALUE (never
        # silently dropped) otherwise - see _apply_situation.
        _apply_situation(narrowed, scope.situation)
    # The same window rule as the player relation's - a named order, or a
    # bare limit read as the newest N (see relation_window).
    narrowed.window = relation_window(scope)
    _team_games_apply_period(con, narrowed, scope)
    return narrowed


def _team_games_apply_period(con: duckdb.DuckDBPyConnection, narrowed: TeamNarrowed, scope: Scope) -> None:
    """A quarter or half narrows every read of the team relation to that part
    of each game (:meth:`~association.query.team_games.TeamNarrowed.narrow_periods`)
    - the ``period``/``half`` cells of :data:`TEAM_RELATION_SCOPING`, the
    team counterpart of :func:`_apply_period`. A warehouse without the shots,
    the player rows or the plays - or with a shot or play table that carries
    no ``team_id`` (a fixture, a partial load) - leaves the columns rebuilt
    from them unknown rather than zero, and the linescore's points still
    answer."""
    asked = period_narrowing(scope)
    if asked is not None:
        shots = _has_table(con, "player_box_stats") and _team_games_has_team_id(con, "shot_chart")
        narrowed.narrow_periods(*asked, shots=shots, plays=shots and _team_games_has_team_id(con, "plays"))


def _team_games_has_team_id(con: duckdb.DuckDBPyConnection, table: str) -> bool:
    """Whether ``table`` exists and carries the ``team_id`` a team's period line groups by."""
    row = con.execute("SELECT COUNT(*) FROM information_schema.columns WHERE table_name = ? AND column_name = 'team_id'", [table]).fetchone()
    return bool(row and row[0])


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


def _condition_from_slot(con: duckdb.DuckDBPyConnection, entry: ConditionSpec, player: Entity, span: _Span, opponent: Entity | None = None) -> Condition | TemplateResult:
    """One ``conditions`` entry - a :class:`~association.query.reading.ConditionSpec`:
    a player, his side (``"own"`` or ``"opponent"``), a predicate, and the
    line a ``reached`` one names - as a
    :class:`~association.query.player_games.Condition` with its player
    resolved: a teammate the way "without" resolves one (narrowed to who
    shared a team with the subject), an opponent-side player against the
    box scores in the span. A predicate or stat this does not read refuses
    rather than narrowing to nothing.

    An absence the question wrote on the subject's own side ("without X")
    whose player was never his teammate in the span is read on the OTHER
    side where the games are narrowed to an ``opponent`` he played for then
    - "vs lakers without lebron" (ROADMAP step 3, Jeff's call: the side is
    settled where the name is resolved, since the parser does not read the
    warehouse) - bounded to his time on that team and said as "without X
    on the other side"; else the teammate refusal stands, naming both.

    .. versionadded:: 5.0.0

    .. versionchanged:: 5.0.0
       Takes ``opponent``, for an absence read on the other side.
    """
    if not entry.player.strip():
        raise TemplateUnsupported(f"a condition needs a player, got {entry!r}")
    side, predicate = entry.side, entry.predicate
    if side not in ("own", "opponent") or predicate not in CONDITION_PREDICATES:
        raise TemplateUnsupported(f"no condition reads side {side!r} with predicate {predicate!r}")
    if side == "own":
        found = _resolved_teammate(con, entry.player, player, span)
        if predicate == "absent" and isinstance(found, Entity):
            return _absence_condition(con, found, player, span, opponent)
    else:
        found = _resolved_player(con, entry.player, f"no player named {entry.player!r}", available=_BOX_SCORES, season=span.season, through=_career_end(span.season))
    if isinstance(found, TemplateResult):
        return found
    line: tuple[str, str, int, str] | None = None
    if predicate == "reached":
        stat, threshold = entry.stat, entry.threshold
        column = THRESHOLD_STAT_COLUMNS.get(stat) if stat is not None else None
        if column is None or stat is None or threshold is None or threshold < 1:
            raise TemplateUnsupported(f"a reached condition needs a known stat and a positive threshold, got {stat!r}/{threshold!r}")
        line = (column, ">=", threshold, f"{threshold}+ {STAT_LABELS.get(stat, stat)}s")
    tenure = _relation_tenure_clause(con, found, span.season) if side == "own" and predicate == "absent" else None
    return Condition(found, side, predicate, line, tenure)


def _absence_condition(con: duckdb.DuckDBPyConnection, mate: Entity, player: Entity, span: _Span, opponent: Entity | None) -> Condition:
    """ "Without X" as the relation reads it: the games X missed while on
    the subject's team (bounded to that tenure), or - where X was never his
    teammate in the span and the games are narrowed to an ``opponent`` X
    played for then - the games X missed on the OTHER side ("vs lakers
    without lebron", ROADMAP step 3), bounded to his time on that team and
    said as "without X on the other side". Settled here, where the name is
    resolved, since the parser reads no roster (Jeff's call, 2026-09-30).
    A man on neither side keeps the own-side reading, whose empty answer
    says he was never the subject's teammate.

    .. versionadded:: 5.0.0
    """
    if opponent is not None and not _teammates_among(con, [mate], player, span) and _on_team_in_span(con, mate, opponent, span):
        return Condition(mate, "opponent", "absent", None, _relation_tenure_clause(con, mate, span.season, side="opponent"))
    return Condition(mate, "own", "absent", None, _relation_tenure_clause(con, mate, span.season))


def _on_team_in_span(con: duckdb.DuckDBPyConnection, mate: Entity, team: Entity, span: _Span) -> bool:
    """Whether ``mate`` has a box score for ``team`` in a season of ``span``."""
    clause, params = span.clause("season")
    row = con.execute(f"SELECT 1 FROM player_box_stats WHERE athlete_id = ? AND team_id = ? AND season_type = ? AND {clause} LIMIT 1", [mate.id, team.id, span.season_type, *params]).fetchone()
    return row is not None


def _resolved_teammate(con: duckdb.DuckDBPyConnection, text: Any, player: Entity, span: _Span) -> Entity | TemplateResult:
    """The teammate a "without" names. "Without curry" is six players by name
    and at most two by roster, so an ambiguous name is narrowed to the ones who
    shared a team with ``player`` in the span before anything is asked.

    .. versionchanged:: 4.4.0
       A near spelling (:func:`~association.query.entities.suggest_players`)
       with exactly one candidate is taken rather than asked about, the same
       default :func:`~association.query.entities.resolve_player` already
       applies to a bare surname - visible in the answer
       (:func:`~association.query.entities.note_typo_reading`) and correctable
       (the note names the exact text that was typed). yardstick-v2 F157:
       "de'aaron fox vs magic last five games without wembyanama" used to
       refuse "did you mean Victor Wembanyama?" over a typo the question's own
       key note says resolves cleanly - the true reason the question falls
       short is a game count, not a name that failed to resolve.

    .. versionchanged:: 5.0.0
       The near spelling is read by :func:`~association.query.entities.resolve_player`
       itself (:func:`~association.query.entities.read_near_spelling`), as for
       every other name slot, and a surname back-off with one survivor ("Jemel
       Embiid") asks here as it does everywhere else rather than being taken.
    """
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
        # Nothing by that name and no single near spelling (resolve_player
        # already reads one): a suggestion, or a refusal.
        found = _resolved_player(con, text, available=_BOX_SCORES)
        if isinstance(found, TemplateResult):
            return found
        resolved = found
    if resolved.id == player.id:
        raise TemplateUnsupported(f"{player.name} cannot play without himself")
    return resolved


def _defaulted_season_note(season_range: tuple[int, int] | None, kind: str, *, career_hint: bool = True) -> str:
    """The sentence a defaulted-season refusal appends when
    :func:`~association.query.season_line.season_redirect` found something to point at - empty with nothing on record at all, which
    leaves the plain refusal standing: that is a genuine gap, not a wrong
    default, and there is nothing here to redirect toward.

    Never substitutes an answer, only names where to ask again - the same
    discipline `entities.suggest_players` follows for a near-miss name."""
    if season_range is None:
        return ""
    # At call time: compose imports this module (the sayer phrases each
    # decision kind once, compose.say.decision_phrase).
    from association.query.compose.say import decision_phrase
    from association.query.result import Decided

    first, last = season_range
    redirect = Decided(kind="season_redirected", field="season", chose=None, why="the season read by default holds nothing for him", facts={"first": first, "last": last, "what": kind})
    return decision_phrase(redirect, career_hint=career_hint)


def no_narrowed_games(con: duckdb.DuckDBPyConnection, player: Entity, span: _Span, narrowed: Narrowed, *, rebuilt: bool = False) -> str:
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
            # At call time: the relation imports this module.
            from association.query.season_line import season_redirect

            redirect = season_redirect(con, player.id, span.season_type, "player_game_log")
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


_no_narrowed_games = no_narrowed_games


def box_score_notes_read(con: duckdb.DuckDBPyConnection, player: Entity, span: _Span, narrowed: Narrowed, *, career_note: bool = True, rebuilt: bool = False, rebuilt_shown: int = 0) -> list[Note]:
    """What a box-score answer has to say about itself, read as kinds and
    facts (:class:`~association.query.notes.Note`): what "without" was
    taken to mean, the figures that were rebuilt rather than fetched, the
    empty lines left out, and - unless ``career_note`` is off, as it is for
    one dated game - a career older than the box scores. The sayer phrases
    each (``compose.say.note_phrase``); :func:`_box_score_notes` is that,
    for the templates that still write sentences.

    .. versionadded:: 5.0.0
    """
    notes: list[Note] = []
    if narrowed.without:
        notes.append(Note("definition", {"term": "without", "names": [mate.name for mate in narrowed.without]}))
    if rebuilt_shown:
        # Said outright, because these numbers did not come from ESPN. Per game
        # they are close (see REBUILT_STATS) but they are not the box score, and
        # a reader quoting one should know which kind of number they hold.
        notes.append(Note("lines_rebuilt", {"games": rebuilt_shown, "what": "shown"}))
    where, params = narrowed.clauses(recorded=False, rebuilt=rebuilt)
    row = con.execute(f"SELECT COUNT(*) {_PLAYER_GAMES} WHERE {where}", params).fetchone()
    empty = row[0] if row else 0
    if empty:
        notes.append(Note("games_unseen", {"games": empty, "why": "empty_box_score"}))
    if span.career and career_note and span.since is None:
        row = con.execute(
            "SELECT MIN(season) FROM player_season_stats_deduped WHERE athlete_id = ? AND season_type = ? AND gamesPlayed > 0",
            [player.id, span.season_type],
        ).fetchone()
        earliest = row[0] if row else None
        if earliest is not None and earliest < span.first:
            notes.append(Note("floor", {"table": "box_scores", "first": span.first, "earliest": earliest}))
    return notes


def box_score_notes(con: duckdb.DuckDBPyConnection, player: Entity, span: _Span, narrowed: Narrowed, *, career_note: bool = True, rebuilt: bool = False, rebuilt_shown: int = 0) -> list[str]:
    """:func:`box_score_notes_read`, each note phrased and recorded - the
    sentences the templates append.

    .. versionchanged:: 5.0.0
       Reads through :func:`box_score_notes_read` and phrases each kind
       once, in the sayer (``compose.say.note_phrase``).
    """
    from association.query.compose.say import note_phrase

    return [note(each.kind, note_phrase(each), **each.facts) for each in box_score_notes_read(con, player, span, narrowed, career_note=career_note, rebuilt=rebuilt, rebuilt_shown=rebuilt_shown)]


_box_score_notes = box_score_notes


# The private name the templates import; one definition, `ordinal_word` -
# the two were the same function written twice in this module.
_ordinal = ordinal_word


def condition_scope(season: int | None, span: Literal["career"] | None, season_type: int | None, tables: tuple[str, ...], since: int | None = None) -> _Scope:
    """The games a question covers. No season means the current one - except
    for a career, where it means every season on record, which is what the
    word asked for. A season the question named beats "career": the router keeps
    a named year alongside it, and "career ... in 2015" is asking about 2015.
    ``since`` is every season from that one on - and, like ``_span_of``'s own
    pairing of the two, conflicts with a named ``season`` rather than silently
    picking one: a caller that let both through here would resolve "since 2022
    and 2020 at once" as though only "since 2022" had been asked, with nothing
    saying the named year was dropped.

    .. versionchanged:: 4.3.0
       Honors ``since``.

    .. versionchanged:: 4.4.0
       Refuses ``since`` alongside a named ``season`` instead of silently
       preferring ``since``.
    """
    kind = season_type or 2
    if since:
        if season:
            raise TemplateUnsupported(f"since {since} and the {season} season at once")
        scope = _game_scope(None, kind, tables)
        return _Scope(None, kind, max(since, scope.first), scope.phantoms)
    if season is not None:
        return _game_scope(season, kind, tables)
    return _game_scope(None if span == "career" else current_season(), kind, tables)


_condition_scope = condition_scope


def where_in(scope: _Scope) -> str:
    """ "in the 2026 regular season", or "in any regular season on record" for a span with nothing in it."""
    return f"in the {scope.label()}" if scope.season is not None else f"in any {scope.kind} on record ({scope.first} onward)"


_where_in = where_in


def optional_team(con: duckdb.DuckDBPyConnection, text: Any, season: int | None = None) -> Entity | TemplateResult | None:
    """A team slot that may be empty: ``None`` for no text, else
    :func:`resolved_team`'s entity or its refusal.

    .. versionadded:: 5.0.0
       Public, as the relation's shared step; ``_optional_team`` is this.
    """
    if not isinstance(text, str) or not text.strip():
        return None
    return resolved_team(con, text, season=season)


_optional_team = optional_team


def no_games(con: duckdb.DuckDBPyConnection, player: Entity, scope: _Scope, team: Entity | None) -> TemplateResult:
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


_no_games = no_games


#: The relation's clause builder under the name the templates and tests knew it by.
_Narrowed = Narrowed
