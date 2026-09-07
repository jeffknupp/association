"""Resolving a name the model produced ("Lakers", "LAL", "Curry") to a real id.

Three callers need this and two of them had grown their own version:
get_leaderboard resolved teams and errored on ambiguity, render_shot_chart
resolved players and silently took the first match. Those are both defensible
for what they do - a chart of the wrong Curry is a visible mistake, a NUMBER
attributed to the wrong Curry is not - so this module keeps the distinction
explicit rather than picking one behaviour for everyone:

    find_*    - every candidate, best first. The caller decides.
    resolve_* - one entity, or Ambiguous/NotFound. Never a guess.

Templates use resolve_*, because a template's whole job is to be trusted with
a number. Ambiguity there is returned, not resolved, so the question falls
through to the agent instead of being answered about the wrong player."""

from __future__ import annotations

from dataclasses import dataclass

import duckdb

MAX_CANDIDATES = 10


@dataclass(frozen=True)
class Entity:
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
    rows = con.execute(
        "SELECT team_id, display_name FROM teams "
        "WHERE team_id = ? OR abbreviation ILIKE ? OR display_name ILIKE ? "
        f"ORDER BY display_name LIMIT {MAX_CANDIDATES}",
        [text, text, f"%{text}%"],
    ).fetchall()
    return [Entity(id=str(r[0]), name=r[1]) for r in rows]


def _resolve(candidates: list[Entity], text: str, exact_keys: tuple[str, ...]) -> Resolution:
    if not candidates:
        return NotFound(query=text)
    if len(candidates) == 1:
        return candidates[0]
    exact = _exact(candidates, text, exact_keys)
    return exact if exact is not None else Ambiguous(query=text, candidates=[c.name for c in candidates])


def resolve_player(con: duckdb.DuckDBPyConnection, text: str) -> Resolution:
    """"Curry" is genuinely ambiguous (Seth and Stephen), and a leaderboard
    row attributed to the wrong one is indistinguishable from a right answer."""
    return _resolve(find_players(con, text), text, ("name",))


def resolve_team(con: duckdb.DuckDBPyConnection, text: str) -> Resolution:
    return _resolve(find_teams(con, text), text, ("name", "id"))
