"""``sim_possessions`` and ``sim_actions``: every possession of every game, and
the events inside it.

Phase 3 of ``SIMULATION_PLAN.md`` (build-order step 9, first half). A
**possession** is one team's turn with the ball; an **action** is a play
inside it that P4 counts - a shot with its zone, a free throw with its
place in the trip, a rebound and whose it was, a turnover and whether the
ball was live, a foul and who drew it, a jump ball and who gained it, a
violation. Both are built per game from ``sim_plays`` in ``seq`` order by a
state machine in Python (DuckDB reads and writes in bulk, as
:mod:`association.sim.segments` does), and every number below comes from
the plays themselves, never from the running score (0b, 0.8).

**Boundaries** (P3.1, with what the data forced; the measurements are
``scripts/sim_p9/`` and the plan's section 0b):

- A **made shot** ends the possession - unless a free throw by the same team
  follows at the same clock, the and-one, in which case the possession runs
  through the trip and ends on its last free throw. The plays between a made
  shot and its free throw (the foul, substitutions, a timeout, a technical)
  are held back until one or the other is known, so a foul at that clock
  lands in the possession it belongs to. 95.6% of ``1 of 1`` rows follow a
  made shot by the same team at the same clock (2025: 5,894 of 6,168).
- A **trip's last free throw** ends the possession if made (``made_ft``); if
  missed, the rebound that follows decides. The dead-ball team rebound ESPN
  logs after a missed free throw that is NOT the last (5,738 of 5,783 missed
  ``1 of 2`` in 2025) ends nothing and is marked ``dead_ball``; so is the
  one after a missed technical free throw.
- A **lone one-of-one** - a ``1 of 1`` with no made shot at its clock - ends
  nothing: the fouled team keeps the ball (an away-from-play, inbound or
  transition take foul). Measured 2025: after a made lone ``1 of 1`` the
  same team's shot, turnover or rebound follows 190 times, the other
  team's 13; after a missed one a dead-ball team rebound follows 44 times
  of 47. A **flagrant or clear-path trip** keeps the ball too (2025: the
  same team shoots next after 41 of 41 made ``Flagrant 2 of 2``).
- A **defensive rebound** ends the possession, a team's as much as a
  player's; an offensive rebound, team or player, continues it. A player's
  side is his team against the offense (ESPN's label disagrees on 55-210
  rows a season from 2018, counted as ``side_disagreements``); a TEAM
  rebound's side is its label, because its ``team_id`` is the SHOOTING
  team's on a defensive team rebound through 2012 and the rebounding
  team's from 2013 (0b, 0.14). A made putback's offensive rebound is logged
  AFTER the putback at the same clock in 2007-2017 (2,000-3,400 a season)
  and is absorbed into the possession the shot ends.
- A scoring row with no team (2016: one ``No Shot`` row) is an orphan
  action in no possession, so its points are nobody's; ``teamless`` counts
  such rows.
- A **turnover** ends the possession, live or dead by ``sim_plays.live_ball``
  (``NULL`` is dead). The turnover half of an offensive foul is the ending
  row; 99.5-99.9% of ``foul_offensive`` rows carry one.
- A **jump ball** names who gained the ball but ends nothing by itself:
  when the defense wins a held ball, ESPN logs a turnover and a steal next
  in two of three cases (2025: 238 of 348), and that row is the ending;
  otherwise the gainer's team's next touch closes the possession as
  ``jump_ball``. The gainer is the THIRD participant and his team comes
  from the roster, because ESPN's ``team_id`` on a jump ball is the gainer's
  team in 2007-2012 and 2018-2026 but a coin flip in 2013-2017 (0b, 0.14).
  A jump ball that opens a period names the first offense.
- The **end of a period** ends the possession. A possession with no action
  and no time (a buzzer-beater's would-be inbound) is dropped.
- **Technical free throws and technical fouls belong to no possession**
  (``poss_no`` NULL); their points are the other half of the score check.
- A play by the OTHER team where the state says the ball was not theirs
  (a shot, free throw or turnover; a rebound is read by its team) closes the
  possession before it and opens theirs on it. How it closed is read from
  the last action: ``jump_ball`` when that was a jump ball this team gained,
  ``offensive_foul`` when it was a personal or flagrant foul by the offense
  (a loose-ball foul on the rebounder: a change of possession without a
  turnover), else ``implied`` - a change the plays never logged, counted
  per season.

``start_type`` is one of :data:`START_TYPES`, from the previous possession's
``end_type`` (:data:`END_TYPES`): ``made_shot`` -> ``after_made``,
``made_ft`` -> ``after_ft``, ``dreb_shot`` and ``dreb_ft`` -> ``after_dreb``,
the two turnovers to their own, everything else -> ``after_other``.
``segment_no`` on a possession is the segment current at its start and on
an action the one current at the action (substitutions happen during
trips), the latest segment of the period starting at or before the play.

**Excluded:** a game whose plays do not sum to ``real_games``' final for both
teams (``plays.SeasonReport.points_match_final``'s rule) is left out of both
tables and counted (``games_excluded``): 0.2-0.6% of games from 2019,
4.7% of 2022 (the unlabeled shot values), 31% of 2013 and 50% of 2016 (made
shots typed as misses; both below the estimation window).

**Acceptance**, measured on this checkout's warehouse (``nba.duckdb`` copied
2026-09-24) by ``scripts/sim_p9/build_and_check.py`` on 2026-10-12
(``results/build_2026-10-12.out``; 2007-2026 in 128 seconds after the 44 of
plays and segments). Per season: games kept, possessions a team-game, the
score check (possession points plus technical free throws against the final,
per team-game), the mean absolute and signed difference against the box
formula over player lines, the two teams' counts within the number of
periods, the exact-match share of player-games for FGA / FTA / OREB / DREB /
AST / TOV, and the possessions closed as ``implied``:

====== ===== ======= ======== ======== ====== ======= ===== ===== ===== ===== ===== ===== =======
season games poss/tg score ok box diff signed periods   FGA   FTA  OREB  DREB   AST   TOV implied
====== ===== ======= ======== ======== ====== ======= ===== ===== ===== ===== ===== ===== =======
2019   1,307   101.2     100%     1.94  -1.27    100% 99.46 99.94 99.28 99.15 99.70 99.48     205
2020   1,141   101.3     100%     1.95  -1.29    100% 99.63 99.93 99.58 99.43 99.86 99.51     103
2021   1,165   100.1     100%     1.83  -1.10    100% 99.61 99.98 99.71 99.55 99.88 99.58     139
2022   1,255    99.1     100%     1.89  -1.18    100% 99.62 99.98 99.59 99.52 99.83 99.63     130
2023   1,308   100.2     100%     1.93  -1.25    100% 99.63 99.94 99.80 99.69 99.74 99.81     148
2024   1,306    99.2     100%     1.90  -1.21    100% 99.71 99.98 99.72 99.62 99.80 99.73     103
2025   1,313    99.7     100%     1.94  -1.21    100% 99.66 99.97 99.79 99.50 99.71 99.74     101
2026   1,313   100.5     100%     1.97  -1.15    100% 95.79 99.95 99.72 99.55 99.70 99.73     113
====== ===== ======= ======== ======== ====== ======= ===== ===== ===== ===== ===== ===== =======

Every season 2007-2026 passes the score gate at 100% and the box formula
within 2 (1.65-1.98); 2007-2012 run 93-94 possessions a team-game with
425-672 implied changes a season, 2013-2018 93-98 with 335-1,214 (2013,
the made-as-missed season). The formula over player lines runs about 1.2
possessions ABOVE the count from 2019, as it must: it subtracts no team
offensive rebound (5.5-6.4 live ones a team-game) and adds no team turnover
(0.6-0.8), and 0.44 of the free throws is its own guess at the trips. Per 48
team-minutes the count is 99.2 in 2025 against NBA.com's published 98.8 for
2024-25 (``rates.py`` cites it), 0.4% high. 2026's FGA share is the heaves:
ESPN's box does not count a typed ``Heave Jump Shot`` as an attempt, and
28,309 of 28,502 player-games match once the 1,149 heaves are left out
(99.3%). 2016's FGA 97.0% and 2017's DREB 97.1% are the seasons whose plays
do not add up to the box (``DATA.md``). End types a team-game, 2025: made
shot 39.3, defensive rebound after a shot 33.2, made free throw 9.2, live
turnover 8.3, dead turnover 6.0, defensive rebound after a free throw 2.1,
period end 1.8, implied 0.03, offensive foul 0.01, jump ball under 0.01.

.. versionadded:: 6.0.0
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field, replace
from itertools import groupby
from typing import Any

import duckdb
import pyarrow as pa

from association.sim import zones
from association.sim.database import WAREHOUSE
from association.sim.segments import period_end_elapsed

log: logging.Logger = logging.getLogger("association.sim.possessions")

START_TYPES: tuple[str, ...] = ("period_start", "after_made", "after_dreb", "after_live_turnover", "after_dead_turnover", "after_ft", "after_other")
"""How a possession began, from the previous possession's ``end_type``.

.. versionadded:: 6.0.0
"""

END_TYPES: tuple[str, ...] = ("made_shot", "made_ft", "dreb_shot", "dreb_ft", "live_turnover", "dead_turnover", "jump_ball", "offensive_foul", "period_end", "implied")
"""How a possession ended. ``implied`` is a change of possession the plays
never logged (the other team shot or turned it over next); ``offensive_foul``
the same where the last action was a personal or flagrant foul by the
offense, which is a change of possession without a turnover.

.. versionadded:: 6.0.0
"""

_START_OF: dict[str, str] = {
    "made_shot": "after_made",
    "made_ft": "after_ft",
    "dreb_shot": "after_dreb",
    "dreb_ft": "after_dreb",
    "live_turnover": "after_live_turnover",
    "dead_turnover": "after_dead_turnover",
}

FREE_THROW_PATTERN: re.Pattern[str] = re.compile(r"Free Throw - (?:(Flagrant|Clear Path) )?(\d) of (\d)")
"""ESPN's free-throw types: ``Free Throw - 1 of 2``, ``- Flagrant 2 of 2``,
``- Clear Path 1 of 2``; ``Free Throw - Technical`` has no count.

.. versionadded:: 6.0.0
"""

REGULAR, FLAGRANT, CLEAR_PATH, TECHNICAL = "regular", "flagrant", "clear_path", "technical"
"""The kinds of free throw :func:`free_throw_shape` tells apart."""

ACTION_KINDS: frozenset[str] = frozenset(
    {
        "shot",
        "unknown_score",
        "free_throw",
        "offensive_rebound",
        "defensive_rebound",
        "rebound",
        "turnover",
        "foul_personal",
        "foul_shooting",
        "foul_offensive",
        "foul_technical",
        "foul_flagrant",
        "jump_ball",
        "violation",
    }
)
"""The play kinds that become actions. Substitutions, timeouts, reviews,
ejections and the period markers do not.

.. versionadded:: 6.0.0
"""

# Plays that may sit between a made shot and its and-one free throw at the
# same clock without settling whether there is one.
_HELD_KINDS: frozenset[str] = frozenset({"substitution", "timeout", "review", "ejection", "ignore", "violation", "foul_personal", "foul_shooting", "foul_offensive", "foul_technical", "foul_flagrant"})
_REBOUND_KINDS: frozenset[str] = frozenset({"offensive_rebound", "defensive_rebound", "rebound"})


def free_throw_shape(type_: str | None) -> tuple[str, int, int]:
    """``(kind, number, of)`` for a free-throw type: ``("regular", 2, 2)`` for
    ``Free Throw - 2 of 2``, ``("flagrant", 1, 2)``, ``("clear_path", 1,
    2)``, ``("technical", 1, 1)``. An unrecognized type is read as a regular
    one-of-one.

    .. versionadded:: 6.0.0
    """
    if type_ is None:
        return REGULAR, 1, 1
    if "Technical" in type_:
        return TECHNICAL, 1, 1
    m = FREE_THROW_PATTERN.match(type_)
    if m is None:
        return REGULAR, 1, 1
    kind = {None: REGULAR, "Flagrant": FLAGRANT, "Clear Path": CLEAR_PATH}[m.group(1)]
    return kind, int(m.group(2)), int(m.group(3))


@dataclass(frozen=True, slots=True)
class PlayRow:
    """The fields of a ``sim_plays`` row the walk reads, with the shot's zone
    from the shot chart.

    .. versionadded:: 6.0.0
    """

    seq: int
    period: int
    elapsed: float
    kind: str | None
    type: str | None
    team_id: str | None
    athlete_id: str | None
    participants: tuple[str, ...]
    text: str
    points: int | None
    scoring: bool
    shot_value: int | None
    live_ball: bool | None
    team_play: bool
    period_end: bool
    zone: str | None

    @property
    def partner(self) -> str | None:
        """The second participant, or ``None``."""
        return self.participants[1] if len(self.participants) >= 2 else None


@dataclass
class Possession:
    """One possession as the walk builds it; ``poss_no`` is assigned when it
    closes and is kept.

    .. versionadded:: 6.0.0
    """

    period: int
    offense: str | None
    start_seq: int
    start_elapsed: float
    start_type: str
    poss_no: int = 0
    end_seq: int = 0
    end_elapsed: float = 0.0
    end_type: str = ""
    points: int = 0
    n_actions: int = 0


@dataclass
class Action:
    """One event inside a possession (or beside one, for a technical).

    .. versionadded:: 6.0.0
    """

    play: PlayRow
    possession: Possession | None
    team_id: str | None
    athlete_id: str | None
    partner_id: str | None = None
    partner_role: str | None = None
    made: bool | None = None
    points: int = 0
    ft_kind: str | None = None
    ft_number: int | None = None
    ft_of: int | None = None
    live_ball: bool | None = None
    dead_ball: bool = False
    ends_possession: bool = False


@dataclass
class GameWalk:
    """The state machine over one game's plays.

    .. versionadded:: 6.0.0
    """

    event_id: str
    season: int
    home_team_id: str
    away_team_id: str
    roster: dict[str, str]
    possessions: list[Possession] = field(default_factory=list)
    actions: list[Action] = field(default_factory=list)
    counts: Counter[str] = field(default_factory=Counter)
    _period: int = 0
    _current: Possession | None = None
    _held: list[PlayRow] | None = None
    _held_shot: Action | None = None
    _and_one: bool = False
    _trip_open: bool = False
    _dead_rebound: bool = False
    _last_miss: str | None = None
    _last_action: Action | None = None

    # -- feeding ---------------------------------------------------------

    def feed(self, play: PlayRow) -> None:
        """Advance the state by one play, in ``seq`` order."""
        if play.period != self._period:
            self._start_period(play)
        if self._held is not None:
            self._feed_held(play)
        else:
            self._handle(play)
        if play.period_end:
            self._end_period(play)

    def _feed_held(self, play: PlayRow) -> None:
        """A made shot is waiting to learn whether an and-one follows."""
        assert self._held is not None and self._held_shot is not None
        shot = self._held_shot.play
        same_clock = play.elapsed == shot.elapsed
        technical = play.kind == "free_throw" and free_throw_shape(play.type)[0] == TECHNICAL
        if same_clock and (play.kind in _HELD_KINDS or technical):
            self._held.append(play)
            return
        offense = self._held_shot.possession.offense if self._held_shot.possession else None
        if same_clock and play.kind in _REBOUND_KINDS and self._rebound_team(play) == offense:
            self._handle(play)  # the rebound that set up the putback, logged after it (2007-2017: 2,000-3,400 a season)
            return
        if same_clock and play.kind == "free_throw" and self._team(play) == offense:
            self.counts["and_ones"] += 1
            self._release_held()
            self._and_one = True
        else:
            self._close(shot.seq, shot.elapsed, "made_shot")
            self._open(self._other(offense), (self._held[0] if self._held else play).seq, shot.elapsed, "after_made")
            self._release_held()
        self._handle(play)

    def _release_held(self) -> None:
        """Feed the plays held since the made shot into the possession now current."""
        held, self._held, self._held_shot = self._held or [], None, None
        for p in held:
            self._handle(p)

    def _handle(self, play: PlayRow) -> None:
        kind = play.kind
        if kind in ("shot", "unknown_score"):
            self._shot(play)
        elif kind == "free_throw":
            self._free_throw(play)
        elif kind in _REBOUND_KINDS:
            self._rebound(play)
        elif kind == "turnover":
            self._turnover(play)
        elif kind == "jump_ball":
            self._jump_ball(play)
        elif kind is not None and kind.startswith("foul_"):
            self._foul(play)
        elif kind == "violation":
            self._record(play, self._team(play), play.athlete_id)

    # -- periods -----------------------------------------------------------

    def _start_period(self, play: PlayRow) -> None:
        if self._current is not None:  # a period whose last row was not flagged; sim_plays flags every one
            self._close(play.seq - 1, period_end_elapsed(self._period), "period_end")
        self._period = play.period
        self._open(None, play.seq, period_end_elapsed(play.period - 1) if play.period > 1 else 0.0, "period_start")

    def _end_period(self, play: PlayRow) -> None:
        """The period's last row: a made shot still waiting for its and-one
        ends the possession where it was, and the open possession ends with
        the period."""
        if self._held is not None and self._held_shot is not None:
            shot = self._held_shot.play
            offense = self._held_shot.possession.offense if self._held_shot.possession else None
            self._close(shot.seq, shot.elapsed, "made_shot")
            self._open(self._other(offense), (self._held[0] if self._held else play).seq, shot.elapsed, "after_made")
            self._release_held()
        if self._current is not None:
            self._close(play.seq, period_end_elapsed(self._period), "period_end")

    # -- possessions -------------------------------------------------------

    def _open(self, offense: str | None, start_seq: int, start_elapsed: float, start_type: str) -> None:
        self._current = Possession(self._period, offense, start_seq, start_elapsed, start_type)
        self._trip_open = self._dead_rebound = False
        self._last_miss = None
        self._last_action = None

    def _close(self, end_seq: int, end_elapsed: float, end_type: str) -> None:
        current = self._current
        if current is None:
            return
        current.end_seq, current.end_elapsed, current.end_type = end_seq, end_elapsed, end_type
        if current.n_actions or (current.offense is not None and current.end_elapsed > current.start_elapsed):
            current.poss_no = len(self.possessions) + 1
            self.possessions.append(current)
        else:
            self.counts["dropped_empty"] += 1
        self._current = None
        self._trip_open = self._dead_rebound = False
        self._last_miss = None

    def _change(self, play: PlayRow, end_type: str, new_offense: str | None, *, includes_play: bool = True) -> None:
        """Close the current possession at ``play`` and open the next after
        it - or, when ``play`` is the other team's and belongs to the next
        possession, close before it and open on it."""
        self._close(play.seq if includes_play else play.seq - 1, play.elapsed, end_type)
        self._open(new_offense, play.seq + 1 if includes_play else play.seq, play.elapsed, _START_OF.get(end_type, "after_other"))

    def _ensure_offense(self, play: PlayRow, team: str) -> None:
        """The play says ``team`` has the ball: record that, or if the state
        says otherwise close the possession and open theirs. How it closed
        is read from its last action: a jump ball this team gained, a
        personal or flagrant foul by the offense (a loose-ball foul on the
        rebounder: a change without a turnover), else ``implied``."""
        current = self._current
        assert current is not None
        if current.offense is None:
            current.offense = team
        elif current.offense != team:
            last = self._last_action
            end_type = "implied"
            if last is not None and last.play.kind == "jump_ball" and last.team_id == team:
                end_type = "jump_ball"
            elif last is not None and last.play.kind in ("foul_personal", "foul_flagrant") and last.team_id == current.offense:
                end_type = "offensive_foul"
            if last is not None and end_type != "implied":
                last.ends_possession = True
            self.counts[{"jump_ball": "jump_ball_changes", "offensive_foul": "offensive_fouls"}.get(end_type, "implied")] += 1
            self._change(play, end_type, team, includes_play=False)

    # -- helpers -------------------------------------------------------------

    def _team(self, play: PlayRow) -> str | None:
        return play.team_id if play.team_id in (self.home_team_id, self.away_team_id) else None

    def _other(self, team: str | None) -> str | None:
        if team is None:
            return None
        return self.away_team_id if team == self.home_team_id else self.home_team_id

    def _record(self, play: PlayRow, team: str | None, athlete: str | None, *, in_possession: bool = True, **facts: Any) -> Action:
        possession = self._current if in_possession else None
        action = Action(play, possession, team, athlete, **facts)
        self.actions.append(action)
        if possession is not None:
            possession.n_actions += 1
            possession.points += action.points
            self._last_action = action
        return action

    @staticmethod
    def _role(play: PlayRow, word: str, role: str) -> tuple[str | None, str | None]:
        partner = play.partner
        if partner is not None and word in play.text.lower():
            return partner, role
        return None, None

    # -- the kinds -----------------------------------------------------------

    def _shot(self, play: PlayRow) -> None:
        team = self._team(play)
        if team is None:
            self.counts["teamless"] += 1
            self._record(play, None, play.athlete_id, in_possession=False, made=play.scoring, points=play.points or 0)
            return
        self._ensure_offense(play, team)
        made = play.scoring
        partner, role = self._role(play, "assist", "assist") if made else self._role(play, "block", "block")
        action = self._record(play, team, play.athlete_id, partner_id=partner, partner_role=role, made=made, points=play.points or 0)
        self._dead_rebound = self._trip_open = False
        if made:
            action.ends_possession = True
            self._held, self._held_shot = [], action
            self._last_miss = None
        else:
            self._last_miss = "shot"

    def _free_throw(self, play: PlayRow) -> None:
        ft_kind, number, of = free_throw_shape(play.type)
        made = play.scoring
        if ft_kind == TECHNICAL:
            self._record(play, self._team(play), play.athlete_id, in_possession=False, made=made, points=play.points or 0, ft_kind=ft_kind, ft_number=number, ft_of=of)
            self._dead_rebound = not made
            return
        team = self._team(play)
        and_one, self._and_one = self._and_one, False
        if team is None:
            self.counts["teamless"] += 1
            self._record(play, None, play.athlete_id, in_possession=False, made=made, points=play.points or 0, ft_kind=ft_kind, ft_number=number, ft_of=of)
            return
        self._ensure_offense(play, team)
        action = self._record(play, team, play.athlete_id, made=made, points=play.points or 0, ft_kind=ft_kind, ft_number=number, ft_of=of)
        self._dead_rebound = False
        if ft_kind != REGULAR:
            self._dead_rebound = not made  # the fouled team keeps the ball; a miss is a dead ball
            self._trip_open = number < of
            return
        if number < of:
            self._trip_open, self._dead_rebound = True, not made
            return
        lone = of == 1 and not self._trip_open and not and_one
        self._trip_open = False
        if lone:
            self.counts["lone_one_of_ones"] += 1
            self._last_miss = None if made else "ft"
            return
        if made:
            action.ends_possession = True
            self._change(play, "made_ft", self._other(team))
        else:
            self._last_miss = "ft"

    def _rebound_team(self, play: PlayRow) -> str | None:
        """Whose rebound: a player's is his team's; a TEAM rebound's side is
        its label, because ESPN's ``team_id`` on a defensive team rebound is
        the SHOOTING team's through 2012 and the rebounding team's from 2013
        (0b, 0.14), so the label is the one constant. ``None`` where the
        offense is unknown and only a team rebound says anything."""
        team = self._team(play)
        if play.athlete_id is not None or play.kind == "rebound" or team is None:
            return team
        offense = self._current.offense if self._current else None
        if offense is None:
            return None
        return offense if play.kind == "offensive_rebound" else self._other(offense)

    def _rebound(self, play: PlayRow) -> None:
        team = self._rebound_team(play)
        if team is None:
            self.counts["teamless"] += 1
            self._record(play, None, play.athlete_id)
            return
        if self._dead_rebound:
            self._dead_rebound = False
            self.counts["dead_rebounds"] += 1
            self._record(play, team, play.athlete_id, dead_ball=True)
            return
        current = self._current
        assert current is not None
        if current.offense is None:
            current.offense = team if play.kind != "defensive_rebound" else self._other(team)
        offensive = team == current.offense
        if play.athlete_id is not None and play.kind != "rebound" and (play.kind == "offensive_rebound") != offensive:
            self.counts["side_disagreements"] += 1
        if play.athlete_id is None and play.team_id != team:
            self.counts["team_rebound_ids"] += 1
        if self._last_miss is None:
            self.counts["rebounds_without_miss"] += 1
        action = self._record(play, team, play.athlete_id)
        if offensive:
            self._last_miss = None
            return
        action.ends_possession = True
        self._change(play, "dreb_ft" if self._last_miss == "ft" else "dreb_shot", team)

    def _turnover(self, play: PlayRow) -> None:
        team = self._team(play)
        if team is None:
            self.counts["teamless"] += 1
            self._record(play, None, play.athlete_id, live_ball=play.live_ball)
            return
        self._ensure_offense(play, team)
        partner, role = self._role(play, "steal", "steal")
        live = play.live_ball is True
        self._record(play, team, play.athlete_id, partner_id=partner, partner_role=role, live_ball=live, ends_possession=True)
        self._change(play, "live_turnover" if live else "dead_turnover", self._other(team))

    def _jump_ball(self, play: PlayRow) -> None:
        """A jump ball names who gained the ball but ends nothing itself: when
        the defense wins a held ball ESPN logs a turnover and a steal next
        in two of three cases (2025: 238 of 348), and that row ends the
        possession; otherwise the gainer's next touch does, as
        ``jump_ball`` (:meth:`_ensure_offense`)."""
        gainer = play.participants[2] if len(play.participants) >= 3 else None
        gainer_team = self.roster.get(gainer) if gainer else None
        self._record(play, gainer_team, play.athlete_id, partner_id=gainer, partner_role="gains_possession" if gainer else None)
        current = self._current
        if gainer_team is not None and current is not None and current.offense is None:
            current.offense = gainer_team

    def _foul(self, play: PlayRow) -> None:
        partner, role = self._role(play, "draws the foul", "fouled")
        self._record(play, self._team(play), play.athlete_id, in_possession=play.kind != "foul_technical", partner_id=partner, partner_role=role)


def walk_game(walk: GameWalk, plays: Sequence[PlayRow]) -> GameWalk:
    """Build the possessions and actions of one game from its plays in ``seq``
    order.

    .. versionadded:: 6.0.0
    """
    for play in plays:
        walk.feed(play)
    return walk


# -- reading and writing ---------------------------------------------------------


def _has_table(con: duckdb.DuckDBPyConnection, name: str) -> bool:
    row = con.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema = 'main' AND table_catalog = current_database() AND table_name = ?", [name]).fetchone()
    return bool(row and row[0])


def _season_plays(con: duckdb.DuckDBPyConnection, season: int) -> Iterator[tuple[str, list[PlayRow]]]:
    zone = zones.zone_sql("sp.shot_value", "sp.clock_remaining", "sc.shot_type")
    rows = con.execute(
        f"""
        SELECT sp.event_id, sp.seq, sp.period, sp.elapsed, sp.kind, sp.type, sp.team_id, sp.athlete_id, sp.participants, sp.text,
               sp.points, coalesce(sp.scoring_play, false), sp.shot_value, sp.live_ball, coalesce(sp.team_play, false), sp.period_end,
               CASE WHEN sp.kind = 'shot' THEN {zone} END AS zone
        FROM sim_plays sp
        LEFT JOIN (SELECT play_id, season, shot_type, coordinate_x, coordinate_y FROM {WAREHOUSE}.shot_chart WHERE season = ?) sc
          ON sc.play_id = sp.play_id AND sc.season = sp.season
        WHERE sp.season = ? ORDER BY sp.event_id, sp.seq
        """,
        [season, season],
    ).fetchall()
    for event_id, group in groupby(rows, key=lambda r: r[0]):
        yield event_id, [PlayRow(r[1], r[2], float(r[3]), r[4], r[5], r[6], r[7], tuple(r[8] or ()), r[9] or "", r[10], bool(r[11]), r[12], r[13], bool(r[14]), bool(r[15]), r[16]) for r in group]


def _season_games(con: duckdb.DuckDBPyConnection, season: int) -> tuple[dict[str, GameWalk], int]:
    """A walk per game whose plays sum to the final, and the count of games
    that do not (excluded)."""
    rows = con.execute(
        f"""
        SELECT sp.event_id, g.home_team_id, g.away_team_id,
               coalesce(sum(sp.points) FILTER (WHERE sp.team_id = g.home_team_id), 0) = g.home_score
               AND coalesce(sum(sp.points) FILTER (WHERE sp.team_id = g.away_team_id), 0) = g.away_score AS points_match
        FROM sim_plays sp JOIN {WAREHOUSE}.real_games g ON g.event_id = sp.event_id AND g.season = sp.season
        WHERE sp.season = ? GROUP BY sp.event_id, g.home_team_id, g.away_team_id, g.home_score, g.away_score
        """,
        [season],
    ).fetchall()
    games = {event_id: GameWalk(event_id, season, home, away, {}) for event_id, home, away, ok in rows if ok}
    for event_id, athlete_id, team_id in con.execute(f"SELECT event_id, athlete_id, team_id FROM {WAREHOUSE}.player_box_stats_deduped WHERE season = ?", [season]).fetchall():
        game = games.get(event_id)
        if game is not None:
            game.roster[athlete_id] = team_id
    return games, sum(1 for r in rows if not r[3])


_POSSESSIONS_SCHEMA = pa.schema(
    [
        ("event_id", pa.string()),
        ("season", pa.int64()),
        ("period", pa.int64()),
        ("poss_no", pa.int64()),
        ("offense_team_id", pa.string()),
        ("start_seq", pa.int64()),
        ("end_seq", pa.int64()),
        ("start_elapsed", pa.float64()),
        ("end_elapsed", pa.float64()),
        ("start_type", pa.string()),
        ("end_type", pa.string()),
        ("points", pa.int64()),
        ("actions", pa.int64()),
    ]
)

_ACTIONS_SCHEMA = pa.schema(
    [
        ("event_id", pa.string()),
        ("season", pa.int64()),
        ("period", pa.int64()),
        ("poss_no", pa.int64()),
        ("seq", pa.int64()),
        ("elapsed", pa.float64()),
        ("kind", pa.string()),
        ("type", pa.string()),
        ("team_id", pa.string()),
        ("athlete_id", pa.string()),
        ("partner_id", pa.string()),
        ("partner_role", pa.string()),
        ("zone", pa.string()),
        ("shot_value", pa.int64()),
        ("made", pa.bool_()),
        ("points", pa.int64()),
        ("ft_kind", pa.string()),
        ("ft_number", pa.int64()),
        ("ft_of", pa.int64()),
        ("team_play", pa.bool_()),
        ("live_ball", pa.bool_()),
        ("dead_ball", pa.bool_()),
        ("ends_possession", pa.bool_()),
    ]
)


def _possession_rows(game: GameWalk) -> list[dict[str, Any]]:
    return [
        {
            "event_id": game.event_id,
            "season": game.season,
            "period": p.period,
            "poss_no": p.poss_no,
            "offense_team_id": p.offense,
            "start_seq": p.start_seq,
            "end_seq": p.end_seq,
            "start_elapsed": p.start_elapsed,
            "end_elapsed": p.end_elapsed,
            "start_type": p.start_type,
            "end_type": p.end_type,
            "points": p.points,
            "actions": p.n_actions,
        }
        for p in game.possessions
    ]


def _action_rows(game: GameWalk) -> list[dict[str, Any]]:
    rows = []
    for a in game.actions:
        kept = a.possession is not None and a.possession.poss_no > 0
        rows.append(
            {
                "event_id": game.event_id,
                "season": game.season,
                "period": a.play.period,
                "poss_no": a.possession.poss_no if kept and a.possession else None,
                "seq": a.play.seq,
                "elapsed": a.play.elapsed,
                "kind": a.play.kind,
                "type": a.play.type,
                "team_id": a.team_id,
                "athlete_id": a.athlete_id,
                "partner_id": a.partner_id,
                "partner_role": a.partner_role,
                "zone": a.play.zone,
                "shot_value": a.play.shot_value if a.play.kind == "shot" else None,
                "made": a.made,
                "points": a.points,
                "ft_kind": a.ft_kind,
                "ft_number": a.ft_number,
                "ft_of": a.ft_of,
                "team_play": a.play.team_play,
                "live_ball": a.live_ball,
                "dead_ball": a.dead_ball,
                "ends_possession": a.ends_possession,
            }
        )
    return rows


def _create_raw_tables(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("""
        CREATE OR REPLACE TABLE sim_possessions_raw (
            event_id VARCHAR, season BIGINT, period BIGINT, poss_no BIGINT, offense_team_id VARCHAR, start_seq BIGINT, end_seq BIGINT,
            start_elapsed DOUBLE, end_elapsed DOUBLE, start_type VARCHAR, end_type VARCHAR, points BIGINT, actions BIGINT)""")
    con.execute("""
        CREATE OR REPLACE TABLE sim_actions_raw (
            event_id VARCHAR, season BIGINT, period BIGINT, poss_no BIGINT, seq BIGINT, elapsed DOUBLE, kind VARCHAR, type VARCHAR,
            team_id VARCHAR, athlete_id VARCHAR, partner_id VARCHAR, partner_role VARCHAR, zone VARCHAR, shot_value BIGINT, made BOOLEAN,
            points BIGINT, ft_kind VARCHAR, ft_number BIGINT, ft_of BIGINT, team_play BOOLEAN, live_ball BOOLEAN, dead_ball BOOLEAN, ends_possession BOOLEAN)""")


def _attach_segments(con: duckdb.DuckDBPyConnection) -> None:
    """The final tables: the raw rows plus ``segment_no``, the latest segment
    of the period starting at or before the play, falling back to the
    period's first segment (the one at 12:00 is dropped when substitutions
    sit at the opening clock); NULL when ``sim_segments`` is absent."""
    if _has_table(con, "sim_segments"):
        firsts = "SELECT event_id, season, period, min(segment_no) AS first_segment FROM sim_segments GROUP BY ALL"
        seg = (
            f"ASOF LEFT JOIN sim_segments s ON s.event_id = r.event_id AND s.season = r.season AND s.period = r.period AND r.{{col}} >= s.start_seq "
            f"LEFT JOIN ({firsts}) f ON f.event_id = r.event_id AND f.season = r.season AND f.period = r.period"
        )
        segment_no = "coalesce(s.segment_no, f.first_segment) AS segment_no"
    else:
        seg, segment_no = "", "CAST(NULL AS BIGINT) AS segment_no"
    con.execute(f"CREATE OR REPLACE TABLE sim_possessions AS SELECT r.*, {segment_no} FROM sim_possessions_raw r {seg.format(col='start_seq')} ORDER BY r.season, r.event_id, r.poss_no")
    con.execute(f"CREATE OR REPLACE TABLE sim_actions AS SELECT r.*, {segment_no} FROM sim_actions_raw r {seg.format(col='seq')} ORDER BY r.season, r.event_id, r.seq")
    con.execute("DROP TABLE sim_possessions_raw")
    con.execute("DROP TABLE sim_actions_raw")


@dataclass(frozen=True)
class PossessionReport:
    """One season's sibling checks (the module docstring says what each is).

    .. versionadded:: 6.0.0
    """

    season: int
    games: int
    games_excluded: int
    possessions: int
    team_games: int
    score_exact: int
    box_team_games: int
    box_abs_diff: float
    box_diff: float
    games_compared: int
    games_within_periods: int
    player_games: int
    fga_exact: int
    fta_exact: int
    oreb_exact: int
    dreb_exact: int
    ast_exact: int
    tov_exact: int
    and_ones: int = 0
    lone_one_of_ones: int = 0
    implied: int = 0
    side_disagreements: int = 0
    team_rebound_ids: int = 0
    jump_ball_changes: int = 0
    offensive_fouls: int = 0
    dead_rebounds: int = 0
    rebounds_without_miss: int = 0
    teamless: int = 0
    dropped_empty: int = 0

    def _share(self, part: float, whole: float) -> float:
        return part / whole if whole else 0.0

    @property
    def possessions_per_team_game(self) -> float:
        """Possessions a team-game over the games kept."""
        return self._share(self.possessions, self.team_games)

    @property
    def score_share(self) -> float:
        """Share of team-games whose possession points plus technical free throws equal the final."""
        return self._share(self.score_exact, self.team_games)

    @property
    def box_mean_abs_diff(self) -> float:
        """Mean absolute difference between possessions and the box formula, per team-game with a box score."""
        return self._share(self.box_abs_diff, self.box_team_games)

    @property
    def box_mean_diff(self) -> float:
        """Mean signed difference (possessions minus the box formula)."""
        return self._share(self.box_diff, self.box_team_games)

    @property
    def within_periods_share(self) -> float:
        """Share of games where the two teams' counts differ by at most the number of periods."""
        return self._share(self.games_within_periods, self.games_compared)

    def player_share(self, stat: str) -> float:
        """Exact-match share of player-games for ``fga``, ``fta``, ``oreb``, ``dreb``, ``ast`` or ``tov``."""
        exact = {"fga": self.fga_exact, "fta": self.fta_exact, "oreb": self.oreb_exact, "dreb": self.dreb_exact, "ast": self.ast_exact, "tov": self.tov_exact}
        return self._share(exact[stat], self.player_games)


def check_possessions(con: duckdb.DuckDBPyConnection) -> list[PossessionReport]:
    """The sibling checks over every season in ``sim_possessions``: the score
    (possession points plus technical free throws against the final, per
    team-game), possessions against the box formula ``FGA - OREB + TOV +
    0.44 FTA`` over ``player_box_stats_deduped`` (team-games with box
    minutes only, so the empty 2013-2018 Chicago and New Orleans lines are
    out), the two teams' counts within the number of periods, and the
    player stats summed from actions against the box for players with
    minutes.

    .. versionadded:: 6.0.0
    """
    team_rows = con.execute(f"""
        WITH games AS (SELECT DISTINCT season, event_id FROM sim_possessions),
        tg AS (
            SELECT g.season, g.event_id, g.home_team_id AS team_id, g.home_score AS final FROM {WAREHOUSE}.real_games g JOIN games USING (season, event_id)
            UNION ALL
            SELECT g.season, g.event_id, g.away_team_id, g.away_score FROM {WAREHOUSE}.real_games g JOIN games USING (season, event_id)
        ),
        poss AS (SELECT season, event_id, offense_team_id AS team_id, count(*) AS n, sum(points) AS pts FROM sim_possessions WHERE offense_team_id IS NOT NULL GROUP BY ALL),
        tech AS (SELECT season, event_id, team_id, sum(points) AS pts FROM sim_actions WHERE poss_no IS NULL AND kind = 'free_throw' GROUP BY ALL),
        box AS (
            SELECT season, event_id, team_id, sum("fieldGoalsAttempted") AS fga, sum("offensiveRebounds") AS oreb, sum(turnovers) AS tov, sum("freeThrowsAttempted") AS fta, sum(minutes) AS mins
            FROM {WAREHOUSE}.player_box_stats_deduped b JOIN games USING (season, event_id) WHERE minutes IS NOT NULL GROUP BY ALL
        ),
        joined AS (
            SELECT tg.season, coalesce(poss.n, 0) AS n, coalesce(poss.pts, 0) + coalesce(tech.pts, 0) = tg.final AS score_ok,
                   box.mins > 0 AS has_box, coalesce(poss.n, 0) - (box.fga - box.oreb + box.tov + 0.44 * box.fta) AS diff
            FROM tg LEFT JOIN poss USING (season, event_id, team_id) LEFT JOIN tech USING (season, event_id, team_id) LEFT JOIN box USING (season, event_id, team_id)
        )
        SELECT season, count(*), count(*) FILTER (WHERE score_ok), sum(n), count(*) FILTER (WHERE has_box),
               coalesce(sum(abs(diff)) FILTER (WHERE has_box), 0), coalesce(sum(diff) FILTER (WHERE has_box), 0)
        FROM joined GROUP BY season ORDER BY season
    """).fetchall()
    game_rows = con.execute(f"""
        WITH per_game AS (
            SELECT p.season, p.event_id, max(p.period) AS periods,
                   count(*) FILTER (WHERE p.offense_team_id = g.home_team_id) AS home_n, count(*) FILTER (WHERE p.offense_team_id = g.away_team_id) AS away_n
            FROM sim_possessions p JOIN {WAREHOUSE}.real_games g ON g.event_id = p.event_id AND g.season = p.season GROUP BY ALL
        )
        SELECT season, count(*), count(*) FILTER (WHERE abs(home_n - away_n) <= periods) FROM per_game GROUP BY season ORDER BY season
    """).fetchall()
    player_rows = con.execute(f"""
        WITH games AS (SELECT DISTINCT season, event_id FROM sim_possessions),
        acts AS (
            SELECT season, event_id, athlete_id,
                   count(*) FILTER (WHERE kind IN ('shot', 'unknown_score')) AS fga, count(*) FILTER (WHERE kind = 'free_throw') AS fta,
                   count(*) FILTER (WHERE kind = 'offensive_rebound') AS oreb, count(*) FILTER (WHERE kind = 'defensive_rebound') AS dreb,
                   count(*) FILTER (WHERE kind = 'turnover') AS tov
            FROM sim_actions WHERE athlete_id IS NOT NULL GROUP BY ALL
        ),
        ast AS (SELECT season, event_id, partner_id AS athlete_id, count(*) AS ast FROM sim_actions WHERE partner_role = 'assist' GROUP BY ALL),
        box AS (
            SELECT b.season, b.event_id, b.athlete_id, b."fieldGoalsAttempted" AS fga, b."freeThrowsAttempted" AS fta,
                   b."offensiveRebounds" AS oreb, b."defensiveRebounds" AS dreb, b.assists AS ast, b.turnovers AS tov
            FROM {WAREHOUSE}.player_box_stats_deduped b JOIN games USING (season, event_id) WHERE b.minutes IS NOT NULL
        )
        SELECT box.season, count(*),
               count(*) FILTER (WHERE coalesce(acts.fga, 0) = box.fga), count(*) FILTER (WHERE coalesce(acts.fta, 0) = box.fta),
               count(*) FILTER (WHERE coalesce(acts.oreb, 0) = box.oreb), count(*) FILTER (WHERE coalesce(acts.dreb, 0) = box.dreb),
               count(*) FILTER (WHERE coalesce(ast.ast, 0) = box.ast), count(*) FILTER (WHERE coalesce(acts.tov, 0) = box.tov)
        FROM box LEFT JOIN acts USING (season, event_id, athlete_id) LEFT JOIN ast USING (season, event_id, athlete_id)
        GROUP BY box.season ORDER BY box.season
    """).fetchall()
    games = {int(r[0]): r for r in game_rows}
    players = {int(r[0]): r for r in player_rows}
    reports = []
    for season, team_games, score_exact, possessions, box_tg, abs_diff, diff in team_rows:
        g = games.get(int(season), (season, 0, 0))
        p = players.get(int(season), (season, 0, 0, 0, 0, 0, 0, 0))
        reports.append(
            PossessionReport(
                season=int(season),
                games=int(g[1]),
                games_excluded=0,
                possessions=int(possessions),
                team_games=int(team_games),
                score_exact=int(score_exact),
                box_team_games=int(box_tg),
                box_abs_diff=float(abs_diff),
                box_diff=float(diff),
                games_compared=int(g[1]),
                games_within_periods=int(g[2]),
                player_games=int(p[1]),
                fga_exact=int(p[2]),
                fta_exact=int(p[3]),
                oreb_exact=int(p[4]),
                dreb_exact=int(p[5]),
                ast_exact=int(p[6]),
                tov_exact=int(p[7]),
            )
        )
    return reports


_WALK_COUNTS = (
    "and_ones",
    "lone_one_of_ones",
    "implied",
    "side_disagreements",
    "team_rebound_ids",
    "jump_ball_changes",
    "offensive_fouls",
    "dead_rebounds",
    "rebounds_without_miss",
    "teamless",
    "dropped_empty",
)


def _insert(con: duckdb.DuckDBPyConnection, table: str, rows: list[dict[str, Any]], schema: pa.Schema) -> None:
    if not rows:
        return
    con.register("new_rows", pa.Table.from_pylist(rows, schema=schema))
    con.execute(f"INSERT INTO {table} SELECT * FROM new_rows")
    con.unregister("new_rows")


def build_possessions(con: duckdb.DuckDBPyConnection, seasons: Sequence[int] | None = None) -> list[PossessionReport]:
    """Build ``sim_possessions`` and ``sim_actions`` from ``sim_plays`` (and
    ``sim_segments`` for ``segment_no``, when present) for ``seasons`` (every
    season ``sim_plays`` holds when ``None``) and return the sibling checks
    per season, with the walk's own counts.

    .. versionadded:: 6.0.0
    """
    if not seasons:
        seasons = [int(r[0]) for r in con.execute("SELECT DISTINCT season FROM sim_plays ORDER BY season").fetchall()]
    _create_raw_tables(con)
    walk_counts: dict[int, Counter[str]] = {}
    excluded: dict[int, int] = {}
    for season in seasons:
        games, excluded[season] = _season_games(con, season)
        counts: Counter[str] = Counter()
        possession_rows: list[dict[str, Any]] = []
        action_rows: list[dict[str, Any]] = []
        for event_id, plays in _season_plays(con, season):
            game = games.get(event_id)
            if game is None:
                continue
            walk_game(game, plays)
            counts.update(game.counts)
            possession_rows.extend(_possession_rows(game))
            action_rows.extend(_action_rows(game))
        _insert(con, "sim_possessions_raw", possession_rows, _POSSESSIONS_SCHEMA)
        _insert(con, "sim_actions_raw", action_rows, _ACTIONS_SCHEMA)
        walk_counts[season] = counts
        log.info("sim_possessions %d: %d games (%d excluded), %d possessions, %d actions", season, len(games), excluded[season], len(possession_rows), len(action_rows))
    _attach_segments(con)
    reports = []
    for r in check_possessions(con):
        counts = walk_counts.get(r.season, Counter())
        reports.append(replace(r, games_excluded=excluded.get(r.season, 0), **{name: counts.get(name, 0) for name in _WALK_COUNTS}))
    for r in reports:
        log.info(
            "sim_possessions %d: %.1f possessions a team-game, score exact %.2f%%, box formula mean abs diff %.2f, within periods %.2f%%, FGA exact %.2f%%, %d implied",
            r.season,
            r.possessions_per_team_game,
            100 * r.score_share,
            r.box_mean_abs_diff,
            100 * r.within_periods_share,
            100 * r.player_share("fga"),
            r.implied,
        )
    return reports
