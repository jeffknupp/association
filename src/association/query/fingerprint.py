"""Rendering a player's NetPoints "fingerprint" to a static HTML radar plot.

The querying half of the fingerprint feature - :mod:`association.query.radar`
does the drawing. Split the same way :mod:`association.query.shotchart` and
:mod:`association.query.court` are, and for the same reason: one implementation
behind both the fast-path template and anything else that wants a plot.

Modelled on espnanalytics.com's Net Pts Fingerprint, which is where the
underlying numbers come from: one axis per skill, in net points per 100
possessions, either as a percentile of the league or on a shared value scale.

Season level only. ``net_points_player_game`` carries an offense/defense/total
split and nothing else - there is no per-game play-type breakdown anywhere in the
warehouse - so a request to fingerprint one game is refused rather than answered
with the season's shape under a game's title.

.. versionadded:: 1.3.0
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import duckdb

from association.season import current_season, eastern_date

from .answer import Artifact, RenderResult
from .entities import Ambiguous, Availability, Entity, clarification, no_match
from .radar import VALUE_ZERO_FRACTION, Axis, Cell, Series, render_fingerprint_html


@dataclass(frozen=True)
class Skill:
    """One spoke: a play-type category, which side of the ball it is measured
    on, and the group it is drawn in.

    A skill is a (category, side) PAIR rather than a category alone. The
    warehouse stores three columns per category - offense, defense and their
    total - and rim protection and rim finishing are different skills that
    happen to share a column prefix. Plotting only one side per plot is what
    hid defense entirely.

    .. versionadded:: 1.3.0
    """

    category: str
    side: str
    group: str
    label: str

    @property
    def column(self) -> str:
        """The ``net_points_player_fingerprint`` column this skill reads."""
        return f"{self.category}_{FINGERPRINT_SIDES[self.side]}_net_pts"


# The 20 skills, grouped and in drawing order, mirroring espnanalytics.com's
# own Skill Fingerprint radar - the same five groups (scoring, shot types,
# creation, rebounding, defense) with the same skills in each, since the numbers
# come from its fingerprint file in the first place.
#
# Three things about this list are load-bearing:
#
# 1. The ORDER. A radar is read as a shape, and two players are comparable only
#    if their spokes mean the same thing at the same angle.
# 2. Each skill is a (category, side) PAIR. The warehouse stores three columns
#    per category, and rim finishing and rim protection are different skills
#    that happen to share a prefix. Drawing one side per plot is what hid
#    defense entirely in the first version of this.
# 3. WHICH skills. Not all 21 categories on both sides - that is 42 spokes, and
#    the aggregate ones (`two_pt`, `three_pt`) would double-count the slices
#    beneath them: a driving layup at the rim is already `driving`, `layup` AND
#    `rim`. The reference leaves the aggregates off its radar for that reason,
#    and so does this. `total` is excluded too - it is the sum being broken
#    down, and it is the headline instead.
#
# The defense group is where this departs from the reference, which shows
# "Defensive FG%" and "Misc Defense" - neither of which is a column here, and
# neither of which the site documents well enough to reconstruct. The two
# widest-swinging real defensive columns stand in for them, named for what they
# actually are.
FINGERPRINT_SKILLS: tuple[Skill, ...] = (
    Skill("rim", "offense", "scoring", "rim scoring"),
    Skill("mid_range", "offense", "scoring", "mid-range"),
    Skill("three_pt_shooting", "offense", "scoring", "3pt shooting"),
    Skill("corner", "offense", "scoring", "corner 3s"),
    Skill("fast_break", "offense", "scoring", "fast break"),
    Skill("driving", "offense", "shot types", "driving"),
    Skill("cutting", "offense", "shot types", "cutting"),
    Skill("floating", "offense", "shot types", "floaters"),
    Skill("fade", "offense", "shot types", "fadeaways"),
    Skill("hook", "offense", "shot types", "hook shots"),
    Skill("assist", "offense", "creation", "passing"),
    Skill("turnover", "offense", "creation", "turnover cost"),
    Skill("foul", "offense", "creation", "drawing fouls"),
    Skill("putback", "offense", "rebounding", "put-backs"),
    Skill("rebound", "offense", "rebounding", "O-rebounding"),
    Skill("rebound", "defense", "rebounding", "D-rebounding"),
    Skill("turnover", "defense", "defense", "forcing TOs"),
    Skill("foul", "defense", "defense", "fouling"),
    Skill("two_pt", "defense", "defense", "2pt defense"),
    Skill("three_pt", "defense", "defense", "3pt defense"),
)

# Which of the three stored columns per category a skill reads.
FINGERPRINT_SIDES: dict[str, str] = {"offense": "o", "defense": "d", "total": "t"}

# The category summing each side, reported as the bolded headline above the plot
# rather than as a spoke - it is the number the spokes break down.
FINGERPRINT_SUMMARY_CATEGORY = "total"

GAME_FINGERPRINT_AVAILABILITY = Availability("net_points_player_game_fingerprint")
"""Where a single game's fingerprint rows live, for narrowing an ambiguous name
when one game is being drawn. Separate from the season constant below because a
row in the season file does not imply a row per game - a player traded in
January has both, and one who played two minutes all year may have neither.

.. versionadded:: 2.1.0
"""

FINGERPRINT_AVAILABILITY = Availability("net_points_player_fingerprint")
"""Where a fingerprint's rows live, for narrowing an ambiguous name to the
players who have a fingerprint in the season being plotted.

.. versionadded:: 2.1.0
"""

# Minutes a player must have logged to be in the pool a percentile is measured
# against - the same floor the netpoints leaderboard metrics use. Without it a
# 40-minute call-up with one good possession sets "league best" and every real
# player is drawn against a number nobody sustained.
FINGERPRINT_MIN_MINUTES = 500

FINGERPRINT_SCALES = ("percentile", "value")
"""The two radial scales, as espnanalytics.com offers them: rank against the
league, or net points on one shared scale so bigger skills draw bigger.

.. versionadded:: 1.3.0
"""

FINGERPRINT_VIEWS = ("total", "offense", "defense")
"""Which skills a plot draws: every one, or only the offensive or defensive
half. Not a choice of COLUMN - each skill already carries the side it is
measured on - so ``"total"`` means the whole fingerprint rather than the
``_t_net_pts`` columns.

.. versionadded:: 1.3.0
"""


class FingerprintUnavailable(Exception):
    """Raised when no fingerprint can be drawn for what was asked.

    The message is written to be shown to a person: a season with no
    fingerprint rows, an unknown view or scale, or a request scoped to a single
    game, which this data cannot answer at all.

    .. versionadded:: 1.3.0
    """


@dataclass(frozen=True)
class SkillValue:
    """One player's standing in one skill.

    ``value`` is the plotted number; ``percentile`` is its rank in the
    qualified pool as a fraction in 0..1, with 1.0 the league best.

    .. versionadded:: 1.3.0
    """

    skill: Skill
    total: float
    value: float
    percentile: float
    league_average: float
    league_best: float


@dataclass(frozen=True)
class PlayerFingerprint:
    """Everything one player contributes to a plot: their resolved name, the
    possessions the rates are over, the overall/offense/defense headline in net
    points per 100, and one :class:`SkillValue` per drawn skill.

    .. versionadded:: 1.3.0
    """

    athlete_id: str
    name: str
    season: int
    minutes: float
    possessions: float
    qualified: bool
    overall: float
    offense: float
    defense: float
    overall_percentile: float
    offense_percentile: float
    defense_percentile: float
    values: list[SkillValue]


@dataclass(frozen=True)
class LeagueScale:
    """The shared value scale a value-scaled plot is drawn against: the largest
    and smallest per-100 figures anyone in the qualified pool posted in any
    drawn skill, plus how many players that pool held.

    .. versionadded:: 1.3.0
    """

    best: float
    worst: float
    pool_size: int


@dataclass(frozen=True)
class Unit:
    """How the plotted numbers should be read.

    A season fingerprint is drawn in net points per 100 possessions; a single
    game's is drawn in the net points of that game, because a per-100 rate over
    one game's ~30 possessions magnifies a single shot into a league-leading
    rate. The two are the same shape and different quantities, so the labels
    travel with the numbers rather than being hardcoded in the renderer - a
    plot headed "per 100 poss" over one game's totals is the wrong-caption
    version of the wrong-answer bug this codebase keeps finding.

    .. versionadded:: 2.1.0
    """

    headline: str
    """Label over the summary figure, e.g. ``"total net pts / 100"``."""

    axis: str
    """Suffix in a spoke's tooltip, e.g. ``"per 100 poss"``."""

    column: str
    """Heading of the value column in the table under the plot."""

    prose: str
    """How the quantity is named in a sentence, e.g. ``"net points per 100 possessions"``."""


PER_100_POSSESSIONS = Unit(headline="total net pts / 100", axis="per 100 poss", column="per 100", prose="net points per 100 possessions")
"""The unit a season fingerprint is drawn in.

.. versionadded:: 2.1.0
"""

PER_GAME = Unit(headline="total net pts", axis="in this game", column="net pts", prose="net points in the game")
"""The unit a single game's fingerprint is drawn in.

.. versionadded:: 2.1.0
"""

# Possessions a player must have had in a game to join the pool that game's
# percentiles are measured against - the per-game counterpart of
# FINGERPRINT_MIN_MINUTES. Without it a garbage-time cameo with one possession
# sets "best in the league" for a skill and every real performance is drawn
# against a number nobody played enough to earn.
FINGERPRINT_MIN_GAME_POSSESSIONS = 20


def skills_for(view: str) -> tuple[Skill, ...]:
    """The skills a ``view`` draws - all of them, or one side's.

    .. versionadded:: 1.3.0
    """
    if view not in FINGERPRINT_VIEWS:
        raise FingerprintUnavailable(f"view must be one of {list(FINGERPRINT_VIEWS)} - got {view!r}.")
    if view == "total":
        return FINGERPRINT_SKILLS
    return tuple(skill for skill in FINGERPRINT_SKILLS if skill.side == view)


def _percentile(values: list[float], value: float) -> float:
    """Where ``value`` ranks in a SEASON pool, as a fraction with 1.0 the best.

    Counts everyone this value is at least as good as, so the league best comes
    out at exactly 1.0 and lands on the outer ring the plot labels "league
    best". Over a season that is also unambiguous: the values are averages over
    thousands of possessions and exact ties essentially do not occur.

    A single game is the opposite case - see :func:`_percentile_midrank`.
    """
    return sum(1 for other in values if other <= value) / len(values)


def _percentile_midrank(values: list[float], value: float) -> float:
    """Where ``value`` ranks in a pool of single GAMES, ties taking the middle
    of the tied block rather than the top of it.

    Load-bearing here and nowhere else. In one game most players are exactly
    0.00 in most categories - they attempted no hook shots at all - and
    counting ``other <= value`` reads that as beating everyone else who also
    attempted none: measured on a real plot, +0.00 hook shots came out at the
    89th percentile and +0.00 fadeaways at the 84th, and the radar drew long
    spokes for skills the player never used. Every number in that table was
    individually defensible, which is why only looking at the rendered picture
    caught it.

    Midrank puts "did nothing, like most people" near the middle, which is what
    it means. It gives up the exact 1.0 at the top, but the game pool is ~11,000
    so the best game still rounds onto the outer ring.
    """
    below = sum(1 for other in values if other < value)
    tied = sum(1 for other in values if other == value)
    return (below + tied / 2) / len(values)


def load_fingerprints(
    con: duckdb.DuckDBPyConnection,
    players: list[Entity],
    season: int,
    view: str = "total",
    min_minutes: int = FINGERPRINT_MIN_MINUTES,
) -> tuple[list[PlayerFingerprint], LeagueScale]:
    """One season's fingerprint for each named player, with league context.

    The whole season is read in one query and the pool, the percentiles and the
    players' own rows all come out of it. Deliberately not three queries: a
    percentile computed against a pool selected separately from the player's own
    row can put a player above a "league best" that excluded them.

    Args:
        con: A read-only warehouse connection.
        players: The players to plot, already resolved, in drawing order. They
            are carried through by name so a plot is titled with the resolved
            name rather than an athlete_id.
        season: Season-ending year - 2024 is the 2023-24 season.
        view: ``"total"``, ``"offense"`` or ``"defense"`` - see
            :data:`FINGERPRINT_VIEWS`.
        min_minutes: Minutes needed to join the pool percentiles are measured
            against. A player below it is still plotted, and said to be below it.

    Returns:
        The fingerprints, in the order asked for, and the league scale.

    Raises:
        FingerprintUnavailable: unknown ``view``, no fingerprint rows for the
            season, or none of the named players has a row in it. The last two
            are separate messages: the first is a fact about the season, the
            second names the players it could not find.

    .. versionadded:: 1.3.0
    """
    skills = skills_for(view)
    summary = [f"{FINGERPRINT_SUMMARY_CATEGORY}_{letter}_net_pts" for letter in ("t", "o", "d")]
    columns = summary + [skill.column for skill in skills]
    rows = con.execute(
        # No season_type filter: net_points_player_fingerprint has no such
        # column. Whatever the season's rows cover is what a fingerprint means.
        f"SELECT athlete_id, minutes, total_poss, {', '.join(columns)} FROM net_points_player_fingerprint WHERE season = ?",
        [season],
    ).fetchall()
    if not rows:
        raise FingerprintUnavailable(f"The warehouse has no NetPoints fingerprint data for season {season}.")

    # per-100 is the comparable unit; a season total mostly ranks by minutes.
    scaled: dict[str, tuple[float, float, list[float], list[float]]] = {}
    for athlete_id, minutes, possessions, *values in rows:
        if not possessions:
            continue
        factor = 100.0 / possessions
        headline = [(v or 0.0) * factor for v in values[: len(summary)]]
        scaled[athlete_id] = (minutes or 0.0, possessions, headline, [(v or 0.0) * factor for v in values[len(summary) :]])

    qualified = [(headline, values) for minutes, _, headline, values in scaled.values() if minutes >= min_minutes]
    if not qualified:
        raise FingerprintUnavailable(f"No player reached {min_minutes} minutes in season {season}, so there is nothing to compare against.")
    pool = [values for _, values in qualified]
    by_axis = [[player_values[index] for player_values in pool] for index in range(len(skills))]
    # The headline three are ranked against the same pool as the spokes, so the
    # percentile on "+1.20 overall" means what the percentiles under it mean.
    headline_pool = [[headline[index] for headline, _ in qualified] for index in range(len(summary))]

    fingerprints = []
    for player in players:
        entry = scaled.get(player.id)
        if entry is None:
            continue
        minutes, possessions, headline, values = entry
        skill_values = []
        for index, skill in enumerate(skills):
            others = by_axis[index]
            value = values[index]
            # Net points are already signed toward "good" in every category - a
            # turnover category is negative on offense and positive on defense -
            # so a higher percentile means better on every axis, with no
            # per-skill direction to get backwards.
            skill_values.append(
                SkillValue(
                    skill=skill,
                    total=value * possessions / 100.0,
                    value=value,
                    percentile=_percentile(others, value),
                    league_average=sum(others) / len(others),
                    league_best=max(others),
                )
            )
        fingerprints.append(
            PlayerFingerprint(
                athlete_id=player.id,
                name=player.name,
                season=season,
                minutes=minutes,
                possessions=possessions,
                qualified=minutes >= min_minutes,
                overall=headline[0],
                offense=headline[1],
                defense=headline[2],
                overall_percentile=_percentile(headline_pool[0], headline[0]),
                offense_percentile=_percentile(headline_pool[1], headline[1]),
                defense_percentile=_percentile(headline_pool[2], headline[2]),
                values=skill_values,
            )
        )
    if not fingerprints:
        # Names the PLAYERS, because the season is known to have rows by the
        # time control reaches here. This branch used to report the season as
        # empty, which is a false coverage claim whenever the real cause is a
        # name that resolved to somebody with no fingerprint: "Maxey" matched
        # Marlon Maxey (retired 1994) ahead of Tyrese, and season 2026 - which
        # holds 566 players, Tyrese among them - was reported as having no
        # fingerprint data at all. A missing player and a missing season are
        # different facts and now read as different sentences.
        who = ", ".join(player.name for player in players) if players else "any player"
        held = f"{len(rows)} player" + ("" if len(rows) == 1 else "s")
        raise FingerprintUnavailable(f"No NetPoints fingerprint on record for {who} in season {season}, which has {held} on record.")

    flat = [value for player_values in pool for value in player_values]
    return fingerprints, LeagueScale(best=max(flat), worst=min(flat), pool_size=len(pool))


@dataclass(frozen=True)
class GamePlayed:
    """Which game a per-game fingerprint was drawn for.

    .. versionadded:: 2.1.0
    """

    event_id: str
    date: str


def _skill_expression(skill: Skill) -> str:
    """The pivot for one skill, over the long per-game table.

    ``net_points_player_game_fingerprint`` is one row per category, not one
    column per category, so the season file's wide shape is rebuilt here.
    Interpolated rather than parameterised because both halves come from
    :data:`FINGERPRINT_SKILLS`, which is code.
    """
    return f"max(CASE WHEN f.category = '{skill.category}' THEN f.{FINGERPRINT_SIDES[skill.side]}_net_pts END)"


def load_game_fingerprints(
    con: duckdb.DuckDBPyConnection,
    players: list[Entity],
    season: int,
    season_type: int = 2,
    order: str = "recent",
    view: str = "total",
    min_possessions: int = FINGERPRINT_MIN_GAME_POSSESSIONS,
) -> tuple[list[PlayerFingerprint], LeagueScale, dict[str, GamePlayed]]:
    """One GAME's fingerprint for each named player, with league context.

    The same shape as :func:`load_fingerprints` over a different table and a
    different unit. Two things are deliberately not carried over:

    - **The numbers are that game's net points, not a per-100 rate.** Over one
      game's ~30 possessions a per-100 rate turns a single made corner three
      into a league-leading season figure. It is also what the source does:
      espnanalytics.com's own per-game awards ("Facilitator", "Corner Pocket")
      rank raw net points in the game.
    - **The pool is every player-GAME in the season**, not every player. A
      percentile against season rates would put any decent game in the 99th,
      since a season average is the mean of games like this one. Ranked against
      other single games, "92nd percentile passing" means a top-tenth passing
      game, which is the claim a reader will take from it.

    Args:
        con: A read-only warehouse connection.
        players: The players to plot, already resolved, in drawing order.
        season: Season-ending year.
        season_type: 2 regular season, 3 postseason.
        order: ``"recent"`` for each player's latest game in the season,
            ``"first"`` for their earliest.
        view: ``"total"``, ``"offense"`` or ``"defense"``.
        min_possessions: Possessions a game needs to join the percentile pool.

    Returns:
        The fingerprints in the order asked for, the league scale, and which
        game each player's is drawn from.

    Raises:
        FingerprintUnavailable: unknown ``view``, the per-game table missing or
            empty for the season, or none of the named players played a game in
            it. As in :func:`load_fingerprints`, those are separate messages -
            a missing season and a missing player are different facts.

    .. versionadded:: 2.1.0
    """
    skills = skills_for(view)
    # Qualified with `f.`, every one: net_points_player_game carries o/d/t_net_pts
    # of its own, so an unqualified name here is ambiguous rather than wrong -
    # DuckDB says so, but only once the join is present.
    summary = [f"max(CASE WHEN f.category = '{FINGERPRINT_SUMMARY_CATEGORY}' THEN f.{letter}_net_pts END)" for letter in ("t", "o", "d")]
    columns = summary + [_skill_expression(skill) for skill in skills]
    try:
        rows = con.execute(
            f"SELECT f.event_id, f.athlete_id, max(g.t_poss), {', '.join(columns)} "
            "FROM net_points_player_game_fingerprint f "
            # A plain join: net_points_player_game holds one row per
            # player-game. It used to hold two for 611 of them in 2026 alone,
            # disagreeing, because two NetPoints dates resolved to one ESPN
            # game - fixed at the source in NetPointsGameIndex rather than
            # collapsed here. max() over the group is still what reads the
            # possessions, since the group is the category pivot.
            "JOIN net_points_player_game g ON g.event_id = f.event_id AND g.athlete_id = f.athlete_id "
            "WHERE f.season = ? AND f.season_type = ? AND f.athlete_id IS NOT NULL "
            "GROUP BY f.event_id, f.athlete_id",
            [season, season_type],
        ).fetchall()
    except duckdb.CatalogException as exc:
        # ONLY the missing-table case. A bare `except duckdb.Error` here read a
        # binder error in this module's own SQL as "you have not pulled this
        # data", which is the wrong-cause refusal in its purest form: a made-up
        # answer about the warehouse, produced by a bug in the query above it.
        raise FingerprintUnavailable(f"Per-game NetPoints fingerprints are not in this warehouse - pull them with `data pull --include-net-points-daily`. ({exc})") from exc
    if not rows:
        raise FingerprintUnavailable(f"The warehouse has no per-game NetPoints fingerprint data for season {season}.")

    pool_values: list[list[float]] = []
    headline_pool: list[list[float]] = [[], [], []]
    by_game: dict[tuple[str, str], tuple[float, list[float], list[float]]] = {}
    for event_id, athlete_id, possessions, *values in rows:
        headline = [v or 0.0 for v in values[: len(summary)]]
        skill_row = [v or 0.0 for v in values[len(summary) :]]
        by_game[(str(athlete_id), str(event_id))] = (possessions or 0.0, headline, skill_row)
        if (possessions or 0.0) >= min_possessions:
            pool_values.append(skill_row)
            for index in range(3):
                headline_pool[index].append(headline[index])
    if not pool_values:
        raise FingerprintUnavailable(f"No game in season {season} reached {min_possessions} possessions, so there is nothing to compare against.")

    by_axis = [[game[index] for game in pool_values] for index in range(len(skills))]

    fingerprints: list[PlayerFingerprint] = []
    games: dict[str, GamePlayed] = {}
    for player in players:
        played = _game_for(con, player.id, season, season_type, order)
        if played is None:
            continue
        entry = by_game.get((player.id, played.event_id))
        if entry is None:
            continue
        possessions, headline, skill_row = entry
        games[player.id] = played
        fingerprints.append(
            PlayerFingerprint(
                athlete_id=player.id,
                name=player.name,
                season=season,
                minutes=0.0,
                possessions=possessions,
                qualified=possessions >= min_possessions,
                overall=headline[0],
                offense=headline[1],
                defense=headline[2],
                overall_percentile=_percentile_midrank(headline_pool[0], headline[0]),
                offense_percentile=_percentile_midrank(headline_pool[1], headline[1]),
                defense_percentile=_percentile_midrank(headline_pool[2], headline[2]),
                values=[
                    SkillValue(
                        skill=skill,
                        total=skill_row[index],
                        value=skill_row[index],
                        percentile=_percentile_midrank(by_axis[index], skill_row[index]),
                        league_average=sum(by_axis[index]) / len(by_axis[index]),
                        league_best=max(by_axis[index]),
                    )
                    for index, skill in enumerate(skills)
                ],
            )
        )
    if not fingerprints:
        who = ", ".join(player.name for player in players) if players else "any player"
        which = "earliest" if order == "first" else "most recent"
        raise FingerprintUnavailable(f"No per-game NetPoints fingerprint on record for {who}'s {which} game of season {season}.")

    flat = [value for game in pool_values for value in game]
    return fingerprints, LeagueScale(best=max(flat), worst=min(flat), pool_size=len(pool_values)), games


def _game_for(con: duckdb.DuckDBPyConnection, athlete_id: str, season: int, season_type: int, order: str) -> GamePlayed | None:
    """One player's first or most recent game of a season, by real date.

    Dated from ``games`` rather than by event_id order: ids are assigned by
    schedule, and a postponed game keeps the id it was given for the date it
    was meant to be played on.
    """
    row = con.execute(
        "SELECT f.event_id, gm.date FROM net_points_player_game_fingerprint f "
        "JOIN games gm ON gm.event_id = f.event_id "
        "WHERE f.athlete_id = ? AND f.season = ? AND f.season_type = ? "
        f"GROUP BY f.event_id, gm.date ORDER BY gm.date {'ASC' if order == 'first' else 'DESC'} LIMIT 1",
        [athlete_id, season, season_type],
    ).fetchone()
    return None if row is None else GamePlayed(event_id=str(row[0]), date=eastern_date(row[1]))


def _radius(value: float, percentile: float, scale: str, league: LeagueScale) -> float:
    """Where a skill's value sits on the radius, as a fraction in 0..1."""
    if scale == "percentile":
        return percentile
    if value >= 0:
        return VALUE_ZERO_FRACTION + (value / league.best) * (1 - VALUE_ZERO_FRACTION) if league.best > 0 else VALUE_ZERO_FRACTION
    # Below zero shrinks toward the center, hitting it at the league's worst.
    return VALUE_ZERO_FRACTION * (1 - min(1.0, value / league.worst)) if league.worst < 0 else VALUE_ZERO_FRACTION


def _ordinal(percentile: float) -> str:
    rank = max(1, round(percentile * 100))
    suffix = "th" if 11 <= rank % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(rank % 10, "th")
    return f"{rank}{suffix}"


def build_series(fingerprint: PlayerFingerprint, scale: str, league: LeagueScale, unit: Unit = PER_100_POSSESSIONS) -> Series:
    """Turn one loaded fingerprint into a drawable radar series.

    .. versionadded:: 1.3.0

    .. versionchanged:: 2.1.0
       Takes the ``unit`` the numbers are in, so a single game's plot is not
       captioned as a per-100 rate.
    """
    return Series(
        name=fingerprint.name,
        headline=[
            (unit.headline, f"{fingerprint.overall:+.2f}", f"{_ordinal(fingerprint.overall_percentile)} pct"),
            ("offense", f"{fingerprint.offense:+.2f}", f"{_ordinal(fingerprint.offense_percentile)} pct"),
            ("defense", f"{fingerprint.defense:+.2f}", f"{_ordinal(fingerprint.defense_percentile)} pct"),
        ],
        axes=[
            Axis(
                label=value.skill.label,
                group=value.skill.group,
                radius=_radius(value.value, value.percentile, scale, league),
                tooltip=(f"{value.skill.label}: {value.value:+.2f} {unit.axis}, {_ordinal(value.percentile)} percentile (league average {value.league_average:+.2f}, best {value.league_best:+.2f})"),
            )
            for value in fingerprint.values
        ],
    )


def _rings(scale: str) -> list[tuple[float, str]]:
    if scale == "percentile":
        return [(0.25, ""), (0.5, "league median"), (0.75, ""), (1.0, "league best")]
    return [(VALUE_ZERO_FRACTION, "zero"), (0.5, ""), (0.75, ""), (1.0, "league best")]


def _table(fingerprints: list[PlayerFingerprint], scale: str, unit: Unit = PER_100_POSSESSIONS) -> tuple[list[str], list[tuple[str, str, list[Cell]]]]:
    """The numbers under the plot. The radar is a shape; this is what makes it
    checkable, which is why it is always drawn and not an option.

    In a comparison every row shades the leader's cell in that player's color,
    with the shade carrying how far ahead they are: the widest gap in the table
    is full strength and everything else is a fraction of it, so a row that is
    all but tied looks all but tied instead of looking like a win.
    """
    values = [f.values for f in fingerprints]
    gaps = [max(column) - min(column) for column in ([v[i].value for v in values] for i in range(len(values[0])))]
    widest = max(gaps) or 1.0

    if len(fingerprints) == 1:
        headers = [unit.column, "percentile", "league avg", "league best"]
        rows = [
            (
                value.skill.label,
                value.skill.group,
                [
                    Cell(f"{value.value:+.2f}"),
                    Cell(_ordinal(value.percentile)),
                    Cell(f"{value.league_average:+.2f}"),
                    Cell(f"{value.league_best:+.2f}"),
                ],
            )
            for value in fingerprints[0].values
        ]
    else:
        headers = [f.name for f in fingerprints] + ["league avg"]
        rows = []
        for index, gap in enumerate(gaps):
            column = [f.values[index].value for f in fingerprints]
            leader = column.index(max(column)) if gap > 0 else None
            # A floor under the shade, so a real but narrow lead is still
            # visible rather than rounding away to no highlight at all.
            intensity = 0.08 + 0.55 * (gap / widest) if leader is not None else 0.0
            rows.append(
                (
                    values[0][index].skill.label,
                    values[0][index].skill.group,
                    [Cell(f"{value:+.2f}", leader if position == leader else None, intensity) for position, value in enumerate(column)]
                    + [Cell(f"{fingerprints[0].values[index].league_average:+.2f}")],
                )
            )

    # Grouped exactly as the plot is grouped, and sorted by the drawn quantity
    # WITHIN each group. Sorting across groups instead would list the skills in
    # an order the radar never shows, and the table is what the radar is checked
    # against.
    key = [-f.percentile for f in fingerprints[0].values] if scale == "percentile" else [-abs(f.value) for f in fingerprints[0].values]
    order = {group: index for index, group in enumerate(dict.fromkeys(skill.group for skill in FINGERPRINT_SKILLS))}
    return headers, [row for _, row in sorted(zip(key, rows, strict=True), key=lambda pair: (order[pair[1][1]], pair[0]))]


def _when_drawn(fingerprints: list[PlayerFingerprint], games: dict[str, GamePlayed], season: int) -> str:
    """What the plot covers, for the subtitle and the message.

    A single game is named by its date rather than by "one game", since the
    whole point of the scoping is that a reader can tell which. Two players
    compared on their own last games were not playing each other, so both dates
    are named - saying "2026-04-13" over a plot half of which is a different
    night is the caption version of answering a question nobody asked.
    """
    if not games:
        return f"{season} season"
    dates = sorted({game.date for game in games.values()})
    return dates[0] if len(dates) == 1 else " and ".join(dates)


def render_for_players(
    con: duckdb.DuckDBPyConnection,
    out_dir: Path,
    players: list[Entity],
    ambiguous: list[str],
    season: int,
    view: str = "total",
    scale: str = "percentile",
    min_minutes: int = FINGERPRINT_MIN_MINUTES,
    season_type: int = 2,
    order: str | None = None,
) -> RenderResult:
    """Render already-resolved players' fingerprints to one radar plot.

    Args:
        con: A read-only warehouse connection.
        out_dir: Directory the HTML is written to; created if missing.
        players: The players to draw, already resolved to warehouse entities.
        ambiguous: Other names that also matched, mentioned in the message -
            whether or not anything was drawn.
        season: Season-ending year.
        view: ``"total"``, ``"offense"`` or ``"defense"``.
        scale: ``"percentile"`` or ``"value"`` - see :data:`FINGERPRINT_SCALES`.
        min_minutes: The pool floor passed to :func:`load_fingerprints`.
        season_type: 2 regular season, 3 postseason. Only read when ``order``
            asks for a single game; the season file has no season_type column.
        order: ``None`` for the whole season, or ``"recent"``/``"first"`` to
            draw one game - each player's latest or earliest of the season.

    Returns:
        A :class:`association.query.answer.RenderResult`: the message, and the
        file written. ``artifact`` is never None here - a fingerprint that
        cannot be drawn raises instead.

    Raises:
        FingerprintUnavailable: nothing could be drawn - see
            :func:`load_fingerprints`.

    .. versionadded:: 1.3.0

    .. versionchanged:: 2.0.0
       Returns a :class:`association.query.answer.RenderResult` rather than a
       ``(message, path)`` tuple, the same shape
       :func:`association.query.shotchart.render_for_player` now returns.
    """
    if scale not in FINGERPRINT_SCALES:
        raise FingerprintUnavailable(f"scale must be one of {list(FINGERPRINT_SCALES)} - got {scale!r}.")
    games: dict[str, GamePlayed] = {}
    unit = PER_GAME if order else PER_100_POSSESSIONS
    try:
        if order:
            fingerprints, league, games = load_game_fingerprints(con, players, season, season_type=season_type, order=order, view=view)
        else:
            fingerprints, league = load_fingerprints(con, players, season, view=view, min_minutes=min_minutes)
    except FingerprintUnavailable as exc:
        # The other matches are carried onto the failure path, not only the
        # success one. Names here are resolved best-match, and what makes that
        # safe is the plot being titled with the name that won - which is
        # precisely what does not happen when nothing is drawn. Dropping the
        # runners-up in the one case that needs them is what left "Maxey"
        # reading as a data gap rather than as an ambiguous name.
        if not ambiguous:
            raise
        raise FingerprintUnavailable(f"{exc} Note: other players also matched: {', '.join(ambiguous)}.") from exc
    # A player with no row in this season's fingerprint file is dropped by
    # load_fingerprints rather than drawn as a zero polygon, which would read as
    # "played and contributed nothing". Named in the message instead.
    drawn = {f.athlete_id for f in fingerprints}
    missing = [p.name for p in players if p.id not in drawn]

    title = " vs ".join(f.name for f in fingerprints)
    ranked_against = "games" if order else "the league"
    units = f"percentile of {ranked_against}" if scale == "percentile" else unit.prose
    scope = {"total": "offense and defense", "offense": "offensive skills only", "defense": "defensive skills only"}[view]
    when = _when_drawn(fingerprints, games, season)
    subtitle = f"{when} - {scope}, {units}"
    pool = f"{league.pool_size} games" if order else f"{league.pool_size} players with at least {min_minutes} minutes"
    axis_note = (
        f"Each spoke is one skill, in {unit.prose}, as a percentile of the {pool} in the {season} season. Further out is better."
        if scale == "percentile"
        else (f"Each spoke is one skill, in {unit.prose}, on one shared scale - bigger skills draw bigger. The inner ring is zero; the outer is the best any of the {pool} posted in any skill.")
    )
    headers, table_rows = _table(fingerprints, scale, unit)
    html = render_fingerprint_html(
        title=title,
        subtitle=subtitle,
        series=[build_series(fingerprint, scale, league, unit) for fingerprint in fingerprints],
        rings=_rings(scale),
        axis_note=axis_note,
        table_headers=headers,
        table_rows=table_rows,
    )

    safe = "_vs_".join("".join(c if c.isalnum() else "_" for c in f.name.lower()) for f in fingerprints)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = f"{season}_{order}_game" if order else str(season)
    out_path = out_dir / f"fingerprint_{safe}_{stamp}_{view}_{scale}.html"
    out_path.write_text(html)

    message = f"Rendered NetPoints fingerprint ({view}) for {title} ({when}, {scale} scale) to {out_path}"
    unqualified = [f.name for f in fingerprints if not f.qualified]
    if unqualified:
        short = f"under {FINGERPRINT_MIN_GAME_POSSESSIONS} possessions in that game" if order else f"under {min_minutes} minutes"
        message += f". Note: {', '.join(unqualified)} played {short}, so they are plotted against a pool they are not in"
    if missing:
        message += f". No fingerprint on record for: {', '.join(missing)}"
    if ambiguous:
        message += f". Note: other players also matched: {ambiguous}"
    return RenderResult(message, Artifact("fingerprint", out_path))


def render_fingerprint(
    con: duckdb.DuckDBPyConnection,
    out_dir: Path,
    player_name: str,
    season: int | None = None,
    view: str = "total",
    scale: str = "percentile",
) -> RenderResult:
    """Resolve one or more player names and render their fingerprint.

    The single-call entry point, and the counterpart to
    :func:`association.query.shotchart.render_shot_chart`. Names are resolved
    best-match, as a chart's are: a plot titled with the resolved name shows a
    wrong match on sight, which is what makes best-match safe here and not in a
    template reporting numbers.

    Args:
        con: A read-only warehouse connection.
        out_dir: Directory the HTML is written to.
        player_name: Full or partial display name. Several, separated by
            ``" vs "``, draw one plot comparing them.
        season: Season-ending year; the current season when omitted. NOT "every
            season" - a fingerprint is a season's shape, and there is no career
            row to plot.
        view: ``"total"``, ``"offense"`` or ``"defense"``.
        scale: ``"percentile"`` or ``"value"``.

    Returns:
        A :class:`association.query.answer.RenderResult` naming the player and
        the file written, or saying why nothing could be drawn - in which case
        ``artifact`` is None.

    .. versionadded:: 1.3.0

    .. versionchanged:: 2.0.0
       Returns a :class:`association.query.answer.RenderResult` rather than a
       message string, so a caller can reach the file that was drawn.

    .. versionchanged:: 2.1.0
       Names are narrowed to the players who have a fingerprint in ``season``
       before the best match is taken, and an ambiguity that survives that is
       answered with a clarifying question rather than a plot. See
       :func:`association.query.shotchart.resolve_chart_player`.
    """
    # Imported here, not at module scope: shotchart imports nothing from this
    # module, and a top-level import in the other direction would still be a
    # cycle waiting for the first edit that reverses it.
    from .shotchart import resolve_chart_player

    # Settled before any name is resolved, so a name narrows against the season
    # that will actually be drawn: "Maxey" is Tyrese in 2026 and nobody at all
    # in 2005.
    season = season if season is not None else current_season()
    resolved, ambiguous = [], []
    for name in player_name.split(" vs "):
        found = resolve_chart_player(con, name.strip(), FINGERPRINT_AVAILABILITY, season)
        if found is None:
            return RenderResult(no_match(con, name.strip()), None)
        if isinstance(found, Ambiguous):
            return RenderResult(clarification(name.strip(), found.candidates, active=found.active), None)
        player, also = found
        resolved.append(player)
        ambiguous.extend(also)
    try:
        rendered = render_for_players(con, out_dir, resolved, ambiguous, season, view=view, scale=scale)
    except FingerprintUnavailable as exc:
        return RenderResult(str(exc), None)
    return rendered
