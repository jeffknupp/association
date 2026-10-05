"""The "top N players by X" query, extracted so every caller - the template,
and the agent's ``get_leaderboard`` tool while there was one - ranks by the
same code rather than two implementations that can drift.

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

import math
from dataclasses import dataclass, field
from difflib import get_close_matches
from typing import Any

import duckdb

from association.nba.coverage import COVERAGE, POSTSEASON, REGULAR_SEASON
from association.nba.franchises import season_name_sql
from association.nba.season import current_season

from .entities import Ambiguous, Entity, NotFound, resolve_team
from .measures import CAREER_METRIC_ALIASES as CAREER_METRIC_ALIASES
from .measures import METRIC_ALIASES as METRIC_ALIASES
from .measures import resolve_metric as resolve_metric
from .metrics import EXTRA_FIELD_COLUMNS, LEADERBOARD_METRICS, SEASON_TYPE_LABELS, CareerAggregate, LeaderboardMetric

# The most rows a ranking lists: the reader's one cap on a question's count
# (``reading.MAX_LIMIT``, which ``_clamp_limit`` applies before a ranking is
# asked for). Applied to the SQL LIMIT itself as well, so a direct caller's
# count cannot fetch more. It was a cap of its own, 100, for the retired
# agent's model-supplied ``limit``; with the agent gone the ranking reader's
# clamped count (``compose.rankings``) is the only one that reaches here.
from .reading import MAX_LIMIT
from .result import Grouped

# What a `scales_with_schedule` floor was calibrated against - see
# `metrics.LeaderboardMetric.scales_with_schedule` and `default_min_sample`
# below. Not `nba.season`'s business: this is the schedule length ts_pct's 550
# and efg_pct's 480 were measured against (ISSUES.md #13), not a fact about
# any one season.
SCHEDULE_BASE_GAMES = 82


class LeaderboardError(Exception):
    """Carries a message naming what the ranking could not do - an unknown
    metric with the nearest real one, a season type out of range, a team
    matching nothing - which the template answers with or refuses on."""


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
    #: Each row's athlete id, aligned by index with ``rows`` - kept OUTSIDE the
    #: row dicts rather than as one more key in them, so it never rides along
    #: into an answer's ``data`` (which carries ``rows`` verbatim for the
    #: page to render) the way a raw id leaking into an answer was already a
    #: fixed regression once. A caller that needs a fact the ranked table itself
    #: does not carry - each player's team, for a leaderboard that asked to
    #: see it (F017, ISSUES.md) - looks it up by these ids.
    #:
    #: .. versionadded:: 4.4.0
    athlete_ids: list[str | None] = field(default_factory=list)


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

    436 of the 7,941 postseason rows in ``player_season_stats`` are exactly that.
    Eddy Curry never played a playoff game, and has 527 "playoff games" and
    6,820 playoff points, because each of his regular seasons is stored a second
    time as a postseason - enough to put him second on a career playoff scoring
    list. Since 1993-94 these copies are exactly the postseason rows with no
    postseason box score behind them (checked: every such row is one, and they
    are the only such rows), which is what makes matching on the stat line
    identification rather than a guess. Matching on games plus ``columns`` finds
    the same 436 rows that matching the whole line does.

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


def _team_games_for_season(con: duckdb.DuckDBPyConnection, season: int) -> int | None:
    """How many regular-season games a team typically played that season, read
    from the warehouse rather than a hardcoded per-season table.

    The MEDIAN of each team's own ``real_games`` count (home starts plus away
    starts), rounded to the nearest game. Not ``games``, which carries 151 rows
    that are not games (AGENTS.md, "Working on the query path" -> "Saying what
    you measured").

    MEDIAN over MAX or a fixed per-season constant because a shortened season
    is not always uniform across teams: 2020's ``real_games`` counts range
    64-75 (the pandemic hiatus, then an 8-game bubble restart for 22 of the 30
    teams; the other 8 never resumed), while 2021's 72-game season gave every
    team exactly 72. The median reads 72 for both - one number, not three, and
    the same number a person would call "the 2020 season" and "the 2021
    season" by. A full 82-game season still reads 82 despite the two Cup-final
    teams' extra counted game (DATA.md, "The NBA Cup final is stored as a
    regular-season game") landing in the tails rather than the middle, so a
    normal season's floor is untouched by this function existing at all.

    Returns ``None`` on anything that stops the query - most commonly
    ``real_games`` not existing, true of every fixture in this test suite that
    predates this function and is not this fix's to update - so a caller falls
    back to the unscaled floor rather than raising.

    .. versionadded:: 4.1.0
    """
    try:
        row = con.execute(
            "WITH per_team AS ("
            "  SELECT home_team_id AS team_id, COUNT(*) AS n FROM real_games WHERE season = ? AND season_type = ? GROUP BY 1"
            "  UNION ALL"
            "  SELECT away_team_id AS team_id, COUNT(*) AS n FROM real_games WHERE season = ? AND season_type = ? GROUP BY 1"
            "), totals AS (SELECT team_id, SUM(n) AS games FROM per_team GROUP BY 1)"
            "SELECT MEDIAN(games) FROM totals",
            [season, REGULAR_SEASON, season, REGULAR_SEASON],
        ).fetchone()
    except duckdb.Error:
        # Missing real_games (an older warehouse, or a fixture built before
        # this floor scaled) - degrade to the flat, unscaled floor rather than
        # taking the whole leaderboard down over a schedule-length lookup.
        return None
    if row is None or row[0] is None:
        return None
    return math.floor(row[0] + 0.5)


def _scale_min_sample(base: int, team_games: int) -> int:
    """Scale an 82-game-season floor to a season whose teams played
    ``team_games`` games instead, rounding half up.

    Half up, not half to even or truncated, because a qualifier exists to
    exclude a small sample on purpose: at the one boundary where the scaled
    value lands exactly on a half-game, the deliberate choice is the stricter
    (higher) floor, not the more permissive one. Away from that boundary the
    rounding direction is not a choice at all - it is nearest, same as it
    would be by any other rule.

    .. versionadded:: 4.1.0
    """
    return math.floor(base * team_games / SCHEDULE_BASE_GAMES + 0.5)


def default_min_sample(
    spec: LeaderboardMetric,
    season_type: int,
    con: duckdb.DuckDBPyConnection | None = None,
    season: int | None = None,
) -> int | None:
    """The qualifier a season ranking applies when the question gives none.

    A postseason floor is its own constant (see
    ``LeaderboardMetric.postseason_min_sample``), never scaled by this
    function. A regular-season floor with ``scales_with_schedule`` set is
    scaled to the season's own team-game count when ``con`` and ``season`` are
    given; without them - or without a usable ``real_games`` for that season -
    it falls back to the flat, 82-game-calibrated value, exactly as it read
    before ``scales_with_schedule`` existed.

    .. versionadded:: 2.1.0

    .. versionchanged:: 4.1.0
       Added ``con`` and ``season``, and the scaling itself - see
       ``LeaderboardMetric.scales_with_schedule`` and ISSUES.md #13.
    """
    if season_type == POSTSEASON and spec.postseason_min_sample is not None:
        return spec.postseason_min_sample
    base = spec.default_min_sample
    if base is None or not spec.scales_with_schedule or season_type != REGULAR_SEASON or con is None or season is None:
        return base
    team_games = _team_games_for_season(con, season)
    return base if team_games is None else _scale_min_sample(base, team_games)


def _run_leaderboard_validate(metric: str, season_type: int, fields: list[str] | None) -> LeaderboardMetric:
    """The three input checks ``run_leaderboard`` raises on, in the order they
    are validated: an unknown metric (with a close-match suggestion), an
    invalid ``season_type``, and any ``fields`` entry outside the known set."""
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
    return spec


def _run_leaderboard_team(con: duckdb.DuckDBPyConnection, team: str) -> tuple[str, str]:
    """Resolve a team filter to its id and display name, or raise - called only
    when ``team`` is not None, so the caller's own None case never reaches this."""
    match resolve_team(con, team):
        case Entity() as resolved:
            return resolved.id, resolved.name
        case Ambiguous(candidates=candidates):
            raise LeaderboardError(f"Error: {team!r} matches more than one team: {candidates}. Be more specific.")
        case NotFound():
            raise LeaderboardError(f"Error: no team found matching {team!r}.")


def _run_leaderboard_select(spec: LeaderboardMetric, fields: list[str] | None, resolved_season: int, season_type: int) -> tuple[list[str], str, list[Any]]:
    """SELECT columns and FROM clause, plus the params the optional ``fields``
    join needs.

    A box-score join, keyed on (athlete_id, season, season_type) and deduped
    to the season-combined row (same pattern as a traded player's
    season-total row), is needed for `fields` regardless of which table the
    ranked metric itself lives in - player_season_stats is the one table
    every metric can join to this way.
    """
    # athlete_id rides along unconditionally - it costs nothing (it is already
    # the join key) and it is what lets a caller look up something the ranked
    # table itself does not carry, such as each player's team (F017, ISSUES.md).
    select_cols = ["p.display_name AS display_name", "p.athlete_id AS athlete_id", f"{_value_sql(spec)} AS value"]
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
    return select_cols, from_clause, params


def _run_leaderboard_where(
    spec: LeaderboardMetric,
    resolved_season: int,
    season_type: int,
    season_type_value: int | str,
    effective_min_sample: int | None,
    resolved_team_id: str | None,
    metric: str,
) -> tuple[list[str], list[Any]]:
    """The WHERE clause and its params: season, season_type, the postseason-copy
    exclusion, the minimum-sample qualifier, and an optional team filter - in
    the order ``run_leaderboard`` applies them."""
    where = [f"t.{spec.season_column} = ?"]
    params: list[Any] = [resolved_season]
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
    return where, params


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

    .. versionchanged:: 4.1.0
       A ``scales_with_schedule`` metric's default floor is scaled to the
       season's own team-game count in a shortened season (ISSUES.md #13),
       and ``min_sample_applied`` on the result always names the number
       actually applied, scaled or not.
    """
    spec = _run_leaderboard_validate(metric, season_type, fields)

    resolved_season = season if season is not None else current_season()
    season_type_value: int | str = SEASON_TYPE_LABELS[season_type] if spec.season_type_is_string else season_type
    effective_min_sample = min_sample if min_sample is not None else default_min_sample(spec, season_type, con, resolved_season)

    resolved_team_id: str | None = None
    resolved_team_name: str | None = None
    if team is not None:
        resolved_team_id, resolved_team_name = _run_leaderboard_team(con, team)

    select_cols, from_clause, select_params = _run_leaderboard_select(spec, fields, resolved_season, season_type)
    where, where_params = _run_leaderboard_where(spec, resolved_season, season_type, season_type_value, effective_min_sample, resolved_team_id, metric)
    params = select_params + where_params
    qualify = "QUALIFY ROW_NUMBER() OVER (PARTITION BY t.athlete_id ORDER BY (t.team_id IS NULL) DESC) = 1" if spec.dedup_traded else ""

    sql = f"SELECT {', '.join(select_cols)} {from_clause} WHERE {' AND '.join(where)} {qualify} ORDER BY value DESC NULLS LAST, display_name LIMIT ?"
    params.append(max(1, min(limit, MAX_LIMIT)))
    try:
        row_dicts = _rows(con, sql, params)
    except Exception as exc:  # e.g. the table needs a warehouse flag that wasn't used
        raise LeaderboardError(f"SQL error: {exc}" + (f" (requires: {spec.requires})" if spec.requires else "")) from exc

    # athlete_id is popped back OUT of each row dict here - it rides the query
    # only to be resolvable, never as a key of `rows` itself (see
    # LeaderboardResult.athlete_ids' own docstring for why).
    athlete_ids = [row_dict.pop("athlete_id", None) for row_dict in row_dicts]
    return LeaderboardResult(
        metric=metric,
        label=spec.label,
        season=resolved_season,
        season_type=season_type if spec.has_season_type else None,
        min_sample_applied=effective_min_sample,
        min_sample_column=spec.min_sample_column,
        team=team,
        team_name=resolved_team_name,
        rows=row_dicts,
        athlete_ids=athlete_ids,
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
        rows = _rows(con, sql, params)
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
        rows=rows,
    )


def _rows(con: duckdb.DuckDBPyConnection, sql: str, params: list[Any]) -> list[dict[str, Any]]:
    """The one place this relation executes a ranking's statement and hands
    its rows back as mappings, column names as the SELECT gave them - the
    season line's counterpart of :func:`~association.query.compose.core.rows_of`.
    The schedule-length lookup (:func:`_team_games_for_season`) runs on its
    own, since a missing ``real_games`` is not an error there."""
    cur = con.execute(sql, params)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]


MIN_SAMPLE_LABELS: dict[str, str] = {
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
"""A qualifying column as a reader names it - its real name ("total_minutes",
"gamesPlayed") is not something to put in front of one. The ``of`` of a
ranking's ``minimum`` decision.

.. versionadded:: 5.0.0
   Moved from ``templates.players`` with the leaderboard's words.
"""


def most_recent_teams(con: duckdb.DuckDBPyConnection, athlete_ids: list[str], season: int, season_type: int) -> tuple[dict[str, str], bool]:
    """Each athlete's team for a ranking that asked to see it (F017,
    ISSUES.md): the team he played his most recent game for that season and
    season type - read off ``player_game_log``, the one table that orders a
    traded player's stints by date, unlike the ranked table itself (whose own
    "combined" row for a traded player has no single team at all). Team names
    are read for the season asked about (:func:`~association.nba.franchises.season_name_sql`),
    since a franchise's own name can differ by season.

    Returns each athlete's team by id, and whether ANY of them played for more
    than one team that season - the fact behind the "most recent team" note.

    .. versionadded:: 5.0.0
       Moved from ``templates.players._leaderboard_team_names``, unchanged.
    """
    placeholders = ", ".join("?" for _ in athlete_ids)
    rows = _rows(
        con,
        f"""
        SELECT athlete_id, team, traded FROM (
            SELECT pgl.athlete_id AS athlete_id,
                   {season_name_sql("pgl.team_id", "pgl.season", "t.display_name")} AS team,
                   COUNT(DISTINCT pgl.team_id) OVER (PARTITION BY pgl.athlete_id) > 1 AS traded,
                   ROW_NUMBER() OVER (PARTITION BY pgl.athlete_id ORDER BY pgl.game_date DESC) AS rn
            FROM player_game_log pgl JOIN teams t ON t.team_id = pgl.team_id
            WHERE pgl.season = ? AND pgl.season_type = ? AND pgl.athlete_id IN ({placeholders})
        ) WHERE rn = 1
        """,
        [season, season_type, *athlete_ids],
    )
    names = {row["athlete_id"]: row["team"] for row in rows}
    return names, any(row["traded"] for row in rows)


@dataclass(frozen=True)
class SeasonLineRanking:
    """The season line ranked, as the reader takes it: the result object the
    statement produced (its season, qualifier, team filter, career pool) and
    its rows as a :class:`~association.query.result.Grouped` body by
    ``player`` - a row per player, ``key`` his name, ``rank`` (tied values
    share one), and ``values`` by measure: the ranked figure under the
    metric's name (``ranked_by``), then every other column the row carries in
    the statement's order (a percentage's makes and attempts, a career's
    games and seasons, the "also" columns asked for, the team). ``traded``
    says whether a shown player played for more than one team that season,
    where the team column was asked for.

    .. versionadded:: 5.0.0
    """

    result: LeaderboardResult | CareerLeaderboardResult
    body: Grouped
    traded: bool = False


def rank_season_line(
    con: duckdb.DuckDBPyConnection,
    metric: str,
    *,
    career: bool,
    season: int | None,
    season_type: int,
    team: str | None,
    fields: list[str],
    limit: int,
) -> SeasonLineRanking:
    """The season line's ranking, the one door the reader calls
    (``compose.rankings.read_leaderboard``): a season's ranking
    (:func:`run_leaderboard`, with its default season, qualifier, traded-player
    dedup and postseason-copy exclusion) or a career's
    (:func:`run_career_leaderboard`, over its own pool), with each shown
    player's most recent team looked up where ``fields`` asks for ``"team"``
    (:func:`most_recent_teams`). ``fields`` beside ``"team"`` are the box-score
    columns :data:`~association.query.metrics.EXTRA_FIELD_COLUMNS` names; a
    career ranking takes none, which its caller refuses before asking.

    Raises:
        LeaderboardError: whatever the ranking raises, and a team column asked
            of a metric with no season type to look a team up by.

    .. versionadded:: 5.0.0
    """
    if career:
        found_career = run_career_leaderboard(con, metric, season_type=season_type, limit=limit)
        return SeasonLineRanking(result=found_career, body=_ranking_body(metric, found_career.rows))
    show_team = "team" in fields
    box_fields = [f for f in fields if f != "team"]
    found = run_leaderboard(con, metric, season=season, season_type=season_type, team=team, fields=box_fields or None, limit=limit)
    traded = False
    if show_team:
        if found.season_type is None:
            # A metric with no season_type (a fingerprint-shaped one) has nothing
            # for player_game_log's own season_type column to join on - refused
            # rather than silently dropping the team column nobody asked to lose.
            raise LeaderboardError(f"{found.label} has no season type to look a team up by")
        traded = _ranking_show_teams(con, found)
    return SeasonLineRanking(result=found, body=_ranking_body(metric, found.rows), traded=traded)


def _ranking_show_teams(con: duckdb.DuckDBPyConnection, result: LeaderboardResult) -> bool:
    """Each shown row's team, set on it last (F017, ISSUES.md), and whether
    anyone shown played for more than one team that season."""
    assert result.season_type is not None  # the caller already refused this case
    ids = [athlete_id for athlete_id in result.athlete_ids if athlete_id is not None]
    if not ids:
        for row in result.rows:
            row["team"] = "-"
        return False
    names, traded = most_recent_teams(con, ids, result.season, result.season_type)
    for athlete_id, row in zip(result.athlete_ids, result.rows, strict=True):
        row["team"] = names.get(athlete_id, "-") if athlete_id is not None else "-"
    return traded


def _ranking_body(metric: str, rows: list[dict[str, Any]]) -> Grouped:
    """A ranking's rows as a :class:`~association.query.result.Grouped` body:
    the order the statement gave (the figure, highest first, then the name),
    and a competition rank - two players on the same figure share a rank, and
    the next takes the place after both."""
    ranked: list[dict[str, Any]] = []
    rank = 0
    previous: Any = object()
    for place, row in enumerate(rows, start=1):
        value = row.get("value")
        if place == 1 or value != previous:
            rank = place
        previous = value
        values = {metric: value, **{key: cell for key, cell in row.items() if key not in ("display_name", "value")}}
        ranked.append({"key": row["display_name"], "rank": rank, "values": values})
    return Grouped(by="player", ranked_by=metric, rows=tuple(ranked))
