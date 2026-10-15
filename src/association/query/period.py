"""The period tagger: the one reader of what a read SEES of each game
(:class:`~association.query.reading.Period`) - one quarter ("1st quarter",
"q1", "4th qtr") or one half ("first half", "2h") - from the lexicon's words
(:mod:`association.query.lexicon`) alone - whether the reader takes it is
the key's reading (:func:`~association.query.point.period_under`). It claims the characters it read
(:class:`~association.query.reading.Claim`), once.

Phase 3, step 2's fourth slice: until it, ``router._period_asked`` read
which period the words name, the intent stage wrote it as the slots
``period`` and ``half`` under the two player-side period intents and a
team's half (``_route_period_intents_choose``, ``raw |= asked``), and the
parser wrote a team's quarter after the stages where they had left none
(``parse._read_route_period``) - three writers of one value. Measured
first (``~/association-research/stages/period_family.py``, the two slots
as each stage set them on all 2,710 readings): 120 readings carry one (79
quarters, 41 halves: 82 ``PERIOD_LOG``, 23 ``PERIOD_RANKING``, 15
``TEAM_PERIOD_TOTAL``), no stage moved one between the route and the
reading, no period word fell inside another tagger's claim, and the value
is written ONLY under the three period intents - six readings name a
quarter and carry none, their subject unread and the point reader
declining them for it ("a quarter or half is the period relation's
question"). The tagger keeps that: it reads which period the words name
wherever they name one (:func:`which_period`, which the grammar's rules
(``grammar._period``) and the line slice's condition reader call too, so no second copy of "first |
second | 1st | 2nd" is written), and keeps the value under a reader that
takes one (:data:`~association.query.point.PERIOD_ASKS`, the key's reading,
:func:`~association.query.point.period_under`).

A "by quarter" question reads NO period: the breakdown across all four is
the point's shape (``point._default_period_split``, ``by`` ``"period"`` in ``Reading.read_as``),
not a filter. A period as a CONDITION on which games count ("after making
one three in the first quarter") is the line slice's value
(``parse.read_period_condition``), whose words the parser takes out of
the question before the stages see it.

.. versionadded:: 6.0.0
"""

from __future__ import annotations

from dataclasses import dataclass

from association.query import lexicon
from association.query.reading import Claim, Period


@dataclass(frozen=True)
class PeriodRead:
    """What the tagger read: the :class:`~association.query.reading.Period`,
    or None for the whole game, and the characters it claimed.

    .. versionadded:: 6.0.0
    """

    period: Period | None
    claims: tuple[Claim, ...]


def which_period(text: str) -> tuple[Period, Claim] | None:
    """The period ``text`` names, with the characters that name it - a half
    first ("first half", "2h"), then a quarter ("1st quarter", "q1", "4th
    qtr"), since a half is never a quarter - or None where it names none:
    "by quarter" and "qtrs" name a breakdown, not one period, and a reader
    of one answers one.

    .. versionadded:: 6.0.0
       ``router._period_asked`` until Phase 3, step 2, which returned the
       slot pair.
    """
    half = lexicon.WHICH_HALF.search(text)
    if half is not None:
        named = half.group("ordinal")
        number = lexicon.ORDINAL_PERIODS[named.lower()] if named else int(half.group("hn"))
        return Period(number=number, half=True), Claim(half.start(), half.end(), "period")
    quarter = lexicon.WHICH_QUARTER.search(text)
    if quarter is None:
        return None
    named = quarter.group("ordinal")
    number = lexicon.ORDINAL_PERIODS[named.lower()] if named is not None else int(quarter.group("qn") or quarter.group("nq"))
    return Period(number=number), Claim(quarter.start(), quarter.end(), "period")


def read_period(question: str) -> PeriodRead:
    """The period ``question``'s words name, with the claim; the whole game
    and no claim where they name none. Whether the reader takes it is the
    key the words ask, decided where the parser writes the Reading
    (:func:`~association.query.point.period_under`).

    .. versionadded:: 6.0.0

    .. versionchanged:: 6.0.0
       Reads no key (Phase 3, step 4, slice (b)): it took a ``PeriodContext``
       and kept the period under ``PERIOD_ASKS`` alone, which
       :func:`~association.query.point.period_under` does now.
    """
    named = which_period(question)
    if named is None:
        return PeriodRead(None, ())
    period, claim = named
    return PeriodRead(period, (claim,))
