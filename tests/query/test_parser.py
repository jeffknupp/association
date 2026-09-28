"""The parser (query/parse.py): a question read into one Reading with no
router - ROADMAP plan item 6, step (b). The measurements live in
``~/association-research/parser-greenfield/`` (``measure.py``); these are the
rules' own cases on the small fixture warehouse."""

from __future__ import annotations

from typing import Any

import duckdb
import pytest

from association.query.decisions import Decision
from association.query.parse import classify_span, measure, parent_intent, read_route, reading_from_route, window
from association.query.reading import Reading
from association.query.router import Route, _threshold_from_text, settle
from association.query.templates.common import TemplateUnsupported, check_scope


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
    subject (ISSUES.md #236): a division, the word "team" and a typo two
    players could be are nobody's; a single near spelling is that player
    (the typo policy: the index defaults, visibly, never the model)."""
    assert classify_span(con, "sixers") == "team" and classify_span(con, "Boston Celtics") == "team" and classify_span(con, "PHI") == "team"
    assert classify_span(con, "Embiid") == "player" and classify_span(con, "maxey") == "player"
    assert classify_span(con, "Embid") == "player" and classify_span(con, "embids") == "player"
    assert classify_span(con, "jolic") is None  # Jokic or Jovic: two near spellings ask, as they always did
    assert classify_span(con, "southeast division") is None and classify_span(con, "team") is None


def test_the_measure_grammar_reads_the_words_before_the_models_key() -> None:
    assert measure("who were the top 10 in defensive netpoints / 100 possessions") == "netpoints_defense_per_100"
    assert measure("who led the league in offensive netpoints?") == "netpoints_offense"
    assert measure("who are the top 50 in total adjusted netpoints") == "netpoints_per_100"
    assert measure("what were SGA's netpoint stats this season") == "netpoints"
    assert measure("KNICKS point differential over the last 7 games") == "points_differential"
    assert measure("show me lebron's 2pt percentage for the past 5 years") == "twoPointFieldGoalPct"
    assert measure("Top 5 scorers on the Lakers?") == "points"
    assert measure("who had the highest avg rebounds") == "rebounds"
    assert measure("what did Nikola Jokic do in his last game?") is None
    # A three is its own column, never the "point" in it.
    assert measure("davion mitchell 3 point stats") == "threePointFieldGoalsMade"
    assert measure("vj edgecombe three points made per game") == "threePointFieldGoalsMade"
    assert measure("Trailblazers 3-point average in the first quarter") == "threePointFieldGoalsMade"
    assert measure("klay thompson 3 point attempts per game") == "threePointFieldGoalsAttempted"
    assert measure("show me lebron's 3pt percentage for the past 5 years") == "threePointFieldGoalPct"
    # What a team gives up is the opponent's line.
    assert measure("rebounds allowed per team") == "rebounds allowed"
    assert measure("which team allowed the most points per game") == "points allowed"
    assert measure("derozan's total points against the knicks") == "points"
    # "to" is a word before it is a turnover count.
    assert measure("25-26 Knicks playoff stats compared to other historical teams") is None
    assert measure("who averages the most TO per game") == "turnovers"


def test_the_window_grammar_reads_the_count_and_the_end() -> None:
    assert window("How did the Celtics do in their last 10 games?", {"order": "recent"}) == {"order": "recent", "limit": 10}
    assert window("what did Nikola Jokic do in his last game?", {}) == {"order": "recent", "limit": 1}
    assert window("What was Curry's first game of the season?", {}) == {"order": "first", "limit": 1}
    assert window("top 5 rebounders on the Lakers in the playoffs", {}) == {"limit": 5}
    # "who led the league" sets no limit: the ranking leads with the one and names the next.
    assert window("who led the league in assists this season?", {}) == {}
    assert window("magic vs nets last 10", {}) == {"order": "recent", "limit": 10}
    assert window("lakers vs mavs record last 10 home games played", {}) == {"order": "recent", "limit": 10}
    assert window("Steph Curry's final two regular season games", {}) == {"order": "recent", "limit": 2}
    assert window("Luka's ppg over the last 10 seasons", {}) == {}
    assert window("Jrue holiday last fifty games as a starter", {}) == {"order": "recent", "limit": 50}
    # A limit the stages already set stands.
    assert window("show the top 50 in total adjusted netpoints", {"limit": 3}) == {"limit": 3}
    assert window("how many points does embiid average", {}) == {}


def test_the_parent_grammar_by_kind_and_words() -> None:
    assert parent_intent("show sga fingerprint for this season", "player") == "fingerprint"
    # A player's own record is his games' W-L (F088, "Embiid's record against Boston this year"): player_splits, never
    # the team's with/without split; a line in it is record_when, which player_stat's reading assigns.
    assert parent_intent("Embiid's record against Boston this year", "player") == "player_splits"
    assert parent_intent("Sga record 36 plus points", "player") == "player_stat"
    assert parent_intent("36-plus points SGA record", "player") == "player_stat"
    assert parent_intent("In his most recent game, what did Nikola Jokic accomplish?", "player") == "game_log"
    assert parent_intent("Last season's threes by Plot Curry", "everyone") == "shot_chart"
    assert parent_intent("25-26 Knicks playoff stats compared to other historical teams", "team") == "team_outlook"
    # A team's triple-doubles are its players'.
    assert parent_intent("oklahoma city thunder all-time triple doubles vs west", "team") == "leaderboard"
    assert parent_intent("how many points does embiid average", "player") == "player_stat"
    assert parent_intent("what did Nikola Jokic do in his last 5 games?", "player") == "game_log"
    assert parent_intent("lebron vs kawhi 2015", "pair") == "player_matchup"
    assert parent_intent("evaluate sga against embiid", "pair") == "player_compare"
    assert parent_intent("Lakers vs Celtics record this season", "teams") == "head_to_head"
    assert parent_intent("What was the Lakers record last season?", "team") == "team_record"
    assert parent_intent("Display the Knicks' previous 5 games", "team") == "game_log"
    # with_without needs a companion the reading found: "when playing away" names nobody.
    assert parent_intent("Record of the 76ers when playing away", "team") == "team_record"
    assert parent_intent("PHI's record when Embiid and Paul George are both in the game", "team", companions=True) == "with_without"
    assert parent_intent("westbrook's stats when he started for the kings", "player") == "player_stat"
    assert parent_intent("Best record from 2010-11 to 2018-19 nba", "everyone") == "team_leaderboard"
    assert parent_intent("who led the league in assists in 2019", "everyone") == "leaderboard"
    assert parent_intent("show sixers first quarter scoring for their last 10 games", "team") == "team_quarter_points"


def _read(con: duckdb.DuckDBPyConnection, question: str, names: list[str] | None = None, stat: str = "") -> Reading:
    """The parser's whole path, as the agent takes it: the route the words
    settle (:func:`read_route`), read into the Reading
    (:func:`reading_from_route`)."""
    route, _, _ = read_route(con, question, names, stat)
    return reading_from_route(con, question, route)


def test_the_parser_reads_a_question_into_a_reading(con: duckdb.DuckDBPyConnection) -> None:
    """The whole path on the fixture: names classified, the kind and parent
    read, the window and the measure from the words, the child assigned."""
    r = _read(con, "How many 30+ point games did Jokic have this season?", names=["Jokic"], stat="points")
    assert r.intent == "threshold_count" and r.subject is not None and r.subject.kind == "player" and r.subject.players == ("Nikola Jokic",)
    assert r.scope.threshold == 30 and r.scope.stat == "points"
    r = _read(con, "Lakers vs Celtics record this season", names=["Lakers", "Celtics"], stat="")
    assert r.intent == "head_to_head" and r.subject is not None and r.subject.kind == "teams"
    # A window over the two teams' meetings is still their meetings when a record is asked for; a log word is one team's games.
    r = _read(con, "lakers vs celtics record last 10 home games played", names=["lakers", "celtics"], stat="")
    assert r.intent == "head_to_head" and r.subject is not None and r.subject.kind == "teams"
    r = _read(con, "lakers game log vs celtics last 10", names=["lakers", "celtics"], stat="")
    assert r.subject is not None and r.subject.kind == "team"
    r = _read(con, "who were the top 10 in defensive netpoints / 100 possessions", names=[], stat="")
    assert r.subject is not None and r.subject.kind == "everyone" and r.scope.stat == "netpoints_defense_per_100" and r.scope.limit == 10
    # A span no player or team has never becomes a subject.
    r = _read(con, "alperen sengun double-doubles vs southeast division career away", names=["alperen sengun", "southeast division"], stat="")
    assert r.subject is not None and r.subject.kind != "pair"


def test_the_parser_with_no_names_and_no_stat_still_reads_the_question(con: duckdb.DuckDBPyConnection) -> None:
    r = _read(con, "what was the sixers record when maxey scored 20+ points?")
    assert r.subject is not None and r.subject.kind == "team" and r.subject.teams == ("Philadelphia 76ers",)
    assert r.intent == "record_when"
    assert r.scope.threshold == 20


def test_a_player_against_a_team_is_never_a_matchup(con: duckdb.DuckDBPyConnection) -> None:
    """The router dressed one player against a team as a two-player matchup -
    with the team, or a garbled spelling of it, or a teammate named as absent
    in the second player's slot ("oubre vs warriors without embiid", "de'aaron
    fox vs magic without wembyanama"), and ``player_matchup`` carried two
    repairs to fold each back into the player's own games. A matchup is read
    only where the question names two players (the pair kind), so neither
    shape reaches it; the repairs are gone (4.5.0)."""
    for question, names in (
        ("maxey vs celtics without embiid", ["maxey", "celtics", "embiid"]),
        ("maxey vs celtics without embiid", ["maxey", "Celtcs", "embiid"]),
        ("maxey vs celtics without embiid", ["maxey", "Joel Embiid"]),
        ("tyrese maxey v bos", ["tyrese maxey", "bos"]),
    ):
        r = _read(con, question, names)
        assert r.intent != "player_matchup" and r.subject is not None and r.subject.kind == "player", (question, names, r.intent)
        assert r.scope.player == "Tyrese Maxey" and r.scope.opponent == "Boston Celtics", (question, names, r.scope)
    r = _read(con, "maxey vs embiid", ["maxey", "embiid"])
    assert (r.intent, r.subject.kind if r.subject else None) == ("player_matchup", "pair")


def test_read_route_takes_the_names_from_the_subject_reading(con: duckdb.DuckDBPyConnection) -> None:
    """The model's spans are where the reading starts, not what the slots
    hold (ROADMAP plan item 6, step c): a name it dropped that the question
    holds is the reading's; a companion is a narrowing, never a second
    player; a team beside a player is his opponent; a team code is the
    team; an ambiguous span the reading could not settle is kept as typed,
    for the template to ask about."""
    dropped, _, _ = read_route(con, "plot the fingerprint for Nikola Jokic for the 2025 season", [], "")
    assert dropped.slots["player"] == "Nikola Jokic"
    companion, subject, _ = read_route(con, "Tyrese Maxey game log without Joel Embiid", ["Tyrese Maxey", "Joel Embiid"], "")
    assert companion.slots["player"] == "Tyrese Maxey" and "players" not in companion.slots and subject.companions
    against, _, _ = read_route(con, "show maxey's games against boston", ["maxey", "boston"], "")
    assert against.slots["opponent"] == "Boston Celtics" and "team" not in against.slots
    code, _, _ = read_route(con, "PHI record 2026", ["PHI"], "")
    assert (code.intent, code.slots.get("team"), code.slots.get("player")) == ("team_record", "Philadelphia 76ers", None)
    kept, _, _ = read_route(con, "who is better, tatum or nikola", ["tatum", "nikola"], "")
    assert kept.slots["players"] == ["Jayson Tatum", "nikola"]
    # The model's names were only the companions: they narrow, and are
    # nobody's subject.
    only, subject, _ = read_route(con, "game log this season without Joel Embiid and Tyrese Maxey", ["Joel Embiid", "Tyrese Maxey"], "")
    assert subject.companions and "player" not in only.slots and "players" not in only.slots


def test_a_conference_or_a_pronoun_is_never_a_name(con: duckdb.DuckDBPyConnection) -> None:
    """ "vs west" is the conference, though four Wests played; "someone" is one
    near spelling from Simone Fontecchio, and a single near spelling defaults -
    the fuzzy-word trap AGENTS.md records, arriving through the model's spans."""
    con.execute("INSERT INTO players VALUES ('8', 'Delonte West'), ('9', 'David West'), ('10', 'Simone Fontecchio')")
    assert classify_span(con, "west") is None and classify_span(con, "the West") is None and classify_span(con, "someone") is None
    assert classify_span(con, "Delonte West") == "player"


def test_read_route_reads_the_window_before_the_stages_and_the_quarter_from_the_words(con: duckdb.DuckDBPyConnection) -> None:
    """A bare "last 10 games" reads both season types, which the stages
    decide only beside the window (``_route_game_log_recent_span``); a
    team's quarter is a slot the router's model used to fill."""
    recent, _, _ = read_route(con, "How did the Celtics do in their last 10 games?", ["Celtics"], "")
    assert (recent.intent, recent.slots["limit"], recent.slots.get("season_type_unstated")) == ("game_log", 10, True)
    quarter, _, _ = read_route(con, "show sixers first quarter scoring for their last 10 games", ["sixers"], "points")
    assert (quarter.intent, quarter.slots.get("period")) == ("team_quarter_points", 1)


def test_a_players_record_reads_his_splits_through_a_window_but_not_over_a_log_word() -> None:
    """ "rec" is a record, and a record narrowed by a date or a window is still
    his record (yardstick-v2 F110, "towns home rec including playoffs since
    1/26/20 vs spurs" read as a game log); a game log asked for is a log."""
    assert parent_intent("towns home rec including playoffs since 1/26/20 vs spurs", "player") == "player_splits"
    assert parent_intent("embiid's record in his last 10 games", "player") == "player_splits"
    assert parent_intent("embiid game log since 1/26/20", "player") == "game_log"
    assert parent_intent("embiid's game log and record vs boston since 1/26/20", "player") == "game_log"
    assert parent_intent("Sga record 36 plus points", "player") != "player_splits"


def test_the_hold_out_rows_the_router_answered_and_the_parser_did_not(con: duckdb.DuckDBPyConnection) -> None:
    """The recorded routing corpus outside day10 (75 questions no step (b) or
    (c) fix was tuned on), answered by both paths: each of these was a
    fluent wrong answer or a fall-through on the parser's alone."""
    # "%" takes no \b: "3pt %" read as 3-pointers made.
    assert measure("who had the highest 3pt % this season") == "threePointFieldGoalPct"
    # Attempts AND makes asked for: the made line reports both.
    assert measure("show embiid's 3pt attempts and 3pts mad for his career") == "threePointFieldGoalsMade"
    assert measure("embiid 3pt attempts per game") == "threePointFieldGoalsAttempted"
    # A team's opener is one game of its log, not its season line.
    assert parent_intent("Lakers opening game of the season", "team") == "game_log"
    assert window("Lakers opening game of the season", {}) == {"order": "first", "limit": 1}
    # A team's odds are its outlook, not its postseason stats.
    assert parent_intent("what are the sixers playoff odds?", "team") == "team_outlook"
    # A record with nobody named is the teams', not the league's scorers.
    assert parent_intent("worst record 2025-26", "everyone") == "team_leaderboard"
    assert parent_intent("who scored the most points this season", "everyone") == "leaderboard"


def test_read_route_reads_the_columns_a_ranking_asks_to_see(con: duckdb.DuckDBPyConnection) -> None:
    """ "with their rebounds and assists" and "the team they play for"
    (yardstick-v2 F017) are the leaderboard's `fields`, which the router's
    model used to fill; a ranking that asks for none gets none."""
    extra, _, _ = read_route(con, "Top 5 scorers with their rebounds and assists", [], "points")
    assert (extra.intent, extra.slots.get("fields"), extra.slots.get("limit")) == ("leaderboard", ["rebounds", "assists"], 5)
    team, _, _ = read_route(con, "show the top 50 in total adjusted netpoints and the team they play for", [], "netpoints")
    assert team.slots.get("fields") == ["team"]
    plain, _, _ = read_route(con, "who led the league in scoring", [], "points")
    assert "fields" not in plain.slots


def test_a_name_the_model_corrected_is_put_back_as_the_question_typed_it(con: duckdb.DuckDBPyConnection) -> None:
    """The model copies names exactly 299 times in 302 and corrects the
    rest: "how many points does embid average" came back "embiid", and the
    answer read Joel Embiid with nothing saying a typo was read. A typo is
    the entity index's to read, visibly, so the question's own spelling goes
    back in; a completion of a surname typed as-is is left alone."""
    typo, _, _ = read_route(con, "how many points does embid average", ["embiid"], "points")
    assert typo.slots["player"] == "embid"
    both, _, _ = read_route(con, "how many points does embidd average", ["Joel Embiid"], "points")
    assert both.slots["player"] == "embidd"
    completed, _, _ = read_route(con, "how many points does embiid average", ["Joel Embiid"], "points")
    assert completed.slots["player"] in ("Joel Embiid", "embiid")
    # A possessive "s" left on a typo ("embids" for "embid's") is the
    # question's own spelling too, as the index reads it: put back, it is a
    # player's history, where two edits from "embiid" it read as a name the
    # question never held and was refused (ISSUES.md #180).
    possessive, subject, _ = read_route(con, "show me embids 3pt percentage over the last 5 years", ["embiid"], "threePointFieldGoalPct")
    assert (possessive.intent, possessive.slots["player"], subject.invented) == ("player_history", "embids", ())


def test_a_count_spelled_out_is_the_count(con: duckdb.DuckDBPyConnection) -> None:
    """One number table, every count pattern built from it: "last twelve
    games" read as a season line while "last 12 games" read the log (the
    package review's F2)."""
    for spelled, n in (("twelve", 12), ("eleven", 11), ("twenty five", 25), ("twenty-five", 25), ("fifty", 50)):
        route, _, _ = read_route(con, f"tyrese maxey last {spelled} games", ["tyrese maxey"], "")
        assert (route.intent, route.slots.get("order"), route.slots.get("limit")) == ("game_log", "recent", n), spelled
    assert window("top twelve scorers", {}) == {"limit": 12}


# ---------------------------------------------------------------------------
# A companion beside the subject, with the role the question gives him (ROADMAP
# plan item 6, step (d) follow-ups). The route is what these assert - the
# intent and the slots - never an answer: `player_stat`'s reading of a
# condition is another change.


def _settled(con: duckdb.DuckDBPyConnection, question: str, names: list[str], stat: str = "") -> tuple[str, dict[str, Any]]:
    """The route :func:`read_route` settles, then the parser's last step
    (:func:`reading_from_route`), which writes a companion's role as the
    ``conditions`` slot - the Reading the agent answers from, as slots."""
    route, _, _ = read_route(con, question, names, stat)
    reading = reading_from_route(con, question, route)
    return reading.intent, reading.scope.to_slots()


def _with_the_warriors(con: duckdb.DuckDBPyConnection) -> None:
    con.executemany("INSERT INTO players VALUES (?, ?)", [("20", "Stephen Curry"), ("21", "Draymond Green")])
    con.execute("INSERT INTO teams VALUES ('9', 'Golden State Warriors', 'GS')")


def test_a_teammates_start_is_his_condition_on_the_subjects_own_intent(con: duckdb.DuckDBPyConnection) -> None:
    """ "maxey points when embiid starts" read Embiid's start as Maxey's own
    starter split, under the with/without row, and fell through
    ("with_without cannot honor ['split']"); "maxey points in games embiid
    started" read the two as a pair and compared them. A teammate's start or
    bench is his condition, on the subject's own intent - never the subject's
    split, never a second subject."""
    started = [{"player": "Joel Embiid", "side": "own", "predicate": "started"}]
    for question, stat, intent in (
        ("maxey points when embiid starts", "points", "player_stat"),
        ("how many points does maxey average when embiid starts", "points", "player_stat"),
        ("maxey points when embiid started", "points", "player_stat"),
        ("maxey points in games embiid started", "points", "player_stat"),
        ("maxey game log when embiid starts", "", "game_log"),
        ("maxey game log in games embiid started", "", "game_log"),
    ):
        route, subject, _ = read_route(con, question, ["maxey", "embiid"], stat)
        assert (route.intent, subject.kind, route.slots["player"]) == (intent, "player", "Tyrese Maxey"), question
        assert "split" not in route.slots and "players" not in route.slots, question
        assert _settled(con, question, ["maxey", "embiid"], stat) == (intent, {**route.slots, "conditions": started}), question
    route, _, _ = read_route(con, "maxey stats when embiid comes off the bench", ["maxey", "embiid"], "")
    assert route.intent == "player_stat" and "split" not in route.slots
    assert _settled(con, "maxey stats when embiid comes off the bench", ["maxey", "embiid"])[1]["conditions"] == [{"player": "Joel Embiid", "side": "own", "predicate": "bench"}]
    # A child the words assign reads it too: the compiler answers a count first and narrows by every condition.
    settled, slots = _settled(con, "how many 30 point games did maxey have when embiid started", ["maxey", "embiid"], "points")
    assert (settled, slots["threshold"], slots["conditions"]) == ("threshold_count", 30, started)


def test_a_teammates_start_narrows_a_shot_chart_or_a_distance_not_the_players_own_starts(con: duckdb.DuckDBPyConnection) -> None:
    """ "stephen curry shot chart when draymond green starts" drew Curry's
    own starts, and "... average shot distance when draymond green starts"
    fell through on the same split."""
    _with_the_warriors(con)
    for question, intent in (("stephen curry shot chart when draymond green starts", "shot_chart"), ("stephen curry average shot distance when draymond green starts", "shot_distance")):
        route, _, _ = read_route(con, question, ["stephen curry", "draymond green"], "")
        assert route.intent == intent and "split" not in route.slots, question
        assert _settled(con, question, ["stephen curry", "draymond green"])[1]["conditions"] == [{"player": "Draymond Green", "side": "own", "predicate": "started"}], question


def test_the_subjects_own_split_still_reads_beside_a_teammates_role(con: duckdb.DuckDBPyConnection) -> None:
    """Only the teammate's phrase is his: the subject's own start still
    reads as his split, a team's split by a teammate's start is
    with_without's own side, and a start the question denies stays the
    with/without split, whose other half is the one asked for - as a
    condition it would narrow to the very games the question excludes."""
    route, _, _ = read_route(con, "maxey points as a starter when embiid comes off the bench", ["maxey", "embiid"], "points")
    assert (route.intent, route.slots["split"]) == ("player_stat", "starter")
    route, _, _ = read_route(con, "maxey points when he starts", ["maxey"], "points")
    assert (route.intent, route.slots["split"]) == ("player_stat", "starter")
    route, _, _ = read_route(con, "76ers record when embiid starts", ["76ers", "embiid"], "")
    assert route.intent == "with_without" and "split" not in route.slots
    route, _, _ = read_route(con, "maxey points when embiid doesn't start", ["maxey", "embiid"], "points")
    assert route.intent == "with_without"
    # Where nothing reads the start as a condition, the question is refused - never answered for every game.
    route, _, _ = read_route(con, "sixers first quarter points when embiid starts", ["sixers", "embiid"], "points")
    assert route.intent == "team_quarter_points"
    with pytest.raises(TemplateUnsupported):
        check_scope(route.intent, route.slots)


def test_a_companion_who_sat_out_is_without_and_out_is_no_part_of_his_name(con: duckdb.DuckDBPyConnection) -> None:
    """ "with draymond green out" was read and written to no slot - the
    whole season charted, his whole log listed - or read by "with" as a
    teammate who PLAYED, named "draymond green out", which asked whether Bo
    or Travis Outlaw was meant. It is ``without``, the way "without X" is."""
    _with_the_warriors(con)
    for question, names, intent in (
        ("stephen curry shot chart with draymond green out", ["stephen curry", "draymond green"], "shot_chart"),
        ("stephen curry game log with draymond green out", ["stephen curry", "draymond green"], "game_log"),
        ("stephen curry stats with draymond green out", ["stephen curry", "draymond green"], "with_without"),
        ("warriors record with draymond green out", ["warriors", "draymond green"], "with_without"),
    ):
        route, _, _ = read_route(con, question, names, "")
        assert (route.intent, route.slots["without"]) == (intent, ["draymond green"]) and "with_player" not in route.slots, question
    route, _, _ = read_route(con, "maxey points when embiid doesn't play", ["maxey", "embiid"], "points")
    assert (route.intent, route.slots["without"]) == ("with_without", ["embiid"]) and "with_player" not in route.slots
    # And on a log he is absent, never a teammate who played beside the absence.
    settled, slots = _settled(con, "maxey game log when embiid doesn't play", ["maxey", "embiid"])
    assert (settled, slots["without"]) == ("game_log", ["embiid"]) and "conditions" not in slots
    # Never a pair: "in games X missed" is X's absence from the subject's games.
    route, subject, _ = read_route(con, "maxey points in games embiid missed", ["maxey", "embiid"], "points")
    assert (route.intent, subject.kind, route.slots["without"]) == ("player_stat", "player", ["embiid"])


def test_a_record_with_a_teammate_out_is_the_split_from_the_other_side() -> None:
    """The stages the parser runs through ``settle``, under the intents a
    record with no line arrives as: "with X out" is the with/without split
    by his absence - never a teammate who played, named "draymond green
    out" or "draymond green" with the absence dropped."""
    for intent in ("record_when", "team_record"):
        route = settle(intent, {"team": "Golden State Warriors", "season_type": 2}, "warriors record with draymond green out")
        assert (route.intent, route.slots.get("without"), route.slots.get("with_player")) == ("with_without", ["draymond green"], None), intent


def test_a_companion_who_played_is_a_condition_where_with_player_is_read_by_nothing(con: duckdb.DuckDBPyConnection) -> None:
    """ "stephen curry shot chart when draymond green plays" was read and
    written to no slot. ``with_player`` is with_without's alone, so on a log
    or a chart the role is the relation's own ``played`` condition; the
    with/without split keeps its ``with_player``."""
    _with_the_warriors(con)
    names = ["stephen curry", "draymond green"]
    played = [{"player": "Draymond Green", "side": "own", "predicate": "played"}]
    for question, intent in (("stephen curry shot chart when draymond green plays", "shot_chart"), ("stephen curry game log when draymond green plays", "game_log")):
        settled, slots = _settled(con, question, names)
        assert (settled, slots["conditions"]) == (intent, played) and "with_player" not in slots, question
    settled, slots = _settled(con, "stephen curry stats with draymond green playing", names)
    assert (settled, slots["with_player"]) == ("with_without", ["draymond green"]) and "conditions" not in slots


def test_a_number_after_a_scoring_verb_is_a_line_on_points(con: duckdb.DuckDBPyConnection) -> None:
    """ "celtics record when jayson tatum scores 30" read no threshold - no
    stat word follows the number - and was refused as a question about a
    team named Jayson Tatum. The verb names the stat."""
    for question, stat, since in (
        ("celtics record when jayson tatum scores 30", "points", None),
        ("celtics record when jayson tatum scores 30 since 2022", "points", 2022),
        ("celtics record when jayson tatum scored 30", "", None),
    ):
        route, subject, _ = read_route(con, question, ["celtics", "jayson tatum"], stat)
        assert (route.intent, route.slots["stat"], route.slots["threshold"], route.slots.get("since")) == ("record_when", "points", 30, since), question
        assert subject.kind == "team" and subject.teams == ("Boston Celtics",), question
    route, _, _ = read_route(con, "how many times has embiid scored 40", ["embiid"], "points")
    assert (route.intent, route.slots["threshold"]) == ("threshold_count", 40)
    # A companion's line reads the same way: "when embiid scores 30" is a 30-point condition.
    assert _settled(con, "maxey points when embiid scores 30", ["maxey", "embiid"], "points")[1]["conditions"] == [
        {"player": "Joel Embiid", "side": "own", "predicate": "reached", "stat": "points", "threshold": 30}
    ]
    # Not a line: a stat word after the number is that stat's, a rate is an average, a year is a year.
    assert _threshold_from_text("when he scored 12 rebounds") == 12 and _threshold_from_text("tatum scored 3 threes") == 3
    for question in ("players who score 30 a game", "who scored 30 per game", "since he scored 2022", "scored 30.5 on average"):
        assert _threshold_from_text(question) is None, question


# ---------------------------------------------------------------------------
# The intent the parser's words assign, as decisions (ISSUES.md #258): a child
# or a stage's intent was a trace line only, so the answer's decisions showed
# who a question was about but never that "how many 30+ point games" was read
# as a count rather than a game log.


def test_each_move_of_the_intent_off_the_grammars_parent_is_a_decision(con: duckdb.DuckDBPyConnection) -> None:
    """The three ways the intent leaves the parent the grammar named, each
    with its own reason, and every slot a child's stages moved: the words
    naming a child for the subject's kind, the stages settling another
    intent, a team's record in the games a companion reached a line. A
    question whose intent is the parent's carries none."""
    route, _, _ = read_route(con, "How many times did embiid score 30+ points", ["embiid"], "points")
    assert route.decisions == (
        Decision("parser", "intent", "game_log", "threshold_count", "the words 'How many times did embiid score 30+' name threshold_count"),
        Decision("parser", "span", None, "career", "read for threshold_count"),
        Decision("parser", "threshold", None, 30, "read for threshold_count"),
    )
    route, _, _ = read_route(con, "rebounds allowed per team", [], "rebounds")
    assert route.decisions == (Decision("parser", "intent", "leaderboard", "team_leaderboard", "the stages settle it from the question's words"),)
    route, subject, _ = read_route(con, "what was the sixers record when maxey scored 15+ points?", ["sixers", "maxey"], "points")
    team_rule = "a team's record in the games a player named beside it reached a line"
    assert route.decisions[0] == Decision("parser", "intent", "with_without", "record_when", team_rule) and subject.intent_reason == team_rule
    assert [(d.field, d.after) for d in route.decisions[1:]] == [("player", "maxey"), ("threshold", 15)]
    route, subject, _ = read_route(con, "how many points does embiid average", ["embiid"], "points")
    assert (route.intent, route.decisions, subject.intent_reason) == ("player_stat", (), None)


def test_the_reading_carries_the_parsers_decisions_between_who_and_what_it_wrote(con: duckdb.DuckDBPyConnection) -> None:
    """The Reading's decisions read in order: who the question is about, how
    the parser moved the intent, then what the subject wrote into the scope
    (here the question's own spelling of the companion, "maxey" read as
    Tyrese Maxey) - and a replayed route, which the parser never read,
    carries none of the parser's. The team rule's own decision is only where
    it moves the player: it used to read the slot after rewriting it, and
    said "subject player: 'maxey' -> 'maxey'" on every such question."""
    question = "what was the sixers record when maxey scored 15+ points?"
    route, _, _ = read_route(con, question, ["sixers", "maxey"], "points")
    decisions = reading_from_route(con, question, route).decisions
    assert [(d.stage, d.field) for d in decisions] == [
        ("subject", "kind"),
        ("subject", "teams"),
        ("subject", "companions"),
        ("parser", "intent"),
        ("parser", "player"),
        ("parser", "threshold"),
        ("subject", "player"),
    ]
    assert (decisions[-1].before, decisions[-1].after) == ("maxey", "Tyrese Maxey")
    replayed = reading_from_route(con, question, Route(route.intent, {**route.slots, "player": "Tyrese Maxey"}))
    assert [(d.stage, d.field) for d in replayed.decisions] == [("subject", "kind"), ("subject", "teams"), ("subject", "companions")]
