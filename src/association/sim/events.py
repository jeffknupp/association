"""What a simulated game produces: the structured events, one box line per
player, one total per team and the game record that holds them.

Decision D11 of ``SIMULATION_PLAN.md``: the engine emits data and renders
nothing. A play-by-play in ESPN's style, a box score and a summary over N
games are step 7's, written from these records; nothing here is a sentence.
Every record is a frozen dataclass so two games from the same seed and
inputs compare equal field by field (the engine's determinism test).

.. versionadded:: 6.0.0
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

EVENT_KINDS: frozenset[str] = frozenset({"period_start", "period_end", "possession", "shot", "free_throw", "rebound", "turnover", "foul", "foul_out", "substitution"})
"""The kinds a :class:`SimEvent` can be, closed: a renderer switches on them.

``possession`` opens a possession (its ``outcome`` is the start type:
``period_start``, ``made_shot``, ``made_free_throw``, ``defensive_rebound``,
``live_turnover`` or ``dead_ball``); ``shot`` is a field-goal attempt
(``made``, ``missed`` or ``blocked``; the heave is a ``shot`` in the
``heave`` zone); ``free_throw`` one attempt (``made`` or ``missed``);
``rebound`` is ``offensive`` or ``defensive``, with no rebounder when it is
the team's; ``turnover`` is ``live`` or ``dead``; ``foul`` is ``shooting``,
``and_one`` or ``non_shooting`` and is credited to the fouler's team;
``foul_out`` names the player who left on his sixth; ``substitution`` names
who came ``in`` and who went ``out``.

.. versionadded:: 6.0.0
"""


@dataclass(frozen=True)
class SimEvent:
    """One thing that happened, with the running score after it.

    ``team`` is the team the event is credited to: the offense for a
    possession, a shot, a free throw, a turnover or an offensive rebound;
    the defense for a defensive rebound; the fouler's team for a foul; the
    team substituting for a substitution; ``""`` for a period boundary.
    ``actors`` maps a role (``shooter``, ``assister``, ``blocker``,
    ``rebounder``, ``stealer``, ``fouler``, ``fouled``, ``player``, ``in``,
    ``out``) to an athlete id. ``zone`` is set on a shot and on a shooting
    foul (the zone the trip was drawn from), ``""`` otherwise. ``points`` are
    the points this event scored, 0 for most.

    .. versionadded:: 6.0.0
    """

    kind: str
    period: int
    clock_remaining: float
    elapsed: float
    team: str
    actors: Mapping[str, str]
    zone: str
    outcome: str
    points: int
    home_score: int
    away_score: int


@dataclass(frozen=True)
class BoxLine:
    """One player's line: seconds on the floor and the box-score counts, with
    his plus-minus over the seconds he played.

    .. versionadded:: 6.0.0
    """

    athlete_id: str
    team_id: str
    seconds: float
    points: int
    fgm: int
    fga: int
    tpm: int
    tpa: int
    ftm: int
    fta: int
    oreb: int
    dreb: int
    ast: int
    stl: int
    blk: int
    tov: int
    pf: int
    plus_minus: int

    @property
    def minutes(self) -> float:
        """Seconds on the floor as minutes."""
        return self.seconds / 60


@dataclass(frozen=True)
class TeamTotals:
    """One team's totals over its box lines, with its possessions and its
    team fouls by period (index 0 is period 1).

    .. versionadded:: 6.0.0
    """

    team_id: str
    points: int
    fgm: int
    fga: int
    tpm: int
    tpa: int
    ftm: int
    fta: int
    oreb: int
    dreb: int
    ast: int
    stl: int
    blk: int
    tov: int
    pf: int
    possessions: int
    team_fouls: tuple[int, ...]
    seconds: float


@dataclass(frozen=True)
class Decision:
    """One draw the engine made, recorded when it is asked to trace: which
    event it led to (``event_index``, the index in ``SimGame.events`` of the
    next event emitted), what kind of decision (``duration``, ``user``,
    ``action``, ``zone``, ``make``, ``block``, ``blocker``, ``assisted``,
    ``assister``, ``and_one``, ``offensive_rebound``, ``rebounder``,
    ``live_ball``, ``stealer``, ``three_shot_trip``, ``free_throw``,
    ``fouler``, ``fouled``, ``non_shooting_foul``, ``last_action``,
    ``rotation``), what was chosen, the uniform draw that chose it (``nan``
    where the choice was a weighted pick whose draw is internal), the
    candidates with their probabilities, and the inputs the probability was
    computed from (a make's player, defense and league rates and the home
    multiplier; a duration's mean and spread).

    .. versionadded:: 6.0.0
    """

    event_index: int
    kind: str
    chosen: str
    draw: float
    probabilities: Mapping[str, float]
    inputs: Mapping[str, float]


@dataclass(frozen=True)
class SimGame:
    """A simulated game: the seed it was played from, the final score, the
    periods played (4, or more with overtime), every event in order, a box
    line per rostered player (a player who never entered has zeros) and the
    two teams' totals.

    .. versionadded:: 6.0.0
    """

    seed: int
    home_team: str
    away_team: str
    home_score: int
    away_score: int
    periods: int
    events: tuple[SimEvent, ...]
    lines: tuple[BoxLine, ...]
    home: TeamTotals
    away: TeamTotals
    decisions: tuple[Decision, ...] = ()

    @property
    def overtimes(self) -> int:
        """How many overtime periods were played."""
        return max(self.periods - 4, 0)
