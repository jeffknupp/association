"""Fixture tests for :mod:`association.query.subject` - one reading of who a
question is about. Each test is a rule the measurement over 290 recorded
questions needed (``~/association-research/subject-kinds/RESULT.md``), named
for the question that needed it."""

from __future__ import annotations

from typing import Any

import duckdb
import pytest

from association.query.subject import SUBJECT_KINDS, Subject, question_supports, read_subject


@pytest.fixture
def con() -> duckdb.DuckDBPyConnection:
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    c.executemany(
        "INSERT INTO players VALUES (?, ?)",
        [
            ("1", "Joel Embiid"),
            ("2", "Nikola Jokic"),
            ("3", "Kawhi Leonard"),
            ("4", "LeBron James"),
            ("5", "Stephen Curry"),
            ("6", "Kevin Durant"),
            ("7", "Travis Best"),
            ("8", "Luther Head"),
            ("9", "Brandon Boston Jr."),
            ("10", "Magic Johnson"),
            ("11", "Jayson Tatum"),
            ("12", "Jaylen Brown"),
            ("13", "Tyrese Maxey"),
            ("14", "Victor Wembanyama"),
            ("15", "De'Aaron Fox"),
            ("16", "Shai Gilgeous-Alexander"),
            ("17", "Sir'Dominic Pointer"),
            ("18", "DeMar DeRozan"),
            ("19", "Deron Williams"),
        ],
    )
    c.execute("CREATE TABLE teams (team_id VARCHAR, display_name VARCHAR, abbreviation VARCHAR)")
    c.executemany(
        "INSERT INTO teams VALUES (?, ?, ?)",
        [
            ("1", "Boston Celtics", "BOS"),
            ("2", "Philadelphia 76ers", "PHI"),
            ("3", "Orlando Magic", "ORL"),
            ("4", "Sacramento Kings", "SAC"),
            ("5", "Los Angeles Lakers", "LAL"),
            ("6", "Atlanta Hawks", "ATL"),
            ("7", "Oklahoma City Thunder", "OKC"),
            ("8", "Charlotte Hornets", "CHA"),
        ],
    )
    return c


def _read(con: duckdb.DuckDBPyConnection, question: str, intent: str = "player_stat", **slots: Any) -> Subject:
    return read_subject(con, question, intent, slots)


def test_every_kind_the_reading_returns_is_declared(con: duckdb.DuckDBPyConnection) -> None:
    for q in ("jokic ppg", "lebron vs kawhi head to head", "celtics record", "lakers vs celtics record", "centers vs kings log", "most points this season", "most rebounds by a hawk player"):
        assert _read(con, q).kind in SUBJECT_KINDS


def test_a_name_the_question_gives_is_the_subject_whatever_the_router_filed(con: duckdb.DuckDBPyConnection) -> None:
    """AGENTS.md, "the router invents names": "compare sga and embiid" came
    back as Jusuf Nurkic. A router name the question does not support is
    not the subject; one it does (a completion of the surname given) is."""
    s = _read(con, "compare jokic and embiid", "player_compare", players=["Nikola Jokic", "Jusuf Nurkic"])
    assert s.kind == "pair" and s.players == ("Nikola Jokic", "Joel Embiid")
    s = _read(con, "jokic career averages", player="Nikola Jokic")
    assert s.kind == "player" and s.players == ("Nikola Jokic",)


def test_a_near_spelling_initials_and_a_nickname_support_a_router_name(con: duckdb.DuckDBPyConnection) -> None:
    """ "embid" is Embiid (the router corrects typos), "SGA" his initials,
    "kd" a curated nickname: each is the question holding the name."""
    assert _read(con, "how many games did embid play", player="Joel Embiid").players == ("Joel Embiid",)
    assert _read(con, "how many 20+ point games did SGA have", "threshold_count", player="Shai Gilgeous-Alexander").players == ("Shai Gilgeous-Alexander",)
    assert question_supports("Kevin Durant", "without kd") and not question_supports("Kevin Durant", "without the ball")


def test_an_ordinary_word_that_is_also_a_whole_name_is_not_a_player(con: duckdb.DuckDBPyConnection) -> None:
    """ "best" is Travis Best, "head" is Luther Head and "pointer" is
    Sir'Dominic Pointer to players_named_in; a question is about them only
    when the router read it that way too. "steph curry" keeps Curry - a
    food in the dictionary - because the question uses his nickname."""
    assert _read(con, "Best record from 2010-11 to 2018-19", "team_leaderboard").kind == "everyone"
    assert _read(con, "lebron vs kawhi head to head", "player_matchup").players == ("LeBron James", "Kawhi Leonard")
    assert _read(con, "How far was Curry average three pointer?", "shot_distance", player="Stephen Curry").players == ("Stephen Curry",)
    assert _read(con, "steph curry record vs lebron without kd", "with_without").players == ("Stephen Curry", "LeBron James")
    assert _read(con, "travis best career points", player="Travis Best").players == ("Travis Best",)


def test_a_word_that_names_a_team_here_is_the_team_not_a_player(con: duckdb.DuckDBPyConnection) -> None:
    """ "boston" after "against" is the Celtics, never Brandon Boston Jr.;
    "magic" is Orlando, never Magic Johnson - the trap players_named_in's
    own docstring records."""
    s = _read(con, "show maxey's games against boston in the past two seasons", "game_log", player="Tyrese Maxey")
    assert s.kind == "player" and s.players == ("Tyrese Maxey",) and s.opponent == "Boston Celtics"
    s = _read(con, "de'aaron fox vs magic last five games", "game_log", player="De'Aaron Fox")
    assert s.players == ("De'Aaron Fox",) and s.opponent == "Orlando Magic"
    s = _read(con, "how many 3 pointers have the magic made", "leaderboard")
    assert s.kind == "team" and s.teams == ("Orlando Magic",)


def test_a_companions_role_comes_from_the_question_not_the_routers_slot(con: duckdb.DuckDBPyConnection) -> None:
    """ "without kd" makes Durant a companion; the router filing Embiid under
    `without` on "Embiid's record against Boston" does not make him one.
    A misspelled companion ("wembyanama") takes the router's corrected name
    only because the question's own phrase is a near spelling of it."""
    s = _read(con, "steph curry record vs lebron regular season without kd", "with_without", without=["kd"])
    assert s.kind == "pair" and s.players == ("Stephen Curry", "LeBron James") and s.companions == ("Kevin Durant",)
    s = _read(con, "Embiid's record against Boston this year", "head_to_head", without=["Joel Embiid"])
    assert s.kind == "player" and s.players == ("Joel Embiid",) and s.companions == () and s.opponent == "Boston Celtics"
    s = _read(con, "de'aaron fox vs magic last five games without wembyanama", "player_matchup", player="De'Aaron Fox", without=["Victor Wembanyama"])
    assert s.players == ("De'Aaron Fox",) and s.companions == ("Victor Wembanyama",)


def test_a_team_conditioned_on_a_player_is_the_teams_question(con: duckdb.DuckDBPyConnection) -> None:
    """ "the Sixers' record when Maxey scored 20+" is about the Sixers; Maxey
    is the condition. And "for the Sixers" with no player subject names the
    team, not a player's own team."""
    s = _read(con, "what was the sixers record when maxey scored 20+ points?", "record_when", player="Tyrese Maxey", team="Philadelphia 76ers")
    assert s.kind == "team" and s.teams == ("Philadelphia 76ers",) and s.companions == ("Tyrese Maxey",)
    s = _read(con, "show me splits for the sixers when maxey scores 20+ points", "player_splits", player="Tyrese Maxey")
    assert s.kind == "team" and s.teams == ("Philadelphia 76ers",) and s.own_team is None
    s = _read(con, "lebron stats as a starter for the lakers", player="LeBron James")
    assert s.kind == "player" and s.own_team == "Los Angeles Lakers" and s.teams == ()


def test_a_router_entry_that_is_not_what_its_slot_says_is_dropped(con: duckdb.DuckDBPyConnection) -> None:
    """ "Alamhamed Embiid" was filed under `teams`; "Boston Celtics" under
    `players`. Neither is what the slot claims."""
    s = _read(con, "Embiid's record against Boston this year", "head_to_head", teams=["Alamhamed Embiid", "Boston Celtics"])
    assert s.kind == "player" and s.players == ("Joel Embiid",) and s.teams == ()
    s = _read(con, "show maxey's games against boston", "game_log", players=["Tyrese Maxey", "Boston Celtics"])
    assert s.players == ("Tyrese Maxey",)


def test_two_teams_a_position_group_a_teams_players_and_everyone(con: duckdb.DuckDBPyConnection) -> None:
    assert _read(con, "Lakers vs Celtics record this season", "head_to_head", teams=["Los Angeles Lakers", "Boston Celtics"]).kind == "teams"
    s = _read(con, "Centers stats game log vs kings", "game_log", team="Kings")
    assert s.kind == "position" and s.position == "C" and s.opponent == "Sacramento Kings" and s.teams == ()
    s = _read(con, "Most reb by a hawk player history", "player_history", player="Hawks")
    assert s.kind == "team_players" and s.teams == ("Atlanta Hawks",)
    # yardstick-v2 F049, still wrong live: the chain hands team_quarter_points
    # the Hornets, and the answer is the TEAM's quarter points, not their best
    # first-quarter scorer. "player" beside a team is the team's players.
    s = _read(con, "hornets average 1st quarter points player", "team_quarter_points", team="Charlotte Hornets")
    assert s.kind == "team_players"
    s = _read(con, "oklahoma city thunder all-time triple doubles", player="Oklahoma City Thunder")
    assert s.kind == "team" and s.teams == ("Oklahoma City Thunder",)
    assert _read(con, "PHI record 2026", "team_record", team="Philadelphia 76ers").teams == ("Philadelphia 76ers",)
    assert _read(con, "most points in a single game 1980 nba season", "single_game_high").kind == "everyone"


def test_apply_subject_writes_the_players_the_question_names_in_the_routers_own_shape(con: duckdb.DuckDBPyConnection) -> None:
    """The first field the reading settles in place of the repair chain:
    override_invented_players' job. A router name the question supports is
    kept as the router spelled it; one it never held is replaced by the
    question's spare name, or reported for the refusal - never guessed."""
    from association.query.subject import apply_subject

    slots: dict[str, Any] = {"players": ["Shai Gilgeous-Alexander", "Jusuf Nurkic"]}
    decisions, dropped = apply_subject(read_subject(con, "compare sga and embiid", "player_compare", slots), slots)
    assert slots["players"] == ["Shai Gilgeous-Alexander", "Joel Embiid"] and dropped == []
    assert [(d.field, d.before, d.after) for d in decisions] == [("players", "Jusuf Nurkic", "Joel Embiid")]

    single: dict[str, Any] = {"player": "Ben Simmons"}
    decisions, dropped = apply_subject(read_subject(con, "how many points does embiid average", "player_stat", single), single)
    assert single == {"player": "Joel Embiid"} and [d.field for d in decisions] == ["player"] and dropped == []

    kept: dict[str, Any] = {"player": "Nikola Jokic"}
    assert apply_subject(read_subject(con, "jokic ppg", "player_stat", kept), kept) == ([], []) and kept == {"player": "Nikola Jokic"}

    # A companion the router filed among the players (player_matchup's pair
    # shape) is the question's own name, kept - the golden caught this as a
    # refusal of "fox vs magic ... without wembyanama" (F157, graded correct).
    pair: dict[str, Any] = {"players": ["De'Aaron Fox", "Victor Wembanyama"]}
    assert apply_subject(read_subject(con, "de'aaron fox vs magic last five games without wembyanama", "player_matchup", pair), pair) == ([], [])
    assert pair["players"] == ["De'Aaron Fox", "Victor Wembanyama"]

    # A kept router name the question spells differently takes the question's
    # own resolved name: a bare surname completed, a fabricated given name
    # corrected, a two-edit near miss ("Deron Williams" for "derozan") put
    # right - _question_derived_player's job, now the reading's.
    bare: dict[str, Any] = {"player": "Jokic"}
    decisions, _ = apply_subject(read_subject(con, "How many 30+ point games did Jokic have this season?", "threshold_count", bare), bare)
    assert bare == {"player": "Nikola Jokic"} and [(d.before, d.after) for d in decisions] == [("Jokic", "Nikola Jokic")]
    near: dict[str, Any] = {"player": "Deron Williams"}
    apply_subject(read_subject(con, "derozan career points vs knicks", "player_stat", near), near)
    assert near == {"player": "DeMar DeRozan"}
    right: dict[str, Any] = {"player": "Deron Williams"}
    assert apply_subject(read_subject(con, "deron williams career points", "player_stat", right), right) == ([], []) and right == {"player": "Deron Williams"}

    # A player the router left OUT (the question names two, the slot holds
    # one) is not put back here - restore_dropped_players' job still - and
    # must not trip anything: the golden crashed on this shape twice.
    omitted: dict[str, Any] = {"player": "Nikola Jokic"}
    assert apply_subject(read_subject(con, "compare jokic and embiid", "player_compare", omitted), omitted) == ([], []) and omitted == {"player": "Nikola Jokic"}

    nobody: dict[str, Any] = {"players": ["Ronaldo Lopes", "Nikola Jokic"]}
    decisions, dropped = apply_subject(read_subject(con, "compare jokic's fingerprint to last season", "fingerprint", nobody), nobody)
    assert dropped == ["Ronaldo Lopes"] and decisions == [] and nobody["players"] == ["Ronaldo Lopes", "Nikola Jokic"]  # reported, untouched


def test_the_compare_whose_second_player_the_router_filed_as_the_opponent(con: duckdb.DuckDBPyConnection) -> None:
    """The chain bug the measurement found first: "compare Jaylen Brown and
    Jason Tatum's netpoints" came back with Tatum as `opponent` and fell
    through. The question names two players; the reading says so."""
    s = _read(con, "compare Jaylen Brown and Jason Tatum's netpoints over the past four seasons", "player_stat", player="Jaylen Brown", opponent="Jason Tatum")
    assert s.kind == "pair" and s.players == ("Jaylen Brown", "Jayson Tatum")
