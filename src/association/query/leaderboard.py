"""The "top N players by X" query, extracted so the fast-path template and the
agent's get_leaderboard tool are the same code rather than two implementations
that can drift.

Every correctness rule this schema needs is applied here rather than left to
the model to remember and re-derive per query: the season defaults to the
CURRENT one (see current_season()) rather than whichever season happens to have
data loaded; a qualifying minimum sample (games or minutes, per metric) is
applied by default for rate/percentage metrics, since those - confirmed live -
let a tiny sample (a garbage-time cameo, a 1-game call-up) swing to an extreme
value no sustained role reaches; a traded player is deduplicated to one row.

`team` and `fields` are deliberately narrow (a resolved team filter and a fixed
whitelist of extra box-score columns) rather than a free-text filter/column
passthrough, which would just reopen the SQL-generation reliability problem
this exists to close."""

from __future__ import annotations

from dataclasses import dataclass
from difflib import get_close_matches
from typing import Any

import duckdb

from .entities import Ambiguous, Entity, NotFound, resolve_team
from .metrics import EXTRA_FIELD_COLUMNS, LEADERBOARD_METRICS, SEASON_TYPE_LABELS, current_season

MAX_ROWS = 200


class LeaderboardError(Exception):
    """Carries a message written for the model to read and act on - the agent
    tool returns it verbatim as a tool result, the template treats it as a
    reason to fall through."""


@dataclass
class LeaderboardResult:
    metric: str
    label: str
    season: int
    season_type: int | None
    min_sample_applied: int | None
    min_sample_column: str | None
    team: str | None
    team_name: str | None
    rows: list[dict[str, Any]]


def resolve_metric(name: str | None) -> str | None:
    """Router slot -> a real metric name, via an EXPLICIT alias table.

    Deliberately not get_close_matches: fuzzy matching is fine for suggesting a
    fix to a model that can then correct itself, but a template silently
    ranking by whichever metric happened to score highest is exactly the
    substitution failure this architecture exists to prevent. An unmapped name
    returns None and the question falls through."""
    if not isinstance(name, str):
        return None
    if name in LEADERBOARD_METRICS:
        return name
    return METRIC_ALIASES.get(name.strip().casefold())


# "who leads in points" means per game - the only points metric here is a
# per-game one, so there is no total-vs-average ambiguity to get wrong.
METRIC_ALIASES = {
    "points": "avg_points",
    "rebounds": "avg_rebounds",
    "assists": "avg_assists",
    "steals": "avg_steals",
    "blocks": "avg_blocks",
    "double_double": "double_doubles",
    "triple_double": "triple_doubles",
    "netpoints": "netpoints_total",
    "true_shooting": "ts_pct",
    "usage": "usage_pct",
}


def run_leaderboard(
    con: duckdb.DuckDBPyConnection,
    metric: str,
    season: int | None = None,
    season_type: int = 2,
    min_sample: int | None = None,
    team: str | None = None,
    fields: list[str] | None = None,
    limit: int = 10,
) -> LeaderboardResult:
    spec = LEADERBOARD_METRICS.get(metric)
    if spec is None:
        # A close-match suggestion (e.g. "points" -> "avg_points") keeps a
        # wrong guess a one-turn fix - confirmed live, without it a wrong
        # metric name sent the model on an unrelated multi-turn detour that
        # eventually recovered but dropped the team/fields it had originally
        # been asked for.
        suggestion = get_close_matches(metric, LEADERBOARD_METRICS, n=1)
        hint = f" Did you mean {suggestion[0]!r}?" if suggestion else ""
        raise LeaderboardError(f"Error: unknown metric {metric!r}.{hint} Known metrics: {sorted(LEADERBOARD_METRICS)}")
    if season_type not in SEASON_TYPE_LABELS:
        raise LeaderboardError(f"Error: season_type must be 1 (preseason), 2 (regular season), or 3 (postseason) - got {season_type!r}.")
    unknown_fields = [f for f in fields or [] if f not in EXTRA_FIELD_COLUMNS]
    if unknown_fields:
        raise LeaderboardError(f"Error: unknown field(s) {unknown_fields}. Known fields: {sorted(EXTRA_FIELD_COLUMNS)}")

    resolved_season = season if season is not None else current_season()
    season_type_value: int | str = SEASON_TYPE_LABELS[season_type] if spec.season_type_is_string else season_type
    effective_min_sample = min_sample if min_sample is not None else spec.default_min_sample

    resolved_team_id: str | None = None
    resolved_team_name: str | None = None
    if team is not None:
        match resolve_team(con, team):
            case Entity() as resolved:
                resolved_team_id, resolved_team_name = resolved.id, resolved.name
            case Ambiguous(candidates=candidates):
                raise LeaderboardError(f"Error: {team!r} matches more than one team: {candidates}. Be more specific.")
            case NotFound():
                raise LeaderboardError(f"Error: no team found matching {team!r}.")

    # A box-score join, keyed on (athlete_id, season, season_type) and deduped
    # to the season-combined row (same pattern as a traded player's
    # season-total row), is needed for `fields` regardless of which table the
    # ranked metric itself lives in - player_season_stats is the one table
    # every metric can join to this way.
    select_cols = ["p.display_name AS display_name", f"t.{spec.column} AS value"]
    select_cols.extend(f"t.{col}" for col in spec.extra_columns)
    select_cols.extend(f"box.{EXTRA_FIELD_COLUMNS[f]} AS {f}" for f in fields or [])
    from_clause = f"FROM {spec.table} t JOIN players p ON p.athlete_id = t.{spec.id_column}"
    params: list[Any] = []
    if fields:
        from_clause += (
            " LEFT JOIN (SELECT * FROM player_season_stats WHERE season = ? AND season_type = ? "
            "QUALIFY ROW_NUMBER() OVER (PARTITION BY athlete_id ORDER BY (team_id IS NULL) DESC) = 1) box "
            f"ON box.athlete_id = t.{spec.id_column}"
        )
        params.extend([resolved_season, season_type])

    where = [f"t.{spec.season_column} = ?"]
    params.append(resolved_season)
    if spec.has_season_type:
        where.append(f"t.{spec.season_type_column} = ?")
        params.append(season_type_value)
    if effective_min_sample is not None:
        if spec.min_sample_column is None:
            raise LeaderboardError(f"Error: metric {metric!r} has no minimum-sample column to apply min_sample to.")
        where.append(f"t.{spec.min_sample_column} >= ?")
        params.append(effective_min_sample)
    if resolved_team_id is not None:
        # A per-stint EXISTS check, not the deduped `box` join above - a traded
        # player's deduped/combined row has team_id IS NULL, which would
        # wrongly exclude them from every team's roster even though they really
        # did play for one of their stint teams that season.
        where.append(
            f"EXISTS (SELECT 1 FROM player_season_stats pss WHERE pss.athlete_id = t.{spec.id_column} "
            "AND pss.season = ? AND pss.season_type = ? AND pss.team_id = ?)"
        )
        params.extend([resolved_season, season_type, resolved_team_id])
    qualify = "QUALIFY ROW_NUMBER() OVER (PARTITION BY t.athlete_id ORDER BY (t.team_id IS NULL) DESC) = 1" if spec.dedup_traded else ""

    sql = f"SELECT {', '.join(select_cols)} {from_clause} WHERE {' AND '.join(where)} {qualify} ORDER BY t.{spec.column} DESC LIMIT ?"
    params.append(limit)
    try:
        cur = con.execute(sql, params)
        cols = [d[0] for d in cur.description]
        rows = cur.fetchmany(MAX_ROWS)
    except Exception as exc:  # e.g. the table needs a warehouse flag that wasn't used
        raise LeaderboardError(f"SQL error: {exc}" + (f" (requires: {spec.requires})" if spec.requires else "")) from exc

    return LeaderboardResult(
        metric=metric,
        label=spec.label,
        season=resolved_season,
        season_type=season_type if spec.has_season_type else None,
        min_sample_applied=effective_min_sample,
        min_sample_column=spec.min_sample_column,
        team=team,
        team_name=resolved_team_name,
        rows=[dict(zip(cols, row, strict=True)) for row in rows],
    )
