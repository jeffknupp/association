"""What a question produces, as values rather than as printed text.

`Agent.ask` used to return a string and print everything else - the trace to
stderr, the chart's file path formatted into the middle of a sentence. That is
enough for a terminal and nothing else. These are the shapes any other caller
(the web API in 2.0, a notebook, a test) needs: the text that was already
being returned, plus the intent and structured data the fast path had already
computed and thrown away, plus the files that were written.

Nothing here imports the rest of the query package, so templates, renderers
and the answering loop can all depend on it without a cycle.

.. versionadded:: 2.0.0
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from association.query.decisions import Decision
from association.query.notes import Note

if TYPE_CHECKING:
    import duckdb

AnsweredBy = Literal["fast", "refused"]
"""Whether the question was answered, or refused with no reading of it.

``"fast"`` means parser -> template or compiler: deterministic, typically
1-2s, and the only value that populates :attr:`Answer.intent` and
:attr:`Answer.data`. It covers a template's or the compiler's own refusal
too - a clarification, a "no match", a shape nothing here reads named by its
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
    out of the message it was formatted into, which is what the shot chart
    renderer used to force.

    .. versionadded:: 2.0.0
    """

    kind: str
    path: Path

    @property
    def name(self) -> str:
        """The file's basename, which is how it is addressed over HTTP."""
        return self.path.name


@dataclass(frozen=True)
class RenderResult:
    """What a chart renderer returns: what to say, and what was drawn.

    ``artifact`` is None when nothing was drawn - no shots matched the filters,
    say. That is a real answer, not an error, so the message still stands on
    its own.

    .. versionadded:: 2.0.0
    """

    message: str
    artifact: Artifact | None


@dataclass(frozen=True)
class AnswerContext:
    """What a reader is given: the warehouse, and somewhere to write output.

    Readers took a bare connection until the shot chart needed an output
    directory too. A small context rather than the whole answering loop keeps
    the readers testable with a plain in-memory DuckDB connection.

    .. versionadded:: 5.0.0
       ``association.query.templates.TemplateContext`` until the templates
       were gone (Phase 2, step 6).
    """

    con: duckdb.DuckDBPyConnection
    out_dir: Path


@dataclass
class Reply:
    """What the answer side hands the answering loop: the final prose, the
    same result as values, and the files it wrote - an answer, a clarifying
    question or a refusal naming its cause alike.

    ``answer`` is the final prose, so the fast path makes NO model call after
    the normalizer. Required, not optional: ollama keeps one KV cache slot per
    model, so a second call with a different system prompt evicts the
    reader's prefix (measured: three consecutive router calls run 11.6s /
    1.3s / 1.7s, but interleaving one narration call puts the next back to
    11.2s). Phrasing every answer here also removes the last place on this
    path where a number could be invented.

    ``data`` is the same result as structured values - resolved names and
    numbers, no ids and no schema. Tests assert against it, and it is carried
    out to the caller in :class:`Answer`.

    ``artifacts`` is whatever the reader wrote to disk - a chart, or nothing.

    .. versionadded:: 5.0.0
       ``association.query.templates.TemplateResult`` until the templates
       were gone (Phase 2, step 6), with the same fields.
    """

    data: dict[str, Any]
    answer: str
    artifacts: list[Artifact] = field(default_factory=list)


@dataclass(frozen=True)
class Timing:
    """Where a run's wall time went, split into model inference and everything
    else. The call counts matter as much as the seconds: on the fast path one
    model call is the whole cost, and a second one would mean the KV cache
    eviction described in :class:`Reply`.

    .. versionadded:: 2.0.0
    """

    total_seconds: float
    model_seconds: float
    model_calls: int
    tool_seconds: float
    tool_calls: int


@dataclass(frozen=True)
class Answer:
    """One question's complete result.

    ``text`` is exactly what the CLI prints, unchanged. Everything else is what
    the CLI never had a way to show: which path answered, and - on the fast
    path only - the intent it was classified as and the structured data the
    template built its sentence from.

    ``intent`` and ``data`` are None when ``answered_by`` is ``"refused"``,
    since only a template or the compiler produces them. Callers must handle
    that rather than assume a shape.

    .. versionadded:: 2.0.0

    .. versionchanged:: 5.0.0
       ``answered_by`` is ``"refused"``, never ``"agent"``, for a question
       nothing here reads.
    """

    question: str
    text: str
    answered_by: AnsweredBy
    timing: Timing
    intent: str | None = None
    data: dict[str, Any] | None = None
    artifacts: list[Artifact] = field(default_factory=list)
    #: The basename of the ``.history/`` file this answer was recorded to -
    #: what a client names to annotate it (``POST /api/notes``) - or None
    #: before the record is written.
    #:
    #: .. versionadded:: 4.4.0
    history_file: str | None = None
    #: What was decided on the way to this answer - every reading of the
    #: question and override of a routed field, as values
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
