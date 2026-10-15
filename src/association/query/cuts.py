"""The cuts tagger: the one reader of which games of a span a read sees
(:class:`~association.query.reading.Cuts`) - a venue, a circumstance (a
weekday, a month, a holiday, "since <day>", the opponent's conference or
division, or words nothing narrows by), one calendar day, a playoff round, a
game of a series, an ordinal season - from the lexicon's words
(:mod:`association.query.lexicon`) and the facts of the question read
before it: the opponent the subject reading read and the teammates it read
as absent (:class:`CutsContext`; what the words ask is the key's reading,
:func:`~association.query.point.cuts_under`, since Phase 3, step 4, slice
(b)). The opponent is the subject reading's word (``subject.read_subject``,
the one reader of who stands against the subject) and the tenure is written
by it after the taggers (``subject._apply_own_team``): the tagger takes the first and claims
nothing for either. It claims the characters it read
(:class:`~association.query.reading.Claim`), each once.

Phase 3, step 2's third slice: until it, the filter stage of
``router.py`` wrote the venue and the situation
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
from association.query.calendar import date_range, month_opened, season_of_day
from association.query.reading import Claim, Cuts, PointShape, situation_of
from association.query.span import career_named


@dataclass(frozen=True, kw_only=True)
class CutsContext:
    """What was read before the cuts are, and the tagger's rules read
    beside the words: the opponent as the subject reading read
    it, and the teammates it read as absent (an "opponent" that is the
    ``without`` list again is no team). What the words ask is no context
    since Phase 3, step 4, slice (b): a venue beside the splits reader's
    venue split is dropped by the key's reading
    (:func:`~association.query.point.cuts_under`).

    .. versionadded:: 6.0.0
    """

    opponent: str | None = None
    without: tuple[str, ...] = ()


@dataclass(frozen=True)
class CutsRead:
    """What the tagger read: the :class:`~association.query.reading.Cuts`,
    the characters it claimed, and the year a range opened on a dated day
    names ("since 1/26/20"), which the span tagger starts the seasons at -
    with, for a range of calendar days with their years, the season it ends
    in.

    .. versionadded:: 6.0.0
    """

    cuts: Cuts
    claims: tuple[Claim, ...]
    dated_since: int | None = None
    #: The last season a range of calendar days with their years reaches
    #: ("from january 1 2015 to june 2 2021" ends in 2021), which the span
    #: tagger ends the seasons at (ISSUES.md #345).
    dated_until: int | None = None
    #: Whether that range can hold a postseason game - it crosses from one
    #: season into the next, or a day of it falls from April to September -
    #: so naming no season type it is every game between its days.
    dated_both: bool = False
    #: The venue's characters, apart from :attr:`claims` until the key's
    #: reading keeps the venue or drops it (:func:`~association.query.point.cuts_under`).
    venue_claim: Claim | None = None

    def unread_situation(self) -> tuple[Claim, ...]:
        """Where a situation nothing reads was read from ("vs top 3 teams",
        "after a playoff loss": no calendar narrowing and no alignment), which
        the relation refuses by value - and which no other family reads a
        value of: the window tagger reads the question with these words
        blanked, so "vs top 3 teams" is no window of his last 3 games
        (ISSUES.md #400).

        .. versionadded:: 6.0.0
        """
        situation = self.cuts.situation
        if situation is None or situation.read:
            return ()
        return tuple(c for c in self.claims if c.what == "situation")

    def dated(self, question: str) -> tuple[Claim, ...]:
        """Where a day written in numbers was read from - a range opened on
        one ("since 1/26/20", a situation with the day in it) or one day
        ("on 2026-01-02", "11/29/24") - which the parent grammar asks for
        where it stands (``parse.grammar_text``). A worded calendar day, a
        month and a weekday stay words: the log row reads a month's name
        itself, as it did.

        .. versionadded:: 6.0.0
           ``parse._date_spans`` until Phase 4, step 6.

        .. versionchanged:: 6.0.0
           A day written in numbers, ISO included, as well as a range opened
           on one (ISSUES.md #323).
        """
        return tuple(c for c in self.claims if c.what in ("situation", "date") and lexicon.NUMERIC_DAY_TEXT.search(question[c.start : c.end]) is not None)


def read_cuts(question: str, context: CutsContext) -> CutsRead:
    """The cuts ``question``'s words name, under ``context``.

    .. versionadded:: 6.0.0
    """
    claims: list[Claim] = []
    venue, venue_claim = _venue(question)
    dated_since = dated_until = None
    dated_both = False
    situation: str | None
    situation_claim: Claim | None
    ranged = _date_range(question)
    if ranged is not None:
        # A range of calendar days with their years is the situation, read
        # whole, and the seasons its two days fall in are the span's: its
        # first day is no single game, and no "since <day>" within a season
        # (ISSUES.md #345).
        situation, dated_since, dated_until, dated_both, situation_claim = ranged
        day, day_claim = None, None
    else:
        situation, situation_claim = _situation(question)
        day, day_claim = _day(question)
        dated = _dated_day(question, situation_claim) or _dated_month(question, situation_claim)
        if dated is not None:
            # A range opened on a worded day with its year is that day on,
            # across seasons, as the numeric form is (ISSUES.md #353), and
            # one opened on a month with its year the month's first day on:
            # the situation is its words whole, and the season the day falls
            # in opens the span.
            situation, dated_since, situation_claim = dated
    if ranged is None and day_claim is None and situation is None:
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
    return CutsRead(cuts, tuple(claims), dated_since, dated_until=dated_until, dated_both=dated_both, venue_claim=venue_claim)


def own_alignment(asked: PointShape | None, cuts: Cuts) -> str | None:
    """The words of a conference or division a RANKING's subject belongs to
    - "top scorers in the western conference", "who leads the east",
    "eastern conference players points leaders" (ISSUES.md #339) - or None.
    The alignment reader reads every conference or division as the
    opponent's, which a ranking with no opponent word is not: "top scorers
    in the western conference" ranked every player's games against the
    West. Nothing ranks a conference's own players or teams yet, so the
    parser refuses it by a cause naming the words (``own_alignment``); one
    led by "vs", "versus" or "against" stays the opponent's, and off a
    ranking the words keep their reading.

    .. versionadded:: 6.0.0
    """
    situation = cuts.situation
    if asked is None or asked.shape != "ranking" or situation is None or situation.alignment is None:
        return None
    if lexicon.ALIGNMENT_AS_OPPONENT.match(situation.text):
        return None
    return situation.text


def _venue(question: str) -> tuple[Literal["home", "away"] | None, Claim | None]:
    """ "home" or "away" when the question restricts itself to one of them,
    with the characters that said so. Both at once is a SPLIT ("home and
    away splits"), not a cut, so it sets nothing; and beside a venue split
    on the splits reader the one side is the split's half, not a cut, which
    the key's reading drops (:func:`~association.query.point.cuts_under`;
    ``router._route_intent_slots`` until Phase 3, step 2). A reader that cannot restrict to a venue refuses one rather than
    answering the whole season: "Knicks home record this season" was
    answered 53-29, their overall record."""
    home, away = lexicon.VENUE_HOME.search(question), lexicon.VENUE_AWAY.search(question)
    if (home is None) == (away is None):
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
    ``PLAYER_LOG`` answers it with that game. The season that fixes it is the
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
    if match is None:
        return _numeric_day(question)
    if match.group("range"):
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


def _numeric_day(question: str) -> tuple[str | None, Claim | None]:
    """One calendar day written in numbers with its year
    (:data:`~association.query.lexicon.NUMERIC_DAY`: "on 2026-01-02",
    "11/29/24", "11/28/2025") as ``YYYY-MM-DD``, with the characters that
    named it - the year written, so no season fixes it (ISSUES.md #323).
    None where the words name none, where a range word opens it ("prior to
    02/03/2024" is a range, a situation), or where no calendar has the day
    ("2/30/2024")."""
    match = lexicon.NUMERIC_DAY.search(question)
    if match is None or match.group("range"):
        return None, None
    if match.group("iso_year") is not None:
        year, month, day = int(match.group("iso_year")), int(match.group("iso_month")), int(match.group("iso_day"))
    else:
        year, month, day = _dated_since(match.group("year")) or 0, int(match.group("month")), int(match.group("day"))
    try:
        return date(year, month, day).isoformat(), Claim(match.start(), match.end(), "date")
    except ValueError:
        return None, None  # "2/30/2024"


def _dated_day(question: str, situation: Claim | None) -> tuple[str, int, Claim] | None:
    """A range opened on a worded day with its year ("since january 31st
    2020", "after Dec 11 2023": :data:`~association.query.lexicon.CALENDAR_DATE`
    with a lead of "since", "from" or "after" and a year) as the situation
    it is, with the season the day falls in and the characters read - where
    the circumstance the words otherwise name is that day's own words (a
    weekday or a conference elsewhere in the question stays the situation).
    None where the words name no such range."""
    match = lexicon.CALENDAR_DATE.search(question)
    if match is None or match.group("year") is None or not match.group("range"):
        return None
    if match.group("range").split()[0].casefold() not in ("since", "from", "after"):
        return None
    if situation is not None and not (match.start() <= situation.start and situation.end <= match.end()):
        return None
    try:
        day = date(int(match.group("year")), lexicon.month_number(match.group("month")), int(match.group("day")))
    except ValueError:
        return None  # "february 31 2020", refused by value as before
    return match.group(0).casefold(), season_of_day(day), Claim(match.start(), match.end(), "situation")


def _dated_month(question: str, situation: Claim | None) -> tuple[str, int, Claim] | None:
    """A range opened on a month with its year ("since january 2021", "from
    oct 2019": :data:`~association.query.lexicon.SINCE_MONTH_YEAR`) as the
    situation it is, with the season its first day falls in and the
    characters read - where the circumstance the words otherwise name is
    that month's own word (a weekday elsewhere stays the situation). None
    where the words name no such range."""
    match = lexicon.SINCE_MONTH_YEAR.search(question)
    if match is None:
        return None
    if situation is not None and not (match.start() <= situation.start and situation.end <= match.end()):
        return None
    first = month_opened(int(match.group("year")), lexicon.month_number(match.group("month")), after=match.group("lead").casefold() == "after")
    return match.group(0).casefold(), season_of_day(first), Claim(match.start(), match.end(), "situation")


def _date_range(question: str) -> tuple[str, int, int, bool, Claim] | None:
    """A range of calendar days with their years
    (:data:`~association.query.lexicon.DATE_RANGE`) as the situation it is -
    its words, casefolded, which :func:`~association.query.calendar.parse_situation`
    reads back as one ``date_range`` cut across seasons - with the seasons
    its first and last day fall in, whether it can hold a postseason game
    (it crosses a season, or a day of it falls from April to September),
    and the characters read; or None where the words name no such range
    ("march 3 and april 4 2024" is two days)."""
    match = lexicon.DATE_RANGE.search(question)
    if match is None:
        return None
    days = date_range(match.groupdict())
    if days is None:
        return None
    first, last = days
    postseason = season_of_day(first) != season_of_day(last) or any(4 <= day.month <= 9 for day in days)
    return match.group(0).casefold(), season_of_day(first), season_of_day(last), postseason, Claim(match.start(), match.end(), "situation")


def _dated_situation(question: str) -> tuple[str | None, int | None, Claim | None]:
    """A date that named itself and could not be pinned to one day - a range
    opened on it ("since January 31st", "from 12/25/2019") or a day on a
    career question - as the situation it is, with the year such a range
    starts the seasons at where it wrote one, and the characters read."""
    named = lexicon.CALENDAR_DATE.search(question) or lexicon.NUMERIC_DATE_RANGE.search(question)
    if named is None:
        # A range opened on a day written in numbers by a word the range
        # pattern does not read ("before 02/03/2024", "prior to
        # 2024-02-03"), or a day no calendar has ("2/30/2024"), is a
        # situation the relation refuses by value, as the worded "before
        # february 3 2024" and "february 31" are - never the one day, and
        # never nothing (ISSUES.md #323).
        named = lexicon.NUMERIC_DAY.search(question)
        if named is None:
            return None, None, None
    groups = named.groupdict()
    return named.group(0).casefold(), _dated_since(groups.get("year") or groups.get("iso_year")), Claim(named.start(), named.end(), "situation")


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
    return int(year) if len(year) > 2 else lexicon.short_year(year)


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
