"""Fixture tests for :mod:`association.query.compose`, the compiler. Modeled
on ``pg_ctx`` in ``tests/query/test_readers.py``: a small
``player_game_log``/``games``/``players`` warehouse in miniature, built once
per test so every assertion is against numbers this file itself defines, not
the real warehouse.

One test per skeleton, one per narrowing rule the fixture can exercise, one
per move the words make of a point (``point._move_*``), and the two guards on
a league-wide point - see the module docstrings of ``compose/core.py`` and
``point.py``.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import duckdb
import pytest
from routed import Routed, Said, planned_query, reading_of, said, with_subject
from warehouse_rows import insert_rows

from association.fetch.repairs import real_games
from association.nba.season import current_season
from association.query import compose
from association.query.answer import AnswerContext
from association.query.compose.core import Query, Refused, Unsupported, compile_query, run
from association.query.compose.netpoints import NetPointsQuery
from association.query.compose.plan import aggregate_of, plan, plan_point, refusal_of, refusal_result
from association.query.compose.rankings import read_game_ranking
from association.query.compose.say import decline_reason_phrase, say
from association.query.compose.seasons import player_history_reads
from association.query.compose.sentence import sentence, team_sentence
from association.query.compose.shots import ShotQuery
from association.query.compose.team import TeamQuery, run_team
from association.query.compose.team_stats import TeamSeasonQuery
from association.query.entities import names_of
from association.query.notes import collect
from association.query.parse import with_point
from association.query.point import _asc_or_desc, default_point, minimum_under, read_point, team_read_point
from association.query.reading import (
    GAME_HIGHS,
    GAMES_COUNTED,
    LINE_RECORD,
    LINE_RUNS,
    PERIOD_RANKING,
    PLAYER_LINE,
    PLAYER_LOG,
    PLAYER_RANKING,
    PLAYER_SPLITS,
    PRESENCE_SPLIT,
    SEASON_HISTORY,
    TEAM_GAME_HIGHS,
    TEAM_LINE,
    TEAM_PERIOD_TOTAL,
    TEAM_RANKING,
    TEAM_RECORD,
    Aggregate,
    Cause,
    Companion,
    Cuts,
    DeclineReason,
    Filtered,
    Line,
    Measure,
    Period,
    PointRefused,
    PointShape,
    Reading,
    Span,
    Window,
    _career_scope,
    situation_of,
)
from association.query.reading import Subject as Who
from association.query.result import Grouped, LineFacts, Part, Refusal, Result
from association.query.result import Span as ResultSpan
from association.query.season_line import HISTORY_LINE
from association.query.subject import Subject, read_subject
from association.query.window import read_minimum


def _reading(con: duckdb.DuckDBPyConnection, asked: PointShape | None, scope: Filtered, question: str, subject: Subject | None = None) -> Reading:
    """A test's typed reading as the Reading the parser hands the compiler:
    the key the words ask, the filters the case states (its lines among
    them), and the subject read from the question the way ``point`` read it
    for a caller with none - never applied to the scope, so a case says what
    it did. A qualifier the words state ("with at least 2 games") is read
    from them as the parser reads it (#377)."""
    stated = replace(scope, minimum=minimum_under(asked, read_minimum(question)).minimum)
    return Reading(**stated.filters(), asked=asked, subject=subject or read_subject(names_of(con), question, asked, scope))


def compose_answer(ctx: AnswerContext, asked: PointShape | None, scope: Filtered, question: str, subject: Subject | None = None, declined: Callable[[str], None] | None = None) -> Said | None:
    """``compose.answer`` over the point ``question`` moves for the case's
    typed reading under the key ``asked``."""
    reading = with_point(question, _reading(ctx.con, asked, scope, question, subject))
    answered = compose.answer(ctx, reading, planned=plan_point(reading), declined=declined)
    return None if answered is None else said(answered)


def move_point(
    con: duckdb.DuckDBPyConnection, asked: PointShape | None, scope: Filtered, question: str, subject: Subject | None = None
) -> Query | TeamQuery | TeamSeasonQuery | NetPointsQuery | ShotQuery:
    """The point ``question`` moves for the case's typed reading, planned -
    raising the compiler's own ``Unsupported``/``Refused`` as the reader
    does."""
    return plan(read_point(_reading(con, asked, scope, question, subject), question))


def team_move_point(con: duckdb.DuckDBPyConnection, scope: Filtered, question: str) -> TeamQuery | None:
    """``point.team_read_point`` over the case's typed reading and the
    subject read from the question, planned - the team's point as the team
    compiler runs it, or ``None`` where the team is not the subject."""
    reading = team_read_point(scope, question, read_subject(names_of(con), question, None, scope))
    if reading is None:
        return None
    query = plan(reading)
    assert isinstance(query, TeamQuery)
    return query


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
def cx_ctx(tmp_path: Path) -> Iterator[AnswerContext]:
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
    games = [
        ("g1", s, 2, f"{s - 1}-11-01T20:00Z", BOS, GS, 100, 110, GS),
        ("g2", s, 2, f"{s - 1}-12-01T20:00Z", GS, DET, 99, 105, DET),
        ("g3", s, 2, f"{s}-01-10T20:00Z", GS, DET, 120, 101, GS),
        ("g4", s, 2, f"{s}-02-10T20:00Z", LAL, GS, 100, 90, LAL),
        ("g5", s, 2, f"{s}-03-01T20:00Z", GS, BOS, 100, 95, GS),
        ("g6", s - 1, 2, f"{s - 1}-02-01T20:00Z", BOS, GS, 100, 90, BOS),
        ("g7", s - 1, 2, f"{s - 1}-03-01T20:00Z", GS, DET, 110, 90, GS),
        # 22 games apiece for the two ranking-only players, so the league read
        # clears PER_GAME_MIN_GAMES (20) - nothing else in this fixture does.
        *((f"x{n}", s, 2, (date(s - 1, 11, 5) + timedelta(days=2 * n)).isoformat() + "T20:00Z", LAL, DET, 100, 90, LAL) for n in range(22)),
    ]
    insert_rows(c, "games", games)
    c.execute(f"CREATE TABLE player_box_stats ({_BOX_COLUMNS})")
    insert_rows(
        c,
        "player_box_stats",
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
    insert_rows(c, "player_season_stats_deduped", [(pid, season, 2, 1) for pid in (PODZ, CURRY, BROWN, SABONIS, "90", "91") for season in (s - 1, s)])
    # `_player_own_seasons` (`compose.core`) reads `season_line.seasons_on_record`,
    # which reads the RAW table `player_season_stats_deduped` is a view over
    # in the real warehouse - so a plain career sentence can name a player's
    # own first and last season instead of the relation's floor. Same rows as the
    # deduped table above: this fixture has no postseason-copy row for either
    # to disagree about.
    c.execute("CREATE TABLE player_season_stats (athlete_id VARCHAR, season INTEGER, season_type INTEGER, team_id VARCHAR, gamesPlayed INTEGER, points INTEGER)")
    insert_rows(c, "player_season_stats", [(pid, season, 2, None, 1, None) for pid in (PODZ, CURRY, BROWN, SABONIS, "90", "91") for season in (s - 1, s)])
    yield AnswerContext(con=c, out_dir=tmp_path)
    c.close()


def _run(con: duckdb.DuckDBPyConnection, q: Query) -> dict[str, Any]:
    return run(con, q)


# ---------------------------------------------------------------------------
# One test per skeleton.
# ---------------------------------------------------------------------------


def test_rows_skeleton_reads_a_log(cx_ctx: AnswerContext) -> None:
    """A player's log: the four-stat line, newest games first."""
    q = planned_query(PLAYER_LOG, Reading(who=Who(kind="player", players=("Brandin Podziemski",))))
    out = _run(cx_ctx.con, q)
    assert [r["points"] for r in out["rows"]] == [10, 28, 15, 20]  # g5, g3, g2, g1 - newest first, g4 (DNP) excluded


def test_scalar_skeleton_reads_a_count_or_an_average(cx_ctx: AnswerContext) -> None:
    """A count of games over a line: a scalar count over the narrowed games."""
    q = planned_query(
        GAMES_COUNTED,
        Reading(who=Who(kind="player", players=("Brandin Podziemski",)), lines=(Line(measure="points", value=15, as_typed="15 points", keyed=True),), measure=Measure(key="points", as_typed="points")),
    )
    out = _run(cx_ctx.con, q)
    assert out["rows"][0]["games"] == 3  # g1 (20), g2 (15), g3 (28) - g5 (10) does not clear the line


def test_a_count_of_one_game_is_said_in_the_singular(cx_ctx: AnswerContext) -> None:
    """#36: the compiler's count of one game says "had 1 game with ...",
    never "had 1 games", and a one-row log is never headed "last 1 games"."""
    q = planned_query(
        GAMES_COUNTED,
        Reading(who=Who(kind="player", players=("Brandin Podziemski",)), lines=(Line(measure="points", value=28, as_typed="28 points", keyed=True),), measure=Measure(key="points", as_typed="points")),
    )
    out = _run(cx_ctx.con, q)
    assert out["rows"][0]["games"] == 1  # g3 (28) alone clears the line
    said = sentence(q, out)
    assert "had 1 game " in said, said
    assert "1 games" not in said
    # #257: the line is said as the counts say it, not raw.
    assert "with 28+ points" in said and ">=" not in said, said
    from dataclasses import replace

    from association.query.compose.sentence import _predicates

    chained = replace(q, predicates=[("triple_double", "=", True), ("points", ">=", 30), ("rebounds", "<", 5), ("won", "=", True)])
    assert _predicates(chained) == " with a triple-double and 30+ points and under 5 rebounds and won"


def test_a_run_is_still_going_only_in_the_season_on_record_now() -> None:
    """#297: the SQL's ``open`` says a run reached its partition's
    last game; "still going" is true of that only in the season on record
    now - a run that ended with a finished season, or at a retired player's
    last game, is not still going."""
    from association.nba.season import current_season
    from association.query.compose.runs import _settled_open
    from association.query.result import run_of

    now = current_season()
    row = {"length": 9, "first_day": "2024-03-20", "last_day": "2024-04-14", "first_season": 2024, "last_season": 2024, "open": True}
    finished, current = run_of(row), run_of({**row, "first_season": now, "last_season": now})
    assert (finished.still_open, current.still_open) == (True, True)  # the SQL's flag, before the rule
    assert [run.still_open for run in _settled_open((finished, current))] == [False, True]


def test_grouped_skeleton_reads_a_split(cx_ctx: AnswerContext) -> None:
    """``player_splits``' point: a record grouped by venue."""
    q = planned_query(PLAYER_SPLITS, Reading(who=Who(kind="player", players=("Brandin Podziemski",))))
    out = _run(cx_ctx.con, q)
    by_group = {r["group"]: r["games"] for r in out["rows"]}
    assert by_group == {"home": 3, "away": 1}  # g2, g3, g5 home; g1 away (g4 DNP excluded)


# ---------------------------------------------------------------------------
# The narrowing rules a fixture can exercise.
# ---------------------------------------------------------------------------


def test_a_bare_limit_is_filler_on_a_count_but_the_newest_n_on_a_log(cx_ctx: AnswerContext) -> None:
    """Rule 1: ``limit`` with no ``order`` means "the newest N" on a rows
    read and is dropped as filler everywhere else - a count reads every
    game in the span."""
    counted = _run(
        cx_ctx.con,
        planned_query(
            GAMES_COUNTED,
            Reading(
                who=Who(kind="player", players=("Brandin Podziemski",)),
                lines=(Line(measure="points", value=15, as_typed="15 points", keyed=True),),
                measure=Measure(key="points", as_typed="points"),
                window=Window(count=1),
            ),
        ),
    )
    assert counted["rows"][0]["games"] == 3  # the limit=1 filler does not cut this to one game
    logged = _run(cx_ctx.con, planned_query(PLAYER_LOG, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), window=Window(count=2))))
    assert [r["points"] for r in logged["rows"]] == [10, 28]  # g5, g3 - the newest two


def test_a_real_limit_with_no_order_is_refused_on_a_split(cx_ctx: AnswerContext) -> None:
    """The bare count's mirror image: a real count (>1) with no end on a
    split is "his last N games" - a window a player's splits refuse rather
    than silently answering for the whole span."""
    q = planned_query(PLAYER_SPLITS, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), window=Window(count=5)))
    with pytest.raises(Unsupported, match="a log's question"):
        compile_query(cx_ctx.con, q)


def test_a_team_beside_the_player_becomes_his_opponent_on_a_log(cx_ctx: AnswerContext) -> None:
    """On a ``rows`` read with no opponent already named, a team beside the
    player becomes his opponent."""
    q = planned_query(PLAYER_LOG, Reading(who=Who(kind="player", players=("Brandin Podziemski",), teams=("Boston Celtics",))))
    out = _run(cx_ctx.con, q)
    assert [r["points"] for r in out["rows"]] == [10, 20]  # g5, g1 - his two games against Boston, newest first


def test_a_team_beside_the_player_narrows_to_his_games_for_that_team_on_a_condition_skeleton(cx_ctx: AnswerContext) -> None:
    """On a condition skeleton (count, record, grouped) a team beside
    the player narrows to his games for that team - a no-op here, since he
    never played for anyone else, but the same code path a traded player's
    question would take."""
    with_team = _run(
        cx_ctx.con,
        planned_query(
            GAMES_COUNTED,
            Reading(
                who=Who(kind="player", players=("Brandin Podziemski",), teams=("Golden State Warriors",)),
                lines=(Line(measure="points", value=15, as_typed="15 points", keyed=True),),
                measure=Measure(key="points", as_typed="points"),
            ),
        ),
    )
    without_team = _run(
        cx_ctx.con,
        planned_query(
            GAMES_COUNTED,
            Reading(
                who=Who(kind="player", players=("Brandin Podziemski",)), lines=(Line(measure="points", value=15, as_typed="15 points", keyed=True),), measure=Measure(key="points", as_typed="points")
            ),
        ),
    )
    assert with_team["rows"][0]["games"] == without_team["rows"][0]["games"] == 3


def test_a_team_beside_the_player_is_ignored_entirely_on_a_per_game_average(cx_ctx: AnswerContext) -> None:
    """A player's line, a per-game average, reads no team beside the player
    at all: a team he never played for, beside a correct opponent, narrows
    nothing, never his averages to zero games."""
    q = planned_query(PLAYER_LINE, Reading(who=Who(kind="player", players=("Brandin Podziemski",), teams=("Detroit Pistons",)), cuts=Cuts(opponent="Boston Celtics")))
    out = _run(cx_ctx.con, q)
    row = out["rows"][0]
    assert row["games"] == 2  # g1 and g5, vs Boston - the bogus Pistons team narrows nothing
    assert row["points"] == pytest.approx(15.0)  # (20 + 10) / 2


def test_situation_narrows_to_the_named_weekday(cx_ctx: AnswerContext) -> None:
    """``situation`` is a cell of the relation: a weekday narrows the games
    to the ones played on it, over the whole career."""
    s = current_season()
    games = {"g1": date(s - 1, 11, 1), "g2": date(s - 1, 12, 1), "g3": date(s, 1, 10), "g5": date(s, 3, 1), "g6": date(s - 1, 2, 1), "g7": date(s - 1, 3, 1)}
    weekday = games["g3"].strftime("%A").lower()
    matching = {event for event, day in games.items() if day.strftime("%A").lower() == weekday}
    q = planned_query(PLAYER_LOG, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), cuts=Cuts(situation=situation_of(f"{weekday}s")), span=Span(career=True)))
    out = _run(cx_ctx.con, q)
    assert len(out["rows"]) == len(matching)
    assert out["rows"][0]["points"] == 28  # g3 is always in the match set (it defines the weekday)


def test_starter_bench_category_is_refused_outside_a_grouped_read(cx_ctx: AnswerContext) -> None:
    """``split: starter_bench`` names a category (a table of both
    halves), not a filter - refused on a ``rows``/``scalar`` read, honored
    only by a grouped read by starter."""
    q = planned_query(PLAYER_LOG, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), split="starter_bench"))
    with pytest.raises(Unsupported, match="a table of both halves"):
        compile_query(cx_ctx.con, q)


def test_an_unhonored_cell_is_refused(cx_ctx: AnswerContext) -> None:
    """A cell the relation cannot narrow by (``round``) is refused rather
    than silently dropped - by the planner, as the point is planned; the
    compile step keeps the same check for a query built by hand."""
    with pytest.raises(Unsupported, match="the finals cannot be read here"):
        planned_query(PLAYER_LOG, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), cuts=Cuts(round="finals")))


# ---------------------------------------------------------------------------
# The moves the words make of a point.
# ---------------------------------------------------------------------------


def test_a_measure_word_the_reading_holds_no_measure_for_moves_the_point(cx_ctx: AnswerContext) -> None:
    """A measure word in the question ("plus-minus") is added to a log's
    line even though the reading's measure says nothing about it."""
    q = move_point(cx_ctx.con, PLAYER_LOG, Reading(who=Who(kind="player", players=("Brandin Podziemski",))), "Podziemski's plus-minus each game this season")
    assert isinstance(q, Query)  # a named player never returns a TeamQuery
    out = _run(cx_ctx.con, q)
    g3 = next(r for r in out["rows"] if r["points"] == 28)
    assert g3["plusMinus"] == 17


def test_most_in_a_game_moves_to_rows_by_measure(cx_ctx: AnswerContext) -> None:
    """ "Career-high" moves a player's line to rows ordered by
    the named measure - the top game(s), not an average."""
    q = move_point(cx_ctx.con, PLAYER_LINE, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), measure=Measure(key="points", as_typed="points")), "Podziemski's career-high in points")
    assert isinstance(q, Query)  # a named player never returns a TeamQuery
    assert q.skeleton == "rows" and q.order == "measure"
    out = _run(cx_ctx.con, q)
    assert out["rows"][0]["points"] == 28  # g3, his career high


def test_how_many_won_is_a_career_count_with_a_predicate(cx_ctx: AnswerContext) -> None:
    """ "How many ... has he won" moves to a career count with the ``won``
    predicate, whatever season was (or was not) named - an unscoped count is
    a career's."""
    q = move_point(cx_ctx.con, LINE_RECORD, Reading(who=Who(kind="player", players=("Brandin Podziemski",))), "how many games has Podziemski's team won?")
    assert isinstance(q, Query)  # a named player never returns a TeamQuery
    assert q.span.career  # the scope itself carries it; the point settles no span of its own
    out = _run(cx_ctx.con, q)
    assert out["rows"][0]["games"] == 4  # g1, g3, g5, g7 - every win across both seasons (g4 DNP excluded)


def test_a_boolean_measure_on_a_per_game_shape_is_counted_never_averaged(cx_ctx: AnswerContext) -> None:
    """yardstick-v2 F055 "alperen sengun double-doubles vs southeast division
    career away" asks for a player's splits, whose figure is a per-game line;
    a double-double is a condition, so its only figure is the count, and
    ``_agg`` refuses the average (``AVG(<boolean>)``, which DuckDB cannot
    compute) outright, whatever move asked for it."""
    from association.query.compose.core import Unsupported, _agg

    scope = Reading(who=Who(kind="player", players=("Brandin Podziemski",)), cuts=Cuts(venue="away"), measure=Measure(key="double_double", as_typed="double_double"), span=Span(career=True))
    q = move_point(cx_ctx.con, PLAYER_SPLITS, scope, "Podziemski double-doubles career away")
    assert isinstance(q, Query) and q.skeleton == "scalar" and q.aggregate == "count" and ("double_double", "=", True) in q.predicates
    result = compose_answer(cx_ctx, PLAYER_SPLITS, scope, "Podziemski double-doubles career away")
    assert result is not None and "double-double" in result.text
    with pytest.raises(Unsupported):
        _agg("double_double", Aggregate.PER_GAME)
    with pytest.raises(Unsupported):
        _agg("triple_double", Aggregate.TOTAL)


def test_career_scope_forces_a_career_span_only_when_nothing_else_scoped_it() -> None:
    """The helper ``_move_how_many_won``/``_move_boolean_count`` share: no
    season, span or since means "his career"; any of the three left alone."""
    assert _career_scope(Reading(who=Who(kind="player", players=("X",)))) == Reading(who=Who(kind="player", players=("X",)), span=Span(career=True))
    assert _career_scope(Reading(who=Who(kind="player", players=("X",)), span=Span(season=2024))) == Reading(who=Who(kind="player", players=("X",)), span=Span(season=2024))
    assert _career_scope(Reading(who=Who(kind="player", players=("X",)), span=Span(career=True))) == Reading(who=Who(kind="player", players=("X",)), span=Span(career=True))
    assert _career_scope(Reading(who=Who(kind="player", players=("X",)), span=Span(since=2020))) == Reading(who=Who(kind="player", players=("X",)), span=Span(since=2020))


def test_a_ranking_word_with_no_player_groups_by_player_league_wide(cx_ctx: AnswerContext) -> None:
    """A ranking word with no player subject reads the league-wide grouped
    point, a ranking of players."""
    q = move_point(cx_ctx.con, None, Reading(), "top scorers this season")
    assert isinstance(q, Query)  # no team named in this fixture's own words
    assert q.subject == "everyone" and q.skeleton == "grouped" and q.group == "player"
    out = _run(cx_ctx.con, q)
    assert out["rows"][0]["group"] == "Derek Vollmer"
    assert out["rows"][0]["points"] == pytest.approx(30.0)


def test_a_position_word_with_no_player_reads_that_positions_log(cx_ctx: AnswerContext) -> None:
    """A position word with no player subject reads that group's log, rows -
    not a ranking, since no ranking word was asked for."""
    q = move_point(cx_ctx.con, None, Reading(), "centers game log this season")
    assert isinstance(q, Query)  # no team named in this fixture's own words
    assert q.subject == "everyone" and q.skeleton == "rows" and q.position == "C"
    out = _run(cx_ctx.con, q)
    assert {r["points"] for r in out["rows"]} == {18, 14}  # Sabonis - the only center on record


def test_the_questions_own_number_names_its_column_not_the_models_stat(cx_ctx: AnswerContext) -> None:
    """A threshold's own words ("30 point") name its column, even where the
    model's stat, on the reading's measure, names a different one entirely."""
    q = move_point(
        cx_ctx.con,
        GAMES_COUNTED,
        Reading(lines=(Line(measure="points", value=30, as_typed="30 point", keyed=True),), measure=Measure(key="rebounds", as_typed="rebounds")),
        "players with a 30 point game this season",
    )
    assert isinstance(q, Query)  # no team named in this fixture's own words
    assert q.predicates == [("points", ">=", 30)]
    out = _run(cx_ctx.con, q)
    assert out["rows"][0]["group"] == "Derek Vollmer"
    assert out["rows"][0]["games"] == 22


# ---------------------------------------------------------------------------
# The two guards on a league-wide point.
# ---------------------------------------------------------------------------


def test_a_team_subject_question_is_refused_not_ranked(cx_ctx: AnswerContext) -> None:
    """A period ("first half") is not this relation's question - refused,
    never answered with a ranking of players over the whole game."""
    with pytest.raises(Unsupported, match="period relation"):
        move_point(cx_ctx.con, None, Reading(), "least points scored by the Warriors in the first half this season")


def test_an_opponents_or_allowed_figure_is_refused_not_ranked(cx_ctx: AnswerContext) -> None:
    """A team's own figure - "allowed", "by team" - is the team relation's
    question; refused rather than answered as a ranking of players, which
    would silently substitute a different question."""
    with pytest.raises(Unsupported, match="team relation"):
        move_point(cx_ctx.con, None, Reading(), "most opponent bench points allowed by team this season")


# ---------------------------------------------------------------------------
# Refused vs. Unsupported, and answer().
# ---------------------------------------------------------------------------


def test_refused_carries_the_relations_own_wording(cx_ctx: AnswerContext) -> None:
    """A near miss the index will not settle alone ("Jemel Podziemski": a
    given name that is nobody's, beside one player's surname) is a handled
    refusal - the relation's own suggestion - not a bare "cannot answer": it
    comes back as :class:`Refused`, carrying the question back (a
    :class:`~association.query.result.Clarify` of near spellings), which the
    sayer words. A plain typo of one player ("Podzemski") is not this
    shape: it is read as him (entities.read_near_spelling)."""
    q = planned_query(PLAYER_LOG, Reading(who=Who(kind="player", players=("Jemel Podziemski",))))
    with pytest.raises(Refused) as excinfo:
        run(cx_ctx.con, q)
    assert "podziemski" in (say(excinfo.value.result).text or "").lower()


def test_a_name_nothing_resolves_to_is_the_reads_refusal(cx_ctx: AnswerContext) -> None:
    """A name with no near match at all (:func:`~association.query.entities.suggest_players`
    finds nothing) is the read's typed refusal, ``name_unmatched``, never a
    decline the user reads as "Nothing here answers this question: ... no
    player matching"."""
    q = planned_query(PLAYER_LOG, Reading(who=Who(kind="player", players=("Zzyzx Nobody",))))
    with pytest.raises(Refused) as caught:
        run(cx_ctx.con, q)
    outcome = caught.value.result
    assert isinstance(outcome, Refusal) and outcome.kind == "name_unmatched" and outcome.facts["asked"] == "Zzyzx Nobody"


def test_answer_returns_none_for_a_question_the_compiler_cannot_say(cx_ctx: AnswerContext) -> None:
    """``answer()`` reports "not a point on this relation" as ``None`` - a
    decline the planner says, not a refusal."""
    assert compose_answer(cx_ctx, PLAYER_LOG, Reading(), "some team's log") is None


def test_answer_composes_a_sentence_and_the_point_it_rests_on(cx_ctx: AnswerContext) -> None:
    """``answer()``'s Answer carries a sentence and the point's
    own values - what a caller checks an answer against. The question's
    "plus-minus" moves the point off a player's own line, so this is the
    compiler's sentence rather than the line reader's."""
    result = compose_answer(cx_ctx, PLAYER_LINE, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), cuts=Cuts(opponent="Boston Celtics")), "Podziemski's plus-minus vs Boston")
    assert result is not None
    assert "Brandin Podziemski" in result.text
    assert result.data["skeleton"] == "scalar"
    assert result.data["player"] == "Brandin Podziemski"
    assert result.data["rows"][0]["games"] == 2
    assert result.artifacts == []


def test_a_closed_range_is_named_as_one_and_counted_as_one(cx_ctx: AnswerContext) -> None:
    """``since``/``until`` bound the read at both ends, and the sentence says
    the range it counted - "(2025-2026)", not "(2025 on)" - so the stated
    scope matches the number (yardstick-v2 F036, where a 2024-2026 count read
    "2024 on"). One season named at both ends is named once."""
    s = current_season()
    # "PRA" moves the point off a player's own line, so the compiler's
    # own span phrase is what answers.
    both = compose_answer(
        cx_ctx, PLAYER_LINE, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), measure=Measure(key="points", as_typed="points"), span=Span(since=(s - 1), until=s)), "Podziemski's PRA"
    )
    assert both is not None
    assert f"({s - 1}-{s})" in both.text and "on)" not in both.text
    assert both.data["rows"][0]["games"] == 6
    one = compose_answer(
        cx_ctx,
        PLAYER_LINE,
        Reading(who=Who(kind="player", players=("Brandin Podziemski",)), measure=Measure(key="points", as_typed="points"), span=Span(since=(s - 1), until=(s - 1))),
        "Podziemski's PRA",
    )
    assert one is not None
    assert f"career ({s - 1})" in one.text
    assert one.data["rows"][0]["games"] == 2


def test_an_absence_is_read_on_the_other_side_where_the_man_was_never_a_teammate(cx_ctx: AnswerContext) -> None:
    """ "without X" is the games X
    missed on the subject's team - or, where X was never his teammate in
    the span and the games are narrowed to an opponent X played for, the
    games X missed on the OTHER side, settled where the name is resolved
    (``player_relation._absence_condition``) and said so. Jaylen Brown (Boston)
    played both of Podziemski's season-``s`` Boston games (g1, g5); a third
    Boston game he sat out is added, and it is the one game "without Jaylen
    Brown on the other side". A player on neither side keeps the own-side
    reading, whose empty answer names him as never the teammate."""
    s = current_season()
    cx_ctx.con.execute("INSERT INTO games VALUES (?, ?, 2, ?, ?, ?, ?, ?, ?)", ["g9", s, f"{s}-03-15T20:00Z", BOS, GS, 90, 101, GS])
    cx_ctx.con.execute(f"INSERT INTO player_box_stats VALUES ({', '.join('?' for _ in range(24))})", _box("g9", s, GS, BOS, PODZ, minutes=31, pts=24, reb=6, ast=5))
    result = compose_answer(
        cx_ctx,
        PLAYER_LOG,
        Reading(
            who=Who(kind="player", players=("Brandin Podziemski",)),
            cuts=Cuts(opponent="Boston"),
            companions=(Companion(player="Jaylen Brown", predicate="absent"),),
            span=Span(season=s, season_type=2),
        ),
        "podziemski game log vs boston without jaylen brown",
    )
    assert result is not None and [g["date"] for g in result.data["games"]] == [f"{s}-03-15"]
    assert "vs the Boston Celtics without Jaylen Brown on the other side" in (result.text or "")
    # A man on neither side: the own-side reading, and the honest empty answer.
    neither = compose_answer(
        cx_ctx,
        PLAYER_LOG,
        Reading(
            who=Who(kind="player", players=("Brandin Podziemski",)),
            cuts=Cuts(opponent="Boston"),
            companions=(Companion(player="Domantas Sabonis", predicate="absent"),),
            span=Span(season=s, season_type=2),
        ),
        "podziemski game log vs boston without sabonis",
    )
    assert neither is not None and "Domantas Sabonis was not Brandin Podziemski's teammate" in (neither.text or "")


def test_an_empty_splits_read_names_the_narrowing_not_a_phantom_did_not_play(cx_ctx: AnswerContext) -> None:
    """ "steph curry record vs lebron" with no meeting this season said
    "listed in 43 box scores but did not play in any of them" - a confident
    refusal naming the wrong missing fact. Podziemski's one Lakers game
    (g4) is a DNP, so his splits against them are empty, and the answer
    says that it is the narrowing that emptied them."""
    s = current_season()
    result = compose_answer(
        cx_ctx, PLAYER_SPLITS, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), cuts=Cuts(opponent="Lakers"), span=Span(season=s, season_type=2)), "podziemski record vs lakers"
    )
    assert result is not None and result.data["games"] == 0
    assert "none of them vs the Los Angeles Lakers" in (result.text or "") and "did not play in any" not in (result.text or "")


def test_a_plain_career_names_the_players_own_seasons_not_the_floor(cx_ctx: AnswerContext) -> None:
    """A career with no ``since``/``until`` named reads a named player's own
    first and last season on record, never the relation's floor - "(1994
    on)", true of every player and naming nothing about the one asked about
    (yardstick-v2 F061, "Sga games with under 14 fta in his whole career").
    The seasons are read by
    (:func:`~association.query.compose.core._player_own_seasons`, reusing
    :func:`~association.query.season_line.seasons_on_record`); the fixture's players are
    on record for seasons s-1 and s (``cx_ctx``'s ``player_season_stats``
    rows). A league-wide career - no one player's seasons to substitute -
    still reads the floor, so ``player_seasons`` stays unset for it."""
    s = current_season()
    # A count against an opponent, so the compiler's own sentence names the span.
    result = compose_answer(
        cx_ctx,
        GAMES_COUNTED,
        Reading(
            who=Who(kind="player", players=("Brandin Podziemski",)),
            cuts=Cuts(opponent="Detroit Pistons"),
            lines=(Line(measure="points", value=15, as_typed="15+ point", keyed=True),),
            measure=Measure(key="points", as_typed="points"),
            span=Span(career=True),
        ),
        "Podziemski's 15+ point games vs Detroit in his career",
    )
    assert result is not None
    assert f"({s - 1}-{s})" in result.text
    assert "1994" not in result.text
    # Against Detroit: g2 (15), g3 (28) and g7 (18) all clear 15 (see the
    # `_box` inserts above).
    assert result.data["rows"][0]["games"] == 3
    assert result.data["span"] == f"regular season career ({s - 1}-{s})"

    q = move_point(
        cx_ctx.con,
        GAMES_COUNTED,
        Reading(lines=(Line(measure="points", value=10, as_typed="10 points", keyed=True),), measure=Measure(key="rebounds", as_typed="rebounds"), span=Span(career=True)),
        "players with 10 points career",
    )
    assert isinstance(q, Query) and q.subject == "everyone"
    out = run(cx_ctx.con, q)
    assert out["player_seasons"] is None


def test_a_refusal_from_answer_is_the_relations_own(cx_ctx: AnswerContext) -> None:
    """A near-miss name is a handled refusal from ``answer()``, not ``None``."""
    result = compose_answer(cx_ctx, PLAYER_LOG, Reading(who=Who(kind="player", players=("Podzemski",))), "Podzemski's last 5 games")
    assert result is not None
    assert re.search(r"podziemski", result.text, re.I)


# ---------------------------------------------------------------------------
# The team as a subject - compose/team.py, and ``point.team_read_point``
# (``team_move_point`` above). A separate small fixture: the team-games relation reads FROM
# ``real_games`` (not ``games`` directly), which ``cx_ctx`` above never
# builds - team_games.py's own module docstring says why ``real_games`` is
# the shared filtered list, and calling ``real_games.build_table`` is what
# every other team fixture in this repo (``team_ctx``, ``team_cells_con``)
# does for the same reason.
# ---------------------------------------------------------------------------

_TS = current_season()


@pytest.fixture
def team_cx_ctx(tmp_path: Path) -> Iterator[AnswerContext]:
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
    insert_rows(
        c,
        "games",
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
    insert_rows(
        c,
        "team_season_stats",
        [
            (_TS, 2, "1", 82, 9200, 961),  # Orlando's regular-season total
            (_TS, 3, "1", 7, 780, 78),  # Orlando's finished playoff run
        ],
    )
    yield AnswerContext(con=c, out_dir=tmp_path)
    c.close()


def test_team_move_point_reads_an_unnarrowed_season_total(team_cx_ctx: AnswerContext) -> None:
    """F127's shape: "how many 3 pointers have the magic made" - a plain
    season total from ``team_season_stats``, not a per-game average, and the
    finished postseason is named too rather than left unmentioned."""
    q = team_move_point(
        team_cx_ctx.con,
        Reading(who=Who(kind="team", teams=("Orlando Magic",)), measure=Measure(key="threePointFieldGoalsMade", as_typed="threePointFieldGoalsMade")),
        "how many 3 pointers have the magic made so far this season",
    )
    assert isinstance(q, TeamQuery)
    result = run_team(team_cx_ctx.con, q)
    assert result.value == 961
    assert result.games == 82
    assert result.from_season_line
    assert "78" in result.note and "playoff" in result.note


def test_team_move_point_finds_a_team_the_reading_does_not_hold(team_cx_ctx: AnswerContext) -> None:
    """With no team on the reading, ``team_named_in`` reads "magic" from the
    text itself, the same restoration
    :func:`association.query.subject.players_named_in` makes for a dropped
    PLAYER."""
    q = team_move_point(team_cx_ctx.con, Reading(measure=Measure(key="threePointFieldGoalsMade", as_typed="threePointFieldGoalsMade")), "how many 3 pointers have the magic made so far this season")
    assert isinstance(q, TeamQuery)
    assert q.who.team == "Orlando Magic"


def test_team_move_point_is_not_fooled_by_magic_johnson(team_cx_ctx: AnswerContext) -> None:
    """ "Magic" is also Magic Johnson's given name - ``players_named_in``
    finds him from this exact question text, and the dropped-subject
    restoration would turn this into a question about him UNLESS the team
    reading is tried first (``move_point``'s own ordering, not just this
    function's)."""
    q = move_point(
        team_cx_ctx.con,
        PLAYER_RANKING,
        Reading(measure=Measure(key="threePointFieldGoalsMade", as_typed="threePointFieldGoalsMade"), span=Span(season_type=2)),
        "how many 3 pointers have the magic made so far this season",
    )
    assert isinstance(q, TeamQuery)
    assert q.who.player is None


def test_team_move_point_reads_a_narrowed_total(team_cx_ctx: AnswerContext) -> None:
    """F128's shape (one season type - the mixed-type "last N games" reader
    is the team log's own ``_team_game_log_mixed``, not this module's): from
    Toronto's own side, their last 3 regular-season games are r3 (110, a
    home loss to Cleveland), r4 (90, an away win) and r5 (115, a home win) -
    315 points, not the season's."""
    q = team_move_point(
        team_cx_ctx.con,
        Reading(who=Who(kind="team", teams=("Toronto Raptors",)), measure=Measure(key="points", as_typed="points"), span=Span(season_type=2), window=Window(order="recent", count=3)),
        "total points scored by the raptors in their last 3 games",
    )
    assert isinstance(q, TeamQuery)
    result = run_team(team_cx_ctx.con, q)
    assert result.value == 315
    assert result.games == 3
    assert not result.from_season_line


def test_a_teams_quarter_is_never_read_as_its_season_line(team_cx_ctx: AnswerContext) -> None:
    """A quarter or half narrows the team's games (the period relation's
    team half): "the magic's first-quarter threes" read as unnarrowed would
    answer their season's 961 - a whole-game total under a quarter's
    question. It goes to the narrowed reader, which sums game-level columns
    only, and declines a box-score count rather than answering the season."""
    for period in (Period(number=1), Period(number=2, half=True)):
        q = TeamQuery(who=Who(kind="team", teams=("Orlando Magic",)), span=Span(season_type=2), period=period, metric="threePointFieldGoalsMade")
        with pytest.raises(Unsupported):
            run_team(team_cx_ctx.con, q)


def test_team_move_point_reads_a_narrowed_differential(team_cx_ctx: AnswerContext) -> None:
    """F129's shape: Toronto's last 3 games (r3 110-120 L, r4 90-100 W, r5
    115-108 W) sum to a -13 differential (-10 -10 +7), not the season's."""
    q = team_move_point(
        team_cx_ctx.con,
        Reading(who=Who(kind="team", teams=("Toronto Raptors",)), measure=Measure(as_typed="pointsDifference"), span=Span(season_type=2), window=Window(order="recent", count=3)),
        "raptors point differential over their last 3 games",
    )
    assert isinstance(q, TeamQuery)
    result = run_team(team_cx_ctx.con, q)
    assert result.value == -13
    assert result.wins == 2 and result.losses == 1


def test_team_move_point_never_hijacks_a_player_ranking(team_cx_ctx: AnswerContext) -> None:
    """ "Who leads the Lakers in scoring" narrows a PLAYER ranking by team -
    it is not the team's own subject, and is left to the league-wide
    reading (``_everyone_point``'s own question) unchanged."""
    assert team_move_point(team_cx_ctx.con, Reading(who=Who(kind="team", teams=("Los Angeles Lakers",)), measure=Measure(key="points", as_typed="points")), "who leads the lakers in scoring") is None


def test_team_move_point_never_hijacks_a_named_player(team_cx_ctx: AnswerContext) -> None:
    """A player already named makes the team a narrowing of him, never the
    subject - ``move_point``'s own player path, untouched."""
    assert (
        team_move_point(
            team_cx_ctx.con,
            Reading(who=Who(kind="player", players=("Paolo Banchero",), teams=("Orlando Magic",)), measure=Measure(key="points", as_typed="points")),
            "how many points has banchero scored for the magic",
        )
        is None
    )


def test_team_move_point_ignores_an_any_team_placeholder(team_cx_ctx: AnswerContext) -> None:
    """ "rebounds allowed per team" with the team ``"any_team"`` - a
    placeholder, not a name, that ``_resolved_team`` RAISES for rather than
    returning a clarification - is no team's point: the question gets the
    decline the league-wide reading's ``_NOT_PLAYERS`` guard gives it
    ("allowed"), never a ``TeamQuery`` over a team nobody named."""
    q = team_move_point(
        team_cx_ctx.con, Reading(who=Who(kind="team", teams=("any_team",)), measure=Measure(key="rebounds", as_typed="rebounds"), span=Span(season_type=2)), "rebounds allowed per team"
    )
    assert q is None
    with pytest.raises(Unsupported, match="team relation"):
        move_point(
            team_cx_ctx.con,
            PLAYER_RANKING,
            Reading(who=Who(kind="team", teams=("any_team",)), measure=Measure(key="rebounds", as_typed="rebounds"), span=Span(season_type=2)),
            "rebounds allowed per team",
        )
    # Under a team's line the question is the team's own season. The point
    # reads the subject's kind as the parser read it: "any_team" is no team
    # the words name, so the league.
    named = Reading(who=Who(kind="team", teams=("any_team",)), measure=Measure(key="rebounds", as_typed="rebounds"), span=Span(season_type=2))
    assert move_point(
        team_cx_ctx.con, TEAM_LINE, Reading(who=Who(kind="team", teams=("any_team",)), measure=Measure(key="rebounds", as_typed="rebounds"), span=Span(season_type=2)), "rebounds allowed per team"
    ) == TeamSeasonQuery(**(replace(named, who=replace(named.who, kind="everyone"))).filters(), relation="team_seasons", shape="scalar")


def test_a_box_stat_count_narrowed_to_a_window_is_summed_over_the_games_box_rows(team_cx_ctx: AnswerContext) -> None:
    """#403: a box-score count (3-pointers made, not a game-outcome
    figure) narrowed to a window is summed over the narrowed games' own team
    box rows, and the games with no box row are said, since the total is
    short by them. A percentage is no
    total, and stays declined."""
    team_cx_ctx.con.execute("CREATE TABLE team_box_stats (event_id VARCHAR, season INTEGER, team_id VARCHAR, threePointFieldGoalsMade INTEGER, threePointFieldGoalsAttempted INTEGER)")
    # Toronto's last three: r3 and r4 carry a box row, r5 none.
    team_cx_ctx.con.executemany("INSERT INTO team_box_stats VALUES (?, ?, '3', ?, ?)", [("r3", _TS, 12, 30), ("r4", _TS, 9, 33), ("r2", _TS, 20, 40)])
    reading = Reading(
        who=Who(kind="team", teams=("Toronto Raptors",)),
        measure=Measure(key="threePointFieldGoalsMade", as_typed="threePointFieldGoalsMade"),
        span=Span(season_type=2),
        window=Window(order="recent", count=3),
    )
    q = team_move_point(team_cx_ctx.con, reading, "3 pointers made by the raptors in their last 3 games")
    assert isinstance(q, TeamQuery)
    result = run_team(team_cx_ctx.con, q)
    assert (result.value, result.games, result.blank) == (21.0, 3, 1)
    said = team_sentence(q, result)
    assert said.endswith("(1 of these games has no 3-pointers on record; the figure is over the rest.)"), said
    pct = team_move_point(
        team_cx_ctx.con, replace(reading, measure=Measure(key="threePointFieldGoalPct", as_typed="threePointFieldGoalPct")), "3 point percentage of the raptors in their last 3 games"
    )
    assert isinstance(pct, TeamQuery)
    with pytest.raises(Unsupported):
        run_team(team_cx_ctx.con, pct)


def test_a_figure_the_teams_opponents_had_is_summed_over_their_rows_of_its_games(team_cx_ctx: AnswerContext) -> None:
    """#425: "assists allowed by the mavericks this season" is the
    opponents' figure, never the team's OWN season total from
    ``team_season_stats``, which holds no row of what its opponents had. The typed measure says whose figure it is
    (``Measure.whose``), and the team compiler sums the opponents' box rows of
    the span's games - the planner's point names the stat as the team's own
    key, the measure beside it the opponent's - and says it as theirs, per
    game too, with the games that have no box row said. A total of points
    allowed, which the season line has no total of either, is the games'
    opponent scores."""
    team_cx_ctx.con.execute("CREATE TABLE team_box_stats (event_id VARCHAR, season INTEGER, team_id VARCHAR, threePointFieldGoalsMade INTEGER, threePointFieldGoalsAttempted INTEGER)")
    # Cleveland's rows of Toronto's games r1-r4 (r5 has none), and Toronto's
    # own, which an opponent's figure must not read.
    rows = [(e, _TS, "2", n, 30) for e, n in (("r1", 10), ("r2", 11), ("r3", 12), ("r4", 13))]
    insert_rows(team_cx_ctx.con, "team_box_stats", [*rows, ("r1", _TS, "3", 40, 50)])
    who, span = Who(kind="team", teams=("Toronto Raptors",)), Span(season=_TS, season_type=2)
    given_up = Measure(key="threePointFieldGoalsMade", as_typed="threes allowed", whose="opponent")
    q = TeamQuery(who=who, span=span, measure=given_up, metric="threePointFieldGoalsMade")
    result = run_team(team_cx_ctx.con, q)
    assert (result.value, result.games, result.blank, result.wins, result.losses) == (46.0, 5, 1, 4, 1)
    said = team_sentence(q, result)
    assert said.startswith(f"The Toronto Raptors' opponents had 46 3-pointers made against them over 5 games in the {_TS} regular season (4-1), 11.5 per game."), said
    assert said.endswith("(1 of these games has no 3-pointers on record; the figure is over the rest.)"), said
    allowed = TeamQuery(who=who, span=span, measure=Measure(key="points", as_typed="points allowed", whose="opponent", unit="total"), metric="points_allowed")
    result = run_team(team_cx_ctx.con, allowed)
    assert (result.value, result.games) == (513, 5)
    assert team_sentence(allowed, result) == f"The Toronto Raptors had 513 points allowed over 5 games in the {_TS} regular season (4-1)."
    # "steals allowed": the catalog holds no opponent's steals, so the
    # words' measure has no key, and the point read the team's own "steals"
    # beside it - answered, that is the team's own season total.
    unread = TeamQuery(who=Who(kind="team", teams=("Orlando Magic",)), span=span, measure=Measure(as_typed="steals allowed"), metric="threePointFieldGoalsMade")
    with pytest.raises(Unsupported, match="steals allowed"):
        run_team(team_cx_ctx.con, unread)


def test_a_team_point_refuses_a_cell_only_a_players_games_carry() -> None:
    """The planner holds a team point to the team relation's own cells,
    every shape, as an allow list: a cell only a player's games carry is
    refused on a team point, never ignored - "raptors game log in games
    where they made 5 threes in the first quarter" never lists every game.
    A cell built on one relation is refused on the other until it is built
    there. The cells a team's reader refuses with its own sentence (a
    teammate's absence is the with-and-without split's question) still reach
    that reader (its shape's row's ``taken``), and the team relation's own
    cells pass.

    The points are the team shapes the point reader writes (a log by date,
    the team compiler's sum, a run of wins, the splits), and the planner's
    check reads each shape's row."""
    from association.query.reading import Line, Period, Reading

    def team_point(shape: str, **cells: Any) -> Reading:
        by, skeleton = {"rows": ("date", "rows"), "scalar": ("", "scalar"), "runs": ("won", "runs"), "split": ("splits", "split")}[shape]
        return Reading(
            relation="team",
            read_as=PointShape("team_games", skeleton, by),  # type: ignore[arg-type]
            measures=(Measure(key="points"),),
            group="venue" if shape == "split" else "none",
            who=Who(kind="team", teams=("Orlando Magic",)),
            **cells,
        )

    line = Line(measure="threePointFieldGoalsMade", value=5, period=Period(number=1))
    banchero_out = (Companion(player="Paolo Banchero", predicate="absent"),)
    refused: list[tuple[str, dict[str, Any], str]] = [
        ("rows", {"lines": (line,)}, "games with 5+ 3-pointers in the 1st quarter"),
        ("scalar", {"lines": (line,)}, "games with 5+ 3-pointers in the 1st quarter"),
        ("rows", {"split": "starter"}, "the games as a starter"),
        ("scalar", {"companions": banchero_out}, "games without Paolo Banchero"),
        ("scalar", {"lines": (Line(measure="freeThrowsAttempted", op="<", value=14, as_typed="14 fta", narrows=True),)}, "games with under 14 fta"),
        ("scalar", {"cuts": Cuts(season_n=3)}, "the 3rd season of a career"),
        ("runs", {"lines": (line,)}, "games with 5+ 3-pointers in the 1st quarter"),
    ]
    for shape, cells, named in refused:
        with pytest.raises(Unsupported, match=rf"^{re.escape(named)} cannot be read over a team's games"):
            plan(team_point(shape, **cells))
    passes: list[tuple[str, dict[str, Any]]] = [
        ("scalar", {"cuts": Cuts(opponent="Boston", venue="home"), "window": Window(order="recent", count=10)}),
        ("rows", {"span": Span(since=2022, until=2024)}),
        ("split", {"split": "home_away"}),
        ("rows", {"companions": banchero_out}),  # the log's own reader says where that question belongs
    ]
    for shape, cells in passes:
        assert isinstance(plan(team_point(shape, **cells)), TeamQuery), (shape, cells)


def test_a_league_point_refuses_a_cell_the_league_read_has_no_subject_for() -> None:
    """The league-wide read (``player_relation.league_games``) narrows by an
    opponent, a team's roster, a venue, the lines, an ordinal season, a
    situation and the period - and has no subject for a date, a game of each
    series, a teammate, a starter half (any split), a tenure or a window of
    one subject's games. Those are refused by name, as both season types at
    once is, never dropped: "most points by a player on nov 22 since
    2015-2024" never ranks the range, "most blocks in a game 7" never lists
    the postseason's highs, "aaron gorodn last 5 games" (the name unread)
    never ranks the season."""
    from association.query.point import read_point
    from association.query.subject import Subject

    def league_ranking(scope: Reading, asked: PointShape = PLAYER_RANKING, question: str = "who led the league in points") -> Any:
        reading = replace(scope, asked=asked, subject=Subject("everyone"))
        return plan(read_point(reading, question))

    points = Measure(key="points", as_typed="points")
    refused: list[tuple[Reading, str]] = [
        (Reading(cuts=Cuts(date="2024-11-22"), measure=points), "the game on 2024-11-22"),
        (Reading(companions=(Companion(player="Jaylen Brown", predicate="absent"),), measure=points), "games without Jaylen Brown"),
        (Reading(measure=points, split="bench"), "the games off the bench"),
        (Reading(cuts=Cuts(tenure="Miami Heat"), measure=points), "games for Miami Heat"),
        (Reading(measure=points, window=Window(order="recent", count=5)), "the last 5 games"),
    ]
    for scope, named in refused:
        with pytest.raises(Unsupported, match=rf"^{re.escape(named)} cannot be read here"):
            league_ranking(scope)
    for scope, named in (
        (Reading(cuts=Cuts(game_n=7), measure=Measure(key="blocks", as_typed="blocks"), span=Span(season_type=3)), "game 7 of a series"),
        (Reading(cuts=Cuts(date="2001-02-02"), measure=points), "the game on 2001-02-02"),
    ):
        with pytest.raises(Unsupported, match=rf"^{re.escape(named)} cannot be read here"):
            league_ranking(scope, GAME_HIGHS, "most blocks in a game")
    # What the league read does narrow by still plans.
    for scope in (Reading(cuts=Cuts(opponent="Boston Celtics"), measure=points), Reading(cuts=Cuts(venue="home"), measure=points), Reading(cuts=Cuts(season_n=15), measure=points)):
        assert isinstance(league_ranking(scope), Query), scope
        assert isinstance(league_ranking(scope, GAME_HIGHS, "most points in a game"), Query), scope


def test_answer_composes_a_team_subject_sentence(team_cx_ctx: AnswerContext) -> None:
    """``answer()``'s dispatch to the team subject, end to end - the same
    surface :func:`association.query.agent.Agent._try_compose` calls."""
    result = compose_answer(
        team_cx_ctx,
        PLAYER_RANKING,
        Reading(who=Who(kind="team", teams=("Orlando Magic",)), measure=Measure(key="threePointFieldGoalsMade", as_typed="threePointFieldGoalsMade")),
        "how many 3 pointers have the magic made so far this season",
    )
    assert result is not None
    assert "961" in result.text
    assert result.data["team"] == "Orlando Magic"
    assert result.data["from_season_line"] is True
    assert result.artifacts == []


# ---------------------------------------------------------------------------
# Coverage floors and caveats (#197). Both subjects, checked against the real nba/coverage.py floors
# (independent of what either fixture's own tables hold, since the refusal
# fires before any query against them runs).
# ---------------------------------------------------------------------------


def test_answer_refuses_a_season_under_the_players_coverage_floor(cx_ctx: AnswerContext) -> None:
    """Box scores (and so a count of games over a line) reach back only to
    1994: a season under the floor is refused, never answered with an empty
    result as confidently as a real one."""
    result = compose_answer(
        cx_ctx,
        GAMES_COUNTED,
        Reading(
            who=Who(kind="player", players=("Brandin Podziemski",)),
            lines=(Line(measure="points", value=15, as_typed="15+ point", keyed=True),),
            measure=Measure(key="points", as_typed="points"),
            span=Span(season=1990),
        ),
        "Podziemski's 15+ point games in 1990",
    )
    assert result is not None
    assert "1994" in result.text and "1990" in result.text


def test_answer_carries_a_coverage_caveat_for_a_partly_covered_season(cx_ctx: AnswerContext) -> None:
    """A season the floor reaches but only partly still answers - #197's
    caveat, not a refusal - with the note appended past the sentence.
    ESPN's 2001 postseason is missing ten games (`association.nba.coverage`),
    which is a real caveat this fixture's own 1990/1990 games say nothing
    about; the assertion is that the caveat function ran and found nothing
    to say for a season it does not cover a note for, proving the call site
    exists without needing to rebuild that exact gap in a tiny fixture."""
    result = compose_answer(
        cx_ctx,
        GAMES_COUNTED,
        Reading(who=Who(kind="player", players=("Brandin Podziemski",)), lines=(Line(measure="points", value=15, as_typed="15+ point", keyed=True),), measure=Measure(key="points", as_typed="points")),
        "Podziemski's 15+ point games this season",
    )
    assert result is not None
    assert "Note:" not in result.text  # the current season carries no caveat - proves one is not added where none applies


def test_answer_refuses_a_season_under_the_teams_coverage_floor(team_cx_ctx: AnswerContext) -> None:
    """``team_season_stats`` reaches back only to 1994 - #197's gap on the
    team subject's own unnarrowed (season-line) reader."""
    result = compose_answer(
        team_cx_ctx,
        PLAYER_RANKING,
        Reading(who=Who(kind="team", teams=("Orlando Magic",)), measure=Measure(key="threePointFieldGoalsMade", as_typed="threePointFieldGoalsMade"), span=Span(season=1990)),
        "how many 3 pointers did the magic make in 1990",
    )
    assert result is not None
    assert "1994" in result.text and "1990" in result.text


def test_answer_carries_a_coverage_caveat_on_a_narrowed_team_question(team_cx_ctx: AnswerContext) -> None:
    """The narrowed (game-level) team reader carries the same caveat call -
    ESPN's 2001 postseason gap does not touch this fixture's games, so the
    assertion is that answering a real, covered narrowed question adds no
    spurious note."""
    result = compose_answer(
        team_cx_ctx,
        PLAYER_LOG,
        Reading(who=Who(kind="team", teams=("Toronto Raptors",)), measure=Measure(key="points", as_typed="points"), span=Span(season_type=2), window=Window(order="recent", count=3)),
        "total points scored by the raptors in their last 3 games",
    )
    assert result is not None
    assert "Note:" not in result.text


# ---------------------------------------------------------------------------
# #199, F124: a ranking of the GAMES that satisfy a boolean measure by
# another measure ("highest scoring triple doubles"), not a per-player count
# or average.
# ---------------------------------------------------------------------------


def test_a_highest_scoring_boolean_measure_ranks_the_games_not_a_per_player_average(cx_ctx: AnswerContext) -> None:
    """ "Players with the highest scoring triple doubles" is rows over
    everyone, ordered by points, with `triple_double` as a predicate - not
    `_everyone_ranking`'s per-player AVERAGE (which would need
    `minimum_games` triple-doubles just to rank anyone). Podziemski's g3
    (28/10/11) is the fixture's only triple-double."""
    q = move_point(
        cx_ctx.con,
        PLAYER_RANKING,
        Reading(measure=Measure(key="triple_double", as_typed="triple_double"), span=Span(season_type=2), window=Window(count=10)),
        "players with the highest scoring triple doubles",
    )
    assert isinstance(q, Query)
    assert q.subject == "everyone" and q.skeleton == "rows" and q.order == "measure"
    assert q.predicates == [("triple_double", "=", True)]
    assert q.measures[0] == "points"
    out = run(cx_ctx.con, q)
    assert [r["points"] for r in out["rows"]] == [28]  # Podziemski's g3, the fixture's only triple-double


def test_biggest_with_no_stat_word_defaults_to_points(cx_ctx: AnswerContext) -> None:
    """ "Biggest triple double" names no stat word at all -
    :func:`~association.query.point._boolean_game_measure` falls
    back to points, the same default a "career-high" question gets."""
    # "ever" is the span tagger's reading (lexicon.CAREER_WORDS), in the
    # stages before the point is read; the point reader reads no span of
    # its own, so the reading carries what the stages would.
    q = move_point(cx_ctx.con, PLAYER_RANKING, Reading(measure=Measure(key="triple_double", as_typed="triple_double"), span=Span(season_type=2, career=True)), "biggest triple double ever")
    assert isinstance(q, Query)
    assert q.measures[0] == "points"
    assert q.span.career


# ---------------------------------------------------------------------------
# F161: several "<N> <stat>" lines in one question at once - a league-wide
# READ OF THE GAMES clearing every line, not `point._everyone_threshold_count`'s
# per-player COUNT of a single one.
# ---------------------------------------------------------------------------


def test_several_number_stat_lines_read_the_qualifying_games_not_a_count(cx_ctx: AnswerContext) -> None:
    """ "33 point and 13 rebound and 10 assist 2 blocks and 2 steals" (F161)
    reads every "<N> <stat>" pair in the question into predicates and lists
    the games clearing all of them - rows over everyone, not a per-player
    count. This fixture's own numbers: exactly two season-``s`` games clear
    20+ points, 7+ rebounds and 3+ assists at once (Brown's g5 and
    Podziemski's g3) - the 20-point-guards ranking pool (6 rebounds apiece)
    and the two players' own lower-rebound games fall short of the 7."""
    q = move_point(
        cx_ctx.con,
        GAMES_COUNTED,
        Reading(
            lines=(
                Line(measure="points", value=20, as_typed="20 points", keyed=True),
                Line(measure="rebounds", value=7, as_typed="7 rebounds"),
                Line(measure="assists", value=3, as_typed="3 assists"),
            ),
            span=Span(season_type=2),
        ),
        "players with 20 points and 7 rebounds and 3 assists this season",
    )
    assert isinstance(q, Query)
    assert q.subject == "everyone" and q.skeleton == "rows"
    assert sorted(q.predicates) == sorted([("points", ">=", 20), ("rebounds", ">=", 7), ("assists", ">=", 3)])
    out = run(cx_ctx.con, q)
    assert sorted(r["points"] for r in out["rows"]) == [28, 31]


def test_a_league_wide_read_since_a_season_reaches_every_season_from_it(cx_ctx: AnswerContext) -> None:
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
    this_season = move_point(
        cx_ctx.con,
        GAMES_COUNTED,
        Reading(lines=(Line(measure="rebounds", value=12, as_typed="12 rebounds", keyed=True), Line(measure="assists", value=5, as_typed="5 assists")), span=Span(season_type=2)),
        question,
    )
    assert isinstance(this_season, Query) and this_season.subject == "everyone" and this_season.skeleton == "rows"
    assert [r["rebounds"] for r in run(cx_ctx.con, this_season)["rows"]] == [12]
    since = move_point(
        cx_ctx.con,
        GAMES_COUNTED,
        Reading(lines=(Line(measure="rebounds", value=12, as_typed="12 rebounds", keyed=True), Line(measure="assists", value=5, as_typed="5 assists")), span=Span(season_type=2, since=s - 1)),
        question,
    )
    assert isinstance(since, Query)
    out = run(cx_ctx.con, since)
    assert sorted(r["rebounds"] for r in out["rows"]) == [12, 14]
    assert f"({s - 1} on)" in sentence(since, out)
    until = move_point(
        cx_ctx.con,
        GAMES_COUNTED,
        Reading(
            lines=(Line(measure="rebounds", value=12, as_typed="12 rebounds", keyed=True), Line(measure="assists", value=5, as_typed="5 assists")), span=Span(season_type=2, since=s - 1, until=s - 1)
        ),
        question,
    )
    assert isinstance(until, Query)
    assert [r["rebounds"] for r in run(cx_ctx.con, until)["rows"]] == [14]
    # A named season beside ``since`` is the contradiction ``_span_of``
    # refuses for a player, and the compiler declines it the same way.
    both = move_point(
        cx_ctx.con,
        GAMES_COUNTED,
        Reading(
            lines=(Line(measure="rebounds", value=12, as_typed="12 rebounds", keyed=True), Line(measure="assists", value=5, as_typed="5 assists")), span=Span(season=s, season_type=2, since=s - 1)
        ),
        question,
    )
    assert isinstance(both, Query)
    with pytest.raises(Unsupported, match="since"):
        run(cx_ctx.con, both)


def test_a_single_number_stat_line_still_counts_by_player(cx_ctx: AnswerContext) -> None:
    """One line only is still :func:`~association.query.point._everyone_threshold_count`'s
    ordinary per-player COUNT shape - the multi-line move stands aside for
    it (F161's move applies only once there are two or more lines to read).
    The measure names a DIFFERENT column than the phrase itself on purpose
    (the same shape ``test_the_questions_own_number_names_its_column_not_the_models_stat``
    exercises): ``point._everyone_threshold_predicates`` adds no second,
    redundant line where the measure names the same column the phrase does."""
    q = move_point(
        cx_ctx.con,
        GAMES_COUNTED,
        Reading(lines=(Line(measure="points", value=15, as_typed="15 points", keyed=True),), measure=Measure(key="rebounds", as_typed="rebounds")),
        "players with 15 points this season",
    )
    assert isinstance(q, Query)
    assert q.subject == "everyone" and q.skeleton == "grouped" and q.group == "player"


def test_a_league_wide_count_with_no_line_at_all_is_still_refused(cx_ctx: AnswerContext) -> None:
    """The guard the multi-line move does not weaken: a league-wide count of
    games naming no line at all - not even in the question's own text - is
    refused, not turned into a whole-league listing."""
    with pytest.raises(PointRefused) as refused:
        move_point(cx_ctx.con, GAMES_COUNTED, Reading(), "players with a good game this season")
    assert refused.value.cause == Cause(kind="needs_line")


# ---------------------------------------------------------------------------
# F056: a position word ("shooting guard") is a position-GROUP subject, not
# a name to resolve; a "with at least N <unit>" phrase is the ranking's own minimum
# sample, applied only where the relation can (a games floor), refused by
# name otherwise (an attempts or minutes floor, which it cannot apply yet).
# ---------------------------------------------------------------------------


def test_a_position_group_subject_reads_that_positions_ranking(cx_ctx: AnswerContext) -> None:
    """F056 ("highest 3 point percentage in a season. by a shooting guard
    with at least 400 attempts"), the shape: a position group as the
    subject is read off the subject reading, never resolved as a player's
    name. Curry ('PG') is
    the fixture's only point guard; a "with at least 2 games" floor
    (``Filtered.minimum``) clears his 4 games past the real
    ``PER_GAME_MIN_GAMES`` default, which he does not reach on his own."""
    q = move_point(
        cx_ctx.con, PLAYER_RANKING, Reading(measure=Measure(key="points", as_typed="points"), span=Span(season_type=2)), "highest points per game this season by a point guard with at least 2 games"
    )
    assert isinstance(q, Query)
    assert q.subject == "everyone" and q.position == "PG" and q.minimum_games == 2
    out = run(cx_ctx.con, q)
    assert out["rows"][0]["group"] == "Stephen Curry"
    assert out["rows"][0]["games"] == 4
    assert out["rows"][0]["points"] == pytest.approx(30.5)  # (35 + 40 + 25 + 22) / 4


def _ranked(con: duckdb.DuckDBPyConnection, q: Query) -> str:
    """A game-level ranking as the answer says it: its reader, then its sayer
    (decision D17; the compiler's own sentence said it until then)."""
    read = read_game_ranking(con, q)
    assert isinstance(read, Result), read
    return say(read).text or ""


@pytest.mark.parametrize("floor", ["minimum 2 games played", "at least 2 gp", "with a minimum of 2 games"])
def test_a_stated_minimum_in_any_wording_is_the_rankings_qualifier_said_as_asked(cx_ctx: AnswerContext, floor: str) -> None:
    """#377: "minimum N games played", "at least N gp" and "a
    minimum of N games" are the qualifier "with at least N games" is. The
    sentence says it as the default is said, and records no decision: the
    words asked for it. Above everyone's games, the ranking is empty, and
    says the minimum emptied it - never "no games"."""
    q = move_point(cx_ctx.con, PLAYER_RANKING, Reading(measure=Measure(key="points", as_typed="points"), span=Span(season_type=2)), f"highest points per game this season by a point guard {floor}")
    assert isinstance(q, Query) and q.minimum_games == 2 and q.minimum is not None
    with collect() as remarks:
        said = _ranked(cx_ctx.con, q)
    assert ", minimum 2 games)" in said and not remarks.decisions
    emptied = move_point(
        cx_ctx.con,
        PLAYER_RANKING,
        Reading(measure=Measure(key="points", as_typed="points"), span=Span(season_type=2)),
        f"highest points per game this season by a point guard {floor.replace('2', '5')}",
    )
    assert isinstance(emptied, Query)
    assert _ranked(cx_ctx.con, emptied).startswith("No player played 5 games in the ")


def test_a_stated_minimum_is_the_qualifier_where_the_season_lines_ranking_is_read_at_the_game_level(cx_ctx: AnswerContext) -> None:
    """A ranking the season line cannot read (narrowed to one opponent) is
    planned as the game-level ranking: a minimum the words state in games
    is its qualifier there too, and one in another unit is refused by name:
    "most points vs boston this season minimum 2 games" ranks at 2 games,
    never at the season line's default 20, and an attempts floor is never
    dropped under a heading saying "minimum 20 games"."""
    scope = Reading(measure=Measure(key="points", as_typed="points"), span=Span(season_type=2), cuts=Cuts(opponent="Boston Celtics"))
    q = move_point(cx_ctx.con, PLAYER_RANKING, scope, "most points vs boston this season minimum 2 games")
    assert isinstance(q, Query) and q.source == "games" and q.minimum_games == 2
    with collect() as remarks:
        said = _ranked(cx_ctx.con, q)
    assert said.startswith("every player vs the Boston Celtics, ") and ", minimum 2 games)" in said and not remarks.decisions
    assert [line.split()[:2] for line in said.splitlines()[1:]] == [["Stephen", "Curry"], ["Brandin", "Podziemski"]]
    # An attempts floor the season line qualifies 3-point percentage by, and
    # the game-level ranking cannot apply.
    threes = replace(scope, measure=Measure(key="threePointFieldGoalPct", as_typed="3pt%"))
    with pytest.raises(Refused) as refused:
        move_point(cx_ctx.con, PLAYER_RANKING, threes, "highest 3pt% vs boston this season minimum 20 attempts")
    assert refused.value.result == refusal_of(Cause(kind="ranking_floor_unit", facts={"unit": "attempts", "count": 20}))


def test_a_default_minimum_no_player_met_is_scaled_to_the_pool_and_said_as_a_decision(cx_ctx: AnswerContext) -> None:
    """#422: the default qualifier (20 games, a regular season's) over the
    games against one team would keep every player out and say "No games
    for every player vs the Boston Celtics" about games that exist. Where it
    keeps everyone out it is scaled to half the most games
    any player in the pool played (two here, so one), said with why and
    recorded as the ``minimum`` decision; a stated minimum is never scaled,
    and a pool with no games still says so."""
    scope = Reading(measure=Measure(key="points", as_typed="points"), span=Span(season_type=2), cuts=Cuts(opponent="Boston Celtics"))
    q = move_point(cx_ctx.con, PLAYER_RANKING, scope, "most points vs boston this season")
    assert isinstance(q, Query) and q.minimum_games == 20 and q.minimum is None
    with collect() as remarks:
        said = _ranked(cx_ctx.con, q)
    assert said.startswith("every player vs the Boston Celtics, ")
    assert ", minimum 1 game: none played the usual 20 here, so half the most any did (2) - state a minimum to set another)" in said
    assert [line.split()[:2] for line in said.splitlines()[1:]] == [["Stephen", "Curry"], ["Brandin", "Podziemski"]]
    assert [(d.kind, d.after, d.instead_of) for d in remarks.decisions] == [("minimum", 1, (20,))]
    stated = move_point(cx_ctx.con, PLAYER_RANKING, scope, "most points vs boston this season minimum 3 games")
    assert isinstance(stated, Query)
    assert _ranked(cx_ctx.con, stated).startswith("No player vs the Boston Celtics played 3 games in the ")
    nobody = move_point(cx_ctx.con, PLAYER_RANKING, replace(scope, cuts=Cuts(opponent="Los Angeles Lakers"), span=Span(season_type=3)), "most points vs the lakers in the playoffs")
    assert isinstance(nobody, Query)
    assert _ranked(cx_ctx.con, nobody).startswith("No games for every player vs the Los Angeles Lakers in the ")


def test_a_ranking_by_games_played_counts_each_players_games(cx_ctx: AnswerContext) -> None:
    """#408: a ranking by games played counts each player's games, never
    ranks by points per game, says so, and adds no total of them."""
    q = move_point(cx_ctx.con, PLAYER_RANKING, Reading(measure=Measure(key="games_played", as_typed="games played"), span=Span(season_type=2)), "most games played this season")
    assert isinstance(q, Query) and q.aggregate == "count" and q.measures == [] and q.minimum_games is None
    said = _ranked(cx_ctx.con, q)
    assert said.startswith("every player, ") and ", by player (games played):" in said and "Total" not in said
    assert [line.split()[-2] for line in said.splitlines()[1:]] == ["22", "22", "4", "4", "2", "2"]


@pytest.mark.parametrize(("key", "label"), [("defensiveRebounds", "DREB"), ("offensiveRebounds", "OREB")])
def test_a_rebound_side_is_said_by_its_abbreviation_not_its_column(cx_ctx: AnswerContext, key: str, label: str) -> None:
    """The compiler's sentence says a rebound side by its label, as every
    other box-score measure, never the column name ("by player
    (defensiveRebounds per game ...", "top 3 by offensiveRebounds") - and a
    stated minimum of one game in the singular, never "minimum 1 games"."""
    scope = Reading(measure=Measure(key=key, as_typed=key), span=Span(season_type=2), cuts=Cuts(opponent="Boston Celtics"))
    q = move_point(cx_ctx.con, PLAYER_RANKING, scope, "most rebounds vs boston this season minimum 1 game")
    assert isinstance(q, Query)
    said = _ranked(cx_ctx.con, q)
    assert f"({label} per game, minimum 1 game)" in said and key not in said


def test_a_stated_minimum_on_a_point_that_ranks_no_players_is_refused_by_name(cx_ctx: AnswerContext) -> None:
    """A qualifier keeps the players a ranking ranks: under a single game's
    high it keeps nothing, so it is refused by a cause naming the floor
    asked rather than answered past (#377)."""
    with pytest.raises(PointRefused) as refused:
        move_point(cx_ctx.con, PLAYER_RANKING, Reading(measure=Measure(key="points", as_typed="points"), span=Span(season_type=2)), "most points in a game this season minimum 20 games")
    assert refused.value.cause == Cause(kind="minimum_unranked", facts={"unit": "games", "count": 20})
    assert "20 games" in refusal_result(refused.value.cause).text


def test_an_attempts_or_minutes_floor_is_refused_by_name_not_dropped_or_misapplied(cx_ctx: AnswerContext) -> None:
    """F056's own shape: "... with at least 100 attempts" names a floor the
    relation has no HAVING clause for yet (only a minimum GAMES count,
    :data:`~association.query.compose.core.Query.minimum_games`) - refused
    by name, never silently dropped (which would rank on an unqualified
    sample) and never misread as a games count (100 attempts is not 100
    games). The reader gives the cause, and the planner says it."""
    with pytest.raises(PointRefused) as refused:
        move_point(
            cx_ctx.con, PLAYER_RANKING, Reading(measure=Measure(key="points", as_typed="points"), span=Span(season_type=2)), "highest points per game by a point guard with at least 100 attempts"
        )
    assert refused.value.cause == Cause(kind="ranking_floor_unit", facts={"unit": "attempts", "count": 100})
    said = refusal_result(refused.value.cause).text
    assert "100 attempts" in said and "at least N games" in said


def test_a_stat_this_relation_cannot_read_is_refused_not_defaulted_to_points(cx_ctx: AnswerContext) -> None:
    """ "Who had the highest netpoints game this season" (no player named, so
    the league-wide reading) named a real stat - NetPoints - this relation
    has no measure for. It is refused by name, never ranked silently by
    points (``_everyone_ranking``'s default), which would look like a real
    ranking of what was asked for and not be one. A
    ranking with no stat named at all still defaults to points - only a
    stat that was NAMED and failed to map is a refusal."""
    with pytest.raises(PointRefused) as refused:
        move_point(cx_ctx.con, GAME_HIGHS, Reading(measure=Measure(key="netpoints", as_typed="netpoints")), "who had the highest netpoints game this season")
    assert refused.value.cause == Cause(kind="no_ranking_measure", facts={"stat": "netpoints"})
    assert "netpoints" in refusal_result(refused.value.cause).text
    # A ranking of players by NetPoints is the season line's (its reader,
    # over the NetPoints tables): a point, not a refusal.
    netpoints = move_point(cx_ctx.con, PLAYER_RANKING, Reading(measure=Measure(key="netpoints", as_typed="netpoints")), "who leads the league in netpoints this season")
    assert isinstance(netpoints, Query) and netpoints.source == "seasons" and netpoints.subject == "everyone"
    # No stat named at all is still the plain "top scorers" default.
    q = move_point(cx_ctx.con, PLAYER_RANKING, Reading(), "who are the top scorers this season")
    assert isinstance(q, Query)
    assert q.measures == ["points"]


def test_at_least_does_not_flip_a_highest_ranking_to_ascending() -> None:
    """The minimum-sample phrase "at least" ("with at least 40 games") is
    not the ascending word "least" ("the least points"): an "at least N ..."
    floor never reverses a "highest ..." ranking's sort order (a
    3-point-percentage ranking "with at least 40 games" is highest-first)."""
    assert _asc_or_desc("highest points per game with at least 40 games") == "desc"
    assert _asc_or_desc("fewest points per game") == "asc"
    assert _asc_or_desc("lowest 3-point percentage with at least 40 games") == "asc"
    assert _asc_or_desc("the least points scored") == "asc"


# ---------------------------------------------------------------------------
# #197, the box-score-caveat half: `core.run` carries the box-score notes -
# a teammate's absence, the empty lines left out of a count, rebuilt lines,
# and a career predating box scores.
# ---------------------------------------------------------------------------


def _add_empty_box_score(con: duckdb.DuckDBPyConnection, event: str, season: int, team: str, opponent: str, athlete: str, tip: str) -> None:
    """A game with a REAL box-score row for ``athlete`` that ESPN served
    empty - listed (``did_not_play`` False), every stat NULL - the fault
    box-score notes' "Not counted" note is about (AGENTS.md, "Whole
    team-seasons of box scores are empty"). Distinct from `_box(..., dnp=True)`,
    which is an ordinary did-not-play entry and is excluded from the
    relation entirely (``Narrowed.base``'s own ``NOT pgl.did_not_play``), so
    it can never be "not counted"."""
    con.execute("INSERT INTO games VALUES (?, ?, 2, ?, ?, ?, ?, ?, ?)", [event, season, tip, team, opponent, 100, 90, team])
    con.execute(f"INSERT INTO player_box_stats VALUES ({', '.join('?' for _ in range(24))})", (event, season, 2, team, opponent, athlete, False, *([None] * 17)))


def test_run_carries_the_not_counted_box_score_note(cx_ctx: AnswerContext) -> None:
    """A log says how many of the span's games it left out for
    an empty box score - ESPN listing Podziemski with no minutes or stats
    at all, not a did-not-play entry (:func:`_add_empty_box_score`)."""
    s = current_season()
    _add_empty_box_score(cx_ctx.con, "g8", s, GS, DET, PODZ, f"{s}-03-15T20:00Z")
    q = planned_query(PLAYER_LOG, Reading(who=Who(kind="player", players=("Brandin Podziemski",))))
    out = run(cx_ctx.con, q)
    assert any("Not counted: 1 game" in note for note in out["notes"])


def test_answer_appends_the_box_score_notes_to_the_sentence(cx_ctx: AnswerContext) -> None:
    """``answer()`` appends ``out["notes"]`` to the sentence the same way it
    already appends the coverage caveat, and carries the same list on
    ``data`` for a caller that reads values rather than the prose."""
    s = current_season()
    _add_empty_box_score(cx_ctx.con, "g8", s, GS, DET, PODZ, f"{s}-03-15T20:00Z")
    result = compose_answer(cx_ctx, PLAYER_LOG, Reading(who=Who(kind="player", players=("Brandin Podziemski",))), "Podziemski's game log this season")
    assert result is not None
    assert "Not counted: 1 game" in result.text
    assert any("Not counted: 1 game" in note for note in result.data["notes"])


def test_a_career_predating_box_scores_gets_the_floor_note(cx_ctx: AnswerContext) -> None:
    """A career reaching further back than box scores do (the real 1994
    floor, `nba.coverage` - not derived from this fixture) says so.
    Podziemski's `player_season_stats_deduped` row for 1990, added here
    only, is what makes his earliest season on record predate the floor. The
    matching raw-table row keeps `_player_own_seasons` (`compose.core`, which
    reads `player_season_stats`) agreeing with it."""
    cx_ctx.con.execute("INSERT INTO player_season_stats_deduped VALUES ('10', 1990, 2, 10)")
    cx_ctx.con.execute("INSERT INTO player_season_stats VALUES ('10', 1990, 2, NULL, 10, NULL)")
    q = planned_query(PLAYER_LOG, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), span=Span(career=True)))
    out = run(cx_ctx.con, q)
    assert any("Box scores begin with the 1993-94 season" in note and "1990-1993" in note for note in out["notes"])


def test_no_career_floor_note_when_the_season_is_defaulted_not_career(cx_ctx: AnswerContext) -> None:
    """The floor note is a CAREER note - it says nothing about a plain
    current-season read, even with the same older row on record."""
    cx_ctx.con.execute("INSERT INTO player_season_stats_deduped VALUES ('10', 1990, 2, 10)")
    cx_ctx.con.execute("INSERT INTO player_season_stats VALUES ('10', 1990, 2, NULL, 10, NULL)")
    q = planned_query(PLAYER_LOG, Reading(who=Who(kind="player", players=("Brandin Podziemski",))))
    out = run(cx_ctx.con, q)
    assert not any("Box scores begin with" in note for note in out["notes"])


def test_a_scalar_or_grouped_read_carries_no_leaked_rebuilt_shown_column(cx_ctx: AnswerContext) -> None:
    """The scratch ``rebuilt_shown`` column :func:`~association.query.compose.core._scalar_selects`
    adds for the box-score notes is popped back off before the rows reach a
    caller, for a named player (a `scalar` read) and for the league-wide
    subject (a `grouped` read, which has no box-score notes of its own to
    read it for at all) alike."""
    named = run(
        cx_ctx.con,
        planned_query(
            GAMES_COUNTED,
            Reading(
                who=Who(kind="player", players=("Brandin Podziemski",)), lines=(Line(measure="points", value=15, as_typed="15 points", keyed=True),), measure=Measure(key="points", as_typed="points")
            ),
        ),
    )
    assert "rebuilt_shown" not in named["rows"][0]
    q = move_point(cx_ctx.con, None, Reading(), "top scorers this season")
    assert isinstance(q, Query)  # no team named in this fixture's own words
    everyone = run(cx_ctx.con, q)
    assert "rebuilt_shown" not in everyone["rows"][0]


def test_a_ranked_by_measure_is_the_compilers_own_not_an_unhonored_cell(cx_ctx: AnswerContext) -> None:
    """yardstick-v2 F124 "highest scoring triple doubles": the measure a
    ranking of yes/no games is ordered by (``Window.by``) is the compiler's
    to read, never refused as a cell the relation does not honor; the games
    are ranked by points."""
    result = compose_answer(
        cx_ctx, PLAYER_RANKING, Reading(measure=Measure(key="triple_double", as_typed="triple_double"), window=Window(count=5, by="points")), "players with the highest scoring triple doubles"
    )
    assert result is not None
    assert "triple-double" in result.text
    assert result.data["rows"][0]["points"] == 28
    # A league-wide row says whose game it is - the one thing a ranking of
    # players' games is asked for.
    assert result.data["rows"][0]["player"] == "Brandin Podziemski"
    assert "Brandin Podziemski" in result.text


def test_an_ordinal_season_over_everyone_is_each_players_own(cx_ctx: AnswerContext) -> None:
    """yardstick-v2 F099 "Most points in 15th season played": no player,
    and `season_n` over everyone is each player's Nth regular season - every fixture player's 1st is s-1 and
    2nd is s, so the top single game moves from Brown's 26 (g6, s-1) to
    Curry's 40 (g3, s), and the sentence names the ordinal."""
    first = compose_answer(cx_ctx, GAME_HIGHS, Reading(cuts=Cuts(season_n=1), measure=Measure(key="points", as_typed="points"), span=Span(career=True)), "most points in a game in 1st season played")
    second = compose_answer(cx_ctx, GAME_HIGHS, Reading(cuts=Cuts(season_n=2), measure=Measure(key="points", as_typed="points"), span=Span(career=True)), "most points in a game in 2nd season played")
    assert first is not None and second is not None
    assert first.data["rows"][0]["points"] == 26 and first.data["rows"][0]["player"] == "Jaylen Brown"
    assert second.data["rows"][0]["points"] == 40 and second.data["rows"][0]["player"] == "Stephen Curry"
    assert "in their 2nd season" in second.text


def test_a_team_with_no_player_is_the_teams_players_games(cx_ctx: AnswerContext) -> None:
    """yardstick-v2 F152 "oklahoma city thunder all-time triple doubles":
    the team named with no player is the `team` narrowing of a league-wide
    read - the Warriors' players' triple-doubles are Podziemski's one (g3,
    28/10/11). The parser reads the team as the subject's team.

    The grouped head names WHAT was counted ("with a triple-double"), not
    only the span, so the table of counts has a subject:
    ``_grouped_sentence`` includes the predicates (``_predicates(q)``, the
    same call ``_rows_sentence``/``_scalar_sentence`` make), and
    ``data["headline"]`` carries the same sentence."""
    result = compose_answer(
        cx_ctx,
        GAMES_COUNTED,
        Reading(who=Who(kind="team", teams=("Golden State Warriors",)), measure=Measure(key="triple_double", as_typed="triple_double"), span=Span(career=True)),
        "golden state warriors all-time triple doubles",
    )
    assert result is not None
    assert "Golden State Warriors" in result.text
    assert "with a triple-double" in result.text  # the predicate, not just the span
    assert result.data["rows"][0]["games"] == 1
    assert result.data["headline"] == result.text.split("\n")[0].rstrip(":")
    assert "with a triple-double" in result.data["headline"]
    # Asked as a player's line, the team still makes it the same count.
    as_stat = compose_answer(cx_ctx, PLAYER_LINE, Reading(who=Who(kind="team", teams=("Golden State Warriors",)), span=Span(career=True)), "golden state warriors all-time triple doubles")
    # (A ranking of the team's players by their games, read and said by the
    # game-level ranking's reader and sayer, decision D17.)
    assert as_stat is not None and as_stat.data["leaders"][0]["games"] == 1 and "with a triple-double" in as_stat.data["headline"]


def test_a_grouped_by_player_count_carries_the_whole_total_a_window_cut(cx_ctx: AnswerContext) -> None:
    """ "Players with 10 points this season", limited to the top 2: the page's
    Total row (the ``grouped`` skeleton) needs the WHOLE count behind the
    listed rows, not just the two shown - ``core.run`` computes it
    (``_grouped_total``) and ``_point_data`` carries it. 90 and 91 (22 games apiece, clearing the
    10-point line every game) are the top two; Podziemski, Curry, Brown and
    Sabonis clear it in fewer games each, so the real total (56) is well
    past what the top two alone account for (44). The measure ``rebounds`` is a
    decoy the phrase's own "10 points" overrides (the same discipline
    ``test_the_questions_own_number_names_its_column_not_the_models_stat``
    exercises) - stat and phrase naming the SAME column is the one case
    ``_everyone_threshold_predicates`` does not add a line for, which would
    leave no predicate to count at all."""
    q = move_point(
        cx_ctx.con,
        GAMES_COUNTED,
        Reading(lines=(Line(measure="points", value=10, as_typed="10 points", keyed=True),), measure=Measure(key="rebounds", as_typed="rebounds"), window=Window(count=2)),
        "players with 10 points this season",
    )
    assert isinstance(q, Query)
    assert q.subject == "everyone" and q.skeleton == "grouped" and q.group == "player"
    out = run(cx_ctx.con, q)
    assert len(out["rows"]) == 2
    assert out["total"] == 56
    assert sum(r["games"] for r in out["rows"]) == 44  # the listed two alone
    result = compose_answer(
        cx_ctx,
        GAMES_COUNTED,
        Reading(lines=(Line(measure="points", value=10, as_typed="10 points", keyed=True),), measure=Measure(key="rebounds", as_typed="rebounds"), window=Window(count=2)),
        "players with 10 points this season",
    )
    assert result is not None
    assert result.data["total"] == 56
    assert "56" in result.text and "listed" in result.text


# ---------------------------------------------------------------------------
# A shape's reader says its own point; a point the words moved, or a cell
# its row does not state, keeps the compiler's own sentence.
# ---------------------------------------------------------------------------


def _add_condition_tables(con: duckdb.DuckDBPyConnection) -> None:
    """The two tables the condition readers (a record over a line, a
    player's splits) read beside the relation and ``cx_ctx`` does not
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


def test_a_point_the_words_moved_keeps_the_compilers_own_sentence(cx_ctx: AnswerContext) -> None:
    """A measure the question's words add ("PRA", which a player's line
    does not carry) is not the line's own point, so the compiler's own
    sentence and point data answer it."""
    result = compose_answer(cx_ctx, PLAYER_LINE, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), cuts=Cuts(opponent="Boston Celtics")), "Podziemski's PRA vs Boston")
    assert result is not None
    assert result.data["skeleton"] == "scalar" and result.data["measures"] == ["pra"]
    assert result.data["rows"][0]["games"] == 2


def test_a_cell_the_count_reader_does_not_state_keeps_the_compilers_own_sentence(cx_ctx: AnswerContext) -> None:
    """An opponent is a cell a count of games' row does not state
    (``compose.plan.cells_unhonored``), so its reader steps aside and the
    count against Boston is the compiler's point, said the compiler's way."""
    result = compose_answer(
        cx_ctx,
        GAMES_COUNTED,
        Reading(
            who=Who(kind="player", players=("Brandin Podziemski",)),
            cuts=Cuts(opponent="Boston Celtics"),
            lines=(Line(measure="points", value=15, as_typed="15+ point", keyed=True),),
            measure=Measure(key="points", as_typed="points"),
        ),
        "podziemski 15+ point games vs boston",
    )
    assert result is not None
    assert result.data["skeleton"] == "scalar"
    assert result.data["rows"][0]["games"] == 1  # g1 (20) - g5 (10) does not clear the line


def test_a_single_game_high_question_is_one_game_even_without_in_a_game(cx_ctx: AnswerContext) -> None:
    """ "What was the highest scoring game against Detroit" names no "in a
    game", and is still one game, never a ranking of per-game AVERAGES - the
    key the words ask names one game: Curry's 40 in g3. (An opponent is a
    cell a single game's high does not state, so the compiler's own rows
    answer it.)"""
    result = compose_answer(cx_ctx, GAME_HIGHS, Reading(cuts=Cuts(opponent="Detroit Pistons"), measure=Measure(key="points", as_typed="points")), "what was the highest scoring game against detroit")
    assert result is not None
    assert result.data["skeleton"] == "rows" and result.data["rows"][0]["points"] == 40


def test_a_league_count_on_its_own_measures_line_is_answered(cx_ctx: AnswerContext) -> None:
    """ "Most games with 10+ assists": the line is on the measure the count
    ranks by (``_everyone_threshold_predicates`` adds no second line for
    it), and the league-wide count is answered - Podziemski's g3 (11
    assists) is the only one."""
    result = compose_answer(
        cx_ctx,
        GAMES_COUNTED,
        Reading(lines=(Line(measure="assists", value=10, as_typed="10+ assists", keyed=True),), measure=Measure(key="assists", as_typed="assists")),
        "most games with 10+ assists this season",
    )
    assert result is not None
    assert result.data["leaders"] == [{"player": "Brandin Podziemski", "games": 1}]


def test_a_history_by_season_keeps_the_newest_seasons(cx_ctx: AnswerContext) -> None:
    """A player's season history at the game level (planned so by
    ``plan.plan``, for a stat the season line has no per-season column for),
    limited to one season, is his NEWEST one, newest first - "the past 4
    seasons" is never a career's first four."""
    s = current_season()
    result = compose_answer(
        cx_ctx,
        SEASON_HISTORY,
        Reading(who=Who(kind="player", players=("Brandin Podziemski",)), measure=Measure(key="turnovers", as_typed="turnovers"), window=Window(count=1)),
        "podziemski's turnovers over the past season",
    )
    assert result is not None
    assert [r["group"] for r in result.data["rows"]] == [s]
    assert "most recent 1 seasons" in result.text
    both = compose_answer(
        cx_ctx,
        SEASON_HISTORY,
        Reading(who=Who(kind="player", players=("Brandin Podziemski",)), measure=Measure(key="turnovers", as_typed="turnovers"), window=Window(count=5)),
        "podziemski's turnovers by season",
    )
    assert both is not None
    assert [r["group"] for r in both.data["rows"]] == [s, s - 1]
    assert "most recent" not in both.text  # the whole career fit


# ---------------------------------------------------------------------------
# The season line as a second source: an unnarrowed player's line and a
# per-season history read player_season_stats_deduped through the season
# line's readers.
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


def test_a_season_line_point_the_words_moved_is_declined_not_said_as_the_line(cx_ctx: AnswerContext) -> None:
    """A measure the question's words add ("PRA") to an unnarrowed line is a
    point the season line's reader does not say, so the compiler declines it
    - never the default stat line in its place."""
    _add_season_line(cx_ctx.con)
    assert compose_answer(cx_ctx, PLAYER_LINE, Reading(who=Who(kind="player", players=("Stephen Curry",))), "curry's PRA this season") is None


def test_the_season_source_is_never_compiled_over_games() -> None:
    """A season-line point reaching the game-level compiler is refused, not
    read over box scores under the season line's name."""
    q = planned_query(PLAYER_LINE, Reading(who=Who(kind="player", players=("Stephen Curry",)), measure=Measure(key="points", as_typed="points")))
    assert q.source == "seasons"
    with pytest.raises(Unsupported, match="seasons source"):
        compile_query(duckdb.connect(":memory:"), q)


def test_own_team_narrows_a_composed_average(cx_ctx: AnswerContext) -> None:
    """The ``tenure`` cut ("lebron stats as a starter for Miami") narrows the
    compiler's read exactly as it narrows a player's line: Curry never
    played for Boston, so his games "for Boston" are none, never an average
    of every game he played."""
    q = planned_query(PLAYER_LINE, Reading(who=Who(kind="player", players=("Stephen Curry",)), cuts=Cuts(opponent="Detroit Pistons", tenure="Boston Celtics")))
    assert _run(cx_ctx.con, q)["rows"][0]["games"] == 0
    q = planned_query(PLAYER_LINE, Reading(who=Who(kind="player", players=("Stephen Curry",)), cuts=Cuts(opponent="Detroit Pistons", tenure="Golden State Warriors")))
    assert _run(cx_ctx.con, q)["rows"][0]["games"] == 1  # g3


def test_a_composed_answer_carries_no_coverage_caveat_of_its_own(cx_ctx: AnswerContext) -> None:
    """The agent appends ``coverage_caveat`` to a composed answer
    (``agent._try_compose``); the compiler adds none of its own, so ESPN's
    2001-playoffs note is said once. The note is the agent's to add."""
    result = compose_answer(cx_ctx, PLAYER_LOG, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), span=Span(season=2001, season_type=3)), "podziemski plus-minus game log 2001 playoffs")
    assert result is not None
    assert "Note:" not in result.text
    assert not any("Note:" in note for note in result.data.get("notes", []))


def test_a_single_games_usage_is_the_percent_the_split_averages(cx_ctx: AnswerContext) -> None:
    """#222: ``usage_pct`` is stored as a percent (``player_advanced_stats``
    computes ``100.0 * ...``), not a fraction - one game's 24.35 is never
    "2435.0%". The per-game row reads the figure a player's splits' USG%
    column averages: Podziemski's only away game this season is g1, so the
    split's away USG% IS g1's usage, and the log's g1 row says the same."""
    from test_readers import player_splits

    _add_condition_tables(cx_ctx.con)
    cx_ctx.con.execute("ALTER TABLE player_box_stats ADD COLUMN usage_pct DOUBLE")
    cx_ctx.con.execute("UPDATE player_box_stats SET usage_pct = CASE event_id WHEN 'g1' THEN 24.35 ELSE 18.0 END WHERE athlete_id = ?", [PODZ])
    split = player_splits(cx_ctx, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), measure=Measure(key="usage_pct", as_typed="usage_pct"), split="home_away"))
    away = next(row for row in split.data["splits"]["home_away"] if row["group"] == "away")
    assert away["games"] == 1 and away["usage_pct"] == pytest.approx(24.35)
    log = compose_answer(cx_ctx, PLAYER_LOG, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), measure=Measure(key="usage_pct", as_typed="usage_pct")), "podziemski usage game log")
    assert log is not None
    g1 = next(row for row in log.data["rows"] if row["opponent"] == "BOS" and not row["home"])
    assert g1["usage_pct"] == pytest.approx(away["usage_pct"])
    assert f"usage {away['usage_pct']:.1f}%" in log.text
    assert "2435" not in log.text


def test_a_player_beside_a_team_subject_is_not_read_as_the_team(cx_ctx: AnswerContext) -> None:
    """ "show me stats for the warriors when podziemski scored 15+ points"
    reads as a TEAM subject with Podziemski beside it; the reading's player
    stays his, never resolved as a team called "Podziemski" (likewise "...
    for sixers when maxey scored 20+ points"), and the record is the record
    reader's own table."""
    _add_condition_tables(cx_ctx.con)
    result = compose_answer(
        cx_ctx,
        LINE_RECORD,
        Reading(who=Who(kind="player", players=("Podziemski",)), lines=(Line(measure="points", value=15, as_typed="15+ points", keyed=True),), measure=Measure(key="points", as_typed="points")),
        "show me stats for the warriors when podziemski scored 15+ points",
    )
    assert result is not None
    assert result.text.startswith("Golden State Warriors record when Brandin Podziemski had 15+ points")


def test_a_narrowed_advanced_rate_is_read_over_the_games_not_the_season_line(cx_ctx: AnswerContext) -> None:
    """ "klay ts% vs boston": the compiler reads a narrowed advanced rate
    over exactly the narrowed games as a ratio of sums (``core.RATES``), in
    its own sentence, never the season line's figure."""
    _add_condition_tables(cx_ctx.con)
    result = compose_answer(
        cx_ctx,
        PLAYER_LINE,
        Reading(who=Who(kind="player", players=("Brandin Podziemski",)), cuts=Cuts(opponent="Boston Celtics"), measure=Measure(key="ts_pct", as_typed="ts_pct")),
        "podziemski ts% vs boston",
    )
    assert result is not None
    assert "TS%" in result.text and "vs the Boston Celtics" in result.text
    assert result.data["measures"] == ["ts_pct"] and result.data["skeleton"] == "scalar" and result.data["rows"][0]["games"] == 2


def test_a_log_reads_a_rebuilt_game_with_its_minutes_blank(cx_ctx: AnswerContext) -> None:
    """A log lists a game rebuilt from play-by-play: ``minutes`` is exempt
    from the rebuilt-line rule - play-by-play cannot recover it, so the
    rebuilt row shows it blank - and the other columns shown are ones a
    rebuild gets right. Podziemski's g5, rebuilt here, is his newest row,
    its minutes "-", and the answer says the game was rebuilt."""
    con = cx_ctx.con
    con.execute("ALTER TABLE player_box_stats ADD COLUMN reconstructed BOOLEAN")
    con.execute("UPDATE player_box_stats SET reconstructed = FALSE")
    con.execute("UPDATE player_box_stats SET minutes = NULL, reconstructed = TRUE WHERE event_id = 'g5' AND athlete_id = ?", [PODZ])
    con.execute("CREATE VIEW player_box_stats_filled AS SELECT * FROM player_box_stats")
    out = run(con, planned_query(PLAYER_LOG, Reading(who=Who(kind="player", players=("Brandin Podziemski",)))))
    newest = out["rows"][0]
    assert newest["points"] == 10 and newest["minutes"] is None and newest["reconstructed"]
    composed = compose_answer(cx_ctx, PLAYER_LOG, Reading(who=Who(kind="player", players=("Brandin Podziemski",))), "podziemski's game log")
    assert composed is not None
    lines = composed.text.split("\n")
    assert lines[0] == f"Brandin Podziemski, last 4 games of the {current_season()} regular season:"
    # The full box line beside it (ISSUES.md #471): a column a rebuild does
    # not fill is blank on the rebuilt row, and the note names it.
    assert lines[2].split() == [f"{current_season()}-03-01", "vs", "BOS", "W", "-", "10", "3", "2", "1", "0", "-", "-", "-", "-", "-"]
    assert lines[-1] == (
        "1 of these games has no box score from ESPN: its figures are rebuilt from play-by-play, and minutes, turnovers, threes, shot attempts and free-throw attempts cannot be recovered at all."
    )
    # The rates are over the games that carry both halves: 56.4% over the
    # three fetched games, never the rebuilt game's makes over their attempts.
    assert lines[-2].split()[-4] == "56.4"


def test_a_teams_total_of_triple_doubles_is_declined_for_the_readings_cause(cx_ctx: AnswerContext) -> None:
    """ "oklahoma city thunder all-time triple doubles vs west" (day5): a team
    subject on a ranking of players with a boolean stat is a team aggregate nothing
    reads - declined here, so the reading's ``team_boolean_count`` cause names it
    instead of the ranking's "no ranking reads triple_double"."""
    from association.query.compose.core import Unsupported

    with pytest.raises(Unsupported, match="team's total"):
        move_point(
            cx_ctx.con,
            PLAYER_RANKING,
            Reading(who=Who(kind="team", teams=("Golden State Warriors",)), measure=Measure(key="triple_double", as_typed="triple_double"), span=Span(career=True)),
            "golden state warriors all-time triple doubles vs west",
            Subject("team", teams=("Golden State Warriors",)),
        )


# ---------------------------------------------------------------------------
# The compiler's own answers to points a shape's reader steps aside for, and
# its declines where nothing reads them.
# ---------------------------------------------------------------------------


def test_a_count_under_a_ceiling_with_no_threshold_is_counted(cx_ctx: AnswerContext) -> None:
    """ "Sga games with under 14 fta in his whole career": the below phrase
    IS the count - no threshold arrives - and the compiler counts it, in the
    count's own words: Curry's four games this season all have under 5 free
    throw attempts."""
    result = compose_answer(
        cx_ctx,
        GAMES_COUNTED,
        Reading(
            who=Who(kind="player", players=("Stephen Curry",)),
            lines=(Line(measure="freeThrowsAttempted", op="<", value=5, as_typed="under 5 fta", narrows=True),),
            measure=Measure(key="freeThrowsAttempted", as_typed="freeThrowsAttempted"),
        ),
        "curry games with under 5 fta",
    )
    assert result is not None
    assert result.text == f"Stephen Curry had 4 games with under 5 free throw attempts in the {current_season()} regular season."
    assert result.data["leaders"] == [{"player": "Stephen Curry", "games": 4}]


def test_a_teams_own_threshold_record_is_said_by_the_teams_record_reader(cx_ctx: AnswerContext) -> None:
    """ "What was the warriors record when they scored 100 points" (#144's
    shape): a team's record above and below its OWN line, which the
    team subject's readers (a season sum, a window sum) cannot represent -
    so run_team declines it, and the team's record-over-a-line reader answers
    (compose.records.read_team_record_when). Golden State this season: 110 (W), 99
    (L), 120 (W), 90 (L), 100 (W)."""
    # The team-games relation reads the venue columns the player fixture skips.
    cx_ctx.con.execute("ALTER TABLE games ADD COLUMN neutral_site BOOLEAN")
    cx_ctx.con.execute("ALTER TABLE games ADD COLUMN venue_city VARCHAR")
    real_games.build_table(cx_ctx.con, {"games", "teams"})
    result = compose_answer(
        cx_ctx,
        LINE_RECORD,
        Reading(
            who=Who(kind="team", teams=("Golden State Warriors",)),
            lines=(Line(measure="points", value=100, as_typed="100 points", keyed=True),),
            measure=Measure(key="points", as_typed="points"),
            span=Span(season_type=2),
        ),
        "warriors record when they scored 100 points",
    )
    assert result is not None
    assert result.text.startswith("Golden State Warriors record when they had 100+ points")
    assert result.data["reached"]["games"] == 3 and result.data["reached"]["wins"] == 3
    assert result.data["fell_short"]["games"] == 2 and result.data["fell_short"]["wins"] == 0


def test_a_record_over_a_line_of_zero_is_refused_with_the_reason(cx_ctx: AnswerContext) -> None:
    """A line of 0 is every game he played - never a record "when" - and is
    refused with the reason, never answered as "went 2-1 in the 3 games
    points >= 0"."""
    refused = compose_answer(
        cx_ctx,
        LINE_RECORD,
        Reading(who=Who(kind="player", players=("Stephen Curry",)), lines=(Line(measure="points", value=0, as_typed="0 points", keyed=True),), measure=Measure(key="points", as_typed="points")),
        "",
    )
    assert refused is not None and refused.text == "A threshold of 0 counts every game - there is no line there to keep games past."


def test_a_single_game_high_with_no_stat_is_declined_for_the_stat_not_the_player() -> None:
    """A named player's high with no stat read ("brice sensabaugh career
    high asistss") is declined for the stat - the fact that is missing -
    never "needs a player and a known stat" of a question that named its
    player, which would send the reader to look for the wrong thing."""
    from association.query.point import default_point

    with pytest.raises(PointRefused) as no_stat:
        default_point(GAME_HIGHS, Reading(who=Who(kind="player", players=("Brice Sensabaugh",)), span=Span(career=True)))
    assert no_stat.value.cause == Cause(kind="needs_stat", facts={"shape": GAME_HIGHS})
    assert refusal_result(no_stat.value.cause).text == "A single-game high needs a stat to rank games by, and none was read."
    with pytest.raises(PointRefused) as unknown:
        default_point(GAME_HIGHS, Reading(who=Who(kind="player", players=("Brice Sensabaugh",)), measure=Measure(as_typed="asistss")))
    assert unknown.value.cause == Cause(kind="unknown_stat", facts={"shape": GAME_HIGHS, "stat": "asistss"})
    assert refusal_result(unknown.value.cause).text == "A single-game high cannot rank games by 'asistss' - only by a box-score stat each game has a number for."
    with pytest.raises(Unsupported, match="needs a named player"):
        default_point(GAME_HIGHS, Reading(measure=Measure(key="points", as_typed="points")))


def test_a_league_count_in_an_ordinal_season_is_declined_not_narrowed_silently(cx_ctx: AnswerContext) -> None:
    """ "Most 20+ point games in a 15th season": a league has no career to
    count seasons in. It is declined, saying why, never narrowed to players
    in their 15th season OF THE DEFAULT YEAR under a sentence naming only
    the year."""
    refused = compose_answer(
        cx_ctx, GAMES_COUNTED, Reading(cuts=Cuts(season_n=15), lines=(Line(measure="points", value=20, as_typed="20 points", keyed=True),), measure=Measure(key="points", as_typed="points")), ""
    )
    assert refused is not None and refused.text == "The 15th season is a place in one player's career, and no player was named."


def test_a_history_naming_no_stat_is_the_whole_line_and_says_so(cx_ctx: AnswerContext) -> None:
    """A per-season history that names no stat reads the whole line - points,
    rebounds and assists per game by season (#387) - and its heading says
    so, never points alone."""
    query = move_point(cx_ctx.con, SEASON_HISTORY, Reading(who=Who(kind="player", players=("Brandin Podziemski",))), "")
    assert isinstance(query, Query) and query.source == "seasons" and query.measures == ["points", "rebounds", "assists"]
    assert player_history_reads(query)
    result = Result(
        subject="Brandin Podziemski",
        relation="player",
        span=ResultSpan(season_type=2, source="seasons"),
        parts=(Part(body=Grouped(by="season", rows=({"key": 2026, "games": 60, "avgPoints": 11.1, "avgRebounds": 5.0, "avgAssists": 3.6},))),),
        facts=LineFacts(stat=HISTORY_LINE),
    )
    assert "points, rebounds and assists per game" in say(result).text.split("\n")[0]


def test_a_history_of_a_stat_nothing_carries_is_declined_not_swapped_for_points(cx_ctx: AnswerContext) -> None:
    """ "shot_distance" has no per-season column and no game-level measure:
    it is declined, never drawn as the player's POINTS by season, a stat
    named and silently replaced."""
    why: list[str] = []
    assert (
        compose_answer(
            cx_ctx, SEASON_HISTORY, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), measure=Measure(key="shot_distance", as_typed="shot_distance")), "", declined=why.append
        )
        is None
    )
    assert why == ["no per-season history for stat 'shot_distance'"]


# ---------------------------------------------------------------------------
# The parser reads the compiler's point once: the
# Reading carries it, and the compiler answers it without the question.
# ---------------------------------------------------------------------------


def test_the_parser_reads_the_point_and_the_compiler_answers_it_unread(cx_ctx: AnswerContext, monkeypatch: pytest.MonkeyPatch) -> None:
    """``parse.reading_from_route`` reads the compiler's point from the
    question's words (``Reading.point``), and ``compose.answer`` plans and
    runs it without the question: with the point reader disabled outright,
    the answer is the one the same point gives read the test's own way
    (``compose_answer`` above, the subject and the point built by hand)."""

    question = "how many 15+ point games did podziemski have"
    reading = reading_of(
        cx_ctx.con,
        question,
        with_subject(
            cx_ctx.con,
            question,
            Routed(
                GAMES_COUNTED,
                Reading(
                    who=Who(kind="player", players=("Brandin Podziemski",)),
                    lines=(Line(measure="points", value=15, as_typed="15 points", keyed=True),),
                    measure=Measure(key="points", as_typed="points"),
                ),
            ),
        ),
    )
    assert reading.point is not None and (reading.point.read_as.shape, aggregate_of(reading.point)) == ("scalar", "count")
    by_hand = compose_answer(
        cx_ctx,
        GAMES_COUNTED,
        Reading(who=Who(kind="player", players=("Brandin Podziemski",)), lines=(Line(measure="points", value=15, as_typed="15+ point", keyed=True),), measure=Measure(key="points", as_typed="points")),
        question,
        reading.subject,
    )

    def unread(*args: Any, **kwargs: Any) -> Reading:
        raise AssertionError("the compiler read the question")

    monkeypatch.setattr("association.query.point.read_point", unread)
    answered = compose.answer(cx_ctx, reading, planned=plan_point(reading))
    assert answered is not None and by_hand is not None
    assert (answered.text, answered.data) == (by_hand.text, by_hand.data)


def test_a_point_the_compiler_declines_or_refuses_travels_on_the_reading(cx_ctx: AnswerContext) -> None:
    """The compiler's verdict, read at parse time, is what ``compose.answer``
    gives: the point reader's cause (a log with no player subject), said at
    a decline's place, and a refusal as the answer -
    the planning's own refusal, unworded (a
    :class:`~association.query.result.Refusal`), worded by the sayer."""

    declined = reading_of(cx_ctx.con, "Embiid's game log", with_subject(cx_ctx.con, "Embiid's game log", Routed(PLAYER_LOG)))
    assert declined.point is None and declined.point_declined is None
    assert declined.point_refusal == Cause(kind="no_subject_reading", facts={"shape": PLAYER_LOG})
    # Declined by that cause, which the answering loop says at a decline's
    # place, after the floor and what the words name that nothing reads.
    why: list[str] = []
    causes: list[Cause] = []
    assert compose.answer(cx_ctx, declined, planned=plan_point(declined), declined=why.append, declined_by=causes.append) is None
    assert why == ["no player subject and no ranking or position-group reading of the question"]
    assert causes == [Cause(kind="no_subject_reading", facts={"shape": PLAYER_LOG})]

    refused = reading_of(cx_ctx.con, "most gizmos in a single game", with_subject(cx_ctx.con, "most gizmos in a single game", Routed(GAME_HIGHS, Reading(measure=Measure(as_typed="gizmos")))))
    assert refused.point is None and refused.point_refusal == Cause(kind="no_ranking_measure", facts={"stat": "gizmos"})
    planned = plan_point(refused)
    answered = compose.answer(cx_ctx, refused, planned=planned)
    assert planned == Refusal(kind="no_ranking_measure", facts={"stat": "gizmos"}, shown={"stat": "gizmos"}) and answered is not None
    assert answered.text == "No ranking reads 'gizmos' on the player-games relation - it only ranks the box-score measures it knows, not a NetPoints or other outside figure."


def test_a_summed_true_shooting_rate_is_a_fraction_like_its_column(cx_ctx: AnswerContext) -> None:
    """``ts_pct`` and ``efg_pct`` are stored per game as fractions (0.57) and
    printed times 100 (``sentence._FRACTION_COLUMNS``, the page's
    ``FRACTIONS``). Their ratio-of-sums rate (``core.RATES``) is a fraction
    too, so a narrowed line is multiplied once, never twice ("4622.5% TS%").
    Podziemski's two games against Boston: 30 points on 30 shots and no free
    throws is a TS% of 0.5."""
    result = compose_answer(
        cx_ctx,
        PLAYER_LINE,
        Reading(who=Who(kind="player", players=("Brandin Podziemski",)), cuts=Cuts(opponent="Boston Celtics"), measure=Measure(key="ts_pct", as_typed="ts_pct")),
        "podziemski true shooting vs boston",
    )
    assert result is not None
    assert result.data["rows"][0]["ts_pct"] == pytest.approx(0.5)
    assert "50.0% TS%" in result.text and "5000" not in result.text


# ---------------------------------------------------------------------------
# The planner refuses from the Reading.


def test_the_planner_refuses_a_narrowing_the_relation_cannot_honor() -> None:
    """``plan`` is where a Reading meets its relation, and a cell the
    relation does not narrow by (``rate``: no per-36 read on the games) is
    refused there - not one call later, inside the compile step."""
    with pytest.raises(Unsupported, match="a rate per 36 cannot be read here"):
        plan(
            default_point(
                GAMES_COUNTED,
                Reading(
                    who=Who(kind="player", players=("Brandin Podziemski",)),
                    lines=(Line(measure="points", value=30, as_typed="30 points", keyed=True),),
                    measure=Measure(key="points", as_typed="points", unit="per_36"),
                ),
            )
        )
    assert isinstance(
        plan(
            default_point(
                GAMES_COUNTED,
                Reading(
                    who=Who(kind="player", players=("Brandin Podziemski",)),
                    lines=(Line(measure="points", value=30, as_typed="30 points", keyed=True),),
                    measure=Measure(key="points", as_typed="points"),
                ),
            )
        ),
        Query,
    )


def test_the_planner_refuses_after_the_parser_has_read_and_only_once(cx_ctx: AnswerContext) -> None:
    """The parser reads the point and does not plan it: a narrowing the
    relation cannot honor is the PLAN stage's verdict (``plan_point``) - the
    reason the refusal names, never a reader's list read after the fact -
    and the compiler answers from the
    planning it is handed without planning again."""
    from association.query.compose import plan as plan_module

    reading = reading_of(
        cx_ctx.con,
        "podziemski 30 point games per 36",
        with_subject(
            cx_ctx.con,
            "podziemski 30 point games per 36",
            Routed(
                GAMES_COUNTED,
                Reading(
                    who=Who(kind="player", players=("Brandin Podziemski",)),
                    lines=(Line(measure="points", value=30, as_typed="30 points", keyed=True),),
                    measure=Measure(key="points", as_typed="points", unit="per_36"),
                ),
            ),
        ),
    )
    assert reading.point is not None and reading.point_declined is None and reading.point_refusal is None
    planned = plan_point(reading)
    assert isinstance(planned, Unsupported) and str(planned).startswith("a rate per 36 cannot be read here")
    why: list[str] = []
    assert compose.answer(cx_ctx, reading, declined=why.append, planned=planned) is None and why == [str(planned)]

    answerable = reading_of(
        cx_ctx.con,
        "podziemski 30 point games",
        with_subject(
            cx_ctx.con,
            "podziemski 30 point games",
            Routed(
                GAMES_COUNTED,
                Reading(
                    who=Who(kind="player", players=("Brandin Podziemski",)),
                    lines=(Line(measure="points", value=30, as_typed="30 points", keyed=True),),
                    measure=Measure(key="points", as_typed="points"),
                ),
            ),
        ),
    )
    handed = plan_point(answerable)
    assert isinstance(handed, Query)
    calls: list[Reading] = []
    original = plan_module.plan
    try:
        plan_module.plan = lambda point: calls.append(point) or original(point)  # type: ignore[assignment, func-returns-value]
        assert compose.answer(cx_ctx, answerable, planned=handed) is not None
        assert calls == []  # handed its planning, the compiler does not plan
        with pytest.raises(TypeError):
            compose.answer(cx_ctx, answerable)  # type: ignore[call-arg]  # no planning, no answer: nothing plans here
    finally:
        plan_module.plan = original


def test_a_reader_steps_aside_for_a_narrowing_its_words_do_not_state(cx_ctx: AnswerContext) -> None:
    """A shape's words name the narrowings its reader honors and no
    other (its shape's row, ``compose.plan.cells_unhonored``): an opponent
    on a single-game high is the relation's to narrow by and the compiler's
    sentence's to state, so the shape's read answers nothing and every
    shape declares."""
    from dataclasses import replace

    from association.query import compose
    from association.query.compose.say import _ASKED_AS
    from association.query.reading import (
        GAME_HIGHS,
        GAMES_COUNTED,
        LINE_RECORD,
        LINE_RUNS,
        NETPOINTS_FINGERPRINT,
        NETPOINTS_RATINGS,
        PERIOD_LOG,
        PERIOD_RANKING,
        PLAYER_COMPARISON,
        PLAYER_LINE,
        PLAYER_LOG,
        PLAYER_MEETINGS,
        PLAYER_RANKING,
        PLAYER_SPLITS,
        PLAYERS_COUNTED,
        PRESENCE_SPLIT,
        SEASON_HISTORY,
        SHOT_CHART,
        SHOT_DISTANCE,
        TEAM_COACH,
        TEAM_LINE,
        TEAM_MEETINGS,
        TEAM_OUTLOOK,
        TEAM_PERIOD_TOTAL,
        TEAM_RANKING,
        TEAM_RECORD,
        TEAM_SEASON_HISTORY,
    )

    # Every shape's words are the sayer's (compose.say); the words a decline
    # names each shape the words ask by (say.asked_as) name every one, and
    # the coach's, which no reader takes.
    ported = {
        PLAYER_LOG,
        LINE_RECORD,
        PLAYER_SPLITS,
        PERIOD_LOG,
        GAMES_COUNTED,
        GAME_HIGHS,
        LINE_RUNS,
        PLAYER_MEETINGS,
        PLAYER_RANKING,
        # How many players a season line holds (#390).
        PLAYERS_COUNTED,
        PLAYER_LINE,
        SEASON_HISTORY,
        PLAYER_COMPARISON,
        PRESENCE_SPLIT,
        # The team shapes, each its point's only answer (their rows' declined="plan").
        TEAM_MEETINGS,
        TEAM_PERIOD_TOTAL,
        PERIOD_RANKING,
        TEAM_RECORD,
        # A team's best single game, its point's only answer.
        TEAM_GAME_HIGHS,
        # The league's players ranked over their games, its point's only
        # answer (decision D17).
        PointShape("player_games", "ranking", "player"),
    }
    # And the team-season readers' (compose.team_stats), a team's history
    # among them (#372).
    team_seasons = {TEAM_LINE, TEAM_SEASON_HISTORY, TEAM_RANKING, TEAM_OUTLOOK}
    # And the NetPoints relation's (compose.netpoints), and the shot relation's (compose.shots).
    netpoints = {NETPOINTS_RATINGS, NETPOINTS_FINGERPRINT}
    shots = {SHOT_CHART, SHOT_DISTANCE}
    assert set(_ASKED_AS) == ported | team_seasons | netpoints | shots | {TEAM_COACH}
    narrowed = planned_query(GAME_HIGHS, Reading(who=Who(kind="player", players=("Brandin Podziemski",)), cuts=Cuts(opponent="Boston Celtics"), measure=Measure(key="points", as_typed="points")))
    assert compose._read(cx_ctx.con, GAME_HIGHS, narrowed) is None
    assert compose._read(cx_ctx.con, GAME_HIGHS, replace(narrowed, **(replace(narrowed, cuts=replace(narrowed.cuts, opponent=None))).filters())) is not None


#: One example of each cause's facts, for the sentence check below.
_CAUSE_EXAMPLES: dict[str, dict[str, Any]] = {
    "shot_distance_ranking": {},
    "no_ranking_measure": {"stat": "gizmos"},
    "ranking_floor_unit": {"unit": "attempts", "count": 100},
    "minimum_unranked": {"unit": "games", "count": 20},
    "window_ends_contradict": {"ends": ["last 5", "first games"]},
    "window_per_season": {"window": "first 10 game", "per": "every season"},
    "ranking_unit": {"metric": "avg_points", "rate": "/ 90"},
    "needs_stat": {"shape": GAME_HIGHS},
    "unknown_stat": {"shape": LINE_RUNS, "stat": "double_double"},
    "needs_threshold": {"shape": LINE_RECORD, "stat": "points"},
    "line_is_a_companions": {"shape": LINE_RECORD, "player": "Tyrese Maxey", "companion": "Joel Embiid", "line": "20+ points"},
    "threshold_needs_stat": {"shape": PLAYER_LOG, "threshold": 15},
    "threshold_counts_every_game": {"shape": GAMES_COUNTED, "threshold": 0},
    "line_names_no_stat": {"phrase": "under 30 gizmos", "side": "under"},
    "line_on_a_percentage": {"phrase": "more than 40 field goal percentage", "side": "over"},
    "season_line_unread": {"words": "averaged 30"},
    "seasons_counted": {"words": "most seasons averaging 30 ppg"},
    "needs_line": {},
    "career_place_needs_player": {"season_n": 15},
    "needs_subject": {"shape": LINE_RECORD},
    "team_streak_of_stat": {"stat": "points"},
    "matchup_needs_two": {"names": ["Jayson Tatum"]},
    "no_period_stat": {"stat": "minutes"},
    "no_coach_table": {"unanswerable": "coach"},
    "too_short": {"asked": "Tatum rec"},
    "championship": {"shape": TEAM_RANKING},
    "no_player_reading": {"player": "Alperen Sengun", "shape": TEAM_RANKING},
    "fact_not_read": {"fact": "draft", "words": "3rd nba overall draft picks"},
    "opponent_not_read": {"words": "phnx", "shape": PLAYER_LINE},
    "team_not_read": {"words": "t wolves", "team": "Minnesota Timberwolves", "shape": PLAYER_SPLITS},
    "unread_number": {"shape": PLAYER_LINE, "words": ("5",), "classes": {"number": ("5",)}},
    "unread_stat": {"shape": PLAYER_RANKING, "words": ("3pm",), "classes": {"stat": ("3pm",)}, "names": "3-pointers made", "read": "field goal percentage"},
    "unread_window": {"shape": PLAYER_LINE, "words": ("last",), "classes": {"window": ("last",)}},
    "unread_date": {"shape": PLAYER_LINE, "words": ("november",), "classes": {"date": ("november",)}},
    "denied_role": {"player": "Joel Embiid", "words": "when embiid doesn't start", "role": "start", "shape": PLAYER_LOG},
    "playoff_round": {"shape": PLAYER_LOG, "round": "finals"},
    "non_calendar_situation": {"shape": PLAYER_LINE, "situation": "18 year old", "reads_as": "age"},
    "period_stat": {"shape": PERIOD_RANKING, "stat": "minutes", "period": 4, "half": None},
    "period_as_condition": {"shape": None},
    "team_period_stat": {"shape": TEAM_PERIOD_TOTAL, "stat": "plusMinus"},
    "bench_points": {"shape": TEAM_RANKING},
    "team_boolean_count": {"shape": PLAYER_RANKING, "stat": "triple_double"},
    "cannot_honor": {"shape": PRESENCE_SPLIT, "cells": [{"cell": "range", "since": 2020, "until": 2024}], "without": None, "reason": DeclineReason.BEYOND_WORDS},
    "no_reading_of_point": {"shape": TEAM_RECORD, "planned": TEAM_RECORD},
    "no_team_metric": {"shape": TEAM_RANKING, "stat": "rebounds allowed"},
    "team_seasons_unsummed": {"shape": TEAM_RANKING, "metric": "free throw attempts per game"},
    "team_venue_unsplit": {"shape": TEAM_RANKING, "metric": "pace", "venue": "home"},
    "since_record_unsplit": {"shape": TEAM_RANKING},
    "own_opponent": {"shape": TEAM_PERIOD_TOTAL, "team": "Boston Celtics"},
    "name_missing": {"shape": PointShape("shots", "chart", "shots"), "needs": "player"},
    "span_and_season": {"shape": PLAYER_LOG, "season": 2001, "since": None},
    "no_season_column": {"shape": PLAYER_LINE, "stat": "netpoints"},
    "franchise_career_ranking": {"shape": PLAYER_RANKING},
    "no_shape": {"shape": None},
    "period_relations_question": {"shape": PLAYER_LOG},
    "team_relations_question": {"shape": PLAYER_RANKING},
    "team_only_question": {"shape": TEAM_RECORD},
    "no_subject_reading": {"shape": PLAYER_LOG},
    "own_seasons_window": {"seasons": ["first 9 seasons"]},
    "own_alignment": {"shape": PLAYER_RANKING, "alignment": "in the western conference"},
    "single_season_ranking": {"shape": PLAYER_RANKING, "words": "in a single season"},
    "ranking_needs_stat": {"shape": PLAYER_RANKING},
    "subject_not_read": {"opponent": "Houston Rockets"},
    "own_best_season": {"words": "best season", "player": "Michael Jordan"},
    "roster_not_read": {"team": "Chicago Bulls", "group": "forwards"},
    "measures_combined": {"shape": PLAYER_RANKING, "words": "fgm + opp fgm"},
}


def test_every_cause_a_reading_refuses_by_is_said_naming_its_fact() -> None:
    """Each kind in the closed ``CAUSES`` has a sentence in the planner, and
    it carries the fact the reading named - the stat, the number, the
    names - rather than only the cell or the shape."""
    from association.query.reading import CAUSES

    assert set(_CAUSE_EXAMPLES) == set(CAUSES)
    for kind, facts in _CAUSE_EXAMPLES.items():
        said = refusal_result(Cause(kind=kind, facts=facts)).text
        assert said and said.endswith(".") and "{" not in said, kind
        for value in facts.values():
            if isinstance(value, DeclineReason):
                # A reason is a key the sayer words: its phrase is in the sentence.
                assert decline_reason_phrase(value) in said, (kind, value, said)
            elif isinstance(value, str) and kind not in ("unknown_stat", "needs_threshold", "ranking_unit", "team_streak_of_stat", "team_boolean_count") and value != "under":
                assert value in said, (kind, value, said)
    assert refusal_result(Cause(kind="matchup_needs_two", facts={"names": []})).text == "A matchup needs two players, and none was read."
    # A shape's decline, said word for word.
    assert refusal_result(Cause(kind="cannot_honor", facts=_CAUSE_EXAMPLES["cannot_honor"])).text == (
        "Nothing here answers this question as a record with and without named players: the seasons 2019-20 through 2023-24 cannot be read here - it would answer for a different span than was asked."
    )
    assert refusal_result(
        Cause(
            kind="cannot_honor",
            facts={
                "shape": LINE_RUNS,
                "cells": [{"cell": "situation", "words": "on tuesdays"}],
                "without": "team or player",
                "reason": DeclineReason.NO_SINGLE_SUBJECT,
            },
        )
    ).text == ("Nothing here answers this question as a streak: the games 'on tuesdays' cannot be read here with no named team or player - the league-wide streak has no single subject to narrow.")
    assert refusal_result(Cause(kind="needs_threshold", facts={"shape": LINE_RUNS, "stat": "threePointFieldGoalsMade"})).text == (
        "A streak needs the number of 3-pointers each game has to reach, and none was read."
    )


def test_a_ranking_in_a_unit_its_metric_has_no_form_of_is_the_readings_cause(cx_ctx: AnswerContext) -> None:
    """ "who were the top 10 in defensive netpoints / 90": the season-line
    ranking's refusal is the point reader's cause (``ranking_unit``), said
    by the planner."""
    reading = with_point(
        "top 10 in points / 90", Reading(**(Reading(measure=Measure(key="points", as_typed="points", how="per_90", unit="/ 90"))).filters(), asked=PLAYER_RANKING, subject=Subject("everyone"))
    )
    assert reading.point is None and reading.point_refusal == Cause(kind="ranking_unit", facts={"metric": "avg_points", "rate": "/ 90"})
    assert refusal_result(reading.point_refusal).text == "No leaderboard ranks avg points per 90 minutes - the warehouse stores it only per game or as a season total."


def test_a_team_season_readers_decline_is_a_cause_the_planner_says() -> None:
    """A team-season reader's decline - no team metric, a span it cannot
    sum, a venue split it does not hold - is a cause, kept through the only
    reader's door (``compose._read_only``) and said by the planner; a metric
    the words never named is said so, never "for stat None"."""
    from association.query import compose
    from association.query.reading import ShapeDeclined

    def declines() -> None:
        raise ShapeDeclined("no_team_metric", {"stat": None}, "the words name no team metric")

    with pytest.raises(ShapeDeclined) as kept:
        compose._read_only(declines, TEAM_RANKING)
    assert kept.value.cause == Cause(kind="no_team_metric", facts={"stat": None})
    said = refusal_result(ShapeDeclined.under(kept.value.cause, TEAM_RANKING)).text
    assert said == "Nothing here answers this question as a ranking of teams: the words name no team metric."
    assert refusal_result(Cause(kind="no_team_metric", facts={"shape": TEAM_RANKING, "stat": "rebounds allowed"})).text == (
        "Nothing here answers this question as a ranking of teams: no team metric for stat 'rebounds allowed'."
    )


def test_a_team_named_as_its_own_opponent_is_said_by_name() -> None:
    """#277: the team named twice is the cause, said by name, never only the
    rule ("a team cannot be its own opponent")."""
    assert refusal_result(Cause(kind="own_opponent", facts={"shape": TEAM_PERIOD_TOTAL, "team": "Boston Celtics"})).text == (
        "Nothing here answers this question as a team's quarter or half: the Boston Celtics are named as both the team and its opponent - name the team they played."
    )


def test_an_unread_number_with_its_plus_is_said_with_examples_of_the_number() -> None:
    """ "20+" is an unread number; the examples say the number, never "20++
    points"."""
    from association.query.compose.say import refusal_phrase

    text = refusal_phrase("unread_number", {"shape": PLAYER_LINE, "words": ("20+",), "classes": {"number": ("20+",)}})
    assert "'20+'" in text and '"last 20 games", "20+ points", "at least 20 games"' in text


def test_a_readers_decline_by_a_cause_is_not_headed_relation() -> None:
    """A reader that steps aside (``compose._read_log``) keeps a decline's
    cause - a shape's own, or a point reading's a shared step raised -
    never a sentence headed "relation:"; a decline with no cause keeps the
    "relation:" sentence."""
    from association.query import compose
    from association.query.reading import PointRefused, ShapeDeclined, Unsupported

    def season_and_career() -> None:
        raise ShapeDeclined("span_and_season", {"season": 2001, "since": None}, "a career span and the 2001 season at once")

    def no_stat() -> None:
        raise PointRefused(Cause(kind="line_names_no_stat", facts={"phrase": "below 8000", "side": "under"}), "'below 8000' names no box-score stat")

    def plain() -> None:
        raise Unsupported("a plain decline")

    with pytest.raises(ShapeDeclined) as kept:
        compose._read_log(season_and_career)
    assert refusal_result(ShapeDeclined.under(kept.value.cause, PLAYER_LOG)).text == "Nothing here answers this question as a game log: a career span and the 2001 season at once."
    with pytest.raises(ShapeDeclined) as caused:
        compose._read_log(no_stat)
    assert caused.value.cause == Cause(kind="line_names_no_stat", facts={"phrase": "below 8000", "side": "under"})
    with pytest.raises(Unsupported, match=r"^relation: a plain decline$"):
        compose._read_log(plain)
