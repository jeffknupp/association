"""The lines tagger: the one reader of the lines on a stat a question keeps
the subject's games past (:class:`~association.query.reading.Line`) - "30+
points", "20+ point 5+ assist games", "scores 30", "fouled out", "under 14
fta", "with 25 minutes", "after making one three in the first quarter" -
from the lexicon's words (:mod:`association.query.lexicon`) alone: which of
the lines it reads are the subject's under the key the words ask is the
point reader's (:func:`~association.query.point.lines_under`, since Phase 3,
step 4, slice (b)). It claims the characters it read (:class:`~association.query.reading.Claim`), each
once. A line inside a companion's phrase ("sixers record when maxey scores
20+ points") is read here as the subject's too, as the router's stages read it -
measured, leaving it to the companion moved one recorded reading ("In his
18th season, how many games with 40+ points did Lebron James have?": "with
40+ points did Lebron James have" reads as a companion phrase, the subject
is LeBron himself, restored by the grammar's rules, and the count with no line
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

from dataclasses import dataclass
from typing import Literal

from association.query import lexicon
from association.query.lines import measure_column, phrase_line
from association.query.period import which_period
from association.query.reading import Claim, Line, LineOp


@dataclass(frozen=True)
class LineCandidate:
    """One line the words state, read with no key: the line (a pair's
    ``keyed`` and ``narrows`` unset - which the key the words ask decides,
    :func:`~association.query.point.lines_under`), where it stands, its
    claim, and how the words state it: ``fouled`` (fouling out: fouls at
    six), ``plus`` ("N+ stat", "N plus stat", "N or more stat"), ``at_least``
    ("at least N stat"), ``bare`` ("N stat"), ``scored`` ("scores N", a
    line on points), ``phrase`` (a floor of minutes or a line under a
    number, :func:`~association.query.lines.phrase_line`), ``stated`` (a
    line over or at a number on a stat, stated by its lead: "more than 40
    points", "exactly 25 fga", "fga = 25").

    .. versionadded:: 6.0.0
    """

    line: Line
    start: int
    claim: Claim
    how: Literal["fouled", "plus", "at_least", "bare", "scored", "phrase", "stated"]
    #: Where the line's number stands, with its plus where the words state
    #: one ("20+", "20 or more"), and how the number is stated (``plus``,
    #: ``at_least`` or ``bare``) - what the choosers read in place of the
    #: words (:class:`LinesNamed`); None for fouling out, which states none.
    number: tuple[int, int, Literal["plus", "at_least", "bare"]] | None = None
    #: Where the line's stat words stand after its number ("points" of
    #: "20+ points", "fta" of "under 14 fta"), or None where the words
    #: state none ("scores 30", fouling out).
    stat: tuple[int, int] | None = None
    #: Where the words that state a line under a number stand before it
    #: ("under", "fewer than"), or None.
    lead: tuple[int, int] | None = None


@dataclass(frozen=True)
class LineCandidates:
    """Every line the words state (:class:`LineCandidate`), in the
    question's order, before the key the words ask decides which are the
    subject's lines (:func:`~association.query.point.lines_under`).

    .. versionadded:: 6.0.0
    """

    found: tuple[LineCandidate, ...]

    def of(self, *how: str) -> list[LineCandidate]:
        """The candidates stated each of the ways ``how`` names, in order."""
        return [candidate for candidate in self.found if candidate.how in how]


@dataclass(frozen=True)
class LinesRead:
    """The lines the subject's games are kept past, under the key the words
    ask: the :class:`~association.query.reading.Line` values in the
    question's order, the characters read, and the measure a line names
    beside a threshold where the words name one ("scores 30" is points; one
    "N+ stat" pair under a count or a record is that word's column) -
    ``None`` where the model's stat stands.

    .. versionadded:: 6.0.0
    """

    lines: tuple[Line, ...]
    claims: tuple[Claim, ...]
    stat: str | None = None


@dataclass(frozen=True)
class LinesNamed:
    """What the choosers - the grammars and the grammar's rules - read of
    the lines the words state, as typed values, never as words: the first
    line's number (:func:`threshold_named`: a count with no line is a
    ranking), whether a line under a number stands (a count stated as a
    ceiling keeps its key), and where each line's number and stat stand
    (``marks``: ``(start, end, how)``, typed over the question in the
    grammar's text, :class:`~association.query.lexicon.GrammarText`).

    .. versionadded:: 6.0.0
    """

    threshold: int | None
    below: bool
    marks: tuple[tuple[int, int, str], ...]
    stats: tuple[tuple[int, int], ...] = ()

    def numbered(self, question: str) -> lexicon.GrammarText:
        """``question`` with each line's number typed - what the parent
        grammar and the grammar's rules read, which read a stat word as a
        measure's (a team ranking is no player's points).

        .. versionchanged:: 6.0.0
           The grammar's text, where it was the question with each number
           written over by a mark (Phase 4, step 6).
        """
        return lexicon.GrammarText.of(question).typed_over(self._typed(tuple(mark for mark in self.marks if mark[2] != "lead")))

    def marked(self, question: str) -> lexicon.GrammarText:
        """``question`` with each line typed whole - its number, its stat
        and the words that lead a line under a number - what the child
        grammars read: a count of "30+ point games" is the count's words
        around a line, and the line is the lines tagger's.

        .. versionchanged:: 6.0.0
           The grammar's text, where it was the question with each line
           written over by marks (Phase 4, step 6).
        """
        return lexicon.GrammarText.of(question).typed_over((*self._typed(self.marks), *((start, end, "line_stat") for start, end in self.stats)))

    @staticmethod
    def _typed(marks: tuple[tuple[int, int, str], ...]) -> tuple[tuple[int, int, lexicon.TypedKind], ...]:
        """Each line's number or lead as the stretch typed over the question,
        by how the words state it - a line inside another (a bare "14 fta"
        inside "under 14 fta") typed as the plainer one first and the stated
        one over it."""
        return tuple((start, end, _LINE_KINDS.get(how, "line_number")) for start, end, how in sorted(marks, key=lambda mark: mark[2] != "bare"))


#: What each way of stating a line's number, and the words that lead a line
#: under a number, is typed as in the grammar's text.
_LINE_KINDS: dict[str, lexicon.TypedKind] = {"plus": "line_plus", "at_least": "line_at_least", "lead": "line_lead"}


def lines_named(question: str) -> LinesNamed:
    """The lines ``question``'s words state, as the choosers read them
    (:class:`LinesNamed`): the number of each line the tagger reads
    (:func:`read_lines`), with its plus where the words state one - the
    stat word and the words before the number ("with", "under", "at
    least", "scores") stay words a grammar may read, as it reads a measure
    by them - and fouling out, which states no number, not marked.

    .. versionadded:: 6.0.0
    """
    found = read_lines(question).found
    marks = (*(candidate.number for candidate in found if candidate.number is not None), *((*candidate.lead, "lead") for candidate in found if candidate.lead is not None))
    stats = tuple(candidate.stat for candidate in found if candidate.stat is not None)
    return LinesNamed(threshold_named(question), lexicon.BELOW.search(question) is not None, marks, stats)


def threshold_named(question: str) -> int | None:
    """The first line the threshold grammar reads in ``question``'s words -
    fouling out (six fouls), else a number with its stat word ("30+ points",
    "15 reb"), else a number after a scoring verb ("scores 30") - or None.
    What the grammar's rules ask before the lines are read: a count with no
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


def read_lines(question: str) -> LineCandidates:
    """Every line ``question``'s words state, read from the words alone:
    fouling out, each "N stat" pair (stated with a plus, an "at least", or
    bare), a number after a scoring verb where the words hold no pair, and
    a floor of minutes or a line under a number. Which of them are the
    subject's - and which is the shape's own line, and which narrow its
    games - is the key the words ask, decided where the parser writes the
    Reading (:func:`~association.query.point.lines_under`).

    .. versionchanged:: 6.0.0
       Reads no key (Phase 3, step 4, slice (b)): it took a ``LineContext``
       holding what the stages settled the words to ask, and chose the lines
       by it (``THRESHOLD_ASKS``, the count's fouling out, the pair that
       names the stat, the one stated line on a log), which
       :func:`~association.query.point.lines_under` does now.
    """
    found: list[LineCandidate] = []
    fouled = lexicon.FOULED_OUT.search(question)
    if fouled is not None:
        found.append(LineCandidate(Line(measure="fouls", value=lexicon.FOUL_OUT_THRESHOLD, as_typed=fouled.group(0).casefold(), keyed=True), -1, Claim(fouled.start(), fouled.end(), "line"), "fouled"))
    stated = _read_lines_stated(question)
    # A pair inside a stated line ("40 points" of "more than 40 points") is
    # that line's number and stat, not a line of its own at or over it.
    pairs = [match for match in lexicon.threshold_pairs(question) if not any(candidate.claim.start <= match.start() and match.end() <= candidate.claim.end for candidate in stated)]
    at_least = [m.span(1) for m in lexicon.AT_LEAST_PAIR.finditer(question)]
    for match in pairs:
        how: Literal["plus", "at_least", "bare"] = (
            "plus" if lexicon.THRESHOLD_PAIR.fullmatch(match.group(0)) is not None else "at_least" if (match.start(), match.start() + len(match.group(1))) in at_least else "bare"
        )
        line = Line(measure=lexicon.THRESHOLD_WORDS[match.group(2).casefold()], value=int(match.group(1)), as_typed=match.group(0).casefold())
        # The number with its plus ("20+", "20 or more"): up to the stat word, its separator aside.
        number_end = len(question[: match.start(2)].rstrip(" \t-")) if how == "plus" else match.end(1)
        found.append(LineCandidate(line, match.start(), Claim(match.start(), match.end(), "line"), how, (match.start(1), number_end, how), (match.start(2), match.end(2))))
    if not pairs:
        scored = lexicon.scored_threshold(question)
        if scored is not None:
            # "scores 30": the verb names the stat, whatever the model filed.
            line = Line(measure="points", value=int(scored.group(1)), as_typed=scored.group(0).casefold(), keyed=True)
            # "scores 30+": the plus is the number's where the verb's line states one.
            number: tuple[int, int, Literal["plus", "at_least", "bare"]] = (scored.start(1), scored.end(), "plus") if scored.end() > scored.end(1) else (scored.start(1), scored.end(1), "bare")
            found.append(LineCandidate(line, scored.start(), Claim(scored.start(), scored.end(), "line"), "scored", number))
    for pattern, below in ((lexicon.ABOVE, False), (lexicon.BELOW, True)):
        for m in pattern.finditer(question):
            phrase_number = _phrase_number(question, m.start(), m.end())
            lead = (m.start(), m.start() + len(question[m.start() : phrase_number[0]].rstrip())) if below and phrase_number is not None and phrase_number[0] > m.start() else None
            found.append(
                LineCandidate(phrase_line(m.group(0), below=below), m.start(), Claim(m.start(), m.end(), "line"), "phrase", phrase_number, _phrase_stat(question, phrase_number, m.end()), lead)
            )
    found.extend(stated)
    return LineCandidates(tuple(found))


def _read_lines_stated(question: str) -> list[LineCandidate]:
    """The lines kept over or at a number on a stat, stated by their lead -
    "more than 40 points", "above 9 rebounds", "exactly 25 field goal
    attempts" (:data:`~association.query.lexicon.LINE_OVER_OR_AT`) - or as "fga
    = 25" (:data:`~association.query.lexicon.EQUALS_LINE`, the longest run
    of words before the sign that names a stat), read on every key a line
    under a number is but a ranking of players' seasons (ISSUES.md #371,
    :func:`~association.query.point.lines_under`)."""
    found: list[LineCandidate] = []
    for m in lexicon.LINE_OVER_OR_AT.finditer(question):
        phrase_number = _phrase_number(question, m.start(), m.end())
        if phrase_number is not None and not m.group(0).casefold().startswith(lexicon.EXACT_LEADS):
            # "more than 30" states a line over a number as "30+" does: typed as one.
            phrase_number = (phrase_number[0], phrase_number[1], "plus")
        # The lead ("more than", "exactly") is the tagger's, typed as a line under a number's lead is.
        lead = (m.start(), m.start() + len(question[m.start() : phrase_number[0]].rstrip())) if phrase_number is not None else None
        found.append(LineCandidate(phrase_line(m.group(0), below=False), m.start(), Claim(m.start(), m.end(), "line"), "stated", phrase_number, _phrase_stat(question, phrase_number, m.end()), lead))
    for m in lexicon.EQUALS_LINE.finditer(question):
        words = m.group(1).split()
        for width in range(len(words), 0, -1):
            measure = measure_column(" ".join(words[-width:]))
            if measure is not None:
                start = m.start(1) + m.group(1).rindex(" ".join(words[-width:]))
                line = Line(measure=measure, op="=", value=int(m.group(2)), as_typed=question[start : m.end()].casefold(), narrows=True)
                found.append(LineCandidate(line, start, Claim(start, m.end(), "line"), "stated", (m.start(2), m.end(2), "bare"), (start, start + len(" ".join(words[-width:])))))
                break
    return found


def _phrase_stat(question: str, number: tuple[int, int, Literal["plus", "at_least", "bare"]] | None, end: int) -> tuple[int, int] | None:
    """Where the words after a phrase's number stand ("fta" of "under 14
    fta", "minutes" of "with 25 minutes"), or None where none follow it."""
    if number is None:
        return None
    rest = question[number[1] : end]
    start = number[1] + len(rest) - len(rest.lstrip())
    return (start, end) if start < end else None


def _phrase_number(question: str, start: int, end: int) -> tuple[int, int, Literal["plus", "at_least", "bare"]] | None:
    """Where the number of a floor of minutes or a line under a number
    stands in ``question[start:end]`` - its first run of digits, with the
    "+" or the "or more" after it where one stands ("25+ minutes", "20 or
    more minutes")."""
    first = next((at for at in range(start, end) if question[at].isdigit()), None)
    if first is None:
        return None
    last = first
    while last < end and question[last].isdigit():
        last += 1
    if last < end and question[last] == "+":
        return first, last + 1, "plus"
    after = question[last:end]
    words = after.casefold().split()
    if words[:2] == ["or", "more"]:
        more = after.casefold().index("more") + len("more")
        return first, last + more, "plus"
    return first, last, "bare"


def read_period_line(question: str) -> tuple[Line, Claim] | None:
    """The quarter or half a question uses as a condition on which games
    count, as a :class:`~association.query.reading.Line` in that period
    with the claim of the words that said it - or None where the question
    uses none, or words one whose stat the period's line does not rebuild
    (:data:`~association.query.measures.PERIOD_COLUMNS`; the reading names
    that by its ``period_as_condition`` cause). Read by the parser before
    the grammar and the taggers, which blanks its words so the period is not read as the
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
    words = lexicon.CONTENT_SPACES.sub(" ", match.group("stat").lower().strip())
    stat = lexicon.CONDITION_STAT_WORDS.get(words) or lexicon.MEASURE_WORDS.get(words) or lexicon.MEASURE_WORDS.get(f"{words}s")
    if stat is None or stat not in PERIOD_COLUMNS or value < 1:
        return None
    asked = which_period(match.group("period"))
    if asked is None:
        return None
    op: LineOp = ">=" if match.group("least") or match.group("more") or number in ("a", "an") else "="
    return Line(measure=stat, op=op, value=value, period=asked[0], as_typed=match.group(0).casefold()), Claim(match.start(), match.end(), "line")
