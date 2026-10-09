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
(``parse.window_scope``), and five stages of :mod:`association.query.router`
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

from dataclasses import dataclass
from typing import Literal

from association.query import lexicon
from association.query.reading import Claim, ScopeError, Window
from association.query.span import LIMIT_COUNTS_SEASONS, claimed, range_named, relative_seasons

ORDER_INTENTS: frozenset[str] = frozenset({"fingerprint", "game_log", "period_split", "player_netpoints", "shot_chart", "shot_distance", "team_quarter_points"})
"""Intents whose reader honors ``order``, so filling it from the question can
only make the answer match what was asked: on these the end of the span is
read from :data:`~association.query.lexicon.ORDER_WORDS` where the grammar
read none, and on every other intent an end the grammar read beside a
count of more than one is dropped, since the reader would refuse the
question for it ("a record over the last N games is game_log's question").
Guarded by ``test_the_order_intents_are_the_ones_that_honor_order`` against
the planner's stated scoping.

.. versionadded:: 2.1.0

.. versionchanged:: 4.4.0
   Added ``team_quarter_points``: it reads its games through the team-games
   relation, which honors a window.

.. versionchanged:: 6.0.0
   In the window tagger (``router.ORDER_INTENTS`` until Phase 3, step 2).
"""

#: Intents that honor an end of the span only beside a real count - one
#: game at one end of it - because filling the end alone would hand "his
#: last game" to a log of his last ten. Read with
#: :data:`~association.query.lexicon.SINGLE_GAME`; the pair is what makes
#: "last game" one game.
_ORDER_ON_A_SINGLE_GAME: frozenset[str] = frozenset({"player_stat"})

#: Intents where an end of the span narrows to ONE game rather than ordering
#: a list: shot_chart, shot_distance, player_netpoints and fingerprint each
#: resolve it to a single event id, where game_log only sorts. So an end
#: read beside a count of two or more costs a whole season here - "a shot
#: chart of steph curry's 2025 season for 3 point shots" drew one game, 7 of
#: 12, where 2025 held hundreds (#153) - and the question has to name a game
#: at one end of the span for it to stand.
_ORDER_IS_ONE_GAME: frozenset[str] = frozenset({"shot_chart", "shot_distance", "player_netpoints", "fingerprint"})

#: The intents whose ranking has an end the question names ("most", "fewest",
#: "best", "worst"): a team ranking, and a team's single best quarter or
#: half, where the rank word is what makes "most points in a first half" one
#: game rather than the season's average.
_RANKED_INTENTS: frozenset[str] = frozenset({"team_leaderboard", "team_quarter_points"})


@dataclass(frozen=True, kw_only=True)
class WindowContext:
    """What the stages settled before the window is read, and the tagger's
    rules read beside the words: the intent they settled on, and whether
    the stat read is a yes/no one (a triple-double, a double-double, fouling
    out), over whose games a ranking by another measure is read.

    .. versionadded:: 6.0.0
    """

    intent: str
    boolean_stat: bool = False


@dataclass(frozen=True)
class WindowRead:
    """What the tagger read: the :class:`~association.query.reading.Window`
    and the characters it claimed.

    .. versionadded:: 6.0.0
    """

    window: Window
    claims: tuple[Claim, ...]


def read_window(question: str, context: WindowContext) -> WindowRead:
    """The window ``question``'s words name, under ``context``.

    .. versionadded:: 6.0.0
    """
    claims: list[Claim] = []
    order, count, grammar_claim = _grammar(question)
    if grammar_claim is not None:
        claims.append(grammar_claim)
    of: Literal["games", "seasons"] = "games"
    seasons = _seasons_counted(question, context)
    if seasons is not None:
        # A history's count is of SEASONS: "the past 5 years" IS that count
        # and needs no range - set as a range instead, "show me sga's 2pt
        # percentage for the past 5 years" refused (#140 and #114, each
        # sound alone). The span tagger claims the same words as a range.
        count, of = seasons[0], "seasons"
        claims.append(seasons[1])
    order, order_claim, single = _order(question, context, order, count)
    if order_claim is not None:
        claims.append(order_claim)
    if single:
        # One game of a player's line: the end and a count of one together
        # - an end without the count would list ten games where one was
        # asked for.
        count, of = 1, "games"
    rank, rank_claim = _rank(question, context)
    if rank_claim is not None:
        claims.append(rank_claim)
    by, by_claims = _ranked_by(question, context)
    claims.extend(by_claims)
    window = Window(order=_as_end(order), count=count, of=of, rank=rank, by=by)
    return WindowRead(window, claimed(claims))


def _as_end(order: str | None) -> Literal["recent", "first"] | None:
    """A grammar row's end as the Window's own literal - the rows write one
    of the two, and a row that wrote anything else is a bug said out loud,
    as the Scope's door says it for a slot dict."""
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


def _seasons_counted(question: str, context: WindowContext) -> tuple[int, Claim] | None:
    """How many seasons "past/last N seasons" names, for an intent whose
    count is of seasons (``LIMIT_COUNTS_SEASONS``), with the characters
    that said so - unless the words name a range outright, which wins."""
    if context.intent not in LIMIT_COUNTS_SEASONS or range_named(question) is not None:
        return None
    seasons = relative_seasons(question)
    if seasons is None:
        return None
    match = lexicon.PAST_N_SEASONS.search(question)
    assert match is not None  # relative_seasons read it from the same pattern
    return seasons, Claim(match.start(), match.end(), "window")


def _order(question: str, context: WindowContext, order: str | None, count: int | None) -> tuple[str | None, Claim | None, bool]:
    """Which end of the span the rows are taken from, settled against the
    intent - three rules, each measured: one game of a player's line ("his
    last game", "steph curry's last regular season game") is that game,
    the end and a count of one together, which the line answers by handing
    the question to the log; on an intent whose reader honors an end
    (:data:`ORDER_INTENTS`) one the grammar missed is read from the
    looser :data:`~association.query.lexicon.ORDER_WORDS` where exactly one
    matches, and on the four where an end means ONE game
    (:data:`_ORDER_IS_ONE_GAME`) it stands only where the words name a game
    at one end; on any other intent an end read beside a count of two or
    more is dropped, since the reader refuses the question for it, while
    the count stands ("lakers vs mavs record last 10 home games" is their
    last ten meetings). Returns the end, its claim, and whether one game of
    a line was named."""
    if context.intent in _ORDER_ON_A_SINGLE_GAME:
        single = lexicon.SINGLE_GAME.search(question)
        if single is not None:
            end = "first" if single.group(1).lower() in ("first", "opening", "earliest") else "recent"
            return end, Claim(single.start(), single.end(), "window"), True
        return *_order_elsewhere(question, order, count), False
    if context.intent in ORDER_INTENTS:
        claim = None
        if order is None:
            named = [(name, match) for name, pattern in lexicon.ORDER_WORDS.items() if (match := pattern.search(question)) is not None]
            if len(named) == 1:
                order = named[0][0]
                claim = Claim(named[0][1].start(), named[0][1].end(), "window")
        if order is not None and context.intent in _ORDER_IS_ONE_GAME and not _names_one_game(question):
            return None, None, False
        return order, claim, False
    return *_order_elsewhere(question, order, count), False


def _order_elsewhere(question: str, order: str | None, count: int | None) -> tuple[str | None, Claim | None]:
    """An end on an intent whose reader honors none: kept beside a count of
    one (one game is what was asked, and the reader refuses or hands it on
    by name) or where the words say it with "games" outright
    (:data:`~association.query.lexicon.ORDER_WORDS`), dropped beside a
    larger count the reader can still take."""
    if order is not None and count not in (None, 1) and not any(pattern.search(question) for pattern in lexicon.ORDER_WORDS.values()):
        return None, None
    return order, None


def _rank(question: str, context: WindowContext) -> tuple[Literal["most", "fewest", "best", "worst"] | None, Claim | None]:
    """Which end of a team ranking was asked for, on the intents that rank
    (:data:`_RANKED_INTENTS`): the first of :data:`~association.query.lexicon.RANK_WORDS`
    that matches, in the table's order."""
    if context.intent not in _RANKED_INTENTS:
        return None, None
    for name, pattern in lexicon.RANK_WORDS:
        match = pattern.search(question)
        if match is not None:
            return name, Claim(match.start(), match.end(), "rank")
    return None, None


def _ranked_by(question: str, context: WindowContext) -> tuple[str | None, list[Claim]]:
    """The measure a ranking of the games over a yes/no stat is ordered by
    ("players with the highest scoring triple doubles", yardstick-v2 F124):
    the SAME names and stat as "most triple doubles", which a count per
    player answers rightly, so the word that tells the two apart is the
    window's - the measure word where one is named, points otherwise. A
    bare "most triple doubles" reads nothing here and keeps its count."""
    if context.intent != "leaderboard" or not context.boolean_stat:
        return None, []
    ranked = lexicon.RANKED_BOOLEAN_GAMES.search(question)
    if ranked is None:
        return None, []
    claims = [Claim(ranked.start(), ranked.end(), "ranked_by")]
    word = lexicon.RANKED_BY_WORD.search(question)
    measure = (word.group(1).lower() if word else "points").rstrip("s")
    if word is not None:
        claims.append(Claim(word.start(), word.end(), "ranked_by"))
    return "points" if measure in ("scoring", "point") else measure + ("s" if not measure.endswith("s") else ""), claims


def _names_one_game(question: str) -> bool:
    """Whether the question itself asks for a game at one end of the span -
    "his last game", "first 5 games" - rather than leaving the end to a
    looser reading (``router._names_one_game`` until Phase 3, step 2)."""
    return lexicon.SINGLE_GAME.search(question) is not None or any(pattern.search(question) for pattern in lexicon.ORDER_WORDS.values())
