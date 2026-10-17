"""The period tagger: the one reader of what a read SEES of each game
(:class:`~association.query.reading.Period`) - one quarter ("1st quarter",
"q1", "4th qtr") or one half ("first half", "2h") - from the lexicon's words
(:mod:`association.query.lexicon`) alone - whether the reader takes it is
the key's reading (:func:`~association.query.point.period_under`). It claims the characters it read
(:class:`~association.query.reading.Claim`), once.

One writer of one value: it reads which period the words name wherever
they name one (:func:`which_period`, which the grammar's rules
(``grammar._period``) and the line family's condition reader call too, so
no second copy of "first | second | 1st | 2nd" is written), and keeps the
value under a reader that takes one
(:data:`~association.query.point.PERIOD_ASKS`, the key's reading,
:func:`~association.query.point.period_under`). Measured on the 2,710
readings (``~/association-research/stages/period_family.py``): 120 carry
one (79 quarters, 41 halves: 82 ``PERIOD_LOG``, 23 ``PERIOD_RANKING``, 15
``TEAM_PERIOD_TOTAL``); six name a quarter on a shape that takes none,
their subject unread, and the point reader declines them ("a quarter or
half is the period relation's question").

A "by quarter" question reads NO period: the breakdown across all four is
the point's shape (``point._default_period_split``, ``by`` ``"period"`` in ``Reading.read_as``),
not a filter. A period as a CONDITION on which games count ("after making
one three in the first quarter") is the line family's value
(:func:`~association.query.line.read_period_line`), whose words the parser
blanks before the grammar reads the question.

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


def respelled(question: str) -> str | None:
    """The near spelling of "quarter" the quarter ``question`` names is
    read by (:data:`~association.query.lexicon.QUARTER_NEAR_SPELLINGS`:
    "first quater"), as typed - or None where the words spell it out, or
    name a half (read first, as :func:`which_period` reads it).

    .. versionadded:: 7.0.0
    """
    if lexicon.WHICH_HALF.search(question) is not None:
        return None
    quarter = lexicon.WHICH_QUARTER.search(question)
    return quarter.group("near") if quarter is not None else None


def periods_together(question: str) -> str | None:
    """The words naming quarters together ("excluding 4th quarter",
    "1-3rd quarter"; :data:`~association.query.lexicon.PERIODS_TOGETHER`),
    or None: a read sees one quarter or one half of each game, so the
    parser refuses them by name (``periods_together``) rather than read the
    one quarter in them.

    .. versionadded:: 7.0.0
    """
    match = lexicon.PERIODS_TOGETHER.search(question)
    return None if match is None else match.group(0)


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
