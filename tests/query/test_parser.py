"""The parser (query/parse.py): a question read into one Reading with no
router - ROADMAP plan item 6, step (b). The measurements live in
``~/association-research/parser-greenfield/`` (``measure.py``); these are the
rules' own cases on the small fixture warehouse."""

from __future__ import annotations

from typing import Any

import duckdb
import pytest

from association.query.parse import classify_span, measure, parent_intent, parse, read_route, window


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


def test_the_window_grammar_reads_the_count_and_the_end() -> None:
    assert window("How did the Celtics do in their last 10 games?", {"order": "recent"}) == {"order": "recent", "limit": 10}
    assert window("what did Nikola Jokic do in his last game?", {}) == {"order": "recent", "limit": 1}
    assert window("What was Curry's first game of the season?", {}) == {"order": "first", "limit": 1}
    assert window("top 5 rebounders on the Lakers in the playoffs", {}) == {"limit": 5}
    assert window("who led the league in assists this season?", {}) == {"limit": 1}
    assert window("Jrue holiday last fifty games as a starter", {}) == {"order": "recent", "limit": 50}
    # A limit the stages already set stands.
    assert window("show the top 50 in total adjusted netpoints", {"limit": 3}) == {"limit": 3}
    assert window("how many points does embiid average", {}) == {}


def test_the_parent_grammar_by_kind_and_words() -> None:
    assert parent_intent("show sga fingerprint for this season", "player") == "fingerprint"
    assert parent_intent("Embiid's record against Boston this year", "player") == "with_without"
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


def test_parse_reads_a_question_into_a_reading(con: duckdb.DuckDBPyConnection) -> None:
    """The whole path on the fixture: names classified, the kind and parent
    read, the window and the measure from the words, the child assigned."""
    r = parse(con, "How many 30+ point games did Jokic have this season?", names=["Jokic"], stat="points")
    assert r.intent == "threshold_count" and r.subject is not None and r.subject.kind == "player" and r.subject.players == ("Nikola Jokic",)
    assert r.scope.get("threshold") == 30 and r.scope.get("stat") == "points"
    r = parse(con, "Lakers vs Celtics record this season", names=["Lakers", "Celtics"], stat="")
    assert r.intent == "head_to_head" and r.subject is not None and r.subject.kind == "teams"
    r = parse(con, "who were the top 10 in defensive netpoints / 100 possessions", names=[], stat="")
    assert r.subject is not None and r.subject.kind == "everyone" and r.scope.get("stat") == "netpoints_defense_per_100" and r.scope.get("limit") == 10
    # A span no player or team has never becomes a subject.
    r = parse(con, "alperen sengun double-doubles vs southeast division career away", names=["alperen sengun", "southeast division"], stat="")
    assert r.subject is not None and r.subject.kind != "pair"
    evidence: tuple[str, ...] = r.evidence
    assert any(line.startswith("parent ") for line in evidence)


def test_parse_with_no_names_and_no_stat_still_reads_the_question(con: duckdb.DuckDBPyConnection) -> None:
    r = parse(con, "what was the sixers record when maxey scored 20+ points?")
    assert r.subject is not None and r.subject.kind == "team" and r.subject.teams == ("Philadelphia 76ers",)
    assert r.intent == "record_when"
    slots: dict[str, Any] = r.scope
    assert slots.get("threshold") == 20


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


def test_read_route_reads_the_window_before_the_stages_and_the_quarter_from_the_words(con: duckdb.DuckDBPyConnection) -> None:
    """A bare "last 10 games" reads both season types, which the stages
    decide only beside the window (``_route_game_log_recent_span``); a
    team's quarter is a slot the router's model used to fill."""
    recent, _, _ = read_route(con, "How did the Celtics do in their last 10 games?", ["Celtics"], "")
    assert (recent.intent, recent.slots["limit"], recent.slots.get("season_type_unstated")) == ("game_log", 10, True)
    quarter, _, _ = read_route(con, "show sixers first quarter scoring for their last 10 games", ["sixers"], "points")
    assert (quarter.intent, quarter.slots.get("period")) == ("team_quarter_points", 1)
