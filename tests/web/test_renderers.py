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
    con.execute("CREATE TABLE games (event_id VARCHAR, season INTEGER, season_type INTEGER, date VARCHAR, home_team_id VARCHAR, away_team_id VARCHAR, winner_team_id VARCHAR, home_score INTEGER, away_score INTEGER)")
    con.execute("INSERT INTO games VALUES ('e1', 2026, 2, '2026-01-01', '10', '11', '10', 110, 100), ('e2', 2026, 2, '2026-01-03', '11', '10', '10', 99, 120)")
    con.execute(
        "CREATE TABLE player_box_stats (event_id VARCHAR, athlete_id VARCHAR, team_id VARCHAR, opponent_team_id VARCHAR, season INTEGER, season_type INTEGER, "
        "minutes INTEGER, points INTEGER, rebounds INTEGER, assists INTEGER, steals INTEGER, blocks INTEGER, turnovers INTEGER, fouls INTEGER, "
        "threePointFieldGoalsMade INTEGER, fieldGoalsMade INTEGER, freeThrowsMade INTEGER)"
    )
    con.execute(
        "INSERT INTO player_box_stats VALUES "
        "('e1','1','10','11',2026,2,36,34,10,5,1,1,2,2,4,12,6),"
        "('e2','1','10','11',2026,2,35,31,9,6,2,0,3,1,3,11,6),"
        "('e1','2','11','10',2026,2,30,18,4,9,1,0,2,3,2,7,2)"
    )
    con.execute(
        "CREATE TABLE player_season_stats (athlete_id VARCHAR, team_id VARCHAR, season INTEGER, season_type INTEGER, gamesPlayed INTEGER, "
        "avgPoints DOUBLE, avgRebounds DOUBLE, avgAssists DOUBLE, points INTEGER, rebounds INTEGER, assists INTEGER)"
    )
    con.execute(
        "INSERT INTO player_season_stats VALUES "
        "('1','10',2026,2,2,32.5,9.5,5.5,65,19,11),('1','10',2025,2,2,28.0,8.0,4.0,56,16,8),('2','11',2026,2,1,18.0,4.0,9.0,18,4,9)"
    )
    con.execute("CREATE OR REPLACE VIEW player_season_stats_deduped AS SELECT * FROM player_season_stats")
    con.execute("CREATE TABLE standings (team_id VARCHAR, season INTEGER, season_type INTEGER, wins DOUBLE, losses DOUBLE, winPercent DOUBLE, playoffSeed DOUBLE, streak DOUBLE)")
    con.execute("INSERT INTO standings VALUES ('10', 2026, 2, 50, 32, 0.6098, 3, 1)")
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
