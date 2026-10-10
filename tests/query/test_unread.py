"""The words a reading did not read (``Reading.unread``, Phase 3, step 3): the
content words of the question no reader rule claimed, counted by the rule
the claims ledger counts by (``lexicon.content_words``), so
``scripts/claims_ledger.py`` - which deletes each word in turn and asks
whether the reading moved - checks the Reading's own list from outside."""

from __future__ import annotations

import duckdb
import pytest

from association.query.lexicon import content_tokens, content_words, read_by_the_model, unread_words, without_possessive
from association.query.parse import read_route, reading_from_route
from association.query.reading import Reading


@pytest.fixture
def con() -> duckdb.DuckDBPyConnection:
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    c.executemany("INSERT INTO players VALUES (?, ?)", [("1", "Joel Embiid"), ("2", "Nikola Jokic"), ("4", "Tyrese Maxey"), ("6", "Stephen Curry")])
    c.execute("CREATE TABLE teams (team_id VARCHAR, display_name VARCHAR, abbreviation VARCHAR)")
    c.executemany("INSERT INTO teams VALUES (?, ?, ?)", [("1", "Boston Celtics", "BOS"), ("2", "Philadelphia 76ers", "PHI")])
    return c


def _read(con: duckdb.DuckDBPyConnection, question: str, names: list[str] | None = None, stat: str = "") -> Reading:
    """The parser's whole path, as the agent takes it."""
    route, _, _ = read_route(con, question, names, stat)
    return reading_from_route(con, question, route)


def test_a_word_is_the_text_between_spaces_lowercased_without_its_edge_punctuation() -> None:
    question = 'How many 30+ points did "Curry\'s" team score, this season?'
    assert [word for _, _, word in content_tokens(question)] == ["how", "many", "30+", "points", "did", "curry's", "team", "score", "this", "season"]
    # The positions are the whole word's, punctuation and all: what a claim is held against.
    assert [question[start:end] for start, end, _ in content_tokens(question)][5] == '"Curry\'s"'
    assert without_possessive("curry\u2019s") == "curry" and without_possessive("curry's") == "curry"


def test_a_content_word_is_no_function_word_and_nothing_the_model_read() -> None:
    question = "how many points does embiid's team average this season"
    assert [word for _, _, word in content_words(question, [], "")] == ["points", "embiid's", "team", "average", "season"]
    # A word of a name the model copied (possessive or not) and a word of the stat it picked are the model's.
    assert read_by_the_model("embiid's", ["Joel Embiid"], "") and read_by_the_model("points", [], "points") and read_by_the_model("ppg", [], "points")
    assert not read_by_the_model("average", ["Joel Embiid"], "points")
    assert [word for _, _, word in content_words(question, ["Joel Embiid"], "points")] == ["team", "average", "season"]


def test_a_question_is_cut_into_words_without_their_punctuation() -> None:
    assert [word for _, _, word in content_tokens("How far was Curry's average three-pointer, (2026)?")] == ["how", "far", "was", "curry's", "average", "three-pointer", "2026"]
    assert [word for _, _, word in content_tokens("20+ points, 45% from 3")] == ["20+", "points", "45%", "from", "3"]


def test_a_word_the_models_reply_accounts_for_is_not_the_readers_to_read() -> None:
    names, stat = ["steph curry", "the Celtics"], "threePointFieldGoalsMade"
    assert read_by_the_model("curry's", names, stat) and read_by_the_model("curry" + chr(0x2019) + "s", names, stat) and read_by_the_model("celtics", names, stat)
    assert read_by_the_model("threes", names, stat) and read_by_the_model("3s", names, stat)
    assert not read_by_the_model("rebounds", names, stat) and not read_by_the_model("playoffs", names, stat)
    # No stat picked: no stat word is the model's.
    assert not read_by_the_model("points", names, "")


def test_content_words_leave_out_function_words_and_the_models_words() -> None:
    words = [word for _, _, word in content_words("How many threes did Steph Curry's team make against the Celtics in the playoffs?", ["Steph Curry", "Celtics"], "threePointFieldGoalsMade")]
    # "make" is the stat's own word (three-pointers MADE), so the model's.
    assert words == ["team", "against", "playoffs"]


def test_the_unread_words_are_the_content_words_no_claim_touches() -> None:
    question = "how many points does embiid average"
    # A claim touching any character of a word reads it; one beside it does not.
    assert unread_words(question, [(9, 15)], [], "") == ("embiid", "average")
    assert unread_words(question, [(9, 15), (21, 27)], [], "") == ("average",)
    assert unread_words(question, [(9, 15), (21, 27), (27, 28)], [], "") == ("average",)
    assert unread_words(question, [(9, 15), (25, 30)], [], "") == ()
    # A zero-width claim touches nothing.
    assert unread_words(question, [(0, 0)], ["Joel Embiid"], "points") == ("average",)


def test_the_reading_states_the_words_nothing_claimed(con: duckdb.DuckDBPyConnection) -> None:
    reading = _read(con, "how many points does embiid average", names=["embiid"], stat="points")
    assert reading.unread == ("average",)
    # Read from the words alone, the subject reading claims the name and the measure tagger the stat.
    assert _read(con, "how many points does embiid average").unread == ("average",)


def test_a_word_the_model_copied_is_read_though_no_claim_holds_it(con: duckdb.DuckDBPyConnection) -> None:
    """The model's names and stat ride the route (``Route.model_names``,
    ``model_stat``): a word of them is the model's reading, as the ledger
    counts it, whatever a rule claimed."""
    route, _, _ = read_route(con, "how many points does embiid average", ["embiid"], "points")
    assert route.model_names == ("embiid",) and route.model_stat == "points"
    assert reading_from_route(con, "how many points does embiid average", route).unread == ("average",)


# ---------------- the claims cut to what each rule read (span.needed, span.read_by) ----------------


def test_a_claim_stops_short_of_a_word_its_rule_did_not_need() -> None:
    """A tagger's claim keeps the words whose deletion changes what the
    tagger read: ten games either way without "games" in "last 10 games";
    "per" reads the rate with "100" or "possessions" gone, so neither is
    claimed, and the claims ledger counts both unread."""
    from association.query.reading import Claim
    from association.query.span import needed
    from association.query.window import WindowContext, read_window

    question = "tatum last 10 games"
    context = WindowContext(intent="game_log")
    read = read_window(question, context)
    assert [question[c.start : c.end] for c in read.claims] == ["last 10 games"]
    assert [question[c.start : c.end] for c in needed(question, read.claims, lambda q: read_window(q, context).window)] == ["last 10"]
    # A word between two kept ones that the rule did not need splits the claim; a function word between them stays.
    assert needed("ab cd ef", [Claim(0, 8, "x")], lambda q: ("ab" in q.split(), "ef" in q.split())) == (Claim(0, 2, "x"), Claim(6, 8, "x"))
    assert needed("ab of ef", [Claim(0, 8, "x")], lambda q: ("ab" in q.split(), "ef" in q.split())) == (Claim(0, 8, "x"),)


def test_a_word_outside_every_claim_the_rule_turns_on_is_claimed_beside_them() -> None:
    """With ``outside``, the rule is asked about every other word too: the
    possessive that makes "curry's last regular season game" one game of
    his is read by the window, though its claim starts at "last"."""
    from association.query.span import needed
    from association.query.window import WindowContext, read_window

    question = "create a shot chart of steph curry's last regular season game"
    context = WindowContext(intent="shot_chart")
    read = read_window(question, context)
    trimmed = needed(question, read.claims, lambda q: read_window(q, context).window, outside="window")
    assert sorted(question[c.start : c.end] for c in trimmed) == ["curry's", "game", "last"]


def test_a_word_whose_presence_makes_the_rule_read_less_is_not_claimed() -> None:
    """``gives_up``: a word outside every claim whose deletion only ADDS to
    the rule's reading suppressed what it read, and the words it suppressed
    are unread - claiming it would hide them."""
    from association.query.span import needed

    def reads(q: str) -> tuple[str | None, ...]:
        years = [w for w in q.split() if w.isdigit()]
        return (years[0] if len(years) == 1 else None,)

    question = "2024 and 2025 record"
    assert needed(question, (), reads, outside="span") != ()
    assert needed(question, (), reads, outside="span", gives_up=True) == ()


def test_a_grammar_over_the_whole_question_claims_the_words_its_decision_turned_on() -> None:
    """``read_by``: a row written as lookaheads reads the words it wants and
    the words that rule it out anywhere in the question - "compare" keeps a
    pair off the matchup row, and is read."""
    from association.query.parse import parent_intent
    from association.query.span import read_by

    question = "compare curry and lebron vs the celtics"
    assert parent_intent(question, "pair") == "player_compare"
    assert [question[c.start : c.end] for c in read_by(question, "intent", lambda q: parent_intent(q, "pair"))] == ["compare"]
    question = "show sga fingerprint for this season"
    assert [question[c.start : c.end] for c in read_by(question, "intent", lambda q: parent_intent(q, "player"))] == ["fingerprint"]


def test_the_reading_claims_what_the_grammar_the_stages_and_the_point_read(con: duckdb.DuckDBPyConnection) -> None:
    """The words that name the intent and the point's word tables' words
    are claimed by the rule that reads them, so the Reading lists none of
    them unread: "fingerprint", "record", "log", "most"."""
    assert _read(con, "show me a fingerprint for Maxey for 2026", names=["Maxey"]).unread == ()
    assert _read(con, "luka ft log", names=["luka"], stat="freeThrowsMade").unread == ()
    reading = _read(con, "how many points does embiid average", names=["embiid"], stat="points")
    assert reading.unread == ("average",)
