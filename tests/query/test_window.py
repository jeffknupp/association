"""The window family, typed (Phase 3, step 2's second slice): the one tagger
that reads which rows a question keeps and from which end
(:func:`association.query.window.read_window`), the characters it claims,
the typed value's door and projection (:class:`association.query.reading.Window`),
the relation's one resolution of it (:func:`association.query.player_relation.relation_window`,
:func:`association.query.season_line.history_seasons`), and the behavioral
check contract 4 asks for: applying the window cell on each relation changes
the result.
"""

from __future__ import annotations

import itertools
from typing import Any

import pytest
from test_templates import pg_ctx, team_cells_con  # noqa: F401 - the two relation fixtures, imported by name

from association.nba.season import current_season
from association.query.answer import AnswerContext
from association.query.compose.core import Query, compile_query, rows_of
from association.query.compose.plan import cells_stated
from association.query.player_relation import RELATION_SCOPING, RELATION_SCOPING_EXCLUDED, relation_window
from association.query.point import ORDER_ASKS, minimum_under, span_under, window_under
from association.query.reading import (
    GAME_HIGHS,
    LINE_RUNS,
    NETPOINTS_FINGERPRINT,
    NETPOINTS_RATINGS,
    PERIOD_LOG,
    PLAYER_LINE,
    PLAYER_LOG,
    PLAYER_MEETINGS,
    PLAYER_RANKING,
    SEASON_HISTORY,
    SHOT_CHART,
    SHOT_DISTANCE,
    TEAM_MEETINGS,
    TEAM_PERIOD_TOTAL,
    TEAM_RANKING,
    TEAM_RECORD,
    Aggregate,
    Claim,
    Cuts,
    Minimum,
    PointShape,
    Reading,
    ScopeError,
    Span,
    Window,
    unhonored_cells,
)
from association.query.reading import Subject as Who
from association.query.season_line import history_seasons
from association.query.span import SpanContext, claimed, read_span
from association.query.team_games import TeamNarrowed, rows_sql
from association.query.team_relation import TEAM_RELATION_SCOPING, TEAM_RELATION_SCOPING_EXCLUDED, scoped_team, team_games
from association.query.window import MinimumRead, WindowContext, WindowRead, contradicting_ends, own_seasons_window, per_season_window, read_minimum, read_window

S = current_season()


def _read(question: str, shape: PointShape | None = PLAYER_LOG, **context: Any) -> Window:
    return window_under(shape, read_window(question, WindowContext(**context))).window


# ---------------- the tagger: words to a Window ----------------


@pytest.mark.parametrize(
    ("question", "shape", "expected"),
    [
        ("jokic stats", PLAYER_LOG, Window()),
        ("How did the Celtics do in their last 10 games?", PLAYER_LOG, Window(order="recent", count=10)),
        ("what did Nikola Jokic do in his last game?", PLAYER_LOG, Window(order="recent", count=1)),
        ("What was Curry's first game of the season?", PLAYER_LOG, Window(order="first", count=1)),
        ("Lakers opening game of the season", PLAYER_LOG, Window(order="first", count=1)),
        ("josh hart gamelog season opener", PLAYER_LOG, Window(order="first", count=1)),  # codespell:ignore hart - a surname
        ("magic vs nets last 10", PLAYER_LOG, Window(order="recent", count=10)),
        ("Steph Curry's final two regular season games", PLAYER_LOG, Window(order="recent", count=2)),
        ("Jrue holiday last fifty games as a starter", PLAYER_LOG, Window(order="recent", count=50)),
        ("tyrese maxey last twenty-five games", PLAYER_LOG, Window(order="recent", count=25)),
        ("Knicks last 5 playoff games", PLAYER_LOG, Window(order="recent", count=5)),
        # "top N" is a count and no end of the span.
        ("top 5 rebounders on the Lakers in the playoffs", PLAYER_RANKING, Window(count=5)),
        ("top twelve scorers", PLAYER_RANKING, Window(count=12)),
        # "who led the league" sets no count: the ranking leads with the one and names the next.
        ("who led the league in assists this season?", PLAYER_RANKING, Window()),
        ("how many points does embiid average", PLAYER_LINE, Window()),
        # A count of seasons is the span's, not a window of games - except for a history, whose count IS seasons.
        ("Luka's ppg over the last 10 seasons", PLAYER_LINE, Window()),
        ("Luka's ppg over the last 10 seasons", SEASON_HISTORY, Window(count=10, of="seasons")),
        ("show tyrese maxey's games against boston in the past two seasons", PLAYER_LOG, Window()),
        ("tatum's last 5 games in the past two seasons", PLAYER_LOG, Window(order="recent", count=5)),
        # A range the words name wins over a history's relative count.
        ("lebron's ppg since 2020 over the last 5 seasons", SEASON_HISTORY, Window()),
        # A line's own number is no count of games, and a period's ordinal is no window.
        ("mikal bridges game log with less than 15 fga and with less than 35 minutes", PLAYER_LOG, Window()),
        ("Portis vs bulls 2019-20 to 2023-24", PLAYER_LINE, Window()),
        ("harrison barnes 1st quarter stats each game vs magic", PERIOD_LOG, Window()),
        ("Rudy gobert first half games this season", PERIOD_LOG, Window()),
        ("zach collins first quarter stats last 5 games as a starter", PERIOD_LOG, Window(order="recent", count=5)),
        ("evan mobley avg against bucks", PLAYER_LINE, Window()),
    ],
)
def test_the_words_read_as_one_window(question: str, shape: PointShape | None, expected: Window) -> None:
    assert _read(question, shape) == expected


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        # ISSUES.md #369: a misspelled "games" after "last N" - one edit from
        # "game(s)" - and "match(es)" or "match ups" are the window's games.
        ("Lamelo ball stats last 5 gMes", Window(order="recent", count=5)),
        ("Draymond green vs mavs last 6 gamew", Window(order="recent", count=6)),
        ("gary trent jr. vs orlando magic last nine gane", Window(order="recent", count=9)),
        ("Giannas last 10 gamess", Window(order="recent", count=10)),
        ("christain braun last 10 gamea vs toronto", Window(order="recent", count=10)),
        ("Luka doncic last 5 away hames", Window(order="recent", count=5)),
        ("anthony davis vs orlando last 5 match", Window(order="recent", count=5)),
        ("last 5 matchs shai gilgeous alexander", Window(order="recent", count=5)),  # codespell:ignore matchs - the feed's question as typed
        ("suns vs jazz last 10 match ups", Window(order="recent", count=10)),
        ("tatum first 3 gmaes of the season", Window(order="first", count=3)),
        # A word of its own one edit from "games" is not one: a name stands there.
        ("lebron last 10 james harden games", Window()),
        ("his last 5 names", Window()),
    ],
)
def test_a_misspelled_games_after_a_count_is_the_windows_games(question: str, expected: Window) -> None:
    assert _read(question, PLAYER_LOG) == expected


def test_one_game_of_a_players_line_is_the_end_and_a_count_of_one() -> None:
    """ "his last game" on player_stat is one game at one end of the span,
    which the line answers by handing the question to the log - and the
    log needs BOTH parts, since an end alone would list his last ten."""
    assert _read("how many points did curry score in his last game", PLAYER_LINE) == Window(order="recent", count=1)
    assert _read("curry's stats in his first game of 2026", PLAYER_LINE) == Window(order="first", count=1)
    assert _read("steph curry's last regular season game", PLAYER_LINE) == Window(order="recent", count=1)
    # "last N games" is a count, not a single game: the count stays and this rule keeps out of it.
    assert _read("curry stats in his last 5 games", PLAYER_LINE) == Window(order="recent", count=5)


def test_an_end_the_grammar_missed_is_read_on_a_reader_that_honors_one() -> None:
    """On an intent whose reader honors an end (ORDER_ASKS) the looser
    ORDER_WORDS fill one the grammar read none for; on the four where an
    end means ONE game it stands only beside a game the words name."""
    assert _read("curry's latest games", PLAYER_LOG) == Window(order="recent")
    assert _read("curry's latest games", PLAYER_LINE) == Window()
    # A filler end costs a chart the season (#153): "a shot chart of steph curry's
    # 2025 season for 3 point shots" drew one game where 2025 held hundreds.
    assert _read("show a shot chart of steph curry's 2025 season for 3 point shots", SHOT_CHART) == Window()
    assert _read("Create a shot chart for steph curry's last two games of the regular season", SHOT_CHART) == Window(count=2)
    assert _read("show me a fingerprint for steph curry's last game in 2026", NETPOINTS_FINGERPRINT) == Window(order="recent", count=1)
    # One game named as his in a phrasing the grammar misses is that game on these readers too (2026-10-09: the
    # chart drew the season's home games, the log listed ten, with nothing saying "last" went unread).
    assert _read("curry's shot chart for his last home game", SHOT_CHART) == Window(order="recent", count=1)
    assert _read("jokic game log for his first road game", PLAYER_LOG) == Window(order="first", count=1)
    assert _read("curry's shot chart for his last home game", TEAM_MEETINGS) == Window()
    # A quarter's ordinal sits where that rule allows two words: the period readers never take it.
    assert _read("harrison barnes's first quarter per game against the magic", PERIOD_LOG) == Window()
    assert _read("rudy gobert's first half game log", PERIOD_LOG) == Window()
    assert _read("fingerprint for curry's first game of 2026", NETPOINTS_FINGERPRINT) == Window(order="first", count=1)


def test_an_end_on_a_reader_with_no_window_of_its_own_stands_where_games_are_said() -> None:
    """An end said with "games" stands on a reader that refuses one, for the
    planner to refuse by name (ISSUES.md #352: "Warriors vs Mavs record last
    ten games" tallied this season's three meetings once its end went); an
    end said with no "games" beside a larger count goes, the count standing;
    beside a count of one both stand."""
    assert _read("lakers vs mavs record last 10 home games played", TEAM_MEETINGS) == Window(order="recent", count=10)
    assert _read("Warriors vs Mavs record last ten games", TEAM_MEETINGS) == Window(order="recent", count=10)
    assert _read("lamelo ball last 7 home games vs suggs", PLAYER_MEETINGS) == Window(order="recent", count=7)
    assert _read("Warriors vs Mavs record last 10 games", TEAM_MEETINGS) == Window(order="recent", count=10)
    assert _read("damian lillard stats vs magic last 10", PLAYER_LINE) == Window(count=10)
    assert _read("Divencezo season opener stats career", PLAYER_LINE) == Window(order="first", count=1)


def test_a_team_rankings_end_and_a_boolean_rankings_measure() -> None:
    assert _read("nba team with least playoff wins since 2022", TEAM_RANKING) == Window(rank="fewest")
    assert _read("Best record from 2010-11 to 2018-19 nba", TEAM_RANKING) == Window(rank="best")
    assert _read("Detroit Pistons most points in a first half this season", TEAM_PERIOD_TOTAL) == Window(rank="most")
    assert _read("least points scored by the wizards in the first half this season", TEAM_PERIOD_TOTAL) == Window(rank="fewest")
    # The table's order decides where two words stand: "worst" before "best", "fewest" before "most".
    assert _read("best and worst records", TEAM_RANKING) == Window(rank="worst")
    assert _read("most free throws this season by teams", PLAYER_RANKING) == Window()
    assert _read("players with the highest scoring triple doubles", PLAYER_RANKING, boolean_stat=True) == Window(by="points")
    assert _read("most rebounds in a triple double", PLAYER_RANKING, boolean_stat=True) == Window(by="rebounds")
    assert _read("most triple doubles", PLAYER_RANKING, boolean_stat=True) == Window()
    assert _read("players with the highest scoring triple doubles", PLAYER_RANKING, boolean_stat=False) == Window()


# ---------------- the claims ----------------


def test_the_tagger_claims_the_characters_it_read_once() -> None:
    question = "jokic stats in his last 5 playoff games"
    read = window_under(PLAYER_LOG, read_window(question, WindowContext()))
    assert read.claims == (Claim(19, 39, "window"),)
    assert question[19:39] == "last 5 playoff games"
    ranked = window_under(PLAYER_RANKING, read_window("players with the highest scoring triple doubles", WindowContext(boolean_stat=True)))
    assert [c.what for c in ranked.claims] == ["ranked_by"] and ranked.claims[0].start == 17
    history = window_under(SEASON_HISTORY, read_window("sga's 2pt percentage for the past 5 years", WindowContext()))
    assert history.claims == (Claim(29, 41, "window"),)
    rank = window_under(TEAM_RANKING, read_window("nba team with least playoff wins", WindowContext()))
    assert rank.claims == (Claim(14, 19, "rank"),)


@pytest.mark.parametrize(
    "question",
    [
        "jokic stats in his last 5 playoff games",
        "Knicks last 5 regular season games",
        "sga's 2pt percentage for the past 5 years",
        "curry's last game of the 2024 season",
        "steph curry's last regular season game",
        "lebron's last 10 games including the playoffs",
    ],
)
def test_the_windows_claims_and_the_spans_fold_and_never_cross(question: str) -> None:
    """The two taggers claim the same question: a span claim inside a window
    claim ("playoff" in "last 5 playoff games") folds, and a partial overlap
    would be two rules reading one word, which fails the reader."""
    shape = SEASON_HISTORY if "past" in question else PLAYER_LOG
    window = window_under(shape, read_window(question, WindowContext()))
    span = span_under(shape, read_span(question, SpanContext(window_named=window.window.count is not None, order=window.window.order, limit=window.window.count)))
    merged = claimed([*window.claims, *span.claims])
    assert all(a.end <= b.start for a, b in itertools.pairwise(merged))


# ---------------- the typed value ----------------


def test_a_count_below_one_is_refused_by_the_window() -> None:
    """A window keeps at least one row, and a count says of what - games
    unless the tagger read seasons (``Window.of``)."""
    with pytest.raises(ScopeError, match="below 1"):
        Window(count=0)
    assert Window(count=4).of == "games"


def test_the_cells_and_what_a_reader_leaves_unhonored() -> None:
    assert Window().cells() == frozenset() and not Window().named
    assert Window(count=5).cells() == frozenset() and Window(count=5).named
    assert Window(order="recent", count=5).cells() == {"window"}
    assert Window(by="points").cells() == {"ranked_by"}
    assert unhonored_cells(Reading(window=Window(order="recent", count=5, by="points")), frozenset()) == ["window", "ranked_by"]
    assert unhonored_cells(Reading(window=Window(order="first", count=1)), frozenset({"window"})) == []
    assert unhonored_cells(Reading(cuts=Cuts(opponent="BOS"), window=Window(order="recent", count=3)), frozenset({"opponent"})) == ["window"]
    assert unhonored_cells(Reading(window=Window(by="points", count=10)), frozenset({"window"})) == ["ranked_by"]


def test_the_relation_tables_declare_the_window_cell_once() -> None:
    assert Window.CELLS <= RELATION_SCOPING and "window" in TEAM_RELATION_SCOPING
    assert cells_stated(PLAYER_LOG) >= Window.CELLS and "window" not in cells_stated(LINE_RUNS)
    assert "window" not in cells_stated(TEAM_RECORD) and "window" in cells_stated(PointShape("team_games", "rows", "date"))
    for table in (RELATION_SCOPING_EXCLUDED, TEAM_RELATION_SCOPING_EXCLUDED):
        assert all("order" not in row.unstated and "limit" not in row.unstated for row in table.values())
    assert {NETPOINTS_FINGERPRINT, PLAYER_LOG, PERIOD_LOG, NETPOINTS_RATINGS, SHOT_CHART, SHOT_DISTANCE, TEAM_PERIOD_TOTAL} >= ORDER_ASKS


# ---------------- the relation's one resolution ----------------


def test_relation_window_reads_an_end_or_a_bare_count_as_the_newest() -> None:
    assert relation_window(Reading()) is None
    assert relation_window(Reading(window=Window(order="recent", count=10))) == ("recent", 10)
    assert relation_window(Reading(window=Window(order="first", count=3))) == ("first", 3)
    # A bare count is the newest N on the games relations; an end alone is one game.
    assert relation_window(Reading(window=Window(count=7))) == ("recent", 7)
    assert relation_window(Reading(window=Window(order="first"))) == ("first", 1)
    assert relation_window(Reading(window=Window(count=400))) == ("recent", 50)
    assert history_seasons(Reading(window=Window(count=6, of="seasons"))) == 6
    assert history_seasons(Reading()) == 4 and history_seasons(Reading(span=Span(career=True))) is None


# ---------------- contract 4: applying the cell changes the result ----------------


def _player_games(ctx: AnswerContext, window: Window) -> tuple[int, int]:
    """The games and the points a total over Podziemski's season reads under
    ``window`` - an aggregate, since the relation cuts the window before the
    sum (``_windowed``); a rows read takes the point's own limit instead."""
    (row,) = rows_of(
        ctx.con,
        compile_query(ctx.con, Query(**(Reading(who=Who(kind="player", players=("Brandin Podziemski",)), window=window)).filters(), skeleton="scalar", measures=["points"], aggregate=Aggregate.TOTAL)),
    )
    return int(row["games"]), int(row["points"])


def test_the_window_cell_changes_what_the_player_relation_reads(pg_ctx: AnswerContext) -> None:  # noqa: F811 - the fixture
    assert _player_games(pg_ctx, Window()) == (3, 45)  # e1, e2, e3 this season
    assert _player_games(pg_ctx, Window(order="recent", count=2)) == (2, 35)  # e2, e3
    assert _player_games(pg_ctx, Window(order="first", count=1)) == (1, 10)  # e1
    assert _player_games(pg_ctx, Window(count=2)) == (2, 35)  # a bare count is the newest N


def _team_games(ctx: AnswerContext, window: Window) -> list[str]:
    settled = scoped_team(ctx.con, Reading(who=Who(kind="team", teams=("Celtics",))), "no team")
    assert isinstance(settled, tuple)
    team, resolved = settled
    narrowed = team_games(ctx.con, team, resolved, Reading(window=window), opponent=None)
    assert isinstance(narrowed, TeamNarrowed)
    sql, params = rows_sql(narrowed, "tg.event_id", order="tg.eastern_date")
    return [str(row[0]) for row in ctx.con.execute(sql, params).fetchall()]


def test_the_window_cell_changes_what_the_team_relation_reads(team_cells_con: AnswerContext) -> None:  # noqa: F811 - the fixture
    every = _team_games(team_cells_con, Window())
    assert len(every) == 2  # r4, r5 this season
    assert _team_games(team_cells_con, Window(order="recent", count=1)) == every[-1:]
    assert _team_games(team_cells_con, Window(order="first", count=1)) == every[:1]


def test_read_window_returns_the_window_and_its_claims() -> None:
    read = window_under(PLAYER_LOG, read_window("curry last 3 games", WindowContext()))
    assert isinstance(read, WindowRead) and read.window == Window(order="recent", count=3) and len(read.claims) == 1


# ---------------- a ranking's stated qualifier (ISSUES.md #377) ----------------


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("nba plus minus per game leaders minimum 100 games played", Minimum(count=100, unit="games", word="games played")),
        ("nba pg highest win% games at least 300 gp", Minimum(count=300, unit="games", word="gp")),
        ("highest true shooting percentage in a playoff run minimum 10 games and average 10 ppg", Minimum(count=10, unit="games", word="games")),
        ("highest single season 3pt percent minimum 20 attempts", Minimum(count=20, unit="attempts", word="attempts")),
        ("who has the worst fg% with atleast 1000 fg attempted", Minimum(count=1000, unit="attempts", of="fg", word="fg attempted")),
        ("who has the worst 3pt% with atleast 1000 3pt attempted", Minimum(count=1000, unit="attempts", of="3pt", word="3pt attempted")),
        ("career 3pt% leaders with a minimum of 2,000 three point attempts", Minimum(count=2000, unit="attempts", of="3pt", word="three point attempts")),
        ("three point leaders min. 200 3pa", Minimum(count=200, unit="attempts", of="3pt", word="3pa")),
        ("top scorers with at least 10 seasons", Minimum(count=10, unit="other", word="seasons")),
        # A box-score word after the number is a line's, and minutes the floor of minutes: neither is a qualifier.
        ("anthony edwards log with at least 50 points", None),
        ("nba players who averaged a minimum of 20 points per game", None),
        ("centers min 2 threes v bos gamelog 2024", None),
        ("jokic stats with at least 30 minutes", None),
    ],
)
def test_the_words_read_as_a_stated_minimum(question: str, expected: Minimum | None) -> None:
    read = read_minimum(question)
    assert read.minimum == expected
    assert len(read.claims) == (0 if expected is None else 1)


def test_a_stated_minimum_stands_on_a_players_ranking_alone() -> None:
    read = read_minimum("most points per game minimum 70 games")
    assert minimum_under(PLAYER_RANKING, read).minimum == Minimum(count=70, unit="games", word="games")
    assert minimum_under(GAME_HIGHS, read) == MinimumRead(None)
    assert minimum_under(PLAYER_LOG, read) == MinimumRead(None)


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        # Both ends at once (ISSUES.md #378): the words of each, in the question's order.
        ("lebron james last 5 first games stats", ["last 5", "first games"]),
        ("first 10 games and last 5 games for curry", ["first 10", "first 10 games", "last 5", "last 5 games"]),
        # One end, or a count of seasons beside a window of games, is no contradiction.
        ("lebron james first 5 games stats", []),
        ("lebron james last 5 games", []),
        ("warriors first 10 games of the last 3 seasons", []),
    ],
)
def test_a_question_naming_both_ends_is_found_by_the_words_of_each(question: str, expected: list[str]) -> None:
    assert [question[claim.start : claim.end] for claim in contradicting_ends(question)] == expected


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        # A window of games taken in each season (ISSUES.md #379): the window's words, then the season's.
        ("warriors first 10 game record every season", ["first 10 game", "every season"]),
        ("warriors record in the first 10 games of each season", ["first 10 games", "each season"]),
        ("celtics last 5 games each season", ["last 5 games", "each season"]),
        # A history by season with no window, or a window with no season beside it, is none.
        ("kobe bryant points per game each season", []),
        ("warriors first 10 games", []),
    ],
)
def test_a_window_taken_in_each_season_is_found_by_its_words(question: str, expected: list[str]) -> None:
    assert [question[claim.start : claim.end] for claim in per_season_window(question)] == expected


@pytest.mark.parametrize(
    ("question", "words"),
    [
        # ISSUES.md #392: a career's own seasons by their place.
        ("lebron james stats through their first 9 seasons", ["their first 9 seasons"]),
        ("Any Player with better Stats in his first 9 seasons than LeBron in his last 9 seasons?", ["his first 9 seasons", "his last 9 seasons"]),
        ("wembanyama first 2 years", ["first 2 years"]),
        # The calendar's last N seasons, and a window of games, are not.
        ("lebron stats last 3 seasons", []),
        ("lebron first 10 games", []),
    ],
)
def test_a_careers_own_seasons_by_their_place(question: str, words: list[str]) -> None:
    assert [question[c.start : c.end] for c in own_seasons_window(question)] == words
