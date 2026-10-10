"""The stages that settle a question's route - the intent and the slots a
template reads - from the question's own words.

A reader hands them a raw route, and :func:`settle` runs them over it: the
parser (:func:`association.query.parse.read_route`) with the parent intent
its grammar names and the names and stat the normalizer copied out of the
question, and the subject reading (:mod:`association.query.subject`) again
for a child intent the question's words assign. Each stage reads a slot from
the text, or checks one against it - the season and its type, the window,
the side of the ball, the teammates absent - so a value the question never
states is dropped rather than trusted. What they settle on is a
:class:`Route`.

Slot values are advisory: every one of them is re-validated in the templates package
against a whitelist before it reaches SQL. Nothing here is trusted.

.. versionchanged:: 5.0.0
   The model classification is gone: ``route()``, which asked a model to
   classify the question into an intent and its slots under a constrained
   schema (the ``router_prompt`` module) and then ran these stages over its
   reply. The parser reads the question instead (ROADMAP plan item 6).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field, fields, replace
from typing import TYPE_CHECKING, Any

from . import lexicon, reading
from .cuts import CutsContext, CutsRead, read_cuts
from .decisions import Decision
from .lexicon import BY_QUARTER, GAMES_WORDS, HALF_WORDS, ORDER_WORDS, PAST_N_SEASONS, PERIOD_AS_CONDITION, PERIOD_LEADERS, PERIOD_TOP, QUARTER_WORDS, WHO_RANKS
from .line import LineContext, LinesRead, read_lines, threshold_named
from .measure import BOOLEAN_KEYS, GAMES_STATS, MeasureContext, named, names_a_stat, read_measure
from .period import PeriodContext, read_period, which_period
from .reading import Claim, Companion, Cuts, Line, Period, Scope, Window
from .span import SpanContext, needed, range_named, read_by, read_span
from .span import claimed as claimed_once
from .subject import is_team_name, subject_named_in, team_named_in_text, team_words_in
from .window import WindowContext, read_window

if TYPE_CHECKING:
    from . import subject as subject_reading

# Questions no template can answer, recognized from the text rather than left
# to the model. Deliberately tiny: not a rules engine, just subjects that read
# like a supported shape ("Steph Curry's average X") while asking for something
# no template computes, so a near-miss template absorbs them confidently.
# Shot distance was the first entry and left by earning a template - a subject
# belongs here only until one covers it.
# "Fouling out" (lexicon.FOULED_OUT) is six personal fouls - an NBA rule, not
# something a 3B knows: the stage below names the count and its stat by it,
# and the lines tagger reads the line (fouls at six).

# The family's words - a quarter or half named at all, a ranking of
# players by one, a period as a condition, "by quarter" - are the
# lexicon's since Phase 3, step 2 (QUARTER_WORDS, HALF_WORDS,
# PERIOD_LEADERS, PERIOD_GAMES_WORDS, PERIOD_AS_CONDITION, BY_QUARTER,
# each with its reason beside it), and WHICH period the words name is the
# period tagger's one reading (``period.which_period``).

# "td3" is a triple-double, and the model reads its "3" as a shot value
# (lexicon.TRIPLE_DOUBLE_ABBREVIATION): the compiler's own measure words
# already read "td3s" (query/point.py), so only the slots have to say it.
_DRAW_WORDS = re.compile(r"\b(?:plot|chart|draw|render|visuali[sz]e|graph|show me a)\b", re.IGNORECASE)


# A TEAM's quarter score (no player named) is exempted below: linescores answer
# it exactly, via templates.team_quarter_points. A PLAYER's quarter or half is
# templates.period_split's job now - it reads shot_chart rather than the
# fragile plays-table derivation this comment used to warn was needed - and the
# override below sends it there instead of forcing it to the agent.
def _is_team_quarter_points(raw: dict[str, Any]) -> bool:
    return raw.get("intent") == "team_quarter_points" and _subject_of(raw).player is None


def _subject_of(raw: dict[str, Any]) -> reading.Subject:
    """Who the stages' working route is about: the typed subject the
    subject reading handed them (:class:`Named`), as a stage has settled it."""
    who = raw.get("subject")
    return who if isinstance(who, reading.Subject) else reading.Subject()


def _names_a_period_subject(handed: Named) -> bool:
    """Whether the question's own grammar names a PLAYER as the scorer ("did
    Jokic score in the 3rd quarter") - the #170 shape, which the model files
    as the team's quarter with no player at all, and which the exemption for
    a team's own quarter must not cover: measured after the 5.0.0 prompt
    shrink, "How many points did Jokic score in the 3rd quarter against
    Boston?" arrived as ``team_quarter_points`` for the Nuggets and the
    exemption kept it there. The same reader and the same team guard as
    :func:`_recover_period_subject`, so "did the 76ers score" stays the
    team's."""
    return handed.grammar is not None and not is_team_name(handed.grammar)


# A coach question, which has no answer here and is refused rather than left to
# fall through (compose/plan.py's COACH_REFUSAL says why, and what ESPN
# actually serves). Read from the question's own words for the same reason
# `period_split` is: the word is unmistakable, and a new ROUTER_SCHEMA enum
# value or ROUTER_PROMPT line would move slots on unrelated questions.
#
# Deliberately only the word itself and its inflections. No coach is named
# without it in practice ("nick nurse coaching record all-time"), and matching
# a bare surname would be the substring trap `players_named_in` was written
# against - "nurse" and "rivers" are ordinary words, and Doc Rivers, Nick Nurse
# and Quin Snyder are all real people a player question could name. Checked
# against the warehouse: no player or team has "coach" anywhere in its name, so
# the word cannot collide with a subject.
_COACH_WORDS = re.compile(r"\bcoach(?:es|ed|ing|es'|'s)?\b|\bhead\s+coach\b", re.IGNORECASE)

CODE_ASSIGNED_INTENTS: frozenset[str] = frozenset({"coach", "period_split", "period_leaderboard"})
"""Intents no model can emit, because :func:`route` assigns them from the
question's own text.

Kept out of ``ROUTER_SCHEMA``'s enum and out of ``ROUTER_PROMPT`` on purpose.
Both are load-bearing on every other question: a new enum value changes the
decoding grammar and a new prompt line changes slots on unrelated questions -
adding one reproducibly flipped "What was the Lakers record last season?" from
``team`` "Lakers" to "Los Angeles Lakers". A period is legible from the
question ("1q", "4th qtr", "first half") with no help from the model, so it
costs nothing to read it here and nothing to route on it.

``test_every_ported_template_has_an_intent_in_the_schema`` exempts these, and
the exemption is why this is a named constant rather than a literal in a test:
a template that is unreachable by BOTH routes is dead, and the two lists have
to disagree deliberately rather than by drift.

.. versionadded:: 2.2.0
"""


class RouterUnavailable(RuntimeError):
    """The model could not be asked at all - ollama is down, or cannot serve
    that model. Raised by :func:`association.query.normalizer.normalize`.

    Distinct from ``normalize`` returning None, which means the model
    answered with something unusable. Both are refused, and neither is
    fatal; what differs is the sentence a person gets, which is the entire
    answer.

    .. versionadded:: 4.4.0

    .. versionchanged:: 5.0.0
       Raised by the normalizer: the router's own model call is gone.
    """


@dataclass
class Route:
    """The stages' settled reading of a question: the intent, and the typed
    :class:`~association.query.reading.Scope` a template or the compiler
    reads - every slot that survived validation, a dropped one the field at
    its default (never a sentinel), so a template's own default applies
    normally. ``decisions`` are what the parser decided reading it
    (:func:`~association.query.parse.read_route`): each move of the intent
    off the parent its grammar named, and each slot a child's stages moved,
    as values the Reading carries on
    (:func:`~association.query.parse.reading_from_route`). A route the stages
    settle carries none.

    .. versionchanged:: 5.0.0
       ``scope`` (a :class:`~association.query.reading.Scope`) replaces
       ``slots`` (a dict): the stages write the typed Scope, and nothing after
       the model holds a slot dict (ROADMAP plan item 6, step (f)).
       :attr:`slots` is the Scope projected to a slot dict - the trace's and
       a test's shape, read nowhere on the answering path. ``decisions``
       added.
    """

    intent: str
    scope: Scope = field(default_factory=Scope)
    decisions: tuple[Decision, ...] = ()
    #: Who the question was read to be about, where the parser read the
    #: question (:func:`~association.query.parse.read_route`): the one
    #: reading of the subject, which the parser's last step settles under
    #: the route's intent rather than read again. ``None`` only on what
    #: the stages hand back (:func:`settle`), before the parser attaches it.
    #:
    #: .. versionadded:: 5.0.0
    subject: subject_reading.Subject | None = None
    #: The characters of the question the reader's rules read
    #: (:class:`~association.query.reading.Claim`): each tagger's, cut to
    #: what its reading depends on (:func:`~association.query.span.needed`),
    #: the grammars' and the stages' by their decisions
    #: (:func:`~association.query.span.read_by`), the subject reading's,
    #: carried onto the Reading.
    #:
    #: .. versionadded:: 6.0.0
    claims: tuple[Claim, ...] = ()
    #: The model's reply the route was read beside - the names it copied
    #: out of the question and the stat key it picked
    #: (:func:`~association.query.parse.read_route`'s arguments, as given) -
    #: so the parser's last step can leave the words the model accounts for
    #: out of the unread ones (:attr:`Reading.unread
    #: <association.query.reading.Reading.unread>`), as the claims ledger
    #: leaves them out of its count. Empty where nothing asked the model.
    #:
    #: .. versionadded:: 6.0.0
    model_names: tuple[str, ...] = ()
    model_stat: str = ""

    @property
    def slots(self) -> dict[str, Any]:
        """The scope as a slot dict (:meth:`~association.query.reading.Scope.to_slots`).

        .. versionchanged:: 5.0.0
           A projection of :attr:`scope`, no longer the field itself.
        """
        return self.scope.to_slots(split_by_presence=self.intent == "with_without")

    def projected(self) -> dict[str, Any]:
        """Every field as a Route was recorded until Phase 3, step 2
        (:func:`~association.query.stages.plain`): the scope's companions
        under the with/without split's slot where the route is the split's.

        .. versionadded:: 6.0.0
        """
        out = {f.name: getattr(self, f.name) for f in fields(self)}
        out["scope"] = self.scope.projected(split_by_presence=self.intent == "with_without")
        return out


@dataclass(frozen=True)
class Named:
    """Who the subject reading hands the stages (:func:`settle`): the typed
    subject as it stands before them (:class:`~association.query.reading.Subject`
    - the names the reading settled, a span the model copied that nothing
    placed, the kind and the position group), the team it read the subject
    set against (the cuts tagger takes it), and the two readings of the words
    the stages settle a name from under the intent they choose: the subject
    the question's grammar names (``grammar``: "maxey" in "the sixers record
    when maxey scored 15+", ``subject.subject_named_in``) and the team
    nicknames the words hold (``team_words``, ``subject.team_words_in``).
    The stages read these and no word for a name of their own; a name they
    settle is the reading's.

    Until Phase 3, step 2 the stages took the names as slots
    (``_MODEL_SLOTS``: ``player``, ``players``, ``team``, ``teams``,
    ``opponent``) and read the grammar's subject and the team words from the
    question themselves.

    .. versionadded:: 6.0.0
    """

    subject: reading.Subject = field(default_factory=reading.Subject)
    opponent: str | None = None
    grammar: str | None = None
    team_words: tuple[str, ...] = ()

    @classmethod
    def of(cls, question: str, *, subject: reading.Subject | None = None, opponent: str | None = None) -> Named:
        """What the stages are handed for ``question``: ``subject`` and
        ``opponent`` as given (nobody, where none is), with the grammar's
        subject and the team words read from the question's words by the
        subject reading's own readers."""
        return cls(subject=subject if subject is not None else reading.Subject(), opponent=opponent, grammar=subject_named_in(question), team_words=team_words_in(question))


# The side of the ball, the rate, the team's total, the stat words, a team
# metric's alias, the advanced metrics, game score, a 2-point percentage, a
# ranking by shot distance, the shots and the attempts beside a make are the
# measure tagger's since Phase 3, step 2 (``measure.read_measure`` over the
# lexicon's words); the stages read nothing of the measure but the model's
# key as context (``raw["stat"]``), for the intents that turn on it.

# "record" asked with a counting intent means wins and losses, not a count of
# games. Measured: "Sixers record when Embiid scores 30 points this season" came
# back as threshold_count and was answered with the league's 30-point games,
# Embiid dropped.
_RECORD = re.compile(r"\brecord\b", re.IGNORECASE)


# The subject a single-game high's or a threshold count's grammar names, when
# the model drops it, is the subject reading's (``subject.subject_named_in``,
# over the lexicon's SUBJECT_OF_HIGH, SUBJECT_OF_COUNT and SUBJECT_OF_HAVE),
# read once and handed to the stages (:class:`Named`) since Phase 3, step 2.


# Which split a player_splits question asks for. Exactly one or nothing.
SPLIT_WORDS: dict[str, re.Pattern[str]] = {
    "home_away": re.compile(r"\bhome\b.{0,15}\b(?:away|road)\b|\b(?:away|road)\b.{0,15}\bhome\b", re.IGNORECASE),
    "starter_bench": re.compile(r"\b(?:starter|starting|starts|bench|reserve)\b", re.IGNORECASE),
    "wins_losses": re.compile(r"\bin\s+(?:wins|losses)\b|\bwins\s+(?:vs\.?|versus|and|or)\s+losses\b", re.IGNORECASE),
    "month": re.compile(r"\b(?:by|each|per)\s+month\b|\bmonthly\b", re.IGNORECASE),
}

# One half of the starter/bench split, where the question names a half.
#
# `SPLIT_WORDS["starter_bench"]` matches "starter" or "bench" and records only
# the CATEGORY, which is right for `player_splits` - a splits question wants
# both groups side by side - and useless to a reader that has to FILTER.
# "Jrue holiday last 50 games as a starter" is a log of his starts, not a log
# of everything with a starter/bench breakdown, and `AnswerContext` carries
# no question text, so the direction can only be recovered here.
#
# Read from the question for the same reason the measure tagger reads the side
# of the ball: it costs nothing and cannot move a slot on any other question,
# where teaching `ROUTER_SCHEMA` a new value reproducibly can. A question that
# names BOTH halves ("starting vs coming off the bench") keeps the category, on
# purpose - that IS the splits question.
_STARTER_WORDS = re.compile(r"\b(?:starter|starting|starts|started)\b", re.IGNORECASE)
_BENCH_WORDS = re.compile(r"\b(?:bench|reserve|reserves)\b", re.IGNORECASE)


def _split_side(split: str, question: str) -> str:
    """``split``, narrowed to the half the question names where it names one.

    Returns ``"starter"`` or ``"bench"`` for a question about one half, and the
    original category otherwise - both halves named, or a split that has no
    halves (``month``, ``home_away``, ``wins_losses``).
    """
    if split != "starter_bench":
        return split
    starter, bench = bool(_STARTER_WORDS.search(question)), bool(_BENCH_WORDS.search(question))
    if starter and not bench:
        return "starter"
    if bench and not starter:
        return "bench"
    return split


# "vs"/"against", for a game log's last N meetings - see route().
_VERSUS_WORDS = re.compile(r"\b(?:vs\.?|versus|against)\s", re.IGNORECASE)


# A ranking of TEAMS, asked with a player-ranking intent. Measured: "which team
# scores the most points per game" came back as `leaderboard` and was answered
# with the players' scoring leaders - a table of real, correct numbers about the
# wrong kind of thing.
_TEAM_SUBJECT = re.compile(
    r"\b(?:which|what)\s+teams?\b|\bby\s+(?:a\s+)?teams?\b|\bper\s+team\b|\bteams?\s+(?:with\s+the|that|leaders|rankings?)\b",
    re.IGNORECASE,
)
_PLAYER_RANKING_INTENTS = frozenset({"leaderboard", "single_game_high", "threshold_count"})
#: Intents the model files for a "<team> when <player> reaches N" question,
#: each of which would answer the player's own line instead of the team's
#: record under the condition.
_WHEN_REACHES_REROUTABLE = frozenset({"player_stat", "threshold_count", "game_log", "team_stat", "team_record", "other"})

# A fingerprint is one named artifact, and a question that never names it is not
# asking for one. Measured: "Plot Curry's threes from last season" came back as
# `fingerprint` under two different prompt revisions, having routed correctly
# only while the prompt happened to be a particular length.
#: The fingerprint itself, by name - not the looser words above ("netpoints"
#: is also a leaderboard's metric and player_netpoints' whole subject).
_FINGERPRINT_NAMED = re.compile(r"\bfinger\s?prints?\b|\bradar\b", re.IGNORECASE)
_FINGERPRINT_REROUTABLE = frozenset({"player_compare", "player_stat", "player_netpoints", "other", "game_log"})
_FINGERPRINT_WORDS = re.compile(r"\bfinger\s?prints?\b|\bradar\b|\bnet\s?points?\b|\bplay[- ]types?\b", re.IGNORECASE)
_SHOT_WORDS = re.compile(r"\bshots?\b|\bthrees\b|\b3s\b|\b(?:3|three)[- ]?pointers?\b|\bjumpers?\b|\blayups?\b|\bdunks?\b|\bchart\b", re.IGNORECASE)


def _route_shot_distance_subject(intent: str, slots: dict[str, Any], question: str) -> None:
    """A ranking by shot distance names no player: any `player` the model
    filled, filler or real, is dropped - `leaderboard` never reads one for
    real (a named player is refused separately), and a filler value here
    ("player": "player" on a question that names nobody) would otherwise
    reach `subject.apply_subject` first and refuse for the WRONG cause -
    "read as a question about player, who the question does not mention" -
    before the ranking's own refusal (``shot_distance_ranking``, the measure
    tagger's sentinel key) ever runs.

    .. versionadded:: 4.4.0
       ``_route_leaderboard_shot_distance``, which wrote the sentinel too, until Phase 3, step 2.
    """
    if intent != "leaderboard" or not lexicon.SHOT_DISTANCE_RANKED.search(question):
        return
    who = _subject_of(slots)
    if who.player is not None:
        slots["subject"] = replace(who, players=())


# A game log asked for by name. Measured: "luka ft log" routed to player_stat
# and was answered with a season average.
_LOG_WORDS = lexicon.LOG_WORDS

# A team's nickname, city and abbreviation (lexicon.TEAM_NICKNAME, TEAM_CITIES,
# TEAM_ABBREVIATIONS) and the readers that tell a team's name from a player's
# (``subject.is_team_name``) and find it in the words (``subject.team_named_in_text``)
# are the subject reading's since Phase 3, step 2.


# "best record" and "worst record" rank the league; with no team named they are
# team_leaderboard's question. Measured: "worst record 2025-26" came back as
# team_record with team='worst'. "the league" or "NBA" between the two words
# is the same ranking: "Best NBA record since January 31st 201" (yardstick-v2
# F104) came back as team_record with no team and fell through.
_BEST_WORST_RECORD = re.compile(r"\b(?:best|worst)\s+(?:nba\s+|league\s+)?records?\b", re.IGNORECASE)


def _route_coach_intent(raw: dict[str, Any], question: str) -> bool:
    """True when the question is about a coach, which nothing here can answer.

    Runs before every other stage and stops them: a coach question is refused
    whatever the model made of it, and the slots the model returned are for a
    question that cannot be answered anyway. Reading them would only risk
    a team or player name reaching the refusal.
    """
    if not _COACH_WORDS.search(question):
        return False
    raw["intent"] = "coach"
    return True


def _recover_period_subject(raw: dict[str, Any], handed: Named, asked: tuple[Period, Claim] | None, named_player: bool, ranks_players: bool) -> str | None:
    """The player a quarter or half question names when the model's reply
    dropped `player` entirely - split out of :func:`_route_period_intents` to
    keep it inside the complexity gate.

    ISSUES.md #170: "How many points did Jokic score in the 3rd quarter
    against Boston?" arrived from the model with NO player at all - it filled
    `team`/`opponent` instead (`team: 'Boston Celtics'`, `opponent: 'Denver
    Nuggets'`, neither one asked for by name), which reads exactly like the
    "team's own half" shape `_route_period_intents` handles next and answered
    Boston's quarter to a question about Jokic. The question's own grammar
    still names him (``subject.subject_named_in``, read once and handed to
    the stages as :attr:`Named.grammar` - the same reading ``threshold_count``
    and ``single_game_high`` settle their own dropped subject from), so it
    gets a turn before the team branch does - but only where nothing ranks
    players (that wins over a recovered subject the same way it wins over the
    model's own player, and has to) and the recovered "subject" is not
    itself a team's name, since "did the 76ers score" fits the identical
    grammar and must stay the team's own question.
    """
    if asked is None or named_player or ranks_players:
        return None
    candidate = handed.grammar
    if candidate is not None and not is_team_name(candidate):
        raw["subject"] = replace(_subject_of(raw), players=(candidate,))
        return candidate
    return None


def _route_period_split_slots(raw: dict[str, Any], question: str, subject: str | None) -> None:
    """The slots `period_split` reads once its intent is chosen - split out
    of :func:`_route_period_intents` to keep it inside the complexity gate."""
    # The model's required `stat`, kept only where the words name one, and
    # a log asked for in place of a season average, are the measure
    # tagger's reading (``measure.read_measure``) since Phase 3, step 2.
    if subject is not None:
        # The player came from the text, not from the model's own
        # `player` slot - so its `team`/`opponent` are no more
        # trustworthy than the player it already dropped. Keep one
        # only where the question's own words actually say it (the
        # same "may be fiction" check `subject.read_subject`
        # runs for a name): "Boston Celtics" is a word the Jokic
        # question used, "Denver Nuggets" is a word it never wrote.
        opponent = team_named_in_text(question, _subject_of(raw).team) or team_named_in_text(question, raw.get("opponent"))
        raw["subject"] = replace(_subject_of(raw), teams=())
        raw.pop("opponent", None)
        if opponent is not None:
            raw["opponent"] = opponent


def _route_period_intents(raw: dict[str, Any], question: str, handed: Named) -> None:
    """Fouling out, and a quarter or half: intents code assigns from the question's own words."""
    low = question.lower()
    if lexicon.FOULED_OUT.search(low):
        # The count of a player's foul-outs: the line itself (fouls at six)
        # is the lines tagger's (``line.read_lines``), the stat the measure tagger's.
        raw["intent"] = "threshold_count"
    ranks_players = PERIOD_LEADERS.search(low) is not None or (PERIOD_TOP.search(low) is not None and not _team_slot_or_word(raw, handed))
    if (QUARTER_WORDS.search(low) and (ranks_players or not _is_team_quarter_points(raw) or _names_a_period_subject(handed))) or HALF_WORDS.search(low):
        # A named player's quarter or half now HAS a template, so the override
        # sends it there instead of to the agent - but only when the question
        # names one and the period is legible, since `period_split` answers
        # about a player and nothing else. Everything else keeps the old
        # behavior: slots are kept, because the agent sees the conversation
        # rather than the Route, and the log line shows what the model thought.
        asked = which_period(question)
        if asked is not None and PERIOD_AS_CONDITION.search(low):
            raw["intent"] = "other"
            return
        # No second `_is_team_quarter_points` check: it means "this intent, and
        # NO player", so it can never be true here where a player is named. The
        # team's own quarter is already exempted by the outer condition.
        named_player = _subject_of(raw).player is not None
        subject = _recover_period_subject(raw, handed, asked, named_player, ranks_players)
        _route_period_intents_choose(raw, question, handed, asked, named_player or subject is not None, ranks_players, subject)


def _route_period_intents_choose(raw: dict[str, Any], question: str, handed: Named, asked: tuple[Period, Claim] | None, named_player: bool, ranks_players: bool, subject: str | None) -> None:
    """Which period intent a quarter or half question is, once its period, its
    player and whether it ranks are known - split out of
    :func:`_route_period_intents` to keep it inside the complexity gate.
    The period itself is the tagger's to write (:func:`~association.query.period.read_period`,
    run once the intent is settled); the stage only chooses."""
    low = question.lower()
    if asked is not None and not named_player and ranks_players:
        # Players ranked by a quarter or a half - templates.games
        # .period_leaderboard. A team may still be named ("knicks 1st
        # quarter scoring leaders"), where it narrows the ranking to that
        # team's players rather than becoming the subject.
        raw["intent"] = "period_leaderboard"
    elif asked is not None and not named_player and (_team_slot_or_word(raw, handed)):
        # A TEAM's half. A team's QUARTER never reaches here - the
        # exemption above keeps it on its own template - but a half always
        # does, because the model maps "first half" onto period 1 and that
        # is wrong for a team the same way it is for a player. The
        # linescore holds both quarters, so the template sums them. The
        # team is the model's `team` slot, or - measured, the model filed
        # none for "Celtics 2nd half scoring this season" (the one
        # check_routing.py gap after step 3) and "least points scored by
        # the wizards in the first half" (yardstick-v2 F064) - the one
        # nickname the question itself holds (:attr:`Named.team_words`).
        raw["intent"] = "team_quarter_points"
    elif asked is not None and named_player:
        raw["intent"] = "period_split"
        _route_period_split_slots(raw, question, subject)
    elif asked is None and named_player and BY_QUARTER.search(low):
        # A named player's four quarters side by side (#162): period_split
        # with no period, which its point reads as the breakdown
        # (point._default_period_split).
        raw["intent"] = "period_split"
        _route_period_split_slots(raw, question, subject)
    elif asked is None and not named_player and BY_QUARTER.search(low) and not _team_slot_or_word(raw, handed):
        # Every player's four quarters side by side - the league's; a
        # team's players' breakdown ("knicks points by quarter") reads
        # as the TEAM's by quarter, which is not built, and stays `other`.
        raw["intent"] = "period_leaderboard"
    else:
        raw["intent"] = "other"


def _route_triple_double_abbreviation(raw: dict[str, Any], question: str) -> None:
    """ "td3s" is the stat ``triple_double``, never a shot value, and one named
    player's count of them is a ``player_stat`` the compiler answers (the
    template refuses a stat with no per-game column, and the compiler then
    counts the games). Measured over the corpus (CASES, the StatMuse feed,
    yardstick-v2): "luka td3s home" is the one question holding the word;
    the model filed it as ``other``, so only that intent is moved - a
    leaderboard or a count the model chose keeps its own intent."""
    if not lexicon.TRIPLE_DOUBLE_ABBREVIATION.search(question):
        return
    # The stat (``triple_double``) and the shot value it is never one of are
    # the measure tagger's reading since Phase 3, step 2.
    # `shot_chart` too, since the 5.0.0 prompt shrink: the "3" reads as a
    # shot to the model ("luka td3s home" arrived as a chart of his twos),
    # and a count of triple-doubles is never a chart unless the question
    # asks for one to be drawn.
    if raw["intent"] in ("other", "shot_chart") and _subject_of(raw).player is not None and not _DRAW_WORDS.search(question):
        raw["intent"] = "player_stat"


def _team_slot_or_word(raw: dict[str, Any], handed: Named) -> bool:
    """Whether a team is named - the one team the subject reading handed the
    stages, or failing that exactly one nickname the question holds
    (:attr:`Named.team_words`), which is then the subject's team, as typed.
    Two nicknames name a matchup, not a subject, and file nothing."""
    if _subject_of(raw).team is not None:
        return True
    if len(set(handed.team_words)) != 1:
        return False
    raw["subject"] = replace(_subject_of(raw), teams=handed.team_words[:1])
    return True


def _route_team_and_player_intents(raw: dict[str, Any], question: str) -> None:
    """A team where a player ranking was asked for, a record, a fingerprint with
    no fingerprint words, and a player measured against a team."""
    if raw["intent"] in _PLAYER_RANKING_INTENTS and _TEAM_SUBJECT.search(question):
        # A team ranking has a template; a team's single-game record and a
        # count of team games do not, and answering either with players is the
        # substitution this exists to stop.
        raw["intent"] = "team_leaderboard" if raw["intent"] == "leaderboard" else "other"
    if raw["intent"] == "threshold_count" and _RECORD.search(question):
        raw["intent"] = "record_when"
    if raw["intent"] in _WHEN_REACHES_REROUTABLE and lexicon.WHEN_REACHES.search(question) and threshold_named(question) is not None:
        # "stats for sixers when maxey scored 20+ points" (yardstick-v2 F087):
        # a team's games divided by a NUMBER a player reached is record_when
        # whatever the model filed - it chose player_stat and answered Maxey's
        # own average, then (after "for <team>" became his tenure) his career
        # average with the 76ers. Two readers agree before it moves: the
        # "when <someone> scores/has/gets" clause and a threshold in the text.
        raw["intent"] = "record_when"
    if raw["intent"] == "fingerprint" and not _FINGERPRINT_WORDS.search(question):
        raw["intent"] = "shot_chart" if _SHOT_WORDS.search(question) else "other"
    if raw["intent"] in _FINGERPRINT_REROUTABLE and _FINGERPRINT_NAMED.search(question):
        # The reverse: "compare fingerprints for embiid vs jokic in 2026"
        # arrived as player_compare after the 5.0.0 prompt shrink, and a
        # table of averages is not the radar the word asks for. The word
        # is as unmistakable as "coach"; nothing else here is named it.
        raw["intent"] = "fingerprint"
    listed = _listed(raw)
    if raw["intent"] == "player_compare" and sum(map(is_team_name, listed)) == 1 and len(listed) == 2:
        # One player compared with a team is his games against it. Measured:
        # "compare curry vs the celtics this season" arrived as player_compare
        # with the Celtics in `players`; subject.apply_subject made them the
        # opponent, which player_compare cannot honor, so the question fell
        # through to the agent while player_stat answers it exactly. Two
        # players and a team stay a comparison, and refuse the opponent.
        raw["intent"] = "player_stat"
    _route_one_player_intents(raw, question, listed)
    _route_matchup_against_team(raw, question, listed)


def _route_pair_over_seasons(raw: dict[str, Any], question: str, listed: list[str]) -> None:
    """ "jokic vs cade since 2022": two players "vs" over a span of seasons,
    with no compare word, is the pair relation's meetings (player_matchup
    honors ``since``; a comparison does not, and fell through - day5).
    "Luka vs Giannis this year" stays a comparison."""
    if raw["intent"] != "player_compare" or len(listed) != 2 or not _VERSUS_WORDS.search(question) or _COMPARE_WORDS.search(question):
        return
    if range_named(question) is not None or PAST_N_SEASONS.search(question):
        raw["intent"] = "player_matchup"


def _route_one_player_intents(raw: dict[str, Any], question: str, listed: list[str]) -> None:
    """A line that is a log, and a line with no player that ranks the
    league - split out of :func:`_route_team_and_player_intents` for the
    complexity gate. (A comparison of ONE player, which the model filed as a
    one-name list - "alperen sengun double-doubles vs southeast division
    career away" - moved its name to the line's slot here until Phase 3,
    step 2: the subject reading hands one player as the one player, so a
    comparison of one is the parent grammar's line, and the move read
    nothing on the 2,710 readings.)"""
    if raw["intent"] == "player_stat" and _LOG_WORDS.search(question):
        raw["intent"] = "game_log"
    _route_pair_over_seasons(raw, question, listed)
    if raw["intent"] == "game_log" and _HOW_MANY.search(question) and raw.get("stat") in GAMES_STATS and not any(p.search(question) for p in ORDER_WORDS.values()):
        # "how many games did embid play" arrived as a log of his most
        # recent game (order recent, limit 1) after the 5.0.0 prompt shrink;
        # the count is the line's ("... in 38 games"), which player_stat
        # states, and a log of one game states nothing of the kind. The
        # games key is no measure: the measure tagger drops it.
        raw["intent"] = "player_stat"
    if raw["intent"] == "player_stat" and not _named_player(raw) and WHO_RANKS.search(question):
        # No player named and "who ... the most": the league's ranking, not
        # one player's line - "who attempted the most three pointers this
        # season?" arrived as player_stat after the 5.0.0 prompt shrink.
        raw["intent"] = "leaderboard"


_COMPARE_WORDS = re.compile(r"\bcompar(?:e[ds]?|ing|ison)\b|\bbetter\b|\bwho scores more\b|\bside by side\b", re.IGNORECASE)


def _named_player(raw: dict[str, Any]) -> bool:
    """Whether the subject reading handed the stages a player, one or more."""
    return bool(_subject_of(raw).players)


def _listed(raw: dict[str, Any]) -> list[str]:
    """The players handed as a list - two or more, a comparison or a pair
    (the ``players`` slot until Phase 3, step 2); none for one player."""
    players = _subject_of(raw).players
    return list(players) if len(players) >= 2 else []


def _route_matchup_against_team(raw: dict[str, Any], question: str, listed: list[str]) -> None:
    """A ``player_matchup`` whose second "player" is a team."""
    if raw["intent"] == "player_matchup" and any(map(is_team_name, listed)):
        # One of the "two players" is a team: this is a player's games against
        # it. subject.apply_subject moves the team to `opponent`.
        raw["intent"] = "game_log" if _LOG_WORDS.search(question) or GAMES_WORDS.search(question) else "player_stat"
    if raw["intent"] == "player_matchup" and len(listed) < 2 and _subject_of(raw).player is not None:
        # The same question, arriving in the other shape. The rule above reads
        # `players`, and the model routinely fills the SINGULAR `player` and an
        # `opponent` instead - "keon ellis stats vs trailblazers", "Kd games vs
        # wizards", "De'angelo russell vs pistons". player_matchup needs two
        # players and had one, so eight feed queries fell through to the agent
        # where player_stat and game_log answer them exactly, both honoring
        # `opponent`.
        #
        # An opponent that is NOT a team is left alone: "jay huff game log vs
        # Embiid" really is a matchup between two players, and the model put
        # the second one in `opponent`.
        teams = _subject_of(raw).teams
        against = raw.get("opponent") or next(iter(teams if len(teams) >= 2 else ()), None)
        if isinstance(against, str) and is_team_name(against):
            raw["intent"] = "game_log" if _LOG_WORDS.search(question) or GAMES_WORDS.search(question) else "player_stat"


_PLAYED_TOGETHER_REROUTABLE = frozenset({"head_to_head", "team_record", "team_stat", "game_log", "other"})


def _route_line_and_record_intents(raw: dict[str, Any], question: str, companions: tuple[Companion, ...], handed: Named) -> None:
    """A history that is really a line, a record ranking, and a career high."""
    if raw["intent"] == "player_history" and (not names_a_stat(question) or (_VERSUS_WORDS.search(question) and handed.team_words)):
        # A season-by-season history of one stat is neither "career averages"
        # (no stat named - the whole line) nor a career against one team.
        # Measured: "Jokic career averages" answered with points by season,
        # "derozan career points vs knicks" refused on its opponent.
        raw["intent"] = "player_stat"
    if raw["intent"] == "with_without" and not _absent(companions) and not _played(companions) and any(pattern.search(question) for pattern in ORDER_WORDS.values()):
        # No teammate named, and "the last 7 games": a team's log, not a
        # split. "KNICKS point differential over the last 7 games" arrived
        # as with_without after the 5.0.0 prompt shrink (day5) and answered
        # the last seven REGULAR-season games where the last seven were the
        # Finals - game_log reads both types for "last N" (_route_game_log_recent_span).
        raw["intent"] = "game_log"
    if raw["intent"] in _PLAYED_TOGETHER_REROUTABLE and _RECORD.search(question) and threshold_named(question) is None and (_played(companions) or _absent_by_phrase(question, companions)):
        # "PHI record when Embiid and Paul George play" arrived as
        # head_to_head, the Pacers invented as the opponent, after the 5.0.0
        # prompt shrink; with no threshold it is the with/without split
        # (#156's reading, which `_route_threshold` makes for record_when).
        # "record with Embiid out" is the same split, from the other side.
        raw["intent"] = "with_without"
    if raw["intent"] == "team_record" and _BEST_WORST_RECORD.search(question) and not handed.team_words:
        # The ranking's metric, the record, is the measure tagger's reading
        # of the same words (the team metric's alias "record"); a ranking of
        # every team is about no one team.
        raw["intent"] = "team_leaderboard"
        raw["subject"] = replace(_subject_of(raw), teams=())
    if raw["intent"] == "player_stat" and lexicon.CAREER_HIGH.search(question):
        # A career high is one game's total, which player_stat never reports.
        # Measured: "Diabate career high assists" was answered with his assists
        # per game.
        raw["intent"] = "single_game_high"


#: The model-era keys of the span, window, cuts, period, line and companion
#: families a raw route may still carry (a test's payload; a settled route
#: run again): every one is read from the words by its tagger or by the
#: subject reading, so none passes the stages. The opponent is not among
#: them: it is the subject reading's word, which the cuts tagger takes as
#: settled.
_MODEL_SPAN_KEYS: frozenset[str] = frozenset(
    {
        "season",
        "season_ref",
        "season_type",
        "season_type_unstated",
        "span",
        "since",
        "until",
        "order",
        "limit",
        "rank",
        "ranked_by",
        "date",
        "venue",
        "situation",
        "round",
        "game_n",
        "season_n",
        "own_team",
        "period",
        "half",
        "threshold",
        "above",
        "below",
        "period_condition",
        "with_player",
        "without",
        "conditions",
    }
)


def _route_blank_slots(raw: dict[str, Any]) -> dict[str, Any]:
    """The slots, with blanks and the span, window and cuts families'
    model-era keys dropped: the season and the season type are the span
    tagger's (:func:`~association.query.span.read_span`), the window the
    window tagger's (:func:`~association.query.window.read_window`) and
    the games' cuts the cuts tagger's (:func:`~association.query.cuts.read_cuts`),
    each read last, over the intent the stages settle - a bare ``season``
    the words do not name was never trusted (#95: the model invented one),
    the reader the model's ``season_ref`` named "last season" for reads the
    words, a model ``limit`` of 1 was filler on four readers, and a model
    ``date`` with no calendar day in the question was the date half of #95
    ("fingerprint maxey vs jaylen brown 2026" arrived with
    ``date='2026-01-01'`` and was refused for a cause the question never
    gave)."""
    # A blank string is how the model says "no value" for a required slot;
    # dropping it here keeps every template's `slots.get(...) or default`
    # working and keeps the logged Route readable.
    return {k: v for k, v in raw.items() if k != "intent" and k not in _MODEL_SPAN_KEYS and not (isinstance(v, str) and not v.strip())}


def _played(companions: tuple[Companion, ...]) -> tuple[str, ...]:
    """The teammates who PLAYED beside the subject, by name (an own-side
    ``played`` companion) - what the stages decide a with/without split by."""
    return tuple(c.player for c in companions if c.side == "own" and c.predicate == "played")


def _absent(companions: tuple[Companion, ...]) -> tuple[str, ...]:
    """The teammates who sat the games out, by name."""
    return tuple(c.player for c in companions if c.absent)


def _absent_by_phrase(question: str, companions: tuple[Companion, ...]) -> bool:
    """Whether a player named beside the subject sat out by an absence
    phrase - "with Embiid out", "when Embiid is injured" - rather than by a
    plain "without Embiid": the wording that makes a record a with/without
    split."""
    return bool(_absent(companions)) and lexicon.ABSENT_NAMED.search(question) is not None


def _route_count_intents(raw: dict[str, Any], slots: dict[str, Any], question: str, companions: tuple[Companion, ...], handed: Named) -> None:
    """A count or a record whose words carry no line: what each becomes.
    Until Phase 3, step 2 this stage also wrote the ``threshold`` slot off
    the words (and ``stat`` for "scores 30"); the lines tagger reads every
    line now (:func:`~association.query.line.read_lines`), and the stage
    asks only whether the words name one (:func:`~association.query.line.threshold_named`)."""
    named = threshold_named(question)
    # A record "when X and Y played" is a with_without question - the games
    # they were all in, beside the ones they were not - and not a record_when
    # one, which divides a season by a NUMBER a player reached. With no
    # threshold there is no number to divide by, and record_when refused all
    # four phrasings the web session asked (#156). A question that does name
    # a threshold keeps its intent, so "Sixers record when Embiid scores 30
    # points" is untouched. "When Embiid is out" is the same split from the
    # other side: an absent companion, which the subject reading writes.
    if raw["intent"] == "record_when" and named is None and (_played(companions) or _absent_by_phrase(question, companions)):
        raw["intent"] = "with_without"
    if raw["intent"] == "threshold_count" and slots.get("stat") in ("games", "game") and named is None:
        # "bam adebayo career games in the month of march" (yardstick-v2 F096)
        # arrived as a count of games over the line 0 on the stat "games" -
        # no line at all, and no template or compiler reads it, so it fell
        # through. A player's games with no line on them are his game log,
        # which states how many there were and his line in them. Measured
        # over the replayed corpus: this question is the only one routed so.
        raw["intent"] = "game_log"
        # The count's subject, settled as a count's would be
        # (_route_subject_slots) - game_log is not one of the intents that
        # step settles it for, and the model dropped Bam here.
        _settle_grammar_subject(slots, handed)
    if raw["intent"] == "threshold_count" and named is None and not lexicon.BELOW.search(question):
        # A count of games needs a threshold. Without one, "who has the most
        # threes" is a season ranking - measured, it arrived here with none and
        # fell through. A ceiling IS the count's line ("Sga games with under
        # 14 fta": compose.counts reads the phrase as the count), so
        # a count stated as one keeps its intent with no threshold at all.
        raw["intent"] = "leaderboard"


def _route_split_slot(slots: dict[str, Any], question: str) -> None:
    """The split, read for every intent, not only player_splits: it is a
    scoping slot, so the reader that answers one honors it and every other
    refuses. Measured: "Joe Ingles stats when starting vs coming off the
    bench" was answered with his season minutes, "Giannis stats by month"
    with his points by season. (The calendar stage this was part of wrote
    the date, the round, the series game and the ordinal season beside it
    until Phase 3, step 2; the cuts tagger reads those.)"""
    split = _route_calendar_slots_split(question)
    if split is not None:
        slots["split"] = split


def _route_calendar_slots_split(question: str) -> str | None:
    """The one split ``question`` names (:data:`SPLIT_WORDS`), narrowed to the
    half it names where it names one (:func:`_split_side`); None for none, or
    for two. The parser reads it again over the question with a teammate's
    start blanked out ("maxey points when embiid starts" is Embiid's start,
    not Maxey's own starter split)."""
    splits = [name for name, pattern in SPLIT_WORDS.items() if pattern.search(question)]
    return _split_side(splits[0], question) if len(splits) == 1 else None


# A ``team`` the words name no franchise by - a rank word ("least", #172),
# "all-NBA", "the league" - was dropped by two stages here until Phase 3,
# step 2 (``_route_intent_slots``, ``_route_team_slots``): the subject
# reading hands the stages a team only where the words name one it resolves,
# so neither ever fired on the 2,710 readings, and both went.


# A count asked of one player with no season in sight: "how many times has
# embiid fouled out?" is 0 this season and 9 in his career, and only the second
# is the question. Product decision (2026-09-19): an unscoped count by a named
# player reads as his career, and the answer names the scope it used.
_HOW_MANY = re.compile(r"\bhow\s+many\b", re.IGNORECASE)


# The intents whose missing subject is read back out of the question's grammar.
# `record_when` joined them for a question that cost 583 seconds and produced
# nothing: "what was the sixers record when maxey scored 15+ points?" routed
# correctly, with the team, the stat and the threshold all right and no
# `player` at all, so the template raised "record_when needs a player" and the
# agent spent nearly ten minutes failing to write the join. The name was
# already sitting in the grammar `_SUBJECT_OF_HIGH` reads ("maxey scored"); it
# was simply never asked for here.
#
# Nothing about this widens what counts as a subject. Note the difference from
# the other two: a `record_when` with no player is not a league question but an
# unanswerable one (it is in `PLAYER_REQUIRED_INTENTS`), so restoring the
# subject can only turn a refusal into an answer, never a league ranking into
# one man's.
_SUBJECT_RESTORED_INTENTS = ("single_game_high", "threshold_count", "record_when")


def _route_subject_slots(intent: str, slots: dict[str, Any], handed: Named) -> None:
    """A single-game high's or a threshold count's missing subject. (A
    log's last meetings with an opponent across seasons, and a count's
    career, are the span tagger's: :func:`~association.query.span.read_span`.)"""
    if intent in _SUBJECT_RESTORED_INTENTS:
        # An optional name the model dropped, settled from the question's own
        # grammar (``subject.subject_named_in``, :attr:`Named.grammar`). Only
        # where the reader reads one player: a leaderboard with no player IS
        # the league's ranking, and so is a threshold_count with no player -
        # settling the subject only where the question's own grammar names
        # one (#138) never turns a genuine league question into one about
        # somebody it only appears to name.
        _settle_grammar_subject(slots, handed)


def _settle_grammar_subject(slots: dict[str, Any], handed: Named) -> None:
    """The player the question's grammar names (:attr:`Named.grammar`), as
    the one player the working route is about - where nobody is named yet."""
    who = _subject_of(slots)
    if not who.players and handed.grammar is not None:
        slots["subject"] = replace(who, players=(handed.grammar,))


#: The measure's model-era slots a raw route may carry (a test's payload):
#: read as the tagger's context, never passed to the Scope.
_MEASURE_CONTEXT_KEYS: tuple[str, ...] = ("stat", "side", "shot_value", "fields")


def settle(intent: str, slots: Mapping[str, Any] | Scope, question: str, companions: tuple[Companion, ...] = (), *, lines: tuple[Line, ...] = (), handed: Named | None = None) -> Route:
    """The route the stages settle on for ``intent`` over ``slots``: the
    parser's raw route (:func:`association.query.parse.read_route` runs them
    under the parent its grammar names), or a settled route run again under
    an intent assigned after it settled.

    That is how :mod:`association.query.subject` assigns a child intent
    (``threshold_count`` under a ``game_log``, ``player_history`` under a
    ``player_stat``: its ``KIND_ASSIGNED_INTENTS``): the question's words
    name the intent, and the child's own slots (``threshold`` from "30+",
    ``limit`` as a count of seasons from "the past 4 seasons", ``kind`` of a
    streak, a ``split``) are the ones these stages already read off the
    text, and the window tagger reads a history's count of seasons under
    it - so re-running them under the child is the whole recovery, and
    one definition of each slot rather than a second reader per child.
    The stages may settle on a DIFFERENT intent than
    asked - a count with no threshold is a ranking, a "when X and Y played"
    record is ``with_without`` - and the caller reads the returned intent
    rather than assuming its own.

    ``slots`` is the model's stat (a test's side, shot value and columns
    beside it: the measure tagger's context) - or a settled route's typed
    :class:`~association.query.reading.Scope`, run again under a child, whose
    subject and opponent are what is handed then; the stages' own working
    dict never leaves this module, and the Route they return carries the
    Scope. ``handed`` is who the subject reading hands the stages
    (:class:`Named`: the typed subject, the opponent, and the grammar's
    subject and the team words the stages settle a name from): the stages
    read it and settle the subject the Route carries from it, and read no
    name from the words themselves. Left out, nobody is named. A slot dict
    holding a name is refused: names are handed typed.

    ``companions`` is who the subject reading found beside the subject
    (:class:`~association.query.reading.Companion`): the stages decide a
    with/without split by them and write none - the subject reading writes
    them onto the Scope (``subject.apply_subject``). ``lines`` are the lines
    read before the stages ran (a line in a quarter, whose words the parser
    blanks so the period is not read as the one measured), which join the
    tagger's. Left out - the stages run alone - nobody is beside him.

    .. versionadded:: 5.0.0

    .. versionchanged:: 5.0.0
       Takes a :class:`~association.query.reading.Scope` as well as a slot
       dict, and returns a Route carrying the typed Scope (ROADMAP plan item
       6, step (f)).

    .. versionchanged:: 5.0.0
       Takes ``beside``; the stages' own readers of the names after "with"
       and "without" are gone (``ROADMAP.md``, Phase 1).

    .. versionchanged:: 6.0.0
       Takes the typed ``companions`` (``router.Beside`` is gone) and the
       ``lines`` read before the stages (Phase 3, step 2).

    .. versionchanged:: 6.0.0
       Takes the typed ``handed`` (Phase 3, step 2, the subject): a slot
       dict carries no name, and ``_MODEL_SLOTS`` is gone.
    """
    if isinstance(slots, Scope):
        if handed is None:
            handed = Named.of(question, subject=slots.subject, opponent=slots.cuts.opponent)
        slots = slots.to_slots()
    elif any(key in slots for key in _NAME_KEYS):
        raise ValueError(f"settle takes the names typed (router.Named), not as slots {sorted(key for key in _NAME_KEYS if key in slots)}")
    raw: dict[str, Any] = {key: value for key, value in slots.items() if key in _MEASURE_CONTEXT_KEYS}
    raw["intent"] = intent
    return _settle(raw, question, companions, lines=lines, handed=handed if handed is not None else Named.of(question))


#: The slot names a name was carried under until Phase 3, step 2 - the four
#: of the subject and the opponent - which :func:`settle` refuses in a slot
#: dict: the subject reading hands the stages who the question is about,
#: typed (:class:`Named`).
_NAME_KEYS: tuple[str, ...] = ("player", "players", "team", "teams", "opponent")


def _settle(raw: dict[str, Any], question: str, companions: tuple[Companion, ...] = (), *, lines: tuple[Line, ...] = (), handed: Named | None = None) -> Route:
    """The stages, over a raw route or a reassigned one (:func:`settle`)."""
    handed = handed if handed is not None else Named.of(question)
    # Who the question is about, as the subject reading handed it: the
    # stages' working subject, which a stage settles a name of (the
    # grammar's subject, a team's nickname) and which the Scope carries.
    raw["subject"] = handed.subject
    if handed.opponent is not None:
        raw["opponent"] = handed.opponent
    model_key = raw.get("stat")
    given = dict(raw)
    coach, slots = _settle_stages(raw, question, companions, handed)
    # What the stages read: the words whose deletion changes the intent they
    # settle or a slot they write (span.read_by) - the stages are rules over
    # the whole question ("which team", "as a starter", "log"), so what they
    # read is what their decision turned on (Phase 3, step 3).
    stage_claims = read_by(question, "intent", lambda probed: _settle_stages_read(given, probed, companions, Named.of(probed, subject=handed.subject, opponent=handed.opponent)))
    # A coach question is refused whatever the model said, and carries no
    # slots, so it short-circuits before any of the taggers below run.
    if coach:
        return Route(intent=raw["intent"], claims=claimed_once(list(stage_claims)))
    # The lines, at the position of the last stage that wrote one
    # (``_route_record_when_threshold``, the pair's stat and number), over
    # the settled intent.
    line_context = LineContext(intent=raw["intent"])
    read = read_lines(question, line_context)
    slots["lines"] = (*lines, *read.lines)
    # The measure, at the position of the last stage that wrote one of its
    # slots (the side, after the lines), over the settled intent, the
    # model's key as context and the stat the lines tagger read beside a line.
    measure_context = _measure_context(raw, question, read)
    measure = read_measure(question, measure_context)
    for slot in _MEASURE_CONTEXT_KEYS:
        slots.pop(slot, None)
    slots["measure"] = measure.measure
    # The period, the cuts, the window, then the span, last: each the one
    # reader of its family, over the intent the stages settled - the
    # period once the intent is final (the stage that chose it wrote the
    # value beside its choice, and the parser again for a team's quarter,
    # until Phase 3, step 2), the cuts at the position of the last stage
    # that wrote one (an opponent that was the without list again,
    # dropped), the span over the window and the cuts, since its rules
    # read both.
    period_context = PeriodContext(intent=raw["intent"])
    period = read_period(question, period_context)
    slots["period"] = period.period
    cuts_context = CutsContext(intent=raw["intent"], split=slots.get("split"), opponent=slots.get("opponent"), without=_absent(companions))
    cuts = read_cuts(question, cuts_context)
    slots.pop("opponent", None)
    slots["cuts"] = cuts.cuts
    window_context = WindowContext(intent=raw["intent"], boolean_stat=measure.measure is not None and measure.measure.key in BOOLEAN_KEYS)
    window = read_window(question, window_context)
    slots["window"] = window.window
    span_context = _span_context(raw["intent"], slots, question, window=window.window, cuts=cuts)
    span = read_span(question, span_context)
    slots["span"] = span.span
    # Each tagger's claims cut to the words it could not do without, asked
    # of the tagger itself (span.needed), with the context its own words
    # give it read again from the same words (the measure the words name,
    # an order word, the span's guard words); and every other word its
    # reading turns on claimed beside them, under the family's name - the
    # possessive of "curry's last game", the "record" of a matchup's career.
    claims = [
        *needed(question, period.claims, lambda q: read_period(q, period_context).period, outside="period", gives_up=True),
        *needed(question, cuts.claims, lambda q: _settle_cuts_read(read_cuts(q, cuts_context)), outside="cuts", gives_up=True),
        *needed(question, window.claims, lambda q: read_window(q, window_context).window, outside="window", gives_up=True),
        *needed(question, read.claims, lambda q: _settle_lines_read(read_lines(q, line_context)), outside="line", gives_up=True),
        *needed(question, measure.claims, lambda q: read_measure(q, _measure_context({**raw, "stat": _settle_worded_key(q, model_key)}, q, read)).measure, outside="measure", gives_up=True),
        *needed(question, span.claims, lambda q: read_span(q, _span_context(raw["intent"], slots, q, window=window.window, cuts=cuts)).span, outside="span", gives_up=True),
    ]
    # The stages' working dict crosses into the typed Scope here, once: a
    # value no field holds raises ScopeError to the parser.
    return Route(intent=raw["intent"], scope=Scope.from_slots(slots), claims=claimed_once([*stage_claims, *claims]))


def _settle_stages(raw: dict[str, Any], question: str, companions: tuple[Companion, ...], handed: Named) -> tuple[bool, dict[str, Any]]:
    """The stages before the taggers, over ``raw`` (rewritten in place):
    whether the question is a coach's (refused whatever the model said; no
    slot is read) and the slots the stages settled, with ``raw["intent"]``
    the intent they settled on."""
    # A coach question is refused whatever the model said, and carries no
    # slots, so it short-circuits before any of the stages below run.
    if _route_coach_intent(raw, question):
        return True, {}
    # The measure the words name stands over the model's key as the stages'
    # CONTEXT (the intents that turn on a games count read it); the measure
    # tagger reads the family itself once the intent is settled.
    worded = named(question)
    if worded is not None:
        raw["stat"] = worded[0]
    # The stages run in this order because each reads what the ones before it
    # rewrote: the intents code assigns decide which slots are read, and a
    # count whose words carry no line turns back into a ranking before any
    # intent-specific slot is chosen.
    _route_period_intents(raw, question, handed)
    _route_triple_double_abbreviation(raw, question)
    _route_team_and_player_intents(raw, question)
    _route_line_and_record_intents(raw, question, companions, handed)
    slots = _route_blank_slots(raw)
    _route_count_intents(raw, slots, question, companions, handed)
    _route_split_slot(slots, question)
    _route_shot_distance_subject(raw["intent"], slots, question)
    _route_subject_slots(raw["intent"], slots, handed)
    return False, slots


def _settle_stages_read(raw: dict[str, Any], question: str, companions: tuple[Companion, ...], handed: Named) -> tuple[Any, bool, dict[str, Any]]:
    """What the stages settle over ``question`` from a copy of ``raw`` - the
    intent, whether it is a coach's, the slots that reach the Scope - as one
    value, the stages' reading the words they read are claimed by
    (:func:`_settle`)."""
    probed = dict(raw)
    coach, slots = _settle_stages(probed, question, companions, handed)
    # The measure's keys are the stages' context alone: the measure tagger
    # reads the family itself, so they leave the slots before the Scope.
    return probed["intent"], coach, {key: value for key, value in slots.items() if key not in _MEASURE_CONTEXT_KEYS}


def _settle_worded_key(question: str, model_key: Any) -> Any:
    """The measure key the stages read beside the words: the one the words
    name (:func:`~association.query.measure.named`), else the model's."""
    worded = named(question)
    return worded[0] if worded is not None else model_key


def _settle_cuts_read(read: CutsRead) -> tuple[Cuts, int | None]:
    """What the cuts tagger writes into the reading: the cuts, and the year
    a dated range opened at, which the span tagger reads."""
    return read.cuts, read.dated_since


def _settle_lines_read(read: LinesRead) -> tuple[tuple[Line, ...], str | None]:
    """What the lines tagger writes into the reading: the lines, and the
    stat it read beside a threshold, which the measure tagger reads."""
    return read.lines, read.stat


def _measure_context(raw: dict[str, Any], question: str, read: LinesRead) -> MeasureContext:
    """What the measure tagger reads beside the words
    (:class:`~association.query.measure.MeasureContext`): the settled
    intent, the model's key (a test's side, shot value and columns beside
    it), the stat the lines tagger read beside a line and whether a line is
    keyed, and whether a game of ordering was named."""
    stat = raw.get("stat")
    shot_value = raw.get("shot_value")
    return MeasureContext(
        intent=raw["intent"],
        key=stat if isinstance(stat, str) and stat.strip() else None,
        side=raw.get("side") if isinstance(raw.get("side"), str) else None,
        shot_value=shot_value if isinstance(shot_value, int) and not isinstance(shot_value, bool) else None,
        fields=tuple(raw["fields"]) if isinstance(raw.get("fields"), (list, tuple)) else (),
        line_stat=read.stat,
        keyed_line=any(line.keyed for line in read.lines),
        order_named=any(pattern.search(question) for pattern in ORDER_WORDS.values()),
    )


def _span_context(intent: str, slots: dict[str, Any], question: str, *, window: Window, cuts: CutsRead) -> SpanContext:
    """What the span tagger reads beside the words
    (:class:`~association.query.span.SpanContext`): the stages' settled
    intent, the typed window, the typed cuts (a date, a game of a series,
    an ordinal season, and the year a range opened on a dated day starts
    the seasons at), and the guard words of other families it keeps until
    their slices move them to the lexicon."""
    return SpanContext(
        intent=intent,
        player_named=_subject_of(slots).player is not None,
        window_named=window.count is not None,
        versus=_VERSUS_WORDS.search(question) is not None,
        how_many=_HOW_MANY.search(question) is not None,
        record=_RECORD.search(question) is not None,
        order=window.order,
        limit=window.count,
        date=cuts.cuts.date,
        game_n=cuts.cuts.game_n,
        season_n=cuts.cuts.season_n,
        dated_since=cuts.dated_since,
    )
