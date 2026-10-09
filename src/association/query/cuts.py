"""The cuts tagger: the one reader of which games of a span a read sees
(:class:`~association.query.reading.Cuts`) - a venue, a circumstance (a
weekday, a month, a holiday, "since <day>", the opponent's conference or
division, or words nothing narrows by), one calendar day, a playoff round, a
game of a series, an ordinal season - from the lexicon's words
(:mod:`association.query.lexicon`) and the facts of the question the stages
settled before it: the intent, the split, the opponent the subject reading
read and the teammates it read as absent. The opponent is the subject
reading's word (``subject.read_subject``, the one reader of who stands
against the subject) and the tenure is written by it after the stages
(``subject._apply_own_team``): the tagger takes the first and claims
nothing for either. It claims the characters it read
(:class:`~association.query.reading.Claim`), each once.

Phase 3, step 2's third slice: until it, the filter stage of
:mod:`association.query.router` wrote the venue and the situation
(``_route_filter_slots``), the calendar stage the date, the round, the
series game and the ordinal season (``_route_calendar_slots``, with
``_validate_date``), the intent stage dropped a venue beside a venue split,
and the last stage dropped an opponent that was the ``without`` list again
(``_route_opponent_named_as_teammates``), each with its own regexes.
Measured first (``~/association-research/stages/cuts_family.py``, the eight
slots as each stage set them on all 2,710 readings): the stages moved none
of them between the route and the reading but the tenure (14), which the
subject reading writes; a date and its own month stand together on 4,
read by two rules off one word ("in march 24 2018"), two claims
:func:`~association.query.span.claimed` joins into one.

.. versionadded:: 6.0.0
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

from association.nba.season import current_season
from association.query import lexicon
from association.query.reading import Claim, Cuts, situation_of
from association.query.span import career_named, claimed


@dataclass(frozen=True, kw_only=True)
class CutsContext:
    """What the stages settled before the cuts are read, and the tagger's
    rules read beside the words: the intent they settled on; the split the
    words name (a venue beside a venue split is the split's, not a cut);
    the opponent as the subject reading read it; and the teammates it read
    as absent (an "opponent" that is the ``without`` list again is no team).

    .. versionadded:: 6.0.0
    """

    intent: str
    split: str | None = None
    opponent: str | None = None
    without: tuple[str, ...] = ()


@dataclass(frozen=True)
class CutsRead:
    """What the tagger read: the :class:`~association.query.reading.Cuts`,
    the characters it claimed, and the year a range opened on a dated day
    names ("since 1/26/20"), which the span tagger starts the seasons at.

    .. versionadded:: 6.0.0
    """

    cuts: Cuts
    claims: tuple[Claim, ...]
    dated_since: int | None = None


def read_cuts(question: str, context: CutsContext) -> CutsRead:
    """The cuts ``question``'s words name, under ``context``.

    .. versionadded:: 6.0.0
    """
    claims: list[Claim] = []
    venue, venue_claim = _venue(question, context)
    if venue_claim is not None:
        claims.append(venue_claim)
    situation, situation_claim = _situation(question)
    day, day_claim = _day(question)
    dated_since = None
    if day_claim is None and situation is None:
        # A date that named itself but could not be pinned to one day - a
        # range opened on it, or a day on a career question - is a
        # situation, which the relation reads or refuses by value, and the
        # year it wrote, if any, is where the span's seasons start.
        situation, dated_since, situation_claim = _dated_situation(question)
    # A day and its own month ("in march 24 2018") are two claims over one
    # word, which `claimed` joins; the month is applied beside the day,
    # redundantly, and the answer names both (ISSUES.md, "A month is read
    # from the words of a day in it").
    claims.extend(claim for claim in (situation_claim, day_claim) if claim is not None)
    playoff_round, round_claim = _round(question)
    if round_claim is not None:
        claims.append(round_claim)
    game_n, game_claim = _series_game(question)
    if game_claim is not None:
        claims.append(game_claim)
    season_n, season_claim = _ordinal_season(question)
    if season_claim is not None:
        claims.append(season_claim)
    cuts = Cuts(
        opponent=_opponent(context),
        venue=venue,
        date=day,
        situation=situation_of(situation) if situation is not None else None,
        round=playoff_round,
        game_n=game_n,
        season_n=season_n,
    )
    return CutsRead(cuts, claimed(claims), dated_since)


def _venue(question: str, context: CutsContext) -> tuple[Literal["home", "away"] | None, Claim | None]:
    """ "home" or "away" when the question restricts itself to one of them,
    with the characters that said so. Both at once is a SPLIT ("home and
    away splits"), not a cut, so it sets nothing; and beside a venue split
    on the splits reader the one side is the split's half, not a cut, so
    it is dropped there (``router._route_intent_slots`` until Phase 3, step
    2). A reader that cannot restrict to a venue refuses one rather than
    answering the whole season: "Knicks home record this season" was
    answered 53-29, their overall record."""
    home, away = lexicon.VENUE_HOME.search(question), lexicon.VENUE_AWAY.search(question)
    if (home is None) == (away is None):
        return None, None
    if context.intent == "player_splits" and context.split == "home_away":
        return None, None
    match = home if home is not None else away
    assert match is not None
    return ("home" if home is not None else "away"), Claim(match.start(), match.end(), "venue")


def _situation(question: str) -> tuple[str | None, Claim | None]:
    """The circumstance the words put the games under
    (:data:`~association.query.lexicon.SITUATION`), as worded and casefolded,
    with the characters that said so."""
    match = lexicon.SITUATION.search(question)
    if match is None:
        return None, None
    return match.group(0).casefold(), Claim(match.start(), match.end(), "situation")


def _day(question: str) -> tuple[str | None, Claim | None]:
    """One calendar day as ``YYYY-MM-DD``, or None if the question names none,
    with the characters that named it.

    The year is not in the question and does not need to be, because a
    season fixes it: season Y runs from October of Y-1 through June of Y, so
    October to December belong to ``season - 1`` and January onward to
    ``season`` - this project's own numbering
    (:func:`~association.nba.season.current_season`) applied to a month, not
    a guess. "Desmond bane march 17" against season 2026 is 2026-03-17, and
    ``game_log`` answers it with that game. The season that fixes it is the
    one a reader would use - the season the words name, or the current one,
    the default they all apply - EXCEPT on a career question, which spans
    twenty Octobers and fixes nothing, so that refuses. Reading an absent
    season as "current" rather than "unknown" matters: "Desmond bane march
    17" names none.

    Three things it will not do, each because the answer would be a guess
    rather than a reading: a year the question states wins ("november 11
    2019" is the calendar day, not November of whatever season 2019 resolves
    to); a date that opens a window is not a day ("since January 31st" names
    a range no reader honors, and is a situation); and with no season to fix
    the year on it refuses. A day no calendar has ("february 31") names
    nothing, and is a situation the relation refuses by value.

    .. versionadded:: 6.0.0
       ``router._validate_date`` was this, without the claim.
    """
    match = lexicon.CALENDAR_DATE.search(question)
    if match is None or match.group("range"):
        return None, None
    month, day = lexicon.month_number(match.group("month")), int(match.group("day"))
    stated = match.group("year")
    if stated is not None:
        year = int(stated)
    elif career_named(question)[0]:
        return None, None
    else:
        season = lexicon.season_from_text(question) or current_season()
        year = season - 1 if month >= 10 else season
    try:
        return date(year, month, day).isoformat(), Claim(match.start(), match.end(), "date")
    except ValueError:
        return None, None  # "february 31"


def _dated_situation(question: str) -> tuple[str | None, int | None, Claim | None]:
    """A date that named itself and could not be pinned to one day - a range
    opened on it ("since January 31st", "from 12/25/2019") or a day on a
    career question - as the situation it is, with the year such a range
    starts the seasons at where it wrote one, and the characters read."""
    named = lexicon.CALENDAR_DATE.search(question) or lexicon.NUMERIC_DATE_RANGE.search(question)
    if named is None:
        return None, None, None
    return named.group(0).casefold(), _dated_since(named.groupdict().get("year")), Claim(named.start(), named.end(), "situation")


def _dated_since(year: str | None) -> int | None:
    """The year a range opened on a date WITH a year ("since 1/26/20")
    starts the seasons at: the season labeled that year ends in it, so no
    game on or after the date is in an earlier one, and the date itself
    (the situation) makes the exact cut. Without this the default season
    applied, and "since January 26, 2020" was read inside 2025-26 alone.
    The span tagger takes it (:class:`~association.query.span.SpanContext`),
    and a career or a range the words name stands over it. Two digits the
    way strptime's %y reads them: 69-99 are the 1900s."""
    if not year:
        return None
    stated = int(year)
    return stated if stated > 99 else (1900 + stated if stated >= 69 else 2000 + stated)


def _round(question: str) -> tuple[str | None, Claim | None]:
    """A playoff round as worded (:data:`~association.query.lexicon.ROUND_WORDS`)."""
    match = lexicon.ROUND_WORDS.search(question)
    if match is None:
        return None, None
    return match.group(0).casefold(), Claim(match.start(), match.end(), "round")


def _series_game(question: str) -> tuple[int | None, Claim | None]:
    """One game of each playoff series, by number (:data:`~association.query.lexicon.SERIES_GAME`)."""
    match = lexicon.SERIES_GAME.search(question)
    if match is None:
        return None, None
    return int(match.group(1)), Claim(match.start(), match.end(), "game_n")


def _ordinal_season(question: str) -> tuple[int | None, Claim | None]:
    """A season named by its place in a career (:data:`~association.query.lexicon.ORDINAL_SEASON`)."""
    match = lexicon.ORDINAL_SEASON.search(question)
    if match is None:
        return None, None
    return int(match.group(1)), Claim(match.start(), match.end(), "season_n")


def _opponent(context: CutsContext) -> str | None:
    """The opponent as the subject reading read it - unless it is the
    ``without`` list again, which is no team. "bane game log without
    anthony black and franz wagner this season" (yardstick-v2 F158) arrived
    with both ``without: ['anthony black', 'franz wagner']`` and
    ``opponent: 'Anthony Black, Franz Wagner'``; the second is no team, so
    the log fell through to the agent where the first alone answers it.
    Dropped only when EVERY name in the opponent is one of the teammates -
    a real team beside a without list is kept
    (``router._route_opponent_named_as_teammates`` until Phase 3, step 2)."""
    opponent = context.opponent
    if opponent is None or not opponent.strip():
        return None
    if not context.without:
        return opponent
    absent = {name.casefold().strip() for name in context.without}
    named = [part.casefold().strip() for part in lexicon.NAME_LIST_SPLIT.split(opponent) if part.strip()]
    if named and all(part in absent for part in named):
        return None
    return opponent
