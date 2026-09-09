"""What a question produces, as values rather than as printed text.

`Agent.ask` used to return a string and print everything else - the trace to
stderr, the chart's file path formatted into the middle of a sentence. That is
enough for a terminal and nothing else. These are the shapes any other caller
(the web API in 2.0, a notebook, a test) needs: the text that was already
being returned, plus the intent and structured data the fast path had already
computed and thrown away, plus the files that were written.

Nothing here imports the rest of the query package, so templates, renderers
and the agent can all depend on it without a cycle.

.. versionadded:: 2.0.0
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

AnsweredBy = Literal["fast", "agent"]
"""Which of the two paths produced an answer.

``"fast"`` means router -> template: deterministic, typically 1-2s, and the
only path that populates :attr:`Answer.intent` and :attr:`Answer.data`.
``"agent"`` means the tool-calling fall-through, where a model wrote the SQL.
The difference is the most useful single thing a reader can know about an
answer's reliability, so it is carried out rather than inferred.

.. versionadded:: 2.0.0
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
class Timing:
    """Where a run's wall time went, split into model inference and everything
    else. The call counts matter as much as the seconds: on the fast path one
    model call is the whole cost, and a second one would mean the KV cache
    eviction described in :class:`association.query.templates.TemplateResult`.

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

    ``intent`` and ``data`` are None when ``answered_by`` is ``"agent"``, since
    only a template produces them. Callers must handle that rather than assume
    a shape; it is the same fall-through that has always existed, now visible.

    .. versionadded:: 2.0.0
    """

    question: str
    text: str
    answered_by: AnsweredBy
    timing: Timing
    intent: str | None = None
    data: dict[str, Any] | None = None
    artifacts: list[Artifact] = field(default_factory=list)
