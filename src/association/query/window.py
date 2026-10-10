"""The window tagger: the one reader of which rows a question keeps and from
which end (:class:`~association.query.reading.Window`) - "last 10 games",
"his first game", "top 5", a history's "past 5 years" as its count of
seasons, which end of a team ranking, the measure a ranking of games over a
yes/no stat is ordered by - from the lexicon's words
(:mod:`association.query.lexicon`) and the two facts of the question the
stages settled before it: the intent, and whether the stat read is a yes/no
one. It claims the characters it read
(:class:`~association.query.reading.Claim`), each once.

Phase 3, step 2's second slice: until it, the parser read the window's
grammar before the stages (``parse.window``) and again after them
(``parse.window_scope``), and five stages of ``router.py``
wrote or dropped the four slots this replaces with their own regexes
(``_route_relative_window``, the window half of ``_route_side_and_order``
with ``_drop_filler_limit`` and ``_drop_filler_order_on_a_series_game``,
``_route_period_window``, the rank writer in ``_route_intent_slots``,
``_route_ranked_boolean_games``, and the limit pops in
``_route_relation_intent_slots``, ``_route_line_stat`` and
``_route_one_player_intents``). Measured first
(``~/association-research/stages/window_family.py``, the four slots as each
stage set them on all 2,710 readings): the parser's grammar read the window
on 314 of them and the stages moved it on 59 - a history's seasons (38) and
an end dropped on a reader with no window of its own (21) - while every
drop of the COUNT (the model-era filler rules: ``_names_a_count`` and the
five sites that asked it) was put back by the parser's second read, since
the grammar was the count's one source; those rules were dead, and are
gone rather than ported.

.. versionadded:: 6.0.0
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from association.query import lexicon
from association.query.reading import (
    Claim,
    ScopeError,
    Window,
)
from association.query.span import range_named, relative_seasons


@dataclass(frozen=True, kw_only=True)
class WindowContext:
    """What the tagger reads beside the words: whether the stat read is a
    yes/no one (a triple-double, a double-double, fouling out), over whose
    games a ranking by another measure is read - the measure tagger's
    reading, not the key the words ask.

    .. versionadded:: 6.0.0

    .. versionchanged:: 6.0.0
       ``asked`` went (Phase 3, step 4, slice (b)): the key's reading of
       the window is :func:`~association.query.point.window_under`.
    """

    boolean_stat: bool = False


@dataclass(frozen=True)
class WindowCandidates:
    """Every reading of the window the words hold, read with no key: the
    grammar's end and count (:data:`~association.query.lexicon.WINDOW_GRAMMAR`),
    a count of SEASONS ("the past 5 years", where no range is named), one
    game named as the subject's own (:data:`~association.query.lexicon.SINGLE_GAME`:
    its end), the looser end words (:data:`~association.query.lexicon.ORDER_WORDS`),
    a ranking's end (:data:`~association.query.lexicon.RANK_WORDS`), and the
    measure a ranking of yes/no games is ordered by - each with the
    characters that said it. Which stand is the key the words ask
    (:func:`~association.query.point.window_under`).

    .. versionadded:: 6.0.0
    """

    grammar: tuple[str | None, int | None, Claim | None]
    seasons: tuple[int, Claim] | None
    single: tuple[str, Claim] | None
    order_words: tuple[tuple[str, Claim], ...]
    rank: tuple[Literal["most", "fewest", "best", "worst"], Claim] | None
    ranked_by: tuple[str, tuple[Claim, ...]] | None


@dataclass(frozen=True)
class WindowRead:
    """What the window is under the key the words ask: the
    :class:`~association.query.reading.Window` and the characters read.

    .. versionadded:: 6.0.0
    """

    window: Window
    claims: tuple[Claim, ...]


def read_window(question: str, context: WindowContext) -> WindowCandidates:
    """Every reading of the window ``question``'s words hold
    (:class:`WindowCandidates`), with no key.

    .. versionadded:: 6.0.0

    .. versionchanged:: 6.0.0
       Reads no key (Phase 3, step 4, slice (b)): it read the window under
       the key the stages settled (``ORDER_ASKS``, ``LIMIT_COUNTS_SEASONS``,
       the one-game and ranked keys), which
       :func:`~association.query.point.window_under` reads now.
    """
    seasons = _seasons_counted(question)
    single = lexicon.SINGLE_GAME.search(question)
    order_words = tuple((name, Claim(match.start(), match.end(), "window")) for name, pattern in lexicon.ORDER_WORDS.items() if (match := pattern.search(question)) is not None)
    return WindowCandidates(
        grammar=_grammar(question),
        seasons=seasons,
        single=(_single_game_end(single), Claim(single.start(), single.end(), "window")) if single is not None else None,
        order_words=order_words,
        rank=_rank(question),
        ranked_by=_ranked_by(question, context),
    )


def window_end(order: str | None) -> Literal["recent", "first"] | None:
    """A grammar row's end as the Window's own literal - the rows write one
    of the two, and a row that wrote anything else is a bug said out loud,
    as the slot door says it for a slot dict.

    .. versionadded:: 6.0.0
    """
    if order == "recent":
        return "recent"
    if order == "first":
        return "first"
    if order:
        raise ScopeError(f"window order {order!r} is not 'recent' or 'first'")
    return None


def _grammar(question: str) -> tuple[str | None, int | None, Claim | None]:
    """The end and the count the window grammar reads off the words
    (:data:`~association.query.lexicon.WINDOW_GRAMMAR`: its first matching
    row), with the characters it read - or none of the three."""
    for pattern, order, limit in lexicon.WINDOW_GRAMMAR:
        match = pattern.search(question)
        if match is None:
            continue
        claim = Claim(match.start(), match.end(), "window")
        if limit == 0:
            number = next((g for g in match.groups() if g and lexicon.COUNT_WORD.fullmatch(g)), None)
            return order, lexicon.count_of(number) if number is not None else None, claim
        return order, limit, claim
    return None, None, None


def _seasons_counted(question: str) -> tuple[int, Claim] | None:
    """How many seasons "past/last N seasons" names, with the characters
    that said so - unless the words name a range outright, which wins. A
    count of seasons where the key counts seasons
    (:data:`~association.query.point.LIMIT_COUNTS_SEASONS`; the key's
    reading, :func:`~association.query.point.window_under`)."""
    if range_named(question) is not None:
        return None
    seasons = relative_seasons(question)
    if seasons is None:
        return None
    match = lexicon.PAST_N_SEASONS.search(question)
    assert match is not None  # relative_seasons read it from the same pattern
    return seasons, Claim(match.start(), match.end(), "window")


def _single_game_end(single: re.Match[str]) -> str:
    """Which end of the span one game named as the subject's own sits at
    (:data:`~association.query.lexicon.SINGLE_GAME`'s first group)."""
    return "first" if single.group(1).lower() in ("first", "opening", "earliest") else "recent"


def _rank(question: str) -> tuple[Literal["most", "fewest", "best", "worst"], Claim] | None:
    """Which end of a ranking the words ask for: the first of
    :data:`~association.query.lexicon.RANK_WORDS` that matches, in the
    table's order - kept on the keys that rank by one (the key's reading)."""
    for name, pattern in lexicon.RANK_WORDS:
        match = pattern.search(question)
        if match is not None:
            return name, Claim(match.start(), match.end(), "rank")
    return None


def _ranked_by(question: str, context: WindowContext) -> tuple[str, tuple[Claim, ...]] | None:
    """The measure a ranking of the games over a yes/no stat is ordered by
    ("players with the highest scoring triple doubles", yardstick-v2 F124):
    the SAME names and stat as "most triple doubles", which a count per
    player answers rightly, so the word that tells the two apart is the
    window's - the measure word where one is named, points otherwise. A
    bare "most triple doubles" reads nothing here and keeps its count. Kept
    on the players' ranking alone (the key's reading)."""
    if not context.boolean_stat:
        return None
    ranked = lexicon.RANKED_BOOLEAN_GAMES.search(question)
    if ranked is None:
        return None
    claims = [Claim(ranked.start(), ranked.end(), "ranked_by")]
    word = lexicon.RANKED_BY_WORD.search(question)
    measure = (word.group(1).lower() if word else "points").rstrip("s")
    if word is not None:
        claims.append(Claim(word.start(), word.end(), "ranked_by"))
    return "points" if measure in ("scoring", "point") else measure + ("s" if not measure.endswith("s") else ""), tuple(claims)
