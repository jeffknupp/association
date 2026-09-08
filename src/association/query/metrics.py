"""The leaderboard metric registry: everything needed to build a correct
"top N players by X" query for a fixed, known set of metrics, decided once
here in code instead of re-derived by the model from prose on every query.

Standalone module (no dependency on prompt.py or toolbox.py) so both can
import from it - toolbox.py uses it to build SQL, prompt.py uses it to list
known metrics in the get_leaderboard tool description - without a circular
import between the two."""

from __future__ import annotations

from dataclasses import dataclass, field

from association.net_points_categories import FINGERPRINT_CATEGORIES, FINGERPRINT_SIDE_LABELS
from association.season import current_season

__all__ = [
    "current_season",
    "SEASON_TYPE_LABELS",
    "LeaderboardMetric",
    "LEADERBOARD_METRICS",
    "EXTRA_FIELD_COLUMNS",
    "CORE_METRIC_NAMES",
    "FINGERPRINT_METRIC_NAMES",
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
class LeaderboardMetric:
    """`column` is what gets ranked; `season_type_is_string` routes to
    net_points_player's own string season_type instead of the numeric one
    every other table uses; `min_sample_column`/`default_min_sample` bake in
    the qualifying threshold that keeps small-sample flukes (a garbage-time
    cameo, a 1-game call-up) from dominating a rate/percentage ranking -
    confirmed live necessary for usage_pct and NetPoints per-100-possession
    values, not needed (and left off) for season-total/average box-score
    stats, which naturally require volume to rank highly."""

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
    "ts_pct": LeaderboardMetric(
        table="player_season_advanced_stats",
        column="ts_pct",
        label="true shooting %",
        extra_columns=("games_played",),
        min_sample_column="games_played",
        default_min_sample=20,
        requires="warehouse rebuilt with `association data load` after player_box_stats was fetched",
    ),
    "efg_pct": LeaderboardMetric(
        table="player_season_advanced_stats",
        column="efg_pct",
        label="effective FG%",
        extra_columns=("games_played",),
        min_sample_column="games_played",
        default_min_sample=20,
        requires="warehouse rebuilt with `association data load` after player_box_stats was fetched",
    ),
    "avg_points": LeaderboardMetric(table="player_season_stats", column="avgPoints", label="points per game", dedup_traded=True, min_sample_column="gamesPlayed"),
    "avg_rebounds": LeaderboardMetric(table="player_season_stats", column="avgRebounds", label="rebounds per game", dedup_traded=True, min_sample_column="gamesPlayed"),
    "avg_assists": LeaderboardMetric(table="player_season_stats", column="avgAssists", label="assists per game", dedup_traded=True, min_sample_column="gamesPlayed"),
    "avg_steals": LeaderboardMetric(table="player_season_stats", column="avgSteals", label="steals per game", dedup_traded=True, min_sample_column="gamesPlayed"),
    "avg_blocks": LeaderboardMetric(table="player_season_stats", column="avgBlocks", label="blocks per game", dedup_traded=True, min_sample_column="gamesPlayed"),
    # ESPN precomputes these as a season COUNT of such games, so "most
    # triple-doubles" is a leaderboard, not a per-game threshold recount. A
    # double-double is >=10 in TWO of {points, rebounds, assists, steals,
    # blocks} in one game, a triple-double >=10 in THREE - settled, and
    # already applied upstream in these columns.
    "double_doubles": LeaderboardMetric(table="player_season_stats", column="doubleDouble", label="double-doubles", dedup_traded=True, min_sample_column="gamesPlayed"),
    "triple_doubles": LeaderboardMetric(table="player_season_stats", column="tripleDouble", label="triple-doubles", dedup_traded=True, min_sample_column="gamesPlayed"),
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
