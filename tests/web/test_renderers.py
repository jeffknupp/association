"""Guards the contract between the page's per-intent renderers and the
templates that produce their data.

The renderers live in JavaScript, in `web/static/index.html`, and read keys out
of `Answer.data`. Nothing in Python or in the browser complains when a template
renames one: the renderer's `needs` check fails, the answer falls back to text,
and the page looks like it simply never had a table. That is this project's
recurring failure shape - a fast, fluent answer that is quietly less than it
was - so it gets a test rather than a convention.

These tests read the `needs` lists out of the page itself, so there is one
source for what each renderer depends on rather than a copy to keep in step.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import duckdb
import pytest

from association.query.templates import TEMPLATES, TemplateContext
from association.web.app import INDEX_HTML

# One set of slots per intent that has a renderer, chosen to produce the shape
# the renderer expects rather than an empty or refusing one.
CASES: dict[str, dict[str, Any]] = {
    "leaderboard": {"stat": "points", "limit": 3},
    "threshold_count": {"stat": "points", "threshold": 30},
    "single_game_high": {"stat": "points"},
    "player_history": {"player": "Ada Star", "stat": "points"},
    "player_compare": {"players": ["Ada Star", "Bo Wall"]},
    "game_log": {"player": "Ada Star", "limit": 3},
    "team_record": {"team": "Rockets", "season": 2026},
}


def _renderers() -> dict[str, list[str]]:
    """intent -> the Answer.data keys its renderer reads, parsed from the page.

    Parsed rather than duplicated here: a hand-copied list would drift, and a
    drifted list would assert the wrong thing while looking maintained.
    """
    page = INDEX_HTML.read_text()
    block = re.search(r"const RENDERERS = \{(.*?)\n  \};", page, re.S)
    assert block is not None, "could not find the RENDERERS table in index.html"
    found = re.findall(r"^    (\w+): \{\n      needs: \[([^\]]*)\],", block.group(1), re.M)
    assert found, "found the RENDERERS table but no renderers in it"
    return {intent: re.findall(r'"([^"]+)"', needs) for intent, needs in found}


def test_the_comparison_renderer_reads_netpoints_without_requiring_it() -> None:
    """The page shows the NetPoints rows when a season has them, but must not
    list the key in `needs`: NetPoints starts in 2019, and requiring it would
    drop the whole table back to plain text for every earlier season."""
    page = INDEX_HTML.read_text()
    block = re.search(r"player_compare: \{(.*?)\n    \},", page, re.S)
    assert block is not None, "could not find the player_compare renderer"
    assert "d.netpoints" in block.group(1), "the renderer no longer reads the NetPoints summary"
    assert '"netpoints"' not in block.group(1), "netpoints must stay out of `needs` - see the docstring"


def test_a_signed_cell_still_counts_as_a_number_for_alignment() -> None:
    """The NetPoints rows are signed ("+5.87"), and table() reads alignment off
    the values: one cell the numeric pattern rejects left-aligns the whole
    column, digits and all."""
    page = INDEX_HTML.read_text()
    assert r"/^[+-]?[\d,]+(\.\d+)?$/" in page, "table()'s numeric test no longer accepts a leading +"


def test_every_renderer_names_an_intent_that_actually_exists() -> None:
    """A renderer keyed 'leaderboards' would never fire, and nothing else would
    ever say so."""
    unknown = sorted(set(_renderers()) - set(TEMPLATES))
    assert unknown == [], f"renderers for intents that do not exist: {unknown}"


def test_the_test_cases_here_cover_every_renderer() -> None:
    """Otherwise adding a renderer silently adds nothing to the test below."""
    assert sorted(_renderers()) == sorted(CASES)


@pytest.fixture
def ctx(tmp_path: Path) -> TemplateContext:
    """A warehouse small enough to reason about, holding whatever every
    template under test needs to return a populated result."""
    con = duckdb.connect()
    con.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    con.execute("INSERT INTO players VALUES ('1', 'Ada Star'), ('2', 'Bo Wall')")
    con.execute("CREATE TABLE teams (team_id VARCHAR, display_name VARCHAR, abbreviation VARCHAR, location VARCHAR, name VARCHAR)")
    con.execute("INSERT INTO teams VALUES ('10', 'Houston Rockets', 'HOU', 'Houston', 'Rockets'), ('11', 'Dallas Mavericks', 'DAL', 'Dallas', 'Mavericks')")
    con.execute(
        "CREATE TABLE games (event_id VARCHAR, season INTEGER, season_type INTEGER, date VARCHAR, home_team_id VARCHAR, "
        "away_team_id VARCHAR, winner_team_id VARCHAR, home_score INTEGER, away_score INTEGER)"
    )
    con.execute("INSERT INTO games VALUES ('e1', 2026, 2, '2026-01-01', '10', '11', '10', 110, 100), ('e2', 2026, 2, '2026-01-03', '11', '10', '10', 99, 120)")
    # did_not_play is on every real row, and a player's game log reads it: a
    # DNP is not a game he played.
    con.execute(
        "CREATE TABLE player_box_stats (event_id VARCHAR, athlete_id VARCHAR, team_id VARCHAR, opponent_team_id VARCHAR, season INTEGER, season_type INTEGER, "
        "did_not_play BOOLEAN, minutes INTEGER, points INTEGER, rebounds INTEGER, assists INTEGER, steals INTEGER, blocks INTEGER, turnovers INTEGER, fouls INTEGER, "
        "threePointFieldGoalsMade INTEGER, fieldGoalsMade INTEGER, freeThrowsMade INTEGER)"
    )
    con.execute(
        "INSERT INTO player_box_stats VALUES "
        "('e1','1','10','11',2026,2,FALSE,36,34,10,5,1,1,2,2,4,12,6),"
        "('e2','1','10','11',2026,2,FALSE,35,31,9,6,2,0,3,1,3,11,6),"
        "('e1','2','11','10',2026,2,FALSE,30,18,4,9,1,0,2,3,2,7,2)"
    )
    con.execute(
        "CREATE TABLE player_season_stats (athlete_id VARCHAR, team_id VARCHAR, season INTEGER, season_type INTEGER, gamesPlayed INTEGER, "
        "avgPoints DOUBLE, avgRebounds DOUBLE, avgAssists DOUBLE, avgSteals DOUBLE, avgBlocks DOUBLE, avgTurnovers DOUBLE, avgFouls DOUBLE, avgMinutes DOUBLE, "
        "points INTEGER, rebounds INTEGER, assists INTEGER, steals INTEGER, blocks INTEGER, turnovers INTEGER, fouls INTEGER)"
    )
    # Ada Star's 40 games clear the leaderboard's 20-game qualifier. Under it,
    # the `leaderboard` case below produces an EMPTY board - which still has a
    # `leaders` key, so the contract test passes while proving nothing. The
    # cases exist to produce populated shapes; see CASES above.
    con.execute(
        "INSERT INTO player_season_stats VALUES "
        "('1','10',2026,2,40,32.5,9.5,5.5,1.5,0.5,2.5,1.5,35.5,1300,380,220,60,20,100,60),"
        "('1','10',2025,2,2,28.0,8.0,4.0,1.0,0.5,2.0,2.0,34.0,56,16,8,2,1,4,4),"
        "('2','11',2026,2,1,18.0,4.0,9.0,1.0,0.0,2.0,3.0,30.0,18,4,9,1,0,2,3)"
    )
    con.execute("CREATE OR REPLACE VIEW player_season_stats_deduped AS SELECT * FROM player_season_stats")
    # player_compare reports a NetPoints summary alongside the box-score line.
    con.execute(
        "CREATE TABLE net_points_player (athlete_id VARCHAR, season INTEGER, net_points_season_type VARCHAR, "
        "overall DOUBLE, offense DOUBLE, defense DOUBLE, overall_per_100_poss DOUBLE, offense_per_100_poss DOUBLE, defense_per_100_poss DOUBLE, total_minutes BIGINT, games BIGINT)"
    )
    con.execute("INSERT INTO net_points_player VALUES ('1',2026,'Regular Season',120.0,90.0,30.0,4.5,3.4,1.1,71,2),('2',2026,'Regular Season',-10.0,-4.0,-6.0,-0.8,-0.3,-0.5,30,1)")
    con.execute(
        "CREATE TABLE standings (team_id VARCHAR, season INTEGER, season_type INTEGER, wins DOUBLE, losses DOUBLE, winPercent DOUBLE, playoffSeed DOUBLE, streak DOUBLE, "
        'gamesBehind DOUBLE, "Home" VARCHAR, "Road" VARCHAR, "Last Ten Games" VARCHAR, avgPointsFor DOUBLE, avgPointsAgainst DOUBLE, differential DOUBLE)'
    )
    con.execute("INSERT INTO standings VALUES ('10', 2026, 2, 50, 32, 0.6098, 3, 1, 4, '28-13', '22-19', '6-4', 115.2, 111.0, 4.2)")
    # team_record checks a season's standings against the team's own game count.
    con.execute("CREATE TABLE team_season_stats (season INTEGER, season_type INTEGER, team_id VARCHAR, gamesPlayed DOUBLE)")
    con.execute("INSERT INTO team_season_stats VALUES (2026, 2, '10', 82)")
    con.execute(
        "CREATE OR REPLACE VIEW player_game_log AS SELECT pbs.*, p.display_name AS player_name, g.date AS game_date, t.abbreviation AS team_abbr, o.abbreviation AS opponent_abbr "
        "FROM player_box_stats pbs LEFT JOIN players p ON p.athlete_id = pbs.athlete_id LEFT JOIN games g ON g.event_id = pbs.event_id "
        "LEFT JOIN teams t ON t.team_id = pbs.team_id LEFT JOIN teams o ON o.team_id = pbs.opponent_team_id"
    )
    return TemplateContext(con=con, out_dir=tmp_path / "out")


def test_every_key_a_renderer_reads_is_a_key_its_template_produces(ctx: TemplateContext, subtests: Any) -> None:
    """The point of the whole file. A template that renames a key leaves the
    page silently falling back to text; this fails instead."""
    for intent, needs in sorted(_renderers().items()):
        with subtests.test(intent=intent):
            result = TEMPLATES[intent](ctx, CASES[intent])
            missing = [key for key in needs if result.data.get(key) is None]
            assert missing == [], f"{intent} no longer produces {missing} - the page's renderer would fall back to text"


def test_the_data_a_renderer_reads_is_json_serializable_as_is(ctx: TemplateContext) -> None:
    """It reaches the page over HTTP. Serialized with a `default=` fallback, a
    Decimal or a date would arrive as a *string*, and a column the renderer
    formats as a number would quietly become left-aligned text. So this
    serializes strictly: anything needing a fallback raises here instead."""
    for intent, needs in sorted(_renderers().items()):
        data = TEMPLATES[intent](ctx, CASES[intent]).data
        json.dumps({key: data[key] for key in needs})  # no default= on purpose


def test_the_pages_javascript_parses() -> None:
    """A syntax error in the inline script breaks the whole page - not one
    renderer, all of it - and no other test here would notice, because every
    other check reads the file as text.

    Skipped where node is missing rather than made a hard dependency: this is a
    cheap extra guard, not the reason the suite exists. `--check` parses and
    exits; it runs nothing and reaches no network.
    """
    import shutil
    import subprocess
    import tempfile

    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed - the page's JavaScript is not syntax-checked here")

    page = INDEX_HTML.read_text()
    script = re.search(r"<script>\n(.*)\n</script>", page, re.S)
    assert script is not None, "could not find the inline script in index.html"
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as handle:
        handle.write(script.group(1))
        path = handle.name
    result = subprocess.run([node, "--check", path], capture_output=True, text=True)
    Path(path).unlink()
    assert result.returncode == 0, result.stderr
