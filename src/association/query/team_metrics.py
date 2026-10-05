"""Team metrics: the whitelist a team question's ``stat`` slot is mapped
through, and where each number behind it comes from.

The team counterpart to :mod:`association.query.metrics`. Kept apart from the
templates because most of it is measurement rather than code - which of ESPN's
115 ``team_season_stats`` columns can be believed, and from which season - and
that is worth reading in one place.

What was measured, against the built warehouse:

- ``possessions`` is a SEASON TOTAL, not a per-game figure: 8,173.92 for the
  2026 Knicks over 82 games. From 2013 on it equals
  ``FGA - OREB + totalTurnovers + 0.44 * FTA`` to the last decimal, for every
  team. Before 2013 it does not, because ``totalTurnovers`` counts every
  turnover twice there (``teamTurnovers`` is stored equal to ``turnovers``
  rather than as the handful of team turnovers it is), so ESPN's own
  possession count runs ~15 a game high - 114 a game in 1994, where the real
  figure is about 96. :data:`POSSESSIONS` recomputes it with the turnover
  column that is right in each era, which reproduces ESPN's number exactly from
  2013 and from 2009-2012, and replaces the inflated one before 2009.
- ``paceFactor`` is 0 for every team before 2003 and inherits the inflated
  possessions from 2003 to 2008, so pace is possessions per game from the same
  formula rather than ESPN's column.
- ``pointsInPaint`` and ``fastBreakPoints`` are 0 for every team before 2009.
- ``plusMinus`` is -1 for every team in every season, and is not used.
- There is no offensive or defensive rating column. Both are derived here,
  from points and possessions, and opponent points come from ``real_games``
  (:mod:`association.fetch.repairs.real_games`, the filtered list team_games is built
  from - see :data:`TEAM_GAMES_SQL`): a team season's opponent points are
  summed over its games there and used only when that game count equals
  ``team_season_stats.gamesPlayed``. The count does not always match - the
  1999-2000 regular season is 80 games for most teams there against 82 in
  ``team_season_stats``, and several postseasons around 2000 are missing
  games - and a rating built from 80 games of points allowed over 82 games of
  possessions would be wrong without looking wrong.
- ``team_season_stats.avgRebounds`` is NOT the game-level fault
  :mod:`association.query.conditions` works around (DATA.md, "The team
  ``totalRebounds`` column stops including team rebounds in 2022"). Checked
  against the 2026-09-17 warehouse: it equals ``avgOffensiveRebounds +
  avgDefensiveRebounds`` to rounding in every regular season from 1994 to
  2026, 2008 included, with no drop across 2021-2022 - unlike
  ``team_box_stats.totalRebounds`` (the per-game column), ESPN's season
  aggregate never counted a separate bucket of team-credited rebounds. So
  ``TEAM_METRICS["rebounds"]`` reads it unchanged. The *season-total*
  ``team_season_stats.totalRebounds`` column is a different story - it carries
  the same fault as the per-game one (53.17/game in 2020, 49.00 in 2021,
  44.45 in 2022, read as ``totalRebounds / gamesPlayed``) - but nothing here
  reads it; only ``run_sql`` can reach it.

.. versionadded:: 2.1.0
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import duckdb

from .measures import STAT_ALIASES as STAT_ALIASES
from .team_games import TEAM_GAMES_SQL as TEAM_GAMES_SQL


@dataclass(frozen=True)
class TeamMetric:
    """One thing a team can be measured or ranked by.

    ``expression`` is SQL over the per-team row
    :func:`association.query.team_seasons.team_lines_statement` builds - its
    column names, never a slot value. None marks the two record metrics,
    which come from ``standings`` (or ``games`` for a postseason) rather than
    from ``team_season_stats`` and are read by
    :func:`association.query.team_seasons.team_records_statement`.

    ``lower_is_better`` decides what "best" means: True for a stat a team wants
    little of (turnovers, points allowed), False for one it wants more of, and
    None for one with no better end at all (pace, attempts), which is ranked
    highest first unless a question asked for the other end.

    .. versionadded:: 2.1.0
    """

    label: str
    expression: str | None
    lower_is_better: bool | None
    percent: bool = False
    first_season: int = 1994
    first_season_reason: str = ""


# Turnovers in each era: see the module docstring for why totalTurnovers is
# only usable from 2013. Before it, `turnovers` is already the full count, team
# turnovers included: summed over a season's box scores, it equals the box
# `totalTurnovers` for 24-27 of 30 teams and the player-only sum for none
# (measured for 1998, 2005, 2010 and 2012; the gap over the player sum is
# ~0.65 a game, which is the team turnovers). An earlier version of this
# comment called it "the player turnovers alone", which is wrong. From 2013
# the two columns swap roles: `totalTurnovers` is the box total, and
# `turnovers` is the player-only figure (2020: the box total for 0 teams).
TURNOVERS = "CASE WHEN ts.season >= 2013 THEN ts.totalTurnovers ELSE ts.turnovers END"
"""SQL for a team season's total turnovers, over ``team_season_stats`` aliased ``ts``.

.. versionadded:: 2.1.0
"""

POSSESSIONS = f"(ts.fieldGoalsAttempted - ts.offensiveRebounds + {TURNOVERS} + 0.44 * ts.freeThrowsAttempted)"
"""SQL for a team season's possessions, a season total. Equal to ESPN's own
``possessions`` column from 2009 on, and in place of it before.

.. versionadded:: 2.1.0
"""

_PAINT_REASON = "ESPN's team season stats hold no points in the paint or fast-break points before 2008-09 - the columns read 0 for every team"

TEAM_METRICS: dict[str, TeamMetric] = {
    "record": TeamMetric("record", None, False),
    "losses": TeamMetric("losses", None, True),
    "points": TeamMetric("points per game", "avgPoints", False),
    "opponent_points": TeamMetric("opponent points per game", "opp_points / gamesPlayed", True),
    "point_differential": TeamMetric("point differential per game", "(points - opp_points) / gamesPlayed", False),
    "pace": TeamMetric("pace (possessions per game)", "possessions / gamesPlayed", None),
    "offensive_rating": TeamMetric("offensive rating (points per 100 possessions)", "100 * points / possessions", False),
    "defensive_rating": TeamMetric("defensive rating (points allowed per 100 possessions)", "100 * opp_points / possessions", True),
    "net_rating": TeamMetric("net rating (per 100 possessions)", "100 * (points - opp_points) / possessions", False),
    "field_goal_pct": TeamMetric("field goal percentage", "fieldGoalPct", False, percent=True),
    "three_point_pct": TeamMetric("3-point percentage", "threePointFieldGoalPct", False, percent=True),
    "free_throw_pct": TeamMetric("free throw percentage", "freeThrowPct", False, percent=True),
    "true_shooting_pct": TeamMetric("true shooting percentage", "trueShootingPct", False, percent=True),
    "effective_fg_pct": TeamMetric("effective field goal percentage", "effectiveFGPct", False, percent=True),
    "rebounds": TeamMetric("rebounds per game", "avgRebounds", False),
    "offensive_rebounds": TeamMetric("offensive rebounds per game", "avgOffensiveRebounds", False),
    "defensive_rebounds": TeamMetric("defensive rebounds per game", "avgDefensiveRebounds", False),
    "assists": TeamMetric("assists per game", "avgAssists", False),
    "turnovers": TeamMetric("turnovers per game", "turnovers_all / gamesPlayed", True),
    "steals": TeamMetric("steals per game", "avgSteals", False),
    "blocks": TeamMetric("blocks per game", "avgBlocks", False),
    "fouls": TeamMetric("fouls per game", "avgFouls", True),
    "three_pointers_made": TeamMetric("3-pointers made per game", "avgThreePointFieldGoalsMade", False),
    "three_pointers_attempted": TeamMetric("3-point attempts per game", "avgThreePointFieldGoalsAttempted", None),
    "field_goals_made": TeamMetric("field goals made per game", "avgFieldGoalsMade", False),
    "free_throws_made": TeamMetric("free throws made per game", "avgFreeThrowsMade", False),
    "free_throws_attempted": TeamMetric("free throw attempts per game", "avgFreeThrowsAttempted", None),
    "points_in_paint": TeamMetric("points in the paint per game", "pointsInPaint / gamesPlayed", False, first_season=2009, first_season_reason=_PAINT_REASON),
    "fast_break_points": TeamMetric("fast-break points per game", "fastBreakPoints / gamesPlayed", False, first_season=2009, first_season_reason=_PAINT_REASON),
}
"""Every metric a team question can be answered with, by key.

.. versionadded:: 2.1.0
"""

# The line team_stat gives when no stat was named: scoring on both ends, the
# pace and efficiency that explain it, and the three counting stats people ask
# for most.
DEFAULT_TEAM_LINE: tuple[str, ...] = (
    "points",
    "opponent_points",
    "pace",
    "offensive_rating",
    "defensive_rating",
    "net_rating",
    "three_point_pct",
    "rebounds",
    "assists",
    "turnovers",
)
"""The metrics a team's season line shows when no stat was named.

.. versionadded:: 2.1.0
"""

# Slot text -> metric key. A whitelist, not a fuzzy match: `stat` is the one
# REQUIRED router slot, so the model fills it on every question, and the words
# it chooses are free text. An unlisted word refuses; it is never guessed at.
# Keys are in normalize_stat's form: camelCase split, lower case, underscores
# and hyphens as spaces - which is how "threePointFieldGoalPct", the router's
# own stat name, arrives here as "three point field goal pct".


def normalize_stat(text: str) -> str:
    """The form :data:`STAT_ALIASES` is keyed in: camelCase split into words,
    lower case, underscores and hyphens as spaces, whitespace collapsed.

    .. versionadded:: 2.1.0
    """
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    return " ".join(spaced.replace("_", " ").replace("-", " ").casefold().split())


def resolve_team_metric(stat: object) -> str | None:
    """The :data:`TEAM_METRICS` key a ``stat`` slot names, or None - for an
    absent slot and for one this whitelist does not know alike, so the caller
    decides which of those is a refusal.

    .. versionadded:: 2.1.0
    """
    if not isinstance(stat, str) or not stat.strip():
        return None
    return STAT_ALIASES.get(normalize_stat(stat))


# TEAM_GAMES_SQL - the WITH clause defining `team_games`, one row per team per
# played game - now lives in association.query.team_games (the relation
# module: TeamNarrowed and the readers built over it) and is imported back
# under this same name, since every module here that reads it wrote
# `team_metrics.TEAM_GAMES_SQL` before the move and none of them needed to
# change. See that module's docstring for what the relation guarantees (the
# phantom 1993 season cannot double a game, a postseason is selected by the
# calendar year it was played in) and what it deliberately still leaves
# undecided (whether the NBA Cup final counts - :func:`games_scope` below
# excludes it from a regular-season RECORD; a plain game list or count does
# not, since the final is a real game the two teams played).

RATING_NOTE = "Ratings and pace count possessions as FGA - OREB + TOV + 0.44 x FTA."
"""The definition beneath a team's ratings and pace - the possessions
:data:`POSSESSIONS` counts (a ``definition`` note, ``term``
``rating_formula``, said by ``compose.say``).

.. versionadded:: 5.0.0
   Moved from ``templates.teams``.
"""


FIRST_FULL_REGULAR_SEASON = 1994
"""The first season ``games`` holds every regular-season game of. Before it,
the table holds one team's 82 games in 1988, 1991 and 1992 and none in
1989-90, and 1993 is a copy of 1994.

.. versionadded:: 2.1.0
"""


def games_scope(season_type: int, season: int | None) -> tuple[str, list[Any]]:
    """The ``WHERE`` predicate over ``team_games`` for one season, or for every
    season when ``season`` is None, with its parameters.

    A regular season is selected by its label, from
    :data:`FIRST_FULL_REGULAR_SEASON`, and never includes the NBA Cup final. A
    postseason is selected by the CALENDAR YEAR it was played in, not by its
    label, because ``games`` labels every postseason before 1994 by the year
    its season STARTED: the games labeled 1990 end on 1991-06-12, which is the
    1991 Finals. Every postseason is played inside the calendar year its season
    is named for (the 2020 bubble ended in October 2020), so the year is exact
    for all of them, and from 1994 on it agrees with the label for every game.

    .. versionadded:: 2.1.0
    """
    if season_type == 3:
        if season is None:
            return "season_type = 3", []
        return "season_type = 3 AND year(eastern_date) = ?", [season]
    if season is None:
        return "season_type = 2 AND season >= ? AND NOT cup_final", [FIRST_FULL_REGULAR_SEASON]
    return "season_type = 2 AND season = ? AND NOT cup_final", [season]


@dataclass
class TeamLine:
    """One team's season, every :data:`TEAM_METRICS` value that is not a
    record, keyed by metric. A value is None where it cannot be computed
    honestly - see :func:`association.query.team_seasons.team_lines`.

    .. versionadded:: 2.1.0
    """

    team: str
    games: int
    listed_games: int | None
    values: dict[str, float | None] = field(default_factory=dict)


@dataclass(frozen=True)
class TeamRecord:
    """One team's win-loss record for a season.

    .. versionadded:: 2.1.0
    """

    team: str
    wins: int
    losses: int

    @property
    def win_pct(self) -> float:
        """Wins over games, 0 for a team with none."""
        games = self.wins + self.losses
        return self.wins / games if games else 0.0


def season_table(con: duckdb.DuckDBPyConnection, season: int, season_type: int) -> list[TeamLine]:
    """Every team's line for one season (:func:`association.query.team_seasons.team_lines_statement`).

    .. versionadded:: 2.1.0

    .. versionchanged:: 5.0.0
       Its statement is the team-season relation's; this runs it for the
       ``team_leaderboard`` template until that retires.
    """
    from association.query.team_seasons import team_lines, team_lines_statement

    statement = team_lines_statement(season, season_type)
    return team_lines(con.execute(statement.sql, statement.params).fetchall(), season)


def record_table(con: duckdb.DuckDBPyConnection, season: int, season_type: int) -> list[TeamRecord]:
    """Every team's record for one season (:func:`association.query.team_seasons.team_records_statement`).

    .. versionadded:: 2.1.0

    .. versionchanged:: 5.0.0
       Its statements are the team-season relation's; this runs them for the
       ``team_leaderboard`` template until that retires.
    """
    from association.query.team_seasons import team_records, team_records_statement

    statement = team_records_statement(season, season_type)
    return team_records(con.execute(statement.sql, statement.params).fetchall())


def ranked(values: dict[str, float], descending: bool) -> list[tuple[int, str, float]]:
    """(rank, team, value) in the order asked for. Ties share a rank and the
    next rank skips past them (1, 2, 2, 4), and a tie is listed by name so the
    order is stable.

    .. versionadded:: 2.1.0
    """
    ordered = sorted(values.items(), key=lambda item: (-item[1] if descending else item[1], item[0]))
    out: list[tuple[int, str, float]] = []
    for position, (team, value) in enumerate(ordered, start=1):
        rank = out[-1][0] if out and out[-1][2] == value else position
        out.append((rank, team, value))
    return out


def descending_for(metric: TeamMetric, rank: str | None) -> bool:
    """Whether a ranking asked for with ``rank`` lists the highest value first.

    "most" and "fewest" are raw ends of the scale; "best" and "worst" depend on
    the metric, which is why they are four words and not two pairs - the
    fewest turnovers are the best, the fewest points are the worst. No rank
    means best. A metric with no better end is ranked highest first for
    "best", and lowest first for "worst".

    .. versionadded:: 2.1.0
    """
    if rank == "most":
        return True
    if rank == "fewest":
        return False
    best_descending = metric.lower_is_better is not True
    return not best_descending if rank == "worst" else best_descending
