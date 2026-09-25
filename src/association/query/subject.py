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

Nothing reads this yet: :func:`read_subject` is pure, takes the routed
slots, and returns a :class:`Subject` with its evidence. Wiring it in - as a
logged decision first, then as the source the slots are written from - is
the roadmap's plan item 1, step by step against the golden.

.. versionadded:: 4.4.0
"""

from __future__ import annotations

import functools
import gzip
import re
from dataclasses import dataclass, replace
from importlib import resources
from typing import Any

import duckdb

from association.query.compose.move import POSITIONS
from association.query.compose.team import team_named_in
from association.query.decisions import Decision
from association.query.entities import PLAYER_NICKNAMES, _edit_budget, _initials, _team_after_for, _team_after_versus, _words, find_players, find_teams, nicknames_in, players_named_in

#: The kinds a subject can be. ``team_players`` is "a Hawks player" - the
#: team's players as a group, which the compiler's team-where-a-player-
#: belongs read answers ("thunder all-time triple doubles", by player).
SUBJECT_KINDS: frozenset[str] = frozenset({"player", "pair", "team", "teams", "position", "everyone", "team_players"})
"""Every value :attr:`Subject.kind` takes.

.. versionadded:: 4.4.0
"""


@dataclass(frozen=True)
class Subject:
    """Who a question is about.

    ``kind`` is one of :data:`SUBJECT_KINDS`. ``players`` and ``teams`` are
    the names as the question or the router gave them - resolution to an
    entity stays with :mod:`association.query.entities`. ``opponent`` is a
    team the subject is set against ("vs the Pistons"); ``own_team`` the
    player's own ("for the Heat"); ``companions`` players named beside the
    subject with a role the question states ("without KD", "when Maxey
    scored 20+"). ``evidence`` says, per line, what the reading rested on.

    .. versionadded:: 4.4.0
    """

    kind: str
    players: tuple[str, ...] = ()
    teams: tuple[str, ...] = ()
    position: str | None = None
    opponent: str | None = None
    own_team: str | None = None
    companions: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    #: The players the question ITSELF names, resolved ("jokic" -> Nikola
    #: Jokic) - the spelling a template gets where the router's is a bare
    #: surname, a truncation or a near miss for the same person.
    named: tuple[str, ...] = ()


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
    """Whether ``question`` holds ``name``: any one word of it (three or more
    letters), a near spelling of one within
    :func:`association.query.entities._edit_budget` (none for a short word,
    one edit to six letters, two beyond - the router corrects typos, "embid"
    is Embiid), a curated nickname for the player, or its initials ("SGA",
    "kd"). The discipline of
    :func:`association.query.entities.override_invented_players`'s grounding
    check, as a predicate.

    .. versionadded:: 4.4.0
    """
    q = [w.casefold() for w in _words(question)]
    words = [w.casefold() for w in _words(name) if len(w) >= 3]
    if any(w in q for w in words):
        return True
    if any(any(len(x) >= 3 and _levenshtein(w, x) <= _edit_budget(w) for x in q) for w in words):
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


def _merge_names(question_named: list[str], router_named: list[str]) -> tuple[str, ...]:
    """The question's names, plus the router's names the question supports
    that are not one of them already (the router's completion of a surname
    the question gives is the same person, not a second one)."""
    out = list(question_named)
    for r in router_named:
        if not any(question_supports(o, r) or question_supports(r, o) for o in out):
            out.append(r)
    return tuple(out)


def _routed_names(con: duckdb.DuckDBPyConnection, slots: dict[str, Any], question: str) -> list[str]:
    """The router's player names the question supports, minus any that is a
    team (the router files "Boston Celtics" as a player)."""
    routed = [p for p in ([slots.get("player")] if slots.get("player") else []) + list(slots.get("players") or []) if isinstance(p, str) and p.strip()]
    return [p for p in routed if question_supports(p, question) and not _is_a_team(con, p)]


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
        if isinstance(r, str) and r not in found and _near(r, text):
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
    versus = _team_after_versus(con, question, season)
    own = _team_after_for(con, question, season)
    team_word = _team_word(con, question)
    position = next((code for pattern, code in POSITIONS if re.search(pattern, question, re.IGNORECASE)), None)
    opponent = versus.name if versus is not None else None
    teams_here = {t for t in (opponent, own[0].name if own is not None else None, team_word) if t}

    routed = _routed_names(con, slots, question)
    named = _question_players(con, question, routed, teams_here)
    players = _merge_names(named, routed)
    companions = _companions(question, players, slots)
    players = tuple(p for p in players if p not in companions)
    # "for the Heat" is a player's OWN team only beside a player subject;
    # with none, "splits for the Sixers" names the team the question is about.
    own_team = own[0].name if own is not None and players else None
    teams = _team_names(con, question, slots, team_word, opponent, own_team)
    evidence = tuple(
        line
        for line in (
            f"question names players {named}" if named else "",
            f"router players the question supports {routed}" if routed else "",
            f"team word {team_word!r}" if team_word else "",
            f"against {opponent!r}" if opponent else "",
        )
        if line
    )
    return replace(_decide(players, teams, position, opponent, own_team, companions, evidence, intent, question), named=tuple(named))


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


def apply_subject(subject: Subject, slots: dict[str, Any]) -> tuple[list[Decision], list[str]]:
    """Write the players the subject was read to be about into the slots a
    template reads - ``player`` or ``players``, whichever shape the router
    used - and report the router's names the question never held, which the
    caller refuses by name rather than answers about (AGENTS.md: "when it
    cannot be repaired, say so - do not hand it to the agent"). Mutates
    ``slots``; returns the decisions made and the names dropped.

    The first field the reading settles in place of the repair chain:
    :func:`association.query.entities.override_invented_players`'s job -
    "compare sga and embiid" arriving as Nurkic - done from the reading
    instead. A name the router filed that the question supports is kept as
    the router spelled it, so nothing downstream sees a different string
    for the same person; a name it does not support is replaced by the
    question's own spare name where there is exactly one per dropped name,
    else dropped and reported. A player the router left OUT is not put back
    here yet - that stays :func:`~association.query.entities.restore_dropped_players`'
    and ``scope_from_question``'s until the golden says the reading may.

    .. versionadded:: 4.4.0
    """
    routed = _routed_player_slots(slots)
    if not routed or subject.kind not in ("player", "pair"):
        return [], []
    # A companion the router filed among the players ("fox vs magic without
    # wembyanama" arrives as the pair Fox/Wembanyama) is the question's own
    # name in the chain's slot shape - kept, not dropped.
    kept = [r for r in routed if _same_person(r, (*subject.players, *subject.companions))]
    dropped = [r for r in routed if r not in kept]
    spare = [p for p in subject.players if not _same_person(p, kept)]
    if dropped and len(spare) != len(dropped):
        return [], dropped
    field = "players" if isinstance(slots.get("players"), list) else "player"
    replacement = dict(zip(dropped, spare, strict=True)) if dropped else {}  # a spare name with nothing dropped is a player the router omitted: not put back here
    decisions = [Decision("subject", field, was, now, "the question never names the router's player; it names this one") for was, now in replacement.items()]
    respelled = _respellings(kept, subject.named)
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


def _routed_player_slots(slots: dict[str, Any]) -> list[str]:
    """The router's player names, from ``player`` or ``players``."""
    return [p for p in ([slots.get("player")] if slots.get("player") else []) + list(slots.get("players") or []) if isinstance(p, str) and p.strip()]


def _same_person(name: str, others: tuple[str, ...] | list[str]) -> bool:
    """Whether ``name`` and one of ``others`` support each other - the same
    person under two spellings (a surname and its completion)."""
    return any(question_supports(name, o) or question_supports(o, name) for o in others)


def _respellings(kept: list[str], named: tuple[str, ...]) -> dict[str, str]:
    """A kept router name the question itself spells differently - a bare
    "Jokic" the router left bare, "Jaylen Tatum" for the question's "tatum",
    a "Deron Williams" two edits from the question's "derozan" - mapped to
    the question's own resolved name:
    :func:`association.query.entities._question_derived_player`'s job."""
    out: dict[str, str] = {}
    for r in kept:
        own = next((p for p in named if _same_person(r, (p,)) and p != r), None)
        if own is not None:
            out[r] = own
    return out
