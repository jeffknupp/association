"""What a question produces, as values rather than as printed text.

A string and a printed trace are enough for a terminal and nothing else.
These are the shapes any other caller (the web API, a notebook, a test)
needs: the text, the shape it was read as, the structured data the fast
path computed, and the files that were written.

Nothing here imports the rest of the query package but the planned shape's
type (:class:`~association.query.reading.PointShape`, from a module that
imports none of the answer side), so readers, the sayer and the answering
loop can all depend on it without a cycle.

.. versionadded:: 2.0.0
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from association.query.decisions import Decision
from association.query.notes import Note
from association.query.reading import Alternative, PointShape

if TYPE_CHECKING:
    import duckdb

AnsweredBy = Literal["fast", "refused"]
"""Whether the question was answered, or refused with no reading of it.

``"fast"`` means parser -> planner -> a reader and the sayer, or the
compiler's own sentence: deterministic, typically 1-2s, and the only value
that populates :attr:`Answer.shape` and :attr:`Answer.data`. It covers a
reader's or the compiler's own refusal too - a clarification, a "no match", a shape nothing here reads named by its
cause - since looking at the question and having something to say about it
is an answer. ``"refused"`` means nothing here had a reading of the question
at all, and :attr:`Answer.text` names why
(:func:`association.query.agent.refusal_text`).

.. versionadded:: 2.0.0

.. versionchanged:: 5.0.0
   ``"refused"`` replaces ``"agent"``: the tool-calling fall-through is gone,
   and the question it would have been handed is refused naming why.
"""


ARTIFACT_KINDS: frozenset[str] = frozenset({"shot_chart", "fingerprint"})
"""Every value :attr:`Artifact.kind` can take.

Named so a caller can dispatch on the kind without matching on filenames.

.. versionadded:: 2.0.0
"""


@dataclass(frozen=True)
class Artifact:
    """A file an answer wrote - today always a self-contained HTML plot.

    The path is carried as a value because the alternative is parsing it back
    out of the message it was formatted into.

    .. versionadded:: 2.0.0
    """

    kind: str
    path: Path

    @property
    def name(self) -> str:
        """The file's basename, which is how it is addressed over HTTP."""
        return self.path.name


@dataclass(frozen=True)
class AnswerContext:
    """What a reader is given: the warehouse, and somewhere to write output.

    Readers took a bare connection until the shot chart needed an output
    directory too. A small context rather than the whole answering loop keeps
    the readers testable with a plain in-memory DuckDB connection.

    .. versionadded:: 5.0.0
    """

    con: duckdb.DuckDBPyConnection
    out_dir: Path


@dataclass(frozen=True)
class Timing:
    """Where a run's wall time went, split into model inference and everything
    else. The call counts matter as much as the seconds: on the fast path one
    model call is the whole cost, and a second one would mean the KV cache
    eviction described in :class:`Answer`.

    .. versionadded:: 2.0.0

    .. versionchanged:: 6.0.0
       Every field defaults to zero: an answer the sayer said and the
       answering loop has not yet stamped is untimed (Phase 4, step 4).
    """

    total_seconds: float = 0.0
    model_seconds: float = 0.0
    model_calls: int = 0
    tool_seconds: float = 0.0
    tool_calls: int = 0


@dataclass(frozen=True, kw_only=True)
class Answer:
    """One question's complete result - and, before the answering loop has
    stamped it, what the answer side said: the sayer (and the compiler's own
    sentence) returns an Answer holding its ``text``, its ``data`` and its
    ``artifacts``, an answer, a clarifying question or a refusal naming its
    cause alike, and :meth:`Agent.ask <association.query.agent.Agent.ask>`
    stamps the question, the path, the timing, the decisions, the notes and
    the shape on it.

    ``text`` is exactly what the CLI prints, unchanged - the final prose, so
    the fast path makes NO model call after the normalizer. ollama keeps one
    KV cache slot per model, so a second call with a different system prompt
    evicts the reader's prefix (measured on the normalizer's model: three
    consecutive calls ran 11.6s / 1.3s / 1.7s, but
    interleaving one narration call put the next back to 11.2s). Phrasing every answer on the answer side also removes the
    last place on this path where a number could be invented. Everything
    else is what the CLI never had a way to show: which path answered, and -
    on the fast path only - the shape it was read and said as and the
    structured data the reader built its sentence from: resolved names and
    numbers, no ids and no schema, which tests assert against.

    ``shape`` and ``data`` are None when ``answered_by`` is ``"refused"``,
    since only a reader or the compiler produces them; ``shape`` is None on
    an answer too where the words came to no point (a refusal the reading
    said, a name the question never held). Callers must handle that rather
    than assume a shape.

    .. versionadded:: 2.0.0

    .. versionchanged:: 5.0.0
       ``answered_by`` is ``"refused"``, never ``"agent"``, for a question
       nothing here reads.

    .. versionchanged:: 6.0.0
       Breaking: ``intent`` is gone - the retired intent's name, the page's
       label until Phase 4, step 1 keyed the page on :attr:`shape`. The
       key the words asked is the Reading's (``Reading.asked``), the shape
       the answer was read as is :attr:`shape`.

    .. versionchanged:: 6.0.0
       Breaking: keyword-only, and the sayer's answer - ``answer.Reply``,
       the answer side's own record of the text (``answer``), the data and
       the files, is folded into it (Phase 4, step 4). ``question``,
       ``answered_by`` and ``timing`` default to an answer not yet stamped
       (``""``, ``"fast"``, untimed).
    """

    question: str = ""
    text: str
    answered_by: AnsweredBy = "fast"
    timing: Timing = Timing()
    data: dict[str, Any] | None = None
    artifacts: list[Artifact] = field(default_factory=list)
    #: The basename of the ``.history/`` file this answer was recorded to -
    #: what a client names to annotate it (``POST /api/notes``) - or None
    #: before the record is written.
    #:
    #: .. versionadded:: 4.4.0
    history_file: str | None = None
    #: What was decided on the way to this answer - every reading of the
    #: question and override of a field the model came back with, as values
    #: (:class:`~association.query.decisions.Decision`), in the order made.
    #:
    #: A decision the answer states - a name read as one player, a season
    #: redirected - is here too, with its ``kind``
    #: (:func:`association.query.notes.decided`).
    #:
    #: .. versionadded:: 4.4.0
    decisions: tuple[Decision, ...] = ()
    #: What the answer says about its data beside the numbers - a game with
    #: no box score, a table's floor, what a column means - each a kind and
    #: its facts (:class:`~association.query.notes.Note`), in the order
    #: written. The sentences themselves are in ``text``.
    #:
    #: .. versionadded:: 5.0.0
    notes: tuple[Note, ...] = ()
    #: The planned point's shape (:class:`~association.query.reading.PointShape`:
    #: the relation it reads, the shape - ``scalar``, ``rows``, ``ranking``,
    #: ``comparison``, ``split``, ``runs`` or ``chart`` - and what it is
    #: ``by``), the one the answer side chose its reader by
    #: (:func:`~association.query.compose.plan.planned_shape`). What
    #: the page renders the answer's ``data`` by.
    #:
    #: .. versionadded:: 6.0.0
    shape: PointShape | None = None
    #: The other readings of a default the answer read by, each offered as
    #: a question that asks it (:class:`~association.query.reading.Alternative`,
    #: decision D23): the season a career the words imply could have been,
    #: or the career a defaulted season could have been. Only those the
    #: answering loop read again and found reach what they say - the page's
    #: buttons and the CLI's "Also:" lines; the text says the default.
    #:
    #: .. versionadded:: 7.0.0
    alternatives: tuple[Alternative, ...] = ()
