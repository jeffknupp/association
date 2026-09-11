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

from .answer import Artifact
from .court import HAS_POSITION_SQL, SHOT_DISTANCE_SQL
from .entities import Ambiguous, Entity, clarification, find_players, no_match, resolve_player, resolve_team, suggest_players, suggestion
from .fingerprint import FINGERPRINT_AVAILABILITY, FINGERPRINT_VIEWS, GAME_FINGERPRINT_AVAILABILITY, FingerprintUnavailable, render_for_players
from .leaderboard import SEASON_TOTAL_OF, LeaderboardError, not_a_postseason_copy, resolve_metric, run_career_leaderboard, run_leaderboard
from .metrics import EXTRA_FIELD_COLUMNS, LEADERBOARD_METRICS, SEASON_TYPE_LABELS
from .shotchart import DERIVED_SHOT_VALUES, SHOT_AVAILABILITY, SHOT_VALUE_SQL, UNSEPARABLE_SHOT_VALUES, render_for_player, resolve_chart_player

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
SCOPING_SLOTS = frozenset({"order", "date", "opponent", "venue", "span", "without", "round", "split", "since"})

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
        "player_netpoints",
        "player_stat",
        "shot_chart",
        "shot_distance",
        "single_game_high",
        "team_quarter_points",
        "threshold_count",
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
RANKING_INTENTS = frozenset({"leaderboard", "threshold_count", "single_game_high"})


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


def _clarify(text: str, candidates: list[str], kind: str = "player") -> TemplateResult:
    """A handled outcome, not a fall-through: the template knows exactly what
    is ambiguous, so it says so instead of passing the problem along.

    The sentence itself is entities.clarification, because the chart entry
    points reach the same ambiguity without going through a template and have
    to phrase it identically."""
    return TemplateResult(data={"ambiguous": text, "candidates": candidates}, answer=clarification(text, candidates, kind))


def _resolved_player(con: duckdb.DuckDBPyConnection, text: Any, missing: str = "no player named") -> Entity | TemplateResult:
    """One player, a clarifying question, or a refusal - the player counterpart
    to _resolved_team. Returning the TemplateResult rather than raising it keeps
    ambiguity a handled outcome: the caller answers with the question instead of
    falling through to an agent that would guess. Callers must forward it."""
    if not isinstance(text, str) or not text.strip():
        raise TemplateUnsupported(missing)
    match resolve_player(con, text):
        case Entity() as player:
            return player
        case Ambiguous(candidates=candidates):
            return _clarify(text, candidates)
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


def _eastern_date(stamp: Any) -> str:
    """The calendar day a game was played, from its stored UTC timestamp."""
    text = str(stamp)
    if "T" not in text:
        return text[:10]
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text[:10]
    return (moment - _EASTERN_SHIFT).date().isoformat()


def threshold_count(ctx: TemplateContext, slots: dict[str, Any]) -> TemplateResult:
    """ "Most games with N+ of some stat" - the shape that motivated this split.

    A KNOWLEDGE_BASE entry covered it, but sat in the truncated-away head of the
    prompt, so three consecutive runs answered with a season-averages
    leaderboard instead. In code it cannot be truncated or substituted.

    .. versionchanged:: 2.1.0
       Honours ``span`` "career": every box score since 1993-94, for the league
       or for one player, saying which. A named player is resolved to one
       person; every player whose name contained the words used to be counted,
       and the top one reported.
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
    # and rejected, applied silently.
    player: Entity | None = None
    if isinstance(slots.get("player"), str) and slots["player"].strip():
        resolved = _resolved_player(con, slots["player"])
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
    player = _resolved_player(con, slots.get("player"), "player_netpoints needs a player name")
    if isinstance(player, TemplateResult):
        return player

    season = slots.get("season") or current_season()
    season_type = slots.get("season_type") or 2

    # "NetPoints from his LAST regular season game" was answered with the whole
    # season - 43 games - because nothing scoped it. Per-game NetPoints live in
    # their own table, with no fingerprint breakdown, so this is a different
    # answer rather than a filtered one.
    if slots.get("order") in ("recent", "first"):
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
    game = {"event_id": event_id, "date": str(date)[:10], "offense": o, "defense": d, "total": t}
    detail = []
    if o_poss is not None and d_poss is not None:
        detail.append(f"{o_poss:.0f} offensive and {d_poss:.0f} defensive possessions")
    if wpa is not None:
        detail.append(f"{wpa:+.3f} win probability added")
    answer = f"{player.name}, NetPoints in his {which} {period} game ({str(date)[:10]}): {_table_cell(t)} total ({_table_cell(o)} offense, {_table_cell(d)} defense)."
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
    player = _resolved_player(con, slots.get("player"), "player_history needs a player name")
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
    # A named season anchors the range's END rather than replacing it, so
    # "3pt% over the 4 seasons through 2024" still spans four rows.
    latest = slots.get("season") or current_season()

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


def _stints(con: duckdb.DuckDBPyConnection, athlete_id: str) -> list[tuple[int, str, str, str]]:
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
    stints = [s for s in _stints(con, mate.id) if span.season is None or s[0] == span.season]
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
        candidates = find_players(con, text)
        shared = _teammates_among(con, candidates, player, span)
        if len(shared) > 1:
            return _clarify(text, [c.name for c in shared])
        if not shared:
            message = f"No player matching {text!r} was {player.name}'s teammate {span.during()}."
            return TemplateResult(data={"unmatched": text, "candidates": [c.name for c in candidates]}, answer=message)
        resolved = shared[0]
    if not isinstance(resolved, Entity):
        found = _resolved_player(con, text)  # a suggestion, or a refusal
        if isinstance(found, TemplateResult):
            return found
        resolved = found
    if resolved.id == player.id:
        raise TemplateUnsupported(f"{player.name} cannot play without himself")
    return resolved


def _no_games(con: duckdb.DuckDBPyConnection, player: Entity, span: _Span, narrowed: _Narrowed) -> str:
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
    player = _resolved_player(con, slots.get("player"), "player_stat needs a player name")
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
    season_type = slots.get("season_type") or 2
    opponent, venue, without = slots.get("opponent"), slots.get("venue"), slots.get("without")
    from_box_scores = bool(opponent or venue or without)
    span = _span_of(slots.get("span"), slots.get("season"), season_type, "player_game_log" if from_box_scores else "player_season_stats_deduped")

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
        message = _no_games(con, player, span, narrowed)
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
       tbs.season
FROM team_box_stats tbs
JOIN games g ON g.event_id = tbs.event_id AND g.season = tbs.season
JOIN teams opp ON opp.team_id = tbs.opponent_team_id
"""


def _as_int(value: Any) -> str:
    return str(int(value)) if isinstance(value, (int, float)) else str(value)


def _ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def team_record(ctx: TemplateContext, slots: dict[str, Any]) -> TemplateResult:
    """A team's win-loss record, read from standings rather than tallied from
    games - standings is the authoritative season record and carries streak and
    seed alongside it. It has no season_type column, so a playoff-record
    question falls through rather than being answered with the regular-season
    number under a playoff-sounding label."""
    con = ctx.con
    team = _resolved_team(con, slots.get("team"))
    if isinstance(team, TemplateResult):
        return team
    if (slots.get("season_type") or 2) != 2:
        raise TemplateUnsupported("standings covers the regular season only")
    if slots.get("limit"):
        # "how did they do in their last 10 games?" is a game_log question -
        # standings only has the full-season record, and answering with it
        # under a "last 10" question is a silent substitution. game_log already
        # tallies the record over exactly the games it lists.
        raise TemplateUnsupported("a record over a limited set of games is a game_log question")

    season = slots.get("season") or current_season()
    row = con.execute(
        "SELECT wins, losses, winPercent, streak, playoffSeed FROM standings WHERE team_id = ? AND season = ?",
        [team.id, season],
    ).fetchone()
    if row is None:
        return TemplateResult(
            data={"team": team.name, "season": season},
            answer=f"There are no {season} standings for the {team.name} in the warehouse.",
        )
    wins, losses, win_pct, streak, seed = row
    # standings stores these as DOUBLE; reporting a 53-29 record as "53.0-29.0"
    # is the kind of detail that makes a correct answer look untrustworthy.
    answer = f"The {team.name} were {_as_int(wins)}-{_as_int(losses)} in the {season} regular season"
    if win_pct is not None:
        # Basketball convention: .646, not 0.646.
        answer += f" ({win_pct:.3f}".replace("(0.", "(.") + ")"
    extras = []
    if seed:
        extras.append(f"{_ordinal(int(seed))} seed")
    if streak:
        extras.append(f"{'won' if streak > 0 else 'lost'} {abs(int(streak))} straight")
    answer += f", {', '.join(extras)}." if extras else "."
    return TemplateResult(
        data={"team": team.name, "season": season, "wins": wins, "losses": losses, "win_pct": win_pct},
        answer=answer,
    )


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

    player = _resolved_player(con, slots.get("player"), "game_log needs a team or a player")
    if isinstance(player, TemplateResult):
        return player
    extras = _log_extras(slots.get("stat"))
    scope = _span_of(span, season, season_type, "player_game_log")
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


def _team_game_log(con: duckdb.DuckDBPyConnection, team: Entity, span: _Span, *, opponent: Any, venue: Any, date: str | None, limit: int, ascending: bool) -> TemplateResult:
    """A team's games in ``span``, narrowed to an opponent, a venue and a date
    where the question named them."""
    clause, params = span.clause("tbs.season")
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
            f"SELECT COUNT(*), MIN(tbs.season), MAX(tbs.season) FROM team_box_stats tbs WHERE {' AND '.join(base)}",
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
        message = _no_games(con, player, span, narrowed)
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
        return _clarify(name, resolved.candidates)
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
            return _clarify(name, found.candidates)
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
    player = _resolved_player(con, slots.get("player"), "shot_distance needs a player name")
    if isinstance(player, TemplateResult):
        return player

    season = slots.get("season") or current_season()
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
        game_note = f" in his {'first' if slots['order'] == 'first' else 'most recent'} game ({str(found[1])[:10]})"
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
        resolved = _resolved_player(ctx.con, text)
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
    if len(set(names)) < 2:
        raise TemplateUnsupported("head_to_head needs two team names")

    resolved: list[Entity] = []
    for name in names[:2]:
        team = _resolved_team(con, name)
        if isinstance(team, TemplateResult):
            return team
        if team.id not in {t.id for t in resolved}:
            resolved.append(team)
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
    where = [
        "((g.home_team_id = ? AND g.away_team_id = ?) OR (g.home_team_id = ? AND g.away_team_id = ?))",
        "g.season_type = ?",
        "g.season = ?",
    ]
    params: list[Any] = [a.id, b.id, b.id, a.id, season_type, season]
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
JOIN games g ON g.event_id = tbs.event_id
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
    where = ["tbs.team_id = ?", "tbs.season = ?", "tbs.season_type = ?"]
    params: list[Any] = [team.id, season, season_type]
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
        games.append({"date": str(date)[:10], "opponent": opp_name, "points": points})

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

    resolved: list[Entity] = []
    for name in names[:MAX_COMPARED_PLAYERS]:
        player = _resolved_player(con, name)
        if isinstance(player, TemplateResult):
            return player
        if player.id not in {p.id for p in resolved}:
            resolved.append(player)
    if len(resolved) < 2:
        raise TemplateUnsupported("the named players resolved to the same person")

    season = slots.get("season") or current_season()
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
}
