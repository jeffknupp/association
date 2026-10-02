"""The players' and teams' names, held in memory for name recognition.

Recognizing a name used to cost a SQL statement per word of the question:
over the 277 yardstick questions the reader issued about 100 statements a
question, and 97% of them asked ``players`` or ``teams`` - 3,101 rows and 30 -
whether some word was somebody's name. :class:`PlayerIndex` and
:class:`TeamIndex` read each table once and answer those questions from
dictionaries (ROADMAP.md, contract 3: "names are recognized from an in-memory
index, not by query").

What they hold is what the matchers in :mod:`association.query.entities`
need, in the form the SQL they replace read it, because no answer may move:

- the rows in table order - a ``LIMIT`` with no ``ORDER BY`` returns rows in
  the order DuckDB scans them, which for these tables is the order they hold;
- each name's words split the way ``regexp_split_to_array(lower(display_name),
  '[^a-z]+')`` splits them (:func:`sql_words`), and a word -> rows map;
- each name's words split the way the near-spelling SQL split them
  (``regexp_split_to_array(display_name, '[^A-Za-z]+')``, then ``lower``;
  :func:`spelled_words`), as a vocabulary :func:`rapidfuzz.process.extract`
  searches in one call, measured the way DuckDB's ``damerau_levenshtein``
  measures (:func:`distance`: over UTF-8 BYTES, so an accented letter is two
  edits from its plain one, as it was);
- whether each column holds only ASCII, which DuckDB keeps in its statistics
  and which decides how its ``ILIKE`` lowers (:attr:`PlayerIndex.ascii_only`);
- which ``teams`` columns exist, and the error reading either table raised -
  several partial warehouses (and many tests) have no ``teams``, or a
  ``teams`` without ``name`` and ``location``, and the callers' answers there
  depend on which error the SQL used to raise where.

This module reads no warehouse itself and never sees a question: the caller
runs the one statement each table takes (``read``) and the matchers hand it
words. Inside ``with loaded():`` - entered once per question by the
answering loop - each table is read at most once per connection, the first
time a lookup needs it. A lookup outside a block is refused
(:class:`NotLoaded`): there is one way to look a name up. The next block
reads the table again, so a warehouse reloaded between two questions is
seen; the index BUILT from rows already seen is reused, which is what keeps
that cheap (:func:`_players_from`).

.. versionadded:: 5.0.0
"""

from __future__ import annotations

import functools
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from rapidfuzz import process
from rapidfuzz.distance import DamerauLevenshtein

from association.query.duckdb_lower import LOWER_RUNS

# DuckDB's lower() maps each code point to its SIMPLE lowercase, one for one,
# from the Unicode tables DuckDB bundles. Python's str.lower() is the full
# mapping from the INTERPRETER's tables, so it differs three ways: U+0130
# (capital I with a dot) becomes two code points, a capital sigma at the end
# of a word becomes a final sigma, and a letter one Unicode version cased and
# the other did not lowers under one and not the other - against DuckDB 1.5.5,
# Python 3.12 and 3.13 agreed on every code point, 3.10 and 3.14 did not. So
# the mapping is DuckDB's own, asked of it once and committed
# (query/duckdb_lower.py), and checked over every code point against the
# installed DuckDB (tests/query/test_names.py).
_LOWER = {code: code + delta for first, last, delta, step in LOWER_RUNS for code in range(first, last + 1, step)}


def sql_lower(text: str) -> str:
    """``text`` lowered exactly as DuckDB's ``lower()`` lowers it, on any
    interpreter.

    .. versionadded:: 5.0.0
    """
    if text.isascii():
        return text.lower()
    return text.translate(_LOWER)


# Every ASCII character that is not a letter, to a space - so an ASCII text's
# words are its runs of letters, read by str.split.
_NOT_A_LETTER = str.maketrans({chr(code): " " for code in range(128) if not chr(code).isalpha()})


def _split_runs(text: str, keep: Callable[[str], bool]) -> list[str]:
    """``text`` split on every run of characters ``keep`` rejects, the way
    ``regexp_split_to_array`` splits on ``[^...]+``: a separator at either end
    leaves an empty word there, and an empty text is one empty word."""
    words = [""]
    in_separator = False
    for char in text:
        if keep(char):
            if in_separator:
                words.append("")
                in_separator = False
            words[-1] += char
        else:
            in_separator = True
    if in_separator:
        words.append("")
    return words


def _split_letters(text: str, keep: Callable[[str], bool]) -> list[str]:
    """:func:`_split_runs` on the runs of ASCII letters ``keep`` accepts all
    of, by ``str.split`` where ``text`` is ASCII (every name on record)."""
    if not text.isascii():
        return _split_runs(text, keep)
    spaced = text.translate(_NOT_A_LETTER)
    words = spaced.split()
    if not spaced or spaced[0] == " ":
        words.insert(0, "")
    if spaced and spaced[-1] == " ":
        words.append("")
    return words


def _lowercase_ascii(char: str) -> bool:
    return "a" <= char <= "z"


def _ascii_letter(char: str) -> bool:
    return "a" <= char <= "z" or "A" <= char <= "Z"


def sql_words(name: str) -> list[str]:
    """``regexp_split_to_array(lower(name), '[^a-z]+')``: the words a name is
    matched on whole. Any character but a plain lowercase letter separates
    words - an accented one too, after lowering - so "Dončić" is the words
    "don", "i" and an empty one, not "dončić"; the warehouse spells every
    name in plain letters, and this keeps the SQL's own reading of one that
    is not.

    .. versionadded:: 5.0.0
    """
    return _split_letters(sql_lower(name), _lowercase_ascii)


def spelled_words(name: str) -> list[str]:
    """``regexp_split_to_array(name, '[^A-Za-z]+')`` with each word lowered:
    the words a near spelling is measured against.

    .. versionadded:: 5.0.0
    """
    return [word.lower() for word in _split_letters(name, _ascii_letter)]


def distance(one: str, other: str) -> int:
    """DuckDB's ``damerau_levenshtein(one, other)``: the unrestricted
    Damerau-Levenshtein distance, over the two strings' UTF-8 bytes - which is
    how DuckDB counts it, so "č" is two edits from "c". The caller lowers
    whichever side its SQL lowered.

    .. versionadded:: 5.0.0
    """
    return int(DamerauLevenshtein.distance(one.encode(), other.encode()))


def _plain(value: Any) -> bool:
    """Whether ``value`` holds no character but ASCII - a NULL or a value
    that is not text counts, since neither is what an ``ILIKE`` reads."""
    return not isinstance(value, str) or value.isascii()


@dataclass(frozen=True)
class PlayerIndex:
    """The ``players`` table, held for name recognition: built by
    :meth:`build` from ``SELECT athlete_id, display_name FROM players``.

    .. versionadded:: 5.0.0
    """

    rows: tuple[tuple[Any, str | None], ...]
    """``(athlete_id, display_name)`` for every player, in table order."""
    error: Exception | None
    """What reading ``players`` raised, or ``None``. A matcher re-raises it
    where its SQL would have."""
    ascii_only: bool
    """Whether every ``display_name`` is plain ASCII. DuckDB keeps that fact
    in a column's statistics and, where it holds, answers ``ILIKE`` by
    lowering ASCII letters alone - so a pattern holding any other character
    matches nothing there, where on a column with one accented value it is
    lowered and matched like any other (``"İndiana"`` finds "Indiana Pacers"
    only in the second)."""
    _by_word: dict[str, tuple[int, ...]] = field(repr=False)
    _by_spelling: dict[bytes, tuple[int, ...]] = field(repr=False)
    _vocabulary: tuple[bytes, ...] = field(repr=False)

    @classmethod
    def build(cls, rows: Sequence[tuple[Any, str | None]], error: Exception | None = None) -> PlayerIndex:
        """The index of ``rows`` (``athlete_id, display_name``, in table
        order), or of no rows and the ``error`` reading them raised.

        .. versionadded:: 5.0.0
        """
        by_word: dict[str, list[int]] = {}
        by_spelling: dict[bytes, list[int]] = {}
        for row, (_athlete_id, name) in enumerate(rows):
            if name is None:
                continue  # NULL matches nothing in any of the SQL this replaces
            for word in dict.fromkeys(sql_words(name)):
                by_word.setdefault(word, []).append(row)
            for word in dict.fromkeys(spelled_words(name)):
                by_spelling.setdefault(word.encode(), []).append(row)
        return cls(
            rows=tuple((athlete_id, name) for athlete_id, name in rows),
            error=error,
            ascii_only=all(_plain(name) for _athlete_id, name in rows),
            _by_word={word: tuple(found) for word, found in by_word.items()},
            _by_spelling={word: tuple(found) for word, found in by_spelling.items()},
            _vocabulary=tuple(by_spelling),
        )

    def with_words(self, words: Sequence[str], limit: int | None = None) -> list[int]:
        """The rows (positions in :attr:`rows`) whose :func:`sql_words` hold
        every one of ``words`` as given, in table order, at most ``limit`` of
        them - ``list_contains(regexp_split_to_array(lower(display_name),
        '[^a-z]+'), ?)`` for each word, ANDed, with ``LIMIT``.

        .. versionadded:: 5.0.0
        """
        if not words:
            return []
        first, *rest = [self._by_word.get(word, ()) for word in words]
        others = [frozenset(found) for found in rest]
        matched = [row for row in first if all(row in found for found in others)]
        return matched if limit is None else matched[:limit]

    def near(self, tokens: Sequence[tuple[str, int]]) -> list[tuple[int, int]]:
        """``(row, total)`` for each player every ``(token, budget)`` is within
        ``budget`` edits of - each token against its NEAREST word of the
        name (:func:`spelled_words`), measured by :func:`distance` on the
        token as DuckDB lowers it - in table order, ``total`` being the sum
        of those nearest distances. The vocabulary is searched once per
        token; no name is compared one at a time.

        .. versionadded:: 5.0.0
        """
        nearest: list[dict[int, int]] = []
        for token, budget in tokens:
            gaps: dict[int, int] = {}
            matches = process.extract(sql_lower(token).encode(), self._vocabulary, scorer=DamerauLevenshtein.distance, processor=None, score_cutoff=budget, limit=None)
            for word, gap, _ in matches:
                for row in self._by_spelling[word]:
                    if gap < gaps.get(row, gap + 1):
                        gaps[row] = int(gap)
            nearest.append(gaps)
        if not nearest:
            return []
        rows = sorted(set(nearest[0]).intersection(*nearest[1:]))
        return [(row, sum(gaps[row] for gaps in nearest)) for row in rows]


@dataclass(frozen=True)
class TeamIndex:
    """The ``teams`` table, held for name recognition: built by
    :meth:`build` from ``SELECT * FROM teams``.

    .. versionadded:: 5.0.0
    """

    rows: tuple[dict[str, Any], ...]
    """Every row, column name -> value, in table order."""
    columns: frozenset[str]
    """The columns the table has."""
    ascii_columns: frozenset[str]
    """The columns every value of which is plain ASCII; see
    :attr:`PlayerIndex.ascii_only`."""
    error: Exception | None
    """What reading ``teams`` raised, or ``None``."""

    @classmethod
    def build(cls, columns: Sequence[str], rows: Sequence[Sequence[Any]], error: Exception | None = None) -> TeamIndex:
        """The index of ``rows`` (values of ``columns``, in table order), or
        of no rows and the ``error`` reading them raised.

        .. versionadded:: 5.0.0
        """
        named = tuple(dict(zip(columns, values, strict=True)) for values in rows)
        return cls(
            rows=named,
            columns=frozenset(columns),
            ascii_columns=frozenset(column for column in columns if all(_plain(row[column]) for row in named)),
            error=error,
        )


# Built once per distinct table content. The rows are read again in every
# loaded() block (once per question), but rows already seen are not indexed
# twice - 17ms of building, measured on the
# 3,101 players, against well under 1ms to hash the rows read. The columns'
# names AND types are part of the key, since 1 == 1.0 == True in Python and
# an id is printed differently for each.
@functools.lru_cache(maxsize=8)
def _players_from(described: tuple[tuple[str, str], ...], rows: tuple[tuple[Any, ...], ...]) -> PlayerIndex:
    del described  # part of the cache key only
    return PlayerIndex.build([(row[0], row[1]) for row in rows])


@functools.lru_cache(maxsize=8)
def _teams_from(described: tuple[tuple[str, str], ...], rows: tuple[tuple[Any, ...], ...]) -> TeamIndex:
    return TeamIndex.build([name for name, _type in described], rows)


TableRead = Callable[[Any], "tuple[Sequence[tuple[str, str]], Sequence[Sequence[Any]], Exception | None]"]
"""How a caller reads a table: the connection to the table's columns (each
a name and its type), its rows in table order, and the error reading it
raised (then no columns and no rows).

.. versionadded:: 5.0.0
"""


@dataclass
class _Slot:
    """One connection's indexes inside a :func:`loaded` block: each table's,
    once a lookup has read it."""

    con: Any
    players: PlayerIndex | None = None
    teams: TeamIndex | None = None


_LOADED: ContextVar[dict[int, _Slot] | None] = ContextVar("association_name_index", default=None)


class NotLoaded(RuntimeError):
    """A name was looked up outside a :func:`loaded` block.

    .. versionadded:: 5.0.0
    """


@contextmanager
def loaded() -> Iterator[None]:
    """While entered, lookups read each of ``players`` and ``teams`` once
    per connection - the first time one needs it, so a question that reads
    no name reads neither - and a lookup outside any block is refused
    (:class:`NotLoaded`).

    Entered by the answering loop around each question, beside
    :func:`~association.nba.season.season_on_record`: per question rather
    than per connection, because a server outlives a reload of the warehouse
    it reads. A ``ContextVar`` and not a module global, since the web server
    and the tests answer on more than one thread. A block inside another
    starts afresh and reads again.

    There is one way to look a name up, and it is inside a block. Until
    5.0.0's last change a lookup outside one read its table again every
    time: the same answers, a statement per lookup, and nothing to say a
    caller had forgotten the block - which is how a reader that is meant to
    issue two statements a question would have gone back to hundreds
    without a test failing.

    .. versionadded:: 5.0.0
    """
    token = _LOADED.set({})
    try:
        yield
    finally:
        _LOADED.reset(token)


def _slot_for(con: Any) -> _Slot:
    block = _LOADED.get()
    if block is None:
        raise NotLoaded("a name was looked up outside names.loaded(): Agent.ask enters it around each question, and any other caller wraps its own lookups in one")
    slot = block.get(id(con))
    if slot is None or slot.con is not con:
        slot = block[id(con)] = _Slot(con)
    return slot


def players_for(con: Any, read: TableRead) -> PlayerIndex:
    """The players' index for ``con`` in the :func:`loaded` block the caller
    is inside; ``read`` runs the first time a lookup needs it.

    ``read`` runs the statement; this module holds no SQL of its own
    (``scripts/check_ratchets.py``, ``sql_outside_the_relations``).

    .. versionadded:: 5.0.0
    """
    slot = _slot_for(con)
    if slot.players is None:
        described, rows, error = read(con)
        slot.players = PlayerIndex.build((), error) if error is not None else _players_from(tuple(described), tuple(tuple(row) for row in rows))
    return slot.players


def teams_for(con: Any, read: TableRead) -> TeamIndex:
    """The teams' index for ``con``, as :func:`players_for` finds the
    players'.

    .. versionadded:: 5.0.0
    """
    slot = _slot_for(con)
    if slot.teams is None:
        described, rows, error = read(con)
        slot.teams = TeamIndex.build((), (), error) if error is not None else _teams_from(tuple(described), tuple(tuple(row) for row in rows))
    return slot.teams
