"""The grammar and its rules, tested at the unit: every reachable key, the
sets and value tables held to the lexicon and the types, and the few
rule-level checks over a tagger. The question cases are
`tests/query/test_reader_cases.py`'s.
"""

from typing import Any, get_args, get_type_hints

import pytest

from association.query.compose.plan import refusal_result
from association.query.grammar import CODE_ASSIGNED_ASKS
from association.query.lexicon import ORDER_WORDS
from association.query.point import ORDER_ASKS
from association.query.reading import PLAYER_LINE, PLAYER_LOG, Cause, Filtered, PointShape, Window


def _scope_values(field: str, of: type = Filtered) -> set[object]:
    """The closed set of values the typed Scope's ``field`` holds (or the ``Window``'s)."""
    (literal,) = (arg for arg in get_args(get_type_hints(of)[field]) if arg is not type(None))
    return set(get_args(literal))


def test_every_asked_key_is_reachable_from_the_reader() -> None:
    """Every shape the words can ask must be REACHABLE, by one of exactly
    three routes: the parser's grammar names it as a parent
    (``parse.PARENT_GRAMMAR``), the grammar's rules assign it from the
    question text (``CODE_ASSIGNED_ASKS``), or the subject reading assigns it
    from the text gated on the subject's kind (``subject.KIND_ASSIGNED_ASKS``).
    A key in none of the lists is dead code that no question can ever reach.
    The keys are every one the reading module names (:func:`_asked_keys`)."""
    from association.query.parse import PARENT_GRAMMAR
    from association.query.subject import KIND_ASSIGNED_ASKS

    parents = {parent for _, _, parent in PARENT_GRAMMAR}
    assert parents | CODE_ASSIGNED_ASKS | KIND_ASSIGNED_ASKS == _asked_keys()


def _asked_keys() -> set[PointShape]:
    """Every key the words can ask, as the reading module names them
    (``reading.PLAYER_LOG``, ...): the grammar's 28, which
    ``test_frozen_shapes`` freezes."""
    from association.query import reading

    keys = {value for name, value in vars(reading).items() if name.isupper() and isinstance(value, PointShape)}
    assert len(keys) == 28, sorted(map(str, keys))  # the frozen keys (test_frozen_shapes)
    return keys


def test_the_side_values_match_the_scope() -> None:
    """The side words the measure tagger reads (the lexicon's ``SIDE_WORDS``)
    and the sides a Measure holds are one set: a side word the Measure
    refuses would leave the question unread, and one the Measure holds that
    no word names could never be asked."""
    from association.query.lexicon import SIDE_WORDS
    from association.query.reading import MeasureSide

    assert set(SIDE_WORDS) | {"total"} == set(get_args(MeasureSide))


def test_the_order_values_match_the_scope() -> None:
    """Same shape as the side check above: the lexicon's order words and the
    ends a Window holds are one set."""
    assert set(ORDER_WORDS) == _scope_values("order", Window)


def test_the_order_asks_are_the_keys_that_state_a_window() -> None:
    """The keys the point reader reads an end of the window under
    (``point.ORDER_ASKS``) are the keys whose cells state the window: a key
    stating the window and missing there drops the end the words asked, and
    one listed there whose cells do not state it is refused for the end."""
    from association.query.compose.plan import cells_stated
    from association.query.point import _WINDOW_ORDER_ON_A_SINGLE_GAME as _ORDER_ON_A_SINGLE_GAME

    # A player's line takes an end only beside a count of one (a single game,
    # read as a log), so the window reader sets the pair together for it
    # rather than the end alone - see _ORDER_ON_A_SINGLE_GAME. A key states
    # the window where the window cell (Window.CELLS) is in its stated cells.
    assert {shape for shape in _asked_keys() if "window" in cells_stated(shape)} == ORDER_ASKS | _ORDER_ON_A_SINGLE_GAME


def test_a_last_n_games_signal_requires_a_real_limit_and_the_recent_order() -> None:
    from association.query.point import span_under
    from association.query.span import SpanContext, read_span

    # No `order` at all: an ordinary log with a count, not "last N games" -
    # e.g. "top 5" is `order` absent, `limit` present.
    assert not span_under(PLAYER_LOG, read_span("Knicks top 5 wins this season", SpanContext(limit=5))).span.both
    # `order="first"` is "his first N games", the opposite end of the season -
    # a real question, but not the one this signal is for.
    assert not span_under(PLAYER_LOG, read_span("Knicks first 5 games", SpanContext(order="first", limit=5))).span.both
    # No real limit at all.
    assert not span_under(PLAYER_LOG, read_span("Knicks last games", SpanContext(order="recent"))).span.both
    # A key other than a player's log never sees it, whatever else is set.
    assert not span_under(PLAYER_LINE, read_span("Knicks last 5 games", SpanContext(order="recent", limit=5))).span.both
    assert span_under(PLAYER_LOG, read_span("Knicks last 5 games", SpanContext(order="recent", limit=5))).span.both


@pytest.mark.parametrize(
    ("question", "context"),
    [
        ("Knicks last 5 games", {"game_n": 4}),  # one game of a KNOWN playoff series
        ("Knicks last 5 games", {"date": "2026-04-12"}),  # one calendar day already finds its own game
    ],
)
def test_a_last_n_games_signal_defers_to_narrower_words_already_read(question: str, context: dict[str, Any]) -> None:
    """Each of these already fixes which games are meant more precisely than
    "last N" does, so none of them widen to both season types."""
    from association.query.point import span_under
    from association.query.span import SpanContext, read_span

    assert not span_under(PLAYER_LOG, read_span(question, SpanContext(order="recent", limit=5, **context))).span.both


@pytest.mark.parametrize("question", ["Knicks last 5 games of his career", "Knicks last 5 games since 2020"])
def test_a_last_n_games_signal_reads_both_types_over_a_career_or_a_range(question: str) -> None:
    """Decision D14: the last N games are the last N played, whatever span
    holds them - a career or a range of seasons widens to both season types
    too (the newest five games of a career hold its last playoff run)."""
    from association.query.point import span_under
    from association.query.span import SpanContext, read_span

    assert span_under(PLAYER_LOG, read_span(question, SpanContext(order="recent", limit=5))).span.both


def test_a_team_metric_given_up_is_named_as_asked() -> None:
    """yardstick-v2 F101, "rebounds allowed per team": the alias `rebounds`
    ranked the teams' own rebounds. Named as asked, no team metric holds it
    and it is refused by name; "points allowed" is an alias of its own,
    and "against the knicks" is the team's own side."""
    from association.query.measure import team_metric_named

    def _named(question: str) -> str | None:
        found = team_metric_named(question)
        return found[0] if found is not None else None

    assert _named("rebounds allowed per team") == "rebounds allowed"
    assert _named("which team has given up the most points allowed") == "points allowed"
    assert _named("celtics rebounds against the knicks") == "rebounds"


@pytest.mark.parametrize("name", ["PJ Washington", "Allan Houston", "Magic Johnson", "Houstan", "Burks", "Hawkins", "Thornton", "Wheat"])
def test_a_player_whose_name_looks_like_a_team_is_still_a_player(name: str) -> None:
    """The reason a city is matched against the WHOLE name and never the last
    word: three players are surnamed Cleveland, Houston and Washington. The
    rest are why a near spelling is not matched at all - Burks/Bucks,
    Hawkins/Hawks, Thornton/Toronto and Wheat/Heat are all within one
    edit, and the model hands bare surnames as names."""
    from association.query.subject import is_team_name as _is_team_name

    assert not _is_team_name(name)


def test_the_threshold_words_are_read_from_the_one_measure_word_table() -> None:
    """#164: what a stat word means has one definition, the lexicon's
    ``MEASURE_WORDS``, which the answer side's ``measures`` module reads as
    the same object; the threshold words are derived from it, so a spelling
    dropped there raises at import rather than silently narrowing what the
    threshold grammar understands ("one concept, one definition", which
    ``check_duplicate_names.py`` cannot see under two names)."""
    from association.query.lexicon import MEASURE_WORDS as LINES_MEASURE_WORDS
    from association.query.lexicon import THRESHOLD_SPELLINGS as _THRESHOLD_SPELLINGS
    from association.query.lexicon import THRESHOLD_WORDS as _THRESHOLD_WORDS
    from association.query.measures import MEASURE_WORDS

    # The one table, read by the grammar's rules and by the below/above phrase reader alike.
    assert LINES_MEASURE_WORDS is MEASURE_WORDS
    assert set(_THRESHOLD_WORDS) == set(_THRESHOLD_SPELLINGS)
    for word, means in _THRESHOLD_WORDS.items():
        assert MEASURE_WORDS[word] == means, word


def test_the_threshold_pair_words_and_their_pattern_cannot_drift() -> None:
    """The alternation is built from `_THRESHOLD_WORDS`, so every word the map
    knows is a word the pattern matches, and every word it matches has a
    meaning."""
    from association.query.lexicon import THRESHOLD_PAIR as _THRESHOLD_PAIR
    from association.query.lexicon import THRESHOLD_WORDS as _THRESHOLD_WORDS

    for word, means in _THRESHOLD_WORDS.items():
        match = _THRESHOLD_PAIR.search(f"games with 20+ {word} this season")
        assert match is not None, word
        assert match.group(2).casefold() == word
        assert _THRESHOLD_WORDS[match.group(2).casefold()] == means
    # A "+" is still required - "top 10 rebound leaders" is not a condition.
    assert _THRESHOLD_PAIR.search("top 10 rebound leaders") is None


def test_a_subject_keeps_the_first_name_the_question_gave_it(subtests: Any) -> None:
    """#165: a possessive keeps the whole name before it, not a bare surname
    ("bryant" alone is Bryant Reeves, Bryant Stith, Carter Bryant and Elijah
    Bryant, and not Kobe), gated on the stopword list so "most points curry
    scored" reads "curry" and never "points curry"."""
    from association.query.subject import subject_named_in as _subject_named_in

    for question, want in (
        ("kobe bryant's stats vs rockets in the 2009 playoffs ts% each game", "kobe bryant"),
        ("Jaden mcdaniel's vs trail blazer last 5 games", "Jaden mcdaniel"),
        ("steve adam's vs kings last 10 games", "steve adam"),
        ("most points curry scored in a game this season", "curry"),
        ("curry's highest scoring game this season", "curry"),
        # A bare "had"/"has" is a subject position only when a threshold
        # follows, as "scored" is.
        ("sixers record when maxey had 10+ rebounds", "maxey"),
        ("warriors record when curry has 30+ points", "curry"),
        # "when" is not part of the name, and a stat's own noun is not a
        # person ("when maxey", "points" and "game" are none).
        ("Total points scored by the toronto raptors in the last 10 games", None),
        ("least points scored by the wizards in the first half this season", None),
        ("game score nba leader", None),
        ("who scored the most points this season", None),
    ):
        with subtests.test(question=question):
            assert _subject_named_in(question) == want


def test_the_coach_refusal_names_the_source_rather_than_blaming_it() -> None:
    """The obvious sentence - "ESPN does not publish coaches" - is false:
    ESPN serves two coach collections, both unusable.
    Saying the source has none would be the wrong-cause refusal this project
    keeps producing, so the sentence says what is actually wrong with them."""
    answer = refusal_result(Cause(kind="no_coach_table", facts={"unanswerable": "coach"})).text
    assert "No table here holds a coach" in answer
    assert "ESPN does publish coaches" in answer
    assert "Player and team questions are unaffected" in answer


def test_the_kind_assigned_asks_all_have_readers() -> None:
    """A child the reading assigns is a shape a reader takes - a key of the
    answer side's routes (``compose._ROUTES``) - and the grammar's rules
    assign none of them."""
    from association.query import compose
    from association.query.subject import KIND_ASSIGNED_ASKS

    assert set(compose._ROUTES) >= KIND_ASSIGNED_ASKS
    assert KIND_ASSIGNED_ASKS.isdisjoint(CODE_ASSIGNED_ASKS)


def test_a_threshold_without_a_plus_reads_every_spelling_the_pair_grammar_does() -> None:
    """A bare "N stat" threshold reads the pair grammar's spellings, "pt",
    "reb" and "ast" among them: "30 pt games" reads a threshold, not a
    ranking."""
    from association.query.line import threshold_named as _threshold_from_text

    assert _threshold_from_text("who had the most 30 pt games in 2024") == 30
    assert _threshold_from_text("games with 15 reb this season") == 15
    assert _threshold_from_text("3 pt shots") is None and _threshold_from_text("3 point shots") is None
