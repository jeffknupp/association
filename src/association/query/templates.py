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

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb

from association.season import current_season

from .entities import Ambiguous, Entity, resolve_player, resolve_team
from .leaderboard import LeaderboardError, resolve_metric, run_leaderboard
from .metrics import EXTRA_FIELD_COLUMNS, SEASON_TYPE_LABELS
from .shotchart import render_shot_chart

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


def threshold_count(ctx: TemplateContext, slots: dict[str, Any]) -> TemplateResult:
    """"Most games with N+ of some stat" - the shape that motivated this split.

    KNOWLEDGE_BASE already carried this exact pattern ("Single-game vs.
    season-total stats"), but it sat in the truncated-away head of the system
    prompt, so three consecutive runs answered a season-averages leaderboard
    instead and presented it as the answer. Encoded here it cannot be
    truncated, misremembered, or silently substituted."""
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


def leaderboard(ctx: TemplateContext, slots: dict[str, Any]) -> TemplateResult:
    """"Top N players by X" for the metrics in LEADERBOARD_METRICS.

    Thin on purpose: run_leaderboard already owns the season default, the
    per-metric minimum-sample floor and traded-player dedup, and the agent's
    get_leaderboard tool calls the same function. All this adds is the router
    slot mapping and deterministic phrasing."""
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
    # (confirmed live: ["points","minutes","minutes"]), and a repeat is a
    # harmless slip, not a reason to spend minutes in the agent. A field that
    # restates the ranked metric is dropped too - asked for "top scorers with
    # their rebounds and assists" the model also returned "points", which
    # rendered 33.5 twice under two different headings.
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
        summary=summary,
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


def player_netpoints(ctx: TemplateContext, slots: dict[str, Any]) -> TemplateResult:
    """One player's NetPoints, with the play-type fingerprint.

    NetPoints was exposed only as leaderboard metrics - ways to rank the
    league - so "what were SGA's netpoint stats this season" had no shape to
    land in. player_stat correctly refused the unsupported stat and fell
    through; the agent then called get_leaderboard for the league, dropped SGA
    entirely, and answered "Nikola Jokic leads the team in NetPoints"."""
    from association.net_points_categories import FINGERPRINT_CATEGORIES

    con = ctx.con
    text = slots.get("player")
    if not isinstance(text, str) or not text.strip():
        raise TemplateUnsupported("player_netpoints needs a player name")
    match resolve_player(con, text):
        case Entity() as player:
            pass
        case Ambiguous(candidates=candidates):
            return _clarify(text, candidates)
        case _:
            raise TemplateUnsupported(f"no player matching {text!r}")

    season = slots.get("season") or current_season()
    season_type = slots.get("season_type") or 2
    # net_points_player uses its OWN string season_type; filtering it with the
    # numeric one every other table uses silently matches nothing.
    label = SEASON_TYPE_LABELS.get(season_type, "Regular Season")
    headline = con.execute(
        "SELECT overall, offense, defense, overall_per_100_poss, total_minutes, games FROM net_points_player "
        "WHERE athlete_id = ? AND season = ? AND net_points_season_type = ?",
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
            summary=f"{player.name} NetPoints, {period}",
            data={"player": player.name, "season": season},
            answer=f"The warehouse has no {period} NetPoints for {player.name}.",
        )
    return TemplateResult(
        summary=f"{player.name} NetPoints, {period}",
        data={"player": player.name, "season": season, "headline": headline, "fingerprint": breakdown},
        answer=_phrase_netpoints(player.name, period, headline, breakdown, per_100, possessions),
    )


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
        lines.append(
            f"{name}, NetPoints in the {period}: {_table_cell(overall)} overall "
            f"({_table_cell(offense)} offense, {_table_cell(defense)} defense)"
        )
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

    lines.append("")
    units = "per 100 possessions" if per_100 else "season totals"
    scope = f" over {possessions:,.0f} possessions" if per_100 and possessions else ""
    lines.append(f"  Fingerprint by play type, {units}{scope}, largest first (offense / defense / total):")
    width = max(len(row["category"]) for row in breakdown)
    lines.append("  " + "category".ljust(width) + "".join(h.rjust(9) for h in ("O", "D", "T")))
    for row in breakdown:
        # Two decimals: per-100 fingerprint values are small, and one decimal
        # collapses most of the play types into the same number.
        cells = "".join(("-" if row[k] is None else f"{row[k]:.2f}").rjust(9) for k in ("offense", "defense", "total"))
        lines.append("  " + row["category"].ljust(width) + cells)
    return "\n".join(lines)


DEFAULT_HISTORY_SEASONS = 4
MAX_HISTORY_SEASONS = 20


def player_history(ctx: TemplateContext, slots: dict[str, Any]) -> TemplateResult:
    """One player's stat across several seasons.

    Every other template answers about a single season, so "what was Klay
    Thompson's 3pt percentage over the past 4 seasons" had nowhere to go: it
    routed to `leaderboard`, which dropped the player entirely and returned the
    league's true-shooting leaders for 2020. A multi-season question needs a
    multi-season shape."""
    con = ctx.con
    text = slots.get("player")
    if not isinstance(text, str) or not text.strip():
        raise TemplateUnsupported("player_history needs a player name")
    match resolve_player(con, text):
        case Entity() as player:
            pass
        case Ambiguous(candidates=candidates):
            return _clarify(text, candidates)
        case _:
            raise TemplateUnsupported(f"no player matching {text!r}")

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
        f"SELECT season, gamesPlayed, {', '.join(c for c, _ in columns)} FROM player_season_stats_deduped "
        "WHERE athlete_id = ? AND season_type = ? AND season <= ? ORDER BY season DESC LIMIT ?",
        [player.id, season_type, latest, seasons],
    ).fetchall()

    period = SEASON_TYPE_NAMES.get(season_type, "regular season")
    history = [dict(zip(["season", "games"] + [c for c, _ in columns], r, strict=True)) for r in rows]
    return TemplateResult(
        summary=f"{player.name} {label}, last {seasons} {period}s",
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


def _wanted_stats(slots: dict[str, Any]) -> list[str]:
    """The stats to report: the one named, or the default line if none was.

    A stat that was NAMED but is not supported must not fall back to the
    default line - that is how "what was Steph Curry's avg 3pt shot distance"
    came back as "26.6 points, 3.6 rebounds and 4.7 assists per game". Falling
    through to the agent is slow; answering a different question is worse."""
    stat = slots.get("stat")
    if stat is None or (isinstance(stat, str) and not stat.strip()):
        return list(STAT_LINE)
    if stat in PLAYER_STAT_COLUMNS:
        return [stat]
    raise TemplateUnsupported(f"no per-game column for stat {stat!r}")

STAT_LINE = ("points", "rebounds", "assists")


def player_stat(ctx: TemplateContext, slots: dict[str, Any]) -> TemplateResult:
    """One named player's season numbers, from player_season_stats_deduped so
    a traded player's multi-row season is already collapsed.

    An incomplete name ("Luka", "Curry") is answered with a question rather
    than a guess. Falling through to the agent for those would cost minutes and
    end in a guess anyway; picking the most prominent match would silently
    attribute a number to the wrong player, which is the one failure this
    architecture is built to prevent. Asking costs ~1.5s and is always right."""
    con = ctx.con
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
    wanted = _wanted_stats(slots)

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


MAX_CLARIFY_CANDIDATES = 5


def _as_int(value: Any) -> str:
    return str(int(value)) if isinstance(value, (int, float)) else str(value)


def _ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _clarify(text: str, candidates: list[str], kind: str = "player") -> TemplateResult:
    """A handled outcome, not a fall-through: the template knows exactly what
    is ambiguous, so it says so instead of passing the problem along."""
    shown, extra = candidates[:MAX_CLARIFY_CANDIDATES], len(candidates) - MAX_CLARIFY_CANDIDATES
    joined = ", ".join(shown[:-1]) + f" or {shown[-1]}" + (f" ({extra} others also match)" if extra > 0 else "")
    return TemplateResult(
        summary=f"ambiguous {kind} {text!r}",
        data={"ambiguous": text, "candidates": candidates},
        answer=f"{text!r} matches more than one {kind} - did you mean {joined}?",
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
# home_team_id silently returns just that team's HOME games, with no error.
# team_box_stats has the team-perspective row (team_id, opponent_team_id,
# home_away), but who WON lives only on games.winner_team_id. team_score /
# opponent_score are computed from home_away rather than reported raw, because
# raw home_score/away_score forces a per-row guess about which number was this
# team's - one that gets made backwards on some rows.
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
            summary=f"{team.name} record, {season}",
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
        summary=f"{team.name} record, {season}",
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

    text = slots.get("player")
    if not isinstance(text, str) or not text.strip():
        raise TemplateUnsupported("game_log needs a team or a player")
    match resolve_player(con, text):
        case Entity() as player:
            pass
        case Ambiguous(candidates=candidates):
            return _clarify(text, candidates)
        case _:
            raise TemplateUnsupported(f"no player matching {text!r}")

    where = "athlete_id = ? AND season = ? AND season_type = ?"
    params = [player.id, season, season_type]
    if date:
        where, params = where + " AND game_date LIKE ?", [*params, f"{date}%"]
    rows = con.execute(
        f"SELECT game_date, opponent_abbr, minutes, points, rebounds, assists FROM player_game_log "
        f"WHERE {where} ORDER BY game_date {'ASC' if ascending else 'DESC'} LIMIT ?",
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
    summary = f"{name} game log, {period}"
    if not rows:
        return TemplateResult(summary=summary, data={"team": name, "games": []}, answer=f"No {period} games found for the {name}.")
    games = [
        {"date": str(r[0])[:10], "home_away": r[1], "opponent": r[2], "team_score": r[3], "opponent_score": r[4], "won": bool(r[5])}
        for r in rows
    ]
    # Tallied here, over exactly the rows being shown, rather than left to be
    # counted back out of the listing - that recount is where a wins/losses
    # total gets inverted.
    wins = sum(1 for g in games if g["won"])
    header = f"{name}, {_scope(len(games), ascending, date)} of the {period} ({wins}-{len(games) - wins}):"
    lines = [
        f"  {g['date']}  {'W' if g['won'] else 'L'} {g['team_score']}-{g['opponent_score']}  "
        f"{'vs' if g['home_away'] == 'home' else 'at'} {g['opponent']}"
        for g in games
    ]
    return TemplateResult(summary=summary, data={"team": name, "wins": wins, "games": games}, answer="\n".join([header, *lines]))


def _player_game_log_result(name: str, period: str, rows: list[tuple[Any, ...]], ascending: bool, date: str | None) -> TemplateResult:
    summary = f"{name} game log, {period}"
    if not rows:
        return TemplateResult(summary=summary, data={"player": name, "games": []}, answer=f"No {period} games found for {name}.")
    games = [{"date": str(r[0])[:10], "opponent": r[1], "minutes": r[2], "points": r[3], "rebounds": r[4], "assists": r[5]} for r in rows]
    header = f"{name}, {_scope(len(games), ascending, date)} of the {period}:"
    lines = [f"  {g['date']}  vs {g['opponent']}  {g['points']} pts, {g['rebounds']} reb, {g['assists']} ast" for g in games]
    return TemplateResult(summary=summary, data={"player": name, "games": games}, answer="\n".join([header, *lines]))


def shot_chart(ctx: TemplateContext, slots: dict[str, Any]) -> TemplateResult:
    """Renders one player's shots to a static HTML court plot.

    Uses shotchart.render_shot_chart, the same function the agent tool calls,
    and so inherits its best-match player handling rather than resolve_player's
    refusal: a chart drawn for the wrong Curry is obvious on sight, and the
    plot is titled with the resolved name."""
    player = slots.get("player")
    if not isinstance(player, str) or not player.strip():
        raise TemplateUnsupported("shot_chart needs a player name")
    # "Curry's threes" comes back either as shot_value 3 or as the equivalent
    # box-score stat, depending on the question's wording. Both mean the same
    # thing; read both rather than fighting the router over which to emit.
    stat = slots.get("stat")
    shot_value = slots.get("shot_value") if slots.get("shot_value") in (1, 2, 3) else SHOT_VALUE_FROM_STAT.get(stat if isinstance(stat, str) else "")
    message = render_shot_chart(
        ctx.con,
        ctx.out_dir,
        player,
        # An unspecified season means the CURRENT one here, exactly as it does
        # in every other template - passing None through charted a player's
        # entire career in one plot (confirmed live: 3,665 Curry attempts).
        season=slots.get("season") or current_season(),
        season_type=slots.get("season_type"),
        shot_value=shot_value,
    )
    # A "no player found" / "no shots found" message is returned as the answer
    # rather than falling through: the agent has no better source for a chart
    # than the same table this just queried.
    return TemplateResult(summary="shot chart", data={"message": message}, answer=message)


SHOT_VALUE_FROM_STAT = {"threePointFieldGoalsMade": 3, "freeThrowsMade": 1}

# The hoop is at (25, 5.25) in shot_chart's coordinate space, in feet - not at
# the origin. Free throws carry NULL coordinates and must be excluded.
HOOP_X, HOOP_Y = 25, 5.25


def shot_distance(ctx: TemplateContext, slots: dict[str, Any]) -> TemplateResult:
    """Average shot distance for one player, optionally by shot value.

    Left to the agent until it got the question wrong in a familiar way: given
    "what was steph curry's avg 3pt shot distance" it wrote the right distance
    formula, then dropped BOTH the 3-point filter and the season filter and
    reported the all-shots, all-seasons average of 16.94 as his current-season
    three-point distance. The real figure is 23.6.

    The formula is fixed and the columns are known, so nothing here is a
    judgement call - which is the whole argument for a template."""
    con = ctx.con
    text = slots.get("player")
    if not isinstance(text, str) or not text.strip():
        raise TemplateUnsupported("shot_distance needs a player name")
    match resolve_player(con, text):
        case Entity() as player:
            pass
        case Ambiguous(candidates=candidates):
            return _clarify(text, candidates)
        case _:
            raise TemplateUnsupported(f"no player matching {text!r}")

    season = slots.get("season") or current_season()
    season_type = slots.get("season_type") or 2
    raw = slots.get("shot_value")
    stat = slots.get("stat")
    shot_value = raw if raw in (1, 2, 3) else SHOT_VALUE_FROM_STAT.get(stat if isinstance(stat, str) else "")
    if shot_value == 1:
        raise TemplateUnsupported("free throws have no meaningful shot distance")

    where = ["athlete_id = ?", "season = ?", "season_type = ?", "coordinate_x IS NOT NULL"]
    params: list[Any] = [player.id, season, season_type]
    if shot_value is not None:
        where.append("points_attempted = ?")
        params.append(shot_value)
    row = con.execute(
        f"SELECT AVG(SQRT(POWER(coordinate_x - {HOOP_X}, 2) + POWER(coordinate_y - {HOOP_Y}, 2))), COUNT(*) "
        f"FROM shot_chart WHERE {' AND '.join(where)}",
        params,
    ).fetchone()

    average, attempts = (row or (None, 0))
    period = _period(season, season_type)
    kind = {2: "2-point ", 3: "3-point "}.get(shot_value or 0, "")
    if not attempts or average is None:
        answer = f"No {kind}shots with recorded coordinates for {player.name} in the {period}."
    else:
        answer = (
            f"{player.name}'s average {kind}shot distance in the {period} was "
            f"{average:.1f} feet, over {attempts:,} attempts with recorded coordinates."
        )
    return TemplateResult(
        summary=f"{player.name} {kind}shot distance, {period}",
        data={"player": player.name, "season": season, "shot_value": shot_value, "avg_feet": average, "attempts": attempts},
        answer=answer,
    )

DEFAULT_SINGLE_GAME_LIMIT = 3


def single_game_high(ctx: TemplateContext, slots: dict[str, Any]) -> TemplateResult:
    """"Most assists in a single game" - a per-game MAXIMUM, not a season
    ranking.

    Added because the router had no such shape and picked the nearest one:
    "who had the most assists in a single game and how many did he have?"
    was answered "Nikola Jokic led the league in assists per game, at 10.7"
    in 1.76s. The real answer was Ryan Nembhard with 23. A missing shape does
    not produce a refusal, it produces a confident answer to a different
    question - which is why the fix is a template, not a prompt tweak."""
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
    named_player = None
    if isinstance(text, str) and text.strip():
        match resolve_player(ctx.con, text):
            case Entity() as player:
                named_player = player
                where.append("athlete_id = ?")
                params.append(player.id)
            case Ambiguous(candidates=candidates):
                return _clarify(text, candidates)
            case _:
                raise TemplateUnsupported(f"no player matching {text!r}")

    rows = ctx.con.execute(
        f"SELECT player_name, {column}, game_date, opponent_abbr FROM player_game_log "
        f"WHERE {' AND '.join(where)} ORDER BY {column} DESC, game_date LIMIT ?",
        [*params, limit],
    ).fetchall()

    label = STAT_LABELS.get(stat or "", stat or "")
    period = _period(season, season_type)
    games = [{"player": r[0], "value": r[1], "date": str(r[2])[:10], "opponent": r[3]} for r in rows]
    return TemplateResult(
        summary=f"single-game high, {label}s, {period}",
        data={"season": season, "stat": stat, "games": games},
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
        sentence = (
            f"{top['player']} had the most {label}s in a single game in the {period}: "
            f"{top['value']}, on {top['date']}{where}."
        )
    rest = [f"{g['player']} ({g['value']})" for g in games if g["value"] != top["value"]]
    return sentence + (f" Next: {', '.join(rest)}." if rest else "")

MAX_COMPARED_PLAYERS = 4


def head_to_head(ctx: TemplateContext, slots: dict[str, Any]) -> TemplateResult:
    """"How many times did the 76ers play Boston?" - games between two teams.

    Added after that exact question was answered "the Philadelphia 76ers did
    not play against the Boston Celtics" (they played four times). The agent
    wrote `home_team_id = 'PHI'`, but team_id is an opaque numeric VARCHAR
    ('20'), so the filter silently matched nothing - and it did that with the
    rule against it, complete with a worked WRONG example, in its prompt.
    Resolving names to ids here is the only fix that holds."""
    con = ctx.con
    names = slots.get("teams")
    if not isinstance(names, list) or len({n for n in names if isinstance(n, str) and n.strip()}) < 2:
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
    # The standing rule every other template follows: no season named means the
    # CURRENT one. "All time" is a defensible reading of a head-to-head, but
    # answering a different span than the rest of the system - silently - is the
    # substitution this whole design exists to prevent. The answer names the
    # season, so asking for another one is one follow-up away.
    season = slots.get("season") or current_season()
    season_type = slots.get("season_type") or 2
    # Both orderings, since `games` is home/away-oriented rather than
    # team-perspective, and the season filter parenthesised around the whole
    # matchup - `A OR B AND season = ...` applies the season to one side only.
    where = [
        "((g.home_team_id = ? AND g.away_team_id = ?) OR (g.home_team_id = ? AND g.away_team_id = ?))",
        "g.season_type = ?",
        "g.season = ?",
    ]
    params: list[Any] = [a.id, b.id, b.id, a.id, season_type, season]
    rows = con.execute(
        f"SELECT g.date, g.home_team_id, g.home_score, g.away_score, g.winner_team_id "
        f"FROM games g WHERE {' AND '.join(where)} ORDER BY g.date",
        params,
    ).fetchall()

    a_wins = sum(1 for r in rows if r[4] == a.id)
    b_wins = sum(1 for r in rows if r[4] == b.id)
    period = _period(season, season_type)
    return TemplateResult(
        summary=f"{a.name} vs {b.name}, {period}",
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


def player_compare(ctx: TemplateContext, slots: dict[str, Any]) -> TemplateResult:
    """Two or more named players' season numbers side by side.

    Previously fell through to the agent, which got it wrong for a reason no
    KNOWLEDGE_BASE entry could fix: it wrote correct SQL (current_season(),
    ILIKE name matching) but expanded "SGA" to '%Scottie G. Allen%' and
    compared Luka Doncic to Luka Garza. Nickname resolution is a lookup, not
    something to hope a 7B model knows - see entities.PLAYER_NICKNAMES."""
    con = ctx.con
    names = slots.get("players")
    if not isinstance(names, list) or len({n for n in names if isinstance(n, str) and n.strip()}) < 2:
        raise TemplateUnsupported("player_compare needs at least two distinct player names")

    resolved: list[Entity] = []
    for name in names[:MAX_COMPARED_PLAYERS]:
        match resolve_player(con, name):
            case Entity() as player:
                if player.id not in {p.id for p in resolved}:
                    resolved.append(player)
            case Ambiguous(candidates=candidates):
                return _clarify(name, candidates)
            case _:
                raise TemplateUnsupported(f"no player matching {name!r}")
    if len(resolved) < 2:
        raise TemplateUnsupported("the named players resolved to the same person")

    season = slots.get("season") or current_season()
    season_type = slots.get("season_type") or 2
    wanted = _wanted_stats(slots)
    columns = ["gamesPlayed"] + [PLAYER_STAT_COLUMNS[name][0] for name in wanted]

    rows: dict[str, dict[str, Any]] = {}
    for player in resolved:
        row = con.execute(
            f"SELECT {', '.join(columns)} FROM player_season_stats_deduped "
            "WHERE athlete_id = ? AND season = ? AND season_type = ?",
            [player.id, season, season_type],
        ).fetchone()
        rows[player.name] = dict(zip(columns, row, strict=True)) if row else {}

    period = _period(season, season_type)
    return TemplateResult(
        summary=f"{' vs '.join(rows)}, {period}",
        data={"season": season, "players": rows},
        answer=_phrase_compare(rows, wanted, period),
    )


def _table_cell(value: Any) -> str:
    if value is None:
        return "-"
    return f"{value:.1f}" if isinstance(value, float) else str(value)


def _phrase_compare(rows: dict[str, dict[str, Any]], wanted: list[str], period: str) -> str:
    """A fixed-width table rather than prose. Comparisons are the one shape
    where a sentence actively hurts - the agent's prose version stated that a
    player with 0.4 steals led one with 1.6."""
    names = list(rows)
    missing = [name for name, values in rows.items() if not values]
    label_width = max(len("games"), *(len(PLAYER_STAT_COLUMNS[name][2]) for name in wanted))
    name_width = max(len(name) for name in names)
    header = f"{' ' * label_width}  " + "  ".join(name.rjust(name_width) for name in names)
    lines = [f"{' vs '.join(names)}, {period}:", header]
    for label, key in [("games", "gamesPlayed")] + [(PLAYER_STAT_COLUMNS[n][2], PLAYER_STAT_COLUMNS[n][0]) for n in wanted]:
        # A fixed decimal here, not _format_value: in an aligned column a
        # trailing-zero-stripped "25" next to "27.7" reads as a different unit.
        cells = [_table_cell(rows[name].get(key)) for name in names]
        lines.append(f"{label.ljust(label_width)}  " + "  ".join(c.rjust(name_width) for c in cells))
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
    "shot_distance": shot_distance,
    "player_history": player_history,
    "player_netpoints": player_netpoints,
}
