"""The games' cuts, typed: the one tagger
that reads which games of a span a read sees
(:func:`association.query.cuts.read_cuts`), the characters it claims, the
typed value's door and projection (:class:`association.query.reading.Cuts`,
:class:`~association.query.reading.Situation`), the relations' one
resolution of each cut, and the behavioral check that applying each cut on
each relation changes the result.
"""

from __future__ import annotations

from typing import Any

import pytest
from routed import staged as settle
from test_readers import pg_ctx, team_cells_con  # noqa: F401 - the two relation fixtures, imported by name

from association.nba.season import current_season
from association.query.answer import AnswerContext
from association.query.calendar import AlignmentNarrowing, CalendarNarrowing
from association.query.compose.core import Query, compile_query, rows_of
from association.query.compose.plan import cells_stated
from association.query.cuts import CutsContext, CutsRead, own_alignment, read_cuts
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
    TEAM_RANKING,
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
        # A date on a career question fixes no year: a situation, the day of every season.
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
        # A round's abbreviation is the round (#475): it answered the regular season.
        ("lebron 2014-2016 ecf", PLAYER_LINE, Cuts(round="ecf")),
        ("Caleb martin ecf", PLAYER_LINE, Cuts(round="ecf")),
        ("jokic wcf stats", PLAYER_LINE, Cuts(round="wcf")),
        ("tatum ecsf stats", PLAYER_LINE, Cuts(round="ecsf")),
        # The Finals in the singular, where it names the series (#455): it answered the regular season.
        ("jaylen brown 2022 nba final stats", PLAYER_LINE, Cuts(round="nba final")),
        ("kobe stats in the final", PLAYER_LINE, Cuts(round="the final")),
        # ... and not where a count, a game or a stretch of play follows it.
        ("kobe stats in the final 5 games", PLAYER_LINE, Cuts()),
        ("kobe stats in the final quarter", PLAYER_LINE, Cuts()),
        ("Ayton stats in game 4 playoff games", PLAYER_LOG, Cuts(game_n=4)),
        ("his 18th season", PLAYER_LINE, Cuts(season_n=18)),
        ("most points in 15th season played", PLAYER_RANKING, Cuts(season_n=15)),
        # The opponents by their seeding: an alignment nothing reads, read whole so it is refused by
        # name - its "top 3" is no window of his last 3 games (#396).
        ("luka doncic stats vs top 3 seed teams in the 2026 season", PLAYER_LINE, Cuts(situation=_situation("vs top 3 seed teams"))),
        ("tatum points against top seeds", PLAYER_LINE, Cuts(situation=_situation("against top seeds"))),
        # A day, and no month of its own beside it (#354).
        ("nba Anthony Davis most offensive rebound in march 24 2018", GAME_HIGHS, Cuts(date="2018-03-24")),
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


@pytest.mark.parametrize(
    ("question", "text", "since", "until", "both"),
    [
        # ISSUES.md #345: a range of calendar days with their years is the
        # situation, whole; its seasons are the span's, and where it can
        # hold a postseason game it hands the span both season types.
        ("james harden ts playoffs from january 1 2015 to june 2 2021", "from january 1 2015 to june 2 2021", 2015, 2021, True),
        ("Sacramento kings record between October 10 2016 and february 19 2017", "between october 10 2016 and february 19 2017", 2017, 2017, False),
        ("Scottie Barnes averages Feb 28th to April 14th 2026", "feb 28th to april 14th 2026", 2026, 2026, True),
        ("how many 20 point games did curry have from april 16th 2022 to may 22nd 2022", "from april 16th 2022 to may 22nd 2022", 2022, 2022, True),
    ],
)
def test_a_range_of_calendar_days_is_one_situation_and_its_seasons(question: str, text: str, since: int, until: int, both: bool) -> None:
    read = cuts_under(PLAYER_LINE, read_cuts(question, CutsContext()), split=None)
    situation = read.cuts.situation
    assert situation is not None and situation.text == text and situation.calendar is not None and situation.calendar.kind == "date_range"
    assert read.cuts.date is None
    assert (read.dated_since, read.dated_until, read.dated_both) == (since, until, both)


@pytest.mark.parametrize(
    ("question", "text", "since"),
    [
        # ISSUES.md #353: a worded since-date with its year is the
        # situation whole, and the season its day falls in opens the span.
        ("lebron ppg since january 31st 2020", "since january 31st 2020", 2020),
        ("Nick Richards stats after Dec 11 2023", "after dec 11 2023", 2024),
        ("Since January 31st, 2015, what is the best NBA record?", "since january 31st, 2015", 2015),
    ],
)
def test_a_worded_since_with_its_year_hands_its_season_to_the_span(question: str, text: str, since: int) -> None:
    read = cuts_under(PLAYER_LINE, read_cuts(question, CutsContext()), split=None)
    situation = read.cuts.situation
    assert situation is not None and situation.text == text and situation.calendar is not None and situation.calendar.kind == "since_date"
    assert read.dated_since == since


def test_a_worded_since_beside_another_circumstance_keeps_that_one() -> None:
    read = read_cuts("celtics record vs eastern conference since september 11th 2001", CutsContext())
    assert read.cuts.situation is not None and read.cuts.situation.text == "vs eastern conference" and read.dated_since is None


@pytest.mark.parametrize(
    ("question", "day", "words"),
    [
        # ISSUES.md #323: a day written in numbers with its year is that
        # day, ISO or slashed, a two-digit year in the 2000s.
        ("sga game log on 2026-01-02", "2026-01-02", "2026-01-02"),
        ("sga fingerprint on 2026-01-02", "2026-01-02", "2026-01-02"),
        ("11/29/24 Jrue Holiday", "2024-11-29", "11/29/24"),
        ("What was Josh Harts stats on 11/28/2025", "2025-11-28", "11/28/2025"),
    ],
)
def test_a_day_written_in_numbers_with_its_year_is_that_day(question: str, day: str, words: str) -> None:
    read = read_cuts(question, CutsContext())
    assert read.cuts.date == day and read.cuts.situation is None
    assert [question[c.start : c.end] for c in read.claims if c.what == "date"] == [words]


@pytest.mark.parametrize(
    ("question", "text"),
    [
        # A range word before it makes it a range, refused by value as the
        # worded "before february 3 2024" is; a day no calendar has is the
        # same, never nothing; no year is no day ("7/14" is a shooting line).
        ("steph curry vs hawks prior to 02/03/2024", "prior to 02/03/2024"),
        ("steph curry vs hawks before 2024-02-03", "before 2024-02-03"),
        ("sga points on 2/30/2024", "2/30/2024"),
    ],
)
def test_a_numeric_day_opened_by_a_range_word_or_on_no_calendar_is_a_situation(question: str, text: str) -> None:
    read = read_cuts(question, CutsContext())
    assert read.cuts.date is None and read.cuts.situation is not None and read.cuts.situation.text == text and read.cuts.situation.calendar is None


@pytest.mark.parametrize("question", ["curry shot 7/14 from three", "embiid 50/40/90 seasons", "harden 2024-25 stats"])
def test_numbers_that_are_no_day_name_no_day(question: str) -> None:
    read = read_cuts(question, CutsContext())
    assert read.cuts.date is None and read.cuts.situation is None


def test_a_since_written_as_an_iso_date_is_every_game_from_it() -> None:
    read = cuts_under(PLAYER_LOG, read_cuts("sga game log since 2026-01-02", CutsContext()), split=None)
    situation = read.cuts.situation
    assert situation is not None and situation.calendar == CalendarNarrowing("since_date", "2026-01-02", "since January 2, 2026") and read.dated_since == 2026


@pytest.mark.parametrize(
    ("question", "text", "month"),
    [
        # A season's month written right before its year, with no "in", is
        # the month cut; the year is the span's.
        ("Luka doncic march 2026", "march", 3),
        ("steph stats may 2021", "may", 5),
        ("jokic stats oct 2024", "oct", 10),
    ],
)
def test_a_month_right_before_its_year_is_the_month(question: str, text: str, month: int) -> None:
    read = read_cuts(question, CutsContext())
    situation = read.cuts.situation
    assert situation is not None and situation.text == text and situation.calendar is not None
    assert (situation.calendar.kind, situation.calendar.value) == ("month", month)


@pytest.mark.parametrize(
    ("question", "text", "since", "first"),
    [
        ("nikola jokić stats since january 2021", "since january 2021", 2021, "2021-01-01"),
        ("lebron after december 2020", "after december 2020", 2021, "2021-01-01"),
        ("celtics record from oct 2019", "from oct 2019", 2020, "2019-10-01"),
    ],
)
def test_a_range_opened_on_a_month_with_its_year_is_its_first_day_on(question: str, text: str, since: int, first: str) -> None:
    read = read_cuts(question, CutsContext())
    situation = read.cuts.situation
    assert situation is not None and situation.text == text and situation.calendar is not None and situation.calendar.kind == "since_date"
    assert situation.calendar.value == first and read.dated_since == since


@pytest.mark.parametrize("question", ["who may 2 win", "march madness 2024 upsets", "lebron stats in 2021"])
def test_a_month_word_with_no_year_right_after_it_is_no_month(question: str) -> None:
    assert read_cuts(question, CutsContext()).cuts.situation is None


@pytest.mark.parametrize("question", ["luka dončić average after a playoff loss 2024playoff", "celtics record coming off a win"])
def test_games_after_a_result_are_a_situation_refused_by_value(question: str) -> None:
    situation = read_cuts(question, CutsContext()).cuts.situation
    assert situation is not None and situation.calendar is None and situation.alignment is None


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


def test_a_day_reads_no_month_of_its_own() -> None:
    """#354: "in march 24 2018" is one day, and the month in it no cut of
    its own - no situation "in march" inside it, and no joined
    ``situation+date`` claim over "march". A month with no day after it is
    the situation; a month before a year is no day."""
    question = "nba Anthony Davis most offensive rebound in march 24 2018"
    read = cuts_under(GAME_HIGHS, read_cuts(question, CutsContext()), split=None)
    assert read.cuts.situation is None and read.cuts.date is not None
    assert [(question[c.start : c.end], c.what) for c in read.claims] == [("march 24 2018", "date")]
    for question in ("lebron points in march", "lebron points in march 2018"):
        read = cuts_under(GAME_HIGHS, read_cuts(question, CutsContext()), split=None)
        assert read.cuts.situation is not None and read.cuts.situation.text == "in march", question


def test_the_cuts_claims_ride_the_route_with_the_windows_and_the_spans() -> None:
    route = settle(PLAYER_LOG, "tatum last 5 home games vs the celtics in the 2024 playoffs", who=Who(kind="player", players=("Jayson Tatum",)))
    assert route.cuts == Cuts(venue="home") and route.window.count == 5 and route.span.season == 2024
    # Each tagger's claim is cut to the words it needed (span.needed): the window
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


# ---------------- applying each cut changes the result ----------------


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


@pytest.mark.parametrize(
    ("question", "shape", "words"),
    [
        # ISSUES.md #339: a conference a ranking's subject belongs to.
        ("who leads the east in scoring", PLAYER_RANKING, "the east"),
        ("top scorers in the western conference this season", PLAYER_RANKING, "in the western conference"),
        ("best record in the east", TEAM_RANKING, "in the east"),
        # The opponent's, and off a ranking, keep their reading.
        ("top scorers vs the west", PLAYER_RANKING, None),
        ("lebron points in the west", PLAYER_LINE, None),
    ],
)
def test_a_rankings_own_conference_is_read_apart_from_the_opponents(question: str, shape: PointShape, words: str | None) -> None:
    assert own_alignment(shape, read_cuts(question, CutsContext()).cuts) == words


@pytest.mark.parametrize(
    ("question", "text"),
    [
        # ISSUES.md #400: the opponents by their standing are a situation read whole.
        ("luka stats vs top 3 teams this season", "vs top 3 teams"),
        ("luka stats against top 5 defenses", "against top 5 defenses"),
        ("who scores the most vs the best defenses", "vs the best defenses"),
        ("luka stats vs the top 4 seeds", "vs the top 4 seeds"),
    ],
)
def test_the_opponents_by_their_standing_are_a_situation_nothing_reads(question: str, text: str) -> None:
    read = read_cuts(question, CutsContext())
    situation = read.cuts.situation
    assert situation is not None and situation.text == text and not situation.read
    assert [question[c.start : c.end] for c in read.unread_situation()] == [text]


@pytest.mark.parametrize(
    ("question", "text"),
    [
        # ISSUES.md #291: a quarter named as a vague condition, with no line.
        ("jokic assists per game after a big first quarter", "after a big first quarter"),
        ("jokic assists when he has a big first quarter", "when he has a big first quarter"),
        ("tatum points following a cold 2nd half", "following a cold 2nd half"),
    ],
)
def test_a_quarter_as_a_vague_condition_is_a_situation_nothing_reads(question: str, text: str) -> None:
    situation = read_cuts(question, CutsContext()).cuts.situation
    assert situation is not None and situation.text == text and not situation.read


def test_a_quarter_as_a_condition_with_a_line_is_no_situation() -> None:
    assert read_cuts("jokic assists after making 3 threes in the first quarter", CutsContext()).cuts.situation is None


def test_an_inactive_players_day_without_a_year_is_every_seasons_day() -> None:
    """Decision D13 reads an inactive player's question with no season named
    as his career, so a day without its year fixes no season: "kobe bryant
    on november 22nd record" read 2025-11-22 beside his 1997-2016 career
    and answered "No games ... on 2025-11-22". It is the day of every
    season, a situation; an active player's is this season's day."""
    assert _read("kobe bryant on november 22nd record", PLAYER_LINE, inactive=True) == Cuts(situation=_situation("november 22nd"))
    assert _read("kobe bryant on november 22nd record in 2010", PLAYER_LINE, inactive=True) == Cuts(date="2009-11-22")
    assert _read("Desmond bane march 17", PLAYER_LOG) == Cuts(date=f"{S}-03-17")
