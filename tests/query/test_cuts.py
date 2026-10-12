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
from routed import staged as settle
from test_templates import pg_ctx, team_cells_con  # noqa: F401 - the two relation fixtures, imported by name

from association.nba.season import current_season
from association.query.answer import AnswerContext
from association.query.calendar import AlignmentNarrowing, CalendarNarrowing
from association.query.compose.core import Query, compile_query, rows_of
from association.query.compose.plan import cells_stated
from association.query.cuts import CutsContext, CutsRead, read_cuts
from association.query.player_relation import RELATION_SCOPING, RELATION_SCOPING_EXCLUDED
from association.query.point import cuts_under
from association.query.reading import (
    GAME_HIGHS,
    GAMES_COUNTED,
    NETPOINTS_FINGERPRINT,
    NETPOINTS_RATINGS,
    PERIOD_RANKING,
    PLAYER_COMPARISON,
    PLAYER_LINE,
    PLAYER_LOG,
    PLAYER_RANKING,
    PLAYER_SPLITS,
    PRESENCE_SPLIT,
    SEASON_HISTORY,
    SHOT_DISTANCE,
    TEAM_LINE,
    TEAM_RECORD,
    Aggregate,
    Claim,
    Companion,
    Cuts,
    PointShape,
    Reading,
    Situation,
    Span,
    cell_set,
    situation_of,
    unhonored_cells,
)
from association.query.reading import Subject as Who
from association.query.team_games import TeamNarrowed, rows_sql
from association.query.team_relation import TEAM_CUTS, TEAM_RELATION_SCOPING, TEAM_RELATION_SCOPING_EXCLUDED, scoped_team, team_games

S = current_season()


def _read(question: str, shape: PointShape | None = PLAYER_LOG, **context: Any) -> Cuts:
    split = context.pop("split", None)
    return cuts_under(shape, read_cuts(question, CutsContext(**context)), split=split).cuts


def _situation(text: str) -> Situation:
    return situation_of(text)


# ---------------- the tagger: words to Cuts ----------------


@pytest.mark.parametrize(
    ("question", "shape", "expected"),
    [
        ("jokic stats", PLAYER_LOG, Cuts()),
        ("Knicks home record this season", TEAM_RECORD, Cuts(venue="home")),
        ("lebron road games", PLAYER_LOG, Cuts(venue="away")),
        ("lebron home and away splits", PLAYER_SPLITS, Cuts()),
        ("How far away does Wembanyama shoot from?", SHOT_DISTANCE, Cuts()),
        # The one side beside a venue split on the splits reader is the split's half, not a cut.
        ("tatum home splits", PLAYER_SPLITS, Cuts()),
        ("Desmond bane march 17", PLAYER_LOG, Cuts(date=f"{S}-03-17")),
        ("bam adebayo november 11 2019", PLAYER_LOG, Cuts(date="2019-11-11")),
        ("jokic stats on october 25 in 2024", PLAYER_LINE, Cuts(date="2023-10-25")),
        # A date on a career question fixes no year: a situation, refused by value.
        ("lebron on march 17 all time", PLAYER_LOG, Cuts(situation=_situation("march 17"))),
        # A day no calendar has names nothing, and is a situation.
        ("lebron on february 31", PLAYER_LOG, Cuts(situation=_situation("february 31"))),
        ("lebron stats since january 31st", PLAYER_LINE, Cuts(situation=_situation("since january 31st"))),
        ("towns home rec including playoffs since 1/26/20 vs spurs", PLAYER_LINE, Cuts(venue="home", situation=_situation("since 1/26/20"))),
        ("lebron stats on christmas", PLAYER_LINE, Cuts(situation=_situation("christmas"))),
        ("lebron ppg vs the west", PLAYER_LINE, Cuts(situation=_situation("vs the west"))),
        ("giannis stats in the month of march", PLAYER_LINE, Cuts(situation=_situation("in the month of march"))),
        ("lebron ppg as an 18 year old", PLAYER_LINE, Cuts(situation=_situation("18 year old"))),
        ("tatum stats in the 2024 finals", PLAYER_LINE, Cuts(round="finals")),
        ("knicks second round record", TEAM_RECORD, Cuts(round="second round")),
        ("Ayton stats in game 4 playoff games", PLAYER_LOG, Cuts(game_n=4)),
        ("his 18th season", PLAYER_LINE, Cuts(season_n=18)),
        ("most points in 15th season played", PLAYER_RANKING, Cuts(season_n=15)),
        # A day and its own month, both (ISSUES.md, "A month is read from the words of a day in it").
        ("nba Anthony Davis most offensive rebound in march 24 2018", GAME_HIGHS, Cuts(date="2018-03-24", situation=_situation("in march"))),
    ],
)
def test_the_words_read_as_one_cuts(question: str, shape: PointShape | None, expected: Cuts) -> None:
    split = "home_away" if "splits" in question else None
    assert _read(question, shape, split=split) == expected


def test_a_venue_beside_a_venue_split_is_the_splits_half_on_the_splits_reader_only() -> None:
    assert _read("tatum home splits", PLAYER_SPLITS, split="home_away") == Cuts()
    assert _read("tatum home splits", PLAYER_LOG, split="home_away") == Cuts(venue="home")


def test_the_opponent_is_the_subjects_word_unless_it_is_the_absent_teammates_again() -> None:
    assert _read("bane game log vs the pistons", opponent="Detroit Pistons").opponent == "Detroit Pistons"
    assert _read("bane game log without anthony black and franz wagner", opponent="Anthony Black, Franz Wagner", without=("anthony black", "franz wagner")).opponent is None
    assert _read("bane game log vs orlando without franz wagner", opponent="Orlando Magic", without=("franz wagner",)).opponent == "Orlando Magic"
    assert _read("bane game log", opponent=" ").opponent is None


def test_a_dated_range_hands_its_year_to_the_span_and_a_worded_one_without_a_year_hands_none() -> None:
    read = cuts_under(PLAYER_LINE, read_cuts("towns home rec since 1/26/20 vs spurs", CutsContext()), split=None)
    assert isinstance(read, CutsRead) and read.dated_since == 2020 and read.cuts.situation is not None and read.cuts.situation.calendar is not None
    assert read.cuts.situation.calendar.kind == "since_date" and read.cuts.situation.calendar.value == "2020-01-26"
    assert cuts_under(PLAYER_LINE, read_cuts("lebron stats since january 31st", CutsContext()), split=None).dated_since is None
    assert cuts_under(PLAYER_LINE, read_cuts("lebron stats since 12/13", CutsContext()), split=None).dated_since is None
    # Two digits the way strptime reads them: 69-99 are the 1900s.
    assert cuts_under(PLAYER_LINE, read_cuts("jordan stats since 3/1/96", CutsContext()), split=None).dated_since == 1996


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
    read = cuts_under(PLAYER_LINE, read_cuts(question, CutsContext()), split=None)
    assert read.cuts == Cuts(venue="home", situation=_situation("christmas"), round="finals", game_n=4, season_n=7)
    assert [(question[c.start : c.end], c.what) for c in read.claims] == [("home", "venue"), ("game 4", "game_n"), ("finals", "round"), ("7th season", "season_n"), ("christmas", "situation")]


def test_a_day_and_its_own_month_are_one_claim_named_for_both() -> None:
    question = "nba Anthony Davis most offensive rebound in march 24 2018"
    read = cuts_under(GAME_HIGHS, read_cuts(question, CutsContext()), split=None)
    assert [(question[c.start : c.end], c.what) for c in read.claims] == [("in march 24 2018", "situation+date")]


def test_the_cuts_claims_ride_the_route_with_the_windows_and_the_spans() -> None:
    route = settle(PLAYER_LOG, "tatum last 5 home games vs the celtics in the 2024 playoffs", who=Who(kind="player", players=("Jayson Tatum",)))
    assert route.cuts == Cuts(venue="home") and route.window.count == 5 and route.span.season == 2024
    # Each tagger's claim is cut to the words it needed (Phase 3, step 3, span.needed): the window
    # needs "last 5" and "games" (without "games", "last 5 home" names no window), not "home",
    # which is the venue's alone.
    q = "tatum last 5 home games vs the celtics in the 2024 playoffs"
    assert [(c.what, q[c.start : c.end]) for c in route.claims] == [("window", "last 5"), ("venue", "home"), ("window", "games"), ("season", "2024"), ("season_type", "playoffs")]


# ---------------- the cells ----------------


def test_the_cells_and_what_a_reader_leaves_unhonored() -> None:
    assert Cuts().cells() == frozenset() and not Cuts().named
    cuts = Cuts(opponent="BOS", tenure="MIA", venue="home", date="2026-01-19", situation=_situation("in march"), round="finals", game_n=4, season_n=2)
    assert cuts.cells() == Cuts.CELLS and cuts.named
    assert unhonored_cells(Reading(cuts=cuts), frozenset()) == ["opponent", "tenure", "venue", "date", "situation", "game_n", "season_n", "round"]
    assert unhonored_cells(Reading(cuts=cuts), Cuts.CELLS - {"round", "tenure"}) == ["tenure", "round"]
    assert unhonored_cells(Reading(cuts=Cuts(round="finals", opponent="BOS")), cells_stated(PLAYER_LOG)) == ["round"]
    assert unhonored_cells(Reading(cuts=Cuts(tenure="MIA")), cells_stated(PLAYER_LOG)) == [] and unhonored_cells(Reading(cuts=Cuts(tenure="MIA")), TEAM_RELATION_SCOPING) == ["tenure"]
    assert cell_set(Reading(cuts=Cuts(tenure="MIA")), "tenure") and not cell_set(Reading(), "tenure") and cell_set(Reading(companions=(Companion(player="X", predicate="absent"),)), "companion")


def test_the_relation_tables_declare_the_cuts_once() -> None:
    assert Cuts.CELLS - {"round"} <= RELATION_SCOPING and "round" not in RELATION_SCOPING
    assert Cuts.CELLS - {"round", "tenure", "season_n"} == TEAM_CUTS and TEAM_CUTS <= TEAM_RELATION_SCOPING and not ({"round", "tenure", "season_n"} & TEAM_RELATION_SCOPING)
    for table in (RELATION_SCOPING_EXCLUDED, TEAM_RELATION_SCOPING_EXCLUDED):
        assert all("own_team" not in row.unstated and "round" not in row.unstated for row in table.values())
    # A reader whose words state fewer cuts than the relation's says so in its shape's row, with a reason per cut.
    for shape in (PLAYER_RANKING, SEASON_HISTORY, PLAYER_COMPARISON, NETPOINTS_RATINGS, GAME_HIGHS):
        assert cells_stated(shape) & Cuts.CELLS == frozenset(), shape
    assert (
        cells_stated(GAMES_COUNTED) & Cuts.CELLS == {"season_n"} and cells_stated(NETPOINTS_FINGERPRINT) & Cuts.CELLS == {"date"} and cells_stated(PERIOD_RANKING) & Cuts.CELLS == {"opponent", "venue"}
    )
    assert cells_stated(PLAYER_LOG) & Cuts.CELLS == Cuts.CELLS - {"round"}
    assert cells_stated(PRESENCE_SPLIT) & Cuts.CELLS == {"opponent"} and cells_stated(TEAM_LINE) & Cuts.CELLS == frozenset() and cells_stated(TEAM_RECORD) & Cuts.CELLS == TEAM_CUTS - {"date"}
    assert all(RELATION_SCOPING_EXCLUDED[PLAYER_RANKING].unstated[cut] for cut in Cuts.CELLS - {"round"})
    # No shape states a cut its relation does not carry: every cut a shape states is a relation table's, less its row's exclusions.
    from association.query import compose

    for shape in compose._ROUTES:
        assert cells_stated(shape) & Cuts.CELLS <= Cuts.CELLS - {"round"}, shape
    assert cells_stated(PointShape("netpoints", "chart", "fingerprint")) & Cuts.CELLS == {"date"}
    assert cells_stated(PointShape("team_games", "split", "presence")) & Cuts.CELLS == {"opponent"}


# ---------------- contract 4: applying each cut changes the result ----------------


def _player_games(ctx: AnswerContext, cuts: Cuts, span: Span | None = None) -> tuple[int, int]:
    """The games and the points a total over Podziemski's season reads under ``cuts``."""
    (row,) = rows_of(
        ctx.con,
        compile_query(
            ctx.con,
            Query(**(Reading(who=Who(kind="player", players=("Brandin Podziemski",)), cuts=cuts, span=span or Span())).filters(), skeleton="scalar", measures=["points"], aggregate=Aggregate.TOTAL),
        ),
    )
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
    settled = scoped_team(ctx.con, Reading(who=Who(kind="team", teams=("Celtics",)), span=span or Span()), "no team")
    assert isinstance(settled, tuple)
    team, resolved = settled
    narrowed = team_games(ctx.con, team, resolved, Reading(cuts=cuts), opponent=cuts.opponent, date=cuts.date)
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
    read = cuts_under(PLAYER_LOG, read_cuts("curry home games vs the lakers", CutsContext(opponent="Los Angeles Lakers")), split=None)
    assert isinstance(read, CutsRead) and read.cuts == Cuts(opponent="Los Angeles Lakers", venue="home") and read.claims == (Claim(6, 10, "venue"),) and read.dated_since is None
