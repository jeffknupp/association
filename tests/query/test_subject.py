"""Fixture tests for :mod:`association.query.subject` - one reading of who a
question is about. Each test is a rule the measurement over 290 recorded
questions needed (``~/association-research/subject-kinds/RESULT.md``), named
for the question that needed it."""

from __future__ import annotations

import pathlib
from typing import Any

import duckdb
import pytest
from routed import slots_route

from association.nba.season import current_season
from association.query import names, subject
from association.query.decisions import Decision
from association.query.entities import find_teams
from association.query.reading import Scope
from association.query.subject import SUBJECT_KINDS, Subject, apply_subject, child_named, question_supports, read_subject, settle_subject


def _applied(con: duckdb.DuckDBPyConnection, question: str, intent: str, slots: dict[str, Any]) -> tuple[dict[str, Any], list[Decision], list[str], str]:
    """``apply_subject`` over ``slots``' typed scope, as the slots the
    reading wrote (``Scope.to_slots``), the decisions, the names dropped
    and the intent - the shape these cases were first written against,
    when the writers mutated a dict (5.0.0: they take and return the Scope)."""
    scope = Scope.from_slots(slots)
    applied = apply_subject(read_subject(con, question, intent, scope), scope, intent=intent)
    return applied.scope.to_slots(), applied.decisions, applied.dropped, applied.intent


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
    return read_subject(con, question, intent, Scope.from_slots(slots))


def _settled(con: duckdb.DuckDBPyConnection, question: str, intent: str = "player_stat", **slots: Any) -> Subject:
    """The subject read, then settled under ``intent`` or the child its
    shape names for it (the grammar's decision alone; the stages run in
    ``parse.read_route``)."""
    read = _read(con, question, intent, **slots)
    named = child_named(read, intent, question)
    chosen, words = named if named is not None else (intent, None)
    return settle_subject(read, chosen, parent=intent, words=words)


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


def test_a_swapped_pair_of_letters_is_one_edit(con: duckdb.DuckDBPyConnection) -> None:
    """ISSUES #239: plain Levenshtein charged a transposition twice, past
    the one-edit budget of a five- or six-letter word, so a question that
    typed "jokci" did not support the router's Nikola Jokic - while the
    entity index, measuring Damerau-Levenshtein in DuckDB, read "jokci" as
    him - and the answer was the misread-name refusal. One metric now."""
    assert question_supports("Nikola Jokic", "jokci stats")
    assert question_supports("Tyrese Maxey", "maxye last 5 games")
    assert question_supports("LeBron James", "leborn stats")
    # Two separate edits in a short word are still a different word.
    assert not question_supports("Nikola Jokic", "jkoci stats")
    assert _read(con, "jokci career averages", player="Nikola Jokic").players == ("Nikola Jokic",)


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


@pytest.mark.parametrize(
    ("question", "named", "stat"),
    [
        ("Most reb by a hawk player history", ["hawk"], "rebounds"),
        ("Most reb by a hawk player history", ["Hawks"], "rebounds"),
        ("celtics record when jayson tatum has 30", ["celtics", "jayson tatum"], "points"),
        ("celtics record when jayson tatum scores 30 points", ["celtics", "jayson tatum"], "points"),
        ("celtics record when jayson tatum has a big game", ["celtics", "jayson tatum"], ""),
    ],
)
def test_no_reading_puts_a_team_where_a_player_belongs(con: duckdb.DuckDBPyConnection, question: str, named: list[str], stat: str) -> None:
    """``refusals._team_where_a_player_belongs`` refused a TEAM in the
    ``player`` slot of a one-player intent ("'Hawks' is a team, and this
    was read as a question about one player's rebounds") and a team's
    record split by a player beside it with no line to split by ("needs a
    line"). It was deleted in Phase 3's first step, because no reading
    reaches either shape: asked of every one of the 2,710 recorded and
    feed questions it says nothing (``~/association-research/stages/refusal_sites.py``,
    on 87cc782), and the parser reads the wordings its two tests were
    written for with the team as the subject - never a team where a
    player belongs, never a companion's record with no line. Held here,
    on the reader, by the deleted check's own predicate."""
    from association.query.parse import read_route, reading_from_route
    from association.query.reading import PLAYER_INTENTS

    route, _, _ = read_route(con, question, named, stat)
    reading = reading_from_route(con, question, route)
    assert reading.subject is not None
    player = reading.scope.player
    team_subject = reading.subject.kind in ("team", "team_players") and not reading.subject.players
    if reading.intent in PLAYER_INTENTS and team_subject and player:
        no_line = reading.intent == "record_when" and not (reading.scope.threshold and reading.scope.stat)
        assert not find_teams(con, player) and not no_line, (question, reading.intent, player)


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

    slots: dict[str, Any] = {"players": ["Shai Gilgeous-Alexander", "Jusuf Nurkic"]}
    after, decisions, dropped, _ = _applied(con, "compare sga and embiid", "player_compare", slots)
    assert after["players"] == ["Shai Gilgeous-Alexander", "Joel Embiid"] and dropped == []
    assert [(d.field, d.before, d.after) for d in decisions] == [("players", "Jusuf Nurkic", "Joel Embiid")]

    single: dict[str, Any] = {"player": "Ben Simmons"}
    after, decisions, dropped, _ = _applied(con, "how many points does embiid average", "player_stat", single)
    assert after == {"player": "Joel Embiid"} and [d.field for d in decisions] == ["player"] and dropped == []

    kept: dict[str, Any] = {"player": "Nikola Jokic"}
    assert _applied(con, "jokic ppg", "player_stat", kept)[:3] == ({"player": "Nikola Jokic"}, [], [])

    # A companion the router filed among the players (player_matchup's pair
    # shape) is the question's own name, kept - the golden caught this as a
    # refusal of "fox vs magic ... without wembyanama" (F157, graded correct).
    pair: dict[str, Any] = {"players": ["De'Aaron Fox", "Victor Wembanyama"]}
    after, decisions, dropped, _ = _applied(con, "de'aaron fox vs magic last five games without wembyanama", "player_matchup", pair)
    assert after["players"] == ["De'Aaron Fox", "Victor Wembanyama"] and dropped == []

    # A kept router name the question spells differently takes the question's
    # own resolved name: a bare surname completed, a fabricated given name
    # corrected, a two-edit near miss ("Deron Williams" for "derozan") put
    # right - _question_derived_player's job, now the reading's.
    bare: dict[str, Any] = {"player": "Jokic"}
    after, decisions, _, _ = _applied(con, "How many 30+ point games did Jokic have this season?", "threshold_count", bare)
    assert after == {"player": "Nikola Jokic"} and [(d.before, d.after) for d in decisions] == [("Jokic", "Nikola Jokic")]
    near: dict[str, Any] = {"player": "Deron Williams"}
    assert _applied(con, "derozan career points vs knicks", "player_stat", near)[0] == {"player": "DeMar DeRozan"}
    right: dict[str, Any] = {"player": "Deron Williams"}
    assert _applied(con, "deron williams career points", "player_stat", right)[:3] == ({"player": "Deron Williams"}, [], [])

    # A player the router left OUT (the question names two, the slot holds
    # one) is not put back here - the parser reads every name the question
    # holds - and must not trip anything: the golden crashed on this shape
    # twice.
    omitted: dict[str, Any] = {"player": "Nikola Jokic"}
    assert _applied(con, "compare jokic and embiid", "player_compare", omitted)[:3] == ({"player": "Nikola Jokic"}, [], [])

    nobody: dict[str, Any] = {"players": ["Ronaldo Lopes", "Nikola Jokic"]}
    after, decisions, dropped, _ = _applied(con, "compare jokic's fingerprint to last season", "fingerprint", nobody)
    assert dropped == ["Ronaldo Lopes"] and decisions == [] and after["players"] == ["Ronaldo Lopes", "Nikola Jokic"]  # reported, untouched


def test_a_typo_of_the_questions_own_is_resolved_from_the_span_the_routers_name_anchors(con: duckdb.DuckDBPyConnection) -> None:
    """ "Seph Curry" is the QUESTION's typo: no whole-word match finds Seth
    Curry in it, and the router's "Stephen Curry" is supported by "curry".
    The span around the router's name, resolved with near spelling
    (subject.question_derived_player), is what reaches him - the last
    thing override_invented_players did that the reading did not, per the
    golden (live_day2: "Seph Curry" twice, "Payton Prichard" once)."""
    from association.query.subject import apply_subject

    slots: dict[str, Any] = {"stat": "threePointFieldGoalsMade", "player": "Stephen Curry", "season": 2025}
    s = read_subject(con, "Show a shot chart for 3 point shots by Seph Curry's last season", "shot_chart", Scope.from_slots(slots))
    assert s.players == ("Seth Curry",)
    applied = apply_subject(s, Scope.from_slots(slots), intent="shot_chart")
    assert applied.scope.player == "Seth Curry" and applied.dropped == [] and [(d.field, d.before, d.after) for d in applied.decisions] == [("player", "Stephen Curry", "Seth Curry")]

    typo: dict[str, Any] = {"player": "Payton Prichard", "opponent": "Philadelphia 76ers", "venue": "home"}
    after = _applied(con, "Payton Prichard stats vs 76ers at home including playoffs game log", "game_log", typo)[0]
    assert after["player"] == "Payton Pritchard" and after["opponent"] == "Philadelphia 76ers"


def test_the_routers_spelling_stands_where_a_whole_word_names_somebody_else(con: duckdb.DuckDBPyConnection) -> None:
    """The 4.4.0 regression this step fixes: "kareem" is a whole word of
    exactly one player here (Kareem Rush) and "lanier" of one (Chaz
    Lanier), so players_named_in names both - two real players the question
    is not about - and the first `apply_subject` respelled the router's
    correct "Kareem Abdul-Jabbar" to Kareem Rush from that. ISSUES.md #123's
    shape, reintroduced through the reading. The anchored span settles
    neither name (subject.question_derived_player's own guard), so the
    router's spelling stands and the template says nobody matched."""
    from association.query.subject import apply_subject

    slots: dict[str, Any] = {"players": ["Kareem Abdul-Jabbar", "Bob Lanier"]}
    s = read_subject(con, "kareem stats vs bob lanier", "player_matchup", Scope.from_slots(slots))
    assert s.players == ("Kareem Abdul-Jabbar", "Bob Lanier")
    applied = apply_subject(s, Scope.from_slots(slots), intent="player_matchup")
    assert (applied.decisions, applied.dropped, applied.scope.players) == ([], [], ("Kareem Abdul-Jabbar", "Bob Lanier"))


def test_a_player_filed_as_the_opponent_is_checked_like_the_subject(con: duckdb.DuckDBPyConnection) -> None:
    """#206, measured live: "jay huff game log vs Embiid" routed
    ``opponent='Nikola Jokic'``. The reading holds a model's opponent apart
    from the question's own pair. On the parser's path the pair relation is
    named from the subject's kind - the question's own two players - and a
    name the model supplied that the question never held, which nothing in
    it can replace, is carried as misread for the refusal rather than
    answered about. A team opponent narrows the one player's games. The
    router-era reroute that moved a player filed as the opponent into
    ``players`` never fired on the parser's output and is gone (ROADMAP plan
    item 6, step (d), part 3)."""
    s = read_subject(con, "jay huff game log vs Embiid", "player_matchup", Scope.from_slots({"player": "Jaylen Huff", "opponent": "Nikola Jokic"}))
    # A log against a player is his own games with the other on the far side (ROADMAP step 3), the model's Jokic still carried as invented.
    assert s.kind == "player" and s.players == ("Jay Huff",) and [(c.name, c.side) for c in s.conditions] == [("Joel Embiid", "opponent")] and s.invented == ("Nikola Jokic",)
    invented = _parsed(con, "jay huff game log vs Embiid", ["Jaylen Huff", "Nikola Jokic"])
    assert invented.intent == "game_log" and invented.misread == ("Nikola Jokic",)
    assert read_subject(con, "luka vs the lakers", "game_log", Scope.from_slots({"player": "Luka Doncic", "opponent": "Los Angeles Lakers"})).opponent == "Los Angeles Lakers"
    team = _parsed(con, "luka vs the lakers", ["luka", "lakers"])
    assert team.scope.player == "Luka Doncic" and team.scope.opponent == "Los Angeles Lakers" and team.misread == ()
    # A log against a player: his own games, the other on the far side (ROADMAP step 3); the bare pair is still the matchup.
    log = _parsed(con, "luka game log vs embiid", ["luka", "embiid"])
    assert log.intent == "game_log" and log.scope.player == "Luka Doncic" and [(c.player, c.side) for c in log.scope.conditions] == [("Joel Embiid", "opponent")] and log.misread == ()
    pair = _parsed(con, "luka vs embiid", ["luka", "embiid"])
    assert pair.intent == "player_matchup" and pair.scope.players == ("Luka Doncic", "Joel Embiid") and not pair.scope.opponent and pair.misread == ()


def test_a_supported_name_stays_as_the_router_spelled_it(con: duckdb.DuckDBPyConnection) -> None:
    """A near spelling ("embid" - the router corrected the surname and
    invented the given name; resolution then suggests rather than this
    replacing a half-supported name), initials ("jb") and a curated nickname
    ("The Answer") each support a router name, and no question slot is
    nothing to check. The cases override_invented_players' tests carried."""

    for question, intent, slots in (
        ("compare sga and embid", "player_compare", {"players": ["Shai Gilgeous-Alexander", "Jemel Embiid"]}),
        ("compare jb and embiid", "player_compare", {"players": ["Jaylen Brown", "Joel Embiid"]}),
        ("Show me The Answer's avg points", "player_stat", {"player": "Allen Iverson"}),
        ("how many points did Luka average?", "player_stat", {"player": "Luka Doncic"}),
        ("who led the league in scoring?", "leaderboard", {"stat": "points"}),
    ):
        after, decisions, dropped, _ = _applied(con, question, intent, slots)
        assert (decisions, dropped) == ([], []), question
        assert after == Scope.from_slots(slots).to_slots(), question


def test_a_position_phrase_or_a_filler_word_in_the_player_slot_is_not_a_player(con: duckdb.DuckDBPyConnection) -> None:
    """The router files "shooting guard" as the player on "highest 3 point
    percentage ... by a shooting guard" (F056) and "player" on "Most points
    in 15th season played" (F099). Both words are in the question, so the
    support check passes them; neither is a name. The first is the
    position-group subject; the second nobody - and the compiler reads the
    kind, where it used to take both back out of the slot."""
    s = _read(con, "highest 3 point percentage in a season by a shooting guard", "leaderboard", player="shooting guard")
    assert s.kind == "position" and s.position == "SG" and s.players == () and s.invented == ()
    s = _read(con, "Most points in 15th season played", "leaderboard", player="player")
    assert s.kind == "everyone" and s.players == () and s.invented == ()
    # A team in the player slot is the team's players' games, not a player.
    s = _read(con, "oklahoma city thunder all-time triple doubles", "threshold_count", player="Thunder")
    assert s.kind == "team" and s.teams == ("Oklahoma City Thunder",) and s.players == ()


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


def _parsed(con: duckdb.DuckDBPyConnection, question: str, names: list[str], stat: str = "") -> Any:
    """The Reading the parser settles for ``question``, the normalizer's
    reply stubbed as ``names`` and ``stat``: its route
    (``parse.read_route``), then its last step (``parse.reading_from_route``)."""
    from association.query.parse import read_route, reading_from_route

    route, _, _ = read_route(con, question, names, stat)
    return reading_from_route(con, question, route)


@pytest.fixture
def franchises() -> duckdb.DuckDBPyConnection:
    """Three franchises under ESPN's own ids, each with a name it no longer
    has: id 3 was the New Orleans Hornets, id 30 the Charlotte Bobcats, id
    25 the Seattle SuperSonics (``nba.franchises.FRANCHISE_ERAS``)."""
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    c.executemany("INSERT INTO players VALUES (?, ?)", [("1", "Chris Paul"), ("2", "Kevin Durant"), ("3", "Kobe Bryant")])
    c.execute("CREATE TABLE teams (team_id VARCHAR, display_name VARCHAR, abbreviation VARCHAR)")
    c.executemany(
        "INSERT INTO teams VALUES (?, ?, ?)",
        [("3", "New Orleans Pelicans", "NO"), ("30", "Charlotte Hornets", "CHA"), ("25", "Oklahoma City Thunder", "OKC"), ("13", "Los Angeles Lakers", "LAL")],
    )
    return c


def test_a_team_is_read_in_the_season_the_question_names(franchises: duckdb.DuckDBPyConnection) -> None:
    # The parser reads the subject before the stages settle the season, so
    # the reading takes the question's own: "the hornets in 2008" is Chris
    # Paul's New Orleans team, not today's Charlotte Hornets, which the
    # answer then asked about as a name nobody typed (ISSUES.md #316).
    own = _parsed(franchises, "chris paul assists for the hornets in 2008", ["chris paul", "hornets"], "assists")
    assert own.scope.own_team == "New Orleans Hornets" and own.scope.season == 2008
    assert _parsed(franchises, "kevin durant points per game for the sonics in 2008", ["kevin durant", "sonics"], "points").scope.own_team == "Seattle SuperSonics"
    # An opponent the same way.
    against = _parsed(franchises, "kobe points against the hornets in 2008", ["kobe", "hornets"], "points")
    assert against.scope.opponent == "New Orleans Hornets"
    # With no season named the name is today's, and the tenure rule reads a career.
    today = _parsed(franchises, "chris paul assists for the hornets", ["chris paul", "hornets"], "assists")
    assert today.scope.own_team == "Charlotte Hornets" and today.scope.span == "career"


def _assigned(con: duckdb.DuckDBPyConnection, question: str, parent: str, **slots: Any) -> tuple[str, dict[str, Any]]:
    """The intent and slots the agent hands the template for a question
    whose route arrives under ``parent``: the one subject reading, the
    parser's child step (``parse._read_route_child``), then its last
    (``parse.reading_from_route``) - the Reading, as slots."""
    from association.query.parse import _read_route_staged, reading_from_route
    from association.query.router import Route

    # The subject is read once; the stages run once, under the child the
    # words name for it or under the parent; the last step settles nothing.
    who = read_subject(con, question, parent, Scope.from_slots(slots))
    staged, decisions, words = _read_route_staged(question, dict(slots), parent, who)
    reading = reading_from_route(con, question, Route(staged.intent, staged.scope, decisions, settle_subject(who, staged.intent, parent=parent, words=words)))
    return reading.intent, reading.scope.to_slots()


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
    # The stages, run under the parent once the child declined, add their own season type.
    assert intent == "other" and slots == {"stat": "points", "player": "Nikola Jokic", "season_type": 2}


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


def test_a_single_game_named_without_an_article_is_one_game(con: duckdb.DuckDBPyConnection) -> None:
    """ "single game" is one game with an article or without (ISSUES.md
    #260): "this season's single game with the most assists" answered the
    assists-per-game leaders, and "most 3 pointers made in single game
    24-25" the season's total. Neither names anybody - "single game with"
    is no player called Single - and the kind still gates it: a team's
    single game is not a player's high."""
    intent, slots = _assigned(con, "this season's single game with the most assists", "leaderboard", stat="assists", season=2026, season_type=2)
    assert intent == "single_game_high" and slots["stat"] == "assists" and slots["season"] == 2026 and "player" not in slots
    intent, slots = _assigned(con, "most 3 pointers made in single game 24-25", "leaderboard", stat="threePointFieldGoalsMade", season=2025, season_type=2)
    assert intent == "single_game_high" and slots["season"] == 2025 and "player" not in slots
    intent, slots = _assigned(con, "single game with the most points this season", "leaderboard", stat="points", season=2026, season_type=2)
    assert intent == "single_game_high" and "player" not in slots
    intent, slots = _assigned(con, "jokic single-game high rebounds", "game_log", stat="rebounds", player="Nikola Jokic", season_type=2)
    assert intent == "single_game_high" and slots["player"] == "Nikola Jokic" and slots["stat"] == "rebounds"
    intent, _ = _assigned(con, "celtics most points single game", "other", stat="points", team="Boston Celtics", season_type=2)
    assert intent == "other"
    # A plural is games, not one game.
    intent, _ = _assigned(con, "jokic single games stats", "game_log", player="Nikola Jokic", season_type=2)
    assert intent == "game_log"


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
    # ... and read by the parser itself, with no team slot: the router once
    # filed the subject as an outlook's "team" named Joel Embiid (day5), a
    # shape the parser never writes.
    reading = _parsed(con, "how many playoff games has embiid won?", ["embiid"])
    assert reading.intent == "record_when" and reading.scope.player == "Joel Embiid" and not reading.scope.team and reading.scope.stat == "wins"
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


def test_a_child_the_router_chose_itself_stands(con: duckdb.DuckDBPyConnection) -> None:
    """With the children still in the router's enum, a child it emitted is
    never re-read: the grammar fires under a parent only, so nothing recorded
    under a child moves (the golden's 308 rows are the proof at scale)."""
    intent, slots = _assigned(con, "PODZIEMSKI career most points in a game", "threshold_count", stat="points", threshold=-1, player="PODZIEMSKI", season_type=2, span="career")
    assert intent == "threshold_count" and slots["threshold"] == -1


def test_a_router_name_that_is_no_name_leaves_the_slots(con: duckdb.DuckDBPyConnection) -> None:
    """Day5 after the 5.0.0 prompt shrink: with no count or ranking example
    in its prompt, the model files the ranking's own words as the player."""
    intent, slots = _assigned(con, "who had the most 30 pt games in 2024", "leaderboard", stat="points", player="most", season=2024, season_type=2)
    assert intent == "threshold_count" and "player" not in slots and slots["threshold"] == 30
    intent, slots = _assigned(con, "Who had the most 30+ point games this season?", "leaderboard", stat="threePointFieldGoalsMade", player="most 30+ point games", limit=1, season=2026, season_type=2)
    assert intent == "threshold_count" and "player" not in slots and slots["stat"] == "points" and slots["threshold"] == 30 and "limit" not in slots
    s = _read(con, "Most points in 15th season played", "player_stat", player="Most Player in 15th Season Played", stat="points")
    assert s.kind == "everyone" and s.filler == ("Most Player in 15th Season Played",)
    # A real name ending in a rank word is still a name.
    assert _read(con, "travis best career stats", "player_stat", player="Travis Best").filler == ()


def test_a_position_group_is_the_subject_and_never_a_player_slot(con: duckdb.DuckDBPyConnection) -> None:
    """F056 ("highest 3 point percentage in a season. by a shooting guard
    with at least 400 attempts"): the position group is the subject's own
    field, and nothing writes its phrase into ``player`` - the leaderboard
    refuses a position subject itself, and the compiler reads the group off
    the subject."""
    question = "highest 3 point percentage in a season by a shooting guard with at least 400 attempts"
    intent, slots = _assigned(con, question, "leaderboard", stat="threePointFieldGoalPct", limit=1, season_type=2)
    assert intent == "leaderboard" and "player" not in slots
    s = read_subject(con, question, "leaderboard", Scope.from_slots({"stat": "threePointFieldGoalPct"}))
    assert s.kind == "position" and s.position == "SG"
    # A team-only intent reads no player, so nothing is written there.
    _, slots = _assigned(con, "which team has the best centers", "team_leaderboard", stat="record", season_type=2)
    assert "player" not in slots


# ---------------------------------------------------------------------------
# ROADMAP plan item 3, step B: a companion carries the role the question
# gives him (Companion), and the roles the router's own slots cannot carry
# are written as the `conditions` slot where the template honors it.


def test_a_companions_role_is_read_off_its_phrase(con: duckdb.DuckDBPyConnection) -> None:
    from association.query.subject import Companion

    s = _read(con, "76ers record when maxey scores 20+ points", "record_when", team="Philadelphia 76ers", stat="points", threshold=20)
    assert s.kind == "team" and s.conditions == (Companion("Tyrese Maxey", "reached", "points", 20),) and s.companions == ("Tyrese Maxey",)
    s = _read(con, "PHI record when Embiid and Paul George start", "with_without", team="Philadelphia 76ers", with_player=["Embiid", "Paul George"])
    assert [(c.name, c.predicate) for c in s.conditions] == [("Joel Embiid", "started"), ("Paul George", "started")]
    s = _read(con, "de'aaron fox game log without wembyanama", "game_log", players=["De'Aaron Fox", "Victor Wembanyama"], without=["wembyanama"])
    assert [(c.name, c.predicate) for c in s.conditions] == [("Victor Wembanyama", "absent")] and s.players == ("De'Aaron Fox",)
    s = _read(con, "celtics record with tatum out", "with_without", team="Boston Celtics", with_player=["tatum"])
    assert [(c.name, c.predicate) for c in s.conditions] == [("Jayson Tatum", "absent")]
    s = _read(con, "jaylen brown stats with tatum off the bench", "player_stat", player="Jaylen Brown", with_player=["tatum"])
    assert [(c.name, c.predicate) for c in s.conditions] == [("Jayson Tatum", "bench")]
    s = _read(con, "hornets record when lebron and kawhi play this year", "with_without", team="Charlotte Hornets", with_player=["lebron", "kawhi"])
    assert [(c.name, c.predicate) for c in s.conditions] == [("LeBron James", "played"), ("Kawhi Leonard", "played")]


def test_a_start_or_a_line_is_written_as_a_condition_where_the_template_honors_it(con: duckdb.DuckDBPyConnection) -> None:
    intent, slots = _assigned(con, "jaylen brown game log with tatum starting", "game_log", player="Jaylen Brown", with_player=["tatum"])
    assert intent == "game_log" and slots["conditions"] == [{"player": "Jayson Tatum", "side": "own", "predicate": "started"}]
    # "in games X did something" is a companion phrase too, the role after the name.
    intent, slots = _assigned(con, "jaylen brown stats in games tatum scored 30+ points", "player_stat", player="Jaylen Brown", stat="points")
    assert slots["conditions"] == [{"player": "Jayson Tatum", "side": "own", "predicate": "reached", "stat": "points", "threshold": 30}]
    intent, slots = _assigned(con, "jaylen brown ppg when tatum scores 30+ points", "player_stat", player="Jaylen Brown", stat="points")
    assert slots["conditions"] == [{"player": "Jayson Tatum", "side": "own", "predicate": "reached", "stat": "points", "threshold": 30}]
    # An absence stays the router's `without`; the comparison templates read it as the split's two sides.
    intent, slots = _assigned(con, "celtics record without tatum", "with_without", team="Boston Celtics", without=["tatum"])
    assert "conditions" not in slots and slots["without"] == ["Jayson Tatum"]  # the stages write the one reading's companion, resolved
    # with_without reads a start as the split's own side (step C).
    intent, slots = _assigned(con, "celtics record when tatum starts", "with_without", team="Boston Celtics", with_player=["tatum"])
    assert slots["conditions"] == [{"player": "Jayson Tatum", "side": "own", "predicate": "started"}] and "with_player" not in slots  # a start is the condition, not a presence


def test_a_team_with_a_companions_line_is_record_when_with_him_as_the_player(con: duckdb.DuckDBPyConnection) -> None:
    """yardstick-v2 F087, "show me splits for the sixers when maxey scores 20+
    points": the 76ers' record in the games Maxey reached 20 - record_when
    with Maxey as its player, whatever the router filed (the player's own
    splits; a line for an invented Joel Embiid)."""
    intent, slots = _assigned(con, "show me splits for the sixers when maxey scores 20+ points", "player_splits", stat="points", player="Maxey", season=2026, season_type=2)
    assert (
        intent == "record_when" and "maxey" in slots["player"].lower() and slots["team"] == "Philadelphia 76ers" and (slots["stat"], slots["threshold"]) == ("points", 20) and slots["season"] == 2026
    )
    intent, slots = _assigned(
        con, "show me splits for the sixers when maxey scores 20+ points", "record_when", stat="points", player="Joel Embiid", opponent="Philadelphia 76ers", season=2026, season_type=2, threshold=20
    )
    assert intent == "record_when" and "maxey" in slots["player"].lower() and slots["team"] == "Philadelphia 76ers" and "opponent" not in slots
    # A player subject keeps his own question: his splits, the condition beside him.
    intent, slots = _assigned(con, "jaylen brown splits when tatum scores 30+ points", "player_splits", stat="points", player="Jaylen Brown", season=2026, season_type=2)
    assert intent == "player_splits" and slots["player"] == "Jaylen Brown"


def test_a_companion_the_router_named_nobody_for_is_read_from_the_question(con: duckdb.DuckDBPyConnection) -> None:
    """F087 arrived with an invented Joel Embiid and no Maxey in any slot; the
    companion phrase's own word names him, in the question's spelling, and
    the settled record_when takes him as the spare name that replaces the
    invention. An ordinary word ("brown"), a stat's ("fga") and a number
    name nobody; a two-word name is tried as the pair first."""
    from association.query.subject import Companion, apply_subject

    # A namesake each: "maxey" and "jalen williams" are whole words of two
    # players' names, so players_named_in names nobody and only the
    # companion phrase's own word reaches them.
    con.executemany("INSERT INTO players VALUES (?, ?)", [("28", "Marlon Maxey"), ("29", "Jalen Williams"), ("30", "Jalen Williams")])
    slots: dict[str, Any] = {"stat": "points", "player": "Joel Embiid", "opponent": "Philadelphia 76ers", "season": 2026, "season_type": 2}
    s = _settled(con, "show me splits for the sixers when maxey scores 20+ points", "player_stat", **slots)
    assert s.kind == "team" and s.teams == ("Philadelphia 76ers",) and s.conditions == (Companion("maxey", "reached", "points", 20),) and s.intent == "record_when"
    assert "companions the router named nobody for ['maxey']" in s.evidence
    applied = apply_subject(s, Scope.from_slots(slots), intent="player_stat")
    assert applied.intent == "record_when" and applied.dropped == [] and (applied.scope.player, applied.scope.team, applied.scope.threshold) == ("maxey", "Philadelphia 76ers", 20)
    s = _read(con, "thunder record with jalen williams out", "team_record", team="Oklahoma City Thunder")
    assert s.conditions == (Companion("jalen williams", "absent", None, None),)
    for question in (
        "mikal bridges game log with less than 15 fga",
        "celtics record with 3 starters out",
        "celtics record with best shooting",
    ):  # Travis Best is in the fixture: the leading word of something else is no name
        s = _read(con, question, "team_record", team="Boston Celtics")
        assert s.conditions == (), (question, s.conditions)
    # An ordinary word in a name's place - before an absence word - IS the
    # name, as typed: ten players are Brown, and the template asks which.
    s = _read(con, "celtics record with brown out", "team_record", team="Boston Celtics")
    assert s.conditions == (Companion("brown", "absent", None, None),)


def test_a_possessive_team_word_names_the_team(con: duckdb.DuckDBPyConnection) -> None:
    """ISSUES.md #232: "the Sixers' record" and "the Knicks' previous 5 games"
    read the team the way the bare word does (11 of 277 paraphrases read no
    team at all)."""
    s = _read(con, "the Sixers' record this season", "team_record")
    assert s.kind == "team" and s.teams == ("Philadelphia 76ers",)
    s = _read(con, "the Hawks' previous 5 games", "game_log")
    assert s.kind == "team" and s.teams == ("Atlanta Hawks",)
    s = _read(con, "the Celtics's record", "team_record")
    assert s.kind == "team" and s.teams == ("Boston Celtics",)


def test_a_player_after_a_versus_word_is_an_opponent_side_condition_where_the_words_ask_for_games(con: duckdb.DuckDBPyConnection) -> None:
    """ROADMAP step 3 (Jeff's call, 2026-09-30): a player after "vs" is on
    the other side of the subject's games - a condition, read wherever the
    words ask for a high, a count, a record, a log or a split - and never a
    second subject there. A bare "curry vs lebron" or "curry stats vs
    lebron" stays the pair, whose matchup summary reads both lines. Three
    names are a subject and conditions whatever the words; a team after
    "vs" names nobody here."""
    from association.query.subject import Companion

    s = _read(con, "most points by stephen curry vs lebron", "player_stat", players=["Stephen Curry", "LeBron James"], stat="points")
    assert s.kind == "player" and s.players == ("Stephen Curry",) and s.conditions == (Companion("LeBron James", "played", side="opponent"),)
    s = _read(con, "how many times did lebron score 30 vs kawhi", "player_stat", players=["LeBron James", "Kawhi Leonard"], stat="points")
    assert s.kind == "player" and s.players == ("LeBron James",) and s.conditions == (Companion("Kawhi Leonard", "played", side="opponent"),)
    for question in ("stephen curry vs lebron", "stephen curry stats vs lebron", "stephen curry ppg against lebron this season"):
        s = _read(con, question, "player_stat", players=["Stephen Curry", "LeBron James"])
        assert s.kind == "pair" and s.conditions == (), question
    s = _read(con, "kawhi points per game vs lebron and curry", "player_stat", players=["Kawhi Leonard", "LeBron James", "Stephen Curry"], stat="points")
    assert s.kind == "player" and [(c.name, c.side) for c in s.conditions] == [("LeBron James", "opponent"), ("Stephen Curry", "opponent")]
    # Beside a teammate's absence: the two conditions, on their two sides.
    s = _read(con, "stephen curry game log vs lebron without durant", "game_log", players=["Stephen Curry", "LeBron James"], without=["durant"])
    assert s.kind == "player" and [(c.name, c.predicate, c.side) for c in s.conditions] == [("Kevin Durant", "absent", "own"), ("LeBron James", "played", "opponent")]
    # A team after "vs" is the opponent slot's; a player named only after it has no subject before him.
    s = _read(con, "stephen curry game log vs the lakers", "game_log", player="Stephen Curry")
    assert s.kind == "player" and s.conditions == ()


def test_compare_x_with_y_reads_a_pair_not_a_companion(con: duckdb.DuckDBPyConnection) -> None:
    """ISSUES.md #233: the "with" a compare verb owns joins the two subjects."""
    s = _read(con, "compare luka with sga", "player_compare", players=["Luka Doncic", "Shai Gilgeous-Alexander"])
    assert s.kind == "pair" and s.players == ("Luka Doncic", "Shai Gilgeous-Alexander") and s.companions == ()
    s = _read(con, "contrast tatum with jaylen brown this season", "player_compare", players=["Jayson Tatum", "Jaylen Brown"])
    assert s.kind == "pair" and s.companions == ()
    # A real companion after a compare verb, further along, is still one.
    s = _read(con, "compare luka and sga without kyrie", "player_compare", players=["Luka Doncic", "Shai Gilgeous-Alexander"], without=["Kyrie Irving"])
    assert s.kind == "pair" and s.players == ("Luka Doncic", "Shai Gilgeous-Alexander")


def test_the_paraphrases_wordings_of_the_children_are_assigned(con: duckdb.DuckDBPyConnection) -> None:
    """Held-out paraphrases (parser-greenfield, step b, v3) the children's
    grammars missed: "in one match", a hyphen in "30-point" and "36-plus",
    "at least 2", "from year to year" - each measured at 0 false positives
    over the recorded corpus (``intent-shrink/port_check.py``)."""
    intent, _ = _assigned(con, "in this season's games, who recorded the highest number of assists in one match?", "leaderboard", stat="assists", season=2026, season_type=2)
    assert intent == "single_game_high"
    intent, slots = _assigned(con, "who had the highest number of 30-point games in 2024", "leaderboard", stat="points", season=2024, season_type=2)
    assert intent == "threshold_count" and slots["threshold"] == 30
    intent, slots = _assigned(con, "games where Klay Thompson made at least 2 threes including playoffs", "game_log", stat="threePointFieldGoalsMade", player="Klay Thompson", season_type=2)
    assert intent == "threshold_count" and slots["threshold"] == 2
    intent, _ = _assigned(con, "Display Luka's average assists from year to year", "player_stat", stat="assists", player="Luka Doncic", season_type=2)
    assert intent == "player_history"
    intent, slots = _assigned(con, "36-plus points SGA record", "player_stat", stat="points", season_type=2)
    assert intent == "record_when" and slots["threshold"] == 36 and slots["player"] == "Shai Gilgeous-Alexander"
    # "per game" is an average, never one game: the season ranking stands.
    intent, _ = _assigned(con, "which player has the highest points per game average in the league?", "leaderboard", stat="points", season_type=2)
    assert intent == "leaderboard"


def test_excluding_featuring_and_a_fronted_without_are_companion_phrases(con: duckdb.DuckDBPyConnection) -> None:
    """ "excluding" is "without" and "featuring" is "with", reworded; and a
    question word ends a companion phrase, so a fronted "Without Kevin
    Durant, what is Steph Curry's record" names Durant alone."""
    s = _read(con, "this season's luka game log excluding kawhi and lebron", "game_log", player="Luka Doncic")
    assert s.kind == "player" and [(c.name, c.predicate) for c in s.conditions] == [("Kawhi Leonard", "absent"), ("LeBron James", "absent")]
    s = _read(con, "Without Kevin Durant, what is Steph Curry's record against Lebron in the regular season?", "other", players=["steph curry", "lebron"])
    assert s.kind == "pair" and [(c.name, c.predicate) for c in s.conditions] == [("Kevin Durant", "absent")]
    s = _read(con, "display the PHI record featuring Embiid and Maxey", "with_without", team="Philadelphia 76ers")
    assert s.kind == "team" and [(c.name, c.predicate) for c in s.conditions] == [("Joel Embiid", "played"), ("Tyrese Maxey", "played")]


def test_the_one_player_named_in_games_he_played_is_the_subject(con: duckdb.DuckDBPyConnection) -> None:
    """ "2 threes in games Jamal Murray played" (a day10 paraphrase) names
    nobody beside him: the games he played are his own, so he is the subject,
    not a companion of nobody - read as a companion, the question had no
    subject and fell through as a league ranking. A second name keeps the
    companion reading."""
    alone = read_subject(con, "2 threes in games Stephen Curry played including playoffs", "other", Scope.from_slots({"player": "Stephen Curry"}))
    assert (alone.kind, alone.players, alone.conditions) == ("player", ("Stephen Curry",), ())
    beside = read_subject(con, "maxey points in games embiid played", "other", Scope.from_slots({"players": ["Tyrese Maxey", "Joel Embiid"]}))
    assert beside.players == ("Tyrese Maxey",) and [(c.name, c.predicate) for c in beside.conditions] == [("Joel Embiid", "played")]
    # Two companions of a team, the team routed but not a word the reading
    # finds ("76ers"), stay the team's question.
    team = read_subject(con, "show me the 76ers record when both Embiid and Paul George played", "other", Scope.from_slots({"team": "76ers", "players": ["Embiid", "Paul George"]}))
    assert team.players == () and {c.name for c in team.conditions} >= {"Joel Embiid"}


def test_the_subject_is_read_once_per_question(con: duckdb.DuckDBPyConnection, monkeypatch: pytest.MonkeyPatch) -> None:
    """``ROADMAP.md``, Phase 1: the parser reads who the question is about
    once (``read_route``), carries that reading on the route, and its child
    step and last step settle it (``settle_subject``) - three readings
    until 5.0.0, each asking the warehouse for the same names. A route
    with no subject is refused at the last step, not read for again."""
    import association.query.parse as parse

    seen: list[str] = []
    original = parse.read_subject

    def counting(connection: duckdb.DuckDBPyConnection, question: str, intent: str, scope: Scope) -> Subject:
        seen.append(intent)
        return original(connection, question, intent, scope)

    monkeypatch.setattr(parse, "read_subject", counting)
    question = "How many 30+ point games did Jokic have this season?"
    route, subject, _ = parse.read_route(con, question, ["Jokic"], "points")
    assert route.subject is not None and route.subject.players == subject.players == ("Nikola Jokic",)
    reading = parse.reading_from_route(con, question, route)
    assert seen == ["other"]
    assert reading.intent == "threshold_count" and reading.subject is not None and reading.subject.intent == "threshold_count"
    with pytest.raises(ValueError, match="needs the route's subject"):
        parse.reading_from_route(con, question, slots_route(route.intent, route.slots))


def test_a_subject_is_settled_under_the_intent_without_reading_a_name_again() -> None:
    """What depends on the intent is written from the reading already made,
    with no stage run: two teams meeting are the ``teams`` kind under
    ``head_to_head`` and a team against another under anything else; the
    words that name a child (``child_named``, the grammar's decision) are
    said once, whichever intent the subject was read under."""
    team = Subject("team", teams=("Philadelphia 76ers",), opponent="Boston Celtics", evidence=("team word '76ers'", "the words 'old' name streak"))
    meeting = settle_subject(team, "head_to_head")
    assert (meeting.kind, meeting.teams, meeting.opponent) == ("teams", ("Philadelphia 76ers", "Boston Celtics"), None)
    against = settle_subject(team, "team_quarter_points")
    assert (against.kind, against.teams, against.opponent, against.intent) == ("team", ("Philadelphia 76ers",), "Boston Celtics", "team_quarter_points")
    assert against.evidence == ("team word '76ers'",)

    player = Subject("player", players=("Nikola Jokic",))
    question = "How many 30+ point games did Jokic have this season?"
    assert child_named(player, "game_log", question) == ("threshold_count", "How many 30+ point games")
    assert child_named(player, "threshold_count", question) is None  # a child already chosen stands
    counted = settle_subject(player, "threshold_count", parent="game_log", words="How many 30+ point games")
    assert counted.intent == "threshold_count" and counted.intent_reason == "the words 'How many 30+ point games' name threshold_count" and counted.evidence[-1].endswith("name threshold_count")


@pytest.mark.parametrize(
    ("text", "names"),
    [
        ("curry", ["curry"]),
        ("Cade Cunningham this season", ["Cade Cunningham"]),
        ("a turnover", []),  # "a" is no name's word
        ("Tatum and Brown", ["Tatum", "Brown"]),
        ("Lebron and AD this season", ["Lebron", "AD"]),
        ("Tatum, Brown and Holiday", ["Tatum", "Brown", "Holiday"]),
        ("brandon miller or lamelo", ["brandon miller", "lamelo"]),  # "or" joins as "and" does
        ("and without Tatum", []),  # a joiner with nothing before it names nobody
        ("20+ points", []),
        ("draymond green out", ["draymond green"]),  # the absence word ends the name
        ("maxey scores 20+ points", ["maxey scores"]),  # what he did follows: the reading takes the leading word
    ],
)
def test_a_companion_phrases_names_are_read_by_position(text: str, names: list[str]) -> None:
    """Every name a phrase holds, not the first: reading one answered
    "Celtics record without Tatum and Brown" with the games Tatum missed - a
    different question, answered fluently. The stages' own reader of these
    phrases held these cases until 5.0.0; the subject's is the only one now."""
    from association.query.subject import _name_segments

    assert _name_segments(text) == names


def test_who_sat_out_and_who_played_is_read_from_the_question_without_the_model(con: duckdb.DuckDBPyConnection) -> None:
    """The one reader of a companion's name: by position after "without"
    (as typed where nobody is known by it - an ordinary word two players
    share, a name nobody resolves), as the player the question is known to
    hold where one is, and for "with and without X" from its "without"."""
    from association.query.subject import Companion, _conditions, beside

    absent = _conditions("Celtics record without Tatum, Brown and Holiday", ("Jayson Tatum",), Scope())
    assert [(c.name, c.predicate) for c in absent] == [("Jayson Tatum", "absent"), ("Brown", "absent"), ("Holiday", "absent")]
    assert beside(absent).absent == ("Jayson Tatum", "Brown", "Holiday") and beside(absent).played == ()
    assert _conditions("Podziemski game log without zzyzx", ("Brandin Podziemski",), Scope()) == (Companion("zzyzx", "absent", None, None),)
    assert _conditions("most games without a turnover", (), Scope()) == ()
    split = _conditions("Celtics record with and without Tatum", ("Jayson Tatum",), Scope())
    assert [(c.name, c.predicate) for c in split] == [("Jayson Tatum", "absent")]
    # Who PLAYED has to be somebody the reading knows: the words after
    # "with" are often no name at all.
    played = _conditions("jjj stats with ja morant and desmond bane last season", ("Jaren Jackson Jr.", "ja morant", "desmond bane"), Scope())
    assert beside(played).played == ("ja morant", "desmond bane")
    assert _conditions("mikal bridges game log with less than 15 fga", ("Mikal Bridges",), Scope()) == ()
    for question in (
        "show me the 76ers record when both Embiid and Paul George played",
        "show me PHI record with Embiid and Paul George",
        "PHI record when Embiid and Paul George play",
        "PHI record when Embiid with Paul George",
        "When Embiid plays with Paul George, what is the PHI record?",
        "PHI's record when Embiid and Paul George are both in the game",
    ):
        assert beside(_conditions(question, ("Joel Embiid", "Paul George"), Scope())).played == ("Joel Embiid", "Paul George"), question
    # The whole reading, with the model naming nobody: the names are the question's.
    s = _read(con, "76ers record without embiid", "team_record", team="Philadelphia 76ers")
    assert s.kind == "team" and [(c.name, c.predicate) for c in s.conditions] == [("Joel Embiid", "absent")]


def test_a_misspelled_companion_is_read_as_typed_where_it_is_one_players_near_spelling(con: duckdb.DuckDBPyConnection) -> None:
    """ "without wembyanama" matches no player as spelled, and until 5.0.0
    only the stages' reader carried it. The words after "without" are a
    name's place, so the near-spelling pass applies as it does to a name
    slot - one candidate, no ordinary word - and the span stays as typed
    for the entity index to resolve and say so. The one player in a "games
    X played" phrase whom the reading made the subject is never his own
    companion."""
    s = _read(con, "de'aaron fox last five games without wembyanama", "game_log", player="De'Aaron Fox")
    assert s.players == ("De'Aaron Fox",) and [(c.name, c.predicate) for c in s.conditions] == [("wembyanama", "absent")]
    # And where he PLAYED, a role that takes only a name the reading found.
    s = _read(con, "de'aaron fox points with wembyanama playing", "player_stat", player="De'Aaron Fox")
    assert [(c.name, c.predicate) for c in s.conditions] == [("wembyanama", "played")]
    assert "companions the router named nobody for ['wembyanama']" in s.evidence
    # Two near spellings name nobody: the question is asked, not guessed.
    # (A table is read once per block: the new row is seen in the next one.)
    con.execute("INSERT INTO players VALUES ('31', 'Vince Wembyanamo')")
    with names.loaded():
        assert _read(con, "de'aaron fox points with wembyanama playing", "player_stat", player="De'Aaron Fox").conditions == ()
        # An ordinary word is no typo, however near a name it is: "brow" is one
        # edit from Jaylen Brown, as "season" is from Tari Eason.
        assert _read(con, "de'aaron fox points with brow", "player_stat", player="De'Aaron Fox").conditions == ()
