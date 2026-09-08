"""Resolving a name the model produced ("Lakers", "LAL", "Curry") to a real id.

Callers want different things from an ambiguous name - a chart of the wrong
Curry is a visible mistake, a NUMBER attributed to the wrong Curry is not - so
both behaviours stay available rather than one being picked for everyone:

    find_*    - every candidate, best first. The caller decides.
    resolve_* - one entity, or Ambiguous/NotFound. Never a guess.

Templates use resolve_*, because a template's job is to be trusted with a
number. Ambiguity is returned as a value, and the template asks a clarifying
question (see templates._clarify) rather than guessing or falling through."""

from __future__ import annotations

from dataclasses import dataclass

import duckdb

MAX_CANDIDATES = 10

# Curated shorthand -> the player it unambiguously means. This is NOT the
# prominence tiebreak that was measured and rejected (no minutes/points ratio
# separates "Luka" Doncic from Garza without also wrongly resolving "Brown"):
# it is an explicit, auditable list, and a name absent from it still gets the
# clarifying question rather than a guess. "Luka" and "Curry" are deliberately
# NOT here - they are ordinary first names and surnames shared with real
# players, and asking is the honest answer.
#
# Confirmed live: every value below matches exactly one row in `players`.
PLAYER_NICKNAMES = {
    "sga": "Shai Gilgeous-Alexander",
    "wemby": "Victor Wembanyama",
    "kd": "Kevin Durant",
    "cp3": "Chris Paul",
    "steph": "Stephen Curry",
    "the greek freak": "Giannis Antetokounmpo",
    "greek freak": "Giannis Antetokounmpo",
    "giannis": "Giannis Antetokounmpo",
    "joker": "Nikola Jokic",
    "ad": "Anthony Davis",
    "dame": "Damian Lillard",
    "pg13": "Paul George",
    "kat": "Karl-Anthony Towns",
    "bron": "LeBron James",
    "lebron": "LeBron James",
    "ant": "Anthony Edwards",
    "jrue": "Jrue Holiday",
    "trae": "Trae Young",
    "zion": "Zion Williamson",
    "book": "Devin Booker",
    "klay": "Klay Thompson",
}


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


def _exact(candidates: list[Entity], text: str, keys: tuple[str, ...] = ("name",)) -> Entity | None:
    """An exact, case-insensitive hit on a full name (or abbreviation) beats
    any number of substring hits - otherwise a real full name that happens to
    be a prefix of another ("Jaylen Brown" vs. a hypothetical "Jaylen Brown
    Jr.") would resolve as ambiguous even though the user named one exactly."""
    wanted = text.strip().casefold()
    hits = [c for c in candidates if any(getattr(c, k).casefold() == wanted for k in keys)]
    return hits[0] if len(hits) == 1 else None


def find_players(con: duckdb.DuckDBPyConnection, text: str) -> list[Entity]:
    """Every token must match, so "Luka Doncic" doesn't also match a player
    sharing only a first name."""
    # Matched against the WHOLE query, never as a substring, so "book" resolves
    # to Devin Booker while "notebook" is untouched - and "Ant" stops matching
    # every player with "ant" in their name (Durant, Anthony, Antetokounmpo).
    text = PLAYER_NICKNAMES.get(text.strip().casefold(), text)
    tokens = [t for t in text.split() if t]
    if not tokens:
        return []
    where = " AND ".join(["display_name ILIKE ?"] * len(tokens))
    rows = con.execute(
        f"SELECT athlete_id, display_name FROM players WHERE {where} ORDER BY display_name LIMIT {MAX_CANDIDATES}",
        [f"%{t}%" for t in tokens],
    ).fetchall()
    return [Entity(id=str(r[0]), name=r[1]) for r in rows]


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
