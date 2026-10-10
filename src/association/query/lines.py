"""The lines a question keeps games under or over - "under 14 fta", "with
25 minutes", "20+ points" - as the relation reads the typed
:class:`~association.query.reading.Line`: the phrase reader every line kept
as words goes through (:func:`phrase_line`), the lines the relation narrows
by with how the answer says each (:func:`measure_filters`), and the one line
a count, a record, a streak or a high is keyed on (:func:`threshold_line`,
:func:`threshold_count_line`). The reader's: it reads words, from the
lexicon, and names no SQL; the relation applies what it reads
(``player_relation.narrow_measures``). Lived in ``templates.common`` until
Phase 2's first slice moved the default points that read it to the
reader's side (``ROADMAP.md``, "Phase 2, the expected steps", step 1).

.. versionadded:: 5.0.0

.. versionchanged:: 6.0.0
   Reads the typed lines (Phase 3, step 2): the ``below``/``above`` phrases
   and the ``threshold`` slot are gone.
"""

from __future__ import annotations

from dataclasses import dataclass

from association.query import lexicon
from association.query.measure import spelled
from association.query.measures import THRESHOLD_STAT_NAMES
from association.query.reading import Cause, Line, LineOp, PointRefused, Scope

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


def measure_column(words: str) -> str | None:
    """The column the words after a number name - the longest run of them
    that is in :data:`~association.query.lexicon.MEASURE_WORDS`, so "free
    throw attempts in his career" reads the first three words and ignores
    the rest.

    .. versionadded:: 6.0.0
       Public: the lines tagger reads a line's measure through it.
    """
    tokens = words.casefold().replace("-", " ").split()
    for width in (3, 2, 1):
        candidate = " ".join(tokens[:width])
        if candidate in lexicon.MEASURE_WORDS:
            return lexicon.MEASURE_WORDS[candidate]
    return None


def phrase_line(phrase: str, *, below: bool) -> Line:
    """A below/above phrase as the line its words read as - "under 14 fta",
    "at most 5 turnovers", "with 25 minutes", "30 minutes or more" - the
    number, the column its words name (None where they name none, which the
    relation refuses by the words: :func:`measure_filters`), and which way
    the line faces: ``below`` reads "at most"/"no more than"/"or less" as at
    or under, anything else as under; a phrase kept over is at or above.

    .. versionadded:: 6.0.0
    """
    text = phrase.strip().casefold()
    match = lexicon.MEASURE_PHRASE.match(text)
    if match is None:
        return Line(measure=None, op="<" if below else ">=", value=0, as_typed=text, narrows=True)
    lead, words = match.group("lead").strip(), match.group("words")
    op: LineOp = ">="
    if below:
        op = "<=" if lead.startswith(lexicon.AT_MOST_LEADS) or " or less" in words else "<"
    return Line(measure=measure_column(words), op=op, value=int(match.group("n")), as_typed=text, narrows=True)


def relation_lines(scope: Scope) -> list[Line]:
    """The subject's lines the RELATION narrows the games by
    (:attr:`~association.query.reading.Line.narrows`), in the order the slots
    applied them: the lines kept under a number, then the floors of minutes
    (and any other line kept over a number), then the "N+ stat" pairs. The
    line the shape is keyed on (:func:`threshold_line`) is read into the
    point's predicate, not here.

    .. versionadded:: 6.0.0
    """
    narrowing = [line for line in scope.lines if line.narrows and line.period is None]
    return [line for line in narrowing if line.below] + [line for line in narrowing if line.op == ">=" and not line.pair] + [line for line in narrowing if line.pair]


def measure_filters(scope: Scope) -> list[MeasureFilter]:
    """The lines the relation narrows ``scope``'s games by
    (:func:`relation_lines`) as filters on the ``player_game`` relation,
    each with how the answer says it.

    A line whose words name no column refuses
    (:class:`~association.query.reading.PointRefused`, a decline to the
    answer side, a cause to the point reader) rather than filtering on a
    guess - the same rule the planner applies to a slot nothing honors.

    .. versionadded:: 4.3.0

    .. versionchanged:: 5.0.0
       Lives on the reader's side (``query/lines.py``).

    .. versionchanged:: 6.0.0
       Takes the Scope and reads its typed lines; the ``below``/``above``
       phrases are gone.
    """
    filters: list[MeasureFilter] = []
    for line in relation_lines(scope):
        if line.measure is None:
            side = "under" if line.below else "over"
            raise PointRefused(Cause(kind="line_names_no_stat", facts={"phrase": line.as_typed, "side": side}), f"{line.as_typed!r} names no box-score stat a game can be kept {side}")
        how = {"<": "under", "<=": "at most", ">=": "at least", ">": "over", "=": "exactly"}[line.op]
        value = int(line.value)
        filters.append(MeasureFilter(line.measure, line.op, value, f"{how} {value} {_MEASURE_LABELS.get(line.measure, line.measure)}"))
    return filters


def threshold_line(scope: Scope) -> Line | None:
    """The one line a count, a record, a streak or a high is keyed on
    (:attr:`~association.query.reading.Line.keyed`: "30+ points", "scores
    30", "fouled out"; the first of two or more pairs, which the count reads
    as the pairs' own) - or None. What the ``threshold`` slot carried.

    .. versionadded:: 6.0.0
    """
    return next((line for line in scope.lines if line.keyed and line.period is None), None)


def threshold_of(scope: Scope) -> int | None:
    """The number of :func:`threshold_line`, or None.

    .. versionadded:: 6.0.0
    """
    line = threshold_line(scope)
    return None if line is None else int(line.value)


def threshold_count_line(scope: Scope) -> tuple[str, int | None]:
    """The box-score column and the threshold a count of games is over;
    raises (:class:`~association.query.reading.PointRefused`, by the missing fact) for a stat no
    line is kept on or a threshold that counts every game. A below/above
    phrase carries a line of its own, in which case the count may have no
    threshold at all ("Sga games with under 14 fta" - the phrase IS the
    count, and once nothing asks the model for a threshold on this shape,
    none arrives).

    .. versionadded:: 5.0.0
       On the reader's side, where ``threshold_count``'s default point reads
       it (``templates.players._threshold_count_ask`` was this).
    """
    stat, threshold = spelled(scope.measure), threshold_of(scope)
    lined = bool(relation_lines(scope))
    if lined and (stat is None or stat not in THRESHOLD_STAT_NAMES):
        # No stat from the model at all (under a player_stat parent, "fta"
        # names none of its words), or one no threshold is kept on (the
        # parser's "freeThrowsAttempted", read off the same "fta"): the one
        # line's own column is the count's.
        lines = measure_filters(scope)
        if len(lines) == 1:
            return lines[0].column, None
    column = stat if stat is not None and stat in THRESHOLD_STAT_NAMES else None
    # A threshold is a whole number or absent (the Scope's own type), and
    # absent is a count only where a below/above phrase carries the line.
    if column is None or (threshold is None and not lined):
        message = f"threshold_count needs a known stat and an integer threshold, got {stat!r}/{threshold!r}"
        if column is None and stat is not None and stat.strip():
            raise PointRefused(Cause(kind="unknown_stat", facts={"intent": "threshold_count", "stat": stat}), message)
        if column is None and threshold is not None:
            raise PointRefused(Cause(kind="threshold_needs_stat", facts={"intent": "threshold_count", "threshold": threshold}), message)
        if column is None:
            raise PointRefused(Cause(kind="needs_stat", facts={"intent": "threshold_count"}), message)
        raise PointRefused(Cause(kind="needs_threshold", facts={"intent": "threshold_count", "stat": column}), message)
    if threshold is None:
        return column, None
    if threshold < 1:
        # ">= 0" counts every game, which is never the question: measured, "most 3
        # pointers made since 2020" arrived as threshold 0 and was answered as
        # "the most games with 0+ 3-pointers".
        raise PointRefused(
            Cause(kind="threshold_counts_every_game", facts={"intent": "threshold_count", "threshold": threshold}),
            f"a threshold of {threshold} counts every game - not a question threshold_count answers",
        )
    return column, threshold
