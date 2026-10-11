"""``sim_segments``: who was on the floor, for every stretch of every game.

Phase 2 of ``SIMULATION_PLAN.md``. A **segment** is a run of plays during
which neither team's five changes; it is the unit every later phase
attributes to (minutes, plus-minus, on-court rates, the rotation model).
Built per game from ``sim_plays`` in ``seq`` order, period by period:

1. **Who opens the period.** A player is on the floor at the start of a
   period if his first appearance in it is anything but entering as a
   substitute: a touch of the ball (a shot, a rebound, a foul committed or
   drawn, a jump ball, a turnover or a steal) or LEAVING by substitution
   both say he was already there. Plays a player can appear in from the
   bench - a technical, an ejection, a delay of game (``vocab``'s
   ``bench_possible``) - say nothing. For period 1 the box score's
   ``starter`` flag is used where it names exactly five, and the first-touch
   rule's answer is recorded against it as the measure of the rule itself.
   For a later period the rule usually finds five; where it finds fewer,
   the players who closed the previous period and neither appear nor enter
   in this one are the only candidates, and the lineup is filled from them
   when they are exactly as many as are missing (``source = 'carried'``).
   Fewer, more, or more than five found: the team-period is **unresolved**
   from its start.
2. **The walk.** Each two-id substitution (the first id enters, the second
   leaves: 0b, check 0.3) closes the open segment at the substitution's
   clock and swaps the player. A substitution that contradicts the lineup
   (the leaver is not on the floor, the entrant already is) is applied as
   far as it can be and marks the team unresolved for the rest of the
   period, as does a one-id substitution (2018-2020, ~2,600: the text names
   one player, so the other is unknown). A touch by a player the lineup
   says is on the bench is a **phantom**: counted per team-period, never
   repaired - the number is what tells a later reader how far to trust the
   table. Points are attributed to the segment the scoring play falls in
   by ``seq``, so a free throw after a substitution at the same clock is
   credited to the lineup that was on the floor for it; a segment with no
   time and no plays (two substitutions at one clock) is dropped.

Unresolved segments are KEPT, with ``resolved = false``, so the build can
count them and a reader can exclude them; a resolved segment of an
unresolved game is still resolved. Chicago's and New Orleans' 2013-2018
games, whose box scores are empty, get segments like any other game (their
``starter`` flag survived: 0b, check 0.7) and simply have no box minutes to
be checked against.

Sibling checks (:func:`check_segments`, printed by ``association sim
build``): per player-game over fully resolved games, segment seconds / 60
against box ``minutes`` (within one minute) and segment point differential
against box ``plusMinus`` (exact); per team-game, the first-touch rule's
period-1 five against the box five; the share of game seconds in resolved
segments; phantoms. The plan's targets were 97%, 95%, 99% and 99%.

Measured on this checkout's warehouse (``nba.duckdb`` copied 2026-09-24) by
``association sim build`` on 2026-10-11, the whole build in 44 seconds
(``scripts/sim_p2/results/build_2026-10-11.out``):

- **Resolved seconds:** 99.84-99.93% in 2021-2026 and 99.65-99.91% in
  2009-2017; 94.9-95.7% in 2018-2020, where the one-id substitutions are
  (808, 1,001 and 782); 97.7% in 2007 and 98.0% in 2013.
- **Period-1 five against the box:** 100% in 2010-2012, 2014, 2016-2026;
  99.92-99.96% otherwise (1-2 team-games a season).
- **Minutes within one:** 99.5-99.99% in every season (24,000-28,000
  player-games a season; 16,000-18,000 in 2018-2020, since only fully
  resolved games are compared).
- **Plus-minus exact:** 96.6-98.6% in 2021-2026, 94.3-98.3% in 2009-2012,
  2014-2015 and 2017-2020; 83.8% in 2013 and 63.9% in 2016, the seasons
  whose plays type made shots as misses (``DATA.md``); and 4.6-4.9% in
  2007-2008 - ESPN's fault, not the walk's: in those two seasons the box
  ``plusMinus`` column is not even consistent with the final (0 of 2,632
  team-games in 2008 sum to five times the margin, against 2,630 of 2,630
  in 2025), while the segments' own sums do (2,604 of 2,606 in 2025).
- **Phantoms:** 0-41 a season from 2009 but 293 in 2013; 31 in 2007.
- **Touches by an id outside the deduped box roster:** 996-1,114 a season
  in 2013-2015 and under 200 otherwise - the second id ESPN files some
  players under (``DATA.md``, "ESPN files one player under two athlete
  ids"), which the deduped box merges and the plays still carry. Those
  touches are counted and not attributed; the minutes check is unmoved.

.. versionadded:: 6.0.0
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field, replace
from itertools import groupby
from typing import Any

import duckdb
import pyarrow as pa

from association.sim.database import WAREHOUSE
from association.sim.plays import OVERTIME_PERIOD_SECONDS, REGULATION_PERIOD_SECONDS

log: logging.Logger = logging.getLogger("association.sim.segments")

PRESENCE_KINDS: frozenset[str] = frozenset(
    {
        "shot",
        "free_throw",
        "offensive_rebound",
        "defensive_rebound",
        "rebound",
        "turnover",
        "foul_personal",
        "foul_shooting",
        "foul_offensive",
        "foul_flagrant",
        "jump_ball",
        "violation",
        "unknown_score",
    }
)
"""The kinds whose participants were on the floor. A technical, an ejection,
a timeout, a review and the period markers say nothing about presence.

.. versionadded:: 6.0.0
"""


def period_end_elapsed(period: int) -> float:
    """Seconds from tip-off at the end of ``period``: 720 a quarter, 300 an
    overtime.

    .. versionadded:: 6.0.0
    """
    if period <= 4:
        return float(period * REGULATION_PERIOD_SECONDS)
    return float(4 * REGULATION_PERIOD_SECONDS + (period - 4) * OVERTIME_PERIOD_SECONDS)


@dataclass(frozen=True, slots=True)
class Play:
    """The fields of a ``sim_plays`` row the walk reads.

    .. versionadded:: 6.0.0
    """

    seq: int
    period: int
    elapsed: float
    kind: str | None
    bench_possible: bool
    team_id: str | None
    participants: tuple[str, ...]
    text: str
    points: int | None

    @property
    def is_presence(self) -> bool:
        """Whether the play's participants were on the floor."""
        return self.kind in PRESENCE_KINDS and not self.bench_possible and bool(self.participants)

    def substitution(self) -> tuple[str | None, str | None] | None:
        """``(entering, leaving)`` for a substitution, either ``None`` when the
        row names one player (the text's shape says which: "X enters the game
        for " names the entrant, "enters the game for Y" the leaver); ``None``
        for any other play."""
        if self.kind != "substitution":
            return None
        if len(self.participants) >= 2:
            return self.participants[0], self.participants[1]
        if len(self.participants) == 1:
            only = self.participants[0]
            if self.text.strip().lower().startswith("enters"):
                return None, only
            return only, None
        return None, None


@dataclass
class TeamPeriod:
    """One team's state through one period: its lineup, how it was found,
    and what went wrong if anything.

    .. versionadded:: 6.0.0
    """

    team_id: str
    period: int
    lineup: set[str]
    source: str  # 'box', 'touch' or 'carried'
    touch_count: int
    agrees_with_box: bool | None = None
    resolved: bool = True
    unresolved_seq: int | None = None
    reason: str | None = None
    phantom_touches: int = 0
    one_id_subs: int = 0
    inconsistent_subs: int = 0

    def give_up(self, seq: int, reason: str) -> None:
        """Mark the rest of the period unresolved, keeping the first reason."""
        if self.resolved:
            self.resolved = False
            self.unresolved_seq = seq
            self.reason = reason


@dataclass
class Segment:
    """One stretch with both fives fixed, and the points scored in it.

    .. versionadded:: 6.0.0
    """

    period: int
    start_seq: int
    end_seq: int
    start_elapsed: float
    end_elapsed: float
    home_lineup: list[str]
    away_lineup: list[str]
    home_points: int = 0
    away_points: int = 0
    carried: bool = False
    resolved: bool = True


@dataclass
class Game:
    """What the walk needs to know about one game before reading its plays.

    .. versionadded:: 6.0.0
    """

    event_id: str
    season: int
    home_team_id: str
    away_team_id: str
    roster: dict[str, str]  # athlete_id -> team_id, from the deduped box score
    box_starters: dict[str, set[str]]  # team_id -> the starter-flagged five, only where exactly five
    segments: list[Segment] = field(default_factory=list)
    team_periods: list[TeamPeriod] = field(default_factory=list)
    unrostered_touches: int = 0
    points_unknown: int = 0


def _first_roles(plays: Sequence[Play]) -> dict[str, str]:
    """Each player's first appearance in a period: ``in`` (entered by
    substitution), ``out`` (left by substitution) or ``touch``."""
    roles: dict[str, str] = {}
    for play in plays:
        sub = play.substitution()
        if sub is not None:
            entering, leaving = sub
            if entering and entering not in roles:
                roles[entering] = "in"
            if leaving and leaving not in roles:
                roles[leaving] = "out"
        elif play.is_presence:
            for athlete in play.participants:
                roles.setdefault(athlete, "touch")
    return roles


def _fill_from_closing(state: TeamPeriod, on: set[str], entering: set[str], previous: set[str] | None, first_seq: int) -> None:
    """Fewer than five found by the first-touch rule: fill from the players who
    closed the previous period and neither appear nor enter in this one, when
    they are exactly as many as are missing; otherwise unresolved."""
    candidates = (previous - on - entering) if previous is not None else None
    if candidates is not None and len(on) + len(candidates) == 5:
        state.lineup, state.source = on | candidates, "carried"
        return
    state.lineup = on | (candidates or set())
    short = candidates is None or len(on) + len(candidates) < 5
    state.give_up(first_seq, "starters_short" if short else "starters_ambiguous")


def _open_team(game: Game, team: str, period: int, roles: dict[str, str], previous: set[str] | None, first_seq: int) -> TeamPeriod:
    on = {a for a, role in roles.items() if role != "in" and game.roster.get(a) == team}
    entering = {a for a, role in roles.items() if role == "in" and game.roster.get(a) == team}
    state = TeamPeriod(team, period, set(on), "touch", len(on))
    box = game.box_starters.get(team)
    if period == 1 and box is not None:
        state.agrees_with_box = on == box if len(on) == 5 else None
        state.lineup, state.source = set(box), "box"
    elif len(on) < 5:
        _fill_from_closing(state, on, entering, previous, first_seq)
    elif len(on) > 5:
        state.give_up(first_seq, "starters_over")
    return state


def _open_period(game: Game, period: int, plays: Sequence[Play], closing: dict[str, set[str] | None]) -> dict[str, TeamPeriod]:
    """The two teams' lineups at the start of ``period``, by the rules in the
    module docstring."""
    roles = _first_roles(plays)
    first_seq = plays[0].seq if plays else 0
    return {team: _open_team(game, team, period, roles, closing.get(team), first_seq) for team in (game.home_team_id, game.away_team_id)}


def _team_of_substitution(game: Game, play: Play, entering: str | None, leaving: str | None) -> str | None:
    for athlete in (entering, leaving):
        if athlete is not None and athlete in game.roster:
            return game.roster[athlete]
    return play.team_id if play.team_id in (game.home_team_id, game.away_team_id) else None


def _apply_substitution(state: TeamPeriod, play: Play, entering: str | None, leaving: str | None) -> None:
    """Swap the players, or mark the period unresolved where the row
    contradicts the lineup or names one player."""
    if entering is None or leaving is None:
        state.one_id_subs += 1
        state.give_up(play.seq, "one_id_substitution")
    elif leaving not in state.lineup or entering in state.lineup:
        state.inconsistent_subs += 1
        state.give_up(play.seq, "inconsistent_substitution")
    if leaving is not None:
        state.lineup.discard(leaving)
    if entering is not None:
        state.lineup.add(entering)


def _count_phantoms(game: Game, states: dict[str, TeamPeriod], play: Play, departed: dict[str, set[str]]) -> None:
    """A touch by a player the lineup says is on the bench. A player who left
    at the clock reading still open counts as present (he shot the free
    throw the substitution was logged beside)."""
    for athlete in play.participants:
        team = game.roster.get(athlete)
        if team is None:
            game.unrostered_touches += 1
            continue
        state = states.get(team)
        if state is not None and state.resolved and athlete not in state.lineup and athlete not in departed.get(team, ()):
            state.phantom_touches += 1


def _new_segment(game: Game, states: dict[str, TeamPeriod], period: int, start_seq: int, start_elapsed: float) -> Segment:
    home, away = states[game.home_team_id], states[game.away_team_id]
    return Segment(
        period,
        start_seq,
        start_seq,
        start_elapsed,
        start_elapsed,
        sorted(home.lineup),
        sorted(away.lineup),
        carried=home.source == "carried" or away.source == "carried",
        resolved=home.resolved and away.resolved,
    )


def _close(game: Game, current: Segment, end_seq: int, end_elapsed: float) -> None:
    """Append the open segment unless it holds neither time nor points."""
    current.end_seq, current.end_elapsed = end_seq, end_elapsed
    if current.end_elapsed > current.start_elapsed or current.home_points or current.away_points:
        game.segments.append(current)


def _score(game: Game, current: Segment, play: Play) -> None:
    if play.points is None and play.kind == "unknown_score":
        game.points_unknown += 1
    if play.points and play.team_id == game.home_team_id:
        current.home_points += play.points
    elif play.points and play.team_id == game.away_team_id:
        current.away_points += play.points


def _walk_period(game: Game, period: int, plays: Sequence[Play], closing: dict[str, set[str] | None]) -> dict[str, set[str] | None]:
    """Walk one period's plays, appending its segments and team states to the
    game; return each team's closing lineup (``None`` where unresolved).

    A substitution changes the lineup at once - for presence, and for the
    consistency check against the next substitution - but the segment it
    ends is closed only when the clock moves past the substitution's
    reading, so a play at the same reading (the second free throw of the
    trip the substitution was logged during) is credited to the five who
    were on the floor before it. That is ESPN's own plus-minus convention,
    measured on 2025: applying substitutions as served matched the box
    plus-minus in 42% of player-games, deferring the close in 98%.
    """
    states = _open_period(game, period, plays, closing)
    start_elapsed = period_end_elapsed(period - 1) if period > 1 else 0.0
    current = _new_segment(game, states, period, plays[0].seq if plays else 0, start_elapsed)
    pending_at: float | None = None  # the clock reading of the substitutions not yet closed on
    departed: dict[str, set[str]] = {}
    for play in plays:
        if pending_at is not None and play.elapsed > pending_at:
            _close(game, current, play.seq - 1, pending_at)
            current = _new_segment(game, states, period, play.seq, pending_at)
            pending_at, departed = None, {}
        sub = play.substitution()
        if sub is not None:
            entering, leaving = sub
            team = _team_of_substitution(game, play, entering, leaving)
            if team is not None:
                _apply_substitution(states[team], play, entering, leaving)
                if leaving is not None:
                    departed.setdefault(team, set()).add(leaving)
            pending_at = play.elapsed
            continue
        if play.is_presence:
            _count_phantoms(game, states, play, departed)
        _score(game, current, play)
    if plays:
        _close(game, current, plays[-1].seq, period_end_elapsed(period))
    game.team_periods.extend(states.values())
    return {team: (set(state.lineup) if state.resolved and len(state.lineup) == 5 else None) for team, state in states.items()}


def walk_game(game: Game, plays: Sequence[Play]) -> Game:
    """Build the segments and team-period states of one game from its plays
    in ``seq`` order.

    .. versionadded:: 6.0.0
    """
    closing: dict[str, set[str] | None] = {}
    for period, period_plays in groupby(plays, key=lambda p: p.period):
        closing = _walk_period(game, period, list(period_plays), closing)
    return game


def _season_plays(con: duckdb.DuckDBPyConnection, season: int) -> Iterator[tuple[str, list[Play]]]:
    rows = con.execute(
        "SELECT event_id, seq, period, elapsed, kind, bench_possible, team_id, participants, text, points FROM sim_plays WHERE season = ? ORDER BY event_id, seq",
        [season],
    ).fetchall()
    for event_id, group in groupby(rows, key=lambda r: r[0]):
        yield event_id, [Play(r[1], r[2], float(r[3]), r[4], bool(r[5]), r[6], tuple(r[7] or ()), r[8] or "", r[9]) for r in group]


def _season_games(con: duckdb.DuckDBPyConnection, season: int) -> dict[str, Game]:
    games = {
        event_id: Game(event_id, season, home, away, {}, {})
        for event_id, home, away in con.execute(f"SELECT event_id, home_team_id, away_team_id FROM {WAREHOUSE}.real_games WHERE season = ?", [season]).fetchall()
    }
    starters: dict[tuple[str, str], set[str]] = {}
    for event_id, athlete_id, team_id, starter in con.execute(f"SELECT event_id, athlete_id, team_id, starter FROM {WAREHOUSE}.player_box_stats_deduped WHERE season = ?", [season]).fetchall():
        game = games.get(event_id)
        if game is None:
            continue
        game.roster[athlete_id] = team_id
        if starter:
            starters.setdefault((event_id, team_id), set()).add(athlete_id)
    for (event_id, team_id), five in starters.items():
        if len(five) == 5:
            games[event_id].box_starters[team_id] = five
    return games


_SEGMENTS_SCHEMA = pa.schema(
    [
        ("event_id", pa.string()),
        ("season", pa.int64()),
        ("period", pa.int64()),
        ("segment_no", pa.int64()),
        ("start_seq", pa.int64()),
        ("end_seq", pa.int64()),
        ("start_elapsed", pa.float64()),
        ("end_elapsed", pa.float64()),
        ("home_lineup", pa.list_(pa.string())),
        ("away_lineup", pa.list_(pa.string())),
        ("home_points", pa.int64()),
        ("away_points", pa.int64()),
        ("carried", pa.bool_()),
        ("resolved", pa.bool_()),
    ]
)

_TEAM_PERIODS_SCHEMA = pa.schema(
    [
        ("event_id", pa.string()),
        ("season", pa.int64()),
        ("period", pa.int64()),
        ("team_id", pa.string()),
        ("source", pa.string()),
        ("touch_count", pa.int64()),
        ("agrees_with_box", pa.bool_()),
        ("resolved", pa.bool_()),
        ("unresolved_seq", pa.int64()),
        ("reason", pa.string()),
        ("phantom_touches", pa.int64()),
        ("one_id_subs", pa.int64()),
        ("inconsistent_subs", pa.int64()),
    ]
)


def _rows_of(games: Iterable[Game]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    segments: list[dict[str, Any]] = []
    team_periods: list[dict[str, Any]] = []
    for game in games:
        for n, s in enumerate(game.segments, start=1):
            segments.append(
                {
                    "event_id": game.event_id,
                    "season": game.season,
                    "period": s.period,
                    "segment_no": n,
                    "start_seq": s.start_seq,
                    "end_seq": s.end_seq,
                    "start_elapsed": s.start_elapsed,
                    "end_elapsed": s.end_elapsed,
                    "home_lineup": s.home_lineup,
                    "away_lineup": s.away_lineup,
                    "home_points": s.home_points,
                    "away_points": s.away_points,
                    "carried": s.carried,
                    "resolved": s.resolved,
                }
            )
        team_periods.extend(
            {
                "event_id": game.event_id,
                "season": game.season,
                "period": t.period,
                "team_id": t.team_id,
                "source": t.source,
                "touch_count": t.touch_count,
                "agrees_with_box": t.agrees_with_box,
                "resolved": t.resolved,
                "unresolved_seq": t.unresolved_seq,
                "reason": t.reason,
                "phantom_touches": t.phantom_touches,
                "one_id_subs": t.one_id_subs,
                "inconsistent_subs": t.inconsistent_subs,
            }
            for t in game.team_periods
        )
    return segments, team_periods


def _create_tables(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("""
        CREATE OR REPLACE TABLE sim_segments (
            event_id VARCHAR, season BIGINT, period BIGINT, segment_no BIGINT, start_seq BIGINT, end_seq BIGINT,
            start_elapsed DOUBLE, end_elapsed DOUBLE, home_lineup VARCHAR[], away_lineup VARCHAR[],
            home_points BIGINT, away_points BIGINT, carried BOOLEAN, resolved BOOLEAN)""")
    con.execute("""
        CREATE OR REPLACE TABLE sim_team_periods (
            event_id VARCHAR, season BIGINT, period BIGINT, team_id VARCHAR, source VARCHAR, touch_count BIGINT,
            agrees_with_box BOOLEAN, resolved BOOLEAN, unresolved_seq BIGINT, reason VARCHAR,
            phantom_touches BIGINT, one_id_subs BIGINT, inconsistent_subs BIGINT)""")


@dataclass(frozen=True)
class SegmentReport:
    """One season's sibling checks (the module docstring says what each is).

    .. versionadded:: 6.0.0
    """

    season: int
    games: int
    team_periods: int
    team_periods_resolved: int
    seconds: float
    resolved_seconds: float
    starters_compared: int
    starters_agree: int
    player_games: int
    minutes_within_1: int
    plus_minus_exact: int
    phantom_touches: int
    one_id_subs: int
    inconsistent_subs: int
    unrostered_touches: int = 0
    points_unknown: int = 0

    def _share(self, part: float, whole: float) -> float:
        return part / whole if whole else 0.0

    @property
    def team_periods_share(self) -> float:
        """Share of team-periods resolved to their end."""
        return self._share(self.team_periods_resolved, self.team_periods)

    @property
    def resolved_share(self) -> float:
        """Share of game seconds in resolved segments."""
        return self._share(self.resolved_seconds, self.seconds)

    @property
    def starters_share(self) -> float:
        """Share of team-games where the first-touch five equals the box five."""
        return self._share(self.starters_agree, self.starters_compared)

    @property
    def minutes_share(self) -> float:
        """Share of player-games within a minute of the box score."""
        return self._share(self.minutes_within_1, self.player_games)

    @property
    def plus_minus_share(self) -> float:
        """Share of player-games whose plus-minus equals the box score's."""
        return self._share(self.plus_minus_exact, self.player_games)


def check_segments(con: duckdb.DuckDBPyConnection) -> list[SegmentReport]:
    """The sibling checks over every season in ``sim_segments``.

    Minutes and plus-minus are compared only in games every segment of which
    is resolved, and only for players the box score gives minutes (so the
    empty 2013-2018 Chicago and New Orleans games, and did-not-plays, are
    out); a player the box gives minutes and the segments never hold counts
    as a miss.

    .. versionadded:: 6.0.0
    """
    rows = con.execute(f"""
        WITH seg AS (
            SELECT event_id, season, end_elapsed - start_elapsed AS secs, home_lineup, away_lineup, home_points, away_points, resolved FROM sim_segments
        ),
        games_ok AS (SELECT event_id, season FROM seg GROUP BY ALL HAVING bool_and(resolved)),
        on_floor AS (
            SELECT event_id, season, unnest(home_lineup) AS athlete_id, secs, home_points - away_points AS pm FROM seg JOIN games_ok USING (event_id, season)
            UNION ALL
            SELECT event_id, season, unnest(away_lineup), secs, away_points - home_points FROM seg JOIN games_ok USING (event_id, season)
        ),
        per_player AS (SELECT event_id, season, athlete_id, sum(secs) / 60 AS minutes, sum(pm) AS pm FROM on_floor GROUP BY ALL),
        box AS (
            SELECT b.season, b.event_id, b.athlete_id, b.minutes, b."plusMinus" AS plus_minus
            FROM {WAREHOUSE}.player_box_stats_deduped b JOIN games_ok USING (event_id, season)
            WHERE b.minutes IS NOT NULL
        ),
        players AS (
            SELECT box.season, count(*) AS n,
                   count(*) FILTER (WHERE abs(coalesce(pp.minutes, 0) - box.minutes) <= 1) AS within_1,
                   count(*) FILTER (WHERE coalesce(pp.pm, 0) = box.plus_minus) AS pm_exact
            FROM box LEFT JOIN per_player pp USING (event_id, season, athlete_id) GROUP BY box.season
        ),
        secs AS (SELECT season, count(DISTINCT event_id) AS games, sum(secs) AS seconds, sum(secs) FILTER (WHERE resolved) AS resolved_seconds FROM seg GROUP BY season),
        tp AS (
            SELECT season, count(*) AS team_periods, count(*) FILTER (WHERE resolved) AS resolved_tp,
                   count(*) FILTER (WHERE agrees_with_box IS NOT NULL) AS compared, count(*) FILTER (WHERE agrees_with_box) AS agree,
                   sum(phantom_touches) AS phantoms, sum(one_id_subs) AS one_id, sum(inconsistent_subs) AS inconsistent
            FROM sim_team_periods GROUP BY season
        )
        SELECT secs.season, secs.games, tp.team_periods, tp.resolved_tp, secs.seconds, coalesce(secs.resolved_seconds, 0),
               tp.compared, tp.agree, coalesce(players.n, 0), coalesce(players.within_1, 0), coalesce(players.pm_exact, 0),
               tp.phantoms, tp.one_id, tp.inconsistent
        FROM secs JOIN tp USING (season) LEFT JOIN players USING (season) ORDER BY secs.season
    """).fetchall()
    return [_report_of(r) for r in rows]


def _with_walk_counts(report: SegmentReport, counts: tuple[int, int]) -> SegmentReport:
    unrostered, unknown = counts
    return replace(report, unrostered_touches=unrostered, points_unknown=unknown)


def _report_of(r: tuple[Any, ...]) -> SegmentReport:
    return SegmentReport(
        season=int(r[0]),
        games=int(r[1]),
        team_periods=int(r[2]),
        team_periods_resolved=int(r[3]),
        seconds=float(r[4]),
        resolved_seconds=float(r[5]),
        starters_compared=int(r[6]),
        starters_agree=int(r[7]),
        player_games=int(r[8]),
        minutes_within_1=int(r[9]),
        plus_minus_exact=int(r[10]),
        phantom_touches=int(r[11]),
        one_id_subs=int(r[12]),
        inconsistent_subs=int(r[13]),
    )


def build_segments(con: duckdb.DuckDBPyConnection, seasons: Sequence[int] | None = None) -> list[SegmentReport]:
    """Build ``sim_segments`` and ``sim_team_periods`` from ``sim_plays`` for
    ``seasons`` (every season ``sim_plays`` holds when ``None``) and return
    the sibling checks per season.

    .. versionadded:: 6.0.0
    """
    if not seasons:
        seasons = [int(r[0]) for r in con.execute("SELECT DISTINCT season FROM sim_plays ORDER BY season").fetchall()]
    _create_tables(con)
    walk_counts: dict[int, tuple[int, int]] = {}
    for season in seasons:
        games = _season_games(con, season)
        walked: list[Game] = []
        for event_id, plays in _season_plays(con, season):
            game = games.get(event_id)
            if game is not None:
                walked.append(walk_game(game, plays))
        segments, team_periods = _rows_of(walked)
        if segments:
            con.register("segment_rows", pa.Table.from_pylist(segments, schema=_SEGMENTS_SCHEMA))
            con.execute("INSERT INTO sim_segments SELECT * FROM segment_rows")
            con.unregister("segment_rows")
        if team_periods:
            con.register("team_period_rows", pa.Table.from_pylist(team_periods, schema=_TEAM_PERIODS_SCHEMA))
            con.execute("INSERT INTO sim_team_periods SELECT * FROM team_period_rows")
            con.unregister("team_period_rows")
        walk_counts[season] = (sum(g.unrostered_touches for g in walked), sum(g.points_unknown for g in walked))
        log.info("sim_segments %d: %d games, %d segments", season, len(walked), len(segments))
    reports = [_with_walk_counts(r, walk_counts.get(r.season, (0, 0))) for r in check_segments(con)]
    for r in reports:
        log.info(
            "sim_segments %d: resolved %.2f%% of seconds and %.2f%% of team-periods, starters %.2f%%, minutes within 1 %.2f%%, plus-minus exact %.2f%%, %d phantoms",
            r.season,
            100 * r.resolved_share,
            100 * r.team_periods_share,
            100 * r.starters_share,
            100 * r.minutes_share,
            100 * r.plus_minus_share,
            r.phantom_touches,
        )
    return reports
