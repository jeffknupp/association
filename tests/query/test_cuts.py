"""The games' cuts, typed (Phase 3, step 2's third slice): the one tagger
that reads which games of a span a read sees
(:func:`association.query.cuts.read_cuts`), the characters it claims, the
typed value's door and projection (:class:`association.query.reading.Cuts`,
:class:`~association.query.reading.Situation`), the relations' one
resolution of each cut, and the behavioral check contract 4 asks for:
applying each cut on each relation changes the result.
"""

from __future__ import annotations

from typing import Any

import pytest
from test_templates import pg_ctx, team_cells_con  # noqa: F401 - the two relation fixtures, imported by name

from association.nba.season import current_season
from association.query.answer import AnswerContext
from association.query.calendar import AlignmentNarrowing, CalendarNarrowing
from association.query.compose.core import Query, compile_query, rows_of
from association.query.compose.plan import STATED_SCOPING
from association.query.cuts import CutsContext, CutsRead, read_cuts
from association.query.player_relation import RELATION_SCOPING, RELATION_SCOPING_EXCLUDED, relation_cuts, relation_scoping
from association.query.reading import Claim, Cuts, PointShape, Scope, ScopeError, Situation, Span, cell_set, situation_of, unhonored_cells
from association.query.router import settle
from association.query.team_games import TeamNarrowed, rows_sql
from association.query.team_relation import TEAM_CUTS, TEAM_RELATION_SCOPING, TEAM_RELATION_SCOPING_EXCLUDED, scoped_team, team_games, team_relation_cuts

S = current_season()


def _read(question: str, intent: str = "game_log", **context: Any) -> Cuts:
    return read_cuts(question, CutsContext(intent=intent, **context)).cuts


def _situation(text: str) -> Situation:
    return situation_of(text)


# ---------------- the tagger: words to Cuts ----------------


@pytest.mark.parametrize(
    ("question", "intent", "expected"),
    [
        ("jokic stats", "game_log", Cuts()),
        ("Knicks home record this season", "team_record", Cuts(venue="home")),
        ("lebron road games", "game_log", Cuts(venue="away")),
        ("lebron home and away splits", "player_splits", Cuts()),
        ("How far away does Wembanyama shoot from?", "shot_distance", Cuts()),
        # The one side beside a venue split on the splits reader is the split's half, not a cut.
        ("tatum home splits", "player_splits", Cuts()),
        ("Desmond bane march 17", "game_log", Cuts(date=f"{S}-03-17")),
        ("bam adebayo november 11 2019", "game_log", Cuts(date="2019-11-11")),
        ("jokic stats on october 25 in 2024", "player_stat", Cuts(date="2023-10-25")),
        # A date on a career question fixes no year: a situation, refused by value.
        ("lebron on march 17 all time", "game_log", Cuts(situation=_situation("march 17"))),
        # A day no calendar has names nothing, and is a situation.
        ("lebron on february 31", "game_log", Cuts(situation=_situation("february 31"))),
        ("lebron stats since january 31st", "player_stat", Cuts(situation=_situation("since january 31st"))),
        ("towns home rec including playoffs since 1/26/20 vs spurs", "player_stat", Cuts(venue="home", situation=_situation("since 1/26/20"))),
        ("lebron stats on christmas", "player_stat", Cuts(situation=_situation("christmas"))),
        ("lebron ppg vs the west", "player_stat", Cuts(situation=_situation("vs the west"))),
        ("giannis stats in the month of march", "player_stat", Cuts(situation=_situation("in the month of march"))),
        ("lebron ppg as an 18 year old", "player_stat", Cuts(situation=_situation("18 year old"))),
        ("tatum stats in the 2024 finals", "player_stat", Cuts(round="finals")),
        ("knicks second round record", "team_record", Cuts(round="second round")),
        ("Ayton stats in game 4 playoff games", "game_log", Cuts(game_n=4)),
        ("his 18th season", "player_stat", Cuts(season_n=18)),
        ("most points in 15th season played", "leaderboard", Cuts(season_n=15)),
        # A day and its own month, both (ISSUES.md, "A month is read from the words of a day in it").
        ("nba Anthony Davis most offensive rebound in march 24 2018", "single_game_high", Cuts(date="2018-03-24", situation=_situation("in march"))),
    ],
)
def test_the_words_read_as_one_cuts(question: str, intent: str, expected: Cuts) -> None:
    split = "home_away" if "splits" in question else None
    assert _read(question, intent, split=split) == expected


def test_a_venue_beside_a_venue_split_is_the_splits_half_on_the_splits_reader_only() -> None:
    assert _read("tatum home splits", "player_splits", split="home_away") == Cuts()
    assert _read("tatum home splits", "game_log", split="home_away") == Cuts(venue="home")


def test_the_opponent_is_the_subjects_word_unless_it_is_the_absent_teammates_again() -> None:
    assert _read("bane game log vs the pistons", opponent="Detroit Pistons").opponent == "Detroit Pistons"
    assert _read("bane game log without anthony black and franz wagner", opponent="Anthony Black, Franz Wagner", without=("anthony black", "franz wagner")).opponent is None
    assert _read("bane game log vs orlando without franz wagner", opponent="Orlando Magic", without=("franz wagner",)).opponent == "Orlando Magic"
    assert _read("bane game log", opponent=" ").opponent is None


def test_a_dated_range_hands_its_year_to_the_span_and_a_worded_one_without_a_year_hands_none() -> None:
    read = read_cuts("towns home rec since 1/26/20 vs spurs", CutsContext(intent="player_stat"))
    assert isinstance(read, CutsRead) and read.dated_since == 2020 and read.cuts.situation is not None and read.cuts.situation.calendar is not None
    assert read.cuts.situation.calendar.kind == "since_date" and read.cuts.situation.calendar.value == "2020-01-26"
    assert read_cuts("lebron stats since january 31st", CutsContext(intent="player_stat")).dated_since is None
    assert read_cuts("lebron stats since 12/13", CutsContext(intent="player_stat")).dated_since is None
    # Two digits the way strptime reads them: 69-99 are the 1900s.
    assert read_cuts("jordan stats since 3/1/96", CutsContext(intent="player_stat")).dated_since == 1996


def test_a_situation_is_parsed_once_into_what_the_relation_applies() -> None:
    weekday = situation_of("on tuesdays")
    assert weekday.read and weekday.calendar == CalendarNarrowing("weekday", 2, "on Tuesdays") and weekday.alignment is None
    west = situation_of("vs the west")
    assert west.read and west.calendar is None and west.alignment == AlignmentNarrowing("conference", "Western Conference", "against Western Conference teams")
    age = situation_of("18 year old")
    assert not age.read and age.text == "18 year old" and age.calendar is None and age.alignment is None


# ---------------- the claims ----------------


def test_the_tagger_claims_the_characters_it_read_once() -> None:
    question = "tatum home stats in game 4 of the 2024 finals in his 7th season on christmas"
    read = read_cuts(question, CutsContext(intent="player_stat"))
    assert read.cuts == Cuts(venue="home", situation=_situation("christmas"), round="finals", game_n=4, season_n=7)
    assert [(question[c.start : c.end], c.what) for c in read.claims] == [("home", "venue"), ("game 4", "game_n"), ("finals", "round"), ("7th season", "season_n"), ("christmas", "situation")]


def test_a_day_and_its_own_month_are_one_claim_named_for_both() -> None:
    question = "nba Anthony Davis most offensive rebound in march 24 2018"
    read = read_cuts(question, CutsContext(intent="single_game_high"))
    assert [(question[c.start : c.end], c.what) for c in read.claims] == [("in march 24 2018", "situation+date")]


def test_the_cuts_claims_ride_the_route_with_the_windows_and_the_spans() -> None:
    route = settle("game_log", {"player": "Jayson Tatum"}, "tatum last 5 home games vs the celtics in the 2024 playoffs")
    assert route.scope.cuts == Cuts(venue="home") and route.scope.window.count == 5 and route.scope.span.season == 2024
    # The venue's "home" sits inside the window's "last 5 home games", one reading (the nested fold).
    assert sorted(c.what for c in route.claims) == ["season", "season_type", "window"]


# ---------------- the typed value's door and projection ----------------


@pytest.mark.parametrize(
    "slots",
    [
        {},
        {"opponent": "Boston Celtics"},
        {"own_team": "Miami Heat"},
        {"venue": "home"},
        {"date": "2026-03-17"},
        {"situation": "on tuesdays"},
        {"situation": "18 year old"},
        {"round": "finals"},
        {"game_n": 4},
        {"season_n": 18},
        {"opponent": "Boston Celtics", "own_team": "Los Angeles Lakers", "date": "2026-03-17", "situation": "in march", "game_n": 7, "season_n": 3, "round": "finals", "venue": "away"},
    ],
)
def test_the_eight_slots_round_trip_through_the_cuts(slots: dict[str, Any]) -> None:
    scope = Scope.from_slots(slots)
    assert scope.to_slots() == slots and Cuts.from_slots(slots).to_slots() == slots
    assert Scope.from_slots({"cuts": scope.cuts}) == scope


def test_the_projection_keeps_the_slot_era_shape_and_its_order() -> None:
    scope = Scope.from_slots(
        {
            "player": "LeBron James",
            "team": "LAL",
            "opponent": "BOS",
            "own_team": "MIA",
            "stat": "points",
            "date": "2026-01-19",
            "situation": "in january",
            "split": "month",
            "game_n": 2,
            "season_n": 5,
            "round": "finals",
            "period": 1,
            "venue": "home",
            "limit": 3,
        }
    )
    keys = list(scope.to_slots())
    assert keys == ["player", "team", "opponent", "own_team", "stat", "season_type", "date", "situation", "split", "game_n", "season_n", "round", "period", "venue", "limit"] or keys == [
        "player",
        "team",
        "opponent",
        "own_team",
        "stat",
        "date",
        "situation",
        "split",
        "game_n",
        "season_n",
        "round",
        "period",
        "venue",
        "limit",
    ]
    projected = scope.projected()
    assert projected["opponent"] == "BOS" and projected["own_team"] == "MIA" and projected["situation"] == "in january" and projected["venue"] == "home" and projected["round"] == "finals"
    assert "cuts" not in projected and "tenure" not in projected
    empty = Scope().projected()
    assert all(empty[slot] is None for slot in ("opponent", "own_team", "venue", "date", "situation", "round", "game_n", "season_n"))
    assert list(empty).index("opponent") == list(empty).index("teams") + 1 and list(empty).index("venue") == list(empty).index("period_condition") + 1


def test_a_typed_cuts_and_a_slot_at_once_or_a_bad_value_is_refused_at_the_door() -> None:
    with pytest.raises(ScopeError, match="typed cuts"):
        Scope.from_slots({"cuts": Cuts(venue="home"), "opponent": "BOS"})
    with pytest.raises(ScopeError, match="calendar day"):
        Scope.from_slots({"date": "last night"})
    with pytest.raises(ScopeError, match="venue"):
        Scope.from_slots({"venue": "neutral"})
    # A blank opponent is the slot absent, as every reader took it.
    assert Scope.from_slots({"opponent": " "}).cuts == Cuts()


# ---------------- the cells ----------------


def test_the_cells_and_what_a_reader_leaves_unhonored() -> None:
    assert Cuts().cells() == frozenset() and not Cuts().named
    cuts = Cuts(opponent="BOS", tenure="MIA", venue="home", date="2026-01-19", situation=_situation("in march"), round="finals", game_n=4, season_n=2)
    assert cuts.cells() == Cuts.CELLS and cuts.named
    assert cuts.unhonored(frozenset()) == ["opponent", "own_team", "date", "situation", "game_n", "season_n", "round", "venue"]
    assert cuts.unhonored(Cuts.CELLS - {"round", "tenure"}) == ["own_team", "round"]
    assert unhonored_cells(Scope(cuts=Cuts(round="finals", opponent="BOS")), relation_scoping("game_log")) == ["round"]
    assert unhonored_cells(Scope(cuts=Cuts(tenure="MIA")), relation_scoping("game_log")) == [] and unhonored_cells(Scope(cuts=Cuts(tenure="MIA")), TEAM_RELATION_SCOPING) == ["own_team"]
    assert cell_set(Scope(cuts=Cuts(tenure="MIA")), "tenure") and not cell_set(Scope(), "tenure") and cell_set(Scope(without=("X",)), "without")


def test_the_relation_tables_declare_the_cuts_once() -> None:
    assert Cuts.CELLS - {"round"} <= RELATION_SCOPING and "round" not in RELATION_SCOPING
    assert Cuts.CELLS - {"round", "tenure", "season_n"} == TEAM_CUTS and TEAM_CUTS <= TEAM_RELATION_SCOPING and not ({"round", "tenure", "season_n"} & TEAM_RELATION_SCOPING)
    for table in (RELATION_SCOPING_EXCLUDED, TEAM_RELATION_SCOPING_EXCLUDED):
        assert all("own_team" not in row and "round" not in row for row in table.values())
    # A reader whose words state fewer cuts than the relation's takes them through relation_cuts, with a reason per row.
    assert relation_cuts("leaderboard") == frozenset() and relation_cuts("player_history") == frozenset() and relation_cuts("player_compare") == frozenset()
    assert relation_cuts("threshold_count") == {"season_n"} and relation_cuts("fingerprint") == {"date"} and relation_cuts("period_leaderboard") == {"opponent", "venue"}
    assert relation_cuts("player_netpoints") == frozenset() and relation_cuts("single_game_high") == frozenset() and relation_cuts("game_log") == Cuts.CELLS - {"round"}
    assert team_relation_cuts("with_without") == {"opponent"} and team_relation_cuts("team_stat") == frozenset() and team_relation_cuts("team_record") == TEAM_CUTS - {"date"}
    assert all(RELATION_SCOPING_EXCLUDED["leaderboard"][cut] for cut in Cuts.CELLS - {"round"})
    # No row of STATED_SCOPING names a cut itself: every cut a shape states is a relation table's, less that reader's exclusions.
    for shape, stated in STATED_SCOPING.items():
        assert stated & Cuts.CELLS <= Cuts.CELLS - {"round"}, shape
    assert STATED_SCOPING[PointShape("netpoints", "chart", "fingerprint")] & Cuts.CELLS == {"date"}
    assert STATED_SCOPING[PointShape("team_games", "split", "presence")] & Cuts.CELLS == {"opponent"}


# ---------------- contract 4: applying each cut changes the result ----------------


def _player_games(ctx: AnswerContext, cuts: Cuts, span: Span | None = None) -> tuple[int, int]:
    """The games and the points a total over Podziemski's season reads under ``cuts``."""
    (row,) = rows_of(ctx.con, compile_query(ctx.con, Query(scope=Scope(player="Brandin Podziemski", cuts=cuts, span=span or Span()), skeleton="scalar", measures=["points"], aggregate="total")))
    return int(row["games"] or 0), int(row["points"] or 0)


@pytest.mark.parametrize(
    ("cell", "cuts", "games"),
    [
        ("opponent", Cuts(opponent="Pistons"), (2, 35)),  # e2, e3
        ("tenure", Cuts(tenure="Warriors"), (3, 45)),  # every game was a Warrior's
        ("tenure", Cuts(tenure="Celtics"), (0, 0)),  # never a Celtic
        ("venue", Cuts(venue="home"), (2, 25)),  # e1, e3
        ("venue", Cuts(venue="away"), (1, 20)),  # e2
        ("date", Cuts(date=f"{S - 1}-12-01"), (1, 20)),  # e2, by its Eastern day
        ("situation", Cuts(situation=situation_of("in january")), (1, 15)),  # e3
        ("situation", Cuts(situation=situation_of("in december")), (1, 20)),  # e2
    ],
)
def test_each_cut_changes_what_the_player_relation_reads(pg_ctx: AnswerContext, cell: str, cuts: Cuts, games: tuple[int, int]) -> None:  # noqa: F811 - the fixture
    assert _player_games(pg_ctx, Cuts()) == (3, 45)  # e1, e2, e3 this season
    assert cuts.cells() == {cell}
    assert _player_games(pg_ctx, cuts) == games


def test_a_series_game_and_an_ordinal_season_change_what_the_player_relation_reads(pg_ctx: AnswerContext) -> None:  # noqa: F811 - the fixture
    assert _player_games(pg_ctx, Cuts(), Span(season_type=3)) == (5, 80)  # p1-p5
    assert _player_games(pg_ctx, Cuts(game_n=2), Span(season_type=3)) == (2, 35)  # p2, p5: game 2 of each series
    assert _player_games(pg_ctx, Cuts(game_n=3), Span(season_type=3)) == (1, 30)  # p3
    # His second season is this one; his first the one before (e5 alone, 8 points).
    assert _player_games(pg_ctx, Cuts(season_n=2)) == (3, 45)
    assert _player_games(pg_ctx, Cuts(season_n=1)) == (1, 8)


def _team_games(ctx: AnswerContext, cuts: Cuts, span: Span | None = None) -> list[str]:
    settled = scoped_team(ctx.con, Scope(team="Celtics", span=span or Span()), "no team")
    assert isinstance(settled, tuple)
    team, resolved = settled
    narrowed = team_games(ctx.con, team, resolved, Scope(cuts=cuts), opponent=cuts.opponent, date=cuts.date)
    assert isinstance(narrowed, TeamNarrowed)
    sql, params = rows_sql(narrowed, "tg.event_id", order="tg.eastern_date")
    return [str(row[0]) for row in ctx.con.execute(sql, params).fetchall()]


@pytest.mark.parametrize(
    ("cell", "cuts", "games"),
    [
        ("opponent", Cuts(opponent="Knicks"), ["r4"]),
        ("venue", Cuts(venue="home"), ["r4", "r5"]),
        ("venue", Cuts(venue="away"), []),
        ("date", Cuts(date=f"{S - 1}-11-15"), ["r4"]),
        ("situation", Cuts(situation=situation_of("in november")), ["r4", "r5"]),
        ("situation", Cuts(situation=situation_of("in december")), []),
    ],
)
def test_each_cut_changes_what_the_team_relation_reads(team_cells_con: AnswerContext, cell: str, cuts: Cuts, games: list[str]) -> None:  # noqa: F811 - the fixture
    assert _team_games(team_cells_con, Cuts()) == ["r4", "r5"]
    assert cuts.cells() == {cell}
    assert _team_games(team_cells_con, cuts) == games


def test_a_series_game_changes_what_the_team_relation_reads(team_cells_con: AnswerContext) -> None:  # noqa: F811 - the fixture
    assert _team_games(team_cells_con, Cuts(), Span(season_type=3)) == ["pS_1", "pS_2", "pS_3"]
    assert _team_games(team_cells_con, Cuts(game_n=2), Span(season_type=3)) == ["pS_2"]


def test_a_situation_nothing_reads_is_refused_by_value_with_its_words(pg_ctx: AnswerContext) -> None:  # noqa: F811 - the fixture
    from association.query.reading import Unsupported

    with pytest.raises(Unsupported, match="no narrowing in situation 'overtime'"):
        _player_games(pg_ctx, Cuts(situation=situation_of("overtime")))


def test_read_cuts_returns_the_cuts_and_its_claims() -> None:
    read = read_cuts("curry home games vs the lakers", CutsContext(intent="game_log", opponent="Los Angeles Lakers"))
    assert isinstance(read, CutsRead) and read.cuts == Cuts(opponent="Los Angeles Lakers", venue="home") and read.claims == (Claim(6, 10, "venue"),) and read.dated_since is None
