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
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb

from association.season import current_season

from .court import HOOP_X, HOOP_Y
from .entities import Ambiguous, Entity, resolve_player, resolve_team
from .leaderboard import LeaderboardError, resolve_metric, run_leaderboard
from .metrics import EXTRA_FIELD_COLUMNS, SEASON_TYPE_LABELS
from .shotchart import render_shot_chart

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
DEFAULT_LEADERBOARD_LIMIT = 10
MAX_LIMIT = 50

# Named in every answer, so answering the wrong one is visible rather than silent.
SEASON_TYPE_NAMES = {1: "preseason", 2: "regular season", 3: "postseason"}


# Slots that narrow WHICH games an answer covers. A template that ignores one
# gives a different answer, not a broader one, and says nothing - confirmed
# three times ("his last game" charting a whole season, and so on). The router
# extracts these CORRECTLY in each case, so check_routing cannot catch a
# template dropping them; only this can.
SCOPING_SLOTS = frozenset({"order", "date"})

# What each template actually honours. Anything not listed here honours none.
HONORED_SCOPING: dict[str, frozenset[str]] = {
    "game_log": frozenset({"order", "date"}),
    "shot_chart": frozenset({"order"}),
    "shot_distance": frozenset({"order"}),
    "player_netpoints": frozenset({"order"}),
}


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
    no ids and no schema. Tests assert against it."""

    summary: str
    data: dict[str, Any]
    answer: str


def _clamp_limit(limit: Any, default: int = DEFAULT_LIMIT) -> int:
    if not isinstance(limit, int) or limit < 1:
        return default
    return min(limit, MAX_LIMIT)


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
    from association.net_points_categories import FINGERPRINT_CATEGORIES

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
            summary=f"{player.name} NetPoints, {period}",
            data={"player": player.name, "season": season},
            answer=f"The warehouse has no {period} NetPoints for {player.name}.",
        )
    return TemplateResult(
        summary=f"{player.name} NetPoints, {period}",
        data={"player": player.name, "season": season, "headline": headline, "fingerprint": breakdown},
        answer=_phrase_netpoints(player.name, period, headline, breakdown, per_100, possessions),
    )


def _single_game_netpoints(ctx: TemplateContext, player: Entity, season: int, season_type: int, order: str) -> TemplateResult:
    """One game's NetPoints, from net_points_player_game.

    That table is opt-in (`data pull --include-net-points-daily`) and, unlike
    net_points_player, uses the normal numeric season_type. No play-type
    fingerprint - that is season-level only."""
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
            summary=f"{player.name} NetPoints, single game",
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
    answer += "\n  (Per-game NetPoints carry no play-type fingerprint - that is season-level only.)"
    return TemplateResult(summary=f"{player.name} NetPoints, {game['date']}", data={"player": player.name, "game": game}, answer=answer)


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


def _season_row(con: duckdb.DuckDBPyConnection, athlete_id: str, columns: list[str], season: int, season_type: int) -> tuple[Any, ...] | None:
    """One player's season line. Reads player_season_stats_deduped, the view
    that has already collapsed a traded player's per-stint rows into one, so no
    caller has to remember to. `columns` comes from PLAYER_STAT_COLUMNS or
    HISTORY_COLUMNS - never from a slot."""
    return con.execute(
        f"SELECT {', '.join(columns)} FROM player_season_stats_deduped WHERE athlete_id = ? AND season = ? AND season_type = ?",
        [athlete_id, season, season_type],
    ).fetchone()


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
    summary = f"{name} game log, {period}"
    if not rows:
        return TemplateResult(summary=summary, data={"team": name, "games": []}, answer=f"No {period} games found for the {name}.")
    games = [{"date": str(r[0])[:10], "home_away": r[1], "opponent": r[2], "team_score": r[3], "opponent_score": r[4], "won": bool(r[5])} for r in rows]
    # Tallied here, over exactly the rows being shown, rather than left to be
    # counted back out of the listing - that recount is where a wins/losses
    # total gets inverted.
    wins = sum(1 for g in games if g["won"])
    header = f"{name}, {_scope(len(games), ascending, date)} of the {period} ({wins}-{len(games) - wins}):"
    lines = [f"  {g['date']}  {'W' if g['won'] else 'L'} {g['team_score']}-{g['opponent_score']}  {'vs' if g['home_away'] == 'home' else 'at'} {g['opponent']}" for g in games]
    return TemplateResult(summary=summary, data={"team": name, "wins": wins, "games": games}, answer="\n".join([header, *lines]))


def _player_game_log_result(name: str, period: str, rows: list[tuple[Any, ...]], ascending: bool, date: str | None) -> TemplateResult:
    summary = f"{name} game log, {period}"
    if not rows:
        return TemplateResult(summary=summary, data={"player": name, "games": []}, answer=f"No {period} games found for {name}.")
    games = [{"date": str(r[0])[:10], "opponent": r[1], "minutes": r[2], "points": r[3], "rebounds": r[4], "assists": r[5]} for r in rows]
    header = f"{name}, {_scope(len(games), ascending, date)} of the {period}:"
    lines = [f"  {g['date']}  vs {g['opponent']}  {g['points']} pts, {g['rebounds']} reb, {g['assists']} ast" for g in games]
    return TemplateResult(summary=summary, data={"player": name, "games": games}, answer="\n".join([header, *lines]))


def _scoping_game(con: duckdb.DuckDBPyConnection, athlete_id: str, season: int, season_type: int, order: str) -> tuple[Any, ...] | None:
    """The single game an `order` slot narrows a question to: (event_id, date),
    or None if the player has no games that season. Both orderings are explicit -
    LIMIT 1 without an ORDER BY returns an arbitrary row, not the first or last."""
    return con.execute(
        f"SELECT event_id, game_date FROM player_game_log WHERE athlete_id = ? AND season = ? AND season_type = ? ORDER BY game_date {'ASC' if order == 'first' else 'DESC'} LIMIT 1",
        [athlete_id, season, season_type],
    ).fetchone()


def _resolve_chart_player(ctx: TemplateContext, text: str) -> str:
    """The athlete_id render_shot_chart will settle on, so a single-game lookup
    scopes to the same player the chart is drawn for."""
    from .entities import find_players

    candidates = find_players(ctx.con, text)
    if not candidates:
        raise TemplateUnsupported(f"no player matching {text!r}")
    return candidates[0].id


def shot_chart(ctx: TemplateContext, slots: dict[str, Any]) -> TemplateResult:
    """Renders one player's shots to a static HTML court plot.

    Uses shotchart.render_shot_chart, the same function the agent tool calls,
    so it inherits best-match player handling rather than resolve_player's
    refusal: a chart of the wrong Curry is obvious on sight, and titled with the
    resolved name."""
    player = slots.get("player")
    if not isinstance(player, str) or not player.strip():
        raise TemplateUnsupported("shot_chart needs a player name")
    shot_value = _shot_value(slots)

    # "a shot chart of Curry's LAST regular season game" charted the whole
    # season - 803 attempts instead of that game's 22 - because nothing scoped
    # the request to one game. `order` means the same here as in game_log, and
    # resolving it to an event_id is the only way render_shot_chart can scope.
    season = slots.get("season") or current_season()
    season_type = slots.get("season_type") or 2
    event_id = None
    if slots.get("order") in ("recent", "first"):
        found = _scoping_game(ctx.con, _resolve_chart_player(ctx, player), season, season_type, slots["order"])
        if found is None:
            raise TemplateUnsupported(f"no games found to chart for {player!r}")
        event_id = found[0]

    message = render_shot_chart(
        ctx.con,
        ctx.out_dir,
        player,
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
    return TemplateResult(summary="shot chart", data={"message": message}, answer=message)


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
        summary=f"{player.name} {kind}shot distance, {period}",
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
        sentence = f"{top['player']} had the most {label}s in a single game in the {period}: {top['value']}, on {top['date']}{where}."
    rest = [f"{g['player']} ({g['value']})" for g in games if g["value"] != top["value"]]
    return sentence + (f" Next: {', '.join(rest)}." if rest else "")


MAX_COMPARED_PLAYERS = 4


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
    prompt."""
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
    summary = f"{team.name} {period_label} points{vs}, {period_str}"
    opponent_name = opponent.name if opponent else None

    games = []
    for date, own_linescores, opp_name in rows:
        scores = _linescores(own_linescores)
        points = scores[period - 1] if period - 1 < len(scores) else None
        games.append({"date": str(date)[:10], "opponent": opp_name, "points": points})

    if not games:
        answer = f"The warehouse has no {period_str} games for the {team.name}{vs}."
        return TemplateResult(summary=summary, data={"team": team.name, "opponent": opponent_name, "games": []}, answer=answer)

    played = [g for g in games if g["points"] is not None]
    if not played:
        plural = "game" if len(games) == 1 else "games"
        answer = f"None of the {team.name}'s {len(games)} {period_str} {plural}{vs} went to the {period_label}."
        return TemplateResult(summary=summary, data={"team": team.name, "opponent": opponent_name, "games": games}, answer=answer)

    total = sum(g["points"] for g in played)
    data = {"team": team.name, "opponent": opponent_name, "period": period, "games": played, "total": total}
    return TemplateResult(summary=summary, data=data, answer=_phrase_team_quarter_points(team.name, opponent_name, period_label, period_str, played, total))


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
    wanted = _wanted_stats(slots)
    columns = ["gamesPlayed"] + [PLAYER_STAT_COLUMNS[name][0] for name in wanted]

    rows: dict[str, dict[str, Any]] = {}
    for player in resolved:
        row = _season_row(con, player.id, columns, season, season_type)
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
    "team_quarter_points": team_quarter_points,
    "shot_distance": shot_distance,
    "player_history": player_history,
    "player_netpoints": player_netpoints,
}
