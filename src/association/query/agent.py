"""The tool-calling loop: holds conversation state, dispatches tool calls, and
guards against the model writing SQL as prose instead of actually running it."""

from __future__ import annotations

import re
import time
import traceback
from collections.abc import Callable, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import ollama

from .answer import Answer, AnsweredBy, Artifact, FallthroughDisabled, Timing
from .decisions import Decision
from .entities import (
    collect_name_readings,
    compared_but_unmatched,
    misread_players,
    override_nicknames,
    player_named_on_a_team_only_question,
    restore_dropped_players,
    team_only_question_names_a_player,
    undo_name_completion,
)
from .history import DEFAULT_HISTORY_DIR, RunHistory, echo_to_stderr
from .keepalive import KEEP_ALIVE
from .models import AGENT_BUDGET_SECONDS, DEFAULT_ROUTER_MODEL
from .prompt import AGENT_NUM_CTX, TOOLS, build_system_prompt
from .refusals import by_question, unanswerable
from .router import Route, RouterUnavailable, route
from .subject import Subject, apply_subject, read_subject
from .templates import TEMPLATES
from .templates.common import (
    PLAYER_INTENTS,
    TEAM_ONLY_INTENTS,
    TemplateContext,
    TemplateResult,
    TemplateUnsupported,
    check_coverage,
    check_scope,
    coverage_caveat,
)
from .toolbox import Toolbox

MAX_TOOL_ITERATIONS = 8
MAX_AUTO_SQL_RECOVERIES = 2  # cap on auto-executing SQL the model wrote instead of calling run_sql
MAX_ERROR_RECOVERIES = 2  # cap on nudging a retry after a tool error, instead of letting it fabricate an answer
MAX_HISTORY_MESSAGES = 40  # trim oldest turns once conversation grows past this, keep system prompt

# The subset of TemplateResult.data a compose.answer() carries that describes
# WHAT was answered - the point on the relation - rather than the rows
# themselves. Traced so a refusal that becomes a composed answer says what it
# composed, the same way "-> (router) intent=..." says what was routed. See
# association.query.compose's module docstring for the full shape.
_COMPOSE_POINT_KEYS = ("skeleton", "measures", "aggregate", "group", "predicates", "window", "span", "narrowing", "player")

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


def _note(result: TemplateResult, note: str) -> None:
    """Carry a note attached to an answer's sentence in its ``data`` too
    (``data["notes"]``), so the web page shows it beneath the rendered table
    instead of only inside the text toggle - a coverage caveat, how a bare
    surname was read, a compared name nothing matched. The sentence still
    carries it: the CLI prints the sentence.

    .. versionadded:: 4.4.0
    """
    if isinstance(result.data, dict):
        result.data.setdefault("notes", []).append(note.strip())


class Agent:
    """Holds conversation state across turns so interactive mode has real
    multi-turn memory (e.g. "what about for 2025?" referring to the prior question).

    .. versionchanged:: 2.0.0
       Takes a ``trace`` callback for its live output, defaulting to stderr, so
       a caller that is not a terminal can collect it. :meth:`ask` returns an
       :class:`association.query.answer.Answer` rather than the answer text.

    .. versionchanged:: 4.4.0
       Takes ``fallthrough``: False raises
       :class:`association.query.answer.FallthroughDisabled` where the agent
       would have been asked, for development. Takes ``budget_seconds``,
       the wall clock the fall-through agent may spend before it gives up
       and says what the fast path could not answer.
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
        fallthrough: bool = True,
        budget_seconds: float = AGENT_BUDGET_SECONDS,
    ):
        self.model = model
        self.router_model = router_model
        self.verbose = verbose
        self.think = think
        self.history_dir = history_dir
        self.fast_path = fast_path
        #: False refuses a question the fast path gives up on, with the reason,
        #: instead of handing it to the agent (:class:`FallthroughDisabled`).
        self.fallthrough = fallthrough
        #: Why the fast path gave the last question up, or None if it did not.
        self.fell_through: str | None = None
        #: Wall-clock seconds the fall-through agent may spend - see
        #: :data:`AGENT_BUDGET_SECONDS`. 0 removes the bound.
        self.budget_seconds = budget_seconds
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
        # Where in `messages` each question was appended, so trimming can drop
        # whole turns. Not derivable by scanning for role="user": the tool loop
        # appends its own user messages mid-turn to nudge a retry.
        self._turn_starts: list[int] = []

    def reset_conversation(self) -> None:
        """Forget every earlier turn, so the next question starts clean.

        Multi-turn memory is a property of one conversation, and whether a
        caller HAS one conversation is the caller's to say. An interactive CLI
        session does; a server answering whoever connects does not, and reusing
        one Agent there silently made every browser share a history - including
        ``last_question``, which is fed to the router, so one person's
        follow-up was resolved against a stranger's question.

        .. versionadded:: 2.1.0
        """
        self.messages = [{"role": "system", "content": build_system_prompt("")}]
        self._turn_starts = []
        self.last_question = None

    def _trim_history(self) -> None:
        """Drop the oldest turns once the conversation grows past the cap.

        Whole turns, never half of one. The fixed slice this replaced cut at an
        offset, which lands inside a turn whenever the arithmetic says so -
        between an ``assistant`` message carrying ``tool_calls`` and the
        ``tool`` results answering them - leaving the history opening on a tool
        result that answers nothing visible. Ollama accepts that rather than
        rejecting it (measured; it is not the schema error a stricter API would
        raise), which is what makes it worth fixing here rather than waiting
        for a crash to report it: nothing fails, and the cost is paid quietly
        as a JSON blob spending context with no question attached to say what
        it was for.

        Measuring it narrowed the shape, which is worth writing down because it
        is not what it looks like. A conversation of uniform turns never splits
        at all: every turn is an EVEN number of messages while the offset is
        odd, so the cut lands in the same safe place in every turn forever.
        What breaks that parity is a round where the model asks for two tools
        at once, since the loop below appends one ``tool`` message per call.
        Mixed that way - measured over template answers and two-call agent
        turns - a quarter of trims orphan a result.

        The cap is a ceiling, not a target: cutting at a turn boundary usually
        keeps a few messages fewer, and keeps the current turn whole even in
        the case where one turn alone would exceed it.
        """
        if len(self.messages) <= MAX_HISTORY_MESSAGES:
            return
        oldest_kept = len(self.messages) - (MAX_HISTORY_MESSAGES - 1)
        cut = next((start for start in self._turn_starts if start >= oldest_kept), self._turn_starts[-1] if self._turn_starts else len(self.messages))
        self.messages = [self.messages[0], *self.messages[cut:]]
        self._turn_starts = [start - cut + 1 for start in self._turn_starts if start >= cut]

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
        answer: Answer | None = None
        try:
            answer = self._ask_inner(question, history)
            recorded = answer.text
        except Exception:
            recorded = "EXCEPTION:\n" + traceback.format_exc()
            raise
        finally:
            path = history.write(command=label, model=self.model, think=self.think, question=question, answer=recorded, router_model=self.router_model)
            self.trace(f"[history] {path}  {history.summary_line()}")
        # The record's name rides on the Answer as a value, so a caller (the
        # web runner's note feature) never has to read it back out of the
        # trace line above - AGENTS.md, "Events carry trace lines verbatim".
        # After the finally, so the record is written before it is named.
        assert answer is not None
        return replace(answer, history_file=Path(path).name)

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
            decisions=tuple(history.decisions),
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
        self.fell_through = None
        if not self.fast_path:
            self.fell_through = "the fast path is off (--no-fast-path)"
            return None
        t0 = time.monotonic()
        try:
            routed = route(self.router_model, question, previous_question=self.last_question)
        except RouterUnavailable as exc:
            # The fast path is gone for this question, but so is the agent's
            # own model, most likely - falling through is still right, and the
            # reason has to name the server rather than the question.
            history.record_model_call(time.monotonic() - t0)
            history.log(f"  -> (router) {exc}, falling through to the agent")
            self.fell_through = str(exc)
            return None
        history.record_model_call(time.monotonic() - t0)
        if routed is None:
            history.log("  -> (router) no usable classification, falling through to the agent")
            self.fell_through = "the router returned no usable classification"
            return None
        # Before anything reads a slot: the router rewrites nicknames, and
        # rewrites some of them to the wrong player. See entities.override_nicknames.
        for was, now in override_nicknames(question, routed.slots):
            history.log(f"  -> (nickname) {was!r} -> {now!r} (from the question, overriding the router)")
        handler = TEMPLATES.get(routed.intent)
        history.log(f"  -> (router) intent={routed.intent!r} slots={routed.slots}" + ("" if handler else " - not ported yet, falling through"))
        # One reading of WHO the question is about, recorded as a decision
        # beside the repair chain below - which still writes every slot. The
        # reading writes none yet: measured against the chain on 290 recorded
        # questions it agreed on 278 and was right on the five where the chain
        # was wrong (query/subject.py); each chain step becomes a no-op, then
        # goes, as the reading takes over the field it settled.
        subject = self._record_subject(question, routed, history)
        # A fingerprint draws as many polygons as it is given, and the router
        # drops the second name often enough that "compare fingerprints for
        # embiid vs jokic" arrived as one player, answered as half the
        # question with nothing saying so.
        if routed.intent == "fingerprint":
            restored = restore_dropped_players(self.toolbox.con, question, routed.slots)
            if restored is not None:
                history.log(f"  -> (player) {restored[0]!r} -> {restored[1]!r} (the question names more players than the router returned)")
        # The reading writes the slots a template reads - who the question
        # is about, in the router's own slot shape - and settles the intent
        # where the router's cannot be about that subject, or where the
        # question's own words name a child of it (subject.KIND_ASSIGNED_INTENTS:
        # a count of 30+ point games under a game log). The router invents
        # whole names, not only nicknames: "compare sga and embiid" came back
        # with Jusuf Nurkic in the second slot, and every stage after this one
        # would have answered about him perfectly; a name nothing in the
        # question can replace is refused by name here.
        settled_intent, misread_result = self._ground_players(routed, subject, history)
        if misread_result is not None:
            return routed.intent, misread_result
        if settled_intent != routed.intent and settled_intent in TEMPLATES:
            # The handler goes with the intent. Resolving them apart is how a
            # reroute shipped broken once: the intent said with_without, the
            # trace said with_without, and head_to_head ran. Before the
            # no-template check below, since the router's `other` is a
            # parent the words assign under.
            routed.intent = settled_intent
            handler = TEMPLATES[routed.intent]
            history.log(f"  -> (subject) intent={routed.intent!r} slots={routed.slots}")
        settled = self._settled_before_template(question, routed, handler, history, subject)
        if settled is not None:
            return settled
        if handler is None:
            return None
        # Completing a bare surname is the prominence tiebreak this project
        # measured and rejected, arriving through the model instead of through
        # code. "brown" is ten players and has to ask, as it always did.
        for was, now in undo_name_completion(self.toolbox.con, question, routed.slots):
            history.log(f"  -> (player) {was!r} -> {now!r} (the question names only part of it, and that part is ambiguous)")
        # AGENTS.md, "Refuse by name where the intent cannot be about the
        # subject": a question naming exactly one real player and no team,
        # routed to an intent with no player reading at all, is about a
        # different subject than the one it would answer - "alperen şengün
        # alltime record" routed to team_leaderboard and answered the league
        # standings, Sengun never read (yardstick-v2 F111).
        if routed.intent in TEAM_ONLY_INTENTS:
            named_player = player_named_on_a_team_only_question(self.toolbox.con, question, routed.slots)
            if named_player is not None:
                message = team_only_question_names_a_player(named_player, routed.intent)
                history.log(f"  -> (player) {message}")
                return routed.intent, TemplateResult(data={"message": message, "named_player": named_player}, answer=message)
        return self._run_scoped_template(question, routed, handler, history, subject)

    def _ground_players(self, routed: Route, subject: Subject, history: RunHistory) -> tuple[str, TemplateResult | None]:
        """Every player name a template will read is one the question holds:
        the subject reading writes the names it read (query/subject.py,
        apply_subject) - the router's players and a player filed as the
        opponent, each write a recorded decision - and a router name the
        question never held that nothing in the question can replace is
        refused by name (the result returned here, beside the intent the
        reading settled the slots for). Split out of _try_fast_path for the
        complexity gate."""
        applied = apply_subject(subject, routed.slots, con=self.toolbox.con, intent=routed.intent)
        for decision in applied.decisions:
            history.record_decision(decision)
        dropped = applied.dropped
        # Said, not passed along. Falling through was tried and is worse: the
        # agent answered one of these with a 55-second fingerprint for "Ronaldo
        # Lopes", a player who does not exist, percentages included. Only where
        # the template would actually be about that player - a stray name on a
        # team question changes no answer.
        if dropped and routed.intent in PLAYER_INTENTS:
            misread = misread_players(dropped)
            history.log(f"  -> (player) {misread}")
            return applied.intent, TemplateResult(data={"message": misread, "misread": dropped}, answer=misread)
        return applied.intent, None

    def _record_subject(self, question: str, routed: Route, history: RunHistory) -> Subject:
        """The subject reading (query/subject.py) as decisions - who the
        question was read to be about, from its own words - recorded beside
        the repair chain, which still writes every slot. Split out of
        _try_fast_path for the complexity gate."""
        subject = read_subject(self.toolbox.con, question, routed.intent, routed.slots)
        history.record_decision(Decision("subject", "kind", None, subject.kind, "; ".join(subject.evidence)))
        for name, value in (
            ("players", subject.players),
            ("teams", subject.teams),
            ("opponent", subject.opponent),
            ("own_team", subject.own_team),
            ("companions", subject.companions),
            ("position", subject.position),
        ):
            if value:
                history.record_decision(Decision("subject", name, None, list(value) if isinstance(value, tuple) else value, "from the question's own words"))
        return subject

    def _settled_before_template(self, question: str, routed: Route, handler: Callable[..., TemplateResult] | None, history: RunHistory, subject: Subject) -> tuple[str, TemplateResult] | None:
        """What is decided before any template runs: a shape the question's
        own words settle (a championship question a team ranking would
        answer fluently and wrongly - refusals.by_question), and, where no
        template exists for the intent, a shape nothing reads at all ("most
        opponent bench points allowed ..." routes to `other`, and the
        refusals module knows bench points are read by nothing - said in
        seconds rather than after the agent's minute). With no template and
        no refusal, records the fall-through and returns None."""
        early = by_question(question, routed.intent)
        if early is not None:
            history.log(f"  -> (refusal) {early.data['refused']}: nothing here reads that shape")
            return routed.intent, early
        if handler is not None:
            return None
        refusal = unanswerable(self.toolbox.con, routed.intent, routed.slots, question, subject)
        if refusal is not None:
            history.log(f"  -> (refusal) {refusal.data['refused']}: nothing here reads that shape")
            return routed.intent, refusal
        self.fell_through = f"intent {routed.intent!r} has no template yet"
        return None

    def _run_scoped_template(
        self, question: str, routed: Route, handler: Callable[[TemplateContext, dict[str, Any]], TemplateResult], history: RunHistory, subject: Subject
    ) -> tuple[str, TemplateResult] | None:
        """Check scope and coverage, run the template, and attach the notes
        every fast-path answer carries. On a scoping refusal
        (``TemplateUnsupported``, from ``check_scope`` or the template itself),
        try the compiled answer before giving up on the fast path - split out
        of ``_try_fast_path`` to keep it under the complexity gate, and because
        it is one coherent step: "run what the router found, and cope with it
        refusing"."""
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
                result = self._run_template(handler, routed.slots, history)
                # A season that IS covered but only partly says so, rather than
                # reporting half a year as a whole one.
                note = coverage_caveat(routed.intent, routed.slots)
                if note:
                    result.answer = f"{result.answer} {note}"
                    _note(result, note)
                # A "vs" question that produced one polygon answered half of
                # itself. entities.compared_but_unmatched tells a name nothing
                # can repair from one restore_dropped_players simply missed -
                # see its docstring - and phrases each case correctly.
                if routed.intent == "fingerprint":
                    unmatched_note = compared_but_unmatched(self.toolbox.con, question, self._named_in(routed.slots))
                    if unmatched_note:
                        result.answer = f"{result.answer} {unmatched_note}"
                        _note(result, unmatched_note)
        except TemplateUnsupported as exc:
            # The template could not honor the scoping asked for - before
            # falling through to the slow agent, see whether the compiler can
            # answer the same point on the relation. A refusal it hands back
            # (a clarification, a "no match") is still an answer, not a
            # fall-through: it looked at the question.
            t1 = time.monotonic()
            composed = self._try_compose(question, routed.intent, routed.slots, history, subject)
            if composed is not None:
                history.record_tool_call(f"compose {routed.intent}", time.monotonic() - t1)
                history.log(f"  -> (template) {exc} - composed instead of falling through")
                return routed.intent, composed
            # Then, before the slow agent: is this a shape nothing here can
            # read - a playoff round, an age, a stat by quarter other than
            # points? The agent has no better source for those either, and a
            # refusal naming the missing thing is the answer (query/refusals).
            refusal = unanswerable(self.toolbox.con, routed.intent, routed.slots, question, subject)
            if refusal is not None:
                history.log(f"  -> (template) {exc} - refused ({refusal.data['refused']}): nothing here reads that shape")
                return routed.intent, refusal
            history.log(f"  -> (template) {exc} - falling through to the agent")
            self.fell_through = f"{routed.intent}: {exc}"
            return None
        history.record_tool_call(f"template {routed.intent}", time.monotonic() - t0)
        # No second model call, ever: templates phrase their own answers. See
        # TemplateResult for why that is both faster and safer than narrating.
        return routed.intent, result

    def _try_compose(self, question: str, intent: str, slots: dict[str, Any], history: RunHistory, subject: Subject) -> TemplateResult | None:
        """The step between a template's refusal and the fall-through agent:
        ``association.query.compose.answer``, called only here so a caller
        that never sees a ``TemplateUnsupported`` never pays for the import.
        Answered exactly like a template's own result - same name-reading and
        coverage-caveat attachment as :meth:`_run_template` - except its trace
        line names the point on the relation it composed rather than a
        template's intent and slots, since that IS the interesting fact about
        a compiled answer.

        Import inside the function, not at module level: the contract this
        calls against (``README_land.md``) is built on a parallel branch, and
        a call-time import is what lets a stub with only ``answer()`` stand in
        for it - and what tests monkeypatch
        (``association.query.compose.answer``).
        """
        from . import compose

        with collect_name_readings() as readings:
            composed = compose.answer(TemplateContext(con=self.toolbox.con, out_dir=self.toolbox.out_dir), intent, slots, question, subject)
        if composed is None:
            return None
        for reading in readings:
            history.log(f"  -> (player) {reading}")
            composed.answer = f"{composed.answer} {reading}"
            _note(composed, reading)
        if readings:
            composed.data["name_readings"] = list(readings)
        note = coverage_caveat(intent, slots)
        if note:
            composed.answer = f"{composed.answer} {note}"
            _note(composed, note)
        point = {key: composed.data[key] for key in _COMPOSE_POINT_KEYS if key in composed.data}
        history.log(f"  -> (compose) intent={intent!r} point={point}")
        return composed

    def _run_template(self, handler: Callable[[TemplateContext, dict[str, Any]], TemplateResult], slots: dict[str, Any], history: RunHistory) -> TemplateResult:
        """The template's answer, with how it read any name the question left
        open. "maxey" is Tyrese because he is the only Maxey who still plays -
        a default, and a default is allowed only where it is visible and can be
        corrected, so the sentence naming who else matched and what to type for
        him is part of the answer (entities.collect_name_readings)."""
        with collect_name_readings() as readings:
            result = handler(TemplateContext(con=self.toolbox.con, out_dir=self.toolbox.out_dir), slots)
        for reading in readings:
            history.log(f"  -> (player) {reading}")
            result.answer = f"{result.answer} {reading}"
            _note(result, reading)
        if readings:
            result.data["name_readings"] = list(readings)
        return result

    def _ask_inner_chat(self, history: RunHistory) -> ollama.Message:
        """One model turn: call ollama, log any thinking, and append the raw
        response to the conversation. Returns the response message.

        The ``thinking`` field is stripped from what gets appended - it is
        scratch work for the turn that produced it, not memory the model needs
        later, and re-sending it costs real prompt-eval time on every
        subsequent iteration (confirmed live: stripping it cut a follow-up
        iteration's prompt eval from ~5.6s to ~0.9s on an 8-token-context
        conversation - the effect compounds with each further tool-call round).
        """
        chat_kwargs: dict[str, Any] = {"model": self.model, "messages": self.messages, "tools": TOOLS, "keep_alive": KEEP_ALIVE, "options": {"num_ctx": AGENT_NUM_CTX}}
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
        dumped.pop("thinking", None)
        self.messages.append(dumped)
        return msg

    def _ask_inner_finalize(
        self,
        question: str,
        history: RunHistory,
        msg: ollama.Message,
        auto_recoveries: int,
        error_recoveries: int,
        pending_error: str | None,
        pending_error_tool: str | None,
    ) -> tuple[Answer | None, int, int]:
        """What happens when a model turn asks for no tool call: either a
        recovery nudge (returning None so the caller's loop continues) or a
        final Answer, plus the possibly-incremented recovery counters.

        Covers three shapes in the order they are checked: SQL the model wrote
        as prose instead of calling run_sql, a finalize attempted right after
        an unrecovered tool error, and - once both recovery caps are spent -
        the honest messages for each.
        """
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
            return None, auto_recoveries, error_recoveries
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
            return None, auto_recoveries, error_recoveries
        self._trim_history()
        if unrun_sql:
            # Recovery cap hit and the model is STILL just printing SQL
            # instead of running it - returning msg.content as-is would
            # read as "I'm about to do this" while doing nothing
            # (confirmed live: a real answer ending in "Let's run this
            # corrected query" that never ran). Say plainly that it
            # didn't work, with the last attempt shown, rather than a
            # reply that only looks like an in-progress action.
            answer = self._answer(
                question,
                history,
                "I wasn't able to get a working query after a few attempts. The last one I tried was:\n\n```sql\n" + unrun_sql + "\n```\n\nYou can run it yourself, or try rephrasing the question.",
                "agent",
            )
            return answer, auto_recoveries, error_recoveries
        if pending_error:
            # Recovery cap hit and it's STILL trying to finalize right after
            # an unrecovered error - say so honestly rather than returning
            # whatever it fabricated.
            answer = self._answer(question, history, "I ran into an error retrieving that data and wasn't able to recover. The last error was:\n\n" + pending_error, "agent")
            return answer, auto_recoveries, error_recoveries
        return self._answer(question, history, msg.content or "", "agent"), auto_recoveries, error_recoveries

    def _ask_inner_dispatch_calls(self, history: RunHistory, tool_calls: Sequence[ollama.Message.ToolCall]) -> tuple[str | None, str | None]:
        """Run every tool call in one model turn, appending each result to the
        conversation, and return the fabrication guard's state after all of
        them - the last data-fetching call's error, if any.

        Only run_sql/get_leaderboard drive the fabrication guard above - a
        describe_table miss (e.g. an unknown table name) doesn't mean the
        model lacks real data, since an earlier run_sql/get_leaderboard call in
        the same turn may have already succeeded.
        """
        pending_error: str | None = None
        pending_error_tool: str | None = None
        for call in tool_calls:
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
                except Exception as exc:  # noqa: BLE001 - the model sees the error and can retry, rather than the question dying
                    result = f"Error calling {name}: {exc}"
            history.record_tool_call(name, time.monotonic() - t0)
            result = str(result)
            if name in ("run_sql", "get_leaderboard"):
                pending_error = result if _is_tool_error(result) else None
                pending_error_tool = name if pending_error else None
            self.messages.append({"role": "tool", "content": result})
        return pending_error, pending_error_tool

    def _ask_inner(self, question: str, history: RunHistory) -> Answer:
        fast = self._try_fast_path(question, history)
        if fast is not None:
            intent, templated = fast
            # Record the turn in the conversation even though the tool loop
            # never ran, so a later follow-up that DOES fall through to the
            # agent still sees what was already asked and answered.
            self._turn_starts.append(len(self.messages))
            self.messages.extend([{"role": "user", "content": question}, {"role": "assistant", "content": templated.answer}])
            self._trim_history()
            self.last_question = question
            return self._answer(question, history, templated.answer, "fast", intent=intent, data=templated.data, artifacts=templated.artifacts)

        if not self.fallthrough:
            # Development only - see FallthroughDisabled. Refused here, before
            # the agent's prompt is built, so no model call is spent on it.
            history.log("  -> (fallthrough) disabled; refusing rather than asking the agent")
            raise FallthroughDisabled(f"no template answered this question and fall-through to the agent is disabled: {self.fell_through}")
        self.last_question = question
        started = time.monotonic()
        # Only the entries this question needs, rather than all 13 - see
        # prompt.select_knowledge. Swapping the system message costs one
        # cache miss on this question's FIRST iteration; the prefix is then
        # stable for the tool-call rounds after it, which is where the cost
        # compounded. Rare path, so that is the right way round.
        self.messages[0] = {"role": "system", "content": build_system_prompt(question)}
        self._turn_starts.append(len(self.messages))
        self.messages.append({"role": "user", "content": question})
        auto_recoveries = 0
        error_recoveries = 0
        pending_error: str | None = None
        pending_error_tool: str | None = None

        for _ in range(MAX_TOOL_ITERATIONS):
            # Before the call, not after: the check is what bounds the wait,
            # and a call already in flight cannot be taken back (ollama's
            # client is blocking and takes no cancellation token).
            if self.budget_seconds > 0 and time.monotonic() - started >= self.budget_seconds:
                return self._gave_up(question, history, f"it did not reach an answer within {self.budget_seconds:.0f}s")
            msg = self._ask_inner_chat(history)

            if not msg.tool_calls:
                answer, auto_recoveries, error_recoveries = self._ask_inner_finalize(question, history, msg, auto_recoveries, error_recoveries, pending_error, pending_error_tool)
                if answer is not None:
                    return answer
                continue

            pending_error, pending_error_tool = self._ask_inner_dispatch_calls(history, msg.tool_calls)

        return self._gave_up(question, history, f"it made {MAX_TOOL_ITERATIONS} tool calls without reaching one")

    def _gave_up(self, question: str, history: RunHistory, why: str) -> Answer:
        """The answer when the fall-through agent runs out of budget or of
        tool calls: what it did, and what the fast path could not answer.

        Naming the shape is the point. "Gave up after too many tool-call
        iterations" told a reader nothing about their own question, while the
        reason the templates declined it - an intent with no template, a
        scoping slot none honors - says which part of the question has no
        answer here yet (#129).
        """
        self._trim_history()
        reason = f" No template answered it either: {self.fell_through}." if self.fell_through else ""
        history.log(f"  -> (agent) gave up: {why}")
        return self._answer(question, history, f"The SQL-writing agent gave up: {why}.{reason}", "agent")
