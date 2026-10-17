"""What a question's words name that nothing here reads, and the refusals
the words come to before any reader runs - read by the parser onto the
Reading as causes (``Reading.unsupported``, ``Reading.refused``) and said by
the planner (``compose.plan.refusal_result``). Each test pairs the refusal
with the fact that makes it safe: nothing reads the same shape (so the
refusal never shadows an answer - the answering loop says it only where the
answer side declined), and the sentence names the thing that is actually
missing."""

from __future__ import annotations

from dataclasses import replace

import duckdb
import pytest
from routed import Said, measure_of, said

from association.query.calendar import parse_alignment, parse_situation
from association.query.compose.plan import refusal_result
from association.query.entities import names_of, players_of, teams_of
from association.query.parse import _reading_from_route_refused, _reading_from_route_unsupported, read_route
from association.query.reading import (
    PERIOD_LOG,
    PERIOD_RANKING,
    PLAYER_LINE,
    PLAYER_LOG,
    PLAYER_MEETINGS,
    PLAYER_RANKING,
    PLAYER_SPLITS,
    TEAM_OUTLOOK,
    TEAM_PERIOD_TOTAL,
    TEAM_RANKING,
    Cause,
    Cuts,
    Line,
    Measure,
    Period,
    PointShape,
    Reading,
    Span,
    Window,
    situation_of,
)
from association.query.reading import Subject as Who
from association.query.subject import Subject, read_subject


def unanswerable(con: duckdb.DuckDBPyConnection, asked: PointShape | None, scope: Reading, question: str) -> Said | None:
    """A test's typed reading - the filters it states, what the words ask
    (``asked``) and the subject read from the question - and the first
    thing its words name that nothing reads, as the planner says it (or
    None)."""
    reading = replace(scope, asked=asked, subject=read_subject(names_of(con), question, asked, scope))
    causes = _reading_from_route_unsupported(question, reading)
    return said(refusal_result(causes[0])) if causes else None


def by_question(question: str, asked: PointShape | None, con: duckdb.DuckDBPyConnection) -> Said | None:
    """The refusal the words come to before any reader runs, as the planner
    says it (or None)."""
    cause = _reading_from_route_refused(players_of(con), teams_of(con), question, Reading(asked=asked, subject=Subject("everyone")))
    return said(refusal_result(cause)) if cause is not None else None


@pytest.fixture
def con() -> duckdb.DuckDBPyConnection:
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    c.execute("INSERT INTO players VALUES ('1','LeBron James'),('2','Kawhi Leonard'),('3','VJ Edgecombe')")
    c.execute("CREATE TABLE teams (team_id VARCHAR, display_name VARCHAR, abbreviation VARCHAR)")
    c.execute("INSERT INTO teams VALUES ('1','Atlanta Hawks','ATL'),('2','Los Angeles Lakers','LAL')")
    return c


def test_an_answerable_question_is_not_refused(con: duckdb.DuckDBPyConnection) -> None:
    """A question whose words name nothing unread carries no cause: the
    refusal is said only where nothing here can read the question."""
    assert (
        unanswerable(con, PLAYER_LOG, Reading(who=Who(kind="player", players=("LeBron James",)), cuts=Cuts(opponent="Atlanta Hawks"), window=Window(count=5)), "lebron's last 5 vs the hawks") is None
    )
    assert (
        unanswerable(con, PERIOD_LOG, Reading(who=Who(kind="player", players=("VJ Edgecombe",)), measure=Measure(key="points", as_typed="points"), period=Period(number=1)), "vj 1st quarter points")
        is None
    )
    assert unanswerable(con, PLAYER_LINE, Reading(who=Who(kind="player", players=("LeBron James",)), cuts=Cuts(situation=situation_of("on christmas"))), "lebron on christmas") is None


def test_a_playoff_round_is_refused_naming_the_missing_label(con: duckdb.DuckDBPyConnection) -> None:
    """yardstick-v2 F165 "nba finals game log 2025": ``round`` is a cut no
    relation honors (the planner declines it), and the games carry no round
    label (ISSUES #10), so the refusal names the missing label."""
    scope = Reading(who=Who(kind="team", teams=("NBA Finals",)), cuts=Cuts(round="finals"), span=Span(season=2025, season_type=3))
    refusal = unanswerable(con, PLAYER_LOG, scope, "nba finals game log 2025")
    assert refusal is not None
    assert "not labeled by playoff round" in refusal.text and "'finals'" in refusal.text
    assert refusal.data["refused"] == "playoff_round"


@pytest.mark.parametrize(
    ("question", "measure", "words"),
    [
        ("toronto raptors points off turnovers", "turnovers", "points off turnovers"),
        ("lebron points in the paint", "points", "points in the paint"),
        ("lebron fast break points", "points", "fast break points"),
        ("celtics second chance points", "points", "second chance points"),
        ("celtics points allowed in the paint", "points", "points allowed in the paint"),
    ],
)
def test_points_scored_one_way_that_nothing_reads_are_refused_by_name(con: duckdb.DuckDBPyConnection, question: str, measure: str, words: str) -> None:
    """ISSUES.md #456: "toronto raptors points off turnovers" answered the
    Raptors' turnovers per game, the phrase read as its last word. Points
    scored one way are a figure of their own; where the measure read is
    not the team metric that reads them, the words are refused by name."""
    reading = Reading(asked=PLAYER_LINE, subject=Subject("everyone"), measure=measure_of(measure))
    cause = _reading_from_route_refused(players_of(con), teams_of(con), question, reading)
    assert cause is not None
    assert cause.kind == "fact_not_read" and cause.facts == {"fact": "points by play", "words": words}
    assert f"No figure for {words!r} is read here" in said(refusal_result(cause)).text


@pytest.mark.parametrize(("question", "measure"), [("toronto raptors points in the paint", "points_in_paint"), ("celtics fast break points this season", "fast_break_points")])
def test_a_teams_points_in_the_paint_and_fast_break_points_are_read_and_not_refused(con: duckdb.DuckDBPyConnection, question: str, measure: str) -> None:
    """The team metrics of those names read them (``team_metrics``), so the
    words pass where the measure read is that metric."""
    reading = Reading(asked=PLAYER_LINE, subject=Subject("everyone"), measure=measure_of(measure))
    assert _reading_from_route_refused(players_of(con), teams_of(con), question, reading) is None


@pytest.mark.parametrize(
    ("question", "fact", "words", "said"),
    [
        ("how old was shaq when he won in miami", "age", "how old", "a player's age ('how old')"),
        ("how old was shaq when he won a championship in miami", "age", "how old", "a player's age ('how old')"),
        ("active nba players youngest to oldest", "age", "youngest", "a player's age ('youngest')"),
        ("Cousins player efficiency rating by year", "player efficiency rating", "player efficiency rating", "No player efficiency rating is on file"),
        ("jordan PER by year", "player efficiency rating", "PER", "No player efficiency rating is on file"),
        ("the most double teamed player per possession", "double team", "double teamed", "No double team is on file"),
        ("jalen brown vs pacer playoff double team", "double team", "double team", "No double team is on file"),
    ],
)
def test_an_age_a_player_efficiency_rating_or_a_double_team_is_refused_as_the_fact_no_table_holds(con: duckdb.DuckDBPyConnection, question: str, fact: str, words: str, said: str) -> None:
    """The feed sample's false causes FS053, FS202, FS323, FS416 and the
    fluent FS391: an age asked outright was refused as a team no read
    placed or as the championship, a player efficiency rating as a
    ranking's missing stat, and a double team as a rate per possession or
    answered as the player's line - each is a fact no table holds, said
    before any point, ahead of the championship."""
    cause = _reading_from_route_refused(players_of(con), teams_of(con), question, Reading(asked=PLAYER_LINE, subject=Subject("everyone")))
    assert cause is not None and cause.kind == "fact_not_read" and cause.facts == {"fact": fact, "words": words}
    assert said in refusal_result(cause).text


@pytest.mark.parametrize("question", ["kevin durant points per game", "jaren jackson per career", "Luka career age by season", "lebron per 36 minutes stats"])
def test_a_lowercase_per_or_a_bare_age_is_no_fact(con: duckdb.DuckDBPyConnection, question: str) -> None:
    """A lowercase "per" is a unit or ambiguous (the sample's keyer read
    "jaren jackson per career" both as the rating and as his line), and a
    bare "age" may be a clipped "average": neither is refused as a fact."""
    assert _reading_from_route_refused(players_of(con), teams_of(con), question, Reading(asked=PLAYER_LINE, subject=Subject("everyone"))) is None


def test_an_age_as_a_narrowing_is_said_before_a_shapes_own_refusal(con: duckdb.DuckDBPyConnection) -> None:
    """The feed sample's FS114, "Most 30 point games by a 35 year old in a
    season", was told the best single season is read by nothing: the age
    is the fact no table holds, so nothing could answer past it, and it is
    the Reading's refusal, said before any reader."""
    reading = read_route(names_of(con), "Most 30 point games by a 35 year old in a season", [], "points")
    assert reading.refused is not None and reading.refused.kind == "non_calendar_situation" and reading.refused.facts["reads_as"] == "age"
    assert not reading.unsupported
    assert refusal_result(reading.refused).text.startswith("'35 year old' needs a birth date")


def test_an_age_is_refused_because_no_birth_date_is_on_record(con: duckdb.DuckDBPyConnection) -> None:
    """yardstick-v2 F054 "lebron ppg as an 18 year old": the calendar reader
    refuses the value (no weekday, month, holiday or "since <day>" in it),
    and the cause is the missing birth date - not "no data"."""
    assert parse_situation("18 year old") is None
    refusal = unanswerable(
        con,
        PLAYER_LINE,
        Reading(who=Who(kind="player", players=("LeBron James",)), cuts=Cuts(situation=situation_of("18 year old")), measure=Measure(key="points", as_typed="points")),
        "lebron ppg as an 18 year old",
    )
    assert refusal is not None
    assert "birth date" in refusal.text and "'18 year old'" in refusal.text
    # Hyphenated, as "18-year-old Lebron's ppg total" types it (#290): read as the same age, not
    # left unread for his season line to answer.
    from association.query.cuts import CutsContext, read_cuts
    from association.query.point import cuts_under

    situation = cuts_under(PLAYER_LINE, read_cuts("18-year-old Lebron's ppg total", CutsContext()), split=None).cuts.situation
    assert situation is not None and situation.text == "18-year-old" and not situation.read
    hyphenated = unanswerable(
        con,
        PLAYER_LINE,
        Reading(who=Who(kind="player", players=("LeBron James",)), cuts=Cuts(situation=situation_of("18-year-old")), measure=Measure(key="points", as_typed="points")),
        "18-year-old Lebron's ppg total",
    )
    assert hyphenated is not None and "birth date" in hyphenated.text and "'18-year-old'" in hyphenated.text


def test_a_conference_or_division_in_no_recognized_shape_names_its_own_cause(con: duckdb.DuckDBPyConnection) -> None:
    """A conference/division word outside the shapes ``parse_alignment`` reads
    ("vs the west", "against eastern conference teams", "vs the southeast
    division", "in the west") still gets its own sentence naming what would
    be read, not the generic "not something the games are read by" one - the
    words are right and only the phrasing is not, which is a different cause
    than an unreadable value entirely. A conference or division in a shape
    ``parse_alignment`` reads is the relation's
    (``test_an_alignment_situation_is_never_refused_here`` below).
    """
    refusal = unanswerable(
        con,
        PLAYER_SPLITS,
        Reading(who=Who(kind="player", players=("LeBron James",)), cuts=Cuts(situation=situation_of("the Central Division these days"))),
        "lebron in the central division these days",
    )
    assert refusal is not None
    assert "conference or division" in refusal.text and "not in a shape this reads" in refusal.text
    other = unanswerable(con, PLAYER_LINE, Reading(who=Who(kind="player", players=("LeBron James",)), cuts=Cuts(situation=situation_of("since returning"))), "lebron since returning")
    assert other is not None
    assert "not something the games are read by" in other.text


def test_a_calendar_situation_is_never_refused_here(con: duckdb.DuckDBPyConnection) -> None:
    """A calendar situation the relation reads is the relation's to answer or
    refuse by value - no cause here claims it."""
    for situation in ("on tuesdays", "in march", "on christmas", "since january 31st"):
        assert parse_situation(situation) is not None
        assert unanswerable(con, PLAYER_LOG, Reading(who=Who(kind="player", players=("LeBron James",)), cuts=Cuts(situation=situation_of(situation))), f"lebron games {situation}") is None


def test_an_alignment_situation_is_never_refused_here(con: duckdb.DuckDBPyConnection) -> None:
    """yardstick-v2 F055 "vs southeast division": a conference or division
    situation the relation reads is the relation's to answer or refuse by
    value - the same discipline the calendar shapes above keep."""
    for situation in ("vs southeast division", "vs the west", "against eastern conference teams", "in the west"):
        assert parse_alignment(situation) is not None
        assert unanswerable(con, PLAYER_SPLITS, Reading(who=Who(kind="player", players=("LeBron James",)), cuts=Cuts(situation=situation_of(situation))), f"lebron {situation}") is None


def test_a_stat_the_period_line_does_not_rebuild_is_refused(con: duckdb.DuckDBPyConnection) -> None:
    """yardstick-v2 F066-F068 "vj edgecombe 1st quarter assists by game":
    the period relation rebuilds assists, rebounds and the rest from the
    plays, so only a stat no play carries per period is refused, and the
    refusal names what IS rebuilt."""
    assert (
        unanswerable(
            con,
            PERIOD_LOG,
            Reading(who=Who(kind="player", players=("VJ Edgecombe",)), measure=Measure(key="assists", as_typed="assists"), period=Period(number=1)),
            "vj edgecombe 1st quarter assists by game",
        )
        is None
    )
    assert unanswerable(con, PERIOD_RANKING, Reading(measure=Measure(key="rebounds", as_typed="rebounds"), period=Period(number=2, half=True)), "most 2nd half rebounds") is None
    refusal = unanswerable(
        con, PERIOD_LOG, Reading(who=Who(kind="player", players=("VJ Edgecombe",)), measure=Measure(key="minutes", as_typed="minutes"), period=Period(number=1)), "vj edgecombe 1st quarter minutes"
    )
    assert refusal is not None
    assert "'minutes' is not among them" in refusal.text and "rebounds, assists" in refusal.text and "the 1st quarter" in refusal.text
    half = unanswerable(
        con,
        PERIOD_LOG,
        Reading(who=Who(kind="player", players=("VJ Edgecombe",)), measure=Measure(key="plusMinus", as_typed="plusMinus"), period=Period(number=2, half=True)),
        "vj 2nd half plus minus",
    )
    assert half is not None and "the 2nd half" in half.text
    # A shooting percentage is a ratio of two rebuilt columns, read by both
    # period readers (a player's and a team's) - so it is not refused here;
    # an advanced rate is, and the refusal names the percentages that ARE
    # read.
    assert (
        unanswerable(
            con,
            PERIOD_LOG,
            Reading(who=Who(kind="player", players=("VJ Edgecombe",)), measure=Measure(key="threePointFieldGoalPct", as_typed="threePointFieldGoalPct"), period=Period(number=1)),
            "vj edgecombe 1st quarter 3pt percentage by game",
        )
        is None
    )
    assert (
        unanswerable(
            con,
            TEAM_PERIOD_TOTAL,
            Reading(who=Who(kind="team", teams=("Knicks",)), measure=Measure(key="freeThrowPct", as_typed="freeThrowPct"), period=Period(number=4)),
            "knicks free throw percentage in the 4th quarter",
        )
        is None
    )
    advanced = unanswerable(
        con, PERIOD_LOG, Reading(who=Who(kind="player", players=("VJ Edgecombe",)), measure=Measure(key="ts_pct", as_typed="ts_pct"), period=Period(number=1)), "vj edgecombe 1st quarter true shooting"
    )
    assert advanced is not None and "free throw percentages from them" in advanced.text and "'ts_pct' is not among them" in advanced.text


def test_a_player_set_against_another_is_the_second_of_two_players(con: duckdb.DuckDBPyConnection) -> None:
    """yardstick-v2 F081 "lebron vs kawhi head to head": the parser names
    two players' meetings (``PLAYER_MEETINGS``) from the subject's kind -
    two players - and files neither as an opponent. A team opponent stays a
    narrowing of the one player's games."""
    from association.query.parse import read_route

    def parsed(question: str, names: list[str]) -> Reading:
        return read_route(names_of(con), question, names, "")

    for question in ("lebron vs kawhi head to head", "lebron vs kawhi record"):
        reading = parsed(question, ["lebron", "kawhi"])
        assert reading.asked == PLAYER_MEETINGS and reading.who.players == ("LeBron James", "Kawhi Leonard") and not reading.cuts.opponent, question
    team = parsed("lebron vs the hawks", ["lebron", "hawks"])
    assert team.asked != PLAYER_MEETINGS and team.who.player == "LeBron James" and team.cuts.opponent == "Atlanta Hawks"


def test_a_teams_period_stat_nothing_rebuilds_is_refused_and_a_rebuilt_one_is_not(con: duckdb.DuckDBPyConnection) -> None:
    """yardstick-v2 F065 "trailblazers stats last 10 games 3 point average
    1st quarter" is answered - the period's line rebuilds threes from the
    plays (the period relation's team half) - so only a stat neither the
    linescore nor that line holds is refused, by name."""
    refusal = unanswerable(
        con,
        TEAM_PERIOD_TOTAL,
        Reading(who=Who(kind="team", teams=("Portland Trail Blazers",)), measure=Measure(key="plusMinus", as_typed="plusMinus"), period=Period(number=1)),
        "blazers 1st quarter plus minus",
    )
    assert refusal is not None
    assert "linescore" in refusal.text and "'plusMinus'" in refusal.text
    for stat in ("points", "threePointFieldGoalsMade", "rebounds", "turnovers"):
        scope = Reading(who=Who(kind="team", teams=("Portland Trail Blazers",)), measure=measure_of(stat), period=Period(number=1))
        assert unanswerable(con, TEAM_PERIOD_TOTAL, scope, f"blazers 1st quarter {stat}") is None, stat


def test_a_period_used_as_a_condition_is_refused_by_name(con: duckdb.DuckDBPyConnection) -> None:
    """yardstick-v2 F062: "vj edgecombe three points made per game after
    making one three in first quarter" asks his whole-game threes over the
    games whose first quarter held one - read into the Reading's lines; a
    wording whose line cannot be read is kept off the period readers, and
    this names what a readable line looks like."""
    question = "vj edgecombe three points made per game after scoring a lot in the first quarter"
    result = unanswerable(con, None, Reading(who=Who(kind="player", players=("VJ Edgecombe",)), measure=Measure(key="threePointFieldGoalsMade", as_typed="threePointFieldGoalsMade")), question)
    assert result is not None and result.data["refused"] == "period_as_condition" and "no line could be read from it" in result.text
    # A condition the parser read is the relation's narrowing (#275), not this refusal.
    read = Reading(
        who=Who(kind="player", players=("VJ Edgecombe",)),
        lines=(Line(measure="threePointFieldGoalsMade", period=Period(number=1)),),
        measure=Measure(key="threePointFieldGoalsMade", as_typed="threePointFieldGoalsMade"),
    )
    assert unanswerable(con, PLAYER_LINE, read, "vj edgecombe three points made per game after making one three in first quarter") is None
    # A period that is the part measured is not this refusal.
    plain = "vj edgecombe 1st quarter assists by game"
    assert unanswerable(con, None, Reading(who=Who(kind="player", players=("VJ Edgecombe",)), measure=Measure(key="assists", as_typed="assists"), period=Period(number=1)), plain) is None


def test_bench_points_are_refused_as_a_gap_of_ours_not_missing_data(con: duckdb.DuckDBPyConnection) -> None:
    """yardstick-v2 F106: the box score flags starters, so bench points are
    derivable - the refusal says nothing reads them yet, never "no data"."""
    from association.query.parse import read_route

    # The recognizer reads the word "bench" beside the typed measure the WORDS
    # named; a reading built from the model's key alone names none.
    reading = read_route(names_of(con), "most opponent bench points allowed in the west at home by team this month", [], "points")
    assert [cause.kind for cause in reading.unsupported] == ["bench_points"]
    refusal = refusal_result(reading.unsupported[0])
    assert "not read yet" in refusal.text and "flags starters" in refusal.text
    assert (
        unanswerable(con, None, Reading(cuts=Cuts(venue="home"), measure=Measure(key="points", as_typed="points")), "most opponent bench points allowed in the west at home by team this month") is None
    )


def test_a_championship_question_is_refused_before_a_ranking_answers_it(con: duckdb.DuckDBPyConnection) -> None:
    """ "show which team won the nba championship for the past 10 years" is
    refused, never answered as a ranking of teams' records: titles are not
    on record as such. "Title odds" is a team's outlook and is left alone."""
    refusal = by_question("show which team won the nba championship for the past 10 years", TEAM_RANKING, con)
    assert refusal is not None and "not on record as such" in refusal.text
    assert refusal.data == {"refused": "championship", "message": refusal.text}
    assert by_question("what are the sixers title odds?", TEAM_OUTLOOK, con) is None
    assert by_question("best record from 2010-11 to 2018-19", TEAM_RANKING, con) is None


def test_a_teams_total_of_triple_doubles_is_refused_for_that_cause(con: duckdb.DuckDBPyConnection) -> None:
    result = unanswerable(
        con,
        PLAYER_RANKING,
        Reading(who=Who(kind="team", teams=("Los Angeles Lakers",)), measure=Measure(key="triple_double", as_typed="triple_double"), span=Span(career=True)),
        "los angeles lakers all-time triple doubles vs west",
    )
    assert result is not None and result.data["refused"] == "team_boolean_count" and "team's total" in result.text
    assert unanswerable(con, PLAYER_RANKING, Reading(measure=Measure(key="triple_double", as_typed="triple_double")), "who has the most triple doubles this season") is None


@pytest.mark.parametrize(
    ("question", "fact", "words"),
    [
        ("3rd nba overall draft picks", "draft", "3rd nba overall draft picks"),
        ("most points scored by a player drafted 58th in the nba draft", "draft", "drafted"),
        ("what pick was wlt chamberlain", "draft", "what pick"),
        ("nba mvps in 2010\u2019s", "award", "mvps"),
        ("1999 sixth man of the year", "award", "sixth man"),
        ("most all star appearances for a nets player", "award", "all star"),
        ("how many times has bam adebayo been selected to all-defensive team", "award", "all-defensive"),
        ("players with 5 or more nba all stars and 5 or more all nba teams since 2000", "award", "all stars"),
        ("Who was MVP in 2019-20?", "award", "MVP"),
        ("who won 6th man of the year in 2015", "award", "6th man"),
        ("did tracy mcgrady ever win most improved player", "award", "most improved player"),
        ("NBA players born on August 1", "birth", "born"),
        ("when did brandon clarke die", "death", "die"),
        ("NBA players with birth date 8/1", "birth", "birth date"),
        ("6 ft 6 inches nba players", "height", "6 ft 6 inches"),
        ("6'10 chicago bulls forwards 2010-2019", "height", "6'10"),
        ("How tall is Yao Ming?", "height", "How tall"),
        ("players that wear number 5 jersey active", "jersey", "wear number 5"),
        ("players with 78 jersey", "jersey", "jersey"),
        ("How many 10+ point leads did the Sacramento Kings have last season", "lead", "10+ point leads"),
        ("nba most 4 point plays", "four-point play", "4 point plays"),
        ("highest scoring 2nd options in the nba this year", "role", "2nd options"),
        ("luka doncic stats against 50 win teams in the 2026 season", "wins", "50 win teams"),
        ("field goal percentage last 5 minutes of a nba game", "clutch", "last 5 minutes"),
        ("lebron clutch stats", "clutch", "clutch"),
        ("Pelicans players defensive field goal percentage 2025-26", "defended shots", "defensive field goal percentage"),
        ("best dfg% in the league", "defended shots", "dfg%"),
        # #118: a betting line, never the plain record or log.
        ("Clippers ats record last 15 games at home", "betting line", "ats"),
        ("76ers versus chicago bulls spread last 10 games", "betting line", "spread"),
        ("Blazers ats as the underdog the last 9 road games", "betting line", "ats"),
        ("blazers as the underdog the last 9 road games", "betting line", "underdog"),
        ("portland trail blazers record against the spread 2024 season", "betting line", "against the spread"),
        ("wizard o/u record at away", "betting line", "o/u"),
        ("highest o/u for points", "betting line", "o/u"),
        ("did the lakers cover the point spread last night", "betting line", "point spread"),
    ],
)
def test_a_fact_no_table_holds_is_refused_by_name(con: duckdb.DuckDBPyConnection, question: str, fact: str, words: str) -> None:
    """#389: the fact is refused by name before any reader runs - never the
    parent grammar's default reading (the points leaders, a player's season
    line, a team's points per game) nor, for a birth date, a game on that
    day."""
    refusal = by_question(question, PLAYER_RANKING, con)
    assert refusal is not None and refusal.data == {"fact": fact, "words": words, "message": refusal.text}, refusal
    assert repr(words) in refusal.text


@pytest.mark.parametrize(
    ("question", "words"),
    [
        ("anthony edwards defensive rating 2025", "defensive rating"),
        ("defense rating lebron james", "defense rating"),
        ("shooting guards with best defensive rating in 2024", "defensive rating"),
        ("top 5 defensive rating in nba in 2026", "defensive rating"),
    ],
)
def test_a_players_rating_is_refused_by_name(con: duckdb.DuckDBPyConnection, question: str, words: str) -> None:
    """A player's defensive rating is no points line: the ratings are kept
    for teams alone, so the point reader refuses it by name - and a team's
    rating is read."""
    reading = read_route(names_of(con), question, [], "")
    assert isinstance(reading.point_verdict, Cause) and reading.point_verdict.facts == {"fact": "player rating", "words": words}
    team = read_route(names_of(con), "Lowest defensive rating by a team this season", [], "")
    assert not isinstance(team.point_verdict, Cause)


@pytest.mark.parametrize(
    "question",
    [
        "who leads the league in assists?",
        "tatum stats in the lead up to the playoffs",
        "knicks record in playoff games when Mitchell Robinson shoots 4 ft",
        "lebron 4 point games",
        "most points by a third year player",
        "who picks up the most fouls",
        "free throw percentage last 10 games",
        "demar derozan stats since the all star break this season",
        "all nba players with 30 ppg",
        "all defensive stats for jokic",
        "who is the most improved scorer this season",
        "what are the celtics playoff odds",
        "jokic stats last 5 games",
    ],
)
def test_words_beside_an_absent_fact_are_not_refused(con: duckdb.DuckDBPyConnection, question: str) -> None:
    """The words each absent fact is read by are taken only where they name
    it: "leads the league" is no lead, "4 ft" a shot's distance, "ft" a free
    throw, a pick only after "what", "which" or "overall"."""
    assert by_question(question, PLAYER_RANKING, con) is None


@pytest.mark.parametrize(
    ("question", "words"),
    [
        ("Name the top 10 players by games played under Lon Kruger in NBa", "under Lon Kruger"),
        ("spurs record under gregg popovich since 2010", "under gregg popovich"),
        ("lakers wins under phil jackson", "under phil jackson"),
    ],
)
def test_a_coach_named_by_under_is_refused_by_the_coach_cause(con: duckdb.DuckDBPyConnection, question: str, words: str) -> None:
    """#431: "under Lon Kruger" is a coach's tenure, refused by the coach
    cause naming the words, never the league's ranking."""
    refusal = by_question(question, PLAYER_RANKING, con)
    assert refusal is not None, question
    assert refusal.text.startswith(f"{words!r} reads as a coach's tenure. No table here holds a coach")


@pytest.mark.parametrize(
    "question",
    [
        "Sga games with under 14 fta in his whole career",
        "how many games in 2005-2006 did kobe score under 25 points",
        "active players under 25 years old with spg+bpg gte 2",
        "kobe bryant career games with under 40 field goal percentage",
        "games under five minutes played by maxey",
        "players under rookie contracts",
        "lakers record under the lights",
        "jokic under pressure",
        "hawks record under lakers",
    ],
)
def test_under_before_a_number_or_no_name_is_no_coach(con: duckdb.DuckDBPyConnection, question: str) -> None:
    """#431: "under" before a number is a line or an age, and before
    words that are no two-word name (a stopword, a team, one word) no coach."""
    assert by_question(question, PLAYER_RANKING, con) is None
