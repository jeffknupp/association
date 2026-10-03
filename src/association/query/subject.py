"""Who a question is about, read once from its own words.

A question's subject - a player, two players, a team, two teams, a position
group, or everyone - is today inferred in four places: the router's slots,
``entities.scope_from_question`` (gone with 5.0.0) and its siblings in
``agent._try_fast_path``, the compiler's ``move_point``, and the refusals.
Each repairs what the one before it got wrong, and each was written for the
question that exposed it (AGENTS.md, "The router invents names"). This module
is one reading of the subject instead, made from the question's own spans
BEFORE any template runs, so that every name a template reads is a span of
the question and the router's slots are a hint, never a source.

Measured before it was written (``~/association-research/subject-kinds/``,
2026-09-25): over 290 recorded questions the reading agrees with what the
repair chain hands the template on 279; of the eleven left, four are the
chain answering a different subject (a compare whose second player the
router filed as the opponent, two position-group logs read as a team's,
yardstick-v2 F114), six are the chain's slot encodings of the same subject,
one a kind neither side had. The rules below are the ones that measurement
needed, each named for the question that needed it.

:func:`read_subject` is pure, takes the routed slots, and returns a
:class:`Subject` with its evidence; :func:`apply_subject` writes the fields
it has taken over from the chain into the slots the templates read - the
players and a player filed as the opponent, so far - each write a recorded
:class:`~association.query.decisions.Decision`. The rest of the chain still
writes the other fields; each step goes as the reading takes its field over,
proven by the golden (the roadmap's plan item 1).

.. versionadded:: 4.4.0
"""

from __future__ import annotations

import functools
import gzip
import re
from dataclasses import dataclass, replace
from importlib import resources
from typing import NamedTuple

import duckdb
from rapidfuzz.distance import DamerauLevenshtein

from association.query.decisions import Decision
from association.query.entities import (
    _AGAINST,
    PLAYER_NICKNAMES,
    Entity,
    _edit_budget,
    _exact_name_span,
    _initials,
    _named_only_by_a_team_word,
    _question_derived_player,
    _suggest_players_by_spelling,
    _team_after_for,
    _team_after_versus,
    _team_grounded,
    _team_named,
    _words,
    find_players,
    find_teams,
    nicknames_in,
    players_named_in,
    team_abbreviations,
    team_named_in,
)
from association.query.reading import FILLER_PLAYER_WORDS, OWN_TEAM_RESTORABLE_INTENTS, PLAYER_REQUIRED_INTENTS, POSITIONS, SUBJECT_RESTORABLE_INTENTS, ConditionSpec, Scope
from association.query.router import _ABSENCE_WORDS, _NAME_STOPWORDS, _THRESHOLD_WORDS, Beside, _threshold_from_text_scored
from association.query.season_text import season_from_text

#: The kinds a subject can be. ``team_players`` is "a Hawks player" - the
#: team's players as a group, which the compiler's team-where-a-player-
#: belongs read answers ("thunder all-time triple doubles", by player).
SUBJECT_KINDS: frozenset[str] = frozenset({"player", "pair", "team", "teams", "position", "everyone", "team_players"})
"""Every value :attr:`Subject.kind` takes.

.. versionadded:: 4.4.0
"""

#: The intents a "<team> when <player> reaches N" question arrives under
#: (the router's own `_WHEN_REACHES_REROUTABLE`, plus the splits the model
#: files it as) - each would answer the player's or the team's own line
#: instead of the team's record under the condition.
_RECORD_WHEN_PARENTS: frozenset[str] = frozenset({"player_stat", "player_splits", "game_log", "team_stat", "team_record", "with_without", "other"})

#: Why a team's question with a companion's line is ``record_when``
#: (:func:`child_named`), wherever that is said: the reading's own
#: decision, and the parser's about the intent.
_TEAM_RECORD_WHEN = "a team's record in the games a player named beside it reached a line"

# A line at or above a number, however it is written: "30+", "36-plus",
# "30 or more", "at least 2" (the paraphrases' spellings, parser-greenfield).
_N_PLUS = r"(?:\d{1,3}[\s-]*(?:\+|plus\b|or more\b)|\bat least \d{1,3})"
_N_SEASONS = r"(?:\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten)\s+(?:seasons?|years?)"
_NOT_A_TEAM: frozenset[str] = SUBJECT_KINDS - {"team", "teams", "team_players"}
_PLAYER_OR_PAIR: frozenset[str] = frozenset({"player", "pair"})
_PLAYER_RELATION_PARENTS: frozenset[str] = frozenset({"game_log", "player_stat", "leaderboard", "other"})

#: The child intents the question's own words assign, gated on the subject's
#: kind - in precedence order, first match wins: (child, the words that name
#: it, the kinds it can be about, the router intents it is assigned under).
#:
#: Each child is a fixed point on a relation whose parent (``game_log``,
#: ``player_stat``, ``leaderboard``, ``team_record``) the router still
#: routes to; the words that tell the child from the parent are the ones
#: below, and the subject's kind is what keeps a word grammar off the other
#: relation's questions - "how many times did the 76ers play boston" is two
#: TEAMS meeting, not a count of a player's games; "who lead the league in
#: avg 3 point distance" names no PLAYER to measure. Measured over 352
#: recorded (question, intent) pairs (``~/association-research/intent-shrink/``,
#: 2026-09-25): with the gate, no question of another intent moves. The
#: precedence settles the two that overlap: "career most points in a game"
#: is a single-game high before it is a count, and "how many 20+ point games
#: ... in the past two seasons" a count before it is a history.
_CHILD_GRAMMARS: tuple[tuple[str, re.Pattern[str], frozenset[str], frozenset[str]], ...] = (
    # "per game" is an average, never one game: "the highest points per game
    # average" is a season ranking. "single game" is one game with an article
    # or without: "this season's single game with the most assists" and "most
    # 3 pointers made in single game 24-25" answered the season's leaders
    # (ISSUES.md #260) - "single games", a plural, is not one.
    (
        "single_game_high",
        re.compile(r"\bin (?:a|one) (?:single )?(?:game|match|contest|outing)\b|\bsingle[- ]game\b|\bcareer[- ]high\b|\bhighest\b.{0,60}(?<!per )\bgame\b", re.IGNORECASE),
        _NOT_A_TEAM,
        _PLAYER_RELATION_PARENTS,
    ),
    ("shot_distance", re.compile(r"\bhow far\b|\bdistance\b", re.IGNORECASE), _PLAYER_OR_PAIR, _PLAYER_RELATION_PARENTS | {"shot_chart"}),
    (
        "streak",
        re.compile(r"\bstreaks?\b|\bwin ?streak\b|\bstraight (?:games|wins|losses)\b|\bin a row\b|\bconsecutive\b", re.IGNORECASE),
        SUBJECT_KINDS,
        frozenset({"team_record", "team_stat", "team_leaderboard", "team_outlook", "head_to_head"}) | _PLAYER_RELATION_PARENTS,
    ),
    (
        "record_when",
        re.compile(rf"\brecord\b.*\b(?:when|with)\b.*{_N_PLUS}|\brecord\b.*{_N_PLUS}|\brecord\b.*\b(?:when|with)\b.*\b(?:scored|scores|had|has)\b.*\d+|{_N_PLUS}\s*\w*.*\brecord\b", re.IGNORECASE),
        SUBJECT_KINDS,
        frozenset({"team_record", "team_stat", "with_without", "head_to_head"}) | _PLAYER_RELATION_PARENTS,
    ),
    # A player's games won or lost: his team's record in the games he
    # played, which is record_when's read with no threshold ("how many
    # playoff games has embiid won?" - answered right today only because
    # the router misfiles it there). A player only: a TEAM's games won are
    # team_record's own question.
    (
        "record_when",
        re.compile(r"\bhow many\b.{0,40}\bgames\b.{0,20}\b(?:won|lost|win|lose)\b", re.IGNORECASE),
        frozenset({"player"}),
        frozenset({"team_record", "team_stat", "team_outlook", "with_without", "head_to_head"}) | _PLAYER_RELATION_PARENTS,
    ),
    (
        "threshold_count",
        re.compile(
            rf"\b(?:how many|most|fewest)\b.*\b(?:games?|times)\b.*{_N_PLUS}|\b(?:how many|most|fewest)\b.*{_N_PLUS}.*\bgames?\b|\bhow many (?:times|occasions)\b"
            r"|\bgames? (?:with|where|in which)\b.*\b\d+\s+\w+"
            r"|\b\d{1,3}[\s-]*(?:pts?|points?|rebs?|rebounds?|asts?|assists?|steals?|blocks?|threes|3s)[\s-]+games?\b"
            # The paraphrases' shapes (parser-greenfield, step b): "which games had 15 or more assists", "the highest number of
            # 30+ point games", "how many games did he score 30 points or more in".
            rf"|\b(?:which|what) games?\b.*{_N_PLUS}|\bnumber of\b.*{_N_PLUS}.*\bgames?\b|\bhow many\b.*\bgames?\b.*\b\d{{1,3}}\s+\w+\s+or more\b",
            re.IGNORECASE,
        ),
        _NOT_A_TEAM,
        _PLAYER_RELATION_PARENTS,
    ),
    (
        "player_history",
        re.compile(
            rf"\b(?:over|for|in|during) the (?:past|last) {_N_SEASONS}\b|\b(?:last|past) {_N_SEASONS}\b|\bby (?:season|year)\b|\b(?:each|every) (?:season|year)\b"
            r"|\bseason[- ](?:by|over)[- ]season\b|\byear[- ](?:by|over)[- ]year\b|\bfrom (?:year|season) to (?:year|season)\b",
            re.IGNORECASE,
        ),
        _PLAYER_OR_PAIR,
        _PLAYER_RELATION_PARENTS,
    ),
    (
        "player_splits",
        re.compile(r"\bsplits?\b|\bby month\b|\bhome and away\b|\bhome/away\b|\bhome vs\.? away\b|\bmonthly\b", re.IGNORECASE),
        _PLAYER_OR_PAIR,
        # head_to_head and team_record too: "show me Embiid's splits against
        # boston" arrived as the two teams meeting, Embiid dropped (day5).
        frozenset({"with_without", "player_compare", "head_to_head", "team_record"}) | _PLAYER_RELATION_PARENTS,
    ),
)

KIND_ASSIGNED_INTENTS: frozenset[str] = frozenset(child for child, _, _, _ in _CHILD_GRAMMARS)
"""The intents :func:`read_subject` assigns from the question's words, gated
on the subject's kind (:data:`_CHILD_GRAMMARS`), under a parent intent the
router chose - the same route ``router.CODE_ASSIGNED_INTENTS`` takes for
``coach`` and ``period_split``, one step later, where the subject's kind is
known. Their slots come from :func:`association.query.router.settle`, run
under the child: the router's own text readers recover the threshold, the
seasons count, a streak's kind and a split.

.. versionadded:: 5.0.0
"""


class Applied(NamedTuple):
    """What :func:`apply_subject` did: the typed scope with the reading
    written into it, the decisions recorded, the router's names the question
    never held that nothing could replace (the caller refuses by name), and
    the intent the subject's shape settled on - the router's own where the
    shape fits it.

    .. versionadded:: 5.0.0
    """

    scope: Scope
    decisions: list[Decision]
    dropped: list[str]
    intent: str


@dataclass(frozen=True)
class Subject:
    """Who a question is about.

    ``kind`` is one of :data:`SUBJECT_KINDS`. ``players`` and ``teams`` are
    the names as the question or the router gave them - resolution to an
    entity stays with :mod:`association.query.entities` - except that a
    router name the question's own span spells differently carries the
    question's spelling (a bare "Jokic" completed, "Jaylen Huff" cut back to
    the "Jay Huff" typed; see :func:`_spellings`). ``opponent`` is a
    team the subject is set against - the question's own "vs the Pistons",
    or the router's ``opponent`` where it names a team the question holds
    (:func:`_read_opponent`); ``own_team`` the
    player's own ("for the Heat"); ``companions`` players named beside the
    subject with a role the question states ("without KD", "when Maxey
    scored 20+"). ``evidence`` says, per line, what the reading rested on.

    .. versionadded:: 4.4.0

    .. versionchanged:: 5.0.0
       ``named`` is gone: ``players`` itself carries the question's own
       spelling of each router name, resolved from the span the router's
       name anchors rather than from a whole-word match - which read "kareem
       stats vs bob lanier" as Kareem Rush. ``invented``,
       ``named_season``, ``intent`` and ``intent_reason`` added. ``opponent``
       also reads the router's slot where the question supports it;
       ``own_team`` needs the player named before the "for <team>" phrase.
    """

    kind: str
    players: tuple[str, ...] = ()
    teams: tuple[str, ...] = ()
    position: str | None = None
    opponent: str | None = None
    own_team: str | None = None
    companions: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    #: The router's player names - from ``player``, ``players`` and a player
    #: filed as the ``opponent`` - that the question never held and that are
    #: not teams: fiction, as far as the question can tell ("Jusuf Nurkic" on
    #: "compare sga and embiid"). :func:`apply_subject` replaces or reports
    #: each, whatever kind the subject is.
    invented: tuple[str, ...] = ()
    #: The season the question ITSELF names, if one - what the tenure rule
    #: reads ("for Miami" with no season is a career, not this season), and
    #: never the router's own "current season" default.
    named_season: int | None = None
    #: The intent the subject's shape settles: a team's record in the games
    #: a companion reached a line (``record_when``), or a child the
    #: question's own words name for this kind of subject
    #: (:data:`KIND_ASSIGNED_INTENTS`); the route's own intent everywhere
    #: else.
    intent: str = ""
    #: Why ``intent`` is not the route's, in a sentence - the words that name
    #: a child ("the words 'in a single game' name single_game_high"), or a
    #: team's record in the games a companion reached a line - and ``None``
    #: where it is the route's own. What the parser's decision about the
    #: intent says (:func:`~association.query.parse.read_route`).
    intent_reason: str | None = None
    #: The question this is a reading of - what :func:`apply_subject` settles
    #: a kind-assigned intent's slots from (:func:`~association.query.router.settle`).
    question: str = ""
    #: Every companion with the role the question states (:class:`Companion`);
    #: ``companions`` above is their names.
    conditions: tuple[Companion, ...] = ()
    #: The router's player names that are no name at all - a rank word or
    #: a phrase of the question ("most", "most 30+ point games";
    #: :func:`_not_a_name`), or a name the question holds only by a TEAM's
    #: word ("magic vs nets" is not Magic Johnson;
    #: :func:`~association.query.entities._named_only_by_a_team_word`) -
    #: which :func:`apply_subject` takes out of the slots rather than leaves
    #: for a template to resolve ("No player found matching 'most'").
    filler: tuple[str, ...] = ()


_MONTH_ABBREVIATIONS = frozenset({"jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec"})

#: A companion's role, from the question's own words: what follows
#: "without" / "with" / "when" / "while", up to the next scoping word.
#: Loose on purpose - the names it holds are still checked against the
#: players the question names.
_COMPARED_WITH = re.compile(r"\b(?:compare|compared|comparing|contrast|contrasted|contrasting)\b[^,;?]{0,40}?\bwith\b", re.IGNORECASE)
"""A "with" that follows a compare verb closely ("compare luka with sga")
joins the two subjects; it is not a companion phrase (ISSUES.md #233)."""
# "excluding" is "without" reworded and "featuring" is "with"; a question word ends the phrase, so a
# fronted "Without Kevin Durant, what is Steph Curry's record" names Durant
# alone, not Curry with him.
# "with and without Tatum" is the split over Tatum, read from its "without":
# the "with" names nobody.
_COMPANION = re.compile(
    r"\b(without|excluding|with(?!\s+(?:and|or)\s+without\b)|featuring|when|while)\s+"
    r"((?:(?!\b(?:vs\.?|versus|against|in|for|this|last|the|what|who|how|which|where)\b)[\w'.,+-]+\s*){1,9})",
    re.IGNORECASE,
)
# "in games Embiid started", "in the games Brown missed": the role stated after
# the games it narrows, with no "when" or "with" before the name - "maxey
# points in games embiid started" compared the two players, since nothing read
# Embiid as anything but a second subject. The name (one to three words) must
# be followed directly by what he did in those games - started, came off,
# played, scored or had a line, sat out - so "in games against Boston" and "in
# games he started" (the subject's own) name no companion here.
_COMPANION_STOP = r"vs\.?|versus|against|in|for|this|last|the|what|who|how|which|where|with|without|when|while"
_COMPANION_IN_GAMES = re.compile(
    r"\b(in\s+(?:the\s+)?games?(?:\s+(?:that|where|in\s+which))?)\s+"
    rf"((?:(?!\b(?:{_COMPANION_STOP}|he|she|they|his|her|their)\b)[\w'.-]+\s+){{1,3}}"
    rf"(?:start(?:s|ed)?|(?:comes?|came)\s+off|play(?:s|ed)?|scor(?:e|es|ed)|had|has|got|{_ABSENCE_WORDS})(?![A-Za-z])"
    rf"(?:\s+(?!(?:{_COMPANION_STOP})\b)[\w'.,+-]+){{0,4}})",
    re.IGNORECASE,
)

#: What a companion phrase says the player DID in the games asked about,
#: read off the phrase's own words: a threshold ("scores 20+ points", or
#: "scores 30", a line on points), a start, the bench, an absence ("out",
#: "injured", "without"), else played.
_CONDITION_THRESHOLD = re.compile(r"\b(\d{1,3})[\s-]*(?:\+|plus|or\s+more)?[\s-]*(" + "|".join(sorted((re.escape(w) for w in _THRESHOLD_WORDS), key=len, reverse=True)) + r")\b", re.IGNORECASE)
_CONDITION_STARTED = re.compile(r"\bstart(?:s|ed|ing)?\b|\bin the starting lineup\b", re.IGNORECASE)
# "off" alone too: the phrase stops at "the" (a stop word), so "with tatum
# off the bench" reaches here as "tatum off".
_CONDITION_BENCH = re.compile(r"\bbench\b|\boff\b|\bas a reserve\b", re.IGNORECASE)
# The router's absence words (one list, so the `without` it reads and the role
# read here agree), and "hurt", which the router cannot end a name at - it is
# Matt Hurt's - and a role reader, which never cuts a name, can take.
_CONDITION_ABSENT = re.compile(rf"\b(?:{_ABSENCE_WORDS}|hurt)(?![A-Za-z])", re.IGNORECASE)


class Companion(NamedTuple):
    """A player named beside the subject with the role the question gives
    him in the games asked about: ``predicate`` is one of
    :data:`~association.query.player_games.CONDITION_PREDICATES` (``played``,
    ``absent``, ``started``, ``bench``, ``reached``), and a ``reached`` role
    carries its ``stat`` and ``threshold`` ("when Maxey scores 20+ points").
    ``side`` is whose games he was in: the subject's own (a teammate), or
    the ``opponent``'s - a player after "vs"/"against" ("most points by
    curry vs lebron": Curry's games with LeBron on the other side, ROADMAP
    step 3). The reading's side of the relation's
    :class:`~association.query.player_games.Condition`;
    :func:`apply_subject` writes it as the ``conditions`` slot.

    .. versionadded:: 5.0.0

    .. versionchanged:: 5.0.0
       ``side`` (ROADMAP step 3).
    """

    name: str
    predicate: str
    stat: str | None = None
    threshold: int | None = None
    side: str = "own"


#: The singular of a team's nickname names the team: "a hawk player", "a
#: laker". :data:`association.query.entities._TEAM_NICKNAMES` holds the
#: plural shorthands ("sixers", "mavs"); these are the singulars, plus the
#: one-word spellings the roster table's split cannot find.
TEAM_SINGULARS: dict[str, str] = {
    "hawk": "Atlanta Hawks",
    "celtic": "Boston Celtics",
    "net": "Brooklyn Nets",
    "hornet": "Charlotte Hornets",
    "bull": "Chicago Bulls",
    "cavalier": "Cleveland Cavaliers",
    "maverick": "Dallas Mavericks",
    "nugget": "Denver Nuggets",
    "piston": "Detroit Pistons",
    "warrior": "Golden State Warriors",
    "rocket": "Houston Rockets",
    "pacer": "Indiana Pacers",
    "clipper": "Los Angeles Clippers",
    "laker": "Los Angeles Lakers",
    "grizzly": "Memphis Grizzlies",
    "buck": "Milwaukee Bucks",
    "timberwolf": "Minnesota Timberwolves",
    "pelican": "New Orleans Pelicans",
    "knick": "New York Knicks",
    "sixer": "Philadelphia 76ers",
    "sun": "Phoenix Suns",
    "king": "Sacramento Kings",
    "spur": "San Antonio Spurs",
    "raptor": "Toronto Raptors",
    "wizard": "Washington Wizards",
    "trailblazers": "Portland Trail Blazers",
    "blazers": "Portland Trail Blazers",
    "okc": "Oklahoma City Thunder",
}
"""A team's singular nickname, or a one-word spelling, mapped to its name.

.. versionadded:: 4.4.0
"""


@functools.lru_cache(maxsize=1)
def _dictionary() -> frozenset[str]:
    """The ordinary words: the lowercase entries of a word list shipped in
    this package (a capitalized entry is a proper noun - "Jordan" is a name
    there, "best" a word), plus the month abbreviations.

    Shipped rather than read from ``/usr/share/dict/words``, which is what
    this first did: a machine without that file read "Best record from
    2010-11 to 2018-19" as a question about Travis Best, so the same question
    had a different answer on a machine without it - GitHub's runner is one,
    and CI failed there while passing locally. ``words.txt.gz`` is Debian's
    ``wamerican`` 2020.12.07 (SCOWL, notice in ``words.COPYRIGHT``) reduced to
    the entries this function used to keep from it (lowercase first letter, no
    apostrophe - ``str.islower``, so "élan" is kept), sorted, gzipped with
    ``mtime=0``: the same 64,005 words the system file gave on the machine
    this was measured on. A missing file raises
    rather than falling back: a wheel that dropped it would otherwise answer
    differently with no error."""
    words = gzip.decompress(resources.files("association.query").joinpath("words.txt.gz").read_bytes()).decode()
    return frozenset(words.split()) | _MONTH_ABBREVIATIONS


def _edit_distance(a: str, b: str) -> int:
    """Edit distance for a near spelling, a swapped pair of letters counted as
    one edit: rapidfuzz's Damerau-Levenshtein, the metric the entity index
    measures with in SQL (DuckDB's ``damerau_levenshtein``, entities.py) -
    the two agree on every ASCII pair measured (142,880 word pairs from the
    corpora and the player table; DuckDB counts UTF-8 bytes where this counts
    letters). Plain Levenshtein here, until 5.0.0, charged a transposition
    twice, so "jokci stats" did not support Nikola Jokic while the index
    read "jokci" as him."""
    return DamerauLevenshtein.distance(a, b)


def question_supports(name: str, question: str) -> bool:
    """Whether ``question`` holds ``name``: any one word of it, a near
    spelling of one (three or more letters) within
    :func:`association.query.entities._edit_budget` (none for a short word,
    one edit to six letters, two beyond - the router corrects typos, "embid"
    is Embiid), a curated nickname for the player, or its initials ("SGA",
    "kd"). The repair chain's grounding check, as a predicate.

    .. versionadded:: 4.4.0
    """
    q = [w.casefold() for w in _words(question)]
    words = [w.casefold() for w in _words(name)]
    if any(w in q for w in words):
        return True
    # A near spelling of a word under three letters is a different word.
    if any(len(w) >= 3 and any(len(x) >= 3 and _edit_distance(w, x) <= _edit_budget(w) for x in q) for w in words):
        return True
    if name in nicknames_in(question):
        return True
    initials = _initials(name)
    return bool(initials) and initials in q


def _near(name: str, text: str) -> bool:
    """A word of the name within two edits of a word of the text (six or
    more letters: "wembyanama" for Wembanyama), or supported outright."""
    if question_supports(name, text):
        return True
    t = [w.casefold() for w in _words(text) if len(w) >= 6]
    return any(len(w) >= 6 and any(_edit_distance(w.casefold(), x) <= 2 for x in t) for w in _words(name))


def _is_a_team(con: duckdb.DuckDBPyConnection, name: str) -> bool:
    """A name the router filed that is a team and no player ("Alamhamed
    Embiid" was filed under ``teams``; "Boston Celtics" under ``players``)."""
    return bool(find_teams(con, name)) and not find_players(con, name)


def _team_abbreviation(con: duckdb.DuckDBPyConnection, question: str) -> str | None:
    """A team named by its abbreviation, in capitals ("PHI record 2026")."""
    rows = {str(abbr).casefold(): str(name) for abbr, name in team_abbreviations(con).items()}
    for w in _words(question):
        if len(w) == 3 and w.isupper() and w.casefold() in rows:
            return rows[w.casefold()]
    return None


def _team_word(con: duckdb.DuckDBPyConnection, question: str) -> str | None:
    """The team the question names as a word: a singular or one-word
    nickname first, then :func:`~association.query.entities.team_named_in`
    (the roster table by whole word, the plural nicknames), then an
    abbreviation."""
    for w in _words(question.lower()):
        if w in TEAM_SINGULARS:
            return TEAM_SINGULARS[w]
    return team_named_in(con, question) or _team_abbreviation(con, question)


def _by_nickname(name: str, question: str) -> bool:
    """The question uses a curated nickname for the player ("steph curry",
    where "curry" alone is a dictionary word - a food)."""
    q = f" {question.lower()} "
    return any(f" {key} " in q for key, who in PLAYER_NICKNAMES.items() if who == name)


def _question_players(con: duckdb.DuckDBPyConnection, question: str, routed: list[str], teams_here: set[str]) -> list[str]:
    """Players the question names, minus the spans that are not players in
    THIS question: the word that names a team here ("boston" after "vs" is
    the Celtics, not Brandon Boston Jr.; "magic" is Orlando, not Magic
    Johnson), and an ordinary word that happens to be somebody's whole name
    ("best" = Travis Best, "head" = Luther Head, "pointer", "jan") unless
    the router read it as that player too or the question uses the
    player's nickname."""
    q_words = [w.casefold() for w in _words(question)]
    dictionary = _dictionary()
    kept: list[str] = []
    for name in dict.fromkeys(players_named_in(con, question)):
        matched = [w for w in (x.casefold() for x in _words(name)) if w in q_words]
        if not matched:
            kept.append(name)  # a nickname key the question used: "kat", "sga"
            continue
        if teams_here and any(len(w) >= 4 and (TEAM_SINGULARS.get(w) or team_named_in(con, w)) in teams_here for w in matched):
            continue
        if all(w in dictionary for w in matched) and not any(question_supports(name, p) for p in routed) and not _by_nickname(name, question):
            continue
        kept.append(name)
    return kept


def _merge_names(router_named: list[str], question_named: list[str]) -> tuple[str, ...]:
    """The router's names the question supports (in the question's own
    spelling, and once each - a player filed as both ``player`` and
    ``opponent`` is one person), plus the names the question gives that are
    not one of them already - the router's completion of a surname the
    question gives is the same person, not a second one. The router's spelling leads on purpose:
    :func:`~association.query.entities.players_named_in` reads "kareem stats
    vs bob lanier" as naming Kareem Rush and Chaz Lanier, two real players by
    whole word and neither the one asked about (ISSUES.md #123), and the
    router's "Kareem Abdul-Jabbar" is the better reading of "kareem"."""
    out: list[str] = []
    for name in (*router_named, *question_named):
        if not _same_person(name, out):
            out.append(name)
    return tuple(out)


def _read_opponent(con: duckdb.DuckDBPyConnection, question: str, scope: Scope, season: int | None) -> str | None:
    """The team the subject is set against: the team after "vs" / "against"
    in the question's own words, whenever there is one - "duren v nets 1h
    gameloh" arrived as ``opponent="New Jersey Nets"``, which resolves to no
    team, "tatum vs lakers" once as the Trail Blazers, a real team the
    question never wrote, and "magic vs nets" with the two sides swapped;
    the question's own wins each time. With no "vs" to read, the router's
    ``opponent`` stands where it names a team the question holds
    (:func:`~association.query.entities._team_grounded`): "76ers 4th
    quarter points against boston" is ``team_quarter_points``' own slot. And
    with a "vs" whose word resolves to nothing, the router's ``team`` stands
    as the opponent where the question holds it nowhere else - its reading
    of that word."""
    versus = _team_after_versus(con, question, season)
    if versus is not None:
        return versus.name
    held = scope.opponent
    held_team = _team_named(con, held, season) if isinstance(held, str) and held.strip() else None
    if held_team is not None and _team_grounded(con, question, held_team) and not _named_as_own(con, question, held_team, season):
        return held_team.name
    # "karl towns stats vs netslast 5 games": the "vs" names a word nothing
    # resolves, and the router read it as a team it filed in `team` - a team
    # the question never holds otherwise, so the router's reading of that
    # word is the one there is.
    team = _team_named(con, scope.team, season) if held_team is None else None
    if team is not None and _AGAINST.search(question) and not _team_grounded(con, question, team):
        return team.name
    return None


def _named_as_own(con: duckdb.DuckDBPyConnection, question: str, team: Entity, season: int | None) -> bool:
    """Whether ``team`` is the one the question names after "for" - "show me
    splits for the sixers when maxey scores 20+ points" arrived with the
    76ers as the ``opponent`` beside an invented Joel Embiid (day5), and the
    reading took the router's word for it, leaving the question with no
    team as its subject. A "for <team>" with no "vs" anywhere is the
    subject's side, never the other one."""
    own = _team_after_for(con, question, season)
    return own is not None and own[0].id == team.id and not _AGAINST.search(question)


def _opponent_player(con: duckdb.DuckDBPyConnection, scope: Scope) -> str | None:
    """A route's ``opponent`` when it names a player and not a team ("jay
    huff game log vs Embiid" arrived from the router with Jokic there): a
    name to check against the question like the subject's own, never read
    as a team the subject is set against. A team opponent is a narrowing,
    not a subject."""
    opponent = scope.opponent
    if not isinstance(opponent, str) or not opponent.strip() or find_teams(con, opponent) or not find_players(con, opponent):
        return None
    return opponent


def _routed_names(con: duckdb.DuckDBPyConnection, scope: Scope, question: str, opponent_player: str | None) -> tuple[list[str], list[str]]:
    """The router's player names - from ``player``, ``players``, a player
    filed as the ``opponent`` and one filed as the ``team`` - split into the
    ones the question supports and
    the ones it never held, minus any that is a team (the router files
    "Boston Celtics" as a player; a team is a narrowing, not an invention)."""
    team_slot = _team_slot_player(con, scope)
    routed = [p for p in _routed_player_slots(scope) + ([opponent_player] if opponent_player else []) + ([team_slot] if team_slot else []) if not _is_a_team(con, p) and not _not_a_name(p)]
    supported = [p for p in routed if question_supports(p, question)]
    return supported, [p for p in routed if p not in supported]


def _filler_names(con: duckdb.DuckDBPyConnection, scope: Scope, question: str) -> tuple[str, ...]:
    """The router's player names that are no name (:func:`_not_a_name`) or
    that the question holds only by a team's word - :attr:`Subject.filler`."""
    return tuple(p for p in _routed_player_slots(scope) if _not_a_name(p) or (not _is_a_team(con, p) and _named_only_by_a_team_word(con, question, p)))


def _not_a_name(text: str) -> bool:
    """A router ``player`` that is no name at all: a position phrase ("shooting
    guard" on "highest 3 point percentage ... by a shooting guard", F056 - the
    position-group subject, which :attr:`Subject.position` carries) or the
    router's filler word ("player" on "Most points in 15th season played",
    F099). Neither is a player to read, replace or report."""
    stripped = text.strip()
    if _NO_NAME_HAS.search(stripped):
        # "most 30+ point games", "most", "Most Player in 15th Season Played"
        # - what the model files as the player once nothing in its prompt
        # shows a count or a ranking with none (the 5.0.0 prompt shrink).
        # No player's name holds a digit, a plus sign, a rank word or the
        # word "player"; a phrase the question literally contains is not a
        # name for holding it.
        return True
    return stripped.lower() in FILLER_PLAYER_WORDS or any(re.fullmatch(pattern, stripped, re.IGNORECASE) for pattern, _ in POSITIONS)


#: Anchored to the START for the rank words, since a real name can end in
#: one ("Travis Best" - the trap AGENTS.md records) and none begins so.
_NO_NAME_HAS = re.compile(r"\d|\+|^(?:most|fewest|least|top|best|worst|highest|lowest)\b|\bplayers?\b", re.IGNORECASE)


def _team_slot_player(con: duckdb.DuckDBPyConnection, scope: Scope) -> str | None:
    """The router's ``team`` when it names a player and no team: "Podziemski
    game log without curry" arrived as ``team='Podziemski'``, and "Will
    Riley last 5 game s" as ``team='Riley'`` (a bare fragment, three
    players) - the subject, in the wrong slot."""
    team = scope.team
    if not isinstance(team, str) or not team.strip() or _team_named(con, team) is not None or not find_players(con, team):
        return None
    return team


def _spellings(con: duckdb.DuckDBPyConnection, question: str, routed: list[str]) -> dict[str, str]:
    """The question's own spelling of each router name it supports, resolved
    from the question's words anchored at the router's
    (:func:`~association.query.entities._question_derived_player`): a bare
    "Jokic" completed, "Jaylen Huff" cut back to the "Jay Huff" typed, "Grady
    Dickinson" put right as Gradey Dick, and "Stephen Curry" for a "Seph
    Curry" the question itself misspelled - a typo of the QUESTION's, which
    no whole-word match finds. Anchored on purpose: a whole-word match reads
    "kareem stats vs bob lanier" as Kareem Rush and Chaz Lanier, while the
    anchored span settles neither and the router's spelling stands."""
    out: dict[str, str] = {}
    for name in routed:
        derived = _question_derived_player(con, question, name)
        if derived is not None and derived.name != name:
            out[name] = derived.name
    return out


def _conditions(question: str, players: tuple[str, ...], scope: Scope) -> tuple[Companion, ...]:
    """The players whose ROLE the question states beside the subject -
    "without X", "with X on the floor", "when X scored 20+" - each with that
    role (:class:`Companion`), read from the question's own words, never
    from the router's ``without`` slot (which filed Embiid under it on
    "Embiid's record against Boston"). The router's ``without``/``with_player``
    slots are consulted only for a companion the question misspells
    ("without wembyanama"), where the phrase is a near spelling of the
    router's name. One phrase, one role: "when Embiid and Paul George
    start" is two ``started`` companions.

    .. versionchanged:: 5.0.0
       Returns :class:`Companion` tuples with the predicate, not names; a
       player after a versus word is an opponent-side ``played`` companion
       where the words ask for the subject's games (:func:`_versus_companions`).
    """
    found: list[Companion] = []
    for match in _companion_phrases(question):
        word, text = match.group(1).lower(), match.group(2)
        predicate, stat, threshold = _condition_role(word, text)
        names = _companion_names(text, players, scope, predicate)
        found.extend(Companion(name, predicate, stat, threshold) for name in names if not any(_same_person(name, [c.name]) for c in found))
    found.extend(_versus_companions(question, players, scope, found))
    return tuple(found)


def _companion_phrases(question: str) -> list[re.Match[str]]:
    """The companion phrases of ``question`` (:data:`_COMPANION`), minus a
    "with" that a compare verb owns - "compare luka with sga" names two
    subjects and no companion (:data:`_COMPARED_WITH`) - and the "in games
    X started" ones (:data:`_COMPANION_IN_GAMES`) no keyword phrase already
    covers, in the order the question gives them."""
    compared = [m.span() for m in _COMPARED_WITH.finditer(question)]
    phrases = [m for m in _COMPANION.finditer(question) if not any(a <= m.start() < b for a, b in compared)]
    in_games = [m for m in _COMPANION_IN_GAMES.finditer(question) if not any(p.start() < m.end() and m.start() < p.end() for p in phrases)]
    return sorted([*phrases, *in_games], key=lambda m: m.start())


_NAME_PIECES = re.compile(r"[^\s,&+]+|[,&+]")
_NAME_SHAPED = re.compile(r"[A-Za-z][A-Za-z.'\-]*")
_NAME_JOINERS = frozenset({"and", "or", "nor", "&", "+", ","})
_MAX_NAME_WORDS = 3


def _name_segments(text: str) -> list[str]:
    """The names a companion phrase holds BY POSITION, as typed and in
    order: the words after the keyword up to one that cannot be part of a
    name (:data:`~association.query.router._NAME_STOPWORDS`, a number),
    split at each joiner ("and", "or", a comma). "Tatum, Brown and Holiday
    this season" is three; "a turnover" is none ("a" is no name's word);
    "and without Tatum" is none, a joiner with nothing before it.

    This is what the stages' own reader of these phrases did until 5.0.0,
    and why it is kept: a name read by position does not depend on the
    model having copied it or on the word being nobody else's - "without
    curry" and "without Tatum and Brown" name ordinary words two or ten
    players share, which no lookup settles and the template asks about."""
    names: list[str] = []
    words: list[str] = []
    for piece in _NAME_PIECES.findall(text):
        lowered = piece.casefold()
        if lowered in _NAME_JOINERS:
            if not words:
                break
            names.append(" ".join(words))
            words = []
            continue
        if not _NAME_SHAPED.fullmatch(piece) or lowered in _NAME_STOPWORDS or len(words) >= _MAX_NAME_WORDS:
            break
        words.append(piece)
    if words:
        names.append(" ".join(words))
    return names


def _companion_names(text: str, players: tuple[str, ...], scope: Scope, predicate: str = "played") -> list[str]:
    """Who one companion phrase names, in the phrase's order: each name it
    holds by position (:func:`_name_segments`) as the player the question
    is known to hold where one supports it, then the known players its
    words support that no position held ("when Embiid plays with Paul
    George"), then a router ``without``/``with_player`` name the phrase
    misspells. A name by position that is no known player is kept as typed
    only for an ABSENCE ("without zzyzx" is refused by that name, never
    answered as though nobody had been named); for any other role it has to
    be one the reading found (:func:`_unrouted_companions`), since the words
    after "with" and "when" are often no name at all ("with less than 15
    fga")."""
    routed = [r for r in list(scope.without or []) + list(scope.with_player or []) + [c.player for c in scope.conditions] if isinstance(r, str)]
    known = [p for p in players if question_supports(p, text)]
    by_position = [_companion_names_segment(segment, known, routed, predicate) for segment in _name_segments(text)]
    names = list(dict.fromkeys(name for name in by_position if name is not None))
    names += [p for p in known if p not in names]
    names += [r for r in routed if not _same_person(r, names) and _near(r, text)]
    return names


def _companion_names_segment(segment: str, known: list[str], routed: list[str], predicate: str) -> str | None:
    """Who one name read by position is: the known player it supports, a
    routed name it misspells, itself as typed for an absence - or nobody."""
    for player in known:
        if _same_person(player, [segment]):
            return player
    for name in routed:
        if _near(name, segment):
            return name
    return segment if predicate == "absent" else None


# A player after a versus word is on the OTHER side of the subject's games -
# a condition (ROADMAP step 3: "most points by curry vs lebron", "how many
# times did lebron score 30 vs kawhi"), never a second subject - wherever the
# words ask for the subject's GAMES rather than the pair's summary: a high,
# a count, a record, a log, a streak, a history, splits (the child grammars'
# words and the log words). A bare "curry vs lebron" or "curry stats vs
# lebron" stays the pair, whose matchup summary reads both lines.
_VERSUS_PHRASE = re.compile(rf"\b(vs\.?|versus|against|v\.?)\s+((?:(?!\b(?:{_COMPANION_STOP})\b)[\w'.,+-]+\s*){{1,9}})", re.IGNORECASE)
# Not "record": a pair's summary carries the head-to-head record, so "lebron
# vs kawhi record" stays the matchup.
_GAMES_NOT_SUMMARY = re.compile(r"\b(?:game ?logs?|gamelogs?|logs?|each game|by game|game by game|box scores?|most|highest|fewest|lowest|best|worst)\b", re.IGNORECASE)


def _asks_for_games(question: str) -> bool:
    """Whether the words ask for the subject's games - a log, a high or a
    low, or any child grammar's shape (a count, a streak, a history, splits,
    a record over a line) - rather than the pair's matchup summary."""
    return _GAMES_NOT_SUMMARY.search(question) is not None or any(words.search(question) for _, words, _, _ in _CHILD_GRAMMARS)


def _versus_companions(question: str, players: tuple[str, ...], scope: Scope, found: list[Companion]) -> list[Companion]:
    """The players after a versus word, as opponent-side ``played``
    companions - only where a player is named BEFORE the phrase (the
    subject; "celtics vs lebron" names no subject beside him) and the words
    ask for his games (:func:`_asks_for_games`). Any number: "giannis
    points vs lebron and curry" is the games both played against him. A
    team after "vs" names nobody here (it is the opponent slot's)."""
    # A pair's summary reads two lines; three names or more are a subject
    # and conditions whatever the words ask - and so is a scope the route
    # already carries them in (the reading's second pass, parse.read_route
    # having written the first's).
    carried = any(c.side == "opponent" for c in scope.conditions)
    subjects = [p for p in players if not any(_same_person(p, [c.name]) for c in found)]
    if not _asks_for_games(question) and len(subjects) <= 2 and not carried:
        return []
    versus: list[Companion] = []
    for match in _VERSUS_PHRASE.finditer(question):
        if not _named_before(question, players, match.start()):
            continue
        names = [n for n in _companion_names(match.group(2), players, scope) if not any(_same_person(n, [c.name]) for c in [*found, *versus])]
        versus.extend(Companion(name, "played", side="opponent") for name in names if not _named_before(question, (name,), match.start()))
    return versus


def _unrouted_companions(con: duckdb.DuckDBPyConnection, question: str, players: tuple[str, ...], scope: Scope) -> tuple[str, ...]:
    """A companion the model named nobody for, read from the phrase's own
    words: "show me splits for the sixers when maxey scores 20+ points"
    arrived with an invented Joel Embiid and no Maxey anywhere in the slots
    (yardstick-v2 F087), and a companion was only ever read from the
    model's names - so the reading refused by Embiid's name where the
    question had an answer (the 76ers' record, 35-28).

    Each name the phrase holds by position (:func:`_name_segments`) that no
    known player supports is taken where its words are, together, the words
    of some player's name - "curry", "brown", "paul george" - or a near
    spelling of exactly one player's (:func:`_unrouted_near_spelling`:
    "wembyanama"). A word a team or a stat is named by is no player
    ("when the sixers ...", "with 20 points"), and one ordinary word that
    is no name is nobody ("with less", "when starting"). What is kept is
    the QUESTION's spelling, never a resolution: the template resolves it
    the way it resolves any open name and says how, so a bare "maxey" is
    Tyrese because he is the one who still plays - visible and correctable
    - and a word two active players share is asked about.

    .. versionchanged:: 5.0.0
       Reads every name of the phrase by position, an ordinary word that is
       a player's name included, where it read the leading two words and
       skipped a dictionary word: the stages' own reader of these phrases is
       gone (``ROADMAP.md``, Phase 1), and this is the only one.
    """
    routed = [r for r in list(scope.without or []) + list(scope.with_player or []) + [c.player for c in scope.conditions] if isinstance(r, str)]
    found: list[str] = []
    for match in _companion_phrases(question):
        absent = _condition_role(match.group(1).lower(), match.group(2))[0] == "absent"
        for segment in _name_segments(match.group(2)):
            # Already somebody: a player the question is known to hold, or a
            # replayed route's own name for him, which the phrase misspells.
            if any(_same_person(segment, [p]) for p in (*players, *found)) or any(_near(r, segment) for r in routed):
                continue
            name = _unrouted_name(con, segment.split(), absent)
            if name is not None and not any(_same_person(name, [p]) for p in (*players, *found)):
                found.append(name)
    return tuple(found)


def _unrouted_name(con: duckdb.DuckDBPyConnection, words: list[str], absent: bool) -> str | None:
    """The player's name a phrase's words by position hold, as typed: the
    whole run where its words are together some player's ("paul george",
    "curry"), else its leading two words, else its first ("maxey scores"
    is Maxey: what he did follows his name). An ordinary word standing for
    a name ("brown", "green") is taken where it is the whole run or the
    phrase states an absence - a name's place - and never as the leading
    word of something else ("with strong shooting" names no Derek Strong).
    A word a team or a stat is named by, a number and a word under three
    letters name nobody."""
    dictionary = _dictionary()
    for size in dict.fromkeys((len(words), 2, 1)):
        if size > len(words):
            continue
        span = [word.casefold().strip("'.,-").removesuffix("'s") for word in words[:size]]
        if any(len(word) < 3 or word in _THRESHOLD_WORDS or word in TEAM_SINGULARS or team_named_in(con, word) for word in span):
            continue
        ordinary = any(word in dictionary for word in span)
        if ordinary and size < len(words) and not absent:
            continue
        if _exact_name_span(con, span, limit=1) or _unrouted_near_spelling(con, span):
            return " ".join(words[:size]) if size == len(words) else " ".join(span)
    return None


def _unrouted_near_spelling(con: duckdb.DuckDBPyConnection, span: list[str]) -> bool:
    """Whether a companion phrase's leading words, which are no player's
    name as spelled, are a near spelling of exactly ONE player's:
    "without wembyanama". The words after "without" or "with" are a name's
    place, not the question's leftover words, so the near-spelling pass
    applies to them as it does to a name slot
    (:func:`~association.query.entities.read_near_spelling`, and its three
    refusals: two candidates ask, a team's name is no player's, and every
    word must be close). The span is kept as typed; resolving it, and saying
    it was read as a near spelling, stays the entity index's. Until 5.0.0
    only the stages' own reader of "without X" carried such a name."""
    text = " ".join(span)
    if any(word in _dictionary() for word in span):
        return False  # a typo is no ordinary word: "season" is one edit from Tari Eason
    return _team_named(con, text) is None and len(_suggest_players_by_spelling(con, span)) == 1


def beside(conditions: tuple[Companion, ...]) -> Beside:
    """Who the reading found beside the subject, for the stages
    (:class:`~association.query.router.Beside`): a teammate who played with
    him, and anyone who sat the games out. A start, the bench and a line
    reached are roles :func:`apply_subject` writes as conditions; a player
    on the other side is the relation's condition, never a teammate.

    .. versionadded:: 5.0.0
    """
    return Beside(
        played=tuple(c.name for c in conditions if c.predicate == "played" and c.side == "own"),
        absent=tuple(c.name for c in conditions if c.predicate == "absent"),
    )


def _condition_role(word: str, text: str) -> tuple[str, str | None, int | None]:
    """The predicate a companion phrase states, from its keyword and its
    words: a threshold pair wins ("scores 20+ points" is ``reached`` whatever
    the keyword, and so is "scores 30", a line on points), then a start, the
    bench, an absence ("without", "out", "injured"), else ``played``
    ("with", "when ... play")."""
    pair = _CONDITION_THRESHOLD.search(text)
    if pair is not None and int(pair.group(1)) >= 1 and not (int(pair.group(1)) == 3 and pair.group(2).lower().startswith(("point", "pt"))):
        return "reached", _THRESHOLD_WORDS[pair.group(2).lower()], int(pair.group(1))
    scored = _threshold_from_text_scored(text)
    if scored is not None:
        return "reached", "points", scored
    if _CONDITION_STARTED.search(text):
        return "started", None, None
    if _CONDITION_BENCH.search(text):
        return "bench", None, None
    if word in ("without", "excluding") or _CONDITION_ABSENT.search(text):
        return "absent", None, None
    return "played", None, None


def _team_names(con: duckdb.DuckDBPyConnection, question: str, scope: Scope, team_word: str | None, opponent: str | None, own_team: str | None) -> list[str]:
    """The team(s) the question is about: the word it names first, then the
    router's ``team`` / ``teams`` where the question supports them and they
    are teams - never the opponent or the player's own team over again."""
    names: list[str] = []
    if team_word and team_word not in (opponent, own_team):
        names.append(team_word)
    routed_team = scope.team
    if (
        isinstance(routed_team, str)
        and routed_team
        and not names
        and question_supports(routed_team, question)
        and _team_named(con, routed_team) is not None  # "any_team" is the router's placeholder, not a team
        and not any(t and (question_supports(routed_team, t) or question_supports(t, routed_team)) for t in (opponent, own_team))
    ):
        names.append(routed_team)
    for t in scope.teams or []:
        if isinstance(t, str) and question_supports(t, question) and _is_a_team(con, t) and t not in names and t != opponent:
            names.append(t)
    return names


def read_subject(con: duckdb.DuckDBPyConnection, question: str, intent: str, scope: Scope) -> Subject:
    """One reading of the subject. The question's spans decide; a slot the
    router filled (``scope``, the typed Scope a route carries) counts only
    where the question's own words support it
    (:func:`question_supports`), and never to invent a subject the question
    does not name. ``intent`` is read only to tell two teams meeting
    (``head_to_head``) from a team set against another.

    .. versionadded:: 4.4.0
    """
    # The season a team's name is read in. The parser reads the subject
    # BEFORE the stages settle the season (they are handed its companions),
    # so the scope it reads from holds none: the question's own season is
    # read here, or "the hornets in 2008" is today's Charlotte Hornets and
    # Chris Paul's New Orleans team is asked about as a name nobody typed
    # (ISSUES.md #316).
    season = scope.season if scope.season is not None else season_from_text(question)
    own = _team_after_for(con, question, season)
    team_word = _team_word(con, question)
    position = next((code for pattern, code in POSITIONS if re.search(pattern, question, re.IGNORECASE)), None)
    routed_opponent = _opponent_player(con, scope)
    opponent = _read_opponent(con, question, scope, season) if routed_opponent is None else None
    teams_here = {t for t in (opponent, own[0].name if own is not None else None, team_word) if t}

    routed, invented = _routed_names(con, scope, question, routed_opponent)
    filler = _filler_names(con, scope, question)
    routed = [p for p in routed if p not in filler]
    named = _question_players(con, question, routed, teams_here)
    spellings = _spellings(con, question, routed)
    players = _merge_names([spellings.get(r, r) for r in routed], named)
    unrouted = _unrouted_companions(con, question, players, scope)
    conditions = _conditions(question, (*players, *unrouted), scope)
    companions = tuple(dict.fromkeys(c.name for c in conditions))
    players = tuple(p for p in players if p not in companions)
    if _read_subject_alone(players, teams_here, conditions, scope):
        players, conditions, companions = companions, (), ()
    # "for the Heat" is a player's OWN team only beside a player subject
    # named BEFORE it - the order a tenure is asked in ("lebron ... for
    # Miami"); with none, or with the player following as a condition
    # ("stats for sixers when maxey scored 20+", F087), it names the team the
    # question is about.
    own_team = own[0].name if own is not None and players and _named_before(question, players, own[1]) else None
    teams = _team_names(con, question, scope, team_word, opponent, own_team)
    evidence = _evidence(named, routed, spellings, invented, team_word, opponent)
    if unrouted:
        evidence = (*evidence, f"companions the router named nobody for {list(unrouted)}")
    subject = replace(_decide(players, teams, position, opponent, own_team, companions, evidence, intent, question), conditions=conditions)
    # Read, not decided: the intent the subject's shape settles is the
    # parser's step (:func:`child_named`, with the stages run once under
    # it), and :func:`settle_subject` writes it here. Until 5.0.0's last
    # change this ran the stages under each child the words named, before
    # the parser ran them at all.
    return replace(subject, invented=tuple(invented), named_season=season_from_text(question), intent=intent, question=question, filler=filler)


def settle_subject(subject: Subject, intent: str, *, parent: str | None = None, words: str | None = None) -> Subject:
    """``subject``, already read, under the ``intent`` the stages settled -
    no name is read again, the warehouse is not asked and the stages do not
    run (``ROADMAP.md``, Phase 1: the subject is read once, the stages run
    once). What depends on the intent is written here: two teams meeting
    are the ``teams`` kind under ``head_to_head``, and the intent itself
    with why it is not ``parent``'s - the ``words`` that named a child
    (:func:`child_named`), or the one move that names no words, a team's
    record in the games a companion reached a line. Who stands beside the
    subject is the reading's too, and the stages take it from there
    (:func:`beside`).

    .. versionadded:: 5.0.0

    .. versionchanged:: 5.0.0
       Takes the child the parser decided (``parent``, ``words``) and runs
       no stage of its own: until then it ran the stages under each child
       the words named, a speculative run beside the parser's.
    """
    who = subject
    if who.kind == "team" and who.teams and who.opponent and intent == "head_to_head":
        who = replace(who, kind="teams", teams=(who.teams[0], who.opponent), opponent=None)
    evidence = tuple(line for line in who.evidence if not line.startswith("the words "))
    moved = parent is not None and intent != parent
    return replace(
        who,
        intent=intent,
        intent_reason=_intent_reason(parent or intent, intent, words) if moved else None,
        evidence=(*evidence, f"the words {words!r} name {intent}") if words else evidence,
    )


def _intent_reason(intent: str, settled: str, words: str | None) -> str | None:
    """Why ``settled`` is not the route's ``intent``
    (:attr:`Subject.intent_reason`): the words that name a child, or -
    the one other way :func:`child_named` moves it, and the only one that
    names no words - a team's record in the games a companion reached a
    line."""
    if settled == intent:
        return None
    return f"the words {words!r} name {settled}" if words is not None else _TEAM_RECORD_WHEN


def _read_subject_alone(players: tuple[str, ...], teams: set[str], conditions: tuple[Companion, ...], scope: Scope) -> bool:
    """Whether the one player named only as a companion is the subject after
    all: "2 threes in games Jamal Murray played" names nobody BESIDE him, so
    the games he played are his own games - he is the subject, and that he
    played them narrows nothing. Only one, and only with no team anywhere:
    "the 76ers record when both Embiid and Paul George played" is the
    team's question with two companions ("76ers" is no word the team
    reading finds, so the routed ``team`` slot is what says so). Split out
    of :func:`read_subject` for the complexity gate."""
    named_team = teams or scope.team or scope.teams
    return not players and not named_team and len(conditions) == 1 and conditions[0].predicate == "played"


def child_named(subject: Subject, parent: str, question: str) -> tuple[str, str | None] | None:
    """The child of ``parent`` the subject's shape names, with the words
    that name it - or None where the parent stands. Decided from the
    grammar alone; the parser runs the stages ONCE under the child, and
    where they decline it (a count with no threshold in the text is a
    ranking) runs them under the parent instead
    (:func:`~association.query.parse.read_route`). Two ways a child is
    named:

    - A team has a companion's line as the condition: "show me splits for
      the sixers when maxey scores 20+ points" (yardstick-v2 F087) is the
      team's record in the games he reached it, which ``record_when``
      answers with HIM as its player.
    - The question's own words name a child of the route's intent
      (:data:`_CHILD_GRAMMARS`, :data:`KIND_ASSIGNED_INTENTS`) - and only
      where the stages, run under that child
      (:func:`~association.query.router.settle`), leave it there: a count of
      games with no threshold in the text is a ranking, and stays the
      route's question.

    The router's reroutes are gone - a player's record against a team read
    as two teams meeting (#163), a pair filed with the second player as the
    ``opponent`` (F081, F142) or sent to ``with_without`` (F114), a
    comparison sent to ``player_stat``: measured over the 628 questions with
    a recorded normalizer reply, none fires on the parser's output, whose
    grammar names a player's own record, the pair relation and a comparison
    by the subject's kind (ROADMAP plan item 6, step (d), part 3).

    .. versionadded:: 5.0.0
    """
    if subject.kind in ("team", "team_players") and parent in _RECORD_WHEN_PARENTS and any(c.predicate == "reached" for c in subject.conditions):
        return "record_when", None
    if parent in KIND_ASSIGNED_INTENTS:
        return None
    for child, words, kinds, parents in _CHILD_GRAMMARS:
        match = words.search(question)
        if match is not None and parent in parents and subject.kind in kinds:
            return child, match.group(0)
    return None


def _named_before(question: str, players: tuple[str, ...], at: int) -> bool:
    """Whether a word of a subject player's name (three letters or more)
    appears in the question before position ``at``."""
    head = question[:at].casefold()
    return any(re.search(rf"\b{re.escape(word)}\b", head) for p in players for word in _words(p.casefold()) if len(word) >= 3)


def _evidence(named: list[str], routed: list[str], spellings: dict[str, str], invented: list[str], team_word: str | None, opponent: str | None) -> tuple[str, ...]:
    """What the reading rested on, one line per finding, for the trace."""
    return tuple(
        line
        for line in (
            f"question names players {named}" if named else "",
            f"router players the question supports {routed}" if routed else "",
            *(f"question spells {was!r} as {now!r}" for was, now in spellings.items()),
            f"router names the question never held {invented}" if invented else "",
            f"team word {team_word!r}" if team_word else "",
            f"against {opponent!r}" if opponent else "",
        )
        if line
    )


def _decide(
    players: tuple[str, ...], teams: list[str], position: str | None, opponent: str | None, own_team: str | None, companions: tuple[str, ...], evidence: tuple[str, ...], intent: str, question: str
) -> Subject:
    """The kind, from what was found - a player before a team, a team before
    the league, the way a named subject is always the narrower claim."""
    if len(players) >= 2:
        return Subject("pair", players, tuple(teams), position, opponent, own_team, companions, evidence)
    if len(players) == 1:
        return Subject("player", players, tuple(teams), position, opponent, own_team, companions, evidence)
    if position:
        return Subject("position", (), tuple(teams), position, opponent, own_team, companions, evidence)
    if teams and re.search(r"\b(?:player|players)\b", question, re.IGNORECASE) and not re.search(r"\bteam\b", question, re.IGNORECASE):
        return Subject("team_players", (), tuple(teams[:1]), position, opponent, own_team, companions, evidence)
    if len(teams) >= 2:
        return Subject("teams", (), tuple(teams[:2]), position, None, own_team, companions, evidence)
    if teams and opponent and intent == "head_to_head":
        return Subject("teams", (), (teams[0], opponent), position, None, own_team, companions, evidence)
    if teams:
        return Subject("team", (), tuple(teams), position, opponent, own_team, companions, evidence)
    return Subject("everyone", (), (), position, opponent, own_team, companions, evidence)


def apply_subject(subject: Subject, scope: Scope, *, intent: str) -> Applied:
    """Write what the subject reading settled into the typed scope a template
    reads - the parser's last step
    (:func:`~association.query.parse.reading_from_route`): the players the
    question is about, in the shape the route already uses (``player`` or
    ``players``); the one player a template that needs one was left
    without; a player's own team, and the tenure it implies; the
    companions' roles; and a team's record in the games a companion reached
    a line. A name the question never held is replaced by the question's
    own spare name where there is exactly one per name, else reported, and
    the caller refuses by name rather than answering about it (AGENTS.md:
    "when it cannot be repaired, say so - do not hand it to the agent").
    Returns the scope with the reading written in (``scope`` itself is
    never changed), the decisions made, the names dropped, and the intent
    the subject settled on (:attr:`Subject.intent`), with the scope
    rewritten for it where it differs.

    A kept name is respelled as the question's own span spells it
    (:func:`_spellings`), so a template reads "Jay Huff" and "Seth Curry"
    where a model wrote "Jaylen Huff" and "Stephen Curry". Every step here is
    one the parser's output reaches: measured over the 628 questions with a
    recorded normalizer reply (ROADMAP plan item 6, step (d), part 3c), the
    router-era repairs that never fired there - a player filed in ``team``,
    a team that displaced the player, a player or a team filed as the
    opponent, a team subject the router dropped - are gone, and so are the
    reroutes that never fired either (a player's record against a team, a
    pair filed through the opponent, a child the parser already settles).

    .. versionadded:: 4.4.0

    .. versionchanged:: 5.0.0
       Respells a kept name from the anchored span (a question's own typo,
       "Seph Curry", included) rather than from a whole-word match; writes
       the restored player, the own team and the companions' roles, which
       were ``entities.scope_from_question``'s; takes ``intent`` and returns
       :class:`Applied`, whose ``intent`` is a team's ``record_when`` where
       the reading settled on it. Takes and returns the typed
       :class:`~association.query.reading.Scope` (ROADMAP plan item 6, step
       (f)) in place of a slot dict it mutated.
    """
    scope, players, dropped = _apply_players(subject, scope, intent)
    if dropped:
        return Applied(scope, [], dropped, intent)
    decisions = list(players)
    scope, restored = _apply_restored_player(subject, scope, intent)
    decisions.extend(restored)
    scope, own = _apply_own_team(subject, scope, intent)
    decisions.extend(own)
    scope, conditions = _apply_conditions(subject, scope, intent)
    decisions.extend(conditions)
    scope, rewritten, settled = _apply_intent(subject, scope, intent)
    decisions.extend(rewritten)
    return Applied(scope, decisions, [], settled)


def _apply_team_record_when(subject: Subject, scope: Scope, intent: str) -> tuple[Scope, list[Decision], str]:
    """The scope for the TEAM's record in the games a companion reached a
    line - split out of :func:`_apply_intent` for the complexity gate; see
    :func:`child_named`'s record_when rule for the shape."""
    # Before the word-assigned children: this record_when is the TEAM's
    # question with a companion's line, not a player's "record when he
    # scored 30+" that the child grammar settles from the words.
    condition = next(c for c in subject.conditions if c.predicate == "reached")
    player = scope.player
    rewritten = Scope(
        season=scope.season,
        season_type=scope.season_type,
        span=scope.span,
        venue=scope.venue,
        # The router filed the subject's own team as the opponent ("sixers"
        # beside its invented Joel Embiid); the question sets the team
        # against nobody.
        opponent=scope.opponent if subject.opponent is not None else None,
        since=scope.since,
        until=scope.until,
        season_type_unstated=scope.season_type_unstated,
        player=condition.name,
        stat=condition.stat,
        threshold=condition.threshold,
        team=subject.teams[0] if subject.teams else None,
    )
    if intent != "record_when":
        return rewritten, [Decision("subject", "intent", intent, "record_when", _TEAM_RECORD_WHEN)], "record_when"
    # Already the team's record (the parser settles it, _decide_intent): a
    # decision only where the companion is not the player the route held -
    # the player read BEFORE the scope was rewritten, since after it is his
    # by construction.
    return rewritten, ([Decision("subject", "player", player, condition.name, _TEAM_RECORD_WHEN)] if player != condition.name else []), "record_when"


def _apply_intent(subject: Subject, scope: Scope, intent: str) -> tuple[Scope, list[Decision], str]:
    """Rewrite the scope for the team's record in the games a companion
    reached a line, where the reading settled on it
    (:func:`child_named`); returns the scope, the decisions and the
    intent the scope is now for. A child the question's words name is the
    parser's own step (:func:`~association.query.parse.read_route` settles
    the route under it), so it arrives here as the route's intent and
    nothing moves."""
    if subject.intent == "record_when" and subject.kind in ("team", "team_players") and any(c.predicate == "reached" for c in subject.conditions):
        return _apply_team_record_when(subject, scope, intent)
    return scope, [], intent


def _apply_restored_player(subject: Subject, scope: Scope, intent: str) -> tuple[Scope, list[Decision]]:
    """Put back the one player the question names where the router left the
    player out - only for a template that cannot answer without one
    (:data:`~association.query.reading.PLAYER_REQUIRED_INTENTS`:
    "Sga record 36 plus points" came back with no player at all) or where an
    empty slot has a real, different answer, the league
    (:data:`~association.query.reading.SUBJECT_RESTORABLE_INTENTS`:
    "kawhi most threes in a game" answered the league's single-game leaders,
    Kawhi Leonard's own 7 never mentioned - yardstick-v2 F093). Anywhere
    else an empty player slot means the league, and filling it would turn a
    league question into one about somebody the question happened to name.
    Only where the reading settled on exactly one player - subject or
    companion: "celtics record with 20+ points from jayson tatum" reads as
    the Celtics with Tatum as the condition, and ``record_when``'s player
    slot IS the condition player. "Best true shooting percentage" names
    Travis Best by whole word and nobody to the reading (an ordinary word),
    so nothing is restored there."""
    if intent not in PLAYER_REQUIRED_INTENTS | SUBJECT_RESTORABLE_INTENTS or scope.player or scope.players:
        return scope, []
    named = list(dict.fromkeys((*subject.players, *subject.companions)))
    if len(named) != 1:
        return scope, []
    return replace(scope, player=named[0]), [Decision("subject", "player", None, named[0], "from the question; the router left it out")]


def _apply_own_team(subject: Subject, scope: Scope, intent: str) -> tuple[Scope, list[Decision]]:
    """Put back a player's OWN team, where "for <team>" / "with the <team>"
    names one beside him and the router filed neither ``team`` nor
    ``opponent`` - yardstick-v2 F166, "lebron stats as a starter for Miami".
    Written to ``own_team``, never ``team``: a router-supplied ``team``
    beside an already-correct ``opponent`` is documented noise
    (``templates/games.py``'s ``_team_slot_for_player``), and a template that
    read ``team`` here would trust it - "lebron james 2 3 pointers all-time
    vs jazz on tuesdays" carries ``team='Los Angeles Lakers'`` beside a real
    ``opponent='Utah Jazz'``, and silently narrowed 9 meetings to 4 before
    ``own_team`` existed. Only for the templates whose relation narrows by
    it (:data:`~association.query.reading.OWN_TEAM_RESTORABLE_INTENTS`).

    A historical team names a TENURE, not "now": with no season named in
    the question (:attr:`Subject.named_season`, never the router's own
    "current season" default), ``span`` becomes "career" - "for Miami"
    fifteen years into a Lakers career is not asking about this season."""
    if intent not in OWN_TEAM_RESTORABLE_INTENTS or subject.own_team is None or not (scope.player or scope.players):
        return scope, []
    if scope.team or scope.opponent or scope.own_team:
        return scope, []
    out = replace(scope, own_team=subject.own_team)
    decisions = [Decision("subject", "own_team", None, subject.own_team, "from the question; the router left it out")]
    if subject.named_season is None and not scope.span:
        out = replace(out, span="career", season=None)
        decisions.append(Decision("subject", "span", None, "career", 'a team named with no season is a tenure, not "now"'))
    return out, decisions


def _apply_filler_players(subject: Subject, scope: Scope) -> tuple[Scope, list[Decision], list[str]]:
    """Take the router's no-names (:attr:`Subject.filler`) out of the player
    slots - "most", "most 30+ point games" (what the model files as the
    player for a ranking once nothing in its prompt shows one with none),
    or a team's word read as a player ("magic vs nets" as Magic Johnson) -
    rather than leave them for a template to resolve into "No player found
    matching 'most'". Returns the scope, the decisions and the names still
    routed."""
    routed = _routed_player_slots(scope)
    if not any(r in subject.filler for r in routed):
        return scope, [], routed
    kept = [r for r in routed if r not in subject.filler]
    field = "players" if scope.players else "player"
    decisions = [Decision("subject", field, r, None, "no player's name - a rank word, a phrase of the question, or a team's word") for r in routed if r in subject.filler]
    if field == "players":
        return replace(scope, players=tuple(kept)), decisions, kept
    return replace(scope, player=None), decisions, kept


def _spare_names(subject: Subject, kept: list[str], intent: str) -> list[str]:
    """The question's own names not yet in the slots, which replace a router
    invention one for one: the subject's players, and for ``record_when`` -
    whose player IS the condition's - a reached companion too, whether the
    router chose ``record_when`` or the reading settled it (the team's
    question with a companion's line, under an invented player)."""
    spare = [p for p in subject.players if not _same_person(p, kept)]
    if "record_when" in (intent, subject.intent):
        spare += [c.name for c in subject.conditions if c.predicate == "reached" and not _same_person(c.name, [*kept, *spare])]
    return spare


def _apply_players(subject: Subject, scope: Scope, intent: str = "") -> tuple[Scope, list[Decision], list[str]]:
    """The ``player``/``players`` half of :func:`apply_subject`. For
    ``record_when``, whose player IS the condition's ("sixers record when
    maxey scored 20+"), a reached companion is a spare name the way the
    subject's own are: the router invented Joel Embiid there (day5) and
    the question's Maxey replaces him rather than the question refusing."""
    scope, decisions, routed = _apply_filler_players(subject, scope)
    if not routed:
        return scope, decisions, []
    dropped = [r for r in routed if r in subject.invented]
    kept = [r for r in routed if r not in dropped]
    spare = _spare_names(subject, kept, intent)
    if dropped and len(spare) != len(dropped):
        return scope, [], dropped
    field = "players" if scope.players else "player"
    replacement = dict(zip(dropped, spare, strict=True)) if dropped else {}  # a spare name with nothing dropped is a player the router omitted: not put back here
    decisions.extend(Decision("subject", field, was, now, "the question never names the router's player; it names this one") for was, now in replacement.items())
    respelled = _respellings(kept, subject.players)
    replacement.update(respelled)
    decisions.extend(Decision("subject", field, was, now, "spelled as the question names the player") for was, now in respelled.items())
    new = [replacement.get(r, r) for r in routed]
    if new == routed:
        return scope, decisions, []
    if field == "players":
        return replace(scope, players=tuple(new)), decisions, []
    return replace(scope, player=new[0]), decisions, []


def _apply_conditions(subject: Subject, scope: Scope, intent: str) -> tuple[Scope, list[Decision]]:
    """Write the companions' roles the router's own slots cannot carry - a
    start, the bench, a line reached ("when Embiid starts", "in games Maxey
    had 20+ points") - as the ``conditions`` the relation reads
    (:class:`~association.query.reading.ConditionSpec`), whatever the
    intent - the relation templates read them as a filter, ``with_without``
    as the split's own side ("record when Embiid and Paul George start":
    started against not), and an answer that cannot honor them refuses by
    name (``check_scope``, the planner). Until 5.0.0's last change they
    were written only where the answering template honored them, a reader
    that asked what would answer before it wrote (``ROADMAP.md``, Phase 1):
    "how many times did the 76ers beat boston when embiid started"
    answered every meeting, the start gone without a word, and "plot
    jokic's fingerprint in games murray started" drew the season. A played companion ("when
    Draymond plays", "with Draymond playing") is a condition too, on the
    relation templates: ``with_player`` is read by ``with_without`` alone,
    so on a game log or a shot chart it narrowed nothing and nothing
    refused it. ``with_without`` keeps reading its ``with_player`` (the
    router's stage reads it from the same words), and an absence stays the
    router's ``without`` ("with Draymond out" included) - ROADMAP plan item
    3, steps B and C. The compiler narrows its relation by every
    condition, so "how many 30 point games did maxey have when embiid
    started" counts his games with Embiid starting (34) - with nothing
    written it counted all of them (86), the start gone without a word."""
    own = [name for name in [scope.player, *scope.players] if isinstance(name, str)]
    held = [c.player for c in scope.conditions]
    roles = ("started", "bench", "reached") if intent == "with_without" else ("started", "bench", "reached", "played")
    written = [
        {"player": c.name, "side": c.side, "predicate": c.predicate, **({"stat": c.stat, "threshold": c.threshold} if c.predicate == "reached" else {})}
        for c in subject.conditions
        if c.predicate in roles and not _same_person(c.name, own) and not _same_person(c.name, held) and (c.side == "own" or intent != "with_without")
    ]
    if not written:
        return scope, []
    # The decision records the roles in the slot shape the trace has always
    # printed; the scope holds them typed - beside any the route already
    # carries (an opponent-side player, parse.read_route's own writing).
    return replace(scope, conditions=(*scope.conditions, *(ConditionSpec.from_slot(w) for w in written))), [
        Decision("subject", "conditions", None, written, "the role the question gives each player named beside the subject")
    ]


def _routed_player_slots(scope: Scope) -> list[str]:
    """The router's player names, from ``player`` or ``players``."""
    return [p for p in ([scope.player] if scope.player else []) + list(scope.players) if isinstance(p, str) and p.strip()]


def _same_person(name: str, others: tuple[str, ...] | list[str]) -> bool:
    """Whether ``name`` and one of ``others`` support each other - the same
    person under two spellings (a surname and its completion)."""
    return any(question_supports(name, o) or question_supports(o, name) for o in others)


def _respellings(kept: list[str], players: tuple[str, ...]) -> dict[str, str]:
    """A kept router name the subject spells differently - a bare "Jokic"
    the router left bare, "Jaylen Tatum" for the question's "tatum", "Seph
    Curry" resolved to Seth where the router wrote Stephen - mapped to the
    subject's spelling of the same person (:func:`_spellings`)."""
    out: dict[str, str] = {}
    for r in kept:
        own = next((p for p in players if _same_person(r, (p,)) and p != r), None)
        if own is not None:
            out[r] = own
    return out
