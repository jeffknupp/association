"""Records every `query` run's command, full event trace, and timing
metrics to a file under `.history/`, named with a random hash - regardless of
whether `--verbose` was passed, so a confusing or failed run's full evidence
is always on disk afterward, not just whatever happened to print to the
terminal at the time.

One file per `ask()` call (one real "run" to debug), not one per CLI
invocation - a caller that asks several questions calls `ask()` once each, and every
gets its own history file, the same as a one-shot `query` call would."""

from __future__ import annotations

import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_HISTORY_DIR = Path(".history")


class RunHistory:
    """`log()` always records a line; it's only ever ALSO printed to stderr
    when `verbose` is true - the file gets everything either way."""

    def __init__(self, verbose: bool, history_dir: Path = DEFAULT_HISTORY_DIR) -> None:
        self.verbose = verbose
        self.history_dir = history_dir
        self.lines: list[str] = []
        self.model_calls: int = 0
        self.model_seconds: float = 0.0
        self.tool_calls: int = 0
        self.tool_seconds: float = 0.0
        self._start = time.monotonic()

    def log(self, line: str) -> None:
        """Record a trace line, and echo it to stderr when ``verbose``."""
        self.lines.append(line)
        if self.verbose:
            print(line, file=sys.stderr)

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
        path = self.history_dir / f"{uuid.uuid4().hex[:16]}.log"
        started = datetime.fromtimestamp(time.time() - self.total_seconds, tz=timezone.utc).isoformat()
        parts = [
            f"command: {command}",
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
