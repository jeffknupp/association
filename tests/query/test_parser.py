"""The parser (query/parse.py): a question read into one Reading. These are
the rules' own cases on the small fixture warehouse."""

from __future__ import annotations

from dataclasses import dataclass

import duckdb
import pytest
from routed import Routed, reading_of, with_subject
from routed import staged as settle

from association.query.compose.plan import aggregate_of, plan_point
from association.query.compose.say import refusal_phrase
from association.query.decisions import Decision
from association.query.entities import names_of
from association.query.line import threshold_named as _threshold_from_text
from association.query.lines import threshold_of
from association.query.measure import named
from association.query.parse import classify_span, grammar_text, read_route
from association.query.parse import parent_asked as _parent_asked
from association.query.reading import (
    GAMES_COUNTED,
    LINE_RECORD,
    NETPOINTS_FINGERPRINT,
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
    TEAM_MEETINGS,
    TEAM_OUTLOOK,
    TEAM_PERIOD_TOTAL,
    TEAM_RANKING,
    TEAM_RECORD,
    Companion,
    PointShape,
    Reading,
    Unsupported,
)
from association.query.reading import Subject as Who
from association.query.result import Refusal
from association.query.subject import Subject


def _measure(question: str) -> str | None:
    """The measure the words name, as its spelling (the measure tagger's grammar, ``measure.named``)."""
    found = named(question)
    return found[0] if found is not None else None


@pytest.fixture
def con() -> duckdb.DuckDBPyConnection:
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    c.executemany(
        "INSERT INTO players VALUES (?, ?)",
        [("1", "Joel Embiid"), ("2", "Nikola Jokic"), ("3", "Nikola Jovic"), ("4", "Tyrese Maxey"), ("5", "Luka Doncic"), ("6", "Shai Gilgeous-Alexander"), ("7", "Jayson Tatum")],
    )
    c.execute("CREATE TABLE teams (team_id VARCHAR, display_name VARCHAR, abbreviation VARCHAR)")
    c.executemany("INSERT INTO teams VALUES (?, ?, ?)", [("1", "Boston Celtics", "BOS"), ("2", "Philadelphia 76ers", "PHI"), ("3", "Los Angeles Lakers", "LAL"), ("4", "Orlando Magic", "ORL")])
    return c


def test_a_span_is_a_team_a_player_or_nothing(con: duckdb.DuckDBPyConnection) -> None:
    """The names the normalizer copies are classified before they become a
    subject (#236): a division, the word "team" and a typo two
    players could be are nobody's; a single near spelling is that player
    (the typo policy: the index defaults, visibly, never the model)."""
    assert classify_span(names_of(con), "sixers") == "team" and classify_span(names_of(con), "Boston Celtics") == "team" and classify_span(names_of(con), "PHI") == "team"
    assert classify_span(names_of(con), "Embiid") == "player" and classify_span(names_of(con), "maxey") == "player"
    assert classify_span(names_of(con), "Embid") == "player" and classify_span(names_of(con), "embids") == "player"
    assert classify_span(names_of(con), "jolic") is None  # Jokic or Jovic: two near spellings ask
    assert classify_span(names_of(con), "southeast division") is None and classify_span(names_of(con), "team") is None


def test_the_measure_grammar_reads_the_words_before_the_models_key() -> None:
    assert _measure("who were the top 10 in defensive netpoints / 100 possessions") == "netpoints_defense_per_100"
    assert _measure("who led the league in offensive netpoints?") == "netpoints_offense"
    assert _measure("who are the top 50 in total adjusted netpoints") == "netpoints_per_100"
    assert _measure("what were SGA's netpoint stats this season") == "netpoints"
    assert _measure("KNICKS point differential over the last 7 games") == "points_differential"
    assert _measure("show me lebron's 2pt percentage for the past 5 years") == "twoPointFieldGoalPct"
    assert _measure("Top 5 scorers on the Lakers?") == "points"
    assert _measure("who had the highest avg rebounds") == "rebounds"
    assert _measure("what did Nikola Jokic do in his last game?") is None
    # A three is its own column, never the "point" in it.
    assert _measure("davion mitchell 3 point stats") == "threePointFieldGoalsMade"
    assert _measure("vj edgecombe three points made per game") == "threePointFieldGoalsMade"
    assert _measure("Trailblazers 3-point average in the first quarter") == "threePointFieldGoalsMade"
    assert _measure("klay thompson 3 point attempts per game") == "threePointFieldGoalsAttempted"
    assert _measure("show me lebron's 3pt percentage for the past 5 years") == "threePointFieldGoalPct"
    # What a team gives up is the opponent's line.
    assert _measure("rebounds allowed per team") == "rebounds allowed"
    assert _measure("which team allowed the most points per game") == "points allowed"
    assert _measure("derozan's total points against the knicks") == "points"
    # "to" is a word before it is a turnover count.
    assert _measure("25-26 Knicks playoff stats compared to other historical teams") is None
    assert _measure("who averages the most TO per game") == "turnovers"


def test_the_parent_grammar_by_kind_and_words() -> None:
    assert parent_asked("show sga fingerprint for this season", "player") == NETPOINTS_FINGERPRINT
    # A player's own record is his games' W-L (F088, "Embiid's record against Boston this year"): his splits, never
    # the team's with/without split; a line in it is a record over a line, which the grammar's rules assign.
    assert parent_asked("Embiid's record against Boston this year", "player") == PLAYER_SPLITS
    assert parent_asked("Sga record 36 plus points", "player") == PLAYER_LINE
    assert parent_asked("36-plus points SGA record", "player") == PLAYER_LINE
    assert parent_asked("In his most recent game, what did Nikola Jokic accomplish?", "player") == PLAYER_LOG
    assert parent_asked("Last season's threes by Plot Curry", "everyone") == SHOT_CHART
    assert parent_asked("25-26 Knicks playoff stats compared to other historical teams", "team") == TEAM_OUTLOOK
    # A team's triple-doubles are its players'.
    assert parent_asked("oklahoma city thunder all-time triple doubles vs west", "team") == PLAYER_RANKING
    assert parent_asked("how many points does embiid average", "player") == PLAYER_LINE
    assert parent_asked("what did Nikola Jokic do in his last 5 games?", "player") == PLAYER_LOG
    assert parent_asked("lebron vs kawhi 2015", "pair") == PLAYER_MEETINGS
    assert parent_asked("evaluate sga against embiid", "pair") == PLAYER_COMPARISON
    assert parent_asked("Lakers vs Celtics record this season", "teams") == TEAM_MEETINGS
    assert parent_asked("What was the Lakers record last season?", "team") == TEAM_RECORD
    assert parent_asked("Display the Knicks' previous 5 games", "team") == PLAYER_LOG
    # The with/without split needs a companion the reading found: "when playing away" names nobody.
    assert parent_asked("Record of the 76ers when playing away", "team") == TEAM_RECORD
    assert parent_asked("PHI's record when Embiid and Paul George are both in the game", "team", companions=True) == PRESENCE_SPLIT
    assert parent_asked("westbrook's stats when he started for the kings", "player") == PLAYER_LINE
    assert parent_asked("Best record from 2010-11 to 2018-19 nba", "everyone") == TEAM_RANKING
    assert parent_asked("who led the league in assists in 2019", "everyone") == PLAYER_RANKING
    assert parent_asked("show sixers first quarter scoring for their last 10 games", "team") == TEAM_PERIOD_TOTAL


@dataclass(frozen=True)
class _Read(Routed):
    """The parser's reading as the route-level checks below assert on it,
    with the Reading it is read off."""

    reading: Reading | None = None

    @property
    def who(self) -> Subject:
        """Who the question was read to be about - every Reading the parser
        writes carries its subject."""
        assert self.subject is not None
        return self.subject


def _route(con: duckdb.DuckDBPyConnection, question: str, names: list[str] | None = None, stat: str = "") -> _Read:
    """The parser's reading of ``question`` (:func:`read_route`), as the
    route-level checks below assert on it: the key, the typed filters, the
    subject, the parser's own decisions - read off the Reading itself."""
    reading = read_route(names_of(con), question, names, stat)
    parser = tuple(decision for decision in reading.decisions if decision.stage == "parser")
    return _Read(reading.asked, reading, reading.subject, parser, reading.claims, reading=reading)


def parent_asked(question: str, kind: str, companions: bool = False) -> PointShape | None:
    """The parent grammar over ``question`` as ``read_route`` hands it: the
    windows of games and the dated ranges typed (``grammar_text``)."""
    return _parent_asked(grammar_text(question), kind, companions)


def _read(con: duckdb.DuckDBPyConnection, question: str, names: list[str] | None = None, stat: str = "") -> Reading:
    """The parser's whole path, as the agent takes it (:func:`read_route`)."""
    return read_route(names_of(con), question, names, stat)


def _quarter(read: Routed | Reading) -> int | None:
    """The quarter the reading sees, where it sees one (``Reading.period``, not a half)."""
    return read.period.number if read.period is not None and not read.period.half else None


def _half(read: Routed | Reading) -> int | None:
    """The half the reading sees, where it sees one (``Reading.period``, a half)."""
    return read.period.number if read.period is not None and read.period.half else None


def _threshold(read: Routed | Reading) -> float | None:
    """The shape's own line on the whole game (the keyed ``Line`` with no period)."""
    return next((line.value for line in read.lines if line.keyed and line.period is None), None)


def test_the_parser_reads_a_question_into_a_reading(con: duckdb.DuckDBPyConnection) -> None:
    """The whole path on the fixture: names classified, the kind and parent
    read, the window and the measure from the words, the child assigned."""
    r = _read(con, "How many 30+ point games did Jokic have this season?", names=["Jokic"], stat="points")
    assert r.asked == GAMES_COUNTED and r.subject is not None and r.subject.kind == "player" and r.subject.players == ("Nikola Jokic",)
    assert threshold_of(r) == 30 and r.measure is not None and r.measure.as_typed == "points"
    r = _read(con, "Lakers vs Celtics record this season", names=["Lakers", "Celtics"], stat="")
    assert r.asked == TEAM_MEETINGS and r.subject is not None and r.subject.kind == "teams"
    # A window over the two teams' meetings is still their meetings when a record is asked for; a log word is one team's games.
    r = _read(con, "lakers vs celtics record last 10 home games played", names=["lakers", "celtics"], stat="")
    assert r.asked == TEAM_MEETINGS and r.subject is not None and r.subject.kind == "teams"
    r = _read(con, "lakers game log vs celtics last 10", names=["lakers", "celtics"], stat="")
    assert r.subject is not None and r.subject.kind == "team"
    r = _read(con, "who were the top 10 in defensive netpoints / 100 possessions", names=[], stat="")
    assert r.subject is not None and r.subject.kind == "everyone" and r.measure is not None and r.measure.as_typed == "netpoints_defense_per_100" and r.window.count == 10
    # A span no player or team has never becomes a subject.
    r = _read(con, "alperen sengun double-doubles vs southeast division career away", names=["alperen sengun", "southeast division"], stat="")
    assert r.subject is not None and r.subject.kind != "pair"


def test_the_parser_with_no_names_and_no_stat_still_reads_the_question(con: duckdb.DuckDBPyConnection) -> None:
    r = _read(con, "what was the sixers record when maxey scored 20+ points?")
    assert r.subject is not None and r.subject.kind == "team" and r.subject.teams == ("Philadelphia 76ers",)
    assert r.asked == LINE_RECORD
    assert threshold_of(r) == 20


def test_a_player_after_a_versus_word_reaches_the_route_as_a_condition(con: duckdb.DuckDBPyConnection) -> None:
    """The Reading carries the other-side player as a typed companion
    (``side="opponent"``, read by the subject reading's
    ``_versus_companions`` with the model's names, so he is kept even where
    his name alone is two players') - and the point is the subject's own: a
    high (rows by measure, "most" against a named opponent being his best
    meeting), a count, a log. A bare pair is two players' meetings, and "vs
    <team> without <player>" is the player's own question, not a
    with/without split."""
    against = Companion(player="Jayson Tatum", side="opponent", predicate="played")
    high = _read(con, "most points by tyrese maxey vs tatum", ["tyrese maxey", "tatum"], "points")
    assert high.asked == PLAYER_LINE and high.who.player == "Tyrese Maxey" and high.companions == (against,)
    assert high.point is not None and high.point.read_as.shape == "rows" and high.point.order == "measure" and high.point.direction == "desc"
    fewest = _read(con, "fewest points by tyrese maxey vs tatum", ["tyrese maxey", "tatum"], "points")
    assert fewest.point is not None and fewest.point.order == "measure" and fewest.point.direction == "asc"
    count = _read(con, "how many times did tyrese maxey score 30 vs tatum", ["tyrese maxey", "tatum"], "points")
    assert count.asked == GAMES_COUNTED and count.companions == (against,) and threshold_of(count) == 30
    log = _read(con, "tyrese maxey game log vs tatum", ["tyrese maxey", "tatum"], "")
    assert log.asked == PLAYER_LOG and log.companions == (against,) and len(log.who.players) < 2
    pair = _read(con, "tyrese maxey vs tatum", ["tyrese maxey", "tatum"], "")
    assert pair.asked == PLAYER_MEETINGS and pair.companions == () and len(pair.who.players) == 2
    narrowed = _read(con, "tyrese maxey points vs boston without embiid", ["tyrese maxey", "boston", "embiid"], "points")
    assert narrowed.asked == PLAYER_LINE and narrowed.cuts.opponent == "Boston Celtics" and narrowed.companions == (Companion(player="Joel Embiid", predicate="absent"),)


def test_a_player_against_a_team_is_never_a_matchup(con: duckdb.DuckDBPyConnection) -> None:
    """One player against a team is his own games, never two players'
    meetings - whether the model hands the team, a garbled spelling of it,
    or only a teammate named as absent beside him. Two players' meetings
    are read only where the question names two players (the pair kind)."""
    for question, names in (
        ("maxey vs celtics without embiid", ["maxey", "celtics", "embiid"]),
        ("maxey vs celtics without embiid", ["maxey", "Celtcs", "embiid"]),
        ("maxey vs celtics without embiid", ["maxey", "Joel Embiid"]),
        ("tyrese maxey v bos", ["tyrese maxey", "bos"]),
    ):
        r = _read(con, question, names)
        assert r.asked != PLAYER_MEETINGS and r.subject is not None and r.subject.kind == "player", (question, names, r.asked)
        assert r.who.player == "Tyrese Maxey" and r.cuts.opponent == "Boston Celtics", (question, names, r)
    r = _read(con, "maxey vs embiid", ["maxey", "embiid"])
    assert (r.asked, r.subject.kind if r.subject else None) == (PLAYER_MEETINGS, "pair")


def test_read_route_takes_the_names_from_the_subject_reading(con: duckdb.DuckDBPyConnection) -> None:
    """The model's spans are where the reading starts, not what the subject
    holds: a name it dropped that the question holds is the reading's; a
    companion is a narrowing, never a second player; a team beside a player
    is his opponent; a team code is the team; an ambiguous span the reading
    could not settle is kept as typed, for the answer side to ask about."""
    dropped = _route(con, "plot the fingerprint for Nikola Jokic for the 2025 season", [], "")
    assert dropped.who.player == "Nikola Jokic"
    companion = _route(con, "Tyrese Maxey game log without Joel Embiid", ["Tyrese Maxey", "Joel Embiid"], "")
    assert companion.who.players == ("Tyrese Maxey",) and companion.who.companions
    against = _route(con, "show maxey's games against boston", ["maxey", "boston"], "")
    assert against.cuts.opponent == "Boston Celtics" and against.who.teams == ()
    code = _route(con, "PHI record 2026", ["PHI"], "")
    assert (code.asked, code.who.team, code.who.player) == (TEAM_RECORD, "Philadelphia 76ers", None)
    kept = _route(con, "who is better, tatum or nikola", ["tatum", "nikola"], "")
    assert kept.who.players == ("Jayson Tatum", "nikola")
    # The model's names were only the companions: they narrow, and are
    # nobody's subject.
    only = _route(con, "game log this season without Joel Embiid and Tyrese Maxey", ["Joel Embiid", "Tyrese Maxey"], "")
    assert only.who.companions and only.who.players == ()


def test_a_conference_or_a_pronoun_is_never_a_name(con: duckdb.DuckDBPyConnection) -> None:
    """ "vs west" is the conference, though four Wests played; "someone" is one
    near spelling from Simone Fontecchio, and a single near spelling defaults -
    the fuzzy-word trap AGENTS.md records, arriving through the model's spans."""
    con.execute("INSERT INTO players VALUES ('8', 'Delonte West'), ('9', 'David West'), ('10', 'Simone Fontecchio')")
    assert classify_span(names_of(con), "west") is None and classify_span(names_of(con), "the West") is None and classify_span(names_of(con), "someone") is None
    assert classify_span(names_of(con), "Delonte West") == "player"


def test_read_route_reads_the_window_before_the_span_and_the_quarter_from_the_words(con: duckdb.DuckDBPyConnection) -> None:
    """A bare "last 10 games" reads both season types, which the span's
    reading decides only beside the window (``point.span_under``); a team's
    quarter is read from the words."""
    recent = _route(con, "How did the Celtics do in their last 10 games?", ["Celtics"], "")
    assert (recent.asked, recent.window.count, recent.span.both) == (PLAYER_LOG, 10, True)
    quarter = _route(con, "show sixers first quarter scoring for their last 10 games", ["sixers"], "points")
    assert (quarter.asked, _quarter(quarter)) == (TEAM_PERIOD_TOTAL, 1)


def test_a_players_record_reads_his_splits_through_a_window_but_not_over_a_log_word() -> None:
    """ "rec" is a record, and a record narrowed by a date or a window is still
    his record (yardstick-v2 F110, "towns home rec including playoffs since
    1/26/20 vs spurs" is no game log); a game log asked for is a log."""
    assert parent_asked("towns home rec including playoffs since 1/26/20 vs spurs", "player") == PLAYER_SPLITS
    assert parent_asked("embiid's record in his last 10 games", "player") == PLAYER_SPLITS
    assert parent_asked("embiid game log since 1/26/20", "player") == PLAYER_LOG
    assert parent_asked("embiid's game log and record vs boston since 1/26/20", "player") == PLAYER_LOG
    assert parent_asked("Sga record 36 plus points", "player") != PLAYER_SPLITS


def test_the_hold_out_wordings_read_their_measure_and_their_parent(con: duckdb.DuckDBPyConnection) -> None:
    """Hold-out wordings the reader was not tuned on, each read to the
    measure or the parent its words ask."""
    # "%" takes no \b: "3pt %" is the percentage, not 3-pointers made.
    assert _measure("who had the highest 3pt % this season") == "threePointFieldGoalPct"
    # Attempts AND makes asked for: the made line reports both.
    assert _measure("show embiid's 3pt attempts and 3pts mad for his career") == "threePointFieldGoalsMade"
    assert _measure("embiid 3pt attempts per game") == "threePointFieldGoalsAttempted"
    # A team's opener is one game of its log, not its season line.
    assert parent_asked("Lakers opening game of the season", "team") == PLAYER_LOG
    # A team's odds are its outlook, not its postseason stats.
    assert parent_asked("what are the sixers playoff odds?", "team") == TEAM_OUTLOOK
    # A record with nobody named is the teams', not the league's scorers.
    assert parent_asked("worst record 2025-26", "everyone") == TEAM_RANKING
    assert parent_asked("who scored the most points this season", "everyone") == PLAYER_RANKING


def test_read_route_reads_the_columns_a_ranking_asks_to_see(con: duckdb.DuckDBPyConnection) -> None:
    """ "with their rebounds and assists" and "the team they play for"
    (yardstick-v2 F017) are the ranking's columns beside its measure
    (``Measure.beside``), read from the words; a ranking that asks for none
    gets none."""
    extra = _route(con, "Top 5 scorers with their rebounds and assists", [], "points")
    assert (extra.asked, extra.measure.beside, extra.window.count) == (PLAYER_RANKING, ("rebounds", "assists"), 5)
    team = _route(con, "show the top 50 in total adjusted netpoints and the team they play for", [], "netpoints")
    assert team.measure.beside == ("team",)
    current = _route(con, "who are the top 50 players in total adjusted netpoints while playing for their current team", [], "netpoints")
    assert current.measure.beside == ("team",)
    plain = _route(con, "who led the league in scoring", [], "points")
    assert plain.measure is not None and plain.measure.beside == ()


def test_a_name_the_model_corrected_is_put_back_as_the_question_typed_it(con: duckdb.DuckDBPyConnection) -> None:
    """The model sometimes corrects a name ("embid" handed back as
    "embiid"). A typo is the entity index's to read, visibly, so the
    question's own spelling goes back in; a completion of a surname typed
    as-is is left alone."""
    typo = _route(con, "how many points does embid average", ["embiid"], "points")
    assert typo.who.player == "embid"
    both = _route(con, "how many points does embidd average", ["Joel Embiid"], "points")
    assert both.who.player == "embidd"
    completed = _route(con, "how many points does embiid average", ["Joel Embiid"], "points")
    assert completed.who.player in ("Joel Embiid", "embiid")
    # A possessive "s" left on a typo ("embids" for "embid's") is the
    # question's own spelling too, as the index reads it: put back, it is a
    # player's history, not a name the question never held (#180).
    possessive = _route(con, "show me embids 3pt percentage over the last 5 years", ["embiid"], "threePointFieldGoalPct")
    assert (possessive.asked, possessive.who.player, possessive.who.invented) == (SEASON_HISTORY, "embids", ())


def test_an_invented_name_never_changes_the_kind_or_the_key(con: duckdb.DuckDBPyConnection) -> None:
    """#373: a model name the question never held rides on
    ``misread`` alone - never among the players, so the subject's kind and
    the grammar's key are the question's own - and is replaced from the
    question's names where the count is exact (AGENTS.md, "Replace only
    from what the question itself names"), or reported where it is not."""
    from association.query.reading import PERIOD_LOG, PLAYER_LINE, PLAYER_LOG

    quarter = _route(con, "points per quarter for Luka", ["Jayson Tatum"], "points")
    assert (quarter.asked, quarter.who.kind, quarter.who.players, quarter.misread) == (PERIOD_LOG, "player", ("Luka Doncic",), ())
    log = _route(con, "jokic game log this season", ["Jayson Tatum"], "points")
    assert (log.asked, log.who.players, log.misread) == (PLAYER_LOG, ("Nikola Jokic",), ())
    # Two model names for one question name: one is fiction with no spare,
    # and the answering loop refuses it by name on a player's shape.
    extra = _route(con, "tatum stats", ["Jayson Tatum", "Joel Embiid"], "points")
    assert (extra.asked, extra.who.players, extra.misread) == (PLAYER_LINE, ("Jayson Tatum",), ("Joel Embiid",))
    # No name in the question at all: the model's span is nobody the words
    # hold, so it is reported, and the answering loop refuses it by name.
    nobody = _route(con, "game log this season", ["Jayson Tatum"], "points")
    assert nobody.misread == ("Jayson Tatum",) and "Jayson Tatum" not in nobody.who.players[1:]


def test_a_companions_line_is_never_the_subjects(con: duckdb.DuckDBPyConnection) -> None:
    """#359: the one line in "maxey record when embiid scores 20+
    points" is Embiid's, so the record has no line of its own and refuses by
    a cause naming his line and the wordings that reach each reading; the
    count whose "companion" the rules restored as the subject himself keeps
    its line; a team's record keyed on its player's line keeps it."""
    from association.query.reading import GAMES_COUNTED, LINE_RECORD

    record = _route(con, "maxey record when embiid scores 20+ points", ["Tyrese Maxey", "Joel Embiid"], "points")
    assert (record.asked, record.lines) == (LINE_RECORD, ()) and record.who.player in ("Maxey", "Tyrese Maxey")
    assert [(c.player, c.predicate, c.line.as_typed if c.line else None) for c in record.companions] == [("Joel Embiid", "reached", "20+ points")]
    assert record.point_refusal is not None and record.point_refusal.kind == "line_is_a_companions"
    assert record.point_refusal.facts["companion"] == "Joel Embiid" and record.point_refusal.facts["line"] == "20+ points"
    count = _route(con, "In his 18th season, how many games with 40+ points did Lebron James have?", ["Lebron James"], "points")
    assert (count.asked, [line.value for line in count.lines], count.companions) == (GAMES_COUNTED, [40], ())
    team = _route(con, "sixers record when maxey scored 20+ points", ["Tyrese Maxey"], "points")
    assert (team.asked, team.who.kind, [line.value for line in team.lines]) == (LINE_RECORD, "team", [20])


def test_a_count_spelled_out_is_the_count(con: duckdb.DuckDBPyConnection) -> None:
    """One number table, every count pattern built from it: "last twelve
    games" reads the log as "last 12 games" does."""
    for spelled, n in (("twelve", 12), ("eleven", 11), ("twenty five", 25), ("twenty-five", 25), ("fifty", 50)):
        route = _route(con, f"tyrese maxey last {spelled} games", ["tyrese maxey"], "")
        assert (route.asked, route.window.order, route.window.count) == (PLAYER_LOG, "recent", n), spelled


# ---------------------------------------------------------------------------
# A companion beside the subject, with the role the question gives him. The
# Reading is what these assert - the key the words ask and the typed filters
# - never an answer.


def _with_the_warriors(con: duckdb.DuckDBPyConnection) -> None:
    con.executemany("INSERT INTO players VALUES (?, ?)", [("20", "Stephen Curry"), ("21", "Draymond Green")])
    con.execute("INSERT INTO teams VALUES ('9', 'Golden State Warriors', 'GS')")


def test_a_teammates_start_is_his_condition_on_the_subjects_own_key(con: duckdb.DuckDBPyConnection) -> None:
    """ "maxey points when embiid starts" and "maxey points in games embiid
    started": a teammate's start or bench is his companion's role, on the
    subject's own key - never the subject's starter split, never a second
    subject."""
    started = (Companion(player="Joel Embiid", predicate="started"),)
    for question, stat, asked in (
        ("maxey points when embiid starts", "points", PLAYER_LINE),
        ("how many points does maxey average when embiid starts", "points", PLAYER_LINE),
        ("maxey points when embiid started", "points", PLAYER_LINE),
        ("maxey points in games embiid started", "points", PLAYER_LINE),
        ("maxey game log when embiid starts", "", PLAYER_LOG),
        ("maxey game log in games embiid started", "", PLAYER_LOG),
    ):
        route = _route(con, question, ["maxey", "embiid"], stat)
        assert (route.asked, route.who.kind, route.who.players) == (asked, "player", ("Tyrese Maxey",)), question
        assert route.split is None and route.companions == started, question
    route = _route(con, "maxey stats when embiid comes off the bench", ["maxey", "embiid"], "")
    assert route.asked == PLAYER_LINE and route.split is None
    assert route.companions == (Companion(player="Joel Embiid", predicate="bench"),)
    # A child the words assign reads it too: the compiler answers a count first and narrows by every condition.
    counted = _read(con, "how many 30 point games did maxey have when embiid started", ["maxey", "embiid"], "points")
    assert (counted.asked, _threshold(counted), counted.companions) == (GAMES_COUNTED, 30, started)


def test_a_teammates_start_narrows_a_shot_chart_or_a_distance_not_the_players_own_starts(con: duckdb.DuckDBPyConnection) -> None:
    """ "stephen curry shot chart when draymond green starts" and "...
    average shot distance when draymond green starts" narrow to Green's
    starts, not Curry's own."""
    _with_the_warriors(con)
    for question, asked in (("stephen curry shot chart when draymond green starts", SHOT_CHART), ("stephen curry average shot distance when draymond green starts", SHOT_DISTANCE)):
        route = _route(con, question, ["stephen curry", "draymond green"], "")
        assert route.asked == asked and route.split is None, question
        assert route.companions == (Companion(player="Draymond Green", predicate="started"),), question


def test_the_subjects_own_split_still_reads_beside_a_teammates_role(con: duckdb.DuckDBPyConnection) -> None:
    """Only the teammate's phrase is his: the subject's own start still
    reads as his split, a team's split by a teammate's start is the
    presence split's own side, and a start the question denies stays the
    with/without split, whose other half is the one asked for - as a
    condition it would narrow to the very games the question excludes."""
    route = _route(con, "maxey points as a starter when embiid comes off the bench", ["maxey", "embiid"], "points")
    assert (route.asked, route.split) == (PLAYER_LINE, "starter")
    route = _route(con, "maxey points when he starts", ["maxey"], "points")
    assert (route.asked, route.split) == (PLAYER_LINE, "starter")
    route = _route(con, "76ers record when embiid starts", ["76ers", "embiid"], "")
    assert route.asked == PRESENCE_SPLIT and route.split is None
    route = _route(con, "maxey points when embiid doesn't start", ["maxey", "embiid"], "points")
    assert route.asked == PRESENCE_SPLIT
    # Where nothing reads the start as a condition, the condition is still
    # written - the reader does not ask what will answer - and the planner
    # refuses it by name, never answering every game.
    question = "sixers first quarter points when embiid starts"
    route = _route(con, question, ["sixers", "embiid"], "points")
    assert route.asked == TEAM_PERIOD_TOTAL and route.split is None
    reading = route.reading
    assert reading is not None
    assert [c.predicate for c in reading.companions] == ["started"]
    planned = plan_point(reading)
    assert isinstance(planned, Unsupported) and "games Joel Embiid started" in str(planned), planned


def test_a_companion_who_sat_out_is_without_and_out_is_no_part_of_his_name(con: duckdb.DuckDBPyConnection) -> None:
    """ "with draymond green out" is his absence, the way "without X" is -
    never dropped, and never a teammate who PLAYED named "draymond green
    out" (Bo or Travis Outlaw)."""
    _with_the_warriors(con)
    for question, names, asked in (
        ("stephen curry shot chart with draymond green out", ["stephen curry", "draymond green"], SHOT_CHART),
        ("stephen curry game log with draymond green out", ["stephen curry", "draymond green"], PLAYER_LOG),
        ("stephen curry stats with draymond green out", ["stephen curry", "draymond green"], PRESENCE_SPLIT),
        ("warriors record with draymond green out", ["warriors", "draymond green"], PRESENCE_SPLIT),
    ):
        reading = _read(con, question, names, "")
        assert (reading.asked, reading.companions) == (asked, (Companion(player="Draymond Green", predicate="absent"),)), question
    reading = _read(con, "maxey points when embiid doesn't play", ["maxey", "embiid"], "points")
    assert (reading.asked, reading.companions) == (PRESENCE_SPLIT, (Companion(player="Joel Embiid", predicate="absent"),))
    # And on a log he is absent, never a teammate who played beside the absence.
    reading = _read(con, "maxey game log when embiid doesn't play", ["maxey", "embiid"])
    assert (reading.asked, reading.companions) == (PLAYER_LOG, (Companion(player="Joel Embiid", predicate="absent"),))
    # Never a pair: "in games X missed" is X's absence from the subject's games.
    route = _route(con, "maxey points in games embiid missed", ["maxey", "embiid"], "points")
    assert (route.asked, route.who.kind, [c.player for c in route.who.conditions if c.absent]) == (PLAYER_LINE, "player", ["Joel Embiid"])


def test_a_record_with_a_teammate_out_is_the_split_from_the_other_side() -> None:
    """The grammar's rules the parser runs through ``settle``, under the keys
    a record with no line arrives as: "with X out" is the with/without split
    by his absence - never a teammate who played, named "draymond green
    out" or "draymond green" with the absence dropped."""
    warriors = Who(kind="team", teams=("Golden State Warriors",))
    for asked in (LINE_RECORD, TEAM_RECORD):
        route = settle(asked, "warriors record with draymond green out", (Companion(player="draymond green", predicate="absent"),), who=warriors)
        # The rules settle the split and write no name: the subject reading writes him onto the Reading (apply_subject).
        assert (route.asked, route.companions) == (PRESENCE_SPLIT, ()), asked
        # Who sat out is the subject reading's: with nobody beside the team, the record stays a record.
        assert settle(asked, "warriors record with draymond green out", who=warriors).asked == asked


def test_a_companion_who_played_is_a_condition_off_the_presence_split(con: duckdb.DuckDBPyConnection) -> None:
    """ "stephen curry shot chart when draymond green plays": on a log or a
    chart a teammate who played is the relation's own ``played`` companion,
    and the with/without split reads the same companion as its side."""
    _with_the_warriors(con)
    names = ["stephen curry", "draymond green"]
    played = (Companion(player="Draymond Green"),)
    for question, asked in (("stephen curry shot chart when draymond green plays", SHOT_CHART), ("stephen curry game log when draymond green plays", PLAYER_LOG)):
        reading = _read(con, question, names)
        assert (reading.asked, reading.companions) == (asked, played), question
    split = _read(con, "stephen curry stats with draymond green playing", names)
    assert (split.asked, split.companions) == (PRESENCE_SPLIT, played)


def test_a_number_after_a_scoring_verb_is_a_line_on_points(con: duckdb.DuckDBPyConnection) -> None:
    """ "celtics record when jayson tatum scores 30" reads a threshold though
    no stat word follows the number: the verb names the stat."""
    for question, stat, since in (
        ("celtics record when jayson tatum scores 30", "points", None),
        ("celtics record when jayson tatum scores 30 since 2022", "points", 2022),
        ("celtics record when jayson tatum scored 30", "", None),
    ):
        route = _route(con, question, ["celtics", "jayson tatum"], stat)
        assert (route.asked, route.measure.as_typed, _threshold(route), route.span.since) == (LINE_RECORD, "points", 30, since), question
        assert route.who.kind == "team" and route.who.teams == ("Boston Celtics",), question
    route = _route(con, "how many times has embiid scored 40", ["embiid"], "points")
    assert (route.asked, _threshold(route)) == (GAMES_COUNTED, 40)
    # A companion's line reads the same way: "when embiid scores 30" is a 30-point condition.
    reached = _read(con, "maxey points when embiid scores 30", ["maxey", "embiid"], "points").companions
    assert [(c.player, c.side, c.predicate, c.line.measure if c.line is not None else None, c.line.value if c.line is not None else None) for c in reached] == [
        ("Joel Embiid", "own", "reached", "points", 30)
    ]
    # Not a line: a stat word after the number is that stat's, a rate is an average, a year is a year.
    assert _threshold_from_text("when he scored 12 rebounds") == 12 and _threshold_from_text("tatum scored 3 threes") == 3
    for question in ("players who score 30 a game", "who scored 30 per game", "since he scored 2022", "scored 30.5 on average"):
        assert _threshold_from_text(question) is None, question


# ---------------------------------------------------------------------------
# The key the parser's words assign, as decisions (#258): the answer's
# decisions show that "how many 30+ point games" was read as a count rather
# than a game log, beside who the question is about.


def test_each_move_of_the_key_off_the_grammars_parent_is_a_decision(con: duckdb.DuckDBPyConnection) -> None:
    """The ways the key leaves the parent the grammar named, each with its
    own reason: the words naming a child for the subject's kind, the
    grammar's rules settling another key, a team's record in the games a
    companion reached a line, and a child the rules declined. A question
    whose key is the parent's carries none."""
    route = _route(con, "How many times did embiid score 30+ points", ["embiid"], "points")
    assert route.decisions == (Decision("parser", "asked", "player_games/rows/date", "player_games/scalar/count", "the words 'How many times did embiid score 30+' name player_games/scalar/count"),)
    route = _route(con, "rebounds allowed per team", [], "rebounds")
    assert route.decisions == (Decision("parser", "asked", "player_seasons/ranking/player", "team_seasons/ranking/team", "the grammar's rules settle it from the question's words"),)
    route = _route(con, "what was the sixers record when maxey scored 15+ points?", ["sixers", "maxey"], "points")
    team_rule = "a team's record in the games a player named beside it reached a line"
    assert route.decisions == (Decision("parser", "asked", "team_games/split/presence", "player_games/split/line", team_rule),) and route.who.asked_reason == team_rule
    route = _route(con, "how many points does embiid average", ["embiid"], "points")
    assert (route.asked, route.decisions, route.who.asked_reason) == (PLAYER_LINE, (), None)
    # A child the words name and the rules decline: said, and the parent
    # stands (a history with no stat named is the whole line).
    route = _route(con, "embiid career averages over the past 4 seasons", ["embiid"], "")
    declined = "the words 'over the past 4 seasons' name player_seasons/split/season, and the grammar's rules declined it"
    assert route.asked == PLAYER_LINE and route.decisions == (Decision("parser", "asked", "player_seasons/split/season", "player_seasons/scalar/line", declined),)


def test_the_reading_carries_the_parsers_decisions_between_who_and_what_it_wrote(con: duckdb.DuckDBPyConnection) -> None:
    """The Reading's decisions read in order: who the question is about, how
    the parser moved the key, then what the subject wrote into the Reading
    (here the question's own spelling of the companion, "maxey" read as
    Tyrese Maxey) - and a replayed route, which the parser never read,
    carries none of the parser's. The team rule records no decision of its
    own where it leaves the player as he was."""
    question = "what was the sixers record when maxey scored 15+ points?"
    route = _route(con, question, ["sixers", "maxey"], "points")
    assert route.reading is not None
    decisions = route.reading.decisions
    # The record's player is the companion who reached the line, written once
    # by the rules from the typed companion: no spelling is written first and
    # overwritten, so no decision records one.
    assert [(d.stage, d.field) for d in decisions] == [("subject", "kind"), ("subject", "teams"), ("subject", "companions"), ("parser", "asked")]
    assert route.reading.who.player == "Tyrese Maxey"
    handed = Reading(**{**route.scope.filters(), "who": Who.from_slots({"player": "Tyrese Maxey", "team": route.who.team})})
    replayed = reading_of(con, question, with_subject(con, question, Routed(route.asked, handed)))
    assert [(d.stage, d.field) for d in replayed.decisions] == [("subject", "kind"), ("subject", "teams"), ("subject", "companions")]


# #259: the apostrophe a phone keyboard types (U+2019, and U+2018,
# its opening twin) is a straight one to every reader, folded once at the
# parser's door - the question and the names the model copied out of it.


@pytest.mark.parametrize("apostrophe", ["\u2019", "\u2018"])
def test_a_typographic_apostrophe_reads_as_a_straight_one(con: duckdb.DuckDBPyConnection, apostrophe: str) -> None:
    """ "most points in the 2010\u2019s" is the decade, and a name the model
    copied with its possessive on ("Devin Booker\u2019s") or an apostrophe
    inside ("D\u2019Angelo Russell") is the player: each reads as its
    straight twin."""
    con.execute("INSERT INTO players VALUES ('11', 'Devin Booker'), ('12', 'D''Angelo Russell')")
    con.execute("INSERT INTO teams VALUES ('5', 'Minnesota Timberwolves', 'MIN')")
    for typed, names, stat in (
        ("most points in the 2010{a}s", [], "points"),
        ("Devin Booker{a}s stats last five games", ["Devin Booker{a}s"], ""),
        ("D{a}Angelo Russell game against the Timberwolves", ["D{a}Angelo Russell", "Timberwolves"], ""),
        ("maxey points on new year{a}s eve", ["maxey"], "points"),
    ):
        curly = _route(con, typed.format(a=apostrophe), [name.format(a=apostrophe) for name in names], stat)
        straight = _route(con, typed.format(a="'"), [name.format(a="'") for name in names], stat)
        assert (curly.asked, curly.scope.filters()) == (straight.asked, straight.scope.filters()), typed
    decade = _route(con, f"most points in the 2010{apostrophe}s", [], "points")
    assert (decade.span.since, decade.span.until, decade.span.season) == (2010, 2019, None)
    booker = _route(con, f"Devin Booker{apostrophe}s stats last five games", [f"Devin Booker{apostrophe}s"], "")
    assert (booker.asked, booker.who.player, booker.window.count) == (PLAYER_LOG, "Devin Booker", 5)
    russell = _route(con, f"D{apostrophe}Angelo Russell game against the Timberwolves", [f"D{apostrophe}Angelo Russell", "Timberwolves"], "")
    assert (russell.who.player, russell.cuts.opponent) == ("D'Angelo Russell", "Minnesota Timberwolves")
    holiday = _route(con, f"maxey points on new year{apostrophe}s eve", ["maxey"], "points")
    assert holiday.cuts.situation is not None and holiday.cuts.situation.text == "new year's eve"
    # The whole path, as the agent takes it: the parser's last step is handed
    # the question as typed too, and reads who it is about from its words.
    reading = _read(con, f"D{apostrophe}Angelo Russell game against the Timberwolves", [f"D{apostrophe}Angelo Russell", "Timberwolves"])
    assert reading.subject is not None and (reading.subject.kind, reading.subject.players, reading.misread) == ("player", ("D'Angelo Russell",), ())
    # A denial is one: typed with U+2019, "doesn't play" is his absence.
    denied = _read(con, f"maxey points when embiid doesn{apostrophe}t play", ["maxey", "embiid"], "points")
    assert denied.filters() == _read(con, "maxey points when embiid doesn't play", ["maxey", "embiid"], "points").filters()
    assert denied.subject is not None and [c.predicate for c in denied.subject.conditions] == ["absent"]


def test_a_name_the_model_copied_with_a_typographic_apostrophe_anchors_to_the_question(con: duckdb.DuckDBPyConnection) -> None:
    """The model copies a name as the question typed it, so the names are
    folded with the question: "De\u2019Aaron" is a span of the folded
    question and is kept, and "Kel\u2019el Ware" is the whole name, not "Ware",
    which two players share."""
    con.execute("INSERT INTO players VALUES ('11', 'De''Aaron Fox'), ('12', 'Kel''el Ware'), ('13', 'Casey Ware')")
    for apostrophe in ("\u2019", "\u2018"):
        first = _route(con, f"De{apostrophe}Aaron stats this season", [f"De{apostrophe}Aaron"], "")
        assert (first.asked, first.who.player, first.who.kind) == (PLAYER_LINE, "De'Aaron", "player")
        full = _route(con, f"Kel{apostrophe}el Ware rebounds", [f"Kel{apostrophe}el Ware"], "rebounds")
        assert full.who.player == "Kel'el Ware"


def test_a_quarter_ranking_that_says_player_ranks_the_teams_players(con: duckdb.DuckDBPyConnection) -> None:
    """yardstick-v2 F049, "hornets average 1st quarter points player" asks
    the team's leading player, not the team's own average: "player" ranks,
    as "who" and "leaders" do, and only where no player is named."""
    route = _route(con, "celtics average 1st quarter points player", ["celtics"], "points")
    assert (route.asked, _quarter(route)) == (PERIOD_RANKING, 1)
    team = _route(con, "celtics average 1st quarter points", ["celtics"], "points")
    assert team.asked == TEAM_PERIOD_TOTAL


def test_a_named_players_period_games_are_listed(con: duckdb.DuckDBPyConnection) -> None:
    """yardstick-v2 F060, "Rudy gobert first half games this season": the key
    lists his first halves. "games" with no stat named is a log, as "log"
    is; with a stat named it stays the total."""
    route = _route(con, "tyrese maxey first half games this season", ["tyrese maxey"], "")
    assert (route.asked, _half(route), route.measure is not None and route.measure.how == "per_game") == (PERIOD_LOG, 1, True)
    total = _route(con, "tyrese maxey first half points over his games this season", ["tyrese maxey"], "points")
    assert total.measure is None or total.measure.how != "per_game"


def test_points_by_quarter_with_no_one_named_is_the_leagues_table(con: duckdb.DuckDBPyConnection) -> None:
    """yardstick-v2 F048, "nba playerspoints by quarter average": every
    quarter at once is a ranking of a period (``PERIOD_RANKING``) with no
    period. A named player's breakdown is a period log (``PERIOD_LOG``) with
    none (#162)."""
    route = _route(con, "nba playerspoints by quarter average", [], "")
    assert route.asked == PERIOD_RANKING and route.period is None
    named = _route(con, "Jokic points by quarter", ["Jokic"], "points")
    assert named.asked == PERIOD_LOG and named.period is None


def test_most_ranks_a_quarter_only_where_no_team_is_named(con: duckdb.DuckDBPyConnection) -> None:
    """ "most first quarter rebounds per game" ranks the league's players; "the
    celtics most points in a first half" is the team's own best half."""
    league = _route(con, "most first quarter rebounds per game this season", [], "rebounds")
    assert (league.asked, _quarter(league), league.measure.as_typed) == (PERIOD_RANKING, 1, "rebounds")
    team = _route(con, "celtics most points in a first half this season", ["celtics"], "points")
    assert team.asked == TEAM_PERIOD_TOTAL


def test_a_teams_quarter_of_any_stat_is_the_teams_quarter(con: duckdb.DuckDBPyConnection) -> None:
    """The period relation's team half (#161): a team's rebounds, threes or
    turnovers in a quarter or half are read as a team's period total
    (``TEAM_PERIOD_TOTAL``) with the stat kept and the period read from the
    words - hyphenated too ("first-quarter rebounds")."""
    rebounds = _route(con, "how many first-quarter rebounds do the celtics average", ["celtics"], "rebounds")
    assert (rebounds.asked, _quarter(rebounds), rebounds.measure.as_typed) == (TEAM_PERIOD_TOTAL, 1, "rebounds")
    threes = _route(con, "magic stats last 10 games 3 point average 1st quarter", ["magic"], "threePointFieldGoalsMade")
    assert (threes.asked, _quarter(threes), threes.window.count, threes.measure.as_typed) == (TEAM_PERIOD_TOTAL, 1, 10, "threePointFieldGoalsMade")
    turnovers = _route(con, "celtics 2nd half turnovers vs the lakers", ["celtics", "lakers"], "turnovers")
    assert (turnovers.asked, _half(turnovers), turnovers.cuts.opponent) == (TEAM_PERIOD_TOTAL, 2, "Los Angeles Lakers")
    assists = _route(con, "sixers second-half assists per game", ["sixers"], "assists")
    assert (assists.asked, _half(assists), assists.measure.as_typed) == (TEAM_PERIOD_TOTAL, 2, "assists")


def test_a_period_used_as_a_condition_is_read_into_the_lines_and_off_the_period_readers(con: duckdb.DuckDBPyConnection) -> None:
    """yardstick-v2 F062, "... three points made per game after making one
    three in first quarter": his whole-game threes over the games whose first
    quarter held one, never his first-quarter threes. The condition is read
    into the Reading's lines (``line.read_period_line``, #275) and its words
    taken out of the question the grammar sees, so the rest reads as the
    per-game average it is; a bare "one" is exactly one (the key's reading,
    said as "exactly" in the answer) and "at least"/"+" at least. A
    condition whose line cannot be read ("after scoring a lot") is kept off
    the period readers, for the refusal to name."""
    from association.query.reading import Line, Period

    def in_period(route: Routed) -> Line | None:
        lines = [line for line in route.lines if line.period is not None]
        return lines[0] if lines else None

    route = _route(con, "tyrese maxey three points made per game after making one three in first quarter", ["tyrese maxey"], "threePointFieldGoalsMade")
    assert route.asked == PLAYER_LINE
    assert in_period(route) == Line(measure="threePointFieldGoalsMade", op="=", value=1, period=Period(number=1), as_typed="after making one three in first quarter")
    assert route.period is None and threshold_of(route.scope) is None, "the condition's number and period are the condition's, not the route's"
    at_least = _route(con, "tyrese maxey three points made per game after making at least one three in first quarter", ["tyrese maxey"], "threePointFieldGoalsMade")
    assert in_period(at_least) == Line(measure="threePointFieldGoalsMade", op=">=", value=1, period=Period(number=1), as_typed="after making at least one three in first quarter")
    log = _route(con, "tyrese maxey game log in games where he scored 10+ points in the first half", ["tyrese maxey"], "points")
    assert log.asked == PLAYER_LOG and in_period(log) == Line(measure="points", op=">=", value=10, period=Period(number=1, half=True), as_typed="in games where he scored 10+ points in the first half")
    plain = _route(con, "tyrese maxey three points made in the first quarter", ["tyrese maxey"], "threePointFieldGoalsMade")
    assert plain.asked == PERIOD_LOG and in_period(plain) is None
    unread = _route(con, "tyrese maxey three points made per game after scoring a lot in the first quarter", ["tyrese maxey"], "threePointFieldGoalsMade")
    assert unread.asked is None and in_period(unread) is None


def test_a_question_of_fewer_than_three_words_is_refused_unread() -> None:
    """ "Tatum rec" and bare names are a user hitting enter early, and are refused with a generic sentence rather than
    guessed at. Three words is a question ("76ers away record"). The
    parser's first verdict, a cause the planner says."""
    from association.query.compose.plan import refusal_result
    from association.query.parse import MIN_QUESTION_WORDS, too_short
    from association.query.reading import Cause

    assert MIN_QUESTION_WORDS == 3
    assert too_short("Tatum rec") == Cause(kind="too_short", facts={"asked": "Tatum rec"})
    assert refusal_result(Cause(kind="too_short", facts={"asked": "Tatum rec"})).text == "I couldn't understand your question, 'Tatum rec'. Please try re-phrasing it."
    short = too_short("  bam stats ")
    assert short is not None and refusal_result(short).text == "I couldn't understand your question, 'bam stats'. Please try re-phrasing it."
    assert too_short("76ers away record") is None
    assert too_short("luka td3s home") is None


@pytest.mark.parametrize(
    ("question", "named", "asked"),
    [
        ("tatum 2nd half plus minus", ["tatum"], PERIOD_LOG),
        ("tatum first quarter +/- this season", ["tatum"], PERIOD_LOG),
        ("who has the most 4th quarter plus minus", [], PERIOD_RANKING),
    ],
)
def test_a_quarters_plus_minus_keeps_its_stat_and_is_refused_for_it(con: duckdb.DuckDBPyConnection, question: str, named: list[str], asked: PointShape) -> None:
    """ "vj edgecombe 2nd half plus minus" and "who has the most 4th quarter
    plus minus" keep plus-minus as their stat though a quarter's line
    defaults to points; a quarter has no plus-minus to give, so each is
    refused for it, never answered with points."""
    route = _route(con, question, named, "")
    reading = route.reading
    assert reading is not None
    assert reading.asked == asked and reading.measure is not None and reading.measure.as_typed == "plus_minus"
    planned = plan_point(reading)
    if asked == PERIOD_LOG:
        assert reading.point_refusal is not None and reading.point_refusal.kind == "no_period_stat"
        assert isinstance(planned, Refusal) and "no per-period 'plus_minus'" in refusal_phrase(planned.kind, planned.facts)
    else:
        assert [cause.kind for cause in reading.unsupported] == ["period_stat"] and reading.unsupported[0].facts["stat"] == "plus_minus"


def test_the_grammars_read_a_line_and_an_opponent_as_the_taggers_type_them(con: duckdb.DuckDBPyConnection) -> None:
    """The child grammars read each line the lines tagger reads as the value
    it typed, and the parent grammar the opponent the subject reading read
    (``lexicon.GrammarText``), so a grammar claims its own words around them
    and the line's and the opponent's characters are their readers' alone -
    no claim is joined from the two (``intent+line``, ``intent+opponent``),
    and the child's words in its decision are the question's own."""
    question = "How many games did SGA score 30+ points this season?"
    r = _read(con, question, names=["SGA"], stat="points")
    assert r.asked == GAMES_COUNTED
    read = {question[c.start : c.end]: c.what for c in r.claims}
    assert read["30+ points"] == "line" and not any("+" in what for what in read.values())
    assert r.subject is not None and r.subject.asked_reason == "the words 'How many games did SGA score 30+' name player_games/scalar/count"
    question = "show maxey's games against boston in the past two seasons"
    r = _read(con, question, names=["maxey", "boston"], stat="")
    assert r.asked == PLAYER_LOG
    read = {question[c.start : c.end]: c.what for c in r.claims}
    assert read["against boston"] == "opponent" and read["games"] == "intent" and not any("+" in what for what in read.values())


def test_the_grammars_read_the_companions_the_windows_and_the_dates_as_the_taggers_type_them(con: duckdb.DuckDBPyConnection) -> None:
    """A companion's phrase, a window of games and a dated range are typed
    the same way (``parse.grammar_text``), and the rules are
    handed the team words, a reached
    companion's "when" clause and his name typed - no claim is joined from
    two readings of one word. The names go in before the first read: the
    index is loaded once per test."""
    con.execute("INSERT INTO players VALUES ('40', 'Julius Randle'), ('41', 'Karl-Anthony Towns')")
    con.execute("INSERT INTO teams VALUES ('42', 'San Antonio Spurs', 'SA'), ('43', 'Toronto Raptors', 'TOR')")
    for question, names, asked, own in (
        ("What was the sixers record with/without embiid this season?", ["sixers", "embiid"], PRESENCE_SPLIT, ("without embiid", "companion")),
        ("What was the sixers record this season when maxey scored 20+ points?", ["sixers", "maxey"], LINE_RECORD, ("when maxey", "companion")),
        ("randle last 10 game log vs lakers", ["randle", "lakers"], PLAYER_LOG, ("last 10 game", "window")),
        ("since 1/26/20, what are the towns home records including playoffs against the spurs?", ["towns", "spurs"], PLAYER_LOG, ("since 1/26/20", "situation")),
        # The opponent's nickname is no team whose half is asked (#367): with
        # no player read, the words ask for no shape.
        ("miles bridges stats vs raptors second half", ["raptors"], None, ("vs raptors", "opponent")),
    ):
        r = _read(con, question, names=names, stat="")
        read = {question[c.start : c.end]: c.what for c in r.claims}
        assert r.asked == asked and read[own[0]] == own[1] and not any("+" in what for what in read.values()), (question, r.asked, read)


def test_a_rank_word_beside_no_quarter_names_no_team(con: duckdb.DuckDBPyConnection) -> None:
    """ "gafford v warriors most recent 5 games": the period rule's check for
    a ranking of players, deciding nothing where no quarter is named, writes
    no team - the Warriors are the opponent of a player's log, not his
    team."""
    _with_the_warriors(con)
    con.execute("INSERT INTO players VALUES ('30', 'Daniel Gafford')")
    reading = read_route(names_of(con), "Gafford v warriors most recent 5 games", ["Gafford", "warriors"], "")
    assert (reading.asked, reading.who.teams, reading.cuts.opponent) == (PLAYER_LOG, (), "Golden State Warriors")


def test_a_line_and_an_opponent_are_typed_where_their_words_stand() -> None:
    """The grammar's text keeps every character where it stands and holds
    what each tagger typed apart from the words: a line's number by how it is
    stated and its stat, the words that lead a line under a number (each
    word, the spaces between left), an opponent by its versus word, a
    companion's phrase by its presence word with a line inside it kept, a
    window of games and a dated range - each a run of
    :data:`~association.query.lexicon.TYPED` in the words, so no pattern
    over the words reads what stands there."""
    from association.query import lexicon
    from association.query.line import lines_named

    def runs(text: lexicon.GrammarText) -> list[tuple[str, str | None]]:
        out: list[tuple[str, str | None]] = []
        for char, kind in zip(text.source, text.kinds, strict=True):
            if out and out[-1][1] == kind:
                out[-1] = (out[-1][0] + char, kind)
            else:
                out.append((char, kind))
        return out

    question = "games with 20+ points or under 14 fta"
    lines = lines_named(question)
    assert lines.threshold == 20 and lines.below
    marked, numbered = lines.marked(question), lines.numbered(question)
    assert len(marked.text) == len(numbered.text) == len(question) and lexicon.TYPED not in question
    assert runs(marked) == [
        ("games with ", None),
        ("20+", "line_plus"),
        (" ", None),
        ("points", "line_stat"),
        (" or ", None),
        ("under", "line_lead"),
        (" ", None),
        ("14", "line_number"),
        (" ", None),
        ("fta", "line_stat"),
    ]
    assert runs(numbered) == [("games with ", None), ("20+", "line_plus"), (" points or under ", None), ("14", "line_number"), (" fta", None)]
    assert numbered.text == "games with " + lexicon.TYPED * 3 + " points or under " + lexicon.TYPED * 2 + " fta"
    opposed = lexicon.GrammarText.of("games vs boston").typed_over(((6, 15, lexicon.opponent_kind("vs boston")),))
    assert runs(opposed) == [("games ", None), ("vs boston", "opponent_against")]
    question = "record when maxey scored 30+ points"
    text = lines_named(question).numbered(question)
    text = text.typed_over(((7, 28, lexicon.companion_kind(text.text[7:28])),), under=lexicon.LINE_KINDS)
    assert runs(text) == [("record ", None), ("when maxey scored ", "companion_with"), ("30+", "line_plus"), (" points", None)]
    text = lexicon.GrammarText.of("log in games embiid missed").typed_over(((4, 26, lexicon.companion_kind("in games embiid missed")),))
    assert runs(text) == [("log ", None), ("in games embiid missed", "companion")]
    assert runs(grammar_text("randle last 10 game log since 1/26/20")) == [("randle ", None), ("last 10 game", "window"), (" log ", None), ("since 1/26/20", "date")]


def test_a_probe_keeps_each_typed_value_where_it_stands() -> None:
    """A rule asks itself whether it needed a word by deleting it (the
    claims ledger's probe): over the grammar's text the gap is closed as
    in a plain question, and each typed value keeps its place among the
    words left, partly deleted where the word held part of it."""
    from association.query import lexicon

    text = lexicon.GrammarText.of("show his  games vs boston").typed_over(((16, 25, "opponent_against"),))
    probed = text.without(5, 8)
    assert probed.text == "show games " + lexicon.TYPED * 9 and probed.kind_at(11) == "opponent_against" and probed.kind_at(10) is None
    assert probed.text == lexicon.without_word(text.text, 5, 8)
    edge = lexicon.GrammarText.of("games vs boston's").typed_over(((6, 15, "opponent_against"),)).without(6, 17)
    assert edge.text == "games" and not edge.holds(frozenset({"opponent_against"}))


def test_an_ask_reads_a_typed_value_by_value_and_never_a_character_typed_in() -> None:
    """An :class:`~association.query.lexicon.Ask` reads a value where it
    stands by its kind, spelled for the match from the typed values alone:
    the same characters typed into the question, or the one every typed
    character is written as in the words, read as nothing typed."""
    from association.query import lexicon

    plus = lexicon.ask(lexicon.typed("line_plus") + "+ games")
    text = lexicon.GrammarText.of("30+ games").typed_over(((0, 3, "line_plus"),))
    assert plus.search(text) is not None
    assert plus.search(text.typed_over(((0, 3, "line_number"),))) is None
    spelled_by_hand = text.spelled[:3] + " games"
    assert plus.search(spelled_by_hand) is None and plus.search(lexicon.TYPED * 3 + " games") is None
    assert plus.search(lexicon.GrammarText.of(spelled_by_hand)) is None


#: Characters a user could type to try to forge a typed value: the
#: private-use characters U+E000-U+E00B, which no reader reads as a value,
#: the alphabet an ask spells a kind by inside its match (from U+E100), and
#: the one every typed character is written as in the grammar's words
#: (U+FFFC, ``lexicon.TYPED``).
_MARKS = tuple(chr(code) for code in (*range(0xE000, 0xE00C), *range(0xE100, 0xE10C), 0xFFFC))


@pytest.mark.parametrize("mark", _MARKS)
@pytest.mark.parametrize(
    "question",
    ["nikola jokic {} stats this season", "how many games {}{} {}{} did nikola jokic have", "{}joel embiid game log vs boston", "luka doncic {} points per game in 2024 {}"],
)
def test_a_mark_typed_into_the_question_forges_no_value(con: duckdb.DuckDBPyConnection, question: str, mark: str) -> None:
    """A user who types one of these characters forges nothing (#383): no
    value is read from a character - the grammars read what the taggers
    typed (``lexicon.GrammarText``), and an ask spells it inside its match
    alone - so the reading is the one of the question with a space there,
    every claim at the same place, with no intake rewriting the question."""
    char = mark
    typed = question.format(*[char] * question.count("{}"))
    spaced = question.format(*[" "] * question.count("{}"))
    assert _read(con, typed) == _read(con, spaced)
    assert _read(con, typed, ["nikola jokic"]) == _read(con, spaced, ["nikola jokic"])


@pytest.mark.parametrize(
    ("question", "names", "kind", "phrase"),
    [
        ("kobe bryant career games with more than 40 field goal percentage", ["kobe bryant"], "line_on_a_percentage", "more than 40 field goal percentage"),
        ("lebron james career games with more than 45 field goal percentage", ["lebron james"], "line_on_a_percentage", "more than 45 field goal percentage"),
    ],
)
def test_a_line_on_a_percentage_is_the_points_cause_on_every_key(con: duckdb.DuckDBPyConnection, question: str, names: list[str], kind: str, phrase: str) -> None:
    """#371: a line on a percentage is the point reader's cause on every
    key - a log kept past one as well as a count - not a decline."""
    reading = _read(con, question, names)
    assert reading.point_refusal is not None and reading.point_refusal.kind == kind and reading.point_refusal.facts["phrase"] == phrase


@pytest.mark.parametrize(
    ("question", "lines"),
    [
        ("How many players averaged 30 ppg in 2026", [("points", ">=", 30, "season")]),
        ("how many players average 25+ in 2006 nba", [("points", ">=", 25, "season")]),
        ("players to average 16 points 10 rebounds", [("points", ">=", 16, "season"), ("rebounds", ">=", 10, "season")]),
        ("players who averaged under 10 points in 2024", [("points", "<", 10, "season")]),
        ("nba all-time ast/to leaders with > 5 apg", [("assists", ">", 5, "season")]),
        ("nba player with 10000 points and 4000 assists in career", [("points", ">=", 10000, "total"), ("assists", ">=", 4000, "total")]),
        (
            "nba players who averaged a minimum of 20 points per game and less than 4 rebounds per game and less than 3 assists per game on a season",
            [("points", ">=", 20, "season"), ("rebounds", "<", 4, "season"), ("assists", "<", 3, "season")],
        ),
    ],
)
def test_a_line_on_a_season_is_read_by_the_season_ranking(con: duckdb.DuckDBPyConnection, question: str, lines: list[tuple[str, str, float, str]]) -> None:
    """#390: a ranking of players' seasons stating a line on the season's
    figure keeps its players past the season lines ("averaged under 10
    points" a season line, not a game line), a line naming no stat on the
    ranking's own measure."""
    reading = _read(con, question)
    # "How many players ..." is the ranking counted (PLAYERS_COUNTED, the next key).
    counted = question.casefold().startswith("how many")
    assert reading.point is not None and reading.point.read_as == (PointShape("player_seasons", "scalar", "count") if counted else PointShape("player_seasons", "ranking", "player"))
    assert reading.asked == (PLAYERS_COUNTED if counted else PLAYER_RANKING)
    assert [(line.measure, line.op, line.value, line.per) for line in reading.point.lines] == lines


@pytest.mark.parametrize(
    ("question", "lines"),
    [
        ("most seasons averaging 30 ppg", [("points", ">=", 30, "season")]),
        ("most seasons averaging 10 rebounds per game", [("rebounds", ">=", 10, "season")]),
        ("Players with most 25 point season", [("points", ">=", 25, "season")]),
        ("nba players with the most seasons with 2000 points", [("points", ">=", 2000, "total")]),
    ],
)
def test_a_count_of_seasons_over_a_season_line_ranks_the_players_by_it(con: duckdb.DuckDBPyConnection, question: str, lines: list[tuple[str, str, float, str]]) -> None:
    """#391: "most seasons averaging 30 ppg" is no plain points ranking: the
    ranking's point is ordered by the count of seasons (``by="seasons"``) -
    asked as the ranking, as a count of games over a line ranks by
    ``games``."""
    reading = _read(con, question)
    assert reading.asked == PLAYER_RANKING and reading.point is not None and reading.point.read_as == PointShape("player_seasons", "ranking", "seasons")
    assert [(line.measure, line.op, line.value, line.per) for line in reading.point.lines] == lines


@pytest.mark.parametrize(
    ("question", "kind", "words"),
    [
        ("most 60 win seasons nba players", "seasons_counted", "most 60 win seasons"),
        ("nba player with most seasons with a per of 30 or more while playing at least 60 games a season", "seasons_counted", "most seasons with a per of 30"),
        ("nba players who average 18 ppg 3.5 assists and shot 54% or better 2026 season", "season_line_unread", "shot 54% or better"),
    ],
)
def test_a_count_of_seasons_or_a_season_line_the_ranking_cannot_hold_is_refused_by_name(con: duckdb.DuckDBPyConnection, question: str, kind: str, words: str) -> None:
    """#390, #391: a count of a player's seasons over a figure no season
    line holds, and a percentage beside a season line, are each refused by a
    cause naming the words, never the unfiltered ranking."""
    reading = _read(con, question)
    assert reading.point_refusal is not None and reading.point_refusal.kind == kind and reading.point_refusal.facts == {"words": words}


@pytest.mark.parametrize("question", ["who averaged the most points in 2024", "who has the highest average 1st quarter points this season?", "who was leading the league in average 3-point distance?"])
def test_an_average_with_no_line_is_not_refused_as_a_season_line(con: duckdb.DuckDBPyConnection, question: str) -> None:
    """The words of a season's line need a number beside the average."""
    reading = _read(con, question)
    assert reading.point_refusal is None or reading.point_refusal.kind not in ("season_line_unread", "seasons_counted")


def test_how_many_players_a_season_line_holds_is_the_ranking_counted(con: duckdb.DuckDBPyConnection) -> None:
    """#390: "how many players averaged 30 ppg" names ``PLAYERS_COUNTED`` -
    "how many" before players, beside a season line as the lines tagger
    typed it;
    "how many" with no season line, or a season line with no "how many", is
    the ranking."""
    for question in ("How many players averaged 30 ppg in 2026", "how many nba players average 25 points per game"):
        assert _read(con, question).asked == PLAYERS_COUNTED, question
    for question in ("who averaged 30 ppg in 2026", "how many players have averaged the most points"):
        assert _read(con, question).asked == PLAYER_RANKING, question


@pytest.mark.parametrize(
    ("question", "predicates"),
    [
        ("most games with 20 pts, 10 reb, 5 ast last season", [("points", ">=", 20), ("rebounds", ">=", 10), ("assists", ">=", 5)]),
        ("most games with 20 pt,s 10 reb, 5 ast", [("points", ">=", 20), ("rebounds", ">=", 10), ("assists", ">=", 5)]),
        ("most playoff games with 46 points 6 assists 6 rebounds", [("points", ">=", 46), ("assists", ">=", 6), ("rebounds", ">=", 6)]),
    ],
)
def test_a_league_count_counts_the_games_past_every_bare_line(con: duckdb.DuckDBPyConnection, question: str, predicates: list[tuple[str, str, int]]) -> None:
    """#358: "most games with 20 pts, 10 reb, 5 ast": the league's count
    takes every bare line as a predicate, none dropped, counting its first
    line's stat."""
    reading = _read(con, question)
    assert reading.point is not None and reading.point.predicates == predicates
    assert reading.measure is not None and reading.measure.key == "points"


@pytest.mark.parametrize(
    ("question", "direction"),
    [
        ("who has the worst fg% with atleast 1000 fg attempted", "asc"),
        ("who has the lowest fg% with atleast 1000 fg attempted", "asc"),
        ("who has the worst turnovers per game", "desc"),
        ("worst win percentage", "desc"),
        ("who has the most points per game", "desc"),
    ],
)
def test_worst_ranks_from_the_worse_end_of_a_stat_the_words_name(con: duckdb.DuckDBPyConnection, question: str, direction: str) -> None:
    """#350: "worst" is a ranking word: "who has the worst fg% ..." ranks
    from the bottom of a stat a player
    wants more of and from the top of one he wants less of (turnovers), and
    a ranking whose stat the words do not name keeps its order."""
    reading = _read(con, question)
    assert reading.point is not None and reading.point.direction == direction


@pytest.mark.parametrize(
    ("question", "line"),
    [
        ("celtics' record last season when committing 10 or fewer turnovers", ("turnovers", "<=", 10)),
        ("celtics record last season when committing 11 or fewer turnovers", ("turnovers", "<=", 11)),
        ("celtics record last season when they had under 12 turnovers", ("turnovers", "<", 12)),
        ("celtics record last season with less than 12 turnovers", ("turnovers", "<", 12)),
    ],
)
def test_a_teams_record_under_a_number_is_keyed_on_that_line(con: duckdb.DuckDBPyConnection, question: str, line: tuple[str, str, int]) -> None:
    """#325: "celtics' record when committing 10 or fewer turnovers" and
    "under 12 turnovers" are the team's record keyed on the line with its
    comparison, which the team's record reader reads
    (``compile_team_line(op=)``) - no narrowing beside a keyed line."""
    reading = _read(con, question, ["celtics"], "turnovers")
    assert reading.asked == LINE_RECORD and reading.point is not None and reading.point.read_as.relation == "team_games"
    keyed = [each for each in reading.point.lines if each.keyed]
    assert [(each.measure, each.op, each.value) for each in keyed] == [line] and not any(each.narrows for each in reading.point.lines)


@pytest.mark.parametrize("question", ["Gabe Vincent 3\u2019s as a laker", "steph curry 3's per season", "jokic 3's this season"])
def test_three_apostrophe_s_is_threes_made(con: duckdb.DuckDBPyConnection, question: str) -> None:
    """#270: "3's" (and its curly spelling) is threes made, never the
    player's points line."""
    reading = _read(con, question)
    assert reading.measure is not None and reading.measure.key == "threePointFieldGoalsMade"


@pytest.mark.parametrize(
    ("question", "whose"),
    [
        ("mavs opp fg percentage last 3 games", "opponent"),
        ("celtics opponent rebounds this season", "opponent"),
        ("mavs fg percentage last 3 games", "own"),
        ("most fgm + opp fgm in a game by celtics", "own"),
        ("celtics vs magic pts and opp pts game logs", "own"),
    ],
)
def test_opp_before_a_stat_is_the_opponents_figure(con: duckdb.DuckDBPyConnection, question: str, whose: str) -> None:
    """#409: "opp" before a stat is the opponent's figure
    (``whose="opponent"``) - not where two figures are added or set side by
    side."""
    reading = _read(con, question)
    assert reading.measure is not None and reading.measure.whose == whose


@pytest.mark.parametrize(
    ("question", "whose"),
    [
        ("how many points did the sixers allow in the 2001 playoffs", "opponent"),
        ("how many points did the sixers give up in the 2001 playoffs", "opponent"),
        ("how many points did the celtics allow this season", "opponent"),
        ("how many points did the celtics give up this season", "opponent"),
        ("which team allowed the most points per game", "opponent"),
        ("which team gave up the most points", "opponent"),
        ("which team conceded the fewest points", "opponent"),
        ("which team scored the most points per game", "own"),
        # Another stat given up stays the team's own: its season reader has no opponent's figure but points.
        ("how many assists on average do the mavericks give up", "own"),
        # A player's points are never the opponent's by the verb.
        ("how many points did embiid allow this season", "own"),
    ],
)
def test_a_teams_points_given_up_by_the_verb_are_the_opponents(con: duckdb.DuckDBPyConnection, question: str, whose: str) -> None:
    """#348, #361: a team's points allowed asked with the verb ("did the
    sixers allow", "which team gave up the most points") are the
    opponents' - "which team allowed the most points per game" too, past the
    longer alias "points per game"."""
    reading = _read(con, question)
    assert reading.measure is not None and reading.measure.whose == whose


@pytest.mark.parametrize(
    ("question", "unit"),
    [
        ("how many 3 pointers did the sixers make in the 2001 playoffs", "total"),
        ("how many points did the celtics score this season", "total"),
        ("how many rebounds did the celtics grab last season", "total"),
        ("how many 3 pointers have the magic made so far this season", "total"),
        # What a team does in a game, and a per-game word, stay per game.
        ("how many points do the celtics score", None),
        ("how many points per game did the celtics score this season", None),
    ],
)
def test_a_teams_count_asked_by_the_bare_verb_after_did_is_a_total(con: duckdb.DuckDBPyConnection, question: str, unit: str | None) -> None:
    """#347: "how many 3 pointers did the sixers make in the 2001 playoffs"
    asks the total, not the per-game figure: "did ... make" is a total's
    verb as the past forms are."""
    reading = _read(con, question)
    assert reading.measure is not None and reading.measure.unit == unit


@pytest.mark.parametrize(
    ("question", "names", "asked"),
    [
        # No player in the words: the teams' standings, as a record is.
        ("worst win percentage", [], TEAM_RANKING),
        ("highest win percentage this season", [], TEAM_RANKING),
        ("best win% since 2010", [], TEAM_RANKING),
        ("team with worst win% last season", [], TEAM_RANKING),
        # A named player's win percentage is his record: his splits, W-L by split.
        ("jokic win percentage this season", ["Nikola Jokic"], PLAYER_SPLITS),
        ("maxey winning pct vs boston", ["Tyrese Maxey"], PLAYER_SPLITS),
        # Players asked for stay the players' ranking, which refuses it by name.
        ("player with worst win percentage", [], PLAYER_RANKING),
        ("highest win percentage duo in nba history", [], PLAYER_RANKING),
    ],
)
def test_a_win_percentage_is_the_teams_standings_or_a_players_record(con: duckdb.DuckDBPyConnection, question: str, names: list[str], asked: PointShape) -> None:
    """#428: with no player in the words a win percentage ("worst win
    percentage") is the teams' standings, ranked by record; a named
    player's is his record, as "record" is; a ranking of players by it
    ("player with worst win percentage") is refused by name."""
    reading = _read(con, question, names)
    assert reading.asked == asked
    if asked == TEAM_RANKING:
        assert reading.measure is not None and reading.measure.key == "record" and reading.point_refusal is None
    if asked == PLAYER_RANKING:
        assert reading.point_refusal is not None and reading.point_refusal.facts["fact"] == "player win percentage"
        assert "No ranking of players by win percentage is read here" in refusal_phrase(reading.point_refusal.kind, reading.point_refusal.facts)


@pytest.mark.parametrize(
    ("question", "names"),
    [
        # Named by the words a player's history is named by, under a team's line.
        ("celtics last 5 seasons", ["celtics"]),
        ("lakers ppg by season", ["lakers"]),
        # A team's line over a range the words name another way is keyed so
        # by the point reader.
        ("how many games did the celtics play 2009 to 2023", ["celtics"]),
        ("celtics ppg since 2015", ["celtics"]),
    ],
)
def test_a_teams_line_over_more_than_one_season_is_its_history(con: duckdb.DuckDBPyConnection, question: str, names: list[str]) -> None:
    """#372: a team's season line reads one season; over more than one it is
    the team's history season by season."""
    reading = _read(con, question, names)
    assert reading.point is not None
    assert reading.point.read_as == PointShape("team_seasons", "split", "season")


@pytest.mark.parametrize(
    ("question", "names"),
    [
        # A ranking word or a line keeps the season line, refused by its cell.
        ("most celtics rebounds since 2010", ["celtics"]),
        ("celtics points per game this season", ["celtics"]),
    ],
)
def test_a_teams_line_ranked_or_of_one_season_is_not_its_history(con: duckdb.DuckDBPyConnection, question: str, names: list[str]) -> None:
    reading = _read(con, question, names)
    assert reading.point is not None
    assert reading.point.read_as != PointShape("team_seasons", "split", "season")


@pytest.mark.parametrize(
    ("question", "names", "since"),
    [
        ("portland trailblazers winning percentage last 10 seasons", ["portland trailblazers"], 2017),
        ("celtics winning percentage last 10 seasons", ["celtics"], 2017),
        ("portland trailblazers winning percentage last 15 seasons", ["portland trailblazers"], 2012),
        ("lakers winning pct this season", ["lakers"], None),
    ],
)
def test_a_teams_winning_percentage_is_its_record(con: duckdb.DuckDBPyConnection, question: str, names: list[str], since: int | None) -> None:
    """#372: "portland trailblazers winning percentage last 10 seasons" is
    the team's record over the seasons the words name, not its season line;
    "winning percentage" reads as "win percentage" does."""
    reading = _read(con, question, names)
    assert reading.asked == TEAM_RECORD
    assert reading.span.since == since


@pytest.mark.parametrize(
    ("question", "names", "asked", "key"),
    [
        ("most games played by a player this season", [], PLAYER_RANKING, "games_played"),
        ("player with most gp since 2020", [], PLAYER_RANKING, "games_played"),
        ("least games played with lakers", [], PLAYER_RANKING, "games_played"),
        ("most games played for the celtics in the 2010s", [], PLAYER_RANKING, "games_played"),
        # A player's own games played is his line, which says his games.
        ("maxey games played last season", ["Tyrese Maxey"], PLAYER_LINE, None),
    ],
)
def test_a_ranking_by_games_played_is_read_as_games_played(con: duckdb.DuckDBPyConnection, question: str, names: list[str], asked: PointShape, key: str | None) -> None:
    """#408: "games played" on a ranking of players, the league's or a
    team's ("most games played by a player denver nuggets"), is the
    measure, and the point counts each player's games."""
    reading = _read(con, question, names)
    assert reading.asked == asked
    assert (reading.measure.key if reading.measure is not None else None) == key
    if key is not None:
        point = reading.point
        assert point is not None and aggregate_of(point) == "count" and point.measures == () and point.predicates == [] and point.order == "measure"


@pytest.mark.parametrize(
    ("question", "line"),
    [
        ("who scored 40 points the most times from 2013 through 2018", ("points", ">=", 40)),
        ("who scored 40 points the most times", ("points", ">=", 40)),
        ("who had 15 assists the most times this season", ("assists", ">=", 15)),
        ("who has scored 50 points the most", ("points", ">=", 50)),
    ],
)
def test_a_line_reached_the_most_times_is_a_count_of_games_over_it(con: duckdb.DuckDBPyConnection, question: str, line: tuple[str, str, int]) -> None:
    """#370: "who scored 40 points the most times" is the players' count of
    games over the line - a bare line followed by "the most times" names the
    count's child as "most ... N+ stat" and "N stat games" do - with "40"
    read."""
    reading = _read(con, question)
    assert reading.asked == GAMES_COUNTED
    assert [(each.measure, each.op, each.value) for each in reading.lines] == [line]
    assert "40" not in reading.unread and "50" not in reading.unread and "15" not in reading.unread


def test_game_score_is_ranked_by_game_score_not_by_points(con: duckdb.DuckDBPyConnection) -> None:
    """#114: "game score nba leader" handed the model's ``stat='points'`` (a
    required key over a closed enum that holds no game score) is game
    score: the words win over the key, and the measure is ranked per game
    on the season line by ``avg_game_score``."""
    from association.query.compose.core import Query
    from association.query.compose.rankings import _leaderboard_metric

    reading = _read(con, "game score nba leader", [], "points")
    assert reading.asked == PLAYER_RANKING
    assert reading.measure is not None and reading.measure.key == "game_score"
    planned = plan_point(reading)
    assert isinstance(planned, Query) and planned.source == "seasons"
    assert _leaderboard_metric(planned, career=False) == "avg_game_score"


@pytest.mark.parametrize(
    ("question", "names", "stat", "kind", "words"),
    [
        ("aaron gorodn lat 5 games", ["Aaron Gordon"], "points", "unread_number", ("5",)),
        ("Bam adeboyo 1st quater average", ["Bam Adebayo"], "points", "unread_number", ("1st",)),  # codespell:ignore quater - the feed's own spelling
        ("sga scored 20 points in the 2nd quarter", ["Shai Gilgeous-Alexander"], "points", "unread_number", ("20",)),
        # "20+" is a number too, not left in no class for the league's rebounding leaders to answer.
        ("murray games of 20+ rebounds", [], "rebounds", "unread_number", ("20+",)),
        ("anthony davis last 1st quarter stats vs pistons", ["Anthony Davis"], "points", "unread_window", ("last",)),
        ("curry stats november", ["Stephen Curry"], "points", "unread_date", ("november",)),
    ],
)
def test_a_word_of_meaning_the_reading_defaulted_past_is_a_refusal_by_name(con: duckdb.DuckDBPyConnection, question: str, names: list[str], stat: str, kind: str, words: tuple[str, ...]) -> None:
    """An unread number, window word or month the reading took a default past
    is a ``defaulted`` cause naming the words, which the planner says where
    it would otherwise answer past them ("aaron gorodn lat 5 games" with the
    5 unread). The cause claims its words, so they leave
    ``Reading.unread``."""
    reading = _read(con, question, names, stat)
    assert reading.defaulted is not None and reading.defaulted.kind == kind and reading.defaulted.facts["words"] == words
    assert not any(word in reading.unread for word in words)
    assert reading.defaulted.facts["shape"] == reading.asked
    planned = plan_point(reading)
    assert isinstance(planned, Refusal) and planned.kind == kind


@pytest.mark.parametrize(
    ("question", "names", "stat", "words"),
    [
        # A token holding a digit that is no number as written is an unread number.
        ("most 3ot points in a game by a team nba", [], "points", ("3ot",)),
        ("lakers play in 20222-23", ["Los Angeles Lakers"], "points", ("20222-23",)),
    ],
)
def test_a_glued_digit_is_a_defaulted_word(con: duckdb.DuckDBPyConnection, question: str, names: list[str], stat: str, words: tuple[str, ...]) -> None:
    """The classes of words of meaning catch a digit glued to a word."""
    reading = _read(con, question, names, stat)
    assert reading.defaulted is not None and reading.defaulted.facts["words"] == words, reading.defaulted


def test_a_number_beside_a_stat_word_it_does_not_count_is_an_unread_number(con: duckdb.DuckDBPyConnection) -> None:
    """A number beside a stat word the reading did not read on is the
    ``unread_number`` cause, naming the number."""
    reading = _read(con, "who lead the league in field goal percentage for 3 point shots", [], "fieldGoalPct")
    assert reading.defaulted is not None and reading.defaulted.kind == "unread_number" and reading.defaulted.facts["words"] == ("3",)
    reading = _read(con, "lebron james 2 3 pointers all-time vs jazz on tuesdays", ["LeBron James"], "threePointFieldGoalsMade")
    assert reading.defaulted is not None and reading.defaulted.kind == "unread_number" and reading.defaulted.facts["words"] == ("2",)


def test_a_stat_word_naming_another_measure_is_the_unread_stat_cause(con: duckdb.DuckDBPyConnection) -> None:
    """A stat word the measure tagger reads to a key other than the one the
    reading took is the ``unread_stat`` cause, naming both, which the
    planner says rather than rank by the one measure."""
    reading = _read(con, "who has the best field goal percentage and 3pm this season", [], "fieldGoalPct")
    assert reading.defaulted is not None and reading.defaulted.kind == "unread_stat"
    assert (reading.defaulted.facts["words"], reading.defaulted.facts["names"], reading.defaulted.facts["read"]) == (("3pm",), "3-pointers", "field-goal percentage")
    planned = plan_point(reading)
    assert isinstance(planned, Refusal) and planned.kind == "unread_stat"


@pytest.mark.parametrize(
    ("question", "names", "stat"),
    [
        # A rate's unit is no count; the season year the span read is no number.
        ("jokic points per 100 possessions in 2024", ["Nikola Jokic"], "points"),
        # A read line's value is read, and its stat word is the line's.
        ("how many games did curry score 30 points", ["Stephen Curry"], "points"),
        # A slashed pair after a verb is a figure, not a day.
        ("curry games where he went 7/14 from three", ["Stephen Curry"], "threePointFieldGoalsMade"),
        # A leftover word of the measure the model read whole names no other stat.
        ("KNICKS point differential over the last 7 games", ["New York Knicks"], "points_differential"),
        # "points" of "three points made" is a leftover of the 3-pointers read (the yardstick's F062).
        ("vj edgecombe three points made per game after making one three in first quarter", ["VJ Edgecombe"], "threePointFieldGoalsMade"),
        # A word that restates the shape is in no class.
        ("Lakers vs Celtics record this season", ["Los Angeles Lakers", "Boston Celtics"], "points"),
        # A window the tagger read is no unread window word.
        ("curry last 5 games", ["Stephen Curry"], "points"),
        # A window word beside no count or game word names no stretch; "may" the verb is no month;
        # a list of stats on a log asks for the log, which shows them all.
        ("who are the top scorers in the nba", [], "points"),
        ("top rebounders 2024", [], "rebounds"),
        ("who may lead the league in assists", [], "assists"),
        ("luka doncic points rebounds assists last 5 games", ["Luka Doncic"], "points"),
    ],
)
def test_a_word_the_reading_read_or_that_restates_the_shape_is_no_defaulted_word(con: duckdb.DuckDBPyConnection, question: str, names: list[str], stat: str) -> None:
    """The classes are the taggers' own words, so a
    unit, a season, a read line's value, a figure written with a slash, a
    leftover of the read measure and a word that restates the shape carry
    no meaning the reading defaulted past."""
    reading = _read(con, question, names, stat)
    assert reading.defaulted is None, reading.defaulted


@pytest.mark.parametrize(
    ("question", "how"), [("most total points by a center this season", "total"), ("most points by a center this season", None), ("Lyngby bk total corners last 5 games average", "total")]
)
def test_a_total_asked_on_the_game_level_ranking_is_the_measures_own(con: duckdb.DuckDBPyConnection, question: str, how: str | None) -> None:
    """ "total" on the league's game-level ranking is how the measure is
    asked (``Measure.how``): the point reader writes it on its point's
    measure, which the planner derives the ranking's sum from (D5) - a
    measure of no key where the words name none the catalog holds."""
    reading = _read(con, question)
    assert reading.point is not None and reading.point.read_as == PointShape("player_games", "ranking", "player")
    assert reading.point.measure is not None and reading.point.measure.how == how


@pytest.mark.parametrize(
    ("question", "words"),
    [
        ("Highest single season 3pt percent", "single season"),
        ("most 60 points games on a single season", "on a single season"),
        ("most assists in a season by a 76ers power forward in the 2000s", "in a season"),
        ("most missed shots in a season", "in a season"),
    ],
)
def test_the_best_single_season_across_years_is_refused_by_name(con: duckdb.DuckDBPyConnection, question: str, words: str) -> None:
    """The league's best single season across years ranks player-seasons
    from every year, which no reader ranks - never the current season alone
    or the career list. It is refused naming the words; a season named
    reads that season."""
    reading = _read(con, question)
    assert reading.point is None
    assert reading.point_refusal is not None and reading.point_refusal.kind == "single_season_ranking"
    assert reading.point_refusal.facts["words"] == words


def test_the_best_single_season_of_a_named_season_reads_that_season(con: duckdb.DuckDBPyConnection) -> None:
    """The words name the one season: no refusal, its leaders."""
    reading = _read(con, "highest single season 3pt percent in 2016")
    assert reading.point_refusal is None and reading.point is not None
    assert reading.span.season == 2016


@pytest.mark.parametrize(
    ("question", "how"),
    [
        ("highest career ppg", "per_game"),
        ("highest career points per game", "per_game"),
        ("who averaged the most points in his career", "per_game"),
        ("how many assist did Wilt average for his career", "per_game"),
        ("most career points", None),
    ],
)
def test_a_career_ranking_asked_per_game_ranks_the_per_game_figure(con: duckdb.DuckDBPyConnection, question: str, how: str | None) -> None:
    """A career ranking reads "ppg", "per game" and "averaged": asked per
    game ("highest career ppg"), the measure is the per-game figure (the
    season line's career points per game); unasked, the career total."""
    reading = _read(con, question, [], "assists" if "assist" in question else "points")
    assert reading.point is not None and reading.point.read_as == PLAYER_RANKING
    assert reading.point.measure is not None and reading.point.measure.how == how


@pytest.mark.parametrize(
    "question",
    ["best shooting percentages 2023", "list nba players most wins in a career", "Cousins player efficiency rating by year", "most career wins"],
)
def test_a_players_ranking_with_no_measure_read_is_refused_naming_the_stat(con: duckdb.DuckDBPyConnection, question: str) -> None:
    """#421: where the words name a measure nothing reads and the model none
    ("best shooting percentages", "most wins", "efficiency rating"), the
    ranking's points default is a cause the planner says where the ranking
    would answer."""
    reading = _read(con, question)
    assert reading.defaulted is not None and reading.defaulted.kind == "ranking_needs_stat"


@pytest.mark.parametrize("question", ["nba players who averaged 25 ppg in 2001", "most ppg in the 2024 playoffs", "top 10 scorers in 2024", "who led the league in assists in 2019"])
def test_a_players_ranking_whose_words_name_its_stat_is_no_default(con: duckdb.DuckDBPyConnection, question: str) -> None:
    """A season's line, a per-game abbreviation and a stat word each name
    the ranking's stat: no cause, and "ppg" reads points per game."""
    reading = _read(con, question)
    assert reading.defaulted is None
    assert reading.point is not None and reading.point.measure is not None and reading.point.measure.key is not None


@pytest.mark.parametrize(("question", "stat"), [("most triple doubles since 2020", "triple_double"), ("most double doubles 2020-2023", "double_double")])
def test_a_yes_no_measure_over_a_range_of_seasons_ranks_each_players_count(con: duckdb.DuckDBPyConnection, question: str, stat: str) -> None:
    """#421: "most triple doubles since 2020" is each player's count of the
    games that hold it over the range - the season line counts one season
    or a whole career, so the point is the game-level ranking with the
    predicate kept."""
    reading = _read(con, question)
    assert reading.point is not None and reading.point.read_as == PointShape("player_games", "ranking", "count")
    assert reading.point.predicates == [(stat, "=", True)]
    assert reading.defaulted is None


@pytest.mark.parametrize(
    ("question", "names", "career"),
    [
        ("jokic stats by season", ["Nikola Jokic"], False),
        ("lebron nba stats by year", ["LeBron James"], False),
        ("Jokic games play each season", ["Nikola Jokic"], True),
        ("lebron stats every season", ["LeBron James"], True),
    ],
)
def test_a_history_with_no_stat_named_is_the_whole_line_season_by_season(con: duckdb.DuckDBPyConnection, question: str, names: list[str], career: bool) -> None:
    """#387: "james wiseman stats by season" is no single season's line:
    asked season by season, it is the whole line's history (points, rebounds
    and assists by season); "every season" and "each season" are every
    season on record, not the history's default four."""
    reading = _read(con, question, names)
    assert reading.asked == SEASON_HISTORY
    assert reading.point is not None and [m.key for m in reading.point.measures] == ["points", "rebounds", "assists"]
    assert reading.span.career is career


def test_career_averages_stay_the_line(con: duckdb.DuckDBPyConnection) -> None:
    """ "career averages" with no "by season" is the whole career line."""
    assert _read(con, "Jokic career averages", ["Nikola Jokic"]).asked == PLAYER_LINE


def test_a_history_by_a_per_game_abbreviation_reads_its_stat(con: duckdb.DuckDBPyConnection) -> None:
    """ "ppg" names the history's stat: points by season, not the line."""
    reading = _read(con, "sga ppg avarage in the last 4 seasons", ["Shai Gilgeous-Alexander"])  # codespell:ignore avarage - the feed's question as typed
    assert reading.measure is not None and reading.measure.key == "points"


@pytest.mark.parametrize(
    ("question", "names", "stat"),
    [
        ("How many points did Jokic average in November 2023", ["Jokic"], "points"),
        ("Jokic averages Feb 28th to April 14th 2026", ["Jokic"], ""),
        ("jokic averages since 12/11/2023", ["Jokic"], ""),
    ],
)
def test_a_per_game_word_beside_a_month_or_a_date_is_the_line_not_the_log(con: duckdb.DuckDBPyConnection, question: str, names: list[str], stat: str) -> None:
    """#412: "How many points did De'aaron fox average in November 2023" is
    his line, not a log of his November games: with a per-game word beside
    it the month or the date cuts the line."""
    reading = _read(con, question, names, stat)
    assert reading.asked == PLAYER_LINE


def test_a_month_with_no_per_game_word_is_still_the_log(con: duckdb.DuckDBPyConnection) -> None:
    """ "jokic games in november 2023" lists the games."""
    assert _read(con, "jokic game log november 2023", ["Jokic"]).asked == PLAYER_LOG


@pytest.mark.parametrize("question", ["warriors record in the first 15 games last 10 season", "lebron first 10 games last 2 seasons"])
def test_a_seasons_first_games_beside_a_count_of_seasons_is_a_window_per_season(con: duckdb.DuckDBPyConnection, question: str) -> None:
    """#432: "warriors record in the first 15 games last 10 season" is a
    window per season, which the reader refuses by name as "every season"
    beside one is - never the range's first 15 games."""
    reading = _read(con, question)
    assert reading.refused is not None and reading.refused.kind == "window_per_season"


@pytest.mark.parametrize("question", ["threes allowed by the celtics this season", "celtics opp 3pm this season"])
def test_a_teams_figure_given_up_is_the_team_compilers_total(con: duckdb.DuckDBPyConnection, question: str) -> None:
    """#425: the opponents' threes are the team compiler's total, which sums
    the opponents' box rows - not the team's season line, which has no
    opponent metric."""
    reading = _read(con, question)
    assert reading.point is not None and reading.point.relation == "team"
    assert reading.measure is not None and reading.measure.whose == "opponent"


@pytest.mark.parametrize(
    ("question", "names", "read_as"),
    [
        ("most 30+ point triple doubles", [], PointShape("player_games", "ranking", "count")),
        ("most 30 point triple doubles in the last 3 years", [], PointShape("player_games", "ranking", "count")),
        ("how many 30 point triple doubles does jokic have", ["Nikola Jokic"], GAMES_COUNTED),
    ],
)
def test_a_count_of_a_yes_no_measure_over_a_line_keeps_both(con: duckdb.DuckDBPyConnection, question: str, names: list[str], read_as: PointShape) -> None:
    """#437: "most 30+ point triple doubles" counts the games holding both:
    the count's child grammar reads "N stat triple doubles" as its games,
    and the line is a second predicate on the same game."""
    reading = _read(con, question, names)
    assert reading.point is not None and reading.point.read_as == read_as
    assert ("triple_double", "=", True) in reading.point.predicates and ("points", ">=", 30) in reading.point.predicates


@pytest.mark.parametrize(
    ("question", "words"),
    [
        ("most fgm + opp fgm in a game by Knicks this season", "fgm + opp fgm"),
        ("knicks fgm + opp fgm per game this season", "fgm + opp fgm"),
        ("highest ppg*ts% by a team in a season", "ppg*ts%"),
        ("fewest field goal misses + opp fga minus opp fgm by knicks 2025", "fga minus opp fgm"),
    ],
)
def test_two_measures_combined_are_refused_naming_both(con: duckdb.DuckDBPyConnection, question: str, words: str) -> None:
    """#424: a figure combined from two measures ("most fgm + opp fgm in a
    game by Knicks this season") is read by nothing, and refused naming
    both - never the first measure alone."""
    reading = _read(con, question)
    assert reading.point_refusal is not None and reading.point_refusal.kind == "measures_combined"
    assert reading.point_refusal.facts["words"] == words


@pytest.mark.parametrize("question", ["jokic points+rebounds game log", "lebron +/- this season", "most 20+ point games"])
def test_a_log_of_two_columns_and_a_plus_sign_are_no_combined_measure(con: duckdb.DuckDBPyConnection, question: str) -> None:
    """A log shows each game's columns; "+/-" and "20+" are no pair."""
    reading = _read(con, question, ["Nikola Jokic"] if "jokic" in question else None)
    assert reading.point_refusal is None or reading.point_refusal.kind != "measures_combined"
