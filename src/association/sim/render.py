"""Words and tables for a simulated game: the play-by-play in ESPN's phrasing,
the box score, the summary over many games, and the same three as JSON.

Phase 7 of ``SIMULATION_PLAN.md`` (decision D11). Everything here takes the
engine's data (:mod:`association.sim.events`) and the game module's
:class:`association.sim.game.Summary`, plus a :class:`Names` lookup, and
returns text or plain dicts; nothing here reads the warehouse or runs a
simulation, so a renderer can be tested on a toy game and the same data can
be printed, written as JSON or shown in a page later.

Two conventions, both so a reader can tell a simulated line from a real one:
every rendering states the window the rates came from and the seed, and a
player simulated at nearly the league prior is marked in the box score and
listed in the summary (``AGENTS.md``: a default must say what it did).

.. versionadded:: 6.0.0
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from association.sim.events import BoxLine, SimEvent, SimGame
from association.sim.params import HEAVE

SCHEMA_VERSION = "sim.1"
"""The version written into every JSON rendering, bumped when a key changes.

.. versionadded:: 6.0.0
"""

ZONE_PHRASES: Mapping[str, str] = {
    "restricted": "layup",
    "paint": "shot in the paint",
    "midrange": "mid-range jumper",
    "corner_three": "corner three",
    "above_break_three": "three-pointer",
    HEAVE: "heave from beyond half court",
}
"""How a shot from each zone is described. The engine knows zones, not shot
types, so the phrase names the zone.

.. versionadded:: 6.0.0
"""


@dataclass(frozen=True)
class Names:
    """Who everyone is: athlete id to display name, team id to abbreviation
    and to full name. An id the maps lack renders as itself.

    .. versionadded:: 6.0.0
    """

    players: Mapping[str, str] = field(default_factory=dict)
    abbreviations: Mapping[str, str] = field(default_factory=dict)
    teams: Mapping[str, str] = field(default_factory=dict)

    def player(self, athlete_id: str) -> str:
        """The player's display name, or his id."""
        return self.players.get(athlete_id, athlete_id)

    def abbreviation(self, team_id: str) -> str:
        """The team's abbreviation, or its id."""
        return self.abbreviations.get(team_id, team_id)

    def team(self, team_id: str) -> str:
        """The team's full name, or its id."""
        return self.teams.get(team_id, team_id)


def clock_text(seconds: float) -> str:
    """ESPN's clock: ``11:42`` with a minute or more left, ``42.3`` under one.

    .. versionadded:: 6.0.0
    """
    if seconds >= 60:
        whole = int(seconds)
        return f"{whole // 60}:{whole % 60:02d}"
    return f"{seconds:.1f}"


def period_label(period: int) -> str:
    """``Q1`` to ``Q4``, then ``OT``, ``2OT``, ...

    .. versionadded:: 6.0.0
    """
    if period <= 4:
        return f"Q{period}"
    return "OT" if period == 5 else f"{period - 4}OT"


def _period_name(period: int) -> str:
    ordinal = {1: "1st Quarter", 2: "2nd Quarter", 3: "3rd Quarter", 4: "4th Quarter"}
    if period in ordinal:
        return ordinal[period]
    return "Overtime" if period == 5 else f"Overtime {period - 4}"


def _shot_text(e: SimEvent, names: Names) -> str:
    shooter = names.player(e.actors.get("shooter", "?"))
    phrase = ZONE_PHRASES.get(e.zone, "shot")
    if e.outcome == "made":
        assisted = f" ({names.player(e.actors['assister'])} assists)" if "assister" in e.actors else ""
        return f"{shooter} makes {phrase}{assisted}"
    if e.outcome == "blocked":
        return f"{names.player(e.actors.get('blocker', '?'))} blocks {shooter}'s {phrase}"
    return f"{shooter} misses {phrase}"


def _foul_text(e: SimEvent, names: Names) -> str:
    fouler = names.player(e.actors.get("fouler", "?"))
    if e.outcome in ("shooting", "and_one"):
        drawn = f" ({names.player(e.actors['fouled'])} draws the foul)" if "fouled" in e.actors else ""
        return f"{fouler} shooting foul{drawn}"
    return f"{fouler} personal foul"


def _rebound_text(e: SimEvent, names: Names) -> str:
    side = "offensive" if e.outcome == "offensive" else "defensive"
    if "rebounder" in e.actors:
        return f"{names.player(e.actors['rebounder'])} {side} rebound"
    return f"{names.team(e.team)} {side} team rebound"


def _turnover_text(e: SimEvent, names: Names) -> str:
    player = names.player(e.actors.get("player", "?"))
    if e.outcome == "live" and "stealer" in e.actors:
        return f"{player} turnover ({names.player(e.actors['stealer'])} steals)"
    return f"{player} turnover"


def describe(e: SimEvent, names: Names, free_throw: tuple[int, int] | None = None, last_period: bool = False) -> str | None:
    """One event as ESPN would write it, or ``None`` for an event that is not
    a line of play-by-play (the possession marker). ``free_throw`` is the
    attempt's ``(number, of)`` within its trip; ``last_period`` turns the
    final period's end into "End of Game".

    .. versionadded:: 6.0.0
    """
    if e.kind == "shot":
        return _shot_text(e, names)
    if e.kind == "free_throw":
        n, of = free_throw or (1, 1)
        verb = "makes" if e.outcome == "made" else "misses"
        return f"{names.player(e.actors.get('shooter', '?'))} {verb} free throw {n} of {of}"
    if e.kind == "rebound":
        return _rebound_text(e, names)
    if e.kind == "turnover":
        return _turnover_text(e, names)
    if e.kind == "foul":
        return _foul_text(e, names)
    if e.kind == "foul_out":
        return f"{names.player(e.actors.get('player', '?'))} fouls out"
    if e.kind == "substitution":
        return f"{names.player(e.actors.get('in', '?'))} enters the game for {names.player(e.actors.get('out', '?'))}"
    if e.kind == "period_start":
        return f"Start of the {_period_name(e.period)}"
    if e.kind == "period_end":
        return "End of Game" if last_period else f"End of the {_period_name(e.period)}"
    return None


def _free_throw_positions(events: Sequence[SimEvent]) -> dict[int, tuple[int, int]]:
    """Each free throw's ``(number, of)`` within its trip: consecutive free
    throws by one shooter at one clock, substitutions between them allowed."""
    positions: dict[int, tuple[int, int]] = {}
    i = 0
    while i < len(events):
        e = events[i]
        if e.kind != "free_throw":
            i += 1
            continue
        trip = [i]
        j = i + 1
        while j < len(events) and events[j].kind in ("free_throw", "substitution") and events[j].clock_remaining == e.clock_remaining and events[j].period == e.period:
            if events[j].kind == "free_throw" and events[j].actors.get("shooter") == e.actors.get("shooter"):
                trip.append(j)
            elif events[j].kind == "free_throw":
                break
            j += 1
        for n, k in enumerate(trip, start=1):
            positions[k] = (n, len(trip))
        i = trip[-1] + 1
    return positions


def play_by_play(game: SimGame, names: Names) -> str:
    """The game as lines of play-by-play: period and clock, the play, the
    score after it (away first, as ESPN prints it).

    .. versionadded:: 6.0.0
    """
    home, away = names.abbreviation(game.home_team), names.abbreviation(game.away_team)
    positions = _free_throw_positions(game.events)
    lines = [f"{away} at {home} - simulated game, seed {game.seed}"]
    for i, e in enumerate(game.events):
        text = describe(e, names, positions.get(i), last_period=e.period == game.periods)
        if text is None:
            continue
        score = f"{away} {e.away_score}-{e.home_score} {home}"
        lines.append(f"{period_label(e.period):>3} {clock_text(e.clock_remaining):>5}  {text:<64} {score}")
    return "\n".join(lines)


_BOX_COLUMNS = ("MIN", "PTS", "FG", "3PT", "FT", "OREB", "DREB", "REB", "AST", "STL", "BLK", "TOV", "PF", "+/-")


def _box_row(label: str, line: BoxLine) -> str:
    cells = (
        f"{line.minutes:.0f}",
        str(line.points),
        f"{line.fgm}-{line.fga}",
        f"{line.tpm}-{line.tpa}",
        f"{line.ftm}-{line.fta}",
        str(line.oreb),
        str(line.dreb),
        str(line.oreb + line.dreb),
        str(line.ast),
        str(line.stl),
        str(line.blk),
        str(line.tov),
        str(line.pf),
        f"{line.plus_minus:+d}",
    )
    return f"{label:<28}" + "".join(f"{c:>6}" for c in cells)


def _team_box(game: SimGame, team_id: str, names: Names, near_prior: Iterable[str]) -> list[str]:
    marked = set(near_prior)
    lines = sorted((line for line in game.lines if line.team_id == team_id), key=lambda line: (-line.seconds, -line.points, line.athlete_id))
    totals = game.home if team_id == game.home_team else game.away
    out = [f"{names.team(team_id)} ({names.abbreviation(team_id)})  {totals.points} points, {totals.possessions} possessions", f"{'':<28}" + "".join(f"{c:>6}" for c in _BOX_COLUMNS)]
    for line in lines:
        if line.seconds <= 0:
            continue
        label = names.player(line.athlete_id) + (" *" if line.athlete_id in marked else "")
        out.append(_box_row(label, line))
    total_line = BoxLine(
        team_id,
        team_id,
        totals.seconds,
        totals.points,
        totals.fgm,
        totals.fga,
        totals.tpm,
        totals.tpa,
        totals.ftm,
        totals.fta,
        totals.oreb,
        totals.dreb,
        totals.ast,
        totals.stl,
        totals.blk,
        totals.tov,
        totals.pf,
        0,
    )
    out.append(_box_row("Totals", total_line).rsplit(" ", 1)[0].rstrip() + f"{'':>6}")
    return out


def box_score(game: SimGame, names: Names, near_prior: Iterable[str] = ()) -> str:
    """Both teams' box scores, the away side first; a player marked ``*`` was
    simulated at nearly the league prior.

    .. versionadded:: 6.0.0
    """
    home, away = names.abbreviation(game.home_team), names.abbreviation(game.away_team)
    ot = f", {game.overtimes} OT" if game.overtimes else ""
    header = [f"{away} {game.away_score}, {home} {game.home_score} (simulated, seed {game.seed}{ot})"]
    body = [*_team_box(game, game.away_team, names, near_prior), "", *_team_box(game, game.home_team, names, near_prior)]
    note = ["", "* simulated at nearly the league prior (under 100 charted shots in the window)"] if set(near_prior) & {line.athlete_id for line in game.lines if line.seconds > 0} else []
    return "\n".join([*header, "", *body, *note])


def game_json(game: SimGame, names: Names, meta: Mapping[str, Any]) -> dict[str, Any]:
    """One game as plain data: the meta (window, seed, ...), the score, every
    event and every box line.

    .. versionadded:: 6.0.0
    """
    return {
        "schema_version": SCHEMA_VERSION,
        **dict(meta),
        "seed": game.seed,
        "home": {"team_id": game.home_team, "abbreviation": names.abbreviation(game.home_team), "name": names.team(game.home_team), "score": game.home_score},
        "away": {"team_id": game.away_team, "abbreviation": names.abbreviation(game.away_team), "name": names.team(game.away_team), "score": game.away_score},
        "periods": game.periods,
        "events": [{**asdict(e), "actors": dict(e.actors)} for e in game.events],
        "lines": [{**asdict(line), "name": names.player(line.athlete_id)} for line in game.lines if line.seconds > 0],
        "totals": {"home": asdict(game.home), "away": asdict(game.away)},
    }
