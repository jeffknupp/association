"""The answering loop: the parser's Reading through the compiler's readers
and the sayer, and a refusal naming why where nothing has a reading of the
question. One model call, the normalizer's, is the whole cost; nothing after
it reaches a model.

Every shape the words ask is the compiler's, read by a reader into a Result
and worded by the sayer (``compose``), and every refusal and decline a
reading comes to is a cause the planner says - answered ``"fast"``, since
having something to say about the question is an answer. Only a question
too short to be one, a reply the normalizer could not give, or a value the
Reading cannot hold is answered ``"refused"``, with a sentence naming why
(#129 records why nothing hands such a question to a model).
"""

from __future__ import annotations

import time
import traceback
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

import duckdb

from association.nba.season import calendar_season, current_season, season_on_record
from association.query.answer import AnswerContext
from association.query.coverage import check_coverage, coverage_caveat
from association.query.reading import PLAYER_ASKS, Cause, PointShape, ShapeDeclined, key_path
from association.query.result import Refusal

from .answer import Answer, AnsweredBy, Timing
from .compose.plan import PlannedQuery, Verdict, cells_unhonored, plan_point, planned_shape, refusal_result
from .compose.say import nothing_answers, say, say_career_implied, say_left_out, say_season_default, say_spelling_read, say_stat_default
from .connection import connect_read_only, latest_season_on_record
from .entities import collect_name_readings, misread_players, names_of
from .history import DEFAULT_HISTORY_DIR, RunHistory, echo_to_stderr
from .models import DEFAULT_MODEL
from .names import loaded as names_loaded
from .notes import collect as collect_remarks
from .notes import unsaid
from .parse import MIN_QUESTION_WORDS, too_short
from .reading import Alternative, Reading, ScopeError
from .season_text import season_phrase

if TYPE_CHECKING:
    from .normalizer import Normalized

# The subset of the said Answer's data a compose.answer() carries that describes
# WHAT was answered - the point on the relation - rather than the rows
# themselves. Traced so a refusal that becomes a composed answer says what it
# composed, the same way "-> (parser) asked=..." says what the words ask. See
# association.query.compose's module docstring for the full shape.
_COMPOSE_POINT_KEYS = ("skeleton", "measures", "aggregate", "group", "predicates", "window", "span", "narrowing", "player")


def _noted(said: Answer, note: str) -> Answer:
    """``said`` with a sentence appended to its text, and recorded on its
    ``data`` too, so a caller rendering from ``data`` (the web page) does not
    lose what the text-only reader is told: which name a default chose, how
    much of a season the source holds. A new Answer: the said one is frozen.
    Beneath an answer of more than one line (a table, a log, a list), on a
    line of its own: run on, it read as the table's last row ("... 83.0
    (132-159) ('carmelo anthoyn' matches no player exactly ...)")."""
    data = dict(said.data or {})
    notes = data.setdefault("notes", [])
    if isinstance(notes, list):
        data["notes"] = [*notes, note]
    return replace(said, text=f"{said.text}{chr(10) if chr(10) in said.text else ' '}{note}", data=data)


def _career_implied(reading: Reading, composed: Answer) -> Answer:
    """``composed`` with the career the parser read where no season was
    named (decision D13, its ``span`` decision) said as the default it is,
    with the wording that asks about this season instead - last, after the
    coverage caveat and the name readings. The parser records the decision;
    the answer has to state it (AGENTS.md, "A reasonable default beats a
    question": visible, and correctable)."""
    if not any(each.stage == "parser" and each.field == "span" and each.after == "career" for each in reading.decisions):
        return composed
    if _read_nothing(composed):
        # A refusal or a question back (the sayer's `message`, `say_refusal`'s
        # and `say_clarify`'s data): nothing was read over the career, and a
        # cause such as an unread number is not corrected by naming a season.
        return composed
    asks = [each.question for each in composed.alternatives if each.kind == "latest" or (each.kind == "span" and not each.span.career)]
    return _noted(composed, say_career_implied(reading.who.kind, current_season(), asks))


def _changed_unhonored(alternative: Alternative, read: Reading, planned: PlannedQuery) -> bool:
    """Whether the cell ``alternative`` changes - the career, both season
    types, or the one game a latest meeting keeps - is one its planned
    shape's reader does not state
    (:func:`~association.query.compose.plan.cells_unhonored`): the reader
    steps aside for it, and the answer is the compiler's own sentence or a
    decline ("which team scores the most points per game of all time":
    every season of a career cannot be read as a ranking of teams), never
    the alternative's reading. A named season is no cell."""
    key = planned_shape(read, planned)
    return key is not None and any(each in _changed_cells(alternative) for each, _ in cells_unhonored(planned, key))


def _changed_cells(alternative: Alternative) -> frozenset[str]:
    """The cells ``alternative`` sets that the reading it stands beside did
    not: the career (a ``span`` or ``round`` read over every season), both
    season types, and the window a ``latest`` meeting keeps."""
    if alternative.kind == "latest":
        return frozenset({"window"})
    if alternative.kind == "season_type":
        return frozenset({"both"})
    return frozenset({"career"}) if alternative.span.career else frozenset()


def _read_nothing(composed: Answer) -> bool:
    """Whether ``composed`` read nothing a default could be said of: a
    refusal or a question back (the sayer's ``message``), a name that
    matched nobody (``unmatched``: "No player found matching 'bill Russell'
    ...", which carried "(No season was named, so this reads every season
    on record ...)" beside it), or a name several players share
    (``ambiguous``, :func:`~association.query.compose.say.say_clarify`:
    "'curry' matches more than one player - did you mean Seth Curry or
    Stephen Curry?", beside which the career was offered)."""
    return bool({"message", "unmatched", "ambiguous"} & set(composed.data or {}))


def _stat_default(reading: Reading, composed: Answer) -> Answer:
    """``composed`` with the stat a single game's high was read by where its
    words named none (the parser's ``measure`` decision: points) said as the
    default it is, with the wording that names another (AGENTS.md, "A
    reasonable default beats a question": visible, and correctable). Not
    beside a refusal or a question back, which read no stat."""
    if _read_nothing(composed):
        return composed
    for each in reading.decisions:
        if each.stage == "parser" and each.field == "measure":
            composed = _noted(composed, say_stat_default(str(each.after)))
    return composed


def _spelling_read(reading: Reading, composed: Answer) -> Answer:
    """``composed`` with each near spelling the parser read as a word of the
    lexicon ("quater" as "quarter", its ``period`` decision) said as the
    reading it is, with the wording that asks without it - the near-spelled
    name's rule (AGENTS.md, "Typos are the entity index's job"), for a word."""
    for each in reading.decisions:
        if each.stage == "parser" and each.field == "period" and isinstance(each.before, str):
            composed = _noted(composed, say_spelling_read(each.before, str(each.after)))
    return composed


def refusal_text(why: str, asked: PointShape | None = None) -> str:
    """The sentence a question nothing here reads is answered with: the
    reason the fast path gave it up, as the parser or the compiler stated
    it - words that ask for nothing a reader reads, a narrowing the
    relation cannot honor, a reply the normalizer could not use - and what
    the words were read as asking (``asked``), where they asked something.
    Naming the cause is the whole point: the reader learns which part of
    the question has no answer here, where the agent this replaced spent
    minutes and then guessed. The sayer's frame
    (:func:`~association.query.compose.say.nothing_answers`).

    .. versionadded:: 5.0.0

    .. versionchanged:: 6.0.0
       Takes ``asked``, said in the shape's words ("Nothing here answers
       this question as a ranking of teams: ..."), where the reason began
       with the retired intent's name ("team_leaderboard: ...").
    """
    return nothing_answers(why, asked)


class Agent:
    """Answers one question at a time: the parser's Reading through the
    compiler, or a refusal naming why nothing had a reading.

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

    .. versionchanged:: 7.0.0
       Breaking: takes ``model``, the normalizer's model, renamed from
       ``router_model``, and holds it as :attr:`model` (renamed from
       ``router_model``).
    """

    def __init__(
        self,
        db_path: str,
        out_dir: Path,
        verbose: bool = False,
        history_dir: Path = DEFAULT_HISTORY_DIR,
        model: str = DEFAULT_MODEL,
        trace: Callable[[str], None] = echo_to_stderr,
    ):
        #: The normalizer's model (:func:`~association.query.normalizer.normalize`),
        #: which copies the names out of the question and picks a stat.
        self.model = model
        self.verbose = verbose
        self.history_dir = history_dir
        #: Why the last question was refused with no reading, or None where a
        #: the compiler or a named refusal answered it. A reader's
        #: own refusal (a clarification, a "no match", a shape nothing reads)
        #: is an answer: it looked at the question and had something to say.
        self.unanswered: str | None = None
        #: What the words were read as asking where the last question was
        #: refused with no reading after they asked something - the key the
        #: refusal names in the shape's words (:func:`refusal_text`) - or
        #: None.
        #:
        #: .. versionadded:: 6.0.0
        self.unanswered_as: PointShape | None = None
        #: The Reading the last question settled into, or None where it was
        #: refused before anything read it - what a stage snapshot records
        #: beside the answer (:func:`association.query.stages.snapshot`).
        #:
        #: .. versionadded:: 5.0.0
        self.reading: Reading | None = None
        #: The last question's Reading planned onto its relation - the
        #: Query-or-Cause (:data:`~association.query.compose.plan.Verdict`:
        #: the query, the refusal it came to or the decline it gave the
        #: point up with, :func:`~association.query.compose.plan.plan_point`)
        #: - or None where nothing was read. Planned once per question,
        #: here, and handed to whatever answers.
        #:
        #: .. versionadded:: 6.0.0
        self.query: Verdict | None = None
        #: The kinds of the remarks written for the last question whose
        #: sentence did not reach its answer (:func:`association.query.notes.unsaid`):
        #: a caveat computed and then dropped. Empty when every one was said.
        #:
        #: .. versionadded:: 5.0.0
        self.unsaid: list[str] = []
        # The normalizer's reply to the last question, which an alternative's
        # wording is read again with (``_reaches``): the same names and stat,
        # since the wording is the question's own words with a span added.
        self._normalized: Normalized | None = None
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
                A caller says what the request was: ``sys.argv`` is only
                true of a CLI and says nothing useful about a server
                handling many questions.

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
           A question of fewer than :data:`~association.query.parse.MIN_QUESTION_WORDS`
           words is refused unread, with a generic sentence, before the
           normalizer is asked.
        """
        history = RunHistory(self.verbose, self.history_dir, sink=self.trace)
        self.reading = None
        self.query = None
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
            # sentence it stays in (query/notes.py).
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
            path = history.write(command=label, question=question, answer=recorded, model=self.model)
            self.trace(f"[history] {path}  {history.summary_line()}")
        # The record's name rides on the Answer as a value, so a caller (the
        # web runner's note feature) never has to read it back out of the
        # trace line above - AGENTS.md, "Events carry trace lines verbatim".
        # After the finally, so the record is written before it is named.
        assert answer is not None
        return replace(answer, history_file=Path(path).name)

    def _answer(self, question: str, history: RunHistory, said: Answer, answered_by: AnsweredBy, shape: PointShape | None = None) -> Answer:
        """The answer ``said`` - what the sayer said, its text, data and
        files, or a refusal's sentence alone - stamped with what the
        answering loop knows of it: the question, which path answered, the
        timing read off the run that just produced it, the decisions made on
        the way and the planned shape. Every return point in _ask_inner goes
        through here so none of them can forget one."""
        return replace(
            said,
            question=question,
            answered_by=answered_by,
            timing=Timing(
                total_seconds=history.total_seconds,
                model_seconds=history.model_seconds,
                model_calls=history.model_calls,
                tool_seconds=history.tool_seconds,
                tool_calls=history.tool_calls,
            ),
            artifacts=list(said.artifacts),
            decisions=tuple(history.decisions),
            shape=shape,
        )

    def _try_fast_path(self, question: str, history: RunHistory) -> Answer | None:
        """Reading -> plan -> reader and sayer -> answer, returning what the
        sayer said, whole (:class:`~association.query.answer.Answer`, which
        :meth:`_answer` stamps). Returns None, with :attr:`unanswered`
        naming why, where nothing here reads the question: words that ask
        for nothing a reader reads, a value the Reading cannot hold, or a point
        the compiler and the named refusals could not take.

        The said Answer is returned, rather than just its text, because
        that text is only one of the things the reader produced - see
        :attr:`Answer.data <association.query.answer.Answer.data>`, the
        answer's structured values."""
        self.unanswered = None
        self.unanswered_as = None
        read = self._read_or_refuse(question, history)
        if read is None:
            return None
        reading = self._reading(read, history)
        # A name the model supplied that the question never held, and that
        # nothing in the question can replace, is refused by name rather than
        # answered: a model invents whole names, not only nicknames -
        # "compare sga and embiid" came back with Jusuf Nurkic as the second
        # name, and every stage after this one would answer about him
        # perfectly. Only where the answer would actually be about that player - a
        # stray name on a team question changes no answer.
        if reading.misread and reading.asked in PLAYER_ASKS:
            misread = misread_players(list(reading.misread))
            history.log(f"  -> (player) {misread}")
            return Answer(data={"message": misread, "misread": list(reading.misread)}, text=misread)
        settled = self._settled_before_reading(reading, history)
        if settled is not None:
            return settled
        if reading.asked is not None:
            return self._run_compiled(reading, history)
        return None

    def _reading(self, reading: Reading, history: RunHistory) -> Reading:
        """``reading`` kept, planned and its decisions recorded. Split out of
        :meth:`_try_fast_path` for the complexity gate."""
        # What the words ask and what will answer, said beside the reading in
        # one line: the key the words ask is the Reading's own.
        path = "the compiler" if reading.asked is not None else "no reader: refused unless a named refusal has its cause"
        kind = reading.subject.kind if reading.subject is not None else None
        history.log(f"  -> (parser) asked={key_path(reading.asked)} kind={kind!r} filters={reading.stated()} - {path}")
        self.reading = reading
        # PLAN, once: the point on its relation, or why there is none. The
        # parser read the point; it does not plan it.
        self.query = plan_point(reading)
        for decision in reading.decisions:
            history.record_decision(decision)
        return reading

    def _read_or_refuse(self, question: str, history: RunHistory) -> Reading | None:
        """The Reading the parser reads for ``question``, or None with the
        reason the question is being refused recorded. Split out of
        :meth:`_try_fast_path` for the complexity gate."""
        from association.query.normalizer import ModelUnavailable

        t0 = time.monotonic()
        try:
            read = self._read_question(question, history)
        except ModelUnavailable as exc:
            # The reason has to name the server rather than the question: a
            # normalizer that could not be reached read nothing of it.
            history.record_model_call(time.monotonic() - t0)
            history.log(f"  -> (normalizer) {exc}: refused")
            self.unanswered = str(exc)
            return None
        except ScopeError as exc:
            # A slot nothing can hold - "stephen curry last 0 games" reads as a
            # window of 0 - stops the parser at the typed value's door (the
            # window tagger's): refused here, the way a reader's refusal is,
            # rather than crashing.
            history.record_model_call(time.monotonic() - t0)
            history.log(f"  -> (scope) {exc}: refused")
            self.unanswered = f"a value the Reading cannot hold: {exc}"
            return None
        history.record_model_call(time.monotonic() - t0)
        if read is None:
            history.log("  -> (normalizer) no usable reply: refused")
            self.unanswered = "the normalizer returned no usable reply"
        return read

    def _read_question(self, question: str, history: RunHistory) -> Reading | None:
        """The Reading the parser reads: the
        model copies the names out of the question and picks a stat key
        (:func:`~association.query.normalizer.normalize`), and
        :func:`~association.query.parse.read_route` checks both and reads
        what the words ask and every filter from the words, over the name
        index read here, before it runs (:func:`~association.query.entities.names_of`).
        None when the model's reply is unusable."""
        from association.query.normalizer import normalize
        from association.query.parse import read_route

        normalized = normalize(self.model, question)
        self._normalized = normalized
        if normalized is None:
            return None
        history.log(f"  -> (normalizer) names={normalized.names} stat={normalized.stat!r}")
        return read_route(names_of(self.con), question, normalized.names, normalized.stat)

    def _settled_before_reading(self, reading: Reading, history: RunHistory) -> Answer | None:
        """What is decided before any reader runs, from the Reading alone:
        the refusal the words come to that no point answers past
        (:attr:`Reading.refused <association.query.reading.Reading.refused>`
        - a championship question a team ranking would answer fluently and
        wrongly; a player named on a question whose shape has no reading for
        one, "alperen şengün alltime record" read as the league's standings),
        which the planner said when it planned the Reading; and, where the
        words ask for no shape a reader reads, the first thing they name that
        nothing reads (:attr:`Reading.unsupported <association.query.reading.Reading.unsupported>`
        - "most opponent bench points allowed ..."), said by the planner.
        With no reader and no named refusal, the ``no_shape`` cause, said by
        the planner ("what its words ask has no reader"). Nothing here reads
        the question again."""
        if reading.refused is not None:
            # The planner's refusal (plan_point), worded here.
            assert isinstance(self.query, Refusal)
            said = say(self.query)
            history.log(f"  -> (refusal) {reading.refused.kind}: {said.text}")
            return said
        if reading.asked is not None:
            return None
        if reading.unsupported:
            refusal = refusal_result(reading.unsupported[0])
            history.log(f"  -> (refusal) {reading.unsupported[0].kind}: nothing here reads that shape")
            return refusal
        history.log("  -> (refusal) no_shape: what its words ask has no reader")
        return refusal_result(Cause(kind="no_shape", facts={"shape": None}))

    def _run_compiled(self, reading: Reading, history: RunHistory) -> Answer | None:
        """A shape the words ask, which the compiler alone answers (every
        one). Where the
        compiler has no reading of the point, in this order: the
        compiler's own reason - the planner refusing a narrowing the relation
        cannot honor (the decline :func:`~association.query.compose.plan.plan_point`
        returns) - names
        why the question is refused; a season under a table's floor is
        refused, never answered from nothing; and a shape nothing here reads
        is refused by name - the first thing the words name that nothing
        reads (:attr:`Reading.unsupported <association.query.reading.Reading.unsupported>`),
        said by the planner. A shape's own decline - a narrowing it cannot
        honor, a point its only reader reads nothing of - is a cause the
        planner says in the decline's own sentence (``cannot_honor``,
        ``no_reading_of_point``), at the decline's place in this order,
        answered: a refusal that names why is an answer."""
        t0 = time.monotonic()
        if reading.defaulted is not None and isinstance(self.query, Refusal):
            # A defaulted-word refusal is said
            # after the coverage floor and the words nothing reads have had
            # their look, as a decline is - a season under the floor and a
            # playoff round are the truer causes, and both are checked
            # below for a decline; this is the same order for a refusal.
            refused = check_coverage(self._planned_shape(), reading)
            if refused is not None:
                history.log(f"  -> (coverage) {refused}")
                return Answer(data={"message": refused, "season": reading.span.season}, text=refused)
            if reading.unsupported:
                history.log(f"  -> (refusal) {reading.unsupported[0].kind} said before the defaulted word's")
                return refusal_result(reading.unsupported[0])
        declined: list[str] = []
        causes: list[Cause] = []
        composed = self._try_compose(reading, history, declined=declined.append, declined_by=causes.append)
        if composed is not None:
            history.record_tool_call(f"compose {key_path(reading.asked)}", time.monotonic() - t0)
            return composed
        why = declined[0] if declined else "the compiler has no reading of this point"
        # The floor of the point the words named (planned_shape, declared on
        # every verdict), over the point's own scope; no point, no floor.
        refused = check_coverage(self._planned_shape(), reading)
        if refused is not None:
            history.log(f"  -> (coverage) {refused}")
            return Answer(data={"message": refused, "season": reading.span.season}, text=refused)
        if reading.unsupported:
            # What the words name that nothing reads, said in place of the
            # plain decline - only here, where the answer side declined, so
            # a question the compiler answers is answered.
            refusal = refusal_result(reading.unsupported[0])
            history.log(f"  -> (compose) {why} - refused ({reading.unsupported[0].kind}): nothing here reads that shape")
            return refusal
        if causes:
            # The shape's decline, by its cause, said by the planner where
            # the plain decline was - the same sentence.
            history.log(f"  -> (compose) {why} - refused ({causes[0].kind})")
            return refusal_result(ShapeDeclined.under(causes[0], reading.asked))
        history.log(f"  -> (compose) {why}: refused")
        self.unanswered, self.unanswered_as = why, reading.asked
        return None

    def _try_compose(self, reading: Reading, history: RunHistory, declined: Callable[[str], None] | None = None, declined_by: Callable[[Cause], None] | None = None) -> Answer | None:
        """The compiler's answer to the point the parser read
        (``association.query.compose.answer``): every shape's only
        answer. The name readings the reader noted and the coverage caveat
        are attached here, and the trace line names the point on the
        relation it composed, since that IS the interesting fact about a
        compiled answer.

        The module is looked up at call time, not bound at import: that is
        what tests monkeypatch (``association.query.compose.answer``).
        """
        from . import compose

        # Planned once, beside the Reading (:meth:`_reading`), before anything
        # answers; the compiler takes that planning and never plans.
        assert self.query is not None
        with collect_name_readings() as readings:
            composed = compose.answer(
                AnswerContext(con=self.con, out_dir=self.out_dir),
                reading,
                trace=lambda point: history.log(f"  -> (reading) {point.describe()}"),
                declined=declined,
                declined_by=declined_by,
                planned=self.query,
            )
        if composed is None:
            return None
        for name_reading in readings:
            history.log(f"  -> (player) {name_reading}")
            composed = _noted(composed, name_reading)
        if readings:
            composed = replace(composed, data={**(composed.data or {}), "name_readings": list(readings)})
        shape = self._planned_shape()
        note = coverage_caveat(shape, reading)
        if note:
            composed = _noted(composed, note)
        composed = self._unmatched_fingerprint(reading, composed)
        composed = _spelling_read(reading, composed)
        composed = _stat_default(reading, composed)
        composed = self._offered(reading, composed, history)
        composed = _career_implied(reading, composed)
        point = {key: composed.data[key] for key in _COMPOSE_POINT_KEYS if composed.data is not None and key in composed.data}
        history.log(f"  -> (compose) asked={key_path(reading.asked)} shape={key_path(shape)} point={point}")
        return composed

    def _offered(self, reading: Reading, composed: Answer, history: RunHistory) -> Answer:
        """``composed`` carrying the alternatives of its defaults that reach
        what they say (:attr:`Answer.alternatives
        <association.query.answer.Answer.alternatives>`, decision D23):
        each wording the parser wrote (:attr:`Reading.alternatives
        <association.query.reading.Reading.alternatives>`) read again with
        the question's own names and stat, and offered only where that
        reading is the alternative's span over everything else as it stood,
        and plans to a query - a button that re-asks the same question, or
        asks one nothing here answers, is worse than none. Not beside a
        refusal or a question back, which read no span. The season default
        an offered career stands beside is recorded as the ``season_default``
        decision where the heading names it; the career's, in the D13
        sentence (:func:`_career_implied`)."""
        if not reading.alternatives or _read_nothing(composed) or self._normalized is None:
            return composed
        offered = tuple(each for each in reading.alternatives if self._reaches(reading, each, history))
        if not offered:
            return composed
        season, season_type = current_season(), reading.span.season_type or 2
        for each in offered:
            if each.kind in ("span", "round") and each.span.career and season_phrase(season, season_type) in composed.text:
                # Recorded where the heading names the season it read: the
                # remark is the heading's own phrase, said already.
                say_season_default(season, season_type, [each.question])
        return replace(composed, alternatives=offered)

    def _reaches(self, reading: Reading, alternative: Alternative, history: RunHistory) -> bool:
        """Whether ``alternative``'s wording, read by the parser with the
        question's own names and stat, reads exactly that alternative of
        ``reading`` (:meth:`Alternative.reached_by
        <association.query.reading.Alternative.reached_by>`) and plans to a
        query; traced either way."""
        from association.query.parse import read_route

        assert self._normalized is not None
        read = read_route(names_of(self.con), alternative.question, self._normalized.names, self._normalized.stat)
        planned = plan_point(read)
        reached = alternative.reached_by(reading, read, reading.asked, read.asked) and isinstance(planned, PlannedQuery) and not _changed_unhonored(alternative, read, planned)
        history.log(f"  -> (alternative) {'offered' if reached else 'not offered'}: {alternative.question!r}")
        return reached

    def _unmatched_fingerprint(self, reading: Reading, composed: Answer) -> Answer:
        """A "vs" fingerprint that drew one polygon answered half of itself:
        the parser read which names the question compares beyond the
        players the reading holds (:attr:`Reading.left_out <association.query.reading.Reading.left_out>`),
        and the sayer says it (:func:`~association.query.compose.say.say_left_out`)
        - after the coverage caveat, and only where the reader answered:
        never beside the coverage floor's refusal, which read no name (the
        floor, then the answer and this note)."""
        if reading.left_out is None or check_coverage(self._planned_shape(), reading) is not None:
            return composed
        return _noted(composed, say_left_out(reading.left_out))

    def _planned_shape(self) -> PointShape | None:
        """What the last question's point is read and said as
        (:func:`~association.query.compose.plan.planned_shape`, from its
        Reading and its planning), or None where nothing was read or planned."""
        if self.reading is None or self.query is None:
            return None
        return planned_shape(self.reading, self.query)

    def _ask_inner(self, question: str, history: RunHistory) -> Answer:
        # A question too short to be one is refused before anything else
        # reads it - the parser's first verdict (parse.too_short), said by
        # the planner: no model call, no guess at "Tatum rec".
        short = too_short(question)
        if short is not None:
            self.unanswered, self.unanswered_as = f"fewer than {MIN_QUESTION_WORDS} words", None
            history.log(f"  -> (refused) {self.unanswered}")
            return self._answer(question, history, Answer(text=refusal_result(short).text), "refused")
        fast = self._try_fast_path(question, history)
        if fast is not None:
            # The planned point's shape, which the page renders the data by:
            # the one the answer side chose its reader by.
            return self._answer(question, history, fast, "fast", shape=self._planned_shape())
        # Nothing read the question: the refusal names why, in the words the
        # parser or the compiler gave it up with (#129).
        why = self.unanswered or "no reading of the question"
        history.log(f"  -> (refused) {why}")
        return self._answer(question, history, Answer(text=refusal_text(why, self.unanswered_as)), "refused")
