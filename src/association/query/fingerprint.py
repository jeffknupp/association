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

from association.season import current_season

from .entities import Entity
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

    ``per_100`` is the plotted number; ``percentile`` is its rank in the
    qualified pool as a fraction in 0..1, with 1.0 the league best.

    .. versionadded:: 1.3.0
    """

    skill: Skill
    season_total: float
    per_100: float
    percentile: float
    league_average_per_100: float
    league_best_per_100: float


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
    overall_per_100: float
    offense_per_100: float
    defense_per_100: float
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


def skills_for(view: str) -> tuple[Skill, ...]:
    """The skills a ``view`` draws - all of them, or one side's.

    .. versionadded:: 1.3.0
    """
    if view not in FINGERPRINT_VIEWS:
        raise FingerprintUnavailable(f"view must be one of {list(FINGERPRINT_VIEWS)} - got {view!r}.")
    if view == "total":
        return FINGERPRINT_SKILLS
    return tuple(skill for skill in FINGERPRINT_SKILLS if skill.side == view)


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
            season, or none of the named players has a row in it.

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

    def percentile(values: list[float], value: float) -> float:
        """Where ``value`` ranks in ``values``, as a fraction with 1.0 the best."""
        return sum(1 for other in values if other <= value) / len(values)

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
                    season_total=value * possessions / 100.0,
                    per_100=value,
                    percentile=percentile(others, value),
                    league_average_per_100=sum(others) / len(others),
                    league_best_per_100=max(others),
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
                overall_per_100=headline[0],
                offense_per_100=headline[1],
                defense_per_100=headline[2],
                overall_percentile=percentile(headline_pool[0], headline[0]),
                offense_percentile=percentile(headline_pool[1], headline[1]),
                defense_percentile=percentile(headline_pool[2], headline[2]),
                values=skill_values,
            )
        )
    if not fingerprints:
        raise FingerprintUnavailable(f"No NetPoints fingerprint on record for season {season}.")

    flat = [value for player_values in pool for value in player_values]
    return fingerprints, LeagueScale(best=max(flat), worst=min(flat), pool_size=len(pool))


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


def build_series(fingerprint: PlayerFingerprint, scale: str, league: LeagueScale) -> Series:
    """Turn one loaded fingerprint into a drawable radar series.

    .. versionadded:: 1.3.0
    """
    return Series(
        name=fingerprint.name,
        headline=[
            ("total net pts / 100", f"{fingerprint.overall_per_100:+.2f}", f"{_ordinal(fingerprint.overall_percentile)} pct"),
            ("offense", f"{fingerprint.offense_per_100:+.2f}", f"{_ordinal(fingerprint.offense_percentile)} pct"),
            ("defense", f"{fingerprint.defense_per_100:+.2f}", f"{_ordinal(fingerprint.defense_percentile)} pct"),
        ],
        axes=[
            Axis(
                label=value.skill.label,
                group=value.skill.group,
                radius=_radius(value.per_100, value.percentile, scale, league),
                tooltip=(
                    f"{value.skill.label}: {value.per_100:+.2f} per 100 poss, "
                    f"{_ordinal(value.percentile)} percentile "
                    f"(league average {value.league_average_per_100:+.2f}, best {value.league_best_per_100:+.2f})"
                ),
            )
            for value in fingerprint.values
        ],
    )


def _rings(scale: str) -> list[tuple[float, str]]:
    if scale == "percentile":
        return [(0.25, ""), (0.5, "league median"), (0.75, ""), (1.0, "league best")]
    return [(VALUE_ZERO_FRACTION, "zero"), (0.5, ""), (0.75, ""), (1.0, "league best")]


def _table(fingerprints: list[PlayerFingerprint], scale: str) -> tuple[list[str], list[tuple[str, str, list[Cell]]]]:
    """The numbers under the plot. The radar is a shape; this is what makes it
    checkable, which is why it is always drawn and not an option.

    In a comparison every row shades the leader's cell in that player's color,
    with the shade carrying how far ahead they are: the widest gap in the table
    is full strength and everything else is a fraction of it, so a row that is
    all but tied looks all but tied instead of looking like a win.
    """
    values = [f.values for f in fingerprints]
    gaps = [max(column) - min(column) for column in ([v[i].per_100 for v in values] for i in range(len(values[0])))]
    widest = max(gaps) or 1.0

    if len(fingerprints) == 1:
        headers = ["per 100", "percentile", "league avg", "league best"]
        rows = [
            (
                value.skill.label,
                value.skill.group,
                [
                    Cell(f"{value.per_100:+.2f}"),
                    Cell(_ordinal(value.percentile)),
                    Cell(f"{value.league_average_per_100:+.2f}"),
                    Cell(f"{value.league_best_per_100:+.2f}"),
                ],
            )
            for value in fingerprints[0].values
        ]
    else:
        headers = [f.name for f in fingerprints] + ["league avg"]
        rows = []
        for index, gap in enumerate(gaps):
            column = [f.values[index].per_100 for f in fingerprints]
            leader = column.index(max(column)) if gap > 0 else None
            # A floor under the shade, so a real but narrow lead is still
            # visible rather than rounding away to no highlight at all.
            intensity = 0.08 + 0.55 * (gap / widest) if leader is not None else 0.0
            rows.append(
                (
                    values[0][index].skill.label,
                    values[0][index].skill.group,
                    [Cell(f"{value:+.2f}", leader if position == leader else None, intensity) for position, value in enumerate(column)]
                    + [Cell(f"{fingerprints[0].values[index].league_average_per_100:+.2f}")],
                )
            )

    # Grouped exactly as the plot is grouped, and sorted by the drawn quantity
    # WITHIN each group. Sorting across groups instead would list the skills in
    # an order the radar never shows, and the table is what the radar is checked
    # against.
    key = [-f.percentile for f in fingerprints[0].values] if scale == "percentile" else [-abs(f.per_100) for f in fingerprints[0].values]
    order = {group: index for index, group in enumerate(dict.fromkeys(skill.group for skill in FINGERPRINT_SKILLS))}
    return headers, [row for _, row in sorted(zip(key, rows, strict=True), key=lambda pair: (order[pair[1][1]], pair[0]))]


def render_for_players(
    con: duckdb.DuckDBPyConnection,
    out_dir: Path,
    players: list[Entity],
    ambiguous: list[str],
    season: int,
    view: str = "total",
    scale: str = "percentile",
    min_minutes: int = FINGERPRINT_MIN_MINUTES,
) -> tuple[str, Path]:
    """Render already-resolved players' fingerprints to one radar plot.

    Args:
        con: A read-only warehouse connection.
        out_dir: Directory the HTML is written to; created if missing.
        players: The players to draw, already resolved to warehouse entities.
        ambiguous: Other names that also matched, mentioned in the message.
        season: Season-ending year.
        view: ``"total"``, ``"offense"`` or ``"defense"``.
        scale: ``"percentile"`` or ``"value"`` - see :data:`FINGERPRINT_SCALES`.
        min_minutes: The pool floor passed to :func:`load_fingerprints`.

    Returns:
        A human-readable message, and the file written.

    Raises:
        FingerprintUnavailable: nothing could be drawn - see
            :func:`load_fingerprints`.

    .. versionadded:: 1.3.0
    """
    if scale not in FINGERPRINT_SCALES:
        raise FingerprintUnavailable(f"scale must be one of {list(FINGERPRINT_SCALES)} - got {scale!r}.")
    fingerprints, league = load_fingerprints(con, players, season, view=view, min_minutes=min_minutes)
    # A player with no row in this season's fingerprint file is dropped by
    # load_fingerprints rather than drawn as a zero polygon, which would read as
    # "played and contributed nothing". Named in the message instead.
    drawn = {f.athlete_id for f in fingerprints}
    missing = [p.name for p in players if p.id not in drawn]

    title = " vs ".join(f.name for f in fingerprints)
    units = "percentile of the league" if scale == "percentile" else "net points per 100 possessions"
    scope = {"total": "offense and defense", "offense": "offensive skills only", "defense": "defensive skills only"}[view]
    subtitle = f"{season} season - {scope}, {units}"
    axis_note = (
        f"Each spoke is one skill, in NetPoints per 100 possessions, as a percentile of the {league.pool_size} players with at least {min_minutes} minutes. Further out is better."
        if scale == "percentile"
        else (
            "Each spoke is one skill, in NetPoints per 100 possessions, on one shared scale - bigger skills draw bigger. "
            f"The inner ring is zero; the outer is the best any of the {league.pool_size} qualified players posted in any skill."
        )
    )
    headers, table_rows = _table(fingerprints, scale)
    html = render_fingerprint_html(
        title=title,
        subtitle=subtitle,
        series=[build_series(fingerprint, scale, league) for fingerprint in fingerprints],
        rings=_rings(scale),
        axis_note=axis_note,
        table_headers=headers,
        table_rows=table_rows,
    )

    safe = "_vs_".join("".join(c if c.isalnum() else "_" for c in f.name.lower()) for f in fingerprints)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"fingerprint_{safe}_{season}_{view}_{scale}.html"
    out_path.write_text(html)

    message = f"Rendered NetPoints fingerprint ({view}) for {title} ({season}, {scale} scale) to {out_path}"
    unqualified = [f.name for f in fingerprints if not f.qualified]
    if unqualified:
        message += f". Note: {', '.join(unqualified)} played under {min_minutes} minutes, so they are plotted against a pool they are not in"
    if missing:
        message += f". No fingerprint on record for: {', '.join(missing)}"
    if ambiguous:
        message += f". Note: other players also matched: {ambiguous}"
    return message, out_path


def render_fingerprint(
    con: duckdb.DuckDBPyConnection,
    out_dir: Path,
    player_name: str,
    season: int | None = None,
    view: str = "total",
    scale: str = "percentile",
) -> str:
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
        A human-readable message naming the player and the file written, or
        saying why nothing could be drawn.

    .. versionadded:: 1.3.0
    """
    # Imported here, not at module scope: shotchart imports nothing from this
    # module, and a top-level import in the other direction would still be a
    # cycle waiting for the first edit that reverses it.
    from .shotchart import resolve_chart_player

    resolved, ambiguous = [], []
    for name in player_name.split(" vs "):
        found = resolve_chart_player(con, name.strip())
        if found is None:
            return f"No player found matching {name.strip()!r}."
        player, also = found
        resolved.append(player)
        ambiguous.extend(also)
    try:
        message, _ = render_for_players(con, out_dir, resolved, ambiguous, season if season is not None else current_season(), view=view, scale=scale)
    except FingerprintUnavailable as exc:
        return str(exc)
    return message
