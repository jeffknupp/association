"""Deterministic query templates: the second half of the router/template split
described in router.py.

Given an intent and its slots, these build and run the SQL themselves, so every
correctness rule lives in code rather than as prose the model re-derives per
query. Only intents present in TEMPLATES are handled; anything else - including
a recognized intent whose slots don't validate - falls through to the agent
untouched."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb

from association.coverage import COVERAGE, POSTSEASON, caveat, unavailable
from association.net_points_categories import FINGERPRINT_CATEGORIES
from association.season import current_season
from association.season import eastern_date as _eastern_date

from .answer import Artifact
from .conditions import (
    _PLAYER_GAME_TABLES,
    _PLAYER_LINE,
    _SPLIT_TITLES,
    _TEAM_GAME_TABLES,
    _TEAM_LINE,
    _box_missing,
    _cell,
    _game_scope,
    _longest_runs,
    _margin,
    _matchup_line,
    _meetings,
    _names,
    _overlaps,
    _player_games,
    _player_streak_rows,
    _Scope,
    _split_cells,
    _split_label,
    _split_rows,
    _stints,
    _table,
    _team_games,
    _totals,
    _unseen,
    _unseen_meetings,
    _unseen_note,
    _win_pct,
    _with_without_games,
    _with_without_group,
)
from .court import HAS_POSITION_SQL, SHOT_DISTANCE_SQL
from .entities import Ambiguous, Availability, Entity, clarification, find_players, no_match, resolve_player, resolve_team, suggest_players, suggestion
from .fingerprint import FINGERPRINT_AVAILABILITY, FINGERPRINT_VIEWS, GAME_FINGERPRINT_AVAILABILITY, FingerprintUnavailable, render_for_players
from .leaderboard import SEASON_TOTAL_OF, LeaderboardError, not_a_postseason_copy, resolve_metric, run_career_leaderboard, run_leaderboard
from .metrics import EXTRA_FIELD_COLUMNS, LEADERBOARD_METRICS, SEASON_TYPE_LABELS
from .shotchart import DERIVED_SHOT_VALUES, SHOT_AVAILABILITY, SHOT_VALUE_SQL, UNSEPARABLE_SHOT_VALUES, render_for_player, resolve_chart_player
from .team_metrics import (
    DEFAULT_TEAM_LINE,
    FIRST_FULL_REGULAR_SEASON,
    TEAM_GAMES_SQL,
    TEAM_METRICS,
    TeamLine,
    TeamMetric,
    TeamRecord,
    descending_for,
    games_scope,
    ranked,
    record_table,
    resolve_team_metric,
    season_table,
)

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

# Beyond three polygons on one radar the shapes stop being separable - and the
# palette in radar.py holds three series colors for the same reason.
MAX_FINGERPRINT_PLAYERS = 3

DEFAULT_LIMIT = 5
DEFAULT_LEADERBOARD_LIMIT = 10
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
# honoured by no template at all: nothing in the warehouse records one. `split`
# and `since` (a range of seasons) are read for every intent for the same reason:
# a template that is not about splits or ranges answered them with one season.
# `below` ("under 14 FTA") and `situation` (back-to-backs, overtime, a month, a
# conference) are refused by every template: nothing narrows to either yet, and
# answering without them answered the inverse or the whole season.
SCOPING_SLOTS = frozenset({"order", "date", "opponent", "venue", "span", "without", "round", "split", "since", "below", "situation"})

# What each template actually honours. Anything not listed here honours none.
HONORED_SCOPING: dict[str, frozenset[str]] = {
    # Every one of them, for a player: opponent, venue and a teammate's absence
    # are filters on the box-score rows, and a career is every season of them.
    # A team's log refuses `without` itself - that is with_without's question.
    "game_log": frozenset({"order", "date", "opponent", "venue", "span", "without"}),
    # The three that narrow games are answered from box scores rather than the
    # season line; a career is summed from the season table.
    "player_stat": frozenset({"opponent", "venue", "span", "without"}),
    "player_history": frozenset({"span"}),
    # It always read `opponent`; listed now that `opponent` is a scoping slot.
    "team_quarter_points": frozenset({"opponent"}),
    # The opponent IS the second team of a head-to-head.
    "head_to_head": frozenset({"opponent"}),
    "shot_chart": frozenset({"order"}),
    "shot_distance": frozenset({"order"}),
    "player_netpoints": frozenset({"order"}),
    # `order` is honoured by DRAWING that game, from the long per-game table.
    # `date` is still honoured by refusing: the router gives a calendar date
    # and the loader picks a player's first or last game of a season, which are
    # different questions - answering one with the other is the substitution
    # this whole module exists to prevent. Both stay listed either way, since
    # leaving one unlisted falls through to an agent with no better source,
    # which is slower and free to answer the season instead.
    "fingerprint": frozenset({"order", "date"}),
    # `span` "career" is honoured by summing every season: a career leaderboard
    # from the per-team season rows, and a career count or high from every box
    # score since 1993-94. Each answer names the pool, since neither is all-time.
    "leaderboard": frozenset({"span"}),
    "threshold_count": frozenset({"span"}),
    "single_game_high": frozenset({"span"}),
    # A career is every season on record rather than the current one; see
    # _condition_scope. `without` is the teammate with_without divides by, and
    # `split` is the one player_splits was asked for.
    "player_splits": frozenset({"span", "split"}),
    "with_without": frozenset({"span", "without"}),
    "record_when": frozenset({"span"}),
    "player_matchup": frozenset({"span"}),
    "streak": frozenset({"span"}),
    # The home/road split, the record against one team, and every season at
    # once - "Knicks home record" was answered with their overall 53-29.
    "team_record": frozenset({"venue", "opponent", "span"}),
    # Honoured for the record metrics, from the standings' own home/road
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
    "threshold_count": ("player_box_stats", "player_season_stats"),
    "single_game_high": ("player_game_log", "player_box_stats", "player_season_stats"),
    # The season line by default and box scores once the question narrows the
    # games, so _sources_for picks per question: a 1990 season line is
    # answerable, and a 1990 line against one opponent is not.
    "player_stat": ("player_season_stats_deduped", "player_game_log", "player_box_stats", "games"),
    "player_compare": ("player_season_stats_deduped",),
    "player_history": ("player_season_stats_deduped",),
    "player_netpoints": ("net_points_player", "net_points_player_fingerprint"),
    "team_record": ("standings",),
    "head_to_head": ("games",),
    "team_quarter_points": ("team_box_stats", "games"),
    # A player's log and a team's come from different tables, and _sources_for
    # picks between them - a team question refused with "Player game logs only
    # go back to..." names the wrong thing.
    "game_log": ("games", "team_box_stats", "player_game_log", "player_box_stats", "player_season_stats_deduped"),
    "shot_chart": ("shot_chart",),
    "shot_distance": ("shot_chart",),
    "fingerprint": ("net_points_player_fingerprint", "net_points_player_game_fingerprint"),
    # A team's splits and streaks read only the team tables; _sources_for
    # picks between the two per question.
    "player_splits": _PLAYER_GAME_TABLES,
    "with_without": _PLAYER_GAME_TABLES,
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


PLAYER_REQUIRED_INTENTS: frozenset[str] = frozenset({"record_when"})
"""Intents whose template cannot answer at all without a player, so a player the
router left out is worth restoring from the question.

Deliberately not every intent that reads one: where the player is optional -
``threshold_count``, ``single_game_high`` - an empty slot means "the league", and
filling it would turn a league question into a question about somebody the
question may only appear to name ("best" is Travis Best).

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


def _sources_for(intent: str, slots: dict[str, Any]) -> tuple[str, ...]:
    """The tables an answer would be built from, resolved per question because
    a leaderboard's depends on which metric was asked for."""
    if intent == "game_log":
        named_player = isinstance(slots.get("player"), str) and slots["player"].strip()
        return _PLAYER_BOX_SOURCES if named_player else ("games", "team_box_stats")
    if intent == "player_stat":
        return _PLAYER_BOX_SOURCES if any(slots.get(s) for s in _BOX_SCORE_SCOPING) else ("player_season_stats_deduped",)
    if intent in ("player_splits", "streak"):
        # A team's splits or streak never touch a player box score, and
        # charging them that table's floor would refuse a 1990 playoff question
        # with a sentence about player box scores - the wrong cause.
        named_player = isinstance(slots.get("player"), str) and slots["player"].strip()
        by_player = named_player or (intent == "streak" and isinstance(slots.get("threshold"), int))
        return _PLAYER_GAME_TABLES if by_player else _TEAM_GAME_TABLES
    if intent == "team_record":
        # A season's record, and its home/road split, are the standings'; a
        # record against one team, or in a postseason, can only be tallied
        # from `games`, whose regular seasons start later.
        against = bool(slots.get("opponent")) or (isinstance(slots.get("teams"), list) and len(slots["teams"]) > 1)
        return ("games",) if against or slots.get("season_type") == 3 else ("standings",)
    if intent == "team_leaderboard":
        key = resolve_team_metric(slots.get("stat"))
        if key is not None and TEAM_METRICS[key].expression is None:
            return ("games",) if slots.get("season_type") == 3 else ("standings",)
        return TEMPLATE_SOURCES[intent]
    if intent != "leaderboard":
        return TEMPLATE_SOURCES.get(intent, ())
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
    return caveat(_sources_for(intent, slots), season) if isinstance(season, int) else None


def check_scope(intent: str, slots: dict[str, Any]) -> None:
    """Raise if the question scoped to particular games and this template
    cannot honour that. Falling through is slow; answering a different question
    quickly is worse."""
    ignored = sorted(s for s in SCOPING_SLOTS if slots.get(s) and s not in HONORED_SCOPING.get(intent, frozenset()))
    if ignored:
        raise TemplateUnsupported(f"{intent} cannot honour {ignored} - it would answer for a different span than was asked")


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


# ---- resolving a name to an entity, shared by every template below ----


def _clarify(text: str, candidates: list[str], kind: str = "player", active: int = 0) -> TemplateResult:
    """A handled outcome, not a fall-through: the template knows exactly what
    is ambiguous, so it says so instead of passing the problem along.

    The sentence itself is entities.clarification, because the chart entry
    points reach the same ambiguity without going through a template and have
    to phrase it identically."""
    return TemplateResult(data={"ambiguous": text, "candidates": candidates}, answer=clarification(text, candidates, kind, active))


# Where each player template's answer is read from, for narrowing an ambiguous
# name to the candidates with a row there. The tables TEMPLATE_SOURCES
# declares, or the per-game table a one-game answer reads instead - so a
# candidate these eliminate is one whose answer would have been empty.
_SEASON_LINES = Availability("player_season_stats_deduped")
_GAME_LOGS = Availability("player_game_log")
# player_netpoints reads the season totals AND the fingerprint, and the two
# disagree about who they hold: measured, 63 player-seasons are in the first
# only and 8 in the second only. A row in either is an answer.
_NET_POINTS = (Availability("net_points_player"), FINGERPRINT_AVAILABILITY)
_NET_POINTS_GAMES = Availability("net_points_player_game")
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


def _resolved_team(con: duckdb.DuckDBPyConnection, text: Any) -> Entity | TemplateResult:
    if not isinstance(text, str) or not text.strip():
        raise TemplateUnsupported("no team named")
    match resolve_team(con, text):
        case Entity() as team:
            return team
        case Ambiguous(candidates=candidates):
            return _clarify(text, candidates, kind="team")
        case _:
            raise TemplateUnsupported(f"no team matching {text!r}")


# ---- one season of box scores, or a career of them ----


def _career_span(intent: str, span: Any, season: Any) -> bool:
    """True for a career question, False for a one-season one; raises for a
    span this template cannot honour.

    A career with a year named is refused rather than read. The router keeps a
    year the question named alongside "career", so "most points ever in a game
    in 2024" (that season), "career leaders since 2015" (a range) and "career
    points through 2010" (a cutoff) all arrive as the same two slots. Answering
    any of them as one of the others is the substitution this module exists to
    prevent."""
    if not span:
        return False
    if span != "career":
        raise TemplateUnsupported(f"{intent} cannot honour span {span!r}")
    if isinstance(season, int):
        raise TemplateUnsupported(f"{intent} cannot tell whether a career span with {season} named means that season, since it, or through it")
    return True


def _season_label(season: int) -> str:
    """1994 -> "1993-94", the way a person names a season."""
    return f"{season - 1}-{season % 100:02d}"


def _box_scope(alias: str, season: int | None, season_type: int) -> tuple[str, list[Any]]:
    """The WHERE clause for one season of box scores, or for a career of them.

    A career starts at the box scores' floor, and that floor is also what keeps
    1993 out: ESPN answers season=1993 with the same games as 1994 (coverage's
    phantom), so a career counted from 1993 counts every 1993-94 game twice -
    26,350 duplicate player-games."""
    if season is None:
        return f"{alias}.season >= ? AND {alias}.season_type = ?", [COVERAGE["player_box_stats"].first_season, season_type]
    return f"{alias}.season = ? AND {alias}.season_type = ?", [season, season_type]


@dataclass(frozen=True)
class _GameSpan:
    """How an answer built from box scores names the games it covers."""

    when: str  # "in the 2026 regular season" - follows a verb
    caption: str  # a noun phrase, for question_shape
    games: str  # "2026 regular season games" - for "no ... in the warehouse"
    since: str  # the box scores' first season, "1993-94"
    preface: str = ""  # said FIRST, when the span is narrower than the question
    league_note: bool = False  # a league-wide career, which is not all-time


def _game_span(con: duckdb.DuckDBPyConnection, season: int | None, season_type: int, player: Entity | None) -> _GameSpan:
    """Name what a box-score answer covers - and, for a named player's career,
    whether the box scores hold it at all.

    Michael Jordan's career began in 1984-85, and the box scores here begin in
    1993-94. His "career high" from them is 55, not 69: fluent, real, and an
    answer to a different question. So a career that began before the box
    scores is answered for the part they hold, and says so before the number
    rather than after it - the reader who stops at the number has been told."""
    kind = SEASON_TYPE_NAMES.get(season_type, "regular season")
    floor = COVERAGE["player_box_stats"].first_season
    since = _season_label(floor)
    if season is not None:
        period = _period(season, season_type)
        return _GameSpan(when=f"in the {period}", caption=period, games=f"{period} games", since=since)
    if player is None:
        return _GameSpan(when=f"in the {kind} since {since}", caption=f"{kind} since {since}", games=f"{kind} games since {since}", since=since, league_note=True)
    began, ended = _seasons_on_record(con, player.id, season_type)
    if isinstance(began, int) and began < floor:
        preface = f"Box scores here begin in {since}, and {player.name}'s {kind} career began in {_season_label(began)}, so his whole career is not in them. "
        return _GameSpan(when=f"in the {kind} since {since}", caption=f"{kind} since {since}", games=f"{kind} games since {since}", since=since, preface=preface)
    years = f" ({_season_label(began)} through {_season_label(ended)})" if isinstance(began, int) and isinstance(ended, int) else ""
    return _GameSpan(when=f"in his {kind} career{years}", caption=f"{kind} career{years}", games=f"{kind} games", since=since)


def _seasons_on_record(con: duckdb.DuckDBPyConnection, athlete_id: str, season_type: int) -> tuple[Any, Any]:
    """A player's first and last season in the per-player season table, which
    reaches back to 1976-77 - before any box score here. A postseason copied
    from the regular season is not a postseason on record."""
    copy = f" AND {not_a_postseason_copy(('points',))}" if season_type == POSTSEASON else ""
    row = con.execute(f"SELECT MIN(t.season), MAX(t.season) FROM player_season_stats t WHERE t.athlete_id = ? AND t.season_type = ?{copy}", [athlete_id, season_type]).fetchone()
    return (row[0], row[1]) if row else (None, None)


def _empty_box_scores(con: duckdb.DuckDBPyConnection, season: int | None, season_type: int, athlete_id: str | None) -> tuple[int, int | None, int | None]:
    """Games in scope whose box score is empty: (count, first season, last season).

    Every game from 2012-13 through 2017-18 has a box score, but 161-166 a
    season hold nothing - every player's minutes NULL and every stat 0. LeBron
    James's 76 games of 2012-13 are all present and sum to 1,835 points, against
    the season table's 2,036. A zero can hide a real maximum or a real count but
    never invent one, so the answer stands - and says how many games it could
    not see. For a player, only the empty games he actually played in count."""
    scope, params = _box_scope("b", season, season_type)
    empty = f"SELECT b.event_id, b.season FROM player_box_stats b WHERE {scope} GROUP BY b.event_id, b.season HAVING MAX(b.minutes) IS NULL"
    if athlete_id is None:
        sql = f"SELECT COUNT(*), MIN(season), MAX(season) FROM ({empty})"
    else:
        sql = (
            f"SELECT COUNT(*), MIN(r.season), MAX(r.season) FROM player_box_stats r JOIN ({empty}) e ON e.event_id = r.event_id AND e.season = r.season "
            "WHERE r.athlete_id = ? AND NOT COALESCE(r.did_not_play, FALSE)"
        )
        params.append(athlete_id)
    row = con.execute(sql, params).fetchone()
    return (int(row[0]), row[1], row[2]) if row else (0, None, None)


def _empty_note(found: tuple[int, int | None, int | None], name: str | None, consequence: str) -> str:
    count, first, last = found
    if not count or first is None or last is None:
        return ""
    whose = f"{count:,} of {name}'s games" if name else f"{count:,} {'game' if count == 1 else 'games'}"
    between = f"in {_season_label(first)}" if first == last else f"between {_season_label(first)} and {_season_label(last)}"
    return f" {whose} {between} {'has' if count == 1 else 'have'} an empty box score in this warehouse, so {consequence}."


# games.date is UTC, and a 7pm Eastern tip is already the next day there:
# LeBron James's 61 against Charlotte, on 3 March 2014, is stored as
# 2014-03-04T00:30Z. A fixed five-hour shift, as fetch/parse.py's
# NetPointsGameIndex uses and for its reason - EST and EDT disagree about a
# tip's date only between midnight and 1am Eastern, when no game starts.
_EASTERN_SHIFT = timedelta(hours=5)


def threshold_count(ctx: TemplateContext, slots: dict[str, Any]) -> TemplateResult:
    """ "Most games with N+ of some stat" - the shape that motivated this split.

    A KNOWLEDGE_BASE entry covered it, but sat in the truncated-away head of the
    prompt, so three consecutive runs answered with a season-averages
    leaderboard instead. In code it cannot be truncated or substituted.

    .. versionchanged:: 2.1.0
       Honours ``span`` "career": every box score since 1993-94, for the league
       or for one player, saying which. A named player is resolved to one
       person; every player whose name contained the words used to be counted,
       and the top one reported. An ambiguous name is narrowed to the players
       with a box score in the season asked about before it is asked about.
    """
    con = ctx.con
    stat = slots.get("stat")
    column = THRESHOLD_STAT_COLUMNS.get(stat) if isinstance(stat, str) else None
    threshold = slots.get("threshold")
    if column is None or not isinstance(threshold, int):
        raise TemplateUnsupported(f"threshold_count needs a known stat and an integer threshold, got {stat!r}/{threshold!r}")
    if threshold < 1:
        # ">= 0" counts every game, which is never the question: measured, "most 3
        # pointers made since 2020" arrived as threshold 0 and was answered as
        # "the most games with 0+ 3-pointers".
        raise TemplateUnsupported(f"a threshold of {threshold} counts every game - not a question threshold_count answers")

    career = _career_span("threshold_count", slots.get("span"), slots.get("season"))
    season = None if career else (slots.get("season") or current_season())
    season_type = slots.get("season_type") or 2
    limit = _clamp_limit(slots.get("limit"))

    # Resolved to one person, as every other template does. This used to be an
    # ILIKE per word, so "Curry" counted Seth's games and Stephen's and reported
    # whichever had more - the prominence tiebreak AGENTS.md records as measured
    # and rejected, applied silently. Narrowed by who has a box score in the
    # season, NOT by who has a qualifying game: that would let the answer pick
    # the player, which is the same tiebreak by another route.
    player: Entity | None = None
    if isinstance(slots.get("player"), str) and slots["player"].strip():
        resolved = _resolved_player(con, slots["player"], available=_BOX_SCORES, season=season, through=_career_end(season))
        if isinstance(resolved, TemplateResult):
            return resolved
        player = resolved

    scope, params = _box_scope("pbs", season, season_type)
    where = [scope, f"pbs.{column} >= ?"]
    params.append(threshold)
    if player is not None:
        where.append("pbs.athlete_id = ?")
        params.append(player.id)
    params.append(limit)
    # Grouped by athlete_id, not by name: two players can share one.
    rows = con.execute(
        f"SELECT p.display_name, COUNT(*) AS games FROM player_box_stats pbs JOIN players p ON p.athlete_id = pbs.athlete_id "
        f"WHERE {' AND '.join(where)} GROUP BY pbs.athlete_id, p.display_name ORDER BY 2 DESC, 1 LIMIT ?",
        params,
    ).fetchall()

    label = STAT_LABELS.get(stat or "", stat or "")
    scope_text = f"{threshold}+ {label}s"
    span = _game_span(con, season, season_type, player)
    empty = _empty_box_scores(con, season, season_type, player.id if player else None)
    answer = span.preface + _phrase_threshold_count(rows, scope_text, span.when, player.name if player else None)
    if span.league_note:
        answer += f" Box scores begin in {span.since}, so these are not all-time counts: a career that began earlier is counted only from {span.since}."
    answer += _empty_note(empty, player.name if player else None, "the count may be low" if player else "these counts may be low")
    leaders = [{"player": name, "games": games} for name, games in rows]
    return TemplateResult(
        data={
            "question_shape": f"games with {scope_text}, {span.caption}",
            "season": season,
            "span": "career" if career else None,
            "leaders": leaders,
            "empty_box_scores": empty[0],
        },
        answer=answer,
    )


def _period(season: int, season_type: int) -> str:
    return f"{season} {SEASON_TYPE_NAMES.get(season_type, 'regular season')}"


def _phrase_threshold_count(rows: list[tuple[Any, ...]], scope: str, when: str, player: str | None) -> str:
    """Always names the season outright rather than echoing "this season" back.
    The original failure answered for 2024 while the user meant the current
    season, and said nothing about it - so the season is stated, every time.
    ``when`` is that statement: one season, or a career and where it starts."""
    label = f"games with {scope}"
    if player is not None:
        games = rows[0][1] if rows else 0
        return f"{player} had {games} {label} {when}." if games else f"{player} had no {label} {when}."
    if not rows:
        return f"No player had a game with {scope} {when}."

    top = rows[0][1]
    tied = [name for name, games in rows if games == top]
    if len(tied) > 1:
        leaders = ", ".join(tied[:-1]) + f" and {tied[-1]}"
        sentence = f"{leaders} tied for the most {label} {when}, with {top} each."
    else:
        sentence = f"{rows[0][0]} had the most {label} {when}, with {top}."
    rest = [f"{name} ({games})" for name, games in rows if games != top]
    return sentence + (f" Next: {', '.join(rest)}." if rest else "")


def _table_cell(value: Any) -> str:
    """A value in an aligned column: a fixed decimal, never trailing-zero
    stripped - "25" next to "27.7" reads as a different unit."""
    if value is None:
        return "-"
    return f"{value:.1f}" if isinstance(value, float) else str(value)


def _signed_cell(value: Any) -> str:
    """A NetPoints cell. Signed, because the sign is the whole reading of it -
    an unmarked "0.42" beside "-1.10" loses which one helped their team."""
    return "-" if value is None else f"{value:+.2f}"


def _format_value(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.3f}".rstrip("0").rstrip(".") if abs(value) < 1 else f"{value:.2f}".rstrip("0").rstrip(".")
    return str(value)


def leaderboard(ctx: TemplateContext, slots: dict[str, Any]) -> TemplateResult:
    """ "Top N players by X" for the metrics in LEADERBOARD_METRICS.

    Thin on purpose: run_leaderboard owns the season default, minimum-sample
    floor and traded-player dedup, and the agent's get_leaderboard tool calls
    the same function. This adds slot mapping and phrasing.

    .. versionchanged:: 2.1.0
       Honours ``span`` "career", ranking whole careers (see
       :func:`~association.query.leaderboard.run_career_leaderboard`) and
       saying whose. ``rate`` "total" ranks a season total rather than a
       per-game average. Every stat name the router is taught now maps to a
       metric, and a qualifier, when one applies, is named in the answer.
    """
    con = ctx.con
    career = _career_span("leaderboard", slots.get("span"), slots.get("season"))
    metric = resolve_metric(slots.get("stat"), career=career)
    if metric is None:
        raise TemplateUnsupported(f"no leaderboard metric for stat {slots.get('stat')!r}")
    if slots.get("rate") == "total":
        # `stat` names a category, never which of its two readings; "most
        # points this season" is a total and "leads in points" a per-game rate.
        metric = SEASON_TOTAL_OF.get(metric, metric)
    if isinstance(slots.get("player"), str) and slots["player"].strip():
        # A leaderboard ranks the league or a team, never one named person.
        # Confirmed live: "Klay Thompson's 3pt percentage over the past 4
        # seasons" landed here and came back with the league's true-shooting
        # leaders, Klay silently dropped.
        raise TemplateUnsupported(f"a leaderboard cannot answer about one named player ({slots['player']!r})")
    # "top 10 in NetPoints ALONGSIDE their points per game" used to be answered
    # without the second half and without saying so - a silent partial answer,
    # the failure this whole architecture exists to prevent. An unknown field
    # falls through rather than being dropped.
    requested = [f for f in slots.get("fields") or [] if isinstance(f, str)]
    unknown = [f for f in requested if f not in EXTRA_FIELD_COLUMNS]
    if unknown:
        raise TemplateUnsupported(f"unknown leaderboard field(s) {unknown}")
    # Deduplicated, order preserved: the router repeats itself sometimes
    # (["points","minutes","minutes"]), which is a slip, not a reason to spend
    # minutes in the agent. A field restating the ranked metric goes too - it
    # rendered the same 33.5 twice under two headings.
    fields = [f for f in dict.fromkeys(requested) if metric != f"avg_{f}"]
    if career:
        return _career_leaderboard(con, metric, slots, fields)
    try:
        result = run_leaderboard(
            con,
            metric,
            season=slots.get("season"),
            season_type=slots.get("season_type") or 2,
            team=slots.get("team") if isinstance(slots.get("team"), str) else None,
            fields=fields or None,
            limit=_clamp_limit(slots.get("limit"), default=DEFAULT_LEADERBOARD_LIMIT),
        )
    except LeaderboardError as exc:
        # An ambiguous team, an unknown metric, or a table that needs a
        # warehouse flag - all reasons to fall through, never to guess.
        raise TemplateUnsupported(str(exc)) from exc

    period = _period(result.season, result.season_type or 2)
    where = f"the {result.team_name}" if result.team_name else "the league"
    summary = f"{result.label}, {period}"
    ratio = LEADERBOARD_METRICS[metric].ratio
    answer = (
        _tabulate_leaderboard(result.rows, result.label, where, period, fields, result.min_sample_applied, result.min_sample_column, ratio)
        if fields
        else _phrase_leaderboard(result.rows, result.label, where, period, _qualifier(result.min_sample_applied, result.min_sample_column), ratio)
    )
    return TemplateResult(
        data={"question_shape": summary, "season": result.season, "fields": fields, "min_sample": result.min_sample_applied, "leaders": result.rows},
        answer=answer,
    )


def _career_leaderboard(con: duckdb.DuckDBPyConnection, metric: str, slots: dict[str, Any], fields: list[str]) -> TemplateResult:
    """A career ranking, on its own path because its pool is its own - see
    :func:`~association.query.leaderboard.run_career_leaderboard`."""
    if fields:
        raise TemplateUnsupported("a career leaderboard cannot add per-game columns")
    if isinstance(slots.get("team"), str) and slots["team"].strip():
        # A franchise's career list sums the per-team rows by team, and where a
        # franchise moved, which years are the franchise's is a question of its
        # own. Refused until that is decided, rather than answered with the
        # league's list under the team's name.
        raise TemplateUnsupported("franchise career leaderboards are not supported")
    season_type = slots.get("season_type") or 2
    try:
        result = run_career_leaderboard(con, metric, season_type=season_type, limit=_clamp_limit(slots.get("limit"), default=DEFAULT_LEADERBOARD_LIMIT))
    except LeaderboardError as exc:
        raise TemplateUnsupported(str(exc)) from exc
    kind = SEASON_TYPE_NAMES.get(season_type, "regular season")
    label = f"career {result.label.removeprefix('total ')}"
    since = _season_label(result.pool_first_season)
    qualifier = _qualifier(result.min_sample_applied, result.min_sample_column)
    return TemplateResult(
        data={
            "question_shape": f"{label}, {kind}, players active since {since}",
            "season": None,
            "span": "career",
            "pool_first_season": result.pool_first_season,
            "fields": [],
            "min_sample": result.min_sample_applied,
            "leaders": result.rows,
        },
        answer=_phrase_career_leaderboard(result.rows, label, kind, since, qualifier, LEADERBOARD_METRICS[metric].ratio),
    )


def _phrase_career_leaderboard(rows: list[dict[str, Any]], label: str, kind: str, since: str, qualifier: str, ratio: tuple[str, str] | None) -> str:
    """Says whose careers, every time. The pool is every player active in
    1993-94 or later, counted over his whole career, and nobody whose career
    ended before it - Kareem Abdul-Jabbar is not in the warehouse at all - so
    presenting it as "all-time" would be the unrepresentative ranking
    coverage.py's second floor exists to refuse."""
    gap = f"Careers that ended before {since} are not in this warehouse, so this is not an all-time list."
    if not rows:
        return f"No player qualified for {label} in the {kind}{qualifier}. {gap}"
    top = rows[0]
    years = f"{_season_label(top['first_season'])} through {_season_label(top['last_season'])}"
    detail = f", over {int(top['games']):,} games ({years})" if top.get("games") else ""
    sentence = f"Among players active in {since} or later, {top['display_name']} leads in {label} in the {kind}{qualifier}: {_leader_value(top, ratio)}{detail}."
    rest = [f"{r['display_name']} ({_leader_value(r, ratio, short=True)})" for r in rows[1:]]
    return " ".join([sentence, *([f"Next: {', '.join(rest)}."] if rest else []), gap])


def _leader_value(row: dict[str, Any], ratio: tuple[str, str] | None, *, short: bool = False) -> str:
    """A ranked value as a reader expects it: a percentage as one, with the
    makes and attempts behind it - the "out of how many?" a bare percentage
    always draws - and a count with its thousands separated."""
    value = row.get("value")
    if value is None:
        return "-"
    if ratio:
        made, attempted = row.get(ratio[0]), row.get(ratio[1])
        text = f"{value * 100:.1f}%"
        return text if short or made is None or attempted is None else f"{text} ({int(made):,} of {int(attempted):,})"
    if isinstance(value, int):
        return f"{value:,}"
    # ESPN's averages carry one decimal, and "4" beside "3.8" reads as a count.
    return f"{value:.1f}" if isinstance(value, float) and value == round(value, 1) and abs(value) >= 1 else _format_value(value)


def _qualifier(min_sample: int | None, column: str | None) -> str:
    """ " (minimum 200 3-point attempts)", or nothing. Shown because it answers
    "why isn't X here?" before it is asked - and makes an empty early-season
    board say why it is empty."""
    if not min_sample:
        return ""
    return f" (minimum {min_sample:,} {MIN_SAMPLE_LABELS.get(column or '', column or '')})"


def _phrase_leaderboard(rows: list[dict[str, Any]], label: str, where: str, period: str, qualifier: str = "", ratio: tuple[str, str] | None = None) -> str:
    if not rows:
        return f"No players qualified for {label} in {where} in the {period}{qualifier}."
    top = rows[0]
    sentence = f"{top['display_name']} led {where} in {label} in the {period}{qualifier}, at {_leader_value(top, ratio)}."
    rest = [f"{r['display_name']} ({_leader_value(r, ratio, short=True)})" for r in rows[1:]]
    return sentence + (f" Next: {', '.join(rest)}." if rest else "")


# The qualifying column's real name is not something to put in front of a
# reader ("total_minutes", "gamesPlayed").
MIN_SAMPLE_LABELS = {
    "total_minutes": "minutes",
    "gamesPlayed": "games",
    "games_played": "games",
    "minutes": "minutes",
    "fieldGoalsAttempted": "field-goal attempts",
    "threePointFieldGoalsAttempted": "3-point attempts",
    "freeThrowsAttempted": "free-throw attempts",
    "field_goals_attempted": "field-goal attempts",
    "true_shooting_attempts": "true-shooting attempts",
}


def _tabulate_leaderboard(
    rows: list[dict[str, Any]],
    label: str,
    where: str,
    period: str,
    fields: list[str],
    min_sample: int | None,
    min_sample_column: str | None,
    ratio: tuple[str, str] | None = None,
) -> str:
    """A table once extra columns are asked for - a sentence carrying three
    numbers per player across ten players is unreadable, and the qualifying
    minimum belongs on screen so "why isn't X here?" has a visible answer."""
    header_note = _qualifier(min_sample, min_sample_column)
    if not rows:
        return f"No players qualified for {label} in {where} in the {period}{header_note}."
    columns = [(label, "value")] + [(f, f) for f in fields]
    name_width = max(len(r["display_name"]) for r in rows)
    # The ranked metric keeps its own precision (9.91, not 9.9); the extra
    # box-score columns are per-game averages, where one decimal is the norm.
    cell = lambda row, key: _leader_value(row, ratio, short=True) if key == "value" else _table_cell(row.get(key))  # noqa: E731
    widths = [max(len(title), *(len(cell(r, key)) for r in rows)) for title, key in columns]
    lines = [f"{label}, {where}, {period}{header_note}:"]
    lines.append(" " * name_width + "  " + "  ".join(t.rjust(w) for (t, _), w in zip(columns, widths, strict=True)))
    for row in rows:
        cells = "  ".join(cell(row, key).rjust(w) for (_, key), w in zip(columns, widths, strict=True))
        lines.append(f"{row['display_name'].ljust(name_width)}  {cells}")
    return "\n".join(lines)


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
    "points": ("points per game", [("avgPoints", "PPG")]),
    "rebounds": ("rebounds per game", [("avgRebounds", "RPG")]),
    "assists": ("assists per game", [("avgAssists", "APG")]),
    "steals": ("steals per game", [("avgSteals", "SPG")]),
    "blocks": ("blocks per game", [("avgBlocks", "BPG")]),
    "minutes": ("minutes per game", [("avgMinutes", "MPG")]),
    "threePointFieldGoalsMade": ("3-pointers per game", [("avgThreePointFieldGoalsMade", "3PM/G")]),
}

# espnanalytics.com's "Net Pts Fingerprint": the play-type breakdown behind a
# player's NetPoints. `total` is the summary rather than a play type, so it is
# reported on the headline line instead of as a category row.
FINGERPRINT_SUMMARY_CATEGORY = "total"

# These six partition a player's NetPoints exactly: they sum to the offensive
# and defensive totals for every player checked, to within float rounding
# (max deviation 0.005 across the league's top minute-earners). Verified
# against net_points_player.offense / .defense, which are stored separately.
FINGERPRINT_PARTITION = ("two_pt", "three_pt", "free_throw", "turnover", "rebound", "foul")

# The remaining categories are overlapping descriptive slices, not a second
# partition - a driving layup at the rim counts in `driving`, `layup` AND
# `rim`, and they sum to roughly twice the total. Shown as detail, kept out of
# the column that is meant to add up.


def player_netpoints(ctx: TemplateContext, slots: dict[str, Any]) -> TemplateResult:
    """One player's NetPoints, with the play-type fingerprint.

    NetPoints existed only as leaderboard metrics - ways to rank the league - so
    a question about one player had nowhere to land: the guard in player_stat
    fired correctly, then the agent ranked the league and dropped the player. A
    guard that turns a wrong answer into a slow one needs something to fall
    through TO."""
    con = ctx.con
    # Settled before the name is resolved: the season is what narrows an
    # ambiguous name to the players with NetPoints in it.
    season = slots.get("season") or current_season()
    season_type = slots.get("season_type") or 2
    one_game = slots.get("order") in ("recent", "first")
    player = _resolved_player(con, slots.get("player"), "player_netpoints needs a player name", available=_NET_POINTS_GAMES if one_game else _NET_POINTS, season=season)
    if isinstance(player, TemplateResult):
        return player

    # "NetPoints from his LAST regular season game" was answered with the whole
    # season - 43 games - because nothing scoped it. Per-game NetPoints live in
    # their own table, with no fingerprint breakdown, so this is a different
    # answer rather than a filtered one.
    if one_game:
        return _single_game_netpoints(ctx, player, season, season_type, slots["order"])

    # net_points_player uses its OWN string season_type; filtering it with the
    # numeric one every other table uses silently matches nothing.
    label = SEASON_TYPE_LABELS.get(season_type, "Regular Season")
    headline = con.execute(
        "SELECT overall, offense, defense, overall_per_100_poss, total_minutes, games FROM net_points_player WHERE athlete_id = ? AND season = ? AND net_points_season_type = ?",
        [player.id, season, label],
    ).fetchone()

    categories = [c for c in FINGERPRINT_CATEGORIES.values() if c != FINGERPRINT_SUMMARY_CATEGORY]
    selected = ", ".join(f"{c}_o_net_pts, {c}_d_net_pts, {c}_t_net_pts" for c in categories)
    # The fingerprint table has no season_type column at all.
    fingerprint = con.execute(
        f"SELECT total_poss, {selected} FROM net_points_player_fingerprint WHERE athlete_id = ? AND season = ?",
        [player.id, season],
    ).fetchone()

    # Per 100 possessions by default: the fingerprint is for comparing players,
    # and season totals mostly rank by playing time. Totals stay in `data`, and
    # the `rate` slot asks for them.
    possessions = fingerprint[0] if fingerprint else None
    per_100 = slots.get("rate") != "total" and bool(possessions)
    scale = 100.0 / possessions if per_100 and possessions else 1.0

    breakdown: list[dict[str, Any]] = []
    if fingerprint is not None:
        for index, category in enumerate(categories):
            o, d, t = fingerprint[1 + index * 3 : 1 + index * 3 + 3]
            if o is None and d is None and t is None:
                continue
            breakdown.append(
                {
                    "category": category.replace("_", " "),
                    "offense": None if o is None else o * scale,
                    "defense": None if d is None else d * scale,
                    "total": None if t is None else t * scale,
                    "offense_season_total": o,
                    "defense_season_total": d,
                    "total_season_total": t,
                }
            )
        breakdown.sort(key=lambda row: -abs(row["total"] or 0))

    period = _period(season, season_type)
    if headline is None and not breakdown:
        return TemplateResult(
            data={"player": player.name, "season": season},
            answer=f"The warehouse has no {period} NetPoints for {player.name}.",
        )
    return TemplateResult(
        data={"player": player.name, "season": season, "headline": headline, "fingerprint": breakdown},
        answer=_phrase_netpoints(player.name, period, headline, breakdown, per_100, possessions),
    )


def _single_game_netpoints(ctx: TemplateContext, player: Entity, season: int, season_type: int, order: str) -> TemplateResult:
    """One game's NetPoints, from net_points_player_game.

    That table is opt-in (`data pull --include-net-points-daily`) and, unlike
    net_points_player, uses the normal numeric season_type. It carries no
    play-type split of its own - that lives in the sibling
    net_points_player_game_fingerprint, which the `fingerprint` intent draws."""
    period = _period(season, season_type)
    try:
        row = ctx.con.execute(
            "SELECT g.event_id, g.date, g.o_net_pts, g.d_net_pts, g.t_net_pts, g.o_poss, g.d_poss, g.t_wpa "
            "FROM (SELECT npg.*, gm.date FROM net_points_player_game npg JOIN games gm ON gm.event_id = npg.event_id "
            "      WHERE npg.athlete_id = ? AND npg.season = ? AND npg.season_type = ?) g "
            f"ORDER BY g.date {'ASC' if order == 'first' else 'DESC'} LIMIT 1",
            [player.id, season, season_type],
        ).fetchone()
    except duckdb.Error as exc:
        # The table only exists if the daily NetPoints fetch was run. Saying so
        # beats falling through to an agent that has no better source.
        raise TemplateUnsupported(f"per-game NetPoints unavailable: {exc}") from exc

    if row is None:
        which = "earliest" if order == "first" else "most recent"
        return TemplateResult(
            data={"player": player.name, "season": season, "game": None},
            answer=f"No per-game NetPoints on record for {player.name}'s {which} {period} game.",
        )
    event_id, date, o, d, t, o_poss, d_poss, wpa = row
    which = "first" if order == "first" else "most recent"
    game = {"event_id": event_id, "date": _eastern_date(date), "offense": o, "defense": d, "total": t}
    detail = []
    if o_poss is not None and d_poss is not None:
        detail.append(f"{o_poss:.0f} offensive and {d_poss:.0f} defensive possessions")
    if wpa is not None:
        detail.append(f"{wpa:+.3f} win probability added")
    answer = f"{player.name}, NetPoints in his {which} {period} game ({_eastern_date(date)}): {_table_cell(t)} total ({_table_cell(o)} offense, {_table_cell(d)} defense)."
    if detail:
        answer += "\n  " + ", ".join(detail) + "."
    answer += "\n  (Ask for a fingerprint of that game to see the play-type split behind it.)"
    return TemplateResult(data={"player": player.name, "game": game}, answer=answer)


def _phrase_netpoints(
    name: str,
    period: str,
    headline: tuple[Any, ...] | None,
    breakdown: list[dict[str, Any]],
    per_100: bool,
    possessions: float | None,
) -> str:
    lines = []
    if headline is not None:
        # NOT `per_100`: that is the parameter saying which UNITS the
        # fingerprint is in, and unpacking over it made a rate=total request
        # print season totals under a "per 100 possessions" heading.
        overall, offense, defense, per_100_rate, minutes, games = headline
        lines.append(f"{name}, NetPoints in the {period}: {_table_cell(overall)} overall ({_table_cell(offense)} offense, {_table_cell(defense)} defense)")
        detail = []
        if per_100_rate is not None:
            detail.append(f"{per_100_rate:.2f} per 100 possessions")
        if minutes:
            detail.append(f"{int(minutes):,} minutes")
        if games:
            detail.append(f"{int(games)} games")
        if detail:
            lines.append("  " + ", ".join(detail) + ".")
    else:
        lines.append(f"{name}, NetPoints fingerprint in the {period} (no season totals on record):")

    if not breakdown:
        lines.append("  No play-type fingerprint on record for this season.")
        return "\n".join(lines)

    units = "per 100 possessions" if per_100 else "season totals"
    scope = f" over {possessions:,.0f} possessions" if per_100 and possessions else ""
    width = max(len(row["category"]) for row in breakdown)
    partition_names = {c.replace("_", " ") for c in FINGERPRINT_PARTITION}
    # NOT `detail`: the headline block above binds that to a list of strings,
    # and reusing it here is the same shadowing that made a rate=total request
    # print under a per-100 heading.
    partition_rows = [r for r in breakdown if r["category"] in partition_names]
    detail_rows = [r for r in breakdown if r["category"] not in partition_names]

    # Offense and defense get a section each, sorted by their OWN side. One
    # table sorted by total renders the defensive profile invisible: for SGA,
    # `turnover` carries the largest defensive value of any category and lands
    # 15th of 21 by total, below categories whose defense is ~0.
    for side, heading in (("offense", "Offense"), ("defense", "Defense")):
        ranked = sorted((r for r in partition_rows if r[side] is not None), key=lambda r: -abs(r[side]))
        if not ranked:
            continue
        lines.append("")
        lines.append(f"  {heading}, {units}{scope}:")
        for row in ranked:
            # Two decimals: per-100 values are small, and one decimal collapses
            # most of the categories onto the same number.
            lines.append("  " + row["category"].ljust(width) + f"{row[side]:.2f}".rjust(9))
        # The sum is printed so the reader can check it against the headline -
        # these six really do add up, and showing it says so without asserting.
        lines.append("  " + "-" * (width + 9))
        lines.append("  " + "total".ljust(width) + f"{sum(r[side] for r in ranked):.2f}".rjust(9))

    if detail_rows:
        lines.append("")
        lines.append(f"  Play-type detail, {units} (overlapping slices - a driving layup at the rim")
        lines.append("  counts in driving, layup and rim, so these do not add up):")
        lines.append("  " + "category".ljust(width) + "".join(h.rjust(9) for h in ("O", "D")))
        for row in sorted(detail_rows, key=lambda r: -abs(r["total"] or 0)):
            cells = "".join(("-" if row[k] is None else f"{row[k]:.2f}").rjust(9) for k in ("offense", "defense"))
            lines.append("  " + row["category"].ljust(width) + cells)
    return "\n".join(lines)


DEFAULT_HISTORY_SEASONS = 4
MAX_HISTORY_SEASONS = 20


def player_history(ctx: TemplateContext, slots: dict[str, Any]) -> TemplateResult:
    """One player's stat across several seasons: the last four by default, a
    number of them the question named, or every one of them for a career.

    Every other template answers about a single season, so a multi-season
    question routed to `leaderboard`, which dropped the player and ranked the
    league. Distinct from the other gaps here: a missing DIMENSION cutting
    across the shapes that existed, not a missing shape.

    .. versionchanged:: 2.1.0
       Honors ``span``: "his career" is every season on record, where it used
       to be the default four under a heading that did not say so. The heading
       now names the seasons shown.
    """
    con = ctx.con
    # A named season anchors the range's END rather than replacing it, so
    # "3pt% over the 4 seasons through 2024" still spans four rows.
    latest = slots.get("season") or current_season()
    # Narrowed over every season the history could read, not the last N: the
    # query takes each player's last seasons up to `latest` wherever they fall,
    # so a player who retired a decade earlier still has an answer here.
    player = _resolved_player(con, slots.get("player"), "player_history needs a player name", available=_SEASON_LINES, through=latest)
    if isinstance(player, TemplateResult):
        return player

    stat = slots.get("stat")
    if not isinstance(stat, str) or stat not in HISTORY_COLUMNS:
        raise TemplateUnsupported(f"no per-season history for stat {stat!r}")
    label, columns = HISTORY_COLUMNS[stat]

    span = slots.get("span")
    if span and span != "career":
        raise TemplateUnsupported(f"no span called {span!r}")
    career = span == "career"
    season_type = slots.get("season_type") or 2
    limit = slots.get("limit")
    seasons = limit if isinstance(limit, int) and 1 <= limit <= MAX_HISTORY_SEASONS else DEFAULT_HISTORY_SEASONS

    # A career is every season, however many - not the default four, and not a
    # count the model put in `limit`, which the router asks it for on this
    # intent whether or not the question gave one. The heading says which
    # seasons are shown either way.
    bound = "" if career else " LIMIT ?"
    rows = con.execute(
        f"SELECT season, gamesPlayed, {', '.join(c for c, _ in columns)} FROM player_season_stats_deduped WHERE athlete_id = ? AND season_type = ? AND season <= ? ORDER BY season DESC{bound}",
        [player.id, season_type, latest, *([] if career else [seasons])],
    ).fetchall()

    period = SEASON_TYPE_NAMES.get(season_type, "regular season")
    history = [dict(zip(["season", "games"] + [c for c, _ in columns], r, strict=True)) for r in rows]
    return TemplateResult(
        data={"player": player.name, "stat": stat, "span": "career" if career else None, "seasons": history},
        answer=_phrase_history(player.name, label, period, history, columns, career=career),
    )


def _phrase_history(name: str, label: str, period: str, history: list[dict[str, Any]], columns: list[tuple[str, str]], *, career: bool = False) -> str:
    if not history:
        return f"The warehouse has no {period} seasons on record for {name}."
    headers = ["season", "G"] + [h for _, h in columns]
    keys = ["season", "games"] + [c for c, _ in columns]
    widths = [max(len(h), *(len(_table_cell(row.get(k))) for row in history)) for h, k in zip(headers, keys, strict=True)]
    newest, oldest = history[0]["season"], history[-1]["season"]
    years = f"{oldest}" if oldest == newest else f"{oldest}-{newest}"
    shown = f"career, {years}" if career else years
    lines = [f"{name}, {label} by {period}, {shown} (most recent first):", "  ".join(h.rjust(w) for h, w in zip(headers, widths, strict=True))]
    for row in history:
        lines.append("  ".join(_table_cell(row.get(k)).rjust(w) for k, w in zip(keys, widths, strict=True)))
    return "\n".join(lines)


def _season_row(con: duckdb.DuckDBPyConnection, athlete_id: str, columns: list[str], season: int, season_type: int) -> tuple[Any, ...] | None:
    """One player's season line. Reads player_season_stats_deduped, the view
    that has already collapsed a traded player's per-stint rows into one, so no
    caller has to remember to. `columns` comes from PLAYER_STAT_COLUMNS or
    HISTORY_COLUMNS - never from a slot."""
    return con.execute(
        f"SELECT {', '.join(columns)} FROM player_season_stats_deduped WHERE athlete_id = ? AND season = ? AND season_type = ?",
        [athlete_id, season, season_type],
    ).fetchone()


STAT_LINE = ("points", "rebounds", "assists")

# A comparison's default line is longer than a single player's, because the two
# answers are read differently. "How many points did Luka average" wants the
# number it asked for; "compare Luka and SGA" is asking which of them is
# better, and three counting stats cannot answer that - they leave out both
# halves of the defensive line and everything a player gives back. Prose is
# already refused here for the same reason (see _phrase_compare); a table costs
# nothing per extra row, so the rows a comparison actually turns on are all
# present by default.
#
# A NAMED stat still narrows to that one. Somebody asking "who scores more"
# gets scoring, not a wall.
COMPARE_STAT_LINE = ("points", "rebounds", "assists", "steals", "blocks", "turnovers", "fouls", "minutes")


def _wanted_stats(slots: dict[str, Any], default: tuple[str, ...] = STAT_LINE) -> list[str]:
    """The stats to report: the one named, or ``default`` if none was.

    A stat that was NAMED but is not supported must not fall back to the
    default line - that is how "what was Steph Curry's avg 3pt shot distance"
    came back as "26.6 points, 3.6 rebounds and 4.7 assists per game". Falling
    through to the agent is slow; answering a different question is worse."""
    stat = slots.get("stat")
    if stat is None or (isinstance(stat, str) and not stat.strip()):
        return list(default)
    if stat in PLAYER_STAT_COLUMNS:
        return [stat]
    raise TemplateUnsupported(f"no per-game column for stat {stat!r}")


# ---- one player's games, narrowed the way a question narrows them ----
#
# Shared by player_stat and game_log. A question narrowed to an opponent, a
# venue or a teammate's absence is answered from box scores - one row per game -
# because the season line cannot be narrowed, and a career is every season of
# them. Measured before any of this existed: "jaylen brown last 8 games vs
# pistons" listed the Celtics' last eight games, and "Podziemski game log
# without curry" listed every game he played.

# ESPN's one timestamp shape. The Eastern date of one is _eastern_date, defined
# once above - both branches of this module added their own copy.
_ESPN_TIMESTAMP = "%Y-%m-%dT%H:%MZ"


def _eastern_day(day: str) -> tuple[str, str]:
    """The half-open range of ``games.date`` values that tip on the Eastern
    date ``day``. Timestamps of one fixed shape compare correctly as strings.

    ``LIKE 'YYYY-MM-DD%'`` matched the UTC date instead, so asking for a game on
    the 15th found the one played the evening of the 14th, and missed its own
    whenever it tipped after 7pm."""
    start = datetime.strptime(day, "%Y-%m-%d") + _EASTERN_SHIFT
    return start.strftime(_ESPN_TIMESTAMP), (start + timedelta(days=1)).strftime(_ESPN_TIMESTAMP)


def _season_name(season: int) -> str:
    """1994 -> "1993-94": seasons are named for the year they end in."""
    return f"{season - 1}-{season % 100:02d}"


def _count_games(count: int) -> str:
    return f"{count:,} game{'' if count == 1 else 's'}"


def _rounded(value: Any) -> float | None:
    """A computed per-game figure to one decimal, as ESPN's stored ones are -
    "21.33 points" next to a stored "27.7" reads as a different unit."""
    return None if value is None else round(float(value), 1)


@dataclass(frozen=True)
class _Span:
    """The seasons an answer covers: one (``season``), or a whole career
    (``season`` None) from ``first`` on, less any ``phantom`` season that is a
    copy of another."""

    season: int | None
    season_type: int
    first: int = 0
    phantom: tuple[int, ...] = ()

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
        if self.season is not None:
            return f"in the {_period(self.season, self.season_type)}"
        return f"over {whose} ({self.years(first, last)})"


def _span_of(span: Any, season: Any, season_type: int, table: str) -> _Span:
    """The seasons a question covers. ``table`` sets how far back a career
    reaches - box scores from 1994, the season line from 1977 - since a career
    is only as long as the table it is summed from."""
    if not span:
        return _Span(season if isinstance(season, int) and season else current_season(), season_type)
    if span != "career":
        raise TemplateUnsupported(f"no span called {span!r}")
    if isinstance(season, int) and season:
        # "Career" and a named year at once. Either reading answers a different
        # question from the other, so neither is picked.
        raise TemplateUnsupported(f"a career span and the {season} season at once")
    coverage = COVERAGE[table]
    return _Span(None, season_type, coverage.floor(season_type).season, coverage.phantom)


# A player-game ESPN lists as played but records no minutes for. Every such row
# in the warehouse carries no stats either (checked, 1994-2026), and they are of
# two kinds. In 2006-2012 they are ~10,000 a season of appearances nobody made,
# which ESPN's own games-played counts mostly leave out (dropping them makes 378
# of 445 players' 2009 counts agree, against 30). In 2013-2018 they are whole
# team box scores ESPN left empty - ~13% of team-games, which is why summing
# those seasons' box scores gives 87% of the season totals. Averaged in, either
# kind reads as a game of zeros, so they are left out and the answer says how
# many.
_RECORDED = "pgl.minutes IS NOT NULL"

# One join serves venue and result both: games.home_team_id agrees with
# team_box_stats.home_away on every row (checked, all 83,424). Keyed on season
# too, since the phantom 1993 shares its event ids with 1994.
_PLAYER_GAMES = "FROM player_game_log pgl JOIN games g ON g.event_id = pgl.event_id AND g.season = pgl.season"

# The teammate played in that game: a row, not flagged did-not-play, with
# minutes - the same line _RECORDED draws for the player himself.
_TEAMMATE_PLAYED = "EXISTS (SELECT 1 FROM player_box_stats m WHERE m.athlete_id = ? AND m.event_id = pgl.event_id AND m.season = pgl.season AND NOT m.did_not_play AND m.minutes IS NOT NULL)"

# An open end of a stint, as a string that sorts before or after any date.
_OPEN_START, _OPEN_END = "0000", "9999"


@dataclass
class _Narrowed:
    """One player's games in a span, and whatever the question narrowed them
    by. Every clause applies to ``_PLAYER_GAMES``; the base ones (player, season
    type, span, played) are kept apart from the rest so an empty answer can say
    which narrowing emptied it."""

    base: list[str]
    base_params: list[Any]
    extra: list[str] = field(default_factory=list)
    extra_params: list[Any] = field(default_factory=list)
    opponent: Entity | None = None
    venue: str | None = None
    without: Entity | None = None
    tenure: tuple[str, list[Any]] | None = None
    date: str | None = None

    def clauses(self, *, narrowed: bool = True, recorded: bool = True) -> tuple[str, list[Any]]:
        """The WHERE body and its parameters - without the narrowing when
        ``narrowed`` is false, and over the empty lines when ``recorded`` is."""
        where = [*self.base, _RECORDED if recorded else f"NOT ({_RECORDED})"]
        params = list(self.base_params)
        if narrowed:
            where += self.extra
            params += self.extra_params
        return " AND ".join(where), params

    def filters(self, *, dated: bool = True) -> str:
        """What the games were narrowed to, as it follows a name: ``" vs the
        Detroit Pistons at home"``."""
        parts = []
        if self.opponent is not None:
            parts.append(f"vs the {self.opponent.name}")
        if self.venue:
            parts.append("at home" if self.venue == "home" else "on the road")
        if self.without is not None:
            parts.append(f"without {self.without.name}")
        if self.date and dated:
            parts.append(f"on {self.date}")
        return "".join(f" {part}" for part in parts)


def _checked_venue(venue: Any) -> str:
    if venue not in ("home", "away"):
        raise TemplateUnsupported(f"no venue called {venue!r}")
    return str(venue)


def _narrow_player_games(con: duckdb.DuckDBPyConnection, player: Entity, span: _Span, *, opponent: Any, venue: Any, without: Any) -> _Narrowed | TemplateResult:
    """``player``'s games in ``span``, narrowed to an opponent, a venue and a
    teammate's absence where the question named them. A name that needs a
    clarifying question comes back as the TemplateResult asking it."""
    season_clause, season_params = span.clause("pgl.season")
    narrowed = _Narrowed(
        base=["pgl.athlete_id = ?", "pgl.season_type = ?", season_clause, "NOT pgl.did_not_play"],
        base_params=[player.id, span.season_type, *season_params],
    )
    if opponent:
        team = _resolved_team(con, opponent)
        if isinstance(team, TemplateResult):
            return team
        narrowed.opponent = team
        narrowed.extra.append("pgl.opponent_team_id = ?")
        narrowed.extra_params.append(team.id)
    if venue:
        narrowed.venue = _checked_venue(venue)
        narrowed.extra.append("(g.home_team_id = pgl.team_id) = ?")
        narrowed.extra_params.append(narrowed.venue == "home")
    if without:
        mate = _resolved_teammate(con, without, player, span)
        if isinstance(mate, TemplateResult):
            return mate
        narrowed.without = mate
        narrowed.tenure = _tenure_clause(con, mate, span)
        tenure, tenure_params = narrowed.tenure
        narrowed.extra += [tenure, f"NOT {_TEAMMATE_PLAYED}"]
        narrowed.extra_params += [*tenure_params, mate.id]
    return narrowed


def _teammate_stints(con: duckdb.DuckDBPyConnection, athlete_id: str) -> list[tuple[int, str, str, str]]:
    """When a player was on each team: ``(season, team_id, start, end)``, with
    ``start``/``end`` compared against ``games.date``.

    There is no roster table, so this is read off his own box-score rows, and
    the one fact that makes that hard is that an injured player mostly has NO
    row: Stephen Curry's 2026 is 43 rows, none of them did-not-play, for an
    82-game Warriors season. So a stint runs from his first row for a team to
    his last, and then:

    - it is open at the start of the season when he ended the previous one on
      that team (or has no earlier rows at all) - LeBron James's first 2026 row
      is 2025-11-19, and the Lakers' 14 games before it were played without
      him;
    - it is open at the end when no later row that season is for another team,
      so an injury that ends a season still counts.

    A mid-season arrival from another team is not extended backwards: Seth
    Curry's first 2026 Warriors row is 2025-12-03, and their October games were
    not played "without" somebody who was in Charlotte's plans. The gap between
    a traded player's last game for one team and his first for the next belongs
    to neither, which undercounts rather than guesses."""
    phantom = COVERAGE["player_game_log"].phantom
    excluded = f" AND season NOT IN ({', '.join('?' for _ in phantom)})" if phantom else ""
    rows = con.execute(
        f"SELECT season, team_id, MIN(game_date), MAX(game_date) FROM player_game_log WHERE athlete_id = ?{excluded} GROUP BY season, team_id ORDER BY season, MIN(game_date)",
        [athlete_id, *phantom],
    ).fetchall()
    by_season: dict[int, list[tuple[str, str, str]]] = {}
    for season, team_id, first, last in rows:
        by_season.setdefault(int(season), []).append((str(team_id), str(first), str(last)))
    stints: list[tuple[int, str, str, str]] = []
    carried: str | None = None  # the team he ended the previous season on
    for season in sorted(by_season):
        spells = by_season[season]
        final = max(range(len(spells)), key=lambda i: spells[i][2])
        for index, (team_id, first, last) in enumerate(spells):
            start = _OPEN_START if index == 0 and carried in (None, team_id) else first
            end = _OPEN_END if index == final else last
            stints.append((season, team_id, start, end))
        carried = spells[final][0]
    return stints


def _tenure_clause(con: duckdb.DuckDBPyConnection, mate: Entity, span: _Span) -> tuple[str, list[Any]]:
    """SQL keeping the games ``pgl`` played on a team ``mate`` was on at the
    time - see _stints for how "was on" is read."""
    stints = [s for s in _teammate_stints(con, mate.id) if span.season is None or s[0] == span.season]
    if not stints:
        return "FALSE", []
    rows = ", ".join("(?, ?, ?, ?)" for _ in stints)
    return (
        f"EXISTS (SELECT 1 FROM (VALUES {rows}) AS stint(season, team_id, start_date, end_date) "
        "WHERE stint.season = pgl.season AND stint.team_id = pgl.team_id AND pgl.game_date BETWEEN stint.start_date AND stint.end_date)"
    ), [value for stint in stints for value in stint]


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


def _no_narrowed_games(con: duckdb.DuckDBPyConnection, player: Entity, span: _Span, narrowed: _Narrowed) -> str:
    """Why a narrowed question found no games, naming the fact that is really
    missing - his games in that span, the teammate, or the match. They are
    different sentences, and "X has no games" said of a player who simply never
    met that opponent sends the reader to look in the wrong place."""
    if narrowed.date:
        # One named day: the rest of his career is not the fact that is missing.
        return f"No {span.kind} game on {narrowed.date} found for {player.name}{narrowed.filters(dated=False)}."
    where, params = narrowed.clauses(narrowed=False)
    total, first, last = con.execute(f"SELECT COUNT(*), MIN(pgl.season), MAX(pgl.season) {_PLAYER_GAMES} WHERE {where}", params).fetchone() or (0, None, None)
    if not total:
        if span.career:
            return f"{player.name} has no {span.kind} box scores in the warehouse, which begin with the {_season_name(span.first)} season."
        return f"No {span.during()[len('in the ') :]} games found for {player.name}."
    during = span.during(first, last)
    if narrowed.without is not None and narrowed.tenure is not None:
        tenure, tenure_params = narrowed.tenure
        together = con.execute(f"SELECT COUNT(*) {_PLAYER_GAMES} WHERE {where} AND {tenure}", [*params, *tenure_params]).fetchone()
        if not together or not together[0]:
            return f"{narrowed.without.name} was not {player.name}'s teammate in any of his {_count_games(total)} {during}."
    return f"{player.name} played {_count_games(total)} {during}, none of them{narrowed.filters()}."


def _box_score_notes(con: duckdb.DuckDBPyConnection, player: Entity, span: _Span, narrowed: _Narrowed, *, career_note: bool = True) -> list[str]:
    """What a box-score answer has to say about itself: what "without" was
    taken to mean, the empty lines left out, and - unless ``career_note`` is
    off, as it is for one dated game - a career older than the box scores."""
    notes = []
    if narrowed.without is not None:
        notes.append(
            f"Without {narrowed.without.name} means games he did not play while on the same team - a did-not-play entry, or no line in the box score at all, which is how most injuries appear."
        )
    where, params = narrowed.clauses(recorded=False)
    row = con.execute(f"SELECT COUNT(*) {_PLAYER_GAMES} WHERE {where}", params).fetchone()
    empty = row[0] if row else 0
    if empty:
        notes.append(f"Not counted: {empty} game{'s' if empty != 1 else ''} in this span whose box score lists him with no minutes and no stats.")
    if span.career and career_note:
        row = con.execute(
            "SELECT MIN(season) FROM player_season_stats_deduped WHERE athlete_id = ? AND season_type = ? AND gamesPlayed > 0",
            [player.id, span.season_type],
        ).fetchone()
        earliest = row[0] if row else None
        if earliest is not None and earliest < span.first:
            notes.append(f"Box scores begin with the {_season_name(span.first)} season, so his {earliest}-{span.first - 1} seasons are not counted.")
    return notes


# The three shooting percentages, as (made column, attempted column, how the
# sentence says it, what the shots are called). The column names are the same
# in the season table and in the box scores, so every span reads them alike.
SHOOTING_STATS: dict[str, tuple[str, str, str, str]] = {
    "fieldGoalPct": ("fieldGoalsMade", "fieldGoalsAttempted", "from the field", "field goals"),
    "threePointFieldGoalPct": ("threePointFieldGoalsMade", "threePointFieldGoalsAttempted", "on 3-pointers", "3-pointers"),
    "freeThrowPct": ("freeThrowsMade", "freeThrowsAttempted", "on free throws", "free throws"),
}
"""Shooting percentages ``player_stat`` answers, always with the makes and
attempts behind them - computed from those, never read from a stored
percentage, so a season, a career and a set of games are all the same sum.

.. versionadded:: 2.1.0
"""

# A career per-game figure is the career total over career games, never an
# average of season averages. Rebounds' total column is named differently from
# the per-stat key; minutes have none, and are weighted by games instead.
_CAREER_TOTALS: dict[str, str | None] = {
    "points": "points",
    "rebounds": "totalRebounds",
    "assists": "assists",
    "steals": "steals",
    "blocks": "blocks",
    "turnovers": "turnovers",
    "minutes": None,
    "fouls": "fouls",
    "threePointFieldGoalsMade": "threePointFieldGoalsMade",
    "fieldGoalsMade": "fieldGoalsMade",
    "freeThrowsMade": "freeThrowsMade",
}


def player_stat(ctx: TemplateContext, slots: dict[str, Any]) -> TemplateResult:
    """One named player's numbers: a season line, a career, or the games a
    question narrowed to.

    - One season comes from player_season_stats_deduped, so a traded player's
      multi-row season is already collapsed.
    - A career (``span``) is summed from the same table - totals over games,
      never an average of averages - which reaches back to 1977 because it is
      fetched per player over a whole career.
    - An ``opponent``, ``venue`` or ``without`` narrows the games, which only
      box scores can do, so those are summed over player_game_log from 1994.
    - A shooting percentage comes with the makes and attempts behind it.

    An incomplete name ("Luka", "Curry") is answered with a question, not a
    guess: falling through costs minutes and guesses anyway, and a prominence
    tiebreak was measured and rejected (no threshold separates Luka Doncic from
    Luka Garza without also wrongly resolving "Brown").

    .. versionchanged:: 2.1.0
       Honors ``opponent``, ``venue``, ``without`` and ``span``, answers
       shooting percentages, and refuses a ``limit`` - a player's numbers over
       his last N games is a game log, which averages the games it lists.
    """
    con = ctx.con
    # Settled before the name is resolved: the span, and the table it is read
    # from, are what narrow an ambiguous name to the players who could be the
    # answer - a career keeps Dell Curry, this season does not.
    season_type = slots.get("season_type") or 2
    opponent, venue, without = slots.get("opponent"), slots.get("venue"), slots.get("without")
    from_box_scores = bool(opponent or venue or without)
    span = _span_of(slots.get("span"), slots.get("season"), season_type, "player_game_log" if from_box_scores else "player_season_stats_deduped")
    lines = _GAME_LOGS if from_box_scores else _SEASON_LINES
    player = _resolved_player(con, slots.get("player"), "player_stat needs a player name", available=lines, season=span.season, through=_career_end(span.season))
    if isinstance(player, TemplateResult):
        return player
    if slots.get("limit"):
        # "Jokic averages last 10 games" answered with his season line would be
        # the substitution this module exists to stop; game_log averages
        # exactly the games it lists.
        raise TemplateUnsupported("a player's numbers over a limited set of games is a game_log question")

    stat = slots.get("stat")
    shooting = SHOOTING_STATS.get(stat) if isinstance(stat, str) else None
    wanted = [] if shooting else _wanted_stats(slots)

    if from_box_scores:
        narrowed = _narrow_player_games(con, player, span, opponent=opponent, venue=venue, without=without)
        if isinstance(narrowed, TemplateResult):
            return narrowed
        return _box_score_player_stat(con, player, span, narrowed, wanted, shooting)
    if span.career:
        return _career_player_stat(con, player, span, wanted, shooting)

    season = span.season or current_season()
    columns = ["gamesPlayed"]
    for name in wanted:
        per_game, total, _ = PLAYER_STAT_COLUMNS[name]
        columns.append(per_game)
        if total:
            columns.append(total)
    if shooting:
        columns += [shooting[0], shooting[1]]
    row = _season_row(con, player.id, columns, season, season_type)

    period = _period(season, season_type)
    if row is None:
        return TemplateResult(
            data={"player": player.name, "season": season, "stats": {}},
            answer=f"{player.name} has no {period} numbers in the warehouse.",
        )
    values = dict(zip(columns, row, strict=True))
    if shooting:
        return _shooting_result(player.name, {"season": season}, values, shooting, when=f"in the {period}")
    return TemplateResult(
        data={"player": player.name, "season": season, "stats": values},
        answer=_phrase_player_stat(player.name, period, values, wanted),
    )


def _career_player_stat(con: duckdb.DuckDBPyConnection, player: Entity, span: _Span, wanted: list[str], shooting: tuple[str, str, str, str] | None) -> TemplateResult:
    """A career line summed from the season table. A season whose total is
    missing falls back to its average times its games, and each per-game figure
    is divided by the games that actually carry that stat."""
    selects = ["SUM(gamesPlayed)", "MIN(season)", "MAX(season)", "COUNT(*)"]
    for stat in wanted:
        per_game = PLAYER_STAT_COLUMNS[stat][0]
        total = _CAREER_TOTALS[stat]
        amount = f"COALESCE({total}, {per_game} * gamesPlayed)" if total else f"{per_game} * gamesPlayed"
        selects += [f"SUM({amount}) / SUM(CASE WHEN {amount} IS NOT NULL THEN gamesPlayed END)", f"SUM({amount})"]
    if shooting:
        made, attempted = shooting[0], shooting[1]
        selects += [f"SUM(CASE WHEN {attempted} IS NOT NULL THEN {made} END)", f"SUM({attempted})"]
    row = con.execute(
        f"SELECT {', '.join(selects)} FROM player_season_stats_deduped WHERE athlete_id = ? AND season_type = ? AND gamesPlayed > 0",
        [player.id, span.season_type],
    ).fetchone()
    if row is None or not row[0]:
        return TemplateResult(data={"player": player.name, "span": "career", "stats": {}}, answer=f"{player.name} has no {span.kind} numbers in the warehouse.")
    games, first, last, seasons = row[:4]
    plural = "" if seasons == 1 else "s"
    when = f"over his career ({seasons} {span.kind}{plural}, {first}-{last})" if first != last else f"over his career (the {first} {span.kind})"
    scope = {"span": "career", "seasons": [first, last], "season_count": seasons}
    values: dict[str, Any] = {"gamesPlayed": int(games)}
    if shooting:
        values[shooting[0]], values[shooting[1]] = row[4], row[5]
        return _shooting_result(player.name, scope, values, shooting, when=when)
    for index, stat in enumerate(wanted):
        per_game_col, total_col, _ = PLAYER_STAT_COLUMNS[stat]
        values[per_game_col] = _rounded(row[4 + 2 * index])
        amount = row[5 + 2 * index]
        if total_col and amount is not None:
            values[total_col] = int(round(amount))
    return TemplateResult(
        data={"player": player.name, **scope, "stats": values},
        answer=_phrase_player_stat(player.name, f"career {span.kind}s", values, wanted, when=when),
    )


def _box_score_player_stat(con: duckdb.DuckDBPyConnection, player: Entity, span: _Span, narrowed: _Narrowed, wanted: list[str], shooting: tuple[str, str, str, str] | None) -> TemplateResult:
    """Averages over exactly the games a question narrowed to. The box-score
    column for each stat is the stat's own name (``points``, ``fouls``...), so
    PLAYER_STAT_COLUMNS' keys reach SQL here, never the slot text itself."""
    selects = ["COUNT(*)", "MIN(pgl.season)", "MAX(pgl.season)"]
    for stat in wanted:
        selects += [f"AVG(pgl.{stat})", f"SUM(pgl.{stat})"]
    if shooting:
        selects += [f"SUM(pgl.{shooting[0]})", f"SUM(pgl.{shooting[1]})"]
    where, params = narrowed.clauses()
    row = con.execute(f"SELECT {', '.join(selects)} {_PLAYER_GAMES} WHERE {where}", params).fetchone()
    filters = narrowed.filters()
    scope: dict[str, Any] = {
        "season": span.season,
        "span": "career" if span.career else None,
        "opponent": narrowed.opponent.name if narrowed.opponent else None,
        "venue": narrowed.venue,
        "without": narrowed.without.name if narrowed.without else None,
    }
    if row is None or not row[0]:
        message = _no_narrowed_games(con, player, span, narrowed)
        return TemplateResult(data={"player": player.name, **scope, "games": 0, "stats": {}}, answer=message)
    games, first, last = row[:3]
    when = span.during(first, last)
    notes = _box_score_notes(con, player, span, narrowed)
    values: dict[str, Any] = {"gamesPlayed": int(games)}
    if shooting:
        values[shooting[0]], values[shooting[1]] = row[3], row[4]
        result = _shooting_result(player.name, {**scope, "seasons": [first, last]}, values, shooting, when=when, games_note=filters)
    else:
        for index, stat in enumerate(wanted):
            per_game_col, total_col, _ = PLAYER_STAT_COLUMNS[stat]
            values[per_game_col] = _rounded(row[3 + 2 * index])
            if total_col and row[4 + 2 * index] is not None:
                values[total_col] = int(row[4 + 2 * index])
        answer = _phrase_player_stat(player.name, _period(first, span.season_type), values, wanted, games_note=filters, when=when)
        result = TemplateResult(data={"player": player.name, **scope, "seasons": [first, last], "stats": values}, answer=answer)
    if notes:
        result.answer = " ".join([result.answer, *notes])
    return result


def _shooting_result(name: str, scope: dict[str, Any], values: dict[str, Any], shooting: tuple[str, str, str, str], *, when: str, games_note: str = "") -> TemplateResult:
    """A percentage with the makes and attempts behind it - "out of how many?"
    is the first thing anybody asks of a percentage without them."""
    made_col, attempted_col, how, noun = shooting
    made, attempted, games = values.get(made_col), values.get(attempted_col), values.get("gamesPlayed")
    played = f" in {_count_games(games)}{games_note}" if games else games_note
    if attempted is None or made is None:
        answer = f"{name} has no {noun} on record{played} {when}."
        pct = None
    elif not attempted:
        answer = f"{name} attempted no {noun}{played} {when}."
        pct = None
    else:
        pct = 100.0 * made / attempted
        answer = f"{name} shot {pct:.1f}% {how} ({made:,} of {attempted:,}){played} {when}."
    return TemplateResult(data={"player": name, **scope, "stats": {**values, "pct": pct}}, answer=answer)


def _phrase_player_stat(name: str, period: str, values: dict[str, Any], wanted: list[str], *, games_note: str = "", when: str | None = None) -> str:
    games = values.get("gamesPlayed")
    parts = []
    for stat in wanted:
        per_game_col, _, label = PLAYER_STAT_COLUMNS[stat]
        per_game = values.get(per_game_col)
        if per_game is not None:
            parts.append(f"{_format_value(per_game)} {label}")
    if not parts:
        return f"{name} has no {period} numbers in the warehouse."
    body = ", ".join(parts[:-1]) + f" and {parts[-1]}" if len(parts) > 1 else parts[0]
    played = f" in {_count_games(games)}{games_note}" if games else games_note
    sentence = f"{name} averaged {body} per game{played} {when or f'in the {period}'}."
    # The season total goes in its own clause rather than inline, and only when
    # a single stat was asked for - inline it read as "33.5 points (2143 total)
    # per game", which says something false.
    if len(wanted) == 1:
        total_col = PLAYER_STAT_COLUMNS[wanted[0]][1]
        total = values.get(total_col) if total_col else None
        if total is not None:
            sentence += f" That is {total:,} in total."
    return sentence


DEFAULT_GAME_LOG_LIMIT = 10
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# games is home/away-oriented, not team-perspective: joining team_id to only
# home_team_id silently returns that team's HOME games, with no error.
# team_box_stats carries the team-perspective row (team_id, opponent_team_id,
# home_away); who WON lives only on games.winner_team_id. team_score /
# opponent_score are derived from home_away rather than read raw, since raw
# home_score/away_score needs a per-row guess that goes backwards sometimes.
#
# Joined on season as well as event_id: the phantom 1993 shares every event id
# with 1994, so a 1994 log keyed on event_id alone listed each game twice. The
# winner is returned raw rather than compared, because 134 games since 1994
# (the 1999 lockout season's, mostly) have none recorded, and "not the winner"
# is not the same fact as "lost".
_TEAM_GAMES_SQL = """
SELECT g.date,
       tbs.home_away,
       opp.display_name AS opponent,
       CASE WHEN tbs.home_away = 'home' THEN g.home_score ELSE g.away_score END AS team_score,
       CASE WHEN tbs.home_away = 'home' THEN g.away_score ELSE g.home_score END AS opponent_score,
       g.winner_team_id,
       tbs.team_id,
       CASE WHEN tbs.season_type = 3 THEN CAST(substr(g.date, 1, 4) AS INTEGER) ELSE tbs.season END AS season
FROM team_box_stats tbs
JOIN games g ON g.event_id = tbs.event_id AND g.season = tbs.season
JOIN teams opp ON opp.team_id = tbs.opponent_team_id
"""


def _as_int(value: Any) -> str:
    return str(int(value)) if isinstance(value, (int, float)) else str(value)


def _ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


# ---------------- team records, team stats, team rankings, team outlook ----------------

# Conference and division words. The warehouse holds no membership for either:
# no table maps a team to one, and standings carry only each team's record in
# its OWN conference's and division's games. So a team slot naming one cannot be
# answered, and is refused by name. Resolved as a team it would match nothing
# and fall through to an agent with no better source.
_CONFERENCE_WORDS = re.compile(r"\b(?:conferences?|divisions?|east(?:ern)?|west(?:ern)?|atlantic|central|southeast|northwest|pacific|southwest)\b", re.IGNORECASE)


def _conference_refusal(slots: dict[str, Any]) -> TemplateResult | None:
    """A refusal naming the real cause, if any team slot holds a conference or a
    division rather than a team."""
    listed: list[Any] = slots["teams"] if isinstance(slots.get("teams"), list) else []
    named = next((c for c in [slots.get("team"), slots.get("opponent"), *listed] if isinstance(c, str) and _CONFERENCE_WORDS.search(c)), None)
    if named is None:
        return None
    message = (
        f"The warehouse has no conference or division membership for any team, so nothing about {named!r} can be tallied from it. "
        "The only conference figure it holds is each team's record in its own conference's games."
    )
    return TemplateResult(data={"message": message, "unanswerable": named}, answer=message)


def _record_pct(value: float) -> str:
    """Basketball convention for a winning percentage: .646, not 0.646."""
    text = f"{value:.3f}"
    return text[1:] if text.startswith("0") else text


def _parse_record(text: Any) -> tuple[int, int] | None:
    """standings' "Home"/"Road" strings: '30-10' -> (30, 10)."""
    if not isinstance(text, str):
        return None
    match = re.fullmatch(r"\s*(\d+)-(\d+)\s*", text)
    return (int(match.group(1)), int(match.group(2))) if match else None


def _tally(wins: int, losses: int) -> str:
    return f"{wins:,}-{losses:,} ({_record_pct(wins / (wins + losses))})" if wins + losses else "0-0"


def _possessive(name: str) -> str:
    """ "the Knicks'" but "the Thunder's" - a team name is plural only sometimes."""
    return f"{name}'" if name.endswith("s") else f"{name}'s"


def _joined(items: list[str]) -> str:
    """ "a, b and c"."""
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + f" and {items[-1]}"


VENUE_WORDS = {"home": "at home", "away": "on the road"}


def team_record(ctx: TemplateContext, slots: dict[str, Any]) -> TemplateResult:
    """A team's win-loss record: for a season, at home or on the road, against
    one team, in a postseason, or across every season the warehouse holds.

    A season's record and its home/road split are read from standings, the
    authoritative source - its "Home" and "Road" strings agree with a tally of
    ``games`` for every team-season from 1994 to 2026 once each era's
    neutral-site rule is applied (through 2024 a neutral-site game counts for
    its designated home team; from 2025 it counts as neither). A record against
    one team or in a postseason has no standings column, and is tallied from
    ``games`` - see team_metrics.TEAM_GAMES_SQL for what that tally removes.

    Every answer names the span it covers, because "all-time" here is not the
    franchise's history: standings begin with 1987-88, and ``games`` holds every
    regular-season game only from 1993-94.

    .. versionchanged:: 2.1.0
       Honours ``venue``, ``opponent`` and ``span``, answers a postseason
       record from ``games`` instead of refusing it, and adds points for and
       against, games behind and the last ten games to a season's record.
    """
    con = ctx.con
    refused = _conference_refusal(slots)
    if refused is not None:
        return refused
    if slots.get("limit"):
        # "how did they do in their last 10 games?" is a game_log question -
        # standings only has the full-season record, and answering with it
        # under a "last 10" question is a silent substitution. game_log already
        # tallies the record over exactly the games it lists.
        raise TemplateUnsupported("a record over a limited set of games is a game_log question")

    team_text = slots.get("team")
    listed = [n for n in slots.get("teams") or [] if isinstance(n, str) and n.strip()] if isinstance(slots.get("teams"), list) else []
    if not (isinstance(team_text, str) and team_text.strip()) and listed:
        # "celtics vs bulls record" can land both teams in `teams`, which
        # scope_from_question leaves alone; the first is the subject.
        team_text, listed = listed[0], listed[1:]
    team = _resolved_team(con, team_text)
    if isinstance(team, TemplateResult):
        return team

    opponent: Entity | None = None
    opponent_text = slots.get("opponent")
    if isinstance(opponent_text, str) and opponent_text.strip():
        found = _resolved_team(con, opponent_text)
        if isinstance(found, TemplateResult):
            return found
        if found.id == team.id:
            raise TemplateUnsupported("team_record's opponent must differ from the team")
        opponent = found
    else:
        for text in listed:
            found = _resolved_team(con, text)
            if isinstance(found, TemplateResult):
                return found
            if found.id != team.id:
                opponent = found
                break

    season_type = slots.get("season_type") or 2
    venue = slots.get("venue") if slots.get("venue") in VENUE_WORDS else None
    career = slots.get("span") == "career"
    season = slots.get("season") if isinstance(slots.get("season"), int) else None
    if career and season is not None:
        # "all-time ... in 2020" is either a slip or a range this cannot read.
        raise TemplateUnsupported("a career span and a single season at once")

    if opponent is None and season_type == 2:
        if career:
            return _standings_career(con, team, venue)
        return _standings_season(con, team, season or current_season(), venue)
    return _games_record(con, team, opponent, None if career else (season or current_season()), season_type, venue)


def _standings_gap(con: duckdb.DuckDBPyConnection, team: Entity, seasons: list[tuple[int, int]]) -> str | None:
    """A note for seasons whose standings cover fewer games than the team's own
    season totals. ESPN's 2000 standings stop two games short for most teams
    (the Lakers finished 67-15 and read 67-13), and nothing in the row says so.
    `seasons` is (season, games in standings)."""
    if not seasons:
        return None
    totals = dict(
        con.execute(
            f"SELECT season, gamesPlayed FROM team_season_stats WHERE team_id = ? AND season_type = 2 AND season IN ({', '.join('?' for _ in seasons)})",
            [team.id, *(s for s, _ in seasons)],
        ).fetchall()
    )
    # Short only: that is the failure measured, and it is the one a reader
    # cannot see from the row.
    short = [(s, g, int(totals[s])) for s, g in seasons if s in totals and totals[s] and g < int(totals[s])]
    if not short:
        return None
    parts = _joined([f"{s} ({g} of {t} games)" for s, g, t in short])
    return f"Note: ESPN's standings do not cover the {_possessive(team.name)} whole season in {parts}, so this record is short by those games."


def _standings_season(con: duckdb.DuckDBPyConnection, team: Entity, season: int, venue: str | None) -> TemplateResult:
    row = con.execute(
        'SELECT wins, losses, winPercent, streak, playoffSeed, gamesBehind, "Home", "Road", "Last Ten Games", avgPointsFor, avgPointsAgainst, differential '
        "FROM standings WHERE team_id = ? AND season = ?",
        [team.id, season],
    ).fetchone()
    if row is None:
        return TemplateResult(
            data={"team": team.name, "season": season},
            answer=f"There are no {season} standings for the {team.name} in the warehouse.",
        )
    wins, losses, win_pct, streak, seed, behind, home_text, road_text, last_ten, points_for, points_against, differential = row
    # standings stores these as DOUBLE; reporting a 53-29 record as "53.0-29.0"
    # is the kind of detail that makes a correct answer look untrustworthy.
    w, lost = int(wins), int(losses)
    home, road = _parse_record(home_text), _parse_record(road_text)
    # '0-0' is how standings say "no split", every season before 1993-94.
    split = (home, road) if home and road and sum(home) + sum(road) > 0 else None
    # Neutral-site games count as neither home nor away from 2025 on, so the
    # halves can sum to less than the whole - and a reader adding them up
    # deserves to know why.
    neutral = w + lost - sum(split[0]) - sum(split[1]) if split else 0
    neutral_note = f" ({neutral} neutral-site game{'s' if neutral != 1 else ''} count{'s' if neutral == 1 else ''} as neither home nor away)" if neutral > 0 else ""
    data: dict[str, Any] = {"team": team.name, "season": season, "wins": w, "losses": lost, "win_pct": win_pct}
    gap = _standings_gap(con, team, [(season, w + lost)])

    if venue is not None:
        if split is None:
            message = (
                f"ESPN's {season} standings carry no home/road split for the {team.name} (it reads 0-0 before 1993-94), and the warehouse has no full game list for that season to tally one from."
            )
            return TemplateResult(data={**data, "message": message}, answer=message)
        vw, vl = split[0] if venue == "home" else split[1]
        answer = f"The {team.name} were {_tally(vw, vl)} {VENUE_WORDS[venue]} in the {season} regular season, {w}-{lost} overall{neutral_note}."
        # `wins`/`losses`/`win_pct` are what the web page draws as the record
        # card, so they carry the record that was asked for. Leaving the
        # season's there would print 53-29 in large type under a question
        # about the home record - the substitution this path exists to stop.
        data.update(
            {
                "wins": vw,
                "losses": vl,
                "win_pct": vw / (vw + vl) if vw + vl else 0.0,
                "season_wins": w,
                "season_losses": lost,
                "season_win_pct": win_pct,
                "venue": venue,
                "venue_wins": vw,
                "venue_losses": vl,
                "neutral_site_games": neutral,
            }
        )
        return TemplateResult(data=data, answer=f"{answer} {gap}" if gap else answer)

    answer = f"The {team.name} were {w}-{lost} in the {season} regular season"
    if win_pct is not None:
        answer += f" ({_record_pct(win_pct)})"
    extras = []
    if seed:
        extras.append(f"{_ordinal(int(seed))} seed")
    if streak:
        extras.append(f"{'won' if streak > 0 else 'lost'} {abs(int(streak))} straight")
    answer += f", {', '.join(extras)}." if extras else "."
    detail = []
    if split:
        detail.append(f"Home {'-'.join(map(str, split[0]))}, road {'-'.join(map(str, split[1]))}{neutral_note}")
    if isinstance(last_ten, str) and last_ten.strip():
        detail.append(f"last 10: {last_ten}")
    if behind:
        detail.append(f"{_format_value(float(behind))} game{'s' if behind != 1 else ''} back")
    if detail:
        answer += "\n  " + "; ".join(detail) + "."
    if points_for is not None and points_against is not None:
        answer += f"\n  {points_for:.1f} points per game, {points_against:.1f} allowed ({(differential if differential is not None else points_for - points_against):+.1f})."
    data.update(
        {
            "home": split[0] if split else None,
            "road": split[1] if split else None,
            "last_ten": last_ten,
            "games_behind": behind,
            "points_for": points_for,
            "points_against": points_against,
        }
    )
    return TemplateResult(data=data, answer=f"{answer}\n  {gap}" if gap else answer)


def _standings_career(con: duckdb.DuckDBPyConnection, team: Entity, venue: str | None) -> TemplateResult:
    rows = con.execute(
        'SELECT season, wins, losses, "Home", "Road" FROM standings WHERE team_id = ? AND wins + losses > 0 ORDER BY season',
        [team.id],
    ).fetchall()
    if not rows:
        return TemplateResult(data={"team": team.name}, answer=f"The warehouse has no standings at all for the {team.name}.")
    if venue is not None:
        halves = []
        for season, wins, losses, home_text, road_text in rows:
            home, road = _parse_record(home_text), _parse_record(road_text)
            if home and road and sum(home) + sum(road) > 0:
                halves.append((season, int(wins) + int(losses), home if venue == "home" else road, sum(home) + sum(road)))
        if not halves:
            message = f"ESPN's standings carry no home/road split for the {team.name} in any season the warehouse holds."
            return TemplateResult(data={"team": team.name, "message": message}, answer=message)
        vw = sum(h[2][0] for h in halves)
        vl = sum(h[2][1] for h in halves)
        neutral = sum(h[1] - h[3] for h in halves)
        first, last = halves[0][0], halves[-1][0]
        answer = (
            f"The {team.name} are {_tally(vw, vl)} {VENUE_WORDS[venue]} across the {len(halves)} regular seasons from {_season_name(first)} through {_season_name(last)}"
            + (" - ESPN's standings carry no home/road split before 1993-94" if first == FIRST_FULL_REGULAR_SEASON else "")
            + "."
        )
        if neutral > 0:
            answer += f" {neutral} neutral-site game{'s' if neutral != 1 else ''} count{'s' if neutral == 1 else ''} as neither."
        data: dict[str, Any] = {
            "team": team.name,
            "venue": venue,
            "wins": vw,
            "losses": vl,
            "win_pct": vw / (vw + vl) if vw + vl else 0.0,
            "first_season": first,
            "last_season": last,
            "seasons": len(halves),
        }
        gap = _standings_gap(con, team, [(h[0], h[1]) for h in halves])
        return TemplateResult(data=data, answer=f"{answer} {gap}" if gap else answer)

    wins = sum(int(r[1]) for r in rows)
    losses = sum(int(r[2]) for r in rows)
    first, last = rows[0][0], rows[-1][0]
    # The start is the warehouse's, not the franchise's, and saying which is
    # the whole difference between an all-time record and a partial one.
    start = (
        "the warehouse's standings begin there, so this is not the franchise's whole history"
        if first == min(r[0] for r in con.execute("SELECT MIN(season) FROM standings").fetchall())
        else "the first season the warehouse holds for them"
    )
    answer = f"The {team.name} are {_tally(wins, losses)} across the {len(rows)} regular seasons from {_season_name(first)} through {_season_name(last)} - {start}."
    gap = _standings_gap(con, team, [(int(r[0]), int(r[1]) + int(r[2])) for r in rows])
    return TemplateResult(
        data={
            "team": team.name,
            "wins": wins,
            "losses": losses,
            "win_pct": wins / (wins + losses) if wins + losses else 0.0,
            "first_season": first,
            "last_season": last,
            "seasons": len(rows),
        },
        answer=f"{answer} {gap}" if gap else answer,
    )


def _game_list_gaps(con: duckdb.DuckDBPyConnection, team: Entity, season_type: int, season: int | None) -> str | None:
    """Seasons where the games tallied for a team do not number its games in
    team_season_stats - the check that makes a tally from ``games`` safe to
    state. Measured: the 2000 and 2001 postseasons hold 15 of the Lakers' 23
    and 10 of their 16 games, and 1995 holds a Miami playoff game in a season
    Miami did not make the playoffs. Only seasons from 1994, where the totals
    exist, can be checked."""
    scope, params = games_scope(season_type, season)
    by_season = "year(eastern_date)" if season_type == 3 else "season"
    season_filter = "" if season is None else "AND ts.season = ?"
    rows = con.execute(
        f"""{TEAM_GAMES_SQL},
tallied AS (SELECT {by_season} AS season, count(*) AS games FROM team_games WHERE {scope} AND team_id = ? GROUP BY 1),
totals AS (SELECT ts.season, ts.gamesPlayed AS games FROM team_season_stats ts WHERE ts.team_id = ? AND ts.season_type = ? {season_filter})
SELECT coalesce(l.season, t.season) AS season, coalesce(l.games, 0), coalesce(t.games, 0)
FROM tallied l FULL OUTER JOIN totals t ON t.season = l.season
WHERE coalesce(l.season, t.season) >= ? AND coalesce(l.games, 0) <> coalesce(t.games, 0)
ORDER BY 1""",
        [*params, team.id, team.id, season_type, *([season] if season is not None else []), FIRST_FULL_REGULAR_SEASON],
    ).fetchall()
    if not rows:
        return None
    parts = _joined([f"{s} ({int(listed)} listed, {int(played)} played)" for s, listed, played in rows])
    kind = "postseason" if season_type == 3 else "regular-season"
    return f"Note: ESPN's game list and the {_possessive(team.name)} season totals disagree on how many {kind} games they played in {parts}, so this tally is off by those games."


def _no_team_games(con: duckdb.DuckDBPyConnection, team: Entity, opponent: Entity | None, season: int | None, season_type: int) -> str:
    """Why a tally found nothing. Three different facts, and three sentences:
    the warehouse has no games that season at all (it holds no 1988
    postseason), the team played none, or the two teams did not meet."""
    kind = "postseason" if season_type == 3 else "regular-season"
    if season is None:
        if opponent is None:
            return f"The warehouse holds no {kind} games for the {team.name}."
        return f"The warehouse holds no {kind} games between the {team.name} and the {opponent.name}."
    scope, params = games_scope(season_type, season)
    period = _period(season, season_type)
    counts = con.execute(f"{TEAM_GAMES_SQL} SELECT count(*), count(*) FILTER (WHERE team_id = ?) FROM team_games WHERE {scope}", [team.id, *params]).fetchone()
    league, own = counts if counts else (0, 0)
    if not league:
        return f"The warehouse holds no {period} games for any team."
    if opponent is not None and own:
        return f"The {team.name} and the {opponent.name} did not meet in the {period}."
    return f"The {team.name} played no games in the {period}."


def _games_record(con: duckdb.DuckDBPyConnection, team: Entity, opponent: Entity | None, season: int | None, season_type: int, venue: str | None) -> TemplateResult:
    """A record tallied from ``games``: against one team, or in a postseason,
    for one season or (``season`` None) every season it holds."""
    if season is not None:
        # _sources_for sends this path through the `games` floor, but a record
        # against a team named only in `teams` reaches it with the standings'
        # floor instead - so the floor is checked where the table is read.
        refused = unavailable(("games",), season, season_type)
        if refused is not None:
            return TemplateResult(data={"team": team.name, "season": season, "message": refused}, answer=refused)
    scope, params = games_scope(season_type, season)
    where = [scope, "tg.team_id = ?"]
    params = [*params, team.id]
    if opponent is not None:
        where.append("tg.opponent_id = ?")
        params.append(opponent.id)
    rows = con.execute(
        f"{TEAM_GAMES_SQL} SELECT tg.eastern_date, tg.side, tg.neutral, tg.team_score, tg.opponent_score, tg.won, o.display_name "
        f"FROM team_games tg JOIN teams o ON o.team_id = tg.opponent_id WHERE {' AND '.join(where)} ORDER BY tg.eastern_date",
        params,
    ).fetchall()
    games = [{"date": str(r[0]), "venue": "neutral" if r[2] else r[1], "team_score": r[3], "opponent_score": r[4], "won": bool(r[5]), "opponent": r[6]} for r in rows]
    shown = [g for g in games if venue is None or g["venue"] == venue]
    wins = sum(1 for g in shown if g["won"])
    losses = len(shown) - wins
    kind = "postseason" if season_type == 3 else "regular season"
    if season is not None:
        span = f"the {_period(season, season_type)}"
    elif season_type == 3:
        span = "every postseason from 1989 through the latest - the warehouse's game list starts with the 1989 playoffs"
    else:
        span = f"the regular seasons from {_season_name(FIRST_FULL_REGULAR_SEASON)} on - the first the warehouse holds every game of"
    against = f" against the {opponent.name}" if opponent else ""
    where_played = f" {VENUE_WORDS[venue]}" if venue else ""
    data: dict[str, Any] = {
        "team": team.name,
        "opponent": opponent.name if opponent else None,
        "season": season,
        "season_type": kind,
        "venue": venue,
        "wins": wins,
        "losses": losses,
        # Read by the web page's record card, like the standings paths' own.
        "win_pct": wins / (wins + losses) if wins + losses else 0.0,
    }

    if not games:
        return TemplateResult(data={**data, "games": []}, answer=_no_team_games(con, team, opponent, season, season_type))

    verb = "went" if season is not None else "are"
    answer = f"The {team.name} {verb} {_tally(wins, losses)}{where_played}{against} in {span}."
    if venue is None:
        home = [g for g in games if g["venue"] == "home"]
        away = [g for g in games if g["venue"] == "away"]
        split = f"Home {sum(g['won'] for g in home)}-{sum(not g['won'] for g in home)}, away {sum(g['won'] for g in away)}-{sum(not g['won'] for g in away)}"
        neutral = len(games) - len(home) - len(away)
        if neutral:
            split += f", neutral site {sum(g['won'] for g in games if g['venue'] == 'neutral')}-{sum(not g['won'] for g in games if g['venue'] == 'neutral')}"
        answer += f"\n  {split}."
    elif len(shown) < len(games) and any(g["venue"] == "neutral" for g in games):
        answer += "\n  Neutral-site games count as neither home nor away."
    # A season's meetings are few enough to list, and the list is what "vs"
    # questions usually want next.
    if opponent is not None and season is not None and shown:
        answer += "\n" + "\n".join(
            f"  {g['date']}  {'W' if g['won'] else 'L'} {g['team_score']}-{g['opponent_score']}  {'at' if g['venue'] == 'away' else 'vs'} {g['opponent']}"
            + (" (neutral site)" if g["venue"] == "neutral" else "")
            for g in shown
        )
    if opponent is not None and season_type == 2:
        # The NBA Cup final is a regular-season game that counts in no
        # standings, so it is not in the record above - but it is a meeting,
        # and leaving it out without a word would read as a missing game.
        cup_scope = "season_type = 2 AND cup_final" + ("" if season is None else " AND season = ?")
        cup = con.execute(
            f"{TEAM_GAMES_SQL} SELECT eastern_date, team_score, opponent_score, won FROM team_games WHERE {cup_scope} AND team_id = ? AND opponent_id = ? ORDER BY 1",
            [*([season] if season is not None else []), team.id, opponent.id],
        ).fetchall()
        for date, own, theirs, won in cup:
            answer += f"\n  They also met in the NBA Cup final on {date}, which counts in no standings: {'won' if won else 'lost'} {own}-{theirs}."
        data["cup_final"] = [{"date": str(d), "won": bool(won), "team_score": own, "opponent_score": theirs} for d, own, theirs, won in cup]
    gap = _game_list_gaps(con, team, season_type, season)
    if gap:
        answer += f"\n  {gap}"
    data["games"] = shown
    return TemplateResult(data=data, answer=answer)


# ---- one team's numbers, and every team ranked ----

DEFAULT_TEAM_LEADERBOARD_LIMIT = 10

RATING_NOTE = "Ratings and pace count possessions as FGA - OREB + TOV + 0.44 x FTA."


def _metric_cell(metric: TeamMetric, value: float) -> str:
    return f"{value:.1f}%" if metric.percent else f"{value:.1f}"


def _uses_possessions(keys: list[str]) -> bool:
    return any(k in ("offensive_rating", "defensive_rating", "net_rating", "pace") for k in keys)


def _first_season_refusal(metric: TeamMetric, season: int) -> TemplateResult | None:
    if season >= metric.first_season:
        return None
    message = f"{metric.label.capitalize()} can't be given for {season}: {metric.first_season_reason}."
    return TemplateResult(data={"message": message, "season": season}, answer=message)


def _incomplete_opponents(metric: TeamMetric, period: str, lines: list[TeamLine], subject: str | None = None) -> str:
    """Why an opponent-based metric has no value: the games behind the points
    allowed do not number the games behind everything else. Names the team
    asked about when it is one of the short ones."""
    short = [line for line in lines if line.values.get(_metric_key(metric)) is None]
    example = next((line for line in short if line.team == subject), short[0])
    others = len(short) - 1
    return (
        f"{metric.label.capitalize()} can't be given for the {period}: ESPN's game list holds "
        f"{example.listed_games or 0} of the {_possessive(example.team)} {example.games} games"
        + (f", and is short for {others} other team{'s' if others != 1 else ''}" if others else "")
        + " - points allowed over fewer games than everything else would make the figure wrong without looking wrong."
    )


def _metric_key(metric: TeamMetric) -> str:
    return next(key for key, candidate in TEAM_METRICS.items() if candidate is metric)


def team_stat(ctx: TemplateContext, slots: dict[str, Any]) -> TemplateResult:
    """One team's season numbers, each with its rank in the league.

    With a ``stat`` it answers that one metric; with none, a compact line -
    points for and against, pace, offensive/defensive/net rating, 3-point
    percentage, rebounds, assists and turnovers. The stat is mapped through
    team_metrics.STAT_ALIASES, an explicit whitelist, and a word it does not
    know is refused rather than matched to something close.

    .. versionadded:: 2.1.0
    """
    con = ctx.con
    refused = _conference_refusal(slots)
    if refused is not None:
        return refused
    team = _resolved_team(con, slots.get("team"))
    if isinstance(team, TemplateResult):
        return team
    stat = slots.get("stat")
    key = resolve_team_metric(stat)
    if key is None and isinstance(stat, str) and stat.strip():
        raise TemplateUnsupported(f"no team metric for stat {stat!r}")
    season = slots.get("season") or current_season()
    season_type = slots.get("season_type") or 2
    period = _period(season, season_type)

    if key is not None and TEAM_METRICS[key].expression is None:
        return _team_record_rank(con, team, key, season, season_type)
    wanted = [key] if key else list(DEFAULT_TEAM_LINE)
    if key is not None:
        refusal = _first_season_refusal(TEAM_METRICS[key], season)
        if refusal is not None:
            return refusal

    lines = season_table(con, season, season_type)
    mine = next((line for line in lines if line.team == team.name), None)
    if mine is None:
        # Which fact is missing decides the sentence: a team that did not reach
        # the postseason is not a team the warehouse lacks numbers for.
        if season_type == 3 and any(line.team == team.name for line in season_table(con, season, 2)):
            answer = f"The {team.name} did not play in the {period}."
        else:
            answer = f"The warehouse has no {period} team stats for the {team.name}."
        return TemplateResult(data={"team": team.name, "season": season, "stats": {}}, answer=answer)

    stats: dict[str, dict[str, Any]] = {}
    for name in wanted:
        metric = TEAM_METRICS[name]
        value = mine.values.get(name)
        complete = all(line.values.get(name) is not None for line in lines)
        rank = None
        if value is not None and complete:
            rank = next(r for r, t, _ in ranked({line.team: line.values[name] or 0.0 for line in lines}, descending_for(metric, "best")) if t == team.name)
        stats[metric.label] = {"value": value, "rank": rank, "of": len(lines)}

    if key is not None:
        metric = TEAM_METRICS[key]
        entry = stats[metric.label]
        if entry["value"] is None:
            answer = _incomplete_opponents(metric, period, lines, team.name)
            return TemplateResult(data={"team": team.name, "season": season, "stats": stats, "message": answer}, answer=answer)
        where = ""
        if entry["rank"] is not None:
            order = "best" if metric.lower_is_better is not None else "highest"
            where = f", {_ordinal(entry['rank'])}-{order} of {entry['of']} teams"
        answer = f"The {_possessive(team.name)} {metric.label} was {_metric_cell(metric, entry['value'])} in the {period} ({mine.games} games){where}."
        if _uses_possessions([key]):
            answer += f" {RATING_NOTE}"
        return TemplateResult(data={"team": team.name, "season": season, "games": mine.games, "stats": stats}, answer=answer)

    label_width = max(len(label) for label in stats)
    cells = {label: "-" if e["value"] is None else _metric_cell(TEAM_METRICS[name], e["value"]) for (label, e), name in zip(stats.items(), wanted, strict=True)}
    value_width = max(5, *(len(c) for c in cells.values()))
    lines_out = [f"{team.name}, {period} ({mine.games} games):", f"{' ' * label_width}  {'value'.rjust(value_width)}  rank"]
    for label, entry in stats.items():
        rank_cell = f"{_ordinal(entry['rank'])} of {entry['of']}" if entry["rank"] is not None else "-"
        lines_out.append(f"{label.ljust(label_width)}  {cells[label].rjust(value_width)}  {rank_cell}")
    notes = ["Rank 1st is the best in the league (for pace, the fastest).", RATING_NOTE]
    if any(e["value"] is None for e in stats.values()):
        notes.append("A '-' needs points allowed, and ESPN's game list does not hold all of this team's games that season.")
    elif any(e["rank"] is None for e in stats.values()):
        notes.append("A rank is left out where ESPN's game list is short for other teams that season.")
    return TemplateResult(
        data={"team": team.name, "season": season, "games": mine.games, "stats": stats},
        answer="\n".join([*lines_out, *notes]),
    )


def _team_record_rank(con: duckdb.DuckDBPyConnection, team: Entity, key: str, season: int, season_type: int) -> TemplateResult:
    """team_stat for a record: the team's record and where it ranks."""
    records = record_table(con, season, season_type)
    period = _period(season, season_type)
    mine = next((r for r in records if r.team == team.name), None)
    if mine is None:
        answer = f"The {team.name} have no {period} record in the warehouse."
        return TemplateResult(data={"team": team.name, "season": season}, answer=answer)
    order = ranked({r.team: r.win_pct for r in records}, True)
    rank = next(r for r, t, _ in order if t == team.name)
    answer = f"The {team.name} were {_tally(mine.wins, mine.losses)} in the {period}, the {_ordinal(rank)}-best record of {len(records)} teams."
    return TemplateResult(data={"team": team.name, "season": season, "wins": mine.wins, "losses": mine.losses, "rank": rank, "of": len(records)}, answer=answer)


def _venue_records(con: duckdb.DuckDBPyConnection, season: int, season_type: int, venue: str) -> list[TeamRecord] | str:
    """Every team's home or road record: standings' own strings for a regular
    season, a tally of ``games`` for a postseason. A string explains why there
    is none."""
    if season_type == 2:
        column = '"Home"' if venue == "home" else '"Road"'
        rows = con.execute(f"SELECT t.display_name, s.{column} FROM standings s JOIN teams t ON t.team_id = s.team_id WHERE s.season = ? ORDER BY 1", [season]).fetchall()
        parsed = [(name, _parse_record(text)) for name, text in rows]
        records = [TeamRecord(team=name, wins=r[0], losses=r[1]) for name, r in parsed if r and sum(r) > 0]
        if rows and not records:
            return f"ESPN's {season} standings carry no home/road split (it reads 0-0 for every team before 1993-94)."
        return records
    scope, params = games_scope(season_type, season)
    rows = con.execute(
        f"{TEAM_GAMES_SQL} SELECT t.display_name, sum(won::INT), sum((NOT won)::INT) FROM team_games tg JOIN teams t ON t.team_id = tg.team_id "
        f"WHERE {scope} AND tg.side = ? AND NOT tg.neutral GROUP BY 1 ORDER BY 1",
        [*params, venue],
    ).fetchall()
    return [TeamRecord(team=name, wins=int(w), losses=int(lost)) for name, w, lost in rows]


def team_leaderboard(ctx: TemplateContext, slots: dict[str, Any]) -> TemplateResult:
    """Every team ranked by one metric from team_metrics.TEAM_METRICS - "which
    team scores the most points per game", "lowest defensive rating", "best
    record".

    The ``rank`` slot picks the end: "most" and "fewest" are the raw ends of the
    scale, "best" and "worst" depend on the metric (the fewest turnovers are the
    best), and no rank means best. The answer says which end it lists first,
    because a list of the fastest teams under a question about the slowest
    would otherwise look perfectly right.

    Before this, "which team scores the most points per game" was answered with
    the players' scoring leaders.

    .. versionadded:: 2.1.0
    """
    con = ctx.con
    refused = _conference_refusal(slots)
    if refused is not None:
        return refused
    stat = slots.get("stat")
    key = resolve_team_metric(stat)
    if key is None:
        raise TemplateUnsupported(f"no team metric for stat {stat!r}")
    metric = TEAM_METRICS[key]
    season = slots.get("season") or current_season()
    season_type = slots.get("season_type") or 2
    period = _period(season, season_type)
    rank_word = slots.get("rank") if slots.get("rank") in ("most", "fewest", "best", "worst") else None
    descending = descending_for(metric, rank_word)
    limit = _clamp_limit(slots.get("limit"), default=DEFAULT_TEAM_LEADERBOARD_LIMIT)
    venue = slots.get("venue") if slots.get("venue") in VENUE_WORDS else None

    named: Entity | None = None
    if isinstance(slots.get("team"), str) and slots["team"].strip():
        found = _resolved_team(con, slots["team"])
        if isinstance(found, TemplateResult):
            return found
        named = found

    display: dict[str, str] = {}
    if metric.expression is None:
        records: list[TeamRecord] | str = _venue_records(con, season, season_type, venue) if venue else record_table(con, season, season_type)
        if isinstance(records, str):
            return TemplateResult(data={"message": records, "season": season}, answer=records)
        values = {r.team: (r.win_pct if key == "record" else 1 - r.win_pct) for r in records}
        display = {r.team: _tally(r.wins, r.losses) for r in records}
    else:
        if venue is not None:
            # Team season stats have no home/road split; team_box_stats does,
            # and the agent can reach it.
            raise TemplateUnsupported(f"team season stats have no {venue} split for {metric.label}")
        refusal = _first_season_refusal(metric, season)
        if refusal is not None:
            return refusal
        lines = season_table(con, season, season_type)
        if lines and any(line.values.get(key) is None for line in lines):
            message = _incomplete_opponents(metric, period, lines)
            return TemplateResult(data={"message": message, "season": season}, answer=message)
        values = {line.team: line.values[key] or 0.0 for line in lines}
        display = {team: _metric_cell(metric, value) for team, value in values.items()}

    title = f"{metric.label.capitalize()}{f' {VENUE_WORDS[venue]}' if venue else ''}, {period}"
    if not values:
        answer = f"The warehouse has no {period} numbers to rank teams by {metric.label}."
        return TemplateResult(data={"question_shape": title, "season": season, "teams": []}, answer=answer)

    order = ranked(values, descending)
    if metric.lower_is_better is None or rank_word in ("most", "fewest"):
        end = "highest first" if descending else "lowest first"
    else:
        best_first = descending == (metric.lower_is_better is False)
        end = ("best first" if best_first else "worst first") + (" (highest)" if descending else " (lowest)")
    if key in ("record", "losses"):
        end = "best record first" if (key == "record") == descending else "worst record first"
    shown = order[:limit]
    extra = [row for row in order[limit:] if named is not None and row[1] == named.name]
    name_width = max(len(team) for _, team, _ in [*shown, *extra])
    value_width = max(len(display[team]) for _, team, _ in [*shown, *extra])
    rows_out = [f"{rank:>2}  {team.ljust(name_width)}  {display[team].rjust(value_width)}" for rank, team, _ in shown]
    if extra:
        rows_out += ["    ...", *(f"{rank:>2}  {team.ljust(name_width)}  {display[team].rjust(value_width)}" for rank, team, _ in extra)]
    lines_out = [f"{title} - {end}, of {len(order)} teams:", *rows_out]
    if _uses_possessions([key]):
        lines_out.append(RATING_NOTE)
    teams = [{"rank": rank, "team": team, "value": value, "display": display[team]} for rank, team, value in [*shown, *extra]]
    return TemplateResult(data={"question_shape": title, "season": season, "order": end, "teams": teams}, answer="\n".join(lines_out))


# ---- ESPN's Basketball Power Index ----

# ESPN's season types, as the power index uses them. 5 is not in the rest of
# the warehouse: its snapshots are dated between the regular season's end and
# the first playoff game (2026-04-18, 2025-04-19), i.e. the play-in.
BPI_SNAPSHOT_NAMES = {1: "preseason", 2: "regular-season", 3: "postseason", 5: "play-in"}

# (label, column) for the chances a snapshot carries. `probmakeconfchamp` is
# reaching the conference finals, not winning them: in the 2026 postseason
# snapshot every conference finalist reads 100 and every other team 0.
_BPI_CHANCES = (("playoffs", "probmakeplayoffs"), ("conference finals", "probmakeconfchamp"), ("Finals", "probmaketitlegame"), ("title", "probwintitle"))


def team_outlook(ctx: TemplateContext, slots: dict[str, Any]) -> TemplateResult:
    """A team's ESPN Basketball Power Index: its rating and where it sits,
    projected record, playoff and title chances, and strength of schedule.

    The power index is sparse, and the answer says so every time. A season
    holds at most a few snapshots, each covering part of the league - 2026 has
    a play-in snapshot of 13 teams and a postseason one of 12, and no regular-
    season snapshot at all - so every answer names the snapshot, its date and
    how many teams it holds, and a team missing from it is told which snapshots
    exist rather than that there is "no data". A regular-season question reads
    the latest pre-playoff snapshot holding the team; a postseason one reads
    the postseason snapshot.

    Where a team stands is counted among the teams in the snapshot, from their
    ratings. ESPN's own rank columns are ranks only from 2022: before it they
    hold values like 83, 2,625 and 26,058.

    .. versionadded:: 2.1.0
    """
    con = ctx.con
    refused = _conference_refusal(slots)
    if refused is not None:
        return refused
    team = _resolved_team(con, slots.get("team"))
    if isinstance(team, TemplateResult):
        return team
    season = slots.get("season") or current_season()
    postseason = (slots.get("season_type") or 2) == 3

    snapshots = con.execute(
        "SELECT season_type, max(last_updated), count(DISTINCT team_id), bool_or(team_id = ?) FROM team_power_index WHERE season = ? GROUP BY 1 ORDER BY 2",
        [team.id, season],
    ).fetchall()

    def _describe(kind: int, updated: Any, teams: int) -> str:
        return f"a {BPI_SNAPSHOT_NAMES.get(kind, f'type-{kind}')} snapshot ({str(updated)[:10]}, {teams} team{'s' if teams != 1 else ''})"

    listing = [_describe(k, u, n) for k, u, n, _ in snapshots]
    if not snapshots:
        message = f"ESPN's power index has no {season} snapshot in the warehouse."
        return TemplateResult(data={"team": team.name, "season": season, "message": message}, answer=message)
    pre = [s for s in snapshots if s[0] != 3 and s[3]]
    post = [s for s in snapshots if s[0] == 3 and s[3]]
    # The latest snapshot of the kind asked for that holds this team. A
    # regular-season question falls back to the postseason snapshot only when
    # no earlier one holds the team, and the answer says it did.
    candidates = post if postseason else (pre or post)
    chosen = candidates[-1] if candidates else None
    if chosen is None:
        # Which snapshot is missing, or which one the team is missing from, is
        # the whole answer - "no data" would send the reader to the wrong place.
        have = _joined(listing)
        if postseason and not any(s[0] == 3 for s in snapshots):
            gap = "and no postseason snapshot"
        elif postseason:
            gap = f"and the {team.name} are not in its postseason snapshot"
        else:
            gap = f"and the {team.name} are {'not in it' if len(listing) == 1 else 'in neither' if len(listing) == 2 else 'in none of them'}"
        message = f"ESPN's power index for {season} has {have}, {gap}."
        holding = [d for (*_, has), d in zip(snapshots, listing, strict=True) if has]
        if holding:
            message += f" The {team.name} are only in {_joined(holding)} - ask about the regular season to see it."
        return TemplateResult(data={"team": team.name, "season": season, "snapshots": listing, "message": message}, answer=message)

    kind = chosen[0]
    row = con.execute(
        "SELECT last_updated, bpi, bpioffense, bpidefense, numwins, numlosses, projectedw, projectedl, probmakeplayoffs, probmakeconfchamp, probmaketitlegame, probwintitle, "
        "sosoverall, sosoverallrank, (SELECT count(*) FROM team_power_index o WHERE o.season = p.season AND o.season_type = p.season_type AND o.bpi > p.bpi) "
        "FROM team_power_index p WHERE season = ? AND season_type = ? AND team_id = ? ORDER BY last_updated DESC LIMIT 1",
        [season, kind, team.id],
    ).fetchone()
    assert row is not None  # the snapshot was chosen because it holds this team
    updated, bpi, offense, defense, wins, losses, proj_w, proj_l, *chances_and_sos = row
    chances = dict(zip([c for _, c in _BPI_CHANCES], chances_and_sos[:4], strict=True))
    sos, sos_rank, higher = chances_and_sos[4], chances_and_sos[5], chances_and_sos[6]
    name = BPI_SNAPSHOT_NAMES.get(kind, f"type-{kind}")
    lines_out = [f"ESPN's power index for the {team.name}, {season} {name} snapshot (updated {str(updated)[:10]}, {chosen[2]} teams):"]
    if not postseason and kind == 3:
        lines_out.append("  (No pre-playoff snapshot for that season holds them, so this is the postseason one.)")
    if str(updated)[:4] > str(season):
        # Every 2017-2020 snapshot is stamped 2019 or 2020 - after the season
        # it describes had ended - and the 2017 preseason and regular-season
        # snapshots carry identical ratings under different records.
        lines_out.append(f"  (ESPN stamps this snapshot {str(updated)[:10]}, after the {season} season ended, so it may not reflect any one moment of it.)")
    if bpi is not None:
        detail = f" (offense {offense:+.1f}, defense {defense:+.1f})" if offense is not None and defense is not None else ""
        lines_out.append(f"  BPI {bpi:+.1f}{detail}, {_ordinal(int(higher) + 1)} of the {chosen[2]} teams in the snapshot")
    if wins is not None and losses is not None:
        played = int(wins) + int(losses)
        regular = (round(proj_w), round(proj_l)) if proj_w is not None and proj_l is not None else None
        if kind == 3:
            # In a postseason snapshot the "projection" is the finished regular
            # season, and the record adds the playoff games to it - so a team
            # whose two agree played none (the 2026 Hornets, out in the play-in).
            if regular and played > sum(regular):
                lines_out.append(f"  record {int(wins)}-{int(losses)} including the playoffs; {regular[0]}-{regular[1]} in the regular season")
            else:
                lines_out.append(f"  record {int(wins)}-{int(losses)}, no playoff games")
        else:
            projection = f", projected {regular[0]}-{regular[1]}" if regular else ""
            lines_out.append(f"  record {int(wins)}-{int(losses)}{projection}" if played else f"  no games played yet{projection}")
    odds = [f"{label} {chances[column]:.1f}%" for label, column in _BPI_CHANCES if chances[column] is not None]
    if odds:
        lines_out.append("  chances: " + ", ".join(odds))
    # ESPN's schedule-strength rank is a league-wide rank only from 2022; the
    # values before it (7,909 to 59,238) are not ranks.
    if sos is not None and 0 < sos < 1:
        rank_note = f", {_ordinal(int(sos_rank))} hardest in the league" if sos_rank is not None and 1 <= sos_rank <= 30 else ""
        lines_out.append(f"  strength of schedule {_record_pct(sos)}{rank_note}")
    others = [d for (k, *_), d in zip(snapshots, listing, strict=True) if k != kind]
    if others:
        lines_out.append(f"  ESPN's power index for {season} also has {_joined(others)}.")
    data = {
        "team": team.name,
        "season": season,
        "snapshot": name,
        "updated": str(updated)[:10],
        "teams_in_snapshot": chosen[2],
        "bpi": bpi,
        "bpi_offense": offense,
        "bpi_defense": defense,
        "position": int(higher) + 1,
        "wins": wins,
        "losses": losses,
        "projected_wins": proj_w,
        "projected_losses": proj_l,
        "chances": {label: chances[column] for label, column in _BPI_CHANCES},
        "strength_of_schedule": sos,
    }
    return TemplateResult(data=data, answer="\n".join(lines_out))


# A player's log columns, by header -> player_game_log column. The four in
# _LOG_BASE are always shown, and a named stat adds its own: "luka ft log" and
# "kyle kuzma last 7 games fgm" are real queries, and the log used to show
# points, rebounds and assists whatever was asked.
_LOG_COLUMNS: dict[str, str] = {
    "MIN": "minutes",
    "PTS": "points",
    "REB": "rebounds",
    "AST": "assists",
    "STL": "steals",
    "BLK": "blocks",
    "TO": "turnovers",
    "PF": "fouls",
    "+/-": "plusMinus",
    "OREB": "offensiveRebounds",
    "DREB": "defensiveRebounds",
    "FGM": "fieldGoalsMade",
    "FGA": "fieldGoalsAttempted",
    "3PM": "threePointFieldGoalsMade",
    "3PA": "threePointFieldGoalsAttempted",
    "FTM": "freeThrowsMade",
    "FTA": "freeThrowsAttempted",
}
# Computed rather than stored: header -> (made header, attempted header, key).
_LOG_PERCENTAGES: dict[str, tuple[str, str, str]] = {
    "FG%": ("FGM", "FGA", "fieldGoalPct"),
    "3P%": ("3PM", "3PA", "threePointFieldGoalPct"),
    "FT%": ("FTM", "FTA", "freeThrowPct"),
}
_LOG_BASE = ("MIN", "PTS", "REB", "AST")

GAME_LOG_STAT_COLUMNS: dict[str, tuple[str, ...]] = {
    "points": (),
    "rebounds": (),
    "assists": (),
    "minutes": (),
    "steals": ("STL",),
    "blocks": ("BLK",),
    "turnovers": ("TO",),
    "fouls": ("PF",),
    "plusMinus": ("+/-",),
    "offensiveRebounds": ("OREB",),
    "defensiveRebounds": ("DREB",),
    "fieldGoalsMade": ("FGM", "FGA"),
    "fieldGoalsAttempted": ("FGM", "FGA"),
    "fieldGoalPct": ("FGM", "FGA", "FG%"),
    "threePointFieldGoalsMade": ("3PM", "3PA"),
    "threePointFieldGoalsAttempted": ("3PM", "3PA"),
    "threePointFieldGoalPct": ("3PM", "3PA", "3P%"),
    "freeThrowsMade": ("FTM", "FTA"),
    "freeThrowsAttempted": ("FTM", "FTA"),
    "freeThrowPct": ("FTM", "FTA", "FT%"),
}
"""The columns a named ``stat`` adds to a player's game log, beyond minutes,
points, rebounds and assists. A shooting stat always brings its makes and
attempts, and a percentage is computed from them per game.

.. versionadded:: 2.1.0
"""


def _log_extras(stat: Any) -> tuple[str, ...]:
    """The columns a named stat adds to a player's log.

    ``stat`` is required in ROUTER_SCHEMA, so the model fills it on every
    question, including ones that name no stat at all - text that is not a stat
    name adds nothing. A REAL stat the log has no column for refuses instead:
    "luka ts% log" answered with no TS% in it would be the narrower answer
    passed off as the one asked for."""
    if not isinstance(stat, str) or not stat.strip():
        return ()
    if stat in GAME_LOG_STAT_COLUMNS:
        return GAME_LOG_STAT_COLUMNS[stat]
    if stat in PLAYER_STAT_COLUMNS or stat in HISTORY_COLUMNS or stat in THRESHOLD_STAT_COLUMNS or resolve_metric(stat) is not None:
        raise TemplateUnsupported(f"a game log has no per-game column for {stat!r}")
    return ()


def game_log(ctx: TemplateContext, slots: dict[str, Any]) -> TemplateResult:
    """A team's or a player's games. Both orderings are explicit: "first game"
    and "last game" differ only by ORDER BY direction, and LIMIT 1 without one
    returns an arbitrary row rather than either.

    A player's log can be narrowed to an ``opponent``, a ``venue`` and the games
    a teammate missed (``without``), and ``span`` "career" makes it every
    season, so "last 8 games vs the Pistons" reaches back as far as it has to.
    It lists games he played, adds the columns a named stat needs, and ends
    with per-game averages over exactly the games listed. A team's log honors
    the same slots except ``without``, which is with_without's question.

    .. versionchanged:: 2.1.0
       Honors ``opponent``, ``venue``, ``span`` and ``without``. Dates are the
       US Eastern date a game was played on rather than its UTC tip time, for
       ``date`` as well as for display. A player's log leaves out games he did
       not play, shows the columns a named stat needs and ends with an average
       row; a ``threshold`` is refused rather than ignored.
    """
    con = ctx.con
    season_type = slots.get("season_type") or 2
    limit = _clamp_limit(slots.get("limit"), default=DEFAULT_GAME_LOG_LIMIT)
    asked = slots["limit"] if isinstance(slots.get("limit"), int) and slots["limit"] >= 1 else None
    ascending = slots.get("order") == "first"
    raw_date = slots.get("date")
    date = raw_date if isinstance(raw_date, str) and _ISO_DATE.match(raw_date) else None
    opponent, venue, span, without = slots.get("opponent"), slots.get("venue"), slots.get("span"), slots.get("without")
    if slots.get("threshold") is not None:
        # "mikal bridges game log with less than 15 fga" would list his last
        # ten games whatever they held; keeping only the games past a line is a
        # filter this template does not have.
        raise TemplateUnsupported("game_log cannot keep only the games past a threshold")
    # A date names its game outright, so it replaces the season rather than
    # being filtered inside it: the router's season is usually its "current"
    # default, and a date from last season looked for in this one finds nothing.
    season = None if date else slots.get("season")
    span = "career" if date else span

    if slots.get("team"):
        team = _resolved_team(con, slots.get("team"))
        if isinstance(team, TemplateResult):
            return team
        if without:
            raise TemplateUnsupported("a team's games without one of its players is a with_without question")
        scope = _span_of(span, season, season_type, "games")
        return _team_game_log(con, team, scope, opponent=opponent, venue=venue, date=date, limit=limit, ascending=ascending)

    # The span is settled before the name is resolved, because it is what
    # narrows the name. `season` here is the raw slot - None means the current
    # season only once _span_of reads it - and passed through as it was, it
    # narrowed "curry's last 5 games" over every season and asked about all six
    # Currys again.
    scope = _span_of(span, season, season_type, "player_game_log")
    player = _resolved_player(con, slots.get("player"), "game_log needs a team or a player", available=_GAME_LOGS, season=scope.season, through=_career_end(scope.season))
    if isinstance(player, TemplateResult):
        return player
    extras = _log_extras(slots.get("stat"))
    narrowed = _narrow_player_games(con, player, scope, opponent=opponent, venue=venue, without=without)
    if isinstance(narrowed, TemplateResult):
        return narrowed
    if date:
        start, end = _eastern_day(date)
        narrowed.extra.append("g.date >= ? AND g.date < ?")
        narrowed.extra_params += [start, end]
        narrowed.date = date
    return _player_game_log(con, player, scope, narrowed, extras, limit=limit, asked=asked, ascending=ascending)


def _scope(count: int, ascending: bool, date: str | None) -> str:
    if date:
        return f"on {date}"
    if count == 1:
        return "first game" if ascending else "most recent game"
    return f"first {count} games" if ascending else f"last {count} games"


# The season a team's game belongs to, by the project's convention: a
# postseason by the calendar year it was played in, since ESPN labels every
# season before 1993-94 by the year it started (see _season_games).
_TEAM_SEASON = "CASE WHEN tbs.season_type = 3 THEN CAST(substr(g.date, 1, 4) AS INTEGER) ELSE tbs.season END"


def _postseason_scope(span: _Span) -> tuple[str, list[Any]]:
    """_Span.clause for a postseason over ``games`` (aliased ``g``): one
    playoffs by the calendar year it was played in, or every playoffs from
    ``span.first`` on - never by label, for the reason _season_games gives.
    Labelled, a career of playoff games dropped the 1989 playoffs (stored as
    1988) and printed the 1991 run as "1990"."""
    if span.season is not None:
        return _season_games(span.season, 3, "g")
    phantom = COVERAGE["games"].phantom
    excluded = f" AND g.season NOT IN ({', '.join('?' for _ in phantom)})" if phantom else ""
    return f"CAST(substr(g.date, 1, 4) AS INTEGER) >= ?{excluded}", [span.first, *phantom]


def _team_game_log(con: duckdb.DuckDBPyConnection, team: Entity, span: _Span, *, opponent: Any, venue: Any, date: str | None, limit: int, ascending: bool) -> TemplateResult:
    """A team's games in ``span``, narrowed to an opponent, a venue and a date
    where the question named them."""
    # A postseason by the calendar year it was played in - see _season_games.
    clause, params = _postseason_scope(span) if span.season_type == 3 else span.clause("tbs.season")
    base, base_params = ["tbs.team_id = ?", "tbs.season_type = ?", clause], [team.id, span.season_type, *params]
    extra: list[str] = []
    extra_params: list[Any] = []
    filters: list[str] = []
    if opponent:
        rival = _resolved_team(con, opponent)
        if isinstance(rival, TemplateResult):
            return rival
        if rival.id == team.id:
            raise TemplateUnsupported("a team cannot be its own opponent")
        extra.append("tbs.opponent_team_id = ?")
        extra_params.append(rival.id)
        filters.append(f"vs the {rival.name}")
    if venue:
        checked = _checked_venue(venue)
        extra.append("tbs.home_away = ?")
        extra_params.append(checked)
        filters.append("at home" if checked == "home" else "on the road")
    if date:
        start, end = _eastern_day(date)
        extra.append("g.date >= ? AND g.date < ?")
        extra_params += [start, end]
    narrowed = "".join(f" {f}" for f in filters)
    rows = con.execute(
        f"{_TEAM_GAMES_SQL} WHERE {' AND '.join(base + extra)} ORDER BY g.date {'ASC' if ascending else 'DESC'} LIMIT ?",
        [*base_params, *extra_params, limit],
    ).fetchall()
    if not rows:
        # Which fact is missing: the team's games in that span, or the match.
        found = con.execute(
            f"SELECT COUNT(*), MIN({_TEAM_SEASON}), MAX({_TEAM_SEASON}) FROM team_box_stats tbs JOIN games g ON g.event_id = tbs.event_id AND g.season = tbs.season WHERE {' AND '.join(base)}",
            base_params,
        ).fetchone()
        total, first, last = found if found else (0, None, None)
        if not total:
            where = _period(span.season, span.season_type) if span.season is not None else f"{span.kind}s on record"
            return TemplateResult(data={"team": team.name, "games": []}, answer=f"No {where} games found for the {team.name}.")
        on_date = f" on {date}" if date else ""
        answer = f"The {team.name} played {total:,} games {span.during(first, last, whose='all seasons on record')}, none of them{narrowed}{on_date}."
        return TemplateResult(data={"team": team.name, "games": []}, answer=answer)

    games = [
        {"date": _eastern_date(r[0]), "home_away": r[1], "opponent": r[2], "team_score": r[3], "opponent_score": r[4], "won": None if r[5] is None else r[5] == r[6], "season": r[7]} for r in rows
    ]
    # Tallied here, over exactly the rows being shown, rather than left to be
    # counted back out of the listing - that recount is where a wins/losses
    # total gets inverted.
    wins = sum(1 for g in games if g["won"] is True)
    losses = sum(1 for g in games if g["won"] is False)
    unknown = len(games) - wins - losses
    record = f"{wins}-{losses}" + (f", {unknown} with no recorded result" if unknown else "")
    seasons = [g["season"] for g in games]
    if span.season is not None:
        where = f" of the {_period(span.season, span.season_type)}"
    else:
        years = span.years(min(seasons), max(seasons))
        where = f" ({years})" if date else f" (all-time, {years})"
    header = f"{team.name}{narrowed}, {_scope(len(games), ascending, date)}{where} ({record}):"
    mark = {True: "W", False: "L", None: "?"}
    lines = [f"  {g['date']}  {mark[g['won']]} {g['team_score']}-{g['opponent_score']}  {'vs' if g['home_away'] == 'home' else 'at'} {g['opponent']}" for g in games]
    return TemplateResult(data={"team": team.name, "wins": wins, "losses": losses, "games": games}, answer="\n".join([header, *lines]))


def _pct(made: Any, attempted: Any) -> float | None:
    return 100.0 * made / attempted if made is not None and attempted else None


def _log_key(header: str) -> str:
    return _LOG_PERCENTAGES[header][2] if header in _LOG_PERCENTAGES else _LOG_COLUMNS[header]


def _log_cell(header: str, value: Any, *, average: bool = False) -> str:
    if value is None:
        return "-"
    if header == "+/-":
        return f"{value:+.1f}" if average else f"{int(value):+d}"
    if header in _LOG_PERCENTAGES or average:
        return f"{value:.1f}"
    return str(int(value))


def _aligned(titles: list[str], rows: list[list[str]], left: int) -> list[str]:
    """Rows under titles, the first ``left`` columns left-aligned and the rest
    right-aligned, indented like every other listing here."""
    widths = [max(len(title), *(len(row[i]) for row in rows)) for i, title in enumerate(titles)]

    def _line(cells: list[str]) -> str:
        return ("  " + "  ".join(cell.ljust(width) if i < left else cell.rjust(width) for i, (cell, width) in enumerate(zip(cells, widths, strict=True)))).rstrip()

    return [_line(titles), *(_line(row) for row in rows)]


def _player_game_log(con: duckdb.DuckDBPyConnection, player: Entity, span: _Span, narrowed: _Narrowed, extras: tuple[str, ...], *, limit: int, asked: int | None, ascending: bool) -> TemplateResult:
    """The listing, and the per-game averages over exactly the rows in it."""
    headers = list(dict.fromkeys([*_LOG_BASE, *extras]))
    # A percentage is never fetched: it is computed from the made/attempted pair
    # behind it, which is fetched whether or not it is shown.
    needed = list(dict.fromkeys([*(h for h in headers if h in _LOG_COLUMNS), *(c for h in headers if h in _LOG_PERCENTAGES for c in _LOG_PERCENTAGES[h][:2])]))
    where, params = narrowed.clauses()
    rows = con.execute(
        f"SELECT pgl.game_date, pgl.season, pgl.opponent_abbr, g.home_team_id = pgl.team_id, g.winner_team_id, pgl.team_id, "
        f"{', '.join(f'pgl.{_LOG_COLUMNS[h]}' for h in needed)} {_PLAYER_GAMES} WHERE {where} ORDER BY pgl.game_date {'ASC' if ascending else 'DESC'} LIMIT ?",
        [*params, limit],
    ).fetchall()
    scope: dict[str, Any] = {
        "player": player.name,
        "season": span.season,
        "span": "career" if span.career and not narrowed.date else None,
        "opponent": narrowed.opponent.name if narrowed.opponent else None,
        "venue": narrowed.venue,
        "without": narrowed.without.name if narrowed.without else None,
    }
    if not rows:
        message = _no_narrowed_games(con, player, span, narrowed)
        return TemplateResult(data={**scope, "games": [], "message": message}, answer=message)

    games: list[dict[str, Any]] = []
    raws: list[dict[str, Any]] = []
    for game_date, season, opponent, home, winner, team_id, *values in rows:
        raw = dict(zip(needed, values, strict=True))
        game: dict[str, Any] = {
            "date": _eastern_date(game_date),
            "season": season,
            "opponent": opponent,
            "home_away": "home" if home else "away",
            "result": None if winner is None else ("W" if winner == team_id else "L"),
        }
        for h in headers:
            game[_log_key(h)] = _pct(raw[_LOG_PERCENTAGES[h][0]], raw[_LOG_PERCENTAGES[h][1]]) if h in _LOG_PERCENTAGES else raw[h]
        games.append(game)
        raws.append(raw)

    averages: dict[str, float | None] = {}
    for h in headers:
        if h in _LOG_PERCENTAGES:
            made_h, attempted_h, key = _LOG_PERCENTAGES[h]
            averages[key] = _pct(sum(r[made_h] or 0 for r in raws), sum(r[attempted_h] or 0 for r in raws))
        else:
            present = [r[h] for r in raws if r[h] is not None]
            averages[_LOG_COLUMNS[h]] = sum(present) / len(present) if present else None

    count = len(games)
    if narrowed.date:
        listed = "game" if count == 1 else "games"
        scope_text = f"{listed} on {narrowed.date}"
    elif count == 1:
        scope_text = "first game" if ascending else "most recent game"
    else:
        scope_text = f"first {count} games" if ascending else f"last {count} games"
    seasons = [g["season"] for g in games]
    if span.season is not None:
        where_text = f" of the {_period(span.season, span.season_type)}"
    elif narrowed.date:
        where_text = f" ({span.years(min(seasons), max(seasons))})"
    else:
        where_text = f" of his career ({span.years(min(seasons), max(seasons))})"
    header = f"{player.name}{narrowed.filters(dated=False)}, {scope_text}{where_text}:"

    titles = ["date", "opp", "W/L", *headers]
    body = [[g["date"], f"{'vs' if g['home_away'] == 'home' else '@'} {g['opponent']}", g["result"] or "-", *(_log_cell(h, g[_log_key(h)]) for h in headers)] for g in games]
    body.append(["per game", "", "", *(_log_cell(h, averages[_log_key(h)], average=True) for h in headers)])

    notes: list[str] = []
    if asked and count < asked and not narrowed.date:
        found_in = "in his box scores" if span.career else f"in the {_period(span.season or current_season(), span.season_type)} - ask about his career to reach earlier seasons"
        notes.append(f"Only {count} game{'s' if count != 1 else ''}{narrowed.filters()} {found_in}.")
    notes += _box_score_notes(con, player, span, narrowed, career_note=not narrowed.date)
    return TemplateResult(
        data={**scope, "columns": headers, "games": games, "averages": averages},
        answer="\n".join([header, *_aligned(titles, body, left=3), *notes]),
    )


def _scoping_game(con: duckdb.DuckDBPyConnection, athlete_id: str, season: int, season_type: int, order: str) -> tuple[Any, ...] | None:
    """The single game an `order` slot narrows a question to: (event_id, date),
    or None if the player has no games that season. Both orderings are explicit -
    LIMIT 1 without an ORDER BY returns an arbitrary row, not the first or last."""
    return con.execute(
        f"SELECT event_id, game_date FROM player_game_log WHERE athlete_id = ? AND season = ? AND season_type = ? ORDER BY game_date {'ASC' if order == 'first' else 'DESC'} LIMIT 1",
        [athlete_id, season, season_type],
    ).fetchone()


def shot_chart(ctx: TemplateContext, slots: dict[str, Any]) -> TemplateResult:
    """Renders one player's shots to a static HTML court plot.

    Uses shotchart.render_shot_chart, the same function the agent tool calls,
    so it inherits best-match player handling rather than resolve_player's
    refusal: a chart of the wrong Curry is obvious on sight, and titled with the
    resolved name."""
    name = slots.get("player")
    if not isinstance(name, str) or not name.strip():
        raise TemplateUnsupported("shot_chart needs a player name")
    shot_value = _shot_value(slots)

    # "a shot chart of Curry's LAST regular season game" charted the whole
    # season - 803 attempts instead of that game's 22 - because nothing scoped
    # the request to one game. `order` means the same here as in game_log, and
    # resolving it to an event_id is the only way the chart can scope.
    #
    # Settled before the name is resolved, because the season is what narrows
    # an ambiguous name to the players who could have taken these shots.
    season = slots.get("season") or current_season()
    season_type = slots.get("season_type") or 2

    # Resolved ONCE, here, and the same player is then used both to find the
    # game to scope to and to draw the chart. Resolving separately for each
    # would let the two disagree and scope the chart to a game the other
    # candidate played.
    resolved = resolve_chart_player(ctx.con, name, SHOT_AVAILABILITY, season)
    if resolved is None:
        message = no_match(ctx.con, name)
        return TemplateResult(data={"message": message}, answer=message)
    if isinstance(resolved, Ambiguous):
        return _clarify(name, resolved.candidates, active=resolved.active)
    player, ambiguous = resolved

    event_id = None
    if slots.get("order") in ("recent", "first"):
        found = _scoping_game(ctx.con, player.id, season, season_type, slots["order"])
        if found is None:
            raise TemplateUnsupported(f"no games found to chart for {name!r}")
        event_id = found[0]

    rendered = render_for_player(
        ctx.con,
        ctx.out_dir,
        player,
        ambiguous,
        # An unspecified season means the CURRENT one here, exactly as it does
        # in every other template - passing None through charted a player's
        # entire career in one plot (confirmed live: 3,665 Curry attempts).
        season=None if event_id else season,
        season_type=None if event_id else season_type,
        event_id=event_id,
        shot_value=shot_value,
    )
    # A "no player found" / "no shots found" message is returned as the answer
    # rather than falling through: the agent has no better source for a chart
    # than the same table this just queried.
    artifact = rendered.artifact
    return TemplateResult(
        data={"message": rendered.message, "player": player.name, "path": str(artifact.path) if artifact else None},
        answer=rendered.message,
        artifacts=[artifact] if artifact else [],
    )


def fingerprint(ctx: TemplateContext, slots: dict[str, Any]) -> TemplateResult:
    """Renders one or more players' NetPoints fingerprints to a static HTML
    radar plot.

    Uses fingerprint.render_for_players, and resolves names the same best-match
    way shot_chart does: a plot titled with the resolved name shows a wrong
    match on sight, which is what makes best-match safe here and not in a
    template reporting numbers.
    """
    # "compare their fingerprints" arrives as `players`, one name as `player`.
    # Both draw one plot; two polygons on shared axes IS the comparison, so
    # this does not need a second intent.
    names = slots.get("players") if isinstance(slots.get("players"), list) else None
    names = [n for n in names if isinstance(n, str) and n.strip()] if names else []
    if not names:
        single = slots.get("player")
        if not isinstance(single, str) or not single.strip():
            raise TemplateUnsupported("fingerprint needs a player name")
        names = [single]
    names = names[:MAX_FINGERPRINT_PLAYERS]

    # A question about one game draws that game, from the long per-game table
    # rather than the season file - see fingerprint.load_game_fingerprints for
    # why its numbers are the game's own net points and not a per-100 rate. A
    # `date` is not honoured the same way: the router gives a calendar date and
    # the loader picks a player's first or last game, which are different
    # questions, so a dated request still says it cannot answer.
    order = slots.get("order") if slots.get("order") in ("recent", "first") else None
    if slots.get("date") and not order:
        message = "A fingerprint can be drawn for a player's first or most recent game of a season, but not yet for a particular date - ask for their last game instead."
        return TemplateResult(data={"message": message}, answer=message)

    # Settled before any name is resolved: the season is what narrows an
    # ambiguous name to the players who have a fingerprint in it.
    season = slots.get("season") or current_season()
    requested_type = slots.get("season_type")
    season_type = requested_type if isinstance(requested_type, int) else 2
    # A one-game plot is narrowed against the table it will actually be drawn
    # from. Availability in the season file does not imply a row per game, and
    # the season file has no season_type at all.
    availability = GAME_FINGERPRINT_AVAILABILITY if order else FINGERPRINT_AVAILABILITY

    players: list[Entity] = []
    ambiguous: list[str] = []
    for name in names:
        found = resolve_chart_player(ctx.con, name, availability, season)
        if found is None:
            message = no_match(ctx.con, name)
            return TemplateResult(data={"message": message}, answer=message)
        if isinstance(found, Ambiguous):
            return _clarify(name, found.candidates, active=found.active)
        player, also = found
        # The same name twice would draw one polygon over itself and report a
        # comparison; deduped on the RESOLVED id, since "SGA" and "Gilgeous"
        # are two names for one player.
        if player.id not in {p.id for p in players}:
            players.append(player)
        ambiguous.extend(also)

    # The router's word for it is `side`, which is what a question says ("his
    # defensive fingerprint"); the renderer's is `view`, because each skill
    # already carries the side it is measured on and this only picks which
    # skills are drawn.
    view = slots.get("side")
    if view not in FINGERPRINT_VIEWS:
        view = "total"
    try:
        rendered = render_for_players(ctx.con, ctx.out_dir, players, ambiguous, season, view=view, season_type=season_type, order=order)
    except FingerprintUnavailable as exc:
        # Returned, not raised: the agent has no better source for this plot
        # than the table this just read, so falling through would only be slow.
        return TemplateResult(data={"message": str(exc)}, answer=str(exc))
    artifact = rendered.artifact
    return TemplateResult(
        data={
            "players": [p.name for p in players],
            "season": season,
            "side": view,
            "scope": "game" if order else "season",
            "path": str(artifact.path) if artifact else None,
            "message": rendered.message,
        },
        answer=rendered.message,
        artifacts=[artifact] if artifact else [],
    )


SHOT_VALUE_FROM_STAT = {"threePointFieldGoalsMade": 3, "freeThrowsMade": 1}


def _shot_value(slots: dict[str, Any]) -> int | None:
    """Which shots a question meant. "Curry's threes" arrives either as
    shot_value 3 or as the equivalent box-score stat depending on wording; both
    mean the same thing, so read both rather than fight the router over which."""
    raw = slots.get("shot_value")
    if raw in (1, 2, 3):
        return int(raw)
    stat = slots.get("stat")
    return SHOT_VALUE_FROM_STAT.get(stat) if isinstance(stat, str) else None


def shot_distance(ctx: TemplateContext, slots: dict[str, Any]) -> TemplateResult:
    """Average shot distance for one player, optionally by shot value.

    The agent wrote a distance formula, then dropped both the 3-point filter
    and the season filter, reporting an all-shots all-seasons 16.94 as a
    current-season three-point figure. A fixed formula over known columns is
    template work - but "fixed" is only as good as the frame: this template
    then measured from a hoop 5.25 feet from where the data puts it, and
    answered Stephen Curry's 2026 threes with 23.6 feet, inside the line. From
    the rim (see court.HOOP_Y) they average 27.6.

    Shot values come from shotchart.SHOT_VALUE_SQL rather than
    ``points_attempted``, which is 0 for an unlabeled shot: "Curry's threes in
    2022" averaged 38 of his 751 attempts, every one of them a miss."""
    con = ctx.con
    season = slots.get("season") or current_season()
    player = _resolved_player(con, slots.get("player"), "shot_distance needs a player name", available=SHOT_AVAILABILITY, season=season)
    if isinstance(player, TemplateResult):
        return player

    season_type = slots.get("season_type") or 2
    shot_value = _shot_value(slots)
    if shot_value == 1:
        raise TemplateUnsupported("free throws have no meaningful shot distance")
    period = _period(season, season_type)
    kind = {2: "2-point ", 3: "3-point "}.get(shot_value or 0, "")
    if shot_value is not None and season in UNSEPARABLE_SHOT_VALUES:
        # Returned, not raised: the agent reads the same unlabeled rows.
        message = f"{UNSEPARABLE_SHOT_VALUES[season]}. {player.name}'s average {kind}shot distance in the {period} cannot be given; his average over all shots can."
        return TemplateResult(data={"player": player.name, "season": season, "shot_value": shot_value, "message": message}, answer=message)

    # Free throws are excluded by value, not by a missing position: from 2002
    # to 2018 they carry a fixed one under the rim, and averaged in as shots.
    where = ["athlete_id = ?", "season = ?", "season_type = ?", HAS_POSITION_SQL, f"{SHOT_VALUE_SQL} IS DISTINCT FROM 1"]
    params: list[Any] = [player.id, season, season_type]
    if shot_value is not None:
        where.append(f"{SHOT_VALUE_SQL} = ?")
        params.append(shot_value)
    game_note = ""
    if slots.get("order") in ("recent", "first"):
        found = _scoping_game(con, player.id, season, season_type, slots["order"])
        if found is None:
            raise TemplateUnsupported(f"no games found for {player.name}")
        where.append("event_id = ?")
        params.append(found[0])
        game_note = f" in his {'first' if slots['order'] == 'first' else 'most recent'} game ({_eastern_date(found[1])})"
    row = con.execute(f"SELECT AVG({SHOT_DISTANCE_SQL}), COUNT(*) FROM shot_chart WHERE {' AND '.join(where)}", params).fetchone()

    average, attempts = row or (None, 0)
    if not attempts or average is None:
        answer = f"No {kind}shots with recorded coordinates for {player.name}{game_note} in the {period}."
    else:
        answer = f"{player.name}'s average {kind}shot distance{game_note or f' in the {period}'} was {average:.1f} feet, over {attempts:,} attempts with recorded coordinates."
        if shot_value is not None and season in DERIVED_SHOT_VALUES:
            answer += f" Note: {DERIVED_SHOT_VALUES[season]}."
    return TemplateResult(
        data={"player": player.name, "season": season, "shot_value": shot_value, "avg_feet": average, "attempts": attempts},
        answer=answer,
    )


DEFAULT_SINGLE_GAME_LIMIT = 3


def single_game_high(ctx: TemplateContext, slots: dict[str, Any]) -> TemplateResult:
    """ "Most assists in a single game" - a per-game MAXIMUM, not a season
    ranking.

    With no such intent the router picked the nearest shape it had: "most
    assists in a single game" was answered with a season average, in 1.76s, off
    by 13. A missing shape does not produce a refusal - it produces a confident
    answer to a different question, so the fix is a template, not prompt
    wording.

    .. versionchanged:: 2.1.0
       Honours ``span`` "career": a named player's career high, or the league's
       best since 1993-94, each saying what it covers. A game's date is the
       Eastern calendar day it was played; it used to be the UTC day it is
       stored under, a day late for every game tipping after 7pm Eastern.
    """
    stat = slots.get("stat")
    column = THRESHOLD_STAT_COLUMNS.get(stat) if isinstance(stat, str) else None
    if column is None:
        raise TemplateUnsupported(f"single_game_high needs a known stat, got {stat!r}")

    career = _career_span("single_game_high", slots.get("span"), slots.get("season"))
    season = None if career else (slots.get("season") or current_season())
    season_type = slots.get("season_type") or 2
    limit = _clamp_limit(slots.get("limit"), default=DEFAULT_SINGLE_GAME_LIMIT)

    scope, params = _box_scope("l", season, season_type)
    where = [scope, f"l.{column} IS NOT NULL"]
    text = slots.get("player")
    named_player: Entity | None = None
    # The player slot is optional here: unset means "the league".
    if isinstance(text, str) and text.strip():
        resolved = _resolved_player(ctx.con, text, available=_GAME_LOGS, season=season, through=_career_end(season))
        if isinstance(resolved, TemplateResult):
            return resolved
        named_player = resolved
        where.append("l.athlete_id = ?")
        params.append(resolved.id)

    rows = ctx.con.execute(
        f"SELECT l.player_name, l.{column}, l.game_date, l.opponent_abbr FROM player_game_log l WHERE {' AND '.join(where)} ORDER BY l.{column} DESC, l.game_date LIMIT ?",
        [*params, limit],
    ).fetchall()

    label = STAT_LABELS.get(stat or "", stat or "")
    span = _game_span(ctx.con, season, season_type, named_player)
    games = [{"player": r[0], "value": r[1], "date": _eastern_date(r[2]), "opponent": r[3]} for r in rows]
    # `question_shape` names the scope in the same form leaderboard and
    # threshold_count use it: a caption for a caller that renders the rows
    # itself and would otherwise have no way to say what season they are from
    # except by reusing the whole sentence, which already lists them.
    shape = f"most {label}s in a single game" + (f", {named_player.name}" if named_player else "") + f", {span.caption}"
    answer = span.preface + _phrase_single_game_high(games, label, span, named_player.name if named_player else None)
    if span.league_note:
        answer += f" Box scores begin in {span.since}, so this is not an all-time record: earlier games are not in this warehouse."
    empty = _empty_box_scores(ctx.con, season, season_type, named_player.id if named_player else None)
    answer += _empty_note(empty, named_player.name if named_player else None, "a bigger game may be missing")
    return TemplateResult(
        data={"question_shape": shape, "season": season, "span": "career" if career else None, "stat": stat, "games": games, "empty_box_scores": empty[0]},
        answer=answer,
    )


def _phrase_single_game_high(games: list[dict[str, Any]], label: str, span: _GameSpan, named_player: str | None) -> str:
    if not games:
        who = f"{named_player} has" if named_player else "There are"
        return f"{who} no {span.games} in the warehouse."
    top = games[0]
    where = f" vs {top['opponent']}" if top["opponent"] else ""
    if named_player:
        return f"{named_player}'s highest {label} total in a single game {span.when} was {top['value']}, on {top['date']}{where}."

    tied = [g for g in games if g["value"] == top["value"]]
    if len(tied) > 1:
        names = ", ".join(g["player"] for g in tied[:-1]) + f" and {tied[-1]['player']}"
        sentence = f"{names} tied for the most {label}s in a single game {span.when}, with {top['value']} each."
    else:
        sentence = f"{top['player']} had the most {label}s in a single game {span.when}: {top['value']}, on {top['date']}{where}."
    rest = [f"{g['player']} ({g['value']})" for g in games if g["value"] != top["value"]]
    return sentence + (f" Next: {', '.join(rest)}." if rest else "")


def _season_games(season: int, season_type: int, alias: str) -> tuple[str, list[Any]]:
    """SQL selecting one season's games from ``games`` (aliased ``alias``), and
    its parameters.

    A postseason is selected by the CALENDAR YEAR it was played in, never by
    its label. ESPN labels every season before 1993-94 by the year it STARTED:
    the postseason games labelled 1990 end on 1991-06-12, the 1991 Finals, so a
    label match answered "the 1991 playoffs" with 1992's. Every postseason is
    played inside the year its season is named for (the 2020 bubble ended in
    October 2020), so the year is exact for all of them - the same choice
    ``team_metrics.games_scope`` makes. The phantom 1993 label is excluded, since
    its games are 1994's and would be counted twice.

    A regular season keeps its label: every regular season a template can reach
    (1994 on) is labelled by the year it ends.
    """
    if season_type == 3:
        phantom = COVERAGE["games"].phantom
        excluded = f" AND {alias}.season NOT IN ({', '.join('?' for _ in phantom)})" if phantom else ""
        return f"CAST(substr({alias}.date, 1, 4) AS INTEGER) = ?{excluded}", [season, *phantom]
    return f"{alias}.season = ?", [season]


def head_to_head(ctx: TemplateContext, slots: dict[str, Any]) -> TemplateResult:
    """ "How many times did the 76ers play Boston?" - games between two teams.

    That question was answered "they did not play" (they played four times).
    The agent wrote `home_team_id = 'PHI'` against an opaque numeric VARCHAR
    ('20'), so the filter matched nothing - with the rule against it, and a
    worked WRONG example, in its prompt. Prompting cannot fix that; resolving
    names to ids in code can."""
    con = ctx.con
    # A city name rather than a nickname ("...play Boston?") makes the router
    # split the two teams across `team` and `teams` instead of putting both in
    # `teams`. Name resolution is fine either way, so treating `team` as a third
    # candidate absorbs the split rather than rejecting an answerable question.
    teams_slot = slots.get("teams")
    names = [n for n in teams_slot if isinstance(n, str) and n.strip()] if isinstance(teams_slot, list) else []
    team_slot = slots.get("team")
    if isinstance(team_slot, str) and team_slot.strip() and team_slot not in names:
        names = [team_slot, *names]
    # The router also writes the other side as `opponent` ("Celtics vs Bulls
    # head to head" arrives as team + opponent): it is one of the two teams.
    opponent_slot = slots.get("opponent")
    if isinstance(opponent_slot, str) and opponent_slot.strip() and opponent_slot not in names:
        names = [*names, opponent_slot]
    if len(set(names)) < 2:
        raise TemplateUnsupported("head_to_head needs two team names")

    # Until two DIFFERENT teams resolve, not the first two names: "Celtics" in
    # `team` and "Boston Celtics" in `teams` are one team, and the opponent
    # after them is the second.
    resolved: list[Entity] = []
    for name in names:
        team = _resolved_team(con, name)
        if isinstance(team, TemplateResult):
            return team
        if team.id not in {t.id for t in resolved}:
            resolved.append(team)
        if len(resolved) == 2:
            break
    if len(resolved) != 2:
        raise TemplateUnsupported("the named teams resolved to the same team")

    a, b = resolved
    # No season named means the CURRENT one, as everywhere else. "All time" is
    # a defensible reading here, but silently answering a different span than
    # the rest of the system is the substitution this design exists to prevent.
    # The answer names the season, so another one is a follow-up away.
    season = slots.get("season") or current_season()
    season_type = slots.get("season_type") or 2
    # Both orderings, since `games` is home/away-oriented rather than
    # team-perspective, and the season filter parenthesized around the whole
    # matchup - `A OR B AND season = ...` applies the season to one side only.
    season_clause, season_params = _season_games(season, season_type, "g")
    where = [
        "((g.home_team_id = ? AND g.away_team_id = ?) OR (g.home_team_id = ? AND g.away_team_id = ?))",
        "g.season_type = ?",
        season_clause,
    ]
    params: list[Any] = [a.id, b.id, b.id, a.id, season_type, *season_params]
    rows = con.execute(
        f"SELECT g.date, g.home_team_id, g.home_score, g.away_score, g.winner_team_id FROM games g WHERE {' AND '.join(where)} ORDER BY g.date",
        params,
    ).fetchall()

    a_wins = sum(1 for r in rows if r[4] == a.id)
    b_wins = sum(1 for r in rows if r[4] == b.id)
    period = _period(season, season_type)
    return TemplateResult(
        data={"teams": [a.name, b.name], "games": len(rows), "wins": {a.name: a_wins, b.name: b_wins}},
        answer=_phrase_head_to_head(a.name, b.name, len(rows), a_wins, b_wins, period),
    )


def _phrase_head_to_head(a: str, b: str, games: int, a_wins: int, b_wins: int, period: str) -> str:
    if games == 0:
        return f"The warehouse has no {period} games between the {a} and the {b}."
    times = "once" if games == 1 else f"{games} times"
    lead = f"The {a} and the {b} met {times} in the {period}"
    if a_wins == b_wins:
        return f"{lead}, splitting them {a_wins}-{b_wins}."
    leader, trailing = (a, f"{a_wins}-{b_wins}") if a_wins > b_wins else (b, f"{b_wins}-{a_wins}")
    return f"{lead}; the {leader} won the series {trailing}."


# The same team-perspective join game_log uses, so which side of a game was
# "this team" is never re-derived from team_id comparisons.
# home_linescores/away_linescores hold the official per-period score for that
# side (index 0 = Q1 ... 4+ = OT1/OT2/...) - no plays table, no LAG(), no
# --include-pbp, unlike the per-PLAYER version of this question.
_TEAM_QUARTER_SQL = """
SELECT g.date,
       CASE WHEN tbs.home_away = 'home' THEN g.home_linescores ELSE g.away_linescores END AS own_linescores,
       opp.display_name AS opponent
FROM team_box_stats tbs
JOIN games g ON g.event_id = tbs.event_id AND g.season = tbs.season
JOIN teams opp ON opp.team_id = tbs.opponent_team_id
"""

# Above this many games, a full per-game breakdown is unreadable rather than
# informative - it only fires when no opponent narrows the season down (a
# real head-to-head, regular season or playoffs, is never more than ~7 games).
_QUARTER_BREAKDOWN_LIMIT = 12


def _linescores(raw: Any) -> list[int]:
    """games.home_linescores/away_linescores: '25,32,25,26' -> [25, 32, 25, 26].
    Not every game reaches overtime, so the list can be shorter than a
    requested OT period asks for - callers treat a missing index as "this game
    didn't go there", not as zero points."""
    if not isinstance(raw, str) or not raw.strip():
        return []
    out = []
    for part in raw.split(","):
        part = part.strip()
        if part:
            try:
                out.append(int(part))
            except ValueError:
                continue
    return out


def _period_label(period: int) -> str:
    if 1 <= period <= 4:
        return f"{_ordinal(period)} quarter"
    ot = period - 4
    return "overtime" if ot == 1 else f"{_ordinal(ot)} overtime"


def team_quarter_points(ctx: TemplateContext, slots: dict[str, Any]) -> TemplateResult:
    """A team's total points in ONE quarter/period, optionally narrowed to one
    named opponent.

    A PLAYER's quarter score has no template - it needs the plays-table LAG()
    derivation - and router.py's _AGENT_ONLY forces those to the agent. A TEAM's
    does not: home_linescores/away_linescores already store the official
    per-period score, so this is a lookup rather than a derivation.

    Worth a template because the agent spent ~150s over 3 calls getting it
    wrong: a games.period column that doesn't exist, then home_team_id compared
    to 'PHI' - the id-vs-abbreviation mistake its own always-on rule warns
    against, on every call. A compound shape (quarter math AND a named
    opponent) is what this model fails at even with both rules in its
    prompt.

    .. versionadded:: 1.1.0
    """
    con = ctx.con
    period = slots.get("period")
    if not isinstance(period, int) or not 1 <= period <= 10:
        raise TemplateUnsupported(f"team_quarter_points needs an integer period 1-10, got {period!r}")
    if isinstance(slots.get("player"), str) and slots["player"].strip():
        # No template computes a PLAYER's quarter score - see the docstring.
        raise TemplateUnsupported("team_quarter_points cannot answer for a named player")

    team = _resolved_team(con, slots.get("team"))
    if isinstance(team, TemplateResult):
        return team

    opponent: Entity | None = None
    opponent_text = slots.get("opponent")
    if isinstance(opponent_text, str) and opponent_text.strip():
        resolved_opponent = _resolved_team(con, opponent_text)
        if isinstance(resolved_opponent, TemplateResult):
            return resolved_opponent
        opponent = resolved_opponent
        if opponent.id == team.id:
            raise TemplateUnsupported("team_quarter_points opponent must differ from the team")

    season = slots.get("season") or current_season()
    season_type = slots.get("season_type") or 2
    season_clause, season_params = _season_games(season, season_type, "g")
    where = ["tbs.team_id = ?", season_clause, "tbs.season_type = ?"]
    params: list[Any] = [team.id, *season_params, season_type]
    if opponent is not None:
        where.append("tbs.opponent_team_id = ?")
        params.append(opponent.id)
    rows = con.execute(f"{_TEAM_QUARTER_SQL} WHERE {' AND '.join(where)} ORDER BY g.date", params).fetchall()

    period_label = _period_label(period)
    period_str = _period(season, season_type)
    vs = f" against the {opponent.name}" if opponent else ""
    opponent_name = opponent.name if opponent else None

    games = []
    for date, own_linescores, opp_name in rows:
        scores = _linescores(own_linescores)
        points = scores[period - 1] if period - 1 < len(scores) else None
        games.append({"date": _eastern_date(date), "opponent": opp_name, "points": points})

    if not games:
        answer = f"The warehouse has no {period_str} games for the {team.name}{vs}."
        return TemplateResult(data={"team": team.name, "opponent": opponent_name, "games": []}, answer=answer)

    played = [g for g in games if g["points"] is not None]
    if not played:
        plural = "game" if len(games) == 1 else "games"
        answer = f"None of the {team.name}'s {len(games)} {period_str} {plural}{vs} went to the {period_label}."
        return TemplateResult(data={"team": team.name, "opponent": opponent_name, "games": games}, answer=answer)

    total = sum(g["points"] for g in played)
    data = {"team": team.name, "opponent": opponent_name, "period": period, "games": played, "total": total}
    return TemplateResult(data=data, answer=_phrase_team_quarter_points(team.name, opponent_name, period_label, period_str, played, total))


def _phrase_team_quarter_points(team: str, opponent: str | None, period_label: str, period_str: str, games: list[dict[str, Any]], total: int) -> str:
    if len(games) == 1:
        g = games[0]
        return f"The {team} scored {g['points']} points in the {period_label} against the {g['opponent']} on {g['date']} ({period_str})."
    if len(games) > _QUARTER_BREAKDOWN_LIMIT:
        avg = total / len(games)
        vs = f" against the {opponent}" if opponent else ""
        return f"The {team} scored {total} total points in the {period_label} across {len(games)} {period_str} games{vs}, averaging {avg:.1f} per game."
    header = f"The {team}, {period_label} scoring" + (f" against the {opponent}" if opponent else "")
    header += f", {period_str} ({len(games)} games, {total} total):"
    lines = [f"  {g['date']}  {g['points']}  vs {g['opponent']}" for g in games]
    return "\n".join([header, *lines])


MAX_COMPARED_PLAYERS = 4


def player_compare(ctx: TemplateContext, slots: dict[str, Any]) -> TemplateResult:
    """Two or more named players' season numbers side by side.

    The agent wrote correct SQL but expanded "SGA" to '%Scottie G. Allen%' and
    compared Luka Doncic to Luka Garza. Nickname resolution is a lookup, not
    something to hope a 7B model knows - see entities.PLAYER_NICKNAMES."""
    con = ctx.con
    names = slots.get("players")
    if not isinstance(names, list) or len({n for n in names if isinstance(n, str) and n.strip()}) < 2:
        raise TemplateUnsupported("player_compare needs at least two distinct player names")

    season = slots.get("season") or current_season()
    resolved: list[Entity] = []
    for name in names[:MAX_COMPARED_PLAYERS]:
        player = _resolved_player(con, name, available=_SEASON_LINES, season=season)
        if isinstance(player, TemplateResult):
            return player
        if player.id not in {p.id for p in resolved}:
            resolved.append(player)
    if len(resolved) < 2:
        raise TemplateUnsupported("the named players resolved to the same person")

    season_type = slots.get("season_type") or 2
    wanted = _wanted_stats(slots, COMPARE_STAT_LINE)
    columns = ["gamesPlayed"] + [PLAYER_STAT_COLUMNS[name][0] for name in wanted]

    rows: dict[str, dict[str, Any]] = {}
    for player in resolved:
        row = _season_row(con, player.id, columns, season, season_type)
        rows[player.name] = dict(zip(columns, row, strict=True)) if row else {}

    net = _compare_netpoints(con, resolved, season, season_type)
    period = _period(season, season_type)
    return TemplateResult(
        data={"season": season, "players": rows, "netpoints": net},
        answer=_phrase_compare(rows, wanted, period, net),
    )


# The NetPoints summary rows, in the order player_netpoints reports them:
# label -> the net_points_player column it reads. Per 100 possessions, not
# season totals, because a comparison is exactly the question totals answer
# badly - they mostly rank by playing time. The same choice fingerprint.py
# makes, and for the same reason.
NETPOINTS_COMPARE_ROWS: tuple[tuple[str, str], ...] = (
    ("net pts/100", "overall_per_100_poss"),
    ("  offense", "offense_per_100_poss"),
    ("  defense", "defense_per_100_poss"),
)


def _compare_netpoints(con: duckdb.DuckDBPyConnection, players: list[Entity], season: int, season_type: int) -> dict[str, dict[str, Any]]:
    """Each player's NetPoints summary, by resolved name. Missing is normal.

    NetPoints starts in 2019 and is a separate opt-in fetch, so this is
    supplementary rather than required: a player with no row contributes an
    empty dict and a season with no rows at all drops the section. That is also
    why `net_points_player` is deliberately NOT in this template's
    TEMPLATE_SOURCES entry - listing it would put a 2019 coverage floor on
    every comparison and refuse the 1994-2018 ones outright.
    """
    # net_points_player uses its OWN string season_type; filtering it with the
    # numeric one every other table uses silently matches nothing.
    label = SEASON_TYPE_LABELS.get(season_type, "Regular Season")
    selected = ", ".join(column for _, column in NETPOINTS_COMPARE_ROWS)
    found: dict[str, dict[str, Any]] = {}
    for player in players:
        try:
            row = con.execute(
                f"SELECT {selected} FROM net_points_player WHERE athlete_id = ? AND season = ? AND net_points_season_type = ?",
                [player.id, season, label],
            ).fetchone()
        except duckdb.Error:
            # The table only exists if the NetPoints fetch was run. Unlike
            # _single_game_netpoints, which has nothing else to say, a
            # comparison is complete without it - so this drops the section
            # rather than failing the answer.
            return {}
        found[player.name] = dict(zip([column for _, column in NETPOINTS_COMPARE_ROWS], row, strict=True)) if row else {}
    return found


def _phrase_compare(rows: dict[str, dict[str, Any]], wanted: list[str], period: str, netpoints: dict[str, dict[str, Any]] | None = None) -> str:
    """A fixed-width table rather than prose. Comparisons are the one shape
    where a sentence actively hurts - the agent's prose version stated that a
    player with 0.4 steals led one with 1.6."""
    names = list(rows)
    missing = [name for name, values in rows.items() if not values]
    # A fixed decimal in every cell, not _format_value: in an aligned column a
    # trailing-zero-stripped "25" next to "27.7" reads as a different unit.
    entries: list[tuple[str, list[str]]] = [("games", [_table_cell(rows[name].get("gamesPlayed")) for name in names])]
    for stat in wanted:
        column, _, label = PLAYER_STAT_COLUMNS[stat]
        entries.append((label, [_table_cell(rows[name].get(column)) for name in names]))

    net = netpoints or {}
    # Shown only when somebody has a row: an empty NetPoints block under a
    # comparison of two 1990s players would read as "both contributed nothing"
    # rather than "this season predates the data".
    if any(net.get(name) for name in names):
        entries.append(("", ["" for _ in names]))
        for label, column in NETPOINTS_COMPARE_ROWS:
            entries.append((label, [_signed_cell(net.get(name, {}).get(column)) for name in names]))

    label_width = max(len(label) for label, _ in entries)
    name_width = max(max(len(name) for name in names), *(len(cell) for _, cells in entries for cell in cells))
    # rstripped so the blank separator row is an empty line rather than a line
    # of spaces, which shows up as trailing whitespace wherever this is stored.
    lines = [f"{' vs '.join(names)}, {period}:", (f"{' ' * label_width}  " + "  ".join(name.rjust(name_width) for name in names)).rstrip()]
    lines += [(f"{label.ljust(label_width)}  " + "  ".join(cell.rjust(name_width) for cell in cells)).rstrip() for label, cells in entries]
    if missing:
        lines.append(f"({', '.join(missing)} has no {period} numbers in the warehouse.)")
    return "\n".join(lines)


# ---------------- games under a condition ----------------
#
# Five templates answering one kind of question: a set of games divided by
# something that happened in each, with the parts side by side. Their SQL is in
# conditions.py, whose docstring records what "played", a game's date and a
# result mean there - each measured against the warehouse, not assumed.

SPLIT_KINDS: tuple[str, ...] = ("home_away", "starter_bench", "wins_losses", "month")
"""The splits :func:`player_splits` answers, and the values ``router.SPLIT_WORDS`` reads out of a question.

.. versionadded:: 2.1.0
"""

_DEFAULT_STREAK_LIMIT = 5
_UNSEEN_ENDS_RUN = " A game with no box score in the warehouse ends a run rather than being carried across, since it cannot be checked."
_DEFAULT_MEETINGS_LOGGED = 5

# What the model tends to put in the required `stat` slot for "longest winning
# streak". Anything else is a stat, and a stat with no threshold is refused
# rather than read as a win streak - "most consecutive double-doubles" must not
# come back as the Lakers' best run of wins.
_RESULT_STATS = frozenset({"win", "wins", "winning", "loss", "losses", "losing", "streak", "streaks", "record", "games", "winning streak", "losing streak"})


def _condition_scope(season: Any, span: Any, season_type: Any, tables: tuple[str, ...]) -> _Scope:
    """The games a question covers. No season means the current one - except
    for a career, where it means every season on record, which is what the
    word asked for. A season the question named beats "career": the router keeps
    a named year alongside it, and "career ... in 2015" is asking about 2015."""
    kind = season_type if season_type in (2, 3) else 2
    if isinstance(season, int) and not isinstance(season, bool):
        return _game_scope(season, kind, tables)
    return _game_scope(None if span == "career" else current_season(), kind, tables)


def _where_in(scope: _Scope) -> str:
    """ "in the 2026 regular season", or "in any regular season on record" for a span with nothing in it."""
    return f"in the {scope.label()}" if scope.season is not None else f"in any {scope.kind} on record ({scope.first} onward)"


def _misfiled_postseason(scope: _Scope) -> TemplateResult | None:
    """A refusal for a single postseason before 1993-94, or None.

    The team tables hold playoff games back to 1988, but ESPN files every
    season before 1993-94 under the year it began: the warehouse's "1990"
    postseason runs from April to June 1991. Answered as asked, "Bulls 1990
    playoffs" would describe the 1991 playoffs under a 1990 label - the
    right-looking answer about another year this project keeps producing. A
    span of seasons starts in 1994 for the same reason (``_game_scope``)."""
    if not scope.misfiled:
        return None
    message = (
        f"The warehouse files playoff games from before 1993-94 under the year the season began - its {scope.season} postseason is the "
        f"{(scope.season or 0) + 1} playoffs - so an answer for {scope.season} would be about the wrong year."
    )
    return TemplateResult(data={"message": message, "season": scope.season}, answer=message)


def _optional_team(con: duckdb.DuckDBPyConnection, text: Any) -> Entity | TemplateResult | None:
    if not isinstance(text, str) or not text.strip():
        return None
    return _resolved_team(con, text)


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


def player_splits(ctx: TemplateContext, slots: dict[str, Any]) -> TemplateResult:
    """A player's per-game averages divided by one condition of the game:
    home or away, starting or off the bench, won or lost, or the month.

    With no ``split``, all four come back as one table rather than a guess at
    which was meant: the router reads the split from the question's words
    (``router.SPLIT_WORDS``) and leaves it unset when they name none or
    several. A team works too ("76ers wins vs losses") with the team's own
    per-game line, except that a team has no starter/bench split of its own,
    which is refused rather than answered with something else.

    A named ``team`` narrows a player's games to that team ("westbrook stats as
    a starter for kings"), since a traded player's splits are otherwise a mix
    of two rosters. Only games he played count, and months are the US Eastern
    date the game was played on - see :mod:`association.query.conditions`.

    .. versionadded:: 2.1.0
    """
    con = ctx.con
    split = slots.get("split")
    if split is not None and split not in SPLIT_KINDS:
        raise TemplateUnsupported(f"no split named {split!r}")
    team = _optional_team(con, slots.get("team"))
    if isinstance(team, TemplateResult):
        return team

    name = slots.get("player")
    if isinstance(name, str) and name.strip():
        scope = _condition_scope(slots.get("season"), slots.get("span"), slots.get("season_type"), _PLAYER_GAME_TABLES)
        player = _resolved_player(con, name, available=_BOX_SCORES, season=scope.season, through=_career_end(scope.season))
        if isinstance(player, TemplateResult):
            return player
        params: dict[str, Any] = {**scope.params(), "player": player.id}
        if team is not None:
            params["team"] = team.id
        base = _player_games(scope, extra=" AND pbs.team_id = $team" if team else "")
        games, first, last = _totals(con, base, params)
        if not games:
            return _no_games(con, player, scope, team)
        subject, alias, line, counted = player.name + (f" for the {team.name}" if team else ""), "p", _PLAYER_LINE, f"{games} games he played"
        data: dict[str, Any] = {"player": player.name, "team": team.name if team else None}
        caveat = _unseen_note(_unseen(con, scope, base, params))
    else:
        if team is None:
            raise TemplateUnsupported("player_splits needs a player or a team")
        if split == "starter_bench":
            # "Bench scoring" is a sum over a team's players - a different
            # question from any this template answers.
            raise TemplateUnsupported("a team has no starter/bench split of its own")
        scope = _condition_scope(slots.get("season"), slots.get("span"), slots.get("season_type"), _TEAM_GAME_TABLES)
        misfiled = _misfiled_postseason(scope)
        if misfiled is not None:
            return misfiled
        params = {**scope.params(), "team": team.id}
        base = _team_games(scope, " AND tbs.team_id = $team")
        games, first, last = _totals(con, base, params)
        if not games:
            message = f"The warehouse has no games with a result for the {team.name} {_where_in(scope)}."
            return TemplateResult(data={"team": team.name, "span": scope.label(), "games": 0}, answer=message)
        subject, alias, line, counted = f"The {team.name}", "t", _TEAM_LINE, f"{games} games"
        data = {"player": None, "team": team.name}
        # The score of a game with no box score is still on record, but its
        # team box stats are NULL - averaged over the rest, and said so.
        blank = con.execute(f"SELECT COUNT(*) FILTER (WHERE fieldGoalsAttempted IS NULL) FROM ({base})", params).fetchone()
        blanks = int(blank[0]) if blank else 0
        caveat = f" Rebounds, assists, 3-pointers and FG% are missing from {blanks} of those games' box scores and are averaged over the rest." if blanks else ""

    kinds = [split] if split else [k for k in SPLIT_KINDS if alias == "p" or k != "starter_bench"]
    splits = {kind: _split_rows(con, base, params, alias, line, kind) for kind in kinds}
    label = scope.label(first, last)
    rows: list[tuple[str, list[str]]] = []
    for kind in kinds:
        if rows:
            rows.append(("", []))
        rows += [(_split_label(kind, entry), _split_cells(entry, line)) for entry in splits[kind]]
    what = _SPLIT_TITLES[split] if split else "splits"
    notes = []
    if alias == "p":
        notes.append("Played means he logged minutes, and W-L is his team's record in those games.")
    if "month" in kinds:
        notes.append("Months go by the US Eastern date of the game.")
    answer = _table(f"{subject}, {what}, {label} ({counted}):", ["G", "W-L", *(h for _, h, _ in line)], rows)
    notes += [note.strip() for note in (scope.floor_note(first), caveat) if note]
    answer += "\n" + " ".join(notes)
    return TemplateResult(data={**data, "span": label, "games": games, "splits": splits}, answer=answer.strip())


def with_without(ctx: TemplateContext, slots: dict[str, Any]) -> TemplateResult:
    """A team's record in the games a teammate played against the games he
    missed - and, when the subject is a player, that player's averages in each.

    Both groups are shown side by side, because the comparison is the question.
    The subject is a team ("Celtics record without Tatum"), or a player whose
    team is implied ("jalen Duren stats without Cade Cunningham"). The
    teammate comes from ``without`` or ``with_player`` (both read from the
    question by the router), or failing those from a second name.

    **Only games inside the teammate's time on that team count.** StatMuse
    answers "Nets record without KD" all-time with 439-672: decades of Nets
    games before he arrived, every one a game "without" him. A teammate's time
    on a team is read from the box scores as a run of rows for that team, from
    the first to the last - see ``conditions._stints`` for where a run ends -
    and the answer prints those dates, so what was counted is on the page.
    "Played" means he logged minutes; a DNP and no box-score row at all are
    both "out", since a missed game appears both ways.

    .. versionadded:: 2.1.0
    """
    con = ctx.con
    without, with_player = slots.get("without"), slots.get("with_player")
    asked_without = isinstance(without, str) and bool(without.strip())
    mate_text: str | None = without if asked_without else with_player if isinstance(with_player, str) and with_player.strip() else None
    players = slots.get("players")
    listed: list[Any] = players if isinstance(players, list) else []
    texts = list(dict.fromkeys(n.strip() for n in [slots.get("player"), *listed] if isinstance(n, str) and n.strip()))
    team = _optional_team(con, slots.get("team"))
    if isinstance(team, TemplateResult):
        return team
    if mate_text is None:
        # No "with" or "without" in the question, so the teammate is whichever
        # name is not the subject: the only name beside a team, or the second
        # of two. More than that is "record when A and B and C play", which
        # this does not answer.
        if team is not None and len(texts) == 1:
            mate_text, texts = texts[0], []
        elif team is None and len(texts) == 2:
            mate_text, texts = texts[1], texts[:1]
        else:
            raise TemplateUnsupported(f"with_without needs exactly one teammate, got {texts!r}")

    scope = _condition_scope(slots.get("season"), slots.get("span"), slots.get("season_type"), _PLAYER_GAME_TABLES)
    mate = _resolved_player(con, mate_text, "with_without needs a teammate", available=_BOX_SCORES, season=scope.season, through=_career_end(scope.season))
    if isinstance(mate, TemplateResult):
        return mate
    subjects: list[Entity] = []
    for text in texts:
        found = _resolved_player(con, text, available=_BOX_SCORES, season=scope.season, through=_career_end(scope.season))
        if isinstance(found, TemplateResult):
            return found
        # The router often repeats the teammate in `player`; that is not a subject.
        if found.id != mate.id and found.id not in {s.id for s in subjects}:
            subjects.append(found)
    if len(subjects) > 1:
        raise TemplateUnsupported(f"with_without answers for one player, got {[s.name for s in subjects]}")
    subject = subjects[0] if subjects else None

    mate_stints = _stints(con, mate.id, scope.phantoms)
    windows = mate_stints if subject is None else _overlaps(_stints(con, subject.id, scope.phantoms), mate_stints)
    if team is not None:
        windows = [w for w in windows if w.team_id == team.id]
    on = f" the {team.name}" if team else ""
    if not mate_stints:
        message = f"{mate.name} has no box-score appearance in the warehouse, so there is no time on a team to count games in."
        return TemplateResult(data={"teammate": mate.name, "groups": []}, answer=message)
    if not windows:
        if subject is None:
            message = f"{mate.name} never appeared in a box score for{on}, so there are no {team.name if team else ''} games with or without him to count."
        else:
            message = f"{subject.name} and {mate.name} were never on{on or ' the same team'} together in the box scores on record, so there are no games to divide by whether {mate.name} played."
        return TemplateResult(data={"teammate": mate.name, "player": subject.name if subject else None, "groups": []}, answer=message)

    games, unknown = _with_without_games(con, scope, windows, mate.id, subject.id if subject else None)
    team_names = _names(con, "teams", "team_id", {w.team_id for w in windows})
    spells = "; ".join(f"{team_names[w.team_id]} {w.first} to {w.last}" for w in windows)
    if not games:
        whose = f"{mate.name}'s time" if subject is None else f"The time {subject.name} and {mate.name} spent together"
        message = f"{whose} on the team, as the box scores show it ({spells}), falls outside the {scope.label() if scope.season else 'seasons on record'}."
        if unknown:
            # Inside the time, but every game of it without a box score - a
            # different fact from the time missing the season altogether.
            message = (
                f"All {unknown} games inside {whose[0].lower() + whose[1:]} on the team in the {scope.label()} have no box score in the warehouse, so whether {mate.name} played them cannot be told."
            )
        return TemplateResult(data={"teammate": mate.name, "player": subject.name if subject else None, "groups": []}, answer=message)

    # Teams in the order the games were played, so a career reads forwards.
    team_order = list(dict.fromkeys(g["team_id"] for g in sorted(games, key=lambda g: g["day"])))
    order = (False, True) if asked_without else (True, False)
    groups: list[dict[str, Any]] = []
    rows: list[tuple[str, list[str]]] = []
    for team_id in team_order:
        for played in order:
            chosen = [g for g in games if g["team_id"] == team_id and g["mate_played"] == played]
            group = {"team": team_names[team_id], "teammate_played": played, **_with_without_group(chosen)}
            groups.append(group)
            cells = [str(group["games"]), f"{group['wins']}-{group['losses']}", _win_pct(group["wins"], group["games"]), _margin(group["avg_margin"])]
            if subject is not None:
                cells += [str(group["player_games"]), *(_cell(group[k]) for k in ("minutes", "points", "rebounds", "assists", "fg_pct"))]
            prefix = f"{team_names[team_id]}, " if len(team_order) > 1 else ""
            rows.append((f"{prefix}{mate.name} {'played' if played else 'out'}", cells))

    label = scope.label(min(g["season"] for g in games), max(g["season"] for g in games))
    counted_teams = ", ".join(team_names[t] for t in team_order)
    headers = ["G", "W-L", "Win%", "Margin"]
    if subject is None:
        title = f"{counted_teams} with and without {mate.name}, {label}:"
        whose = f"{mate.name}'s time with the team"
    else:
        title = f"{subject.name} with and without {mate.name} ({counted_teams}), {label}:"
        whose = f"the time {subject.name} and {mate.name} were both on the team"
        headers += ["Played", "MIN", "PTS", "REB", "AST", "FG%"]
    used = [w for w in windows if any(g["team_id"] == w.team_id and w.first <= g["day"] <= w.last for g in games)]
    spells = "; ".join(f"{team_names[w.team_id]} {w.first} to {w.last}" if len(team_order) > 1 else f"{w.first} to {w.last}" for w in used)
    notes = [
        f"Counted: games inside {whose} ({spells}), which runs from the first box score that lists him there to the last.",
        f"Played means {mate.name} logged minutes; out is a DNP or no box-score row at all.",
    ]
    if unknown:
        # A game with no box score is not a game he missed - see
        # conditions._box_missing - so it is on neither side, and said so.
        notes.append(f"{unknown} game{'' if unknown == 1 else 's'} inside that time {'has' if unknown == 1 else 'have'} no box score, so whether he played is unknown; they are on neither side.")
    if subject is not None:
        notes.append(f"G, W-L and margin are the team's; Played counts {subject.name}'s games, and his averages are over those.")
    answer = _table(title, headers, rows) + "\n" + " ".join(notes)
    tenure = [{"team": team_names[w.team_id], "from": str(w.first), "to": str(w.last)} for w in used]
    data = {"teammate": mate.name, "player": subject.name if subject else None, "teams": [team_names[t] for t in team_order], "span": label, "groups": groups, "tenure": tenure}
    return TemplateResult(data=data, answer=answer)


def record_when(ctx: TemplateContext, slots: dict[str, Any]) -> TemplateResult:
    """A team's record in the games a named player reached a stat threshold,
    beside its record in the games he fell short of it.

    "Sixers record when Embiid scores 30" - a count of wins and losses that
    ``threshold_count`` cannot give, since it counts games and not results. The
    stat is whitelisted like everywhere else, and both rows are always shown:
    the question is a comparison even when it names only one side. Only games
    he played count. The team is his team in each game, so a traded player's
    record follows him; a named ``team`` narrows it to that one.

    .. versionadded:: 2.1.0
    """
    con = ctx.con
    stat = slots.get("stat")
    column = THRESHOLD_STAT_COLUMNS.get(stat) if isinstance(stat, str) else None
    threshold = slots.get("threshold")
    if column is None or not isinstance(threshold, int) or isinstance(threshold, bool) or threshold < 1:
        raise TemplateUnsupported(f"record_when needs a known stat and a positive threshold, got {stat!r}/{threshold!r}")
    scope = _condition_scope(slots.get("season"), slots.get("span"), slots.get("season_type"), _PLAYER_GAME_TABLES)
    player = _resolved_player(con, slots.get("player"), "record_when needs a player", available=_BOX_SCORES, season=scope.season, through=_career_end(scope.season))
    if isinstance(player, TemplateResult):
        return player
    team = _optional_team(con, slots.get("team"))
    if isinstance(team, TemplateResult):
        return team

    params: dict[str, Any] = {**scope.params(), "player": player.id}
    if team is not None:
        params["team"] = team.id
    base = _player_games(scope, extra=" AND pbs.team_id = $team" if team else "")
    found = con.execute(
        f"WITH p AS ({base}) SELECT p.{column} >= $threshold, COUNT(*), COUNT(*) FILTER (WHERE p.won), AVG(p.team_score - p.opponent_score), "
        "MIN(p.season), MAX(p.season), list(DISTINCT p.team_id) FROM p GROUP BY 1",
        {**params, "threshold": threshold},
    ).fetchall()
    if not found:
        return _no_games(con, player, scope, team)

    by_hit = {bool(row[0]): row for row in found}
    team_ids = {str(t) for row in found for t in row[6]}
    names = _names(con, "teams", "team_id", team_ids)
    unit = f"{STAT_LABELS.get(stat or '', stat or '')}s"

    def group(hit: bool | None) -> dict[str, Any]:
        """The team's record in the games he reached the threshold (True), fell short (False), or both (None)."""
        rows = [by_hit[h] for h in ((hit,) if hit is not None else (True, False)) if h in by_hit]
        games = sum(int(r[1]) for r in rows)
        wins = sum(int(r[2]) for r in rows)
        margin = sum((r[3] or 0) * int(r[1]) for r in rows) / games if games else None
        return {"games": games, "wins": wins, "losses": games - wins, "avg_margin": margin}

    reached, short, every = group(True), group(False), group(None)
    label = scope.label(min(r[4] for r in found), max(r[5] for r in found))
    teams = sorted(names.values())
    whose = f"{teams[0]} record" if len(teams) == 1 else f"Record of {player.name}'s teams ({', '.join(teams)})"
    title = f"{whose} when {player.name} had {threshold}+ {unit}, {label}:"
    rows = [(f"{threshold}+ {unit}", reached), (f"under {threshold} {unit}", short), ("all his games", every)]
    table = _table(title, ["G", "W-L", "Win%", "Margin"], [(name, [str(g["games"]), f"{g['wins']}-{g['losses']}", _win_pct(g["wins"], g["games"]), _margin(g["avg_margin"])]) for name, g in rows])
    caveat = _unseen_note(_unseen(con, scope, base, params))
    answer = f"{table}\nOver the {every['games']} games he played; a game he missed is in neither row.{scope.floor_note(min(r[4] for r in found))}{caveat}"
    data = {"player": player.name, "teams": teams, "stat": stat, "threshold": threshold, "span": label, "reached": reached, "fell_short": short}
    return TemplateResult(data=data, answer=answer)


def player_matchup(ctx: TemplateContext, slots: dict[str, Any]) -> TemplateResult:
    """The games two named players both played, on opposite teams: the
    head-to-head record, each one's averages in those games, and the most
    recent meetings.

    Not ``player_compare``, which sets two players' season lines side by side
    whether or not they ever met. The router sends only log, record and
    head-to-head wordings here ("Andre Drummond vs Al Horford game log"), so
    this does not second-guess which of the two readings was meant. A game in
    which they were teammates is not a meeting, and when every shared game was
    one, the answer says that rather than that they never played.

    .. versionadded:: 2.1.0
    """
    con = ctx.con
    players = slots.get("players")
    listed: list[Any] = players if isinstance(players, list) else []
    texts = list(dict.fromkeys(n.strip() for n in [*listed, slots.get("player")] if isinstance(n, str) and n.strip()))
    if len(texts) != 2:
        raise TemplateUnsupported(f"player_matchup needs exactly two players, got {texts!r}")
    scope = _condition_scope(slots.get("season"), slots.get("span"), slots.get("season_type"), _PLAYER_GAME_TABLES)
    resolved: list[Entity] = []
    for text in texts:
        found = _resolved_player(con, text, available=_BOX_SCORES, season=scope.season, through=_career_end(scope.season))
        if isinstance(found, TemplateResult):
            return found
        resolved.append(found)
    a, b = resolved
    if a.id == b.id:
        raise TemplateUnsupported("the named players resolved to the same person")

    meetings, together = _meetings(con, scope, a.id, b.id)
    unseen = _unseen_meetings(con, scope, a.id, b.id)
    caveat = (
        f" {unseen} game{'' if unseen == 1 else 's'} between their teams while both were playing for them {'has' if unseen == 1 else 'have'} no box score, so a meeting there is not counted."
        if unseen
        else ""
    )
    if not meetings:
        for player in (a, b):
            if _totals(con, _player_games(scope), {**scope.params(), "player": player.id})[0] == 0:
                return _no_games(con, player, scope, None)
        teammates = f" - they were teammates in all {together} games they both played" if together else ""
        message = f"{a.name} and {b.name} never played against each other {_where_in(scope)}{teammates}.{caveat}"
        return TemplateResult(data={"players": [a.name, b.name], "meetings": 0, "teammate_games": together}, answer=message)

    wins = sum(1 for m in meetings if m["won"])
    lines = {a.name: _matchup_line([m["a"] for m in meetings]), b.name: _matchup_line([m["b"] for m in meetings])}
    label = scope.label(min(m["season"] for m in meetings), max(m["season"] for m in meetings))
    count = len(meetings)
    title = f"{a.name} vs {b.name}, {label}: {count} meeting{'' if count == 1 else 's'}, {a.name}'s team won {wins}."
    summary = [("wins", [str(wins), str(count - wins)])] + [
        (header, [_cell(lines[p.name][key]) for p in (a, b)]) for key, header in (("minutes", "minutes"), ("points", "points"), ("rebounds", "rebounds"), ("assists", "assists"), ("fg_pct", "FG%"))
    ]
    shown = meetings[: _clamp_limit(slots.get("limit"), _DEFAULT_MEETINGS_LOGGED)]
    abbr = _names(con, "teams", "team_id", {m["team_id"] for m in shown} | {m["opponent_team_id"] for m in shown}, column="abbreviation")

    def line(stats: dict[str, Any]) -> str:
        """One player's points/rebounds/assists in one meeting, for the log."""
        return f"{stats['points']}/{stats['rebounds']}/{stats['assists']}"

    log = [(str(m["day"]), [f"{abbr[m['team_id']]} {m['team_score']}-{m['opponent_score']} {abbr[m['opponent_team_id']]}", line(m["a"]), line(m["b"])]) for m in shown]
    answer = _table(title, [a.name, b.name], summary)
    answer += "\n\n" + _table(f"Most recent {len(shown)} of {count} (points/rebounds/assists):", ["score", a.name, b.name], log)
    answer += f"\n{caveat.strip()}" if caveat else ""
    games = [{"date": str(m["day"]), "won": m["won"], "team_score": m["team_score"], "opponent_score": m["opponent_score"], a.name: m["a"], b.name: m["b"]} for m in shown]
    data = {"players": [a.name, b.name], "span": label, "meetings": count, "wins": {a.name: wins, b.name: count - wins}, "averages": lines, "games": games}
    return TemplateResult(data=data, answer=answer)


def streak(ctx: TemplateContext, slots: dict[str, Any]) -> TemplateResult:
    """The longest run of consecutive games meeting a condition.

    For a team, its longest winning or losing run (``kind``) - within one
    season, as the record book counts them. With no team named, the league's
    longest, one per team-season ("longest winning streak in the NBA this
    season"). For a player, his longest run of games with ``stat`` at or above
    ``threshold`` ("most 40 point games in a row"), or with no stat his team's
    longest run of wins in games he played; with no player named, the league's
    longest such run. A player's run counts only games he played - a game he
    missed neither extends it nor ends it - and in a career it carries across
    seasons, as consecutive-game records do (Curry's 3-pointer streak ran
    through four of them). Each answer says which of those rules applied.

    Games are ordered by their US Eastern date, so two nights either side of
    midnight UTC land in the order they were played.

    .. versionadded:: 2.1.0
    """
    con = ctx.con
    want_win = slots.get("kind") != "loss"
    stat, threshold = slots.get("stat"), slots.get("threshold")
    column = THRESHOLD_STAT_COLUMNS.get(stat) if isinstance(stat, str) else None
    named_stat = isinstance(stat, str) and bool(stat.strip()) and stat.strip().casefold() not in _RESULT_STATS
    has_threshold = isinstance(threshold, int) and not isinstance(threshold, bool)
    if named_stat and column is None:
        raise TemplateUnsupported(f"no per-game column for stat {stat!r}")
    if has_threshold != (column is not None) or (isinstance(threshold, int) and threshold < 1):
        raise TemplateUnsupported(f"a streak of a stat needs both a known stat and a positive threshold, got {stat!r}/{threshold!r}")
    by_stat = column is not None
    unit = f"{STAT_LABELS.get(stat or '', stat or '')}s"
    result = "winning streak" if want_win else "losing streak"
    # A player's rows carry the stat as `value` (see conditions._player_streak_rows); a team's carry only `won`.
    hit = "x.value >= $threshold" if by_stat else "x.won = $want"
    condition: dict[str, Any] = {"threshold": threshold} if by_stat else {"want": want_win}
    span = slots.get("span")
    team = _optional_team(con, slots.get("team"))
    if isinstance(team, TemplateResult):
        return team

    name = slots.get("player")
    if isinstance(name, str) and name.strip():
        scope = _condition_scope(slots.get("season"), span, slots.get("season_type"), _PLAYER_GAME_TABLES)
        player = _resolved_player(con, name, available=_BOX_SCORES, season=scope.season, through=_career_end(scope.season))
        if isinstance(player, TemplateResult):
            return player
        params: dict[str, Any] = {**scope.params(), "player": player.id}
        if team is not None:
            params["team"] = team.id
        base = _player_games(scope, extra=" AND pbs.team_id = $team" if team else "")
        games, first, last = _totals(con, base, params)
        if not games:
            return _no_games(con, player, scope, team)
        rows_sql = _player_streak_rows(scope, base, f"p.{column}" if by_stat else "NULL")
        runs = _longest_runs(con, rows_sql, {**params, **condition}, ("athlete_id",), hit, 3, best_per_partition=False)
        label = scope.label(first, last)
        what = f"consecutive games with {threshold}+ {unit}" if by_stat else f"{result} in games he played"
        rule = "Only games he played count: a game he missed neither extends the run nor ends it" + (", and a run carries on from one season into the next." if scope.season is None else ".")
        rule += _UNSEEN_ENDS_RUN if _unseen(con, scope, base, params) else ""
        if not runs:
            never = f"never had a game with {threshold}+ {unit}" if by_stat else f"never {'won' if want_win else 'lost'} a game he played"
            return TemplateResult(data={"player": player.name, "span": label, "streaks": []}, answer=f"{player.name} {never} in the {label}.")
        return _single_streak(f"{player.name}'s longest run of {what}" if by_stat else f"{player.name}'s longest {what}", label, runs, rule, scope, {"player": player.name})

    if team is not None:
        if by_stat:
            raise TemplateUnsupported("a team's streak is of wins or losses, not of a stat")
        scope = _condition_scope(slots.get("season"), span, slots.get("season_type"), _TEAM_GAME_TABLES)
        misfiled = _misfiled_postseason(scope)
        if misfiled is not None:
            return misfiled
        params = {**scope.params(), "team": team.id}
        base = _team_games(scope, " AND tbs.team_id = $team")
        games, first, last = _totals(con, base, params)
        label = scope.label(first, last)
        if not games:
            return TemplateResult(data={"team": team.name, "streaks": []}, answer=f"The warehouse has no games with a result for the {team.name} {_where_in(scope)}.")
        runs = _longest_runs(con, base, {**params, **condition}, ("team_id", "season"), hit, 3, best_per_partition=False)
        if not runs:
            return TemplateResult(data={"team": team.name, "span": label, "streaks": []}, answer=f"The {team.name} did not {'win' if want_win else 'lose'} a game in the {label}.")
        return _single_streak(
            f"The {team.name}' longest {result}" if team.name.endswith("s") else f"The {team.name}'s longest {result}",
            label,
            runs,
            "Streaks are counted within one season.",
            scope,
            {"team": team.name},
        )

    # Nobody named: the league's longest, each team-season or player once.
    tables = _PLAYER_GAME_TABLES if by_stat else _TEAM_GAME_TABLES
    scope = _condition_scope(slots.get("season"), span, slots.get("season_type"), tables)
    misfiled = _misfiled_postseason(scope)
    if misfiled is not None:
        return misfiled
    limit = _clamp_limit(slots.get("limit"), _DEFAULT_STREAK_LIMIT)
    if by_stat:
        base = _player_streak_rows(scope, _player_games(scope, player=""), f"p.{column}")
        runs = _longest_runs(con, base, {**scope.params(), **condition}, ("athlete_id",), hit, limit, best_per_partition=True)
        names = _names(con, "players", "athlete_id", [r["athlete_id"] for r in runs])
        what, who = f"run of consecutive games with {threshold}+ {unit}", [names[r["athlete_id"]] for r in runs]
        rule = "Each player's longest run, counting only games he played" + (", carried across seasons." if scope.season is None else ".")
        rule += _UNSEEN_ENDS_RUN if _totals(con, _box_missing(scope), scope.params())[0] else ""
    else:
        # Teams the `teams` table does not hold are exhibition opponents that
        # turn up in a few regular-season rows (1992-2000), not franchises.
        base = _team_games(scope, " AND tbs.team_id IN (SELECT team_id FROM teams)")
        runs = _longest_runs(con, base, {**scope.params(), **condition}, ("team_id", "season"), hit, limit, best_per_partition=True)
        names = _names(con, "teams", "team_id", [r["team_id"] for r in runs])
        what = result
        who = [names[r["team_id"]] + (f" ({r['season']})" if scope.season is None else "") for r in runs]
        rule = "Each team's longest in a season, counted within that season" + ("; franchises are named as they are today." if scope.season is None else ".")
    # The span searched, not the seasons the leaders' runs happen to fall in:
    # "1997-2023" under a question about every season reads as a narrower search.
    _, first, last = _totals(con, base, scope.params())
    label = scope.label(first, last)
    if not runs:
        nobody = f"No player had a game with {threshold}+ {unit}" if by_stat else "No team has a game with a result"
        return TemplateResult(data={"span": label, "streaks": []}, answer=f"{nobody} {_where_in(scope)}.")
    streaks = [
        {"name": n, "season": r["first_season"] if not by_stat else None, "length": r["length"], "from": str(r["first_day"]), "to": str(r["last_day"]), "open": bool(r["open"])}
        for n, r in zip(who, runs, strict=True)
    ]
    top = [s for s in streaks if s["length"] == streaks[0]["length"]]
    # A tie is reported as a tie, the way threshold_count reports one.
    leaders = " and ".join(s["name"] for s in top)
    headline = f"{leaders} {'shared' if len(top) > 1 else 'had'} the longest {what} of the {label}: {streaks[0]['length']} games."
    rows = [(s["name"], [str(s["length"]), s["from"], s["to"] + (" *" if s["open"] else "")]) for s in streaks]
    footnote = " * still going at the last game on record." if any(s["open"] for s in streaks) else ""
    answer = f"{headline}\n" + _table(f"Longest, {label}:", ["games", "from", "to"], rows) + f"\n{rule}{footnote}"
    return TemplateResult(
        data={"span": label, "stat": stat if by_stat else None, "threshold": threshold if by_stat else None, "kind": None if by_stat else ("win" if want_win else "loss"), "streaks": streaks},
        answer=answer,
    )


def _single_streak(subject: str, label: str, runs: list[dict[str, Any]], rule: str, scope: _Scope, who: dict[str, Any]) -> TemplateResult:
    """One named player's or team's longest run, with any run that ties it."""
    top = runs[0]
    ties = [r for r in runs[1:] if r["length"] == top["length"]]
    season = f" (the {top['first_season']} season)" if scope.season is None and top["first_season"] == top["last_season"] else ""
    answer = f"{subject}, {label}: {top['length']} games, {top['first_day']} to {top['last_day']}{season}."
    if ties:
        answer += " Matched by " + ", ".join(f"{r['first_day']} to {r['last_day']}" for r in ties) + "."
    if top["open"] and (scope.season is None or scope.season == current_season()):
        answer += " It was still going at the last game on record."
    streaks = [{"length": r["length"], "from": str(r["first_day"]), "to": str(r["last_day"]), "open": bool(r["open"])} for r in [top, *ties]]
    return TemplateResult(data={**who, "span": label, "streaks": streaks}, answer=f"{answer}\n{rule}")


TEMPLATES: dict[str, Callable[[TemplateContext, dict[str, Any]], TemplateResult]] = {
    "threshold_count": threshold_count,
    "leaderboard": leaderboard,
    "player_stat": player_stat,
    "team_record": team_record,
    "game_log": game_log,
    "shot_chart": shot_chart,
    "player_compare": player_compare,
    "single_game_high": single_game_high,
    "head_to_head": head_to_head,
    "team_quarter_points": team_quarter_points,
    "shot_distance": shot_distance,
    "player_history": player_history,
    "player_netpoints": player_netpoints,
    "fingerprint": fingerprint,
    "player_splits": player_splits,
    "with_without": with_without,
    "record_when": record_when,
    "player_matchup": player_matchup,
    "streak": streak,
    "team_stat": team_stat,
    "team_leaderboard": team_leaderboard,
    "team_outlook": team_outlook,
}
