"""A typed value the grammar's text held that the Reading dropped.

The parent grammar reads the question's words with each value a tagger
typed standing in them (``lexicon.GrammarText``): a line, an opponent, a
companion, a window, a date. A value of those that leaves no trace on the
Reading the parser returns - a line with no ``lines``, an opponent with no
``cuts.opponent``, a companion with no ``companions``, a window with no
count or end, a date with no ``cuts.date`` or ``situation`` - is dropped
by the key's reading (``point.*_under``) before the planner, which never
sees it: the silent part of a fluent narrower answer (#352 a window, #357
a line). ``~/association-research/stages/grammar_rows_dropped.py``
measures it over the 2,710 readings with this module's rule and held
list; ``tests/query/test_grammar_drops.py`` holds every reader case to it.

A drop is HELD, never silent, in one of two ways: by the rule
:func:`held_by_rule` - a companion who is the subject himself (the team's
record in the games he reached a line, whose line is the record's own and
whose player he is; a player named only as a companion, whose games are
his own) - or by the question's text in :data:`HELD`, each with its
reason. A held question that does not drop leaves the list with the
change that cleared it, as a ratchet's GONE entry does.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from association.query import lexicon, parse
from association.query.reading import Reading
from association.query.subject import question_supports

FAMILY: dict[str, str] = {
    "line_plus": "line",
    "line_at_least": "line",
    "line_number": "line",
    "line_stat": "line",
    "line_lead": "line",
    "line_season": "line",
    "opponent": "opponent",
    "opponent_against": "opponent",
    "opponent_versus": "opponent",
    "companion": "companion",
    "companion_with": "companion",
    "window": "window",
    "date": "date",
}
"""Each typed kind of the grammar's text (``lexicon.TypedKind``) by the
family of the Reading it lands in."""

HELD: dict[str, str] = {
    "marvin bagley gameswith 20 points by season": (
        "refused by name (unread_number): 'gameswith' is a glued typo the games words do not read, and a history by season over the games holding a line is no shape"
    ),
    "Towns over 9 rebound": "refused by name (unread_number): with no model names 'towns' is an ordinary word, and a line on the league's season ranking reads nothing",
    "harden 61 points": "refused by name (unread_number): with no model names 'harden' is an ordinary word; a bare '61 points' can name one game or a line",
    "2 threes in games Jamal Murray played including playoffs": "the one player named only as a companion is the subject, and the games he played are his own (subject._read_subject_alone)",
    "murray games of 20+ rebounds": "refused by name (unread_number): with no model names 'murray' is several players, and a line on the league's season ranking reads nothing",
    "murray's games of 20+ rebounds": "refused by name (unread_number): as 'murray games of 20+ rebounds'",
    "sga scored 20 points in the 2nd quarter": "refused by name (unread_number): a line on a quarter's own figure, kept on the quarter's log, is read by nothing (ISSUES.md #357's remainder)",
    "players who made exactly 129 three pointers in the NBA in 2025-26": "refused by name (unread_number): a season's total stated as 'made exactly N' is no season line the lines tagger reads",
}
"""The questions whose dropped value is held, by their text, each with its
reason - every one but the companion who is the subject refused by name."""


def held_by_rule(reading: Reading, dropped: set[str]) -> bool:
    """Whether every value dropped is a companion who is the subject
    himself, as the subject reading read him: the team's record in the
    games a companion reached a line - the line the record's own, the
    companion its player - and the companion a
    rule restored as the subject ("how many games with 40+ points did
    Lebron James have")."""
    if dropped != {"companion"} or reading.subject is None or not reading.subject.conditions:
        return False
    players = reading.who.players
    return all(any(question_supports(c.player, p) or question_supports(p, c.player) for p in players) for c in reading.subject.conditions)


def kept(reading: Reading) -> set[str]:
    """The families the Reading holds a value of."""
    out: set[str] = set()
    if reading.lines:
        out.add("line")
    if reading.cuts.opponent:
        out.add("opponent")
    if reading.companions:
        out.add("companion")
    if reading.window.count is not None or reading.window.order is not None:
        out.add("window")
    if reading.cuts.date or reading.cuts.situation:
        out.add("date")
    return out


def caused(reading: Reading) -> bool:
    """Whether the reading carries a cause the planner says before any
    reader runs or in the point's place: a dropped value there is said."""
    return reading.point_refusal is not None or reading.refused is not None or reading.point_declined is not None


@contextmanager
def typed_families() -> Iterator[Callable[[], set[str]]]:
    """While open, records the families typed in the grammar's text at the
    parser's FIRST parent-grammar call of each question (the later calls
    are the claims' probes, each with a word deleted); the function handed
    back returns and clears them."""
    seen: list[set[str]] = []
    original = parse.parent_asked

    def recording(question: Any, kind: str, companions: bool = False) -> Any:
        if not seen:
            text = lexicon.GrammarText.of(question)
            seen.append({FAMILY[k] for k in text.kinds if k is not None and k in FAMILY})
        return original(question, kind, companions)

    def taken() -> set[str]:
        families = seen[0] if seen else set()
        seen.clear()
        return families

    parse.parent_asked = recording
    try:
        yield taken
    finally:
        parse.parent_asked = original


def live_drop(reading: Reading, families: set[str]) -> set[str]:
    """The families typed and dropped with no cause and not held - empty
    where nothing is live (:data:`HELD` is the caller's, by question)."""
    dropped = families - kept(reading)
    if not dropped or reading.asked is None or caused(reading) or held_by_rule(reading, dropped):
        return set()
    return dropped
