"""Who a question is about, read once from its own words.

A question's subject - a player, two players, a team, two teams, a position
group, or everyone - is read ONCE, from the question's own spans, before
any reader runs, so that every name a reader reads is a span of the
question and the model's names (the normalizer's,
:mod:`association.query.normalizer`) are a hint, never a source (AGENTS.md,
"A model invents names, and an invented name resolves"). It is one reading
because two readings of one question's subject disagree, and each would
repair what the other got wrong. The rules below are each named for the
question that needed it.

:func:`read_subject` is pure: it takes the model's names as the parser
classified them (``parse._model_reading``: the players and the team the
model copied, a second team as the opponent) and returns a :class:`Subject`
with its evidence. :func:`settle_subject` writes what the words ask once the
grammar's rules have settled it, and :func:`apply_subject` - in the parser's
last step - writes the reading into the Reading's typed filters
(:class:`~association.query.reading.Filtered`: the subject, the companions,
the tenure), each write a recorded
:class:`~association.query.decisions.Decision`: the reading is the one
writer of who a question is about.

.. versionadded:: 4.4.0
"""

from __future__ import annotations

import functools
import gzip
import itertools
from dataclasses import dataclass, replace
from importlib import resources
from typing import TYPE_CHECKING, NamedTuple, get_args

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
    checked_players,
    find_players,
    find_teams,
    former_franchise,
    near_team,
    team_abbreviations,
    team_columns,
    teammate_names,
    teams_named_by_word,
)
from association.query.lexicon import season_from_text, season_named
from association.query.measures import THRESHOLD_STAT_NAMES
from association.query.reading import (
    GAME_HIGHS,
    GAMES_COUNTED,
    LINE_RECORD,
    LINE_RUNS,
    NETPOINTS_FINGERPRINT,
    OWN_TEAM_RESTORABLE_ASKS,
    PLAYER_COMPARISON,
    PLAYER_LINE,
    PLAYER_LOG,
    PLAYER_RANKING,
    PLAYER_REQUIRED_ASKS,
    PLAYER_SPLITS,
    PRESENCE_SPLIT,
    SEASON_HISTORY,
    SHOT_CHART,
    SHOT_DISTANCE,
    SUBJECT_RESTORABLE_ASKS,
    TEAM_GAME_HIGHS,
    TEAM_LINE,
    TEAM_MEETINGS,
    TEAM_OUTLOOK,
    TEAM_RANKING,
    TEAM_RECORD,
    TEAM_SEASON_HISTORY,
    Claim,
    Companion,
    Cuts,
    Filtered,
    LeftOut,
    Line,
    Measure,
    PointShape,
    Predicate,
    SubjectKind,
    Window,
    key_path,
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

#: What the words ask a "<team> when <player> reaches N" question under
#: (``None`` the grammar's "no shape") - each would answer the
#: player's or the team's own line instead of the team's record under the
#: condition.
_RECORD_WHEN_PARENTS: frozenset[PointShape | None] = frozenset({PLAYER_LINE, PLAYER_SPLITS, PLAYER_LOG, TEAM_LINE, TEAM_RECORD, PRESENCE_SPLIT, None})

#: Why a team's question with a companion's line is a record over a line
#: (``LINE_RECORD``)
#: (:func:`child_named`), wherever that is said: the reading's own
#: decision, and the parser's about what the words ask.
_TEAM_RECORD_WHEN = "a team's record in the games a player named beside it reached a line"

_NOT_A_TEAM: frozenset[str] = SUBJECT_KINDS - {"team", "teams", "team_players"}
_PLAYER_OR_PAIR: frozenset[str] = frozenset({"player", "pair"})
_PLAYER_RELATION_PARENTS: frozenset[PointShape | None] = frozenset({PLAYER_LOG, PLAYER_LINE, PLAYER_RANKING, None})

#: The children the question's own words assign, gated on the subject's
#: kind - in precedence order, first match wins: (the child's key, the
#: words that name it, the kinds it can be about, the parents' keys it is
#: assigned under).
#:
#: Each child is a fixed point on a relation whose parent (a player's log,
#: his line, the league's ranking, a team's record) the parent grammar
#: names; the words that tell the child from the parent are the ones
#: below, and the subject's kind is what keeps a word grammar off the other
#: relation's questions - "how many times did the 76ers play boston" is two
#: TEAMS meeting, not a count of a player's games; "who lead the league in
#: avg 3 point distance" names no PLAYER to measure. Measured over 352
#: recorded questions with what each asked (``~/association-research/intent-shrink/``):
#: with the gate, no question asking another shape moves. The
#: precedence settles the two that overlap: "career most points in a game"
#: is a single-game high before it is a count, and "how many 20+ point games
#: ... in the past two seasons" a count before it is a history.
_CHILD_GRAMMARS: tuple[tuple[PointShape, lexicon.Ask, frozenset[str], frozenset[PointShape | None]], ...] = (
    # "per game" is an average, never one game; "single game" is one game
    # with an article or without (the lexicon's CHILD_SINGLE_GAME_HIGH).
    (GAME_HIGHS, lexicon.CHILD_SINGLE_GAME_HIGH, _NOT_A_TEAM, _PLAYER_RELATION_PARENTS),
    # A team's own best single game, or every team's ("most 3s made in a
    # game by the 76ers", "most 3s made in a single game by a team"): under
    # the team's own parents, and the league's teams' ranking for the
    # league's (ISSUES.md #364). A team
    # named as its players' team is ``team_players`` and the players' high.
    (TEAM_GAME_HIGHS, lexicon.CHILD_SINGLE_GAME_HIGH, frozenset({"team", "everyone"}), frozenset({TEAM_LINE, TEAM_RECORD, TEAM_RANKING})),
    (SHOT_DISTANCE, lexicon.CHILD_SHOT_DISTANCE, _PLAYER_OR_PAIR, _PLAYER_RELATION_PARENTS | {SHOT_CHART}),
    (LINE_RUNS, lexicon.CHILD_STREAK, SUBJECT_KINDS, frozenset({TEAM_RECORD, TEAM_LINE, TEAM_RANKING, TEAM_OUTLOOK, TEAM_MEETINGS}) | _PLAYER_RELATION_PARENTS),
    (LINE_RECORD, lexicon.CHILD_RECORD_WHEN_LINE, SUBJECT_KINDS, frozenset({TEAM_RECORD, TEAM_LINE, PRESENCE_SPLIT, TEAM_MEETINGS}) | _PLAYER_RELATION_PARENTS),
    # A player's games won or lost: his team's record in the games he
    # played, which is a record over a line read with no threshold ("how many
    # playoff games has embiid won?"). A player only: a TEAM's games won are
    # the team's record's own question.
    (
        LINE_RECORD,
        lexicon.CHILD_RECORD_WHEN_GAMES_WON,
        frozenset({"player"}),
        frozenset({TEAM_RECORD, TEAM_LINE, TEAM_OUTLOOK, PRESENCE_SPLIT, TEAM_MEETINGS}) | _PLAYER_RELATION_PARENTS,
    ),
    # Under the with/without split too: "how many 30 point games did maxey
    # have without embiid" is his count over the games Embiid missed, which
    # the count honors (the companion cell), not his line with and without
    # him (ISSUES.md #394).
    (GAMES_COUNTED, lexicon.CHILD_THRESHOLD_COUNT, _NOT_A_TEAM, _PLAYER_RELATION_PARENTS | {PRESENCE_SPLIT}),
    (SEASON_HISTORY, lexicon.CHILD_PLAYER_HISTORY, _PLAYER_OR_PAIR, _PLAYER_RELATION_PARENTS),
    # A team's season line season by season, by the same words a player's
    # history is named by ("raptors last 5 seasons", "bulls ppg by season"):
    # under a team's season line alone (ISSUES.md #372). A season line over a range the words name another
    # way ("2009 to 2023") is keyed so by the point reader.
    (TEAM_SEASON_HISTORY, lexicon.CHILD_PLAYER_HISTORY, frozenset({"team"}), frozenset({TEAM_LINE})),
    (
        PLAYER_SPLITS,
        lexicon.CHILD_PLAYER_SPLITS,
        _PLAYER_OR_PAIR,
        # Two teams' meetings and a team's record too: "show me Embiid's splits against
        # boston" arrived as the two teams meeting, Embiid dropped (day5).
        frozenset({PRESENCE_SPLIT, PLAYER_COMPARISON, TEAM_MEETINGS, TEAM_RECORD}) | _PLAYER_RELATION_PARENTS,
    ),
)

KIND_ASSIGNED_ASKS: frozenset[PointShape] = frozenset(child for child, _, _, _ in _CHILD_GRAMMARS)
"""The shapes :func:`child_named` assigns from the question's words, gated
on the subject's kind (:data:`_CHILD_GRAMMARS`), under a parent the
grammar named - the same route ``grammar.CODE_ASSIGNED_ASKS`` takes for a
coach and a quarter, one step later, where the subject's kind is known.
The grammar's rules (:func:`association.query.grammar.settle`) run under
the child and may decline it, and the taggers read its filters from the
words after them.

.. versionadded:: 5.0.0

.. versionchanged:: 6.0.0
   ``KIND_ASSIGNED_INTENTS``, the children's names, until Phase 3, step 4.
"""


class Applied(NamedTuple):
    """What :func:`apply_subject` did: the typed filters with the reading
    written into them, the decisions recorded, the model's names the
    question never held that nothing could replace (the caller refuses by
    name), and what the words ask as the subject's shape settled it - the
    key handed in where the shape fits it.

    .. versionadded:: 5.0.0
    """

    scope: Filtered
    decisions: list[Decision]
    dropped: list[str]
    asked: PointShape | None


@dataclass(frozen=True)
class Subject(reading.Subject):
    """Who a question is about, as the subject reading read it: the typed
    subject (:class:`~association.query.reading.Subject` - the kind, the
    players, the teams, the position group) and what only the parser's
    settling needs beside it - the opponent and the player's own team the
    cuts tagger takes, the companions, the evidence, the names the model
    invented or filed as filler, the season named, what the words ask and
    why, the characters each finding was read from.

    ``kind`` is one of :data:`SUBJECT_KINDS`. ``players`` and ``teams`` are
    the names as the question or the model gave them - resolution to an
    entity stays with :mod:`association.query.entities` - except that a
    model name the question's own span spells differently carries the
    question's spelling (a bare "Jokic" completed, "Jaylen Huff" cut back to
    the "Jay Huff" typed; see :func:`_spellings`). ``opponent`` is a
    team the subject is set against - the question's own "vs the Pistons",
    or the model's second team where it names a team the question holds
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
       ``named_season``, ``intent`` and ``intent_reason`` added (``intent``
       gone and ``intent_reason`` named ``asked_reason`` since 6.0.0, the
       Phase 4, step 2 that deleted the label). ``opponent``
       also reads the router's slot where the question supports it;
       ``own_team`` needs the player named before the "for <team>" phrase.

    .. versionchanged:: 6.0.0
       A :class:`~association.query.reading.Subject` - the typed value the
       Reading carries (``Reading.who``, Phase 3, step 2; the ``Scope`` until
       Phase 3, step 4, slice (b)) - with the parser's fields beside it:
       ``kind``, ``players``, ``teams`` and ``position`` are the typed
       subject's, as read.
    """

    opponent: str | None = None
    own_team: str | None = None
    companions: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    #: The model's player names - the players it copied and a player
    #: filed as the ``opponent`` - that the question never held and that are
    #: not teams: fiction, as far as the question can tell ("Jusuf Nurkic" on
    #: "compare sga and embiid"). :func:`apply_subject` replaces or reports
    #: each, whatever kind the subject is.
    invented: tuple[str, ...] = ()
    #: The model's player names the question DOES hold, as the model spelled
    #: them: what :func:`apply_subject` counts a spare name against when it
    #: replaces an invented one from the question's own names (the question's
    #: names the model never copied are the spares; since the parser keeps an
    #: invented name off the players, ISSUES.md #373).
    supported: tuple[str, ...] = ()
    #: The season the question ITSELF names, if one - what the tenure rule
    #: reads ("for Miami" with no season is a career, not this season), and
    #: never a defaulted "current season".
    named_season: int | None = None
    #: What the subject's shape settles the words to ask: a team's record in
    #: the games a companion reached a line (``LINE_RECORD``), or a child the
    #: question's own words name for this kind of subject
    #: (:data:`KIND_ASSIGNED_ASKS`); the key handed in everywhere else.
    #:
    #: .. versionadded:: 6.0.0
    asked: PointShape | None = None
    #: Why ``asked`` is not the parent's, in a sentence - the words that
    #: name a child ("the words 'in a single game' name
    #: player_games/rows/measure", the key as
    #: :func:`~association.query.reading.key_path` names it), or a team's
    #: record in the games a companion reached a line - and ``None`` where
    #: it is the parent's own. What the parser's decision about what the
    #: words ask says (:func:`~association.query.parse.read_route`).
    #:
    #: .. versionchanged:: 6.0.0
    #:    ``intent_reason`` until Phase 4, step 2, which named the key by
    #:    the page's label (``single_game_high``), as the deleted ``intent``
    #:    beside it held it.
    asked_reason: str | None = None
    #: Every companion with the role the question states
    #: (:class:`~association.query.reading.Companion`); ``companions`` above
    #: is their names.
    conditions: tuple[Companion, ...] = ()
    #: The characters each companion was read from - the keyword, the
    #: names and the role's words, a reached one's line included
    #: (:class:`~association.query.reading.Claim`, ``what`` ``"companion"``),
    #: which ride onto ``Reading.claims``. The subject's own names
    #: are the subject slice's to claim.
    #:
    #: .. versionadded:: 6.0.0
    claims: tuple[Claim, ...] = ()
    #: Where each companion's phrase stands - the keyword to the last word
    #: the reading took (``(start, end)``) - with the role read from it:
    #: what the parent grammar reads in place of the words, typed
    #: (``parse.grammar_text``), and the rules are handed as a reached
    #: line's "when" clause. Positions in the text, like :attr:`claims`,
    #: which the probe cuts to the words the reading needed; these are the
    #: phrases whole, before it.
    #:
    #: .. versionadded:: 6.0.0
    companion_phrases: tuple[tuple[int, int, str], ...] = ()

    #: The model's player names that are no name at all - a rank word or
    #: a phrase of the question ("most", "most 30+ point games";
    #: :func:`_not_a_name`), or a name the question holds only by a TEAM's
    #: word ("magic vs nets" is not Magic Johnson;
    #: :func:`~association.query.entities._named_only_by_a_team_word`) -
    #: which :func:`apply_subject` takes out of the typed subject rather than
    #: leaves for a reader to resolve ("No player found matching 'most'").
    filler: tuple[str, ...] = ()


# The companions' words - the phrases a companion is named in ("without X",
# "with X", "when X", "in games X started", the words after "vs"), what ends
# a name, the absence, start and bench words, a compare verb's "with" that
# names no companion - are the lexicon's (COMPANION,
# COMPANION_IN_GAMES, COMPARED_WITH, VERSUS_PHRASE, NAME_STOPWORDS,
# ABSENCE_WORDS, CONDITION_STARTED, CONDITION_BENCH, CONDITION_ABSENT, the
# line's THRESHOLD), each with its reason beside it; the companion itself is
# the reading's typed value (reading.Companion, with a reached one's Line).


@functools.lru_cache(maxsize=1)
def _dictionary() -> frozenset[str]:
    """The ordinary words: the lowercase entries of a word list shipped in
    this package (a capitalized entry is a proper noun - "Jordan" is a name
    there, "best" a word), plus the month abbreviations.

    Shipped rather than read from ``/usr/share/dict/words``: a machine
    without that file reads "Best record from 2010-11 to 2018-19" as a
    question about Travis Best, so the same question would have a different
    answer on a machine without it - GitHub's runner is one. ``words.txt.gz`` is Debian's
    ``wamerican`` 2020.12.07 (SCOWL, notice in ``words.COPYRIGHT``) reduced to
    the entries this function keeps (lowercase first letter, no
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
    letters). Not plain Levenshtein, which charges a transposition twice, so
    "jokci stats" would not support Nikola Jokic while the index reads
    "jokci" as him."""
    return DamerauLevenshtein.distance(a, b)


def question_supports(name: str, question: str) -> bool:
    """Whether ``question`` holds ``name``: any one word of it, a near
    spelling of one (three or more letters) within
    :func:`association.query.entities._edit_budget` (none for a short word,
    one edit to six letters, two beyond - a model may correct a typo the
    question made silently, and "embid" is Embiid), a curated nickname for
    the player, or its initials ("SGA", "kd").

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


def _is_a_team(index: names.NameIndex, name: str) -> bool:
    """A name the model handed back that is a team and no player (a model
    filed "Alamhamed Embiid" among the teams and "Boston Celtics" among the
    players)."""
    return bool(find_teams(index.teams, name)) and not find_players(index.players, name)


def _team_abbreviation(teams: names.TeamIndex, question: str) -> str | None:
    """A team named by its abbreviation, in capitals ("PHI record 2026")."""
    rows = {str(abbr).casefold(): str(name) for abbr, name in team_abbreviations(teams).items()}
    for w in _words(question):
        if len(w) == 3 and w.isupper() and w.casefold() in rows:
            return rows[w.casefold()]
    return None


def _teams_by_word(teams: names.TeamIndex, question: str) -> set[str]:
    """Every team a word of the question names by itself - a singular, a
    one-word spelling, a nickname or a whole word of exactly one team's
    name (:func:`team_named_in`, word by word)."""
    found: set[str] = set()
    question = lexicon.SINGULAR_NAMING_NO_TEAM.sub(_team_word_blank, question)
    for run in lexicon.TEAM_SPELLING_RUN.findall(question.lower()):
        team = lexicon.TEAM_SINGULARS.get(run.removesuffix("'s").rstrip("'")) or team_named_in(teams, run) or _former_name(run)
        if team is not None:
            found.add(team)
    return found


def _team_word(teams: names.TeamIndex, question: str) -> str | None:
    """The team the question names as a word: a singular or one-word
    nickname first, then :func:`~association.query.entities.team_named_in`
    (the roster table by whole word, the plural nicknames), then an
    abbreviation. A singular in words that name no team ("net points",
    "martin luther king") is none."""
    words = _words(lexicon.SINGULAR_NAMING_NO_TEAM.sub(_team_word_blank, question).lower())
    for w in words:
        if w in lexicon.TEAM_SINGULARS:
            return lexicon.TEAM_SINGULARS[w]
    return team_named_in(teams, question) or _team_abbreviation(teams, question) or next((name for name in map(_former_name, words) if name), None)


def _former_name(word: str) -> str | None:
    """The franchise a word names by a name no team holds today
    ("supersonics", "bullets", :func:`~association.query.entities.former_franchise`),
    as that name - or None."""
    era = former_franchise(word.removesuffix("'s").rstrip("'"))
    return era.name if era is not None else None


def _team_word_blank(match: re.Match[str]) -> str:
    """``match``'s characters as spaces, so every later offset holds."""
    return " " * len(match.group(0))


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


def _question_players(index: names.NameIndex, question: str, routed: list[str], teams_here: set[str]) -> list[str]:
    """Players the question names, minus the spans that are not players in
    THIS question: the word that names a team here ("boston" after "vs" is
    the Celtics, not Brandon Boston Jr.; "magic" is Orlando, not Magic
    Johnson), and an ordinary word that happens to be somebody's whole name
    ("best" = Travis Best, "head" = Luther Head, "pointer", "jan") unless
    the model read it as that player too or the question uses the
    player's nickname."""
    q_words = [w.casefold() for w in _words(question)]
    dictionary = _dictionary()
    kept: list[str] = []
    for name in dict.fromkeys(players_named_in(index.players, question)):
        matched = [w for w in (x.casefold() for x in _words(name)) if w in q_words]
        if not matched:
            kept.append(name)  # a nickname key the question used: "kat", "sga"
            continue
        if teams_here and any(len(w) >= 4 and (lexicon.TEAM_SINGULARS.get(w) or team_named_in(index.teams, w)) in teams_here for w in matched):
            continue
        if (
            all(w in dictionary for w in matched)
            and not any(question_supports(name, p) for p in routed)
            and not _by_nickname(name, question)
            and not _question_players_in_full(index.players, name, q_words)
        ):
            continue
        kept.append(name)
    return kept


def _question_players_in_full(players: names.PlayerIndex, name: str, q_words: list[str]) -> bool:
    """Whether the question writes a player's name whole, two words or
    more in a row: "miles bridges", "derrick white" are players though each
    word is an ordinary one (:func:`_question_players`' dictionary rule is
    for a single word - "best" is Travis Best). ``name`` must be a player's
    name on record, never a span read as a near spelling ("most most")."""
    words = [w.casefold() for w in _words(name)]
    if len(words) < 2 or not any(q_words[i : i + len(words)] == words for i in range(len(q_words) - len(words) + 1)):
        return False
    return any(entity.name.casefold() == name.casefold() for entity in find_players(players, name))


def _merge_names(model_named: list[str], question_named: list[str]) -> tuple[str, ...]:
    """The model's names the question supports (in the question's own
    spelling, and once each - a player filed as both a player and the
    ``opponent`` is one person), plus the names the question gives that are
    not one of them already - the model's completion of a surname the
    question gives is the same person, not a second one. The model's spelling leads on purpose:
    :func:`players_named_in` reads "kareem stats
    vs bob lanier" as naming Kareem Rush and Chaz Lanier, two real players by
    whole word and neither the one asked about (ISSUES.md #123), and the
    model's "Kareem Abdul-Jabbar" was the better reading of "kareem"."""
    out: list[str] = []
    for name in (*model_named, *question_named):
        if not _same_person(name, out):
            out.append(name)
    return tuple(out)


def _read_opponent(index: names.NameIndex, question: str, scope: Filtered, season: int | None) -> str | None:
    """The team the subject is set against: the team after "vs" / "against"
    in the question's own words, whenever there is one - "duren v nets 1h
    gameloh" arrived as ``opponent="New Jersey Nets"``, which resolves to no
    team, "tatum vs lakers" once as the Trail Blazers, a real team the
    question never wrote, and "magic vs nets" with the two sides swapped;
    the question's own wins each time. With no "vs" to read, the model's
    second team (the ``opponent`` the parser hands in) stands where it names a team the question holds
    (:func:`~association.query.entities._team_grounded`): "76ers 4th
    quarter points against boston" is the team's quarter against Boston. And
    with a "vs" whose word resolves to nothing, the model's team stands
    as the opponent where the question holds it nowhere else - its reading
    of that word. A word after the "vs" that is a player's name the question
    names is the player, not a team it clips: "kevin garnett vs tim duncan
    games" read "tim" as the Timberwolves (:func:`_team_after_versus`)."""
    versus = _team_after_versus(index.teams, question, season, names=players_named_in(index.players, question))
    if versus is not None:
        return versus.name
    held = scope.cuts.opponent
    held_team = _team_named(index.teams, held, season) if isinstance(held, str) and held.strip() else None
    if held_team is not None and _team_grounded(index.teams, question, held_team) and not _named_as_own(index.teams, question, held_team, season):
        return held_team.name
    # "karl towns stats vs netslast 5 games": the "vs" names a word nothing
    # resolves, and the model read it as a team (the parser's `team`) - a
    # team the question never holds otherwise, so the model's reading of
    # that word is the one there is.
    team = _team_named(index.teams, scope.who.team, season) if held_team is None else None
    if team is not None and lexicon.AGAINST_PHRASE.search(question) and not _team_grounded(index.teams, question, team):
        return team.name
    return _read_opponent_near(index, question)


def _read_opponent_near(index: names.NameIndex, question: str) -> str | None:
    """The word a versus word leads, as typed, where it is a near spelling
    of exactly one team and no player's (:func:`~association.query.entities.near_team`):
    "gui last 5 games vs sours" is the Spurs' games, and was his last 5
    games against anybody, the word read by nothing (ISSUES.md #247). Kept
    as typed - names arrive as typed - so the relation's team resolution
    reads it and the answer says so
    (:func:`~association.query.entities.resolved_team`). Not a word that is
    a whole word of some player's name ("vs hawes" is Spencer Hawes, one
    edit from the Hawks). A word near a player as well as a team ("vs
    houson": the Rockets, Allan Houston) is kept too, and the resolution
    asks which (:func:`~association.query.entities.resolved_team`): read
    as neither, the answer was the subject's line over every opponent, the
    word named nowhere in it (ISSUES.md #247)."""
    for match in lexicon.AGAINST_PHRASE.finditer(question):
        words = _team_words(match.group(1))[:1]
        if not words:
            continue
        word = words[0]
        if near_team(index.teams, word) is None or _exact_name_span(index.players, [word]):
            continue
        return word
    return None


def _named_as_own(teams: names.TeamIndex, question: str, team: Entity, season: int | None) -> bool:
    """Whether ``team`` is the one the question names after "for" - "show me
    splits for the sixers when maxey scores 20+ points" arrived with the
    76ers as the ``opponent`` beside an invented Joel Embiid (day5), and the
    reading took the model's word for it, leaving the question with no
    team as its subject. A "for <team>" with no "vs" anywhere is the
    subject's side, never the other one."""
    own = _team_after_for(teams, question, season)
    return own is not None and own[0].id == team.id and not lexicon.AGAINST_PHRASE.search(question)


def _opponent_player(index: names.NameIndex, scope: Filtered) -> str | None:
    """The ``opponent`` handed in when it names a player and not a team ("jay
    huff game log vs Embiid" once arrived from the model with Jokic there): a
    name to check against the question like the subject's own, never read
    as a team the subject is set against. A team opponent is a narrowing,
    not a subject."""
    opponent = scope.cuts.opponent
    if not isinstance(opponent, str) or not opponent.strip() or find_teams(index.teams, opponent) or not find_players(index.players, opponent):
        return None
    return opponent


def _routed_names(index: names.NameIndex, scope: Filtered, question: str, opponent_player: str | None) -> tuple[list[str], list[str]]:
    """The model's player names - the players handed in, a player filed as
    the ``opponent`` and one filed as the team - split into the ones the
    question supports and the ones it never held, minus any that is a team
    (a model filed "Boston Celtics" as a player; a team is a narrowing,
    not an invention)."""
    team_slot = _team_slot_player(index, scope)
    routed = [p for p in _routed_player_slots(scope) + ([opponent_player] if opponent_player else []) + ([team_slot] if team_slot else []) if not _is_a_team(index, p) and not _not_a_name(p)]
    # A name the question holds only by a word its grammar holds there (a
    # month in a date, "best") is one the question never named.
    supported = [p for p in routed if question_supports(p, question) and not _named_only_by_a_common_word(question, p)]
    return supported, [p for p in routed if p not in supported]


def _filler_names(index: names.NameIndex, scope: Filtered, question: str) -> tuple[str, ...]:
    """The model's player names that are no name (:func:`_not_a_name`) or
    that the question holds only by a team's word - :attr:`Subject.filler`."""
    return tuple(p for p in _routed_player_slots(scope) if _not_a_name(p) or (not _is_a_team(index, p) and _named_only_by_a_team_word(index.teams, question, p)))


def _not_a_name(text: str) -> bool:
    """A model's player name that is no name at all: a position phrase ("shooting
    guard" on "highest 3 point percentage ... by a shooting guard", F056 - the
    position-group subject, which :attr:`Subject.position` carries) or a
    filler word ("player" on "Most points in 15th season played", F099).
    Neither is a player to read, replace or report."""
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


def _team_slot_player(index: names.NameIndex, scope: Filtered) -> str | None:
    """The model's team when it names a player and no team: "Podziemski
    game log without curry" once arrived as ``team='Podziemski'``, and "Will
    Riley last 5 game s" as ``team='Riley'`` (a bare fragment, three
    players) - the subject, filed as the team."""
    team = scope.who.team
    if team is None or _team_named(index.teams, team) is not None or not find_players(index.players, team):
        return None
    return team


def _spellings(players: names.PlayerIndex, question: str, routed: list[str]) -> dict[str, str]:
    """The question's own spelling of each model name it supports, resolved
    from the question's words anchored at the model's
    (:func:`~association.query.entities._question_derived_player`): a bare
    "Jokic" completed, "Jaylen Huff" cut back to the "Jay Huff" typed, "Grady
    Dickinson" put right as Gradey Dick, and "Stephen Curry" for a "Seph
    Curry" the question itself misspelled - a typo of the QUESTION's, which
    no whole-word match finds. Anchored on purpose: a whole-word match reads
    "kareem stats vs bob lanier" as Kareem Rush and Chaz Lanier, while the
    anchored span settles neither and the model's spelling stands."""
    out: dict[str, str] = {}
    for name in routed:
        derived = question_derived_player(players, question, name)
        if derived is not None and derived.name != name and not _named_only_by_a_common_word(question, derived.name):
            # A respelling read from a word the grammar holds in its place
            # is none: "curry best shooting night" never spells the model's
            # "Stephen Curry" as Travis Best (ISSUES.md #486).
            out[name] = derived.name
    return _spellings_owned(out)


def _spellings_owned(spelled: dict[str, str]) -> dict[str, str]:
    """``spelled`` without a spelling one model name took from ANOTHER's
    words: anchored at "sga" in "compare sga embiid", the window reaches
    "embiid" and reads Joel Embiid - the player "embiid" itself spells as -
    so "sga" read as him, and the pair came out in the other order with its
    evidence saying "question spells 'sga' as 'Joel Embiid'". Where two names
    spell as one player, the one whose own word is in that player's name
    keeps it and the other keeps its own spelling, as typed."""
    by_player: dict[str, list[str]] = {}
    for name, player in spelled.items():
        by_player.setdefault(player, []).append(name)
    kept = dict(spelled)
    for player, names_ in by_player.items():
        own = [name for name in names_ if set(_words(name.casefold())) & set(_words(player.casefold()))]
        if len(names_) > 1 and own:
            for name in names_:
                if name not in own:
                    del kept[name]
    return kept


def _conditions(question: str, players: tuple[str, ...], scope: Filtered) -> tuple[Companion, ...]:
    """The players whose ROLE the question states beside the subject -
    "without X", "with X on the floor", "when X scored 20+" - each with that
    role (:class:`~association.query.reading.Companion`), read from the
    question's own words, never from the model (which once filed Embiid as
    absent on "Embiid's record against Boston").
    The companions the reading handed in already carries (the model names
    none; a caller's own reading may) are consulted only for one the
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


def _conditions_read(question: str, players: tuple[str, ...], scope: Filtered) -> tuple[tuple[Companion, ...], tuple[Claim, ...], tuple[tuple[int, int, str], ...]]:
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
    phrases: list[tuple[int, int, str]] = []
    for match in _companion_phrases(question):
        word, text = match.group(1).lower(), match.group(2)
        predicate, line, role_end = _condition_role(word, text)
        names = _companion_names(text, players, scope, predicate)
        at = _conditions_read_name_at(text, names)
        if at and lexicon.COMPANION_CLAUSE_BREAK.search(text[:at]):
            # The role is read from the companion's own words on where a
            # clause break stands right before his name: the line before it
            # is somebody else's - "when they score 120 points when embiid
            # starts" is the team's 120 and Embiid's start, never Embiid's
            # 120 points (ISSUES.md #246).
            predicate, line, role_end = _condition_role(word, text[at:])
            role_end = None if role_end is None else at + role_end
            names = _companion_names(text, players, scope, predicate)
        clauses = _conditions_read_clauses(question, match, players, scope)
        if clauses is not None:
            # Each name with the role its own clause states: "when AD plays
            # and lebron doesn't", "when AD plays without lebron" (ISSUES.md
            # #479) - one role for the phrase read the denied one as playing.
            read, role_end = clauses
            new = [c for c in read if not any(_same_person(c.player, [f.player]) for f in found)]
        else:
            new = [Companion(player=name, predicate=predicate, line=line) for name in names if not any(_same_person(name, [c.player]) for c in found)]
        if new:
            found.extend(new)
            end = match.start(2) + _read_to(text, [c.player for c in new], role_end)
            claims.append(Claim(match.start(), end, "companion"))
            phrases.append((match.start(), end, predicate))
    versus, versus_claims = _versus_companions(question, players, scope, found)
    found.extend(versus)
    claims.extend(versus_claims)
    phrases.extend((claim.start, claim.end, "played") for claim in versus_claims)
    return tuple(found), tuple(claims), tuple(phrases)


def _conditions_read_clauses(question: str, match: re.Match[str], players: tuple[str, ...], scope: Filtered) -> tuple[list[Companion], int] | None:
    """The companions of one phrase read clause by clause, where its names
    do not share one role - or None where they do, and the phrase's one
    role stands for every name.

    A phrase's words split into chunks at each joiner ("and", a comma) and
    at a companion keyword inside them ("when AD plays WITHOUT lebron"); a
    chunk that states no role of its own (a bare name: "Tatum" in "without
    Tatum and Brown", "Embiid" in "when Embiid and George start") shares the
    role of the chunk after it. Each clause's role is read from its own
    words under its own keyword (:func:`_condition_role`), and a denial in
    them (:data:`~association.query.lexicon.COMPANION_DENIED`: "lebron
    doesn't", "jayson tatum did not") makes a clause that would read as
    playing an absence. The clauses are used only where two named clauses
    read different roles or a denial turned one: everywhere else - one
    clause above all, where the phrase's own role reader already reads "when
    maxey was not playing" - the phrase reads as it always did.

    Returns the companions with how far into the phrase's words the roles
    reached."""
    text, base = match.group(2), match.start(2)
    clauses = _conditions_read_clauses_split(match.group(1).lower(), text)
    read: list[tuple[list[str], Predicate, Line | None]] = []
    ends: list[int] = []
    turned = False
    for word, start, end in clauses:
        words = text[start:end]
        predicate, line, role_end = _condition_role(word, words)
        denied = lexicon.COMPANION_DENIED.search(question, base + start)
        if predicate == "played" and denied is not None and denied.start() < base + end and _conditions_read_clauses_bare_before(text[start : denied.start() - base]):
            # The denial's own words may run past the phrase ("did" ends a
            # phrase of "in games ... jayson tatum did not"), so it is
            # searched in the question from the clause on.
            predicate, line, role_end, turned = "absent", None, denied.end() - base - start, True
        names = _companion_names(words, players, scope, predicate)
        if names:
            read.append((names, predicate, line))
            ends.append(start + (role_end if role_end is not None else len(words.rstrip())))
    if len(read) < 2 or (not turned and len({(predicate, line) for _, predicate, line in read}) < 2):
        return None
    companions: list[Companion] = []
    for names, predicate, line in read:
        companions += [Companion(player=name, predicate=predicate, line=line) for name in names if not any(_same_person(name, [c.player]) for c in companions)]
    return companions, max(ends)


def _conditions_read_clauses_split(word: str, text: str) -> list[tuple[str, int, int]]:
    """A companion phrase's words as clauses: ``(keyword, start, end)`` in
    the words, split at each joiner and at a companion keyword inside them,
    a chunk stating no role of its own joined to the one after it - but
    never across a companion keyword, which states the next names' role
    itself ("with jaylen brown and without jayson tatum": Brown is the
    "with"'s)."""
    chunks: list[tuple[str, int, int, bool]] = []
    keyword, start, end, led = word, 0, 0, False
    for piece in lexicon.NAME_PIECES.finditer(text):
        nested = lexicon.COMPANION.match(text, piece.start())
        keyed = nested is not None and nested.end(1) == piece.end()
        if piece.group(0).casefold() in lexicon.NAME_JOINERS or keyed:
            if end > start:
                chunks.append((keyword, start, end, led))
                led = False
            if keyed:
                keyword, led = piece.group(0).casefold(), True
            start = end = piece.end()
            continue
        end = piece.end()
    if end > start:
        chunks.append((keyword, start, end, led))
    clauses: list[tuple[str, int, int]] = []
    held: tuple[str, int, int] | None = None
    for own, at, end, keyed in chunks:
        if keyed and held is not None:
            clauses.append(held)
            held = None
        keyword, start = (held[0], held[1]) if held is not None else (own, at)
        if _conditions_read_clauses_states_role(keyword, text[start:end]):
            clauses.append((keyword, start, end))
            held = None
        else:
            held = (keyword, start, end)
    if held is not None:
        clauses.append(held)
    return clauses


def _conditions_read_clauses_states_role(word: str, words: str) -> bool:
    """Whether a chunk of a companion phrase states a role of its own - a
    role word or a denial, a word no name holds, a number - rather than
    being a bare name that shares the next chunk's."""
    if _condition_role(word, words)[0] != "played" or lexicon.COMPANION_DENIED.search(words):
        return True
    pieces = lexicon.NAME_PIECES.findall(words)
    return any(not lexicon.NAME_SHAPED.fullmatch(piece) or piece.casefold() in lexicon.NAME_STOPWORDS for piece in pieces)


def _conditions_read_clauses_bare_before(words: str) -> bool:
    """Whether the words of a clause before its denial are a name alone -
    "lebron" in "lebron doesn't" - so the denial is that name's: "AD plays
    but lebron doesn't" holds "plays" before it, and the denial is not
    AD's."""
    return not any(not lexicon.NAME_SHAPED.fullmatch(piece) or piece.casefold() in lexicon.NAME_STOPWORDS for piece in lexicon.NAME_PIECES.findall(words))


def _conditions_read_name_at(text: str, names: list[str]) -> int:
    """Where in a companion phrase's words the first of ``names`` stands -
    the name as the text spells it, or the earliest of its words the text
    holds - or 0 where none stands in it (a carried name the phrase
    misspells)."""
    low = text.casefold()
    found = [at for name in names for part in (name, *name.split()) if len(part) >= 3 and (at := low.find(part.casefold())) >= 0]
    return min(found) if found else 0


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
            # A name the text spells in part ("lebron" for LeBron James, a
            # surname alone): as far as its longest word the text holds,
            # the whole text where it holds none (a carried name the
            # phrase misspells).
            words = [(at, word) for word in sorted(name.casefold().split(), key=len, reverse=True) if (at := low.find(word)) >= 0]
            if not words:
                return len(text.rstrip())
            at, word = words[0]
            ends.append(at + len(word))
            continue
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

    A name is read by position because that does not depend on the model
    having copied it or on the word being nobody else's - "without
    curry" and "without Tatum and Brown" name ordinary words two or ten
    players share, which no lookup settles and the reader asks about."""
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


def _companion_names(text: str, players: tuple[str, ...], scope: Filtered, predicate: str = "played") -> list[str]:
    """Who one companion phrase names, in the phrase's order: each name it
    holds by position (:func:`_name_segments`) as the player the question
    is known to hold where one supports it, then the known players its
    words support that no position held ("when Embiid plays with Paul
    George"), then a companion the reading handed in already carries
    whose name the phrase misspells. A name by position that is no known player is kept as typed
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
# of the subject's games - a condition ("most points by
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


def versus_word_unread(players: names.PlayerIndex, teams: names.TeamIndex, question: str, reading: reading.Reading) -> str | None:
    """The word a versus word leads that names nothing - no team and no
    player on record, exactly or by a near spelling, and no ordinary word -
    where the reading read no opponent at all, or None: "steph curry vs
    phnx", "og annuby vs philidalephis stats" answered his games against
    everybody, the word read by nothing (ISSUES.md #247). The parser refuses
    by it (``opponent_not_read``) rather than answer the un-narrowed
    question.

    .. versionadded:: 6.0.0
    """
    if reading.cuts.opponent is not None or any(c.side == "opponent" for c in reading.companions):
        return None
    if reading.asked == NETPOINTS_FINGERPRINT:
        # A fingerprint draws the side it holds and says the other is
        # missing (compared_but_unmatched, the name_left_out decision).
        return None
    for match in lexicon.AGAINST_PHRASE.finditer(question):
        run = lexicon.LETTER_RUN.search(match.group(1))
        if run is None or run.start() != 0:
            continue
        if _versus_word_names_something(players, teams, run.group(0).casefold(), reading):
            continue
        return run.group(0)
    return None


def team_not_read(teams: names.TeamIndex, question: str, reading: reading.Reading) -> tuple[str, str] | None:
    """The first team the subject reading read on a player's question (one
    player or two) that the reading put nowhere - not the subject's team,
    not his tenure, not the opponent - as the words that name it and the
    team, or None: "lebron record vs t wolves" answered his splits over
    every game, "Mikal bridges logs at philly" his last ten, the team read
    by nothing and counted as read (ISSUES.md #436). The parser refuses by
    it (``team_not_read``) rather than answer the question without the
    team. A team placed under another name of its franchise ("the hornets
    in 2008", read as the New Orleans Hornets) is placed.

    .. versionadded:: 6.0.0
    """
    if reading.who.kind not in ("player", "pair") or reading.subject is None or reading.point is None:
        # No point, nothing answered over every game: the reading refuses
        # by its own cause ("steve kerr coaching record ... as a warrior").
        return None
    season = reading.span.season if reading.span.season is not None else reading.subject.named_season
    placed = [name for name in (*reading.who.teams, reading.cuts.tenure, reading.cuts.opponent) if name]
    tokens = _subject_claims_tokens(question)
    held = {word.casefold() for name in (*reading.who.players, *(c.player for c in reading.companions)) for word in _words(name)}
    # His own team too ("for the nets", "with okc"): where no tenure was
    # written for it, nothing reads it.
    named = (*reading.subject.teams, *((reading.subject.own_team,) if reading.subject.own_team else ()))
    # Every team a word of the question names, not only the one the
    # subject reading kept ("with Detroit Pistons and Atlanta Hawks").
    named = (*named, *sorted(_teams_by_word(teams, question) - set(named)))
    for team in named:
        if any(_team_not_read_same(teams, team, other, season) for other in placed):
            continue
        stretches = _subject_claims_team(teams, tokens, team, season)
        if stretches and all(_team_not_read_in_a_name(question[start:end], held) for start, end in stretches):
            # "magic johnson vs larry bird": the team's word is a word of a
            # player's name the reading holds, and names him.
            continue
        words = " ".join(question[start:end] for start, end in stretches) or team
        resolved = _team_named(teams, team, season)
        return words, resolved.name if resolved is not None else team
    return None


def _team_not_read_in_a_name(words: str, held: set[str]) -> bool:
    """Whether every word of ``words`` is a word of a name the reading holds."""
    each = [word.casefold() for word in _words(words)]
    return bool(each) and all(word in held for word in each)


def _team_not_read_same(teams: names.TeamIndex, team: str, other: str, season: int | None) -> bool:
    """Whether ``team`` and ``other`` are one franchise's names: the same
    team resolved, or one nickname under two cities' names (the Hornets of
    Charlotte and of New Orleans, the Nets of New Jersey and Brooklyn)."""
    if team.casefold() == other.casefold():
        return True
    one, two = _team_named(teams, team, season), _team_named(teams, other, season)
    if one is not None and two is not None and one.id == two.id:
        return True
    last = [word.casefold() for word in _words(team)[-1:]]
    return bool(last) and [word.casefold() for word in _words(other)[-1:]] == last


def _versus_word_names_something(players: names.PlayerIndex, teams: names.TeamIndex, word: str, reading: reading.Reading) -> bool:
    """Whether ``word`` after a versus word is something a reader reads: an
    ordinary or a short word, a nickname or a team's word, a word of a name
    the reading holds ("jokic vs wemby": the pair), a team or a player on
    record, exactly or by a near spelling (:func:`versus_word_unread`)."""
    if len(word) < 4 or word in _dictionary() or word in lexicon.NOT_A_SUBJECT or word in PLAYER_NICKNAMES or is_team_name(word):
        return True
    if any(question_supports(name, word) for name in (*reading.who.players, *(c.player for c in reading.companions))):
        return True
    if _team_named(teams, word, None) is not None or near_team(teams, word) is not None or teams_named_by_word(teams, word):
        return True
    return bool(_exact_name_span(players, [word]) or _fuzzy_name_span(players, [word], limit=1))


def _versus_companions_led_by_a_group(question: str, at: int) -> bool:
    """Whether a position group's word stands before ``at``, the versus word
    - "centers vs gobert": the group is the subject (ISSUES.md #365)."""
    return any(pattern.search(question[:at]) for pattern, _ in lexicon.POSITION_WORDS)


def _versus_companions(question: str, players: tuple[str, ...], scope: Filtered, found: list[Companion]) -> tuple[list[Companion], list[Claim]]:
    """The players after a versus word, as opponent-side ``played``
    companions - only where a player is named BEFORE the phrase (the
    subject; "celtics vs lebron" names no subject beside him) and the words
    ask for his games (:func:`_asks_for_games`). Any number: "giannis
    points vs lebron and curry" is the games both played against him. A
    team after "vs" names nobody here (it is the opponent's). Returns
    the companions and the characters each was read from (the versus word
    and the names)."""
    # A pair's summary reads two lines; three names or more are a subject
    # and conditions whatever the words ask - and so is a reading handed in
    # that already carries them.
    carried = any(c.side == "opponent" for c in scope.companions)
    subjects = [p for p in players if not any(_same_person(p, [c.player]) for c in found)]
    # A position group before a versus word is the subject whatever the
    # words ask: its games against the player (ISSUES.md #365).
    grouped = any(_versus_companions_led_by_a_group(question, m.start()) for m in lexicon.VERSUS_PHRASE.finditer(question))
    if not _asks_for_games(question) and len(subjects) <= 2 and not carried and not grouped:
        return [], []
    versus: list[Companion] = []
    claims: list[Claim] = []
    for match in lexicon.VERSUS_PHRASE.finditer(question):
        # A position group before the versus word is the subject too:
        # "centers vs gobert gamelog" is the centers' games against Gobert,
        # never Gobert's own log (ISSUES.md #365).
        if not _named_before(question, players, match.start()) and not _versus_companions_led_by_a_group(question, match.start()):
            continue
        names = [n for n in _companion_names(match.group(2), players, scope) if not any(_same_person(n, [c.player]) for c in [*found, *versus])]
        new = [Companion(player=name, predicate="played", side="opponent") for name in names if not _named_before(question, (name,), match.start())]
        if new:
            versus.extend(new)
            claims.append(Claim(match.start(), match.start(2) + _read_to(match.group(2), [c.player for c in new], None), "companion"))
    return versus, claims


def _unrouted_companions(index: names.NameIndex, question: str, players: tuple[str, ...], scope: Filtered) -> tuple[str, ...]:
    """A companion the model named nobody for, read from the phrase's own
    words: "show me splits for the sixers when maxey scores 20+ points"
    arrived with an invented Joel Embiid and no Maxey anywhere in the model's names
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
    the QUESTION's spelling, never a resolution: the reader resolves it
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
            # carried companion's own name for him, which the phrase misspells.
            if any(_same_person(segment, [p]) for p in (*players, *found)) or any(_near(r, segment) for r in routed):
                continue
            name = _unrouted_name(index, segment.split(), absent)
            if name is not None and not any(_same_person(name, [p]) for p in (*players, *found)):
                found.append(name)
    return tuple(found)


def _unrouted_name(index: names.NameIndex, words: list[str], absent: bool) -> str | None:
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
        if any(len(word) < 3 or word in lexicon.THRESHOLD_WORDS or word in lexicon.TEAM_SINGULARS or team_named_in(index.teams, word) for word in span):
            continue
        ordinary = any(word in dictionary for word in span)
        if ordinary and size < len(words) and not absent:
            continue
        if _exact_name_span(index.players, span, limit=1) or _unrouted_near_spelling(index, span):
            return " ".join(words[:size]) if size == len(words) else " ".join(span)
    return None


def _unrouted_near_spelling(index: names.NameIndex, span: list[str]) -> bool:
    """Whether a companion phrase's leading words, which are no player's
    name as spelled, are a near spelling of exactly ONE player's:
    "without wembyanama". The words after "without" or "with" are a name's
    place, not the question's leftover words, so the near-spelling pass
    applies to them as it does to a name the model copied
    (:func:`~association.query.entities.read_near_spelling`, and its three
    refusals: two candidates ask, a team's name is no player's, and every
    word must be close). The span is kept as typed; resolving it, and saying
    it was read as a near spelling, stays the entity index's."""
    text = " ".join(span)
    if any(word in _dictionary() for word in span):
        return False  # a typo is no ordinary word: "season" is one edit from Tari Eason
    return _team_named(index.teams, text) is None and len(_suggest_players_by_spelling(index, span)) == 1


def with_without_named(scope: Filtered) -> tuple[list[str], bool, dict[str, tuple[str, tuple[str, int] | None]]]:
    """The teammates a with/without split divides by, read off the scope's
    typed companions (:class:`~association.query.reading.Companion`): the
    ones absent, else the ones who played, else the ones a role is stated
    for - whether it asked "without", and each name's role (``started``,
    ``bench``, or ``reached`` with its column and threshold). Read by the
    split's default point (:func:`association.query.point.default_point`)
    and by the relation's read of the split.

    .. versionadded:: 5.0.0

    .. versionchanged:: 6.0.0
       Reads the typed companions; the ``without``, ``with_player`` and
       ``conditions`` slots are gone.
    """
    # Lists: teammate_names reads a list or one
    # bare name, and a tuple would be neither - every teammate dropped.
    mate_texts = teammate_names([c.player for c in scope.companions if c.absent])
    asked_without = bool(mate_texts)
    if not asked_without:
        mate_texts = teammate_names([c.player for c in scope.companions if c.side == "own" and c.predicate == "played"])
    roles = _with_without_roles(scope.companions)
    if not mate_texts and roles:
        mate_texts = list(roles)
    return mate_texts, asked_without, roles


def with_without_beside(scope: Filtered) -> list[str]:
    """The teammates a with/without split over an absence keeps the games
    of - the own-side companions who PLAYED beside one who is absent: "when
    AD plays and lebron doesn't" divides the games Anthony Davis played by
    whether LeBron James did (ISSUES.md #479), where the split had divided
    every game by LeBron alone and dropped Davis. Empty where nobody is
    absent: there the ones who played are what the split divides by
    (:func:`with_without_named`).

    .. versionadded:: 7.0.0
    """
    if not any(c.absent for c in scope.companions):
        return []
    return teammate_names([c.player for c in scope.companions if c.side == "own" and c.predicate == "played"])


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
    has = lexicon.CONDITION_HAS_LINE.search(text)
    if has is not None and int(has.group(1)) >= 1:
        # "has 4 fta": a box-score word the threshold spellings do not hold
        # (ISSUES.md #340), the column MEASURE_WORDS names it by.
        return "reached", Line(measure=lexicon.MEASURE_WORDS[has.group(2).lower()], value=int(has.group(1)), as_typed=has.group(0).casefold()), has.end()
    bare = _condition_role_bare_number(text)
    if bare is not None:
        # "when tatum has 30": a line whose words name no stat (ISSUES.md
        # #340) - read as a line on nothing, which the relation refuses by
        # its words (``line_names_no_stat``), where reading no line answered
        # the with/without split, a different question.
        return "reached", Line(measure=None, value=bare[0], as_typed=bare[1]), bare[2]
    started = lexicon.CONDITION_STARTED.search(text)
    if started is not None:
        return "started", None, started.end()
    bench = lexicon.CONDITION_BENCH.search(text)
    if bench is not None:
        return "bench", None, bench.end()
    absent = lexicon.CONDITION_ABSENT.search(text)
    if word in ("without", "excluding") or absent is not None or (word == "with" and lexicon.COMPANION_NONE.match(text)):
        return "absent", None, absent.end() if absent is not None else None
    return "played", None, None


def _condition_role_bare_number(text: str) -> tuple[int, str, int] | None:
    """A number standing after a companion's name with no stat word read
    beside it - "tatum has 30", "maxey has 20+" - as ``(value, the words
    from the one before it, where it ends)``; None where the phrase holds
    none. A year or an ordinal is no count of a stat (three digits at
    most, digits alone)."""
    pieces = list(lexicon.NAME_PIECES.finditer(text))
    for at, (before, piece) in enumerate(itertools.pairwise(pieces), start=2):
        digits = piece.group(0).removesuffix("+")
        after = pieces[at].group(0).casefold() if at < len(pieces) else None
        # A word after the number that is no joiner or function word is a
        # stat the line spellings do not hold ("shoots 4 ft"): not a line
        # on nothing, and left to the reading's unread number.
        if digits.isdigit() and len(digits) <= 3 and int(digits) >= 1 and (after is None or after in lexicon.NAME_JOINERS or after in lexicon.NAME_STOPWORDS):
            return int(digits), text[before.start() : piece.end()].casefold(), piece.end()
    return None


def _team_names(index: names.NameIndex, question: str, scope: Filtered, team_word: str | None, opponent: str | None, own_team: str | None) -> list[str]:
    """The team(s) the question is about: the word it names first, then the
    model's team (or teams) where the question supports them and they
    are teams - never the opponent or the player's own team over again."""
    names: list[str] = []
    if team_word and team_word not in (opponent, own_team):
        names.append(team_word)
    routed_team = scope.who.team
    if (
        isinstance(routed_team, str)
        and routed_team
        and not names
        and question_supports(routed_team, question)
        and _team_named(index.teams, routed_team) is not None  # a placeholder ("any_team") is no team
        and not any(t and (question_supports(routed_team, t) or question_supports(t, routed_team)) for t in (opponent, own_team))
    ):
        names.append(routed_team)
    for t in _listed_teams(scope):
        if isinstance(t, str) and question_supports(t, question) and _is_a_team(index, t) and t not in names and t != opponent:
            names.append(t)
    return names


def read_subject(index: names.NameIndex, question: str, asked: PointShape | None, scope: Filtered) -> Subject:
    """One reading of the subject. The question's spans decide; a name the
    model copied (``scope``, the reading of the model's names the parser
    builds, ``parse._model_reading``) counts only
    where the question's own words support it
    (:func:`question_supports`), and never to invent a subject the question
    does not name. ``asked`` is read only to tell two teams meeting
    (``TEAM_MEETINGS``) from a team set against another.

    .. versionadded:: 4.4.0

    .. versionchanged:: 6.0.0
       Takes what the words ask (``asked``, a
       :class:`~association.query.reading.PointShape`) where it took an intent.

    .. versionchanged:: 6.0.0
       Takes the name index (:class:`~association.query.names.NameIndex`) in
       place of a connection.
    """
    # The season a team's name is read in. The parser reads the subject
    # BEFORE the span tagger reads the season (the grammar's rules are
    # handed its companions),
    # so the scope it reads from holds none: the question's own season is
    # read here, or "the hornets in 2008" is today's Charlotte Hornets and
    # Chris Paul's New Orleans team is asked about as a name nobody typed
    # (ISSUES.md #316).
    season = scope.span.season if scope.span.season is not None else season_from_text(question)
    own = _team_after_for(index.teams, question, season)
    team_word = _team_word(index.teams, question)
    position = next((code for pattern, code in lexicon.POSITION_WORDS if pattern.search(question)), None)
    routed_opponent = _opponent_player(index, scope)
    opponent = _read_opponent(index, question, scope, season) if routed_opponent is None else None
    # Every team the words name, not only the first: "how many times did the
    # 76ers play boston" names the Celtics by "boston" beside the 76ers, and
    # "boston" is no player there (Brandon Boston Jr.).
    teams_here = {t for t in (opponent, own[0].name if own is not None else None, team_word) if t} | _teams_by_word(index.teams, question)

    routed, invented = _routed_names(index, scope, question, routed_opponent)
    filler = _filler_names(index, scope, question)
    routed = [p for p in routed if p not in filler]
    named = _question_players(index, question, routed, teams_here)
    spellings = _spellings(index.players, question, routed)
    players = _merge_names([spellings.get(r, r) for r in routed], named)
    unrouted = _unrouted_companions(index, question, players, scope)
    conditions, claims, phrases = _read_subject_conditions(question, (*players, *unrouted), scope)
    companions = tuple(dict.fromkeys(c.player for c in conditions))
    players = tuple(p for p in players if p not in companions)
    if _read_subject_alone(players, teams_here, conditions, scope) and position is None:
        players, conditions, companions, claims = companions, (), (), ()
    # "for the Heat" is a player's OWN team only beside a player subject
    # named BEFORE it - the order a tenure is asked in ("lebron ... for
    # Miami"); with none, or with the player following as a condition
    # ("stats for sixers when maxey scored 20+", F087), it names the team the
    # question is about.
    own_team = _read_subject_own_team(index.teams, question, players, own, season, opponent)
    teams = _team_names(index, question, scope, team_word, opponent, own_team)
    teams = _read_subject_near_team(index, question, opponent, teams, others=(*players, *companions, position, own_team))
    evidence = _evidence(named, routed, spellings, invented, team_word, opponent)
    if unrouted:
        evidence = (*evidence, f"companions the model named nobody for {list(unrouted)}")
    decided = _decide_team_players(index.teams, _decide(players, teams, position, opponent, own_team, companions, evidence, asked, question), question)
    subject = replace(decided, conditions=conditions, companion_phrases=phrases, claims=(*claims, *_decide_claims(index.teams, decided, question)))
    # Read, not decided: what the subject's shape settles the words to ask
    # is the parser's step (:func:`child_named`, with the grammar's rules run
    # once under it), and :func:`settle_subject` writes it here.
    return replace(
        subject,
        invented=tuple(invented),
        supported=tuple(routed),
        named_season=season_from_text(question),
        asked=asked,
        filler=filler,
        claims=(*subject.claims, *_read_subject_season_claims(question)),
    )


def _read_subject_near_team(index: names.NameIndex, question: str, opponent: str | None, teams: list[str], *, others: tuple[str | None, ...]) -> list[str]:
    """The team a question naming no other subject names by a typo -
    "nuggers record this season" - as typed, where exactly one of its words
    is a near spelling of a team (:func:`~association.query.entities.near_team`)
    and that word is no ordinary word ("rings" is one edit from the Kings),
    no word of a player's name and near no player's: read as nobody, the
    question ranked every team. Resolving it, and saying it was read as a
    near spelling, is the entity index's
    (:func:`~association.query.entities.resolved_team`), as for the word a
    versus word leads (ISSUES.md #247). ``teams`` as read where the words
    name a team or ``others`` holds another subject (a player, a companion,
    a position, an own team), and empty where no word, or several, are
    near one."""
    if teams or any(others):
        return teams
    players = checked_players(index.players)
    dictionary = _dictionary()
    found: dict[str, str] = {}
    for word in dict.fromkeys(_team_words(question)):
        lowered = word.casefold()
        if lowered in dictionary or (opponent is not None and question_supports(opponent, word)):
            continue
        team = near_team(index.teams, word)
        if team is None or _exact_name_span(index.players, [lowered]) or players.near([(lowered, _edit_budget(lowered))]):
            continue
        found.setdefault(team.id, word)
    return list(found.values()) if len(found) == 1 else []


def _read_subject_own_team(teams: names.TeamIndex, question: str, players: tuple[str, ...], own: tuple[Entity, int] | None, season: int | None, opponent: str | None) -> str | None:
    """The player's own team: the one a lead names after his name ("for the
    Heat", "with okc"), else - for one player - the team word right beside
    his name ("lakers westbrook stats", ISSUES.md #436)."""
    if own is not None and players and _named_before(question, players, own[1]):
        return own[0].name
    if len(players) != 1:
        return None
    beside = _team_beside_player(teams, question, players[0], season, opponent)
    return beside[0].name if beside is not None else None


def _read_subject_conditions(question: str, beside: tuple[str, ...], scope: Filtered) -> tuple[tuple[Companion, ...], tuple[Claim, ...], tuple[tuple[int, int, str], ...]]:
    """The companions beside the subject (:func:`_conditions_read`), each
    phrase's claim cut to the words the companion reader needed
    (:func:`~association.query.span.needed`): "play" in "when Embiid and
    Paul George play" reads the same companions without it. A word outside
    every phrase that the companions turn on is claimed beside them. The
    phrases whole, with the role each read, come third
    (:attr:`Subject.companion_phrases`)."""
    conditions, claims, phrases = _conditions_read(question, beside, scope)
    return conditions, needed(question, claims, lambda probed: _conditions_read(probed, beside, scope)[0], outside="companion", gives_up=True), phrases


def _read_subject_season_claims(question: str) -> tuple[Claim, ...]:
    """The words of the season the names are settled in, where the words
    name one (:func:`~association.query.lexicon.season_named`): read here -
    the span tagger reads them again, and its claim holds the same
    characters; a coach's question runs no tagger."""
    named_season = season_named(question)
    return () if named_season is None else needed(question, [Claim(start, end, "season") for start, end in named_season[1]], season_from_text)


class _NoParent:
    """No parent handed to :func:`settle_subject` - apart from a parent of
    "no shape" (``None``), which is one."""


_NO_PARENT = _NoParent()


def settle_subject(subject: Subject, asked: PointShape | None, *, parent: PointShape | _NoParent | None = _NO_PARENT, words: str | None = None) -> Subject:
    """``subject``, already read, under what the grammar's rules settled the
    words to ask (``asked``) - no name is read again, the warehouse is not
    asked and the rules do not run (the subject is read once, the grammar's
    rules run once). What
    depends on it is written here: two
    teams meeting are the ``teams`` kind under ``TEAM_MEETINGS``, and the
    key itself with why it is not ``parent``'s - the ``words`` that named a child
    (:func:`child_named`), or the one move that names no words, a team's
    record in the games a companion reached a line. Who stands beside the
    subject is the reading's too, and the grammar's rules and the taggers
    are handed it typed (:attr:`Subject.conditions`).

    .. versionadded:: 5.0.0

    .. versionchanged:: 5.0.0
       Takes the child the parser decided (``parent``, ``words``) and runs
       no stage of its own: until then it ran the stages under each child
       the words named, a speculative run beside the parser's.

    .. versionchanged:: 6.0.0
       Takes the grammar's keys (``asked``, ``parent``) where it took
       intents; ``None`` is the parent "no shape", and leaving ``parent``
       out is no parent at all.
    """
    who = subject
    if who.kind == "team" and who.teams and who.opponent and asked == TEAM_MEETINGS:
        who = replace(who, kind="teams", teams=(who.teams[0], who.opponent), opponent=None)
    evidence = tuple(line for line in who.evidence if not line.startswith("the words "))
    moved = not isinstance(parent, _NoParent) and asked != parent
    return replace(
        who,
        asked=asked,
        asked_reason=_asked_reason(asked, words) if moved else None,
        evidence=(*evidence, f"the words {words!r} name {key_path(asked)}") if words else evidence,
    )


def _asked_reason(asked: PointShape | None, words: str | None) -> str:
    """Why the settled key is not the parent's (:attr:`Subject.asked_reason`):
    the words that name a child, or - the one other way :func:`child_named`
    moves it, and the only one that names no words - a team's record in the
    games a companion reached a line. The key (``asked``) is named as
    :func:`~association.query.reading.key_path` names it."""
    return f"the words {words!r} name {key_path(asked)}" if words is not None else _TEAM_RECORD_WHEN


def _read_subject_alone(players: tuple[str, ...], teams: set[str], conditions: tuple[Companion, ...], scope: Filtered) -> bool:
    """Whether the one player named only as a companion is the subject after
    all: "2 threes in games Jamal Murray played" names nobody BESIDE him, so
    the games he played are his own games - he is the subject, and that he
    played them narrows nothing. Only one, and only with no team anywhere:
    "the 76ers record when both Embiid and Paul George played" is the
    team's question with two companions ("76ers" is no word the team
    reading finds, so the model's team, handed in, is what says so). Split out
    of :func:`read_subject` for the complexity gate."""
    named_team = teams or scope.who.teams
    return not players and not named_team and len(conditions) == 1 and conditions[0].predicate == "played"


def child_named(subject: Subject, parent: PointShape | None, question: str | lexicon.GrammarText, *, original: str | None = None) -> tuple[PointShape, str | None] | None:
    """The child of ``parent`` the subject's shape names, with the words
    that name it - or None where the parent stands. Decided from the
    grammar alone; the parser runs the grammar's rules ONCE under the child,
    and where they decline it (a count with no threshold in the text is a
    ranking) runs them under the parent instead
    (:func:`~association.query.parse.read_route`). Two ways a child is
    named:

    - A team has a companion's line as the condition: "show me splits for
      the sixers when maxey scores 20+ points" (yardstick-v2 F087) is the
      team's record in the games he reached it, which a record over a line
      (``LINE_RECORD``) answers with HIM as its player.
    - The question's own words name a child of the parent's key
      (:data:`_CHILD_GRAMMARS`, :data:`KIND_ASSIGNED_ASKS`) - and only
      where the grammar's rules, run under that child
      (:func:`~association.query.grammar.settle`), leave it there: a count of
      games with no threshold in the text is a ranking, and stays the
      parent's question.

    No other move is made here: the grammar names a player's own record, the
    pair relation and a comparison by the subject's kind, so a reroute of
    one into another has nothing to fire on (measured over the 628 questions
    with a recorded normalizer reply).

    .. versionadded:: 5.0.0

    .. versionchanged:: 6.0.0
       Takes and returns the grammar's keys (a
       :class:`~association.query.reading.PointShape`) where it took and
       returned intents.

    .. versionchanged:: 6.0.0
       Reads the grammar's text (:class:`~association.query.lexicon.GrammarText`)
       as well as a plain question, each child's words an
       :class:`~association.query.lexicon.Ask` that reads a typed line where
       it stands (Phase 4, step 6).
    """
    if subject.kind in ("team", "team_players") and parent in _RECORD_WHEN_PARENTS and any(c.predicate == "reached" for c in subject.conditions):
        return LINE_RECORD, None
    if parent in KIND_ASSIGNED_ASKS:
        return None
    for child, words, kinds, parents in _CHILD_GRAMMARS:
        match = words.search(question)
        if match is not None and parent in parents and subject.kind in kinds:
            # The words that named it, as the question holds them - or, with
            # no question beside the grammar's text (a probe of it), the
            # text as the ask read it, each value spelled by its kind.
            return child, (original if original is not None else match.string)[match.start() : match.end()]
    return None


def _named_before(question: str, players: tuple[str, ...], at: int) -> bool:
    """Whether a word of a subject player's name (three letters or more)
    appears in the question before position ``at``."""
    # A whole word of the head: the runs of word characters the head holds
    # (a name's words are letters alone, so a word standing whole in the
    # head is one of its runs).
    head = {run.group(0) for run in lexicon.WORD_RUN.finditer(question[:at].casefold())}
    # A possessive "s" glued on is the name still ("lebrons playoff outcome
    # during lakers", ISSUES.md #436).
    head |= {word[:-1] for word in head if word.endswith("s")}
    return any(word in head for p in players for word in _words(p.casefold()) if len(word) >= 3)


def _evidence(named: list[str], routed: list[str], spellings: dict[str, str], invented: list[str], team_word: str | None, opponent: str | None) -> tuple[str, ...]:
    """What the reading rested on, one line per finding, for the trace.

    Each line is a decision's recorded reason
    (``tests/query/reader_cases.json``, the stage snapshots), so rewording
    one is a recorded-reading move to enumerate."""
    return tuple(
        line
        for line in (
            f"question names players {named}" if named else "",
            f"model players the question supports {routed}" if routed else "",
            *(f"question spells {was!r} as {now!r}" for was, now in spellings.items()),
            f"model names the question never held {invented}" if invented else "",
            f"team word {team_word!r}" if team_word else "",
            f"against {opponent!r}" if opponent else "",
        )
        if line
    )


def _decide(
    players: tuple[str, ...],
    teams: list[str],
    position: str | None,
    opponent: str | None,
    own_team: str | None,
    companions: tuple[str, ...],
    evidence: tuple[str, ...],
    asked: PointShape | None,
    question: str,
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
    if teams and opponent and asked == TEAM_MEETINGS:
        return Subject("teams", (), (teams[0], opponent), position, None, own_team, companions, evidence)
    if teams:
        return Subject("team", (), tuple(teams), position, opponent, own_team, companions, evidence)
    return Subject("everyone", (), (), position, opponent, own_team, companions, evidence)


def _decide_team_players(teams: names.TeamIndex, subject: Subject, question: str) -> Subject:
    """A team named as the team its players played for, beside a single
    game, is its players as the subject (``team_players``): "most points in
    a single game on the 76ers", "most points by a knick in a game", "most
    points in a game in cavs history" asked the team's players' best game
    and were answered with the team's season line (ISSUES.md #364). Only
    the subject's own team, named by the words
    :data:`~association.query.lexicon.TEAM_AS_ITS_PLAYERS` leads it with."""
    if subject.kind != "team" or len(subject.teams) != 1 or lexicon.CHILD_SINGLE_GAME_HIGH.search(question) is None:
        return subject
    if _team_named_by(teams, lexicon.TEAM_AS_ITS_PLAYERS, question, subject.teams[0]) is None:
        return subject
    return replace(subject, kind="team_players")


def _team_named_by(teams: names.TeamIndex, phrase: re.Pattern[str], question: str, team: str) -> tuple[re.Match[str], int, int] | None:
    """The first match of ``phrase`` whose group is a word naming ``team``
    (its singular, a nickname or a whole word of its name), with where that
    word stands - or None."""
    for match in phrase.finditer(question):
        group = next((i for i in range(1, (match.lastindex or 0) + 1) if match.group(i)), None)
        if group is None:
            continue
        word = match.group(group).lower().removesuffix("'s").rstrip("'")
        if lexicon.TEAM_SINGULARS.get(word) == team or team_named_in(teams, word) == team:
            return match, match.start(group), match.end(group)
    return None


def _decide_claims(teams: names.TeamIndex, subject: Subject, question: str) -> tuple[Claim, ...]:
    """The words :func:`_decide` read the subject's kind from beside the
    names (which :func:`subject_claims` claims): the "player" of "a Hawks
    player", which makes the team's players the subject - or the words that
    lead the team as its players' ("on the", "by a", "in ... history"), the
    team's own word left to the team's claim."""
    if subject.kind != "team_players":
        return ()
    # The lead first: beside a single game it reads the team's players alone,
    # so a "player" after it restates them ("most points in a game by a knicks
    # player" reads the same without the word, and the ledger's deletion says so).
    named = _team_named_by(teams, lexicon.TEAM_AS_ITS_PLAYERS, question, subject.teams[0]) if subject.teams and lexicon.CHILD_SINGLE_GAME_HIGH.search(question) else None
    if named is not None:
        match, start, end = named
        return tuple(Claim(a, b, "subject") for a, b in ((match.start(), start), (end, match.end())) if question[a:b].strip())
    noun = lexicon.PLAYER_NOUN.search(question)
    return () if noun is None else (Claim(noun.start(), noun.end(), "subject"),)


def apply_subject(subject: Subject, scope: Filtered, *, asked: PointShape | None) -> Applied:
    """Write what the subject reading settled into the Reading's typed
    filters (:class:`~association.query.reading.Filtered`) - in the parser's
    last step (``parse._reading``): the players the question is about, on
    the typed subject (``Reading.who``); the one player a shape that needs
    one was left without; a player's own team, and the tenure it implies; the
    companions' roles; and a team's record in the games a companion reached
    a line. A name the question never held is replaced by the question's
    own spare name where there is exactly one per name, else reported, and
    the caller refuses by name rather than answering about it (AGENTS.md:
    "When it cannot be repaired, say so by name").
    Returns the scope with the reading written in (``scope`` itself is
    never changed), the decisions made, the names dropped, and what the
    subject settled the words to ask (:attr:`Subject.asked`), with the scope
    rewritten for it where it differs.

    A kept name is respelled as the question's own span spells it
    (:func:`_spellings`), so a reader reads "Jay Huff" and "Seth Curry"
    where a model wrote "Jaylen Huff" and "Stephen Curry". Every step here is
    one the parser's output reaches (measured over the 628 questions with a
    recorded normalizer reply).

    .. versionadded:: 4.4.0

    .. versionchanged:: 5.0.0
       Respells a kept name from the anchored span (a question's own typo,
       "Seph Curry", included) rather than from a whole-word match; writes
       the restored player, the own team and the companions' roles, which
       were ``entities.scope_from_question``'s; takes ``intent`` and returns
       :class:`Applied`, whose ``intent`` is a team's ``record_when`` where
       the reading settled on it. Takes and returns the typed
       :class:`~association.query.reading.Filtered` (ROADMAP plan item 6, step
       (f)) in place of a slot dict it mutated.

    .. versionchanged:: 6.0.0
       Takes ``asked`` (the grammar's key) where it took ``intent``;
       :class:`Applied` carries the key.
    """
    scope, players, dropped = _apply_players(subject, scope, asked)
    if dropped:
        # A name the question never held is refused by it; the companions
        # the words DO hold are still written ("jay huff game log vs
        # Embiid", the model's Jokic beside it).
        scope, _ = _apply_companions(subject, scope, asked)
        return Applied(_apply_who(subject, scope), [], dropped, asked)
    decisions = list(players)
    scope, restored = _apply_restored_player(subject, scope, asked)
    decisions.extend(restored)
    scope, own = _apply_own_team(subject, scope, asked)
    decisions.extend(own)
    scope, former = _apply_former_franchise(subject, scope)
    decisions.extend(former)
    scope, conditions = _apply_companions(subject, scope, asked)
    decisions.extend(conditions)
    scope, rewritten, settled = _apply_intent(subject, scope, asked)
    decisions.extend(rewritten)
    return Applied(_apply_who(subject, scope), decisions, [], settled)


def _apply_who(subject: Subject, scope: Filtered) -> Filtered:
    """The scope's typed subject as the reading settled it: its names as
    the steps above wrote them, with the reading's kind and position group
    (:class:`~association.query.reading.Subject`) - the one value the point
    reader and the relations read who the question is about from."""
    return replace(scope, who=replace(scope.who, kind=subject.kind, position=subject.position))


def _apply_team_record_when(subject: Subject, scope: Filtered, asked: PointShape | None) -> tuple[Filtered, list[Decision], PointShape]:
    """The scope for the TEAM's record in the games a companion reached a
    line - split out of :func:`_apply_intent` for the complexity gate; see
    :func:`child_named`'s rule for a team with a companion's line."""
    # Before the word-assigned children: this record over a line is the TEAM's
    # question with a companion's line, not a player's "record when he
    # scored 30+" that the child grammar settles from the words.
    condition = next(c for c in subject.conditions if c.predicate == "reached")
    player = scope.who.player
    rewritten = replace(
        scope,
        span=scope.span,
        # The venue stands; a model filed the subject's own team as the
        # opponent ("sixers" beside its invented Joel Embiid), and the
        # question sets the team against nobody.
        cuts=Cuts(venue=scope.cuts.venue, opponent=scope.cuts.opponent if subject.opponent is not None else None),
        # The companion is the player the record is keyed on, beside the
        # team it is the record of.
        who=reading.Subject(kind=subject.kind, players=(condition.player,), teams=subject.teams[:1], position=subject.position),
        measure=Measure.from_slots({"stat": condition.line.measure}) if condition.line is not None else None,
        # The companion's line is the record's own now: the shape is keyed on it.
        lines=(replace(condition.line, keyed=True),) if condition.line is not None else (),
        # Nothing else of the scope stands: the companion is the record's
        # player now, and no split, period or window was read for it. Any
        # OTHER companion stands, for the planner to honor or refuse ("when
        # tatum scores 30 and brown plays": dropped, the record was every
        # game of Tatum's 30).
        companions=tuple(c for c in subject.conditions if c is not condition),
        split=None,
        period=None,
        window=Window(),
    )
    if asked != LINE_RECORD:
        return rewritten, [Decision("subject", "asked", key_path(asked), key_path(LINE_RECORD), _TEAM_RECORD_WHEN)], LINE_RECORD
    # Already the team's record (the parser settles it, _decide_intent): a
    # decision only where the companion is not the player the reading held -
    # the player read BEFORE the scope was rewritten, since after it is his
    # by construction.
    return rewritten, ([Decision("subject", "player", player, condition.player, _TEAM_RECORD_WHEN)] if player != condition.player else []), LINE_RECORD


def _apply_intent(subject: Subject, scope: Filtered, asked: PointShape | None) -> tuple[Filtered, list[Decision], PointShape | None]:
    """Rewrite the scope for the team's record in the games a companion
    reached a line, where the reading settled on it
    (:func:`child_named`); returns the scope, the decisions and what the
    words ask the scope for now. A child the question's words name is the
    parser's own step (:func:`~association.query.parse.read_route` settles
    the reading under it), so it arrives here as the key handed in and
    nothing moves."""
    if subject.asked == LINE_RECORD and subject.kind in ("team", "team_players") and any(c.predicate == "reached" for c in subject.conditions):
        return _apply_team_record_when(subject, scope, asked)
    return scope, [], asked


def _apply_restored_player(subject: Subject, scope: Filtered, asked: PointShape | None) -> tuple[Filtered, list[Decision]]:
    """Put back the one player the question names where the model left the
    player out - only for a shape that cannot answer without one
    (:data:`~association.query.reading.PLAYER_REQUIRED_ASKS`:
    "Sga record 36 plus points" came back with no player at all) or where an
    empty slot has a real, different answer, the league
    (:data:`~association.query.reading.SUBJECT_RESTORABLE_ASKS`:
    "kawhi most threes in a game" answered the league's single-game leaders,
    Kawhi Leonard's own 7 never mentioned - yardstick-v2 F093). Anywhere
    else no player means the league, and filling one in would turn a
    league question into one about somebody the question happened to name.
    Only where the reading settled on exactly one player - subject or
    companion: "celtics record with 20+ points from jayson tatum" reads as
    the Celtics with Tatum as the condition, and a record over a line's
    player IS the condition player. "Best true shooting percentage" names
    Travis Best by whole word and nobody to the reading (an ordinary word),
    so nothing is restored there."""
    if asked not in PLAYER_REQUIRED_ASKS | SUBJECT_RESTORABLE_ASKS or scope.who.players:
        return scope, []
    # A companion who only played, started or came off the bench is the
    # condition on the team's games, never the record's player: "76ers record when they score
    # 120 points when embiid starts" is the team's line with Embiid starting
    # (ISSUES.md #246), where the restored Embiid keyed the record on his 120.
    roles = {c.player: c.predicate for c in subject.conditions}
    named = list(dict.fromkeys((*subject.players, *(p for p in subject.companions if roles.get(p) not in ("started", "bench", "played")))))
    if len(named) != 1:
        return scope, []
    return replace(scope, who=replace(scope.who, players=(named[0],))), [Decision("subject", "player", None, named[0], "from the question; the model left it out")]


def _apply_own_team(subject: Subject, scope: Filtered, asked: PointShape | None) -> tuple[Filtered, list[Decision]]:
    """Put back a player's OWN team, where "for <team>" / "with the <team>"
    names one beside him and the model handed in neither a team nor an
    ``opponent`` - yardstick-v2 F166, "lebron stats as a starter for Miami".
    Written to the tenure (``Cuts.tenure``), never the team: a model's
    ``team`` beside an already-correct ``opponent`` was measured noise, and
    a reader that read the team here would trust it - "lebron james 2 3 pointers all-time
    vs jazz on tuesdays" carried ``team='Los Angeles Lakers'`` beside a real
    ``opponent='Utah Jazz'``, and silently narrowed 9 meetings to 4 before
    ``own_team`` existed. Only for the shapes whose relation narrows by
    it (:data:`~association.query.reading.OWN_TEAM_RESTORABLE_ASKS`).

    A historical team names a TENURE, not "now": with no season named in
    the question (:attr:`Subject.named_season`, never a defaulted
    "current season"), ``span`` becomes "career" - "for Miami"
    fifteen years into a Lakers career is not asking about this season."""
    if asked not in OWN_TEAM_RESTORABLE_ASKS or subject.own_team is None or len(scope.who.players) != 1:
        return scope, []
    # Beside an opponent too, so long as it is another team: "d'angelo
    # russell vs pistons as a laker" is his Lakers games against Detroit,
    # where the opponent left "as a laker" unread and his 2026 games for
    # Dallas answered (ISSUES.md #269). The team is the words', never the
    # model's - which the noise above was.
    if scope.who.team or scope.cuts.tenure or scope.cuts.opponent == subject.own_team:
        return scope, []
    out = replace(scope, cuts=replace(scope.cuts, tenure=subject.own_team))
    decisions = [Decision("subject", "own_team", None, subject.own_team, "from the question; the model left it out")]
    if subject.named_season is None and not scope.span.career:
        out = replace(out, span=out.span.over_career())
        decisions.append(Decision("subject", "span", None, "career", 'a team named with no season is a tenure, not "now"'))
    return out, decisions


def _apply_former_franchise(subject: Subject, scope: Filtered) -> tuple[Filtered, list[Decision]]:
    """A team named by a name no franchise holds today, with no season
    named, is that franchise over the seasons it held the name: "highest
    scoring games of supersonics players" is Seattle's 1994-2008, never
    today's Thunder (ISSUES.md #420) - as a tenure with no season is a
    career. The span is written as a range of the era's seasons, where the
    words named no season, range or career of their own."""
    if subject.named_season is not None or scope.span.named:
        return scope, []
    for name in (*scope.who.teams, scope.cuts.tenure, scope.cuts.opponent):
        era = former_franchise(name) if name else None
        if era is not None and era.last_season is not None:
            out = replace(scope, span=replace(scope.span, since=era.first_season, until=era.last_season))
            return out, [Decision("subject", "span", None, f"{era.first_season}-{era.last_season}", f"{era.name} is a former name, read over the seasons it was held")]
    return scope, []


def _apply_filler_players(subject: Subject, scope: Filtered) -> tuple[Filtered, list[Decision], list[str]]:
    """Take the model's no-names (:attr:`Subject.filler`) out of the typed
    subject's players - "most", "most 30+ point games" (what the model files
    as a name for a ranking once nothing in its prompt shows one with none),
    or a team's word read as a player ("magic vs nets" as Magic Johnson) -
    rather than leave them for a reader to resolve into "No player found
    matching 'most'". Returns the scope, the decisions and the model's
    names still standing."""
    routed = _routed_player_slots(scope)
    if not any(r in subject.filler for r in routed):
        return scope, [], routed
    kept = [r for r in routed if r not in subject.filler]
    decisions = [Decision("subject", _players_slot(scope), r, None, "no player's name - a rank word, a phrase of the question, or a team's word") for r in routed if r in subject.filler]
    return replace(scope, who=replace(scope.who, players=tuple(kept))), decisions, kept


def _spare_names(subject: Subject, kept: list[str], asked: PointShape | None) -> list[str]:
    """The question's own names not yet on the typed subject, which replace
    a model's invention one for one: the subject's players, and for
    a record over a line - whose player IS the condition's - a reached companion
    too, whether the words asked ``LINE_RECORD`` or the reading settled it
    (the team's question with a companion's line, under an invented player)."""
    spare = [p for p in subject.players if not _same_person(p, kept)]
    if LINE_RECORD in (asked, subject.asked):
        spare += [c.player for c in subject.conditions if c.predicate == "reached" and not _same_person(c.player, [*kept, *spare])]
    return spare


def _apply_players(subject: Subject, scope: Filtered, asked: PointShape | None = None) -> tuple[Filtered, list[Decision], list[str]]:
    """The ``player``/``players`` half of :func:`apply_subject`. For
    a record over a line, whose player IS the condition's ("sixers record when
    maxey scored 20+"), a reached companion is a spare name the way the
    subject's own are: a model invented Joel Embiid there (day5), and the
    question's Maxey replaces him
    rather than the question refusing. An invented name is never among the
    players the parser hands here (``parse._read_route_players``, ISSUES.md
    #373): it is reported from :attr:`Subject.invented` as dropped, so the
    answering loop's by-name refusal still fires on a player shape, while
    the kind and the key were decided from the names the question holds."""
    scope, decisions, routed = _apply_filler_players(subject, scope)
    dropped = [*(r for r in routed if r in subject.invented), *(name for name in subject.invented if name not in routed)]
    kept = [r for r in routed if r not in dropped]
    # The spares are the question's own names the model never copied,
    # counted against the model's names it DID hold (``Subject.supported``),
    # not against the players written here, which are the question's names
    # already: "compare sga and embiid" with a model's [SGA, Nurkic] has Embiid
    # spare for Nurkic; "lebron stats" with [LeBron, Durant] has none.
    spare = _spare_names(subject, list(subject.supported), asked) if dropped else []
    if dropped and len(spare) != len(dropped):
        return scope, [], dropped
    field = _players_slot(scope)
    replacement = dict(zip(dropped, spare, strict=True)) if dropped else {}  # a spare name with nothing dropped is a player the model omitted: not put back here
    decisions.extend(Decision("subject", field, was, now, "the question never names the model's player; it names this one") for was, now in replacement.items())
    if not routed:
        return scope, decisions, []
    respelled = _respellings(kept, subject.players)
    replacement.update(respelled)
    decisions.extend(Decision("subject", field, was, now, "spelled as the question names the player") for was, now in respelled.items())
    new = [replacement.get(r, r) for r in routed]
    if new == routed:
        return scope, decisions, []
    return replace(scope, who=replace(scope.who, players=tuple(new))), decisions, []


def _apply_companions(subject: Subject, scope: Filtered, asked: PointShape | None) -> tuple[Filtered, list[Decision]]:
    """Write every companion the reading found - an absence, a start, the
    bench, a line reached, a teammate who played, a player on the other
    side ("without KD", "when Embiid starts", "in games Maxey had 20+
    points", "with Draymond playing", "vs lebron") - onto the scope as the
    typed :class:`~association.query.reading.Companion`, whatever the
    words ask: the relation readers read each as a filter on the games, the
    with/without split as the names it divides by (an absence, else the
    teammates who played) and the roles it holds each to ("record when
    Embiid and Paul George start": started against not), and an answer that
    cannot honor one refuses by name (the planner): the roles are written
    whatever answers, because writing them only where an answer honored
    them once let "how many times did the 76ers beat boston when embiid
    started" answer every meeting, the start gone without a word. This is
    the companions' one writer. A companion who is the subject himself (his
    name restored from the companion's phrase, "In his 18th season, how
    many games with 40+ points did Lebron James have?") is no companion.
    The compiler narrows its relation by every one, so "how many 30 point
    games did maxey have when embiid started" counts his games with Embiid
    starting (34) - with nothing written it counted all of them (86)."""
    written = _apply_companions_to_write(subject, scope)
    if not written:
        return scope, []
    # The scope holds every companion typed, the other side first.
    ordered = [*(c for c in written if c.side == "opponent"), *(c for c in written if c.side == "own")]
    return replace(scope, companions=(*scope.companions, *ordered)), _apply_companions_decision(ordered, asked)


def _apply_companions_to_write(subject: Subject, scope: Filtered) -> list[Companion]:
    """The companions the reading found that ``scope`` does not already
    hold: an absence keeps every name, the subject's own included
    ("without zzyzx" is refused by name); any other
    role is written once per person and never for the subject himself."""
    own = list(scope.who.players)
    held = [c.player for c in scope.companions]
    written: list[Companion] = []
    for each in subject.conditions:
        already = [c.player for c in written]
        new = not _same_person(each.player, already) if each.absent else not _same_person(each.player, own) and not _same_person(each.player, [*held, *already])
        if new:
            written.append(each)
    return written


def _apply_companions_decision(ordered: list[Companion], asked: PointShape | None) -> list[Decision]:
    """The decision that records the own-side roles in the shape the trace
    has always printed them in (:meth:`~association.query.reading.Companion.to_slot`;
    the other side's players and a teammate the with/without split divides
    by are no condition there)."""
    recorded = [c.to_slot() for c in ordered if c.side == "own" and not c.absent and (c.predicate != "played" or asked != PRESENCE_SPLIT)]
    return [Decision("subject", "conditions", None, recorded, "the role the question gives each player named beside the subject")] if recorded else []


def _routed_player_slots(scope: Filtered) -> list[str]:
    """The model's player names, as the scope's typed subject carries them."""
    return list(scope.who.players)


def _players_slot(scope: Filtered) -> str:
    """The field a decision about the scope's players names - ``players``
    for two or more, ``player`` for one - as the trace has always printed
    it."""
    return "players" if len(scope.who.players) >= 2 else "player"


def _listed_teams(scope: Filtered) -> tuple[str, ...]:
    """The teams the scope names as a list (two or more; the one team a
    scope names is :attr:`~association.query.reading.Subject.team`)."""
    return scope.who.teams if len(scope.who.teams) >= 2 else ()


def _same_person(name: str, others: tuple[str, ...] | list[str]) -> bool:
    """Whether ``name`` and one of ``others`` support each other - the same
    person under two spellings (a surname and its completion)."""
    return any(question_supports(name, o) or question_supports(o, name) for o in others)


def _respellings(kept: list[str], players: tuple[str, ...]) -> dict[str, str]:
    """A kept model name the subject spells differently - a bare "Jokic"
    the model left bare, "Jaylen Tatum" for the question's "tatum", "Seph
    Curry" resolved to Seth where the model wrote Stephen - mapped to the
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
# subject reading's building blocks. They take the names' in-memory index,
# never a connection (entities.players_of, entities.teams_of).


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

    What restores a team the model dropped entirely (F127, ISSUES.md:
    "how many 3 pointers have the magic made" arrived with no team at all)
    - the same repair :func:`players_named_in`
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


def subject_named_in(question: str | lexicon.GrammarText) -> str | None:
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
    grammar's rules (:mod:`association.query.grammar`), which settle it where
    the key they settle reads one and nobody else is named.

    .. versionadded:: 6.0.0

    .. versionchanged:: 6.0.0
       Takes the grammar's text as well as a plain question (Phase 4, step
       6): "had <N>" reads a line's number where the lines tagger typed one.
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
    lowercased, in order - what the grammar's rules file as a team's quarter or half
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

    ISSUES.md #170: a quarter question with no player named sometimes had
    the team and the opponent filled with a team the model inferred rather than one
    the question used - "Jokic ... 3rd quarter against Boston" filled the
    opponent with 'Denver Nuggets', Jokic's own, a word the question never
    wrote, while the team held 'Boston Celtics', a word it did.

    .. versionadded:: 6.0.0
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
_NICKNAME_RE = lexicon.whole_phrases(tuple(PLAYER_NICKNAMES), possessive=True)


def nicknames_in(question: str) -> list[str]:
    """Player names for every nickname appearing as a whole word in ``question``,
    in the order they appear, without repeats.

    Matched against the user's own words, which is the only place a nickname
    still exists: by the time a model has copied a name it has usually
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
    used to overrule the model's names. A span matches only if it is a nickname key or
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
            near = _players_named_in_near(players, words[index : index + lexicon.MAX_NAME_WORDS]) if size == 1 else None
            if near is not None:
                # Two or three adjacent words that are the single near
                # spelling of one player outrank a lone word that is somebody
                # else's exactly - tried after every exact span, so "vince
                # carter career" is Vince Carter by his two words, and before the
                # lone word, so "micheal jordan" is Michael Jordan, never
                # Micheal Williams by his given name (ISSUES.md #346). Never
                # a single word: one leftover word near a name read
                # "season" as Tari Eason. Kept as typed, so the entity index
                # reads the one near spelling and the answer says so
                # (entities.read_near_spelling) - the default is visible.
                found.append(" ".join(near))
                index += len(near)
                break
            rows = _exact_name_span(players, span)
            if len(rows) == 1:
                found.append(rows[0].name)
                index += size
                break
            possessive = _players_named_in_possessive(span) if size == 1 and not rows else None
            if possessive is not None:
                found.append(possessive)
                index += 1
                break
        else:
            index += 1
    seen: list[str] = []
    for name in found:
        if name not in seen:
            seen.append(name)
    return seen


def _players_named_in_possessive(span: list[str]) -> str | None:
    """The player a one-word nickname names where the word is that nickname
    with its possessive "s" glued on and no apostrophe - "lebrons record
    when AD plays", "ADs record with lebron" - or None. Only a curated
    nickname: a surname plus "s" is an ordinary plural too often ("browns",
    "greens"). Unread, the word leaves the companion's the one name the
    question holds, read as the subject: "Lebrons record when AD plays"
    would answer Anthony Davis's splits."""
    word = span[0].casefold()
    return PLAYER_NICKNAMES.get(word[:-1]) if len(word) >= 3 and word.endswith("s") and word not in PLAYER_NICKNAMES else None


def _players_named_in_near(players: names.PlayerIndex, words: list[str]) -> list[str] | None:
    """The longest run of two or more of ``words``, from the first, that is
    the single near spelling of one player (each word three letters or
    more), or None: "karl anthonyu towns" whole, "micheal jordan" as a pair
    (:func:`players_named_in`, ISSUES.md #346)."""
    for size in range(len(words), 1, -1):
        span = words[:size]
        if all(len(w) >= 3 for w in span) and len(_fuzzy_name_span(players, span, limit=2)) == 1:
            return span
    return None


def _anchor_word_position(q_words: list[str], lowered_q: list[str], word: str) -> int | None:
    """Where ``word`` (one word of the model's name) turns up in the
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
    wherever ``name`` (the model's guess, right or wrong) itself appears -
    plausibly names, when that is confident enough to act on.

    Two faults this repairs, both structural, and both invisible to the
    "any one word is enough" check a name is otherwise held to
    (:func:`question_supports`), because that check
    is deliberately generous: a model TRUNCATED a name the question spells in
    full ("dennis schröder", typed correctly, arrived as just ``'Dennis'`` -
    grounded, and a 7-way surname), and it FABRICATED a word next to a real
    one ("tatum rec home" arrived as ``'Jaylen Tatum'``, the league's only
    Tatum with an invented given name bolted on; "Grady dick" arrived as
    ``'Grady Dickinson'``, grounded by its own typo'd given name). Neither
    ever reaches a repair, because grounding already says yes.

    So this reads the question instead of trusting the model's spelling: it
    anchors each of ``name``'s own words to where it turns up nearby - exactly,
    or within :func:`~association.query.entities._edit_budget`, since a model may silently correct a typo
    and a near spelling still marks the spot - and resolves the question's
    literal words there against the roster, never the model's spelling.

    Deliberately anchored, never a sentence-wide scan: fuzzy-matching a
    question's leftover words was measured and rejected elsewhere in this
    module ("season" is one edit from Tari Eason, wherever it turns up), and
    what keeps this safe is that nothing is tried unless it sits next to a
    word the model already pointed at - a name with no anchor at all is left
    for the ungrounded path below to report, exactly as before.

    Two things measured against real corpus rows keep this from trusting too
    little of a coincidence:

    - **Two or more of the model's own words anchoring is answered from
      within exactly that range of the question, narrowed a word at a time -
      but never down to one word alone.** "kareem stats vs bob lanier"
      anchors both "bob" and "lanier" (the question's own words, spelled
      exactly), and that pair names nobody: Bob Lanier retired before the
      warehouse's 1993-94 floor. Falling back to "lanier" alone then named
      Chaz Lanier, a real but wholly unrelated player - the same false-cause
      shape the Maxey example in the module docstring warns about, arrived at
      through this function instead of a nickname. The range still shrinks
      rather than being tried whole-or-nothing, because the model's own
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
      the LAST of the model's words, the position a surname sits in, is
      falling all the way back to the anchor word ALONE, with nothing else
      corroborating it. "Jaylen Tatum" anchors only on "Tatum", its last
      word, and the league's only Tatum is trustworthy alone. "Kareem
      Abdul-Jabbar" anchors only on "Kareem", its FIRST word, and "Kareem"
      alone is exactly as unrelated a near-miss as "lanier" alone above, for
      the identical reason - Kareem Abdul-Jabbar is the other player these
      two corpus rows have in common, and neither he nor Bob Lanier has a
      row in `players` at all. A model-supplied given name with nothing
      else in the question corroborating it is left alone here, for the
      "any one word" check to judge as it always has.

    Returns:
        The one player the question's own words resolve to, or ``None`` when
        nothing anchors at all, when two or more anchored words do not
        resolve together, when a single anchor resolves only alone and is
        not the last of the model's words, or when what is left resolves to
        more than one player (left for :func:`~association.query.entities.resolve_player` to ask about)
        or to none.

    .. versionadded:: 6.0.0
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
    window, min_size, alone = bounds

    try:
        return _question_derived_player_search(players, window, min_size, alone)
    except duckdb.CatalogException:
        # A warehouse without `players` (a partial load, or a test double) has
        # nothing here to resolve against - the same best-effort rule
        # `_team_named` follows: finding nothing leaves the names exactly as
        # the model gave them, which is never worse than before this ran.
        return None


def _question_derived_player_multi_anchor_window(q_words: list[str], anchors: set[int]) -> tuple[list[str], int, int | None] | None:
    """The window and size floor for two or more anchored words - resolved
    from exactly that range of the question, one word narrower at a time,
    but never down to a single word alone. See
    :func:`question_derived_player`'s docstring's "kareem ... bob lanier"
    measurement for why: falling back that far is what named Chaz Lanier.
    "de'angelo russell" is why the range still shrinks at all - the model's
    own spelling of "De" does not match the roster's "D", and dropping it
    lets "angelo russell" resolve on its own. ``None`` when the anchors are
    too spread out to be one coherent name."""
    if max(anchors) - min(anchors) > 4:
        return None
    return q_words[min(anchors) : max(anchors) + 1], 2, None


def _question_derived_player_single_anchor_window(q_words: list[str], name_words: list[str], positions: list[int | None], anchors: set[int]) -> tuple[list[str], int, int | None]:
    """The window and size floor for exactly one anchored word: a small
    window around it, never the whole question - what keeps
    :func:`question_derived_player` from becoming the rejected
    leftover-word scan. Two or more words of THIS window agreeing is trusted
    regardless of where the anchor sits ("Grady dick" anchors on the given
    name "Grady", and the window's "grady dick" is what resolves it) - what
    is trusted only from the surname position (the floor of 1 rather than 2)
    is falling all the way back to the anchor WORD ALONE, with nothing else
    corroborating it; see the docstring's "Kareem" measurement. The word
    alone is the ANCHOR's, never a neighbor's: "how many points may luka
    score" anchors on "luka", which alone names several Lukas, and its
    neighbor "may" alone is Sean May - a player nobody named, read as the
    model's "luka" (ISSUES.md #447). The third value is where in the window
    the anchor stands."""
    anchor_index = next(i for i, p in enumerate(positions) if p is not None)
    anchor = next(iter(anchors))
    start = max(0, anchor - 1)
    window = q_words[start : min(len(q_words), anchor + 2)]
    min_size = 1 if anchor_index == len(name_words) - 1 else 2
    return window, min_size, anchor - start


def _question_derived_player_search(players: names.PlayerIndex, window: list[str], min_size: int, alone: int | None = None) -> Entity | None:
    """``window``, tried longest span first down to ``min_size``, exact
    words before near ones - the shared search both
    :func:`_question_derived_player_multi_anchor_window` and
    :func:`_question_derived_player_single_anchor_window` resolve into. A
    single word is tried only where it is the anchor (``alone``, its place
    in the window)."""
    for size in range(min(lexicon.MAX_NAME_WORDS, len(window)), min_size - 1, -1):
        starts = range(len(window) - size + 1) if size > 1 or alone is None else [alone]
        found = [resolved for start in starts if (resolved := _resolve_word_span(players, window[start : start + size])) is not None]
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


def _has_a_real_team(teams: names.TeamIndex, who: reading.Subject, season: int | None, question: str) -> bool:
    """Whether ``who`` carries a team that actually resolves to a real
    franchise - not merely a non-empty string. A model invents a team the
    way it invents a player (AGENTS.md, "A model invents names, and an
    invented name resolves"): "alperen şengün alltime record" arrived one run with no
    `team` at all, and another with `team='Alperen Şengün'` - the player's
    own name, filed as though it were a franchise, which a team's record's
    readers then refuse as "no team matching", the wrong cause. A team
    nothing resolves is functionally the same as no team at all.

    So is one that resolves to a franchise the question never names:
    "towns home rec including playoffs since 1/26/20 vs spurs" (yardstick-v2
    F110) arrived with ``team='Toronto Raptors'`` - a real team, and no word
    of it in the question - and would have answered the Raptors' record
    about a question naming Karl-Anthony Towns (:func:`_team_grounded`)."""
    for text in who.teams:
        team = _team_named(teams, text, season) if text.strip() else None
        if team is not None and _team_grounded(teams, question, team):
            return True
    return False


def player_named_on_a_team_only_question(players: names.PlayerIndex, teams: names.TeamIndex, question: str, who: reading.Subject, season: int | None) -> str | None:
    """The one player a question names, when what the words ask has no
    reading for him at all and the question names no REAL team either - or
    None.

    yardstick-v2 F111: "alperen şengün alltime record" was read as a
    ranking of teams, with no team that resolves to a real franchise named,
    and answered the league standings, entirely off the subject the
    question named. Diacritics are already handled: ``players_named_in``
    folds "şengün" to "sengun" (:func:`~association.query.entities._fold`) before matching, the same way
    it reads "dončić". See :func:`_has_a_real_team` for why an invented
    ``team`` value (the player's own name, filed as though it were a
    franchise - measured live, a second run of this exact question) counts
    as no team at all rather than stopping this check.

    Caller-gated to :data:`~association.query.reading.TEAM_ONLY_ASKS`
    (this function does not check what the words ask itself, the same shape the
    subject reading's sets take in
    :func:`apply_subject`): a REAL team already named there is the
    real subject, and a player coincidentally named beside it changes no
    answer - the same reasoning that keeps a stray name on two teams' meetings
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
       Takes the typed subject and the season the words name where it took
       the slot dict (Phase 3, step 4, slice (c)).
    """
    if _has_a_real_team(teams, who, season, question):
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
    refused as one (the matchup "cannot honor ['opponent']")."""
    held = {word.casefold() for name in names for word in _words(name)}
    for match in lexicon.AGAINST_PHRASE.finditer(question):
        words = _team_words(match.group(1))[:3]
        for size in (3, 2, 1):
            if size <= len(words) and len(" ".join(words[:size])) >= 2:
                team = _team_named(teams, " ".join(words[:size]), season)
                if team is not None and not _team_after_versus_a_name(teams, team, words[:size], held) and not _team_after_versus_led_by_another(teams, team, words[:size], season):
                    return team
    return None


def _team_after_versus_led_by_another(teams: names.TeamIndex, team: Entity, span: list[str], season: int | None) -> bool:
    """Whether ``span``, two words or more, starts with a word that names a
    team other than ``team`` - a span that only ENDS in ``team``: "vs magic
    on bulls" is the Magic, never the Bulls the three words hold (ISSUES.md,
    "The team after a versus word is read from a span that only ends in a
    team")."""
    if len(span) < 2:
        return False
    first = _team_named(teams, span[0], season)
    return first is not None and first.id != team.id


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
        span_text = next((group for group in match.groups() if group), "")
        words = _team_words(span_text)
        # A team's span ends at a versus word: "with knicks vs hawks" is the
        # Knicks, and its three words read as the Hawks by the last.
        words = list(itertools.takewhile(lambda word: word.casefold() not in _VERSUS_WORDS, words))[:3]
        bare = any(match.group(group) for group in lexicon.FOR_TEAM_BARE_GROUPS) or (match.group(3) is not None and match.group(0).split() == ["as"])
        if match.group(lexicon.FOR_TEAM_BARE_GROUPS[-1]) and not (words and _team_after_for_nickname(words[0])):
            # "in" leads a season, a city and a quarter far more than a
            # team: only a team's nickname after it is one ("in nets").
            continue
        for size in (3, 2, 1):
            if size <= len(words) and len(" ".join(words[:size])) >= 2:
                if size == 1 and words[0].casefold() in lexicon.COMMON_WORDS_THAT_NAME_TEAMS:
                    # "for me" is the asker, not the Memphis Grizzlies.
                    continue
                if bare and not (is_team_name(" ".join(words[:size])) or (size == 1 and _team_after_for_nickname(words[0]))):
                    # "with at least 50 points" is no Atlanta Hawk, "with
                    # no paul george" no New Orleans Pelican: a bare lead
                    # reads a span that is a team's name by its own words.
                    continue
                team = _team_named(teams, " ".join(words[:size]), season)
                if team is not None:
                    return team, match.start()
    return None


#: The words a versus phrase opens with (:data:`~association.query.lexicon.AGAINST_PHRASE`'s).
_VERSUS_WORDS = frozenset({"vs", "versus", "against", "v"})


def _team_after_for_nickname(word: str) -> bool:
    """Whether ``word`` is a team's nickname, a shorthand or a singular -
    never a city or an abbreviation (:func:`_team_after_for`'s "in")."""
    folded = word.casefold()
    return lexicon.TEAM_NICKNAME.fullmatch(folded) is not None or folded in lexicon.TEAM_SINGULARS or folded in _TEAM_NICKNAMES


def _team_beside_player(teams: names.TeamIndex, question: str, player: str, season: int | None, opponent: str | None) -> tuple[Entity, int] | None:
    """The team a player's question names right beside his name, with no
    word leading it - "lakers westbrook stats", "darius garland clippers
    stats", "sixers kelly oubre jr. vs clippers" (ISSUES.md #436): his
    tenure, the way "for the lakers" is. The word just before his name's
    first word or just after its last, where it names one team and not the
    opponent; with where the team stands, or None."""
    tokens = _subject_claims_tokens(question)
    stretches = [(claim.start, claim.end) for claim in _subject_claims_player(question, tokens, player)]
    if not stretches:
        return None
    first, last = min(start for start, _ in stretches), max(end for _, end in stretches)
    before = [token for token in tokens if token[2] <= first][-1:]
    after = [token for token in tokens if token[1] >= last][:1]
    for text, start, _end in (*before, *after):
        found = _teams_by_word(teams, text)
        if len(found) != 1:
            continue
        team = _team_named(teams, next(iter(found)), season)
        if team is not None and team.name != opponent:
            return team, start
    return None


# --- What the subject reading claims ---------------------------------------
#
# Contract 2: each word is read once, and the reader rule that read it claims
# the characters (reading.Claim). The subject reading claims the words of the
# names it settled - the players and the teams the read is about, the team
# the subject is set against and the one he played for - and the position
# group's words, and the companions' phrases. Read off the settled value, so a name the
# reading read and set aside (a dictionary word that is somebody's surname, a
# name the model invented) claims nothing.


def subject_claims(teams: names.TeamIndex, question: str, scope: Filtered) -> tuple[Claim, ...]:
    """The characters of ``question`` the subject reading read the settled
    ``scope``'s names (a teammate who played beside an absence among them)
    and position group from (:class:`~association.query.reading.Claim`):
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
    # The teammates who played beside an absence are names the split reads
    # (with_without_beside), as the subject's own players are.
    for player in (*scope.who.players, *with_without_beside(scope)):
        found.extend(_subject_claims_player(question, tokens, player))
    season = scope.span.season
    for team in scope.who.teams:
        found.extend(Claim(start, end, "team") for start, end in _subject_claims_team(teams, tokens, team, season))
    for placed, phrase, what in ((scope.cuts.opponent, lexicon.AGAINST_PHRASE, "opponent"), (scope.cuts.tenure, lexicon.FOR_TEAM_PHRASE, "tenure")):
        if placed is not None:
            found.extend(_subject_claims_led(question, phrase, _subject_claims_team(teams, tokens, placed, season), what))
    position = scope.who.position
    if position is not None:
        match = next((m for pattern, code in lexicon.POSITION_WORDS if code == position for m in [pattern.search(question)] if m is not None), None)
        if match is not None:
            found.append(Claim(match.start(), match.end(), "position"))
    return tuple(sorted(set(found), key=lambda c: (c.start, c.end, c.what)))


def opponent_read(teams: names.TeamIndex, question: str, opponent: str | None) -> tuple[Claim, ...]:
    """Where the team the subject reading set the subject against was read
    from, with the versus word before it (``"opponent"`` claims, as
    :func:`subject_claims` makes them) - read before any season is settled,
    so a franchise's name is matched over every season. What the parent
    grammar reads in place of the words, typed (``parse.grammar_text``).

    .. versionadded:: 6.0.0
    """
    if opponent is None:
        return ()
    tokens = _subject_claims_tokens(question)
    return tuple(_subject_claims_led(question, lexicon.AGAINST_PHRASE, _subject_claims_team(teams, tokens, opponent, None), "opponent"))


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
    # teams, and a model's expansion of "cav" to the Cavaliers is its job,
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
    """Whether every place the question holds a word of ``player``'s name
    is a word the question's grammar holds there - the homograph rule,
    read once for every reader of the subject's names (ISSUES.md #342,
    #486): a month in a calendar day or before its year ("may 10th 2019"
    is no Sean May, "jan 3 2019" no Jan Vesely; :func:`_words_the_grammar_holds`),
    or an ordinary word known to collide with a real surname wherever it
    stands (:data:`lexicon.COMMON_WORDS_THAT_NAME_PLAYERS`: "best" is no
    Travis Best, "head" no Luther Head) - the same shape
    :func:`_named_only_by_a_team_word` checks for a team name, applied to a
    plain word instead of a team's.

    .. versionadded:: 4.4.0
    """
    words = {w.casefold() for w in _words(player)}
    held = _words_the_grammar_holds(question)
    places = [(start, end) for word, start, end in _subject_claims_tokens(question) if word in words]
    return bool(places) and all(place in held for place in places)


def _words_the_grammar_holds(question: str) -> frozenset[tuple[int, int]]:
    """The places (start, end) of the question's words that hold a sense
    of its grammar no name can take there: a month written in a calendar
    day, each end of a range of days among them
    (:data:`~association.query.lexicon.CALENDAR_DATE`), or in a month the
    games are narrowed to - "in may", "march 2026" (the month a
    :data:`~association.query.lexicon.SITUATION` ends on) - and every word
    of :data:`~association.query.lexicon.COMMON_WORDS_THAT_NAME_PLAYERS`."""
    held: set[tuple[int, int]] = set()
    for match in lexicon.CALENDAR_DATE.finditer(question):
        held.add(match.span("month"))
    for match in lexicon.SITUATION.finditer(question):
        # "in may", "march 2026": the month the situation ends on.
        tokens = _subject_claims_tokens(match.group(0))
        if tokens and tokens[-1][0] in lexicon.MEANING_MONTH_WORDS:
            held.add((match.start() + tokens[-1][1], match.start() + tokens[-1][2]))
    held.update((start, end) for word, start, end in _subject_claims_tokens(question) if word in lexicon.COMMON_WORDS_THAT_NAME_PLAYERS)
    return frozenset(held)


def _named_only_by_a_team_word(teams: names.TeamIndex, question: str, player: str) -> bool:
    """Whether every word of ``player``'s name the question holds also names a
    team - "magic" in "magic vs nets" is the Orlando Magic, not Magic Johnson,
    and "boston" is the Celtics before it is Brandon Boston Jr."""
    asked = {w.casefold() for w in _words(question)}
    supporting = [w for w in _words(player) if w.casefold() in asked]
    return bool(supporting) and all(_team_named(teams, w) is not None for w in supporting)


def _shares_word(one: str, other: str) -> bool:
    return bool({w.casefold() for w in _words(one) if len(w) >= 3} & {w.casefold() for w in _words(other) if len(w) >= 3})
