"""Runs one question at a time against a shared Agent.

The serialization is not caution, it is a measurement. ollama keeps a single KV
cache slot per model: three consecutive router calls run 11.6s / 1.3s / 1.7s,
but slipping one call with a different system prompt in between puts the next
back to 11.2s. Two browser tabs asking questions at the same time would do
exactly that to each other, and the second question would pay for the first.
So the server answers one at a time and tells a waiting request it is waiting,
rather than letting them interleave and both come back slow.

.. versionadded:: 2.0.0
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from ..query.answer import Answer

if TYPE_CHECKING:
    # For the annotation only. Importing the Agent at runtime brings ollama with
    # it, and web/app.py imports this module: the API layer must load no model
    # client (the "ollama stays out of the API layer" contract in pyproject.toml).
    from ..query.agent import Agent


def discard(line: str) -> None:
    """A trace sink that drops the line. The default for a server, which has no
    terminal worth writing to - every real request supplies its own.

    .. versionadded:: 2.0.0
    """


@dataclass(frozen=True)
class Answered:
    """One question's answer, plus the name of the history file it was
    recorded to.

    ``history_file`` is a *basename*, never a full path - the same discipline
    :func:`association.web.app.as_response` already keeps for an artifact's
    ``name``, so the API never has to redact a filesystem path a client has no
    way to read. It is None only when nothing that looked like a history
    file's own trace line was seen at all, which real traffic never produces:
    :meth:`association.query.agent.Agent.ask` always writes one, on every
    return path including an exception, and always reports it through the
    trace - see :meth:`AgentRunner.ask`.

    .. versionadded:: 4.4.0
    """

    answer: Answer
    history_file: str | None


class Answerer(Protocol):
    """What the API layer needs from a query engine, and no more.

    A Protocol rather than the Agent itself so the tests can substitute a stub:
    the whole test suite is offline, and an Agent would want ollama.

    .. versionadded:: 2.0.0

    .. versionchanged:: 4.4.0
       ``ask`` returns :class:`Answered` rather than a bare
       :class:`~association.query.answer.Answer`, so a caller can name the
       history file an answer was recorded to without a second, racy call.
    """

    def ask(self, question: str, label: str, trace: Callable[[str], None] = discard) -> Answered:
        """Answer one question, reporting progress lines to ``trace``.

        ``trace`` is optional so ``POST /api/ask``, which has nowhere to stream
        to, does not have to invent a sink to throw away.
        """
        ...

    @property
    def busy(self) -> bool:
        """Whether a question is being answered right now."""
        ...

    @property
    def ready(self) -> bool:
        """Whether the model backend can be reached at all."""
        ...


class AgentRunner:
    """An :class:`association.query.agent.Agent` behind a lock.

    The Agent is built once and reused, so the DuckDB connection and ollama's
    keep-alive are not re-established per request. What is NOT reused is the
    conversation: see :meth:`ask`.

    .. versionadded:: 2.0.0
    """

    def __init__(self, agent: Agent) -> None:
        self.agent = agent
        self._lock = threading.Lock()

    @property
    def busy(self) -> bool:
        """Whether a question is in flight, so a new one can be told it is
        queued instead of appearing to hang before it has even started."""
        return self._lock.locked()

    @property
    def ready(self) -> bool:
        """Whether ollama answers at all.

        Listing models does not load one, so this stays cheap enough for a
        health check. It lives here rather than in the API layer so the API
        layer imports no model client at all, which is what keeps the web
        tests offline by construction rather than by discipline.
        """
        import ollama

        try:
            ollama.list()
        except Exception:  # noqa: BLE001 - any failure to reach ollama means not ready, whatever the transport raised
            return False
        return True

    def ask(self, question: str, label: str, trace: Callable[[str], None] = discard) -> Answered:
        """Answer one question, with everything else waiting its turn.

        The trace sink is swapped rather than passed down because it belongs to
        the Agent, not to a call - and swapping it is safe here only because
        this holds the lock while it does so. Restored afterwards so a request
        that has finished cannot keep writing into a closed stream.

        Every request starts a fresh conversation, which is what the docs
        already promised ("each message is a new question") and what the code
        did not do: one Agent reused across requests accumulated ONE history
        shared by every browser that connected. Two costs, and the second is
        the serious one - the history is context nobody asked to spend, and
        ``Agent.last_question`` goes to the router as ``previous_question``, so
        "what about jokic" from one person was routed against whatever a
        stranger had asked before it. Reset under the lock, where no question
        is in flight to lose its own history mid-answer.

        Giving the web UI real multi-turn memory means giving it per-client
        conversations, which needs a session the API does not have yet. Until
        then this is stateless on purpose rather than by accident.

        ``Agent.ask`` (``query/agent.py``) never returns the history file it
        wrote, only names it in one exact trace line on its way out - on every
        return path, including an exception. The wrapped sink below reads that
        one line as it goes by and keeps the name in a variable local to this
        call, never on ``self``: two questions can only ever be mid-flight here
        one at a time (the whole point of the lock), but the lock is released
        on the way OUT of this method, before the caller can read anything
        back, so a name stored on the instance would be a real race against
        the very next call landing here before this one's caller has read it.
        A local has no such window.

        .. versionchanged:: 2.1.0
           Resets the conversation per request instead of sharing one across
           every client.

        .. versionchanged:: 4.4.0
           Returns :class:`Answered`, naming the history file the answer was
           recorded to, instead of a bare
           :class:`~association.query.answer.Answer`.
        """
        history_file: list[str | None] = [None]

        def capture(line: str) -> None:
            """Forward one trace line to ``trace`` exactly as before, and, on
            the way past, keep it if it is the one line naming this call's own
            history file."""
            found = _ask_history_file(line)
            if found is not None:
                history_file[0] = found
            trace(line)

        with self._lock:
            self.agent.reset_conversation()
            previous, self.agent.trace = self.agent.trace, capture
            try:
                answer = self.agent.ask(question, label=label)
            finally:
                self.agent.trace = previous
        return Answered(answer=answer, history_file=history_file[0])


_HISTORY_TRACE_LINE = re.compile(r"^\[history\] (\S+)")
"""What ``Agent.ask``'s own finally block always writes to the trace, verbatim,
as its very last line (``query/agent.py``, out of scope for this change):
``f"[history] {path}  {history.summary_line()}"``. ``\\S+`` is enough to
isolate ``path`` because :func:`association.query.history.RunHistory.write`
builds it from :func:`~association.query.history.build_id` and a hex
``uuid4`` - never anything containing whitespace. Private, so not itself a
compatibility promise - it exists to be perturbed and re-checked the day
``Agent.ask``'s trace line changes shape, not to be read as one.
"""


def _ask_history_file(line: str) -> str | None:
    """The history file's basename, if ``line`` is :meth:`AgentRunner.ask`'s
    one chance to see it - None for every other trace line, which is most of
    them.

    Only the basename: a client addresses a history file by name over
    ``POST /api/notes`` exactly the way it already addresses an artifact over
    ``GET /api/artifacts/{name}`` (:func:`association.web.app.artifact_path`),
    never by the server's own path to it.
    """
    match = _HISTORY_TRACE_LINE.match(line)
    return Path(match.group(1)).name if match else None
