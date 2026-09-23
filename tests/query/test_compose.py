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

import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import duckdb
import pytest

from association.nba.season import current_season
from association.query.compose import answer as compose_answer
from association.query.compose.adapt import to_query
from association.query.compose.core import Query, Refused, Unsupported, compile_query, run
from association.query.compose.move import _career_slots, move_point
from association.query.templates.common import TemplateContext

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
    refused rather than silently dropped."""
    q = to_query("game_log", {"player": "Brandin Podziemski", "round": "finals"})
    with pytest.raises(Unsupported, match="cannot honor"):
        compile_query(cx_ctx.con, q)


# ---------------------------------------------------------------------------
# K2 moves.
# ---------------------------------------------------------------------------


def test_a_measure_word_the_router_did_not_name_moves_the_point(cx_ctx: TemplateContext) -> None:
    """A measure word in the question ("plus-minus") is added to a log's
    line even though the router's own ``stat`` slot said nothing about it."""
    q = move_point(cx_ctx.con, "game_log", {"player": "Brandin Podziemski"}, "Podziemski's plus-minus each game this season")
    out = _run(cx_ctx.con, q)
    g3 = next(r for r in out["rows"] if r["points"] == 28)
    assert g3["plusMinus"] == 17


def test_most_in_a_game_moves_to_rows_by_measure(cx_ctx: TemplateContext) -> None:
    """ "Career-high" moves a ``player_stat``-shaped point to rows ordered by
    the named measure - the top game(s), not an average."""
    q = move_point(cx_ctx.con, "player_stat", {"player": "Brandin Podziemski", "stat": "points"}, "Podziemski's career-high in points")
    assert q.skeleton == "rows" and q.order == "measure"
    out = _run(cx_ctx.con, q)
    assert out["rows"][0]["points"] == 28  # g3, his career high


def test_how_many_won_is_a_career_count_with_a_predicate(cx_ctx: TemplateContext) -> None:
    """ "How many ... has he won" moves to a career count with the ``won``
    predicate, whatever season was (or was not) named - route()'s own
    unscoped-count-is-career rule, read here in code."""
    q = move_point(cx_ctx.con, "record_when", {"player": "Brandin Podziemski"}, "how many games has Podziemski's team won?")
    assert q.slots.get("span") == "career"  # Query.span (a binding-parity override) is unset; the slot itself carries it
    out = _run(cx_ctx.con, q)
    assert out["rows"][0]["games"] == 4  # g1, g3, g5, g7 - every win across both seasons (g4 DNP excluded)


def test_career_slots_forces_a_career_span_only_when_nothing_else_scoped_it() -> None:
    """The helper ``_move_how_many_won``/``_move_boolean_count`` share: no
    season, span or since means "his career"; any of the three left alone."""
    assert _career_slots({"player": "X"})["span"] == "career"
    assert _career_slots({"player": "X", "season": 2024}).get("span") is None
    assert _career_slots({"player": "X", "span": "career"})["span"] == "career"
    assert _career_slots({"player": "X", "since": 2020}).get("span") is None


def test_a_ranking_word_with_no_player_groups_by_player_league_wide(cx_ctx: TemplateContext) -> None:
    """A ranking word with no player subject reads the league-wide grouped
    point - the leaderboard shape a template refuses because no player was
    named."""
    q = move_point(cx_ctx.con, "other", {}, "top scorers this season")
    assert q.subject == "everyone" and q.skeleton == "grouped" and q.group == "player"
    out = _run(cx_ctx.con, q)
    assert out["rows"][0]["group"] == "Derek Vollmer"
    assert out["rows"][0]["points"] == pytest.approx(30.0)


def test_a_position_word_with_no_player_reads_that_positions_log(cx_ctx: TemplateContext) -> None:
    """A position word with no player subject reads that group's log, rows -
    not a ranking, since no ranking word was asked for."""
    q = move_point(cx_ctx.con, "other", {}, "centers game log this season")
    assert q.subject == "everyone" and q.skeleton == "rows" and q.position == "C"
    out = _run(cx_ctx.con, q)
    assert {r["points"] for r in out["rows"]} == {18, 14}  # Sabonis - the only center on record


def test_the_questions_own_number_names_its_column_not_the_routers_stat(cx_ctx: TemplateContext) -> None:
    """A threshold's own words ("30 point") name its column, even where the
    router's ``stat`` slot names a different one entirely."""
    q = move_point(cx_ctx.con, "threshold_count", {"threshold": 30, "stat": "rebounds"}, "players with a 30 point game this season")
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
    """A near-miss spelling ("Podzemski") is a handled refusal - the
    relation's own suggestion - not a bare "cannot answer": it comes back as
    :class:`Refused`, carrying the template-shaped result."""
    q = to_query("game_log", {"player": "Podzemski"})
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
    own values - what a caller checks an answer against."""
    result = compose_answer(cx_ctx, "player_stat", {"player": "Brandin Podziemski", "opponent": "Boston Celtics"}, "Podziemski's points vs Boston")
    assert result is not None
    assert "Brandin Podziemski" in result.answer
    assert result.data["skeleton"] == "scalar"
    assert result.data["player"] == "Brandin Podziemski"
    assert result.data["rows"][0]["games"] == 2
    assert result.artifacts == []


def test_a_refusal_from_answer_is_the_relations_own(cx_ctx: TemplateContext) -> None:
    """A near-miss name is a handled refusal from ``answer()``, not ``None``."""
    result = compose_answer(cx_ctx, "game_log", {"player": "Podzemski"}, "Podzemski's last 5 games")
    assert result is not None
    assert re.search(r"podziemski", result.answer, re.I)
