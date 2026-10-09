"""The words a season and its type are said with - a season's name, the
season types' names, the months - the sayer's and the relations' vocabulary.

Until Phase 3, step 2 this module also read the season a question names
("last season" is arithmetic on a calendar, not language understanding, and
it was the slot the router most reliably dropped); that reading is the
lexicon's now (:func:`~association.query.lexicon.season_from_text`,
:func:`~association.query.lexicon.season_spans`), beside every other word of
the span family.

.. versionchanged:: 6.0.0
   ``MIN_SEASON``, ``SeasonSpan``, ``season_spans`` and ``season_from_text``
   moved to :mod:`association.query.lexicon`.
"""

from __future__ import annotations

MONTH_NAMES: tuple[str, ...] = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December")
"""The months by name, January first - the words a question narrows a
season to a month by (``calendar``), and the label a monthly split prints.
On the reader's side since 2026-10-03: ``calendar`` took it from
``conditions``, which loads the team-games relation, so every reader module
loaded the answer side through one tuple of names.

.. versionadded:: 5.0.0
"""

# Named in every answer, so answering the wrong one is visible rather than silent.
# `0` is `player_games.BOTH_SEASON_TYPES` - never a real value a row carries,
# only a question that asked for both at once ("including the playoffs"). One
# entry here means every existing `SEASON_TYPE_NAMES.get(season_type, ...)`
# call site names it correctly with no further change.
SEASON_TYPE_NAMES = {0: "regular season and postseason", 1: "preseason", 2: "regular season", 3: "postseason"}


def season_phrase(season: int, season_type: int) -> str:
    """``"2026 regular season"``: a season and its type as an answer names them.

    .. versionadded:: 5.0.0
       Public, as the relation's shared step.
    """
    return f"{season} {SEASON_TYPE_NAMES.get(season_type, 'regular season')}"


def season_label(season: int) -> str:
    """1994 -> "1993-94": seasons are named for the year they end in."""
    return f"{season - 1}-{season % 100:02d}"
