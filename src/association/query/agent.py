"""The tool-calling loop: holds conversation state, dispatches tool calls, and
guards against the model writing SQL as prose instead of actually running it."""

from __future__ import annotations

import re
import time
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any

import ollama

from .answer import Answer, AnsweredBy, Artifact, Timing
from .entities import compared_but_unmatched, misread_players, override_invented_players, override_nicknames, restore_dropped_players, undo_name_completion
from .history import DEFAULT_HISTORY_DIR, RunHistory, echo_to_stderr
from .keepalive import KEEP_ALIVE
from .models import DEFAULT_ROUTER_MODEL
from .prompt import AGENT_NUM_CTX, TOOLS, build_system_prompt
from .router import route
from .templates import PLAYER_INTENTS, TEMPLATES, TemplateContext, TemplateResult, TemplateUnsupported, check_coverage, check_scope, coverage_caveat
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
    multi-turn memory (e.g. "what about for 2025?" referring to the prior question).

    .. versionchanged:: 2.0.0
       Takes a ``trace`` callback for its live output, defaulting to stderr, so
       a caller that is not a terminal can collect it. :meth:`ask` returns an
       :class:`association.query.answer.Answer` rather than the answer text.
    """

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
        trace: Callable[[str], None] = echo_to_stderr,
    ):
        self.model = model
        self.router_model = router_model
        self.verbose = verbose
        self.think = think
        self.history_dir = history_dir
        self.fast_path = fast_path
        self.trace = trace
        self.last_question: str | None = None
        self.toolbox: Toolbox = Toolbox(db_path, out_dir)
        # heterogeneous signatures dispatched generically via **args below -
        # a specific Callable type would make mypy check the wrong signature.
        self.dispatch: dict[str, Callable[..., str]] = {
            "describe_table": self.toolbox.describe_table,
            "run_sql": self.toolbox.run_sql,
            "get_leaderboard": self.toolbox.get_leaderboard,
            "render_shot_chart": self.toolbox.render_shot_chart,
            "render_fingerprint": self.toolbox.render_fingerprint,
        }
        # Rebuilt per question in _ask_inner; this is the always-on core only,
        # so a fresh Agent is usable before any question has been asked.
        self.messages: list[dict[str, Any]] = [{"role": "system", "content": build_system_prompt("")}]

    def _trim_history(self) -> None:
        # keep the system prompt (index 0) plus the most recent messages
        if len(self.messages) > MAX_HISTORY_MESSAGES:
            self.messages = [self.messages[0]] + self.messages[-(MAX_HISTORY_MESSAGES - 1) :]

    def ask(self, question: str, label: str = "") -> Answer:
        """Answer one question.

        Wraps _ask_inner so a RunHistory is ALWAYS written on the way out -
        including on an exception - regardless of --verbose. See history.py:
        one file per call, named with a random hash under .history/, meant to
        make a confusing or failed run's full evidence easy to find afterward
        rather than lost to whatever happened to print to the terminal.

        Args:
            question: The natural-language question.
            label: What to record as the run's ``command`` in the history file.
                A caller says what the request was; this used to be
                ``shlex.join(sys.argv)``, which is only true of a CLI and says
                nothing useful about a server handling many questions.

        Returns:
            An :class:`association.query.answer.Answer`. ``answer.text`` is
            what the CLI prints; the rest is what it could never show.

        .. versionchanged:: 2.0.0
           Returns an :class:`association.query.answer.Answer` rather than the
           answer text alone, and takes ``label`` rather than reading
           ``sys.argv``.
        """
        history = RunHistory(self.verbose, self.history_dir, sink=self.trace)
        self.toolbox.take_artifacts()  # anything left by a previous question is not this one's
        recorded = ""
        try:
            answer = self._ask_inner(question, history)
            recorded = answer.text
            return answer
        except Exception:
            recorded = "EXCEPTION:\n" + traceback.format_exc()
            raise
        finally:
            path = history.write(command=label, model=self.model, think=self.think, question=question, answer=recorded, router_model=self.router_model)
            self.trace(f"[history] {path}  {history.summary_line()}")

    def _answer(
        self,
        question: str,
        history: RunHistory,
        text: str,
        answered_by: AnsweredBy,
        intent: str | None = None,
        data: dict[str, Any] | None = None,
        artifacts: list[Artifact] | None = None,
    ) -> Answer:
        """Assemble the result, reading the timing and the rendered files off
        the run that just produced it. Every return point in _ask_inner goes
        through here so none of them can forget one.

        Charts arrive by two routes, hence the two sources: a template renders
        straight to disk and reports what it wrote, while the agent renders
        through a tool whose return value is prose the model reads, so the
        Toolbox has to record the file on the side."""
        return Answer(
            question=question,
            text=text,
            answered_by=answered_by,
            timing=Timing(
                total_seconds=history.total_seconds,
                model_seconds=history.model_seconds,
                model_calls=history.model_calls,
                tool_seconds=history.tool_seconds,
                tool_calls=history.tool_calls,
            ),
            intent=intent,
            data=data,
            artifacts=list(artifacts or []) + self.toolbox.take_artifacts(),
        )

    @staticmethod
    def _named_in(slots: dict[str, Any]) -> list[str]:
        """The player names a Route carries, however the router split them."""
        listed = slots.get("players")
        raw = listed if isinstance(listed, list) else [slots.get("player")]
        return [name for name in raw if isinstance(name, str) and name.strip()]

    def _try_fast_path(self, question: str, history: RunHistory) -> tuple[str, TemplateResult] | None:
        """Route -> deterministic template -> answer, returning the intent
        alongside the template's whole result. Returns None to fall through to
        the agent: an unported intent, slots that fail validation, or any
        router or template failure. Falling through costs one ~1-2s round trip
        and changes no answer.

        The intent and the TemplateResult are returned, rather than just its
        `answer` text, because that text is only one of the things the template
        produced - see TemplateResult.data, which nothing could reach before
        2.0."""
        if not self.fast_path:
            return None
        t0 = time.monotonic()
        routed = route(self.router_model, question, previous_question=self.last_question)
        history.record_model_call(time.monotonic() - t0)
        if routed is None:
            history.log("  -> (router) no usable classification, falling through to the agent")
            return None
        # Before anything reads a slot: the router rewrites nicknames, and
        # rewrites some of them to the wrong player. See entities.override_nicknames.
        for was, now in override_nicknames(question, routed.slots):
            history.log(f"  -> (nickname) {was!r} -> {now!r} (from the question, overriding the router)")
        handler = TEMPLATES.get(routed.intent)
        history.log(f"  -> (router) intent={routed.intent!r} slots={routed.slots}" + ("" if handler else " - not ported yet, falling through"))
        if handler is None:
            return None
        # A fingerprint draws as many polygons as it is given, and the router
        # drops the second name often enough that "compare fingerprints for
        # embiid vs jokic" arrived as one player, answered as half the
        # question with nothing saying so.
        if routed.intent == "fingerprint":
            restored = restore_dropped_players(self.toolbox.con, question, routed.slots)
            if restored is not None:
                history.log(f"  -> (player) {restored[0]!r} -> {restored[1]!r} (the question names more players than the router returned)")
        # The router invents whole names, not only nicknames: "compare sga and
        # embiid" came back with Jusuf Nurkic in the second slot, and every
        # stage after this one would have answered about him perfectly.
        grounded, invented = override_invented_players(self.toolbox.con, question, routed.slots)
        for was, now in grounded:
            history.log(f"  -> (player) {was!r} -> {now!r} (from the question, overriding the router)")
        # Said, not passed along. Falling through was tried and is worse: the
        # agent answered one of these with a 55-second fingerprint for "Ronaldo
        # Lopes", a player who does not exist, percentages included. Only where
        # the template would actually be about that player - a stray name on a
        # team question changes no answer.
        if invented and routed.intent in PLAYER_INTENTS:
            misread = misread_players(invented)
            history.log(f"  -> (player) {misread}")
            return routed.intent, TemplateResult(data={"message": misread, "misread": invented}, answer=misread)
        # Completing a bare surname is the prominence tiebreak this project
        # measured and rejected, arriving through the model instead of through
        # code. "brown" is ten players and has to ask, as it always did.
        for was, now in undo_name_completion(self.toolbox.con, question, routed.slots):
            history.log(f"  -> (player) {was!r} -> {now!r} (the question names only part of it, and that part is ambiguous)")
        t0 = time.monotonic()
        try:
            check_scope(routed.intent, routed.slots)
            # Returned as the answer rather than raised past this point. A
            # season under a table's floor has no better source anywhere - the
            # agent would query the same empty tables, more slowly, and is then
            # free to fill the silence from its own weights.
            refused = check_coverage(routed.intent, routed.slots)
            if refused is not None:
                history.log(f"  -> (coverage) {refused}")
                result = TemplateResult(data={"message": refused, "season": routed.slots.get("season")}, answer=refused)
            else:
                result = handler(TemplateContext(con=self.toolbox.con, out_dir=self.toolbox.out_dir), routed.slots)
                # A season that IS covered but only partly says so, rather than
                # reporting half a year as a whole one.
                note = coverage_caveat(routed.intent, routed.slots)
                if note:
                    result.answer = f"{result.answer} {note}"
                # A "vs" question that produced one polygon answered half of
                # itself. The missing name cannot be recovered - see
                # entities.compared_but_unmatched - so it is stated instead.
                if routed.intent == "fingerprint" and compared_but_unmatched(question, self._named_in(routed.slots)):
                    result.answer = f"{result.answer} Note: the question compares two players, but only one of them matches anybody in the warehouse - check the spelling of the other."
        except TemplateUnsupported as exc:
            history.log(f"  -> (template) {exc} - falling through to the agent")
            return None
        history.record_tool_call(f"template {routed.intent}", time.monotonic() - t0)
        # No second model call, ever: templates phrase their own answers. See
        # TemplateResult for why that is both faster and safer than narrating.
        return routed.intent, result

    def _ask_inner(self, question: str, history: RunHistory) -> Answer:
        fast = self._try_fast_path(question, history)
        if fast is not None:
            intent, templated = fast
            # Record the turn in the conversation even though the tool loop
            # never ran, so a later follow-up that DOES fall through to the
            # agent still sees what was already asked and answered.
            self.messages.extend([{"role": "user", "content": question}, {"role": "assistant", "content": templated.answer}])
            self._trim_history()
            self.last_question = question
            return self._answer(question, history, templated.answer, "fast", intent=intent, data=templated.data, artifacts=templated.artifacts)

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
            chat_kwargs: dict[str, Any] = dict(model=self.model, messages=self.messages, tools=TOOLS, keep_alive=KEEP_ALIVE, options={"num_ctx": AGENT_NUM_CTX})
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
                    return self._answer(
                        question,
                        history,
                        "I wasn't able to get a working query after a few attempts. "
                        "The last one I tried was:\n\n```sql\n" + unrun_sql + "\n```\n\n"
                        "You can run it yourself, or try rephrasing the question.",
                        "agent",
                    )
                if pending_error:
                    # Recovery cap hit and it's STILL trying to finalize right after
                    # an unrecovered error - say so honestly rather than returning
                    # whatever it fabricated.
                    return self._answer(question, history, "I ran into an error retrieving that data and wasn't able to recover. The last error was:\n\n" + pending_error, "agent")
                return self._answer(question, history, msg.content or "", "agent")

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
        return self._answer(question, history, "Gave up after too many tool-call iterations.", "agent")
