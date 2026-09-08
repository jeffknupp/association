"""The tool-calling loop: holds conversation state, dispatches tool calls, and
guards against the model writing SQL as prose instead of actually running it."""

from __future__ import annotations

import re
import shlex
import sys
import time
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any

import ollama

from .history import DEFAULT_HISTORY_DIR, RunHistory
from .keepalive import KEEP_ALIVE
from .models import DEFAULT_ROUTER_MODEL
from .prompt import NUM_CTX, TOOLS, build_system_prompt
from .router import route
from .templates import TEMPLATES, TemplateContext, TemplateUnsupported, check_scope
from .toolbox import Toolbox

MAX_TOOL_ITERATIONS = 8
MAX_AUTO_SQL_RECOVERIES = 2  # cap on auto-executing SQL the model wrote instead of calling run_sql
MAX_ERROR_RECOVERIES = 2  # cap on nudging a retry after a tool error, instead of letting it fabricate an answer
MAX_HISTORY_MESSAGES = 40  # trim oldest turns once conversation grows past this, keep system prompt

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

    def __init__(
        self,
        model: str,
        db_path: str,
        out_dir: Path,
        verbose: bool = False,
        think: bool = False,
        history_dir: Path = DEFAULT_HISTORY_DIR,
        fast_path: bool = True,
        router_model: str = DEFAULT_ROUTER_MODEL,
    ):
        self.model = model
        self.router_model = router_model
        self.verbose = verbose
        self.think = think
        self.history_dir = history_dir
        self.fast_path = fast_path
        self.last_question: str | None = None
        self.toolbox: Toolbox = Toolbox(db_path, out_dir)
        # heterogeneous signatures dispatched generically via **args below -
        # a specific Callable type would make mypy check the wrong signature.
        self.dispatch: dict[str, Callable[..., str]] = {
            "describe_table": self.toolbox.describe_table,
            "run_sql": self.toolbox.run_sql,
            "get_leaderboard": self.toolbox.get_leaderboard,
            "render_shot_chart": self.toolbox.render_shot_chart,
        }
        # Rebuilt per question in _ask_inner; this is the always-on core only,
        # so a fresh Agent is usable before any question has been asked.
        self.messages: list[dict[str, Any]] = [{"role": "system", "content": build_system_prompt("")}]

    def reset(self) -> None:
        """Drop the conversation, keeping the agent usable for a fresh question."""
        self.messages = [{"role": "system", "content": build_system_prompt("")}]
        self.last_question = None

    def _trim_history(self) -> None:
        # keep the system prompt (index 0) plus the most recent messages
        if len(self.messages) > MAX_HISTORY_MESSAGES:
            self.messages = [self.messages[0]] + self.messages[-(MAX_HISTORY_MESSAGES - 1) :]

    def ask(self, question: str) -> str:
        """Wraps _ask_inner so a RunHistory is ALWAYS written on the way out -
        including on an exception - regardless of --verbose. See history.py:
        one file per call, named with a random hash under .history/, meant to
        make a confusing or failed run's full evidence easy to find afterward
        rather than lost to whatever happened to print to the terminal."""
        history = RunHistory(self.verbose, self.history_dir)
        command = shlex.join(sys.argv)
        answer = ""
        try:
            answer = self._ask_inner(question, history)
            return answer
        except Exception:
            answer = "EXCEPTION:\n" + traceback.format_exc()
            raise
        finally:
            path = history.write(command=command, model=self.model, think=self.think, question=question, answer=answer, router_model=self.router_model)
            print(f"[history] {path}  {history.summary_line()}", file=sys.stderr)

    def _try_fast_path(self, question: str, history: RunHistory) -> str | None:
        """Route -> deterministic template -> answer. Returns None to fall
        through to the agent: an unported intent, slots that fail validation, or
        any router or template failure. Falling through costs one ~1-2s round
        trip and changes no answer."""
        if not self.fast_path:
            return None
        t0 = time.monotonic()
        routed = route(self.router_model, question, previous_question=self.last_question)
        history.record_model_call(time.monotonic() - t0)
        if routed is None:
            history.log("  -> (router) no usable classification, falling through to the agent")
            return None
        handler = TEMPLATES.get(routed.intent)
        history.log(f"  -> (router) intent={routed.intent!r} slots={routed.slots}" + ("" if handler else " - not ported yet, falling through"))
        if handler is None:
            return None
        t0 = time.monotonic()
        try:
            check_scope(routed.intent, routed.slots)
            result = handler(TemplateContext(con=self.toolbox.con, out_dir=self.toolbox.out_dir), routed.slots)
        except TemplateUnsupported as exc:
            history.log(f"  -> (template) {exc} - falling through to the agent")
            return None
        history.record_tool_call(f"template {routed.intent}", time.monotonic() - t0)
        # No second model call, ever: templates phrase their own answers. See
        # TemplateResult for why that is both faster and safer than narrating.
        return result.answer

    def _ask_inner(self, question: str, history: RunHistory) -> str:
        fast = self._try_fast_path(question, history)
        if fast is not None:
            # Record the turn in the conversation even though the tool loop
            # never ran, so a later follow-up that DOES fall through to the
            # agent still sees what was already asked and answered.
            self.messages.extend([{"role": "user", "content": question}, {"role": "assistant", "content": fast}])
            self._trim_history()
            self.last_question = question
            return fast

        self.last_question = question
        # Only the entries this question needs, rather than all 26 - see
        # prompt.select_knowledge. Swapping the system message costs one
        # cache miss on this question's FIRST iteration; the prefix is then
        # stable for the tool-call rounds after it, which is where the cost
        # compounded. Rare path, so that is the right way round.
        self.messages[0] = {"role": "system", "content": build_system_prompt(question)}
        self.messages.append({"role": "user", "content": question})
        auto_recoveries = 0
        error_recoveries = 0
        pending_error: str | None = None
        pending_error_tool: str | None = None

        for _ in range(MAX_TOOL_ITERATIONS):
            chat_kwargs: dict[str, Any] = dict(model=self.model, messages=self.messages, tools=TOOLS, keep_alive=KEEP_ALIVE, options={"num_ctx": NUM_CTX})
            if self.think:
                chat_kwargs["think"] = True
            t0 = time.monotonic()
            try:
                response = ollama.chat(**chat_kwargs)
            except ollama.ResponseError as exc:
                if self.think and "does not support thinking" in str(exc):
                    raise SystemExit(f"Error: model {self.model!r} does not support --think (try a thinking-capable model, e.g. qwen3:8b).") from None
                raise
            history.record_model_call(time.monotonic() - t0)
            msg = response.message
            if self.think and msg.thinking:
                history.log(f"  [thinking] {msg.thinking}")
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
                    history.log(f"  -> (auto) running SQL the model wrote instead of calling run_sql: {unrun_sql!r}")
                    t0 = time.monotonic()
                    result = self.toolbox.run_sql(unrun_sql)
                    history.record_tool_call("run_sql (auto)", time.monotonic() - t0)
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
                    history.log("  -> (guard) blocked a finalize right after a tool error, nudging a retry")
                    self.messages.append(
                        {
                            "role": "user",
                            "content": (
                                f"Your last {pending_error_tool} call failed, so you do not have real data "
                                "yet - do not answer with placeholder or made-up values. Call "
                                f"{pending_error_tool} again with corrected arguments, keeping every other "
                                "argument you had already filled in (season, team, fields, limit, etc.) "
                                "exactly as before - only fix what caused the error. If you can't get it "
                                "working, say plainly that you couldn't get the data. The error was:\n" + pending_error
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
                history.log(f"  -> {name}({args})")
                fn = self.dispatch.get(name)
                t0 = time.monotonic()
                if fn is None:
                    result = f"Error: unknown tool {name!r}"
                else:
                    try:
                        result = fn(**args)
                    except Exception as exc:
                        result = f"Error calling {name}: {exc}"
                history.record_tool_call(name, time.monotonic() - t0)
                result = str(result)
                if name in ("run_sql", "get_leaderboard"):
                    # Only these two data-fetching tools drive the fabrication
                    # guard below - a describe_table miss (e.g. an unknown table
                    # name) doesn't mean the model lacks real data, since an
                    # earlier run_sql/get_leaderboard call in the same turn may
                    # have already succeeded.
                    pending_error = result if _is_tool_error(result) else None
                    pending_error_tool = name if pending_error else None
                self.messages.append({"role": "tool", "content": result})

        self._trim_history()
        return "Gave up after too many tool-call iterations."
