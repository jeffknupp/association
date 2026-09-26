"""Fixture tests for :mod:`association.query.subject` - one reading of who a
question is about. Each test is a rule the measurement over 290 recorded
questions needed (``~/association-research/subject-kinds/RESULT.md``), named
for the question that needed it."""

from __future__ import annotations

import pathlib
from typing import Any

import duckdb
import pytest

from association.nba.season import current_season
from association.query import subject
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
            ("20", "Seth Curry"),
            ("21", "Payton Pritchard"),
            ("22", "Jay Huff"),
            ("23", "Luka Doncic"),
            ("24", "Klay Thompson"),
            ("25", "Allen Iverson"),
            # "kareem stats vs bob lanier": neither legend is in `players`
            # (both retired before the 1993-94 floor), and each surname alone
            # is a whole word of one unrelated real player - ISSUES.md #123.
            ("26", "Kareem Rush"),
            ("27", "Chaz Lanier"),
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


def test_the_ordinary_words_do_not_depend_on_the_machine(monkeypatch: pytest.MonkeyPatch) -> None:
    """The word list ships with the package. It was read from
    /usr/share/dict/words, which GitHub's runner lacks, so "Best record ..."
    above was Travis Best's question there and nobody's here. Hide the system
    file and the list must still be whole."""
    real_read_text = pathlib.Path.read_text

    def no_system_word_list(self: pathlib.Path, *args: Any, **kwargs: Any) -> str:
        if str(self).startswith("/usr/share/dict"):
            raise FileNotFoundError(self)
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "read_text", no_system_word_list)
    subject._dictionary.cache_clear()
    try:
        words = subject._dictionary()
    finally:
        subject._dictionary.cache_clear()
    assert {"best", "head", "pointer", "curry", "sept"} <= words
    assert "jordan" not in words
    assert len(words) > 60_000


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
    the invented-name check's job. A router name the question supports is
    kept as the router spelled it; one it never held is replaced by the
    question's spare name, or reported for the refusal - never guessed."""
    from association.query.subject import apply_subject

    slots: dict[str, Any] = {"players": ["Shai Gilgeous-Alexander", "Jusuf Nurkic"]}
    decisions, dropped, _ = apply_subject(read_subject(con, "compare sga and embiid", "player_compare", slots), slots, con=con, intent="player_compare")
    assert slots["players"] == ["Shai Gilgeous-Alexander", "Joel Embiid"] and dropped == []
    assert [(d.field, d.before, d.after) for d in decisions] == [("players", "Jusuf Nurkic", "Joel Embiid")]

    single: dict[str, Any] = {"player": "Ben Simmons"}
    decisions, dropped, _ = apply_subject(read_subject(con, "how many points does embiid average", "player_stat", single), single, con=con, intent="player_stat")
    assert single == {"player": "Joel Embiid"} and [d.field for d in decisions] == ["player"] and dropped == []

    kept: dict[str, Any] = {"player": "Nikola Jokic"}
    assert apply_subject(read_subject(con, "jokic ppg", "player_stat", kept), kept, con=con, intent="player_stat")[:2] == ([], []) and kept == {"player": "Nikola Jokic"}

    # A companion the router filed among the players (player_matchup's pair
    # shape) is the question's own name, kept - the golden caught this as a
    # refusal of "fox vs magic ... without wembyanama" (F157, graded correct).
    pair: dict[str, Any] = {"players": ["De'Aaron Fox", "Victor Wembanyama"]}
    decisions, dropped, _ = apply_subject(read_subject(con, "de'aaron fox vs magic last five games without wembyanama", "player_matchup", pair), pair, con=con, intent="player_matchup")
    assert pair["players"] == ["De'Aaron Fox", "Victor Wembanyama"] and dropped == []
    assert [(d.field, d.after) for d in decisions] == [("opponent", "Orlando Magic")] and pair["opponent"] == "Orlando Magic"

    # A kept router name the question spells differently takes the question's
    # own resolved name: a bare surname completed, a fabricated given name
    # corrected, a two-edit near miss ("Deron Williams" for "derozan") put
    # right - _question_derived_player's job, now the reading's.
    bare: dict[str, Any] = {"player": "Jokic"}
    decisions, _, _ = apply_subject(read_subject(con, "How many 30+ point games did Jokic have this season?", "threshold_count", bare), bare, con=con, intent="threshold_count")
    assert bare == {"player": "Nikola Jokic"} and [(d.before, d.after) for d in decisions] == [("Jokic", "Nikola Jokic")]
    near: dict[str, Any] = {"player": "Deron Williams"}
    apply_subject(read_subject(con, "derozan career points vs knicks", "player_stat", near), near, con=con, intent="player_stat")
    assert near == {"player": "DeMar DeRozan"}
    right: dict[str, Any] = {"player": "Deron Williams"}
    assert apply_subject(read_subject(con, "deron williams career points", "player_stat", right), right, con=con, intent="player_stat")[:2] == ([], []) and right == {"player": "Deron Williams"}

    # A player the router left OUT (the question names two, the slot holds
    # one) is not put back here - restore_dropped_players' job still - and
    # must not trip anything: the golden crashed on this shape twice.
    omitted: dict[str, Any] = {"player": "Nikola Jokic"}
    assert apply_subject(read_subject(con, "compare jokic and embiid", "player_compare", omitted), omitted, con=con, intent="player_compare")[:2] == ([], []) and omitted == {"player": "Nikola Jokic"}

    nobody: dict[str, Any] = {"players": ["Ronaldo Lopes", "Nikola Jokic"]}
    decisions, dropped, _ = apply_subject(read_subject(con, "compare jokic's fingerprint to last season", "fingerprint", nobody), nobody, con=con, intent="fingerprint")
    assert dropped == ["Ronaldo Lopes"] and decisions == [] and nobody["players"] == ["Ronaldo Lopes", "Nikola Jokic"]  # reported, untouched


def test_a_typo_of_the_questions_own_is_resolved_from_the_span_the_routers_name_anchors(con: duckdb.DuckDBPyConnection) -> None:
    """ "Seph Curry" is the QUESTION's typo: no whole-word match finds Seth
    Curry in it, and the router's "Stephen Curry" is supported by "curry".
    The span around the router's name, resolved with near spelling
    (entities._question_derived_player), is what reaches him - the last
    thing override_invented_players did that the reading did not, per the
    golden (live_day2: "Seph Curry" twice, "Payton Prichard" once)."""
    from association.query.subject import apply_subject

    slots: dict[str, Any] = {"stat": "threePointFieldGoalsMade", "player": "Stephen Curry", "season": 2025}
    s = read_subject(con, "Show a shot chart for 3 point shots by Seph Curry's last season", "shot_chart", slots)
    assert s.players == ("Seth Curry",)
    decisions, dropped, _ = apply_subject(s, slots, con=con, intent="shot_chart")
    assert slots["player"] == "Seth Curry" and dropped == [] and [(d.field, d.before, d.after) for d in decisions] == [("player", "Stephen Curry", "Seth Curry")]

    typo: dict[str, Any] = {"player": "Payton Prichard", "opponent": "Philadelphia 76ers", "venue": "home"}
    apply_subject(read_subject(con, "Payton Prichard stats vs 76ers at home including playoffs game log", "game_log", typo), typo, con=con, intent="game_log")
    assert typo["player"] == "Payton Pritchard" and typo["opponent"] == "Philadelphia 76ers"


def test_the_routers_spelling_stands_where_a_whole_word_names_somebody_else(con: duckdb.DuckDBPyConnection) -> None:
    """The 4.4.0 regression this step fixes: "kareem" is a whole word of
    exactly one player here (Kareem Rush) and "lanier" of one (Chaz
    Lanier), so players_named_in names both - two real players the question
    is not about - and the first `apply_subject` respelled the router's
    correct "Kareem Abdul-Jabbar" to Kareem Rush from that. ISSUES.md #123's
    shape, reintroduced through the reading. The anchored span settles
    neither name (entities._question_derived_player's own guard), so the
    router's spelling stands and the template says nobody matched."""
    from association.query.subject import apply_subject

    slots: dict[str, Any] = {"players": ["Kareem Abdul-Jabbar", "Bob Lanier"]}
    s = read_subject(con, "kareem stats vs bob lanier", "player_matchup", slots)
    assert s.players == ("Kareem Abdul-Jabbar", "Bob Lanier")
    assert apply_subject(s, slots, con=con, intent="player_matchup")[:2] == ([], []) and slots["players"] == ["Kareem Abdul-Jabbar", "Bob Lanier"]


def test_a_player_filed_as_the_opponent_is_checked_like_the_subject(con: duckdb.DuckDBPyConnection) -> None:
    """#206, measured live: "jay huff game log vs Embiid" routed
    ``opponent='Nikola Jokic'`` and the refusal for a player in the opponent
    slot named Jokic. A player there the question never held is replaced by
    the one player the question names that the subject does not claim, or
    dropped - never reported, since the refusal would name him. A team, and
    a player the question does name, are left exactly as they came."""
    from association.query.subject import apply_subject

    slots: dict[str, Any] = {"player": "Jaylen Huff", "opponent": "Nikola Jokic"}
    s = read_subject(con, "jay huff game log vs Embiid", "player_matchup", slots)
    assert s.kind == "pair" and s.players == ("Jay Huff", "Joel Embiid") and s.routed_opponent == "Nikola Jokic"
    decisions, dropped, intent = apply_subject(s, slots, con=con, intent="player_matchup")
    assert slots == {"players": ["Jay Huff", "Joel Embiid"]} and dropped == [] and intent == "player_matchup"
    assert [(d.field, d.before, d.after) for d in decisions] == [("player", "Jaylen Huff", "Jay Huff"), ("opponent", "Nikola Jokic", "Joel Embiid"), ("players", None, ["Jay Huff", "Joel Embiid"])]

    two_spare: dict[str, Any] = {"player": "Luka Doncic", "opponent": "Nikola Jokic"}
    decisions, dropped, _ = apply_subject(read_subject(con, "luka game log vs embiid and klay thompson", "game_log", two_spare), two_spare, con=con, intent="game_log")
    assert "opponent" not in two_spare and dropped == [] and [(d.field, d.before, d.after) for d in decisions] == [("opponent", "Nikola Jokic", None)]

    team: dict[str, Any] = {"player": "Luka Doncic", "opponent": "Los Angeles Lakers"}
    assert read_subject(con, "luka vs the lakers", "game_log", team).routed_opponent is None
    assert apply_subject(read_subject(con, "luka vs the lakers", "game_log", team), team, con=con, intent="game_log")[:2] == ([], []) and team["opponent"] == "Los Angeles Lakers"
    # A player the question does name in `opponent` is the second of a pair:
    # the games the two played against each other (player_matchup), which
    # refusals.pair_from_opponent used to decide.
    named: dict[str, Any] = {"player": "Luka Doncic", "opponent": "Joel Embiid"}
    applied = apply_subject(read_subject(con, "luka game log vs embiid", "game_log", named), named, con=con, intent="game_log")
    assert applied.intent == "player_matchup" and named == {"players": ["Luka Doncic", "Joel Embiid"]} and applied.dropped == []


def test_a_supported_name_stays_as_the_router_spelled_it(con: duckdb.DuckDBPyConnection) -> None:
    """A near spelling ("embid" - the router corrected the surname and
    invented the given name; resolution then suggests rather than this
    replacing a half-supported name), initials ("jb") and a curated nickname
    ("The Answer") each support a router name, and no question slot is
    nothing to check. The cases override_invented_players' tests carried."""
    from association.query.subject import apply_subject

    for question, intent, slots in (
        ("compare sga and embid", "player_compare", {"players": ["Shai Gilgeous-Alexander", "Jemel Embiid"]}),
        ("compare jb and embiid", "player_compare", {"players": ["Jaylen Brown", "Joel Embiid"]}),
        ("Show me The Answer's avg points", "player_stat", {"player": "Allen Iverson"}),
        ("how many points did Luka average?", "player_stat", {"player": "Luka Doncic"}),
        ("who led the league in scoring?", "leaderboard", {"stat": "points"}),
    ):
        before = dict(slots)
        assert apply_subject(read_subject(con, question, intent, slots), slots, con=con, intent=intent)[:2] == ([], []), question
        assert slots == before, question


def test_a_position_phrase_or_a_filler_word_in_the_player_slot_is_not_a_player(con: duckdb.DuckDBPyConnection) -> None:
    """The router files "shooting guard" as the player on "highest 3 point
    percentage ... by a shooting guard" (F056) and "player" on "Most points
    in 15th season played" (F099). Both words are in the question, so the
    support check passes them; neither is a name. The first is the
    position-group subject; the second nobody - and the compiler, which
    used to match both again, reads the kind."""
    from association.query.compose.move import _drop_filler_or_team_player, _drop_position_only_player

    s = _read(con, "highest 3 point percentage in a season by a shooting guard", "leaderboard", player="shooting guard")
    assert s.kind == "position" and s.position == "SG" and s.players == () and s.invented == ()
    assert _drop_position_only_player({"player": "shooting guard"}, s) == {"player": None}
    s = _read(con, "Most points in 15th season played", "leaderboard", player="player")
    assert s.kind == "everyone" and s.players == () and s.invented == ()
    assert _drop_filler_or_team_player({"player": "player", "stat": "points"}, s) == {"player": None, "stat": "points"}
    # A team in the player slot is the team's players' games, not a player.
    s = _read(con, "oklahoma city thunder all-time triple doubles", "threshold_count", player="Thunder")
    assert s.kind == "team" and s.teams == ("Oklahoma City Thunder",) and s.players == ()
    assert _drop_filler_or_team_player({"player": "Thunder"}, s) == {"player": None, "team": "Thunder"}


def test_the_compare_whose_second_player_the_router_filed_as_the_opponent(con: duckdb.DuckDBPyConnection) -> None:
    """The chain bug the measurement found first: "compare Jaylen Brown and
    Jason Tatum's netpoints" came back with Tatum as `opponent` and fell
    through. The question names two players; the reading says so."""
    s = _read(con, "compare Jaylen Brown and Jason Tatum's netpoints over the past four seasons", "player_stat", player="Jaylen Brown", opponent="Jason Tatum")
    assert s.kind == "pair" and s.players == ("Jaylen Brown", "Jayson Tatum")


# ---------------------------------------------------------------------------
# Kind-assigned intents (ROADMAP plan item 2, step 2c-i): the children the
# question's own words name under a parent the router chose, gated on the
# subject's kind. Each case is a recorded question from the routing corpus
# (~/association-research/intent-shrink/, 352 (question, intent) pairs, no
# false positive on any child with the gate), arriving as the PARENT the
# router would emit once the child leaves its prompt, with the slots the
# model could have filled - and the child's own slots read back off the text.


def _assigned(con: duckdb.DuckDBPyConnection, question: str, parent: str, **slots: Any) -> tuple[str, dict[str, Any]]:
    """The intent and slots the agent hands the template: the reading of a
    parent-routed question, applied."""
    from association.query.subject import apply_subject

    given: dict[str, Any] = dict(slots)
    subject = read_subject(con, question, parent, given)
    return apply_subject(subject, given, con=con, intent=parent).intent, given


def test_a_count_of_games_over_a_threshold_is_assigned_under_its_parents(con: duckdb.DuckDBPyConnection) -> None:
    for parent in ("game_log", "player_stat", "leaderboard", "other"):
        intent, slots = _assigned(con, "How many 30+ point games did Jokic have this season?", parent, stat="points", player="Jokic", season=2026, season_type=2)
        assert intent == "threshold_count", parent
        assert slots["threshold"] == 30 and slots["stat"] == "points" and slots["season"] == 2026 and "Jokic" in slots["player"], slots
    # No "+": "30 pt games" is still thirty or more, and "pt" a point.
    intent, slots = _assigned(con, "who had the most 30 pt games in 2024", "leaderboard", stat="points", season=2024, season_type=2)
    assert intent == "threshold_count" and slots["threshold"] == 30 and slots["season"] == 2024 and "player" not in slots
    # "How many times" with no season is a career, as the router reads it.
    intent, slots = _assigned(con, "How many times did wembanyama score 30+ points", "other", stat="points", player="wembanyama")
    assert intent == "threshold_count" and slots["threshold"] == 30 and slots["span"] == "career" and "season" not in slots
    # A ceiling is the count's own line: no threshold at all, and the
    # attempted column the question names - not the model's nearest made-stat.
    intent, slots = _assigned(con, "Sga games with under 14 fta in his whole career", "game_log", stat="freeThrowsMade", player="Shai Gilgeous-Alexander", season_type=2)
    assert intent == "threshold_count" and slots["below"] == ["under 14 fta"] and slots["stat"] == "freeThrowsAttempted" and "threshold" not in slots


def test_a_count_with_no_threshold_in_the_text_stays_the_routers_question(con: duckdb.DuckDBPyConnection) -> None:
    """The router's own stages, run under the child, turn a count with no
    threshold into a ranking - so the words alone do not move it."""
    intent, slots = _assigned(con, "how many times has jokic been named mvp", "other", stat="points", player="Nikola Jokic")
    assert intent == "other" and slots == {"stat": "points", "player": "Nikola Jokic"}


def test_two_teams_meeting_is_not_a_count_of_games(con: duckdb.DuckDBPyConnection) -> None:
    """The kind gate: "how many times" on two TEAMS is head_to_head's
    question, the one false positive the ungated grammar had."""
    intent, slots = _assigned(con, "how many times did the 76ers play boston", "head_to_head", stat="points", teams=["Philadelphia 76ers", "Boston Celtics"])
    assert intent == "head_to_head" and slots["teams"] == ["Philadelphia 76ers", "Boston Celtics"]


def test_a_single_game_high_is_assigned_and_its_subject_restored(con: duckdb.DuckDBPyConnection) -> None:
    # The router dropped Kawhi (F093); under a game log nothing restores him,
    # and the single-game high the words settle is his.
    intent, slots = _assigned(con, "kawhi most threes in a game", "game_log", stat="threePointFieldGoalsMade", season=2026, season_type=2)
    assert intent == "single_game_high" and slots["player"] == "Kawhi Leonard" and slots["stat"] == "threePointFieldGoalsMade"
    intent, slots = _assigned(con, "who had the most assists in a single game this season?", "leaderboard", stat="assists", season=2026, season_type=2)
    assert intent == "single_game_high" and "player" not in slots
    intent, slots = _assigned(con, "Diabate career high assists", "player_stat", stat="assists", player="Diabate", season_type=2)
    assert intent == "single_game_high" and slots["span"] == "career"
    # Precedence: a career's most points IN A GAME is a single-game high
    # before it is a count of games.
    intent, slots = _assigned(con, "PODZIEMSKI career most points in a game", "game_log", stat="points", player="PODZIEMSKI", season_type=2)
    assert intent == "single_game_high" and slots["span"] == "career"


def test_a_history_over_several_seasons_is_assigned_with_the_seasons_count(con: duckdb.DuckDBPyConnection) -> None:
    for parent in ("player_stat", "game_log", "other"):
        intent, slots = _assigned(con, "Klay Thompson's 3pt percentage over the past 4 seasons", parent, stat="threePointFieldGoalPct", player="Klay Thompson", season_type=2)
        assert intent == "player_history", parent
        # `limit` counts seasons for a history; a game log's own `since` for
        # the same words does not survive into a template that refuses it.
        assert slots["limit"] == 4 and "since" not in slots and "season" not in slots, slots
    intent, slots = _assigned(con, "show me lebron's 2pt percentage for the past 10 years", "player_stat", stat="fieldGoalPct", player="LeBron James", season_type=2)
    assert intent == "player_history" and slots["limit"] == 10 and slots["stat"] == "twoPointFieldGoalPct"
    intent, slots = _assigned(con, "Show me luka's avg assists in each year since he joined the league", "player_stat", stat="assists", player="Luka Doncic", season_type=2)
    assert intent == "player_history" and slots["span"] == "career"
    # A count over the past two seasons is the count, not a history.
    intent, slots = _assigned(con, "how many 20+ point games did SGA have in the past two seasons?", "game_log", stat="points", player="Shai Gilgeous-Alexander", season_type=2)
    assert intent == "threshold_count" and slots["threshold"] == 20 and slots["since"] == current_season() - 1 and "limit" not in slots
    # A team ranking over ten years names no player to read a history of.
    intent, _ = _assigned(con, "which team won the championship for the past 10 years", "team_leaderboard", stat="record")
    assert intent == "team_leaderboard"


def test_shot_distance_is_assigned_for_a_player_and_not_for_the_league(con: duckdb.DuckDBPyConnection) -> None:
    intent, slots = _assigned(con, "How far away does Wembanyama shoot from?", "player_stat", stat="fieldGoalsMade", player="Victor Wembanyama", season=2026, season_type=2)
    assert intent == "shot_distance" and slots["player"] == "Victor Wembanyama"
    # A leaderboard's own stage drops the filler player a distance question
    # arrives with; the reading puts the named one back for the template
    # that cannot answer without him.
    intent, slots = _assigned(con, "what was steph curry's avg 3pt shot distance", "leaderboard", stat="shot_distance", season=2026, season_type=2)
    assert intent == "shot_distance" and slots["player"] == "Stephen Curry"
    intent, slots = _assigned(con, "who lead the league in avg 3 point distance", "leaderboard", stat="shot_distance", season=2026, season_type=2)
    assert intent == "leaderboard" and "player" not in slots


def test_a_teams_record_when_a_player_reached_a_threshold_is_assigned(con: duckdb.DuckDBPyConnection) -> None:
    for parent in ("team_record", "other", "game_log"):
        intent, slots = _assigned(con, "what was the sixers record when maxey scored 15+ points?", parent, stat="points", team="Philadelphia 76ers", season=2026, season_type=2)
        assert intent == "record_when", parent
        assert slots["threshold"] == 15 and slots["stat"] == "points" and "maxey" in slots["player"].lower() and slots["team"] == "Philadelphia 76ers", slots
    # No player at all from the model, and no "X scored" grammar for the
    # router's own restore to read: the reading's player, since the
    # template cannot answer without one.
    intent, slots = _assigned(con, "Sga record 36 plus points", "team_record", stat="points", season_type=2)
    assert intent == "record_when" and slots["threshold"] == 36 and slots["player"] == "Shai Gilgeous-Alexander"
    # A player's games won: his team's record in the games he played, with
    # no threshold - and a TEAM's games won stay the team's own record.
    for parent in ("team_record", "game_log", "player_stat"):
        intent, slots = _assigned(con, "how many playoff games has embiid won?", parent, stat="wins", team="Philadelphia 76ers", season_type=3)
        assert intent == "record_when" and slots["player"] == "Joel Embiid" and slots["season_type"] == 3 and "threshold" not in slots, (parent, slots)
    intent, _ = _assigned(con, "how many games have the celtics won this season", "team_record", stat="wins", team="Boston Celtics", season=2026, season_type=2)
    assert intent == "team_record"


def test_a_streak_is_assigned_with_its_kind(con: duckdb.DuckDBPyConnection) -> None:
    intent, slots = _assigned(con, "what was the sixers longest winstreak this year?", "team_record", team="Philadelphia 76ers", season=2026, season_type=2)
    assert intent == "streak" and slots["kind"] == "win" and slots["team"] == "Philadelphia 76ers"
    intent, slots = _assigned(con, "Longest losing streak in the NBA this season", "team_leaderboard", stat="record", season=2026, season_type=2)
    assert intent == "streak" and slots["kind"] == "loss"


def test_a_players_splits_are_assigned_with_the_split(con: duckdb.DuckDBPyConnection) -> None:
    intent, slots = _assigned(con, "Nikola Jokic home and away splits", "player_stat", stat="points", player="Nikola Jokic", season=2026, season_type=2)
    assert intent == "player_splits" and slots["split"] == "home_away" and "venue" not in slots
    intent, slots = _assigned(con, "Giannis Antetokounmpo stats by month", "player_stat", stat="points", player="Giannis Antetokounmpo", season=2026, season_type=2)
    assert intent == "player_splits" and slots["split"] == "month"
    # A team's record by month names no player to split.
    intent, _ = _assigned(con, "knicks record by month", "team_record", team="New York Knicks", season=2026, season_type=2)
    assert intent == "team_record"


def test_an_assigned_child_is_recorded_with_every_slot_it_moved(con: duckdb.DuckDBPyConnection) -> None:
    from association.query.subject import apply_subject

    slots: dict[str, Any] = {"stat": "points", "player": "Nikola Jokic", "season": 2026, "season_type": 2, "order": "recent", "limit": 5}
    subject = read_subject(con, "How many 30+ point games did Jokic have in his last 5 games", "game_log", slots)
    assert subject.intent == "threshold_count" and subject.question.startswith("How many") and any("name threshold_count" in line for line in subject.evidence)
    applied = apply_subject(subject, slots, con=con, intent="game_log")
    moved = {d.field: (d.before, d.after) for d in applied.decisions if d.stage == "subject"}
    assert moved["intent"] == ("game_log", "threshold_count") and moved["threshold"] == (None, 30) and applied.intent == "threshold_count"


def test_a_child_the_router_chose_itself_stands(con: duckdb.DuckDBPyConnection) -> None:
    """With the children still in the router's enum, a child it emitted is
    never re-read: the grammar fires under a parent only, so nothing recorded
    under a child moves (the golden's 308 rows are the proof at scale)."""
    intent, slots = _assigned(con, "PODZIEMSKI career most points in a game", "threshold_count", stat="points", threshold=-1, player="PODZIEMSKI", season_type=2, span="career")
    assert intent == "threshold_count" and slots["threshold"] == -1


def test_a_router_name_that_is_no_name_leaves_the_slots(con: duckdb.DuckDBPyConnection) -> None:
    """Day5 after the 4.5.0 prompt shrink: with no count or ranking example
    in its prompt, the model files the ranking's own words as the player."""
    intent, slots = _assigned(con, "who had the most 30 pt games in 2024", "leaderboard", stat="points", player="most", season=2024, season_type=2)
    assert intent == "threshold_count" and "player" not in slots and slots["threshold"] == 30
    intent, slots = _assigned(con, "Who had the most 30+ point games this season?", "leaderboard", stat="threePointFieldGoalsMade", player="most 30+ point games", limit=1, season=2026, season_type=2)
    assert intent == "threshold_count" and "player" not in slots and slots["stat"] == "points" and slots["threshold"] == 30 and "limit" not in slots
    s = _read(con, "Most points in 15th season played", "player_stat", player="Most Player in 15th Season Played", stat="points")
    assert s.kind == "everyone" and s.filler == ("Most Player in 15th Season Played",)
    # A real name ending in a rank word is still a name.
    assert _read(con, "travis best career stats", "player_stat", player="Travis Best").filler == ()


def test_a_team_word_read_as_a_player_is_the_team(con: duckdb.DuckDBPyConnection) -> None:
    """ "magic vs lakers last 10" arrived as Magic Johnson's games (day5)."""
    intent, slots = _assigned(con, "magic vs lakers last 10", "game_log", stat="points", player="Magic Johnson", opponent="Los Angeles Lakers", order="recent", limit=10, season_type=2, span="career")
    assert intent == "game_log" and "player" not in slots and slots["opponent"] == "Los Angeles Lakers" and slots.get("team") == "Orlando Magic"


def test_an_opponent_the_question_never_names_is_dropped(con: duckdb.DuckDBPyConnection) -> None:
    intent, slots = _assigned(
        con, "PHI record when Embiid and Paul George play", "with_without", team="Philadelphia 76ers", opponent="Atlanta Hawks", season=2026, season_type=2, with_player=["Embiid", "Paul George"]
    )
    assert intent == "with_without" and "opponent" not in slots and slots["team"] == "Philadelphia 76ers"
    # ... and one it does name stays.
    _, slots = _assigned(con, "PHI record vs the hawks when Embiid plays", "with_without", team="Philadelphia 76ers", opponent="Atlanta Hawks", season=2026, season_type=2)
    assert slots["opponent"] == "Atlanta Hawks"


def test_a_position_group_is_handed_to_the_compiler_as_the_player(con: duckdb.DuckDBPyConnection) -> None:
    intent, slots = _assigned(con, "highest 3 point percentage in a season by a shooting guard with at least 400 attempts", "leaderboard", stat="threePointFieldGoalPct", limit=1, season_type=2)
    assert intent == "leaderboard" and slots["player"] == "shooting guard"
    # A team-only intent reads no player, so nothing is written there.
    _, slots = _assigned(con, "which team has the best centers", "team_leaderboard", stat="record", season_type=2)
    assert "player" not in slots


def test_a_non_team_in_the_team_slot_that_is_the_opponents_word_is_dropped(con: duckdb.DuckDBPyConnection) -> None:
    intent, slots = _assigned(con, "Centers stats game log vs kings", "game_log", stat="fieldGoalsMade", team="Los Angeles Kings", order="recent", season_type=2, span="career")
    assert intent == "game_log" and "team" not in slots and slots["opponent"] == "Sacramento Kings" and "player" not in slots


def test_a_completion_that_resolves_to_nobody_is_cut_back(con: duckdb.DuckDBPyConnection) -> None:
    """ "derozan career points vs knicks" arrived as 'Derozan Valenčić' (day5)
    - a surname no player has, on a part the question carries that reaches
    DeMar DeRozan by itself."""
    from association.query.entities import undo_name_completion

    slots: dict[str, Any] = {"player": "Derozan Valenčić"}
    assert undo_name_completion(con, "derozan career points vs knicks", slots) == [("Derozan Valenčić", "Derozan")] and slots["player"] == "Derozan"
    kept: dict[str, Any] = {"player": "DeMar DeRozan"}
    assert undo_name_completion(con, "derozan career points vs knicks", kept) == [] and kept["player"] == "DeMar DeRozan"
