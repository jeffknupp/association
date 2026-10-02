"""Fixture tests for :mod:`association.query.compose` - the compiler landed
from the skeleton spike. Modeled on ``pg_ctx`` in ``tests/query/test_templates.py``:
a small ``player_game_log``/``games``/``players`` warehouse in miniature,
built once per test so every assertion is against numbers this file itself
defines, not the real warehouse (that comparison is ``golden.py``, run by
hand against the real database - see the landing report).

One test per skeleton, one per K1 rule the fixture can exercise, one per K2
move, and the two K2 guards - see the module docstrings of ``core.py`` and
``move.py`` for what each rule and move is.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import duckdb
import pytest
from routed import slots_route, with_subject

from association.fetch.repairs import real_games
from association.nba.season import current_season
from association.query import compose
from association.query.compose.adapt import to_query
from association.query.compose.core import Query, Refused, Unsupported, compile_query, run
from association.query.compose.move import _asc_or_desc, _career_scope, _everyone_career_scope, _ranking_minimum, read_point
from association.query.compose.move import team_move_point as _team_move_point
from association.query.compose.plan import plan
from association.query.compose.sentence import sentence
from association.query.compose.team import TeamQuery, run_team
from association.query.parse import with_point
from association.query.reading import Reading, Scope
from association.query.subject import Subject, read_subject
from association.query.templates.common import TemplateContext, TemplateResult


def _reading(con: duckdb.DuckDBPyConnection, intent: str, slots: dict[str, Any], question: str, subject: Subject | None = None) -> Reading:
    """A test's slot dict as the Reading the parser hands the compiler (5.0.0:
    the package takes the Reading alone): the typed scope, and the subject
    read from the question the way ``compose.move`` read it for a caller
    with none - never applied to the slots, so a case says what it did."""
    return Reading(scope=Scope.from_slots(slots), intent=intent, subject=subject or read_subject(con, question, intent, Scope.from_slots(dict(slots))))


def compose_answer(ctx: TemplateContext, intent: str, slots: dict[str, Any], question: str, subject: Subject | None = None, declined: Callable[[str], None] | None = None) -> TemplateResult | None:
    """``compose.answer`` over the point ``question`` moves for ``slots``."""
    return compose.answer(ctx, with_point(ctx.con, question, _reading(ctx.con, intent, slots, question, subject)), declined=declined)


def move_point(con: duckdb.DuckDBPyConnection, intent: str, slots: dict[str, Any], question: str, subject: Subject | None = None) -> Query | TeamQuery:
    """The point ``question`` moves for ``slots``, planned - raising the
    compiler's own ``Unsupported``/``Refused`` as the reader does."""
    return plan(read_point(con, _reading(con, intent, slots, question, subject), question))


def team_move_point(con: duckdb.DuckDBPyConnection, slots: dict[str, Any], question: str) -> TeamQuery | None:
    """``compose.move.team_move_point`` over ``slots``' scope and the subject read from the question."""
    return _team_move_point(con, Scope.from_slots(slots), question, read_subject(con, question, "", Scope.from_slots(dict(slots))))


#: Box-score columns, in the order ``_box`` below fills them - the same shape
#: ``player_box_stats`` carries in the real warehouse.
_BOX_COLUMNS = (
    "event_id VARCHAR, season INTEGER, season_type INTEGER, team_id VARCHAR, opponent_team_id VARCHAR, athlete_id VARCHAR, did_not_play BOOLEAN, "
    "minutes INTEGER, points INTEGER, rebounds INTEGER, assists INTEGER, steals INTEGER, blocks INTEGER, turnovers INTEGER, fouls INTEGER, plusMinus INTEGER, "
    "fieldGoalsMade INTEGER, fieldGoalsAttempted INTEGER, threePointFieldGoalsMade INTEGER, threePointFieldGoalsAttempted INTEGER, "
    "freeThrowsMade INTEGER, freeThrowsAttempted INTEGER, offensiveRebounds INTEGER, defensiveRebounds INTEGER"
)


def _box(
    event: str,
    season: int,
    team: str,
    opponent: str,
    athlete: str,
    *,
    dnp: bool = False,
    minutes: int = 30,
    pts: int = 0,
    reb: int = 0,
    ast: int = 0,
    fga: int | None = None,
    fta: int = 0,
    ftm: int = 0,
    pm: int = 0,
    season_type: int = 2,
) -> tuple[Any, ...]:
    """One ``player_box_stats`` row. ``fga`` defaults to ``pts`` (a made shot
    a point, roughly) so a TS% is always computable without naming it for
    every call; ``dnp`` is a did-not-play entry, whose stats are all NULL."""
    if dnp:
        return (event, season, season_type, team, opponent, athlete, True, *([None] * 17))
    made = pts // 2
    attempted = pts if fga is None else fga
    return (event, season, season_type, team, opponent, athlete, False, minutes, pts, reb, ast, 1, 0, 2, 1, pm, made, attempted, 0, 0, ftm, fta, 0, 0)


#: Team ids used throughout the fixture.
GS, BOS, DET, LAL = "1", "2", "3", "4"

#: Player ids, for readability in the game data below.
PODZ, CURRY, BROWN, SABONIS = "10", "11", "12", "13"


@pytest.fixture
def cx_ctx(tmp_path: Path) -> TemplateContext:
    """A small two-season warehouse: Golden State (Podziemski, Curry),
    Boston (Brown) and Detroit (Sabonis), plus two extra players with 22
    games each (Marcus Fillmore, Derek Vollmer) for the league-ranking tests,
    which need a pool past :data:`association.query.metrics.PER_GAME_MIN_GAMES`.

    ====  ======  ===============  ===================
    game  season  matchup           GS player's venue
    ====  ======  ===============  ===================
    g1    s       BOS home, GS away  away, opp BOS
    g2    s       GS home, DET away  home, opp DET
    g3    s       GS home, DET away  home, opp DET
    g4    s       LAL home, GS away  away, opp LAL (Podziemski DNP)
    g5    s       GS home, BOS away  home, opp BOS
    g6    s-1     BOS home, GS away  away, opp BOS
    g7    s-1     GS home, DET away  home, opp DET
    ====  ======  ===============  ===================

    Podziemski plays g1, g2, g3, g5 (season s) and g6, g7 (season s-1); he
    sits out g4. Curry plays g1, g3, g4, g5 and sits out g2. Brown (Boston)
    plays g1, g5, g6; Sabonis (Detroit) plays g2, g3, g7.
    """
    s = current_season()
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR, position_abbr VARCHAR)")
    c.execute(
        "INSERT INTO players VALUES "
        "('10','Brandin Podziemski','G'),('11','Stephen Curry','PG'),('12','Jaylen Brown','SF'),('13','Domantas Sabonis','C'),"
        "('90','Marcus Fillmore','PF'),('91','Derek Vollmer','PF')"
    )
    c.execute("CREATE TABLE teams (team_id VARCHAR, abbreviation VARCHAR, display_name VARCHAR)")
    c.execute("INSERT INTO teams VALUES ('1','GS','Golden State Warriors'),('2','BOS','Boston Celtics'),('3','DET','Detroit Pistons'),('4','LAL','Los Angeles Lakers')")
    c.execute(
        "CREATE TABLE games (event_id VARCHAR, season INTEGER, season_type INTEGER, date VARCHAR, home_team_id VARCHAR, away_team_id VARCHAR, "
        "home_score INTEGER, away_score INTEGER, winner_team_id VARCHAR)"
    )
    c.executemany(
        "INSERT INTO games VALUES (?, ?, 2, ?, ?, ?, ?, ?, ?)",
        [
            ("g1", s, f"{s - 1}-11-01T20:00Z", BOS, GS, 100, 110, GS),
            ("g2", s, f"{s - 1}-12-01T20:00Z", GS, DET, 99, 105, DET),
            ("g3", s, f"{s}-01-10T20:00Z", GS, DET, 120, 101, GS),
            ("g4", s, f"{s}-02-10T20:00Z", LAL, GS, 100, 90, LAL),
            ("g5", s, f"{s}-03-01T20:00Z", GS, BOS, 100, 95, GS),
            ("g6", s - 1, f"{s - 1}-02-01T20:00Z", BOS, GS, 100, 90, BOS),
            ("g7", s - 1, f"{s - 1}-03-01T20:00Z", GS, DET, 110, 90, GS),
        ],
    )
    # 22 games apiece for the two ranking-only players, so the league read
    # clears PER_GAME_MIN_GAMES (20) - nothing else in this fixture does.
    extra_games = [(f"x{n}", s, (date(s - 1, 11, 5) + timedelta(days=2 * n)).isoformat() + "T20:00Z", LAL, DET, 100, 90, LAL) for n in range(22)]
    c.executemany("INSERT INTO games VALUES (?, ?, 2, ?, ?, ?, ?, ?, ?)", extra_games)
    c.execute(f"CREATE TABLE player_box_stats ({_BOX_COLUMNS})")
    c.executemany(
        f"INSERT INTO player_box_stats VALUES ({', '.join('?' for _ in range(24))})",
        [
            _box("g1", s, GS, BOS, PODZ, minutes=30, pts=20, reb=5, ast=3),
            _box("g2", s, GS, DET, PODZ, minutes=28, pts=15, reb=4, ast=6),
            _box("g3", s, GS, DET, PODZ, minutes=35, pts=28, reb=10, ast=11, fga=20, fta=6, pm=17),
            _box("g4", s, GS, LAL, PODZ, dnp=True),
            _box("g5", s, GS, BOS, PODZ, minutes=22, pts=10, reb=3, ast=2),
            _box("g6", s - 1, GS, BOS, PODZ, minutes=25, pts=12, reb=4, ast=3),
            _box("g7", s - 1, GS, DET, PODZ, minutes=29, pts=18, reb=5, ast=4),
            _box("g1", s, GS, BOS, CURRY, minutes=34, pts=35, reb=4, ast=6),
            _box("g2", s, GS, DET, CURRY, dnp=True),
            _box("g3", s, GS, DET, CURRY, minutes=36, pts=40, reb=5, ast=8),
            _box("g4", s, GS, LAL, CURRY, minutes=33, pts=25, reb=3, ast=9),
            _box("g5", s, GS, BOS, CURRY, minutes=30, pts=22, reb=4, ast=5),
            _box("g1", s, BOS, GS, BROWN, minutes=32, pts=28, reb=6, ast=3),
            _box("g5", s, BOS, GS, BROWN, minutes=34, pts=31, reb=7, ast=4),
            _box("g6", s - 1, BOS, GS, BROWN, minutes=31, pts=26, reb=5, ast=3),
            _box("g2", s, DET, GS, SABONIS, minutes=30, pts=18, reb=12, ast=5),
            _box("g3", s, DET, GS, SABONIS, minutes=28, pts=14, reb=10, ast=3),
            _box("g7", s - 1, DET, GS, SABONIS, minutes=32, pts=20, reb=14, ast=6),
            *(_box(f"x{n}", s, LAL, DET, "90", minutes=20, pts=10, reb=3, ast=2) for n in range(22)),
            *(_box(f"x{n}", s, LAL, DET, "91", minutes=34, pts=30, reb=6, ast=4) for n in range(22)),
        ],
    )
    c.execute(
        "CREATE VIEW player_game_log AS SELECT pbs.*, p.display_name AS player_name, g.date AS game_date, t.abbreviation AS team_abbr, o.abbreviation AS opponent_abbr "
        "FROM player_box_stats pbs LEFT JOIN players p ON p.athlete_id = pbs.athlete_id LEFT JOIN games g ON g.event_id = pbs.event_id AND g.season = pbs.season "
        "LEFT JOIN teams t ON t.team_id = pbs.team_id LEFT JOIN teams o ON o.team_id = pbs.opponent_team_id"
    )
    # `_box_score_notes` (threaded through `core.run` for #197's box-score-
    # caveat half) reads this for its career-floor note - MIN(season) with a
    # game played, per player. One row per player per season here keeps that
    # note quiet (earliest on record is never before the real 1994 floor,
    # which is fixed in `nba.coverage` and not derived from this fixture);
    # `test_a_career_predating_box_scores_gets_the_floor_note` adds an older
    # row of its own to turn the note on.
    c.execute("CREATE TABLE player_season_stats_deduped (athlete_id VARCHAR, season INTEGER, season_type INTEGER, gamesPlayed INTEGER)")
    c.executemany(
        "INSERT INTO player_season_stats_deduped VALUES (?, ?, 2, 1)",
        [(pid, season) for pid in (PODZ, CURRY, BROWN, SABONIS, "90", "91") for season in (s - 1, s)],
    )
    # `_player_own_seasons` (`compose.core`) reuses `templates.players._seasons_on_record`,
    # which reads the RAW table `player_season_stats_deduped` is a view over
    # in the real warehouse - so a plain career sentence can name a player's
    # own first and last season instead of the relation's floor (ISSUES.md,
    # "The compiler's career span says '(1994 on)' ..."). Same rows as the
    # deduped table above: this fixture has no postseason-copy row for either
    # to disagree about.
    c.execute("CREATE TABLE player_season_stats (athlete_id VARCHAR, season INTEGER, season_type INTEGER, team_id VARCHAR, gamesPlayed INTEGER, points INTEGER)")
    c.executemany(
        "INSERT INTO player_season_stats VALUES (?, ?, 2, NULL, 1, NULL)",
        [(pid, season) for pid in (PODZ, CURRY, BROWN, SABONIS, "90", "91") for season in (s - 1, s)],
    )
    return TemplateContext(con=c, out_dir=tmp_path)


def _run(con: duckdb.DuckDBPyConnection, q: Query) -> dict[str, Any]:
    return run(con, q)


# ---------------------------------------------------------------------------
# One test per skeleton.
# ---------------------------------------------------------------------------


def test_rows_skeleton_reads_a_log(cx_ctx: TemplateContext) -> None:
    """``game_log``'s point: the four-stat line, newest games first."""
    q = to_query("game_log", {"player": "Brandin Podziemski"})
    out = _run(cx_ctx.con, q)
    assert [r["points"] for r in out["rows"]] == [10, 28, 15, 20]  # g5, g3, g2, g1 - newest first, g4 (DNP) excluded


def test_scalar_skeleton_reads_a_count_or_an_average(cx_ctx: TemplateContext) -> None:
    """``threshold_count``'s point: a scalar count over the narrowed games."""
    q = to_query("threshold_count", {"player": "Brandin Podziemski", "stat": "points", "threshold": 15})
    out = _run(cx_ctx.con, q)
    assert out["rows"][0]["games"] == 3  # g1 (20), g2 (15), g3 (28) - g5 (10) does not clear the line


def test_grouped_skeleton_reads_a_split(cx_ctx: TemplateContext) -> None:
    """``player_splits``' point: a record grouped by venue."""
    q = to_query("player_splits", {"player": "Brandin Podziemski"})
    out = _run(cx_ctx.con, q)
    by_group = {r["group"]: r["games"] for r in out["rows"]}
    assert by_group == {"home": 3, "away": 1}  # g2, g3, g5 home; g1 away (g4 DNP excluded)


# ---------------------------------------------------------------------------
# K1 rules a fixture can exercise.
# ---------------------------------------------------------------------------


def test_a_bare_limit_is_filler_on_a_count_but_the_newest_n_on_a_log(cx_ctx: TemplateContext) -> None:
    """Rule 1: ``limit`` with no ``order`` means "the newest N" on a rows
    read and is dropped as filler everywhere else - a count reads every
    game in the span."""
    counted = _run(cx_ctx.con, to_query("threshold_count", {"player": "Brandin Podziemski", "stat": "points", "threshold": 15, "limit": 1}))
    assert counted["rows"][0]["games"] == 3  # the limit=1 filler does not cut this to one game
    logged = _run(cx_ctx.con, to_query("game_log", {"player": "Brandin Podziemski", "limit": 2}))
    assert [r["points"] for r in logged["rows"]] == [10, 28]  # g5, g3 - the newest two


def test_a_real_limit_with_no_order_is_refused_on_a_split(cx_ctx: TemplateContext) -> None:
    """Rule 1's mirror image: a real limit (>1) with no ``order`` on a split
    is "his last N games" - a window ``player_splits`` refuses rather than
    silently answering for the whole span."""
    q = to_query("player_splits", {"player": "Brandin Podziemski", "limit": 5})
    with pytest.raises(Unsupported, match="game_log's question"):
        compile_query(cx_ctx.con, q)


def test_a_team_beside_the_player_becomes_his_opponent_on_a_log(cx_ctx: TemplateContext) -> None:
    """Rule 3: on a ``rows`` read with no opponent already named, a team
    beside the player becomes his opponent."""
    q = to_query("game_log", {"player": "Brandin Podziemski", "team": "Boston Celtics"})
    out = _run(cx_ctx.con, q)
    assert [r["points"] for r in out["rows"]] == [10, 20]  # g5, g1 - his two games against Boston, newest first


def test_a_team_slot_narrows_to_his_games_for_that_team_on_a_condition_skeleton(cx_ctx: TemplateContext) -> None:
    """Rule 3: on a condition skeleton (count, record, grouped) a team beside
    the player narrows to his games for that team - a no-op here, since he
    never played for anyone else, but the same code path a traded player's
    question would take."""
    with_team = _run(cx_ctx.con, to_query("threshold_count", {"player": "Brandin Podziemski", "stat": "points", "threshold": 15, "team": "Golden State Warriors"}))
    without_team = _run(cx_ctx.con, to_query("threshold_count", {"player": "Brandin Podziemski", "stat": "points", "threshold": 15}))
    assert with_team["rows"][0]["games"] == without_team["rows"][0]["games"] == 3


def test_a_team_slot_is_ignored_entirely_on_a_per_game_average(cx_ctx: TemplateContext) -> None:
    """Rule 3, the case the real ``player_stat`` template also takes: a
    per-game average reads no ``team`` slot at all. Before this was fixed,
    a team the player never played for (as the router routinely invents
    beside a correct opponent) silently narrowed his own averages to zero
    games instead of being ignored."""
    q = to_query("player_stat", {"player": "Brandin Podziemski", "opponent": "Boston Celtics", "team": "Detroit Pistons"})
    out = _run(cx_ctx.con, q)
    row = out["rows"][0]
    assert row["games"] == 2  # g1 and g5, vs Boston - the bogus Pistons team narrows nothing
    assert row["points"] == pytest.approx(15.0)  # (20 + 10) / 2


def test_situation_narrows_to_the_named_weekday(cx_ctx: TemplateContext) -> None:
    """``situation`` is a cell of the relation: a weekday narrows the games
    to the ones played on it, over the whole career."""
    s = current_season()
    games = {"g1": date(s - 1, 11, 1), "g2": date(s - 1, 12, 1), "g3": date(s, 1, 10), "g5": date(s, 3, 1), "g6": date(s - 1, 2, 1), "g7": date(s - 1, 3, 1)}
    weekday = games["g3"].strftime("%A").lower()
    matching = {event for event, day in games.items() if day.strftime("%A").lower() == weekday}
    q = to_query("game_log", {"player": "Brandin Podziemski", "span": "career", "situation": f"{weekday}s"})
    out = _run(cx_ctx.con, q)
    assert len(out["rows"]) == len(matching)
    assert out["rows"][0]["points"] == 28  # g3 is always in the match set (it defines the weekday)


def test_starter_bench_category_is_refused_outside_a_grouped_read(cx_ctx: TemplateContext) -> None:
    """Rule 9: ``split: starter_bench`` names a category (a table of both
    halves), not a filter - refused on a ``rows``/``scalar`` read, honored
    only by a grouped read by starter."""
    q = to_query("game_log", {"player": "Brandin Podziemski", "split": "starter_bench"})
    with pytest.raises(Unsupported, match="a table of both halves"):
        compile_query(cx_ctx.con, q)


def test_an_unhonored_scoping_slot_is_refused(cx_ctx: TemplateContext) -> None:
    """Rule 7: a scoping slot the relation cannot narrow by (``round``) is
    refused rather than silently dropped - by the planner, as the point is
    planned (``to_query`` plans); the compile step keeps the same check for
    a query built by hand."""
    with pytest.raises(Unsupported, match="cannot honor"):
        to_query("game_log", {"player": "Brandin Podziemski", "round": "finals"})


# ---------------------------------------------------------------------------
# K2 moves.
# ---------------------------------------------------------------------------


def test_a_measure_word_the_router_did_not_name_moves_the_point(cx_ctx: TemplateContext) -> None:
    """A measure word in the question ("plus-minus") is added to a log's
    line even though the router's own ``stat`` slot said nothing about it."""
    q = move_point(cx_ctx.con, "game_log", {"player": "Brandin Podziemski"}, "Podziemski's plus-minus each game this season")
    assert isinstance(q, Query)  # a named player never returns a TeamQuery
    out = _run(cx_ctx.con, q)
    g3 = next(r for r in out["rows"] if r["points"] == 28)
    assert g3["plusMinus"] == 17


def test_most_in_a_game_moves_to_rows_by_measure(cx_ctx: TemplateContext) -> None:
    """ "Career-high" moves a ``player_stat``-shaped point to rows ordered by
    the named measure - the top game(s), not an average."""
    q = move_point(cx_ctx.con, "player_stat", {"player": "Brandin Podziemski", "stat": "points"}, "Podziemski's career-high in points")
    assert isinstance(q, Query)  # a named player never returns a TeamQuery
    assert q.skeleton == "rows" and q.order == "measure"
    out = _run(cx_ctx.con, q)
    assert out["rows"][0]["points"] == 28  # g3, his career high


def test_how_many_won_is_a_career_count_with_a_predicate(cx_ctx: TemplateContext) -> None:
    """ "How many ... has he won" moves to a career count with the ``won``
    predicate, whatever season was (or was not) named - route()'s own
    unscoped-count-is-career rule, read here in code."""
    q = move_point(cx_ctx.con, "record_when", {"player": "Brandin Podziemski"}, "how many games has Podziemski's team won?")
    assert isinstance(q, Query)  # a named player never returns a TeamQuery
    assert q.scope.span == "career"  # Query.span (a binding-parity override) is unset; the scope itself carries it
    out = _run(cx_ctx.con, q)
    assert out["rows"][0]["games"] == 4  # g1, g3, g5, g7 - every win across both seasons (g4 DNP excluded)


def test_a_boolean_measure_on_a_per_game_intent_is_counted_never_averaged(cx_ctx: TemplateContext) -> None:
    """yardstick-v2 F055 "alperen sengun double-doubles vs southeast division
    career away" routes ``player_splits``, whose figure is a per-game line;
    a double-double is a condition, so its only figure is the count. Before
    this the compiler built ``AVG(<boolean>)`` and DuckDB threw - a crash,
    the one shape worse than a wrong answer - so ``_agg`` refuses the
    average outright, whatever move asked for it."""
    from association.query.compose.core import Unsupported, _agg

    slots = {"player": "Brandin Podziemski", "stat": "double_double", "venue": "away", "span": "career"}
    q = move_point(cx_ctx.con, "player_splits", dict(slots), "Podziemski double-doubles career away")
    assert isinstance(q, Query) and q.skeleton == "scalar" and q.aggregate == "count" and ("double_double", "=", True) in q.predicates
    result = compose_answer(cx_ctx, "player_splits", dict(slots), "Podziemski double-doubles career away")
    assert result is not None and "double-double" in result.answer
    with pytest.raises(Unsupported):
        _agg("double_double", "per_game")
    with pytest.raises(Unsupported):
        _agg("triple_double", "total")


def test_career_scope_forces_a_career_span_only_when_nothing_else_scoped_it() -> None:
    """The helper ``_move_how_many_won``/``_move_boolean_count`` share: no
    season, span or since means "his career"; any of the three left alone."""
    assert _career_scope(Scope(player="X")) == Scope(player="X", span="career")
    assert _career_scope(Scope(player="X", season=2024)) == Scope(player="X", season=2024)
    assert _career_scope(Scope(player="X", span="career")) == Scope(player="X", span="career")
    assert _career_scope(Scope(player="X", since=2020)) == Scope(player="X", since=2020)


def test_a_ranking_word_with_no_player_groups_by_player_league_wide(cx_ctx: TemplateContext) -> None:
    """A ranking word with no player subject reads the league-wide grouped
    point - the leaderboard shape a template refuses because no player was
    named."""
    q = move_point(cx_ctx.con, "other", {}, "top scorers this season")
    assert isinstance(q, Query)  # no team named in this fixture's own words
    assert q.subject == "everyone" and q.skeleton == "grouped" and q.group == "player"
    out = _run(cx_ctx.con, q)
    assert out["rows"][0]["group"] == "Derek Vollmer"
    assert out["rows"][0]["points"] == pytest.approx(30.0)


def test_a_position_word_with_no_player_reads_that_positions_log(cx_ctx: TemplateContext) -> None:
    """A position word with no player subject reads that group's log, rows -
    not a ranking, since no ranking word was asked for."""
    q = move_point(cx_ctx.con, "other", {}, "centers game log this season")
    assert isinstance(q, Query)  # no team named in this fixture's own words
    assert q.subject == "everyone" and q.skeleton == "rows" and q.position == "C"
    out = _run(cx_ctx.con, q)
    assert {r["points"] for r in out["rows"]} == {18, 14}  # Sabonis - the only center on record


def test_the_questions_own_number_names_its_column_not_the_routers_stat(cx_ctx: TemplateContext) -> None:
    """A threshold's own words ("30 point") name its column, even where the
    router's ``stat`` slot names a different one entirely."""
    q = move_point(cx_ctx.con, "threshold_count", {"threshold": 30, "stat": "rebounds"}, "players with a 30 point game this season")
    assert isinstance(q, Query)  # no team named in this fixture's own words
    assert q.predicates == [("points", ">=", 30)]
    out = _run(cx_ctx.con, q)
    assert out["rows"][0]["group"] == "Derek Vollmer"
    assert out["rows"][0]["games"] == 22


# ---------------------------------------------------------------------------
# The two K2 guards.
# ---------------------------------------------------------------------------


def test_a_team_subject_question_is_refused_not_ranked(cx_ctx: TemplateContext) -> None:
    """A period ("first half") is not this relation's question - refused,
    never answered with a ranking of players over the whole game."""
    with pytest.raises(Unsupported, match="period relation"):
        move_point(cx_ctx.con, "other", {}, "least points scored by the Warriors in the first half this season")


def test_an_opponents_or_allowed_figure_is_refused_not_ranked(cx_ctx: TemplateContext) -> None:
    """A team's own figure - "allowed", "by team" - is the team relation's
    question; refused rather than answered as a ranking of players, which
    silently substituted a different question until this guard existed."""
    with pytest.raises(Unsupported, match="team relation"):
        move_point(cx_ctx.con, "other", {}, "most opponent bench points allowed by team this season")


# ---------------------------------------------------------------------------
# Refused vs. Unsupported, and answer().
# ---------------------------------------------------------------------------


def test_refused_carries_the_relations_own_wording(cx_ctx: TemplateContext) -> None:
    """A near miss the index will not settle alone ("Jemel Podziemski": a
    given name that is nobody's, beside one player's surname) is a handled
    refusal - the relation's own suggestion - not a bare "cannot answer": it
    comes back as :class:`Refused`, carrying the template-shaped result. A
    plain typo of one player ("Podzemski") is no longer this shape: it is read
    as him (entities.read_near_spelling)."""
    q = to_query("game_log", {"player": "Jemel Podziemski"})
    with pytest.raises(Refused) as excinfo:
        run(cx_ctx.con, q)
    assert "podziemski" in excinfo.value.result.answer.lower()


def test_a_name_nothing_resolves_to_is_unsupported_not_refused(cx_ctx: TemplateContext) -> None:
    """A name with no near match at all (:func:`~association.query.entities.suggest_players`
    finds nothing) is the compiler declining outright - ``Unsupported``, which
    the agent may still do better with, not a handled ``Refused``."""
    q = to_query("game_log", {"player": "Zzyzx Nobody"})
    with pytest.raises(Unsupported):
        run(cx_ctx.con, q)


def test_answer_returns_none_for_a_question_the_compiler_cannot_say(cx_ctx: TemplateContext) -> None:
    """``answer()`` reports "not a point on this relation" as ``None`` -
    which is a fall-through to the agent, not a refusal."""
    assert compose_answer(cx_ctx, "game_log", {}, "some team's log") is None


def test_answer_composes_a_sentence_and_the_point_it_rests_on(cx_ctx: TemplateContext) -> None:
    """``answer()``'s ``TemplateResult`` carries a sentence and the point's
    own values - what a caller checks an answer against. The question's
    "plus-minus" moves the point off ``player_stat``'s own line, so this is
    the compiler's sentence rather than the template's (``compose.present``,
    step 2a - ``test_an_intents_own_point_reads_as_its_template``)."""
    result = compose_answer(cx_ctx, "player_stat", {"player": "Brandin Podziemski", "opponent": "Boston Celtics"}, "Podziemski's plus-minus vs Boston")
    assert result is not None
    assert "Brandin Podziemski" in result.answer
    assert result.data["skeleton"] == "scalar"
    assert result.data["player"] == "Brandin Podziemski"
    assert result.data["rows"][0]["games"] == 2
    assert result.artifacts == []


def test_a_closed_range_is_named_as_one_and_counted_as_one(cx_ctx: TemplateContext) -> None:
    """``since``/``until`` bound the read at both ends, and the sentence says
    the range it counted - "(2025-2026)", not "(2025 on)" - so the stated
    scope matches the number (yardstick-v2 F036, where a 2024-2026 count read
    "2024 on"). One season named at both ends is named once."""
    s = current_season()
    # "PRA" moves the point off player_stat's own line, so the compiler's
    # own span phrase is what answers (compose.present, step 2a).
    both = compose_answer(cx_ctx, "player_stat", {"player": "Brandin Podziemski", "stat": "points", "since": s - 1, "until": s}, "Podziemski's PRA")
    assert both is not None
    assert f"({s - 1}-{s})" in both.answer and "on)" not in both.answer
    assert both.data["rows"][0]["games"] == 6
    one = compose_answer(cx_ctx, "player_stat", {"player": "Brandin Podziemski", "stat": "points", "since": s - 1, "until": s - 1}, "Podziemski's PRA")
    assert one is not None
    assert f"career ({s - 1})" in one.answer
    assert one.data["rows"][0]["games"] == 2


def test_an_absence_is_read_on_the_other_side_where_the_man_was_never_a_teammate(cx_ctx: TemplateContext) -> None:
    """ROADMAP step 3 (Jeff's call, 2026-09-30): "without X" is the games X
    missed on the subject's team - or, where X was never his teammate in
    the span and the games are narrowed to an opponent X played for, the
    games X missed on the OTHER side, settled where the name is resolved
    (``common._absence_condition``) and said so. Jaylen Brown (Boston)
    played both of Podziemski's season-``s`` Boston games (g1, g5); a third
    Boston game he sat out is added, and it is the one game "without Jaylen
    Brown on the other side". A player on neither side keeps the own-side
    reading, whose empty answer names him as never the teammate."""
    s = current_season()
    cx_ctx.con.execute("INSERT INTO games VALUES (?, ?, 2, ?, ?, ?, ?, ?, ?)", ["g9", s, f"{s}-03-15T20:00Z", BOS, GS, 90, 101, GS])
    cx_ctx.con.execute(f"INSERT INTO player_box_stats VALUES ({', '.join('?' for _ in range(24))})", _box("g9", s, GS, BOS, PODZ, minutes=31, pts=24, reb=6, ast=5))
    result = compose_answer(
        cx_ctx, "game_log", {"player": "Brandin Podziemski", "opponent": "Boston", "without": ["Jaylen Brown"], "season": s, "season_type": 2}, "podziemski game log vs boston without jaylen brown"
    )
    assert result is not None and [g["date"] for g in result.data["games"]] == [f"{s}-03-15"]
    assert "vs the Boston Celtics without Jaylen Brown on the other side" in (result.answer or "")
    # A man on neither side: the own-side reading, and the honest empty answer.
    neither = compose_answer(
        cx_ctx, "game_log", {"player": "Brandin Podziemski", "opponent": "Boston", "without": ["Domantas Sabonis"], "season": s, "season_type": 2}, "podziemski game log vs boston without sabonis"
    )
    assert neither is not None and "Domantas Sabonis was not Brandin Podziemski's teammate" in (neither.answer or "")


def test_an_empty_splits_read_names_the_narrowing_not_a_phantom_did_not_play(cx_ctx: TemplateContext) -> None:
    """ "steph curry record vs lebron" with no meeting this season said
    "listed in 43 box scores but did not play in any of them" - a confident
    refusal naming the wrong missing fact. Podziemski's one Lakers game
    (g4) is a DNP, so his splits against them are empty, and the answer
    says that it is the narrowing that emptied them."""
    s = current_season()
    result = compose_answer(cx_ctx, "player_splits", {"player": "Brandin Podziemski", "opponent": "Lakers", "season": s, "season_type": 2}, "podziemski record vs lakers")
    assert result is not None and result.data["games"] == 0
    assert "none of them vs the Los Angeles Lakers" in (result.answer or "") and "did not play in any" not in (result.answer or "")


def test_a_plain_career_names_the_players_own_seasons_not_the_floor(cx_ctx: TemplateContext) -> None:
    """A career with no ``since``/``until`` named used to read the relation's
    floor - "(1994 on)", true of every player and naming nothing about the
    one asked about (ISSUES.md, "The compiler's career span says '(1994 on)'
    where the template named the player's own seasons" - yardstick-v2 F061,
    "Sga games with under 14 fta in his whole career", where ``threshold_count``
    said "(2018-19 through 2025-26)" for the same 479 games and the compiler
    said "(1994 on)"). A named player's own first and last season on record
    (:func:`~association.query.compose.core._player_own_seasons`, reusing
    :func:`~association.query.templates.players._seasons_on_record` - the
    same read ``threshold_count``/``single_game_high`` already make through
    ``_game_span``) now names the career instead; the fixture's players are
    on record for seasons s-1 and s (``cx_ctx``'s ``player_season_stats``
    rows). A league-wide career - no one player's seasons to substitute -
    still reads the floor, so ``player_seasons`` stays unset for it."""
    s = current_season()
    # A count against an opponent - a slot threshold_count's template does
    # not take - so the compiler's own sentence names the span (step 2a:
    # the template's own point reads in the template's words instead).
    result = compose_answer(
        cx_ctx,
        "threshold_count",
        {"player": "Brandin Podziemski", "stat": "points", "threshold": 15, "span": "career", "opponent": "Detroit Pistons"},
        "Podziemski's 15+ point games vs Detroit in his career",
    )
    assert result is not None
    assert f"({s - 1}-{s})" in result.answer
    assert "1994" not in result.answer
    # Against Detroit: g2 (15), g3 (28) and g7 (18) all clear 15 (see the
    # `_box` inserts above).
    assert result.data["rows"][0]["games"] == 3
    assert result.data["span"] == f"regular season career ({s - 1}-{s})"

    q = move_point(cx_ctx.con, "threshold_count", {"threshold": 10, "stat": "rebounds", "span": "career"}, "players with 10 points career")
    assert isinstance(q, Query) and q.subject == "everyone"
    out = run(cx_ctx.con, q)
    assert out["player_seasons"] is None


def test_a_refusal_from_answer_is_the_relations_own(cx_ctx: TemplateContext) -> None:
    """A near-miss name is a handled refusal from ``answer()``, not ``None``."""
    result = compose_answer(cx_ctx, "game_log", {"player": "Podzemski"}, "Podzemski's last 5 games")
    assert result is not None
    assert re.search(r"podziemski", result.answer, re.I)


# ---------------------------------------------------------------------------
# The team as a subject (step 3, K1) - compose/team.py, and team_move_point
# in move.py. A separate small fixture: the team-games relation reads FROM
# ``real_games`` (not ``games`` directly), which ``cx_ctx`` above never
# builds - team_games.py's own module docstring says why ``real_games`` is
# the shared filtered list, and calling ``real_games.build_table`` is what
# every other team fixture in this repo (``team_ctx``, ``team_cells_con``)
# does for the same reason.
# ---------------------------------------------------------------------------

_TS = current_season()


@pytest.fixture
def team_cx_ctx(tmp_path: Path) -> TemplateContext:
    """The Magic (season total, plus a finished postseason for the addendum)
    and the Raptors (five regular-season games, for a narrowed
    total/differential window). ``players`` is empty but present, since
    ``move_point`` (unlike ``team_move_point`` alone) reads the subject,
    which reads it via ``players_named_in`` for every question."""
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR, position_abbr VARCHAR)")
    c.execute("CREATE TABLE teams (team_id VARCHAR, abbreviation VARCHAR, display_name VARCHAR)")
    c.execute("INSERT INTO teams VALUES ('1','ORL','Orlando Magic'),('2','CLE','Cleveland Cavaliers'),('3','TOR','Toronto Raptors'),('4','LAL','Los Angeles Lakers')")
    c.execute(
        "CREATE TABLE games (event_id VARCHAR, season INTEGER, season_type INTEGER, date VARCHAR, home_team_id VARCHAR, away_team_id VARCHAR, "
        "home_score INTEGER, away_score INTEGER, winner_team_id VARCHAR, neutral_site BOOLEAN, venue_city VARCHAR)"
    )
    c.executemany(
        "INSERT INTO games VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            # Orlando: one regular-season and one postseason game, just to
            # prove the relation reads real games at all - the season TOTAL
            # itself comes from team_season_stats, not these rows.
            ("m1", _TS, 2, f"{_TS - 1}-11-01T23:00Z", "1", "2", 110, 100, "1", False, "Orlando"),
            ("m2", _TS, 3, f"{_TS}-04-20T23:00Z", "1", "2", 105, 100, "1", False, "Orlando"),
            # Toronto's last 5 regular-season games, oldest to newest.
            ("r1", _TS, 2, f"{_TS - 1}-11-10T23:00Z", "3", "2", 100, 90, "3", False, "Toronto"),
            ("r2", _TS, 2, f"{_TS - 1}-11-15T23:00Z", "2", "3", 95, 105, "3", False, "Cleveland"),
            ("r3", _TS, 2, f"{_TS}-01-05T23:00Z", "3", "2", 110, 120, "2", False, "Toronto"),
            ("r4", _TS, 2, f"{_TS}-01-10T23:00Z", "2", "3", 100, 90, "3", False, "Cleveland"),
            ("r5", _TS, 2, f"{_TS}-01-15T23:00Z", "3", "2", 115, 108, "3", False, "Toronto"),
        ],
    )
    real_games.build_table(c, {"games", "teams"})
    c.execute("CREATE TABLE team_season_stats (season INTEGER, season_type INTEGER, team_id VARCHAR, gamesPlayed INTEGER, points INTEGER, threePointFieldGoalsMade INTEGER)")
    c.executemany(
        "INSERT INTO team_season_stats VALUES (?, ?, ?, ?, ?, ?)",
        [
            (_TS, 2, "1", 82, 9200, 961),  # Orlando's regular-season total
            (_TS, 3, "1", 7, 780, 78),  # Orlando's finished playoff run
        ],
    )
    return TemplateContext(con=c, out_dir=tmp_path)


def test_team_move_point_reads_an_unnarrowed_season_total(team_cx_ctx: TemplateContext) -> None:
    """F127's shape: "how many 3 pointers have the magic made" - a plain
    season total from ``team_season_stats``, not a per-game average, and the
    finished postseason is named too rather than left unmentioned."""
    q = team_move_point(team_cx_ctx.con, {"stat": "threePointFieldGoalsMade", "team": "Orlando Magic"}, "how many 3 pointers have the magic made so far this season")
    assert isinstance(q, TeamQuery)
    result = run_team(team_cx_ctx.con, q)
    assert result.value == 961
    assert result.games == 82
    assert result.from_season_line
    assert "78" in result.note and "playoff" in result.note


def test_team_move_point_finds_a_team_the_router_dropped(team_cx_ctx: TemplateContext) -> None:
    """The router filed no ``team`` slot at all for this exact question, live
    (ISSUES.md) - ``team_named_in`` reads "magic" from the text itself, the
    same repair :func:`association.query.entities.players_named_in` already
    makes for a dropped PLAYER."""
    q = team_move_point(team_cx_ctx.con, {"stat": "threePointFieldGoalsMade"}, "how many 3 pointers have the magic made so far this season")
    assert isinstance(q, TeamQuery)
    assert q.scope.team == "Orlando Magic"


def test_team_move_point_is_not_fooled_by_magic_johnson(team_cx_ctx: TemplateContext) -> None:
    """ "Magic" is also Magic Johnson's given name - ``players_named_in``
    finds him from this exact question text, and ``repair``'s dropped-subject
    restoration would turn this into a question about him UNLESS the team
    reading is tried first (``move_point``'s own ordering, not just this
    function's)."""
    q = move_point(team_cx_ctx.con, "leaderboard", {"stat": "threePointFieldGoalsMade", "season_type": 2}, "how many 3 pointers have the magic made so far this season")
    assert isinstance(q, TeamQuery)
    assert q.scope.player is None


def test_team_move_point_reads_a_narrowed_total(team_cx_ctx: TemplateContext) -> None:
    """F128's shape (one season type - the mixed-type "last N games" reader
    is ``game_log``'s own ``_team_game_log_mixed``, not this module's): from
    Toronto's own side, their last 3 regular-season games are r3 (110, a
    home loss to Cleveland), r4 (90, an away win) and r5 (115, a home win) -
    315 points, not the season's."""
    q = team_move_point(team_cx_ctx.con, {"stat": "points", "team": "Toronto Raptors", "order": "recent", "limit": 3, "season_type": 2}, "total points scored by the raptors in their last 3 games")
    assert isinstance(q, TeamQuery)
    result = run_team(team_cx_ctx.con, q)
    assert result.value == 315
    assert result.games == 3
    assert not result.from_season_line


def test_a_teams_quarter_is_never_read_as_its_season_line(team_cx_ctx: TemplateContext) -> None:
    """A quarter or half narrows the team's games (the period relation's
    team half): "the magic's first-quarter threes" read as unnarrowed would
    answer their season's 961 - a whole-game total under a quarter's
    question. It goes to the narrowed reader, which sums game-level columns
    only, and declines a box-score count rather than answering the season."""
    for period in ({"period": 1}, {"half": 2}):
        q = TeamQuery(scope=Scope.from_slots({"team": "Orlando Magic", "season_type": 2, **period}), measure="threePointFieldGoalsMade")
        with pytest.raises(Unsupported):
            run_team(team_cx_ctx.con, q)


def test_team_move_point_reads_a_narrowed_differential(team_cx_ctx: TemplateContext) -> None:
    """F129's shape: Toronto's last 3 games (r3 110-120 L, r4 90-100 W, r5
    115-108 W) sum to a -13 differential (-10 -10 +7), not the season's."""
    q = team_move_point(team_cx_ctx.con, {"stat": "pointsDifference", "team": "Toronto Raptors", "order": "recent", "limit": 3, "season_type": 2}, "raptors point differential over their last 3 games")
    assert isinstance(q, TeamQuery)
    result = run_team(team_cx_ctx.con, q)
    assert result.value == -13
    assert result.wins == 2 and result.losses == 1


def test_team_move_point_never_hijacks_a_player_ranking(team_cx_ctx: TemplateContext) -> None:
    """ "Who leads the Lakers in scoring" narrows a PLAYER ranking by team -
    it is not the team's own subject, and must fall through to the
    league-wide reading (``_everyone_point``'s own question) unchanged."""
    assert team_move_point(team_cx_ctx.con, {"stat": "points", "team": "Los Angeles Lakers"}, "who leads the lakers in scoring") is None


def test_team_move_point_never_hijacks_a_named_player(team_cx_ctx: TemplateContext) -> None:
    """A player already named makes the team a narrowing of him, never the
    subject - ``move_point``'s own player path, untouched."""
    assert team_move_point(team_cx_ctx.con, {"stat": "points", "team": "Orlando Magic", "player": "Paolo Banchero"}, "how many points has banchero scored for the magic") is None


def test_team_move_point_ignores_the_routers_any_team_placeholder(team_cx_ctx: TemplateContext) -> None:
    """K2's own corpus: "rebounds allowed per team" files ``team: "any_team"``
    - a router placeholder, not a name, that ``_resolved_team`` RAISES for
    rather than returning a clarification. Treating it as a real team to
    resolve only delayed the exact decline ``_everyone_point``'s own
    ``_NOT_PLAYERS`` guard already gives this question ("allowed"), through a
    noisier path - caught by re-running ``k2_run_pkg.py`` against this
    package, which crashed on it (``'TeamQuery' object has no attribute
    'subject'``) before this guard existed."""
    q = team_move_point(team_cx_ctx.con, {"stat": "rebounds", "team": "any_team", "season_type": 2}, "rebounds allowed per team")
    assert q is None
    with pytest.raises(Unsupported, match="team relation"):
        move_point(team_cx_ctx.con, "team_stat", {"stat": "rebounds", "team": "any_team", "season_type": 2}, "rebounds allowed per team")


def test_a_box_stat_measure_narrowed_to_a_window_is_unsupported(team_cx_ctx: TemplateContext) -> None:
    """A box-score count (3-pointers made, not a game-outcome figure) narrowed
    to a window needs a join the team-games relation does not have yet
    (ISSUES.md) - refused rather than silently answering the season instead."""
    q = team_move_point(
        team_cx_ctx.con, {"stat": "threePointFieldGoalsMade", "team": "Toronto Raptors", "order": "recent", "limit": 3, "season_type": 2}, "3 pointers made by the raptors in their last 3 games"
    )
    assert isinstance(q, TeamQuery)
    with pytest.raises(Unsupported):
        run_team(team_cx_ctx.con, q)


def test_a_team_point_refuses_a_cell_only_a_players_games_carry() -> None:
    """ISSUES.md P1 (2026-09-30): the planner checked a team point against
    the PLAYER relation's cells, so one only a player's games carry passed
    and the team relation, which has no such cell, ignored it - "raptors
    game log in games where they made 5 threes in the first quarter" listed
    every game, and a team SUM was not checked at all. A team point is held
    to the team relation's own cells now, every shape, as an allow list: a
    cell built on one relation is refused on the other until it is built
    there. The cells a team's reader refuses with its own sentence (a
    teammate's absence is with_without's question) still reach that
    reader, and the team relation's own cells pass."""
    from association.query.reading import PeriodCondition, Reading

    def team_point(shape: str, **cells: Any) -> Reading:
        aggregate = "none" if shape in ("rows", "run") else ("record" if shape == "grouped" else "total")
        return Reading(relation="team", shape=shape, measures=["points"], aggregate=aggregate, group="venue" if shape == "grouped" else "none", scope=Scope(team="Orlando Magic", **cells))  # type: ignore[arg-type]

    line = PeriodCondition(stat="threePointFieldGoalsMade", threshold=5, period=1)
    refused: list[tuple[str, dict[str, Any], str]] = [
        ("rows", {"period_condition": line}, "period_condition"),
        ("scalar", {"period_condition": line}, "period_condition"),
        ("rows", {"split": "starter"}, "split"),
        ("scalar", {"without": ("Paolo Banchero",)}, "without"),
        ("scalar", {"below": ("14 fta",)}, "below"),
        ("scalar", {"season_n": 3}, "season_n"),
        ("run", {"period_condition": line}, "period_condition"),
    ]
    for shape, cells, named in refused:
        with pytest.raises(Unsupported, match=rf"a team's games cannot be narrowed by .*{named}"):
            plan(team_point(shape, **cells))
    passes: list[tuple[str, dict[str, Any]]] = [
        ("scalar", {"opponent": "Boston", "venue": "home", "order": "recent", "limit": 10}),
        ("rows", {"since": 2022, "until": 2024}),
        ("grouped", {"split": "home_away"}),
        ("rows", {"without": ("Paolo Banchero",)}),  # the log's own reader says where that question belongs
    ]
    for shape, cells in passes:
        assert isinstance(plan(team_point(shape, **cells)), TeamQuery), (shape, cells)


def test_answer_composes_a_team_subject_sentence(team_cx_ctx: TemplateContext) -> None:
    """``answer()``'s dispatch to the team subject, end to end - the same
    surface :func:`association.query.agent.Agent._try_compose` calls."""
    result = compose_answer(team_cx_ctx, "leaderboard", {"stat": "threePointFieldGoalsMade", "team": "Orlando Magic"}, "how many 3 pointers have the magic made so far this season")
    assert result is not None
    assert "961" in result.answer
    assert result.data["team"] == "Orlando Magic"
    assert result.data["from_season_line"] is True
    assert result.artifacts == []


# ---------------------------------------------------------------------------
# Coverage floors and caveats (#197, ISSUES.md) - the compiler used to read
# neither. Both subjects, checked against the real nba/coverage.py floors
# (independent of what either fixture's own tables hold, since the refusal
# fires before any query against them runs).
# ---------------------------------------------------------------------------


def test_answer_refuses_a_season_under_the_players_coverage_floor(cx_ctx: TemplateContext) -> None:
    """Box scores (and so ``threshold_count``) reach back only to 1994 -
    #197's first gap: the compiler used to answer an empty result as
    confidently as a real one for a season no template would ever reach."""
    result = compose_answer(cx_ctx, "threshold_count", {"player": "Brandin Podziemski", "stat": "points", "threshold": 15, "season": 1990}, "Podziemski's 15+ point games in 1990")
    assert result is not None
    assert "1994" in result.answer and "1990" in result.answer


def test_answer_carries_a_coverage_caveat_for_a_partly_covered_season(cx_ctx: TemplateContext) -> None:
    """A season the floor reaches but only partly still answers - #197's
    caveat, not a refusal - with the note appended past the sentence.
    ESPN's 2001 postseason is missing ten games (`association.nba.coverage`),
    which is a real caveat this fixture's own 1990/1990 games say nothing
    about; the assertion is that the caveat function ran and found nothing
    to say for a season it does not cover a note for, proving the call site
    exists without needing to rebuild that exact gap in a tiny fixture."""
    result = compose_answer(cx_ctx, "threshold_count", {"player": "Brandin Podziemski", "stat": "points", "threshold": 15}, "Podziemski's 15+ point games this season")
    assert result is not None
    assert "Note:" not in result.answer  # the current season carries no caveat - proves one is not added where none applies


def test_answer_refuses_a_season_under_the_teams_coverage_floor(team_cx_ctx: TemplateContext) -> None:
    """``team_season_stats`` reaches back only to 1994 - #197's gap on the
    team subject's own unnarrowed (season-line) reader."""
    result = compose_answer(team_cx_ctx, "leaderboard", {"stat": "threePointFieldGoalsMade", "team": "Orlando Magic", "season": 1990}, "how many 3 pointers did the magic make in 1990")
    assert result is not None
    assert "1994" in result.answer and "1990" in result.answer


def test_answer_carries_a_coverage_caveat_on_a_narrowed_team_question(team_cx_ctx: TemplateContext) -> None:
    """The narrowed (game-level) team reader carries the same caveat call -
    ESPN's 2001 postseason gap does not touch this fixture's games, so the
    assertion is that answering a real, covered narrowed question adds no
    spurious note."""
    result = compose_answer(
        team_cx_ctx, "game_log", {"stat": "points", "team": "Toronto Raptors", "order": "recent", "limit": 3, "season_type": 2}, "total points scored by the raptors in their last 3 games"
    )
    assert result is not None
    assert "Note:" not in result.answer


# ---------------------------------------------------------------------------
# #199, F124: a ranking of the GAMES that satisfy a boolean measure by
# another measure ("highest scoring triple doubles"), not a per-player count
# or average.
# ---------------------------------------------------------------------------


def test_a_highest_scoring_boolean_measure_ranks_the_games_not_a_per_player_average(cx_ctx: TemplateContext) -> None:
    """ "Players with the highest scoring triple doubles" is rows over
    everyone, ordered by points, with `triple_double` as a predicate - not
    `_everyone_ranking`'s per-player AVERAGE (which would need
    `minimum_games` triple-doubles just to rank anyone). Podziemski's g3
    (28/10/11) is the fixture's only triple-double."""
    q = move_point(cx_ctx.con, "leaderboard", {"stat": "triple_double", "limit": 10, "season_type": 2}, "players with the highest scoring triple doubles")
    assert isinstance(q, Query)
    assert q.subject == "everyone" and q.skeleton == "rows" and q.order == "measure"
    assert q.predicates == [("triple_double", "=", True)]
    assert q.measures[0] == "points"
    out = run(cx_ctx.con, q)
    assert [r["points"] for r in out["rows"]] == [28]  # Podziemski's g3, the fixture's only triple-double


def test_biggest_with_no_stat_word_defaults_to_points(cx_ctx: TemplateContext) -> None:
    """ "Biggest triple double" names no stat word at all -
    :func:`~association.query.compose.move._boolean_game_measure` falls
    back to points, the same default a "career-high" question gets."""
    q = move_point(cx_ctx.con, "leaderboard", {"stat": "triple_double", "season_type": 2}, "biggest triple double ever")
    assert isinstance(q, Query)
    assert q.measures[0] == "points"
    assert q.scope.span == "career"  # "ever" moved the default current-season span


def test_everyone_career_scope_reads_ever_and_all_time_only_with_no_season_named() -> None:
    """:func:`_everyone_career_scope`: "ever"/"all-time" is a career span for
    a league-wide read UNLESS the question also named a season - the same
    "do not silently override a named year" discipline
    :func:`~association.query.compose.core._span_of` keeps for ``since``."""
    assert _everyone_career_scope(Scope(), "the best triple double ever") == Scope(span="career")
    assert _everyone_career_scope(Scope(), "the best triple double this season") == Scope()
    assert _everyone_career_scope(Scope(season=2024), "the best triple double of all time") == Scope(season=2024)  # a named season is not overridden


# ---------------------------------------------------------------------------
# F161: several "<N> <stat>" lines in one question at once - a league-wide
# READ OF THE GAMES clearing every line, not `_everyone_threshold_count`'s
# per-player COUNT of a single one.
# ---------------------------------------------------------------------------


def test_several_number_stat_lines_read_the_qualifying_games_not_a_count(cx_ctx: TemplateContext) -> None:
    """ "33 point and 13 rebound and 10 assist 2 blocks and 2 steals" (F161)
    reads every "<N> <stat>" pair in the question into predicates and lists
    the games clearing all of them - rows over everyone, not a per-player
    count. This fixture's own numbers: exactly two season-``s`` games clear
    20+ points, 7+ rebounds and 3+ assists at once (Brown's g5 and
    Podziemski's g3) - the 20-point-guards ranking pool (6 rebounds apiece)
    and the two players' own lower-rebound games fall short of the 7."""
    q = move_point(cx_ctx.con, "threshold_count", {"season_type": 2}, "players with 20 points and 7 rebounds and 3 assists this season")
    assert isinstance(q, Query)
    assert q.subject == "everyone" and q.skeleton == "rows"
    assert sorted(q.predicates) == sorted([("points", ">=", 20), ("rebounds", ">=", 7), ("assists", ">=", 3)])
    out = run(cx_ctx.con, q)
    assert sorted(r["points"] for r in out["rows"]) == [28, 31]


def test_a_league_wide_read_since_a_season_reaches_every_season_from_it(cx_ctx: TemplateContext) -> None:
    """``since``/``until`` bound a league-wide read exactly as they bound a
    named player's (F161, #207: "... games since 2000-01" listed the current
    season's games alone, under a heading that said so, because
    :func:`~association.query.compose.core._resolve_everyone` settled the
    span from ``season``/``span`` and never read ``since``). This fixture's
    own numbers: two games clear 12 rebounds and 5 assists - Sabonis's g2
    (season ``s``) and g7 (season ``s-1``) - so the current season alone
    holds one, "since s-1" both, and "s-1 through s-1" the other one."""
    s = current_season()
    question = "players with 12 rebounds and 5 assists games"
    this_season = move_point(cx_ctx.con, "threshold_count", {"season_type": 2}, question)
    assert isinstance(this_season, Query) and this_season.subject == "everyone" and this_season.skeleton == "rows"
    assert [r["rebounds"] for r in run(cx_ctx.con, this_season)["rows"]] == [12]
    since = move_point(cx_ctx.con, "threshold_count", {"season_type": 2, "since": s - 1}, question)
    assert isinstance(since, Query)
    out = run(cx_ctx.con, since)
    assert sorted(r["rebounds"] for r in out["rows"]) == [12, 14]
    assert f"({s - 1} on)" in sentence(since, out)
    until = move_point(cx_ctx.con, "threshold_count", {"season_type": 2, "since": s - 1, "until": s - 1}, question)
    assert isinstance(until, Query)
    assert [r["rebounds"] for r in run(cx_ctx.con, until)["rows"]] == [14]
    # A named season beside ``since`` is the contradiction ``_span_of``
    # refuses for a player, and the compiler declines it the same way.
    both = move_point(cx_ctx.con, "threshold_count", {"season_type": 2, "season": s, "since": s - 1}, question)
    assert isinstance(both, Query)
    with pytest.raises(Unsupported, match="since"):
        run(cx_ctx.con, both)


def test_a_single_number_stat_line_still_counts_by_player(cx_ctx: TemplateContext) -> None:
    """One line only is still :func:`~association.query.compose.move._everyone_threshold_count`'s
    ordinary per-player COUNT shape - the multi-line move stands aside for
    it (F161's move applies only once there are two or more lines to read).
    ``stat`` names a DIFFERENT column than the phrase itself on purpose
    (the same shape ``test_the_questions_own_number_names_its_column_not_the_routers_stat``
    exercises) - `_everyone_threshold_predicates` does not add a second,
    redundant line when the router's own ``stat`` already names the same
    column the phrase does; filed as a finding (ISSUES.md), not fixed here,
    since it is a pre-existing single-line quirk outside F161's own scope."""
    q = move_point(cx_ctx.con, "threshold_count", {"threshold": 15, "stat": "rebounds"}, "players with 15 points this season")
    assert isinstance(q, Query)
    assert q.subject == "everyone" and q.skeleton == "grouped" and q.group == "player"


def test_a_league_wide_count_with_no_line_at_all_is_still_refused(cx_ctx: TemplateContext) -> None:
    """The K2 guard :func:`_numbered_stat_lines`'s move does not weaken: a
    league-wide ``threshold_count`` naming no line at all - not even in the
    question's own text - is refused, not turned into a whole-league listing."""
    with pytest.raises(Unsupported, match="needs the line"):
        move_point(cx_ctx.con, "threshold_count", {}, "players with a good game this season")


# ---------------------------------------------------------------------------
# F056: a position word in the ``player`` slot ("shooting guard") is the
# router misfiling a position-GROUP subject as a name, not a player to
# resolve; a "with at least N <unit>" phrase is the ranking's own minimum
# sample, applied only where the relation can (a games floor), refused by
# name otherwise (an attempts or minutes floor, which it cannot apply yet).
# ---------------------------------------------------------------------------


def test_a_position_group_subject_reads_that_positions_ranking(cx_ctx: TemplateContext) -> None:
    """F056 ("highest 3 point percentage in a season. by a shooting guard
    with at least 400 attempts"), the shape: a position group as the
    subject is read off the subject reading - the phrase no longer rides in
    the ``player`` slot for the compiler to take back out. Curry ('PG') is
    the fixture's only point guard; a "with at least 2 games" floor
    (:func:`_ranking_minimum`) clears his 4 games past the real
    ``PER_GAME_MIN_GAMES`` default, which he does not reach on his own."""
    q = move_point(cx_ctx.con, "leaderboard", {"stat": "points", "season_type": 2}, "highest points per game this season by a point guard with at least 2 games")
    assert isinstance(q, Query)
    assert q.subject == "everyone" and q.position == "PG" and q.minimum_games == 2
    out = run(cx_ctx.con, q)
    assert out["rows"][0]["group"] == "Stephen Curry"
    assert out["rows"][0]["games"] == 4
    assert out["rows"][0]["points"] == pytest.approx(30.5)  # (35 + 40 + 25 + 22) / 4


def test_an_attempts_or_minutes_floor_is_refused_by_name_not_dropped_or_misapplied(cx_ctx: TemplateContext) -> None:
    """F056's own shape: "... with at least 100 attempts" names a floor the
    relation has no HAVING clause for yet (only a minimum GAMES count,
    :data:`~association.query.compose.core.Query.minimum_games`) - refused
    by name, never silently dropped (which would rank on an unqualified
    sample) and never misread as a games count (100 attempts is not 100
    games)."""
    with pytest.raises(Refused) as refused:
        move_point(cx_ctx.con, "leaderboard", {"stat": "points", "season_type": 2}, "highest points per game by a point guard with at least 100 attempts")
    assert "100 attempts" in refused.value.result.answer and "at least N games" in refused.value.result.answer


def test_a_stat_this_relation_cannot_read_is_refused_not_defaulted_to_points(cx_ctx: TemplateContext) -> None:
    """ "Who had the highest netpoints game this season" (no player named, so
    the league-wide reading) named a real stat - NetPoints - this relation
    has no measure for. Before this, ``measure`` came back ``None`` and
    ``_everyone_ranking`` silently ranked by points instead, so the answer
    looked like a real ranking of what was asked for and was not one. A
    ranking with no stat named at all still defaults to points - only a
    stat that was NAMED and failed to map is a refusal."""
    with pytest.raises(Refused) as refused:
        move_point(cx_ctx.con, "single_game_high", {"stat": "netpoints"}, "who had the highest netpoints game this season")
    assert "netpoints" in refused.value.result.answer
    # A leaderboard's NetPoints ranking is the season line's (its retired
    # template's reader, over the NetPoints tables): a point, not a refusal.
    netpoints = move_point(cx_ctx.con, "leaderboard", {"stat": "netpoints"}, "who leads the league in netpoints this season")
    assert isinstance(netpoints, Query) and netpoints.source == "seasons" and netpoints.subject == "everyone"
    # No stat named at all is still the plain "top scorers" default.
    q = move_point(cx_ctx.con, "leaderboard", {}, "who are the top scorers this season")
    assert isinstance(q, Query)
    assert q.measures == ["points"]


def test_ranking_minimum_reads_the_unit_and_the_number() -> None:
    """:func:`_ranking_minimum` reads the "at least N <unit>" phrase without
    committing to what a caller does with it - the unit is read back, not
    silently coerced to a games count."""
    assert _ranking_minimum("... with at least 100 attempts") == ("attempts", 100)
    assert _ranking_minimum("... with at least 20 games") == ("games", 20)
    assert _ranking_minimum("... this season") is None


def test_at_least_does_not_flip_a_highest_ranking_to_ascending() -> None:
    """The minimum-sample phrase "at least" ("with at least 40 games") is
    not the ascending word "least" ("the least points") - before this, any
    "at least N ..." floor silently reversed a "highest ..." ranking's sort
    order (measured against the real warehouse: a 3-point-percentage
    leaderboard "with at least 40 games" answered lowest-first)."""
    assert _asc_or_desc("highest points per game with at least 40 games") == "desc"
    assert _asc_or_desc("fewest points per game") == "asc"
    assert _asc_or_desc("lowest 3-point percentage with at least 40 games") == "asc"
    assert _asc_or_desc("the least points scored") == "asc"


# ---------------------------------------------------------------------------
# #197, the box-score-caveat half: `core.run` now threads the templates' own
# `_box_score_notes` through - a teammate's absence, the empty lines left
# out of a count, rebuilt lines, and a career predating box scores.
# ---------------------------------------------------------------------------


def _add_empty_box_score(con: duckdb.DuckDBPyConnection, event: str, season: int, team: str, opponent: str, athlete: str, tip: str) -> None:
    """A game with a REAL box-score row for ``athlete`` that ESPN served
    empty - listed (``did_not_play`` False), every stat NULL - the fault
    `_box_score_notes`' "Not counted" note is about (AGENTS.md, "Whole
    team-seasons of box scores are empty"). Distinct from `_box(..., dnp=True)`,
    which is an ordinary did-not-play entry and is excluded from the
    relation entirely (``Narrowed.base``'s own ``NOT pgl.did_not_play``), so
    it can never be "not counted" - a real gap in this file's first attempt
    at this test."""
    con.execute("INSERT INTO games VALUES (?, ?, 2, ?, ?, ?, ?, ?, ?)", [event, season, tip, team, opponent, 100, 90, team])
    con.execute(f"INSERT INTO player_box_stats VALUES ({', '.join('?' for _ in range(24))})", (event, season, 2, team, opponent, athlete, False, *([None] * 17)))


def test_run_carries_the_not_counted_box_score_note(cx_ctx: TemplateContext) -> None:
    """A game_log read now says how many of the span's games it left out for
    an empty box score - ESPN listing Podziemski with no minutes or stats
    at all, not a did-not-play entry (:func:`_add_empty_box_score`)."""
    s = current_season()
    _add_empty_box_score(cx_ctx.con, "g8", s, GS, DET, PODZ, f"{s}-03-15T20:00Z")
    q = to_query("game_log", {"player": "Brandin Podziemski"})
    out = run(cx_ctx.con, q)
    assert any("Not counted: 1 game" in note for note in out["notes"])


def test_answer_appends_the_box_score_notes_to_the_sentence(cx_ctx: TemplateContext) -> None:
    """``answer()`` appends ``out["notes"]`` to the sentence the same way it
    already appends the coverage caveat, and carries the same list on
    ``data`` for a caller that reads values rather than the prose."""
    s = current_season()
    _add_empty_box_score(cx_ctx.con, "g8", s, GS, DET, PODZ, f"{s}-03-15T20:00Z")
    result = compose_answer(cx_ctx, "game_log", {"player": "Brandin Podziemski"}, "Podziemski's game log this season")
    assert result is not None
    assert "Not counted: 1 game" in result.answer
    assert any("Not counted: 1 game" in note for note in result.data["notes"])


def test_a_career_predating_box_scores_gets_the_floor_note(cx_ctx: TemplateContext) -> None:
    """A career reaching further back than box scores do (the real 1994
    floor, `nba.coverage` - not derived from this fixture) says so.
    Podziemski's `player_season_stats_deduped` row for 1990, added here
    only, is what makes his earliest season on record predate the floor. The
    matching raw-table row keeps `_player_own_seasons` (`compose.core`, which
    reads `player_season_stats`) agreeing with it."""
    cx_ctx.con.execute("INSERT INTO player_season_stats_deduped VALUES ('10', 1990, 2, 10)")
    cx_ctx.con.execute("INSERT INTO player_season_stats VALUES ('10', 1990, 2, NULL, 10, NULL)")
    q = to_query("game_log", {"player": "Brandin Podziemski", "span": "career"})
    out = run(cx_ctx.con, q)
    assert any("Box scores begin with the 1993-94 season" in note and "1990-1993" in note for note in out["notes"])


def test_no_career_floor_note_when_the_season_is_defaulted_not_career(cx_ctx: TemplateContext) -> None:
    """The floor note is a CAREER note - it says nothing about a plain
    current-season read, even with the same older row on record."""
    cx_ctx.con.execute("INSERT INTO player_season_stats_deduped VALUES ('10', 1990, 2, 10)")
    cx_ctx.con.execute("INSERT INTO player_season_stats VALUES ('10', 1990, 2, NULL, 10, NULL)")
    q = to_query("game_log", {"player": "Brandin Podziemski"})
    out = run(cx_ctx.con, q)
    assert not any("Box scores begin with" in note for note in out["notes"])


def test_a_scalar_or_grouped_read_carries_no_leaked_rebuilt_shown_column(cx_ctx: TemplateContext) -> None:
    """The scratch ``rebuilt_shown`` column :func:`~association.query.compose.core._scalar_selects`
    adds for the box-score notes is popped back off before the rows reach a
    caller, for a named player (a `scalar` read) and for the league-wide
    subject (a `grouped` read, which has no box-score notes of its own to
    read it for at all) alike."""
    named = run(cx_ctx.con, to_query("threshold_count", {"player": "Brandin Podziemski", "stat": "points", "threshold": 15}))
    assert "rebuilt_shown" not in named["rows"][0]
    q = move_point(cx_ctx.con, "other", {}, "top scorers this season")
    assert isinstance(q, Query)  # no team named in this fixture's own words
    everyone = run(cx_ctx.con, q)
    assert "rebuilt_shown" not in everyone["rows"][0]


def test_a_ranked_by_marker_is_the_compilers_own_slot_not_an_unhonored_one(cx_ctx: TemplateContext) -> None:
    """yardstick-v2 F124, measured live: route() files `ranked_by` so
    `leaderboard` refuses "highest scoring triple doubles" to the compiler -
    which then refused it too, as a scoping slot the relation does not
    honor, and the question fell through to the agent. The marker is the
    compiler's to read (COMPILER_SLOTS); the games are ranked by points."""
    result = compose_answer(cx_ctx, "leaderboard", {"stat": "triple_double", "limit": 5, "ranked_by": "points"}, "players with the highest scoring triple doubles")
    assert result is not None
    assert "triple-double" in result.answer
    assert result.data["rows"][0]["points"] == 28
    # A league-wide row says whose game it is - the one thing a ranking of
    # players' games is asked for.
    assert result.data["rows"][0]["player"] == "Brandin Podziemski"
    assert "Brandin Podziemski" in result.answer


def test_an_ordinal_season_over_everyone_is_each_players_own(cx_ctx: TemplateContext) -> None:
    """yardstick-v2 F099 "Most points in 15th season played": no player,
    and `season_n` over everyone is each player's Nth regular season - every fixture player's 1st is s-1 and
    2nd is s, so the top single game moves from Brown's 26 (g6, s-1) to
    Curry's 40 (g3, s), and the sentence names the ordinal."""
    first = compose_answer(cx_ctx, "single_game_high", {"stat": "points", "season_n": 1, "span": "career"}, "most points in a game in 1st season played")
    second = compose_answer(cx_ctx, "single_game_high", {"stat": "points", "season_n": 2, "span": "career"}, "most points in a game in 2nd season played")
    assert first is not None and second is not None
    assert first.data["rows"][0]["points"] == 26 and first.data["rows"][0]["player"] == "Jaylen Brown"
    assert second.data["rows"][0]["points"] == 40 and second.data["rows"][0]["player"] == "Stephen Curry"
    assert "in their 2nd season" in second.answer


def test_a_team_with_no_player_is_the_teams_players_games(cx_ctx: TemplateContext) -> None:
    """yardstick-v2 F152 "oklahoma city thunder all-time triple doubles":
    the team named with no player is the `team` narrowing of a league-wide
    read - the Warriors' players' triple-doubles are Podziemski's one (g3,
    28/10/11). The parser files the team as `team`; the router once filed it
    as the player, and the compiler moved it.

    Seen live on the rendered page (2026-09-24): the grouped head named the
    span but not WHAT was counted ("... regular season career (1994 on), by
    player" - no "with a triple-double" at all), so the table of counts had
    no subject. ``_grouped_sentence`` now includes the predicates
    (``_predicates(q)``, the same call ``_rows_sentence``/``_scalar_sentence``
    already make), and ``data["headline"]`` carries the same sentence."""
    result = compose_answer(cx_ctx, "threshold_count", {"team": "Golden State Warriors", "stat": "triple_double", "span": "career"}, "golden state warriors all-time triple doubles")
    assert result is not None
    assert "Golden State Warriors" in result.answer
    assert "with a triple-double" in result.answer  # the predicate, not just the span
    assert result.data["rows"][0]["games"] == 1
    assert result.data["headline"] == result.answer.split("\n")[0].rstrip(":")
    assert "with a triple-double" in result.data["headline"]
    # Filed under player_stat (the router's intent for the live wording), the
    # team still makes it the same count.
    as_stat = compose_answer(cx_ctx, "player_stat", {"team": "Golden State Warriors", "span": "career"}, "golden state warriors all-time triple doubles")
    assert as_stat is not None and as_stat.data["rows"][0]["games"] == 1


def test_a_grouped_by_player_count_carries_the_whole_total_a_window_cut(cx_ctx: TemplateContext) -> None:
    """ "Players with 10 points this season", limited to the top 2: the page's
    Total row (``renderComposed``'s ``grouped`` skeleton) needs the WHOLE
    count behind the listed rows, not just the two shown - ``core.run``
    already computed it (``_grouped_total``) but ``_point_data`` dropped it
    on the floor before this. 90 and 91 (22 games apiece, clearing the
    10-point line every game) are the top two; Podziemski, Curry, Brown and
    Sabonis clear it in fewer games each, so the real total (56) is well
    past what the top two alone account for (44). ``stat: "rebounds"`` is a
    decoy the phrase's own "10 points" overrides (the same discipline
    ``test_the_questions_own_number_names_its_column_not_the_routers_stat``
    exercises) - stat and phrase naming the SAME column is the one case
    ``_everyone_threshold_predicates`` does not add a line for, which would
    leave no predicate to count at all."""
    q = move_point(cx_ctx.con, "threshold_count", {"threshold": 10, "stat": "rebounds", "limit": 2}, "players with 10 points this season")
    assert isinstance(q, Query)
    assert q.subject == "everyone" and q.skeleton == "grouped" and q.group == "player"
    out = run(cx_ctx.con, q)
    assert len(out["rows"]) == 2
    assert out["total"] == 56
    assert sum(r["games"] for r in out["rows"]) == 44  # the listed two alone
    result = compose_answer(cx_ctx, "threshold_count", {"threshold": 10, "stat": "rebounds", "limit": 2}, "players with 10 points this season")
    assert result is not None
    assert result.data["total"] == 56
    assert "56" in result.answer and "listed" in result.answer


# ---------------------------------------------------------------------------
# Plan item 2, step 2a: an intent's own default point is said the way its
# template says it (compose.present) - text and data both, checked here
# against the template itself on the same slots.
# ---------------------------------------------------------------------------


def _add_condition_tables(con: duckdb.DuckDBPyConnection) -> None:
    """The two tables the condition templates (``record_when``,
    ``player_splits``) read beside the relation and ``cx_ctx`` does not
    build: ``real_games`` (``real_games.build_table``, as every team fixture
    here builds it) and a ``team_box_stats`` row per team per game - the
    unseen-games count joins them."""
    con.execute("ALTER TABLE games ADD COLUMN neutral_site BOOLEAN")
    con.execute("ALTER TABLE games ADD COLUMN venue_city VARCHAR")
    real_games.build_table(con, {"games", "teams"})
    con.execute(
        "CREATE TABLE team_box_stats AS SELECT event_id, season, season_type, home_team_id AS team_id, away_team_id AS opponent_team_id FROM games "
        "UNION ALL SELECT event_id, season, season_type, away_team_id, home_team_id FROM games"
    )


#: The retired templates' own answers to the parity cases below, frozen when
#: the compiler replaced them (ROADMAP plan item 6, step (d), part 4).
_RETIRED = json.loads((Path(__file__).parent / "retired_templates.json").read_text())


def _retired_template_answer(intent: str, slots: dict[str, Any], question: str) -> TemplateResult:
    """The answer ``intent``'s retired template gave for this case
    (``retired_templates.json``), the fixture's seasons put back."""
    s = current_season()
    for case in _RETIRED["cases"]:
        if (case["intent"], case["slots"], case["question"]) == (intent, slots, question):
            text = case["expected"].replace("{S-1}-{s}", f"{s - 1}-{s % 100:02d}").replace("{S-2}-{s-1}", f"{s - 2}-{(s - 1) % 100:02d}")
            frozen = json.loads(text.replace("{S-2}", str(s - 2)).replace("{S-1}", str(s - 1)).replace("{S}", str(s)))
            return TemplateResult(data=frozen["data"], answer=frozen["answer"])
    raise AssertionError(f"no frozen answer for {intent} {slots} {question!r}")


def _parity(ctx: TemplateContext, intent: str, slots: dict[str, Any], question: str) -> tuple[Any, Any]:
    """The template's answer and the compiler's for the same slots, the
    compiler's with no template in front of it. For an intent the compiler
    alone answers now (``compose.COMPILED_INTENTS``), the template's answer is
    the one it gave when it retired, frozen, and the compiler's ``data`` is
    compared in the same JSON form."""
    from association.query.compose import COMPILED_INTENTS
    from association.query.templates import TEMPLATES

    _add_condition_tables(ctx.con)

    if intent in COMPILED_INTENTS:
        composed = compose_answer(ctx, intent, dict(slots), question)
        assert composed is not None
        return _retired_template_answer(intent, slots, question), TemplateResult(data=json.loads(json.dumps(composed.data, default=str)), answer=composed.answer)
    template = TEMPLATES[intent](ctx, Reading.from_slots(dict(slots)))
    composed = compose_answer(ctx, intent, dict(slots), question)
    assert composed is not None
    return template, composed


@pytest.mark.parametrize(
    ("intent", "slots", "question"),
    [
        ("threshold_count", {"player": "Brandin Podziemski", "stat": "points", "threshold": 15}, "how many 15+ point games did podziemski have"),
        ("threshold_count", {"player": "Brandin Podziemski", "stat": "points", "threshold": 15, "span": "career"}, "how many 15+ point games has podziemski had in his career"),
        ("threshold_count", {"stat": "points", "threshold": 25}, "who had the most 25+ point games"),
        ("threshold_count", {"stat": "points", "threshold": 20, "above": ["20+ point", "5+ rebound"]}, "who had the most 20+ point 5+ rebound games"),
        ("threshold_count", {"player": "Brandin Podziemski", "stat": "fouls", "threshold": 6, "span": "career"}, "how many times has podziemski fouled out"),
        ("single_game_high", {"player": "Stephen Curry", "stat": "points"}, "stephen curry's most points in a game"),
        ("single_game_high", {"stat": "points"}, "most points in a single game"),
        ("single_game_high", {"stat": "rebounds"}, "what was the highest rebounding game this year"),
        ("game_log", {"player": "Brandin Podziemski", "limit": 2}, "podziemski's last 2 games"),
        ("game_log", {"player": "Brandin Podziemski", "opponent": "Boston Celtics", "span": "career"}, "podziemski's games against boston"),
        ("player_stat", {"player": "Brandin Podziemski", "opponent": "Boston Celtics"}, "podziemski's stats vs boston"),
        ("record_when", {"player": "Brandin Podziemski", "stat": "points", "threshold": 15}, "warriors record when podziemski scores 15+"),
        ("player_splits", {"player": "Brandin Podziemski", "split": "home_away"}, "podziemski home and away splits"),
        ("player_splits", {"player": "Brandin Podziemski", "split": "wins_losses", "span": "career"}, "podziemski's career splits in wins and losses"),
    ],
)
def test_an_intents_own_point_reads_as_its_template(cx_ctx: TemplateContext, intent: str, slots: dict[str, Any], question: str) -> None:
    """Step 2a's parity, per intent: the compiler's answer to the intent's
    own default point is the template's, word for word and key for key -
    what folding the template into the compiler needs. The numbers are the
    compiler's own settled player, span and narrowing; the sentence and
    ``data`` are the template's own helpers (``compose.present``)."""
    template, composed = _parity(cx_ctx, intent, slots, question)
    assert composed.answer == template.answer
    assert composed.data == template.data


def test_a_point_the_words_moved_keeps_the_compilers_own_sentence(cx_ctx: TemplateContext) -> None:
    """A measure the question's words add ("PRA", which player_stat's line
    does not carry) is not the intent's own point, so the compiler's own
    sentence and point data answer it, as before step 2a."""
    result = compose_answer(cx_ctx, "player_stat", {"player": "Brandin Podziemski", "opponent": "Boston Celtics"}, "Podziemski's PRA vs Boston")
    assert result is not None
    assert result.data["skeleton"] == "scalar" and result.data["measures"] == ["pra"]
    assert result.data["rows"][0]["games"] == 2


def test_a_slot_the_template_refuses_keeps_the_compilers_own_sentence(cx_ctx: TemplateContext) -> None:
    """``check_scope`` gates the template's presentation: an opponent is a
    slot ``threshold_count`` does not honor, so the count against Boston is
    the compiler's point, said the compiler's way."""
    result = compose_answer(cx_ctx, "threshold_count", {"player": "Brandin Podziemski", "stat": "points", "threshold": 15, "opponent": "Boston Celtics"}, "podziemski 15+ point games vs boston")
    assert result is not None
    assert result.data["skeleton"] == "scalar"
    assert result.data["rows"][0]["games"] == 1  # g1 (20) - g5 (10) does not clear the line


def test_a_single_game_high_question_is_one_game_even_without_in_a_game(cx_ctx: TemplateContext) -> None:
    """ "What was the highest scoring game against Detroit" names no "in a
    game", and the league-wide read ranked per-game AVERAGES for it - the
    intent itself names one game: Curry's 40 in g3. (An opponent is a slot
    ``single_game_high`` refuses, which is how such a question reaches the
    compiler at all, so the compiler's own rows answer it.)"""
    result = compose_answer(cx_ctx, "single_game_high", {"stat": "points", "opponent": "Detroit Pistons"}, "what was the highest scoring game against detroit")
    assert result is not None
    assert result.data["skeleton"] == "rows" and result.data["rows"][0]["points"] == 40


def test_a_league_count_on_the_routers_own_line_is_answered(cx_ctx: TemplateContext) -> None:
    """ "Most games with 10+ assists": the router's stat and threshold are
    the whole line, and the league-wide count declined for want of one
    (``_everyone_threshold_predicates`` adds none on the measure it would
    rank by). Counted now - Podziemski's g3 (11 assists) is the only one."""
    result = compose_answer(cx_ctx, "threshold_count", {"stat": "assists", "threshold": 10}, "most games with 10+ assists this season")
    assert result is not None
    assert result.data["leaders"] == [{"player": "Brandin Podziemski", "games": 1}]


def test_a_history_by_season_keeps_the_newest_seasons(cx_ctx: TemplateContext) -> None:
    """``player_history``'s game-level reading (``move.games_reading``, for a
    stat the season line has no per-season column for), limited to one
    season, is his NEWEST one, newest first - it ordered by the label
    ascending and kept the oldest, so "the past 4 seasons" was answered with
    a career's first four."""
    s = current_season()
    result = compose_answer(cx_ctx, "player_history", {"player": "Brandin Podziemski", "stat": "turnovers", "limit": 1}, "podziemski's turnovers over the past season")
    assert result is not None
    assert [r["group"] for r in result.data["rows"]] == [s]
    assert "most recent 1 seasons" in result.answer
    both = compose_answer(cx_ctx, "player_history", {"player": "Brandin Podziemski", "stat": "turnovers", "limit": 5}, "podziemski's turnovers by season")
    assert both is not None
    assert [r["group"] for r in both.data["rows"]] == [s, s - 1]
    assert "most recent" not in both.answer  # the whole career fit


# ---------------------------------------------------------------------------
# The season line as a second source (#228): an unnarrowed player_stat and a
# per-season player_history read player_season_stats_deduped, through the
# templates' own readers - text and data the template's, on the same slots.
# ---------------------------------------------------------------------------


def _add_season_line(con: duckdb.DuckDBPyConnection) -> None:
    """``player_season_stats_deduped`` with the columns the season-line
    readers select, replacing ``cx_ctx``'s games-only stub: Podziemski and
    Curry over two seasons, numbers of their own (not the fixture's box
    scores - the season line is a separate source, and a reading that summed
    the games instead would show here as a different number)."""
    s = current_season()
    con.execute("DROP TABLE player_season_stats_deduped")
    con.execute(
        "CREATE TABLE player_season_stats_deduped (athlete_id VARCHAR, season INTEGER, season_type INTEGER, gamesPlayed INTEGER, "
        "avgPoints DOUBLE, points DOUBLE, avgRebounds DOUBLE, totalRebounds DOUBLE, avgAssists DOUBLE, assists DOUBLE, "
        "threePointFieldGoalPct DOUBLE, threePointFieldGoalsMade DOUBLE, threePointFieldGoalsAttempted DOUBLE)"
    )
    con.executemany(
        "INSERT INTO player_season_stats_deduped VALUES (?, ?, 2, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (PODZ, s - 1, 60, 9.5, 570, 4.0, 240, 3.0, 180, 35.0, 70, 200),
            (PODZ, s, 70, 12.5, 875, 5.0, 350, 3.5, 245, 38.0, 95, 250),
            (CURRY, s - 1, 70, 24.5, 1715, 4.5, 315, 6.0, 420, 40.0, 280, 700),
            (CURRY, s, 43, 26.0, 1118, 4.0, 172, 5.0, 215, 39.3, 190, 484),
        ],
    )


@pytest.mark.parametrize(
    ("intent", "slots", "question"),
    [
        ("player_stat", {"player": "Stephen Curry", "stat": "points"}, "how many points does curry average"),
        ("player_stat", {"player": "Stephen Curry"}, "what are curry's numbers this season"),
        ("player_stat", {"player": "Stephen Curry", "span": "career"}, "curry career averages"),
        ("player_stat", {"player": "Stephen Curry", "stat": "threePointFieldGoalPct", "span": "career"}, "curry career 3pt percentage"),
        ("player_history", {"player": "Stephen Curry", "stat": "threePointFieldGoalPct", "limit": 4}, "curry's 3pt percentage over the past 4 seasons"),
        ("player_history", {"player": "Brandin Podziemski", "stat": "points", "limit": 1}, "podziemski's ppg over the past season"),
        ("player_history", {"player": "Stephen Curry", "stat": "points", "span": "career"}, "curry's ppg every season of his career"),
    ],
)
def test_the_season_line_reads_as_its_template(cx_ctx: TemplateContext, intent: str, slots: dict[str, Any], question: str) -> None:
    """The season line as the compiler's second source: an unnarrowed
    ``player_stat`` and a ``player_history`` are the template's own answer,
    word for word and key for key - the numbers from the season line
    (Curry's 26.0 this season is not his box scores' average), where the
    compiler declined the first and answered the second from box scores
    grouped by season, in its own words, before (#228)."""
    _add_season_line(cx_ctx.con)
    template, composed = _parity(cx_ctx, intent, slots, question)
    assert composed.answer == template.answer
    assert composed.data == template.data


def test_a_season_line_point_the_words_moved_is_not_the_templates(cx_ctx: TemplateContext) -> None:
    """A measure the question's words add ("PRA") to an unnarrowed line is a
    point the season line's reader does not say, so the compiler declines it
    exactly as it declined every unnarrowed line before - never the default
    stat line in its place."""
    _add_season_line(cx_ctx.con)
    assert compose_answer(cx_ctx, "player_stat", {"player": "Stephen Curry"}, "curry's PRA this season") is None


def test_the_season_source_is_never_compiled_over_games() -> None:
    """A season-line point reaching the game-level compiler is refused, not
    read over box scores under the season line's name."""
    q = to_query("player_stat", {"player": "Stephen Curry", "stat": "points"})
    assert q.source == "seasons"
    with pytest.raises(Unsupported, match="seasons source"):
        compile_query(duckdb.connect(":memory:"), q)


def test_own_team_narrows_a_composed_average(cx_ctx: TemplateContext) -> None:
    """``own_team`` ("lebron stats as a starter for Miami") narrows the
    compiler's read exactly as it narrows ``player_stat``'s: Curry never
    played for Boston, so his games "for Boston" are none - the compiler
    ignored the slot and averaged every game he played."""
    q = to_query("player_stat", {"player": "Stephen Curry", "opponent": "Detroit Pistons", "own_team": "Boston Celtics"})
    assert _run(cx_ctx.con, q)["rows"][0]["games"] == 0
    q = to_query("player_stat", {"player": "Stephen Curry", "opponent": "Detroit Pistons", "own_team": "Golden State Warriors"})
    assert _run(cx_ctx.con, q)["rows"][0]["games"] == 1  # g3


def test_a_composed_answer_carries_no_coverage_caveat_of_its_own(cx_ctx: TemplateContext) -> None:
    """The agent appends ``coverage_caveat`` to a composed answer exactly as
    to a template's (``agent._try_compose``); the compiler appending it too
    printed ESPN's 2001-playoffs note twice. The note is the agent's to add."""
    result = compose_answer(cx_ctx, "game_log", {"player": "Brandin Podziemski", "season": 2001, "season_type": 3}, "podziemski plus-minus game log 2001 playoffs")
    assert result is not None
    assert "Note:" not in result.answer
    assert not any("Note:" in note for note in result.data.get("notes", []))


def test_a_single_games_usage_is_the_percent_the_split_averages(cx_ctx: TemplateContext) -> None:
    """#222: ``usage_pct`` is stored as a percent (``player_advanced_stats``
    computes ``100.0 * ...``), not a fraction - one game's 24.35 printed as
    "2435.0%". The per-game row now reads the figure ``player_splits``' USG%
    column averages: Podziemski's only away game this season is g1, so the
    split's away USG% IS g1's usage, and the log's g1 row says the same."""
    from test_templates import player_splits  # the compiler's, player_splits' template retired

    _add_condition_tables(cx_ctx.con)
    cx_ctx.con.execute("ALTER TABLE player_box_stats ADD COLUMN usage_pct DOUBLE")
    cx_ctx.con.execute("UPDATE player_box_stats SET usage_pct = CASE event_id WHEN 'g1' THEN 24.35 ELSE 18.0 END WHERE athlete_id = ?", [PODZ])
    split = player_splits(cx_ctx, Reading.from_slots({"player": "Brandin Podziemski", "stat": "usage_pct", "split": "home_away"}))
    away = next(row for row in split.data["splits"]["home_away"] if row["group"] == "away")
    assert away["games"] == 1 and away["usage_pct"] == pytest.approx(24.35)
    log = compose_answer(cx_ctx, "game_log", {"player": "Brandin Podziemski", "stat": "usage_pct"}, "podziemski usage game log")
    assert log is not None
    g1 = next(row for row in log.data["rows"] if row["opponent"] == "BOS" and not row["home"])
    assert g1["usage_pct"] == pytest.approx(away["usage_pct"])
    assert f"usage {away['usage_pct']:.1f}%" in log.answer
    assert "2435" not in log.answer


def test_a_player_beside_a_team_subject_is_not_read_as_the_team(cx_ctx: TemplateContext) -> None:
    """ "show me stats for the warriors when podziemski scored 15+ points"
    reads as a TEAM subject with Podziemski beside it; the router's
    ``player`` slot holds him, and moving it into ``team`` resolved a team
    called "Podziemski" and declined the question (measured live on "... for
    sixers when maxey scored 20+ points"). It stays his, and the record is
    the template's own table."""
    _add_condition_tables(cx_ctx.con)
    result = compose_answer(cx_ctx, "record_when", {"stat": "points", "player": "Podziemski", "threshold": 15}, "show me stats for the warriors when podziemski scored 15+ points")
    assert result is not None
    assert result.answer.startswith("Golden State Warriors record when Brandin Podziemski had 15+ points")


def test_a_narrowed_advanced_rate_is_read_over_the_games_not_the_season_line(cx_ctx: TemplateContext) -> None:
    """ "klay ts% vs boston": the retired player_stat template refused a
    narrowed advanced rate ("computed per season"); the compiler reads it
    over exactly the narrowed games as a ratio of sums (``core.RATES``), in
    its own sentence, never the season line's figure (5.0.0)."""
    _add_condition_tables(cx_ctx.con)
    result = compose_answer(cx_ctx, "player_stat", {"player": "Brandin Podziemski", "stat": "ts_pct", "opponent": "Boston Celtics"}, "podziemski ts% vs boston")
    assert result is not None
    assert "TS%" in result.answer and "vs the Boston Celtics" in result.answer
    assert result.data["measures"] == ["ts_pct"] and result.data["skeleton"] == "scalar" and result.data["rows"][0]["games"] == 2


def test_a_log_reads_a_rebuilt_game_with_its_minutes_blank(cx_ctx: TemplateContext) -> None:
    """A listing's rebuilt-line rule is ``game_log``'s own
    (``templates.games._rebuilt_readable``): ``minutes`` is exempt - play-by-
    play cannot recover it, so a rebuilt row shows it blank - and the other
    columns shown are ones a rebuild gets right. The compiler held minutes
    against the rule and so never listed a rebuilt game on its default line:
    Podziemski's g5, rebuilt here, was missing from his log."""
    con = cx_ctx.con
    con.execute("ALTER TABLE player_box_stats ADD COLUMN reconstructed BOOLEAN")
    con.execute("UPDATE player_box_stats SET reconstructed = FALSE")
    con.execute("UPDATE player_box_stats SET minutes = NULL, reconstructed = TRUE WHERE event_id = 'g5' AND athlete_id = ?", [PODZ])
    con.execute("CREATE VIEW player_box_stats_filled AS SELECT * FROM player_box_stats")
    out = run(con, to_query("game_log", {"player": "Brandin Podziemski"}))
    newest = out["rows"][0]
    assert newest["points"] == 10 and newest["minutes"] is None and newest["reconstructed"]
    template, composed = _parity(cx_ctx, "game_log", {"player": "Brandin Podziemski"}, "podziemski's game log")
    assert composed.answer == template.answer


def test_a_teams_total_of_triple_doubles_is_declined_for_the_refusals_module(cx_ctx: TemplateContext) -> None:
    """ "oklahoma city thunder all-time triple doubles vs west" (day5): a team
    subject on a leaderboard with a boolean stat is a team aggregate nothing
    reads - declined here, so refusals._team_boolean_count names that cause
    instead of the ranking's "no ranking reads triple_double"."""
    from association.query.compose.core import Unsupported

    with pytest.raises(Unsupported, match="team's total"):
        move_point(
            cx_ctx.con,
            "leaderboard",
            {"stat": "triple_double", "team": "Golden State Warriors", "span": "career"},
            "golden state warriors all-time triple doubles vs west",
            Subject("team", teams=("Golden State Warriors",), question="golden state warriors all-time triple doubles vs west"),
        )


# ---------------------------------------------------------------------------
# What only the four compiler-first templates answered, answered by the
# compiler (ROADMAP plan item 6, step (d), part 4): measured on the unit tests
# and the recorded questions, these were the shapes the template fallback
# still carried.
# ---------------------------------------------------------------------------


def test_a_count_under_a_ceiling_with_no_threshold_is_counted(cx_ctx: TemplateContext) -> None:
    """ "Sga games with under 14 fta in his whole career": the below phrase
    IS the count - no threshold arrives - and the compiler declined it, so
    the template's fallback answered. Counted now, in the template's words:
    Curry's four games this season all have under 5 free throw attempts."""
    result = compose_answer(cx_ctx, "threshold_count", {"player": "Stephen Curry", "stat": "freeThrowsAttempted", "below": ["under 5 fta"]}, "curry games with under 5 fta")
    assert result is not None
    assert result.answer == f"Stephen Curry had 4 games with under 5 free throw attempts in the {current_season()} regular season."
    assert result.data["leaders"] == [{"player": "Stephen Curry", "games": 4}]


def test_a_teams_own_threshold_record_is_said_by_record_whens_team_reader(cx_ctx: TemplateContext) -> None:
    """ "What was the warriors record when they scored 100 points" (ISSUES.md
    #144's shape): a team's record above and below its OWN line, which the
    team subject's readers (a season sum, a window sum) cannot represent -
    so run_team declines it, and record_when's own team reader answers
    (compose.present.present_team). Golden State this season: 110 (W), 99
    (L), 120 (W), 90 (L), 100 (W)."""
    # The team-games relation reads the venue columns the player fixture skips.
    cx_ctx.con.execute("ALTER TABLE games ADD COLUMN neutral_site BOOLEAN")
    cx_ctx.con.execute("ALTER TABLE games ADD COLUMN venue_city VARCHAR")
    real_games.build_table(cx_ctx.con, {"games", "teams"})
    result = compose_answer(cx_ctx, "record_when", {"team": "Golden State Warriors", "stat": "points", "threshold": 100, "season_type": 2}, "warriors record when they scored 100 points")
    assert result is not None
    assert result.answer.startswith("Golden State Warriors record when they had 100+ points")
    assert result.data["reached"]["games"] == 3 and result.data["reached"]["wins"] == 3
    assert result.data["fell_short"]["games"] == 2 and result.data["fell_short"]["wins"] == 0


def test_a_record_over_a_line_of_zero_is_declined_as_the_template_refused_it(cx_ctx: TemplateContext) -> None:
    """A line of 0 is every game he played - never a record "when". The
    template refused it; the compiler answered "went 2-1 in the 3 games
    points >= 0" until it refused the same way, with the reason."""
    why: list[str] = []
    assert compose_answer(cx_ctx, "record_when", {"player": "Stephen Curry", "stat": "points", "threshold": 0}, "", declined=why.append) is None
    assert why and "positive threshold" in why[0]


def test_a_league_count_in_an_ordinal_season_is_declined_not_narrowed_silently(cx_ctx: TemplateContext) -> None:
    """ "Most 20+ point games in a 15th season": a league has no career to
    count seasons in. Read over everyone, the compiler narrowed to players in
    their 15th season OF THE DEFAULT YEAR while its sentence named only the
    year; it declines now, as the template did, saying why."""
    why: list[str] = []
    assert compose_answer(cx_ctx, "threshold_count", {"stat": "points", "threshold": 20, "season_n": 15}, "", declined=why.append) is None
    assert why and "15th season is a place in one player's career" in why[0]


def test_a_history_naming_no_stat_is_points_and_says_so(cx_ctx: TemplateContext) -> None:
    """A per-season history that names no stat reads points per game, and its
    heading says so - a default that is displayed and corrected by naming a
    stat (AGENTS.md). The retired template refused it; the compiler, which
    answered player_history first from step (a), read the same default."""
    result = compose_answer(cx_ctx, "player_history", {"player": "Brandin Podziemski"}, "")
    assert result is not None
    assert "points per game" in result.answer.split("\n")[0]


def test_a_history_of_a_stat_nothing_carries_is_declined_not_swapped_for_points(cx_ctx: TemplateContext) -> None:
    """ "shot_distance" has no per-season column and no game-level measure:
    the compiler drew Luka Doncic's POINTS by season for it, a stat named and
    silently replaced, until it declined as the retired template did."""
    why: list[str] = []
    assert compose_answer(cx_ctx, "player_history", {"player": "Brandin Podziemski", "stat": "shot_distance"}, "", declined=why.append) is None
    assert why == ["no per-season history for stat 'shot_distance'"]


# ---------------------------------------------------------------------------
# The parser reads the compiler's point once (ROADMAP plan item 6, step (e)): the
# Reading carries it, and the compiler answers it without the question.
# ---------------------------------------------------------------------------


def test_the_parser_reads_the_point_and_the_compiler_answers_it_unread(cx_ctx: TemplateContext, monkeypatch: pytest.MonkeyPatch) -> None:
    """``parse.reading_from_route`` reads the compiler's point from the
    question's words (``Reading.point``), and ``compose.answer`` plans and
    runs it without the question: with the point reader disabled outright,
    the answer is the one the same point gives read the test's own way
    (``compose_answer`` above, the subject and the point built by hand)."""
    from association.query.parse import reading_from_route

    question = "how many 15+ point games did podziemski have"
    reading = reading_from_route(cx_ctx.con, question, with_subject(cx_ctx.con, question, slots_route("threshold_count", {"player": "Brandin Podziemski", "stat": "points", "threshold": 15})))
    assert reading.point is not None and (reading.point.shape, reading.point.aggregate) == ("scalar", "count")
    by_hand = compose_answer(cx_ctx, "threshold_count", reading.scope.to_slots(), question, reading.subject)

    def unread(*args: Any, **kwargs: Any) -> Reading:
        raise AssertionError("the compiler read the question")

    monkeypatch.setattr("association.query.compose.move.read_point", unread)
    answered = compose.answer(cx_ctx, reading)
    assert answered is not None and by_hand is not None
    assert (answered.answer, answered.data) == (by_hand.answer, by_hand.data)


def test_a_point_the_compiler_declines_or_refuses_travels_on_the_reading(cx_ctx: TemplateContext) -> None:
    """The compiler's verdict, read at parse time, is what ``compose.answer``
    gives: a decline's reason (a league count with no line to count), and a
    refusal as the answer - a copy, since the agent appends its notes to the
    answer it is handed."""
    from association.query.parse import reading_from_route

    declined = reading_from_route(cx_ctx.con, "how many games", with_subject(cx_ctx.con, "how many games", slots_route("threshold_count", {"stat": "points"})))
    assert declined.point is None and declined.point_declined is not None
    why: list[str] = []
    assert compose.answer(cx_ctx, declined, declined=why.append) is None
    assert why == [declined.point_declined]

    refused = reading_from_route(cx_ctx.con, "most gizmos in a single game", with_subject(cx_ctx.con, "most gizmos in a single game", slots_route("single_game_high", {"stat": "gizmos"})))
    assert refused.point is None and refused.point_refusal is not None
    answered = compose.answer(cx_ctx, refused)
    assert answered is not None and answered.answer == refused.point_refusal.answer and answered is not refused.point_refusal


def test_a_summed_true_shooting_rate_is_a_fraction_like_its_column(cx_ctx: TemplateContext) -> None:
    """``ts_pct`` and ``efg_pct`` are stored per game as fractions (0.57) and
    printed times 100 (``sentence._FRACTION_COLUMNS``, the page's
    ``FRACTIONS``). Their ratio-of-sums rate (``core.RATES``) was computed
    times 100 already, so a narrowed line printed it twice: on the real
    warehouse, "Joel Embiid averaged 4622.5% TS% per game over 2 games vs the
    Boston Celtics". Podziemski's two games against Boston: 30 points on 30
    shots and no free throws is a TS% of 0.5."""
    result = compose_answer(cx_ctx, "player_stat", {"player": "Brandin Podziemski", "stat": "ts_pct", "opponent": "Boston Celtics"}, "podziemski true shooting vs boston")
    assert result is not None
    assert result.data["rows"][0]["ts_pct"] == pytest.approx(0.5)
    assert "50.0% TS%" in result.answer and "5000" not in result.answer


# ---------------------------------------------------------------------------
# The planner refuses from the Reading (ROADMAP plan item 6, step (f)).


def test_the_planner_refuses_a_narrowing_the_relation_cannot_honor() -> None:
    """``plan`` is where a Reading meets its relation, and a scoping slot the
    relation does not narrow by (``rate``: no per-36 read on the games) is
    refused there - not one call later, inside the compile step."""
    from association.query.compose.adapt import to_reading

    with pytest.raises(Unsupported, match="the relation cannot honor \\['rate'\\]"):
        plan(to_reading("threshold_count", {"player": "Brandin Podziemski", "stat": "points", "threshold": 30, "rate": "per_36"}))
    assert isinstance(plan(to_reading("threshold_count", {"player": "Brandin Podziemski", "stat": "points", "threshold": 30})), Query)


def test_the_planner_refuses_after_the_parser_has_read_and_only_once(cx_ctx: TemplateContext) -> None:
    """The parser reads the point and does not plan it (``ROADMAP.md``,
    Phase 1): a narrowing the relation cannot honor is the PLAN stage's
    verdict (``plan_point``) - the reason the refusal names, never a
    template's list read after the fact - and the compiler answers from the
    planning it is handed without planning again."""
    from association.query.compose import plan as plan_module
    from association.query.compose.plan import plan_point
    from association.query.parse import reading_from_route

    reading = reading_from_route(
        cx_ctx.con,
        "podziemski 30 point games per 36",
        with_subject(cx_ctx.con, "podziemski 30 point games per 36", slots_route("threshold_count", {"player": "Brandin Podziemski", "stat": "points", "threshold": 30, "rate": "per_36"})),
    )
    assert reading.point is not None and reading.point_declined is None and reading.point_refusal is None
    planned = plan_point(reading)
    assert planned.query is None and planned.refusal is None
    assert planned.declined is not None and planned.declined.startswith("the relation cannot honor ['rate']")
    why: list[str] = []
    assert compose.answer(cx_ctx, reading, declined=why.append, planned=planned) is None and why == [planned.declined]

    answerable = reading_from_route(
        cx_ctx.con,
        "podziemski 30 point games",
        with_subject(cx_ctx.con, "podziemski 30 point games", slots_route("threshold_count", {"player": "Brandin Podziemski", "stat": "points", "threshold": 30})),
    )
    handed = plan_point(answerable)
    assert isinstance(handed.query, Query)
    calls: list[Reading] = []
    original = plan_module.plan
    try:
        plan_module.plan = lambda point: calls.append(point) or original(point)  # type: ignore[assignment, func-returns-value]
        assert compose.answer(cx_ctx, answerable, planned=handed) is not None
        assert calls == []  # handed its planning, the compiler does not plan
        assert compose.answer(cx_ctx, answerable) is not None
        assert len(calls) == 1  # a caller with only a Reading has it planned here
    finally:
        plan_module.plan = original


def test_a_presenter_steps_aside_for_a_narrowing_its_words_do_not_state(cx_ctx: TemplateContext) -> None:
    """A retired template's words name the narrowings it honored and no
    other (``compose.present.STATED_SCOPING``): an opponent on a single-game
    high is the relation's to narrow by and the compiler's sentence's to
    state, so the presenter answers nothing and every presenter declares."""
    from association.query.compose.present import PRESENTERS, STATED_SCOPING, TEAM_ONLY_PRESENTERS, present

    assert set(STATED_SCOPING) == set(PRESENTERS) | TEAM_ONLY_PRESENTERS
    narrowed = to_query("single_game_high", {"player": "Brandin Podziemski", "stat": "points", "opponent": "Boston Celtics"})
    assert present(cx_ctx.con, "single_game_high", narrowed) is None
