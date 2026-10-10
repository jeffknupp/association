"""The lines tagger: the one reader of the lines on a stat a question keeps
the subject's games past (:class:`~association.query.reading.Line`) - "30+
points", "20+ point 5+ assist games", "scores 30", "fouled out", "under 14
fta", "with 25 minutes", "after making one three in the first quarter" -
from the lexicon's words (:mod:`association.query.lexicon`) and the one fact
of the question settled before it: the intent the stages settled. It claims
the characters it read (:class:`~association.query.reading.Claim`), each
once. A line inside a companion's phrase ("sixers record when maxey scores
20+ points") is read here as the subject's too, as the stages read it -
measured, leaving it to the companion moved one recorded reading ("In his
18th season, how many games with 40+ points did Lebron James have?": "with
40+ points did Lebron James have" reads as a companion phrase, the subject
is LeBron himself, restored after the stages, and the count with no line
turned into a ranking) - and the two claims fold into one (the companion's
holds the line's); where the companion is a real one, the subject's own
line reads the same number (ISSUES.md, "A companion's line is read as the
subject's too").

Phase 3, step 2's fifth slice: until it, a line on a stat had FIVE
carriers, each read by different code - the ``threshold`` slot beside the
model's ``stat``, written by ``router._route_threshold`` (the first "N
stat" of the words, or "scores N"), the fouled-out branch of
``_route_period_intents`` (fouls at six), and ``_route_record_when_threshold``
(exactly one "N+ stat" pair, which also rewrote the stat); the ``above``
and ``below`` phrases kept whole by ``_route_filter_slots`` (a floor of
minutes, a line under a number, and two or more "N+ stat" pairs); the
point's own re-reading of the number's words into a predicate
(``point._everyone_threshold_predicates``, ``_numbered_stat_lines``); a
companion's ``reached`` entry; and the ``period_condition`` the parser read
before the stages. Measured first
(``~/association-research/stages/line_family.py``, the five carriers and
the three companion slots as each stage set them on all 2,710 readings): a
threshold on 120, a floor or a line under a number on 39, two or more
pairs on 11 - where the slot pair contradicted the words every time
("20+ point 5+ assist" read ``stat: assists, threshold: 20``, the relation
narrowing by the words and the pair unread) - a line in a quarter on 2, a
companion's line on 23 (every one a team's record, the companion's line
becoming the record's own); the point's re-read differed from the words
on 1 ("most games with 20 pt,s 10 reb, 5 ast": the re-read's pattern could
not pass the comma and fell back to the model's "rebounds", so the point
counted games with 20+ rebounds).

.. versionadded:: 6.0.0
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from association.query import lexicon
from association.query.lines import phrase_line
from association.query.period import which_period
from association.query.reading import GAME_HIGHS, GAMES_COUNTED, LINE_RECORD, LINE_RUNS, PLAYER_LOG, PLAYER_SPLITS, Claim, Line, LineOp, PointShape

THRESHOLD_ASKS: frozenset[PointShape] = frozenset({GAMES_COUNTED, LINE_RECORD, LINE_RUNS, GAME_HIGHS})
"""The readers whose shape is a line on a stat: a count of games over it, a
record above and below it, a run of games holding it, a high ranked by its
stat. On these a bare "30 point games" reads as a line; on every other
reader a line is read only where the words say so outright - a floor of
minutes, a line under a number, two or more "N+ stat" pairs on one game -
as the stages read them (the measurement above: a single "20+ points" on a
log is read by nothing, and stays so until the measure slice).

.. versionadded:: 6.0.0
   ``router._THRESHOLD_ASKS`` until Phase 3, step 2.
"""

#: The readers under which exactly one "N+ stat" pair rewrites the model's
#: ``stat`` to the pair's own word (``router._route_record_when_threshold``
#: until Phase 3, step 2): the count, assigned from the words under a parent
#: whose stat the model filed, and the record.
_PAIR_NAMES_THE_STAT: frozenset[PointShape] = frozenset({LINE_RECORD, GAMES_COUNTED})

#: The readers of a player's games that narrow by ONE line stated outright -
#: "N+ stat", "N or more stat", "at least N stat" - as they narrow by two:
#: "lebron game log with 20+ points" is his 20-point games, where until Phase
#: 3, step 3 the line was read by nothing and every game listed (ISSUES.md
#: #357; the Reading's unread words named its number). A bare "N stat" stays
#: unread there ("harden 61 points" can as well name one game), and a
#: player's line is not among them ("lebron 20+ points this season" can ask
#: whether he averages it).
_ONE_LINE_NARROWS: frozenset[PointShape] = frozenset({PLAYER_LOG, PLAYER_SPLITS})


@dataclass(frozen=True, kw_only=True)
class LineContext:
    """What was settled before the lines are read: what the words ask
    (``asked``, the grammar's key - an intent's name until Phase 3, step 4), as the stages
    settled on, and whether a companion beside the subject reached a line
    (``beside_line``: the line the words state is his, not the subject's).

    .. versionadded:: 6.0.0
    """

    asked: PointShape | None
    beside_line: bool = False


@dataclass(frozen=True)
class LinesRead:
    """What the tagger read: the subject's :class:`~association.query.reading.Line`
    values in the question's order, the characters it claimed, and the
    measure the stages wrote beside a threshold where the words name one
    ("scores 30" is points; one "N+ stat" pair under a count or a record is
    that word's column) - ``None`` where the model's stat stands, until the
    measure slice types the stat itself.

    .. versionadded:: 6.0.0
    """

    lines: tuple[Line, ...]
    claims: tuple[Claim, ...]
    stat: str | None = None


def threshold_named(question: str) -> int | None:
    """The first line the threshold grammar reads in ``question``'s words -
    fouling out (six fouls), else a number with its stat word ("30+ points",
    "15 reb"), else a number after a scoring verb ("scores 30") - or None.
    What the intent stages ask before the lines are read: a count with no
    line is a ranking, a record with none is the with/without split.

    .. versionadded:: 6.0.0
       ``router._threshold_from_text`` until Phase 3, step 2, which read
       fouling out in a stage of its own.
    """
    if lexicon.FOULED_OUT.search(question):
        return lexicon.FOUL_OUT_THRESHOLD
    pairs = lexicon.threshold_pairs(question)
    if pairs:
        return int(pairs[0].group(1))
    scored = lexicon.scored_threshold(question)
    return int(scored.group(1)) if scored is not None else None


def read_lines(question: str, context: LineContext) -> LinesRead:
    """The subject's lines ``question``'s words name, under ``context``, in
    the question's order, with the claims: the "N+ stat" pairs on every
    reader, a bare "N stat", "scores N" and fouling out on a reader whose
    shape is a line (:data:`THRESHOLD_ASKS`), a floor of minutes and a
    line under a number on every reader.

    .. versionadded:: 6.0.0
    """
    found: list[tuple[int, Line, Claim]] = []
    fouled = lexicon.FOULED_OUT.search(question)
    if fouled is not None and context.asked == GAMES_COUNTED:
        # Fouling out is the count's own line (fouls at six), whatever else
        # the words number - the intent stage chose the count by it.
        found.append((-1, Line(measure="fouls", value=lexicon.FOUL_OUT_THRESHOLD, as_typed=fouled.group(0).casefold(), keyed=True), Claim(fouled.start(), fouled.end(), "line")))
    pairs = lexicon.threshold_pairs(question)
    lined = context.asked in THRESHOLD_ASKS
    plus_pairs = [match for match in pairs if lexicon.THRESHOLD_PAIR.fullmatch(match.group(0)) is not None]
    found.extend(_read_lines_pairs(question, context, pairs, plus_pairs))
    stat: str | None = None
    if lined and not pairs:
        scored = lexicon.scored_threshold(question)
        if scored is not None:
            # "scores 30": the verb names the stat, whatever the model filed.
            found.append((scored.start(), Line(measure="points", value=int(scored.group(1)), as_typed=scored.group(0).casefold(), keyed=True), Claim(scored.start(), scored.end(), "line")))
            stat = "points"
    for pattern, below in ((lexicon.ABOVE, False), (lexicon.BELOW, True)):
        found.extend((m.start(), phrase_line(m.group(0), below=below), Claim(m.start(), m.end(), "line")) for m in pattern.finditer(question))
    if context.asked in _PAIR_NAMES_THE_STAT and len(plus_pairs) == 1:
        stat = lexicon.THRESHOLD_WORDS[plus_pairs[0].group(2).casefold()]
    found.sort(key=lambda each: each[0])
    return LinesRead(tuple(line for _, line, _ in found), tuple(claim for _, _, claim in found), stat)


def _read_lines_pairs(question: str, context: LineContext, pairs: list[re.Match[str]], plus_pairs: list[re.Match[str]]) -> list[tuple[int, Line, Claim]]:
    """The "N stat" pairs :func:`read_lines` reads as lines, each with where
    it stands and its claim. On a reader whose shape is a line, every line;
    elsewhere two or more "N+ stat" pairs on one game, which the relation
    narrows by together - one alone was read by nothing there (the stages'
    two rules, measured on the 2,710: a single "10+ point leads" on a team's
    line carried no threshold) - and on a reader whose shape is a line the
    first line of the words is the shape's own (keyed), the pairs among them
    filters as well, and a second bare "N stat" neither. One line stated
    outright - "N+ stat", or "at least N stat" - on a reader of a player's
    games narrows them (:data:`_ONE_LINE_NARROWS`), as two do everywhere;
    never a line a companion beside him reached ("splits when tatum scores
    30+ points" is Tatum's line)."""
    lined = context.asked in THRESHOLD_ASKS
    at_least = [m.span(1) for m in lexicon.AT_LEAST_PAIR.finditer(question)]
    stated = [match for match in pairs if match in plus_pairs or (match.start(), match.start() + len(match.group(1))) in at_least]
    one = context.asked in _ONE_LINE_NARROWS and not context.beside_line and len(pairs) == 1 and len(stated) == 1
    narrowing = stated if one else plus_pairs
    return [
        (
            match.start(),
            Line(
                measure=lexicon.THRESHOLD_WORDS[match.group(2).casefold()],
                value=int(match.group(1)),
                as_typed=match.group(0).casefold(),
                keyed=lined and match is pairs[0],
                narrows=match in narrowing and (len(narrowing) > 1 or one),
            ),
            Claim(match.start(), match.end(), "line"),
        )
        for match in pairs
        if lined or (match in narrowing and (len(narrowing) > 1 or one))
    ]


def read_period_line(question: str) -> tuple[Line, Claim] | None:
    """The quarter or half a question uses as a condition on which games
    count, as a :class:`~association.query.reading.Line` in that period
    with the claim of the words that said it - or None where the question
    uses none, or words one whose stat the period's line does not rebuild
    (:data:`~association.query.measures.PERIOD_COLUMNS`; the reading names
    that by its ``period_as_condition`` cause). Read by the parser before
    the stages, which blanks its words so the period is not read as the
    one measured. "At least N", "N+", "N or more" and "a"/"an" are at-least
    lines; a bare number ("one three", "10 points") is exactly that many -
    the reading yardstick-v2 F062's key takes (31 games with exactly one
    first-quarter three, not the 36 with one or more) - and the answer says
    "exactly", so the other reading is one word away (Jeff's rule: a
    visible default that can be corrected).

    .. versionadded:: 6.0.0
       ``parse.read_period_condition`` until Phase 3, step 2, which
       returned a ``PeriodCondition``.
    """
    from association.query.measures import PERIOD_COLUMNS  # the columns a period's line rebuilds; `measures` imports the reading's types

    match = lexicon.PERIOD_CONDITION.search(question)
    if match is None:
        return None
    number = match.group("n").lower()
    value = int(number) if number.isdigit() else lexicon.CONDITION_NUMBERS[number]
    words = re.sub(r"\s+", " ", match.group("stat").lower().strip())
    stat = lexicon.CONDITION_STAT_WORDS.get(words) or lexicon.MEASURE_WORDS.get(words) or lexicon.MEASURE_WORDS.get(f"{words}s")
    if stat is None or stat not in PERIOD_COLUMNS or value < 1:
        return None
    asked = which_period(match.group("period"))
    if asked is None:
        return None
    op: LineOp = ">=" if match.group("least") or match.group("more") or number in ("a", "an") else "="
    return Line(measure=stat, op=op, value=value, period=asked[0], as_typed=match.group(0).casefold()), Claim(match.start(), match.end(), "line")
