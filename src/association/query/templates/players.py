"""Player rankings and lines: threshold counts, leaderboards, a player's stats, comparisons, multi-season history and single-game highs.

.. versionadded:: 3.0.0
   Split out of the former ``association.query.templates`` module.
"""

from __future__ import annotations

from dataclasses import dataclass

import duckdb

from association.query.answer import Reply
from association.query.measures import STAT_LINE as STAT_LINE
from association.query.reading import Scope, Unsupported, scope_reads_box_scores

from ..conditions import box_source
from ..player_games import scope_without_guard
from .common import PLAYER_STAT_COLUMNS


def rebuilt_in_scope(con: duckdb.DuckDBPyConnection, season: int | None, season_type: int, athlete_id: str | None) -> int:
    """How many games in scope carry a line rebuilt from play-by-play.

    Used to explain a refusal rather than to answer: when the stat asked for is
    outside :data:`REBUILT_STATS`, "no games with a box score" is true of the
    fetched lines and hides that rebuilt ones exist and were withheld on
    purpose. Saying which is the difference between a gap and a decision.

    .. versionadded:: 2.2.0

    .. versionchanged:: 5.0.0
       Public (``_rebuilt_in_scope`` until then): the count's and the
       high's readers take it.
    """
    if not box_source(con).rebuilt:
        return 0
    scope, params = scope_without_guard("l", season, season_type)
    where = f"{scope} AND l.reconstructed"
    if athlete_id is not None:
        where += " AND l.athlete_id = ?"
        params.append(athlete_id)
    row = con.execute(f"SELECT COUNT(*) FROM player_game_log l WHERE {where}", params).fetchone()
    return int(row[0]) if row else 0


def empty_box_scores(con: duckdb.DuckDBPyConnection, season: int | None, season_type: int, athlete_id: str | None, *, covered_by_rebuild: bool = False) -> tuple[int, int | None, int | None]:
    """Games in scope whose box score is empty: (count, first season, last season).

    Every game from 2012-13 through 2017-18 has a box score, but 161-166 a
    season hold nothing - every player's minutes NULL and every stat 0. LeBron
    James's 76 games of 2012-13 are all present and sum to 1,835 points, against
    the season table's 2,036. A zero can hide a real maximum or a real count but
    never invent one, so the answer stands - and says how many games it could
    not see. For a player, only the empty games he actually played in count.

    ``covered_by_rebuild`` excludes the games the answer DID see through
    ``player_box_stats_filled``. Without it the same answer both reads a game
    and reports it as unseen - "his highest was 43, rebuilt from play-by-play"
    beside "68 of his games have an empty box score, so a bigger game may be
    missing", where those 68 are the very games the 43 came from.

    .. versionchanged:: 5.0.0
       Public (``_empty_box_scores`` until then): the count's and the
       high's readers take it.
    """
    if covered_by_rebuild and box_source(con).rebuilt:
        # What is still unseen: no minutes AND no rebuild to stand in for them.
        scope, params = scope_without_guard("l", season, season_type)
        if athlete_id is None:
            sql = (
                f"SELECT COUNT(*), MIN(season), MAX(season) FROM (SELECT l.season FROM player_game_log l WHERE {scope} "
                "GROUP BY l.event_id, l.season HAVING MAX(l.minutes) IS NULL AND NOT BOOL_OR(COALESCE(l.reconstructed, FALSE)))"
            )
        else:
            sql = (
                f"SELECT COUNT(*), MIN(l.season), MAX(l.season) FROM player_game_log l WHERE {scope} "
                "AND l.minutes IS NULL AND NOT COALESCE(l.reconstructed, FALSE) AND NOT COALESCE(l.did_not_play, FALSE) AND l.athlete_id = ?"
            )
            params.append(athlete_id)
        row = con.execute(sql, params).fetchone()
        return (int(row[0]), row[1], row[2]) if row else (0, None, None)

    scope, params = scope_without_guard("b", season, season_type)
    empty = f"SELECT b.event_id, b.season FROM player_box_stats b WHERE {scope} GROUP BY b.event_id, b.season HAVING MAX(b.minutes) IS NULL"
    if athlete_id is None:
        sql = f"SELECT COUNT(*), MIN(season), MAX(season) FROM ({empty})"
    else:
        sql = (
            f"SELECT COUNT(*), MIN(r.season), MAX(r.season) FROM player_box_stats r JOIN ({empty}) e ON e.event_id = r.event_id AND e.season = r.season "
            "WHERE r.athlete_id = ? AND NOT COALESCE(r.did_not_play, FALSE)"
        )
        params.append(athlete_id)
    row = con.execute(sql, params).fetchone()
    return (int(row[0]), row[1], row[2]) if row else (0, None, None)


def leaderboard_shot_distance_refusal() -> Reply:
    """The refusal for a shot-distance ranking, naming the real cause (ISSUES.md
    #114): no leaderboard metric ranks distance, and the nearest real one is
    a percentage.

    .. versionadded:: 5.0.0
    """
    message = "Shot distance is not ranked league-wide yet - ask about one named player's average shot distance instead."
    return Reply(data={"message": message, "headline": message}, answer=message)


def wanted_stats(scope: Scope, default: tuple[str, ...] = STAT_LINE) -> list[str]:
    """The stats to report: the one named, or ``default`` if none was.

    A stat that was NAMED but is not supported must not fall back to the
    default line - that is how "what was Steph Curry's avg 3pt shot distance"
    came back as "26.6 points, 3.6 rebounds and 4.7 assists per game". A
    refusal names the stat; answering a different question is worse."""
    stat = scope.stat
    if stat is None or not stat.strip():
        return list(default)
    if stat in PLAYER_STAT_COLUMNS:
        return [stat]
    raise Unsupported(f"no per-game column for stat {stat!r}")


@dataclass(frozen=True)
class _ShootingStat:
    """One percentage ``player_stat`` answers, computed from makes and
    attempts rather than read from a stored percentage - a season, a career
    and a narrowed set of games are all the same sum.

    ``made``/``attempted`` are the SQL spliced into a SELECT list - a bare
    column for every stat but ``twoPointFieldGoalPct``, whose "column" is an
    expression (neither table stores a 2-point make/attempt count; see the
    class's own entry below). The season and career readers splice them in;
    a narrowed set of games reads the compiler's own rate and its two sums
    instead (``compose.core.SHOT_RATES``, ``compose.stats``).

    ``made_key``/``attempted_key`` are the STABLE names an answer's
    ``data["stats"]`` exposes the makes and attempts under - identical to
    ``made``/``attempted`` for every stat but ``twoPointFieldGoalPct``, whose
    SQL expression is not a valid key at all. Before these existed, the raw
    expression itself became the dict key: the page looked up the stat it
    wanted by its slot name (``twoPointFieldGoalPct``), found nothing, and
    printed the SQL text as a column header with no value beneath it (seen
    live on the rendered page, 2026-09-24). ``made_label``/``attempted_label``/
    ``pct_label`` are the short forms ("2PM", "2PA", "2PT%") a page prints
    beside those keys, carried in ``data["labels"]`` alongside ``data["stats"]``
    rather than left for a renderer to guess from the key's own spelling.

    .. versionadded:: 4.4.0
    """

    made: str
    attempted: str
    how: str
    noun: str
    made_key: str
    attempted_key: str
    made_label: str
    attempted_label: str
    pct_label: str


SHOOTING_STATS: dict[str, _ShootingStat] = {
    "fieldGoalPct": _ShootingStat("fieldGoalsMade", "fieldGoalsAttempted", "from the field", "field goals", "fieldGoalsMade", "fieldGoalsAttempted", "FGM", "FGA", "FG%"),
    "threePointFieldGoalPct": _ShootingStat(
        "threePointFieldGoalsMade", "threePointFieldGoalsAttempted", "on 3-pointers", "3-pointers", "threePointFieldGoalsMade", "threePointFieldGoalsAttempted", "3PM", "3PA", "3PT%"
    ),
    "freeThrowPct": _ShootingStat("freeThrowsMade", "freeThrowsAttempted", "on free throws", "free throws", "freeThrowsMade", "freeThrowsAttempted", "FTM", "FTA", "FT%"),
    "twoPointFieldGoalPct": _ShootingStat(
        "(fieldGoalsMade - threePointFieldGoalsMade)",
        "(fieldGoalsAttempted - threePointFieldGoalsAttempted)",
        "on 2-pointers",
        "2-pointers",
        "twoPointFieldGoalsMade",
        "twoPointFieldGoalsAttempted",
        "2PM",
        "2PA",
        "2PT%",
    ),
}
"""Shooting percentages ``player_stat`` answers, always with the makes and
attempts behind them - computed from those, never read from a stored
percentage, so a season, a career and a set of games are all the same sum.

.. versionadded:: 2.1.0
.. versionchanged:: 4.4.0
   Added ``twoPointFieldGoalPct``, computed from field goals less the
   three-point columns rather than read from a stored column - ESPN's season
   table has no 2-point make/attempt count of its own.
.. versionchanged:: 4.4.0
   Values are :class:`_ShootingStat` rather than a bare 4-tuple, carrying a
   stable ``data["stats"]`` key and a short label for the makes, the attempts
   and the percentage itself, alongside the SQL each was already keeping.
"""


# A made-count stat's attempted sibling column - the same name in the season
# table and in player_game_log, exactly like SHOOTING_STATS' own pairs, but
# for the three stats the router files as a plain COUNT ("3-pointers made")
# rather than a percentage. "Davion Mitchell 3 point stats" answered makes and
# games and nothing else - not the attempts or the percentage they make,
# both `must_include` in the yardstick key (F051, ISSUES.md). `player_stat`
# reads this only for a single named made-count stat, never a multi-stat
# line, where singling out one entry's "out of how many?" would read as
# though only it needed the qualifier.
MADE_STAT_ATTEMPTS: dict[str, str] = {
    "threePointFieldGoalsMade": "threePointFieldGoalsAttempted",
    "fieldGoalsMade": "fieldGoalsAttempted",
    "freeThrowsMade": "freeThrowsAttempted",
}
"""``player_stat`` made-count stat -> its attempted column.

.. versionadded:: 4.4.0
"""


@dataclass(frozen=True)
class _AdvancedStat:
    """One of the stats computed from box scores rather than served by ESPN.

    ``column`` is its name in ``player_season_advanced_stats``; ``label`` is how
    the sentence says it; ``weight`` is the volume column a career is weighted
    by, with ``volume`` naming it in prose, and ``None`` means the stat has no
    career answer.
    """

    column: str
    label: str
    weight: str | None
    percentage: bool
    volume: str = ""


# These were RANKABLE and not LOOKUP-ABLE, which is one concept carrying two
# vocabularies. `leaderboard` has ranked true shooting, effective FG% and usage
# since 2.1.0, and the router emits their names correctly - "kevin durant true
# shooting percentage career" arrives with `stat="ts_pct"` - but `player_stat`
# knew only PLAYER_STAT_COLUMNS and refused it with "no per-game column for
# stat 'ts_pct'". A stat the system can rank is one it should be able to look
# up, and the gap was invisible because each half was correct on its own.
#
# A career is weighted by the stat's OWN denominator, never averaged across
# seasons - the discipline SHOOTING_STATS already states. TS% weighted by true
# shooting attempts is exact, because a season's ts_pct times its attempts IS
# that season's points over two; the same holds for eFG% over field-goal
# attempts. Usage and game score have no such denominator (usage is a rate per
# possession while on court, and weighting it by games is an approximation
# nobody asked for), so they answer for a season and refuse a career rather
# than quietly reporting a mean of means. That is the same line
# `metrics.LeaderboardMetric.career` draws for the ranking.
ADVANCED_STATS: dict[str, _AdvancedStat] = {
    "ts_pct": _AdvancedStat("ts_pct", "true shooting percentage", "true_shooting_attempts", percentage=True, volume="true-shooting attempts"),
    "efg_pct": _AdvancedStat("efg_pct", "effective field goal percentage", "field_goals_attempted", percentage=True, volume="field-goal attempts"),
    "usage_pct": _AdvancedStat("usage_pct", "usage rate", None, percentage=False),
    "game_score": _AdvancedStat("avg_game_score", "game score", None, percentage=False),
}
"""The computed advanced stats ``player_stat`` can look up, mapped to how each
one reads and how it adds up over a career.

.. versionadded:: 4.3.0
"""


_player_stat_reads_box_scores = scope_reads_box_scores
