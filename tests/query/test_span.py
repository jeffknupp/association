"""The span family, typed (Phase 3, step 2): the one tagger that reads the
seasons a question covers and the season type it reads
(:func:`association.query.span.read_span`), the characters it claims, the
typed value's door and projection (:class:`association.query.reading.Span`),
the relation's one resolution of it (:func:`association.query.player_relation.span_of`),
and the behavioral check contract 4 asks for: applying each span cell on
each relation changes the result.
"""

from __future__ import annotations

import itertools
from typing import Any

import pytest
from test_templates import pg_ctx, team_cells_con  # noqa: F401 - the two relation fixtures, imported by name

from association.nba.season import current_season
from association.query.answer import AnswerContext
from association.query.compose.core import Query, compile_query, rows_of
from association.query.player_relation import condition_scope, relation_scoping, relation_span, span_of
from association.query.reading import Claim, Cuts, Scope, ScopeError, Span, unhonored_cells
from association.query.reading import Subject as Who
from association.query.span import SpanContext, claimed, read_span
from association.query.team_games import TEAM_GAMES_SQL, TeamNarrowed
from association.query.team_relation import scoped_team, team_games, team_relation_scoping, team_relation_span

S = current_season()


def _read(question: str, intent: str = "player_stat", **context: Any) -> Span:
    return read_span(question, SpanContext(intent=intent, **context)).span


# ---------------- the tagger: words to a Span ----------------


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("jokic stats", Span(season_type=2)),
        ("jokic stats this season", Span(season=S, season_type=2)),
        ("jokic stats last season", Span(season=S - 1, season_type=2)),
        ("jokic stats in 2019", Span(season=2019, season_type=2)),
        ("jokic stats 2023-24", Span(season=2024, season_type=2)),
        ("jokic playoff stats", Span(season_type=3)),
        ("jokic stats in the 2024 finals", Span(season=2024, season_type=3)),
        ("jokic stats including the playoffs", Span(season_type=2, both=True)),
        ("jokic career averages", Span(season_type=2, career=True)),
        ("jokic all-time triple doubles", Span(season_type=2, career=True)),
        ("luka's avg assists since he joined the league", Span(season_type=2, career=True)),
        ("curry shot chart in all playoff games", Span(season_type=3, career=True)),
        # A career high is one game's best, not a span, where a season stands beside it.
        ("diabate career high assists this season", Span(season=S, season_type=2)),
        ("jokic stats since 2020", Span(season_type=2, since=2020)),
        ("best 3 point shooters of the 2010s", Span(season_type=2, since=2010, until=2019)),
        ("portis vs bulls 2019-20 to 2023-24", Span(season_type=2, since=2020, until=2024)),
        ("kobe playoff stats from 02-03 to 06-07", Span(season_type=3, since=2003, until=2007)),
        ("games between 2020 and 2024", Span(season_type=2, since=2020, until=2024)),
        ("sga 20+ point games 2024-2026", Span(season_type=2, since=2024, until=2026)),
        ("knicks record by month 2024 2025", Span(season_type=2, since=2024, until=2025)),
        ("players with 33 point games since 2000-01", Span(season_type=2, since=2001)),
        ("maxey games vs boston in the past two seasons", Span(season_type=2, since=S - 1)),
        # A range beside a named season: the range wins (the season is dropped).
        ("jokic since 2020 in 2024", Span(season_type=2, since=2020)),
        # A career beside a named season: both kept, for the relation to refuse.
        ("jokic career averages in 2023", Span(season=2023, season_type=2, career=True)),
    ],
)
def test_the_words_read_as_one_span(question: str, expected: Span) -> None:
    assert _read(question) == expected


def test_a_history_reads_the_past_n_seasons_as_its_count_not_a_range() -> None:
    assert _read("sga's 2pt percentage for the past 5 years", intent="player_history") == Span(season_type=2)
    assert _read("sga's 2pt percentage for the past 5 years", intent="game_log") == Span(season_type=2, since=S - 4)


def test_a_range_opened_on_a_dated_day_starts_the_seasons_there() -> None:
    assert _read("towns home rec since 1/26/20 vs spurs", dated_since=2020) == Span(season_type=2, since=2020)
    # A career the words name stands over it.
    assert _read("towns career rec since 1/26/20", dated_since=2020) == Span(season_type=2, career=True)


def test_a_count_a_pair_and_a_log_imply_a_career_without_a_career_word() -> None:
    assert _read("how many times has embiid fouled out?", intent="threshold_count", player_named=True, how_many=True).career
    assert not _read("how many times has embiid fouled out this season?", intent="threshold_count", player_named=True, how_many=True).career
    assert not _read("how many times has embiid fouled out in his 5th season?", intent="threshold_count", player_named=True, how_many=True, season_n=5).career
    assert _read("steph curry record vs lebron", intent="player_matchup", record=True, versus=True).career
    assert _read("jaylen brown last 8 games vs pistons", intent="game_log", versus=True, window_named=True).career
    assert _read("prichard stats vs 76ers including playoffs game log", intent="game_log", versus=True).career
    assert not _read("jaylen brown last 8 games vs pistons this season", intent="game_log", versus=True, window_named=True).career


def test_a_bare_last_n_games_log_reads_both_types() -> None:
    assert _read("knicks last 5 games", intent="game_log", order="recent", limit=5).both
    assert not _read("knicks last 5 regular season games", intent="game_log", order="recent", limit=5).both
    assert not _read("knicks last 5 games", intent="game_log", order="first", limit=5).both


# ---------------- the claims ----------------


def test_the_tagger_claims_the_characters_it_read_once() -> None:
    question = "Payton Pritchard stats vs 76ers at home including playoffs game log"
    read = read_span(question, SpanContext(intent="game_log", versus=True))
    assert read.claims == (Claim(40, 58, "both"),) and question[40:58] == "including playoffs"  # the "playoffs" inside it folds in
    question = "kobe playoff stats from 02-03 to 06-07"
    read = read_span(question, SpanContext(intent="player_stat"))
    assert [(c.what, question[c.start : c.end]) for c in read.claims] == [("season_type", "playoff"), ("range", "02-03 to 06-07")]
    read = read_span("jokic career averages in 2023", SpanContext(intent="player_stat"))
    assert [c.what for c in read.claims] == ["career", "season"]
    assert ["jokic career averages in 2023"[c.start : c.end] for c in read.claims] == ["career", "2023"]


@pytest.mark.parametrize(
    "question",
    [
        "jokic stats since 2000-01",
        "nba mvps in 1980's",
        "warriors all-time record including playoff record at away",
        "lebron james 4th qtr stats 2026 regular season and playoffs",
        "how many 20+ point games did SGA have in the past two seasons?",
        "this season, how many times did the 76ers play against the celtics?",
    ],
)
def test_no_two_claims_overlap(question: str) -> None:
    claims = read_span(question, SpanContext(intent="player_stat")).claims
    assert all(a.end <= b.start for a, b in itertools.pairwise(claims))


def test_claimed_folds_a_nested_claim_and_joins_a_partial_overlap() -> None:
    """A claim inside another is the same reading; two that share a word are
    one claim named for both readings - "his last game 7" is the window's
    "last game" and the postseason's "game 7", and until 2026-10-09 it raised
    out of the reader ("lebron's last game 7" was a traceback, not an answer)."""
    assert claimed([Claim(10, 30, "both"), Claim(20, 28, "season_type")]) == (Claim(10, 30, "both"),)
    assert claimed([Claim(10, 20, "range"), Claim(15, 25, "season")]) == (Claim(10, 25, "range+season"),)
    assert claimed([Claim(15, 25, "season"), Claim(10, 20, "range"), Claim(22, 30, "career")]) == (Claim(10, 30, "range+season+career"),)


def test_a_word_two_readings_share_reads_both_and_raises_nothing() -> None:
    """ "lebron's last game 7": one game at the end of his span (the window)
    and the seventh game of a series (the postseason's word and the series
    game), both read, the shared "game" claimed once for both."""
    from routed import staged as settle

    route = settle("game_log", {"player": "LeBron James"}, "lebron's last game 7")
    assert route.scope.window.order == "recent" and route.scope.window.count == 1 and route.scope.span.season_type == 3 and route.scope.cuts.game_n == 7
    assert [c.what for c in route.claims] == ["window+game_n"]


# ---------------- the typed value's door and projection ----------------


@pytest.mark.parametrize(
    "slots",
    [
        {},
        {"season": 2024},
        {"season_type": 3},
        {"season": 2024, "season_type": 3},
        {"season_type": 2, "season_type_unstated": True},
        {"span": "career"},
        {"span": "career", "season": 2001},
        {"since": 2020},
        {"since": 2020, "until": 2024},
        {"span": "career", "since": 2020, "season_type_unstated": True},
    ],
)
def test_the_six_slots_round_trip_through_the_span(slots: dict[str, Any]) -> None:
    scope = Scope.from_slots(slots)
    assert scope.span.to_slots() == slots
    assert scope.to_slots() == slots
    assert Scope.from_slots(scope.to_slots()) == scope


def test_the_projection_keeps_the_slot_era_shape() -> None:
    projected = Scope(subject=Who(kind="player", players=("X",)), span=Span(since=2020, until=2024, season_type=2)).projected()
    assert {"season", "season_type", "season_type_unstated", "span", "since", "until"} <= set(projected)
    assert (projected["season"], projected["season_type"], projected["season_type_unstated"], projected["span"], projected["since"], projected["until"]) == (None, 2, False, None, 2020, 2024)
    assert "player" in projected and all(not isinstance(v, Span) for v in projected.values())


def test_a_typed_span_and_a_slot_at_once_is_refused_at_the_door() -> None:
    with pytest.raises(ScopeError, match="typed span"):
        Scope.from_slots({"span": Span(career=True), "season": 2024})
    assert Scope.from_slots({"span": Span(career=True)}).span.career


def test_the_cells_and_what_a_reader_leaves_unhonored() -> None:
    assert Span().cells() == frozenset()
    assert Span(since=2020, until=2024, both=True, career=True).cells() == {"range", "both", "career"}
    assert Span(since=2020).unhonored(frozenset()) == ["since"]
    assert Span(since=2020, until=2024).unhonored(frozenset({"career"})) == ["since", "until"]
    assert Span(both=True, career=True).unhonored(frozenset({"both"})) == ["span"]
    assert unhonored_cells(Scope(cuts=Cuts(opponent="BOS"), span=Span(since=2020, both=True)), frozenset({"opponent", "range"})) == ["season_type_unstated"]
    assert Span(season=2024, season_type=3, both=True).stated == {"season", "postseason", "both"}
    assert Span(since=2020).over_career() == Span(career=True, since=2020)
    assert Span(season=2024).as_career() == Span(season=2024, career=True)
    assert Span(career=True, since=2020, until=2024).without_range() == Span(career=True)


def test_the_relation_tables_declare_the_three_cells_once() -> None:
    assert relation_scoping("game_log") >= Span.CELLS and team_relation_scoping("team_record") >= Span.CELLS
    assert relation_span("leaderboard") == {"career"} and relation_span("player_compare") == frozenset()
    assert team_relation_span("team_leaderboard") == {"range"} and team_relation_span("with_without") == {"career"}


# ---------------- the relation's one resolution ----------------


def test_span_of_resolves_each_cell_and_refuses_a_contradiction() -> None:
    assert span_of(Span(), "player_game_log").season == S
    assert span_of(Span(), "player_game_log").defaulted and not span_of(Span(season=2024), "player_game_log").defaulted
    career = span_of(Span(career=True), "player_game_log")
    assert career.season is None and career.first == 1994 and career.phantom == (1993,)
    ranged = span_of(Span(since=2020, until=2024), "player_game_log")
    assert (ranged.since, ranged.until, ranged.first) == (2020, 2024, 2020)
    assert span_of(Span(both=True), "player_game_log", season_type=0).season_type == 0
    for contradiction, reason in (
        (Span(career=True, season=2001), "career span and the 2001"),
        (Span(since=2020, season=2024), "since 2020 and the 2024"),
        (Span(until=2024), "until 2024 with no since"),
    ):
        with pytest.raises(Exception, match=reason):
            span_of(contradiction, "player_game_log")
    assert condition_scope(Span(since=2020), ("player_game_log",)).first == 2020
    assert condition_scope(Span(career=True), ("player_game_log",)).season is None and condition_scope(Span(), ("player_game_log",)).season == S


# ---------------- contract 4: applying each cell changes the result ----------------


def _player_games(ctx: AnswerContext, span: Span) -> int:
    rows = rows_of(ctx.con, compile_query(ctx.con, Query(scope=Scope(subject=Who(kind="player", players=("Brandin Podziemski",)), span=span), skeleton="rows", measures=["points"], limit=50)))
    return len(rows)


@pytest.mark.parametrize(
    ("cell", "span", "games"),
    [
        ("season", Span(), 3),  # e1, e2, e3 this season (e4 a DNP, e6 an empty line)
        ("career", Span(career=True), 4),  # and e5 last season
        ("range", Span(since=S - 1, until=S - 1), 1),  # e5 alone
        ("both", Span(both=True), 8),  # this season's three and the five playoff games
    ],
)
def test_each_span_cell_changes_what_the_player_relation_reads(pg_ctx: AnswerContext, cell: str, span: Span, games: int) -> None:  # noqa: F811 - the fixture
    assert _player_games(pg_ctx, span) == games, cell


def _team_games(ctx: AnswerContext, span: Span) -> int:
    settled = scoped_team(ctx.con, Scope(subject=Who(kind="team", teams=("Celtics",)), span=span), "no team")
    assert isinstance(settled, tuple)
    team, resolved = settled
    narrowed = team_games(ctx.con, team, resolved, Scope(), opponent=None)
    assert isinstance(narrowed, TeamNarrowed)
    where, params = narrowed.clauses(narrowed=False)
    row = ctx.con.execute(f"{TEAM_GAMES_SQL} SELECT COUNT(*) FROM team_games tg WHERE {where}", params).fetchone()
    return int(row[0]) if row else 0


@pytest.mark.parametrize(
    ("cell", "span", "games"),
    [
        ("season", Span(), 2),  # r4, r5
        ("career", Span(career=True), 5),  # r1 through r5
        ("range", Span(since=S - 2, until=S - 1), 3),  # r1, r2, r3
        ("postseason", Span(season_type=3), 3),  # this season's series
    ],
)
def test_each_span_cell_changes_what_the_team_relation_reads(team_cells_con: AnswerContext, cell: str, span: Span, games: int) -> None:  # noqa: F811 - the fixture
    assert _team_games(team_cells_con, span) == games, cell
