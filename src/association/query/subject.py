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
from dataclasses import dataclass, fields, replace
from importlib import resources
from typing import TYPE_CHECKING, Any, NamedTuple, get_args

import duckdb
from rapidfuzz.distance import DamerauLevenshtein

from association.query import lexicon, names, reading
from association.query.decisions import Decision
from association.query.entities import (
    _ERA_ALIASES,
    _TEAM_NICKNAMES,
    PLAYER_NICKNAMES,
    Entity,
    _edit_budget,
    _exact_name_span,
    _fold,
    _fuzzy_name_span,
    _initials,
    _run_together,
    _suggest_players_by_spelling,
    _team_named,
    _words,
    find_players,
    find_teams,
    players_of,
    team_abbreviations,
    team_columns,
    teammate_names,
    teams_named_by_word,
    teams_of,
)
from association.query.lexicon import season_from_text, season_named
from association.query.measures import THRESHOLD_STAT_NAMES
from association.query.reading import (
    OWN_TEAM_RESTORABLE_INTENTS,
    PLAYER_REQUIRED_INTENTS,
    SUBJECT_RESTORABLE_INTENTS,
    Claim,
    Companion,
    Cuts,
    LeftOut,
    Line,
    Measure,
    Predicate,
    Scope,
    SubjectKind,
)
from association.query.span import needed

if TYPE_CHECKING:
    import re

#: The kinds a subject can be (:data:`~association.query.reading.SubjectKind`).
#: ``team_players`` is "a Hawks player" - the team's players as a group,
#: which the compiler's team-where-a-player-belongs read answers ("thunder
#: all-time triple doubles", by player).
SUBJECT_KINDS: frozenset[str] = frozenset(get_args(SubjectKind))
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
    # "per game" is an average, never one game; "single game" is one game
    # with an article or without (the lexicon's CHILD_SINGLE_GAME_HIGH).
    ("single_game_high", lexicon.CHILD_SINGLE_GAME_HIGH, _NOT_A_TEAM, _PLAYER_RELATION_PARENTS),
    ("shot_distance", lexicon.CHILD_SHOT_DISTANCE, _PLAYER_OR_PAIR, _PLAYER_RELATION_PARENTS | {"shot_chart"}),
    ("streak", lexicon.CHILD_STREAK, SUBJECT_KINDS, frozenset({"team_record", "team_stat", "team_leaderboard", "team_outlook", "head_to_head"}) | _PLAYER_RELATION_PARENTS),
    ("record_when", lexicon.CHILD_RECORD_WHEN_LINE, SUBJECT_KINDS, frozenset({"team_record", "team_stat", "with_without", "head_to_head"}) | _PLAYER_RELATION_PARENTS),
    # A player's games won or lost: his team's record in the games he
    # played, which is record_when's read with no threshold ("how many
    # playoff games has embiid won?" - answered right today only because
    # the router misfiles it there). A player only: a TEAM's games won are
    # team_record's own question.
    (
        "record_when",
        lexicon.CHILD_RECORD_WHEN_GAMES_WON,
        frozenset({"player"}),
        frozenset({"team_record", "team_stat", "team_outlook", "with_without", "head_to_head"}) | _PLAYER_RELATION_PARENTS,
    ),
    ("threshold_count", lexicon.CHILD_THRESHOLD_COUNT, _NOT_A_TEAM, _PLAYER_RELATION_PARENTS),
    ("player_history", lexicon.CHILD_PLAYER_HISTORY, _PLAYER_OR_PAIR, _PLAYER_RELATION_PARENTS),
    (
        "player_splits",
        lexicon.CHILD_PLAYER_SPLITS,
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
class Subject(reading.Subject):
    """Who a question is about, as the subject reading read it: the typed
    subject (:class:`~association.query.reading.Subject` - the kind, the
    players, the teams, the position group) and what only the parser's
    settling needs beside it - the opponent and the player's own team the
    cuts tagger takes, the companions, the evidence, the names the model
    invented or filed as filler, the season named, the intent and why, the
    characters each finding was read from.

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

    .. versionchanged:: 6.0.0
       A :class:`~association.query.reading.Subject` - the typed value the
       Scope carries (Phase 3, step 2) - with the parser's fields beside it:
       ``kind``, ``players``, ``teams`` and ``position`` are the typed
       subject's, as read.
    """

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
    #: Every companion with the role the question states
    #: (:class:`~association.query.reading.Companion`); ``companions`` above
    #: is their names.
    conditions: tuple[Companion, ...] = ()
    #: The characters each companion was read from - the keyword, the
    #: names and the role's words, a reached one's line included
    #: (:class:`~association.query.reading.Claim`, ``what`` ``"companion"``),
    #: which ride the Route onto ``Reading.claims``. The subject's own names
    #: are the subject slice's to claim.
    #:
    #: .. versionadded:: 6.0.0
    claims: tuple[Claim, ...] = ()

    def projected(self) -> dict[str, Any]:
        """Every field as a Subject was recorded until Phase 3, step 2
        (:func:`~association.query.stages.plain`): each companion as the
        five-field tuple it was (name, predicate, stat, threshold, side),
        and the claims - positions in the text, as the Reading's own claims
        are - left out, so a reading recorded before the companions were
        typed compares identical to one recorded after. The typed
        companions are recorded beside the reading (``stages._reading_record``,
        ``companions``), never here.

        .. versionadded:: 6.0.0
        """
        out = {f.name: getattr(self, f.name) for f in fields(self) if f.name != "claims"}
        out["conditions"] = [[c.player, c.predicate, c.line.measure if c.line is not None else None, c.line.value if c.line is not None else None, c.side] for c in self.conditions]
        return out

    #: The router's player names that are no name at all - a rank word or
    #: a phrase of the question ("most", "most 30+ point games";
    #: :func:`_not_a_name`), or a name the question holds only by a TEAM's
    #: word ("magic vs nets" is not Magic Johnson;
    #: :func:`~association.query.entities._named_only_by_a_team_word`) -
    #: which :func:`apply_subject` takes out of the slots rather than leaves
    #: for a template to resolve ("No player found matching 'most'").
    filler: tuple[str, ...] = ()


# The companions' words - the phrases a companion is named in ("without X",
# "with X", "when X", "in games X started", the words after "vs"), what ends
# a name, the absence, start and bench words, a compare verb's "with" that
# names no companion - are the lexicon's since Phase 3, step 2 (COMPANION,
# COMPANION_IN_GAMES, COMPARED_WITH, VERSUS_PHRASE, NAME_STOPWORDS,
# ABSENCE_WORDS, CONDITION_STARTED, CONDITION_BENCH, CONDITION_ABSENT, the
# line's THRESHOLD), each with its reason beside it; the companion itself is
# the reading's typed value (reading.Companion, with a reached one's Line).


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
    return frozenset(words.split()) | lexicon.MONTH_ABBREVIATIONS


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


def _teams_by_word(teams: names.TeamIndex, question: str) -> set[str]:
    """Every team a word of the question names by itself - a singular, a
    one-word spelling, a nickname or a whole word of exactly one team's
    name (:func:`team_named_in`, word by word)."""
    found: set[str] = set()
    for run in lexicon.TEAM_SPELLING_RUN.findall(question.lower()):
        team = lexicon.TEAM_SINGULARS.get(run.removesuffix("'s").rstrip("'")) or team_named_in(teams, run)
        if team is not None:
            found.add(team)
    return found


def _team_word(con: duckdb.DuckDBPyConnection, question: str) -> str | None:
    """The team the question names as a word: a singular or one-word
    nickname first, then :func:`~association.query.entities.team_named_in`
    (the roster table by whole word, the plural nicknames), then an
    abbreviation."""
    for w in _words(question.lower()):
        if w in lexicon.TEAM_SINGULARS:
            return lexicon.TEAM_SINGULARS[w]
    return team_named_in(teams_of(con), question) or _team_abbreviation(con, question)


def _team_words(text: str) -> list[str]:
    """``text``'s words as a team's name is read from them: the letter runs
    every name is split into (:func:`~association.query.entities._words`),
    except a word a team is spelled by with a digit in it ("76ers",
    :data:`~association.query.lexicon.TEAM_SINGULARS`), kept whole where the
    split would leave "ers"."""
    words: list[str] = []
    for run in lexicon.WORD_RUN.finditer(_fold(text)):
        token = run.group(0)
        words.extend([token] if token.casefold() in lexicon.TEAM_SINGULARS else _words(token))
    return words


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
    for name in dict.fromkeys(players_named_in(players_of(con), question)):
        matched = [w for w in (x.casefold() for x in _words(name)) if w in q_words]
        if not matched:
            kept.append(name)  # a nickname key the question used: "kat", "sga"
            continue
        if teams_here and any(len(w) >= 4 and (lexicon.TEAM_SINGULARS.get(w) or team_named_in(teams_of(con), w)) in teams_here for w in matched):
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
    of that word. A word after the "vs" that is a player's name the question
    names is the player, not a team it clips: "kevin garnett vs tim duncan
    games" read "tim" as the Timberwolves (:func:`_team_after_versus`)."""
    versus = _team_after_versus(teams_of(con), question, season, names=players_named_in(players_of(con), question))
    if versus is not None:
        return versus.name
    held = scope.cuts.opponent
    held_team = _team_named(teams_of(con), held, season) if isinstance(held, str) and held.strip() else None
    if held_team is not None and _team_grounded(teams_of(con), question, held_team) and not _named_as_own(con, question, held_team, season):
        return held_team.name
    # "karl towns stats vs netslast 5 games": the "vs" names a word nothing
    # resolves, and the router read it as a team it filed in `team` - a team
    # the question never holds otherwise, so the router's reading of that
    # word is the one there is.
    team = _team_named(teams_of(con), scope.subject.team, season) if held_team is None else None
    if team is not None and lexicon.AGAINST_PHRASE.search(question) and not _team_grounded(teams_of(con), question, team):
        return team.name
    return None


def _named_as_own(con: duckdb.DuckDBPyConnection, question: str, team: Entity, season: int | None) -> bool:
    """Whether ``team`` is the one the question names after "for" - "show me
    splits for the sixers when maxey scores 20+ points" arrived with the
    76ers as the ``opponent`` beside an invented Joel Embiid (day5), and the
    reading took the router's word for it, leaving the question with no
    team as its subject. A "for <team>" with no "vs" anywhere is the
    subject's side, never the other one."""
    own = _team_after_for(teams_of(con), question, season)
    return own is not None and own[0].id == team.id and not lexicon.AGAINST_PHRASE.search(question)


def _opponent_player(con: duckdb.DuckDBPyConnection, scope: Scope) -> str | None:
    """A route's ``opponent`` when it names a player and not a team ("jay
    huff game log vs Embiid" arrived from the router with Jokic there): a
    name to check against the question like the subject's own, never read
    as a team the subject is set against. A team opponent is a narrowing,
    not a subject."""
    opponent = scope.cuts.opponent
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
    return tuple(p for p in _routed_player_slots(scope) if _not_a_name(p) or (not _is_a_team(con, p) and _named_only_by_a_team_word(teams_of(con), question, p)))


def _not_a_name(text: str) -> bool:
    """A router ``player`` that is no name at all: a position phrase ("shooting
    guard" on "highest 3 point percentage ... by a shooting guard", F056 - the
    position-group subject, which :attr:`Subject.position` carries) or the
    router's filler word ("player" on "Most points in 15th season played",
    F099). Neither is a player to read, replace or report."""
    stripped = text.strip()
    if lexicon.NO_NAME_HAS.search(stripped):
        # "most 30+ point games", "most", "Most Player in 15th Season Played"
        # - what the model files as the player once nothing in its prompt
        # shows a count or a ranking with none (the 5.0.0 prompt shrink).
        # No player's name holds a digit, a plus sign, a rank word or the
        # word "player"; a phrase the question literally contains is not a
        # name for holding it.
        return True
    return stripped.lower() in lexicon.FILLER_PLAYER_WORDS or any(pattern.fullmatch(stripped) for pattern, _ in lexicon.POSITION_WORDS)


def _team_slot_player(con: duckdb.DuckDBPyConnection, scope: Scope) -> str | None:
    """The router's ``team`` when it names a player and no team: "Podziemski
    game log without curry" arrived as ``team='Podziemski'``, and "Will
    Riley last 5 game s" as ``team='Riley'`` (a bare fragment, three
    players) - the subject, in the wrong slot."""
    team = scope.subject.team
    if team is None or _team_named(teams_of(con), team) is not None or not find_players(con, team):
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
        derived = question_derived_player(players_of(con), question, name)
        if derived is not None and derived.name != name:
            out[name] = derived.name
    return out


def _conditions(question: str, players: tuple[str, ...], scope: Scope) -> tuple[Companion, ...]:
    """The players whose ROLE the question states beside the subject -
    "without X", "with X on the floor", "when X scored 20+" - each with that
    role (:class:`~association.query.reading.Companion`), read from the
    question's own words, never from the router's ``without`` slot (which
    filed Embiid under it on "Embiid's record against Boston"). The
    companions a replayed route carries are consulted only for one the
    question misspells ("without wembyanama"), where the phrase is a near
    spelling of the carried name. One phrase, one role: "when Embiid and
    Paul George start" is two ``started`` companions.

    .. versionchanged:: 5.0.0
       Returns :class:`Companion` tuples with the predicate, not names; a
       player after a versus word is an opponent-side ``played`` companion
       where the words ask for the subject's games (:func:`_versus_companions`).

    .. versionchanged:: 6.0.0
       The typed :class:`~association.query.reading.Companion`, a reached
       one carrying its :class:`~association.query.reading.Line`;
       :func:`_conditions_read` returns the claims beside them.
    """
    return _conditions_read(question, players, scope)[0]


def _conditions_read(question: str, players: tuple[str, ...], scope: Scope) -> tuple[tuple[Companion, ...], tuple[Claim, ...]]:
    """:func:`_conditions`, with the characters each companion was read
    from: the keyword, the names and the role's words ("without Tatum and
    Brown", "when maxey scores 20+ points", "vs lebron") - what the subject
    reading claims (contract 2), and inside which the lines tagger reads no
    line.

    .. versionadded:: 6.0.0
    """
    found: list[Companion] = []
    # A compare verb that owns a "with" ("contrast luka with sga") reads that
    # "with" as joining two subjects, never as a companion's phrase: its
    # words are read here (span.needed keeps the ones the reading needs).
    claims: list[Claim] = [Claim(m.start(), m.end(), "companion") for m in lexicon.COMPARED_WITH.finditer(question)]
    for match in _companion_phrases(question):
        word, text = match.group(1).lower(), match.group(2)
        predicate, line, role_end = _condition_role(word, text)
        names = _companion_names(text, players, scope, predicate)
        new = [Companion(player=name, predicate=predicate, line=line) for name in names if not any(_same_person(name, [c.player]) for c in found)]
        if new:
            found.extend(new)
            claims.append(Claim(match.start(), match.start(2) + _read_to(text, [c.player for c in new], role_end), "companion"))
    versus, versus_claims = _versus_companions(question, players, scope, found)
    found.extend(versus)
    claims.extend(versus_claims)
    return tuple(found), tuple(claims)


def _read_to(text: str, names: list[str], role_end: int | None) -> int:
    """How far into a companion phrase's words the reading reached: past the
    last of the names it read (as the text spells each) and the role's
    words, or the whole text where a name is not spelled in it (a carried
    name the phrase misspells)."""
    low = text.casefold()
    ends = [role_end or 0]
    for name in names:
        at = low.find(name.casefold())
        if at < 0:
            return len(text.rstrip())
        ends.append(at + len(name))
    return max(ends)


def _companion_phrases(question: str) -> list[re.Match[str]]:
    """The companion phrases of ``question`` (:data:`~association.query.lexicon.COMPANION`),
    minus a "with" that a compare verb owns - "compare luka with sga" names
    two subjects and no companion (:data:`~association.query.lexicon.COMPARED_WITH`)
    - and the "in games X started" ones (:data:`~association.query.lexicon.COMPANION_IN_GAMES`)
    no keyword phrase already covers, in the order the question gives them."""
    compared = [m.span() for m in lexicon.COMPARED_WITH.finditer(question)]
    phrases = [m for m in lexicon.COMPANION.finditer(question) if not any(a <= m.start() < b for a, b in compared)]
    in_games = [m for m in lexicon.COMPANION_IN_GAMES.finditer(question) if not any(p.start() < m.end() and m.start() < p.end() for p in phrases)]
    return sorted([*phrases, *in_games], key=lambda m: m.start())


def _name_segments(text: str) -> list[str]:
    """The names a companion phrase holds BY POSITION, as typed and in
    order: the words after the keyword up to one that cannot be part of a
    name (:data:`~association.query.lexicon.NAME_STOPWORDS`, a number),
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
    for piece in lexicon.NAME_PIECES.findall(text):
        lowered = piece.casefold()
        if lowered in lexicon.NAME_JOINERS:
            if not words:
                break
            names.append(" ".join(words))
            words = []
            continue
        if not lexicon.NAME_SHAPED.fullmatch(piece) or lowered in lexicon.NAME_STOPWORDS or len(words) >= lexicon.MAX_NAME_WORDS:
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
    routed = [c.player for c in scope.companions]
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


# A player after a versus word (lexicon.VERSUS_PHRASE) is on the OTHER side
# of the subject's games - a condition (ROADMAP step 3: "most points by
# curry vs lebron", "how many times did lebron score 30 vs kawhi"), never a
# second subject - wherever the words ask for the subject's GAMES rather
# than the pair's summary (lexicon.GAMES_NOT_SUMMARY, and the child
# grammars' words). A bare "curry vs lebron" or "curry stats vs lebron"
# stays the pair, whose matchup summary reads both lines.


def _asks_for_games(question: str) -> bool:
    """Whether the words ask for the subject's games - a log, a high or a
    low, or any child grammar's shape (a count, a streak, a history, splits,
    a record over a line) - rather than the pair's matchup summary."""
    return lexicon.GAMES_NOT_SUMMARY.search(question) is not None or any(words.search(question) for _, words, _, _ in _CHILD_GRAMMARS)


def _versus_companions(question: str, players: tuple[str, ...], scope: Scope, found: list[Companion]) -> tuple[list[Companion], list[Claim]]:
    """The players after a versus word, as opponent-side ``played``
    companions - only where a player is named BEFORE the phrase (the
    subject; "celtics vs lebron" names no subject beside him) and the words
    ask for his games (:func:`_asks_for_games`). Any number: "giannis
    points vs lebron and curry" is the games both played against him. A
    team after "vs" names nobody here (it is the opponent slot's). Returns
    the companions and the characters each was read from (the versus word
    and the names)."""
    # A pair's summary reads two lines; three names or more are a subject
    # and conditions whatever the words ask - and so is a scope a replayed
    # route already carries them in.
    carried = any(c.side == "opponent" for c in scope.companions)
    subjects = [p for p in players if not any(_same_person(p, [c.player]) for c in found)]
    if not _asks_for_games(question) and len(subjects) <= 2 and not carried:
        return [], []
    versus: list[Companion] = []
    claims: list[Claim] = []
    for match in lexicon.VERSUS_PHRASE.finditer(question):
        if not _named_before(question, players, match.start()):
            continue
        names = [n for n in _companion_names(match.group(2), players, scope) if not any(_same_person(n, [c.player]) for c in [*found, *versus])]
        new = [Companion(player=name, predicate="played", side="opponent") for name in names if not _named_before(question, (name,), match.start())]
        if new:
            versus.extend(new)
            claims.append(Claim(match.start(), match.start(2) + _read_to(match.group(2), [c.player for c in new], None), "companion"))
    return versus, claims


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
    routed = [c.player for c in scope.companions]
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
        if any(len(word) < 3 or word in lexicon.THRESHOLD_WORDS or word in lexicon.TEAM_SINGULARS or team_named_in(teams_of(con), word) for word in span):
            continue
        ordinary = any(word in dictionary for word in span)
        if ordinary and size < len(words) and not absent:
            continue
        if _exact_name_span(players_of(con), span, limit=1) or _unrouted_near_spelling(con, span):
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
    return _team_named(teams_of(con), text) is None and len(_suggest_players_by_spelling(con, span)) == 1


def with_without_named(scope: Scope) -> tuple[list[str], bool, dict[str, tuple[str, tuple[str, int] | None]]]:
    """The teammates a with/without split divides by, read off the scope's
    typed companions (:class:`~association.query.reading.Companion`): the
    ones absent, else the ones who played, else the ones a role is stated
    for - whether it asked "without", and each name's role (``started``,
    ``bench``, or ``reached`` with its column and threshold). Read by the
    split's default point (:func:`association.query.point.default_point`)
    and by the relation's read of the split.

    .. versionadded:: 5.0.0
       On the reader's side (``templates.splits._with_without_named`` was this).

    .. versionchanged:: 6.0.0
       Reads the typed companions; the ``without``, ``with_player`` and
       ``conditions`` slots are gone.
    """
    # Lists, as the slots always were: teammate_names reads a list or one
    # bare name, and a tuple would be neither - every teammate dropped.
    mate_texts = teammate_names([c.player for c in scope.companions if c.absent])
    asked_without = bool(mate_texts)
    if not asked_without:
        mate_texts = teammate_names([c.player for c in scope.companions if c.side == "own" and c.predicate == "played"])
    roles = _with_without_roles(scope.companions)
    if not mate_texts and roles:
        mate_texts = list(roles)
    return mate_texts, asked_without, roles


def _with_without_roles(companions: tuple[Companion, ...]) -> dict[str, tuple[str, tuple[str, int] | None]]:
    """The role each companion gives its player - ``started``, ``bench``,
    ``reached`` (with its column and threshold) - by the name as written,
    for the split's read to pair with the resolved teammates. A
    ``played``/``absent`` companion adds nothing the names do not already
    say. A line's column is its stat's own name
    (:data:`~association.query.measures.THRESHOLD_STAT_NAMES`; the box
    score's column is named the same).
    """
    roles: dict[str, tuple[str, tuple[str, int] | None]] = {}
    for each in companions:
        if each.predicate in ("started", "bench"):
            roles[each.player] = (each.predicate, None)
        elif (
            each.predicate == "reached"
            and each.line is not None
            and each.line.measure is not None
            and isinstance(each.line.value, int)
            and each.line.value >= 1
            and each.line.measure in THRESHOLD_STAT_NAMES
        ):
            roles[each.player] = ("reached", (each.line.measure, each.line.value))
    return roles


def _condition_role(word: str, text: str) -> tuple[Predicate, Line | None, int | None]:
    """The predicate a companion phrase states, from its keyword and its
    words - a line reached wins ("scores 20+ points" is ``reached`` whatever
    the keyword, and so is "scores 30", a line on points), then a start,
    the bench, an absence ("without", "out", "injured"), else ``played``
    ("with", "when ... play") - with the reached one's
    :class:`~association.query.reading.Line` and how far into the text the
    role's words reach (None where the keyword alone said it)."""
    pairs = lexicon.threshold_pairs(text)
    if pairs:
        pair = pairs[0]
        return "reached", Line(measure=lexicon.THRESHOLD_WORDS[pair.group(2).lower()], value=int(pair.group(1)), as_typed=pair.group(0).casefold()), pair.end()
    scored = lexicon.scored_threshold(text)
    if scored is not None:
        return "reached", Line(measure="points", value=int(scored.group(1)), as_typed=scored.group(0).casefold()), scored.end()
    started = lexicon.CONDITION_STARTED.search(text)
    if started is not None:
        return "started", None, started.end()
    bench = lexicon.CONDITION_BENCH.search(text)
    if bench is not None:
        return "bench", None, bench.end()
    absent = lexicon.CONDITION_ABSENT.search(text)
    if word in ("without", "excluding") or absent is not None:
        return "absent", None, absent.end() if absent is not None else None
    return "played", None, None


def _team_names(con: duckdb.DuckDBPyConnection, question: str, scope: Scope, team_word: str | None, opponent: str | None, own_team: str | None) -> list[str]:
    """The team(s) the question is about: the word it names first, then the
    router's ``team`` / ``teams`` where the question supports them and they
    are teams - never the opponent or the player's own team over again."""
    names: list[str] = []
    if team_word and team_word not in (opponent, own_team):
        names.append(team_word)
    routed_team = scope.subject.team
    if (
        isinstance(routed_team, str)
        and routed_team
        and not names
        and question_supports(routed_team, question)
        and _team_named(teams_of(con), routed_team) is not None  # "any_team" is the router's placeholder, not a team
        and not any(t and (question_supports(routed_team, t) or question_supports(t, routed_team)) for t in (opponent, own_team))
    ):
        names.append(routed_team)
    for t in _listed_teams(scope):
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
    season = scope.span.season if scope.span.season is not None else season_from_text(question)
    own = _team_after_for(teams_of(con), question, season)
    team_word = _team_word(con, question)
    position = next((code for pattern, code in lexicon.POSITION_WORDS if pattern.search(question)), None)
    routed_opponent = _opponent_player(con, scope)
    opponent = _read_opponent(con, question, scope, season) if routed_opponent is None else None
    # Every team the words name, not only the first: "how many times did the
    # 76ers play boston" names the Celtics by "boston" beside the 76ers, and
    # "boston" is no player there (Brandon Boston Jr.).
    teams_here = {t for t in (opponent, own[0].name if own is not None else None, team_word) if t} | _teams_by_word(teams_of(con), question)

    routed, invented = _routed_names(con, scope, question, routed_opponent)
    filler = _filler_names(con, scope, question)
    routed = [p for p in routed if p not in filler]
    named = _question_players(con, question, routed, teams_here)
    spellings = _spellings(con, question, routed)
    players = _merge_names([spellings.get(r, r) for r in routed], named)
    unrouted = _unrouted_companions(con, question, players, scope)
    conditions, claims = _read_subject_conditions(question, (*players, *unrouted), scope)
    companions = tuple(dict.fromkeys(c.player for c in conditions))
    players = tuple(p for p in players if p not in companions)
    if _read_subject_alone(players, teams_here, conditions, scope):
        players, conditions, companions, claims = companions, (), (), ()
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
    decided = _decide(players, teams, position, opponent, own_team, companions, evidence, intent, question)
    subject = replace(decided, conditions=conditions, claims=(*claims, *_decide_claims(decided, question)))
    # Read, not decided: the intent the subject's shape settles is the
    # parser's step (:func:`child_named`, with the stages run once under
    # it), and :func:`settle_subject` writes it here. Until 5.0.0's last
    # change this ran the stages under each child the words named, before
    # the parser ran them at all.
    return replace(subject, invented=tuple(invented), named_season=season_from_text(question), intent=intent, filler=filler, claims=(*subject.claims, *_read_subject_season_claims(question)))


def _read_subject_conditions(question: str, beside: tuple[str, ...], scope: Scope) -> tuple[tuple[Companion, ...], tuple[Claim, ...]]:
    """The companions beside the subject (:func:`_conditions_read`), each
    phrase's claim cut to the words the companion reader needed
    (:func:`~association.query.span.needed`): "play" in "when Embiid and
    Paul George play" reads the same companions without it. A word outside
    every phrase that the companions turn on is claimed beside them."""
    conditions, claims = _conditions_read(question, beside, scope)
    return conditions, needed(question, claims, lambda probed: _conditions_read(probed, beside, scope)[0], outside="companion", gives_up=True)


def _read_subject_season_claims(question: str) -> tuple[Claim, ...]:
    """The words of the season the names are settled in, where the words
    name one (:func:`~association.query.lexicon.season_named`): read here -
    the span tagger reads them again, and its claim holds the same
    characters; a coach's question runs no tagger."""
    named_season = season_named(question)
    return () if named_season is None else needed(question, [Claim(start, end, "season") for start, end in named_season[1]], season_from_text)


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
    named_team = teams or scope.subject.teams
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
    # A whole word of the head: the runs of word characters the head holds
    # (a name's words are letters alone, so a word standing whole in the
    # head is one of its runs).
    head = {run.group(0) for run in lexicon.WORD_RUN.finditer(question[:at].casefold())}
    return any(word in head for p in players for word in _words(p.casefold()) if len(word) >= 3)


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
    if teams and lexicon.PLAYER_NOUN.search(question) and not lexicon.TEAM_NOUN.search(question):
        return Subject("team_players", (), tuple(teams[:1]), position, opponent, own_team, companions, evidence)
    if len(teams) >= 2:
        return Subject("teams", (), tuple(teams[:2]), position, None, own_team, companions, evidence)
    if teams and opponent and intent == "head_to_head":
        return Subject("teams", (), (teams[0], opponent), position, None, own_team, companions, evidence)
    if teams:
        return Subject("team", (), tuple(teams), position, opponent, own_team, companions, evidence)
    return Subject("everyone", (), (), position, opponent, own_team, companions, evidence)


def _decide_claims(subject: Subject, question: str) -> tuple[Claim, ...]:
    """The words :func:`_decide` read the subject's kind from beside the
    names (which :func:`subject_claims` claims): the "player" of "a Hawks
    player", which makes the team's players the subject."""
    noun = lexicon.PLAYER_NOUN.search(question) if subject.kind == "team_players" else None
    return () if noun is None else (Claim(noun.start(), noun.end(), "subject"),)


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
        # A name the question never held is refused by it; the companions
        # the words DO hold are still written, as the parser wrote the
        # other side's before Phase 3, step 2 ("jay huff game log vs
        # Embiid", the model's Jokic beside it).
        scope, _ = _apply_companions(subject, scope, intent)
        return Applied(_apply_who(subject, scope), [], dropped, intent)
    decisions = list(players)
    scope, restored = _apply_restored_player(subject, scope, intent)
    decisions.extend(restored)
    scope, own = _apply_own_team(subject, scope, intent)
    decisions.extend(own)
    scope, conditions = _apply_companions(subject, scope, intent)
    decisions.extend(conditions)
    scope, rewritten, settled = _apply_intent(subject, scope, intent)
    decisions.extend(rewritten)
    return Applied(_apply_who(subject, scope), decisions, [], settled)


def _apply_who(subject: Subject, scope: Scope) -> Scope:
    """The scope's typed subject as the reading settled it: its names as
    the steps above wrote them, with the reading's kind and position group
    (:class:`~association.query.reading.Subject`) - the one value the point
    reader and the relations read who the question is about from."""
    return replace(scope, subject=replace(scope.subject, kind=subject.kind, position=subject.position))


def _apply_team_record_when(subject: Subject, scope: Scope, intent: str) -> tuple[Scope, list[Decision], str]:
    """The scope for the TEAM's record in the games a companion reached a
    line - split out of :func:`_apply_intent` for the complexity gate; see
    :func:`child_named`'s record_when rule for the shape."""
    # Before the word-assigned children: this record_when is the TEAM's
    # question with a companion's line, not a player's "record when he
    # scored 30+" that the child grammar settles from the words.
    condition = next(c for c in subject.conditions if c.predicate == "reached")
    player = scope.subject.player
    rewritten = Scope(
        span=scope.span,
        # The venue stands; the router filed the subject's own team as the
        # opponent ("sixers" beside its invented Joel Embiid), and the
        # question sets the team against nobody.
        cuts=Cuts(venue=scope.cuts.venue, opponent=scope.cuts.opponent if subject.opponent is not None else None),
        # The companion is the player the record is keyed on, beside the
        # team it is the record of.
        subject=reading.Subject(kind=subject.kind, players=(condition.player,), teams=subject.teams[:1], position=subject.position),
        measure=Measure.from_slots({"stat": condition.line.measure}) if condition.line is not None else None,
        # The companion's line is the record's own now: the shape is keyed on it.
        lines=(replace(condition.line, keyed=True),) if condition.line is not None else (),
    )
    if intent != "record_when":
        return rewritten, [Decision("subject", "intent", intent, "record_when", _TEAM_RECORD_WHEN)], "record_when"
    # Already the team's record (the parser settles it, _decide_intent): a
    # decision only where the companion is not the player the route held -
    # the player read BEFORE the scope was rewritten, since after it is his
    # by construction.
    return rewritten, ([Decision("subject", "player", player, condition.player, _TEAM_RECORD_WHEN)] if player != condition.player else []), "record_when"


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
    if intent not in PLAYER_REQUIRED_INTENTS | SUBJECT_RESTORABLE_INTENTS or scope.subject.players:
        return scope, []
    named = list(dict.fromkeys((*subject.players, *subject.companions)))
    if len(named) != 1:
        return scope, []
    return replace(scope, subject=replace(scope.subject, players=(named[0],))), [Decision("subject", "player", None, named[0], "from the question; the router left it out")]


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
    if intent not in OWN_TEAM_RESTORABLE_INTENTS or subject.own_team is None or not scope.subject.players:
        return scope, []
    if scope.subject.team or scope.cuts.opponent or scope.cuts.tenure:
        return scope, []
    out = replace(scope, cuts=replace(scope.cuts, tenure=subject.own_team))
    decisions = [Decision("subject", "own_team", None, subject.own_team, "from the question; the router left it out")]
    if subject.named_season is None and not scope.span.career:
        out = replace(out, span=out.span.over_career())
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
    decisions = [Decision("subject", _players_slot(scope), r, None, "no player's name - a rank word, a phrase of the question, or a team's word") for r in routed if r in subject.filler]
    return replace(scope, subject=replace(scope.subject, players=tuple(kept))), decisions, kept


def _spare_names(subject: Subject, kept: list[str], intent: str) -> list[str]:
    """The question's own names not yet in the slots, which replace a router
    invention one for one: the subject's players, and for ``record_when`` -
    whose player IS the condition's - a reached companion too, whether the
    router chose ``record_when`` or the reading settled it (the team's
    question with a companion's line, under an invented player)."""
    spare = [p for p in subject.players if not _same_person(p, kept)]
    if "record_when" in (intent, subject.intent):
        spare += [c.player for c in subject.conditions if c.predicate == "reached" and not _same_person(c.player, [*kept, *spare])]
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
    field = _players_slot(scope)
    replacement = dict(zip(dropped, spare, strict=True)) if dropped else {}  # a spare name with nothing dropped is a player the router omitted: not put back here
    decisions.extend(Decision("subject", field, was, now, "the question never names the router's player; it names this one") for was, now in replacement.items())
    respelled = _respellings(kept, subject.players)
    replacement.update(respelled)
    decisions.extend(Decision("subject", field, was, now, "spelled as the question names the player") for was, now in respelled.items())
    new = [replacement.get(r, r) for r in routed]
    if new == routed:
        return scope, decisions, []
    return replace(scope, subject=replace(scope.subject, players=tuple(new))), decisions, []


def _apply_companions(subject: Subject, scope: Scope, intent: str) -> tuple[Scope, list[Decision]]:
    """Write every companion the reading found - an absence, a start, the
    bench, a line reached, a teammate who played, a player on the other
    side ("without KD", "when Embiid starts", "in games Maxey had 20+
    points", "with Draymond playing", "vs lebron") - onto the scope as the
    typed :class:`~association.query.reading.Companion`, whatever the
    intent: the relation readers read each as a filter on the games, the
    with/without split as the names it divides by (an absence, else the
    teammates who played) and the roles it holds each to ("record when
    Embiid and Paul George start": started against not), and an answer that
    cannot honor one refuses by name (the planner). Until 5.0.0's last
    change the roles were written only where the answering template honored
    them, a reader that asked what would answer before it wrote
    (``ROADMAP.md``, Phase 1): "how many times did the 76ers beat boston
    when embiid started" answered every meeting, the start gone without a
    word. Until Phase 3, step 2 the stages wrote the absences and the split's
    names into two slots of their own, and the parser the other side's
    players: one writer now. A companion who is the subject himself (his
    name restored from the companion's phrase, "In his 18th season, how
    many games with 40+ points did Lebron James have?") is no companion.
    The compiler narrows its relation by every one, so "how many 30 point
    games did maxey have when embiid started" counts his games with Embiid
    starting (34) - with nothing written it counted all of them (86)."""
    written = _apply_companions_to_write(subject, scope)
    if not written:
        return scope, []
    # The scope holds every companion typed, the other side first, as the
    # parser wrote them before the stages.
    ordered = [*(c for c in written if c.side == "opponent"), *(c for c in written if c.side == "own")]
    return replace(scope, companions=(*scope.companions, *ordered)), _apply_companions_decision(ordered, intent)


def _apply_companions_to_write(subject: Subject, scope: Scope) -> list[Companion]:
    """The companions the reading found that ``scope`` does not already
    hold: an absence keeps every name, the subject's own included, as the
    ``without`` slot did ("without zzyzx" is refused by name); any other
    role is written once per person and never for the subject himself."""
    own = list(scope.subject.players)
    held = [c.player for c in scope.companions]
    written: list[Companion] = []
    for each in subject.conditions:
        already = [c.player for c in written]
        new = not _same_person(each.player, already) if each.absent else not _same_person(each.player, own) and not _same_person(each.player, [*held, *already])
        if new:
            written.append(each)
    return written


def _apply_companions_decision(ordered: list[Companion], intent: str) -> list[Decision]:
    """The decision that records the own-side roles in the slot shape the
    trace has always printed (the other side's players the parser wrote
    without one until Phase 3, step 2, and a teammate the with/without
    split divides by was never a condition)."""
    recorded = [c.to_slot() for c in ordered if c.side == "own" and not c.absent and (c.predicate != "played" or intent != "with_without")]
    return [Decision("subject", "conditions", None, recorded, "the role the question gives each player named beside the subject")] if recorded else []


def _routed_player_slots(scope: Scope) -> list[str]:
    """The model's player names, as the scope's typed subject carries them."""
    return list(scope.subject.players)


def _players_slot(scope: Scope) -> str:
    """The slot the scope's players were carried in until Phase 3, step 2
    - ``players`` for two or more, ``player`` for one - the field a
    decision about them still names, as the trace has always printed it."""
    return "players" if len(scope.subject.players) >= 2 else "player"


def _listed_teams(scope: Scope) -> tuple[str, ...]:
    """The teams the scope names as a list (two or more: the router-era
    ``teams`` slot; the one team a scope names is :attr:`~association.query.reading.Subject.team`)."""
    return scope.subject.teams if len(scope.subject.teams) >= 2 else ()


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


# --- The names a question's words hold ---------------------------------------
#
# The readers of the question's own words for a player's or a team's name, the
# subject reading's building blocks. They lived in entities.py until Phase 3's
# first step (2026-10-09), where the answering loop called two of them with the
# question; they take the names' in-memory index, never a connection
# (entities.players_of, entities.teams_of).


# "vs", "versus", "against" or "v" and whatever follows (lexicon.AGAINST_PHRASE):
# whether what follows is a team is decided against the teams table, not
# there - "lebron vs kawhi" is two players and must stay a comparison.


def team_named_in(teams: names.TeamIndex, question: str) -> str | None:
    """The one team the question itself names, by a whole word of it (or a
    curated nickname) - the team counterpart of
    :func:`players_named_in`, kept deliberately
    minimal: single words only, since no franchise name has an internal
    ambiguity a span needs to resolve the way a player's first/last name
    does ("Portland Trail Blazers" is found by "blazers" alone; nothing
    named "Trail" collides with it). Never a guess between two candidates -
    only an exact single match counts, and the first match wins, read left
    to right the way a question states its subject first.

    Used to restore a team the router dropped entirely (F127, ISSUES.md:
    "how many 3 pointers have the magic made" routed with no ``team`` slot
    at all) - the same repair :func:`players_named_in`
    already makes for a dropped player.

    .. versionadded:: 4.4.0

    .. versionchanged:: 5.0.0
       Lives here, beside the player's reader: it was ``compose.team``'s,
       which the subject reading and the parser imported from the answer
       side to read a team word (``ROADMAP.md``, Phase 1).

    .. versionchanged:: 5.0.0
       A possessive ("the Sixers' record") names the team as the bare word does.

    .. versionchanged:: 6.0.0
       Lives in :mod:`association.query.subject`, the reader's, and takes the
       teams' in-memory index (:func:`~association.query.entities.teams_of`)
       in place of a connection: it was ``entities.team_named_in``. Reads
       "76ers", the one name spelled with a digit, where it stands.
    """
    for run in lexicon.TEAM_SPELLING_RUN.findall(question.lower()):
        # "76ers", the one name spelled with a digit, which the letter runs
        # below split into "ers": read whole, where the question gives it.
        spelled = run.removesuffix("'s").rstrip("'")
        if spelled in lexicon.TEAM_SINGULARS and any(ch.isdigit() for ch in spelled):
            return lexicon.TEAM_SINGULARS[spelled]
        for found in lexicon.LETTER_RUN.findall(run):
            # "the Sixers' record", "the Knicks' last 5 games": the possessive
            # is the question's, not the name's (ISSUES.md #232 - 11 of 277
            # paraphrases read no team at all).
            word = found.removesuffix("'s").rstrip("'")
            if len(word) < 4:
                continue
            nickname = _TEAM_NICKNAMES.get(word)
            if nickname:
                return nickname
            named = teams_named_by_word(teams, word)
            if len(named) == 1:
                return str(named[0])
    return None


def subject_named_in(question: str) -> str | None:
    """The word (or two) a single-game-high or threshold-count question's
    grammar makes its subject, or None (:data:`~association.query.lexicon.SUBJECT_OF_HIGH`,
    ``SUBJECT_OF_COUNT``, ``SUBJECT_OF_HAVE``, tried in that order): a name
    before a scoring verb or "fouled out", with a possessive, before "games
    with"/"<N> <stat> games", or between an auxiliary and "have". A word of
    :data:`~association.query.lexicon.NOT_A_SUBJECT` is never the name, and
    never its leading word - "most points curry scored" reads "curry", never
    "points curry"; "kobe bryant's" reads "kobe bryant", where a bare "bryant"
    is four other players and none of them him.

    Returns the question's own words, not a resolved player: resolution
    decides whether they name somebody, and asks when it is ambiguous.
    "curry" then answers "did you mean Seth Curry or Stephen Curry?", which is
    the question asked - where the league's high is not. Read once, by the
    parser where it hands the stages who the question is about
    (``router.Named``); the stages settle it where the intent reads one and
    nobody else is named.

    .. versionadded:: 6.0.0
       ``router._subject_named_in`` until Phase 3, step 2: the stages read a
       name from the words themselves.
    """
    for match in lexicon.SUBJECT_OF_HIGH.finditer(question):
        lead, word = match.group(1), match.group(2)
        # The richer list for the word itself too: "Total points scored by the
        # toronto raptors" read "points", "least points scored by the wizards"
        # the same - a stat's own noun read as a person. Measured over the
        # 261-question corpus, this loses no real name and drops three pieces
        # of junk.
        if word.casefold() in lexicon.NOT_A_SUBJECT:
            continue
        if lead is not None and lead.casefold() not in lexicon.NOT_A_SUBJECT:
            return f"{lead} {word}"
        return word
    for pattern in (lexicon.SUBJECT_OF_COUNT, lexicon.SUBJECT_OF_HAVE):
        for match in pattern.finditer(question):
            lead, word = match.group(1), match.group(2)
            if word.casefold() in lexicon.NOT_A_SUBJECT:
                continue
            if lead is not None and lead.casefold() not in lexicon.NOT_A_SUBJECT:
                return f"{lead} {word}"
            return word
    return None


def team_words_in(question: str) -> tuple[str, ...]:
    """Every team nickname the question holds as a whole word
    (:data:`~association.query.lexicon.TEAM_NICKNAME`), as typed and
    lowercased, in order - what the stages file as a team's quarter or half
    where nothing else named the team, read here once.

    .. versionadded:: 6.0.0
    """
    return tuple(lexicon.TEAM_NICKNAME.findall(question.lower()))


def is_team_name(name: str) -> bool:
    """Whether ``name`` - a span the model or the grammar offered as a name -
    is a team's. Three tests, narrowing as they get looser:

    - **Its LAST word is a nickname** (:data:`~association.query.lexicon.TEAM_NICKNAME`).
      Checked against the warehouse: all 30 team names end in one and none
      of 3,101 player names does, while "Magic Johnson" holds one as his
      first name - and a match anywhere in the name took him for a team.
    - **The WHOLE name is a city or an abbreviation** ("det", "Orlando";
      :data:`~association.query.lexicon.TEAM_CITIES`, ``TEAM_ABBREVIATIONS``).
      Never the last word, because three players are surnamed Cleveland,
      Houston and Washington.

    A misspelled team is deliberately NOT matched (the lexicon's note: fuzzy
    matching turns 16 real player surnames into teams).

    .. versionadded:: 6.0.0
       ``router._is_team_name`` until Phase 3, step 2.
    """
    words = name.lower().split()
    if not words:
        return False
    if lexicon.TEAM_NICKNAME.fullmatch(words[-1]) is not None:
        return True
    whole = " ".join(words)
    return whole in lexicon.TEAM_CITIES or whole in lexicon.TEAM_ABBREVIATIONS


def team_named_in_text(question: str, candidate: str | None) -> str | None:
    """``candidate`` back, but only if the question's own words say it - any
    one word of four letters or more, whole - the mirror of
    :func:`is_team_name`, which asks whether a span IS a team at all rather
    than whether the question named this one.

    ISSUES.md #170: a quarter question with no player slot sometimes filled
    the team and the opponent with a team the model inferred rather than one
    the question used - "Jokic ... 3rd quarter against Boston" filled the
    opponent with 'Denver Nuggets', Jokic's own, a word the question never
    wrote, while the team held 'Boston Celtics', a word it did.

    .. versionadded:: 6.0.0
       ``router._team_slot_named_in_text`` until Phase 3, step 2.
    """
    if candidate is None:
        return None
    words = lexicon.LONG_LETTER_RUN.findall(candidate)
    if not words:
        return None
    held = {run.group(0) for run in lexicon.WORD_RUN.finditer(question.lower())}
    return candidate if any(word.lower() in held for word in words) else None


# Built once, by the lexicon's reader of whole phrases (longest first, "greek
# freak" before a hypothetical "greek"), over the curated table.
_NICKNAME_RE = lexicon.whole_phrases(tuple(PLAYER_NICKNAMES))


def nicknames_in(question: str) -> list[str]:
    """Player names for every nickname appearing as a whole word in ``question``,
    in the order they appear, without repeats.

    Matched against the user's own words, which is the only place a nickname
    still exists: by the time a model has filled a slot it has usually
    rewritten the nickname, and when it rewrites one wrongly there is nothing
    downstream to notice.

    .. versionadded:: 2.1.0

    .. versionchanged:: 6.0.0
       Lives in :mod:`association.query.subject`, the reader's: it was
       ``entities.nicknames_in``.
    """
    seen: list[str] = []
    for match in _NICKNAME_RE.finditer(question):
        name = PLAYER_NICKNAMES[match.group(1).casefold()]
        if name not in seen:
            seen.append(name)
    return seen


def players_named_in(players: names.PlayerIndex, question: str) -> list[str]:
    """Players the question itself names, in the order it names them.

    The generalization of :func:`nicknames_in` from the curated table to the
    whole roster, and the same idea: the question is the only place a name the
    user actually typed still exists. Spans of three words down to one are
    tried left to right, longest first, so "karl anthony towns" is read as one
    name rather than three.

    Deliberately strict about what counts as naming somebody, because this is
    used to overrule the router. A span matches only if it is a nickname key or
    if every word of it equals a whole word of exactly one player's name -
    substring matching would read "What was the highest scoring game" as naming
    Jaron Blossomgame, and word-boundary matching would read "with" as naming
    Jeff Withey. Single words shorter than three letters are ignored for the
    same reason: the possessive left behind by "Jokic's" is an "s", which is a
    whole word of "John S. Williams".

    .. versionadded:: 2.1.0

    .. versionchanged:: 6.0.0
       Lives in :mod:`association.query.subject`, the reader's, and takes the
       players' in-memory index (:func:`~association.query.entities.players_of`)
       in place of a connection: it was ``entities.players_named_in``.
    """
    words = _words(question)
    found: list[str] = []
    index = 0
    while index < len(words):
        for size in range(lexicon.MAX_NAME_WORDS, 0, -1):
            if index + size > len(words):
                continue
            span = words[index : index + size]
            nickname = PLAYER_NICKNAMES.get(" ".join(span).casefold())
            if nickname is not None:
                found.append(nickname)
                index += size
                break
            if any(len(w) < 3 for w in span):
                continue
            rows = _exact_name_span(players, span)
            if len(rows) == 1:
                found.append(rows[0].name)
                index += size
                break
        else:
            index += 1
    seen: list[str] = []
    for name in found:
        if name not in seen:
            seen.append(name)
    return seen


def _anchor_word_position(q_words: list[str], lowered_q: list[str], word: str) -> int | None:
    """Where ``word`` (one word of the router's name) turns up in the
    question - exactly, or the nearest near spelling within
    :func:`~association.query.entities._edit_budget` - or ``None`` when it turns up nowhere at all."""
    exact = next((i for i, w in enumerate(lowered_q) if w == word.casefold()), None)
    if exact is not None:
        return exact
    if len(word) < 3:
        return None  # a near spelling of a word this short is a different word
    # Measured as DuckDB's damerau_levenshtein(lower(q), word) measured it,
    # when this asked the warehouse to do the arithmetic.
    row = [names.distance(names.sql_lower(q), word.casefold()) for q in q_words]
    budget = _edit_budget(word)
    near = [i for i, d in enumerate(row) if d <= budget and len(lowered_q[i]) >= 3]
    return min(near, key=lambda i: row[i]) if near else None


def _resolve_word_span(players: names.PlayerIndex, span: list[str]) -> Entity | None:
    """``span``, resolved to one player - exact words before near ones, the
    same order :func:`players_named_in` tries - or ``None``. A single word is
    never handed to the fuzzy pass; see :func:`question_derived_player`."""
    if any(len(w) < 3 for w in span):
        return None
    matches = _exact_name_span(players, span)
    if len(matches) != 1 and len(span) > 1:
        matches = _fuzzy_name_span(players, span, limit=2)
    return matches[0] if len(matches) == 1 else None


def question_derived_player(players: names.PlayerIndex, question: str, name: str) -> Entity | None:
    """The one player a window of the question's OWN words - anchored to
    wherever ``name`` (the router's guess, right or wrong) itself appears -
    plausibly names, when that is confident enough to act on.

    Two faults this repairs, both structural, and both invisible to the
    "any one word is enough" check a name is otherwise held to
    (:func:`question_supports`), because that check
    is deliberately generous: the router TRUNCATES a name the question spells in
    full ("dennis schröder", typed correctly, arrived as just ``'Dennis'`` -
    grounded, and a 7-way surname), and it FABRICATES a word next to a real
    one ("tatum rec home" arrived as ``'Jaylen Tatum'``, the league's only
    Tatum with an invented given name bolted on; "Grady dick" arrived as
    ``'Grady Dickinson'``, grounded by its own typo'd given name). Neither
    ever reaches a repair, because grounding already says yes.

    So this reads the question instead of trusting the router's spelling: it
    anchors each of ``name``'s own words to where it turns up nearby - exactly,
    or within :func:`~association.query.entities._edit_budget`, since the router silently corrects typos
    and a near spelling still marks the spot - and resolves the question's
    literal words there against the roster, never the router's spelling.

    Deliberately anchored, never a sentence-wide scan: fuzzy-matching a
    question's leftover words was measured and rejected elsewhere in this
    module ("season" is one edit from Tari Eason, wherever it turns up), and
    what keeps this safe is that nothing is tried unless it sits next to a
    word the router already pointed at - a name with no anchor at all is left
    for the ungrounded path below to report, exactly as before.

    Two things measured against real corpus rows keep this from trusting too
    little of a coincidence:

    - **Two or more of the router's own words anchoring is answered from
      within exactly that range of the question, narrowed a word at a time -
      but never down to one word alone.** "kareem stats vs bob lanier"
      anchors both "bob" and "lanier" (the question's own words, spelled
      exactly), and that pair names nobody: Bob Lanier retired before the
      warehouse's 1993-94 floor. Falling back to "lanier" alone then named
      Chaz Lanier, a real but wholly unrelated player - the same false-cause
      shape the Maxey example in the module docstring warns about, arrived at
      through this function instead of a nickname. The range still shrinks
      rather than being tried whole-or-nothing, because the router's own
      spelling can itself be the mismatch: "de'angelo russell" anchors "de",
      "angelo" and "russell" all exactly, and the full three-word span fails
      only because the roster spells the first of them "d", not "de" -
      dropping it and resolving "angelo russell" alone is what recovers
      D'Angelo Russell. What is never tried is the SINGLE remaining word once
      the range is down to it: two anchored words failing together, with no
      narrower range above one word left to try, is the answer, not an
      invitation to trust one of them alone.
    - **With exactly one anchor, a WINDOW around it (two words or more) is
      trusted regardless of where the anchor sits** - "Grady dick" anchors
      only on the given name "Grady", and the window's "grady dick" is what
      resolves it to Gradey Dick. What is trusted only when that anchor is
      the LAST of the router's words, the position a surname sits in, is
      falling all the way back to the anchor word ALONE, with nothing else
      corroborating it. "Jaylen Tatum" anchors only on "Tatum", its last
      word, and the league's only Tatum is trustworthy alone. "Kareem
      Abdul-Jabbar" anchors only on "Kareem", its FIRST word, and "Kareem"
      alone is exactly as unrelated a near-miss as "lanier" alone above, for
      the identical reason - Kareem Abdul-Jabbar is the other player these
      two corpus rows have in common, and neither he nor Bob Lanier has a
      row in `players` at all. A router-supplied given name with nothing
      else in the question corroborating it is left alone here, for the
      "any one word" check to judge as it always has.

    Returns:
        The one player the question's own words resolve to, or ``None`` when
        nothing anchors at all, when two or more anchored words do not
        resolve together, when a single anchor resolves only alone and is
        not the last of the router's words, or when what is left resolves to
        more than one player (left for :func:`~association.query.entities.resolve_player` to ask about)
        or to none.

    .. versionadded:: 6.0.0
       ``entities._question_derived_player``, public in the reader, taking
       the players' in-memory index (:func:`~association.query.entities.players_of`).
    """
    q_words = _words(question)
    name_words = [w for w in _words(name) if w]
    if not q_words or not name_words:
        return None

    lowered_q = [w.casefold() for w in q_words]
    positions = [_anchor_word_position(q_words, lowered_q, word) for word in name_words]
    anchors = {p for p in positions if p is not None}
    if not anchors:
        return None

    bounds = _question_derived_player_multi_anchor_window(q_words, anchors) if len(anchors) >= 2 else _question_derived_player_single_anchor_window(q_words, name_words, positions, anchors)
    if bounds is None:
        return None
    window, min_size = bounds

    try:
        return _question_derived_player_search(players, window, min_size)
    except duckdb.CatalogException:
        # A warehouse without `players` (a partial load, or a test double) has
        # nothing here to resolve against - the same best-effort rule
        # `_team_named` follows: finding nothing leaves the slots exactly as
        # the router gave them, which is never worse than before this ran.
        return None


def _question_derived_player_multi_anchor_window(q_words: list[str], anchors: set[int]) -> tuple[list[str], int] | None:
    """The window and size floor for two or more anchored words - resolved
    from exactly that range of the question, one word narrower at a time,
    but never down to a single word alone. See
    :func:`question_derived_player`'s docstring's "kareem ... bob lanier"
    measurement for why: falling back that far is what named Chaz Lanier.
    "de'angelo russell" is why the range still shrinks at all - the router's
    own spelling of "De" does not match the roster's "D", and dropping it
    lets "angelo russell" resolve on its own. ``None`` when the anchors are
    too spread out to be one coherent name."""
    if max(anchors) - min(anchors) > 4:
        return None
    return q_words[min(anchors) : max(anchors) + 1], 2


def _question_derived_player_single_anchor_window(q_words: list[str], name_words: list[str], positions: list[int | None], anchors: set[int]) -> tuple[list[str], int]:
    """The window and size floor for exactly one anchored word: a small
    window around it, never the whole question - what keeps
    :func:`question_derived_player` from becoming the rejected
    leftover-word scan. Two or more words of THIS window agreeing is trusted
    regardless of where the anchor sits ("Grady dick" anchors on the given
    name "Grady", and the window's "grady dick" is what resolves it) - what
    is trusted only from the surname position (the floor of 1 rather than 2)
    is falling all the way back to the anchor WORD ALONE, with nothing else
    corroborating it; see the docstring's "Kareem" measurement."""
    anchor_index = next(i for i, p in enumerate(positions) if p is not None)
    anchor = next(iter(anchors))
    window = q_words[max(0, anchor - 1) : min(len(q_words), anchor + 2)]
    min_size = 1 if anchor_index == len(name_words) - 1 else 2
    return window, min_size


def _question_derived_player_search(players: names.PlayerIndex, window: list[str], min_size: int) -> Entity | None:
    """``window``, tried longest span first down to ``min_size``, exact
    words before near ones - the shared search both
    :func:`_question_derived_player_multi_anchor_window` and
    :func:`_question_derived_player_single_anchor_window` resolve into."""
    for size in range(min(lexicon.MAX_NAME_WORDS, len(window)), min_size - 1, -1):
        found = [resolved for start in range(len(window) - size + 1) if (resolved := _resolve_word_span(players, window[start : start + size])) is not None]
        unique_ids = {c.id for c in found}
        if len(unique_ids) == 1:
            return found[0]
    return None


# "X vs Y" (lexicon.VERSUS_WORD), the one structural signal that two SUBJECTS
# were meant - tighter than a compare word on purpose: "compare Jokic's
# fingerprint to last season" compares seasons, and a note claiming a player
# is missing there would be noise.


def compared_but_unmatched(players: names.PlayerIndex, question: str, held: list[str]) -> LeftOut | None:
    """What a "vs" question the reading holds fewer than two players of
    leaves out (:class:`~association.query.reading.LeftOut`), or None where
    it compares nothing or holds both sides. The answer says it beside a
    fingerprint that drew one polygon (the ``name_left_out`` decision).

    A misspelling nothing can repair - "generate fingerprints for embiid vs
    jolic" drew Joel Embiid alone, because "jolic" matches no player and is not
    close enough to exactly one to guess at. Recovering it was measured and
    rejected: a near-spelling search over a question's leftover words finds a
    spurious player in 29 of 51 corpus questions ("season" is one edit from
    Tari Eason, "most" from Quinten Post), and it does not find Nikola Jokic
    here either. That case still gets the spelling note.

    But one polygon on a "vs" question has a second cause that is not a
    misspelling at all: "show a fingerprint for maxey vs jaylen brown in 2026"
    said "only one of them matches anybody in the warehouse - check the
    spelling of the other" about Jaylen Brown, whom ``players`` holds and
    ``net_points_player_fingerprint`` has a 2026 row for - he was simply never
    carried into the answer. That is the mirror-image bug AGENTS.md calls out
    under "a refusal that names the wrong cause", so before blaming a
    spelling, the leftover name is resolved against the roster - and where
    it resolves, the sentence says the player was dropped, never that he is
    missing from the warehouse.

    .. versionadded:: 2.1.0
    .. versionchanged:: 4.4.0
       Takes ``con`` and returns the caveat text (or ``None``) rather than a
       bool, so a name that resolves against the roster gets a true sentence
       instead of being folded into the same claim as one that does not.

    .. versionchanged:: 6.0.0
       Lives in :mod:`association.query.subject`, the reader's, and takes the
       players' in-memory index (:func:`~association.query.entities.players_of`)
       in place of a connection: it was ``entities.compared_but_unmatched``.
       Returns the names as values (a :class:`~association.query.reading.LeftOut`),
       which the parser carries on the Reading; the sentence is the sayer's.
    """
    if len(held) >= 2 or not lexicon.VERSUS_WORD.search(question):
        return None
    dropped = [name for name in players_named_in(players, question) if not any(_shares_word(name, k) for k in held)]
    return LeftOut(held=tuple(held), names=tuple(dropped))


def _has_a_real_team(teams: names.TeamIndex, slots: dict[str, Any], question: str) -> bool:
    """Whether ``slots`` carries a ``team``/``teams`` value that actually
    resolves to a real franchise - not merely a non-empty string. The router
    invents a team the way it invents a player (AGENTS.md, "the router
    invents names"): "alperen şengün alltime record" arrived one run with no
    `team` at all, and another with `team='Alperen Şengün'` - the player's
    own name, filed as though it were a franchise, which
    ``team_record``-shaped readers then refuse as "no team matching", the
    wrong cause. A team
    slot nothing resolves is functionally the same as no team slot at all.

    So is one that resolves to a franchise the question never names:
    "towns home rec including playoffs since 1/26/20 vs spurs" (yardstick-v2
    F110) arrived with ``team='Toronto Raptors'`` - a real team, and no word
    of it in the question - and would have answered the Raptors' record
    about a question naming Karl-Anthony Towns (:func:`_team_grounded`)."""
    season = slots.get("season") if isinstance(slots.get("season"), int) else None
    filed = slots.get("teams")
    texts = [slots.get("team"), *(filed if isinstance(filed, list) else [filed])]
    for text in texts:
        team = _team_named(teams, text, season) if isinstance(text, str) and text.strip() else None
        if team is not None and _team_grounded(teams, question, team):
            return True
    return False


def player_named_on_a_team_only_question(players: names.PlayerIndex, teams: names.TeamIndex, question: str, slots: dict[str, Any]) -> str | None:
    """The one player a question names, when the routed intent has no
    reading for him at all and the question names no REAL team either - or
    None.

    yardstick-v2 F111: "alperen şengün alltime record" routed to
    ``team_leaderboard`` - no player slot exists on that intent at all, and
    no ``team`` slot that resolves to a real franchise was filled either -
    and answered the league standings, entirely off the subject the
    question named. Diacritics are already handled: ``players_named_in``
    folds "şengün" to "sengun" (:func:`~association.query.entities._fold`) before matching, the same way
    it reads "dončić". See :func:`_has_a_real_team` for why an invented
    ``team`` value (the player's own name, filed as though it were a
    franchise - measured live, a second run of this exact question) counts
    as no team at all rather than stopping this check.

    Caller-gated to :data:`~association.query.reading.TEAM_ONLY_INTENTS`
    (this function does not check the intent itself, the same shape the
    subject reading's intent sets take in
    :func:`apply_subject`): a REAL team already named there is the
    real subject, and a player coincidentally named beside it changes no
    answer - the same reasoning that keeps a stray name on ``head_to_head``
    from being refused elsewhere in this module. A word that names only a
    team ("magic" in "magic vs nets" is the Orlando Magic, not Magic
    Johnson - :func:`_named_only_by_a_team_word`) or only a common English
    word that collides with a surname ("best" is Travis Best -
    :func:`_named_only_by_a_common_word`) is excluded the same way
    the subject reading already excludes both.

    .. versionadded:: 4.4.0

    .. versionchanged:: 6.0.0
       Lives in :mod:`association.query.subject`, the reader's, and takes the
       players' and teams' in-memory indexes (:func:`~association.query.entities.players_of`)
       in place of a connection: it was ``entities.player_named_on_a_team_only_question``.
    """
    if _has_a_real_team(teams, slots, question):
        return None
    named = [name for name in players_named_in(players, question) if not _named_only_by_a_team_word(teams, question, name) and not _named_only_by_a_common_word(question, name)]
    return named[0] if len(named) == 1 else None


def _team_after_versus(teams: names.TeamIndex, question: str, season: int | None = None, *, names: list[str] | tuple[str, ...] = ()) -> Entity | None:
    """The team a question sets a subject AGAINST ("jaylen brown last 8 games vs
    pistons"), or None. Spans of three words down to one are tried, so "vs new
    york" is the Knicks rather than an ambiguous "new". A span the team only
    clips - no word of its name, abbreviation or nickname, only the start of
    one ("tim" of "Timberwolves") - is no team where its words are a name
    the question gives a player (``names``, :func:`players_named_in`):
    "kevin garnett vs tim duncan games" is two players, and read "tim" as
    the Timberwolves, the opponent of a matchup that has none, it was
    refused as one ("player_matchup cannot honor ['opponent']")."""
    held = {word.casefold() for name in names for word in _words(name)}
    for match in lexicon.AGAINST_PHRASE.finditer(question):
        words = _team_words(match.group(1))[:3]
        for size in (3, 2, 1):
            if size <= len(words) and len(" ".join(words[:size])) >= 2:
                team = _team_named(teams, " ".join(words[:size]), season)
                if team is not None and not _team_after_versus_a_name(teams, team, words[:size], held):
                    return team
    return None


def _team_after_versus_a_name(teams: names.TeamIndex, team: Entity, span: list[str], held: set[str]) -> bool:
    """Whether ``span`` is a player's name the question gives (``held``,
    its words) that ``team`` only clips: none of its words a trace of the
    team (:func:`_team_traces`, or its singular: "vs buck" is the Bucks
    whoever else is named Buck) and every one a word of that name."""
    traces = _team_traces(teams, team) | {word for word, name in lexicon.TEAM_SINGULARS.items() if name == team.name}
    return bool(held) and all(word.casefold() in held for word in span) and not any(word.casefold() in traces for word in span)


# "for", "with the" and whatever follows (lexicon.FOR_TEAM_PHRASE) - a
# player's OWN team, unlike the versus phrase's opponent; the teams table is
# the gate, not the pattern.


def _team_after_for(teams: names.TeamIndex, question: str, season: int | None = None) -> tuple[Entity, int] | None:
    """The player's OWN team a question names with "for"/"with the"
    ("lebron stats as a starter for Miami", yardstick-v2 F166) with where
    in the question the phrase starts, or None. Spans of three words down
    to one are tried, the same as :func:`_team_after_versus`.

    .. versionadded:: 4.4.0
    """
    for match in lexicon.FOR_TEAM_PHRASE.finditer(question):
        span_text = match.group(1) or match.group(2) or ""
        words = _team_words(span_text)[:3]
        for size in (3, 2, 1):
            if size <= len(words) and len(" ".join(words[:size])) >= 2:
                if size == 1 and words[0].casefold() in lexicon.COMMON_WORDS_THAT_NAME_TEAMS:
                    # "for me" is the asker, not the Memphis Grizzlies.
                    continue
                team = _team_named(teams, " ".join(words[:size]), season)
                if team is not None:
                    return team, match.start()
    return None


# --- What the subject reading claims ---------------------------------------
#
# Contract 2: each word is read once, and the reader rule that read it claims
# the characters (reading.Claim). The subject reading claims the words of the
# names it settled - the players and the teams the read is about, the team
# the subject is set against and the one he played for - and the position
# group's words (Phase 3, step 2's seventh slice; the companions' phrases it
# claimed since the line slice). Read off the settled value, so a name the
# reading read and set aside (a dictionary word that is somebody's surname, a
# name the model invented) claims nothing.


def subject_claims(teams: names.TeamIndex, question: str, scope: Scope) -> tuple[Claim, ...]:
    """The characters of ``question`` the subject reading read the settled
    ``scope``'s names and position group from (:class:`~association.query.reading.Claim`):
    each player's (``"player"``: the question's words of his name, a curated
    nickname, his initials, or - where no word of it stands as typed - each
    word's nearest near spelling), each team's (``"team"``: a word of its
    name, its abbreviation, a nickname, a singular, the name run together, a
    clipped word), the opponent's and the tenure's with the versus word or
    the "for" before them (``"opponent"``, ``"tenure"``), and the position
    group's words (``"position"``). In the question's order, each stretch
    once; a name the question gives twice is claimed where each stands.

    .. versionadded:: 6.0.0
    """
    tokens = _subject_claims_tokens(question)
    found: list[Claim] = []
    for player in scope.subject.players:
        found.extend(_subject_claims_player(question, tokens, player))
    season = scope.span.season
    for team in scope.subject.teams:
        found.extend(Claim(start, end, "team") for start, end in _subject_claims_team(teams, tokens, team, season))
    for placed, phrase, what in ((scope.cuts.opponent, lexicon.AGAINST_PHRASE, "opponent"), (scope.cuts.tenure, lexicon.FOR_TEAM_PHRASE, "tenure")):
        if placed is not None:
            found.extend(_subject_claims_led(question, phrase, _subject_claims_team(teams, tokens, placed, season), what))
    position = scope.subject.position
    if position is not None:
        match = next((m for pattern, code in lexicon.POSITION_WORDS if code == position for m in [pattern.search(question)] if m is not None), None)
        if match is not None:
            found.append(Claim(match.start(), match.end(), "position"))
    return tuple(sorted(set(found), key=lambda c: (c.start, c.end, c.what)))


def _subject_claims_tokens(question: str) -> list[tuple[str, int, int]]:
    """The question's words with where each stands: runs of word
    characters (:data:`~association.query.lexicon.WORD_RUN`), each folded
    to its plain lowercase spelling, as a name's words are."""
    return [(_fold(m.group(0)).casefold(), m.start(), m.end()) for m in lexicon.WORD_RUN.finditer(question)]


def _subject_claims_runs(tokens: list[tuple[str, int, int]], held: list[bool]) -> list[tuple[int, int]]:
    """Each run of consecutive question words ``held`` marks, as one
    stretch of characters: "joel embiid", "gilgeous-alexander"."""
    runs: list[tuple[int, int]] = []
    for (_, start, end), keep in zip(tokens, held, strict=True):
        if not keep:
            continue
        if runs and runs[-1][1] >= start - 1 and _subject_claims_adjacent(tokens, runs[-1][1], start):
            runs[-1] = (runs[-1][0], end)
        else:
            runs.append((start, end))
    return runs


def _subject_claims_adjacent(tokens: list[tuple[str, int, int]], end: int, start: int) -> bool:
    """Whether the word starting at ``start`` follows the one ending at
    ``end`` with no word between them."""
    return not any(end <= t_start and t_end <= start for _, t_start, t_end in tokens)


def _subject_claims_player(question: str, tokens: list[tuple[str, int, int]], player: str) -> list[Claim]:
    """The characters ``player``'s name was read from: its words as the
    question types them, a curated nickname, its initials - or, where no
    word of the name stands as typed, each word's nearest near spelling
    within the entity index's budget (a typo the index read as him)."""
    words = [_fold(m.group(0)).casefold() for m in lexicon.WORD_RUN.finditer(player)]
    held = [text in words for text, _, _ in tokens]
    if not any(held):
        near = [
            min(((names.distance(text, word), i) for i, (text, _, _) in enumerate(tokens) if len(text) >= 3 and names.distance(text, word) <= _edit_budget(word)), default=None)
            for word in words
            if len(word) >= 3
        ]
        for each in near:
            if each is not None:
                held[each[1]] = True
    initials = _initials(player)
    held = [keep or (bool(initials) and text == initials) for keep, (text, _, _) in zip(held, tokens, strict=True)]
    claims = [Claim(start, end, "player") for start, end in _subject_claims_runs(tokens, held)]
    claims += [Claim(m.start(), m.end(), "player") for m in _NICKNAME_RE.finditer(question) if PLAYER_NICKNAMES[m.group(1).casefold()] == player]
    return claims


def _subject_claims_team(teams: names.TeamIndex, tokens: list[tuple[str, int, int]], team: str, season: int | None) -> list[tuple[int, int]]:
    """The stretches of the question ``team`` was read from: the words
    :func:`_team_grounded` takes as its trace - a word of its name, its
    abbreviation, a nickname, the name run together, a clipped word of three
    letters or more - beside its singular, the name as the scope spells it
    and a former name's shorthand."""
    traces = {_fold(m.group(0)).casefold() for m in lexicon.WORD_RUN.finditer(team)}
    resolved = _team_named(teams, team, season)
    if resolved is not None:
        traces |= _team_traces(teams, resolved)
    traces |= {word for word, name in lexicon.TEAM_SINGULARS.items() if name in (team, resolved.name if resolved is not None else None)}
    traces |= {alias for alias, name in _ERA_ALIASES.items() if " " not in alias and name.casefold() == team.casefold()}
    traces -= lexicon.COMMON_WORDS_THAT_NAME_TEAMS
    held = [text in traces or (len(text) >= 3 and any(trace.startswith(text) for trace in traces if " " not in trace)) for text, _, _ in tokens]
    return _subject_claims_runs(tokens, held)


def _subject_claims_led(question: str, phrase: re.Pattern[str], stretches: list[tuple[int, int]], what: str) -> list[Claim]:
    """The stretches a team was read from, each with the versus word or the
    "for" that leads it where one stands right before it (``phrase``:
    :data:`~association.query.lexicon.AGAINST_PHRASE`, ``FOR_TEAM_PHRASE``,
    whose group holds the words after the lead), named ``what``."""
    leads = [(m.start(), m.start(m.lastindex or 1)) for m in phrase.finditer(question)]
    claims: list[Claim] = []
    for start, end in stretches:
        lead = next((lead_start for lead_start, words_start in leads if words_start == start), None)
        claims.append(Claim(lead if lead is not None else start, end, what))
    return claims


def _team_grounded(teams: names.TeamIndex, question: str, team: Entity) -> bool:
    """Whether the question shows any trace of ``team`` - a word of its name,
    its abbreviation, or a nickname: the team counterpart of the check a
    player's name is held to (:func:`question_supports`)."""
    carried = _team_traces(teams, team)
    if not carried:
        return True  # nothing to check it against; leave it alone
    asked = {word.casefold() for word in _words(question)}
    if carried & asked:
        return True
    # A clipped word is a trace too: "cav vs celtic last 10games" names both
    # teams, and the router's expansion of "cav" to the Cavaliers is its job,
    # not an invention. Three letters, so "la" and "no" ground nothing.
    return any(len(word) >= 3 and any(name.startswith(word) for name in carried) for word in asked)


def _team_traces(teams: names.TeamIndex, team: Entity) -> set[str]:
    """Every word a question carries ``team`` by: a word of its name, its
    abbreviation, a nickname, the name with a space left out - empty where
    the index holds no row for it."""
    row = next(((t["abbreviation"], t["display_name"]) for t in team_columns(teams, "abbreviation", "display_name", "team_id").rows if t["team_id"] == team.id), None)
    if row is None:
        return set()
    carried = {word.casefold() for word in _words(row[1])} | {str(row[0]).casefold()}
    carried |= {nickname for nickname, name in _TEAM_NICKNAMES.items() if name == row[1]}
    # A name written with a space left out is a trace of the team as plainly as
    # its own words are: "trailblazers stats last 10 games" was dropped as a
    # team the question never mentioned, and refused for naming no team at all.
    carried |= _run_together(row[1])
    return carried


def _named_only_by_a_common_word(question: str, player: str) -> bool:
    """Whether every word of ``player``'s name the question holds is ALSO an
    ordinary English word known to collide with a real surname
    (:data:`lexicon.COMMON_WORDS_THAT_NAME_PLAYERS`) - the same shape
    :func:`_named_only_by_a_team_word` checks for a team name, applied to a
    plain word instead of a team's.

    .. versionadded:: 4.4.0
    """
    asked = {w.casefold() for w in _words(question)}
    supporting = [w for w in _words(player) if w.casefold() in asked]
    return bool(supporting) and all(w.casefold() in lexicon.COMMON_WORDS_THAT_NAME_PLAYERS for w in supporting)


def _named_only_by_a_team_word(teams: names.TeamIndex, question: str, player: str) -> bool:
    """Whether every word of ``player``'s name the question holds also names a
    team - "magic" in "magic vs nets" is the Orlando Magic, not Magic Johnson,
    and "boston" is the Celtics before it is Brandon Boston Jr."""
    asked = {w.casefold() for w in _words(question)}
    supporting = [w for w in _words(player) if w.casefold() in asked]
    return bool(supporting) and all(_team_named(teams, w) is not None for w in supporting)


def _shares_word(one: str, other: str) -> bool:
    return bool({w.casefold() for w in _words(one) if len(w) >= 3} & {w.casefold() for w in _words(other) if len(w) >= 3})
