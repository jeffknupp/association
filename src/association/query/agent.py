"""The answering loop: the parser's Reading through a template or the
compiler, and a refusal naming why where neither has a reading of the
question. One model call, the normalizer's, is the whole cost; nothing after
it reaches a model.

Until 5.0.0 a question no template or compiled reading answered fell through
to a tool-calling agent that wrote SQL by hand. Measured (ISSUES.md #129,
2026-09-18: 24 questions at production defaults), it answered one question in
23 and did not finish 61% of the time, and where it finished it was wrong five
times in six - an agent with nothing to read fills the silence from its own
weights, which is exactly what ``check_coverage``'s reasoning had said all
along. So it is gone, with its prompt, its tools and its budget. Where it
would have been asked, :meth:`Agent.ask` answers a refusal that names what the
fast path could not read (``answered_by="refused"``), in seconds. That is the
same answer ``--disable-fallthrough`` gave as an error, now the only answer,
and every recorded reason stands: an intent with no template, a narrowing the
relation cannot honor, a reply the normalizer could not use.
"""

from __future__ import annotations

import time
import traceback
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import duckdb

from association.nba.season import calendar_season, season_on_record

from .answer import Answer, AnsweredBy, Artifact, Timing
from .compose import COMPILED_INTENTS
from .compose.core import Query
from .compose.plan import Planned, plan_point
from .compose.team import TeamQuery
from .connection import connect_read_only, latest_season_on_record
from .entities import (
    collect_name_readings,
    compared_but_unmatched,
    misread_players,
    player_named_on_a_team_only_question,
    team_only_question_names_a_player,
)
from .history import DEFAULT_HISTORY_DIR, RunHistory, echo_to_stderr
from .models import DEFAULT_ROUTER_MODEL
from .names import loaded as names_loaded
from .notes import collect as collect_remarks
from .notes import unsaid
from .reading import Reading, Scope, ScopeError
from .refusals import MIN_QUESTION_WORDS, by_question, too_short, unanswerable
from .router import Route, RouterUnavailable
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

# The subset of TemplateResult.data a compose.answer() carries that describes
# WHAT was answered - the point on the relation - rather than the rows
# themselves. Traced so a refusal that becomes a composed answer says what it
# composed, the same way "-> (router) intent=..." says what was routed. See
# association.query.compose's module docstring for the full shape.
_COMPOSE_POINT_KEYS = ("skeleton", "measures", "aggregate", "group", "predicates", "window", "span", "narrowing", "player")


def _note(result: TemplateResult, note: str) -> None:
    """Record a sentence appended to a template's answer on its ``data`` too,
    so a caller rendering from ``data`` (the web page) does not lose what the
    text-only reader is told: which name a default chose, how much of a
    season the source holds."""
    notes = result.data.setdefault("notes", [])
    if isinstance(notes, list):
        notes.append(note)


def refusal_text(why: str) -> str:
    """The sentence a question nothing here reads is answered with: the
    reason the fast path gave it up, as the parser, a template or the compiler
    stated it - an intent with no template, a narrowing the relation cannot
    honor, a reply the normalizer could not use. Naming the cause is the
    whole point: the reader learns which part of the question has no answer
    here, where the agent this replaced spent minutes and then guessed.

    .. versionadded:: 5.0.0
    """
    return f"Nothing here answers this question: {why}."


class Agent:
    """Answers one question at a time: the parser's Reading through a template
    or the compiler, or a refusal naming why neither had a reading.

    .. versionchanged:: 2.0.0
       Takes a ``trace`` callback for its live output, defaulting to stderr, so
       a caller that is not a terminal can collect it. :meth:`ask` returns an
       :class:`association.query.answer.Answer` rather than the answer text.

    .. versionchanged:: 5.0.0
       The parser reads the question: the model copies names and picks a
       stat (:mod:`~association.query.normalizer`) and
       :func:`~association.query.parse.read_route` reads the rest, where the
       router's model classified the whole question.

    .. versionchanged:: 5.0.0
       Breaking: the tool-calling fall-through is gone, and with it the
       ``model``, ``think``, ``fast_path``, ``fallthrough`` and
       ``budget_seconds`` arguments (``--model``, ``--think``,
       ``--no-fast-path``, ``--disable-fallthrough`` and ``--agent-budget``
       on the CLI). The first argument is the warehouse path. A question no
       template or compiled reading answers is a refusal naming why
       (``answered_by="refused"``, :func:`refusal_text`) rather than minutes
       of a model writing SQL. Conversation memory went with it: nothing
       read the previous question any more.
    """

    def __init__(
        self,
        db_path: str,
        out_dir: Path,
        verbose: bool = False,
        history_dir: Path = DEFAULT_HISTORY_DIR,
        router_model: str = DEFAULT_ROUTER_MODEL,
        trace: Callable[[str], None] = echo_to_stderr,
    ):
        #: The normalizer's model (:func:`~association.query.normalizer.normalize`),
        #: which copies the names out of the question and picks a stat.
        self.router_model = router_model
        self.verbose = verbose
        self.history_dir = history_dir
        #: Why the last question was refused with no reading, or None where a
        #: template, the compiler or a named refusal answered it. A template's
        #: own refusal (a clarification, a "no match", a shape nothing reads)
        #: is an answer: it looked at the question and had something to say.
        self.unanswered: str | None = None
        #: The Reading the last question settled into, or None where it was
        #: refused before anything read it - what a stage snapshot records
        #: beside the answer (:func:`association.query.stages.snapshot`).
        #:
        #: .. versionadded:: 5.0.0
        self.reading: Reading | None = None
        #: The last question's Reading planned onto its relation - the
        #: query, or why there is none
        #: (:func:`~association.query.compose.plan.plan_point`) - or None
        #: where nothing was read. Planned once per question, here, and
        #: handed to whatever answers.
        #:
        #: .. versionadded:: 5.0.0
        self.planned: Planned | None = None
        #: The query the compiler itself executed for the last question, or
        #: None: a template's answer, a presenter's (which runs the retired
        #: template's own read), a refusal: the planned query
        #: (``compose.answer``'s ``ran``); the stage snapshot records it.
        #:
        #: .. versionadded:: 5.0.0
        self.ran: Query | TeamQuery | None = None
        #: The kinds of the remarks written for the last question whose
        #: sentence did not reach its answer (:func:`association.query.notes.unsaid`):
        #: a caveat computed and then dropped. Empty when every one was said.
        #:
        #: .. versionadded:: 5.0.0
        self.unsaid: list[str] = []
        self.trace = trace
        #: The warehouse, read-only and cut off from the disk
        #: (:func:`~association.query.connection.connect_read_only`).
        self.con: duckdb.DuckDBPyConnection = connect_read_only(db_path)
        self.out_dir = out_dir
        self.out_dir.mkdir(parents=True, exist_ok=True)

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

        .. versionchanged:: 5.0.0
           A question nothing here reads is answered with a refusal naming
           why (``answered_by="refused"``), never handed to a model.

        .. versionchanged:: 5.0.0
           A question of fewer than :data:`~association.query.refusals.MIN_QUESTION_WORDS`
           words is refused unread, with a generic sentence, before the
           normalizer is asked.
        """
        history = RunHistory(self.verbose, self.history_dir, sink=self.trace)
        self.reading = None
        self.planned = None
        self.ran = None
        self.unsaid = []
        recorded = ""
        answer: Answer | None = None
        # "This season" is the latest one the warehouse has games for, not
        # the calendar's, from October 1 until the new season's games are
        # loaded (nba.season.current_season). Read per question: a server
        # outlives a reload.
        on_record = latest_season_on_record(self.con)
        if on_record is not None and on_record < calendar_season():
            history.log(f"  -> (decision) default season: {on_record} (the calendar's {calendar_season()} has no games on record)")
        try:
            # Every remark written on the way - a caveat, a stated default, a
            # definition - recorded as a kind and its facts beside the
            # sentence it stays in (query/notes.py; ROADMAP.md, Phase 0).
            # One read of the players' and teams' names for the whole
            # question (query/names.py), not a statement per word.
            with season_on_record(on_record), names_loaded(), collect_remarks() as remarks:
                answer = self._ask_inner(question, history)
            answer = replace(answer, notes=tuple(remarks.notes), decisions=(*answer.decisions, *remarks.decisions))
            self.unsaid = unsaid(remarks, answer.text)
            recorded = answer.text
        except Exception:
            recorded = "EXCEPTION:\n" + traceback.format_exc()
            raise
        finally:
            path = history.write(command=label, question=question, answer=recorded, router_model=self.router_model)
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
        """Assemble the result, reading the timing off the run that just
        produced it. Every return point in _ask_inner goes through here so
        none of them can forget one."""
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
            artifacts=list(artifacts or []),
            decisions=tuple(history.decisions),
        )

    @staticmethod
    def _named_in(scope: Scope) -> list[str]:
        """The player names a Reading's scope carries, however the route split them."""
        raw = list(scope.players) if scope.players else [scope.player]
        return [name for name in raw if isinstance(name, str) and name.strip()]

    def _try_fast_path(self, question: str, history: RunHistory) -> tuple[str, TemplateResult] | None:
        """Route -> Reading -> deterministic template -> answer, returning the
        intent alongside the template's whole result. Returns None, with
        :attr:`unanswered` naming why, where nothing here reads the question:
        an unported intent, slots that fail validation, or any reader or
        template failure the compiler and the named refusals could not take.

        The intent and the TemplateResult are returned, rather than just its
        `answer` text, because that text is only one of the things the template
        produced - see TemplateResult.data, which nothing could reach before
        2.0."""
        self.unanswered = None
        routed = self._read_or_refuse(question, history)
        if routed is None:
            return None
        reading = self._reading(question, routed, history)
        # A name the model supplied that the question never held, and that
        # nothing in the question can replace, is refused by name rather than
        # answered: the router invented whole names, not only nicknames -
        # "compare sga and embiid" came back with Jusuf Nurkic in the second
        # slot, and every stage after this one would have answered about him
        # perfectly. Passing it on was tried and is worse: the agent this
        # replaced answered one of these with a 55-second fingerprint for
        # "Ronaldo Lopes", a player who does not exist, percentages included.
        # Only where the template would actually be about that player - a
        # stray name on a team question changes no answer.
        if reading.misread and reading.intent in PLAYER_INTENTS:
            misread = misread_players(list(reading.misread))
            history.log(f"  -> (player) {misread}")
            return reading.intent, TemplateResult(data={"message": misread, "misread": list(reading.misread)}, answer=misread)
        # The handler goes with the intent the Reading settled - where the
        # route's cannot be about the subject, or where the question's own
        # words name a child of it (subject.KIND_ASSIGNED_INTENTS: a count of
        # 30+ point games under a game log). Resolving them apart is how a
        # reroute shipped broken once: the intent said with_without, the trace
        # said with_without, and head_to_head ran.
        handler = TEMPLATES.get(reading.intent)
        if reading.intent != routed.intent:
            history.log(f"  -> (subject) intent={reading.intent!r} slots={reading.scope.to_slots()}")
        settled = self._settled_before_template(question, reading, handler, history)
        if settled is not None:
            return settled
        if reading.intent in COMPILED_INTENTS:
            return self._run_compiled(question, reading, history)
        if handler is None:
            return None
        # AGENTS.md, "Refuse by name where the intent cannot be about the
        # subject": a question naming exactly one real player and no team,
        # routed to an intent with no player reading at all, is about a
        # different subject than the one it would answer - "alperen şengün
        # alltime record" routed to team_leaderboard and answered the league
        # standings, Sengun never read (yardstick-v2 F111).
        if reading.intent in TEAM_ONLY_INTENTS:
            named_player = player_named_on_a_team_only_question(self.con, question, reading.scope.to_slots())
            if named_player is not None:
                message = team_only_question_names_a_player(named_player, reading.intent)
                history.log(f"  -> (player) {message}")
                return reading.intent, TemplateResult(data={"message": message, "named_player": named_player}, answer=message)
        return self._run_scoped_template(question, reading, handler, history)

    def _reading(self, question: str, routed: Route, history: RunHistory) -> Reading:
        """The Reading ``routed`` settles into (:func:`~association.query.parse.reading_from_route`),
        with every decision it made recorded. Split out of
        :meth:`_try_fast_path` for the complexity gate. The route carries the
        typed Scope already: a slot nothing can hold stopped the parser at the
        Scope's door (:meth:`_read_or_refuse`)."""
        from association.query.parse import reading_from_route

        # What will answer, said beside the route: until 5.0.0 every intent
        # outside TEMPLATES printed "not ported yet" here - on 206 of 277
        # yardstick answers, all of them the compiler's (ISSUES.md #284).
        path = "a template" if routed.intent in TEMPLATES else "the compiler" if routed.intent in COMPILED_INTENTS else "no reader: refused unless a named refusal has its cause"
        history.log(f"  -> (router) intent={routed.intent!r} slots={routed.slots} - {path}")
        # One reading of WHO the question is about, from its own words, written
        # into the Scope - the parser's last step, and the only writer: nothing
        # after this changes a slot (query/subject.py, ROADMAP plan item 6).
        reading = reading_from_route(self.con, question, routed)
        self.reading = reading
        # PLAN, once: the point on its relation, or why there is none. The
        # parser read the point; it does not plan it (ROADMAP.md, Phase 1).
        self.planned = plan_point(reading)
        for decision in reading.decisions:
            history.record_decision(decision)
        return reading

    def _read_or_refuse(self, question: str, history: RunHistory) -> Route | None:
        """The route the parser reads for ``question``, or None with the
        reason the question is being refused recorded. Split out of
        :meth:`_try_fast_path` for the complexity gate."""
        t0 = time.monotonic()
        try:
            routed = self._read_question(question, history)
        except RouterUnavailable as exc:
            # The reason has to name the server rather than the question: a
            # normalizer that could not be reached read nothing of it.
            history.record_model_call(time.monotonic() - t0)
            history.log(f"  -> (normalizer) {exc}: refused")
            self.unanswered = str(exc)
            return None
        except ScopeError as exc:
            # A slot nothing can hold - "stephen curry last 0 games" reads as a
            # window of 0 - stops the parser at the Scope's door (the stages'
            # own, router.settle): refused here, the way a template's refusal
            # is, rather than crashing.
            history.record_model_call(time.monotonic() - t0)
            history.log(f"  -> (scope) {exc}: refused")
            self.unanswered = f"a slot the Reading cannot hold: {exc}"
            return None
        history.record_model_call(time.monotonic() - t0)
        if routed is None:
            history.log("  -> (normalizer) no usable reply: refused")
            self.unanswered = "the normalizer returned no usable reply"
        return routed

    def _read_question(self, question: str, history: RunHistory) -> Route | None:
        """The route the parser reads (ROADMAP plan item 6, step c): the model
        copies the names out of the question and picks a stat key
        (:func:`~association.query.normalizer.normalize`), and
        :func:`~association.query.parse.read_route` checks both and reads the
        intent and every other slot from the words. None when the model's
        reply is unusable. What follows is the path every route takes: the
        parser's last step (:func:`~association.query.parse.reading_from_route`)
        and the templates."""
        from association.query.normalizer import normalize
        from association.query.parse import read_route

        normalized = normalize(self.router_model, question)
        if normalized is None:
            return None
        history.log(f"  -> (normalizer) names={normalized.names} stat={normalized.stat!r}")
        routed, subject, parent = read_route(self.con, question, normalized.names, normalized.stat)
        history.log(f"  -> (parser) parent={parent!r} kind={subject.kind!r} intent={routed.intent!r}")
        return routed

    def _settled_before_template(self, question: str, reading: Reading, handler: Callable[..., TemplateResult] | None, history: RunHistory) -> tuple[str, TemplateResult] | None:
        """What is decided before any template runs: a shape the question's
        own words settle (a championship question a team ranking would
        answer fluently and wrongly - refusals.by_question), and, where no
        template exists for the intent, a shape nothing reads at all ("most
        opponent bench points allowed ..." routes to `other`, and the
        refusals module knows bench points are read by nothing). With no
        template and no named refusal, records why and returns None."""
        early = by_question(question, reading.intent)
        if early is not None:
            history.log(f"  -> (refusal) {early.data['refused']}: nothing here reads that shape")
            return reading.intent, early
        if handler is not None or reading.intent in COMPILED_INTENTS:
            return None
        refusal = unanswerable(self.con, reading, question)
        if refusal is not None:
            history.log(f"  -> (refusal) {refusal.data['refused']}: nothing here reads that shape")
            return reading.intent, refusal
        self.unanswered = f"intent {reading.intent!r} has no template yet"
        return None

    def _run_scoped_template(self, question: str, reading: Reading, handler: Callable[[TemplateContext, Reading], TemplateResult], history: RunHistory) -> tuple[str, TemplateResult] | None:
        """Check scope and coverage, run the template, and attach the notes
        every fast-path answer carries. On a scoping refusal
        (``TemplateUnsupported``, from ``check_scope`` or the template itself),
        try the compiled answer, then a named refusal, before giving the
        question up - split out of ``_try_fast_path`` to keep it under the
        complexity gate, and because it is one coherent step: "run what the
        reader found, and cope with it refusing"."""
        t0 = time.monotonic()
        intent, scope = reading.intent, reading.scope
        try:
            check_scope(intent, scope)
            # Returned as the answer rather than raised past this point. A
            # season under a table's floor has no better source anywhere.
            refused = check_coverage(intent, scope)
            if refused is not None:
                history.log(f"  -> (coverage) {refused}")
                result = TemplateResult(data={"message": refused, "season": scope.season}, answer=refused)
            else:
                result = self._run_template(handler, reading, history)
                # A season that IS covered but only partly says so, rather than
                # reporting half a year as a whole one.
                note = coverage_caveat(intent, scope)
                if note:
                    result.answer = f"{result.answer} {note}"
                    _note(result, note)
                # A "vs" question that produced one polygon answered half of
                # itself: entities.compared_but_unmatched says which name the
                # question compares matched nobody - see its docstring.
                if intent == "fingerprint":
                    unmatched_note = compared_but_unmatched(self.con, question, self._named_in(scope))
                    if unmatched_note:
                        result.answer = f"{result.answer} {unmatched_note}"
                        _note(result, unmatched_note)
        except TemplateUnsupported as exc:
            # The template could not honor the scoping asked for - see whether
            # the compiler can answer the same point on the relation. A
            # refusal it hands back (a clarification, a "no match") is still
            # an answer: it looked at the question.
            t1 = time.monotonic()
            composed = self._try_compose(question, reading, history)
            if composed is not None:
                history.record_tool_call(f"compose {intent}", time.monotonic() - t1)
                history.log(f"  -> (template) {exc} - composed instead")
                return intent, composed
            # Then: is this a shape nothing here can read - a playoff round,
            # an age, a stat by quarter other than points? A refusal naming
            # the missing thing is the answer (query/refusals).
            refusal = unanswerable(self.con, reading, question)
            if refusal is not None:
                history.log(f"  -> (template) {exc} - refused ({refusal.data['refused']}): nothing here reads that shape")
                return intent, refusal
            history.log(f"  -> (template) {exc}: refused")
            self.unanswered = f"{intent}: {exc}"
            return None
        history.record_tool_call(f"template {intent}", time.monotonic() - t0)
        # No second model call, ever: templates phrase their own answers. See
        # TemplateResult for why that is both faster and safer than narrating.
        return intent, result

    def _run_compiled(self, question: str, reading: Reading, history: RunHistory) -> tuple[str, TemplateResult] | None:
        """An intent the compiler alone answers (``compose.COMPILED_INTENTS``:
        the four whose templates it reproduced exactly, retired in ROADMAP
        plan item 6, step (d), part 4, and ``game_log``, ``player_stat`` and
        ``player_splits``, step (g)). Where the compiler has no reading of
        the point, the steps a template's refusal took, in its order: the
        compiler's own reason - the planner refusing a narrowing the relation
        cannot honor (``Planned.declined``) - names
        why the question is refused; a season under a table's floor is
        refused, never answered from nothing; and a shape nothing here reads
        is refused by name (query/refusals)."""
        t0 = time.monotonic()
        intent, scope = reading.intent, reading.scope
        declined: list[str] = []
        composed = self._try_compose(question, reading, history, declined=declined.append)
        if composed is not None:
            history.record_tool_call(f"compose {intent}", time.monotonic() - t0)
            return intent, composed
        why = declined[0] if declined else "the compiler has no reading of this point"
        refused = check_coverage(intent, scope)
        if refused is not None:
            history.log(f"  -> (coverage) {refused}")
            return intent, TemplateResult(data={"message": refused, "season": scope.season}, answer=refused)
        refusal = unanswerable(self.con, reading, question)
        if refusal is not None:
            history.log(f"  -> (compose) {why} - refused ({refusal.data['refused']}): nothing here reads that shape")
            return intent, refusal
        history.log(f"  -> (compose) {why}: refused")
        self.unanswered = f"{intent}: {why}"
        return None

    def _ran(self, query: Query | TeamQuery) -> None:
        """Keep the query the compiler executed (:attr:`ran`)."""
        self.ran = query

    def _try_compose(self, question: str, reading: Reading, history: RunHistory, declined: Callable[[str], None] | None = None) -> TemplateResult | None:
        """The compiler's answer to the point the parser read
        (``association.query.compose.answer``): the compiled intents' only
        answer, and the step after a template's refusal. Answered exactly
        like a template's own result - same name-reading and coverage-caveat
        attachment as :meth:`_run_template` - except its trace line names the
        point on the relation it composed rather than a template's intent and
        slots, since that IS the interesting fact about a compiled answer.

        The module is looked up at call time, not bound at import: that is
        what tests monkeypatch (``association.query.compose.answer``).
        """
        from . import compose

        # Planned once, beside the Reading (:meth:`_reading`), before anything
        # answers; the compiler takes that planning and never plans.
        assert self.planned is not None
        with collect_name_readings() as readings:
            composed = compose.answer(
                TemplateContext(con=self.con, out_dir=self.out_dir),
                reading,
                trace=lambda point: history.log(f"  -> (reading) {point.describe()}"),
                declined=declined,
                planned=self.planned,
                ran=self._ran,
            )
        if composed is None:
            return None
        for name_reading in readings:
            history.log(f"  -> (player) {name_reading}")
            composed.answer = f"{composed.answer} {name_reading}"
            _note(composed, name_reading)
        if readings:
            composed.data["name_readings"] = list(readings)
        note = coverage_caveat(reading.intent, reading.scope)
        if note:
            composed.answer = f"{composed.answer} {note}"
            _note(composed, note)
        point = {key: composed.data[key] for key in _COMPOSE_POINT_KEYS if key in composed.data}
        history.log(f"  -> (compose) intent={reading.intent!r} point={point}")
        return composed

    def _run_template(self, handler: Callable[[TemplateContext, Reading], TemplateResult], reading: Reading, history: RunHistory) -> TemplateResult:
        """The template's answer, with how it read any name the question left
        open. "maxey" is Tyrese because he is the only Maxey who still plays -
        a default, and a default is allowed only where it is visible and can be
        corrected, so the sentence naming who else matched and what to type for
        him is part of the answer (entities.collect_name_readings)."""
        with collect_name_readings() as name_readings:
            result = handler(TemplateContext(con=self.con, out_dir=self.out_dir), reading)
        for name_reading in name_readings:
            history.log(f"  -> (player) {name_reading}")
            result.answer = f"{result.answer} {name_reading}"
            _note(result, name_reading)
        if name_readings:
            result.data["name_readings"] = list(name_readings)
        return result

    def _ask_inner(self, question: str, history: RunHistory) -> Answer:
        # A question too short to be one is refused before anything reads
        # it (refusals.too_short): no model call, no guess at "Tatum rec".
        short = too_short(question)
        if short is not None:
            self.unanswered = f"fewer than {MIN_QUESTION_WORDS} words"
            history.log(f"  -> (refused) {self.unanswered}")
            return self._answer(question, history, short, "refused")
        fast = self._try_fast_path(question, history)
        if fast is not None:
            intent, templated = fast
            return self._answer(question, history, templated.answer, "fast", intent=intent, data=templated.data, artifacts=templated.artifacts)
        # Nothing read the question: the refusal names why, in the words the
        # parser, the template or the compiler gave it up with (#129 - the
        # agent this replaced answered one such question in 23).
        why = self.unanswered or "no reading of the question"
        history.log(f"  -> (refused) {why}")
        return self._answer(question, history, refusal_text(why), "refused")
