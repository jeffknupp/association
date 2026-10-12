"""The first estimator: every player, team and league rate the engine plays
from, per minute on the floor, from the box score and the shot chart, in a
point-in-time window, shrunk toward the league with a strength fitted from
the data.

Build-order step 4 of ``SIMULATION_PLAN.md`` (P4-box under decision D15: no
possession table yet, so usage is per minute and the defense is the team's,
D6 option 1). :func:`estimate` aggregates in SQL - six statements over
``nba.real_games``, ``nba.player_box_stats_deduped``, ``nba.shot_chart`` and
``sim_plays`` - and shrinks in Python, so a three-season window estimates in
seconds (below).

**The window** (:class:`association.sim.params.Window`, D5) is every
``real_games`` game of the listed seasons whose US Eastern date
(``nba.season.eastern_date_sql``, the one rule the project dates games by)
is strictly before ``as_of``. A replay passes the replayed game's date and
cannot see that evening's games; a season simulation passes the day after
its last game. Each season's counts are weighted ``Window.weight``
(``season_decay`` to the power of the seasons back, 0.5 by default -
``params.SEASON_DECAY`` has the measurement) before they are summed for a
player, a team or the league, so a rate is the weighted mean and its
``trials`` the weighted count, which the shrinkage treats as the sample;
the prior strengths are fitted within each season (odd games against even)
and so need no weight.

**What each rate is read from.** A player's minutes, shots, free throws,
turnovers, assists, rebounds, steals, blocks and fouls are the box score's
(``player_box_stats_deduped``, rows with ``minutes IS NOT NULL`` - which
also leaves out the empty 2013-2018 Chicago and New Orleans lines, and
whose integer minutes are the denominator of every per-minute rate). Free-
throw TRIPS are counted from ``sim_plays`` as the first attempt of each
trip (``Free Throw - ... 1 of N``, technicals excluded): ESPN's shooting-foul
rows name only the fouler in every season from 2019 (0 of 25,543-27,573 a
season carry the shooter as a participant), so the trip is read from the
free throws themselves rather than approximated as FTA/2, which runs 8%
short (28,113 against 30,769 in 2025). A shot's zone is :mod:`zones`' over
``shot_chart`` joined to ``sim_plays`` on ``(play_id, season)`` for the
clock and the value; a heave and an unknown-position shot count toward
usage (the box's attempts) and never toward a zone. An and-one is a made
shot followed in the same period by a ``Free Throw - 1 of 1`` by the same
player at the same clock, which is 97% of all one-of-one rows 2019-2026
(``scripts/sim_p4/zone_table.py``); an assisted make is one whose play
names a second participant. The live-ball share of turnovers is
``sim_plays.live_ball`` over the player's own turnover plays. A team's
possessions per game are the box formula ``FGA - OREB + TOV + 0.44 FTA``
over its players' box lines plus its team turnovers from ``sim_plays``
(shot-clock and similar, nobody's: 1,900-2,100 a season), and its pace is
possessions per 48 team-minutes; its defense is what opponents did against
it in the window's games - their make rate and zone mix (shots whose
``team_id`` is the other side), their turnovers and free-throw trips per
their possession, and the share of their misses this team rebounded
(``DREB / (DREB + opponents' OREB)``, player rebounds on both sides). The
league's figures are the sums; ``home_court`` is the mean home margin.

**Shrinkage.** Every rate is ``shrink.shrink`` toward the league mean with a
strength fitted by ``shrink.fit_strength`` on the window's player-seasons
(team-seasons for a team rate), each split into its odd and even games by
date, and every mix by ``fit_mix_strength`` the same way. A per-minute
rate's trials are the minutes, so its ``k`` is in pseudo-minutes; a
probability's trials are its attempts. The fitted strengths, every one in
``LeagueParams.strengths`` and printed by
``scripts/sim_p4/estimate_report.py``, measured on seasons 2024-2026 through
2026-04-13 (the day after the last 2026 regular-season game by the Eastern
clock; 3,859 games, 1,723 player-seasons with minutes, 90 team-seasons;
``results/estimate_2026-10-11.out``, which estimates 802 players and 30
teams in 1.5 seconds):

==================================== ============ ==============================
rate                                     fitted k pseudo-trials of
==================================== ============ ==============================
shots_per_minute                             21.6 minutes
free_throw_trips_per_minute                  63.6 minutes
turnovers_per_minute                        128.7 minutes
assists_per_minute                           40.6 minutes
offensive_rebounds_per_minute                45.5 minutes
defensive_rebounds_per_minute                55.8 minutes
steals_per_minute                           228.7 minutes
blocks_per_minute                            68.8 minutes
fouls_per_minute                            119.5 minutes
zone_mix                                      8.4 shots (Dirichlet)
make_rate.restricted                         90.0 shots
make_rate.paint                             119.6 shots
make_rate.midrange                          165.0 shots
make_rate.corner_three                      160.0 shots
make_rate.above_break_three                 417.3 shots
and_one_rate                                 91.5 makes
assisted_rate                                10.1 makes
free_throw_rate                              21.8 free throws
live_ball_share                              37.8 turnovers
pace                                          3.6 games of 48 team-minutes
allowed_zone_mix                          1,112.3 shots
allowed_make_rate.restricted                457.3 shots
allowed_make_rate.paint                     765.5 shots
allowed_make_rate.midrange                3,374.4 shots
allowed_make_rate.corner_three        1,000,000.0 shots (the search's ceiling)
allowed_make_rate.above_break_three       6,088.7 shots
forced_turnover_rate                        538.6 possessions
foul_rate                                 1,338.3 possessions
defensive_rebound_rate                    1,070.2 misses
==================================== ============ ==============================

What they say: a player's usage and shot profile show almost at once (a
quarter-game of minutes, eight shots), his assisted share and free-throw
percentage in a handful of makes and attempts, his finishing at the rim in
90 shots and his above-the-break three only in 417 - a season's worth -
the regression to the mean the plan asked for. A team's pace is known in
four games. A team's DEFENSE is where the shrinkage bites: opponents'
corner-three make rate against a team carries no signal from one half of
its games to the other (the fit runs to the search's ceiling, so every
team is at the league's 38.7%), their above-the-break rate needs 6,000
attempts (two and a half seasons' worth) to count as much as the league,
and only the restricted area (457) and the paint (766) are learned inside a
season. Forced turnovers (539 possessions, five games) and fouls drawn
(1,338, thirteen games) are team traits; defensive rebounding needs 1,070
misses, about a season.

Sanity (the same output): league pace 101.2 possessions per 48 against
NBA.com's 98.8 for 2024-25 - the box formula over one side's player lines,
without the team rebounds, runs a possession or two high, said rather than
corrected; free-throw rate 78.2%; threes 40.9% of charted shots; home
margin +1.97 (the window holds the 2024 and 2025 playoffs); turnovers
0.138 a possession; offensive rebounds 25.1% of misses (NBA.com's 26-28%
counts team rebounds, which player lines do not carry). Shai
Gilgeous-Alexander estimates at 21.6 shots and 4.8 free-throw trips per 36
minutes with a 0.2% prior weight on his usage; 211 of the 802 players (16
more with no minutes at all, in ``missing``) are under 100 charted shots
and carry ``near_prior``.

**Refusals.** A requested team with no game in the window, and a player on
a requested team's roster (or named in ``athlete_ids``) with no minutes in
it, is in ``Estimates.missing`` for the caller to refuse by name; a player
under ``params.MIN_TRIALS`` charted shots carries ``near_prior`` on his
record and the output says so.

.. versionadded:: 6.0.0
"""

from __future__ import annotations

import datetime as dt
import logging
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

import duckdb

from association.nba.season import eastern_date_sql
from association.sim import shrink, zones
from association.sim.database import WAREHOUSE
from association.sim.params import DURATION_BINS, DURATION_KEYS, ZONES, LeagueParams, Mix, PlayerParams, Rate, TeamParams, Window

TRIP_START_SQL = "(sp.type LIKE 'Free Throw - %1 of %' AND sp.type NOT LIKE '%1 of 1' AND sc.event_id IS NOT NULL)"
"""The first attempt of a free-throw trip DRAWN BY A SHOOTING FOUL ON A MISS
in ``sim_plays`` (aliased ``sp``, joined to :data:`SHOOTING_CLOCKS_SQL` as
``sc``): a ``1 of 2`` or ``1 of 3`` at a clock where a shooting foul was
called. Two kinds of trip are left out, each because the engine produces
it another way and counting it here simulated it twice. A ``1 of 1``:
95.7-98.0% are and-ones (section 0b, 0.10), drawn from ``and_one_rate`` on a
made shot - counting them as trips gave every and-one a second trip, and
the first replay validation (2026-10-11) simulated 0.377 free-throw
attempts per field-goal attempt against a real 0.211. A BONUS trip, a
``1 of 2`` after a non-shooting foul in the penalty: the engine's
non-shooting-foul path awards those, and counting them as the shooter's own
trips kept 0.283 attempts per field-goal attempt against 0.240 after the
first fix. Measured on 2024-2026 regular seasons, per team-game: 7.65
shooting-foul trips, 1.77 bonus trips, 2.47 one-of-ones, 0.07 flagrant;
only 14 of 70,156 two- and three-shot trips had no foul of either kind at
their clock. A technical has no "of" and is no trip.

.. versionadded:: 6.0.0
"""

SHOOTING_CLOCKS_SQL = "SELECT DISTINCT event_id, season, period, clock_remaining FROM sim_plays WHERE kind = 'foul_shooting'"
"""The clocks at which a shooting foul was called, which is how a free-throw
trip is told from a bonus trip (ESPN's shooting-foul rows name only the
fouler, 0b 0.10, so the trip is matched on the clock, not the player).

.. versionadded:: 6.0.0
"""

logger = logging.getLogger(__name__)

FREE_THROW_POSSESSION_WEIGHT = 0.44
"""The box formula's weight on free-throw attempts: ``FGA - OREB + TOV + 0.44
FTA`` possessions per team-game.

.. versionadded:: 6.0.0
"""

TEAM_MINUTES_PER_GAME = 240.0
"""Five players' minutes in a regulation game, the unit pace is per.

.. versionadded:: 6.0.0
"""

Spec = tuple[str, str, str]
"""A rate's name, the counter key of its successes and of its trials."""

PLAYER_RATES: tuple[Spec, ...] = (
    ("shots_per_minute", "fga", "minutes"),
    ("free_throw_trips_per_minute", "trips", "minutes"),
    ("turnovers_per_minute", "tov", "minutes"),
    ("and_one_rate", "and_ones", "makes"),
    ("assisted_rate", "assisted", "makes"),
    ("free_throw_rate", "ftm", "fta"),
    ("live_ball_share", "live_tov", "tov_plays"),
    ("assists_per_minute", "ast", "minutes"),
    ("offensive_rebounds_per_minute", "oreb", "minutes"),
    ("defensive_rebounds_per_minute", "dreb", "minutes"),
    ("steals_per_minute", "stl", "minutes"),
    ("blocks_per_minute", "blk", "minutes"),
    ("fouls_per_minute", "pf", "minutes"),
    ("usage_share", "uses", "lineup_uses"),
    *((f"make_rate.{zone}", f"makes.{zone}", f"attempts.{zone}") for zone in ZONES),
)
"""Every binomial or per-minute player rate: its field name on
:class:`~association.sim.params.PlayerParams` (a zone's as
``make_rate.<zone>``), and the counter keys it is read from.

.. versionadded:: 6.0.0
"""

TEAM_RATES: tuple[Spec, ...] = (
    ("pace", "poss", "games48"),
    ("first_use_seconds", "use_seconds", "first_uses"),
    ("forced_turnover_rate", "opp_tov", "opp_poss"),
    ("foul_rate", "opp_trips", "opp_poss"),
    ("defensive_rebound_rate", "dreb", "opp_misses"),
    *((f"allowed_make_rate.{zone}", f"allowed_makes.{zone}", f"allowed_attempts.{zone}") for zone in ZONES),
)
"""Every team rate, as :data:`PLAYER_RATES`.

.. versionadded:: 6.0.0
"""

PER_MINUTE_RATES: frozenset[str] = frozenset(name for name, _, trials in PLAYER_RATES if trials == "minutes")
"""The player rates whose trials are minutes, which
``LeagueParams.per_minute`` records the league's values of.

.. versionadded:: 6.0.0
"""

Key = tuple[str, int, int]
"""A counter's key: the player's or team's id, the season, the game parity
(1 for his odd games by date, 0 for the even ones)."""

Counts = dict[Key, Counter[str]]
"""Counts by key."""


@dataclass(frozen=True)
class Estimates:
    """What :func:`estimate` returns: the league's parameters (every prior and
    every fitted strength), the teams' and the players' records by id, and
    the ids the caller asked about that the window holds nothing for.

    .. versionadded:: 6.0.0
    """

    league: LeagueParams
    teams: Mapping[str, TeamParams]
    players: Mapping[str, PlayerParams]
    missing: frozenset[str]


def _seasons_sql(window: Window) -> str:
    return "(" + ", ".join(str(int(s)) for s in sorted(set(window.seasons))) + ")"


def _window_ctes(window: Window) -> str:
    """The CTEs every statement shares: the window's games, each player's
    box line per game with his game parity, each team's game parity."""
    seasons = _seasons_sql(window)
    return f"""
    games AS (
        SELECT g.event_id, g.season, g.date, g.home_team_id, g.away_team_id, g.home_score, g.away_score
        FROM {WAREHOUSE}.real_games g
        WHERE g.season IN {seasons} AND {eastern_date_sql("g.date")} < DATE '{window.as_of.isoformat()}'
    ),
    player_games AS (
        SELECT b.athlete_id, b.team_id, b.season, b.event_id, g.date, b.minutes,
               b.fieldGoalsAttempted AS fga, b.freeThrowsAttempted AS fta, b.freeThrowsMade AS ftm, b.turnovers AS tov, b.assists AS ast,
               b.offensiveRebounds AS oreb, b.defensiveRebounds AS dreb, b.steals AS stl, b.blocks AS blk, b.fouls AS pf,
               row_number() OVER (PARTITION BY b.athlete_id, b.season ORDER BY g.date, b.event_id) % 2 AS parity
        FROM {WAREHOUSE}.player_box_stats_deduped b
        JOIN games g ON g.event_id = b.event_id AND g.season = b.season
        WHERE b.minutes IS NOT NULL
    ),
    team_parity AS (
        SELECT team_id, season, event_id,
               row_number() OVER (PARTITION BY team_id, season ORDER BY date, event_id) % 2 AS parity
        FROM (SELECT unnest([g.home_team_id, g.away_team_id]) AS team_id, g.season, g.event_id, g.date FROM games g)
    ),
    shots AS (
        SELECT sc.athlete_id, sc.team_id, sc.event_id, sc.season, sc.made,
               {zones.zone_sql("sp.shot_value", "sp.clock_remaining", "sc.shot_type")} AS zone,
               len(sp.participants) >= 2 AS assisted,
               sp.period, sp.clock_remaining
        FROM {WAREHOUSE}.shot_chart sc
        JOIN sim_plays sp ON sp.play_id = sc.play_id AND sp.season = sc.season
        JOIN games g ON g.event_id = sc.event_id AND g.season = sc.season
        WHERE sp.kind = 'shot' AND sc.season IN {seasons} AND sp.season IN {seasons}
    )"""


def _add(counts: Counts, key: Key, **values: float) -> None:
    counts.setdefault(key, Counter()).update({name: float(v) for name, v in values.items() if v is not None})


def _read_box(con: duckdb.DuckDBPyConnection, ctes: str, counts: Counts) -> None:
    """Minutes and the box columns per player-season-parity."""
    rows = con.execute(f"""
        WITH {ctes}
        SELECT athlete_id, season, parity, sum(minutes), sum(fga), sum(fta), sum(ftm), sum(tov), sum(ast), sum(oreb), sum(dreb), sum(stl), sum(blk), sum(pf)
        FROM player_games GROUP BY 1, 2, 3
    """).fetchall()
    for athlete, season, parity, minutes, fga, fta, ftm, tov, ast, oreb, dreb, stl, blk, pf in rows:
        _add(counts, (athlete, int(season), int(parity)), minutes=minutes, fga=fga, fta=fta, ftm=ftm, tov=tov, ast=ast, oreb=oreb, dreb=dreb, stl=stl, blk=blk, pf=pf)


def _read_plays(con: duckdb.DuckDBPyConnection, ctes: str, seasons: str, counts: Counts) -> None:
    """Free-throw trips and turnovers (with their live-ball share) from the
    plays, per player-season-parity, over the window's games."""
    rows = con.execute(f"""
        WITH {ctes}
        SELECT pg.athlete_id, pg.season, pg.parity,
               count(*) FILTER (WHERE {TRIP_START_SQL}) AS trips,
               count(*) FILTER (WHERE sp.kind = 'turnover') AS tov_plays,
               count(*) FILTER (WHERE sp.kind = 'turnover' AND sp.live_ball) AS live_tov
        FROM sim_plays sp
        JOIN player_games pg ON pg.athlete_id = sp.athlete_id AND pg.event_id = sp.event_id AND pg.season = sp.season
        LEFT JOIN ({SHOOTING_CLOCKS_SQL} AND season IN {seasons}) sc
          ON sc.event_id = sp.event_id AND sc.season = sp.season AND sc.period = sp.period AND sc.clock_remaining = sp.clock_remaining
        WHERE sp.season IN {seasons} AND sp.kind IN ('turnover', 'free_throw')
        GROUP BY 1, 2, 3
    """).fetchall()
    for athlete, season, parity, trips, tov_plays, live_tov in rows:
        _add(counts, (athlete, int(season), int(parity)), trips=trips, tov_plays=tov_plays, live_tov=live_tov)


def _read_shots(con: duckdb.DuckDBPyConnection, ctes: str, seasons: str, counts: Counts) -> None:
    """Attempts and makes by zone, and-ones and assisted makes, per
    player-season-parity. A heave or an unknown-position shot is counted
    under its own name and never enters a zone's rate or the mix."""
    rows = con.execute(f"""
        WITH {ctes},
        one_of_ones AS (
            SELECT DISTINCT season, event_id, period, athlete_id, clock_remaining
            FROM sim_plays sp WHERE sp.type = 'Free Throw - 1 of 1' AND sp.season IN {seasons}
        )
        SELECT pg.athlete_id, pg.season, pg.parity, s.zone,
               count(*), count(*) FILTER (WHERE s.made),
               count(*) FILTER (WHERE s.made AND f.athlete_id IS NOT NULL),
               count(*) FILTER (WHERE s.made AND s.assisted)
        FROM shots s
        JOIN player_games pg ON pg.athlete_id = s.athlete_id AND pg.event_id = s.event_id AND pg.season = s.season
        LEFT JOIN one_of_ones f ON f.season = s.season AND f.event_id = s.event_id AND f.period = s.period AND f.athlete_id = s.athlete_id AND f.clock_remaining = s.clock_remaining
        WHERE s.zone IS NOT NULL
        GROUP BY 1, 2, 3, 4
    """).fetchall()
    for athlete, season, parity, zone, attempts, makes, and_ones, assisted in rows:
        key = (athlete, int(season), int(parity))
        _add(counts, key, **{f"attempts.{zone}": attempts, f"makes.{zone}": makes})
        if zone != zones.HEAVE:
            _add(counts, key, makes=makes, and_ones=and_ones, assisted=assisted)


def _read_identities(con: duckdb.DuckDBPyConnection, ctes: str, team_ids: Sequence[str] | None) -> tuple[dict[str, tuple[str, str]], set[str]]:
    """Each player's latest team in the window and his name; and, for the
    requested teams (every team when none is named), the rostered players
    with NO minutes in the window - ``did_not_play`` on every line."""
    rows = con.execute(f"""
        WITH {ctes}
        SELECT pg.athlete_id, arg_max(pg.team_id, pg.date), coalesce(any_value(p.display_name), pg.athlete_id)
        FROM player_games pg LEFT JOIN {WAREHOUSE}.players p ON p.athlete_id = pg.athlete_id
        GROUP BY 1
    """).fetchall()
    identities = {athlete: (team, name) for athlete, team, name in rows}
    teams_sql = "" if team_ids is None else "AND b.team_id IN (" + ", ".join("'" + t.replace("'", "''") + "'" for t in team_ids) + ")"
    sat_out = con.execute(f"""
        WITH {ctes}
        SELECT DISTINCT b.athlete_id FROM {WAREHOUSE}.player_box_stats_deduped b
        JOIN games g ON g.event_id = b.event_id AND g.season = b.season
        WHERE b.athlete_id NOT IN (SELECT athlete_id FROM player_games) {teams_sql}
    """).fetchall()
    return identities, {r[0] for r in sat_out}


def _read_team_games(con: duckdb.DuckDBPyConnection, ctes: str, seasons: str, counts: Counts) -> None:
    """Possessions, minutes and the defensive counts per team-season-parity,
    each team-game beside its opponent's. A game where one side has no box
    line with minutes drops out for both sides."""
    rows = con.execute(f"""
        WITH {ctes},
        team_box AS (
            SELECT team_id, season, event_id, sum(minutes) AS minutes, sum(fga) AS fga, sum(oreb) AS oreb, sum(dreb) AS dreb, sum(fta) AS fta, sum(tov) AS tov
            FROM player_games GROUP BY 1, 2, 3
        ),
        team_plays AS (
            SELECT sp.team_id, sp.season, sp.event_id,
                   count(*) FILTER (WHERE sp.kind = 'turnover' AND sp.team_play) AS team_tov,
                   count(*) FILTER (WHERE {TRIP_START_SQL}) AS trips
            FROM sim_plays sp JOIN games g ON g.event_id = sp.event_id AND g.season = sp.season
            LEFT JOIN ({SHOOTING_CLOCKS_SQL} AND season IN {seasons}) sc
              ON sc.event_id = sp.event_id AND sc.season = sp.season AND sc.period = sp.period AND sc.clock_remaining = sp.clock_remaining
            WHERE sp.season IN {seasons} AND sp.kind IN ('turnover', 'free_throw') AND sp.team_id IS NOT NULL
            GROUP BY 1, 2, 3
        ),
        sides AS (
            SELECT tb.*, tp.parity, coalesce(pl.trips, 0) AS trips, tb.tov + coalesce(pl.team_tov, 0) AS tov_all,
                   tb.fga - tb.oreb + tb.tov + coalesce(pl.team_tov, 0) + {FREE_THROW_POSSESSION_WEIGHT} * tb.fta AS poss
            FROM team_box tb
            JOIN team_parity tp ON tp.team_id = tb.team_id AND tp.season = tb.season AND tp.event_id = tb.event_id
            LEFT JOIN team_plays pl ON pl.team_id = tb.team_id AND pl.season = tb.season AND pl.event_id = tb.event_id
        )
        SELECT t.team_id, t.season, t.parity, count(*), sum(t.minutes), sum(t.poss), sum(t.tov_all), sum(t.trips), sum(t.oreb), sum(t.dreb),
               sum(o.poss), sum(o.tov_all), sum(o.trips), sum(o.oreb)
        FROM sides t JOIN sides o ON o.event_id = t.event_id AND o.season = t.season AND o.team_id <> t.team_id
        GROUP BY 1, 2, 3
    """).fetchall()
    for team, season, parity, games, minutes, poss, tov, trips, oreb, dreb, opp_poss, opp_tov, opp_trips, opp_oreb in rows:
        _add(
            counts,
            (team, int(season), int(parity)),
            games=games,
            games48=minutes / TEAM_MINUTES_PER_GAME,
            poss=poss,
            tov=tov,
            trips=trips,
            oreb=oreb,
            dreb=dreb,
            opp_poss=opp_poss,
            opp_tov=opp_tov,
            opp_trips=opp_trips,
            opp_oreb=opp_oreb,
            opp_misses=dreb + opp_oreb,
        )


def _read_allowed_shots(con: duckdb.DuckDBPyConnection, ctes: str, counts: Counts) -> None:
    """Opponents' attempts and makes by zone against each team, per
    team-season-parity: the shots whose side is the other team in the game."""
    rows = con.execute(f"""
        WITH {ctes}
        SELECT tp.team_id, tp.season, tp.parity, s.zone, count(*), count(*) FILTER (WHERE s.made)
        FROM shots s
        JOIN team_parity tp ON tp.event_id = s.event_id AND tp.season = s.season AND tp.team_id <> s.team_id
        WHERE s.zone IS NOT NULL AND s.team_id IS NOT NULL
        GROUP BY 1, 2, 3, 4
    """).fetchall()
    for team, season, parity, zone, attempts, makes in rows:
        _add(counts, (team, int(season), int(parity)), **{f"allowed_attempts.{zone}": attempts, f"allowed_makes.{zone}": makes})


USE_KINDS_SQL = "('shot', 'turnover', 'foul_shooting', 'foul_personal', 'foul_offensive', 'foul_flagrant')"
"""The ``sim_actions`` kinds that are a USE of the ball - what the engine's
duration draw runs up to.

.. versionadded:: 6.0.0
"""

DURATION_START_TYPES: Mapping[str, str] = {
    "period_start": "period_start",
    "after_made": "made_shot",
    "after_ft": "made_free_throw",
    "after_dreb": "defensive_rebound",
    "after_live_turnover": "live_turnover",
    "after_dead_turnover": "dead_ball",
}
"""``sim_possessions.start_type`` to the engine's start type
(:data:`~association.sim.params.DURATION_KEYS`); ``after_other`` (a few
hundred a season, mostly zero seconds) is left out.

.. versionadded:: 6.0.0
"""


def _has_table(con: duckdb.DuckDBPyConnection, name: str) -> bool:
    return con.execute("SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [name]).fetchone()[0] > 0  # type: ignore[index]


def _duration_ctes(ctes: str, seasons: str) -> str:
    """The window's possessions and, for every action in them, the next
    action; ``first_uses`` is each possession's first use of the ball."""
    return f"""{ctes},
    poss AS (
        SELECT p.event_id, p.season, p.period, p.poss_no, p.offense_team_id, p.start_type, p.start_elapsed
        FROM sim_possessions p JOIN games g ON g.event_id = p.event_id AND g.season = p.season
        WHERE p.season IN {seasons} AND p.poss_no IS NOT NULL
    ),
    acts AS (
        SELECT a.event_id, a.season, a.period, a.poss_no, a.seq, a.elapsed, a.kind, a.team_id, a.dead_ball, a.made,
               lead(a.kind) OVER w AS next_kind, lead(a.elapsed) OVER w AS next_elapsed, lead(a.dead_ball) OVER w AS next_dead
        FROM sim_actions a JOIN poss USING (event_id, season, period, poss_no)
        WINDOW w AS (PARTITION BY a.event_id, a.season, a.period, a.poss_no ORDER BY a.seq)
    ),
    first_uses AS (
        SELECT * FROM (
            SELECT a.season, a.elapsed, p.offense_team_id, p.event_id, p.start_type, p.start_elapsed,
                   row_number() OVER (PARTITION BY a.event_id, a.season, a.period, a.poss_no ORDER BY a.seq) AS use_no
            FROM acts a JOIN poss p USING (event_id, season, period, poss_no)
            WHERE a.kind IN {USE_KINDS_SQL} AND NOT a.dead_ball
        ) WHERE use_no = 1
    )"""


def _read_durations(con: duckdb.DuckDBPyConnection, ctes: str, seasons: str, window: Window) -> dict[str, tuple[float, ...]]:
    """The league's duration histograms by :data:`~association.sim.params.DURATION_KEYS`:
    whole seconds from a possession's start to its first use by start type,
    from an offensive rebound or a non-shooting foul to the next use, and
    from a missed shot or free throw to its rebound, each season's counts
    weighted by :meth:`Window.weight`."""
    bins = f"least(greatest(round({{}}), 0), {DURATION_BINS - 1})::INTEGER"
    rows = con.execute(
        f"""WITH {_duration_ctes(ctes, seasons)}
        SELECT season, start_type, {bins.format("elapsed - start_elapsed")} AS s, count(*) FROM first_uses GROUP BY 1, 2, 3
        UNION ALL
        SELECT a.season, CASE WHEN a.kind = 'offensive_rebound' THEN 'putback' ELSE 'after_foul' END, {bins.format("a.next_elapsed - a.elapsed")}, count(*)
        FROM acts a JOIN poss p USING (event_id, season, period, poss_no)
        WHERE ((a.kind = 'offensive_rebound' AND NOT a.dead_ball) OR (a.kind = 'foul_personal' AND a.team_id <> p.offense_team_id))
          AND a.next_kind IN {USE_KINDS_SQL} AND NOT a.next_dead
        GROUP BY 1, 2, 3
        UNION ALL
        SELECT a.season, CASE WHEN a.kind = 'shot' THEN 'rebound' ELSE 'free_throw_rebound' END, {bins.format("a.next_elapsed - a.elapsed")}, count(*)
        FROM acts a
        WHERE a.kind IN ('shot', 'free_throw') AND NOT a.made AND a.next_kind IN ('offensive_rebound', 'defensive_rebound') AND NOT a.next_dead
        GROUP BY 1, 2, 3"""
    ).fetchall()
    counts: dict[str, list[float]] = {}
    for season, start_type, second, n in rows:
        key = DURATION_START_TYPES.get(start_type, start_type)
        if key in DURATION_KEYS:
            counts.setdefault(key, [0.0] * DURATION_BINS)[int(second)] += n * window.weight(int(season))
    return {key: tuple(c / sum(bucket) for c in bucket) for key, bucket in counts.items() if sum(bucket) > 0}


def _read_first_uses(con: duckdb.DuckDBPyConnection, ctes: str, seasons: str, counts: Counts) -> None:
    """Each team's seconds from a possession's start to its first use, summed
    with the count, per team-season-parity (period starts left out)."""
    rows = con.execute(
        f"""WITH {_duration_ctes(ctes, seasons)}
        SELECT f.offense_team_id, f.season, tp.parity, sum(f.elapsed - f.start_elapsed), count(*)
        FROM first_uses f JOIN team_parity tp ON tp.team_id = f.offense_team_id AND tp.season = f.season AND tp.event_id = f.event_id
        WHERE f.start_type <> 'period_start' GROUP BY 1, 2, 3"""
    ).fetchall()
    for team, season, parity, seconds, uses in rows:
        _add(counts, (str(team), int(season), int(parity)), use_seconds=float(seconds), first_uses=uses)


USES_SQL = "(a.kind IN ('shot', 'turnover') OR (a.kind = 'free_throw' AND a.ft_kind = 'regular' AND a.ft_number = 1 AND a.ft_of >= 2))"
"""A USE of the ball in ``sim_actions`` for the usage share: a shot, a
turnover, or the first free throw of a two- or three-shot trip (an and-one's
free throw belongs to its shot).

.. versionadded:: 6.0.0
"""


def _read_uses(con: duckdb.DuckDBPyConnection, ctes: str, seasons: str, counts: Counts) -> None:
    """Each player's uses and his lineups' uses while he was on the floor of
    a resolved segment, per player-season-parity (``scripts/sim_p9/usage.py``
    is the measurement)."""
    rows = con.execute(
        f"""WITH {ctes},
        uses AS (
            SELECT a.event_id, a.season, a.period, a.segment_no, a.team_id, a.athlete_id
            FROM sim_actions a JOIN games g ON g.event_id = a.event_id AND g.season = a.season
            WHERE a.season IN {seasons} AND a.poss_no IS NOT NULL AND {USES_SQL}
        ),
        on_floor AS (
            SELECT u.event_id, u.season, u.athlete_id, unnest(CASE WHEN u.team_id = g.home_team_id THEN s.home_lineup ELSE s.away_lineup END) AS on_id
            FROM uses u JOIN games g ON g.event_id = u.event_id AND g.season = u.season
            JOIN sim_segments s ON s.event_id = u.event_id AND s.season = u.season AND s.period = u.period AND s.segment_no = u.segment_no AND s.resolved
        )
        SELECT pg.athlete_id, pg.season, pg.parity, count(*) FILTER (WHERE o.athlete_id = o.on_id), count(*)
        FROM on_floor o JOIN player_games pg ON pg.athlete_id = o.on_id AND pg.season = o.season AND pg.event_id = o.event_id
        GROUP BY 1, 2, 3"""
    ).fetchall()
    for athlete, season, parity, uses, lineup_uses in rows:
        _add(counts, (str(athlete), int(season), int(parity)), uses=uses, lineup_uses=lineup_uses)


def _read_home_margin(con: duckdb.DuckDBPyConnection, ctes: str) -> tuple[float, int]:
    margin, games = con.execute(f"WITH {ctes} SELECT coalesce(avg(home_score - away_score), 0.0), count(*) FROM games").fetchone() or (0.0, 0)
    return float(margin), int(games)


def _weighted(c: Counter[str], weight: float) -> Counter[str]:
    return c if weight == 1.0 else Counter({k: v * weight for k, v in c.items()})


def _totals(counts: Counts, window: Window) -> dict[str, Counter[str]]:
    """Each id's counts summed over its seasons and parities, each season's
    weighted by :meth:`Window.weight`."""
    totals: dict[str, Counter[str]] = defaultdict(Counter)
    for (owner, season, _), c in counts.items():
        totals[owner].update(_weighted(c, window.weight(season)))
    return dict(totals)


def _splits(counts: Counts) -> list[tuple[Counter[str], Counter[str]]]:
    """Each id-season's odd-game counts beside its even-game counts."""
    halves: dict[tuple[str, int], tuple[Counter[str], Counter[str]]] = defaultdict(lambda: (Counter(), Counter()))
    for (owner, season, parity), c in counts.items():
        halves[owner, season][0 if parity == 1 else 1].update(c)
    return list(halves.values())


def _mean(total: Counter[str], successes: str, trials: str) -> float:
    return total[successes] / total[trials] if total[trials] > 0 else 0.0


def _fit_rates(splits: Iterable[tuple[Counter[str], Counter[str]]], specs: Iterable[Spec], means: Mapping[str, float]) -> dict[str, float]:
    rows = list(splits)
    return {name: shrink.fit_strength(((odd[s], odd[t], even[s], even[t]) for odd, even in rows), means[name]) for name, s, t in specs}


def _mix_counts(c: Counter[str], prefix: str) -> dict[str, float]:
    return {zone: c[f"{prefix}.{zone}"] for zone in ZONES}


def _fit_mix(splits: Iterable[tuple[Counter[str], Counter[str]]], prefix: str, mu: Mapping[str, float]) -> float:
    return shrink.fit_mix_strength(((_mix_counts(odd, prefix), _mix_counts(even, prefix)) for odd, even in splits), mu)


def _rate(c: Counter[str], spec: Spec, means: Mapping[str, float], strengths: Mapping[str, float]) -> Rate:
    name, successes, trials = spec
    return Rate(value=shrink.shrink(c[successes], c[trials], means[name], strengths[name]), trials=c[trials], prior=means[name], strength=strengths[name])


def _mix(c: Counter[str], prefix: str, mu: Mapping[str, float], k: float) -> Mix:
    counts = _mix_counts(c, prefix)
    return Mix(probabilities=shrink.shrink_mix(counts, mu, k), trials=sum(counts.values()), strength=k)


def _league_mix(total: Counter[str], prefix: str) -> dict[str, float]:
    counts = _mix_counts(total, prefix)
    n = sum(counts.values())
    return {zone: (v / n if n > 0 else 1 / len(ZONES)) for zone, v in counts.items()}


def _player_record(athlete: str, identity: tuple[str, str], c: Counter[str], means: Mapping[str, float], strengths: Mapping[str, float], zone_mu: Mapping[str, float]) -> PlayerParams:
    rates = {spec[0]: _rate(c, spec, means, strengths) for spec in PLAYER_RATES}
    team, name = identity
    return PlayerParams(
        athlete_id=athlete,
        name=name,
        team_id=team,
        minutes=c["minutes"],
        shots_per_minute=rates["shots_per_minute"],
        free_throw_trips_per_minute=rates["free_throw_trips_per_minute"],
        turnovers_per_minute=rates["turnovers_per_minute"],
        zone_mix=_mix(c, "attempts", zone_mu, strengths["zone_mix"]),
        make_rate={zone: rates[f"make_rate.{zone}"] for zone in ZONES},
        and_one_rate=rates["and_one_rate"],
        assisted_rate=rates["assisted_rate"],
        free_throw_rate=rates["free_throw_rate"],
        live_ball_share=rates["live_ball_share"],
        assists_per_minute=rates["assists_per_minute"],
        offensive_rebounds_per_minute=rates["offensive_rebounds_per_minute"],
        defensive_rebounds_per_minute=rates["defensive_rebounds_per_minute"],
        steals_per_minute=rates["steals_per_minute"],
        blocks_per_minute=rates["blocks_per_minute"],
        fouls_per_minute=rates["fouls_per_minute"],
        usage_share=rates["usage_share"],
    )


def _team_record(team: str, name: str, c: Counter[str], means: Mapping[str, float], strengths: Mapping[str, float], zone_mu: Mapping[str, float]) -> TeamParams:
    rates = {spec[0]: _rate(c, spec, means, strengths) for spec in TEAM_RATES}
    return TeamParams(
        team_id=team,
        name=name,
        pace=rates["pace"],
        allowed_make_rate={zone: rates[f"allowed_make_rate.{zone}"] for zone in ZONES},
        forced_turnover_rate=rates["forced_turnover_rate"],
        foul_rate=rates["foul_rate"],
        defensive_rebound_rate=rates["defensive_rebound_rate"],
        allowed_zone_mix=_mix(c, "allowed_attempts", zone_mu, strengths["allowed_zone_mix"]),
        first_use_seconds=rates["first_use_seconds"],
    )


def _team_names(con: duckdb.DuckDBPyConnection) -> dict[str, str]:
    return dict(con.execute(f"SELECT team_id, display_name FROM {WAREHOUSE}.teams").fetchall())


def _league_totals(player_counts: Counts, team_counts: Counts, window: Window) -> tuple[Counter[str], Counter[str]]:
    """Everyone's counts summed, each season's weighted by :meth:`Window.weight`."""
    players: Counter[str] = Counter()
    for (_, season, _), c in player_counts.items():
        players.update(_weighted(c, window.weight(season)))
    teams: Counter[str] = Counter()
    for (_, season, _), c in team_counts.items():
        teams.update(_weighted(c, window.weight(season)))
    return players, teams


def _league_means(players: Counter[str], teams: Counter[str]) -> tuple[dict[str, float], dict[str, float]]:
    """The league mean of every player rate and every team rate. A team's
    allowed make rate shrinks toward the league's make rate in the zone
    (what every shooter did), and its defensive rebounding toward the
    league's share of misses the defense gets."""
    player_means = {name: _mean(players, s, t) for name, s, t in PLAYER_RATES}
    team_means = {name: _mean(teams, s, t) for name, s, t in TEAM_RATES}
    for zone in ZONES:
        team_means[f"allowed_make_rate.{zone}"] = player_means[f"make_rate.{zone}"]
    return player_means, team_means


def _league(
    window: Window,
    players: Counter[str],
    teams: Counter[str],
    player_means: Mapping[str, float],
    team_means: Mapping[str, float],
    strengths: Mapping[str, float],
    home_court: float,
    durations: Mapping[str, tuple[float, ...]],
) -> LeagueParams:
    return LeagueParams(
        window=window,
        pace=team_means["pace"],
        make_rate={zone: player_means[f"make_rate.{zone}"] for zone in ZONES},
        zone_mix=_league_mix(players, "attempts"),
        and_one_rate=player_means["and_one_rate"],
        assisted_rate=player_means["assisted_rate"],
        free_throw_rate=player_means["free_throw_rate"],
        live_ball_share=player_means["live_ball_share"],
        turnover_rate=_mean(teams, "tov", "poss"),
        foul_rate=_mean(teams, "trips", "poss"),
        offensive_rebound_rate=_mean(teams, "oreb", "opp_misses"),
        home_court=home_court,
        strengths=dict(strengths),
        per_minute={name: player_means[name] for name in sorted(PER_MINUTE_RATES)},
        durations=dict(durations),
        first_use_seconds=_mean(teams, "use_seconds", "first_uses"),
    )


def _selected(identities: Mapping[str, tuple[str, str]], team_ids: Sequence[str] | None, athlete_ids: Sequence[str] | None) -> set[str]:
    """The players to return: everyone whose latest team is a requested one,
    plus everyone named, or every player when nothing is requested."""
    if team_ids is None and athlete_ids is None:
        return set(identities)
    chosen = {a for a, (team, _) in identities.items() if team_ids is not None and team in team_ids}
    chosen.update(a for a in (athlete_ids or ()) if a in identities)
    return chosen


def estimate(con: duckdb.DuckDBPyConnection, window: Window, team_ids: Sequence[str] | None = None, athlete_ids: Sequence[str] | None = None) -> Estimates:
    """Estimate every rate from the window's games on ``con`` (a
    :func:`association.sim.database.connect` connection: ``sim_plays`` built,
    the warehouse attached as ``nba``).

    The league's means and every strength are fitted over EVERY player and
    team the window holds, whoever was asked for; ``team_ids`` and
    ``athlete_ids`` only choose which records come back - the players whose
    latest team in the window is a requested one, and the players named -
    and which ids are checked for ``missing``: a requested team with no game
    in the window, a rostered player of a requested team (or a named player)
    with no minutes in it. With neither given, every player and team is
    returned and every rostered player with no minutes is in ``missing``.

    .. versionadded:: 6.0.0
    """
    ctes, seasons = _window_ctes(window), _seasons_sql(window)
    player_counts: Counts = {}
    _read_box(con, ctes, player_counts)
    _read_plays(con, ctes, seasons, player_counts)
    _read_shots(con, ctes, seasons, player_counts)
    team_counts: Counts = {}
    _read_team_games(con, ctes, seasons, team_counts)
    _read_allowed_shots(con, ctes, team_counts)
    durations: dict[str, tuple[float, ...]] = {}
    if _has_table(con, "sim_actions"):
        durations = _read_durations(con, ctes, seasons, window)
        _read_first_uses(con, ctes, seasons, team_counts)
        if _has_table(con, "sim_segments"):
            _read_uses(con, ctes, seasons, player_counts)
    else:
        logger.warning("sim_possessions and sim_actions are not built (association sim build): no duration histograms, and the engine will refuse to play")
    identities, sat_out = _read_identities(con, ctes, team_ids)
    home_court, _ = _read_home_margin(con, ctes)

    player_total, team_total = _league_totals(player_counts, team_counts, window)
    player_means, team_means = _league_means(player_total, team_total)
    zone_mu = _league_mix(player_total, "attempts")
    player_splits, team_splits = _splits(player_counts), _splits(team_counts)
    strengths = _fit_rates(player_splits, PLAYER_RATES, player_means)
    strengths["zone_mix"] = _fit_mix(player_splits, "attempts", zone_mu)
    strengths.update(_fit_rates(team_splits, TEAM_RATES, team_means))
    strengths["allowed_zone_mix"] = _fit_mix(team_splits, "allowed_attempts", zone_mu)

    league = _league(window, player_total, team_total, player_means, team_means, strengths, home_court, durations)
    player_totals, team_totals = _totals(player_counts, window), _totals(team_counts, window)
    names = _team_names(con)
    teams = {t: _team_record(t, names.get(t, t), c, team_means, strengths, zone_mu) for t, c in team_totals.items() if team_ids is None or t in team_ids}
    players = {a: _player_record(a, identities[a], player_totals[a], player_means, strengths, zone_mu) for a in sorted(_selected(identities, team_ids, athlete_ids))}
    missing = {t for t in (team_ids or ()) if t not in team_totals} | sat_out | {a for a in (athlete_ids or ()) if a not in identities}
    return Estimates(league=league, teams=teams, players=players, missing=frozenset(missing))


def as_of_after(con: duckdb.DuckDBPyConnection, season: int, season_type: int = 2) -> dt.date:
    """The day after the last game of ``season`` (regular season by default)
    on record in ``nba.real_games``, in US Eastern terms - the ``as_of`` a
    whole-season window takes.

    .. versionadded:: 6.0.0
    """
    row = con.execute(f"SELECT max({eastern_date_sql('date')}) FROM {WAREHOUSE}.real_games WHERE season = ? AND season_type = ?", [season, season_type]).fetchone()
    if row is None or row[0] is None:
        raise ValueError(f"no games on record for season {season}, type {season_type}")
    last: dt.date = row[0]
    return last + dt.timedelta(days=1)
