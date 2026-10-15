"""A team's record over a line (#144): "what was the celtics record when they
scored 120 points" is a win-loss record split by the line asked about, never
the season's whole point total with the line dropped - the team compiler
(``compose.team``), which reads a plain season or window total, declines a
point carrying a line.

Kept apart from ``tests/query/test_conditions.py`` and
``tests/query/test_compose.py``: this is the one place the FULL agent path is
exercised end to end, the way #144's own question is asked - the team
subject's own readers declining a line, and the compiler answering it
through the team's record reader (``compose.records.read_team_record_when``).
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest
from routed import Routed, ask_routed

from association.fetch.repairs import real_games
from association.nba.season import current_season
from association.query.agent import Agent
from association.query.reading import LINE_RECORD, Line, LineOp, Measure, PointShape, Reading, Span
from association.query.reading import Subject as Who

S = current_season()


def _agent_with_a_team_threshold_split(tmp_path: Path) -> Agent:
    """A small warehouse: the Celtics have two 120+ point games (one win, one
    loss) and two games under 120 (one win, one loss), all against the same
    opponent - real enough for the team's record reader
    (``compose.records.read_team_record_when``) to answer a record over a
    line for real, rather than merely proving the compiler declines.

    ``team_season_stats`` also carries a season total - a decoy, deliberately
    unlike anything a record over a line could produce (9,999 points, 10
    games), so the team compiler answering a plain season total with the
    line dropped reads as a
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
    """#144's own wording, "what was the celtics record when they scored 120
    points", read as a record over a line (points, 120, the Boston Celtics).
    ``run_team`` declines a point carrying a line, and the compiler answers
    it with the team's record reader, as the record asked
    for: g1 (125, W) and g2 (130, L) reach 120+ (1-1, margin +15 and -5); g3
    (110, W) and g4 (95, L) fall short (1-1, margin +10 and -10). No model
    call is needed - the fast path answers on its own."""
    agent = _agent_with_a_team_threshold_split(tmp_path)
    lines: list[str] = []
    agent.trace, agent.verbose = lines.append, True
    answer = ask_routed(
        agent,
        "what was the celtics record when they scored 120 points",
        Routed(
            LINE_RECORD,
            Reading(
                who=Who(kind="team", teams=("Boston Celtics",)),
                lines=(Line(measure="points", value=120, as_typed="120 points", keyed=True),),
                measure=Measure(key="points", as_typed="points"),
                span=Span(season_type=2),
            ),
        ),
    )
    assert answer.answered_by == "fast"
    assert answer.shape == PointShape("team_games", "split", "line")
    assert answer.text.startswith("Boston Celtics record when they had 120+ points")
    assert "120+ points" in answer.text and "under 120 points" in answer.text
    assert answer.data is not None
    assert answer.data["reached"] == {"games": 2, "wins": 1, "losses": 1, "avg_margin": pytest.approx(5.0)}
    assert answer.data["fell_short"] == {"games": 2, "wins": 1, "losses": 1, "avg_margin": pytest.approx(0.0)}
    assert any("-> (compose) asked=player_games/split/line shape=team_games/split/line" in line for line in lines), lines


@pytest.mark.parametrize(
    ("op", "held", "missed", "reached", "short"),
    [
        # "125 or fewer points": g1 (125, W), g3 (110, W), g4 (95, L) - 2-1; g2 (130, L) is more.
        ("<=", "125 or fewer points", "more than 125 points", (3, 2), (1, 0)),
        # "under 125 points": g3, g4 - 1-1; g1 (125) and g2 reach 125.
        ("<", "under 125 points", "125+ points", (2, 1), (2, 1)),
        # "more than 125 points": g2 alone - 0-1.
        (">", "more than 125 points", "125 or fewer points", (1, 0), (3, 2)),
    ],
)
def test_a_teams_line_under_a_number_splits_the_games_by_its_own_comparison(tmp_path: Path, op: LineOp, held: str, missed: str, reached: tuple[int, int], short: tuple[int, int]) -> None:
    """#325 ("warriors' record last season when committing 10 or fewer
    turnovers"): the team's record over its own line reads the line's
    comparison, so a line under a number ("N or fewer", "under N") or over
    it ("more than N") splits the games by it and says it, never as an
    "N+" split."""
    agent = _agent_with_a_team_threshold_split(tmp_path)
    answer = ask_routed(
        agent,
        f"what was the celtics record when they had {held}",
        Routed(
            LINE_RECORD,
            Reading(
                who=Who(kind="team", teams=("Boston Celtics",)),
                lines=(Line(measure="points", op=op, value=125, as_typed=held, keyed=True),),
                measure=Measure(key="points", as_typed="points"),
                span=Span(season_type=2),
            ),
        ),
    )
    assert answer.shape == PointShape("team_games", "split", "line")
    assert answer.text.startswith(f"Boston Celtics record when they had {held}, "), answer.text
    rows = answer.text.split("\n")
    assert rows[2].startswith(held) and rows[3].startswith(missed), rows
    assert answer.data is not None
    assert (answer.data["reached"]["games"], answer.data["reached"]["wins"]) == reached
    assert (answer.data["fell_short"]["games"], answer.data["fell_short"]["wins"]) == short
    assert answer.data["op"] == op


def test_a_teams_record_over_its_opponents_line_splits_the_games_by_their_score(tmp_path: Path) -> None:
    """#426: "celtics record when allowing 100+ points" is a line on the
    opponents' figure (``Line.whose``), never on the Celtics' own points,
    and splits the
    games by the opponents' score: at 110+, g1 (allowed 110, W) and g2
    (135, L) reach it (1-1, margins +15 and -5), g3 (100, W) and g4 (105,
    L) do not (1-1, +10 and -10) - where the Celtics' own score put g1 and
    g3 (125, 110) on one side and g4 (95) on the other."""
    agent = _agent_with_a_team_threshold_split(tmp_path)
    answer = ask_routed(
        agent,
        "celtics record when allowing 110+ points",
        Routed(
            LINE_RECORD,
            Reading(
                who=Who(kind="team", teams=("Boston Celtics",)),
                lines=(Line(measure="points", value=110, as_typed="110+ points", keyed=True, whose="opponent"),),
                measure=Measure(key="points", as_typed="points"),
                span=Span(season_type=2),
            ),
        ),
    )
    assert answer.shape == PointShape("team_games", "split", "line")
    assert answer.text.startswith("Boston Celtics record when their opponents had 110+ points"), answer.text
    assert answer.data is not None
    assert answer.data["reached"] == {"games": 2, "wins": 1, "losses": 1, "avg_margin": pytest.approx(5.0)}
    assert answer.data["fell_short"] == {"games": 2, "wins": 1, "losses": 1, "avg_margin": pytest.approx(0.0)}
    assert answer.data["whose"] == "opponent"
