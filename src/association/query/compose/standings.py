"""A team's record from ESPN's standings - one season's line (the record,
its home and road split, the last ten games, games back, the seed, the
streak, points for and against), or every season's added up, overall or
at home or on the road - read over the team-season relation
(:mod:`association.query.team_seasons`) into a
:class:`~association.query.result.Result` with a
:class:`~association.query.result.Scalar` record body (``how="record"``)
and, for a season with a home/road split, a
:class:`~association.query.result.Grouped` part by venue. Phase 2's slice
(iv): the retired ``team_record`` template's standings reads
(``templates.teams._standings_season``, ``_standings_career``) moved here,
their statements into the relation, their sentences to the sayer
(:func:`association.query.compose.say.say_team_record`).

The standings are the authoritative source for a regular season's record:
their "Home" and "Road" strings agree with a tally of the game list for
every team-season from 1994 to 2026 once each era's neutral-site rule is
applied (through 2024 a neutral-site game counts for its designated home
team; from 2025 it counts as neither).

.. versionadded:: 5.0.0
"""

from __future__ import annotations

from typing import Any

import duckdb

from association.query.entities import Entity
from association.query.notes import Note
from association.query.result import Grouped, Narrowing, Part, Result, Scalar, Span
from association.query.team_metrics import FIRST_FULL_REGULAR_SEASON
from association.query.team_seasons import games_played, record_text, standings_career, standings_first_season, standings_season

from .core import values_of


def _standings_short(con: duckdb.DuckDBPyConnection, team: Entity, seasons: list[tuple[int, int]]) -> list[Note]:
    """A note for seasons whose standings cover fewer games than the team's
    own season totals (``seasons`` is (season, games in standings)). Short
    only: that is the failure measured, and the one a reader cannot see
    from the row."""
    if not seasons:
        return []
    totals = dict(values_of(con, games_played(team.id, [s for s, _ in seasons])))
    short = [(s, g, int(totals[s])) for s, g in seasons if s in totals and totals[s] and g < int(totals[s])]
    if not short:
        return []
    return [Note("standings_short", {"team": team.name, "seasons": [{"season": int(s), "held": int(g), "played": t} for s, g, t in short]})]


def _home_road(home_text: Any, road_text: Any) -> tuple[tuple[int, int], tuple[int, int]] | None:
    """The standings' home and road records, where the season has a split -
    "0-0" is how standings say "no split", every season before 1993-94."""
    home, road = record_text(home_text), record_text(road_text)
    return (home, road) if home and road and sum(home) + sum(road) > 0 else None


def read_standings_season(con: duckdb.DuckDBPyConnection, team: Entity, season: int, venue: str | None) -> Result:
    """One regular season's standings line - or, with ``venue``, its home or
    road record beside the season's - and the remarks: the neutral-site
    games in neither half (from 2025), the games the standings fall short
    of the team's own total.

    .. versionadded:: 5.0.0
    """
    found = values_of(con, standings_season(team.id, season))
    span = Span(season=season, season_type=2, source="team_seasons")
    narrowing = Narrowing(venue=venue)
    if not found:
        return Result(subject=team.name, relation="team", span=span, narrowing=narrowing, parts=(Part(body=Scalar(games=0, how="record")),))
    wins, losses, win_pct, streak, seed, behind, home_text, road_text, last_ten, points_for, points_against, differential = found[0]
    # standings stores these as DOUBLE; a 53-29 record is never "53.0-29.0".
    w, lost = int(wins), int(losses)
    split = _home_road(home_text, road_text)
    # Neutral-site games count as neither home nor away from 2025 on, so the
    # halves can sum to less than the whole.
    neutral = w + lost - sum(split[0]) - sum(split[1]) if split else 0
    notes = [Note("definition", {"term": "neutral_site", "games": neutral})] if neutral > 0 else []
    if not (venue is not None and split is None):
        # Where a venue is asked of a season with no split, the answer is
        # that refusal alone.
        notes += _standings_short(con, team, [(season, w + lost)])
    values = {
        "wins": w,
        "losses": lost,
        "win_pct": win_pct,
        "streak": streak,
        "seed": seed,
        "games_behind": behind,
        "last_ten": last_ten,
        "points_for": points_for,
        "points_against": points_against,
        "differential": differential,
        "neutral": neutral,
    }
    parts = [Part(body=Scalar(games=w + lost, values=values, how="record"))]
    if split is not None:
        parts.append(Part(role="detail", body=Grouped(by="venue", rows=({"key": "home", "wins": split[0][0], "losses": split[0][1]}, {"key": "road", "wins": split[1][0], "losses": split[1][1]}))))
    return Result(subject=team.name, relation="team", span=span, narrowing=narrowing, parts=tuple(parts), notes=tuple(notes))


def read_standings_career(con: duckdb.DuckDBPyConnection, team: Entity, venue: str | None) -> Result:
    """Every regular season's standings added up - or, with ``venue``, every
    home or road record over the seasons whose standings carry a split -
    with where the span starts (the warehouse's standings, or the first
    season it holds for the team), the neutral-site games in neither half,
    and the seasons the standings fall short.

    .. versionadded:: 5.0.0
    """
    rows = values_of(con, standings_career(team.id))
    narrowing = Narrowing(venue=venue)
    if not rows:
        return Result(subject=team.name, relation="team", span=Span(season_type=2, career=True, source="team_seasons"), narrowing=narrowing, parts=(Part(body=Scalar(games=0, how="record")),))
    if venue is not None:
        return _standings_career_venue(con, team, venue, rows)
    wins = sum(int(r[1]) for r in rows)
    losses = sum(int(r[2]) for r in rows)
    first, last = rows[0][0], rows[-1][0]
    # The start is the warehouse's, not the franchise's, and saying which is
    # the whole difference between an all-time record and a partial one.
    notes = [Note("floor", {"table": "standings", "first": int(first)})] if first == min(r[0] for r in values_of(con, standings_first_season())) else []
    notes += _standings_short(con, team, [(int(r[0]), int(r[1]) + int(r[2])) for r in rows])
    return Result(
        subject=team.name,
        relation="team",
        span=Span(season_type=2, career=True, first=first, last=last, source="team_seasons"),
        narrowing=narrowing,
        parts=(Part(body=Scalar(games=wins + losses, values={"wins": wins, "losses": losses, "seasons": len(rows)}, how="record")),),
        notes=tuple(notes),
    )


def _standings_career_venue(con: duckdb.DuckDBPyConnection, team: Entity, venue: str, rows: list[tuple[Any, ...]]) -> Result:
    """Every season's home or road record added up, over the seasons whose
    standings carry a split."""
    halves = []
    for season, wins, losses, home_text, road_text in rows:
        split = _home_road(home_text, road_text)
        if split is not None:
            halves.append((season, int(wins) + int(losses), split[0] if venue == "home" else split[1], sum(split[0]) + sum(split[1])))
    narrowing = Narrowing(venue=venue)
    if not halves:
        span = Span(season_type=2, career=True, source="team_seasons")
        return Result(subject=team.name, relation="team", span=span, narrowing=narrowing, parts=(Part(body=Scalar(games=0, how="record", values={"seasons": 0})),))
    vw = sum(h[2][0] for h in halves)
    vl = sum(h[2][1] for h in halves)
    neutral = sum(h[1] - h[3] for h in halves)
    first, last = halves[0][0], halves[-1][0]
    notes = [Note("floor", {"table": "standings", "first": FIRST_FULL_REGULAR_SEASON, "what": "home_road_split"})] if first == FIRST_FULL_REGULAR_SEASON else []
    if neutral > 0:
        notes.append(Note("definition", {"term": "neutral_site", "games": neutral}))
    notes += _standings_short(con, team, [(h[0], h[1]) for h in halves])
    return Result(
        subject=team.name,
        relation="team",
        span=Span(season_type=2, career=True, first=first, last=last, source="team_seasons"),
        narrowing=narrowing,
        parts=(Part(body=Scalar(games=vw + vl, values={"wins": vw, "losses": vl, "seasons": len(halves)}, how="record")),),
        notes=tuple(notes),
    )
