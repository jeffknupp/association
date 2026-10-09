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
from association.query.player_relation import RELATION_SCOPING, RELATION_SCOPING_EXCLUDED, relation_scoping, relation_window
from association.query.reading import Claim, Cuts, Scope, ScopeError, Span, Window, unhonored_cells
from association.query.season_line import history_seasons
from association.query.span import SpanContext, claimed, read_span
from association.query.team_games import TeamNarrowed, rows_sql
from association.query.team_relation import TEAM_RELATION_SCOPING, TEAM_RELATION_SCOPING_EXCLUDED, scoped_team, team_games, team_relation_scoping
from association.query.window import ORDER_INTENTS, WindowContext, WindowRead, read_window

S = current_season()


def _read(question: str, intent: str = "game_log", **context: Any) -> Window:
    return read_window(question, WindowContext(intent=intent, **context)).window


# ---------------- the tagger: words to a Window ----------------


@pytest.mark.parametrize(
    ("question", "intent", "expected"),
    [
        ("jokic stats", "game_log", Window()),
        ("How did the Celtics do in their last 10 games?", "game_log", Window(order="recent", count=10)),
        ("what did Nikola Jokic do in his last game?", "game_log", Window(order="recent", count=1)),
        ("What was Curry's first game of the season?", "game_log", Window(order="first", count=1)),
        ("Lakers opening game of the season", "game_log", Window(order="first", count=1)),
        ("josh hart gamelog season opener", "game_log", Window(order="first", count=1)),  # codespell:ignore hart - a surname
        ("magic vs nets last 10", "game_log", Window(order="recent", count=10)),
        ("Steph Curry's final two regular season games", "game_log", Window(order="recent", count=2)),
        ("Jrue holiday last fifty games as a starter", "game_log", Window(order="recent", count=50)),
        ("tyrese maxey last twenty-five games", "game_log", Window(order="recent", count=25)),
        ("Knicks last 5 playoff games", "game_log", Window(order="recent", count=5)),
        # "top N" is a count and no end of the span.
        ("top 5 rebounders on the Lakers in the playoffs", "leaderboard", Window(count=5)),
        ("top twelve scorers", "leaderboard", Window(count=12)),
        # "who led the league" sets no count: the ranking leads with the one and names the next.
        ("who led the league in assists this season?", "leaderboard", Window()),
        ("how many points does embiid average", "player_stat", Window()),
        # A count of seasons is the span's, not a window of games - except for a history, whose count IS seasons.
        ("Luka's ppg over the last 10 seasons", "player_stat", Window()),
        ("Luka's ppg over the last 10 seasons", "player_history", Window(count=10, of="seasons")),
        ("show tyrese maxey's games against boston in the past two seasons", "game_log", Window()),
        ("tatum's last 5 games in the past two seasons", "game_log", Window(order="recent", count=5)),
        # A range the words name wins over a history's relative count.
        ("lebron's ppg since 2020 over the last 5 seasons", "player_history", Window()),
        # A line's own number is no count of games, and a period's ordinal is no window.
        ("mikal bridges game log with less than 15 fga and with less than 35 minutes", "game_log", Window()),
        ("Portis vs bulls 2019-20 to 2023-24", "player_stat", Window()),
        ("harrison barnes 1st quarter stats each game vs magic", "period_split", Window()),
        ("Rudy gobert first half games this season", "period_split", Window()),
        ("zach collins first quarter stats last 5 games as a starter", "period_split", Window(order="recent", count=5)),
        ("evan mobley avg against bucks", "player_stat", Window()),
    ],
)
def test_the_words_read_as_one_window(question: str, intent: str, expected: Window) -> None:
    assert _read(question, intent) == expected


def test_one_game_of_a_players_line_is_the_end_and_a_count_of_one() -> None:
    """ "his last game" on player_stat is one game at one end of the span,
    which the line answers by handing the question to the log - and the
    log needs BOTH parts, since an end alone would list his last ten."""
    assert _read("how many points did curry score in his last game", "player_stat") == Window(order="recent", count=1)
    assert _read("curry's stats in his first game of 2026", "player_stat") == Window(order="first", count=1)
    assert _read("steph curry's last regular season game", "player_stat") == Window(order="recent", count=1)
    # "last N games" is a count, not a single game: the count stays and this rule keeps out of it.
    assert _read("curry stats in his last 5 games", "player_stat") == Window(order="recent", count=5)


def test_an_end_the_grammar_missed_is_read_on_a_reader_that_honors_one() -> None:
    """On an intent whose reader honors an end (ORDER_INTENTS) the looser
    ORDER_WORDS fill one the grammar read none for; on the four where an
    end means ONE game it stands only beside a game the words name."""
    assert _read("curry's latest games", "game_log") == Window(order="recent")
    assert _read("curry's latest games", "player_stat") == Window()
    # A filler end costs a chart the season (#153): "a shot chart of steph curry's
    # 2025 season for 3 point shots" drew one game where 2025 held hundreds.
    assert _read("show a shot chart of steph curry's 2025 season for 3 point shots", "shot_chart") == Window()
    assert _read("Create a shot chart for steph curry's last two games of the regular season", "shot_chart") == Window(count=2)
    assert _read("show me a fingerprint for steph curry's last game in 2026", "fingerprint") == Window(order="recent", count=1)
    # One game named as his in a phrasing the grammar misses is that game on these readers too (2026-10-09: the
    # chart drew the season's home games, the log listed ten, with nothing saying "last" went unread).
    assert _read("curry's shot chart for his last home game", "shot_chart") == Window(order="recent", count=1)
    assert _read("jokic game log for his first road game", "game_log") == Window(order="first", count=1)
    assert _read("curry's shot chart for his last home game", "head_to_head") == Window()
    # A quarter's ordinal sits where that rule allows two words: the period readers never take it.
    assert _read("harrison barnes's first quarter per game against the magic", "period_split") == Window()
    assert _read("rudy gobert's first half game log", "period_split") == Window()
    assert _read("fingerprint for curry's first game of 2026", "fingerprint") == Window(order="first", count=1)


def test_an_end_on_a_reader_with_no_window_of_its_own_is_dropped_beside_a_larger_count() -> None:
    """ "lakers vs mavs record last 10 home games" is their last ten meetings
    for a reader that takes a count and refuses an end: the end goes, the
    count stands - as it did when the stages dropped it; beside a count of
    one, or an end said with "games" outright, both stand."""
    assert _read("lakers vs mavs record last 10 home games played", "head_to_head") == Window(count=10)
    assert _read("damian lillard stats vs magic last 10", "player_stat") == Window(count=10)
    assert _read("Warriors vs Mavs record last 10 games", "head_to_head") == Window(order="recent", count=10)
    # ORDER_WORDS read the count as digits alone, so "last ten games" keeps only its count here - as the stages left it.
    assert _read("Warriors vs Mavs record last ten games", "head_to_head") == Window(count=10)
    assert _read("Divencezo season opener stats career", "player_stat") == Window(order="first", count=1)


def test_a_team_rankings_end_and_a_boolean_rankings_measure() -> None:
    assert _read("nba team with least playoff wins since 2022", "team_leaderboard") == Window(rank="fewest")
    assert _read("Best record from 2010-11 to 2018-19 nba", "team_leaderboard") == Window(rank="best")
    assert _read("Detroit Pistons most points in a first half this season", "team_quarter_points") == Window(rank="most")
    assert _read("least points scored by the wizards in the first half this season", "team_quarter_points") == Window(rank="fewest")
    # The table's order decides where two words stand: "worst" before "best", "fewest" before "most".
    assert _read("best and worst records", "team_leaderboard") == Window(rank="worst")
    assert _read("most free throws this season by teams", "leaderboard") == Window()
    assert _read("players with the highest scoring triple doubles", "leaderboard", boolean_stat=True) == Window(by="points")
    assert _read("most rebounds in a triple double", "leaderboard", boolean_stat=True) == Window(by="rebounds")
    assert _read("most triple doubles", "leaderboard", boolean_stat=True) == Window()
    assert _read("players with the highest scoring triple doubles", "leaderboard", boolean_stat=False) == Window()


# ---------------- the claims ----------------


def test_the_tagger_claims_the_characters_it_read_once() -> None:
    question = "jokic stats in his last 5 playoff games"
    read = read_window(question, WindowContext(intent="game_log"))
    assert read.claims == (Claim(19, 39, "window"),)
    assert question[19:39] == "last 5 playoff games"
    ranked = read_window("players with the highest scoring triple doubles", WindowContext(intent="leaderboard", boolean_stat=True))
    assert [c.what for c in ranked.claims] == ["ranked_by"] and ranked.claims[0].start == 17
    history = read_window("sga's 2pt percentage for the past 5 years", WindowContext(intent="player_history"))
    assert history.claims == (Claim(29, 41, "window"),)
    rank = read_window("nba team with least playoff wins", WindowContext(intent="team_leaderboard"))
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
    intent = "player_history" if "past" in question else "game_log"
    window = read_window(question, WindowContext(intent=intent))
    span = read_span(question, SpanContext(intent=intent, window_named=window.window.count is not None, order=window.window.order, limit=window.window.count))
    merged = claimed([*window.claims, *span.claims])
    assert all(a.end <= b.start for a, b in itertools.pairwise(merged))


# ---------------- the slot door and the projection ----------------


@pytest.mark.parametrize(
    "slots",
    [
        {},
        {"order": "recent", "limit": 10},
        {"limit": 5},
        {"order": "first", "limit": 1},
        {"rank": "most"},
        {"ranked_by": "points", "limit": 10},
        {"ranked_by": "points", "rank": "best", "order": "recent", "limit": 3},
    ],
)
def test_the_four_slots_round_trip_through_the_window(slots: dict[str, Any]) -> None:
    scope = Scope.from_slots(slots)
    assert scope.window.to_slots() == slots
    assert scope.to_slots() == slots
    assert Scope.from_slots(scope.to_slots()) == scope


def test_the_projection_keeps_the_slot_era_shape_and_its_order() -> None:
    projected = Scope(player="X", window=Window(order="recent", count=10, rank="most", by="points")).projected()
    keys = list(projected)
    assert (projected["order"], projected["limit"], projected["rank"], projected["ranked_by"]) == ("recent", 10, "most", "points")
    # The four keys sit where the four fields sat: `ranked_by` after `fields`, `rank` after `kind`, `order` and `limit` last.
    assert keys.index("ranked_by") == keys.index("fields") + 1 and keys.index("rank") == keys.index("kind") + 1 and keys[-2:] == ["order", "limit"]
    assert "window" not in projected and all(not isinstance(v, Window) for v in projected.values())
    assert Scope().projected()["limit"] is None and Scope().projected()["order"] is None


def test_a_typed_window_and_a_slot_at_once_or_a_count_below_one_is_refused_at_the_door() -> None:
    with pytest.raises(ScopeError, match="typed window"):
        Scope.from_slots({"window": Window(count=5), "limit": 5})
    with pytest.raises(ScopeError, match="below 1"):
        Scope.from_slots({"limit": 0})
    with pytest.raises(ScopeError, match="below 1"):
        Window(count=0)
    assert Scope.from_slots({"window": Window(count=5)}).window.count == 5
    assert Window.from_slots({"limit": 4}).of == "games"


def test_the_cells_and_what_a_reader_leaves_unhonored() -> None:
    assert Window().cells() == frozenset() and not Window().named
    assert Window(count=5).cells() == frozenset() and Window(count=5).named
    assert Window(order="recent", count=5).cells() == {"window"}
    assert Window(by="points").cells() == {"ranked_by"}
    assert Window(order="recent", count=5, by="points").unhonored(frozenset()) == ["ranked_by", "order"] or Window(order="recent", count=5, by="points").unhonored(frozenset()) == [
        "order",
        "ranked_by",
    ]
    assert Window(order="first", count=1).unhonored(frozenset({"window"})) == []
    assert unhonored_cells(Scope(cuts=Cuts(opponent="BOS"), window=Window(order="recent", count=3)), frozenset({"opponent"})) == ["order"]
    assert unhonored_cells(Scope(window=Window(by="points", count=10)), frozenset({"window"})) == ["ranked_by"]


def test_the_relation_tables_declare_the_window_cell_once() -> None:
    assert Window.CELLS <= RELATION_SCOPING and "window" in TEAM_RELATION_SCOPING
    assert relation_scoping("game_log") >= Window.CELLS and "window" not in relation_scoping("streak")
    assert "window" not in team_relation_scoping("team_record") and "window" in team_relation_scoping("game_log")
    for table in (RELATION_SCOPING_EXCLUDED, TEAM_RELATION_SCOPING_EXCLUDED):
        assert all("order" not in row and "limit" not in row for row in table.values())
    assert {"fingerprint", "game_log", "period_split", "player_netpoints", "shot_chart", "shot_distance", "team_quarter_points"} >= ORDER_INTENTS


# ---------------- the relation's one resolution ----------------


def test_relation_window_reads_an_end_or_a_bare_count_as_the_newest() -> None:
    assert relation_window(Scope()) is None
    assert relation_window(Scope(window=Window(order="recent", count=10))) == ("recent", 10)
    assert relation_window(Scope(window=Window(order="first", count=3))) == ("first", 3)
    # A bare count is the newest N on the games relations; an end alone is one game.
    assert relation_window(Scope(window=Window(count=7))) == ("recent", 7)
    assert relation_window(Scope(window=Window(order="first"))) == ("first", 1)
    assert relation_window(Scope(window=Window(count=400))) == ("recent", 50)
    assert history_seasons(Scope(window=Window(count=6, of="seasons"))) == 6
    assert history_seasons(Scope()) == 4 and history_seasons(Scope(span=Span(career=True))) is None


# ---------------- contract 4: applying the cell changes the result ----------------


def _player_games(ctx: AnswerContext, window: Window) -> tuple[int, int]:
    """The games and the points a total over Podziemski's season reads under
    ``window`` - an aggregate, since the relation cuts the window before the
    sum (``_windowed``); a rows read takes the point's own limit instead."""
    (row,) = rows_of(ctx.con, compile_query(ctx.con, Query(scope=Scope(player="Brandin Podziemski", window=window), skeleton="scalar", measures=["points"], aggregate="total")))
    return int(row["games"]), int(row["points"])


def test_the_window_cell_changes_what_the_player_relation_reads(pg_ctx: AnswerContext) -> None:  # noqa: F811 - the fixture
    assert _player_games(pg_ctx, Window()) == (3, 45)  # e1, e2, e3 this season
    assert _player_games(pg_ctx, Window(order="recent", count=2)) == (2, 35)  # e2, e3
    assert _player_games(pg_ctx, Window(order="first", count=1)) == (1, 10)  # e1
    assert _player_games(pg_ctx, Window(count=2)) == (2, 35)  # a bare count is the newest N


def _team_games(ctx: AnswerContext, window: Window) -> list[str]:
    settled = scoped_team(ctx.con, Scope(team="Celtics"), "no team")
    assert isinstance(settled, tuple)
    team, resolved = settled
    narrowed = team_games(ctx.con, team, resolved, Scope(window=window), opponent=None)
    assert isinstance(narrowed, TeamNarrowed)
    sql, params = rows_sql(narrowed, "tg.event_id", order="tg.eastern_date")
    return [str(row[0]) for row in ctx.con.execute(sql, params).fetchall()]


def test_the_window_cell_changes_what_the_team_relation_reads(team_cells_con: AnswerContext) -> None:  # noqa: F811 - the fixture
    every = _team_games(team_cells_con, Window())
    assert len(every) == 2  # r4, r5 this season
    assert _team_games(team_cells_con, Window(order="recent", count=1)) == every[-1:]
    assert _team_games(team_cells_con, Window(order="first", count=1)) == every[:1]


def test_read_window_returns_the_window_and_its_claims() -> None:
    read = read_window("curry last 3 games", WindowContext(intent="game_log"))
    assert isinstance(read, WindowRead) and read.window == Window(order="recent", count=3) and len(read.claims) == 1
