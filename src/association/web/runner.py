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

import threading
from collections.abc import Callable
from typing import Protocol

from ..query.agent import Agent
from ..query.answer import Answer


def discard(line: str) -> None:
    """A trace sink that drops the line. The default for a server, which has no
    terminal worth writing to - every real request supplies its own.

    .. versionadded:: 2.0.0
    """


class Answerer(Protocol):
    """What the API layer needs from a query engine, and no more.

    A Protocol rather than the Agent itself so the tests can substitute a stub:
    the whole test suite is offline, and an Agent would want ollama.

    .. versionadded:: 2.0.0
    """

    def ask(self, question: str, label: str, trace: Callable[[str], None] = discard) -> Answer:
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
    keep-alive are not re-established per request.

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
        except Exception:
            return False
        return True

    def ask(self, question: str, label: str, trace: Callable[[str], None] = discard) -> Answer:
        """Answer one question, with everything else waiting its turn.

        The trace sink is swapped rather than passed down because it belongs to
        the Agent, not to a call - and swapping it is safe here only because
        this holds the lock while it does so. Restored afterwards so a request
        that has finished cannot keep writing into a closed stream.
        """
        with self._lock:
            previous, self.agent.trace = self.agent.trace, trace
            try:
                return self.agent.ask(question, label=label)
            finally:
                self.agent.trace = previous
