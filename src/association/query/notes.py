"""What an answer says beside its numbers, as values: a kind and its facts.

Until 5.0.0 a caveat, a stated default or a definition was a sentence glued
onto the answer, and some of them a second time into ``data["notes"]``: 69
distinct remarks written by about 75 functions, the same fact in up to
eleven wordings (``ROADMAP-TYPES.md``, "Notes - the kinds"). A sentence
cannot be compared, so a reworded answer could lose a caveat with every
number still matching. Here each remark is recorded as it is written:

- a :class:`Note` is about the data or about a term the answer uses - a
  game with no box score, a table's floor, what "played" means;
- a decision (:class:`~association.query.decisions.Decision` with a
  ``kind``) is something the question left open and the system chose - a
  name read as one player, a season redirected, a ranking's minimum.

The dividing question is "could the question have said it differently?"
(Jeff, 2026-10-01). The kinds are closed (:data:`NOTE_KINDS`,
:data:`DECISION_KINDS`); the sentence stays exactly where it is written
today, and :func:`note` and :func:`decided` hand it back unchanged, so
wrapping a writer moves no answer. One phrase per kind, written from the
facts, is ``ROADMAP.md``'s Phase 2.

Recorded only inside :func:`collect` - the answering loop's, around one
question. A writer called directly (a test, a template on its own) records
nothing and behaves the same.

.. versionadded:: 5.0.0
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from association.query.decisions import Decision

NOTE_KINDS: dict[str, str] = {
    "partial_season": "a season the table holds only part of",
    "floor": "seasons before the first one a table holds are not counted",
    "games_unseen": "games the answer could not see: no box score, an empty one, no play-by-play",
    "lines_rebuilt": "figures rebuilt from play-by-play where ESPN has no box score",
    "rebuilt_agreement": "how often a figure rebuilt this way matches the official one",
    "stat_withheld": "a stat not read from a rebuilt or partial source",
    "stat_blank": "games with no figure for the stat asked about",
    "seasons_missing": "seasons left out because their source is empty",
    "standings_short": "ESPN's standings cover fewer games than the team played",
    "game_list_disagrees": "ESPN's game list and the season's totals disagree on the games played",
    "shots_unlabeled": "shots left out because their value cannot be told",
    "shot_values_derived": "shot values derived where ESPN labeled few",
    "snapshot": "which power-index snapshot was read, and what is odd about it",
    "value_withheld": "a column or rank left blank, and why",
    "part_missing": "a part of the answer with nothing on record",
    "no_data_for": "named players with nothing on record for what was asked",
    "below_pool": "players shown against a pool they did not qualify for",
    "window_short": "fewer games found than the window asked for",
    "still_open": "a run still going at the last game on record",
    "definition": "what a word or column in the answer means",
    "hint": "another question that shows more",
}
"""Every kind a :class:`Note` may have, with what it says.

.. versionadded:: 5.0.0
"""

DECISION_KINDS: dict[str, str] = {
    "name_reading": "a name several players share, or a near spelling, read as one player",
    "name_left_out": "a name the question holds that the answer is not about",
    "also_matched": "the best match was answered; others matched too",
    "season_redirected": "the season read by default holds nothing for him; the seasons that do",
    "season_fallback": "no games this season, so an earlier one was read",
    "season_default": "no season named, so the latest on record was read - said as the span the heading names",
    "minimum": "the fewest games or attempts a ranking required",
    "cut": "how many qualified, and how many are shown",
}
"""Every kind a decision the answer states may have, with what it says.

.. versionadded:: 5.0.0
"""


FACTS: dict[str, frozenset[str]] = {
    # notes
    "partial_season": frozenset({"season", "season_type", "intent"}),
    "floor": frozenset({"table", "first", "what", "earliest", "last", "whose", "season_type"}),
    "games_unseen": frozenset({"games", "why", "whose", "what", "of", "first", "last"}),
    "lines_rebuilt": frozenset({"games", "what", "total", "whose", "columns"}),
    "rebuilt_agreement": frozenset({"season", "seasons", "pct", "columns", "stat", "what"}),
    "stat_withheld": frozenset({"games", "stat", "label"}),
    "stat_blank": frozenset({"games", "stat", "columns", "whose"}),
    "seasons_missing": frozenset({"seasons", "stat", "label", "why"}),
    "standings_short": frozenset({"team", "seasons"}),
    "game_list_disagrees": frozenset({"team", "seasons", "what", "narrowed"}),
    "shots_unlabeled": frozenset({"shots", "seasons", "why"}),
    "shot_values_derived": frozenset({"season"}),
    "snapshot": frozenset({"what", "season", "team", "date", "snapshot", "snapshots"}),
    "value_withheld": frozenset({"what", "why", "team", "teams", "season"}),
    "part_missing": frozenset({"what"}),
    "no_data_for": frozenset({"names", "what", "period"}),
    "below_pool": frozenset({"names", "threshold", "of"}),
    "window_short": frozenset({"found", "asked", "season", "season_type"}),
    "still_open": frozenset(),
    "definition": frozenset({"term", "what", "names", "games", "whose", "units", "possessions", "stints", "across_seasons"}),
    "hint": frozenset({"what", "team", "season", "snapshots"}),
    # decisions (beside field, chose, before, instead_of and why)
    "name_reading": frozenset({"season"}),
    "name_left_out": frozenset({"names"}),
    "also_matched": frozenset(),
    "season_redirected": frozenset({"first", "last", "what"}),
    "season_default": frozenset({"season_type"}),
    "season_fallback": frozenset({"games", "season_type"}),
    "minimum": frozenset({"of", "column"}),
    "cut": frozenset({"total"}),
}
"""The facts each kind may carry, by name - closed, as the kinds are. A
writer needing another name adds it here, deliberately, where the next
reader of the kind will see every fact it can hold.

.. versionadded:: 5.0.0
"""


@dataclass(frozen=True)
class Note:
    """One remark about the data, or about a term the answer uses: a
    ``kind`` from :data:`NOTE_KINDS` and the ``facts`` its sentence is made
    of - counts, names, seasons, never the sentence.

    .. versionadded:: 5.0.0
    """

    kind: str
    facts: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """The wire and file form."""
        return {"kind": self.kind, "facts": dict(self.facts)}


@dataclass
class Collected:
    """What one question's answer said beside its numbers: the notes, the
    decisions it stated, and each remark's sentence as written (``said``,
    by kind) - kept so :func:`unsaid` can check the sentence reached the
    answer.

    .. versionadded:: 5.0.0
    """

    notes: list[Note] = field(default_factory=list)
    decisions: list[Decision] = field(default_factory=list)
    said: list[tuple[str, str]] = field(default_factory=list)


_COLLECTING: ContextVar[Collected | None] = ContextVar("association_remarks", default=None)


@contextmanager
def collect() -> Iterator[Collected]:
    """Collect every remark written while one question is answered. A
    ``ContextVar``, as :func:`~association.query.entities.collect_name_readings`
    is: the web server and the tests answer on more than one thread.

    .. versionadded:: 5.0.0
    """
    collected = Collected()
    token = _COLLECTING.set(collected)
    try:
        yield collected
    finally:
        _COLLECTING.reset(token)


def note(kind: str, text: str, /, **facts: Any) -> str:
    """Record that ``text`` - a sentence being written into an answer - is a
    note of ``kind`` made of ``facts``, and return ``text`` unchanged. An
    empty ``text`` is no remark and records nothing, so a writer can wrap
    its sentence whether or not it had one to write.

    .. versionadded:: 5.0.0
    """
    if kind not in NOTE_KINDS:
        raise ValueError(f"{kind!r} is not a note kind (association.query.notes.NOTE_KINDS)")
    held = _values(kind, facts)
    collected = _COLLECTING.get()
    if collected is not None and text.strip():
        recorded = Note(kind, held)
        if recorded not in collected.notes:
            collected.notes.append(recorded)
        _remember(collected, kind, text)
    return text


def decided(kind: str, text: str, /, *, field: str, chose: Any, before: Any = None, instead_of: tuple[Any, ...] | list[Any] = (), why: str = "", **facts: Any) -> str:
    """Record that ``text`` states a decision of ``kind``: ``field`` was
    left open by the question (``before`` is what it typed, if anything),
    the system ``chose`` a value where it could have been one of
    ``instead_of``, for the reason ``why``. Returns ``text`` unchanged, and
    records nothing for an empty one, as :func:`note` does.

    .. versionadded:: 5.0.0
    """
    if kind not in DECISION_KINDS:
        raise ValueError(f"{kind!r} is not a decision kind (association.query.notes.DECISION_KINDS)")
    held = _values(kind, facts)
    typed, chosen, others = _value("before", before), _value("chose", chose), tuple(_value("instead_of", list(instead_of)))
    collected = _COLLECTING.get()
    if collected is not None and text.strip():
        recorded = Decision("answer", field, typed, chosen, why, kind=kind, instead_of=others, facts=held)
        if recorded not in collected.decisions:
            collected.decisions.append(recorded)
        _remember(collected, kind, text)
    return text


def _values(kind: str, facts: dict[str, Any]) -> dict[str, Any]:
    """``facts`` as plain values under the names ``kind`` declares
    (:data:`FACTS`), checked whoever is listening - so a writer handing over
    an object, or a fact the kind does not hold, is caught by any test that
    runs it, not by the first answer served."""
    undeclared = sorted(facts.keys() - FACTS[kind])
    if undeclared:
        raise ValueError(f"{kind!r} holds no fact named {undeclared} (association.query.notes.FACTS: {sorted(FACTS[kind])})")
    return {name: _value(name, value) for name, value in facts.items()}


def _value(name: str, value: Any) -> Any:
    """One fact as a plain value: a number, a string, None, or a list or
    dict of them (a tuple or a set becomes a list). Anything else - an
    entity, a row, a date - is refused: a fact is what the sentence was made
    of, as a value a client can read."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_value(name, each) for each in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_value(name, each) for each in value), key=repr)
    if isinstance(value, dict):
        return {str(key): _value(name, each) for key, each in value.items()}
    raise TypeError(f"the fact {name!r} is a {type(value).__name__}, not a plain value: pass its name, its number or its ISO date")


def _remember(collected: Collected, kind: str, text: str) -> None:
    """Keep a remark's sentence once, for :func:`unsaid`."""
    said = (kind, text.strip())
    if said not in collected.said:
        collected.said.append(said)


def unsaid(collected: Collected, answer_text: str) -> list[str]:
    """The kinds of the remarks whose sentence is not in ``answer_text``: a
    remark written and then dropped on the way to the answer. Compared with
    runs of spaces collapsed, since a writer's sentence is joined to the
    answer with one space or a line break.

    .. versionadded:: 5.0.0
    """
    answer = " ".join(answer_text.split())
    return [kind for kind, text in collected.said if " ".join(text.split()) not in answer]
