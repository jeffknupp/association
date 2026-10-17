"""Fixture tests for :mod:`association.query.subject` - one reading of who a
question is about. Each test is a rule of the reading, named for the
question it reads."""

from __future__ import annotations

import pathlib
from typing import Any

import duckdb
import pytest
from routed import Routed, measure_of, reading_of

from association.nba.season import current_season
from association.query import names, subject
from association.query.decisions import Decision
from association.query.entities import find_teams, names_of, teams_of
from association.query.lines import threshold_of
from association.query.names import NameIndex
from association.query.reading import (
    GAME_HIGHS,
    GAMES_COUNTED,
    LINE_RECORD,
    LINE_RUNS,
    NETPOINTS_FINGERPRINT,
    PLAYER_COMPARISON,
    PLAYER_LINE,
    PLAYER_LOG,
    PLAYER_MEETINGS,
    PLAYER_RANKING,
    PLAYER_SPLITS,
    PRESENCE_SPLIT,
    SEASON_HISTORY,
    SHOT_CHART,
    SHOT_DISTANCE,
    TEAM_GAME_HIGHS,
    TEAM_LINE,
    TEAM_MEETINGS,
    TEAM_PERIOD_TOTAL,
    TEAM_RANKING,
    TEAM_RECORD,
    Companion,
    Cuts,
    Filtered,
    Line,
    Measure,
    PointShape,
    Reading,
    Span,
    Window,
)
from association.query.reading import Subject as Who
from association.query.subject import SUBJECT_KINDS, Subject, apply_subject, child_named, question_supports, read_subject, settle_subject


def _applied(con: duckdb.DuckDBPyConnection, question: str, asked: PointShape | None, scope: Reading) -> tuple[Filtered, list[Decision], list[str], PointShape | None]:
    """``apply_subject`` over a test's typed reading under the key
    ``asked``: the typed filters the reading wrote, the decisions, the names
    dropped and the key."""
    applied = apply_subject(read_subject(names_of(con), question, asked, scope), scope, asked=asked)
    return applied.scope, applied.decisions, applied.dropped, applied.asked


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


def _read(con: duckdb.DuckDBPyConnection, question: str, asked: PointShape | None = PLAYER_LINE, *, scope: Reading | None = None) -> Subject:
    """The subject reading of ``question`` under the key ``asked``, over a
    test's typed reading (none by default)."""
    return read_subject(names_of(con), question, asked, scope or Reading())


def _settled(con: duckdb.DuckDBPyConnection, question: str, asked: PointShape | None = PLAYER_LINE, *, scope: Reading | None = None) -> Subject:
    """The subject read, then settled under ``asked`` or the child its
    shape names for it (the grammar's decision alone; the grammar's rules
    and the taggers run in ``parse.read_route``)."""
    read = _read(con, question, asked, scope=scope)
    named = child_named(read, asked, question)
    chosen, words = named if named is not None else (asked, None)
    return settle_subject(read, chosen, parent=asked, words=words)


def test_every_kind_the_reading_returns_is_declared(con: duckdb.DuckDBPyConnection) -> None:
    for q in ("jokic ppg", "lebron vs kawhi head to head", "celtics record", "lakers vs celtics record", "centers vs kings log", "most points this season", "most rebounds by a hawk player"):
        assert _read(con, q).kind in SUBJECT_KINDS


def test_a_name_the_question_gives_is_the_subject_whatever_the_model_named(con: duckdb.DuckDBPyConnection) -> None:
    """AGENTS.md, "A model invents names": the model can name Jusuf Nurkic
    for "compare jokic and embiid". A model's name the question does not
    support is not the subject; one it does (a completion of the surname
    given) is."""
    s = _read(con, "compare jokic and embiid", PLAYER_COMPARISON, scope=Reading(who=Who(kind="pair", players=("Nikola Jokic", "Jusuf Nurkic"))))
    assert s.kind == "pair" and s.players == ("Nikola Jokic", "Joel Embiid")
    s = _read(con, "jokic career averages", scope=Reading(who=Who(kind="player", players=("Nikola Jokic",))))
    assert s.kind == "player" and s.players == ("Nikola Jokic",)


def test_a_near_spelling_initials_and_a_nickname_support_a_models_name(con: duckdb.DuckDBPyConnection) -> None:
    """ "embid" is Embiid (the model corrects typos), "SGA" his initials,
    "kd" a curated nickname: each is the question holding the name."""
    assert _read(con, "how many games did embid play", scope=Reading(who=Who(kind="player", players=("Joel Embiid",)))).players == ("Joel Embiid",)
    assert _read(con, "how many 20+ point games did SGA have", GAMES_COUNTED, scope=Reading(who=Who(kind="player", players=("Shai Gilgeous-Alexander",)))).players == ("Shai Gilgeous-Alexander",)
    assert question_supports("Kevin Durant", "without kd") and not question_supports("Kevin Durant", "without the ball")


def test_a_nickname_with_its_possessive_s_glued_on_names_the_subject(con: duckdb.DuckDBPyConnection) -> None:
    """ "Lebrons record when kd plays" is LeBron's record, the companion
    beside him, not the companion's splits: a curated nickname with its
    possessive "s" and no apostrophe is the nickname; a surname plus "s" is
    not ("browns")."""
    s = _read(con, "Lebrons record when kd plays")
    assert s.players == ("LeBron James",) and [c.player for c in s.conditions] == ["Kevin Durant"]
    assert _read(con, "kds stats this season").players == ("Kevin Durant",)
    assert _read(con, "browns stats this season").players == ()


def test_a_swapped_pair_of_letters_is_one_edit(con: duckdb.DuckDBPyConnection) -> None:
    """ISSUES #239: a transposition is one edit, within the one-edit budget
    of a five- or six-letter word, so "jokci" supports the model's Nikola
    Jokic - the Damerau-Levenshtein the entity index measures in DuckDB,
    one metric for both."""
    assert question_supports("Nikola Jokic", "jokci stats")
    assert question_supports("Tyrese Maxey", "maxye last 5 games")
    assert question_supports("LeBron James", "leborn stats")
    # Two separate edits in a short word are still a different word.
    assert not question_supports("Nikola Jokic", "jkoci stats")
    assert _read(con, "jokci career averages", scope=Reading(who=Who(kind="player", players=("Nikola Jokic",)))).players == ("Nikola Jokic",)


def test_an_ordinary_word_that_is_also_a_whole_name_is_not_a_player(con: duckdb.DuckDBPyConnection) -> None:
    """ "best" is Travis Best, "head" is Luther Head and "pointer" is
    Sir'Dominic Pointer to players_named_in; a question is about them only
    when the model read it that way too. "steph curry" keeps Curry - a
    food in the dictionary - because the question uses his nickname."""
    assert _read(con, "Best record from 2010-11 to 2018-19", TEAM_RANKING).kind == "everyone"
    assert _read(con, "lebron vs kawhi head to head", PLAYER_MEETINGS).players == ("LeBron James", "Kawhi Leonard")
    assert _read(con, "How far was Curry average three pointer?", SHOT_DISTANCE, scope=Reading(who=Who(kind="player", players=("Stephen Curry",)))).players == ("Stephen Curry",)
    assert _read(con, "steph curry record vs lebron without kd", PRESENCE_SPLIT).players == ("Stephen Curry", "LeBron James")
    assert _read(con, "travis best career points", scope=Reading(who=Who(kind="player", players=("Travis Best",)))).players == ("Travis Best",)


def test_the_ordinary_words_do_not_depend_on_the_machine(monkeypatch: pytest.MonkeyPatch) -> None:
    """The word list ships with the package, not read from
    /usr/share/dict/words, which GitHub's runner lacks (where "Best record
    ..." above would be Travis Best's question). Hide the system file and
    the list must still be whole."""
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
    s = _read(con, "show maxey's games against boston in the past two seasons", PLAYER_LOG, scope=Reading(who=Who(kind="player", players=("Tyrese Maxey",))))
    assert s.kind == "player" and s.players == ("Tyrese Maxey",) and s.opponent == "Boston Celtics"
    s = _read(con, "de'aaron fox vs magic last five games", PLAYER_LOG, scope=Reading(who=Who(kind="player", players=("De'Aaron Fox",))))
    assert s.players == ("De'Aaron Fox",) and s.opponent == "Orlando Magic"
    s = _read(con, "how many 3 pointers have the magic made", PLAYER_RANKING)
    assert s.kind == "team" and s.teams == ("Orlando Magic",)


def test_a_companions_role_comes_from_the_question_not_the_models_reading(con: duckdb.DuckDBPyConnection) -> None:
    """ "without kd" makes Durant a companion; Embiid handed as an absent
    companion on "Embiid's record against Boston" is not one.
    A misspelled companion ("wembyanama") takes the model's corrected name
    only because the question's own phrase is a near spelling of it."""
    s = _read(con, "steph curry record vs lebron regular season without kd", PRESENCE_SPLIT, scope=Reading(companions=(Companion(player="kd", predicate="absent"),)))
    assert s.kind == "pair" and s.players == ("Stephen Curry", "LeBron James") and s.companions == ("Kevin Durant",)
    s = _read(con, "Embiid's record against Boston this year", TEAM_MEETINGS, scope=Reading(companions=(Companion(player="Joel Embiid", predicate="absent"),)))
    assert s.kind == "player" and s.players == ("Joel Embiid",) and s.companions == () and s.opponent == "Boston Celtics"
    s = _read(
        con,
        "de'aaron fox vs magic last five games without wembyanama",
        PLAYER_MEETINGS,
        scope=Reading(who=Who(kind="player", players=("De'Aaron Fox",)), companions=(Companion(player="Victor Wembanyama", predicate="absent"),)),
    )
    assert s.players == ("De'Aaron Fox",) and s.companions == ("Victor Wembanyama",)


def test_a_team_conditioned_on_a_player_is_the_teams_question(con: duckdb.DuckDBPyConnection) -> None:
    """ "the Sixers' record when Maxey scored 20+" is about the Sixers; Maxey
    is the condition. And "for the Sixers" with no player subject names the
    team, not a player's own team."""
    s = _read(con, "what was the sixers record when maxey scored 20+ points?", LINE_RECORD, scope=Reading(who=Who(kind="player", players=("Tyrese Maxey",), teams=("Philadelphia 76ers",))))
    assert s.kind == "team" and s.teams == ("Philadelphia 76ers",) and s.companions == ("Tyrese Maxey",)
    s = _read(con, "show me splits for the sixers when maxey scores 20+ points", PLAYER_SPLITS, scope=Reading(who=Who(kind="player", players=("Tyrese Maxey",))))
    assert s.kind == "team" and s.teams == ("Philadelphia 76ers",) and s.own_team is None
    s = _read(con, "lebron stats as a starter for the lakers", scope=Reading(who=Who(kind="player", players=("LeBron James",))))
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
    """No reading puts a TEAM as the player of a one-player key ("hawk" on
    the Hawks' players' rebounds) or splits a team's record by a player
    beside it with no line to split by: the parser reads these wordings
    with the team as the subject - never a team where a player belongs,
    never a companion's record with no line."""
    from association.query.parse import read_route
    from association.query.reading import PLAYER_ASKS

    route = read_route(names_of(con), question, named, stat)
    reading = route
    assert reading.subject is not None
    player = reading.who.player
    team_subject = reading.subject.kind in ("team", "team_players") and not reading.subject.players
    if reading.asked in PLAYER_ASKS and team_subject and player:
        # A record over a line with no stat is refused by the cause naming
        # the stat missing ("celtics record when jayson tatum has 30", #340).
        no_line = reading.asked == LINE_RECORD and not (threshold_of(reading) and reading.measure is not None and reading.measure.named) and reading.point_refusal is None
        assert not find_teams(teams_of(con), player) and not no_line, (question, reading.asked, player)


@pytest.mark.parametrize("question", ["celtics record when jayson tatum has 30", "sixers record when maxey has 20+", "celtics record when tatum had 30 and brown plays"])
def test_a_companions_number_with_no_stat_is_refused_by_the_stat_missing(con: duckdb.DuckDBPyConnection, question: str) -> None:
    """ "celtics record when tatum has 30" names no stat after the number:
    no line could be read, and the team's record fell back to the
    with/without split over Tatum - a different question, fluently (ISSUES.md
    #340). The number is read as the companion's line on no stat, and the
    record over it is refused by the cause naming the stat it lacks; the
    model's stat is no evidence for it (empty on a third of the recorded
    "record when X scores N points" replies that name the stat outright)."""
    from association.query.parse import read_route

    reading = read_route(names_of(con), question, [], "points")
    assert reading.asked == LINE_RECORD and reading.point_refusal is not None and reading.point_refusal.kind == "threshold_needs_stat", (question, reading.asked, reading.point_refusal)


def test_a_word_after_versus_near_a_team_and_a_player_is_kept_for_the_resolution_to_ask(con: duckdb.DuckDBPyConnection) -> None:
    """ "Bobby Portis vs magics Gamelog": "magics" is near the Orlando Magic
    and Magic Johnson, and was read as neither - his log over every
    opponent (ISSUES.md #247). Kept as typed, the opponent's resolution asks
    which; a whole word of a player's name is still his ("vs hawes")."""
    s = _read(con, "luka vs magics Gamelog", PLAYER_LOG, scope=Reading(who=Who(kind="player", players=("Luka Doncic",))))
    assert s.opponent == "magics"


@pytest.mark.parametrize(("question", "teams"), [("celtcs record this season", ("celtcs",)), ("most rings all time", ()), ("kngs record vs lakers", ("kngs",))])
def test_a_teams_typo_is_the_subject_where_no_other_subject_stands(con: duckdb.DuckDBPyConnection, question: str, teams: tuple[str, ...]) -> None:
    """ "nuggers record this season" named its team by a typo and was read
    as no subject: every team ranked (ISSUES.md #247). A word near exactly
    one team, no ordinary word ("rings" is one edit from the Kings) and no
    player's, is that team where nothing else is the subject - kept as
    typed for the team's resolution to read and say so."""
    assert _read(con, question, TEAM_RECORD).teams == teams


def test_a_models_name_filed_as_the_wrong_kind_is_dropped(con: duckdb.DuckDBPyConnection) -> None:
    """ "Alamhamed Embiid" handed as a team; "Boston Celtics" as a player.
    Neither is what the subject's field claims."""
    s = _read(con, "Embiid's record against Boston this year", TEAM_MEETINGS, scope=Reading(who=Who(kind="teams", teams=("Alamhamed Embiid", "Boston Celtics"))))
    assert s.kind == "player" and s.players == ("Joel Embiid",) and s.teams == ()
    s = _read(con, "show maxey's games against boston", PLAYER_LOG, scope=Reading(who=Who(kind="pair", players=("Tyrese Maxey", "Boston Celtics"))))
    assert s.players == ("Tyrese Maxey",)


def test_two_teams_a_position_group_a_teams_players_and_everyone(con: duckdb.DuckDBPyConnection) -> None:
    assert _read(con, "Lakers vs Celtics record this season", TEAM_MEETINGS, scope=Reading(who=Who(kind="teams", teams=("Los Angeles Lakers", "Boston Celtics")))).kind == "teams"
    s = _read(con, "Centers stats game log vs kings", PLAYER_LOG, scope=Reading(who=Who(kind="team", teams=("Kings",))))
    assert s.kind == "position" and s.position == "C" and s.opponent == "Sacramento Kings" and s.teams == ()
    s = _read(con, "Most reb by a hawk player history", SEASON_HISTORY, scope=Reading(who=Who(kind="player", players=("Hawks",))))
    assert s.kind == "team_players" and s.teams == ("Atlanta Hawks",)
    # yardstick-v2 F049: "player" beside a team is the team's players - their
    # best first-quarter scorer, not the TEAM's quarter points.
    s = _read(con, "hornets average 1st quarter points player", TEAM_PERIOD_TOTAL, scope=Reading(who=Who(kind="team", teams=("Charlotte Hornets",))))
    assert s.kind == "team_players"
    s = _read(con, "oklahoma city thunder all-time triple doubles", scope=Reading(who=Who(kind="player", players=("Oklahoma City Thunder",))))
    assert s.kind == "team" and s.teams == ("Oklahoma City Thunder",)
    assert _read(con, "PHI record 2026", TEAM_RECORD, scope=Reading(who=Who(kind="team", teams=("Philadelphia 76ers",)))).teams == ("Philadelphia 76ers",)
    assert _read(con, "most points in a single game 1980 nba season", GAME_HIGHS).kind == "everyone"


def test_apply_subject_writes_the_players_the_question_names_onto_the_models_names(con: duckdb.DuckDBPyConnection) -> None:
    """The invented-name check: a model's name the question supports is
    kept as the model spelled it; one it never held is replaced by the
    question's spare name, or reported for the refusal - never guessed."""

    handed = Reading(who=Who(kind="pair", players=("Shai Gilgeous-Alexander", "Jusuf Nurkic")))
    after, decisions, dropped, _ = _applied(con, "compare sga and embiid", PLAYER_COMPARISON, handed)
    assert after.who.players == ("Shai Gilgeous-Alexander", "Joel Embiid") and dropped == []
    assert [(d.field, d.before, d.after) for d in decisions] == [("players", "Jusuf Nurkic", "Joel Embiid")]

    single = Reading(who=Who(kind="player", players=("Ben Simmons",)))
    after, decisions, dropped, _ = _applied(con, "how many points does embiid average", PLAYER_LINE, single)
    assert after == Reading(who=Who(kind="player", players=("Joel Embiid",))) and [d.field for d in decisions] == ["player"] and dropped == []

    kept = Reading(who=Who(kind="player", players=("Nikola Jokic",)))
    assert _applied(con, "jokic ppg", PLAYER_LINE, kept)[:3] == (Reading(who=Who(kind="player", players=("Nikola Jokic",))), [], [])

    # A companion the model filed among the players (two players' meetings)
    # is the question's own name, kept (F157).
    pair = Reading(who=Who(kind="pair", players=("De'Aaron Fox", "Victor Wembanyama")))
    after, decisions, dropped, _ = _applied(con, "de'aaron fox vs magic last five games without wembyanama", PLAYER_MEETINGS, pair)
    assert after.who.players == ("De'Aaron Fox", "Victor Wembanyama") and dropped == []

    # A kept model's name the question spells differently takes the question's
    # own resolved name: a bare surname completed, a fabricated given name
    # corrected, a two-edit near miss ("Deron Williams" for "derozan") put
    # right.
    bare = Reading(who=Who(kind="player", players=("Jokic",)))
    after, decisions, _, _ = _applied(con, "How many 30+ point games did Jokic have this season?", GAMES_COUNTED, bare)
    assert after == Reading(who=Who(kind="player", players=("Nikola Jokic",))) and [(d.before, d.after) for d in decisions] == [("Jokic", "Nikola Jokic")]
    near = Reading(who=Who(kind="player", players=("Deron Williams",)))
    assert _applied(con, "derozan career points vs knicks", PLAYER_LINE, near)[0] == Reading(who=Who(kind="player", players=("DeMar DeRozan",)))
    right = Reading(who=Who(kind="player", players=("Deron Williams",)))
    assert _applied(con, "deron williams career points", PLAYER_LINE, right)[:3] == (Reading(who=Who(kind="player", players=("Deron Williams",))), [], [])

    # A player the model left OUT (the question names two, the model's names
    # hold one) is not put back here - the parser reads every name the
    # question holds - and must not trip anything.
    omitted = Reading(who=Who(kind="player", players=("Nikola Jokic",)))
    after, decisions, dropped, _ = _applied(con, "compare jokic and embiid", PLAYER_COMPARISON, omitted)
    assert (after.who.players, decisions, dropped) == (("Nikola Jokic",), [], [])

    nobody = Reading(who=Who(kind="pair", players=("Ronaldo Lopes", "Nikola Jokic")))
    after, decisions, dropped, _ = _applied(con, "compare jokic's fingerprint to last season", NETPOINTS_FINGERPRINT, nobody)
    assert dropped == ["Ronaldo Lopes"] and decisions == [] and after.who.players == ("Ronaldo Lopes", "Nikola Jokic")  # reported, untouched


def test_a_typo_of_the_questions_own_is_resolved_from_the_span_the_models_name_anchors(con: duckdb.DuckDBPyConnection) -> None:
    """ "Seph Curry" is the QUESTION's typo: no whole-word match finds Seth
    Curry in it, and the model's "Stephen Curry" is supported by "curry".
    The span around the model's name, resolved with near spelling
    (subject.question_derived_player), is what reaches him; "Payton
    Prichard" likewise reaches Payton Pritchard."""
    from association.query.subject import apply_subject

    scope = Reading(who=Who(kind="player", players=("Stephen Curry",)), measure=measure_of("threePointFieldGoalsMade"), span=Span(season=2025))
    s = read_subject(names_of(con), "Show a shot chart for 3 point shots by Seph Curry's last season", SHOT_CHART, scope)
    assert s.players == ("Seth Curry",)
    applied = apply_subject(s, scope, asked=SHOT_CHART)
    assert applied.scope.who.player == "Seth Curry" and applied.dropped == [] and [(d.field, d.before, d.after) for d in applied.decisions] == [("player", "Stephen Curry", "Seth Curry")]

    typo = Reading(who=Who(kind="player", players=("Payton Prichard",)), cuts=Cuts(opponent="Philadelphia 76ers", venue="home"))
    after = _applied(con, "Payton Prichard stats vs 76ers at home including playoffs game log", PLAYER_LOG, typo)[0]
    assert (after.who.player, after.cuts.opponent) == ("Payton Pritchard", "Philadelphia 76ers")


def test_the_models_spelling_stands_where_a_whole_word_names_somebody_else(con: duckdb.DuckDBPyConnection) -> None:
    """ "kareem" is a whole word of exactly one player here (Kareem Rush)
    and "lanier" of one (Chaz Lanier), so players_named_in names both - two
    real players the question is not about - and `apply_subject` must not
    respell the model's correct "Kareem Abdul-Jabbar" to Kareem Rush from
    that (ISSUES.md #123's shape). The anchored span settles neither name
    (subject.question_derived_player's own guard), so the model's spelling
    stands and the reader says nobody matched."""
    from association.query.subject import apply_subject

    scope = Reading(who=Who(kind="pair", players=("Kareem Abdul-Jabbar", "Bob Lanier")))
    s = read_subject(names_of(con), "kareem stats vs bob lanier", PLAYER_MEETINGS, scope)
    assert s.players == ("Kareem Abdul-Jabbar", "Bob Lanier")
    applied = apply_subject(s, scope, asked=PLAYER_MEETINGS)
    assert (applied.decisions, applied.dropped, applied.scope.who.players) == ([], [], ("Kareem Abdul-Jabbar", "Bob Lanier"))


def test_a_player_filed_as_the_opponent_is_checked_like_the_subject(con: duckdb.DuckDBPyConnection) -> None:
    """#206: "jay huff game log vs Embiid" with the model's opponent Nikola
    Jokic. The reading holds a model's opponent apart
    from the question's own pair. On the parser's path the pair relation is
    named from the subject's kind - the question's own two players - and a
    name the model supplied that the question never held, which nothing in
    it can replace, is carried as misread for the refusal rather than
    answered about. A team opponent narrows the one player's games."""
    s = read_subject(names_of(con), "jay huff game log vs Embiid", PLAYER_MEETINGS, Reading(who=Who(kind="player", players=("Jaylen Huff",)), cuts=Cuts(opponent="Nikola Jokic")))
    # A log against a player is his own games with the other on the far side, the model's Jokic still carried as invented.
    assert s.kind == "player" and s.players == ("Jay Huff",) and [(c.player, c.side) for c in s.conditions] == [("Joel Embiid", "opponent")] and s.invented == ("Nikola Jokic",)
    invented = _parsed(con, "jay huff game log vs Embiid", ["Jaylen Huff", "Nikola Jokic"])
    assert invented.asked == PLAYER_LOG and invented.misread == ("Nikola Jokic",)
    assert (
        read_subject(names_of(con), "luka vs the lakers", PLAYER_LOG, Reading(who=Who(kind="player", players=("Luka Doncic",)), cuts=Cuts(opponent="Los Angeles Lakers"))).opponent
        == "Los Angeles Lakers"
    )
    team = _parsed(con, "luka vs the lakers", ["luka", "lakers"])
    assert team.who.player == "Luka Doncic" and team.cuts.opponent == "Los Angeles Lakers" and team.misread == ()
    # A log against a player: his own games, the other on the far side; the bare pair is the matchup.
    log = _parsed(con, "luka game log vs embiid", ["luka", "embiid"])
    assert log.asked == PLAYER_LOG and log.who.player == "Luka Doncic" and [(c.player, c.side) for c in log.companions] == [("Joel Embiid", "opponent")] and log.misread == ()
    pair = _parsed(con, "luka vs embiid", ["luka", "embiid"])
    assert pair.asked == PLAYER_MEETINGS and pair.who.players == ("Luka Doncic", "Joel Embiid") and not pair.cuts.opponent and pair.misread == ()


def test_a_supported_name_stays_as_the_model_spelled_it(con: duckdb.DuckDBPyConnection) -> None:
    """A near spelling ("embid" - the model corrected the surname and
    invented the given name; resolution then suggests rather than this
    replacing a half-supported name), initials ("jb") and a curated nickname
    ("The Answer") each support a model's name, and a question with no
    model's name has nothing to check."""

    for question, asked, scope in (
        ("compare sga and embid", PLAYER_COMPARISON, Reading(who=Who(kind="pair", players=("Shai Gilgeous-Alexander", "Jemel Embiid")))),
        ("compare jb and embiid", PLAYER_COMPARISON, Reading(who=Who(kind="pair", players=("Jaylen Brown", "Joel Embiid")))),
        ("Show me The Answer's avg points", PLAYER_LINE, Reading(who=Who(kind="player", players=("Allen Iverson",)))),
        ("how many points did Luka average?", PLAYER_LINE, Reading(who=Who(kind="player", players=("Luka Doncic",)))),
        ("who led the league in scoring?", PLAYER_RANKING, Reading(measure=measure_of("points"))),
    ):
        after, decisions, dropped, _ = _applied(con, question, asked, scope)
        assert (decisions, dropped) == ([], []), question
        assert after == scope, question


def test_a_position_phrase_or_a_filler_word_handed_as_the_player_is_not_a_player(con: duckdb.DuckDBPyConnection) -> None:
    """The model files "shooting guard" as the player on "highest 3 point
    percentage ... by a shooting guard" (F056) and "player" on "Most points
    in 15th season played" (F099). Both words are in the question, so the
    support check passes them; neither is a name. The first is the
    position-group subject; the second nobody - and the compiler reads the
    kind."""
    s = _read(con, "highest 3 point percentage in a season by a shooting guard", PLAYER_RANKING, scope=Reading(who=Who(kind="player", players=("shooting guard",))))
    assert s.kind == "position" and s.position == "SG" and s.players == () and s.invented == ()
    s = _read(con, "Most points in 15th season played", PLAYER_RANKING, scope=Reading(who=Who(kind="player", players=("player",))))
    assert s.kind == "everyone" and s.players == () and s.invented == ()
    # A team handed as the player is the team's players' games, not a player.
    s = _read(con, "oklahoma city thunder all-time triple doubles", GAMES_COUNTED, scope=Reading(who=Who(kind="player", players=("Thunder",))))
    assert s.kind == "team" and s.teams == ("Oklahoma City Thunder",) and s.players == ()


def test_the_compare_whose_second_player_the_model_filed_as_the_opponent(con: duckdb.DuckDBPyConnection) -> None:
    """ "compare Jaylen Brown and Jason Tatum's netpoints" handed with Tatum
    as the opponent: the question names two players; the reading says so - the
    second as typed, since "jason tatum" is the one near spelling of Jayson
    Tatum, which the entity index reads and the answer says (ISSUES.md
    #346: a near-spelled pair is read from the words as typed)."""
    s = _read(
        con,
        "compare Jaylen Brown and Jason Tatum's netpoints over the past four seasons",
        PLAYER_LINE,
        scope=Reading(who=Who(kind="player", players=("Jaylen Brown",)), cuts=Cuts(opponent="Jason Tatum")),
    )
    assert s.kind == "pair" and s.players == ("Jaylen Brown", "Jason Tatum")


# ---------------------------------------------------------------------------
# Kind-assigned keys (subject.KIND_ASSIGNED_ASKS): the children the
# question's own words name under a parent key, gated on the subject's kind.
# Each case is a recorded question (~/association-research/intent-shrink/),
# arriving under the PARENT key with what the model could have handed beside
# the words - and the child's own fields read back off the text.


def _parsed(con: duckdb.DuckDBPyConnection, question: str, names: list[str], stat: str = "") -> Any:
    """The Reading the parser settles for ``question``, the normalizer's
    reply stubbed as ``names`` and ``stat`` (``parse.read_route``)."""
    from association.query.parse import read_route

    return read_route(names_of(con), question, names, stat)


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
    # The parser reads the subject before the taggers settle the season, so
    # the reading takes the question's own: "the hornets in 2008" is Chris
    # Paul's New Orleans team, not today's Charlotte Hornets (ISSUES.md #316).
    own = _parsed(franchises, "chris paul assists for the hornets in 2008", ["chris paul", "hornets"], "assists")
    assert own.cuts.tenure == "New Orleans Hornets" and own.span.season == 2008
    assert _parsed(franchises, "kevin durant points per game for the sonics in 2008", ["kevin durant", "sonics"], "points").cuts.tenure == "Seattle SuperSonics"
    # An opponent the same way.
    against = _parsed(franchises, "kobe points against the hornets in 2008", ["kobe", "hornets"], "points")
    assert against.cuts.opponent == "New Orleans Hornets"
    # With no season named the name is today's, and the tenure rule reads a career.
    today = _parsed(franchises, "chris paul assists for the hornets", ["chris paul", "hornets"], "assists")
    assert today.cuts.tenure == "Charlotte Hornets" and today.span.career


def _assigned(con: duckdb.DuckDBPyConnection, question: str, parent: PointShape | None, scope: Reading | None = None) -> tuple[PointShape | None, Reading]:
    """The key and the Reading the agent answers from for a question whose
    reading arrives under ``parent``: the one subject reading, the parser's
    child step and the grammar's rules (``parse._read_route_staged``), the
    taggers (``parse._tagged``), then its last step (``parse._reading``).
    ``scope`` is what the model would have handed beside the words - the
    names (the subject the rules are handed, and the opponent), its stat
    (the measure's spelling) - typed."""
    from association.query.parse import _read_route_staged, _tagged

    given = scope or Reading()
    stat = given.measure.as_typed if given.measure is not None else None
    # The subject is read once; the rules run once, under the child the
    # words name for it or under the parent; the last step settles nothing.
    who = read_subject(names_of(con), question, parent, given)
    settled, decisions, words, _claims = _read_route_staged(question, stat, parent, who, given.who, given.cuts.opponent)
    tagged = _tagged(question, settled, who.conditions, model_stat=stat)[0] if not settled.coach else Reading()
    reading = reading_of(con, question, Routed(settled.asked, tagged, settle_subject(who, settled.asked, parent=parent, words=words), decisions))
    return reading.asked, reading


def _threshold(reading: Reading) -> float | None:
    """The count's own line on the whole game."""
    return next((line.value for line in reading.lines if line.keyed and line.period is None), None)


def _below(reading: Reading) -> list[str]:
    """The lines kept under a number, as typed."""
    return [line.as_typed for line in reading.lines if line.narrows and line.below]


def _stat(reading: Reading) -> str | None:
    """The measure's spelling."""
    return reading.measure.as_typed if reading.measure is not None else None


def test_a_count_of_games_over_a_threshold_is_assigned_under_its_parents(con: duckdb.DuckDBPyConnection) -> None:
    for parent in (PLAYER_LOG, PLAYER_LINE, PLAYER_RANKING, None):
        asked, reading = _assigned(
            con,
            "How many 30+ point games did Jokic have this season?",
            parent,
            scope=Reading(who=Who(kind="player", players=("Jokic",)), measure=Measure(key="points", as_typed="points"), span=Span(season=2026, season_type=2)),
        )
        assert asked == GAMES_COUNTED, parent
        assert _threshold(reading) == 30 and _stat(reading) == "points" and reading.span.season == 2026 and "Jokic" in (reading.who.player or ""), reading.stated()
    # No "+": "30 pt games" is still thirty or more, and "pt" a point.
    asked, reading = _assigned(con, "who had the most 30 pt games in 2024", PLAYER_RANKING, scope=Reading(measure=Measure(key="points", as_typed="points"), span=Span(season=2024, season_type=2)))
    assert asked == GAMES_COUNTED and _threshold(reading) == 30 and reading.span.season == 2024 and reading.who.player is None
    # "How many times" with no season is a career.
    asked, reading = _assigned(
        con, "How many times did wembanyama score 30+ points", None, scope=Reading(who=Who(kind="player", players=("wembanyama",)), measure=Measure(key="points", as_typed="points"))
    )
    assert asked == GAMES_COUNTED and _threshold(reading) == 30 and reading.span.career and reading.span.season is None
    # A ceiling is the count's own line: no threshold at all, and the
    # attempted column the question names - not the model's nearest made-stat.
    asked, reading = _assigned(
        con,
        "Sga games with under 14 fta in his whole career",
        PLAYER_LOG,
        scope=Reading(who=Who(kind="player", players=("Shai Gilgeous-Alexander",)), measure=Measure(key="freeThrowsMade", as_typed="freeThrowsMade"), span=Span(season_type=2)),
    )
    assert asked == GAMES_COUNTED and _below(reading) == ["under 14 fta"] and _stat(reading) == "freeThrowsAttempted" and _threshold(reading) is None
    # "How many games ... N stat" with bare numbers (ISSUES.md #290): the count, its lines the
    # five numbers - not the league's ranking by one stat.
    question = "since 2000-01, how many games have players recorded 33 points, 13 rebounds, 10 assists, 2 blocks, and 2 steals?"
    asked, reading = _assigned(con, question, PLAYER_RANKING, scope=Reading(measure=Measure(key="rebounds", as_typed="rebounds")))
    assert asked == GAMES_COUNTED and _threshold(reading) == 33 and reading.who.player is None
    asked, reading = _assigned(
        con, "how many games in 2005-2006 did kobe score under 25 points", PLAYER_LOG, scope=Reading(who=Who(kind="player", players=("Kobe Bryant",)), measure=Measure(key="points", as_typed="points"))
    )
    assert asked == GAMES_COUNTED and _below(reading) == ["under 25 points"]
    # A playoff round between the line and "games": the count, its round refused by name - not a
    # player's line refusing the "40" as a number it did not use.
    asked, reading = _assigned(con, "how many 40 point finals games does kobe have", PLAYER_LINE, scope=Reading(who=Who(kind="player", players=("Kobe Bryant",))))
    assert asked == GAMES_COUNTED and _threshold(reading) == 40


def test_a_count_with_no_threshold_in_the_text_stays_under_its_parent(con: duckdb.DuckDBPyConnection) -> None:
    """The grammar's rules, run under the child, decline a count with no
    threshold - so the words alone do not move it off its parent."""
    asked, reading = _assigned(con, "how many times has jokic been named mvp", None, scope=Reading(who=Who(kind="player", players=("Nikola Jokic",)), measure=Measure(key="points", as_typed="points")))
    # The taggers, run under the parent once the child declined, add their own season type.
    assert asked is None and reading.filters() == Reading(who=Who(kind="player", players=("Nikola Jokic",)), measure=Measure(key="points", as_typed="points"), span=Span(season_type=2)).filters()


def test_two_teams_meeting_is_not_a_count_of_games(con: duckdb.DuckDBPyConnection) -> None:
    """The kind gate: "how many times" on two TEAMS is their meetings'
    question, not a count of games."""
    asked, reading = _assigned(
        con,
        "how many times did the 76ers play boston",
        TEAM_MEETINGS,
        scope=Reading(who=Who(kind="teams", teams=("Philadelphia 76ers", "Boston Celtics")), measure=Measure(key="points", as_typed="points")),
    )
    assert asked == TEAM_MEETINGS and reading.who.teams == ("Philadelphia 76ers", "Boston Celtics")


def test_a_single_game_high_is_assigned_and_its_subject_restored(con: duckdb.DuckDBPyConnection) -> None:
    # The model dropped Kawhi (F093); under a game log nothing restores him,
    # and the single-game high the words settle is his.
    asked, reading = _assigned(con, "kawhi most threes in a game", PLAYER_LOG, scope=Reading(measure=Measure(key="threePointFieldGoalsMade", as_typed="threePointFieldGoalsMade")))
    assert asked == GAME_HIGHS and reading.who.player == "Kawhi Leonard" and _stat(reading) == "threePointFieldGoalsMade"
    asked, reading = _assigned(
        con, "who had the most assists in a single game this season?", PLAYER_RANKING, scope=Reading(measure=Measure(key="assists", as_typed="assists"), span=Span(season=2026, season_type=2))
    )
    assert asked == GAME_HIGHS and reading.who.player is None
    asked, reading = _assigned(
        con, "Diabate career high assists", PLAYER_LINE, scope=Reading(who=Who(kind="player", players=("Diabate",)), measure=Measure(key="assists", as_typed="assists"), span=Span(season_type=2))
    )
    assert asked == GAME_HIGHS and reading.span.career
    # Precedence: a career's most points IN A GAME is a single-game high
    # before it is a count of games.
    asked, reading = _assigned(
        con,
        "PODZIEMSKI career most points in a game",
        PLAYER_LOG,
        scope=Reading(who=Who(kind="player", players=("PODZIEMSKI",)), measure=Measure(key="points", as_typed="points"), span=Span(season_type=2)),
    )
    assert asked == GAME_HIGHS and reading.span.career


def test_a_single_game_named_without_an_article_is_one_game(con: duckdb.DuckDBPyConnection) -> None:
    """ "single game" is one game with an article or without (ISSUES.md
    #260): "this season's single game with the most assists" is a single
    game's high, not the assists-per-game leaders, and "most 3 pointers made
    in single game 24-25" not the season's total. Neither names anybody - "single game with"
    is no player called Single - and the kind still gates it: a team's
    single game is not a player's high."""
    asked, reading = _assigned(
        con, "this season's single game with the most assists", PLAYER_RANKING, scope=Reading(measure=Measure(key="assists", as_typed="assists"), span=Span(season=2026, season_type=2))
    )
    assert asked == GAME_HIGHS and _stat(reading) == "assists" and reading.span.season == 2026 and reading.who.player is None
    asked, reading = _assigned(
        con,
        "most 3 pointers made in single game 24-25",
        PLAYER_RANKING,
        scope=Reading(measure=Measure(key="threePointFieldGoalsMade", as_typed="threePointFieldGoalsMade"), span=Span(season=2025, season_type=2)),
    )
    assert asked == GAME_HIGHS and reading.span.season == 2025 and reading.who.player is None
    asked, reading = _assigned(
        con, "single game with the most points this season", PLAYER_RANKING, scope=Reading(measure=Measure(key="points", as_typed="points"), span=Span(season=2026, season_type=2))
    )
    assert asked == GAME_HIGHS and reading.who.player is None
    asked, reading = _assigned(
        con,
        "jokic single-game high rebounds",
        PLAYER_LOG,
        scope=Reading(who=Who(kind="player", players=("Nikola Jokic",)), measure=Measure(key="rebounds", as_typed="rebounds"), span=Span(season_type=2)),
    )
    assert asked == GAME_HIGHS and reading.who.player == "Nikola Jokic" and _stat(reading) == "rebounds"
    asked, reading = _assigned(
        con, "celtics most points single game", TEAM_LINE, scope=Reading(who=Who(kind="team", teams=("Boston Celtics",)), measure=Measure(key="points", as_typed="points"), span=Span(season_type=2))
    )
    # The team's own best game: its games ranked by the figure (ISSUES.md
    # #364), not the team's season line.
    assert asked == TEAM_GAME_HIGHS and reading.who.team == "Boston Celtics" and reading.refused is None
    # A plural is games, not one game.
    asked, _ = _assigned(con, "jokic single games stats", PLAYER_LOG, scope=Reading(who=Who(kind="player", players=("Nikola Jokic",)), span=Span(season_type=2)))
    assert asked == PLAYER_LOG


def test_a_history_over_several_seasons_is_assigned_with_the_seasons_count(con: duckdb.DuckDBPyConnection) -> None:
    for parent in (PLAYER_LINE, PLAYER_LOG, None):
        asked, reading = _assigned(
            con,
            "Klay Thompson's 3pt percentage over the past 4 seasons",
            parent,
            scope=Reading(who=Who(kind="player", players=("Klay Thompson",)), measure=Measure(key="threePointFieldGoalPct", as_typed="threePointFieldGoalPct"), span=Span(season_type=2)),
        )
        assert asked == SEASON_HISTORY, parent
        # The window's count is of seasons for a history; a game log's own
        # `since` for the same words does not survive into a reader that refuses it.
        assert (reading.window.count, reading.window.of, reading.span.since, reading.span.season) == (4, "seasons", None, None), reading.stated()
    asked, reading = _assigned(
        con,
        "show me lebron's 2pt percentage for the past 10 years",
        PLAYER_LINE,
        scope=Reading(who=Who(kind="player", players=("LeBron James",)), measure=Measure(key="fieldGoalPct", as_typed="fieldGoalPct"), span=Span(season_type=2)),
    )
    assert asked == SEASON_HISTORY and reading.window.count == 10 and _stat(reading) == "twoPointFieldGoalPct"
    asked, reading = _assigned(
        con,
        "Show me luka's avg assists in each year since he joined the league",
        PLAYER_LINE,
        scope=Reading(who=Who(kind="player", players=("Luka Doncic",)), measure=Measure(key="assists", as_typed="assists"), span=Span(season_type=2)),
    )
    assert asked == SEASON_HISTORY and reading.span.career
    # A count over the past two seasons is the count, not a history.
    asked, reading = _assigned(
        con,
        "how many 20+ point games did SGA have in the past two seasons?",
        PLAYER_LOG,
        scope=Reading(who=Who(kind="player", players=("Shai Gilgeous-Alexander",)), measure=Measure(key="points", as_typed="points"), span=Span(season_type=2)),
    )
    assert asked == GAMES_COUNTED and _threshold(reading) == 20 and reading.span.since == current_season() - 1 and reading.window.count is None
    # A team ranking over ten years names no player to read a history of.
    asked, _ = _assigned(con, "which team won the championship for the past 10 years", TEAM_RANKING, scope=Reading(measure=Measure(key="record", as_typed="record")))
    assert asked == TEAM_RANKING


def test_shot_distance_is_assigned_for_a_player_and_not_for_the_league(con: duckdb.DuckDBPyConnection) -> None:
    asked, reading = _assigned(
        con,
        "How far away does Wembanyama shoot from?",
        PLAYER_LINE,
        scope=Reading(who=Who(kind="player", players=("Victor Wembanyama",)), measure=Measure(key="fieldGoalsMade", as_typed="fieldGoalsMade")),
    )
    assert asked == SHOT_DISTANCE and reading.who.player == "Victor Wembanyama"
    # Under a ranking of players no player is handed; the reading puts the
    # named one back for the shot distance, which cannot be answered without
    # him.
    asked, reading = _assigned(con, "what was steph curry's avg 3pt shot distance", PLAYER_RANKING, scope=Reading(measure=Measure(key="shot_distance", as_typed="shot_distance")))
    assert asked == SHOT_DISTANCE and reading.who.player == "Stephen Curry"
    asked, reading = _assigned(con, "who lead the league in avg 3 point distance", PLAYER_RANKING, scope=Reading(measure=Measure(key="shot_distance", as_typed="shot_distance")))
    assert asked == PLAYER_RANKING and reading.who.player is None


def test_a_teams_record_when_a_player_reached_a_threshold_is_assigned(con: duckdb.DuckDBPyConnection) -> None:
    for parent in (TEAM_RECORD, None, PLAYER_LOG):
        asked, reading = _assigned(
            con,
            "what was the sixers record when maxey scored 15+ points?",
            parent,
            scope=Reading(who=Who(kind="team", teams=("Philadelphia 76ers",)), measure=Measure(key="points", as_typed="points")),
        )
        assert asked == LINE_RECORD, parent
        assert _threshold(reading) == 15 and _stat(reading) == "points" and "maxey" in (reading.who.player or "").lower() and reading.who.team == "Philadelphia 76ers", reading.stated()
    # No player at all from the model, and no "X scored" grammar: the
    # reading's player, since a record over a line cannot be answered
    # without one.
    asked, reading = _assigned(con, "Sga record 36 plus points", TEAM_RECORD, scope=Reading(measure=Measure(key="points", as_typed="points"), span=Span(season_type=2)))
    assert asked == LINE_RECORD and _threshold(reading) == 36 and reading.who.player == "Shai Gilgeous-Alexander"
    # A player's games won: his team's record in the games he played, with
    # no threshold - and a TEAM's games won stay the team's own record.
    for parent in (TEAM_RECORD, PLAYER_LOG, PLAYER_LINE):
        asked, reading = _assigned(
            con,
            "how many playoff games has embiid won?",
            parent,
            scope=Reading(who=Who(kind="team", teams=("Philadelphia 76ers",)), measure=Measure(key="wins", as_typed="wins"), span=Span(season_type=3)),
        )
        assert asked == LINE_RECORD and reading.who.player == "Joel Embiid" and reading.span.season_type == 3 and _threshold(reading) is None, (parent, reading.stated())
    # ... and read by the parser itself, with no team handed: the subject is
    # Joel Embiid, never a "team" named after him.
    reading = _parsed(con, "how many playoff games has embiid won?", ["embiid"])
    assert reading.asked == LINE_RECORD and reading.who.player == "Joel Embiid" and not reading.who.team and reading.measure is not None and reading.measure.as_typed == "wins"
    asked, _ = _assigned(
        con,
        "how many games have the celtics won this season",
        TEAM_RECORD,
        scope=Reading(who=Who(kind="team", teams=("Boston Celtics",)), measure=Measure(key="wins", as_typed="wins"), span=Span(season=2026, season_type=2)),
    )
    assert asked == TEAM_RECORD


def test_a_streak_is_assigned_with_its_kind(con: duckdb.DuckDBPyConnection) -> None:
    asked, reading = _assigned(
        con, "what was the sixers longest winstreak this year?", TEAM_RECORD, scope=Reading(who=Who(kind="team", teams=("Philadelphia 76ers",)), span=Span(season=2026, season_type=2))
    )
    assert asked == LINE_RUNS and reading.measure is not None and reading.measure.won is True and reading.who.team == "Philadelphia 76ers"
    asked, reading = _assigned(
        con, "Longest losing streak in the NBA this season", TEAM_RANKING, scope=Reading(measure=Measure(key="record", as_typed="record"), span=Span(season=2026, season_type=2))
    )
    assert asked == LINE_RUNS and reading.measure is not None and reading.measure.won is False


def test_a_players_splits_are_assigned_with_the_split(con: duckdb.DuckDBPyConnection) -> None:
    asked, reading = _assigned(
        con, "Nikola Jokic home and away splits", PLAYER_LINE, scope=Reading(who=Who(kind="player", players=("Nikola Jokic",)), measure=Measure(key="points", as_typed="points"))
    )
    assert asked == PLAYER_SPLITS and reading.split == "home_away" and reading.cuts.venue is None
    asked, reading = _assigned(
        con, "Giannis Antetokounmpo stats by month", PLAYER_LINE, scope=Reading(who=Who(kind="player", players=("Giannis Antetokounmpo",)), measure=Measure(key="points", as_typed="points"))
    )
    assert asked == PLAYER_SPLITS and reading.split == "month"
    # A team's record by month names no player to split.
    asked, _ = _assigned(con, "knicks record by month", TEAM_RECORD, scope=Reading(who=Who(kind="team", teams=("New York Knicks",))))
    assert asked == TEAM_RECORD


def test_a_child_key_handed_as_the_parent_stands(con: duckdb.DuckDBPyConnection) -> None:
    """A child key handed as the parent is never re-read: the children's
    grammar fires under a parent only, so a question read under a child
    stays there."""
    asked, reading = _assigned(
        con,
        "PODZIEMSKI career 30 point games in a single game",
        GAMES_COUNTED,
        scope=Reading(
            who=Who(kind="player", players=("PODZIEMSKI",)),
            lines=(Line(measure="points", value=-1, as_typed="-1 points", keyed=True),),
            measure=Measure(key="points", as_typed="points"),
            span=Span(season_type=2, career=True),
        ),
    )
    assert asked == GAMES_COUNTED and _threshold(reading) == 30  # the words' line; a handed -1 is dropped


def test_a_models_name_that_is_no_name_is_not_the_player(con: duckdb.DuckDBPyConnection) -> None:
    """The model can file the ranking's own words ("most", "most 30+ point
    games") as the player: they are filler, never the subject's player."""
    asked, reading = _assigned(
        con,
        "who had the most 30 pt games in 2024",
        PLAYER_RANKING,
        scope=Reading(who=Who(kind="player", players=("most",)), measure=Measure(key="points", as_typed="points"), span=Span(season=2024, season_type=2)),
    )
    assert asked == GAMES_COUNTED and reading.who.player is None and _threshold(reading) == 30
    asked, reading = _assigned(
        con,
        "Who had the most 30+ point games this season?",
        PLAYER_RANKING,
        scope=Reading(
            who=Who(kind="player", players=("most 30+ point games",)),
            measure=Measure(key="threePointFieldGoalsMade", as_typed="threePointFieldGoalsMade"),
            span=Span(season=2026, season_type=2),
            window=Window(count=1),
        ),
    )
    assert asked == GAMES_COUNTED and reading.who.player is None and _stat(reading) == "points" and _threshold(reading) == 30 and reading.window.count is None
    s = _read(
        con, "Most points in 15th season played", PLAYER_LINE, scope=Reading(who=Who(kind="player", players=("Most Player in 15th Season Played",)), measure=Measure(key="points", as_typed="points"))
    )
    assert s.kind == "everyone" and s.filler == ("Most Player in 15th Season Played",)
    # A real name ending in a rank word is still a name.
    assert _read(con, "travis best career stats", PLAYER_LINE, scope=Reading(who=Who(kind="player", players=("Travis Best",)))).filler == ()


def test_a_position_group_is_the_subject_and_never_a_player(con: duckdb.DuckDBPyConnection) -> None:
    """F056 ("highest 3 point percentage in a season. by a shooting guard
    with at least 400 attempts"): the position group is the subject's own
    field, and nothing writes its phrase as the player - the compiler reads
    the group off the subject."""
    question = "highest 3 point percentage in a season by a shooting guard with at least 400 attempts"
    asked, reading = _assigned(con, question, PLAYER_RANKING, scope=Reading(measure=Measure(key="threePointFieldGoalPct", as_typed="threePointFieldGoalPct"), span=Span(season_type=2)))
    assert asked == PLAYER_RANKING and reading.who.player is None
    s = read_subject(names_of(con), question, PLAYER_RANKING, Reading(measure=Measure(key="threePointFieldGoalPct", as_typed="threePointFieldGoalPct")))
    assert s.kind == "position" and s.position == "SG"
    # A team-only key reads no player, so nothing is written there.
    _, reading = _assigned(con, "which team has the best centers", TEAM_RANKING, scope=Reading(measure=Measure(key="record", as_typed="record"), span=Span(season_type=2)))
    assert reading.who.player is None


# ---------------------------------------------------------------------------
# A companion carries the role the question gives him (Companion): played,
# absent, started, bench, or reached a line.


def test_a_companions_role_is_read_off_its_phrase(con: duckdb.DuckDBPyConnection) -> None:

    s = _read(
        con,
        "76ers record when maxey scores 20+ points",
        LINE_RECORD,
        scope=Reading(
            who=Who(kind="team", teams=("Philadelphia 76ers",)), lines=(Line(measure="points", value=20, as_typed="20 points", keyed=True),), measure=Measure(key="points", as_typed="points")
        ),
    )
    assert (
        s.kind == "team"
        and [(c.player, c.predicate, c.line) for c in s.conditions] == [("Tyrese Maxey", "reached", Line(measure="points", value=20, as_typed="20+ points"))]
        and s.companions == ("Tyrese Maxey",)
    )
    s = _read(
        con,
        "PHI record when Embiid and Paul George start",
        PRESENCE_SPLIT,
        scope=Reading(who=Who(kind="team", teams=("Philadelphia 76ers",)), companions=(Companion(player="Embiid"), Companion(player="Paul George"))),
    )
    assert [(c.player, c.predicate) for c in s.conditions] == [("Joel Embiid", "started"), ("Paul George", "started")]
    s = _read(
        con,
        "de'aaron fox game log without wembyanama",
        PLAYER_LOG,
        scope=Reading(who=Who(kind="pair", players=("De'Aaron Fox", "Victor Wembanyama")), companions=(Companion(player="wembyanama", predicate="absent"),)),
    )
    assert [(c.player, c.predicate) for c in s.conditions] == [("Victor Wembanyama", "absent")] and s.players == ("De'Aaron Fox",)
    s = _read(con, "celtics record with tatum out", PRESENCE_SPLIT, scope=Reading(who=Who(kind="team", teams=("Boston Celtics",)), companions=(Companion(player="tatum"),)))
    assert [(c.player, c.predicate) for c in s.conditions] == [("Jayson Tatum", "absent")]
    s = _read(con, "jaylen brown stats with tatum off the bench", PLAYER_LINE, scope=Reading(who=Who(kind="player", players=("Jaylen Brown",)), companions=(Companion(player="tatum"),)))
    assert [(c.player, c.predicate) for c in s.conditions] == [("Jayson Tatum", "bench")]
    s = _read(
        con,
        "hornets record when lebron and kawhi play this year",
        PRESENCE_SPLIT,
        scope=Reading(who=Who(kind="team", teams=("Charlotte Hornets",)), companions=(Companion(player="lebron"), Companion(player="kawhi"))),
    )
    assert [(c.player, c.predicate) for c in s.conditions] == [("LeBron James", "played"), ("Kawhi Leonard", "played")]


def test_a_start_or_a_line_is_written_as_the_companions_role(con: duckdb.DuckDBPyConnection) -> None:
    asked, reading = _assigned(con, "jaylen brown game log with tatum starting", PLAYER_LOG, scope=Reading(who=Who(kind="player", players=("Jaylen Brown",)), companions=(Companion(player="tatum"),)))
    assert asked == PLAYER_LOG and reading.companions == (Companion(player="Jayson Tatum", predicate="started"),)
    # "in games X did something" is a companion phrase too, the role after the name.
    reached = [("Jayson Tatum", "own", "reached", "points", 30)]
    asked, reading = _assigned(
        con, "jaylen brown stats in games tatum scored 30+ points", PLAYER_LINE, scope=Reading(who=Who(kind="player", players=("Jaylen Brown",)), measure=Measure(key="points", as_typed="points"))
    )
    assert [(c.player, c.side, c.predicate, c.line.measure if c.line is not None else None, c.line.value if c.line is not None else None) for c in reading.companions] == reached
    asked, reading = _assigned(
        con, "jaylen brown ppg when tatum scores 30+ points", PLAYER_LINE, scope=Reading(who=Who(kind="player", players=("Jaylen Brown",)), measure=Measure(key="points", as_typed="points"))
    )
    assert [(c.player, c.side, c.predicate, c.line.measure if c.line is not None else None, c.line.value if c.line is not None else None) for c in reading.companions] == reached
    # An absence is the one reading's companion, resolved; the split reads it as its two sides.
    asked, reading = _assigned(
        con, "celtics record without tatum", PRESENCE_SPLIT, scope=Reading(who=Who(kind="team", teams=("Boston Celtics",)), companions=(Companion(player="tatum", predicate="absent"),))
    )
    assert reading.companions == (Companion(player="Jayson Tatum", predicate="absent"),)
    # The presence split reads a start as its own side: a start is the condition, not a presence.
    asked, reading = _assigned(con, "celtics record when tatum starts", PRESENCE_SPLIT, scope=Reading(who=Who(kind="team", teams=("Boston Celtics",)), companions=(Companion(player="tatum"),)))
    assert reading.companions == (Companion(player="Jayson Tatum", predicate="started"),)


def test_a_team_with_a_companions_line_is_a_record_over_his_line(con: duckdb.DuckDBPyConnection) -> None:
    """yardstick-v2 F087, "show me splits for the sixers when maxey scores 20+
    points": the 76ers' record in the games Maxey reached 20 - a record over
    a line with Maxey as its player, whatever the model handed (the player's
    own splits; a line for an invented Joel Embiid)."""
    asked, reading = _assigned(
        con, "show me splits for the sixers when maxey scores 20+ points", PLAYER_SPLITS, scope=Reading(who=Who(kind="player", players=("Maxey",)), measure=Measure(key="points", as_typed="points"))
    )
    assert asked == LINE_RECORD and "maxey" in (reading.who.player or "").lower() and reading.who.team == "Philadelphia 76ers" and (_stat(reading), _threshold(reading)) == ("points", 20)
    assert reading.span.season is None and reading.span.season_type == 2  # no season named: the span tagger's reading, carried onto the rewritten scope
    asked, reading = _assigned(
        con,
        "show me splits for the sixers when maxey scores 20+ points",
        LINE_RECORD,
        scope=Reading(
            who=Who(kind="player", players=("Joel Embiid",)),
            cuts=Cuts(opponent="Philadelphia 76ers"),
            lines=(Line(measure="points", value=20, as_typed="20 points", keyed=True),),
            measure=Measure(key="points", as_typed="points"),
            span=Span(season=2026, season_type=2),
        ),
    )
    assert asked == LINE_RECORD and "maxey" in (reading.who.player or "").lower() and reading.who.team == "Philadelphia 76ers" and reading.cuts.opponent is None
    # A player subject keeps his own question: his splits, the condition beside him.
    asked, reading = _assigned(
        con, "jaylen brown splits when tatum scores 30+ points", PLAYER_SPLITS, scope=Reading(who=Who(kind="player", players=("Jaylen Brown",)), measure=Measure(key="points", as_typed="points"))
    )
    assert asked == PLAYER_SPLITS and reading.who.player == "Jaylen Brown"


def test_a_companion_the_model_named_nobody_for_is_read_from_the_question(con: duckdb.DuckDBPyConnection) -> None:
    """F087 handed with an invented Joel Embiid and no Maxey among the
    model's names: the companion phrase's own word names him, in the
    question's spelling, and the settled record over a line takes him as the
    spare name that replaces the invention. An ordinary word ("brown"), a stat's ("fga") and a number
    name nobody; a two-word name is tried as the pair first."""
    from association.query.subject import Companion, apply_subject

    # A namesake each: "maxey" and "jalen williams" are whole words of two
    # players' names, so players_named_in names nobody and only the
    # companion phrase's own word reaches them.
    con.executemany("INSERT INTO players VALUES (?, ?)", [("28", "Marlon Maxey"), ("29", "Jalen Williams"), ("30", "Jalen Williams")])
    scope = Reading(who=Who(kind="player", players=("Joel Embiid",)), cuts=Cuts(opponent="Philadelphia 76ers"), measure=measure_of("points"), span=Span(season=2026, season_type=2))
    s = _settled(con, "show me splits for the sixers when maxey scores 20+ points", PLAYER_LINE, scope=scope)
    assert (
        s.kind == "team"
        and s.teams == ("Philadelphia 76ers",)
        and [(c.player, c.predicate, c.line) for c in s.conditions] == [("maxey", "reached", Line(measure="points", value=20, as_typed="20+ points"))]
        and s.asked == LINE_RECORD
    )
    assert "companions the model named nobody for ['maxey']" in s.evidence
    applied = apply_subject(s, scope, asked=PLAYER_LINE)
    assert applied.asked == LINE_RECORD and applied.dropped == [] and (applied.scope.who.player, applied.scope.who.team, threshold_of(applied.scope)) == ("maxey", "Philadelphia 76ers", 20)
    s = _read(con, "thunder record with jalen williams out", TEAM_RECORD, scope=Reading(who=Who(kind="team", teams=("Oklahoma City Thunder",))))
    assert s.conditions == (Companion(player="jalen williams", predicate="absent"),)
    for question in (
        "mikal bridges game log with less than 15 fga",
        "celtics record with 3 starters out",
        "celtics record with best shooting",
    ):  # Travis Best is in the fixture: the leading word of something else is no name
        s = _read(con, question, TEAM_RECORD, scope=Reading(who=Who(kind="team", teams=("Boston Celtics",))))
        assert s.conditions == (), (question, s.conditions)
    # An ordinary word in a name's place - before an absence word - IS the
    # name, as typed: ten players are Brown, and the reader asks which.
    s = _read(con, "celtics record with brown out", TEAM_RECORD, scope=Reading(who=Who(kind="team", teams=("Boston Celtics",))))
    assert s.conditions == (Companion(player="brown", predicate="absent"),)


def test_a_possessive_team_word_names_the_team(con: duckdb.DuckDBPyConnection) -> None:
    """ISSUES.md #232: "the Sixers' record" and "the Knicks' previous 5 games"
    read the team the way the bare word does."""
    s = _read(con, "the Sixers' record this season", TEAM_RECORD)
    assert s.kind == "team" and s.teams == ("Philadelphia 76ers",)
    s = _read(con, "the Hawks' previous 5 games", PLAYER_LOG)
    assert s.kind == "team" and s.teams == ("Atlanta Hawks",)
    s = _read(con, "the Celtics's record", TEAM_RECORD)
    assert s.kind == "team" and s.teams == ("Boston Celtics",)


def test_a_player_after_a_versus_word_is_an_opponent_side_condition_where_the_words_ask_for_games(con: duckdb.DuckDBPyConnection) -> None:
    """A player after "vs" is on the other side of the subject's games - a condition, read wherever the
    words ask for a high, a count, a record, a log or a split - and never a
    second subject there. A bare "curry vs lebron" or "curry stats vs
    lebron" stays the pair, whose matchup summary reads both lines. Three
    names are a subject and conditions whatever the words; a team after
    "vs" names nobody here."""
    from association.query.subject import Companion

    s = _read(
        con, "most points by stephen curry vs lebron", PLAYER_LINE, scope=Reading(who=Who(kind="pair", players=("Stephen Curry", "LeBron James")), measure=Measure(key="points", as_typed="points"))
    )
    assert s.kind == "player" and s.players == ("Stephen Curry",) and s.conditions == (Companion(player="LeBron James", predicate="played", side="opponent"),)
    s = _read(
        con,
        "how many times did lebron score 30 vs kawhi",
        PLAYER_LINE,
        scope=Reading(who=Who(kind="pair", players=("LeBron James", "Kawhi Leonard")), measure=Measure(key="points", as_typed="points")),
    )
    assert s.kind == "player" and s.players == ("LeBron James",) and s.conditions == (Companion(player="Kawhi Leonard", predicate="played", side="opponent"),)
    for question in ("stephen curry vs lebron", "stephen curry stats vs lebron", "stephen curry ppg against lebron this season"):
        s = _read(con, question, PLAYER_LINE, scope=Reading(who=Who(kind="pair", players=("Stephen Curry", "LeBron James"))))
        assert s.kind == "pair" and s.conditions == (), question
    s = _read(
        con,
        "kawhi points per game vs lebron and curry",
        PLAYER_LINE,
        scope=Reading(who=Who(kind="pair", players=("Kawhi Leonard", "LeBron James", "Stephen Curry")), measure=Measure(key="points", as_typed="points")),
    )
    assert s.kind == "player" and [(c.player, c.side) for c in s.conditions] == [("LeBron James", "opponent"), ("Stephen Curry", "opponent")]
    # Beside a teammate's absence: the two conditions, on their two sides.
    s = _read(
        con,
        "stephen curry game log vs lebron without durant",
        PLAYER_LOG,
        scope=Reading(who=Who(kind="pair", players=("Stephen Curry", "LeBron James")), companions=(Companion(player="durant", predicate="absent"),)),
    )
    assert s.kind == "player" and [(c.player, c.predicate, c.side) for c in s.conditions] == [("Kevin Durant", "absent", "own"), ("LeBron James", "played", "opponent")]
    # A team after "vs" is the opponent; a player named only after it has no subject before him.
    s = _read(con, "stephen curry game log vs the lakers", PLAYER_LOG, scope=Reading(who=Who(kind="player", players=("Stephen Curry",))))
    assert s.kind == "player" and s.conditions == ()


def test_compare_x_with_y_reads_a_pair_not_a_companion(con: duckdb.DuckDBPyConnection) -> None:
    """ISSUES.md #233: the "with" a compare verb owns joins the two subjects."""
    s = _read(con, "compare luka with sga", PLAYER_COMPARISON, scope=Reading(who=Who(kind="pair", players=("Luka Doncic", "Shai Gilgeous-Alexander"))))
    assert s.kind == "pair" and s.players == ("Luka Doncic", "Shai Gilgeous-Alexander") and s.companions == ()
    s = _read(con, "contrast tatum with jaylen brown this season", PLAYER_COMPARISON, scope=Reading(who=Who(kind="pair", players=("Jayson Tatum", "Jaylen Brown"))))
    assert s.kind == "pair" and s.companions == ()
    # A real companion after a compare verb, further along, is still one.
    s = _read(
        con,
        "compare luka and sga without kyrie",
        PLAYER_COMPARISON,
        scope=Reading(who=Who(kind="pair", players=("Luka Doncic", "Shai Gilgeous-Alexander")), companions=(Companion(player="Kyrie Irving", predicate="absent"),)),
    )
    assert s.kind == "pair" and s.players == ("Luka Doncic", "Shai Gilgeous-Alexander")


def test_the_paraphrases_wordings_of_the_children_are_assigned(con: duckdb.DuckDBPyConnection) -> None:
    """Held-out paraphrases' wordings of the children: "in one match", a
    hyphen in "30-point" and "36-plus", "at least 2", "from year to year" -
    each held at 0 false positives over the recorded corpus
    (``intent-shrink/port_check.py``)."""
    asked, _ = _assigned(
        con,
        "in this season's games, who recorded the highest number of assists in one match?",
        PLAYER_RANKING,
        scope=Reading(measure=Measure(key="assists", as_typed="assists"), span=Span(season=2026, season_type=2)),
    )
    assert asked == GAME_HIGHS
    asked, reading = _assigned(
        con, "who had the highest number of 30-point games in 2024", PLAYER_RANKING, scope=Reading(measure=Measure(key="points", as_typed="points"), span=Span(season=2024, season_type=2))
    )
    assert asked == GAMES_COUNTED and _threshold(reading) == 30
    asked, reading = _assigned(
        con,
        "games where Klay Thompson made at least 2 threes including playoffs",
        PLAYER_LOG,
        scope=Reading(who=Who(kind="player", players=("Klay Thompson",)), measure=Measure(key="threePointFieldGoalsMade", as_typed="threePointFieldGoalsMade"), span=Span(season_type=2)),
    )
    assert asked == GAMES_COUNTED and _threshold(reading) == 2
    asked, _ = _assigned(
        con,
        "Display Luka's average assists from year to year",
        PLAYER_LINE,
        scope=Reading(who=Who(kind="player", players=("Luka Doncic",)), measure=Measure(key="assists", as_typed="assists"), span=Span(season_type=2)),
    )
    assert asked == SEASON_HISTORY
    asked, reading = _assigned(con, "36-plus points SGA record", PLAYER_LINE, scope=Reading(measure=Measure(key="points", as_typed="points"), span=Span(season_type=2)))
    assert asked == LINE_RECORD and _threshold(reading) == 36 and reading.who.player == "Shai Gilgeous-Alexander"
    # "per game" is an average, never one game: the season ranking stands.
    asked, _ = _assigned(
        con, "which player has the highest points per game average in the league?", PLAYER_RANKING, scope=Reading(measure=Measure(key="points", as_typed="points"), span=Span(season_type=2))
    )
    assert asked == PLAYER_RANKING


def test_excluding_featuring_and_a_fronted_without_are_companion_phrases(con: duckdb.DuckDBPyConnection) -> None:
    """ "excluding" is "without" and "featuring" is "with", reworded; and a
    question word ends a companion phrase, so a fronted "Without Kevin
    Durant, what is Steph Curry's record" names Durant alone."""
    s = _read(con, "this season's luka game log excluding kawhi and lebron", PLAYER_LOG, scope=Reading(who=Who(kind="player", players=("Luka Doncic",))))
    assert s.kind == "player" and [(c.player, c.predicate) for c in s.conditions] == [("Kawhi Leonard", "absent"), ("LeBron James", "absent")]
    s = _read(con, "Without Kevin Durant, what is Steph Curry's record against Lebron in the regular season?", None, scope=Reading(who=Who(kind="pair", players=("steph curry", "lebron"))))
    assert s.kind == "pair" and [(c.player, c.predicate) for c in s.conditions] == [("Kevin Durant", "absent")]
    s = _read(con, "display the PHI record featuring Embiid and Maxey", PRESENCE_SPLIT, scope=Reading(who=Who(kind="team", teams=("Philadelphia 76ers",))))
    assert s.kind == "team" and [(c.player, c.predicate) for c in s.conditions] == [("Joel Embiid", "played"), ("Tyrese Maxey", "played")]


def test_the_one_player_named_in_games_he_played_is_the_subject(con: duckdb.DuckDBPyConnection) -> None:
    """ "2 threes in games Jamal Murray played" names nobody beside him: the
    games he played are his own, so he is the subject, not a companion of
    nobody (which would leave the question no subject, a league ranking). A
    second name keeps the companion reading."""
    alone = read_subject(names_of(con), "2 threes in games Stephen Curry played including playoffs", None, Reading(who=Who(kind="player", players=("Stephen Curry",))))
    assert (alone.kind, alone.players, alone.conditions) == ("player", ("Stephen Curry",), ())
    beside = read_subject(names_of(con), "maxey points in games embiid played", None, Reading(who=Who(kind="pair", players=("Tyrese Maxey", "Joel Embiid"))))
    assert beside.players == ("Tyrese Maxey",) and [(c.player, c.predicate) for c in beside.conditions] == [("Joel Embiid", "played")]
    # Two companions of a team, the team handed but not a word the reading
    # finds ("76ers"), stay the team's question.
    team = read_subject(names_of(con), "show me the 76ers record when both Embiid and Paul George played", None, Reading(who=Who(kind="pair", players=("Embiid", "Paul George"), teams=("76ers",))))
    assert team.players == () and {c.player for c in team.conditions} >= {"Joel Embiid"}


def test_the_subject_is_read_once_per_question(con: duckdb.DuckDBPyConnection, monkeypatch: pytest.MonkeyPatch) -> None:
    """The parser reads who the question is about once (``read_route``),
    and its child step and last step settle that reading
    (``settle_subject``) rather than reading it again."""
    import association.query.parse as parse

    seen: list[Any] = []
    original = parse.read_subject

    def counting(index: NameIndex, question: str, shape: Any, scope: Filtered) -> Subject:
        seen.append(shape)
        return original(index, question, shape, scope)

    monkeypatch.setattr(parse, "read_subject", counting)
    question = "How many 30+ point games did Jokic have this season?"
    route = parse.read_route(names_of(con), question, ["Jokic"], "points")
    reading = route
    assert reading.subject is not None and reading.subject.players == ("Nikola Jokic",)
    assert seen == [None]  # read under no shape: the grammar has not named one yet
    assert reading.asked == GAMES_COUNTED and reading.subject.asked == GAMES_COUNTED


def test_a_subject_is_settled_under_the_key_without_reading_a_name_again() -> None:
    """What depends on the key is written from the reading already made,
    with no rule run: two teams meeting are the ``teams`` kind under their
    meetings (``TEAM_MEETINGS``) and a team against another under anything
    else; the words that name a child (``child_named``, the grammar's
    decision) are said once, whichever key the subject was read under."""
    team = Subject("team", teams=("Philadelphia 76ers",), opponent="Boston Celtics", evidence=("team word '76ers'", "the words 'old' name streak"))
    meeting = settle_subject(team, TEAM_MEETINGS)
    assert (meeting.kind, meeting.teams, meeting.opponent) == ("teams", ("Philadelphia 76ers", "Boston Celtics"), None)
    against = settle_subject(team, TEAM_PERIOD_TOTAL)
    assert (against.kind, against.teams, against.opponent, against.asked) == ("team", ("Philadelphia 76ers",), "Boston Celtics", TEAM_PERIOD_TOTAL)
    assert against.evidence == ("team word '76ers'",)

    player = Subject("player", players=("Nikola Jokic",))
    question = "How many 30+ point games did Jokic have this season?"
    assert child_named(player, PLAYER_LOG, question) == (GAMES_COUNTED, "How many 30+ point games")
    assert child_named(player, GAMES_COUNTED, question) is None  # a child already chosen stands
    counted = settle_subject(player, GAMES_COUNTED, parent=PLAYER_LOG, words="How many 30+ point games")
    assert (
        counted.asked == GAMES_COUNTED
        and counted.asked_reason == "the words 'How many 30+ point games' name player_games/scalar/count"
        and counted.evidence[-1].endswith("name player_games/scalar/count")
    )


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
    """Every name a phrase holds, not the first: "Celtics record without
    Tatum and Brown" is the games both missed, not the games Tatum missed -
    a different question, answered fluently."""
    from association.query.subject import _name_segments

    assert _name_segments(text) == names


def test_who_sat_out_and_who_played_is_read_from_the_question_without_the_model(con: duckdb.DuckDBPyConnection) -> None:
    """The one reader of a companion's name: by position after "without"
    (as typed where nobody is known by it - an ordinary word two players
    share, a name nobody resolves), as the player the question is known to
    hold where one is, and for "with and without X" from its "without"."""
    from association.query.subject import Companion, _conditions

    def beside(found: tuple[Companion, ...]) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """Who played beside the subject and who sat out, by name - what the taggers are handed."""
        return tuple(c.player for c in found if c.predicate == "played" and c.side == "own"), tuple(c.player for c in found if c.absent)

    absent = _conditions("Celtics record without Tatum, Brown and Holiday", ("Jayson Tatum",), Reading())
    assert [(c.player, c.predicate) for c in absent] == [("Jayson Tatum", "absent"), ("Brown", "absent"), ("Holiday", "absent")]
    assert beside(absent) == ((), ("Jayson Tatum", "Brown", "Holiday"))
    assert _conditions("Podziemski game log without zzyzx", ("Brandin Podziemski",), Reading()) == (Companion(player="zzyzx", predicate="absent"),)
    assert _conditions("most games without a turnover", (), Reading()) == ()
    split = _conditions("Celtics record with and without Tatum", ("Jayson Tatum",), Reading())
    assert [(c.player, c.predicate) for c in split] == [("Jayson Tatum", "absent")]
    # Who PLAYED has to be somebody the reading knows: the words after
    # "with" are often no name at all.
    played = _conditions("jjj stats with ja morant and desmond bane last season", ("Jaren Jackson Jr.", "ja morant", "desmond bane"), Reading())
    assert beside(played)[0] == ("ja morant", "desmond bane")
    assert _conditions("mikal bridges game log with less than 15 fga", ("Mikal Bridges",), Reading()) == ()
    for question in (
        "show me the 76ers record when both Embiid and Paul George played",
        "show me PHI record with Embiid and Paul George",
        "PHI record when Embiid and Paul George play",
        "PHI record when Embiid with Paul George",
        "When Embiid plays with Paul George, what is the PHI record?",
        "PHI's record when Embiid and Paul George are both in the game",
    ):
        assert beside(_conditions(question, ("Joel Embiid", "Paul George"), Reading()))[0] == ("Joel Embiid", "Paul George"), question
    # The whole reading, with the model naming nobody: the names are the question's.
    s = _read(con, "76ers record without embiid", TEAM_RECORD, scope=Reading(who=Who(kind="team", teams=("Philadelphia 76ers",))))
    assert s.kind == "team" and [(c.player, c.predicate) for c in s.conditions] == [("Joel Embiid", "absent")]


@pytest.mark.parametrize(
    ("question", "players", "roles"),
    [
        # A denial after the second name, its verb elided (ISSUES.md #479).
        (
            "Lakers record when AD plays and lebron doesnt",  # codespell:ignore doesnt - the feed's question as typed
            ("Anthony Davis", "LeBron James"),
            [("Anthony Davis", "played"), ("LeBron James", "absent")],
        ),
        (
            "boston celtics record in games in which jaylen brown played and jayson tatum did not",
            ("Jaylen Brown", "Jayson Tatum"),
            [("Jaylen Brown", "played"), ("Jayson Tatum", "absent")],
        ),
        ("lakers record when AD plays and lebron isn't playing", ("Anthony Davis", "LeBron James"), [("Anthony Davis", "played"), ("LeBron James", "absent")]),
        # A companion keyword inside another's phrase, either way round.
        ("lakers record when AD plays without lebron", ("Anthony Davis", "LeBron James"), [("Anthony Davis", "played"), ("LeBron James", "absent")]),
        ("lakers record without lebron when AD plays", ("Anthony Davis", "LeBron James"), [("LeBron James", "absent"), ("Anthony Davis", "played")]),
        # A bare name before a companion keyword keeps its own keyword's role.
        (
            "boston celtics record in games with jaylen brown and without jayson tatum in the 2025/26 regular season",
            ("Jaylen Brown", "Jayson Tatum"),
            [("Jaylen Brown", "played"), ("Jayson Tatum", "absent")],
        ),
        # One role for every name where the names share it.
        ("PHI record when Embiid and Paul George play", ("Joel Embiid", "Paul George"), [("Joel Embiid", "played"), ("Paul George", "played")]),
        ("celtics record without Tatum and Brown", ("Jayson Tatum", "Jaylen Brown"), [("Jayson Tatum", "absent"), ("Jaylen Brown", "absent")]),
        (
            "lakers record when AD and lebron dont play",
            ("Anthony Davis", "LeBron James"),
            [("Anthony Davis", "absent"), ("LeBron James", "absent")],
        ),
    ],
)
def test_each_companion_takes_the_role_its_own_clause_states(question: str, players: tuple[str, ...], roles: list[tuple[str, str]]) -> None:
    """A phrase naming two companions may state a role for each: "when AD
    plays and lebron doesn't" is Davis on and LeBron off, where one role for
    the phrase read both as playing and the split answered the games both
    played - a different question, fluently (ISSUES.md #479; the feed
    sample's FS339). A name with no role words of its own shares the role
    after it."""
    from association.query.subject import _conditions

    assert [(c.player, c.predicate) for c in _conditions(question, players, Reading())] == roles


def test_a_misspelled_companion_is_read_as_typed_where_it_is_one_players_near_spelling(con: duckdb.DuckDBPyConnection) -> None:
    """ "without wembyanama" matches no player as spelled. The words after
    "without" are a name's place, so the near-spelling pass applies as it
    does to a model's name - one candidate, no ordinary word - and the span stays as typed
    for the entity index to resolve and say so. The one player in a "games
    X played" phrase whom the reading made the subject is never his own
    companion."""
    s = _read(con, "de'aaron fox last five games without wembyanama", PLAYER_LOG, scope=Reading(who=Who(kind="player", players=("De'Aaron Fox",))))
    assert s.players == ("De'Aaron Fox",) and [(c.player, c.predicate) for c in s.conditions] == [("wembyanama", "absent")]
    # And where he PLAYED, a role that takes only a name the reading found.
    s = _read(con, "de'aaron fox points with wembyanama playing", PLAYER_LINE, scope=Reading(who=Who(kind="player", players=("De'Aaron Fox",))))
    assert [(c.player, c.predicate) for c in s.conditions] == [("wembyanama", "played")]
    assert "companions the model named nobody for ['wembyanama']" in s.evidence
    # Two near spellings name nobody: the question is asked, not guessed.
    # (A table is read once per block: the new row is seen in the next one.)
    con.execute("INSERT INTO players VALUES ('31', 'Vince Wembyanamo')")
    with names.loaded():
        assert _read(con, "de'aaron fox points with wembyanama playing", PLAYER_LINE, scope=Reading(who=Who(kind="player", players=("De'Aaron Fox",)))).conditions == ()
        # An ordinary word is no typo, however near a name it is: "brow" is one
        # edit from Jaylen Brown, as "season" is from Tari Eason.
        assert _read(con, "de'aaron fox points with brow", PLAYER_LINE, scope=Reading(who=Who(kind="player", players=("De'Aaron Fox",)))).conditions == ()


def test_a_spelling_one_name_took_from_the_next_names_words_is_not_its_own(con: duckdb.DuckDBPyConnection) -> None:
    """ "compare sga embiid": the window anchored at "sga" reaches "embiid"
    and reads Joel Embiid, the player "embiid" itself spells as - which
    would spell "sga" as him and put the pair in the other order ("Joel
    Embiid vs Shai Gilgeous-Alexander"); "Wemby" beside "Jokic" in "Who
    scores more: Wemby, Jokic, or Luka?" the same way. Where two names spell
    as one player, the one whose own word is in his name keeps it."""
    from association.query.subject import _spellings_owned

    assert _spellings_owned({"sga": "Joel Embiid", "embiid": "Joel Embiid"}) == {"embiid": "Joel Embiid"}
    assert _spellings_owned({"Wemby": "Nikola Jokic", "Jokic": "Nikola Jokic"}) == {"Jokic": "Nikola Jokic"}
    assert _spellings_owned({"Steph Curry": "Stephen Curry", "embiid": "Joel Embiid"}) == {"Steph Curry": "Stephen Curry", "embiid": "Joel Embiid"}
    # Neither name's word in the player's: nothing to tell them apart by, and both stand as they were.
    assert _spellings_owned({"seph": "Stephen Curry", "steph": "Stephen Curry"}) == {"seph": "Stephen Curry", "steph": "Stephen Curry"}
    # Through the reading: the pair in the question's order, "sga" spelled as nobody else.
    read = _read(con, "compare sga embiid", PLAYER_COMPARISON, scope=Reading(who=Who(kind="pair", players=("sga", "embiid"))))
    assert read.players[1] == "Joel Embiid" and read.players[0] != "Joel Embiid"
    assert not any("spells 'sga' as 'Joel Embiid'" in line for line in read.evidence)


def test_a_count_is_named_from_a_line_the_tagger_read_never_a_bare_number() -> None:
    """The count's child grammar reads the line the lines tagger typed,
    marked where its words stand (``LinesNamed.marked``): "games with 15
    points" names a count, and "games with more than 40 field goal
    percentage" - a number no line is read from - names none, so the number
    stays unread rather than claimed by a count the rules then decline
    (ISSUES.md, the percentage line). The words a child is named by are the
    question's own."""
    from association.query.line import lines_named

    player = Subject("player", players=("Stephen Curry",))
    question = "steph curry games with 15 points"
    assert child_named(player, PLAYER_LOG, lines_named(question).marked(question), original=question) == (GAMES_COUNTED, "games with 15 points")
    question = "kobe bryant career games with more than 40 field goal percentage"
    assert child_named(player, PLAYER_LOG, lines_named(question).marked(question), original=question) is None
