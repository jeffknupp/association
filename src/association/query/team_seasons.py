"""The team-season relations: a team's season line and the standings
(``team_season_stats``, ``standings`` and a tally of ``real_games`` for a
postseason - ``team_seasons``), and ESPN's power index for it
(``team_power_index`` - ``team_snapshots``, ``ROADMAP-TYPES.md``, "Query"),
beside the team-games relation (:mod:`association.query.team_games`) and the
player's season line (:mod:`association.query.season_line`).

This module holds what the team-season readers
(:mod:`association.query.compose.team_stats`) share and nothing that words
an answer: each statement a read runs, built here and executed through the
compiler's one door (:func:`~association.query.compose.core.values_of`),
never by a reader of its own, and the plain values its rows are read into.
Phase 2's slice (iv) (``ROADMAP.md``): the statements are the retired
templates' (``templates.teams.team_outlook``'s two power-index reads,
``team_metrics.season_table`` and ``record_table``,
``templates.teams._venue_records`` and
``_team_leaderboard_since_records``), moved whole.

.. versionadded:: 5.0.0
"""

from __future__ import annotations

from typing import Any

from association.nba.franchises import season_name_sql
from association.query.season_line import Statement
from association.query.team_games import TEAM_GAMES_SQL
from association.query.team_metrics import POSSESSIONS, TEAM_METRICS, TURNOVERS, TeamLine, TeamRecord, games_scope

# --- the season line and the standings (team_seasons) ----------------------------


def team_lines_statement(season: int, season_type: int) -> Statement:
    """Every team's line for one season, from ``team_season_stats`` with
    opponent points from ``real_games``: ``(team, gamesPlayed, listed_games,
    <each TEAM_METRICS value that is not a record>)``, read by
    :func:`team_lines`.

    A metric needing opponent points is NULL for a team whose games in
    ``real_games`` do not number its ``gamesPlayed`` - the points allowed would
    cover a different set of games than everything they are divided by.

    .. versionadded:: 5.0.0
       ``team_metrics.season_table``'s statement, moved whole.
    """
    scope, params = games_scope(season_type, season)
    selected = ", ".join(f"{metric.expression} AS {key}" for key, metric in _line_metrics().items())
    sql = f"""
{TEAM_GAMES_SQL},
opp AS (
    SELECT team_id, count(*) AS games, sum(opponent_score) AS opp_points FROM team_games WHERE {scope} GROUP BY team_id
),
base AS (
    SELECT {season_name_sql("t.team_id", "ts.season", "t.display_name")} AS team, ts.gamesPlayed, o.games AS listed_games,
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
    return Statement(sql, [*params, season, season_type])


def _line_metrics() -> dict[str, Any]:
    """The metrics a team's line carries: every one that is not a record."""
    return {key: metric for key, metric in TEAM_METRICS.items() if metric.expression is not None}


def team_lines(rows: list[tuple[Any, ...]], season: int) -> list[TeamLine]:
    """:func:`team_lines_statement`'s rows as each team's line. A metric is
    None for every team before its own ``first_season``.

    .. versionadded:: 5.0.0
       ``team_metrics.season_table``'s reading of its rows.
    """
    lines = []
    for row in rows:
        team, games, listed = row[0], row[1], row[2]
        values: dict[str, float | None] = {}
        for index, (key, metric) in enumerate(_line_metrics().items()):
            value = row[3 + index]
            values[key] = None if value is None or season < metric.first_season else float(value)
        lines.append(TeamLine(team=team, games=int(games), listed_games=None if listed is None else int(listed), values=values))
    return lines


def team_records_statement(season: int, season_type: int) -> Statement:
    """Every team's record for one season: ``standings`` for a regular season,
    the authoritative source, and a tally of ``real_games`` for a postseason,
    which standings do not cover - ``(team, wins, losses)``.

    .. versionadded:: 5.0.0
       ``team_metrics.record_table``'s two statements, moved whole.
    """
    if season_type == 2:
        return Statement(
            f"SELECT {season_name_sql('t.team_id', 's.season', 't.display_name')}, s.wins, s.losses "
            "FROM standings s JOIN teams t ON t.team_id = s.team_id WHERE s.season = ? AND s.wins + s.losses > 0 ORDER BY 1",
            [season],
        )
    scope, params = games_scope(season_type, season)
    return Statement(
        f"{TEAM_GAMES_SQL} SELECT {season_name_sql('t.team_id', 'tg.season', 't.display_name')}, sum(won::INT), sum((NOT won)::INT) "
        f"FROM team_games tg JOIN teams t ON t.team_id = tg.team_id WHERE {scope} GROUP BY 1 ORDER BY 1",
        params,
    )


def team_records(rows: list[tuple[Any, ...]]) -> list[TeamRecord]:
    """A record statement's ``(team, wins, losses)`` rows as records.

    .. versionadded:: 5.0.0
    """
    return [TeamRecord(team=name, wins=int(wins), losses=int(losses)) for name, wins, losses in rows]


# --- the power index (team_snapshots) ------------------------------------------

# ESPN's season types, as the power index uses them. 5 is not in the rest of
# the warehouse: its snapshots are dated between the regular season's end and
# the first playoff game (2026-04-18, 2025-04-19), i.e. the play-in.
#: ESPN's season-type code for a preseason power-index snapshot.
#:
#: Named because it is a tiebreak, not a filter: a preseason rating is a real
#: answer where it is the only snapshot holding a team, and merely the worst
#: one to pick among the pre-playoff snapshots when neither is the
#: regular-season one - :data:`BPI_REGULAR` is preferred outright and never
#: reaches this tiebreak.
#:
#: .. versionadded:: 2.2.0
#: .. versionchanged:: 4.0.1
#:    Its docstring now says what actually reaches the tiebreak - a
#:    regular-season question no longer compares dates against preseason at
#:    all, `team_outlook` reads the regular-season snapshot outright.
#: .. versionchanged:: 5.0.0
#:    Moved from ``templates.teams``, with the read it orders.
BPI_PRESEASON = 1


#: ESPN's season-type code for a regular-season power-index snapshot.
#:
#: A regular-season question (``season_type`` unset or ``2``) reads this
#: snapshot outright when it holds the team asked about, rather than
#: whichever pre-playoff snapshot ESPN stamped last - see
#: :func:`team_outlook_chosen`.
#:
#: .. versionadded:: 4.0.1
#: .. versionchanged:: 5.0.0
#:    Moved from ``templates.teams``.
BPI_REGULAR = 2


BPI_SNAPSHOT_NAMES = {1: "preseason", 2: "regular-season", 3: "postseason", 5: "play-in"}
"""What each of the power index's season-type codes is called in an answer.

.. versionadded:: 5.0.0
   Moved from ``templates.teams``.
"""


BPI_CHANCES = (("playoffs", "probmakeplayoffs"), ("conference finals", "probmakeconfchamp"), ("Finals", "probmaketitlegame"), ("title", "probwintitle"))
"""``(label, column)`` for the chances a snapshot carries, in the order an
answer names them. ``probmakeconfchamp`` is reaching the conference finals,
not winning them: in the 2026 postseason snapshot every conference
finalist reads 100 and every other team 0.

.. versionadded:: 5.0.0
   ``templates.teams._BPI_CHANCES``.
"""


def team_outlook_snapshots_statement(team_id: str, season: int) -> Statement:
    """Each power-index snapshot a season holds - one per season type - with
    its latest stamp, how many teams it holds and whether it holds
    ``team_id``: ``(season_type, last_updated, teams, holds)``.

    Ordered by date, then with a PRESEASON snapshot pushed behind any other of
    the same date, because :func:`team_outlook_chosen` takes the last row.
    Measured on the backfilled table, nothing needs this yet: 2018's two
    snapshots are stamped one minute apart (preseason 07:47Z, regular season
    07:48Z on 2020-10-12, the day ESPN backfilled both), so the regular
    season already sorts last. That one minute is the whole margin, and it is
    ESPN's to change - the tiebreak makes the choice explicit rather than
    resting on it.

    .. versionadded:: 5.0.0
    """
    return Statement(
        "SELECT season_type, max(last_updated), count(DISTINCT team_id), bool_or(team_id = ?) FROM team_power_index WHERE season = ? "
        f"GROUP BY 1 ORDER BY 2, CASE season_type WHEN {BPI_PRESEASON} THEN 0 ELSE 1 END",
        [team_id, season],
    )


def team_outlook_chosen(snapshots: list[tuple[Any, ...]], postseason: bool) -> tuple[Any, ...] | None:
    """The one row of ``snapshots`` (:func:`team_outlook_snapshots_statement`'s,
    one per season type) that answers this question, or None where nothing
    does.

    A postseason question reads the postseason snapshot. A regular-season
    question reads the regular-season snapshot outright whenever it holds the
    team, never "whichever pre-playoff snapshot is latest" - that used to be
    the play-in one (season type 5) in 2023, 2025 and 2026, because the paging
    fix gave it all 30 teams and it is stamped after the regular-season
    snapshot in those years. See ``DATA.md``, "ESPN's power index is a paged
    collection, and holds all 30 teams", and ``ISSUES.md`` #88. Falling back
    to the latest OTHER pre-playoff snapshot (preseason or play-in), and then
    to the postseason one, only happens when no regular-season snapshot holds
    this team - measured across every season ``team_power_index`` holds
    (2017-2026), that never happens today, but the fallback exists so a gap
    answers from the next-best snapshot instead of refusing outright.

    .. versionadded:: 4.0.1

    .. versionchanged:: 5.0.0
       ``templates.teams._team_outlook_choose``, moved to the relation.
    """
    regular = next((s for s in snapshots if s[0] == BPI_REGULAR and s[3]), None)
    pre = [s for s in snapshots if s[0] not in (BPI_REGULAR, 3) and s[3]]
    post = [s for s in snapshots if s[0] == 3 and s[3]]
    if postseason:
        candidates = post
    elif regular is not None:
        candidates = [regular]
    else:
        candidates = pre or post
    return candidates[-1] if candidates else None


def team_outlook_row_statement(season: int, kind: int, team_id: str) -> Statement:
    """One team's row of one snapshot: ``(last_updated, bpi, bpioffense,
    bpidefense, numwins, numlosses, projectedw, projectedl, probmakeplayoffs,
    probmakeconfchamp, probmaketitlegame, probwintitle, sosoverall,
    sosoverallrank, higher)``, where ``higher`` counts the teams in the
    snapshot with a higher rating - where the team stands is counted among
    the teams in the snapshot, since ESPN's own rank columns are ranks only
    from 2022 (before it they hold values like 83, 2,625 and 26,058).

    .. versionadded:: 5.0.0
    """
    return Statement(
        "SELECT last_updated, bpi, bpioffense, bpidefense, numwins, numlosses, projectedw, projectedl, probmakeplayoffs, probmakeconfchamp, probmaketitlegame, probwintitle, "
        "sosoverall, sosoverallrank, (SELECT count(*) FROM team_power_index o WHERE o.season = p.season AND o.season_type = p.season_type AND o.bpi > p.bpi) "
        "FROM team_power_index p WHERE season = ? AND season_type = ? AND team_id = ? ORDER BY last_updated DESC LIMIT 1",
        [season, kind, team_id],
    )


def snapshot_facts(snapshots: list[tuple[Any, ...]]) -> list[dict[str, Any]]:
    """Each snapshot as the plain values an answer describes it by - its
    kind's name, its date and how many teams it holds - and whether it holds
    the team asked about (``holds``).

    .. versionadded:: 5.0.0
       ``templates.teams._team_outlook_snapshot_facts`` (without ``holds``).
    """
    return [{"kind": BPI_SNAPSHOT_NAMES.get(k, f"type-{k}"), "date": str(u)[:10], "teams": int(n), "holds": bool(has)} for k, u, n, has in snapshots]
