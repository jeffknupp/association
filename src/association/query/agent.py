"""The tool-calling loop: holds conversation state, dispatches tool calls, and
guards against the model writing SQL as prose instead of actually running it."""

from __future__ import annotations

import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import ollama

from .prompt import SYSTEM_PROMPT, TOOLS
from .toolbox import Toolbox

MAX_TOOL_ITERATIONS = 8
MAX_AUTO_SQL_RECOVERIES = 2  # cap on auto-executing SQL the model wrote instead of calling run_sql
MAX_ERROR_RECOVERIES = 2  # cap on nudging a retry after a tool error, instead of letting it fabricate an answer
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


def _is_tool_error(result: str) -> bool:
    """A dispatched tool's result string that signals failure rather than real
    data - toolbox.run_sql returns 'SQL error: ...' for a DB error, the dispatch
    loop itself returns 'Error ...' for an unknown tool or a raised exception."""
    return result.startswith("Error") or result.startswith("SQL error")


class Agent:
    """Holds conversation state across turns so interactive mode has real
    multi-turn memory (e.g. "what about for 2025?" referring to the prior question)."""

    def __init__(self, model: str, db_path: str, out_dir: Path, verbose: bool = False, think: bool = False):
        self.model = model
        self.verbose = verbose
        self.think = think
        self.toolbox = Toolbox(db_path, out_dir)
        # heterogeneous signatures dispatched generically via **args below -
        # a specific Callable type would make mypy check the wrong signature.
        self.dispatch: dict[str, Callable[..., str]] = {
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
        error_recoveries = 0
        pending_error: str | None = None

        for _ in range(MAX_TOOL_ITERATIONS):
            chat_kwargs: dict[str, Any] = dict(model=self.model, messages=self.messages, tools=TOOLS, options={"num_ctx": NUM_CTX})
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
            dumped = msg.model_dump()
            # Don't replay past reasoning back into the model's own context - it's
            # scratch work for the turn that produced it, not memory it needs later,
            # and re-sending it costs real prompt-eval time on every subsequent
            # iteration (confirmed live: stripping it cut a follow-up iteration's
            # prompt eval from ~5.6s to ~0.9s on an 8-token-context conversation -
            # the effect compounds with each further tool-call round).
            dumped.pop("thinking", None)
            self.messages.append(dumped)

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
                if not unrun_sql and pending_error and error_recoveries < MAX_ERROR_RECOVERIES:
                    # The last tool call errored and the model never got real data
                    # afterward, yet it's trying to finalize anyway - confirmed live,
                    # this produced a fabricated answer with literal "[Player Name 1]"
                    # / "[NetPoints Value]" placeholder text after a column-not-found
                    # error, presented as if it were real. Never let a finalize
                    # through right after an unrecovered error - nudge a retry
                    # instead of trusting whatever it wrote.
                    error_recoveries += 1
                    if self.verbose:
                        print("  -> (guard) blocked a finalize right after a tool error, nudging a retry", file=sys.stderr)
                    self.messages.append(
                        {
                            "role": "user",
                            "content": (
                                "Your last tool call failed, so you do not have real data yet - do "
                                "not answer with placeholder or made-up values. Either call run_sql "
                                "again with a corrected query, or say plainly that you couldn't get "
                                "the data. The error was:\n" + pending_error
                            ),
                        }
                    )
                    continue
                self._trim_history()
                if unrun_sql:
                    # Recovery cap hit and the model is STILL just printing SQL
                    # instead of running it - returning msg.content as-is would
                    # read as "I'm about to do this" while doing nothing
                    # (confirmed live: a real answer ending in "Let's run this
                    # corrected query" that never ran). Say plainly that it
                    # didn't work, with the last attempt shown, rather than a
                    # reply that only looks like an in-progress action.
                    return (
                        "I wasn't able to get a working query after a few attempts. "
                        "The last one I tried was:\n\n```sql\n" + unrun_sql + "\n```\n\n"
                        "You can run it yourself, or try rephrasing the question."
                    )
                if pending_error:
                    # Recovery cap hit and it's STILL trying to finalize right after
                    # an unrecovered error - say so honestly rather than returning
                    # whatever it fabricated.
                    return "I ran into an error retrieving that data and wasn't able to recover. The last error was:\n\n" + pending_error
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
                result = str(result)
                if name == "run_sql":
                    # Only run_sql's outcome drives the fabrication guard below -
                    # a describe_table miss (e.g. an unknown table name) doesn't
                    # mean the model lacks real data, since an earlier run_sql
                    # call in the same turn may have already succeeded.
                    pending_error = result if _is_tool_error(result) else None
                self.messages.append({"role": "tool", "content": result})

        self._trim_history()
        return "Gave up after too many tool-call iterations."
