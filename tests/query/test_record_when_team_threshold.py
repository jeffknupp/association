"""Regression test for ISSUES.md #144 / the follow-up filed alongside it:
`record_when`'s team branch used to be preempted by the team compiler
(``compose.team``), which reads a plain season or window total and has no
notion of a threshold - so "what was the celtics record when they scored 120
points" silently answered the season's whole point total, the threshold
dropped, instead of a win-loss record split by the line asked about.

Kept apart from ``tests/query/test_conditions.py`` and
``tests/query/test_compose.py``: this is the one place the FULL agent path is
exercised end to end, the way #144's own question is actually asked - the
team subject's own readers declining a threshold, and the compiler answering
it through ``record_when``'s own team reader
(``compose.present.present_team``; ROADMAP plan item 6, step (d), part 4).
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest
from routed import ask_routed, slots_route

from association.fetch.repairs import real_games
from association.nba.season import current_season
from association.query.agent import Agent

S = current_season()


def _agent_with_a_team_threshold_split(tmp_path: Path) -> Agent:
    """A small warehouse: the Celtics have two 120+ point games (one win, one
    loss) and two games under 120 (one win, one loss), all against the same
    opponent - real enough for `record_when`'s team branch
    (`splits._record_when_team_answer`) to answer a threshold record for
    real, rather than merely proving the compiler declines.

    ``team_season_stats`` also carries a season total - a decoy, deliberately
    unlike anything a threshold record could produce (9,999 points, 10
    games), so the pre-fix behavior (the team compiler answering a plain
    season total with the threshold dropped, #144's own bug) reads as a
    wrong but fluent answer here too, not merely an exception from a table
    this fixture never built."""
    db_path = tmp_path / "test.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    con.execute("CREATE TABLE teams (team_id VARCHAR, abbreviation VARCHAR, display_name VARCHAR)")
    con.execute("INSERT INTO teams VALUES ('2', 'BOS', 'Boston Celtics'), ('20', 'PHI', 'Philadelphia 76ers')")
    con.execute(
        "CREATE TABLE games (event_id VARCHAR, season INTEGER, season_type INTEGER, date VARCHAR, home_team_id VARCHAR, away_team_id VARCHAR, "
        "home_score INTEGER, away_score INTEGER, winner_team_id VARCHAR, neutral_site BOOLEAN, venue_city VARCHAR)"
    )
    con.executemany(
        "INSERT INTO games VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            # g1: Celtics home, 125-110 - reaches 120+, a win.
            ("g1", S, 2, f"{S - 1}-11-01T23:00Z", "2", "20", 125, 110, "2", False, "Boston"),
            # g2: Celtics away, 130-135 - reaches 120+, a loss.
            ("g2", S, 2, f"{S - 1}-11-05T23:00Z", "20", "2", 135, 130, "20", False, "Philadelphia"),
            # g3: Celtics home, 110-100 - falls short, a win.
            ("g3", S, 2, f"{S - 1}-11-10T23:00Z", "2", "20", 110, 100, "2", False, "Boston"),
            # g4: Celtics away, 95-105 - falls short, a loss.
            ("g4", S, 2, f"{S - 1}-11-15T23:00Z", "20", "2", 105, 95, "20", False, "Philadelphia"),
        ],
    )
    real_games.build_table(con, {"games", "teams"})
    con.execute("CREATE TABLE team_season_stats (season INTEGER, season_type INTEGER, team_id VARCHAR, gamesPlayed INTEGER, points INTEGER)")
    con.execute("INSERT INTO team_season_stats VALUES (?, ?, ?, ?, ?)", [S, 2, "2", 10, 9999])
    con.close()
    return Agent(str(db_path), tmp_path / "out", history_dir=tmp_path / ".history")


def test_a_team_only_threshold_record_answers_the_record_not_the_season_total(tmp_path: Path) -> None:
    """ISSUES.md #144's own wording. "what was the celtics record when they
    scored 120 points" routes `record_when {'stat': 'points', 'team': 'Boston
    Celtics', 'season_type': 2, 'threshold': 120}` - and because the
    compiler answered `record_when` first, the team compiler used to answer
    it with no notion of a threshold, silently reading the plain season/window
    total and dropping the threshold. `run_team` declines a
    point whose scope carries a ``threshold``, and the compiler answers it
    with `record_when`'s own team reader, as the record it actually asked
    for: g1 (125, W) and g2 (130, L) reach 120+ (1-1, margin +15 and -5); g3
    (110, W) and g4 (95, L) fall short (1-1, margin +10 and -10). No model
    call is needed - the fast path answers on its own."""
    agent = _agent_with_a_team_threshold_split(tmp_path)
    lines: list[str] = []
    agent.trace, agent.verbose = lines.append, True
    answer = ask_routed(agent, "what was the celtics record when they scored 120 points", slots_route("record_when", {"stat": "points", "team": "Boston Celtics", "season_type": 2, "threshold": 120}))
    assert answer.answered_by == "fast"
    assert answer.intent == "record_when"
    assert answer.text.startswith("Boston Celtics record when they had 120+ points")
    assert "120+ points" in answer.text and "under 120 points" in answer.text
    assert answer.data is not None
    assert answer.data["reached"] == {"games": 2, "wins": 1, "losses": 1, "avg_margin": pytest.approx(5.0)}
    assert answer.data["fell_short"] == {"games": 2, "wins": 1, "losses": 1, "avg_margin": pytest.approx(0.0)}
    assert any("-> (compose) intent='record_when'" in line for line in lines), lines
