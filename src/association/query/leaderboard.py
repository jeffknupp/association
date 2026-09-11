"""The "top N players by X" query, extracted so the fast-path template and the
agent's get_leaderboard tool are the same code rather than two implementations
that can drift.

Every correctness rule is applied here rather than left to the model to
re-derive per query: the season defaults to the CURRENT one rather than
whichever happens to have data loaded; rate and percentage metrics get a
qualifying minimum sample, since a garbage-time cameo otherwise tops the board;
a traded player is deduplicated to one row; a postseason row that is really a
copy of the regular season is dropped.

`team` and `fields` are deliberately narrow (a resolved team filter and a fixed
whitelist of extra box-score columns) rather than a free-text filter/column
passthrough, which would just reopen the SQL-generation reliability problem
this exists to close.

A career ranking is a separate function, :func:`run_career_leaderboard`,
because its pool is a different thing and has to be reported as one: every
player whose career reached 1993-94, not every player who ever played."""

from __future__ import annotations

from dataclasses import dataclass
from difflib import get_close_matches
from typing import Any

import duckdb

from association.coverage import COVERAGE, POSTSEASON, REGULAR_SEASON
from association.season import current_season

from .entities import Ambiguous, Entity, NotFound, resolve_team
from .metrics import EXTRA_FIELD_COLUMNS, LEADERBOARD_METRICS, SEASON_TYPE_LABELS, CareerAggregate, LeaderboardMetric

# `limit` is model-supplied on the agent path (the template clamps its own):
# a leaderboard of 5,000 helps nobody and floods the context window. Applied to
# the SQL LIMIT itself, so the fetch below cannot return more than this.
MAX_LIMIT = 100


class LeaderboardError(Exception):
    """Carries a message written for the model to read and act on - the agent
    tool returns it verbatim as a tool result, the template treats it as a
    reason to fall through."""


@dataclass
class LeaderboardResult:
    """A ranked leaderboard plus the qualifiers that produced it.

    ``min_sample_applied`` and ``min_sample_column`` are part of the answer, not
    bookkeeping: they are what makes "why is this player missing?" answerable.
    """

    metric: str
    label: str
    season: int
    season_type: int | None
    min_sample_applied: int | None
    min_sample_column: str | None
    team: str | None
    team_name: str | None
    rows: list[dict[str, Any]]


@dataclass
class CareerLeaderboardResult:
    """A career leaderboard plus the pool and the qualifier that produced it.

    ``pool_first_season`` is part of the answer for the same reason the
    qualifier is. The season table is fetched per player over a whole career,
    and players are discovered from box scores, which begin in 1993-94. So the
    pool is every player whose career reached that season, counted in full, and
    nobody whose career ended before it. An answer that does not say so is
    presenting a list without Kareem Abdul-Jabbar as the all-time one.

    Each row carries ``value``, ``games``, and the ``first_season`` and
    ``last_season`` of the career summed; a percentage's rows also carry the
    summed makes and attempts under their column names.

    .. versionadded:: 2.1.0
    """

    metric: str
    label: str
    season_type: int
    min_sample_applied: int | None
    min_sample_column: str | None
    pool_first_season: int
    rows: list[dict[str, Any]]


def resolve_metric(name: str | None, *, career: bool = False) -> str | None:
    """Router slot -> a real metric name, via an EXPLICIT alias table.

    Deliberately not get_close_matches: fuzzy matching is fine for suggesting a
    fix to a model that can then correct itself, but a template silently
    ranking by whichever metric happened to score highest is exactly the
    substitution failure this architecture exists to prevent. An unmapped name
    returns None and the question falls through.

    With ``career``, a bare box-score name reads as the career TOTAL - see
    ``CAREER_METRIC_ALIASES``. A real metric name is never reinterpreted, so a
    career average stays reachable as ``avg_points``.

    .. versionchanged:: 2.1.0
       Added ``career``, and an alias for every stat name the router is taught.
    """
    if not isinstance(name, str):
        return None
    if name in LEADERBOARD_METRICS:
        return name
    key = name.strip().casefold()
    if career and key in CAREER_METRIC_ALIASES:
        return CAREER_METRIC_ALIASES[key]
    return METRIC_ALIASES.get(key)


# The router has one stat vocabulary (the `stat` line of ROUTER_PROMPT) for
# every intent, so each of its names needs a metric here: every one it taught
# that had none - turnovers, minutes, fouls, the three kinds of make, the three
# percentages - fell through to the agent. Keys are casefolded.
#
# Which reading a bare name gets follows the record books. The five a scoring,
# rebounding, assist, steal or block title is decided on are per game, as they
# always were here; a make is a season COUNT ("most threes this season" is the
# 402-three kind of record, not a rate); turnovers, minutes and fouls are per
# game, the way a league leaderboard lists them. Every answer names which it
# ranked, and `rate` "total" asks for the other - see SEASON_TOTAL_OF.
METRIC_ALIASES = {
    "points": "avg_points",
    "rebounds": "avg_rebounds",
    "assists": "avg_assists",
    "steals": "avg_steals",
    "blocks": "avg_blocks",
    "turnovers": "avg_turnovers",
    "minutes": "avg_minutes",
    "fouls": "avg_fouls",
    "threepointfieldgoalsmade": "total_three_pointers_made",
    "fieldgoalsmade": "total_field_goals_made",
    "freethrowsmade": "total_free_throws_made",
    "threepointfieldgoalpct": "three_pt_pct",
    "fieldgoalpct": "fg_pct",
    "freethrowpct": "ft_pct",
    "double_double": "double_doubles",
    "triple_double": "triple_doubles",
    "netpoints": "netpoints_total",
    "true_shooting": "ts_pct",
    "usage": "usage_pct",
}

CAREER_METRIC_ALIASES = {
    "points": "total_points",
    "rebounds": "total_rebounds",
    "assists": "total_assists",
    "steals": "total_steals",
    "blocks": "total_blocks",
    "turnovers": "total_turnovers",
}
"""How a bare stat name reads in a CAREER ranking, where it differs.

A career list is a list of totals: "career points leaders" is the all-time
scoring list LeBron James tops at 43,440, not Michael Jordan's 30.1 a game.
Names absent here read as they do for a season (minutes and fouls have no
career-total metric, so they stay per game).

.. versionadded:: 2.1.0
"""

SEASON_TOTAL_OF = {
    "avg_points": "total_points",
    "avg_rebounds": "total_rebounds",
    "avg_assists": "total_assists",
    "avg_steals": "total_steals",
    "avg_blocks": "total_blocks",
    "avg_turnovers": "total_turnovers",
    "avg_three_pointers_made": "total_three_pointers_made",
}
"""Per-game metric -> its season-total counterpart, for a question whose
``rate`` slot says "total".

.. versionadded:: 2.1.0
"""


def not_a_postseason_copy(columns: tuple[str, ...], alias: str = "t") -> str:
    """SQL that is true unless ``alias``'s postseason row copies the same
    player's regular season.

    437 of the 7,941 postseason rows in ``player_season_stats`` are exactly that.
    Eddy Curry never played a playoff game, and has 527 "playoff games" and
    6,820 playoff points, because each of his regular seasons is stored a second
    time as a postseason - enough to put him second on a career playoff scoring
    list. Since 1993-94 these copies are exactly the postseason rows with no
    postseason box score behind them (checked: every such row is one, and they
    are the only such rows), which is what makes matching on the stat line
    identification rather than a guess. Matching on games plus ``columns`` finds
    the same 437 rows that matching the whole line does.

    .. versionadded:: 2.1.0
    """
    same = " AND ".join(f"rs.{column} IS NOT DISTINCT FROM {alias}.{column}" for column in ("gamesPlayed", *columns))
    return (
        f"NOT EXISTS (SELECT 1 FROM player_season_stats rs WHERE rs.athlete_id = {alias}.athlete_id AND rs.season = {alias}.season "
        f"AND rs.season_type = {REGULAR_SEASON} AND rs.team_id IS NOT DISTINCT FROM {alias}.team_id AND {same})"
    )


def _value_sql(spec: LeaderboardMetric) -> str:
    if spec.ratio:
        made, attempted = spec.ratio
        return f"CAST(t.{made} AS DOUBLE) / NULLIF(t.{attempted}, 0)"
    return f"t.{spec.column}"


def _value_columns(spec: LeaderboardMetric) -> tuple[str, ...]:
    return spec.ratio if spec.ratio else (spec.column,)


def default_min_sample(spec: LeaderboardMetric, season_type: int) -> int | None:
    """The qualifier a season ranking applies when the question gives none.

    .. versionadded:: 2.1.0
    """
    if season_type == POSTSEASON and spec.postseason_min_sample is not None:
        return spec.postseason_min_sample
    return spec.default_min_sample


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
    """Rank players by one known metric, with every correctness rule applied.

    Returns:
        The ranked rows, with the season and the qualifier that produced them.

    Raises:
        LeaderboardError: with a message written for the model to read and act
            on - an unknown metric (with a close-match suggestion), an
            ambiguous team, or a table needing a warehouse flag that was not
            used.

    .. versionchanged:: 2.1.0
       A percentage ranks makes over attempts rather than ESPN's rounded
       column; a postseason can carry its own qualifier; a postseason row that
       copies the regular season is excluded.
    """
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
    effective_min_sample = min_sample if min_sample is not None else default_min_sample(spec, season_type)

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
    select_cols = ["p.display_name AS display_name", f"{_value_sql(spec)} AS value"]
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
    if spec.table == "player_season_stats" and season_type == POSTSEASON:
        where.append(not_a_postseason_copy(_value_columns(spec)))
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
        where.append(f"EXISTS (SELECT 1 FROM player_season_stats pss WHERE pss.athlete_id = t.{spec.id_column} AND pss.season = ? AND pss.season_type = ? AND pss.team_id = ?)")
        params.extend([resolved_season, season_type, resolved_team_id])
    qualify = "QUALIFY ROW_NUMBER() OVER (PARTITION BY t.athlete_id ORDER BY (t.team_id IS NULL) DESC) = 1" if spec.dedup_traded else ""

    sql = f"SELECT {', '.join(select_cols)} {from_clause} WHERE {' AND '.join(where)} {qualify} ORDER BY value DESC NULLS LAST, display_name LIMIT ?"
    params.append(max(1, min(limit, MAX_LIMIT)))
    try:
        cur = con.execute(sql, params)
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
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


def _career_value_sql(career: CareerAggregate) -> str:
    # The denominator counts only seasons whose numerator is on record: 222
    # season rows carry games but NULL stats, and counting their games would
    # quietly lower every average they touch.
    has_value = f"t.{career.numerator} IS NOT NULL"
    if career.weighted:
        return f"SUM(t.{career.numerator} * t.gamesPlayed) / NULLIF(SUM(t.gamesPlayed) FILTER (WHERE {has_value}), 0)"
    if career.denominator:
        return f"CAST(SUM(t.{career.numerator}) AS DOUBLE) / NULLIF(SUM(t.{career.denominator}) FILTER (WHERE {has_value}), 0)"
    return f"SUM(t.{career.numerator})"


def run_career_leaderboard(
    con: duckdb.DuckDBPyConnection,
    metric: str,
    season_type: int = REGULAR_SEASON,
    min_sample: int | None = None,
    limit: int = 10,
) -> CareerLeaderboardResult:
    """Rank players by one metric summed over their whole careers.

    Totals are summed; a per-game value is the career total over career games,
    and a percentage career makes over career attempts - never an average of
    season averages, which weights a 10-game season like an 82-game one. Both
    carry a career qualifier (see :class:`~association.query.metrics.CareerAggregate`).

    The sum runs over per-team season rows, so a traded player's season counts
    once, through its stints, and never through the combined row - see
    :class:`~association.query.metrics.CareerAggregate` for why that row cannot
    be trusted.

    Returns:
        The ranked careers, with the pool's first season and the qualifier.

    Raises:
        LeaderboardError: an unknown metric, a metric with no career form, or a
            season type other than regular season or postseason.

    .. versionadded:: 2.1.0
    """
    spec = LEADERBOARD_METRICS.get(metric)
    if spec is None:
        raise LeaderboardError(f"Error: unknown metric {metric!r}. Known metrics: {sorted(LEADERBOARD_METRICS)}")
    career = spec.career
    if career is None:
        raise LeaderboardError(f"Error: {metric!r} has no career ranking - it needs team context the season rows do not carry, or its data starts too recently to span a career.")
    if season_type not in (REGULAR_SEASON, POSTSEASON):
        raise LeaderboardError(f"Error: a career ranking covers the regular season (2) or the postseason (3) - got {season_type!r}.")
    qualifier = min_sample if min_sample is not None else (career.postseason_min_sample if season_type == POSTSEASON else career.min_sample)

    has_value = f"t.{career.numerator} IS NOT NULL"
    value = _career_value_sql(career)
    select_cols = [
        "p.display_name AS display_name",
        f"{value} AS value",
        "SUM(t.gamesPlayed) AS games",
        "MIN(t.season) AS first_season",
        "MAX(t.season) AS last_season",
    ]
    if spec.ratio:
        select_cols.extend(f"SUM(t.{column}) FILTER (WHERE {has_value}) AS {column}" for column in spec.ratio)

    # team_id IS NOT NULL keeps the per-team rows and drops the combined one. A
    # player who was never traded has exactly one row a season, with his team
    # on it - checked: no season is a lone combined row.
    where = ["t.season_type = ?", "t.team_id IS NOT NULL"]
    params: list[Any] = [season_type]
    if season_type == POSTSEASON:
        where.append(not_a_postseason_copy(tuple(c for c in (career.numerator, career.denominator) if c)))
    having = [f"{value} IS NOT NULL"]
    if qualifier is not None:
        if spec.min_sample_column is None:
            raise LeaderboardError(f"Error: metric {metric!r} has no minimum-sample column to apply min_sample to.")
        having.append(f"SUM(t.{spec.min_sample_column}) FILTER (WHERE {has_value}) >= ?")
        params.append(qualifier)
    params.append(max(1, min(limit, MAX_LIMIT)))

    sql = (
        f"SELECT {', '.join(select_cols)} FROM {spec.table} t JOIN players p ON p.athlete_id = t.{spec.id_column} "
        f"WHERE {' AND '.join(where)} GROUP BY t.{spec.id_column}, p.display_name HAVING {' AND '.join(having)} ORDER BY value DESC, display_name LIMIT ?"
    )
    try:
        cur = con.execute(sql, params)
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
    except Exception as exc:
        raise LeaderboardError(f"SQL error: {exc}") from exc

    coverage = COVERAGE[spec.table]
    return CareerLeaderboardResult(
        metric=metric,
        label=spec.label,
        season_type=season_type,
        min_sample_applied=qualifier,
        min_sample_column=spec.min_sample_column,
        pool_first_season=coverage.first_ranking_season or coverage.first_season,
        rows=[dict(zip(cols, row, strict=True)) for row in rows],
    )
