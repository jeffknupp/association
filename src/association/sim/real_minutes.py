"""The real game's lineups as a rotation policy: a diagnostic, not a model.

A replay normally predicts who is on the floor with
:class:`~association.sim.rotation.RotationModel`. With this policy the five
are the real game's, read from ``sim_segments`` (build-order step 2) and
followed by the clock: at every dead ball the policy names the lineup of
the segment the simulated game has reached, so the simulated minutes are
the real minutes and only the possessions are simulated. Comparing a
validation run under this policy with the normal one sizes the rotation
model's share of the remaining error (``association sim validate
--real-minutes``); it is never a forecast, because minutes are an outcome.

Where the real lineup cannot be followed the policy fills honestly: a real
player who has fouled out or been ejected in the simulation is replaced by
the available teammate with the most real seconds still to come, then by
roster order; a simulated overtime a real game did not have keeps the last
real lineup; an unresolved segment's lineup (fewer than five ids) is filled
the same way.

.. versionadded:: 6.0.0
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass

import duckdb

from association.sim.params import RotationContext

Segment = tuple[float, float, tuple[str, ...]]
"""One real segment for one team: its start and end in seconds from
tip-off and the five on the floor.

.. versionadded:: 6.0.0
"""


@dataclass(frozen=True)
class RealLineups:
    """A :class:`~association.sim.params.RotationPolicy` that follows one
    team's real segments by the clock (module docstring).

    .. versionadded:: 6.0.0
    """

    team_id: str
    segments: tuple[Segment, ...]

    def __post_init__(self) -> None:
        if not self.segments:
            raise ValueError(f"team {self.team_id} has no real segments to follow")

    def at(self, elapsed: float) -> tuple[str, ...]:
        """The real lineup at ``elapsed`` seconds from tip-off: the segment
        that started last at or before it, the first before the game, the
        last after its end."""
        starts = [s for s, _, _ in self.segments]
        index = max(0, bisect_right(starts, elapsed) - 1)
        return self.segments[index][2]

    def remaining(self, elapsed: float) -> dict[str, float]:
        """Each player's real seconds still to come after ``elapsed``."""
        out: dict[str, float] = {}
        for start, end, lineup in self.segments:
            seconds = max(0.0, end - max(start, elapsed))
            if seconds > 0.0:
                for athlete in lineup:
                    out[athlete] = out.get(athlete, 0.0) + seconds
        return out

    def lineup(self, context: RotationContext) -> tuple[str, ...]:
        """The real five at the context's time, among the available; filled
        from the available roster by real seconds still to come, then by
        roster order, when fewer than five of them can play."""
        available = [p.athlete_id for p in context.players if p.available]
        five = [a for a in dict.fromkeys(self.at(context.elapsed)) if a in available]
        if len(five) < 5:
            remaining = self.remaining(context.elapsed)
            order = {a: i for i, a in enumerate(available)}
            for athlete in sorted((a for a in available if a not in five), key=lambda a: (-remaining.get(a, 0.0), order[a])):
                five.append(athlete)
                if len(five) == 5:
                    break
        return tuple(five[:5])


def read_lineups(con: duckdb.DuckDBPyConnection, event_id: str, season: int, home: str, away: str) -> dict[str, RealLineups]:
    """Both teams' policies for one game from ``sim_segments``, keyed by team
    id. Unresolved segments are used as recorded (their lineups are the best
    record there is; a short one is filled at play time).

    .. versionadded:: 6.0.0
    """
    rows = con.execute("SELECT start_elapsed, end_elapsed, home_lineup, away_lineup FROM sim_segments WHERE event_id = ? AND season = ? ORDER BY period, segment_no", [event_id, season]).fetchall()
    if not rows:
        raise ValueError(f"game {event_id} has no segments in sim_segments (association sim build)")
    home_segments: list[Segment] = [(float(s), float(e), tuple(str(a) for a in h or ())) for s, e, h, _ in rows]
    away_segments: list[Segment] = [(float(s), float(e), tuple(str(a) for a in w or ())) for s, e, _, w in rows]
    return {home: RealLineups(home, tuple(home_segments)), away: RealLineups(away, tuple(away_segments))}


def describe(policies: Sequence[RealLineups]) -> str:
    """The note a prepared replay carries when it follows real lineups."""
    return "rotation: the real game's lineups, followed by the clock (a diagnostic - minutes are given, not predicted; " + ", ".join(f"{p.team_id} {len(p.segments)} segments" for p in policies) + ")"
