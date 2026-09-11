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
from pathlib import Path
from typing import Any

import duckdb

from association.coverage import caveat, unavailable
from association.net_points_categories import FINGERPRINT_CATEGORIES
from association.season import current_season

from .answer import Artifact
from .court import HOOP_X, HOOP_Y
from .entities import Ambiguous, Entity, clarification, no_match, resolve_player, resolve_team, suggest_players, suggestion
from .fingerprint import FINGERPRINT_AVAILABILITY, FINGERPRINT_VIEWS, GAME_FINGERPRINT_AVAILABILITY, FingerprintUnavailable, render_for_players
from .leaderboard import LeaderboardError, resolve_metric, run_leaderboard
from .metrics import EXTRA_FIELD_COLUMNS, LEADERBOARD_METRICS, SEASON_TYPE_LABELS
from .shotchart import SHOT_AVAILABILITY, render_for_player, resolve_chart_player
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
# The four after those are read from the question text by the router and by
# entities.scope_from_question, never asked of the model, and exist for the same
# reason. Measured against real StatMuse queries before they did: "jaylen brown
# last 8 games vs pistons" answered with the Celtics' last 8 games, "Knicks
# home record" with their overall record, "career points leaders" with this
# season's, and "Podziemski game log without curry" with his whole log. Each
# was fast, fluent and about something else.
SCOPING_SLOTS = frozenset({"order", "date", "opponent", "venue", "span", "without"})

# What each template actually honours. Anything not listed here honours none.
HONORED_SCOPING: dict[str, frozenset[str]] = {
    "game_log": frozenset({"order", "date"}),
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
    "threshold_count": ("player_box_stats",),
    "single_game_high": ("player_game_log",),
    "player_stat": ("player_season_stats_deduped",),
    "player_compare": ("player_season_stats_deduped",),
    "player_history": ("player_season_stats_deduped",),
    "player_netpoints": ("net_points_player", "net_points_player_fingerprint"),
    "team_record": ("standings",),
    "head_to_head": ("games",),
    "team_quarter_points": ("team_box_stats", "games"),
    # A player's log and a team's come from different tables, and _sources_for
    # picks between them - a team question refused with "Player game logs only
    # go back to..." names the wrong thing.
    "game_log": ("games",),
    "shot_chart": ("shot_chart",),
    "shot_distance": ("shot_chart",),
    "fingerprint": ("net_points_player_fingerprint", "net_points_player_game_fingerprint"),
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


def _sources_for(intent: str, slots: dict[str, Any]) -> tuple[str, ...]:
    """The tables an answer would be built from, resolved per question because
    a leaderboard's depends on which metric was asked for."""
    if intent == "game_log":
        named_player = isinstance(slots.get("player"), str) and slots["player"].strip()
        return ("player_game_log",) if named_player else ("games", "team_box_stats")
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
    metric = resolve_metric(stat) if isinstance(stat, str) else None
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


def threshold_count(ctx: TemplateContext, slots: dict[str, Any]) -> TemplateResult:
    """ "Most games with N+ of some stat" - the shape that motivated this split.

    A KNOWLEDGE_BASE entry covered it, but sat in the truncated-away head of the
    prompt, so three consecutive runs answered with a season-averages
    leaderboard instead. In code it cannot be truncated or substituted."""
    con = ctx.con
    stat = slots.get("stat")
    column = THRESHOLD_STAT_COLUMNS.get(stat) if isinstance(stat, str) else None
    threshold = slots.get("threshold")
    if column is None or not isinstance(threshold, int):
        raise TemplateUnsupported(f"threshold_count needs a known stat and an integer threshold, got {stat!r}/{threshold!r}")

    season = slots.get("season") or current_season()
    season_type = slots.get("season_type") or 2
    limit = _clamp_limit(slots.get("limit"))
    player = slots.get("player")

    where = ["pbs.season = ?", "pbs.season_type = ?", f"pbs.{column} >= ?"]
    params: list[Any] = [season, season_type, threshold]
    if isinstance(player, str) and player.strip():
        # Every token must match, so "Luka Doncic" doesn't also match a player
        # sharing only a first name - same approach as render_shot_chart.
        for token in player.split():
            where.append("p.display_name ILIKE ?")
            params.append(f"%{token}%")
    params.append(limit)

    rows = con.execute(
        f"SELECT p.display_name, COUNT(*) AS games FROM player_box_stats pbs JOIN players p ON p.athlete_id = pbs.athlete_id WHERE {' AND '.join(where)} GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT ?",
        params,
    ).fetchall()

    label = STAT_LABELS.get(stat or "", stat or "")
    scope = f"{threshold}+ {label}s"
    period = _period(season, season_type)
    summary = f"games with {scope}, {period}"
    leaders = [{"player": name, "games": games} for name, games in rows]
    return TemplateResult(
        data={"question_shape": summary, "season": season, "leaders": leaders},
        answer=_phrase_threshold_count(rows, scope, period, filtered_to_one_player=bool(player)),
    )


def _period(season: int, season_type: int) -> str:
    return f"{season} {SEASON_TYPE_NAMES.get(season_type, 'regular season')}"


def _phrase_threshold_count(rows: list[tuple[Any, ...]], scope: str, period: str, filtered_to_one_player: bool) -> str:
    """Always names the season outright rather than echoing "this season" back.
    The original failure answered for 2024 while the user meant the current
    season, and said nothing about it - so the season is stated, every time."""
    label = f"games with {scope}"
    if not rows:
        if filtered_to_one_player:
            return f"That player had no {label} in the {period}."
        return f"No player had a game with {scope} in the {period}."
    if filtered_to_one_player:
        name, games = rows[0]
        return f"{name} had {games} {label} in the {period}."

    top = rows[0][1]
    tied = [name for name, games in rows if games == top]
    if len(tied) > 1:
        leaders = ", ".join(tied[:-1]) + f" and {tied[-1]}"
        sentence = f"{leaders} tied for the most {label} in the {period}, with {top} each."
    else:
        sentence = f"{rows[0][0]} had the most {label} in the {period}, with {top}."
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
    the same function. This adds slot mapping and phrasing."""
    con = ctx.con
    metric = resolve_metric(slots.get("stat"))
    if metric is None:
        raise TemplateUnsupported(f"no leaderboard metric for stat {slots.get('stat')!r}")
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
    answer = (
        _tabulate_leaderboard(result.rows, result.label, where, period, fields, result.min_sample_applied, result.min_sample_column)
        if fields
        else _phrase_leaderboard(result.rows, result.label, where, period)
    )
    return TemplateResult(
        data={"question_shape": summary, "season": result.season, "fields": fields, "leaders": result.rows},
        answer=answer,
    )


def _phrase_leaderboard(rows: list[dict[str, Any]], label: str, where: str, period: str) -> str:
    if not rows:
        return f"No players qualified for {label} in {where} in the {period}."
    top = rows[0]
    sentence = f"{top['display_name']} led {where} in {label} in the {period}, at {_format_value(top['value'])}."
    rest = [f"{r['display_name']} ({_format_value(r['value'])})" for r in rows[1:]]
    return sentence + (f" Next: {', '.join(rest)}." if rest else "")


# The qualifying column's real name is not something to put in front of a
# reader ("total_minutes", "gamesPlayed").
MIN_SAMPLE_LABELS = {"total_minutes": "minutes", "gamesPlayed": "games", "games_played": "games", "minutes": "minutes"}


def _tabulate_leaderboard(
    rows: list[dict[str, Any]],
    label: str,
    where: str,
    period: str,
    fields: list[str],
    min_sample: int | None,
    min_sample_column: str | None,
) -> str:
    """A table once extra columns are asked for - a sentence carrying three
    numbers per player across ten players is unreadable, and the qualifying
    minimum belongs on screen so "why isn't X here?" has a visible answer."""
    if not rows:
        return f"No players qualified for {label} in {where} in the {period}."
    unit = MIN_SAMPLE_LABELS.get(min_sample_column or "", min_sample_column or "")
    header_note = f" (minimum {min_sample} {unit})".rstrip() if min_sample else ""
    columns = [(label, "value")] + [(f, f) for f in fields]
    name_width = max(len(r["display_name"]) for r in rows)
    # The ranked metric keeps its own precision (9.91, not 9.9); the extra
    # box-score columns are per-game averages, where one decimal is the norm.
    cell = lambda row, key: _format_value(row[key]) if key == "value" else _table_cell(row.get(key))  # noqa: E731
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
    """One player's stat across several seasons.

    Every other template answers about a single season, so a multi-season
    question routed to `leaderboard`, which dropped the player and ranked the
    league. Distinct from the other gaps here: a missing DIMENSION cutting
    across the shapes that existed, not a missing shape."""
    con = ctx.con
    player = _resolved_player(con, slots.get("player"), "player_history needs a player name")
    if isinstance(player, TemplateResult):
        return player

    stat = slots.get("stat")
    if not isinstance(stat, str) or stat not in HISTORY_COLUMNS:
        raise TemplateUnsupported(f"no per-season history for stat {stat!r}")
    label, columns = HISTORY_COLUMNS[stat]

    season_type = slots.get("season_type") or 2
    limit = slots.get("limit")
    seasons = limit if isinstance(limit, int) and 1 <= limit <= MAX_HISTORY_SEASONS else DEFAULT_HISTORY_SEASONS
    # A named season anchors the range's END rather than replacing it, so
    # "3pt% over the 4 seasons through 2024" still spans four rows.
    latest = slots.get("season") or current_season()

    rows = con.execute(
        f"SELECT season, gamesPlayed, {', '.join(c for c, _ in columns)} FROM player_season_stats_deduped WHERE athlete_id = ? AND season_type = ? AND season <= ? ORDER BY season DESC LIMIT ?",
        [player.id, season_type, latest, seasons],
    ).fetchall()

    period = SEASON_TYPE_NAMES.get(season_type, "regular season")
    history = [dict(zip(["season", "games"] + [c for c, _ in columns], r, strict=True)) for r in rows]
    return TemplateResult(
        data={"player": player.name, "stat": stat, "seasons": history},
        answer=_phrase_history(player.name, label, period, history, columns),
    )


def _phrase_history(name: str, label: str, period: str, history: list[dict[str, Any]], columns: list[tuple[str, str]]) -> str:
    if not history:
        return f"The warehouse has no {period} seasons on record for {name}."
    headers = ["season", "G"] + [h for _, h in columns]
    keys = ["season", "games"] + [c for c, _ in columns]
    widths = [max(len(h), *(len(_table_cell(row.get(k))) for row in history)) for h, k in zip(headers, keys, strict=True)]
    lines = [f"{name}, {label} by {period} (most recent first):", "  ".join(h.rjust(w) for h, w in zip(headers, widths, strict=True))]
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


def player_stat(ctx: TemplateContext, slots: dict[str, Any]) -> TemplateResult:
    """One named player's season numbers, from player_season_stats_deduped so
    a traded player's multi-row season is already collapsed.

    An incomplete name ("Luka", "Curry") is answered with a question, not a
    guess: falling through costs minutes and guesses anyway, and a prominence
    tiebreak was measured and rejected (no threshold separates Luka Doncic from
    Luka Garza without also wrongly resolving "Brown")."""
    con = ctx.con
    player = _resolved_player(con, slots.get("player"), "player_stat needs a player name")
    if isinstance(player, TemplateResult):
        return player

    season = slots.get("season") or current_season()
    season_type = slots.get("season_type") or 2
    wanted = _wanted_stats(slots)

    columns = ["gamesPlayed"]
    for name in wanted:
        per_game, total, _ = PLAYER_STAT_COLUMNS[name]
        columns.append(per_game)
        if total:
            columns.append(total)
    row = _season_row(con, player.id, columns, season, season_type)

    period = _period(season, season_type)
    if row is None:
        return TemplateResult(
            data={"player": player.name, "season": season, "stats": {}},
            answer=f"{player.name} has no {period} numbers in the warehouse.",
        )
    values = dict(zip(columns, row, strict=True))
    return TemplateResult(
        data={"player": player.name, "season": season, "stats": values},
        answer=_phrase_player_stat(player.name, period, values, wanted),
    )


def _phrase_player_stat(name: str, period: str, values: dict[str, Any], wanted: list[str]) -> str:
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
    played = f" in {games} games" if games else ""
    sentence = f"{name} averaged {body} per game{played} in the {period}."
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
_TEAM_GAMES_SQL = """
SELECT g.date,
       tbs.home_away,
       opp.display_name AS opponent,
       CASE WHEN tbs.home_away = 'home' THEN g.home_score ELSE g.away_score END AS team_score,
       CASE WHEN tbs.home_away = 'home' THEN g.away_score ELSE g.home_score END AS opponent_score,
       g.winner_team_id = tbs.team_id AS won
FROM team_box_stats tbs
JOIN games g ON g.event_id = tbs.event_id
JOIN teams opp ON opp.team_id = tbs.opponent_team_id
WHERE tbs.team_id = ? AND tbs.season = ? AND tbs.season_type = ?
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


def _pct(value: float) -> str:
    """Basketball convention for a winning percentage: .646, not 0.646."""
    text = f"{value:.3f}"
    return text[1:] if text.startswith("0") else text


def _season_name(season: int) -> str:
    """2026 -> "2025-26". Used wherever a range is stated, where a bare year
    would leave a reader guessing which end of the season it names."""
    return f"{season - 1}-{season % 100:02d}"


def _parse_record(text: Any) -> tuple[int, int] | None:
    """standings' "Home"/"Road" strings: '30-10' -> (30, 10)."""
    if not isinstance(text, str):
        return None
    match = re.fullmatch(r"\s*(\d+)-(\d+)\s*", text)
    return (int(match.group(1)), int(match.group(2))) if match else None


def _tally(wins: int, losses: int) -> str:
    return f"{wins:,}-{losses:,} ({_pct(wins / (wins + losses))})" if wins + losses else "0-0"


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
        answer += f" ({_pct(win_pct)})"
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


def _no_games(con: duckdb.DuckDBPyConnection, team: Entity, opponent: Entity | None, season: int | None, season_type: int) -> str:
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
        return TemplateResult(data={**data, "games": []}, answer=_no_games(con, team, opponent, season, season_type))

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
        lines_out.append(f"  strength of schedule {_pct(sos)}{rank_note}")
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


def game_log(ctx: TemplateContext, slots: dict[str, Any]) -> TemplateResult:
    """A team's or a player's games. Both orderings are explicit: "first game"
    and "last game" differ only by ORDER BY direction, and LIMIT 1 without one
    returns an arbitrary row rather than either."""
    con = ctx.con
    season = slots.get("season") or current_season()
    season_type = slots.get("season_type") or 2
    limit = _clamp_limit(slots.get("limit"), default=DEFAULT_GAME_LOG_LIMIT)
    ascending = slots.get("order") == "first"
    date = slots.get("date") if isinstance(slots.get("date"), str) and _ISO_DATE.match(slots.get("date", "")) else None
    period = _period(season, season_type)

    if slots.get("team"):
        team = _resolved_team(con, slots.get("team"))
        if isinstance(team, TemplateResult):
            return team
        sql, params = _TEAM_GAMES_SQL, [team.id, season, season_type]
        if date:
            # games.date is a full ISO timestamp, so `= 'YYYY-MM-DD'` is valid
            # SQL that silently matches nothing.
            sql, params = sql + " AND g.date LIKE ?", [*params, f"{date}%"]
        rows = con.execute(f"{sql} ORDER BY g.date {'ASC' if ascending else 'DESC'} LIMIT ?", [*params, limit]).fetchall()
        return _team_game_log_result(team.name, period, rows, ascending, date)

    player = _resolved_player(con, slots.get("player"), "game_log needs a team or a player")
    if isinstance(player, TemplateResult):
        return player

    where = "athlete_id = ? AND season = ? AND season_type = ?"
    params = [player.id, season, season_type]
    if date:
        where, params = where + " AND game_date LIKE ?", [*params, f"{date}%"]
    rows = con.execute(
        f"SELECT game_date, opponent_abbr, minutes, points, rebounds, assists FROM player_game_log WHERE {where} ORDER BY game_date {'ASC' if ascending else 'DESC'} LIMIT ?",
        [*params, limit],
    ).fetchall()
    return _player_game_log_result(player.name, period, rows, ascending, date)


def _scope(count: int, ascending: bool, date: str | None) -> str:
    if date:
        return f"on {date}"
    if count == 1:
        return "first game" if ascending else "most recent game"
    return f"first {count} games" if ascending else f"last {count} games"


def _team_game_log_result(name: str, period: str, rows: list[tuple[Any, ...]], ascending: bool, date: str | None) -> TemplateResult:
    if not rows:
        return TemplateResult(data={"team": name, "games": []}, answer=f"No {period} games found for the {name}.")
    games = [{"date": str(r[0])[:10], "home_away": r[1], "opponent": r[2], "team_score": r[3], "opponent_score": r[4], "won": bool(r[5])} for r in rows]
    # Tallied here, over exactly the rows being shown, rather than left to be
    # counted back out of the listing - that recount is where a wins/losses
    # total gets inverted.
    wins = sum(1 for g in games if g["won"])
    header = f"{name}, {_scope(len(games), ascending, date)} of the {period} ({wins}-{len(games) - wins}):"
    lines = [f"  {g['date']}  {'W' if g['won'] else 'L'} {g['team_score']}-{g['opponent_score']}  {'vs' if g['home_away'] == 'home' else 'at'} {g['opponent']}" for g in games]
    return TemplateResult(data={"team": name, "wins": wins, "games": games}, answer="\n".join([header, *lines]))


def _player_game_log_result(name: str, period: str, rows: list[tuple[Any, ...]], ascending: bool, date: str | None) -> TemplateResult:
    if not rows:
        return TemplateResult(data={"player": name, "games": []}, answer=f"No {period} games found for {name}.")
    games = [{"date": str(r[0])[:10], "opponent": r[1], "minutes": r[2], "points": r[3], "rebounds": r[4], "assists": r[5]} for r in rows]
    header = f"{name}, {_scope(len(games), ascending, date)} of the {period}:"
    lines = [f"  {g['date']}  vs {g['opponent']}  {g['points']} pts, {g['rebounds']} reb, {g['assists']} ast" for g in games]
    return TemplateResult(data={"player": name, "games": games}, answer="\n".join([header, *lines]))


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


# Free throws carry NULL coordinates and must be excluded from distance math.


def shot_distance(ctx: TemplateContext, slots: dict[str, Any]) -> TemplateResult:
    """Average shot distance for one player, optionally by shot value.

    The agent wrote the right distance formula, then dropped both the 3-point
    filter and the season filter, reporting an all-shots all-seasons 16.94 as a
    current-season three-point figure (real answer 23.6). A fixed formula over
    known columns is template work."""
    con = ctx.con
    player = _resolved_player(con, slots.get("player"), "shot_distance needs a player name")
    if isinstance(player, TemplateResult):
        return player

    season = slots.get("season") or current_season()
    season_type = slots.get("season_type") or 2
    shot_value = _shot_value(slots)
    if shot_value == 1:
        raise TemplateUnsupported("free throws have no meaningful shot distance")

    where = ["athlete_id = ?", "season = ?", "season_type = ?", "coordinate_x IS NOT NULL"]
    params: list[Any] = [player.id, season, season_type]
    if shot_value is not None:
        where.append("points_attempted = ?")
        params.append(shot_value)
    game_note = ""
    if slots.get("order") in ("recent", "first"):
        found = _scoping_game(con, player.id, season, season_type, slots["order"])
        if found is None:
            raise TemplateUnsupported(f"no games found for {player.name}")
        where.append("event_id = ?")
        params.append(found[0])
        game_note = f" in his {'first' if slots['order'] == 'first' else 'most recent'} game ({str(found[1])[:10]})"
    row = con.execute(
        f"SELECT AVG(SQRT(POWER(coordinate_x - {HOOP_X}, 2) + POWER(coordinate_y - {HOOP_Y}, 2))), COUNT(*) FROM shot_chart WHERE {' AND '.join(where)}",
        params,
    ).fetchone()

    average, attempts = row or (None, 0)
    period = _period(season, season_type)
    kind = {2: "2-point ", 3: "3-point "}.get(shot_value or 0, "")
    if not attempts or average is None:
        answer = f"No {kind}shots with recorded coordinates for {player.name}{game_note} in the {period}."
    else:
        answer = f"{player.name}'s average {kind}shot distance{game_note or f' in the {period}'} was {average:.1f} feet, over {attempts:,} attempts with recorded coordinates."
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
    wording."""
    stat = slots.get("stat")
    column = THRESHOLD_STAT_COLUMNS.get(stat) if isinstance(stat, str) else None
    if column is None:
        raise TemplateUnsupported(f"single_game_high needs a known stat, got {stat!r}")

    season = slots.get("season") or current_season()
    season_type = slots.get("season_type") or 2
    limit = _clamp_limit(slots.get("limit"), default=DEFAULT_SINGLE_GAME_LIMIT)

    where = ["season = ?", "season_type = ?", f"{column} IS NOT NULL"]
    params: list[Any] = [season, season_type]
    text = slots.get("player")
    named_player: Entity | None = None
    # The player slot is optional here: unset means "the league".
    if isinstance(text, str) and text.strip():
        resolved = _resolved_player(ctx.con, text)
        if isinstance(resolved, TemplateResult):
            return resolved
        named_player = resolved
        where.append("athlete_id = ?")
        params.append(resolved.id)

    rows = ctx.con.execute(
        f"SELECT player_name, {column}, game_date, opponent_abbr FROM player_game_log WHERE {' AND '.join(where)} ORDER BY {column} DESC, game_date LIMIT ?",
        [*params, limit],
    ).fetchall()

    label = STAT_LABELS.get(stat or "", stat or "")
    period = _period(season, season_type)
    games = [{"player": r[0], "value": r[1], "date": str(r[2])[:10], "opponent": r[3]} for r in rows]
    # `question_shape` names the scope in the same form leaderboard and
    # threshold_count use it: a caption for a caller that renders the rows
    # itself and would otherwise have no way to say what season they are from
    # except by reusing the whole sentence, which already lists them.
    shape = f"most {label}s in a single game" + (f", {named_player.name}" if named_player else "") + f", {period}"
    return TemplateResult(
        data={"question_shape": shape, "season": season, "stat": stat, "games": games},
        answer=_phrase_single_game_high(games, label, period, named_player.name if named_player else None),
    )


def _phrase_single_game_high(games: list[dict[str, Any]], label: str, period: str, named_player: str | None) -> str:
    if not games:
        who = f"{named_player} has" if named_player else "There are"
        return f"{who} no {period} games in the warehouse."
    top = games[0]
    where = f" vs {top['opponent']}" if top["opponent"] else ""
    if named_player:
        return f"{named_player}'s highest {label} total in a single game in the {period} was {top['value']}, on {top['date']}{where}."

    tied = [g for g in games if g["value"] == top["value"]]
    if len(tied) > 1:
        names = ", ".join(g["player"] for g in tied[:-1]) + f" and {tied[-1]['player']}"
        sentence = f"{names} tied for the most {label}s in a single game in the {period}, with {top['value']} each."
    else:
        sentence = f"{top['player']} had the most {label}s in a single game in the {period}: {top['value']}, on {top['date']}{where}."
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
    "team_stat": team_stat,
    "team_leaderboard": team_leaderboard,
    "team_outlook": team_outlook,
}
