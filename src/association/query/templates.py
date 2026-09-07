"""Deterministic query templates: the second half of the router/template split
described in router.py.

Given an intent and its slots, these build and run the SQL themselves. Every
correctness rule they need lives here, in code, rather than as prose the model
has to re-derive per query - the same move get_leaderboard already made for
"top N by metric", extended to the other recurring question shapes.

Only intents present in TEMPLATES are handled; anything else (including a
recognized intent whose slots don't validate) falls through to the full agent
untouched. That is what makes the migration incremental: a shape is ported by
adding a function here, and its KNOWLEDGE_BASE entry then becomes deletable."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import duckdb

from association.season import current_season

from .entities import Ambiguous, Entity, resolve_player
from .leaderboard import LeaderboardError, resolve_metric, run_leaderboard

# Slot value -> real player_box_stats column. A whitelist, not a passthrough:
# the router's `stat` slot is model-generated text, and this is the only place
# it can reach SQL. Same reasoning as toolbox.EXTRA_FIELD_COLUMNS.
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
}

DEFAULT_LIMIT = 5
DEFAULT_LEADERBOARD_LIMIT = 10
MAX_LIMIT = 50

# Named in every answer, so answering the wrong one is visible rather than silent.
SEASON_TYPE_NAMES = {1: "preseason", 2: "regular season", 3: "postseason"}


class TemplateUnsupported(Exception):
    """Raised when slots don't validate. The caller treats this exactly like an
    unrecognized intent - fall through to the agent - so a router slip
    degrades to the old (slow) path rather than to a wrong answer."""


@dataclass
class TemplateResult:
    """`answer`, when set, is the final prose and NO model call is made at all.

    That is worth more than it looks. ollama keeps one KV cache slot per model
    by default, so a second call with a different system prompt evicts the
    router's cached prefix - measured: three consecutive router calls run
    11.6s / 1.3s / 1.7s, but interleaving a narrator call puts the next router
    call back to 11.2s. A deterministic answer avoids the eviction AND removes
    the last place on the fast path where a number could be invented.

    `data` is the fallback for templates that don't phrase their own answer: it
    is what a narrator model would see, and deliberately carries no ids and no
    schema - only resolved names and numbers it can restate."""

    summary: str
    data: dict[str, Any]
    answer: str | None = None


def _clamp_limit(limit: Any, default: int = DEFAULT_LIMIT) -> int:
    if not isinstance(limit, int) or limit < 1:
        return default
    return min(limit, MAX_LIMIT)


def threshold_count(con: duckdb.DuckDBPyConnection, slots: dict[str, Any]) -> TemplateResult:
    """"Most games with N+ of some stat" - the shape that motivated this split.

    KNOWLEDGE_BASE already carried this exact pattern ("Single-game vs.
    season-total stats"), but it sat in the truncated-away head of the system
    prompt, so three consecutive runs answered a season-averages leaderboard
    instead and presented it as the answer. Encoded here it cannot be
    truncated, misremembered, or silently substituted."""
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
        f"SELECT p.display_name, COUNT(*) AS games FROM player_box_stats pbs "
        f"JOIN players p ON p.athlete_id = pbs.athlete_id "
        f"WHERE {' AND '.join(where)} GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT ?",
        params,
    ).fetchall()

    label = STAT_LABELS.get(stat or "", stat or "")
    scope = f"{threshold}+ {label}s"
    period = _period(season, season_type)
    summary = f"games with {scope}, {period}"
    leaders = [{"player": name, "games": games} for name, games in rows]
    return TemplateResult(
        summary=summary,
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


def _format_value(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.3f}".rstrip("0").rstrip(".") if abs(value) < 1 else f"{value:.2f}".rstrip("0").rstrip(".")
    return str(value)


def leaderboard(con: duckdb.DuckDBPyConnection, slots: dict[str, Any]) -> TemplateResult:
    """"Top N players by X" for the metrics in LEADERBOARD_METRICS.

    Thin on purpose: run_leaderboard already owns the season default, the
    per-metric minimum-sample floor and traded-player dedup, and the agent's
    get_leaderboard tool calls the same function. All this adds is the router
    slot mapping and deterministic phrasing."""
    metric = resolve_metric(slots.get("stat"))
    if metric is None:
        raise TemplateUnsupported(f"no leaderboard metric for stat {slots.get('stat')!r}")
    try:
        result = run_leaderboard(
            con,
            metric,
            season=slots.get("season"),
            season_type=slots.get("season_type") or 2,
            team=slots.get("team") if isinstance(slots.get("team"), str) else None,
            limit=_clamp_limit(slots.get("limit"), default=DEFAULT_LEADERBOARD_LIMIT),
        )
    except LeaderboardError as exc:
        # An ambiguous team, an unknown metric, or a table that needs a
        # warehouse flag - all reasons to fall through, never to guess.
        raise TemplateUnsupported(str(exc)) from exc

    period = _period(result.season, result.season_type or 2)
    where = f"the {result.team_name}" if result.team_name else "the league"
    summary = f"{result.label}, {period}"
    return TemplateResult(
        summary=summary,
        data={"question_shape": summary, "season": result.season, "leaders": result.rows},
        answer=_phrase_leaderboard(result.rows, result.label, where, period),
    )


def _phrase_leaderboard(rows: list[dict[str, Any]], label: str, where: str, period: str) -> str:
    if not rows:
        return f"No players qualified for {label} in {where} in the {period}."
    top = rows[0]
    sentence = f"{top['display_name']} led {where} in {label} in the {period}, at {_format_value(top['value'])}."
    rest = [f"{r['display_name']} ({_format_value(r['value'])})" for r in rows[1:]]
    return sentence + (f" Next: {', '.join(rest)}." if rest else "")


# Slot value -> (per-game column, season-total column or None, label).
# Reported together, so "how many points did X average" and "how many points
# did X score" don't have to be told apart from wording - a distinction the
# router got wrong more often than it got right.
PLAYER_STAT_COLUMNS = {
    "points": ("avgPoints", "points", "points"),
    "rebounds": ("avgRebounds", None, "rebounds"),
    "assists": ("avgAssists", "assists", "assists"),
    "steals": ("avgSteals", "steals", "steals"),
    "blocks": ("avgBlocks", "blocks", "blocks"),
    "turnovers": ("avgTurnovers", "turnovers", "turnovers"),
    "minutes": ("avgMinutes", None, "minutes"),
}

STAT_LINE = ("points", "rebounds", "assists")


def player_stat(con: duckdb.DuckDBPyConnection, slots: dict[str, Any]) -> TemplateResult:
    """One named player's season numbers, from player_season_stats_deduped so
    a traded player's multi-row season is already collapsed.

    An incomplete name ("Luka", "Curry") is answered with a question rather
    than a guess. Falling through to the agent for those would cost minutes and
    end in a guess anyway; picking the most prominent match would silently
    attribute a number to the wrong player, which is the one failure this
    architecture is built to prevent. Asking costs ~1.5s and is always right."""
    text = slots.get("player")
    if not isinstance(text, str) or not text.strip():
        raise TemplateUnsupported("player_stat needs a player name")

    match resolve_player(con, text):
        case Entity() as player:
            pass
        case Ambiguous(candidates=candidates):
            return _clarify(text, candidates)
        case _:
            raise TemplateUnsupported(f"no player matching {text!r}")

    season = slots.get("season") or current_season()
    season_type = slots.get("season_type") or 2
    stat = slots.get("stat") if slots.get("stat") in PLAYER_STAT_COLUMNS else None
    wanted = [stat] if stat else list(STAT_LINE)

    columns = ["gamesPlayed"]
    for name in wanted:
        per_game, total, _ = PLAYER_STAT_COLUMNS[name]
        columns.append(per_game)
        if total:
            columns.append(total)
    row = con.execute(
        f"SELECT {', '.join(columns)} FROM player_season_stats_deduped "
        "WHERE athlete_id = ? AND season = ? AND season_type = ?",
        [player.id, season, season_type],
    ).fetchone()

    period = _period(season, season_type)
    if row is None:
        return TemplateResult(
            summary=f"{player.name}, {period}",
            data={"player": player.name, "season": season, "stats": {}},
            answer=f"{player.name} has no {period} numbers in the warehouse.",
        )
    values = dict(zip(columns, row, strict=True))
    return TemplateResult(
        summary=f"{player.name}, {period}",
        data={"player": player.name, "season": season, "stats": values},
        answer=_phrase_player_stat(player.name, period, values, wanted),
    )


def _clarify(text: str, candidates: list[str]) -> TemplateResult:
    """A handled outcome, not a fall-through: the template knows exactly what
    is ambiguous, so it says so instead of passing the problem along."""
    joined = ", ".join(candidates[:-1]) + f" or {candidates[-1]}"
    return TemplateResult(
        summary=f"ambiguous player {text!r}",
        data={"ambiguous": text, "candidates": candidates},
        answer=f"{text!r} matches more than one player - did you mean {joined}?",
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


TEMPLATES: dict[str, Callable[[duckdb.DuckDBPyConnection, dict[str, Any]], TemplateResult]] = {
    "threshold_count": threshold_count,
    "leaderboard": leaderboard,
    "player_stat": player_stat,
}
