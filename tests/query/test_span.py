"""The span family, typed: the one tagger that reads the
seasons a question covers and the season type it reads
(:func:`association.query.span.read_span`), the characters it claims, the
typed value's door and projection (:class:`association.query.reading.Span`),
the relation's one resolution of it (:func:`association.query.player_relation.span_of`),
and the behavioral check that applying each span cell on each relation
changes the result.
"""

from __future__ import annotations

import itertools
from typing import Any

import pytest
from test_readers import pg_ctx, team_cells_con  # noqa: F401 - the two relation fixtures, imported by name

from association.nba.season import current_season
from association.query.answer import AnswerContext
from association.query.compose.core import Query, compile_query, rows_of
from association.query.compose.plan import cells_stated
from association.query.player_relation import condition_scope, span_of
from association.query.point import span_under
from association.query.reading import (
    GAME_HIGHS,
    GAMES_COUNTED,
    PERIOD_LOG,
    PLAYER_COMPARISON,
    PLAYER_LINE,
    PLAYER_LOG,
    PLAYER_MEETINGS,
    PLAYER_RANKING,
    PLAYER_SPLITS,
    PRESENCE_SPLIT,
    SEASON_HISTORY,
    TEAM_MEETINGS,
    TEAM_RANKING,
    TEAM_RECORD,
    Claim,
    Cuts,
    PointShape,
    Reading,
    Span,
    unhonored_cells,
)
from association.query.reading import Subject as Who
from association.query.span import SpanContext, career_named, claimed, range_named, read_span
from association.query.team_games import TEAM_GAMES_SQL, TeamNarrowed
from association.query.team_relation import scoped_team, team_games

S = current_season()


def _read(question: str, shape: PointShape | None = PLAYER_LINE, **context: Any) -> Span:
    return span_under(shape, read_span(question, SpanContext(**context))).span


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
        # Two bare years joined by "to"/"through", or two adjacent ones by "and" (#345, #289), are the
        # range between them, not the current season.
        ("most three pointers from 2002 to 2004", Span(season_type=2, since=2002, until=2004)),
        ("how many games did miami heat play 2009 through 2023", Span(season_type=2, since=2009, until=2023)),
        ("2024 and 2025 Knicks record by month", Span(season_type=2, since=2024, until=2025)),
        # Two seasons apart are no range: neither is read, rather than the three between them.
        ("knicks record in 2020 and 2024", Span(season_type=2)),
        # A second year that opens a season span is the span's ("2001-02" is 2002).
        ("tmac 2001 to 2001-02", Span(season=2002, season_type=2)),
        # A span to a bare year is the range to the season the year names (#382): the year is read,
        # not left for the span's own season to answer.
        ("curry playoff stats 2014-15 to 2017", Span(season_type=3, since=2015, until=2017)),
        ("how many games did miami heat play 2008-09 to 2025", Span(season_type=2, since=2009, until=2025)),
        ("kobe stats 1999-00 through 2002", Span(season_type=2, since=2000, until=2002)),
        ("curry stats 2015-18 to 2020", Span(season_type=2, since=2015, until=2020)),
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
    assert _read("sga's 2pt percentage for the past 5 years", shape=SEASON_HISTORY) == Span(season_type=2)
    assert _read("sga's 2pt percentage for the past 5 years", shape=PLAYER_LOG) == Span(season_type=2, since=S - 4)


def test_a_range_opened_on_a_dated_day_starts_the_seasons_there() -> None:
    assert _read("towns home rec since 1/26/20 vs spurs", dated_since=2020) == Span(season_type=2, since=2020)
    # A career the words name stands over it.
    assert _read("towns career rec since 1/26/20", dated_since=2020) == Span(season_type=2, career=True)


def test_a_range_of_calendar_days_is_the_seasons_its_days_fall_in() -> None:
    # ISSUES.md #345: the cuts tagger hands the span the first and last season.
    assert _read("james harden ts playoffs from january 1 2015 to june 2 2021", dated_since=2015, dated_until=2021, dated_both=True) == Span(season_type=3, since=2015, until=2021)
    # Inside one season it is that season, cut at the days.
    assert _read("curry from april 16th 2022 to may 22nd 2022", dated_since=2022, dated_until=2022, dated_both=True) == Span(season=2022, season_type=2, both=True)
    # A regular season named, short or in full, keeps the type.
    assert _read("curry from december 1st 2020 to november 28th 2021 in the reg season", dated_since=2021, dated_until=2022, dated_both=True) == Span(season_type=2, since=2021, until=2022)
    assert _read("kings record between october 10 2016 and february 19 2017", dated_since=2017, dated_until=2017) == Span(season=2017, season_type=2)


@pytest.mark.parametrize(
    "question",
    [
        # ISSUES.md #167: a franchise's history is every season on record.
        "most points in a game in cavs history",
        "most points in a game in pistons history power forward",
        "most points in the history of the philadelphia 76ers",
        "most points in a game in knicks franchise history",
        "knicks franchise leader in points",
        "most points in nba history",
    ],
)
def test_a_franchises_history_is_a_career(question: str) -> None:
    assert career_named(question)[0]


@pytest.mark.parametrize("question", ["knicks franchise player stats", "history of the lakers vs celtics rivalry games last season"])
def test_franchise_or_history_alone_is_no_career(question: str) -> None:
    assert not career_named(question)[0]


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        # ISSUES.md #393: two two-digit seasons after "between"/"from", and a
        # decade written short after "the", are ranges of seasons.
        ("who averaged the most points per game in the nba between 03 to 25", (2003, 2025)),
        ("most pts in the 80s", (1980, 1989)),
        ("How many players averaged 30 ppg in the '90s", (1990, 1999)),
        # Never a line or a percentage.
        ("players scoring between 20 to 30 points", None),
        ("who shot in the 90s from the line", None),
        ("players shooting in the 40s", None),
    ],
)
def test_short_years_and_a_short_decade_are_ranges(question: str, expected: tuple[int, int] | None) -> None:
    assert range_named(question) == expected


def test_a_count_a_pair_and_a_log_imply_a_career_without_a_career_word() -> None:
    assert _read("how many times has embiid fouled out?", shape=GAMES_COUNTED, player_named=True, how_many=True).career
    assert not _read("how many times has embiid fouled out this season?", shape=GAMES_COUNTED, player_named=True, how_many=True).career
    assert not _read("how many times has embiid fouled out in his 5th season?", shape=GAMES_COUNTED, player_named=True, how_many=True, season_n=5).career
    assert _read("steph curry record vs lebron", shape=PLAYER_MEETINGS, record=True, versus=True).career
    assert _read("jaylen brown last 8 games vs pistons", shape=PLAYER_LOG, versus=True, window_named=True).career
    assert _read("prichard stats vs 76ers including playoffs game log", shape=PLAYER_LOG, versus=True).career
    assert not _read("jaylen brown last 8 games vs pistons this season", shape=PLAYER_LOG, versus=True, window_named=True).career
    # The opponent read with no versus word beside it (ISSUES.md #446).
    assert _read("last 3 meetings between knicks and nets", shape=PLAYER_LOG, opponent=True, window_named=True).career


@pytest.mark.parametrize(
    ("question", "shape", "context"),
    [
        # A player's games against one opponent: every meeting.
        ("scottie barnes points vs bulls", PLAYER_LINE, {"player_named": True, "opponent": True, "versus": True}),
        ("norman powell v portland last 20 games", PLAYER_LOG, {"player_named": True, "opponent": True, "order": "recent", "limit": 20}),
        # Two players set against each other: their meetings.
        ("kevin garnett vs tim duncan games", PLAYER_MEETINGS, {"pair": True, "versus": True}),
        ("Kobe and Ray Allen stats head to head stats", PLAYER_MEETINGS, {"pair": True}),
        # A companion who played: the games the two shared.
        ("ADs record with lebron", PLAYER_SPLITS, {"player_named": True, "companion_played": True, "record": True}),
        ("Lakers record when AD plays and lebron doesnt", PRESENCE_SPLIT, {"companion_played": True, "companion_absent": True, "record": True}),  # codespell:ignore doesnt - read whole
        # A companion who reached a line, in the postseasons named.
        ("knicks record in playoff games when mitchell robinson has 4 fta", PLAYER_SPLITS, {"companion_reached": True, "record": True}),
        # A holiday: every one on record.
        ("lebron james Christmas Day game record", PLAYER_SPLITS, {"player_named": True, "annual_day": True, "record": True}),
        # A count of games over a line, with no "how many".
        ("stephen curry 50 point games", GAMES_COUNTED, {"player_named": True}),
        # The single game's high, a player's and a team's players'.
        ("lebron james most threes in a game", GAME_HIGHS, {"player_named": True}),
        ("most points in a single game on the magic", PLAYER_RANKING, {}),
        # A player with no game this season, asked his line.
        ("kobe bryant playoff stats", PLAYER_LINE, {"player_named": True, "inactive": True}),
        ("tim duncan threes made", PLAYER_LINE, {"player_named": True, "inactive": True}),
        # A ranking's floor of more games than a season holds.
        ("nba plus minus per game leaders minimum 100 games played", PLAYER_RANKING, {"minimum_games": 100}),
        # The newest N playoff games.
        ("kawhi last 10 playoff games", PLAYER_LOG, {"player_named": True, "order": "recent", "limit": 10}),
    ],
)
def test_the_words_imply_a_career_where_no_season_is_named(question: str, shape: PointShape, context: dict[str, Any]) -> None:
    """Decision D13: a question naming no season reads the career where its
    words imply history - and a season named, this one included, wins."""
    read = span_under(shape, read_span(question, SpanContext(**context)))
    assert read.span.career and read.span.season is None and read.implied
    assert not _read(f"{question} in 2026", shape=shape, **context).career
    assert not _read(f"{question} this season", shape=shape, **context).career


@pytest.mark.parametrize(
    ("question", "shape", "context"),
    [
        # An active player's own line: the current season stays.
        ("james harden stats", PLAYER_LINE, {"player_named": True}),
        ("tyrese maxey ppg", PLAYER_LINE, {"player_named": True}),
        ("jokic last 5 games", PLAYER_LOG, {"player_named": True, "order": "recent", "limit": 5}),
        # A companion's line on this season's games (the yardstick's F087).
        ("what was the sixers record when maxey scored 20+ points?", PLAYER_SPLITS, {"companion_reached": True, "record": True}),
        # An absence alone is read over the question's span (D15).
        ("jeremiah fears stats without dejounte murray", PRESENCE_SPLIT, {"player_named": True}),
        # One season asked for, which no career answers.
        ("michael jordan's best season", PLAYER_LINE, {"player_named": True, "inactive": True}),
        ("michael jordan rookie stats", PLAYER_LINE, {"player_named": True, "inactive": True}),
        # A denial the companions' roles do not hold (the reading read both as playing).
        ("Lakers record when AD plays and lebron doesnt", PRESENCE_SPLIT, {"companion_played": True, "record": True}),  # codespell:ignore doesnt - the feed's question as typed
        # A versus word with nobody read on its other side.
        ("ron artest vs carmelo anthony", PLAYER_LINE, {"player_named": True, "inactive": True, "versus": True}),
        # An inactive player asked what no line of his answers.
        ("how many rings does jeremy lin have", PLAYER_LINE, {"player_named": True, "inactive": True, "how_many": True}),
        ("what position did moses malone play", PLAYER_LINE, {"player_named": True, "inactive": True}),
        ("vince carter teammates", PLAYER_LINE, {"player_named": True, "inactive": True}),
        ("michael jordan lowest scoring games", PLAYER_LINE, {"player_named": True, "inactive": True}),
        # A floor one season can hold, and an average "in a game".
        ("plus minus leaders minimum 50 games", PLAYER_RANKING, {"minimum_games": 50}),
        ("who averages the most points in a game", PLAYER_RANKING, {}),
        # A range named wins.
        ("jayson tatum's games against the knicks in the past two seasons", PLAYER_LOG, {"player_named": True, "opponent": True, "versus": True}),
    ],
)
def test_the_words_imply_no_career(question: str, shape: PointShape, context: dict[str, Any]) -> None:
    """Decision D13's other half: the current season stays where the words
    ask a current line, and the careers stay off a question whose words
    ask one season, or read short of it."""
    read = span_under(shape, read_span(question, SpanContext(**context)))
    assert not read.implied and not read.span.career


def test_a_guard_word_is_claimed_where_it_keeps_a_career_off() -> None:
    """The words that keep an implied career off are what the span read."""
    question = "michael jordan's best season stats"
    read = span_under(PLAYER_LINE, read_span(question, SpanContext(player_named=True, inactive=True)))
    assert [(c.what, question[c.start : c.end]) for c in read.claims] == [("span", "best")]
    # Nothing to keep off, nothing read: no line asked of him.
    read = span_under(PLAYER_LINE, read_span("michael jordan's best season", SpanContext(player_named=True, inactive=True)))
    assert read.claims == ()
    question = "how many rebounds does jeremy lin have"
    read = span_under(PLAYER_LINE, read_span(question, SpanContext(player_named=True, inactive=True, how_many=True)))
    assert [(c.what, question[c.start : c.end]) for c in read.claims] == [("span", "how many")]


def test_a_bare_last_n_games_log_reads_both_types() -> None:
    assert _read("knicks last 5 games", shape=PLAYER_LOG, order="recent", limit=5).both
    assert not _read("knicks last 5 regular season games", shape=PLAYER_LOG, order="recent", limit=5).both
    assert not _read("knicks last 5 games", shape=PLAYER_LOG, order="first", limit=5).both


@pytest.mark.parametrize(
    ("question", "shape", "context"),
    [
        # An opponent and the career it implies.
        ("kyrie irving last 5 games vs thunder", PLAYER_LOG, {"player_named": True, "opponent": True, "versus": True}),
        # A venue beside it.
        ("jalen brunson last 5 home games vs 76ers", PLAYER_LOG, {"player_named": True, "opponent": True, "versus": True}),
        # A named season.
        ("brook lopez last 10 games against washington in 2024", PLAYER_LOG, {"player_named": True, "opponent": True, "versus": True}),
        # Two teams' meetings.
        ("spurs record vs thunder last 16 games", TEAM_MEETINGS, {"opponent": True, "versus": True, "record": True}),
    ],
)
def test_a_stretch_of_games_reads_both_types_whatever_else_is_set(question: str, shape: PointShape, context: dict[str, Any]) -> None:
    """Decision D14: the last N games are the last N played, both season
    types, with an opponent, a venue, a career or a named season beside the
    window - and a type named, or a stretch of seasons, keeps one."""
    window = {"order": "recent", "limit": int(next(w for w in question.split() if w.isdigit()))}
    assert _read(question, shape=shape, **context, **window).both
    assert not _read(question.replace("games", "regular season games"), shape=shape, **context, **window).both
    assert not _read(question.replace("games", "playoff games"), shape=shape, **context, **window).both
    assert not _read(question, shape=shape, **context, **window, window_of="seasons").both


def test_a_stretch_of_games_stays_one_type_where_its_reader_reads_one() -> None:
    """Not yet a quarter's or a half's (the period reads label and caveat one
    season's games), nor a declared relation's single game, nor a team's
    with/without split, which takes no window."""
    window = {"order": "recent", "limit": 10}
    assert not _read("derozan 2nd half stats vs lac last 10 games", shape=PERIOD_LOG, player_named=True, opponent=True, **window).both
    assert not _read("piston vs heat last 15 games without ausar thompson", shape=PRESENCE_SPLIT, opponent=True, **window).both


# ---------------- the claims ----------------


def test_the_tagger_claims_the_characters_it_read_once() -> None:
    question = "Payton Pritchard stats vs 76ers at home including playoffs game log"
    read = span_under(PLAYER_LOG, read_span(question, SpanContext(versus=True)))
    assert read.claims == (Claim(40, 58, "both"),) and question[40:58] == "including playoffs"  # the "playoffs" inside it folds in
    question = "kobe playoff stats from 02-03 to 06-07"
    read = span_under(PLAYER_LINE, read_span(question, SpanContext()))
    assert [(c.what, question[c.start : c.end]) for c in read.claims] == [("season_type", "playoff"), ("range", "02-03 to 06-07")]
    read = span_under(PLAYER_LINE, read_span("jokic career averages in 2023", SpanContext()))
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
    claims = span_under(PLAYER_LINE, read_span(question, SpanContext())).claims
    assert all(a.end <= b.start for a, b in itertools.pairwise(claims))


def test_claimed_folds_a_nested_claim_and_joins_a_partial_overlap() -> None:
    """A claim inside another is the same reading; two that share a word are
    one claim named for both readings - "his last game 7" is the window's
    "last game" and the postseason's "game 7", one joined claim and no
    error out of the reader."""
    assert claimed([Claim(10, 30, "both"), Claim(20, 28, "season_type")]) == (Claim(10, 30, "both"),)
    assert claimed([Claim(10, 20, "range"), Claim(15, 25, "season")]) == (Claim(10, 25, "range+season"),)
    assert claimed([Claim(15, 25, "season"), Claim(10, 20, "range"), Claim(22, 30, "career")]) == (Claim(10, 30, "range+season+career"),)


def test_a_word_two_readings_share_reads_both_and_raises_nothing() -> None:
    """ "lebron's last game 7": one game at the end of his span (the window)
    and the seventh game of a series (the postseason's word and the series
    game), both read, the shared "game" claimed once for both."""
    from routed import staged as settle

    route = settle(PLAYER_LOG, "lebron's last game 7", who=Who(kind="player", players=("LeBron James",)))
    assert route.window.order == "recent" and route.window.count == 1 and route.span.season_type == 3 and route.cuts.game_n == 7
    assert [c.what for c in route.claims] == ["window+game_n"]


# ---------------- the typed value ----------------


def test_the_cells_and_what_a_reader_leaves_unhonored() -> None:
    assert Span().cells() == frozenset()
    assert Span(since=2020, until=2024, both=True, career=True).cells() == {"range", "both", "career"}
    assert unhonored_cells(Reading(span=Span(both=True, career=True)), frozenset({"both"})) == ["career"]
    assert unhonored_cells(Reading(cuts=Cuts(opponent="BOS"), span=Span(since=2020, both=True)), frozenset({"opponent", "range"})) == ["both"]
    assert Span(season=2024, season_type=3, both=True).stated == {"season", "postseason", "both"}
    assert Span(since=2020).over_career() == Span(career=True, since=2020)
    assert Span(season=2024).as_career() == Span(season=2024, career=True)
    assert Span(career=True, since=2020, until=2024).without_range() == Span(career=True)


def test_the_relation_tables_declare_the_three_cells_once() -> None:
    assert cells_stated(PLAYER_LOG) >= Span.CELLS and cells_stated(TEAM_RECORD) >= Span.CELLS
    assert cells_stated(PLAYER_RANKING) & Span.CELLS == {"career"} and cells_stated(PLAYER_COMPARISON) & Span.CELLS == frozenset()
    assert cells_stated(TEAM_RANKING) & Span.CELLS == {"range"} and cells_stated(PRESENCE_SPLIT) & Span.CELLS == {"career"}


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


# ---------------- applying each cell changes the result ----------------


def _player_games(ctx: AnswerContext, span: Span) -> int:
    rows = rows_of(ctx.con, compile_query(ctx.con, Query(**(Reading(who=Who(kind="player", players=("Brandin Podziemski",)), span=span)).filters(), skeleton="rows", measures=["points"], limit=50)))
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
    settled = scoped_team(ctx.con, Reading(who=Who(kind="team", teams=("Celtics",)), span=span), "no team")
    assert isinstance(settled, tuple)
    team, resolved = settled
    narrowed = team_games(ctx.con, team, resolved, Reading(), opponent=None)
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
