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
from dataclasses import dataclass, field, fields
from typing import TYPE_CHECKING, Any

from . import lexicon
from .cuts import CutsContext, CutsRead, read_cuts
from .decisions import Decision
from .lexicon import BY_QUARTER, GAMES_WORDS, HALF_WORDS, ORDER_WORDS, PAST_N_SEASONS, PERIOD_AS_CONDITION, PERIOD_GAMES_WORDS, PERIOD_LEADERS, PERIOD_TOP, QUARTER_WORDS, RANK_WORDS, WHO_RANKS
from .line import LineContext, LinesRead, read_lines, threshold_named
from .measures import STAT_ALIASES
from .period import PeriodContext, read_period, which_period
from .reading import Claim, Companion, Line, Period, Scope, Window
from .span import SpanContext, range_named, read_span
from .span import claimed as claimed_once
from .window import WindowContext, read_window

if TYPE_CHECKING:
    from .subject import Subject

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
    return raw.get("intent") == "team_quarter_points" and not (isinstance(raw.get("player"), str) and raw["player"].strip())


def _names_a_period_subject(question: str) -> bool:
    """Whether the question's own grammar names a PLAYER as the scorer ("did
    Jokic score in the 3rd quarter") - the #170 shape, which the model files
    as the team's quarter with no player at all, and which the exemption for
    a team's own quarter must not cover: measured after the 5.0.0 prompt
    shrink, "How many points did Jokic score in the 3rd quarter against
    Boston?" arrived as ``team_quarter_points`` for the Nuggets and the
    exemption kept it there. The same reader and the same team guard as
    :func:`_recover_period_subject`, so "did the 76ers score" stays the
    team's."""
    candidate = _subject_named_in(question)
    return candidate is not None and not _is_team_name(candidate)


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
    subject: Subject | None = None
    #: The characters of the question the taggers consumed
    #: (:class:`~association.query.reading.Claim`): the span's
    #: (:func:`~association.query.span.read_span`), carried onto the Reading.
    #:
    #: .. versionadded:: 6.0.0
    claims: tuple[Claim, ...] = ()

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


# The side of the ball a fingerprint asked for, recognized from the text. The
# words are matched whole so "offensive" and "defensive" count but a player
# named Offenberg would not.
SIDE_WORDS: dict[str, re.Pattern[str]] = {
    "offense": re.compile(r"\boffens(?:e|ive)\b", re.IGNORECASE),
    "defense": re.compile(r"\bdefens(?:e|ive)\b", re.IGNORECASE),
}

# Kept in step with ROUTER_SCHEMA's own enum by
# test_the_side_values_match_the_router_schema - two hand-maintained lists of
# the same thing is the shape that already produced the player_compare bug.
SIDE_VALUES = ("offense", "defense", "total")


def _validate_side(slots: dict[str, Any], question: str) -> str | None:
    """Which half of a fingerprint was asked for, the question first.

    Same reasoning as the span tagger reading the year out of the
    text: the question is the source, and this slot is dropped often enough
    that deferring to the model silently answers a broader question than the
    one asked - the whole radar where its defensive half was wanted.

    Why it is dropped is worth writing down, because no prompt wording fixes
    it. `stat` is the one REQUIRED slot (see ROUTER_SCHEMA), and a constrained
    decoder fills what it must before what it may: on "Show me Wembanyama's
    defensive fingerprint chart" the model spends the adjective on
    stat="defensive" and then omits `side` entirely. Measured 6/6 at
    temperature 0, and that question appears verbatim as a worked example in
    ROUTER_PROMPT with the right answer next to it, so it is not a wording the
    prompt failed to cover.
    """
    named = [side for side, pattern in SIDE_WORDS.items() if pattern.search(question)]
    # Exactly one, or nothing. A question naming both halves is asking for the
    # whole radar, which is what leaving this unset already means.
    if len(named) == 1:
        return named[0]
    side = slots.get("side")
    return side if isinstance(side, str) and side in SIDE_VALUES else None


# "record" asked with a counting intent means wins and losses, not a count of
# games. Measured: "Sixers record when Embiid scores 30 points this season" came
# back as threshold_count and was answered with the league's 30-point games,
# Embiid dropped.
_RECORD = re.compile(r"\brecord\b", re.IGNORECASE)


# The subject of a single-game high or a threshold count, when the model
# drops it. Measured live: "most points curry scored in a game this season"
# comes back as single_game_high with NO player slot, and the answer is the
# league's high - Bam Adebayo's - to a question about one man; "how many times
# has embiid fouled out?" comes back as threshold_count with no player slot
# either, and the answer is the league's leader in 6+-foul games - Karl-
# Anthony Towns - to a question about Joel Embiid (#138). Both slots are
# optional (an empty one means "the league"), so nothing downstream restores
# either, and players_named_in cannot: "curry" is six players and it refuses
# to guess.
#
# So the subject is read from the GRAMMAR rather than from a word list. A word
# scan cannot work here: "best" is Travis Best, "game" is Jaron Blossomgame,
# "high" is Haywood Highsmith and "single" is four players, so scanning would
# hijack "the highest scoring game by a player this year". A name before a
# scoring verb or "fouled out", or carrying a possessive, is a subject; the
# question words are excluded because "who scored the most" names nobody.
_SUBJECT_WORDS = frozenset({"who", "what", "which", "that", "he", "she", "they", "it", "player", "anyone", "someone", "nobody", "team", "one", "the", "and", "any"})
#
# The leading word is optional and captured, the way the count grammars below
# capture one, because a one-word subject is a clarifying question where the
# question wrote the name out: measured over all 261 corpus questions, three
# possessives ("kobe bryant's", "Jaden mcdaniel's", "steve adam's") gave a
# bare surname matching four players each, and "bryant" does not even include
# Kobe. Which word may lead is decided in `_subject_named_in` rather than here,
# against `_COUNT_SUBJECT_WORDS` - the richer list, and the one that matters:
# without it "most points curry scored" would read "points curry" as the name.
#
# "had"/"has" is a subject position too, and a bare one - `_SUBJECT_OF_HAVE`
# below requires a leading "does/did/has/have", so "sixers record when maxey
# had 10+ rebounds" named nobody while the same question with "scored"
# answered. It is deliberately NOT in the alternation above: the words there
# are all scoring verbs and a possessive, where "had" is ordinary enough that
# it is only a subject position when a threshold follows it, which is what the
# lookahead asserts.
_SUBJECT_OF_HIGH = re.compile(
    r"\b(?:([A-Za-z][A-Za-z.'\-]*)\s+)?([A-Za-z][A-Za-z.'\-]{2,})"
    r"(?:'s\b|\s+(?:scored|scores|score|dropped|put\s+up|hung|shot|foul(?:ed|s|ing)?\s+out)|\s+ha[ds]\s+(?=\d))",
    re.IGNORECASE,
)

# None of the above covers a threshold_count named with no verb at all (#148):
# "jamal murray games with 2 threes including playoffs" fell through
# unrestored, the same shape as "Sga games with under 14 fta in his whole
# career", and (by number instead of "with") a form like "murray 30 point
# games" or "murray games of 20+ rebounds" - none of which puts a scoring verb
# or a possessive anywhere near the name. So a second grammar is tried after
# the first, anchored on "games" itself rather than a verb: a name directly
# before "games with"/"games of", or before "<N>[+] <stat> games". It also
# captures ONE more word immediately before that name, to catch a first name -
# "jamal murray games with" resolves uniquely where a bare "murray" is five
# players (Collin Murray-Boyles, Dejounte, Jamal, Keegan, Kris all have a 2026
# box score) and would only trade the league-ranking bug for an unnecessary
# clarifying question. The extra word is kept only when it is not itself one
# of the stopwords below - "which players have games with 30+ points" must not
# read "players have" as a name.
#
# Measured against the full routing corpus
# (`/home/jeff/association-research/statmuse-2026-09/feed_queries.txt`), this
# grammar matches exactly the two real cases above and nothing else -
# "last 10 games of scottie barnes" does not match because the word before
# "games" there is "10", not a letter, and "bam adebayo career games in the
# month of march" does not match because "career" sits directly before
# "games" and is a stopword, not a name (there is no verb-based grammar this
# shape fits either, so it is left unrestored rather than guessed at). The
# extra stopwords below ("career", "has", "many", "postseason", ...) are what
# make that refusal-by-omission work: without them, "bam adebayo career games"
# would read "career" as the player and "how many 40+ points games does
# lebron james have" would read "many" - both a refusal naming the wrong
# cause (AGENTS.md, "the same bug has a mirror image"), not a name the
# question ever offered as the subject. This grammar is kept separate from
# `_SUBJECT_OF_HIGH` rather than folded into it: a shared pattern let a
# trailing possessive ("murray's games of...") get swallowed whole into the
# captured word before the new "games of" alternative even got a chance to
# apply, since the possessive's own `'s\b` branch was no longer the only way
# to succeed. Kept apart, `_SUBJECT_OF_HIGH` matches "murray" via its own
# `'s\b` branch exactly as it always did, and this grammar is never reached
# for that question at all.
_COUNT_SUBJECT_WORDS = _SUBJECT_WORDS | frozenset(
    {
        # A stat's own name sits before "10+ rebound games" in "the most 30+
        # point 10+ rebound games", where it is the first line's noun, not a
        # subject: read as one, "point" resolved to Sir'Dominic Pointer.
        "point",
        "points",
        "pt",
        "pts",
        "rebound",
        "rebounds",
        "reb",
        "rebs",
        "assist",
        "assists",
        "ast",
        "steal",
        "steals",
        "stl",
        "block",
        "blocks",
        "blk",
        "three",
        "threes",
        "double",
        "triple",
        "players",
        "has",
        "have",
        "having",
        "his",
        "her",
        "their",
        "its",
        "these",
        "those",
        "some",
        "many",
        "how",
        "much",
        "several",
        "few",
        "most",
        "all",
        "career",
        "season",
        "postseason",
        "playoff",
        "playoffs",
        "regular",
        "such",
        "no",
        "every",
        "each",
        # Function words that can sit directly before a name and are not part
        # of it. These matter for the LEADING word `_SUBJECT_OF_HIGH` now
        # captures: "sixers record when maxey had 10+ rebounds" read "when
        # maxey" as the name. Rejecting them as a name in their own right is
        # right too - "with" is the word AGENTS.md records reading as Jeff
        # Withey - and it can only make the count grammars more conservative,
        # which is the safe direction for a guess about a person.
        "when",
        "while",
        "if",
        "with",
        "without",
        "vs",
        "versus",
        "against",
        "did",
        "does",
        "do",
        "was",
        "were",
        "is",
        "are",
        "for",
        "by",
        "from",
        "in",
        "on",
        "at",
        "to",
        "of",
        "after",
        "before",
        "during",
        "than",
        "then",
        "but",
        "or",
        "per",
        # "single game with the most assists" is one game, not a player named
        # Single: under "X games with" it read "single" as the subject of a
        # single-game high, and asked which Singleton was meant (#260).
        "single",
        "game",
        "games",
        "total",
        "least",
        "best",
        "worst",
        "highest",
        "lowest",
    }
)
_COUNT_STAT_WORD = r"(?:point|pt|rebound|reb|assist|ast|steal|stl|block|blk|three)"
_SUBJECT_OF_COUNT = re.compile(
    r"\b(?:([A-Za-z][A-Za-z.'\-]*)\s+)?([A-Za-z][A-Za-z.'\-]{2,})\s+(?:"
    r"games?\s+with\b"
    r"|games?\s+of\b"
    r"|\d+\+?\s*[- ]?\s*" + _COUNT_STAT_WORD + r"s?\s+games?\b"
    # "bam adebayo career games in the month of march" (yardstick-v2 F096):
    # the model dropped Bam, and none of the shapes above follows a name
    # with "career games".
    r"|career\s+games?\b"
    r")",
    re.IGNORECASE,
)


# "how many 40+ point games does lebron james have": the subject sits between
# an auxiliary and "have", nowhere near the count. The model dropped LeBron
# from exactly this question (#148's shape, in a third grammar).
_SUBJECT_OF_HAVE = re.compile(r"\b(?:does|did|has|have)\s+(?:([A-Za-z][A-Za-z.'\-]*)\s+)?([A-Za-z][A-Za-z.'\-]{2,})\s+(?:have|had|got|gotten|recorded|posted)\b", re.IGNORECASE)


def _subject_named_in(question: str) -> str | None:
    """The word (or two) a single-game-high or threshold-count question makes
    its subject, or None.

    Returns the question's own words, not a resolved player: resolution
    decides whether they name somebody, and asks when it is ambiguous.
    "curry" then answers "did you mean Seth Curry or Stephen Curry?", which is
    the question asked - where the league's high is not.
    """
    for match in _SUBJECT_OF_HIGH.finditer(question):
        lead, word = match.group(1), match.group(2)
        # The richer list here too, not just for the lead. On the narrow one,
        # "Total points scored by the toronto raptors" returned the subject
        # "points" and "least points scored by the wizards" the same - a stat's
        # own noun read as a person, which is the trap `_COUNT_SUBJECT_WORDS`
        # was written for. Measured over the 261-question corpus, this loses no
        # real name and drops three pieces of junk.
        if word.casefold() in _COUNT_SUBJECT_WORDS:
            continue
        # A first name where the question gave one: "kobe bryant's" reaches
        # Kobe, where a bare "bryant" is four other players and none of them
        # him. Gated on the richer stopword list, so "most points curry
        # scored" still reads "curry" and never "points curry".
        if lead is not None and lead.casefold() not in _COUNT_SUBJECT_WORDS:
            return f"{lead} {word}"
        return word
    for pattern in (_SUBJECT_OF_COUNT, _SUBJECT_OF_HAVE):
        for match in pattern.finditer(question):
            lead, word = match.group(1), match.group(2)
            if word.casefold() in _COUNT_SUBJECT_WORDS:
                continue
            if lead is not None and lead.casefold() not in _COUNT_SUBJECT_WORDS:
                return f"{lead} {word}"
            return word
    return None


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
# Read from the question for the same reason `_validate_side` reads the side of
# the ball: it costs nothing and cannot move a slot on any other question,
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


# Words that name a TEAM stat, beyond the box-score words _STAT_WORDS knows.
_TEAM_STAT_WORDS = re.compile(r"\b(?:pace|ratings?|offen\w*|defen\w*|net|possessions?|record|wins?|losses)\b", re.IGNORECASE)  # codespell:ignore offen - a regex stem

# "vs"/"against", for a game log's last N meetings - see route().
_VERSUS_WORDS = re.compile(r"\b(?:vs\.?|versus|against)\s", re.IGNORECASE)

_LOSING_STREAK = re.compile(r"\blos(?:ing|s|e)\s+streaks?\b|\bstraight\s+losses\b|\blosses\s+in\s+a\s+row\b|\bskid\b", re.IGNORECASE)

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

# Rate stats the prompt never lists as a player `stat`, so the model reaches for
# the nearest one it knows. Measured: "kevin durant true shooting percentage
# career" came back as stat='threePointFieldGoalPct' and was answered with his
# 3-point percentage - a different stat, fluently. Named in the question, the
# stat is read from it; a template that has no such stat then refuses.
_ADVANCED_STAT_WORDS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("ts_pct", re.compile(r"\btrue[- ]shooting\b|\bts\s?%|\bts\s+pct\b", re.IGNORECASE)),
    ("efg_pct", re.compile(r"\beffective\s+(?:field\s+goal|fg)\b|\befg\b", re.IGNORECASE)),
    ("usage_pct", re.compile(r"\busage\b", re.IGNORECASE)),
)
_ADVANCED_STAT_INTENTS = frozenset({"player_stat", "player_compare", "player_history", "leaderboard", "game_log"})

# Hollinger's single-game composite (ISSUES.md #114). ROUTER_SCHEMA has no
# `stat` value for it, and `stat` is the one REQUIRED slot, so the decoder
# fills it with the nearest one it knows: "game score nba leader" arrived at
# `leaderboard` with stat='points' and was answered "Luka Doncic led the
# league in points per game ... at 33.5" - correct about points, and not what
# was asked. Same mechanism as `_validate_side`, same remedy: read it off the
# question text in route() rather than teach ROUTER_SCHEMA a new enum value,
# which would move slots on unrelated questions and cannot be measured
# without ollama.
#
# Anchored to the two-word phrase and not to "score" alone, because "score"
# alone means points everywhere else in basketball - "pacers score", "what
# was the score of the game", "total points scored by the toronto raptors"
# would all be hijacked into a Game Score leaderboard, trading one fluently
# wrong answer for several. The trailing `\b` is what keeps "per game scored
# on fridays" out: "scored" fails the boundary after "score". Verified against
# the 261-question StatMuse feed corpus: the phrase appears in exactly one
# question, and the pattern rejects all three near-miss shapes above.
_GAME_SCORE = re.compile(r"\bgame\s*scores?\b", re.IGNORECASE)

# The two templates that can look this metric up name it differently, so the
# question's own intent decides which spelling to emit - a value correct for
# one is unknown to the other. `leaderboard` reads it through
# `metrics.LEADERBOARD_METRICS`, keyed "avg_game_score" like every other
# per-game average there (avg_points, avg_rebounds); `player_stat` reads it
# through `season_line.ADVANCED_STATS`, keyed "game_score" with no
# prefix, alongside ts_pct/efg_pct/usage_pct. Left out of every other intent
# in _ADVANCED_STAT_INTENTS on purpose: player_compare, player_history and
# game_log read a player's stat line through PLAYER_STAT_COLUMNS /
# COMPARE_STAT_LINE, never ADVANCED_STATS, so a game_score value there would
# be silently unreadable rather than answered - the same "looks handled, does
# nothing" trap a stray slot leaves everywhere else in this module.
_GAME_SCORE_STAT_BY_INTENT: dict[str, str] = {"leaderboard": "avg_game_score", "player_stat": "game_score"}


def _route_game_score(intent: str, slots: dict[str, Any], question: str) -> None:
    """Hollinger's single-game composite, read from the question text - see
    the comment above :data:`_GAME_SCORE` for why and :data:`_GAME_SCORE_STAT_BY_INTENT`
    for why the value it sets depends on the intent.

    Runs after the rest of ``stat`` resolution so it overrides whatever the
    model or ``_ADVANCED_STAT_WORDS`` guessed, not just fills a gap: "game
    score" contains "score", which ``_named_a_stat`` already treats as naming
    a stat, so the model's wrong guess (typically ``points``) would otherwise
    survive untouched.

    .. versionadded:: 4.3.0
    """
    if intent not in _GAME_SCORE_STAT_BY_INTENT or not _GAME_SCORE.search(question):
        return
    slots["stat"] = _GAME_SCORE_STAT_BY_INTENT[intent]


# Two-point field-goal percentage (ISSUES.md #114). ROUTER_SCHEMA leaves
# `stat` an open string with no enum - see the comment on that field - so the
# model CAN emit "twoPointFieldGoalPct" verbatim, and measured over the
# 2026-09-20 web session it did 3 times out of 10. The other 7 it substituted
# the nearest stat ROUTER_PROMPT actually teaches ("threePointFieldGoalPct,
# fieldGoalPct, freeThrowPct"), which is fieldGoalPct here, and answered
# overall shooting where 2-point shooting was asked - the same substitution
# _ADVANCED_STAT_WORDS exists to stop for ts_pct/efg_pct/usage_pct. Read from
# the question rather than taught to the prompt, for the same reason every
# other entry in this file gives: a prompt edit moves slots on unrelated
# questions and needs ollama to measure; a regex costs nothing and cannot.
#
# "2pt", "2-pt", "2 point", "two point" and "2p", each read against
# percentage/pct/% (optionally with "field goal(s)" in between, the way the
# router's own worked examples phrase the other two percentages) - and
# nothing shorter, so "3 point percentage" and a plain "field goal
# percentage" are never swept in: neither alternative can start matching
# without a literal "2" or "two" immediately before the pt/point token, and
# "20 point" (a threshold, not a shooting split) fails the same way - the "0"
# sits where "pt"/"point" must start.
_TWO_POINT_PCT = re.compile(
    r"\b(?:2[- ]?pts?|2p|2[- ]?points?|two[- ]?points?)\b(?:\s+field\s*goals?)?\s*(?:%|pct\.?|percent(?:age)?)\b",
    re.IGNORECASE,
)
_TWO_POINT_PCT_INTENTS = frozenset({"player_history", "player_stat"})


def _route_two_point_pct(intent: str, slots: dict[str, Any], question: str) -> None:
    """2-point field-goal percentage, read from the question text - see
    :data:`_TWO_POINT_PCT`.

    Overrides whatever the model guessed, the same discipline
    :func:`_route_game_score` uses and for the same reason: "2pt" and
    "percentage" are both words ``_named_a_stat`` already reads as naming a
    stat, so a wrong guess (typically ``fieldGoalPct``) would otherwise
    survive untouched. Scoped to the two readers that can look the stat up
    (``player_history``'s ``HISTORY_COLUMNS`` and ``player_stat``'s
    ``SHOOTING_STATS``, both in ``season_line.py``) - the same
    discipline ``_GAME_SCORE_STAT_BY_INTENT`` follows for game score, so a
    value lands only where something reads it.

    .. versionadded:: 4.4.0
    """
    if intent not in _TWO_POINT_PCT_INTENTS or not _TWO_POINT_PCT.search(question):
        return
    slots["stat"] = "twoPointFieldGoalPct"


# A leaderboard ranking of shot distance (ISSUES.md #114). No such metric
# exists and none is planned - a shot's distance has no leaderboard-shaped
# rate the way a percentage or a per-game average does. Measured: "who lead
# the league in avg 3 point distance" arrived at `leaderboard` with the
# nearest real metric the model knew (threePointFieldGoalPct) and answered
# Luke Kennard's 3-point PERCENTAGE, 47.8% - a real, fluently wrong number.
# The sibling phrasing with no metric word in it ("...in shot distance for 3
# point shots") arrived with a filler `player: "player"` instead, which
# the invented-name check (agent.py) then refused for naming a player the
# question does not mention - honest-sounding, and also the wrong cause,
# since no leaderboard could answer either question anyway.
_LEADERBOARD_SHOT_DISTANCE = re.compile(
    r"\bshot\s+distance\b|\b(?:3|three)[- ]?points?\s+distance\b|\bdistance\s+for\s+(?:3|three)[- ]?points?\b",
    re.IGNORECASE,
)


_ATTEMPTED = re.compile(r"\battempt(?:ed|s)?\b|\bfga\b|\b3pa\b|\bfta\b|\bshots?\s+taken\b", re.IGNORECASE)
_MADE_TO_ATTEMPTED = {"threePointFieldGoalsMade": "threePointFieldGoalsAttempted", "fieldGoalsMade": "fieldGoalsAttempted", "freeThrowsMade": "freeThrowsAttempted"}


def _route_attempted_stat(slots: dict[str, Any], question: str) -> None:
    """ "Who attempted the most three pointers" filed ``threePointFieldGoalsMade``
    and answered makes (Jeff's session, 2026-09-24) - the model's stat enum
    reaches for the made column whenever a shot is named. The question's own
    "attempted"/"attempts"/"FGA" word decides: a made-stat beside it is the
    attempted column. Read only where the question names attempts and NOT
    makes ("made" / "hit" / "makes"), so "3-pointers made per attempt" is
    left alone.

    .. versionadded:: 4.4.0
    """
    stat = slots.get("stat")
    # "mad" too: "show embiid's 3pt attempts and 3pts mad for his career" asks
    # for both, which the made line reports ("585 of 1,727").
    if stat not in _MADE_TO_ATTEMPTED or not _ATTEMPTED.search(question) or re.search(r"\b(?:made|mad|makes?|hit|hits)\b", question, re.IGNORECASE):
        return
    slots["stat"] = _MADE_TO_ATTEMPTED[stat]


# Which shots a distance or a chart is about, from the question's own words.
# The intents that read `shot_value` (templates.shots._shot_value: a
# shot_distance and a shot_chart) used to have the model taught it by their
# own worked examples in ROUTER_PROMPT; with shot_distance assigned from the
# text instead (subject.KIND_ASSIGNED_INTENTS), "avg 3pt shot distance" has
# to read its 3 here - the same discipline `_validate_side` follows, and for
# the same reason: a value read off the question cannot move any other slot.
_SHOT_VALUE_WORDS: tuple[tuple[int, re.Pattern[str]], ...] = (
    (3, re.compile(r"\b(?:3|three)[- ]?(?:pt|pts|point(?:er)?s?)\b|\bthrees\b|\b3s\b", re.IGNORECASE)),
    (2, re.compile(r"\b(?:2|two)[- ]?(?:pt|pts|point(?:er)?s?)\b|\btwos\b", re.IGNORECASE)),
    (1, re.compile(r"\bfree[- ]throws?\b|\bfts?\b", re.IGNORECASE)),
)
_SHOT_VALUE_INTENTS = frozenset({"shot_distance", "shot_chart"})


def _route_shot_value(intent: str, slots: dict[str, Any], question: str) -> None:
    """The shot value a distance or chart question names, where the model
    left the slot empty - never over a value it did fill, and only where
    exactly one value is named ("twos and threes" is neither).

    .. versionadded:: 5.0.0
    """
    if intent not in _SHOT_VALUE_INTENTS or isinstance(slots.get("shot_value"), int):
        return
    named = [value for value, pattern in _SHOT_VALUE_WORDS if pattern.search(question)]
    if len(named) == 1:
        slots["shot_value"] = named[0]


def _route_leaderboard_shot_distance(intent: str, slots: dict[str, Any], question: str) -> None:
    """No leaderboard ranks shot distance - see :data:`_LEADERBOARD_SHOT_DISTANCE`.

    Sets `stat` to the sentinel ``"shot_distance"`` - not a real metric name,
    an explicit string `templates.players.leaderboard` checks for by value
    (the two modules agree on the literal rather than sharing a symbol, the
    same way `_GAME_SCORE_STAT_BY_INTENT`'s spellings are agreed rather than
    imported) - so the template can refuse naming the real cause instead of
    resolving to the nearest real metric.

    Also drops any `player` the router filled, filler or real: `leaderboard`
    never reads one for real (a named player is refused separately), and a
    filler value here ("player": "player" on a question that names nobody)
    would otherwise reach `subject.apply_subject` first and refuse for
    the WRONG cause - "read as a question about player, who the question does
    not mention" - before this refusal, the right one, ever runs.

    .. versionadded:: 4.4.0
    """
    if intent != "leaderboard" or not _LEADERBOARD_SHOT_DISTANCE.search(question):
        return
    slots["stat"] = "shot_distance"
    slots.pop("player", None)


#: The stats that are a yes/no about a game, which `leaderboard` counts per
#: player ("most triple-doubles"). Ranking THOSE GAMES by another measure
#: ("highest scoring triple doubles") is the window's `by`, which the window
#: tagger reads where the stat is one of these (`window.WindowContext.boolean_stat`).
_BOOLEAN_STATS = frozenset({"triple_double", "double_double", "fouled_out"})


# A game log asked for by name. Measured: "luka ft log" routed to player_stat
# and was answered with a season average.
_LOG_WORDS = re.compile(r"\b(?:game\s*logs?|gamelogs?|logs?)\b|\b(?:each|every|by)\s+game\b", re.IGNORECASE)

# The thirty team nicknames, and the shorthand a question uses for some. Only to
# tell a team from a player in a slot the model filled: "zach lavine vs nuggets"
# came back as player_matchup with players ['Zach LaVine', 'Denver Nuggets'].
_TEAM_WORD = re.compile(
    r"\b(?:hawks|celtics|nets|hornets|bulls|cavaliers|cavs|mavericks|mavs|nuggets|pistons|warriors|rockets|pacers|clippers|lakers|"
    r"grizzlies|heat|bucks|timberwolves|wolves|pelicans|knicks|thunder|magic|76ers|sixers|suns|blazers|kings|spurs|raptors|jazz|wizards)\b",
    re.IGNORECASE,
)


# A team named by its city or its abbreviation, which is how a question names
# one when it does not use the nickname: "mathurin v det", "sam hauser v mil",
# "pascal vs orlando". These are matched against the WHOLE name and never as a
# last word, and the distinction is load-bearing rather than fussy: three real
# players are surnamed Cleveland, Houston and Washington, so a last-word rule
# over cities turns PJ Washington and Allan Houston into teams. Measured
# against the warehouse, no player name equals a city or an abbreviation, and
# exactly one player name is a single word at all ("Nene"), which matches none
# of these.
#
# Two-letter forms are left out on purpose. ESPN's own table abbreviates four
# teams "no", "ny", "sa" and "gs", and "no" is an English word; questions use
# the three-letter forms, so those are what is listed.
_TEAM_CITY = frozenset(
    {
        "atlanta", "boston", "brooklyn", "charlotte", "chicago", "cleveland", "dallas", "denver", "detroit",
        "golden state", "houston", "indiana", "los angeles", "memphis", "miami", "milwaukee", "minnesota",
        "new orleans", "new york", "oklahoma city", "orlando", "philadelphia", "phoenix", "portland",
        "sacramento", "san antonio", "toronto", "utah", "washington",
    }
)  # fmt: skip
_TEAM_ABBREVIATION = frozenset(
    {
        "atl", "bkn", "bos", "cha", "chi", "cle", "dal", "den", "det", "gsw", "hou", "ind", "lac", "lal",
        "mem", "mia", "mil", "min", "nop", "nyk", "okc", "orl", "phi", "phx", "por", "sac", "sas", "tor",
        "uta", "wsh", "was",
    }
)  # fmt: skip

# A near spelling is NOT matched here, and that was measured rather than
# assumed. The feed misspells three teams inside a `players` slot - "taptors",
# "warriners", "blakers" - and `difflib` at cutoff 0.8 reaches the right team
# for all three. It also reaches a team for **16 real player surnames**:
# Burks -> Bucks, Hawkins -> Hawks, Thornton -> Toronto, Gooden -> Golden,
# Wheat -> Heat, Houstan -> Houston, and ten more. The model puts bare
# surnames in that slot routinely (["Mathurin", "Detroit"], ["Pascal",
# "Orlando"]), so those collisions are live, and turning a player into a team
# is the same fluent wrong answer in the other direction. Three queries is not
# worth sixteen, and the cutoff cannot separate them - "houstan"/"houston" and
# "taptors"/"raptors" are both one edit in seven characters, ratio 0.857. Same
# conclusion `find_players` reached for player names, for the same reason.


def _is_team_name(name: str) -> bool:
    """Whether a name the model put in ``players`` is a team's.

    Three tests, narrowing as they get looser:

    - **Its LAST word is a nickname.** Checked against the warehouse: all 30
      team names end in one and none of 3,101 player names does, while "Magic
      Johnson" holds one as his first name - and a match anywhere in the name
      took him for a team.
    - **The WHOLE name is a city or an abbreviation** ("det", "Orlando"). Never
      the last word, because three players are surnamed Cleveland, Houston and
      Washington.
    A misspelled team is deliberately NOT matched - see the note above
    ``_TEAM_CITY``, where fuzzy matching was measured and rejected because it
    turns 16 real player surnames into teams.

    .. versionchanged:: 2.2.0
       Recognizes a city and an abbreviation. Before this, a team the question
       named any way but by nickname read as a player, and "mathurin v det"
       was routed as a matchup between two players.
    """
    words = name.lower().split()
    if not words:
        return False
    if _TEAM_WORD.fullmatch(words[-1]) is not None:
        return True
    whole = " ".join(words)
    return whole in _TEAM_CITY or whole in _TEAM_ABBREVIATION


def _team_slot_named_in_text(question: str, candidate: Any) -> str | None:
    """``candidate`` back, but only if the question's own words actually say
    it - the mirror of :func:`_is_team_name`, which asks whether a slot IS a
    team at all rather than whether the question named this one.

    ISSUES.md #170: a quarter question with no `player` slot (see
    :func:`_route_period_intents`) sometimes fills `team`/`opponent` with a
    team the model inferred rather than one the question used - "Jokic ...
    3rd quarter against Boston" filled `opponent` with 'Denver Nuggets', a
    real team and Jokic's own, but a word the question never wrote, while
    `team` held 'Boston Celtics', a word it did. Checked against the text the
    same way :func:`association.query.subject.question_supports`
    checks an invented player name - any one word is enough, since half a
    name is how a question normally carries one.
    """
    if not isinstance(candidate, str):
        return None
    words = re.findall(r"[A-Za-z]{4,}", candidate)
    if not words:
        return None
    low = question.lower()
    if any(re.search(rf"\b{re.escape(word.lower())}\b", low) for word in words):
        return candidate
    return None


# "best record" and "worst record" rank the league; with no team named they are
# team_leaderboard's question. Measured: "worst record 2025-26" came back as
# team_record with team='worst'. "the league" or "NBA" between the two words
# is the same ranking: "Best NBA record since January 31st 201" (yardstick-v2
# F104) came back as team_record with no team and fell through.
_BEST_WORST_RECORD = re.compile(r"\b(?:best|worst)\s+(?:nba\s+|league\s+)?records?\b", re.IGNORECASE)

# A `team` slot that names the league rather than a team - "all-NBA",
# "all_teams", "worst" - measured on three questions, each of which then
# refused as an unknown team.
_PSEUDO_TEAM = re.compile(r"(?:the\s+)?(?:all[-_ ]?nba|nba|league|all[-_ ]?teams?|teams?|every\s+team|worst|best)", re.IGNORECASE)


# A NetPoints rate asked for by any of its names. In this data the only
# adjusted form of NetPoints is the per-100-possessions rate, so "adjusted"
# has exactly one honest reading; the model reaches for the season total
# whatever the question says ("top 10 in defensive netpoints / 100
# possessions" came back as netpoints_defense, and the two lists disagree
# from the second name down - Holmgren is 2nd by season total and outside
# the top three per 100). "/ 90" is a rate nothing here holds, and refuses
# (#152).
_RATE_WORDS = re.compile(r"\badjusted\b|\bper\s+(?:100\s+)?poss?ess?ions?\b|\bper\s+100\b|/\s*100\b", re.IGNORECASE)
_PER_90 = re.compile(r"\bper\s+90\b|/\s*90\b", re.IGNORECASE)
_NETPOINTS_PER_100: dict[str, str] = {
    "netpoints": "netpoints_per_100",
    "netpoints_total": "netpoints_per_100",
    "netpoints_offense": "netpoints_offense_per_100",
    "netpoints_defense": "netpoints_defense_per_100",
}


def _route_rate(intent: str, slots: dict[str, Any], question: str) -> None:
    """A per-possession rate the question asks a ranking for - see _RATE_WORDS.
    Switches a NetPoints metric to its per-100 variant; any other metric, or a
    per-90 rate, gets a ``rate`` slot no reader honors, so the planner
    refuses rather than ranking the wrong unit."""
    if intent != "leaderboard":
        return
    stat = slots.get("stat")
    if isinstance(stat, str) and stat in ("netpoints", "netpoints_per_100", "netpoints_total"):
        # "who are the top 10 in adjusted offensive netpoints" arrived as
        # the total after the 5.0.0 prompt shrink; the side word decides.
        sides = [name for name, pattern in SIDE_WORDS.items() if pattern.search(question)]
        if len(sides) == 1:
            slots["stat"] = f"netpoints_{sides[0]}" + ("_per_100" if stat.endswith("_per_100") else "")
    per_90 = _PER_90.search(question)
    if per_90 is not None:
        slots["rate"] = per_90.group(0).casefold()
        return
    rate = _RATE_WORDS.search(question)
    if rate is None:
        return
    stat = slots.get("stat")
    if isinstance(stat, str) and stat in _NETPOINTS_PER_100:
        slots["stat"] = _NETPOINTS_PER_100[stat]
    elif not (isinstance(stat, str) and stat.endswith("_per_100")):
        slots["rate"] = rate.group(0).casefold()


#: A team's season TOTAL asked for by "how many ... made/scored/have" or
#: "total", with no per-game word beside it - the compiler's unnarrowed team
#: read (compose/team.py), never team_stat's per-game line.
_TEAM_TOTAL = re.compile(r"\bhow\s+many\b.{0,60}\b(?:made|scored|have|has|had|hit|grabbed|dished)\b|\btotal\b", re.IGNORECASE)
_PER_GAME_WORDS = re.compile(r"\bper\s+game\b|\bppg\b|\brpg\b|\bapg\b|\baverages?\b|\bavg\b", re.IGNORECASE)


def _route_team_total(intent: str, slots: dict[str, Any], question: str) -> None:
    """A season total asked of a team's own stat is filed as ``rate: "total"``
    (the schema's own word for it, which NetPoints already uses), which
    ``team_stat``'s words do not state - its reader steps aside - so the
    compiler reads the season's raw total.
    "how many 3 pointers have the magic made so far this season" arrived as
    ``team_stat`` after the 5.0.0 prompt shrink (it was ``leaderboard`` with
    no team before, which the reading restored and the compiler answered)
    and was answered "11.7 per game" - the right stat, the wrong question.

    .. versionadded:: 5.0.0
    """
    if intent != "team_stat" or slots.get("rate") or not _TEAM_TOTAL.search(question) or _PER_GAME_WORDS.search(question):
        return
    slots["rate"] = "total"


def _team_metric_in(question: str) -> str | None:
    """The longest team-metric alias the question names ("defensive rating"),
    or None. The model invents team stats ("usage_pct_defense" for "lowest
    defensive rating"), and the question says which one it meant. An alias
    the question qualifies as given up comes back as asked ("rebounds
    allowed"), for the template to refuse by name - see :data:`_GIVEN_UP`."""
    text = question.casefold()
    for alias in sorted(STAT_ALIASES, key=len, reverse=True):
        found = re.search(r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])", text)
        if found:
            return f"{alias} allowed" if _GIVEN_UP.match(text, found.end()) else alias
    return None


# A team metric's word qualified as the OTHER side's: "rebounds allowed per
# team" is what a team gives up, and reading it as the alias `rebounds`
# answered the teams' own rebounds, best first (yardstick-v2 F101). It is
# named as asked instead, which no team metric is, so the template refuses by
# name rather than ranking a different column. A metric whose own alias holds
# the qualifier ("points allowed") matches whole first, being longer. Not
# "against": "rebounds against the knicks" is the team's own.
_GIVEN_UP = re.compile(r"\s+(?:allowed|given\s+up|conceded)\b")


# The words a question uses when it is actually asking about one stat, as
# opposed to asking who is better. Loose on purpose, and safe because of where
# it is used: see :func:`_named_a_stat`.
_STAT_WORDS = re.compile(
    r"\b(points?|scor\w*|pts?|rebound\w*|boards|reb|assist\w*|passing|dimes|ast|steal\w*|stl|block\w*|blk|"
    r"turnover\w*|giveaways?|fouls?|minutes?|mins?|shoot\w*|shots?|three\w*|3pt|3-point\w*|field goals?|free throws?|"
    r"percentage|efficien\w*|usage|double-doubles?|triple-doubles?|td3s?|ppg|rpg|apg|spg|bpg|fg|ft|3p|ts|efg|plus[ /-]?minus)\b|\+/-",
    re.IGNORECASE,
)


def _named_a_stat(question: str) -> bool:
    """Whether the question asked about a particular stat at all.

    ``stat`` is the one REQUIRED slot in ``ROUTER_SCHEMA``, so the model fills
    it on every question whether or not the question named one: "compare sga
    and embiid" comes back with ``stat='points'`` 12 times out of 12. For
    ``player_compare`` that slot is not a detail - it collapses the whole line
    the template exists to show back to the single average it used to print.

    The same fix as ``_validate_side``, and forgiving in both directions
    *because it is used for one intent only*. A word this misses widens a
    comparison to the full line, which still holds the stat asked about; a word
    it matches too eagerly leaves the behavior exactly as it was. Neither can
    produce a wrong number, which is why the list may be loose here and could
    not be if `leaderboard` read it.

    It is read beyond the comparison now - a quarter's line and its ranking
    (``_route_period_split_slots``, ``_route_period_intents_choose``), a
    team's line, a streak - and there a word it misses DOES move a number:
    "vj edgecombe 2nd half plus minus" lost its stat and answered his
    second-half points ("567 points in the 2nd half"), and "who has the most
    4th quarter plus minus" the league's fourth-quarter scorers (found
    2026-10-09, Phase 3, step 0). Plus-minus is a stat the question names,
    and reads as one; a stat word added here is a number kept.
    """
    return bool(_STAT_WORDS.search(question))


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


def _recover_period_subject(raw: dict[str, Any], question: str, asked: tuple[Period, Claim] | None, named_player: bool, ranks_players: bool) -> str | None:
    """The player a quarter or half question names when the model's reply
    dropped `player` entirely - split out of :func:`_route_period_intents` to
    keep it inside the complexity gate.

    ISSUES.md #170: "How many points did Jokic score in the 3rd quarter
    against Boston?" arrived from the model with NO player at all - it filled
    `team`/`opponent` instead (`team: 'Boston Celtics'`, `opponent: 'Denver
    Nuggets'`, neither one asked for by name), which reads exactly like the
    "team's own half" shape `_route_period_intents` handles next and answered
    Boston's quarter to a question about Jokic. The question's own grammar
    still names him (`_subject_named_in`, the same reader `threshold_count`
    and `single_game_high` use for their own dropped subject), so it gets a
    turn before the team branch does - but only where nothing ranks players
    (that wins over a recovered subject the same way it wins over the
    model's own `player` slot, and has to) and the recovered "subject" is not
    itself a team's name, since "did the 76ers score" fits the identical
    grammar and must stay the team's own question.
    """
    if asked is None or named_player or ranks_players:
        return None
    candidate = _subject_named_in(question)
    if candidate is not None and not _is_team_name(candidate):
        raw["player"] = candidate
        return candidate
    return None


def _route_period_split_slots(raw: dict[str, Any], question: str, subject: str | None) -> None:
    """The slots `period_split` reads once its intent is chosen - split out
    of :func:`_route_period_intents` to keep it inside the complexity gate."""
    # `stat` is the one REQUIRED slot, so the model fills it whether or
    # not the question named one - measured, "duren v nets 1h gameloh"
    # arrived with stat="none" and "scottie barnes stats 2nd half log"
    # with stat="minutes", and the template refused both as asking for
    # a stat it cannot give. Only a stat the question names is kept,
    # the same rule player_compare follows (see _named_a_stat).
    if not _named_a_stat(question):
        raw.pop("stat", None)
    # A log was asked for, not a season average. Measured, 7 of the 11
    # questions this template answered in its first replay said "log",
    # "by game" or "each game" and got a total and an average.
    if _LOG_WORDS.search(question) or (PERIOD_GAMES_WORDS.search(question) and "stat" not in raw):
        raw["per_game"] = True
    if subject is not None:
        # The player came from the text, not from the model's own
        # `player` slot - so its `team`/`opponent` are no more
        # trustworthy than the player it already dropped. Keep one
        # only where the question's own words actually say it (the
        # same "may be fiction" check `subject.read_subject`
        # runs for a name): "Boston Celtics" is a word the Jokic
        # question used, "Denver Nuggets" is a word it never wrote.
        opponent = _team_slot_named_in_text(question, raw.get("team")) or _team_slot_named_in_text(question, raw.get("opponent"))
        raw.pop("team", None)
        raw.pop("opponent", None)
        if opponent is not None:
            raw["opponent"] = opponent


def _route_period_intents(raw: dict[str, Any], question: str) -> None:
    """Fouling out, and a quarter or half: intents code assigns from the question's own words."""
    low = question.lower()
    if lexicon.FOULED_OUT.search(low):
        # The count of a player's foul-outs: the line itself (fouls at six)
        # is the lines tagger's (``line.read_lines``).
        raw["intent"] = "threshold_count"
        raw["stat"] = "fouls"
    ranks_players = PERIOD_LEADERS.search(low) is not None or (PERIOD_TOP.search(low) is not None and not _team_slot_or_word(raw, low))
    if (QUARTER_WORDS.search(low) and (ranks_players or not _is_team_quarter_points(raw) or _names_a_period_subject(question))) or HALF_WORDS.search(low):
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
        if asked is not None:
            _route_period_intents_player_beside_team(raw, question)
        # No second `_is_team_quarter_points` check: it means "this intent, and
        # NO player", so it can never be true here where a player is named. The
        # team's own quarter is already exempted by the outer condition.
        named_player = isinstance(raw.get("player"), str) and raw["player"].strip()
        subject = _recover_period_subject(raw, question, asked, named_player, ranks_players)
        _route_period_intents_choose(raw, question, asked, bool(named_player) or subject is not None, ranks_players, subject)


def _route_period_intents_choose(raw: dict[str, Any], question: str, asked: tuple[Period, Claim] | None, named_player: bool, ranks_players: bool, subject: str | None) -> None:
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
        if not _named_a_stat(question):
            raw.pop("stat", None)
    elif asked is not None and not named_player and (_team_slot_or_word(raw, low)):
        # A TEAM's half. A team's QUARTER never reaches here - the
        # exemption above keeps it on its own template - but a half always
        # does, because the model maps "first half" onto period 1 and that
        # is wrong for a team the same way it is for a player. The
        # linescore holds both quarters, so the template sums them. The
        # team is the model's `team` slot, or - measured, the model filed
        # none for "Celtics 2nd half scoring this season" (the one
        # check_routing.py gap after step 3) and "least points scored by
        # the wizards in the first half" (yardstick-v2 F064) - the one
        # nickname the question itself holds, which _TEAM_WORD reads.
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
    elif asked is None and not named_player and BY_QUARTER.search(low) and not _team_slot_or_word(raw, low):
        # Every player's four quarters side by side - the league's; a
        # team's players' breakdown ("knicks points by quarter") reads
        # as the TEAM's by quarter, which is not built, and stays `other`.
        raw["intent"] = "period_leaderboard"
        if not _named_a_stat(question):
            raw.pop("stat", None)
    else:
        raw["intent"] = "other"


def _route_period_intents_player_beside_team(raw: dict[str, Any], question: str) -> None:
    """A player and a team in ``players``, on a "vs" question, are the
    player and his opponent.

    yardstick-v2 F059: "Kd vs clippers 2h at home gamelog" arrived with
    ``players: ['Kevin Durant', 'Los Angeles Clippers']``, no ``player`` and
    ``team: 'clippers'``, so :func:`_route_period_intents` saw no named
    player and took the team's-half branch - team_quarter_points, which
    refused for naming a player. A named player's half is ``period_split``;
    the team beside him in the list is who he played. Only an exact pair
    (one player, one team, per :func:`_is_team_name`) on a question that
    says "vs"/"against", and only where the model filed no ``player`` - so a
    real two-player list, or a team with no versus word, is left alone. A
    ``team`` slot naming that same opponent goes too: it is the opponent
    filed twice, not the player's own team."""
    if isinstance(raw.get("player"), str) and raw["player"].strip():
        return
    listed = raw.get("players")
    if not isinstance(listed, list) or len(listed) != 2 or not all(isinstance(name, str) and name.strip() for name in listed) or not _VERSUS_WORDS.search(question):
        return
    teams = [name for name in listed if _is_team_name(name)]
    if len(teams) != 1:
        return
    opponent = teams[0]
    raw["player"] = next(name for name in listed if name != opponent)
    raw.pop("players", None)
    team_slot = raw.get("team")
    if isinstance(team_slot, str) and set(team_slot.lower().split()) & set(opponent.lower().split()):
        raw.pop("team", None)
    if not (isinstance(raw.get("opponent"), str) and raw["opponent"].strip()):
        raw["opponent"] = opponent


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
    raw["stat"] = "triple_double"
    raw.pop("shot_value", None)
    # `shot_chart` too, since the 5.0.0 prompt shrink: the "3" reads as a
    # shot to the model ("luka td3s home" arrived as a chart of his twos),
    # and a count of triple-doubles is never a chart unless the question
    # asks for one to be drawn.
    if raw["intent"] in ("other", "shot_chart") and isinstance(raw.get("player"), str) and raw["player"].strip() and not _DRAW_WORDS.search(question):
        raw["intent"] = "player_stat"


def _team_slot_or_word(raw: dict[str, Any], low: str) -> bool:
    """Whether a team is named - by the model's ``team`` slot, or failing
    that by exactly one nickname in the question, which is then filed as the
    slot. Two nicknames name a matchup, not a subject, and file nothing."""
    if isinstance(raw.get("team"), str) and raw["team"].strip():
        return True
    nicknames = _TEAM_WORD.findall(low)
    if len(set(nicknames)) != 1:
        return False
    raw["team"] = nicknames[0]
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
    listed = [name for name in raw.get("players") or [] if isinstance(name, str)]
    if raw["intent"] == "player_compare" and sum(map(_is_team_name, listed)) == 1 and len(listed) == 2:
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
    """A comparison of one player, a line that is a log, and a line with no
    player that ranks the league - split out of
    :func:`_route_team_and_player_intents` for the complexity gate."""
    if raw["intent"] == "player_compare" and len(listed) == 1 and not raw.get("player"):
        # One player "compared" with nobody is his own line (or log):
        # "alperen sengun double-doubles vs southeast division career away"
        # arrived so after the 5.0.0 prompt shrink, and player_compare needs
        # two. The name moves to the slot the line reads.
        raw["intent"] = "game_log" if _LOG_WORDS.search(question) or GAMES_WORDS.search(question) else "player_stat"
        raw["player"] = listed[0]
        raw.pop("players", None)
    if raw["intent"] == "player_stat" and _LOG_WORDS.search(question):
        raw["intent"] = "game_log"
    _route_pair_over_seasons(raw, question, listed)
    if raw["intent"] == "game_log" and _HOW_MANY.search(question) and raw.get("stat") in _GAMES_STATS and not any(p.search(question) for p in ORDER_WORDS.values()):
        # "how many games did embid play" arrived as a log of his most
        # recent game (order recent, limit 1) after the 5.0.0 prompt shrink;
        # the count is the line's ("... in 38 games"), which player_stat
        # states, and a log of one game states nothing of the kind.
        raw["intent"] = "player_stat"
        for key in ("stat", "fields"):
            raw.pop(key, None)
    if raw["intent"] == "player_stat" and not _named_player(raw) and WHO_RANKS.search(question):
        # No player named and "who ... the most": the league's ranking, not
        # one player's line - "who attempted the most three pointers this
        # season?" arrived as player_stat after the 5.0.0 prompt shrink.
        raw["intent"] = "leaderboard"


_GAMES_STATS = frozenset({"games", "game", "games_played", "gamesPlayed", "gp"})
_COMPARE_WORDS = re.compile(r"\bcompar(?:e[ds]?|ing|ison)\b|\bbetter\b|\bwho scores more\b|\bside by side\b", re.IGNORECASE)


def _named_player(raw: dict[str, Any]) -> bool:
    """Whether the model filed a player, in either slot shape."""
    return bool((isinstance(raw.get("player"), str) and raw["player"].strip()) or raw.get("players"))


def _route_matchup_against_team(raw: dict[str, Any], question: str, listed: list[str]) -> None:
    """A ``player_matchup`` whose second "player" is a team."""
    if raw["intent"] == "player_matchup" and any(map(_is_team_name, listed)):
        # One of the "two players" is a team: this is a player's games against
        # it. subject.apply_subject moves the team to `opponent`.
        raw["intent"] = "game_log" if _LOG_WORDS.search(question) or GAMES_WORDS.search(question) else "player_stat"
    if raw["intent"] == "player_matchup" and len(listed) < 2 and isinstance(raw.get("player"), str):
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
        against = raw.get("opponent") or next(iter(raw.get("teams") or []), None)
        if isinstance(against, str) and _is_team_name(against):
            raw["intent"] = "game_log" if _LOG_WORDS.search(question) or GAMES_WORDS.search(question) else "player_stat"


_PLAYED_TOGETHER_REROUTABLE = frozenset({"head_to_head", "team_record", "team_stat", "game_log", "other"})


def _route_line_and_record_intents(raw: dict[str, Any], question: str, companions: tuple[Companion, ...]) -> bool:
    """A history that is really a line, a record ranking, and a career high.
    Returns whether a history was rerouted to a line."""
    rerouted_to_line = False
    if raw["intent"] == "player_history" and (not _named_a_stat(question) or (_VERSUS_WORDS.search(question) and _TEAM_WORD.search(question))):
        # A season-by-season history of one stat is neither "career averages"
        # (no stat named - the whole line) nor a career against one team.
        # Measured: "Jokic career averages" answered with points by season,
        # "derozan career points vs knicks" refused on its opponent.
        raw["intent"] = "player_stat"
        rerouted_to_line = True
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
    if raw["intent"] == "team_record" and _BEST_WORST_RECORD.search(question) and not _TEAM_WORD.search(question):
        raw["intent"] = "team_leaderboard"
        raw["stat"] = "record"
        raw.pop("team", None)
    if raw["intent"] == "player_stat" and lexicon.CAREER_HIGH.search(question):
        # A career high is one game's total, which player_stat never reports.
        # Measured: "Diabate career high assists" was answered with his assists
        # per game.
        raw["intent"] = "single_game_high"
    return rerouted_to_line


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


def _route_count_intents(raw: dict[str, Any], slots: dict[str, Any], question: str, companions: tuple[Companion, ...]) -> None:
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
        slots.pop("stat", None)
        # The count's subject, restored as a count's would be
        # (_route_subject_slots) - game_log is not one of the intents that
        # step restores for, and the model dropped Bam here.
        subject = None if slots.get("player") or slots.get("players") else _subject_named_in(question)
        if subject is not None:
            slots["player"] = subject
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


def _route_intent_slots(intent: str, slots: dict[str, Any], question: str) -> None:
    """Slots only one template reads. (The with/without split's teammates
    were this stage's until Phase 3, step 2: the subject reading writes
    every companion, and the split reads the ones who played.)"""
    # Intent-specific: each means nothing to any other template, so each is
    # only added where one reads it - the same rule `side` follows below.
    if intent in ("team_leaderboard", "team_quarter_points"):
        # ISSUES.md #172: "nba team with least playoff wins since 2022" filed
        # the same word twice - correctly into the ranking's end (the window
        # tagger's `rank`), and again into `team`, where no franchise is
        # named "least" and the template refused the whole question ("no
        # team matching 'least'") over a cause the question never gave. The
        # same shape as `subject.apply_subject`: a slot the question does
        # not support. Read against the lexicon's `RANK_WORDS` - the tagger's
        # own table - rather than a new word list, so the two checks cannot
        # drift apart (AGENTS.md, "one concept, one definition").
        team_slot = slots.get("team")
        if isinstance(team_slot, str) and any(pattern.fullmatch(team_slot.strip()) for _, pattern in RANK_WORDS):
            slots.pop("team", None)
    if intent == "streak":
        slots["kind"] = "loss" if _LOSING_STREAK.search(question) else "win"


def _route_line_stat(intent: str, slots: dict[str, Any], question: str, rerouted_to_line: bool) -> None:
    """A required ``stat`` the question never named, a history's season count, and an advanced metric the question did name."""
    # For the two templates whose default is a whole line, which still holds
    # any stat the word list missed. Dropping it for `leaderboard` would leave
    # it with no metric to rank by. Measured on player_stat: "Jokic career
    # averages" arrived with stat='points' and "LeBron James career playoff
    # stats" with stat='career_playoffs'.
    if intent in ("player_compare", "player_stat") and not _named_a_stat(question):
        slots.pop("stat", None)
    if rerouted_to_line:
        # A history's `fields` were its own; the line it became has none
        # (its count of seasons the window tagger never reads for a line).
        slots.pop("fields", None)
    if intent in _ADVANCED_STAT_INTENTS:
        advanced = next((metric for metric, pattern in _ADVANCED_STAT_WORDS if pattern.search(question)), None)
        if advanced is not None:
            slots["stat"] = advanced


def _route_team_slots(intent: str, slots: dict[str, Any], question: str) -> None:
    """A pseudo-team dropped, and a team's or a streak's ``stat`` kept only where the question names one."""
    team_slot = slots.get("team")
    if isinstance(team_slot, str) and _PSEUDO_TEAM.fullmatch(team_slot.strip()):
        slots.pop("team", None)
    # The same drop for two more intents where the required slot is noise when
    # the question names no stat: "Knicks stats" arrived as stat='points' and
    # narrowed a team's line to one number, and a team's winning streak arrived
    # with a stat and no threshold and was refused.
    if intent == "team_stat" and not (_named_a_stat(question) or _TEAM_STAT_WORDS.search(question)):
        slots.pop("stat", None)
    if intent in ("team_stat", "team_leaderboard"):
        named_metric = _team_metric_in(question)
        if named_metric is not None:
            slots["stat"] = named_metric
    if intent == "streak" and not _named_a_stat(question):
        slots.pop("stat", None)


# A count asked of one player with no season in sight: "how many times has
# embiid fouled out?" is 0 this season and 9 in his career, and only the second
# is the question. Product decision (2026-09-19): an unscoped count by a named
# player reads as his career, and the answer names the scope it used.
_HOW_MANY = re.compile(r"\bhow\s+many\b", re.IGNORECASE)


def _route_games_won(intent: str, slots: dict[str, Any], question: str, read: LinesRead) -> None:
    """A player's games won or lost - "how many playoff games has embiid
    won?" - is his team's record in the games he played, with no line at
    all: the compiler's read once the record's reader steps aside, and it
    reads "wins"/"losses", not the model's own word for it ("playoff_wins",
    day5). Where the words name a line the record is over it, and the
    stat the lines tagger read beside it stands (``read.stat``: "scores 30"
    is points; one "N+ stat" pair under a count or a record is that word's
    column - until Phase 3, step 2 ``_route_record_when_threshold`` wrote
    both off the words here: "what was the sixers record this season when
    tyrese maxey had 20+ points?" came back with ``stat='wins'``, the word
    "record" filed under the required slot, and the template refused with
    "record_when needs a known stat and a positive threshold, got
    'wins'/20" about a question that states its stat plainly).
    """
    won = lexicon.GAMES_WON.search(question)
    if intent == "record_when" and won is not None and not any(line.keyed for line in read.lines):
        slots["stat"] = "losses" if won.group(1).lower().startswith("los") else "wins"
        return
    if read.stat is not None:
        slots["stat"] = read.stat


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


def _route_subject_slots(intent: str, slots: dict[str, Any], question: str) -> None:
    """A single-game high's or a threshold count's missing subject. (A
    log's last meetings with an opponent across seasons, and a count's
    career, are the span tagger's: :func:`~association.query.span.read_span`.)"""
    if intent in _SUBJECT_RESTORED_INTENTS and not slots.get("player") and not slots.get("players"):
        # An optional slot the model dropped, restored from the question's own
        # grammar - see _subject_named_in. Only where the template reads one
        # player: a leaderboard with no player IS the league's ranking, and so
        # is a threshold_count with no player - restoring the subject only
        # where the question's own grammar names one (#138) never turns a
        # genuine league question into one about somebody it only appears to
        # name.
        subject = _subject_named_in(question)
        if subject is not None:
            slots["player"] = subject


def _route_side(intent: str, slots: dict[str, Any], question: str) -> None:
    """The side of the ball for a fingerprint. Until Phase 3, step 2 this
    stage (``_route_side_and_order``) wrote the window beside it; the
    window tagger reads it now (:func:`~association.query.window.read_window`)."""
    if intent == "fingerprint":
        side = _validate_side(slots, question)
        if side is None:
            slots.pop("side", None)
        else:
            slots["side"] = side


#: The slot keys a raw route carries into the stages: the router model's
#: schema properties less the intent, which is the shape the parser writes
#: its names, stat and window in now that the model classification is gone
#: (5.0.0). What :func:`settle` keeps of a settled route before running the
#: stages again, since every other key is one the stages themselves read off
#: the question for the intent they were run under (``since`` for a
#: ``game_log``), and would
#: otherwise survive into an intent whose template refuses it. The span's
#: six slots, the window's four, the cuts' seven and the period's two read
#: from the words are not among them: the four taggers read every one from
#: the words again (Phase 3, step 2); the ``opponent`` is, since it is the
#: subject reading's word, which the cuts tagger takes as settled.
_MODEL_SLOTS: frozenset[str] = frozenset({"stat", "player", "players", "team", "teams", "opponent", "rate", "side", "shot_value", "fields"})


def settle(intent: str, slots: Mapping[str, Any] | Scope, question: str, companions: tuple[Companion, ...] = (), *, lines: tuple[Line, ...] = ()) -> Route:
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
    ``slots`` may be a settled route's, so the keys the stages derive are
    dropped first (:data:`_MODEL_SLOTS`). The stages may settle on a DIFFERENT intent than
    asked - a count with no threshold is a ranking, a "when X and Y played"
    record is ``with_without`` - and the caller reads the returned intent
    rather than assuming its own.

    ``slots`` is the model's slot dict - the names and the stat - or a settled route's typed
    :class:`~association.query.reading.Scope`, run again under a child; the
    stages' own working dict never leaves this module, and the Route they
    return carries the Scope.

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
    """
    if isinstance(slots, Scope):
        slots = slots.to_slots()
    raw: dict[str, Any] = {key: value for key, value in slots.items() if key in _MODEL_SLOTS}
    raw["intent"] = intent
    return _settle(raw, question, companions, lines=lines)


def _settle(raw: dict[str, Any], question: str, companions: tuple[Companion, ...] = (), *, lines: tuple[Line, ...] = ()) -> Route:
    """The stages, over a raw route or a reassigned one (:func:`settle`)."""
    # A coach question is refused whatever the model said, and carries no
    # slots, so it short-circuits before any of the stages below run.
    if _route_coach_intent(raw, question):
        return Route(intent=raw["intent"])
    # The stages run in this order because each reads what the ones before it
    # rewrote: the intents code assigns decide which slots are read, and a
    # count whose words carry no line turns back into a ranking before any
    # intent-specific slot is chosen.
    _route_period_intents(raw, question)
    _route_triple_double_abbreviation(raw, question)
    _route_team_and_player_intents(raw, question)
    rerouted_to_line = _route_line_and_record_intents(raw, question, companions)
    slots = _route_blank_slots(raw)
    _route_count_intents(raw, slots, question, companions)
    _route_split_slot(slots, question)
    _route_intent_slots(raw["intent"], slots, question)
    _route_line_stat(raw["intent"], slots, question, rerouted_to_line)
    _route_game_score(raw["intent"], slots, question)
    _route_two_point_pct(raw["intent"], slots, question)
    _route_leaderboard_shot_distance(raw["intent"], slots, question)
    _route_shot_value(raw["intent"], slots, question)
    _route_attempted_stat(slots, question)
    _route_team_slots(raw["intent"], slots, question)
    _route_rate(raw["intent"], slots, question)
    _route_team_total(raw["intent"], slots, question)
    _route_subject_slots(raw["intent"], slots, question)
    # The lines, at the position of the last stage that wrote one
    # (``_route_record_when_threshold``, the pair's stat and number), over
    # the settled intent; the stat the words name beside a line is written
    # where that stage wrote it.
    read = read_lines(question, LineContext(intent=raw["intent"]))
    slots["lines"] = (*lines, *read.lines)
    _route_games_won(raw["intent"], slots, question, read)
    _route_side(raw["intent"], slots, question)
    # The period, the cuts, the window, then the span, last: each the one
    # reader of its family, over the intent the stages settled - the
    # period once the intent is final (the stage that chose it wrote the
    # value beside its choice, and the parser again for a team's quarter,
    # until Phase 3, step 2), the cuts at the position of the last stage
    # that wrote one (an opponent that was the without list again,
    # dropped), the span over the window and the cuts, since its rules
    # read both.
    period = read_period(question, PeriodContext(intent=raw["intent"]))
    slots["period"] = period.period
    cuts = read_cuts(question, CutsContext(intent=raw["intent"], split=slots.get("split"), opponent=slots.get("opponent"), without=_absent(companions)))
    slots.pop("opponent", None)
    slots["cuts"] = cuts.cuts
    window = read_window(question, WindowContext(intent=raw["intent"], boolean_stat=slots.get("stat") in _BOOLEAN_STATS))
    slots["window"] = window.window
    span = read_span(question, _span_context(raw["intent"], slots, question, window=window.window, cuts=cuts))
    slots["span"] = span.span
    # The stages' working dict crosses into the typed Scope here, once: a
    # value no field holds raises ScopeError to the parser.
    return Route(intent=raw["intent"], scope=Scope.from_slots(slots), claims=claimed_once([*period.claims, *cuts.claims, *window.claims, *read.claims, *span.claims]))


def _span_context(intent: str, slots: dict[str, Any], question: str, *, window: Window, cuts: CutsRead) -> SpanContext:
    """What the span tagger reads beside the words
    (:class:`~association.query.span.SpanContext`): the stages' settled
    intent, the typed window, the typed cuts (a date, a game of a series,
    an ordinal season, and the year a range opened on a dated day starts
    the seasons at), and the guard words of other families it keeps until
    their slices move them to the lexicon."""
    return SpanContext(
        intent=intent,
        player_named=bool(slots.get("player")),
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
