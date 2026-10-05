"""The lines a question keeps games under or over - "under 14 fta", "with
25 minutes", "20+ points" - read from the ``below``/``above`` phrases the
parser kept, as filters on the player-games relation. The reader's: it
reads words, with a regex, and names no SQL; the relation applies what it
reads (``templates.common.narrow_measures``). Lived in ``templates.common``
until Phase 2's first slice moved the default points that read it to the
reader's side (``ROADMAP.md``, "Phase 2, the expected steps", step 1).

.. versionadded:: 5.0.0
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from association.query.measures import MEASURE_WORDS, THRESHOLD_STAT_NAMES
from association.query.reading import Scope, Unsupported

#: How the answer names each column a game was kept under or over.
_MEASURE_LABELS: dict[str, str] = {
    "fieldGoalsAttempted": "field goal attempts",
    "fieldGoalsMade": "field goals made",
    "freeThrowsAttempted": "free throw attempts",
    "freeThrowsMade": "free throws made",
    "threePointFieldGoalsAttempted": "3-point attempts",
    "threePointFieldGoalsMade": "3-pointers",
    "offensiveRebounds": "offensive rebounds",
    "defensiveRebounds": "defensive rebounds",
}


# The number and the words after it in a `below` / `above` phrase. The
# leading words ("under", "at most", "with") say which way the line faces.
_MEASURE_PHRASE = re.compile(r"^(?P<lead>.*?)\b(?P<n>\d+)\+?%?\s*(?P<words>.*)$")
_AT_MOST = ("at most", "no more than")
_STRICTLY_BELOW = ("under", "fewer than", "less than", "below")


@dataclass(frozen=True)
class MeasureFilter:
    """One line a question keeps games under or over: the box-score column,
    the comparison (a key of :data:`association.query.player_games.MEASURE_OPS`),
    the number, and how the answer says it.

    .. versionadded:: 4.3.0
    """

    column: str
    op: str
    value: int
    label: str


def _measure_column(words: str) -> str | None:
    """The column the words after a number name - the longest run of them
    that is in MEASURE_WORDS, so "free throw attempts in his career" reads the
    first three words and ignores the rest."""
    tokens = words.casefold().replace("-", " ").split()
    for width in (3, 2, 1):
        candidate = " ".join(tokens[:width])
        if candidate in MEASURE_WORDS:
            return MEASURE_WORDS[candidate]
    return None


def measure_filters(below: Any, above: Any) -> list[MeasureFilter]:
    """The lines a question keeps games under (the ``below`` slot) or over
    (``above``), read from the phrases ``route()`` kept - "under 14 fta",
    "with 25 minutes" - as filters on the ``player_game`` relation.

    The model's own ``stat`` is not consulted: beside "under 14 fta" it said
    ``freeThrowsMade``, the nearest name it knows, so the phrase is the only
    honest carrier of which column was meant. A phrase whose words name no
    column refuses (:class:`~association.query.reading.Unsupported`) rather
    than filtering on a guess - the same rule ``check_scope`` applies to a
    slot nothing honors.

    .. versionadded:: 4.3.0

    .. versionchanged:: 5.0.0
       Lives on the reader's side (``query/lines.py``).
    """
    filters: list[MeasureFilter] = []
    for key, phrases, default_op in (("below", below, "<"), ("above", above, ">=")):
        for phrase in [phrases] if isinstance(phrases, str) else (phrases or []):
            match = _MEASURE_PHRASE.match(str(phrase).strip())
            column = _measure_column(match.group("words")) if match else None
            if match is None or column is None:
                raise Unsupported(f"{phrase!r} names no box-score stat a game can be kept {'under' if key == 'below' else 'over'}")
            lead, words = match.group("lead").strip().casefold(), match.group("words").casefold()
            op = default_op
            if key == "below" and (lead.startswith(_AT_MOST) or " or less" in words):
                op = "<="
            elif key == "below" and not lead.startswith(_STRICTLY_BELOW):
                op = "<"
            value = int(match.group("n"))
            how = {"<": "under", "<=": "at most", ">=": "at least", ">": "over"}[op]
            filters.append(MeasureFilter(column, op, value, f"{how} {value} {_MEASURE_LABELS.get(column, column)}"))
    return filters


def threshold_count_line(scope: Scope) -> tuple[str, int | None]:
    """The box-score column and the threshold a count of games is over;
    raises (:class:`~association.query.reading.Unsupported`) for a stat no
    line is kept on or a threshold that counts every game. A below/above
    phrase carries a line of its own, in which case the count may have no
    threshold at all ("Sga games with under 14 fta" - the phrase IS the
    count, and once nothing asks the model for a threshold on this shape,
    none arrives).

    .. versionadded:: 5.0.0
       On the reader's side, where ``threshold_count``'s default point reads
       it (``templates.players._threshold_count_ask`` was this).
    """
    stat, threshold = scope.stat, scope.threshold
    lined = bool(scope.below or scope.above)
    if lined and (stat is None or stat not in THRESHOLD_STAT_NAMES):
        # No stat from the model at all (under a player_stat parent, "fta"
        # names none of its words), or one no threshold is kept on (the
        # parser's "freeThrowsAttempted", read off the same "fta"): the one
        # line's own column is the count's.
        lines = measure_filters(scope.below, scope.above)
        if len(lines) == 1:
            return lines[0].column, None
    column = stat if stat is not None and stat in THRESHOLD_STAT_NAMES else None
    # A threshold is a whole number or absent (the Scope's own type), and
    # absent is a count only where a below/above phrase carries the line.
    if column is None or (threshold is None and not lined):
        raise Unsupported(f"threshold_count needs a known stat and an integer threshold, got {stat!r}/{threshold!r}")
    if threshold is None:
        return column, None
    if threshold < 1:
        # ">= 0" counts every game, which is never the question: measured, "most 3
        # pointers made since 2020" arrived as threshold 0 and was answered as
        # "the most games with 0+ 3-pointers".
        raise Unsupported(f"a threshold of {threshold} counts every game - not a question threshold_count answers")
    return column, threshold
