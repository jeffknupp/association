"""Guards the contract between the page's renderers - one per shape a
planned point is read and said as - and the readers that produce their data.

The renderers live in JavaScript, in `web/static/index.html`, keyed by the
answer's planned shape (``Answer.shape``), and each draws the bodies its shape's readers say,
reading keys out of `Answer.data`. Nothing in Python or in the browser
complains when a reader renames one: the body's `needs` check fails, the
answer falls back to text, and the page looks like it simply never had a
table. That is this project's recurring failure shape - a fast, fluent answer
that is quietly less than it was - so it gets a test rather than a
convention.

These tests read the renderers' bodies and `needs` lists out of the page
itself, so there is one source for what each body depends on rather than a
copy to keep in step; and where node is installed they run each renderer's
own choice of body (its `of`) on the planned shape and the data a case is
answered with, so a case is checked against the body the page would draw.
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any, get_args

import duckdb
import pytest
from routed import Said, planned_answer
from warehouse_rows import plays_all_complete

from association.nba.netpoints import FINGERPRINT_CATEGORIES
from association.query.answer import AnswerContext
from association.query.compose.plan import plan_point, planned_shape
from association.query.compose.sentence import _FRACTION_COLUMNS
from association.query.entities import names_of
from association.query.parse import with_point
from association.query.reading import (
    GAME_HIGHS,
    GAMES_COUNTED,
    LINE_RECORD,
    LINE_RUNS,
    NETPOINTS_RATINGS,
    PERIOD_LOG,
    PERIOD_RANKING,
    PLAYER_COMPARISON,
    PLAYER_LINE,
    PLAYER_LOG,
    PLAYER_MEETINGS,
    PLAYER_RANKING,
    PLAYER_SPLITS,
    PRESENCE_SPLIT,
    SEASON_HISTORY,
    SHOT_DISTANCE,
    TEAM_LINE,
    TEAM_MEETINGS,
    TEAM_OUTLOOK,
    TEAM_PERIOD_TOTAL,
    TEAM_RANKING,
    TEAM_RECORD,
    TEAM_SEASON_HISTORY,
    Companion,
    Cuts,
    Line,
    Measure,
    Period,
    PointShape,
    Reading,
    Shape,
    Span,
    Window,
)
from association.query.reading import Subject as Who
from association.query.subject import read_subject
from association.web.app import INDEX_HTML

_ADA = Who(kind="player", players=("Ada Star",))
_ROCKETS = Who(kind="team", teams=("Rockets",))
_PAIR = Who(kind="pair", players=("Ada Star", "Bo Wall"))
_THIRTY = Line(measure="points", value=30, as_typed="30+ points", keyed=True)
_POINTS = Measure(key="points", as_typed="points")

# One case per body a shape's renderer draws, keyed "<shape>.<body>" - the key
# the words ask and the typed reading - chosen to produce the body the
# renderer draws rather than an empty or refusing one. A "compiled" case is a
# point its reader steps aside for, which the compiler's own sentence answers.
CASES: dict[str, tuple[PointShape, Reading]] = {
    "ranking.leaders": (PLAYER_RANKING, Reading(measure=_POINTS, window=Window(count=3))),
    "ranking.counts": (GAMES_COUNTED, Reading(lines=(Line(measure="points", value=30, as_typed="30 points", keyed=True),), measure=_POINTS)),
    "ranking.periods": (PERIOD_RANKING, Reading(span=Span(season=2026), period=Period(number=1))),
    "ranking.teams": (TEAM_RANKING, Reading(measure=Measure(key="record", as_typed="record"), span=Span(season=2026))),
    # A count of games over a line by player at home, which the count's reader
    # steps aside for (a venue its words do not state): the compiler's own
    # ranking. (A ranking of players over their games is read and said by its
    # own reader since decision D17 - the leaders' body.)
    "ranking.compiled": (GAMES_COUNTED, Reading(lines=(_THIRTY,), measure=_POINTS, cuts=Cuts(venue="home"), span=Span(season=2026))),
    "rows.games": (PLAYER_LOG, Reading(who=_ADA, window=Window(count=3))),
    "rows.highs": (GAME_HIGHS, Reading(measure=_POINTS)),
    "rows.figures": (PERIOD_LOG, Reading(who=_ADA, span=Span(season=2026), period=Period(number=1))),
    "rows.compiled": (GAMES_COUNTED, Reading(lines=(_THIRTY, Line(measure="rebounds", value=5, as_typed="5 rebounds", keyed=True)), measure=_POINTS)),
    "scalar.line": (PLAYER_LINE, Reading(who=_ADA, measure=_POINTS, span=Span(season=2026))),
    "scalar.team_line": (TEAM_LINE, Reading(who=_ROCKETS, measure=_POINTS, span=Span(season=2026))),
    "scalar.record": (TEAM_RECORD, Reading(who=_ROCKETS, span=Span(season=2026))),
    "scalar.projection": (TEAM_OUTLOOK, Reading(who=_ROCKETS, span=Span(season=2026))),
    "scalar.figures": (TEAM_PERIOD_TOTAL, Reading(who=_ROCKETS, span=Span(season=2026), period=Period(number=1))),
    "scalar.distance": (SHOT_DISTANCE, Reading(who=_ADA, span=Span(season=2026))),
    "scalar.ratings": (NETPOINTS_RATINGS, Reading(who=_ADA, span=Span(season=2026))),
    "scalar.compiled": (GAMES_COUNTED, Reading(who=_ADA, lines=(_THIRTY,), measure=_POINTS, cuts=Cuts(venue="home"), span=Span(season=2026))),
    "comparison.subject": (PLAYER_COMPARISON, Reading(who=_PAIR)),
    "comparison.met": (PLAYER_MEETINGS, Reading(who=_PAIR, span=Span(season=2026))),
    "comparison.opponent": (TEAM_MEETINGS, Reading(who=Who(kind="teams", teams=("Rockets", "Mavericks")), span=Span(season=2026))),
    "comparison.compiled": (PLAYER_MEETINGS, Reading(who=_PAIR, span=Span(both=True))),
    "split.season": (SEASON_HISTORY, Reading(who=_ADA, measure=_POINTS)),
    "split.team_season": (TEAM_SEASON_HISTORY, Reading(who=_ROCKETS, measure=_POINTS, span=Span(since=2026))),
    "split.splits": (PLAYER_SPLITS, Reading(who=_ADA, span=Span(season=2026), split="wins_losses")),
    "split.presence": (PRESENCE_SPLIT, Reading(who=_ROCKETS, companions=(Companion(player="Ada Star"),), span=Span(season=2026))),
    "split.line": (LINE_RECORD, Reading(who=_ADA, lines=(Line(measure="points", value=32, as_typed="32 points", keyed=True),), measure=_POINTS, span=Span(season=2026))),
    "split.compiled": (SEASON_HISTORY, Reading(who=_ADA, measure=_POINTS, cuts=Cuts(opponent="Mavericks"))),
    "runs.runs": (LINE_RUNS, Reading(who=_ROCKETS, measure=Measure(won=True), span=Span(season=2026))),
}


def _renderer_blocks() -> dict[str, str]:
    """shape -> its renderer's source, parsed from the page's RENDERERS table."""
    page = INDEX_HTML.read_text()
    table = re.search(r"const RENDERERS = \{(.*?)\n  \};", page, re.S)
    assert table is not None, "could not find the RENDERERS table in index.html"
    found = dict(re.findall(r"^    (\w+): \{\n(.*?)\n    \},$", table.group(1), re.M | re.S))
    assert found, "found the RENDERERS table but no renderers in it"
    return found


def _renderers() -> dict[str, dict[str, list[str]]]:
    """shape -> body -> the Answer.data keys the body reads, parsed from the page.

    Parsed rather than duplicated here: a hand-copied list would drift, and a
    drifted list would assert the wrong thing while looking maintained.
    """
    renderers = {}
    for shape, block in _renderer_blocks().items():
        bodies = re.findall(r"^        (\w+): \{ needs: \[([^\]]*)\], draw: \w+ \},$", block, re.M)
        assert bodies, f"the {shape} renderer names no body"
        renderers[shape] = {body: re.findall(r'"([^"]+)"', needs) for body, needs in bodies}
    return renderers


def test_the_page_has_one_renderer_per_shape() -> None:
    """The page renders by the planned point's shape: one
    renderer per :data:`~association.query.reading.Shape`, and none for a
    label - a renderer keyed "leaderboards" would never fire, and nothing
    else would ever say so."""
    assert sorted(_renderers()) == sorted(get_args(Shape))


def test_the_comparison_renderer_reads_netpoints_without_requiring_it() -> None:
    """The page shows the NetPoints rows when a season has them, but must not
    list the key in `needs`: NetPoints starts in 2019, and requiring it would
    drop the whole table back to plain text for every earlier season."""
    page = INDEX_HTML.read_text()
    block = re.search(r"function playersBody\(d, text\) \{(.*?)\n  \}\n", page, re.S)
    assert block is not None, "could not find the comparison's players body"
    assert "d.netpoints" in block.group(1), "the body no longer reads the NetPoints summary"
    assert "netpoints" not in _renderers()["comparison"]["subject"], "netpoints must stay out of `needs` - see the docstring"


def test_a_signed_cell_still_counts_as_a_number_for_alignment() -> None:
    """The NetPoints rows are signed ("+5.87"), and table() reads alignment off
    the values: one cell the numeric pattern rejects left-aligns the whole
    column, digits and all."""
    page = INDEX_HTML.read_text()
    assert r"/^[+-]?[\d,]+(\.\d+)?$/" in page, "table()'s numeric test no longer accepts a leading +"


def test_the_pages_fraction_set_matches_the_compilers() -> None:
    """``usage_pct`` is stored as a percent (``fetch/advanced_stats.py``
    computes it as ``100.0 * ...``), the same fact the compiler's own
    ``_FRACTION_COLUMNS`` (``compose/sentence.py``) holds - only ``ts_pct``
    and ``efg_pct`` are raw fractions (0.57) needing a ``* 100`` before a "%"
    sign. The page keeps a hand-copied ``FRACTIONS`` Set of the same fact
    (``measureCell``), and one concept in two files under two different
    names is exactly what `scripts/check_duplicate_names.py` cannot see: a
    rate in the page's copy that is not a fraction prints 100x too large -
    "USG% 2435.5%" (#225). Parsed out of the page rather than hand-copied
    here, so the two sets cannot drift apart without this failing.
    """
    page = INDEX_HTML.read_text()
    block = re.search(r"const FRACTIONS = new Set\(\[([^\]]*)\]\);", page)
    assert block is not None, "could not find the FRACTIONS set in index.html"
    page_fractions = set(re.findall(r'"([^"]+)"', block.group(1)))
    assert page_fractions == set(_FRACTION_COLUMNS)


def test_the_test_cases_here_cover_every_body() -> None:
    """Otherwise adding a body silently adds nothing to the tests below. A
    chart's frames read the answer's artifacts, not its data, and need
    nothing of it."""
    bodies = {f"{shape}.{body}" for shape, each in _renderers().items() for body, needs in each.items() if needs}
    assert sorted(CASES) == sorted(bodies)


@pytest.fixture
def ctx(tmp_path: Path) -> AnswerContext:
    """A warehouse small enough to reason about, holding whatever every
    reader under test needs to return a populated result."""
    con = duckdb.connect()
    con.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    con.execute("INSERT INTO players VALUES ('1', 'Ada Star'), ('2', 'Bo Wall')")
    con.execute("CREATE TABLE teams (team_id VARCHAR, display_name VARCHAR, abbreviation VARCHAR, location VARCHAR, name VARCHAR)")
    con.execute("INSERT INTO teams VALUES ('10', 'Houston Rockets', 'HOU', 'Houston', 'Rockets'), ('11', 'Dallas Mavericks', 'DAL', 'Dallas', 'Mavericks')")
    # home_linescores/away_linescores are a team's quarter totals' source, comma-joined
    # per-period points summing to home_score/away_score. neutral_site/venue_city
    # back team_metrics.TEAM_GAMES_SQL's NBA Cup final detection - neither game
    # here is one.
    con.execute(
        "CREATE TABLE games (event_id VARCHAR, season INTEGER, season_type INTEGER, date VARCHAR, home_team_id VARCHAR, "
        "away_team_id VARCHAR, winner_team_id VARCHAR, home_score INTEGER, away_score INTEGER, home_linescores VARCHAR, away_linescores VARCHAR, "
        "neutral_site BOOLEAN, venue_city VARCHAR)"
    )
    # A UTC tip stamp, as ESPN stores every game's date: a bare day here read
    # as midnight UTC, the evening before in the East, and dated a day early.
    con.execute(
        "INSERT INTO games VALUES "
        "('e1', 2026, 2, '2026-01-02T00:30Z', '10', '11', '10', 110, 100, '28,27,30,25', '24,26,25,25', FALSE, 'Houston'),"
        "('e2', 2026, 2, '2026-01-04T00:30Z', '11', '10', '10', 99, 120, '24,25,25,25', '30,32,28,30', FALSE, 'Dallas')"
    )
    # real_games is the one filtered view of `games` every reader reads
    # instead (see conditions.py's module docstring) - a plain copy here, since
    # this fixture holds no placeholder, phantom or duplicate rows to filter.
    con.execute("CREATE VIEW real_games AS SELECT * FROM games")
    # did_not_play is on every real row, and a player's game log reads it: a
    # DNP is not a game he played. fieldGoalsAttempted is read alongside
    # fieldGoalsMade wherever FG% is computed (conditions._PLAYER_LINE,
    # _matchup_line) - not only where a renderer displays it.
    con.execute(
        "CREATE TABLE player_box_stats (event_id VARCHAR, athlete_id VARCHAR, team_id VARCHAR, opponent_team_id VARCHAR, season INTEGER, season_type INTEGER, "
        "did_not_play BOOLEAN, minutes INTEGER, points INTEGER, rebounds INTEGER, assists INTEGER, steals INTEGER, blocks INTEGER, turnovers INTEGER, fouls INTEGER, "
        "threePointFieldGoalsMade INTEGER, fieldGoalsMade INTEGER, fieldGoalsAttempted INTEGER, freeThrowsMade INTEGER)"
    )
    con.execute(
        "INSERT INTO player_box_stats VALUES "
        "('e1','1','10','11',2026,2,FALSE,36,34,10,5,1,1,2,2,4,12,22,6),"
        "('e2','1','10','11',2026,2,FALSE,35,31,9,6,2,0,3,1,3,11,21,6),"
        "('e1','2','11','10',2026,2,FALSE,30,18,4,9,1,0,2,3,2,7,16,2)"
    )
    # team_box_stats carries the team-perspective row of each game - the home
    # side of it (games is home/away-oriented) - and is what conditions._team_games
    # and _player_games both join through for a team's or a player's result.
    con.execute(
        "CREATE TABLE team_box_stats (event_id VARCHAR, team_id VARCHAR, opponent_team_id VARCHAR, season INTEGER, season_type INTEGER, home_away VARCHAR, "
        "offensiveRebounds INTEGER, defensiveRebounds INTEGER, assists INTEGER, threePointFieldGoalsMade INTEGER, fieldGoalsMade INTEGER, fieldGoalsAttempted INTEGER)"
    )
    con.execute(
        "INSERT INTO team_box_stats VALUES "
        "('e1','10','11',2026,2,'home',10,30,25,12,40,85),"
        "('e1','11','10',2026,2,'away',8,28,20,10,36,80),"
        "('e2','11','10',2026,2,'home',9,27,22,11,35,78),"
        "('e2','10','11',2026,2,'away',11,31,28,14,44,88)"
    )
    # shot_chart backs period_split and shot_distance. Coordinates are feet
    # from the rim (25, 0) - see nba.court.HOOP_Y - not the baseline.
    con.execute(
        "CREATE TABLE shot_chart (event_id VARCHAR, athlete_id VARCHAR, season INTEGER, season_type INTEGER, period INTEGER, made BOOLEAN, "
        "shot_type VARCHAR, points_attempted INTEGER, description VARCHAR, coordinate_x DOUBLE, coordinate_y DOUBLE)"
    )
    con.execute(
        "INSERT INTO shot_chart VALUES "
        "('e1','1',2026,2,1,TRUE,'Jump Shot',2,'Ada Star makes 10-foot jumper',25,10),"
        "('e1','1',2026,2,1,FALSE,'Jump Shot',0,'Ada Star misses 24-foot three point jumper',25,26),"
        "('e1','1',2026,2,2,TRUE,'Jump Shot',3,'Ada Star makes 25-foot three point jumper',25,27),"
        "('e2','1',2026,2,1,TRUE,'Jump Shot',2,'Ada Star makes 8-foot jumper',25,8)"
    )
    # team_power_index backs team_outlook - one regular-season BPI snapshot.
    con.execute(
        "CREATE TABLE team_power_index (season INTEGER, season_type INTEGER, team_id VARCHAR, last_updated VARCHAR, bpi DOUBLE, bpioffense DOUBLE, bpidefense DOUBLE, "
        "numwins DOUBLE, numlosses DOUBLE, projectedw DOUBLE, projectedl DOUBLE, probmakeplayoffs DOUBLE, probmakeconfchamp DOUBLE, probmaketitlegame DOUBLE, "
        "probwintitle DOUBLE, sosoverall DOUBLE, sosoverallrank DOUBLE)"
    )
    con.execute("INSERT INTO team_power_index VALUES (2026, 2, '10', '2026-04-10', 5.2, 3.1, -2.1, 50, 32, 52, 30, 95.0, 20.0, 10.0, 5.0, 0.51, 15)")
    con.execute(
        "CREATE TABLE player_season_stats (athlete_id VARCHAR, team_id VARCHAR, season INTEGER, season_type INTEGER, gamesPlayed INTEGER, "
        "avgPoints DOUBLE, avgRebounds DOUBLE, avgAssists DOUBLE, avgSteals DOUBLE, avgBlocks DOUBLE, avgTurnovers DOUBLE, avgFouls DOUBLE, avgMinutes DOUBLE, "
        "points INTEGER, rebounds INTEGER, assists INTEGER, steals INTEGER, blocks INTEGER, turnovers INTEGER, fouls INTEGER, "
        "fieldGoalsMade INTEGER, fieldGoalsAttempted INTEGER, threePointFieldGoalsMade INTEGER, threePointFieldGoalsAttempted INTEGER, freeThrowsMade INTEGER, freeThrowsAttempted INTEGER)"
    )
    # Ada Star's 40 games clear the leaderboard's 20-game qualifier. Under it,
    # the `leaderboard` case below produces an EMPTY board - which still has a
    # `leaders` key, so the contract test passes while proving nothing. The
    # cases exist to produce populated shapes; see CASES above.
    con.execute(
        "INSERT INTO player_season_stats VALUES "
        "('1','10',2026,2,40,32.5,9.5,5.5,1.5,0.5,2.5,1.5,35.5,1300,380,220,60,20,100,60,480,1000,120,330,220,260),"
        "('1','10',2025,2,2,28.0,8.0,4.0,1.0,0.5,2.0,2.0,34.0,56,16,8,2,1,4,4,22,48,4,12,8,10),"
        "('2','11',2026,2,1,18.0,4.0,9.0,1.0,0.0,2.0,3.0,30.0,18,4,9,1,0,2,3,7,15,1,4,3,4)"
    )
    con.execute("CREATE OR REPLACE VIEW player_season_stats_deduped AS SELECT * FROM player_season_stats")
    # player_compare reports a NetPoints summary alongside the box-score line.
    con.execute(
        "CREATE TABLE net_points_player (athlete_id VARCHAR, season INTEGER, net_points_season_type VARCHAR, "
        "overall DOUBLE, offense DOUBLE, defense DOUBLE, overall_per_100_poss DOUBLE, offense_per_100_poss DOUBLE, defense_per_100_poss DOUBLE, total_minutes BIGINT, games BIGINT)"
    )
    con.execute("INSERT INTO net_points_player VALUES ('1',2026,'Regular Season',120.0,90.0,30.0,4.5,3.4,1.1,71,2),('2',2026,'Regular Season',-10.0,-4.0,-6.0,-0.8,-0.3,-0.5,30,1)")
    # player_netpoints' play-type fingerprint, one row per player-season - see
    # test_readers.py's own np_ctx fixture for the same shape.
    _fp_categories = [c for c in FINGERPRINT_CATEGORIES.values() if c != "total"]
    _fp_cols = ", ".join(f"{cat}_{side}_net_pts DOUBLE" for cat in _fp_categories for side in ("o", "d", "t"))
    con.execute(f"CREATE TABLE net_points_player_fingerprint (athlete_id VARCHAR, season INTEGER, total_poss DOUBLE, {_fp_cols})")
    _fp_values = ", ".join("1.0" for _ in _fp_categories for _ in range(3))
    con.execute(f"INSERT INTO net_points_player_fingerprint VALUES ('1', 2026, 71, {_fp_values})")
    con.execute("UPDATE net_points_player_fingerprint SET two_pt_t_net_pts = 25.9, two_pt_o_net_pts = 25.3")
    con.execute(
        "CREATE TABLE standings (team_id VARCHAR, season INTEGER, season_type INTEGER, wins DOUBLE, losses DOUBLE, winPercent DOUBLE, playoffSeed DOUBLE, streak DOUBLE, "
        'gamesBehind DOUBLE, "Home" VARCHAR, "Road" VARCHAR, "Last Ten Games" VARCHAR, avgPointsFor DOUBLE, avgPointsAgainst DOUBLE, differential DOUBLE)'
    )
    con.execute(
        "INSERT INTO standings VALUES "
        "('10', 2026, 2, 50, 32, 0.6098, 3, 1, 4, '28-13', '22-19', '6-4', 115.2, 111.0, 4.2),"
        "('11', 2026, 2, 40, 42, 0.4878, 8, -1, 12, '20-21', '20-21', '5-5', 108.0, 110.0, -2.0)"
    )
    # team_record checks a season's standings against the team's own game
    # count; team_stat's non-record metrics (team_metrics.season_table) read
    # the rest of this table's columns for every team in the season, whether
    # or not the question asked about them - `wanted` narrows which ones
    # `team_stat` reports, not which ones the SQL computes.
    con.execute(
        "CREATE TABLE team_season_stats (season INTEGER, season_type INTEGER, team_id VARCHAR, gamesPlayed DOUBLE, points DOUBLE, avgPoints DOUBLE, "
        "fieldGoalsMade DOUBLE, fieldGoalsAttempted DOUBLE, fieldGoalPct DOUBLE, threePointFieldGoalsMade DOUBLE, threePointFieldGoalsAttempted DOUBLE, threePointFieldGoalPct DOUBLE, "
        "freeThrowsMade DOUBLE, freeThrowsAttempted DOUBLE, freeThrowPct DOUBLE, trueShootingPct DOUBLE, effectiveFGPct DOUBLE, "
        "avgRebounds DOUBLE, offensiveRebounds DOUBLE, avgOffensiveRebounds DOUBLE, avgDefensiveRebounds DOUBLE, avgAssists DOUBLE, avgSteals DOUBLE, avgBlocks DOUBLE, avgFouls DOUBLE, "
        "avgThreePointFieldGoalsMade DOUBLE, avgThreePointFieldGoalsAttempted DOUBLE, avgFieldGoalsMade DOUBLE, avgFreeThrowsMade DOUBLE, avgFreeThrowsAttempted DOUBLE, "
        "totalTurnovers DOUBLE, turnovers DOUBLE, pointsInPaint DOUBLE, fastBreakPoints DOUBLE)"
    )
    # Built from a {column: value} dict rather than a positional VALUES tuple:
    # the table has 34 columns, and a miscounted tuple binds a real column to
    # the wrong figure with no error - exactly the silent-mismatch shape this
    # file exists to catch elsewhere. Two rows, close but not equal, so
    # team_stat's rank is a real comparison rather than a tie.
    _team_season_row = {
        "gamesPlayed": 82.0,
        "points": 9430.0,
        "avgPoints": 115.0,
        "fieldGoalsMade": 40.5,
        "fieldGoalsAttempted": 85.0,
        "fieldGoalPct": 47.6,
        "threePointFieldGoalsMade": 13.0,
        "threePointFieldGoalsAttempted": 35.0,
        "threePointFieldGoalPct": 37.1,
        "freeThrowsMade": 19.0,
        "freeThrowsAttempted": 23.0,
        "freeThrowPct": 82.6,
        "trueShootingPct": 58.5,
        "effectiveFGPct": 53.0,
        "avgRebounds": 44.0,
        "offensiveRebounds": 9.0,
        "avgOffensiveRebounds": 35.0,
        "avgDefensiveRebounds": 26.0,
        "avgAssists": 7.5,
        "avgSteals": 5.0,
        "avgBlocks": 18.0,
        "avgFouls": 13.0,
        "avgThreePointFieldGoalsMade": 35.0,
        "avgThreePointFieldGoalsAttempted": 40.5,
        "avgFieldGoalsMade": 19.0,
        "avgFreeThrowsMade": 23.0,
        "avgFreeThrowsAttempted": 12.5,
        "totalTurnovers": 12.5,
        "turnovers": 12.5,
        "pointsInPaint": 45.0,
        "fastBreakPoints": 15.0,
    }
    for _team_id, _factor in (("10", 1.0), ("11", 0.94)):
        _row = {"season": 2026, "season_type": 2, "team_id": _team_id, **{k: v * _factor if isinstance(v, float) else v for k, v in _team_season_row.items()}}
        con.execute(f"INSERT INTO team_season_stats ({', '.join(_row)}) VALUES ({', '.join('?' for _ in _row)})", list(_row.values()))
    con.execute(
        "CREATE OR REPLACE VIEW player_game_log AS SELECT pbs.*, NULL::INTEGER AS threePointFieldGoalsAttempted, NULL::INTEGER AS freeThrowsAttempted, "
        "p.display_name AS player_name, g.date AS game_date, t.abbreviation AS team_abbr, o.abbreviation AS opponent_abbr "
        "FROM player_box_stats pbs LEFT JOIN players p ON p.athlete_id = pbs.athlete_id LEFT JOIN games g ON g.event_id = pbs.event_id "
        "LEFT JOIN teams t ON t.team_id = pbs.team_id LEFT JOIN teams o ON o.team_id = pbs.opponent_team_id"
    )
    plays_all_complete(con)
    return AnswerContext(con=con, out_dir=tmp_path / "out")


def _read(ctx: AnswerContext, case: str) -> tuple[PointShape | None, Said]:
    """The planned shape and the answer of the case named ``case``: the
    compiler's, over the key and the typed reading the case states, with no
    question words to move its own point - planned once, as ``Agent.ask``
    plans a question's Reading."""
    asked, scope = CASES[case]
    reading = with_point("", replace(scope, asked=asked, subject=read_subject(names_of(ctx.con), "", asked, scope)))
    result = planned_answer(ctx, reading)
    assert result is not None, f"the compiler has no reading of {case}'s case"
    return planned_shape(reading, plan_point(reading)), result


def test_each_case_is_planned_as_the_shape_whose_renderer_draws_it(ctx: AnswerContext, subtests: Any) -> None:
    """A case under the wrong shape would check a body its answers never reach."""
    for case in sorted(CASES):
        with subtests.test(case=case):
            shape, _ = _read(ctx, case)
            assert shape is not None and shape.shape == case.split(".")[0], f"{case} is planned as {shape}"


def test_every_key_a_body_reads_is_a_key_its_reader_produces(ctx: AnswerContext, subtests: Any) -> None:
    """The point of the whole file. A reader that renames a key leaves the
    page silently falling back to text; this fails instead."""
    renderers = _renderers()
    for case in sorted(CASES):
        with subtests.test(case=case):
            shape, body = case.split(".")
            data = _read(ctx, case)[1].data
            missing = [key for key in renderers[shape][body] if data.get(key) is None]
            assert missing == [], f"{case} no longer produces {missing} - the page's renderer would fall back to text"


def test_a_ranking_over_the_players_games_is_drawn_as_the_leaders(ctx: AnswerContext) -> None:
    """Decision D17: the league's players ranked over their games at home (a
    ranking the season line does not read) is its reader's and sayer's, not
    the compiler's own sentence - its data carries what the leaders' body
    reads, and no compiler skeleton to send the page to the text."""
    scope = Reading(measure=_POINTS, cuts=Cuts(venue="home"), span=Span(season=2026))
    reading = with_point("", replace(scope, asked=PLAYER_RANKING, subject=read_subject(names_of(ctx.con), "", PLAYER_RANKING, scope)))
    result = planned_answer(ctx, reading)
    assert result is not None and planned_shape(reading, plan_point(reading)) == PointShape("player_games", "ranking", "player")
    assert "skeleton" not in result.data
    assert [key for key in _renderers()["ranking"]["leaders"] if result.data.get(key) is None] == []
    assert result.data["leaders"][0]["display_name"] and result.data["question_shape"].startswith("points per game, ")


def test_the_data_a_body_reads_is_json_serializable_as_is(ctx: AnswerContext) -> None:
    """It reaches the page over HTTP. Serialized with a `default=` fallback, a
    Decimal or a date would arrive as a *string*, and a column the renderer
    formats as a number would quietly become left-aligned text. So this
    serializes strictly: anything needing a fallback raises here instead -
    but a date, which the compiler's own rows carry as their ``day`` and the
    API sends as its ISO string, a column the page draws as text either way."""

    def dates_only(value: object) -> str:
        if isinstance(value, date):
            return value.isoformat()
        raise TypeError(f"{type(value).__name__} is not JSON serializable")

    renderers = _renderers()
    for case in sorted(CASES):
        shape, body = case.split(".")
        data = _read(ctx, case)[1].data
        json.dumps({key: data[key] for key in renderers[shape][body]}, default=dates_only)


def _node() -> str:
    """The node binary, or a skip: these are cheap extra guards, not the
    reason the suite exists. Nothing they run reaches a network."""
    import shutil

    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed - the page's JavaScript is not run here")
    return node


def test_each_renderer_chooses_the_body_its_case_names(ctx: AnswerContext) -> None:
    """Each renderer chooses the body an answer's data carries by its planned
    shape (`of`): run here, in node, on the shape and the data each case is
    answered with, so a case is held to the body the page would draw - a
    `by` the choice misreads draws nothing, and falls back to text."""
    import subprocess

    node = _node()
    choosers = {}
    for shape, block in _renderer_blocks().items():
        chooser = re.search(r"^      of: (.*),$", block, re.M)
        assert chooser is not None, f"the {shape} renderer has no `of`"
        choosers[shape] = chooser.group(1)
    answered = []
    for case in sorted(CASES):
        planned, result = _read(ctx, case)
        assert planned is not None
        answered.append({"case": case, "shape": {"relation": planned.relation, "shape": planned.shape, "by": planned.by}, "data": result.data})
    script = "const OF = {" + ", ".join(f"{shape}: {chooser}" for shape, chooser in choosers.items()) + "};\n"
    script += "const cases = " + json.dumps(answered, default=str) + ";\n"
    script += "console.log(JSON.stringify(cases.map(c => [c.case, c.shape.shape + '.' + OF[c.shape.shape](c.data, c.shape)])));\n"
    run = subprocess.run([node, "-e", script], capture_output=True, text=True, check=False)
    assert run.returncode == 0, run.stderr
    chosen = dict(json.loads(run.stdout))
    assert {case: body for case, body in chosen.items() if case != body} == {}


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
    result = subprocess.run([node, "--check", path], capture_output=True, text=True, check=False)
    Path(path).unlink()
    assert result.returncode == 0, result.stderr
