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
MAX_LIMIT = 50


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


def _clamp_limit(limit: Any) -> int:
    if not isinstance(limit, int) or limit < 1:
        return DEFAULT_LIMIT
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
    limit = _clamp_limit(slots.get("limit"))
    player = slots.get("player")

    where = ["pbs.season = ?", "pbs.season_type = 2", f"pbs.{column} >= ?"]
    params: list[Any] = [season, threshold]
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
    summary = f"games with {scope}, {season} regular season"
    leaders = [{"player": name, "games": games} for name, games in rows]
    return TemplateResult(
        summary=summary,
        data={"question_shape": summary, "season": season, "leaders": leaders},
        answer=_phrase_threshold_count(rows, scope, season, filtered_to_one_player=bool(player)),
    )


def _phrase_threshold_count(rows: list[tuple[Any, ...]], scope: str, season: int, filtered_to_one_player: bool) -> str:
    """Always names the season outright rather than echoing "this season" back.
    The original failure answered for 2024 while the user meant the current
    season, and said nothing about it - so the season is stated, every time."""
    label = f"games with {scope}"
    if not rows:
        if filtered_to_one_player:
            return f"That player had no {label} in the {season} regular season."
        return f"No player had a game with {scope} in the {season} regular season."
    if filtered_to_one_player:
        name, games = rows[0]
        return f"{name} had {games} {label} in the {season} regular season."

    top = rows[0][1]
    tied = [name for name, games in rows if games == top]
    if len(tied) > 1:
        leaders = ", ".join(tied[:-1]) + f" and {tied[-1]}"
        sentence = f"{leaders} tied for the most {label} in the {season} regular season, with {top} each."
    else:
        sentence = f"{rows[0][0]} had the most {label} in the {season} regular season, with {top}."
    rest = [f"{name} ({games})" for name, games in rows if games != top]
    return sentence + (f" Next: {', '.join(rest)}." if rest else "")


TEMPLATES: dict[str, Callable[[duckdb.DuckDBPyConnection, dict[str, Any]], TemplateResult]] = {
    "threshold_count": threshold_count,
}
