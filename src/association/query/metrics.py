"""The leaderboard metric registry: everything needed to build a correct
"top N players by X" query for a fixed, known set of metrics, decided once
here in code instead of re-derived by the model from prose on every query.

Standalone (it imports nothing from the rest of query/) so every consumer can
read it without a cycle: leaderboard.py builds SQL from it, prompt.py lists the
known metrics in the get_leaderboard tool description, templates.py validates
router slots against it."""

from __future__ import annotations

from dataclasses import dataclass, field

from association.net_points_categories import FINGERPRINT_CATEGORIES, FINGERPRINT_SIDE_LABELS

__all__ = [
    "SEASON_TYPE_LABELS",
    "CareerAggregate",
    "LeaderboardMetric",
    "LEADERBOARD_METRICS",
    "EXTRA_FIELD_COLUMNS",
    "CORE_METRIC_NAMES",
    "FINGERPRINT_METRIC_NAMES",
    "BOX_SCORE_METRIC_NAMES",
]

SEASON_TYPE_LABELS = {1: "Preseason", 2: "Regular Season", 3: "Postseason"}

# Extra display-only columns get_leaderboard can join in on request - a small,
# fixed whitelist (name -> the real player_season_stats column) rather than an
# arbitrary "fields" passthrough, so the model can't reintroduce a column-
# guessing/injection surface the tool exists to close off. All sourced from
# player_season_stats since that's the one table every metric can join to on
# (athlete_id, season, season_type) regardless of which table the ranked
# metric itself lives in.
EXTRA_FIELD_COLUMNS = {
    "points": "avgPoints",
    "rebounds": "avgRebounds",
    "assists": "avgAssists",
    "steals": "avgSteals",
    "blocks": "avgBlocks",
    "minutes": "avgMinutes",
}


@dataclass(frozen=True)
class CareerAggregate:
    """How one metric adds up over a whole career rather than one season.

    Summed from a player's per-team season rows, never from the row that
    combines a traded player's stints. That combined row is sometimes empty -
    Moses Malone's 1976-77 one is all NULL, which silently dropped the season
    and 1,083 points from his total - and in 1995-96 a dozen of them disagree
    with their own stints (Eric Murdock's says 9 games; his two stints say 73).

    ``numerator`` is summed. With a ``denominator`` the value is the ratio of
    the two sums - games for a per-game average, attempts for a percentage -
    which weights each season by its volume instead of averaging averages.
    ``weighted`` is for a per-game column with no season total behind it
    (minutes): it is multiplied back out by games before it is summed.

    ``min_sample`` and ``postseason_min_sample`` are the career qualifiers,
    compared against the career SUM of the metric's ``min_sample_column``.

    .. versionadded:: 2.1.0
    """

    numerator: str
    denominator: str | None = None
    weighted: bool = False
    min_sample: int | None = None
    postseason_min_sample: int | None = None


@dataclass(frozen=True)
class LeaderboardMetric:
    """`column` is what gets ranked; `season_type_is_string` routes to
    net_points_player's own string season_type instead of the numeric one
    every other table uses; `min_sample_column`/`default_min_sample` bake in
    the qualifying threshold that keeps small-sample flukes (a garbage-time
    cameo, a 1-game call-up) from dominating a rate/percentage ranking -
    confirmed live necessary for usage_pct and NetPoints per-100-possession
    values, and necessary for every per-game average too, where the fewest
    games is the easiest way to the top of a board.

    A COUNT is the exception and the only one: a season total, a
    double-double count and a cumulative NetPoints figure all need volume to
    rank at all, so they carry ``min_sample_column`` (a question may still
    give its own minimum) and no floor of their own.

    ``min_sample_column`` without a ``default_min_sample`` therefore means
    "ranked unqualified unless asked otherwise", which is a claim about the
    metric - not an omission to be read as one. It was read as one for the
    five original per-game averages, and they ranked unqualified for as long
    as it was.

    ``ratio`` ranks makes over attempts, computed from the two season totals,
    instead of ``column``. ESPN's percentage columns are rounded to one decimal
    (46.7), which ties players who are not tied; the computed value is a 0-1
    fraction the answer prints as a percentage.

    ``postseason_min_sample`` replaces ``default_min_sample`` in the postseason,
    where a season-sized qualifier would leave only the two finalists. Unset,
    the regular-season qualifier applies to both, as it always did.

    ``career`` says how the metric sums over a career. A metric without one has
    no career ranking: usage and true shooting need team context the season
    rows do not carry, and NetPoints starts in 2019, so a "career" of it would
    be seven seasons presented as a career.

    .. versionchanged:: 2.1.0
       Added ``ratio``, ``postseason_min_sample`` and ``career``.

    .. versionchanged:: 2.1.1
       Every per-game average now carries the games qualifiers, so a ranking
       by one is qualified in both season types.
    """

    table: str
    column: str
    label: str
    id_column: str = "athlete_id"
    season_column: str = "season"
    season_type_column: str = "season_type"
    season_type_is_string: bool = False
    has_season_type: bool = True
    dedup_traded: bool = False
    extra_columns: tuple[str, ...] = field(default_factory=tuple)
    min_sample_column: str | None = None
    default_min_sample: int | None = None
    requires: str | None = None
    ratio: tuple[str, str] | None = None
    postseason_min_sample: int | None = None
    career: CareerAggregate | None = None


# The career-average qualifier: 400 games is the long-standing record-book
# minimum for a career per-game leader, and 50 postseason games is the same
# eighth of it a postseason is of a season. Without one, a player with a single
# 40-point season tops "career points per game".
CAREER_MIN_GAMES = 400
CAREER_MIN_POSTSEASON_GAMES = 50


def _career_per_game(total: str) -> CareerAggregate:
    return CareerAggregate(numerator=total, denominator="gamesPlayed", min_sample=CAREER_MIN_GAMES, postseason_min_sample=CAREER_MIN_POSTSEASON_GAMES)


# The season qualifier for a per-game rate: the same 20 games usage and true
# shooting already use. Measured on 2025-26 without one, "fouls per game" was
# led by a player with three games and "minutes per game" had a six-game
# player fourth. The postseason's is 5 - more than a first-round sweep.
#
# Both are flat, not scaled to the schedule, and measured against the whole
# warehouse that holds: over 1994-2026 the thinnest regular season still
# qualifies 336 players (1999, the 50-game lockout year) of 440, and the
# thinnest postseason 92 of 185. No board anywhere goes empty at these floors,
# so the cost of applying them is a small-sample leader and nothing else.
PER_GAME_MIN_GAMES = 20
PER_GAME_MIN_POSTSEASON_GAMES = 5


def _per_game(column: str, total: str | None, label: str) -> LeaderboardMetric:
    # `total` is the season-total column behind the average; minutes has none,
    # so its career value is games-weighted from the averages instead.
    return LeaderboardMetric(
        table="player_season_stats",
        column=column,
        label=label,
        dedup_traded=True,
        extra_columns=("gamesPlayed",),
        min_sample_column="gamesPlayed",
        default_min_sample=PER_GAME_MIN_GAMES,
        postseason_min_sample=PER_GAME_MIN_POSTSEASON_GAMES,
        career=_career_per_game(total) if total else CareerAggregate(numerator=column, weighted=True, min_sample=CAREER_MIN_GAMES, postseason_min_sample=CAREER_MIN_POSTSEASON_GAMES),
    )


LEADERBOARD_METRICS: dict[str, LeaderboardMetric] = {
    "usage_pct": LeaderboardMetric(
        table="player_season_advanced_stats",
        column="usage_pct",
        label="usage rate",
        extra_columns=("games_played",),
        min_sample_column="games_played",
        default_min_sample=20,
        requires="warehouse rebuilt with `association data load` after player_box_stats was fetched",
    ),
    # The two shooting percentages qualify on ATTEMPTS, each on its own
    # denominator, not on games. Twenty games let Kai Jones top 2025's true
    # shooting at .804 on 109 shots, with Patrick Baldwin Jr.'s 35 third.
    #
    # Both floors were calibrated against StatMuse's published 2025 and 2026
    # top 15s, whose rules are 725 points (TS%) and 300 made field goals (eFG%)
    # per 82 games:
    # - 550 true-shooting attempts reproduces 2026's TS% list exactly, and
    #   2025's but for rank 15, where Okongwu's .6341 and SGA's .6338 swap - the
    #   725-point rule makes the same swap on these numbers. Anything from 520 to
    #   576 does the same; 550 is the middle of that, not a measured optimum.
    # - 480 field-goal attempts puts every published eFG% top-12 player on the
    #   board in both seasons. The band is 475-491, and its top is 2026's
    #   leader, Rudy Gobert, at 491 - a round 500 drops the league leader. What
    #   still differs is the rule and not noise: 3-point shooters with the
    #   attempts but under 300 makes (2026's Sam Merrill and Isaiah Joe, 7th
    #   and 8th here, and AJ Green), who push published ranks 13-15 out.
    # Not the made-shot rules themselves, because a made-shot minimum leans on
    # the thing it ranks: a better shooter qualifies on fewer attempts.
    #
    # The postseason floors are the same per-game rate over 10 games rather
    # than 82. No published postseason list is qualified at all (StatMuse's
    # 2025 leader shot 150% on two attempts), so those are scaled, not
    # calibrated. The season floors are flat, not scaled to the schedule: the
    # shortened 2020 and 2021 seasons qualify ~155 players against ~180.
    "ts_pct": LeaderboardMetric(
        table="player_season_advanced_stats",
        column="ts_pct",
        label="true shooting %",
        extra_columns=("games_played", "true_shooting_attempts"),
        min_sample_column="true_shooting_attempts",
        default_min_sample=550,
        postseason_min_sample=67,
        requires="warehouse rebuilt with `association data load` after player_box_stats was fetched",
    ),
    "efg_pct": LeaderboardMetric(
        table="player_season_advanced_stats",
        column="efg_pct",
        label="effective FG%",
        extra_columns=("games_played", "field_goals_attempted"),
        min_sample_column="field_goals_attempted",
        default_min_sample=480,
        postseason_min_sample=59,
        requires="warehouse rebuilt with `association data load` after player_box_stats was fetched",
    ),
    # Built by the same helper the newer per-game metrics use, so there is one
    # definition of "a per-game metric" rather than two that can disagree.
    # These five were the two: each carried min_sample_column with no floor to
    # apply to it, which reads as deliberate and ranked every board
    # unqualified. Danny Fortson's 6 games led 2001 rebounding at 16.3 (it was
    # Dikembe Mutombo) and Kawhi Leonard's 2 led 2023 playoff scoring.
    "avg_points": _per_game("avgPoints", "points", "points per game"),
    "avg_rebounds": _per_game("avgRebounds", "totalRebounds", "rebounds per game"),
    "avg_assists": _per_game("avgAssists", "assists", "assists per game"),
    "avg_steals": _per_game("avgSteals", "steals", "steals per game"),
    "avg_blocks": _per_game("avgBlocks", "blocks", "blocks per game"),
    # ESPN precomputes these as a season COUNT of such games, so "most
    # triple-doubles" is a leaderboard, not a per-game threshold recount. A
    # double-double is >=10 in TWO of {points, rebounds, assists, steals,
    # blocks} in one game, a triple-double >=10 in THREE - settled, and
    # already applied upstream in these columns.
    #
    # No games floor, unlike the averages above, because a count is
    # self-limiting: nobody records more triple-doubles than he plays games.
    # Measured over 1994-2026, no double-double board and no season-total
    # board was ever led from under these floors, and the two triple-double
    # boards that were are right - Kevin Garnett's 2 in the 2000 playoffs beat
    # everybody else's 1. A floor here would delete a true answer rather than
    # correct a wrong one.
    "double_doubles": LeaderboardMetric(
        table="player_season_stats", column="doubleDouble", label="double-doubles", dedup_traded=True, min_sample_column="gamesPlayed", career=CareerAggregate(numerator="doubleDouble")
    ),
    "triple_doubles": LeaderboardMetric(
        table="player_season_stats", column="tripleDouble", label="triple-doubles", dedup_traded=True, min_sample_column="gamesPlayed", career=CareerAggregate(numerator="tripleDouble")
    ),
    "netpoints_total": LeaderboardMetric(
        table="net_points_player",
        column="overall",
        label="NetPoints (season total)",
        season_type_column="net_points_season_type",
        season_type_is_string=True,
    ),
    "netpoints_offense": LeaderboardMetric(
        table="net_points_player",
        column="offense",
        label="offensive NetPoints (season total)",
        season_type_column="net_points_season_type",
        season_type_is_string=True,
    ),
    "netpoints_defense": LeaderboardMetric(
        table="net_points_player",
        column="defense",
        label="defensive NetPoints (season total)",
        season_type_column="net_points_season_type",
        season_type_is_string=True,
    ),
    "netpoints_per_100": LeaderboardMetric(
        table="net_points_player",
        column="overall_per_100_poss",
        label="NetPoints per 100 possessions",
        season_type_column="net_points_season_type",
        season_type_is_string=True,
        extra_columns=("total_minutes",),
        min_sample_column="total_minutes",
        default_min_sample=500,
    ),
    "netpoints_offense_per_100": LeaderboardMetric(
        table="net_points_player",
        column="offense_per_100_poss",
        label="offensive NetPoints per 100 possessions",
        season_type_column="net_points_season_type",
        season_type_is_string=True,
        extra_columns=("total_minutes",),
        min_sample_column="total_minutes",
        default_min_sample=500,
    ),
    "netpoints_defense_per_100": LeaderboardMetric(
        table="net_points_player",
        column="defense_per_100_poss",
        label="defensive NetPoints per 100 possessions",
        season_type_column="net_points_season_type",
        season_type_is_string=True,
        extra_columns=("total_minutes",),
        min_sample_column="total_minutes",
        default_min_sample=500,
    ),
}

CORE_METRIC_NAMES = frozenset(LEADERBOARD_METRICS)

# net_points_player_fingerprint's 22 shot/play-type categories x 3 sides
# (offense/defense/total) = 66 more metrics, generated from the same category
# list parse.py uses to build the columns in the first place - one source of
# truth for both. The table has no season_type column at all (has_season_type
# =False), unlike every other metric above. No default_min_sample: these are
# season CUMULATIVE totals in the same units as netpoints_total/offense/
# defense above, not a rate - same reasoning, no floor needed by default, but
# `minutes` is still exposed as min_sample_column for a question that gives
# its own minimum. Kept out of CORE_METRIC_NAMES (tracked separately as
# FINGERPRINT_METRIC_NAMES) so prompt.py can describe this whole group by its
# <category>_<side>_net_pts naming pattern instead of spelling out all 66
# names in prose - the tool's JSON schema enum still lists every one.
for _src_category, _our_prefix in FINGERPRINT_CATEGORIES.items():
    for _side, _side_label in FINGERPRINT_SIDE_LABELS.items():
        _metric_name = f"{_our_prefix}_{_side}_net_pts"
        LEADERBOARD_METRICS[_metric_name] = LeaderboardMetric(
            table="net_points_player_fingerprint",
            column=_metric_name,
            label=f"{_our_prefix.replace('_', ' ')} NetPoints ({_side_label})",
            has_season_type=False,
            min_sample_column="minutes",
        )

FINGERPRINT_METRIC_NAMES = frozenset(LEADERBOARD_METRICS) - CORE_METRIC_NAMES


def _season_total(column: str, label: str) -> LeaderboardMetric:
    # No qualifier: a season total needs volume to rank at all.
    return LeaderboardMetric(table="player_season_stats", column=column, label=label, dedup_traded=True, min_sample_column="gamesPlayed", career=CareerAggregate(numerator=column))


def _percentage(column: str, made: str, attempted: str, label: str, qualifiers: tuple[int, int, int, int]) -> LeaderboardMetric:
    """A shooting percentage, qualified on ATTEMPTS.

    ``qualifiers`` is (season, postseason, career, postseason career), and each
    set comes from one per-game attempt rate scaled to 82, 10, 400 and 50 games
    - so the four agree with each other rather than being four guesses. On
    attempts rather than the league's own made-shot minimums because a
    made-shot minimum lets a poor shooter qualify on volume that a better one
    with the same attempts does not: the qualifier leans on the thing it
    ranks. Measured on 2025-26, the season values still name the same three
    leaders the made-shot rule does (Gobert, Kennard, Cam Spencer).
    """
    season, postseason, career, postseason_career = qualifiers
    return LeaderboardMetric(
        table="player_season_stats",
        column=column,
        label=label,
        dedup_traded=True,
        extra_columns=(made, attempted),
        min_sample_column=attempted,
        default_min_sample=season,
        postseason_min_sample=postseason,
        ratio=(made, attempted),
        career=CareerAggregate(numerator=made, denominator=attempted, min_sample=career, postseason_min_sample=postseason_career),
    )


# Every stat name ROUTER_PROMPT teaches the router that had no metric behind
# it - so every leaderboard question naming one fell through to the agent. Kept
# out of CORE_METRIC_NAMES, which the agent's preamble spells out three times:
# the preamble has ~120 tokens of headroom and these 16 names would cost ~200.
# The agent can still rank by any of them; an unknown name comes back with the
# full list, the same way the fingerprint metrics are reached.
_BOX_SCORE_METRICS: dict[str, LeaderboardMetric] = {
    "total_points": _season_total("points", "total points"),
    "total_rebounds": _season_total("totalRebounds", "total rebounds"),
    "total_assists": _season_total("assists", "total assists"),
    "total_steals": _season_total("steals", "total steals"),
    "total_blocks": _season_total("blocks", "total blocks"),
    "total_three_pointers_made": _season_total("threePointFieldGoalsMade", "3-pointers made"),
    "total_field_goals_made": _season_total("fieldGoalsMade", "field goals made"),
    "total_free_throws_made": _season_total("freeThrowsMade", "free throws made"),
    "total_turnovers": _season_total("turnovers", "total turnovers"),
    "avg_three_pointers_made": _per_game("avgThreePointFieldGoalsMade", "threePointFieldGoalsMade", "3-pointers made per game"),
    "avg_turnovers": _per_game("avgTurnovers", "turnovers", "turnovers per game"),
    "avg_minutes": _per_game("avgMinutes", None, "minutes per game"),
    "avg_fouls": _per_game("avgFouls", "fouls", "fouls per game"),
    # 5, 2.5 and 1.5 attempts a game, over 82 / 10 / 400 / 50 games, rounded.
    "fg_pct": _percentage("fieldGoalPct", "fieldGoalsMade", "fieldGoalsAttempted", "field-goal percentage", (400, 50, 2000, 250)),
    "three_pt_pct": _percentage("threePointFieldGoalPct", "threePointFieldGoalsMade", "threePointFieldGoalsAttempted", "3-point percentage", (200, 25, 1000, 125)),
    "ft_pct": _percentage("freeThrowPct", "freeThrowsMade", "freeThrowsAttempted", "free-throw percentage", (125, 15, 600, 75)),
}
LEADERBOARD_METRICS.update(_BOX_SCORE_METRICS)

BOX_SCORE_METRIC_NAMES = frozenset(_BOX_SCORE_METRICS)
"""Season totals, per-game rates and shooting percentages from
``player_season_stats``, beyond the core set.

Rankable exactly like the core metrics, but not listed in the agent's
preamble - see the budget note above ``_BOX_SCORE_METRICS``.

.. versionadded:: 2.1.0
"""
