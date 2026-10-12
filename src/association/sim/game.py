"""A matchup from the warehouse to a summary: resolve the teams, estimate the
rates, build the rotations, simulate N games and summarize them.

Phase 7 of ``SIMULATION_PLAN.md``. This is the one module that wires the
others together - :func:`association.sim.rates.estimate` for the parameters,
:func:`association.sim.rotation.build_profiles` and
:class:`association.sim.rotation.RotationModel` for who plays when,
:func:`association.sim.engine.simulate` for the games - and it is where the
defaults a simulation needs are chosen and SAID (``AGENTS.md``: a default
must display what it used and how to reach the alternative):

- **The window** (decision D5): the three seasons ending in the season asked
  for, every game whose Eastern date is before ``as_of``; a "fun" matchup
  takes the day after the season's last regular-season game
  (:func:`association.sim.rates.as_of_after`), a replay the real game's own
  date, so nothing it predicts is in its rates.
- **The roster** (:func:`regulars`): the players whose latest team in the
  window is this team, who appeared in at least :data:`REGULAR_SHARE` of
  the team's games in the window's LAST season, the most-used
  :data:`ROSTER_SIZE` of them; the summary prints it with each player's
  minutes target, and a caller may hand its own roster instead. A roster
  with fewer than :data:`MIN_ROSTER` players is refused rather than played
  short.
- **Who is near the prior**: players under ``params.MIN_TRIALS`` charted
  shots are named in the summary and marked in the box score; players the
  rotation profile never saw are listed too.

Seeds: game ``i`` of ``n`` is played from ``seed + i``, so a run is
reproducible and any one game of it can be replayed alone.

.. versionadded:: 6.0.0
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import duckdb

from association.nba.season import eastern_date_sql
from association.sim import rates, rotation
from association.sim import real_minutes as real_minutes_module
from association.sim.database import WAREHOUSE
from association.sim.engine import EngineError, GameSetup, TeamSetup, simulate
from association.sim.events import SimGame
from association.sim.params import LeagueParams, PlayerParams, RotationPolicy, TeamParams, Window
from association.sim.render import Names

WINDOW_SEASONS = 3
"""How many seasons a matchup's window reaches back, the season asked for
included.

.. versionadded:: 6.0.0
"""

REGULAR_SHARE = 0.25
"""A player is a regular when he appeared in at least this share of his
team's games in the window's last season.

.. versionadded:: 6.0.0
"""

ROSTER_SIZE = 13
"""The most regulars a default roster carries, by minutes target.

.. versionadded:: 6.0.0
"""

MIN_ROSTER = 8
"""Fewer rostered players than this is refused: the rotation could not rest
anyone and the game would not look like one.

.. versionadded:: 6.0.0
"""


class SimulationRefused(Exception):
    """A matchup the data cannot support, with the specific missing fact: an
    unknown team, a player with no minutes in the window, a roster too short.

    .. versionadded:: 6.0.0
    """


def resolve_team(con: duckdb.DuckDBPyConnection, text: str) -> str:
    """The team id for an abbreviation (``BOS``), a nickname (``Celtics``), a
    location (``Boston``) or a full name, case-insensitively; refused by
    name when nothing matches or more than one team does.

    .. versionadded:: 6.0.0
    """
    rows = con.execute(f"SELECT team_id, abbreviation, name, nickname, location, display_name FROM {WAREHOUSE}.teams").fetchall()
    wanted = text.strip().lower()
    hits = [r[0] for r in rows if wanted in {str(v).lower() for v in r[1:] if v is not None}]
    if len(hits) == 1:
        return str(hits[0])
    if not hits:
        known = ", ".join(sorted(str(r[1]) for r in rows if r[1]))
        raise SimulationRefused(f"no team matches {text!r}; abbreviations on record: {known}")
    raise SimulationRefused(f"{text!r} names {len(hits)} teams; use the abbreviation")


def latest_season(con: duckdb.DuckDBPyConnection) -> int:
    """The latest season with a regular-season game on record.

    .. versionadded:: 6.0.0
    """
    row = con.execute(f"SELECT max(season) FROM {WAREHOUSE}.real_games WHERE season_type = 2").fetchone()
    if row is None or row[0] is None:
        raise SimulationRefused("no games on record")
    return int(row[0])


def window_for(con: duckdb.DuckDBPyConnection, season: int, as_of: dt.date | None = None) -> Window:
    """The window a matchup in ``season`` is estimated from: the
    :data:`WINDOW_SEASONS` seasons ending in it, up to ``as_of`` (the day
    after the season's last regular-season game when not given).

    .. versionadded:: 6.0.0
    """
    seasons = tuple(range(season - WINDOW_SEASONS + 1, season + 1))
    return Window(seasons, as_of or rates.as_of_after(con, season))


@dataclass(frozen=True)
class Shared:
    """The per-window work several matchups can share: the league-wide
    estimates, every team's rotation profile and the league's rotation
    tables. :func:`shared_for` builds one; :func:`prepare` and
    :func:`prepare_replay` take it so a validation run that replays every
    game of a day estimates once for the day.

    .. versionadded:: 6.0.0
    """

    window: Window
    estimates: rates.Estimates
    profiles: Mapping[str, rotation.RotationProfile]
    league_rotation: rotation.LeagueRotation


def shared_for(con: duckdb.DuckDBPyConnection, window: Window) -> Shared:
    """Estimate the whole league and build every rotation for ``window``.

    .. versionadded:: 6.0.0
    """
    return Shared(window, rates.estimate(con, window), rotation.build_profiles(con, window), rotation.build_league_rotation(con, window))


@dataclass(frozen=True)
class Roster:
    """One side's players, the five who start, and how the roster was chosen.

    .. versionadded:: 6.0.0
    """

    team_id: str
    players: tuple[str, ...]
    starters: tuple[str, ...]
    chosen_by: str


@dataclass(frozen=True)
class Prepared:
    """Everything a run needs, estimated once: the parameters, the rotation
    profiles and league tables, both rosters, the names, and the notes the
    output must carry (near-prior players, unprofiled players, players
    dropped for want of minutes).

    .. versionadded:: 6.0.0
    """

    home: Roster
    away: Roster
    window: Window
    league: LeagueParams
    teams: Mapping[str, TeamParams]
    players: Mapping[str, PlayerParams]
    profiles: Mapping[str, rotation.RotationProfile]
    league_rotation: rotation.LeagueRotation
    names: Names
    notes: tuple[str, ...] = ()
    policies: Mapping[str, RotationPolicy] | None = None  # a rotation policy per team id instead of the model (the real-minutes diagnostic)

    @property
    def near_prior(self) -> tuple[str, ...]:
        """The rostered players simulated at nearly the league prior."""
        return tuple(a for a in self.home.players + self.away.players if self.players[a].near_prior)

    def setup(self) -> GameSetup:
        """A fresh :class:`GameSetup` with a new rotation model per side (the
        model keeps per-game memory)."""
        return GameSetup(home=self._side(self.home), away=self._side(self.away), league=self.league)

    def _side(self, roster: Roster) -> TeamSetup:
        policy: RotationPolicy = self.policies[roster.team_id] if self.policies is not None else rotation.RotationModel(self.profiles[roster.team_id], self.league_rotation)
        return TeamSetup(team=self.teams[roster.team_id], players={a: self.players[a] for a in roster.players}, starters=roster.starters, policy=policy)


def _last_season_games(con: duckdb.DuckDBPyConnection, window: Window, team_id: str) -> tuple[int, dict[str, int]]:
    """The team's games in the window's last season before ``as_of``, and how
    many each player appeared in (box minutes)."""
    season = max(window.seasons)
    rows = con.execute(
        f"""
        WITH games AS (
            SELECT event_id, season FROM {WAREHOUSE}.real_games
            WHERE season = ? AND {eastern_date_sql("date")} < ? AND (home_team_id = ? OR away_team_id = ?)
        )
        SELECT b.athlete_id, count(*) FROM {WAREHOUSE}.player_box_stats_deduped b JOIN games USING (event_id, season)
        WHERE b.team_id = ? AND b.minutes IS NOT NULL GROUP BY b.athlete_id
        """,
        [season, window.as_of, team_id, team_id, team_id],
    ).fetchall()
    total = con.execute(
        f"SELECT count(*) FROM {WAREHOUSE}.real_games WHERE season = ? AND {eastern_date_sql('date')} < ? AND (home_team_id = ? OR away_team_id = ?)",
        [season, window.as_of, team_id, team_id],
    ).fetchone()
    return int(total[0]) if total else 0, {str(a): int(n) for a, n in rows}


def regulars(con: duckdb.DuckDBPyConnection, estimates: rates.Estimates, profiles: Mapping[str, rotation.RotationProfile], window: Window, team_id: str) -> Roster:
    """The default roster: the team's players who appeared in at least
    :data:`REGULAR_SHARE` of its games in the window's last season, the top
    :data:`ROSTER_SIZE` by minutes target; the starters are the profile's
    likely five among them.

    .. versionadded:: 6.0.0
    """
    games, appearances = _last_season_games(con, window, team_id)
    profile = profiles.get(team_id)
    if profile is None or games == 0:
        raise SimulationRefused(f"{team_id} has no games in {window.describe()}")
    own = [a for a, p in estimates.players.items() if p.team_id == team_id]
    regular = [a for a in own if appearances.get(a, 0) >= REGULAR_SHARE * games]
    ranked = sorted(regular, key=lambda a: (-(profile.players[a].minutes_target if a in profile.players else 0.0), a))[:ROSTER_SIZE]
    if len(ranked) < MIN_ROSTER:
        raise SimulationRefused(f"{team_id} has {len(ranked)} regulars in {window.describe()}; {MIN_ROSTER} are needed - hand the roster in")
    return Roster(team_id, tuple(ranked), profile.likely_starters(ranked), f"appeared in >= {REGULAR_SHARE:.0%} of {games} games in {max(window.seasons)}, top {ROSTER_SIZE} by minutes")


def _names(con: duckdb.DuckDBPyConnection, players: Mapping[str, PlayerParams]) -> Names:
    teams = con.execute(f"SELECT team_id, abbreviation, display_name FROM {WAREHOUSE}.teams").fetchall()
    return Names(
        players={a: p.name for a, p in players.items()},
        abbreviations={str(t): str(abbr) for t, abbr, _ in teams},
        teams={str(t): str(name) for t, _, name in teams},
    )


def _check_roster(roster: Roster, estimates: rates.Estimates, window: Window) -> list[str]:
    missing = [a for a in roster.players if a not in estimates.players]
    if missing:
        raise SimulationRefused(f"no minutes in {window.describe()} for {', '.join(missing)}; the rates cannot say how they play")
    if len(roster.players) < MIN_ROSTER:
        raise SimulationRefused(f"{roster.team_id}'s roster has {len(roster.players)} players; {MIN_ROSTER} are needed")
    return [f"{a} simulated at nearly the league prior ({estimates.players[a].zone_mix.trials:.0f} charted shots in the window)" for a in roster.players if estimates.players[a].near_prior]


def prepare(
    con: duckdb.DuckDBPyConnection,
    home: str,
    away: str,
    window: Window,
    rosters: Mapping[str, Sequence[str]] | None = None,
    starters: Mapping[str, Sequence[str]] | None = None,
    shared: Shared | None = None,
) -> Prepared:
    """Estimate and assemble a matchup. ``home`` and ``away`` are team ids;
    ``rosters`` and ``starters`` by team id replace the defaults; ``shared``
    is the window's league-wide work already done (:func:`shared_for`), used
    when its window is this one.

    .. versionadded:: 6.0.0
    """
    if home == away:
        raise SimulationRefused("a team cannot play itself")
    requested = {a for r in (rosters or {}).values() for a in r}
    if shared is not None and shared.window == window:
        estimates, profiles, league_rotation = shared.estimates, shared.profiles, shared.league_rotation
    else:
        estimates = rates.estimate(con, window, team_ids=[home, away], athlete_ids=sorted(requested) or None)
        profiles = rotation.build_profiles(con, window)
        league_rotation = rotation.build_league_rotation(con, window)
    for team in (home, away):
        if team not in estimates.teams or team not in profiles:
            raise SimulationRefused(f"{team} has no games in {window.describe()}")
    sides: dict[str, Roster] = {}
    notes: list[str] = []
    for team in (home, away):
        if rosters and team in rosters:
            players = tuple(rosters[team])
            five = tuple(starters[team]) if starters and team in starters else profiles[team].likely_starters(players)
            sides[team] = Roster(team, players, five, "handed in")
        else:
            sides[team] = regulars(con, estimates, profiles, window, team)
        notes.extend(_check_roster(sides[team], estimates, window))
        unseen = [a for a in sides[team].players if a not in profiles[team].players]
        if unseen:
            notes.append(f"{team}: no rotation on record for {', '.join(unseen)}; played at the unknown rate")
    return Prepared(sides[home], sides[away], window, estimates.league, estimates.teams, estimates.players, profiles, league_rotation, _names(con, estimates.players), tuple(notes))


def run(prepared: Prepared, n: int, seed: int) -> list[SimGame]:
    """Simulate ``n`` games from seeds ``seed`` to ``seed + n - 1``.

    .. versionadded:: 6.0.0
    """
    if n < 1:
        raise SimulationRefused("at least one game")
    games = []
    for i in range(n):
        try:
            games.append(simulate(prepared.setup(), seed + i))
        except EngineError as exc:
            # Name the seed, so `sim replay ... --n 1 --seed <seed> --pbp --explain` replays exactly this game.
            raise EngineError(f"seed {seed + i}: {exc}") from exc
    return games


def traced(prepared: Prepared, seed: int) -> SimGame:
    """One game from ``seed`` with every decision recorded
    (``SimGame.decisions``), for ``--explain``: the same game ``run`` plays
    from that seed, since tracing consumes no randomness.

    .. versionadded:: 6.0.0
    """
    return simulate(prepared.setup(), seed, trace=True)


def quantiles(values: Sequence[float], points: Sequence[float] = (0.1, 0.25, 0.5, 0.75, 0.9)) -> dict[str, float]:
    """Linear-interpolation quantiles of ``values`` at ``points``, keyed
    ``p10``, ``p25``, ...

    .. versionadded:: 6.0.0
    """
    ordered = sorted(values)
    if not ordered:
        return {f"p{round(p * 100)}": 0.0 for p in points}
    out: dict[str, float] = {}
    for p in points:
        pos = p * (len(ordered) - 1)
        lo, hi = int(pos), min(int(pos) + 1, len(ordered) - 1)
        out[f"p{round(p * 100)}"] = ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)
    return out


@dataclass(frozen=True)
class PlayerSummary:
    """One player's line over the simulated games: how often he played, his
    median minutes and the 10th, 50th and 90th percentiles of points,
    rebounds and assists.

    .. versionadded:: 6.0.0
    """

    athlete_id: str
    team_id: str
    name: str
    games: int
    minutes: float
    points: dict[str, float]
    rebounds: dict[str, float]
    assists: dict[str, float]
    near_prior: bool


@dataclass(frozen=True)
class Summary:
    """The run over ``n`` games: the home win probability, the margin (home
    minus away) and total quantiles, the teams' mean points, the overtime
    share and every player's summary, with the window, the seed, the rosters
    and the notes the output must state.

    .. versionadded:: 6.0.0
    """

    home: Roster
    away: Roster
    window: Window
    n: int
    seed: int
    home_win_probability: float
    margin: dict[str, float]
    total: dict[str, float]
    home_points: float
    away_points: float
    overtime_share: float
    players: tuple[PlayerSummary, ...]
    notes: tuple[str, ...] = field(default_factory=tuple)


def _player_summaries(games: Sequence[SimGame], prepared: Prepared) -> tuple[PlayerSummary, ...]:
    by_player: dict[str, list[tuple[float, int, int, int]]] = {}
    for game in games:
        for line in game.lines:
            if line.seconds > 0:
                by_player.setdefault(line.athlete_id, []).append((line.minutes, line.points, line.oreb + line.dreb, line.ast))
    summaries = []
    for roster in (prepared.away, prepared.home):
        for athlete in roster.players:
            lines = by_player.get(athlete, [])
            summaries.append(
                PlayerSummary(
                    athlete,
                    roster.team_id,
                    prepared.names.player(athlete),
                    len(lines),
                    quantiles([m for m, _, _, _ in lines], (0.5,))["p50"],
                    quantiles([float(p) for _, p, _, _ in lines], (0.1, 0.5, 0.9)),
                    quantiles([float(r) for _, _, r, _ in lines], (0.1, 0.5, 0.9)),
                    quantiles([float(a) for _, _, _, a in lines], (0.1, 0.5, 0.9)),
                    prepared.players[athlete].near_prior,
                )
            )
    return tuple(sorted(summaries, key=lambda s: (s.team_id != prepared.away.team_id, -s.minutes)))


def summarize(games: Sequence[SimGame], prepared: Prepared, seed: int) -> Summary:
    """Summarize a run.

    .. versionadded:: 6.0.0
    """
    margins = [float(g.home_score - g.away_score) for g in games]
    totals = [float(g.home_score + g.away_score) for g in games]
    return Summary(
        home=prepared.home,
        away=prepared.away,
        window=prepared.window,
        n=len(games),
        seed=seed,
        home_win_probability=sum(1 for g in games if g.home_score > g.away_score) / len(games),
        margin=quantiles(margins),
        total=quantiles(totals),
        home_points=sum(g.home_score for g in games) / len(games),
        away_points=sum(g.away_score for g in games) / len(games),
        overtime_share=sum(1 for g in games if g.overtimes) / len(games),
        players=_player_summaries(games, prepared),
        notes=prepared.notes,
    )


def summary_text(summary: Summary, names: Names) -> str:
    """The summary as text: the result, the quantiles, each player's line,
    the window, the seed and the notes.

    .. versionadded:: 6.0.0
    """
    home, away = names.abbreviation(summary.home.team_id), names.abbreviation(summary.away.team_id)
    m, t = summary.margin, summary.total
    lines = [
        f"{names.team(summary.away.team_id)} at {names.team(summary.home.team_id)}: {summary.n} simulated games, "
        f"seeds {summary.seed}-{summary.seed + summary.n - 1}, rates from {summary.window.describe()}",
        f"{home} wins {summary.home_win_probability:.1%}; mean score {home} {summary.home_points:.1f}, {away} {summary.away_points:.1f}; overtime in {summary.overtime_share:.1%}",
        f"margin ({home} minus {away}): p10 {m['p10']:+.0f}  p25 {m['p25']:+.0f}  median {m['p50']:+.0f}  p75 {m['p75']:+.0f}  p90 {m['p90']:+.0f}",
        f"total points: p10 {t['p10']:.0f}  p25 {t['p25']:.0f}  median {t['p50']:.0f}  p75 {t['p75']:.0f}  p90 {t['p90']:.0f}",
        "",
        f"{'player':<28}{'team':>5}{'GP':>6}{'MIN':>6}{'PTS (p10-p90)':>18}{'REB':>14}{'AST':>14}",
    ]
    for p in summary.players:
        mark = " *" if p.near_prior else ""
        lines.append(
            f"{p.name + mark:<28}{names.abbreviation(p.team_id):>5}{p.games:>6}{p.minutes:>6.1f}"
            f"{p.points['p50']:>7.0f} ({p.points['p10']:.0f}-{p.points['p90']:.0f}){'':>2}"
            f"{p.rebounds['p50']:>5.0f} ({p.rebounds['p10']:.0f}-{p.rebounds['p90']:.0f}){'':>1}"
            f"{p.assists['p50']:>5.0f} ({p.assists['p10']:.0f}-{p.assists['p90']:.0f})"
        )
    lines += ["", f"rosters: {home} {summary.home.chosen_by}; {away} {summary.away.chosen_by} (pass --home-roster / --away-roster with athlete ids to change one)"]
    lines += [f"note: {n}" for n in summary.notes]
    if any(p.near_prior for p in summary.players):
        lines.append("* simulated at nearly the league prior (under 100 charted shots in the window)")
    return "\n".join(lines)


def summary_json(summary: Summary, names: Names) -> dict[str, Any]:
    """The summary as plain data.

    .. versionadded:: 6.0.0
    """
    return {
        "schema_version": "sim.1",
        "window": {"seasons": list(summary.window.seasons), "as_of": summary.window.as_of.isoformat()},
        "n": summary.n,
        "seed": summary.seed,
        "home": {
            "team_id": summary.home.team_id,
            "abbreviation": names.abbreviation(summary.home.team_id),
            "roster": list(summary.home.players),
            "starters": list(summary.home.starters),
            "chosen_by": summary.home.chosen_by,
        },
        "away": {
            "team_id": summary.away.team_id,
            "abbreviation": names.abbreviation(summary.away.team_id),
            "roster": list(summary.away.players),
            "starters": list(summary.away.starters),
            "chosen_by": summary.away.chosen_by,
        },
        "home_win_probability": summary.home_win_probability,
        "margin": summary.margin,
        "total": summary.total,
        "home_points": summary.home_points,
        "away_points": summary.away_points,
        "overtime_share": summary.overtime_share,
        "players": [
            {
                "athlete_id": p.athlete_id,
                "team_id": p.team_id,
                "name": p.name,
                "games": p.games,
                "minutes": p.minutes,
                "points": p.points,
                "rebounds": p.rebounds,
                "assists": p.assists,
                "near_prior": p.near_prior,
            }
            for p in summary.players
        ],
        "notes": list(summary.notes),
    }


@dataclass(frozen=True)
class RealGame:
    """A real game to replay: its teams, the players who appeared for each
    with their starters, its Eastern date and its real score.

    .. versionadded:: 6.0.0
    """

    event_id: str
    season: int
    date: dt.date
    home: str
    away: str
    rosters: Mapping[str, tuple[str, ...]]
    starters: Mapping[str, tuple[str, ...]]
    home_score: int
    away_score: int


def real_game(con: duckdb.DuckDBPyConnection, event_id: str) -> RealGame:
    """The real game with ``event_id``, with the players who appeared (box
    minutes) and the box starters per team; refused when the event is not a
    real game on record.

    .. versionadded:: 6.0.0
    """
    row = con.execute(f"SELECT season, {eastern_date_sql('date')}, home_team_id, away_team_id, home_score, away_score FROM {WAREHOUSE}.real_games WHERE event_id = ?", [event_id]).fetchone()
    if row is None:
        raise SimulationRefused(f"no real game {event_id} on record")
    season, date, home, away, hs, as_ = row
    lines = con.execute(
        f"SELECT team_id, athlete_id, starter FROM {WAREHOUSE}.player_box_stats_deduped WHERE event_id = ? AND season = ? AND minutes IS NOT NULL ORDER BY starter DESC, minutes DESC, athlete_id",
        [event_id, season],
    ).fetchall()
    rosters: dict[str, list[str]] = {str(home): [], str(away): []}
    starters: dict[str, list[str]] = {str(home): [], str(away): []}
    for team, athlete, starter in lines:
        if str(team) in rosters:
            rosters[str(team)].append(str(athlete))
            if starter:
                starters[str(team)].append(str(athlete))
    return RealGame(event_id, int(season), date, str(home), str(away), {t: tuple(r) for t, r in rosters.items()}, {t: tuple(s) for t, s in starters.items()}, int(hs), int(as_))


def prepare_replay(con: duckdb.DuckDBPyConnection, game: RealGame, shared: Shared | None = None, real_minutes: bool = False) -> Prepared:
    """A replay's setup: the window ends on the game's own Eastern date, the
    rosters are the players who appeared, the starters the box five (where
    the box names five). A player with no minutes before that date is
    dropped with a note rather than refusing the whole game; a side left
    under :data:`MIN_ROSTER` is refused. With ``real_minutes`` the rotation
    is the real game's lineups followed by the clock
    (:mod:`association.sim.real_minutes`), a diagnostic the notes name.

    .. versionadded:: 6.0.0
    """
    window = window_for(con, game.season, game.date)
    if shared is not None and shared.window == window:
        known = shared.estimates.players
    else:
        known = rates.estimate(con, window, team_ids=[game.home, game.away], athlete_ids=sorted(a for r in game.rosters.values() for a in r)).players
    rosters = {t: tuple(a for a in r if a in known) for t, r in game.rosters.items()}
    dropped = [a for t, r in game.rosters.items() for a in r if a not in known]
    starters = {t: s for t, s in game.starters.items() if len(s) == 5 and all(a in rosters[t] for a in s)}
    prepared = prepare(con, game.home, game.away, window, rosters=rosters, starters=starters, shared=shared)
    notes = list(prepared.notes) + ([f"dropped for want of minutes before {game.date.isoformat()}: {', '.join(dropped)}"] if dropped else [])
    policies: dict[str, RotationPolicy] | None = None
    if real_minutes:
        lineups = real_minutes_module.read_lineups(con, game.event_id, game.season, game.home, game.away)
        policies = dict(lineups)
        notes.append(real_minutes_module.describe(list(lineups.values())))
    return Prepared(
        prepared.home, prepared.away, prepared.window, prepared.league, prepared.teams, prepared.players, prepared.profiles, prepared.league_rotation, prepared.names, tuple(notes), policies
    )
