"""The Result: what the RUN stage produces for a question, as values - the
subject, the span the read covered, the narrowings it applied, the parts of
the answer (a table of rows, a figure) and the remarks the sayer has to
make, each as a kind and its facts. The sayer takes a Result and nothing
else, so everything the result carries is said or is visibly dropped
(``ROADMAP.md``, contract 5), and "identical" between two trees is a
comparison of values with the sentence left out (``ROADMAP-TYPES.md``,
"Outcome - what RUN produces").

Declared here by Phase 2's first slice (``ROADMAP.md``, "Phase 2, the
expected steps", step 0: a player's or a team's game log), as the target
types draft says a type is - by the phase that first uses it. What this
first version carries beyond the draft, on purpose and to be cut as the
sayers take it over: the narrowing's phrase and the span's words
(``Narrowing.phrase``, ``Span.years``), which the reader still takes from
the relation's own ``filters()`` and ``years()`` rather than the sayer
building them from cells; and an ``empty`` sentence, where the reader
found no rows and the shared "which fact is missing" writer
(``templates.common._no_narrowed_games``) still composes the reason, as
it does for the templates that have not retired.

.. versionadded:: 5.0.0
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from association.query.notes import Note


@dataclass(frozen=True, kw_only=True)
class Span:
    """The seasons a read covered: one season and its type, or a career
    (``career``) whose first and last seasons the rows name, or one dated
    game (``date``). ``years`` is the span's own wording of a range
    ("2024-25 to 2025-26"), the relation's ``_Span.years``, carried until
    the sayer words it from ``first`` and ``last``.

    .. versionadded:: 5.0.0
    """

    season: int | None = None
    season_type: int | None = None
    career: bool = False
    date: str | None = None
    first: int | None = None
    last: int | None = None
    years: str | None = None


@dataclass(frozen=True, kw_only=True)
class Narrowing:
    """The cells the read applied beyond the span, as values and as the
    phrase the relation says them with (``Narrowed.filters()``) - the
    phrase is carried, not built, until each cell has one phrase in the
    sayer (``ROADMAP.md``, contract 4).

    .. versionadded:: 5.0.0
    """

    phrase: str = ""
    opponent: str | None = None
    venue: str | None = None
    without: tuple[str, ...] = ()


@dataclass(frozen=True, kw_only=True)
class Rows:
    """A table of games (or of any one-row-per-item read): ``columns`` in
    display order, ``rows`` as plain mappings, the count the window cut
    them from (``total_before_window``), the per-row figures summed or
    averaged over exactly these rows (``summary``: a log's per-game
    averages, a team log's wins and losses and its total), and how many
    came from each season type where two were merged (``by_season_type``).

    .. versionadded:: 5.0.0
    """

    columns: tuple[str, ...] = ()
    rows: tuple[Mapping[str, Any], ...] = ()
    total_before_window: int | None = None
    summary: Mapping[str, Any] = field(default_factory=dict)
    by_season_type: Mapping[int, int] = field(default_factory=dict)


@dataclass(frozen=True, kw_only=True)
class Part:
    """One part of an answer: its role and its body. The first part is the
    headline's.

    .. versionadded:: 5.0.0
    """

    role: Literal["answer", "summary", "detail"] = "answer"
    body: Rows | None = None
    notes: tuple[Note, ...] = ()


@dataclass(frozen=True, kw_only=True)
class Window:
    """The window the read applied after every other narrowing: how many
    rows it kept (``limit``), how many the question asked for (``asked``,
    ``None`` for a default), and which end of the span it took them from.

    .. versionadded:: 5.0.0
    """

    limit: int
    asked: int | None = None
    ascending: bool = False


@dataclass(frozen=True, kw_only=True)
class Result:
    """What one read produced. ``subject`` names who it is about (a player,
    a team), ``relation`` which relation it read; ``parts`` hold the rows
    or figures, ``notes`` what the sayer must say about the data (kinds and
    facts, never sentences), ``facts`` the plain values a shape's sayer
    needs beside them (a team log's ``stat`` for its total line). ``empty``
    is the sentence a read with no rows gives its reason with - see the
    module docstring for why it is a sentence still.

    .. versionadded:: 5.0.0
    """

    subject: str
    relation: Literal["player", "team", "everyone"]
    span: Span = field(default_factory=Span)
    narrowing: Narrowing = field(default_factory=Narrowing)
    window: Window | None = None
    parts: tuple[Part, ...] = ()
    notes: tuple[Note, ...] = ()
    facts: Mapping[str, Any] = field(default_factory=dict)
    empty: str | None = None

    @property
    def rows(self) -> Rows | None:
        """The first part's rows, where the answer is a table."""
        return self.parts[0].body if self.parts else None
