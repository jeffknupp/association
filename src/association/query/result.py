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

import dataclasses
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
    the sayer words it from ``first`` and ``last``. ``floor`` is the first
    season a span over every season could reach (the relation's floor),
    which a span with nothing in it is named by ("in any regular season on
    record (1994 onward)").

    .. versionadded:: 5.0.0
    """

    season: int | None = None
    season_type: int | None = None
    career: bool = False
    date: str | None = None
    first: int | None = None
    last: int | None = None
    years: str | None = None
    phrase: str | None = None
    floor: int | None = None


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
    ``by`` is what the rows are in order of, the draft's "rows by date"
    and "rows by the measure": ``"date"`` for a log, or the measure a
    single-game high ranks its games by.

    .. versionadded:: 5.0.0
    """

    columns: tuple[str, ...] = ()
    rows: tuple[Mapping[str, Any], ...] = ()
    total_before_window: int | None = None
    summary: Mapping[str, Any] = field(default_factory=dict)
    by_season_type: Mapping[int, int] = field(default_factory=dict)
    by: str = "date"


@dataclass(frozen=True, kw_only=True)
class Grouped:
    """One row per value of ``by``: a record split by whether a line was
    reached, a splits table by venue, a ranking by player. Each row is a
    plain mapping with its ``key`` and its values.

    ``ranked_by`` is the measure a ranking orders its rows by, the draft's
    "order: by the measure" (``ROADMAP-TYPES.md``, "The shapes") - what tells
    two rankings by ``player`` apart: ``"games"`` for a count of games over a
    line (``threshold_count``'s league, whose rows carry ``games``), a
    season-line metric's name (``"avg_points"``, ``"ts_pct"``) for a stat
    ranking (``leaderboard``, whose rows carry ``rank`` and ``values`` by
    measure, the ranked figure under this name). ``None`` for a group that
    is no ranking (a split, a record over a line, two named subjects).

    .. versionadded:: 5.0.0
    """

    by: str
    rows: tuple[Mapping[str, Any], ...] = ()
    ranked_by: str | None = None


@dataclass(frozen=True, kw_only=True)
class Scalar:
    """The whole narrowed set reduced to one line (``ROADMAP-TYPES.md``,
    "The shapes": ``scalar``): how many ``games`` it held, each measure's
    figure over them (``values``, by measure name - a per-game average, or a
    rate as the ratio of its sums), and the sums the line is said from
    (``sums``: a stat's total by its name, a percentage's ``made`` and
    ``attempted``, a made count's attempts by their column). Declared by the
    first scalar shape to retire its template (``player_stat``'s narrowed
    line), as the draft says a type is.

    ``how`` is how the set was reduced, the draft's ``Measure.how``: each
    measure per game beside its sums (``"per_game"``, a line), or the games
    counted (``"count"``, ``threshold_count``'s one player: ``games`` is the
    figure, ``values`` is empty, and ``sums`` holds how many of the counted
    games are rebuilt from play-by-play, ``rebuilt``).

    .. versionadded:: 5.0.0
    """

    games: int
    values: Mapping[str, Any] = field(default_factory=dict)
    sums: Mapping[str, Any] = field(default_factory=dict)
    how: Literal["per_game", "count"] = "per_game"


@dataclass(frozen=True, kw_only=True)
class Run:
    """One run of consecutive games a condition held along (``ROADMAP-TYPES.md``,
    "The shapes": ``runs``): whose it is where a listing names several
    (``owner``; ``None`` for one named subject's own), how many games, the
    first and last day and season, and whether it was still going at the
    owner's last game on record (``still_open``).

    .. versionadded:: 5.0.0
    """

    owner: str | None = None
    length: int
    first: Any
    last: Any
    first_season: int
    last_season: int
    still_open: bool = False


def run_of(row: Mapping[str, Any], owner: str | None = None) -> Run:
    """One row of a run statement - the columns ``conditions._longest_runs_sql``
    and the team compiler's run read return (``length``, ``first_day``,
    ``last_day``, ``first_season``, ``last_season``, ``open``) - as a
    :class:`Run`, under ``owner`` where a listing names several.

    .. versionadded:: 5.0.0
    """
    return Run(
        owner=owner,
        length=row["length"],
        first=row["first_day"],
        last=row["last_day"],
        first_season=row["first_season"],
        last_season=row["last_season"],
        still_open=bool(row["open"]),
    )


@dataclass(frozen=True, kw_only=True)
class Runs:
    """The longest runs a read found, longest first (``ROADMAP-TYPES.md``,
    "The shapes": ``runs``) - a named subject's longest and any that tie
    it, or one per owner over the league. Declared by the first runs shape
    to retire its template (``streak``).

    .. versionadded:: 5.0.0
    """

    runs: tuple[Run, ...] = ()


@dataclass(frozen=True, kw_only=True)
class Decided:
    """One decision the read made where the question left a field open
    (``ROADMAP-TYPES.md``, "Outcome": ``decisions``): the ``kind`` from
    :data:`~association.query.notes.DECISION_KINDS`, the ``field`` it
    settled, what the question typed (``before``), what was chosen and
    what it could have been, and the facts the sentence is made of - never
    the sentence. The sayer phrases it once and records it through
    :func:`~association.query.notes.decided`, as it does a :class:`Note`.

    .. versionadded:: 5.0.0
    """

    kind: str
    field: str
    chose: Any
    before: Any = None
    instead_of: tuple[Any, ...] = ()
    why: str = ""
    # ``dataclasses.field`` by its module: the attribute above named ``field`` shadows the import.
    facts: Mapping[str, Any] = dataclasses.field(default_factory=dict)


@dataclass(frozen=True, kw_only=True)
class Part:
    """One part of an answer: its role and its body. The first part is the
    headline's.

    .. versionadded:: 5.0.0
    """

    role: Literal["answer", "summary", "detail"] = "answer"
    body: Rows | Grouped | Scalar | Runs | None = None
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
    facts, never sentences), ``decisions`` what it chose where the question
    left a field open (:class:`Decided`), ``facts`` the plain values a shape's sayer
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
    decisions: tuple[Decided, ...] = ()
    facts: Mapping[str, Any] = field(default_factory=dict)
    empty: str | None = None

    @property
    def rows(self) -> Rows | None:
        """The first part's rows, where the answer is a table of items."""
        body = self.parts[0].body if self.parts else None
        return body if isinstance(body, Rows) else None

    @property
    def scalar(self) -> Scalar | None:
        """The first part's line, where the answer is one row reduced from the whole set."""
        body = self.parts[0].body if self.parts else None
        return body if isinstance(body, Scalar) else None

    @property
    def grouped(self) -> Grouped | None:
        """The first part's groups, where the answer is one row per group."""
        body = self.parts[0].body if self.parts else None
        return body if isinstance(body, Grouped) else None

    @property
    def runs(self) -> Runs | None:
        """The first part's runs, where the answer is the longest runs of consecutive games."""
        body = self.parts[0].body if self.parts else None
        return body if isinstance(body, Runs) else None
