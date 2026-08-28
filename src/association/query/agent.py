"""The tool-calling loop: holds conversation state, dispatches tool calls, and
guards against the model writing SQL as prose instead of actually running it."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import ollama

from .prompt import SYSTEM_PROMPT, TOOLS
from .toolbox import Toolbox

MAX_TOOL_ITERATIONS = 8
MAX_AUTO_SQL_RECOVERIES = 2  # cap on auto-executing SQL the model wrote instead of calling run_sql
MAX_HISTORY_MESSAGES = 40  # trim oldest turns once conversation grows past this, keep system prompt
NUM_CTX = 8192  # local model's default (4096) is too small for multi-turn + tool-result JSON

_SQL_FENCE_RE = re.compile(r"```(?:sql)?\s*\n?(.*?)```", re.IGNORECASE | re.DOTALL)


def _extract_unrun_sql(text: str) -> str | None:
    """Find a SELECT/WITH query the model wrote as plain text instead of calling
    run_sql - a code-fenced block, or (fallback) the whole reply if it's bare SQL."""
    for match in _SQL_FENCE_RE.finditer(text or ""):
        candidate = match.group(1).strip()
        head = candidate[:10].lstrip().upper()
        if head.startswith("SELECT") or head.startswith("WITH"):
            return candidate
    stripped = (text or "").strip()
    head = stripped[:10].upper()
    if head.startswith("SELECT") or head.startswith("WITH"):
        return stripped
    return None


class Agent:
    """Holds conversation state across turns so interactive mode has real
    multi-turn memory (e.g. "what about for 2025?" referring to the prior question)."""

    def __init__(self, model: str, db_path: str, out_dir: Path, verbose: bool = False, think: bool = False):
        self.model = model
        self.verbose = verbose
        self.think = think
        self.toolbox = Toolbox(db_path, out_dir)
        self.dispatch = {
            "describe_table": self.toolbox.describe_table,
            "run_sql": self.toolbox.run_sql,
            "render_shot_chart": self.toolbox.render_shot_chart,
        }
        self.messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]

    def reset(self) -> None:
        self.messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    def _trim_history(self) -> None:
        # keep the system prompt (index 0) plus the most recent messages
        if len(self.messages) > MAX_HISTORY_MESSAGES:
            self.messages = [self.messages[0]] + self.messages[-(MAX_HISTORY_MESSAGES - 1):]

    def ask(self, question: str) -> str:
        self.messages.append({"role": "user", "content": question})
        auto_recoveries = 0

        for _ in range(MAX_TOOL_ITERATIONS):
            chat_kwargs = dict(model=self.model, messages=self.messages, tools=TOOLS, options={"num_ctx": NUM_CTX})
            if self.think:
                chat_kwargs["think"] = True
            try:
                response = ollama.chat(**chat_kwargs)
            except ollama.ResponseError as exc:
                if self.think and "does not support thinking" in str(exc):
                    raise SystemExit(
                        f"Error: model {self.model!r} does not support --think "
                        "(try a thinking-capable model, e.g. qwen3:8b)."
                    ) from None
                raise
            msg = response.message
            if self.think and self.verbose and msg.thinking:
                print(f"  [thinking] {msg.thinking}", file=sys.stderr)
            self.messages.append(msg.model_dump())

            if not msg.tool_calls:
                unrun_sql = _extract_unrun_sql(msg.content or "")
                if unrun_sql and auto_recoveries < MAX_AUTO_SQL_RECOVERIES:
                    auto_recoveries += 1
                    if self.verbose:
                        print(f"  -> (auto) running SQL the model wrote instead of calling run_sql: {unrun_sql!r}", file=sys.stderr)
                    result = self.toolbox.run_sql(unrun_sql)
                    self.messages.append(
                        {
                            "role": "user",
                            "content": (
                                "You wrote SQL directly in your reply instead of calling the run_sql "
                                "tool, so nothing had actually run. I ran it for you - here are the "
                                "real results. Give your final answer using this data now, and call "
                                "run_sql yourself next time instead of printing a query:\n" + result
                            ),
                        }
                    )
                    continue
                self._trim_history()
                return msg.content or ""

            for call in msg.tool_calls:
                name = call.function.name
                args = call.function.arguments or {}
                if self.verbose:
                    print(f"  -> {name}({args})", file=sys.stderr)
                fn = self.dispatch.get(name)
                if fn is None:
                    result = f"Error: unknown tool {name!r}"
                else:
                    try:
                        result = fn(**args)
                    except Exception as exc:
                        result = f"Error calling {name}: {exc}"
                self.messages.append({"role": "tool", "content": str(result)})

        self._trim_history()
        return "Gave up after too many tool-call iterations."
