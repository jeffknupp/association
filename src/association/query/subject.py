"""Who a question is about, read once from its own words.

A question's subject - a player, two players, a team, two teams, a position
group, or everyone - is today inferred in four places: the router's slots,
:func:`association.query.entities.scope_from_question` and its siblings in
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
from typing import Any, NamedTuple

import duckdb

from association.query.compose.team import team_named_in
from association.query.decisions import Decision
from association.query.entities import (
    _AGAINST,
    PLAYER_NICKNAMES,
    Entity,
    _edit_budget,
    _initials,
    _question_derived_player,
    _shares_word,
    _team_after_for,
    _team_after_versus,
    _team_grounded,
    _team_named,
    _words,
    find_players,
    find_teams,
    nicknames_in,
    players_named_in,
)
from association.query.router import settle
from association.query.season_text import season_from_text
from association.query.templates.common import (
    FILLER_PLAYER_WORDS,
    OWN_TEAM_RESTORABLE_INTENTS,
    PLAYER_INTENTS,
    PLAYER_REQUIRED_INTENTS,
    POSITIONS,
    SUBJECT_RESTORABLE_INTENTS,
    TEAM_SUBJECT_RESTORABLE_INTENTS,
)

#: The kinds a subject can be. ``team_players`` is "a Hawks player" - the
#: team's players as a group, which the compiler's team-where-a-player-
#: belongs read answers ("thunder all-time triple doubles", by player).
SUBJECT_KINDS: frozenset[str] = frozenset({"player", "pair", "team", "teams", "position", "everyone", "team_players"})
"""Every value :attr:`Subject.kind` takes.

.. versionadded:: 4.4.0
"""

#: Intents whose ``opponent`` is a team the subject played against. A PLAYER
#: in that slot is the pair relation's question - "lebron vs kawhi head to
#: head", "jay huff game log vs embiid" - which ``player_matchup`` answers
#: from two reads of the relation joined on the event; the router filed the
#: second player as an opponent, and a genuine two-player matchup refuses
#: ``opponent``, so both fell through (yardstick-v2 F081, F142).
_PAIRABLE_INTENTS: frozenset[str] = frozenset({"player_matchup", "game_log", "player_stat", "threshold_count", "single_game_high", "streak", "record_when", "player_splits"})

_RECORD_ASKED = re.compile(r"\brecords?\b", re.IGNORECASE)

#: A question that compares its two players - "compare", never "vs", which
#: on two players is the pair relation's meetings, not a comparison.
_COMPARES = re.compile(r"\bcompar(?:e[ds]?|ing|ison)\b", re.IGNORECASE)

_N_PLUS = r"\d{1,3}\s*(?:\+|plus|or more)"
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
    ("single_game_high", re.compile(r"\bin (?:a|one) (?:single )?game\b|\bcareer[- ]high\b|\bhighest\b.{0,60}\bgame\b", re.IGNORECASE), _NOT_A_TEAM, _PLAYER_RELATION_PARENTS),
    ("shot_distance", re.compile(r"\bhow far\b|\bdistance\b", re.IGNORECASE), _PLAYER_OR_PAIR, _PLAYER_RELATION_PARENTS | {"shot_chart"}),
    (
        "streak",
        re.compile(r"\bstreaks?\b|\bwin ?streak\b|\bstraight (?:games|wins|losses)\b|\bin a row\b|\bconsecutive\b", re.IGNORECASE),
        SUBJECT_KINDS,
        frozenset({"team_record", "team_stat", "team_leaderboard", "team_outlook", "head_to_head"}) | _PLAYER_RELATION_PARENTS,
    ),
    (
        "record_when",
        re.compile(rf"\brecord\b.*\b(?:when|with)\b.*{_N_PLUS}|\brecord\b.*{_N_PLUS}|\brecord\b.*\b(?:when|with)\b.*\b(?:scored|scores|had|has)\b.*\d+", re.IGNORECASE),
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
        frozenset({"team_record", "team_stat", "with_without", "head_to_head"}) | _PLAYER_RELATION_PARENTS,
    ),
    (
        "threshold_count",
        re.compile(
            rf"\b(?:how many|most|fewest)\b.*\b(?:games?|times)\b.*{_N_PLUS}|\b(?:how many|most|fewest)\b.*{_N_PLUS}.*\bgames?\b|\bhow many times\b|\bgames? with\b.*\b\d+\s+\w+"
            r"|\b\d{1,3}\s*(?:pts?|points?|rebs?|rebounds?|asts?|assists?|steals?|blocks?|threes|3s)\s+games?\b",
            re.IGNORECASE,
        ),
        _NOT_A_TEAM,
        _PLAYER_RELATION_PARENTS,
    ),
    (
        "player_history",
        re.compile(
            rf"\b(?:over|for|in|during) the (?:past|last) {_N_SEASONS}\b|\b(?:last|past) {_N_SEASONS}\b|\bby (?:season|year)\b|\b(?:each|every) (?:season|year)\b"
            r"|\bseason[- ](?:by|over)[- ]season\b|\byear[- ](?:by|over)[- ]year\b",
            re.IGNORECASE,
        ),
        _PLAYER_OR_PAIR,
        _PLAYER_RELATION_PARENTS,
    ),
    (
        "player_splits",
        re.compile(r"\bsplits?\b|\bby month\b|\bhome and away\b|\bhome/away\b|\bhome vs\.? away\b|\bmonthly\b", re.IGNORECASE),
        _PLAYER_OR_PAIR,
        frozenset({"with_without", "player_compare"}) | _PLAYER_RELATION_PARENTS,
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

.. versionadded:: 4.5.0
"""


class Applied(NamedTuple):
    """What :func:`apply_subject` did: the decisions recorded, the router's
    names the question never held that nothing could replace (the caller
    refuses by name), and the intent the subject's shape settled on -
    the router's own where the shape fits it.

    .. versionadded:: 4.5.0
    """

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

    .. versionchanged:: 4.5.0
       ``named`` is gone: ``players`` itself carries the question's own
       spelling of each router name, resolved from the span the router's
       name anchors rather than from a whole-word match - which read "kareem
       stats vs bob lanier" as Kareem Rush. ``routed_opponent`` and
       ``invented``, ``named_season`` and ``intent`` added. ``opponent``
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
    #: The router's ``opponent`` when it names a player rather than a team -
    #: the second of a pair, in the slot shape ``refusals.pair_from_opponent``
    #: reads - so :func:`apply_subject` knows that slot holds a name to check.
    #: ``None`` for a team opponent, which is a narrowing and not a subject.
    routed_opponent: str | None = None
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
    #: The intent the subject's shape settles, where the router's cannot be
    #: about this subject - a player's record against a team is
    #: ``with_without``, not two franchises meeting (``head_to_head``); two
    #: players are the pair relation (``player_matchup``), or a comparison
    #: (``player_compare``) where the question compares them; the router's
    #: own intent everywhere else.
    intent: str = ""
    #: The question this is a reading of - what :func:`apply_subject` settles
    #: a kind-assigned intent's slots from (:func:`~association.query.router.settle`).
    question: str = ""


_MONTH_ABBREVIATIONS = frozenset({"jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec"})

#: A companion's role, from the question's own words: what follows
#: "without" / "with" / "when" / "while", up to the next scoping word.
#: Loose on purpose - the names it holds are still checked against the
#: players the question names.
_COMPANION = re.compile(r"\b(?:without|with|when|while)\s+((?:(?!\b(?:vs\.?|versus|against|in|for|this|last|the)\b)[\w'.+-]+\s*){1,7})", re.IGNORECASE)

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


def _levenshtein(a: str, b: str) -> int:
    """Edit distance, for a near spelling the router silently corrected."""
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        cur.extend(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)) for j, cb in enumerate(b, 1))
        prev = cur
    return prev[-1]


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
    if any(len(w) >= 3 and any(len(x) >= 3 and _levenshtein(w, x) <= _edit_budget(w) for x in q) for w in words):
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
    return any(len(w) >= 6 and any(_levenshtein(w.casefold(), x) <= 2 for x in t) for w in _words(name))


def _is_a_team(con: duckdb.DuckDBPyConnection, name: str) -> bool:
    """A name the router filed that is a team and no player ("Alamhamed
    Embiid" was filed under ``teams``; "Boston Celtics" under ``players``)."""
    return bool(find_teams(con, name)) and not find_players(con, name)


def _team_abbreviation(con: duckdb.DuckDBPyConnection, question: str) -> str | None:
    """A team named by its abbreviation, in capitals ("PHI record 2026")."""
    rows = {str(abbr).casefold(): str(name) for abbr, name in con.execute("SELECT DISTINCT abbreviation, display_name FROM teams WHERE abbreviation IS NOT NULL").fetchall()}
    for w in _words(question):
        if len(w) == 3 and w.isupper() and w.casefold() in rows:
            return rows[w.casefold()]
    return None


def _team_word(con: duckdb.DuckDBPyConnection, question: str) -> str | None:
    """The team the question names as a word: a singular or one-word
    nickname first, then :func:`~association.query.compose.team.team_named_in`
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


def _read_opponent(con: duckdb.DuckDBPyConnection, question: str, slots: dict[str, Any], season: int | None) -> str | None:
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
    held = slots.get("opponent")
    held_team = _team_named(con, held, season) if isinstance(held, str) and held.strip() else None
    if held_team is not None and _team_grounded(con, question, held_team):
        return held_team.name
    # "karl towns stats vs netslast 5 games": the "vs" names a word nothing
    # resolves, and the router read it as a team it filed in `team` - a team
    # the question never holds otherwise, so the router's reading of that
    # word is the one there is.
    team = _team_named(con, slots.get("team"), season) if held_team is None else None
    if team is not None and _AGAINST.search(question) and not _team_grounded(con, question, team):
        return team.name
    return None


def _opponent_player(con: duckdb.DuckDBPyConnection, slots: dict[str, Any]) -> str | None:
    """The router's ``opponent`` when it names a player and not a team: the
    second of a pair, filed in the slot ``refusals.pair_from_opponent``
    reads ("jay huff game log vs Embiid" arrived with Jokic there). A team
    opponent is a narrowing, not a subject, and stays ``scope_from_question``'s."""
    opponent = slots.get("opponent")
    if not isinstance(opponent, str) or not opponent.strip() or find_teams(con, opponent) or not find_players(con, opponent):
        return None
    return opponent


def _routed_names(con: duckdb.DuckDBPyConnection, slots: dict[str, Any], question: str, opponent_player: str | None) -> tuple[list[str], list[str]]:
    """The router's player names - from ``player``, ``players``, a player
    filed as the ``opponent`` and one filed as the ``team`` - split into the
    ones the question supports and
    the ones it never held, minus any that is a team (the router files
    "Boston Celtics" as a player; a team is a narrowing, not an invention)."""
    team_slot = _team_slot_player(con, slots)
    routed = [p for p in _routed_player_slots(slots) + ([opponent_player] if opponent_player else []) + ([team_slot] if team_slot else []) if not _is_a_team(con, p) and not _not_a_name(p)]
    supported = [p for p in routed if question_supports(p, question)]
    return supported, [p for p in routed if p not in supported]


def _not_a_name(text: str) -> bool:
    """A router ``player`` that is no name at all: a position phrase ("shooting
    guard" on "highest 3 point percentage ... by a shooting guard", F056 - the
    position-group subject, which :attr:`Subject.position` carries) or the
    router's filler word ("player" on "Most points in 15th season played",
    F099). Neither is a player to read, replace or report."""
    stripped = text.strip()
    return stripped.lower() in FILLER_PLAYER_WORDS or any(re.fullmatch(pattern, stripped, re.IGNORECASE) for pattern, _ in POSITIONS)


def _team_slot_player(con: duckdb.DuckDBPyConnection, slots: dict[str, Any]) -> str | None:
    """The router's ``team`` when it names a player and no team: "Podziemski
    game log without curry" arrived as ``team='Podziemski'``, and "Will
    Riley last 5 game s" as ``team='Riley'`` (a bare fragment, three
    players) - the subject, in the wrong slot."""
    team = slots.get("team")
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


def _companions(question: str, players: tuple[str, ...], slots: dict[str, Any]) -> tuple[str, ...]:
    """The players whose ROLE the question states beside the subject -
    "without X", "with X on the floor", "when X scored" - read from the
    question's own words, never from the router's ``without`` slot (which
    filed Embiid under it on "Embiid's record against Boston"). The router's
    slot is consulted only for a companion the question misspells
    ("without wembyanama"), where its phrase is a near spelling of the
    router's name."""
    text = " ".join(m.group(1) for m in _COMPANION.finditer(question))
    if not text:
        return ()
    found = [p for p in players if question_supports(p, text)]
    for r in list(slots.get("without") or []) + list(slots.get("with_player") or []):
        if isinstance(r, str) and not _same_person(r, found) and _near(r, text):
            found.append(r)
    return tuple(found)


def _team_names(con: duckdb.DuckDBPyConnection, question: str, slots: dict[str, Any], team_word: str | None, opponent: str | None, own_team: str | None) -> list[str]:
    """The team(s) the question is about: the word it names first, then the
    router's ``team`` / ``teams`` where the question supports them and they
    are teams - never the opponent or the player's own team over again."""
    names: list[str] = []
    if team_word and team_word not in (opponent, own_team):
        names.append(team_word)
    routed_team = slots.get("team")
    if (
        isinstance(routed_team, str)
        and routed_team
        and not names
        and question_supports(routed_team, question)
        and _team_named(con, routed_team) is not None  # "any_team" is the router's placeholder, not a team
        and not any(t and (question_supports(routed_team, t) or question_supports(t, routed_team)) for t in (opponent, own_team))
    ):
        names.append(routed_team)
    for t in slots.get("teams") or []:
        if isinstance(t, str) and question_supports(t, question) and _is_a_team(con, t) and t not in names and t != opponent:
            names.append(t)
    return names


def read_subject(con: duckdb.DuckDBPyConnection, question: str, intent: str, slots: dict[str, Any]) -> Subject:
    """One reading of the subject. The question's spans decide; a slot the
    router filled counts only where the question's own words support it
    (:func:`question_supports`), and never to invent a subject the question
    does not name. ``intent`` is read only to tell two teams meeting
    (``head_to_head``) from a team set against another.

    .. versionadded:: 4.4.0
    """
    season = slots.get("season") if isinstance(slots.get("season"), int) else None
    own = _team_after_for(con, question, season)
    team_word = _team_word(con, question)
    position = next((code for pattern, code in POSITIONS if re.search(pattern, question, re.IGNORECASE)), None)
    routed_opponent = _opponent_player(con, slots)
    opponent = _read_opponent(con, question, slots, season) if routed_opponent is None else None
    teams_here = {t for t in (opponent, own[0].name if own is not None else None, team_word) if t}

    routed, invented = _routed_names(con, slots, question, routed_opponent)
    named = _question_players(con, question, routed, teams_here)
    spellings = _spellings(con, question, routed)
    players = _merge_names([spellings.get(r, r) for r in routed], named)
    companions = _companions(question, players, slots)
    players = tuple(p for p in players if p not in companions)
    # "for the Heat" is a player's OWN team only beside a player subject
    # named BEFORE it - the order a tenure is asked in ("lebron ... for
    # Miami"); with none, or with the player following as a condition
    # ("stats for sixers when maxey scored 20+", F087), it names the team the
    # question is about.
    own_team = own[0].name if own is not None and players and _named_before(question, players, own[1]) else None
    teams = _team_names(con, question, slots, team_word, opponent, own_team)
    evidence = _evidence(named, routed, spellings, invented, team_word, opponent)
    subject = _decide(players, teams, position, opponent, own_team, companions, evidence, intent, question)
    settled, words = _decide_intent(subject, routed_opponent, intent, question, slots)
    return replace(
        subject,
        routed_opponent=routed_opponent,
        invented=tuple(invented),
        named_season=season_from_text(question),
        intent=settled,
        question=question,
        evidence=(*evidence, f"the words {words!r} name {settled}") if words else evidence,
    )


def _decide_intent(subject: Subject, routed_opponent: str | None, intent: str, question: str, slots: dict[str, Any]) -> tuple[str, str | None]:
    """The intent the subject's shape settles, and the words that named it
    where a child intent was assigned - the router's own unless it cannot be
    about this subject:

    - A player's record against a team is not two franchises meeting.
      ``head_to_head`` counts every meeting, the ones he sat out included;
      ``with_without`` splits the team's record by the games he played,
      narrowed to the opponent - "Embiid career record vs boston", #163.
    - Two players are the pair relation, which ``player_matchup`` reads: a
      player the router filed as the ``opponent`` (F081, F142), and a pair
      the router sent to ``with_without`` - "steph curry record vs lebron
      regular season without kd" (yardstick-v2 F114) is Curry's games
      against LeBron with Durant absent, which that template answered as a
      team's record with and without Durant.
    - A pair the question compares, sent to ``player_stat`` with the second
      player as the ``opponent`` ("compare Jaylen Brown and Jason Tatum's
      netpoints ..."), is ``player_compare``.

    Exactly two players: a player whose invented opponent was deleted for
    want of one spare name ("luka game log vs embiid and klay thompson")
    is not a pair, and stays the router's question.

    Last, a child intent the question's own words name under a parent the
    router chose (:data:`_CHILD_GRAMMARS`, :data:`KIND_ASSIGNED_INTENTS`) -
    and only where the router's own stages, run under that child
    (:func:`~association.query.router.settle`), leave it there: a count of
    games with no threshold in the text is a ranking, and stays the
    router's question."""
    if intent == "head_to_head" and subject.kind == "player" and subject.opponent and _RECORD_ASKED.search(question):
        return "with_without", None
    if len(subject.players) == 2:
        if intent == "with_without" or (intent in _PAIRABLE_INTENTS and routed_opponent is not None):
            return "player_matchup", None
        if intent == "player_stat" and _COMPARES.search(question):
            return "player_compare", None
    return _child_intent(subject, intent, question, slots)


def _child_intent(subject: Subject, intent: str, question: str, slots: dict[str, Any]) -> tuple[str, str | None]:
    """The first child of :data:`_CHILD_GRAMMARS` whose words the question
    holds, whose kinds admit the subject and whose parents include the
    router's intent - with the words - or the router's intent and ``None``.
    A child the router (or its own stages) already chose stands."""
    if intent in KIND_ASSIGNED_INTENTS:
        return intent, None
    for child, words, kinds, parents in _CHILD_GRAMMARS:
        match = words.search(question)
        if match is None or intent not in parents or subject.kind not in kinds:
            continue
        if settle(child, slots, question).intent == child:
            return child, match.group(0)
        return intent, None
    return intent, None


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


def apply_subject(subject: Subject, slots: dict[str, Any], *, con: duckdb.DuckDBPyConnection, intent: str) -> Applied:
    """Write the players the subject was read to be about into the slots a
    template reads - ``player`` or ``players``, whichever shape the router
    used - and report the router's names the question never held, which the
    caller refuses by name rather than answers about (AGENTS.md: "when it
    cannot be repaired, say so - do not hand it to the agent"). Mutates
    ``slots``; returns the decisions made, the names dropped, and the intent
    the subject settled on (:attr:`Subject.intent`), with the slots rewritten
    for it where it differs from the router's.

    The first fields the reading settles in place of the repair chain, and
    what ``entities.override_invented_players`` did until 4.5.0: "compare sga
    and embiid" arriving as Nurkic, "jay huff game log vs Embiid" arriving
    with Jokic as the ``opponent``. A name the router filed that the question
    supports is kept as the router spelled it - or as the question's own span
    spells it where the two differ (:func:`_spellings`), so a template reads
    "Jay Huff" and "Seth Curry" where the router wrote "Jaylen Huff" and
    "Stephen Curry"; a name it does not support is replaced by the question's
    own spare name where there is exactly one per dropped name, else dropped
    and reported. An unsupported opponent with no spare is deleted rather
    than reported: the refusal would name him. A player the router left OUT
    is not put back here yet - that stays
    :func:`~association.query.entities.restore_dropped_players`' and
    ``scope_from_question``'s until the golden says the reading may.

    .. versionadded:: 4.4.0

    ``con`` and ``intent`` are what the opponent-TEAM pass needs: a team is
    the same team under any of its names only by id, and whether a team
    beside the subject is his opponent or the question's own side depends
    on whether the template reads a player
    (:data:`~association.query.templates.common.PLAYER_INTENTS`).

    .. versionchanged:: 4.5.0
       Writes ``opponent`` too, when it holds a player's name, and respells a
       kept name from the anchored span (a question's own typo, "Seph
       Curry", included) rather than from a whole-word match. Takes ``con``
       and ``intent``, and writes the opponent TEAM - into ``opponent``, and
       out of ``players``, ``team`` and ``teams`` beside a player - the
       restored player, the own team, the team subject, a player filed in
       ``team`` and a team that displaced the player, all of which were
       ``entities.scope_from_question``'s; and returns :class:`Applied`,
       whose ``intent`` is the reroute ``player_record_against_a_team`` and
       ``refusals.pair_from_opponent`` used to make.
    """
    decisions = _apply_team_slot_player(subject, slots, con, intent)
    decisions.extend(_apply_displaced_team(subject, slots, con, intent))
    players, dropped = _apply_players(subject, slots)
    if dropped:
        return Applied([], dropped, intent)
    decisions.extend(players)
    decisions.extend(_apply_opponent(subject, slots))
    decisions.extend(_apply_restored_player(subject, slots, intent))
    decisions.extend(_apply_opponent_team(subject, slots, con, intent))
    decisions.extend(_apply_own_team(subject, slots, intent))
    decisions.extend(_apply_team_subject(subject, slots, intent))
    rewritten, settled = _apply_intent(subject, slots, intent)
    decisions.extend(rewritten)
    return Applied(decisions, [], settled)


def _apply_team_slot_player(subject: Subject, slots: dict[str, Any], con: duckdb.DuckDBPyConnection, intent: str) -> list[Decision]:
    """Move a player's name out of ``team`` and into ``player``, for a
    template that reads one. "Podziemski game log without curry" arrived as
    ``team='Podziemski'``, ``player='Curry'`` - the subject in the team slot
    and the absent teammate in the player slot - and "Will Riley last 5 game
    s" as ``team='Riley'``, a fragment three players share that the
    question's own "will riley" settles (:func:`_spellings`). The player
    slot gives way only when empty or holding a companion; a garbled
    ``team`` beside some OTHER player the question names cannot borrow that
    name."""
    team = slots.get("team")
    if intent not in PLAYER_INTENTS or not isinstance(team, str) or not team.strip() or _team_named(con, team) is not None or not find_players(con, team):
        return []
    name = next((p for p in subject.players if _same_person(team, (p,))), None)
    held = slots.get("player")
    if name is None or (held and not (isinstance(held, str) and _same_person(held, subject.companions))):
        return []
    slots.pop("team", None)
    slots["player"] = name
    return [Decision("subject", "player", held or None, name, f"{team!r} is a player, not a team; the subject is {name!r}")]


def _apply_displaced_team(subject: Subject, slots: dict[str, Any], con: duckdb.DuckDBPyConnection, intent: str) -> list[Decision]:
    """Put back the player a team in ``team`` displaced, for a template that
    reads one, and put the team where it belongs. The team there is the
    opponent (the team after "vs") or one the question never mentions (the
    router's guess at the player's own); either way the player it displaced
    is the subject, and:

    - the opponent stays an opponent: "karl towns stats vs netslast 5 games"
      arrived as ``team='Brooklyn Nets'``, and dropping the team with Towns
      restored answered his last five games against anybody.
    - a team's question against another is put back in the order the
      question gives: "magic vs nets last 10" arrived with the sides swapped
      (and "magic" is Orlando, never Magic Johnson).
    - a team the question never names goes even when nobody was found:
      "stating centers vs phoenix suns log" arrived as the Lakers, whose log
      it then was. A subject the question does not name is for the template
      to refuse, not for the router's guess to supply."""
    team = _team_named(con, slots.get("team"), slots.get("season") if isinstance(slots.get("season"), int) else None)
    if intent not in PLAYER_INTENTS or team is None or slots.get("player") or slots.get("players"):
        return []
    versus = _team_named(con, subject.opponent) if subject.opponent else None
    displaced = versus is not None and team.id == versus.id
    grounded = any((found := _team_named(con, name)) is not None and found.id == team.id for name in (*subject.teams, subject.own_team) if name)
    if not displaced and grounded:
        return []
    slots.pop("team", None)
    if subject.kind in ("player", "pair"):
        return [_apply_displaced_team_player(subject, slots, team, displaced)]
    if displaced and subject.kind in ("team", "team_players") and subject.teams:
        slots["team"], slots["opponent"] = subject.teams[0], team.name
        return [Decision("subject", "team", team.name, subject.teams[0], f"{subject.teams[0]!r} is the subject and {team.name!r} the opponent, as the question orders them")]
    if displaced:
        slots.setdefault("opponent", team.name)
        return [Decision("subject", "team", team.name, None, "the team the question plays against; the question names no subject")]
    return [Decision("subject", "team", team.name, None, "not in the question, and the question names no player; dropped")]


def _apply_displaced_team_player(subject: Subject, slots: dict[str, Any], team: Entity, displaced: bool) -> Decision:
    """The player(s) the team in ``team`` displaced, put back in the router's own shape."""
    field = "player" if subject.kind == "player" else "players"
    if subject.kind == "player":
        slots["player"] = subject.players[0]
    else:
        slots["players"] = list(subject.players)
    why = "the opponent" if displaced else "not in the question"
    return Decision("subject", field, None, list(subject.players), f"{team.name!r} was {why}; the subject is {list(subject.players)!r}")


def _apply_intent(subject: Subject, slots: dict[str, Any], intent: str) -> tuple[list[Decision], str]:
    """Rewrite the slots for the intent the subject settled
    (:func:`_decide_intent`), where it differs from the router's; returns
    the decisions and the intent the slots are now for."""
    if not subject.intent:
        return [], intent
    if subject.intent == intent and not (subject.intent == "player_matchup" and len(subject.players) == 2 and slots.get("opponent") and slots.get("player")):
        # Already the router's intent - unless a player still sits in
        # `opponent` beside `player` on a matchup the router itself chose
        # ("lebron vs kawhi head to head"), which the template reads as a
        # team and refuses; the pair goes into `players` either way.
        return [], intent
    if subject.intent in KIND_ASSIGNED_INTENTS:
        return _apply_child_intent(subject, slots, intent)
    if subject.intent == "with_without":
        # Only the slots that still mean the same thing for the new intent.
        # The team slots are exactly what must not survive: they are the
        # reading being replaced. The subject goes in `without` because that
        # is the slot the template splits BY; it infers his team itself.
        kept = {key: value for key, value in slots.items() if key in ("season", "season_type", "span", "venue")}
        slots.clear()
        slots.update(kept)
        slots["without"] = [subject.players[0]]
        slots["opponent"] = subject.opponent
        return [Decision("subject", "intent", intent, "with_without", "a player's record against a team, not two teams meeting")], "with_without"
    slots["players"] = list(subject.players)
    for key in ("player", "opponent", "team"):
        slots.pop(key, None)
    reason = "two players the question compares" if subject.intent == "player_compare" else "two players: the games the two played against each other"
    if subject.intent == intent:
        return [Decision("subject", "players", None, list(subject.players), reason)], intent
    return [Decision("subject", "intent", intent, subject.intent, reason)], subject.intent


def _apply_child_intent(subject: Subject, slots: dict[str, Any], intent: str) -> tuple[list[Decision], str]:
    """The slots for a child intent the words assigned
    (:data:`KIND_ASSIGNED_INTENTS`): the router's own stages run again under
    it (:func:`~association.query.router.settle`), so the threshold, the
    seasons count, a streak's kind or a split are read the one way the
    router reads them - and the parent's own derived slots (a ``since`` a
    game log read where a history reads ``limit``) do not survive into a
    template that refuses them. Every slot that moved is a decision. Where
    the stages settle elsewhere after all, nothing is written and the
    router's intent stands."""
    settled = settle(subject.intent, slots, subject.question)
    if settled.intent != subject.intent:
        return [], intent
    before = dict(slots)
    slots.clear()
    slots.update(settled.slots)
    decisions = [Decision("subject", "intent", intent, subject.intent, "the question's own words name it, and the subject is one it can be about")]
    decisions.extend(Decision("subject", key, before.get(key), slots.get(key), f"read for {subject.intent}") for key in sorted(before.keys() | slots.keys()) if before.get(key) != slots.get(key))
    # The player the reading holds, where the child's template needs one and
    # neither the parent's stages nor the child's put one back: "kawhi most
    # threes in a game" under a game log names nobody to a game log, and the
    # single-game high the words settle is Kawhi's.
    decisions.extend(_apply_restored_player(subject, slots, subject.intent))
    return decisions, subject.intent


def _apply_restored_player(subject: Subject, slots: dict[str, Any], intent: str) -> list[Decision]:
    """Put back the one player the question names where the router left the
    player out - only for a template that cannot answer without one
    (:data:`~association.query.templates.common.PLAYER_REQUIRED_INTENTS`:
    "Sga record 36 plus points" came back with no player at all) or where an
    empty slot has a real, different answer, the league
    (:data:`~association.query.templates.common.SUBJECT_RESTORABLE_INTENTS`:
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
    if intent not in PLAYER_REQUIRED_INTENTS | SUBJECT_RESTORABLE_INTENTS or slots.get("player") or slots.get("players"):
        return []
    named = list(dict.fromkeys((*subject.players, *subject.companions)))
    if len(named) != 1:
        return []
    slots["player"] = named[0]
    return [Decision("subject", "player", None, named[0], "from the question; the router left it out")]


def _apply_own_team(subject: Subject, slots: dict[str, Any], intent: str) -> list[Decision]:
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
    it (:data:`~association.query.templates.common.OWN_TEAM_RESTORABLE_INTENTS`).

    A historical team names a TENURE, not "now": with no season named in
    the question (:attr:`Subject.named_season`, never the router's own
    "current season" default), ``span`` becomes "career" - "for Miami"
    fifteen years into a Lakers career is not asking about this season."""
    if intent not in OWN_TEAM_RESTORABLE_INTENTS or subject.own_team is None or not (slots.get("player") or slots.get("players")):
        return []
    if slots.get("team") or slots.get("opponent") or slots.get("own_team"):
        return []
    slots["own_team"] = subject.own_team
    decisions = [Decision("subject", "own_team", None, subject.own_team, "from the question; the router left it out")]
    if subject.named_season is None and not slots.get("span"):
        slots["span"] = "career"
        slots.pop("season", None)
        decisions.append(Decision("subject", "span", None, "career", 'a team named with no season is a tenure, not "now"'))
    return decisions


def _apply_team_subject(subject: Subject, slots: dict[str, Any], intent: str) -> list[Decision]:
    """Put back a team the question names as its OWN subject, where the
    router routed a team-shaped question to ``leaderboard`` or ``team_stat``
    and dropped the team entirely - yardstick-v2 F127, "how many 3 pointers
    have the magic made so far this season" arrived at ``leaderboard`` with
    no team slot at all and ranked the league's leaders in makes
    (:data:`~association.query.templates.common.TEAM_SUBJECT_RESTORABLE_INTENTS`).

    Only a team the reading settled as the subject itself - not one beside a
    player (that is ``own_team``'s), not one set against another ("76ers vs
    magic total points" is a comparison, not one team's total), and not a
    team-only intent naming a player, which is the F111 refusal-by-name
    shape this must not paper over. For ``leaderboard`` the restored team
    is ALSO marked ``team_restored``, a slot ``HONORED_SCOPING`` never lists
    for it, so ``check_scope`` refuses and ``query.compose`` answers the
    team's own total instead of ``leaderboard`` ranking players "on" it -
    while a ``team`` the router itself supplied ("Top 5 scorers on the
    Lakers?") keeps its direct answer. ``team_stat`` needs no marker: an
    empty ``team`` there already raises, so the restore is a strict
    improvement."""
    if intent not in TEAM_SUBJECT_RESTORABLE_INTENTS or subject.kind not in ("team", "team_players") or subject.opponent is not None or len(subject.teams) != 1:
        return []
    if slots.get("team") or slots.get("player") or slots.get("players") or slots.get("opponent"):
        return []
    slots["team"] = subject.teams[0]
    if intent == "leaderboard":
        slots["team_restored"] = True
    return [Decision("subject", "team", None, subject.teams[0], "from the question; the router left it out")]


def _apply_players(subject: Subject, slots: dict[str, Any]) -> tuple[list[Decision], list[str]]:
    """The ``player``/``players`` half of :func:`apply_subject`."""
    routed = _routed_player_slots(slots)
    if not routed:
        return [], []
    # Whatever kind the subject is: "compare the two best centers" reads as a
    # position group, and the two players the router put in its slots are
    # still nobody the question named. A companion the router filed among
    # the players ("fox vs magic without wembyanama" arrives as the pair
    # Fox/Wembanyama) is the question's own name in the chain's slot shape -
    # supported, so kept, not dropped.
    dropped = [r for r in routed if r in subject.invented]
    kept = [r for r in routed if r not in dropped]
    spare = [p for p in subject.players if not _same_person(p, kept)]
    if dropped and len(spare) != len(dropped):
        return [], dropped
    field = "players" if isinstance(slots.get("players"), list) else "player"
    replacement = dict(zip(dropped, spare, strict=True)) if dropped else {}  # a spare name with nothing dropped is a player the router omitted: not put back here
    decisions = [Decision("subject", field, was, now, "the question never names the router's player; it names this one") for was, now in replacement.items()]
    respelled = _respellings(kept, subject.players)
    replacement.update(respelled)
    decisions.extend(Decision("subject", field, was, now, "spelled as the question names the player") for was, now in respelled.items())
    new = [replacement.get(r, r) for r in routed]
    if new == routed:
        return [], []
    if field == "players":
        slots["players"] = new
    else:
        slots["player"] = new[0]
    return decisions, []


def _apply_opponent(subject: Subject, slots: dict[str, Any]) -> list[Decision]:
    """The ``opponent`` half of :func:`apply_subject`, run after the players'
    so their names are spoken for. A player there the question never held is
    replaced by the one player the question names that no subject slot
    claims, or dropped rather than answered about - never reported for the
    refusal, since the refusal would name him (#206). One the question does
    support stays as the router spelled him: the pair template resolves it."""
    opponent = subject.routed_opponent
    if opponent is None or slots.get("opponent") != opponent or opponent not in subject.invented:
        return []
    held = _routed_player_slots(slots)
    spare = [p for p in subject.players if not _same_person(p, held)]
    if len(spare) == 1:
        slots["opponent"] = spare[0]
        return [Decision("subject", "opponent", opponent, spare[0], "the question never names the router's opponent; it names this player")]
    del slots["opponent"]
    return [Decision("subject", "opponent", opponent, None, "the question never names the router's opponent, and names no one else to put there")]


def _apply_opponent_team(subject: Subject, slots: dict[str, Any], con: duckdb.DuckDBPyConnection, intent: str) -> list[Decision]:
    """The opponent-TEAM half of :func:`apply_subject`: put the team the
    subject is set against where a template will see it - ``opponent`` - and
    take it out of the slots the router filed it in instead. Measured against
    real StatMuse questions, the single most common repair there is (a third
    of the feed): "how did curry do against the celtics" put the Celtics in
    ``players`` (where "boston" is Brandon Boston Jr.); "compare curry and
    lebron vs the celtics" put them in ``team``, where no template read it
    and nothing refused; "Keyonte George against blazers" put them in
    ``teams``, which only ``head_to_head`` reads. The resulting ``opponent``
    is a scoping slot: a template that cannot narrow to one refuses it
    (``check_scope``) rather than answering about every opponent.

    A team the slots already carry as a SIDE - both of ``head_to_head``'s
    ``teams``, the ``team`` of a team question - stays where it is; only
    beside a player a template reads (``intent`` in
    :data:`~association.query.templates.common.PLAYER_INTENTS`) is the team
    after "vs" his opponent rather than a side. And an ``opponent`` naming
    a player the slots already ask about ("sga vs tyrese maxey fingerprint"
    put Maxey there beside himself) narrows nothing and goes."""
    season = slots.get("season") if isinstance(slots.get("season"), int) else None
    versus = _team_named(con, subject.opponent, season) if subject.opponent else None
    decisions: list[Decision] = []
    if versus is not None:
        decisions.extend(_apply_opponent_team_out_of_players(slots, con, versus, season))
        carries_player = intent in PLAYER_INTENTS and (bool(slots.get("player")) or bool(slots.get("players")))
        team = _team_named(con, slots.get("team"), season)
        if carries_player and team is not None and team.id == versus.id and not slots.get("opponent"):
            # The team the question plays AGAINST, filed as the subject's
            # own team beside him; left there, nothing reads it and nothing
            # refuses. "compare curry and lebron vs the celtics" came back
            # with team='Boston Celtics'.
            slots.pop("team", None)
            decisions.append(Decision("subject", "team", team.name, None, "the team the question plays against, not the subject's"))
        decisions.extend(_apply_opponent_team_slot(slots, con, versus, season, carries_player=carries_player))
    held = slots.get("opponent")
    if isinstance(held, str) and held.strip() and _team_named(con, held) is None:
        subjects = [name for name in [slots.get("player"), *(slots.get("players") or [])] if isinstance(name, str) and name.strip()]
        if any(_shares_word(subject_name, held) for subject_name in subjects):
            slots.pop("opponent", None)
            decisions.append(Decision("subject", "opponent", held, None, "a player the question already asks about, not a team"))
    return decisions


def _apply_opponent_team_out_of_players(slots: dict[str, Any], con: duckdb.DuckDBPyConnection, versus: Entity, season: int | None) -> list[Decision]:
    """Take the team the question plays against out of ``players``."""
    listed = slots.get("players")
    if not isinstance(listed, list):
        return []
    kept = [name for name in listed if not ((found := _team_named(con, name, season)) is not None and found.id == versus.id)]
    if len(kept) == len(listed):
        return []
    if len(kept) == 1:
        slots.pop("players", None)
        slots["player"] = kept[0]
    else:
        slots["players"] = kept
    return [Decision("subject", "players", listed, kept, f"{versus.name} is a team, not a player to compare")]


def _apply_opponent_team_slot(slots: dict[str, Any], con: duckdb.DuckDBPyConnection, versus: Entity, season: int | None, *, carries_player: bool) -> list[Decision]:
    """Write ``opponent`` itself: over a router value that is no team the
    question holds (the reading already chose the question's own, so the
    two differ only then), or into an empty slot the sides do not already
    carry - unless a player is the subject, when a team after "vs" filed in
    ``teams`` is his opponent even though ``head_to_head`` would read
    ``teams`` as its sides ("Keyonte George against blazers" answered his
    whole season, not his two games against Portland, while it sat there)."""
    held = slots.get("opponent")
    held_team = _team_named(con, held, season) if isinstance(held, str) and held.strip() else None
    if held:
        if held_team is not None and held_team.id == versus.id:
            return []
        slots["opponent"] = versus.name
        return [
            Decision(
                "subject",
                "opponent",
                held,
                versus.name,
                "the question names it; the router's resolves to no team" if held_team is None else "the question names it; the router's is not in the question",
            )
        ]
    listed_teams = [name for name in slots.get("teams") or [] if isinstance(name, str)]
    if carries_player and _apply_opponent_team_from_teams(slots, con, versus, season, listed_teams):
        return [Decision("subject", "opponent", None, versus.name, "the question plays against it; the router filed it as a team")]
    carried = [slots.get("team"), *listed_teams]
    if any((found := _team_named(con, name, season)) is not None and found.id == versus.id for name in carried):
        return []
    slots["opponent"] = versus.name
    return [Decision("subject", "opponent", None, versus.name, "from the question")]


def _apply_opponent_team_from_teams(slots: dict[str, Any], con: duckdb.DuckDBPyConnection, versus: Entity, season: int | None, listed_teams: list[str]) -> bool:
    """Move the team after "vs" out of ``teams`` and into ``opponent``, beside
    a player. Returns whether it was there to move."""
    kept = [name for name in listed_teams if not ((found := _team_named(con, name, season)) is not None and found.id == versus.id)]
    if len(kept) == len(listed_teams):
        return False
    if kept:
        slots["teams"] = kept
    else:
        slots.pop("teams", None)
    slots["opponent"] = versus.name
    return True


def _routed_player_slots(slots: dict[str, Any]) -> list[str]:
    """The router's player names, from ``player`` or ``players``."""
    return [p for p in ([slots.get("player")] if slots.get("player") else []) + list(slots.get("players") or []) if isinstance(p, str) and p.strip()]


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
