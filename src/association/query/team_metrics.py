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
  from points and possessions, and opponent points come from ``games``: a team
  season's opponent points are summed over its games there and used only when
  that game count equals ``team_season_stats.gamesPlayed``. The count does not
  always match - the 1999-2000 regular season is 80 games for most teams in
  ``games`` against 82 in ``team_season_stats``, and several postseasons around
  2000 are missing games - and a rating built from 80 games of points allowed
  over 82 games of possessions would be wrong without looking wrong.

.. versionadded:: 2.1.0
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import duckdb


@dataclass(frozen=True)
class TeamMetric:
    """One thing a team can be measured or ranked by.

    ``expression`` is SQL over the per-team row :func:`season_table` builds -
    its column names, never a slot value. None marks the two record metrics,
    which come from ``standings`` (or ``games`` for a postseason) rather than
    from ``team_season_stats`` and are built by :func:`record_table`.

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
    needs_opponent: bool = False


# Turnovers in each era: see the module docstring for why totalTurnovers is
# only usable from 2013. Before it, `turnovers` is the player turnovers alone,
# which leaves out team turnovers (about 0.6 a game) - a small, uniform
# undercount, where the stored total is a large and uniform overcount.
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
    "opponent_points": TeamMetric("opponent points per game", "opp_points / gamesPlayed", True, needs_opponent=True),
    "point_differential": TeamMetric("point differential per game", "(points - opp_points) / gamesPlayed", False, needs_opponent=True),
    "pace": TeamMetric("pace (possessions per game)", "possessions / gamesPlayed", None),
    "offensive_rating": TeamMetric("offensive rating (points per 100 possessions)", "100 * points / possessions", False),
    "defensive_rating": TeamMetric("defensive rating (points allowed per 100 possessions)", "100 * opp_points / possessions", True, needs_opponent=True),
    "net_rating": TeamMetric("net rating (per 100 possessions)", "100 * (points - opp_points) / possessions", False, needs_opponent=True),
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
_ALIASES: dict[str, tuple[str, ...]] = {
    "record": ("record", "records", "wins", "win", "win pct", "win percentage", "winning percentage", "win percent", "standings", "win loss", "win loss record"),
    "losses": ("losses", "loss", "losing record"),
    "points": ("points", "point", "ppg", "points per game", "scoring", "points scored", "avg points"),
    "opponent_points": (
        "opponent points",
        "opponent points per game",
        "opponent ppg",
        "opp points",
        "opp ppg",
        "points allowed",
        "points allowed per game",
        "points against",
        "points given up",
    ),
    "point_differential": ("point differential", "differential", "point diff", "margin", "point margin", "scoring margin", "margin of victory", "plus minus"),
    "pace": ("pace", "pace factor", "possessions", "possessions per game"),
    "offensive_rating": ("offensive rating", "off rating", "ortg", "offensive efficiency", "offensive rtg"),
    "defensive_rating": ("defensive rating", "def rating", "drtg", "defensive efficiency", "defensive rtg", "defense", "defensive"),
    "net_rating": ("net rating", "net efficiency", "net rtg", "nrtg"),
    "field_goal_pct": ("field goal pct", "field goal percentage", "fg pct", "fg%", "fg percentage", "field goal %", "shooting percentage"),
    "three_point_pct": (
        "three point field goal pct",
        "three point field goal percentage",
        "three point pct",
        "three point percentage",
        "3pt pct",
        "3pt%",
        "3pt percentage",
        "3 point pct",
        "3 point percentage",
        "3p%",
        "3p pct",
        "three point shooting",
    ),
    "free_throw_pct": ("free throw pct", "free throw percentage", "ft pct", "ft%", "free throw %"),
    "true_shooting_pct": ("ts pct", "ts%", "true shooting", "true shooting pct", "true shooting percentage"),
    "effective_fg_pct": ("efg pct", "efg%", "efg", "effective field goal pct", "effective field goal percentage", "effective fg pct"),
    "rebounds": ("rebounds", "rebound", "rpg", "total rebounds", "rebounding", "boards", "avg rebounds"),
    "offensive_rebounds": ("offensive rebounds", "offensive rebound", "oreb", "offensive boards"),
    "defensive_rebounds": ("defensive rebounds", "defensive rebound", "dreb", "defensive boards"),
    "assists": ("assists", "assist", "apg", "dimes", "avg assists"),
    "turnovers": ("turnovers", "turnover", "tov", "giveaways", "total turnovers"),
    "steals": ("steals", "steal", "spg"),
    "blocks": ("blocks", "block", "bpg", "blocked shots"),
    "fouls": ("fouls", "foul", "personal fouls"),
    "three_pointers_made": (
        "three point field goals made",
        "threes",
        "threes made",
        "three pointers",
        "three pointers made",
        "3 pointers",
        "3 pointers made",
        "3pm",
        "3pt made",
        "made threes",
    ),
    "three_pointers_attempted": ("three point field goals attempted", "three point attempts", "threes attempted", "3 point attempts", "3pa", "3pt attempts"),
    "field_goals_made": ("field goals made", "field goals", "fgm"),
    "free_throws_made": ("free throws made", "free throws", "ftm"),
    "free_throws_attempted": ("free throws attempted", "free throw attempts", "fta"),
    "points_in_paint": ("points in the paint", "points in paint", "paint points"),
    "fast_break_points": ("fast break points", "fastbreak points", "fast break"),
}

STAT_ALIASES: dict[str, str] = {alias: key for key, aliases in _ALIASES.items() for alias in aliases}
"""Normalized slot text -> :data:`TEAM_METRICS` key.

.. versionadded:: 2.1.0
"""


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


# One row per team per game, from that team's side, over every game that was
# actually played - built from `games` alone, because `games` is the only table
# holding who won and it needs no second table to say which side a team was on.
# What it removes, each measured:
#
# - Rows with no winner. 1999 and 2000 hold 50 and 82 phantom "games" scored
#   0-0, a few hours before the real game between the same two teams; 2001 and
#   2002 hold one each.
# - Duplicates. A team cannot play twice on one Eastern calendar date, so a
#   second row for the same pairing that day is the same game under a second
#   event id - 2003 Dallas-Philadelphia on January 4 and the 2000 Toronto-New
#   York playoff game on April 30. The same partition collapses season 1993,
#   which is a full copy of 1994 under a second label (see coverage.COVERAGE);
#   the 1994 label is the one kept.
# - Nothing else. The NBA Cup final is FLAGGED rather than dropped: it is a
#   season_type 2 game that counts in no standings and no team season totals
#   (the 2026 Knicks' 82 games and 9,549 points in both leave out their 124 in
#   the final), so a regular-season record must skip it, but a record against
#   the team they beat in it should still mention it. It is identified as the
#   last neutral-site regular-season game in Las Vegas each season, which
#   picks out exactly the two teams per season (2024-2026) that `games` holds
#   83 regular-season games for and standings 82.
#
# A game's date is its US Eastern date - games.date is a UTC timestamp, and a
# fixed five-hour shift is enough for the reason given at fetch/parse.py's
# _EASTERN_OFFSET: no NBA game tips in the hour where EST and EDT disagree.
TEAM_GAMES_SQL = """
WITH cup_finals AS (
    SELECT arg_max(event_id, date) AS event_id
    FROM games
    WHERE season_type = 2 AND neutral_site AND venue_city = 'Las Vegas'
    GROUP BY season
),
listed AS (
    SELECT g.event_id, g.season, g.season_type, g.home_team_id, g.away_team_id, g.home_score, g.away_score, g.winner_team_id,
           coalesce(g.neutral_site, false) AS neutral,
           CAST(CAST(REPLACE(g.date, 'Z', '') AS TIMESTAMP) - INTERVAL 5 HOUR AS DATE) AS eastern_date,
           g.event_id IN (SELECT event_id FROM cup_finals) AS cup_final
    FROM games g
    WHERE g.winner_team_id IS NOT NULL
),
played AS (
    SELECT * FROM listed
    QUALIFY row_number() OVER (PARTITION BY season_type, home_team_id, away_team_id, eastern_date ORDER BY season DESC, event_id) = 1
),
team_games AS (
    SELECT season, season_type, eastern_date, event_id, cup_final, neutral, home_team_id AS team_id, away_team_id AS opponent_id, 'home' AS side,
           home_score AS team_score, away_score AS opponent_score, winner_team_id = home_team_id AS won
    FROM played
    UNION ALL
    SELECT season, season_type, eastern_date, event_id, cup_final, neutral, away_team_id AS team_id, home_team_id AS opponent_id, 'away' AS side,
           away_score AS team_score, home_score AS opponent_score, winner_team_id = away_team_id AS won
    FROM played
)
"""
"""A ``WITH`` clause defining ``team_games``: one row per team per played
game, with ``season``, ``season_type``, ``eastern_date``, ``event_id``,
``cup_final``, ``neutral``, ``team_id``, ``opponent_id``, ``side`` (home/away),
``team_score``, ``opponent_score`` and ``won``.

.. versionadded:: 2.1.0
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
    its season STARTED: the games labelled 1990 end on 1991-06-12, which is the
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
    honestly - see :func:`season_table`.

    .. versionadded:: 2.1.0
    """

    team: str
    games: int
    listed_games: int | None
    values: dict[str, float | None] = field(default_factory=dict)


def season_table(con: duckdb.DuckDBPyConnection, season: int, season_type: int) -> list[TeamLine]:
    """Every team's line for one season, from ``team_season_stats`` with
    opponent points from ``games``.

    A metric needing opponent points is None for a team whose games in
    ``games`` do not number its ``gamesPlayed`` - the points allowed would
    cover a different set of games than everything they are divided by. A
    metric is None for every team before its own ``first_season``.

    .. versionadded:: 2.1.0
    """
    scope, params = games_scope(season_type, season)
    metrics = {key: metric for key, metric in TEAM_METRICS.items() if metric.expression is not None}
    selected = ", ".join(f"{metric.expression} AS {key}" for key, metric in metrics.items())
    sql = f"""
{TEAM_GAMES_SQL},
opp AS (
    SELECT team_id, count(*) AS games, sum(opponent_score) AS opp_points FROM team_games WHERE {scope} GROUP BY team_id
),
base AS (
    SELECT t.display_name AS team, ts.gamesPlayed, o.games AS listed_games,
           CASE WHEN o.games = ts.gamesPlayed THEN o.opp_points END AS opp_points,
           ts.points, {POSSESSIONS} AS possessions, {TURNOVERS} AS turnovers_all,
           ts.avgPoints, ts.fieldGoalPct, ts.threePointFieldGoalPct, ts.freeThrowPct, ts.trueShootingPct, ts.effectiveFGPct,
           ts.avgRebounds, ts.avgOffensiveRebounds, ts.avgDefensiveRebounds, ts.avgAssists, ts.avgSteals, ts.avgBlocks, ts.avgFouls,
           ts.avgThreePointFieldGoalsMade, ts.avgThreePointFieldGoalsAttempted, ts.avgFieldGoalsMade, ts.avgFreeThrowsMade, ts.avgFreeThrowsAttempted,
           ts.pointsInPaint, ts.fastBreakPoints
    FROM team_season_stats ts
    JOIN teams t ON t.team_id = ts.team_id
    LEFT JOIN opp o ON o.team_id = ts.team_id
    WHERE ts.season = ? AND ts.season_type = ? AND ts.gamesPlayed > 0
)
SELECT team, gamesPlayed, listed_games, {selected} FROM base ORDER BY team
"""
    rows = con.execute(sql, [*params, season, season_type]).fetchall()
    lines = []
    for row in rows:
        team, games, listed = row[0], row[1], row[2]
        values: dict[str, float | None] = {}
        for index, (key, metric) in enumerate(metrics.items()):
            value = row[3 + index]
            values[key] = None if value is None or season < metric.first_season else float(value)
        lines.append(TeamLine(team=team, games=int(games), listed_games=None if listed is None else int(listed), values=values))
    return lines


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


def record_table(con: duckdb.DuckDBPyConnection, season: int, season_type: int) -> list[TeamRecord]:
    """Every team's record for one season: ``standings`` for a regular season,
    the authoritative source, and a tally of ``games`` for a postseason, which
    standings do not cover.

    .. versionadded:: 2.1.0
    """
    if season_type == 2:
        rows = con.execute(
            "SELECT t.display_name, s.wins, s.losses FROM standings s JOIN teams t ON t.team_id = s.team_id WHERE s.season = ? AND s.wins + s.losses > 0 ORDER BY 1",
            [season],
        ).fetchall()
    else:
        scope, params = games_scope(season_type, season)
        rows = con.execute(
            f"{TEAM_GAMES_SQL} SELECT t.display_name, sum(won::INT), sum((NOT won)::INT) FROM team_games tg JOIN teams t ON t.team_id = tg.team_id WHERE {scope} GROUP BY 1 ORDER BY 1",
            params,
        ).fetchall()
    return [TeamRecord(team=name, wins=int(wins), losses=int(losses)) for name, wins, losses in rows]


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
