"""The grammar and the stages' rules, tested at the unit (Phase 3, step 4, slice (c)): what
`tests/query/test_router.py` held that was not a question-to-Reading case -
every reachable key, the sets and value tables held to the lexicon and the
types, and the few rule-level checks over a tagger. The question cases are
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


def test_every_intent_is_reachable_from_the_reader() -> None:
    """Every shape the words can ask must be REACHABLE, by one of exactly
    three routes: the parser's grammar names it as a parent
    (``parse.PARENT_GRAMMAR``), the stages assign it from the question text
    (``CODE_ASSIGNED_ASKS``), or the subject reading assigns it from the
    text gated on the subject's kind (``subject.KIND_ASSIGNED_ASKS``). A
    key in none of the lists is dead code that no question can ever reach.
    Until Phase 2, step 6 this read
    ``test_every_template_is_reachable_from_the_reader``, over the
    templates; until Phase 3, step 4 it held the intents' names
    (``compose.COMPILED_INTENTS``) - the asked keys now, every one the
    reading module names (:func:`_asked_keys`).

    Until 5.0.0 the first route was the router's schema enum, and this read
    ``test_every_ported_template_has_an_intent_in_the_schema``."""
    from association.query.parse import PARENT_GRAMMAR
    from association.query.subject import KIND_ASSIGNED_ASKS

    parents = {parent for _, _, parent in PARENT_GRAMMAR}
    assert parents | CODE_ASSIGNED_ASKS | KIND_ASSIGNED_ASKS == _asked_keys()


def _asked_keys() -> set[PointShape]:
    """Every key the words can ask, as the reading module names them
    (``reading.PLAYER_LOG``, ...): the grammar's 26 (``test_frozen_shapes``
    freezes them; the 26th, ``PLAYERS_COUNTED``, and the 27th, a team's best
    single game, by Jeff's lifting of D4 on 2026-10-13; the 28th, a team's
    history season by season, on 2026-10-14). Until Phase 4, step 0 the tests' label table
    (``tests/shapes.ASKED``) listed them by the retired intents' names."""
    from association.query import reading

    keys = {value for name, value in vars(reading).items() if name.isupper() and isinstance(value, PointShape)}
    assert len(keys) == 28, sorted(map(str, keys))  # 25 + the two keys D4 was lifted for on 2026-10-13 + a team's history, lifted 2026-10-14 (#372)
    return keys


def test_the_side_values_match_the_scope() -> None:
    """Two hand-maintained lists of the same names is the shape that produced
    the player_compare bug - a side word the Measure refuses falls the question
    through, and one the Measure holds that no word names gets dropped. The
    other list was the router's schema enum until 5.0.0, then the router's
    ``SIDE_WORDS``; the measure tagger reads the lexicon's since Phase 3, step 2."""
    from association.query.lexicon import SIDE_WORDS
    from association.query.reading import MeasureSide

    assert set(SIDE_WORDS) | {"total"} == set(get_args(MeasureSide))


def test_the_order_values_match_the_scope() -> None:
    """Same shape as the side check above: a value here the Scope refuses
    falls the question through, and one it holds that is missing here gets
    dropped."""
    assert set(ORDER_WORDS) == _scope_values("order", Window)


def test_the_order_intents_are_the_ones_that_honor_order() -> None:
    """Two hand-maintained lists of the same intents, kept apart so the router
    does not import the templates. An intent honoring `order` and missing here
    keeps the bug this fixed; one listed here that does not honor it turns
    into a fall-through."""
    from association.query.compose.plan import cells_stated
    from association.query.point import _WINDOW_ORDER_ON_A_SINGLE_GAME as _ORDER_ON_A_SINGLE_GAME

    # player_stat honors an order only beside a limit of one (a single game
    # handed to game_log), so the tagger sets the pair together for it rather
    # than filling order alone - see _ORDER_ON_A_SINGLE_GAME. A retired
    # template's list is what its reader's words state (STATED_SCOPING); the
    # cell is the window's (Window.CELLS, "window") since Phase 3, step 2;
    # the shapes are the keys the words ask since Phase 4, step 0, where the
    # retired names stood.
    assert {shape for shape in _asked_keys() if "window" in cells_stated(shape)} == ORDER_ASKS | _ORDER_ON_A_SINGLE_GAME


def test_a_last_n_games_signal_requires_a_real_limit_and_the_recent_order() -> None:
    from association.query.point import span_under
    from association.query.span import SpanContext, read_span

    # No `order` at all: an ordinary game_log question with a limit, not "last
    # N games" - e.g. "top 5" is `order` absent, `limit` present.
    assert not span_under(PLAYER_LOG, read_span("Knicks top 5 wins this season", SpanContext(limit=5))).span.both
    # `order="first"` is "his first N games", the opposite end of the season -
    # a real question, but not the one this signal is for.
    assert not span_under(PLAYER_LOG, read_span("Knicks first 5 games", SpanContext(order="first", limit=5))).span.both
    # No real limit at all.
    assert not span_under(PLAYER_LOG, read_span("Knicks last games", SpanContext(order="recent"))).span.both
    # A non-game_log intent never sees it, whatever else is set.
    assert not span_under(PLAYER_LINE, read_span("Knicks last 5 games", SpanContext(order="recent", limit=5))).span.both
    assert span_under(PLAYER_LOG, read_span("Knicks last 5 games", SpanContext(order="recent", limit=5))).span.both


@pytest.mark.parametrize(
    ("question", "context"),
    [
        ("Knicks last 5 games", {"game_n": 4}),  # one game of a KNOWN playoff series
        ("Knicks last 5 games of his career", {}),  # a career has no single year to mix two types within
        ("Knicks last 5 games since 2020", {}),  # a range of seasons
        ("Knicks last 5 games", {"date": "2026-04-12"}),  # one calendar day already finds its own game
    ],
)
def test_a_last_n_games_signal_defers_to_a_narrower_slot_already_set(question: str, context: dict[str, Any]) -> None:
    """Each of these already fixes which games are meant more precisely than
    "last N" does, so none of them widen to both season types."""
    from association.query.point import span_under
    from association.query.span import SpanContext, read_span

    assert not span_under(PLAYER_LOG, read_span(question, SpanContext(order="recent", limit=5, **context))).span.both


def test_a_team_metric_given_up_is_named_as_asked() -> None:
    """yardstick-v2 F101, "rebounds allowed per team": the alias `rebounds`
    ranked the teams' own rebounds. Named as asked, no team metric holds it
    and the template refuses by name; "points allowed" is an alias of its own,
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
    `difflib` step, and the model puts bare surnames in that slot."""
    from association.query.subject import is_team_name as _is_team_name

    assert not _is_team_name(name)


def test_the_router_and_the_templates_agree_on_what_a_stat_word_means() -> None:
    """#164: the router kept its own copy of the vocabulary
    `templates/common.py` reads, and nothing checked that they agreed - the
    shape AGENTS.md calls "one concept, one definition", which
    `check_duplicate_names.py` cannot see because the two names differ.

    There is one definition now (`query/measures.py`), and the router derives
    its map from it, so a spelling dropped there raises at import rather than
    silently narrowing what the threshold grammar understands. This pins that
    the derivation stays a derivation."""
    from association.query.lexicon import MEASURE_WORDS as LINES_MEASURE_WORDS
    from association.query.lexicon import THRESHOLD_SPELLINGS as _THRESHOLD_SPELLINGS
    from association.query.lexicon import THRESHOLD_WORDS as _THRESHOLD_WORDS
    from association.query.measures import MEASURE_WORDS

    # The one table, read by the stages and by the below/above phrase reader alike.
    assert LINES_MEASURE_WORDS is MEASURE_WORDS
    assert set(_THRESHOLD_WORDS) == set(_THRESHOLD_SPELLINGS)
    for word, means in _THRESHOLD_WORDS.items():
        assert MEASURE_WORDS[word] == means, word


def test_the_threshold_pair_words_and_their_pattern_cannot_drift() -> None:
    """The alternation is built from `_THRESHOLD_WORDS`, so every word the map
    knows is a word the pattern matches, and every word it matches has a
    meaning. They were a hand-kept list and a map before, which could disagree
    about a word with nothing to notice."""
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
    """#165: `_SUBJECT_OF_HIGH` captured ONE word, so a possessive gave a bare
    surname. Measured over the 261-question corpus, three questions wrote the
    name out and got a clarification listing players they never mentioned -
    "bryant" is Bryant Reeves, Bryant Stith, Carter Bryant and Elijah Bryant,
    and not Kobe. The count grammars already captured a leading word; this one
    does now, gated on the same richer stopword list so "most points curry
    scored" still reads "curry" and never "points curry"."""
    from association.query.subject import subject_named_in as _subject_named_in

    for question, want in (
        ("kobe bryant's stats vs rockets in the 2009 playoffs ts% each game", "kobe bryant"),
        ("Jaden mcdaniel's vs trail blazer last 5 games", "Jaden mcdaniel"),
        ("steve adam's vs kings last 10 games", "steve adam"),
        ("most points curry scored in a game this season", "curry"),
        ("curry's highest scoring game this season", "curry"),
        # A bare "had"/"has" is a subject position only when a threshold
        # follows: "sixers record when maxey had 10+ rebounds" named nobody,
        # while the same question with "scored" answered.
        ("sixers record when maxey had 10+ rebounds", "maxey"),
        ("warriors record when curry has 30+ points", "curry"),
        # "when" is not part of the name, and a stat's own noun is not a
        # person: these three returned "when maxey", "points" and "game".
        ("Total points scored by the toronto raptors in the last 10 games", None),
        ("least points scored by the wizards in the first half this season", None),
        ("game score nba leader", None),
        ("who scored the most points this season", None),
    ):
        with subtests.test(question=question):
            assert _subject_named_in(question) == want


def test_the_coach_refusal_names_the_source_rather_than_blaming_it() -> None:
    """The obvious sentence - "ESPN does not publish coaches" - was probed on
    2026-09-17 and is false: it serves two coach collections, both unusable.
    Saying the source has none would be the wrong-cause refusal this project
    keeps producing, so the sentence says what is actually wrong with them."""
    answer = refusal_result(Cause(kind="no_coach_table", facts={"unanswerable": "coach"})).text
    assert "No table here holds a coach" in answer
    assert "ESPN does publish coaches" in answer
    assert "Player and team questions are unaffected" in answer


def test_the_kind_assigned_intents_all_have_readers() -> None:
    """A child the reading assigns is a shape a reader takes - a key of the
    answer side's routes (``compose._ROUTES``; of the page's label table,
    ``reading.SHAPE_NAMES``, until Phase 4, step 2) - and the stages assign
    none of them."""
    from association.query import compose
    from association.query.subject import KIND_ASSIGNED_ASKS

    assert set(compose._ROUTES) >= KIND_ASSIGNED_ASKS
    assert KIND_ASSIGNED_ASKS.isdisjoint(CODE_ASSIGNED_ASKS)


def test_a_threshold_without_a_plus_reads_every_spelling_the_pair_grammar_does() -> None:
    """`_THRESHOLD` used to be a second hand-kept alternation that lacked
    "pt", "reb" and "ast", so "30 pt games" read no threshold and became a
    ranking."""
    from association.query.line import threshold_named as _threshold_from_text

    assert _threshold_from_text("who had the most 30 pt games in 2024") == 30
    assert _threshold_from_text("games with 15 reb this season") == 15
    assert _threshold_from_text("3 pt shots") is None and _threshold_from_text("3 point shots") is None
