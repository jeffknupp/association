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
from .court import HOOP_X, HOOP_Y
from .entities import Ambiguous, Entity, clarification, no_match, resolve_player, resolve_team, suggest_players, suggestion
from .fingerprint import FINGERPRINT_AVAILABILITY, FINGERPRINT_VIEWS, GAME_FINGERPRINT_AVAILABILITY, FingerprintUnavailable, render_for_players
from .leaderboard import LeaderboardError, resolve_metric, run_leaderboard
from .metrics import EXTRA_FIELD_COLUMNS, LEADERBOARD_METRICS, SEASON_TYPE_LABELS
from .shotchart import SHOT_AVAILABILITY, render_for_player, resolve_chart_player

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
    # A career is every season on record rather than the current one; see
    # _condition_scope. `without` is the teammate with_without divides by.
    "player_splits": frozenset({"span"}),
    "with_without": frozenset({"span", "without"}),
    "record_when": frozenset({"span"}),
    "player_matchup": frozenset({"span"}),
    "streak": frozenset({"span"}),
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
    # A team's splits and streaks read only the team tables; _sources_for
    # picks between the two per question.
    "player_splits": _PLAYER_GAME_TABLES,
    "with_without": _PLAYER_GAME_TABLES,
    "record_when": _PLAYER_GAME_TABLES,
    "player_matchup": _PLAYER_GAME_TABLES,
    "streak": _PLAYER_GAME_TABLES,
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


def _sources_for(intent: str, slots: dict[str, Any]) -> tuple[str, ...]:
    """The tables an answer would be built from, resolved per question because
    a leaderboard's depends on which metric was asked for."""
    if intent == "game_log":
        named_player = isinstance(slots.get("player"), str) and slots["player"].strip()
        return ("player_game_log",) if named_player else ("games", "team_box_stats")
    if intent in ("player_splits", "streak"):
        # A team's splits or streak never touch a player box score, and
        # charging them that table's floor would refuse a 1990 playoff question
        # with a sentence about player box scores - the wrong cause.
        named_player = isinstance(slots.get("player"), str) and slots["player"].strip()
        by_player = named_player or (intent == "streak" and isinstance(slots.get("threshold"), int))
        return _PLAYER_GAME_TABLES if by_player else _TEAM_GAME_TABLES
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
        player = _resolved_player(con, name)
        if isinstance(player, TemplateResult):
            return player
        scope = _condition_scope(slots.get("season"), slots.get("span"), slots.get("season_type"), _PLAYER_GAME_TABLES)
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

    mate = _resolved_player(con, mate_text, "with_without needs a teammate")
    if isinstance(mate, TemplateResult):
        return mate
    subjects: list[Entity] = []
    for text in texts:
        found = _resolved_player(con, text)
        if isinstance(found, TemplateResult):
            return found
        # The router often repeats the teammate in `player`; that is not a subject.
        if found.id != mate.id and found.id not in {s.id for s in subjects}:
            subjects.append(found)
    if len(subjects) > 1:
        raise TemplateUnsupported(f"with_without answers for one player, got {[s.name for s in subjects]}")
    subject = subjects[0] if subjects else None

    scope = _condition_scope(slots.get("season"), slots.get("span"), slots.get("season_type"), _PLAYER_GAME_TABLES)
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
    player = _resolved_player(con, slots.get("player"), "record_when needs a player")
    if isinstance(player, TemplateResult):
        return player
    team = _optional_team(con, slots.get("team"))
    if isinstance(team, TemplateResult):
        return team

    scope = _condition_scope(slots.get("season"), slots.get("span"), slots.get("season_type"), _PLAYER_GAME_TABLES)
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
    resolved: list[Entity] = []
    for text in texts:
        found = _resolved_player(con, text)
        if isinstance(found, TemplateResult):
            return found
        resolved.append(found)
    a, b = resolved
    if a.id == b.id:
        raise TemplateUnsupported("the named players resolved to the same person")

    scope = _condition_scope(slots.get("season"), slots.get("span"), slots.get("season_type"), _PLAYER_GAME_TABLES)
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
        player = _resolved_player(con, name)
        if isinstance(player, TemplateResult):
            return player
        scope = _condition_scope(slots.get("season"), span, slots.get("season_type"), _PLAYER_GAME_TABLES)
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
}
