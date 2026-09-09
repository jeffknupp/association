"""Resolving a name the model produced ("Lakers", "LAL", "Curry") to a real id.

Callers want different things from an ambiguous name - a chart of the wrong
Curry is a visible mistake, a NUMBER attributed to the wrong Curry is not - so
both behaviours stay available rather than one being picked for everyone:

    find_*    - every candidate, best first. The caller decides.
    resolve_* - one entity, or Ambiguous/NotFound. Never a guess.

Templates use resolve_*, because a template's job is to be trusted with a
number. Ambiguity is returned as a value, and the template asks a clarifying
question (see :func:`clarification`) rather than guessing or falling through.

Charts take the third road: they narrow with :func:`narrow_to_available` first,
to the candidates who have the rows the chart would be drawn from, and only ask
when more than one survives. What made a best match defensible there was the
plot being titled with the name that won - which is no help at all when the
wrong name means no plot gets drawn."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import duckdb

MAX_CANDIDATES = 10

# Curated shorthand -> the player it unambiguously means. NOT the prominence
# tiebreak that was measured and rejected: that ranked every candidate by
# minutes or points, which also resolved "Brown" and "Mitchell" - names no
# query can reliably carry on their own. This is an explicit, auditable list,
# and a name absent from it still gets the clarifying question.
#
# A shorthand may take a token another player owns ("melo" is also Fab Melo's
# surname, "russ" also Russ Smith's) when one player dominates that shorthand
# in ordinary use, because a question carrying only the shorthand cannot
# reliably have meant the other player either - "show me Melo's fingerprint"
# is not a plausible request for Fab Melo's. First names are here on the same
# reasoning: "luka" and "kobe" resolve, where they used to ask. What stays out
# is a shorthand that is simply someone's ordinary name ("timmy" is Timmy
# Allen's actual first name, "buck" is Buck Williams's), and a surname on its
# own, which nobody can expect to complete: "brown" still asks, across ten
# candidates, and should.
#
# Confirmed against the warehouse: every value matches exactly one row in
# `players`, and no key is a name token belonging only to someone else. Re-run
# `python scripts/check_nicknames.py` after editing.
#
# Sourced from Wikipedia's "List of nicknames in basketball", filtered to
# players the warehouse actually has - it starts at 1993-94, so Bird, Kareem
# and Dr. J are not here because they are not in `players` at all.
PLAYER_NICKNAMES = {
    # Shorthand and initialisms.
    "sga": "Shai Gilgeous-Alexander",
    "wemby": "Victor Wembanyama",
    "alien": "Victor Wembanyama",
    "kd": "Kevin Durant",
    "cp3": "Chris Paul",
    "steph": "Stephen Curry",
    "chef curry": "Stephen Curry",
    "ad": "Anthony Davis",
    "the brow": "Anthony Davis",
    "dame": "Damian Lillard",
    "pg13": "Paul George",
    "kat": "Karl-Anthony Towns",
    "bron": "LeBron James",
    "lebron": "LeBron James",
    "king james": "LeBron James",
    "the king": "LeBron James",
    "ant": "Anthony Edwards",
    "jrue": "Jrue Holiday",
    "trae": "Trae Young",
    "zion": "Zion Williamson",
    "book": "Devin Booker",
    "klay": "Klay Thompson",
    "luka": "Luka Doncic",
    "russ": "Russell Westbrook",
    "dlo": "D'Angelo Russell",
    "ja": "Ja Morant",
    "melo": "Carmelo Anthony",
    "boogie": "DeMarcus Cousins",
    "jojo": "Joel Embiid",
    "the process": "Joel Embiid",
    "the beard": "James Harden",
    "the claw": "Kawhi Leonard",
    "the klaw": "Kawhi Leonard",
    "giannis": "Giannis Antetokounmpo",
    "the greek freak": "Giannis Antetokounmpo",
    "greek freak": "Giannis Antetokounmpo",
    "joker": "Nikola Jokic",
    # Players whose careers reach back toward the warehouse's 1993-94 floor.
    # These are the ones the router gets wrong rather than merely misses: it
    # answered "The Answer" with Klay Thompson and "The Glove" with Jayson
    # Tatum, both confidently.
    "ai": "Allen Iverson",
    "a.i.": "Allen Iverson",
    "the answer": "Allen Iverson",
    "bubba chuck": "Allen Iverson",
    "vc": "Vince Carter",
    "vinsanity": "Vince Carter",
    "air canada": "Vince Carter",
    "half man half amazing": "Vince Carter",
    "kobe": "Kobe Bryant",
    "black mamba": "Kobe Bryant",
    "the mamba": "Kobe Bryant",
    "the big fundamental": "Tim Duncan",
    "the dream": "Hakeem Olajuwon",
    "the mailman": "Karl Malone",
    "the admiral": "David Robinson",
    "the glove": "Gary Payton",
    "the worm": "Dennis Rodman",
    "the truth": "Paul Pierce",
    "big ticket": "Kevin Garnett",
    "kg": "Kevin Garnett",
    "shaq": "Shaquille O'Neal",
    "the diesel": "Shaquille O'Neal",
    "mj": "Michael Jordan",
    "air jordan": "Michael Jordan",
    "the german": "Dirk Nowitzki",
    "t-mac": "Tracy McGrady",
    "c-webb": "Chris Webber",
    "penny": "Anfernee Hardaway",
    "the matrix": "Shawn Marion",
    "the reignman": "Shawn Kemp",
    "reign man": "Shawn Kemp",
    "agent zero": "Gilbert Arenas",
    "white chocolate": "Jason Williams",
    "the glide": "Clyde Drexler",
    "zo": "Alonzo Mourning",
    "j-kidd": "Jason Kidd",
    "d-wade": "Dwyane Wade",
    "flash": "Dwyane Wade",
    "manu": "Manu Ginobili",
    "birdman": "Chris Andersen",
    "the birdman": "Chris Andersen",
}

# Built once: an alternation of every nickname, longest first so "greek freak"
# wins over a hypothetical "greek". Word boundaries are spelled as lookarounds
# rather than \b because several keys end in a non-word character ("a.i."),
# where \b asserts the opposite of what is wanted.
_NICKNAME_RE = re.compile(
    r"(?<![\w])(" + "|".join(re.escape(k) for k in sorted(PLAYER_NICKNAMES, key=len, reverse=True)) + r")(?![\w])",
    re.IGNORECASE,
)


def nicknames_in(question: str) -> list[str]:
    """Player names for every nickname appearing as a whole word in ``question``,
    in the order they appear, without repeats.

    Matched against the user's own words, which is the only place a nickname
    still exists: by the time the router has filled a slot it has usually
    rewritten the nickname, and when it rewrites one wrongly there is nothing
    downstream to notice - see :func:`override_nicknames`.

    .. versionadded:: 2.1.0
    """
    seen: list[str] = []
    for match in _NICKNAME_RE.finditer(question):
        name = PLAYER_NICKNAMES[match.group(1).casefold()]
        if name not in seen:
            seen.append(name)
    return seen


def override_nicknames(question: str, slots: dict[str, Any]) -> list[tuple[str, str]]:
    """Replace router-supplied player slots with what the question's nicknames
    actually mean. Mutates ``slots``; returns the ``(was, now)`` pairs changed.

    The router rewrites a nickname it recognizes, and *invents* one it does not:
    measured against qwen2.5:3b, "The Answer" became `player='Klay Thompson'`
    and "The Glove" became `player='Jayson Tatum'` - each a real player, each
    resolving cleanly, each producing a confident answer about the wrong person.
    Nothing after the router can catch that, because the nickname is already
    gone. So the question itself is the authority, and this runs before any
    template sees the slots.

    Deliberately conservative, because a wrong override is the same class of bug
    as the one being fixed. A single ``player`` slot is only overridden when the
    question names exactly one nickname; a ``players`` list only when it names
    exactly as many as the list holds, and then positionally, since nothing else
    says which name belongs to which slot. Anything else is left alone.

    .. versionadded:: 2.1.0
    """
    names = nicknames_in(question)
    if not names:
        return []

    changed: list[tuple[str, str]] = []
    players = slots.get("players")
    if isinstance(players, list) and players:
        if len(names) != len(players):
            return []
        for i, (was, now) in enumerate(zip(players, names, strict=True)):
            if was != now:
                players[i] = now
                changed.append((str(was), now))
        return changed

    if len(names) != 1:
        return []
    was = slots.get("player")
    if isinstance(was, str) and was.strip() and was != names[0]:
        slots["player"] = names[0]
        changed.append((was, names[0]))
    return changed


@dataclass(frozen=True)
class Entity:
    """One resolved player or team: an opaque warehouse id and its display name."""

    id: str
    name: str


@dataclass(frozen=True)
class Ambiguous:
    """`candidates` is for telling the user what to disambiguate between - it
    is the reason this is a return value and not just None."""

    query: str
    candidates: list[str]


@dataclass(frozen=True)
class NotFound:
    """Nothing matched ``query`` - distinct from :class:`Ambiguous`, where too
    much did."""

    query: str


Resolution = Entity | Ambiguous | NotFound


MAX_CLARIFY_CANDIDATES = 5
"""How many candidates a "did you mean" sentence names before it starts
counting the rest instead.

.. versionadded:: 2.1.0
"""


def clarification(text: str, candidates: list[str], kind: str = "player") -> str:
    """The "did you mean" sentence for an ambiguous name.

    Lives here rather than in :mod:`association.query.templates` because both
    halves of the query path ask it now: a template returns it as its answer,
    and a chart's rendering entry point returns it as a message. One phrasing,
    so the same ambiguity does not read two ways depending on which path the
    router happened to take.

    .. versionadded:: 2.1.0
    """
    shown, extra = candidates[:MAX_CLARIFY_CANDIDATES], len(candidates) - MAX_CLARIFY_CANDIDATES
    joined = ", ".join(shown[:-1]) + f" or {shown[-1]}" + (f" ({extra} others also match)" if extra > 0 else "")
    return f"{text!r} matches more than one {kind} - did you mean {joined}?"


@dataclass(frozen=True)
class Availability:
    """The table a chart's rows come from, for narrowing a name to the players
    who could actually have produced the chart being asked for.

    A declared constant next to the code that owns the table, rather than a
    string literal at the call site: the name is interpolated into SQL, so the
    set of tables that can appear there stays small, named and checkable.

    .. versionadded:: 2.1.0
    """

    table: str


def narrow_to_available(con: duckdb.DuckDBPyConnection, candidates: list[Entity], source: Availability, season: int | None = None) -> list[Entity]:
    """The candidates with at least one row in ``source``, in the order given -
    for ``season`` when one is given, and in any season when it is not.

    ``season`` is optional because a chart's is: a request that names no season
    is drawn over a whole career, and narrowing that by one year would be
    filtering the candidates by something the question never said.

    Elimination, never preference. It drops the candidates who cannot be the
    answer to the question asked; it does not choose between two who both can.
    That distinction is the whole reason this is allowed to exist where the
    prominence tiebreak recorded above ``PLAYER_NICKNAMES`` was rejected -
    ranking by minutes or points also "resolved" Brown and Mitchell, which no
    question carries on its own. "Maxey" matches Marlon (last played 1994) and
    Tyrese; only one of them has a 2026 fingerprint, and that is a fact about
    the warehouse rather than a guess about who was meant.

    Returns an empty list when none of them has data, which the caller must
    handle rather than treat as "no such player": it means the question is
    unanswerable for everybody named, which is a different sentence.

    .. versionadded:: 2.1.0
    """
    if not candidates:
        return []
    placeholders = ", ".join("?" for _ in candidates)
    where = f"athlete_id IN ({placeholders})" + ("" if season is None else " AND season = ?")
    rows = con.execute(
        f"SELECT DISTINCT athlete_id FROM {source.table} WHERE {where}",
        [*(c.id for c in candidates)] + ([] if season is None else [season]),
    ).fetchall()
    have = {str(row[0]) for row in rows}
    return [c for c in candidates if c.id in have]


def _exact(candidates: list[Entity], text: str, keys: tuple[str, ...] = ("name",)) -> Entity | None:
    """An exact, case-insensitive hit on a full name (or abbreviation) beats
    any number of substring hits - otherwise a real full name that happens to
    be a prefix of another ("Jaylen Brown" vs. a hypothetical "Jaylen Brown
    Jr.") would resolve as ambiguous even though the user named one exactly."""
    wanted = text.strip().casefold()
    hits = [c for c in candidates if any(getattr(c, k).casefold() == wanted for k in keys)]
    return hits[0] if len(hits) == 1 else None


# A token matches "strongly" when it starts a word in the name rather than
# landing anywhere inside it. The boundary is any non-letter, not a space, so
# the halves of a hyphenated or apostrophed name each start a word: "Alexander"
# is a strong match for Shai Gilgeous-Alexander and Nickeil Alexander-Walker,
# "Neal" for Shaquille O'Neal.
_WORD_START = "(^|[^A-Za-z])"


def find_players(con: duckdb.DuckDBPyConnection, text: str) -> list[Entity]:
    """Every token must match, so "Luka Doncic" doesn't also match a player
    sharing only a first name.

    .. versionchanged:: 2.1.0
       Candidates matching at a word boundary rank first and, when there are
       any, are the only ones returned. Incidental substring hits used to be
       ordered among them purely by name: "Ball" answered with Cedric Ceballos.
    """
    # Matched against the WHOLE query, never as a substring, so "book" resolves
    # to Devin Booker while "notebook" is untouched - and "Ant" stops matching
    # every player with "ant" in their name (Durant, Anthony, Antetokounmpo).
    text = PLAYER_NICKNAMES.get(text.strip().casefold(), text)
    tokens = [t for t in text.split() if t]
    if not tokens:
        return []
    where = " AND ".join(["display_name ILIKE ?"] * len(tokens))
    # Word-boundary matches rank first and, when there are any, are the whole
    # answer - the same two-step find_teams uses, and for the same reason:
    # substring matching keeps a name honestly ambiguous, but an INCIDENTAL hit
    # is not a candidate a person would recognize. "Ball" offered Cedric
    # Ceballos ahead of LaMelo, "Bey" offered Mike Tobey ahead of Saddiq, and
    # "Ford" offered Al Horford ahead of Aleem Ford - each of them the pick a
    # best-match caller then drew a chart of. Ordering inside the query rather
    # than filtering after it is load-bearing: LIMIT would otherwise be free to
    # truncate the strong matches away in favor of alphabetically earlier weak
    # ones.
    strong = " AND ".join(["regexp_matches(display_name, ?, 'i')"] * len(tokens))
    rows = con.execute(
        f"SELECT athlete_id, display_name, ({strong}) AS strong FROM players WHERE {where} ORDER BY strong DESC, display_name LIMIT {MAX_CANDIDATES}",
        [_WORD_START + re.escape(t) for t in tokens] + [f"%{t}%" for t in tokens],
    ).fetchall()
    matched = [row for row in rows if row[2]] or rows
    return [Entity(id=str(r[0]), name=r[1]) for r in matched]


def find_teams(con: duckdb.DuckDBPyConnection, text: str) -> list[Entity]:
    """Substring matching is kept (so "LA" stays honestly ambiguous rather than
    silently resolving to whichever team is literally named "LA"), but matches
    that start a word are ranked first - otherwise "LA" offers "Atlanta Hawks"
    as a candidate, which makes a clarification look broken."""
    rows = con.execute(
        "SELECT team_id, display_name, "
        "  (team_id = ? OR abbreviation ILIKE ? OR display_name ILIKE ? OR display_name ILIKE ?) AS strong "
        "FROM teams WHERE team_id = ? OR abbreviation ILIKE ? OR display_name ILIKE ? "
        f"ORDER BY strong DESC, display_name LIMIT {MAX_CANDIDATES}",
        # strong = an id/abbreviation hit, or a name match that starts a word:
        # 'LA%' catches "LA Clippers", '% LA%' catches "Los Angeles Lakers".
        [text, text, f"{text}%", f"% {text}%", text, text, f"%{text}%"],
    ).fetchall()
    strong = [Entity(id=str(r[0]), name=r[1]) for r in rows if r[2]]
    # Incidental substring hits ("LA" inside "Atlanta") are dropped whenever a
    # word-boundary match exists, so a clarification offers plausible teams
    # rather than everything the LIKE happened to touch.
    return strong or [Entity(id=str(r[0]), name=r[1]) for r in rows]


def _resolve(candidates: list[Entity], text: str, exact_keys: tuple[str, ...]) -> Resolution:
    if not candidates:
        return NotFound(query=text)
    if len(candidates) == 1:
        return candidates[0]
    exact = _exact(candidates, text, exact_keys)
    return exact if exact is not None else Ambiguous(query=text, candidates=[c.name for c in candidates])


def resolve_player(con: duckdb.DuckDBPyConnection, text: str) -> Resolution:
    """ "Curry" is genuinely ambiguous (Seth and Stephen), and a leaderboard
    row attributed to the wrong one is indistinguishable from a right answer."""
    return _resolve(find_players(con, text), text, ("name",))


def resolve_team(con: duckdb.DuckDBPyConnection, text: str) -> Resolution:
    """One team, or a refusal. See :func:`resolve_player` for why ambiguity is
    returned rather than resolved."""
    return _resolve(find_teams(con, text), text, ("name", "id"))
