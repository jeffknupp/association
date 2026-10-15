"""A player's line honors a teammate's role: "maxey points when embiid
starts" reads the games Embiid started from the box scores, never the whole
season's line with the teammate dropped. A companion is one of the
narrowings that send a player's line to the box scores rather than the
season line, even with nothing else narrowing it.

In the real warehouse Tyrese Maxey's
2025 regular-season line is 26.3 points in 52 games
(``player_season_stats_deduped``); narrowed to the 16 games Joel Embiid
started that season, his average is 23.8. The fixture below is a small,
hand-built analog of that shape - four games, a companion who starts two,
comes off the bench in one and is absent from the box score entirely in the
fourth - so every count asserted here can be read straight off it rather than
guessed, the same discipline ``test_conditions.py``'s own fixture docstring
states.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb
import pytest
from routed import Routed, ask_routed
from test_readers import player_line

from association.fetch.repairs import real_games
from association.nba.season import current_season
from association.query.answer import AnswerContext
from association.query.point import default_point
from association.query.reading import PLAYER_LINE, Companion, Line, Measure, Predicate, Reading
from association.query.reading import Subject as Who

SEASON = current_season()

_BOX_COLUMNS = (
    "event_id VARCHAR, season INTEGER, season_type INTEGER, team_id VARCHAR, opponent_team_id VARCHAR, athlete_id VARCHAR, did_not_play BOOLEAN, "
    "minutes INTEGER, points INTEGER, rebounds INTEGER, assists INTEGER, steals INTEGER, blocks INTEGER, turnovers INTEGER, fouls INTEGER, plusMinus INTEGER, "
    "fieldGoalsMade INTEGER, fieldGoalsAttempted INTEGER, threePointFieldGoalsMade INTEGER, threePointFieldGoalsAttempted INTEGER, "
    "freeThrowsMade INTEGER, freeThrowsAttempted INTEGER, offensiveRebounds INTEGER, defensiveRebounds INTEGER, starter BOOLEAN"
)


def _box(event: str, athlete: str, opponent: str, points: int, *, starter: bool, minutes: int = 30) -> tuple[Any, ...]:
    """One ``player_box_stats`` row, both named players always on team ``1``."""
    return (event, SEASON, 2, "1", opponent, athlete, False, minutes, points, 5, 3, 1, 0, 2, 3, 0, points // 2, points, 0, 0, 0, 0, 0, 0, starter)


@pytest.fixture
def pstat_conditions_ctx(tmp_path: Path) -> AnswerContext:
    """Tyrese Maxey's (athlete ``1``) 76ers season in miniature, with Joel
    Embiid (athlete ``2``) as his teammate:

    ====  ==============================  =================
    game  Embiid's role                   Maxey's points
    ====  ==============================  =================
    e1    starts                          24
    e2    starts                          22
    e3    absent (no box row at all)      30
    e4    off the bench                   20
    ====  ==============================  =================

    Season total: 96 in 4 games, average 24.0 - ``player_season_stats_deduped``
    carries that line, so a read that keeps reading it despite a companion
    is caught rather than coincidentally reproduced. "Started" (e1, e2)
    averages 23.0 over 2 games; "off the bench" (e4) is 20.0 over 1; "reached
    20+ points" (e1: 30, e2: 28, e4: 25 - not e3, where Embiid has no row to
    read a threshold off) averages 22.0 over 3 - three counts, each different
    from the season line and from each other.
    """
    s = SEASON
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    c.execute("INSERT INTO players VALUES ('1','Tyrese Maxey'),('2','Joel Embiid')")
    c.execute("CREATE TABLE teams (team_id VARCHAR, abbreviation VARCHAR, display_name VARCHAR)")
    c.execute("INSERT INTO teams VALUES ('1','PHI','Philadelphia 76ers'),('2','BOS','Boston Celtics'),('3','MIA','Miami Heat')")
    c.execute(
        "CREATE TABLE games (event_id VARCHAR, season INTEGER, season_type INTEGER, date VARCHAR, home_team_id VARCHAR, away_team_id VARCHAR, "
        "home_score INTEGER, away_score INTEGER, winner_team_id VARCHAR)"
    )
    c.executemany(
        "INSERT INTO games VALUES (?, ?, 2, ?, ?, ?, ?, ?, ?)",
        [
            ("e1", s, f"{s - 1}-11-01T23:00Z", "1", "2", 110, 100, "1"),
            ("e2", s, f"{s - 1}-11-05T23:00Z", "2", "1", 108, 99, "1"),
            ("e3", s, f"{s - 1}-11-08T23:00Z", "1", "3", 105, 110, "3"),
            ("e4", s, f"{s - 1}-11-12T23:00Z", "3", "1", 100, 95, "1"),
        ],
    )
    c.execute(f"CREATE TABLE player_box_stats ({_BOX_COLUMNS})")
    c.executemany(
        f"INSERT INTO player_box_stats VALUES ({', '.join('?' for _ in range(25))})",
        [
            _box("e1", "1", "2", 24, starter=True),
            _box("e1", "2", "2", 30, starter=True),
            _box("e2", "1", "1", 22, starter=True),
            _box("e2", "2", "1", 28, starter=True),
            _box("e3", "1", "3", 30, starter=True),
            # Embiid has no row at all for e3 - absent from the box score entirely.
            _box("e4", "1", "3", 20, starter=True),
            _box("e4", "2", "3", 25, starter=False),  # off the bench
        ],
    )
    c.execute(
        "CREATE VIEW player_game_log AS SELECT pbs.*, p.display_name AS player_name, g.date AS game_date, t.abbreviation AS team_abbr, o.abbreviation AS opponent_abbr "
        "FROM player_box_stats pbs LEFT JOIN players p ON p.athlete_id = pbs.athlete_id LEFT JOIN games g ON g.event_id = pbs.event_id AND g.season = pbs.season "
        "LEFT JOIN teams t ON t.team_id = pbs.team_id LEFT JOIN teams o ON o.team_id = pbs.opponent_team_id"
    )
    c.execute(
        "CREATE TABLE player_season_stats_deduped (athlete_id VARCHAR, season INTEGER, season_type INTEGER, gamesPlayed INTEGER, avgPoints DOUBLE, points INTEGER, "
        "avgRebounds DOUBLE, totalRebounds INTEGER, avgAssists DOUBLE, assists INTEGER, avgMinutes DOUBLE, threePointFieldGoalsMade INTEGER, threePointFieldGoalsAttempted INTEGER)"
    )
    c.execute("INSERT INTO player_season_stats_deduped VALUES ('1', ?, 2, 4, 24.0, 96, 3.0, 12, 3.0, 12, 30.0, 0, 0)", [s])
    real_games.build_table(c, {"games", "teams"})
    return AnswerContext(con=c, out_dir=tmp_path / "out")


def _embiid(predicate: Predicate, line: Line | None = None) -> Companion:
    """Joel Embiid beside the subject, on his own side, in ``predicate``'s role."""
    return Companion(player="Joel Embiid", predicate=predicate, line=line)


def _maxey_points(*companions: Companion) -> Reading:
    """Tyrese Maxey's points, with ``companions`` beside him."""
    return Reading(who=Who(kind="player", players=("Tyrese Maxey",)), companions=companions, measure=Measure(key="points", as_typed="points"))


def test_a_players_line_with_only_a_companion_reads_box_scores_not_the_season_line(pstat_conditions_ctx: AnswerContext) -> None:
    """A companion with nothing else narrowing reads Maxey's games beside
    Embiid from the box scores, never the season line (24.0 in 4 games).
    Also pins the unnarrowed season line, so a change cannot make this pass
    by coincidence (both branches reading the same number)."""
    season = player_line(pstat_conditions_ctx, Reading(who=Who(kind="player", players=("Tyrese Maxey",)), measure=Measure(key="points", as_typed="points")))
    assert season.text == f"Tyrese Maxey averaged 24 points per game in 4 games in the {SEASON} regular season. That is 96 in total."

    started = player_line(pstat_conditions_ctx, _maxey_points(_embiid("started")))
    assert started.data["stats"] == {"gamesPlayed": 2, "avgPoints": 23.0, "points": 46}
    assert started.text == f"Tyrese Maxey averaged 23 points per game in 2 games with Joel Embiid starting in the {SEASON} regular season. That is 46 in total."


def test_a_players_line_honors_a_bench_companion(pstat_conditions_ctx: AnswerContext) -> None:
    result = player_line(pstat_conditions_ctx, _maxey_points(_embiid("bench")))
    assert result.data["stats"] == {"gamesPlayed": 1, "avgPoints": 20.0, "points": 20}
    assert result.text == f"Tyrese Maxey averaged 20 points per game in 1 game with Joel Embiid off the bench in the {SEASON} regular season. That is 20 in total."


def test_a_players_line_honors_a_reached_companion(pstat_conditions_ctx: AnswerContext) -> None:
    """Embiid reached 20+ points in e1 (30), e2 (28) and e4 (25) - not e3,
    where he has no box row to read a threshold off at all, so the EXISTS
    clause a "reached" condition compiles to correctly excludes a game he
    did not play rather than crediting it to a threshold of zero."""
    result = player_line(pstat_conditions_ctx, _maxey_points(_embiid("reached", Line(measure="points", value=20))))
    assert result.data["stats"] == {"gamesPlayed": 3, "avgPoints": 22.0, "points": 66}
    assert result.text == f"Tyrese Maxey averaged 22 points per game in 3 games in games Joel Embiid had 20+ points in the {SEASON} regular season. That is 66 in total."


def test_the_default_point_also_reads_box_scores_for_a_condition() -> None:
    """A player's line's default point (``point._default_player_stat``)
    takes the same test (``reading.scope_reads_box_scores``) to decide its
    relation. Unnarrowed, the point is the season line
    (``player_seasons``); a companion alone switches it to box scores
    (``player_games``)."""
    season = default_point(PLAYER_LINE, _maxey_points())
    assert season.read_as.relation == "player_seasons"
    narrowed = default_point(PLAYER_LINE, _maxey_points(_embiid("started")))
    assert narrowed.read_as.relation == "player_games"


def _agent_warehouse(tmp_path: Path) -> Path:
    """A file-backed copy of ``pstat_conditions_ctx``'s tables, for the agent
    fixture below - ``Agent`` opens its warehouse by path, not by connection."""
    db_path = tmp_path / "test.duckdb"
    c = duckdb.connect(str(db_path))
    c.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    c.execute("INSERT INTO players VALUES ('1','Tyrese Maxey'),('2','Joel Embiid')")
    c.execute("CREATE TABLE teams (team_id VARCHAR, abbreviation VARCHAR, display_name VARCHAR)")
    c.execute("INSERT INTO teams VALUES ('1','PHI','Philadelphia 76ers'),('2','BOS','Boston Celtics'),('3','MIA','Miami Heat')")
    c.execute(
        "CREATE TABLE games (event_id VARCHAR, season INTEGER, season_type INTEGER, date VARCHAR, home_team_id VARCHAR, away_team_id VARCHAR, "
        "home_score INTEGER, away_score INTEGER, winner_team_id VARCHAR)"
    )
    s = SEASON
    c.executemany(
        "INSERT INTO games VALUES (?, ?, 2, ?, ?, ?, ?, ?, ?)",
        [
            ("e1", s, f"{s - 1}-11-01T23:00Z", "1", "2", 110, 100, "1"),
            ("e2", s, f"{s - 1}-11-05T23:00Z", "2", "1", 108, 99, "1"),
            ("e3", s, f"{s - 1}-11-08T23:00Z", "1", "3", 105, 110, "3"),
            ("e4", s, f"{s - 1}-11-12T23:00Z", "3", "1", 100, 95, "1"),
        ],
    )
    c.execute(f"CREATE TABLE player_box_stats ({_BOX_COLUMNS})")
    c.executemany(
        f"INSERT INTO player_box_stats VALUES ({', '.join('?' for _ in range(25))})",
        [
            _box("e1", "1", "2", 24, starter=True),
            _box("e1", "2", "2", 30, starter=True),
            _box("e2", "1", "1", 22, starter=True),
            _box("e2", "2", "1", 28, starter=True),
            _box("e3", "1", "3", 30, starter=True),
            _box("e4", "1", "3", 20, starter=True),
            _box("e4", "2", "3", 25, starter=False),
        ],
    )
    c.execute(
        "CREATE VIEW player_game_log AS SELECT pbs.*, p.display_name AS player_name, g.date AS game_date, t.abbreviation AS team_abbr, o.abbreviation AS opponent_abbr "
        "FROM player_box_stats pbs LEFT JOIN players p ON p.athlete_id = pbs.athlete_id LEFT JOIN games g ON g.event_id = pbs.event_id AND g.season = pbs.season "
        "LEFT JOIN teams t ON t.team_id = pbs.team_id LEFT JOIN teams o ON o.team_id = pbs.opponent_team_id"
    )
    c.execute(
        "CREATE TABLE player_season_stats_deduped (athlete_id VARCHAR, season INTEGER, season_type INTEGER, gamesPlayed INTEGER, avgPoints DOUBLE, points INTEGER, "
        "avgRebounds DOUBLE, totalRebounds INTEGER, avgAssists DOUBLE, assists INTEGER, avgMinutes DOUBLE, threePointFieldGoalsMade INTEGER, threePointFieldGoalsAttempted INTEGER)"
    )
    c.execute("INSERT INTO player_season_stats_deduped VALUES ('1', ?, 2, 4, 24.0, 96, 3.0, 12, 3.0, 12, 30.0, 0, 0)", [s])
    real_games.build_table(c, {"games", "teams"})
    c.close()
    return db_path


def test_agent_with_a_recorded_route_also_honors_the_condition(tmp_path: Path) -> None:
    """End to end through the real ``Agent``, not a direct reader call: the
    agent path adds the coverage caveat and the name readings on top of the
    reader's own answer, so a reader called directly can look like it works
    while the fuller path drops the companion somewhere on the way in. The
    route is given as recorded (``tests/routed.py``'s ``ask_routed``), so
    no model is asked."""
    from association.query.agent import Agent

    db_path = _agent_warehouse(tmp_path)
    agent = Agent(str(db_path), tmp_path / "out", history_dir=tmp_path / ".history")
    route = Routed(PLAYER_LINE, _maxey_points(_embiid("started")))
    answer = ask_routed(agent, "Tyrese Maxey points when embiid starts", route)
    data = answer.data
    assert data is not None
    assert data["stats"] == {"gamesPlayed": 2, "avgPoints": 23.0, "points": 46}
    assert answer.text.startswith(f"Tyrese Maxey averaged 23 points per game in 2 games with Joel Embiid starting in the {SEASON} regular season.")
