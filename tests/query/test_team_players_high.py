"""ISSUES.md #167, the answer side: a single-game high over a team's players
says the team it narrowed to, and over a career reads every season. "most
points in a game in cavs history" answered the 2026 high of the Cavaliers'
players with nothing saying a team narrowed it ("Donovan Mitchell had the
most points in a single game in the 2026 regular season: 48 ..."). The
reading's half - "history" read as a career - is the span tagger's.

The whole agent over a small warehouse, the reading handed in typed
(:mod:`routed`): the Cavaliers' players' games in two seasons, and a
higher game by another team's player, which a team's high must not read.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest
from routed import Routed, ask_routed

from association.fetch.repairs import real_games
from association.nba.season import current_season
from association.query.agent import Agent
from association.query.reading import GAME_HIGHS, Measure, PointShape, Reading, Span
from association.query.reading import Subject as Who

S = current_season()


@pytest.fixture
def agent(tmp_path: Path) -> Agent:
    """Mitchell's 48 this season and Irving's 57 ten seasons back for the
    Cavaliers; Embiid's 70 for the 76ers this season, the league's high."""
    con = duckdb.connect(str(tmp_path / "test.duckdb"))
    con.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    con.execute("INSERT INTO players VALUES ('1','Donovan Mitchell'),('2','Kyrie Irving'),('3','Joel Embiid')")
    con.execute("CREATE TABLE teams (team_id VARCHAR, abbreviation VARCHAR, display_name VARCHAR)")
    con.execute("INSERT INTO teams VALUES ('5','CLE','Cleveland Cavaliers'),('20','PHI','Philadelphia 76ers'),('27','WSH','Washington Wizards')")
    con.execute(
        "CREATE TABLE games (event_id VARCHAR, season INTEGER, season_type INTEGER, date VARCHAR, home_team_id VARCHAR, away_team_id VARCHAR, "
        "home_score INTEGER, away_score INTEGER, winner_team_id VARCHAR, neutral_site BOOLEAN, venue_city VARCHAR)"
    )
    con.executemany(
        "INSERT INTO games VALUES (?, ?, 2, ?, ?, ?, ?, ?, ?, false, 'X')",
        [
            ("e1", S, f"{S - 1}-12-12T00:30Z", "5", "27", 130, 120, "5"),
            ("e2", S - 10, f"{S - 11}-03-12T00:30Z", "5", "27", 128, 110, "5"),
            ("e3", S, f"{S - 1}-01-22T00:30Z", "20", "27", 133, 123, "20"),
        ],
    )
    real_games.build_table(con, {"games", "teams"})
    con.execute(
        "CREATE TABLE player_game_log (athlete_id VARCHAR, season INTEGER, season_type INTEGER, player_name VARCHAR, game_date VARCHAR, team_id VARCHAR, "
        "opponent_abbr VARCHAR, points INTEGER, minutes INTEGER, rebounds INTEGER, assists INTEGER, event_id VARCHAR, did_not_play BOOLEAN DEFAULT FALSE)"
    )
    con.executemany(
        "INSERT INTO player_game_log VALUES (?, ?, 2, ?, ?, ?, 'WSH', ?, 40, 5, 5, ?, FALSE)",
        [
            ("1", S, "Donovan Mitchell", f"{S - 1}-12-12T00:30Z", "5", 48, "e1"),
            ("2", S - 10, "Kyrie Irving", f"{S - 11}-03-12T00:30Z", "5", 57, "e2"),
            ("3", S, "Joel Embiid", f"{S - 1}-01-22T00:30Z", "20", 70, "e3"),
        ],
    )
    con.execute("CREATE TABLE player_box_stats (event_id VARCHAR, season INTEGER, season_type INTEGER, athlete_id VARCHAR, minutes INTEGER, did_not_play BOOLEAN)")
    con.close()
    return Agent(str(tmp_path / "test.duckdb"), tmp_path / "out", history_dir=tmp_path / ".history")


def _high(agent: Agent, *, career: bool) -> str:
    """The Cavaliers' players' single-game high in points, this season or over every season."""
    reading = Reading(who=Who(kind="team_players", teams=("Cleveland Cavaliers",)), measure=Measure(key="points", as_typed="points"), span=Span(season_type=2, career=career))
    answer = ask_routed(agent, "most points in a game in cavs history", Routed(GAME_HIGHS, reading))
    assert answer.answered_by == "fast" and answer.shape == PointShape("player_games", "rows", "measure"), answer
    return answer.text


def test_a_teams_players_high_names_the_team(agent: Agent) -> None:
    """The team the high narrowed to is said, and another team's higher game is not read."""
    text = _high(agent, career=False)
    assert text.startswith(f"Donovan Mitchell had the most points in a single game for the Cleveland Cavaliers in the {S} regular season: 48, on "), text
    assert "Embiid" not in text, text


def test_a_teams_players_high_over_a_career_reads_every_season(agent: Agent) -> None:
    """Over a career the franchise's history is read: Irving's 57 of ten seasons back leads."""
    text = _high(agent, career=True)
    assert text.startswith("Kyrie Irving had the most points in a single game for the Cleveland Cavaliers in the regular season since 1993-94: 57, on "), text
    assert "Next: Donovan Mitchell (48)." in text and "Embiid" not in text, text
