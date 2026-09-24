"""Records every `query` run's command, full event trace, and timing
metrics to a file under `.history/`, named with the build that produced it and
a random hash - regardless of
whether `--verbose` was passed, so a confusing or failed run's full evidence
is always on disk afterward, not just whatever happened to print to the
terminal at the time.

One file per `ask()` call (one real "run" to debug), not one per CLI
invocation - a caller that asks several questions calls `ask()` once each, and every
gets its own history file, the same as a one-shot `query` call would."""

from __future__ import annotations

import functools
import subprocess
import sys
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from association import __version__

DEFAULT_HISTORY_DIR = Path(".history")


@functools.cache
def build_id() -> str:
    """What this run was produced by: the short commit where the source is a git
    checkout, else the released version.

    A history file is evidence about a behavior, and the version alone cannot
    say which behavior - dozens of commits share ``4.2.0``, and the answers
    this project keeps changing are exactly the ones a reader needs to tie back
    to a build. A dirty tree is marked, because a run from uncommitted work is
    not reproducible from the commit alone.

    ``git`` is asked about the **package's own directory**, never the caller's
    working directory: running ``association query`` inside some unrelated
    checkout must not stamp that repository's commit onto this run. Anything
    that goes wrong - no git, no checkout, a wheel installed outside one - falls
    back to the version, since a history file that fails to write is far worse
    than one identified a little more loosely. Cached: it cannot change inside a
    process, and ``ask()`` would otherwise pay a subprocess per question.

    .. versionadded:: 4.3.0
    """
    package = Path(__file__).resolve().parent
    try:
        commit = subprocess.run(
            ["git", "-C", str(package), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "-C", str(package), "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return __version__
    return f"{commit}-dirty" if dirty else commit


def echo_to_stderr(line: str) -> None:
    """The default trace sink: what a terminal wants, and what this class did
    unconditionally before a sink could be supplied.

    .. versionadded:: 2.0.0
    """
    print(line, file=sys.stderr)


class RunHistory:
    """`log()` always records a line; it's only ever ALSO handed to `sink`
    when `verbose` is true - the file gets everything either way.

    `sink` is where a live trace goes. It defaults to stderr, which is the only
    place it ever went; a server passes its own so the same lines can be
    forwarded to a browser as they happen, instead of being recovered from the
    history file after the run is over.

    .. versionchanged:: 2.0.0
       Added ``sink``.
    """

    def __init__(self, verbose: bool, history_dir: Path = DEFAULT_HISTORY_DIR, sink: Callable[[str], None] = echo_to_stderr) -> None:
        self.verbose = verbose
        self.history_dir = history_dir
        self.sink = sink
        self.lines: list[str] = []
        self.model_calls: int = 0
        self.model_seconds: float = 0.0
        self.tool_calls: int = 0
        self.tool_seconds: float = 0.0
        self._start = time.monotonic()

    def log(self, line: str) -> None:
        """Record a trace line, and pass it to ``sink`` when ``verbose``."""
        self.lines.append(line)
        if self.verbose:
            self.sink(line)

    def record_model_call(self, elapsed: float) -> None:
        """Count one model round trip. These dominate wall time, so the count
        matters as much as the seconds."""
        self.model_calls += 1
        self.model_seconds += elapsed
        self.log(f"  [timing] model inference #{self.model_calls}: {elapsed:.2f}s")

    def record_tool_call(self, name: str, elapsed: float) -> None:
        """Count one tool or template call. Typically sub-millisecond, which is
        the point: the timing split shows where the time is not going."""
        self.tool_calls += 1
        self.tool_seconds += elapsed
        self.log(f"  [timing] {name}: {elapsed:.2f}s")

    @property
    def total_seconds(self) -> float:
        """Wall time since this run started."""
        return time.monotonic() - self._start

    def summary_line(self) -> str:
        """The one-line summary printed to stderr after every run, splitting
        total time into model inference versus tool execution."""
        return (
            f"[timing] total {self.total_seconds:.2f}s - "
            f"model {self.model_seconds:.2f}s ({self.model_calls} call{'s' if self.model_calls != 1 else ''}), "
            f"tools {self.tool_seconds:.2f}s ({self.tool_calls} call{'s' if self.tool_calls != 1 else ''})"
        )

    def write(self, command: str, model: str, think: bool, question: str, answer: str, router_model: str | None = None) -> Path:
        """Always called (from a finally block) regardless of how ask() exited -
        an exception's traceback text as `answer` is exactly the "failed run"
        evidence this exists to keep."""
        self.history_dir.mkdir(parents=True, exist_ok=True)
        # The build leads the name so `ls` groups a run with the code that
        # produced it, and so one commit's runs can be selected with a glob.
        path = self.history_dir / f"{build_id()}-{uuid.uuid4().hex[:16]}.log"
        started = datetime.fromtimestamp(time.time() - self.total_seconds, tz=timezone.utc).isoformat()
        parts = [
            f"command: {command}",
            f"build: {build_id()}",
            f"model: {model} (think={think})" + (f", router: {router_model}" if router_model else ""),
            f"started: {started}",
            f"question: {question}",
            "=" * 80,
            "\n".join(self.lines),
            "=" * 80,
            self.summary_line(),
            "=" * 80,
            "answer:",
            answer,
        ]
        path.write_text("\n".join(parts) + "\n")
        return path


def append_note(path: Path, note: str) -> str:
    """Append one timestamped annotation to an already-written history file -
    what is wrong with that run's answer, or a thought on how it should look,
    recorded beside the trace and the answer it is about rather than living
    only in the head of whoever noticed it.

    Appending, never rewriting: :meth:`RunHistory.write` is the only thing
    that lays down a run's own trace and summary, so a note added afterward is
    layered on top of a file that already says what happened, not mixed into
    it. Calling this more than once on the same file is how more than one note
    ends up on the same answer - each call adds its own line, none of them
    overwritten by the next.

    ``note`` always lands on a single line: an embedded backslash or newline
    is escaped (``\\`` and ``\\n`` respectively) rather than written literally,
    so a multi-line note can never be mistaken for a second note, or for the
    trace text around it - the whole reason this file records one thing per
    line. Returns the exact line written, so a caller does not have to
    re-derive it just to report what happened.

    .. versionadded:: 4.4.0
    """
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    escaped = note.replace("\\", "\\\\").replace("\n", "\\n")
    line = f"[note {timestamp}] {escaped}"
    with path.open("a") as f:
        f.write(line + "\n")
    return line
